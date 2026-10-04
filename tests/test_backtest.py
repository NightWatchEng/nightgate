"""Backtesting a proposed rule over the repo's own history.

A recommendation without flagged / true-positive / projected-false-positive
numbers — or an explicit unbacktested marker with a reason — does not ship. A
rule that would have fired and caught nothing is REJECTED with its numbers,
not quietly dropped. These pin exactly that.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import _AMBIENT_GIT_CONFIG
from warden import backtest as bt
from warden import catalog as cat


def _entry(pattern=r"eval\(", engine="declarative", scope="added",
           require=False):
    """A one-check catalog Entry, built through the real loader."""
    check = {"id": "c", "pattern": pattern, "message": "m", "scope": scope}
    if require:
        # the gate's compiler requires scope:file with require:true
        check["require"] = True
        check["scope"] = "file"
    raw = {
        "id": "danger-eval", "taxonomy": "CWE-95", "name": "eval of input",
        "guards": "Calls eval on text that may be untrusted.",
        "engine": engine,
        "applies_when": "the code evaluates strings",
        "false_positive_cost": "A flagged constant eval costs a dismissal.",
        "sources": [{"title": "CWE-95",
                     "url": "https://cwe.mitre.org/data/definitions/95.html"}],
    }
    if engine == "declarative":
        raw["starter"] = {"checks": [check]}
    elif engine == "claude":
        pass  # a claude entry ships no starter
    tmp = Path(pytest.importorskip("tempfile").mkdtemp()) / "g.yaml"
    tmp.write_text(yaml.safe_dump({"version": 1, "entries": [raw]}))
    return cat.load_catalog(tmp)[0]


# ── pure replay logic (synthetic history, no git) ─────────────────────────

def test_flag_commits_fires_only_on_matching_added_text():
    entry = _entry(r"eval\(")
    commits = [("a1", "x = eval(user_input)"),
               ("b2", "y = safe(value)"),
               ("c3", "z = eval(cfg)")]
    flagged, caveats = bt.flag_commits(entry, commits)
    assert flagged == {"a1", "c3"}
    assert caveats == []


def test_require_true_check_is_excluded_with_a_caveat():
    entry = _entry(require=True)
    flagged, caveats = bt.flag_commits(entry, [("a1", "eval(x)")])
    assert flagged == set()
    assert any("require:true" in c for c in caveats)


def test_classify_rejects_a_rule_that_fires_and_catches_nothing():
    # flagged three, none of them a known defect -> REJECTED with numbers
    result = bt.classify({"a1", "b2", "c3"}, defect_shas=set())
    assert result["verdict"] == "REJECTED"
    assert result["true_positive"] == 0
    assert result["false_positive"] == 3
    assert result["projected_fp_rate"] == 1.0


def test_classify_is_a_candidate_when_it_catches_a_real_defect():
    result = bt.classify({"a1", "b2"}, defect_shas={"a1"})
    assert result["verdict"] == "CANDIDATE"
    assert result["true_positive"] == 1
    assert result["false_positive"] == 1
    assert result["projected_fp_rate"] == 0.5


def test_classify_no_signal_when_the_rule_never_fires():
    result = bt.classify(set(), defect_shas={"a1"})
    assert result["verdict"] == "no-signal"
    assert result["projected_fp_rate"] is None


def test_defect_match_tolerates_abbreviated_revert_shas():
    # a revert names a 7-char prefix; the window carries the full 40-char sha
    full = "abcdef1234567890abcdef1234567890abcdef12"
    result = bt.classify({full}, defect_shas={"abcdef1"})
    assert result["true_positive"] == 1


# ── unbacktestable rules are marked, never given a fabricated number ──────

def test_a_claude_rule_is_unbacktested_with_a_reason():
    entry = _entry(engine="claude")
    result = bt.backtest_starter(entry, root=Path("."),
                                 commits=[], defect_shas=set())
    assert result.backtestable is False
    assert result.verdict == "unbacktested"
    assert result.reason and "judgment" in result.reason
    # and it carries NO fabricated precision figure
    assert result.projected_fp_rate is None


def test_backtest_starter_reports_the_window_and_estimate_caveat():
    entry = _entry(r"eval\(")
    commits = [("a1", "eval(x)"), ("b2", "ok()")]
    result = bt.backtest_starter(entry, root=Path("."),
                                 commits=commits, defect_shas=set())
    assert result.backtestable is True
    assert result.commits_scanned == 2
    assert result.flagged == 1
    assert result.verdict == "REJECTED"          # fired once, caught nothing
    # The rate is a revert-coincidence ESTIMATE that can err in either
    # direction, NOT an upper bound — the caveat must say so.
    joined = " ".join(result.caveats)
    assert "estimate" in joined.lower() and "either direction" in joined
    assert "upper bound" not in joined.lower()


def test_render_shows_a_rejected_proposals_numbers():
    entry = _entry(r"eval\(")
    result = bt.backtest_starter(entry, root=Path("."),
                                 commits=[("a1", "eval(x)")], defect_shas=set())
    line = bt.render_backtest(result)
    assert "REJECTED" in line
    assert "flagged 1" in line and "true-positive 0" in line


def test_render_unbacktested_says_so_with_the_reason():
    entry = _entry(engine="claude")
    result = bt.backtest_starter(entry, root=Path("."),
                                 commits=[], defect_shas=set())
    assert "UNBACKTESTED" in bt.render_backtest(result)


# ── git readers, exercised against a real throwaway repo ──────────────────

def _git(root, *args):
    # The ambient config layers come from conftest, never re-typed here:
    # this env is CLOSED, so the session fixture's os.environ cannot reach
    # it, and a hand-typed pair is easy to get wrong by omitting
    # GIT_CONFIG_NOSYSTEM — the key that covers the git versions predating
    # GIT_CONFIG_SYSTEM.
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                   env={**_AMBIENT_GIT_CONFIG, "HOME": str(root),
                        "PATH": os.environ["PATH"]})


@pytest.fixture
def history_repo(tmp_path):
    """A repo with: a clean commit, an 'eval(' commit, and a later revert of
    a second 'eval(' commit — so the revert target is the true positive."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.py").write_text("x = safe(1)\n")
    _git(r, "add", "."); _git(r, "commit", "-q", "-m", "clean start")
    (r / "b.py").write_text("y = eval(untrusted)\n")           # flagged, not reverted
    _git(r, "add", "."); _git(r, "commit", "-q", "-m", "add b")
    (r / "c.py").write_text("z = eval(bad)\n")                 # flagged AND reverted
    _git(r, "add", "."); _git(r, "commit", "-q", "-m", "add c")
    bad_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=r, text=True,
                             capture_output=True).stdout.strip()
    _git(r, "revert", "--no-edit", bad_sha)                    # names c's sha
    return r, bad_sha


