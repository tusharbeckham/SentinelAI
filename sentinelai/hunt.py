"""A small threat-hunting query language over scored windows.

An analyst should not have to edit Python to ask "show me every window with a
high score, few flows and many destinations". This is a deliberately tiny DSL
with the same grammar implemented twice - here for the CLI, and in
web/src/lib/hunt.ts for the console - so a query typed in the browser means
exactly what it means in the terminal.

    probability > 0.9 and distinct_dsts > 20 | sort probability desc | limit 10
    attack ~ brute or failed_logins >= 5
    not entity ~ h00 and night_flag = 1

Grammar (whole thing):

    query    := filter ( '|' directive )*
    filter   := andgroup ( 'or' andgroup )*
    andgroup := clause ( 'and' clause )*
    clause   := [ 'not' ] field op value
    op       := '>' | '>=' | '<' | '<=' | '=' | '==' | '!=' | '~' | '!~'
    value    := number | bareword | quoted string
    directive:= 'sort' field [ 'asc' | 'desc' ] | 'limit' integer

Known limitation, stated rather than hidden: there are no parentheses. `and`
binds tighter than `or`, so `a or b and c` means `a or (b and c)`. Parentheses
were left out because the grammar has to stay small enough to keep the Python and
TypeScript implementations provably identical; a mismatch between them would be
worse than a missing feature.

`~` is a case-insensitive substring match and works on any field, numeric or not.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Sequence

OPERATORS = (">=", "<=", "!=", "!~", "==", ">", "<", "=", "~")

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<pipe>\|)
      | (?P<op>>=|<=|!=|!~|==|>|<|=|~)
      | (?P<quoted>'[^']*'|"[^"]*")
      | (?P<word>[A-Za-z_][A-Za-z0-9_.]*)
      | (?P<number>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)
    )""",
    re.VERBOSE,
)


class HuntError(ValueError):
    """Raised for any malformed query. Never swallowed: a query that cannot be
    parsed must fail visibly rather than silently return every row."""


@dataclass(frozen=True)
class Clause:
    field: str
    op: str
    value: float | str
    negated: bool = False

    def matches(self, row: dict[str, Any]) -> bool:
        if self.field not in row:
            raise HuntError(f"unknown field {self.field!r}")
        actual = row[self.field]
        result = _compare(actual, self.op, self.value)
        return not result if self.negated else result


@dataclass(frozen=True)
class Query:
    # OR of AND groups.
    groups: tuple[tuple[Clause, ...], ...] = ()
    sort_field: str | None = None
    sort_desc: bool = True
    limit: int | None = None
    text: str = ""

    def matches(self, row: dict[str, Any]) -> bool:
        if not self.groups:
            return True
        return any(all(c.matches(row) for c in group) for group in self.groups)


def _compare(actual: Any, op: str, expected: float | str) -> bool:
    if op in ("~", "!~"):
        hit = str(expected).lower() in str(actual).lower()
        return hit if op == "~" else not hit

    if isinstance(expected, str):
        # Equality against a string is a plain comparison; ordering is not.
        if op in ("=", "=="):
            return str(actual) == expected
        if op == "!=":
            return str(actual) != expected
        raise HuntError(f"operator {op!r} needs a number, got {expected!r}")

    if actual is None:
        return False
    try:
        left = float(actual)
    except (TypeError, ValueError):
        # Comparing a label against a number is a query bug worth surfacing.
        raise HuntError(f"cannot compare non-numeric value {actual!r} with {op} {expected}")

    if op == ">":
        return left > expected
    if op == ">=":
        return left >= expected
    if op == "<":
        return left < expected
    if op == "<=":
        return left <= expected
    if op in ("=", "=="):
        return left == expected
    if op == "!=":
        return left != expected
    raise HuntError(f"unsupported operator {op!r}")


