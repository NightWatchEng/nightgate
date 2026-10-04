"""Shared fixtures: a complete synthetic repo the suite runs against.

The suite must pass with no sibling worktree present at all:
every repo fact the tests need — repo.yaml, ruleset, params-referenced files,
git history — is built here in tmp_path_factory. The fixture mirrors the
SHAPES the package supports (one rule per CHECKERS id, param-driven rules,
engine:claude rules, excludes, context_excludes), not any real repo's facts.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from warden import runs as runs_mod

REPO_ROOT = Path(__file__).parent.parent

# ── The suite refuses pytest's assertion-rewrite cache ──────────────────────
#
# THE MECHANISM. pytest keys
# `tests/__pycache__/test_x.cpython-3NN-pytest-*.pyc` on `(int(mtime), size)`
# of the source. A SAME-SIZE edit to a test module — `assert not X` ->
# `assert     X`, which is exactly what a mutation harness writes — followed by
# a restore that lands in the same integer second leaves a `.pyc` compiled FROM
# THE MUTATION whose key matches the clean source. With bytecode enabled: run 1
# clean GREEN, run 2 mutated RED, run 3 restored in the same second RED ON A
# CLEAN TREE, run 4 untouched STILL RED, run 5 source touched GREEN. That is the
# "red once, never recurred" shape: a verdict about source no longer on disk.
#
# TWO HALVES, because `_pytest/assertion/rewrite.py` gates only the WRITE on
# `sys.dont_write_bytecode` and READS an existing `.pyc` regardless:
#
#   (1) this line, which stops the suite writing rewritten bytecode for every
#       test module imported after this conftest — which is all of them;
#   (2) `pytest_configure` below, which removes any already on disk, from an
#       earlier run or another harness.
#
# Either half alone is a partial defence. WHAT IS NOT DEFENDED, stated because a
# defence described more broadly than it is, is the defect this suite is about:
# a run with `--noconftest`; a harness that imports test modules without pytest
# at all; anything executing outside `tests/` (the purge is scoped there); and
# `.pyc` files for the `warden` package itself, which pytest does not rewrite
# and which CPython keys the same way — a same-size edit to `warden/*.py`
# restored within one second has the same trap and this does not close it. The
# discipline (`PYTHONDONTWRITEBYTECODE=1` plus a `__pycache__` sweep in every
# mutation harness) is still the answer there, and
# `tests/test_guard_mutations.py` avoids the whole class differently, by never
# editing a source file at all.
#
# COST, re-derived 2026-09-21 on a tree of 4378 collected tests (this note
# stated 2319 and "a little over two minutes" until then, both of them the
# figures of an older, smaller suite). The rewrite is paid on
# EVERY run here, because the two halves above mean no rewritten `.pyc` is
# ever left on disk to read back: `--collect-only` takes about 1.75s as the
# tree stands, against about 0.27s once a cache is populated AND readable.
# So assertion rewriting costs about 1.5s per run — roughly 0.3% of a serial
# suite of about nine minutes, and about 1% of the ~140s an `-n auto` run
# takes.
#
# THE SERIAL FIGURE IS MEASURED, and it said "about twelve minutes" until
# 2026-09-21. Re-derived on an otherwise idle machine at
# 4dc253d: `uv run pytest -q` reports 4377 passed, 1 skipped in 535.72s —
# 8:55, against 720s claimed, so the denominator was about 35% high and the
# percentage it carried was correspondingly low. The wall clock is not a
# property of this tree alone (this suite shells out to git, bash and uv
# several hundred times, so it moves with the machine), which is the second
# reason to date it rather than state it.
#
# MEASURING THE CACHED HALF NEEDS BOTH HALVES OFF, and that is the trap this
# note already fell into once: an earlier re-derivation put the cached figure
# at "about 1.7s" and the cost "on the order of a tenth of a second", which
# were two timings of the SAME uncached path. Clearing `dont_write_bytecode`
# alone leaves `pytest_configure`'s purge deleting the cache before the next
# run can read it, so nothing is ever cached. A "cached" figure sitting
# within noise of the uncached one is the symptom, not a fast tree.
#
# The figures are dated rather than absolute: the test count moves every
# week, and a cost note that states a number with no date is the drift this
# one already made once.
sys.dont_write_bytecode = True


def _rewritten_bytecode(root: Path | None = None) -> list[Path]:
    """Every pytest-rewritten `.pyc` under `tests/`. One derivation, two
    callers: the purge below and the regression test that watches it."""
    root = root or REPO_ROOT / "tests"
    return sorted(root.glob("**/__pycache__/*-pytest-*.pyc"))


# The caller's git binding, unbound for the whole session and put back at the
# end. `clean_git_env` below does this too and says why; what THIS does is do
# it EARLIER — see the note in `pytest_configure`.
_UNBOUND_GIT_ENV: dict[str, str] = {}


def pytest_configure(config):
    """Remove rewritten bytecode left by an earlier run (the second half), and
    unbind the caller's git BEFORE a single test module is imported.

    THE BYTECODE HALF. `sys.dont_write_bytecode` above stops this session
    WRITING one; a `.pyc` already on disk is still READ, and the stale one is
    the whole hazard. Once per session, never per test.

    THE GIT HALF, and why it is not left to `clean_git_env`. That fixture is
    session-scoped and autouse, so it runs before the first TEST — but test
    modules are IMPORTED before that, during collection, and a module-level
    `ENV = {**os.environ, ...}` captures the binding the fixture has not
    stripped yet. Every `git` handed that dict is then bound to the CALLER's
    repository, whatever `cwd=` or `-C` it is given, because git honours the
    environment over both.

    That is not hypothetical: it is how this branch's own
    `tests/test_index_isolation.py` came to `git init`, `git add -A` and
    `git commit` into the real checkout when the suite ran from
    `.githooks/pre-push`, which git invokes with `GIT_DIR` exported. The
    fixture repos vanished into the ambient one, 24 tests errored, the index
    held a two-file tree and HEAD had moved to a fixture's `seed` commit.
    Exactly the index-write defect the guard below exists for, written by
    the module added to prevent it, and reachable ONLY from the hook — which is why every direct run was
    green.

    `pytest_configure` is the first hook that runs after this conftest is
    imported and before collection, so a module-level capture here sees an
    already-unbound environment. Fixing it at the module was necessary and
    not sufficient: the next module to spread `os.environ` into a git call
    would reopen it, and `tests/test_ambient_git.py` is the guard that keeps
    this closed.
    """
    for stale in _rewritten_bytecode():
        try:
            stale.unlink()
        except OSError:
            pass            # a read-only cache dir is not worth failing a run
    for key in runs_mod._GIT_ENV_OVERRIDES:
        if key in os.environ:
            _UNBOUND_GIT_ENV[key] = os.environ.pop(key)


def pytest_unconfigure(config):
    """Hand the caller back the git binding `pytest_configure` took."""
    os.environ.update(_UNBOUND_GIT_ENV)
    _UNBOUND_GIT_ENV.clear()


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print one line per old toolchain spelling this run read.

    `tests/toolchain.py` reads `AGENTOPS_REQUIRE_TOOLCHAIN` and
    `[tool.agentops.toolchain]` as aliases until its SUNSET_RELEASE, and an
    alias nobody is told about is never moved off. Printed in the terminal
    summary because that is written under `-q` too, and only by the
    controlling process, so an `-n auto` run prints it once.
    """
    if hasattr(config, "workerinput"):
        return
    import toolchain
    for line in toolchain.deprecations():
        terminalreporter.write_line(line, yellow=True, bold=True)