def test_read_reverted_shas_finds_the_revert_target(history_repo):
    r, bad_sha = history_repo
    reverted = bt.read_reverted_shas(r)
    assert any(bad_sha.startswith(d) or d == bad_sha for d in reverted)


def test_read_commit_additions_captures_added_lines(history_repo):
    r, _ = history_repo
    additions = bt.read_commit_additions(r, 10)
    # each commit is (sha, [(path, added_text), ...])
    text = "\n".join(t for _, files in additions for _, t in files)
    assert "eval(untrusted)" in text and "eval(bad)" in text


def test_end_to_end_backtest_over_a_real_repo_splits_tp_from_fp(history_repo):
    r, bad_sha = history_repo
    entry = _entry(r"eval\(")
    result = bt.backtest_starter(entry, r, window=10)
    # two commits added eval(: one was reverted (true positive), one was not
    assert result.flagged == 2
    assert result.true_positive == 1
    assert result.false_positive == 1
    assert result.verdict == "CANDIDATE"
    assert 0.49 < result.projected_fp_rate < 0.51


# ── the advisor renders a backtest line on a recommendation ───────────────

def test_render_report_shows_a_backtest_line_when_present():
    from warden import advisor
    rec = advisor.Recommendation(
        entry_id="danger-eval", taxonomy="CWE-95", name="eval",
        engine="declarative", basis="prior-art", score=1.0,
        rationale="prior art", corpus_n=0, measured_hits=None,
        backtest=bt.Backtest(
            engine="declarative", backtestable=True, window="last 80 commits",
            commits_scanned=80, flagged=5, true_positive=0, false_positive=5,
            projected_fp_rate=1.0, verdict="REJECTED", caveats=()).to_dict())
    report = advisor.Report(recommendations=(rec,), gap_known=True)
    out = advisor.render(report)
    assert "backtest: REJECTED" in out
    assert "flagged 5" in out and "true-positive 0" in out


