"""`warden declare check` — the mechanism that binds a declaration to behaviour.

The defect these checks exist for: the gate states, or relies on, something
nothing verifies. These tests are what makes the detector a detector — each
check must be shown catching its own instance before it is allowed to report
clean, because a guard that has never failed proves nothing about what it
guards.

The integration test at the bottom is the one that keeps THIS repo honest: the
four checks are clean here, including D-03's `--scope example` step and D-04's
tiers for `.cage/`, `.warden/`, `repo.yaml`, `.githooks/` and `.claude/`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from warden import cli
from warden import config as config_mod
from warden import declared as declared_mod

ROOT = Path(__file__).parent.parent


def _repo(tmp_path: Path, raw: dict, *, rules: bool = True) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    (root / "repo.yaml").write_text(yaml.safe_dump(raw))
    # Real files, because D-01 matches every declared `covers:` glob against the
    # TREE: a glob that can never fire exempts its step from every diff.
    for rel in ("app/x.py", "backend/svc.py", "frontend/x.js",
                "schemas/api.schema.json", "elsewhere/x", "nodeclare/y.py"):
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x\n")
    if rules:
        rules_dir = root / ".warden" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "a-rule.md").write_text(
            "---\nid: a-rule\nseverity: MEDIUM\nengine: claude\n"
            'applies_to: ["**"]\n---\nbody\n')
    return root


def _base_yaml(**over) -> dict:
    raw = {
        "version": 1, "repo": "probe",
        "components": {"app": {"path": "app/", "lang": "python",
                               "description": "app"}},
        "risk_tiers": [{"glob": "app/**", "tier": "MEDIUM"}],
        "verify": {"tests": [{"run": "true", "covers": ["**"]}]},
        "review": {"rules_dir": ".warden/rules", "blocking_severities": ["HIGH"]},
    }
    raw.update(over)
    return raw


# --- D-01 ---------------------------------------------------------------------

def test_d01_catches_a_scope_that_narrows_by_inference(tmp_path):
    """A step whose `cwd` sits in a declared component
    area is exempted from every diff outside it, on an inference no declaration
    in the file states — the case where a command that reaches outside its cwd
    is silently not required."""
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "pytest", "cwd": "app"}]}))
    result = declared_mod.check_scope_reach(config_mod.load(root), root)
    assert result.status == declared_mod.DRIFT
    assert "by INFERENCE" in result.findings[0].detail
    assert "covers:" in result.findings[0].detail


def test_d01_a_root_cwd_claims_nothing_and_is_not_drift(tmp_path):
    """The check is precise about what it reports. A step at the repo root
    infers "the whole tree", which exempts no diff from anything, so requiring a
    declaration there would be noise rather than a finding."""
    root = _repo(tmp_path, _base_yaml(verify={"tests": [{"run": "pytest"}]}))
    assert declared_mod.check_scope_reach(
        config_mod.load(root), root).status == declared_mod.CLEAN


def test_d01_a_declared_reach_is_clean_even_when_it_narrows(tmp_path):
    """What the check wants is the DECLARATION, not a wide reach: a step that
    says it covers one subtree has said so, and that is the whole point."""
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "pytest", "cwd": "app", "covers": ["app/**"]}]}))
    assert declared_mod.check_scope_reach(
        config_mod.load(root), root).status == declared_mod.CLEAN


def test_d01_reads_deploy_scopes_too(tmp_path):
    """`deploy:` scopes are the same shape and the same reach question; leaving
    them out would let the inference live on in the half nobody looked at."""
    root = _repo(tmp_path, _base_yaml(deploy={
        "staging": [{"run": "ship", "cwd": "app"}]}))
    result = declared_mod.check_scope_reach(config_mod.load(root), root)
    assert result.status == declared_mod.DRIFT
    assert result.findings[0].check == "deploy:staging"


# --- D-02 ---------------------------------------------------------------------

def test_d02_proves_the_hash_claims_by_perturbation(tmp_path):
    """The claim that matters is about the code that RUNS, so the check changes
    the input and looks. `review:` is hashed and `components:` is not, and
    both halves are proved here rather than only stated in prose."""
    root = _repo(tmp_path, _base_yaml())
    scratch = tmp_path / "s"
    scratch.mkdir()
    assert declared_mod._version_moves_when(root, scratch, "review") is True
    assert declared_mod._version_moves_when(root, scratch, "components") is False
    assert declared_mod._version_moves_when(root, scratch, "verify") is False


def test_d02_catches_a_hash_claim_that_is_wrong_in_either_direction(tmp_path):
    """Both directions are drift: an input hashed while the inventory says it is
    not (a widening nobody recorded — every consumer's version moved) and one
    declared hashed that silently stopped being (the gate quietly comparing
    verdicts across rulesets)."""
    root = _repo(tmp_path, _base_yaml())
    scratch = tmp_path / "s"
    scratch.mkdir()
    lying = tuple(
        declared_mod.GateInput(g.where, not g.hashed, g.reason)
        if g.where in ("repo.yaml:components", "repo.yaml:review") else g
        for g in declared_mod.GATE_INPUTS)
    original = declared_mod.GATE_INPUTS
    try:
        declared_mod.GATE_INPUTS = lying            # type: ignore[misc]
        result = declared_mod.check_gate_inputs(root, scratch)
    finally:
        declared_mod.GATE_INPUTS = original          # type: ignore[misc]
    assert result.status == declared_mod.DRIFT
    where = {f.check for f in result.findings}
    assert where == {"repo.yaml:components", "repo.yaml:review"}, result.findings


def test_d02_refuses_a_schema_key_the_inventory_never_heard_of(tmp_path,
                                                                monkeypatch):
    """The open end, and where it actually is. `repo.schema.json` sets
    `additionalProperties: false`, so a key no consumer may write cannot appear
    in a valid repo.yaml — a NEW gate input arrives by someone adding a PROPERTY
    to the schema. So the comparison is inventory-versus-schema, and the check
    is shown catching a property with no hash claim."""
    root = _repo(tmp_path, _base_yaml())
    scratch = tmp_path / "s"
    scratch.mkdir()
    real = declared_mod._schema_keys
    monkeypatch.setattr(declared_mod, "_schema_keys",
                        lambda: real() | {"brand_new_gate_input"})
    result = declared_mod.check_gate_inputs(root, scratch)
    assert result.status == declared_mod.DRIFT
    assert any("brand_new_gate_input" in f.detail for f in result.findings)


def test_d02_the_inventory_covers_every_key_the_shipped_schema_allows():
    """And the live version of the same assertion: nothing in the schema today
    is missing a claim. This is the test that goes red the next time a property
    is added to repo.schema.json without one."""
    claimed = {g.where.split(":", 1)[1] for g in declared_mod.GATE_INPUTS
               if g.where.startswith("repo.yaml:")}
    assert declared_mod._schema_keys() - claimed - {"version", "repo"} == set()


def test_d02_a_declared_optional_key_this_repo_omits_is_not_drift(tmp_path):
    """`deploy:` and `design:` are optional. An absent key has no bytes to
    perturb and makes no claim — silence, not drift."""
    root = _repo(tmp_path, _base_yaml())
    scratch = tmp_path / "s"
    scratch.mkdir()
    assert declared_mod.check_gate_inputs(
        root, scratch).status == declared_mod.CLEAN


def test_d02_the_package_surfaces_are_compared_to_what_rules_py_hashes(tmp_path):
    """The half that cannot be perturbed — those files live at an absolute path
    inside the installed package — is a set comparison against the tuple
    `rules.py` itself reads, so a surface added or dropped there is caught even
    though the bytes are not re-hashed. Stated as the limit it is."""
    from warden import rules as rules_mod
    declared = {g.where.split(":", 1)[1] for g in declared_mod.GATE_INPUTS
                if g.where.startswith("package:")}
    assert declared == {s.name for s in rules_mod._EXTRA_SURFACES}, (
        "the inventory and rules.py's own hashed surfaces have parted company")


def test_d02_the_rules_dir_and_checkers_dir_really_move_the_version(tmp_path):
    """The two root-relative hashed surfaces ARE perturbable, so they are
    perturbed rather than asserted."""
    root = _repo(tmp_path, _base_yaml())
    scratch = tmp_path / "s"
    scratch.mkdir()
    assert declared_mod._tree_surface_moves(root, scratch, "rules:*.md") is True
    assert declared_mod._tree_surface_moves(
        root, scratch, "checkers:*.py") is True


# --- D-03 ---------------------------------------------------------------------

def _workflow(root: Path, runs: list[str], name: str = "ci.yml") -> None:
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / name).write_text(yaml.safe_dump({
        "name": "ci", "on": {"pull_request": {}},
        "jobs": {"gate": {"runs-on": "ubuntu-latest",
                          "steps": [{"run": r} for r in runs]}}}))


def test_d03_catches_a_gate_job_that_skips_a_declared_scope(tmp_path):
    """The job runs `warden review` and only
    one of two declared scopes, so a diff requiring the other renders a true and
    unclearable NO VERIFY RESULT line."""
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "true", "covers": ["**"]}],
        "example": [{"run": "true", "covers": ["examples/**"]}]}))
    _workflow(root, ["warden verify --scope tests", "warden review"])
    result = declared_mod.check_scope_runners(root, config_mod.load(root))
    assert result.status == declared_mod.DRIFT
    assert "--scope example" in result.findings[0].detail
    assert "NO VERIFY RESULT" in result.findings[0].detail


def test_d03_is_clean_when_the_gate_job_runs_every_scope(tmp_path):
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "true", "covers": ["**"]}],
        "example": [{"run": "true", "covers": ["examples/**"]}]}))
    _workflow(root, ["warden verify --scope tests",
                     "warden verify --scope example", "warden review"])
    assert declared_mod.check_scope_runners(
        root, config_mod.load(root)).status == declared_mod.CLEAN


def test_d03_requires_the_scope_in_the_SAME_job_as_the_review(tmp_path):
    """Why the check is per-JOB and not per-workflow: `warden audit` pairs a
    verify result to the reviewed commit out of the runner's own filesystem
    so a scope verified in a different job is invisible to the sticky comment —
    the gate would render NO VERIFY RESULT forever even though the commands had
    run."""
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "true", "covers": ["**"]}]}))
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(yaml.safe_dump({
        "name": "ci", "on": {"pull_request": {}},
        "jobs": {
            "tests": {"steps": [{"run": "warden verify --scope tests"}]},
            "gate": {"steps": [{"run": "warden review"}]}}}))
    result = declared_mod.check_scope_runners(root, config_mod.load(root))
    assert result.status == declared_mod.DRIFT
    assert result.findings[0].check == "ci.yml:gate"


def test_d03_skips_rather_than_passes_when_no_job_runs_the_review(tmp_path):
    """A repo whose gate runs nowhere in CI owes no paired evidence, and saying
    "clean" would be claiming a pairing nobody made. Reported as
    not-applicable — visible, not silent."""
    root = _repo(tmp_path, _base_yaml())
    _workflow(root, ["pytest -q"])
    result = declared_mod.check_scope_runners(root, config_mod.load(root))
    assert result.status == declared_mod.SKIPPED
    assert "warden review" in result.detail


def test_d03_refuses_a_workflow_it_cannot_parse(tmp_path):
    """Fail-closed: a gate job this check cannot read is not a gate job it may
    assume is complete. DeclareError is exit 2 at the CLI, never a verdict."""
    root = _repo(tmp_path, _base_yaml())
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("jobs: [this: is: not: valid\n")
    with pytest.raises(declared_mod.DeclareError, match="could not be parsed"):
        declared_mod.check_scope_runners(root, config_mod.load(root))


# --- D-04 ---------------------------------------------------------------------

def _policy(root: Path, paragraph: str) -> None:
    path = root / ".warden" / "skills-policy.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# policy\n\n## Forbidden paths\n\n{paragraph}\n\n"
                    "## Verify\n\n- something\n")


def test_d04_catches_a_gate_surface_path_with_no_tier(tmp_path):
    """`.cage/` declares the fences and the prompt of the platform's own
    unattended runner; matching no `risk_tiers` glob, `warden explain` would
    report the unmatched default — the tier a docs page gets."""
    root = _repo(tmp_path, _base_yaml())
    _policy(root, "Never edit: `.cage/` · `app/core/`")
    result = declared_mod.check_gate_surface_tiers(root, config_mod.load(root))
    assert result.status == declared_mod.DRIFT
    assert [f.detail.split("`")[1] for f in result.findings] == [".cage/"]
    assert "UNMATCHED" in result.findings[0].detail


def test_d04_an_explicit_tier_is_the_exemption_even_at_LOW(tmp_path):
    """The line is declared-versus-default, not high-versus-low. A glob that
    says `tier: LOW` is a statement someone made; the default is the absence of
    one."""
    root = _repo(tmp_path, _base_yaml(risk_tiers=[
        {"glob": ".cage/**", "tier": "LOW", "reason": "deliberately low"},
        {"glob": "app/**", "tier": "MEDIUM"}]))
    _policy(root, "Never edit: `.cage/`")
    assert declared_mod.check_gate_surface_tiers(
        root, config_mod.load(root)).status == declared_mod.CLEAN


def test_d04_reads_protected_paths_as_gate_surface_too(tmp_path):
    """Two declarations name gate surface and both are read: a protected path is
    a statement that consumers depend on those bytes."""
    root = _repo(tmp_path, _base_yaml(protected_paths=[
        {"path": "contracts/", "reason": "consumers parse these"}]))
    result = declared_mod.check_gate_surface_tiers(root, config_mod.load(root))
    assert result.status == declared_mod.DRIFT
    assert result.findings[0].check == "repo.yaml protected_paths"


def test_d04_skips_when_nothing_declares_a_gate_surface(tmp_path):
    """No protected paths and no policy paragraph is not a clean gate surface —
    it is no declared gate surface, and the check says which."""
    root = _repo(tmp_path, _base_yaml())
    result = declared_mod.check_gate_surface_tiers(root, config_mod.load(root))
    assert result.status == declared_mod.SKIPPED


def test_d04_reads_the_first_paragraph_not_the_carve_out_prose(tmp_path):
    """The policy's Never-edit list is the fence; the carve-out prose below it
    names paths that are ILLUSTRATIONS (`.warden/rules/*.md`, `mechanical.py`).
    Reading the whole section would report drift against examples."""
    root = _repo(tmp_path, _base_yaml(risk_tiers=[
        {"glob": ".cage/**", "tier": "HIGH", "reason": "the cage source"},
        {"glob": "app/**", "tier": "MEDIUM"}]))
    path = root / ".warden" / "skills-policy.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# policy\n\n## Forbidden paths\n\nNever edit: `.cage/`\n\n"
        "A carve-out mentions `totally/untiered/illustration.py` in prose.\n\n")
    assert declared_mod.policy_gate_paths(root) == (".cage/",)
    assert declared_mod.check_gate_surface_tiers(
        root, config_mod.load(root)).status == declared_mod.CLEAN


# --- the command and the exit contract ---------------------------------------

def test_declare_check_exit_codes_are_the_contract(tmp_path, monkeypatch, capsys):
    """0 clean, 1 drift. A drift report must not be silent and must not be a 2:
    exit 2 is reserved for a check that could not evaluate."""
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "pytest", "cwd": "app"}]}))
    monkeypatch.chdir(root)
    assert cli.main(["declare", "check"]) == 1
    out = capsys.readouterr().out
    assert "DRIFT: D-01" in out

    (root / "repo.yaml").write_text(yaml.safe_dump(_base_yaml()))
    assert cli.main(["declare", "check"]) == 0
    assert "clean:" in capsys.readouterr().out


def test_declare_check_reports_as_json_for_a_machine(tmp_path, monkeypatch,
                                                     capsys):
    root = _repo(tmp_path, _base_yaml())
    monkeypatch.chdir(root)
    assert cli.main(["declare", "check", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["verdict"] == "clean"
    assert [c["id"] for c in doc["checks"]] == [
        "D-01", "D-02", "D-03", "D-04", "D-05"]


def test_declare_check_exits_2_when_a_check_cannot_evaluate(tmp_path,
                                                            monkeypatch, capsys):
    """Fail-closed at the command boundary: a check that could not run is not a
    clean one, and the exit code says so distinctly from drift."""
    root = _repo(tmp_path, _base_yaml())
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("jobs: [this: is: not: valid\n")
    monkeypatch.chdir(root)
    assert cli.main(["declare", "check"]) == 2
    assert "declare check" in capsys.readouterr().err


def test_declare_check_perturbs_a_copy_and_never_the_real_repo_yaml(tmp_path,
                                                                    monkeypatch):
    """D-02 mutates bytes to answer its question, so the one thing it must never
    mutate is the repo it is checking."""
    root = _repo(tmp_path, _base_yaml())
    before = (root / "repo.yaml").read_bytes()
    rules = sorted(p.name for p in (root / ".warden" / "rules").iterdir())
    monkeypatch.chdir(root)
    assert cli.main(["declare", "check"]) == 0
    assert (root / "repo.yaml").read_bytes() == before
    assert sorted(p.name for p in (root / ".warden" / "rules").iterdir()) == rules


# --- this repo ----------------------------------------------------------------

def test_this_repo_has_no_declaration_drift(tmp_path):
    """The integration test, and the reason the four checks exist. Run against
    the platform's own tree: its verify scopes declare their reach, every gate
    input's hash claim holds, CI's gate job runs every scope a diff here can
    require, and every path the policy forbids carries a tier (`.cage/`,
    `.warden/`, `repo.yaml`, `.githooks/` and `.claude/` among them).

    Driven through the CLI in a subprocess so the assertion is about the command
    a human or CI runs, not about a library call. D-05's `gh` is a fake on
    PATH: the suite reads no network, and the real read is in the PR body."""
    bin_dir = _fake_gh(tmp_path, FORGE_DISALLOWS)
    proc = subprocess.run(
        [sys.executable, "-m", "warden.cli", "declare", "check"],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for check in ("D-01", "D-02", "D-03", "D-04", "D-05"):
        assert f"✓ {check}" in proc.stdout, proc.stdout


# --- inputs a check must refuse, and spellings it must read ------------------

def test_d01_refuses_a_covers_glob_that_matches_no_path_in_the_tree(tmp_path):
    """The declaration meant to close a silent exemption must not open a worse
    one: `covers: ["backend"]` — a bare directory name, which is a legal glob
    matching one FILE — matches nothing, so the step would be exempt from
    EVERY diff while `reach_of` reports `declared`, and the check that exists
    to make reach visible would call it settled.

    A glob that can never fire is drift, and D-01 names the fix."""
    root = _repo(tmp_path, _base_yaml(verify={
        "lint": [{"run": "ruff check .", "covers": ["**"]}],
        "tests": [{"run": "pytest", "covers": ["backend"]}]}))
    result = declared_mod.check_scope_reach(config_mod.load(root), root)
    assert result.status == declared_mod.DRIFT
    assert any("matches no path in the tree" in f.detail
               for f in result.findings), result.findings
    # the trailing-slash spelling is repo.yaml's own convention for an area and
    # is normalized rather than reported
    two = tmp_path / "two"
    two.mkdir()
    ok = _repo(two, _base_yaml(verify={
        "lint": [{"run": "ruff check .", "covers": ["**"]}],
        "tests": [{"run": "pytest", "covers": ["backend/"]}]}))
    assert declared_mod.check_scope_reach(
        config_mod.load(ok), ok).status == declared_mod.CLEAN


def test_d01_refuses_a_tree_it_cannot_read_rather_than_blessing_every_glob(
        tmp_path):
    """Fail-closed on the input, not just the output: with no readable files the
    glob check would pass every declaration vacuously, which is the shape this
    whole module refuses."""
    root = tmp_path / "empty"
    root.mkdir()
    (root / "repo.yaml").write_text(yaml.safe_dump(_base_yaml()))
    rules = root / ".warden" / "rules"
    rules.mkdir(parents=True)
    cfg = config_mod.load(root)
    import shutil
    shutil.rmtree(rules)
    (root / "repo.yaml").unlink()
    with pytest.raises(declared_mod.DeclareError, match="no readable files"):
        declared_mod.check_scope_reach(cfg, root)


def test_d04_refuses_a_policy_paragraph_it_cannot_read(tmp_path):
    """`policy_gate_paths` must not return `()` both when the policy is ABSENT
    and when it is PRESENT but its Never-edit paragraph does not match the
    regex — rewording the heading to `## Forbidden paths (the fence)` would
    silently narrow D-04's input set and report `✓ clean` at exit 0. The check
    losing its inputs and saying nothing is the class it exists to refuse.

    Absent returns `()`; present-and-unreadable raises."""
    root = _repo(tmp_path, _base_yaml())
    # absent: nothing declared, no refusal
    assert declared_mod.policy_gate_paths(root) == ()
    policy = root / ".warden" / "skills-policy.md"
    policy.parent.mkdir(parents=True, exist_ok=True)
    # The heading reworded: answering `()` here would let D-04 print
    # `✓ clean: 2 declared gate-surface path(s)`.
    policy.write_text("# policy\n\n## Forbidden paths (the fence)\n\n"
                      "Never edit: `.cage/` · `repo.yaml`\n\n")
    with pytest.raises(declared_mod.DeclareError, match="no `## Forbidden"):
        declared_mod.policy_gate_paths(root)
    with pytest.raises(declared_mod.DeclareError):
        declared_mod.check_gate_surface_tiers(root, config_mod.load(root))
    # ...but a section that IS read and names no backticked path declares no
    # fenced path, which is a real and documented state — Skills-Policy.md shows
    # `none` and examples/hello-svc writes exactly that. Folding it into the
    # refusal would make `warden declare check` exit 2 on the shipped example
    # consumer with nothing they could do about it. Read-and-empty and
    # cannot-read are different states.
    policy.write_text("# policy\n\n## Forbidden paths\n\nnone\n\n"
                      "## Verify\n\n- x\n")
    assert declared_mod.policy_gate_paths(root) == ()
    # ...and the honest heading still reads, so the refusal is not vacuous.
    policy.write_text("# policy\n\n## Forbidden paths\n\nNever edit: "
                      "`.cage/` · `repo.yaml`\n\n## Verify\n\n- x\n")
    assert declared_mod.policy_gate_paths(root) == (".cage/", "repo.yaml")


def test_d03_does_not_read_a_commented_out_or_echoed_invocation_as_a_run(
        tmp_path):
    """A regex unanchored within the job's concatenated `run:` text would let
    a COMMENTED-OUT step satisfy it — and commenting the step out is precisely
    how this drift returns."""
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "true", "covers": ["**"]}],
        "example": [{"run": "true", "covers": ["app/**"]}]}))
    _workflow(root, ["warden verify --scope tests",
                     "# warden verify --scope example\necho skipping",
                     'echo "run warden verify --scope example some day"',
                     "warden review"])
    result = declared_mod.check_scope_runners(root, config_mod.load(root))
    assert result.status == declared_mod.DRIFT, (
        "a commented-out or echoed invocation was read as a run")
    assert "--scope example" in result.findings[0].detail


def test_d03_reads_the_spellings_a_shell_actually_runs(tmp_path):
    """The OTHER direction. `--scope=NAME` (argparse accepts it) and a
    backslash-continued invocation are both runs; missing either would make
    D-03 report a FALSE drift about a workflow that does run the scope. A
    false drift is loud rather than silent, and it is still wrong."""
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "true", "covers": ["**"]}],
        "example": [{"run": "true", "covers": ["app/**"]}]}))
    _workflow(root, ["./bin/warden verify --scope=tests",
                     "warden verify \\\n  --scope example",
                     "warden review"])
    assert declared_mod.check_scope_runners(
        root, config_mod.load(root)).status == declared_mod.CLEAN


def test_d03_a_quoted_hash_is_not_a_comment(tmp_path):
    """The comment stripper respects quotes, so a `#` inside a quoted argument
    does not truncate the command that carries the invocation."""
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "true", "covers": ["**"]}]}))
    _workflow(root, ['echo "issue #42" && warden verify --scope tests',
                     "warden review"])
    assert declared_mod.check_scope_runners(
        root, config_mod.load(root)).status == declared_mod.CLEAN


# --- wrapped invocations, and mentions that are not runs ---------------------

def test_d03_reads_a_wrapper_prefixed_invocation_the_docs_prescribe():
    """Anchoring `warden` to the START of a command to keep an echoed mention
    out would make every WRAPPED invocation invisible. `uv run --locked
    --project ../.. warden verify --scope app` is the spelling
    docs/wiki/Quickstart.md prescribes and examples/hello-svc's own workflow
    uses, so D-03 would report drift about a workflow that runs the scope, on
    the shipped example consumer.

    Driven against the REAL example tree, not a fixture, because a fixture
    covers only the spellings someone thought to write into it."""
    example = ROOT / "examples" / "hello-svc"
    result = declared_mod.check_scope_runners(example, config_mod.load(example))
    assert result.status != declared_mod.DRIFT, (
        "D-03 reports drift on the shipped example consumer's own workflow: "
        f"{[f.detail for f in result.findings]}")


def test_d03_still_refuses_a_mention_now_that_quotes_are_stripped(tmp_path):
    """The other half: without that anchor a mention must still not read as a
    run. Discrimination lives where it belongs — the quoted span is removed,
    so a mention inside quotes is not a run however it is punctuated,
    including shapes with command separators inside the quotes."""
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "true", "covers": ["**"]}],
        "example": [{"run": "true", "covers": ["app/**"]}]}))
    _workflow(root, ["warden verify --scope tests",
                     "echo 'do not run; warden verify --scope example'",
                     'echo "TODO: re-enable && warden verify --scope example"',
                     "# warden verify --scope example",
                     "warden review"])
    result = declared_mod.check_scope_runners(root, config_mod.load(root))
    assert result.status == declared_mod.DRIFT, (
        "a quoted or commented mention was read as a run")
    assert "--scope example" in result.findings[0].detail


# --- command position, not quoting alone, tells a mention from a run ---------

@pytest.mark.parametrize("shape,step", [
    ("echoed", "echo warden verify --scope example"),
    ("conditional-body", "if false; then warden verify --scope example; fi"),
    ("function-body", "gate() { warden verify --scope example; }"),
    # The shapes a `run: |` block is ACTUALLY written in — body on its own
    # line. A discriminator that recognises only the `;`-separated one-liner
    # above renders the MULTI-LINE shapes below CLEAN over a scope the job
    # never runs. The multi-line `if` is the shape this repo's own gate job
    # uses. `multiline-function` is the CONTROL of the group: it is here so
    # that removing the brace half of `_commands` is caught by this
    # parametrization rather than nowhere.
    ("multiline-conditional",
     "if false; then\n  warden verify --scope example\nfi"),
    ("multiline-loop",
     "for s in example; do\n  warden verify --scope $s\n  warden verify "
     "--scope example\ndone"),
    ("multiline-case",
     "case $x in\n  a)\n    warden verify --scope example\n    ;;\nesac"),
    ("multiline-function",
     "gate() {\n  warden verify --scope example\n}"),
    # A bare depth COUNTER lets one unbalanced closer zero it, so an
    # invocation genuinely inside a block would read as an unconditional run.
    # A closer STACK prevents that, and this is the shape that proves it — a
    # closer of the wrong type closes nothing.
    #
    # HEREDOC shapes are NOT here: they live in
    # test_a_heredoc_body_is_not_commands.
    ("cross-type-closer",
     "for s in a; do\n  echo $s\nesac\n  warden verify --scope example\ndone"),
])
def test_an_unquoted_mention_is_not_a_run(tmp_path, shape, step):
    """Named regression.

    With comments and quoted spans removed, quoting alone cannot tell a
    mention of the invocation from a run of it — each of these unquoted shapes
    would capture `example` and D-03 would render CLEAN over a scope the job
    never executes. A silent pass, in the check whose whole subject is a scope
    nobody ran.

    The discriminator is COMMAND POSITION: a command whose
    first word makes a mention (`echo`, `printf`, `:`), one that is a shell
    keyword (so a conditional or loop BODY, which is not an unconditional
    run), one inside a `{ ... }` block, and one that is an assignment are
    none of them a run.
    """
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "true", "covers": ["**"]}],
        "example": [{"run": "true", "covers": ["app/**"]}]}))
    _workflow(root, ["warden verify --scope tests", step, "warden review"])
    result = declared_mod.check_scope_runners(root, config_mod.load(root))
    assert result.status == declared_mod.DRIFT, (
        f"an unquoted {shape} mention was read as a run")
    assert "--scope example" in result.findings[0].detail


@pytest.mark.parametrize("shape,step", [
    ("here-string", 'grep x <<< "$V"'),
    ("commented", "# see <<EOF for the old shape"),
    ("quoted", 'echo "use <<EOF here"'),
    # The shapes the anchored heredoc detector must ALSO leave alone:
    # `<<` is the shift operator inside arithmetic, and `<<=` is its
    # assignment form. Neither opens a heredoc in bash.
    ("arithmetic-shift", "x=$((1<<2))"),
    ("arithmetic-command", "(( x <<= 1 ))"),
    ("arithmetic-command-shift", "(( x << 1 ))"),
    ("single-quoted-here-string", "grep x <<<'v'"),
    # Shapes where a per-line scan would reset bash's lexical state at every
    # newline, so a `<<` bash reads as quoted text or as a shift would open a
    # phantom heredoc — each blanking the step's `warden review` line in a
    # real consumer workflow shape.
    ("escaped-quote-before-shift",
     'echo "usage: wrap it \\"<<name>>\\""'),
    ("ansi-c-escaped-quote", "echo $'it\\'s <<EOF'"),
    ("multi-line-quoted-string", 'python3 -c "\nx = 1 << 2\n"'),
    ("multi-line-arithmetic", "x=$((\n1<<2\n))"),
])
def test_an_ordinary_shift_left_does_not_blank_the_rest_of_the_step(
        tmp_path, shape, step):
    """Named regression: an ordinary `<<` does not open a heredoc.

    A heredoc opener search that is an unanchored `<<` over the raw line makes
    each of these ordinary lines — a here-string, a `<<` in a comment, a `<<`
    inside a quoted span, an arithmetic shift — start a PHANTOM heredoc that
    blanks every remaining line of the step. That takes the `warden review`
    line with it, so the job stops being a gate job and D-03 returns `clean`
    at exit 0 over a scope the job never runs: a silent pass across every
    scope. This fails if that mechanism returns.

    The `<<` and the `warden review` line share ONE step on purpose: the
    per-step split (`_job_runs`) bounds a phantom heredoc to the step it starts
    in, so a fixture that put them in different steps would pass against an
    unanchored detector too and pin nothing."""
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "true", "covers": ["**"]}],
        "example": [{"run": "true", "covers": ["app/**"]}]}))
    _workflow(root, ["warden verify --scope tests",
                     f"{step}\nwarden review"])
    result = declared_mod.check_scope_runners(root, config_mod.load(root))
    assert result.status == declared_mod.DRIFT, (
        f"an ordinary `<<` ({shape}) blanked the rest of the step and took "
        f"the gate-job mention with it: {result.status} — {result.detail}")
    assert "--scope example" in result.findings[0].detail


def test_a_run_after_a_closed_block_is_still_a_run(tmp_path):
    """The bound on the block counting, and what keeps it from being a blanket
    "anything after an `if` is invisible": a block that CLOSES stops covering
    what follows it. Without this, the fail-closed direction would swallow the
    ordinary `if ...; fi` preamble every real workflow writes before its gate
    commands."""
    root = _repo(tmp_path, _base_yaml(verify={
        "app": [{"run": "true", "covers": ["**"]}]}))
    _workflow(root, ["if [ -z \"$SKIP\" ]; then\n  echo running\nfi\n"
                     "warden verify --scope app",
                     "warden review"])
    assert declared_mod.check_scope_runners(
        root, config_mod.load(root)).status == declared_mod.CLEAN


def test_one_steps_unbalanced_block_does_not_hide_the_next_steps_run(
        tmp_path):
    """The third mechanism of command-position reading: `_job_runs` keeps
    STEPS apart, so a block this reader cannot balance inside one step cannot
    swallow a real invocation in the next one. Collapsing the steps back into
    one shell text turns this test red."""
    root = _repo(tmp_path, _base_yaml(verify={
        "app": [{"run": "true", "covers": ["**"]}]}))
    _workflow(root, ["if [ -n \"$CI\" ]; then\n  echo unbalanced",
                     "warden verify --scope app",
                     "warden review"])
    assert declared_mod.check_scope_runners(
        root, config_mod.load(root)).status == declared_mod.CLEAN, (
        "an unbalanced block in one step hid the next step's invocation")


def test_the_wrapper_spelling_the_docs_prescribe_is_still_a_run(tmp_path):
    """The bound, and the wrapper-spelling drift this must not reintroduce.
    Command position is not the same anchor: `uv run --locked --project ../..
    warden verify --scope app` is a run whose first word is `uv`, and so is
    `FOO=1 warden verify --scope app`, whose first word is an assignment the
    shell strips before the command."""
    root = _repo(tmp_path, _base_yaml(verify={
        "app": [{"run": "true", "covers": ["**"]}],
        "tests": [{"run": "true", "covers": ["app/**"]}]}))
    _workflow(root, ["uv run --locked --project ../.. warden verify --scope app",
                     "FOO=1 warden verify --scope tests",
                     "warden review"])
    assert declared_mod.check_scope_runners(
        root, config_mod.load(root)).status == declared_mod.CLEAN


def test_a_job_whose_review_is_conditional_is_still_a_gate_job(tmp_path):
    """The asymmetry, stated and pinned: the two questions D-03 asks fail
    closed in OPPOSITE directions, so they read the text differently on
    purpose. "Is this a gate job?" stays a plain mention test — this repo's own
    CI runs `warden review` inside an `if` (fork PRs take the `--no-comment`
    arm), and treating that job as no gate job at all would drop every scope
    obligation silently. "Did this scope RUN?" is the one that must not be
    satisfied by a mention."""
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "true", "covers": ["**"]}]}))
    _workflow(root, ['if [ -n "$CI" ]; then warden review --no-comment; '
                     "else warden review; fi"])
    result = declared_mod.check_scope_runners(root, config_mod.load(root))
    assert result.status == declared_mod.DRIFT, (
        "a job whose review is conditional owes its verify evidence like any "
        "other gate job")
    assert "--scope tests" in result.findings[0].detail


def test_the_docstring_names_the_limits_that_remain(tmp_path):
    """`_shell_text`'s docstring carries the list of what this reader does
    NOT model: a gap left unrecorded is as much a defect as the gap itself."""
    doc = declared_mod._shell_text.__doc__
    assert "command position" in doc.lower(), doc
    # The list moves when the reach moves — that is the point of pinning it.
    # The scan reads `$'...'` escapes, so that is not a limit; `$[ ]` and the
    # unterminated quote's step-wide reach are.
    for limit in ("heredoc", "backtick", "eval", "$[ ]", "unterminated quote"):
        assert limit in doc, f"{limit} dropped from the stated limits"
    # Heredoc bodies ARE modelled, by an opener detected inside the scan —
    # the docstring says so, names the residual an unterminated one leaves,
    # and does not describe a reader that ignores heredocs.
    assert "anchored" in doc, "the heredoc detection's anchor is not stated"
    assert "unterminated heredoc" in doc, (
        "the residual the heredoc drop leaves is not named")
    assert "not distinguished from commands" not in doc, (
        "the docstring still describes the reader that did not model heredocs")


def test_d04_reads_a_policy_that_declares_no_fenced_path_on_the_real_example():
    """READ-AND-EMPTY is not CANNOT-READ: a policy whose Never-edit paragraph
    says `none` — the spelling docs/wiki/Skills-Policy.md documents, and what
    examples/hello-svc writes — must not make `warden declare check` exit 2 on
    the shipped example consumer with nothing they could do about it. Aborting
    the report there would also mask any D-03 drift by ending it first.

    Against the real example tree, for the reason the test above gives."""
    example = ROOT / "examples" / "hello-svc"
    assert (example / ".warden" / "skills-policy.md").is_file(), (
        "the example consumer no longer ships a policy — this test is about "
        "how declare check reads one")
    assert declared_mod.policy_gate_paths(example) == ()
    result = declared_mod.check_gate_surface_tiers(
        example, config_mod.load(example))
    assert result.status != declared_mod.DRIFT
    proc = subprocess.run(
        [sys.executable, "-m", "warden.cli", "declare", "check"],
        cwd=example, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert proc.returncode == 0, (
        "`warden declare check` does not pass on the shipped example consumer: "
        + proc.stdout + proc.stderr)


def test_d01_matches_globs_against_the_warden_tree_files_too(tmp_path):
    """`tree_paths`' `.warden` special case lists that directory's FILES
    rather than pushing them onto the directory stack, where `iterdir()` would
    raise and be swallowed — leaving `.warden/skills-policy.md` and its
    siblings absent from the set every `covers:` glob is matched against, so a
    glob naming one of them would read as unmatched."""
    paths = declared_mod.tree_paths(ROOT)
    assert ".warden/skills-policy.md" in paths, (
        "a file directly under .warden/ is missing from the tree set")
    assert any(p.startswith(".warden/rules/") for p in paths)
    assert not any(p.startswith(".warden/out/") for p in paths), (
        "evidence run dirs are not tree")


# --- a heredoc body is not commands ------------------------------------------

@pytest.mark.parametrize("shape,step", [
    # A body line reading `fi` must not close the REAL enclosing block, or
    # the invocation genuinely inside the conditional reads as an
    # unconditional run.
    ("body-fi-closes-a-real-block",
     "if [ -n \"$X\" ]; then\n  cat > f <<'EOF'\nfi\nEOF\n"
     "  warden verify --scope example\nfi"),
    ("invocation-in-body", "cat <<EOF\nwarden verify --scope example\nEOF"),
    ("quoted-delimiter", "cat <<'EOF'\nwarden verify --scope example\nEOF"),
    ("double-quoted-delimiter",
     'cat <<"EOF"\nwarden verify --scope example\nEOF'),
    ("backslashed-delimiter",
     "cat <<\\EOF\nwarden verify --scope example\nEOF"),
    ("dash-strips-leading-tabs",
     "cat <<-EOF\n\twarden verify --scope example\n\tEOF"),
    ("redirect-after-the-opener",
     "cat <<EOF > out.txt\nwarden verify --scope example\nEOF"),
    ("two-heredocs-on-one-line",
     "diff <(cat <<A) <(cat <<B)\nwarden verify --scope example\nA\n"
     "warden verify --scope example\nB"),
    # A `#` or a quote in the body is body, not a comment or a quote: the
    # opener's own delimiter still terminates it and the body still hides.
    ("hash-and-quote-in-body",
     "cat <<EOF\n# it's\nwarden verify --scope example\nEOF"),
    # The idiom a consumer is likeliest to write: the opener sits inside a
    # double-quoted `$( )`, which bash reads as a fresh unquoted context, so a
    # scope MENTION in the comment body must not count as a run.
    ("body-inside-quoted-substitution",
     'gh pr comment 1 --body "$(cat <<EOF\nrun warden verify --scope '
     'example locally\nEOF\n)"'),
    ("body-inside-substitution-terminator-closes-it",
     "BODY=$(cat <<EOF\nwarden verify --scope example\nEOF)"),
    # The negative control for the `EOF)` prefix rule: with
    # a space before the paren bash does NOT terminate, and neither may the
    # reader — the invocation stays inside the body.
    ("space-before-the-paren-is-not-a-terminator",
     "X=$(cat <<EOF\nwarden verify --scope example\nEOF )"),
])
def test_a_heredoc_body_is_not_commands(tmp_path, shape, step):
    """Named regression.

    A reader that does not model heredocs lets a body line reading `fi` close
    a real block and reads an invocation inside a body as a run — D-03 CLEAN
    over a scope the job never runs, a silent pass in the check whose subject
    is a scope nobody ran. Dropping bodies on an UNANCHORED `<<` search over
    the raw line is worse (a here-string blanks the step and takes the
    `warden review` line with it). The opener is detected INSIDE the
    character scan — unquoted, outside a comment, not `<<<`, not `<<=`, not
    inside `$(( ))` — and
    test_an_ordinary_shift_left_does_not_blank_the_rest_of_the_step keeps
    the ordinary-`<<` inputs reporting drift."""
    assert declared_mod._scope_runs(declared_mod._shell_text(step)) == set(), (
        f"{shape}: a heredoc body was read as commands")
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "true", "covers": ["**"]}],
        "example": [{"run": "true", "covers": ["app/**"]}]}))
    _workflow(root, ["warden verify --scope tests", step, "warden review"])
    result = declared_mod.check_scope_runners(root, config_mod.load(root))
    assert result.status == declared_mod.DRIFT, (
        f"{shape}: an invocation in a heredoc body was read as a run")
    assert "--scope example" in result.findings[0].detail


@pytest.mark.parametrize("shape,step", [
    ("rest-of-the-opener-line",
     "cat <<EOF; warden verify --scope example\nbody\nEOF"),
    ("after-the-terminator",
     "cat <<EOF\nbody\nEOF\nwarden verify --scope example"),
    ("after-a-dash-terminator-with-tabs",
     "cat <<-EOF\n\tbody\n\tEOF\nwarden verify --scope example"),
    ("terminator-then-review-mention",
     "cat <<EOF\nbody\nEOF\nwarden verify --scope example\nwarden review"),
    # Terminators a per-line scan cannot see: `EOF)` closing a `$( )` — bash
    # 3.2 and 5 terminate there; a quoted-delimiter body whose last line ends
    # in a backslash — bash keeps it literal and terminates on the next line;
    # and a delimiter spelled `$END`, which bash never expands, so a literal
    # `$END` line terminates.
    ("terminator-closes-the-substitution",
     "X=$(cat <<EOF\nbody\nEOF)\nwarden verify --scope example"),
    ("quoted-delimiter-body-ending-in-backslash",
     "cat <<'EOF'\nfoo \\\nEOF\nwarden verify --scope example"),
    ("expansion-spelled-delimiter",
     "cat <<$END\nbody\n$END\nwarden verify --scope example"),
    # Bash ends a heredoc inside `$( )` on a line that STARTS with `EOF)` and
    # runs the rest of that line — the `"` that closes the quote, a `;` and
    # the next command — which an exact whole-line match would swallow; a `)`
    # that closes a case arm inside `"$( )"` is not the substitution's
    # closer; a heredoc opened in a substitution that closes on the same line
    # is EMPTY, and the lines after it are commands; and a body line ending
    # in an ESCAPED backslash is not a continuation.
    ("terminator-closes-the-substitution-then-the-quote",
     'X="$(cat <<EOF\nbody\nEOF)"\nwarden verify --scope example'),
    ("terminator-then-more-of-the-line",
     "X=$(cat <<EOF\nbody\nEOF); warden verify --scope example"),
    # multi-line on purpose: the one-line spelling resyncs at the closing
    # quote and cannot show the defect
    ("case-arm-paren-inside-quoted-substitution",
     'MSG="$(\n  case $OS in\n    (linux) echo L;;\n  esac\n)"\n'
     "warden verify --scope example"),
    ("nested-subshell-inside-quoted-substitution",
     'MSG="$(\n  (echo L)\n)"\nwarden verify --scope example'),
    ("empty-heredoc-in-a-substitution-closed-on-its-line",
     'X="$(cat <<EOF)"\nwarden verify --scope example'),
    ("body-line-ending-in-an-escaped-backslash",
     "cat <<EOF\nfoo \\\\\nEOF\nwarden verify --scope example"),
])
def test_the_body_and_only_the_body_is_dropped(shape, step):
    """The bound on the drop, and what keeps it from being the reverted
    mechanism under a new name: the rest of the opener's own line is still
    commands (bash runs it), and the first line after the terminator is
    commands again. A drop that ate either would hide real invocations —
    and, for the `warden review` line, turn a gate job into no gate job."""
    assert declared_mod._scope_runs(declared_mod._shell_text(step)) == {
        "example"}, f"{shape}: the heredoc drop ate a real invocation"


_REST_OF_THE_STEP = "\nwarden verify --scope example\nwarden review"


def test_esac_after_a_subshell_still_ends_the_case_inside_a_substitution():
    """The bound on narrowing the `)` position to plain parens: after a
    subshell's `)` bash rejects a bare word but takes a terminator —
    `a) (echo L) esac` passes bash 5.2 -n — so that `esac` still ends the
    case. Were only a case pattern's `)` a command position, the `esac` would
    be missed, the substitution's own `)` read as a case arm's, and the `"`
    after it would open a quote over the review mention."""
    step = 'X="$(\n  case $OS in\n    a) (echo L) esac\n)"\nwarden review'
    assert "warden review" in declared_mod._shell_text(step), (
        "the esac after a subshell was missed and the step was swallowed")


@pytest.mark.parametrize("shape,arm", [
    # `bash 5.2 a.sh` on each, with `warden` a function that echoes. The
    # subject is `x` and every arm's pattern is `a`, so no arm BODY runs —
    # what each cell is about is where the case ENDS, and on all four the
    # whole output is `ran:verify --scope app`: the `esac` closed the case
    # and the verify ran at the top level after it. (Spelled `case a in`,
    # the subshell cell prints `L` first, which is how the body running
    # would look.)
    ("subshell", "a) (echo L) esac"),
    # extglob off, bash's default: `!(false)` is a NEGATED subshell, so its
    # `)` is a command position too and the `esac` still ends the case.
    ("negated-subshell", "a) !(false) esac"),
    ("brace-group", "a) { echo L; } esac"),
    # the control: the arm bash scripts are usually written with
    ("terminated-arm", "a) echo L ;;\nesac"),
])
def test_a_verify_after_an_esac_that_follows_a_subshell_reads_as_a_run(
        shape, arm):
    """Named regression. `_shell_text` ends the `case` correctly here, but
    `_scope_runs` is a SEPARATE reader over `_commands`, and `_commands` split
    only at `;`, `&&`, `||`, `|`, `&` and newline: `a) (echo L) esac` is one
    command whose head is `a)`, so the `esac` never popped the block and every
    line after it read as conditional. D-03 then reported drift over a scope
    the job does run — an unclearable NO VERIFY RESULT line the author cannot
    fix by doing anything right.

    Each shape was run on bash 5.2 (`docker run --rm bash:5.2`), not reasoned
    about: all four reach the verify at the top level."""
    step = f"case x in\n{arm}\nwarden verify --scope app\n"
    assert declared_mod._scope_runs(declared_mod._shell_text(step)) == {"app"}, (
        f"{shape}: the verify after the esac still reads as inside the case")


@pytest.mark.parametrize("shape,step,unknown", [
    # `shopt -s extglob` then `rm -rf !(dist)` is the pair bash documents, and
    # on bash 5.2 it runs: `dist` survives and the verify after it runs, exit
    # 0. The scan reads the `!(` only where the answer could change what it
    # reads — inside `$( )` or inside an open `case` — so none of these is
    # refused, however unreadable the extglob state is. Refusing one would
    # exit 2 over the WHOLE `declare check`, D-01..D-04, not just D-03.
    ("a comment naming extglob above the pair",
     "# extglob keeps this tidy\nshopt -s extglob\nrm -rf !(dist)\n"
     "warden verify --scope app", None),
    ("a shopt inside a closed if above the pair",
     "if true; then shopt -s extglob; fi\nrm -rf !(dist)\n"
     "warden verify --scope app", None),
    ("the canonical pair with nothing else",
     "shopt -s extglob\nrm -rf !(dist)\nwarden verify --scope app", None),
    ("the environment making extglob unreadable",
     "shopt -s extglob\nrm -rf !(dist)\nwarden verify --scope app",
     "the step `env` sets BASHOPTS"),
])
def test_a_top_level_negated_paren_outside_a_case_is_not_refused(
        shape, step, unknown):
    """Named regression. Extending the paren reading to the top level made
    every top-level `!(` ask an extglob question it then had to refuse, so a
    workflow that correctly enables the option — or merely names it in a
    comment — lost its whole `declare check` to exit 2. Over-refusal is the
    direction that teaches people to route around a gate. The two readings
    of these lines do NOT produce the same text — one writes `);` and the
    other `)` — so the reading taken here is the EXTGLOB one, which emits no
    separator and marks no command position, and can therefore only ever
    join commands, never split one (`_ShellScan._bang_is_a_subshell`)."""
    text = declared_mod._shell_text(step, unknown)
    assert declared_mod._scope_runs(text) == {"app"}, (
        f"{shape}: a `!(` the reading of which changes nothing was not read")


@pytest.mark.parametrize("shape,step", [
    # The other direction, each measured the same way: bash does NOT reach
    # these verifies at the top level, and neither may the reader.
    ("an-arm-the-esac-never-closes", "case x in\na) echo L ;;\n"
     "warden verify --scope app\n"),
    ("inside-the-arm-itself", "case x in\na) (warden verify --scope app) ;;\n"
     "esac\n"),
    ("inside-a-conditional", "if false; then\nwarden verify --scope app\nfi\n"),
    # a `)` after which bash reads an ARGUMENT, not a command: were every `)`
    # a command position, the `case` here would close on the `esac` that bash
    # reads as a word and the verify below would read as unconditional
    ("after-a-process-substitution",
     "case x in\na) diff <(echo L) esac\nwarden verify --scope app\n"),
])
def test_a_verify_the_shell_does_not_reach_is_still_not_a_run(shape, step):
    """The bound on the cells above, so the `esac` fix cannot be had by
    ending the command at every `)`. The last shape is the one that pins it:
    `diff <(echo L) esac` runs `diff` with two arguments and bash 5.2 then
    hits end-of-file looking for the `esac` — a scanner that counted that `)`
    as a command position would credit a run bash never makes, which is the
    silent direction and the worse one."""
    assert declared_mod._scope_runs(declared_mod._shell_text(step)) == set(), (
        f"{shape}: a verify the shell does not reach was counted as a run")


@pytest.mark.parametrize("shape,step", [
    # With extglob ON, `!(a)` is a PATTERN and the words after it are the
    # same command's arguments — bash 5.2 runs one `echo` and never calls
    # `warden` (`docker run --rm bash:5.2`, `warden` a function that echoes:
    # no `ran:` line). A reader that wrote `);` after the pattern's `)` would
    # split one command into two and credit a scope the job never runs, which
    # deletes a real D-03 finding and prints clean at exit 0.
    ("after an echo",
     "shopt -s extglob\necho !(a) warden verify --scope app"),
    ("after a printf",
     "shopt -s extglob\nprintf x !(a) warden verify --scope app"),
    # The `(` at a step's very first character is a SUBSHELL, so the `case`
    # after it opens a block and the verify inside the arm is conditional.
    # `prev` is a newline there and not `""`, because `"" not in "<>=@!+*?"`
    # is False in Python and the empty string read the subshell as an extglob.
    ("a subshell at the step's first character",
     "(echo a) case x in a) warden verify --scope app;; esac"),
])
def test_a_scope_after_a_paren_bash_reads_an_argument_after_is_not_a_run(
        shape, step):
    """The silent direction, pinned on both readings of a `)`. Each cell is
    `set()` here and `{'app'}` under the misreading it guards."""
    assert declared_mod._scope_runs(declared_mod._shell_text(step)) == set(), (
        f"{shape}: a word bash reads as an argument was counted as a run")




_NEGATED_SUBSHELL_ARM = 'X="$(\n  case $OS in\n    a) !(false) esac\n)"'


@pytest.mark.parametrize("shape,step", [
    ("extglob-off-by-default", _NEGATED_SUBSHELL_ARM),
    ("extglob-set-then-unset",
     "shopt -s extglob\nshopt -u extglob\n" + _NEGATED_SUBSHELL_ARM),
])
def test_esac_after_a_negated_subshell_still_ends_the_case_inside_a_substitution(
        shape, step):
    """With extglob off — bash's default, and the hosted runner's — `!(cmd)`
    is a negated subshell, not a pattern, so after its `)` the `esac` ends
    the case exactly as it does after `(cmd)`. Read as an extglob, the
    `esac` would be missed, the substitution never closed, and the verify and
    the review mention after it swallowed. Each shape was run on bash 5.2
    with the rest of the step executing. Like the plain-subshell cell above,
    this pins the scan's text; the arm's command list is a separate reader."""
    text = declared_mod._shell_text(step + _REST_OF_THE_STEP)
    assert "warden verify --scope example" in text, (
        f"{shape}: the verify after the negated subshell was swallowed")
    assert "warden review" in text, (
        f"{shape}: the review mention after the negated subshell was swallowed")


# A `shopt` naming extglob anywhere but an unconditional top-level line of its
# own. Run for real on bash 5.2, not `bash -n`: none of these turns extglob on
# for a later line, except the unset that never runs, which leaves it ON. The
# `shopt` on the substitution's own line, inside it, or inside the same
# multi-line `if` is not in effect when bash parses the `!(`. The scan cannot
# tell which state holds, so it must refuse to read the step rather than guess.
# A quoted mention is refused too: the scan matches raw lines with the quotes
# removed, so `shopt -s "extglob"`, which bash runs, is not missed.
@pytest.mark.parametrize("shape,step", [
    ("inside-an-if-that-does-not-run",
     "if false; then shopt -s extglob; fi\n" + _NEGATED_SUBSHELL_ARM),
    ("behind-an-environment-guard",
     'if [ -n "${DEBUG_GLOBS:-}" ]; then shopt -s extglob; fi\n'
     + _NEGATED_SUBSHELL_ARM),
    ("in-an-uncalled-function",
     "enable_globs() { shopt -s extglob; }\n" + _NEGATED_SUBSHELL_ARM),
    ("in-a-subshell", "( shopt -s extglob )\n" + _NEGATED_SUBSHELL_ARM),
    ("in-a-command-substitution",
     "X=$(shopt -s extglob)\n" + _NEGATED_SUBSHELL_ARM),
    ("after-a-failing-and", "false && shopt -s extglob\n" + _NEGATED_SUBSHELL_ARM),
    ("on-its-own-line-after-a-trailing-and",
     "false &&\nshopt -s extglob\n" + _NEGATED_SUBSHELL_ARM),
    ("as-an-argument", "echo shopt -s extglob\n" + _NEGATED_SUBSHELL_ARM),
    ("as-a-quoted-argument",
     'echo "shopt -s extglob"\n' + _NEGATED_SUBSHELL_ARM),
    ("inside-a-loop-that-does-not-run",
     "while false; do shopt -s extglob; done\n" + _NEGATED_SUBSHELL_ARM),
    ("inside-the-same-multi-line-if",
     "if true; then\n  shopt -s extglob\n" + _NEGATED_SUBSHELL_ARM + "\nfi"),
    ("on-the-substitutions-own-line",
     "shopt -s extglob; " + _NEGATED_SUBSHELL_ARM),
    ("inside-the-substitution",
     'X="$(\n  shopt -s extglob\n  case $OS in\n    a) !(false) esac\n)"'),
    ("an-unset-that-does-not-run",
     "shopt -s extglob\nif false; then shopt -u extglob; fi\n"
     + _NEGATED_SUBSHELL_ARM),
    ("bashopts-named-in-the-step", "export BASHOPTS\n" + _NEGATED_SUBSHELL_ARM),
    ("a-set-o-spelling", "set -o extglob || true\n" + _NEGATED_SUBSHELL_ARM),
    ("a-nested-bash-with-extglob",
     "bash -O extglob -c true\n" + _NEGATED_SUBSHELL_ARM),
    # three lines that look like top-level `shopt`s but that bash 5.2 never
    # runs in the main shell. After a trailing `|&` the
    # line is the pipeline's last element, a subshell; inside a multi-line
    # backtick substitution it runs in that substitution; and once a `shopt`
    # function is defined it shadows the builtin, so the line calls it.
    ("on-its-own-line-after-a-trailing-pipe-and",
     "true |&\nshopt -s extglob\n" + _NEGATED_SUBSHELL_ARM),
    ("inside-a-multi-line-backtick-substitution",
     "X=`\nshopt -s extglob\n`\n" + _NEGATED_SUBSHELL_ARM),
    # a backtick inside double quotes opens a substitution too, and its own
    # `"` does not close the outer span
    ("inside-a-double-quoted-multi-line-backtick-substitution",
     'X="`echo "\nshopt -s extglob\n"`"\n' + _NEGATED_SUBSHELL_ARM),
    ("after-a-shopt-function-shadows-the-builtin",
     "shopt() { :; }\nshopt -s extglob\n" + _NEGATED_SUBSHELL_ARM),
    ("after-a-function-keyword-shopt-shadows-the-builtin",
     "function shopt {\n  :\n}\nshopt -s extglob\n" + _NEGATED_SUBSHELL_ARM),
    # A function is not the only way `shopt` stops being the builtin. Each
    # of these was run on bash 5.2 followed by `builtin shopt extglob`, and
    # each printed `extglob off` where the bare `shopt -s extglob` prints
    # `extglob on` — so the scan honouring the line read a state bash never
    # reached, and the `!(` after it as an extglob.
    ("after-an-alias-shadows-the-shopt-builtin",
     "shopt -s expand_aliases\nalias shopt=:\nshopt -s extglob\n"
     + _NEGATED_SUBSHELL_ARM),
    # The alias without `shopt -s expand_aliases` in the step is inert in a
    # script bash starts fresh — but expand_aliases can come from outside the
    # step's text (`BASHOPTS`, `bash -O`, a `BASH_ENV` file), none of which
    # this scan reads, so it may not assume the alias does nothing.
    ("after-an-alias-with-no-expand-aliases-line",
     "alias shopt=:\nshopt -s extglob\n" + _NEGATED_SUBSHELL_ARM),
    # `enable -n shopt` goes further than shadowing: bash 5.2 then reports
    # `shopt: command not found`, and even `builtin shopt` is gone.
    ("after-enable-n-turns-the-shopt-builtin-off",
     "enable -n shopt\nshopt -s extglob\n" + _NEGATED_SUBSHELL_ARM),
])
def test_a_negated_paren_after_an_extglob_state_the_scan_cannot_decide_is_refused(
        shape, step):
    with pytest.raises(declared_mod.DeclareError, match="extglob"):
        declared_mod._shell_text(step + _REST_OF_THE_STEP)


@pytest.mark.parametrize("shape,step", [
    ("after-a-closed-if",
     'if true; then echo x; fi\nshopt -s extglob\nX="$(echo !(a) case)"'),
    ("after-a-function-definition",
     'f() { :; }\nshopt -s extglob\nX="$(echo !(a) case)"'),
    ("inside-a-later-if-block",
     'shopt -s extglob\nif true; then\n  X="$(echo !(a) case)"\nfi'),
    # the bound on the backtick refusals: a substitution CLOSED on its own
    # line leaves the next line top-level, quoted or not
    ("after-a-closed-backtick-substitution",
     'X=`date`\nshopt -s extglob\nX="$(echo !(a) case)"'),
    ("after-a-closed-double-quoted-backtick-substitution",
     'X="`date`"\nshopt -s extglob\nX="$(echo !(a) case)"'),
    # The bound on the shadow refusals: a `shopt` line is unhonoured only
    # when `shopt` ITSELF was taken away. Each of these four prints
    # `extglob on` under `builtin shopt extglob` on bash 5.2, so refusing
    # any of them would be a scanner that trains people to route around it.
    ("after-an-alias-of-another-name",
     "shopt -s expand_aliases\nalias ll='ls -l'\n"
     'shopt -s extglob\nX="$(echo !(a) case)"'),
    ("after-an-alias-whose-name-merely-holds-shopt",
     "shopt -s expand_aliases\nalias myshopt=:\n"
     'shopt -s extglob\nX="$(echo !(a) case)"'),
    ("after-an-enable-with-no-n",
     'enable shopt\nshopt -s extglob\nX="$(echo !(a) case)"'),
    ("after-an-enable-n-of-another-builtin",
     'enable -n alias\nshopt -s extglob\nX="$(echo !(a) case)"'),
])
def test_a_top_level_shopt_line_is_still_honoured_after_closed_blocks(
        shape, step):
    """bash 5.2, run for real: a `shopt -s extglob` line at the top level
    after a closed block is in effect for the lines below it, so `case`
    after the `!(a)` is an argument and the step runs on."""
    text = declared_mod._shell_text(step + _REST_OF_THE_STEP)
    assert declared_mod._scope_runs(text) == {"example"}, (
        f"{shape}: a top-level extglob was not honoured")


# The residual of the verb rule, listed one shape at a time rather than left
# to be discovered. `_shopt_shadow` reads a command's VERB for the `alias` and
# `enable` spellings, so it sees those only where the step runs one directly —
# and every shape below installs the same shadow through a word the scan never
# reads as either.
#
# The FUNCTION spelling is not here, and the test after this one says why: it
# is matched wherever it sits on the line, so `eval "shopt() { :; }"` and a
# function that defines the shadow when called are both caught already. The
# residual is the alias and enable half reached indirectly, plus whatever a
# file this scan never opens contains — not everything reached indirectly.
#
# MEASURED on bash 5.2 (`docker run --rm bash:5.2`), each step followed by
# `builtin shopt extglob`, against a bare `shopt -s extglob` control that
# prints `extglob on`:
#   eval'd alias                          -> extglob off
#   eval "enable -n shopt"                -> shopt: command not found, and
#                                            `builtin shopt` gone as well
#   `. setup.sh`, setup.sh defining a shopt function -> extglob off
#   `. setup.sh`, setup.sh with expand_aliases + alias -> extglob off
#   `. setup.sh`, setup.sh running enable -n shopt  -> builtin gone
# WHAT THE SOURCED FILE CONTAINS matters and is stated rather than implied: a
# bare `alias shopt=:` in it with no `shopt -s expand_aliases` anywhere prints
# `extglob on` — bash expands no aliases in a non-interactive shell — so that
# one spelling is not a shadow at all and is not claimed as one. The step text
# is identical for all four, since the scan never opens the file; what the
# measurement settles is which of them the scan is WRONG about, and it is
# wrong about three.
#
# The step must not SPELL the shadow, which is why setup.sh is referenced and
# never written inline: `printf "shopt() { :; }" > setup.sh` puts the function
# spelling on the line and the scan catches it — measured, and the reason the
# first draft of these cells was red.
_INDIRECT_SHADOWS_UNSEEN = [
    ("an-eval-defined-alias",
     'shopt -s expand_aliases\neval "alias shopt=:"\n'
     'shopt -s extglob\nX="$(echo !(a) case)"'),
    ("an-eval-that-disables-the-builtin",
     'eval "enable -n shopt"\nshopt -s extglob\nX="$(echo !(a) case)"'),
    # The three sourced cells differ only in what setup.sh CONTAINS, which is
    # the point: the step text is the same either way and the scan never opens
    # the file, so one step stands for all three. The `.` and `source` verbs
    # are both spelled because the scan reads a verb.
    ("a-sourced-file-spelled-dot", '. ./setup.sh\n'
     'shopt -s extglob\nX="$(echo !(a) case)"'),
    ("a-sourced-file-spelled-source", 'source ./setup.sh\n'
     'shopt -s extglob\nX="$(echo !(a) case)"'),
]


@pytest.mark.parametrize("shape,step", _INDIRECT_SHADOWS_UNSEEN,
                         ids=[s for s, _ in _INDIRECT_SHADOWS_UNSEEN])
def test_a_shadow_reached_indirectly_is_unseen_and_said_so(shape, step):
    """The limit of reading the verb, executed rather than described.

    Each step here takes `shopt` away from the builtin without ever spelling
    `alias`, `enable` or `shopt() {` where the scan looks, so the scan honours
    the `shopt -s extglob` line after it and reads the `!(` as an extglob
    pattern — on a state bash never reached. `X="$(echo !(a) case)"` therefore
    parses as the pattern reading and the step goes on, where the honest
    answer is `ExtglobUndecidable`.

    THE DIRECTION IS SILENT, which is why this is a listed residual and not a
    fix: the scan reads the step rather than refusing it, so nothing shouts.
    An over-refusal would be louder and worse, and treating every `eval`,
    `source` and `.` as making the shopt state unknown from there on would
    refuse any workflow that sources a setup file — the shape that trains
    people to route around a scanner.

    This test goes RED the day that changes, which is the point: the residual
    is pinned to its current size, so closing it is a deliberate edit here and
    widening it cannot happen in silence.
    """
    text = declared_mod._shell_text(step + _REST_OF_THE_STEP)
    assert declared_mod._scope_runs(text) == {"example"}, (
        f"{shape}: the scan now sees this shadow — if that is deliberate, "
        "move the cell to the refusal test above and say so in "
        "`_shopt_shadow`'s docstring")
    for line in step.splitlines()[:-1]:
        assert not declared_mod._shopt_shadow(line), (
            f"{shape}: the verb rule reads a shadow in `{line}`, so this cell "
            "is not the residual it says it is")


def test_the_residual_is_counted_not_only_listed():
    """The count, because nothing else can see a cell leave.

    "Widening it cannot happen in silence" is a claim about this table, and a
    cell deleted with the shape it names would keep every assertion above
    green. FOUR cells, named once, and moving one in or out is an edit here.

    THREE of the four carry a measured configuration; the fourth does not,
    and saying so is the point. The measurement block above spells `.
    setup.sh` in all three of its sourced rows and never runs `source`, so
    the `a-sourced-file-spelled-source` cell is carried by the VERB RULE the
    scan implements rather than by anything run on bash 5.2 — the sourced
    contents vary the measurement, not the step text, which is identical
    however the file is written.
    """
    assert [shape for shape, _ in _INDIRECT_SHADOWS_UNSEEN] == [
        "an-eval-defined-alias",
        "an-eval-that-disables-the-builtin",
        "a-sourced-file-spelled-dot",
        "a-sourced-file-spelled-source"], (
        "a shape left the residual table; if that was deliberate, or the "
        "scan now sees it, say which in the same change")


@pytest.mark.parametrize("shape,step", [
    ("an-eval-defined-shopt-function",
     'eval "shopt() { :; }"\nshopt -s extglob\nX="$(echo !(a) case)"'),
    ("a-function-that-defines-the-shadow-when-called",
     'setup() { shopt() { :; }; }\nsetup\n'
     'shopt -s extglob\nX="$(echo !(a) case)"'),
])
def test_the_function_spelling_is_caught_however_it_is_reached(
        shape, step):
    """The bound on the residual above, so it is not read wider than it is.

    The function spelling is matched wherever it sits on the quote-stripped
    line rather than at a verb, which is the loud direction — a definition
    that never runs is counted too — and it is also what makes these two
    shapes visible where the alias and enable ones are not. Both print
    `extglob off` on bash 5.2, and both are refused here.
    """
    with pytest.raises(declared_mod.DeclareError, match="extglob"):
        declared_mod._shell_text(step + _REST_OF_THE_STEP)


@pytest.mark.parametrize("shape,where", [
    ("step-env", "step"),
    ("job-env", "job"),
    ("workflow-env", "workflow"),
    ("step-shell", "shell"),
    ("defaults-shell", "defaults"),
    ("step-bash-env", "bash_env"),
    ("workflow-bash-env", "workflow_bash_env"),
    ("step-shell-bash-env", "shell_bash_env"),
    # An exported shell function reaches a step as `BASH_FUNC_<name>%%`, and
    # bash defines it before the script's first line: run on bash 5.2 with
    # `BASH_FUNC_shopt%%=() { :; }` in the environment, `shopt -s extglob`
    # then calls the function and `builtin shopt extglob` prints `extglob
    # off`. The step's own text shows a plain `shopt` line that the scan
    # would otherwise honour, so this too leaves the `!(` unreadable.
    ("step-env-exported-shopt-function", "shopt_function"),
    ("job-env-exported-shopt-function", "job_shopt_function"),
])
def test_d03_refuses_a_negated_paren_when_the_environment_can_set_extglob(
        tmp_path, shape, where):
    """BASHOPTS in the environment turns extglob on before the first line runs
    (bash 5.2), and so does a `bash -O extglob` shell, and so does a BASH_ENV
    file, which non-interactive bash sources before the script.
    None is in the step text, so D-03 has no reading of `!(` to trust and must
    not render one."""
    root = _repo(tmp_path, _base_yaml())
    step = {"name": "the gate",
            "run": _NEGATED_SUBSHELL_ARM + _REST_OF_THE_STEP}
    job = {"runs-on": "ubuntu-latest", "steps": [step]}
    doc = {"name": "ci", "on": {"pull_request": {}}, "jobs": {"gate": job}}
    if where == "step":
        step["env"] = {"BASHOPTS": "extglob"}
    elif where == "job":
        job["env"] = {"BASHOPTS": "extglob"}
    elif where == "workflow":
        doc["env"] = {"BASHOPTS": "extglob"}
    elif where == "shell":
        step["shell"] = "bash -O extglob {0}"
    elif where == "bash_env":
        step["env"] = {"BASH_ENV": "./globs.sh"}
    elif where == "workflow_bash_env":
        doc["env"] = {"BASH_ENV": "./globs.sh"}
    elif where == "shell_bash_env":
        step["shell"] = "env BASH_ENV=./globs.sh bash -e {0}"
    elif where == "shopt_function":
        step["env"] = {"BASH_FUNC_shopt%%": "() { :; }"}
        step["run"] = "shopt -s extglob\n" + step["run"]
    elif where == "job_shopt_function":
        job["env"] = {"BASH_FUNC_shopt%%": "() { :; }"}
        step["run"] = "shopt -s extglob\n" + step["run"]
    else:
        doc["defaults"] = {"run": {"shell": "bash -O extglob -e {0}"}}
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(yaml.safe_dump(doc))
    with pytest.raises(declared_mod.DeclareError) as raised:
        declared_mod.check_scope_runners(root, config_mod.load(root))
    message = str(raised.value)
    for cue in ("ci.yml", "gate", "the gate", "extglob"):
        assert cue in message, f"{shape}: the refusal does not name {cue!r}: {message}"


def _two_step_gate(tmp_path, first: str, second: str) -> Path:
    root = _repo(tmp_path, _base_yaml())
    steps = [{"name": "the setup", "run": first},
             {"name": "the gate", "run": second}]
    doc = {"name": "ci", "on": {"pull_request": {}},
           "jobs": {"gate": {"runs-on": "ubuntu-latest", "steps": steps}}}
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(yaml.safe_dump(doc))
    return root


@pytest.mark.parametrize("shape,write", [
    ("bashopts-written-literally", 'echo "BASHOPTS=extglob" >> "$GITHUB_ENV"'),
    ("bash-env-written-literally", "echo BASH_ENV=./globs.sh >> $GITHUB_ENV"),
    # the content cannot be read in general, so any write is the question
    ("written-through-a-variable", 'echo "$SETTING" >> "${GITHUB_ENV}"'),
])
def test_d03_refuses_a_negated_paren_after_an_earlier_step_writes_github_env(
        tmp_path, shape, write):
    """A line an earlier step appends to $GITHUB_ENV is in the
    environment of every later step in the job, so `BASHOPTS=extglob` written
    there starts the gate step's bash with extglob on (bash 5.2) although
    each step's own text and `env` show nothing. D-03 must not read the `!(`."""
    root = _two_step_gate(tmp_path, write,
                          _NEGATED_SUBSHELL_ARM + _REST_OF_THE_STEP)
    with pytest.raises(declared_mod.DeclareError) as raised:
        declared_mod.check_scope_runners(root, config_mod.load(root))
    message = str(raised.value)
    for cue in ("ci.yml", "the gate", "the setup", "GITHUB_ENV", "extglob"):
        assert cue in message, f"{shape}: the refusal does not name {cue!r}: {message}"


def test_d03_reads_a_negated_paren_before_a_later_step_writes_github_env():
    """The bound on the refusal above: $GITHUB_ENV reaches only LATER steps,
    so a write after the gate step leaves the gate step's `!(` a negated
    subshell, and D-03 reads the step as it always has."""
    steps = [{"name": "the gate",
              "run": _NEGATED_SUBSHELL_ARM + _REST_OF_THE_STEP},
             {"name": "the teardown",
              "run": 'echo "BASHOPTS=extglob" >> "$GITHUB_ENV"'}]
    doc = {"name": "ci", "on": {"pull_request": {}},
           "jobs": {"gate": {"runs-on": "ubuntu-latest", "steps": steps}}}
    # the scanned text, as the negated-subshell cells above pin it: the arm's
    # command list is `_scope_runs`'s, a separate reader
    text = declared_mod._job_runs(doc)["gate"][0]
    assert "warden verify --scope example" in text and "warden review" in text


def test_d03_on_the_example_consumer_is_not_clean_behind_a_guarded_extglob(
        tmp_path):
    """On a copy of the shipped example: a gate job that skips a declared
    scope is drift (exit 1). Adding a guarded `shopt -s extglob` that never
    runs must not make the scan read the `!(` as an extglob, swallow the
    `warden review` mention, and report clean (exit 0). It must exit 2,
    naming the step."""
    example = tmp_path / "hello-svc"
    shutil.copytree(ROOT / "examples" / "hello-svc", example)
    ci = example / ".github" / "workflows" / "ci.yml"
    original = ci.read_text()
    verify = "run: uv run --locked --project ../.. warden verify --scope app"
    review = ('run: uv run --locked --project ../.. warden review --event '
              '"$GITHUB_EVENT_PATH"')
    assert verify in original and review in original, "the example's workflow moved"

    def run_with(prefix: str) -> subprocess.CompletedProcess:
        body = ("run: |\n" + "".join(
            f"          {line}\n" for line in (
                prefix, 'X="$(', "  case $OS in", "    a) !(false) esac",
                ')"', review[len("run: "):])))
        ci.write_text(original.replace(verify, "run: 'true'")
                      .replace(review + "\n", body))
        return subprocess.run(
            [sys.executable, "-m", "warden.cli", "declare", "check"],
            cwd=example, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT)})

    unguarded = run_with("true")
    assert unguarded.returncode == 1, unguarded.stdout + unguarded.stderr
    guarded = run_with('if [ -n "${DEBUG_GLOBS:-}" ]; then shopt -s extglob; fi')
    assert guarded.returncode == 2, (
        "a guarded shopt that never runs changed D-03's verdict:\n"
        + guarded.stdout + guarded.stderr)
    assert "extglob" in guarded.stderr and "the AI rules gate" in guarded.stderr, (
        guarded.stderr)


