# pre-pr-review: the reasoning behind its steps

`skills/nightgate-skills/skills/pre-pr-review/SKILL.md` is the steps. This
page is why they have the shape they do: the incidents, the measurements and
the arguments that used to sit inside the steps. It was moved here on
2026-09-27 (#330), grouped by the skill section it came from.
The narrated sentences are the skill's own words; what is not is the short
"On …" lead-in naming the step each passage belongs to, a clause restated
where a passage needed its subject back, and the citation lines. Every guard, command, refusal and boundary stayed in the skill. Where
this page and the skill disagree, the skill governs, and this page is stale.

Where the skill kept the start of a sentence and moved its tail, the tail
is here, with enough of the sentence to read on its own. A citation the skill carried
beside a step is listed under that section, by what it fixed and the PR that
shipped it.

## 1. Scope

On the convergence rule. `verification.convergence` is written once for the
whole org, so a rule like "a finding is acted on only if the examiner
confirms it" states the shape of the round that examiner is declared for —
not a promise that every round dispatches one.

## 2. One round directory, then the round's crew

On the light round's brief. Docstrings are not inert here — this platform
prints them as CLI usage text.

On `--base origin/main`. This protocol minted with `--base main` until
#194, and in the worktree that found it local main was six commits
behind `origin/main` — so every round manifest recorded a base describing a
state nobody was reviewing against, and `.warden/bin/warden round classify`'s
subtractions, whose only input is those manifests, had nothing fresh to
measure the base branch against.

On `round new`. That is one command because the path is no longer yours to
choose, and that is the fix.

On probing with `--help`. A `2>/dev/null` around the real call converts the
refusal into a silent `mktemp -d`. That is the fix disarming itself, and it
is the same idiom step 5 already uses to probe `--review-dir`.

Why a command replaced this sentence. The instruction here used to be prose —
never a fixed name, and never the shared scratchpad root — and prose has no
`exist_ok=False`. Two rounds on one machine share that root, so any
unqualified filename in it is a collision candidate, and the rule failed
three times. First (#144): a findings write failed silently and an EARLIER
round's file was still at the same path, so a well-formed CLEAN attestation
was one `git add` away from describing a different commit. Second: two
builders under one session both wrote `scratchpad/candidates.json`, and one's
judging pass read the OTHER PR's candidate list. Third (#172), after that
sentence was hardened: two builders found a FOREIGN branch's review package
in their own round, and its base sha MATCHED — so it read as plausible on
every axis anything checked. Two of four reviewers caught it by having read
the repo. Nothing mechanical did, because nothing mechanical was looking.

`range_binding` is a backstop, not the fix, and it is silent exactly when it
matters most: it warns when findings name no file the range changed, so it
fires for two builders on disjoint files and says NOTHING for two builders
touching the same area — the common case in one repo, and exactly this case.
The roster check cannot see it either: `attest write --review-dir` proves
every declared reviewer has a distinct, non-empty, non-symlink regular file
(#168), all of which a foreign report at the expected path
satisfies. Isolation at the path holds in both, which is why the mechanism is
the mint and not a smarter reader.

On naming the head, the round and the role in every report. Those are the
three coordinates of a report's identity, and `attest write` compares bytes
on two axes — the reports claimed in one round against each other, and this
round's reports against every sibling round's whatever head it was minted
for — which between them admit three shapes of honest collision, one per
coordinate. `judged` alone separates only different heads, so it leaves two
HONEST states colliding — the two reviewers of one round on a clean PR, and a
round re-minted at the same head — and each would be refused as a copy.
Named all three, an honest report is distinguishable and only a genuine copy
collides (#182).

On `unmapped:`. `unmapped:` is not a dumping ground and not a workaround — it
says "reviewers keep finding something no rule covers", which is the
strongest input the rule advisor has.

On never pre-judging a finding. The examiner exists to kill false positives
AFTER they are reported; a finding suppressed in the prompt never reaches the
examiner, the corpus, or the founder, and a corpus fed by curated reviews is
quietly poisoned at exactly the seam every PR feeds.

## 3. The judging pass

Citation: a judge's report naming its head, round and role, so an empty
judging pass is not refused as a carry-forward — #182.

The package is shared ground truth (both passes judge the same bytes); the
isolation this step protects is the finder's rationale, not the diff.

## 4. Triage and fix

Citations: attesting before the repair commit — #191; the boundary's
age read by `progress show` — #201; filing every refuted candidate —
#186, which is also "this protocol's own rule" the undeclared-lens
paragraph invokes.

Why every refuted candidate is filed. The attestation is written AFTER
adjudication, so this is the only moment a refutation can reach the corpus;
a refuted candidate left out here is not "no finding", it is a finding the
corpus cannot see. That matters for the number the platform hangs promotion
on: `memory stats` computes a rule's precision over the records FILED, so if
only survivors are filed the rate measures what a refute-first arbiter let
through, not what the rule raised — biased high by the arbiter's kill rate
(measured at roughly a third of what the lenses raise). Filing the refutation
is what makes "this rule rarely fires falsely" distinguishable from "this
rule fires falsely a lot and someone catches it every time", and the two have
opposite implications for promoting the rule to a blocking severity. The
examiner's own memory feed reads these records back as precedents, so a
refuted class is exactly what the next round is told not to re-litigate.

Why a scoped-out candidate is not filed. Every status the payload can carry
is a JUDGED status: `memory stats` bumps the rule's `n` and computes its
floor over `confirmed + fixed`, so filing a scoped-out candidate as
`dismissed-with-reason` scores it as a wrong call by the rule that raised it.
A duplicate is the sharp case — two lenses raising one real defect become
n=2 / upheld=1, 50% precision on a finding the round got RIGHT twice, and the
fold never merges two records one round filed, so it is permanent. That is
the opposite bias from the one this section exists to remove, and it lands
hardest on a candidate near the bar (evidence-attribution sat at 9 judged / floor
0.701 — one lens-overlap dismissal would have pushed it further from a
promotion nobody thought it had failed). A refutation is a judgment of the
rule and belongs in the corpus; "this was never this rule's question" is
not, and putting it there is not visibility, it is a wrong number
(#186, review round 1, C03/C12).

On a finding raised by an undeclared lens. It happens: a session reads a
convergence rule as a roster (step 1), dispatches a role the round's crew
does not name, and the role comes back with a real defect. And the cost of a
confirmed defect that exists only in a session transcript is not
hypothetical: it has been paid once, by a round-2 attribution finding that
could be filed in no round-2 payload at all and had to be carried to a later
round to be fixed.

## 5. Attest

On a payload that files every judged candidate. A candidate the examiner
scoped out has no entry, for the reason step 4 gives. A payload that files
only survivors attests to a review that judged fewer findings than it did,
and `memory stats` prints how many rounds filed a refutation so the gap is
visible beside the precision numbers it biases (#186).

On lens and round attribution (#184). Before this the corpus carried
22 spellings for about 7 roles and 0 of 979 records said which reviewer
raised them, so "how often does each lens's finding survive" could not be
answered from the committed evidence at all.

Citation: an unminted round's repair commit read as the ORIGINAL diff —
#191, found on PR #190.

On the closure round. A branch that committed a repair at the cap has no
attestable head at all and its gate is red forever — that was found live on
PR #266, and it is why the default is still "the round at the
cap makes no repair commit".

On one attestation per round. The attestation schema's `findings[].round`
description says the same thing in the same words since #194 — it
read "the round this finding first arrived in" until then, so for a carried
finding the schema and this protocol diverged, and a guard now reads that
description the way
`test_the_schema_does_not_carry_the_instruction_the_ruling_retired` reads
the `reviewers[].round` one. Two instructions used to govern this, and they
were jointly unsatisfiable for any review of more than one round, because
warden enforces both halves: one had an earlier round's report be present in
the FINAL round's `reviewers/`, where the roster check wants it, and the
other refuses it there by byte identity (#191). The refusal is the
half that stands, because the other half is the one no mechanism can tell
from a fabrication — a duplicated report is a distinct non-empty file, which
is all `verify_roster` asks — so blessing it would have reopened the copied-forward
report (#182) by protocol.

On re-filing an open finding VERBATIM. That is what makes the copies a
RESTATEMENT the corpus folds into one finding: `fold_restatements` groups on
exactly those four, so a re-raise written in the re-confirming lens's own
words is recurrence instead, and it inflates the raising rule's `n` and the
Wilson floor a promotion is gated on (#186).

On the round-2 restatement refusal (#278). Found live closing
the closure-round work (#272), where a round-2 payload filed verdict text and the break
surfaced three rounds later at the closure round, with the remedy being to
re-file everything the whole chain had left open.

What this costs, stated rather than left to be discovered. `memory` keys a
round on its shard's timestamp and head sha, premising one attestation per
round — which was not true while one attestation covered N rounds: it read
those N as ONE and under-counted every round-based streak. Per-round
attestation makes that premise true, and moves BOTH round-keyed readings, in
opposite directions: the refutation streak behind `.warden/bin/warden
autonomy pause` (a machine tier that opens a PR a human merges, never a
merge) reaches its trigger in fewer reviews, and the suppressor weight that
blocks a retro proposal is computed over more, smaller rounds, so it can FALL
where the whole review used to count once. Nothing has measured either on
this corpus; the measurement is tracked, and until it lands neither
direction is claimed as safe here.

On attesting each round before the next commit. A round left unattested when
the repair commit lands can never be attested at all — that is how PR #190
lost ten of its eleven rounds, permanently.

On the order of the last two steps. Budget a round for it rather than
discovering it in CI: 16 of the 39 PRs merged before this shipped would have
been refused, 11 of them for merging the base branch at the end.

On parking. That status has a corpus cost, and it points the OTHER way from
the re-raise's: `dismissed-with-reason` lands in `memory`'s `dismissed`
bucket, so a parked finding — a defect the review UPHELD and handed to a
tracked item — still counts in the raising rule's `n` while sitting outside
the numerator of the precision floor a promotion is gated on, and it becomes
examiner case law on recall. Nothing has measured that either; it is
tracked beside the round-key effect.

Citation: a REFUTED finding attributed exactly like an upheld one —
#186.

On the roster being TRUE (#168). Free text is not checkable, which is
exactly how a shard once named a fail-closed reviewer that never ran, one
commit from permanent false provenance.

On `no-review`. Until warden derived `outcome`, a reviewer that returned no
review was counted `returned: true, findings: 0`: zero-yield, the very
denominator the review-depth ledger rests on.

On a round not surviving a commit. That cost was measured before it was accepted
(#182, ruled by `.warden/bin/warden decide record` with the count,
corrected once): over the 132 committed attestation shards, 4 were
re-attestations of an earlier round on the same branch line: 3 of the 4
carried a change under `tests/**` on the branch itself — reviewed surface,
and the class that ruling's own hazard note names (a test fixture *is*
reviewed; PR #172's CI red was in exactly that class) — and 1 of the 4 was
forced by a merge of `origin/main` alone, whose branch-side diff was the
attestation shard and nothing else. The refusal stands unnarrowed.

On never copying a report forward (#182). PR #172 carried round 1's
`code-reviewer.json` unchanged through rounds 2-4, and the roster check was
satisfied by every copy, because a copy is a distinct non-empty file.

## Boundaries

On linting the PR title. Two PRs were refused by CI for this on one day with
every commit message clean, and a multi-bead PR is where it
bites: the 100 fits at most two item ids before any prose.
