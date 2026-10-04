"""Review-round isolation, demonstrated rather than asserted.

Three other defences exist and none of them sees this bug:

- the `pre-pr-review` skill said "never a fixed name, and never the shared
  scratchpad root" (that history is now docs/design/pre-pr-review-rationale.md).
  Prose has no `exist_ok=False`.
- `verify_roster` proves each declared reviewer has a distinct,
  non-empty, non-symlink regular file. A foreign report AT THE EXPECTED PATH
  satisfies every one of those and gets attested.
- `range_binding` fires when the colliding branches' diffs are disjoint and is
  silent when they overlap — the dangerous case, and the normal one for two
  builders in one repo. `test_range_binding_is_silent_when_the_colliding_diffs_overlap` below turns
  that sentence from a comment into a fact that fails if it stops being true.

So the tests here are about a FILESYSTEM and, where the claim is about
concurrency, about real concurrent processes.

What they do not do is read a clock. Requiring two mint windows to
INTERSECT in wall-clock time, as proof that the concurrency is real, is a
proxy that flakes on contended runners. The properties a clock would stand in
for are asserted directly: a collision across eight real processes is refused
rather than reused, and a name taken inside the check-to-create window is
refused too. A test that has never failed proves nothing.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from warden import attest as attest_mod
from warden import runs as runs_mod

ROOT = Path(__file__).parent.parent

# The smallest repo.yaml that passes schema validation, so the
# CLI surface test exercises `round new` and not config loading.
from conftest import REPO_YAML  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    (r / ".warden").mkdir(parents=True)
    # `-b main`, never a bare `git init`. Without it the fixture inherits the
    # AMBIENT `init.defaultBranch`, so the base branch is `main` on a laptop
    # configured for it and `master` on a runner that is not — and every test
    # below that names `main` passes locally and fails in CI. `-b` overrides
    # the config, `defaultBranch=master` included, which is what makes this
    # fixture self-contained rather than borrowing a name from whoever ran it.
    # The sibling fixture in tests/test_autonomy_carve_out.py does the same.
    _git(r.parent, "init", "-q", "-b", "main", "r")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("one\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    # Asserted, not assumed. If a future git ever ignores `-b`, this fails at
    # SETUP naming the cause, instead of surfacing as an unrelated-looking
    # "cannot resolve the range" three tests later.
    on = _git(r, "symbolic-ref", "--short", "HEAD").strip()
    assert on == "main", (
        f"fixture base branch is {on!r}, not 'main' — `git init -b main` did "
        "not take, so every test naming `main` below is about to fail for a "
        "reason that has nothing to do with review rounds")
    return r


def _mint(repo, branch="feature", head="a" * 40, **kw):
    return runs_mod.create_round_dir(repo, branch=branch, head=head, **kw)


# ---------- isolation, demonstrated -----------------------------------------

_CHILD = """
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from warden import runs

repo, head, out, go = Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), Path(sys.argv[5])
# Every child parks here until the parent fires the starting gun, so the mints
# are launched together instead of being spread across a loop. Nothing below
# ASSERTS on when they landed — see the docstring of the test that follows.
# BOUNDED, for the reason tests/conftest.py::finishes_within states: a park
# with no bound turns a dead parent into a process spinning for the life of the
# runner, which reads as infrastructure flake rather than as this defect.
for _ in range(60000):
    if go.exists():
        break
    time.sleep(0.001)
else:
    sys.exit("starting gun " + str(go) + " never fired in 60s — the parent "
             "died before the race began, so this child minted nothing")
d = runs.create_round_dir(repo, branch="concurrent", head=head)
runs.write_round_manifest(d, base="main", base_sha="b" * 40, head=head,
                          branch="concurrent", round_no=1)
(d / "package.txt").write_text(head + chr(10))
out.write_text(json.dumps({"dir": str(d), "head": head, "pid": os.getpid()}))
"""


# Same shape, one difference that is the whole point: every racer is pinned to
# ONE derived name. Branch, head, clock and pid are all fixed by argument, so
# the twelve-way spread of `_CHILD` above collapses to a single path that all of
# them want. Which process wins is the scheduler's business. That exactly one
# does is not, and it is true whether the racers overlap by a microsecond or
# are serialised end to end — which is precisely why this needs no check on
# the clock.
_COLLIDER = """
import json, sys, time
sys.path.insert(0, sys.argv[1])
from datetime import datetime, timezone
from pathlib import Path
from warden import runs

repo, marker, out, go = Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), Path(sys.argv[5])
frozen = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
for _ in range(60000):           # bounded, for the reason _CHILD's park is
    if go.exists():
        break
    time.sleep(0.001)
else:
    sys.exit("starting gun " + str(go) + " never fired in 60s — the parent "
             "died before the race began, so this child minted nothing")
try:
    d = runs.create_round_dir(repo, branch="collide", head="c" * 40,
                              now=frozen, pid=4242)
except runs.RoundError as e:
    out.write_text(json.dumps({"minted": False, "error": str(e)}))
else:
    (d / "reviewers" / "code-reviewer.json").write_text(marker)
    out.write_text(json.dumps({"minted": True, "dir": str(d), "marker": marker}))
