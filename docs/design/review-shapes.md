# Review shapes — the synthesis pass over six attested rounds

Decision record for the review-shapes synthesis (#103). Six review rounds in one session produced
106 findings that reduce to five repeating shapes. This document decides, for
each shape, what mechanism can auto-reject a new instance — or records the
decision not to build one, with the reason. Per the bead: the shape question
comes before the mechanism question.

Every count in this file is a dated snapshot: derived from
`warden memory stats` over the committed corpus at main `e61f22f`
(2026-08-27) and quoted with that provenance. The counts for the three new
rules additionally live in committed backtest artifacts whose count fields
are re-derived from the shards by `tests/test_corpus_rules.py` on every run;
every other figure here (the fail-closed 39, the tag histories) is a dated
snapshot only, protected by nothing but its date stamp. That split — derive
what a test can reach, date what it cannot — is itself Finding 4's decision,
applied to this document.

## The shape decision, stated once

The recurring question the bead poses: rule, certification rung,
deterministic checker, or convention? The answer this pass settled on:

**A rule when the corpus has judged the class; a convention only when the
class resists per-diff judgment; a rung or checker only when the subject is
enumerable.** Three rounds on the answerable guardrail gap (#102) showed why: patching each
instance produced a new instance one signal over, and only collapsing the
decision into one place held. A rule is that collapse for review judgment —
one place, hashed into `rules_version`, accumulating a precision record
that can pause or promote it. A charter lens in prose binds only sessions
that are handed the prose. A checker needs an enumerable subject, and most
of these classes' subjects (claims, guards, patterns) are only enumerable
at diff time by judgment.

Scope, stated honestly: these three rules live in this repo's own
`.warden/rules/` and gate this repo. They reach no pinned consumer — the
platform ships the `warden` and `cage` packages, and every consumer points
`rules_dir` at its own rules directory. Getting these classes in front of a
consumer requires catalog entries or per-repo adoption, and that is a
separate, future act this pass does not perform.

## Finding 1 — three classes past the proposal bar with no rule

**Decision: write all three, as `engine: claude`, severity MEDIUM,
non-blocking by construction (`blocking_severities: [HIGH]`).** Landed with
this document:

| Class (candidate row, 2026-08-27) | Rule | FP cost measured before adoption |
|---|---|---|
| `enforcement-claim` — 45 judged, 42 upheld, 2 refuted, 1 dismissed, floor 0.821 | `enforcement-truth` | 6.7% non-upheld (2 refuted + 1 dismissed of 45) |
| `test-cannot-fail` — 11 judged, 11 upheld, floor 0.741 | `tests-bite` | 0 of 11 |
| `overbroad-pattern` — 10 judged, 10 upheld, floor 0.722 | `pattern-fit` | 0 of 10 |

For scale, `fail-closed` was written from 39 judged records;
`enforcement-claim` stood at 45 as the most-evidenced unruled class in the
corpus. Each rule ships with a promote backtest artifact whose counts are
re-derived from the committed shards by test, an `applies_to` set from the
class's measured directory distribution (in the backtest note), and a
`covers:` declaration plus grandfathered legacy ids — the
recipe #143 wrote down, because a rule id that equals a candidate slug
invalidates the very evidence that justified it.

Severity: MEDIUM is not caution theater — it is the adoption ladder. A
MEDIUM rule reports and cannot fail a build; it earns a precision history,
and a person promotes it to blocking only if the record shows it earned
that (10+ judged, 0.7 floor). That is the same footing `fail-closed`,
`wiki-fidelity`, and the three catalog adoptions shipped on.

One decision deliberately NOT folded in at the time (flagged by
the enforcement-claim rule's proposal): the "Enforcement truth" lens stayed in
`.warden/skills-policy.md`'s review charter even though the rule now exists.
Removing it was a protocol edit — the autonomy ladder puts
protocol edits on the human side of the line, and two sources for one
obligation was a smaller defect than an agent editing the charter under a
general grant. Filed for a later decision.

**Resolved (#171), under a founder grant scoped to that item: the
charter KEEPS the lens, reduced to a pointer at the rule.** Dropping it
entirely was the other candidate and is worse: the charter is read by a human
as the complete list of lenses `pre-pr-review` applies, and a list silently
missing one reads as a lens that was retired, not as one that was promoted.
Pointing removes the duplication that mattered — there is now one source of
the obligation, the rule file, hashed into `rules_version` and carrying the
precision record — while the charter still enumerates what a diff is judged
against. The pointer states that it does not restate the rule, which is what
keeps it from drifting back into a second copy.

## Finding 2 — the mechanism exists and does not reach the gate

Six instances, three of them in one branch of one function, found one
signal at a time across three rounds.

**Decision: the review-time mechanism is the `enforcement-truth` rule's
second shape** — "the signal that never reaches an outcome" is written into
the rule body as its own flagged shape, with that gap's instances as the cited
examples. The structural template that actually held (collapse the decision
into one place with explicit states — `advisor.answer_state`) is recorded
here as the repair convention: when this class fires, the fix is the
collapse, not a patch at the site of the symptom.

**Declined: a deterministic "declared-ceilings have callers" checker.** The
suggestion in the bead is a checker that walks documented ceilings and
asserts each has a caller. Declined because the subject is not enumerable:
"documented ceilings" is a prose set with no registry, so the checker would
either need its own hand-maintained list (which is itself a
declared-not-enforced surface, one meta-level up) or a heuristic scan whose
misses read as passes — a fail-open guard hunting fail-open guards. The
honest mechanical version is per-ceiling: each known ceiling gets a named
test asserting its wiring, which is exactly what #104 does for
`--max-unanswered`, the one known unwired ceiling on the board.

## Finding 3 — the guard is narrower than the thing it certifies

Ten instances (3 prior + 7 in-session), every in-session one inside a guard
written to close the previous instance. The corpus's name for it is
`test-cannot-fail`.

**Decision: the `tests-bite` rule**, whose body asks the mutation question
directly — for each added guard, what could change in the subject without
the guard noticing — plus the two mechanisms that already exist and stay:
the TDD discipline in `.warden/skills-policy.md` (the failing test is
watched failing before the fix) and the FIRES/SILENT pinning pattern from
`tests/test_adopted_rules.py`, which the rule body names as the deserved
treatment for any pattern that gates future diffs. One claim this pass had
to correct about itself: the wiki asserts the DoD *requires* a mutation
proof, and the DoD contains no such item — nothing can reject a guard
shipped without one. That is an enforcement-claim instance in the wiki,
found by this round's own lens and filed; PR #99's
corpus-poisoning guard governs the mutation procedure when it runs, and
nothing yet requires it to run.

## Finding 4 — counts written into prose rot, reliably

Three instances in two beads, each inside the commit correcting the
previous one; plus the "8 of 8 vs 6 of 8" evidence failure that reached the
founder.

**Declined as a rule.** A counts-in-prose rule has an unbounded
false-positive surface: this repo's beads, attestation notes, backtest
notes, and retrospectives are legitimately full of dated historical counts,
and a rule that cannot tell a dated snapshot from a live claim would fire
on the corpus's own evidence discipline. The refuted/dismissed record that
would justify the rule does not exist, and adopting it would violate this
pass's own bar (measured cost before adoption).

**The mechanism is the derive-or-date convention, enforced per artifact:**

- A count presented as **current** must be derived by a test from the
  source of truth — `test_corpus_rules.py::test_backtest_numbers_cannot_drift`
  (backtest artifacts, this pass). The wiki precedent it generalized was
  retired on 2026-09-14 with the other tests that pinned documentation
  wording. Each new current-count surface gets its own
  derivation test at the moment the count is written.
- A count that cannot be derived must be **dated and commit-stamped**, which
  turns it into a historical record that cannot rot (this document's own
  practice, and the backtest `note` fields').

Recurrence is watched, not assumed away: `wiki-fidelity` (docs claims) and
the retro's escape mining are the safety net, and if the class recurs past
them, the corpus will say so — that is what the proposal bar is for.

## Finding 5 — the self-reference trap

Four instances of a file quoting the patterns it hunts.

**Declined as a new mechanism.** The mitigation exists — per-rule
`excludes:` (`secrets-in-diff` precedent) — and the class sits at 3 judged,
2 upheld, 1 refuted (floor 0.208) under the slug `self-reference`: below
any bar this repo uses, with a live refutation. What changes with this
pass is *when* the mitigation is applied: `enforcement-truth` ships its
`.warden/rules/**` exclude on day one, pinned by
`test_enforcement_truth_excludes_rule_bodies`, instead of after the first
self-hit. `pattern-fit` deliberately does NOT exclude the rules directory —
pattern definitions are its subject, and its engine judges intent rather
than grepping strings, so the mechanical trap does not arise; the rule
frontmatter records that reasoning.

## What this pass did not do

- It did not touch `blocking_severities`, promote anything to blocking, or
  edit the certification baseline. Nothing here raises the bar on anyone.
- It did not resolve the charter lens the `enforcement-truth` rule
  duplicates; #171 later did, by reducing the charter entry to a
  pointer at the rule rather than dropping it (see Finding 1).
- It did not write rules for the classes still under the bar
  (`corpus-integrity` 8, `incomplete-rename` 6, `diagnosability` 5 at
  2026-08-27) — those keep their own tracked items and
  their own moment.
