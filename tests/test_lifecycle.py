"""Rule lifecycle: retire the dead, demote the noisy, narrow the over-broad.

The advisor only ever ADDS. A recommender that never subtracts produces rule
debt, and rule debt is worse than a missing rule: a gate that fires constantly
on things nobody acts on trains every author to skim past it, which silently
disables the rules that DO matter.

Three chronic conditions, all measured from data the platform already records:
never fires, always argued down, always fires. The ACUTE case — a consecutive
refutation streak — already has a mechanism (`warden.autonomy.pause_candidates`);
this is the chronic one, and these tests pin that it reads the SAME precision
data rather than growing a second copy of the metric.

The hardest case has its own tests: a rule that has never fired may be the
reason the thing it guards has never happened. Silence is consistent with two
opposite worlds, and every proposal here is a question, never a verdict.
"""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from warden import cli
from warden import lifecycle
from warden import memory as memory_mod
from warden import rules as rules_mod

MODULE_SRC = Path(lifecycle.__file__)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def rule_file(root: Path, rule_id: str, *, engine: str = "claude",
              applies_to=("**",), checks=None, **meta) -> Path:
    d = root / ".warden" / "rules"
    d.mkdir(parents=True, exist_ok=True)
    front = {"id": rule_id, "severity": "MEDIUM", "engine": engine,
             "applies_to": list(applies_to), **meta}
    if checks is not None:
        front["checks"] = checks
    path = d / f"{rule_id}.md"
    path.write_text("---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n"
                    + "a rule body\n")
    return path


def record(rule_id: str, status: str, *, day: int = 1, seq: int = 0,
           tags=("fail-open",), file: str = "app/x.py") -> dict:
    return {"rule_id": rule_id, "status": status,
            "ts": f"2026-08-{day:02d}T12:00:00+00:00", "sha": f"{seq:040d}",
            "seq": seq, "tags": list(tags), "file": file,
            "dir_prefix": file.rsplit("/", 1)[0] if "/" in file else "",
            # One text per judgment: an identical finding in a later round
            # reads as that judgment RESTATED and folds to one
            "finding": f"a finding {day}.{seq}", "reason": "a reason"}


def commits(n: int, *, path: str = "app/x.py", added: str = "harmless\n",
            start: int = 0):
    """`n` synthetic commits in the backtest's (sha, [(path, added)]) shape."""
    return [(f"{i + start:040d}", [(path, added)]) for i in range(n)]


def run(root: Path, *, records=None, commits_=None, window=200, **kw):
    return lifecycle.rule_lifecycle(
        root, records=records if records is not None else [],
        rules_dir=root / ".warden" / "rules",
        window=window, commits=commits_, **kw)


def row_of(report, rule_id: str):
    return next(r for r in report.rows if r.rule_id == rule_id)


def never_matches(check_id: str = "never-matches"):
    """A replayable rule whose pattern nothing in these fixtures contains: the
    only shape for which "it fired zero times" is a MEASUREMENT rather than an
    inference from an empty corpus. Check ids must be unique across the whole
    ruleset (one `warden:allow` marker keys on the id alone), so each fixture
    rule gets its own."""
    return [{"id": check_id, "pattern": r"zzz-no-such-token-zzz",
             "message": "a hazard nothing in the fixture writes"}]


# --------------------------------------------------------------------------
# NEVER FIRES — and the absence-of-evidence problem underneath it
# --------------------------------------------------------------------------

def test_a_never_fired_rule_is_proposed_for_retirement_with_its_window(tmp_path):
    """The first condition. The window is not optional decoration: a
    'never fires' claim without the window it was measured over is
    unfalsifiable, and the reader cannot tell a long silence from a short
    look."""
    rule_file(tmp_path, "quiet-rule", engine="declarative",
              applies_to=["app/**"], checks=never_matches())
    report = run(tmp_path, commits_=commits(40))
    row = row_of(report, "quiet-rule")
    assert row.verdict == "retire?", row.verdict
    assert row.selected == 40
    assert "40" in report.commit_window, report.commit_window
    text = lifecycle.render(report)
    assert "quiet-rule" in text
    assert report.commit_window in text, (
        "a retirement proposal that does not state the window it was measured "
        "over is unfalsifiable")


def test_the_report_states_the_absence_of_evidence_case_plainly(tmp_path):
    """The intellectual core of the lifecycle. A rule that has never fired may
    be the REASON the thing it guards has never happened. The report must say
    so in the same breath as the proposal, not bury it as a footnote."""
    rule_file(tmp_path, "quiet-rule", applies_to=["app/**"])
    # Whitespace-normalised: the render wraps, and a doctrine assertion that
    # goes red because a phrase moved across a line break would be testing the
    # column width, not the doctrine.
    text = " ".join(lifecycle.render(run(tmp_path, commits_=commits(40)))
                    .lower().split())
    assert "absence of evidence" in text
    for phrase in ("may be the reason", "deterrence", "inert"):
        assert phrase in text, f"the silence doctrine never says {phrase!r}"
    assert "proposal" in text and "never automatic" in text


def test_never_selected_is_reported_apart_from_evaluated_and_silent(tmp_path):
    """Two different silences, two different first questions. A rule the window
    never even SELECTED was never evaluated — check the glob before the rule.
    A rule selected on many commits and silent on all of them is the strongest
    case for a stale pattern AND the strongest case for deterrence."""
    rule_file(tmp_path, "unreachable", engine="declarative",
              applies_to=["gone/**"], checks=never_matches("never-a"))
    rule_file(tmp_path, "evaluated", engine="declarative",
              applies_to=["app/**"], checks=never_matches())
    report = run(tmp_path, commits_=commits(40))
    assert row_of(report, "unreachable").silence == "never-selected"
    assert row_of(report, "evaluated").silence == "evaluated-silent"
    assert row_of(report, "unreachable").selected == 0
    text = lifecycle.render(report).lower()
    assert "glob" in text, (
        "a rule whose applies_to matched nothing may have a stale glob, not a "
        "dead hazard — the report must say which question comes first")


def test_a_barely_exercised_rule_is_untested_never_retired(tmp_path):
    """Silence over a window that barely exercised the rule is a statement
    about the sample, not about the rule. The bar is the same evidence bar
    memory already uses for a precision claim."""
    # Declarative, so the `evaluated-silent` branch is the one reached and
    # its `selected >= MIN_EXERCISED` guard is actually under test — with a
    # claude fixture the comparison is unreachable and the bar could be
    # deleted outright with the suite green.
    rule_file(tmp_path, "young", engine="declarative", applies_to=["app/**"],
              checks=never_matches("never-young"))
    report = run(tmp_path, commits_=commits(memory_mod.PROMOTE_MIN_N - 1))
    row = row_of(report, "young")
    assert row.verdict == "untested"
    assert row.silence == "window-too-thin"
    # One more commit and the same rule crosses the bar: the threshold is
    # pinned from both sides, not just asserted below it.
    at_bar = row_of(run(tmp_path, commits_=commits(memory_mod.PROMOTE_MIN_N)),
                    "young")
    assert at_bar.verdict == "retire?" and at_bar.silence == "evaluated-silent"


def test_never_selected_needs_a_window_long_enough_to_mean_it(tmp_path):
    """`never-selected` carries the exercised-enough bar the other silences
    carry: without it, in a repo whose history is shorter than the bar EVERY
    rule matches nothing and every rule is proposed for retirement — a
    statement about the window, not the ruleset."""
    rule_file(tmp_path, "unreachable", applies_to=["gone/**"])
    thin = row_of(run(tmp_path, commits_=commits(memory_mod.PROMOTE_MIN_N - 1)),
                  "unreachable")
    assert thin.verdict == "untested" and thin.silence == "window-too-thin"
    long = row_of(run(tmp_path, commits_=commits(memory_mod.PROMOTE_MIN_N)),
                  "unreachable")
    assert long.verdict == "retire?" and long.silence == "never-selected"


def test_a_blind_corpus_and_a_blind_history_retire_nothing(tmp_path):
    """Both windows blind: no commits readable and an empty corpus. Every rule
    reads as silent, and none of that silence is the rule's."""
    rule_file(tmp_path, "any-rule")
    report = lifecycle.rule_lifecycle(
        tmp_path, records=[], rules_dir=tmp_path / ".warden" / "rules",
        commits=None, window=None)
    assert row_of(report, "any-rule").verdict == "untested"
    assert not [r for r in report.rows if r.verdict == "retire?"]


def test_an_unreadable_history_is_stated_never_counted_as_silence(tmp_path,
                                                                  monkeypatch):
    """'Could not look' is not 'clean' — this repo's oldest doctrine, applied
    to the window that would otherwise manufacture a retirement proposal."""
    rule_file(tmp_path, "quiet-rule", applies_to=["app/**"])

    def boom(*a, **kw):
        raise lifecycle.backtest_mod.BacktestError("git is not on PATH")

    monkeypatch.setattr(lifecycle.backtest_mod, "read_commit_additions", boom)
    report = lifecycle.rule_lifecycle(
        tmp_path, records=[], rules_dir=tmp_path / ".warden" / "rules",
        window=200)
    assert row_of(report, "quiet-rule").verdict == "untested"
    assert any("git is not on PATH" in limit for limit in report.limits)
    assert "UNREAD" in lifecycle.render(report)