"""


# The builtins that turn DATA into CODE. A child program that spells one of
# these can run a clock reading the source scan never saw — `exec(sys.argv[3])`
# with the reading in argv reaches a real process past every source guard — so
# `_race` refuses them rather than trying to scan what they might be handed.
_CODE_FROM_DATA = frozenset({"exec", "eval", "compile"})


def _code_from_data(tree: ast.AST) -> list[tuple[int, str]]:
    """Every place `tree` names a builtin that turns data into code, as sorted
    (line, spelling).

    FIVE node shapes, and they match the NAME rather than the fetch: a bare
    `exec(...)`; an ATTRIBUTE `builtins.exec(...)`; the name as a STRING
    constant, which is how `__builtins__["exec"]` and
    `getattr(builtins, "exec")` both spell it; the same constant as BYTES,
    `getattr(builtins, b"exec".decode())`; and a FROM-IMPORT
    (`from builtins import exec as _e`), where the name lands in an
    `ast.alias` node that is none of the others.

    Each arm closes a spelling that names the builtin as plainly as the
    others, and each one, left out, lets a live clock reading walk into a
    real child through `_race`:

    - without the ATTRIBUTE arm, `builtins.exec(sys.argv[3])` walks a live
      `time.time()` in with the file green. `_subprocess_uses` carries the
      same arm for the same reason.
    - without the FROM-IMPORT arm, `from builtins import exec as _e` does.
      `_clock_reads` in this same file carries an `ImportFrom` arm for the
      same reason: a from-import binds what the module EXPORTS.
    - without BYTES in the constant arm,
      `getattr(builtins, b"exec".decode())(sys.argv[3])` does.
      `b"exec" in {"exec"}` is False, so a str-only membership test reads a
      verbatim spelling as innocent.

    STATED BOUND, in both directions.

    TOO NARROW. The arms read FOUR places a name can sit: a bare `ast.Name`'s
    `id`, an `ast.Attribute`'s `attr`, an `ast.Constant`'s `value` (str or
    bytes), and an `ast.ImportFrom` alias's IMPORTED name. Stated as the
    places, not as a property of "the tree": "some constant or identifier in
    the tree IS one of these names" is false in the fail-open direction.
    What is left over:

    - the name as an identifier somewhere no arm reads: an `as` name
      (`import builtins as exec`), a PARAMETER (`def f(exec): ...`), a
      `def exec(...)`, a `class exec`, a keyword argument (`f(exec=1)`).
      Those five are MEASURED clean and pinned below — the list is the ones
      tried, not a proof of the set, and a `global exec` or an
      `except E as exec` is the same shape untried. They are left unread
      rather than closed because none of them FETCHES the builtin: each
      binds or mentions the identifier, and calling the real one from there
      still goes through one of the four places above — or through the
      assembly residual below, which is open either way.
    - a name TRANSFORMED or assembled at runtime — `getattr(builtins,
      "ex" + "ec")`, `"ExEc".lower()`, `"exec ".strip()`, `vars(builtins)[k]`
      for a `k` built from data. Every constant there (`"ex"`, `"ExEc"`,
      `"exec "`) is a different string from the name, so no static read finds
      it. All three are open against these arms, deliberately.
    - a code LOADER that never spells one of these three — `importlib`,
      `__import__`, `runpy`, `types.FunctionType`, a module written to disk
      and imported.
    - a RE-EXPORT under a DIFFERENT name — `from mytools import run_it`
      where `run_it` is `exec`. (A re-export under the SAME name,
      `from mytools import exec`, IS caught: the import arm below matches the
      name from any module, unlike `_clock_reads`'s.)

    Those three are the residual `_subprocess_uses` states for its own
    module: closing one means adding a PRIMITIVE to the refusal, which is a
    review conversation rather than another name here.

    A STAR-import (`from builtins import *`) is deliberately NOT a fourth
    residual. It binds `exec` under its own name, so a program that USES it
    still spells the name and the bare-Name arm catches the call; and
    `_clock_reads` refuses every star-import from every module at the same
    launch path, so even a program that imports and never calls is refused
    before a process starts. `test_a_star_import_binds_a_name_that_still_
    has_to_be_spelled` executes both halves, because a reason nothing runs is
    the same prose this file opens by distrusting.

    TOO WIDE: it errs NOISY, and the import arm widens that. Any attribute,
    constant or imported name that happens to be one of these three is a hit
    even where it builds no code — `from re import compile` is a real
    collision and does fire, alongside the `re.compile(...)` the attribute
    arm catches. That is ACCEPTED rather than absent: a child
    program of this file has no reason to spell one of these at all, so the
    module is not consulted the way `_clock_reads` consults `_CLOCK_MODULES`
    for `from datetime import time`. The failure names the line and the
    spelling, which is what an author needs to rename around it.
    """
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _CODE_FROM_DATA:
            hits.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute) and node.attr in _CODE_FROM_DATA:
            hits.append((node.lineno, f".{node.attr}"))
        elif isinstance(node, ast.Constant):
            # BYTES as well as str. `b"exec"` spells the name in the tree as
            # plainly as `"exec"` does, and `b"exec" in _CODE_FROM_DATA` is
            # False against a frozenset of str — so a str-only test reads it
            # as innocent and `getattr(builtins, b"exec".decode())(argv)`
            # returns a live clock reading from a real process with this
            # file green.
            # Undecodable bytes cannot be one of these ASCII names, and are
            # not one after `replace` either.
            spelled = node.value
            prefix = ""
            if isinstance(spelled, bytes):
                spelled, prefix = spelled.decode("utf-8", "replace"), "b"
            if spelled in _CODE_FROM_DATA:
                hits.append((node.lineno, f'{prefix}"{spelled}"'))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _CODE_FROM_DATA:
                    # The imported NAME, never the `as` name: `from builtins
                    # import exec as _e` binds a builtin under a name that
                    # says nothing, so the import is the only place it can be
                    # caught — the same reason `_clock_reads`'s own
                    # ImportFrom arm matches `alias.name`.
                    #
                    # From ANY module, unlike that arm, which checks
                    # `_CLOCK_MODULES` because `from datetime import time`
                    # imports a class rather than a reader. The equivalent
                    # collision here (`from re import compile`) exists and is
                    # accepted under the NOISY bound above rather than
                    # narrowed around, so a re-export under the same name is
                    # caught too.
                    #
                    # `alias.lineno`, not the statement's: a parenthesised
                    # import puts each name on its own line, and the bound
                    # above promises the failure names the line an author has
                    # to go and edit. `_clock_reads`'s arm still reports the
                    # statement's line; this one is not made worse to match it.
                    module = f"{'.' * node.level}{node.module or ''}"
                    hits.append(
                        (alias.lineno, f"from {module} import {alias.name}"))
    return sorted(hits)


def _race(repo: Path, tmp_path: Path, source: str, per_child: list[str], *,
          tag: str) -> list[dict]:
    """Run `source` in one real OS process per entry of `per_child`, all parked
    on a single starting gun, and return what each one wrote.

    The gun is what launches them together rather than in a loop. Note what is
    NOT here: a clock. Both callers below assert on what ended up on the
    FILESYSTEM, which is the same whether the children overlapped or ran in
    single file, whereas a wall-clock overlap check flakes on a contended
    runner.

    Each child is handed `(repo_root, its own argument, its output file, the
    gun)` after the import root, so the two child scripts take the same argv
    shape and this launcher stays the only place that knows it.

    Every failure path names the CHILD, by the argument it was handed. Twelve
    processes that differ only in a head sha, or eight that differ only in a
    marker, are otherwise indistinguishable in a failure report — and the
    signal-kill case (`returncode` -9 on a runner that ran out of memory) has
    no stderr at all, so the argument is the only identity left to print.

    And the program is SCANNED here, before anything is written or spawned.
    Every program THIS function launches passes through here whatever name it
    was built under or called by — a module constant, a local string, an alias
    of this function, a `functools.partial` — so a clock reading is refused at
    the launch path itself rather than at a static derivation of where the
    launches are, which `_launched_programs` below has to recognise by
    spelling and cannot close. This is not the only place
    the file starts a Python process: the warden CLI runs from a declared
    site, and `_SUBPROCESS_SITES` is where that site and what it is trusted
    to launch are written down. The static guard still runs over every
    module-level string, so a program nothing launches is scanned too.

    STATED BOUND: what is scanned is the program's SOURCE. A program that
    turns data into code — `exec`/`eval`/`compile` of its argv, a file, an
    environment variable — could run a reading the scan never saw, so a child
    program that SPELLS one of those three names is refused, in the shape a
    star-import is refused by `_clock_reads`: nothing this file launches has
    a reason to build code at runtime. WHICH spellings that covers, and which
    it does not, is `_code_from_data`'s bound rather than a claim here —
    matching a bare name only would let `builtins.exec(sys.argv[3])` walk a
    live clock reading into a real child.
    """
    try:
        program = ast.parse(source)
    except SyntaxError as e:
        raise AssertionError(
            f"{tag}: the child program does not parse ({e.msg} at line "
            f"{e.lineno} of the program), so it cannot be scanned for a clock "
            "reading and could not have run either — refused before anything "
            "was written or spawned") from None
    reads = _clock_reads(program)
    assert not reads, (
        f"{tag}: refusing to launch a child program that reads a clock ("
        + ", ".join(f"line {line} {what}" for line, what in reads)
        + "). Every program _race launches is scanned at this launch path, so "
        "no alias, partial or local string handed to it as the PROGRAM gets a "
        "clock reading into a real process past test_no_verdict_in_this_"
        "file_is_derived_from_a_wall_clock; see its message for what to do "
        "instead")
    dynamic = _code_from_data(program)
    assert not dynamic, (
        f"{tag}: refusing to launch a child program that builds code from "
        "data (" + ", ".join(f"line {line} {what}" for line, what in dynamic)
        + "). A program that exec()s its argv or a file can run a clock "
        "reading this scan never saw, and nothing this file launches needs "
        "to build code at runtime; write the code in the program instead")
    script = tmp_path / f"{tag}.py"
    script.write_text(source)
    go = tmp_path / f"{tag}.go"
    started: list[tuple[str, Path, subprocess.Popen]] = []
    made = []
    try:
        try:
            for i, arg in enumerate(per_child):
                out = tmp_path / f"{tag}.out{i}.json"
                started.append((arg, out, subprocess.Popen(
                    [sys.executable, str(script), str(ROOT), str(repo), arg,
                     str(out), str(go)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)))
        finally:
            # The gun fires even if a Popen raised part-way through the field.
            # Every child already spawned is parked waiting for this file, so
            # without the `finally` a spawn that fails on child five strands
            # four processes on a gun nobody will ever fire. They are bounded
            # (see the park in the child), but sixty seconds of spinning on a
            # two-vCPU runner is a flake generator inside a flake fix.
            go.write_text("go")

        for arg, out, proc in started:
            try:
                _, err = proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                # communicate() does NOT kill on timeout, and the buffered
                # stderr it was draining is discarded with the exception — so
                # kill first, then drain, or the one artifact naming the cause
                # is thrown away at the moment it is needed.
                proc.kill()
                _, err = proc.communicate()
                raise AssertionError(
                    f"{tag} child {arg!r} did not finish inside 60s and was "
                    f"killed; its stderr was {err!r}") from None
            assert proc.returncode == 0, (
                f"{tag} child {arg!r} exited {proc.returncode} (a negative "
                f"code is the signal that killed it); its stderr was {err!r}")
            assert out.is_file(), (
                f"{tag} child {arg!r} exited 0 without writing {out.name}, so "
                "it reported nothing about what it did")
            made.append(json.loads(out.read_text()))
    finally:
        # Whatever went wrong above, nothing is left running behind it.
        for _, _, proc in started:
            if proc.poll() is None:
                proc.kill()
    return made


def test_two_concurrent_rounds_cannot_land_in_one_directory(repo, tmp_path):
    """THE ACCEPTANCE. Two builders in one session window must never land in
    one round directory; this launches twelve real mints together on a
    starting gun and shows every one of them got its own.

    REAL OS PROCESSES, started by hand, not a `ProcessPoolExecutor`:
    multiprocessing's `spawn` re-executes `sys.executable` and re-imports the
    test module through `runpy`, and under `uv run --python 3.14` that child
    can load a 3.12 stdlib beside a 3.14 one and die in `pkgutil`. A test that
    fails for a reason unrelated to its subject is nearly as useless as one
    that passes for a wrong reason, so the children are plain `subprocess`
    calls that import `warden.runs` and nothing else.

    The children park on a starting gun, so twelve real mints are launched
    together. What this test does NOT do is assert on the clock.

    Requiring at least two mint intervals to intersect in wall-clock time, as
    proof that the concurrency is real, is a flaky proxy — on py3.11 and
    py3.14 alike, so it is runner LOAD, never the interpreter. The reason is a
    margin no test controls: a mint takes well under a millisecond while the
    twelve launches stagger across tens of milliseconds, so pairwise
    intersection is a scheduling coin flip.

    How weighted that coin is is not a property of this code at all. The same
    assertion on one idle 18-core laptop gives a handful of intersecting pairs
    out of 66 under one harness and around twenty under another; on contended
    two-vCPU CI runners it comes out at ZERO — twelve windows inside 38 ms,
    each 0.25 to 2.4 ms wide, none of them touching. A proxy whose margin
    swings by an order of magnitude with the harness and hits zero on the
    machine that matters is not measuring the thing it is named for.

    The property such a check is a proxy FOR is real: two rounds deriving one
    name must collide and be refused, never silently share a directory. So
    that property is tested directly and without a clock, by two tests below —
    `test_a_cross_process_collision_is_refused_not_reused`, which points
    eight real processes at one derived name, and
    `test_a_name_taken_inside_the_window_is_still_refused`, which
    produces the check-to-create race rather than waiting for a scheduler to
    hand it over. Neither can be defeated by runner load. Everything this test
    asserts holds whether the twelve landed together or in single file:
    each got its OWN directory, each name carries its own head and pid, and
    each round's bytes are its own.

    And the assertion is not merely that the paths differ — it is that each
    round's manifest and package describe ITS OWN head, which is the thing that
    matters. A round whose bytes came from another branch is the bug,
    whatever its directory was called.
    """
    # Distinct in the FIRST 12 hex digits, which is the slice the derived name
    # carries. `f"{i:040x}"` varies only the LAST digits, so every child would
    # share a head[:12] of zeros and the name's head component would be
    # constant — the test could not notice it going missing.
    heads = [f"{i + 1:x}".rjust(12, "e") + "0" * 28 for i in range(12)]
    made = _race(repo, tmp_path, _CHILD, heads, tag="mint")

    dirs = [m["dir"] for m in made]
    assert len(set(dirs)) == len(dirs), (
        f"two of the twelve rounds shared a directory: {sorted(dirs)}")
    assert len({m["pid"] for m in made}) == len(made), (
        "the children were not distinct processes, so the pid component of the "
        "derived name was never exercised")
    # Read off the DIRECTORY NAME, not off os.getpid(). Asserting the pids
    # differ says nothing about the path: pids differ by definition, and with
    # the pid stripped from the derived name a pid-only check stays green while
    # isolation is broken. The claim in the message above is only true if the
    # name carries them.
    for m in made:
        name = Path(m["dir"]).name
        assert str(m["pid"]) in name, (
            f"round {name} does not carry its minting pid, so two processes "
            "deriving one name inside a microsecond would collide rather than "
            "be separated")
        assert m["head"][:12] in name, (
            f"round {name} does not carry its head, so a stray round from "
            "another commit is not visibly foreign")

    for m in made:
        round_dir = Path(m["dir"])
        doc = json.loads((round_dir / "round.json").read_text())
        assert doc["head_sha"] == m["head"], (
            f"{round_dir} carries a manifest for {doc['head_sha']}, not the "
            f"{m['head']} it was minted for — the directories are distinct but "
            "their CONTENTS crossed over, which is the collision bug exactly")
        assert (round_dir / "package.txt").read_text().strip() == m["head"], (
            f"{round_dir}'s package describes another round's head")
        assert not any((round_dir / "reviewers").iterdir()), (
            f"{round_dir}/reviewers is not empty at mint")


def test_a_cross_process_collision_is_refused_not_reused(repo, tmp_path):
    """THE CONCURRENCY ACCEPTANCE, with no clock in it.

    Eight real OS processes, all parked on one starting gun, all pinned to the
    SAME derived name — same branch, same head, same frozen microsecond, same
    pid argument. So they do not merely run at the same time; they all want one
    directory, which is the case round isolation exists for.

    `mkdir(exist_ok=False)` is a single atomic syscall, so the answer does not
    depend on how the eight were scheduled: exactly one creates the directory
    and the other seven are refused by name. That is what makes this test
    load-proof where a wall-clock overlap check is not — a contended runner
    can serialise these eight end to end and every assertion below still
    holds, because none of them asks WHEN anything happened.

    Be precise about what that buys, because the temptation is to claim more.
    Since serialised racers satisfy every assertion here, so does a
    check-then-create implementation — caught in only a small and unrepeatable
    fraction of runs, which is the same coin flip in a different costume. This
    test proves the refusal holds across real PROCESS boundaries: the error
    text a losing process actually receives, and that no second round
    directory lands. It does NOT prove the claim is atomic.
    `test_a_name_taken_inside_the_window_is_still_refused` below is what
    proves that, by producing the race instead of hoping for it.

    The in-process sibling (`test_a_collision_on_the_derived_name_is_
    refused_not_reused`, below) makes the same claim in ONE process, where the
    collision is arranged rather than raced. It pins the failure to
    `create_round_dir`'s own logic; this one adds that the refusal is what a
    separate process is really told, across a real fork and a real filesystem.
    """
    racers = [f"racer-{i}" for i in range(8)]
    made = _race(repo, tmp_path, _COLLIDER, racers, tag="collide")

    winners = [m for m in made if m["minted"]]
    assert len(winners) == 1, (
        f"{len(winners)} of {len(made)} processes deriving ONE round name each "
        "believed they had minted it — a collision was reused instead of "
        f"refused, which is the collision bug exactly: {made}")

    for m in made:
        if m["minted"]:
            continue
        assert "already exists" in m["error"], (
            "a losing racer was refused for some other reason than the "
            f"collision, so this test is not exercising the stop: {m['error']}")
        assert "mint a fresh one" in m["error"], (
            "the refusal does not tell the loser what to do: re-run to mint "
            f"a fresh round: {m['error']}")

    winner = Path(winners[0]["dir"])
    rounds_root = repo / ".warden" / "out" / "rounds"
    # DIRECTORIES, not entries: the claim is "one round landed", and warden
    # already writes convenience pointers beside run dirs elsewhere (the
    # `latest` symlink in create_run_dir). Asserting on every entry would fail
    # a future sibling FILE for a reason that has nothing to do with isolation.
    #
    # WHAT THIS FILTER DOES NOT COVER. It does not exclude a symlink such as
    # `latest`: `Path.is_dir()` FOLLOWS symlinks, so a symlink pointing at a
    # round directory passes this filter and lands in `landed`, on 3.11
    # through 3.14. The predicate that would exclude one is
    # `d.is_dir() and not d.is_symlink()`, and whoever writes the first sibling
    # pointer into this directory needs it. Not reachable today: nothing writes
    # a sibling into `.warden/out/rounds/` at all.
    landed = sorted(d.name for d in rounds_root.iterdir() if d.is_dir())
    assert landed == [winner.name], (
        "eight racers deriving one name left more than one round directory "
        f"behind: {landed}")

    # The winner's round holds what the winner put there and NOTHING else. A
    # refusal that drops a lock file, a partial manifest or a clobbered report
    # into the directory it is refusing is not a refusal — it is a write with
    # an error message after it, and this is the assertion that says so.
    assert sorted(p.name for p in winner.iterdir()) == ["reviewers"], (
        "a refused racer left something behind in the winner's round: "
        f"{sorted(p.name for p in winner.iterdir())}")
    assert sorted(p.name for p in (winner / "reviewers").iterdir()) == [
        "code-reviewer.json"], (
        "a refused racer left a report in the winner's reviewers dir: "
        f"{sorted(p.name for p in (winner / 'reviewers').iterdir())}")
    assert (winner / "reviewers" / "code-reviewer.json").read_text() == (
        winners[0]["marker"]), (
        "the winner's own report was overwritten by another racer's bytes")


def test_a_name_taken_inside_the_window_is_still_refused(repo, monkeypatch):
    """THE ATOMICITY, made trippable — the guard racing cannot reach.

    Every refusal in this file rests on one property: the mint CLAIMS the name
    by creating it, and never first LOOKS to see whether it is free. Swap
    `mkdir(exist_ok=False)` for a look followed by `mkdir(exist_ok=True)` and
    the isolation is gone — two processes can pass the look together and both
    proceed — yet every other test in this file goes on passing, the eight-way
    race above included: a check-then-create build goes green against this
    file in most runs.

    It cannot be caught by racing. Two processes have to land inside a window
    a few microseconds wide, and a window a THOUSAND times wider fails to
    intersect on a loaded runner. Waiting for the scheduler to hand over this
    window is the same bet, at worse odds.

    So the window is MANUFACTURED, and the way it is manufactured is the point.
    A rival takes the name inside the mint's own `os.mkdir` call — after any
    look the mint might have made, before its create lands. Nothing is told a
    lie: the name really is free when the mint looks and really is taken when
    it creates, which is exactly the interleaving a loaded runner produces once
    in a very long while.

    The alternative, blinding a lookup, does not hold. Patching `Path.exists`
    catches a mint spelled `round_dir.exists()` and MISSES the same defect
    spelled `round_dir.is_dir()` or `os.path.exists(...)` — the file passes
    on a genuinely non-atomic build. Patching the stat syscall underneath is
    worse, not better: `mkdir(exist_ok=True)` re-checks `is_dir()` itself,
    so a blinded stat makes a broken build refuse for the WRONG reason and
    this test reports a guard that is not there. A rival privileges no
    spelling.

    The rival's directory is left EMPTY on purpose, and the reason is narrow.
    Leave something in it and a check-then-create build IS still caught — the
    test fails on `assert "already exists" in str(e.value)` against a
    RoundError naming the leftover file. So the refusal would come from the
    wrong guard (the not-empty backstop, not the atomic create), but the test
    would NOT go green.

    What the empty rival actually buys is robustness to that message assertion
    being weakened later: with an empty rival, a check-then-create build raises
    nothing at all, so this test trips on the REFUSAL ITSELF rather than on
    which RoundError text came back.
    """
    frozen = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Mint once to learn the name the next mint will derive, then give it back.
    # Deriving it here instead would copy the naming rule into the test, and a
    # test that re-implements what it checks agrees with itself forever.
    probe = _mint(repo, now=frozen, pid=4242)
    target = Path(probe)
    (probe / "reviewers").rmdir()
    probe.rmdir()

    real_mkdir = os.mkdir
    taken = []

    def rival(path, *args, **kwargs):
        if Path(path) == target and not taken:
            taken.append(real_mkdir(path, *args, **kwargs))   # the rival wins
        return real_mkdir(path, *args, **kwargs)              # the mint's own

    monkeypatch.setattr(os, "mkdir", rival)

    with pytest.raises(runs_mod.RoundError) as e:
        _mint(repo, now=frozen, pid=4242)
    assert taken, (
        "the rival never fired, so this test proved nothing — the mint no "
        f"longer derives {target.name}, or no longer creates it with os.mkdir")
    assert "already exists" in str(e.value), (
        "a round whose name was taken inside the look-to-create window was "
        f"refused for some other reason, or not at all: {e.value}")
    assert not any(target.iterdir()), (
        "the mint adopted the directory a rival had taken — a round that "
        "starts on someone else's name is the collision bug, whatever it then "
        f"says: {sorted(p.name for p in target.iterdir())}")


def test_a_collision_on_the_derived_name_is_refused_not_reused(repo):
    """The stop. With the clock and the pid pinned, two rounds derive ONE name
    — and the second is refused with a named error instead of quietly writing
    into the first one's directory.

    This is the dangerous case, reduced to something a test can actually
    produce: the first round's bytes must still be its own
    afterwards. A collision is a stop, not a silent read.
    """
    frozen = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    first = _mint(repo, now=frozen, pid=4242)
    (first / "reviewers" / "code-reviewer.json").write_text('{"mine": true}')

    with pytest.raises(runs_mod.RoundError) as e:
        _mint(repo, now=frozen, pid=4242)
    assert "already exists" in str(e.value)
    assert "mint a fresh one" in str(e.value), (
        "the refusal does not tell the caller what to do: re-run to mint a "
        "fresh round")

    assert json.loads(
        (first / "reviewers" / "code-reviewer.json").read_text()) == {
            "mine": True}, (
        "the refused second round overwrote the first round's report — the "
        "refusal has to happen BEFORE anything is written")


def test_a_round_that_is_not_empty_at_creation_is_refused(repo, monkeypatch):
    """Assert-empty-at-creation, made observable. `mkdir(exist_ok=False)` is
    what makes the directory new, so this is belt-and-braces — but a guard
    nothing can trip is a guard nobody can trust, so the mkdir is replaced by
    one that leaves a foreign file behind and the refusal is watched to fire.

    The scenario it stands for is the real one: a round starting on top of
    somebody else's reviewer output, which is what "foreign artifacts in my
    own round directory" was.
    """
    real = Path.mkdir

    def leaky(self, *a, **kw):
        real(self, *a, **kw)
        if self.parent.name == "rounds":
            (self / "code-reviewer.json").write_text("someone else's round")

    monkeypatch.setattr(Path, "mkdir", leaky)
    with pytest.raises(runs_mod.RoundError) as e:
        _mint(repo)
    assert "not empty" in str(e.value)
    assert "code-reviewer.json" in str(e.value), (
        "the refusal does not name what it found, so a caller cannot tell a "
        "stale round from a foreign one")


def test_the_round_name_carries_the_identity_it_claims_to(repo):
    """The name is the audit surface: a stray directory has to be readably
    foreign. Branch and head must both survive into it, and a branch name with
    slashes must not escape the rounds root."""
    d = _mint(repo, branch="agent/worktree-01", head="deadbeefcafe" + "0" * 28)
    assert d.parent.name == "rounds"
    assert "agent-worktree-01" in d.name
    assert "deadbeefcafe" in d.name
    assert str(os.getpid()) in d.name
    assert d.parent.parent.name == "out"


def test_a_rounds_root_that_is_not_a_directory_is_refused(repo):
    """Fail-closed on the container. If `.warden/out/rounds` is a file, there
    is nowhere to isolate a round — and writing the package somewhere else
    would put review evidence where no later reader looks for it."""
    (repo / ".warden" / "out").mkdir(parents=True)
    (repo / ".warden" / "out" / "rounds").write_text("not a directory")
    with pytest.raises(runs_mod.RoundError) as e:
        _mint(repo)
    assert "is not a directory" in str(e.value)


def test_a_mint_that_cannot_create_its_directory_is_refused_not_returned(
        repo):
    """The OSError arm, made undeletable.

    `create_round_dir` has two failure arms on the same `mkdir`, and both are
    pinned: replacing `raise RoundError(...)` with `return round_dir` in
    EITHER arm must turn a test red. An arm no test can tell from a silent
    pass is deletable in silence, which is the class `tests-bite` names and
    this file's own subject.

    The arm matters in the fail-closed direction: it is what carries a
    permission error, a read-only filesystem or an ENAMETOOLONG into a STOP.
    Without it a mint that created nothing hands the caller a path anyway, and
    the round's reviewers write into a directory that does not exist — or,
    once someone makes it, into one nothing minted.

    TWO real errnos, both unconditional, because one is not enough: with only
    the ENOTDIR case running as the unconditional pin, narrowing the clause
    to `except NotADirectoryError` leaves the file green wherever the EACCES
    sibling below skips — which is every root runner. ELOOP arrives as a BARE
    `OSError`, so only `except OSError` catches it, and no chmod and no uid
    are involved.

    Neither case reaches the rounds-root guard above: `Path.exists()` answers
    False rather than raising for a path under a file and for a symlink loop,
    so both walk straight into the mkdir the arm wraps.
    """
    # ENOTDIR: `.warden/out` is a FILE, so `.warden/out/rounds/<name>` cannot
    # be made. `mkdir(parents=True)` only recurses on FileNotFoundError, so
    # this lands in the arm rather than in pathlib's parent-creation path.
    (repo / ".warden" / "out").write_text("a file where the out dir goes\n")
    _assert_failed_mint_is_refused(repo, NotADirectoryError)

    # ELOOP: the rounds root is a symlink to itself. A bare OSError, which is
    # what defeats a narrowing of the clause to any one subclass.
    (repo / ".warden" / "out").unlink()
    (repo / ".warden" / "out").mkdir()
    (repo / ".warden" / "out" / "rounds").symlink_to("rounds")
    _assert_failed_mint_is_refused(repo, OSError)


def _assert_failed_mint_is_refused(repo, expected_cause):
    """One failed mint, refused by name — the shared body of the cases above.

    `expected_cause` is the exact type the mkdir must have raised, compared
    with `type(...) is`, not `isinstance`: the ELOOP case's whole point is
    that the cause is a BARE `OSError`, and `isinstance` would let every
    subclass satisfy it and the case stop distinguishing anything.
    """
    with pytest.raises(runs_mod.RoundError) as e:
        _mint(repo, branch="feature", head="a" * 40)

    msg = str(e.value)
    cause = e.value.__cause__
    # `type(...) is`, and this ONE assertion is what separates the two arms.
    # Asking the same question again — `not isinstance(cause,
    # FileExistsError)` or `"already exists" not in msg` — would add checks
    # the exact-type check already makes unreachable, and assertions that
    # cannot fail are what this file is about.
    assert type(cause) is expected_cause, (
        f"the refusal was raised from {cause!r}, not the "
        f"{expected_cause.__name__} the mkdir raised — so the mint did not "
        "fail the way this case drives it, and the arm under test was never "
        "entered. A FileExistsError here means the COLLISION arm ran, which "
        "test_a_collision_on_the_derived_name_is_refused_not_reused "
        "already pins and this case is not for")
    assert "could not be created" in msg, (
        f"the refusal reads {msg!r}, which is not the failed-mint arm")
    assert str(cause) in msg, (
        f"the refusal does not carry the reason the mkdir failed ({cause}), "
        "so a caller reading it cannot tell a read-only filesystem from a "
        "permission denial from a name the filesystem refused")

    # The message's OWN words, not the errno it interpolates. Checked against
    # the whole message, `"rounds" in msg` is satisfied by the path inside
    # `({e})` alone: dropping `{round_dir}` from the message entirely, or
    # naming the rounds ROOT instead of the round it failed to mint, would
    # both leave the file green. The branch slug
    # and head prefix appear in the derived NAME and nowhere in the parent.
    named = msg.split("(", 1)[0]
    assert "feature" in named and "aaaaaaaaaaaa" in named, (
        f"the refusal's own words are {named!r}, which do not name the round "
        "directory it could not mint — only the interpolated errno does, so a "
        "message naming the wrong path, or no path, would read the same")
    assert "nowhere isolated to write" in msg, (
        f"the refusal {msg!r} does not say what a failed mint costs the round")


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="running as root: an unwritable directory is still writable")
def test_a_rounds_root_the_process_cannot_write_into_is_refused(repo):
    """The same arm, on EACCES.

    EACCES — a rounds root the process cannot write into — is the shape "a
    permission error, a read-only filesystem" actually takes on a runner. It
    cannot be driven as root, so it is `skipif`-ed; the two unconditional
    cases in the sibling above are what pin the arm and its breadth where
    this one does not run. That division is deliberate: with only ENOTDIR
    unconditional, `except NotADirectoryError` survives wherever this test
    skips.
    """
    rounds_root = repo / ".warden" / "out" / "rounds"
    rounds_root.mkdir(parents=True)
    rounds_root.chmod(0o500)          # readable and listable, NOT writable
    try:
        with pytest.raises(runs_mod.RoundError) as e:
            _mint(repo)
    finally:
        rounds_root.chmod(0o700)      # so tmp_path teardown can remove it
    assert isinstance(e.value.__cause__, PermissionError), (
        f"raised from {e.value.__cause__!r}, not the EACCES the mkdir hit")
    assert "could not be created" in str(e.value)


# ---------- the binding, and why the old backstops could not do it ----------

def test_attest_refuses_a_round_minted_for_another_head(repo):
    """Defence in depth, on the dangerous shape: the round describes
    a different commit and its BASE MATCHES, so nothing else fires."""
    d = _mint(repo, head="f" * 40)
    runs_mod.write_round_manifest(d, base="main", base_sha="b" * 40,
                                  head="f" * 40, branch="other", round_no=1)
    with pytest.raises(attest_mod.AttestError) as e:
        attest_mod.round_binding(d / "reviewers", "a" * 40)
    msg = str(e.value)
    assert "ffffffffffff" in msg and "aaaaaaaaaaaa" in msg, (
        "the refusal does not name both commits, so the reader cannot tell "
        "which artifact is the foreign one")


def test_attest_binds_a_round_minted_for_this_head(repo):
    d = _mint(repo, head="a" * 40)
    runs_mod.write_round_manifest(d, base="main", base_sha="b" * 40,
                                  head="a" * 40, branch="mine", round_no=1)
    state, _ = attest_mod.round_binding(d / "reviewers", "a" * 40)
    assert state == "bound"


def test_an_unminted_review_dir_is_recorded_never_silently_passed(repo):
    """A directory warden did not mint has no identity to check. That is a
    STATE the caller records and prints, not a pass — the same shape as
    `unverified-roster`, and for the same reason: "could not look" must never
    render as "looked and it was fine"."""
    plain = repo / "somewhere"
    plain.mkdir()
    state, detail = attest_mod.round_binding(plain, "a" * 40)
    assert state == "unminted"
    assert "warden round new" in detail


def test_an_unreadable_round_manifest_is_refused_not_ignored(repo):
    """Fail-closed. A round.json that cannot be parsed is not a round that
    has none — treating it as absent is how a check that cannot evaluate
    starts reading like a pass."""
    d = _mint(repo)
    (d / "round.json").write_text("{ not json")
    with pytest.raises(attest_mod.AttestError):
        attest_mod.round_binding(d / "reviewers", "a" * 40)


def test_a_round_manifest_with_no_head_is_refused_by_name(repo):
    """Refused for the RIGHT REASON, which is the whole of what this guard
    buys. Deleting the head_sha check leaves the call still raising — the
    empty string simply fails the comparison below — so a test asserting
    only `raises` cannot see the guard go. What it
    costs is the message: "names no head_sha" tells the reader the round is
    malformed, where "minted for head , but this attestation stamps a1b2c3"
    sends them hunting for a commit that was never named.
    """
    d = _mint(repo)
    (d / "round.json").write_text(json.dumps({"schema": 1, "branch": "x"}))
    with pytest.raises(attest_mod.AttestError) as e:
        attest_mod.round_binding(d / "reviewers", "a" * 40)
    assert "names no head_sha" in str(e.value), str(e.value)


def test_a_round_manifest_that_is_not_an_object_is_refused(repo):
    """`json.loads` returns whatever the file holds. A list would reach
    `.get` and raise AttributeError — a crash escaping instead of the check
    reporting failure, which is the `wrong exit direction` shape of this
    repo's fail-closed rule."""
    d = _mint(repo)
    (d / "round.json").write_text("[1, 2, 3]")
    with pytest.raises(attest_mod.AttestError) as e:
        attest_mod.round_binding(d / "reviewers", "a" * 40)
    assert "names no head_sha" in str(e.value), str(e.value)


def test_range_binding_is_silent_when_the_colliding_diffs_overlap():
    """The reason round isolation cannot be replaced by a range check, as an
    executable fact rather than a comment.

    Two builders both touching `warden/cli.py` produce a findings set that
    `range_binding` accepts while it describes the WRONG PR. If this ever
    starts failing, range binding grew teeth and this file's framing — that
    isolation is the mechanism and binding is the backstop — needs revisiting.
    """
    foreign = [{"file": "warden/cli.py", "line": 10, "rule_id": "x",
                "severity": "MEDIUM", "note": "from another branch"}]
    kind, warning = attest_mod.range_binding(
        foreign, ("warden/cli.py", "tests/test_cli.py"), "b" * 40, "a" * 40)
    assert not warning, (
        "range_binding now warns on an overlapping foreign findings set — if "
        "that is intended, the round-isolation premise has changed")
    assert kind == "ok", kind


# ---------- the CLI surface the skill actually calls -------------------------

# The one child program this file runs that is not a `_race` child: the warden
# CLI, in a real interpreter. MODULE-LEVEL, and that is a rule rather than
# tidiness — every module-level string here is scanned for a clock reading
# by `_shipped_programs`, and a program held in a local at the call site would
# be a child no scan reads.
_CLI_ROUND_NEW = ("import sys; from warden import cli; "
                  "sys.exit(cli.main(['round', 'new', '--base', 'main']))")


def test_round_new_prints_only_the_path_on_stdout(repo):
    """`ROUND="$(warden round new --base main)"` is the whole point: a caller
    that never types a path cannot type a colliding one. So stdout is the path
    and nothing else — a stray word here lands inside someone's directory
    name."""
    (repo / "repo.yaml").write_text(REPO_YAML)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "repo.yaml")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "a.txt").write_text("two\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")

    out = subprocess.run(
        [sys.executable, "-c", _CLI_ROUND_NEW],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert out.returncode == 0, out.stderr
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout is not just the path: {out.stdout!r}"
    minted = Path(lines[0])
    assert minted.is_dir()
    assert (minted / "review-package.md").is_file()
    assert (minted / "round.json").is_file()
    assert minted.parent.name == "rounds"
    # and the human-readable half went to stderr, where it cannot be captured
    # into the variable
    assert "minted" in out.stderr


def test_a_round_manifest_that_cannot_be_examined_is_refused(repo):
    """Named regression: a round manifest that cannot be examined fails closed.

    A lookup through `Path.is_file()` swallows OSError and answers False. So a
    `round.json` warden cannot LOOK at — a dangling symlink, a symlink loop, a
    directory wearing the name — would read as one that is not THERE, and
    `round_binding` would downgrade to `unminted` at exit 0 with an
    attestation written. That is the precise inversion the function's
    docstring forbids: "could not look" rendering as "nothing to see".
    """
    for name, make in (
        ("dangling symlink", lambda p: p.symlink_to("nowhere-at-all")),
        ("directory", lambda p: p.mkdir()),
    ):
        d = _mint(repo, head="a" * 40)
        (d / "round.json").unlink(missing_ok=True)
        make(d / "round.json")
        with pytest.raises(attest_mod.AttestError) as e:
            attest_mod.round_binding(d / "reviewers", "a" * 40)
        assert "unminted" not in str(e.value), (
            f"a {name} at round.json was treated as an absent manifest")


def test_the_package_describes_the_head_the_round_is_minted_for(repo, monkeypatch):
    """Named regression: `round new` resolves `--head` exactly once.

    Resolved TWICE — once into `round.json` and the directory name, and again
    inside `review_package` — a commit landing between the two binds the
    round to sha A while the package it holds describes sha B: a round whose
    manifest and bytes disagree, the same shape as a collision.

    Asserting the two agree cannot catch it (absent a real race they agree
    anyway), so this asserts the PROPERTY that makes the race impossible: the
    package builder is handed an already-resolved 40-hex sha, never the
    symbolic ref the caller typed.
    """
    from warden import cli as cli_mod
    from warden import diffs as diffs_mod

    (repo / "repo.yaml").write_text(REPO_YAML)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "repo.yaml")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "a.txt").write_text("two\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")

    seen = {}
    real = diffs_mod.review_package

    def spy(root, base, head):
        seen["head"] = head
        seen["base"] = base
        return real(root, base, head)

    monkeypatch.setattr(diffs_mod, "review_package", spy)
    monkeypatch.chdir(repo)
    assert cli_mod.main(["round", "new", "--base", "main"]) == 0

    def _is_sha(v):
        return len(v) == 40 and all(c in "0123456789abcdef" for c in v)

    assert _is_sha(seen["head"]), (
        f"review_package was handed {seen['head']!r}, a symbolic ref it "
        "re-resolves — so the package and round.json can describe different "
        "commits")
    # BOTH ends of the range. `--base` is resolved once for the same reason,
    # and without this assertion nothing in the suite guards it. A ref that
    # moves between the
    # manifest and the package makes them describe different RANGES, which is
    # the head defect one argument over.
    assert _is_sha(seen["base"]), (
        f"review_package was handed {seen['base']!r} as its base, a symbolic "
        "ref it re-resolves — so round.json's base_sha and the package can "
        "describe different ranges")


