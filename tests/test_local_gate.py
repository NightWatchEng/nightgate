"""The local DoD gate is a machine-local fact, so warden reports it.

A clone's `core.hooksPath` can be repointed — at `.beads/hooks`, say — so
that `.githooks/pre-push` (ruff plus the full suite) never runs, and
`.githooks/commit-msg` never runs either, because beads ships no commit-msg
hook and `.githooks` holds the only one. Both failures are silent, and a
clone can stay that way indefinitely.

No repo artifact can assert this and no ladder check can either: the value is
per-clone, and a certify rung keyed on it would fail in CI where hooks do not
apply. Reporting it in the orientation brief is the honest middle.
"""

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import toolchain
import yaml

from warden.explain import local_gate_state

ROOT = Path(__file__).resolve().parents[1]


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path


def test_unset_hookspath_reports_not_enabled(repo):
    state, detail = local_gate_state(repo)
    assert state == "unset"
    assert "git config core.hooksPath .githooks" in detail


def test_githooks_reports_enabled(repo):
    _git(repo, "config", "core.hooksPath", ".githooks")
    state, detail = local_gate_state(repo)
    assert state == "enabled"
    assert ".githooks" in detail


def test_trailing_slash_still_counts_as_enabled(repo):
    _git(repo, "config", "core.hooksPath", ".githooks/")
    assert local_gate_state(repo)[0] == "enabled"


def test_absolute_path_to_githooks_counts_as_enabled(repo):
    _git(repo, "config", "core.hooksPath", str(repo / ".githooks"))
    assert local_gate_state(repo)[0] == "enabled"


def test_hookspath_pointed_elsewhere_reports_bypassed(repo):
    """The real failure: not absent, but pointed somewhere plausible.

    `.beads/hooks` looks like hooks are configured. They are — just not the
    ones carrying the Definition of Done.
    """
    _git(repo, "config", "core.hooksPath", ".beads/hooks")
    state, detail = local_gate_state(repo)
    assert state == "bypassed"
    assert ".beads/hooks" in detail
    assert "NOT running" in detail
    assert "git config core.hooksPath .githooks" in detail


def test_brief_shouts_when_the_gate_is_bypassed(monkeypatch):
    """A bypassed gate must be loud in the actual brief, not a quiet line.

    Renders the real repo's brief with the gate state forced, so this asserts
    on render() output rather than on a lookup table beside it.
    """
    import warden.config as config_mod
    import warden.explain as explain

    monkeypatch.setattr(explain, "local_gate_state",
                        lambda root: ("bypassed", "core.hooksPath is `.beads/hooks`"))
    brief = explain.render(config_mod.load(ROOT))

    assert "## Local gate" in brief
    assert "BYPASSED" in brief
    assert "CI is the fence; this is the local one, and it is off." in brief


def test_brief_stays_quiet_when_the_gate_is_enabled(monkeypatch):
    import warden.config as config_mod
    import warden.explain as explain

    monkeypatch.setattr(explain, "local_gate_state",
                        lambda root: ("enabled", ".githooks"))
    brief = explain.render(config_mod.load(ROOT))

    assert "enabled: .githooks" in brief
    assert "BYPASSED" not in brief
    assert "CI is the fence" not in brief


# ---------- the hook itself: deletions push no code -------------------------
#
# A branch deletion sends an all-zero local sha; running ruff plus the full
# suite gates a push that contains nothing. The skip must cover ONLY that
# case — a hook that skips more than deletions is a gate that looks like it
# is running and is not (fail closed on empty stdin, mixed pushes run).

HOOK = ROOT / ".githooks" / "pre-push"
ZERO = "0" * 40
REAL = "a" * 40


@pytest.fixture
def hook_repo(tmp_path):
    """A throwaway repo with the REAL hook and a stubbed `uv` that records
    invocations instead of running the actual gates."""
    _git(tmp_path, "init", "-q", "-b", "main")
    hooks = tmp_path / ".githooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_bytes(HOOK.read_bytes())
    hook.chmod(0o755)
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    uv = stub_bin / "uv"
    # The stub answers two different questions the hook asks it. Any argv
    # mentioning `tomllib` is the hook reading the DECLARED toolchain out of
    # pyproject.toml, and the answer is whatever the cell put in
    # STUB_DECLARED (default: `uv` itself, which is on PATH here, so the
    # default arm is the strict one). Every other argv is a gate command, and
    # what it records now includes the strictness variable's value, because
    # that variable is passed through the ENVIRONMENT and an argv log cannot
    # see it — the exact blindness that let the hook and CI diverge.
    uv.write_text(
        "#!/bin/sh\n"
        "echo \"uv $@ [NIGHTGATE_REQUIRE_TOOLCHAIN="
        "${NIGHTGATE_REQUIRE_TOOLCHAIN-unset}]\" >> \"$UV_CALL_LOG\"\n"
        "case \"$*\" in *tomllib*) printf '%s' \"${STUB_DECLARED-uv}\" ;; esac\n"
        # The hook's artifact question goes to the REAL warden, so the cells
        # below judge the shipped check rather than a stub that says yes. In
        # a fixture with no repo.yaml it exits 2, and the hook runs the gates.
        "case \"$*\" in *--check-head*)\n"
        "  while [ \"$1\" != warden ]; do shift; done; shift\n"
        "  exec \"$STUB_PY\" -m warden \"$@\" ;;\n"
        "esac\n"
        "exit 0\n")
    uv.chmod(0o755)
    return tmp_path


def run_hook(repo: Path, stdin: str,
             **extra_env: str) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ,
               PATH=f"{repo / 'stub-bin'}:{os.environ['PATH']}",
               UV_CALL_LOG=str(repo / "uv-calls.log"),
               STUB_PY=sys.executable,
               **extra_env)
    # The variable under test is INHERITED, which is the whole reason it could
    # diverge unnoticed: the hook now exports it for the suite, so this suite's
    # own environment already carries it and a nested run would read the
    # parent's value as if the hook had decided it. Caught by the pre-push hook
    # on the very commit that introduced it. Cleared here so each cell below
    # observes what the HOOK set and nothing else. Both spellings: the old
    # name is still read as an alias until v4.0.0 (tests/toolchain.py).
    env.pop("NIGHTGATE_REQUIRE_TOOLCHAIN", None)
    env.pop("AGENTOPS_REQUIRE_TOOLCHAIN", None)
    return subprocess.run(["sh", str(repo / ".githooks" / "pre-push"),
                           "origin", "git@example:x.git"],
                          input=stdin, cwd=repo, env=env,
                          capture_output=True, text=True)


def uv_calls(repo) -> list[str]:
    log = repo / "uv-calls.log"
    return log.read_text().splitlines() if log.exists() else []


def test_deletion_only_push_skips_the_gates(hook_repo):
    """A deletion-only push runs neither gate: a push containing no code has
    nothing to lint or test, and each branch deletion would otherwise run the
    full suite."""
    proc = run_hook(hook_repo,
                    f"refs/heads/dead {ZERO} refs/heads/dead {REAL}\n")
    assert proc.returncode == 0
    assert "nothing to gate" in proc.stdout
    assert uv_calls(hook_repo) == [], "a deletion-only push must not run gates"


def test_normal_push_still_runs_both_gates(hook_repo):
    proc = run_hook(hook_repo,
                    f"refs/heads/main {REAL} refs/heads/main {ZERO}\n")
    assert proc.returncode == 0
    calls = uv_calls(hook_repo)
    assert any("ruff" in c for c in calls) and any("pytest" in c for c in calls)
    assert "all gates green" in proc.stdout


def test_mixed_push_runs_the_gates(hook_repo):
    """One real ref among deletions is code leaving the machine — gate it."""
    proc = run_hook(hook_repo,
                    f"refs/heads/dead {ZERO} refs/heads/dead {REAL}\n"
                    f"refs/heads/feat {REAL} refs/heads/feat {ZERO}\n")
    assert proc.returncode == 0
    assert uv_calls(hook_repo) != []


def test_empty_stdin_runs_the_gates(hook_repo):
    """Fail closed: no ref lines is not proof of a deletion — a hook that
    skips more than deletions is the same defect class as every other gate
    that looks like it is running and is not."""
    proc = run_hook(hook_repo, "")
    assert proc.returncode == 0
    assert uv_calls(hook_repo) != []


def test_beads_hook_still_receives_the_refs_on_stdin(hook_repo):
    """The chained beads hook consumes stdin too; reading the refs in
    .githooks/pre-push must not starve it."""
    beads = hook_repo / ".beads" / "hooks"
    beads.mkdir(parents=True)
    bh = beads / "pre-push"
    bh.write_text("#!/bin/sh\ncat > beads-got.txt\nexit 0\n")
    bh.chmod(0o755)
    line = f"refs/heads/main {REAL} refs/heads/main {ZERO}\n"
    proc = run_hook(hook_repo, line)
    assert proc.returncode == 0
    assert (hook_repo / "beads-got.txt").read_text() == line


def test_beads_hook_gets_an_empty_stream_on_empty_stdin(hook_repo):
    """Round finding (hook-stdin-fidelity): an up-to-date push runs pre-push
    with 0-byte stdin, and printf '%s\\n' "" would hand the beads hook one
    blank ref line where git gave it EOF — a malformed entry its parser
    never saw before. Byte fidelity, both directions."""
    beads = hook_repo / ".beads" / "hooks"
    beads.mkdir(parents=True)
    bh = beads / "pre-push"
    bh.write_text("#!/bin/sh\ncat > beads-got.txt\nexit 0\n")
    bh.chmod(0o755)
    proc = run_hook(hook_repo, "")
    assert proc.returncode == 0
    assert (hook_repo / "beads-got.txt").read_text() == ""


PUSH = f"refs/heads/main {REAL} refs/heads/main {ZERO}\n"


def _hook_pytest_call(repo: Path) -> str:
    calls = [c for c in uv_calls(repo) if "pytest" in c]
    assert len(calls) == 1, f"expected one pytest invocation, got {calls}"
    return calls[0]


def test_the_hook_runs_the_suite_in_cis_environment_when_it_can(hook_repo):
    """The gap this closes, and the direction that matters.

    The CI tests job sets `NIGHTGATE_REQUIRE_TOOLCHAIN=1` on its pytest step
    and `repo.yaml`'s `tests` scope sets it inline, so both CI gates run the
    suite STRICT while this hook ran it LENIENT. The cells that shell out to
    `go` and `npm` therefore skipped on a laptop and failed on a runner: a
    push could be green here and red there with the code identical, which
    happened twice in one session.

    The flags were already held in lockstep across the callers; the
    environment was not, and nothing looked at it — an argv log cannot see a
    variable passed through the environment, which is why the stub records
    its value rather than the command alone.
    """
    proc = run_hook(hook_repo, PUSH, STUB_DECLARED="uv")
    assert proc.returncode == 0, proc.stderr
    assert "NIGHTGATE_REQUIRE_TOOLCHAIN=1" in _hook_pytest_call(hook_repo), (
        "the local gate runs the suite without the strictness CI applies, so "
        "the toolchain cells skip here and fail there")
    assert "strict toolchain" in proc.stdout


