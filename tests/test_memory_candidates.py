"""Candidate rules — the `unmapped:` namespace read as PROPOSALS.

Rendering declared rules and `unmapped:` ids in one undifferentiated list
would mix two different kinds of statement:

  rule lang-conventions:           n=3   wilson_lb=0.438   <- a rule that EXISTS
  rule unmapped:fail-open:         n=7   wilson_lb=0.646   <- a rule that DOES NOT

The first is a measurement of a rule's precision. The second is seven findings
reviewers kept making that no rule covers — a proposal, not a measurement. The
two get different sections, different vocabulary, and different bars.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import seed_rules

from warden import memory as memory_mod
from warden import rules as rules_mod


# One base, read once: `i` indexes the review ROUND (ts and sha both advance
# with it), so "three refutations running" means three rounds rather than three
# datetime.now() calls that happened to differ.
_BASE = datetime.now(timezone.utc)


def record(rid: str, i: int, *, rule: str, status: str = "fixed",
           days_old: int = 1) -> dict:
    ts = (_BASE - timedelta(days=days_old) + timedelta(minutes=i)).isoformat()
    return {"id": rid, "ts": ts, "seq": i, "sha": f"{i:040x}", "rule_id": rule,
            "tags": [], "dir_prefix": "src/api", "file": "src/api/x.py",
            "line": i, "severity": "MEDIUM", "finding": f"f{i}",
            "evidence": "e", "status": status, "origin": "day"}


def seed(root: Path, records: list[dict]) -> None:
    mem = root / ".warden" / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "findings.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records))


def many(rule: str, n: int, *, status: str = "fixed", start: int = 0) -> list[dict]:
    return [record(f"{rule}-{i}", i, rule=rule, status=status)
            for i in range(start, start + n)]


def candidate(doc: dict, slug: str) -> dict:
    for row in doc["candidates"]:
        if row["slug"] == slug:
            return row
    raise AssertionError(
        f"no candidate {slug!r} in {[c['slug'] for c in doc['candidates']]}")


# ---------- the split ------------------------------------------------------

def test_candidates_are_split_out_of_the_declared_rule_table(tmp_path):
    seed(tmp_path, many("lang-conventions", 3) + many("unmapped:fail-open", 7))
    doc = memory_mod.stats(tmp_path)
    assert set(doc["per_rule"]) == {"lang-conventions"}, \
        "a candidate is not a declared rule's precision row"
    assert [c["slug"] for c in doc["candidates"]] == ["fail-open"]
    assert candidate(doc, "fail-open")["rule_id"] == "unmapped:fail-open"


def test_the_render_separates_the_two_kinds_of_statement(tmp_path):
    seed(tmp_path, many("lang-conventions", 3) + many("unmapped:fail-open", 7))
    out = memory_mod.render_stats(memory_mod.stats(tmp_path))
    declared, _, candidates = out.partition("CANDIDATE RULES")
    assert candidates, "no candidate section rendered"
    assert "rule lang-conventions" in declared
    assert "fail-open" not in declared, \
        "a candidate must not appear in the declared-rule section"
    assert "PROPOSE RULE fail-open" in candidates


# ---------- candidate promotion semantics ----------------------------------

def test_a_candidate_is_never_promotable_to_a_deterministic_checker(tmp_path):
    """12 judged cases at 100% clears the checker gate on the numbers — but
    there is no rule to automate. A candidate's promotion is 'write this
    rule', a different action with a different bar."""
    seed(tmp_path, many("unmapped:docs-drift", 12))
    doc = memory_mod.stats(tmp_path)
    assert doc["promotable"] == []
    assert "unmapped:docs-drift" not in doc["per_rule"]
    assert doc["rule_proposals"] == ["docs-drift"]
    out = memory_mod.render_stats(doc)
    assert "guardrail holding" in out, "the checker gate still reports itself"


def test_the_candidate_bar_is_recurrence_not_the_checker_confidence_floor(tmp_path):
    """CANDIDATE_MIN_N judged cases, all upheld, sits BELOW the 0.7 checker
    floor — and is still a legitimate proposal to write the rule."""
    n = memory_mod.CANDIDATE_MIN_N
    seed(tmp_path, many("unmapped:fail-open", n))
    doc = memory_mod.stats(tmp_path)
    row = candidate(doc, "fail-open")
    assert row["wilson_lb"] < memory_mod.PROMOTE_WILSON_LB
    assert row["verdict"] == "propose"
    assert doc["rule_proposals"] == ["fail-open"]


