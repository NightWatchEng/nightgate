---
id: tests-required
severity: MEDIUM
engine: claude
judges: behaviour
applies_to: ["warden/**"]
implements: ["review-lens-tests"]
# The MECHANICAL half of this rule — added logic under warden/ with no tests/
# change in the same diff — was promoted to the engine:python checker
# `tests-accompany-logic`, which runs at $0 on every PR.
# What remains here is the half a checker CANNOT decide: whether a bug fix
# carries a regression test that NAMES the bug it prevents. Do not re-add the
# diff-fact half — that would double-report it against the checker.
---
Definition of Done, the judgment half: a bug found in review or a live run
gets a NAMED regression test — one that would fail if that exact bug returned.

Flag a fix whose diff repairs a described defect but adds no test, or adds a
test that does not actually pin the bug (asserts around it, mocks the unit
under repair, or names no failing input). The presence of *a* tests/ change is
not enough — the mechanical checker already reports its absence; this lens
judges whether the test that shipped would have CAUGHT the bug.

Do NOT flag a change that fixes nothing (a feature, a refactor), or a fix
whose regression test is present and names the failing case. The
"added logic without any tests/ change" shape is the checker's job now, not
this rule's — leave it to `tests-accompany-logic`.
