"""Ingest enforces the SAME rule_id policy as `attest write`.

`warden attest write` rejects any finding whose rule_id neither resolves
against the declared ruleset nor is a well-formed `unmapped:<defect-class>`.
That invariant alone guards only the WRITING end of the pipe. `memory ingest`
globs `.warden/out/*-attest/attestation.json` and normalizes whatever it finds
into committed shards, so without its own check any attestation not produced
by the CLI — hand-edited, written by an older platform version, or
constructed directly by a skill — would enter the corpus carrying ids attest
itself would refuse. The promotion gate and candidate ranking read the CORPUS,
not the CLI: an invariant enforced only at the writing end of a pipe does not
hold for the reader.

Rejection is per ARTIFACT and the sweep continues — the established
`skipped`-malformed pattern. Validation sits at the artifact -> shard seam
only: shards already committed still load (see
test_legacy_shards_still_load_for_recall_and_stats), because this check stops
new invalid records arriving, it does not retroactively invalidate evidence.
"""

import json
from pathlib import Path

import pytest
from conftest import seed_rules

from warden import memory as memory_mod
from warden import rules as rules_mod

DECLARED = ("scope-creep", "secrets-in-diff")


def attestation(findings, *, reviewed_at="2026-08-24T01:00:00+00:00",
                head="c" * 40) -> dict:
    return {"head_sha": head, "base_sha": "a" * 40, "rules_version": "v1",
            "reviewed_at": reviewed_at,
            "reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
            "findings": findings, "verdict": "findings-open"}


def finding(rule_id, *, file="src/api/x.py", status="confirmed") -> dict:
    return {"rule_id": rule_id, "severity": "MEDIUM", "file": file, "line": 1,
            "finding": "f", "evidence": "e", "status": status}


def write_attest(root: Path, stamp: str, doc: dict) -> None:
    d = root / ".warden" / "out" / f"{stamp}-attest"
    d.mkdir(parents=True)
    (d / "attestation.json").write_text(json.dumps(doc) + "\n")


def write_gate(root: Path, stamp: str, findings: list[dict]) -> None:
    d = root / ".warden" / "out" / f"{stamp}-review"
    d.mkdir(parents=True)
    (d / "review-findings.json").write_text(json.dumps({
        "rules_version": "v1", "engine": "warden", "base_sha": "a" * 40,
        "head_sha": "b" * 40, "findings": findings,
        "deferred_to_pre_pr": [], "paused": []}))


def ingest(root: Path, *ids: str, **kwargs) -> dict:
    return memory_mod.ingest(root, rules_dir=seed_rules(root, *(ids or DECLARED)),
                             **kwargs)


# ---------- ids attest refuses cannot walk in through ingest ---------------

@pytest.mark.parametrize("rule_id, reason", [
    ("general", "names no declared rule"),
    ("docs-drift", "names no declared rule"),
    ("Scope Creep", "names no declared rule"),
    ("unmapped:general", "names no defect class"),
    ("unmapped:Docs Drift", "is not a well-formed"),
    # `unmapped:scope-creep` (a declared id behind the prefix) is deliberately
    # absent: attest still refuses it at write time, but ingest MIGRATES it —
    # see the covered-legacy section below.
])
def test_ingest_rejects_a_rule_id_attest_write_would_refuse(tmp_path, rule_id, reason):
    write_attest(tmp_path, "20260824T010000Z", attestation([finding(rule_id)]))
    result = ingest(tmp_path)
    assert result["total_records"] == 0, \
        f"{rule_id!r} entered the corpus through the reading end of the pipe"
    assert result["new_shards"] == []
    assert not list((memory_mod.memory_dir(tmp_path) / "attest").glob("*.json"))
    assert len(result["invalid_rule_ids"]) == 1
    assert reason in result["invalid_rule_ids"][0]


