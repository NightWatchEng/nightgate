"""A clean review is a review EVENT — and the corpus has to hold it.

The defect these pin: if `build_records` derived records ONLY from findings, a
clean round (`findings: []`) would produce zero records, `ingest` would skip
it, no shard would be written — and `attest check`, which looks for a
committed shard naming a commit in the PR's range, would report NO
ATTESTATION. The gate would punish the good outcome: the PRs that passed
review cleanly would be exactly the PRs it said were never reviewed.

Ingest writes a findings-free shard recording the EVENT (sha, base_sha,
rules_version, reviewed_at, reviewers, verdict). Two properties are
load-bearing in opposite directions and both are pinned here:

- it MUST satisfy `attest check` — that is the whole point;
- it MUST NOT create a phantom record: not in recall, not in any rule's `n`,
  not in precision or the Wilson bound. Trading a fail-open for a corrupted
  denominator would be the worse bargain.
"""

import json
import subprocess
from pathlib import Path

import pytest

from conftest import seed_rules

from warden import attest as attest_mod
from warden import memory as memory_mod

HEAD, BASE = "c" * 40, "a" * 40
FIXTURE_RULE_IDS = ("scope-creep", "secrets-in-diff")


def clean_attestation(**extra) -> dict:
    doc = {
        "head_sha": HEAD, "base_sha": BASE, "rules_version": "v1",
        "reviewed_at": "2026-08-24T00:00:00+00:00",
        "reviewers": [{"role": "code-reviewer", "agent": "subagent"},
                      {"role": "cross-examiner", "agent": "subagent"}],
        "findings": [], "verdict": "clean",
    }
    doc.update(extra)
    return doc


def judged_finding(rule="scope-creep", status="confirmed") -> dict:
    # `ts` rides along because two tests below use this FINDING shape directly
    # as a shard RECORD, and `RECORD_FIELD_CONTRACT` now requires `ts` on a
    # record: every counting seam buckets by it, and the empty default folds
    # distinct rounds into one, which is fail-open in the Wilson math.
    # Harmless on the artifact path — `build_records` derives
    # its own `ts` from the attestation's `reviewed_at` and never reads this
    # one — so one helper still serves both callers.
    f = {"rule_id": rule, "severity": "MEDIUM", "file": "app/core/a.py",
         "line": 1, "finding": "f", "evidence": "e", "status": status,
         "ts": "2026-01-01T00:00:00+00:00", "tags": ["fail-open"]}
    if status in ("refuted", "dismissed-with-reason"):
        f["reason"] = "reason"
    return f


def write_attest_artifact(root: Path, stamp: str, attestation: dict) -> None:
    d = root / ".warden" / "out" / f"{stamp}-attest"
    d.mkdir(parents=True)
    (d / "attestation.json").write_text(json.dumps(attestation) + "\n")


def write_gate_artifact(root: Path, stamp: str, findings: list) -> None:
    d = root / ".warden" / "out" / f"{stamp}-review"
    d.mkdir(parents=True)
    (d / "review-findings.json").write_text(json.dumps({
        "rules_version": "v1", "engine": "warden", "base_sha": BASE,
        "head_sha": HEAD, "findings": findings,
        "deferred_to_pre_pr": [], "paused": []}))


def ingest(root: Path, **kwargs) -> dict:
    return memory_mod.ingest(
        root, rules_dir=seed_rules(root, *FIXTURE_RULE_IDS), **kwargs)


def shards(root: Path) -> list[Path]:
    return sorted((memory_mod.memory_dir(root) / memory_mod.SHARD_SUBDIR)
                  .glob("*.json"))


# ---------- the event reaches the corpus -------------------------------------

def test_clean_review_writes_a_shard(tmp_path):
    """The defect itself: a clean round left no trace at all."""
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation())
    result = ingest(tmp_path)
    assert len(shards(tmp_path)) == 1
    assert result["new_shards"] == [shards(tmp_path)[0].name]
    assert result["skipped"] == []


