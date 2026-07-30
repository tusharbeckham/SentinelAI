// Brace-balance gate for the stylesheets.
//
// This exists because v1.8.0 shipped a stylesheet with one orphaned closing
// brace and the entire toolchain waved it through. tsc does not parse CSS.
// esbuild, as we invoke it, does not parse CSS either. Tailwind is the only
// component that does, and it only runs under the dev server or a full build,
// so the break reached a human before it reached a check.

import { readFileSync } from "node:fs"
import { globSync } from "node:fs"

const files = globSync("src/**/*.css")

let failed = false

for (const file of files) {
	const css = readFileSync(file, "utf8")
	let depth = 0
	let orphan = -1

	for (let i = 0; i < css.length; i++) {
		const ch = css[i]
		if (ch === "{") depth++
		else if (ch === "}") {
			if (depth === 0) { orphan = i; break }
			depth--
		}
	}

	// Report the offset, not just the fact. Finding the orphan by eye in a
	// 450 line stylesheet is exactly the step that wasted an evening.
	if (orphan !== -1) {
		console.error(`${file}: stray closing brace at offset ${orphan}`)
		failed = true
	} else if (depth !== 0) {
		console.error(`${file}: ${depth} unclosed block(s)`)
		failed = true
	} else {
		console.log(`${file}: balanced`)
	}
}

if (failed) process.exit(1)
