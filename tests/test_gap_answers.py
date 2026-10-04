"""Answering the guardrail gap.

THE PROBLEM. `warden rules recommend` reports which catalog entries a repo
does not enforce. Without a way to ANSWER one, a class deliberately
considered and declined reappears in every run, indistinguishable from a
class nobody has ever looked at — so the report degrades into advice nobody
acts on, and declines survive only as wiki PROSE.

THE SHAPE OF THE FIX, and the line it walks: an answer is a RECORD that
thinking happened, not an exemption from thinking. It carries a verdict, an
argument, and (for a deferral) a tracker id. Answered entries stay visible
with their reasons. Everything unanswered stays in the count, and the count
is the headline.

The two halves of the acceptance are tested separately below:
  - the proposal PROVABLY RECURS      (an unanswered gap does not decay)
  - a DETERMINISTIC SIGNAL GOES RED   (--max-unanswered)
"""

from pathlib import Path

import pytest
import yaml
from conftest import why_a_ci_job_might_not_run

from warden import advisor

ROOT = Path(__file__).parent.parent


def _write(root: Path, answers: list[dict]) -> None:
    p = root / ".warden"
    p.mkdir(parents=True, exist_ok=True)
    (p / "catalog-answers.yaml").write_text(
        yaml.safe_dump({"version": 1, "answers": answers}))


REASON = ("no database and no SQL surface anywhere in this tree, verified "
          "rather than assumed; flips the moment one is added")


# ---------- the loader fails LOUD, never silent -----------------------------

def test_absent_file_means_everything_is_unanswered(tmp_path):
    answers, complaints = advisor.load_answers(tmp_path)
    assert answers == {} and complaints == []


def test_unreadable_file_does_not_silence_the_gap(tmp_path):
    """The failure direction is load-bearing.

    An unreadable answers file yields NO answers, so every entry falls back to
    UNANSWERED — the report gets louder, never quieter. The opposite default
    would let one malformed line silence the whole gap, which is this file
    becoming the thing it was built to prevent.
    """
    (tmp_path / ".warden").mkdir()
    (tmp_path / ".warden" / "catalog-answers.yaml").write_text("{{ not yaml")
    answers, complaints = advisor.load_answers(tmp_path)
    assert answers == {}
    assert complaints and "could not be read" in complaints[0]


@pytest.mark.parametrize("bad,why", [
    ({"id": "sql-injection", "verdict": "nope", "reason": REASON},
     "verdict"),
    ({"id": "sql-injection", "verdict": "not-applicable", "reason": "no."},
     "not an argument"),
    ({"verdict": "not-applicable", "reason": REASON}, "'id' must be"),
    # Scalar fields are typed, not only the CONTAINERS: untyped, these three
    # crash instead of complaining — an int id would load CLEAN and then kill
    # sorted() later in recommend().
    ({"id": 42, "verdict": "not-applicable", "reason": REASON}, "'id' must be"),
    ({"id": ["a", "b"], "verdict": "not-applicable", "reason": REASON},
     "'id' must be"),
    ({"id": "sql-injection", "verdict": "not-applicable", "reason": 42},
     "'reason' must be"),
    ({"id": "sql-injection", "verdict": "not-applicable", "reason": REASON,
      "decided_in": True}, "'decided_in' must be"),
    ({"id": "sql-injection", "verdict": "deferred", "reason": REASON},
     "needs a 'bead'"),
    # Everything below passes a check that measures presence rather than
    # substance. Forty dots clear a `len(reason) >= 40` floor, and a
    # whitespace or boolean bead clears a bare truthiness test — the boolean
    # then reaches the JSON artifact as `true` where a tracker id belongs.
    ({"id": "sql-injection", "verdict": "not-applicable", "reason": "." * 40},
     "not an argument"),
    ({"id": "sql-injection", "verdict": "not-applicable",
      "reason": "https://example.com/a/very/long/url/over/forty/characters"},
     "not an argument"),
    ({"id": "sql-injection", "verdict": "not-applicable",
      "reason": "no no no no no no no no no no no no"},
     "not an argument"),
    ({"id": "sql-injection", "verdict": "deferred", "reason": REASON,
      "bead": "   "}, "needs a 'bead'"),
    ({"id": "sql-injection", "verdict": "deferred", "reason": REASON,
      "bead": True}, "needs a 'bead'"),
])
def test_an_answer_without_an_argument_is_rejected(tmp_path, bad, why):
    """A verdict with no reasoning is a mute, not an answer.

    The reason floor and the deferral's required bead are what stop this file
    becoming a list of things somebody once waved through: an answer has to
    leave an argument the next reader inherits, and a deferral has to leave a
    tracker so it is a decision rather than a gap with a nicer name.
    """
    _write(tmp_path, [bad])
    answers, complaints = advisor.load_answers(tmp_path)
    assert answers == {}, "a malformed answer was accepted"
    assert any(why in c for c in complaints), complaints


