"""repo.yaml loading and validation.

repo.yaml at the repo root is the single source of truth for repo facts:
components, risk tiers, protected paths, verify commands, review settings.
This module contains no repo-specific paths or tiers, so no shadow copy of a
repo fact can drift — a test greps the package to keep it that way.
Invalid config fails closed: a wrong source of truth is worse than none.
"""

import errno
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from . import yamlio

CONFIG_NAME = "repo.yaml"
# Where a repo that declares nothing keeps its rules. The platform's own
# convention, not a repo fact — repo-specific paths stay out of this module.
DEFAULT_RULES_DIR = ".warden/rules"
_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "repo.schema.json"


class ConfigError(Exception):
    pass


class NotARegularFileError(ConfigError, OSError):
    """repo.yaml is there, but it is not a regular file, so reading it may
    never finish.

    TWO bases, because two live contracts have to keep holding at once and
    neither caller should be special-casing a class name:

    - **OSError**, because `cli._cmd_mine`'s probe splits FileNotFoundError
      ("unenrolled" — the repo genuinely has no repo.yaml) from every other
      OSError ("uncomputable:<class>" — there is one and it could not be
      read), and `rules._enforcement_surface` makes the same split. A refusal
      that were not an OSError would fall out of both and be stamped a false
      `unenrolled`, collapsing "cannot look" into "absent".
    - **ConfigError**, because `cli.main` prints a ConfigError as one sentence
      and everything else as a traceback. A named error nobody can read is
      only half the fix.
    """


def _kind(mode: int) -> str:
    """Name the st_mode class in the words the operator's own tools use, so
    the message says what `ls -l` says (error-names-cause)."""
    for predicate, name in ((stat.S_ISFIFO, "fifo"),
                            (stat.S_ISSOCK, "socket"),
                            (stat.S_ISCHR, "character device"),
                            (stat.S_ISBLK, "block device"),
                            (stat.S_ISDIR, "directory")):
        if predicate(mode):
            return name
    return "not a regular file"


