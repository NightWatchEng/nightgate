"""`warden memory check-vocabulary` — the drift ceiling, enforced.

`.warden/memory/tags.yaml` may declare a ceiling over the tags in the
corpus carrying NO recorded disposition (neither declared, folded, nor
written down under `left_undeclared:`), and `memory ingest` and
`memory stats` REPORT a breach. Neither can enforce it: a pytest guard in the
platform repo reaches no consumer, and the ingest exit code cannot carry the
verdict, because `cage/run.sh` reads it as "was the corpus fed" and would
discard the attest shard of the very round that coined the drifting name.
Without this module a consumer could DECLARE the obligation and nothing it
was shipped could ENFORCE it (the ruling and the alternatives weighed are a
decision shard under `.warden/memory/decide/`).

This module is the enforcement. Its exit code means ONE thing, the way
`warden rules recommend` already means one thing for the guardrail-gap
ceiling:

    0  a declared ceiling holds
    1  a declared ceiling is breached — every undecided name is printed,
       with the three remedies
    2  the check could not evaluate — nothing declared, a declaration or
       receipts block that cannot be read, or an unreadable committed shard.
       NEVER folded into a verdict: a step whose only job is this check must
       not be green when there was nothing to check.

It reads the COMMITTED attest shards only, through the same
`tags.ceiling_status` definition `memory stats` renders — and with the same
INPUT the platform's own guard test reads (`records_from_shards`), so the
check and the guard cannot disagree. `stats` is one definition with a
different input: it reads the derived, gitignored cache, which lags a `git
pull` until the next ingest and carries gate shards too, so its line can
differ from this verdict. Not the cache
and not the gate shards here, because both are gitignored working state
(R-08, R-11), CI never sees them, and a verdict that depended on either
would pass locally and fail in CI or the reverse. Deliberately kept apart
from `memory.ingest` and `memory.stats`:
this is a reader with a verdict, and it changes nothing about what those
two report or exit.
"""

from pathlib import Path

from .memory import SHARD_SUBDIR, records_from_shards
from .tags import (DECLARE_MIN_N, LEFT_KEY, VOCAB_FILE,
                   alias_problems, ceiling_status, load_ceiling)

STATUS_HOLDS = "holds"
STATUS_BREACHED = "breached"
STATUS_CANNOT_EVALUATE = "cannot-evaluate"

_EXIT = {STATUS_HOLDS: 0, STATUS_BREACHED: 1, STATUS_CANNOT_EVALUATE: 2}

# The artifact the run dir carries, so the verdict can be audited after the
# fact — the unattended cage has no terminal to read.
ARTIFACT = "vocabulary-check.json"


def check(root: Path) -> dict:
    """One reading of the committed corpus against the declared ceiling.

    Returns a dict a caller can switch on (`status`) and an artifact writer
    can dump whole. NEVER RAISES for the conditions it exists to judge: an
    unreadable shard, declaration, or receipts block is a `cannot-evaluate`
    verdict carrying the complaint, not a traceback — a traceback would
    leave no artifact and read, to a CI log skimmer, like a crash rather
    than a refusal.
    """
    try:
        records = records_from_shards(root)
    except ValueError as e:
        # Silently skipping a shard deletes its records from the count, and
        # the shard most likely to matter is the one carrying the coined
        # name. records_from_shards already refuses; this names the refusal.
        return {"status": STATUS_CANNOT_EVALUATE, "ceiling": None,
                "undecided": [], "at_bar": [], "breached": False,
                "records": None, "invalid_aliases": [],
                "complaints": [f"CORPUS UNREAD — a committed shard under "
                               f".warden/memory/{SHARD_SUBDIR}/ could not be "
                               f"read, so the count would be missing its "
                               f"records: {e}"]}
    status = ceiling_status(root, records)
    invalid_aliases = alias_problems(root)
    # LABELLED BY READER, and the labeller is `tags.label_complaint` — not a
    # set-membership re-derivation here. `ceiling_status` merges complaints
    # from FOUR sources (the `ceiling:` BLOCK, the vocabulary FILE, the
    # `left_undeclared:` receipts block, the `tags:`/`aliases:` blocks) and
    # only the merge site knows which is which, so that is where the label is
    # attached. A renderer that prefixed every complaint "CEILING UNREADABLE"
    # would assert a declaration that may not exist, telling a repo with no
    # ceiling and one bad receipt that its ceiling was declared and
    # unreadable. Four sources, four labels, three renderers (this module's
    # `render_verdict`, `memory ingest`'s stderr line, and
    # `tags._render_ceiling` under `memory stats`), ONE labeller; calling the
    # FILE case "declared" asserts a declaration nobody can know exists.
    ceiling_problems = set(load_ceiling(root)[1])
    complaints = list(status["labelled"])
    if status["ceiling"] is None and not ceiling_problems:
        # Nothing declared is not nothing wrong. `rules recommend` exits 0
        # here because it is a report first and the ceiling rides along;
        # this action IS the check, and a CI step named for it that passes
        # with nothing to check is an enforcement claim.
        complaints.append(
            f"NO CEILING — .warden/memory/{VOCAB_FILE} declares no ceiling, "
            f"so there is nothing to check; declare one (`ceiling:` with "
            f"`max_undecided: N` and a `rationale:` arguing for N) or remove "
            f"this step — a check with nothing to check is not a pass")
    if complaints:
        # Fail closed even when the count is also over the line: 2 is "the
        # gate did not run", and a verdict computed from a declaration that
        # could not be read is not a verdict.
        verdict = STATUS_CANNOT_EVALUATE
    elif status["breached"]:
        verdict = STATUS_BREACHED
    else:
        verdict = STATUS_HOLDS
    return {"status": verdict, "ceiling": status["ceiling"],
            "undecided": status["undecided"], "at_bar": status["at_bar"],
            "breached": status["breached"], "records": len(records),
            # Reported, never a verdict of its own: a refused alias folds
            # nothing, so it can only ever make the count LARGER — the loud
            # direction — and the exit code stays the three the decision
            # shard names. What it must not do is stay invisible on the one
            # surface that prescribes folding as the remedy.
            "invalid_aliases": invalid_aliases,
            "complaints": complaints}


