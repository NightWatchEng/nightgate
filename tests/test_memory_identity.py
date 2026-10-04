"""Corpus identity across re-attestation, and refutations as filed evidence.

Identity: a record's id hashes the head sha, so re-attesting after a repair
round moves the head and files the SAME findings again under fresh ids — on
the corpus at 1fdda8a (2026-09-02, 125 shards / 979 records) 86 copies, 84
of them in the two most-reviewed PRs and 2 in an earlier #114
re-attestation. A content-derived FINDING identity is read at every seam
that counts (never a rewrite of a committed shard): a finding restated in a
later shard counts once, and the later shard's disposition wins.

Refutations: the attestation is written after adjudication, so a candidate
the cross-examiner refuted would never become a record, and per-rule
precision would measure the survivors of a refute-first filter. The protocol
files every refuted candidate with its reason, and the stats render says how
many rounds actually did.
"""

import json
import random
from pathlib import Path

import pytest

from conftest import seed_rules

from warden import memory as memory_mod
from private_evidence import needs_corpus

REPO = Path(__file__).resolve().parent.parent
HEAD_1, HEAD_2, BASE = "1" * 40, "2" * 40, "a" * 40
RULE_IDS = ("rule-a", "rule-b")


def attestation(findings, *, head, reviewed_at, verdict="findings-open"):
    return {"head_sha": head, "base_sha": BASE, "rules_version": "v1",
            "reviewed_at": reviewed_at,
            "reviewers": [{"role": "code-reviewer", "agent": "x"},
                          {"role": "cross-examiner", "agent": "x"}],
            "findings": findings, "verdict": verdict}


def finding(i, *, rule="rule-a", status="confirmed", text=None, line=None):
    f = {"rule_id": rule, "severity": "MEDIUM", "file": f"src/f{i}.py",
         "line": i if line is None else line,
         "finding": text or f"finding {i}", "evidence": "code",
         "status": status}
    if status in ("refuted", "dismissed-with-reason"):
        f["reason"] = f"because {i}"
    return f


def write_artifact(root, doc, stamp):
    run_dir = root / ".warden" / "out" / f"{stamp}-attest"
    run_dir.mkdir(parents=True)
    (run_dir / "attestation.json").write_text(json.dumps(doc) + "\n")


def ingest(root):
    return memory_mod.ingest(root, rules_dir=seed_rules(root, *RULE_IDS))


def record(i, *, ts, sha, status="confirmed", rule="rule-a", text=None,
           **extra):
    r = {"id": f"{sha[:2]}-{i}", "ts": ts, "seq": i, "sha": sha,
         "rule_id": rule, "tags": [], "dir_prefix": "src",
         "file": f"src/f{i}.py", "line": i, "severity": "MEDIUM",
         "finding": text or f"finding {i}", "evidence": "code",
         "status": status, "origin": "interactive", **extra}
    if status in ("refuted", "dismissed-with-reason"):
        r["reason"] = f"because {i}"
    return r


T1, T2, T3 = ("2026-09-01T10:00:00+00:00", "2026-09-01T11:00:00+00:00",
              "2026-09-01T12:00:00+00:00")


# ---------- the identity itself ---------------------------------------------

def test_finding_identity_ignores_sha_status_and_timestamp():
    a = record(1, ts=T1, sha=HEAD_1, status="confirmed")
    b = record(1, ts=T2, sha=HEAD_2, status="fixed")
    assert memory_mod.finding_identity(a) == memory_mod.finding_identity(b)
    # and it is not the record id, which DOES hash the sha
    assert memory_mod._record_id(a) != memory_mod._record_id(b)


@pytest.mark.parametrize("change", [
    {"rule_id": "rule-b"}, {"file": "src/other.py"}, {"line": 99},
    {"finding": "a different finding"}])
def test_finding_identity_separates_rule_file_line_and_text(change):
    a = record(1, ts=T1, sha=HEAD_1)
    b = {**a, **change}
    assert memory_mod.finding_identity(a) != memory_mod.finding_identity(b)


def test_finding_identity_is_routing_independent():
    """rule_id is rewritten by covered-legacy migration and flips with pause
    state; the artifact's ORIGINAL spelling is the stable key,
    exactly as `_normalized_ids` already does for shard identity."""
    raw = record(1, ts=T1, sha=HEAD_1, rule="docs-drift")
    routed = record(1, ts=T2, sha=HEAD_2, rule="wiki-fidelity",
                    legacy_rule_id="docs-drift")
    assert memory_mod.finding_identity(raw) == memory_mod.finding_identity(routed)