def test_clean_shard_records_the_review_event(tmp_path):
    """What makes it an event and not an empty file: who reviewed what, when,
    against which rules, with what verdict."""
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation())
    ingest(tmp_path)
    shard = json.loads(shards(tmp_path)[0].read_text())
    assert shard["sha"] == HEAD
    assert shard["base_sha"] == BASE
    assert shard["rules_version"] == "v1"
    assert shard["reviewed_at"] == "2026-08-24T00:00:00+00:00"
    assert shard["verdict"] == "clean"
    assert [r["role"] for r in shard["reviewers"]] == ["code-reviewer",
                                                       "cross-examiner"]
    assert shard["records"] == [], "a review event carries no findings"
    assert shard["source"] == "attest"


def test_shard_filename_keeps_the_concurrent_safe_convention(tmp_path):
    """<utc-ts>-<sha8>-<digest8>.json — distinct filenames are why concurrent
    sessions never conflict."""
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation())
    ingest(tmp_path)
    name = shards(tmp_path)[0].name
    stem, _, ext = name.rpartition(".")
    ts, sha8, digest8 = stem.split("-")
    assert (ts, sha8, ext) == ("20260824T000000Z", HEAD[:8], "json")
    assert len(digest8) == 8


def test_two_clean_reviews_of_one_sha_do_not_overwrite_each_other(tmp_path):
    """The digest exists so DISTINCT events survive a ts+sha tie. A
    findings-free shard has no records to hash, so it must hash the event —
    or every clean review would share one filename."""
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation())
    write_attest_artifact(tmp_path, "20260824T000001Z", clean_attestation(
        reviewers=[{"role": "code-reviewer", "agent": "other-model"}]))
    ingest(tmp_path)
    assert len(shards(tmp_path)) == 2


def test_clean_ingest_is_idempotent(tmp_path):
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation())
    ingest(tmp_path)
    again = ingest(tmp_path)
    assert again["new_shards"] == []
    assert len(shards(tmp_path)) == 1


# ---------- and creates no phantom records -----------------------------------

def test_clean_shard_adds_no_records(tmp_path):
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation())
    result = ingest(tmp_path)
    assert result["total_records"] == 0
    assert memory_mod.load_records(tmp_path) == []


def test_clean_shard_is_never_recalled(tmp_path):
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation())
    ingest(tmp_path)
    for role in ("reviewer", "examiner"):
        assert memory_mod.recall(tmp_path, files=["app/core/a.py"],
                                 role=role, tags=["fail-open"],
                                 rules=["scope-creep"]) == []


def test_clean_shard_moves_no_precision_number(tmp_path):
    """The constraint that matters most: a fail-open traded for a corrupted
    denominator is the worse bargain. Every stats number must be identical
    with and without the clean round."""
    rules_dir = seed_rules(tmp_path, *FIXTURE_RULE_IDS)
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation(
        findings=[judged_finding(), judged_finding(status="refuted")],
        verdict="findings-open", head_sha="d" * 40))
    memory_mod.ingest(tmp_path, rules_dir=rules_dir)
    before = memory_mod.stats(tmp_path, rules_dir)

    write_attest_artifact(tmp_path, "20260824T000100Z", clean_attestation())
    memory_mod.ingest(tmp_path, rules_dir=rules_dir)
    after = memory_mod.stats(tmp_path, rules_dir)

    for key in ("per_rule", "per_tag", "candidates", "promotable",
                "pause_candidates", "gate_detections", "total_records"):
        assert after[key] == before[key], f"clean review moved {key}"
    assert after["per_rule"]["scope-creep"]["n"] == 2
    assert after["corpus_age"]["count"] == 2


