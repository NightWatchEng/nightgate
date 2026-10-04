"""Review memory: the design-of-record acceptance criteria.

docs/design/memory.md acceptance: ingest recovers a historical multi-finding
round; a planted-key artifact ingests REDACTED (grep proves no secret in the
committed shards); concurrent writers produce no conflicts; role-split recall
stays within budget; stats show no rule qualifying for promotion at low N.
"""

import json
from pathlib import Path

import pytest

from conftest import seed_rules

from warden import memory as memory_mod

HEAD, BASE = "b" * 40, "a" * 40
PLANTED_KEY = "sk-live_FAKE1234567890abcdefFAKE"


def make_attestation(findings, reviewed_at="2026-08-20T04:00:00+00:00",
                     head=HEAD) -> dict:
    return {
        "head_sha": head, "base_sha": BASE, "rules_version": "v123",
        "reviewed_at": reviewed_at,
        "reviewers": [{"role": "code-reviewer", "agent": "subagent"},
                      {"role": "cross-examiner", "agent": "subagent"}],
        "findings": findings, "verdict": "findings-open",
    }


def finding(i, *, rule="scope-creep", status="confirmed", file=None, tags=None,
            evidence="code snippet"):
    f = {"rule_id": rule, "severity": "MEDIUM",
         "file": file or f"worker/api/handler_{i}.py", "line": i,
         "finding": f"finding number {i}", "evidence": evidence,
         "status": status}
    if status in ("dismissed-with-reason", "refuted"):
        f["reason"] = f"reason {i}"
    if tags:
        f["tags"] = tags
    return f


def write_attest_artifact(root: Path, attestation: dict, stamp: str) -> None:
    run_dir = root / ".warden" / "out" / f"{stamp}-attest"
    run_dir.mkdir(parents=True)
    (run_dir / "attestation.json").write_text(json.dumps(attestation) + "\n")


# ingest resolves every finding's rule_id against the declared ruleset, so
# the fixture repo declares the ids these rounds use.
FIXTURE_RULE_IDS = ("scope-creep", "secrets-in-diff", "sql-migration-guard",
                    "rule-a", "rule-b", "good-rule", "noisy-rule", "flaky-rule")


def ingest(root: Path, **kwargs) -> dict:
    return memory_mod.ingest(
        root, rules_dir=seed_rules(root, *FIXTURE_RULE_IDS), **kwargs)


@pytest.fixture()
def root(tmp_path) -> Path:
    return tmp_path


# ---------- acceptance: historical round recovers ----------------------------

def test_ingest_recovers_a_14_finding_round(root):
    findings = [finding(i) for i in range(10)] + \
        [finding(i, status="refuted") for i in range(10, 13)] + \
        [finding(13, status="fixed")]
    assert len(findings) == 14
    write_attest_artifact(root, make_attestation(findings), "20260820T040000Z")
    result = ingest(root)
    assert result["total_records"] == 14
    records = memory_mod.load_records(root)
    statuses = sorted(r["status"] for r in records)
    assert statuses.count("confirmed") == 10
    assert statuses.count("refuted") == 3
    assert statuses.count("fixed") == 1
    # keys are (dir_prefix, tags, rule_id) — never bare file paths
    assert all(r["dir_prefix"] == "worker/api" for r in records)


# ---------- acceptance: planted key ingests redacted -------------------------

def test_planted_key_never_reaches_committed_shards(root):
    findings = [
        finding(0, rule="secrets-in-diff", evidence=f'API_KEY = "{PLANTED_KEY}"'),
        finding(1, rule="scope-creep", evidence=f'token = "{PLANTED_KEY}"'),
    ]
    write_attest_artifact(root, make_attestation(findings), "20260820T040000Z")
    ingest(root)
    shard_dir = memory_mod.memory_dir(root) / "attest"
    corpus = "".join(p.read_text() for p in shard_dir.glob("*.json"))
    assert PLANTED_KEY not in corpus, "planted key leaked into committed shards"
    assert "<redacted:secrets-in-diff>" in corpus   # secret-class rule
    assert "<redacted:secret-pattern>" in corpus    # belt-and-braces scrub
    cache = (memory_mod.memory_dir(root) / "findings.jsonl").read_text()
    assert PLANTED_KEY not in cache


def test_reason_field_is_redacted(root):
    # reason is REQUIRED on the examiner path — exactly where a human quotes
    # the secret while dismissing it
    f = finding(0, rule="secrets-in-diff", status="dismissed-with-reason")
    f["reason"] = f'fixture only; the value {PLANTED_KEY} is not live'
    write_attest_artifact(root, make_attestation([f]), "20260820T040000Z")
    ingest(root)
    shard_dir = memory_mod.memory_dir(root) / "attest"
    corpus = "".join(p.read_text() for p in shard_dir.glob("*.json"))
    assert PLANTED_KEY not in corpus
    records = memory_mod.recall(root, files=["worker/api/x.py"], role="examiner")
    assert PLANTED_KEY not in memory_mod.render_recall(records, "examiner")


