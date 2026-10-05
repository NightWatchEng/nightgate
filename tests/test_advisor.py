"""warden rules recommend — the gap analysis.

The join that turns three inputs into a recommendation: the shipped catalog
(what guardrails exist), the review corpus (what has gone wrong HERE), and
`.warden/rules/` (what this repo already enforces).

The distinction this module exists to protect is EVIDENCE vs PRIOR ART. An
entry the corpus has records for is recommended on evidence; an entry with no
local signal is recommended on prior art alone. Conflating them would let an
unevidenced rule wear the authority of a measured one, the most damaging thing
this command could do.

Every link is DECLARED, never inferred. A rule says which catalog entry it
implements; a catalog entry says which corpus defect-classes it corresponds
to. Guessing which rule subsumes which class is the unevidenced claim the
whole report exists to avoid — the same principle `covers:` is built on.
"""

from pathlib import Path

import pytest
import yaml

from conftest import seed_rules

from warden import advisor
from warden import catalog as cat
from warden import cli
from warden import rules as rules_mod

ENTRY = {
    "id": "swallowed-exceptions",
    "taxonomy": "A10:2025",
    "name": "Mishandling of exceptional conditions",
    "guards": "Failures caught and discarded.",
    "engine": "declarative",
    "applies_when": "always",
    "false_positive_cost": "An optional cleanup step is flagged.",
    "sources": [{"title": "OWASP", "url": "https://owasp.org/Top10/2025/"}],
    "starter": {"checks": [{"id": "except-pass", "pattern": r"except[^:\n]*:\s*\n\s*pass\b",
                            "message": "exception swallowed", "scope": "file",
                            "flags": ["m"]}]},
}


def write_catalog(tmp_path: Path, *entries: dict) -> Path:
    p = tmp_path / "catalog.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "entries": list(entries)}))
    return p


def rule_file(root: Path, rule_id: str, body: str = "", **meta) -> None:
    d = root / ".warden" / "rules"
    d.mkdir(parents=True, exist_ok=True)
    front = {"id": rule_id, "severity": "MEDIUM", "engine": "claude",
             "applies_to": ["**"], **meta}
    (d / f"{rule_id}.md").write_text(
        "---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n"
        + (body or "a rule body\n"))


# --------------------------------------------------------------------------
# the declared links — nothing inferred
# --------------------------------------------------------------------------


def test_a_rule_declares_which_catalog_entry_it_implements(tmp_path):
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"])
    rules = rules_mod.load_rules(tmp_path / ".warden" / "rules")
    assert rules[0].implements == ("swallowed-exceptions",)


def test_a_rule_implementing_an_unknown_entry_is_rejected(tmp_path):
    """A typo'd link is a link to nothing, and it would read as coverage."""
    rule_file(tmp_path, "no-swallow", implements=["no-such-entry"])
    with pytest.raises(rules_mod.RuleError, match="no-such-entry"):
        rules_mod.load_rules(tmp_path / ".warden" / "rules")


def test_implements_defaults_to_empty(tmp_path):
    rule_file(tmp_path, "plain")
    assert rules_mod.load_rules(tmp_path / ".warden" / "rules")[0].implements == ()


def test_a_catalog_entry_may_declare_its_corpus_classes(tmp_path):
    entry = dict(ENTRY, corpus_classes=["fail-open"])
    loaded = cat.load_catalog(write_catalog(tmp_path, entry))
    assert loaded[0].corpus_classes == ("fail-open",)


def test_corpus_classes_must_be_well_formed_slugs(tmp_path):
    entry = dict(ENTRY, corpus_classes=["Not A Slug"])
    with pytest.raises(cat.CatalogError, match="slug"):
        cat.load_catalog(write_catalog(tmp_path, entry))


# --------------------------------------------------------------------------
# the gap analysis
# --------------------------------------------------------------------------


def test_an_implemented_entry_is_reported_enforced_not_recommended(tmp_path):
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"])
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    assert [e.entry_id for e in report.enforced] == ["swallowed-exceptions"]
    assert report.recommendations == ()


# --------------------------------------------------------------------------
# a PAUSED rule enforces nothing
# --------------------------------------------------------------------------

# A second REAL catalog id, so the paused and the active rule can implement
# different entries in one tree — `implements:` is validated against the
# shipped catalog, so these cannot be invented.
ENTRY_2 = dict(ENTRY, id="logging-and-alerting-gaps",
               name="Logging and alerting gaps")


def test_a_paused_rule_does_not_count_its_entry_as_enforced(tmp_path):
    """review.py skips paused rules, so a paused rule enforces NOTHING.

    Reporting its catalog entry as ALREADY ENFORCED is a fail-open in the one
    command whose job is "what does this repo not enforce".
    """
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"],
              paused=True, paused_reason="auto-paused on a 3-refutation streak")
    report = advisor.recommend(tmp_path,
                               catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    assert [e.entry_id for e in report.enforced] == []
    assert [r.entry_id for r in report.recommendations] == ["swallowed-exceptions"]


def test_a_reopened_entry_names_the_rule_that_is_paused(tmp_path):
    """Not merely dropped: the row carries the trail from "why is this back"
    to "because that rule is paused, for this reason"."""
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"],
              paused=True, paused_reason="auto-paused on a 3-refutation streak")
    report = advisor.recommend(tmp_path,
                               catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    caveats = " ".join(report.recommendations[0].caveats)
    assert "no-swallow" in caveats
    assert "PAUSED" in caveats
    assert "auto-paused on a 3-refutation streak" in caveats


def test_only_the_paused_rule_s_entry_reopens(tmp_path):
    """The control: an ACTIVE rule's entry must stay enforced in the same
    run. A fix that dropped every implements: link would pass the assertion
    above and be catastrophically wrong."""
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"],
              paused=True, paused_reason="refuted three times running")
    rule_file(tmp_path, "log-gaps", implements=["logging-and-alerting-gaps"])
    report = advisor.recommend(
        tmp_path, catalog_path=write_catalog(tmp_path, ENTRY, ENTRY_2),
        records=[])
    assert [e.entry_id for e in report.enforced] == ["logging-and-alerting-gaps"]
    assert [r.entry_id for r in report.recommendations] == ["swallowed-exceptions"]