# ---------- the fold --------------------------------------------------------

def test_the_later_shard_disposition_wins():
    earlier = record(1, ts=T1, sha=HEAD_1, status="confirmed")
    later = record(1, ts=T2, sha=HEAD_2, status="fixed")
    folded = memory_mod.fold_restatements([earlier, later])
    assert len(folded) == 1
    assert folded[0]["status"] == "fixed"
    assert folded[0]["sha"] == HEAD_2
    assert folded[0]["restated"] == 1


def test_the_fold_is_order_independent():
    rounds = [record(1, ts=t, sha=s, status=st) for t, s, st in
              [(T1, HEAD_1, "confirmed"), (T2, HEAD_2, "confirmed"),
               (T3, "3" * 40, "fixed")]]
    expected = memory_mod.fold_restatements(list(rounds))
    for _ in range(5):
        shuffled = list(rounds)
        random.shuffle(shuffled)
        got = memory_mod.fold_restatements(shuffled)
        assert got == expected
    assert expected[0]["sha"] == "3" * 40 and expected[0]["restated"] == 2


def test_the_fold_is_idempotent_and_keeps_the_count():
    rounds = [record(1, ts=T1, sha=HEAD_1), record(1, ts=T2, sha=HEAD_2)]
    once = memory_mod.fold_restatements(rounds)
    twice = memory_mod.fold_restatements(once)
    assert twice == once and twice[0]["restated"] == 1


def test_distinct_findings_are_never_folded():
    # same rule, same file, same text — two LINES are two instances (the
    # `_record_id` docstring's own rule, kept)
    a = record(1, ts=T1, sha=HEAD_1, text="same")
    b = {**record(2, ts=T1, sha=HEAD_1, text="same"), "file": a["file"]}
    assert len(memory_mod.fold_restatements([a, b])) == 2
    # same sha, same event, two different findings
    assert len(memory_mod.fold_restatements(
        [record(1, ts=T1, sha=HEAD_1), record(2, ts=T1, sha=HEAD_1)])) == 2


def test_gate_detections_are_facts_and_never_fold():
    """A checker firing on four commits is four facts (test_lifecycle counts
    them as the rule having fired); there is no disposition to restate."""
    hits = [{**record(1, ts=f"2026-09-0{d}T10:00:00+00:00", sha=f"{d:040x}",
                      status="detected"), "source": "gate"} for d in range(1, 5)]
    assert memory_mod.fold_restatements(hits) == hits


def test_the_fold_never_crosses_within_a_round():
    """A restatement is in a LATER round by definition. One round filing the
    same finding twice with two dispositions — reviewer and examiner
    disagreeing — is two judgments, as `_record_id` already keeps them."""
    a = record(1, ts=T1, sha=HEAD_1, status="confirmed")
    b = {**record(1, ts=T1, sha=HEAD_1, status="refuted"), "seq": 9,
         "id": "same-round-copy"}
    assert memory_mod.fold_restatements([a, b]) == [a, b]
    # and when a later round restates it once, the later round's single
    # record stands for both earlier copies
    later = record(1, ts=T2, sha=HEAD_2, status="fixed")
    [only] = memory_mod.fold_restatements([a, b, later])
    assert only["sha"] == HEAD_2 and only["restated"] == 2


def test_the_latest_round_keeps_everything_it_filed():
    earlier = record(1, ts=T1, sha=HEAD_1, status="confirmed")
    kept_a = record(1, ts=T2, sha=HEAD_2, status="confirmed")
    kept_b = {**record(1, ts=T2, sha=HEAD_2, status="refuted"), "seq": 5,
              "id": "second"}
    folded = memory_mod.fold_restatements([earlier, kept_a, kept_b])
    assert [r["id"] for r in folded] == [kept_a["id"], kept_b["id"]]
    assert folded[0]["restated"] == 1 and "restated" not in folded[1]


def test_the_fold_does_not_mutate_its_input():
    rounds = [record(1, ts=T1, sha=HEAD_1), record(1, ts=T2, sha=HEAD_2)]
    before = json.dumps(rounds, sort_keys=True)
    memory_mod.fold_restatements(rounds)
    assert json.dumps(rounds, sort_keys=True) == before


