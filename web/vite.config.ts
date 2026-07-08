import { createLogger, defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { fileURLToPath } from "node:url"
import type { ProxyOptions } from "vite"

// The scorer is OPTIONAL. When `python -m sentinelai.serve --port 8088` is not
// running, the console falls back to static artifacts on purpose - so a refused
// connection here is expected behaviour, not a failure. Vite's default proxy
// logger prints a stack trace for it anyway, which looks like a real error and
// buries actual problems, so ECONNREFUSED is swallowed and reported once as a
// single readable line.
let warnedAboutScorer = false
const scorerProxy = (): ProxyOptions => ({
	target: "http://127.0.0.1:8088",
	changeOrigin: true,
	configure: (proxy) => {
		proxy.on("error", (err, _req, res) => {
			const refused = (err as NodeJS.ErrnoException).code === "ECONNREFUSED"
			if (refused && !warnedAboutScorer) {
				warnedAboutScorer = true
				console.log(
					"[sentinelai] scorer not running on :8088 - serving static artifacts. " +
						"Start it with: python -m sentinelai.serve --port 8088",
				)
			} else if (!refused) {
				console.error("[sentinelai] proxy error:", err.message)
			}
			// Answer the browser so the fetch rejects fast instead of hanging.
			if ("writeHead" in res && !res.headersSent) {
				res.writeHead(503, { "Content-Type": "application/json" })
				res.end('{"error":"scorer offline"}')
			} else {
				res.destroy()
			}
		})
	},
})

// Vite logs proxy errors through its own logger, separately from the handler
// above, so the refused-connection stack trace has to be filtered here too.
// Only the expected offline-scorer case is dropped; every other error passes.
const logger = createLogger()
const baseError = logger.error
logger.error = (msg, opts) => {
	const expected = msg.includes("http proxy error") || msg.includes("ECONNREFUSED")
	if (expected) return
	baseError(msg, opts)
}

// The console is served two ways and must work in both:
//   1. static (Vercel / GitHub Pages / HF static Space) reading artifacts/*.json
//   2. behind the Python API, where /v1/* is proxied to the authenticated server
// Relative base keeps asset URLs correct when a Space serves from a subpath.
export default defineConfig({
	base: "./",
	customLogger: logger,
	plugins: [react(), tailwindcss()],
	resolve: {
		alias: {
			"@": fileURLToPath(new URL("./src", import.meta.url)),
		},
	},
	server: {
		port: 5173,
		proxy: {
			"/v1": scorerProxy(),
			"/healthz": scorerProxy(),
		},
	},
	build: {
		outDir: "dist",
		sourcemap: true,
		// Keep the animation libraries out of the critical path.
		rollupOptions: {
			output: {
				manualChunks: {
					motion: ["motion"],
					anime: ["animejs"],
				},
			},
		},
	},
})
