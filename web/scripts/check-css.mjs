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