@pytest.mark.parametrize("raw", ["- id: sql-injection", "just a string", "42"])
def test_a_wrong_shape_file_complains_instead_of_crashing(tmp_path, raw):
    """Valid YAML of the wrong shape must complain, not raise.

    A top-level list or scalar parses fine and then dies on `.get` — a
    traceback and exit 2 through the CLI, with no report at all. That is the
    opposite of the contract: a malformed file must make the report NOISIER.
    """
    (tmp_path / ".warden").mkdir()
    (tmp_path / ".warden" / "catalog-answers.yaml").write_text(raw)
    answers, complaints = advisor.load_answers(tmp_path)
    assert answers == {}
    assert complaints and "not a mapping" in complaints[0]


def test_a_duplicate_answer_is_reported_not_silently_overwritten(tmp_path):
    """Last-write-wins dropped the stronger verdict and its tracker.

    A human reads the file top-down and sees the deferral; the tool read the
    bottom block and enforced the weaker one, with no diagnostic.
    """
    _write(tmp_path, [
        {"id": "sql-injection", "verdict": "deferred", "reason": REASON,
         "bead": "agentops-1"},
        {"id": "sql-injection", "verdict": "not-applicable", "reason": REASON},
    ])
    answers, complaints = advisor.load_answers(tmp_path)
    assert answers["sql-injection"].verdict == "deferred", "the first lost"
    assert any("duplicate answer" in c for c in complaints), complaints


def test_a_well_formed_answer_loads(tmp_path):
    _write(tmp_path, [{"id": "sql-injection", "verdict": "not-applicable",
                       "reason": REASON}])
    answers, complaints = advisor.load_answers(tmp_path)
    assert not complaints
    assert answers["sql-injection"].verdict == "not-applicable"


# ---------- this repo's own answers -----------------------------------------

def test_this_repos_answers_are_well_formed():
    answers, complaints = advisor.load_answers(ROOT)
    assert not complaints, f"{advisor.ANSWERS_PATH} has problems: {complaints}"
    assert answers, "this repo records no answers — three were decided"


def test_every_deferral_names_a_tracker():
    answers, _ = advisor.load_answers(ROOT)
    naked = [a.entry_id for a in answers.values()
             if a.verdict == "deferred" and not a.bead]
    assert not naked, f"deferrals with no bead: {naked}"


def test_decisions_are_machine_readable_now():
    """Catalog decisions in docs/wiki/Gate-Pipeline.md are machine-readable.

    This is the surface that holds them; the prose points at it rather than
    being the only copy.
    """
    answers, _ = advisor.load_answers(ROOT)
    for entry_id in ("sql-injection", "xss-unescaped-output"):
        assert entry_id in answers, f"{entry_id} lost its recorded decision"
    # The third decision, os-command-injection, is not an answer: its starter
    # is ADOPTED, so its record is the rule that implements the entry, and an
    # answer for an enforced entry would be reported stale.
    from warden import rules as rules_mod
    implementing = [r.id for r in rules_mod.load_rules(ROOT / ".warden" / "rules")
                    if "os-command-injection" in r.implements]
    assert implementing, ("os-command-injection is neither answered nor "
                          "enforced — the rule's adoption was lost")
    assert "os-command-injection" not in answers, (
        "an answer for an enforced entry rots as stale — the deferral "
        "should have been removed when the rule landed")


# ---------- the two halves of the acceptance --------------------------------

def test_an_answered_entry_leaves_the_gap_but_stays_visible():
    """Answered is not hidden. The reason travels with it."""
    report = advisor.recommend(ROOT, records=[],
                               rules_dir=ROOT / ".warden" / "rules")
    answered = {a.entry_id for a in report.answered}
    assert "sql-injection" in answered
    assert "sql-injection" not in {r.entry_id for r in report.recommendations}
    rendered = advisor.render(report)
    # "ANSWERED (" — the bare word is a substring of the UNANSWERED headline,
    # so the first version of this assertion could not fail if the section
    # were deleted.
    assert "ANSWERED (" in rendered and "sql-injection" in rendered
    assert "not an exemption" in rendered


