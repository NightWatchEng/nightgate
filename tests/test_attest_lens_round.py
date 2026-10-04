"""Lens and round attribution on attestation findings.

A roster that gives per-reviewer COUNTS on a shard but never JOINS a finding
to the reviewer who raised it leaves per-lens outcome — how often each lens's
findings are fixed vs refuted vs dismissed — unrecoverable from the committed
corpus however long it grows, and a free-text `role` string drifts into many
spellings per role.

These pin the three halves: the vocabulary is read from the schema
and has exactly the two shapes the decision record names; the write path
refuses, under --review-dir, a finding attributed to a dispatch that did not
run; and the fields ride into records and the stats tally, with unattributed
records counted apart rather than folded in.
"""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from warden import attest as attest_mod
from warden import memory as memory_mod
from warden import runs as runs_mod
from private_evidence import needs_corpus

HEAD, BASE = "b" * 40, "a" * 40
SCHEMA_PATH = Path(attest_mod.__file__).parent / "schemas" / "attestation.schema.json"


@pytest.fixture
def rules_dir(sample_repo) -> Path:
    return sample_repo / ".warden" / "rules"


@pytest.fixture
def build(rules_dir):
    def _build(payload: dict, *, review_dir: Path | None = None) -> dict:
        return attest_mod.build(payload, head_sha=HEAD, base_sha=BASE,
                                rules_version="v123", rules_dir=rules_dir,
                                review_dir=review_dir)
    return _build


def _round(root: Path, *dispatches: tuple[str, int], head: str = HEAD) -> Path:
    """A warden-minted round whose reviewers/ holds one report per dispatch.

    Built through `runs.write_round_manifest`, so a change to what warden
    mints breaks this fixture rather than leaving it describing a shape
    warden no longer writes. Returns the reviewers/ directory, which is what
    `--review-dir` takes.
    """
    d = root / "round"
    reviewers = d / "reviewers"
    reviewers.mkdir(parents=True)
    for role, rnd in dispatches:
        # Each report names its own dispatch. One byte-string for every role
        # is precisely the carried-forward copy `carry_forward` refuses,
        # so a shared placeholder would make this fixture
        # describe a round warden rejects.
        (reviewers / f"{role.replace(':', '-')}-r{rnd}.md").write_text(
            f"# report\nlens: {role}\nround: {rnd}\n")
    runs_mod.write_round_manifest(d, base="main", base_sha=BASE, head=head,
                                  branch="mine",
                                  round_no=max(rnd for _, rnd in dispatches))
    return reviewers


def _roster(*dispatches: tuple[str, int], returned: bool = True) -> list[dict]:
    return [{"role": role, "agent": "subagent", "returned": returned,
             **({"findings": 1,
                 "output": f"{role.replace(':', '-')}-r{rnd}.md"}
                if returned else {}),
             "round": rnd}
            for role, rnd in dispatches]


def _finding(lens: str | None = "code-reviewer", rnd: int | None = 1,
             **extra) -> dict:
    f = {"rule_id": "scope-creep", "severity": "MEDIUM", "file": "app/X.swift",
         "line": 4, "finding": "new surface", "evidence": "code",
         "status": "fixed"}
    if lens is not None:
        f["lens"] = lens
    if rnd is not None:
        f["round"] = rnd
    f.update(extra)
    return f


# --- the vocabulary -----------------------------------------------------------


def test_the_lens_vocabulary_is_read_from_the_schema_and_has_two_shapes():
    """One definition, in the contract consumers pin. The enum members are the
    roles the platform's own skills dispatch; a charter lens is `crew:<slug>`
    — a prefix that is structurally distinguishable plus a kebab slug, the
    same argument `unmapped:` makes for rule ids. Exactly ONE of the corpus's
    four spellings of the fail-closed lens survives.

    `claims-auditor` joined it with the proportionate round, for the same
    reason and under the same argument: a role warden's own schema does not
    know could never be declared, so the round could not exist.

    `closure-attestor` joined the enum with the terminal closure round.
    Widening the enum is the schema edit the contract-freeze
    gate calls safe — no shard the old contract accepted is refused by the new
    one — and it is REQUIRED for the round to exist at all: `graph.resolve_
    review` refuses a declared role outside this vocabulary, so a closure crew
    warden's own schema does not know could never be declared.
    """
    schema = json.loads(SCHEMA_PATH.read_text())
    assert set(attest_mod.LENS_ROLES) == {
        "code-reviewer", "cross-examiner", "scoped-re-reviewer", "builder",
        "closure-attestor", "claims-auditor"}
    assert set(attest_mod.LENS_ROLES) == set(
        schema["$defs"]["lens"]["anyOf"][0]["enum"]), (
        "LENS_ROLES is retyped rather than read from the schema — the two "
        "will drift the moment the enum grows")
    for ok in ("code-reviewer", "cross-examiner", "scoped-re-reviewer",
               "builder", "closure-attestor", "claims-auditor",
               "crew:fail-closed",
               "crew:consumer-blast-radius", "crew:x1"):
        assert attest_mod.lens_is_valid(ok), ok
    for bad in ("crew-fail-closed", "fail-closed",
                "fail-closed review (independent, post-hoc)", "review-crew",
                "crew:", "crew:Fail-Closed", "crew:fail_closed",
                "crew:fail-closed ", "crew:fail-closed\n", "reviewer",
                "cross-examiner-round2", "", 42, None):
        assert not attest_mod.lens_is_valid(bad), repr(bad)


# --- the write path under --review-dir -----------------------------------------


