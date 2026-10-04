"""Evidence run directories: .warden/out/<UTC-ts>-<cmd>/ with a manifest.json.

Every subcommand writes one; the manifest binds command, argv, git SHAs, and
rules_version so the artifact chain is auditable rather than merely logged.
"""

import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

OUT_DIRNAME = "out"
# Variables a git hook (or an in-progress rebase/commit) exports to bind git to
# ITS repo. A subprocess honors them over cwd, so warden invoked from a hook
# would stamp the ambient repo onto a run for a different root. Provenance must
# describe the root it was handed.
_GIT_ENV_OVERRIDES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                      "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                      "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX",
                      "GIT_QUARANTINE_PATH", "GIT_NAMESPACE")


def git_env() -> dict:
    """os.environ minus the repo-binding git variables."""
    return {k: v for k, v in os.environ.items() if k not in _GIT_ENV_OVERRIDES}
# Provenance we could not establish. Never a guess, and never pairable:
# artifacts stamped with it cannot prove which commit they judged.
UNKNOWN_SHA = "unknown"


def head_sha(root: Path) -> str:
    """The commit a run is judging. Public because artifacts other than the
    manifest must stamp it too."""
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                              capture_output=True, text=True,
                              env=git_env()).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return UNKNOWN_SHA


def is_dirty(root: Path) -> bool | None:
    """Whether the working tree carried uncommitted content. None when git
    cannot answer — absent is "not recorded", never "clean"."""
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=root, check=True,
                             capture_output=True, text=True, env=git_env()).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return bool(out.strip())


def _session_stamp() -> dict:
    """Who ran this — session and model, when the environment says so.

    Completes the org-chart story: graph_node answers WHICH node executed,
    this answers WHO. Absent env means absent keys, never guessed values.
    """
    import os
    out = {}
    for key, env in (("session_id", "WARDEN_SESSION_ID"),
                     ("agent_model", "WARDEN_AGENT_MODEL")):
        value = os.environ.get(env, "").strip()
        if value:
            out[key] = value[:200]
    return out


def create_run_dir(root: Path, cmd: str) -> Path:
    # Microsecond precision + exist_ok=False: two runs must never share an
    # evidence dir or silently clobber each other's manifest.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = root / ".warden" / OUT_DIRNAME / f"{ts}-{cmd}"
    run_dir.mkdir(parents=True, exist_ok=False)
    # convenience pointer at the newest run; never load-bearing
    latest = run_dir.parent / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(run_dir.name)
    # Every reader falls back to sorting the
    # run dirs, so a failed write of this pointer loses nothing.
    # warden:allow(except-pass): the latest symlink is only a convenience pointer, never load-bearing for any reader
    except OSError:
        pass
    return run_dir


def write_manifest(run_dir: Path, *, root: Path, cmd: str, rules_version: str,
                   exit_status: str, extra: dict | None = None) -> Path:
    from . import __version__
    from . import graph as graph_mod
    manifest = {
        "cmd": cmd,
        "platform_version": __version__,
        "argv": sys.argv[1:],
        "git_sha": head_sha(root),
        "rules_version": rules_version,
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exit_status": exit_status,
        **_session_stamp(),
        "files": sorted(p.name for p in run_dir.iterdir() if p.name != "manifest.json"),
        **(extra or {}),
    }
    # Org-chart telemetry: stamp which declared graph node this run executed.
    # Absent/invalid graph -> no stamp; the graph never blocks the tools.
    node = graph_mod.node_for_cmd(root, cmd)
    if node:
        manifest["graph_node"] = node
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


ROUNDS_DIRNAME = "rounds"
_SLUG_OK = "abcdefghijklmnopqrstuvwxyz0123456789"


class RoundError(Exception):
    """A review round could not be minted. Never a warning: a round that
    cannot be isolated must stop the review, not start one on a shared path."""


def _slug(text: str, *, limit: int = 40) -> str:
    """A filesystem-safe, identity-carrying fragment of a branch name."""
    out = "".join(c if c in _SLUG_OK else "-" for c in text.lower())
    return out.strip("-")[:limit] or "detached"