# ── No test may write the git INDEX of the checkout it runs in
#
# THE INCIDENT. A full `uv run pytest -q` left a reviewer's worktree index
# holding a tree that was not its HEAD's. The files were byte-identical to
# HEAD throughout, so nothing looked wrong — but every guard that reads
# git-tracked state reads the INDEX (`tracked()` above is `git ls-files`), and
# three of them went red on a clean tree. `git reset` fixed all three. A
# spurious red is read as a real failure, and a polluted index committed by
# mistake stages another branch's tree.
#
# WHAT THE HUNT FOUND, so the next reader does not repeat it. Instrumented
# full-suite runs — every git invocation of an index-writing verb logged with
# the repository it actually resolved to — issue ZERO such commands against
# this checkout: serially here, in an isolated clone, and with two suites
# running AT ONCE in two sibling worktrees (both green, every index byte-
# identical). And the polluted tree from the incident was identified exactly:
# it was another worktree's in-flight feature branch, staged whole, which no
# test in this suite ever has in hand. So the writer was outside the session.
# That does not make the guard unnecessary — it makes it the right shape. The
# damage is done to the run either way, and what the incident cost was three
# hours of reading unrelated reds; this turns the same event into one loud
# failure at the moment it happens.
#
# WHY A DETECTOR AND NOT A PIN. The obvious fix — export a session-private
# `GIT_INDEX_FILE` — is wrong here: git honours that variable over cwd and
# `-C`, so a single session-wide pin would redirect the index of every
# synthetic fixture repo in this suite into one shared file. And a pin cannot
# stop a writer outside the session at all. The hazard is not that git lacks a
# way to be told which index to use; it is that the index this suite READS can
# move under it. That can only be caught where it happens, so this watches the
# one index that must never move and fails the moment it does.
#
# WHAT IS WATCHED, exactly: the index of the checkout the suite runs FROM —
# `<absolute-git-dir>/index`, which for a linked worktree is
# `.git/worktrees/<id>/index` and not the common `.git/index`. Sibling
# worktrees are deliberately NOT watched from here: several agents work this
# repo at once, and another agent staging a file in THEIR worktree while this
# suite runs is not this suite's defect. A guard that goes red for someone
# else's `git add` is the same disease it exists to cure. The cross-worktree
# property is proved instead where it can be proved honestly, over throwaway
# worktrees this suite builds itself:
# `tests/test_index_isolation.py`.
#
# COMPARED BY ENTRIES, NOT BY BYTES. Git rewrites the index whenever a plain
# read command refreshes its cached stat data, which changes the file's bytes,
# its size and its mtime while staging nothing. So the cheap per-test check is
# `(size, mtime_ns, inode)` and a mismatch only ESCALATES — to `git ls-files
# -s`, mode + blob + stage + path, which a stat refresh cannot change. Under
# that escalation the false-positive rate is zero and the per-test cost is one
# `stat`.
_INDEX_WATCH: dict = {}


