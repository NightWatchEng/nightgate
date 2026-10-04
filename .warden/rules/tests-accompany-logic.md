---
id: tests-accompany-logic
severity: MEDIUM
engine: python
applies_to: ["warden/**"]
params:
  companion_prefix: "tests/"
  exempt_globs: []
# The MECHANICAL half of tests-required, promoted to a $0 checker because
# tests-required cleared the promotion bar — the retro
# proposed it at 12 judged / 12 upheld / floor 0.757, and the corpus has since
# grown to 23 judged / 23 upheld. Only the half a checker can DECIDE is
# promoted — "added logic under warden/ with no change under tests/ in the same
# diff" is a diff fact — while the half it cannot — "does a bug fix carry a
# regression test that NAMES the bug" — stays engine:claude on tests-required.
#
# The companion check reads ctx.all_files (every changed path, before
# context_excludes), NOT the scanned set: this repo's context_excludes hides
# tests/ from scanning, so the checker would never see a test file in
# ctx.files and would fire on every warden/ PR. Reading all_files lets it
# observe that tests/ moved without scanning its excluded content — the reason
# `companion_prefix` is exempt from validate_excludes (review.py
# _ALL_FILES_PARAMS). The delivery that first tried this WITHOUT all_files was
# refused by validate_excludes and reverted rather than shipped broken.
#
# Shipped MEDIUM (reports on every PR in CI at $0, does not block): the
# autonomy ladder reserves blocking promotion for an explicit person-decision,
# so HIGH is a separate founder call, and MEDIUM lets the checker earn a
# mechanical precision history first — the same non-blocking-while-it-earns
# posture the corpus-written rules take.
---
Does this diff ADD logic under `warden/` — a function definition, or an `if` /
`elif` / `for` / `while` / `with` / `try` / `except` at statement start
(the hard keywords only; `match`/`case` are soft keywords a variable can share,
so they are not matched) — without any change under `tests/` in the same diff?

The Definition of Done is that new logic carries a unit test. This is the
mechanical half of it: whether the tests tree MOVED with the logic, not
whether the test that moved actually covers it. The judgment half — a bug fix
that ships a regression test NAMING the bug it prevents — a checker cannot
decide, and stays on the `tests-required` claude rule.

The finding is a REPORT, not a verdict of neglect: a diff that adds logic and
touches no test is where the DoD is most often skipped, but a pure refactor
with existing coverage, or a change the surrounding tests already exercise,
lands here too. The checker reads ADDED LINES, not parsed code, so a logic
keyword at the start of a multi-line string body — a prompt or docstring line
that begins with `def `/`if `/`for ` — also lands here; distinguishing that
from real code needs the whole file's string state, which an added-line view
does not carry. That residual is why this rule reports (MEDIUM) rather than
blocks — the false positives are dismissed in review, not paid at the gate,
until the checker has earned a precision history of its own. Carve-outs a diff
CAN state — data files under `warden/` that carry no logic — go in
`exempt_globs`, kept in policy (this rule's params) rather than in the checker.
