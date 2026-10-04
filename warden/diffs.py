"""Diff acquisition for the review gate.

The diff is taken as base...head (merge-base three-dot, matching what a PR
shows). DiffContext carries the structured view the deterministic checkers
consume: changed files, added/removed lines with new-file line numbers, and
readers for file content at base and head.
"""

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import gate_workflows
from .rules import glob_match
from .runs import git_env


class DiffError(Exception):
    pass


@dataclass(frozen=True)
class DiffContext:
    base: str    # resolved sha
    head: str    # resolved sha
    files: tuple[str, ...]
    # per file: added lines as (new-file line number, text without '+')
    added: dict[str, tuple[tuple[int, str], ...]]
    # per file: removed line texts (without '-')
    removed: dict[str, tuple[str, ...]]
    # content readers; None when the file doesn't exist at that rev.
    # Injectable so checker unit tests need no git repo.
    read_base: Callable[[str], str | None]
    read_head: Callable[[str], str | None]
    # Every changed file BEFORE context_excludes were applied.
    # `files` is scanned (exclude-filtered); `all_files` lets a checker observe
    # that a companion tree changed — e.g. tests/ — WITHOUT scanning its
    # excluded content. Superset of `files`; defaults to `files` for a
    # hand-built context that predates the distinction, so an unset value is
    # never smaller than what is scanned.
    all_files: tuple[str, ...] = ()
    # The same range read with rename detection OFF.
    # `all_files` reports a move as its DESTINATION alone, so it cannot answer
    # "which subtrees did this range touch" — the question the verify-scope
    # derivation asks, and a move touches two. Superset of `all_files`;
    # defaults to it for a hand-built context, so an unset value is never
    # smaller than what is scanned.
    scope_files: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.all_files and self.files:
            object.__setattr__(self, "all_files", self.files)
        if not self.scope_files:
            object.__setattr__(self, "scope_files", self.all_files)


_HUNK = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def run_git(root: Path, *args: str, env: dict | None = None) -> str:
    try:
        # env: a hook-exported GIT_DIR would silently retarget every diff
        # command at the invoking repo instead of `root`.
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True,
                              text=True, env={**git_env(), **(env or {})})
    except FileNotFoundError as e:
        raise DiffError("git executable not found") from e
    if proc.returncode != 0:
        raise DiffError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def git_blobs(root: Path, shas: list[str]) -> dict[str, bytes]:
    """Each blob in `shas`, by sha, from one `git cat-file --batch`.

    One process however many blobs: a reader that spawns `git show` per file
    pays a process per file, and over a store that only grows that is a cost
    every caller pays more of each time it runs.

    Raises DiffError for an object the store lacks: git answers that on stdout
    as `<sha> missing` and exits 0, so the exit code alone cannot show it.
    """
    if not shas:
        return {}
    try:
        proc = subprocess.run(["git", "cat-file", "--batch"], cwd=root,
                              input="\n".join(shas).encode() + b"\n",
                              capture_output=True, env=git_env())
    except FileNotFoundError as e:
        raise DiffError("git executable not found") from e
    if proc.returncode != 0:
        raise DiffError(
            f"git cat-file --batch failed: {proc.stderr.decode().strip()}")
    out, at, blobs = proc.stdout, 0, {}
    for sha in shas:
        end = out.find(b"\n", at)
        header = out[at:end].split() if end >= 0 else []
        if len(header) != 3 or not header[2].isdigit():
            answer = b" ".join(header[1:]).decode(errors="replace")
            raise DiffError(
                f"git object {sha} cannot be read: {answer or 'no answer'}")
        size = int(header[2])
        blobs[sha] = out[end + 1:end + 1 + size]
        at = end + 1 + size + 1
    return blobs