def test_an_entry_an_active_rule_also_implements_stays_enforced(tmp_path):
    """Two rules on one entry, one paused: the ACTIVE one still enforces it,
    so the entry must not reopen, must be credited to the LIVE rule, and must
    carry no pause note anywhere. The paused rule is named to sort FIRST, so
    a fix that let it claim the enforcing slot cannot pass on load order.
    """
    rule_file(tmp_path, "a-swallow-paused", implements=["swallowed-exceptions"],
              paused=True, paused_reason="refuted three times running")
    rule_file(tmp_path, "z-swallow-live", implements=["swallowed-exceptions"])
    report = advisor.recommend(tmp_path,
                               catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    assert [(e.entry_id, e.rule_id) for e in report.enforced] == [
        ("swallowed-exceptions", "z-swallow-live")]
    assert report.recommendations == ()
    assert "PAUSED" not in advisor.render(report)


def test_the_pause_reason_survives_into_the_rendered_report(tmp_path):
    """The guard the founder actually reads is the rendered text, not the
    dataclass."""
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"],
              paused=True, paused_reason="auto-paused on a 3-refutation streak")
    report = advisor.recommend(tmp_path,
                               catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    out = advisor.render(report)
    assert "UNANSWERED: 1" in out
    assert "ALREADY ENFORCED" not in out
    assert "no-swallow" in out and "PAUSED" in out
    assert "auto-paused on a 3-refutation streak" in out


def test_a_pause_that_reopens_an_entry_is_reported_as_a_limit(tmp_path):
    """The trail must survive the paths that never build a recommendation —
    an entry the catalog calls NOT-A-RULE is `declined` and carries no row,
    so the pause would vanish entirely without the LIMITS line."""
    entry = {k: v for k, v in ENTRY.items() if k != "starter"}
    entry.update(engine="not-a-rule",
                 instead="argue it in review; no engine can check it")
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"],
              paused=True, paused_reason="auto-paused on a 3-refutation streak")
    report = advisor.recommend(tmp_path,
                               catalog_path=write_catalog(tmp_path, entry),
                               records=[])
    assert report.recommendations == ()
    assert report.declined == ("swallowed-exceptions",)
    out = advisor.render(report)
    assert "no-swallow" in out and "PAUSED" in out
    assert "auto-paused on a 3-refutation streak" in out


def test_an_unimplemented_entry_is_recommended(tmp_path):
    rule_file(tmp_path, "unrelated")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    assert [r.entry_id for r in report.recommendations] == ["swallowed-exceptions"]


def test_a_recommendation_with_corpus_records_is_evidence_based(tmp_path):
    rule_file(tmp_path, "unrelated")
    entry = dict(ENTRY, corpus_classes=["fail-open"])
    records = [{"rule_id": "unmapped:fail-open", "tags": ["fail-open"],
                "status": "fixed", "file": "warden/x.py"} for _ in range(6)]
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, entry),
                               records=records)
    rec = report.recommendations[0]
    assert rec.basis == "evidence"
    assert rec.corpus_n == 6
    assert "6" in rec.rationale


def test_a_recommendation_with_no_local_signal_is_prior_art(tmp_path):
    """The distinction that must never blur: prior art is not evidence."""
    rule_file(tmp_path, "unrelated")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    rec = report.recommendations[0]
    assert rec.basis == "prior-art"
    assert rec.corpus_n == 0


def test_evidence_outranks_prior_art(tmp_path):
    rule_file(tmp_path, "unrelated")
    quiet = dict(ENTRY, id="weak-cryptography", taxonomy="A04:2025",
                 corpus_classes=[], starter={"checks": [
                     {"id": "md5", "pattern": r"\bmd5\(", "message": "broken hash"}]})
    loud = dict(ENTRY, corpus_classes=["fail-open"])
    records = [{"rule_id": "unmapped:fail-open", "tags": ["fail-open"],
                "status": "fixed", "file": "warden/x.py"} for _ in range(9)]
    report = advisor.recommend(
        tmp_path, catalog_path=write_catalog(tmp_path, quiet, loud), records=records)
    assert [r.entry_id for r in report.recommendations][0] == "swallowed-exceptions"
    assert report.recommendations[0].basis == "evidence"
    assert report.recommendations[1].basis == "prior-art"


def test_every_recommendation_states_its_rationale_in_one_line(tmp_path):
    """A rank a founder cannot interrogate will not be adopted, and should not
    be."""
    rule_file(tmp_path, "unrelated")
    entry = dict(ENTRY, corpus_classes=["fail-open"])
    records = [{"rule_id": "unmapped:fail-open", "tags": ["fail-open"],
                "status": "fixed", "file": "warden/x.py"} for _ in range(4)]
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, entry),
                               records=records)
    for rec in report.recommendations:
        assert rec.rationale.strip()
        assert "\n" not in rec.rationale


# --------------------------------------------------------------------------
# measured cost — a recommendation states what it would fire on TODAY
# --------------------------------------------------------------------------


def test_a_declarative_recommendation_measures_its_hits_on_this_tree(tmp_path):
    """A rule that fires noisily is worse than no rule. The count comes from
    running the starter, not from hoping."""
    rule_file(tmp_path, "unrelated")
    src = tmp_path / "svc.py"
    src.write_text("try:\n    go()\nexcept Exception:\n    pass\n")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    rec = report.recommendations[0]
    assert rec.measured_hits == 1
    assert "svc.py" in rec.hit_samples[0]