def test_an_unanswered_gap_survives_the_report_being_read(tmp_path):
    """The first half of the acceptance, tested against STATE, not repetition.

    The first version called a pure function three times in one process and
    asserted the counts matched — which cannot vary, so it proved nothing. The
    property that matters is that reading the report does not consume the gap
    and that nothing decays it: only adopting the entry or recording an answer
    moves the number. So: measure, read, re-measure over a fresh scratch repo,
    then answer one entry and watch the count move by exactly one.
    """
    import shutil
    shutil.copytree(ROOT / ".warden" / "rules", tmp_path / ".warden" / "rules")
    rules_dir = tmp_path / ".warden" / "rules"

    first = advisor.recommend(tmp_path, records=[], rules_dir=rules_dir)
    assert first.recommendations, "no gap to test against — vacuous"
    advisor.render(first)                       # reading must not consume it
    again = advisor.recommend(tmp_path, records=[], rules_dir=rules_dir)
    assert len(again.recommendations) == len(first.recommendations)

    target = first.recommendations[0].entry_id
    _write(tmp_path, [{"id": target, "verdict": "not-applicable",
                       "reason": REASON}])
    after = advisor.recommend(tmp_path, records=[], rules_dir=rules_dir)
    assert len(after.recommendations) == len(first.recommendations) - 1, (
        "recording an answer is the ONLY thing that should move this count")


def test_this_repo_has_no_stale_or_contradicted_answers():
    """A live-tree health check — NOT the stale-detection test.

    The first version of this was the only coverage the stale branch had, and
    it asserted an empty list against a tree that has no stale answer. Deleting
    the entire detection block left the suite green. The real tests are below;
    this one only asserts the shipped file is currently healthy.
    """
    report = advisor.recommend(ROOT, records=[],
                               rules_dir=ROOT / ".warden" / "rules")
    bad = [x for x in report.limits
           if "is stale" in x or "not a catalog entry" in x
           or "CONTRADICTED" in x]
    assert not bad, bad


def _repo(tmp_path: Path, answers: list[dict]) -> Path:
    """A scratch repo that can actually be recommended over.

    The tests below exercise the answer/contradiction machinery through
    os-command-injection, which needs to be UNENFORCED in the scratch repo —
    an answer for an enforced entry is (correctly) reported stale before the
    contradiction logic ever runs. That starter is adopted as a real rule,
    so the scratch copy removes any rule implementing the probe
    entry rather than silently changing what these tests test. Deriving the
    removal from `implements:` keeps this true even if the rule is renamed.
    """
    import shutil

    from warden import rules as rules_mod
    shutil.copytree(ROOT / ".warden" / "rules", tmp_path / ".warden" / "rules")
    rules_dir = tmp_path / ".warden" / "rules"
    for rule in rules_mod.load_rules(rules_dir):
        if "os-command-injection" in rule.implements:
            Path(rule.path).unlink()
    _write(tmp_path, answers)
    return rules_dir


def test_an_answer_for_an_enforced_entry_is_reported_stale(tmp_path):
    """Answering something a rule already implements is a rotted decision."""
    rules_dir = _repo(tmp_path, [{"id": "swallowed-exceptions",
                                  "verdict": "not-applicable",
                                  "reason": REASON}])
    report = advisor.recommend(tmp_path, records=[], rules_dir=rules_dir)
    assert any("swallowed-exceptions" in x and "stale" in x
               for x in report.limits), report.limits


def test_an_answer_for_an_unknown_entry_is_reported(tmp_path):
    """The catalog dropped it, or it was never an entry — either way, say so."""
    rules_dir = _repo(tmp_path, [{"id": "no-such-entry-at-all",
                                  "verdict": "not-applicable",
                                  "reason": REASON}])
    report = advisor.recommend(tmp_path, records=[], rules_dir=rules_dir)
    assert any("no-such-entry-at-all" in x and "not a catalog entry" in x
               for x in report.limits), report.limits


def test_a_not_applicable_answer_is_contradicted_by_the_code(tmp_path):
    """THE property that makes an answer a record rather than a mute.

    An answered branch that short-circuits BEFORE `measure()` leaves a
    `not-applicable` verdict that can never be refuted. Claim
    os-command-injection is inapplicable here while planting a `probe.py`
    that its starter matches, and the report must say so.

    The hit comes from THAT probe file, not from the real tree: `_repo`
    copies only `.warden/rules`, and `.warden` is in `_SKIP_DIRS`, so nothing
    copied is even scanned.
    """
    rules_dir = _repo(tmp_path, [{"id": "os-command-injection",
                                  "verdict": "not-applicable",
                                  "reason": REASON}])
    (tmp_path / "probe.py").write_text('os.system("rm " + p)\n')
    report = advisor.recommend(tmp_path, records=[], rules_dir=rules_dir)

    answer = next(a for a in report.answered
                  if a.entry_id == "os-command-injection")
    assert answer.hits and answer.hits > 0, "the starter was never measured"
    assert answer.contradicted
    assert any("CONTRADICTED" in x for x in report.limits), report.limits
    assert "CONTRADICTED BY THE CODE" in advisor.render(report)


