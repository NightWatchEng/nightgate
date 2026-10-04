---
id: error-names-cause
severity: MEDIUM
engine: claude
judges: behaviour
applies_to: ["warden/**", "cage/**", "tests/**"]
# tests/** is in scope because 2 of the 5 judged records landed there (a
# _num() ValueError that aborted the run, a bare KeyError guard) — a test that
# fails uninformatively is a diagnosability defect too. NOTE the latency: this
# repo's repo.yaml context_excludes strips tests/ from the review context, so
# the tests/ scope is INERT in review today (a claude rule is only selected
# for files that survive that exclude); the two test-file records above were
# human-found, and automated tests/ coverage activates only if that exclude
# changes. Declared at the honest scope of the class rather than trimmed to
# what the current config happens to reach (the same context_excludes that
# makes tests-accompany-logic read ctx.all_files to see tests/ at all).
# Five judged candidate records, five upheld — the class is
# `diagnosability`: a failure that reports nothing actionable about its cause.
# Thin on purpose (the retro rated it the lowest-confidence of the five,
# floor 0.566), so the body draws a HARD line — flag only a message that
# names NO cause a reader can act on, never "this could be clearer" — because
# an unbounded clarity rule is the noisiest failure mode a review rule has.
# Not named `diagnosability`: a rule id equal to a candidate slug
# retroactively invalidates every shard filed under `unmapped:<slug>` (the
# LEVEL 5 to LEVEL 3 trap). `covers:` is the seam; the 5 legacy ids are
# grandfathered in .warden/certification.yaml. `diagnosability` remains a
# cross-cutting TAG in tags.yaml — a different namespace from this slug.
covers: [diagnosability]
---
When this diff adds an error path — a `raise`, a failure message, an error
return, an assertion that aborts — does the message NAME the cause a reader
can act on, or does it send them nowhere?

Five judged records, five upheld. The instances, from those records:
`warden catalog show` with no id printed "no catalog entry None" and exited,
sending the reader to hunt an entry called None instead of naming the missing
positional; a `_num()` helper raised on an unexpected word and aborted the
whole run, hiding every other mismatch; an out-of-vocabulary branch reused a
"missing fields" message, so an artifact that HAD its fields was told they
were absent; a bare `except Exception` discarded the exception object, so the
note named neither the offending input nor the reason; a guard died on a bare
`KeyError` that never said which field it required.

Flag an added error path where the reader is left without the cause:

- **The value with no input named.** A message interpolates a value ("got
  None", "invalid: 3") without saying WHICH input produced it or what was
  expected — the reader cannot tell where to look.
- **The failure that names no expectation.** An error that reports what it
  received but never what it REQUIRED — a bare `KeyError`, an `assert x` with
  no message, "config invalid" naming no field — so the reader learns the
  input was rejected but not what would satisfy it. Naming which input and
  what was expected IS the actionable cause; a prescribed remediation beyond
  that is NOT required — an error that names the input and the expectation is
  diagnosable even with no "here is the fix" line, and flagging it for that is
  the clarity slide this rule refuses.
- **The message that points the wrong way.** A default or absent value
  rendered as though it were the user's input (the `None`-hunt), or an error
  naming a symptom whose real cause is elsewhere.
- **The swallowed exception.** A bare `except`/`except Exception` that
  discards the exception object, so the failure that reaches the reader names
  neither what failed nor why (this is also a fail-closed concern; here the
  defect is that the diagnostic is gone).

Do NOT flag — the hard line the retro asked for, because "clearer" is
unbounded: a message that NAMES its cause but reads awkwardly (style is not
this rule); an internal assertion guarding an invariant the caller cannot
trigger; a `raise ... from e` or re-raise that PRESERVES the original cause; a
deliberately terse error in a hot path where the cause is obvious from one
frame up. The line is "names no cause a reader can act on", not "could be
worded better".