def test_the_strongest_recurring_class_is_proposed_despite_one_refutation(tmp_path):
    """n=11, 10 upheld: the most recurrent class in the live corpus. Its
    Wilson bound (0.623) fails the checker floor — reusing that floor here
    would silence the corpus's loudest signal."""
    seed(tmp_path, many("unmapped:enforcement-claim", 10)
         + many("unmapped:enforcement-claim", 1, status="refuted", start=10))
    row = candidate(memory_mod.stats(tmp_path), "enforcement-claim")
    assert (row["n"], row["upheld"], row["refuted"]) == (11, 10, 1)
    assert row["wilson_lb"] < memory_mod.PROMOTE_WILSON_LB
    assert row["verdict"] == "propose"


def test_a_class_under_the_recurrence_bar_is_watched_not_proposed(tmp_path):
    seed(tmp_path, many("unmapped:diagnosability", memory_mod.CANDIDATE_MIN_N - 1))
    doc = memory_mod.stats(tmp_path)
    assert candidate(doc, "diagnosability")["verdict"] == "watch"
    assert doc["rule_proposals"] == []
    assert "watching" in memory_mod.render_stats(doc)


def test_a_class_refuted_more_often_than_upheld_is_not_proposed(tmp_path):
    """Recurrence alone would propose reviewer noise as policy."""
    seed(tmp_path, many("unmapped:false-positive", 2, status="refuted")
         + many("unmapped:false-positive", 1, start=2)
         + many("unmapped:false-positive", 2, status="dismissed-with-reason",
                start=3))
    doc = memory_mod.stats(tmp_path)
    row = candidate(doc, "false-positive")
    assert row["n"] == 5 and row["upheld"] == 1
    assert row["verdict"] == "noisy"
    assert doc["rule_proposals"] == []
    assert "too noisy to propose" in memory_mod.render_stats(doc)


def test_a_single_refuted_finding_is_watched_not_branded_noise(tmp_path):
    """Recurrence is tested BEFORE the rate. One judged case that was refuted
    is one data point; calling it a noisy class is the small-sample error the
    promotion floor exists to prevent, pointed the other way."""
    seed(tmp_path, many("unmapped:contradictory-remedy", 1, status="refuted"))
    doc = memory_mod.stats(tmp_path)
    assert candidate(doc, "contradictory-remedy")["verdict"] == "watch"
    assert "too noisy" not in memory_mod.render_stats(doc)


def test_candidates_rank_by_recurrence(tmp_path):
    seed(tmp_path, many("unmapped:fail-open", 7) + many("unmapped:docs-drift", 9)
         + many("unmapped:graph-fidelity", 1))
    doc = memory_mod.stats(tmp_path)
    assert [c["slug"] for c in doc["candidates"]] == \
        ["docs-drift", "fail-open", "graph-fidelity"]


# ---------- a candidate the ruleset has already answered --------------------

def test_a_candidate_whose_slug_names_a_declared_rule_is_covered(tmp_path):
    seed(tmp_path, many("unmapped:docs-drift", 9))
    d = seed_rules(tmp_path, "docs-drift")
    doc = memory_mod.stats(tmp_path, d)
    row = candidate(doc, "docs-drift")
    assert row["verdict"] == "covered" and row["covered_by"] == "docs-drift"
    assert doc["rule_proposals"] == [], \
        "proposing a rule that exists is not a proposal"
    assert "covered" in memory_mod.render_stats(doc)


def test_a_rule_may_declare_the_candidate_class_it_answers(tmp_path):
    """The loop closing: `docs-drift` recurred nine times, and the rule that
    shipped for it is named `wiki-fidelity`. Nothing links slug to rule id but
    a declaration, so the rule file makes it — and stats stops proposing a
    rule that already exists under another name."""
    seed(tmp_path, many("unmapped:docs-drift", 9))
    d = seed_rules(tmp_path, "wiki-fidelity")
    (d / "wiki-fidelity.md").write_text(
        (d / "wiki-fidelity.md").read_text().replace(
            "engine: claude", "engine: claude\ncovers: [docs-drift]"))
    doc = memory_mod.stats(tmp_path, d)
    assert candidate(doc, "docs-drift")["covered_by"] == "wiki-fidelity"
    assert doc["rule_proposals"] == []


