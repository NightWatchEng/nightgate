"""repo.yaml gains two optional lifecycle blocks: `design:` and `deploy:`.

Both are additive-optional. The repo.yaml root is additionalProperties:false,
so each key must be declared in the schema to be accepted at all — and ABSENT
must behave byte-identically to today, because a platform pull never hands an
enrolled repo a new obligation (the same contract the optional build-disciplines
section keeps).
"""

import shutil

import pytest
import yaml

from warden import config as config_mod
from warden import explain as explain_mod
from warden import verify as verify_mod


def _write(sample_repo, tmp_path, mutate):
    raw = yaml.safe_load((sample_repo / "repo.yaml").read_text())
    mutate(raw)
    (tmp_path / "repo.yaml").write_text(yaml.safe_dump(raw))
    # explain.render loads the ruleset, so carry it alongside the mutated config
    rules_src = sample_repo / raw["review"]["rules_dir"]
    if rules_src.is_dir():
        shutil.copytree(rules_src, tmp_path / raw["review"]["rules_dir"])
    return tmp_path


# ---- absent = byte-identical (the core acceptance for both blocks) ----------

def test_design_absent_is_none_and_unrendered(sample_cfg):
    assert sample_cfg.design is None
    # sentinel is the CURRENT rendered header — a stale one (a renamed "Design
    # gate" heading) would make this render-guard vacuously true.
    assert "Design decision records" not in explain_mod.render(sample_cfg)


def test_deploy_absent_is_empty_and_unrendered(sample_cfg):
    assert sample_cfg.deploy == {}
    assert "Deploy scopes" not in explain_mod.render(sample_cfg)


# ---- design: — which tiers require a decision record -----------------------

def test_design_block_parses_and_renders(sample_repo, tmp_path):
    def m(raw):
        raw["design"] = {"decision_required": ["HIGH"],
                         "charter": "name the alternatives weighed"}
    cfg = config_mod.load(_write(sample_repo, tmp_path, m))
    assert cfg.design.decision_required == ("HIGH",)
    assert "alternatives" in cfg.design.charter
    brief = explain_mod.render(cfg)
    assert "HIGH" in brief and "decision record" in brief.lower()
    # enforcement-truth: the render must NOT read as a mechanical
    # warden gate — it declares, the design-approval discipline binds it.
    assert "not enforcement" in brief.lower() or "does not block" in brief.lower()


def test_design_requires_the_decision_required_key(sample_repo, tmp_path):
    def m(raw):
        raw["design"] = {"charter": "x"}  # missing decision_required
    with pytest.raises(config_mod.ConfigError, match="decision_required"):
        config_mod.load(_write(sample_repo, tmp_path, m))


def test_design_rejects_a_bad_tier(sample_repo, tmp_path):
    def m(raw):
        raw["design"] = {"decision_required": ["CRITICAL"]}
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(_write(sample_repo, tmp_path, m))


# ---- deploy: — same shape as verify:, reuses run_scope --------------------

def test_deploy_block_parses_like_verify_and_renders(sample_repo, tmp_path):
    def m(raw):
        raw["deploy"] = {"staging": [{"run": "echo deploy"},
                                     {"run": "smoke", "cwd": "app"}]}
    cfg = config_mod.load(_write(sample_repo, tmp_path, m))
    assert [s.run for s in cfg.deploy["staging"]] == ["echo deploy", "smoke"]
    assert cfg.deploy["staging"][1].cwd == "app"
    brief = explain_mod.render(cfg)
    assert "Deploy scopes" in brief and "staging" in brief


def test_deploy_reuses_run_scope_unforked(sample_repo, tmp_path):
    """verify.run_scope must run a deploy scope directly, not a fork."""
    def m(raw):
        raw["deploy"] = {"ok": [{"run": "true"}, {"run": "echo hi"}]}
    cfg = config_mod.load(_write(sample_repo, tmp_path, m))
    doc = verify_mod.run_scope(cfg, "ok", which="deploy")
    assert doc["passed"] is True
    assert [r["exit_code"] for r in doc["results"]] == [0, 0]


def test_deploy_unknown_scope_names_deploy_not_verify(sample_repo, tmp_path):
    def m(raw):
        raw["deploy"] = {"staging": [{"run": "true"}]}
    cfg = config_mod.load(_write(sample_repo, tmp_path, m))
    with pytest.raises(verify_mod.VerifyError, match="deploy"):
        verify_mod.run_scope(cfg, "no-such", which="deploy")


def test_deploy_rejects_a_step_with_no_run(sample_repo, tmp_path):
    def m(raw):
        raw["deploy"] = {"bad": [{}]}  # step missing required `run`
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(_write(sample_repo, tmp_path, m))


def test_run_scope_rejects_an_unknown_map_name(sample_cfg):
    """Fail-closed: a bad `which` must raise a legible VerifyError,
    not getattr an unrelated attribute (config.review, config.root) and blow up
    with an opaque TypeError."""
    with pytest.raises(verify_mod.VerifyError, match="scope map"):
        verify_mod.run_scope(sample_cfg, "app", which="review")


def test_render_summary_labels_the_run_by_its_map(sample_repo, tmp_path):
    """Pattern-fit: render_summary must name a deploy run 'deploy', not the
    hardcoded 'verify', since run_scope serves both maps."""
    def m(raw):
        raw["deploy"] = {"staging": [{"run": "true"}]}
    cfg = config_mod.load(_write(sample_repo, tmp_path, m))
    doc = verify_mod.run_scope(cfg, "staging", which="deploy")
    assert verify_mod.render_summary(doc, which="deploy").startswith(
        "deploy --scope staging")