def read_repo_yaml(root: Path) -> bytes:
    """Read `root/repo.yaml`, refusing anything a read could never finish on.

    ONE reader for the whole platform: `load`, `declared_rules_dir`,
    `rules._enforcement_surface` and `cli._cmd_mine`'s enrollment probe all
    read this file through here. A plain blocking read by name parks forever
    on a FIFO: no timeout, no error, and no way for a caller to tell a hang
    from slow work. Hardening any ONE call site buys nothing, because a probe
    that succeeds routes straight into `load`, which would open the same path
    the same way.

    The shape, and why each half is load-bearing:

    - ``O_NONBLOCK`` is what makes the OPEN return on a FIFO with no writer,
      which a blocking open waits on until someone writes.
    - the ``fstat`` is what makes the return a REFUSAL rather than a WRONG
      ANSWER. Measured, not assumed: on this fd a writerless FIFO reads
      ``b""`` — an EMPTY repo.yaml, which `declared_rules_dir` would then
      report as "this repo declares nothing", the permissive answer — and a
      FIFO with a writer holding it open but no byte ready raises EAGAIN
      mid-file, which is a PARTIAL repo.yaml. Both are worse than a hang,
      because both are silent. (The park itself belongs to a blocking open: a
      one-byte probe can succeed on a writer-held FIFO and the command then
      park inside a blocking read.)
    - the fstat is also the only thing that keeps ``IsADirectoryError`` alive:
      ``os.open(dir, O_RDONLY)`` SUCCEEDS on POSIX, so a non-blocking reader
      that only opened would silently lose the class `Path.read_text()` raised
      for free — and with it the "cannot look" / "absent" distinction.
    - the bytes come off the SAME fd that was classified, never a second open
      by name, so nothing can be swapped in between.

    Two things it does NOT do, said out loud rather than claimed away:

    - a path whose open or stat blocks in the KERNEL — a symlink into a
      wedged NFS or FUSE mount — still parks, and no open flag prevents that.
      O_NONBLOCK governs FIFOs and the device opens that honour it. That
      residual is ruled: warden ships no guard here YET — a
      deferral, not an impossibility. A child process doing the read, with
      a parent that deadlines, reports and os._exit()s WITHOUT reaping it,
      bounds this: measured exiting 3 after 1.00s with the child still
      blocked, because an orphaned child blocks nothing in its parent where
      exit_group must stop a D-state thread in the same process. It needs
      DEVNULL stdio and start_new_session, or the orphan holds the caller's
      stdout pipe and CI, a hook or the cage hangs regardless (exit 3 after
      1.02s under a CAPTURING caller with them, still hung at 8s without).

      The cost was measured and the guard ruled against,
      which is why this paragraph carries a price. On CPython 3.12 the in-process find_repo_root + read_repo_yaml
      pair takes 0.03ms; the cheapest child that could replace it — stdlib
      only, `-S -E`, no warden import — takes 10.75ms, 322x. A child that
      imports this module, so there is still ONE reader, takes 75.8ms, 2273x.
      `warden explain` performs four such reads (+43ms on a 0.71s command) and
      one suite run performs 1006 of them in-process alone (+10.8s on a 112s
      suite), before every warden PROCESS the suite spawns pays the 43ms again.
      Converting a hang nobody here has hit into `exit 3`, at 322x on warden's
      most load-bearing read path forever, is not a trade worth taking; an
      opt-in flag was refused separately, because a default-off branch nothing
      reaches has zero consumers. The ruling is a `warden decide` shard, and a
      test that lists `subprocess` among the names this module may not use
      makes a fold-in argue with it rather than step over it.

      Every other candidate was measured and refused on the merits.
      SIGALRM interrupted a blocking FIFO open
      in 0.51s and a daemon-thread watchdog exited 3 in 0.55s — but a FIFO
      open is an INTERRUPTIBLE wait, exactly the class the O_NONBLOCK above
      already handles, and a signal is never delivered to a thread in an
      uninterruptible one. So a FIFO-based test of such a guard goes GREEN
      over a bound that does not exist, which is the trap the ruling refuses.
      A non-daemon worker thread hung the interpreter outright; SIGKILL
      cannot reap a D-state process either; and refusing a symlink off the
      repo's own device is honestly non-blocking but misses repo.yaml sitting
      ON the wedged mount. A guard that prints a timeout while the read is
      still parked is a false negative dressed as a check. `warden decide
      show` carries the ruling; docs/wiki/Roadmap.md lists it under Stated
      limitations. Revisit if CPython gains a stat with a real deadline.
    - a SOCKET never reaches the classification at all: `os.open` on one
      fails first (EOPNOTSUPP on darwin, ENXIO on Linux), so the caller gets
      that plain OSError and not this module's sentence. Every consumer still
      fails closed on it, because all three read it as "there and unreadable"
      — but the named error belongs to FIFOs, directories and devices.
      `find_repo_root` classifies with `stat` rather than an open, so it is
      the one path that does name a socket.

    Raises FileNotFoundError (no repo.yaml, or a dangling symlink — the one
    answer that means "not enrolled"), IsADirectoryError, NotARegularFileError,
    or any other OSError the open or read produces. Returns raw bytes:
    decoding is the caller's, because the callers disagree on purpose (`load`
    wants UnicodeDecodeError to surface, `_enforcement_surface` hashes bytes
    it cannot decode).

    POSIX-only, and deliberately so rather than by accident: `os.O_NONBLOCK`
    does not exist on Windows. Nothing this platform ships claims Windows —
    CI is ubuntu-only, the cage renders launchd and shell — so the flag is
    named directly instead of being defaulted away, which would silently
    restore the blocking open it exists to remove.

    Not every repo.yaml read in the package comes through here, and three
    deliberately do not: `autonomy._committed_policy` reads it with
    `git show <rev>:repo.yaml`, `warden round classify` reads it with
    `git cat-file` at the round head, and `proportion._declared_at` lists it
    with `git ls-tree` and reads it with `git show` at each end of the range
    being classified. None touches the working tree, so none can meet this
    hazard at all.
    """
    path = root / CONFIG_NAME
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        mode = os.fstat(fd).st_mode
        if stat.S_ISDIR(mode):
            raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR),
                                    str(path))
        if not stat.S_ISREG(mode):
            raise NotARegularFileError(
                errno.EINVAL,
                f"{CONFIG_NAME} is a {_kind(mode)}, not a regular file — a "
                "read of it may never finish, so warden refuses it instead of "
                "waiting",
                str(path))
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 1 << 16)
            if not block:
                return b"".join(chunks)
            chunks.append(block)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class RiskTier:
    glob: str
    tier: str
    reason: str = ""


