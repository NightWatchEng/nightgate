"""Auto-pause — the safe half of the autonomy ladder.

warden.memory computes a streak of three refuting review ROUNDS (rounds, never
records; see the streak section), and warden.rules/review read + honor a
paused rule. These tests pin the ACTION — code that actually sets `paused:
true` with a recorded reason and writes the pause backtest artifact S-05
binds — and prove the loop closes: a paused rule then produces no findings.

Pausing is the ladder's strongest machine-tier candidate precisely because it
only ever makes the gate QUIETER and is reversible by one commit — so the test
that matters most is that nothing here can pause a rule the corpus has NOT
refuted three times running.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import seed_rules

from warden import autonomy as autonomy_mod
from warden import certify as certify_mod
from warden import rules as rules_mod
from private_evidence import needs_design_records


# One fixed base, 30 days back (inside FRESH_DAYS): every ts below is derived
# from it rather than read off the wall clock per record. Two `datetime.now()`
# calls landing on the same microsecond — or not — would decide whether a
# fixture was one review round or several, which is the exact distinction
# these tests exist to pin.
_BASE = datetime.now(timezone.utc) - timedelta(days=30)


def _record(i: int, *, rule: str, status: str) -> dict:
    # ts AND sha both advance with i: one review event writes one shard, so a
    # distinct i here is a distinct review round.
    return {"id": f"{rule}-{i}", "ts": (_BASE + timedelta(minutes=i)).isoformat(),
            "seq": i, "sha": f"{i:040x}",
            "rule_id": rule, "tags": [], "dir_prefix": "src", "file": "src/x.py",
            "line": i, "severity": "MEDIUM", "finding": f"f{i}",
            "evidence": "e", "status": status, "origin": "day"}


def _streak(rule: str, statuses: list[str]) -> list[dict]:
    """One judgment per review ROUND: "refuted three times running" means three
    separate rounds, which is what a streak has always claimed to measure."""
    return [_record(i, rule=rule, status=s) for i, s in enumerate(statuses)]


def _corpus(root: Path, rule_ids, records: list[dict]) -> list[dict]:
    """Declare `rule_ids` and return the records to INJECT into
    pause_candidates. The mechanism reads committed shards in production;
    injecting the records keeps these unit tests off the shard-writing path
    while exercising the exact same code."""
    seed_rules(root, *rule_ids)
    return records


# ---------- detection: only a real streak, nothing else --------------------

def test_pause_candidates_flags_a_three_refutation_streak(tmp_path):
    recs = _corpus(tmp_path, ["noisy"], _streak("noisy", ["refuted"] * 3))
    actions = autonomy_mod.pause_candidates(tmp_path, records=recs)
    assert [a.rule_id for a in actions] == ["noisy"]
    a = actions[0]
    assert a.streak >= 3 and a.refuted == 3 and a.judged == 3


def test_two_refutations_are_not_enough(tmp_path):
    recs = _corpus(tmp_path, ["noisy"], _streak("noisy", ["refuted"] * 2))
    assert autonomy_mod.pause_candidates(tmp_path, records=recs) == []


def test_a_non_refutation_resets_the_streak(tmp_path):
    # refuted, refuted, confirmed, refuted -> longest tail streak is 1.
    recs = _corpus(tmp_path, ["noisy"],
            _streak("noisy", ["refuted", "refuted", "confirmed", "refuted"]))
    assert autonomy_mod.pause_candidates(tmp_path, records=recs) == []


def test_an_already_paused_rule_is_not_re_flagged(tmp_path):
    recs = _corpus(tmp_path, [], _streak("noisy", ["refuted"] * 3))
    # declare the rule already paused
    rules_dir = tmp_path / ".warden" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "noisy.md").write_text(
        '---\nid: noisy\nseverity: MEDIUM\nengine: claude\n'
        'applies_to: ["**"]\npaused: true\n'
        'paused_reason: "already off"\n---\nbody\n')
    assert autonomy_mod.pause_candidates(tmp_path, records=recs) == []


# ---------- the action: set the field, record the reason -------------------

def test_apply_pause_sets_the_field_and_a_reason_that_cites_the_streak(tmp_path):
    recs = _corpus(tmp_path, ["noisy"], _streak("noisy", ["refuted"] * 3))
    (action,) = autonomy_mod.pause_candidates(tmp_path, records=recs)
    autonomy_mod.apply_pause(tmp_path, action, today="2026-08-29")
    rule = rules_mod.load_rules(tmp_path / ".warden" / "rules")[0]
    assert rule.paused is True
    assert rule.paused_reason.strip()
    assert "streak" in rule.paused_reason.lower()
    assert ".warden/memory/backtests/" in rule.paused_reason, (
        "the reason no longer says where the pause's evidence is")


def test_apply_pause_writes_a_backtest_stamped_with_the_new_rules_version(tmp_path):
    recs = _corpus(tmp_path, ["noisy"], _streak("noisy", ["refuted"] * 3))
    (action,) = autonomy_mod.pause_candidates(tmp_path, records=recs)
    _, artifact = autonomy_mod.apply_pause(tmp_path, action, today="2026-08-29")
    doc = json.loads(Path(artifact).read_text())
    assert doc["action"] == "pause"
    assert doc["rule_id"] == "noisy"
    assert isinstance(doc["judged"], int) and doc["judged"] == 3
    # The version S-05 binds to is the one AFTER the pause edit perturbs it.
    rv_now = rules_mod.rules_version(tmp_path / ".warden" / "rules", tmp_path)
    assert doc["rules_version"] == rv_now


def test_the_auto_paused_rule_then_produces_no_findings(tmp_path):
    """The loop closes end to end: a DECLARATIVE rule that fires, auto-paused
    by this mechanism, is skipped by the real gate — no findings, and the pause
    is recorded (never silent). Uses a firing rule on purpose; an engine:claude
    rule defers in run_review and would prove nothing about the skip."""
    from warden import review as review_mod
    from warden.diffs import DiffContext

    rules_dir = tmp_path / ".warden" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "noisy.md").write_text(
        "---\nid: noisy\nseverity: MEDIUM\nengine: declarative\n"
        'applies_to: ["src/**/*.py"]\n'
        "checks:\n  - id: c1\n    pattern: 'TODO'\n    message: \"TODO\"\n"
        "---\nBody.\n")
    recs = _streak("noisy", ["refuted"] * 3)

    (action,) = autonomy_mod.pause_candidates(tmp_path, records=recs)
    autonomy_mod.apply_pause(tmp_path, action, today="2026-08-29")

    ctx = DiffContext(base="a" * 40, head="b" * 40, files=("src/a.py",),
                      added={"src/a.py": ((1, "x = 1  # TODO"),)}, removed={},
                      read_base=lambda f: None, read_head=lambda f: None)

    class Cfg:
        root = tmp_path

        class review:
            rules_dir = ".warden/rules"
            blocking_severities = ["HIGH"]
            context_excludes = []

    paused = rules_mod.load_rules(rules_dir)
    doc = review_mod.run_review(Cfg, paused, ctx, "v1")
    assert doc["findings"] == [], "the auto-paused rule still fired"
    assert doc["paused"] == ["noisy"], "the pause was silent — it must be recorded"


def test_the_pause_backtest_satisfies_s05(tmp_path):
    """What we write must be exactly what the certification ladder demands —
    S-05 (rule_change_backtested) accepts a pause backtest at the current
    rules_version. If this ever goes red, the artifact drifted from the gate."""
    recs = _corpus(tmp_path, ["noisy"], _streak("noisy", ["refuted"] * 3))
    (action,) = autonomy_mod.pause_candidates(tmp_path, records=recs)
    autonomy_mod.apply_pause(tmp_path, action, today="2026-08-29")
    ok, detail = certify_mod._run_check(
        {"type": "rule_change_backtested"}, tmp_path)
    assert ok, f"S-05 rejected the auto-pause backtest: {detail}"


def test_apply_pause_handles_a_bom_prefixed_rule_file(tmp_path):
    """Encoding parity: the loader reads rule files
    utf-8-sig, so a BOM-prefixed file loads and can become a pause candidate.
    The writer must read the same encoding, or it rejects the frontmatter it
    was just handed as 'no YAML frontmatter'."""
    rules_dir = tmp_path / ".warden" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "noisy.md").write_bytes(
        b"\xef\xbb\xbf"  # UTF-8 BOM
        b'---\nid: noisy\nseverity: MEDIUM\nengine: claude\n'
        b'applies_to: ["**"]\n---\nbody\n')
    recs = _streak("noisy", ["refuted"] * 3)
    (action,) = autonomy_mod.pause_candidates(tmp_path, records=recs)
    autonomy_mod.apply_pause(tmp_path, action, today="2026-08-29")  # must not raise
    assert rules_mod.load_rules(rules_dir)[0].paused is True


def test_apply_pause_refuses_a_rule_that_is_already_paused(tmp_path):
    recs = _corpus(tmp_path, ["noisy"], _streak("noisy", ["refuted"] * 3))
    (action,) = autonomy_mod.pause_candidates(tmp_path, records=recs)
    autonomy_mod.apply_pause(tmp_path, action, today="2026-08-29")
    # a second apply of the same action is refused, not a silent double-write
    with pytest.raises(autonomy_mod.AutonomyError):
        autonomy_mod.apply_pause(tmp_path, action, today="2026-08-29")


# ---------- a streak is a TREND ACROSS ROUNDS -------------------------------
#
# A streak counted over RECORDS ordered by (ts, sha, seq) is not a trend.
# Every record written by one review round shares that round's ts (to the
# second) and sha, so within a round the order is `seq` alone — the order the
# reviewer happened to write its findings in. One round that filed 15 upheld
# findings and then 5 refutations would read as "5 consecutive refutations",
# one `warden autonomy pause` away from silencing a high-yield rule, and
# permuting seq inside that single round would move the streak between 0 and
# 5 over an unchanged corpus.
#
# A round contributes ONE step: it extends the streak only if every judged
# record it holds for that rule is a refutation, and any upheld or dismissed
# judgment in the round resets it. PAUSE_STREAK is therefore three review
# ROUNDS in a row — a trend the corpus can substantiate.

_UPHELD_STATUS = "fixed"


def _rounds(rule: str, rounds: list[list[str]]) -> list[dict]:
    """One list of statuses per review ROUND. Every record in a round shares
    that round's ts and sha and differs only in `seq` — exactly the shape a
    committed shard has."""
    # `_BASE` again, offset a day clear of `_streak`'s rounds so the two
    # helpers can never collide on a round key if a test mixes them.
    base = _BASE + timedelta(days=1)
    out: list[dict] = []
    for n, statuses in enumerate(rounds):
        ts = (base + timedelta(minutes=n)).isoformat()
        for seq, status in enumerate(statuses):
            r = _record(seq, rule=rule, status=status)
            r["ts"] = ts
            r["sha"] = f"{n:040x}"
            r["id"] = f"{rule}-{n}-{seq}"
            # One text per judgment: the same text in a later round is that
            # judgment RESTATED and folds to one, and a streak
            # of restatements is one round's opinion, not three
            r["finding"] = f"f{n}.{seq}"
            out.append(r)
    return out


def _streak_of(rule: str, records: list[dict]) -> int:
    from warden import memory as memory_mod
    return memory_mod.refutation_streaks(records).get(rule, 0)


@pytest.mark.parametrize("upheld,refuted", [(1, 3), (2, 4), (15, 5), (3, 9)])
def test_one_round_filing_k_refutations_after_upheld_is_not_a_streak_of_k(
        tmp_path, upheld, refuted):
    """Acceptance, over the dimension a per-record count scales with: the
    number of refutations the single round files last. Counting records would
    return exactly `refuted` for every row here."""
    recs = _corpus(tmp_path, ["noisy"],
                   _rounds("noisy", [[_UPHELD_STATUS] * upheld
                                     + ["refuted"] * refuted]))
    assert sum(1 for r in recs if r["status"] == "refuted") == refuted, \
        "fixture invalid: the round does not file the refutations it claims"
    assert _streak_of("noisy", recs) == 0, (
        "one round that upheld the rule is not a refutation streak, whatever "
        "order it wrote its findings in")
    assert autonomy_mod.pause_candidates(tmp_path, records=recs) == []


@pytest.mark.parametrize("refuted", [3, 5, 9])
def test_a_single_round_of_only_refutations_counts_once(tmp_path, refuted):
    """Even with no upheld finding to reset it, ONE round is one step. Three
    refutations filed in a single round is a round, not a trend."""
    recs = _corpus(tmp_path, ["noisy"], _rounds("noisy", [["refuted"] * refuted]))
    assert _streak_of("noisy", recs) == 1
    assert autonomy_mod.pause_candidates(tmp_path, records=recs) == []


@pytest.mark.parametrize("upheld,refuted", [(1, 3), (2, 3)])
def test_permuting_seq_within_a_round_cannot_change_the_streak(
        tmp_path, upheld, refuted):
    """Exhaustive over every distinct arrangement of the round's findings —
    no shuffling, no clock, nothing that could pass by luck. Counted over
    records, these arrangements would span 0..refuted."""
    import itertools

    statuses = [_UPHELD_STATUS] * upheld + ["refuted"] * refuted
    arrangements = sorted(set(itertools.permutations(statuses)))
    assert len(arrangements) > 1, "fixture invalid: nothing to permute"
    seen = set()
    for arrangement in arrangements:
        recs = _rounds("noisy", [list(arrangement)])
        seen.add(_streak_of("noisy", recs))
    assert seen == {0}, (
        f"the streak depends on intra-round write order: saw {sorted(seen)} "
        f"across {len(arrangements)} arrangements of one round")


def test_three_consecutive_refuting_rounds_still_pause(tmp_path):
    """The true positive the mechanism exists for must still streak: three
    separate review rounds, each refuting and upholding nothing."""
    recs = _corpus(tmp_path, ["noisy"],
                   _rounds("noisy", [["refuted"], ["refuted", "refuted"],
                                     ["refuted"]]))
    (action,) = autonomy_mod.pause_candidates(tmp_path, records=recs)
    assert action.rule_id == "noisy"
    assert action.streak == 3, "the streak counts rounds, not records"


def test_a_round_that_upholds_the_rule_resets_a_running_streak(tmp_path):
    """Two refuting rounds, then a round that both upheld and refuted, then
    one more refuting round: the tail streak is 1, not 4."""
    recs = _corpus(tmp_path, ["noisy"],
                   _rounds("noisy", [["refuted"], ["refuted"],
                                     [_UPHELD_STATUS, "refuted"], ["refuted"]]))
    assert _streak_of("noisy", recs) == 1
    assert autonomy_mod.pause_candidates(tmp_path, records=recs) == []


@needs_design_records
def test_the_c4_round_in_the_committed_corpus_is_not_a_streak():
    """Over this repo's own committed shards — no synthetic fixture can go
    stale against it.

    The pinned shard is one round that both refuted and upheld
    `wiki-fidelity`, refutations filed last. Counted over records, the corpus
    as of that round reads as a refutation streak while the round upheld the
    rule. Later rounds reset the lifetime streak, so the replay is pinned to
    that instant or the guard would pass vacuously.
    """
    from warden import memory as memory_mod

    root = Path(__file__).resolve().parents[1]
    shard = (root / ".warden" / "memory" / "attest"
             / "20260829T182538Z-fdef6cb2-9566c3bb.json")
    assert shard.is_file(), (
        f"{shard.name} is gone — docs/design/retro-20260829.md argues from it by name")
    round_records = json.loads(shard.read_text())["records"]
    rule = "wiki-fidelity"
    refuted = [r for r in round_records
               if r["rule_id"] == rule and r["status"] == "refuted"]
    upheld = [r for r in round_records
              if r["rule_id"] == rule and r["status"] in ("fixed", "confirmed")]
    assert len(refuted) >= memory_mod.PAUSE_STREAK and upheld, (
        "fixture invalid: this shard can no longer exhibit the defect "
        f"({len(refuted)} refuted, {len(upheld)} upheld)")
    cutoff = round_records[0]["ts"]

    as_of = [r for r in memory_mod.records_from_shards(root) if r["ts"] <= cutoff]
    streak = memory_mod.refutation_streaks(as_of).get(rule, 0)
    assert streak < memory_mod.PAUSE_STREAK, (
        f"{rule} reads as a {streak}-round refutation streak as of the round "
        f"that upheld it {len(upheld)} times")


# --- the ceiling guard -----------------------------------------------------

def _ceiling_repo(root: Path, *, ceiling: int) -> Path:
    """A repo whose only implementing rule is the one under test, with a
    declared UNANSWERED ceiling — the shape this platform repo is in (it sits
    at its ceiling with zero headroom)."""
    import yaml as _yaml
    seed_rules(root, "no-swallow")
    d = root / ".warden" / "rules"
    (d / "no-swallow.md").write_text(
        (d / "no-swallow.md").read_text().replace(
            "engine: claude", 'engine: claude\nimplements: ["swallowed-exceptions"]'))
    cat = root / "catalog.yaml"
    cat.write_text(_yaml.safe_dump({"version": 1, "entries": [{
        "id": "swallowed-exceptions", "taxonomy": "A10:2025",
        "name": "Mishandling of exceptional conditions",
        "guards": "Failures caught and discarded.", "engine": "claude",
        "applies_when": "always", "false_positive_cost": "low",
        "sources": [{"title": "OWASP", "url": "https://owasp.org/"}]}]}))
    (root / ".warden" / "catalog-answers.yaml").write_text(
        f"version: 1\nceiling:\n  max_unanswered: {ceiling}\n"
        "  rationale: this fixture declares the tolerance explicitly so the\n"
        "    pause guard has a real line to measure against, rather than one\n"
        "    set to whatever today's count happens to be.\n")
    return cat


def test_a_pause_that_would_breach_the_ceiling_is_refused(tmp_path):
    """A machine pause that would breach the UNANSWERED ceiling is refused.

    A paused rule does not count as coverage: its catalog entry reopens and
    raises UNANSWERED. `rules recommend` exits 1 above the declared ceiling,
    so in a repo sitting AT its ceiling with zero headroom, pausing any
    sole-implementing rule would turn green CI red, from an action the
    autonomy ladder calls machine-eligible PRECISELY because it 'only ever
    makes the gate QUIETER'.

    The guard keeps that claim true: a machine pause that
    would breach the ceiling is refused, so it cannot raise enforcement.
    """
    cat = _ceiling_repo(tmp_path, ceiling=0)
    action = autonomy_mod.PauseAction(
        rule_id="no-swallow", streak=3, judged=9, upheld=6, refuted=3,
        dismissed=0, rate=0.667, wilson_lb=0.35)
    with pytest.raises(autonomy_mod.AutonomyError) as e:
        autonomy_mod.apply_pause(tmp_path, action, catalog_path=cat)
    msg = str(e.value)
    assert "swallowed-exceptions" in msg, f"name the entry that reopens: {msg}"
    # the pair, not two loose digits: "0" and "1" both pass if the numbers are
    # swapped, if the arrow is lost, or if either digit appears anywhere else
    assert "UNANSWERED 0 -> 1" in msg, f"name the before/after pair: {msg}"
    assert "ceiling of 0" in msg, f"name the ceiling it crosses: {msg}"
    assert "catalog-answers" in msg, f"name a remedy: {msg}"
    # ...and it refused BEFORE writing: a guard that edits first is not a guard
    assert not any(r.paused for r in
                   rules_mod.load_rules(tmp_path / ".warden" / "rules"))


def test_a_pause_with_headroom_still_applies(tmp_path):
    """The guard must not become a blanket refusal: with room under the
    ceiling the pause proceeds exactly as before."""
    cat = _ceiling_repo(tmp_path, ceiling=5)
    action = autonomy_mod.PauseAction(
        rule_id="no-swallow", streak=3, judged=9, upheld=6, refuted=3,
        dismissed=0, rate=0.667, wilson_lb=0.35)
    rule_path, artifact = autonomy_mod.apply_pause(tmp_path, action,
                                                   catalog_path=cat)
    assert rule_path.is_file() and artifact.is_file()
    assert any(r.paused for r in
               rules_mod.load_rules(tmp_path / ".warden" / "rules"))


def test_a_repo_with_no_declared_ceiling_is_not_blocked(tmp_path):
    """No ceiling declared means no line to cross. The guard must not invent
    one, or it would refuse pauses in every repo that never opted in."""
    seed_rules(tmp_path, "no-swallow")
    action = autonomy_mod.PauseAction(
        rule_id="no-swallow", streak=3, judged=9, upheld=6, refuted=3,
        dismissed=0, rate=0.667, wilson_lb=0.35)
    rule_path, _ = autonomy_mod.apply_pause(tmp_path, action)
    assert rule_path.is_file()


def test_an_unreadable_declared_ceiling_refuses_the_pause(tmp_path):
    """Fail-closed: `load_ceiling` returns (None, complaints) for a ceiling
    that was DECLARED and could not be read, and its own docstring says that
    case "must stop the run, never silently 'no ceiling' — a typo must not
    disarm the one deterministic obligation this file carries". A guard that
    discarded the complaints would read a `version: 2` typo or unparseable
    YAML as "no ceiling to cross" and write the pause — disarming the guard in
    the one mechanism whose entire job is refusing. `rules recommend` exits 2
    in that same state.
    """
    cat = _ceiling_repo(tmp_path, ceiling=0)
    answers = tmp_path / ".warden" / "catalog-answers.yaml"
    action = autonomy_mod.PauseAction(
        rule_id="no-swallow", streak=3, judged=9, upheld=6, refuted=3,
        dismissed=0, rate=0.667, wilson_lb=0.35)
    for broken in ("version: 2\nceiling:\n  max_unanswered: 0\n",
                   "version: 1\nceiling: [oops\n"):
        answers.write_text(broken)
        with pytest.raises(autonomy_mod.AutonomyError) as e:
            autonomy_mod.apply_pause(tmp_path, action, catalog_path=cat)
        assert "could not be read" in str(e.value), str(e.value)
        assert not any(r.paused for r in rules_mod.load_rules(
            tmp_path / ".warden" / "rules")), "refused, but wrote anyway"


def test_a_pause_that_reopens_nothing_is_allowed_over_the_ceiling(tmp_path):
    """Error-names-cause: the refusal test is "this pause made it worse", not
    `n_after > ceiling`. The latter would make a repo already over its
    ceiling refuse EVERY pause — including ones that reopen nothing — with a
    message naming a reopening that did not happen ("reopens an entry it
    implements, taking UNANSWERED 1 -> 1").

    A pause that reopens nothing genuinely only makes the gate quieter, which
    is the ladder's own test for machine-eligibility, so refusing it is the
    opposite of the guard's intent.
    """
    import yaml as _yaml
    cat = _ceiling_repo(tmp_path, ceiling=0)
    # the repo must ALREADY be over its ceiling, or the naive condition
    # (n_after > ceiling) never fires and this guard proves nothing
    doc = _yaml.safe_load(cat.read_text())
    doc["entries"].append({
        "id": "csrf-protection", "taxonomy": "A01:2025", "name": "CSRF",
        "guards": "State-changing requests without a token.",
        "engine": "claude", "applies_when": "always",
        "false_positive_cost": "low",
        "sources": [{"title": "OWASP", "url": "https://owasp.org/"}]})
    cat.write_text(_yaml.safe_dump(doc))
    from warden import advisor as _adv
    assert len(_adv.recommend(tmp_path, catalog_path=cat, records=[]
                              ).recommendations) == 1, "already over ceiling 0"
    # a second rule that implements nothing: pausing it reopens no entry
    seed_rules(tmp_path, "unrelated")
    action = autonomy_mod.PauseAction(
        rule_id="unrelated", streak=3, judged=9, upheld=6, refuted=3,
        dismissed=0, rate=0.667, wilson_lb=0.35)
    rule_path, _ = autonomy_mod.apply_pause(tmp_path, action, catalog_path=cat)
    assert rule_path.is_file()
    paused = {r.id for r in rules_mod.load_rules(
        tmp_path / ".warden" / "rules") if r.paused}
    assert paused == {"unrelated"}


def test_the_guard_is_cumulative_across_a_batch(tmp_path):
    """Enforcement-truth: the real loop measures a tree each previous pause
    already changed, so measuring each candidate against bare disk would
    clear a pause the previous one made unsafe. Two rules, each implementing
    a distinct entry, ceiling 1: a per-candidate dry run would print "would
    pause" for BOTH, while the real run pauses the first and skips the
    second.

    Two rules and two entries here, because one of each cannot exhibit it.
    """
    import yaml as _yaml
    cat = _ceiling_repo(tmp_path, ceiling=1)
    # a second implementing rule and the entry it answers
    d = tmp_path / ".warden" / "rules"
    seed_rules(tmp_path, "no-csrf")
    (d / "no-csrf.md").write_text(
        (d / "no-csrf.md").read_text().replace(
            "engine: claude", 'engine: claude\nimplements: ["csrf-protection"]'))
    doc = _yaml.safe_load(cat.read_text())
    doc["entries"].append({
        "id": "csrf-protection", "taxonomy": "A01:2025", "name": "CSRF",
        "guards": "State-changing requests without a token.",
        "engine": "claude", "applies_when": "always",
        "false_positive_cost": "low",
        "sources": [{"title": "OWASP", "url": "https://owasp.org/"}]})
    cat.write_text(_yaml.safe_dump(doc))

    # each is safe measured ALONE: 0 -> 1, within the ceiling of 1
    assert autonomy_mod._ceiling_breach(tmp_path, "no-swallow", cat) == ""
    assert autonomy_mod._ceiling_breach(tmp_path, "no-csrf", cat) == ""

    # ...but the second is NOT safe once the first is taken
    breach = autonomy_mod._ceiling_breach(
        tmp_path, "no-csrf", cat, also_paused=frozenset({"no-swallow"}))
    assert breach, "a batch must be measured cumulatively, not each on bare disk"
    assert "csrf-protection" in breach and "1 -> 2" in breach, breach


# ---------- the declared rules dir, not the hardcoded default -------------


def test_pause_acts_on_the_declared_rules_dir(tmp_path):
    """pause_candidates and apply_pause read the declared `review.rules_dir`,
    not `.warden/rules`: on a consumer layout the hardcoded default would
    never detect the streak, and nothing would ever pause."""
    (tmp_path / "repo.yaml").write_text(
        "review:\n  rules_dir: policy/rules\n")
    d = tmp_path / "policy" / "rules"
    d.mkdir(parents=True)
    (d / "noisy-rule.md").write_text(
        "---\nid: noisy-rule\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\n---\nfixture rule\n')
    records = _streak("noisy-rule", ["refuted", "refuted", "refuted"])
    actions = autonomy_mod.pause_candidates(tmp_path, records=records)
    assert [a.rule_id for a in actions] == ["noisy-rule"]
    rule_path, artifact = autonomy_mod.apply_pause(tmp_path, actions[0])
    assert rule_path == d / "noisy-rule.md"
    assert rules_mod.load_rules(d)[0].paused


def test_pause_refuses_when_the_rules_dir_declaration_cannot_be_read(tmp_path):
    """Fail closed: with the declaration unreadable there is no way to know
    which ruleset a pause would edit."""
    (tmp_path / "repo.yaml").write_text("review: [unclosed")
    with pytest.raises(autonomy_mod.AutonomyError, match="repo.yaml"):
        autonomy_mod.pause_candidates(
            tmp_path, records=_streak("noisy-rule", ["refuted"] * 3))


def test_a_pause_whose_gap_cannot_be_computed_is_refused(tmp_path):
    """Fail-closed: recommend can return gap_known=False with ZERO
    recommendations, and _ceiling_breach must not read two such reports as
    n_before=0, n_after=0, reopened=[] — 'could not measure the cost'
    silently becoming 'the pause is safe'. The guard must
    check the reports it measures with, not rely on callers erroring first."""
    import shutil

    cat = _ceiling_repo(tmp_path, ceiling=0)
    shutil.rmtree(tmp_path / ".warden" / "rules")
    breach = autonomy_mod._ceiling_breach(tmp_path, "no-swallow", cat)
    assert breach, "an uncomputable gap must refuse, never read as safe"
    assert "gap" in breach.lower() and "no-swallow" in breach
