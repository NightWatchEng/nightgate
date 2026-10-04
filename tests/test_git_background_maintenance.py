"""No git this suite runs against a repo it will delete may fork a background
writer into it.

Worded that way on purpose. The blunter "no git this suite runs forks a
background writer" is false of this module itself — the control leg below runs
an unsuppressed commit deliberately, into a repo it does not delete — and a
guard whose first sentence is false of its own body teaches a reader to skim
the rest.

THE DEFECT THIS HOLDS CLOSED. Since git 2.31 a plain `git commit` ends by
forking `git maintenance run --auto --quiet --detach`, which takes
`$GIT_DIR/objects/maintenance.lock`. The child outlives the commit, so a test
that commits into a throwaway repo and then deletes the repo is racing a
process it never started: `shutil.rmtree` lists `.git/objects`, the detached
git removes its lock, and the unlink dies with

    FileNotFoundError: [Errno 2] No such file or directory: 'maintenance.lock'

That is a test-hermeticity defect, not a product one, and it is latent in every
one of the hundreds of throwaway repos this suite builds. Load decides whether
it fires — it surfaced on a loaded CI runner under `-n auto` after ten serial
runs stayed green, in `tests/test_memory_watch.py`, which is one call site of a
shape this tree has everywhere. So the fix is in `conftest._NO_BACKGROUND_GIT`
rather than at that call site, and this module is what makes the fix the thing
that holds rather than the comment beside it.

WHY TRACE2 AND NOT A TIMING ASSERTION. The obvious regression test is the
failing shape — init, commit, rmtree, assert no raise. On a quiet machine that
test passes with the fix reverted, because the race it names is a race: it
would ship green and prove nothing, which is the worst kind of guard. Git's
trace2 event stream records `child_start` in the PARENT before the parent
exits, so "was a background process spawned at all" is answerable
synchronously, by reading a file, with no timing in it. `test_a_commit_in_a_
fixture_repo_forks_no_background_maintenance` is therefore red on a revert
every time it runs, on any machine, at any load.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

from conftest import _NO_BACKGROUND_GIT


def _repo(root: Path) -> Path:
    """An empty throwaway repo, started the way the suite starts them.

    Branch pinned because a bare `git init` reads `init.defaultBranch`, which
    differs between a developer's machine and the runner. The commit — and the
    identity it needs, which `-c` supplies because the config layers are empty
    for the session — belongs to `_commit`, not here.
    """
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True,
                   capture_output=True)
    return root


def _children(trace: Path) -> list[list[str]]:
    """Every child git recorded as started in `trace`, as argv lists.

    ONE derivation for both callers. A push and the `git-receive-pack` it
    spawns both inherit `GIT_TRACE2_EVENT` and append to the same file, so a
    torn line is possible; a bare `json.loads` per line answers that with a
    ValueError traceback instead of the test's own message, which is the
    failure a reader can do nothing with. Skipping what will not parse costs
    nothing — a torn `child_start` that is dropped can only make the control
    leg fail, never the guarded one pass.
    """
    out = []
    for line in trace.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "child_start":
            out.append(event.get("argv") or [])
    return out


def _commit(root: Path, message: str, env: dict[str, str],
            trace: Path) -> list[list[str]]:
    """Commit into `root` under `env`, returning every child git spawned.

    The trace file is the whole point: `child_start` is written by the process
    doing the forking, at the moment it forks, so this returns a fact about
    what the commit DID rather than about what happened to still be running
    when the test looked.
    """
    subprocess.run(
        ["git", "-c", "user.email=fixture@example.invalid",
         "-c", "user.name=fixture", "commit", "-q", "--allow-empty",
         "-m", message],
        cwd=root, check=True, capture_output=True,
        env={**env, "GIT_TRACE2_EVENT": str(trace)})
    return _children(trace)


def test_a_commit_in_a_fixture_repo_forks_no_background_maintenance(tmp_path):
    """A fixture repo's commit spawns no detached maintenance, and this git
    would have.

    BOTH HALVES OR NEITHER. The first assertion alone is satisfied by a git
    that stopped auto-maintaining, by a commit that failed quietly, or by a
    trace file nobody wrote — three ways to be green while proving nothing.
    The control half runs the identical commit with the session's overrides
    stripped back off and requires the maintenance child to appear, so the
    guard only makes a claim on a git that still has the behaviour being
    suppressed.

    The control env is built by REMOVING keys rather than by naming them, so
    dropping or renaming a key in the one derivation cannot leave a
    hand-typed copy here still claiming the old spelling is what is tested.

    IT REMOVES THE SUPPRESSION ONLY, never the config-layer neutralisation.
    Stripping the whole of `_AMBIENT_GIT_CONFIG` would hand the control commit
    the developer's `~/.gitconfig` and the host `/etc/gitconfig`, and a machine
    whose global config carries `gc.auto = 0` — an ordinary way to silence
    git's nagging, and something a corporate `/etc/gitconfig` can set for
    everyone — would then see the control fork nothing and fail with a message
    blaming the git version. That is the ambient-config leak in a control leg:
    green or red decided by the machine, and a failure that sends the reader to
    the wrong problem.

    NOT COVERED BY THE CLOSED-ENV SCAN, said here because the next editor will
    assume it is. `tests/test_ambient_git.py`'s omission guard walks `ast.Dict`,
    so a comprehension is invisible to it; what keeps this hermetic is that it
    DERIVES from `os.environ` rather than naming keys. An edit that turns it
    into a dict literal, or that drops the derivation, loses that for free and
    nothing in the tree reports it.
    """
    leaky = {k: v for k, v in os.environ.items()
             if k not in _NO_BACKGROUND_GIT}

    control = _commit(_repo(tmp_path / "control"), "control",
                      leaky, tmp_path / "control.trace")
    assert any("maintenance" in argv for argv in control), (
        "this git no longer forks background maintenance from a commit, so "
        "the assertion below can no longer fail and this module has stopped "
        "guarding anything. Children seen: "
        f"{control!r}")

    guarded = _commit(_repo(tmp_path / "guarded"), "guarded",
                      dict(os.environ), tmp_path / "guarded.trace")
    assert not any("maintenance" in argv for argv in guarded), (
        "a commit in a fixture repo forked a detached maintenance process, "
        "which takes .git/objects/maintenance.lock and outlives the commit. "
        "Every test that deletes its own repo after committing is now racing "
        "it, and rmtree dies on the vanishing lock under load. Children "
        f"seen: {guarded!r}")


def test_a_fixture_repo_cannot_opt_back_into_a_background_writer(tmp_path):
    """The suppression outranks anything a fixture writes into its own repo.

    Not a restatement of the test above. That one proves the behaviour is off
    for a repo built under the session env; this one proves it stays off for a
    repo that asks for it back — which is what makes the fix a property of the
    suite rather than of the fixtures that happen to exist today. The
    `GIT_CONFIG_COUNT` layer git reads last is the only place these keys can
    sit and still beat a repository config, so a future move to a weaker layer
    reddens here rather than years later on a loaded runner.

    Read through `git config`, which resolves the layers the way the git that
    forks does, not by inspecting an environment this test could also have
    written.
    """
    root = _repo(tmp_path / "repo")
    subprocess.run(["git", "config", "maintenance.auto", "true"],
                   cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "gc.auto", "6700"],
                   cwd=root, check=True, capture_output=True)

    for key, expected in (("maintenance.auto", "false"), ("gc.auto", "0")):
        got = subprocess.run(["git", "config", "--get", key], cwd=root,
                             check=True, capture_output=True, text=True)
        assert got.stdout.strip() == expected, (
            f"a fixture repo's effective {key} is {got.stdout.strip()!r}, not "
            f"{expected!r} — its own repository config won, so any fixture "
            "that sets this key hands itself back the background writer the "
            "suite suppresses for everyone")


def test_a_push_into_a_second_fixture_repo_needs_that_repos_own_config(tmp_path):
    """The env layer does NOT cross into a repo git spawns into, and the
    repository layer does.

    `GIT_CONFIG_COUNT` and its keys are in git's `local_repo_env`: git strips
    them when it hands an environment to a child working on a DIFFERENT
    repository, which is exactly what `git push <local path>` does to
    `git-receive-pack`. So a fixture that pushes into a second repo and then
    deletes that repo is back in the race the session env appears to have
    closed — the appearance is the danger, because the env spread is right
    there in the fixture and reads as covering it.

    Both halves measured, so neither the limit nor the remedy is asserted from
    the documentation: the receiving repo with no config of its own gets a
    detached maintenance child, and the same push into a repo carrying
    `maintenance.auto=false` gets none. `tests/test_cage_runner.py` is the one
    fixture in this tree with the shape;
    `test_the_one_fixture_that_pushes_into_a_repo_it_deletes_writes_that_config`
    is what holds it to writing the config, because this test cannot — it
    measures the property on its own repos, and a property proved on synthetic
    repos says nothing about whether the live fixture still applies it.
    """
    def push_children(dest: Path, configure: bool) -> list[list[str]]:
        source = _repo(dest.parent / f"{dest.name}-source")
        _commit(source, "seed", dict(os.environ), dest.parent / f"{dest.name}.s")
        subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(dest)],
                       cwd=dest.parent, check=True, capture_output=True)
        if configure:
            subprocess.run(["git", "config", "maintenance.auto", "false"],
                           cwd=dest, check=True, capture_output=True)
        trace = dest.parent / f"{dest.name}.trace"
        subprocess.run(["git", "push", "-q", str(dest), "main"], cwd=source,
                       check=True, capture_output=True,
                       env={**os.environ, "GIT_TRACE2_EVENT": str(trace)})
        return _children(trace)

    bare = tmp_path / "bare"
    bare.mkdir()
    unconfigured = push_children(bare / "plain", configure=False)
    assert any("maintenance" in argv for argv in unconfigured), (
        "a push into a plain second repo no longer forks maintenance there, "
        "so the repository-layer config the cage fixture writes is no longer "
        f"buying anything and can be dropped. Children seen: {unconfigured!r}")

    configured = push_children(bare / "configured", configure=True)
    assert not any("maintenance" in argv for argv in configured), (
        "writing maintenance.auto=false into the RECEIVING repo did not stop "
        "the push forking maintenance into it, so the remedy the cage fixture "
        f"uses does not work. Children seen: {configured!r}")


def test_the_one_fixture_that_pushes_into_a_repo_it_deletes_writes_that_config():
    """`test_cage_runner`'s `_seed_repo` configures its origin, before pushing.

    WHY A SOURCE SCAN. The test above proves the PROPERTY — that the env layer
    does not cross a push and the repository layer does — on repos it builds
    itself. It cannot prove the live fixture still applies it, and measurement
    says the gap is real: deleting both `config` calls from `_seed_repo` leaves
    the whole suite green, so the one place in this tree that actually has the
    dangerous shape was protected by a comment. That is the class this repo
    keeps finding — a repair nothing reddens on a revert — and the same answer
    `tests/test_ambient_git.py` gives for the closed-env spreads works here:
    read the source and require the call.

    ORDER IS PART OF THE CLAIM. A config written AFTER the push is written
    after the receiving git has already forked, so the assertion is not just
    that the lines exist but that they precede the push that needs them.

    SCOPED TO `_seed_repo` by construction: the fixture builds its origin
    there and nowhere else, so a scan of the whole module would go green on a
    config call in an unrelated method.
    """
    source = (Path(__file__).parent / "test_cage_runner.py").read_text()
    tree = ast.parse(source)
    seed = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_seed_repo"),
                None)
    assert seed is not None, (
        "tests/test_cage_runner.py no longer has a `_seed_repo` — this guard "
        "has lost its subject and is passing on nothing. Find where the cage "
        "fixture now builds its origin and repoint it")

    calls = [ast.unparse(n) for n in ast.walk(seed) if isinstance(n, ast.Call)]
    configured = [c for c in calls
                  if "self.origin" in c and "'config'" in c
                  and "'maintenance.auto'" in c and "'false'" in c]
    assert configured, (
        "the cage fixture's `_seed_repo` no longer writes maintenance.auto="
        "false into the bare origin it pushes to. git strips GIT_CONFIG_* "
        "crossing into another repository, so the session's suppression does "
        "NOT reach the git-receive-pack behind that push: the origin gets a "
        "detached maintenance process, and "
        "test_an_unreachable_remote_stops_the_run rmtree's that origin as its "
        "first statement. Calls seen in _seed_repo:\n  " + "\n  ".join(calls))

    pushes = [i for i, c in enumerate(calls) if "'push'" in c]
    assert pushes, (
        "`_seed_repo` no longer pushes, so this guard is asserting a remedy "
        "for a shape the fixture has stopped having — check whether the "
        "config write is still needed at all")
    assert calls.index(configured[0]) < pushes[0], (
        "the origin's maintenance.auto is written AFTER the push that needs "
        "it, so the receiving git has already forked by the time the config "
        "lands. Move the config write above the push")


def test_deleting_a_fixture_repo_straight_after_a_commit_completes(tmp_path):
    """The shape that failed, end to end: init, commit, remove the repo.

    HONEST ABOUT WHAT IT IS. This asserts an absence of a race, so on a quiet
    machine it passes with the suppression reverted — it cannot be the guard,
    and it is not counted as one. It is here because the two tests above check
    a mechanism and a config layer, and neither runs the sequence a caller
    actually writes; if some future change makes this sequence fail for a
    reason that is not the race at all, nothing else in this module would
    notice. The test that bites on a revert is the trace2 one above.
    """
    root = _repo(tmp_path / "repo")
    _commit(root, "work", dict(os.environ), tmp_path / "trace")

    shutil.rmtree(root / ".git")

    assert not (root / ".git").exists()