def test_an_unevaluable_gap_is_unknown_not_zero(tmp_path):
    """FAIL CLOSED. The strictest ceiling must not PASS on a broken ruleset.

    `recommend` returns an empty Report when the ruleset cannot be read, so if
    `len(recommendations)` were the headline, the artifact field and the exit
    condition, a repo with an unreadable rules dir would print `UNANSWERED: 0`
    and satisfy `--max-unanswered 0`. A count of 0 for want of looking is the
    fail-open this feature exists to prevent.
    """
    (tmp_path / ".warden" / "rules").mkdir(parents=True)   # empty: unreadable
    report = advisor.recommend(tmp_path, records=[],
                               rules_dir=tmp_path / ".warden" / "rules")
    assert report.gap_known is False
    assert report.to_dict()["unanswered"] is None, "0 would claim an answer"
    assert "UNKNOWN" in advisor.render(report)
    assert "NOT zero" in advisor.render(report)


def test_the_ceiling_is_the_deterministic_signal(tmp_path, monkeypatch, capsys):
    """The second half of the acceptance: something can go RED.

    Runs against a SCRATCH repo. Invoking the CLI in the real one would write
    real run dirs into the developer's `.warden/out/`, repoint `latest`, and
    derive the ceiling from live state — asserting against the thing it runs
    on.
    """
    import shutil

    from warden import cli

    shutil.copytree(ROOT / ".warden" / "rules", tmp_path / ".warden" / "rules")
    shutil.copy(ROOT / "repo.yaml", tmp_path / "repo.yaml")
    monkeypatch.chdir(tmp_path)

    report = advisor.recommend(tmp_path, records=[],
                               rules_dir=tmp_path / ".warden" / "rules")
    n = len(report.recommendations)
    assert n > 1, "need a real gap for the ceiling to mean anything"

    assert cli.main(["rules", "recommend", "--max-unanswered", str(n)]) == 0
    assert cli.main(["rules", "recommend", "--max-unanswered", str(n - 1)]) == 1
    assert "exceeds the declared ceiling" in capsys.readouterr().err


def test_the_ceiling_refuses_to_pass_on_an_unevaluable_gap(tmp_path,
                                                           monkeypatch,
                                                           capsys):
    """Exit 2, never 0, when the gap could not be computed.

    The strictest ceiling passing on a broken ruleset is the fail-open this
    feature exists to prevent. Exit 2 is the
    platform's infra/config code — distinguishable from 1, a real breach.
    """
    import shutil

    from warden import cli

    (tmp_path / ".warden" / "rules").mkdir(parents=True)   # empty: unreadable
    shutil.copy(ROOT / "repo.yaml", tmp_path / "repo.yaml")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["rules", "recommend", "--max-unanswered", "0"]) == 2
    assert "could not be computed" in capsys.readouterr().err


def test_a_long_reason_is_wrapped_whole_never_cut(tmp_path):
    """The argument must travel with the entry, in full.

    A reason cut at any bound, with or without a marker, loses its tail — and
    the tail is where the flip condition the answers file's own header makes
    mandatory, and the sentence saying what would END a deferral, tend to
    live.

    So it wraps rather than truncating, and this pins the property that
    matters — every word of the reason reaches the reader.
    """
    tail = "and it flips the moment this repository grows a database of any kind"
    reason = (
        "No database and no SQL surface anywhere in this tree, verified "
        "rather than assumed by reading every import and every call site, "
        "which took a while but is the only way to be sure about it, " + tail)
    assert len(reason) > 240, "probe is too short to exercise the old cut"

    rules_dir = _repo(tmp_path, [{"id": "sql-injection",
                                  "verdict": "not-applicable",
                                  "reason": reason}])
    rendered = advisor.render(
        advisor.recommend(tmp_path, records=[], rules_dir=rules_dir))

    assert "[truncated]" not in rendered, "the reason was cut, not wrapped"
    flat = " ".join(rendered.split())
    assert tail in flat, "the tail of the argument never reached the reader"
    # Scoped to the wrapped REASON lines: asserting over every line in the
    # report would fail on PRIOR ART rationales — a guard broader than the
    # thing it guards.
    reason_lines = [ln for ln in rendered.splitlines()
                    if ln.startswith("      ") and "verified" in ln
                    or ln.startswith("      ") and "database" in ln]
    assert reason_lines, "the reason never rendered"
    assert all(len(ln) < 120 for ln in reason_lines), \
        f"a wrapped reason line is too wide: {max(reason_lines, key=len)!r}"