# ---------- through the real seams -------------------------------------------

def _re_attested_corpus(root):
    """One review, attested twice: the head moved after a repair round, the
    payload was carried forward. Round one: two confirmed, one refuted. Round
    two restates all three and adds a fourth; the confirmed ones are now fixed."""
    first = [finding(1), finding(2), finding(3, status="refuted")]
    second = [finding(1, status="fixed"), finding(2, status="fixed"),
              finding(3, status="refuted"), finding(4, rule="rule-b")]
    write_artifact(root, attestation(first, head=HEAD_1, reviewed_at=T1),
                   "20260901T100000Z")
    write_artifact(root, attestation(second, head=HEAD_2, reviewed_at=T2),
                   "20260901T110000Z")
    return ingest(root)


def test_re_attestation_counts_once_in_stats(tmp_path):
    result = _re_attested_corpus(tmp_path)
    assert result["total_records"] == 7          # the shards hold every copy
    doc = memory_mod.stats(tmp_path)
    assert doc["total_records"] == 4             # the tally counts findings
    assert doc["restatements_folded"] == 3
    assert doc["per_rule"]["rule-a"] == {
        "confirmed": 0, "fixed": 2, "refuted": 1, "dismissed": 0, "n": 3,
        "wilson_lb": doc["per_rule"]["rule-a"]["wilson_lb"]}
    assert doc["per_rule"]["rule-b"]["n"] == 1


def test_no_committed_shard_is_modified_by_the_fold(tmp_path):
    _re_attested_corpus(tmp_path)
    shard_dir = tmp_path / ".warden" / "memory" / "attest"
    before = {p.name: p.read_bytes() for p in shard_dir.glob("*.json")}
    assert len(before) == 2
    memory_mod.stats(tmp_path)
    memory_mod.records_from_shards(tmp_path)
    memory_mod.load_records(tmp_path)
    assert {p.name: p.read_bytes() for p in shard_dir.glob("*.json")} == before
    # and every copy is still in the shards — the fold is a READ, the store
    # is append-only evidence
    assert sum(len(json.loads(b)["records"]) for b in before.values()) == 7


def test_both_read_seams_fold_the_same_way(tmp_path):
    _re_attested_corpus(tmp_path)
    from_cache = memory_mod.load_records(tmp_path)
    from_shards = memory_mod.records_from_shards(tmp_path)
    assert len(from_cache) == len(from_shards) == 4
    assert {r["id"] for r in from_cache} == {r["id"] for r in from_shards}
    assert all(r["sha"] == HEAD_2 for r in from_cache)


def test_a_restated_refutation_is_one_refuting_round(tmp_path):
    """The pause streak counts refuting ROUNDS. A refutation carried forward
    into a re-attestation is the same judgment restated, not a second round
    refuting the rule — or every repair round would walk a rule toward a
    pause it never earned."""
    for n, (head, ts) in enumerate([(HEAD_1, T1), (HEAD_2, T2)]):
        write_artifact(tmp_path, attestation(
            [finding(1, status="refuted")], head=head, reviewed_at=ts),
            f"2026090{n}T100000Z")
    ingest(tmp_path)
    streaks = memory_mod.refutation_streaks(
        memory_mod.records_from_shards(tmp_path))
    assert streaks == {"rule-a": 1}


def test_recall_does_not_report_a_restatement_as_recurrence(tmp_path):
    _re_attested_corpus(tmp_path)
    hits = memory_mod.recall(tmp_path, files=["src/f1.py"], role="reviewer")
    rendered = memory_mod.render_recall(hits, "reviewer")
    assert rendered.count("finding 1") == 1
    # recurrence is keyed by (rule, dir_prefix): findings 1 and 2 both sit in
    # src/, so rule-a genuinely recurred twice there — not four times, which
    # is what two copies of each read as before the fold
    assert "[recurred 2x here]" in rendered and "4x" not in rendered


def test_stats_names_the_fold_in_the_render(tmp_path):
    _re_attested_corpus(tmp_path)
    text = memory_mod.render_stats(memory_mod.stats(tmp_path))
    # "finding(s)": the header counts findings, and the word says so —
    # `memory ingest` is where "record(s)" means the cache's rows
    assert "memory stats — 4 finding(s)" in text
    assert "3 restatement(s) folded" in text
    assert "counts once" in text