def test_a_machine_that_promised_nothing_stays_lenient_and_says_so(hook_repo):
    """The half that must NOT become strict.

    `tests/toolchain.py` decides skip-or-fail by CONTRACT, not by tool: a
    runner installs every declared binary with a pinned action and then
    promises them, so an absence there is a breach. A contributor's fresh Mac
    has neither `go` nor `node` and is not broken — it promised nothing. So
    the hook makes CI's promise only where the machine can keep it, and where
    it cannot it NAMES what this run did not prove instead of letting CI find
    out. A hook that failed a first clone would be the first thing this
    project says to a newcomer.
    """
    proc = run_hook(hook_repo, PUSH,
                    STUB_DECLARED="uv\nno-such-toolchain-binary")
    assert proc.returncode == 0, proc.stderr
    call = _hook_pytest_call(hook_repo)
    assert "NIGHTGATE_REQUIRE_TOOLCHAIN=unset" in call, (
        f"a machine missing a declared binary is being held to CI's contract "
        f"anyway, so a first clone fails its own gate: {call!r}")
    assert "no-such-toolchain-binary" in proc.stdout, (
        "the lenient run does not NAME the absent binary, so the gap between "
        "this gate and CI's is silent again — which is the whole defect")


def test_a_toolchain_it_cannot_read_stops_the_push(hook_repo):
    """Fail closed on the read itself.

    "I could not tell which binaries were declared" and "none are missing"
    are the two answers this hook must never conflate: conflating them is a
    gate that silently returns to the lenient half, which is the state this
    change exists to leave.
    """
    proc = run_hook(hook_repo, PUSH, STUB_DECLARED="")
    assert proc.returncode == 1, proc.stdout
    assert "cannot read" in proc.stdout
    assert not [c for c in uv_calls(hook_repo) if "pytest" in c], (
        "the suite ran anyway, so the refusal is a message rather than a gate")


# ── an artifact that names the pushed head stands for the run
#
# `warden verify --scope tests` runs the gate scope and records the commit it
# judged; the hook re-ran the same suite on push for a verdict already on
# disk. It now asks `warden verify --check-head` — the real one, through the
# stub — and runs the gates on every answer but a clean PASS on the exact sha.


