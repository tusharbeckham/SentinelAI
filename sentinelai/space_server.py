"""Single-port server for a public demo (Hugging Face Docker Space, Fly, Render).

Why this exists: the React console is a static bundle, the scorer is an
authenticated Python service, and a Space exposes exactly one port. This process
serves the built front end and reverse-proxies the API routes to the real
:mod:`sentinelai.api` server running on a loopback port, so the browser sees one
origin and no CORS.

    export SENTINELAI_JWT_SECRET="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
    python3 -m sentinelai.space_server --port 7860 --dist web/dist

Security posture is unchanged from the local server: /v1/* still requires a
signed JWT with the right role, and the responder is still dry-run. The only
new surface is static file serving, which is confined to --dist and refuses
path traversal.
"""

from __future__ import annotations

import argparse
import os
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import api
from .serve import build_service

PROXY_PREFIXES = ("/v1/", "/healthz")
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class ConsoleHandler(SimpleHTTPRequestHandler):
    """Static files for the console, reverse proxy for the API."""

    upstream = "http://127.0.0.1:8088"

    def _is_api(self) -> bool:
        return self.path.startswith(PROXY_PREFIXES)

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        req = urllib.request.Request(
            self.upstream + self.path,
            data=body,
            method=self.command,
        )
        # Forward only what the API needs. Authorization is the important one:
        # without it every /v1 route correctly answers 401.
        for header in ("Authorization", "Content-Type", "Accept"):
            value = self.headers.get(header)
            if value:
                req.add_header(header, value)

        try:
            with urllib.request.urlopen(req, timeout=30) as upstream_response:
                status = upstream_response.status
                payload = upstream_response.read()
                headers = upstream_response.headers.items()
        except urllib.error.HTTPError as exc:
            # 401/403/404/422 are normal, meaningful answers here - pass them
            # through verbatim instead of masking them as 500s.
            try:
                status = exc.code
                payload = exc.read()
                headers = exc.headers.items()
            finally:
                exc.close()
        except urllib.error.URLError as exc:
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(f'{{"error":"scorer unavailable: {exc.reason}"}}'.encode("utf-8"))
            return

        self.send_response(status)
        for key, value in headers:
            if key.lower() in HOP_BY_HOP or key.lower() == "content-length":
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self._is_api():
            self._proxy()
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self._is_api():
            self._proxy()
            return
        self.send_error(405, "static host accepts GET only")

    def end_headers(self) -> None:
        # Static assets only; the API sets its own headers upstream.
        if not self._is_api():
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self'",
            )
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        print("http %s - %s" % (self.address_string(), fmt % args), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "7860")))
    ap.add_argument("--api-port", type=int, default=8088)
    ap.add_argument("--dist", default="web/dist")
    ap.add_argument("--days", type=float, default=1.0, help="telemetry days to fit on at boot")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--artifacts", default="artifacts")
    args = ap.parse_args()

    dist = Path(args.dist)
    if not (dist / "index.html").exists():
        raise SystemExit(
            f"no built front end at {dist}/index.html\n"
            "  cd web && pnpm install && pnpm sync-data && pnpm build"
        )

    if not os.environ.get("SENTINELAI_JWT_SECRET"):
        raise SystemExit(
            "SENTINELAI_JWT_SECRET is not set. There is deliberately no default secret."
        )

    service = build_service(args.days, args.seed, args.artifacts)
    api_server = api.serve(service, host="127.0.0.1", port=args.api_port)
    threading.Thread(target=api_server.serve_forever, daemon=True).start()
    print(f"scorer on loopback :{args.api_port}", flush=True)

    ConsoleHandler.upstream = f"http://127.0.0.1:{args.api_port}"
    handler = partial(ConsoleHandler, directory=str(dist))
    public = ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    print(f"console on http://0.0.0.0:{args.port} (serving {dist})", flush=True)
    try:
        public.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        public.shutdown()
        api_server.shutdown()


if __name__ == "__main__":
    main()
