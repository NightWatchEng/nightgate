"""The repair budget is a typed repo.yaml key, and R-12 reads only that key.

deliver caps repair at PLATFORM_REPAIR_CAP rounds, and a repo may set a lower
budget, never a higher one. The budget is declared as `repair: {budget: N}` in
repo.yaml and bounded by repo.schema.json. Certify rung R-12 fails at Level 3
when the key is undeclared, malformed or above the cap.

A budget line in `.warden/skills-policy.md` is documentation: R-12 does not
parse prose, and the last test below holds that it gates nothing.
"""

import json
import subprocess
from pathlib import Path

import pytest

from warden import certify as certify_mod
from warden import config as config_mod

ROOT = Path(__file__).resolve().parents[1]
CAP = certify_mod.PLATFORM_REPAIR_CAP


def _rung() -> dict:
    ladder = certify_mod.load(ROOT)
    found = [c for c in ladder["levels"][3]["checks"] if c["id"] == "R-12"]
    assert len(found) == 1, found
    return found[0]


def _repo_yaml(repair: str | None) -> str:
    text = ("version: 1\nrepo: t\n"
            "components:\n  app: {path: app/}\n"
            "risk_tiers:\n  - {glob: '**', tier: LOW}\n"
            "verify:\n  app:\n    - run: 'true'\n"
            "review:\n  rules_dir: .warden/rules\n"
            "  blocking_severities: [HIGH]\n")
    return text if repair is None else text + repair


def _budget(value) -> str:
    """A `repair:` block whose budget is `value`, spelled as YAML."""
    return f"repair:\n  budget: {json.dumps(value)}\n"


def _r12(root: Path) -> tuple[bool, str]:
    return certify_mod._run_check(_rung(), root)


# ---------- the schema ---------------------------------------------------------

def test_the_schema_maximum_is_the_platform_cap():
    """The schema and certify state the cap separately, so they are held
    equal here: a cap moved in one place and not the other fails this."""
    schema = json.loads(
        (ROOT / "warden" / "schemas" / "repo.schema.json").read_text())
    budget = schema["properties"]["repair"]["properties"]["budget"]
    assert budget["type"] == "integer"
    assert budget["minimum"] == 1
    assert budget["maximum"] == CAP
    assert "repair" not in schema["required"], (
        "a schema-required key fails every warden command and Level 1; the "
        "ruling requires it at Level 3, which is R-12's job")


@pytest.mark.parametrize("value", [1, CAP])
def test_the_loader_accepts_a_budget_within_the_cap(tmp_path, value):
    (tmp_path / "repo.yaml").write_text(_repo_yaml(_budget(value)))
    config_mod.load(tmp_path)


@pytest.mark.parametrize("repair", [
    pytest.param(_budget(CAP + 1), id="one-above-the-cap"),
    pytest.param(_budget(9), id="nine"),
    pytest.param(_budget(0), id="zero"),
    pytest.param(_budget(-1), id="negative"),
    pytest.param(_budget("2"), id="a-string"),
    pytest.param(_budget(True), id="a-boolean"),
    pytest.param(_budget(1.5), id="a-fraction"),
    pytest.param(_budget(None), id="null"),
    pytest.param("repair: {}\n", id="no-budget-in-the-block"),
    pytest.param("repair: 2\n", id="a-bare-number"),
    pytest.param("repair:\n  budget: 2\n  extra: 1\n", id="an-unknown-key"),
])
def test_the_loader_refuses_a_malformed_or_over_cap_budget(tmp_path, repair):
    (tmp_path / "repo.yaml").write_text(_repo_yaml(repair))
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(tmp_path)


def test_the_loader_accepts_a_repo_yaml_with_no_repair_block(tmp_path):
    """Optional to the schema: an undeclared budget costs Level 3, never
    every warden command."""
    (tmp_path / "repo.yaml").write_text(_repo_yaml(None))
    config_mod.load(tmp_path)


# ---------- the rung -------------------------------------------------------------

def test_the_rung_reads_repo_yaml_at_level_3():
    rung = _rung()
    assert rung["type"] == "policy_repair_budget_capped"
    assert rung["path"] == "repo.yaml"