def _verified(hook_repo: Path, sample_repo: Path, cmd: str = "true") -> str:
    """Commit a repo.yaml whose `tests` scope runs `cmd`, run the real
    `warden verify --scope tests` on that commit, and return its sha."""
    import os
    raw = yaml.safe_load((sample_repo / "repo.yaml").read_text())
    raw["verify"] = {"tests": [{"run": cmd}]}
    (hook_repo / "repo.yaml").write_text(yaml.safe_dump(raw))
    (hook_repo / ".gitignore").write_text(
        ".warden/\n.githooks/\nstub-bin/\nuv-calls.log\n")
    _git(hook_repo, "add", ".")
    _git(hook_repo, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "fixture")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    subprocess.run([sys.executable, "-m", "warden", "verify", "--scope", "tests"],
                   cwd=hook_repo, env=env, capture_output=True, text=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=hook_repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def _gates_ran(repo: Path) -> bool:
    calls = uv_calls(repo)
    return any("ruff" in c for c in calls) and any("pytest" in c for c in calls)


def test_an_artifact_for_the_pushed_head_is_accepted(hook_repo, sample_repo):
    sha = _verified(hook_repo, sample_repo)
    proc = run_hook(hook_repo, f"refs/heads/main {sha} refs/heads/main {ZERO}\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not _gates_ran(hook_repo), "a PASS on the pushed head re-ran the suite"
    assert "ACCEPTED" in proc.stdout and "NOT ACCEPTED" not in proc.stdout
    assert "-verify records PASS on " + sha[:12] in proc.stdout, (
        "the hook accepted an artifact without naming which one")


def test_an_artifact_for_another_head_runs_the_gates(hook_repo, sample_repo):
    _verified(hook_repo, sample_repo)
    proc = run_hook(hook_repo, PUSH)
    assert proc.returncode == 0, proc.stderr
    assert "NOT ACCEPTED" in proc.stdout
    assert _gates_ran(hook_repo), "an artifact for another commit stood in"


def test_one_unverified_ref_among_verified_ones_runs_the_gates(
        hook_repo, sample_repo):
    # The unverified sha is the LAST one possible, so the verified commit
    # (its sha varies run to run) is always checked first: a hook that
    # stopped at the first accepted sha, or checked only one, fails here on
    # every run rather than on the runs its sha happened to sort first.
    last = "f" * 40
    sha = _verified(hook_repo, sample_repo)
    proc = run_hook(hook_repo, f"refs/heads/main {sha} refs/heads/main {ZERO}\n"
                               f"refs/heads/other {last} refs/heads/other {ZERO}\n")
    assert ": ACCEPTED — .warden/out/" in proc.stdout, (
        "the verified sha was not checked first, so this cell proves nothing")
    assert proc.returncode == 0, proc.stderr
    assert _gates_ran(hook_repo), "one verified ref stood in for the whole push"


def test_an_artifact_recording_fail_runs_the_gates(hook_repo, sample_repo):
    sha = _verified(hook_repo, sample_repo, cmd="false")
    proc = run_hook(hook_repo, f"refs/heads/main {sha} refs/heads/main {ZERO}\n")
    assert proc.returncode == 0, proc.stderr
    assert "records FAIL" in proc.stdout
    assert _gates_ran(hook_repo), "a FAIL artifact was accepted as a pass"


def test_an_artifact_of_other_commands_runs_the_gates(hook_repo, sample_repo):
    sha = _verified(hook_repo, sample_repo)
    raw = yaml.safe_load((hook_repo / "repo.yaml").read_text())
    raw["verify"]["tests"].append({"run": "echo the scope grew"})
    (hook_repo / "repo.yaml").write_text(yaml.safe_dump(raw))
    proc = run_hook(hook_repo, f"refs/heads/main {sha} refs/heads/main {ZERO}\n")
    assert proc.returncode == 0, proc.stderr
    assert "other commands" in proc.stdout
    assert _gates_ran(hook_repo), (
        "an artifact of fewer commands than the scope declares stood in for it")


SCOPE_SCRIPT = ROOT / "scripts" / "ci-event-scope.sh"


def _event_scope(**env) -> tuple[str, str]:
    """`scripts/ci-event-scope.sh` EXECUTED, with the forge query stubbed.

    Executed rather than read, for the reason this repo learned the hard way
    on D-03: a decision that must be fail-closed cannot be proved by parsing
    the shell that makes it. Every branch below is a real run of the real
    script, and the two query commands exist in it precisely so the forge's
    half can be substituted.

    `GITHUB_OUTPUT` is CLEARED, and that is not tidiness. Under CI this suite
    runs inside a step that has one, the script appends its verdict there by
    design, and every cell below would then write a real `fast=` line into the
    runner's own step output — nine of them, concurrently under `-n auto`.
    Nothing reads that step's outputs today, which makes it contained rather
    than harmless: it is the same ambient-inheritance shape `run_hook` clears
    above, found by the same round.
    """
    import os

    unset = env.pop("_unset", ())
    ambient = dict(os.environ, **env)
    ambient.pop("GITHUB_OUTPUT", None)
    for name in unset:
        if name not in env:
            ambient.pop(name, None)
    proc = subprocess.run(["sh", str(SCOPE_SCRIPT)], capture_output=True,
                          text=True, cwd=ROOT, env=ambient)
    assert proc.returncode == 0, proc.stderr
    verdict = [line for line in proc.stdout.splitlines()
               if line.startswith("fast=")]
    assert len(verdict) == 1, proc.stdout
    return verdict[0].split("=", 1)[1], proc.stdout


GREEN = "printf 2026-09-25T12:00:00Z"      # this sha passed at noon
BASE_OLDER = "printf 2026-09-25T09:00:00Z"  # the base last moved before that
BASE_NEWER = "printf 2026-09-25T15:00:00Z"  # ...and after it


def test_a_retitle_over_an_already_green_sha_reruns_only_the_title_job():
    """The saving, and the only case that earns it.

    `edited` stays in the trigger list — without it a PR retitled after CI
    went green fired no new run, and a stale green satisfied a non-strict
    merge button, which is how an invalid header landed on main permanently.
    What it cost: only `commit messages` reads the title, and the other jobs
    re-built, re-linted and re-ran the whole suite for text none of them looks
    at. Measured over 60 consecutive pull_request runs across 45 distinct head
    shas, 15 were a repeat build of a sha already built and the `edited`
    trigger accounts for 326 RUNNER-MINUTES of that — runner-minutes, not wall
    clock, because the interpreter legs are what a run's wall clock is.
    """
    fast, out = _event_scope(EVENT_NAME="pull_request", ACTION="edited",
                             BASE_CHANGED="false", HEAD_SHA="deadbeef",
                             GREEN_AT_CMD=GREEN, BASE_RUN_AT_CMD=BASE_OLDER)
    assert fast == "true", out


def test_a_retitle_cannot_turn_a_run_that_was_not_green_into_a_green_one():
    """The trap, and it is the reason this decision is a script.

    A job skipped by `if:` publishes its check run as SKIPPED, and branch
    protection reads a skipped required check as a passed one. So a fast path
    is a way to launder a RED run into a mergeable one by editing the title —
    unless it can only be taken where the same sha has already been green.
    Nothing here is about the title's content; the question is whether this
    sha has a green run behind it.
    """
    fast, out = _event_scope(EVENT_NAME="pull_request", ACTION="edited",
                             BASE_CHANGED="false", HEAD_SHA="deadbeef",
                             GREEN_AT_CMD="printf ''",
                             BASE_RUN_AT_CMD=BASE_OLDER)
    assert fast == "false", out
    assert "still has something to prove" in out


def test_a_base_that_moved_since_the_green_run_reruns_everything():
    """The half of the second trap round 1 found open.

    A base-branch change fires an `edited` event; a base-branch COMMIT fires
    none. The merge ref moves either way, so a run that went green against
    yesterday's main says nothing about the same head against today's, and
    keying the fast path on the head sha alone skipped seven jobs on the
    strength of it. The green run must be newer than the base branch's newest
    run, and the boundary case — the two timestamps equal, which cannot
    establish an order — is the full build.
    """
    for base_at, case in ((BASE_NEWER, "the base moved after the green run"),
                          (GREEN, "the two are indistinguishable in time")):
        fast, out = _event_scope(
            EVENT_NAME="pull_request", ACTION="edited", BASE_CHANGED="false",
            HEAD_SHA="deadbeef", GREEN_AT_CMD=GREEN, BASE_RUN_AT_CMD=base_at)
        assert fast == "false", f"{case}: {out}"


def test_a_base_change_arrives_as_edited_and_invalidates_every_job():
    """A change of base BRANCH is an `edited` event too, and it moves the
    diff, the merge ref and every guard that compares against the base. It is
    checked before the forge is asked anything, so even a sha with a fresh
    green run behind it re-runs everything."""
    fast, out = _event_scope(EVENT_NAME="pull_request", ACTION="edited",
                             BASE_CHANGED="true", HEAD_SHA="deadbeef",
                             GREEN_AT_CMD=GREEN, BASE_RUN_AT_CMD=BASE_OLDER)
    assert fast == "false", out
    assert "base branch" in out.lower()


@pytest.mark.parametrize("base_changed", [None, "", "TRUE", "yes", "0",
                                          "false extra"])
def test_a_base_change_input_this_cannot_read_reruns_everything(base_changed):
    """The one input whose ABSENCE used to be permissive.

    Every other unset input answers `false` on its own; `BASE_CHANGED` unset
    fell straight through to the forge query, so deleting one line from the
    workflow's `env:` made the base-change branch unreachable with every cell
    still green — a guard whose subject the caller can remove in silence.
    Only the literal `false` now continues, so an absent, misspelled or
    unexpected value is the full build.
    """
    env = dict(EVENT_NAME="pull_request", ACTION="edited",
               HEAD_SHA="deadbeef", GREEN_AT_CMD=GREEN,
               BASE_RUN_AT_CMD=BASE_OLDER)
    if base_changed is not None:
        env["BASE_CHANGED"] = base_changed
    fast, out = _event_scope(_unset=("BASE_CHANGED",), **env)
    assert fast == "false", out


@pytest.mark.parametrize("bad_answer", ["false", "printf ''", "printf maybe",
                                        "printf -- -1", "printf 3",
                                        "printf 2026-09-25T12:00:00"])
def test_an_unanswerable_forge_reruns_everything(bad_answer):
    """"I could not tell" and "it was green" are the two answers this must
    never conflate. A query that fails, returns nothing, or returns something
    that is not an instant off the forge's own clock — a bare count, a
    timestamp missing its zone — all land on the full run, on either
    question."""
    for which in ("GREEN_AT_CMD", "BASE_RUN_AT_CMD"):
        env = dict(EVENT_NAME="pull_request", ACTION="edited",
                   BASE_CHANGED="false", HEAD_SHA="deadbeef",
                   GREEN_AT_CMD=GREEN, BASE_RUN_AT_CMD=BASE_OLDER)
        env[which] = bad_answer
        fast, out = _event_scope(**env)
        assert fast == "false", f"{which}={bad_answer!r}: {out}"


@pytest.mark.parametrize("action", ["opened", "synchronize", "reopened", ""])
def test_every_other_activity_type_still_runs_everything(action):
    """Only `edited` can take the fast path. Every other activity type on a
    pull request changes the code under review, and an unnamed one is not
    assumed harmless."""
    fast, out = _event_scope(EVENT_NAME="pull_request", ACTION=action,
                             BASE_CHANGED="false", HEAD_SHA="deadbeef",
                             GREEN_AT_CMD=GREEN, BASE_RUN_AT_CMD=BASE_OLDER)
    assert fast == "false", out


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_a_push_is_never_a_retitle(event):
    fast, out = _event_scope(EVENT_NAME=event, ACTION="", BASE_CHANGED="false",
                             HEAD_SHA="", GREEN_AT_CMD=GREEN,
                             BASE_RUN_AT_CMD=BASE_OLDER)
    assert fast == "false", out


def test_the_workflow_hands_the_script_every_input_it_branches_on():
    """The script is only fail-closed if it is CALLED with what it reads.

    Each input below has a branch of its own, and the workflow step is the
    only caller. Reading the step's `env:` here is what stops one of them
    being dropped — the shape that made the base-change branch unreachable.
    """
    import yaml

    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = [s for s in ci["jobs"]["triage"]["steps"]
             if "ci-event-scope.sh" in str(s.get("run", ""))]
    assert len(steps) == 1, "the triage job no longer calls the decision script"
    supplied = set((steps[0].get("env") or {}))
    required = {"EVENT_NAME", "ACTION", "BASE_CHANGED", "HEAD_SHA", "BASE_REF"}
    assert required <= supplied, (
        f"the triage step does not supply {sorted(required - supplied)}, and "
        "every one of those has a branch in the script — an input the caller "
        "omits is a branch nothing can reach")


def test_every_job_that_does_not_read_the_title_is_gated_on_the_triage():
    """DERIVED, not enumerated: every job the pull-request event runs, other
    than the one that reads the title and the triage job itself, must wait on
    triage and must skip only on a positive `fast == 'true'`.

    Enumerating the jobs is how a job added later ships outside the fast path
    — which is the harmless direction — or, far worse, how a job added later
    ends up skipped on an answer that was never given. The `always()` half is
    what makes the second impossible: without it a failed triage job skips
    every dependent, and a skipped required check reads as a passed one.
    """
    import yaml

    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    jobs = ci["jobs"]
    assert "triage" in jobs, "the fast-path decision job is gone"
    assert str(jobs["commits"].get("if", "")).find("triage") == -1, (
        "the job that READS the title is gated on the fast path, so a retitle "
        "would skip the only check that validates it — which is the whole "
        "reason `edited` is in the trigger list")
    assert "triage" not in (jobs["commits"].get("needs") or []), (
        "the title job now waits on triage, which adds a serial hop to the "
        "one job a retitle exists to re-run")

    def runs_on_a_pull_request(job) -> bool:
        cond = str(job.get("if", ""))
        return "github.event_name == 'push'" not in cond \
            and "refs/tags/v" not in cond

    unguarded = []
    for name, job in jobs.items():
        if name in ("triage", "commits") or not runs_on_a_pull_request(job):
            continue
        cond = str(job.get("if", ""))
        if "triage" not in (job.get("needs") or []) \
                or "needs.triage.outputs.fast != 'true'" not in cond \
                or "always()" not in cond:
            unguarded.append(f"{name}: needs={job.get('needs')} if={cond!r}")
    assert not unguarded, (
        "these pull-request jobs do not carry the title-only fast path, or "
        "carry it without the always() that keeps it fail-closed: "
        f"{unguarded}")


def test_the_edited_trigger_is_still_armed():
    """The fast path is a cheaper `edited`, never an absent one. Drop the
    activity type and a retitle fires no run at all, the stale green is still
    the latest, and an invalid header can land on main permanently — which it
    did once."""
    import yaml

    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    types = ci[True]["pull_request"]["types"]
    assert "edited" in types, (
        f"`edited` has left the trigger list, so a retitle after a green run "
        f"re-validates nothing: {types}")
    assert {"opened", "synchronize", "reopened"} <= set(types), (
        f"listing types replaces GitHub's defaults, so all three have to be "
        f"restated: {types}")


def test_the_hook_and_ci_declare_the_same_suite_environment():
    """One list, THREE callers, equal in every direction.

    The same shape as the ruff pin above: the value is written in each caller
    that runs the suite and a guard holds them identical, so adding a variable
    to one without adding it to the others is red rather than a divergence
    nobody looks at. Asserting AGREEMENT rather than a literal name means a
    third variable is one edit in each file.

    THE THIRD CALLER is `repo.yaml`'s `tests` scope, and leaving it out was a
    round-1 finding rather than an oversight worth restating: that scope is
    what `warden verify --scope tests` runs, which is what CI's `verify` job
    executes on the gate's behalf, so it is EXECUTED code and not a prose
    quotation. A guard that
    named two while the hook's own comment named three would have let the
    gate's environment drift away from both of the others silently. It sets
    the variable INLINE — `NIGHTGATE_REQUIRE_TOOLCHAIN=1 uv run pytest ...` —
    so it is read off the leading assignments of the command rather than out
    of a mapping.

    The hook side is read off `SUITE_ENV=`, because that is the declaration
    the hook actually applies; a variable exported some other way would not
    be in it, and the equality would then be false in the direction that
    matters.
    """
    import re
    import shlex

    import yaml

    def leading_assignments(command: str) -> dict:
        """The `VAR=value` words a shell command sets before its program."""
        out = {}
        for word in shlex.split(command):
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word):
                name, value = word.split("=", 1)
                out[name] = value
            else:
                break
        return out

    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    ci_env = {}
    for step in ci["jobs"]["tests"]["steps"]:
        if "pytest" in str(step.get("run", "")):
            ci_env.update({k: str(v) for k, v in (step.get("env") or {}).items()})
    assert ci_env, (
        "the CI pytest step declares no environment at all — either the job "
        "moved or this guard reads the wrong place")

    scope = yaml.safe_load((ROOT / "repo.yaml").read_text())["verify"]["tests"]
    scope_pytest = [s["run"] for s in scope
                    if isinstance(s, dict) and "pytest" in s.get("run", "")]
    assert scope_pytest, "repo.yaml's tests scope no longer runs pytest"
    scope_env = {}
    for run in scope_pytest:
        scope_env.update(leading_assignments(run))

    hook = (ROOT / ".githooks" / "pre-push").read_text()
    declared = re.findall(r'^SUITE_ENV="([^"]*)"', hook, re.M)
    assert len(declared) == 1, (
        f"the hook no longer declares exactly one SUITE_ENV: {declared}")
    hook_env = dict(pair.split("=", 1) for pair in declared[0].split() if pair)

    callers = {".githooks/pre-push": hook_env,
               ".github/workflows/ci.yml": ci_env,
               "repo.yaml (verify.tests)": scope_env}
    assert len({tuple(sorted(e.items())) for e in callers.values()}) == 1, (
        "the three callers that run this suite set different environments, "
        f"so a push, an interpreter leg and the gate judge it differently: "
        f"{callers}. A push judged under one and merged under another is "
        "green locally and red in CI with the code identical; the list moves "
        "in all three or in none")
    # Agreement on the NEW spelling. The old name is an alias the reader
    # honours until v4.0.0, never a name a caller sets: three callers agreeing
    # on the old one would still pass the equality above while every run
    # printed a deprecation line.
    agreed = hook_env
    assert agreed.get(toolchain.STRICT_ENV) == "1", (
        f"the three callers agree on {agreed}, which does not set "
        f"{toolchain.STRICT_ENV}=1")
    assert toolchain.LEGACY_STRICT_ENV not in agreed, (
        f"a caller still sets the deprecated {toolchain.LEGACY_STRICT_ENV}: "
        f"{agreed}")


def _hook_toolchain_read() -> str:
    """The Python the hook hands `uv run python -c` to read the declared
    toolchain, as the hook carries it."""
    import re
    hook = (ROOT / ".githooks" / "pre-push").read_text()
    found = re.findall(
        r"^declared=\$\(uv run --quiet --locked python -c '([^']*)'\)$",
        hook, re.M)
    assert len(found) == 1, (
        f"the hook no longer reads its toolchain with exactly one "
        f"`uv run python -c '...'`: {found}")
    return found[0]


@pytest.mark.parametrize("table,warns", [
    ('[tool.nightgate.toolchain]\nrequire = ["uv", "go"]\n', False),
    ('[tool.agentops.toolchain]\nrequire = ["uv", "go"]\n', True),
    ('[tool.agentops.toolchain]\nrequire = ["npm"]\n'
     '[tool.nightgate.toolchain]\nrequire = ["uv", "go"]\n', False),
])
def test_the_hooks_toolchain_read_takes_the_old_table_as_an_alias(
        tmp_path, table, warns):
    """The hook's own read, executed, not paraphrased: the new table is read,
    the old one is read in its place until v4.0.0 and the read says so on
    stderr (which the hook's command substitution leaves on the terminal),
    and the new one wins when both are present."""
    (tmp_path / "pyproject.toml").write_text(table)
    proc = subprocess.run([sys.executable, "-c", _hook_toolchain_read()],
                          cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["uv", "go"], proc.stdout
    if warns:
        assert ("DEPRECATED" in proc.stderr
                and "[tool.agentops.toolchain]" in proc.stderr
                and "v4.0.0" in proc.stderr), proc.stderr
    else:
        assert proc.stderr == "", proc.stderr


def test_the_hooks_toolchain_read_prints_nothing_when_neither_table_exists(
        tmp_path):
    """Fail-closed stays fail-closed through the alias: no table, no list on
    stdout, so the hook's `[ -z "$declared" ]` refusal fires."""
    (tmp_path / "pyproject.toml").write_text("[tool.other]\nx = 1\n")
    proc = subprocess.run([sys.executable, "-c", _hook_toolchain_read()],
                          cwd=tmp_path, capture_output=True, text=True)
    assert proc.stdout.strip() == "", proc.stdout


# ---------- core.bare: worktree isolation is not config isolation ----------
#
# Git worktrees SHARE one .git/config. A write to a shared key from anywhere
# — a sibling worktree, a tool, or the harness's own worktree teardown under
# .claude/worktrees/ — rewrites it for the parent and every sibling. Two
# symptoms:
# core.hooksPath repointed (the local gate, above) and core.bare=true, which
# makes git WRITES silently fail in the parent while READS keep working, so
# nothing looks broken until a push or commit fails. warden explain already
# reports the hooksPath drift; core.bare is the same machine-local fact —
# assert it, report it loud.

from warden.explain import core_bare_state


def test_core_bare_unset_is_ok(repo):
    assert core_bare_state(repo)[0] == "ok"


def test_core_bare_false_is_ok(repo):
    _git(repo, "config", "core.bare", "false")
    assert core_bare_state(repo)[0] == "ok"


def test_core_bare_true_is_corrupted(repo):
    _git(repo, "config", "core.bare", "true")
    state, detail = core_bare_state(repo)
    assert state == "corrupted"
    # names the symptom (writes fail), the cause (shared worktree config),
    # and the exact repair — an orientation brief must be actionable.
    assert "git config core.bare false" in detail
    assert "write" in detail.lower()
    assert "worktree" in detail.lower()


@pytest.mark.parametrize("spelling", ["1", "yes", "on", "TRUE"])
def test_core_bare_true_in_every_git_spelling_is_corrupted(repo, spelling):
    """git treats 1/yes/on/TRUE identically to true — each genuinely puts
    the checkout in bare mode (writes fail). A raw compare against the string
    'true' would read `core.bare = 1` as healthy; the canonicalising
    --type=bool catches every spelling."""
    _git(repo, "config", "core.bare", spelling)
    assert core_bare_state(repo)[0] == "corrupted", (
        f"core.bare={spelling!r} is bare mode to git but read as healthy")


def test_core_bare_unparseable_value_fails_closed(repo):
    """A value git cannot parse as a boolean is itself corruption; it must
    not read as 'ok' — fail closed to unknown."""
    _git(repo, "config", "core.bare", "garbage")
    state, detail = core_bare_state(repo)
    assert state == "unknown"
    assert "cannot parse" in detail.lower()


def test_core_bare_unreadable_fails_closed(repo, monkeypatch):
    """A read that could not run must not report 'ok' — 'could not look' is
    not 'healthy', the same fail-closed direction the local gate takes."""
    import warden.explain as explain

    def boom(*a, **k):
        raise OSError("git unavailable")
    monkeypatch.setattr(explain.subprocess, "run", boom)
    assert core_bare_state(repo)[0] == "unknown"


def test_brief_shouts_when_core_bare_is_true(monkeypatch):
    import warden.config as config_mod
    import warden.explain as explain

    monkeypatch.setattr(explain, "core_bare_state",
                        lambda root: ("corrupted",
                                      "core.bare is true — git writes fail; "
                                      "a worktree rewrote the shared config. "
                                      "Run: git config core.bare false"))
    brief = explain.render(config_mod.load(ROOT))
    assert "core.bare" in brief
    assert "git config core.bare false" in brief


def test_brief_stays_quiet_when_core_bare_is_ok(monkeypatch):
    """Loud-only: a healthy repo adds NO core.bare line. Asserting only that
    the remediation string is absent would not bite — that string only ever
    comes from the corrupted branch, so it stays absent even with the
    'if bare_state != ok' guard deleted (which prints 'UNKNOWN (fail
    closed): false' on every healthy run). Pin the absence of every core.bare
    marker so removing the guard goes red."""
    import warden.config as config_mod
    import warden.explain as explain

    monkeypatch.setattr(explain, "core_bare_state", lambda root: ("ok", "false"))
    brief = explain.render(config_mod.load(ROOT))
    assert "git config core.bare false" not in brief
    assert "core.bare" not in brief
    assert "CORRUPTED" not in brief
    assert "UNKNOWN (fail closed)" not in brief


#: "Does this step put THIS repo's pytest suite on a runner" — BORROWED, not
#: re-spelled. tests/test_toolchain.py owns the question, and its two
#: spellings (`pytest` directly, and `warden verify --scope tests`, which runs
#: repo.yaml's two commands) are what its round-1 F2 finding was about. A
#: second definition here would be the same defect: one reader would see a job
#: running the suite and the other would not.
from test_toolchain import _runs_the_suite


def _ci_interpreters(ci: dict) -> dict[str, set[str]]:
    """The CPython minor versions each CI job that runs this suite exercises.

    DERIVED per job, never a list of job names, for the reason the toolchain
    guards give: a job that starts running the suite inherits the obligation
    instead of being missed by a name nobody updated.

    Two ways a job can name its interpreter, and a job that runs the suite
    without either is reported as exercising NOTHING — which is louder than
    omitting it, because "whatever the runner image offered" is exactly the
    state the interpreter matrix exists to rule out:

    - a matrix leg, when a suite step binds `--python` to `matrix.python`. The
      VALUE matters, not the flag: `--python "3.12"` hardcoded on every leg
      would run one interpreter under three names.
    - `setup-uv`'s `python-version` input, for a job with no matrix. That is
      how the `verify` job carries the committed `.python-version` now that
      the matrix no longer does. Its scope commands come from
      repo.yaml, which deliberately names no interpreter — repo.yaml is the
      contract a consumer's gate runs too — so the job's own input is the only
      place the declaration can live.
    """
    out: dict[str, set[str]] = {}
    for name, job in (ci.get("jobs") or {}).items():
        steps = job.get("steps") or []
        if not any(_runs_the_suite(s) for s in steps):
            continue
        legs = (((job.get("strategy") or {}).get("matrix") or {}).get("python")
                or [])
        bound = any('--python "${{ matrix.python }}"' in str(s.get("run", ""))
                    for s in steps if isinstance(s, dict))
        if legs and bound:
            out[name] = {str(leg) for leg in legs}
            continue
        pinned = {str((s.get("with") or {}).get("python-version"))
                  for s in steps if isinstance(s, dict)
                  and str(s.get("uses", "")).startswith("astral-sh/setup-uv@")
                  and (s.get("with") or {}).get("python-version") is not None}
        out[name] = pinned
    return out


def test_the_declared_gate_scopes_run_once_and_the_gate_takes_them():
    """The suite runs three times on a pull request, not four, and the gate's
    verify evidence arrives by `warden take` rather than by re-earning it.

    WHAT WAS WRONG. `warden audit` pairs a verify result to the reviewed
    commit by SHA out of the runner's own filesystem, so the gate job ran
    `warden verify --scope tests` itself — on top of three interpreter legs
    that had already run repo.yaml's two commands. Measured over 15
    consecutive pull_request runs, that fourth copy cost a mean of 15.3
    runner-minutes each (min 11.3, max 17.7) and proved nothing the matrix had
    not. The currency is RUNNER-MINUTES: the suite is the pole of this
    workflow either way, so no wall clock was ever at stake in the duplicate
    itself — what the fix does cost in wall clock is the gate job now waiting
    on the verify job instead of running beside it.

    WHY THE SEAM IS THE ONE WARDEN ALREADY SHIPS. `warden take --from` and
    `declare check` D-03's take credit exist for exactly this: the layout
    `warden init` writes for every consumer is an `install` job, a `verify`
    job that uploads its results and a `gate` job that downloads, takes and
    reviews. This repo did not run the shape it ships. The take path was the
    one part of the emitted workflow the platform never used on itself, and
    self-hosting is the platform's credibility test.

    WHAT THIS PINS, and the last two are EXECUTED rather than transcribed —
    the structural assertions above them would all stay green over a workflow
    D-03 rejects, because they read the YAML the same way a reader does:

      1. exactly one job runs the declared `tests` scope, so a fourth copy
         cannot come back by being added somewhere else;
      2. the gate does not run the suite at all;
      3. the gate `needs` the verify job and reads its RESULT to a non-zero
         exit — a red suite must be a red gate, not a gate blaming the
         plumbing when the take finds no artifact;
      4. the verify job's interpreter input equals the committed
         `.python-version`, since that is the leg the matrix gave up;
      5. `declare check` D-03 renders CLEAN over this tree, and its detail
         says the gate TAKES the scope rather than running it;
      6. it renders DRIFT over the same tree with the artifact name broken at
         one end, and again with the take step removed. Without those two the
         clean verdict could be a check that stopped looking.
    """
    import shutil

    import yaml

    from warden import config as config_mod
    from warden import declared as declared_mod

    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    ci = yaml.safe_load(workflow.read_text())
    jobs = ci["jobs"]

    scope_runners = sorted(
        name for name, job in jobs.items()
        if any("verify --scope tests" in str(s.get("run", ""))
               for s in (job.get("steps") or []) if isinstance(s, dict)))
    assert scope_runners == ["verify"], (
        "the declared `tests` scope is run by "
        f"{scope_runners or 'no job'}, not by the `verify` job alone. Every "
        "extra runner is another full copy of this suite on every pull "
        "request, at a measured mean of 15.3 runner-minutes")

    gate = jobs["gate"]
    gate_steps = gate["steps"]
    assert not any(_runs_the_suite(s) for s in gate_steps), (
        "the gate job runs the suite again instead of taking the verify job's "
        "result for it")
    assert "verify" in gate["needs"], (
        f"the gate no longer waits on the job whose results it takes: "
        f"{gate['needs']}")
    guards = [str(s.get("run", "")) for s in gate_steps
              if "needs.verify.result" in str(s.get("run", ""))
              or "needs.verify.result" in str((s.get("env") or {}).values())]
    guards += [str(s.get("run", "")) for s in gate_steps
               if "needs.verify.result" in yaml.safe_dump(s.get("env") or {})]
    assert guards, (
        "the gate never reads the verify job's result. With always() on the "
        "job and no check, a failing suite would leave the gate to fail on "
        "`warden take` finding no artifact — which reports the gate DID NOT "
        "RUN, the wrong verdict for a red suite")
    assert any("exit 1" in g for g in guards), (
        f"the gate reads the verify job's result and reaches no verdict: {guards}")

    pin = (ROOT / ".python-version").read_text().strip()
    assert _ci_interpreters(ci)["verify"] == {pin}, (
        f"the verify job does not pin the committed interpreter {pin!r}; it "
        f"exercises {_ci_interpreters(ci)['verify']}. repo.yaml's scope "
        "commands name no interpreter on purpose, so this job's setup-uv "
        "input is the only declaration there is — without it the one leg the "
        "matrix gave up runs on whatever the runner image offered")

    clean = declared_mod.check_scope_runners(ROOT, config_mod.load(ROOT))
    assert clean.status == declared_mod.CLEAN, (
        f"D-03 does not credit this workflow: {clean.detail} "
        f"{[f.detail for f in clean.findings]}")
    assert "takes every declared scope" in clean.detail, (
        f"D-03 is clean for some other reason than the take: {clean.detail}")

    # The two mutations. A scratch copy of this repo's own declaration and
    # workflow, because D-03 reads both off a root.
    def probe(mutate) -> declared_mod.CheckResult:
        root = tmp / f"probe{next(counter)}"
        (root / ".github" / "workflows").mkdir(parents=True)
        shutil.copy(ROOT / "repo.yaml", root / "repo.yaml")
        for rel in ("warden", "cage", "tests", "examples", "skills", "docs"):
            (root / rel).mkdir(exist_ok=True)
            (root / rel / "x.py").write_text("x\n")
        (root / "examples" / "hello-svc").mkdir()
        doc = yaml.safe_load(workflow.read_text())
        mutate(doc)
        (root / ".github" / "workflows" / "ci.yml").write_text(yaml.safe_dump(doc))
        return declared_mod.check_scope_runners(root, config_mod.load(root))

    import itertools
    import tempfile
    counter = itertools.count()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)

        # The control, first: a probe that mutates NOTHING must be clean. A
        # scratch root that D-03 rejects for its own reasons — a component
        # directory it cannot find, a repo.yaml it re-resolves differently —
        # would make both mutations below drift for free.
        control = probe(lambda doc: None)
        assert control.status == declared_mod.CLEAN, (
            "the unmutated probe of this repo's own workflow is not clean "
            f"({control.detail}), so the two mutations below prove nothing")

        def rename_upload(doc):
            for step in doc["jobs"]["verify"]["steps"]:
                if (step.get("with") or {}).get("name") == "warden-verify":
                    step["with"]["name"] = "warden-verified"

        broken = probe(rename_upload)
        assert broken.status == declared_mod.DRIFT, (
            "D-03 still reports clean with the verify job uploading under a "
            "name the gate never downloads, so the clean verdict above is a "
            f"check that stopped looking: {broken.detail}")

        def drop_take(doc):
            doc["jobs"]["gate"]["steps"] = [
                s for s in doc["jobs"]["gate"]["steps"]
                if not str(s.get("run", "")).strip().startswith("warden take")]

        without = probe(drop_take)
        assert without.status == declared_mod.DRIFT, (
            "D-03 still reports clean with no take step at all, so nothing "
            "binds the gate's verify evidence to the job that earned it: "
            f"{without.detail}")


def test_the_ruff_pin_is_the_same_in_all_three_executed_callers():
    """repo.yaml's `verify: tests` scope, .github/workflows/ci.yml's lint
    step and .githooks/pre-push must name ONE ruff version.

    ci.yml's `verify` job runs `warden verify --scope tests`, so repo.yaml's
    command is the lint CI actually enforces. With ruff unpinned in any caller, a new
    ruff release could turn the gate red or green with no diff, and
    `--locked` does not govern a `--with` dependency (which is exactly why
    `pinned-run-invocations` refuses `uv run --locked --with ruff` as a
    floating invocation).

    This asserts AGREEMENT, not a literal version, so a bump moves one number
    in each caller and stays green; dropping any one pin goes red.

    Two ways this test could be weak. Reading `verify.tests[0]` by INDEX
    would turn reordering the ruff and pytest entries red with the misleading
    message "runs ruff without a pin". And grepping the WHOLE file for ci.yml
    and pre-push would let `# --with ruff==0.16.5` in a comment above a
    floating `--with ruff` keep it green while the CI ruff floats —
    precisely the defect this test exists to close. Scanning every line that
    writes the ruff invocation, and nothing else, closes both.

    EXECUTED callers only. CLAUDE.md and .warden/skills-policy.md quote the
    same command as prose an agent reads and copies; neither is run by
    anything, and reading a command out of a sentence is a prose pin — the
    class of reader this repo removed from the proportionate tier — so they
    are not held here. The count in this test's name is the executed set and
    moves with it.
    """
    import re

    import yaml

    pin = re.compile(r"--with\s+'?ruff==([0-9][^'\s]*)'?")

    def ruff_invocation_lines(text: str) -> list[str]:
        """Lines that WRITE the ruff invocation: shell lines and YAML `run:`
        scalars. A comment cannot satisfy the pin — strip it before matching,
        and drop any line that is only a comment."""
        out = []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0]
            if "ruff" in line and ("uv run" in line or "uvx" in line):
                out.append(line)
        return out

    repo_yaml = yaml.safe_load((ROOT / "repo.yaml").read_text())
    scopes = [step["run"] for steps in repo_yaml["verify"].values()
              for step in steps if isinstance(step, dict) and "run" in step]
    sources = {
        "repo.yaml": [s for s in scopes if "ruff" in s],
        ".github/workflows/ci.yml": ruff_invocation_lines(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text()),
        ".githooks/pre-push": ruff_invocation_lines(
            (ROOT / ".githooks" / "pre-push").read_text()),
    }
    found = {}
    for name, lines in sources.items():
        assert lines, f"{name} runs ruff nowhere — the lockstep set has changed"
        versions = {v for line in lines for v in pin.findall(line)}
        unpinned = [line.strip() for line in lines if not pin.search(line)]
        assert not unpinned, (
            f"{name} runs ruff without a `--with ruff==` pin: {unpinned}")
        assert len(versions) == 1, f"{name} names several ruff pins: {versions}"
        found[name] = versions.pop()
    assert len(found) == 3, (
        f"the lockstep set is no longer three callers: {sorted(found)}")
    assert len(set(found.values())) == 1, (
        f"the ruff pin has drifted between its three callers: {found}")


