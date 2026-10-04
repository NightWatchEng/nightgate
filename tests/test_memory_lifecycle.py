"""Memory lifecycle and tag vocabulary.

Two failure modes the memory articles name explicitly:
  - memory that only grows: old findings outrank current ones until the
    reader is deciding from a pile of half-truths
  - vocabulary that fragments: two names for one defect class split the
    recall index, silently — nothing errors, recall just stops firing
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from conftest import seed_rules

from warden import memory as memory_mod
from warden import provenance as prov_mod
from warden import tags as tags_mod


def record(rid: str, days_old: int, *, tags=None, status="confirmed") -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return {"id": rid, "ts": ts, "seq": 0, "sha": "s", "rule_id": "r",
            "tags": tags or [], "dir_prefix": "src/api",
            "file": "src/api/x.py", "line": 1, "severity": "HIGH",
            "finding": f"finding {rid}", "evidence": "e",
            "status": status, "origin": "day"}


def seed(root: Path, records: list[dict]) -> None:
    mem = root / ".warden" / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "findings.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records))


# ---------- freshness ------------------------------------------------------

def test_stale_records_stop_being_recalled(tmp_path):
    seed(tmp_path, [record("fresh", 5), record("ancient", 500)])
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    ids = [r["id"] for r in got]
    assert "fresh" in ids
    assert "ancient" not in ids, "a 500-day-old finding is noise in evidence costume"


def test_fresh_outranks_aging(tmp_path):
    # aging record is NEWER in insertion order but older than the horizon
    seed(tmp_path, [record("aging", 200), record("fresh", 10)])
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert [r["id"] for r in got] == ["fresh", "aging"]


def test_stale_records_are_kept_not_deleted(tmp_path):
    """Committed evidence does not expire because attention does."""
    seed(tmp_path, [record("ancient", 500)])
    assert len(memory_mod.load_records(tmp_path)) == 1
    assert memory_mod.recall(tmp_path, files=["src/api/y.py"],
                             role="reviewer") == []


def test_unparseable_timestamp_is_treated_as_fresh(tmp_path):
    bad = record("bad", 0)
    bad["ts"] = "not-a-date"
    seed(tmp_path, [bad])
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert len(got) == 1, "a clock problem must not silently erase history"


def test_stats_report_corpus_age(tmp_path):
    seed(tmp_path, [record("a", 5), record("b", 200), record("c", 500)])
    doc = memory_mod.stats(tmp_path)
    age = doc["corpus_age"]
    assert (age["fresh"], age["aging"], age["stale"]) == (1, 1, 1)
    assert age["oldest_days"] >= 499
    assert "stale" in memory_mod.render_stats(doc)


# ---------- vocabulary -----------------------------------------------------

def write_vocab(root: Path, mapping: dict, aliases: dict | None = None) -> None:
    mem = root / ".warden" / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    doc: dict = {"tags": mapping}
    if aliases:
        doc["aliases"] = aliases
    (mem / "tags.yaml").write_text(yaml.safe_dump(doc))


def test_near_duplicates_are_detected_across_common_variations():
    pairs = tags_mod.near_duplicates(
        ["deploy", "deploys", "fail-open", "failing_open", "docs-drift"])
    flat = {frozenset(p) for p in pairs}
    assert frozenset({"deploy", "deploys"}) in flat
    assert frozenset({"fail-open", "failing_open"}) in flat
    assert not any("docs-drift" in p for p in pairs)


def test_unknown_tags_reported_only_when_a_vocabulary_exists(tmp_path):
    assert tags_mod.unknown_tags(tmp_path, ["anything"]) == []
    write_vocab(tmp_path, {"fail-open": "errs toward passing"})
    assert tags_mod.unknown_tags(tmp_path, ["fail-open", "mystery"]) == ["mystery"]


def test_unknown_tags_warn_but_never_reject(tmp_path):
    """A new defect class must be nameable the moment it is found."""
    write_vocab(tmp_path, {"known": "d"})
    seed(tmp_path, [record("a", 1, tags=["brand-new-class"])])
    doc = memory_mod.stats(tmp_path)
    assert doc["tags"]["unknown"] == ["brand-new-class"]
    assert doc["total_records"] == 1, "the record is still stored and usable"


def test_audit_surfaces_fragmentation_and_dead_vocabulary(tmp_path):
    write_vocab(tmp_path, {"fail-open": "d", "never-used": "d"})
    seed(tmp_path, [record("a", 1, tags=["fail-open"]),
                    record("b", 2, tags=["failing-open"])])
    doc = memory_mod.stats(tmp_path)["tags"]
    assert doc["unknown"] == ["failing-open"]
    assert doc["unused_declared"] == ["never-used"]
    assert any({"fail-open", "failing-open"} == set(p)
               for p in doc["near_duplicates"])
    rendered = tags_mod.render_audit(doc)
    assert "NEAR-DUPLICATES" in rendered and "recall splits" in rendered


def test_no_vocabulary_declared_says_so(tmp_path):
    seed(tmp_path, [record("a", 1, tags=["x"])])
    rendered = tags_mod.render_audit(memory_mod.stats(tmp_path)["tags"])
    assert "no vocabulary declared" in rendered


# ---------- aliases --------------------------------------------------------
#
# Shards are committed evidence and never rewritten, so a tag merged after
# the fact (e.g. test-determinism -> wall-clock-dependence) can
# only be folded at the read seams. Without folding, the retired name warns
# on every ingest forever and the recall index stays split.

def test_alias_folds_at_the_cache_read_seam(tmp_path):
    write_vocab(tmp_path, {"wall-clock-dependence": "d"},
                aliases={"test-determinism": "wall-clock-dependence"})
    seed(tmp_path, [record("a", 1, tags=["test-determinism"])])
    got = memory_mod.load_records(tmp_path)
    assert got[0]["tags"] == ["wall-clock-dependence"]
    doc = memory_mod.stats(tmp_path)["tags"]
    assert "wall-clock-dependence" in doc["used"]
    assert "test-determinism" not in doc["used"]
    assert doc["unknown"] == []


def test_alias_folds_at_the_shard_read_seam(tmp_path):
    write_vocab(tmp_path, {"wall-clock-dependence": "d"},
                aliases={"test-determinism": "wall-clock-dependence"})
    shard_dir = tmp_path / ".warden" / "memory" / "attest"
    shard_dir.mkdir(parents=True)
    (shard_dir / "s.json").write_text(json.dumps(
        {"schema": 1, "records": [record("a", 1, tags=["test-determinism"])]}))
    got = memory_mod.records_from_shards(tmp_path)
    assert got[0]["tags"] == ["wall-clock-dependence"]


def test_alias_and_canonical_on_one_record_fold_to_one_key(tmp_path):
    """Folding must dedupe, or the merge doubles the count it was
    supposed to unify."""
    write_vocab(tmp_path, {"wall-clock-dependence": "d"},
                aliases={"test-determinism": "wall-clock-dependence"})
    seed(tmp_path, [record("a", 1,
                           tags=["test-determinism", "wall-clock-dependence"])])
    got = memory_mod.load_records(tmp_path)
    assert got[0]["tags"] == ["wall-clock-dependence"]


def test_ingest_canonicalizes_and_does_not_warn_on_aliases(tmp_path):
    write_vocab(tmp_path, {"wall-clock-dependence": "d"},
                aliases={"test-determinism": "wall-clock-dependence"})
    out = tmp_path / ".warden" / "out" / "20260822T000000Z-attest"
    out.mkdir(parents=True)
    (out / "attestation.json").write_text(json.dumps({
        "head_sha": "c" * 40, "base_sha": "a" * 40, "rules_version": "v1",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewers": [{"role": "r", "agent": "a"}], "verdict": "findings-open",
        "findings": [{"rule_id": "r", "severity": "HIGH", "file": "src/a.py",
                      "finding": "f", "evidence": "e", "status": "confirmed",
                      "tags": ["test-determinism"]}]}))
    result = memory_mod.ingest(tmp_path, rules_dir=seed_rules(tmp_path, "r"))
    assert result["unknown_tags"] == []
    cached = memory_mod.load_records(tmp_path)
    assert cached[0]["tags"] == ["wall-clock-dependence"]


def test_alias_folds_unmapped_candidate_slugs_too(tmp_path):
    """Candidates key on `unmapped:` rule_ids, not tags — a merged class must
    stop splitting the candidate ranking, which is the index the rule advisor
    mines: the whole candidate-ranking mechanism keys on these names."""
    write_vocab(tmp_path, {"wall-clock-dependence": "d"},
                aliases={"test-determinism": "wall-clock-dependence"})
    a = record("a", 1, tags=["wall-clock-dependence"])
    b = record("b", 2, tags=["test-determinism"])
    a["rule_id"] = "unmapped:wall-clock-dependence"
    b["rule_id"] = "unmapped:test-determinism"
    seed(tmp_path, [a, b])
    got = {r["id"]: r["rule_id"] for r in memory_mod.load_records(tmp_path)}
    assert got == {"a": "unmapped:wall-clock-dependence",
                   "b": "unmapped:wall-clock-dependence"}
    candidates = memory_mod.stats(tmp_path)["candidates"]
    merged = [c for c in candidates
              if c["slug"] == "wall-clock-dependence"]
    assert len(merged) == 1 and merged[0]["n"] == 2
    assert not any(c["slug"] == "test-determinism" for c in candidates)


def test_alias_never_touches_declared_rule_ids(tmp_path):
    """Only the unmapped namespace folds: a DECLARED rule's id is a promotion
    key bound to backtest artifacts and must never be rewritten by a tag
    alias that happens to share its name."""
    write_vocab(tmp_path, {"wall-clock-dependence": "d"},
                aliases={"test-determinism": "wall-clock-dependence"})
    a = record("a", 1)
    a["rule_id"] = "test-determinism"   # declared-rule namespace, no prefix
    seed(tmp_path, [a])
    got = memory_mod.load_records(tmp_path)
    assert got[0]["rule_id"] == "test-determinism"


def test_recall_query_on_retired_name_still_hits_merged_key(tmp_path):
    """Query-side alias blindness. Records fold at the read seams, so a query
    still using the retired name — often copied straight out of an old shard
    — must fold too, or it matches nothing, silently: the exact
    silent-index-miss the alias mechanism exists to prevent."""
    write_vocab(tmp_path, {"wall-clock-dependence": "d"},
                aliases={"test-determinism": "wall-clock-dependence"})
    a = record("a", 1, tags=["test-determinism"])
    a["rule_id"] = "unmapped:test-determinism"
    seed(tmp_path, [a])
    # files far from the record's dir_prefix: tag/rule keys are the only route
    by_tag = memory_mod.recall(tmp_path, files=["docs/z.md"], role="reviewer",
                               tags=["test-determinism"])
    assert [r["id"] for r in by_tag] == ["a"]
    by_rule = memory_mod.recall(tmp_path, files=["docs/z.md"], role="reviewer",
                                rules=["unmapped:test-determinism"])
    assert [r["id"] for r in by_rule] == ["a"]


def test_invalid_alias_is_reported_and_not_applied(tmp_path):
    """An alias that could lose information never fires: a target outside the
    vocabulary would move records onto an undeclared key, and an alias name
    that is itself declared would silently vanish a live tag."""
    write_vocab(tmp_path, {"fail-open": "d", "docs-drift": "d"},
                aliases={"mystery-class": "not-declared",
                         "fail-open": "docs-drift"})
    seed(tmp_path, [record("a", 1, tags=["mystery-class"]),
                    record("b", 2, tags=["fail-open"])])
    doc = memory_mod.stats(tmp_path)["tags"]
    assert doc["unknown"] == ["mystery-class"], "invalid alias must not fold"
    assert "fail-open" in doc["used"], "a declared tag must never be aliased away"
    assert len(doc["invalid_aliases"]) == 2
    rendered = tags_mod.render_audit(doc)
    assert "INVALID ALIASES" in rendered


# ---------- aliases vs the rule namespace -----------------------------------
#
# One alias table folds TWO namespaces: tags, and the `unmapped:<slug>` half
# of rule_ids. So an alias that is perfectly valid in tag space can still move
# candidate evidence onto a key `attest write` refuses to create.


def _covering_ruleset(root: Path, rule_id: str, covered: str) -> Path:
    """A declared rule that `covers:` a candidate class — the shape that makes
    `unmapped:<covered>` unwritable (`wiki-fidelity` covers `docs-drift`)."""
    rules_dir = seed_rules(root, rule_id)
    path = rules_dir / f"{rule_id}.md"
    path.write_text(path.read_text().replace(
        "engine: claude", f"engine: claude\ncovers: [{covered}]"))
    return rules_dir


def test_alias_onto_a_covered_class_is_refused_at_every_seam(tmp_path):
    """The acceptance scenario: `doc-drift -> docs-drift` is
    valid in TAG space, but `wiki-fidelity` covers `docs-drift`, so folding
    `unmapped:doc-drift` onto `unmapped:docs-drift` writes a corpus key
    `attest write` refuses — the judgments would land as a covered candidate
    row instead of counting toward the covering rule's precision."""
    _covering_ruleset(tmp_path, "wiki-fidelity", "docs-drift")
    write_vocab(tmp_path, {"docs-drift": "d"},
                aliases={"doc-drift": "docs-drift"})

    applicable, problems = tags_mod._split_aliases(tmp_path)
    assert applicable == {}
    assert len(problems) == 1 and "docs-drift" in problems[0]

    # Every seam that folds reads that one verdict: records, queries, the
    # provenance tally, and the audit that reports it.
    records = [{"rule_id": "unmapped:doc-drift", "tags": ["doc-drift"]}]
    folded = tags_mod.canonicalize_records(tmp_path, records)
    assert folded[0]["rule_id"] == "unmapped:doc-drift"
    assert folded[0]["tags"] == ["doc-drift"]
    assert tags_mod.canonicalize_query(
        tmp_path, tags=["doc-drift"], rules=["unmapped:doc-drift"]) == (
            {"doc-drift"}, {"unmapped:doc-drift"})
    assert prov_mod.load_vocab_and_aliases(tmp_path)[1] == {}
    doc = tags_mod.audit(tmp_path, records)
    assert doc["invalid_aliases"] == problems
    assert "INVALID ALIASES" in tags_mod.render_audit(doc)


