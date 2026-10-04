"""Where an enrollment's gate workflows are.

GitHub runs workflows only from `.github/workflows/` at the git root. `warden
init` writes a root enrollment's gate there as `warden.yml`, and the gate of an
enrollment below the root, say `svc/api`, in the same directory as
`warden-svc-api.yml`, whose top-level `name:` is `warden (svc/api)`.

The checks that read an enrollment's workflows (certify's G-04, V-02 and the
`ci_step_enforced` rungs, and declare check's D-03) read only workflows GitHub
runs, so only files in the git root's `.github/workflows/`.

At the git root they read that directory, less the workflows that belong to an
enrollment below the root. A root workflow is skipped when either holds:

- it is `warden-<name>.yml`, and `<name>` is the path, with each `/` as `-`, of
  a `repo.yaml` below the root that git TRACKS (`svc/api/repo.yaml` names
  `warden-svc-api.yml`), or of one that is only ON DISK — git-ignored or not —
  whose gate this workflow says it is, its top-level `name:` being
  `warden (svc/api)`. On disk counts because right after `warden init` in
  `svc/api` that file is not committed yet; the `name:` is read with it
  because a file name cannot say whose gate a file is, and on shape alone a
  root gate named plainly `warden` is skipped as some subdirectory's. The
  disk is asked for each reading of the `-`s in `<name>` as `-` or `/`, for a
  name with at most `MAX_DASHES` of them; a longer name is looked up in git's
  index only.
- every one of its jobs runs below the git root: each job's effective
  `working-directory` (its `defaults.run`, else the workflow's) is a relative
  path of plain segments that is not the root. That is the gate of an
  enrollment whose `repo.yaml` a pull request deleted, which neither the index
  nor the disk still names.

Every other root workflow is credited: `warden.yml`, one the user named
`warden-ci.yml`, or a `warden-<name>.yml` no enrollment is named for whose jobs
run at the root. When git cannot list the tracked files, every `warden-*.yml`
but `warden.yml` is skipped. `skipped_gates` names what was skipped and why, so
G-04 can say why it found no gate. `disowned_gates` names the skips whose own
`name:` is not the one init writes for the enrollment the skip gave them to —
the file name or the jobs' directory said whose gate the file was, and the file
did not agree — so D-03 can refuse where it would read the root as having none.

Below the root they read one file: the root workflow named for the enrollment,
and only when its top-level `name:` is `warden (<path>)` for that enrollment's
path. The file name alone cannot tell `svc/api` from `svc-api`, which share
`warden-svc-api.yml`; the `name:` init writes carries the path with its `/`.
A gate whose `name:` was edited is not credited, and G-04 says so. Nothing in
the enrollment directory's own `.github/workflows/` is read: GitHub never runs
a nested workflow.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import yaml

from . import runs
from . import yamlio

WORKFLOWS_DIR = Path(".github") / "workflows"
# The most `-`s a `warden-<name>.yml` may hold for the disk to be asked about
# each reading of them: 2**12 lookups.
MAX_DASHES = 12
_NAMED = re.compile(r"warden \((?P<prefix>[^()]+)\)")
_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


class GitLocationError(Exception):
    """git could not say where the directory sits; the message is git's."""


def git_location(root: Path) -> tuple[Path, str]:
    """The git root and the path from it to ROOT, `""` at the root itself and
    `svc/api` below it, with no trailing slash."""
    try:
        proc = subprocess.run(["git", "rev-parse", "--show-toplevel", "--show-prefix"],
                              cwd=root, capture_output=True, text=True, env=runs.git_env())
    except OSError as e:
        raise GitLocationError(f"could not run git ({e})") from e
    lines = proc.stdout.split("\n")
    if proc.returncode != 0 or len(lines) < 2 or not lines[0]:
        raise GitLocationError(proc.stderr.strip()
                               or f"git rev-parse exited {proc.returncode}")
    return Path(lines[0]), lines[1].rstrip("/")


def workflow_name(prefix: str) -> str:
    """The file name init gives the gate of the enrollment at PREFIX."""
    return "warden.yml" if not prefix else f"warden-{prefix.replace('/', '-')}.yml"


def root_gate_path(prefix: str) -> str:
    """The root gate of the enrollment at PREFIX as a path relative to that
    enrollment, `../../.github/workflows/warden-svc-api.yml` for `svc/api`:
    the one path outside an enrollment its review lists."""
    up = "../" * len(prefix.split("/"))
    return f"{up}{WORKFLOWS_DIR.as_posix()}/{workflow_name(prefix)}"


def is_own_root_gate_path(root: Path, path: str) -> bool:
    """Whether PATH, as the review of the enrollment at ROOT lists it, is that
    enrollment's OWN gate at the git root: `root_gate_path` of its prefix, and
    no other file. Compared against that one path rather than matched against
    a second pattern of the same shape, which disagreed with it both ways — a
    pattern of file-name characters excludes a prefix holding anything else
    (`café/api`), and matching the shape alone accepts any number of `../` and
    any `warden-*.yml`, including another enrollment's gate. False at the git
    root, and where git cannot place ROOT. Only a path that leaves the
    enrollment can be it, so git is asked about no other."""
    if not path.startswith("../"):
        return False
    try:
        _, prefix = git_location(root)
    except GitLocationError:
        return False
    return bool(prefix) and path == root_gate_path(prefix)