@pytest.mark.parametrize("value", [1, CAP])
def test_a_budget_within_the_cap_passes_and_is_reported(tmp_path, value):
    (tmp_path / "repo.yaml").write_text(_repo_yaml(_budget(value)))
    ok, detail = _r12(tmp_path)
    assert ok, detail
    assert str(value) in detail, detail


@pytest.mark.parametrize("repair", [
    pytest.param(None, id="no-repair-block"),
    pytest.param("repair: {}\n", id="no-budget-in-the-block"),
    pytest.param("repair:\n", id="an-empty-block"),
])
def test_an_undeclared_budget_fails_naming_the_key_and_the_value(tmp_path,
                                                                repair):
    """An undeclared budget fails rather than passing as 'deliver's cap
    applies', and the failure says what to add."""
    (tmp_path / "repo.yaml").write_text(_repo_yaml(repair))
    ok, detail = _r12(tmp_path)
    assert not ok, f"an undeclared repair budget passed R-12: {detail}"
    assert "repair" in detail and "budget" in detail, detail
    assert str(CAP) in detail, detail


@pytest.mark.parametrize("value", [CAP + 1, 9, 99])
def test_a_budget_above_the_cap_fails(tmp_path, value):
    """The schema refuses it too; R-12 still reads the file itself, so the
    rung names the over-cap value rather than an unreadable config."""
    (tmp_path / "repo.yaml").write_text(_repo_yaml(_budget(value)))
    ok, detail = _r12(tmp_path)
    assert not ok, f"a budget of {value} passed under a cap of {CAP}"
    assert str(value) in detail and str(CAP) in detail, detail


@pytest.mark.parametrize("value", [0, -1, "2", True, 1.5, 2.0, None,
                                   [2], {"n": 2}])
def test_a_budget_that_is_not_a_whole_number_in_range_fails(tmp_path, value):
    (tmp_path / "repo.yaml").write_text(_repo_yaml(_budget(value)))
    ok, detail = _r12(tmp_path)
    assert not ok, f"budget {value!r} passed R-12: {detail}"


@pytest.mark.parametrize("text", [
    pytest.param("repair: 2\n", id="a-bare-number"),
    pytest.param("- a list\n", id="not-a-mapping"),
    pytest.param("repair: [\n", id="unparseable"),
])
def test_a_repo_yaml_the_rung_cannot_read_a_budget_from_fails(tmp_path, text):
    body = text if text.startswith("-") else _repo_yaml(text)
    (tmp_path / "repo.yaml").write_text(body)
    ok, detail = _r12(tmp_path)
    assert not ok, detail


def test_a_missing_repo_yaml_fails_the_rung(tmp_path):
    ok, detail = _r12(tmp_path)
    assert not ok, detail


def test_every_checked_in_repo_yaml_declares_a_budget_within_the_cap():
    """The platform gates itself: every repo.yaml this repo tracks (its own
    and hello-svc's) passes R-12."""
    tracked = subprocess.run(
        ["git", "ls-files", "repo.yaml", "*/repo.yaml"], cwd=ROOT,
        capture_output=True, text=True, check=True).stdout.split()
    assert len(tracked) >= 2, tracked
    for rel in tracked:
        ok, detail = _r12((ROOT / rel).parent)
        assert ok, f"{rel} fails R-12: {detail}"


# ---------- the policy prose gates nothing -----------------------------------------

def test_a_budget_line_in_the_skills_policy_does_not_affect_the_rung(tmp_path):
    """An over-cap prose budget beside a declared key passes; a compliant
    prose budget with no key fails. Only the key is read."""
    policy = tmp_path / ".warden" / "skills-policy.md"
    policy.parent.mkdir()
    policy.write_text("## Verify\n\n"
                      "- Iteration budget before a run reverts: 9 attempts.\n")
    (tmp_path / "repo.yaml").write_text(_repo_yaml(_budget(CAP)))
    assert _r12(tmp_path)[0] is True

    policy.write_text("## Verify\n\n"
                      "- Iteration budget before a run reverts: 1 attempt.\n")
    (tmp_path / "repo.yaml").write_text(_repo_yaml(None))
    assert _r12(tmp_path)[0] is False