def create_round_dir(root: Path, *, branch: str, head: str,
                     now=None, pid=None) -> Path:
    """Mint an ISOLATED directory for one review round, or refuse loudly.

    The sibling of `create_run_dir`, and here for a sharper reason. A round
    directory placed by PROSE (a skill telling the agent to run `mktemp -d`)
    has no `exist_ok=False`: two builders can write a fixed name under a
    shared root, or a foreign branch's review package can land in a builder's
    round with a MATCHING base sha, where it reads as plausible on every axis
    anything checks.

    So the path is not the agent's to choose. The name is DERIVED — UTC
    microsecond, branch, head prefix, pid — which does three things: two
    processes cannot collide (the pid differs), the directory says at a glance
    which branch and commit it belongs to, and a stray artifact from elsewhere
    is visibly foreign rather than plausibly local.

    `exist_ok=False`, and NO retry on a collision. That is the deliberate half:
    the one shape left — two rounds deriving one name inside a single
    microsecond of one process — is refused with a named error rather than
    quietly renamed. A collision is a STOP, not a silent read.

    The emptiness assertion below is belt-and-braces on the same claim, and is
    described as that rather than as the guard: `mkdir(exist_ok=False)` is what
    makes the directory new. The assertion is what makes "new" OBSERVABLE if
    some future path ever creates the directory another way — the one thing
    that must never happen silently is a round starting on someone else's
    files.
    """
    when = now or datetime.now(timezone.utc)
    ts = when.strftime("%Y%m%dT%H%M%S%fZ")
    proc = os.getpid() if pid is None else pid
    name = f"{ts}-{_slug(branch)}-{head[:12]}-{proc}"
    rounds_root = root / ".warden" / OUT_DIRNAME / ROUNDS_DIRNAME
    if rounds_root.exists() and not rounds_root.is_dir():
        raise RoundError(
            f"{rounds_root} exists and is not a directory, so no round can be "
            "isolated under it — refusing rather than writing a review round "
            "somewhere a later reader will not look for it")
    round_dir = rounds_root / name
    try:
        round_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as e:
        raise RoundError(
            f"{round_dir} already exists — another review round is using it. "
            "A round directory is minted once and never reused: reading a "
            "directory another round wrote is exactly how a foreign review "
            "package with a matching base sha gets attested. "
            "Re-run to mint a fresh one") from e
    except OSError as e:
        raise RoundError(f"{round_dir} could not be created ({e}), so this "
                         "round has nowhere isolated to write") from e
    leftovers = sorted(p.name for p in round_dir.iterdir())
    if leftovers:
        raise RoundError(
            f"{round_dir} was created but is not empty ({', '.join(leftovers)})"
            " — a round starts on an empty directory or it does not start")
    (round_dir / "reviewers").mkdir()
    return round_dir


def write_round_manifest(round_dir: Path, *, base: str, base_sha: str,
                         head: str, branch: str, round_no: int) -> Path:
    """The round's identity, on disk, for anything that later reads its output.

    `round_no` is WHICH round this is — 1 for the first review of a
    base..head, 2 for the next — and it is required rather than defaulted
    because it is a fact only the minter can establish (`repair.round_count`
    derives it from the shards COMMITTED in the range and the same chain
    `round classify` proves, taking the higher of the two and reporting any
    disagreement) and a default would silently mint every round as the same
    one.
    It is warden's own record of the number, which is what makes
    `attest write --review-dir` able to check a roster's `round` labels
    against something: a label is text the builder writes, and before this
    field nothing in the platform compared it with anything, so a first and
    only review could label itself round 2 and be checked against round 2's
    (shorter) declared crew.

    `attest write --review-dir` binds against `head_sha` here. That binding is
    an IDENTITY comparison, not the overlap heuristic `range_binding` applies:
    a foreign package can share a base sha with the round it lands in and
    differ only in head, and `range_binding` is silent whenever the two
    branches touch any file in common — the common case for two builders in
    one repo, and so exactly the case that needs catching.
    """
    if isinstance(round_no, bool) or not isinstance(round_no, int) \
            or round_no < 1:
        raise RoundError(
            f"a round is numbered from 1 and {round_no!r} is not such a "
            "number — a manifest stating a round nobody can read is worse "
            "than one that never stated a round at all, because every reader "
            "of it must then guess")
    doc = {"schema": 1, "round_id": round_dir.name, "base": base,
           "base_sha": base_sha, "head_sha": head, "branch": branch,
           "round": round_no,
           "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    path = round_dir / "round.json"
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return path


def round_manifest_for(review_dir: Path) -> dict | None:
    """The round manifest governing `review_dir`, or None if warden did not
    mint it.

    None means UNMINTED — a caller-supplied directory warden has no identity
    for — and every caller must RECORD that rather than treat it as a pass.
    An unreadable or malformed manifest is NOT None: it raises, because a
    round that cannot state which head it judged is worse than one that never
    claimed to, and "could not look" must not read as "nothing to see".
    """
    for candidate in (review_dir / "round.json",
                      review_dir.parent / "round.json"):
        # NOT `candidate.is_file()`. That swallows OSError and answers False,
        # so a round.json warden cannot LOOK at — a dangling symlink, a symlink
        # loop, a directory — would read as one that is not there and downgrade
        # to `unminted` at exit 0, which is the exact inversion this function's
        # docstring forbids.
        try:
            st = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as e:
            raise RoundError(
                f"{candidate} could not be examined ({e}), so warden cannot "
                "tell whether this round has an identity — refused rather "
                "than read as a round that never claimed one") from e
        if stat.S_ISLNK(st.st_mode):
            raise RoundError(
                f"{candidate} is a symlink. A round's identity is evidence, "
                "and evidence is a regular file: the same discipline "
                "`--review-dir` applies to reviewer reports")
        if not stat.S_ISREG(st.st_mode):
            raise RoundError(
                f"{candidate} exists but is not a regular file, so it cannot "
                "be the round manifest it occupies the name of")
        try:
            doc = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise RoundError(
                f"{candidate} could not be read as a round manifest ({e}) — "
                "the round cannot say which commit it judged, and an "
                "unreadable identity must not read as an absent one") from e
        if not isinstance(doc, dict) or not doc.get("head_sha"):
            raise RoundError(
                f"{candidate} names no head_sha, so the round it describes "
                "cannot be bound to a commit")
        return doc
    return None