@dataclass(frozen=True)
class ProtectedPath:
    path: str
    reason: str


@dataclass(frozen=True)
class VerifyStep:
    run: str
    cwd: str = "."
    # The reach this step DECLARES, as globs. Empty tuple means the repo
    # declared none, and `verify.scopes_for_paths` falls back to the
    # `cwd` + `components:` inference. A tuple, not a list, because the dataclass is frozen and a
    # mutable default would make an equal step unhashable.
    covers: tuple[str, ...] = ()
    # The program the step DECLARES it runs: `pytest` or
    # None. The command text is never parsed for it.
    runner: str | None = None


def _verify_step(raw: dict) -> VerifyStep:
    """One `verify:`/`deploy:` step, with `covers:` normalized to a tuple.

    The schema has already said `covers` is a non-empty list of non-empty
    strings when present, so this only fixes the CONTAINER — `VerifyStep(**raw)`
    would otherwise store a list in a frozen dataclass. An unknown key still
    raises TypeError here rather than being dropped.
    """
    if "covers" in raw:
        raw = {**raw, "covers": tuple(raw["covers"])}
    return VerifyStep(**raw)


@dataclass(frozen=True)
class ReviewSettings:
    rules_dir: str
    blocking_severities: tuple[str, ...]
    context_excludes: tuple[str, ...]


@dataclass(frozen=True)
class DesignSettings:
    # Which risk tiers must carry a decision record (a `warden decide` ruling)
    # before Build — the machine-readable half of the design-approval
    # discipline. A DECLARATION read by the skills, not a mechanical block.
    decision_required: tuple[str, ...]
    charter: str = ""


@dataclass(frozen=True)
class Component:
    name: str
    path: str
    lang: str = ""
    description: str = ""