def test_clean_shard_does_not_corrupt_the_rendered_sections(tmp_path):
    """DECLARED RULES / CANDIDATE RULES / TAGS keep rendering, and the
    standing caveat still prints."""
    rules_dir = seed_rules(tmp_path, *FIXTURE_RULE_IDS)
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation(
        findings=[judged_finding(), judged_finding(rule="unmapped:phantom-record")],
        verdict="findings-open", head_sha="d" * 40))
    write_attest_artifact(tmp_path, "20260824T000100Z", clean_attestation())
    memory_mod.ingest(tmp_path, rules_dir=rules_dir)
    out = memory_mod.render_stats(memory_mod.stats(tmp_path, rules_dir))
    assert "CAVEAT: precision only; recall is unobservable" in out
    assert "DECLARED RULES" in out
    assert "CANDIDATE RULES" in out
    assert "TAGS" in out
    assert "rule scope-creep: n=1" in out


def test_stats_can_finally_count_clean_reviews(tmp_path):
    """Stats count clean rounds apart from rounds with findings: "how many
    rounds came back clean" must be answerable from the corpus, which cannot
    hold only the rounds that FOUND something."""
    rules_dir = seed_rules(tmp_path, *FIXTURE_RULE_IDS)
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation(
        findings=[judged_finding()], verdict="findings-open",
        head_sha="d" * 40))
    write_attest_artifact(tmp_path, "20260824T000100Z", clean_attestation())
    memory_mod.ingest(tmp_path, rules_dir=rules_dir)
    doc = memory_mod.stats(tmp_path, rules_dir)
    assert doc["review_events"]["total"] == 2
    assert doc["review_events"]["clean"] == 1
    assert doc["review_events"]["findings_open"] == 1
    assert "REVIEW EVENTS" in memory_mod.render_stats(doc)


def test_unreadable_shard_makes_the_tally_a_floor_not_a_count(tmp_path):
    """"I could not read the store" must never render as "there were fewer
    reviews" — the same contract `attest.attested_shas` keeps."""
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation())
    ingest(tmp_path)
    (memory_mod.memory_dir(tmp_path) / memory_mod.SHARD_SUBDIR
     / "broken.json").write_text("{ not json")
    events = memory_mod.review_events(tmp_path)
    assert events == {"total": 1, "clean": 1, "findings_open": 0,
                      "verdict_unrecorded": 0, "unreadable": 1,
                      "with_records": 0, "with_refutation": 0}
    assert "floor, not a count" in memory_mod.render_stats(
        memory_mod.stats(tmp_path))


def test_shards_predating_the_event_envelope_are_counted_apart(tmp_path):
    """A shard written before the envelope carried a verdict cannot answer
    'clean or not' — it must not be made to look like it did."""
    shard_dir = memory_mod.memory_dir(tmp_path) / memory_mod.SHARD_SUBDIR
    shard_dir.mkdir(parents=True)
    (shard_dir / "20260101T000000Z-deadbeef-cafebabe.json").write_text(
        json.dumps({"schema": 1, "source": "attest", "sha": "d" * 40,
                    "base_sha": BASE, "rules_version": "v0",
                    "reviewed_at": "2026-01-01T00:00:00+00:00",
                    "records": []}))
    events = memory_mod.review_events(tmp_path)
    assert (events["total"], events["clean"], events["verdict_unrecorded"]) \
        == (1, 0, 1)
    assert "no verdict recorded" in memory_mod.render_stats(
        memory_mod.stats(tmp_path))


def test_gate_shards_are_not_review_events(tmp_path):
    """A deterministic checker firing is a fact, not an orchestrated review."""
    write_gate_artifact(tmp_path, "20260824T000000Z", [
        {"rule_id": "secrets-in-diff", "severity": "HIGH", "file": "app/a.py",
         "line": 1, "finding": "k", "evidence": "redacted"}])
    ingest(tmp_path)
    assert memory_mod.review_events(tmp_path)["total"] == 0


# ---------- fail-closed: an empty shard is not free ---------------------------