def ambient_index(root: Path | None = None) -> Path | None:
    """The git index of the checkout at `root` (default: this suite's), or None.

    For a LINKED WORKTREE that is `.git/worktrees/<id>/index` and not the
    common `.git/index`, which is why this asks git rather than joining
    `.git/index` onto the root: the wrong file is watched forever and never
    moves, and a guard that cannot fail is worse than none.

    None whenever git cannot answer — an exported sdist, a tree that is not a
    checkout, no git on PATH. Absent is "nothing to watch", never a failure:
    this guard exists to catch a write, not to require a repository.
    """
    try:
        out = subprocess.run(["git", "rev-parse", "--absolute-git-dir"],
                             cwd=root or REPO_ROOT, capture_output=True,
                             text=True, check=True,
                             env=runs_mod.git_env()).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    index = Path(out) / "index"
    return index if index.is_file() else None


def index_entries(index: Path, root: Path | None = None) -> str:
    """`git ls-files -s` over `index` — mode, blob, stage and path per line.

    The staged CONTENT, with every byte a stat refresh can rewrite left out,
    so "changed" means an entry moved and never that git touched the file.
    """
    out = subprocess.run(["git", "ls-files", "-s"], cwd=root or REPO_ROOT,
                         capture_output=True, text=True, check=True,
                         env={**runs_mod.git_env(), "GIT_INDEX_FILE": str(index)})
    return out.stdout


def _index_stat(index: Path) -> tuple | None:
    try:
        st = index.stat()
    except OSError:
        return None                      # deleted counts as changed
    return (st.st_size, st.st_mtime_ns, st.st_ino)


def watch_index(root: Path | None = None) -> Path | None:
    """Take the baseline for `root`'s index and return the file being watched.

    Public because `tests/test_index_isolation.py` arms this same guard over a
    throwaway checkout: the regression test must exercise the shipped code
    path, not a re-implementation of it that can drift green while this one
    rots.
    """
    root = root or REPO_ROOT
    index = ambient_index(root)
    _INDEX_WATCH.clear()
    if index is None:
        return None
    _INDEX_WATCH.update(root=root, path=index, stat=_index_stat(index),
                        entries=index_entries(index, root))
    return index


def pytest_sessionstart(session):
    """Take the baseline before a single test has run."""
    watch_index()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """Stop the run at the test during which this checkout's index moved.

    A WRAPPER, and that is correctness rather than style: a plain hookimpl
    that raises pre-empts pytest's own teardown hook, and the session's setup
    stack is then left un-unwound — every following test errors with "previous
    item was not torn down properly" and the culprit's name is buried under
    the cascade. Yielding first lets the real teardown finish, so the write
    marks one test PER PROCESS rather than every test after it.

    ONE PER PROCESS, not one full stop — and the difference is the whole of
    what this guard can honestly claim. `_INDEX_WATCH` is a module global, so
    under `pytest -n auto` each xdist worker holds its own baseline and each
    notices the same write independently: N workers means up to N marked
    tests, N-1 of them bystanders that never went near git. Measured at
    `-n 4`: 4 errors, 3 of them tests that ran no git at all. That is not a
    defect to paper over with a docstring — it is a property of a
    process-local watch on a process-shared file, and no amount of
    cross-worker bookkeeping would fix it, because the workers cannot tell
    which of them was running when a write from OUTSIDE the session landed.
    The message says so, so a reader looking at four reds does not hunt four
    culprits. Serially — what CI runs — it is exactly one, and
    `test_index_isolation` pins both shapes. The PARALLEL half of that claim
    was untrue for a while and is worth the sentence: the xdist test's
    bystanders returned instantly, so how many workers were still running
    when the write landed was a race — measured anywhere from one worker to
    four across 28 sessions — and what the test asserted could not fail
    either way. The bystanders now sleep, and the test counts DISTINCT
    WORKERS rather than errors: `25 passed, 4 errors` across gw0..gw3, 7
    sessions out of 7 (the xdist test's own docstring carries the per-session
    numbers).

    The baseline is advanced to whatever the write left behind, for the same
    reason: within a process, one write must mark ONE test. Marking every
    later test too would hide which one was running, which is the only
    locating information a reader gets when the writer is outside the
    session.
    """
    result = yield
    _fail_if_index_moved(item)
    return result


