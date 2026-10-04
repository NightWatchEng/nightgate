"""A test run must not move any checkout's git INDEX.

THE INCIDENT. A full `uv run pytest -q` left a reviewer's worktree index
holding a tree its HEAD did not have. The files were byte-identical to HEAD
the whole time, so nothing looked wrong — but every guard in this suite that
asks what the repository TRACKS reads the index (`conftest.tracked()` is
`git ls-files`), and three of them went red on a clean tree. `git reset` fixed
all three. The cost is not the index: it is that a spurious red is
indistinguishable from a real one, and a polluted index committed by mistake
stages a tree nobody wrote.

WHAT THE SUITE WAS CLEARED OF. The hunt instrumented every git invocation of
an index-writing verb with the repository it actually resolved to, over full
runs: serially in a real worktree with seven siblings, in an isolated clone,
and with two suites running AT ONCE in two sibling worktrees. Zero commands
reached any checkout, both concurrent runs were green, and every index was
byte-identical afterwards. The incident's polluted tree was identified too —
another worktree's in-flight feature branch, staged whole, which nothing here
ever holds. So this module does not pin a culprit test; it pins the PROPERTY,
and the guard it exercises turns the same event, from whatever writer, into
one named failure at the moment it happens instead of three unrelated reds
three minutes later.

WHAT IS PROVED HERE, and how. Every test in this module runs a MINIATURE
pytest session — armed with the SHIPPED guard out of `tests/conftest.py`, via
`watch_index`, never a re-implementation — inside a throwaway repository built
under `tmp_path`. Throwaway because the hazard can only be demonstrated by
committing it: a test that stages into a checkout has to have a checkout to
stage into, and it must never be this one or the worktree of any other agent
working this repo.

The four properties, in three tests — the first two are one behaviour and are
proved in one session:

  1. a session that stages into the checkout it runs in goes RED, naming the
     test that did it;
  2. exactly ONE test goes red — the cascade is what buried the culprit the
     first time;
  3. that session leaves a SIBLING worktree's index byte-identical, which is
     the bead's acceptance property;
  4. a git read that merely refreshes the index's cached stat data keeps the
     session GREEN — the guard's own false-positive half, without which it
     would fail sessions that staged nothing and be turned off within a week.

These three prove the DETECTOR works. Its preventive counterpart — a static
refusal of the repo-less call shape over every tracked `*.py` file, so the
detector never has to earn its keep on a call this repo wrote — lives in
`tests/test_git_repo_binding.py`, kept separate because the sweep in
`test_guard_mutations.py` re-runs that module once per exemption and this one
drives nested pytest sessions.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from warden import runs as runs_mod

from conftest import _AMBIENT_GIT_CONFIG, REPO_ROOT, index_entries


def git_env(**extra: str) -> dict:
    """The environment every git call in this module runs under.

    A FUNCTION, not a module constant, and that is the whole point. Spreading
    `os.environ` into a dict at module scope evaluates it at IMPORT, which
    pytest does during collection — before any session fixture has unbound the
    caller's git. Run from `.githooks/pre-push`, which git invokes with
    `GIT_DIR` exported, such a dict carries that binding into every `git init`
    / `add` / `commit` below, and git honours the environment over `cwd=` and
    `-C` alike: the throwaway repos silently become the real checkout. That
    happened here — 24 errors, the index holding a two-file tree, HEAD moved
    to a fixture's `seed` commit — the index-write defect committed by the
    module written to prevent it.

    `runs.git_env()` is the repo's one derivation of "os.environ minus the
    repo-binding variables", read fresh on every call. `conftest.
    pytest_configure` now strips them before collection as well, so this is
    the second of two locks on the same door; either alone would have held,
    and the cost of both is one function call.

    The identity is fixed here because the session points the user and system
    config layers at /dev/null, so there is none to inherit.
    """
    return {**runs_mod.git_env(), **_AMBIENT_GIT_CONFIG,
            "GIT_AUTHOR_NAME": "isolation",
            "GIT_AUTHOR_EMAIL": "isolation@example.invalid",
            "GIT_COMMITTER_NAME": "isolation",
            "GIT_COMMITTER_EMAIL": "isolation@example.invalid", **extra}

# The mini-session's conftest. It loads the real one by PATH under a different
# module name: pytest has already bound the name `conftest` to this generated
# file, so a plain `import conftest` here would import the generated file into
# itself. `watch_index(HERE)` re-arms the shipped guard on the throwaway
# checkout; the teardown hook is re-exported unchanged.
MINI_CONFTEST = '''\
import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("platform_conftest", {real!r})
platform_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(platform_conftest)

pytest_runtest_teardown = platform_conftest.pytest_runtest_teardown
# The real one: it unbinds the caller's git BEFORE this session imports a test
# module, which is the half a module-level `os.environ` capture defeats.
pytest_configure = platform_conftest.pytest_configure
pytest_unconfigure = platform_conftest.pytest_unconfigure


def pytest_sessionstart(session):
    platform_conftest.watch_index(HERE)
'''

# The hunted shape, exactly: git handed no repository at all — no `-C`, no
# `cwd=`, no GIT_DIR — so it walks up from the process's cwd and finds the
# checkout the session is running in.
STAGES_THE_CHECKOUT = '''\
import subprocess


def test_clean_before():
    assert True


def test_stages_the_checkout():
    (__import__("pathlib").Path("planted.txt")).write_text("staged by a test\\n")
    subprocess.run(["git", "add", "-A"], check=True)


def test_clean_after():
    assert True
'''

# A git READ that rewrites the index without staging anything: touching a
# tracked file makes its cached stat data stale, and the next `git status`
# re-stats, re-hashes, finds the same blob and writes the index back with
# fresh stat data. Different bytes, different size-or-mtime, identical
# entries.
REFRESHES_THE_INDEX = '''\
import os
import pathlib
import subprocess


def test_a_read_that_refreshes_the_cached_stat_data():
    tracked = pathlib.Path("tracked.txt")
    os.utime(tracked, (0, 0))                     # make the stat cache stale
    subprocess.run(["git", "status", "--porcelain"], check=True,
                   capture_output=True)
'''


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True, env=git_env())


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """A throwaway repository with one tracked file, committed on `main`."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "tracked.txt").write_text("tracked\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    return root


def _mini_session(where: Path, name: str, body: str,
                  extra: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run a guarded pytest session with cwd = `where`, and report it.

    cwd is the whole point: a git subprocess that names no repository resolves
    from it, so the session has to actually RUN there rather than merely
    point at it.
    """
    (where / "conftest.py").write_text(
        MINI_CONFTEST.format(real=str(REPO_ROOT / "tests" / "conftest.py")))
    (where / name).write_text(body)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--no-header", *(extra or []), name],
        cwd=where, capture_output=True, text=True,
        env=git_env(PYTHONPATH=str(REPO_ROOT),
                    PYTHONDONTWRITEBYTECODE="1"))


def test_a_test_that_stages_into_its_own_checkout_is_named_and_fails(
        fixture_repo):
    """Property 1 and 2: the write is caught, the culprit is NAMED, and the
    blame does not spread.

    Both halves in one session because they are one behaviour. Drop the
    `wrapper=True` from `conftest.pytest_runtest_teardown` and this goes red
    on the second assertion, not the first: pytest's own teardown never runs,
    the setup stack is left un-unwound, and `test_clean_after` errors with
    "previous item was not torn down properly" — the cascade that buried the
    culprit in the original incident.
    """
    proc = _mini_session(fixture_repo, "test_stager.py", STAGES_THE_CHECKOUT)
    assert proc.returncode != 0, (
        "a test staged into the checkout it ran in and the session stayed "
        f"green:\n{proc.stdout}\n{proc.stderr}")
    assert "test_stager.py::test_stages_the_checkout" in proc.stdout, (
        f"the failure does not name the offending test:\n{proc.stdout}")
    assert "it has two causes" in proc.stdout, (
        f"the failure does not explain its causes:\n{proc.stdout}")
    # Three passed, one error: the offending test's BODY succeeds — staging is
    # not an exception — and it is its teardown that goes red, so the count of
    # passes includes it. What the error count pins is that the blame stopped
    # there and `test_clean_after` was not dragged down with it.
    assert "3 passed, 1 error" in proc.stdout, (
        "exactly one test must be blamed — the innocent tests around it have "
        f"to survive:\n{proc.stdout}")


def test_a_session_in_one_worktree_leaves_a_sibling_worktree_index_intact(
        fixture_repo, tmp_path):
    """Property 3, the bead's acceptance: a run in one worktree leaves
    ANOTHER worktree's index untouched.

    Two LINKED worktrees of one repository, each with its own index under
    `.git/worktrees/<id>/`, and deliberately on different trees so a leak
    would be visible rather than a coincidence. The session runs in A and
    stages there; B is compared both ways — raw bytes, and the entries
    `git ls-files -s` reports — because either alone can miss a change the
    other sees.

    The last assertion is what stops this passing for the wrong reason: the
    hazard must actually have fired in A, or "B is untouched" is the trivial
    truth about a session that did nothing.
    """
    a, b = tmp_path / "wt-a", tmp_path / "wt-b"
    _git(fixture_repo, "worktree", "add", "-q", "-b", "wt-a", str(a))
    _git(fixture_repo, "worktree", "add", "-q", "-b", "wt-b", str(b))
    # B carries a tree A does not, so a leak cannot hide behind equality.
    (b / "only-in-b.txt").write_text("b\n")
    _git(b, "add", "-A")
    _git(b, "commit", "-qm", "b diverges")

    b_index = fixture_repo / ".git" / "worktrees" / "wt-b" / "index"
    assert b_index.is_file(), f"no linked-worktree index for B at {b_index}"
    b_bytes, b_entries = b_index.read_bytes(), index_entries(b_index, b)
    a_index = fixture_repo / ".git" / "worktrees" / "wt-a" / "index"
    a_entries = index_entries(a_index, a)

    proc = _mini_session(a, "test_stager.py", STAGES_THE_CHECKOUT)

    assert b_index.read_bytes() == b_bytes, (
        "a test session in worktree A rewrote worktree B's index "
        f"({b_index}) — the whole defect")
    assert index_entries(b_index, b) == b_entries, (
        f"worktree B's staged entries moved during a session in A:\n{proc.stdout}")
    assert index_entries(a_index, a) != a_entries, (
        "the staging test did not reach A's index either, so this test "
        f"proved nothing about isolation:\n{proc.stdout}\n{proc.stderr}")
    # AND the guard SAW it. Without this the test passes with the guard
    # deleted wholesale — the three assertions above are about git's own
    # linked-worktree index separation, which nothing in this repo can break,
    # so they are the CONTROL and this is the measurement. Raised in review
    # round 1 (tests-bite) after the examiner ran exactly that deletion and
    # watched all three hold.
    assert proc.returncode != 0 and "it has two causes" in proc.stdout, (
        "worktree B is intact, but the guard in A never fired — so this test "
        "is measuring git's worktree separation, not anything this repo "
        f"ships:\n{proc.stdout}\n{proc.stderr}")


def test_a_git_read_that_only_refreshes_the_index_keeps_the_session_green(
        fixture_repo):
    """Property 4, the opposite error: the guard must not fire on a git read.

    `git status` rewrites the index whenever the cached stat data is stale —
    different bytes, different mtime, nothing staged. A guard comparing the
    FILE would go red here, on a session that did exactly what every
    well-behaved fixture does, and would be disabled within a week.

    Narrow the escalation in `conftest._fail_if_index_moved` from
    `index_entries` back to the file's bytes and this goes red.
    """
    index = fixture_repo / ".git" / "index"
    before = index.read_bytes()
    proc = _mini_session(fixture_repo, "test_reader.py", REFRESHES_THE_INDEX)
    assert proc.returncode == 0, (
        "a git read that staged nothing failed the session — the guard "
        f"cannot tell a stat refresh from a write:\n{proc.stdout}\n{proc.stderr}")
    assert index.read_bytes() != before, (
        "the index was not rewritten at all, so the false-positive case this "
        "test exists for never arose — it passed without testing anything")



# The parallel shape, which is the one the pre-push hook now runs.
#
# THE BYSTANDERS SLEEP, and that is the test rather than a detail of it.
# The guard fires in TEARDOWN, so a worker can only notice
# the write if it still has a test running when the write lands. With `assert
# True` bodies the bystanders raced the stager's `git add`, and how many
# workers were still running when it landed was luck — so the multi-worker
# shape this module's test NAME claims, and `conftest.
# pytest_runtest_teardown`'s docstring says is pinned here, was reached only
# sometimes. The instability, and the measurements behind it, are in that
# test's own docstring. 0.2s per bystander is the smallest change that keeps
# every worker busy across the write, and it is the half that DOES reproduce:
# `25 passed, 4 errors` across gw0..gw3, 7 sessions out of 7, on each of the
# two machines that ran it.
MANY_TESTS_ONE_STAGER = '''\
import subprocess
import time


def test_stages_the_checkout():
    (__import__("pathlib").Path("planted.txt")).write_text("staged\\n")
    subprocess.run(["git", "add", "-A"], check=True)


''' + "".join(f"def test_bystander_{i:03d}():\n    time.sleep(0.2)\n\n\n"
              for i in range(24))