def test_a_truthful_attribution_writes_and_is_rendered(build, tmp_path):
    rd = _round(tmp_path, ("code-reviewer", 1), ("crew:fail-closed", 1),
                ("cross-examiner", 2))
    doc = build({"reviewers": _roster(("code-reviewer", 1),
                                      ("crew:fail-closed", 1),
                                      ("cross-examiner", 2)),
                 "findings": [_finding("code-reviewer", 1),
                              _finding("crew:fail-closed", 1, line=9)],
                 "verdict": "clean"}, review_dir=rd)
    assert [f["lens"] for f in doc["findings"]] == ["code-reviewer",
                                                     "crew:fail-closed"]
    assert [r["round"] for r in doc["reviewers"]] == [1, 1, 2]
    out = attest_mod.render_summary(doc)
    assert "round 2" in out, "the round a reviewer ran in is not rendered"
    absent = dict(doc, reviewers=[{"role": "crew:fail-closed", "agent": "x",
                                   "returned": False, "round": 3}])
    assert "round 3" in attest_mod.render_summary(absent), (
        "a reviewer that did not return loses its round on render (R1-11)")
    assert "code-reviewer 1" in out and "crew:fail-closed 1" in out, (
        "per-lens finding counts are not rendered — a declared field with "
        "no reader is the defect this schema already earned once (277 R2F1)")


@pytest.mark.parametrize("role", ["crew-fail-closed", "fail-closed",
                                  "review-crew", "reviewer"])
def test_under_review_dir_a_role_outside_the_vocabulary_is_refused(
        build, tmp_path, role):
    rd = _round(tmp_path, (role, 1))
    with pytest.raises(attest_mod.AttestError) as e:
        build({"reviewers": _roster((role, 1)), "findings": [],
               "verdict": "clean"}, review_dir=rd)
    msg = str(e.value)
    assert role in msg and "crew:<slug>" in msg and "code-reviewer" in msg, (
        f"the refusal must name the offending role AND the vocabulary: {msg}")


def test_under_review_dir_every_roster_entry_states_its_round(
        build, tmp_path):
    rd = _round(tmp_path, ("code-reviewer", 1))
    roster = _roster(("code-reviewer", 1))
    del roster[0]["round"]
    with pytest.raises(attest_mod.AttestError, match="round"):
        build({"reviewers": roster, "findings": [], "verdict": "clean"},
              review_dir=rd)


@pytest.mark.parametrize("missing", ["lens", "round"])
def test_under_review_dir_every_finding_carries_lens_and_round(
        build, tmp_path, missing):
    """Optional in the schema — the committed corpus predates the fields —
    but REQUIRED where the check runs, exactly as `returned` is: a round
    attested without them is a round per-lens outcome can never count, and
    an optional field nobody is made to fill stays empty."""
    rd = _round(tmp_path, ("code-reviewer", 1))
    f = _finding()
    del f[missing]
    with pytest.raises(attest_mod.AttestError, match=missing):
        build({"reviewers": _roster(("code-reviewer", 1)), "findings": [f],
               "verdict": "clean"}, review_dir=rd)


def test_a_finding_cannot_be_attributed_to_a_dispatch_that_did_not_run(
        build, tmp_path):
    """The join is the point. A finding naming a lens no roster entry
    declares, a lens whose reviewer did not return, or a round in which that
    lens was not dispatched, is attributed to nothing observable."""
    rd = _round(tmp_path, ("code-reviewer", 1))
    roster = _roster(("code-reviewer", 1))
    # Branch-specific text on purpose: matching the lens name alone would
    # let a mutant with the whole "no roster entry declares" branch deleted
    # stay green, because the fall-through round message
    # names the lens too.
    with pytest.raises(attest_mod.AttestError, match="no roster entry declares"):
        build({"reviewers": roster, "findings": [_finding("crew:fail-closed", 1)],
               "verdict": "clean"}, review_dir=rd)
    with pytest.raises(attest_mod.AttestError,
                       match=r"round 2, but that lens was dispatched only in round\(s\) 1"):
        build({"reviewers": roster, "findings": [_finding("code-reviewer", 2)],
               "verdict": "clean"}, review_dir=rd)

    # Declared, dispatched, did NOT return: it raised nothing.
    rd2 = _round(tmp_path / "two", ("code-reviewer", 1))
    roster2 = _roster(("code-reviewer", 1)) + _roster(("crew:fail-closed", 1),
                                                       returned=False)
    with pytest.raises(attest_mod.AttestError, match="did not return"):
        build({"reviewers": roster2,
               "findings": [_finding("crew:fail-closed", 1)],
               "verdict": "clean"}, review_dir=rd2)


def test_the_join_is_checked_before_the_verdict(build, tmp_path):
    """Ordering, so the actionable error is the one handed back. The payload
    is one BOTH checks refuse — a `clean` verdict over a still-`confirmed`
    finding that is also misattributed — so only the order decides which
    message surfaces (with a payload that satisfied the verdict checks,
    moving the join after them would leave this test green)."""
    rd = _round(tmp_path, ("code-reviewer", 1))
    with pytest.raises(attest_mod.AttestError, match="no roster entry declares"):
        build({"reviewers": _roster(("code-reviewer", 1)),
               "findings": [_finding("crew:fail-closed", 1,
                                     status="confirmed")],
               "verdict": "clean"}, review_dir=rd)


# --- without --review-dir: optional, but never contradictory --------------------


def test_without_review_dir_the_fields_are_optional(build):
    legacy = build({"reviewers": [{"role": "reviewer", "agent": "x"}],
                    "findings": [_finding(None, None)], "verdict": "clean"})
    assert "lens" not in legacy["findings"][0]
    assert "round" not in legacy["findings"][0]


def test_without_review_dir_a_stated_lens_still_joins_the_roster(build):
    """A caller that volunteers the attribution gets it checked: a lens the
    roster never declared is a contradiction whether or not a directory was
    offered. A lens the roster does declare, with no rounds on either side,
    joins on the lens alone."""
    with pytest.raises(attest_mod.AttestError, match="crew:fail-closed"):
        build({"reviewers": [{"role": "code-reviewer", "agent": "x"}],
               "findings": [_finding("crew:fail-closed", None)],
               "verdict": "clean"})
    ok = build({"reviewers": [{"role": "code-reviewer", "agent": "x"}],
                "findings": [_finding("code-reviewer", None)],
                "verdict": "clean"})
    assert ok["findings"][0]["lens"] == "code-reviewer"