def is_subdirectory_gate(name: str) -> bool:
    """Whether NAME has the shape `workflow_name` gives an enrollment below
    the root, whichever enrollment that is."""
    return name.startswith("warden-") and name != workflow_name("")


def subdirectory_gates(top: Path) -> dict[str, str] | None:
    """The gate file name init gives each enrollment below the git root TOP,
    mapped to its path: one per `repo.yaml` git tracks below the root. None
    when git cannot list them."""
    try:
        proc = subprocess.run(["git", "ls-files", "-z", "--", "*/repo.yaml"], cwd=top,
                              capture_output=True, env=runs.git_env())
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    gates: dict[str, str] = {}
    for raw in proc.stdout.split(b"\0"):
        if raw:
            prefix = os.fsdecode(raw).rpartition("/")[0]
            gates[workflow_name(prefix)] = prefix
    return gates


def _plain_prefix(prefix: str) -> bool:
    """Whether PREFIX is spelled as a path below the git root: not absolute,
    and no segment empty, `.` or `..`. Spelling alone does not make it the
    path of a directory — a plain segment can be a symlink — so `_repo_yaml_at`
    asks where the path resolves as well."""
    return all(_SEGMENT.fullmatch(p) and p not in (".", "..") for p in prefix.split("/"))


def _repo_yaml_at(top: Path, prefix: str) -> bool:
    """Whether a `repo.yaml` is at PREFIX below the git root TOP, with PREFIX a
    plain path none of whose segments is a symlink: it resolves to itself below
    TOP. Resolving merely somewhere inside TOP is not enough — `x -> .` reaches
    the root's own `repo.yaml` and `alias -> svc/api` another enrollment's, and
    warden run from either is told git's physical prefix, never `x` or `alias`.
    Nor is resolving to itself: resolving never folds case, so on a
    case-insensitive filesystem `SVC/api` resolves to itself beside `svc/api`,
    and git's prefix is `svc/api` — every segment must be spelled as its parent
    directory stores it. A path that cannot be resolved or listed is not one."""
    if not (_plain_prefix(prefix) and os.path.lexists(top / prefix / "repo.yaml")):
        return False
    try:
        parent = top
        for segment in prefix.split("/"):
            if segment not in os.listdir(parent):
                return False
            parent = parent / segment
        return (top / prefix).resolve() == top.resolve() / prefix
    except (OSError, RuntimeError):
        return False


def on_disk_enrollment(top: Path, name: str) -> str | None:
    """The path below the git root TOP of a `repo.yaml` on disk whose gate
    `workflow_name` calls NAME, trying each reading of the `-`s in NAME as `-`
    or `/`. None when there is none, or NAME holds more than `MAX_DASHES`."""
    if not (is_subdirectory_gate(name) and name.endswith(".yml")):
        return None
    words = name[len("warden-"):-len(".yml")].split("-")
    if len(words) - 1 > MAX_DASHES:
        return None
    for mask in range(2 ** (len(words) - 1)):
        prefix = words[0] + "".join(("/" if mask >> i & 1 else "-") + word
                                    for i, word in enumerate(words[1:]))
        if _repo_yaml_at(top, prefix):
            return prefix
    return None


def _load(path: Path) -> object:
    try:
        return yamlio.load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


def named_enrollment(path: Path) -> str | None:
    """The enrollment the gate workflow at PATH names in its top-level `name:`,
    as init writes it: `""` for `warden`, `svc/api` for `warden (svc/api)`.
    None when the file cannot be read or its `name:` is neither."""
    doc = _load(path)
    name = doc.get("name") if isinstance(doc, dict) else None
    if name == "warden":
        return ""
    match = _NAMED.fullmatch(name) if isinstance(name, str) else None
    return match.group("prefix") if match else None


def _below_root(directory: object) -> str | None:
    """DIRECTORY as a path of plain segments below the git root, or None for
    the root itself, no directory, an absolute path, `..`, or an expression."""
    if not isinstance(directory, str) or directory.strip().startswith("/"):
        return None
    parts = [p for p in directory.strip().split("/") if p not in ("", ".")]
    if not parts or not all(_SEGMENT.fullmatch(p) and p != ".." for p in parts):
        return None
    return "/".join(parts)


def jobs_below_root(path: Path) -> list[str] | None:
    """The directory below the git root each job of the workflow at PATH runs
    in, when every job runs below it; None when a job runs anywhere else, the
    workflow has no job, or it cannot be read."""
    doc = _load(path)
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict) or not jobs:
        return None
    found: list[str] = []
    for job in jobs.values():
        if not isinstance(job, dict):
            return None
        directory = None
        for holder in (job, doc):
            defaults = holder.get("defaults")
            run = defaults.get("run") if isinstance(defaults, dict) else None
            if isinstance(run, dict) and "working-directory" in run:
                directory = run["working-directory"]
                break
        below = _below_root(directory)
        if below is None:
            return None
        found.append(below)
    return found