def test_a_gate_detection_counts_as_the_rule_having_fired(tmp_path):
    """A `detected` record is a deterministic checker firing — a fact, not a
    judgment. memory keeps it out of PRECISION for good reason; keeping it out
    of the FIRING evidence too would call a rule dead that fires every week."""
    # Declarative and never-matching, over a long window: without the
    # detection term this row is `retire?`, so deleting `or detected > 0` from
    # the firing test goes red. A claude fixture here would be vacuous — it
    # falls to `untested` either way.
    rule_file(tmp_path, "fires-mechanically", engine="declarative",
              applies_to=["app/**"], checks=never_matches("never-detect"))
    records = [record("fires-mechanically", "detected", day=d + 1)
               for d in range(4)]
    report = run(tmp_path, records=records, commits_=commits(40))
    row = row_of(report, "fires-mechanically")
    assert row.detections == 4
    assert row.verdict == "active", (
        "a rule whose checker fires every week was called dead because gate "
        "detections are not judged findings")


def test_a_judged_finding_counts_as_the_rule_having_fired(tmp_path):
    rule_file(tmp_path, "judged-rule", applies_to=["app/**"])
    report = run(tmp_path, records=[record("judged-rule", "confirmed")],
                 commits_=commits(40))
    assert row_of(report, "judged-rule").verdict != "retire?"


# --------------------------------------------------------------------------
# the discriminator: does the hazard still exist in the tree?
# --------------------------------------------------------------------------

EXCEPT_PASS = [{"id": "except-pass", "pattern": r"except[^:\n]*:\s*\n\s*pass\b",
                "message": "exception swallowed", "scope": "file",
                "flags": ["m"]}]


def test_a_silent_rule_whose_pattern_still_matches_the_tree_reads_as_inert(tmp_path):
    """The one discriminator the data can actually offer. The deterrence
    hypothesis predicts the hazard is ABSENT from the code, not merely
    unflagged; finding it present and unflagged argues the rule is inert —
    which is a reason to FIX it before proposing retirement."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text("try:\n    f()\nexcept Exception:\n    pass\n")
    rule_file(tmp_path, "no-swallow", engine="declarative",
              applies_to=["app/**"], checks=EXCEPT_PASS)
    report = run(tmp_path, commits_=commits(40, added="harmless\n"))
    row = row_of(report, "no-swallow")
    assert row.verdict == "retire?"
    assert row.tree_matches == 1, row.tree_matches
    text = lifecycle.render(report)
    assert "fix" in text.lower(), (
        "a silent rule whose hazard is sitting in the tree unflagged is a "
        "broken rule before it is a dead one")


def test_a_silent_rule_with_a_clean_tree_is_reported_undecidable(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text("def f():\n    return 1\n")
    rule_file(tmp_path, "no-swallow", engine="declarative",
              applies_to=["app/**"], checks=EXCEPT_PASS)
    row = row_of(run(tmp_path, commits_=commits(40)), "no-swallow")
    assert row.tree_matches == 0
    assert row.hazard == "absent-from-the-tree"


def test_a_require_true_rule_is_not_called_silent_on_a_replay_that_skipped_it(tmp_path):
    """A `require: true` check fires when its pattern is ABSENT — the replay
    and the tree scan both EXCLUDE it and still return a count, so a rule
    built only of those would replay as `flagged 0` and read as never-fired:
    a retirement proposal built on a replay that never ran the rule."""
    rule_file(tmp_path, "must-declare", engine="declarative",
              applies_to=["app/**"],
              checks=[{"id": "needs-header", "pattern": r"^# SPDX",
                       "message": "missing header", "require": True,
                       "scope": "file"}])
    row = row_of(run(tmp_path, commits_=commits(40)), "must-declare")
    assert row.flagged is None, "a replay that excluded every check reported 0"
    assert "require:true" in row.replay_reason
    assert row.tree_matches is None and row.hazard == "unscannable"
    assert row.verdict == "untested", (
        "with no corpus and no runnable replay, nothing about this rule was "
        "measured — and unmeasured is not dead")


def test_a_partially_replayable_rule_keeps_its_replay_and_its_caveat(tmp_path):
    """The complement: one require:true check alongside a runnable one is still
    a measurement, and the excluded half travels as a stated limit rather than
    silently shrinking the count."""
    rule_file(tmp_path, "mixed", engine="declarative", applies_to=["app/**"],
              checks=[{"id": "absent-header", "pattern": r"^# SPDX",
                       "message": "missing header", "require": True,
                       "scope": "file"},
                      {"id": "present-token", "pattern": r"harmless",
                       "message": "found"}])
    report = run(tmp_path, commits_=commits(40))
    row = row_of(report, "mixed")
    assert row.flagged == 40
    assert any("absent-header" in limit for limit in report.limits), (
        "the excluded check vanished from the report instead of being stated")


def test_a_hazard_looked_for_in_zero_files_is_not_reported_absent(tmp_path):
    """The tree scan admits a fixed suffix set; a rule scoped outside it
    measures 0 over ZERO files, and the row must not print "absent from the
    tree" while the hazard sits in the tree. Reporting 0 where nothing was
    examined is the one failure this module exists to
    refuse."""
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.tf").write_text('acl = "public-read"\n')
    rule_file(tmp_path, "no-public-bucket", engine="declarative",
              applies_to=["infra/**"],
              checks=[{"id": "public-read", "pattern": r"public-read",
                       "message": "world-readable bucket", "scope": "file"}])
    report = run(tmp_path, commits_=commits(40, path="infra/main.tf"))
    row = row_of(report, "no-public-bucket")
    assert row.tree_matches is None, "a zero was reported over zero files"
    assert row.hazard == "nothing-scanned"
    assert any("ZERO files" in limit for limit in report.limits)
    assert "not evidence of absence" in " ".join(
        lifecycle.render(report).split())


def test_pre_existing_matches_do_not_convict_a_scope_added_rule(tmp_path):
    """A `scope: added` check fires only on lines a diff ADDS, so existing
    matches are exactly what a correctly working rule leaves alone — the
    report must not tell the reader to FIX a rule behaving as declared. Only
    a whole-file check could have fired on them."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "legacy.py").write_text("legacy_token = 1\nlegacy_token = 2\n")
    rule_file(tmp_path, "no-legacy-token", engine="declarative",
              applies_to=["app/**"],
              checks=[{"id": "legacy-token", "pattern": r"legacy_token",
                       "message": "legacy token"}])
    report = run(tmp_path, commits_=commits(40, added="harmless\n"))
    row = row_of(report, "no-legacy-token")
    assert row.tree_matches == 2
    assert row.hazard == "present-in-lines-it-never-polices"
    text = " ".join(lifecycle.render(report).split())
    assert "argue NEITHER way" in text
    assert "argues the rule is broken" not in text


def test_the_tree_scans_own_caveats_reach_the_report(tmp_path):
    """measure()'s caveats say what the count does NOT cover; dropping them
    would turn a partial scan into an absolute absence claim."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text("nothing to see\n")
    rule_file(tmp_path, "mixed-scan", engine="declarative",
              applies_to=["app/**"],
              checks=[{"id": "absent-marker", "pattern": r"^# LICENSE",
                       "message": "missing marker", "require": True,
                       "scope": "file"},
                      {"id": "present-marker", "pattern": r"zzz-absent-zzz",
                       "message": "hazard"}])
    report = run(tmp_path, commits_=commits(40))
    row = row_of(report, "mixed-scan")
    assert row.hazard == "absent-from-the-tree"
    assert any("absent-marker" in limit and "mixed-scan" in limit
               for limit in report.limits), (
        "the excluded half of the scan vanished behind an absence claim")


def test_an_adjudicated_allow_marker_is_not_counted_as_an_unflagged_hazard(tmp_path):
    """The tree scan honours the per-line `warden:allow(<check-id>)` markers
    the gate obeys, which `advisor.measure` alone does not. Without them a
    line the gate provably never fires on counts as a live hazard, and the
    row tells the reader the rule is BROKEN — on adjudicated carve-outs like
    this repo's own."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text(
        "try:\n    f()\n# warden:allow(swallow): the walk failing means this is "
        "not a usable repo, and the noisier direction is to continue\n"
        "except Exception:\n    pass\n")
    rule_file(tmp_path, "no-swallow", engine="declarative",
              applies_to=["app/**"],
              checks=[{"id": "swallow",
                       "pattern": r"except[^:\n]*:\s*\n\s*pass\b",
                       "message": "exception swallowed", "scope": "file",
                       "flags": ["m"]}])
    report = run(tmp_path, commits_=commits(40))
    row = row_of(report, "no-swallow")
    assert row.tree_matches == 0, (
        "a reasoned, adjudicated waiver was counted as an unflagged hazard")
    assert row.hazard == "absent-from-the-tree"
    assert "argues the rule is broken" not in lifecycle.render(report)