@pytest.mark.parametrize("lens", ["fail-closed", "crew-fail-closed", "Crew:x"])
def test_a_lens_outside_the_vocabulary_fails_the_schema_itself(build, lens):
    """Even with no review dir: the vocabulary is in the contract, not only
    on the check, so a consumer's validator refuses the same spellings."""
    with pytest.raises(attest_mod.AttestError, match="invalid"):
        build({"reviewers": [{"role": lens, "agent": "x"}],
               "findings": [_finding(lens, 1)], "verdict": "clean"})


def test_round_zero_and_non_integer_rounds_fail_the_schema(build):
    """Both placements: the finding's `minimum` as well as the roster's, so
    deleting either turns this red."""
    for bad in (0, -1, "1", 1.5):
        with pytest.raises(attest_mod.AttestError, match="invalid"):
            build({"reviewers": [{"role": "code-reviewer", "agent": "x",
                                  "round": bad}],
                   "findings": [], "verdict": "clean"})
        with pytest.raises(attest_mod.AttestError, match="invalid"):
            build({"reviewers": [{"role": "code-reviewer", "agent": "x"}],
                   "findings": [_finding("code-reviewer", bad)],
                   "verdict": "clean"})


def test_a_float_round_is_refused_not_silently_unjoined(build, tmp_path):
    """Two definitions of "integer". JSON Schema's `type: integer` accepts
    `2.0`; Python's `isinstance(int)` does not. A finding with `round: 2.0`
    passes the schema and the presence check, and if `_round_of` read it as
    "no round" the join would compare nothing — the finding attested against
    a lens never dispatched in round 2, and ingest dropping the round. On the
    roster side `1.0` must not be refused with a message that calls it
    absent. Both are refusals, and both name the value."""
    rd = _round(tmp_path, ("code-reviewer", 1))
    with pytest.raises(attest_mod.AttestError, match=r"round 2\.0 is not"):
        build({"reviewers": _roster(("code-reviewer", 1)),
               "findings": [_finding("code-reviewer", 2.0)],
               "verdict": "clean"}, review_dir=rd)
    roster = _roster(("code-reviewer", 1))
    roster[0]["round"] = 1.0
    with pytest.raises(attest_mod.AttestError, match=r"round 1\.0 is not") as e:
        build({"reviewers": roster, "findings": [], "verdict": "clean"},
              review_dir=rd)
    assert "states no" not in str(e.value), "a present value was reported as absent"
    # And without a review dir, where nothing is required, a stated float
    # round is still a stated value in the wrong shape, not an absent one.
    with pytest.raises(attest_mod.AttestError, match=r"round 2\.0 is not"):
        build({"reviewers": [{"role": "code-reviewer", "agent": "x"}],
               "findings": [_finding("code-reviewer", 2.0)],
               "verdict": "clean"})


def test_a_trailing_newline_is_not_a_second_spelling(build, tmp_path):
    """The schema pattern ends in `$`, which under ECMA-262 is end of input
    and under Python `re` also matches before a trailing newline — so
    jsonschema accepts `crew:fail-closed\\n` as a vocabulary member, and it
    would become a second corpus key for one lens, the exact defect the
    vocabulary exists to end. warden's own
    check is the strict one, and it runs on every lens whether or not a
    review dir was offered."""
    bad = "crew:fail-closed\n"
    assert not attest_mod.lens_is_valid(bad)
    with pytest.raises(attest_mod.AttestError, match="not in the lens vocabulary"):
        build({"reviewers": [{"role": bad, "agent": "x"}],
               "findings": [_finding(bad, None)], "verdict": "clean"})
    rd = _round(tmp_path, ("crew:fail-closed", 1))
    roster = _roster(("crew:fail-closed", 1))
    roster[0]["role"] = bad
    with pytest.raises(attest_mod.AttestError, match="not in the lens vocabulary"):
        build({"reviewers": roster, "findings": [], "verdict": "clean"},
              review_dir=rd)


def test_a_refusal_for_a_missing_field_names_the_stale_pack_cause(
        build, tmp_path):
    """The version-skew cell: a consumer already passing --review-dir from a
    pre-0.9.0 skill pack meets this refusal on the first write after a pin
    bump. The message names that cause and its remedy, so the operator is not
    sent to edit a roster by hand that the pack will write correctly once
    updated."""
    rd = _round(tmp_path, ("code-reviewer", 1))
    roster = _roster(("code-reviewer", 1))
    del roster[0]["round"]
    with pytest.raises(attest_mod.AttestError, match="skill pack"):
        build({"reviewers": roster, "findings": [], "verdict": "clean"},
              review_dir=rd)
    f = _finding(); del f["lens"]
    with pytest.raises(attest_mod.AttestError, match="skill pack"):
        build({"reviewers": _roster(("code-reviewer", 1)), "findings": [f],
               "verdict": "clean"}, review_dir=rd)


# --- additive, on the corpus this repo actually committed ----------------------


def _reconstruct(shard: dict) -> dict:
    """A memory shard folded back into the attestation it came from — the
    envelope's provenance plus its records as findings — so it can be
    validated against the contract the way the artifact was."""
    doc = {"head_sha": shard.get("sha", ""), "base_sha": shard.get("base_sha", ""),
           "rules_version": shard.get("rules_version", ""),
           "reviewed_at": shard.get("reviewed_at", ""),
           "reviewers": shard.get("reviewers", []),
           "verdict": shard.get("verdict", ""), "findings": []}
    for k in ("pr", "bead", "range_patch_ids", "roster_verification",
              "round_binding"):
        if k in shard:
            doc[k] = shard[k]
    for r in shard.get("records", []):
        doc["findings"].append({k: r[k] for k in (
            "rule_id", "severity", "file", "line", "finding", "evidence",
            "status", "reason", "tags", "lens", "round") if k in r})
    return doc


