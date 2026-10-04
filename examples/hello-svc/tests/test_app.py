"""Tests for hello_svc.app — pytest-compatible plain functions, stdlib only.

Run from examples/hello-svc:  python3 -m pytest tests -q
"""

import json
import threading
import urllib.request
from http.server import HTTPServer

from hello_svc import app


# --- unit: greet input validation -------------------------------------------

def test_greet_happy_path():
    assert app.greet("Ada") == (200, {"greeting": "Hello, Ada!"})


def test_greet_allows_spaces_apostrophes_hyphens():
    status, body = app.greet("Ada Lovelace-O'Neil")
    assert status == 200
    assert body["greeting"] == "Hello, Ada Lovelace-O'Neil!"


def test_greet_missing_name_is_400():
    status, body = app.greet(None)
    assert status == 400
    assert "required" in body["error"]


def test_greet_empty_name_is_400():
    assert app.greet("")[0] == 400


def test_greet_oversized_name_is_400():
    assert app.greet("x" * (app.MAX_NAME_LEN + 1))[0] == 400
    assert app.greet("x" * app.MAX_NAME_LEN)[0] == 200


def test_greet_rejects_markup_and_control_characters():
    for bad in ("<script>", "a;rm -rf", "{}", "\n", " leading-space"):
        assert app.greet(bad)[0] == 400, bad


# --- unit: routing -----------------------------------------------------------

def test_route_health():
    assert app.route("/health") == (200, {"status": "ok"})


def test_route_greet_takes_first_query_value():
    assert app.route("/greet?name=Ada&name=Bob") == (200, {"greeting": "Hello, Ada!"})


def test_route_unknown_path_is_404():
    status, body = app.route("/nope")
    assert status == 404
    assert "/nope" in body["error"]


def test_selfcheck_passes():
    assert app.selfcheck() == 0


# --- integration: one real HTTP round-trip on an ephemeral port --------------

def test_http_round_trip():
    server = HTTPServer(("127.0.0.1", 0), app.Handler)  # port 0 = OS-assigned
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/health") as resp:
            assert resp.status == 200
            assert json.load(resp) == {"status": "ok"}
        with urllib.request.urlopen(f"{base}/greet?name=Ada") as resp:
            assert json.load(resp) == {"greeting": "Hello, Ada!"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_trailing_newline_rejected_regression():
    """review P1-5: `$` matched before a trailing newline, reflecting %0A into
    the greeting — the anchor is now \\Z."""
    from hello_svc.app import route
    status, _ = route("/greet?name=Ada%0A")
    assert status == 400