@pytest.mark.parametrize("shape,opener", [
    ("echo-argument", 'X="$(echo case)"'),
    ("pytest-selector-argument", 'X="$(pytest -q -k case)"'),
    ("grep-pattern-argument", 'X="$(grep -l case README.md)"'),
    # The bound on the case-arm `)` position: a `)` that closes a nested
    # substitution is not one, so a `case` after it is still an argument.
    ("argument-after-a-nested-substitution", 'X="$(echo $(date) case)"'),
    # Nor is the `)` of a process substitution, an array assignment, an
    # extglob (valid under `bash -O extglob`) or an arithmetic expansion:
    # bash 5.2 runs `case` after each as an argument.
    ("argument-after-input-process-substitution", 'X="$(echo <(true) case)"'),
    ("argument-after-output-process-substitution", 'X="$(echo >(cat) case)"'),
    ("argument-after-an-array-assignment",
     'X="$(declare -a arr=(a b) case; echo ok)"'),
    ("argument-after-an-extglob-pattern", 'X="$(echo @(a) case)"'),
    # `!(` is an extglob only once `shopt -s extglob` has run on an earlier
    # line; bash 5.2 then runs `case` after it as an argument.
    ("argument-after-a-negated-extglob-with-extglob-set",
     'shopt -s extglob\nX="$(echo !(a) case)"'),
    ("argument-after-a-negated-extglob-with-extglob-set-among-options",
     'shopt -s nullglob extglob\nX="$(echo !(a) case)"'),
    ("argument-after-arithmetic-expansion", 'X="$(echo $((1+2)) case)"'),
])
def test_case_as_an_ordinary_argument_inside_a_substitution_does_not_swallow_the_step(
        tmp_path, shape, opener):
    """`case` is a keyword only in COMMAND position. As an argument inside
    `$( )` — `echo case`, `pytest -k case`, `grep -l case` — bash runs it as
    a word (measured on bash 3.2 and 5.2), so the substitution's `)` is its
    closer. Read as a keyword, that `)` would be taken for a case arm's, the
    substitution never closed, and the `"` after it would open a quote that
    swallows the verify line AND the `warden review` mention: D-03 would read
    the job as no gate job at all and report not-applicable at exit 0."""
    step = opener + _REST_OF_THE_STEP
    text = declared_mod._shell_text(step)
    assert declared_mod._scope_runs(text) == {"example"}, (
        f"{shape}: the verify after the substitution was swallowed")
    assert "warden review" in text, (
        f"{shape}: the review mention after the substitution was swallowed")
    root = _repo(tmp_path, _base_yaml(verify={
        "tests": [{"run": "true", "covers": ["**"]}],
        "example": [{"run": "true", "covers": ["app/**"]}]}))
    _workflow(root, ["warden verify --scope tests", step])
    result = declared_mod.check_scope_runners(root, config_mod.load(root))
    assert result.status == declared_mod.CLEAN, (
        f"{shape}: {result.status} — {result.detail}")