def test_the_interpreter_is_pinned_and_the_matrix_covers_what_we_claim():
    """Local-green and CI-green must mean the same thing, and
    `requires-python` must be a claim something evaluates.

    An unpinned interpreter lets a developer's machine and the CI runner
    resolve different Pythons, so one sha can be green locally and red in CI.
    The difference is real: Python 3.13 made the compiler strip common
    leading whitespace from docstrings, so a test's hand-rolled unwrap is a
    no-op on 3.14 and its assertion can silently stop evaluating there while
    still firing on 3.12.

    Three facts have to move together or the pin rots: the committed
    `.python-version` (what a bare `uv run` uses locally), the interpreters CI
    actually exercises, and `requires-python` (what a `uvx --from git+`
    consumer is told it may enroll with). This binds them. It pins
    RELATIONSHIPS, not literal versions, so adding or retiring an interpreter
    is a deliberate edit in the files rather than a red test with a
    misleading message.

    THE SECOND OF THE THREE IS NO LONGER THE MATRIX ALONE.
    The suite used to run four times per pull request — three interpreter
    legs plus the gate job's own `warden verify --scope tests` — and the
    fourth was a duplicate. Cutting it moved the committed `.python-version`
    off the matrix and onto the `verify` job, which runs the same suite
    through the declared scope. So the interpreter set is read from
    `_ci_interpreters` below, which derives it per job rather than naming the
    matrix, and a leg dropped from EITHER job is red here.
    """
    import re

    import yaml

    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    tests_job = ci["jobs"]["tests"]
    matrix = tests_job["strategy"]["matrix"]["python"]
    exercised = _ci_interpreters(ci)
    assert len(matrix) >= 2, (
        "the interpreter matrix collapsed to a single leg — a pin makes local "
        "reproduce CI and HIDES the spread, and it hides it in both "
        "directions: under one pin a failure on any other interpreter is "
        "invisible to the gate forever")
    assert tests_job["strategy"].get("fail-fast") is False, (
        "fail-fast cancels the surviving legs on the first red, which hides "
        "whether a failure is version-specific — the one question this "
        "matrix exists to answer")

    pin = (ROOT / ".python-version").read_text().strip()
    assert re.fullmatch(r"3\.\d+", pin), (
        f".python-version must name one CPython minor version, got {pin!r} — "
        "an unpinned local interpreter is what made a local pass evidence "
        "for nothing")
    every = set().union(*exercised.values())
    assert pin in every, (
        f".python-version pins {pin}, which CI never runs (it exercises "
        f"{exercised}). A local pass would then still be evidence about an "
        "interpreter the gate does not exercise")
    assert len(every) >= 3, (
        "CI exercises fewer than three interpreters. The declared set is the "
        "floor requires-python claims, the committed .python-version and the "
        f"developer interpreter — it exercises {sorted(every)}. A leg dropped "
        "from the matrix OR from the verify job is a leg the gate stopped "
        "asking about")

    def key(v):
        return tuple(int(part) for part in v.split("."))

    pyproject = (ROOT / "pyproject.toml").read_text()
    floor = re.search(r'requires-python\s*=\s*">=\s*([0-9.]+)"', pyproject)
    assert floor, "pyproject no longer declares a requires-python floor"
    assert min(every, key=key) == floor.group(1), (
        f"requires-python claims >={floor.group(1)} but the lowest interpreter "
        f"CI exercises is {min(every, key=key)}. One of the two is a claim "
        "nothing checks: either test the floor, or narrow it and say so — "
        "narrowing it narrows who can enroll via `uvx --from git+`")

    # Every leg must actually VARY the interpreter. setup-uv's input alone is
    # not enough: the committed .python-version wins for `uv run`, so three
    # legs would report three names and run one interpreter — a matrix that
    # cannot fail differently is the `test-cannot-fail` class wearing a
    # strategy block.
    runs = [s["run"] for s in tests_job["steps"] if "run" in s]
    for run in runs:
        if "uv run" in run:
            # The VALUE, not just the flag. `--python "3.12"` hardcoded on
            # every leg would satisfy a bare `"--python" in run` while running
            # one interpreter three times under three names — the exact
            # failure this matrix exists to prevent, passing its own guard.
            assert '--python "${{ matrix.python }}"' in run, (
                "a tests-job invocation does not bind its interpreter to the "
                "matrix leg, so either the committed .python-version decides "
                f"it or one version is hardcoded for all legs: {run.strip()!r}")
    # The READING is not the verdict. Looking only for `sys.version_info` in
    # some `run:` scalar stays green if the comparison is deleted and the echo
    # kept — a printout that can never fail, guarded by a test that can never
    # fail, which is precisely the "three greens for one fact" it claims to
    # prevent. Pin the comparison and the exit, the way the sibling
    # aggregator test in this file already does.
    proof = [r for r in runs if "sys.version_info" in r]
    assert proof, (
        "no leg verifies it RAN the interpreter it claims — without that, a "
        "silently ignored --python turns the matrix into three copies of one "
        "run and the gate reports three greens for one fact")
    step = proof[0]
    assert '"$got" = "${{ matrix.python }}"' in step, (
        "the leg reads the interpreter but never compares it to the one it "
        f"advertises, so the reading decides nothing: {step.strip()!r}")
    assert "exit 1" in step, (
        "the interpreter comparison reaches no verdict — a mismatch must "
        f"fail the leg, not print: {step.strip()!r}")
    # ...and the verdict must still be able to reach the runner. Appending
    # `|| true` AFTER the whole `[ ... ] || { ...; exit 1; }` block keeps
    # every substring above present while the step exits 0 on a mismatch,
    # so substring checks alone would stay green. A static test cannot
    # execute the shell, so it pins the constructs that neutralize an exit
    # status instead — the ones that turn a verdict back into a printout.
    for neutralizer in ("|| true", "|| :", "; true", "set +e", "|| exit 0"):
        for run in runs:
            assert neutralizer not in run, (
                f"a tests-job step disables its own exit status with "
                f"{neutralizer!r}, so the check it wraps cannot fail the "
                f"leg: {run.strip()!r}")