# ---------- and the clock stays out ------------------------------------------

# Names that RETURN a clock reading. `_clock_reads` below matches these as
# IDENTIFIERS, never as text, so the spelling that reaches one does not matter:
# `time.time()`, `import time as clk; clk.time()`, `from time import time` and
# `p.stat().st_mtime` are one entry here, not four patterns to keep in step.
#
# `sleep` is deliberately absent, with every other name that only WAITS. The
# children park on the starting gun, and waiting is not measuring. What has to
# stay out is the READING, because that is the only way a verdict in this file
# can come to depend on how a scheduler felt on the day.
#
# Two policies, because one alone cannot serve both kinds of name.
#
# Where a name has NO ordinary non-clock receiver — `gmtime`, `localtime`,
# `asctime`, `times` — the set errs NOISY and matches wherever the name
# appears, which costs nothing this file wants to do. Three of them read the
# clock when handed nothing and convert when handed something; `os.times()`
# takes no argument at all and only ever reads.
#
# Where a name IS an ordinary method on something else — `ctime`, `strftime`,
# `time`, all three of which datetime objects carry — noisy is the wrong
# setting, and `_TIME_MODULE_ONLY` below restricts them to the `time` module.
# Converting a value that already exists is explicitly ALLOWED here, and
# `_SILENT_PROGRAMS` asserts it as a positive requirement rather than leaving
# it to this comment: `frozen.strftime(fmt)`, `frozen.ctime()` and
# `frozen.time()` must not fire, or the guard fails the suite for taking the
# advice its own failure message gives.
#
# Both policies bite on real spellings: without `ctime` in the set,
# `time.ctime()` planted inside `_CHILD` ships clean; with noisy applied to
# `ctime`, `strftime` or `time`, the frozen-constant conversion fails.
# `_clock_reads`'s bound says the rest: a name set is an open enumeration, and
# the only honest thing to do with one is say so and keep correcting it.
_CLOCK_READERS = frozenset({
    # time
    "time", "time_ns", "monotonic", "monotonic_ns", "perf_counter",
    "perf_counter_ns", "process_time", "process_time_ns", "thread_time",
    "thread_time_ns", "clock_gettime", "clock_gettime_ns", "gmtime",
    "localtime", "ctime", "asctime", "strftime",
    # datetime / date. `fromtimestamp` and `timestamp` are deliberately NOT
    # here: they CONSUME an epoch value that already exists, so they add no
    # detection power — whatever read the clock to produce that value is caught
    # on its own — while failing the deterministic conversion of a FROZEN
    # constant, which is the repair this guard's own message prescribes.
    "now", "utcnow", "today",
    # timeit, os, os.path
    "default_timer", "times", "getmtime", "getctime", "getatime",
    # The FILESYSTEM's clock, which is the one a text scan for `time.` cannot
    # see at all. Sorting minted round dirs by mtime to decide which is newest
    # is the most natural clock dependency there is for a file whose subject is
    # minting round dirs concurrently, and this repo's suite already derives a
    # verdict that way in tests/test_cage_runner.py.
    # `st_birthtime` is here because this repo develops on darwin, where it is
    # a live field rather than the AttributeError it is on most Linux.
    "st_atime", "st_ctime", "st_mtime", "st_birthtime",
    "st_atime_ns", "st_ctime_ns", "st_mtime_ns", "st_birthtime_ns",
})