@pytest.mark.parametrize("shape,step,runs", [
    # Held by the PAREN counter alone. Read as the closer, the `)` resumes
    # the quote outside, the mention's own `"` closes it, and the quoted
    # scope reads as RUN — a silence.
    ("nested-subshell",
     'X="$(\n  (echo L)\n  echo "warden verify --scope example"\n)"', set()),
    # Held by the CASE counter alone. bash 5.2 — the shell a hosted runner
    # runs — parses a bare `linux)` arm inside `$( )` (bash 3.2 rejects it)
    # and runs the verify after the substitution. Read as the closer, the
    # arm's `)` resumes the quote, `esac` is swallowed into it, and the open
    # `case` hides the verify — a false drift.
    ("bare-case-arm",
     'X="$(\n  case $OS in\n    linux) echo L;;\n  esac\n)"\n'
     "warden verify --scope example", {"example"}),
    # Held by the keyword test: after `then`, `case` is still in command
    # position, and the same arm must not close the substitution.
    ("bare-case-arm-after-then",
     'X="$(\n  if true; then case $OS in\n    linux) echo L;;\n  esac; fi\n)"\n'
     "warden verify --scope example", {"example"}),
    # The other command positions a keyword can follow, each a shape bash 5.2
    # runs: the condition of `while`, `until`, `if` and `elif`, and a nested
    # `case` straight after a bare arm's `)` — whose second outer arm is the
    # `)` a missed inner `case` would take for the closer.
    ("case-after-while",
     'X="$(\n  while case $OS in\n    linux) false;;\n  esac; do :; done\n)"\n'
     "warden verify --scope example", {"example"}),
    ("case-after-until",
     'X="$(\n  until case $OS in\n    linux) true;;\n  esac; do :; done\n)"\n'
     "warden verify --scope example", {"example"}),
    ("case-after-if",
     'X="$(\n  if case $OS in\n    linux) true;;\n  esac; then echo L; fi\n)"\n'
     "warden verify --scope example", {"example"}),
    ("case-after-elif",
     'X="$(\n  if false; then :; elif case $OS in\n    linux) true;;\n'
     '  esac; then echo L; fi\n)"\n'
     "warden verify --scope example", {"example"}),
    # Wrapped in `if ... fi` so the early close swallows the `fi` and leaves
    # a block open over the verify; bare, the inner `esac` happens to
    # balance the outer `case` and the misread is invisible.
    ("nested-case-after-a-bare-arm",
     'X="$(\n  if true; then\n    case $OS in\n      linux) case $ARCH in\n'
     '        arm64) echo A;;\n      esac;;\n      darwin) echo D;;\n'
     '    esac\n  fi\n)"\n'
     "warden verify --scope example", {"example"}),
])
def test_a_paren_that_closes_something_inside_a_substitution_is_not_its_closer(
        shape, step, runs):
    """Each counter pinned by a shape only it holds: deleting the paren
    counter reddens the first cell, deleting the case counter the second
    and third, and dropping the command-position keywords the third. Each
    expectation was measured on bash 5.2."""
    assert declared_mod._scope_runs(declared_mod._shell_text(step)) == runs, (
        f"{shape}: a `)` inside the substitution was read as its closer")