def _fail_if_index_moved(item) -> None:
    index = _INDEX_WATCH.get("path")
    if index is None:
        return
    now = _index_stat(index)
    if now == _INDEX_WATCH["stat"]:
        return
    root = _INDEX_WATCH["root"]
    try:
        entries = index_entries(index, root) if now is not None else ""
    except (OSError, subprocess.CalledProcessError):
        # Read while git was mid-rewrite, or while the index lock was held —
        # reachable under `-n auto`, where other workers run git in this same
        # checkout. Leave the baseline where it is and re-evaluate at the next
        # teardown: a guard that goes red because it could not LOOK is a flake,
        # and a flaky guard gets deleted.
        return
    _INDEX_WATCH["stat"] = now
    before = _INDEX_WATCH["entries"]
    if entries == before:
        return                           # a stat refresh: nothing was staged
    _INDEX_WATCH["entries"] = entries
    was, is_now = set(before.splitlines()), set(entries.splitlines())
    sample = sorted(is_now ^ was)[:5]
    # xdist sets PYTEST_XDIST_WORKER in each worker; empty under a serial run.
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    raise AssertionError(
        f"the git index of the checkout this suite is running in ({index}) "
        f"moved while {item.nodeid} was running"
        + (" (worker " + worker + "; under `-n auto` each worker watches the "
           "index independently, so ONE write marks one test per worker and "
           "all but one of them is a bystander — read the diff below, not the "
           "test names)" if worker else "") + ".\n"
        f"{len(was - is_now)} entries left the index, {len(is_now - was)} "
        f"entered it. First differences:\n  "
        + "\n  ".join(sample)
        + "\n\nThis is a test writing this checkout's index, and it has two causes. EITHER a test "
        "ran a git subprocess that named no repository — no `-C`, no `cwd=`, "
        "no GIT_DIR — which inherits THIS checkout: give that call its own "
        "repo (`cwd=tmp_path`) or its own index (GIT_INDEX_FILE under "
        "tmp_path), and `tests/test_git_repo_binding.py` refuses that shape "
        "statically, so start by running it. OR something outside this "
        "session staged here while the suite ran — another agent, an editor, "
        "a hook, a `warden` or `bd` command in this worktree — in which case "
        "the test named above is a bystander and the diff above says who. "
        "The diff is the evidence; the test name is only WHEN. Either "
        "way the run is no longer trustworthy: every guard that reads "
        "`git ls-files` is now reading a tree nobody wrote, so the reds it "
        "produces are not findings. `git reset` repairs the checkout; then "
        "re-run.")


# Suppression of git's own background work, as an environment layer. NOT
# ambient config: it is folded into `_AMBIENT_GIT_CONFIG` below so that one
# spread reaches every caller, but it is a SECOND concern with its own guard
# module, and it is named separately because the two must be separable. A
# control that wants to observe git's unsuppressed behaviour has to remove
# THESE keys while keeping the config layers neutralised — remove the whole
# union and the control reads the developer's ~/.gitconfig, which is the
# ambient-config leak wearing a different hat, and a machine whose global config
# sets `gc.auto = 0` then makes the control silently prove nothing.
#
# See `_AMBIENT_GIT_CONFIG` for what these keys do and why.
_NO_BACKGROUND_GIT = {
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "maintenance.auto",
    "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "gc.auto",
    "GIT_CONFIG_VALUE_1": "0",
}

