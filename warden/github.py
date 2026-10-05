"""GitHub PR plumbing: event payload parsing and the sticky provenance comment.

Direct REST calls via stdlib urllib (GITHUB_TOKEN/GH_TOKEN env) — no gh CLI
dependency, so CI behavior does not vary with the runner's bundled gh version,
and failures surface GitHub's own error body verbatim. One comment per PR,
found by a hidden marker and updated in place.
"""

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

STICKY_MARKER = "<!-- warden:sticky -->"
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
# urlopen's default is no timeout at all, so a stalled socket hangs the gate
# in CI with no diagnostic. Bounded here rather than at each call site: every
# caller wants a bound, and one that has to be remembered is one that gets
# forgotten.
DEFAULT_TIMEOUT = 30


class GitHubError(Exception):
    """A GitHub API call failed. STATUS is the HTTP status when GitHub
    answered, None when it did not (no token, no connection)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# The payload's `head.repo` key absent: the run cannot say where the head
# lives, so whether this is a fork is unknown, not "no".
_HEAD_REPO_UNKNOWN = "<unknown>"


@dataclass(frozen=True)
class PRContext:
    repo: str          # owner/name
    number: int
    base_sha: str
    head_sha: str
    # owner/name of the repository the head branch lives in; None when the
    # payload says `head.repo: null` (a deleted fork), `_HEAD_REPO_UNKNOWN`
    # when the payload carries no `head.repo` at all.
    head_repo: str | None = _HEAD_REPO_UNKNOWN

    def fork(self) -> bool | None:
        """True when the head lives outside the base repository, False when
        it lives in it, None when the payload does not say."""
        if self.head_repo == _HEAD_REPO_UNKNOWN:
            return None
        return self.head_repo != self.repo


def from_event(event_path: str) -> PRContext:
    """PR context from a GitHub Actions pull_request event payload."""
    try:
        event = json.loads(Path(event_path).read_text())
        pr = event["pull_request"]
        head = pr["head"]
        if "repo" not in head:
            head_repo = _HEAD_REPO_UNKNOWN
        elif head["repo"] is None:
            head_repo = None
        else:
            head_repo = head["repo"]["full_name"]
        return PRContext(repo=event["repository"]["full_name"],
                         number=pr["number"],
                         base_sha=pr["base"]["sha"],
                         head_sha=head["sha"],
                         head_repo=head_repo)
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as e:
        raise GitHubError(f"cannot read pull_request event from {event_path}: {e}") from e


def pr_head_sha_from_env() -> str | None:
    """The PR head commit this CI run reports about, from the Actions event.

    Deliberately tolerant and env-driven: no event, a non-PR event, or an
    unreadable payload means "not a PR run" — never an exception and never a
    guess. Env-driven rather than a flag so already-pinned consumer workflows
    get correct pairing without editing their YAML.
    """
    path = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    if not path:
        return None
    try:
        sha = json.loads(Path(path).read_text())["pull_request"]["head"]["sha"]
    except (OSError, TypeError, KeyError, json.JSONDecodeError):
        return None
    return sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{7,40}", sha) else None


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise GitHubError("GITHUB_TOKEN/GH_TOKEN is not set")
    return token


def _api(path: str, method: str = "GET", payload: dict | None = None,
         timeout: float = DEFAULT_TIMEOUT) -> object:
    req = urllib.request.Request(
        f"{API}/{path}", method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {_token()}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "warden"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:400]
        raise GitHubError(f"{method} {path} -> HTTP {e.code}: {body}",
                          status=e.code) from e
    except urllib.error.URLError as e:
        raise GitHubError(f"{method} {path} failed: {e.reason}") from e


def _list_comments(ctx: PRContext) -> list[dict]:
    comments: list[dict] = []
    page = 1
    while True:
        batch = _api(f"repos/{ctx.repo}/issues/{ctx.number}/comments"
                     f"?per_page=100&page={page}")
        if not isinstance(batch, list) or not batch:
            return comments
        comments += batch
        if len(batch) < 100:
            return comments
        page += 1


def sticky_marker(root: Path | None) -> str:
    """The hidden marker of the enrollment at ROOT's comment. The root
    enrollment keeps `STICKY_MARKER`; one below the git root, at `svc/api`,
    is `<!-- warden:sticky svc/api -->`, so two gates on one pull request
    each keep their own comment. ROOT None, or outside git, is the root's."""
    from . import gate_workflows
    if root is None:
        return STICKY_MARKER
    try:
        _, prefix = gate_workflows.git_location(root)
    except gate_workflows.GitLocationError:
        return STICKY_MARKER
    return f"<!-- warden:sticky {prefix} -->" if prefix else STICKY_MARKER


def upsert_sticky_comment(ctx: PRContext, body: str, root: Path | None) -> None:
    """Create or update the enrollment at ROOT's single warden comment on the
    PR. ROOT is required, not defaulted, so no caller can forget it."""
    marker = sticky_marker(root)
    body = f"{marker}\n{body}"
    existing = next((c for c in _list_comments(ctx)
                     if marker in (c.get("body") or "")), None)
    if existing:
        _api(f"repos/{ctx.repo}/issues/comments/{existing['id']}",
             method="PATCH", payload={"body": body})
    else:
        _api(f"repos/{ctx.repo}/issues/{ctx.number}/comments",
             method="POST", payload={"body": body})
