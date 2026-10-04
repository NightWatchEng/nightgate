"""Skill-step provenance and the quiet-step report.

A rule carries covers:, a backtest, a precision history, and a pause with a
reason. A skill step with none of that only ever accumulates, because nothing
measures it and nobody wants to be the one who deletes a guardrail. This is
the measuring half: a step that exists because of an
incident CITES it, and the retro can name a step whose cited class has gone
quiet — WITHOUT proposing its removal on that basis alone, because a guard
that prevents a class from ever recurring produces no findings by working
(the same fail-open vs real-zero line the miner already draws).
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from warden import provenance as prov

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _rec(tag: str, status: str, days_ago: int, seq: int = 0) -> dict:
    ts = (NOW - timedelta(days=days_ago)).isoformat()
    return {"rule_id": f"unmapped:{tag}", "file": "x", "status": status,
            "ts": ts, "tags": [tag], "seq": seq}


PROV_TEXT = """# Deliver

Body.

## Provenance

Earned steps and the incidents behind them.

```yaml
as_of: 2026-08-27
steps:
  - step: "re-branch before the first commit of every item"
    evidence: agentops-dtx.12
  - step: "fail-closed when a check cannot evaluate"
    evidence: tag:fail-open
```

## Boundaries
Never merges.
"""


def test_parse_reads_as_of_and_steps():
    doc = prov.parse_provenance_section(PROV_TEXT)
    assert doc is not None
    assert doc["as_of"] == "2026-08-27"
    assert len(doc["steps"]) == 2
    assert doc["steps"][0]["evidence"] == "agentops-dtx.12"
    assert doc["steps"][1]["evidence"] == "tag:fail-open"


def test_parse_absent_section_returns_none():
    assert prov.parse_provenance_section("# Skill\n\nNo provenance here.\n") is None


def test_parse_requires_a_yaml_fence_not_just_a_heading():
    # A heading with prose but no machine-readable block is a stub, not a
    # provenance record — the reader must not silently accept it.
    stub = "## Provenance\n\nWe should add this someday.\n\n## Boundaries\nx\n"
    with pytest.raises(prov.ProvenanceError):
        prov.parse_provenance_section(stub)


def test_classify_evidence_kinds():
    vocab = {"fail-open": "…"}
    assert prov.classify_evidence("tag:fail-open", vocab, {})[0] == "tag"
    assert prov.classify_evidence("tag:not-a-real-tag", vocab, {})[0] == "unrecognized-tag"
    assert prov.classify_evidence("agentops-dtx.12", vocab, {})[0] == "incident"
    assert prov.classify_evidence("commit:e61f22f", vocab, {})[0] == "commit"
    assert prov.classify_evidence("shard:20260827T-x.json", vocab, {})[0] == "shard"
    assert prov.classify_evidence("just some prose", vocab, {})[0] == "malformed"


def test_quiet_step_is_a_tag_with_history_but_nothing_fresh():
    # fail-open was judged 200 days ago and never since → quiet, a REVIEW
    # candidate. FRESH_DAYS is 90.
    entries = [{"skill": "deliver", "step": "fail-closed", "evidence": "tag:fail-open"}]
    records = [_rec("fail-open", "confirmed", 200)]
    vocab = {"fail-open": "…"}
    rows = prov.quiet_steps(entries, records, vocab=vocab, now=NOW)
    (row,) = rows
    assert row["verdict"] == "review"
    assert row["judged_total"] == 1
    assert row["judged_fresh"] == 0


def test_an_active_tag_is_not_quiet():
    entries = [{"skill": "deliver", "step": "fail-closed", "evidence": "tag:fail-open"}]
    records = [_rec("fail-open", "confirmed", 10)]  # inside the fresh window
    rows = prov.quiet_steps(entries, records, vocab={"fail-open": "…"}, now=NOW)
    assert rows[0]["verdict"] == "active"
    assert rows[0]["judged_fresh"] == 1


def test_no_verdict_is_ever_a_removal():
    # THE acceptance guard: the retro names a quiet step, it never proposes
    # removal on that basis alone. No row may carry a remove/removal verdict.
    entries = [
        {"skill": "d", "step": "a", "evidence": "tag:fail-open"},
        {"skill": "d", "step": "b", "evidence": "agentops-dtx.12"},
        {"skill": "d", "step": "c", "evidence": "tag:not-real"},
        {"skill": "d", "step": "e", "evidence": "garbage"},
    ]
    records = [_rec("fail-open", "confirmed", 200)]
    rows = prov.quiet_steps(entries, records, vocab={"fail-open": "…"}, now=NOW)
    for row in rows:
        assert "remove" not in row["verdict"].lower()
        assert "delete" not in row["verdict"].lower()


def test_an_incident_citation_is_unmeasured_not_quiet():
    # A one-time incident (a bead) is provenance, not a recurring class. It
    # must never read as "quiet", or every step earned from a single bug
    # would look removable the day it landed.
    entries = [{"skill": "d", "step": "branch first", "evidence": "agentops-dtx.12"}]
    rows = prov.quiet_steps(entries, [], now=NOW)
    assert rows[0]["verdict"] == "unmeasured"


def test_a_tag_with_no_history_is_distinct_from_quiet():
    # Zero judged EVER is not the same as "was active, went quiet". It may be
    # a brand-new guard, or a guard working so well the class never recurs —
    # the real-zero case. Reported as its own verdict, never as review.
    entries = [{"skill": "d", "step": "x", "evidence": "tag:fail-open"}]
    rows = prov.quiet_steps(entries, [], vocab={"fail-open": "…"}, now=NOW)
    assert rows[0]["verdict"] == "no-history"


def test_an_unrecognized_tag_is_surfaced_as_a_provenance_error():
    entries = [{"skill": "d", "step": "x", "evidence": "tag:invented-class"}]
    rows = prov.quiet_steps(entries, [], vocab={"fail-open": "…"}, now=NOW)
    assert rows[0]["verdict"] == "unrecognized-tag"


def test_refuted_history_still_counts_as_judged_activity():
    # A class reviewers keep REFUTING is still active in the corpus — the step
    # is being exercised, so it is not quiet. Judged means judged, upheld or not.
    entries = [{"skill": "d", "step": "x", "evidence": "tag:fail-open"}]
    records = [_rec("fail-open", "refuted", 5)]
    rows = prov.quiet_steps(entries, records, vocab={"fail-open": "…"}, now=NOW)
    assert rows[0]["verdict"] == "active"


def test_detected_but_unjudged_records_do_not_count():
    # A gate detection nobody judged is not evidence the class recurred in
    # review — the same exclusion skill_recurrence and the candidate bar make.
    entries = [{"skill": "d", "step": "x", "evidence": "tag:fail-open"}]
    records = [_rec("fail-open", "detected", 5)]
    rows = prov.quiet_steps(entries, records, vocab={"fail-open": "…"}, now=NOW)
    assert rows[0]["verdict"] == "no-history"


def test_an_undateable_timestamp_does_not_flip_a_quiet_class_to_active():
    """Fail-closed: a judged record whose ts is empty/corrupt must NOT count
    as fresh. Reusing _age_days (undateable->0.0, i.e. fresh) would let a
    single dateless record flip a genuinely-quiet class to `active` and bury
    the review candidate — here `fresh` is the benign
    outcome, so undateable->fresh is a fail-open on corpus-integrity data."""
    entries = [{"skill": "d", "step": "x", "evidence": "tag:fail-open"}]
    old = _rec("fail-open", "confirmed", 200)          # genuinely quiet
    dateless = _rec("fail-open", "confirmed", 0)
    dateless["ts"] = ""                                # corrupt/missing ts
    rows = prov.quiet_steps(entries, [old, dateless],
                            vocab={"fail-open": "…"}, now=NOW)
    assert rows[0]["verdict"] == "review", "an undateable ts flipped quiet->active"
    assert rows[0]["judged_total"] == 2                # counted toward history
    assert rows[0]["judged_fresh"] == 0                # never toward fresh


def test_a_malformed_block_is_reported_not_raised(tmp_path):
    """Fail-closed: stats() builds this report,
    and certify calls stats() to judge the promotion bar OUTSIDE its
    ValueError guard. A single unparseable ## Provenance block must surface as
    a reported row, never propagate a ProvenanceError that crashes the whole
    certify ladder over a skill-doc typo in an unrelated check."""
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    (good / "SKILL.md").write_text(PROV_TEXT)
    (bad / "SKILL.md").write_text(
        "# Bad\n\n## Provenance\n\nwe will add this later\n\n## Boundaries\nx\n")

    # read_pack_provenance stores the error rather than raising
    pack = prov.read_pack_provenance(tmp_path)
    assert isinstance(pack["bad"], dict) and pack["bad"].get("error")

    # provenance_report is resilient AND surfaces the bad block as a row
    rows = prov.provenance_report(tmp_path, [], vocab={"fail-open": "…"}, now=NOW)
    bad_rows = [r for r in rows if r["skill"] == "bad"]
    assert bad_rows and bad_rows[0]["verdict"] == "malformed-block"
    # the good skill's steps still came through
    assert any(r["skill"] == "good" for r in rows)
    assert "PROVENANCE ERROR bad" in "\n".join(prov.render_quiet_steps(rows))


def test_an_unreadable_skill_file_is_reported_not_raised(tmp_path, monkeypatch):
    """Fail-closed: the resilience must cover the read_text() half too. An
    OSError reading a SKILL.md (no permission, or a race) is the SAME
    crash-the-certify-ladder fail-mode as a malformed block — it is stored as
    an error row, never raised."""
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    (good / "SKILL.md").write_text(PROV_TEXT)
    (bad / "SKILL.md").write_text("# placeholder\n")

    real_read = Path.read_text

    def flaky(self, *a, **k):
        if self.parent.name == "bad":
            raise PermissionError("simulated unreadable SKILL.md")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", flaky)
    pack = prov.read_pack_provenance(tmp_path)  # must not raise
    assert isinstance(pack["bad"], dict) and pack["bad"].get("error")
    rows = prov.provenance_report(tmp_path, [], vocab={"fail-open": "…"}, now=NOW)
    assert any(r["skill"] == "bad" and r["verdict"] == "malformed-block"
               for r in rows)


# ── Harness assumptions and the fail-closed pre-flight ──────────────────────
#
# One shape recurs: a skill's assumption about its environment silently stops
# being true (a renamed invocation, a stale installed pack, a cache serving
# retired versions, a pre-flight that checks existence but not validity).
# These tests pin the answer: skills DECLARE what they assume, and a
# pre-flight resolves the declarations against what is installed — failing
# closed, never accepting a file's existence as proof that what it names
# resolves.

ASSUMES_TEXT = """# Orchestrate