def test_covers_entries_must_be_well_formed_slugs(tmp_path):
    d = seed_rules(tmp_path, "wiki-fidelity")
    (d / "wiki-fidelity.md").write_text(
        (d / "wiki-fidelity.md").read_text().replace(
            "engine: claude", "engine: claude\ncovers: [Docs Drift]"))
    with pytest.raises(rules_mod.RuleError, match="covers"):
        rules_mod.load_rules(d)


def test_two_rules_may_not_cover_the_same_candidate_class(tmp_path):
    d = seed_rules(tmp_path, "wiki-fidelity", "other-rule")
    for name in ("wiki-fidelity", "other-rule"):
        (d / f"{name}.md").write_text(
            (d / f"{name}.md").read_text().replace(
                "engine: claude", "engine: claude\ncovers: [docs-drift]"))
    with pytest.raises(rules_mod.RuleError, match="docs-drift"):
        rules_mod.load_rules(d)


# ---------- behaviours the split must not disturb ---------------------------

def test_the_caveat_still_prints_above_both_sections(tmp_path):
    seed(tmp_path, many("unmapped:fail-open", 7))
    out = memory_mod.render_stats(memory_mod.stats(tmp_path))
    assert out.index("precision only") < out.index("CANDIDATE RULES")


def test_gate_detections_never_become_candidates(tmp_path):
    """`status: detected` is a deterministic hit — a FACT, not a judgment. It
    cannot confirm a rule and it cannot propose one."""
    seed(tmp_path, many("unmapped:fail-open", 20, status="detected"))
    doc = memory_mod.stats(tmp_path)
    assert doc["candidates"] == [] and doc["rule_proposals"] == []
    assert doc["gate_detections"]["unmapped:fail-open"] == 20


def test_a_candidate_is_never_a_pause_candidate(tmp_path):
    """You cannot pause a rule that does not exist."""
    seed(tmp_path, many("unmapped:false-positive", 3, status="refuted"))
    doc = memory_mod.stats(tmp_path)
    assert doc["pause_candidates"] == []
    assert candidate(doc, "false-positive")["verdict"] == "noisy"


# ---------- floors, coverage and boundaries --------------------------------

def test_r1_the_floor_is_printed_on_every_candidate_the_report_calls_out(tmp_path):
    """The confidence floor prints on every CALLED-OUT candidate line; `watch`
    rows collapse into a roll-up with no floor at all. The docs' claim is that
    narrower one, and it is pinned instead of asserted in prose.
    """
    seed(tmp_path, many("unmapped:fail-open", 7)
         + many("unmapped:false-positive", 1)
         + many("unmapped:false-positive", 4, status="refuted", start=1)
         + many("unmapped:diagnosability", 1))
    out = memory_mod.render_stats(memory_mod.stats(tmp_path))
    called_out = [ln for ln in out.splitlines()
                  if ln.startswith(("  PROPOSE RULE ", "  covered ",
                                    "  too noisy to propose "))]
    assert len(called_out) == 2, called_out
    for line in called_out:
        assert "floor " in line, f"called-out candidate with no floor: {line}"
    watching = [ln for ln in out.splitlines() if ln.startswith("  watching ")]
    assert len(watching) == 1 and "floor" not in watching[0], \
        "the roll-up must not imply a floor it does not print"


def test_r1_a_covered_class_may_no_longer_be_filed_as_a_candidate(tmp_path):
    """'Once the rule is declared its findings are filed under the rule's own
    id' must hold through a `covers:` link too, not only when the rule id
    equals the slug. Accepting `unmapped:<slug>` there would let judgments
    accrue to a class instead of to the rule that answers it — the carry-over
    the design says cannot happen.
    """
    from warden import attest as attest_mod
    d = seed_rules(tmp_path, "wiki-fidelity")
    (d / "wiki-fidelity.md").write_text(
        (d / "wiki-fidelity.md").read_text().replace(
            "engine: claude", "engine: claude\ncovers: [docs-drift]"))
    finding = {"rule_id": "unmapped:docs-drift", "file": "docs/wiki/Memory.md"}
    with pytest.raises(attest_mod.AttestError, match="wiki-fidelity"):
        attest_mod._check_rule_ids([finding], d)
    # a class nothing covers is still perfectly legal
    attest_mod._check_rule_ids(
        [{"rule_id": "unmapped:fail-open", "file": "x.py"}], d)