def test_zero_hits_is_reported_as_zero_not_as_unknown(tmp_path):
    rule_file(tmp_path, "unrelated")
    (tmp_path / "clean.py").write_text("x = 1\n")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    assert report.recommendations[0].measured_hits == 0


def test_a_judgment_entry_reports_hits_as_unmeasurable(tmp_path):
    """A judgment rule cannot be measured by running a pattern, and saying 0
    would claim a clean tree nobody checked."""
    rule_file(tmp_path, "unrelated")
    judged = {k: v for k, v in ENTRY.items() if k != "starter"}
    judged["engine"] = "claude"
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, judged),
                               records=[])
    assert report.recommendations[0].measured_hits is None


def test_a_python_entry_with_a_prose_sketch_is_unmeasurable_too(tmp_path):
    """The other disjunct. An engine:python starter is a `sketch:` — prose a
    reviewer implements from, with nothing to run. Without the engine check
    every python entry would report 'fires 0x', which is the
    claim-a-clean-tree failure this module exists to prevent."""
    rule_file(tmp_path, "unrelated")
    sketchy = dict(ENTRY, id="path-traversal", taxonomy="CWE-22",
                   engine="python", starter={"sketch": "find joins whose "
                                             "right-hand operand is not a literal"})
    (tmp_path / "real.py").write_text("try:\n    go()\nexcept Exception:\n    pass\n")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, sketchy),
                               records=[])
    assert report.recommendations[0].measured_hits is None, (
        "a prose sketch was 'measured' — nothing was run")


def test_the_corpus_being_unread_is_stated_in_the_report_not_only_on_stderr(tmp_path):
    """A committed artifact reading 'no local evidence (0 records)' when the
    corpus could not be READ is a measurement that never happened. The sibling
    miner draws exactly this line between a real zero and UNREAD."""
    rule_file(tmp_path, "unrelated")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[], corpus_unread="shard 3 is malformed")
    assert any("NOT READ" in n for n in report.notes)
    assert any("shard 3 is malformed" in n for n in report.notes)
    assert "not read" in advisor.render(report).lower()


def test_an_unreadable_ruleset_says_which_rule_and_why(tmp_path):
    """load_rules raises a path-anchored, actionable message; discarding it
    leaves the one place a reader would act on with nothing to act on."""
    d = tmp_path / ".warden" / "rules"
    d.mkdir(parents=True)
    (d / "broken.md").write_text("no frontmatter here\n")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    assert report.recommendations == ()
    assert any("broken.md" in n for n in report.notes), report.notes


def test_a_refuted_class_is_not_counted_as_evidence_for_it(tmp_path):
    """The corpus arguing a class DOWN is not support for adopting it."""
    rule_file(tmp_path, "unrelated")
    entry = dict(ENTRY, corpus_classes=["fail-open"])
    records = [{"rule_id": "unmapped:fail-open", "tags": ["fail-open"],
                "status": "refuted", "file": "warden/x.py"} for _ in range(5)]
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, entry),
                               records=records)
    rec = report.recommendations[0]
    assert rec.basis == "prior-art", "5 refutations were read as evidence FOR it"
    assert rec.corpus_n == 0
    assert "refuted" in rec.rationale


def test_a_rule_whose_only_history_is_refutation_is_unsupported(tmp_path):
    """The inverse: findings that argued a rule down must not shield it from
    the section that exists to surface rules with nothing behind them."""
    rule_file(tmp_path, "house-style")
    records = [{"rule_id": "house-style", "tags": [], "status": "refuted",
                "file": "a.py"} for _ in range(9)]
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=records)
    assert [u.rule_id for u in report.unsupported] == ["house-style"]


def test_a_scope_added_check_says_its_count_is_an_upper_bound(tmp_path):
    """declarative.run fires scope:added only on lines a DIFF adds. Counting
    existing matches answers a different question, so the row says so."""
    rule_file(tmp_path, "unrelated")
    added = dict(ENTRY, id="os-command-injection", taxonomy="CWE-78",
                 starter={"checks": [{"id": "shell-true",
                                      "pattern": r"shell\s*=\s*True",
                                      "message": "shell"}]})
    (tmp_path / "svc.py").write_text("subprocess.run(c, shell=True)\n")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, added),
                               records=[])
    rec = report.recommendations[0]
    assert rec.measured_hits == 1
    assert any("upper bound" in c for c in rec.caveats)


def test_a_scope_file_check_counts_one_per_file_like_the_engine_does(tmp_path):
    """declarative.run yields at most ONE finding per file for scope:file.
    Counting every match would overstate the rule's noise."""
    rule_file(tmp_path, "unrelated")
    (tmp_path / "many.py").write_text(
        "try:\n    a()\nexcept E:\n    pass\ntry:\n    b()\nexcept E:\n    pass\n")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    assert report.recommendations[0].measured_hits == 1


def test_a_require_check_is_excluded_rather_than_counted_backwards(tmp_path):
    """require:true fires when the pattern is ABSENT; counting presences would
    report the exact inverse of the rule's behaviour."""
    rule_file(tmp_path, "unrelated")
    inverted = dict(ENTRY, id="weak-cryptography", taxonomy="A04:2025",
                    starter={"checks": [{"id": "needs-header", "pattern": "LICENSE",
                                         "message": "missing header",
                                         "scope": "file", "require": True}]})
    (tmp_path / "a.py").write_text("LICENSE\n")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, inverted),
                               records=[])
    rec = report.recommendations[0]
    assert rec.measured_hits == 0
    assert any("require:true" in c for c in rec.caveats)