def test_the_required_check_name_survives_the_matrix():
    """Matrix legs carry their version in the check name, so the status
    context `warden tests` that branch protection names would simply stop
    reporting — and a required check that never reports blocks every merge.
    The aggregator keeps the name resolvable and must be fail-closed."""
    import yaml

    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    agg = ci["jobs"]["tests-all"]
    assert agg["name"] == "warden tests", (
        "the aggregator no longer publishes the `warden tests` status "
        "context that the matrix legs stopped publishing")
    assert "tests" in agg["needs"], (
        f"the aggregator no longer waits on the matrix it aggregates: "
        f"{agg['needs']}")
    # `if: always()` is parsed by pyyaml as the boolean True unless quoted;
    # accept either spelling of the same instruction. The condition may now
    # carry the title-only fast path's clause too — what must
    # never leave it is always().
    assert "always()" in str(agg["if"]), (
        "without always() this job SKIPS when a leg fails, and a skipped "
        "required check is neither passed nor red — the ambiguity is the "
        "fail-open shape, so the result is read explicitly instead")
    body = " ".join(s.get("run", "") for s in agg["steps"])
    assert "needs.tests.result" in body and "exit 1" in body, (
        "the aggregator no longer READS the matrix result — with always() "
        "and no check it would report green however the legs went, which is "
        "strictly worse than not having it")

    # EVERY job that runs the suite, derived rather than named. `warden tests`
    # is the one required status branch protection asks for, and its meaning is
    # "every declared interpreter is green" — so an interpreter that moved off
    # the matrix and onto another job must still reach this verdict. Deleting
    # the arm that reads a second job's result leaves this job green over a red
    # leg, because always() keeps it running: a claim in the aggregator's own
    # comment that nothing evaluated is the class this repo keeps finding.
    aggregated = sorted(set(_ci_interpreters(ci)) & set(agg["needs"]))
    assert aggregated, (
        "the aggregator waits on no job that runs the suite, so the required "
        f"`warden tests` status stands for nothing: needs={agg['needs']}")
    for job in aggregated:
        assert f"needs.{job}.result" in body, (
            f"the aggregator waits on `{job}`, which runs this suite on an "
            f"interpreter of its own ({sorted(_ci_interpreters(ci)[job])}), "
            "and never reads its result. With always() and no check, `warden "
            f"tests` publishes green over a red `{job}` — covering fewer "
            "interpreters than the workflow claims to exercise")

    # ...and the verdicts must still be able to reach the runner, the same way
    # the interpreter legs above are held. `|| true` after a complete
    # `[ ... ] || { ...; exit 1; }` block keeps every substring above present
    # while the step exits 0 regardless, so substring checks alone stay green.
    for neutralizer in ("|| true", "|| :", "; true", "set +e", "|| exit 0"):
        assert neutralizer not in body, (
            f"an aggregator step disables its own exit status with "
            f"{neutralizer!r}, so the result it reads decides nothing and the "
            "required check reports green over a red leg")