def test_an_unmeasurable_entry_says_so_rather_than_looking_clean(tmp_path):
    """25 of 32 catalog entries ship no starter and can never be contradicted.

    A blank suffix reads as "measured, and clean" — the same real-zero-vs-
    unread confusion this module is built to prevent, on the classes that are
    most expensive to silence (every engine:claude entry).
    """
    rules_dir = _repo(tmp_path, [{"id": "broken-access-control",
                                  "verdict": "not-applicable",
                                  "reason": REASON}])
    report = advisor.recommend(tmp_path, records=[], rules_dir=rules_dir)
    answer = next(a for a in report.answered
                  if a.entry_id == "broken-access-control")
    assert answer.hits is None and answer.contradicted is False
    assert "NOT MECHANICALLY CHECKABLE" in advisor.render(report)


def test_a_contradicted_answer_is_counted_as_unanswered(tmp_path):
    """Detection that does not reach the gate is theatre.

    A refutation computed and then discarded leaves the entry out of the
    count and under the ceiling, so a falsified `not-applicable` flips a red
    build green and a false waiver is cheaper than an honest deferral.
    """
    rules_dir = _repo(tmp_path, [{"id": "os-command-injection",
                                  "verdict": "not-applicable",
                                  "reason": REASON}])
    (tmp_path / "probe.py").write_text('os.system("rm " + p)\n')
    report = advisor.recommend(tmp_path, records=[], rules_dir=rules_dir)

    assert "os-command-injection" in {r.entry_id for r in report.recommendations}, \
        "a contradicted answer bought its way out of the gated count"
    assert "os-command-injection" in {a.entry_id for a in report.answered}, \
        "the record and its refutation must both stay visible"


# ---------- answer_state: one function, one rule -----------------------------

def test_an_unmeasured_answer_fails_closed(tmp_path, monkeypatch):
    """"Could not look" is not "clean".

    `measure()` returns caveats and `_scan_targets` can truncate
    ALPHABETICALLY at its bound. Discarding either signal lets a
    `not-applicable` waiver over code the scan never reached render
    `(starter matches 0)` — a zero nobody measured — and leave the gated
    count. A contradicted answer is the same shape one signal earlier, which
    is why the decision lives in exactly one function.
    """
    rules_dir = _repo(tmp_path, [{"id": "os-command-injection",
                                  "verdict": "not-applicable",
                                  "reason": REASON}])
    (tmp_path / "probe.py").write_text('os.system("rm " + p)\n')
    monkeypatch.setattr(advisor, "_MAX_SCAN_FILES", 1)   # force truncation

    report = advisor.recommend(tmp_path, records=[], rules_dir=rules_dir)
    answer = next(a for a in report.answered
                  if a.entry_id == "os-command-injection")

    assert answer.state == "unmeasured"
    assert answer.hits is None, "an unmeasured entry must not report a count"
    assert "os-command-injection" in {r.entry_id for r in report.recommendations}, \
        "an unverifiable waiver stayed out of the gated count"
    assert any("could NOT be checked" in x for x in report.limits), report.limits
    assert "UNVERIFIED" in advisor.render(report)


def test_a_no_starter_answer_is_disclosed_but_still_answers(tmp_path):
    """Structural unmeasurability is accepted; incidental failure is not.

    An entry that ships no starter can NEVER be checked mechanically, so
    requiring a measurement would make the feature useless for 25 of 32
    entries. It is disclosed instead. That is a different case from a
    measurement that could have run and did not, which fails closed above.
    """
    rules_dir = _repo(tmp_path, [{"id": "broken-access-control",
                                  "verdict": "not-applicable",
                                  "reason": REASON}])
    report = advisor.recommend(tmp_path, records=[], rules_dir=rules_dir)
    answer = next(a for a in report.answered
                  if a.entry_id == "broken-access-control")

    assert answer.state == "no-starter"
    assert "broken-access-control" not in {r.entry_id
                                           for r in report.recommendations}
    assert "NOT MECHANICALLY CHECKABLE" in advisor.render(report)


@pytest.mark.parametrize("bad,why", [
    ({"id": "sql-injection", "verdict": "not-applicable", "reason": REASON,
      "expires": "2026-01-01"}, "unknown key"),
    ({"id": "sql-injection", "verdict": "not-applicable", "reason": REASON,
      "resaon": "typo"}, "unknown key"),
])
def test_an_unknown_key_is_refused_not_dropped(tmp_path, bad, why):
    """A constraint the loader does not understand must not be silently lost.

    `expires:` or `scope:` written by a founder who believes it narrows a
    waiver must not be dropped, making the waiver total. `attest.build`
    already refuses extra keys for exactly this reason.
    """
    _write(tmp_path, [bad])
    answers, complaints = advisor.load_answers(tmp_path)
    assert answers == {}
    assert any(why in c for c in complaints), complaints