# --- a backtick substitution is taken whole, not carried as a context --------

@pytest.mark.parametrize("shape,step,runs,reaches_review", [
    # Named regression, each cell run on bash 5.2. Modelled as a CONTEXT that
    # ends at the next backtick, the `#` skip ran first and ate the closing
    # backtick with the comment, so the context stayed open to the end of the
    # step. bash does the opposite: it finds the closing backtick over the raw
    # characters FIRST, and only then reads the span, where the `#` is a
    # comment of the span's own.
    #
    # Here the verify sits inside the double-quoted string the backtick span
    # is embedded in: bash echoes the line and runs nothing. Reading the
    # context as still open made the verify a top-level command — a scope
    # D-03 credits that bash never runs, the silent direction.
    ("a-comment-inside-a-quoted-span",
     'echo "`true # c`\nwarden verify --scope example\n"\nwarden review',
     set(), True),
    # The other direction from the same defect: with the span left open, the
    # `warden review` line is swallowed, the job stops being a gate job, and
    # D-03 renders not-applicable at exit 0. bash reaches the review line.
    ("a-comment-inside-an-unquoted-span",
     'Y=`echo a # note`\necho "sha `date`"\nwarden review', set(), True),
    # A heredoc opened inside a span is the span's own: bash sets X to `body`
    # and runs on. Waiting for a bare terminator line swallowed the rest of
    # the step, review mention included.
    ("a-heredoc-inside-a-quoted-span",
     'X="`cat <<EOF\nbody\nEOF`"\nwarden review', set(), True),
    # A heredoc whose OPENER is outside the span takes its body from after
    # the whole command, not from inside the span (bash 5.2, measured):
    # the verify here is heredoc body and runs nothing.
    ("a-heredoc-opened-before-a-multi-line-span",
     "cat <<EOF `echo a\nb`\nwarden verify --scope example\nEOF\nwarden review",
     set(), True),
    # The bound, so the fix cannot be had by ignoring backticks again: a span
    # that really does hold the invocation still reads as a run, and a span
    # under a mention-maker still does not.
    ("a-verify-that-is-the-span",
     "`warden verify --scope example`\nwarden review", {"example"}, True),
    ("a-span-under-a-mention-maker",
     "echo `warden verify --scope example`\nwarden review", set(), True),
])
def test_a_backtick_span_ends_at_its_closing_backtick_whatever_it_holds(
        shape, step, runs, reaches_review):
    """Named regression for the two shapes a backtick CONTEXT read wrongly.
    Both halves are asserted on every cell, because the defect showed up as
    each in turn: which scopes the step runs, and whether the `warden review`
    mention that makes the job a gate job at all survives the scan."""
    text = declared_mod._shell_text(step)
    assert declared_mod._scope_runs(text) == runs, (
        f"{shape}: the scopes read out of the step are not the ones bash runs")
    assert ("warden review" in text) is reaches_review, (
        f"{shape}: the review mention after the span was lost")