def test_r2f3_the_tests_job_can_resolve_the_base_its_guards_compare_against():
    """The tests job checks out enough history to resolve `origin/main`.

    The suite holds guards that compare the tree against `origin/main` — the
    pack-version invariant is one. They SKIP when the base ref cannot be
    resolved, which is honest but useless if the job that runs them checks out
    a shallow clone: the guard would only ever evaluate on a developer's
    machine, and the gate would report green having asked nothing.

    A skip that only ever happens in CI is a check that does not exist there.
    """
    import yaml

    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    checkouts = [s for s in ci["jobs"]["tests"]["steps"]
                 if "actions/checkout" in str(s.get("uses", ""))]
    assert checkouts, "the tests job no longer checks out the repo"
    for step in checkouts:
        assert (step.get("with") or {}).get("fetch-depth") == 0, (
            "the tests job checks out a shallow clone, so every guard that "
            "compares against origin/main skips in CI and evaluates only "
            "locally — the gate would pass without asking the question")


def test_the_tests_job_asserts_its_checkout_contract_and_names_its_skips():
    """The two things the declaration above cannot do by itself.

    `fetch-depth: 0` is a DECLARATION, and the test above checks it by parsing
    YAML. YAML is not the runner: if `actions/checkout` changes what
    fetch-depth 0 fetches, or the fetch half-fails, the declaration stays green
    while every guard that compares against origin/main silently starts
    skipping again. So the job asserts the contract at runtime too.

    And `-rs` is what makes a skip legible. `pytest -q` prints "1889 passed, 8
    skipped" and names none of them, so the suite can get quietly weaker in CI
    while every run stays green — an absent zsh, say, silently skipping the
    commit-lint tests that need it. The skips are named at
    their sites; the flag is what carries those names to the log the gate is
    actually read from.
    """
    import yaml

    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = ci["jobs"]["tests"]["steps"]
    runs = [s.get("run", "") for s in steps]

    assert any("rev-parse --verify" in r and "origin/main" in r and "exit 1" in r
               for r in runs), (
        "the tests job no longer ASSERTS that origin/main resolves — the "
        "checkout contract is declared in YAML and checked nowhere the "
        "runner can disagree with it")

    pytest_runs = [r for r in runs if "pytest" in r]
    assert pytest_runs, "the tests job no longer runs pytest at all"
    for run in pytest_runs:
        assert " -rs" in run or run.endswith("-rs"), (
            "the CI pytest invocation no longer reports skip reasons, so a "
            f"skip in CI is an unnamed number again: {run!r}")


def test_the_gate_runs_the_suite_in_parallel_and_nothing_defaults_to_it():
    """Every gate runs the suite in parallel; nothing makes it the default.

    THE SHAPE, and it is the whole point: the flag lives in the INVOCATION,
    in each caller that wants it, and in no ambient default anywhere. Those
    are not two preferences, they are the two halves of one rule — a repo
    that wants a fast gate and an unchanged meaning of "green" for everyone
    downstream can have both only by writing the flag where a reader of the
    command can see it.

    This repo gated single-process for a while, on the argument that
    determinism outranks speed on runners whose core count nobody here
    controls. HALF of that was about `addopts`, and that half still holds
    verbatim below. The other half — the gate's own command — was reversed
    on measurement once the suite's known parallel-unsafe shape was fixed at
    the session rather than per module: a module-level capture of the
    environment that bound to the real checkout under the `GIT_DIR` only
    `git push` exports. The decision shard under `.warden/memory/decide/`
    carries both halves and the numbers.

    Five facts that only hold TOGETHER, which is why they are one test:

      1. `.githooks/pre-push` runs pytest with `-n auto`. This gate fires on
         every push, dozens of times a session across every agent.
      2. `repo.yaml`'s `tests` scope — what `warden verify --scope tests`
         runs, which IS this repo's gate — carries it too.
      3. CI's interpreter legs carry it. 2 and 3 are one fact wearing two
         hats: both run the whole suite, in jobs that start together, so
         the wall clock is whichever of them is slower and leaving either
         one serial buys nothing at all.
      4. `pytest-xdist` is in the dev dependency group. TWO of those three
         callers run `--locked` — the hook and the CI legs, and the
         assertions below say so one caller at a time rather than as a
         blanket — and a locked resolve is exactly the one that cannot
         reach past the lockfile, so without that entry the flag is not a
         slower gate, it is a gate that DIES with "unrecognized arguments:
         -n". A gate that fails closed for a reason unrelated to the code is
         a gate people disable. The scope's un-`--locked` `uv run` resolves
         the same dev group by default, so the entry is what serves all
         three; only the failure mode without it differs.
      5. NOTHING defaults to it. Not any `addopts` pytest would read, and
         not `PYTEST_ADDOPTS` anywhere in any workflow. A default changes
         what "green" means for every consumer of this repo and for the
         shipped example; the environment variable does that AND is
         inherited by the pytest runs this suite itself shells out to.

    Drop the flag from any of the three callers, drop pytest-xdist from the
    group, or put a parallel flag into an `addopts` or a `PYTEST_ADDOPTS`,
    and this goes red.

    WHAT THE POSITIVE ARMS ASK, precisely, because the two questions differ:
    they ask whether the argv requests at least one WORKER, not whether it
    names an xdist flag. `-n 0` and `--numprocesses=0` are xdist's own
    spellings for "no workers" and run the suite in-process, so a caller
    carrying one is single-process while naming the flag (round 1,
    fail-closed). The refusal arms keep the wider question — any parallel
    spelling in an ambient default is refused, `-n 0` included, because
    over-refusing a default nobody writes costs nothing.
    """
    import tomllib

    import yaml

    hook = (ROOT / ".githooks" / "pre-push").read_text()
    hook_pytest = [line.split("#", 1)[0] for line in hook.splitlines()
                   if "pytest" in line.split("#", 1)[0] and "uv run" in line]
    assert hook_pytest, "the pre-push hook no longer runs pytest at all"
    for line in hook_pytest:
        assert _requests_workers(_pytest_argv(line.split())), (
            "the pre-push suite is single-process again, so every push pays "
            f"the full serial suite: {line.strip()!r}")
        assert "--locked" in line, (
            f"the pre-push suite no longer runs --locked: {line.strip()!r}")

    # The gate's own declared command. Read by SCOPE rather than by index:
    # reordering the ruff and pytest entries must not turn this red with a
    # message about parallelism.
    scope = yaml.safe_load((ROOT / "repo.yaml").read_text())["verify"]["tests"]
    scope_pytest = [s["run"] for s in scope
                    if isinstance(s, dict) and "pytest" in s.get("run", "")]
    assert scope_pytest, "repo.yaml's tests scope no longer runs pytest"
    for run in scope_pytest:
        assert _requests_workers(_pytest_argv(run.split())), (
            "repo.yaml's `tests` scope is single-process again, so the "
            "command `warden verify --scope tests` runs — this repo's gate, "
            "and the one the CI gate job executes — pays the full serial "
            f"suite: {run!r}")

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dev = pyproject["dependency-groups"]["dev"]
    assert any(spec.startswith("pytest-xdist") for spec in dev), (
        "the callers above ask for `-n auto` and pytest-xdist is not in the "
        "dev dependency group, so every one of them fails before a single "
        "test runs — and the two that resolve `--locked`, the hook and the "
        f"CI legs, cannot reach past the lockfile to find it: {dev}")

    # EVERY config file pytest would read, not just pyproject.toml: pytest
    # resolves pytest.ini, then tox.ini/setup.cfg, and only then
    # pyproject.toml, so a pytest.ini added later would outrank the file this
    # guard reads and carry `-n` past it (round 1, pattern-fit).
    for name in ("pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"):
        path = ROOT / name
        if not path.is_file():
            continue
        if name == "pyproject.toml":
            addopts = (tomllib.loads(path.read_text()).get("tool", {})
                       .get("pytest", {}).get("ini_options", {}).get("addopts", ""))
        else:
            addopts = _ini_addopts(path.read_text())
        if isinstance(addopts, list):
            addopts = " ".join(addopts)
        assert not _parallel_flag(addopts.split()), (
            f"{name} puts a parallel flag in pytest's addopts, which makes "
            "the parallel run the default everywhere including a "
            "contributor's bare run and the shipped example — the half of "
            "this repo's parallelism ruling that was NOT reversed: "
            f"{addopts!r}")

    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    ci_pytest = [s.get("run", "") for s in ci["jobs"]["tests"]["steps"]
                 if "pytest" in s.get("run", "")]
    assert ci_pytest, "the tests job no longer runs pytest at all"
    for run in ci_pytest:
        assert _requests_workers(_pytest_argv(run.split())), (
            "a CI interpreter leg runs the suite single-process, so the "
            "wall clock of this workflow is that leg no matter what the "
            f"other callers do: {run!r}")

    _refuse_an_ambient_parallel_default(_every_workflow())