def test_scrub_covers_every_gate_secret_shape(root):
    # memory's scrub must cover at least what the gate's mechanical checker
    # auto-rejects — DSNs, AWS ids, JWTs, github_pat
    shapes = [
        "postgres://svc:hunter2secret@db.prod/x",
        "AKIA1234567890ABCDEF",
        "eyJabcdefghij1.eyJabcdefghij2.sigpart",
        "github_pat_11ABCDEFGHIJKLMNOPQRST",
    ]
    findings = [finding(i, rule="sql-migration-guard", evidence=s)
                for i, s in enumerate(shapes)]
    write_attest_artifact(root, make_attestation(findings), "20260820T040000Z")
    ingest(root)
    shard_dir = memory_mod.memory_dir(root) / "attest"
    corpus = "".join(p.read_text() for p in shard_dir.glob("*.json"))
    for shape in shapes:
        assert shape not in corpus, f"gate-rejectable secret committed: {shape}"


def test_ts_sha_tie_between_distinct_events_loses_nothing(root):
    # same reviewed_at + head sha, different findings — the second event must
    # not overwrite the first's shard
    a = make_attestation([finding(0, rule="rule-a")])
    b = make_attestation([finding(1, rule="rule-b")])
    write_attest_artifact(root, a, "20260820T040000Z")
    write_attest_artifact(root, b, "20260820T040001Z")
    result = ingest(root)
    assert len(result["new_shards"]) == 2
    rules = {r["rule_id"] for r in memory_mod.load_records(root)}
    assert rules == {"rule-a", "rule-b"}


def test_same_finding_on_two_lines_is_two_records(root):
    # id basis includes line — stats n must not undercount
    findings = [finding(0, file="worker/api/h.py"), finding(0, file="worker/api/h.py")]
    findings[1]["line"] = 99
    write_attest_artifact(root, make_attestation(findings), "20260820T040000Z")
    result = ingest(root)
    assert result["total_records"] == 2


def test_malformed_artifact_skipped_cache_still_rebuilt(root):
    # one bad artifact must not abort mid-mutation
    write_attest_artifact(root, make_attestation([finding(0)]), "20260819T030000Z")
    bad = root / ".warden" / "out" / "20260820T040000Z-attest"
    bad.mkdir(parents=True)
    (bad / "attestation.json").write_text("{not json")
    result = ingest(root)
    assert result["total_records"] == 1
    assert len(result["skipped"]) == 1 and "20260820T040000Z-attest" in result["skipped"][0]


def test_recall_by_rule_id_key(root):
    write_attest_artifact(root, make_attestation([finding(0)]), "20260820T040000Z")
    ingest(root)
    hit = memory_mod.recall(root, files=["ios/View.swift"], role="reviewer",
                            rules=["scope-creep"])
    assert len(hit) == 1


# ---------- acceptance: concurrent writers, idempotency ----------------------

def test_distinct_events_shard_to_distinct_files_and_reingest_is_idempotent(root):
    write_attest_artifact(root, make_attestation([finding(0)]), "20260819T030000Z")
    write_attest_artifact(
        root, make_attestation([finding(1)], reviewed_at="2026-08-21T04:00:00+00:00",
                               head="c" * 40), "20260821T040000Z")
    first = ingest(root)
    assert len(first["new_shards"]) == 2  # distinct filenames: no merge conflict
    second = ingest(root)
    assert second["new_shards"] == []     # idempotent
    assert second["total_records"] == first["total_records"] == 2


# ---------- acceptance: role-split recall within budget ----------------------

def test_recall_split_and_budget(root):
    findings = [finding(i, tags=["fences"]) for i in range(20)] + \
        [finding(i, status="refuted", tags=["fences"]) for i in range(20, 26)]
    write_attest_artifact(root, make_attestation(findings), "20260820T040000Z")
    ingest(root)

    reviewer = memory_mod.recall(root, files=["worker/api/new_thing.py"],
                                 role="reviewer")
    examiner = memory_mod.recall(root, files=["worker/api/new_thing.py"],
                                 role="examiner")
    assert reviewer and examiner
    assert all(r["status"] in ("confirmed", "fixed") for r in reviewer)
    assert all(r["status"] in ("refuted", "dismissed-with-reason") for r in examiner)
    assert len(reviewer) <= memory_mod.RECALL_K
    rendered = memory_mod.render_recall(reviewer, "reviewer")
    assert len(rendered) <= memory_mod.RECALL_BUDGET + 200  # header allowance
    # disjoint priors: no record appears in both splits
    assert not ({r["id"] for r in reviewer} & {r["id"] for r in examiner})