# The names above that read a clock ONLY when they hang off the `time` module.
# Each is also a method on a datetime object — `date` carries `.ctime()` and
# `.strftime()` but not `.time()`, so `datetime` is the receiver all three
# share — where it converts a value that already exists and reads nothing:
# `time.strftime(fmt)` reads the clock, `frozen.strftime(fmt)` renders a frozen
# constant, and `frozen.ctime()` / `frozen.time()` likewise. Matched
# receiver-blind, the way every other name here is matched, they would forbid
# the deterministic conversion of exactly the frozen constant `_COLLIDER`
# already builds, and warden/runs.py's own `when.strftime(...)` — the same
# false positive that keeps `timestamp` out of the set above, so matching them
# receiver-blind would be the set contradicting itself.
#
# `time` is here for the same reason: `datetime.time()` returns the
# time-of-day COMPONENT of a value that already exists, so matched
# receiver-blind `frozen.time()` would fire on the very frozen constant whose
# `.strftime` and `.ctime` are let through — one receiver, three methods, and
# all three handled alike.
#
# Nothing is lost PROVIDED the receiver check resolves every way the module can
# be named: reading `import` statements only would let `clock = time` then
# `clock.time()` — a real wall-clock read, and the commonest one in the
# language — through. `_clock_reads` resolves plain assignment to a fixed
# point, and `_CAUGHT_PROGRAMS` plants both that and a chain.
#
# `asctime` is NOT here: it exists only on the `time` module, so matching it
# receiver-blind costs nothing.
_TIME_MODULE_ONLY = frozenset({"ctime", "strftime", "time"})

# Modules whose EXPORTS read a clock, for the from-import arm. `datetime` is
# absent deliberately: what it exports are CLASSES, so `from datetime import
# time` imports the time-of-day class and reads nothing, while the readers it
# does have are ATTRIBUTES on those classes (`datetime.now`) that the attribute
# arm already sees. Ignoring the module would make that import a hit — the
# mirror of the attribute arm's deliberate receiver-blindness, and wrong for
# the same reason.
#
# Every entry here exports a name `_CLOCK_READERS` declares, and each is
# exercised by a from-import plant in the control. `resource` is not here: no
# name in `_CLOCK_READERS` comes from it, so it would be inert config no
# mutation could reach — and a set entry nothing exercises is the same silence
# this whole file is about.
_CLOCK_MODULES = frozenset({"time", "os", "os.path", "timeit"})


def _is_clock_module(node: ast.AST, time_names: set) -> bool:
    """Is `node`, used as a receiver, the time module itself?

    THREE spellings, not one. Reading only a bare `ast.Name` bound to the
    module lets `__import__("time").time()` and `sys.modules["time"].time()` —
    the receiver is a Call and a Subscript, not a Name — return a live reading
    from a real process through `_race` with this file GREEN. `monotonic` is
    matched receiver-blind and is caught either way, so that hole is exactly
    the width of the `_TIME_MODULE_ONLY` narrowing.

    Firing on EVERY non-Name receiver is the obvious repair and is the one this
    does not take: it fires on `datetime(2026, 9, 1).strftime(fmt)` and on
    `d.date().strftime(fmt)`, which convert a value that already exists — the
    false positive `_TIME_MODULE_ONLY` exists to avoid. So the two
    MODULE-FETCH shapes are named instead, and `_SILENT_PROGRAMS` keeps the
    frozen-conversion plants that say so.

    STATED BOUND: a fetch spelled any other way — `importlib.import_module`
    under an alias, `vars(sys.modules)["time"]`, a module handed in as a
    parameter — is still unread, and so is a receiver that is an arbitrary
    expression. That is the same residual the seed's TOO NARROW bound carries,
    and it is a narrower hole than firing on every conversion would be.
    """
    if isinstance(node, ast.Name):
        return node.id in time_names
    if isinstance(node, ast.NamedExpr):
        # `(clk := time).time()` — the receiver is the walrus EXPRESSION, whose
        # value is the module. The binding it also makes is what `rebinds` sees;
        # this is the immediate receiver, and they are two different reads.
        return _is_clock_module(node.value, time_names)
    if isinstance(node, ast.Call):
        # `__import__("time")` / `importlib.import_module("time")`
        target = node.func
        name = (target.id if isinstance(target, ast.Name)
                else target.attr if isinstance(target, ast.Attribute) else "")
        if name in ("__import__", "import_module") and node.args:
            first = node.args[0]
            return (isinstance(first, ast.Constant)
                    and first.value in _CLOCK_MODULES)
        return False
    if isinstance(node, ast.Subscript):
        # `sys.modules["time"]`
        holder = node.value
        if not (isinstance(holder, ast.Attribute) and holder.attr == "modules"):
            return False
        key = node.slice
        return isinstance(key, ast.Constant) and key.value in _CLOCK_MODULES
    return False


def _clock_reads(tree: ast.AST) -> list[tuple[int, str]]:
    """Every place `tree` obtains a clock reading, as sorted (line, spelling).

    Factored out of the tests below, and that is the house pattern rather than
    tidiness: a detector inlined in the test it serves can never be shown to
    FIRE, only to stay quiet, and `test_the_clock_scan_is_proved_to_bite_
    before_it_reports_clean` is what pins the other half — the shape
    tests/test_ambient_git.py uses for `_ambient_key_writes`.

    Two node shapes, which between them cover every spelling the controls
    below plant:

    - an ATTRIBUTE whose name reads a clock — `time.time()`, `clk.time()`,
      `_dt.now()`, `date.today()`, `p.stat().st_mtime`, `os.path.getmtime(p)`.
      What the attribute hangs off is irrelevant, so an alias is not a disguise
      and neither is a space before the paren. The exceptions are
      `_TIME_MODULE_ONLY` — readers on the `time` module, and methods that
      convert an existing value when they hang off a datetime — matched only
      against `time` or a name this same tree binds to that module, by import,
      by `as`, or by plain assignment.
    - a FROM-IMPORT of such a name FROM a clock-bearing module —
      `from time import monotonic`, or `from time import perf_counter as pc`.
      The call site is then a bare Name that says nothing on its own, so the
      import is where it has to be caught, and `as` does not help, because the
      imported NAME is what is matched. The MODULE is checked because
      `from datetime import time` imports a class, not a reader.
      A STAR-import is a hit whatever it comes from, because `from time import
      *` binds `monotonic` while its alias name is the literal `*` and matches
      nothing — the one ImportFrom the identifier match cannot read.
      Ruff's F403 already refuses this at module
      level under the repo's `--select F` gate, but the child programs are
      STRINGS and ruff never sees inside them, which is exactly where it would
      be uncompensated.

    Comments and docstrings are not code and so never reach here, which is the
    other half of why this is an AST walk. Since this file's whole subject IS
    the clock proxy it refuses, a raw-text scan would fail the suite on a
    comment or docstring that merely DESCRIBES a clock reading, under a
    message announcing the proxy was back.

    STATED BOUND, in BOTH directions, because a guard that states only one
    direction hides the other, and this one can be wrong in each.

    TOO NARROW is the live risk. `_CLOCK_READERS` is an OPEN ENUMERATION, not a
    proof: it holds the names known to read a clock, and a name nobody has
    thought of yet reads one just as well. Not hypothetical — without `ctime`
    in the set, `time.ctime()` planted inside `_CHILD` ships clean. Outside
    the node shapes entirely: a
    reading fetched through a helper in another module, or through
    `getattr(time, "time")()`, is neither an Attribute nor an ImportFrom and is
    not caught. The remedy for a gap is one entry in the set and one planted
    spelling in the control below, which
    `test_every_declared_clock_reader_is_planted_in_the_control` makes
    obligatory rather than aspirational.

    TOO WIDE is the risk this guard creates, and it is the one that would tax
    an innocent author. A name here fires wherever it is spelled, so any future
    receiver that happens to carry one of these names is a false hit even
    though it reads nothing. The known collisions are HANDLED rather than
    disclaimed — `_TIME_MODULE_ONLY` for `ctime`, `strftime` and `time`, each a
    method on a datetime as well as a reader on the time module, and
    `_CLOCK_MODULES` for the import arm — and `timestamp`/`fromtimestamp` are
    kept out of the set for the same reason.

    Do not read that list as complete: it is the collisions FOUND so far.
    Narrowing has its own cost, too — each of these exceptions is a place a
    real reading can hide, which is why the receiver resolution below chases
    assignment as well as import. What remains is the residual the set
    accepts on purpose — it errs noisy where a name is genuinely ambiguous
    (`gmtime`, `localtime`, `times` all read a clock when handed nothing) — and
    an author who trips this on something that demonstrably reads no clock
    should narrow that entry the way these three were narrowed, never add an
    exemption beside it.
    """
    # Names bound to the `time` MODULE in this tree, so nothing that is really
    # the module reads as an innocent receiver. Collected first because a use
    # can precede its binding in an ast.walk ordering.
    #
    # THREE ways, not one. Resolving only `import`/`as` would let
    # `clock = time` through, and `clock.time()` is a real wall-clock read —
    # the commonest in the language. Plain assignment is chased to a
    # FIXED POINT so a chain (`a = time`, `b = a`) resolves too; it terminates
    # because each pass either adds a name from a finite set or stops.
    #
    # The bare name `time` counts as the module even with no import in the
    # tree — a fragment cannot be required to carry one — UNLESS a from-import
    # in the tree binds that name to something else. That guard is what stops
    # `from datetime import time` followed by `noon = time` reporting
    # `noon.strftime(...)` as a reading: the time-of-day CLASS, laundered into a
    # module by the very loop that closes the alias hole. A guard that invents
    # a reading is the twin of one that misses it.
    #
    # The guard is a CONDITION on the seed, not a delay of it. Deferring the
    # seed until after the propagation would stop the laundering, but it would
    # also drop `clock = time` in an import-less fragment — the exact spelling
    # the resolution exists for. Conditioning the seed keeps both.
    #
    # Bound, stated, in both directions.
    #
    # TOO NARROW: a binding this cannot see leaves `.time`/`.ctime`/
    # `.strftime` off that receiver unread — `globals()["clock"] = time`, a
    # module stashed in a dict or an attribute, a parameter it is passed to, a
    # TUPLE target (`clock, other = time, os`, which the loop skips because it
    # matches only a bare Name), and a from-import whose export IS a module — a
    # relative `from . import time`, or `from os import path as time` — which
    # suppresses the seed tree-wide because this cannot tell a module bound that
    # way from the stdlib one. That is the price of not firing on
    # `frozen.strftime(fmt)`, and it is a narrower hole than resolving imports
    # alone would leave.
    #
    # Two shapes are NOT on this list, because each ships a live reading from
    # inside `_CHILD` if left unread: a binding by walrus or `for` target
    # (`(clk := time).time()`, `for clk in (time,)`), read by `rebinds`; and
    # the module fetched by a CALL or a SUBSCRIPT as the receiver itself
    # (`__import__("time").time()`, `sys.modules["time"].time()`), read by
    # `_is_clock_module`. Neither is closed by firing on every non-Name
    # receiver, which would fire on `datetime(...).strftime(fmt)` — the two
    # module-FETCH spellings are named instead, and the frozen conversions
    # stay in `_SILENT_PROGRAMS` to say so.
    #
    # TOO WIDE: this is SCOPE-BLIND. A name bound to the module anywhere in the
    # tree marks that name everywhere, so `frozen = time` inside one function
    # makes an unrelated parameter called `frozen` a time-module receiver in
    # another. Deliberate, not an oversight: the alternative is a scope
    # analysis, and expensive machinery is where the next defect hides. The
    # failure mode is a false RED that names the line, which an author can
    # read and rename around.
    module_names = set()
    shadowed = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "time":
                    module_names.add(alias.asname or "time")
        elif isinstance(node, ast.ImportFrom):
            # ANY from-import that binds the name, whatever module it comes
            # from. A from-import binds what the module EXPORTS, not the
            # module itself — `from datetime import time` binds the time-of-day
            # class, `from time import time` binds the function, and `from
            # time import sleep as time` binds a different function again —
            # except where the export IS a module, as in a relative `from .
            # import time` or `from os import path as time`, which this cannot
            # tell from the stdlib one and so suppresses the seed for, exactly
            # as the bound above says. In every case the bare-name seed would
            # be a lie about this tree.
            #
            # `node.module == "time"` is NOT excluded, although such an import
            # is often caught on the import arm below anyway. That holds only
            # when the imported NAME is a declared reader:
            # `from time import sleep as time` binds `time` to something that
            # is not the module and fires nothing below, so excluding it would
            # leave the seed treating it as one. The `_SILENT_PROGRAMS` plant
            # for that spelling is what says so.
            shadowed = shadowed or any(
                (alias.asname or alias.name) == "time" for alias in node.names)
    if not shadowed:
        module_names.add("time")
    rebinds = [(node.targets, node.value) for node in ast.walk(tree)
               if isinstance(node, ast.Assign)]
    rebinds += [([node.target], node.value) for node in ast.walk(tree)
                if isinstance(node, ast.AnnAssign) and node.value is not None]
    # WALRUS and `for` TARGETS. Built from `ast.Assign` and `ast.AnnAssign`
    # only, `rebinds` would never see `(clk := time).time()` or
    # `for clk in (time,): clk.time()`, so neither name would enter
    # `time_names` and both would ship a live clock reading from inside
    # `_CHILD` with this file GREEN. A `for` target binds the ELEMENTS of a
    # literal sequence, so the iterable is unpacked when it is one —
    # `for clk in (time,)` binds `clk` to `time` and nothing else would see it.
    rebinds += [([node.target], node.value) for node in ast.walk(tree)
                if isinstance(node, ast.NamedExpr)]
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            source = node.iter
            elements = (source.elts
                        if isinstance(source, (ast.Tuple, ast.List, ast.Set))
                        else [source])
            rebinds += [([node.target], element) for element in elements]
    # A fixed point, not one pass: `rebinds` follows ast.walk's breadth-first
    # order, so a link can be visited before the assignment that binds it
    # (`alias = holder` at module level, `holder = time` inside a function).
    # It terminates because every pass either adds a name from the finite set
    # of assignment targets in this tree, or stops.
    grew = True
    while grew:
        grew = False
        for targets, value in rebinds:
            if not (isinstance(value, ast.Name) and value.id in module_names):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in module_names:
                    module_names.add(target.id)
                    grew = True
    time_names = module_names

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in _TIME_MODULE_ONLY:
                # Only off the time module. `frozen.strftime(fmt)` converts a
                # value that already exists and reads nothing.
                if _is_clock_module(node.value, time_names):
                    hits.append((node.lineno, f".{node.attr}"))
            elif node.attr in _CLOCK_READERS:
                hits.append((node.lineno, f".{node.attr}"))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    # From ANY module, and that is the fail-closed direction on
                    # purpose: a star-import binds names this walk can no
                    # longer see, and nothing in this file should use one.
                    hits.append((node.lineno,
                                 f"from {node.module} import *"))
                elif (alias.name in _CLOCK_READERS
                        and node.module in _CLOCK_MODULES):
                    hits.append(
                        (node.lineno,
                         f"from {node.module} import {alias.name}"))
    return sorted(hits)


def _reader_name(spelling: str) -> str:
    """The bare reader name a `_clock_reads` spelling reports.

    `.st_mtime` -> `st_mtime`, `from time import monotonic` -> `monotonic`.
    Exists so the coverage control can compare NAMES exactly rather than by
    substring, where `.st_mtime` would match `.st_mtime_ns` and report a name
    as planted that nothing plants.
    """
    return spelling.rsplit(" ", 1)[-1] if spelling.startswith("from ") \
        else spelling.lstrip(".")


