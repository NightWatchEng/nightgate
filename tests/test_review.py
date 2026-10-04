"""Deterministic review gate: routing, severity authority, registry, exit logic."""

from pathlib import Path

import pytest

from warden import config as config_mod
from warden import review as review_mod
from warden.diffs import DiffContext
from warden.rules import Rule


def _rule(rid: str, severity: str = "HIGH", engine: str = "python",
          applies_to: tuple = ("**",), params: dict | None = None,
          excludes: tuple = ()) -> Rule:
    return Rule(id=rid, severity=severity, engine=engine, applies_to=applies_to,
                body="x", path=Path(f"{rid}.md"), params=params, excludes=excludes)


def _ctx(files: dict[str, list[tuple[int, str]]]) -> DiffContext:
    return DiffContext(base="a" * 40, head="b" * 40, files=tuple(files),
                       added={n: tuple(v) for n, v in files.items()},
                       removed={}, read_base=lambda p: None, read_head=lambda p: None)


SECRET_LINE = 'KEY = "sk-ant-api03-fakefakefake"'


def test_python_rule_finding_is_blocking(sample_cfg):
    rules = (_rule("secrets-in-diff"),)
    doc = review_mod.run_review(sample_cfg, rules, _ctx({"w/a.py": [(1, SECRET_LINE)]}), "v1")
    assert doc["engine"] == "deterministic"
    (f,) = doc["findings"]
    assert f["severity"] == "HIGH" and f["rule_id"] == "secrets-in-diff"
    assert review_mod.blocking(sample_cfg, doc) == doc["findings"]


def test_severity_stamped_from_rule_file(sample_cfg):
    """The checker returns no severity; the rule file is the only source."""
    rules = (_rule("secrets-in-diff", severity="LOW"),)
    doc = review_mod.run_review(sample_cfg, rules, _ctx({"w/a.py": [(1, SECRET_LINE)]}), "v1")
    assert doc["findings"][0]["severity"] == "LOW"
    assert review_mod.blocking(sample_cfg, doc) == []


def test_claude_rules_deferred_not_run(sample_cfg):
    rules = (_rule("scope-creep", severity="MEDIUM", engine="claude"),
             _rule("secrets-in-diff"))
    doc = review_mod.run_review(sample_cfg, rules, _ctx({"notes/x.md": [(1, "text")]}), "v1")
    assert doc["deferred_to_pre_pr"] == ["scope-creep"]
    assert doc["findings"] == []


def test_unmatched_rules_neither_run_nor_deferred(sample_cfg):
    rules = (_rule("migration-safety", applies_to=("db/migrations/**",)),
             _rule("scope-creep", engine="claude", applies_to=("app/**",)))
    doc = review_mod.run_review(sample_cfg, rules, _ctx({"notes/x.md": [(1, "t")]}), "v1")
    assert doc["findings"] == [] and doc["deferred_to_pre_pr"] == []


def test_python_rule_without_checker_fails_closed(sample_cfg):
    rules = (_rule("brand-new-rule"),)
    with pytest.raises(config_mod.ConfigError, match="no checker"):
        review_mod.run_review(sample_cfg, rules, _ctx({"a.py": [(1, "x")]}), "v1")


def test_registry_covers_all_sample_python_rules(sample_repo):
    """Every engine:python rule in the fixture ruleset has a checker — a rule
    that silently never runs must be impossible."""
    from warden import rules as rules_mod
    from warden.plugins import load_registry
    rules_dir = sample_repo / ".warden" / "rules"
    rules = rules_mod.load_rules(rules_dir)
    review_mod.validate_registry(rules, load_registry(rules_dir))  # raises on gap
    python_ids = {r.id for r in rules if r.engine == "python"}
    assert python_ids == {"secrets-in-diff", "prompt-eval-gate", "baseline-ratchet",
                          "contract-freeze", "migration-safety"}
    claude_ids = {r.id for r in rules if r.engine == "claude"}
    assert claude_ids == {"scope-creep", "tests-required"}


def test_artifact_is_schema_valid_and_deterministic(sample_cfg):
    rules = (_rule("secrets-in-diff"), _rule("tests-required", severity="LOW",
                                             engine="claude"))
    ctx = _ctx({"w/a.py": [(1, SECRET_LINE)]})
    doc1 = review_mod.run_review(sample_cfg, rules, ctx, "v1")
    doc2 = review_mod.run_review(sample_cfg, rules, ctx, "v1")
    assert doc1 == doc2  # same input, same artifact — no model in the loop


def test_g2_1_2_excludes_hiding_params_fail_closed(sample_cfg):
    """review G2-1/2: a global context_exclude that hides a checker-params file
    must be a load-time ConfigError, never a silent false positive."""
    import dataclasses
    bad_review = dataclasses.replace(
        sample_cfg.review, context_excludes=("tests/fixtures/**",))
    bad_cfg = dataclasses.replace(sample_cfg, review=bad_review)
    rule = _rule("contract-freeze", applies_to=("schemas/**",),
                 params={"schema_file": "schemas/api.schema.json",
                         "fixture_files": ["tests/fixtures/sample_payload.json"]})
    with pytest.raises(config_mod.ConfigError, match="hides"):
        review_mod.run_review(bad_cfg, (rule,), _ctx({"schemas/x.json": [(1, "{}")]}),
                              "v1")


def test_companion_prefix_param_is_exempt_from_the_excludes_check(sample_cfg):
    """A companion_prefix param is matched against
    ctx.all_files (every changed path, pre-exclude), NOT read from the scanned
    context — so context_excludes hiding tests/ does NOT break the checker and
    must NOT raise, unlike a real read-path param (test above)."""
    import dataclasses
    bad_review = dataclasses.replace(
        sample_cfg.review, context_excludes=("tests/**",))
    bad_cfg = dataclasses.replace(sample_cfg, review=bad_review)
    rule = _rule("tests-accompany-logic", applies_to=("warden/**",),
                 params={"companion_prefix": "tests/", "exempt_globs": []})
    # must NOT raise despite tests/** being context-excluded
    review_mod.run_review(bad_cfg, (rule,),
                          _ctx({"warden/x.py": [(1, "MAX = 1")]}), "v1")


def test_g2_11_per_rule_excludes_do_not_blind_other_rules():
    from warden import rules as rules_mod
    r_secrets = _rule("secrets-in-diff", excludes=("tests/fixtures/**",))
    r_other = _rule("scope-creep", engine="claude")
    hits = rules_mod.applicable((r_secrets, r_other),
                                ["tests/fixtures/test_x.py"])
    assert "secrets-in-diff" not in hits
    assert hits["scope-creep"] == ["tests/fixtures/test_x.py"]
