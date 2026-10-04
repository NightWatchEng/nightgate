"""Rule pause — the retro's proposal path reaching enforcement.

The retro proposes a pause after a refutation streak; a founder-merged PR
sets `paused: true` on the rule file. The rule then stops producing findings
but keeps its file, its history, and its place in rules_version — pausing is
cheap and reversible, and it is never silent.
"""

from pathlib import Path

import pytest

from warden import memory as memory_mod
from warden import rules as rules_mod

ACTIVE = """\
---
id: noisy-rule
severity: HIGH
engine: declarative
applies_to: ["src/**/*.py"]
checks:
  - id: c1
    pattern: 'TODO'
    message: "TODO left behind"
---
Body.
"""

PAUSED = ACTIVE.replace("engine: declarative",
                        "engine: declarative\npaused: true\n"
                        "paused_reason: \"3 straight refutations (retro 2026-08-21)\"")


def rules_dir(tmp_path: Path, body: str) -> Path:
    d = tmp_path / "rules"
    d.mkdir(exist_ok=True)
    (d / "noisy-rule.md").write_text(body)
    return d


def test_paused_rule_loads_with_its_reason(tmp_path):
    rule = rules_mod.load_rules(rules_dir(tmp_path, PAUSED))[0]
    assert rule.paused is True
    assert "refutations" in rule.paused_reason


def test_pause_without_reason_is_refused(tmp_path):
    body = ACTIVE.replace("engine: declarative", "engine: declarative\npaused: true")
    with pytest.raises(rules_mod.RuleError, match="requires a paused_reason"):
        rules_mod.load_rules(rules_dir(tmp_path, body))


def test_reason_without_pause_is_refused(tmp_path):
    body = ACTIVE.replace("engine: declarative",
                          'engine: declarative\npaused_reason: "why"')
    with pytest.raises(rules_mod.RuleError, match="without paused: true"):
        rules_mod.load_rules(rules_dir(tmp_path, body))


def test_pausing_moves_the_gate_cohort(tmp_path):
    """Pausing IS a policy change — it must change rules_version."""
    d = rules_dir(tmp_path, ACTIVE)
    before = rules_mod.rules_version(d, tmp_path)
    (d / "noisy-rule.md").write_text(PAUSED)
    assert rules_mod.rules_version(d, tmp_path) != before


def test_paused_rule_produces_no_findings_but_is_recorded(tmp_path, monkeypatch):
    from warden import review as review_mod
    from warden.diffs import DiffContext

    ctx = DiffContext(base="a" * 40, head="b" * 40, files=("src/a.py",),
                      added={"src/a.py": ((1, "x = 1  # TODO"),)}, removed={},
                      read_base=lambda f: None, read_head=lambda f: None)

    class Cfg:
        root = tmp_path

        class review:
            rules_dir = "rules"
            blocking_severities = ["HIGH"]
            context_excludes = []

    active = rules_mod.load_rules(rules_dir(tmp_path, ACTIVE))
    doc = review_mod.run_review(Cfg, active, ctx, "v1")
    assert len(doc["findings"]) == 1 and doc["paused"] == []

    paused = rules_mod.load_rules(rules_dir(tmp_path, PAUSED))
    doc = review_mod.run_review(Cfg, paused, ctx, "v1")
    assert doc["findings"] == []
    assert doc["paused"] == ["noisy-rule"], "a pause must never be silent"


def test_sticky_comment_surfaces_paused_rules():
    from warden import audit as audit_mod
    doc = {"rules_version": "v1", "engine": "e", "base_sha": "a" * 40,
           "head_sha": "b" * 40, "findings": [], "deferred_to_pre_pr": [],
           "paused": ["noisy-rule"]}
    assert "PAUSED" in audit_mod.render(doc, {"tests": None})
    assert "noisy-rule" in audit_mod.render(doc, {"tests": None})


def test_stats_never_promotes_or_re_pauses_an_already_paused_rule(tmp_path):
    d = rules_dir(tmp_path, PAUSED)
    memory_dir = tmp_path / ".warden" / "memory"
    memory_dir.mkdir(parents=True)
    import json
    # Twelve DISTINCT findings, not one text repeated twelve times: the
    # restatement fold counts a finding once however many rounds restate it,
    # so twelve copies of "f" collapse to n=1 — below the promotion bar for
    # any rule — and the assertion below would hold with the pause guard
    # deleted. The fixture has to clear the bar on its own for "a paused rule
    # must not be promoted" to be about the pause.
    rows = [{"id": f"i{i}", "ts": f"2026-08-2{i%9}T00:00:00+00:00", "seq": i,
             "sha": "s", "rule_id": "noisy-rule", "tags": [], "dir_prefix": "src",
             "file": "src/a.py", "severity": "HIGH", "finding": f"finding {i}",
             "evidence": "e", "status": "confirmed", "origin": "day"}
            for i in range(12)]
    (memory_dir / "findings.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))

    doc = memory_mod.stats(tmp_path, d)
    assert doc["paused"] == ["noisy-rule"]
    # The guard the next line tests is only load-bearing while the row it
    # guards would otherwise promote: n over the bar AND the floor over it.
    row = doc["per_rule"]["noisy-rule"]
    assert row["n"] == 12 and row["wilson_lb"] >= memory_mod.PROMOTE_WILSON_LB
    assert doc["promotable"] == [], "a paused rule must not be promoted"
    rendered = memory_mod.render_stats(doc)
    assert "PAUSED (not enforced): noisy-rule" in rendered