def test_a_backtick_span_with_no_closer_takes_the_rest_of_the_step():
    """The stated residual, in the loud direction: bash would not parse the
    step at all, so the reader swallows what follows rather than inventing a
    top-level command out of it."""
    text = declared_mod._shell_text("X=`echo a\nwarden verify --scope example")
    assert declared_mod._scope_runs(text) == set(), (
        "an unterminated span invented a run")


# The `!(` sits inside a `$( )` INSIDE the span, which is where an
# unreadable extglob state is refused rather than read the safe way.
_SPAN_WITH_A_NEGATED_ARM = "X=`Y=$(case $OS in\n  a) !(false) esac\n)`\n"


@pytest.mark.parametrize("shape,step,unknown", [
    # The span is scanned as its own text, so it needs the extglob state the
    # OUTER scan had reached — otherwise it starts at bash's default (off),
    # reads the `!(` as a negated subshell, and a step the scan cannot decide
    # reads as one it can. One cell per carried field: each flips from exit 2
    # to a counted run when that field's carry-in is dropped, which is the
    # silent direction.
    ("extglob unknown from a line above",
     "echo extglob\n" + _SPAN_WITH_A_NEGATED_ARM, None),
    ("a shopt shadow from a line above",
     "shopt() { :; }\nX=`Y=$(shopt -s extglob\n  case $OS in\n"
     "    a) !(false) esac\n)`\n", None),
    ("a reason from outside the step's text",
     _SPAN_WITH_A_NEGATED_ARM, "the step `env` sets BASHOPTS"),
])
def test_a_backtick_span_inherits_the_extglob_state_it_opens_under(
        shape, step, unknown):
    """Named regression for a guard the first draft had no cell for: the three
    `span.*` carry-in lines in `_backtick` could be deleted with the suite
    still green."""
    with pytest.raises(declared_mod.DeclareError, match="extglob"):
        declared_mod._shell_text(step + "warden verify --scope app", unknown)