def test_a_mixed_scope_rules_added_only_hits_are_not_credited_to_a_file_check(tmp_path):
    """Hits are attributed per check. One total across every check, with the
    scope test asking `any(check is scope:file)`, would attribute ALL the hits
    of a rule declaring one whole-file check and one scope:added check to the
    whole-file check — even when that check matched nothing. It is the
    scope:added case above, reached through a second door."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text("legacy_token = 1\nlegacy_token = 2\n")
    rule_file(tmp_path, "mixed-scope", engine="declarative",
              applies_to=["app/**"],
              checks=[{"id": "never-file", "pattern": r"zzz-never-zzz",
                       "message": "never", "scope": "file"},
                      {"id": "legacy-added", "pattern": r"legacy_token",
                       "message": "legacy"}])
    report = run(tmp_path, commits_=commits(40))
    row = row_of(report, "mixed-scope")
    assert row.tree_matches == 2
    assert row.hazard == "present-in-lines-it-never-polices", (
        "hits from a scope:added check were credited to a whole-file check "
        "that matched nothing")
    assert "argues the rule is broken" not in lifecycle.render(report)


def test_a_judgment_rule_gets_no_fabricated_tree_measurement(tmp_path):
    """A claude rule is a judgment, not a regex — there is nothing to scan the
    tree with, and 0 would read as a measured clean tree."""
    rule_file(tmp_path, "judgment", applies_to=["app/**"])
    row = row_of(run(tmp_path, commits_=commits(40)), "judgment")
    assert row.tree_matches is None
    assert row.hazard == "unscannable"


# --------------------------------------------------------------------------
# ALWAYS DISMISSED — one precision metric, not two
# --------------------------------------------------------------------------

def test_a_near_always_dismissed_rule_is_proposed_for_demotion(tmp_path):
    rule_file(tmp_path, "noisy", applies_to=["app/**"])
    records = [record("noisy", "dismissed-with-reason", day=(i % 28) + 1, seq=i)
               for i in range(12)]
    report = run(tmp_path, records=records, commits_=commits(40))
    row = row_of(report, "noisy")
    assert row.verdict == "demote?"
    assert row.judged == 12 and row.dismissed == 12 and row.upheld == 0
    text = lifecycle.render(report)
    assert "12" in text, "the demotion proposal must carry its counts"
    assert "narrow" in text.lower(), "narrowing is offered before deletion"


def test_the_chronic_read_catches_what_the_acute_streak_cannot(tmp_path):
    """The pause mechanism fires on 3 consecutive REFUTATIONS. A rule whose
    findings are dismissed-with-reason instead never streaks at all — any
    non-refutation resets the count, and a dismissal is one — so a rule argued
    down eight times in nine, forever, is invisible to the acute lever by
    construction. That is the gap this report exists to close."""
    rule_file(tmp_path, "chronic", applies_to=["app/**"])
    records = []
    for i in range(27):
        status = "confirmed" if i % 9 == 0 else "dismissed-with-reason"
        records.append(record("chronic", status, day=(i % 28) + 1, seq=i))
    assert memory_mod.refutation_streaks(records).get("chronic", 0) == 0, (
        "fixture invalid: the acute streak mechanism would already fire")
    from warden import autonomy as autonomy_mod
    assert autonomy_mod.pause_candidates(
        tmp_path, records=records) == [], "the acute mechanism sees nothing here"
    row = row_of(run(tmp_path, records=records, commits_=commits(40)), "chronic")
    assert row.verdict == "demote?"


def test_the_dismissal_bound_is_memorys_own_wilson_function(tmp_path):
    """Constraint: reuse the existing precision data, do not
    invent a second measure — a second copy of a metric is a second thing to
    drift. The chronic read is the SAME estimator on the SAME records, pointed
    at the complementary count."""
    rule_file(tmp_path, "noisy", applies_to=["app/**"])
    records = [record("noisy", "dismissed-with-reason", day=(i % 28) + 1, seq=i)
               for i in range(11)]
    records.append(record("noisy", "confirmed", day=28, seq=99))
    row = row_of(run(tmp_path, records=records, commits_=commits(40)), "noisy")
    assert row.judged == 12 and row.argued_down == 11
    assert row.argued_down_lb == round(
        memory_mod.wilson_lower_bound(11, 12), 3)
    assert row.wilson_lb == round(memory_mod.wilson_lower_bound(1, 12), 3), (
        "the row must also carry memory's own upheld-side bound unchanged")


def test_every_confidence_bound_in_the_module_is_memorys():
    """Structural guard for the no-second-metric constraint, checked as a
    PROPERTY rather than as three spellings.

    Grepping for `math.sqrt` / `import math` / `def wilson` would be a guard
    narrower than its claim to forbid "a private sqrt or a hand-rolled
    ratio": a re-typed Wilson expression under any other name walks past it.
    This parses the module and
    asserts that (a) it imports no statistics library, and (b) every
    `wilson_lower_bound` call is an attribute of the memory module.
    """
    import ast

    tree = ast.parse(MODULE_SRC.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & {"math", "statistics", "numpy", "scipy"}), (
        f"lifecycle.py imports a statistics library ({sorted(imported)}) — the "
        "precision arithmetic belongs to warden.memory, once")

    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    wilsons = [c for c in calls
               if (isinstance(c.func, ast.Attribute)
                   and c.func.attr == "wilson_lower_bound")
               or (isinstance(c.func, ast.Name)
                   and c.func.id == "wilson_lower_bound")]
    assert wilsons, "the module computes no Wilson bound at all any more"
    for call in wilsons:
        assert (isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "memory_mod"), (
            "a Wilson bound is computed from something other than "
            "warden.memory — a second copy of a metric is a second thing to "
            "drift")
    # And no local definition can shadow it.
    assert not [n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and "wilson" in n.name], (
        "lifecycle.py defines its own Wilson function")


def test_a_thin_dismissal_history_is_not_enough_to_demote(tmp_path):
    """The small-sample error, pointed the other way. Three dismissals is one
    reviewer's bad afternoon; branding a rule harmful on it is the same
    mistake the promotion floor exists to prevent."""
    rule_file(tmp_path, "young-noisy", applies_to=["app/**"])
    records = [record("young-noisy", "dismissed-with-reason",
                      day=(i % 28) + 1, seq=i)
               for i in range(memory_mod.PROMOTE_MIN_N - 1)]
    row = row_of(run(tmp_path, records=records, commits_=commits(40)),
                 "young-noisy")
    assert row.verdict != "demote?"


def test_a_rule_the_corpus_upholds_is_left_alone(tmp_path):
    rule_file(tmp_path, "good", applies_to=["app/**"])
    records = [record("good", "confirmed", day=(i % 28) + 1, seq=i)
               for i in range(12)]
    assert row_of(run(tmp_path, records=records, commits_=commits(40)),
                  "good").verdict == "active"


# --------------------------------------------------------------------------
# ALWAYS FIRES — narrow, never delete
# --------------------------------------------------------------------------

FIRES_ALWAYS = [{"id": "any-line", "pattern": r"\w", "message": "always"}]


def test_a_rule_firing_on_nearly_every_diff_is_proposed_for_narrowing(tmp_path):
    rule_file(tmp_path, "over-broad", engine="declarative",
              applies_to=["app/**"], checks=FIRES_ALWAYS)
    report = run(tmp_path, commits_=commits(40))
    row = row_of(report, "over-broad")
    assert row.verdict == "narrow?"
    assert row.flagged == 40 and row.fire_rate == 1.0
    text = lifecycle.render(report)
    assert "applies_to" in text, "the proposal must name the field to narrow"
    section = " ".join(text.split("NARROW?")[1].split("\n\n")[0].lower().split())
    assert "narrow" in section and "not a deletion" in section, (
        "an over-broad rule is a convention the repo has not adopted, or a "
        "scope error — the section must propose narrowing and rule deletion "
        "out, not leave the reader to choose")


def test_the_firing_rate_is_measured_within_the_rules_own_applies_to(tmp_path):
    """The denominator is the commits the rule was SELECTED on. Measuring
    against every commit in the window would call a correctly-scoped rule
    'rarely fires' purely because the repo has other directories."""
    rule_file(tmp_path, "scoped", engine="declarative",
              applies_to=["db/**"], checks=FIRES_ALWAYS)
    window = commits(30, path="app/x.py") + commits(10, path="db/y.sql", start=30)
    row = row_of(run(tmp_path, commits_=window), "scoped")
    assert row.selected == 10 and row.flagged == 10
    assert row.fire_rate == 1.0 and row.verdict == "narrow?"


def test_a_rule_that_fires_occasionally_is_left_alone(tmp_path):
    """The narrow? threshold pinned from BELOW as well as above. Both other
    narrow tests sit at a firing rate of 1.0, so ALWAYS_FIRES_RATE could drift
    down to "fires at all" with the suite green."""
    rule_file(tmp_path, "occasional", engine="declarative",
              applies_to=["app/**"],
              checks=[{"id": "rare-token", "pattern": r"rare-token",
                       "message": "found"}])
    window = (commits(4, path="app/x.py", added="rare-token\n")
              + commits(36, path="app/y.py", added="harmless\n", start=4))
    row = row_of(run(tmp_path, commits_=window), "occasional")
    assert row.selected == 40 and row.flagged == 4
    assert row.fire_rate == 0.1 and row.fire_rate < lifecycle.ALWAYS_FIRES_RATE
    assert row.verdict == "active", (
        "a rule firing on a tenth of the diffs it sees was called over-broad")


def test_excluded_paths_are_not_counted_as_selections(tmp_path):
    """`excludes` is part of how the gate selects a rule; a lifecycle read that
    ignores it measures a rule the gate never runs."""
    rule_file(tmp_path, "carved", engine="declarative", applies_to=["app/**"],
              excludes=["app/generated/**"], checks=FIRES_ALWAYS)
    window = commits(20, path="app/generated/z.py")
    row = row_of(run(tmp_path, commits_=window), "carved")
    assert row.selected == 0


def test_a_judgment_rule_is_never_called_over_broad_on_an_unmeasured_rate(tmp_path):
    """Only a mechanical rule can be replayed. Calling a claude rule over-broad
    on a firing rate nobody measured is exactly the fabricated number the
    backtest module refuses to produce."""
    rule_file(tmp_path, "judgment", applies_to=["**"])
    records = [record("judgment", "confirmed", day=(i % 28) + 1, seq=i)
               for i in range(12)]
    report = run(tmp_path, records=records, commits_=commits(40))
    row = row_of(report, "judgment")
    assert row.flagged is None and row.fire_rate is None
    assert row.verdict != "narrow?"
    assert "not replayable" in row.replay_reason or "judgment" in row.replay_reason
    assert "UNMEASURED" in lifecycle.render(report)


# --------------------------------------------------------------------------
# boundaries: this module proposes, it never acts
# --------------------------------------------------------------------------

def test_an_already_paused_rule_is_reported_not_re_proposed(tmp_path):
    """The acute mechanism owns a paused rule. Proposing retirement of a rule
    that is already switched off is churn, and it would double-count the same
    evidence in two ladders."""
    rule_file(tmp_path, "asleep", applies_to=["app/**"], paused=True,
              paused_reason="auto-paused on a refutation streak")
    report = run(tmp_path, commits_=commits(40))
    row = row_of(report, "asleep")
    assert row.verdict == "paused"
    assert "asleep" in lifecycle.render(report)


def test_a_corpus_only_silence_needs_a_corpus_big_enough_to_mean_it(tmp_path):
    """The corpus-only retirement path carries an evidence bar of its own. If
    `corpus_live` were true whenever ANY id anywhere carried one judged
    record, a single finding about a different rule would make every
    unreplayable rule retire?-eligible on nothing measured about it. The
    replay can say "selected 40 times, fired 0"; the corpus can only say "no
    record in N attested reviews", which is worth nothing until N is real."""
    rule_file(tmp_path, "unmeasurable", applies_to=["app/**"])
    rule_file(tmp_path, "noisy-neighbour", applies_to=["app/**"])
    records = [record("noisy-neighbour", "confirmed", day=3, seq=1)]
    report = run(tmp_path, records=records, commits_=commits(40))
    row = row_of(report, "unmeasurable")
    assert row.verdict == "untested", (
        "one judged finding about ANOTHER rule made this one retire?-eligible")
    assert any("attested review event" in limit for limit in report.limits)


