"""Ledger analysis: did declaring build disciplines change the trajectory?

The ledger already carries duration_s and attempt per run. This turns
those columns into the two numbers the question actually needs — repair
loops and first-pass gate rate — split at the adoption date.
"""

from cage.measure import HEADER, parse, split_at, summarize

# Outcome vocabulary is run.sh's real one: done / timeout / usage-limit /
# push-denied / failed(N) / FORBIDDEN-PATH / skipped / usage-skipped — plus whatever .run-summary
# declares. Never invent a value here; a metric keyed to a word the runner
# does not emit reads 0% forever and looks like a finding.
HEADER_FREE_ROWS = [
    # date, bead, outcome, pr, note, node, duration_s, attempt
    "2026-08-01,x-1,done,10,,builder,900,1",
    "2026-08-02,x-2,done,11,,builder,1200,3",
    "2026-08-03,x-3,failed(1),,no root cause,builder,600,3",
    "2026-08-20,x-4,done,12,,builder,700,1",
    "2026-08-21,x-5,done,13,,builder,650,1",
]


def test_summarize_reports_loops_and_first_pass_rate():
    s = summarize(HEADER_FREE_ROWS)
    assert s["runs"] == 5
    assert s["mean_attempts"] == 1.8          # (1+3+3+1+1)/5
    assert s["first_pass_rate"] == 0.6        # 3 of 5 landed on attempt 1
    assert s["landed_rate"] == 0.8            # 4 of 5 opened a PR


def test_landing_is_keyed_to_a_pr_not_to_an_outcome_word():
    """The PR column is vocabulary-independent. Outcome strings drift with
    the runner and with whatever .run-summary declares; a PR number does
    not. A run that opened a PR landed, whatever it called itself."""
    rows = ["2026-08-01,x,some-new-word,77,,builder,100,1"]
    assert summarize(rows)["landed_rate"] == 1.0


def test_skipped_runs_do_not_dilute_the_repair_statistics():
    """A run with no eligible bead attempted nothing. Counting it as a
    first-pass success inflates the rate; counting it as a failure deflates
    it. It belongs in neither — only in its own count."""
    rows = HEADER_FREE_ROWS + [
        "2026-08-22,none,skipped,,no eligible bead,,0,1"]
    s = summarize(rows)
    assert s["skipped"] == 1
    assert s["runs"] == 5, "skipped runs must not count as worked runs"
    assert s["first_pass_rate"] == 0.6, "unchanged by a skipped run"


def test_a_usage_skipped_run_does_not_dilute_the_repair_statistics():
    """The usage pre-flight skips a run before any session starts, so it
    attempted nothing, exactly like a `skipped` row."""
    rows = HEADER_FREE_ROWS + [
        "2026-08-22,none,usage-skipped,,usage exhausted — resets 1:30am,,0,1"]
    s = summarize(rows)
    assert (s["skipped"], s["runs"]) == (1, 5)
    assert s["first_pass_rate"] == 0.6


def test_a_run_id_column_does_not_break_the_readers():
    """run.sh appends `run_id` as column 9 so two runs on the
    same date are still tellable apart. The readers here index 0-7, so a row
    carrying it must summarize and split IDENTICALLY — that append-only
    contract is the whole reason the runner may grow the ledger at all.
    """
    with_run_id = [f"{row},20260801-233000" for row in HEADER_FREE_ROWS]
    assert summarize(with_run_id) == summarize(HEADER_FREE_ROWS)
    before, after = split_at(with_run_id, "2026-08-19")
    assert len(before) == 3 and len(after) == 2


def test_two_runs_in_one_day_are_two_rows():
    """One ledger row = one RUN, never one day. RUN_ID makes a second
    same-day run possible; the measure must count both rows and
    never collapse them by date."""
    rows = [
        "2026-08-24,b-1,done,41,,builder,3600,1,20260824-013000",
        "2026-08-24,b-2,done,42,,builder,1800,2,20260824-190000",
    ]
    s = summarize(rows)
    assert s["runs"] == 2
    assert s["landed_rate"] == 1.0
    assert s["first_pass_rate"] == 0.5


def test_split_at_separates_the_two_arms():
    before, after = split_at(HEADER_FREE_ROWS, "2026-08-19")
    assert len(before) == 3 and len(after) == 2
    assert summarize(after)["first_pass_rate"] == 1.0


def test_empty_arm_reports_no_data_rather_than_zero():
    """An empty 'after' arm must not read as a 0.0 result — that is the
    difference between 'it did not help' and 'we have not measured yet'."""
    s = summarize([])
    assert s["runs"] == 0
    assert s["first_pass_rate"] is None
    assert s["mean_attempts"] is None
    assert s["landed_rate"] is None


def test_malformed_rows_are_counted_by_line_not_fatal():
    s = summarize(HEADER_FREE_ROWS + ["garbage", "", "a,b,c"])
    assert s["runs"] == 5, "short rows must not be counted as runs"
    assert s["malformed"] == [6, 8], "a dropped row is named, never silent"


def test_a_header_row_is_skipped_not_counted_or_malformed():
    s = summarize([",".join(HEADER)] + HEADER_FREE_ROWS)
    assert s == summarize(HEADER_FREE_ROWS)
    before, after = split_at([",".join(HEADER)] + HEADER_FREE_ROWS, "2026-08-19")
    assert len(before) == 3 and len(after) == 2