def test_a_backtick_span_with_a_readable_extglob_state_is_still_read():
    """The bound: nothing above the span makes extglob a question, so the
    `!(` is the negated subshell bash reads with extglob off and the verify
    after the span is a run."""
    text = declared_mod._shell_text(
        _SPAN_WITH_A_NEGATED_ARM + "warden verify --scope app")
    assert declared_mod._scope_runs(text) == {"app"}


@pytest.mark.parametrize("shape,line", [
    # Each of these leaves `shopt` the builtin on bash 5.2: run followed by
    # `shopt -s extglob` and `builtin shopt extglob`, every one prints
    # `extglob on`. A reader that bridged from the verb to a later `shopt`
    # token would refuse each with a message naming a command it never runs.
    ("enable -n of another builtin, then a real shopt",
     "enable -n echo && shopt -s extglob"),
    ("enable -n of another builtin, shopt only in the comment",
     "enable -n printf  # keeps shopt honest"),
    ("enable -n of another builtin, then enable shopt",
     "enable -n alias; enable shopt"),
    ("a loop whose words happen to be alias and shopt",
     "for a in alias enable; do shopt=1; done"),
    ("enable -n of another builtin redirected to a shopt file",
     "enable -n echo > shopt.log"),
    ("enable -n of another builtin beside a substitution naming shopt",
     "enable -n echo $(true grep shopt f)"),
    # The whole spelling, in a comment. A bridge that only refused a `#`
    # BETWEEN the verb and `shopt` still fired here, and the refusal quoted
    # `enable -n shopt` back at a line that only mentions it.
    ("the whole spelling inside a trailing comment",
     "rm -rf build  # enable -n shopt if you must"),
    ("alias and shopt as arguments of a mention-maker",
     'echo "alias shopt=:"'),
    ("an alias of another name", "alias ll=\'ls -l\'"),
    ("an alias whose name merely holds shopt", "alias myshopt=:"),
    ("an enable with no -n", "enable shopt"),
    ("an enable -n of another builtin", "enable -n alias"),
])
def test_a_line_that_leaves_shopt_the_builtin_is_not_read_as_a_shadow(
        shape, line):
    """The SILENT side of the shadow reader, which the firing cells cannot
    give. Over-refusal is the direction that teaches people to route around
    a gate: every line here leaves `shopt` the builtin, so refusing a `!(`
    below it would cost the repository its whole `declare check`."""
    assert declared_mod._shopt_shadow(line) == "", (
        f"{shape}: `{line}` was read as shadowing `shopt`")