@dataclass(frozen=True)
class RepoConfig:
    root: Path
    repo: str
    platform_pin: str | None
    components: tuple[Component, ...]
    risk_tiers: tuple[RiskTier, ...]
    protected_paths: tuple[ProtectedPath, ...]
    verify: dict[str, tuple[VerifyStep, ...]]
    review: ReviewSettings
    # Optional lifecycle blocks. Absent -> `deploy` is empty and `design` is
    # None.
    deploy: dict[str, tuple[VerifyStep, ...]] = field(default_factory=dict)
    design: DesignSettings | None = None


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default cwd) to the directory holding repo.yaml.

    A repo.yaml that is THERE but is not a regular file stops the walk with a
    named error; it is never stepped over. `is_file()` answers False for a
    FIFO exactly as it does for an absent file, so a walk on it would continue
    past one — and both outcomes are wrong: with no ancestor repo.yaml,
    `explain` and `review` would report "no repo.yaml found from <dir>
    upward", an absence claim about a file the reader never managed to look
    at; with one, the walk would resolve to the ANCESTOR repo and warden would
    gate the inner tree against someone else's policy, silently. Failing
    closed here is this module's stated rule — a
    wrong source of truth is worse than none.

    A DANGLING SYMLINK is the boundary this must not overshoot: it resolves to
    no repo.yaml, so it is genuinely absent and the walk continues — the same
    answer `_cmd_mine` and `rules._enforcement_surface` give it.

    ONE `stat` decides both questions — is it here, and is it readable — for
    the reason `_cmd_mine`'s probe is a read and not a stat: a two-call
    version answers the second question with a call that cannot report the
    first. An `is_file()` in front would lose exactly that: on an EACCES
    parent `is_file()` RE-RAISES rather than answering False, so the
    permission wall this handler exists for would never reach it and would
    escape as a bare traceback.
    """
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        path = candidate / CONFIG_NAME
        try:
            mode = path.stat().st_mode          # follows symlinks: dangling -> absent
        except FileNotFoundError:
            continue                            # genuinely absent here: keep walking
        except NotADirectoryError as e:
            if not os.path.isdir(candidate):
                # `start` was a FILE, so this candidate cannot hold a
                # repo.yaml at all. Absent, not unreadable — keep walking.
                continue
            # The candidate IS a directory, so ENOTDIR came from a symlink
            # whose target path runs through a non-directory. That is "cannot
            # look", and treating it as absent would resolve the walk to an
            # ANCESTOR repo and gate this tree against someone else's policy
            # — the same silent outcome as the FIFO case, one errno over.
            raise ConfigError(
                f"{path} could not be examined ({type(e).__name__}: {e}); "
                f"warden will not treat that as an absent {CONFIG_NAME} and "
                "resolve to a different repo's config") from e
        except OSError as e:
            # Everything else that stops the look — a permission wall, a
            # symlink loop. Same reasoning: "cannot look" is not "absent".
            #
            # It does NOT say the file exists, though the refusal is the same
            # either way: EACCES here comes from the CONTAINING directory's
            # mode and is byte-identical for a present and an absent
            # repo.yaml (measured), so claiming existence would send the
            # reader to look at a file that may not be there instead of at
            # the directory.
            raise ConfigError(
                f"{path} could not be examined "
                f"({type(e).__name__}: {e}); warden will not treat that as an "
                f"absent {CONFIG_NAME}") from e
        if stat.S_ISREG(mode):
            return candidate
        raise NotARegularFileError(
            errno.EINVAL,
            f"{CONFIG_NAME} is a {_kind(mode)}, not a regular file — warden "
            "will not read past it to another repo's config, and will not "
            "call it absent",
            str(path))
    raise ConfigError(f"no {CONFIG_NAME} found from {cur} upward")


def declared_rules_dir(root: Path) -> tuple[Path, str]:
    """The rules dir this repo DECLARES (`review.rules_dir`), from the root
    alone, read leniently.

    `load()` is the strict reader and stays strict: a wrong source of truth is
    worse than none. This one serves callers that hold only a root and must
    not crash on a repo.yaml they are not the ones validating — `certify` has
    to be able to SAY a repo is immature, and `tags` folds aliases on every
    recall. An ABSENT repo.yaml, or one that declares no rules_dir, falls
    back to the default location with an empty problem — a repo may
    genuinely declare nothing. A repo.yaml that EXISTS but cannot be
    resolved returns the default WITH a non-empty problem, and consuming
    that path while ignoring the problem is fail-open: a caller about to ACT
    refuses (autonomy), one about to MEASURE withholds the claim (advisor,
    lifecycle, tags), and one about to SCORE fails the check naming the
    problem (certify's ruleset-keyed checks, where a stale dir at the
    fallback location would otherwise make the bet lose silently). The
    returned path is for naming the fallback in messages, not an answer to
    build on.

    ONE derivation, period, because two would be worse than none here: a
    check keyed to the rules dir must land the same for every reader. The tag
    layer, certify, autonomy, the advisor's default, and the lifecycle
    reader's default all resolve the declaration HERE — a hardcoded default
    lets `autonomy adopt` write a rule into `.warden/rules` that a gate
    reading `policy/rules` never sees. A new caller that needs the rules dir
    from a bare root asks this function; a fresh `root / ".warden" /
    "rules"` is that same fail-open.

    Returns (rules_dir, problem). `problem` is non-empty only when the
    declaration could not be READ, which is deliberately distinct from a repo
    that declares none: collapsed into the same permissive answer, a consumer
    with `rules_dir: policy/rules` and a corrupt repo.yaml would fall back to
    a default path that does not exist, which the tag layer then reads as "no
    ruleset exists to collide with" and fires every alias — including the
    collision the guard exists to refuse.
    """
    try:
        doc = yamlio.load(read_repo_yaml(root).decode("utf-8"))
    except FileNotFoundError:
        # The ONE answer that means "absent": no repo.yaml, or a dangling
        # symlink, which resolves to the same thing. Permissive, because a
        # repo may genuinely declare nothing.
        #
        # Split on the class the reader raises, never a `path.exists()`
        # pre-check, which answers the wrong question: `Path.exists()` swallows
        # ELOOP and ENOTDIR (returning False -> the PERMISSIVE empty problem,
        # so a symlink loop at repo.yaml would read as "this repo declares
        # nothing" and `autonomy adopt` would write a rule into a guessed dir
        # the gate never reads) and RE-RAISES EACCES, crashing out of the
        # reader whose whole contract is not to crash. Asking the reader
        # itself is what makes all four readers agree.
        return root / DEFAULT_RULES_DIR, ""
    except (OSError, ValueError, yaml.YAMLError) as e:
        # OSError covers every way the file is THERE and unreadable —
        # permissions, a directory, a symlink loop, and the FIFO/socket/device
        # whose read can never finish, which `read_repo_yaml` turns into a
        # NotARegularFileError rather than a park.
        # ValueError covers UnicodeDecodeError from the decode on a non-UTF-8
        # file — the class tags.py already catches for rule files. A crash
        # out of the LENIENT reader breaks the (default, problem) contract
        # for every caller that routed here to avoid crashing.
        return root / DEFAULT_RULES_DIR, f"{CONFIG_NAME} cannot be read ({e})"
    # "Declares nothing" stays permissive; "declares something that cannot be
    # resolved" carries a problem. Coercing every malformed shape to {} would
    # make `review:` as a LIST (one indent error from valid) return the
    # default with an EMPTY problem, and a machine-tier adopt would write into
    # the guessed dir with no refusal.
    if doc is None:
        return root / DEFAULT_RULES_DIR, ""     # empty file: declares nothing
    if not isinstance(doc, dict):
        return root / DEFAULT_RULES_DIR, (
            f"{CONFIG_NAME} parses but is not a mapping (got "
            f"{type(doc).__name__}), so the declaration cannot be resolved")
    review = doc.get("review")
    if review is None:
        return root / DEFAULT_RULES_DIR, ""     # no review block: declares nothing
    if not isinstance(review, dict):
        return root / DEFAULT_RULES_DIR, (
            f"{CONFIG_NAME}: `review` is not a mapping (got "
            f"{type(review).__name__}), so the declaration cannot be resolved")
    if "rules_dir" not in review:
        return root / DEFAULT_RULES_DIR, ""     # no rules_dir key: declares nothing
    rel = review["rules_dir"]
    if not isinstance(rel, str) or not rel.strip():
        # None lands here with "" and [a, b]: `rules_dir:` with an orphaned
        # value is the null SPELLING of the empty declaration, one keystroke
        # from a merge accident — the key exists, so the repo declared
        # something that cannot be resolved. Collapsing it into key-absent
        # would send a machine-tier adopt into the guessed default with no
        # refusal.
        return root / DEFAULT_RULES_DIR, (
            f"{CONFIG_NAME}: `review.rules_dir` is declared but is not a "
            "non-empty string, so the declaration cannot be resolved")
    return root / rel, ""


def load(root: Path | None = None) -> RepoConfig:
    root = root or find_repo_root()
    # Through the shared reader, not `path.read_text()`: a probe that
    # succeeds (a writer holding the FIFO open, one byte pushed) routes
    # straight here, so a blocking read on this line would park the
    # command however well `cli._cmd_mine`'s probe is hardened.
    return parse(read_repo_yaml(root), root, root / CONFIG_NAME)


def parse(data: bytes, root: Path, path: Path | str) -> RepoConfig:
    """A RepoConfig from repo.yaml's BYTES, validated exactly as `load`
    validates the working tree's. `path` only names the source in errors — a
    caller reading repo.yaml out of a commit passes `<rev>:<path>`."""
    try:
        raw = yamlio.load(data.decode("utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: not valid YAML: {e}") from e

    # A key the schema still names, so an older file parses, and the runtime
    # refuses: checked before the schema so every spelling of it earns the one
    # sentence that says what to do rather than a shape error.
    from .proportion import FLOOR_KEY, removed_entries_refusal
    refusal = removed_entries_refusal(
        raw.get(FLOOR_KEY) if isinstance(raw, dict) else None)
    if refusal is not None:
        raise ConfigError(f"{path}: {refusal}")

    schema = json.loads(_SCHEMA_PATH.read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(raw), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
                           for e in errors[:5])
        raise ConfigError(f"{path} fails schema validation: {detail}")

    return RepoConfig(
        root=root,
        repo=raw["repo"],
        platform_pin=(raw.get("platform") or {}).get("pin"),
        components=tuple(Component(name=name, **spec)
                         for name, spec in raw["components"].items()),
        risk_tiers=tuple(RiskTier(**t) for t in raw["risk_tiers"]),
        protected_paths=tuple(ProtectedPath(**p) for p in raw.get("protected_paths", [])),
        verify={scope: tuple(_verify_step(s) for s in steps)
                for scope, steps in raw["verify"].items()},
        review=ReviewSettings(
            rules_dir=raw["review"]["rules_dir"],
            blocking_severities=tuple(raw["review"]["blocking_severities"]),
            context_excludes=tuple(raw["review"].get("context_excludes", [])),
        ),
        deploy={scope: tuple(_verify_step(s) for s in steps)
                for scope, steps in raw.get("deploy", {}).items()},
        design=(DesignSettings(
            decision_required=tuple(_design["decision_required"]),
            charter=_design.get("charter", ""),
        ) if (_design := raw.get("design")) is not None else None),
    )


def enforce_platform_pin(config: RepoConfig) -> None:
    """A consuming repo's gate cohort is defined by (its rules + its pinned
    platform version). Running a different platform than the recorded pin is a
    policy mismatch -> fail closed. No pin recorded = no check
    (pre-extraction repos, the platform repo itself)."""
    if config.platform_pin is None:
        return
    from . import __version__
    if config.platform_pin.lstrip("v") != __version__:
        raise ConfigError(
            f"repo.yaml pins platform {config.platform_pin} but warden "
            f"{__version__} is running — align the shim pin and repo.yaml")


def classify(config: RepoConfig, file_path: str) -> RiskTier:
    """Risk tier for a repo-relative path: first matching glob wins; default LOW.

    One path is HIGH before any glob is read: an enrollment's own gate
    workflow at the git root, `../../.github/workflows/warden-svc-api.yml`
    from `svc/api`, which the review of an enrollment below the root lists.
    It is gate surface, and repo.yaml's globs name paths inside the
    enrollment, so no glob of its own could tier it. That one path only:
    another enrollment's gate beside it at the root, and a path of the same
    shape naming no gate of this enrollment's, are tiered by the globs like
    anything else."""
    from .gate_workflows import is_own_root_gate_path  # local, as below
    from .rules import glob_match  # local import to keep module deps one-way

    if is_own_root_gate_path(config.root, file_path):
        return RiskTier(glob="<root gate workflow>", tier="HIGH",
                        reason="this enrollment's gate workflow at the git root, "
                               "where GitHub runs it")
    for tier in config.risk_tiers:
        if glob_match(tier.glob, file_path):
            return tier
    return RiskTier(glob="<default>", tier="LOW")
