"""PR corpus miner: read every merged PR, not just the attested ones.

`warden memory` sees only what produced an attestation shard. Everything
else is invisible to it — human hotfixes, PRs merged before enrollment,
anything that skipped the chain — and that blind spot is worst exactly where
the signal is richest. An emergency fix at 2am is often the most informative
commit in a repo, and it is the one least likely to have gone through a
review round.

This reads full history from git and the GitHub REST API (stdlib urllib, the
same seam `github.py` uses — no gh CLI dependency, no paid ingestion) and
extracts defect signals into an artifact under `.warden/out/`. Rows carry a
link, so any claim here can be checked back to a real commit or PR. It is
evidence, not a verdict.

**The honesty contract.** A signal class this could not read is reported as
UNAVAILABLE with a reason, and is absent from the counts entirely. It is
never reported as a zero. A zero means "looked, found nothing"; unavailable
means "could not look". A corpus that conflates them asserts a clean history
it never examined, which is worse than having no corpus at all.

Limits stated rather than papered over: git history shows what changed, not
why; a revert may be a product decision rather than a defect; and a
squash-merging repo has already thrown away the intermediate signal before
this ever runs.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .runs import git_env

SCHEMA = 1

# Read from git alone: always available in a git repo, so a zero here is a
# real zero.
GIT_CLASSES = ("revert", "fix-after-merge")
# Need a GitHub reader. Absent token, absent network, or an API error means
# UNAVAILABLE — named, never counted as zero.
GH_CLASSES = ("review-comment-precedes-change", "ci-failure", "merge-actor")

# Subjects that claim to repair something. Deliberately narrow: 'improve' and
# 'update' describe work, not defects, and folding them in would make every
# row unfalsifiable.
# The (?![-\w]) tail matters: '-' is a non-word character, so a plain \b
# closes on it and 'feat(ui): fixed-width columns' would read as a repair.
# A row claiming a feature commit repaired a PR is exactly the
# unfalsifiable kind this pattern was kept narrow to avoid.
_FIX_SUBJECT = re.compile(r"^(fix|hotfix|revert|bugfix)(?![-\w])"
                          r"|\bfix(es|ed)?(?![-\w])", re.IGNORECASE)
_REVERT_SUBJECT = re.compile(r"^Revert\b")
_REVERTS_COMMIT = re.compile(r"This reverts commit ([0-9a-f]{7,40})")
_MERGE_PR = re.compile(r"^Merge pull request #(\d+)\b")
# git diff -U0 old-file hunk header: @@ -start,count +start,count @@
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+")

_MAX_PRS = 200          # one page-walk's worth; the artifact records the cap
# A narrow --since filters client-side, so the walk cannot stop when a page
# falls out of the window (the list is sorted by UPDATED, not merged). Bounded
# here, and the bound is stated whenever it bites.
_MAX_PAGES = 10
# Comments and check-runs cost ONE REQUEST PER PR. Unbounded, a 91-PR repo
# makes ~182 calls and can time out mid-sweep, which correctly reports the
# class as unread but reads nothing. A bound keeps the class actually readable; the
# artifact always states it when it truncates (no silent caps).
_DEFAULT_PR_DETAIL = 40
_MAX_BLAME_LINES = 400  # a rename-sized diff would blame forever
# Without a timeout a stalled socket wedges the command forever and the
# UNREAD row it exists to write is never reached. `catalog check --online`
# bounds its own fetches the same way.
_HTTP_TIMEOUT = 20


# The injected GitHub reader: takes an API path, returns decoded JSON. Raises
# MineError when it cannot read — which is what turns a class UNREAD instead
# of into a silent zero.
Reader = Callable[[str], object]


class MineError(Exception):
    pass


@dataclass(frozen=True)
class Signal:
    kind: str
    ref: str      # the commit sha or PR number this row is about
    link: str     # something a reader can open to check the row
    summary: str
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "link": self.link,
                "summary": self.summary, "detail": self.detail}


@dataclass(frozen=True)
class Partial:
    """A class that WAS read, with named gaps.

    Unavailable is all-or-nothing and too blunt for the git side: dropping a
    handful of commits must not discard the hundreds that read fine, but it
    must not vanish either. Every drop names the ref it lost and why, so a
    count can be trusted exactly as far as this list is empty.
    """
    signal_class: str
    ref: str
    reason: str

    def to_dict(self) -> dict:
        return {"class": self.signal_class, "ref": self.ref,
                "reason": self.reason}


@dataclass(frozen=True)
class Unavailable:
    signal_class: str
    reason: str

    def to_dict(self) -> dict:
        return {"class": self.signal_class, "reason": self.reason}


@dataclass(frozen=True)
class MineResult:
    head: str
    repo_slug: str | None
    since: str | None
    merge_style: str
    signals: tuple[Signal, ...]
    unavailable: tuple[Unavailable, ...]
    limits: tuple[str, ...]
    prs_read: int | None = None   # the DENOMINATOR the GitHub-side zeros were
    #                               counted over. None means GitHub was never
    #                               read at all — 0 would claim an empty repo.
    prs_detailed: int | None = None  # how many of those got per-PR requests
    partial: tuple[Partial, ...] = ()  # rows a READ class could not attribute

    def counts(self) -> dict[str, int]:
        """Per-class counts for the classes that were actually READ.

        An unavailable class is omitted, not zeroed — the omission is the
        signal. Callers that want the full picture read `unavailable` too.
        """
        unread = {u.signal_class for u in self.unavailable}
        out = {c: 0 for c in GIT_CLASSES + GH_CLASSES if c not in unread}
        for signal in self.signals:
            if signal.kind in out:
                out[signal.kind] += 1
        return out

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "head": self.head,
            "repo": self.repo_slug,
            "since": self.since,
            "merge_style": self.merge_style,
            "prs_read": self.prs_read,
            "prs_detailed": self.prs_detailed,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "counts": self.counts(),
            "unavailable": [u.to_dict() for u in self.unavailable],
            "partial": [p.to_dict() for p in self.partial],
            "limits": list(self.limits),
            "signals": [s.to_dict() for s in self.signals],
        }


# --------------------------------------------------------------------------
# git plumbing
# --------------------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    # GIT_LITERAL_PATHSPECS: a git pathspec is a wildmatch PATTERN by default,
    # so `diff -- 'a?.py'` also matches ab.py and this module would attribute
    # another file's hunks to it — F1's misattribution through a second door,
    # reintroduced by the per-file diff that fixed the first one. Set on the
    # whole seam rather than per call site: every pathspec here is a literal
    # path read out of git, and a flag that must be remembered gets forgotten.
    env = git_env()
    env["GIT_LITERAL_PATHSPECS"] = "1"
    try:
        return subprocess.run(["git", *args], cwd=root, check=True,
                              capture_output=True, text=True,
                              env=env).stdout
    except FileNotFoundError as e:
        raise MineError("git is not on PATH") from e
    except subprocess.CalledProcessError as e:
        raise MineError(f"git {' '.join(args)} failed: "
                        f"{e.stderr.strip()[:200]}") from e


def _log(root: Path, since: str | None, *extra: str) -> list[tuple[str, str]]:
    """(sha, subject) newest-first, honoring the --since window."""
    args = ["log", "--no-merges", "--format=%H%x00%s", *extra]
    if since:
        args.insert(1, f"--since={since}")
    out = _git(root, *args)
    rows = []
    for line in out.splitlines():
        if "\0" in line:
            sha, subject = line.split("\0", 1)
            rows.append((sha, subject))
    return rows


def detect_merge_style(root: Path) -> str:
    """How this repo lands PRs — which decides what signal even survives."""
    merges = _git(root, "log", "--merges", "--format=%s", "-20").splitlines()
    if any(_MERGE_PR.match(m) for m in merges):
        return "merge-commits"
    if merges:
        return "merge-commits-unlabelled"
    return "squash-or-rebase"


def _pr_for_commit(root: Path, sha: str) -> tuple[int | None, str | None]:
    """The merge commit that brought `sha` onto the current branch, and its PR.

    `--ancestry-path` walks forward from the commit; the LAST merge on that
    path is the one that landed it. A commit made directly on main has no
    such merge, and the answer is honestly (None, None) rather than a guess.
    """
    try:
        out = _git(root, "log", "--merges", "--ancestry-path",
                   "--format=%H%x00%s", f"{sha}..HEAD")
    except MineError:
        return None, None
    rows = [line.split("\0", 1) for line in out.splitlines() if "\0" in line]
    if not rows:
        return None, None
    merge_sha, subject = rows[-1]
    match = _MERGE_PR.match(subject)
    return (int(match.group(1)) if match else None), merge_sha


def find_reverts(root: Path, since: str | None) -> list[Signal]:
    signals = []
    for sha, subject in _log(root, since):
        if not _REVERT_SUBJECT.match(subject):
            continue
        body = _git(root, "log", "-1", "--format=%B", sha)
        reverted = _REVERTS_COMMIT.search(body)
        target = reverted.group(1) if reverted else "unrecorded"
        pr, _ = _pr_for_commit(root, sha)
        signals.append(Signal(
            kind="revert", ref=sha, link=f"git show {sha}",
            summary=f"{subject} — reverts {target[:8]}",
            detail={"reverted": target, "subject": subject, "pr": pr}))
    return signals


def _changed_files(root: Path, sha: str) -> list[str]:
    """Paths this commit touched, read NUL-separated.

    Never parsed out of `--- a/<path>` headers. git C-quotes any path needing
    it (`core.quotepath` is on by default), so a non-ASCII path renders as
    `--- "a/caf\\303\\251.py"` and matches no `--- a/` prefix, while a path
    with a space gets a trailing TAB. Both cases would leave a header parser
    holding the PREVIOUS file's name, so one file's hunks would be attributed
    to another and the row would blame an innocent commit. `-z` has no
    quoting and no delimiter ambiguity.
    """
    out = _git(root, "diff", "--name-only", "-z", f"{sha}^", sha)
    return [name for name in out.split("\0") if name]


def _replaced_line_ranges(root: Path, sha: str,
                          name: str) -> list[tuple[int, int]]:
    """The OLD line ranges this commit replaced or removed in ONE file.

    Replaced lines are the ones that were wrong. A commit that only ADDS
    lines replaced nothing, so it repaired nothing — reporting it would put
    an uncheckable row in a corpus whose value is that rows check out.

    Diffed one path at a time, so a hunk header cannot be attributed to the
    wrong file no matter how git renders the path.
    """
    diff = _git(root, "diff", "-U0", f"{sha}^", sha, "--", name)
    ranges: list[tuple[int, int]] = []
    for line in diff.splitlines():
        match = _HUNK.match(line)
        if not match:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        if count:  # count 0 == pure insertion at this point
            ranges.append((start, count))
    return ranges


def find_fix_after_merge(root: Path, since: str | None,
                         partial: list[Partial]) -> list[Signal]:
    """Fixes that replaced lines a merged PR introduced.

    Appends to `partial` for anything it could not read. A git class is
    declared one whose zero is a real zero, so a commit dropped here without
    a word would make that promise false.
    """
    signals = []
    for sha, subject in _log(root, since):
        if _REVERT_SUBJECT.match(subject) or not _FIX_SUBJECT.search(subject):
            continue
        try:
            files = _changed_files(root, sha)
        except MineError as e:
            partial.append(Partial("fix-after-merge", sha,
                                   f"cannot read the diff: {e}"))
            continue

        ranges = {}
        for name in files:
            try:
                spans = _replaced_line_ranges(root, sha, name)
            except MineError as e:
                partial.append(Partial("fix-after-merge", sha,
                                       f"cannot diff {name}: {e}"))
                continue
            if spans:
                ranges[name] = spans
        if not ranges:
            continue

        introduced: dict[str, tuple[int | None, str | None]] = {}
        blamed: list[str] = []
        budget = _MAX_BLAME_LINES
        truncated = False
        for name, spans in sorted(ranges.items()):
            if budget <= 0:
                # The budget is per COMMIT, so exhausting it skips every file
                # left. Breaking the inner loop only would leave the row
                # claiming files it never blamed.
                truncated = True
                break
            examined = False
            for start, count in spans:
                if budget <= 0:
                    truncated = True
                    break
                budget -= count
                end = start + max(count, 1) - 1
                try:
                    blame = _git(root, "blame", "-l", "-L", f"{start},{end}",
                                 f"{sha}^", "--", name)
                except MineError as e:
                    partial.append(Partial("fix-after-merge", sha,
                                           f"cannot blame {name}:{start}: {e}"))
                    continue
                examined = True
                for row in blame.splitlines():
                    origin = row.split(" ", 1)[0].lstrip("^")
                    if len(origin) >= 7:
                        introduced.setdefault(origin, _pr_for_commit(root, origin))
            if examined:
                blamed.append(name)

        if truncated:
            partial.append(Partial(
                "fix-after-merge", sha,
                f"blame budget of {_MAX_BLAME_LINES} lines exhausted — "
                f"attributed from {len(blamed)} of {len(ranges)} changed "
                "file(s), so the PRs named may be incomplete"))
        if not introduced:
            if ranges:
                partial.append(Partial(
                    "fix-after-merge", sha,
                    "replaced lines were found but none could be attributed "
                    "to an introducing commit"))
            continue
        prs = sorted({pr for pr, _ in introduced.values() if pr is not None})
        signals.append(Signal(
            kind="fix-after-merge", ref=sha,
            link=f"git show {sha}",
            summary=f"{subject} — repairs {', '.join(sorted(blamed))}",
            detail={"subject": subject, "files": sorted(ranges),
                    "files_attributed": sorted(blamed),
                    "truncated": truncated,
                    "introduced_by": sorted(introduced),
                    "pr": prs[0] if prs else None,
                    "prs": prs}))
    return signals


# --------------------------------------------------------------------------
# github-side signals
# --------------------------------------------------------------------------


def default_reader() -> Reader | None:
    """A GitHub reader over the same stdlib seam `github.py` uses, or None.

    Returns None rather than raising when no token is configured: absent
    credentials is a normal state for a laptop run, and the caller reports
    the affected classes as unavailable.
    """
    import os

    from . import github as github_mod

    if not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")):
        return None

    def reader(path: str) -> object:
        try:
            return github_mod._api(path, timeout=_HTTP_TIMEOUT)
        except github_mod.GitHubError as e:
            raise MineError(str(e)) from e
        except Exception as e:  # noqa: BLE001 — a read-phase ConnectionReset
            # or IncompleteRead is not a GitHubError, and letting it escape
            # would discard every git-side signal already collected before any
            # artifact was written. Everything the reader can
            # raise must arrive as MineError so the class goes UNREAD.
            raise MineError(f"{type(e).__name__}: {e}") from e
    return reader


def parse_since(since: str | None) -> datetime | None:
    """`--since` as a datetime, or None when it is not an absolute date.

    git accepts relative windows ('2 weeks ago') that no stdlib parser
    understands. Rather than guess, an unparseable window is reported to the
    caller so it can SAY the GitHub classes were not filtered — the git side
    still honors it, because git parsed it.
    """
    if not since:
        return None
    text = since.strip().replace("Z", "+00:00")
    for shape in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            parsed = datetime.strptime(text, shape)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _merged_at(pull: dict) -> datetime | None:
    """A PR's merge time as an AWARE datetime, or None if it cannot be read.

    Always aware: GitHub stamps a `Z`, but a naive value slipping through made
    `naive >= after` raise TypeError, which is not a MineError and so escaped
    `mine()` entirely — discarding every git-side signal already collected,
    the exact failure the reader's error mapping exists to prevent.
    """
    raw = (pull.get("merged_at") or "").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _merged_pulls(gh: Reader, repo_slug: str, after: datetime | None = None,
                  notes: list[str] | None = None) -> list[dict]:
    """Merged PRs, newest-updated first, optionally bounded by `after`.

    `notes` collects anything a reader must be told: PRs whose merge time
    could not be read, and a page walk that hit its bound.
    """
    pulls: list[dict] = []
    unreadable = 0
    page = 1
    while len(pulls) < _MAX_PRS and page <= _MAX_PAGES:
        batch = gh(f"repos/{repo_slug}/pulls?state=closed&per_page=100"
                   f"&page={page}&sort=updated&direction=desc")
        if not isinstance(batch, list) or not batch:
            break
        merged = [p for p in batch if p.get("merged_at")]
        if after is not None:
            # The pulls endpoint has no `since`, so the window is applied
            # here. Without it the GitHub classes would be counted over all
            # time while the artifact stamped a window beside them.
            kept = []
            for pull in merged:
                when = _merged_at(pull)
                if when is None:
                    # Cannot place it in or out of the window. Keeping it is
                    # the direction that does not silently shrink the corpus,
                    # but a reader has to be told the window did not decide.
                    unreadable += 1
                    kept.append(pull)
                elif when >= after:
                    kept.append(pull)
            merged = kept
        pulls += merged
        if len(batch) < 100:
            break
        page += 1

    if notes is not None:
        if unreadable:
            notes.append(f"{unreadable} merged PR(s) carried a merge time "
                         "this could not read and were counted regardless of "
                         "--since — the window did not decide them")
        if page > _MAX_PAGES:
            # Unbounded, a narrow --since walks the entire closed-PR list, one
            # 20s-timeout request per page, so the bound is stated when it bites.
            notes.append(f"the PR page walk stopped at its bound of "
                         f"{_MAX_PAGES} pages ({_MAX_PAGES * 100} closed PRs "
                         "scanned) — merged PRs older than that were not seen")
    return pulls[:_MAX_PRS]


def find_review_comment_signals(gh: Reader, repo_slug: str,
                                pulls: list[dict]) -> list[Signal]:
    """Review comments a human left on a diff — someone caught something there."""
    signals = []
    for pr in pulls:
        number = pr.get("number")
        for comment in gh(f"repos/{repo_slug}/pulls/{number}/comments?per_page=100") or []:
            path = comment.get("path")
            if not path:
                continue
            signals.append(Signal(
                kind="review-comment-precedes-change", ref=f"PR #{number}",
                link=comment.get("html_url") or pr.get("html_url", ""),
                summary=f"review comment on {path} in PR #{number}",
                detail={"file": path, "pr": number,
                        "author": (comment.get("user") or {}).get("login"),
                        "body": (comment.get("body") or "")[:300]}))
    return signals


def find_ci_failures(gh: Reader, repo_slug: str,
                     pulls: list[dict]) -> list[Signal]:
    signals = []
    for pr in pulls:
        sha = (pr.get("head") or {}).get("sha")
        if not sha:
            continue
        runs = (gh(f"repos/{repo_slug}/commits/{sha}/check-runs") or {})
        for run in (runs.get("check_runs") if isinstance(runs, dict) else []) or []:
            if run.get("conclusion") != "failure":
                continue
            signals.append(Signal(
                kind="ci-failure", ref=f"PR #{pr.get('number')}",
                link=run.get("html_url") or pr.get("html_url", ""),
                summary=f"{run.get('name')} failed on PR #{pr.get('number')}",
                detail={"check": run.get("name"), "pr": pr.get("number"),
                        "head": sha}))
    return signals


def declared_merge_logins(root: Path) -> tuple[str | None, list[str]]:
    """(node name holding authority:merge, the github logins it declares).

    No graph, no merge node, or a merge node with no `github:` list all mean
    the same thing for this check: nothing binds a real merge event to the
    declared org, and the caller reports it
    as unavailable rather than as "no drift".
    """
    from . import graph as graph_mod

    try:
        doc = graph_mod.load(root)
    except Exception:  # noqa: BLE001 — a broken graph is "cannot bind", never a crash
        return None, []
    nodes = doc.get("nodes") or {}
    if not isinstance(nodes, dict):
        return None, []
    for name, node in nodes.items():
        if isinstance(node, dict) and node.get("authority") == "merge":
            logins = node.get("github") or []
            if not isinstance(logins, list):
                logins = []
            return name, [str(login) for login in logins]
    return None, []


def find_merge_actor_drift(root: Path, pulls: list[dict],
                           node: str, logins: list[str]) -> list[Signal]:
    signals = []
    allowed = {login.lower() for login in logins}
    for pr in pulls:
        actor = ((pr.get("merged_by") or {}).get("login") or "").strip()
        if not actor or actor.lower() in allowed:
            continue
        signals.append(Signal(
            kind="merge-actor", ref=f"PR #{pr.get('number')}",
            link=pr.get("html_url", ""),
            summary=(f"PR #{pr.get('number')} merged by {actor}, who is not a "
                     f"declared login of the '{node}' node"),
            detail={"actor": actor, "declared_node": node,
                    "declared_logins": logins, "pr": pr.get("number")}))
    return signals


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def repo_slug_from_git(root: Path) -> str | None:
    try:
        url = _git(root, "remote", "get-url", "origin").strip()
    except MineError:
        return None
    match = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", url)
    return match.group(1) if match else None


def mine(root: Path, *, since: str | None = None, gh: Reader | None = None,
         repo_slug: str | None = None,
         pr_limit: int = _DEFAULT_PR_DETAIL) -> MineResult:
    """Mine defect signals from history. `gh=None` means the GitHub-side
    classes go unread, and they are named as such."""
    root = Path(root)
    head = _git(root, "rev-parse", "HEAD").strip()
    merge_style = detect_merge_style(root)

    partial: list[Partial] = []
    signals: list[Signal] = list(find_reverts(root, since))
    signals += find_fix_after_merge(root, since, partial)

    unavailable: list[Unavailable] = []
    limits: list[str] = [
        "git history shows WHAT changed, not why — a row is a place to look, "
        "not a proven defect",
        "a revert may be a product decision rather than a defect",
    ]
    if merge_style == "squash-or-rebase":
        limits.append("this history has no merge commits, so it squash- or "
                      "rebase-merges: the intermediate commits and their "
                      "review context were discarded before this ran, and "
                      "fix-after-merge cannot name the PR it repaired")

    slug = repo_slug or repo_slug_from_git(root)
    if gh is None:
        for cls in GH_CLASSES:
            unavailable.append(Unavailable(
                cls, "no GitHub reader: set GITHUB_TOKEN or GH_TOKEN "
                     "(this class was NOT read — it is not a zero)"))
        return MineResult(head, slug, since, merge_style, tuple(signals),
                          tuple(unavailable), tuple(limits),
                          partial=tuple(partial))

    if not slug:
        for cls in GH_CLASSES:
            unavailable.append(Unavailable(
                cls, "no github.com remote named 'origin' — cannot resolve "
                     "owner/name to read from"))
        return MineResult(head, slug, since, merge_style, tuple(signals),
                          tuple(unavailable), tuple(limits),
                          partial=tuple(partial))

    after = parse_since(since)
    if since and after is None:
        limits.append(
            f"--since {since!r} is a relative window git understands and this "
            "does not, so it was applied to the git-side classes ONLY — the "
            "GitHub-side counts below are over all time. Pass an ISO date "
            "(YYYY-MM-DD) to bound both.")
    try:
        pulls = _merged_pulls(gh, slug, after, limits)
    except MineError as e:
        for cls in GH_CLASSES:
            unavailable.append(Unavailable(cls, f"GitHub read failed: {e}"))
        return MineResult(head, slug, since, merge_style, tuple(signals),
                          tuple(unavailable), tuple(limits),
                          partial=tuple(partial))

    prs_read = len(pulls)
    detailed = pulls[:max(pr_limit, 0)]
    if len(detailed) < prs_read:
        limits.append(
            f"per-PR detail (review comments, check runs) read for the "
            f"{len(detailed)} most-recently-updated of {prs_read} merged PRs "
            "— those classes are counted over that subset, not all of them")
    if len(pulls) >= _MAX_PRS:
        limits.append(f"PR read capped at {_MAX_PRS} most-recently-updated "
                      "merged PRs — older PRs were not examined")

    for cls, extractor in (
        ("review-comment-precedes-change",
         lambda: find_review_comment_signals(gh, slug, detailed)),
        ("ci-failure", lambda: find_ci_failures(gh, slug, detailed)),
    ):
        try:
            signals += extractor()
        except MineError as e:
            unavailable.append(Unavailable(cls, f"GitHub read failed: {e}"))

    node, logins = declared_merge_logins(root)
    if not node:
        unavailable.append(Unavailable(
            "merge-actor", "no graph.yaml node holds authority:merge, so a "
            "real merge event cannot be checked against a declared one"))
    elif not logins:
        unavailable.append(Unavailable(
            "merge-actor", f"the '{node}' node holds authority:merge but "
            "declares no `github:` logins — nothing binds a real merge event "
            "to the declared org, so this is unread, NOT 'no drift'"))
    else:
        signals += find_merge_actor_drift(root, pulls, node, logins)

    return MineResult(head, slug, since, merge_style, tuple(signals),
                      tuple(unavailable), tuple(limits), prs_read,
                      len(detailed), tuple(partial))


def render(result: MineResult) -> str:
    """Terminal summary. Carries the same honesty as the artifact: a reader
    who never opens the JSON still learns which classes went unread."""
    lines = [f"warden mine — {result.head[:12]} · merge style: {result.merge_style}"]
    counts = result.counts()
    if counts:
        lines.append("")
        for cls in sorted(counts):
            lines.append(f"  {counts[cls]:>4}  {cls}")
    if result.prs_read is not None:
        # The denominator the GitHub-side zeros were counted over: 0 findings
        # over 40 PRs and 0 over 0 PRs are different facts.
        lines.append("")
        lines.append(f"  GitHub-side classes counted over "
                     f"{result.prs_read} merged PR(s).")
    if result.unavailable:
        lines.append("")
        lines.append("  NOT READ (absent from the counts above — an unread "
                     "class is not a zero):")
        for entry in result.unavailable:
            lines.append(f"    {entry.signal_class}: {entry.reason}")
    if result.partial:
        lines.append("")
        lines.append(f"  PARTIAL ({len(result.partial)}) — these classes were "
                     "read, but these rows could not be attributed. The counts "
                     "above are short by at most this many:")
        for entry in result.partial[:10]:
            lines.append(f"    {entry.signal_class} {entry.ref[:12]}: "
                         f"{entry.reason}")
        if len(result.partial) > 10:
            lines.append(f"    ... and {len(result.partial) - 10} more "
                         "(see partial[] in pr-corpus.json)")
    if result.limits:
        lines.append("")
        lines.append("  limits:")
        for limit in result.limits:
            lines.append(f"    - {limit}")
    return "\n".join(lines)


def write_artifact(run_dir: Path, result: MineResult) -> Path:
    path = run_dir / "pr-corpus.json"
    path.write_text(json.dumps(result.to_dict(), indent=2) + "\n")
    return path