def _pytest_argv(words: list[str]) -> list[str]:
    """The words pytest itself receives, out of ONE command's words.

    BOUNDED AT BOTH ENDS, and the second end is what round 2 found missing.
    Splitting at the program name stops `uv run --locked` answering a
    question about pytest's own flags. It does NOT stop a word AFTER the
    invocation answering one — `words[i + 1:]` ran to the end of whatever it
    was handed, so a trailing `# ... -n auto` or a shell clause past `&&`
    still vouched for a command that never carried the flag. That was round
    1's own exploit, repaired one token short of closing it. The argv now
    ends at a comment or a shell separator.

    It reads SHELL, never prose: every caller hands it a line from a script,
    a workflow `run:` scalar or repo.yaml's scope. A markdown sentence is not
    shell and nothing here pretends to parse one.

    `python -m pytest` and a path spelling (`.venv/bin/pytest`) both land on
    the same word.
    """
    for i, word in enumerate(words):
        if word == "pytest" or word.endswith("/pytest"):
            argv = []
            for tail in words[i + 1:]:
                if tail.startswith("#") or tail in ("&&", "||", ";", "|"):
                    break
                argv.append(tail)
            return argv
    return []


def _worker_request(words: list[str]) -> str | None:
    """The value this argv gives xdist's `-n`, or None if it gives none.

    THE LAST ONE, because that is the one pytest uses. argparse lets a later
    `-n` overwrite an earlier one, so reading the first made
    `pytest -q -n auto -n 0` — the minimal-diff way to serialise a caller
    without touching what is already written — read as parallel while
    measurably running in-process (round 2, fail-closed).

    Every spelling xdist accepts for the value: `-n N`, `-nN`,
    `--numprocesses N` and `--numprocesses=N`. `--dist` is deliberately not
    here — it selects a distribution mode and asks for no workers of its own.
    A `-n` with nothing after it returns `""`, which reads as "no workers
    requested" below: pytest would refuse that argv outright, and a guard
    that called it parallel would be reading an invocation that cannot run.
    """
    found: str | None = None
    for i, word in enumerate(words):
        if word in ("-n", "--numprocesses"):
            found = words[i + 1] if i + 1 < len(words) else ""
        elif word.startswith("--numprocesses="):
            found = word.split("=", 1)[1]
        elif word.startswith("-n") and word[2:3].isalnum():
            found = word[2:]
    return found


def _requests_workers(words: list[str]) -> bool:
    """Does this argv ask xdist for AT LEAST ONE worker process?

    NOT the same question as `_parallel_flag`, and the difference is the
    whole reason this exists. `-n 0` and `--numprocesses=0` are xdist's own
    spellings for "no workers": pytest runs the suite in-process, with no
    "bringing up nodes" line and no speedup at all. `_parallel_flag` answers
    yes to both, because it exists to refuse a flag in an ambient default
    where over-refusing costs nothing. A POSITIVE arm asking "is this caller
    parallel" must not accept them (round 1, fail-closed).
    """
    value = _worker_request(words)
    return value is not None and value not in ("", "0")


def _every_workflow() -> dict[str, object]:
    """Every workflow file in the repo, parsed, keyed by path.

    ALL of them, not `ci.yml`: `PYTEST_ADDOPTS` set in any workflow that runs
    this suite has the same effect, and a workflow added later is exactly the
    file nobody remembers to re-check.
    """
    import yaml

    out = {}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        out[str(path.relative_to(ROOT))] = yaml.safe_load(path.read_text())
    assert out, "no workflow files found — this guard reads the wrong place"
    return out


def _refuse_an_ambient_parallel_default(workflows: dict[str, object]) -> None:
    """No workflow may hand pytest a parallel flag through the environment.

    THE HOLE THIS CLOSES, which was real and which a 788-test guard subset
    stayed green over: the parallelism pins above read `run:` strings for
    pytest's own flags, and `PYTEST_ADDOPTS` is not one. GitHub merges `env:`
    from three levels — workflow, job and step — and the step is the only one
    anything here used to look at, so `PYTEST_ADDOPTS: "-n auto"` written one
    indentation level out, on the JOB, changed what two of three interpreter
    legs actually ran with every guard still green.

    So this does not enumerate the three levels. It walks the parsed document
    and reports EVERY `env:` mapping wherever it sits, which covers the three
    that exist today and any nesting a later schema adds. Taken as a separate
    function so the mutation proof beside it can feed it a deliberately
    poisoned document and watch it bite, rather than asserting that it would.

    THE `env:` MAPPING IS NOT THE ONLY WAY IN, and the promise here is worded
    to what is actually read (round 1, fail-closed — the first version of
    this docstring promised "any level of any workflow" while reading
    literal mappings only). A `run:` block can `export PYTEST_ADDOPTS=...`
    for its own step, or append it to `$GITHUB_ENV` for every later step in
    the job. Reading shell is not something this guard attempts, so it does
    the only other honest thing: ANY mention of the variable inside a `run:`
    block is refused outright, whatever the value. Over-refusing a spelling
    no workflow here uses costs nothing; reading half of shell and calling it
    coverage is how the job level was missed in the first place.

    THE THIRD WAY IN is a value this guard cannot evaluate — an `env:` whose
    value is a `${{ }}` expression or a shell variable, or an `env:` key that
    is not a mapping at all (GitHub accepts an expression producing one).
    Each is refused as unreadable rather than passed over, because "I could
    not tell" and "it is clean" are the two answers a gate must never
    conflate.

    Why the variable is refused at all rather than merely kept consistent
    with the commands: `PYTEST_ADDOPTS` is inherited by child processes, and
    this suite shells out to pytest — `warden init`'s generated gates run one
    per enrolled fixture repo. A parallel flag exported at the job would
    reach every one of those nested runs, which no caller asked for and no
    reader of any command would see.
    """
    findings = []
    for name, doc in workflows.items():
        for where, env in _env_blocks(doc, name):
            if not isinstance(env, dict):
                findings.append(
                    f"{where}: an `env:` this guard cannot read ({env!r}) — "
                    "it may or may not carry PYTEST_ADDOPTS")
                continue
            value = env.get("PYTEST_ADDOPTS")
            if value is None:
                continue
            text = str(value)
            if "${{" in text or "$" in text:
                findings.append(
                    f"{where}: PYTEST_ADDOPTS={value!r} is computed, so what "
                    "pytest receives cannot be read here")
            elif _parallel_flag(text.split()):
                findings.append(f"{where}: PYTEST_ADDOPTS={value!r}")
        for where, script in _run_blocks(doc, name):
            if "PYTEST_ADDOPTS" in script:
                findings.append(
                    f"{where}: a `run:` block names PYTEST_ADDOPTS, which "
                    "this guard refuses whatever the value — shell is not "
                    "read here")
    assert not findings, (
        "a workflow reaches pytest through PYTEST_ADDOPTS instead of writing "
        "the flags in the command. Every `env:` level — workflow, job and "
        "step — is inherited by the pytest runs this suite shells out to, "
        "and none of it is visible to anyone reading the `run:` line: "
        f"{findings}")


def _env_blocks(node: object, where: str) -> Iterator[tuple[str, object]]:
    """Every `env:` value in a parsed workflow, with a path to it.

    Depth-first over mappings and sequences, so a job's `env`, a step's
    `env` and the workflow's own top-level `env` all arrive, and so does one
    nested anywhere a later schema puts it. The recursion is over the
    document, not over a list of known levels — a guard that enumerates the
    levels it knows is the guard that missed the job level.

    The value is yielded WHATEVER its type. It used to be yielded only when
    it was a mapping, which quietly dropped the one shape nobody can read —
    the caller now refuses that rather than skipping it.

    ONE `env` IS NOT AN ENVIRONMENT: the one under a step's `with:`, which
    is an action INPUT that happens to be named `env` and is a string by
    design (`with: {env: staging}`). Dropping the type test widened this
    past its own subject and would have reported such a step as an
    environment nobody can read — a false red, and one the mutation proof
    does not exercise (round 2, pattern-fit). Excluded by POSITION, which is
    what distinguishes them, rather than by type, which is what used to hide
    the unreadable case.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{where}.{key}"
            if key == "env" and not where.endswith(".with"):
                yield child, value
            yield from _env_blocks(value, child)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _env_blocks(item, f"{where}[{i}]")


def _run_blocks(node: object, where: str) -> Iterator[tuple[str, str]]:
    """Every `run:` script in a parsed workflow, with a path to it.

    The same walk as `_env_blocks` over the other half of a step: the shell
    a job actually executes, where an `export` or a `$GITHUB_ENV` append can
    set for later steps what no `env:` mapping declares.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{where}.{key}"
            if key == "run" and isinstance(value, str):
                yield child, value
            yield from _run_blocks(value, child)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _run_blocks(item, f"{where}[{i}]")


