---
id: swallowed-exceptions
severity: MEDIUM
engine: declarative
# Shipped code only, the unsafe-deserialization precedent — and here the
# adoption explicitly weighed blanket-excluding tests/** and
# the adjudication answered it: both real test hits are the assert-raises
# idiom (`raise AssertionError(...)` then `except X: pass`), a legitimate
# shape the catalog itself names, and the remaining tests/ matches are
# fixture STRINGS proving starters fire (test_catalog_starters.py). tests/**
# stays under fail-closed's judgment coverage, which is what actually caught
# the 5-of-39 fail-open records there.
applies_to: ["warden/**/*.py", "cage/**/*.py"]
# fail-closed (engine: claude) also implements this catalog entry: it judges
# every swallow-SHAPE a regex cannot see. This rule is the $0 deterministic
# floor for the one spelling that recurs; the two agree because every
# adjudicated carve-out is a reasoned warden:allow marker both can read.
implements: ["swallowed-exceptions"]
checks:
  # The adoption review found `except X:  # transient` + pass escaping —
  # a throwaway trailing comment was an unreasoned waiver strictly cheaper
  # than the sanctioned marker. The pattern now crosses trailing and
  # interposed comment lines; a handler that DOES anything besides pass
  # still does not match. The catalog starter carries the same fix.
  - id: except-pass
    pattern: 'except[^:\n]*:[ \t]*(?:#[^\n]*)?(?:\n[ \t]*(?:#[^\n]*\n[ \t]*)*)?pass\b'
    message: "exception swallowed — an errored check must not look like a pass"
    scope: file
    flags: [m]
---
Failures caught and discarded, so a check that errored is indistinguishable
from one that passed (OWASP Top 10:2025 A10; this corpus's `fail-open`, 38
judged records).

Adopted by the #105 adjudication at **four measured hits** in
shipped code, each examined and **kept** under a reasoned
`warden:allow(except-pass)` marker at its line — the verdicts live next to
the code, not here: the `latest` convenience symlink that is never
load-bearing (`warden/runs.py`); a git-detection walk whose documented job
is answering when git itself is broken (`warden/memory.py`); and two scan
paths whose failure direction is MORE scanning, never less
(`warden/advisor.py`). Zero unallowed hits at adoption.

**Dismissing a true match.** If the swallow is genuinely deliberate, the fix
is a `warden:allow(except-pass): <reason>` marker on or above the `except`
line — the reason is the record. A bare marker suppresses nothing, and a
swallow you cannot write one honest line for is a finding, not a carve-out.

**What this rule does NOT cover.** Only the `except ...: pass` spelling.
`except X: continue`, `return None` on failure, a caught-and-logged-at-debug
error, and every other fail-open shape stay with the fail-closed rule's
judgment — this is the mechanical floor, not the ceiling.

MEDIUM is deliberate: `blocking_severities: [HIGH]`, so this reports without
blocking while it earns a precision history.
