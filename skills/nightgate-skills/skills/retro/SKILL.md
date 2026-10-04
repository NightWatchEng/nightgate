---
name: retro
description: Weekly retro over the review-memory corpus for a repo enrolled on the Nightgate platform — evidence-cited proposals only. promotion to a deterministic checker requires 10+ judged cases AND a 95% confidence floor of 0.7; fast pause on refutation streaks; escapes mined as the (only) recall signal. Proposes, never edits — the founder merges every change.
---

# Retro — the org improves itself, with evidence

The incidents and reasoning behind these steps live in the Nightgate
platform's docs/design/retro-rationale.md; this page is the steps.

Weekly session (founder-triggered; may run headless and leave a report).
The retro turns the review-memory corpus into PROPOSALS. It never edits a
rule, a checker, a skill, or the graph — every accepted proposal becomes a
tracker issue and lands through a normal gated PR the founder merges.

**Standing caveat — put it verbatim at the top of every retro output:**
"Stats measure PRECISION ONLY; recall is unobservable from the review
corpus. Escapes are the recall signal and are mined separately below."

Project specifics come from `.warden/skills-policy.md` (the policy file
contract): its `## Integrations` hooks name the lenses and rituals this
project actually runs — the retro audits what exists, and proposes against
that declared baseline, not an imagined one.

## 1 · Gather (evidence before opinions)