def test_under_xdist_the_guard_marks_one_test_per_worker_and_says_so(
        fixture_repo):
    """The claim the guard makes under `-n auto`, pinned — because this PR
    makes `-n auto` what `.githooks/pre-push` runs.

    `conftest._INDEX_WATCH` is a module global, so every xdist worker takes
    its own baseline and every worker notices the same write: one write marks
    one test PER WORKER, and all but one of those tests is a bystander that
    never went near git. That is not fixable by bookkeeping — a worker cannot
    know which of its siblings was running when a write from outside the
    session landed — so the honest move is to SAY it, and this is what keeps
    the saying true.

    Three properties, and the last two are what matter to whoever reads four
    reds at 2am: the session still fails (the guard does not go silent under
    parallelism), the write is marked on MORE THAN ONE worker, and every
    failure carries the worker id and the bystander warning. Delete the
    `PYTEST_XDIST_WORKER` branch from `conftest._fail_if_index_moved` and the
    third assertion goes red.

    Raised in review round 1 (enforcement-truth): the docstring promised
    "exactly one test goes red" and the measured answer at `-n 4` was four,
    three of them innocent.

    THE SECOND ASSERTION IS THE ONE THIS TEST IS NAMED FOR, and it was not
    always here (nor could it have been — see below). What
    stood in its place was `errors >= 1`, which cannot fail once `returncode
    != 0` has passed; and half of the term it counted, the substring "wrote
    the git index", appears nowhere in `conftest`'s message, so it
    contributed 0 on every run there has ever been — measured 0 occurrences
    in 14 consecutive sessions.

    WHY THE BYSTANDER BODIES HAD TO CHANGE TOO, stated from the measurements
    rather than from the bead that opened this — and stated as a RANGE,
    because no single sequence reproduces. With `assert True` bystanders the
    marked-worker count is not one and not four: it is load-dependent. Three
    independent runs of this exact fixture, on two machines:

      1, 1, 4, 2, 4, 4, 1              (7 sessions)
      4, 4, 4, 4, 2, 4, 3              (7 sessions)
      4, 3, 4, 4, 2, 4, 4 / 3, 4, 1, 4, 2, 4, 4   (14 sessions)

    — counted off those rows rather than recalled, because the sentence that
    stood here said FIVE ones and the rows hold four: one
    worker in 4 of the 28 sessions, four workers in 17, two or three in the
    other 7. 4 + 17 + 7 = 28, which is the check the old sentence could not
    survive. The bead that opened this recorded 7 of 7 at one worker, which no
    run here reproduced; that figure is a third sample from a differently
    loaded machine, not a contradiction of these, and the honest reading of
    all four is that the count is a race and nothing else. So asserting
    `len(marked) > 1` against those bodies would have been a flake on a
    schedule nobody controls — a worse defect than the dead assertion it
    replaces. Making the bystanders sleep is what turns the race into a
    property, and that half DOES reproduce exactly: `25 passed, 4 errors`
    across gw0..gw3, 7 of 7 on each machine that ran it. The assertion and
    the fixture are one change, and neither is sound alone.
    """
    pytest.importorskip("xdist", reason="pytest-xdist is the subject here")
    proc = _mini_session(fixture_repo, "test_stager.py", MANY_TESTS_ONE_STAGER,
                         extra=["-n", "4", "-p", "xdist"])
    assert proc.returncode != 0, (
        f"the guard went silent under -n 4:\n{proc.stdout}\n{proc.stderr}")
    # The worker ids `conftest._fail_if_index_moved` parenthesises, read off
    # that message — so a reworded message reddens this rather than silently
    # zeroing it, which is exactly how the dead term above survived.
    marked = sorted(set(re.findall(r"\(worker (gw\d+);", proc.stdout)))
    assert len(marked) > 1, (
        "one `git add` was marked on "
        f"{len(marked) or 'no'} worker(s) ({marked or 'none'}) at -n 4, so "
        "this session is the SERIAL shape under a parallel flag and the "
        "per-worker claim in this test's name — and in "
        "`conftest.pytest_runtest_teardown`'s docstring — went unmeasured:\n"
        f"{proc.stdout}")
    # ONE test per worker, which is the rest of the name: the error count and
    # the marked-worker count are the same number, so a worker that marked
    # two tests (the baseline failing to advance) or none (a red raised by
    # something other than the guard) is a red here.
    assert f"{len(marked)} errors" in proc.stdout, (
        f"{len(marked)} workers were marked but the session did not report "
        f"{len(marked)} errors — one write must mark exactly one test per "
        f"worker:\n{proc.stdout}")