Body that runs `nightgate-skills:deliver` per builder.

## Provenance

```yaml
as_of: 2026-08-30
assumes:
  skills: [deliver]
  subagents: true
steps: []
```
"""


def _mk_skill(root: Path, name: str, body: str = "",
              frontmatter_name: str | None = "SAME",
              provenance: str = "```yaml\nas_of: 2026-08-30\nsteps: []\n```"):
    """A minimal pack skill. frontmatter_name=None writes NO frontmatter;
    'SAME' names itself correctly; any other string mis-names it."""
    fm_name = name if frontmatter_name == "SAME" else frontmatter_name
    fm = "" if frontmatter_name is None else \
        f"---\nname: {fm_name}\ndescription: x\n---\n\n"
    (root / name).mkdir()
    (root / name / "SKILL.md").write_text(
        f"{fm}# {name}\n\n{body}\n\n## Provenance\n\n{provenance}\n")


def test_parse_reads_the_assumes_block():
    doc = prov.parse_provenance_section(ASSUMES_TEXT)
    assert doc["assumes"]["skills"] == ["deliver"]
    assert doc["assumes"]["subagents"] is True


def test_parse_normalizes_an_absent_assumes_block():
    # Absent declaration means nothing declared — vacuously resolvable, and
    # exactly as skills behaved before the key existed.
    doc = prov.parse_provenance_section(PROV_TEXT)
    assert doc["assumes"] == {"skills": [], "subagents": None}


def test_a_misspelled_assumes_key_is_refused():
    # `skill:` instead of `skills:` would declare NOTHING and pass every
    # check — the exact "existence checked, validity not" shape.
    bad = ASSUMES_TEXT.replace("  skills: [deliver]", "  skill: [deliver]")
    with pytest.raises(prov.ProvenanceError):
        prov.parse_provenance_section(bad)


def test_assumes_shapes_are_validated_not_trusted():
    for mangled in (
            ASSUMES_TEXT.replace("  skills: [deliver]", "  skills: deliver"),
            ASSUMES_TEXT.replace("  subagents: true", "  subagents: yes please"),
            ASSUMES_TEXT.replace("assumes:\n  skills: [deliver]\n  subagents: true",
                                 "assumes: a string")):
        with pytest.raises(prov.ProvenanceError):
            prov.parse_provenance_section(mangled)


def _problems(rows):
    return [r for r in rows if r["status"] in ("unresolved", "unreadable")]


def test_preflight_resolves_a_declared_sibling(tmp_path):
    _mk_skill(tmp_path, "a", provenance=(
        "```yaml\nas_of: 2026-08-30\nassumes:\n  skills: [b]\nsteps: []\n```"))
    _mk_skill(tmp_path, "b")
    assert _problems(prov.preflight_assumptions(tmp_path)) == []


def test_preflight_names_the_sibling_the_pack_does_not_serve(tmp_path):
    """THE acceptance: a skill naming a sibling the installed
    pack does not serve fails the pre-flight WITH the name it could not
    resolve — proved by removing that skill from a fixture pack."""
    _mk_skill(tmp_path, "a", provenance=(
        "```yaml\nas_of: 2026-08-30\nassumes:\n  skills: [b]\nsteps: []\n```"))
    _mk_skill(tmp_path, "b")
    assert _problems(prov.preflight_assumptions(tmp_path)) == []
    # remove b from the pack — the assumption is now stale
    (tmp_path / "b" / "SKILL.md").unlink()
    (tmp_path / "b").rmdir()
    problems = _problems(prov.preflight_assumptions(tmp_path))
    assert problems, "a vanished sibling passed the pre-flight"
    assert any(p["name"] == "b" and p["skill"] == "a" for p in problems), (
        f"the failure does not name the unresolved skill: {problems}")


def test_file_existence_is_never_accepted_as_resolution(tmp_path):
    """Verifying that a file EXISTS is version-blind; what it names has to
    RESOLVE. A SKILL.md whose
    frontmatter mis-names or lacks the skill does not resolve it."""
    _mk_skill(tmp_path, "a", provenance=(
        "```yaml\nas_of: 2026-08-30\nassumes:\n  skills: [b, c]\nsteps: []\n```"))
    _mk_skill(tmp_path, "b", frontmatter_name="something-else")
    _mk_skill(tmp_path, "c", frontmatter_name=None)
    problems = _problems(prov.preflight_assumptions(tmp_path))
    assert {p["name"] for p in problems} >= {"b", "c"}, (
        f"existence passed as resolution: {problems}")


def test_a_prose_reference_is_checked_even_when_undeclared(tmp_path):
    """A hand-authored text can name a skill the pack no longer serves. The
    pre-flight scans the body for
    `nightgate-skills:<name>` refs and resolves those too — an undeclared
    reference is not an unchecked one."""
    _mk_skill(tmp_path, "a", body="Then run `nightgate-skills:ghost`.")
    problems = _problems(prov.preflight_assumptions(tmp_path))
    assert any(p["name"] == "ghost" for p in problems), (
        f"a prose reference to a missing skill passed: {problems}")


def test_an_unreadable_declaration_fails_closed(tmp_path):
    # A ## Provenance block that cannot be parsed means the assumptions
    # cannot be read — that is a failure, never a pass.
    _mk_skill(tmp_path, "a", provenance="prose, no yaml fence")
    problems = _problems(prov.preflight_assumptions(tmp_path))
    assert any(p["skill"] == "a" and p["status"] == "unreadable"
               for p in problems)


def test_a_missing_pack_is_a_failure_not_a_pass(tmp_path):
    rows = prov.preflight_assumptions(tmp_path / "no-such-pack")
    assert _problems(rows), "an absent pack pre-flighted clean"


def test_an_existing_but_skill_less_pack_is_not_a_pass(tmp_path, sample_repo,
                                                       monkeypatch, capsys):
    """The absent-dir arm alone would leave a vacuous pass — a directory that
    EXISTS but serves no skill (an empty cache dir, or --pack pointed at the
    plugin ROOT whose children hold no SKILL.md directly) examines nothing and
    would exit 0. Nothing examined is never a pass."""
    from warden import cli as cli_mod
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _problems(prov.preflight_assumptions(empty)), (
        "an empty pack directory pre-flighted clean")
    # the plugin-root shape: skills live one level down, so the root itself
    # serves nothing
    root = tmp_path / "plugin-root"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / "skills").mkdir()
    _mk_skill(root / "skills", "deliver")
    assert _problems(prov.preflight_assumptions(root)), (
        "--pack at the plugin root examined nothing and pre-flighted clean")
    # and through the CLI both are could-not-run (2), never 0
    monkeypatch.chdir(sample_repo)
    assert cli_mod.main(["skills", "preflight", "--pack", str(empty)]) == 2
    assert cli_mod.main(["skills", "preflight", "--pack", str(root)]) == 2
    err = capsys.readouterr().err
    assert "DID NOT RUN" in err


def test_a_falsy_assumes_skills_shape_is_refused():
    """A FALSY wrong shape is not 'nothing declared': `raw.get("skills") or
    []` would coerce every one before validation ran — the silent-drift shape
    the validator's own docstring promises to raise on."""
    for falsy in ("  skills: false", "  skills: ''", "  skills: 0"):
        mangled = ASSUMES_TEXT.replace("  skills: [deliver]", falsy)
        with pytest.raises(prov.ProvenanceError):
            prov.parse_provenance_section(mangled)