@needs_corpus
def test_the_fields_are_additive_so_every_committed_shard_stays_valid():
    """Consumer blast radius, asserted rather than asserted-about, and on the
    real corpus rather than a fixture: the schema WITHOUT the three fields and
    `$defs` (the pre-attribution contract, reconstructed) against the schema
    WITH them, over every committed shard.

    The invariant is ONE-DIRECTIONAL. That the two contracts agree on every
    shard is a claim no change of this shape can survive, because the round
    that reviews it commits the first shard that USES the new fields and the
    pre-attribution contract refuses it as `additionalProperties`. What
    additive means is that nothing the old contract accepted is refused by the
    new one, and that a shard the old one refuses is refused for exactly one
    reason — it carries `lens` or `round` — proved by stripping them and
    watching the two agree again.
    """
    schema = json.loads(SCHEMA_PATH.read_text())
    assert "lens" not in schema["properties"]["findings"]["items"]["required"]
    assert "round" not in schema["properties"]["findings"]["items"]["required"]
    assert set(schema["properties"]["reviewers"]["items"]["required"]) == {
        "role", "agent"}
    assert schema["properties"]["reviewers"]["items"]["properties"]["role"] == {
        "type": "string", "minLength": 1} | {
        k: v for k, v in schema["properties"]["reviewers"]["items"]
        ["properties"]["role"].items() if k == "description"}, (
        "`role` must stay a free string in the contract: 22 spellings are "
        "already committed, and the vocabulary is enforced on the write path")
    for obj in (schema, schema["properties"]["reviewers"]["items"],
                schema["properties"]["findings"]["items"]):
        assert obj["additionalProperties"] is False

    pre = copy.deepcopy(schema)
    del pre["$defs"]
    del pre["properties"]["findings"]["items"]["properties"]["lens"]
    del pre["properties"]["findings"]["items"]["properties"]["round"]
    del pre["properties"]["reviewers"]["items"]["properties"]["round"]
    base, head = Draft202012Validator(pre), Draft202012Validator(schema)
    shard_dir = Path(__file__).resolve().parents[1] / attest_mod.SHARD_DIR
    n = agree = attributed = pre_envelope = 0
    for p in sorted(shard_dir.glob("*.json")):
        d = json.loads(p.read_text())
        if d.get("source", "attest") != "attest":
            continue
        n += 1
        doc = _reconstruct(d)
        if "reviewers" not in d and "verdict" not in d:
            # predates the review EVENT on the envelope:
            # neither contract can accept a reconstruction with no roster and
            # no verdict, and no schema change of ours can rescue one
            assert not base.is_valid(doc) and not head.is_valid(doc), (
                f"{p.name} has no roster and no verdict on its envelope, so "
                "`_reconstruct` substitutes `reviewers: []` and `verdict: \"\"` "
                "— and both contracts must refuse those substituted defaults "
                "(`minItems: 1`, the verdict enum). One of them accepted this "
                "reconstruction, so the branch is counting a shard it cannot "
                "account for: base "
                + ("accepted" if base.is_valid(doc) else "refused")
                + ", head "
                + ("accepted" if head.is_valid(doc) else "refused"))
            pre_envelope += 1
            continue
        # The load-bearing half, and the one a tightening cannot hide from:
        # `pre` is `schema` minus three properties, so ANY other narrowing
        # lands in both and the two go on agreeing. Asserting the SHIPPED
        # contract accepts every enveloped shard on the corpus is what
        # notices it; comparing only the two is a check no tightening could
        # ever fail.
        assert head.is_valid(doc), (
            f"{p.name} is a committed shard the shipped contract refuses: "
            + "; ".join(e.message for e in list(head.iter_errors(doc))[:3]))
        carries = any("lens" in f or "round" in f for f in doc["findings"]) \
            or any("round" in r for r in doc["reviewers"])
        assert base.is_valid(doc) is not carries, (
            f"{p.name}: the pre-2c0 contract must refuse a shard exactly when "
            "it carries the new fields, and accept it otherwise")
        if carries:
            attributed += 1
        else:
            agree += 1
    assert n and agree + attributed + pre_envelope == n
    assert agree and attributed, (
        "the corpus must hold both kinds for this to have compared anything: "
        f"{agree} pre-attribution, {attributed} attributed, "
        f"{pre_envelope} pre-envelope, {n} total")


# --- the corpus half ----------------------------------------------------------


def test_lens_and_round_ride_into_records_only_when_stated():
    att = {"head_sha": HEAD, "base_sha": BASE, "reviewed_at": "2026-09-02T00:00:00+00:00",
           "findings": [_finding("crew:fail-closed", 2),
                        _finding(None, None),
                        _finding(5, "2")]}
    records = memory_mod.build_records(att)
    assert records[0]["lens"] == "crew:fail-closed" and records[0]["round"] == 2
    assert "lens" not in records[1] and "round" not in records[1], (
        "an unattributed finding acquired a default — absent must stay "
        "absent, because absent is not 'code-reviewer'")
    assert "lens" not in records[2] and "round" not in records[2], (
        "a value of the wrong type reached the corpus")