def test_a_corpus_only_silence_is_named_the_weakest_row(tmp_path):
    """The complement: with enough attested review events behind it the
    proposal IS made, and it says plainly that nothing about the RULE was
    measured — only that no record names it."""
    import json

    rule_file(tmp_path, "unmeasurable", applies_to=["app/**"])
    rule_file(tmp_path, "neighbour", applies_to=["app/**"])
    shards = tmp_path / ".warden" / "memory" / "attest"
    shards.mkdir(parents=True)
    for i in range(memory_mod.PROMOTE_MIN_N):
        (shards / f"event-{i}.json").write_text(json.dumps(
            {"sha": f"{i:040d}", "source": "attest", "verdict": "clean",
             "records": []}))
    records = [record("neighbour", "confirmed", day=3, seq=1)]
    report = run(tmp_path, records=records, commits_=commits(40))
    row = row_of(report, "unmeasurable")
    assert row.silence == "unobservable-firing" and row.verdict == "retire?"
    text = " ".join(lifecycle.render(report).split())
    assert "absence of RECORDS" in text, (
        "the weakest row in the report must say what it did NOT measure")


def test_the_json_artifact_carries_nulls_where_nothing_was_measured(tmp_path):
    """The machine-readable artifact is the whole surface a skill or CI
    consumer reads. It emits `proposals: null` on an uncomputed report, and
    reverting that to an empty map (the fail-open direction) must turn this
    red."""
    rule_file(tmp_path, "judgment", applies_to=["app/**"])
    doc = run(tmp_path, commits_=commits(40)).to_dict()
    assert doc["schema"] == 1 and doc["computed"] is True
    assert set(doc["proposals"]) == set(lifecycle.PROPOSAL_VERDICTS)
    row = doc["rows"][0]
    assert row["flagged"] is None and row["fire_rate"] is None
    assert row["tree_matches"] is None
    assert row["rate"] is None, (
        "a precision of 0.0 on zero judged findings is a number nobody "
        "measured (review round 2, F9)")
    import json
    json.dumps(doc)          # the artifact must actually serialise

    d = tmp_path / ".warden" / "rules"
    (d / "broken.md").write_text("no frontmatter here\n")
    blind = run(tmp_path, commits_=commits(40)).to_dict()
    assert blind["computed"] is False
    assert blind["proposals"] is None, (
        "a consumer reading `proposals` alone got a clean bill from a report "
        "that judged nothing")


def test_the_module_proposes_and_never_edits_a_rule(tmp_path):
    """Retirement is a PROPOSAL, never automatic — enforced structurally, not
    by intention. There is no writer here for a reason."""
    src = MODULE_SRC.read_text()
    for forbidden in ("write_text(", ".unlink(", "shutil.", "os.remove"):
        assert forbidden not in src, (
            f"lifecycle.py can mutate the tree ({forbidden!r}) — retirement is "
            "a proposal a human acts on, never an edit this module makes")
    assert not [n for n in dir(lifecycle)
                if n.startswith(("apply_", "retire_", "delete_"))]

    rule_file(tmp_path, "quiet-rule", applies_to=["app/**"])
    before = (tmp_path / ".warden" / "rules" / "quiet-rule.md").read_text()
    run(tmp_path, commits_=commits(40))
    assert (tmp_path / ".warden" / "rules" / "quiet-rule.md").read_text() == before


def test_an_unreadable_ruleset_makes_no_lifecycle_claim(tmp_path):
    """Fail closed: with no ruleset there is nothing to judge, and an empty
    report would read as 'every rule is healthy'."""
    d = tmp_path / ".warden" / "rules"
    d.mkdir(parents=True)
    (d / "broken.md").write_text("no frontmatter here\n")
    report = run(tmp_path, commits_=commits(40))
    assert report.rows == ()
    assert report.computed is False
    text = lifecycle.render(report)
    assert "could not be read" in text
    assert "NOT" in text, "an uncomputed report must not read as a clean one"


def test_the_report_states_both_windows_it_measured_over(tmp_path):
    rule_file(tmp_path, "quiet-rule", applies_to=["app/**"])
    records = [record("other", "confirmed", day=3, seq=1)]
    rule_file(tmp_path, "other", applies_to=["app/**"])
    report = run(tmp_path, records=records, commits_=commits(40))
    text = lifecycle.render(report)
    assert report.commit_window in text
    assert report.corpus_window in text
    # The exact phrase, not a bare "1": the window string is dense with
    # digits and one of them is wall-clock derived (oldest Nd), so a
    # one-character needle goes vacuous with the calendar rather than with
    # the code.
    assert "1 record(s)" in report.corpus_window, (
        "the corpus window must state how much history it is reading")


# --------------------------------------------------------------------------
# PYTHON RULES — replayed through the gate's own checker, not left UNMEASURED
# --------------------------------------------------------------------------

def tal_rule(root: Path) -> None:
    """tests-accompany-logic over app/**, with this repo's own params."""
    rule_file(root, "tests-accompany-logic", engine="python",
              applies_to=["app/**"],
              params={"companion_prefix": "tests/", "exempt_globs": []})


def history(root: Path, *steps: dict, root_files=None) -> None:
    """A real git history under `root`: one root commit of `root_files`
    (a README by default), then one commit per step, each a {path: content}
    mapping written over the tree. The python replay reads real diffs, so
    synthetic (sha, additions) tuples cannot stand in for it."""
    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True)

    git("init", "-q", "-b", "main")
    for i, files in enumerate([root_files or {"README.md": "# fixture\n"},
                               *steps]):
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        git("add", *files)
        git("-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-q", "-m", f"step {i}")


def three_steps() -> tuple[dict, ...]:
    """Three commits under app/: logic with a test, logic without, no logic."""
    return ({"app/a.py": "def f():\n    return 1\n",
             "tests/test_a.py": "def test_f():\n    pass\n"},
            {"app/a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"},
            {"app/b.py": "X = 1\n"})


def test_a_python_rule_that_fires_on_one_commit_prints_1_of_n(tmp_path):
    """The replay docs/design/tests-accompany-logic-20260927.md ran by hand,
    run by the command: the checker fires on the one commit that added logic
    under app/ without touching tests/, and the row prints the count."""
    tal_rule(tmp_path)
    history(tmp_path, *three_steps())
    report = run(tmp_path, commits_=None, window=200)
    row = row_of(report, "tests-accompany-logic")
    assert (row.selected, row.flagged, row.replay_reason) == (3, 1, ""), row
    assert "flagged 1/3 selected" in lifecycle.render(report)


def test_a_python_rule_that_never_fires_prints_0_of_n(tmp_path):
    """A measured silence: the checker ran on every selected commit and found
    nothing, which is a 0 with its denominator, not UNMEASURED."""
    rule_file(tmp_path, "secrets-in-diff", engine="python",
              applies_to=["app/**"])
    history(tmp_path, *three_steps())
    report = run(tmp_path, commits_=None, window=200)
    row = row_of(report, "secrets-in-diff")
    assert (row.selected, row.flagged) == (3, 0), row
    text = lifecycle.render(report)
    assert "flagged 0/3 selected" in text
    assert "firing UNMEASURED" not in text


def test_a_claude_rule_still_prints_unmeasured_beside_replayed_python_rules(
        tmp_path):
    """UNMEASURED remains, for the one engine that cannot be replayed: a
    judgment rule. It keeps its selection count and reads no firing."""
    tal_rule(tmp_path)
    rule_file(tmp_path, "a-judgment", engine="claude", applies_to=["app/**"])
    history(tmp_path, *three_steps())
    report = run(tmp_path, commits_=None, window=200)
    row = row_of(report, "a-judgment")
    assert (row.selected, row.flagged) == (3, None), row
    assert "firing UNMEASURED" in lifecycle._counts_line(row)
    assert "judgment rule" in row.replay_reason
    assert row_of(report, "tests-accompany-logic").flagged == 1