- `.warden/skills-policy.md` — the declared org surface to audit.
- `.warden/bin/warden memory ingest`, then `.warden/bin/warden memory stats`.
  Run the ingest FIRST, every time. `stats` reads the derived, gitignored
  cache (`.warden/memory/findings.jsonl`, R-08), so a fresh clone, a fresh
  worktree, or CI before its ingest step has none — and every precision row,
  candidate, skill-recurrence row, and tag audit line then renders from an
  empty corpus as "none — guardrail holding" and `in use: 0`, while REVIEW
  EVENTS still reports the true count off the shards. Cross-check the two
  before reading anything else: an `in use: 0` tag vocabulary beside a
  nonzero review-event count means the corpus was NOT READ. An ingest that
  errors is a NOT-READ corpus too, even though a cache from an earlier run
  can leave `in use` nonzero-but-stale — stop on a failed ingest rather
  than reading stats over whatever the last run left. Report either case as
  unread, never as clean.

  **Read EVERY section, by its header, and read each line as the renderer
  states it.** The render says what a line MEANS; this table says only which
  PROPOSAL each section feeds, and the step that carries it.

  | section header | the proposal it feeds |
  | --- | --- |
  | `CORPUS` | none — it is the gate on reading anything below it: which store answered, and `STALE` when the cache lags the committed shards |
  | `REVIEW EVENTS` | none of its own — it is the review-level denominator and the refutation-bias warning that every promote and pause proposal quotes verbatim (step 3) |
  | `DECLARED RULES` | **promote**, **pause**, **reword** a rule (step 3) |
  | `CANDIDATE RULES` | **add a rule or lens** — a `PROPOSE RULE` row is a class no rule covers |
  | `SKILL RECURRENCE` | **change a skill** — a `PROPOSE SKILL CHANGE` row is a protocol that keeps producing judged findings |
  | `SKILL-STEP PROVENANCE` | **review or drop a skill step** for a `QUIET` row; a `PROVENANCE ERROR` row is a fix, not a proposal |
  | `LENSES` | **change the review topology** — outcome per lens and per round is the only committed evidence for scaling review depth, adding a lens or retiring one, and it is what `.warden/bin/warden graph validate`'s standing warnings are argued against |
  | `DISPATCHES` | **change a protocol** — a lens's `no-review` share is dispatches that did not review, and a rising one is a protocol proposal, never a precision reading. Read beside LENSES, never joined to it: these rows count ROSTER ENTRIES keyed on the roster's label, LENSES counts FINDINGS keyed on the finding's lens, and the two key spellings do not even line up |
  | `REVIEWER SEATS` | **change the crew composition** — per seat `graph.yaml` declares, the findings it first raised, their precision floor, and the share no other seat raised in the same round (a seat alone in its round reads 100% by construction, so that share compares only seats that share a round); a seat is not a rule, so nothing here promotes or pauses one, and a seat change is a `graph.yaml` proposal for the founder |
  | `TAGS` | **reconcile the tag vocabulary** (step 3) |

  Above them the header line carries the corpus size, the standing `CAVEAT:`,
  and — when nonzero — the restatement fold.

  Three readings the render cannot give you, because each is about a line
  that is ABSENT, and each has TWO causes:
  - no `<N> of <M> rounds … filed a refutation` line — a pin predating
    #186, or a corpus in which no round filed a record at all (an
    all-clean corpus still clears E-04's five events and still renders no
    bias line). Neither a pin defect nor "no round refuted anything" until
    you have checked which.
  - no `<N> restatement(s) folded` on the header — that line is silent at
    zero: an older pin (before #186), or a corpus with nothing folded.
  - a section in the table above that your run does not print — the pin
    predates it, or nothing in the corpus reaches it. Read that as
    **unknown, never as "the protocols are clean"**.

  A one-cause reading of any of them has a retro reporting a stale pin that
  is not there, and the same ruling governs all three.
- The cage ledger (`ledger.csv` in the cage dir from `cage.toml`) —
  run outcomes and skip/blocked reasons since the last retro. The
  acceptance rate is merged ÷ opened where "opened" comes from the ledger
  and "merged" from `gh pr list --state merged` on the run branch prefix
  — the ledger records runs, not review-queue merge decisions.
  No cage enrollment → skip ledger inputs, gather from PRs and bd alone,
  and say so in the report.
- The ledger's trajectory columns (`duration_s`, `attempt`) and each
  run's `loops`: outcome says whether a run landed, trajectory says what
  it cost. Ask both — "are runs getting faster", "how often does a bead
  need a second attempt", "did repair loops rise after a rule changed".
  A loop count climbing while acceptance holds steady is a warning that
  arrives before the acceptance rate moves.
- `.warden/bin/warden rules recommend` — the guardrail GAP: applicable catalog entries
  this repo does not enforce, joined against the corpus and `.warden/rules/`.
  Read the **UNANSWERED: N** headline first — applicable, unenforced, and no
  decision on record. That number is the gap, and reading it does not shrink
  it. If it reads **UNKNOWN**, the ruleset could not be read and the gap was
  never computed: report that and stop, because UNKNOWN is not zero.
  Below it, read the rows as separate lists, never one. **EVIDENCE-BASED**
  rows have judged findings here; **PRIOR ART** rows have fewer than the
  evidence threshold and rest on published sources alone. **ANSWERED** rows
  were considered and decided in `.warden/catalog-answers.yaml`; they are out
  of the count but NOT out of scope — an answer marked
  **CONTRADICTED BY THE CODE** means a `not-applicable` verdict whose starter
  has started matching; it is counted as unanswered again, and re-arguing or
  withdrawing it is a proposal. A row marked **NOT MECHANICALLY CHECKABLE**
  ships no starter, so nothing can ever refute it — those verdicts rest on
  their argument alone and are the ones worth re-reading by hand. Both belong in the report; only the first
  has earned authority, and a proposal that blurs them is worse than no
  proposal. Rows also carry a MEASURED hit count where the entry ships a
  mechanical starter — that is the noise the rule would add, on today's tree.
  Read the `caveat:` lines under a row and carry them into any proposal. A
  match count is NOT a firing count: a `scope: added` starter fires only on
  lines a diff ADDS, so its matches are places the rule COULD fire, an upper
  bound. Reporting them as dismissals-to-come would ask the founder to reject
  a guardrail on a cost nobody measured.
  **Command unavailable → say so and propose nothing from it.** An unread gap is
  reported as unread, exactly like the ledger inputs below — never as "no
  gaps found".
- `.warden/bin/warden catalog check --online` — the CURRENCY report. It
  resolves every citation, then reports CURRENT / SUPERSEDED per dated source
  by probing where the NEXT edition would live. A `SUPERSEDED` line means a
  newer edition of a cited source (CWE Top 25, OWASP Top 10) appears to
  exist — a prompt to re-triage the catalog against it, NOT a defect and NOT a
  failed check. Resolution is not currency: "all citations resolved" only
  proves the links work, because an archived edition keeps resolving after a
  newer one ships. Treat a probe that never flips as a stale probe URL to
  re-triage too. Command unavailable (older pin) → say so, propose nothing
  from it, exactly like the guardrail gap above.
- `bd list` — bug-type issues created since the last retro; note which
  touch code that passed review (escape suspects).
- `.warden/bin/warden graph validate` — standing warnings (single-verifier, unwired
  memory) are topology inputs.
- The last retro's proposals — what was accepted, what did it change?

## 2 · Mine escapes (the recall side)

An **escape** is a defect that review saw and passed: a bug fixed on main
where the causing diff went through the gate + pre-pr-review. For each
suspect: find the causing PR, confirm the defect was visible in that diff
(not introduced by later drift), then:
- comment the bead: `escape: shipped in PR #N despite review — <one line>`,
  and label it `escape`;
- cite it in the report with the rule (or missing rule/lens) that should
  have caught it.
Never blame-hunt: escapes are corpus food, not performance reviews.

`.warden/bin/warden mine` reads the GitHub side of this (merged PRs, comments, check
runs) only with a token: export `GH_TOKEN=$(gh auth token)` (or
`GITHUB_TOKEN`) first. Without one, mine is honest — it lists those classes
under NOT READ — but three of the retro's recall inputs are then never
gathered, so when the command exists, export the token rather than
reporting around the gap. Command unavailable (a
consumer on an older pin) → say so and report those classes as NOT READ,
exactly like the guardrail gap above — never mined by other means and never
as clean.

## 3 · Propose (each with citations, or not at all)

- **Promote a rule to deterministic checker** — ONLY when `memory stats`
  shows **N≥10 AND confidence floor≥0.7** for it; quote the stats line verbatim.
  Quote the two caveats beside it verbatim too, because they are about THIS
  number: the `<N> of <M> rounds ... filed a refutation` line (the rate reads
  high by the share of raised findings nobody filed) and, when the header
  carries it, `<N> restatement(s) folded` (the N is findings, not records).
  A rule below either bar is never proposed, however obvious it feels.
  The bar says WHEN; the **micro-test protocol** is the evidence the proposal
  itself carries. Run a **no-guidance CONTROL arm first** — if the control
  does not exhibit the failure there is nothing to fix: stop. Then **5+
  reps per variant**, **variance reported as a metric**, and
  **every flagged match manually read** — automated counts are never
  trusted unread. A proposal that ran no micro-test is still fileable, but
  only by saying so: its backtest artifact carries
  `validation: {status: unvalidated, reason: ...}`. Certification S-05
  refuses a promote artifact at the current rules_version that carries
  neither the evidence nor the marker.
- **Every rule-change proposal drafts its backtest artifact** —
  the JSON decision record (`rule_id`, `action`, `judged`, plus the counts
  and window it was derived from, AND `rules_version`: the version the rule
  files hash to AFTER this change — the rule file is already written, so
  `warden`'s `rules_version` can compute it before you commit) destined for
  `.warden/memory/backtests/<rule>-<action>-<date>.json`. A **promote**
  of a rule this retro proposed also drafts a promotion record,
  `.warden/memory/promotions/<rule>-<date>.json`, with `rule_id`, `backtest`
  (that artifact's file name), `"proposed_by": {"source": "retro", "date":
  "<this retro's date>"}` and `proposal` (where this report proposed it).
  Certification S-04 counts only such a record, whose artifact's
  `judged`/`upheld` clear the bar and carry `window` and `derived_from`. S-05 binds the
  evidence to the ruleset it judged: it counts a
  backtest only if its `rules_version` equals the CURRENT one, so a rule
  change that ships without a fresh backtest at the new version fails
  certification — "some change once was backtested" is not "this change is".
  Append-only: a rule change writes a NEW dated artifact naming the new
  version; never rewrite an existing one (the old ones are history, naming
  the versions they judged). The founder's merge commits it. A **promote**
  artifact additionally carries its `validation` field — the micro-test
  record (control/variant arms with reps and exhibited counts, variance,
  `flagged_matches_read: true`) or the explicit unvalidated marker with
  its reason.
- **Pause a rule** — any pause candidate (3 straight refutations, counted
  over review ROUNDS rather than findings) gets a
  pause proposal by default: pausing is cheap and reversible; a noisy rule
  taxes every review. The streak is
  restatement-aware since #186 — a refutation carried forward into a
  re-attestation is one judgment restated, not a fresh refuting round — so a
  rule that was a pause candidate on an older pin may not be one here. That
  is the intended direction and needs no remedy; say which pin the streak was
  read on rather than reporting the two as the same number.
- **Reword a rule** — cite the dismissal/refutation reasons that show how
  it is being misread.
- **Add a rule or lens** — cite the escapes it would have caught, and every
  `PROPOSE RULE` line in the candidate section: quote its tally verbatim. The
  slug names the CLASS, never the rule id — a rule id equal to its slug
  shadows the committed evidence that justified it and drops certification;
  the report prints the coordinated recipe beside every
  proposal, and it is carried into the proposal, not summarised away.
  A candidate marked `covered` is already
  answered — say which rule answers it and move on. If the answering rule's
  id differs from the slug, propose adding `covers: [<slug>]` to that rule
  file so the report stops re-proposing work that shipped — and propose the
  grandfather declaration WITH it. Retrofitting `covers:` to an existing rule
  drops E-02 exactly as naming a rule after its slug does: both are shadowing
  conditions, and the committed ids filed under that slug stop resolving the
  moment either lands. A covers-retrofit proposed on its own reproduces the
  incident this warning is about.
  **Backtest it first: a rule proposal without its history
  numbers does not ship.** Run `.warden/bin/warden rules recommend --backtest` and carry
  the row's `backtest:` line into the proposal — flagged / true-positive /
  projected-false-positive over the stated window. The projected-FP is a
  revert-COINCIDENCE ESTIMATE, not a bound: true-positive counts a flagged
  commit that a later commit reverted, which over-counts when the revert was
  unrelated to the flag and under-counts a defect fixed without a revert — so
  it can err in either direction; carry it as an estimate, never a bound or a
  precise figure. A
  `backtest: REJECTED` row — fired and caught nothing — is reported WITH its
  numbers and NOT proposed; the rejection is evidence that stops the same bad
  rule being re-proposed next cycle. A rule no regex can replay reports
  `UNBACKTESTED` with its reason — propose it on its other evidence and say
  the precision is unmeasured, never invent a number.
  The retro PROPOSES the rule from the weekly memory corpus; DRAFTING the
  actual `.warden/rules/` file — with its backtest artifact and the coordinated
  grandfather/registration a covers-bearing rule needs to hold certification —
  is `nightgate-skills:rule-advisor`'s job, the on-demand skill that works from
  full PR history plus prior art. Hand an accepted rule proposal to it rather
  than hand-writing the file here; the two overlap only on `rules recommend`.
- **Answer a guardrail row** — from `.warden/bin/warden rules recommend`, and there are
  TWO ways to answer one, both legitimate. Adopting the entry is the first.
  Recording a verdict in `.warden/catalog-answers.yaml` is the second: a
  class that genuinely cannot occur here, or one deferred behind a tracked
  bead, with the argument written down. An answer is a RECORD, not a mute —
  it needs a reason a later reader can disagree with, a deferral needs its
  bead. An entry with a mechanical starter is re-measured on each run and
  CONTRADICTED if its starter starts matching; most entries ship no starter
  and can never be refuted that way, which is why the argument carries the
  weight. Proposing an answer is
  proposing a DECISION, so it goes to the founder like any other; the retro
  never edits the file itself.
- **Adopt a missing guardrail** — the first of those two. State which
  list the row came from, because the two carry different weight and the
  founder is entitled to know which they are being asked to trust:
  - an **EVIDENCE-BASED** row cites judged findings in this repo. Quote the
    count and the classes, exactly as a candidate-rule proposal quotes its
    tally. This is the strongest proposal the retro can make, because it is
    the catalog and the corpus agreeing.
  - a **PRIOR ART** row cites published sources and NO local verdict. Say so
    in the proposal itself — "no finding here either way" — and expect a
    higher bar, because adopting it is a bet that a class which has not bitten
    this repo will. That bet is sometimes right; it is never evidence.
  Quote the row's MEASURED hit count AND its caveats, or say the cost is
  unmeasured. Three states, and they are not interchangeable:
  - **a number** — the entry ships a mechanical starter and its patterns match
    that many places today. Where it is high, propose the carve-outs with it
    or propose nothing: the catalog's own text says a rule that fires noisily
    is worse than no rule, and that the damage is not local to the rule. Read
    it as an upper bound on where the rule could fire, never as findings-to-come.
  - **unmeasured** (`null`) — no mechanical starter, so nothing was run. This
    is most `engine: claude` rows, and they are the EXPENSIVE ones: a judgment
    lens is paid on every review, forever, by every consumer. No cost signal
    is a reason for MORE scrutiny, not less, and a proposal must say "cost
    unmeasured" rather than let a blank read as zero.
  - **zero** — measured and found nothing. Different from unmeasured, and the
    proposal must not blur them.
  Never propose an entry the report marks `not-a-rule`: the catalog is saying
  no engine can check that class, and writing one anyway is the
  checklist-of-things-that-technically-pass failure that class exists to name.
- **Reconcile the tag vocabulary** — the audit lines in `memory stats` are
  the evidence to quote, and it is ALL of them: read every line the tag
  section of YOUR OWN pinned warden prints, not a list typed here. What the
  current platform prints, in one section: `no vocabulary declared`,
  `UNKNOWN`, `NEAR-DUPLICATES`, `declared but unused`, `INVALID ALIASES`, and
  then the ceiling block — `UNDECLARED AT THE DECLARATION BAR` (the RE-READ
  list: classes whose recorded `left_undeclared:` reason has been outgrown by
  its own evidence), `UNDECIDED`, one of FOUR unreadable-declaration labels,
  `CEILING BREACHED`, or the clean `drift ceiling: N undecided of M allowed`.
  The four say WHICH declaration could not be read and are not
  interchangeable: `TAG CEILING UNREADABLE` (the `ceiling:` block),
  `VOCABULARY UNREADABLE` (the whole `tags.yaml`, so whether a ceiling is
  declared cannot be known), `DECLARATION BLOCK UNREADABLE` (the `tags:` or
  `aliases:` block), `RECEIPTS UNREADABLE` (the `left_undeclared:` block).
  `.warden/bin/warden memory stats` is the authority for which of those exist, never this
  list.
  **A MISSING ceiling reading has two causes, and you cannot tell them apart
  from the report.** No `drift ceiling: N of M` line and no `CEILING BREACHED`
  means either a warden pinned before the ceiling shipped, or a CURRENT warden
  in a repo that has declared no `ceiling:` at all. Read the block precisely:
  `UNDECLARED AT THE DECLARATION BAR`, `UNDECIDED` and the four
  unreadable-declaration labels above render whether or not a ceiling is
  declared, so seeing `UNDECIDED` with no
  reading under it is the ordinary state of an undeclared repo and NOT
  evidence that your pin is current. Do not rule the absence a pin defect and
  do not rule it a clean ceiling: check `platform.pin` if you need to know
  which, and either way report the READING as unread, never as a ceiling with
  nothing to say — the same rule step 1 applies to an unread corpus. Declare an honestly-coined tag that names a real
  class; retire dead vocabulary; and when two names describe one class,
  MERGE them: the losing name becomes an `aliases:` entry in
  `.warden/memory/tags.yaml` (`test-determinism: wall-clock-dependence`).
  Never rewrite a shard — shards are committed evidence; the read seams
  fold the retired key, `unmapped:` candidate slugs included. Cite the
  split counts and, where it exists, the coining round's own judgment.
- **Change a skill** — the craft-layer half of the loop,
  and the ONLY proposal type that can act on a defect in a PROTOCOL rather
  than in code. Cite the `PROPOSE SKILL CHANGE` row from the SKILL RECURRENCE
  section: the skill file and its judged findings, quoted the way a candidate
  rule quotes its tally. The bar is the CANDIDATE bar — 3+ judged, more
  upheld than not — never the promotion bar and never a single finding: a
  protocol governs how every change is judged, so an unevidenced edit to it
  is more expensive than an unevidenced rule and cannot be measured by
  precision afterward, which is exactly why the recurrence has to be explicit
  rather than felt. Propose the specific step to add or reword and name each
  finding behind it; a `PROPOSE SKILL CHANGE` about a skill you also propose
  to DROP is a contradiction, so resolve it before writing either. Proposes
  only — the skill changes via a gated PR the founder merges, like a rule.
- **Topology changes** (graph.yaml) — add/drop a reviewer lens, wire a
  missing memory edge, split a role: cite ledger outcomes, escape
  clusters, or `graph validate` warnings. The org self-improves; the
  founder merges.
- **Drop something** — a lens or ritual that caught nothing all period is
  a candidate for removal; cite its empty yield. Cheaper org > bigger org.
- **Drop a skill step** — the craft-layer application of the same principle,
  and the one that needs the most care. A `QUIET` row from the SKILL-STEP
  PROVENANCE section names a step whose cited class has gone silent, and
  `deliver`/`pre-pr-review` are read in full on every invocation, so a dead
  step is paid for forever. But **never propose removal on quiet alone**:
  "this step caught nothing" is not proof it is useless, because a guard that
  prevents a class from ever occurring produces no findings by working. Draw
  the same line the escape miner draws — **real-zero** (the class genuinely
  cannot occur here anymore, so the guard is spent) versus **fail-open**
  (nobody exercises the step, so its silence tells you nothing) — and make
  the case in words before proposing. A quiet step with no argument either
  way stays, and is reported as still-quiet next retro. Removal is a
  proposal to the founder like any other, never an edit.

## 4 · Report and file

- Write the report to `reports/retro-<YYYYMMDD>.md` in the cage dir (no
  cage enrollment → deliver it in-session and let the beads carry the
  record): caveat first, then evidence digest, escapes, proposals (each:
  claim → citations → suggested change → cost). Keep it short enough to
  read over coffee. The previous report there is next retro's first read.
- File one tracker issue per proposal the founder accepts, linking the
  report. Rejected proposals stay in the report with the founder's reason
  — next retro reads them before re-proposing anything.

## Boundaries

- **Proposes only.** Never edits rules, checkers, skills, graph.yaml, or
  policy files — those change via gated PRs the founder merges.
- Never lowers the promotion bar (N≥10, confidence floor≥0.7) or promotes on
  narrative ("we all know this rule is good"). A candidate never enters that
  bar at all: writing a rule and automating one are different acts, and a
  candidate's counts do not carry into the rule's own promotion evidence.
- Pause proposals never delete a rule or its history — reversibility is
  the point.
- The escapes pass never edits code and never reopens merged PRs.
- If the corpus is too thin to say anything (stats N low everywhere, no
  escapes), the report says exactly that and stops — a null retro is a
  valid retro.

## Provenance

Earned steps and the incidents behind them. `as_of` is the
last date this protocol was checked against the system it describes. The
retro reads every skill's block, including its own, to name steps whose cited
class has gone quiet — a REVIEW candidate, never a removal on quiet alone.

```yaml
as_of: 2026-09-20
assumes:
  skills: [rule-advisor]
steps:
  - step: "gather the guardrail GAP (warden rules recommend), not only defects that already bit here"
    evidence: commit:d7b367ce
  - step: "read UNKNOWN as not-zero and an unread gap as unread, never as 'no gaps found'"
    evidence: commit:3697f0eb
  - step: "a guardrail proposal distinguishes unmeasured cost from a measured zero"
    evidence: commit:d7b367ce
  - step: "gather SKILL RECURRENCE — recurrence by skill file, the additive craft-layer proposal"
    evidence: commit:1e57f68a
  - step: "gather SKILL-STEP PROVENANCE and never propose a quiet step's removal on quiet alone"
    evidence: commit:990407be
  - step: "gather catalog edition currency — a SUPERSEDED source is a re-triage prompt, resolution is not currency"
    evidence: commit:44cd9343
  - step: "backtest a rule proposal over history before proposing it; a REJECTED backtest ships its numbers, never a fabricated one"
    evidence: commit:988dd3d3
  - step: "every rule-change proposal drafts a backtest naming the post-change rules_version, under the action enum the pinned warden accepts"
    evidence: commit:fc49085a
  - step: "a promotion proposal carries micro-test validation (control arm first, 5+ reps per variant, variance, flagged matches read) or is marked unvalidated with the reason"
    evidence: commit:959025ac
  - step: "read every memory stats section by its header, including LENSES and DISPATCHES, and never restate what a rendered line already says"
    evidence: commit:7fe41fe3
```
