"""Boundary records: a unit writes where it is, so nothing has to go look.

Without it, an orchestrator's only window into a builder is its FINAL report,
so "stalled" and "working" look identical and every status question is
answered by inspecting worktrees off disk — `git log`, `ls
.warden/out/rounds`, `find -newermt`, and `ps` to see whether a suite is
actually running. A unit that stalls and DIES with commits local and unpushed
looks, from outside, exactly like an agent still working.

So a unit appends one line per protocol boundary it passes, and the boundary
carries the HEAD it applies to and a timestamp. Two properties make this worth
the line it costs, and they are the design rather than decoration:

- **The AGE of the last boundary is the signal.** Not the boundary — a unit
  three hours past `round-attested` and a unit ten minutes past it look the
  same in every other artifact on disk, and the difference between them is the
  only question anyone was asking.
- **It survives the unit.** The record is a file, written as the boundary is
  passed, so a process that dies mid-round still leaves its last boundary.
  Nothing here asks the unit to cooperate beyond the call that already
  happened.

The log lives under `.warden/out/`, the evidence run root. THIS repo and the
shipped `examples/hello-svc` fixture gitignore that root, `docs/wiki/Adopting.md`
tells an enrolling repo to, and `warden certify` REFUSES an enrolled repo that
tracks it: rung R-13 is a `not_tracked` check on `.warden/out/`, beside R-08
(`.warden/memory/findings.jsonl`), R-11 (`.warden/memory/gate/`) and R-10
(`.beads/interactions.jsonl`). The rung matters more for this writer than for
any other under the root: every other writer mints a uniquely-named run dir,
and this one appends to a FIXED path on every branch, which is the conflict
shape R-08 exists for.
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import runs

LOG_NAME = "progress.jsonl"

# The protocol boundaries a unit can report, and the ONLY names `record`
# accepts. A closed vocabulary because the table is read across units: a
# free-text boundary would let two units describe the same step two ways and
# the column would stop being comparable, which is most of what made the
# hand-reconstruction expensive in the first place.
BOUNDARIES = (
    "round-minted",        # warden round new
    "round-attested",      # warden attest write
    "repair-committed",    # a repair round's commit landed
    "verify-run",          # warden verify
    "parity-run",          # warden review --base ... --no-comment exit 0
    "pushed",              # the branch reached the remote
    "pr-opened",           # the PR exists
)

_MAX_DETAIL = 200
# Read bound for the log. A boundary line is ~150 bytes, so this is thousands
# of them — and a bound means a log that has become something else (a stray
# redirect, a binary paste) cannot be read into memory unboundedly by a status
# command whose whole purpose is to be cheap.
_MAX_LOG_BYTES = 2_000_000


class ProgressError(Exception):
    """A boundary could not be recorded, or a unit's log could not be read.

    Never a warning on the READ side: `progress show` exists to answer "where
    is this unit", and a log it cannot read must say so rather than render the
    unit as one that reached no boundary. Those are different states and the
    whole value of the table is that it distinguishes states that look alike.
    """


def log_path(root: Path) -> Path:
    return root / ".warden" / runs.OUT_DIRNAME / LOG_NAME


def record(root: Path, boundary: str, *, head: str | None = None,
           detail: str = "") -> Path:
    """Append one boundary to `root`'s log and return the log path.

    `head` defaults to the commit the unit is on, via the same `runs.head_sha`
    every other artifact stamps — including its `UNKNOWN_SHA` answer, which is
    recorded as-is rather than omitted: "could not determine" must not be
    written the same way as "not applicable" (the tri-state house rule).
    """
    if boundary not in BOUNDARIES:
        raise ProgressError(
            f"unknown boundary {boundary!r} — the vocabulary is "
            f"{', '.join(BOUNDARIES)}. A free-text boundary would not be "
            "comparable across units, which is the whole point of the table")
    entry = {
        "boundary": boundary,
        "head": head or runs.head_sha(root),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "detail": str(detail)[:_MAX_DETAIL],
    }
    path = log_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError as e:
        raise ProgressError(f"{path} could not be appended to ({e}), so this "
                            "unit's progress is unreadable from outside") from e
    return path


def record_quietly(root: Path, boundary: str, *, head: str | None = None,
                   detail: str = "") -> Path | None:
    """`record`, for call sites where the boundary is TELEMETRY beside a
    verdict the command already reached.

    `warden verify`, `round new` and `attest write` each pass a boundary as
    they finish. None of them may fail because the progress log could not be
    appended to: the command's own answer is the gate, and turning a full
    attestation into exit 2 over a status line would be the tail wagging the
    dog. It is never SILENT, though — the failure prints, for the reason every
    other advisory in this codebase is also recorded rather than swallowed: the
    unattended cage has no terminal, and a signal that exists only in
    scrollback cannot be audited afterwards.
    """
    try:
        return record(root, boundary, head=head, detail=detail)
    except ProgressError as e:
        # Not a bare pass: the reason is printed, and the caller's verdict is
        # deliberately left alone. STDERR, never stdout — `warden round new`
        # calls this, and its stdout is a command-substitution surface where a
        # stray word becomes part of someone's directory name.
        print(f"warden: progress boundary {boundary!r} NOT recorded: {e}",
              file=sys.stderr)
        return None


@dataclass(frozen=True)
class Unit:
    """One checkout under the scanned root, and the last thing it said."""
    name: str
    path: Path
    branch: str
    head: str
    boundary: dict | None
    error: str = ""

    @property
    def moved(self) -> bool:
        """The unit's current head is past the head its last boundary names.

        The signature of a unit that finished and never shipped: work
        committed AFTER the last boundary they reported. Visible in the table
        rather than reconstructed from `git log`.

        UNKNOWN on EITHER side answers False. `record` stores
        `runs.head_sha`'s `UNKNOWN_SHA` as-is when git could not answer, and
        `warden verify` passes exactly that value through, so a recorded
        "unknown" is reachable from a shipped call site — and two shas that
        are not equal because one of them is not a sha is not evidence that
        work happened. Guarding only the unit side would leave the
        table asserting "work committed and no boundary since" about a unit
        whose boundary never knew which commit it applied to.
        """
        if not self.boundary:
            return False
        recorded = str(self.boundary.get("head") or "")
        if runs.UNKNOWN_SHA in (recorded, self.head):
            return False
        return bool(recorded and self.head and recorded != self.head)


def _git(path: Path, *args: str) -> str:
    """git in `path`, or "" — a unit whose git cannot answer still has a
    boundary worth rendering, and that is the case this command exists for."""
    try:
        proc = subprocess.run(["git", *args], cwd=path, capture_output=True,
                              text=True, env=runs.git_env())
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def checkout_state(path: Path) -> bool | None:
    """True a git checkout, False not one, None could not be examined.

    Checkout-ness, not log-presence, is what makes a directory a unit. A unit
    that died before its first boundary has no log at all, and defining units
    by their logs would make exactly that unit invisible.

    TRI-STATE, and `os.stat` rather than `Path.exists()`. A unit
    directory whose permissions changed — by whatever killed the unit, by a
    half-torn-down sandbox — is exactly the unit this command exists to report,
    and `Path.exists()` swallows the EACCES itself and answers False, so no
    `except OSError` around it can ever fire: the unit would vanish from the table
    with no signal at all, which is the failure this feature is about, produced
    by the feature. `os.stat` lets the EACCES through, and None travels to the
    table as UNREADABLE.
    """
    try:
        os.stat(path / ".git")
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return None
    return True


def units(root: Path) -> list[Path]:
    """Every unit under `root`: its immediate subdirectory checkouts, plus
    `root` itself when it is one.

    Immediate children only. Worktrees are laid out one directory per unit
    under a single root (`.claude/worktrees/<unit>`), and a recursive walk
    would descend into each unit's own nested worktrees and report a tree of
    stale copies as live units — the failure `tests/conftest.py::tracked`
    records from the other side.
    """
    found = []
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError as e:
        raise ProgressError(f"{root} could not be listed ({e}), so no unit "
                            "under it can be reported") from e
    # `is not False`: a directory warden could not EXAMINE is included and
    # reported, never silently dropped. Dropping it is indistinguishable from
    # the unit never existing, and this table exists to tell those apart.
    found.extend(p for p in children if checkout_state(p) is not False)
    if checkout_state(root) is not False:
        found.append(root)
    return found


WORKTREE_ROOT = Path(".claude") / "worktrees"


def default_root() -> Path:
    """Where `progress show` looks when nobody said: the enclosing repo's
    worktree root if it has one, else the repo itself, else the cwd.

    A repo with no `.claude/worktrees/` is its own single unit — `progress show`
    there reports one row rather than an empty table, which is the honest
    answer for a builder working in the checkout directly.
    """
    # Local import to keep module deps one-way: config knows nothing about
    # progress, and a status command must not be the thing that makes the
    # config loader reachable from a new direction.
    from . import config as config_mod
    try:
        root = config_mod.find_repo_root()
    except config_mod.ConfigError:
        return Path.cwd()
    worktrees = root / WORKTREE_ROOT
    return worktrees if worktrees.is_dir() else root


def last_boundary(unit: Path) -> dict | None:
    """The last readable boundary in `unit`'s log, or None if it has none.

    Scans from the END and stops at the first well-formed entry: a truncated
    tail line — a unit killed mid-write, which is precisely the unit this is
    for — must not hide the boundary before it.

    None means the log is ABSENT. A log that exists, carries content, and
    yields no boundary at all RAISES instead: returning None
    there would render a corrupt log — a stray redirect, a binary paste, a
    partially overwritten file — identically to a unit that has recorded
    nothing, which is the state this module's own `ProgressError` docstring
    forbids producing. The oversized arm above already treats "this is not a
    boundary log any more" as a refusal; this is the same judgement one line
    lower, where the file is small enough to read and still is not one.
    """
    path = log_path(unit)
    try:
        if path.stat().st_size > _MAX_LOG_BYTES:
            raise ProgressError(
                f"{path} is {path.stat().st_size} bytes, past the "
                f"{_MAX_LOG_BYTES}-byte read bound — this is not a boundary "
                "log any more, and reading it would not be cheap")
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ProgressError(f"{path} could not be read ({e}) — this unit's "
                            "state is unknown, which is not the same as "
                            "unreached") from e
    content = False
    for line in reversed(text.splitlines()):
        if not line.strip():
            continue
        content = True
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("boundary"):
            return entry
    if content:
        raise ProgressError(
            f"{path} has content but no boundary in it — every line is "
            "unparseable or names none, so this unit's state is unknown "
            "rather than unreached")
    return None


def read_units(root: Path, *, names: tuple[str, ...] = ()) -> list[Unit]:
    """Every unit under `root` with its branch, head and last boundary.

    `names` restricts the scan, and an unreadable log becomes the unit's
    `error` rather than an exception: one broken unit must not hide the four
    beside it, which is the same reason `audit.latest_artifact` skips a corrupt
    artifact instead of aborting its scan.
    """
    out = []
    for path in units(root):
        if names and path.name not in names:
            continue
        # `checkout_state`'s THIRD value, carried. `units()` selects with
        # `is not False`, which merges True and None at the filter — so
        # without it a directory warden could not examine would arrive here
        # indistinguishable from a real unit and render as NONE RECORDED: not
        # a missing row, a false claim that this IS a unit and has recorded
        # nothing.
        error = ""
        if checkout_state(path) is None:
            error = (f"{path / '.git'} could not be examined, so warden cannot "
                     "tell whether this is a unit at all — reported rather "
                     "than dropped, because a dropped row reads as a "
                     "directory that was never there")
        try:
            boundary = last_boundary(path)
        except ProgressError as e:
            boundary = None
            error = f"{error}; {e}" if error else str(e)
        out.append(Unit(name=path.name, path=path,
                        branch=_git(path, "rev-parse", "--abbrev-ref", "HEAD"),
                        head=_git(path, "rev-parse", "HEAD"),
                        boundary=boundary, error=error))
    return out


def age_seconds(entry: dict | None, *, now: datetime | None = None) -> float | None:
    """Seconds since the boundary was recorded, or None when it cannot be
    computed — a timestamp that will not parse is unknown, never zero."""
    if not entry:
        return None
    try:
        when = datetime.fromisoformat(str(entry.get("ts") or ""))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - when).total_seconds()


def render_age(seconds: float | None) -> str:
    """An age a reader can compare at a glance. `unknown` is never rendered as
    a number, and a negative age (clock skew) says so rather than reading as
    fresh."""
    if seconds is None:
        return "unknown"
    total = int(seconds)
    if total < 0:
        return "clock skew"
    if total < 90:
        return f"{total}s"
    minutes = total // 60
    if minutes < 90:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h {minutes % 60:02d}m"
    return f"{hours // 24}d {hours % 24:02d}h"


def _sort_key(unit: Unit) -> tuple:
    """Unknown first, then stalest first. The stale end of the list is the end
    anyone reading this table is looking for."""
    age = age_seconds(unit.boundary)
    if age is None:
        return (0, 0.0, unit.name)
    return (1, -age, unit.name)


def table(root: Path, *, now: datetime | None = None,
          names: tuple[str, ...] = ()) -> str:
    """The whole worktree root as one table: unit, branch, the unit's head, the
    head its last boundary applies to, that boundary, and its AGE.

    `head` and `at` are two columns because they are two facts. One column
    carrying whichever of them happened to be available would, in the STARRED
    row — the one case this feature exists for — show the reader the commit
    the unit had moved PAST, under a label saying `head`, while the unit's
    actual head appeared nowhere on the page.
    """
    found = sorted(read_units(root, names=names), key=_sort_key)
    header = ("unit", "branch", "head", "at", "last boundary", "age")
    rows = []
    moved = False
    for unit in found:
        entry = unit.boundary or {}
        boundary = str(entry.get("boundary") or "")
        if not boundary:
            boundary = "NONE RECORDED" if not unit.error else "UNREADABLE"
        at = str(entry.get("head") or "")[:12] or "—"
        if unit.moved:
            at = f"{at} *"
            moved = True
        rows.append((unit.name, unit.branch or "?", unit.head[:12] or "?", at,
                     boundary,
                     render_age(age_seconds(unit.boundary, now=now))))
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(header)]
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(header)).rstrip(),
             "  ".join("-" * w for w in widths)]
    for row in rows:
        lines.append("  ".join(c.ljust(widths[i])
                               for i, c in enumerate(row)).rstrip())
    if not rows:
        lines.append(f"(no git checkout under {root})")
    lines.append("")
    lines.append(f"{len(rows)} unit(s) under {root} · "
                 "age is time since the last boundary: that is what tells "
                 "stalled from working")
    if moved:
        lines.append("* `at` is behind `head`: the unit committed work and "
                     "recorded no boundary since")
    for unit in found:
        if unit.error:
            lines.append(f"! {unit.name}: {unit.error}")
    return "\n".join(lines) + "\n"
