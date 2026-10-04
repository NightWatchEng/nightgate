"""hello-svc: a deliberately tiny HTTP JSON service (Python stdlib only).

This is the Nightgate enrollment example — see ../repo.yaml and
docs/wiki/Adopting.md. It is small but real: routes, input validation on
untrusted query data, tests, and a --selfcheck mode so CI can verify the
service without binding a port.

Run the server:    python3 -m hello_svc.app [--port 8000]
CI verification:   python3 -m hello_svc.app --selfcheck   (exits 0 on pass)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

MAX_NAME_LEN = 64
# Untrusted input: letters/digits first, then letters/digits/space/'/- only.
_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '\-]*\Z")


def health() -> tuple[int, dict]:
    """GET /health — liveness probe."""
    return 200, {"status": "ok"}


def greet(name: str | None) -> tuple[int, dict]:
    """GET /greet?name= — validate the externally supplied name, then greet.

    Returns (http_status, response_body). The name arrives from the query
    string, i.e. it is untrusted: reject missing, empty, oversized, and
    unexpected-character values with a 400 and a machine-readable error.
    """
    if not name:
        return 400, {"error": "query parameter 'name' is required"}
    if len(name) > MAX_NAME_LEN:
        return 400, {"error": f"'name' must be at most {MAX_NAME_LEN} characters"}
    if not _NAME_OK.match(name):
        return 400, {"error": "'name' may contain only letters, digits, spaces, ' and -"}
    return 200, {"greeting": f"Hello, {name}!"}


def route(target: str) -> tuple[int, dict]:
    """Dispatch a request target (path + query) to a handler."""
    parts = urlsplit(target)
    if parts.path == "/health":
        return health()
    if parts.path == "/greet":
        values = parse_qs(parts.query).get("name", [])
        return greet(values[0] if values else None)
    return 404, {"error": f"no route for {parts.path}"}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, body = route(self.path)
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 (stdlib signature)
        pass  # keep tests and selfcheck output clean


def selfcheck() -> int:
    """Exercise the routing table in-process; CI runs this instead of a server."""
    checks: list[tuple[object, object]] = [
        (route("/health"), (200, {"status": "ok"})),
        (route("/greet?name=Ada"), (200, {"greeting": "Hello, Ada!"})),
        (route("/greet")[0], 400),
        (route("/greet?name=")[0], 400),
        (route("/greet?name=" + "x" * (MAX_NAME_LEN + 1))[0], 400),
        (route("/greet?name=%3Cscript%3E")[0], 400),
        (route("/nope")[0], 404),
    ]
    failures = [(got, want) for got, want in checks if got != want]
    for got, want in failures:
        print(f"selfcheck FAIL: got {got!r}, want {want!r}", file=sys.stderr)
    print(f"selfcheck: {'FAIL' if failures else 'ok'} ({len(checks)} checks)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hello_svc.app", description=__doc__)
    parser.add_argument("--selfcheck", action="store_true",
                        help="verify routes in-process and exit (no port bound)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.selfcheck:
        return selfcheck()
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"hello-svc listening on http://127.0.0.1:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