def test_the_scan_states_what_it_did_not_cover(tmp_path, monkeypatch):
    """A silent cap makes a partial scan read as a complete one — the standard
    the sibling `mine` command sets one row over in the same table."""
    rule_file(tmp_path, "unrelated")
    monkeypatch.setattr(advisor, "_MAX_SCAN_FILES", 1)
    for i in range(4):
        (tmp_path / f"f{i}.py").write_text("x = 1\n")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    assert any("stopped at its bound" in limit for limit in report.limits)
    assert "LIMITS" in advisor.render(report)


# --------------------------------------------------------------------------
# the inverse: rules with nothing behind them
# --------------------------------------------------------------------------


def test_a_rule_with_no_catalog_or_corpus_support_is_listed_separately(tmp_path):
    """Not to delete it — a project may have reasons the platform cannot see —
    but so the list is reviewed rather than accumulated forever."""
    rule_file(tmp_path, "house-style")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    assert [u.rule_id for u in report.unsupported] == ["house-style"]


def test_a_rule_with_corpus_records_is_not_called_unsupported(tmp_path):
    rule_file(tmp_path, "house-style")
    records = [{"rule_id": "house-style", "tags": [], "status": "fixed",
                "file": "a.py"} for _ in range(3)]
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=records)
    assert report.unsupported == ()


def test_an_implementing_rule_is_not_called_unsupported(tmp_path):
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"])
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    assert report.unsupported == ()


# --------------------------------------------------------------------------
# render + CLI
# --------------------------------------------------------------------------


def test_the_render_separates_evidence_from_prior_art(tmp_path):
    rule_file(tmp_path, "unrelated")
    entry = dict(ENTRY, corpus_classes=["fail-open"])
    quiet = dict(ENTRY, id="weak-cryptography", taxonomy="A04:2025",
                 starter={"checks": [{"id": "md5", "pattern": r"\bmd5\(",
                                      "message": "broken hash"}]})
    records = [{"rule_id": "unmapped:fail-open", "tags": ["fail-open"],
                "status": "fixed", "file": "warden/x.py"} for _ in range(5)]
    report = advisor.recommend(
        tmp_path, catalog_path=write_catalog(tmp_path, entry, quiet), records=records)
    text = advisor.render(report)
    assert "EVIDENCE" in text
    assert "PRIOR ART" in text
    lead = text.index("EVIDENCE")
    assert text.index("PRIOR ART") > lead, "evidence must lead the report"


def test_the_render_says_prior_art_is_not_evidence(tmp_path):
    rule_file(tmp_path, "unrelated")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    text = advisor.render(report).lower()
    assert "no local evidence" in text


def test_cli_rules_recommend_exits_0_and_writes_an_artifact(sample_repo, tmp_path,
                                                            monkeypatch, capsys):
    from conftest import copy_sample_repo
    root = copy_sample_repo(sample_repo, tmp_path / "repo")
    seed_rules(root, "house-style")
    monkeypatch.chdir(root)

    assert cli.main(["rules", "recommend"]) == 0
    out = capsys.readouterr().out
    assert "recommend" in out.lower()

    artifacts = sorted((root / ".warden" / "out").glob("*-rules-recommend/rule-recommendations.json"))
    assert len(artifacts) == 1


def test_measurement_never_scans_the_files_that_quote_the_patterns(tmp_path):
    """The declared self-reference class, in the shape it really takes: the
    hit comes from a starter's MESSAGE, not its pattern.

    `shell\\s*=\\s*True` does not match its own regex source, but it DOES
    match the message that explains it — "subprocess with shell=True — pass an
    argv list instead". So scanning the catalog makes the starter fire on its
    own documentation, reporting hits that are not real.
    """
    rule_file(tmp_path, "unrelated")
    shell_entry = dict(
        ENTRY, id="os-command-injection", taxonomy="CWE-78",
        starter={"checks": [{"id": "shell-true", "pattern": r"shell\s*=\s*True",
                             "message": "subprocess with shell=True — use argv"}]})

    guardrails = tmp_path / "warden" / "guardrails"
    guardrails.mkdir(parents=True)
    # The catalog stores that message verbatim — this is the self-reference.
    (guardrails / "catalog.yaml").write_text(
        '    message: "subprocess with shell=True — use argv"\n')
    (tmp_path / "real.py").write_text("subprocess.run(cmd, shell=True)\n")

    catalog_path = write_catalog(tmp_path, shell_entry)
    report = advisor.recommend(tmp_path, catalog_path=catalog_path, records=[])
    rec = report.recommendations[0]
    assert rec.measured_hits == 1, (
        f"the catalog's own message was counted: {rec.hit_samples}")
    assert all("guardrails" not in s for s in rec.hit_samples)


def test_declared_flags_are_honoured_when_measuring(tmp_path):
    """declarative.run honours a check's `flags`; forcing MULTILINE and
    dropping the rest moves the count in either direction. Unpinned until
    mutation showed the whole suite green without it."""
    rule_file(tmp_path, "unrelated")
    entry = dict(ENTRY, id="weak-cryptography", taxonomy="A04:2025",
                 starter={"checks": [{"id": "broken-hash", "pattern": r"MD5\(",
                                      "message": "broken hash", "flags": ["i"]}]})
    (tmp_path / "a.py").write_text("h = md5(x)\n")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, entry),
                               records=[])
    assert report.recommendations[0].measured_hits == 1, (
        "flags:[i] was ignored, so a case-insensitive rule measured as silent")