def test_stats_tally_outcome_per_lens_and_per_round_and_count_the_rest(
        tmp_path):
    """The reader the field exists for. Unattributed records are counted
    APART, never folded into a lens: records that predate the fields are
    evidence that cannot answer the question, and a tally that made them
    look like they could would conflate them with attributed evidence."""
    def rec(status, lens=None, rnd=None, line=1):
        r = {"rule_id": "scope-creep", "status": status, "tags": [],
             "file": "a.py", "line": line, "sha": HEAD, "ts": "2026-09-02T00:00:00+00:00"}
        if lens:
            r["lens"], r["round"] = lens, rnd
        return r
    records = [rec("fixed", "code-reviewer", 1, 1),
               rec("refuted", "code-reviewer", 1, 2),
               rec("fixed", "crew:fail-closed", 2, 3),
               rec("dismissed-with-reason", None, None, 4),
               rec("confirmed", None, None, 5),
               # lens but no usable round: a shape allowed without
               # --review-dir, and a bool that Python would count as round 1
               dict(rec("fixed", "builder", None, 6), round=None),
               dict(rec("fixed", "builder", None, 7), round=True)]
    doc = memory_mod.stats(tmp_path, records=records)
    assert doc["per_lens"]["code-reviewer"] == {
        "confirmed": 0, "fixed": 1, "refuted": 1, "dismissed": 0, "n": 2}
    assert doc["per_lens"]["crew:fail-closed"]["n"] == 1
    assert doc["per_round"][1]["n"] == 2 and doc["per_round"][2]["n"] == 1
    # `True in {1: ...}` is True by hash equality, so the proof that the bool
    # was not folded into round 1 is the count: round 1 stays at n=2.
    assert set(doc["per_round"]) == {1, 2}
    assert doc["lens_unrecorded"] == 2
    assert doc["round_unrecorded"] == 2, (
        "records with a lens and no usable round vanished from per_round "
        "with no counter (R1-04)")
    out = memory_mod.render_stats(doc)
    assert "LENSES" in out and "lens code-reviewer: n=2" in out
    assert "round 2: n=1" in out
    assert "2 judged record(s) carry no lens" in out
    assert "2 lens-attributed record(s) carry no round" in out


def test_stats_render_nothing_about_lenses_on_a_corpus_with_none(tmp_path):
    """A pre-attribution corpus prints the count of what it lacks, not empty
    tables."""
    doc = memory_mod.stats(tmp_path, records=[
        {"rule_id": "scope-creep", "status": "fixed", "tags": [], "file": "a.py",
         "line": 1, "sha": HEAD, "ts": "2026-09-02T00:00:00+00:00"}])
    assert doc["per_lens"] == {} and doc["lens_unrecorded"] == 1
    out = memory_mod.render_stats(doc)
    assert "1 judged record(s) carry no lens" in out
    assert "carry no round" not in out, (
        "the round accounting line printed with no lens-attributed "
        "record missing a round — it is conditional on a non-zero count")


# --- the probe a pinned consumer's skill relies on -----------------------------


def test_the_cli_help_names_lens_so_a_skill_can_probe_for_support(capsys):
    """The skill pack ships from the default branch; a consumer's warden is a
    pin. The pre-pr-review skill probes `attest write --help` for
    `--review-dir` before writing the structured roster, and
    for `lens` before writing the attribution — so the word must be in the
    help of exactly that command."""
    from warden import cli as cli_mod

    with pytest.raises(SystemExit):
        cli_mod.main(["attest", "--help"])
    out = capsys.readouterr().out
    assert "--review-dir" in out and "lens" in out and "round" in out


# --- attribution text must tell the reader the right thing ------------------


def test_r2_3_the_round_unrecorded_line_blames_the_finding_not_the_roster(
        build, tmp_path):
    """The line counts the right records and must name the right cause.

    `build_records` copies `round` off the FINDING and never off the roster
    entry it joins to, so a lens-bearing record with no round is a finding
    that stated none — whatever the roster said. A wording blaming 'a roster
    that stated none' is falsified end to end by this case: the roster states
    round 1, the write is accepted, and the record still lands on that line.
    """
    payload = {"reviewers": _roster(("code-reviewer", 1)),
               "findings": [_finding("code-reviewer", None)],
               "verdict": "clean"}
    doc = build(payload)          # no --review-dir: attribution is optional
    records = memory_mod.build_records(doc)
    assert records[0]["lens"] == "code-reviewer" and "round" not in records[0]

    rendered = memory_mod.render_stats(memory_mod.stats(tmp_path, records=[
        {"rule_id": "scope-creep", "status": "fixed", "tags": [],
         "file": "a.py", "line": 1, "sha": HEAD,
         "ts": "2026-09-02T00:00:00+00:00", "lens": "code-reviewer"}]))
    line = next(ln for ln in rendered.splitlines()
                if "carry no round" in ln)
    assert "roster that stated none" not in line, (
        "the round-unrecorded line blames the roster for a count taken off "
        "the finding — the roster in this very payload stated round 1 (R2-3)")
    assert "FINDING" in line and "roster's round is never copied" in line


def test_r2_4_verify_attribution_documents_that_the_schema_is_only_a_floor():
    """The docstring must not claim everything reaching the function 'has the
    declared shape' or that the vocabulary is a schema error: the body is
    stricter than the schema in exactly two places. This asserts the two
    shapes really are schema-valid-and-refused, then that the contract text
    says so; a docstring claiming otherwise fails here.
    """
    schema = json.loads(SCHEMA_PATH.read_text())
    assert Draft202012Validator({**schema["$defs"]["lens"]}).is_valid(
        "crew:fail-closed\n"), (
        "the premise moved: the schema now refuses a trailing newline, so "
        "this test's reason to exist should be re-derived, not deleted")
    assert not attest_mod.lens_is_valid("crew:fail-closed\n")
    # BOTH round constraints are READ from the shipped schema, never retyped:
    # a literal `{"type": "integer", "minimum": 1}` here would be an
    # assertion about jsonschema that no edit in this repo could falsify, so
    # tightening the contract would leave the docstring stale and this green.
    rounds = [schema["properties"][side]["items"]["properties"]["round"]
              for side in ("reviewers", "findings")]
    assert len(rounds) == 2
    for constraint in rounds:
        assert Draft202012Validator(constraint).is_valid(2.0), (
            "the premise moved: the shipped round constraint now refuses a "
            "float, so this test's reason to exist should be re-derived")
    with pytest.raises(attest_mod.AttestError):
        attest_mod._round_of(2.0, who="code-reviewer")

    doc = " ".join((attest_mod.verify_attribution.__doc__ or "").split())
    assert "the schema is the floor here, not the contract" in doc
    assert "trailing newline" in doc and "2.0" in doc
    assert "everything read here has the declared shape" not in doc, (
        "verify_attribution's docstring claims the schema pass settles what "
        "the function then re-checks more strictly (R2-4)")