def test_a_yaml_quoted_frontmatter_name_still_resolves(tmp_path):
    """`name: "b"` is legal YAML that every frontmatter parser serves as the
    skill b — a raw-text compare would refuse it and false-block an honest
    pack."""
    _mk_skill(tmp_path, "a", provenance=(
        "```yaml\nas_of: 2026-08-30\nassumes:\n  skills: [b, c]\nsteps: []\n```"))
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "SKILL.md").write_text(
        '---\nname: "b"\ndescription: x\n---\n\n# b\n')
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "SKILL.md").write_text(
        "---\nname: 'c'\ndescription: x\n---\n\n# c\n")
    assert _problems(prov.preflight_assumptions(tmp_path)) == [], (
        "a YAML-quoted served name was refused as unresolved")


def test_a_subagents_assumption_is_surfaced_never_verified(tmp_path):
    # Whether the harness serves subagents is not machine-checkable here; the
    # declaration is surfaced as `assumed` so the caller can see it, and the
    # skill text owes an observable degradation path. It must never render as
    # resolved — that would claim a check that does not exist.
    _mk_skill(tmp_path, "a", provenance=(
        "```yaml\nas_of: 2026-08-30\nassumes:\n  subagents: true\nsteps: []\n```"))
    rows = prov.preflight_assumptions(tmp_path)
    sub = [r for r in rows if r["kind"] == "subagents"]
    assert sub and sub[0]["status"] == "assumed"
    assert not _problems(rows)