def test_declared_globs_narrow_the_measurement(tmp_path):
    """A check's `globs` restrict it within applies_to; ignoring them counts
    files the rule would never look at."""
    rule_file(tmp_path, "unrelated")
    entry = dict(ENTRY, id="security-misconfiguration", taxonomy="A02:2025",
                 starter={"checks": [{"id": "debug", "pattern": r"DEBUG\s*=\s*True",
                                      "message": "debug on",
                                      "globs": ["conf/**"]}]})
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf" / "settings.py").write_text("DEBUG = True\n")
    (tmp_path / "elsewhere.py").write_text("DEBUG = True\n")
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, entry),
                               records=[])
    assert report.recommendations[0].measured_hits == 1, (
        "globs were ignored, so a path-scoped rule measured the whole tree")


def test_a_gate_detection_is_not_counted_as_a_judged_record(tmp_path):
    """`detected` is a deterministic checker firing — a fact, not a verdict.
    memory.py excludes it from _JUDGED_STATUSES; the rationale says 'judged
    record(s)', so counting one here would make that sentence false."""
    rule_file(tmp_path, "unrelated")
    entry = dict(ENTRY, corpus_classes=["fail-open"])
    records = [{"rule_id": "unmapped:fail-open", "tags": ["fail-open"],
                "status": "detected", "file": "warden/x.py"} for _ in range(7)]
    report = advisor.recommend(tmp_path, catalog_path=write_catalog(tmp_path, entry),
                               records=records)
    assert report.recommendations[0].corpus_n == 0
    assert report.recommendations[0].basis == "prior-art"


def test_no_test_copies_the_git_fixture_unguarded():
    """No whole-repo copy of the git-backed fixture without excluding `.git`.

    Copying `.git` races git's background maintenance: `shutil.Error` on
    `.git/objects/maintenance.lock`, a file created and removed while
    copytree is walking, can fail one pytest run and pass the next on the
    same commit.

    A flaky gate is worse than a red one: it teaches people to re-run until
    green. `conftest.copy_sample_repo` is the guarded way, and every test
    file is held to it.

    Scoped to copies of the repo ROOT. A copy of a SUBPATH (the rules dir,
    say) cannot contain `.git` and is not this hazard; flagging one would
    make the guard an overbroad pattern.

    This docstring deliberately does NOT spell the call it hunts: the guard
    reads every line of every test file, so spelling it here would match its
    own prose — the self-reference class this repo already records.
    """
    import re
    whole_repo = re.compile(r"copytree\(\s*sample_repo\s*,")
    offenders = []
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if whole_repo.search(line) and "ignore" not in line:
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, (
        "copytree of the git-backed sample fixture without an ignore — use "
        "conftest.copy_sample_repo instead:\n  " + "\n  ".join(offenders))


def test_the_pause_note_does_not_claim_a_count_that_did_not_happen(tmp_path):
    """The declined and answered-clean paths `continue` before a row is
    built, so the entry is not counted at all, and the pause note on those
    paths must not say it is "counted UNANSWERED rather than enforced". That
    note would contradict the report's own "UNANSWERED: 0" header in the same
    output. The LIMITS line exists ONLY for those two paths, so it has to be
    true for exactly them.
    """
    entry = {k: v for k, v in ENTRY.items() if k != "starter"}
    entry.update(engine="not-a-rule",
                 instead="argue it in review; no engine can check it")
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"],
              paused=True, paused_reason="a 3-refutation streak")
    report = advisor.recommend(tmp_path,
                               catalog_path=write_catalog(tmp_path, entry),
                               records=[])
    out = advisor.render(report)
    assert report.recommendations == ()
    assert "PAUSED" in out, "the pause must still be reported"
    assert "counted \nUNANSWERED" not in out and "counted UNANSWERED" not in out, \
        f"the note claims a count the report's own header contradicts:\n{out}"
    assert "answered or declined here" in out