# --- The reading instrument ------------------------------------------------
#
# The command that renders the LENSES section answers from a derived cache. A
# cache written before the first lens-carrying shard makes `memory stats`
# render no lens rows at all and report `N judged record(s) carry no lens` — a
# true sentence about a stale corpus, indistinguishable from a corpus that
# genuinely carries no attribution — while the same report's REVIEW EVENTS
# line is counted from the shard directory, live. Two corpora in one report
# must not go unsaid.


def _stats_repo(tmp_path: Path, *, shard_records: list[dict] | None = None,
                cache_records: list[dict] | None = None) -> Path:
    """A repo whose shard store and derived cache can be set INDEPENDENTLY.

    That independence is the point: a fixture that wrote both from one list
    could not express a store newer than its cache.
    """
    from tests.conftest import REPO_YAML, RULES
    root = tmp_path / "repo"
    (root / ".warden" / "rules").mkdir(parents=True)
    (root / "repo.yaml").write_text(REPO_YAML)
    for name, content in RULES.items():
        (root / ".warden" / "rules" / name).write_text(content)
    attest = root / ".warden" / "memory" / "attest"
    attest.mkdir(parents=True)
    cache = root / ".warden" / "memory" / memory_mod.CACHE_NAME
    cache.write_text("".join(json.dumps(r) + "\n"
                             for r in (cache_records or [])))
    for i, rec in enumerate(shard_records or []):
        (attest / f"2026090{i}T000000Z-aaaa-bbbb.json").write_text(json.dumps(
            {"schema": 1, "source": "attest", "sha": HEAD, "base_sha": BASE,
             "rules_version": "v123", "reviewed_at": "2026-09-02T00:00:00+00:00",
             "verdict": "findings", "records": [rec]}))
    return root


def _record(**over) -> dict:
    rec = {"rule_id": "scope-creep", "status": "fixed", "tags": [],
           "file": "a.py", "line": 1, "sha": HEAD, "seq": 0,
           "source": "attest", "base_sha": BASE, "severity": "MEDIUM",
           "finding": "f", "evidence": "e", "origin": "interactive",
           "dir_prefix": "", "ts": "2026-09-02T00:00:00+00:00"}
    rec.update(over)
    return rec


def test_memory_stats_names_the_corpus_it_computed_over(tmp_path):
    """The missing sentence. A precision row is read BEFORE any caveat under
    the table it belongs to, so the provenance is a header: which file, how
    many rows, when it was written, and how many shards sit beside it."""
    root = _stats_repo(tmp_path, shard_records=[_record()],
                       cache_records=[_record()])
    line = memory_mod.render_corpus_provenance(
        memory_mod.cache_provenance(root)).splitlines()[0]
    assert line.startswith("CORPUS —"), line
    for needle in (".warden/memory/findings.jsonl", "1 row(s)",
                   "1 shard(s)", "warden memory ingest", "DERIVED"):
        assert needle in line, (
            f"the corpus provenance line lost {needle!r} — a reader who cannot "
            f"tell which corpus answered cannot tell a real zero from a stale "
            f"one: {line}")


def test_a_cache_older_than_the_committed_shards_reads_as_stale(tmp_path):
    """Shards newer than the cache. The count is named, because `run ingest`
    without a number reads as advice and this is the reason every figure
    below it is wrong."""
    root = _stats_repo(tmp_path, shard_records=[_record(), _record()],
                       cache_records=[_record()])
    cache = root / ".warden" / "memory" / memory_mod.CACHE_NAME
    import os
    old = 1_600_000_000
    os.utime(cache, (old, old))
    doc = memory_mod.cache_provenance(root)
    assert doc["stale"] is True and doc["newer_shards"] == 2, doc
    rendered = memory_mod.render_corpus_provenance(doc)
    assert "STALE" in rendered and "2 of 2 committed shard(s)" in rendered, (
        f"a cache behind the committed store did not say so: {rendered}")
    assert "warden memory ingest" in rendered


def test_a_current_cache_is_not_cried_stale(tmp_path):
    """The other half, and the one a warning-only test cannot hold: a cache
    rebuilt after the last shard must render NO stale line. A provenance check
    that fires always is a provenance check nobody reads."""
    root = _stats_repo(tmp_path, shard_records=[_record()],
                       cache_records=[_record()])
    cache = root / ".warden" / "memory" / memory_mod.CACHE_NAME
    import os
    os.utime(cache, (2_000_000_000, 2_000_000_000))
    doc = memory_mod.cache_provenance(root)
    assert doc["stale"] is False and doc["problem"] is None, doc
    assert "STALE" not in memory_mod.render_corpus_provenance(doc)


def test_an_unreadable_store_reads_as_stale_rather_than_as_current(tmp_path):
    """Fail-closed, the policy's own `## Build disciplines` reading: a check
    that cannot evaluate behaves like a hit. An unlistable shard dir must not
    render as a cache that is up to date, and must not raise either — a report
    that dies computing its own header is worse than one that says it cannot
    tell."""
    import os
    root = _stats_repo(tmp_path, shard_records=[_record()],
                       cache_records=[_record()])
    attest = root / ".warden" / "memory" / "attest"
    os.chmod(attest, 0o000)
    try:
        doc = memory_mod.cache_provenance(root)
    finally:
        os.chmod(attest, 0o755)
    assert doc["stale"] is True and doc["shards"] is None, doc
    assert "cannot be listed" in (doc["problem"] or ""), doc
    # A missing cache is the other unreadable state, and it reads as an EMPTY
    # corpus today — which is exactly how a fresh worktree answers 0 findings.
    missing = _stats_repo(tmp_path / "b", shard_records=[_record()])
    (missing / ".warden" / "memory" / memory_mod.CACHE_NAME).unlink()
    doc = memory_mod.cache_provenance(missing)
    assert doc["stale"] is True and "EMPTY corpus" in (doc["problem"] or ""), doc