def test_a_python_replay_scans_the_diff_the_gate_scans(tmp_path):
    """repo.yaml's context_excludes reach the replay, as they reach the gate:
    a commit whose only admitted file lives in an excluded path shows the
    checker nothing, so it is not SELECTED — never counted as a silence the
    checker was not in a position to break."""
    tal_rule(tmp_path)
    history(tmp_path, {"app/gen/x.py": "def h():\n    return 3\n"},
            {"app/y.py": "Y = 2\n"})
    row = row_of(run(tmp_path, commits_=None, window=200),
                 "tests-accompany-logic")
    assert (row.selected, row.flagged) == (2, 1), row
    row = row_of(run(tmp_path, commits_=None, window=200,
                     context_excludes=("app/gen/**",)),
                 "tests-accompany-logic")
    assert (row.selected, row.flagged) == (1, 0), row


def test_a_python_replay_selects_a_commit_that_only_deletes(tmp_path):
    """The gate runs a checker on a diff that only REMOVES lines, and
    `contract-freeze` fires on exactly that. Selecting on added lines would
    never show it the commit and print a measured 0 over a window that held
    the breaking change."""
    rule_file(tmp_path, "contract-freeze", engine="python",
              applies_to=["schemas/**"],
              params={"schema_file": "schemas/api.json",
                      "fixture_files": []})
    lines = ['{"type": "object", "properties": {', '"a": {}', "}}"]
    # The breaking commit deletes the `"b": {},` line and adds none.
    history(tmp_path, {"schemas/api.json": "\n".join(
                [lines[0], '"b": {},', *lines[1:]]) + "\n"},
            {"schemas/api.json": "\n".join(lines) + "\n"})
    row = row_of(run(tmp_path, commits_=None, window=200), "contract-freeze")
    assert (row.selected, row.flagged) == (2, 1), row


def test_a_selected_commit_whose_diff_cannot_be_read_is_named_unreplayed(
        tmp_path, monkeypatch):
    """Its path list selected it, so a failed full-diff read afterwards is a
    commit the replay missed — named, even when it added no line, never
    dropped from the count as though the rule had not selected it."""
    from warden import diffs
    rule_file(tmp_path, "contract-freeze", engine="python",
              applies_to=["schemas/**"],
              params={"schema_file": "schemas/api.json", "fixture_files": []})
    history(tmp_path, {"schemas/api.json": '{\n"b": {},\n"a": {}\n}\n'},
            {"schemas/api.json": '{\n"a": {}\n}\n'})
    deleting = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                              check=True, capture_output=True,
                              text=True).stdout.strip()
    real = diffs.get_context

    def flaky(root, base, head="HEAD", excludes=()):
        if head == deleting:
            raise diffs.DiffError("git diff failed: simulated")
        return real(root, base, head, excludes)

    monkeypatch.setattr(diffs, "get_context", flaky)
    report = run(tmp_path, commits_=None, window=200)
    row = row_of(report, "contract-freeze")
    assert (row.selected, row.flagged) == (2, 0), row
    assert any("simulated" in limit and "a floor over 2" in limit
               for limit in report.limits), report.limits