def _builtin_name(spelling: str) -> str:
    """The bare builtin name a `_code_from_data` spelling reports.

    `.exec` -> `exec`, `"eval"` -> `eval`, `b"exec"` -> `exec`,
    `from builtins import compile` -> `compile`. The sibling of `_reader_name`
    above, and placed beside it rather than beside `_code_from_data` because
    the two are the same thing for the two detectors — the coverage controls
    compare NAMES, and a spelling that carries a module and two keywords, or
    a bytes prefix, no longer yields the subject to stripping punctuation. A
    control that could not recover it would report the name as unplanted
    while the plant sits right there.
    """
    if spelling.startswith("from "):
        return spelling.rsplit(" ", 1)[-1]
    if spelling.startswith("."):
        return spelling[1:]
    # ANCHORED on the two-character marker `b"`, never a bare `b`, the way
    # its sibling `_code_from_data_shape` anchors. The two disagreeing about
    # what the bytes prefix is would decapitate the bare plant for any future
    # name starting with `b` — reporting it as drifted off its subject while
    # the plant sits right there, which is the exact failure this helper
    # exists to prevent.
    return spelling.removeprefix('b"').strip('"')


# The shapes the arms of `_code_from_data` can report. It is a literal — one
# arm serves two of them (`ast.Constant` answers `string` and `bytes`), so no
# 1:1 read of the arms could produce this list — but it is not the ONLY thing
# standing between a new arm and silence:
# `test_every_arm_of_the_code_from_data_scan_is_exercised_by_a_plant`
# executes the function over every plant and requires each `hits.append` site to
# have RUN. A new arm with no plant is then red whatever it reports, which this
# literal on its own cannot do: an arm with no plant and no entry here would
# leave the file green.
_CODE_FROM_DATA_SHAPES = ("bare", "attribute", "string", "bytes",
                          "from-import")


# The list-mutating methods `_append_sites` reads, declared so the control
# below can require one exercised spelling EACH rather than a typed count.
_ADD_METHODS = ("append", "extend", "insert")


def _append_sites(fn, *, holder: str = "hits") -> dict[int, str]:
    """{line: source} for every statement in `fn` that adds to `holder`.

    Read from the function's own AST, so an arm added to it appears here
    without anyone remembering to say so — the half a hand-typed vocabulary
    cannot supply.

    EVERY spelling that mutates the list counts, not just `hits.append(...)`:
    `hits += [...]` and `hits.extend(...)` too. Matching one shape would make
    an arm written another way invisible, and this function would report full
    coverage over a site set that does not contain the new arm — an unplanted
    arm reached through a different spelling, in the guard written to make
    totality mechanical.
    `test_every_arm_of_the_code_from_data_scan_is_exercised_by_a_plant` proves
    the widening on a scratch
    function carrying all three.
    """
    source = textwrap.dedent(inspect.getsource(fn))
    base = fn.__code__.co_firstlineno - 1
    lines = source.splitlines()
    sites = {}

    def names_holder(node) -> bool:
        return isinstance(node, ast.Name) and node.id == holder

    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _ADD_METHODS
                and names_holder(node.func.value)):
            sites[base + node.lineno] = lines[node.lineno - 1].strip()
        elif isinstance(node, ast.AugAssign) and names_holder(node.target):
            sites[base + node.lineno] = lines[node.lineno - 1].strip()
    return sites


def _lines_run(fn, *args) -> set:
    """Which lines of `fn`'s own body executed during one call.

    A line tracer scoped to `fn.__code__`, which is how "this arm RAN" is
    asked of the real function rather than inferred from what it returned.
    Any tracer already installed is restored, so a coverage run is interrupted
    for the length of one call and no longer.
    """
    seen = set()

    def trace(frame, event, arg):
        if frame.f_code is not fn.__code__:
            return None
        if event == "line":
            seen.add(frame.f_lineno)
        return trace

    previous = sys.gettrace()
    sys.settrace(trace)
    try:
        fn(*args)
    finally:
        sys.settrace(previous)
    return seen


def _unexercised_code_from_data_arms() -> dict[int, str]:
    """`hits.append` sites in `_code_from_data` that no declared plant runs."""
    sites = _append_sites(_code_from_data)
    run = set()
    for _, _, program in _CODE_FROM_DATA_PLANTS:
        run |= _lines_run(_code_from_data, ast.parse(program))
    return {line: src for line, src in sites.items() if line not in run}


def _code_from_data_shape(spelling: str) -> str:
    """Which of `_code_from_data`'s arms reported `spelling`.

    The control below labels each plant with the shape it is supposed to
    exercise, and without this the label is checked against nothing:
    rewriting all three `from-import` plant PROGRAMS as bare-name programs,
    with their labels untouched, would leave the file green — the very cell
    the pair check makes undeletable, corruptible in silence. Deriving the
    shape from
    what the detector actually REPORTED is what closes the gap between the
    label and the subject.

    STATED BOUND: an arm added later that reports a spelling in none of these
    forms answers `unknown`, and the control refuses it by name rather than
    bucketing it as `bare` — noisy, never silent, the direction this file errs
    in. What it cannot do on its own is force a new arm to be PLANTED;
    `_unexercised_code_from_data_arms` closes that by reading which
    `hits.append` SITES the plants execute rather than which shapes they
    report.
    """
    if spelling.startswith("from "):
        return "from-import"
    if spelling.startswith("."):
        return "attribute"
    if spelling.startswith('b"'):
        return "bytes"
    if spelling.startswith('"'):
        return "string"
    if spelling.isidentifier():
        return "bare"
    return f"unknown:{spelling}"


# Module-level strings that are PROSE or CONFIG rather than child programs, by
# name. Every other module-level string must parse as Python, and is then
# scanned as a program whether or not anything launches it. A string that
# does neither is refused by name. That inversion is the point: coverage does
# not depend on finding the launch sites, so there is no launch spelling left
# to recognise. Each entry here must be a live
# module-level string that does NOT parse; an entry a scan would have reached
# anyway is dead config, the same silence `_CLOCK_MODULES` refused `resource`.
_NOT_PROGRAMS = frozenset({"REPO_YAML"})

# The dunder-named strings the INTERPRETER binds in a module namespace, which
# are the only ones here that are not a child program. A SET, not the dunder
# SHAPE, and that distinction is the whole entry: a shape reads as "the
# interpreter put it there" and is not, so it is a silent bucket —
# `name.startswith("__")` lets a `__THIRD` program through, and a both-ends
# test lets `__ESCAPE__` through (the same string would fail the guard named
# `_XESCAPE` and pass it named `__XESCAPE__`).
#
# Non-string module dunders (`__builtins__`, `__loader__`, `__spec__`) are
# deliberately absent: `_shipped_programs` skips every non-str anyway, so
# naming them here would be config no mutation could reach — the standard
# `_CLOCK_MODULES` applied to `resource`.
#
# STATED BOUND: an interpreter that binds a module dunder STRING not listed
# here makes that name a program, which will not parse and so is REFUSED by
# name — noisy, never silent, which is the direction this file errs in.
# `test_a_dunder_named_string_is_a_program_unless_the_interpreter_bound_it`
# asserts the set matches this interpreter in both directions, so the day that
# happens it arrives as a named failure rather than a puzzle.
_MODULE_DUNDERS = frozenset({
    "__cached__", "__doc__", "__file__", "__name__", "__package__",
})

# WHERE this file reaches for the `subprocess` module, and what each site
# launches — the second half is a declaration a reviewer reads, not a value
# anything checks. The guard pins the WHERE: any function that names the
# module and is not listed here is refused by name, so a hand-rolled launch,
# in any spelling (`Popen`, `run`, a from-import, `__import__("subprocess")`
# — there is no list of spellings here to widen), has to either go through
# `_race`, where the program is scanned before it is spawned, or be declared
# here in a diff a reviewer sees. That is what turns "this file launches its
# children exactly one way" from a claim into a check.
#
# STATED BOUND: WHAT a declared site launches is trusted to the review that
# admitted it, not checked. `_git` runs whatever git runs — including an
# alias a caller passes as `-c alias.x=!...` — and the CLI site runs the
# module-level `_CLI_ROUND_NEW`, which is scanned only because it IS
# module-level (a local program at that call site would be a child no scan
# reads). A declared site that
# starts launching something else is a diff to a function this dict names,
# which is the review conversation this dict exists to force.
_SUBPROCESS_SITES = {
    "_race": "child programs — each scanned by _race before it is spawned",
    "_git": "git, with the caller's arguments",
    "test_round_new_prints_only_the_path_on_stdout":
        "the warden CLI, running _CLI_ROUND_NEW (module-level, so scanned)",
    "_subprocess_uses": "nothing — the scan names the module as a string",
    "test_the_subprocess_scan_is_proved_to_see_every_spelling":
        "nothing — the control names the module as a string it expects back",
}


def _shipped_programs(namespace: dict) -> tuple[dict[str, ast.Module], list[str]]:
    """Every module-level STRING in `namespace`, parsed as a program, by name —
    and the names of the ones that neither parse nor are declared prose.

    `_CHILD` and `_COLLIDER` are real programs that run in real processes, and
    a child program is where clock plumbing would live — so a scan that
    stopped at this module's own statements would miss the one place the
    defect can actually occur.

    Every module-level str BINDING, and that word is load-bearing: a str
    inside a module-level container is not a binding this reads, so the
    control's own plants — `_CAUGHT_PROGRAMS`, `_SILENT_PROGRAMS`,
    `_CODE_FROM_DATA_PLANTS` — live in TUPLES precisely so this does not
    take them for shipped child programs. That is a deliberate skip the
    controls depend on, not a bucket: walking containers would report every
    plant in this file as a violation.

    This reads the module's RUNTIME namespace, not its AST, and that is the
    point. Matching `ast.Constant` strings at module level cannot see a
    program assembled by concatenation
    (`_THIRD = _CHILD + \"\"\"…\"\"\"` is a BinOp), an f-string, or anything built by
    a call, and leaves that silent skip to `_launched_programs`, which
    recognises exactly one launch spelling — so a program launched through an
    alias of `_race` would ship a live clock read into a real process while
    the guard reported clean. Here a str is a str
    however it was built, and every one is either parsed and scanned, declared
    in `_NOT_PROGRAMS`, or refused by name — there is no third, silent bucket.

    The names the INTERPRETER bound are skipped, and only those —
    `_MODULE_DUNDERS`, where `__doc__` is this module's docstring and prose is
    not code. Skipping by dunder SHAPE is a silent bucket: a prefix test lets
    `__THIRD` through, and a both-ends test lets `__ESCAPE__` through.
    Everything else
    that is a str, including names imported from elsewhere, is held to the
    rule: `REPO_YAML` is why the allowlist exists.
    """
    programs: dict[str, ast.Module] = {}
    undeclared: list[str] = []
    for name, value in sorted(namespace.items()):
        if name in _MODULE_DUNDERS or not isinstance(value, str):
            continue
        if name in _NOT_PROGRAMS:
            continue
        try:
            programs[name] = ast.parse(value)
        except SyntaxError:
            undeclared.append(name)
    return programs, undeclared


def _launched_programs(tree: ast.Module) -> list[str]:
    """What each literal `_race(...)` call site names as its child program.

    This is a CROSS-CHECK, not the source of coverage. Coverage comes from
    two places that need no launch site at all: `_shipped_programs` scans every
    module-level string, and `_race` scans whatever it is handed at the launch
    path itself. What this still contributes is that the races exist (`assert
    launched` in the guard — a file whose subject is real concurrent processes
    has to still launch some) and that a literal call site names a
    module-level program rather than building one inline or in a local, which
    comes back here as source text, matches no scanned program, and is refused
    by the caller.

    STATED BOUND, and harmless: it recognises the literal `_race(...)` call,
    positional or `source=` keyword, and no other spelling. A call through an alias, a
    `functools.partial`, or a different name is not seen here — and does not
    need to be, because the program it launches is scanned by `_race` on the
    way in and, if module-level, by `_shipped_programs` regardless. This
    predicate is deliberately not widened: every additional launch spelling is
    another arm on an enumeration that cannot be closed.
    `test_the_launch_site_derivation_is_proved_to_derive` pins it.
    """
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_race"):
            continue
        source = node.args[2] if len(node.args) > 2 else next(
            (kw.value for kw in node.keywords if kw.arg == "source"), None)
        if source is None:
            out.append("<_race called with no child-program argument>")
        elif isinstance(source, ast.Name):
            out.append(source.id)
        else:
            out.append(ast.unparse(source))
    return out


def _subprocess_uses(tree: ast.Module) -> list[tuple[int, str, str]]:
    """Every use of the `subprocess` module in `tree`, as sorted
    `(line, enclosing top-level function or "<module>", spelling)`.

    A use is a Name bound to the module — `subprocess` itself, or whatever an
    `import subprocess as …` anywhere in the tree bound — a from-import out
    of it, or the module's name as a STRING CONSTANT, which is how
    `__import__("subprocess")`, `importlib.import_module("subprocess")` and
    `sys.modules["subprocess"]` all fetch it: three spellings a Name-only
    match would not see. Matching the constant is one closed check rather
    than an arm per fetch. What hangs off the Name is deliberately NOT
    inspected: `Popen`, `run`, `check_output` and the next spelling are one
    rule here, "this function reaches for subprocess", and the guard decides
    by the scope's NAME against `_SUBPROCESS_SITES`. The `import subprocess`
    statement itself is a binding, not a use, and is not reported. A method
    is attributed to `Class.method` and a class-body use to `Class`, so the
    refusal names the scope an author actually wrote.

    STATED BOUND: this sees the `subprocess` module by name — a Name, an
    import, or the whole word as one string. A name assembled at runtime
    (`"sub" + "process"`), a module reached through another module's
    attribute, or a process started through `os.fork`/`os.posix_spawn`/
    `os.system`, `multiprocessing`, or `asyncio`'s subprocess API is not
    seen. None is a spelling of a launch this file uses, and an
    author who adds one is adding a launch primitive, which is a review
    conversation rather than a name on this list.
    """
    module_names = {"subprocess"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    module_names.add(alias.asname or "subprocess")
    uses: list[tuple[int, str, str]] = []

    def scan(node: ast.AST, scope: str) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in module_names:
                uses.append((sub.lineno, scope, sub.id))
            elif isinstance(sub, ast.Constant) and sub.value == "subprocess":
                uses.append((sub.lineno, scope, '"subprocess"'))
            elif (isinstance(sub, ast.ImportFrom) and sub.module
                    and sub.module.split(".")[0] == "subprocess"):
                uses.append((sub.lineno, scope, f"from {sub.module} import "
                             + ", ".join(a.name for a in sub.names)))

    defs = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, defs):
            scan(node, node.name)
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, defs):
                    scan(member, f"{node.name}.{member.name}")
                else:
                    scan(member, node.name)
        else:
            scan(node, "<module>")
    return sorted(uses)


def _defined_twice(tree: ast.Module) -> list[str]:
    """Top-level function and class names `tree` defines more than once.

    A `_SUBPROCESS_SITES` declaration is by NAME, so a second `def _git` would
    inherit the first one's declaration for whatever it launches — and ruff's
    F811 stays quiet when the first definition is used before the second.
    One name, one definition.
    """
    top = [node.name for node in tree.body
           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef))]
    return sorted({name for name in top if top.count(name) > 1})


def _module_clock_reads(namespace: dict, source: str, here: str,
                        ) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """The guard's whole scan, as one composition: `source` (this module's
    own text, attributed to `here`) plus every program `namespace` ships,
    each attributed to its NAME. Returns `(hits, undeclared strings, the
    names scanned)` — the third so the guard's launch cross-check reads the
    SAME derivation it scanned, rather than a second one that could stay
    intact while this one was handed an empty namespace (`globals()` -> `{}`
    here would survive while a separate `_shipped_programs(globals())` kept
    `missing` empty).

    Factored out so `test_the_shipped_programs_are_proved_to_reach_the_
    scan` can plant a reader into a program and watch it come back attributed
    to that program through the same path the guard uses. Pinned only by its
    KEYS, the composition would survive `ast.parse("")` in place of the real
    parse, or the loop that feeds each program to `_clock_reads` replaced by
    `pass`, with the suite green and every child program reduced to an empty
    tree.
    """
    programs, undeclared = _shipped_programs(namespace)
    hits = [(f"{here}:{line}", what)
            for line, what in _clock_reads(ast.parse(source))]
    for name, program in sorted(programs.items()):
        hits += [(f"{name}:{line}", what)
                 for line, what in _clock_reads(program)]
    return hits, undeclared, sorted(programs)


