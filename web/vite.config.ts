import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { fileURLToPath } from "node:url"

// The console is served two ways and must work in both:
//   1. static (Vercel / GitHub Pages / HF static Space) reading artifacts/*.json
//   2. behind the Python API, where /v1/* is proxied to the authenticated server
// Relative base keeps asset URLs correct when a Space serves from a subpath.
export default defineConfig({
	base: "./",
	plugins: [react(), tailwindcss()],
	resolve: {
		alias: {
			"@": fileURLToPath(new URL("./src", import.meta.url)),
		},
	},
	server: {
		port: 5173,
		proxy: {
			// `python -m sentinelai.serve --port 8088` in another terminal.
			"/v1": { target: "http://127.0.0.1:8088", changeOrigin: true },
			"/healthz": { target: "http://127.0.0.1:8088", changeOrigin: true },
		},
	},
	build: {
		outDir: "dist",
		sourcemap: true,
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
