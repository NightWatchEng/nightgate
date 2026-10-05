"""The toolchain this repo's own suite needs is DECLARED, and the declaration bites.

`tests/test_init.py` enrolls a fixture repo per language and RUNS the verify
scope `warden init` wrote for it, so the suite shells out to `go`, `npm` and
`uv` for real. Undeclared, CI stays green only because the GitHub-hosted image
preinstalls Go and Node — a dependency on a runner image — and a first clone
goes red with cells whose entire stated reason is `assert 1 == 0` over a buried `/bin/sh: npm: command
not found`.

`CLAUDE.md`'s first principle is policy -> enforcement -> evidence, and a
README line nothing checks is precisely the failure mode it exists to prevent.
So the declaration in `pyproject.toml` is held by three checks, in three
different places, none of which can be satisfied by prose:

  1. it COVERS what the enrollment surface runs, derived from
     `enroll.verify_commands` rather than retyped, so a fourth language in
     `enroll.LANGUAGES` reddens here;
  2. CI INSTALLS every entry with a pinned action and runs the suite in strict
     mode, so the runner image stops being the real declaration;
  3. an absent tool produces a NAMED skip or a NAMED failure at the point of
     use, never a bare 127 — proven by driving `toolchain.require` against a
     PATH that genuinely lacks the binary, not by asserting it would.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import toolchain
import yaml

from warden import enroll as enroll_mod
from warden import verify as verify_mod

ROOT = Path(__file__).resolve().parent.parent
CI_YAML = ROOT / ".github" / "workflows" / "ci.yml"
#: The CONTRIBUTING.md heading whose section must name every declared tool.
PREREQ_HEADING = "## What the suite needs on your machine"

#: Which `uses:` action supplies which declared binary. Stated here so a tool
#: added to `require` with no way to install it in CI fails check 2 loudly
#: rather than being skipped by a lookup that quietly finds nothing.
_INSTALLER = {
    "uv": "astral-sh/setup-uv",
    "go": "actions/setup-go",
    "npm": "actions/setup-node",
    "java": "actions/setup-java",
    # No first-party action installs Maven: setup-java installs the JDK only.
    # stCarolas/setup-maven downloads the exact release from Maven Central.
    "mvn": "stCarolas/setup-maven",
    "gradle": "gradle/actions/setup-gradle",
}


@pytest.fixture(autouse=True)
def _no_ambient_legacy_switch(monkeypatch):
    """The old variable name is read as an alias (`toolchain.strict()`), so an
    ambient one — a cage's run.sh still exports it — would decide every cell
    below that clears only the new name. Cleared here; the cells that are ABOUT
    the alias set it themselves."""
    monkeypatch.delenv(toolchain.LEGACY_STRICT_ENV, raising=False)


# ---------- 1. the declaration covers what the suite actually runs ----------

def test_the_declaration_covers_every_binary_the_enrollment_surface_runs():
    r"""DERIVED, never retyped — the same direction `test_self_cage.py` uses.

    `warden init` writes a verify scope per detected language and this suite
    runs it. So every binary `enroll.verify_commands` can emit, for every
    language `enroll.LANGUAGES` supports, must be named in
    `[tool.nightgate.toolchain].require`. Adding a fourth language reddens this
    line instead of costing a contributor an afternoon on an `exit 127`.

    The direction is code -> declaration only. Declaring MORE than the suite
    needs is allowed and costs a skip; declaring less costs the diagnosis.
    """
    declared = set(toolchain.declared())
    assert declared, "[tool.nightgate.toolchain].require names no binary"

    # EVERY BRANCH, not one sample. Round 1 X6: probing a single empty
    # directory walks one branch per language — `verify_commands` also reads
    # uv.lock, package-lock.json and package.json's test script — so the
    # guarantee "a fourth language arrives with its toolchain already named"
    # held by luck rather than by construction.
    shapes = {
        "bare": {},
        "uv-locked": {"uv.lock": "version = 1\n"},
        "npm-locked": {"package-lock.json": '{"name":"demo","lockfileVersion":3}\n'},
        "npm-tested": {"package.json": '{"name":"demo","scripts":{"test":"node --test"}}\n'},
        "npm-placeholder": {"package.json": json.dumps(
            {"name": "demo", "scripts": {"test": enroll_mod.NPM_INIT_PLACEHOLDER}}) + "\n"},
        "maven": {"pom.xml": "<project/>\n"},
        "gradle": {"build.gradle": ""},
        "gradle-wrapper": {"build.gradle.kts": "", "gradlew": "#!/bin/sh\n"},
    }
    needed, commands = set(), set()
    for shape, files in shapes.items():
        with tempfile.TemporaryDirectory() as probe:
            for rel, text in files.items():
                (Path(probe) / rel).write_text(text)
            for lang in enroll_mod.LANGUAGES:
                for command in enroll_mod.verify_commands(Path(probe), lang):
                    commands.add(command)
                    # A repository's own wrapper resolves against its checkout,
                    # never PATH, so `first_binary` declines it; what it needs
                    # from PATH is the binary enroll says it runs on.
                    binary = (verify_mod.first_binary(command)
                              or enroll_mod.WRAPPER_RUNS_ON.get(command.split()[0]))
                    assert binary, (
                        f"`warden init` can write the verify step {command!r} "
                        f"(repo shape {shape!r}) and `verify.first_binary` "
                        "cannot name its program, so no declaration can cover "
                        "it and `verify --scope` could only ever report it as "
                        "a bare exit 127")
                    needed.add(binary)
    # EXACT, not a floor (round 2 nit): a floor one short lets a probe shape
    # leave without tripping anything. Changing this number is a deliberate
    # edit that says a branch was added or removed.
    assert len(commands) == 10, (
        f"the probe shapes walked {len(commands)} distinct verify commands "
        f"({sorted(commands)}), not the 10 branches `enroll.verify_commands` "
        "holds (python x2 lock states, node install x2 plus `npm test`, go "
        "x2, java mvn, gradle and the gradle wrapper). Fewer means this guard samples rather than covers and a new "
        "branch could carry an undeclared binary; more means a branch was "
        "added and this number should move with it")

    missing = sorted(needed - declared)
    assert not missing, (
        f"`warden init` writes verify steps that run {missing}, this suite "
        f"RUNS those steps in tests/test_init.py, and pyproject.toml's "
        f"[tool.nightgate.toolchain].require does not declare them (it declares "
        f"{sorted(declared)}). Undeclared means CI green by runner-image "
        "accident and a fresh laptop red with no cause.")


def test_every_declared_tool_has_a_way_to_be_installed_in_ci():
    """The declaration is only worth as much as check 2 can enforce.

    A tool added to `require` with no entry in `_INSTALLER` would make the CI
    guard below iterate over nothing for it and pass — the quiet-pass shape
    this whole bead is about. Fail here instead, where the message can say so.
    """
    unmapped = sorted(set(toolchain.declared()) - set(_INSTALLER))
    assert not unmapped, (
        f"{unmapped} is declared in [tool.nightgate.toolchain].require and this "
        "file knows no action that installs it, so "
        "test_ci_installs_every_declared_toolchain would silently check "
        "nothing for it. Add it to _INSTALLER with the pinned action.")


# ---------- 2. CI declares the toolchain instead of inheriting it -----------

def _ci() -> dict:
    return yaml.safe_load(CI_YAML.read_text())


def _runs_the_suite(step: dict) -> bool:
    """Whether this step puts THIS repo's pytest suite on a runner.

    Two spellings, and the second is the one round 1 (F2) found missing:
    `pytest` directly, and `warden verify --scope tests`, which runs
    repo.yaml's `tests` scope, which runs the same suite. A guard that saw only
    the first blessed a workflow that installed the toolchain in the job that
    does NOT gate while the gating job still inherited it from the image.
    """
    run = str(step.get("run", ""))
    return "pytest" in run or "verify --scope tests" in run


def _suite_jobs() -> dict[str, dict]:
    """Every job that runs the suite, DERIVED from the workflow.

    Named jobs would have to be kept in step with ci.yml by hand, which is the
    drift this whole bead is about; a third job that starts running the suite
    inherits the requirement instead.
    """
    jobs = {name: job for name, job in _ci()["jobs"].items()
            if any(_runs_the_suite(s) for s in job.get("steps", []))}
    assert jobs, "no job in ci.yml runs the suite — this guard reads it wrong"
    return jobs


def _pytest_step(job: dict) -> dict:
    steps = [s for s in job["steps"] if "pytest" in str(s.get("run", ""))]
    assert len(steps) == 1, (
        f"expected exactly one pytest step, found {len(steps)} — this guard "
        "names the wrong one")
    return steps[0]


def test_every_ci_job_that_runs_the_suite_installs_the_declared_toolchain():
    """The runner image stops being the undeclared dependency — in EVERY job.

    Read from the workflow YAML, which is the only statement CI acts on. A
    README sentence saying "CI installs Go" is the failure mode `CLAUDE.md`'s
    first principle names. Round 1 F2: the first cut of this guard read only
    `jobs.tests`, so the `gate` job — the one whose verdict actually gates —
    kept running the same five go/npm cells on whatever the image shipped.

    The floor below names `verify` where it used to name `gate`, and the
    substitution is the whole of the change that made it: the gate job no
    longer runs
    the suite, it TAKES the `verify` job's result for the scope. The floor
    exists so that a derivation reading the workflow wrong fails here rather
    than reporting a clean sweep of nothing, so it has to name the jobs that
    actually run it — and `verify` is the one whose verdict now feeds the gate.
    """
    jobs = _suite_jobs()
    assert {"tests", "verify"} <= set(jobs), (
        f"the derivation found {sorted(jobs)}; both the matrix suite job and "
        "the job whose verify result the gate takes are expected to run the "
        "suite, so a derivation that misses one is reading the workflow wrong "
        "rather than reporting a fix")
    assert "gate" not in jobs, (
        "the `gate` job runs the suite again. It is meant to TAKE the "
        "`verify` job's result for the tests scope: a fourth "
        "run of the suite on every pull request cost a mean of 15.3 "
        "runner-minutes and proved what the interpreter legs had already "
        f"proved. Derivation found {sorted(jobs)}")
    for name, job in jobs.items():
        uses = [str(s["uses"]).split("@")[0] for s in job["steps"] if "uses" in s]
        for tool in toolchain.declared():
            action = _INSTALLER[tool]
            assert action in uses, (
                f"job `{name}` runs this suite and never runs `{action}`, so "
                f"`{tool}` reaches it only because the ubuntu-latest image "
                f"happens to ship it, so an image change turns "
                f"tests/test_init.py red with exit 127 and no cause.")


def test_the_toolchain_actions_are_pinned_to_a_commit_like_every_other():
    """A floating tag is a different action tomorrow.

    The repo pins every action to a SHA and keeps them fresh through
    .github/dependabot.yml. A toolchain installer added at `@v7`
    would be the one unpinned step in the job that proves portability.
    """
    installers = {_INSTALLER[t] for t in toolchain.declared()}
    seen = 0
    for job in _suite_jobs().values():
        for step in job["steps"]:
            ref = str(step.get("uses", ""))
            name, _, pin = ref.partition("@")
            if name not in installers:
                continue
            seen += 1
            assert len(pin) == 40 and all(c in "0123456789abcdef" for c in pin), (
                f"{ref} is not pinned to a 40-character commit SHA")
    assert seen >= len(installers), (
        f"only {seen} installer step(s) found — this guard would pass vacuously "
        "if the steps were deleted; the installs guard above is what catches "
        "that, and this control keeps the two from failing open together")


def test_the_toolchain_is_installed_before_the_suite_runs():
    """Order, which presence alone does not give (round 1, X6).

    A setup step placed after the pytest step installs a toolchain the suite
    has already run without. Correct in fact today, and now in policy.
    """
    installers = {_INSTALLER[t] for t in toolchain.declared()}
    for name, job in _suite_jobs().items():
        steps = job["steps"]
        first_suite = next(i for i, s in enumerate(steps) if _runs_the_suite(s))
        for i, step in enumerate(steps):
            if str(step.get("uses", "")).split("@")[0] in installers:
                assert i < first_suite, (
                    f"job `{name}` installs a toolchain at step {i}, after the "
                    f"suite runs at step {first_suite}")


def test_the_gate_scope_is_strict_because_it_is_the_contract():
    """Round 1 F3 — the sharpest half of the skip-vs-fail split.

    `warden verify --scope tests` is this repo's own GATE. Before this, it ran
    a bare `uv run pytest -q`, so the lenient default applied to the gate as
    well as to a laptop and the scope could record PASS on a machine that never
    ran the go cell — a gate green over the one executable proof here that the
    enrollment surface works for a language the platform is not written in.
    Putting the marker in repo.yaml rather than in pytest's `addopts` is what
    keeps both halves true: the gate contracts, a contributor's bare run does
    not.
    """
    scope = yaml.safe_load((ROOT / "repo.yaml").read_text())["verify"]["tests"]
    pytest_steps = [s["run"] for s in scope if "pytest" in s["run"]]
    assert pytest_steps, "repo.yaml's tests scope no longer runs pytest"
    for run in pytest_steps:
        assert f"{toolchain.STRICT_ENV}=1" in run, (
            f"repo.yaml's tests scope runs {run!r} without "
            f"{toolchain.STRICT_ENV}=1, so this repo's own gate can pass on a "
            "machine that skipped the toolchain cells")


def _is_strict(text: str) -> bool:
    raw = str(text).strip().lower()
    return bool(raw) and raw not in ("0", "false", "no", "off")


def _strict_scopes() -> set[str]:
    """repo.yaml scopes whose own commands set the strict variable."""
    verify = yaml.safe_load((ROOT / "repo.yaml").read_text())["verify"]
    return {scope for scope, steps in verify.items()
            if any(f"{toolchain.STRICT_ENV}=1" in s["run"] for s in steps)}


def _step_is_strict(step: dict) -> bool:
    """Whether this step runs the suite strictly, either way it can.

    A step carries the variable itself, OR it invokes a repo.yaml scope whose
    command does — which is how the `verify` job qualifies: it runs
    `warden verify --scope tests`, and that scope's own pytest command sets it.
    Reading only the step's `env` would call that job unstrict when it is the
    strictest thing in the workflow — and it is the one whose result the gate
    TAKES, so an unstrict reading of it is an unstrict reading of the gate.
    """
    if _is_strict((step.get("env") or {}).get(toolchain.STRICT_ENV, "")):
        return True
    run = str(step.get("run", ""))
    return any(f"--scope {scope}" in run for scope in _strict_scopes())


def test_every_ci_job_that_runs_the_suite_runs_it_strictly():
    """The half that makes an absent tool RED where it was promised.

    Without it the setup steps could stop working and the go cell would skip
    green — losing the portability proof while the run stayed green.

    Round 2 R2F4: the first cut of this guard read `jobs.tests` by name, which
    is the very shape round 1 F2 raised one guard over, so a third job running
    the suite without the flag left it green. It derives the jobs now. A job
    satisfies the requirement in either of the two honest ways: the step
    carries the variable, or it invokes a repo.yaml scope that does (which is
    how the `verify` job gets there — `warden verify --scope tests` runs the
    scope whose own command sets it). The `gate` job used to be that second
    way's example and no longer runs the suite at all, so it is not one of the
    jobs this guard reaches.
    """
    for name, job in _suite_jobs().items():
        unstrict = [str(s.get("run", "")).strip().splitlines()[0]
                    for s in job["steps"]
                    if _runs_the_suite(s) and not _step_is_strict(s)]
        assert not unstrict, (
            f"job `{name}` runs this suite at {unstrict} without "
            f"{toolchain.STRICT_ENV} — neither on the step nor in the "
            f"repo.yaml scope it invokes — so an absent toolchain would SKIP "
            "there. A skipped portability proof is not a passed one.")


def test_every_ci_job_that_runs_pytest_prints_the_reason_for_every_skip():
    """The lenient half has to be legible wherever the gate is read.

    The declared toolchain depends on `-rs`: a named skip nobody prints is a
    silent gap. Derived over the
    suite jobs for R2F4's reason; only the steps that invoke pytest DIRECTLY
    can carry the flag, since a `warden verify` step's command lives in
    repo.yaml. So the flag is asked of the interpreter legs and not of the
    `verify` job, whose skip reasons reach a reader only through the verify
    artifact's own output tail — the one legibility cost of running the scope
    through warden rather than by hand.
    """
    checked = 0
    for name, job in _suite_jobs().items():
        for step in job["steps"]:
            run = str(step.get("run", ""))
            if "pytest" not in run:
                continue
            checked += 1
            assert "-rs" in run, (
                f"job `{name}` runs pytest without -rs, so a toolchain skip "
                "would print as a number with no name")
    assert checked, "no direct pytest step found — this guard reads ci.yml wrong"


def test_the_contributor_page_names_the_toolchain_it_asks_them_to_install():
    """The page a newcomer reads before the suite is the page that must say it.

    CONTRIBUTING.md's "The mechanics" tells a contributor `uv run pytest -q`
    must be green. That is the exact sentence that walked someone into five
    unexplained `exit 127` cells, so the prerequisite belongs one heading
    above it — and belongs THERE checked, because a README line nothing
    verifies is the failure mode `CLAUDE.md`'s first principle exists to
    prevent. Adding a tool to `require` and not to the page reddens here.
    """
    page = (ROOT / "CONTRIBUTING.md").read_text()
    assert PREREQ_HEADING in page, (
        f"CONTRIBUTING.md lost the {PREREQ_HEADING!r} section this guard reads")
    # The SECTION, not the page. Round 1 X6: a bare substring over the whole
    # file was already vacuous for `uv` — one incidental mention elsewhere
    # satisfied it — so deleting the prerequisites outright would have left
    # that arm green. Word boundaries too, or "going" would satisfy `go`.
    section = page.split(PREREQ_HEADING, 1)[1].split("\n## ", 1)[0]
    for tool in toolchain.declared():
        assert re.search(rf"\b{re.escape(tool)}\b", section), (
            f"`{tool}` is declared in [tool.nightgate.toolchain].require and "
            f"CONTRIBUTING.md's {PREREQ_HEADING!r} section never names it, so "
            f"a contributor's first clone can still fail for a reason no page "
            f"they read mentions")
    assert toolchain.STRICT_ENV in section, (
        f"the section does not name {toolchain.STRICT_ENV}, so a contributor "
        "whose environment exports CI gets five failures where the page "
        "promises named skips, with no escape hatch stated (round 1, F6)")


# ---------- 3. the absence is NAMED, driven rather than asserted ------------

@pytest.fixture
def no_go(tmp_path, monkeypatch):
    """A PATH that genuinely does not contain `go`.

    A symlink farm rather than a stub that exits 127: `toolchain.require` asks
    `shutil.which`, and a stub would still resolve. This is the same
    construction the bead's evidence run used on the whole suite.
    """
    farm = tmp_path / "bin"
    farm.mkdir()
    for name in ("sh", "git", "env"):
        real = shutil.which(name)
        if real:
            (farm / name).symlink_to(real)
    monkeypatch.setenv("PATH", str(farm))
    assert shutil.which("go") is None, "the control: go really is unreachable"
    return farm


def test_an_absent_tool_is_a_named_skip_on_a_machine_that_promised_nothing(
        no_go, monkeypatch):
    """A laptop. The reason names the binary, the cause and the cure.

    It is NOT "required -> red": the corpus's REQUIRED class is "what both
    callers run under AND EVERY MACHINE HAS"; `go` on a Python project's
    contributor machine is neither. Red there does not report a broken
    environment, it teaches a newcomer the project is broken.
    """
    monkeypatch.delenv(toolchain.STRICT_ENV, raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert toolchain.strict() is False

    # `pytest.skip.Exception` BY NAME, never `Exception`: pytest's outcomes
    # derive from BaseException, so `pytest.raises(Exception)` does not catch
    # them — it lets the skip through and the TEST skips, reporting green
    # while asserting nothing — a can't-fail shape, and it caught this file on
    # its first run.
    with pytest.raises(pytest.skip.Exception) as caught:
        toolchain.require("go")
    reason = str(caught.value.msg)
    assert "go" in reason and "not found on PATH" in reason, reason
    assert "go.dev" in reason or "brew install go" in reason, (
        f"the skip reason does not say how to close the gap: {reason}")
    assert "[tool.nightgate.toolchain].require" in reason, (
        f"the skip reason does not name the declaration: {reason}")


def test_an_absent_tool_is_a_named_failure_where_it_was_promised(
        no_go, monkeypatch):
    """CI. The environment said it would supply the toolchain and did not.

    #276 made the cage's `go` cell FAIL rather than skip, and that ruling
    holds exactly here: the cage DECLARES AND SUPPLIES its toolchain
    (`[toolchain].path` beside `.require`), so an absence inside it is a
    breach. Strict mode is this repo's CI making the same promise.
    """
    monkeypatch.setenv(toolchain.STRICT_ENV, "1")
    assert toolchain.strict() is True

    with pytest.raises(pytest.fail.Exception) as caught:
        toolchain.require("go")
    reason = str(caught.value.msg)
    assert "go" in reason and "not found on PATH" in reason, reason
    assert toolchain.STRICT_ENV in reason, reason


def test_a_present_tool_costs_nothing(monkeypatch):
    """The control. Without it the two above pass on a `require` that always
    raises, which would turn every toolchain-using cell into a skip and read
    as green."""
    monkeypatch.delenv(toolchain.STRICT_ENV, raising=False)
    assert toolchain.require("sh") is None
    assert toolchain.missing("sh") == ()


def test_only_the_absent_tools_are_named(no_go, monkeypatch):
    """A gap report that lists present tools is noise the reader learns to
    ignore, which is how the next real one gets missed."""
    monkeypatch.delenv(toolchain.STRICT_ENV, raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert toolchain.missing("sh", "go", "git") == ("go",)
    with pytest.raises(pytest.skip.Exception) as caught:
        toolchain.require("sh", "go", "git")
    reason = str(caught.value.msg)
    assert "go" in reason and "TOOLCHAIN GAP: go not" in reason, reason


# ---------- the strict switch answers to the environment, in both directions -

@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False),
])
def test_strict_reads_the_declaration(value, expected, monkeypatch):
    monkeypatch.setenv(toolchain.STRICT_ENV, value)
    monkeypatch.setenv("CI", "1" if not expected else "0")
    assert toolchain.strict() is expected, (
        "the explicit variable must win over ambient CI in BOTH directions, "
        "or a contributor debugging under a stripped PATH has no way to say "
        "so and a forge that does not set CI has no way to be strict")


def test_an_unset_declaration_falls_back_to_the_forge(monkeypatch):
    monkeypatch.delenv(toolchain.STRICT_ENV, raising=False)
    monkeypatch.setenv("CI", "true")
    assert toolchain.strict() is True
    monkeypatch.delenv("CI", raising=False)
    assert toolchain.strict() is False


def test_an_empty_variable_is_not_a_declaration(monkeypatch):
    """`env NIGHTGATE_REQUIRE_TOOLCHAIN= pytest` and a forge that exports an
    empty CI are the same thing: nobody said anything. Reading either as
    truthy would make a laptop strict by accident, and reading an empty
    STRICT_ENV as `false` would mask a CI that did set one."""
    monkeypatch.setenv(toolchain.STRICT_ENV, "")
    monkeypatch.setenv("CI", "1")
    assert toolchain.strict() is True
    monkeypatch.setenv("CI", "")
    assert toolchain.strict() is False


# ---------- the Nightgate rename: new spellings, old ones as aliases ----------
#
# The product was renamed to Nightgate. The switch and the pyproject table
# moved with it; the old spellings are read for one release
# (`toolchain.SUNSET_RELEASE`) and every run that reads one says so.

def _pytest_probe(env: dict) -> subprocess.CompletedProcess:
    """One cheap cell of this file, run as a real pytest session under `env`."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_toolchain.py::test_a_present_tool_costs_nothing"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)