def stale_base(root: Path, base: str) -> tuple[str, str] | None:
    """(upstream ref name, upstream sha) when `base` is BEHIND or DIVERGED from
    its own upstream — otherwise None.

    A review round minted with `--base main` names the LOCAL ref, while
    `git fetch` moves only `origin/main`. When local main is behind, every
    round manifest records a base describing a state nobody is reviewing
    against — which breaks the precondition `warden round classify`'s
    subtractions silently assume and cannot check, because the manifests are
    their only input.

    Narrow by design, and the narrowness is the fail-closed reading here rather
    than its opposite:

    - no upstream configured — `origin/main`, a bare sha, a local-only branch —
      makes no staleness claim this can read, so None. Refusing "cannot tell"
      would refuse every mint in a clone with no remote.
    - upstream identical, or an ANCESTOR of `base` (the branch is ahead), is
      not stale: None.
    - anything else — behind, or diverged — returns the upstream, and the
      caller refuses.
    """
    try:
        name = run_git(root, "rev-parse", "--abbrev-ref",
                       f"{base}@{{upstream}}").strip()
        upstream = run_git(root, "rev-parse", "--verify",
                           f"{base}@{{upstream}}^{{commit}}").strip()
        here = run_git(root, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
    except DiffError:
        return None  # no upstream to compare against: no claim to read
    if upstream == here:
        return None
    proc = subprocess.run(["git", "merge-base", "--is-ancestor", upstream, here],
                          cwd=root, capture_output=True, text=True, env=git_env())
    if proc.returncode == 0:
        return None  # base is AHEAD of its upstream, which is not stale
    return name, upstream


def annotate(file_diff: str) -> str:
    """Prefix diff body lines with new-file line numbers (`N:`; removed lines `-:`)."""
    out = []
    new_line = 0
    in_hunk = False
    for line in file_diff.splitlines():
        m = _HUNK.match(line)
        if m:
            new_line = int(m.group(1))
            in_hunk = True
            out.append(line)
        elif in_hunk and line.startswith("\\"):
            # "\ No newline at end of file" — passthrough WITHOUT leaving hunk
            # state, or every following added line loses its number.
            out.append(line)
        elif in_hunk and line.startswith("-"):
            out.append(f"   -: {line}")
        elif in_hunk and (line.startswith("+") or line.startswith(" ")):
            out.append(f"{new_line:>5}: {line}")
            new_line += 1
        else:
            in_hunk = False
            out.append(line)
    return "\n".join(out)


def parse_lines(file_diff: str) -> tuple[tuple[tuple[int, str], ...], tuple[str, ...]]:
    """Added lines (numbered against the new file) and removed line texts."""
    added: list[tuple[int, str]] = []
    removed: list[str] = []
    new_line = 0
    in_hunk = False
    for line in file_diff.splitlines():
        m = _HUNK.match(line)
        if m:
            new_line = int(m.group(1))
            in_hunk = True
        elif in_hunk and line.startswith("\\"):
            continue  # "\ No newline at end of file"
        elif in_hunk and line.startswith("-"):
            removed.append(line[1:])
        elif in_hunk and line.startswith("+"):
            added.append((new_line, line[1:]))
            new_line += 1
        elif in_hunk and (line.startswith(" ") or line == ""):
            # "" tolerates blank context lines whose leading space was stripped
            # in transit (editors/pagers) — dropping out of the hunk there would
            # silently skip every following added line.
            new_line += 1
        else:
            in_hunk = False
    return tuple(added), tuple(removed)


def _read_at(root: Path, rev: str) -> Callable[[str], str | None]:
    def read(path: str) -> str | None:
        # `./`: the path is relative to ROOT, which may be below the git root.
        proc = subprocess.run(["git", "show", f"{rev}:./{path}"], cwd=root,
                              capture_output=True, text=True)
        return proc.stdout if proc.returncode == 0 else None
    return read


def changed_files(root: Path, base: str, head: str = "HEAD") -> tuple[str, ...]:
    """Every path base...head touched, on BOTH sides of a rename, without
    reading a single file's content.

    `--no-renames` is the whole difference from `get_context`'s file list and
    it is load-bearing. With rename detection on —
    git's default since 2.9 — `git mv examples/hello-svc/x.py warden/x.py`
    reports the DESTINATION alone, so a scope covering only the subtree the
    file LEFT is never required for a diff that emptied it. This function
    answers "which paths did this range touch", which is the question the
    verify-scope derivation asks, and a move touches two.

    `--relative`, here and in the listings below: a repository enrolled
    below the git root reads only the paths inside it, relative to it, which
    are the paths its globs and components name, and one more: its own gate
    workflow at the git root, when the range changed it (`_root_gate`). At
    the git root it changes nothing.
    """
    return _nul_names(root, base, head, "--no-renames")


def _root_gate(root: Path) -> tuple[str, str] | None:
    """Below the git root, the enrollment's own gate workflow at the root: a
    pathspec naming it from the top of the tree, and the path it is listed by,
    relative to the enrollment (`../../.github/workflows/warden-svc-api.yml`).
    A pull request that weakens that file changes nothing inside the
    enrollment, so without it the enrollment's review never read the edit.
    None at the git root, or where git cannot place ROOT."""
    try:
        _, prefix = gate_workflows.git_location(root)
    except gate_workflows.GitLocationError:
        return None
    if not prefix:
        return None
    top = f"{gate_workflows.WORKFLOWS_DIR.as_posix()}/{gate_workflows.workflow_name(prefix)}"
    return f":(top,literal){top}", gate_workflows.root_gate_path(prefix)


def _nul_names(root: Path, base: str, head: str, *opts: str) -> tuple[str, ...]:
    # NUL-terminated, the only listing git never C-quotes. A plain
    # `--name-only` prints `café.py` as `"caf\303\251.py"` under the default
    # core.quotepath, and `q"x.py` as `"q\"x.py"` whatever it is set to: a
    # string naming no file, so the scan read no added line from it.
    names = tuple(n for n in
                  run_git(root, "diff", "--relative", *opts, "--name-only", "-z",
                          f"{base}...{head}").split("\0")
                  if n)
    gate = _root_gate(root)
    if gate and run_git(root, "diff", *opts, "--name-only", "-z", f"{base}...{head}",
                        "--", gate[0]).strip("\0"):
        names += (gate[1],)
    return names


def range_paths(root: Path, base: str, head: str) -> tuple[str, ...]:
    """Every path base...head changed inside ROOT, relative to it, read
    NUL-terminated so git never C-quotes one. The file list `attest write`
    stamps, `memory ingest` re-checks, and review and explain scan and tier."""
    return _nul_names(root, base, head)


def get_context(root: Path, base: str, head: str = "HEAD",
                excludes: tuple[str, ...] = ()) -> DiffContext:
    base_sha = run_git(root, "rev-parse", base).strip()
    head_sha = run_git(root, "rev-parse", head).strip()
    all_names = list(range_paths(root, base, head))
    # `names` is what gets SCANNED (content excluded); `all_names` is every
    # changed path, so a checker can see a companion tree changed even when its
    # content is excluded from context.
    names = [n for n in all_names if not any(glob_match(g, n) for g in excludes)]
    added: dict[str, tuple[tuple[int, str], ...]] = {}
    removed: dict[str, tuple[str, ...]] = {}
    for name in names:
        # --literal-pathspecs: the name is a file, never pathspec magic. A
        # directory named `:` gives `:/leak.py`, which as magic reads the
        # top-level `leak.py` instead.
        raw = run_git(root, "--literal-pathspecs", "diff", f"{base}...{head}",
                      "--", name)
        added[name], removed[name] = parse_lines(raw)
    # Diff against the merge-base, matching the three-dot file list above —
    # base_sha itself may be on a diverged branch.
    merge_base = run_git(root, "merge-base", base, head).strip()
    return DiffContext(base=base_sha, head=head_sha, files=tuple(names),
                       added=added, removed=removed,
                       read_base=_read_at(root, merge_base),
                       read_head=_read_at(root, head_sha),
                       all_files=tuple(all_names),
                       scope_files=changed_files(root, base, head))


def review_package(root: Path, base: str, head: str = "HEAD") -> str:
    """One deterministic file for BASE..HEAD: resolved shas, commit log,
    diff --stat, and a -U10 diff.

    A review subagent reads this from disk instead of the controller holding
    the whole diff in its context (modelled on superpowers'
    review-package). Byte-deterministic for a fixed range — no timestamps,
    no environment — so two invocations agree and the artifact can be
    compared or cached. The resolved shas are IN the package: a reviewer
    must know exactly what range it judged, and attest binds to shas.
    """
    base_sha = run_git(root, "rev-parse", base).strip()
    head_sha = run_git(root, "rev-parse", head).strip()
    # Determinism must hold under the USER'S gitconfig, not only a clean one:
    # color.ui=always injects
    # ANSI escapes, diff.external replaces hunks with per-invocation temp
    # paths, diff.noprefix drops the a/ b/ prefixes, and core.abbrev (plus
    # object-count auto-scaling) moves --oneline hash lengths. Pin all of it:
    # full hashes in the log, no color, no external diff, explicit prefixes.
    pin = ("-c", "color.ui=false", "-c", "diff.noprefix=false",
           "-c", "diff.mnemonicPrefix=false")
    log = run_git(root, *pin, "log", "--no-decorate", "--format=%H %s",
                  f"{base_sha}..{head_sha}")
    stat = run_git(root, *pin, "diff", "--relative", "--stat", "--no-color",
                   f"{base_sha}...{head_sha}")
    diff = run_git(root, *pin, "diff", "--relative", "--no-ext-diff", "--no-color",
                   "-U10", f"{base_sha}...{head_sha}")
    gate = _root_gate(root)
    if gate:
        # Below the git root, the enrollment's own gate workflow too, named
        # from the top of the tree.
        stat += run_git(root, *pin, "diff", "--stat", "--no-color",
                        f"{base_sha}...{head_sha}", "--", gate[0])
        diff += run_git(root, *pin, "diff", "--no-ext-diff", "--no-color", "-U10",
                        f"{base_sha}...{head_sha}", "--", gate[0])
    return (f"# review package\n"
            f"base: {base_sha}\n"
            f"head: {head_sha}\n\n"
            f"## commits ({base}..{head})\n\n{log}\n"
            f"## stat\n\n{stat}\n"
            f"## diff (-U10)\n\n{diff}")