# The user/system git configuration layers, pointed at nothing for the whole
# session, PLUS the background-work suppression above. Exported rather than
# written inline for two readers that both exist: the guard in
# test_ambient_git.py asserts the session runs under the three config-layer
# keys named individually in its own checker, and every fixture that builds a
# CLOSED env dict for a git
# subprocess spreads it — `test_backtest._git`, `test_decide`'s two repo
# builders, and `test_cage_runner`'s caged environment. The session mutation
# cannot reach those, because they replace os.environ rather than inherit it,
# so each must spread this constant: a hand-typed subset that omits
# GIT_CONFIG_NOSYSTEM or GIT_CONFIG_SYSTEM leaves, on the git versions that key
# exists for, a config layer readable that nobody meant them to read.
# `GIT_CONFIG_NOSYSTEM` is belt and braces for the git versions that predate
# `GIT_CONFIG_SYSTEM` (2.32); both are cheap and neither can be wrong.
#
# THE COUNT LAYER IS NOT ABOUT AMBIENT CONFIG — it is about processes. Emptying
# the config layers stops a fixture READING the machine; it does not stop git
# WRITING into a fixture repo behind the test's back. Since 2.31 a plain `git
# commit` ends by forking `git maintenance run --auto --quiet --detach`, which
# takes `$GIT_DIR/objects/maintenance.lock`; that child outlives the commit
# that spawned it. A test that commits and then removes its own `.git` races
# it, and `shutil.rmtree` dies with FileNotFoundError on `maintenance.lock` —
# the entry was in the scandir and gone before the unlink. The suite creates
# hundreds of throwaway repos, so this is latent in every one of them; load is
# what decides whether it fires, which is why the parallel gate surfaced it and
# ten serial runs did not.
#
# `GIT_CONFIG_COUNT` is the layer that reaches a repo the fixture has not
# created yet, and it outranks the repository config a fixture may write, so no
# test can opt back into a background writer by accident. `gc.auto` rides along
# because it is the other knob that lets a git command fork work of its own.
#
# WHICH KEY ACTUALLY SUPPRESSES IT, measured under `GIT_TRACE=1` on both gits
# this suite runs under, for `commit`, `fetch` and `pull`, with the config
# layers emptied:
#
#                        none   maintenance.auto=false   gc.auto=0   both
#   git 2.55             forks         no fork            no fork    no fork
#   Apple git 2.50.1     forks         no fork             FORKS     no fork
#
# So `maintenance.auto=false` is the load-bearing key on BOTH, and there is no
# measured git or command here where `gc.auto` is the one doing the work. The
# pair is not a conjunction on either git: on each of them `maintenance.auto`
# alone is sufficient. `gc.auto` is kept anyway, and as belt and braces rather
# than as a second necessary condition — it is the other knob git has for
# forking work of its own, it costs nothing, and 2.55 is a git that DOES wire
# the commit fork to it, which is what makes it worth setting on a git nobody
# here has measured yet. Note the direction, because it is the opposite of
# what you might guess from the table: it is the NEWER git where `gc.auto`
# alone suffices, and the older one where it does nothing. This paragraph has
# now read that table backwards twice — first calling the pair a conjunction
# on the git where it is least like one, then crediting the older git with
# the wiring the newer one has — so the rule for editing it is to re-read the
# row before writing the sentence.
#
# WHAT IS PINNED AND HOW, since the two keys are not pinned alike.
# tests/test_git_background_maintenance.py builds its control by removing the
# WHOLE of this constant, so what it pins BY EFFECT is the pair: on these gits
# `maintenance.auto` alone carries that test, and dropping `gc.auto` would
# leave it green. `gc.auto` is pinned only by SPELLING, by the assertion that
# `git config --get gc.auto` reads `0`.
#
# WHAT THIS LAYER DOES NOT REACH, measured rather than assumed: `GIT_CONFIG_*`
# is in git's `local_repo_env`, so git STRIPS it when it spawns into another
# repository — `git push <local path>` hands `git-receive-pack` an environment
# without it. A fixture that pushes into a second repo and then deletes THAT
# repo is back in the race, and the only thing that reaches it is
# `maintenance.auto` written into the receiving repo's own config. There is one
# such fixture, `test_cage_runner`'s origin, and it writes exactly that.
_AMBIENT_GIT_CONFIG = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    **_NO_BACKGROUND_GIT,
}

# The EXTERNAL consumer repos, lowercase. HERE rather than in either guard
# that reads it, because there are two structurally identical guards:
# `test_skillpack.py` refuses these names in a SHIPPED SKILL, `test_config.py`
# refuses them in `warden/*.py`. Two copies of a set is how one of them goes
# stale — a guard holding a single bare literal is blind to every other
# consumer, including the one that actually uses the platform today.
#
# Not "every enrolled repo", which would have to include nightgate itself, and a
# skill pack that may not name nightgate is not a rule anyone wants. Historical
# names stay in the set — naming a FORMER consumer is exactly as
# project-specific as naming the current one.
#
# Matched with word boundaries, which buys less than it looks like it does.
# It stops `distilled` and `distillation` matching, and that is all: one of
# these names is ALSO an ordinary English noun, and the prose these guards scan
# reasons about budgets, iteration caps and precision floors, so "a shortfall
# in judged cases" is a sentence either could plausibly carry. Both guards fire
# on it, deliberately — fail-closed, because a guard cannot tell a repo name
# from a noun and a human can in one second. What the failure message must NOT
# do is send that human to the wrong problem, so it says the ambiguity aloud.
CONSUMER_REPO_NAMES = ("distill", "shortfall")