def test_a_corpus_with_no_restatements_says_nothing_about_them(tmp_path):
    write_artifact(tmp_path, attestation([finding(1)], head=HEAD_1,
                                         reviewed_at=T1), "20260901T100000Z")
    ingest(tmp_path)
    doc = memory_mod.stats(tmp_path)
    assert doc["restatements_folded"] == 0
    assert "restatement" not in memory_mod.render_stats(doc)


# ---------- the known duplicates, on the committed corpus -------------------

# PR #172 ("warden mints the review round", merged 2026-09-01T16:18Z) — the
# x2 group, two attestations of one payload. Not #167, whose shards are the
# THREE below.
PR_172_PAIR = ("20260901T154508Z-c4d8eb0a-589b2883.json",
               "20260901T161109Z-80303cde-024af812.json")
# PR #167 — the x3 group, counted separately.
PR_167_TRIO = ("20260831T223415Z-c00f7f43-bbff46f9.json",
               "20260831T224644Z-88e8d10b-8c79ad51.json",
               "20260831T232211Z-9e13c3ce-d72336c8.json")


def _committed(names):
    return [json.loads((REPO / ".warden" / "memory" / "attest" / n).read_text())
            for n in names]


@needs_corpus
def test_the_172_re_attestation_shards_fold_to_their_distinct_findings():
    """The x2 pair: 30 of 30 findings shared BY TEXT, 0 of 30
    by id. Read from the committed shards of this repo — never rewritten."""
    docs = _committed(PR_172_PAIR)
    records = [r for d in docs for r in d["records"]]
    assert len(records) == 61 and len({r["id"] for r in records}) == 61
    folded = memory_mod.fold_restatements(records)
    assert len(folded) == 31
    assert sum(r.get("restated", 0) for r in folded) == 30
    # the later shard's disposition wins, so every folded winner is from it
    later = docs[1]["sha"]
    assert all(r["sha"] == later for r in folded if r.get("restated"))


@needs_corpus
def test_the_167_re_attestation_shards_fold_to_their_distinct_findings():
    """The x3 group, three
    attestations of PR #167 under three moved heads. Distinct shards, one
    set of findings; the newest shard's records are the survivors."""
    docs = _committed(PR_167_TRIO)
    records = [r for d in docs for r in d["records"]]
    assert len({r["id"] for r in records}) == len(records)
    folded = memory_mod.fold_restatements(records)
    assert len(folded) < len(records), "three attestations, one set of findings"
    assert sum(r.get("restated", 0) for r in folded) == (
        len(records) - len(folded))
    latest = docs[-1]["sha"]
    assert all(r["sha"] == latest for r in folded if r.get("restated"))


@needs_corpus
def test_the_two_known_groups_are_the_bulk_of_the_corpus_wide_fold():
    """The copies land where review was heaviest. Stated as the measurement it
    is — 84 of the 86 in these two PRs, 2 elsewhere — rather than as the 'all
    of them' the fold's own output refutes."""
    total = memory_mod.records_from_shards(REPO)
    folded_away = sum(r.get("restated", 0) for r in total)
    known = {n.split("-")[1] for n in PR_172_PAIR + PR_167_TRIO}
    in_known = 0
    for r in total:
        for prior in r.get("restated_from", ()):
            in_known += any(prior["sha"].startswith(k) for k in known)
    # The five named shards are frozen files, so `in_known` is a constant;
    # `folded_away` is NOT — re-attestation is the normal flow this change
    # documents, and every future one adds to it. Pinning their difference
    # would pin the live corpus at exactly 86 and turn an ordinary merge into
    # a red main. What the claim actually needs is
    # that the two known groups are the bulk of it and are not the whole of
    # it; the exact 86 is pinned where it belongs, against the frozen tree
    # 1fdda8a, in the prose and in the two tests above.
    assert in_known == 84, "the two known groups account for 84 copies"
    assert folded_away > in_known, (
        "at least one copy sits outside them — the earlier #114 "
        "re-attestation — so the claim is 'almost all', never 'all'")


# ---------- refutations as filed evidence ------------------------------------