@pytest.mark.parametrize("shape,line", [
    # The other side, and the one a quote-stripping reader lost: bash 5.2
    # with `shopt -s expand_aliases` above each of these, then
    # `shopt -s extglob` and `builtin shopt extglob`, prints `extglob off`.
    # The separator that would end the command is inside a STRING.
    ("a separator inside the aliased command",
     "alias gs='git status | head' shopt=:"),
    ("a semicolon inside the aliased command", "alias two='a; b' shopt=:"),
    # And the shapes a verb-anchored reader must still reach.
    ("an alias behind a then", "if true; then alias shopt=:; fi"),
    ("an alias after a leading assignment", "X=1 alias shopt=:"),
    ("the quoted spelling bash still runs", "alias \'shopt\'=:"),
])
def test_a_shadow_whose_command_holds_a_quoted_separator_is_still_read(
        shape, line):
    """A `;` or `|` inside a quoted word is not a separator, and a reader
    that strips the quotes before splitting cannot tell the two apart. It
    lost these — and honouring a `shopt -s extglob` bash never ran is the
    guess `ExtglobUndecidable` exists to refuse."""
    assert declared_mod._shopt_shadow(line) != "", (
        f"{shape}: `{line}` was not read as shadowing `shopt`")

def test_the_shadow_reader_is_fed_the_line_with_its_quotes_still_on():
    """End to end, because the cells above call `_shopt_shadow` directly and
    cannot see WHICH line `_end_line` hands it. Stripped of its quotes first,
    the `|` inside the aliased command reads as a separator, the shadow is
    missed, the `shopt -s extglob` below it is honoured, and the `!(` is read
    on a state bash never reached instead of refused."""
    step = ("shopt -s expand_aliases\n"
            "alias gs='git status | head' shopt=:\n"
            "shopt -s extglob\n" + _NEGATED_SUBSHELL_ARM + _REST_OF_THE_STEP)
    with pytest.raises(declared_mod.DeclareError, match="extglob"):
        declared_mod._shell_text(step)