def tracked(pattern: str) -> list[Path]:
    """Files git TRACKS matching `pattern`, as absolute paths.

    Derived from the index, never from a filesystem walk. A
    `ROOT.rglob(...)` / `ROOT.glob("**/...")` descends everything under the
    repo root, and a git worktree checked out under `.claude/worktrees/` is a
    full second copy of the tree: leftover worktrees make this suite's
    workflow assertions report "unpinned invocation" offenders in files nobody
    reviewed, failing `warden verify --scope tests` locally while CI — a fresh
    clone — stays green. Worse than noise: the failure names real-looking
    paths, so a reader's first move is to go fix a `ci.yml` that is already
    correct.

    It lives in conftest rather than beside any one caller because the class
    recurs: test_skillpack's policy-file discovery needs the same walk. One
    derivation, every caller.

    The index is the right source on its own terms, not just as a worktree
    workaround: these assertions are about the files this repo SHIPS. Build
    output, untracked scratch state and nested worktrees are all out of reach
    by construction, so no future stray directory can reintroduce the class.
    """
    out = subprocess.run(["git", "ls-files", "-z", "--", pattern],
                         cwd=REPO_ROOT, capture_output=True, text=True,
                         check=True)
    return sorted(REPO_ROOT / rel for rel in out.stdout.split("\0") if rel)


#: The ONE clause a CI job may carry and still be something that always runs.
#: Written once here because three guards in three files ask the question, and
#: three copies of an admissible condition is how a fourth, inadmissible one
#: gets in.
CI_TITLE_ONLY_FAST_PATH = "needs.triage.outputs.fast != 'true'"


def why_a_ci_job_might_not_run(
        job: dict, *, besides: tuple[str, ...] = ()) -> str | None:
    """None when this CI job runs on every pull request, else why it may not.

    "A guard that may not run is not a guard" is the rule, and three separate
    guards used to enforce it by asserting their job carried no `if:` at all.
    Exactly one condition is admissible, and it is not an exception to the
    rule: on a `pull_request` `edited` event that changed only text, a job may
    skip when the forge has already reported a GREEN run of this same workflow
    on this same head sha. The job ran for that sha — what is skipped is
    running it a second time over bytes that did not move.

    Both halves are required and both are checked. Without the fast-path
    clause the condition is something else entirely. Without `always()` a
    triage job that FAILED would skip every dependent, and a skipped required
    check reads to branch protection as a passed one — so the fail-open shape
    is one dropped token away and nothing else would notice.

    `besides` is for a job that pins its own clause deliberately at the call
    site (the gate job's event check). Anything left over is reported, so a
    clause added later has to be pinned by whoever adds it.
    """
    cond = str(job.get("if", "")).strip()
    if not cond:
        return None
    clauses = [c.strip() for c in cond.split("&&") if c.strip()]
    if CI_TITLE_ONLY_FAST_PATH not in clauses:
        return (f"the job is conditional on {cond!r}, and that is not the "
                "title-only fast path — a guard that may not run is not a "
                "guard")
    if "always()" not in clauses:
        return (f"the job's condition {cond!r} carries the fast path without "
                "`always()`, so a triage job that FAILED skips this one — and "
                "a skipped required check reads as a passed one")
    extra = [c for c in clauses
             if c not in ("always()", CI_TITLE_ONLY_FAST_PATH)
             and c not in besides]
    if extra:
        return (f"the job's condition carries clause(s) nothing pins: "
                f"{extra} — each one is a way for this job not to run")
    return None



@pytest.fixture(scope="session", autouse=True)
def clean_git_env():
    """The suite must not inherit the caller's git binding. Run from the
    pre-push hook, GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE retarget every
    fixture's git at the real repo and the synthetic ones silently vanish.

    THE SECOND AMBIENT LAYER. A git binding is not the only thing a fixture
    can inherit: `~/.gitconfig` and `/etc/gitconfig` are read by every `git`
    the suite spawns, and the runner does not have the developer's. A fixture
    that calls a bare `git init` takes its base branch from the ambient
    `init.defaultBranch` — `main` on one machine, `master` on the ubuntu
    runner — so a test asking for `--base main` passes locally and fails in
    CI on the same sha.

    Pointing the two config layers at /dev/null makes local and CI read the
    SAME (empty) configuration, so a test that reaches for ambient git config
    now behaves identically in both places instead of quietly differing. The
    REPOSITORY layer (`.git/config`) is untouched and must be: fixtures set
    their own, and the tests that read this repo depend on its committed
    settings.

    `test_backtest`, `test_decide`, `test_cage_runner` and `test_build_wiki`
    build closed env dicts this fixture's os.environ mutation cannot reach, so
    they spread `_AMBIENT_GIT_CONFIG` rather than type a subset of its keys;
    every other git call in the suite inherits it from here. One derivation,
    every caller.

    `test_both_ambient_config_layers_are_neutralised_for_the_session` is
    what keeps that sentence true of the tree rather than of the intent, and it
    checks BOTH ways a caller can break it: re-typing a key name, and building
    a closed env that omits the keys entirely. The second half is needed
    because a closed `{"HOME": …, "PATH": …}` handed to `git init` reads
    /etc/gitconfig with no key name to grep for. A guard that can only see the
    loud half of a class is a guard whose docstring is wrong about the class.
    """
    bound = {k: v for k, v in os.environ.items() if k not in runs_mod.git_env()}
    for key in bound:
        os.environ.pop(key)
    ambient_config = {k: os.environ.get(k) for k in _AMBIENT_GIT_CONFIG}
    os.environ.update(_AMBIENT_GIT_CONFIG)
    # GITHUB_EVENT_PATH is the same class of inherited binding. `attest check`
    # resolves its head from the Actions event before falling back to
    # `rev-parse HEAD`, so under CI every test that runs `attest check` inside
    # a fixture repo would get the PLATFORM's PR head — a sha the fixture does
    # not contain — and exit 2 with "Invalid revision range", while a
    # developer machine has no such env and passes. The suite must not inherit
    # the caller's CI binding any more than it inherits their git binding.
    event = os.environ.pop("GITHUB_EVENT_PATH", None)
    yield
    if event is not None:
        os.environ["GITHUB_EVENT_PATH"] = event
    for key, value in ambient_config.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    os.environ.update(bound)