def test_r1_a_candidate_refuted_three_times_running_is_not_proposed(tmp_path):
    """Candidates are off the pause list, and their refutations do not sink
    the upheld rate far enough to stop a proposal: 7 upheld then 3 straight
    refutations is rate 0.7. Without the streak in the verdict the class would
    be proposed with the streak reported nowhere.
    """
    seed(tmp_path, many("unmapped:streaky", 7)
         + many("unmapped:streaky", 3, status="refuted", start=7))
    doc = memory_mod.stats(tmp_path)
    row = candidate(doc, "streaky")
    assert row["rate"] > memory_mod.CANDIDATE_MIN_RATE, "rate alone would propose it"
    assert row["streak"] >= memory_mod.PAUSE_STREAK
    assert row["verdict"] == "noisy"
    assert doc["rule_proposals"] == []
    assert "refuted in 3 rounds running" in memory_mod.render_stats(doc)


def test_r1_an_unreadable_ruleset_is_reported_not_swallowed(tmp_path):
    """`_declared_rules` must not swallow exceptions: a broken ruleset that
    silently emptied `paused` would list a `paused: true` rule as promotable,
    the one outcome stats claims it prevents. A failure must be louder, not
    quieter.
    """
    d = seed_rules(tmp_path, "a-rule", "b-rule")
    for name in ("a-rule", "b-rule"):   # duplicate covers -> RuleError
        (d / f"{name}.md").write_text(
            (d / f"{name}.md").read_text().replace(
                "engine: claude", "engine: claude\ncovers: [shared-class]"))
    seed(tmp_path, many("a-rule", 12))
    doc = memory_mod.stats(tmp_path, d)
    assert doc["rules_error"], "a ruleset that would not load must be reported"
    assert doc["promotable"] == [], \
        "no promotion proposal off a ruleset we could not read"
    out = memory_mod.render_stats(doc)
    assert "RULESET UNREADABLE" in out and "shared-class" in out


def test_r1_covers_may_not_name_a_declared_rule(tmp_path):
    """The clash check compares covers entries with declared rule ids, not
    only with each other; otherwise `z-rule: covers: [b-rule]` loads and stats
    attributes the class to whichever rule file sorts last."""
    d = seed_rules(tmp_path, "b-rule", "z-rule")
    (d / "z-rule.md").write_text(
        (d / "z-rule.md").read_text().replace(
            "engine: claude", "engine: claude\ncovers: [b-rule]"))
    with pytest.raises(rules_mod.RuleError, match="b-rule"):
        rules_mod.load_rules(d)


def test_r1_exactly_half_upheld_is_not_a_majority(tmp_path):
    """The bar is 'more than 0.5', so this test sits on the boundary: `rate <=
    MIN` mutated to `rate <` flips a 2-of-4 class from noisy to PROPOSE RULE
    and must go red here."""
    seed(tmp_path, many("unmapped:halfway", 2)
         + many("unmapped:halfway", 2, status="refuted", start=2))
    doc = memory_mod.stats(tmp_path)
    assert candidate(doc, "halfway")["rate"] == memory_mod.CANDIDATE_MIN_RATE
    assert candidate(doc, "halfway")["verdict"] == "noisy"
    assert doc["rule_proposals"] == []


def test_r1_covers_accepts_the_prefixed_spelling_of_a_slug(tmp_path):
    """The `unmapped:` normalisation on covers entries is pinned: without it
    `covers: [unmapped:docs-drift]` silently covers nothing."""
    seed(tmp_path, many("unmapped:docs-drift", 9))
    d = seed_rules(tmp_path, "wiki-fidelity")
    (d / "wiki-fidelity.md").write_text(
        (d / "wiki-fidelity.md").read_text().replace(
            "engine: claude", "engine: claude\ncovers: [unmapped:docs-drift]"))
    assert rules_mod.load_rules(d)[0].covers == ("docs-drift",)
    doc = memory_mod.stats(tmp_path, d)
    assert candidate(doc, "docs-drift")["covered_by"] == "wiki-fidelity"
    assert doc["rule_proposals"] == []