def test_findings_free_gate_run_writes_no_shard(tmp_path):
    """A gate run that fired nothing judged nothing and names no reviewer;
    `attest check` ignores gate shards by design, so an empty one per run
    would be churn with no reader."""
    write_gate_artifact(tmp_path, "20260824T000000Z", [])
    ingest(tmp_path)
    assert list((memory_mod.memory_dir(tmp_path) / memory_mod.GATE_SUBDIR)
                .glob("*.json")) == []


@pytest.mark.parametrize("field", ["reviewers", "verdict", "head_sha",
                                   "base_sha", "reviewed_at"])
def test_findings_free_artifact_that_names_no_review_is_refused(
        tmp_path, field):
    """The findings-free shard IS the whole evidence `attest check` reads, so
    it must not be mintable by an artifact that cannot say what was reviewed,
    by whom, or with what verdict. Refused loudly, never silently."""
    doc = clean_attestation()
    doc[field] = [] if field == "reviewers" else ""
    write_attest_artifact(tmp_path, "20260824T000000Z", doc)
    result = ingest(tmp_path)
    assert shards(tmp_path) == []
    assert any(field in s for s in result["skipped"]), result["skipped"]


def test_r2f1_verdict_vocabulary_is_read_from_the_schema_not_retyped():
    """A verdict list hand-copied into memory.py, or retyped as bare literals
    in the tally, means adding a value to the schema enum — the schema edit
    the contract gate calls SAFE — would silently make `ingest` refuse a
    schema-valid clean review (NO ATTESTATION) and mis-bucket the tally, with
    the suite green.

    This drift guard fails the moment the schema grows a verdict the corpus
    does not know how to file."""
    schema = json.loads(
        (Path(attest_mod.__file__).parent / "schemas"
         / "attestation.schema.json").read_text())
    declared = tuple(schema["properties"]["verdict"]["enum"])
    assert attest_mod.VERDICTS == declared
    assert set(memory_mod._VERDICT_BUCKETS) == set(declared), (
        "a verdict the schema declares but the tally cannot bucket would be "
        "counted as 'no verdict recorded' — recorded, and miscounted")
    for bucket in memory_mod._VERDICT_BUCKETS.values():
        assert bucket in memory_mod.review_events(Path("/nonexistent"))


def test_r2f2_an_invalid_verdict_is_reported_as_invalid_not_absent(tmp_path):
    """The absent and the out-of-vocabulary case are reported differently:
    "missing verdict" for both would tell an operator to supply a field they
    had supplied and never name the values that would work."""
    write_attest_artifact(tmp_path, "20260824T000000Z",
                          clean_attestation(verdict="CLEAN"))
    invalid = ingest(tmp_path)["skipped"]
    assert len(invalid) == 1
    assert "'CLEAN' is not one of clean | findings-open" in invalid[0]
    assert "no verdict" not in invalid[0], "it named a verdict; say so"

    absent = tmp_path / "absent"
    doc = clean_attestation()
    del doc["verdict"]
    write_attest_artifact(absent, "20260824T000000Z", doc)
    reported = ingest(absent)["skipped"]
    assert len(reported) == 1
    assert "no verdict" in reported[0]
    assert "is not one of" not in reported[0]


def test_r1f2_secrets_anywhere_in_the_envelope_are_redacted(tmp_path):
    """Redaction covers the whole envelope, not two fields: an artifact with a
    planted key in `rules_version` or `base_sha` must not shard it verbatim
    into committed evidence."""
    planted_rv = "api_key=sk-live_RULESVERSION1234567890"
    planted_base = "sk-live_BASESHA1234567890abcdef"
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation(
        rules_version=planted_rv, base_sha=planted_base))
    ingest(tmp_path)
    corpus = "".join(p.read_text() for p in shards(tmp_path))
    assert "sk-live_RULESVERSION" not in corpus
    assert "sk-live_BASESHA" not in corpus
    shard = json.loads(shards(tmp_path)[0].read_text())
    assert shard["rules_version"] == "<redacted:secret-pattern>"
    assert shard["base_sha"] == "<redacted:secret-pattern>"