def test_a_parallel_flag_smuggled_through_the_environment_is_caught_at_every_level():
    """The mutation proof for the guard above, executed rather than promised.

    The real `ci.yml` is parsed, then poisoned one way at a time with the
    exact spelling that slipped through before — `PYTEST_ADDOPTS: "-n auto"`
    on the JOB, which is the one that was missed — and with the five other
    ways the same thing can be done: the step and workflow `env:` levels, a
    shell `export` inside a `run:` block, a `$GITHUB_ENV` append that reaches
    every LATER step, and a value the guard cannot evaluate. Each must bite,
    and the unpoisoned document must not.

    The mapping poisons use `-n auto` at the job level and xdist's OTHER
    spellings elsewhere, so a guard that pattern-matched the literal string
    rather than reading argv would fail here.

    The `run:` poisons carry NO negative control, deliberately: the guard
    refuses the variable's name in a `run:` block whatever the value, so
    there is no benign spelling to control against, and a control asserting
    one would be asserting the opposite of the rule.
    """
    import copy

    import yaml

    clean = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text())

    # The control: the tree as it stands passes, so a red below is the
    # mutation and not a pre-existing finding.
    _refuse_an_ambient_parallel_default({"ci.yml": clean})

    def pytest_step(doc: dict) -> dict:
        return next(s for s in doc["jobs"]["tests"]["steps"]
                    if "pytest" in s.get("run", ""))

    def at_workflow(doc: dict) -> None:
        doc["env"] = {"PYTEST_ADDOPTS": "--numprocesses=4"}

    def at_job(doc: dict) -> None:
        doc["jobs"]["tests"]["env"] = {"PYTEST_ADDOPTS": "-n auto"}

    def at_step(doc: dict) -> None:
        pytest_step(doc).setdefault("env", {})["PYTEST_ADDOPTS"] = "-n4"

    # An env mapping the guard cannot read: GitHub accepts an expression
    # that produces one, and "I could not tell" must not read as clean.
    def as_an_expression(doc: dict) -> None:
        doc["jobs"]["tests"]["env"] = "${{ fromJSON(inputs.env) }}"

    def as_a_computed_value(doc: dict) -> None:
        doc["jobs"]["tests"]["env"] = {"PYTEST_ADDOPTS": "${{ inputs.flags }}"}

    # The two shell spellings no `env:` mapping carries: one for this step,
    # one for every step after it in the job.
    def as_a_shell_export(doc: dict) -> None:
        step = pytest_step(doc)
        step["run"] = 'export PYTEST_ADDOPTS="-n auto"\n' + step["run"]

    def as_a_github_env_append(doc: dict) -> None:
        doc["jobs"]["tests"]["steps"].insert(0, {
            "name": "smuggle",
            "run": 'echo "PYTEST_ADDOPTS=-n auto" >> "$GITHUB_ENV"',
        })

    # The label reaches every message. With seven poisons, a bare
    # `DID NOT RAISE` inside a loop names none of them, and the reader of a
    # red is left to work out which shape the guard went blind to (round 2,
    # error-names-cause).
    #
    # EACH POISON CARRIES THE REASON IT EXPECTS, not a disjunction of every
    # reason any poison could earn. Accepting "PYTEST_ADDOPTS" OR "cannot
    # read" for all seven let a refusal misreport a perfectly readable
    # job-level mapping as an unreadable `env:` and still satisfy the proof —
    # the one thing a mutation proof is for is telling those two apart.
    def findings_of(exc: AssertionError) -> str:
        # THE FINDINGS, not the whole message. The standing sentence around
        # them names PYTEST_ADDOPTS whatever the refusal decided, so a search
        # over the message text lets any arm answer for any poison — which is
        # how the disjunction this replaced looked like it was checking
        # something. The findings are rendered as a list at the end and
        # nothing before them opens a bracket.
        text = str(exc)
        return text[text.index("["):]

    def must_bite(label: str, doc: dict, reason: str,
                  not_because: str = "") -> None:
        try:
            _refuse_an_ambient_parallel_default({"ci.yml": doc})
        except AssertionError as exc:
            found = findings_of(exc)
            assert reason in found, (
                f"the `{label}` poison was refused, but not for the reason it "
                f"plants — expected {reason!r} among the findings: {found}")
            assert not (not_because and not_because in found), (
                f"the `{label}` poison was refused for the wrong arm: the "
                f"findings say {not_because!r}, and what this poison plants "
                f"is readable: {found}")
            return
        raise AssertionError(
            f"the `{label}` poison did not bite — a workflow carrying it "
            "reads as clean, so the ambient-default refusal is blind to "
            "that spelling")

    def must_pass(label: str, doc: dict) -> None:
        try:
            _refuse_an_ambient_parallel_default({"ci.yml": doc})
        except AssertionError as exc:
            raise AssertionError(
                f"the `{label}` control is not parallel and not unreadable, "
                f"so refusing it is a false red: {exc}") from exc

    # Each poison carries the value it plants, because that value IS the
    # reason it must earn: a readable mapping has to be refused by quoting
    # what it says. ONE exclusion is needed on top of that, and only one. The
    # unreadable-`env:` arm names no value, so the positive assertion already
    # fails if it fired. The computed arm DOES quote the value, and its
    # message opens with the same bytes — `PYTEST_ADDOPTS=<value>` is a
    # strict prefix of `PYTEST_ADDOPTS=<value> is computed …` — so that one
    # arm can satisfy the positive assertion while answering the wrong
    # question. `not_because` excludes it by its own words.
    mapping_poisons = (("workflow env", at_workflow, "--numprocesses=4"),
                       ("job env", at_job, "-n auto"),
                       ("step env", at_step, "-n4"))
    for label, poison, planted in mapping_poisons:
        mutated = copy.deepcopy(clean)
        poison(mutated)
        must_bite(label, mutated, f"PYTEST_ADDOPTS={planted!r}",
                  not_because="is computed")

        # And the negative control at the same level: an env block that sets
        # PYTEST_ADDOPTS to something that is NOT a parallel flag stays
        # green, so the guard refuses parallelism rather than the variable.
        benign = copy.deepcopy(clean)
        poison(benign)  # same level, value replaced below
        for _, env in _env_blocks(benign, "ci.yml"):
            if isinstance(env, dict) and "PYTEST_ADDOPTS" in env:
                env["PYTEST_ADDOPTS"] = "--no-header -p no:cacheprovider"
        must_pass(f"{label}, benign value", benign)

    # Only the first of these four plants an `env:` that is not a mapping, so
    # only the first may be refused as unreadable. The next plants a value the
    # guard genuinely cannot evaluate, and the last two plant shell, which it
    # refuses on sight and says so.
    for label, poison, reason in (
            ("unreadable env", as_an_expression,
             "an `env:` this guard cannot read"),
            ("computed value", as_a_computed_value,
             "is computed, so what pytest receives cannot be read here"),
            ("shell export", as_a_shell_export,
             "a `run:` block names PYTEST_ADDOPTS"),
            ("GITHUB_ENV append", as_a_github_env_append,
             "a `run:` block names PYTEST_ADDOPTS")):
        mutated = copy.deepcopy(clean)
        poison(mutated)
        must_bite(label, mutated, reason)

    # The one `env` that is not an environment: an action INPUT named `env`,
    # a string by design. The unreadable-value refusal above must not reach
    # it, or every deploy-style step becomes a false red (round 2,
    # pattern-fit).
    action_input = copy.deepcopy(clean)
    action_input["jobs"]["tests"]["steps"].insert(
        0, {"uses": "some/deploy@v1", "with": {"env": "staging"}})
    must_pass("an action input named env", action_input)


def _ini_addopts(text: str) -> str:
    """`addopts` out of an ini-style config, or "" — enough to see a flag.

    READ THE WAY PYTEST READS IT. pytest parses these files with `iniconfig`,
    which hands back the value verbatim; `configparser` performs %-
    interpolation, so an addopts pytest accepts raised out of here instead of
    being scanned and took the rest of the caller down with it, the CI half
    included. Both spellings, and they raise DIFFERENT errors — the very
    ordinary `--log-format=%(asctime)s %(levelname)s` raises
    `InterpolationMissingOptionError`, a bare percent in `-k not%slow` raises
    `InterpolationSyntaxError` — which is why the guard below catches
    `configparser.Error` rather than either by name. `RawConfigParser` does no
    interpolation at all.

    NOT a claim that it IS iniconfig. Measured, two disagreements remain: this
    reader honours a `[DEFAULT]` section and folds option names to lower case,
    and iniconfig does neither, so a `[DEFAULT] addopts` or an `ADDOPTS =` is
    read here and not by pytest. Both are the OVER-reading direction — the
    guard refuses a parallel flag pytest would have ignored — so the failure
    mode is a false red a human reads, never the silent pass this helper
    exists to prevent. A reader that under-read would be the defect.

    THE `except` IS DELIBERATELY NOT WIDENED OVER THE READ, although the
    raise came from `get()`, one line past where the guard ends. Swallowing an
    error on the VALUE would turn a crash into `""`, and `""` here reads as
    "this file sets no addopts" — the caller then passes on a file that does,
    which is the silent hole this whole guard exists to refuse. Fixing the
    parser removes the raise at its source; catching it would have hidden the
    finding instead. `RawConfigParser.get()` cannot raise once `read_string`
    has returned: the section and option are checked first, and there is no
    interpolation left to fail.
    """
    import configparser
    parser = configparser.RawConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return ""
    for section in ("pytest", "tool:pytest"):
        if parser.has_option(section, "addopts"):
            return parser.get(section, "addopts")
    return ""


def _parallel_flag(words: list[str]) -> bool:
    """Does this argv NAME an xdist flag?

    NOT "does it run in parallel" — `-n 0` names the flag and runs the suite
    in-process. The two questions were one until round 1's fail-closed
    finding separated them; `_requests_workers` answers the other, and the
    positive arms of the guard use that one. This stays the wider reader
    because it serves the REFUSAL arms, where over-reading is the safe
    direction: an `addopts` or a `PYTEST_ADDOPTS` carrying `-n 0` is refused
    too, and nobody writes one.

    READ AS WORDS, never as a substring of the whole command. `"-n" in line`
    is true of `--no-header`, `--no-cov` and `--nf`, so it manufactures a red
    with a wrong diagnosis; and `"-n auto" in line` misses xdist's own long
    spelling `--numprocesses auto` and its attached form `-n4`, so the CI half
    passed while CI ran parallel. Both directions were measured in review
    round 1 (pattern-fit).

    Every spelling xdist accepts: `-n N`, `-nN`, `--numprocesses N`,
    `--numprocesses=N`, and `--dist`, which only means anything with workers.
    """
    for i, word in enumerate(words):
        if word in ("-n", "--numprocesses", "--dist"):
            return True
        if word.startswith(("-n", "--numprocesses=", "--dist=")) and word != "-n":
            if word.startswith("-n") and not word[2:3].isalnum():
                continue          # `--no-header` etc: -n must be followed by a value
            return True
        del i
    return False


def test_an_addopts_carrying_a_percent_is_read_and_not_raised_on():
    """The ini read is pytest's read, verbatim, percent included.

    `configparser` interpolates `%`; `iniconfig`, which is what pytest itself
    parses these files with, does not. So an addopts pytest accepts raised out
    of the helper rather than being scanned, and the raise happened one line
    OUTSIDE the guard that was meant to contain it — killing the rest of
    `test_the_gate_runs_the_suite_in_parallel_and_nothing_defaults_to_it`,
    its CI arms included, on a repo that had merely added a `pytest.ini`.

    DERIVED FROM iniconfig, not from a literal: the claim is that this helper
    reads what pytest reads, so pytest's own parser supplies the expectation.
    The last case carries a parallel flag BESIDE the percent, which is the
    whole point — the guard has to keep biting on a file it can now read.
    """
    import iniconfig

    cases = [
        "[pytest]\naddopts = --log-format=%(asctime)s %(levelname)s\n",
        "[pytest]\naddopts = -k not%slow\n",
        "[tool:pytest]\naddopts = --log-cli-format=%(name)s 100%% -q\n",
        "[pytest]\naddopts = -n auto --log-format=%(asctime)s\n",
    ]
    for text in cases:
        section = "pytest" if "[pytest]" in text else "tool:pytest"
        expected = iniconfig.IniConfig("pytest.ini", data=text)[section]["addopts"]
        assert _ini_addopts(text) == expected, (
            f"the ini read no longer agrees with pytest's own parser on "
            f"{text!r} — it read {_ini_addopts(text)!r} where pytest reads "
            f"{expected!r}")

    assert _parallel_flag(_ini_addopts(cases[-1]).split()), (
        "a percent in addopts hides the `-n auto` beside it from the guard, "
        "so the parallel-default refusal stops biting on exactly the "
        "later-added config file it was written to catch")
    assert not _parallel_flag(_ini_addopts(cases[0]).split()), (
        "the percent case with no parallel flag now reads as one")

    # A file that is not ini at all is still "", not a raise: the caller
    # skips what it cannot parse rather than failing the suite on it.
    assert _ini_addopts("this is not an ini file at all\n") == ""