REPO_YAML = """\
version: 1
repo: sampleproj
components:
  app:
    path: app/
    lang: python
    description: application code
  migrations:
    path: db/migrations/
    lang: sql
    description: database migrations
  schemas:
    path: schemas/
    lang: json
    description: wire contracts
risk_tiers:
  - glob: db/migrations/**
    tier: HIGH
    reason: schema changes are irreversible
  - glob: app/core/secrets_surface.py
    tier: HIGH
    reason: credential handling surface
  - glob: app/**
    tier: MEDIUM
    reason: production code
verify:
  smoke:
    - run: "true"
  contracts:
    - run: python3 -c "import json; json.load(open('schemas/api.schema.json'))"
review:
  rules_dir: .warden/rules
  blocking_severities: [HIGH]
  context_excludes:
    - "**/uv.lock"
    - "**/*.png"
"""

# One rule per id the CHECKERS registry knows, plus two engine:claude rules —
# the full shape surface: plain, excludes, params-driven, claude-deferred.
RULES = {
    "secrets-in-diff.md": """\
---
id: secrets-in-diff
severity: HIGH
engine: python
applies_to: ["**"]
excludes: ["tests/fixtures/**"]
---
No credentials in added lines. Evidence must quote the line.
""",
    "prompt-eval-gate.md": """\
---
id: prompt-eval-gate
severity: HIGH
engine: python
applies_to: ["app/prompt.py"]
params:
  surface_symbols: [PROMPT_TEXT]
  companion_file: db/BASELINE
---
Prompt surface edits land with a same-commit eval baseline ratchet.
""",
    "baseline-ratchet.md": """\
---
id: baseline-ratchet
severity: HIGH
engine: python
applies_to: ["db/BASELINE"]
params:
  justification_globs: ["app/prompt.py", "app/evals/**"]
---
The eval baseline may only rise, and only with a justifying change in the diff.
""",
    "contract-freeze.md": """\
---
id: contract-freeze
severity: HIGH
engine: python
applies_to: ["schemas/**"]
params:
  schema_file: schemas/api.schema.json
  fixture_files:
    - tests/fixtures/sample_payload.json
---
The wire contract is additive-only; fixtures move in the same diff.
""",
    "migration-safety.md": """\
---
id: migration-safety
severity: HIGH
engine: python
applies_to: ["db/migrations/**"]
---
Migrations are immutable and non-destructive without an allow marker.
""",
    "scope-creep.md": """\
---
id: scope-creep
severity: MEDIUM
engine: claude
applies_to: ["**"]
---
Flag changes outside the declared task scope.
""",
    "tests-required.md": """\
---
id: tests-required
severity: MEDIUM
engine: claude
applies_to: ["app/**"]
---
New logic carries tests in the same diff.
""",
}

# Working files the rules' params reference, plus enough tree to exercise
# risk-tier classification and diff building.
FILES = {
    "app/prompt.py": 'PROMPT_TEXT = """\nYou are a careful reviewer.\n"""\n',
    "app/core/secrets_surface.py": (
        'import os\n\nAPI_KEY = os.environ.get("SAMPLE_API_KEY", "")\n'),
    "db/BASELINE": "0.95\n",
    "db/migrations/0001_init.sql": (
        "CREATE TABLE items (id uuid PRIMARY KEY);\n"
        "ALTER TABLE items ENABLE ROW LEVEL SECURITY;\n"
        "CREATE POLICY own ON items FOR SELECT USING (true);\n"),
    "schemas/api.schema.json": (
        '{"type": "object", "properties": {"id": {"type": "string"}}}\n'),
    "tests/fixtures/sample_payload.json": '{"id": "abc"}\n',
    "uv.lock": "# lockfile noise excluded from review context\n",
    "README.md": "# sampleproj\n",
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(scope="session")
def sample_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("sampleproj")
    (root / "repo.yaml").write_text(REPO_YAML)
    rules_dir = root / ".warden" / "rules"
    rules_dir.mkdir(parents=True)
    for name, content in RULES.items():
        (rules_dir / name).write_text(content)
    for rel, content in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "initial fixture state")
    return root