def test_the_propose_rule_header_carries_the_recipe_not_just_the_slug(tmp_path):
    """A header reading only "The slug is the proposed rule name." leads a
    reader to write `.warden/rules/fail-open.md` with `id: fail-open`, and
    then every shard that ever filed under `unmapped:fail-open` becomes
    invalid the moment the rule exists and certification drops a level. That
    advice alone is the one move that breaks the corpus.

    The slug is still the CLASS the rule answers. What the header owes a
    reader is the rest of the recipe, at the point the mistake is made.
    """
    seed(tmp_path, many("unmapped:fail-open", 7))
    out = memory_mod.render_stats(memory_mod.stats(tmp_path))
    header = out.split("CANDIDATE RULES")[1].split("PROPOSE RULE")[0]
    assert "The slug is the proposed rule name." not in header, \
        "the bare instruction is the trap; it must not survive alone"
    assert "covers:" in header, "the rule id must differ from the slug"
    assert "certification.yaml" in header and "grandfather" in header.lower(), \
        f"the committed ids must be grandfathered, or certification drops: {header}"

    # the line shape the retro quotes verbatim is unchanged
    assert "  PROPOSE RULE fail-open: 7 judged" in out


def test_the_recipe_is_absent_when_nothing_is_proposed(tmp_path):
    """The recipe is guidance for an action, not boilerplate. A report with
    no PROPOSE RULE line asks nobody to write a rule, so printing the
    coordinated-change warning there would train readers to skip it."""
    seed(tmp_path, many("unmapped:fail-open", 2))
    out = memory_mod.render_stats(memory_mod.stats(tmp_path))
    assert "PROPOSE RULE" not in out
    assert "grandfathered_rule_ids" not in out


def one_round(rule: str, n: int, *, status: str, i: int) -> list[dict]:
    """n records filed by ONE review round: same ts, same sha, differing seq —
    the shape a single reviewer's findings list takes."""
    return [dict(record(f"{rule}-{i}-{j}", i, rule=rule, status=status), seq=j,
                 id=f"{rule}-{i}-{j}") for j in range(n)]


def test_a_class_one_round_just_refuted_three_times_is_not_proposed(tmp_path):
    """Counting the streak in ROUNDS is right for the pause TRIGGER and wrong
    for the SUPPRESSOR. `_candidate_rows` reads the streak to STOP proposing a
    class reviewers keep refuting, so a shorter streak un-blocks a proposal:
    seven upheld rounds then one round refuting a class three times would go
    from `noisy` to `propose` — the retro offering to write a rule about a
    class a round had just rejected three times over.

    Counting refutations WITHIN a round is order-independent, so the
    suppressor keeps its bar while the trigger stays strictly round-based.
    """
    seed(tmp_path, many("unmapped:streaky", 7)
         + one_round("unmapped:streaky", 3, status="refuted", i=8))
    s = memory_mod.stats(tmp_path)
    row = next(c for c in s["candidates"] if c["slug"] == "streaky")
    assert row["verdict"] == "noisy", \
        f"a class just refuted 3x in one round must not be proposed: {row}"
    assert "streaky" not in s["rule_proposals"]


def test_one_round_of_refutations_still_does_not_pause_the_rule(tmp_path):
    """The other half: the same corpus must
    NOT trigger an auto-pause, because one round is not a trend. The two bars
    are deliberately different — suppressing a proposal is cheap and
    reversible, silencing a declared rule mutates gate surface."""
    seed(tmp_path, many("streaky-rule", 7)
         + one_round("streaky-rule", 3, status="refuted", i=8))
    streaks = memory_mod.refutation_streaks(
        memory_mod.load_records(tmp_path))
    assert streaks.get("streaky-rule", 0) == 1, \
        f"one round is one step, never three: {streaks}"