def test_review_events_count_the_rounds_that_filed_a_refutation(tmp_path):
    write_artifact(tmp_path, attestation(
        [finding(1), finding(2, status="refuted")], head=HEAD_1,
        reviewed_at=T1), "20260901T100000Z")
    write_artifact(tmp_path, attestation(
        [finding(3, status="fixed")], head=HEAD_2, reviewed_at=T2,
        verdict="clean"), "20260901T110000Z")
    write_artifact(tmp_path, attestation(
        [], head="3" * 40, reviewed_at=T3, verdict="clean"),
        "20260901T120000Z")
    ingest(tmp_path)
    events = memory_mod.review_events(tmp_path)
    assert events["total"] == 3
    assert events["with_records"] == 2
    assert events["with_refutation"] == 1


def test_the_render_says_precision_is_over_filed_judgments(tmp_path):
    write_artifact(tmp_path, attestation(
        [finding(1)], head=HEAD_1, reviewed_at=T1), "20260901T100000Z")
    write_artifact(tmp_path, attestation(
        [finding(2, status="refuted")], head=HEAD_2, reviewed_at=T2,
        verdict="clean"), "20260901T110000Z")
    ingest(tmp_path)
    text = memory_mod.render_stats(memory_mod.stats(tmp_path))
    assert "1 of 2 rounds that FILED a record filed a refutation" in text
    assert "FILED judgments" in text


def test_a_refuted_candidate_enters_the_corpus_with_its_reason(tmp_path):
    write_artifact(tmp_path, attestation(
        [finding(1, status="refuted")], head=HEAD_1, reviewed_at=T1,
        verdict="clean"), "20260901T100000Z")
    ingest(tmp_path)
    [rec] = memory_mod.records_from_shards(tmp_path)
    assert rec["status"] == "refuted" and rec["reason"] == "because 1"
    # and it is what the examiner is shown, never the reviewer
    assert memory_mod.recall(tmp_path, files=["src/f1.py"], role="examiner")
    assert not memory_mod.recall(tmp_path, files=["src/f1.py"], role="reviewer")


# ---------- fold safety and reader parity ------------------------------------

def test_the_fold_can_never_lengthen_a_refutation_streak():
    """Round 1 is MIXED as it happened; round 3 restates its upheld finding as
    refuted. Folding only the survivor would turn round 1 into an all-refuted
    round and walk the streak from 2 to PAUSE_STREAK — and `warden autonomy
    pause` reads exactly this number to mutate gate surface, so the fold
    would REMOVE enforcement by rewriting a round's verdict."""
    r1 = [record(9, ts=T1, sha=HEAD_1, status="confirmed", text="upheld"),
          record(1, ts=T1, sha=HEAD_1, status="refuted")]
    r2 = [record(2, ts=T2, sha=HEAD_2, status="refuted")]
    r3 = [record(3, ts=T3, sha="3" * 40, status="refuted"),
          record(9, ts=T3, sha="3" * 40, status="refuted", text="upheld")]
    raw = r1 + r2 + r3
    assert memory_mod.refutation_streaks(raw) == {"rule-a": 2}
    folded = memory_mod.fold_restatements(list(raw))
    assert memory_mod.refutation_streaks(folded) == {"rule-a": 2}
    assert memory_mod.refutation_streaks(folded)["rule-a"] < memory_mod.PAUSE_STREAK


def test_the_fold_only_ever_shortens_a_streak_over_random_corpora():
    """The property `_round_key`'s docstring states as the safety invariant,
    made executable rather than asserted: a fold may SHORTEN a streak (a
    refutation carried forward is one judgment restated) and may never
    lengthen one (that direction removes enforcement)."""
    rng = random.Random(20260902)   # seeded: a property test must reproduce
    for _ in range(300):
        raw = []
        for round_n in range(rng.randint(1, 5)):
            ts = f"2026-09-0{round_n + 1}T10:00:00+00:00"
            for k in range(rng.randint(1, 3)):
                raw.append(record(
                    rng.randint(1, 3), ts=ts, sha=f"{round_n:040x}",
                    status=rng.choice(["confirmed", "fixed", "refuted",
                                       "dismissed-with-reason"])))
        before = memory_mod.refutation_streaks(raw)
        after = memory_mod.refutation_streaks(
            memory_mod.fold_restatements(list(raw)))
        for rule in set(before) | set(after):
            assert after.get(rule, 0) <= before.get(rule, 0), (
                f"the fold LENGTHENED {rule}'s streak: "
                f"{before.get(rule, 0)} -> {after.get(rule, 0)}")