@pytest.mark.parametrize("shape,key,shadows", [
    ("the exported-function spelling", "BASH_FUNC_shopt%%", True),
    # bash 5.2 does NOT import this pre-2014 spelling (measured: `extglob
    # on` with it exported), so the match is for a runner on a bash that
    # does — the loud direction, refusing a step rather than reading it.
    ("the pre-2014 exported-function spelling", "BASH_FUNC_shopt()", True),
    # `BASH_FUNC_shopts%%` exports a function named `shopts`; bash 5.2 with
    # it in the environment prints `extglob on` after `shopt -s extglob`.
    ("a longer name that merely starts with shopt", "BASH_FUNC_shopts%%", False),
    ("a saved-copy name", "BASH_FUNC_shopt_save%%", False),
    ("an unrelated exported function", "BASH_FUNC_echo%%", False),
])
def test_only_an_exported_shopt_function_makes_the_environment_a_question(
        shape, key, shadows):
    """The SILENT side of the env check: matched by prefix it blamed every
    `BASH_FUNC_shopt*` key, and the message then told the reader the env
    exports a `shopt` function it does not."""
    reason = declared_mod._extglob_from_outside(
        {}, {}, {"env": {key: "() { :; }"}})
    assert bool(reason) is shadows, f"{shape}: {key} -> {reason!r}"


def test_an_escaped_backtick_does_not_close_the_span():
    """`` `echo x\\`y` `` ends at the LAST backtick on bash 5.2 — the
    backslash quotes the one before it — so the span is not closed early and
    the line after it stays top-level."""
    text = declared_mod._shell_text(
        "X=`echo x\\`y`\nwarden verify --scope example")
    assert declared_mod._scope_runs(text) == {"example"}, (
        "the escaped backtick was read as the closer")


# --- the direction paragraph, driven -----------------------------------------

_SILENCE = "silence"    # the reader counts a run the shell would not make
_DRIFT = "drift"        # the reader hides an invocation the shell would run

_DIRECTION_PROBES = [
    # No escaped-quote or `$'...'` entry: the scan models those escapes, a
    # limit that is modelled is not a limit, and the docstring does not list
    # them.
    ("an unquoted indirection", "bash -c warden verify --scope example",
     _SILENCE),
    ("nested subshell",
     "((cd d && cat <<EOF) ; true)\nwarden verify --scope example\nEOF",
     _SILENCE),
    ("unterminated quote", 'echo "x; warden verify --scope example', _DRIFT),
    ("unterminated heredoc", "cat <<EOF\nwarden verify --scope example",
     _DRIFT),
    ("unterminated backtick span", "X=`echo a\nwarden verify --scope example",
     _DRIFT),
    ("mixed-quoted delimiter",
     "cat <<E'O'F\nbody\nEOF\nwarden verify --scope example", _DRIFT),
    ("$[ ]", "x=$[1<<2]\nwarden verify --scope example", _DRIFT),
    ("backtick", "echo `warden verify --scope example`", _DRIFT),
    ("a quoted indirection", 'bash -c "warden verify --scope example"',
     _DRIFT),
]


@pytest.mark.parametrize("limit,probe,direction", _DIRECTION_PROBES,
                         ids=[p[0] for p in _DIRECTION_PROBES])
def test_each_stated_limit_errs_in_the_direction_the_docstring_bins_it(
        limit, probe, direction):
    """Named regression. `_shell_text`'s closing paragraph sorts each stated
    limit by the direction it errs in, and a check of string PRESENCE alone
    lets a wrong direction claim pass. So each entry is driven: the probe is
    run through the reader, the direction is asserted from what comes back,
    and then the docstring is asserted to bin the limit under that heading
    and not the other."""
    runs = declared_mod._scope_runs(declared_mod._shell_text(probe))
    if direction == _SILENCE:
        assert "example" in runs, (
            f"{limit}: the probe no longer invents a run — the limit moved, "
            "and so must the paragraph")
    else:
        assert runs == set(), (
            f"{limit}: the probe no longer hides the invocation — the limit "
            "moved, and so must the paragraph")
    # whitespace-normalised: the paragraph wraps, and a phrase broken across
    # a line is the same phrase
    doc = " ".join(declared_mod._shell_text.__doc__.split())
    head, _, tail = doc.partition("Toward SILENCE")
    silence, _, drift = tail.partition("Toward a false DRIFT")
    assert silence and drift, "the direction paragraph lost a heading"
    mine, other = (silence, drift) if direction == _SILENCE else (drift, silence)
    assert limit in mine, f"{limit!r} is not binned under {direction}"
    assert limit not in other, f"{limit!r} is binned under both directions"


# --- D-05 ---------------------------------------------------------------------

FORGE_ALLOWS = "owner/probe\ntrue\nmain\n"
FORGE_DISALLOWS = "owner/probe\nfalse\nmain\n"


def _fake_gh(tmp_path: Path, repo_answer: str | None) -> Path:
    """A `gh` on PATH that answers the two reads D-05 makes: the repository
    (REPO_ANSWER on stdout, or a 403 when None) and the default branch's
    required checks. Every call is logged, so a test can say none was made."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "gh-calls.log"
    repo = (f"printf '{repo_answer}'" if repo_answer is not None else
            "echo 'gh: Resource not accessible by integration (HTTP 403)' >&2; "
            "exit 1")
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{log}'\n"
        'case "$2" in\n'
        f"  'repos/{{owner}}/{{repo}}') {repo} ;;\n"
        "  */required_status_checks) printf 'warden gate\\nwarden tests\\n' ;;\n"
        "  *) echo \"unexpected: $*\" >&2; exit 9 ;;\n"
        "esac\n")
    gh.chmod(0o755)
    return bin_dir


def _delegating_repo(tmp_path: Path) -> Path:
    root = _repo(tmp_path, _base_yaml())
    shutil.copy(ROOT / "graph.yaml", root / "graph.yaml")
    assert "delegation:" in (root / "graph.yaml").read_text(), (
        "this repo's graph.yaml no longer declares review.delegation")
    return root


def _on_path(monkeypatch, bin_dir: Path) -> None:
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_d05_refuses_a_forge_that_allows_auto_merge(tmp_path, monkeypatch,
                                                     capsys):
    """The case the check exists for: graph.yaml's delegation cannot spell
    auto-merge, and the repository has it switched on. The refusal names the
    exact setting, and `declare check` exits 1 on it."""
    root = _delegating_repo(tmp_path)
    _on_path(monkeypatch, _fake_gh(tmp_path, FORGE_ALLOWS))
    result = declared_mod.check_forge_auto_merge(root)
    assert result.status == declared_mod.DRIFT
    assert result.detail.startswith("REFUSED")
    assert "allow_auto_merge: true on owner/probe" in result.detail
    assert "merge-on-green" in result.findings[0].detail
    assert "warden gate, warden tests" in result.findings[0].detail
    monkeypatch.chdir(root)
    assert cli.main(["declare", "check"]) == 1
    assert "DRIFT: D-05" in capsys.readouterr().out


def test_d05_passes_a_forge_that_disallows_auto_merge(tmp_path, monkeypatch):
    root = _delegating_repo(tmp_path)
    _on_path(monkeypatch, _fake_gh(tmp_path, FORGE_DISALLOWS))
    result = declared_mod.check_forge_auto_merge(root)
    assert result.status == declared_mod.CLEAN
    assert result.detail.startswith(
        "PASS — allow_auto_merge: false on owner/probe")
    assert "required checks on `main`: warden gate, warden tests" in result.detail


@pytest.mark.parametrize("answer", [None, "owner/probe\n\nmain\n", ""])
def test_d05_reports_unreadable_and_never_clean(tmp_path, monkeypatch, capsys,
                                                answer):
    """Fail-closed: a 403, a missing setting (a token without admin is served
    none, which `--jq` prints as an empty line) or an empty answer is UNREADABLE — never PASS. No rung claims D-05,
    so the exit code stays 0, and neither the render nor the JSON calls the
    result clean."""
    root = _delegating_repo(tmp_path)
    _on_path(monkeypatch, _fake_gh(tmp_path, answer))
    result = declared_mod.check_forge_auto_merge(root)
    assert result.status == declared_mod.UNREADABLE
    assert result.detail.startswith("UNREADABLE")
    # The missing setting is named as missing — never the branch name shifted
    # into its place, which a whitespace split of `\n main\n` produced.
    assert "'main'" not in result.detail
    if answer and answer.startswith("owner/probe"):
        assert "on owner/probe came back empty" in result.detail
    monkeypatch.chdir(root)
    assert cli.main(["declare", "check"]) == 0
    out = capsys.readouterr().out
    assert "? D-05" in out and "not a clean result" in out
    assert "\nclean:" not in out
    assert cli.main(["declare", "check", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "unreadable"


def test_d05_reports_unreadable_when_gh_is_not_installed(tmp_path, monkeypatch):
    root = _delegating_repo(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    result = declared_mod.check_forge_auto_merge(root)
    assert result.status == declared_mod.UNREADABLE
    assert "`gh` is not on PATH" in result.detail


def test_d05_reads_no_forge_where_no_delegation_is_declared(tmp_path,
                                                            monkeypatch):
    """Not-applicable without calling `gh`: an enrolled consumer with no
    graph.yaml, or one declaring no delegation, pays no network read."""
    root = _repo(tmp_path, _base_yaml())
    _on_path(monkeypatch, _fake_gh(tmp_path, FORGE_ALLOWS))
    assert declared_mod.check_forge_auto_merge(root).status == declared_mod.SKIPPED
    doc = yaml.safe_load((ROOT / "graph.yaml").read_text())
    del doc["review"]["delegation"]
    (root / "graph.yaml").write_text(yaml.safe_dump(doc))
    result = declared_mod.check_forge_auto_merge(root)
    assert result.status == declared_mod.SKIPPED
    assert "no review.delegation" in result.detail
    assert not (tmp_path / "gh-calls.log").exists()
