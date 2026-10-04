"""`memory stats` scores each reviewer SEAT graph.yaml declares.

Rules carry a precision record; the crew seats that raise the findings did
not. The REVIEWER SEATS section reads the same folded records the rule rows
read, keyed on each record's `lens` (who RAISED it), and reports per declared
seat: n raised, the four dispositions, the Wilson floor the rule rows use, and
the share of its findings no other declared seat raised in the same round.
Read-only: no seat is promoted or paused off these rows.
"""

from pathlib import Path

from test_graph_review import _repo, _review
from warden import memory as memory_mod

HEAD, OTHER = "b" * 40, "c" * 40
TS1, TS2 = "2026-09-02T00:00:00+00:00", "2026-09-03T00:00:00+00:00"


def _rec(lens, status, *, line, rule="scope-creep", file="a.py",
         sha=HEAD, ts=TS1, rnd=1, text=None):
    r = {"rule_id": rule, "status": status, "tags": [], "file": file,
         "line": line, "sha": sha, "ts": ts, "seq": line,
         "finding": text or f"{lens} at {file}:{line}"}
    if lens is not None:
        r["lens"], r["round"] = lens, rnd
    return r


def _corpus() -> list[dict]:
    """Two rounds, hand-countable.

    Round A (TS1, HEAD): code-reviewer raises lines 1-4; cross-examiner
    raises line 1 (the same rule, file and line, in its own words) and
    line 5. Round B (TS2, OTHER): scoped-re-reviewer raises line 1 alone
    — the same location as round A's, in ANOTHER round, so it stays unique.
    Plus one record from a lens no seat declares, and one with no lens.
    """
    return [
        _rec("code-reviewer", "confirmed", line=1),
        _rec("code-reviewer", "fixed", line=2),
        _rec("code-reviewer", "refuted", line=3),
        _rec("code-reviewer", "dismissed-with-reason", line=4),
        _rec("cross-examiner", "fixed", line=1, text="examiner's own words"),
        _rec("cross-examiner", "refuted", line=5),
        _rec("scoped-re-reviewer", "confirmed", line=1, sha=OTHER, ts=TS2,
             rnd=2),
        _rec("crew:fail-closed", "fixed", line=9),
        _rec(None, "confirmed", line=10),
    ]


def _root(tmp_path: Path) -> Path:
    return _repo(tmp_path, _review())


def _rows(doc: dict) -> dict:
    return {row["seat"]: row for row in doc["seats"]["rows"]}


def test_stats_reports_one_row_per_reviewer_seat(tmp_path):
    """Every seat graph.yaml's review block declares gets exactly one row,
    in declared order, a seat that raised nothing included — a seat absent
    from the table would read as a seat that does not exist."""
    root = _root(tmp_path)
    doc = memory_mod.stats(root, records=_corpus())
    seats = doc["seats"]
    assert seats["error"] == "" and seats["declared"] is True
    assert [r["seat"] for r in seats["rows"]] == [
        "code-reviewer", "cross-examiner", "scoped-re-reviewer"]

    # a declared seat that raised nothing still has its row, at n=0
    quiet = memory_mod.stats(root, records=[_rec("code-reviewer", "fixed",
                                                 line=1)])
    assert _rows(quiet)["scoped-re-reviewer"]["n"] == 0
    assert _rows(quiet)["scoped-re-reviewer"]["unique_share"] is None

    out = memory_mod.render_stats(doc)
    assert "REVIEWER SEATS — " in out
    for seat in ("code-reviewer", "cross-examiner", "scoped-re-reviewer"):
        assert sum(1 for line in out.splitlines()
                   if line.startswith(f"  seat {seat}:")) == 1, out
    # a lens no seat declares is accounted for, never given a row
    assert "seat crew:fail-closed" not in out
    assert "1 judged record(s) raised by a lens no seat declares" in out
    assert "crew:fail-closed=1" in out
    # a finding whose first copy names no lens is counted, credited to no seat
    assert seats["unattributed"] == 1
    assert "1 judged finding(s) whose first copy names no lens" in out


def test_seat_precision_matches_a_hand_count(tmp_path):
    """code-reviewer: 4 raised — 1 confirmed, 1 fixed, 1 refuted, 1 dismissed
    — so 2 upheld of 4, and its floor is the rule rows' Wilson bound over
    exactly those two numbers."""
    doc = memory_mod.stats(_root(tmp_path), records=_corpus())
    rows = _rows(doc)
    cr = rows["code-reviewer"]
    assert (cr["n"], cr["confirmed"], cr["fixed"], cr["refuted"],
            cr["dismissed"]) == (4, 1, 1, 1, 1)
    assert cr["wilson_lb"] == round(memory_mod.wilson_lower_bound(2, 4), 3)
    xe = rows["cross-examiner"]
    assert (xe["n"], xe["fixed"], xe["refuted"]) == (2, 1, 1)
    assert xe["wilson_lb"] == round(memory_mod.wilson_lower_bound(1, 2), 3)
    sr = rows["scoped-re-reviewer"]
    assert (sr["n"], sr["confirmed"]) == (1, 1)
    out = memory_mod.render_stats(doc)
    assert (f"  seat code-reviewer: n=4 confirmed=1 fixed=1 refuted=1 "
            f"dismissed=1 wilson_lb={cr['wilson_lb']} "
            "unique=3 (75%)") in out, out


