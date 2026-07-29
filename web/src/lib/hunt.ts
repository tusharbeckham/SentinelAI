/**
 * The hunting query language, in the browser.
 *
 * This is a deliberate second implementation of sentinelai/hunt.py. The grammar
 * is small precisely so the two can stay identical: a query typed into the
 * console means exactly what the same query means in the CLI. Every operator,
 * precedence rule and error case below mirrors the Python module.
 *
 *   probability > 0.9 and distinct_dsts > 20 | sort probability desc | limit 10
 *   attack ~ brute or failed_logins >= 5
 *   not entity ~ h00 and night_flag = 1
 *
 * Known limitation, same as the CLI: no parentheses. `and` binds tighter than
 * `or`, so `a or b and c` parses as `a or (b and c)`.
 */

export type HuntRow = Record<string, string | number | null>

export type Clause = {
	field: string
	op: string
	value: number | string
	negated: boolean
}

export type Query = {
	/** OR of AND groups. */
	groups: Clause[][]
	sortField: string | null
	sortDesc: boolean
	limit: number | null
	text: string
}

/** Thrown for any malformed query. Never swallowed: a broken query must not
 *  silently return every row. */
export class HuntError extends Error {}

type Token = { kind: 'pipe' | 'op' | 'word' | 'number' | 'quoted'; text: string }

const OP_RE = /^(>=|<=|!=|!~|==|>|<|=|~)/
const WORD_RE = /^[A-Za-z_][A-Za-z0-9_.]*/
const NUMBER_RE = /^-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?/

function tokenize(input: string): Token[] {
	const tokens: Token[] = []
	let rest = input
	let pos = 0
	while (rest.length > 0) {
		const ws = rest.match(/^\s+/)
		if (ws) {
			const len = ws[0].length
			rest = rest.slice(len)
			pos += len
			continue
		}
		if (rest.startsWith('|')) {
			tokens.push({ kind: 'pipe', text: '|' })
			rest = rest.slice(1)
			pos += 1
			continue
		}
		// Operators are matched before words so `~` never becomes part of a name.
		const op = rest.match(OP_RE)
		if (op) {
			tokens.push({ kind: 'op', text: op[0] })
			rest = rest.slice(op[0].length)
			pos += op[0].length
			continue
		}
		const quote = rest[0]
		if (quote === "'" || quote === '"') {
			const end = rest.indexOf(quote, 1)
			if (end < 0) throw new HuntError(`unterminated string at position ${pos}`)
			tokens.push({ kind: 'quoted', text: rest.slice(1, end) })
			rest = rest.slice(end + 1)
			pos += end + 1
			continue
		}
		// Numbers before words, so -1.5e3 is not split apart.
		const num = rest.match(NUMBER_RE)
		if (num) {
			tokens.push({ kind: 'number', text: num[0] })
			rest = rest.slice(num[0].length)
			pos += num[0].length
			continue
		}
		const word = rest.match(WORD_RE)
		if (word) {
			tokens.push({ kind: 'word', text: word[0] })
			rest = rest.slice(word[0].length)
			pos += word[0].length
			continue
		}
		throw new HuntError(`cannot parse query at position ${pos}: ${rest.slice(0, 12)}`)
	}
	return tokens
}

