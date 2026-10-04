---
id: tests-required
severity: MEDIUM
engine: claude
applies_to: ["hello_svc/**"]
---
Definition of Done: new logic carries unit tests; bugs found live or in review
get a NAMED regression test.

Flag when this diff:
- Adds a new function or branching logic under `hello_svc/` with NO
  corresponding change under `tests/` in the same diff.
- Is clearly a bug fix (changes wrong behavior) but adds no regression test
  naming or covering the bug.

Do NOT flag: pure refactors with existing coverage, docstring/comment-only
changes, config or docs changes, or trivial one-liners.

Evidence: name the new/changed symbol and note that `tests/` has no matching
change.