def test_a_fold_that_reverses_a_judgment_is_counted_and_rendered(tmp_path):
    """`restated: N` alone cannot distinguish a copy that repeated a judgment
    from one that REVERSED it, so an upheld judgment erased by a later round
    would move the sign of a contribution invisibly."""
    write_artifact(tmp_path, attestation(
        [finding(1, status="confirmed")], head=HEAD_1, reviewed_at=T1),
        "20260901T100000Z")
    write_artifact(tmp_path, attestation(
        [finding(1, status="refuted")], head=HEAD_2, reviewed_at=T2,
        verdict="clean"), "20260901T110000Z")
    ingest(tmp_path)
    doc = memory_mod.stats(tmp_path)
    assert doc["restatements_folded"] == 1
    assert doc["restatements_changed"] == 1
    assert "REVERSING an earlier round's judgment" in memory_mod.render_stats(doc)
    # and a plain restatement is not reported as one
    assert memory_mod.stats(tmp_path, records=[
        record(1, ts=T1, sha=HEAD_1, status="confirmed"),
        record(1, ts=T2, sha=HEAD_2, status="confirmed"),
    ])["restatements_changed"] == 0


def test_the_survivor_carries_the_judgments_it_stands_for():
    earlier = record(1, ts=T1, sha=HEAD_1, status="confirmed")
    later = record(1, ts=T2, sha=HEAD_2, status="refuted")
    [only] = memory_mod.fold_restatements([earlier, later])
    assert [p["status"] for p in only["restated_from"]] == ["confirmed"]
    # bookkeeping never leaks into an earlier copy the fold carries forward
    assert "restated_from" not in only["restated_from"][0]


def test_ingest_and_stats_cannot_disagree_on_the_tag_ceiling(tmp_path):
    """`undeclared_at_bar` counts judged RECORDS against DECLARE_MIN_N, so
    unless both readers fold, one coined tag carried through three
    re-attestations is at the bar in ingest-result.json and absent from
    `memory stats` — two readers of one corpus printing different ceiling
    verdicts, one of them the line `warden memory ingest` shouts as TAG
    CEILING BREACHED."""
    mem = tmp_path / ".warden" / "memory"
    mem.mkdir(parents=True)
    (mem / "tags.yaml").write_text(
        "tags: {}\nceiling:\n  max_undecided: 0\n"
        "  rationale: a name with no disposition is recall nobody can find\n")
    for n, (head, ts) in enumerate([(HEAD_1, T1), (HEAD_2, T2),
                                    ("3" * 40, T3)]):
        f = finding(1)
        f["tags"] = ["zzz-coined"]
        write_artifact(tmp_path, attestation([f], head=head, reviewed_at=ts),
                       f"2026090{n + 1}T100000Z")
    result = ingest(tmp_path)
    doc = memory_mod.stats(tmp_path)
    assert result["tag_ceiling"] == doc["tags"]["ceiling"]
    # and the artifact reconciles its own row count with the finding count
    assert result["restatements_folded"] == 2
    assert (result["total_records"] - result["restatements_folded"]
            == doc["total_records"])


def test_a_malformed_records_field_is_unreadable_not_an_empty_round(tmp_path):
    """`records_from_shards` REFUSES a shard whose records is not a list ('not
    an evidence shard'); the review-events denominator must not read the same
    shard as a round that filed nothing, with `unreadable` left at 0 so the
    'floor, not a count' warning never fires. Two readers of one store
    disagreeing about what a shard IS is the class the function's own comment
    names."""
    shards = tmp_path / ".warden" / "memory" / "attest"
    shards.mkdir(parents=True)
    good = {"sha": HEAD_1, "source": "attest", "verdict": "clean",
            "records": []}
    (shards / "20260901T100000Z-aaaaaaaa-11111111.json").write_text(
        json.dumps(good))
    for name, bad in (("bbbbbbbb", {"not": "a list"}),
                      ("cccccccc", ["refuted", "refuted"])):
        (shards / f"20260901T110000Z-{name}-22222222.json").write_text(
            json.dumps({**good, "sha": name * 5, "records": bad}))
    events = memory_mod.review_events(tmp_path)
    assert events["unreadable"] == 2
    assert events["total"] == 1 and events["with_records"] == 0
    text = memory_mod.render_stats(memory_mod.stats(tmp_path))
    assert "this tally is a floor, not a count" in text
    # The PARITY claim is not asserted here, and that is deliberate:
    # `records_from_shards` walks `sorted(glob)` and raises on the FIRST bad
    # shard, so one `pytest.raises` over a directory holding two shapes only
    # ever reaches one of them, and would pass with the divergence it denies
    # present. One shape per fixture:
    # test_both_readers_refuse_the_same_malformed_shard.


