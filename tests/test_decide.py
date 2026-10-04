"""The decision record, bound to commit and rules_version.

A decision is stamped with head/base SHA + rules_version so it cannot mislabel
what was decided against what, is schema-validated so it cannot ship a
half-record, and rides the .warden/out -> committed-shard path so
`warden memory ingest` picks it up and decisions compound.
"""

import json
import os
from pathlib import Path

import pytest

from conftest import _AMBIENT_GIT_CONFIG, tracked
from warden import decide
from private_evidence import needs_corpus


ROOT = Path(__file__).parent.parent
SHA_A = "a" * 40
SHA_B = "b" * 40


def _payload(**over):
    p = {"decided": "bind S-05 to rules_version",
         "why": "a stale backtest satisfied the gate forever",
         "cost_if_wrong": "a consumer at L5 fails S-05 until it re-derives",
         "alternatives": ["narrower per-rule marker", "do nothing"],
         "scope": "warden certify S-05 only"}
    p.update(over)
    return p


def test_build_stamps_provenance_and_validates():
    doc = decide.build(_payload(), head_sha=SHA_A, base_sha=SHA_B,
                       rules_version="abc123", bead="agentops-pcy.1")
    assert doc["head_sha"] == SHA_A and doc["base_sha"] == SHA_B
    assert doc["rules_version"] == "abc123"
    assert doc["decided_at"]                      # stamped, not accepted
    assert doc["bead"] == "agentops-pcy.1"
    assert doc["decided"].startswith("bind S-05")


def test_build_rejects_a_provenance_field_in_the_payload():
    # provenance is stamped, never accepted from the caller — a skill must not
    # be able to forge the commit a decision was made against.
    with pytest.raises(decide.DecideError, match="extra key"):
        decide.build({**_payload(), "head_sha": "forged"},
                     head_sha=SHA_A, base_sha=SHA_B, rules_version="v")


@pytest.mark.parametrize("missing", sorted(decide.PAYLOAD_KEYS))
def test_build_requires_every_author_field(missing):
    p = {k: v for k, v in _payload().items() if k != missing}
    with pytest.raises(decide.DecideError):
        decide.build(p, head_sha=SHA_A, base_sha=SHA_B, rules_version="v")


def test_build_requires_at_least_one_alternative():
    # a decision with no alternative was not a decision.
    with pytest.raises(decide.DecideError):
        decide.build(_payload(alternatives=[]),
                     head_sha=SHA_A, base_sha=SHA_B, rules_version="v")


def test_render_summary_names_the_ruling_and_its_provenance():
    doc = decide.build(_payload(), head_sha=SHA_A, base_sha=SHA_B,
                       rules_version="abc123")
    out = decide.render_summary(doc)
    assert "bind S-05" in out
    assert "cost if wrong" in out
    assert "rules_version abc123" in out


# ── ingest: out artifact -> committed shard, idempotent, fail-closed ───────

def _write_artifact(root, doc, name="20260828T120000Z-decide"):
    d = root / ".warden" / "out" / name
    d.mkdir(parents=True)
    (d / "decision.json").write_text(json.dumps(doc))


def test_ingest_sweeps_an_artifact_into_a_committed_shard(tmp_path):
    doc = decide.build(_payload(), head_sha=SHA_A, base_sha=SHA_B,
                       rules_version="v")
    _write_artifact(tmp_path, doc)
    result = decide.ingest(tmp_path)
    assert len(result["new_decisions"]) == 1
    shards = list((tmp_path / decide.SHARD_DIR).glob("*.json"))
    assert len(shards) == 1
    assert json.loads(shards[0].read_text())["decided"] == doc["decided"]


def test_ingest_is_idempotent(tmp_path):
    doc = decide.build(_payload(), head_sha=SHA_A, base_sha=SHA_B,
                       rules_version="v")
    _write_artifact(tmp_path, doc)
    assert len(decide.ingest(tmp_path)["new_decisions"]) == 1
    assert decide.ingest(tmp_path)["new_decisions"] == []     # second is a no-op
    assert len(list((tmp_path / decide.SHARD_DIR).glob("*.json"))) == 1


def test_ingest_skips_a_schema_invalid_artifact_loudly(tmp_path):
    # a half-record must be skipped with a reason, never filed as a decision.
    _write_artifact(tmp_path, {"decided": "only this", "head_sha": SHA_A})
    result = decide.ingest(tmp_path)
    assert result["new_decisions"] == []
    assert result["skipped_decisions"]
    assert not (tmp_path / decide.SHARD_DIR).exists() or \
        not list((tmp_path / decide.SHARD_DIR).glob("*.json"))