def test_the_renderer_was_never_the_defect(tmp_path):
    """Three candidate causes for missing lens rows — the renderer dropping
    them, `judged` excluding the lens-carrying subset, or the source. Fed
    records that carry a lens, the renderer prints a row per lens and the
    judged filter keeps every one, so the source is the only cause left
    standing."""
    doc = memory_mod.stats(tmp_path, records=[
        _record(lens="crew:fail-closed", round=1),
        _record(lens="crew:fail-closed", round=2, status="confirmed"),
        _record(lens="code-reviewer", round=1, status="refuted")])
    assert doc["lens_unrecorded"] == 0, doc
    rendered = memory_mod.render_stats(doc)
    assert "lens crew:fail-closed: n=2" in rendered, rendered
    assert "lens code-reviewer: n=1" in rendered, rendered
    assert "carry no lens" not in rendered, (
        "a corpus where every judged record carries a lens still printed the "
        "unattributed accounting line")


def test_the_stats_command_itself_prints_the_corpus_line(tmp_path, capsys,
                                                             monkeypatch):
    """Tests-bite: helper tests that call `cache_provenance` directly leave
    the CLI wiring — the call, the stdout provenance line and the stderr
    STALE repeat — a deletable subject. This drives `warden memory stats`
    itself."""
    import os
    from warden import cli as cli_mod
    root = _stats_repo(tmp_path, shard_records=[_record(), _record()],
                       cache_records=[_record()])
    cache = root / ".warden" / "memory" / memory_mod.CACHE_NAME
    os.utime(cache, (1_600_000_000, 1_600_000_000))
    monkeypatch.chdir(root)
    assert cli_mod.main(["memory", "stats"]) == 0
    out, err = capsys.readouterr()
    # The provenance line comes FIRST, above every row it qualifies.
    assert out.splitlines()[0].startswith("CORPUS —"), out.splitlines()[:2]
    assert "2 shard(s)" in out and ".warden/memory/findings.jsonl" in out
    # ...and the staleness is repeated on stderr, where a human acts on it.
    assert "STALE CORPUS" in err and "warden memory ingest" in err, err


def test_a_dangling_shard_store_is_not_reported_as_a_current_cache(tmp_path):
    """Fail-closed. `iterdir` raises FileNotFoundError for a DANGLING SYMLINK
    exactly as it does for an absent directory, and the absent case is the
    one benign state — so a symlink taking the "a real, empty store" branch
    would render `stale: False`, `problem: None`: a false CURRENT, the one
    direction the docstring says cannot happen. `records_from_shards` already
    refuses this shape by name."""
    import os
    root = _stats_repo(tmp_path, cache_records=[_record()])
    attest = root / ".warden" / "memory" / "attest"
    attest.rmdir()
    os.symlink(root / "does-not-exist", attest)
    doc = memory_mod.cache_provenance(root)
    assert doc["stale"] is True, doc
    assert doc["shards"] is None, doc
    assert "dangling symlink" in (doc["problem"] or ""), doc
    assert "STALE" in memory_mod.render_corpus_provenance(doc)


def test_a_missing_cache_still_counts_the_store_beside_it(tmp_path):
    """The store is counted first, unconditionally. A missing-cache branch
    that returned BEFORE listing the store — the state of every fresh clone
    and every CI run — would render a repo holding unread shards and one
    holding none identically as "unknown shard(s)"."""
    root = _stats_repo(tmp_path, shard_records=[_record(), _record(), _record()])
    (root / ".warden" / "memory" / memory_mod.CACHE_NAME).unlink()
    doc = memory_mod.cache_provenance(root)
    assert doc["shards"] == 3 and doc["stale"] is True, doc
    rendered = memory_mod.render_corpus_provenance(doc)
    assert "3 shard(s)" in rendered and "EMPTY corpus" in rendered, rendered
    assert "unknown" not in rendered, rendered


def test_an_empty_store_with_no_cache_is_not_stale(tmp_path):
    """The other side of the missing-cache arm: a repo with no cache AND no
    committed shards is consistent, not behind. The
    portability fixture `examples/hello-svc` ships that shape, and warning there
    spends the signal on the one repo that cannot be stale."""
    root = _stats_repo(tmp_path)
    (root / ".warden" / "memory" / memory_mod.CACHE_NAME).unlink()
    doc = memory_mod.cache_provenance(root)
    assert doc["shards"] == 0 and doc["stale"] is False, doc
    assert doc["problem"] is None, doc
    assert "STALE" not in memory_mod.render_corpus_provenance(doc)


def test_recall_also_reports_a_stale_corpus(tmp_path, capsys, monkeypatch):
    """Tests-bite: `memory recall` carries the same STALE signal as `stats`,
    and deleting its wiring must turn a test red. Recall's empty answer is
    the one a builder acts on, so this drives the command and asserts the
    warning reaches stderr."""
    import os
    from warden import cli as cli_mod
    root = _stats_repo(tmp_path, shard_records=[_record(), _record()],
                       cache_records=[])
    os.utime(root / ".warden" / "memory" / memory_mod.CACHE_NAME,
             (1_600_000_000, 1_600_000_000))
    (root / "a.py").write_text("x = 1\n")
    monkeypatch.chdir(root)
    assert cli_mod.main(["memory", "recall", "--files", "a.py",
                         "--for", "reviewer"]) == 0
    out, err = capsys.readouterr()
    assert "no relevant history" in out, out
    assert "STALE CORPUS" in err and "warden memory ingest" in err, err
    # The cause is stated at a seam with no rows under it, so it must not
    # promise any.
    assert "precision row below" not in err, err