# ---------- the suppressor, reversals and malformed shards -------------------

def test_the_fold_can_never_lower_the_refuting_weight():
    """The weight is a SUPPRESSOR — `_candidate_rows` and `skill_recurrence`
    read `weight >= PAUSE_STREAK` to STOP proposing a class reviewers keep
    refuting — so its safe direction is the OPPOSITE of the streak's.
    Withholding a folded-away refutation from the round that filed it would
    drop the weight below the bar and turn a `noisy` candidate into a
    `propose` one, flipping certify's S-04 from False to True."""
    weight = lambda recs: memory_mod._refuting_weight(  # noqa: E731
        recs, subject=lambda r: r["rule_id"])
    r1 = [record(i, ts=T1, sha=HEAD_1, status="refuted") for i in (1, 2, 3)]
    r2 = [record(1, ts=T2, sha=HEAD_2, status="refuted")]
    raw = r1 + r2
    assert weight(raw) == {"rule-a": 3}
    assert weight(memory_mod.fold_restatements(list(raw))) == {"rule-a": 3}


def test_neither_streak_nor_weight_moves_unsafely_over_random_corpora():
    """The two round-level readers have OPPOSITE safe directions and one
    restoration rule cannot serve both. Pinned together so a future edit to
    `_with_restatements` cannot satisfy one and silently break the other."""
    rng = random.Random(20260903)   # seeded: a property test must reproduce
    weight = lambda recs: memory_mod._refuting_weight(  # noqa: E731
        recs, subject=lambda r: r["rule_id"])
    for _ in range(300):
        raw = []
        for round_n in range(rng.randint(1, 5)):
            ts = f"2026-09-0{round_n + 1}T10:00:00+00:00"
            for _k in range(rng.randint(1, 3)):
                raw.append(record(
                    rng.randint(1, 3), ts=ts, sha=f"{round_n:040x}",
                    status=rng.choice(["confirmed", "fixed", "refuted",
                                       "dismissed-with-reason"])))
        folded = memory_mod.fold_restatements(list(raw))
        s_before = memory_mod.refutation_streaks(raw)
        s_after = memory_mod.refutation_streaks(folded)
        w_before, w_after = weight(raw), weight(folded)
        for rule in set(s_before) | set(s_after):
            assert s_after.get(rule, 0) <= s_before.get(rule, 0), (
                "the fold LENGTHENED a streak — that direction PAUSES a rule")
        for rule in set(w_before) | set(w_after):
            assert w_after.get(rule, 0) >= w_before.get(rule, 0), (
                "the fold LOWERED the suppressor — that direction un-blocks "
                "a proposal about a class reviewers keep refuting")


def test_a_repair_round_restatement_is_not_reported_as_a_reversal():
    """`confirmed` -> `fixed` is the canonical repair-round restatement and
    the shape the fold exists for; both sit in the Wilson numerator, so no
    sign moves and nothing should be shouted. A raw `status !=` would fire
    loudest on exactly this benign majority."""
    progression = [record(1, ts=T1, sha=HEAD_1, status="confirmed"),
                   record(1, ts=T2, sha=HEAD_2, status="fixed")]
    doc = memory_mod.stats(REPO, records=list(progression))
    assert doc["restatements_folded"] == 1
    assert doc["restatements_changed"] == 0
    assert "REVERSING" not in memory_mod.render_stats(doc)
    # a real reversal still counts, and so does refuted -> dismissed, which
    # moves no Wilson sign but does reset a streak
    for later in ("refuted", "dismissed-with-reason"):
        pair = [record(1, ts=T1, sha=HEAD_1, status="confirmed"),
                record(1, ts=T2, sha=HEAD_2, status=later)]
        assert memory_mod.stats(REPO, records=pair)["restatements_changed"] == 1
    flip = [record(1, ts=T1, sha=HEAD_1, status="refuted"),
            record(1, ts=T2, sha=HEAD_2, status="dismissed-with-reason")]
    assert memory_mod.stats(REPO, records=flip)["restatements_changed"] == 1