def test_an_unknown_schema_version_is_refused(tmp_path):
    """`version:` was declared everywhere and read nowhere."""
    (tmp_path / ".warden").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".warden" / "catalog-answers.yaml").write_text(
        "version: 99\nanswers: []\n")
    answers, complaints = advisor.load_answers(tmp_path)
    assert answers == {}
    assert any("version" in c for c in complaints), complaints


def test_answers_file_complaints_survive_an_unreadable_ruleset(tmp_path):
    """Two loud failures, reported together rather than one at a time."""
    (tmp_path / ".warden" / "rules").mkdir(parents=True)      # empty
    (tmp_path / ".warden" / "catalog-answers.yaml").write_text("{{ not yaml")
    report = advisor.recommend(tmp_path, records=[],
                               rules_dir=tmp_path / ".warden" / "rules")
    assert report.gap_known is False
    assert any("could not be read" in x for x in report.limits), report.limits
    assert "could not be read" in advisor.render(report)


def test_a_blocking_caveat_also_fails_closed(tmp_path, monkeypatch):
    """The OTHER half of "could not look": a file or pattern that failed.

    `_MAX_SCAN_FILES` truncation is one way a measurement does not happen;
    an unreadable file or a pattern that does not compile is the other, and
    `measure()` reports both as caveats. Mutation testing found the caveat
    branch uncovered while the truncation branch was pinned — half a fix
    with a whole claim.
    """
    rules_dir = _repo(tmp_path, [{"id": "os-command-injection",
                                  "verdict": "not-applicable",
                                  "reason": REASON}])
    (tmp_path / "probe.py").write_text("x = 1\n")

    real = advisor.measure
    def _blocked(entry, root, targets):
        count, samples, caveats = real(entry, root, targets)
        return count, samples, caveats + ("probe.py unreadable (denied) — not scanned",)
    monkeypatch.setattr(advisor, "measure", _blocked)

    report = advisor.recommend(tmp_path, records=[], rules_dir=rules_dir)
    answer = next(a for a in report.answered
                  if a.entry_id == "os-command-injection")
    assert answer.state == "unmeasured", "an incomplete scan read as clean"
    assert "os-command-injection" in {r.entry_id for r in report.recommendations}


# ---------- the DECLARED ceiling: enforcement without a flag -----------------
#
# `--max-unanswered` alone is wired to no caller: answering a gap is possible
# and recorded, but nothing obliges an answer — the count can grow with
# nothing red anywhere. So the ceiling lives in
# `.warden/catalog-answers.yaml` next to the answers it governs, with its
# rationale, and `warden rules recommend` enforces it with NO flag — which is
# what lets CI run the bare command and lets the value have one source of
# truth instead of a count re-typed into a workflow file.


def _write_raw(root: Path, doc: dict) -> None:
    p = root / ".warden"
    p.mkdir(parents=True, exist_ok=True)
    (p / "catalog-answers.yaml").write_text(yaml.safe_dump(doc))


CEILING_RATIONALE = ("must not grow past the count measured when "
                     "this scratch fixture declared it; growth "
                     "demands an answer rather than absorption")


def _scratch(tmp_path, monkeypatch) -> int:
    """A scratch repo with the real ruleset; returns its unanswered count."""
    import shutil

    shutil.copytree(ROOT / ".warden" / "rules", tmp_path / ".warden" / "rules")
    shutil.copy(ROOT / "repo.yaml", tmp_path / "repo.yaml")
    monkeypatch.chdir(tmp_path)
    report = advisor.recommend(tmp_path, records=[],
                               rules_dir=tmp_path / ".warden" / "rules")
    n = len(report.recommendations)
    assert n > 1, "need a real gap for the ceiling to mean anything"
    return n


def test_a_declared_ceiling_is_enforced_with_no_flag(tmp_path, monkeypatch,
                                                     capsys):
    from warden import cli

    n = _scratch(tmp_path, monkeypatch)
    _write_raw(tmp_path, {"version": 1, "answers": [],
                          "ceiling": {"max_unanswered": n - 1,
                                      "rationale": CEILING_RATIONALE}})
    assert cli.main(["rules", "recommend"]) == 1
    assert "exceeds the declared ceiling" in capsys.readouterr().err


def test_a_declared_ceiling_at_the_count_passes(tmp_path, monkeypatch):
    from warden import cli

    n = _scratch(tmp_path, monkeypatch)
    _write_raw(tmp_path, {"version": 1, "answers": [],
                          "ceiling": {"max_unanswered": n,
                                      "rationale": CEILING_RATIONALE}})
    assert cli.main(["rules", "recommend"]) == 0


