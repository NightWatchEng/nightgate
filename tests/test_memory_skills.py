"""Skill recurrence — the craft-layer half of the proposal loop.

Judged findings about skills and the policy contract need a proposal type
that can act on them: the loop that turns recurrence-in-the-corpus into a
proposal serves rules, and the protocols that govern how every change is
judged need it too. `skill_recurrence` is that grouping — judged findings
attributed to a SKILL FILE, ranked as proposals at the candidate bar (3+
judged, more upheld than not), never on a single finding, because an
unevidenced edit to a protocol is more expensive than an unevidenced rule and
cannot be measured by precision the way a rule can.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from warden import memory as memory_mod


# Derived from ONE base, never re-read per record: `i` indexes the review
# ROUND (ts and sha both advance with it), so "three times running" means
# three rounds and cannot depend on two datetime.now() calls disagreeing.
_BASE = datetime.now(timezone.utc) - timedelta(days=30)


def _rec(i: int, *, file: str, status: str = "fixed") -> dict:
    ts = (_BASE + timedelta(minutes=i)).isoformat()
    return {"id": f"r{i}", "ts": ts, "seq": i, "sha": f"{i:040x}",
            "rule_id": "policy-contract", "tags": ["policy-contract"],
            "dir_prefix": "/".join(Path(file).parent.parts[:2]),
            "file": file, "line": i, "severity": "MEDIUM",
            "finding": f"f{i}", "evidence": "e", "status": status,
            "origin": "day"}


RETRO = "skills/nightgate-skills/skills/retro/SKILL.md"
DELIVER = "skills/nightgate-skills/skills/deliver/SKILL.md"
POLICY = ".warden/skills-policy.md"
CODE = "warden/memory.py"


def _rows(records):
    return {r["file"]: r for r in memory_mod.skill_recurrence(records)}


def test_only_skill_and_policy_files_are_attributed():
    records = ([_rec(i, file=RETRO) for i in range(3)]
               + [_rec(i, file=CODE) for i in range(10, 14)]
               + [_rec(i, file="warden/cli.py") for i in range(20, 24)])
    rows = _rows(records)
    assert RETRO in rows, "a skill-file finding was not attributed"
    assert CODE not in rows and "warden/cli.py" not in rows, (
        "a code-file finding leaked into skill recurrence — the craft layer "
        "is skills and the policy contract, not warden/")


def test_the_policy_contract_is_attributed():
    rows = _rows([_rec(i, file=POLICY) for i in range(3)])
    assert POLICY in rows, (
        "the policy contract is the protocol every consumer runs under — its "
        "findings must reach the skill-change proposal path")


def test_the_candidate_bar_refuses_a_single_finding():
    rows = _rows([_rec(0, file=RETRO)])
    assert rows[RETRO]["verdict"] == "watch", (
        "one finding proposed a skill edit — the bead's explicit failure "
        "mode; a protocol edit from a single finding is unevidenced")


def test_three_upheld_findings_clear_the_bar():
    rows = _rows([_rec(i, file=RETRO) for i in range(3)])
    assert rows[RETRO]["verdict"] == "propose"
    assert rows[RETRO]["n"] == 3 and rows[RETRO]["upheld"] == 3


def test_mostly_refuted_findings_do_not_propose():
    records = ([_rec(0, file=RETRO, status="fixed")]
               + [_rec(i, file=RETRO, status="refuted") for i in range(1, 4)])
    assert _rows(records)[RETRO]["verdict"] == "noisy", (
        "a skill file reviewers keep clearing must not be proposed for a "
        "change — more upheld than not is the bar")


def test_an_even_split_does_not_propose():
    """The boundary CANDIDATE_MIN_RATE draws: 'more upheld than not' is
    STRICTLY greater, so rate exactly 0.5 is noisy, not propose.
    Interleave so no refutation streak reaches 3 — the rate alone must
    decide this case. Without this, `rate <= 0.5` could silently become
    `rate < 0.5` and a tied protocol would be proposed."""
    records = []
    for i in range(3):
        records.append(_rec(i * 2, file=RETRO, status="fixed"))
        records.append(_rec(i * 2 + 1, file=RETRO, status="refuted"))
    row = _rows(records)[RETRO]
    assert row["n"] == 6 and row["rate"] == 0.5
    assert row["verdict"] == "noisy", (
        "a tied skill file (rate exactly 0.5) was proposed — the "
        "'strictly greater' bar was softened")


def test_a_refutation_streak_suppresses_a_strong_history():
    """The streak is a SEPARATE bar (parity with _candidate_rows): a
    protocol with a strong lifetime rate but three fresh refutations must not
    be proposed — reviewers have just cleared it three times running. Six
    upheld then three refuted: rate 0.667 clears the rate bar, the streak
    stops it."""
    records = [_rec(i, file=RETRO, status="fixed") for i in range(6)]
    records += [_rec(10 + i, file=RETRO, status="refuted") for i in range(3)]
    row = _rows(records)[RETRO]
    assert row["rate"] > 0.5 and row["streak"] == 3
    assert row["verdict"] == "noisy", (
        "a skill file with three fresh refutations was proposed on its old "
        "rate — the streak bar the rule path carries is missing here")


def test_unjudged_findings_do_not_count_toward_the_bar():
    records = ([_rec(i, file=RETRO, status="detected") for i in range(5)]
               + [_rec(i, file=RETRO, status="fixed") for i in range(10, 12)])
    row = _rows(records)[RETRO]
    assert row["n"] == 2 and row["verdict"] == "watch", (
        "unjudged detections inflated the judged count — the same "
        "distinction the promotion gate draws")


def test_rows_rank_by_recurrence():
    records = ([_rec(i, file=RETRO) for i in range(3)]
               + [_rec(i, file=DELIVER) for i in range(10, 15)])
    ordered = [r["file"] for r in memory_mod.skill_recurrence(records)]
    assert ordered[0] == DELIVER, "the most-recurrent skill file must rank first"


def test_empty_corpus_yields_no_rows():
    assert memory_mod.skill_recurrence([]) == []


def test_render_marks_a_recurring_skill_as_a_proposal():
    doc = {"total_records": 3, "per_rule": {}, "per_tag": {}, "promotable": [],
           "pause_candidates": [], "paused": [], "candidates": [],
           "review_events": {}, "corpus_age": {},
           "skill_recurrence": memory_mod.skill_recurrence(
               [_rec(i, file=RETRO) for i in range(3)])}
    out = memory_mod.render_stats(doc)
    assert "SKILL RECURRENCE" in out
    assert f"PROPOSE SKILL CHANGE {RETRO}" in out


def test_render_omits_the_section_when_no_skill_findings():
    doc = {"total_records": 0, "per_rule": {}, "per_tag": {}, "promotable": [],
           "pause_candidates": [], "paused": [], "candidates": [],
           "review_events": {}, "corpus_age": {}, "skill_recurrence": []}
    assert "SKILL RECURRENCE" not in memory_mod.render_stats(doc)


def test_stats_carries_skill_recurrence_and_a_propose_only_list(tmp_path):
    """The wiring, not just the function: stats() must put skill_recurrence
    into the doc render reads, and skill_proposals must list ONLY the
    propose-verdict files. Deleting either from stats() must turn this red."""
    mem = tmp_path / ".warden" / "memory"
    mem.mkdir(parents=True)
    records = ([_rec(i, file=RETRO) for i in range(3)]        # propose
               + [_rec(i, file=DELIVER) for i in range(10, 11)])  # watch (1)
    (mem / "findings.jsonl").write_text(
        "".join(__import__("json").dumps(r) + "\n" for r in records))
    doc = memory_mod.stats(tmp_path)
    files = {r["file"]: r for r in doc["skill_recurrence"]}
    assert files[RETRO]["verdict"] == "propose"
    assert files[DELIVER]["verdict"] == "watch"
    assert doc["skill_proposals"] == [RETRO], (
        "skill_proposals must be exactly the propose-verdict files — a watch "
        "or noisy file leaking in is a false proposal to the founder")
    assert "SKILL RECURRENCE" in memory_mod.render_stats(doc), (
        "the section stats() feeds does not reach the render")


def test_render_names_the_streak_reason_not_a_false_rate_claim():
    """The noisy render must not say 'more cleared than upheld' for a
    streak-suppressed file — it has more upheld, and the streak branch must
    not print a false reason."""
    records = ([_rec(i, file=RETRO, status="fixed") for i in range(6)]
               + [_rec(10 + i, file=RETRO, status="refuted") for i in range(3)])
    doc = {"total_records": 9, "per_rule": {}, "per_tag": {}, "promotable": [],
           "pause_candidates": [], "paused": [], "candidates": [],
           "review_events": {}, "corpus_age": {},
           "skill_recurrence": memory_mod.skill_recurrence(records)}
    out = memory_mod.render_stats(doc)
    assert "refuted in 3 rounds running" in out
    assert "more cleared than upheld" not in out
