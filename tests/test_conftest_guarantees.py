"""The two promises `tests/conftest.py` makes to every test in the suite.

Each promise is asserted on the mechanism behind it, not on its wording:

- the suite must not read a STALE pytest-rewritten `.pyc`. That trap yields
  wrong verdicts and it reproduces deterministically — so the defence is
  asserted rather than described.
- `make_fifo`'s teardown removes FIFOs "even when the test fails or PARKS".
  For a reader already blocked in `open()` an unlink alone does not help, it
  makes the park permanent. So teardown reaps, and a reader is watched
  returning.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import conftest as conftest_mod
from guard_mutation import _fixture_function

REPO_ROOT = Path(__file__).parent.parent


def test_the_suite_writes_no_rewritten_bytecode_under_tests():
    """Half 1: `sys.dont_write_bytecode` is set BEFORE any test
    module is imported, so pytest's assertion rewriter cannot write a `.pyc`
    for one.

    Driven in a SUBPROCESS with `PYTHONDONTWRITEBYTECODE` deliberately REMOVED
    from the child's environment, because the thing under test is the conftest
    line and not the variable this session happens to run under. The child is
    one fast module, so this costs about a second.

    Remove `sys.dont_write_bytecode = True` from `tests/conftest.py` and this
    goes red: the child then leaves `tests/__pycache__/*-pytest-*.pyc` behind.
    """
    env = {k: v for k, v in os.environ.items()
           if k != "PYTHONDONTWRITEBYTECODE"}
    assert "PYTHONDONTWRITEBYTECODE" not in env
    before = set(conftest_mod._rewritten_bytecode())
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--no-header", "tests/test_measure.py"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    written = set(conftest_mod._rewritten_bytecode()) - before
    assert not written, (
        "the suite wrote pytest-rewritten bytecode for a test module: "
        f"{sorted(p.name for p in written)}. pytest keys that file on "
        "(int(mtime), size) of the source, so a same-size mutation restored "
        "within one second is served from a .pyc compiled FROM THE MUTATION — "
        "the stale-bytecode trap, which has produced three wrong review verdicts "
        "on this repo")


def test_the_session_purges_rewritten_bytecode_it_did_not_write():
    """Half 2: `pytest_configure` removes a rewritten `.pyc` written by an
    earlier run or by another harness.

    `sys.dont_write_bytecode` gates only the WRITE; `_pytest/assertion/rewrite`
    READS an existing `.pyc` whatever that flag says, and the stale one is the
    whole hazard. So the purge is not belt and braces — it is the half that
    covers the file already on disk.

    Driven over a planted file rather than over the real cache, so the purge is
    watched doing its work instead of being inferred from an empty directory —
    which is what a purge that had stopped running also produces.
    """
    cache = REPO_ROOT / "tests" / "__pycache__"
    cache.mkdir(exist_ok=True)
    planted = cache / "test_planted.cpython-999-pytest-0.0.0.pyc"
    planted.write_bytes(b"stale")
    try:
        assert planted in conftest_mod._rewritten_bytecode(), (
            "the derivation behind the purge does not see a rewritten .pyc, so "
            "the purge runs over an empty set and can never remove one")
        conftest_mod.pytest_configure(None)
        assert not planted.exists(), (
            "pytest_configure left a rewritten .pyc in place, so a stale one "
            "from an earlier run or another harness is still read")
    finally:
        planted.unlink(missing_ok=True)


def test_the_fifo_teardown_wakes_a_reader_already_parked_in_open(tmp_path):
    """`make_fifo`'s teardown removes FIFOs "even when the test fails or
    PARKS".

    The unlink is real and it does stop a LATER walker parking — that is the
    hazard the fixture was built for. For a reader ALREADY blocked in `open()`
    it does nothing, and is arguably worse than nothing: on POSIX that process
    is waiting for a WRITER, and once the name is gone no writer can open it by
    path, so the unlink converts a recoverable park into a permanent one.

    The fixture's own TEARDOWN is what runs here, driven by stepping its
    generator, rather than `wake_fifo_readers` being called directly. That is
    not a detail: with the helper called directly, deleting the call FROM the
    teardown would leave this file green — the test would pin the helper and
    not its use.

    Two FIFOs, because the first half of this would pass over a reader that was
    never parked at all. One is left alone and its reader must STAY parked until
    teardown; the other is woken and its reader must RETURN. Nothing here reads
    a clock: every wait is a bounded `Event.wait`, and the negative control is
    sound in the slow direction — a machine too busy to park the reader yet
    leaves it un-returned, which is what that assertion requires.
    """
    teardown = _fixture_function(conftest_mod.make_fifo)
    assert teardown is not None, (
        "`make_fifo` is not reachable as a fixture function, so this test "
        "cannot drive the teardown it is about")
    lifecycle = teardown()
    make_fifo = next(lifecycle)

    def park(path: Path, returned: threading.Event) -> threading.Thread:
        def reader() -> None:
            try:
                with open(path, "rb") as handle:
                    handle.read()
            except OSError:
                pass
            returned.set()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        return thread

    left_alone, woken = make_fifo(tmp_path / "a"), make_fifo(tmp_path / "b")
    stayed, returned = threading.Event(), threading.Event()
    control = park(left_alone, stayed)
    subject = park(woken, returned)

    # THE NEGATIVE CONTROL, taken BEFORE teardown: neither reader has returned
    # on its own, so whatever happens next is attributable to the teardown.
    assert not returned.wait(0.2) and not stayed.is_set(), (
        "a reader on a FIFO nobody wrote to returned by itself, so nothing "
        "below measures a teardown")

    # THE TEARDOWN, the real one. Both readers must come back.
    next(lifecycle, None)
    assert returned.wait(5), (
        "a reader blocked in open() did not return after teardown ran — on "
        "POSIX the unlink does not wake it, because it is waiting for a WRITER "
        "and once the name is gone no writer can ever open it by path, so the "
        "unlink alone turns a recoverable park into a permanent one")
    assert stayed.wait(5), "the second parked reader was not reaped either"
    for thread in (subject, control):
        thread.join(5)
        assert not thread.is_alive()
    assert not woken.exists() and not left_alone.exists(), (
        "teardown left a FIFO in the tmp tree, which is the hazard the fixture "
        "was built for in the first place")

    # THE HALF UNLINK CANNOT DO, asserted on the mechanism rather than on a
    # second parked thread: once the name is gone, no writer can be attached BY
    # PATH. With the subject above — a parked reader returns exactly when a
    # writer attaches — that is why an unlink alone cannot reap: unlinking
    # first takes away the only thing that could have woken it.
    gone = tmp_path / "already-unlinked"
    os.mkfifo(gone)
    gone.unlink()
    assert conftest_mod.wake_fifo_readers(gone) is False, (
        "a writer could be attached to an unlinked FIFO by path — if that is "
        "now true on this platform, `make_fifo`'s docstring and this test both "
        "need rewriting, because the fixture is built on it not being")

    # …and `wake_fifo_readers` answers honestly when nobody is waiting, which
    # is what lets the teardown loop be unconditional.
    quiet = tmp_path / "nobody-waiting"
    os.mkfifo(quiet)
    assert conftest_mod.wake_fifo_readers(quiet) is False
    quiet.unlink()