def test_new_toolchain_env_var_makes_the_gate_strict(no_go, monkeypatch):
    """The new name alone, with neither the old name nor CI to lean on, turns
    an absent tool into a named failure — and it is the name the gate's own
    scope sets, so the gate is strict by the new spelling, not by an alias."""
    assert toolchain.STRICT_ENV == "NIGHTGATE_REQUIRE_TOOLCHAIN"
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(toolchain.STRICT_ENV, "1")
    assert toolchain.strict() is True
    with pytest.raises(pytest.fail.Exception) as caught:
        toolchain.require("go")
    assert "NIGHTGATE_REQUIRE_TOOLCHAIN" in str(caught.value.msg)
    assert toolchain.deprecations() == [], (
        "the new spelling alone is reported as deprecated")
    scope = yaml.safe_load((ROOT / "repo.yaml").read_text())["verify"]["tests"]
    runs = [s["run"] for s in scope if "pytest" in s.get("run", "")]
    assert runs and all(r.startswith("NIGHTGATE_REQUIRE_TOOLCHAIN=1 ")
                        for r in runs), runs
    assert not any(toolchain.LEGACY_STRICT_ENV in r for r in runs), runs


def test_old_toolchain_env_var_still_works_and_warns_with_the_sunset_release(
        monkeypatch):
    """The old name keeps a promising environment strict until the sunset,
    and says so. Both halves: `strict()` honours it, and a real pytest run
    under it prints the deprecation line naming the release it goes away in —
    in the terminal summary, which `-q` still writes."""
    monkeypatch.delenv(toolchain.STRICT_ENV, raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(toolchain.LEGACY_STRICT_ENV, "1")
    assert toolchain.strict() is True
    # A name no machine has, rather than the `no_go` PATH farm: the real
    # pytest run below needs this process's own PATH.
    with pytest.raises(pytest.fail.Exception):
        toolchain.require("no-such-toolchain-binary")
    lines = toolchain.deprecations()
    assert len(lines) == 1, lines
    assert ("AGENTOPS_REQUIRE_TOOLCHAIN" in lines[0]
            and "NIGHTGATE_REQUIRE_TOOLCHAIN" in lines[0]
            and "v4.0.0" in lines[0]), lines

    # The new name wins in both directions when both are set: an explicit
    # new-name opt-out is not overridden by an old export, and is not
    # reported as a deprecated read.
    monkeypatch.setenv(toolchain.STRICT_ENV, "0")
    assert toolchain.strict() is False
    assert toolchain.deprecations() == []
    monkeypatch.setenv(toolchain.LEGACY_STRICT_ENV, "0")
    monkeypatch.setenv(toolchain.STRICT_ENV, "1")
    assert toolchain.strict() is True

    # Printed by a real run, and only when the old name is what was read.
    # The probe is run without `-n`: the line comes from the controlling
    # process's terminal summary either way.
    env = {k: v for k, v in os.environ.items()
           if k not in (toolchain.STRICT_ENV, "CI", "PYTEST_ADDOPTS")
           and not k.startswith("PYTEST_XDIST")}
    env[toolchain.LEGACY_STRICT_ENV] = "1"
    proc = _pytest_probe(env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ("DEPRECATED: AGENTOPS_REQUIRE_TOOLCHAIN" in proc.stdout
            and "v4.0.0" in proc.stdout), proc.stdout
    env.pop(toolchain.LEGACY_STRICT_ENV)
    proc = _pytest_probe(env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DEPRECATED" not in proc.stdout, (
        f"a run on the new spellings prints a deprecation line: {proc.stdout}")


def test_old_pyproject_toolchain_key_is_read_as_an_alias(monkeypatch, tmp_path):
    """A pyproject still carrying `[tool.agentops.toolchain]` declares the same
    toolchain until the sunset, and says so; the new table wins when both are
    present; a malformed old table fails loud under its own name."""
    old = tmp_path / "old.toml"
    old.write_text('[tool.agentops.toolchain]\nrequire = ["uv", "go"]\n')
    monkeypatch.setattr(toolchain, "PYPROJECT", old)
    assert toolchain.declared() == ("uv", "go")
    lines = toolchain.deprecations()
    assert len(lines) == 1, lines
    assert ("[tool.agentops.toolchain]" in lines[0]
            and "[tool.nightgate.toolchain]" in lines[0]
            and "v4.0.0" in lines[0]), lines

    both = tmp_path / "both.toml"
    both.write_text('[tool.agentops.toolchain]\nrequire = ["go"]\n'
                    '[tool.nightgate.toolchain]\nrequire = ["uv"]\n')
    monkeypatch.setattr(toolchain, "PYPROJECT", both)
    assert toolchain.declared() == ("uv",)
    assert toolchain.deprecations() == []

    bad = tmp_path / "bad.toml"
    bad.write_text('[tool.agentops.toolchain]\nrequire = "go"\n')
    monkeypatch.setattr(toolchain, "PYPROJECT", bad)
    with pytest.raises(AssertionError, match=r"\[tool\.agentops\.toolchain\]"):
        toolchain.declared()

    # This repo's own pyproject is on the new table.
    monkeypatch.setattr(toolchain, "PYPROJECT", ROOT / "pyproject.toml")
    assert toolchain.deprecations() == []
    assert toolchain.declared(), "the new table declares no toolchain"


def test_the_declaration_must_be_a_list_of_strings(monkeypatch, tmp_path):
    """The loader fails LOUD. A malformed declaration that read as `()` would
    make check 1 report "names no binary" instead of the real problem, and
    would make `require` a no-op everywhere."""
    bad = tmp_path / "pyproject.toml"
    bad.write_text('[tool.nightgate.toolchain]\nrequire = "go"\n')
    monkeypatch.setattr(toolchain, "PYPROJECT", bad)
    with pytest.raises(AssertionError, match="list of strings"):
        toolchain.declared()


def test_every_declared_tool_says_how_to_install_it():
    """A gap report that names a binary and not the cure is half a report.

    `toolchain.reason` falls back to a bare "install it" for a tool with no
    hint, which reads fine and helps nobody. This names each member so a
    dropped one is red rather than a quiet degradation — and it is what pins
    `_INSTALL_HINT` in tests/test_guard_mutations.py.
    """
    for tool in toolchain.declared():
        hint = toolchain._INSTALL_HINT.get(tool)
        assert hint, (
            f"`{tool}` is declared in [tool.nightgate.toolchain].require and "
            f"tests/toolchain.py has no install hint for it, so its skip "
            f"reason would say 'install it' and stop there")
        assert tool in toolchain.reason((tool,)) and hint in toolchain.reason((tool,))


# ---------- the third arm, made a checker rather than a convention ----------
#
# Round 1 X2: "every suite site that shells out calls `require()`" was a
# CONVENTION wearing an enforcement costume. The call lives in
# `tests/test_init.py::_verify`, which is structural for anything routed
# through that helper — but a new test invoking `warden verify --scope go`
# directly bypasses it entirely, and the cross-examiner reproduced exactly
# that: a red cell on a laptop, the outcome this bead exists to prevent, with
# every guard in this file still green.
#
# So the claim is now checked. `warden verify --scope <lang>` is the ONE way
# this suite reaches a language toolchain, so the rule is narrow enough to
# state exactly: in tests/test_init.py, a `_warden(..., "verify", ...)` call
# belongs inside `_verify` unless it is named below with the reason its scope
# needs nothing undeclared.

#: Functions allowed to invoke `warden verify` without routing through
#: `_verify`: function name -> (the ONE scope it may run, the reason). Short
#: by design — it is the one place this guard takes an argument instead of a
#: test.
#:
#: Keyed on the SCOPE as well as the name (round 2, R2F5). Keyed on the name
#: alone, a carved test that later switched to the `go` scope would keep its
#: exemption in silence, which is the shape of exemption this repo does not
#: allow anywhere else.
_VERIFY_CARVE_OUT = {
    "test_review_in_another_checkout_pairs_the_verify_result_the_gate_took": (
        "python",
        "runs the `python` scope only, whose binary is `uv` — present by "
        "construction in any run of this suite, since `uv` is what started it"),
}


def _warden_verify_callers(tree) -> dict[str, list[tuple[int, str | None]]]:
    """Every function lexically containing a `_warden(..., "verify", ...)`
    call: name -> [(line, the literal scope it runs, or None)].

    An AST walk rather than a regex: a regex over the text cannot tell a call
    from the same words in a docstring or an assertion message, and this file
    is full of both.

    WHAT THIS DOES NOT SEE, stated because round 2 (R2F5) refused a claim
    wider than its enforcement: a call splatted from a variable or a tuple, a
    helper that wraps `_warden`, a `subprocess.run` straight to a toolchain,
    and any file other than `tests/test_init.py`. It covers a DIRECT
    `_warden(...)` call carrying a literal "verify" argument in that one file,
    which is the shape every toolchain-running site in this suite has today
    and the shape the cross-examiner walked in with.
    """
    import ast
    out: dict[str, list[tuple[int, str | None]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            fn = call.func
            if not (isinstance(fn, ast.Name) and fn.id == "_warden"):
                continue
            args = [a.value if isinstance(a, ast.Constant) else None
                    for a in call.args]
            if "verify" not in args:
                continue
            scope = None
            if "--scope" in args:
                index = args.index("--scope") + 1
                scope = args[index] if index < len(args) else None
            out.setdefault(node.name, []).append((call.lineno, scope))
    return out


def test_no_suite_site_reaches_a_toolchain_around_the_declaration():
    """The enforcement X2 said was missing.

    `_verify` calls `toolchain.require` with the binaries derived from the
    enrolled repo's own repo.yaml, so anything going through it is covered. A
    call that goes around it is a cell that can still fail with a bare exit 127
    on a contributor's first clone, which is what the declaration prevents.
    """
    import ast
    source = (ROOT / "tests" / "test_init.py").read_text()
    callers = _warden_verify_callers(ast.parse(source))
    assert callers, (
        "no `_warden(..., 'verify', ...)` call found in tests/test_init.py — "
        "this guard is reading the file wrong, and a guard that finds nothing "
        "passes vacuously")
    rogue = {name: calls for name, calls in callers.items()
             if name != "_verify" and name not in _VERIFY_CARVE_OUT}
    assert not rogue, (
        f"{sorted(rogue)} invoke `warden verify` without routing through "
        f"`_verify`, so they shell out to whatever binary that scope needs "
        f"with nothing checking it is present ({rogue}). Route them through "
        f"`_verify`, or name each in _VERIFY_CARVE_OUT with the scope it runs "
        f"and the reason that scope needs no declared toolchain.")
    # R2F5: the carve-out is spent on ONE scope, not on the function.
    for name, (allowed, _reason) in _VERIFY_CARVE_OUT.items():
        for line, scope in callers.get(name, []):
            assert scope == allowed, (
                f"{name} is carved out for the `{allowed}` scope and runs "
                f"`{scope}` at line {line}. The exemption does not follow the "
                "function to a scope with a different toolchain — re-justify "
                "it or route the call through `_verify`.")


def test_every_verify_carve_out_names_a_real_function():
    """A carve-out that outlives its subject is an exemption nobody can audit,
    and it would silently re-admit the shape it was written to allow."""
    import ast
    source = (ROOT / "tests" / "test_init.py").read_text()
    callers = _warden_verify_callers(ast.parse(source))
    stale = sorted(set(_VERIFY_CARVE_OUT) - set(callers))
    assert not stale, (
        f"{stale} is carved out of the verify-routing guard and no longer "
        "invokes `warden verify` — drop the entry")


def test_the_routing_guard_would_catch_a_bypass():
    """The control. X2's reproduction, run against the guard rather than
    described: the detector must fire on the exact source shape the
    cross-examiner appended to test_init.py, or it is a guard that cannot
    fail."""
    import ast
    planted = (
        "def test_a_new_go_test(tmp_path, monkeypatch):\n"
        "    repo, _ = _enrolled(tmp_path, monkeypatch, FIXTURES['go'])\n"
        "    assert _warden(repo, monkeypatch, 'verify', '--scope', 'go') == 0\n")
    found = _warden_verify_callers(ast.parse(planted))
    assert "test_a_new_go_test" in found, found
    assert found["test_a_new_go_test"][0][1] == "go", found
    # and the negative: a docstring or message mentioning the words is not a call
    innocent = (
        'def test_prose():\n'
        '    """_warden(repo, monkeypatch, "verify") is what _verify does."""\n'
        '    assert True, "run warden verify --scope go by hand"\n')
    assert _warden_verify_callers(ast.parse(innocent)) == {}