def exit_code(doc: dict) -> int:
    return _EXIT[doc["status"]]


def render_report(doc: dict) -> str:
    """The reading, for stdout — printed whether or not it is in breach.

    A ceiling nobody can see in the report is a ceiling nobody knows they
    are near, so the clean case prints the number too.
    """
    lines = ["tag vocabulary ceiling"]
    if doc["records"] is None:
        lines.append("  committed corpus: UNREAD (see the complaint)")
    else:
        lines.append(f"  committed corpus: {doc['records']} record(s) under "
                     f".warden/memory/{SHARD_SUBDIR}/")
    if doc["at_bar"]:
        # Reported, never enforced: it tells the retro which receipts have
        # been outgrown by their own evidence.
        lines.append(
            f"  UNDECLARED AT THE DECLARATION BAR ({DECLARE_MIN_N}+ judged, "
            f"upheld more often than not — RE-READ each one's recorded "
            f"reason, it may have been outgrown): " + ", ".join(doc["at_bar"]))
    if doc.get("invalid_aliases"):
        # Beside the count they failed to reduce, because that is where the
        # reader is standing when they wonder why a folded name is still
        # here. Same reading `memory stats` renders, one definition
        # (`tags.alias_problems`).
        lines.append(
            "  INVALID ALIASES (declared, refused, and folding nothing — so "
            "a name you already folded is still counted below): "
            + "; ".join(doc["invalid_aliases"]))
    if doc["undecided"]:
        lines.append(
            f"  UNDECIDED (in the corpus, with no recorded disposition — "
            f"neither declared, folded, nor written down under "
            f"'{LEFT_KEY}:'): " + ", ".join(doc["undecided"]))
    if doc["status"] == STATUS_HOLDS:
        lines.append(f"  drift ceiling: {len(doc['undecided'])} undecided of "
                     f"{doc['ceiling']} allowed")
    return "\n".join(lines) + "\n"


def render_verdict(doc: dict) -> str:
    """The verdict, for stderr — empty when the ceiling holds.

    The breach line carries the NAMES and the three remedies on one line:
    a red step that does not say which line to write is diagnosability
    debt, and a reader grepping a CI log finds one line, not a paragraph.
    """
    prefix = "warden memory check-vocabulary"
    if doc["status"] == STATUS_BREACHED:
        return (f"{prefix}: CEILING BREACHED — {len(doc['undecided'])} "
                f"tag(s) in the committed corpus have no recorded "
                f"disposition, against a declared ceiling of "
                f"{doc['ceiling']}: {', '.join(doc['undecided'])}. Declare "
                f"each in .warden/memory/{VOCAB_FILE}, fold it with an "
                f"`aliases:` entry, or record why it is left under "
                f"'{LEFT_KEY}:' — recording costs one line and decides "
                f"nothing about the class"
                + (f" (NOTE: {len(doc['invalid_aliases'])} declared alias(es) "
                   f"were refused and folded nothing — see INVALID ALIASES "
                   f"in the reading above before writing another one)"
                   if doc.get("invalid_aliases") else "") + "\n")
    if doc["status"] == STATUS_CANNOT_EVALUATE:
        return "".join(f"{prefix}: CANNOT EVALUATE — {problem}\n"
                       for problem in doc["complaints"])
    return ""