export function parse(input: string, fields?: readonly string[]): Query {
	const tokens = tokenize(input)
	const empty: Query = { groups: [], sortField: null, sortDesc: true, limit: null, text: input }
	if (tokens.length === 0) return empty

	const segments: Token[][] = [[]]
	for (const token of tokens) {
		if (token.kind === 'pipe') segments.push([])
		else segments[segments.length - 1]!.push(token)
	}

	const body = segments[0] ?? []
	const groups: Clause[][] = []
	let current: Clause[] = []
	let negated = false
	let i = 0

	while (i < body.length) {
		const token = body[i]!
		const low = token.text.toLowerCase()

		if (token.kind === 'word' && low === 'not') {
			negated = true
			i += 1
			continue
		}
		if (token.kind === 'word' && low === 'and') {
			i += 1
			continue
		}
		if (token.kind === 'word' && low === 'or') {
			if (current.length > 0) {
				groups.push(current)
				current = []
			}
			i += 1
			continue
		}

		if (token.kind !== 'word') {
			throw new HuntError(`expected a field name, got ${token.text}`)
		}
		if (fields && !fields.includes(token.text)) {
			throw new HuntError(`unknown field ${token.text}`)
		}
		const opToken = body[i + 1]
		if (!opToken || opToken.kind !== 'op') {
			throw new HuntError(`expected an operator after ${token.text}`)
		}
		const valToken = body[i + 2]
		if (!valToken) {
			throw new HuntError(`missing a value after ${token.text} ${opToken.text}`)
		}
		const value: number | string =
			valToken.kind === 'number' ? Number(valToken.text) : valToken.text
		if (valToken.kind === 'op' || valToken.kind === 'pipe') {
			throw new HuntError(`expected a value after ${token.text} ${opToken.text}`)
		}

		current.push({ field: token.text, op: opToken.text, value, negated })
		negated = false
		i += 3
	}

	if (negated) throw new HuntError("trailing 'not' with no clause after it")
	if (current.length > 0) groups.push(current)

	let sortField: string | null = null
	let sortDesc = true
	let limit: number | null = null

	for (const segment of segments.slice(1)) {
		const head = segment[0]
		if (!head) throw new HuntError("empty directive after '|'")
		const name = head.text.toLowerCase()
		if (name === 'sort') {
			const target = segment[1]
			if (!target) throw new HuntError("'sort' needs a field name")
			if (fields && !fields.includes(target.text)) {
				throw new HuntError(`cannot sort by unknown field ${target.text}`)
			}
			sortField = target.text
			const dir = segment[2]
			if (dir) {
				const d = dir.text.toLowerCase()
				if (d !== 'asc' && d !== 'desc') {
					throw new HuntError(`sort direction must be asc or desc, got ${dir.text}`)
				}
				sortDesc = d === 'desc'
			}
		} else if (name === 'limit') {
			const n = segment[1]
			if (!n || n.kind !== 'number') throw new HuntError("'limit' needs an integer")
			limit = Math.trunc(Number(n.text))
			if (limit <= 0) throw new HuntError("'limit' must be positive")
		} else {
			throw new HuntError(`unknown directive ${name}; expected 'sort' or 'limit'`)
		}
	}

	return { groups, sortField, sortDesc, limit, text: input }
}

function compare(actual: unknown, op: string, expected: number | string): boolean {
	if (op === '~' || op === '!~') {
		const hit = String(actual).toLowerCase().includes(String(expected).toLowerCase())
		return op === '~' ? hit : !hit
	}
	if (typeof expected === 'string') {
		if (op === '=' || op === '==') return String(actual) === expected
		if (op === '!=') return String(actual) !== expected
		throw new HuntError(`operator ${op} needs a number, got ${expected}`)
	}
	if (actual === null || actual === undefined) return false
	const left = Number(actual)
	if (Number.isNaN(left)) {
		throw new HuntError(`cannot compare non-numeric value ${String(actual)} with ${op}`)
	}
	switch (op) {
		case '>':
			return left > expected
		case '>=':
			return left >= expected
		case '<':
			return left < expected
		case '<=':
			return left <= expected
		case '=':
		case '==':
			return left === expected
		case '!=':
			return left !== expected
		default:
			throw new HuntError(`unsupported operator ${op}`)
	}
}

function matches(query: Query, row: HuntRow): boolean {
	if (query.groups.length === 0) return true
	return query.groups.some((group) =>
		group.every((clause) => {
			if (!(clause.field in row)) throw new HuntError(`unknown field ${clause.field}`)
			const hit = compare(row[clause.field], clause.op, clause.value)
			return clause.negated ? !hit : hit
		}),
	)
}

export function run(
	input: string | Query,
	rows: readonly HuntRow[],
	fields?: readonly string[],
): HuntRow[] {
	const query = typeof input === 'string' ? parse(input, fields) : input
	const hits = rows.filter((row) => matches(query, row))
	if (query.sortField) {
		const key = query.sortField
		hits.sort((a, b) => {
			const av = Number(a[key])
			const bv = Number(b[key])
			if (Number.isNaN(av) || Number.isNaN(bv)) {
				return String(a[key]).localeCompare(String(b[key])) * (query.sortDesc ? -1 : 1)
			}
			return query.sortDesc ? bv - av : av - bv
		})
	}
	return query.limit === null ? hits : hits.slice(0, query.limit)
}

/** The same examples the CLI prints, so both surfaces teach the same syntax. */
export const HUNT_EXAMPLES: ReadonlyArray<{ query: string; why: string }> = [
	{ query: 'probability > 0.9 | sort probability desc | limit 10', why: 'strongest detections' },
	{ query: 'distinct_dsts > 20 and flow_count < 50', why: 'few flows, many hosts: scanning' },
	{ query: 'failed_logins >= 5 and probability > 0.5', why: 'credential pressure that scored' },
	{ query: 'dns_entropy_max > 3.5', why: 'high-entropy DNS: tunnelling' },
	{ query: 'attack ~ brute and probability < 0.63', why: 'missed brute force: below threshold' },
	{ query: 'graph_score > 0 and night_flag = 1', why: 'auth-graph movement out of hours' },
	{ query: 'night_flag = 1 and bytes_out_sum > 1e6', why: 'large egress overnight' },
]