def test_a_reopened_entry_reports_its_pause_once_not_twice(tmp_path):
    """On the ordinary path the pause sentence is emitted once, as the row's
    `caveat:`, not verbatim again as a LIMITS bullet. The LIMITS line exists
    for entries that build NO row; emitting both teaches the reader that a
    doubled message is normal."""
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"],
              paused=True, paused_reason="a 3-refutation streak")
    report = advisor.recommend(tmp_path,
                               catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    assert len(report.recommendations) == 1, "a reopened entry lands in the gap"
    out = advisor.render(report)
    assert out.count("implements this entry but is PAUSED") == 1, \
        f"the pause is reported twice:\n{out}"
    assert "reopens into the gap" in out, \
        "on this path the entry really is counted, and the note should say so"


def test_a_second_unpaused_rule_keeps_the_entry_enforced_and_silent(tmp_path):
    """The code deliberately keeps an entry enforced when an UNPAUSED rule
    also implements it, so the reopening is not unconditional — and this repo
    ships exactly that pair (`fail-closed` and `swallowed-exceptions` both
    implement `swallowed-exceptions`).

    This is a characterization test, not a bug guard: it pins behaviour the
    docs describe rather than a defect in the code. It earns its place by
    mutation instead: dropping the `eid not in implemented` filter makes it
    fail. It stops the code drifting away from the docs, which is the
    direction that would make the qualifier a lie.
    """
    # named so the PAUSED rule sorts first — otherwise a load-order bug would
    # pass here for the wrong reason
    rule_file(tmp_path, "a-paused-judge", implements=["swallowed-exceptions"],
              paused=True, paused_reason="a 3-refutation streak")
    rule_file(tmp_path, "z-live-checker", implements=["swallowed-exceptions"])
    report = advisor.recommend(tmp_path,
                               catalog_path=write_catalog(tmp_path, ENTRY),
                               records=[])
    assert report.recommendations == (), "an active rule still enforces it"
    assert [e.rule_id for e in report.enforced] == ["z-live-checker"]
    out = advisor.render(report)
    assert "PAUSED" not in out, \
        f"a still-enforced entry must not carry a pause note:\n{out}"


# --- the ceiling guard on a machine pause ---------------------------------

def test_recommend_can_simulate_a_pause_without_writing_one(tmp_path):
    """`autonomy pause` has to know what a pause would COST
    before it writes one, and measuring it by editing the rule file first is
    the opposite of a guard — the breach would already exist on disk.
    """
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"])
    cat = write_catalog(tmp_path, ENTRY)
    assert advisor.recommend(tmp_path, catalog_path=cat, records=[]
                             ).recommendations == (), "enforced while active"

    simulated = advisor.recommend(tmp_path, catalog_path=cat, records=[],
                                  paused_extra={"no-swallow"})
    assert len(simulated.recommendations) == 1, \
        "the simulated pause must reopen the entry"
    assert "PAUSED" in advisor.render(simulated)
    # ...and nothing was written
    from warden import rules as rules_mod
    assert not any(r.paused for r in
                   rules_mod.load_rules(tmp_path / ".warden" / "rules"))


def test_a_simulated_pause_of_an_unknown_rule_changes_nothing(tmp_path):
    """paused_extra names rules, not entries. A rule id nobody declares must
    not silently reopen anything — a typo in the simulation would otherwise
    read as a breach and refuse a pause that is actually fine."""
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"])
    cat = write_catalog(tmp_path, ENTRY)
    assert advisor.recommend(tmp_path, catalog_path=cat, records=[],
                             paused_extra={"ghost"}).recommendations == ()


# --------------------------------------------------------------------------
# the default rules dir is the DECLARED one
# --------------------------------------------------------------------------


def test_recommend_defaults_to_the_declared_rules_dir(tmp_path):
    """recommend's fallback is the declared `review.rules_dir`, not a
    hardcoded `.warden/rules`: on a consumer layout a hardcoded fallback reads
    a rule the gate enforces as absent and recommends its entry as a gap."""
    (tmp_path / "repo.yaml").write_text(
        "review:\n  rules_dir: policy/rules\n")
    d = tmp_path / "policy" / "rules"
    d.mkdir(parents=True)
    front = {"id": "no-swallow", "severity": "MEDIUM", "engine": "claude",
             "applies_to": ["**"], "implements": ["swallowed-exceptions"]}
    (d / "no-swallow.md").write_text(
        "---\n" + yaml.safe_dump(front, sort_keys=False) + "---\na body\n")
    report = advisor.recommend(
        tmp_path, catalog_path=write_catalog(tmp_path, ENTRY), records=[])
    assert [e.entry_id for e in report.enforced] == ["swallowed-exceptions"]
    assert report.recommendations == ()


def test_recommend_fails_closed_on_an_unreadable_rules_dir_declaration(tmp_path):
    """With repo.yaml unreadable the true rules dir is unknown; reading the
    default and reporting enforcement off it is a claim about a ruleset that
    may not be the repo's."""
    rule_file(tmp_path, "no-swallow", implements=["swallowed-exceptions"])
    (tmp_path / "repo.yaml").write_text("review: [unclosed")
    report = advisor.recommend(
        tmp_path, catalog_path=write_catalog(tmp_path, ENTRY), records=[])
    assert report.gap_known is False
    assert report.enforced == ()
    assert any("repo.yaml" in n for n in report.notes)


# --------------------------------------------------------------------------
# language: an entry whose starter reads one language's idioms is not
# recommended to a repo that declares only others (agentops-elsx.22)
# --------------------------------------------------------------------------

# The shipped entries whose starters read Python idioms (`shell=True`,
# `os.system`, `pickle.loads`, `except: pass`, `Path(root).resolve()`). Seen
# recommended on a node:http service right after `warden init`
# (NightWatchEng/nightgate-demo, 2026-10-04).
PYTHON_IDIOM_ENTRIES = {
    "sql-injection", "os-command-injection", "unsafe-deserialization",
    "path-traversal", "security-misconfiguration", "weak-cryptography",
    "swallowed-exceptions",
}


def enrolled(root: Path) -> None:
    """The ruleset `warden init` writes: one rule, implementing one entry."""
    rule_file(root, "secrets-in-diff", implements=["hardcoded-credentials"])


def declare_components(root: Path, **langs: str) -> None:
    enrolled(root)
    comps = {name: {"path": ".", "lang": lang} for name, lang in langs.items()}
    (root / "repo.yaml").write_text(yaml.safe_dump({"components": comps}))


def test_rules_recommend_on_a_node_repo_proposes_no_python_engine_rules(tmp_path):
    """What `warden init` writes for a package.json repo: one component,
    `lang: node`. No row may carry a Python-idiom starter — not the
    python-engine path-traversal sketch, not the shell-true / os-system
    checks — and each one set aside is named, with the language its starter
    reads, rather than dropped."""
    declare_components(tmp_path, node="node")
    report = advisor.recommend(tmp_path)
    assert report.gap_known
    recommended = {r.entry_id for r in report.recommendations}
    assert not recommended & PYTHON_IDIOM_ENTRIES
    by_id = {e.id: e for e in cat.load_catalog()}
    check_ids = {c["id"] for eid in recommended
                 for c in ((by_id[eid].starter or {}).get("checks") or [])}
    assert not check_ids & {"shell-true", "os-system"}
    assert dict(report.set_aside) == {eid: ("python",)
                                      for eid in PYTHON_IDIOM_ENTRIES}
    text = advisor.render(report)
    assert "shell-true" not in text and "os-system" not in text
    assert "OTHER LANGUAGES (7)" in text
    assert "no node starter exists for these yet" in text
    # The language-neutral rows stay: the class matters whatever the
    # language, and their starters (or prose) do not read one.
    assert {"xss-unescaped-output", "supply-chain-pinning", "ssrf",
            "review-lens-tests"} <= recommended
    assert report.to_dict()["set_aside_by_language"] == {
        eid: ["python"] for eid in sorted(PYTHON_IDIOM_ENTRIES)}


def test_a_node_repo_is_told_what_engine_python_means(tmp_path):
    """engine:python names what the CHECKER is written in. On a repo that
    declares no Python, a bare `engine:python` row reads as 'a Python rule',
    which is how supply-chain-pinning (it reads package.json) was misread."""
    declare_components(tmp_path, node="node")
    text = advisor.render(advisor.recommend(tmp_path))
    assert "supply-chain-pinning [A03:2025, engine:python]" in text
    assert "engine:python names the language the CHECKER is written in" in text
    declare_components(tmp_path, app="python")
    text = advisor.render(advisor.recommend(tmp_path))
    assert "the CHECKER is written in" not in text


def test_python_repo_recommendations_are_unchanged_by_the_language_filter(tmp_path):
    """A Python repo, declared or not, gets exactly the rows it got before."""
    enrolled(tmp_path)
    undeclared = advisor.recommend(tmp_path)
    declare_components(tmp_path, app="python", docs="md")
    declared = advisor.recommend(tmp_path)
    assert undeclared.gap_known and declared.gap_known
    assert ([r.to_dict() for r in declared.recommendations]
            == [r.to_dict() for r in undeclared.recommendations])
    assert declared.set_aside == () and undeclared.set_aside == ()
    assert PYTHON_IDIOM_ENTRIES <= {r.entry_id for r in declared.recommendations}
    assert "OTHER LANGUAGES" not in advisor.render(declared)


def test_no_declared_language_sets_nothing_aside_and_says_so(tmp_path):
    """Components with no language the catalog tags: the filter cannot know,
    so it filters nothing — a row too many is the safe direction for a gap
    report — and the LIMITS line says why."""
    declare_components(tmp_path, docs="md")
    report = advisor.recommend(tmp_path)
    assert report.set_aside == ()
    assert PYTHON_IDIOM_ENTRIES <= {r.entry_id for r in report.recommendations}
    assert any("no entry is set aside by language" in lim
               for lim in report.limits)


@pytest.mark.parametrize("spelling", ["typescript", "JavaScript", "js", "nodejs"])
def test_hand_written_node_spellings_read_as_node(tmp_path, spelling):
    """`lang:` is free text in the schema; a hand-written repo.yaml that says
    typescript means node, and must not fall back to recommending Python."""
    declare_components(tmp_path, web=spelling)
    assert dict(advisor.recommend(tmp_path).set_aside).keys() == PYTHON_IDIOM_ENTRIES


def test_an_answered_entry_in_another_language_stays_answered(tmp_path):
    """An answer is checked before the language filter: setting an answered
    entry aside would drop its record from the report."""
    declare_components(tmp_path, node="node")
    answer = {"id": "sql-injection", "verdict": "not-applicable",
              "reason": "No database and no SQL surface anywhere in the "
                        "tree; flips the day it stores anything."}
    (tmp_path / ".warden" / "catalog-answers.yaml").write_text(
        yaml.safe_dump({"version": 1, "answers": [answer]}))
    report = advisor.recommend(tmp_path)
    assert [a.entry_id for a in report.answered] == ["sql-injection"]
    assert "sql-injection" not in dict(report.set_aside)


def test_catalog_langs_must_name_a_language_init_detects(tmp_path):
    bad = dict(ENTRY, langs=["pyhton"])
    with pytest.raises(cat.CatalogError, match="pyhton"):
        cat.load_catalog(write_catalog(tmp_path, bad))


def test_catalog_langs_belong_only_to_an_entry_with_a_starter(tmp_path):
    """`langs` says which language a STARTER reads; a judgment rule's prose
    reads none, so declaring one would hide it for no reason."""
    claude = {k: v for k, v in ENTRY.items() if k != "starter"}
    claude.update(engine="claude", langs=["python"])
    with pytest.raises(cat.CatalogError, match="langs"):
        cat.load_catalog(write_catalog(tmp_path, claude))


def test_catalog_languages_are_the_ones_init_detects():
    from warden import enroll
    assert cat.LANGS == enroll.ALL_LANGUAGES


def test_the_shipped_catalog_tags_exactly_the_python_idiom_entries():
    tagged = {e.id: e.langs for e in cat.load_catalog() if e.langs}
    assert tagged == {eid: ("python",) for eid in PYTHON_IDIOM_ENTRIES}


# Review round 1 (agentops-elsx.22): what the set-aside path must never hide.


def test_a_contradicted_answer_on_another_language_still_counts_unanswered(tmp_path):
    """A `not-applicable` answer the code contradicts falls through into the
    gap. Setting it aside by language would let a false waiver buy its way
    past UNANSWERED and the ceiling on any node repo."""
    declare_components(tmp_path, node="node")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "deploy.py").write_text(
        "import subprocess\nsubprocess.run(cmd, shell=True)\n")
    answer = {"id": "os-command-injection", "verdict": "not-applicable",
              "reason": "This repo never shells out; every process it starts "
                        "is an argv list."}
    (tmp_path / ".warden" / "catalog-answers.yaml").write_text(
        yaml.safe_dump({"version": 1, "answers": [answer]}))
    report = advisor.recommend(tmp_path)
    assert [a.state for a in report.answered] == ["contradicted"]
    assert "os-command-injection" in {r.entry_id for r in report.recommendations}
    assert "os-command-injection" not in dict(report.set_aside)