def test_a_skill_file_one_round_refuted_three_times_is_not_proposed(tmp_path):
    """`skill_recurrence` counts refutations per round the same way. A skill
    fixture writing one record per round makes the per-record and per-round
    arithmetics indistinguishable; this fixture tells them apart, so a
    per-record loop there goes red.
    """
    f = "skills/nightgate-skills/skills/retro/SKILL.md"
    recs = [dict(record(f"{f}-{i}", i, rule="unmapped:x"), file=f)
            for i in range(7)]
    recs += [dict(r, file=f) for r in
             one_round("unmapped:x", 3, status="refuted", i=8)]
    rows = memory_mod.skill_recurrence(recs)
    row = next(r for r in rows if r["file"] == f)
    assert row["verdict"] == "noisy", \
        f"one round refuting a skill file 3x must not propose a change: {row}"


# ---------- a paused rule covers nothing ------------------------------------
#
# review.py skips a paused rule before it can produce a finding, so its
# `covers:` (and its own id) must not keep marking the class covered — the
# same fail-open the advisor closes for `implements:`, one declared link over.
# Otherwise, with wiki-fidelity paused, docs-drift would read as covered while
# nothing enforced it.


def _pause(d: Path, rule_id: str) -> None:
    p = d / f"{rule_id}.md"
    p.write_text(p.read_text().replace(
        "engine: claude",
        "engine: claude\npaused: true\n"
        "paused_reason: auto-paused on a 3-refutation streak"))


def test_a_class_whose_only_covering_rule_is_paused_is_not_covered(tmp_path):
    """The covers: link is coverage only
    while the rule is UNPAUSED — a paused rule enforces nothing, so the class
    returns to the candidate list instead of being suppressed forever."""
    seed(tmp_path, many("unmapped:docs-drift", 9))
    d = seed_rules(tmp_path, "wiki-fidelity")
    (d / "wiki-fidelity.md").write_text(
        (d / "wiki-fidelity.md").read_text().replace(
            "engine: claude", "engine: claude\ncovers: [docs-drift]"))
    _pause(d, "wiki-fidelity")
    doc = memory_mod.stats(tmp_path, d)
    row = candidate(doc, "docs-drift")
    assert row["verdict"] == "propose", \
        f"a paused rule's covers: must not mark the class covered: {row}"
    assert row["covered_by"] is None


def test_a_paused_rule_whose_id_is_the_slug_does_not_cover_it(tmp_path):
    """The identity link is the sibling coverage claim and pauses the same
    way: rule docs-drift paused means the class docs-drift is unenforced."""
    seed(tmp_path, many("unmapped:docs-drift", 9))
    d = seed_rules(tmp_path, "docs-drift")
    _pause(d, "docs-drift")
    doc = memory_mod.stats(tmp_path, d)
    row = candidate(doc, "docs-drift")
    assert row["verdict"] == "propose" and row["covered_by"] is None


def test_the_reproposed_row_names_the_paused_rule_not_a_bare_duplicate(tmp_path):
    """A re-proposed class may clear the
    proposal bar while a rule for it already EXISTS, paused. The row must say
    so — silently proposing a duplicate invites a second rule for a class one
    unpause would answer."""
    seed(tmp_path, many("unmapped:docs-drift", 9))
    d = seed_rules(tmp_path, "wiki-fidelity")
    (d / "wiki-fidelity.md").write_text(
        (d / "wiki-fidelity.md").read_text().replace(
            "engine: claude", "engine: claude\ncovers: [docs-drift]"))
    _pause(d, "wiki-fidelity")
    doc = memory_mod.stats(tmp_path, d)
    assert candidate(doc, "docs-drift")["paused_covered_by"] == "wiki-fidelity"
    out = memory_mod.render_stats(doc)
    line = next(ln for ln in out.splitlines()
                if ln.strip().startswith("PROPOSE RULE docs-drift"))
    assert "wiki-fidelity" in line and "PAUSED" in line


def test_an_unpaused_covering_rule_still_covers_with_no_pause_note(tmp_path):
    """The fix must not leak the annotation onto healthy coverage."""
    seed(tmp_path, many("unmapped:docs-drift", 9))
    d = seed_rules(tmp_path, "wiki-fidelity")
    (d / "wiki-fidelity.md").write_text(
        (d / "wiki-fidelity.md").read_text().replace(
            "engine: claude", "engine: claude\ncovers: [docs-drift]"))
    doc = memory_mod.stats(tmp_path, d)
    row = candidate(doc, "docs-drift")
    assert row["verdict"] == "covered"
    assert row["paused_covered_by"] is None
    assert "PAUSED" not in memory_mod.render_stats(doc)