# ── diff parsing, glob scoping and revert framing ────────────────────────

def test_parse_added_by_file_keeps_a_line_that_starts_with_plus_plus():
    """An added source line beginning with '++' is emitted as '+++content'
    by --unified=0; a startswith('+++') filter would drop it as a header."""
    diff = ("diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -0,0 +1 @@\n"
            "+++counter = eval(bad)\n")
    files = bt.parse_added_by_file(diff)
    assert files == [("x.py", "++counter = eval(bad)")]


def test_flag_commits_honours_a_checks_globs():
    """A check scoped to *.py must not flag a match in a .md file."""
    import yaml as _yaml
    from warden import catalog as _cat
    raw = {"id": "danger-eval", "taxonomy": "CWE-95", "name": "e",
           "guards": "g calls eval on text.", "engine": "declarative",
           "applies_when": "w", "false_positive_cost": "c costs a dismissal.",
           "sources": [{"title": "CWE-95",
                        "url": "https://cwe.mitre.org/data/definitions/95.html"}],
           "starter": {"checks": [{"id": "c", "pattern": r"eval\(",
                                   "message": "m", "scope": "added",
                                   "globs": ["*.py"]}]}}
    p = Path(__import__("tempfile").mkdtemp()) / "g.yaml"
    p.write_text(_yaml.safe_dump({"version": 1, "entries": [raw]}))
    e = _cat.load_catalog(p)[0]
    flagged_md, _ = bt.flag_commits(e, [("a1", [("notes.md", "x = eval(1)")])])
    flagged_py, _ = bt.flag_commits(e, [("a1", [("app.py", "x = eval(1)")])])
    assert flagged_md == set()          # .md excluded by the glob
    assert flagged_py == {"a1"}         # .py admitted


def test_scope_file_check_carries_an_under_flag_caveat():
    import yaml as _yaml
    from warden import catalog as _cat
    raw = {"id": "danger-eval", "taxonomy": "CWE-95", "name": "e",
           "guards": "g calls eval on text.", "engine": "declarative",
           "applies_when": "w", "false_positive_cost": "c costs a dismissal.",
           "sources": [{"title": "CWE-95",
                        "url": "https://cwe.mitre.org/data/definitions/95.html"}],
           "starter": {"checks": [{"id": "c", "pattern": r"eval\(",
                                   "message": "m", "scope": "file"}]}}
    p = Path(__import__("tempfile").mkdtemp()) / "g.yaml"
    p.write_text(_yaml.safe_dump({"version": 1, "entries": [raw]}))
    e = _cat.load_catalog(p)[0]
    _, caveats = bt.flag_commits(e, [("a1", [("x.py", "eval(1)")])])
    assert any("scope:file" in c and "under-flag" in c for c in caveats)


def test_parse_reverted_survives_control_bytes_in_a_body():
    """NUL-terminated records are robust to a stray control byte a body
    might contain, where 0x1d framing would split mid-record."""
    sha_a = "a" * 40
    sha_b = "b" * 40
    raw = (f"{sha_a}chore: touch \x1d weird byte in body\x00"
           f"{sha_b}Revert x\n\nThis reverts commit {'c' * 40}.\x00")
    assert bt.parse_reverted(raw) == {"c" * 40}