def test_cli_rules_lifecycle_replays_with_the_repos_context_excludes(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The command, not the function: `warden rules lifecycle` hands the
    replay repo.yaml's context_excludes. The sample repo excludes
    `**/uv.lock`, so a credential committed there is not a firing, while the
    same line in a scanned file is."""
    from conftest import copy_sample_repo
    root = copy_sample_repo(sample_repo, tmp_path / "repo")
    secret = 'password = "' + "q7Zr2mXv9LpT4wKs" + '"\n'
    history(root, {"lib/uv.lock": secret}, {"lib/conf.py": secret},
            root_files={"README.md": "# sampleproj\n"})
    monkeypatch.chdir(root)
    assert cli.main(["rules", "lifecycle"]) == 0
    capsys.readouterr()
    artifact = next((root / ".warden" / "out").glob(
        "*-rules-lifecycle/rule-lifecycle.json"))
    row = next(r for r in json.loads(artifact.read_text())["rows"]
               if r["rule_id"] == "secrets-in-diff")
    # 2 selected: lib/conf.py's commit, and the parentless README commit,
    # which is named unreplayed rather than counted. The excluded lock file's
    # commit is neither — without the excludes it would be 3 and 2.
    assert (row["selected"], row["flagged"]) == (2, 1), row


def test_a_python_commit_that_cannot_be_replayed_is_never_counted_silent(
        tmp_path):
    """A commit the replay could not diff measured nothing. Some of them make
    the count a floor, said so in LIMITS; all of them leave no count at all,
    which is the UNMEASURED path with the reason instead of a fabricated 0."""
    tal_rule(tmp_path)
    # The root commit adds logic under app/ and has no parent to diff against.
    history(tmp_path, {"app/b.py": "X = 1\n"},
            root_files={"app/a.py": "def f():\n    return 1\n"})
    report = run(tmp_path, commits_=None, window=200)
    row = row_of(report, "tests-accompany-logic")
    assert (row.selected, row.flagged) == (2, 0), row
    assert any("1 commit(s) it may select could not be replayed" in limit
               and "root commit" in limit and "a floor over 2" in limit
               for limit in report.limits), report.limits

    # Synthetic shas git has never seen: nothing was replayed at all.
    report = run(tmp_path, commits_=commits(12))
    row = row_of(report, "tests-accompany-logic")
    assert (row.selected, row.flagged) == (12, None), row
    assert "none of its 12 selected commit(s) could be replayed" in (
        row.replay_reason)
    assert row.verdict == "untested"


def test_a_python_rule_with_no_registered_checker_stays_unmeasured(tmp_path):
    """No checker under the rule's id means nothing to replay with: the row
    says so rather than reading the absent checker's silence as 0."""
    rule_file(tmp_path, "no-such-checker", engine="python",
              applies_to=["app/**"])
    history(tmp_path, *three_steps())
    row = row_of(run(tmp_path, commits_=None, window=200), "no-such-checker")
    assert (row.selected, row.flagged) == (3, None), row
    assert "no checker is registered" in row.replay_reason


# --------------------------------------------------------------------------
# CLI + skill wiring
# --------------------------------------------------------------------------

def test_cli_rules_lifecycle_exits_0_and_writes_an_artifact(sample_repo, tmp_path,
                                                            monkeypatch, capsys):
    from conftest import copy_sample_repo
    root = copy_sample_repo(sample_repo, tmp_path / "repo")
    monkeypatch.chdir(root)

    assert cli.main(["rules", "lifecycle"]) == 0
    out = capsys.readouterr().out
    assert "lifecycle" in out.lower()

    artifacts = sorted(
        (root / ".warden" / "out").glob("*-rules-lifecycle/rule-lifecycle.json"))
    assert len(artifacts) == 1


def test_an_action_refuses_a_flag_it_does_not_implement(sample_repo, tmp_path,
                                                         monkeypatch, capsys):
    """One parser serves both actions, so each action must refuse the other's
    flags: `rules lifecycle --max-unanswered 0` accepted and exiting 0 would
    be a ceiling enforcing nothing — a gate flag wired to no caller, which is
    the failure `enforcement-truth` names outright."""
    from conftest import copy_sample_repo
    root = copy_sample_repo(sample_repo, tmp_path / "repo")
    monkeypatch.chdir(root)

    assert cli.main(["rules", "lifecycle", "--max-unanswered", "0"]) == 2
    assert "--max-unanswered" in capsys.readouterr().err
    assert cli.main(["rules", "recommend", "--window", "5"]) == 2
    assert "--window" in capsys.readouterr().err
    assert cli.main(["rules", "lifecycle", "--window", "5"]) == 0


def skills_needing_a_version_bump(names, *, read_head, read_base):
    """Which skills' judgment contracts differ from the merge base.

    Separate from the guard below so the decision is REACHABLE by a test.
    Its most important branch — a skill absent from the base — cannot occur
    in a diff that adds no skill, so inside the guard it would be unreachable
    and a mutation reverting it would stay green: an untested branch inside
    the guard against untested guards.

    `read_base` returns None for a skill that is not on the base. That is a
    brand-new skill: the LARGEST possible judgment-contract change, not the
    absence of one. Reading it as "unchanged" would let a whole new skill
    ship under a frozen version id.
    """
    changed = []
    for name in names:
        base = read_base(name)
        if base is None or base != read_head(name):
            changed.append(name)
    return changed


def test_r2f3_a_brand_new_skill_needs_a_version_bump_too():
    """A skill absent from the merge base needs a version bump too.

    The three cases the guard has to tell apart, exercised directly because
    the real tree contains only one of them.
    """
    head = {"deliver": "same", "retro": "edited", "brand-new": "hello"}
    base = {"deliver": "same", "retro": "original"}
    changed = skills_needing_a_version_bump(
        sorted(head), read_head=head.__getitem__, read_base=base.get)
    assert changed == ["brand-new", "retro"], (
        "a brand-new skill must count as a changed judgment contract — it is "
        "the largest one there is, and reading its absent base as 'unchanged' "
        f"ships it under a frozen version id; got {changed}")


PACK_MANIFEST = "skills/nightgate-skills/.claude-plugin/plugin.json"
PACK_SKILLS_DIR = "skills/nightgate-skills/skills"


class UnreadableBase(Exception):
    """git could not hand over a file it says the base commit contains.

    RAISED, never folded into "absent". A comparison that could not happen
    must never read as one that passed, which is the posture the code this
    replaced stated and then lost: it caught every git failure into the same
    `None` the absent case uses, so a base whose blob cannot be read scored as
    "the pack is new" and the guard returned no refusal at all. Round 1 built
    that state by deleting one loose object and watched a real breach go green.
    """


def _rev(root: Path, *args: str) -> str | None:
    out = subprocess.run(["git", *args], cwd=root, capture_output=True,
                         text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def _reachable_from(root: Path, commit: str, ref: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", commit, ref],
                          cwd=root, capture_output=True).returncode == 0


def review_head(root: Path, *, upstream: str = "origin/main") -> str:
    """The commit this branch PROPOSES, read off the TOPOLOGY, never the environment.

    On a `pull_request` event `actions/checkout` leaves `refs/pull/N/merge`
    checked out: a merge commit whose FIRST parent is the base tip and whose
    second is the branch. `merge-base HEAD origin/main` is therefore
    `origin/main` itself, so a fork point taken from `HEAD` is the moving tip
    wearing another name, and the guard below is inert on the only machine
    that gates.

    THE FIRST FIX FOR THAT WAS ALSO INERT, and the reason is specific enough
    to be worth writing down rather than re-discovering: it read the proposed
    head out of the Actions event, and `conftest.clean_git_env` POPS
    `GITHUB_EVENT_PATH` for the whole session — deliberately, so a fixture
    repo's `attest check` cannot resolve the platform's own PR head. Any
    reader of that variable returns nothing inside this suite by
    construction, and a cell that sets it back proves the seam in the one
    state the suite guarantees never holds.

    So this asks git, and needs no environment at all. HEAD is a merge ref
    when three things hold together: it has exactly two parents; it is NOT
    reachable from `<upstream>` (a merge commit sitting ON the base branch is
    nobody's pull request); and its FIRST parent IS reachable from
    `<upstream>`, which is the property that makes the merge-base collapse.
    Then the proposed head is the second parent.

    Otherwise HEAD is returned unchanged, which is right everywhere else — a
    developer's branch commit, and equally a branch that merged `<upstream>`
    INTO itself, whose first parent is the branch rather than the base, so
    the middle test answers no.

    ONE SHAPE IS INDISTINGUISHABLE FROM A MERGE REF, and there is no fourth
    arm because git cannot tell them apart: a branch cut at the base tip that
    immediately merges a topic branch in (`checkout -b b main; merge topic`)
    has exactly two parents, is not reachable from `<upstream>`, and its first
    parent IS the base tip. That is byte for byte the topology a merge ref
    has. Only a commit of its own on the branch before the merge separates
    them, and this reader is handed neither a reflog nor an event.

    SO THE DIRECTION IS WHAT MAKES IT SAFE, and it is stated rather than
    claimed away. The topic head is returned, so the fork point comes from the
    topic branch rather than from `b` — at or BELOW the true one, since the
    topic forked no later than `b` did. A fork point further back can only
    make `pack_bump_refusal` see MORE history, which makes it more likely to
    find a version difference and stay silent. It errs toward not refusing,
    never toward the false red this whole helper exists to prevent. The cost
    is a missed refusal in that one topology, which is a workflow nothing here
    uses: `test_a_branch_that_merges_a_topic_at_the_base_tip_reads_as_a_
    merge_ref` builds it and pins both the misreading and its direction.
    """
    parents = _rev(root, "rev-list", "--parents", "-n", "1", "HEAD")
    if parents is None or len(parents.split()) != 3:
        return "HEAD"
    _, first_parent, second_parent = parents.split()
    if _reachable_from(root, "HEAD", upstream):
        return "HEAD"
    if not _reachable_from(root, first_parent, upstream):
        return "HEAD"
    return second_parent


def fork_point(root: Path, *, upstream: str = "origin/main",
               head: str | None = None) -> str | None:
    """The commit a branch FORKED FROM — `git merge-base <head> <upstream>`.

    NOT `<upstream>` itself, and the difference is the whole of this helper.
    The invariant below is a statement about one branch: it changed a
    judgment contract, so it must bump the pack. Read against the moving tip
    instead, the verdict stops being about the branch and starts depending on
    what OTHER branches land while this one waits for review — a branch that
    edited a skill and correctly bumped the pack goes red the moment a
    sibling merges and takes that same version, with nothing wrong with it at
    all.

    That is not hypothetical. One pull request merged and took pack version
    0.22.4; a second, already green and holding the same bump, went red on
    all three interpreter legs with `assert '0.22.4' != '0.22.4'`. A gate
    whose verdict on a frozen sha can change because someone else pressed
    merge is not enforcing an invariant, it is reporting a race.

    `head` defaults to `review_head`, and that default is load-bearing rather
    than tidy: passing `HEAD` on a runner reads the merge ref and gives back
    the tip. The claim this helper can make is therefore narrower than the one
    the first version of this docstring made — the fork point does not move
    under another branch's merge WHEN IT IS TAKEN FROM THE PROPOSED HEAD, and
    `review_head` is what takes it from there without consulting anything
    outside the repository.

    None when git cannot answer — a shallow clone, no remote, no git at all —
    which the guard reports as unevaluated rather than passed.
    """
    rev = head if head is not None else review_head(root, upstream=upstream)
    try:
        out = subprocess.run(
            ["git", "merge-base", rev, upstream], cwd=root,
            capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def file_at(root: Path, rev: str, path: str) -> str | None:
    """One tracked file's content at `rev`, or None when `rev` does not hold it.

    None means ABSENT AT THAT COMMIT and nothing else — the caller reads it as
    "new", which for a skill is the largest judgment-contract change there is,
    so every other failure has to be told apart from it. The tree is asked
    first (`ls-tree`, which reads the tree object and needs no blob) and only
    a tree that genuinely does not list the path answers None. A path the tree
    DOES list whose content git will not then produce raises
    `UnreadableBase`: that is the comparison failing, not the file being new.

    `--full-tree` is not optional. Without it `ls-tree` resolves its pathspec
    against the process CWD, so the same call from a subdirectory lists
    nothing at exit 0 — "proven absent" for every path, in the permissive
    direction, on the reader that decides what counts as changed.
    """
    listed = subprocess.run(
        ["git", "ls-tree", "--full-tree", "--name-only", "-z", rev, "--", path],
        cwd=root, capture_output=True, text=True)
    if listed.returncode != 0:
        raise UnreadableBase(
            f"git could not list {path} at {rev}: {listed.stderr.strip()}")
    if not listed.stdout.strip("\0"):
        return None
    out = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=root,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise UnreadableBase(
            f"{rev} lists {path} and git would not hand it over "
            f"({out.stderr.strip()}) — the comparison did not happen, which "
            "is not the same as the file being new")
    return out.stdout


def _base_pack_dir(root: Path, base_rev: str) -> str:
    """Where the base commit kept the pack, read off the base's own
    marketplace manifest, so a pack moved to a new directory is compared with
    its old self rather than read as new. A base with no manifest, or one
    serving other than exactly one plugin from a local path, keeps the head's
    directory, which is what the guard compared before."""
    head_pack = str(Path(PACK_SKILLS_DIR).parent)
    text = file_at(root, base_rev, ".claude-plugin/marketplace.json")
    if text is None:
        return head_pack
    plugins = json.loads(text).get("plugins") or []
    sources = [p.get("source") for p in plugins if isinstance(p, dict)]
    if len(sources) != 1 or not isinstance(sources[0], str) \
            or not sources[0].startswith("./"):
        return head_pack
    return sources[0].removeprefix("./").rstrip("/")


def pack_bump_refusal(root: Path, base_rev: str) -> str | None:
    """The refusal message when the pack-version invariant is broken, else None.

    Separate from the guard, and reachable, for the reason
    `skills_needing_a_version_bump` is: the answers that matter — a branch
    that changed a contract without bumping, one that bumped correctly while
    main moved past the same version, and a base whose content git cannot
    read — cannot all be present in the real tree at once, so inside the guard
    each is unreachable and a mutation reverting it stays green.

    `UnreadableBase` propagates deliberately. The caller turns it into a SKIP;
    swallowing it here is the shape round 1 found.
    """
    skills_dir = root / PACK_SKILLS_DIR
    base_pack = _base_pack_dir(root, base_rev)
    names = sorted(d.name for d in skills_dir.iterdir()
                   if d.is_dir() and (d / "SKILL.md").is_file())
    changed = skills_needing_a_version_bump(
        names,
        read_head=lambda n: (skills_dir / n / "SKILL.md").read_text(),
        read_base=lambda n: file_at(root, base_rev,
                                    f"{base_pack}/skills/{n}/SKILL.md"))
    if not changed:
        return None
    head = json.loads((root / PACK_MANIFEST).read_text())["version"]
    base_manifest = file_at(root, base_rev,
                            f"{base_pack}/.claude-plugin/plugin.json")
    # A manifest the base tree does not list means the pack itself is new,
    # which is a version the base never carried — never an unchanged one.
    base = (json.loads(base_manifest)["version"]
            if base_manifest is not None else None)
    if head != base:
        return None
    return (f"judgment contract(s) changed ({', '.join(changed)}) under an "
            "unchanged pack version id — the installer's cache and install "
            "identity key on that field, so the change ships silently. Bump "
            f"{PACK_MANIFEST} (the comparison is against the fork point "
            f"{base_rev}, so this is not another branch's merge)")


def test_the_pack_version_moves_when_a_skills_judgment_contract_does():
    """The pack's own contract says a
    change to what a skill gathers, proposes or forbids bumps plugin.json,
    because the installer's cache and install identity key on that field.

    A guard keyed to a literal (`!= "0.3.0"`) checks a moment, not an
    invariant: it goes blind the moment the version moves once, and stays
    green through every later change to a judgment contract — the exact
    case it exists to catch.

    The invariant is RELATIVE: if any skill's SKILL.md differs from the one on
    the merge base, the pack version must differ too.
    """
    root = Path(__file__).resolve().parents[1]
    base_rev = fork_point(root)
    if base_rev is None:
        pytest.skip("no fork point to compare against (shallow or fresh "
                    "clone, or an event naming a head this checkout does not "
                    "hold) — the pack-version invariant is relative, so it is "
                    "reported as unevaluated rather than passed")
    try:
        refusal = pack_bump_refusal(root, base_rev)
    except UnreadableBase as exc:
        pytest.skip(f"the fork point's content could not be read ({exc}) — "
                    "reported as unevaluated rather than passed")
    assert refusal is None, refusal


def _git_out(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _pack_race_repo(tmp_path: Path, *, bump_on_branch: bool,
                    checkout: str = "branch") -> tuple[Path, str]:
    """A repo carrying the exact topology that produced the false red.

    main holds one skill and pack version 0.22.3 and that commit is the fork
    point. A branch edits the skill (and bumps, or does not). THEN main moves:
    an unrelated pull request lands and takes 0.22.4 — the sibling merge.

    `checkout` decides which of the two shapes the guard then runs in, and the
    second is the one that gates. `"branch"` is a developer's laptop: the
    branch commit itself. `"merge-ref"` is CI: `refs/pull/N/merge`, a merge
    commit whose FIRST PARENT is main's tip, which is what `actions/checkout`
    leaves checked out on a `pull_request` event. Round 1 found the fixture
    built only the first, so the cells below could not see a fix that was
    inert in the second.

    Returns the root and the branch head sha — the sha a pull_request event's
    payload names, which is the thing `review_head` exists to read.
    """
    root = tmp_path / "race"
    skill = root / PACK_SKILLS_DIR / "deliver"
    skill.mkdir(parents=True)
    manifest = root / PACK_MANIFEST
    manifest.parent.mkdir(parents=True)

    def commit(message):
        subprocess.run(["git", "add", "-A"], cwd=root, check=True,
                       capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "-m", message], cwd=root, check=True,
                       capture_output=True)

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True,
                   capture_output=True)
    (skill / "SKILL.md").write_text("gather the evidence\n")
    manifest.write_text(json.dumps({"version": "0.22.3"}))
    commit("the fork point")
    # The upstream ref the guard compares against, standing where the branch
    # forked from — as it did when the branch was pushed.
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
                   cwd=root, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "-q", "-b", "branch"], cwd=root,
                   check=True, capture_output=True)
    (skill / "SKILL.md").write_text("gather the evidence, then judge it\n")
    if bump_on_branch:
        manifest.write_text(json.dumps({"version": "0.22.4"}))
    commit("the branch edits a judgment contract")
    branch_head = _git_out(root, "rev-parse", "HEAD")

    # The sibling lands and takes the same version the branch already holds.
    subprocess.run(["git", "checkout", "-q", "main"], cwd=root, check=True,
                   capture_output=True)
    manifest.write_text(json.dumps({"version": "0.22.4"}))
    commit("an unrelated pull request bumps the pack")
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "main"],
                   cwd=root, check=True, capture_output=True)

    if checkout == "merge-ref":
        # First parent main's tip, second the branch head — the order
        # actions/checkout's merge ref has, and the order that makes
        # merge-base(HEAD, origin/main) collapse onto the tip.
        # The identity is passed here for the same reason `commit()` passes
        # it: the suite runs under GIT_CONFIG_GLOBAL=/dev/null, so no
        # configured identity exists anywhere, and `commit-tree` survives only
        # where git AUTO-DETECTS one. macOS does; a runner does not, which
        # turned every gating job red while this cell was green on a laptop.
        merge = _git_out(root, "-c", "user.email=t@t", "-c", "user.name=t",
                         "commit-tree", f"{branch_head}^{{tree}}",
                         "-p", _git_out(root, "rev-parse", "main"),
                         "-p", branch_head, "-m", "Merge pull request")
        subprocess.run(["git", "checkout", "-q", merge], cwd=root, check=True,
                       capture_output=True)
    else:
        subprocess.run(["git", "checkout", "-q", branch_head], cwd=root,
                       check=True, capture_output=True)
    return root, branch_head