def test_ingest_accepts_declared_and_well_formed_unmapped_ids(tmp_path):
    write_attest(tmp_path, "20260824T010000Z", attestation(
        [finding("scope-creep"), finding("unmapped:fail-open", file="src/api/y.py")]))
    result = ingest(tmp_path)
    assert result["invalid_rule_ids"] == []
    assert result["total_records"] == 2
    assert sorted(r["rule_id"] for r in memory_mod.load_records(tmp_path)) == \
        ["scope-creep", "unmapped:fail-open"]


def test_a_paused_rule_still_resolves_at_ingest(tmp_path):
    """A pause keeps the rule's id and its history — attesting and ingesting
    against one is not an error (attest._check_rule_ids docstring)."""
    rules_dir = seed_rules(tmp_path, "scope-creep")
    (rules_dir / "scope-creep.md").write_text(
        "---\nid: scope-creep\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\npaused: true\npaused_reason: retro proposal\n'
        "---\npaused rule\n")
    write_attest(tmp_path, "20260824T010000Z", attestation([finding("scope-creep")]))
    result = memory_mod.ingest(tmp_path, rules_dir=rules_dir)
    assert result["invalid_rule_ids"] == []
    assert result["total_records"] == 1


# ---------- rejection must not abort the sweep ------------------------------

def test_one_bad_artifact_does_not_stop_the_sweep_or_the_cache_rebuild(tmp_path):
    """`ingest` rebuilds the whole cache from every shard at the end; aborting
    mid-sweep would leave it half-rebuilt. Same contract as `skipped`."""
    write_attest(tmp_path, "20260824T010000Z", attestation([finding("general")]))
    write_attest(tmp_path, "20260824T020000Z", attestation(
        [finding("scope-creep")], reviewed_at="2026-08-24T02:00:00+00:00",
        head="d" * 40))
    result = ingest(tmp_path)
    assert len(result["invalid_rule_ids"]) == 1
    assert len(result["new_shards"]) == 1
    assert [r["rule_id"] for r in memory_mod.load_records(tmp_path)] == ["scope-creep"]


def test_rejection_is_per_artifact_not_per_finding(tmp_path):
    """A shard is one review EVENT. Dropping only the bad finding would file a
    shard whose records silently disagree with the artifact it names."""
    write_attest(tmp_path, "20260824T010000Z", attestation(
        [finding("scope-creep"), finding("general", file="src/api/y.py")]))
    result = ingest(tmp_path)
    assert result["total_records"] == 0
    assert len(result["invalid_rule_ids"]) == 1


def test_malformed_and_invalid_are_reported_as_distinct_classes(tmp_path):
    bad_json = tmp_path / ".warden" / "out" / "20260824T000000Z-attest"
    bad_json.mkdir(parents=True)
    (bad_json / "attestation.json").write_text("{not json")
    write_attest(tmp_path, "20260824T010000Z", attestation([finding("general")]))
    result = ingest(tmp_path)
    assert len(result["skipped"]) == 1 and len(result["invalid_rule_ids"]) == 1
    assert "general" in result["invalid_rule_ids"][0]


def test_gate_artifacts_are_validated_at_the_same_seam(tmp_path):
    """review.py stamps gate rule_ids from the ruleset, so a gate artifact
    carrying an undeclared id was not produced by warden either."""
    write_gate(tmp_path, "20260824T010000Z", [
        {"rule_id": "no-such-rule", "severity": "HIGH", "file": "src/a.py",
         "line": 3, "finding": "k", "evidence": "e"}])
    result = ingest(tmp_path)
    assert result["total_records"] == 0
    assert len(result["invalid_rule_ids"]) == 1
    assert not list((memory_mod.memory_dir(tmp_path) / "gate").glob("*.json"))


# ---------- covered legacy ids migrate at the reader seam -------------------
#
# attest's write-side refusal reaches an AUTHOR who can fix the id; ingest's
# rejection hits legacy artifacts with no author present, so rejecting a
# committed-era id a declared rule now answers would reject it forever —
# permanent, loudly repeated corpus loss (REJECTED printed on every single
# ingest). The reader seam migrates what the writer refuses: the
# record lands under the covering rule with the legacy id kept as provenance.