def _tokenize(query: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(query):
        if query[pos].isspace():
            pos += 1
            continue
        m = _TOKEN.match(query, pos)
        if not m or m.end() == pos:
            raise HuntError(f"cannot parse query at position {pos}: {query[pos:pos + 12]!r}")
        kind = m.lastgroup or ""
        text = (m.group(kind) or "").strip()
        if kind == "quoted":
            text = text[1:-1]
        tokens.append((kind, text))
        pos = m.end()
    return tokens


def parse(query: str, fields: Sequence[str] | None = None) -> Query:
    """Parse a query string. Raises HuntError on anything malformed."""
    tokens = _tokenize(query)
    if not tokens:
        return Query(text=query)

    # Split off the trailing directives first.
    segments: list[list[tuple[str, str]]] = [[]]
    for kind, text in tokens:
        if kind == "pipe":
            segments.append([])
        else:
            segments[-1].append((kind, text))

    groups: list[tuple[Clause, ...]] = []
    current: list[Clause] = []
    body = segments[0]
    i = 0
    negated = False

    while i < len(body):
        kind, text = body[i]
        low = text.lower()

        if kind == "word" and low == "not":
            negated = True
            i += 1
            continue
        if kind == "word" and low == "and":
            i += 1
            continue
        if kind == "word" and low == "or":
            if current:
                groups.append(tuple(current))
                current = []
            i += 1
            continue

        if kind != "word":
            raise HuntError(f"expected a field name, got {text!r}")
        if fields is not None and text not in fields:
            raise HuntError(f"unknown field {text!r}; known fields: {', '.join(sorted(fields))}")
        if i + 2 >= len(body) + 0 or i + 1 >= len(body):
            raise HuntError(f"field {text!r} is missing an operator and value")

        op_kind, op_text = body[i + 1]
        if op_kind != "op":
            raise HuntError(f"expected an operator after {text!r}, got {op_text!r}")
        if i + 2 >= len(body):
            raise HuntError(f"missing a value after {text!r} {op_text}")

        val_kind, val_text = body[i + 2]
        value: float | str
        if val_kind == "number":
            value = float(val_text)
        elif val_kind in ("word", "quoted"):
            value = val_text
        else:
            raise HuntError(f"expected a value after {text!r} {op_text}, got {val_text!r}")

        current.append(Clause(field=text, op=op_text, value=value, negated=negated))
        negated = False
        i += 3

    if negated:
        raise HuntError("trailing 'not' with no clause after it")
    if current:
        groups.append(tuple(current))

    sort_field: str | None = None
    sort_desc = True
    limit: int | None = None

    for segment in segments[1:]:
        if not segment:
            raise HuntError("empty directive after '|'")
        head = segment[0][1].lower()
        if head == "sort":
            if len(segment) < 2:
                raise HuntError("'sort' needs a field name")
            sort_field = segment[1][1]
            if fields is not None and sort_field not in fields:
                raise HuntError(f"cannot sort by unknown field {sort_field!r}")
            if len(segment) > 2:
                direction = segment[2][1].lower()
                if direction not in ("asc", "desc"):
                    raise HuntError(f"sort direction must be asc or desc, got {direction!r}")
                sort_desc = direction == "desc"
        elif head == "limit":
            if len(segment) < 2 or segment[1][0] != "number":
                raise HuntError("'limit' needs an integer")
            limit = int(float(segment[1][1]))
            if limit <= 0:
                raise HuntError("'limit' must be positive")
        else:
            raise HuntError(f"unknown directive {head!r}; expected 'sort' or 'limit'")

    return Query(
        groups=tuple(groups),
        sort_field=sort_field,
        sort_desc=sort_desc,
        limit=limit,
        text=query,
    )


def run(query: str | Query, rows: Sequence[dict[str, Any]], fields: Sequence[str] | None = None
        ) -> list[dict[str, Any]]:
    """Execute a query against rows."""
    q = parse(query, fields) if isinstance(query, str) else query
    hits = [row for row in rows if q.matches(row)]
    if q.sort_field:
        key = q.sort_field

        def sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
            value = row.get(key)
            try:
                return (0, float(value), "")  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return (1, 0.0, str(value))

        hits.sort(key=sort_key, reverse=q.sort_desc)
    if q.limit is not None:
        hits = hits[: q.limit]
    return hits


EXAMPLES: tuple[tuple[str, str], ...] = (
    ("probability > 0.9 | sort probability desc | limit 10", "strongest detections"),
    ("distinct_dsts > 20 and flow_count < 50", "few flows, many hosts: scanning shape"),
    ("failed_logins >= 5 and probability > 0.5", "credential pressure that also scored"),
    ("dns_entropy_max > 3.5", "high-entropy DNS: tunnelling shape"),
    ("attack ~ brute and probability < 0.63", "missed brute force: below threshold"),
    ("graph_score > 0 and night_flag = 1", "auth-graph movement out of hours"),
    ("night_flag = 1 and bytes_out_sum > 1e6", "large egress overnight"),
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Hunt over scored windows")
    ap.add_argument("query", nargs="?", help="query string; omit to list examples")
    ap.add_argument("--artifacts", default="artifacts")
    ap.add_argument("--columns", default="entity,win,probability,attack")
    args = ap.parse_args(argv)

    index_path = Path(args.artifacts) / "hunt_index.json"
    if not index_path.exists():
        raise SystemExit(
            f"missing {index_path}\nRun: python -m sentinelai.entity_risk --artifacts {args.artifacts}"
        )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = index["rows"]
    fields: list[str] = index["fields"]

    if not args.query:
        print(f"{len(rows)} windows indexed. Fields:")
        print("  " + ", ".join(fields))
        print("\nExamples:")
        for q, why in EXAMPLES:
            print(f"  {q}\n      {why}")
        return 0

    try:
        hits = run(args.query, rows, fields)
    except HuntError as exc:
        raise SystemExit(f"bad query: {exc}")

    columns = [c for c in args.columns.split(",") if c in fields]
    print(f"{len(hits)} of {len(rows)} windows match\n")
    print("  ".join(f"{c:<14}" for c in columns))
    for row in hits[:50]:
        cells = []
        for c in columns:
            v = row.get(c)
            cells.append(f"{v:<14.6f}" if isinstance(v, float) else f"{str(v):<14}")
        print("  ".join(cells))
    if len(hits) > 50:
        print(f"... {len(hits) - 50} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