@pytest.mark.parametrize("envelope", [
    {},                                  # a well-formed attest envelope
    {"source": "gate"},                  # a foreign source
    {"sha": None},                       # no head sha
], ids=["attest", "foreign-source", "no-sha"])
@pytest.mark.parametrize("bad", [
    {"not": "a list"},                       # records is not a list
    ["refuted", "refuted"],                  # its elements are not objects
    [{"ok": 1}, "not-a-dict"],               # one of them is not
    [{"rule_id": 123}],                      # a PRESENT non-string rule_id
    [{"rule_id": "r", "tags": "fail-open"}],  # tags that is not a list
    [{"rule_id": "r", "tags": [1]}],          # tags that is not strings
])
def test_both_readers_refuse_the_same_malformed_shard(tmp_path, bad,
                                                          envelope):
    """ONE shape per fixture, because with two in one directory
    `records_from_shards` raises on whichever sorts first — so a parity
    assertion would pass with the divergence it denies present. A records LIST
    whose elements are not dicts passes an `isinstance(..., list)` check and
    must not die as a bare AttributeError, past the `except ValueError` that
    certify and `autonomy pause` fail closed with."""
    shards = tmp_path / ".warden" / "memory" / "attest"
    shards.mkdir(parents=True)
    (shards / "20260901T110000Z-bbbbbbbb-22222222.json").write_text(
        json.dumps({"sha": HEAD_2, "source": "attest", "verdict": "clean",
                    "records": bad, **envelope}))
    # The envelope is parametrized because the guard's PLACEMENT matters:
    # sitting after the source/sha short-circuit, a shard that is malformed
    # AND carries a foreign source or a null sha would be skipped as "not an
    # orchestrated review" — neither counted nor called unreadable — while
    # records_from_shards refuses the whole corpus on the record shape alone.
    assert memory_mod.review_events(tmp_path)["unreadable"] == 1
    # The message differs by shape — "not an evidence shard (no records
    # list)" for a non-list, "record N is not a mapping" for a bad element
    # (the per-record loop, a strict superset of what this branch needs).
    # What both readers owe is the same: a ValueError NAMING
    # THE SHARD, which is the type certify and `autonomy pause` fail closed
    # on, rather than an AttributeError from deep inside.
    with pytest.raises(ValueError, match=r"22222222\.json"):
        memory_mod.records_from_shards(tmp_path)
    # and the two commands that read this store deliver a VERDICT, never a
    # traceback: ingest refuses by name, certification's E-02 fails by name
    with pytest.raises(memory_mod.AttestError, match=r"22222222\.json"):
        memory_mod.ingest(tmp_path, rules_dir=seed_rules(tmp_path, *RULE_IDS))
    from warden import certify as certify_mod
    ok, detail = certify_mod._run_check(
        {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"},
        tmp_path)
    # E-02 must name THE SHARD, not blame the ruleset: a non-string rule_id
    # reaching `_check_rule_ids` would come back as "ruleset unreadable"
    # while the ruleset is fine and one committed shard is not.
    assert not ok and "22222222.json" in detail
    assert "ruleset unreadable" not in detail


def test_ingest_and_stats_agree_on_unknown_tags_too(tmp_path):
    """`unknown_tags` is read from the folded list, like the ceiling:
    `tags.audit` derives its own `unknown` from the records `stats` hands it,
    which ARE folded, so an unfolded `unknown_tags` would make the two
    commands print different UNKNOWN verdicts for one store on the same
    question."""
    mem = tmp_path / ".warden" / "memory"
    mem.mkdir(parents=True)
    (mem / "tags.yaml").write_text("tags:\n  flake: a flaky test\n")
    early = finding(1)
    early["tags"] = ["zzz-only-on-the-copy"]
    write_artifact(tmp_path, attestation([early], head=HEAD_1, reviewed_at=T1),
                   "20260901T100000Z")
    later = finding(1)
    later["tags"] = ["flake"]
    write_artifact(tmp_path, attestation([later], head=HEAD_2, reviewed_at=T2),
                   "20260901T110000Z")
    result = ingest(tmp_path)
    assert result["unknown_tags"] == memory_mod.stats(tmp_path)["tags"]["unknown"]
