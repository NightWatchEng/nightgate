"""Turn the run ledger into the two numbers that answer "did it help".

One ledger row = one RUN, never one day — RUN_ID made a second same-day
run possible, and collapsing rows by date would double-count nothing and
undercount everything. The `attempt` column is the honest proxy for repair
loops: a run that shipped on attempt 1 needed no repair round; attempt 3
exhausted the budget. `outcome` says whether it landed at all. Everything here is
arithmetic over those two columns — deliberately so, because a metric an
agent can reason its way around is not a metric.
"""

from __future__ import annotations

import csv
import io
import re

# ledger row: date,bead,outcome,pr,note,node,duration_s,attempt,run_id
# run_id (column 9) is APPENDED, so every index below is unmoved and
# _MIN_COLS stays 8 — a row without it is still well-formed here.
_DATE, _OUTCOME, _PR, _NOTE, _DURATION, _ATTEMPT = 0, 2, 3, 4, 6, 7

# A run that found no eligible bead attempted nothing. It belongs in
# neither numerator nor denominator of a repair statistic — only in its
# own count.
_NOT_WORKED = ("skipped", "usage-skipped")   # the usage pre-flight starts no session
_MIN_COLS = 8
_ROW_START = re.compile(r"\n\d{4}-\d{2}-\d{2},")
# run.sh writes this once, into an absent or empty ledger; a ledger older
# than it has none, and reads the same either way.
HEADER = ("date", "bead", "outcome", "pr", "note", "node", "duration_s",
          "attempt", "run_id")


def parse(raw: list[str]) -> tuple[list[list[str]], list[int]]:
    """(well-formed rows, every physical line of each malformed record).

    Read with the csv module, so a quoted note may carry commas and even
    newlines. The header row is skipped, never counted. A record shorter
    than eight columns, or with no date, is older-format or corrupt — an
    unquoted multi-line note from a pre-header ledger splits into several
    such — and is RETURNED by line, never silently dropped: counting it
    would understate attempts, and hiding it would understate the ledger.
    """
    text = "\n".join(line.rstrip("\r\n") for line in raw)
    reader = csv.reader(io.StringIO(text))
    rows, malformed, start = [], [], 1
    while True:
        try:
            rec = next(reader)
        except StopIteration:
            break
        except csv.Error:          # e.g. a field over the csv size limit
            malformed.extend(range(start, reader.line_num + 1))
            start = reader.line_num + 1
            continue
        first, start = start, reader.line_num + 1
        blank = len(rec) <= 1 and not "".join(rec).strip()
        if blank or rec[:1] == [HEADER[0]]:
            continue
        # Only the note may span lines, and never across a row start. A
        # newline in any other field, a note holding a line that opens with
        # a date, or more columns than the header is a stray quote that
        # swallowed the rows after it: EVERY physical line it spans is named.
        spans = any("\n" in f for i, f in enumerate(rec) if i != _NOTE) or (
            len(rec) > _NOTE and _ROW_START.search(rec[_NOTE]) is not None)
        if (len(rec) >= _MIN_COLS and len(rec) <= len(HEADER)
                and rec[_DATE].strip() and not spans):
            rows.append(rec)
        else:
            malformed.extend(range(first, reader.line_num + 1))
    return rows, malformed


def malformed_note(lines: list[int], shown: int = 10) -> str:
    """Name the malformed records by line — the first SHOWN, then a count."""
    named = ", ".join(map(str, lines[:shown]))
    more = f" and {len(lines) - shown} more" if len(lines) > shown else ""
    return f"{len(lines)} malformed rows not counted, at line {named}{more}"


def _line(row: list[str]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(row)
    return buf.getvalue()


def _int(value: str, default: int = 1) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def summarize(raw: list[str]) -> dict:
    """Loop and landing statistics for one arm.

    An empty arm returns None for every rate, never 0.0 — "not measured"
    and "measured as zero" are different claims and must not look alike.
    """
    all_rows, malformed = parse(raw)
    skipped = [r for r in all_rows if r[_OUTCOME] in _NOT_WORKED]
    rows = [r for r in all_rows if r[_OUTCOME] not in _NOT_WORKED]
    n = len(rows)
    if not n:
        return {"runs": 0, "skipped": len(skipped), "mean_attempts": None,
                "first_pass_rate": None, "landed_rate": None,
                "mean_duration_s": None, "malformed": malformed}

    attempts = [_int(r[_ATTEMPT]) for r in rows]
    # Landing is keyed to a PR number, not to an outcome word: the runner's
    # vocabulary drifts (and .run-summary can override it outright), but a
    # PR number means a PR was opened, whatever the run called itself.
    landed = [r for r in rows if r[_PR].strip()]
    first_pass = [r for r in rows if _int(r[_ATTEMPT]) == 1]
    durations = [_int(r[_DURATION], 0) for r in rows if _int(r[_DURATION], 0) > 0]

    return {
        "runs": n,
        "skipped": len(skipped),
        "mean_attempts": round(sum(attempts) / n, 3),
        "first_pass_rate": round(len(first_pass) / n, 3),
        "landed_rate": round(len(landed) / n, 3),
        "mean_duration_s": round(sum(durations) / len(durations)) if durations else None,
        "malformed": malformed,
    }


def split_at(raw: list[str], date: str) -> tuple[list[str], list[str]]:
    """Split well-formed records into (before, on-or-after) by ISO date.

    Each side holds one CSV record per entry, re-quoted, so it feeds
    `summarize` as it is. Malformed records have no date to split on; the
    caller reports them from `parse` over the whole ledger. Lexical
    comparison is correct for YYYY-MM-DD and needs no date parsing.
    """
    before, after = [], []
    for row in parse(raw)[0]:
        (after if row[_DATE] >= date else before).append(_line(row))
    return before, after
