"""GitHub REST plumbing: pagination and sticky-comment upsert (mocked _api)."""

import subprocess

import pytest

from warden import github as github_mod

CTX = github_mod.PRContext(repo="o/r", number=1, base_sha="a" * 40, head_sha="b" * 40)


def _patch_api(monkeypatch, pages: list[list[dict]]) -> list[tuple]:
    calls: list[tuple] = []
    page_iter = iter(pages)

    def fake_api(path: str, method: str = "GET", payload: dict | None = None):
        calls.append((path, method, payload))
        if method == "GET":
            return next(page_iter, [])
        return {}

    monkeypatch.setattr(github_mod, "_api", fake_api)
    return calls


def test_multi_page_comments_parse_regression(monkeypatch):
    """review M2/M3-1 lineage: >100 comments must not brick the gate — full
    pages trigger a next-page fetch until a short/empty page."""
    full_page = [{"id": i, "body": "x"} for i in range(100)]
    page2 = [{"id": 200, "body": f"{github_mod.STICKY_MARKER}\nold"}]
    calls = _patch_api(monkeypatch, [full_page, page2])
    github_mod.upsert_sticky_comment(CTX, "new body", None)
    gets = [c for c in calls if c[1] == "GET"]
    assert len(gets) == 2
    assert "page=2" in gets[1][0]
    # marker found on page 2 -> PATCH the existing comment, not POST a new one
    patch_call = calls[-1]
    assert patch_call[1] == "PATCH"
    assert "issues/comments/200" in patch_call[0]
    assert github_mod.STICKY_MARKER in patch_call[2]["body"]


def test_no_existing_comment_posts_new(monkeypatch):
    calls = _patch_api(monkeypatch, [[{"id": 1, "body": "unrelated"}]])
    github_mod.upsert_sticky_comment(CTX, "new body", None)
    post_call = calls[-1]
    assert post_call[1] == "POST"
    assert post_call[0] == "repos/o/r/issues/1/comments"


def test_api_failure_raises(monkeypatch):
    def boom(path, method="GET", payload=None):
        raise github_mod.GitHubError("HTTP 403: nope")
    monkeypatch.setattr(github_mod, "_api", boom)
    with pytest.raises(github_mod.GitHubError):
        github_mod.upsert_sticky_comment(CTX, "x", None)


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(github_mod.GitHubError, match="GITHUB_TOKEN"):
        github_mod._token()


def test_api_bounds_every_request_with_a_timeout(monkeypatch):
    """urlopen's default is NO timeout, so a stalled socket would hang the
    gate in CI with no diagnostic, and in `warden mine` it would make the
    UNREAD row the honesty contract exists to write unreachable. Removing the
    timeout must turn this test red; nothing else in the suite covers it."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    seen = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def read(self): return b"[]"

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(github_mod.urllib.request, "urlopen", fake_urlopen)
    github_mod._api("repos/o/r/pulls")
    assert seen["timeout"] == github_mod.DEFAULT_TIMEOUT
    assert isinstance(seen["timeout"], (int, float)) and seen["timeout"] > 0


def test_api_timeout_is_overridable_per_call(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    seen = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def read(self): return b"[]"

    monkeypatch.setattr(github_mod.urllib.request, "urlopen",
                        lambda req, timeout=None: (seen.update(timeout=timeout)
                                                   or _Resp()))
    github_mod._api("repos/o/r/pulls", timeout=5)
    assert seen["timeout"] == 5


def test_a_gate_below_the_git_root_keeps_its_own_sticky_comment(monkeypatch, tmp_path):
    """Two enrollments in one repository gate the same pull request. Each keeps
    one comment: neither gate may overwrite the other's review."""
    mono, api = tmp_path / "mono", tmp_path / "mono" / "svc" / "api"
    api.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(mono)], check=True,
                   capture_output=True)
    assert github_mod.sticky_marker(mono) == github_mod.STICKY_MARKER
    assert github_mod.sticky_marker(None) == github_mod.STICKY_MARKER
    marker = github_mod.sticky_marker(api)
    assert marker != github_mod.STICKY_MARKER and "svc/api" in marker

    calls = _patch_api(monkeypatch, [[{"id": 7, "body": f"{github_mod.STICKY_MARKER}\nroot"}]])
    github_mod.upsert_sticky_comment(CTX, "api review", api)
    assert calls[-1][1] == "POST", "the subdirectory gate overwrote the root gate's comment"
    assert calls[-1][2]["body"].startswith(marker + "\n")

    calls = _patch_api(monkeypatch, [[{"id": 7, "body": f"{github_mod.STICKY_MARKER}\nroot"},
                                      {"id": 8, "body": f"{marker}\nold api review"}]])
    github_mod.upsert_sticky_comment(CTX, "api review", api)
    assert calls[-1][1] == "PATCH" and "issues/comments/8" in calls[-1][0]

    calls = _patch_api(monkeypatch, [[{"id": 8, "body": f"{marker}\nold api review"}]])
    github_mod.upsert_sticky_comment(CTX, "root review", mono)
    assert calls[-1][1] == "POST", "the root gate overwrote the subdirectory gate's comment"