def test_a_multi_line_quoted_note_is_one_row():
    """A quoted note may carry commas and newlines; the csv reader takes the
    record whole, and split_at hands it on still one record."""
    rows = [",".join(HEADER),
            '2026-08-20,x-4,done,12,"refused, twice:',
            '  line two",builder,700,1,20260820-010000'] + HEADER_FREE_ROWS[4:]
    parsed, bad = parse(rows)
    assert not bad and len(parsed) == 2
    assert parsed[0][4] == "refused, twice:\n  line two"
    assert parsed[0][6:] == ["700", "1", "20260820-010000"]
    before, after = split_at(rows, "2026-08-19")
    assert before == [] and summarize(after)["runs"] == 2


def test_a_legacy_unquoted_multi_line_note_is_named_not_misread():
    """The pre-header shape: an unquoted note whose newlines split one run
    into three physical lines. None of them is a run; all three are named."""
    rows = ["2026-09-20,x-1,FORBIDDEN-PATH,,refused: outside",
            "  a.json: outside the carve-out",
            "  b.json: outside the carve-out,builder,1620,1,20260920-205434",
            ] + HEADER_FREE_ROWS
    s = summarize(rows)
    assert s["runs"] == 5 and s["malformed"] == [1, 2, 3]


def test_a_stray_quote_that_fuses_rows_names_every_line_it_swallowed():
    """Round-1 regression: a '"' opening an unquoted field pairs with the
    next quote the reader meets, fusing runs into one record. Every line it
    spans is named, never counted as one run and never silently."""
    fused = ['2026-09-27,x-1,done,10,"first",builder,100,1,r1'.replace(
                 "builder", '"builder'),
             '2026-09-27,x-2,done,12,"second",builder,100,1,r2']
    rows, bad = parse(fused + HEADER_FREE_ROWS)
    assert bad == [1, 2] and len(rows) == 5
    legacy = ['2026-08-01,x,failed,,"opens a quote; never closed,b,1,1,r'
              ] + HEADER_FREE_ROWS
    rows, bad = parse(legacy)
    assert rows == [] and bad == [1, 2, 3, 4, 5, 6]


def test_a_newline_outside_the_note_alone_marks_a_fused_record():
    """Round-2 regression: eight-column rows, so the fused record has nine
    fields and the column cap cannot see it, and the swallowed text sits in
    the NODE, so the row-start check cannot either. Only the newline check."""
    rows, bad = parse(['2026-08-01,x,done,1,n,"builder,1,1',
                       '2026-08-02,y,done,2,say "hi" ok,b,1,1',
                       '2026-08-03,z,done,3,fine,b,1,1'])
    assert bad == [1, 2] and [r[1] for r in rows] == ["z"]


def test_more_columns_than_the_header_alone_marks_a_row_malformed():
    rows, bad = parse(["2026-08-01,x,done,1,n,b,1,1,r,extra"] + HEADER_FREE_ROWS)
    assert bad == [1] and len(rows) == 5


def test_a_note_that_swallowed_a_row_start_is_malformed():
    rows, bad = parse(['2026-08-01,x,done,1,"a\n2026-08-02,y,done,2,b",n,1,1,r'])
    assert rows == [] and bad == [1, 2]


def test_a_row_the_csv_module_rejects_is_named_not_raised():
    huge = "2026-08-01,x,done,1," + "n" * 200_000 + ",builder,1,1"
    rows, bad = parse([huge] + HEADER_FREE_ROWS)
    assert bad == [1] and len(rows) == 5
    # a rejected QUOTED field spanning lines names every line it spans
    huge = '2026-08-01,x,done,1,"short\n' + "n" * 200_000 + '",builder,1,1'
    rows, bad = parse(huge.split("\n") + HEADER_FREE_ROWS)
    assert bad == [1, 2] and len(rows) == 5


def test_cli_names_malformed_rows(tmp_path, capsys):
    from cage.cli import main
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("\n".join(["2026-08-01,x", *HEADER_FREE_ROWS]) + "\n")
    assert main(["measure", str(ledger)]) == 0
    out = capsys.readouterr().out
    assert "1 malformed rows not counted, at line 1" in out


def test_cli_refuses_to_compare_against_an_empty_arm(tmp_path, capsys):
    """The failure mode this guards: reporting a 0% 'after' rate from zero
    runs, which reads as 'the disciplines did not help'."""
    from cage.cli import main
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("\n".join(HEADER_FREE_ROWS[:3]) + "\n")
    assert main(["measure", str(ledger), "--since", "2026-08-19"]) == 0
    out = capsys.readouterr().out
    assert "NO DATA" in out and "not the same as no effect" in out
    assert "no comparison" in out


def test_cli_flags_small_samples_when_it_does_compare(tmp_path, capsys):
    from cage.cli import main
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("\n".join(HEADER_FREE_ROWS) + "\n")
    assert main(["measure", str(ledger), "--since", "2026-08-19"]) == 0
    out = capsys.readouterr().out
    assert "first-pass delta" in out
    assert "CAUTION" in out and "not controlled for" in out


def test_a_push_denied_run_is_a_worked_run_that_did_not_land():
    """push-denied is finished work the profile kept from becoming a PR: it
    counts as a run, never as skipped, and never as landed."""
    rows = HEADER_FREE_ROWS + [
        "2026-08-23,x-6,push-denied,,push denied: git push -u origin auto/x,builder,4860,2"]
    s = summarize(rows)
    assert (s["runs"], s["skipped"]) == (6, 0)
    assert s["landed_rate"] == round(4 / 6, 3)