def test_cli_skills_preflight_exit_codes(tmp_path, sample_repo, monkeypatch, capsys):
    """0 = every declared assumption resolves; 1 = an assumption is stale or
    unreadable (a blocking finding, named); 2 = no pack to check — the check
    did not run, which must never read as a pass."""
    from warden import cli as cli_mod
    monkeypatch.chdir(sample_repo)
    pack = tmp_path / "pack"
    pack.mkdir()
    _mk_skill(pack, "a", provenance=(
        "```yaml\nas_of: 2026-08-30\nassumes:\n  skills: [b]\nsteps: []\n```"))
    _mk_skill(pack, "b")
    assert cli_mod.main(["skills", "preflight", "--pack", str(pack)]) == 0
    (pack / "b" / "SKILL.md").unlink()
    assert cli_mod.main(["skills", "preflight", "--pack", str(pack)]) == 1
    out = capsys.readouterr().out
    # The REAL line, not single-letter needles: 'a'/'b' alone are satisfied
    # by the always-printed summary ('stale', 'unreadable') — a mutation
    # deleting the naming fields would stay green.
    assert "STALE ASSUMPTION a -> b" in out, (
        "the failure output no longer names the skill and its unresolved "
        "sibling")
    assert cli_mod.main(
        ["skills", "preflight", "--pack", str(tmp_path / "absent")]) == 2