def test_recall_keys_are_dir_prefix_and_tags_not_paths(root):
    findings = [finding(0, file="worker/api/old_name.py", tags=["fences"])]
    write_attest_artifact(root, make_attestation(findings), "20260820T040000Z")
    ingest(root)
    # renamed file, same directory -> still recalled
    hit = memory_mod.recall(root, files=["worker/api/renamed.py"], role="reviewer")
    assert len(hit) == 1
    # unrelated directory, but matching tag -> recalled via tags
    tag_hit = memory_mod.recall(root, files=["ios/View.swift"], role="reviewer",
                                tags=["fences"])
    assert len(tag_hit) == 1
    # unrelated directory, no tags -> nothing
    miss = memory_mod.recall(root, files=["ios/View.swift"], role="reviewer")
    assert miss == []


def test_fixed_records_are_marked_not_open(root):
    findings = [finding(0, status="fixed")]
    write_attest_artifact(root, make_attestation(findings), "20260820T040000Z")
    ingest(root)
    records = memory_mod.recall(root, files=["worker/api/x.py"], role="reviewer")
    rendered = memory_mod.render_recall(records, "reviewer")
    assert "since FIXED" in rendered and "do not report as open" in rendered


# ---------- acceptance: stats guardrail visibly working ----------------------

def test_no_rule_promotable_at_low_n_and_wilson_math(root):
    findings = [finding(i) for i in range(6)]  # n=6 < 10, all confirmed
    write_attest_artifact(root, make_attestation(findings), "20260820T040000Z")
    ingest(root)
    doc = memory_mod.stats(root)
    assert doc["promotable"] == []  # guardrail: N too small even at 100% precision
    row = doc["per_rule"]["scope-creep"]
    assert row["n"] == 6 and row["confirmed"] == 6
    assert 0 < row["wilson_lb"] < 1
    rendered = memory_mod.render_stats(doc)
    assert "precision only" in rendered and "guardrail holding" in rendered


def test_stats_teaches_the_microtest_protocol_beside_the_bar(root):
    """The bar says WHEN a rule may be promoted; the
    micro-test protocol says what EVIDENCE the proposal carries. The reader
    who acts on `promotable` learns both halves in the same render."""
    findings = [finding(i) for i in range(6)]
    write_attest_artifact(root, make_attestation(findings), "20260820T040000Z")
    ingest(root)
    rendered = memory_mod.render_stats(memory_mod.stats(root))
    assert ("%d+ reps per variant" % memory_mod.MICROTEST_MIN_REPS) in rendered
    assert "control arm" in rendered
    assert "unvalidated" in rendered


def test_promotion_requires_both_n_and_wilson(root):
    good = [finding(i, rule="good-rule") for i in range(12)]
    noisy = [finding(i, rule="noisy-rule",
                     status="confirmed" if i % 2 else "refuted") for i in range(12)]
    write_attest_artifact(root, make_attestation(good + noisy), "20260820T040000Z")
    ingest(root)
    doc = memory_mod.stats(root)
    assert doc["promotable"] == ["good-rule"]  # n=12, WLB(12/12) ~ 0.76
    assert "noisy-rule" not in doc["promotable"]


def _round(root: Path, n: int, findings: list[dict]) -> None:
    """Write one REVIEW ROUND — its own attestation, its own head sha, its own
    reviewed_at. A streak is a trend across these, so a fixture that means
    "three rounds" has to write three."""
    write_attest_artifact(
        root,
        make_attestation(findings, reviewed_at=f"2026-08-2{n}T04:00:00+00:00",
                         head=f"{n:040x}"),
        f"2026082{n}T040000Z")


def test_pause_after_three_straight_refutations(root):
    _round(root, 0, [finding(0, rule="flaky-rule", status="confirmed")])
    for n in (1, 2, 3):
        _round(root, n, [finding(n, rule="flaky-rule", status="refuted")])
    ingest(root)
    doc = memory_mod.stats(root)
    assert doc["pause_candidates"] == ["flaky-rule"]


def test_one_round_of_refutations_is_not_a_pause_candidate(root):
    """The same arithmetic over the real ingest path: a single round filing
    PAUSE_STREAK refutations for a rule it also upheld is one round's opinion,
    not a trend, and must not reach the pause list."""
    _round(root, 0,
           [finding(0, rule="flaky-rule", status="confirmed")]
           + [finding(i, rule="flaky-rule", status="refuted")
              for i in range(1, memory_mod.PAUSE_STREAK + 3)])
    ingest(root)
    doc = memory_mod.stats(root)
    assert doc["per_rule"]["flaky-rule"]["refuted"] >= memory_mod.PAUSE_STREAK, \
        "fixture invalid: the round files fewer refutations than the pause bar"
    assert doc["pause_candidates"] == []


def test_wilson_lower_bound_reference_values():
    assert memory_mod.wilson_lower_bound(0, 0) == 0.0
    assert abs(memory_mod.wilson_lower_bound(10, 10) - 0.722) < 0.005
    assert memory_mod.wilson_lower_bound(9, 10) < 0.7  # 90% at n=10 not promotable
    assert memory_mod.wilson_lower_bound(50, 50) > 0.9