@pytest.mark.parametrize("path, text, entry_id", [
    ("src/config.js", "const cfg = yaml.load(fs.readFileSync(p));\n",
     "unsafe-deserialization"),
    ("src/hash.js", "const digest = md5(password);\n", "weak-cryptography"),
    ("scripts/deploy.py", "subprocess.run(cmd, shell=True)\n",
     "os-command-injection"),
])
def test_a_python_idiom_starter_that_matches_a_node_tree_is_still_recommended(
        tmp_path, path, text, entry_id):
    """The patterns are not confined to Python: js-yaml's `yaml.load(` and
    the md5 package's `md5(` match in JavaScript, and a node repo can carry
    `.py` scripts init never declared. A match keeps the row, with a caveat
    saying why it is there."""
    declare_components(tmp_path, node="node")
    (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / path).write_text(text)
    report = advisor.recommend(tmp_path)
    rows = {r.entry_id: r for r in report.recommendations}
    assert entry_id in rows and entry_id not in dict(report.set_aside)
    assert rows[entry_id].measured_hits
    assert any("does not declare (node)" in c for c in rows[entry_id].caveats)


@pytest.mark.parametrize("spelling", ["python", "python3.12", "Python 3",
                                      "py3", "cpython", "pypy", "PyPy3.10"])