def test_no_verdict_in_this_file_is_derived_from_a_wall_clock():
    """The guard against a wall-clock proxy coming back.

    This module's docstring promises that its tests do not read a clock. As
    PROSE that promise has no `exist_ok=False`, which is the sentence this
    file opens with about the `pre-pr-review` skill. So it is an executable
    fact here, in the shape the file already uses for
    `test_range_binding_is_silent_when_the_colliding_diffs_overlap`.

    WHY THE CLASS EARNS A GUARD. A wall-clock proxy fails intermittently —
    under load on a full-suite run, passing both in isolation and on the run
    immediately after — so its failures read as unrelated flakes rather than
    one defect. And deleting such a proxy leaves behind nothing that notices
    the next one.

    AND THE PROXY CANNOT DETECT WHAT IT IS NAMED FOR. With the twelve mints
    serialised under an exclusive 200 ms `flock`, so that no two are ever
    concurrently inside `create_round_dir`, an overlap assertion still passes:
    taking `t0` before the call and `t1` after, each interval includes the
    lock wait. It measures that the twelve children are ALIVE at once, never
    that the mints overlap. On one idle 18-core laptop it scores 1 to 7
    intersecting pairs out of 66 across 20 trials — a floor of ONE, a single
    coin flip from red — while the same 20 trials under 96-way CPU contention
    score 9 to 66, because contention makes each mint slower and so WIDENS
    the windows it is supposed to narrow. On contended two-vCPU CI runners it
    scores zero. A number that ranges over 0, 1 and 66 with the machine, and
    that passes on a build with the concurrency locked out, is not a
    measurement of round isolation.

    WHAT THIS ENFORCES, exactly: that no clock reading is SPELLED in this
    module or in any module-level string BINDING it holds, as `_clock_reads`
    defines spelled — every such string is a program to this scan unless
    `_NOT_PROGRAMS` names it prose or the interpreter bound the name, however
    it was built. A str inside a module-level container is not a binding and
    is not scanned, which is what lets the controls above hold their plants
    in tuples; `_shipped_programs` says so where the skip lives. `_race` enforces
    the same rule at the launch path for whatever it is handed, and
    `test_every_use_of_subprocess_in_this_file_is_at_a_declared_site`
    is what says `_race` is the launch path. It is not a proof that no timing
    dependency of any kind can exist here, and `_clock_reads` states its
    bound. The companion tests prove each half bites before this one is
    allowed to report clean.
    """
    source = Path(__file__).read_text()
    tree = ast.parse(source)
    here = Path(__file__).name

    hits, undeclared, scanned = _module_clock_reads(globals(), source, here)
    assert not undeclared, (
        f"{undeclared} is a module-level string that neither parses as a "
        "program this scan can read nor is declared in _NOT_PROGRAMS. A child "
        "program is exactly where the deleted plumbing lived, so a string this "
        "cannot see is not skipped — name it prose in _NOT_PROGRAMS, or make it "
        "parse")

    launched = _launched_programs(tree)
    assert launched, (
        "no call to _race passes a child program, so this scan is covering "
        "nothing that runs in another process — _race was renamed, or the "
        "races were rewritten to launch their children some other way, and "
        "either way the programs that must be scanned are no longer findable")
    missing = sorted(set(launched) - set(scanned))
    assert not missing, (
        f"{missing} is launched into a real process by _race, but is not a "
        "module-level string this scan parses — built inline at the call, "
        "held in a local, or no longer valid Python. _race scans it on the "
        "way in regardless; this is the static half saying the same thing "
        "before any process starts")

    assert not hits, (
        "a clock reading is back in tests/test_round_isolation.py, and every "
        "verdict in this file is supposed to hold whether the racers "
        "overlapped or ran in single file:\n"
        + "\n".join(f"  {where}  {what}" for where, what in hits)
        + "\nThat proxy shipped once and flaked twice, "
        "and a reviewer showed it passing on a build where the "
        "mints had been serialised under a lock. If a new test needs to WAIT, "
        "time.sleep is not a reading and is allowed. If it needs to prove two "
        "mints collided, pin them to one derived name the way _COLLIDER does "
        "and let the filesystem answer.")


# The control's plants, module level so the coverage test below can reach the
# same tuple the firing test uses. A TUPLE is not a str, so `_shipped_programs`
# — which scans every module-level str — does not take this for a shipped
# child program and the guard does not report the control's own plants as
# violations; held as one module-level string each, they would be.

# Programs that MUST produce a hit. Every entry is a spelling that escapes a
# narrower scan, or a declared reader that would otherwise be deletable in
# silence.
_CAUGHT_PROGRAMS = (
    # --- the spellings a narrower scan lets escape ---------------------------
    "import time\nt = time.time()",                    # the retired proxy
    "import time as clk\nt = clk.time()",              # an alias
    "from time import monotonic\nt = monotonic()",     # a from-import
    "from time import perf_counter as pc\nt = pc()",   # and aliased
    "import time\nt = time.perf_counter ()",           # a space beats text
    "import time\nt = time.clock_gettime(0)",
    "import time\nt = time.thread_time()",
    "import time\nt = time.gmtime()",
    "from datetime import datetime as dt\nt = dt.now()",
    "from datetime import datetime as dt\nt = dt.utcnow()",
    "from datetime import date\nt = date.today()",
    "import os\nt = os.times()",
    "import os.path\nt = os.path.getmtime('x')",
    "t = p.stat().st_mtime",                           # the fs clock
    "import time\nclock = time.monotonic",             # bound, called later
    "from time import *\nt = monotonic()",             # binds, names nothing
    # ctime / asctime / strftime / st_birthtime: four easily missed, each
    # returning the CURRENT wall clock when handed no timestamp.
    # ctime and strftime are receiver-sensitive, so both are planted OFF THE
    # TIME MODULE, which is the only spelling that reads anything; the silent
    # block plants the bound-method spelling that must NOT fire.
    "import time\nt = time.ctime()",
    "import time as clk\nt = clk.ctime()",             # and via an alias
    # No import in this tree, which pins the `time_names` SEED rather than the
    # alias loop. The seed is what makes the scan independent of seeing the
    # import, and by the standard this file applies to `resource` and to every
    # name in `_CLOCK_READERS`, logic nothing exercises is logic that can be
    # deleted in silence.
    "t = time.ctime()",
    # The module rebound by plain ASSIGNMENT rather than by `as`, the
    # commonest clock read in Python.
    "import time\nclock = time\nt = clock.time()",
    # The same rebinding in a FRAGMENT that carries no import, which is what
    # says the bare-name seed takes part in the propagation rather than being
    # bolted on after it. Deferring the seed would leave this silent, and no
    # other plant could tell.
    "clock = time\nt = clock.time()",
    # And a CHAIN, which is what the fixed point is for.
    "import time as clk\na = clk\nb = a\nt = b.ctime()",
    # An ANNOTATED rebinding, which is a different node type and an arm of the
    # resolution nothing else exercises.
    "import time\nclock: object = time\nt = clock.time()",
    # A chain the walk meets BACKWARDS — `alias = holder` sits at module level
    # and `holder = time` one level deeper, so ast.walk's breadth-first order
    # visits the consumer first and one pass resolves nothing. This is what the
    # fixed point is for, and the forward-ordered chain above cannot show it.
    "import time\n"
    "def _bind():\n"
    "    global holder\n"
    "    holder = time\n"
    "alias = holder\n"
    "t = alias.ctime()",
    "import time\nt = time.asctime()",
    "import time\nt = time.strftime('%s')",
    "import time as clk\nt = clk.strftime('%s')",
    "t = p.stat().st_birthtime",                       # darwin's fs clock
    # --- the two shapes a Name-only receiver resolution cannot see. Unread,
    # each one planted inside `_CHILD` and launched through the literal `_race`
    # call reaches a real process and returns a live `time.time()` with this
    # file GREEN. `.monotonic` is matched receiver-blind and is caught anyway,
    # so the hole is exactly the width of the `_TIME_MODULE_ONLY` narrowing —
    # which is why every plant here uses one of those three names.
    #
    # Shape 1, the module fetched as the RECEIVER itself: a Call and a
    # Subscript, neither of them an `ast.Name`.
    '__import__("time").time()',
    'import sys\nt = sys.modules["time"].time()',
    'import importlib\nt = importlib.import_module("time").ctime()',
    # Shape 2, a binding an Assign-only rebind graph lacks: a walrus and a
    # `for` target, including the literal-sequence unpack.
    "import time\nt = (clk := time).strftime('%s')",
    "import time\n_ = (clk := time)\nt = clk.time()",
    "import time\nfor clk in (time,):\n    t = clk.time()",
    "import time\nfor clk in [time]:\n    t = clk.ctime()",
    # …and a COMPREHENSION target, the same node type one context over.
    "import time\nts = [clk.time() for clk in (time,)]",
    # --- the rest of the declared set, so no name is deletable in silence.
    # The detector's docstring promises "one entry in the set and one planted
    # spelling in the control", and a protocol nobody follows is a comment,
    # not a protocol.
    "import time\nt = time.time_ns()",
    "import time\nt = time.monotonic_ns()",
    "import time\nt = time.perf_counter_ns()",
    "import time\nt = time.process_time()",
    "import time\nt = time.process_time_ns()",
    "import time\nt = time.thread_time_ns()",
    "import time\nt = time.clock_gettime_ns(0)",
    "import time\nt = time.localtime()",
    "from timeit import default_timer\nt = default_timer()",
    # A from-import plant for every module in _CLOCK_MODULES, so dropping one
    # cannot go unnoticed: the attribute plants above reach `os.path.getmtime`
    # without ever exercising the IMPORT arm for it, which would leave
    # `os.path` removable in silence.
    "from os.path import getmtime\nt = getmtime('x')",
    "from os import times\nt = times()",
    "import os.path\nt = os.path.getctime('x')",
    "import os.path\nt = os.path.getatime('x')",
    "t = p.stat().st_atime",
    "t = p.stat().st_ctime",
    "t = p.stat().st_atime_ns",
    "t = p.stat().st_ctime_ns",
    "t = p.stat().st_mtime_ns",
    "t = p.stat().st_birthtime_ns",
)

# Programs that must produce NO hit. Waiting is not measuring, prose is not
# code, and converting a value that already exists is not reading a clock.
_SILENT_PROGRAMS = (
    "import time\ntime.sleep(0.001)",                  # the starting gun
    "from time import sleep\nsleep(0.001)",
    "from datetime import datetime, timezone\n"
    "d = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)",  # frozen
    "import subprocess\nsubprocess.run(['x'], timeout=60)",
    # Deriving an epoch from the FROZEN constant, which is the repair this
    # guard's own failure message prescribes. It consumes a value that already
    # exists and reads no clock, so forbidding it would fail the suite for
    # taking the advice.
    "from datetime import datetime, timezone\n"
    "frozen = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)\n"
    "epoch = int(frozen.timestamp())\n"
    "back = datetime.fromtimestamp(epoch, timezone.utc)",
    # RENDERING the frozen constant, which reads no clock at all.
    # `time.strftime(fmt)` reads one; `frozen.strftime(fmt)` converts a value
    # that already exists; they are one attribute name. Matched receiver-blind,
    # this would fail the suite on warden/runs.py's own idiom and contradict
    # the `timestamp` exclusion in the set.
    "from datetime import datetime, timezone\n"
    "frozen = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)\n"
    "name = frozen.strftime('%Y%m%dT%H%M%S%fZ')\n"
    "also = frozen.ctime()\n"
    "tod = frozen.time()",
    # The time-of-day CLASS, not a reader. An import arm that matches the name
    # and discards the module makes this a hit.
    "from datetime import datetime, date, time as tt\n"
    "d = datetime.combine(date(2026, 1, 1), tt(0, 0))",
    # The same class REBOUND, which a receiver resolution can launder into the
    # time module and report as a reading. Nothing here reads a clock.
    # The two frozen CONVERSIONS the module-fetch arms must keep silent:
    # constructing a datetime and rendering it, and narrowing one to a date
    # first. Both have a non-Name receiver — a Call — which is exactly what
    # firing on every non-Name receiver would catch, and neither reads a
    # clock. They are the price the named module-fetch shapes do not pay.
    "from datetime import datetime\n"
    "name = datetime(2026, 9, 1).strftime('%Y%m%d')",
    "from datetime import datetime\n"
    "d = datetime(2026, 9, 1)\n"
    "name = d.date().strftime('%Y%m%d')",
    "from datetime import time\n"
    "noon = time\n"
    "label = noon.strftime('%H')\n"
    "also = noon.ctime()",
    # The same class under the BARE name. The seed treats a bare `time` as the
    # module when no import says otherwise, and this import says otherwise.
    "from datetime import time\n"
    "label = time.strftime('%H')\n"
    "also = time.ctime()",
    # The name rebound OUT OF THE TIME MODULE ITSELF to something that is not
    # the module. This is what says the shadow arm looks at the NAME and not at
    # the module it came from: with `module == "time"` excluded, and `sleep`
    # not a declared reader, nothing on the import arm would fire and the seed
    # would call `time.ctime()` here a reading.
    "from time import sleep as time\n"
    "also = time.ctime()",
    "# the deleted proxy was `t0 = time.time()`",  # a comment
    '"""One test used to require two mint windows to intersect."""',
)


def test_the_clock_scan_is_proved_to_bite_before_it_reports_clean():
    """The positive control — and the reason the guard above is worth having.

    Without it nothing proves the scan can produce a hit: a detector that
    finds nothing makes `assert not hits` vacuously true. That is exactly the
    shape the guard exists to close one level up — a deletion that leaves
    nothing behind to notice.

    So every spelling a narrower scan lets escape is planted here and must be
    caught, and the
    ones that must stay silent are planted too. A detector that has only ever
    reported clean has proved nothing about what it would reject.

    The plants are the module-level `_CAUGHT_PROGRAMS` and `_SILENT_PROGRAMS`
    above, so `test_every_declared_clock_reader_is_planted_in_the_control`
    can check the same tuple this test fires. They are a TUPLE rather than a
    string constant each, which is what keeps `_shipped_programs` from
    mistaking them for shipped child programs and making the guard above report
    this control's own plants as violations.
    """
    caught = _CAUGHT_PROGRAMS
    for program in caught:
        assert _clock_reads(ast.parse(program)), (
            f"the clock scan sees no reading in:\n{program}\n"
            "Every spelling here was MEASURED getting past this guard's first "
            "cut, so a scan that misses one has "
            "gone back to forbidding a spelling instead of a class")

    silent = _SILENT_PROGRAMS
    for program in silent:
        assert not _clock_reads(ast.parse(program)), (
            f"the clock scan reports a reading in:\n{program}\n"
            "Waiting is not measuring, and prose is not code. A guard that "
            "fails the suite over a COMMENT naming the retired proxy makes "
            "this file's own history unwritable, and the first cut did "
            "precisely that")


def test_every_declared_clock_reader_is_planted_in_the_control():
    """The promise in `_clock_reads`'s bound, made obligatory.

    That docstring tells the next author the remedy for a gap is "one entry in
    the set and one planted spelling in the control". Unchecked, a name can be
    removed from `_CLOCK_READERS` with the suite staying green, and a protocol
    nobody checks is a comment, not a protocol.

    So the control's coverage is CHECKED rather than trusted: every declared
    name must be exercised by at least one planted program, and no plant may
    exercise a name the set does not declare. Both directions matter — the
    first stops a name being deletable in silence, the second catches a plant
    whose spelling drifted onto something no longer enforced.

    This is NOT the detector re-implementing itself, which would agree with
    itself forever. It asserts nothing about WHICH constructs read a clock;
    only that each declared name is actually exercised by a hand-written plant.
    The judgment stays in `_CLOCK_READERS` and in the plants, where a reader
    can argue with it.
    """
    exercised = {
        _reader_name(spelling)
        for program in _CAUGHT_PROGRAMS
        for _, spelling in _clock_reads(ast.parse(program))
        if not spelling.endswith(" import *")
    }
    unpinned = sorted(_CLOCK_READERS - exercised)
    assert not unpinned, (
        f"{len(unpinned)} declared clock reader(s) are planted nowhere in the "
        "control, so deleting them from _CLOCK_READERS would leave the suite "
        f"green and nothing would notice: {unpinned}. Add one planted spelling "
        "per name to _CAUGHT_PROGRAMS, or drop the name — a reader nothing "
        "exercises is a reader nobody can trust is still there")
    stray = sorted(exercised - _CLOCK_READERS)
    assert not stray, (
        f"the control plants exercise {stray}, which _CLOCK_READERS does not "
        "declare — a plant has drifted onto a name the set no longer holds, so "
        "it is proving something about a reader that is not enforced")