def test_the_flag_overrides_the_declared_ceiling(tmp_path, monkeypatch):
    """Explicit beats declared, in both directions — a CI caller that types a
    flag is asking for exactly that ceiling, not the file's."""
    from warden import cli

    n = _scratch(tmp_path, monkeypatch)
    _write_raw(tmp_path, {"version": 1, "answers": [],
                          "ceiling": {"max_unanswered": 0,
                                      "rationale": CEILING_RATIONALE}})
    assert cli.main(["rules", "recommend", "--max-unanswered", str(n)]) == 0
    _write_raw(tmp_path, {"version": 1, "answers": [],
                          "ceiling": {"max_unanswered": n,
                                      "rationale": CEILING_RATIONALE}})
    assert cli.main(["rules", "recommend", "--max-unanswered",
                     str(n - 1)]) == 1


@pytest.mark.parametrize("ceiling,why", [
    ({"max_unanswered": 3}, "no rationale — the value must carry its argument"),
    ({"max_unanswered": 3, "rationale": "   "}, "blank rationale"),
    ({"max_unanswered": "ten", "rationale": "r"}, "non-int value"),
    ({"max_unanswered": True, "rationale": "r"}, "bool is not a count"),
    ({"max_unanswered": -1, "rationale": "r"}, "negative ceiling"),
    ({"max_unanswered": 3, "rationale": "r", "expires": "2027"},
     "unknown key silently dropped would widen the waiver"),
    ("not-a-mapping", "scalar ceiling"),
])
def test_a_broken_ceiling_is_exit_2_never_silence(tmp_path, monkeypatch,
                                                  capsys, ceiling, why):
    """FAIL CLOSED: a ceiling that was DECLARED but cannot be read must not
    quietly become 'no ceiling' — that would let a typo disarm the one
    deterministic obligation this file carries."""
    from warden import cli

    _scratch(tmp_path, monkeypatch)
    _write_raw(tmp_path, {"version": 1, "answers": [], "ceiling": ceiling})
    assert cli.main(["rules", "recommend"]) == 2, why
    assert "ceiling" in capsys.readouterr().err.lower()


def test_a_declared_ceiling_refuses_an_unevaluable_gap(tmp_path, monkeypatch,
                                                       capsys):
    """Same contract as the flag: exit 2 when the gap was never computed."""
    import shutil

    from warden import cli

    (tmp_path / ".warden" / "rules").mkdir(parents=True)   # empty: unreadable
    shutil.copy(ROOT / "repo.yaml", tmp_path / "repo.yaml")
    monkeypatch.chdir(tmp_path)
    _write_raw(tmp_path, {"version": 1, "answers": [],
                          "ceiling": {"max_unanswered": 0,
                                      "rationale": CEILING_RATIONALE}})
    assert cli.main(["rules", "recommend"]) == 2
    assert "could not be computed" in capsys.readouterr().err


def test_the_declared_ceiling_reaches_the_artifact_and_the_report(tmp_path,
                                                                  monkeypatch):
    """Provenance: the committed artifact and the rendered report both say
    which ceiling governed the run — a reader of either learns the
    obligation exists without opening the answers file."""
    n = _scratch(tmp_path, monkeypatch)
    _write_raw(tmp_path, {"version": 1, "answers": [],
                          "ceiling": {"max_unanswered": n,
                                      "rationale": CEILING_RATIONALE}})
    report = advisor.recommend(tmp_path, records=[],
                               rules_dir=tmp_path / ".warden" / "rules")
    assert report.declared_ceiling == n
    assert report.to_dict()["declared_ceiling"] == n
    assert f"Declared ceiling: {n}" in advisor.render(report)


def test_this_repos_ceiling_is_declared_and_holds():
    """The live obligation: this repo declares a ceiling with a rationale,
    and the current gap does not exceed it. When a catalog change or a
    flipped deferral pushes the count over, THIS test goes red locally and
    `warden rules recommend` goes red in CI — answer the entry or raise the
    ceiling with a new rationale; never delete the declaration."""
    report = advisor.recommend(ROOT, records=[],
                               rules_dir=ROOT / ".warden" / "rules")
    assert report.declared_ceiling is not None, (
        f"{advisor.ANSWERS_PATH} declares no ceiling — the gate wires "
        "one; deleting it un-wires the obligation")
    assert not report.ceiling_complaints, report.ceiling_complaints
    assert len(report.recommendations) <= report.declared_ceiling, (
        f"unanswered gap {len(report.recommendations)} exceeds the declared "
        f"ceiling {report.declared_ceiling} — adopt the new entry or record "
        f"a verdict in {advisor.ANSWERS_PATH}")