def test_alias_named_for_a_covered_class_is_refused(tmp_path):
    """An alias whose NAME is a covered class would detach that rule's
    committed evidence — `unmapped:overbroad-pattern` records would stop
    counting toward `pattern-fit` — so it is refused."""
    _covering_ruleset(tmp_path, "pattern-fit", "overbroad-pattern")
    write_vocab(tmp_path, {"false-positive": "d"},
                aliases={"overbroad-pattern": "false-positive"})
    applicable, problems = tags_mod._split_aliases(tmp_path)
    assert applicable == {}
    assert len(problems) == 1 and "overbroad-pattern" in problems[0]


def test_alias_onto_a_declared_rule_id_is_refused(tmp_path):
    """The other half of the reserved namespace: a rule whose id IS the slug.
    `unmapped:false-positive` is refused by `attest write` the moment the rule
    ships, so no alias may fold records onto it either."""
    seed_rules(tmp_path, "false-positive")
    write_vocab(tmp_path, {"false-positive": "d"},
                aliases={"false-positives": "false-positive"})
    applicable, problems = tags_mod._split_aliases(tmp_path)
    assert applicable == {}
    assert len(problems) == 1 and "false-positive" in problems[0]


def test_the_rule_namespace_is_read_from_the_declared_rules_dir(tmp_path):
    """The check is DERIVED from the repo root, never guessed: a repo that
    declares `review.rules_dir` elsewhere gets the same refusal, and every
    seam gets it because every seam already passes the root (an inconsistent
    check would fold differently per reader)."""
    (tmp_path / "repo.yaml").write_text("review:\n  rules_dir: policy/rules\n")
    rules_dir = tmp_path / "policy" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "wiki-fidelity.md").write_text(
        "---\nid: wiki-fidelity\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\ncovers: [docs-drift]\n---\nfixture rule\n')
    write_vocab(tmp_path, {"docs-drift": "d"},
                aliases={"doc-drift": "docs-drift"})
    assert tags_mod._split_aliases(tmp_path)[0] == {}
    # ...and the default location is not consulted behind the declaration: a
    # ruleset left at .warden/rules is not this repo's ruleset.
    seed_rules(tmp_path, "unrelated")
    assert tags_mod._split_aliases(tmp_path)[0] == {}


def test_an_alias_colliding_with_nothing_still_folds(tmp_path):
    """The guard must not disable the mechanism: with a ruleset present and no
    collision, the shipped alias shape folds exactly as before."""
    seed_rules(tmp_path, "lang-conventions")
    write_vocab(tmp_path, {"wall-clock-dependence": "d"},
                aliases={"test-determinism": "wall-clock-dependence"})
    applicable, problems = tags_mod._split_aliases(tmp_path)
    assert (applicable, problems) == (
        {"test-determinism": "wall-clock-dependence"}, [])


def test_a_ruleset_edit_is_not_answered_from_a_stale_rule_index(tmp_path):
    """The rule index is memoized — parsing a ruleset costs ~100x folding a
    record, and several seams fold per command. Keyed on the rule FILES, so a
    rule that starts covering a class mid-process refuses the alias that was
    applicable a moment earlier; a cache keyed on the path alone would answer
    from a ruleset that no longer exists."""
    rules_dir = seed_rules(tmp_path, "wiki-fidelity")
    write_vocab(tmp_path, {"docs-drift": "d"},
                aliases={"doc-drift": "docs-drift"})
    assert tags_mod._split_aliases(tmp_path)[0] == {"doc-drift": "docs-drift"}

    path = rules_dir / "wiki-fidelity.md"
    path.write_text(path.read_text().replace(
        "engine: claude", "engine: claude\ncovers: [docs-drift]"))
    assert tags_mod._split_aliases(tmp_path)[0] == {}


def test_ingest_reports_unknown_tags(tmp_path):
    write_vocab(tmp_path, {"known": "d"})
    out = tmp_path / ".warden" / "out" / "20260822T000000Z-attest"
    out.mkdir(parents=True)
    (out / "attestation.json").write_text(json.dumps({
        "head_sha": "c" * 40, "base_sha": "a" * 40, "rules_version": "v1",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewers": [{"role": "r", "agent": "a"}], "verdict": "findings-open",
        "findings": [{"rule_id": "r", "severity": "HIGH", "file": "src/a.py",
                      "finding": "f", "evidence": "e", "status": "confirmed",
                      "tags": ["mystery"]}]}))
    result = memory_mod.ingest(tmp_path, rules_dir=seed_rules(tmp_path, "r"))
    assert result["unknown_tags"] == ["mystery"]


# ---------- origin vocabulary is trigger-neutral ----------------------------
#
# `--origin day|night` named the CLOCK, and the clock is deprecated: a run is
# interactive (a human is present) or unattended (nobody is), whatever hour
# it starts. The write side speaks the new vocabulary; the READ side never
# validates it, so records written under the old words stay readable forever.

def test_ingest_defaults_to_the_interactive_origin(tmp_path):
    write_vocab(tmp_path, {"known": "d"})
    out = tmp_path / ".warden" / "out" / "20260825T000000Z-attest"
    out.mkdir(parents=True)
    (out / "attestation.json").write_text(json.dumps({
        "head_sha": "c" * 40, "base_sha": "a" * 40, "rules_version": "v1",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewers": [{"role": "r", "agent": "a"}], "verdict": "findings-open",
        "findings": [{"rule_id": "r", "severity": "HIGH", "file": "src/a.py",
                      "finding": "f", "evidence": "e", "status": "confirmed",
                      "tags": ["known"]}]}))
    memory_mod.ingest(tmp_path, rules_dir=seed_rules(tmp_path, "r"))
    assert memory_mod.load_records(tmp_path)[0]["origin"] == "interactive"


def test_unattended_origin_is_recorded_as_given(tmp_path):
    write_vocab(tmp_path, {"known": "d"})
    out = tmp_path / ".warden" / "out" / "20260825T010000Z-attest"
    out.mkdir(parents=True)
    (out / "attestation.json").write_text(json.dumps({
        "head_sha": "d" * 40, "base_sha": "a" * 40, "rules_version": "v1",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewers": [{"role": "r", "agent": "a"}], "verdict": "findings-open",
        "findings": [{"rule_id": "r", "severity": "HIGH", "file": "src/a.py",
                      "finding": "f", "evidence": "e", "status": "confirmed",
                      "tags": ["known"]}]}))
    memory_mod.ingest(tmp_path, rules_dir=seed_rules(tmp_path, "r"),
                      origin="unattended")
    assert memory_mod.load_records(tmp_path)[0]["origin"] == "unattended"


def test_legacy_origin_values_stay_readable(tmp_path):
    """Committed records carry origin 'day'/'night' from before the rename.
    The reader never validates the vocabulary — evidence does not become
    unreadable because a word changed."""
    seed(tmp_path, [record("old", 1)])   # helper writes origin: "day"
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert [r["id"] for r in got] == ["old"]
    assert memory_mod.load_records(tmp_path)[0]["origin"] == "day"


def test_origin_is_derived_from_the_caged_environment(tmp_path, monkeypatch):
    """unwired-provenance-field: --origin is a presence claim that no
    producer passes, so a fixed 'interactive' default would stamp every
    unattended run falsely — the corpus asserting something false.

    Presence is a property of the environment, not of the caller's intent,
    and the caged session is exactly where it is knowable: run.sh exports
    CAGE_PROJECT into the session it launches. So the default is DERIVED,
    and an explicit flag still wins."""
    from warden import cli as cli_mod
    monkeypatch.setenv("CAGE_PROJECT", "distill")
    assert cli_mod._default_origin() == "unattended"
    monkeypatch.delenv("CAGE_PROJECT")
    assert cli_mod._default_origin() == "interactive"


def test_an_unreadable_declaration_refuses_aliases_rather_than_firing_them(tmp_path):
    """Fail-closed: `_rule_index` must not collapse "this repo declares no
    ruleset" and "I cannot read this repo's declaration" into the same
    PERMISSIVE answer. Otherwise a consumer declaring `rules_dir: policy/rules`
    with a corrupt repo.yaml falls back to the default path, finds nothing
    there, reads that as "no ruleset exists to collide with", and fires every
    alias — including the `doc-drift -> docs-drift` collision the guard exists
    to refuse. The two inputs nobody could examine must not be the two that
    disable the guard.
    """
    from warden import tags as tags_mod
    (tmp_path / "policy" / "rules").mkdir(parents=True)
    (tmp_path / "policy" / "rules" / "wiki-fidelity.md").write_text(
        "---\nid: wiki-fidelity\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\ncovers: [docs-drift]\n---\nbody\n")
    (tmp_path / ".warden" / "memory").mkdir(parents=True)
    (tmp_path / ".warden" / "memory" / "tags.yaml").write_text(
        "version: 1\nvocabulary:\n  docs-drift: d\naliases:\n"
        "  doc-drift: docs-drift\n")
    repo_yaml = tmp_path / "repo.yaml"
    repo_yaml.write_text(
        "version: 1\nrepo: c\ncomponents:\n  a: {path: a/, lang: python, description: d}\n"
        "review:\n  rules_dir: policy/rules\n  blocking_severities: [HIGH]\n")

    applicable, problems = tags_mod._split_aliases(tmp_path)
    assert applicable == {} and problems, "the collision must be refused"

    # the same repo, with a repo.yaml nobody can parse
    repo_yaml.write_text("version: 1\nreview: [this is not: a mapping\n")
    applicable, problems = tags_mod._split_aliases(tmp_path)
    assert applicable == {}, \
        f"an unreadable declaration must not fire the alias: {applicable}"
    assert problems and "cannot be read" in problems[0], problems

    # ...and a declared ruleset dir that does not exist is misconfiguration,
    # not freedom
    repo_yaml.write_text(
        "version: 1\nrepo: c\ncomponents:\n  a: {path: a/, lang: python, description: d}\n"
        "review:\n  rules_dir: gone/rules\n  blocking_severities: [HIGH]\n")
    applicable, problems = tags_mod._split_aliases(tmp_path)
    assert applicable == {} and problems, f"{applicable} {problems}"


def test_a_broken_rule_file_does_not_escape_as_an_exception(tmp_path):
    """Fail-closed: a broken symlink named *.md (FileNotFoundError from the
    stat() building the cache key) and a rule file that is not valid UTF-8
    (UnicodeDecodeError) both return the documented refusal instead of
    raising out through every folding seam."""
    from warden import tags as tags_mod
    rules = tmp_path / ".warden" / "rules"
    rules.mkdir(parents=True)
    (rules / "r.md").write_text(
        "---\nid: r\nseverity: LOW\nengine: claude\napplies_to: ['**']\n---\nb\n")
    (tmp_path / ".warden" / "memory").mkdir(parents=True)
    (tmp_path / ".warden" / "memory" / "tags.yaml").write_text(
        "version: 1\nvocabulary:\n  docs-drift: d\naliases:\n"
        "  doc-drift: docs-drift\n")

    (rules / "ghost.md").symlink_to(tmp_path / "nowhere.md")
    applicable, problems = tags_mod._split_aliases(tmp_path)
    assert applicable == {} and problems, "a broken symlink must not raise"

    (rules / "ghost.md").unlink()
    (rules / "bad.md").write_bytes(b"\xff\xfe not utf-8")
    applicable, problems = tags_mod._split_aliases(tmp_path)
    assert applicable == {} and problems, "invalid UTF-8 must not raise"