def test_a_branch_that_bumped_correctly_survives_a_siblings_merge(tmp_path):
    """The false red, reproduced and closed on a developer's shape.

    The branch edited a skill and bumped the pack. Then a sibling merged and
    took the same version. Against the FORK POINT the branch still bumped and
    the guard is silent; against the TIP the two versions are equal and the
    guard refuses a branch that did everything right. The second half is the
    mutation proof: put `origin/main` back where the merge base is and this
    cell reports the failure it exists to prevent.
    """
    root, _ = _pack_race_repo(tmp_path, bump_on_branch=True)
    base_rev = fork_point(root)
    assert base_rev is not None
    assert pack_bump_refusal(root, base_rev) is None, (
        "a branch that edited a skill and bumped the pack is being refused "
        "because a sibling merged and took the same version — the verdict is "
        "reading the moving tip, not the point the branch forked from")
    assert pack_bump_refusal(root, "origin/main") is not None, (
        "reading the tip no longer reproduces the false red, so nothing here "
        "pins that the merge base is what does the work")


def test_the_fork_point_holds_on_the_merge_ref_ci_actually_checks_out(
        tmp_path):
    """The same branch, in the shape the gate runs in — and with no environment.

    On a `pull_request` event `actions/checkout` leaves `refs/pull/N/merge`
    checked out, and its FIRST parent is the base tip, so a fork point taken
    from `HEAD` collapses onto `origin/main` and the fix becomes the defect it
    replaced. Both halves are asserted here, and the second is the mutation
    proof for this arm: reading `HEAD` on this topology refuses a branch that
    bumped correctly, and letting `review_head` find the proposed head does
    not.

    Nothing here sets an environment variable, deliberately. The first attempt
    at this fix read the Actions event, and this suite pops
    `GITHUB_EVENT_PATH` for its whole session — so the cell that proved it had
    to put the variable back, which made it a proof about a state that never
    occurs where the guard runs. A topology the fixture builds is a state that
    does occur.
    """
    root, branch_head = _pack_race_repo(tmp_path, bump_on_branch=True,
                                        checkout="merge-ref")
    assert fork_point(root, head="HEAD") == _git_out(root, "rev-parse", "main"), (
        "this fixture no longer reproduces the merge-ref collapse, so the arm "
        "below proves nothing about CI")
    assert pack_bump_refusal(root, fork_point(root, head="HEAD")) is not None, (
        "reading HEAD on the merge ref no longer reproduces the false red — "
        "the mutation this arm exists to pin has stopped biting")

    assert review_head(root) == branch_head, (
        "the merge ref's proposed head is its SECOND parent, and that is not "
        f"what was resolved: {review_head(root)}")
    base_rev = fork_point(root)
    assert base_rev == _git_out(root, "rev-parse", "HEAD^2^"), (
        f"the fork point taken from the proposed head is {base_rev}, not the "
        "commit the branch forked from")
    assert pack_bump_refusal(root, base_rev) is None, (
        "on the merge ref CI checks out, a branch that bumped correctly is "
        "still refused after a sibling's merge — the fix does not reach the "
        "only machine that gates")


def test_only_a_merge_ref_is_read_as_one(tmp_path):
    """The shapes `review_head` has to tell apart, each built.

    Over-reaching here would be worse than the defect it closes: reading a
    second parent that is not a pull request's would compare the branch
    against something it never forked from. So a plain branch commit and a
    branch that merged the base INTO itself must both answer `HEAD`, and only
    the merge ref — not reachable from the base, first parent reachable from
    it — answers with its second parent.
    """
    root, branch_head = _pack_race_repo(tmp_path, bump_on_branch=True)
    assert review_head(root) == "HEAD", (
        "a plain branch commit is being read as a merge ref")

    # Two parents, but the FIRST is the branch, so the merge-base does not
    # collapse and HEAD is already the proposed head.
    merged = _git_out(root, "-c", "user.email=t@t", "-c", "user.name=t",
                      "commit-tree", f"{branch_head}^{{tree}}",
                      "-p", branch_head,
                      "-p", _git_out(root, "rev-parse", "main"),
                      "-m", "Merge origin/main into the branch")
    subprocess.run(["git", "checkout", "-q", merged], cwd=root, check=True,
                   capture_output=True)
    assert review_head(root) == "HEAD", (
        "a branch that merged the base into itself is being read as a merge "
        "ref, so the guard would compare against the wrong parent entirely")