def covers_rules(root: Path) -> Path:
    rules_dir = seed_rules(root, "scope-creep")
    (rules_dir / "wiki-fidelity.md").write_text(
        "---\nid: wiki-fidelity\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\ncovers: [docs-drift]\n---\nfixture rule\n')
    return rules_dir


def test_bare_covered_legacy_id_migrates_to_the_covering_rule(tmp_path):
    """A bare rule_id 'docs-drift' predating the wiki-fidelity rule that
    ships covers:[docs-drift] migrates onto that rule."""
    write_attest(tmp_path, "20260823T151100Z", attestation([finding("docs-drift")]))
    result = memory_mod.ingest(tmp_path, rules_dir=covers_rules(tmp_path))
    assert result["invalid_rule_ids"] == []
    assert result["total_records"] == 1
    assert result["migrated_rule_ids"] == \
        ["20260823T151100Z-attest: docs-drift -> wiki-fidelity"]
    rec = memory_mod.load_records(tmp_path)[0]
    assert rec["rule_id"] == "wiki-fidelity"
    assert rec["legacy_rule_id"] == "docs-drift", \
        "migration must be recorded in the shard, not applied silently"
    assert "wiki-fidelity" in memory_mod.stats(tmp_path)["per_rule"]


def test_unmapped_covered_slug_migrates_the_same_way(tmp_path):
    write_attest(tmp_path, "20260823T151100Z",
                 attestation([finding("unmapped:docs-drift")]))
    result = memory_mod.ingest(tmp_path, rules_dir=covers_rules(tmp_path))
    assert result["invalid_rule_ids"] == []
    rec = memory_mod.load_records(tmp_path)[0]
    assert rec["rule_id"] == "wiki-fidelity"
    assert rec["legacy_rule_id"] == "unmapped:docs-drift"


def test_unmapped_declared_id_migrates_to_the_declared_rule(tmp_path):
    """The identity link in covered_classes: a declared id hidden behind the
    unmapped: prefix lands on the rule itself, exactly where attest's
    write-side error message tells the author to file it."""
    write_attest(tmp_path, "20260823T151100Z",
                 attestation([finding("unmapped:scope-creep")]))
    result = ingest(tmp_path)
    assert result["invalid_rule_ids"] == []
    rec = memory_mod.load_records(tmp_path)[0]
    assert rec["rule_id"] == "scope-creep"
    assert rec["legacy_rule_id"] == "unmapped:scope-creep"


def test_migration_never_double_shards_an_already_sharded_event(tmp_path):
    """migration-duplicate-event-shards. Era 1: `unmapped:docs-drift` is a
    valid candidate id and the artifact shards under it. Era 2: a rule ships
    `covers: [docs-drift]`; the artifact still sits in .warden/out, and
    migrating it changes every record id and the shard digest — a SECOND
    shard for the same event, review_events counting one review as two and
    the same judgment feeding both the candidate and the rule's Wilson
    bound. A migrated artifact whose event already has a shard must not
    shard again."""
    write_attest(tmp_path, "20260823T151100Z",
                 attestation([finding("unmapped:docs-drift")]))
    era1 = memory_mod.ingest(tmp_path, rules_dir=seed_rules(tmp_path, "scope-creep"))
    assert len(era1["new_shards"]) == 1 and era1["migrated_rule_ids"] == []

    era2 = memory_mod.ingest(tmp_path, rules_dir=covers_rules(tmp_path))
    assert era2["new_shards"] == [], "one review event, one shard — ever"
    assert era2["migrated_rule_ids"] == [], \
        "a skipped write must not report a migration that never sharded"
    shards = list((memory_mod.memory_dir(tmp_path) / "attest").glob("*.json"))
    assert len(shards) == 1
    assert memory_mod.stats(tmp_path)["review_events"]["total"] == 1
    assert era2["total_records"] == 1


def test_migration_report_is_quiet_on_reingest(tmp_path):
    """The first ingest shards and reports the migration; re-running ingest
    (idempotent by contract) must not re-report a migration that produced
    nothing new."""
    write_attest(tmp_path, "20260823T151100Z", attestation([finding("docs-drift")]))
    first = memory_mod.ingest(tmp_path, rules_dir=covers_rules(tmp_path))
    assert len(first["migrated_rule_ids"]) == 1
    second = memory_mod.ingest(tmp_path, rules_dir=covers_rules(tmp_path))
    assert second["new_shards"] == [] and second["migrated_rule_ids"] == []


def test_migration_never_rescues_a_genuinely_unknown_id(tmp_path):
    """Only ids a declared rule ANSWERS migrate — everything else keeps the
    ingest rejection, or the reader seam reopens the free-form id hole."""
    write_attest(tmp_path, "20260823T151100Z", attestation([finding("mystery-id")]))
    result = memory_mod.ingest(tmp_path, rules_dir=covers_rules(tmp_path))
    assert result["total_records"] == 0
    assert len(result["invalid_rule_ids"]) == 1
    assert result["migrated_rule_ids"] == []


def test_migration_is_not_reported_for_a_rejected_artifact(tmp_path):
    """Rejection stays per-artifact: a migratable finding riding with an
    invalid one must not be reported as migrated when the whole artifact is
    rejected."""
    write_attest(tmp_path, "20260823T151100Z", attestation(
        [finding("docs-drift"), finding("general", file="src/api/y.py")]))
    result = memory_mod.ingest(tmp_path, rules_dir=covers_rules(tmp_path))
    assert result["total_records"] == 0
    assert result["migrated_rule_ids"] == []


# ---------- the corpus already committed must keep loading ------------------

def test_legacy_shards_still_load_for_recall_and_stats(tmp_path):
    """Validation guards the artifact -> shard seam ONLY. Three shards
    committed before write-side rule_id validation carry `general`,
    `docs-drift` and `graph-fidelity`; migrating them is separate work.
    Re-ingesting must not
    silently drop the evidence they hold."""
    shard_dir = memory_mod.memory_dir(tmp_path) / "attest"
    shard_dir.mkdir(parents=True)
    legacy = {
        "schema": 1, "source": "attest", "sha": "e" * 40, "base_sha": "a" * 40,
        "rules_version": "v0", "reviewed_at": "2026-08-23T15:11:00+00:00",
        "records": [{"id": "legacy1", "ts": "2026-08-23T15:11:00+00:00",
                     "seq": 0, "source": "attest", "sha": "e" * 40,
                     "rule_id": rid, "tags": [], "dir_prefix": "src/api",
                     "file": "src/api/legacy.py", "line": 1,
                     "severity": "MEDIUM", "finding": f"legacy {rid}",
                     "evidence": "e", "status": "confirmed", "origin": "day"}
                    for rid in ("general", "docs-drift", "graph-fidelity")]}
    for i, rec in enumerate(legacy["records"]):
        rec["id"] = f"legacy{i}"
    (shard_dir / "20260823T151100Z-eeeeeeee-legacy00.json").write_text(
        json.dumps(legacy, indent=2) + "\n")

    result = ingest(tmp_path)
    assert result["invalid_rule_ids"] == []
    assert result["total_records"] == 3
    assert set(memory_mod.stats(tmp_path)["per_rule"]) == \
        {"general", "docs-drift", "graph-fidelity"}
    recalled = memory_mod.recall(tmp_path, files=["src/api/z.py"], role="reviewer")
    assert len(recalled) == 3


def test_a_serialization_change_never_re_shards_a_filed_event(tmp_path):
    """A change to record serialization never re-shards a filed event.

    The shard filename carries a content digest, so ANY change to how a
    record serializes — a new field, a renamed default (e.g. `origin` from
    'day' to 'interactive') — gives every artifact still in .warden/out a new
    filename on the next ingest, and without this guard the corpus grows a
    second shard for a review that happened once.

    The migration guard already covers MIGRATED artifacts. The invariant is not
    about migration: one review event, one shard — ever. Identity is the
    event (its timestamp, its head sha, its records), never the bytes of
    the moment it was filed.
    """
    write_attest(tmp_path, "20260825T010000Z", attestation([finding("scope-creep")]))
    rules = seed_rules(tmp_path, *DECLARED)
    first = memory_mod.ingest(tmp_path, rules_dir=rules)
    assert len(first["new_shards"]) == 1

    # the same artifact, ingested under a changed record serialization
    second = memory_mod.ingest(tmp_path, rules_dir=rules, origin="unattended")
    assert second["new_shards"] == [], "a filed event was re-sharded"
    shards = list((memory_mod.memory_dir(tmp_path) / "attest").glob("*.json"))
    assert len(shards) == 1
    assert memory_mod.stats(tmp_path)["review_events"]["total"] == 1


def test_two_distinct_events_tied_on_timestamp_and_sha_both_survive(tmp_path):
    """The property the content digest exists for, still holding: distinct
    events that tie on ts+sha are two files, not one overwriting the other.
    The guard above keys on the event's RECORDS, so it must not collapse
    these."""
    rules = seed_rules(tmp_path, *DECLARED)
    write_attest(tmp_path, "20260825T020000Z", attestation([finding("scope-creep")]))
    memory_mod.ingest(tmp_path, rules_dir=rules)
    # a second, genuinely different review of the same commit at the same ts
    write_attest(tmp_path, "20260825T020001Z", attestation(
        [finding("secrets-in-diff", file="src/api/other.py")]))
    memory_mod.ingest(tmp_path, rules_dir=rules)
    shards = list((memory_mod.memory_dir(tmp_path) / "attest").glob("*.json"))
    assert len(shards) == 2, "a distinct event was swallowed by the guard"


def test_shadowed_matches_validator(tmp_path):
    """`shadowed_rule_ids` is the same reading as `_check_rule_ids`: both
    read `_RuleIndex.shadowing`, and this is the guard that says so. With
    re-typed predicates nothing pins equal, a third refusal branch in the
    validator would silently drop certify's E-02 back to the truncated
    per-shard message with no test failing.

    The property: for every id, `shadowed_rule_ids` names a rule EXACTLY when
    the validator refuses that id for being shadowed — no more, no less.
    """
    from warden import attest as attest_mod

    d = seed_rules(tmp_path, "tests-bite")
    (d / "wiki-fidelity.md").write_text(
        "---\nid: wiki-fidelity\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\ncovers: [docs-drift, stale-self-description]\n"
        "---\nbody\n")
    (d / "dormant.md").write_text(
        "---\nid: dormant-rule\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\ncovers: [sleepy-class]\n"
        "paused: true\npaused_reason: refutation streak\n---\nbody\n")

    ids = [
        "unmapped:tests-bite",             # shadowed: a rule id IS the slug
        "unmapped:docs-drift",             # shadowed: a rule covers it
        "unmapped:stale-self-description",  # shadowed: second covers entry
        "unmapped:fail-open",              # legal candidate, nothing covers it
        "unmapped:sleepy-class",           # NOT shadowed: covering rule paused
        "unmapped:dormant-rule",           # shadowed: even a paused rule's id
        "unmapped:general",                # refused, but NOT shadowed
        "unmapped:Not A Slug",             # refused, but NOT shadowed
        "tests-bite",                      # a declared rule id: accepted
        "ghost",                           # refused, but NOT shadowed
    ]
    for rid in ids:
        finding = [{"rule_id": rid, "file": "x.py"}]
        shadowed = attest_mod.shadowed_rule_ids(finding, d)
        try:
            attest_mod._check_rule_ids(finding, d)
        except attest_mod.AttestError as e:
            refused_as_shadow = "hides declared rule" in str(e) or \
                "already covers" in str(e)
            assert e.rule_id == rid, \
                f"the error must name the id that raised it: {rid} vs {e.rule_id}"
        else:
            refused_as_shadow = False
        assert bool(shadowed) == refused_as_shadow, (
            f"{rid}: shadowed_rule_ids says {shadowed}, validator "
            f"{'refuses as shadowed' if refused_as_shadow else 'does not'}")
        if refused_as_shadow:
            assert shadowed[rid] in {r.id for r in rules_mod.load_rules(d)}, \
                f"{rid}: named a shadowing rule that is not declared"


def paused_covers_rules(root: Path) -> Path:
    d = covers_rules(root)
    (d / "wiki-fidelity.md").write_text(
        (d / "wiki-fidelity.md").read_text().replace(
            "engine: claude",
            "engine: claude\npaused: true\npaused_reason: refutation streak"))
    return d


def test_covered_slug_files_under_its_own_slug_while_paused(tmp_path):
    """The read end of pausing a covering rule. `memory stats` re-opens a
    paused rule's class as a candidate, so ingest must stop migrating
    `unmapped:docs-drift` onto the paused rule; otherwise the re-opened row's
    tally stays frozen at pre-pause history — the proposal's own evidence
    channel blocked. While the covering rule is paused the slug
    is a live candidate key: accepted as-is, not migrated, and the candidate
    row accrues it, naming the paused rule."""
    d = paused_covers_rules(tmp_path)
    write_attest(tmp_path, "20260823T151100Z",
                 attestation([finding("unmapped:docs-drift")]))
    result = memory_mod.ingest(tmp_path, rules_dir=d)
    assert result["invalid_rule_ids"] == []
    assert result["migrated_rule_ids"] == [], \
        "a live candidate key needs no rescue — migrating it re-froze the row"
    rec = memory_mod.load_records(tmp_path)[0]
    assert rec["rule_id"] == "unmapped:docs-drift"
    assert "legacy_rule_id" not in rec
    rows = {r["slug"]: r for r in
            memory_mod.stats(tmp_path, d)["candidates"]}
    assert rows["docs-drift"]["n"] == 1, "the candidate row must accrue"
    assert rows["docs-drift"]["paused_covered_by"] == "wiki-fidelity"


def test_pause_flip_never_double_files_an_event(tmp_path):
    """Fail-closed. Routing for `unmapped:<covered-slug>` flips with pause
    state, and record ids hash rule_id — so a still-present artifact
    re-swept after the covering rule paused matches no filed shard by
    record-set comparison and, unguarded, files a SECOND shard for one review
    event: the same judgment counted once toward the rule's precision and
    once as fresh recurrence on the re-opened candidate row. One event, one
    shard — across pause flips too."""
    write_attest(tmp_path, "20260823T151100Z",
                 attestation([finding("unmapped:docs-drift")]))
    first = memory_mod.ingest(tmp_path, rules_dir=covers_rules(tmp_path))
    assert len(first["new_shards"]) == 1
    # the rule pauses (a normal refutation-streak action); the artifact
    # still sits in .warden/out and the next ingest re-sweeps it
    second = memory_mod.ingest(tmp_path,
                               rules_dir=paused_covers_rules(tmp_path))
    assert second["new_shards"] == [], "one review event, one shard — ever"
    shards = list((memory_mod.memory_dir(tmp_path) / "attest").glob("*.json"))
    assert len(shards) == 1
    assert memory_mod.stats(tmp_path)["review_events"]["total"] == 1


def test_unpause_flip_never_double_files_an_event(tmp_path):
    """The reverse flip of the regression above, pinned: filed under the
    slug during a pause, the artifact re-swept after unpause migrates, and
    the migration branch's prefix guard must skip the already-filed event
    (and not report a migration that never sharded)."""
    write_attest(tmp_path, "20260823T151100Z",
                 attestation([finding("unmapped:docs-drift")]))
    first = memory_mod.ingest(tmp_path,
                              rules_dir=paused_covers_rules(tmp_path))
    assert len(first["new_shards"]) == 1
    second = memory_mod.ingest(tmp_path, rules_dir=covers_rules(tmp_path))
    assert second["new_shards"] == [] and second["migrated_rule_ids"] == []
    shards = list((memory_mod.memory_dir(tmp_path) / "attest").glob("*.json"))
    assert len(shards) == 1


def test_tied_distinct_events_survive_routing_sensitive_dedup(tmp_path):
    """A routing-sensitive dedup guard keyed on filename PREFIX (ts+sha)
    would silently drop a DISTINCT review event tying on ts+sha with an
    already-filed one — never sharded, never reported — whenever its
    findings involve a covers: link (pause_kept or migrated).
    Two same-second attestations of one head are constructible on the real
    write path (run dirs are microsecond-stamped; reviewed_at is
    seconds-stamped). Identity is the EVENT: both must shard, in both
    routing directions."""
    d = paused_covers_rules(tmp_path)
    write_attest(tmp_path, "20260824T010000Z",
                 attestation([finding("unmapped:fail-open")]))
    first = memory_mod.ingest(tmp_path, rules_dir=d)
    assert len(first["new_shards"]) == 1
    # a distinct second round, same reviewed_at + head, pause-kept finding
    write_attest(tmp_path, "20260824T010000Z2",
                 attestation([finding("unmapped:docs-drift",
                                      file="src/api/other.py")]))
    second = memory_mod.ingest(tmp_path, rules_dir=d)
    assert len(second["new_shards"]) == 1, \
        "a distinct tied event was silently swallowed by the prefix guard"
    # and the migrated direction: a third distinct tied event whose finding
    # migrates (unpause the rule) must also file, not ride the stranded-
    # event skip
    write_attest(tmp_path, "20260824T010000Z3",
                 attestation([finding("docs-drift", file="src/api/third.py")]))
    third = memory_mod.ingest(tmp_path, rules_dir=covers_rules(tmp_path))
    assert len(third["new_shards"]) == 1, \
        "a distinct tied migrated event was swallowed by the prefix guard"
    shards = list((memory_mod.memory_dir(tmp_path) / "attest").glob("*.json"))
    assert len(shards) == 3
    # idempotency still holds for every one of them
    again = memory_mod.ingest(tmp_path, rules_dir=covers_rules(tmp_path))
    assert again["new_shards"] == []
    assert len(list((memory_mod.memory_dir(tmp_path) / "attest")
                    .glob("*.json"))) == 3


def test_unmapped_declared_id_still_migrates_while_paused(tmp_path):
    """Identity is pause-proof both ways: a declared id hidden behind the
    unmapped: prefix is a spelling the writer refuses paused or not, so the
    reader still rescues it onto the rule itself — the id never frees."""
    d = paused_covers_rules(tmp_path)
    write_attest(tmp_path, "20260823T151100Z",
                 attestation([finding("unmapped:wiki-fidelity")]))
    result = memory_mod.ingest(tmp_path, rules_dir=d)
    assert result["invalid_rule_ids"] == []
    rec = memory_mod.load_records(tmp_path)[0]
    assert rec["rule_id"] == "wiki-fidelity"
    assert rec["legacy_rule_id"] == "unmapped:wiki-fidelity"


def test_a_paused_covering_rule_still_receives_its_legacy_ids(tmp_path):
    """Migration is an IDENTITY question, not an enforcement one: a paused
    rule's covers: stops counting as coverage in stats, but filtering it HERE
    would re-open the permanent corpus loss reader-seam migration closes — a
    legacy docs-drift record rejected on every
    ingest for as long as wiki-fidelity stays paused."""
    d = paused_covers_rules(tmp_path)
    write_attest(tmp_path, "20260823T151100Z",
                 attestation([finding("docs-drift")]))
    result = memory_mod.ingest(tmp_path, rules_dir=d)
    assert result["invalid_rule_ids"] == []
    rec = memory_mod.load_records(tmp_path)[0]
    assert rec["rule_id"] == "wiki-fidelity"
    assert rec["legacy_rule_id"] == "docs-drift"