def test_r1f3_a_verdict_outside_the_vocabulary_is_refused(tmp_path):
    """`ingest` never calls `attest.build`, and `_check_rule_ids` returns
    immediately with no findings — so unless ingest checks the verdict itself,
    `verdict: "CLEAN"` mints an attestation whose outcome no reader can
    classify."""
    write_attest_artifact(tmp_path, "20260824T000000Z",
                          clean_attestation(verdict="CLEAN"))
    result = ingest(tmp_path)
    assert shards(tmp_path) == []
    assert any("verdict" in s for s in result["skipped"]), result["skipped"]
    assert result["new_shards"] == []


def test_r1f3_a_verdict_nobody_gave_is_not_recorded(tmp_path):
    """A findings-bearing artifact naming no verdict must not be sharded with
    `verdict: ""` — an answer nobody gave, indistinguishable in the render
    from evidence that predates the field."""
    doc = clean_attestation(findings=[judged_finding()], head_sha="d" * 40)
    del doc["verdict"]
    write_attest_artifact(tmp_path, "20260824T000000Z", doc)
    ingest(tmp_path)
    shard = json.loads(shards(tmp_path)[0].read_text())
    assert "verdict" not in shard, "an empty verdict is data wearing a costume"
    assert memory_mod.review_events(tmp_path)["verdict_unrecorded"] == 1


def test_r1f3_the_unrecorded_bucket_claims_no_cause(tmp_path):
    """The render names no cause: hard-coding "shards predating the event
    envelope" would report a malformed shard written minutes ago to the retro
    as legacy evidence."""
    doc = clean_attestation(findings=[judged_finding()], head_sha="d" * 40)
    del doc["verdict"]
    write_attest_artifact(tmp_path, "20260824T000000Z", doc)
    rules_dir = seed_rules(tmp_path, *FIXTURE_RULE_IDS)
    memory_mod.ingest(tmp_path, rules_dir=rules_dir)
    out = memory_mod.render_stats(memory_mod.stats(tmp_path, rules_dir))
    assert "1 with no verdict recorded" in out
    assert "predating" not in out