# --- the dispatch denominator splits zero-yield ---------------


def test_stats_split_zero_yield_into_reviewed_clean_and_no_review(tmp_path):
    """The review-depth ledger (docs/design/review-depth-20260910.md, Table
    A) leads with `zero-yield` — dispatches that came back with nothing — and
    every lens-retirement bar rests on that denominator. 'Reviewed and found
    nothing' and 'did not review' are two numbers, per lens, read off the
    committed rosters; a shard written before the field carries none
    and is counted UNKNOWN, never upgraded."""
    from tests.test_memory_clean_review import (
        clean_attestation, seed_rules, write_attest_artifact)
    rules_dir = seed_rules(tmp_path, "scope-creep")
    with_outcomes = [
        {"role": "code-reviewer", "agent": "s", "round": 1, "returned": True,
         "findings": 0, "output": "a.json", "outcome": "reviewed-clean"},
        {"role": "crew:fail-closed", "agent": "s", "round": 1,
         "returned": True, "findings": 0, "output": "b.json",
         "outcome": "no-review"},
        {"role": "crew:fail-closed", "agent": "s", "round": 1,
         "returned": True, "findings": 3, "output": "c.json",
         "outcome": "reviewed-findings"},
        {"role": "cross-examiner", "agent": "s", "round": 1,
         "returned": False, "outcome": "no-review"}]
    write_attest_artifact(tmp_path, "20260914T000000Z",
                          clean_attestation(reviewers=with_outcomes,
                                            roster_verification="verified"))
    # an older shard: returned, zero findings, no outcome — unknown
    write_attest_artifact(tmp_path, "20260914T000100Z", clean_attestation(
        head_sha="d" * 40,
        reviewers=[{"role": "code-reviewer", "agent": "s", "round": 1,
                    "returned": True, "findings": 0, "output": "a.json"}]))
    memory_mod.ingest(tmp_path, rules_dir=rules_dir)
    doc = memory_mod.stats(tmp_path, rules_dir)
    d = doc["dispatches"]
    assert d["code-reviewer"] == {
        "dispatches": 2, "returned": 2, "reviewed-clean": 1,
        "reviewed-findings": 0, "no-review": 0, "unknown": 1}, d
    assert d["crew:fail-closed"] == {
        "dispatches": 2, "returned": 2, "reviewed-clean": 0,
        "reviewed-findings": 1, "no-review": 1, "unknown": 0}, d
    assert d["cross-examiner"]["returned"] == 0
    assert d["cross-examiner"]["no-review"] == 1
    out = memory_mod.render_stats(doc)
    assert "DISPATCHES" in out
    assert ("dispatch code-reviewer: dispatches=2 returned=2 reviewed-clean=1 "
            "reviewed-findings=0 no-review=0 unknown=1") in out, out
    assert "unknown" in out and "never upgraded" in out


def test_stats_render_no_dispatch_table_on_a_corpus_with_no_roster(tmp_path):
    doc = memory_mod.stats(tmp_path, records=[])
    assert doc["dispatches"] == {}
    assert "DISPATCHES" not in memory_mod.render_stats(doc)


def test_the_cli_help_names_outcome_so_a_skill_can_probe_for_support(capsys):
    """Same shape as `lens` and `--review-dir`: the pack ships from the
    default branch, a consumer's warden is a pin, and a pinned warden older
    than this refuses `outcome` as an additional property."""
    from warden import cli as cli_mod

    with pytest.raises(SystemExit):
        cli_mod.main(["attest", "--help"])
    assert "outcome" in capsys.readouterr().out


def test_an_outcome_nothing_corroborated_counts_as_unknown_never_as_clean(tmp_path):
    """`derive_outcomes` carries a declared outcome through when no review
    dir was offered — the builder's word, on a roster stamped
    `unverified-roster`. The dispatch table must not read that word as if
    warden had derived it, or a builder-typed `reviewed-clean` counts as a
    verified clean review: the exact claim the field exists to stop being
    free. An outcome on a roster nothing cross-checked counts `unknown`."""
    from tests.test_memory_clean_review import (
        clean_attestation, seed_rules, write_attest_artifact)
    rules_dir = seed_rules(tmp_path, "scope-creep")
    declared = [
        {"role": "code-reviewer", "agent": "s", "round": 1, "returned": True,
         "findings": 0, "output": "a.json", "outcome": "reviewed-clean"},
        {"role": "crew:fail-closed", "agent": "s", "round": 1,
         "returned": True, "findings": 2, "output": "b.json",
         "outcome": "reviewed-findings"}]
    write_attest_artifact(tmp_path, "20260914T000000Z", clean_attestation(
        reviewers=declared, roster_verification="unverified-roster"))
    write_attest_artifact(tmp_path, "20260914T000100Z", clean_attestation(
        head_sha="d" * 40, reviewers=[dict(declared[0])]))   # no state at all
    memory_mod.ingest(tmp_path, rules_dir=rules_dir)
    d = memory_mod.stats(tmp_path, rules_dir)["dispatches"]
    assert d["code-reviewer"]["reviewed-clean"] == 0, (
        f"a builder-declared reviewed-clean nothing corroborated was counted "
        f"as a clean review: {d['code-reviewer']}")
    assert d["code-reviewer"]["unknown"] == 2, d["code-reviewer"]
    assert d["crew:fail-closed"]["reviewed-findings"] == 0, d["crew:fail-closed"]
    assert d["crew:fail-closed"]["unknown"] == 1, d["crew:fail-closed"]