def copy_sample_repo(sample_repo: Path, dst: Path) -> Path:
    """Copy the sample fixture WITHOUT its `.git`.

    `sample_repo` is session-scoped and holds a real git repo, so git's
    background maintenance creates and removes `.git/objects/*.lock`
    underneath it. A bare `shutil.copytree` enumerates a lock file and then
    fails when it vanishes mid-copy:

        shutil.Error: [(.../.git/objects/maintenance.lock, ...,
                        "[Errno 2] No such file or directory")]

    One CI job can pass while another fails on the SAME commit running the
    same pytest, which is the signature of a race rather than a defect in the
    code under test. Nothing that copies this fixture needs its history — the
    advisor and CLI paths read files, not commits — so the `.git` goes.
    """
    import shutil
    shutil.copytree(sample_repo, dst, ignore=shutil.ignore_patterns(".git"))
    return dst


@pytest.fixture(scope="session")
def sample_cfg(sample_repo):
    from warden import config as config_mod
    return config_mod.load(sample_repo)


def seed_rules(root: Path, *rule_ids: str) -> Path:
    """Write a minimal declared ruleset at root/.warden/rules; return the dir.

    `memory ingest` resolves every finding's rule_id against the declared
    ruleset, so any fixture that ingests must declare the ids
    its findings use — exactly as an enrolled repo does.
    """
    rules_dir = root / ".warden" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for rule_id in rule_ids:
        (rules_dir / f"{rule_id}.md").write_text(
            f"---\nid: {rule_id}\nseverity: MEDIUM\nengine: claude\n"
            'applies_to: ["**"]\n---\nfixture rule\n')
    return rules_dir


def finishes_within(seconds: float, fn, *args, **kwargs):
    """Run `fn` on a daemon thread; fail if it has not returned in `seconds`.

    The parked-read regressions feed warden a repo.yaml whose READ can never
    finish (a FIFO with no writer). Called bare, a reintroduced hazard would
    not fail this suite — it would park it, until the CI job's own wall clock
    killed the run, which reads as infrastructure flake rather than as this
    defect. So the bound is the assertion.

    The thread is a DAEMON on purpose: a parked one can never be joined, and a
    non-daemon thread would hold the interpreter open at exit — turning a
    failed assertion into a hung test session, which is the thing being
    guarded against.

    Returns what `fn` returned and re-raises what it raised, so a caller can
    still assert on the value or wrap the call in `pytest.raises`.
    """
    import threading

    box: dict = {}

    def run() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise AssertionError(
            f"{getattr(fn, '__name__', fn)!r} did not finish in {seconds}s — "
            "it parked (a repo.yaml read that never returns)")
    if "error" in box:
        raise box["error"]
    return box["value"]


def wake_fifo_readers(path: Path) -> bool:
    """Attach a writer to `path` and close it, so any reader already blocked in
    `open()` returns at EOF. True if a writer could be attached.

    This is the half `unlink` cannot do. On POSIX, unlinking a
    FIFO does NOT wake a process blocked opening it for read: that process is
    waiting for a WRITER, and once the name is gone no writer can ever open it
    by path — so the unlink converts a recoverable park (someone could still
    open the path for writing) into a permanent one. Opening `O_WRONLY |
    O_NONBLOCK` is what a waiting reader is waiting for; it raises ENXIO when
    nobody is waiting, which is why the return value is a fact and not a
    promise.
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return False        # ENXIO: no reader waiting. ENOENT: already gone.
    os.close(fd)
    return True


@pytest.fixture
def make_fifo():
    """Create FIFOs that are reaped when the test finishes, fails, or parks.

    Two hazards, and they want different things. A FIFO left in a tmp dir
    outlives the test — pytest keeps the last few `tmp_path` trees — and
    anything that LATER walks that tree can park on it exactly as warden did;
    unlinking is the whole answer to that one. A reader ALREADY blocked in
    `open()` is the other, and unlinking does not help it at all: see
    `wake_fifo_readers`, which is why teardown attaches a writer BEFORE it
    unlinks.

    What this still does not do, because the fixture cannot: it does not bound
    the lifetime of a reader a test spawns. A test that starts a blocking reader
    and then fails before teardown runs leaves that reader parked until teardown
    arrives — which it does, on any ordinary failure, but not if the session is
    killed. An investigating agent reaching for a blocking probe should use
    `conftest.finishes_within` or this fixture, not a hand-rolled one: a
    reader parked by an ad-hoc script has nothing to reap it and can leak for
    days.
    """
    made: list[Path] = []

    def _make(path: Path) -> Path:
        os.mkfifo(path)
        made.append(Path(path))
        return Path(path)

    yield _make
    for path in made:
        wake_fifo_readers(path)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