def test_a_branch_that_merges_a_topic_at_the_base_tip_reads_as_a_merge_ref(
        tmp_path):
    """The one topology `review_head` cannot tell from a merge ref, and the
    direction of its error — built rather than argued.

    `checkout -b b main; merge topic` gives a commit with two parents, not
    reachable from the base, whose first parent IS the base tip. A merge ref
    has exactly that shape, so no test on the topology can separate them and
    there is no fourth arm to write. What can be pinned is the direction: the
    topic head is returned, so the fork point comes from the topic branch, and
    the topic forked no later than `b` did. A fork point at or below the true
    one makes `pack_bump_refusal` see more history, so it errs toward staying
    silent — a MISSED refusal, never the false red the helper exists to
    prevent.

    Raised by a closure round and filed rather than fixed, because the two
    over-reach arms beside it cover the opposite direction and this shape has
    none.
    """
    root, branch_head = _pack_race_repo(tmp_path, bump_on_branch=True)
    main = _git_out(root, "rev-parse", "main")

    # A topic branch forked from the FIRST commit, so its fork point is
    # strictly below main's tip and the direction is measurable rather than a
    # coincidence of both forking at the same place.
    root_commit = _git_out(root, "rev-list", "--max-parents=0", "main")
    subprocess.run(["git", "checkout", "-q", "-b", "topic", root_commit],
                   cwd=root, check=True, capture_output=True)
    (root / "topic.txt").write_text("x\n")
    for args in (("add", "topic.txt"),
                 ("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-qm", "topic work")):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True)
    topic_head = _git_out(root, "rev-parse", "HEAD")

    # The branch: cut at main's tip, merging the topic in as its first commit.
    merged = _git_out(root, "-c", "user.email=t@t", "-c", "user.name=t",
                      "commit-tree", f"{branch_head}^{{tree}}",
                      "-p", main, "-p", topic_head,
                      "-m", "Merge topic into b")
    subprocess.run(["git", "checkout", "-q", merged], cwd=root, check=True,
                   capture_output=True)

    assert review_head(root) == topic_head, (
        "this fixture no longer reproduces the misreading, so nothing below "
        f"describes it: review_head answered {review_head(root)}")

    # THE DIRECTION. The fork point it reaches is an ancestor of the one the
    # true proposed head would give, so the range it judges is wider.
    read = fork_point(root)
    true = fork_point(root, head="HEAD")
    assert read == root_commit and true == main, (
        f"the fork points moved: read {read}, true {true}")
    assert _reachable_from(root, read, true), (
        "the fork point this misreading reaches is NOT an ancestor of the true "
        "one, so the error is no longer in the direction the docstring names "
        "and this shape needs deciding again rather than recording")


def test_a_commit_on_the_base_branch_is_never_read_as_a_merge_ref(tmp_path):
    """A merge commit sitting ON the base branch is nobody's pull request.

    This is the arm that keeps the second-parent rule from firing on `push`
    CI for the base branch, where HEAD is reachable from it and the proposed
    head is HEAD itself.
    """
    root, branch_head = _pack_race_repo(tmp_path, bump_on_branch=True)
    merged = _git_out(root, "-c", "user.email=t@t", "-c", "user.name=t",
                      "commit-tree", f"{branch_head}^{{tree}}",
                      "-p", _git_out(root, "rev-parse", "main"),
                      "-p", branch_head, "-m", "Merge pull request")
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", merged],
                   cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-q", merged], cwd=root, check=True,
                   capture_output=True)
    assert review_head(root) == "HEAD", (
        "the base branch's own merge commit is being read as a merge ref")


def test_a_branch_that_skipped_the_bump_is_still_refused(tmp_path):
    """The fix must not become a way to skip the bump.

    Same topology, same sibling merge, one difference: this branch edited the
    judgment contract and did not bump. The fork point carries 0.22.3 and so
    does the branch, so the pack ships a changed contract under a frozen id —
    which is the whole reason the guard exists, and it must still bite. Both
    shapes are driven, because only the second one gates.
    """
    for checkout in ("branch", "merge-ref"):
        root, branch_head = _pack_race_repo(
            tmp_path / checkout, bump_on_branch=False, checkout=checkout)
        refusal = pack_bump_refusal(
            root, fork_point(root, head=branch_head))
        assert refusal is not None, (
            f"on the {checkout} shape, a branch that changed a skill's "
            "judgment contract without bumping the pack version is passing — "
            "the merge-base reading has become a way to skip the bump")
        assert "deliver" in refusal and PACK_MANIFEST in refusal, (
            f"the refusal names neither the changed skill nor what to bump: "
            f"{refusal}")


def test_a_base_whose_content_git_cannot_read_is_never_a_pass(tmp_path):
    """A comparison that did not happen must not read as one that passed.

    Round 1's repro, kept: the base tree LISTS the manifest and the blob is
    gone, which the code this replaced folded into the same `None` the absent
    case uses — so "the pack is new" and a real breach returned no refusal at
    all. The tree is asked first now, so the absent case still answers None
    and this one raises.
    """
    root, branch_head = _pack_race_repo(tmp_path, bump_on_branch=False)
    base_rev = fork_point(root, head=branch_head)
    assert pack_bump_refusal(root, base_rev) is not None, (
        "the fixture no longer carries a real breach, so the raise below "
        "would not be standing in for a refusal")

    blob = _git_out(root, "rev-parse", f"{base_rev}:{PACK_MANIFEST}")
    (root / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
    with pytest.raises(UnreadableBase) as caught:
        pack_bump_refusal(root, base_rev)
    assert PACK_MANIFEST in str(caught.value), (
        f"the refusal does not name the file it could not read: {caught.value}")

    # And the absent case still answers None, so the split is real rather
    # than everything raising.
    assert file_at(root, base_rev, "skills/nightgate-skills/skills/absent/SKILL.md") is None


def _moved_pack_repo(tmp_path: Path, *, bump: bool) -> tuple[Path, str]:
    """A base whose pack lives under an older directory name, which the
    marketplace manifest names, and a head that moved it to PACK_SKILLS_DIR,
    edited a skill, and bumped the version or did not."""
    root = tmp_path / "moved"
    root.mkdir(parents=True)

    def commit(message):
        subprocess.run(["git", "add", "-A"], cwd=root, check=True,
                       capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "-m", message], cwd=root, check=True,
                       capture_output=True)

    def lay_out(pack: str, body: str, version: str):
        skill = root / pack / "skills" / "deliver"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(body)
        (root / pack / ".claude-plugin").mkdir()
        (root / pack / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": version}))
        (root / ".claude-plugin").mkdir(exist_ok=True)
        (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
            {"plugins": [{"name": Path(pack).name, "source": f"./{pack}"}]}))

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True,
                   capture_output=True)
    lay_out("skills/old-skills", "gather the evidence\n", "0.22.3")
    commit("the fork point")
    base = _git_out(root, "rev-parse", "HEAD")
    subprocess.run(["git", "rm", "-rq", "skills/old-skills"], cwd=root,
                   check=True, capture_output=True)
    lay_out(str(Path(PACK_SKILLS_DIR).parent),
            "gather the evidence, then judge it\n",
            "0.23.0" if bump else "0.22.3")
    commit("the pack moves and a judgment contract changes")
    return root, base


def test_a_pack_moved_to_a_new_directory_is_compared_with_where_the_base_kept_it(
        tmp_path):
    """A rename of the pack directory read as a brand-new pack: the guard
    looked for the base manifest only at the head's path, found none, and
    passed every changed skill under an unchanged version id. The base's pack
    path is read from the base's own marketplace manifest."""
    root, base = _moved_pack_repo(tmp_path / "unbumped", bump=False)
    refusal = pack_bump_refusal(root, base)
    assert refusal is not None and "deliver" in refusal, (
        "a moved pack that changed a skill under the same version passed")
    root, base = _moved_pack_repo(tmp_path / "bumped", bump=True)
    assert pack_bump_refusal(root, base) is None, (
        "a moved pack that bumped its version was refused")


@pytest.mark.parametrize("verdict", ["retire?", "demote?", "narrow?"])
def test_every_proposal_verdict_is_phrased_as_a_question(verdict):
    """Not cosmetic. These are proposals a human decides; a verdict that reads
    as a decision is how a report becomes an action nobody authorised."""
    assert verdict.endswith("?")
    assert verdict in lifecycle.PROPOSAL_VERDICTS


def test_rules_lifecycle_reads_the_declared_ruleset_not_a_hardcoded_path(tmp_path):
    """Repo facts come from config, never from this module."""
    elsewhere = tmp_path / "custom" / "rules"
    elsewhere.mkdir(parents=True)
    (elsewhere / "r.md").write_text(
        '---\nid: r\nseverity: MEDIUM\nengine: claude\napplies_to: ["**"]\n'
        "---\nbody\n")
    report = lifecycle.rule_lifecycle(tmp_path, records=[], rules_dir=elsewhere,
                                      commits=commits(40), window=200)
    assert [r.rule_id for r in report.rows] == ["r"]
    assert rules_mod.load_rules(elsewhere)[0].id == "r"


def test_rule_lifecycle_defaults_to_the_declared_rules_dir(tmp_path):
    """The fallback reads the declared `review.rules_dir`; a hardcoded
    `.warden/rules` would be a third copy of the derivation, and nothing
    else stops one appearing."""
    (tmp_path / "repo.yaml").write_text(
        "review:\n  rules_dir: policy/rules\n")
    d = tmp_path / "policy" / "rules"
    d.mkdir(parents=True)
    (d / "policy-lens.md").write_text(
        "---\nid: policy-lens\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\n---\nfixture rule\n')
    report = lifecycle.rule_lifecycle(tmp_path, records=[], commits=[])
    assert report.computed is True
    assert [r.rule_id for r in report.rows] == ["policy-lens"]


def test_rule_lifecycle_fails_closed_on_an_unreadable_declaration(tmp_path):
    """An unreadable repo.yaml means the ruleset to judge is unknown — that
    is an absent report, never a clean one."""
    (tmp_path / "repo.yaml").write_text("review: [unclosed")
    report = lifecycle.rule_lifecycle(tmp_path, records=[], commits=[])
    assert report.computed is False
    assert any("repo.yaml" in n for n in report.notes)
