# Skills policy — nightgate

The platform is its own consumer: it ships this contract, so it writes one.
(`warden certify` scored the repo Level 2 for exactly this omission — the
ladder caught its author.)

Which warden a skill runs is resolved by the skill, not stated here: its
`## Which warden` step takes this repo's `.warden/bin/warden` launcher (it
runs the package this checkout contains), else `warden` on PATH when its
`--version` is `repo.yaml`'s `platform.pin`, and refuses otherwise. Every
`warden` command this file names means the one that step resolved; a skill
still spelling the launcher outright runs only where one exists, as here.

## Verify

- Any change: `uv run pytest -q -n auto` and
  `uv run --locked --with ruff==0.16.5 ruff check warden cage tests --select F,E9`.
  This quotation is prose an agent reads and COPIES; nothing executes it and
  no test reads it. The three EXECUTED writings of the same command —
  `repo.yaml`'s `verify: tests` scope, `.github/workflows/ci.yml` and
  `.githooks/pre-push` — are held in lockstep by
  `tests/test_local_gate.py::test_the_ruff_pin_is_the_same_in_all_three_executed_callers`
  for the ruff version and by
  `tests/test_local_gate.py::test_the_gate_runs_the_suite_in_parallel_and_nothing_defaults_to_it`
  for `-n auto`; when they move, this line and `CLAUDE.md`'s `## Build &
  test` block move by hand.
  `-n auto` is in the invocation and deliberately not in `addopts` or
  `PYTEST_ADDOPTS`, so a bare `uv run pytest` stays single-process for every
  consumer and for anything this suite shells out to.
- Enrollment surfaces (`examples/**`, `docs/wiki/**`, rules, schemas):
  additionally `./scripts/portability-sim.sh`.
- The gate's own DECLARATION surface (`repo.yaml`, `warden/schemas/`,
  `.github/workflows/`, this file): additionally `warden declare check`, which
  exits 1 when a declaration and the behaviour it governs have parted company
  and 2 when a check could not evaluate. It is not a second opinion on the
  suite — `tests/test_declare_check.py::test_this_repo_has_no_declaration_drift`
  runs the same command, so `uv run pytest -q -n auto` already fails on drift; naming it
  here is so an agent editing one of those four surfaces reads the obligation
  before CI does. Four checks (#194): every verify
  scope's reach is DECLARED rather than inferred; every gate input carries a
  `rules_version` claim that holds under perturbation; every declared verify
  scope runs in the CI job that runs `warden review`; every declared
  gate-surface path carries an explicit risk tier.
- Repair budget: `repo.yaml`'s typed `repair.budget` key, 2 here. `warden
  certify` R-12 and `deliver` read the key; this line documents it and gates
  nothing, by the founder ruling of 2026-09-15 (`warden decide list` carries
  the shard). 2 is the platform maximum, cut from 3 by the redesign the founder
  approved on 2026-09-14
  (`docs/superpowers/specs/2026-09-14-agentops-redesign-design.md`), which
  supersedes the ruling that had raised it from 2 (#94); `warden
  decide list` carries the shard. `deliver` caps repair at 2 and a repo may
  set a lower cap. What that budget counts, how many rounds review runs and
  what each round may file are stated ONCE, in `## Review charter` under
  **The round contract**: an attempt is a change-defect round, and a round
  `warden round classify` reads as the crew reviewing its own scaffolding
  does not count. The measurement that scopes round 2 is #185's, over
  the three PRs with round-level data (#172, #173, #176): 0 of 28 round-2+
  findings were HIGH, every HIGH arrived in round 1, and 12 of 13 round-2+
  findings cited a file a prior repair commit wrote.

## Autonomy scope

Beyond the platform baseline, never eligible here:
- Anything under `cage/run.sh`, `cage/render.py`, or `cage/providers.py` —
  a session must not edit the cage that contains sessions.
- Release mechanics: version bumps, tags, `platform.pin`.

## Forbidden paths

Never edit: `graph.yaml` · `cage/run.sh` · `cage/render.py` ·
`cage/providers.py` · `warden/certification/` · `warden/mechanical.py` ·
`warden/plugins.py` · `warden/schemas/` · `.cage/` — plus the platform
list (`.warden/`, `repo.yaml`, `.github/`, `.githooks/`, `.claude/`).

**WHERE this fence BITES**, stated because the list above has no grammatical
subject and a reader takes the mechanism to be wider than it is. Exactly one
thing enforces it: `cage/run.sh`'s post-check, which greps the caged
worktree's `origin/main...HEAD` diff against the regex rendered from
`.cage/cage.toml`'s `[gate].forbidden_paths` — this paragraph's list — and
closes the PR on a hit. Nothing in the rest of the gate pipeline reads the
list AS A FENCE — not `warden review`, not the `warden gate` job, not
`.githooks/` — none of them rejects an edit to a listed path. Two things DO
read it, and neither gates an edit. `tests/test_self_cage.py` asserts the
Never-edit paragraph against `.cage/cage.toml`'s `[gate].forbidden_paths`,
against the boundary PARAGRAPH and the offer SENTENCE in `.cage/prompt.md`
(the first is read as a paragraph on purpose — see `prompt_boundaries`), and
against `risk_tiers`: SEVEN test functions reach `policy_forbidden_paths`,
and `uv run pytest` carries every one of them into `.githooks/pre-push` and
into CI, where they run in each interpreter leg of the `tests` matrix (named
`warden tests (py3.11)` and so on) and again in the `verify` job (named
`warden verify`, via `verify --scope tests`), whose result the `gate` job
TAKES rather than re-earning — so the job named `warden gate` runs no suite,
and the bare status `warden tests` is the `tests-all` aggregator, which runs
none either. And `warden declare check`
D-04 reads the same paragraph to refuse a listed path that classifies by the
UNMATCHED tier default. Every one of them asks whether the list is DECLARED
consistently, never whether a diff TOUCHES it. So what the fence MECHANICALLY
closes is still the unattended run, which is the scope
`docs/wiki/Skills-Policy.md` has given this section since it was written
("project paths the autonomous session must never EDIT") — and what governs
an interactive session is not this mechanism but the standing default stated
in the paragraph below, which is unchanged and stricter.

**That is a statement about the mechanism and NOT a permission**, and the
difference is a question that is OPEN and is the
founder's to rule: whether the fence stays absolute in prose (a `graph.yaml`
edit arrives as a founder commit) or gains a narrow propose-only carve-out.
An agent must not settle that by editing this file, so nothing here settles
it. Until it is ruled, the standing default is the stricter reading and is
unchanged: an interactive session touches no path on this list without a
current, explicit founder instruction naming the path; the instruction is
recorded in the PR that carries the edit; a human merges it; and
`review.delegation.eligible` is out of reach on either ruling, because an
agent writing itself into it is the self-grant the ladder forbids.
`skills/nightgate-skills/skills/deliver/SKILL.md` states the flat "never edits
gate surface" bound for the protocol BOTH tiers run, and that bound stands
over this paragraph. The founder-directed `graph.yaml` edit in #269 is
what RAISED the question, not an instance of it already being answered.

The three `warden/` files joined the list in #268. `repo.yaml` tiers
all three HIGH — `mechanical.py` because checker code IS policy, `plugins.py`
because plugin loading is the policy execution surface, `warden/schemas/**`
because they are the artifact contracts consumers parse — and this paragraph
is what `.cage/cage.toml` renders its `forbidden_paths` from, so until they
were named here the cage's hard stop closed NONE of the three — the one
`warden/` path it did close, `warden/certification/`, is tiered MEDIUM by the
`warden/**` catch-all — and the prompt handed `plugins.py` to an unattended
session outright. `tests/test_self_cage.py::test_the_fence_and_the_
prompt_close_every_high_tiered_path` derives the required set from `repo.yaml`
and reddens if a HIGH glob is added that this list does not cover.

`.cage/` is here for the reason the three `cage/` files are: a session must not
edit the cage that contains sessions. It is the SOURCE of the
platform's own runner — its fences and its hand-authored prompt — and like the
three above it binds only after merge and re-enroll, because what runs is the
installed copy. Until #194 the only statement of that fence was a sentence in
`.cage/prompt.md`, and `.cage/**` carried no `risk_tiers` glob at all, so
`warden explain` classified the file declaring the gate's own hard stops LOW by
default. Both halves are declared now, and `warden declare check` D-04 refuses
the recurrence: a path named here that classifies by the unmatched tier default
is drift, not a tier.

**Autonomy-ladder carve-out** (founder-granted, #132; widened once,
#273): the machine tier of the ladder below MAY write, via a PR that a
human merges and never by auto-merge, exactly four things under the
otherwise-forbidden `.warden/`:

- `paused: true` + `paused_reason:` in an existing `.warden/rules/*.md`
  frontmatter — an auto-pause on a refutation streak;
- a new **non-blocking** rule file under `.warden/rules/` — a machine
  adoption; `warden autonomy adopt` refuses any severity in
  `blocking_severities`;
- the pause/adopt backtest artifact that change carries, under
  `.warden/memory/backtests/`;
- the **attestation shard** a review round leaves, under
  `.warden/memory/attest/` — EVIDENCE that a review ran, not a rule that
  changes what passes.

The fourth is the founder's ruling in #273, and the ground is that
distinction. The first three write RULES and the artifacts justifying them;
a shard changes nothing the gate enforces. Freezing it made the platform
contradict itself — `warden gate` refuses a PR carrying no committed shard,
the freeze refused the shard — and the first completed unattended run
(20260920-205434) built, reviewed and repaired a fix, then stopped at a
forbidden path for committing the evidence this protocol told it to commit,
with its work unpushed. There was no path between the two halves, so no
unattended run could ever ship.

Two narrowings past what `warden attest write` itself permits: the shard must
record `roster_verification: verified` (the roster was cross-checked against
the round's own reports, not merely asserted) and `round_binding: bound` (the
reports came from a directory `warden round new` minted for the head it
attests). Its `sha` must also name a commit in the PR's own range, so an
imported shard is refused. It does NOT have to be `clean`: an early round's
`findings-open` shard is committed by design, and `warden attest check` owns
the tip-clean rule. What none of this claims is that the review was REAL —
`verify_roster`'s own docstring says a caller writing plausible files passes
it — and nothing is lost by admitting the path, because `cage/run.sh` already
published the same session-authored attestation content into the corpus on
its behalf. The bound is elsewhere: a human merges.

Nothing else under `.warden/` is in scope, `.warden/memory/decide/` included
and by decision rather than omission — a decision shard records what was
RULED, which this document and the skills refer back to as policy, and no run
is blocked for want of one. Nor is any of `repo.yaml`, the schemas,
`mechanical.py`, `warden/certification/`, `.warden/certification.yaml` or
`.warden/memory/tags.yaml` — those raise enforcement or change the gate's own
machinery, which stays human. The carve-out is narrow on purpose: it widens
what a machine may PROPOSE, never what it may merge.

## Autonomy ladder

Decided after the founder's challenge that "propose only,
never adopt" was a blanket posture, not a destination. Autonomy here is per
ACTION. The line is not human-versus-machine; it is: **does the action raise
enforcement on anyone?** What makes the distinction real today, for
corpus-written rules: `blocking_severities: [HIGH]` in repo.yaml while every
covers-bearing rule ships MEDIUM, enforced by test — those rules report and
cannot fail a build. A catalog rule carries no such guard of its own (this
repo's own pinned-actions ships HIGH), so what bounds the machine-adoption
rung below is the severity refusal #132 built: `warden autonomy
adopt` refuses a rule whose severity is in `blocking_severities` — HIGH on
this repo — and
`tests/test_autonomy_adopt.py::test_adopt_refuses_a_high_blocking_rule`
proves it.

Machine-eligible — reduces enforcement, or adds only reporting. The mechanism
that performs these landed in #132: `warden autonomy pause` and
`warden autonomy adopt`. Each writes under `.warden/`, which `## Forbidden
paths` above otherwise bars every agent from — so the same bead added the
narrow, founder-granted **carve-out** there that permits exactly these writes,
**via a PR only, never auto-merged**. So the tier is permission now — but
**bounded permission**, and the bound is the point: the command opens a change
for a human to merge. #167 landed the wiring that used to be a
separate tracked step, so the honest description changed with it: the cage's
forbidden-path post-check now consults `warden autonomy carve-out`, and where
a project sets `[gate].autonomy_carve_out = true` it leaves such a PR **open**
instead of auto-closing it. So the mechanism IS reached from the unattended
loop now — behind a per-consumer grant that is **default-off**, so no repo
gets it by upgrading. What the wiring did not move is the whole bound: the
machine tier still only ever opens a PR a human merges, still **never
auto-merges**, and can only ever make the gate quieter or add reporting, never
raise enforcement:

- **Draft the rule file and open the PR.** Opening is not merging — pure
  saved typing, zero blast radius.
- **Pause a rule on a refutation streak** — three consecutive refuting
  review **rounds** (rounds, never records: one round's findings are one
  round's opinion however many it filed), and the
  pause is **immediate**, not deferred to the next retro: it only ever
  makes the gate quieter, it is reversible by design, and it must carry a
  recorded reason (a pause backtest artifact). A noisy rule taxes every
  review until someone gets round to it; immediacy is the honest reading
  of "reversible and only quieter".
- **Adopt a non-blocking rule.** It cannot stop anyone, and reverting is
  one commit. `warden autonomy adopt` measures nothing before it adopts: the
  artifact it writes records `action: adopt` with `judged: 0` and no hit
  count, because the rule has produced no findings and an `engine: claude`
  rule cannot be replayed over the tree, and the carve-out refuses an adopt
  artifact that claims a measurement. Its noise is counted after adoption,
  by the refutation streak the pause rung above reads. It refuses a
  `covers:` or `implements:` rule at any severity (#318). Machine adoption
  opens a PR and **never
  auto-merges** — merge authority is the founder's, delegated only by an
  explicit, current, revocable grant, on the terms `graph.yaml`'s
  `review.delegation` declares. The terms live there and the grant never
  does: the block has no holder, issue-time or expiry field and closes to
  additional properties, so a grant written into it is refused outright, and
  a tracked file has no clock to expire one with. auto-merge-on-green
  would be a further grant, taken never assumed.

Human-only — raises enforcement, or changes judgment itself:

- Promoting a rule to a **blocking severity** or a deterministic checker: a
  false positive now stops everyone. The bar: 10+ judged, 0.7 floor, and a
  person agreeing.
- Changing a **skill or protocol**: it governs how every change is judged,
  and its quality cannot be measured by precision the way a rule's can.
- The gate's own machinery, the **certification ladder**, and **merge
  authority**. An agent that can rewrite the gate it is judged by does not
  have a gate.

Scope: this ladder binds the **platform** repo first. It does not bind a
**consumer** repo until the loop has run here long enough to show its
precision record — a consumer adopts it by declaring it in their own
policy, which is a fresh decision, not an inheritance.

## Review charter

Beyond the `engine: claude` rules, every diff gets the lenses listed at the
end of this section. The list is complete: it is the set `graph.yaml`'s
`review.lenses` declares, and
`tests/test_graph_review.py::test_this_repos_graph_resolves_and_its_lens_ids_equal_the_charters`
fails when the two differ. A reader who finds a lens missing would take it
to have been retired, so where a lens has BECOME a rule its entry is reduced
to a POINTER at that rule rather than dropped. A lens becomes a pointer when
a rule file takes the obligation over, never before. All three are pointers
now: Enforcement truth, Fail-closed and
Consumer blast radius (#360 and
#361). So a lens's findings are filed under the rule its pointer names:
`warden attest write` refuses `unmapped:<slug>` for a slug a declared rule
`covers:`, which refuses `unmapped:consumer-blast-radius` now.

Three more `crew:` lenses carry findings in the corpus and are NOT on the
list: `crew:evidence-attribution` and `crew:tests-bite` (PR #185 only) and
`crew:evidence-intact` (PR #186 only). By the founder's ruling of 2026-09-26
they were one-off dispatches of those sessions, not
standing lenses the charter under-declared. None is promoted, and the reason
is recorded so the numbers do not reopen it: `crew:evidence-intact` clears
the 10-judged / 0.7-floor bar (11 judged, 11 upheld, Wilson floor 0.74), but
each of the three ran on ONE PR, the thin-sample trap the 2026-09-10 ruling
declined on (shard `20260910T2130400000-c46097ff-0a1f72fe`, its
`alternatives`). Rule files now exist for all three obligations:
`.warden/rules/attribution-holds.md`, `.warden/rules/evidence-intact.md` and
`.warden/rules/tests-bite.md`.

**The round contract, stated once.** `pre-pr-review` and `deliver` carry the
commands; this is the contract they run under. **Review runs ONE round by
default**, and WHO runs it is declared in `graph.yaml`'s `review` block
rather than stated here: `warden graph crew --round N` prints that round's
roles in dispatch order, and `warden attest write --review-dir` refuses a
roster that is not exactly them. Round one runs every lens below as a
checklist inside the dispatch the graph feeds the diff. A second round runs
only when a round-one finding that counts was repaired. It is a scoped
re-review, run by the crew round 2 declares, handed EVERY round-one finding
still open, repaired or not, and its payload re-files each one not addressed
as `confirmed`, because `warden round classify` reads only that payload.
Round 2 makes no repair commit: no review round follows it to review one.
The cap is `repo.yaml`'s `repair.budget` (`## Verify`); `graph.yaml`'s
`review.cap` must equal it, and `warden/graph.py` refuses a graph where it
does not. Past the cap the only round `attest write --review-dir` accepts is
the closure round `review.closure` declares, and it is TERMINAL: it spends no
budget and reviews nothing, because `verify_closure` refuses any record of
its that does not repeat, whole, a claim a round within the cap already
committed on the branch. It exists so that a head a repair moved at the cap
has something to attest (#272). The proportionate round
`review.light` declares is not used on this repository (`## Small-PR
contract`, rule 4).

**What an attempt is** was re-specified by
ruling on 2026-09-02 (#185; `warden decide list` carries the
shard) after the cap was exceeded three times in one session — PRs #166,
#172, #175 — with the change right every time and something else wrong: a
lens that never ran, a test that never evaluated, a builder's unexecuted
claims about its own diff. **For a repair ROUND** — a red `## Verify`
command is still one plain fix iteration, with no findings payload and no
round to classify — an attempt is a **change-defect round**: one with an
open COUNTING finding on the original diff (the first round's
`base_sha...head_sha`), or any open counting finding above LOW wherever it
lands. A counting finding is a behaviour finding, or a wording finding that
does not rank below every severity in `review.blocking_severities`. What a
finding judges is resolved in one place, `warden/repair.py`'s `kind_of`: a
finding under a declared rule judges what that rule's `judges:` says, and
behaviour when it says nothing; an `unmapped:` finding is wording when its
own `judges` field is absent or says `wording`, and behaviour on any other
value (#317); any other rule_id, one that resolves to no rule, is behaviour.
`warden round classify` reads the rules and `review.blocking_severities` as
committed at the latest round's head (#222). An open wording finding below
every blocking severity does not count, and it is resolved in the same
round, never held for the cap. On this repository (`## Small-PR contract`,
rule 2) it is never filed as a tracked item: in round 1 it rides the repair
commit and is filed `fixed` in the next payload when the fix is a line or
two and a repair commit is being made anyway, and is otherwise recorded
`dismissed-with-reason` in the payload with its reason. One raised in round
2 cannot be recorded there — `verify_chain` refuses a
`dismissed-with-reason` record that carries no earlier round's claim — so it
is kept in the round directory and named in the PR body. Neither is a
scaffolding-stop adjudication.
A round whose open findings are all LOW and all on files the repair
commits wrote (the first round's head to the latest, first-parent and
non-merge, so a mid-repair merge COMMIT of the base branch adds nothing —
base-branch work that arrives by fast-forward, squash or cherry-pick IS on
that chain, and what removes it is the subtractions BELOW that filter, of
which `_repair_files` has three: a commit reachable from a recorded base, a
commit reachable from the base's NAME resolved now (for a
chain whose rounds all recorded a stale base), and a path the base branch
moved between those bases WHERE THE BRANCH CARRIES BYTES THE BASE BRANCH
ITSELF PUT THERE. That
LAST one used to fire on the move alone; it is conditional
because `round new` resolves `--base` at mint time and a sibling PR merging
between rounds otherwise deleted the repair's OWN files from this set) is
the crew reviewing its own scaffolding; it ends the loop and does
not count. A file in BOTH ranges reads as repair-written — that is the
measured definition, and it is what resolves a LOW that is in the change
and in the repair. At that stop, what is open is parked as at the cap.
`warden round classify` computes which from the round manifests — the
builder never classifies its own diff. A counting finding STILL OPEN ON THE
ORIGINAL DIFF at the cap is not a budget question — it says the change is
wrong and should revert. Anything else open at the cap is adjudicated
instead, exactly as at a scaffolding stop: recorded `dismissed-with-reason`,
naming a tracked item only for a `judges: behaviour` finding on runtime code
(`warden/`, `cage/`, `scripts/`, `.github/`) and stating the reason for any
other (`## Small-PR contract`, rule 2). A finding first raised in round 2
cannot be recorded so, since `verify_chain` refuses it: a behaviour finding
on runtime code is filed `confirmed` and reaches the closure round, and any
other is kept in the round directory and named in the PR body (rule 4).
The repair being wrong
is not the change being wrong.

**Review depth — the ruling in force.** One round by default and a cap of
two: the redesign ruling of 2026-09-14 (#214, shard
`20260914T2018550000-c740d097-2d471865`, corrected by
`20260914T2123400000-0ec0e2a2-be8524d2`). The terminal closure round above
came later, from #272. Review stays FLAT: every diff
gets every lens below whatever it touches, the crew is not tiered by diff
class, no lens is retired or narrowed, and the **enforcement-truth** lens is
never dropped for any class, nor for any tier a later ruling might create —
the conclusion of the rulings of 2026-09-02 and 2026-09-10 (#185, #200),
which the redesign kept. The bar the 2026-09-10 ruling applied to reducing
review stands: retiring a lens needs 10+ dispatches on the class, a
MAJORITY of them zero-yield, and 0 upheld HIGHs; narrowing one to a class
needs 10+ dispatches on the EXCLUDED class, all zero-yield; keeping one
needs nothing. Rosters no longer count per-lens dispatches, so no lens can
clear that bar on today's records; a lens stays countable through its
findings, under the rule its pointer names. This repository's own PRs also
run under the small-PR ruling of 2026-09-27 (#310, shard
`20260927T0434390000-9189ab03-ca6028b6`, its CI-failure figure restated by
`20260927T0536460000-f77708ad-cc41be80`), stated as the `## Small-PR
contract` section of `pre-pr-review` and of `deliver`. Superseded rulings
are not restated here: they live in their decide shards (`warden decide
list`), and the measurements behind the 2026-09-10 ruling in
`docs/design/review-depth-20260910.md`.

- **Enforcement truth** — now enforced by `.warden/rules/enforcement-truth.md`,
  which is hashed into `rules_version` and carries the class's precision
  record. The rule file is the single source of the obligation; this entry
  points at it and deliberately does not restate it, so the two cannot drift
  (resolving the duplication a review round raised, #171).
- **Fail-closed** — now enforced by `.warden/rules/fail-closed.md`, which is
  hashed into `rules_version` and carries the class's precision record (39
  judged), its eight named shapes, and the one record this corpus has refuted.
  The rule file is the single source of the obligation; this entry points at it
  and deliberately does not restate it, so the two cannot drift (the
  second half of the duplication a review round raised, #171).
- **Consumer blast radius** — now enforced by
  `.warden/rules/blast-radius-named.md`, which `covers:` the
  `consumer-blast-radius` slug, is hashed into `rules_version` and carries
  the class's precision record (#360); `graph.yaml`'s
  `review.lenses` points the lens at it (#361). The rule
  file is the single source of the obligation; this entry points at it and
  deliberately does not restate it, so the two cannot drift.


## Integrations

- review-crew: none beyond `pre-pr-review` — the crew itself is `graph.yaml`'s
  `review` block (`warden graph crew --round N`), and the charter lenses run
  as checklists inside round one's diff-fed dispatch (`## Review charter`),
  findings triaged before push.
- No `plan-gauntlet`, no `dev-executor`, no `memory` hook — this repo's work
  is founder-directed and its narrative lives in beads.

## Build disciplines

The platform builds under the disciplines it asks consumers to declare. Each
of these is here because its absence produced a real defect in this repo, not
because it reads well.

- test-driven-development: any change under `warden/` or `cage/` — the failing
  test comes first and is watched failing. A test that has never failed proves
  nothing about what it tests.
- systematic-debugging: any red test, flake, or surprising exit code — name the
  root cause before changing a line. A wrong diagnosis that happens to silence
  the symptom is the most expensive outcome, not the cheapest.
- verification-before-completion: before any "passes" / "clean" / "done" claim,
  have fresh output from the full command in hand. Never a prior run, never an
  inference from a partial check, and never an exit code read through a pipe.

## Shipping

PR bodies state the evidence line verbatim (test counts, sim results) and
name every review finding that was fixed or dismissed with its reason.