def test_versioned_python_spellings_read_as_python(tmp_path, spelling):
    """A mixed repo keeps every entry one of its languages reads: a Python
    component, however spelled, beside a node one is not node-only."""
    declare_components(tmp_path, api=spelling, web="node")
    report = advisor.recommend(tmp_path)
    assert report.repo_langs == ("node", "python")
    assert report.set_aside == ()


@pytest.mark.parametrize("spelling", ["gopher", "pythonic-docs", "markdown"])
def test_a_name_that_only_starts_like_a_language_is_not_one(spelling):
    assert advisor._catalog_lang(spelling) is None


def test_a_paused_rules_entry_on_another_language_reopens_into_the_gap(tmp_path):
    """A paused rule's entry reopens into the gap with the pause named. Set
    aside instead, its LIMITS line would describe a path it never took."""
    declare_components(tmp_path, node="node")
    rule_file(tmp_path, "no-shell", implements=["os-command-injection"],
              paused=True, paused_reason="refuted three times running")
    report = advisor.recommend(tmp_path)
    rows = {r.entry_id: r for r in report.recommendations}
    assert "os-command-injection" not in dict(report.set_aside)
    assert any("PAUSED" in c for c in rows["os-command-injection"].caveats)
    assert not any("os-command-injection" in lim for lim in report.limits)


# agentops-elsx.24: a lang spelling the reader cannot classify fails closed.


@pytest.mark.parametrize("spelling", ["python-3.12", "python:3.12",
                                      "py-3", "CPython-3.11", "python_3",
                                      "python>=3.11"])
def test_a_python_spelling_with_any_separator_beside_node_sets_nothing_aside(
        tmp_path, spelling):
    """A version or qualifier after the name, whatever separates it, still
    names Python: the mixed repo is not read as node-only."""
    declare_components(tmp_path, api=spelling, web="node")
    report = advisor.recommend(tmp_path)
    assert report.repo_langs == ("node", "python")
    assert report.set_aside == ()


@pytest.mark.parametrize("spelling", ["TypeScript/Python",
                                      "typescript+python", "Python & Node"])
def test_a_spelling_naming_two_languages_reads_both(tmp_path, spelling):
    declare_components(tmp_path, app=spelling)
    report = advisor.recommend(tmp_path)
    assert report.repo_langs == ("node", "python")
    assert report.set_aside == ()


@pytest.mark.parametrize("spelling", ["cobol-ng", "pythonic-docs",
                                      "Rust/Django", "3.12", "",
                                      "Python/Django", "Go/Django", "go-flask",
                                      "TypeScript + FastAPI"])
def test_an_unclassifiable_lang_sets_nothing_aside_and_names_the_spelling(
        tmp_path, spelling):
    """A word that is neither a catalog language nor a known other one —
    alone, or beside a catalog word as in `Go/Django` — means the reader
    cannot say this component is not Python, so nothing is set aside as
    OTHER LANGUAGES, and the LIMITS line quotes the spelling it could not
    read."""
    declare_components(tmp_path, api=spelling, web="node")
    report = advisor.recommend(tmp_path)
    assert report.set_aside == () and report.repo_langs == ()
    assert PYTHON_IDIOM_ENTRIES <= {r.entry_id for r in report.recommendations}
    lines = [lim for lim in report.limits
             if "no entry is set aside by language" in lim]
    assert len(lines) == 1 and repr(spelling) in lines[0] and "'api'" in lines[0]
    assert "OTHER LANGUAGES" not in advisor.render(report)


@pytest.mark.parametrize("spec", [{"path": "."}, {"path": ".", "lang": 3.12},
                                  {"path": ".", "lang": ["python"]}])
def test_a_component_with_no_readable_lang_sets_nothing_aside(tmp_path, spec):
    """A component that declares no lang string says nothing about its
    language; dropping it would read the repo as its other components'."""
    enrolled(tmp_path)
    (tmp_path / "repo.yaml").write_text(yaml.safe_dump(
        {"components": {"api": spec, "web": {"path": ".", "lang": "node"}}}))
    report = advisor.recommend(tmp_path)
    assert report.set_aside == () and report.repo_langs == ()
    assert any("'api'" in lim and "no entry is set aside by language" in lim
               for lim in report.limits)


@pytest.mark.parametrize("spelling", ["md", "Markdown", "rust 1.80", "shell",
                                      "c++17", "java-21", "docs"])
def test_a_known_other_language_beside_node_still_sets_python_aside(
        tmp_path, spelling):
    """A declared, known non-catalog language is read, not unread: it names
    no catalog language, so the node repo's Python-idiom entries are still
    set aside and no LIMITS line blames the spelling."""
    declare_components(tmp_path, docs=spelling, web="node")
    report = advisor.recommend(tmp_path)
    assert report.repo_langs == ("node",)
    assert dict(report.set_aside).keys() == PYTHON_IDIOM_ENTRIES
    assert not any("no entry is set aside by language" in lim
                   for lim in report.limits)