def test_decisions_from_shards_returns_them_newest_first(tmp_path):
    older = decide.build(_payload(decided="older"), head_sha=SHA_A,
                         base_sha=SHA_B, rules_version="v")
    older["decided_at"] = "2026-08-01T00:00:00+00:00"
    newer = decide.build(_payload(decided="newer"), head_sha=SHA_A,
                         base_sha=SHA_B, rules_version="v")
    newer["decided_at"] = "2026-08-28T00:00:00+00:00"
    sd = tmp_path / decide.SHARD_DIR
    sd.mkdir(parents=True)
    (sd / decide._shard_name(older)).write_text(json.dumps(older))
    (sd / decide._shard_name(newer)).write_text(json.dumps(newer))
    got = decide.decisions_from_shards(tmp_path)
    assert [d["decided"] for d in got] == ["newer", "older"]


# ── the acceptance: `warden memory ingest` picks the decision up ──────────

def test_memory_ingest_commits_a_decision_shard(tmp_path):
    """End to end at the ingest seam: a decision
    artifact in .warden/out is swept into a committed decision shard by the
    same `memory ingest` that commits attestations — into its OWN store, never
    the findings cache."""
    import subprocess

    from warden import memory

    root = tmp_path
    # Closed env, so conftest's session neutralisation cannot reach it —
    # the constant is imported rather than re-typed.
    env = {**_AMBIENT_GIT_CONFIG, "HOME": str(root),
           "PATH": os.environ["PATH"]}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True, env=env)
    (root / ".warden" / "rules").mkdir(parents=True)
    doc = decide.build(_payload(), head_sha=SHA_A, base_sha=SHA_B,
                       rules_version="v")
    _write_artifact(root, doc)

    result = memory.ingest(root, rules_dir=root / ".warden" / "rules")
    assert len(result["new_decisions"]) == 1
    # committed into the decide store, not the findings corpus
    assert list((root / decide.SHARD_DIR).glob("*.json"))
    assert result["total_records"] == 0          # a decision is not a record


def test_memory_ingest_survives_a_decision_sweep_failure(tmp_path, monkeypatch):
    """The findings ingest is memory ingest's critical job; a filesystem
    failure in the decision sweep (which runs after the cache is written) must
    be reported as skipped, never abort the whole command."""
    import subprocess

    from warden import decide as decide_mod
    from warden import memory

    root = tmp_path
    # Closed env, so conftest's session neutralisation cannot reach it —
    # the constant is imported rather than re-typed.
    env = {**_AMBIENT_GIT_CONFIG, "HOME": str(root),
           "PATH": os.environ["PATH"]}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True, env=env)
    (root / ".warden" / "rules").mkdir(parents=True)

    def boom(_root):
        raise OSError("disk full")

    monkeypatch.setattr(decide_mod, "ingest", boom)
    result = memory.ingest(root, rules_dir=root / ".warden" / "rules")
    assert result["new_decisions"] == []
    assert any("decision sweep failed" in s for s in result["skipped_decisions"])
    assert result["total_records"] == 0          # findings ingest still returned


def test_a_secret_in_a_decision_is_redacted_in_the_committed_shard(tmp_path):
    """Fail-closed: decision shards commit into the same .warden/memory/ tree
    attestations do, so they must be redacted at the shard boundary by the
    SAME scrubber — a rationale that
    quotes the secret it discusses must not land in the corpus verbatim."""
    from warden.memory import _has_secret_shape
    token = "ghp_ABCD1234efgh5678ijkl9012mnop3456qrst"
    assert _has_secret_shape(token)          # the token really is secret-shaped
    doc = decide.build(
        _payload(why=f"the old value {token} was pushed to a public gist"),
        head_sha=SHA_A, base_sha=SHA_B, rules_version="v")
    _write_artifact(tmp_path, doc)
    decide.ingest(tmp_path)
    shard_text = next((tmp_path / decide.SHARD_DIR).glob("*.json")).read_text()
    assert token not in shard_text
    assert "redacted" in shard_text


# ── read the committed decision store back, so decisions compound ──────────