def _names_an_enrollment_for(top: Path, path: Path, owner: str | None) -> bool:
    """Whether OWNER, the enrollment PATH's `name:` names, is one whose gate
    `workflow_name` calls PATH and whose `repo.yaml` is at that path. The index
    keeps one path per gate file name, so `svc-api`'s gate reads as `svc/api`'s
    there when both are tracked. An OWNER outside the root — `../x`, absolute,
    or a symlink out of the checkout — names no enrollment of this
    repository, and neither does one through a symlink back into it."""
    return bool(owner) and workflow_name(owner) == path.name and _repo_yaml_at(top, owner)


def _root_skip(top: Path, path: Path,
               gates: dict[str, str] | None) -> tuple[str, bool] | None:
    """Why the root enrollment does not credit the root workflow at PATH, and
    whether the file DISOWNS that reason: its own `name:` does not name the
    enrollment the skip gives it to, or one below the directory its jobs run
    in. None when the root credits it."""
    if gates is None and is_subdirectory_gate(path.name):
        return ("as the gate of an enrollment below the root, since git could not "
                "list the tracked files"), \
            not _names_an_enrollment_for(top, path, named_enrollment(path))
    enrollment = (gates or {}).get(path.name)
    if enrollment:
        owner = named_enrollment(path)
        return f"as the gate of the enrollment at `{enrollment}`", \
            owner != enrollment and not _names_an_enrollment_for(top, path, owner)
    # Not in the index, so the disk is asked — and the workflow with it.
    # A gate file name is shared: `warden-svc-api.yml` is the name init
    # gives `svc/api`'s gate AND a name the root's own gate may carry, so
    # crediting it to an enrollment on the name's shape alone skips a root
    # gate that is nobody's but the root's. The `name:` init writes settles
    # it here as it settles the same question below the root, so this skip
    # never disowns.
    on_disk = on_disk_enrollment(top, path.name)
    if on_disk and named_enrollment(path) == on_disk:
        return f"as the gate of the enrollment at `{on_disk}`", False
    below = jobs_below_root(path)
    if below:
        # warden finds a `repo.yaml` walking up from where it runs, so a
        # job in `app/src` can be `app`'s.
        owner = named_enrollment(path)
        dirs = sorted(set(below))
        return ("because every job in it runs below the git root, in "
                + ", ".join(f"`{d}`" for d in dirs)), \
            not (owner and all(d == owner or d.startswith(owner + "/") for d in dirs))
    return None


_Skips = list[tuple[Path, str]]


def _select(root: Path, patterns: tuple[str, ...]) -> tuple[list[Path], _Skips, _Skips]:
    found = sorted({p for pattern in patterns for p in (root / WORKFLOWS_DIR).glob(pattern)})
    try:
        top, prefix = git_location(root)
    except GitLocationError:
        return found, [], []
    if prefix:
        named = top / WORKFLOWS_DIR / workflow_name(prefix)
        if not (named.is_file() and any(named.match(pattern) for pattern in patterns)):
            return [], [], []
        owner = named_enrollment(named)
        if owner == prefix:
            return [named], [], []
        if owner is None:
            why = (f"because its top-level `name:` is not `warden ({prefix})`, the "
                   "name `warden init` writes for this enrollment")
        else:
            why = (f"as the gate of the enrollment at `{owner}`" if owner
                   else "as the gate of the root enrollment")
        return [], [(named, why)], []
    gates = subdirectory_gates(top)
    kept: list[Path] = []
    skipped: _Skips = []
    disowned: _Skips = []
    for path in found:
        skip = _root_skip(top, path, gates)
        if skip is None:
            kept.append(path)
            continue
        why, disowns = skip
        skipped.append((path, why))
        if disowns:
            disowned.append((path, why))
    return kept, skipped, disowned


def enrollment_workflows(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    """The workflow files matching PATTERNS that belong to the enrollment at
    ROOT: at the git root its own, less another enrollment's gate; below it,
    the root workflow that names it."""
    return _select(root, patterns)[0]


def skipped_gates(root: Path, patterns: tuple[str, ...]) -> list[tuple[Path, str]]:
    """The root workflows matching PATTERNS that `enrollment_workflows` left
    out, each with why, worded to follow "was skipped"."""
    return _select(root, patterns)[1]


def disowned_gates(root: Path, patterns: tuple[str, ...]) -> list[tuple[Path, str]]:
    """The `skipped_gates` at the git root whose own top-level `name:` is not
    `warden (<path>)` for an enrollment the skip may give them to — the one
    the index names for the file name, another enrollment whose gate that
    file name is and whose `repo.yaml` is on disk, or, for a file whose jobs
    all run below the root, an enrollment every job runs in or under. So
    `warden`, an unrelated path, some other name, or none. The file name or
    the jobs' directory said whose gate the file was and the file did not
    agree, so the skip may have taken the root's own gate. Always empty below
    the root, where the one candidate is already judged by its `name:`."""
    return _select(root, patterns)[2]