def test_a_module_imported_under_an_exported_git_dir_binds_to_no_repo(
        fixture_repo, tmp_path):
    """The pre-push shape, which is the one that actually bit.

    `git push` invokes `.githooks/pre-push` with `GIT_DIR` exported — measured,
    not assumed: probing the real hook prints
    `GIT_DIR=/…/.git/worktrees/<name>`. Every `git` the suite then spawns is
    bound to THAT repository, over `cwd=` and over `-C`, unless something
    strips it. `conftest.clean_git_env` strips it, but it is a session
    fixture and test modules are IMPORTED first, so a module-level
    `{**os.environ, …}` captures the binding before the strip and hands it to
    every fixture call in that module.

    This module did exactly that, and running `git push` was the first thing
    that ran the suite under the hook: `git init` / `add -A` / `commit` in
    what should have been a throwaway repo instead wrote the real checkout —
    24 errors, the index reduced to the fixture's two-file tree, and the
    branch ref moved to a fixture's `seed` commit. Every direct `uv run
    pytest` was green, because nothing exports `GIT_DIR`.

    So the environment is SIMULATED rather than waited for: a nested session
    launched with `GIT_DIR` and `GIT_WORK_TREE` pointing at a decoy repo, and
    a test module that captures `os.environ` at import exactly as the broken
    one did. What is asserted is the property that failed — the decoy's HEAD
    and index do not move, and the fixture repo the test names is what got
    written.

    WHAT THIS PINS, exactly, because the two locks are not equals. The
    capture in the fixture below lives in the NESTED module, so only the
    session-wide strip can reach it: drop the `_GIT_ENV_OVERRIDES` loop from
    `conftest.pytest_configure` and this goes red. Spreading `os.environ`
    again in THIS module's `git_env` does NOT redden it, because the strip has
    already emptied what the spread would copy — that lock guards this
    module's own calls if the first is ever removed, and
    `test_this_modules_git_env_carries_no_repo_binding` is what pins it.
    Saying "revert either and it fails" would have been the enforcement claim
    this repo's own rule refuses; it was written that way first, measured, and
    corrected.
    """
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    _git(decoy, "init", "-q", "-b", "main")
    (decoy / "decoy.txt").write_text("do not touch\n")
    _git(decoy, "add", "-A")
    _git(decoy, "commit", "-qm", "decoy")
    decoy_head = _git(decoy, "rev-parse", "HEAD").stdout.strip()
    decoy_index = (decoy / ".git" / "index").read_bytes()

    work = tmp_path / "work"
    work.mkdir()
    (work / "conftest.py").write_text(
        MINI_CONFTEST.format(real=str(REPO_ROOT / "tests" / "conftest.py")))
    # The broken shape: os.environ captured at import, then handed to git.
    (work / "test_captures.py").write_text(
        "import os, pathlib, subprocess\n"
        "CAPTURED = dict(os.environ)\n"
        "\n"
        "def test_builds_a_repo_of_its_own(tmp_path):\n"
        "    root = tmp_path / 'own'\n"
        "    root.mkdir()\n"
        "    for argv in (['init', '-q', '-b', 'main'], ['add', '-A'],\n"
        "                 ['commit', '-qm', 'own']):\n"
        "        if argv[0] == 'add':\n"
        "            (root / 'mine.txt').write_text('mine\\n')\n"
        "        subprocess.run(['git', *argv], cwd=root, check=True,\n"
        "                       capture_output=True, env=CAPTURED)\n"
        "    pathlib.Path(os.environ['OTH6_REPORT']).write_text(\n"
        "        subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root,\n"
        "                       capture_output=True, text=True,\n"
        "                       env=CAPTURED).stdout.strip())\n")
    report = tmp_path / "report.txt"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--no-header", "test_captures.py"],
        cwd=work, capture_output=True, text=True,
        env=git_env(PYTHONPATH=str(REPO_ROOT), PYTHONDONTWRITEBYTECODE="1",
                    OTH6_REPORT=str(report),
                    GIT_DIR=str(decoy / ".git"), GIT_WORK_TREE=str(decoy)))

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert (decoy / ".git" / "index").read_bytes() == decoy_index, (
        "a nested session inherited GIT_DIR through a module-level "
        "os.environ capture and staged into the decoy repository — this is "
        "the shape that wrote the real checkout from the pre-push hook")
    assert _git(decoy, "rev-parse", "HEAD").stdout.strip() == decoy_head, (
        "the decoy's HEAD moved: a fixture's `git commit` landed on the "
        "ambient repository, exactly as it did on this branch")
    assert report.read_text().strip() != decoy_head, (
        "the nested test's own repo resolved to the decoy, so the binding "
        "was never stripped and this test proved nothing")


def test_this_modules_git_env_carries_no_repo_binding(monkeypatch):
    """The second lock, pinned on its own terms.

    `git_env()` must not hand a repo binding to any git call in this module,
    even when the ambient environment carries one — which is the state the
    pre-push hook creates and the state `conftest.pytest_configure` normally
    removes before collection. Set them here deliberately, AFTER that strip,
    so this measures the function rather than the session it runs in.

    Spread `os.environ` instead of `runs.git_env()` in `git_env` and this goes
    red on the first variable.
    """
    for key in runs_mod._GIT_ENV_OVERRIDES:
        monkeypatch.setenv(key, "/nowhere/decoy")
    leaked = sorted(k for k in runs_mod._GIT_ENV_OVERRIDES if k in git_env())
    assert not leaked, (
        f"git_env() passes the caller's git binding through: {leaked}. Git "
        "honours these over cwd= and -C, so every throwaway repo in this "
        "module would silently become whatever repository the caller was "
        "bound to — the index-write defect, the way it actually happened here.")