def test_unique_share_counts_findings_no_other_seat_raised(tmp_path):
    """Line 1 of round A was raised by code-reviewer AND cross-examiner, so it
    is unique to neither. Round B's line 1 is a different round and stays
    unique. A lens no seat declares does not take uniqueness away."""
    records = _corpus() + [
        # an undeclared lens on code-reviewer's line 2, same round: line 2
        # stays unique, because only declared seats compete
        _rec("crew:fail-closed", "fixed", line=2),
    ]
    root = _root(tmp_path)
    rows = _rows(memory_mod.stats(root, records=records))
    assert rows["code-reviewer"]["unique"] == 3           # lines 2, 3, 4
    assert rows["code-reviewer"]["unique_share"] == 0.75
    assert rows["cross-examiner"]["unique"] == 1          # line 5
    assert rows["cross-examiner"]["unique_share"] == 0.5
    assert rows["scoped-re-reviewer"]["unique"] == 1
    assert rows["scoped-re-reviewer"]["unique_share"] == 1.0

    # FIRES: the same location under a DIFFERENT rule is a different finding
    moved = [r if not (r.get("lens") == "cross-examiner" and r["line"] == 1)
             else {**r, "rule_id": "other-rule"} for r in _corpus()]
    rows = _rows(memory_mod.stats(root, records=moved))
    assert rows["code-reviewer"]["unique"] == 4
    assert rows["cross-examiner"]["unique"] == 2


def test_seat_rows_are_withheld_with_the_cause_when_graph_is_unreadable(
        tmp_path):
    """Fail closed: a graph.yaml warden cannot resolve yields no rows and
    says why, never an empty table that reads as 'no seat raised anything'."""
    root = _root(tmp_path)
    (root / "graph.yaml").write_text("review: [unclosed\n")
    doc = memory_mod.stats(root, records=_corpus())
    assert doc["seats"]["rows"] == [] and doc["seats"]["error"]
    out = memory_mod.render_stats(doc)
    assert "REVIEWER SEATS — " in out and "SEATS WITHHELD" in out


def test_a_repo_with_no_declared_crew_says_so(tmp_path):
    doc = memory_mod.stats(tmp_path, records=_corpus())
    assert doc["seats"] == {"declared": False, "error": "", "rows": [],
                            "undeclared": {}, "unattributed": 0}
    out = memory_mod.render_stats(doc)
    assert "declares no review crew" in out


def test_the_seat_section_leaves_every_existing_key_alone(tmp_path):
    """Additive: over one corpus, a repo with a crew and a repo with none
    compute the same document in every key but the seat section's own."""
    with_crew = memory_mod.stats(_root(tmp_path), records=_corpus())
    without = memory_mod.stats(tmp_path / "none", records=_corpus())
    # keys read off the repo itself, not off the records, differ for reasons
    # of their own (the fixture repo carries rules and a skills dir or not)
    for doc in (with_crew, without):
        for key in ("seats", "quiet_steps", "quiet_step_reviews", "tags",
                    "review_events", "dispatches", "candidates",
                    "rule_proposals"):
            doc.pop(key)
    assert with_crew == without


def test_a_restated_finding_is_credited_to_the_seat_that_first_raised_it(
        tmp_path):
    """Regression (round 1, seat-credit-follows-the-fold). The fold keeps the
    LATEST round's copy of a finding, lens included, so a round-2 re-review
    or a closure round re-filing a finding verbatim — the protocol's normal
    path — moved it out of the raising seat's row into the re-filer's. The
    seat is read off the EARLIEST copy; the disposition stays the fold's."""
    text = "the guard returns early on a missing key"
    raised = _rec("code-reviewer", "confirmed", line=1, text=text)
    refiled = _rec("scoped-re-reviewer", "fixed", line=1, text=text,
                   sha=OTHER, ts=TS2, rnd=2)
    closed = _rec("closure-attestor", "fixed", line=1, text=text,
                  sha="d" * 40, ts="2026-09-04T00:00:00+00:00", rnd=3)
    root = _root(tmp_path)
    for records in ([raised, refiled], [raised, refiled, closed],
                    [closed, refiled, raised]):
        rows = _rows(memory_mod.stats(root, records=records))
        cr = rows["code-reviewer"]
        assert (cr["n"], cr["fixed"], cr["confirmed"]) == (1, 1, 0), (
            f"the finding left the seat that raised it: {rows}")
        assert rows["scoped-re-reviewer"]["n"] == 0
        assert cr["unique"] == 1


def test_a_seat_that_raised_nothing_prints_no_floor(tmp_path):
    """Regression (round 1, zero-n-floor-reads-as-measured): at n=0 the
    Wilson bound is 0.0 by definition, which reads as the worst measured
    floor; the row prints `-` for it, as it already did for unique share."""
    doc = memory_mod.stats(_root(tmp_path), records=[
        _rec("code-reviewer", "fixed", line=1)])
    assert _rows(doc)["scoped-re-reviewer"]["wilson_lb"] is None
    out = memory_mod.render_stats(doc)
    assert ("  seat scoped-re-reviewer: n=0 confirmed=0 fixed=0 refuted=0 "
            "dismissed=0 wilson_lb=- unique=0 (-)") in out, out