def test_render_list_shows_committed_decisions_newest_first():
    docs = [decide.build(_payload(decided="older"), head_sha=SHA_A,
                         base_sha=SHA_B, rules_version="v"),
            decide.build(_payload(decided="newer"), head_sha=SHA_A,
                         base_sha=SHA_B, rules_version="v", bead="agentops-hff")]
    docs[0]["decided_at"] = "2026-08-01T00:00:00+00:00"
    docs[1]["decided_at"] = "2026-08-28T00:00:00+00:00"
    out = decide.render_list(sorted(docs, key=lambda d: d["decided_at"],
                                    reverse=True))
    assert "committed decisions (2)" in out
    assert out.index("newer") < out.index("older")   # newest first
    assert "agentops-hff" in out


def test_render_list_empty_says_so():
    assert "no committed decisions" in decide.render_list([])


def test_decide_list_reads_the_committed_shard_store(tmp_path):
    """The committed decision shards are readable back,
    so the record `record` + `memory ingest` write actually compounds."""
    doc = decide.build(_payload(decided="use rules_version binding"),
                       head_sha=SHA_A, base_sha=SHA_B, rules_version="v")
    sd = tmp_path / decide.SHARD_DIR
    sd.mkdir(parents=True)
    (sd / decide._shard_name(doc)).write_text(json.dumps(doc))
    got = decide.decisions_from_shards(tmp_path)
    assert len(got) == 1
    assert "use rules_version binding" in decide.render_list(got)


def test_render_list_flattens_a_newline_in_decided():
    """One line per decision — a `decided` carrying a
    newline is flattened, not spilled across rows."""
    d = {"decided_at": "2026-08-28T00:00:00+00:00", "head_sha": "a" * 40,
         "decided": "line1\nline2 injected"}
    out = decide.render_list([d])
    body = out.splitlines()
    assert len(body) == 2                      # header + one decision line
    assert "line1 line2 injected" in body[1]


def test_base_sha_semantic_is_documented_and_base_equals_head_is_valid():
    """base_sha is 'as of this commit', NOT a diff base. Pin the
    schema description so the semantic cannot be silently dropped, and prove a
    base==head decision (the CLI default) validates."""
    import json
    from pathlib import Path
    schema = json.loads(
        (Path(decide.__file__).parent / "schemas" / "decision.schema.json").read_text())
    desc = schema["properties"]["base_sha"].get("description", "")
    assert "NOT a diff base" in desc, "base_sha lost its as-of / not-a-range semantic"
    # base == head (the CLI default when --base is omitted) is a valid record
    doc = decide.build(_payload(), head_sha=SHA_A, base_sha=SHA_A,
                       rules_version="v")
    assert doc["base_sha"] == doc["head_sha"] == SHA_A


@needs_corpus
def test_every_committed_decision_shard_names_the_bead_it_answers():
    """A ruling a later reader of the tracked item cannot reach is not a
    record, and `bead` is the only field that carries the two to each other.

    `warden decide record --bead <id>` stamps it, `render_list` prints it as
    the middle column, and a grep from the item finds it. One shard of 68
    landed without it — and the finding that shard was written to ANSWER was
    precisely that a scoped-out half of an acceptance, living only in prose,
    is invisible from the bead. Held over the shards git tracks rather than
    over `build`, because the hole was a shard that reached the store, not a
    function that dropped the key.
    """
    shards = tracked(str(decide.SHARD_DIR / "*.json"))
    assert shards, f"no committed decision shards under {decide.SHARD_DIR}"
    missing = [p.name for p in shards
               if not str(json.loads(p.read_text()).get("bead") or "").strip()]
    assert not missing, (
        "committed decision shard(s) carry no `bead`, so neither `warden "
        "decide list` nor a grep from the tracked item reaches the ruling: "
        + ", ".join(missing))


def test_every_committed_decision_shard_is_named_for_its_own_content():
    """The shard name is a content digest, so a shard edited in place and not
    renamed is a record whose name describes different bytes.

    `_shard_name` is what `ingest` writes, and it is what makes re-ingesting
    one decision a no-op. A name that no longer derives from the bytes under
    it breaks that idempotence silently: the next sweep writes the same ruling
    again under its true digest, and the store carries two spellings of one
    decision with nothing to reconcile them.
    """
    wrong = [p.name for p in tracked(str(decide.SHARD_DIR / "*.json"))
             if p.name != decide._shard_name(json.loads(p.read_text()))]
    assert not wrong, (
        "committed decision shard(s) whose name is not the digest of their "
        "own content — re-ingest or rename them: " + ", ".join(wrong))