def test_ci_runs_the_ceiling_on_prs_into_main():
    """The wiring is the deliverable: the GATE job runs the bare
    `rules recommend` as a blocking step. A substring check over the raw file
    would be satisfied by the line commented out, wrapped in `|| true`, given
    `continue-on-error`, handed a `--max-unanswered 9999` override, or moved
    to a job that never runs. This test parses the workflow, so each of those
    mutations removes or changes the step it asserts on.
    """
    wf = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text())
    gate = wf["jobs"]["gate"]
    steps = [s for s in gate["steps"]
             if s.get("run", "").strip() == ".warden/bin/warden rules recommend"]
    assert steps, (
        "the gate job has no step running exactly "
        "`.warden/bin/warden rules recommend` — the ceiling is declared but "
        "nothing calls it, or the command grew flags "
        "or wrappers that change what it enforces")
    step = steps[0]
    assert "continue-on-error" not in step and "if" not in step, (
        "the ceiling step is conditional or non-blocking — red that cannot "
        "fail the job is not red")
    # The job itself must actually run on PRs into main. Its own clause is the
    # event check, pinned here; the only OTHER clause it may carry is the
    # title-only fast path, which conftest declares once and checks with its
    # fail-closed `always()` half. Any third clause is a way for the ceiling
    # not to run, and is reported.
    assert "github.event_name == 'pull_request'" in str(gate.get("if", "")), (
        "the gate job's condition changed — verify the ceiling step still "
        "runs on PRs into main and update this pin deliberately")
    why = why_a_ci_job_might_not_run(
        gate, besides=("github.event_name == 'pull_request'",))
    assert why is None, f"the ceiling may not run on a PR into main: {why}"
    assert wf[True]["pull_request"]["branches"] == ["main"], (
        "the workflow's pull_request trigger changed — this test pins that "
        "the ceiling runs on PRs into main (yaml parses the `on:` key as "
        "the boolean True)")


# ---------- the loader contract holes that disarm the ceiling ---------------


@pytest.mark.parametrize("raw,why", [
    ("- ceiling:\n    max_unanswered: 0\n    rationale: strict\n",
     "a non-mapping FILE silently disarmed the declared ceiling (F1)"),
    ("version: 2\nanswers: []\nceiling:\n  max_unanswered: 0\n"
     "  rationale: a ceiling from a schema this warden does not know\n",
     "a version load_answers refuses was ENFORCED by load_ceiling (F2)"),
    ("version: 1\nanswers: []\n"
     "ceiling:\n  max_unanswered: 0\n  rationale: first strict declaration\n"
     "ceiling:\n  max_unanswered: 99\n  rationale: loose duplicate wins\n",
     "duplicate ceiling: keys were last-write-wins with no complaint (F3)"),
    ("version: 1\nanswers: []\nceiling:\n  max_unanswered: 3\n"
     "  rationale: x\n",
     "a one-letter rationale passed the non-blank check (F7) — the same "
     "substance floor an answer's reason carries applies"),
])
def test_round1_loader_holes_are_exit_2(tmp_path, monkeypatch, capsys,
                                        raw, why):
    """Each of these shapes, with a declared (or textually present) ceiling,
    must not exit 0 — that is the ceiling silently disarmed, against the
    file's own 'never silently' contract."""
    from warden import cli

    _scratch(tmp_path, monkeypatch)
    p = tmp_path / ".warden" / "catalog-answers.yaml"
    p.write_text(raw)
    assert cli.main(["rules", "recommend"]) == 2, why
    assert "ceiling" in capsys.readouterr().err.lower()


def test_an_unhashable_yaml_key_is_a_complaint_not_a_traceback(tmp_path,
                                                               monkeypatch,
                                                               capsys):
    """The duplicate-key guard's `key in seen` can raise TypeError on an
    unhashable mapping key (`? [a, b]`) BEFORE SafeLoader's own
    ConstructorError does, escaping both loaders'
    `except (yaml.YAMLError, OSError)` — a raw traceback where the contract
    promises a curated complaint. Both readers must complain cleanly."""
    from warden import cli

    _scratch(tmp_path, monkeypatch)
    (tmp_path / ".warden" / "catalog-answers.yaml").write_text(
        "version: 1\nanswers: []\n? [a, b]\n: 1\n"
        "ceiling:\n  max_unanswered: 0\n  rationale: buried under the crash\n")
    answers, complaints = advisor.load_answers(tmp_path)
    assert answers == {} and complaints, "load_answers must complain, not raise"
    assert cli.main(["rules", "recommend"]) == 2
    err = capsys.readouterr().err.lower()
    assert "ceiling" in err and "traceback" not in err