# ---------- the child-program coverage, pinned ------------------------------

def test_the_shipped_programs_are_proved_to_reach_the_scan():
    """The composition control for the guard.

    `test_the_clock_scan_is_proved_to_bite_before_it_reports_clean` proves
    `_clock_reads` fires on hand-built trees. This proves the PARSED BODIES of
    the shipped programs reach it: pinned only by their keys,
    `_shipped_programs` and the loop that feeds it would survive
    `ast.parse("")` in place of the real parse, or the loop replaced by
    `pass`, with the suite green and every child program scanned as an empty
    tree — the half the guard calls load-bearing, doing nothing.

    So a reader is planted into a copy of `_CHILD`, built by CONCATENATION,
    which is a BinOp and not a Constant —
    and must come back through `_module_clock_reads`, attributed to the name
    it was planted under, with the clean copy beside it reporting nothing.
    The names are distinct from anything this module binds, so no real
    program is mistaken for a plant.
    """
    planted = _CHILD + "\nt0 = time.time()\n"
    namespace = {"_PLANTED": planted, "_CLEAN": _CHILD,
                 "_not_a_program": 42, "__doc__": "t = time.time()"}
    hits, undeclared, scanned = _module_clock_reads(namespace, "x = 1", "self")
    assert not undeclared
    assert scanned == ["_CLEAN", "_PLANTED"], (
        "the names the composition reports scanning are not the programs it "
        "was handed, so the guard's launch cross-check reads a different "
        "derivation from the one that was scanned")
    line = planted.count("\n")            # the plant is the last line
    assert hits == [(f"_PLANTED:{line}", ".time")], (
        f"a clock reading planted in a shipped program came back as {hits}; "
        "the parsed bodies of the programs this module ships are not reaching "
        "_clock_reads through the composition the guard uses, so the guard is "
        "scanning empty trees and reporting clean")

    # and the module's OWN source is scanned and attributed to `here`, so the
    # first half of the composition cannot be dropped in favour of the second
    hits, _, _ = _module_clock_reads({}, "import time\nt = time.time()", "self")
    assert hits == [("self:2", ".time")]


def test_a_module_level_string_that_is_not_a_program_is_refused_by_name():
    """The inversion, with its two edges pinned.

    A module-level string is a program to the scan unless `_NOT_PROGRAMS`
    names it prose. There is no third bucket: one that neither parses nor is
    declared comes back by name, and the guard refuses it. That is what makes
    a program built by concatenation, or an f-string, or a call, impossible
    to skip in silence — an `ast.Constant` filter has exactly that bucket, and
    a launch-site derivation behind it sees one spelling.

    The other edge is the allowlist itself. Every name it holds must be a live
    module-level string that does NOT parse: an entry naming nothing is stale,
    and one naming a string the scan would parse anyway is dead config — the
    standard `_CLOCK_MODULES` applied to `resource` — and either would be a
    place a future program could be hidden by declaration.
    """
    programs, undeclared = _shipped_programs({
        "_PROSE": "the proxy was t0 = time.time(), and it flaked",
        "_PROGRAM": "import time\nt = time.time()",
        "__THIRD": "import time\nt = time.time()",     # a prefix is not a dunder
        "__NOTE": "prose: under a double-underscore prefix",
        "REPO_YAML": "version: 1\nrepo: x",
        "__doc__": "not: a program",
        "_tuple": ("import time\nt = time.time()",),
    })
    assert undeclared == ["_PROSE", "__NOTE"], undeclared
    assert sorted(programs) == ["_PROGRAM", "__THIRD"]

    for name in sorted(_NOT_PROGRAMS):
        value = globals().get(name)
        assert isinstance(value, str), (
            f"_NOT_PROGRAMS names {name}, which is not a module-level string "
            "here — a stale entry is a name a future program could hide under")
        with pytest.raises(SyntaxError):
            ast.parse(value)


def test_a_dunder_named_string_is_a_program_unless_the_interpreter_bound_it():
    """The third silent bucket, closed.

    Skipping every name that is dunder at BOTH ends, before the parse and
    before `_NOT_PROGRAMS`, would leave a module-level `__ESCAPE__` carrying a
    live reading neither scanned, nor declared, nor refused — while
    `_shipped_programs` says "there is no third, silent bucket" and the guard
    it feeds claims every module-level string is a program to the scan
    "however it was built". The SAME string must fail the guard whether it is
    named `_XESCAPE` or `__XESCAPE__`.

    A dunder shape reads as "the interpreter put it there" and is not. So the
    skip is a declared SET of the names the interpreter actually binds, and
    everything else is held to the rule — the same inversion as
    `_NOT_PROGRAMS`, one level down.
    """
    escape = "import time\nt0 = time.time()\n"
    programs, undeclared = _shipped_programs({
        "__XESCAPE__": escape,
        "__doc__": "not: a program",
        "__name__": "tests.test_round_isolation",
    })
    assert sorted(programs) == ["__XESCAPE__"], (
        "a dunder-named module-level string was skipped by SHAPE rather than "
        "because the interpreter bound it, so it is neither scanned, declared "
        f"nor refused: {sorted(programs)}")
    assert not undeclared

    # and it reaches the scan through the composition the guard uses
    hits, _, scanned = _module_clock_reads({"__XESCAPE__": escape}, "x = 1", "s")
    assert hits == [("__XESCAPE__:2", ".time")], hits
    assert scanned == ["__XESCAPE__"]

    # The declared set is exactly the dunder-named STRINGS this interpreter
    # binds in a module: no unclassified one may exist (that is the silent
    # bucket returning), and no entry may name something absent (that is the
    # dead config `_CLOCK_MODULES` refused `resource` for).
    here = {name for name, value in globals().items()
            if name.startswith("__") and name.endswith("__")
            and isinstance(value, str)}
    assert not sorted(here - _MODULE_DUNDERS), (
        f"{sorted(here - _MODULE_DUNDERS)} is a dunder-named module-level "
        "string this interpreter bound that _MODULE_DUNDERS does not declare, "
        "so _shipped_programs would treat it as a child program — declare it "
        "there, or it will be refused by name")
    assert not sorted(_MODULE_DUNDERS - here), (
        f"_MODULE_DUNDERS declares {sorted(_MODULE_DUNDERS - here)}, which is "
        "not a dunder-named module-level string here — a name the skip covers "
        "that nothing binds is a place a future program could be hidden")


def test_every_declared_code_from_data_name_is_planted_in_the_control():
    """`_CLOCK_READERS`'s coverage protocol, applied to the sibling set.

    A control that exercises `exec` alone lets `eval` and `compile` be
    deleted from the set with the suite green. That is the deletable-subject
    shape this file makes obligatory for `_CLOCK_READERS`, in
    `test_every_declared_clock_reader_is_planted_in_the_control`, and the
    sibling set follows the same protocol.

    Both directions, and the SHAPES too — a per-name control that only ever
    plants bare names lets the attribute spelling escape.
    """
    exercised, shapes = set(), set()
    for name, shape, program in _CODE_FROM_DATA_PLANTS:
        found = _code_from_data(ast.parse(program))
        assert found, (
            f"the {shape} plant for {name} produces no hit, so it proves "
            "nothing about the refusal it is the control for")
        # The (name, shape) the detector actually REPORTED, not the label the
        # tuple carries. Checked against the label alone, the label is
        # checked against nothing: rewriting all three from-import plant
        # PROGRAMS as bare-name programs, labels untouched, would leave this
        # file green — the cell the pair assertion below makes undeletable,
        # corruptible in silence.
        pairs = {(_builtin_name(sp), _code_from_data_shape(sp))
                 for _, sp in found}
        assert (name, shape) in pairs, (
            f"the {shape} plant for {name} reports {sorted(pairs)}, which "
            "does not contain it — the plant has drifted off its subject, or "
            "off the SHAPE it is labelled with, and is now a duplicate of "
            "another cell wearing this one's name")
        exercised |= {n for n, _ in pairs}
        shapes.add(shape)

    unpinned = sorted(_CODE_FROM_DATA - exercised)
    assert not unpinned, (
        f"{unpinned} is declared in _CODE_FROM_DATA and planted nowhere, so "
        "deleting it would leave the suite green — add one plant per name to "
        "_CODE_FROM_DATA_PLANTS, or drop the name")
    stray = sorted(exercised - _CODE_FROM_DATA)
    assert not stray, (
        f"the control plants exercise {stray}, which _CODE_FROM_DATA does not "
        "declare, so they prove something about a name nothing enforces")

    # What the plant loop above does NOT already force, asserted one per
    # item. NO TOTAL, deliberately: a count of these is easy to get wrong by
    # one, so the sentence carries none — a list that can be appended to
    # cannot be wrong about its own length.
    #
    # One is a BRANCH: `_code_from_data_shape`'s `unknown:` fall-through,
    # which no plant reaches at all. The rest are PREFIX TESTS inside
    # branches the loop DOES force. Each runs on every spelling the plants
    # emit and none of them can be told from a shorter, wrong prefix there,
    # because no name in `_CODE_FROM_DATA` begins with the character the
    # weakened test would accept: `b` for the two `b"` anchors (and `bytes`,
    # `bool`, `bin` are all real builtins), `from` for the `"from "` one.
    # Each leaves the whole file GREEN without its assertion, including the
    # one in `_code_from_data_shape` that `_builtin_name`'s own body comment
    # anchors itself to.
    # Every (name, shape) the plants emit runs
    # through `_code_from_data_shape` and `_builtin_name` in that loop, so a
    # list assertion restating those spellings here cannot fail, and is
    # deliberately absent: an assertion whose subject a preceding guard has
    # already settled is the class this whole file is about.
    #
    # What is left is the two spellings no plant produces. Without the first,
    # `unknown:` could be replaced by a plain `"bare"` fall-through and
    # nothing would notice, turning the refuse-by-name above into a silent
    # bucket — the exact shape a dunder-SHAPE skip is, and the reason
    # `_MODULE_DUNDERS` is a SET.
    assert _code_from_data_shape("exec(argv)") == "unknown:exec(argv)", (
        "a spelling in no known form is bucketed rather than named, so a "
        "sixth arm's reports would be silently filed under an existing shape")
    assert _builtin_name("bytes") == "bytes", (
        "the bytes marker is stripped unanchored, so a future name starting "
        "with `b` is decapitated and reported as drifted off its subject "
        "while its plant sits right there")
    assert _code_from_data_shape("bytes") == "bare", (
        "the shape decoder tests a bare `b` rather than the two-character "
        "marker `b\"`, so a future name starting with `b` is filed as the "
        "BYTES shape wherever it is spelled — including as a bare name, "
        "which is the pair the control would then report as unplanted")
    assert _code_from_data_shape("fromkeys") == "bare", (
        "the shape decoder tests `from` rather than `from `, so a future "
        "name beginning with those four letters is filed as a FROM-IMPORT "
        "wherever it is spelled — the same wrong-pair-in-silence the "
        "assertion above it exists for, on the other prefix test")

    # The five places `_code_from_data` does NOT read, which its bound names
    # one by one. Asserted so the bound is a measurement rather than a claim:
    # "every identifier in the tree is covered" is false on exactly these.
    for unread in ("import builtins as exec\n", "def f(exec):\n    pass\n",
                   "def exec(s):\n    pass\n", "class exec:\n    pass\n",
                   "f(exec=1)\n"):
        assert _code_from_data(ast.parse(unread)) == [], (
            f"{unread!r} now reports a hit, so the bound's list of places "
            "this does not read is out of date — either the list or the arms "
            "moved without the other")
    # Every NAME in every SHAPE, as PAIRS — not the union of the names and
    # the union of the shapes, checked apart. Apart, this control loses a
    # whole cell in silence: with the other two names still planted in every
    # shape, deleting the `exec` from-import plant would leave the whole file
    # GREEN. So would deleting the `exec` attribute plant, because the string
    # plant for `compile` spells `"exec"` as its mode argument and a
    # name-union counts that as coverage. The PAIR is the unit an escape
    # takes — a name in one spelling, past a set that already holds the same
    # name in another.
    #
    # STATED BOUND: this pins DELETION, not ADDITION. The shape vocabulary
    # below is typed out here, so a new arm added to `_code_from_data` with no
    # plant and no entry leaves this green; the
    # `test_every_arm_of_the_code_from_data_scan_is_exercised_by_a_plant`
    # control below
    # covers that. `_code_from_data_shape` narrows that to arms whose
    # spelling is in a form it already knows; a genuinely new form answers
    # `unknown:` and is refused above, but only once some plant reaches it.
    covered = {(name, shape) for name, shape, _ in _CODE_FROM_DATA_PLANTS}
    expected = {(name, shape) for name in _CODE_FROM_DATA
                for shape in _CODE_FROM_DATA_SHAPES}
    assert covered == expected, (
        f"the control plants the {sorted(shapes)} spelling(s) and is missing "
        f"{sorted(expected - covered)}, while planting "
        f"{sorted(covered - expected)} that nothing declares; a per-name "
        "control that never varies the SHAPE is how `builtins.exec` escaped "
        "a set that already held `exec`, and how `from builtins import exec "
        "as _e` escaped the cut that closed it")


def test_every_arm_of_the_code_from_data_scan_is_exercised_by_a_plant():
    """The pair control pins DELETION, not ADDITION; this pins ADDITION.

    `test_every_declared_code_from_data_name_is_planted_in_the_control`
    requires every (name, shape) PAIR, so deleting a plant or mislabelling one
    goes red. It does NOT require an ARM to be planted: the shape vocabulary is
    a literal typed beside the assertion, so a new arm added to
    `_code_from_data` with no plant and no entry leaves that control green.

    `_code_from_data_shape` narrows it and cannot close it: a new arm whose
    spelling is in no known form answers `unknown:` and is refused by name, but
    only once SOME plant reaches the new arm. An arm nothing plants stays
    invisible to every assertion that reads what the function RETURNED.

    So this reads what it RAN. The `hits.append` sites come from the function's
    own AST — an arm appears there the moment it is written, with nobody
    remembering to declare it — and a line tracer scoped to
    `_code_from_data.__code__` records which of them executed while the declared
    plants were scanned. A site no plant runs is named by line and by source.
    """
    sites = _append_sites(_code_from_data)
    # The floor is the SHAPES the plants declare, not a number typed beside
    # the read: four append sites serve five shapes because the `ast.Constant`
    # arm answers both `string` and `bytes`. A bare `>= 4` would sit exactly at
    # the current count and stay green with a fifth arm of any other spelling,
    # so it is derived from both ends instead.
    assert len(sites) >= len(_CODE_FROM_DATA_SHAPES) - 1, (
        f"only {len(sites)} site(s) that add to `hits` found in "
        f"`_code_from_data`, against {len(_CODE_FROM_DATA_SHAPES)} declared "
        "shapes — the AST read has lost its subject, so every arm would "
        "report as exercised over a set that does not hold it")
    unexercised = _unexercised_code_from_data_arms()
    assert not unexercised, (
        "arm(s) of `_code_from_data` that no plant in "
        "`_CODE_FROM_DATA_PLANTS` executes: "
        + "; ".join(f"line {line}: {src}" for line, src in
                    sorted(unexercised.items()))
        + ". Add a plant that reaches the arm, or delete the arm — an arm "
        "nothing exercises is a claim about what the refusal reads, and it is "
        "how `builtins.exec(argv)` and `from builtins import exec as _e` each "
        "walked past the cut before them")

    # The tracer is proved able to SEE an unexercised arm, over the real
    # function: scanning only the bare-name plants must leave the attribute,
    # constant and import arms unrun. Without this the check above passes over
    # an empty set the moment the line tracer stops working — a coverage read
    # that always reports full coverage is the defect one level up.
    bare_only = [p for n, shape, p in _CODE_FROM_DATA_PLANTS
                 if shape == "bare"]
    assert bare_only, "the bare plants are gone, so this control is vacuous"
    run = set()
    for program in bare_only:
        run |= _lines_run(_code_from_data, ast.parse(program))
    missed = {line for line in sites if line not in run}
    assert missed, (
        "scanning only the bare-name plants leaves NO arm unrun, so the line "
        "tracer is reporting every site as executed whatever runs — this check "
        "cannot fail and the unexercised-arm gap is open behind it")

    # …and the AST read sees every spelling that ADDS to the list, not only
    # `hits.append(...)`. Driven over a scratch function carrying every
    # spelling, because an arm spelled `hits += [...]` or `hits.extend(...)`
    # must not be invisible while `_append_sites`'s docstring says an arm
    # appears "the moment it is written".
    def _every_spelling(tree):                       # pragma: no cover
        hits = []
        hits.append((1, "append"))
        hits += [(2, "augassign")]
        hits.extend([(3, "extend")])
        hits.insert(0, (4, "insert"))
        return hits

    # ONE CONTROL PER SPELLING, named INDEPENDENTLY of the tuple the read
    # consults. Without one, deleting `insert` from `_ADD_METHODS` leaves this
    # file green; a count derived from that same tuple is no better — it
    # shrinks with the tuple and stays green too, which is the
    # self-referential shape this whole file is about. The
    # scratch function above plants each spelling with its own marker word, and
    # every marker must come back.
    found = " ".join(_append_sites(_every_spelling).values())
    for marker in ("append", "augassign", "extend", "insert"):
        assert marker in found, (
            f"the AST read does not see the {marker!r} spelling of adding to "
            f"`hits` — it found {found!r}. An arm written in a spelling it "
            "cannot see is an arm this control reports as covered while no "
            "plant reaches it")

    # …and the shape vocabulary the pair control uses is still the one the
    # shapes decoder can answer, so the two cannot drift into disagreement.
    decoded = {_code_from_data_shape(spelling)
               for _, _, program in _CODE_FROM_DATA_PLANTS
               for _, spelling in _code_from_data(ast.parse(program))}
    assert decoded == set(_CODE_FROM_DATA_SHAPES), (
        f"the plants decode to {sorted(decoded)} while the declared shapes are "
        f"{sorted(_CODE_FROM_DATA_SHAPES)} — one list moved without the other")