def test_r1f6_review_events_agrees_with_attested_shas_on_a_sha_less_shard(
        tmp_path):
    """Two readers of one store must agree: `review_events` counting a shard
    with no `sha` as a confident review while `attest.attested_shas` calls the
    same shard unreadable and refuses to guess would be a stats line asserting
    what the gate would not."""
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation())
    ingest(tmp_path)
    (memory_mod.memory_dir(tmp_path) / memory_mod.SHARD_SUBDIR
     / "nosha.json").write_text(json.dumps(
         {"schema": 1, "source": "attest", "verdict": "clean", "records": []}))
    # attested_shas reads the committed tree, so give it one
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run([*git, "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "shards"], cwd=tmp_path,
                   check=True, capture_output=True)
    events = memory_mod.review_events(tmp_path)
    _found, unreadable, _, _verdicts = attest_mod.attested_shas(tmp_path, "HEAD")
    # `unreadable` is path -> CAUSE: the one error that can block a
    # consumer's PR has to name the defect and not only the file.
    nosha = (attest_mod.SHARD_DIR / "nosha.json").as_posix()
    assert list(unreadable) == [nosha]
    # The cause is a SENTENCE, not the bare key a KeyError stringifies to:
    # `'sha'` tells the reader what was received and never what was required.
    assert "names no" in unreadable[nosha] and "sha" in unreadable[nosha], (
        f"the cause is not a sentence an author can act on: "
        f"{unreadable[nosha]!r}")
    assert events["unreadable"] == len(unreadable)
    assert events["total"] == 1, "the sha-less shard must not count as a review"


def test_secrets_in_the_event_envelope_are_redacted(tmp_path):
    """Shards are committed evidence: the redaction boundary covers the event
    envelope, not just finding text."""
    planted = "sk-live_EVENTENVELOPE1234567890"
    write_attest_artifact(tmp_path, "20260824T000000Z", clean_attestation(
        reviewers=[{"role": "code-reviewer", "agent": planted}]))
    ingest(tmp_path)
    corpus = "".join(p.read_text() for p in shards(tmp_path))
    assert planted not in corpus
    assert "redacted" in corpus


# ---------- attest check accepts a clean-only range --------------------------

class TestAttestCheckOnACleanRange:
    """`attest check` MUST pass on a range whose only evidence is a
    clean-review shard. This is the whole point of that shard, and the test
    that goes red if someone "optimises the empty shard away"."""

    @staticmethod
    def _git(root, *args):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True, text=True)

    def _commit(self, root, name):
        (root / name).write_text("x\n")
        self._git(root, "add", "-A")
        self._git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "-m", f"add {name}")
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              check=True, capture_output=True,
                              text=True).stdout.strip()

    def test_clean_review_satisfies_attest_check(self, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        self._git(root, "init", "-q", "-b", "main")
        self._commit(root, "README.md")
        self._git(root, "switch", "-q", "-c", "feature")
        reviewed = self._commit(root, "feature.py")

        write_attest_artifact(root, "20260824T000000Z",
                              clean_attestation(head_sha=reviewed))
        result = ingest(root)
        assert result["new_shards"], "the clean round produced no shard"
        # The ingest commit, faithfully: the shard and NOTHING else. A marker
        # file beside it is content landing after the round, which the
        # coverage rule refuses. So is
        # `.warden/memory/findings.jsonl`, the derived cache `warden enroll`
        # gitignores (GITIGNORE_ENTRIES) and this bare fixture repo does not.
        self._git(root, "add", attest_mod.SHARD_DIR.as_posix())
        self._git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "-m", "ops(memory): commit the shard")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              check=True, capture_output=True,
                              text=True).stdout.strip()

        doc = attest_mod.check_range(root, base="main", head=head)
        assert [m["sha"] for m in doc["attested"]] == [reviewed]
        assert "PASS" in attest_mod.render_check(doc)

    def test_pre_envelope_shard_needs_no_migration(self, tmp_path):
        """This change adds fields; it does not make old evidence illegal.

        A shard written before the envelope carried verdict/reviewers is still
        a valid attestation and still answers `attest check`. That is the
        difference from the trap #56 hit, where a policy applied only forward
        left committed shards unreadable under the rule that reads them: here
        nothing rejects a pre-envelope shard, so nothing needs rewriting — and
        the verdict is reported as unrecorded rather than guessed at, because
        inferring one would be fabricating committed evidence.
        """
        root = tmp_path / "repo"
        root.mkdir()
        self._git(root, "init", "-q", "-b", "main")
        self._commit(root, "README.md")
        self._git(root, "switch", "-q", "-c", "feature")
        reviewed = self._commit(root, "feature.py")

        shard_dir = root / attest_mod.SHARD_DIR
        shard_dir.mkdir(parents=True)
        (shard_dir / f"20260101T000000Z-{reviewed[:8]}-cafebabe.json").write_text(
            json.dumps({"schema": 1, "source": "attest", "sha": reviewed,
                        "base_sha": BASE, "rules_version": "v0",
                        "reviewed_at": "2026-01-01T00:00:00+00:00",
                        "records": [judged_finding()]}))
        head = self._commit(root, "ingest-the-shard")

        doc = attest_mod.check_range(root, base="main", head=head)
        assert [m["sha"] for m in doc["attested"]] == [reviewed]
        assert memory_mod.review_events(root)["verdict_unrecorded"] == 1