def test_the_import_arm_reads_every_part_of_the_statement():
    """The four properties of the import arm the plants cannot pin, because
    every plant is a single-name absolute import from `builtins`.

    A control built only out of the plants stays green with the arm narrowed
    to `node.module == "builtins"`, narrowed to the statement's FIRST name,
    reporting the statement's line instead of the name's, or printing
    `from None import exec` for a relative one. All four are asserted here on
    trees the plants do not contain, which is the shape
    `test_the_launch_site_derivation_is_proved_to_derive` uses for the
    same reason.
    """
    # 1. From ANY module. A re-export under the SAME name is exactly what a
    # module check would wave through. The equivalent collision to the one
    # `_CLOCK_MODULES` protects (`from re import compile`) does exist here
    # and is accepted under `_code_from_data`'s NOISY bound rather than
    # narrowed around.
    reexport = _code_from_data(ast.parse("from mytools import exec\n"))
    assert reexport == [(1, "from mytools import exec")], (
        f"a re-export reports {reexport} — the import arm no longer matches "
        "`from mytools import exec`, the same builtin one hop away, so the "
        "docstring's 'from any module' is not true")
    assert _code_from_data(ast.parse("from re import compile\n")) == [
        (1, "from re import compile")], (
        "the accepted collision stopped firing, so the NOISY bound the "
        "docstring states is describing a width the arm no longer has")

    # 2. EVERY name in the statement, not the first. `from builtins import
    # id as _i, exec as _e` reports nothing under `node.names[:1]`.
    assert _code_from_data(
        ast.parse("from builtins import id as _i, exec as _e\n")) == [
        (1, "from builtins import exec")], (
        "a from-import binding several names is read past its first one, so "
        "hiding the builtin behind an innocent name in the same statement is "
        "enough to get it through")

    # 3. The NAME's line, not the statement's. A parenthesised import puts
    # each name on its own line, and the bound promises the failure names the
    # line an author has to go and edit.
    assert _code_from_data(ast.parse(
        "from builtins import (\n    exec,\n    eval,\n)\n")) == [
        (2, "from builtins import exec"), (3, "from builtins import eval")], (
        "a parenthesised import reports the statement's line for every name, "
        "so the failure sends an author to the wrong line of a long import")

    # 4. A RELATIVE from-import has no module name. The spelling must still
    # read as an import an author can go and find, not as
    # `from None import exec`. `_clock_reads`'s star arm still prints
    # `from None import *` for a relative star — a message defect in a guard
    # that does fire, and out of scope for this test.
    relative = _code_from_data(ast.parse("from . import exec\n"))
    assert relative == [(1, "from . import exec")], (
        f"a relative from-import reports {relative}, so the spelling a "
        "failure prints does not name an import an author could find")
    assert _code_from_data(ast.parse("from .pkg import eval\n")) == [
        (1, "from .pkg import eval")]


def test_the_launch_site_derivation_is_proved_to_derive():
    """`_launched_programs` needs a control: its body replaced by a hard-coded
    `["_CHILD", "_COLLIDER"]` would otherwise leave the suite green. So the
    derivation is exercised on trees this
    module does not contain: a third program, an inline program, a missing
    argument, and no launch at all."""
    tree = ast.parse(
        "_race(repo, tmp_path, _THIRD, ['d' * 40], tag='third')\n"
        "_race(repo, tmp_path, _CHILD + 'x', heads, tag='inline')\n"
        "_race(repo, tmp_path)\n"
        "_race(repo, tmp_path, source=_FIFTH, per_child=heads, tag='kw')\n"
        "runner = _race\n"
        "runner(repo, tmp_path, _FOURTH, heads, tag='alias')\n")
    # The keyword spelling is legitimate under _race's own signature; deriving
    # the sentinel for it would send an author hunting for an inline program
    # that does not exist.
    assert _launched_programs(tree) == [
        "_THIRD", "_CHILD + 'x'", "<_race called with no child-program argument>",
        "_FIFTH"]
    assert _launched_programs(ast.parse("x = 1")) == []


def test_every_use_of_subprocess_in_this_file_is_at_a_declared_site():
    """"This file launches its children exactly one way", as a sentence, is a
    claim; this is the check.

    Every use of the `subprocess` module in this file is inside a function
    `_SUBPROCESS_SITES` names, and every name it holds is used. `_race` is
    the one that launches child programs and scans them on the way in; the
    other two run git and the warden CLI. A new function that reaches for
    subprocess — through `Popen`, `run`, a from-import, any spelling — is
    refused by its NAME, and the author either routes the launch through
    `_race` or adds the name here, where a reviewer sees it.
    """
    tree = ast.parse(Path(__file__).read_text())
    uses = _subprocess_uses(tree)
    assert uses, (
        "no use of subprocess anywhere in this file, so nothing here launches "
        "a real process — the races that are this file's subject are gone, "
        "or they launch some way this scan does not see")
    strays = [(line, scope, what) for line, scope, what in uses
              if scope not in _SUBPROCESS_SITES]
    assert not strays, (
        "subprocess is used outside the functions _SUBPROCESS_SITES declares:\n"
        + "\n".join(f"  line {line} in {scope}: {what}"
                    for line, scope, what in strays)
        + "\nA child program launched anywhere but _race is not scanned before "
        "it runs in a real process. Route the launch through _race, or "
        "declare the function in _SUBPROCESS_SITES with what it launches")
    unused = sorted(set(_SUBPROCESS_SITES) - {scope for _, scope, _ in uses})
    assert not unused, (
        f"_SUBPROCESS_SITES declares {unused}, which use no subprocess — a "
        "stale declaration is a name a future launch could hide under")
    # A declaration is by NAME, so a second top-level `def _git` that launches
    # something else would inherit the first one's declaration — and ruff's
    # F811 does not fire when the first definition is used before the second.
    # Refused here instead.
    twice = _defined_twice(tree)
    assert not twice, (
        f"{twice} is defined more than once at module level, so a declared "
        "launch site's name covers a second body nobody declared; one name, "
        "one definition")


def test_the_subprocess_scan_is_proved_to_see_every_spelling():
    """The positive control for `_subprocess_uses`: an aliased import, a
    from-import, a use at module level, a use inside a nested function, a
    method and a class-body use, and the module fetched by its name as a
    string all come back attributed to the scope that holds them, in line
    order."""
    tree = ast.parse(
        "import subprocess as sp\n"
        "from subprocess import Popen\n"
        "def _race():\n"
        "    subprocess.Popen(['x'])\n"
        "def helper():\n"
        "    def inner():\n"
        "        return sp.run(['x'])\n"
        "    return subprocess.run(inner())\n"
        "class TestLaunch:\n"
        "    def test_x(self):\n"
        "        return subprocess.run(['x'])\n"
        "    marker = subprocess\n"
        "def dyn():\n"
        "    return __import__('subprocess')\n"
        "top = subprocess\n")
    # Line 8 is visited BEFORE line 7 inside `helper` (ast.walk is
    # breadth-first, and the inner def's body is one level deeper), so this
    # fixture is what pins the sort rather than riding on walk order.
    assert _subprocess_uses(tree) == [
        (2, "<module>", "from subprocess import Popen"),
        (4, "_race", "subprocess"),
        (7, "helper", "sp"),
        (8, "helper", "subprocess"),
        (11, "TestLaunch.test_x", "subprocess"),
        (12, "TestLaunch", "subprocess"),
        (14, "dyn", '"subprocess"'),
        (15, "<module>", "subprocess"),
    ]
    assert _subprocess_uses(ast.parse("import subprocess\nx = 1")) == [], (
        "the import statement is a binding, not a use, and must not be "
        "reported — or every file that imports subprocess is a finding")

    # and a redefinition of a declared site is seen — by the site guard, which
    # refuses it by name
    assert _defined_twice(ast.parse(
        "def _git(): pass\nclass C: pass\ndef _git(): pass\n"
        "class C: pass\ndef once(): pass\n")) == ["C", "_git"]
    assert _defined_twice(ast.parse("def a(): pass\ndef b(): pass\n")) == []


# One program per name in `_CODE_FROM_DATA`, in EVERY shape the refusal has to
# match — a BARE name, an ATTRIBUTE of the builtins module, the name as a
# STRING (how every dynamic fetch spells it), the same as BYTES, and a
# FROM-IMPORT. The full cross product, and the control below requires it as
# PAIRS: adding a name or an arm without the plants that go with it is how a
# spelling in this family escapes. Module level so the coverage test
# below reaches the same tuple the live control uses, and a TUPLE rather than
# a str so `_shipped_programs` does not take the control's own plants for
# shipped child programs — the shape `_CAUGHT_PROGRAMS` uses for the same
# reason.
#
# Three of the five shapes each escape a scan without the matching arm,
# returning a live `time.time()` from a real process with every other guard in
# this file green: `builtins.exec(argv)`, `from builtins import exec as _e`,
# and `getattr(builtins, b"exec".decode())(argv)`. Each is the same
# one-spelling mistake `_subprocess_uses` guards against for its own module.
_CODE_FROM_DATA_PLANTS = (
    ("exec", "bare", "import sys\nexec(sys.argv[3])\n"),
    ("eval", "bare", "import sys\nv = eval(sys.argv[3])\n"),
    ("compile", "bare",
     'import sys\nc = compile(sys.argv[3], "x", "exec")\n'),
    ("exec", "attribute", "import builtins, sys\nbuiltins.exec(sys.argv[3])\n"),
    ("eval", "attribute",
     "import builtins, sys\nv = builtins.eval(sys.argv[3])\n"),
    ("compile", "attribute",
     'import builtins, sys\nc = builtins.compile(sys.argv[3], "x", "exec")\n'),
    ("exec", "string", 'import sys\n__builtins__["exec"](sys.argv[3])\n'),
    ("eval", "string",
     'import builtins, sys\nv = getattr(builtins, "eval")(sys.argv[3])\n'),
    ("compile", "string",
     'import builtins, sys\n'
     'c = getattr(builtins, "compile")(sys.argv[3], "x", "exec")\n'),
    ("exec", "from-import",
     "import sys\nfrom builtins import exec as _e\n_e(sys.argv[3])\n"),
    ("eval", "from-import",
     "import sys\nfrom builtins import eval as _v\nv = _v(sys.argv[3])\n"),
    ("compile", "from-import",
     'import sys\nfrom builtins import compile as _c\n'
     'c = _c(sys.argv[3], "x", "exec")\n'),
    ("exec", "bytes",
     'import builtins, sys\n'
     'getattr(builtins, b"exec".decode())(sys.argv[3])\n'),
    ("eval", "bytes",
     'import builtins, sys\n'
     'v = getattr(builtins, b"eval".decode())(sys.argv[3])\n'),
    ("compile", "bytes",
     'import builtins, sys\n'
     'c = getattr(builtins, b"compile".decode())(sys.argv[3], "x", "exec")\n'),
)


def test_race_refuses_a_program_that_reads_a_clock_before_launching_it(tmp_path):
    """The launch-path scan, watched biting.

    A program handed to `_race` under any name — here a copy of `_CHILD`
    extended by concatenation, launched through an alias — is refused before
    the script is written or a process spawned. The static guard cannot see a
    local string or an aliased call; this is what closes both without
    recognising either.
    """
    planted = _CHILD + "\nt0 = time.time()\n"
    runner = _race
    with pytest.raises(AssertionError, match=r"refusing to launch.*\.time"):
        runner(tmp_path, tmp_path, planted, ["d" * 40], tag="planted")
    assert not (tmp_path / "planted.py").exists(), (
        "the child script was written before the program was scanned, so a "
        "refused program still reached the disk a process would run it from")
    assert not (tmp_path / "planted.go").exists()

    # A program that does not parse cannot be scanned, and is refused by a
    # message that names the tag and the line — not a bare SyntaxError from
    # `<unknown>`.
    with pytest.raises(AssertionError, match=r"broken: .*does not parse.*line 1"):
        runner(tmp_path, tmp_path, "def (\n", ["d" * 40], tag="broken")
    assert not (tmp_path / "broken.py").exists()

    # A program that would run its ARGV as code carries no reading the scan
    # can see; the reading arrives as data. Refused by the name that turns
    # data into code — in every shape that names the builtin, because a
    # bare-name-only match lets `builtins.exec(argv)` through and a real child
    # returns a live clock reading with the file green.
    for name, shape, program in _CODE_FROM_DATA_PLANTS:
        tag = f"{name}-{shape}"
        with pytest.raises(
                AssertionError,
                match=rf"{tag}: .*builds code from data.*{name}"):
            runner(tmp_path, tmp_path, program,
                   ["import time; t = time.time()"], tag=tag)
        assert not (tmp_path / f"{tag}.py").exists(), (
            f"the {shape} spelling of {name} was written to disk before the "
            "program was refused")


def test_a_star_import_binds_a_name_that_still_has_to_be_spelled(tmp_path):
    """The one spelling `_code_from_data`'s bound declines to grow an arm for.

    `from builtins import *` binds `exec` while reporting nothing here: the
    alias name is the literal `*`, which is in no set. The bound above says
    that is safe for two reasons, and a reason nothing executes is exactly the
    prose this file's first paragraph distrusts — so both are run.

    Launched through an alias of `_race` for the reason
    `test_race_refuses_a_program_that_reads_a_clock_before_launching_it`
    is: a literal `_race(...)` call site naming an inline program is refused
    by the launch cross-check, and a module-level string holding this one
    would be a star-import in this module's own scan.
    """
    runner = _race
    # Invisible to THIS check, by name — the alias is `*` and matches nothing.
    assert _code_from_data(ast.parse("from builtins import *\n")) == []

    # Refused anyway, before the script is written or a process spawned, by
    # the sibling guard's star arm — which fires whatever module it is from.
    with pytest.raises(
            AssertionError,
            match=r"star: refusing to launch.*from builtins import \*"):
        runner(tmp_path, tmp_path, "from builtins import *\n", ["d" * 40],
               tag="star")
    assert not (tmp_path / "star.py").exists(), (
        "the star-import program reached the disk a process would run it from"
    )

    # And a program that USES what the star bound spells the name, which the
    # bare-Name arm catches. That is the half that
    # makes the star import a non-escape rather than an unclosed one.
    assert _code_from_data(
        ast.parse("from builtins import *\nexec(x)\n")) == [(2, "exec")]
