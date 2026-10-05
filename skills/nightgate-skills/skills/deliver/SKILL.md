---
name: deliver
description: Take one tracked item (or, with --no-tracker, one task stated in the prompt) from ready to a merge-ready PR in a single invocation — plan, build, verify, adversarial review, gate parity, PR with its evidence chain. The founder names what to build (or says "next") and reviews the result; they never run the pipeline by hand. Never merges.
---

# Deliver — ready to merge-ready, once

The incidents and reasoning behind these steps live in the Nightgate
platform's docs/design/deliver-rationale.md; this page is the steps.

You own the whole chain for ONE item. The person who invoked you states what
they want and reads the result; they do not run commands, and they do not
hold state between steps.
If you find yourself about to tell them to run something, run it.

This is the same protocol the unattended autonomous-run loop uses. The difference is
only who is present: here you MAY ask when genuinely blocked, but you never
idle waiting — ask, and if no answer comes, take the documented default and
say which one you took.

Project specifics come from `.warden/skills-policy.md`. Missing file or
missing section → stop and report; never guess a project's policy.

## Which warden — resolve it before any other step

Run this from the repo root before anything else; every command below is
written `$WARDEN <subcommand>`.

```sh
if [ -x .warden/bin/warden ]; then echo .warden/bin/warden
elif ! command -v warden >/dev/null 2>&1; then
  echo "REFUSED: no .warden/bin/warden in this repo and none on PATH; install the release repo.yaml's platform.pin names" >&2; false
else
  pin=$(awk '/^platform:/ {p=1; next} p && /^[^[:space:]#]/ {exit} p && /^[[:space:]]+pin:/ {sub(/^[[:space:]]+pin:[[:space:]]*/, ""); sub(/[[:space:]].*$/, ""); gsub(/["'\'']/, ""); sub(/^v/, ""); print; exit}' repo.yaml 2>/dev/null)
  have=$(warden --version 2>/dev/null | awk '{sub(/^v/, "", $2); print $2}')
  if [ -n "$pin" ] && [ "$have" = "$pin" ]; then echo warden
  else echo "REFUSED: PATH has warden ${have:-of no readable version}, repo.yaml's platform.pin is ${pin:-unreadable}; install the pinned release" >&2; false; fi
fi
```

It prints this repo's `.warden/bin/warden` launcher when there is one, else
`warden` on PATH when its `--version` is `repo.yaml`'s `platform.pin` (warden
init enrolls a consumer with no launcher). A refusal is a stop: report it and
never substitute another warden. Put the printed word wherever `$WARDEN`
appears, or start each shell command with `WARDEN=<that word>;` — a variable
does not survive between tool calls. Each probe fence below opens with the
line `: "${WARDEN:?is unset; run the resolution step first}"`, which stops on
an unset `$WARDEN` that would otherwise fall silently into the probe's
fallback arm. Run that line first, too, before any other step that reads a
failed command as an older pin: unset, `$WARDEN rules recommend` runs `rules
recommend`, exits 127 and reads as a subcommand the pin lacks.

## Without a tracker — `--no-tracker`

By default the item lives in the repo's tracker (beads, `bd`): Select reads
and claims it, parking files follow-ups into it, Wrap comments on it. A repo
with no tracker invokes this protocol with `--no-tracker` and the task in
plain language:

```
/nightgate-skills:deliver --no-tracker add a --json flag to the status command
```

Every other step is unchanged: Orient, Plan, Design, Build, Verify, Ship,
every gate and every review round. Only where the item is recorded moves:

- **Select**: the task is the prompt's text after `--no-tracker`. Restate it
  as a testable outcome before building; a task you cannot restate is the
  "nothing to do" stop. There is no `bd ready` and no claim, and "next"
  names nothing without a tracker: stop and say so.
- **Provenance**: the commit records the task in place of an item id. The
  subject ends in the repo's no-item form — `(no-bead: <reason>)` on a repo
  whose validator is the platform's `scripts/commit-lint.sh`, for example
  `(no-bead: no tracker)` — and the body carries a `Task:` line quoting the
  prompt's task verbatim. The PR body opens with the same quotation where it
  would name the item id. `--bead` is optional on
  `$WARDEN decide record` and `$WARDEN attest write`;
  leave it off.
- **Follow-ups**: wherever this page or `pre-pr-review` says to file a
  tracked item or tracker issue (a parked finding, a finding from an
  undeclared lens, an idea outside the task), write it whole — file, line,
  failure scenario — under a `## Follow-ups` heading in the PR body instead,
  and a `dismissed-with-reason` record names that entry. The PR body is then
  the only place it lives; say so in the Wrap report.
- **Wrap**: there is no item to comment on. The report and the PR body are
  the record.

**Fail closed.** Without `--no-tracker`, a tracker that cannot be read (`bd`
not installed, `bd show` or `bd ready` erroring) is stop and report, naming
`--no-tracker` as the way to run without one — never a silent switch to
prompt mode. The two modes record provenance in different places, and the
invoker picks one.

The cage has no such mode. `autonomous-run` selects its own work with
`bd ready` and nothing carries a task into an unattended run, so the cage
stays tracker-bound.

## 0 · Orient

1. `$WARDEN explain` — components, risk tiers, which rules fire.
2. Read `.warden/skills-policy.md` in full, and the repair budget from
   `repo.yaml`'s `repair.budget`.
3. If `graph.yaml` exists, read it: it declares which node you are, which
   judges are independent, which roles review each round
   (`$WARDEN graph crew --round N`), and your failure policy. The
   declared topology beats this skill's defaults; say so in the attestation
   when they differ.
4. Confirm a clean tree on a branch that is not `main`. Dirty tree or wrong
   branch → stop and report; never build on top of someone else's work.

## 1 · Select

Under `--no-tracker`, the section above replaces this step. Given an id, use it. Given "next", run `bd ready` and take the first item
that is genuinely actionable — skipping anything the policy's
`## Autonomy scope` excludes, anything needing a human decision, and
anything whose description you cannot restate as a testable outcome. Say
which you picked and what you skipped.

Nothing eligible → say so, list what you rejected and why, stop. A truthful
"nothing to do" is a result.

Claim it (`bd update <id> --claim`).

## 2 · Plan

```
: "${WARDEN:?is unset; run the resolution step first}"
if $WARDEN plan --help 2>&1 | grep -q -- '--path'; then
  $WARDEN plan --task "<the item's intent>" [--area <area>] [--path <file>]...
else
  $WARDEN plan --task "<the item's intent>" [--area <area>]
fi
```

Pass `--path` once for each file the item names, relative to the current
directory as git reads it (the repo root, run from there); each path is
tiered exactly as `explain` tiers it. With no `--path`, and no hinted paths
or declared `risk:` in the area's file hints, the packet's `**Risk tier**`
reads `UNKNOWN` and Design below has nothing to read. **Probe before you
rely on it**, as Build probes `round classify`: a pinned warden predating
`--path` refuses it and prints no packet at all, so on such a pin run
without it and read the named files' tiers off `repo.yaml`'s `risk_tiers`
as Design below says.

The packet carries likely paths, risk tier, the commands that must pass, and
**priors from review memory** — where this area has gone wrong before.
Priors are places to look, never findings about your work.

If the change spans components or touches HIGH-tier files, and the policy
names a `plan-gauntlet` hook, invoke it and fold its objections in as
constraints. Advisory: it never decides, and its absence never blocks.

## 2.5 · Design — HIGH-tier only, before a line is written

Read the risk tier off the plan packet (`**Risk tier**`, classified by
`repo.yaml`'s `risk_tiers` — first glob match wins, unmatched is LOW; no new
tiering logic). A **HIGH**-tier change does not reach Build without a
**decision record**: the front-half artifact that says what you are about to
do and why, *before* the code exists to argue about.

Record it with `$WARDEN decide record --input <file> [--bead <id>]`, where
`<file>` is a JSON object carrying the ruling (`decided`), the reasoning a
reviewer can disagree with (`why`), what it costs if wrong (`cost_if_wrong`),
the alternatives weighed (`alternatives`), the scope it governs (`scope`).
Then `$WARDEN memory ingest` writes the decision shard under
`.warden/memory/decide/`, and **`git add` it into the branch and commit it** —
`ingest` writes the shard, committing it is yours, exactly as the attestation
shard is `git add`ed in Verify. That shard is committed EVIDENCE on the same
path an attestation takes, **not** a gate-surface edit, so it sits inside the
boundaries below. A HIGH-tier change does not reach Build without a committed
decision record on HEAD — it is incomplete without one, exactly as one that
reaches Ship with no attestation is.

MEDIUM and LOW tiers **skip this step and behave exactly as before**.

## 3 · Build

Read the policy's `## Build disciplines` first — it names the *methods*
that bind here, where `dev-executor` names only who builds.
**No disciplines declared → build exactly as before**: implement to the
Definition of Done inside the budget. Declared, they bind whoever builds, hook or
not, and you say which ones you are under before you start.

Policy names a `dev-executor` hook → invoke it with the intent *and the
declared disciplines*; it owns spec, implementation, its own review loops,
and the commit. Honor its verdict.

No hook → build directly: implement with tests per the repo's Definition of
Done, commit in the repo's convention. The count an `Evidence:` line cites
("N tests passed") is read off `$WARDEN verify`'s summary line,
which states `PASS — pytest: N passed, M skipped, K failed` for each scope
whose runner is pytest — never out of a gitignored artifact, and never
from memory. A line that prints no counts (a pinned warden that predates
them, or counts withheld or not found) gives no number: cite the command's
own output instead.

**The round contract is stated once**, in the Nightgate platform's
`.warden/skills-policy.md`, `## Review charter`, under "The round
contract": how many rounds review runs, what the repair budget counts, what
each round may file, and the closure round past the cap. This page carries
the commands that contract runs under, and the parking and wording defaults
below, which any repo follows. Round one is the crew
`$WARDEN graph crew --round 1` prints: `pre-pr-review` dispatches
exactly those roles, in that order, carrying that command's `lenses` as
checklists inside the dispatch the graph feeds the diff; a second round's
crew is whatever `--round 2` prints.
Repair runs in **rounds**, capped at 2 — a repo may set a lower cap,
never a higher one — so there is no third round. The budget is
`repo.yaml`'s typed `repair.budget` key (a whole number from 1 to 2), never
a number read out of the policy's prose. A `repo.yaml` with no key: take the
cap of 2 and say so in the attestation; `$WARDEN certify` R-12
fails that repo at Level 3.
The scoped re-review is a round DIRECTORY like any
other, so mint it with `$WARDEN round new` — `pre-pr-review`'s
**Mint every one of them** covers it, and a round nobody minted is invisible
to `classify`: at or past #191 that is the exit 2 below, and on an
older pin it is measured silently, which is worse. Whether a round counts,
and whether the loop has reached a scaffolding stop, is computed, never
argued from your own diff:

```
$WARDEN round classify --findings <attestation payload> --round <dir> [--round <dir>...]
```

exits 0 when the loop ends (clean, or scaffolding stop), 1 when the round
counts, and 2 when it cannot answer — and a 2 is never a stop condition
met. More than one thing exits 2, and not all of them are a broken
artifact. It could not read the chain or the payload. Or the payload declares more rounds than the
chain carries round directories: that reads fine and is refused anyway
(#191), because a round nobody minted leaves its repair commit
inside what is then measured as the original diff. Or a finding states a
`round` warden will not read as one — below 1, or `2.0` / `"2"` — which is
also a readable payload, refused rather than treated as absent. The
declared-rounds case is a MINTING defect, not a broken artifact: mint the
missing round, or pass the directory you left out, and run it again. And
the refusal is NOT universal: a warden pinned at or after #185 but
before #191 HAS `classify`, so the probe below passes, and has no
declared-rounds refusal at all — there an unminted round is measured
silently. Mint every round because the chain needs it, never because you
expect to be caught. It reads both ranges from the manifests
`$WARDEN round new` wrote and the findings from the payload `attest write`
consumes; a file in both ranges is repair-written, and a finding with no
file, or on a file in neither range, is the change's.

**Probe before you rely on it**, the same way `pre-pr-review` probes `round
new` and `--review-dir`: this pack ships from the default branch while each
repo pins a warden VERSION, and a pinned warden older than #185 has
no `classify` at all. `argparse` exits 2 on `invalid choice: 'classify'`
exactly as the command exits 2 on a chain it cannot read, so probe the
SUBCOMMAND with `--help`, never by running it and reading the exit code:

```
: "${WARDEN:?is unset; run the resolution step first}"
if $WARDEN round classify --help >/dev/null 2>&1; then
  $WARDEN round classify --findings <payload> --round <dir> [--round <dir>...]
else
  : # pinned warden predates the classifier — every round counts, as before
fi
```

On the fallback the cap counts ROUNDS, there is no scaffolding stop, and
you say so in the attestation: a stop you cannot compute is not one you may
assume. The rounds are the ones `pre-pr-review` minted, so **keep every
round directory it printed** — `classify` needs all of them, and a repair
loop that discards the earlier ones has no chain left to prove. A budget
is a stopping rule, not a method:

- Each round names the **root cause** before it changes a line; a fix that
  makes the symptom disappear without naming why it occurred is spent
  budget, not progress.
- The second round's **scoped re-review** verdicts every round-one finding
  still open `addressed` or `not addressed`. A second round that ends
  without per-finding verdicts has not ended.
- **Park only at the cap, or at a scaffolding stop**: file a tracked item
  that names each still-open finding and record it `dismissed-with-reason`
  naming that item, on the item, in the PR body's evidence chain and in the
  attestation (on this repository the Small-PR contract below narrows which
  findings get a tracked item). A silent discard is forbidden, and parking
  *before* the cap to end a loop early — or calling a round a scaffolding
  round without `$WARDEN round classify` saying so — is
  pre-judging with a different name.
- **An open wording finding `classify` does not count** is resolved in the
  SAME round, never held for the cap: in round one, fix it in the repair
  commit and file it `fixed` in the next payload, or file a tracked item and
  record it `dismissed-with-reason` naming that item (on this repository, the
  Small-PR contract below). A payload that leaves it `confirmed` cannot be
  attested clean.

A counting finding still open on the original diff at the cap says the
change is wrong, and the answer is revert, not a third round — **revert
wins** over parking wherever both fire. The repair being wrong is not the
change being wrong, and the Nightgate platform policy's `## Review charter`
draws the same line in the same words.

Out of rounds with no root cause named → it is blocked, and say so rather
than committing another guess.

Blocked either way → revert uncommitted debris (`git checkout . && git clean
-fd`), record why, go to WRAP. A truthful "blocked" beats a creative
workaround.

## 4 · Verify — all of it, in order

1. **The policy's `## Verify` commands** for what changed. Red you cannot
   fix inside `repo.yaml`'s `repair.budget` (platform cap: 2) → revert,
   record, WRAP.
2. **Review crew**, only if the policy names a `review-crew` hook: one
   bounded round over `git diff origin/main...HEAD`. Keep findings with
   file/line substance; discard vibes.
3. **`nightgate-skills:pre-pr-review`** with any crew findings injected into
   the candidate list. The judging role in the round's declared crew — the
   graph's answer, never this skill's — is the single arbiter. Fix
   confirmed findings in Build's rounds, under the round contract Build
   points at. Must end `$WARDEN attest write` CLEAN.
4. **Gate parity**: `$WARDEN review --base origin/main --no-comment` exits 0.
   This is exactly what CI will say.

## 5 · Ship

The tail is ONE command. It runs the steps in order, prints what each one
checked and what it found, and stops at the first step it cannot prove:

```
$WARDEN ship --base origin/main --title "<the squash headline>" --body-file <path>
```

Six steps: a verify artifact that names **this** HEAD and passed, under every
scope the diff requires; every tip attestation in `origin/main..HEAD`
committed and verdict-clean; the PR title linted by the repo's own
`scripts/commit-lint.sh` **before the PR exists**; gate parity
(`$WARDEN review --base origin/main --no-comment`, the command CI runs); the
push, confirmed by asking the REMOTE whether it carries HEAD; and the PR —
created, or the existing OPEN PR for this branch updated. A refusal stops the
tail and nothing after it runs. Exit 1 is "a step refused"; exit 2 is "a step
could not be evaluated", which is never a pass.

- **Step 1 is where a green claim about the wrong commit dies.** Committing
  the attestation shard MOVES HEAD, so the last `$WARDEN verify` ran on a
  commit that is no longer the one you are shipping. Re-run the policy's
  verify scopes on the commit you are about to ship, then ship. An artifact
  naming the pre-shard commit is a refusal, and it is the correct one.
- **The title is linted before the PR is created**: the budget is 100
  characters for what you write. Over budget → shorten the title; never
  widen the budget.
- The body `--body-file` points at carries the evidence chain: the item id,
  `$WARDEN attest show` output, what was built, every finding and its
  disposition, a suggested review order, and whatever the policy's
  `## Shipping` section adds. It is required to open a PR; `--checks-only`
  runs the four checks and stops, pushing nothing and opening nothing.
- Other flags, all defaulted: `--remote` (`origin`), `--pr-base` (`main`),
  `--commit-lint <path>` for a repo whose validator is not at
  `scripts/commit-lint.sh`. A validator that is not there is a step that
  COULD NOT BE EVALUATED — exit 2, printed as `COULD NOT EVALUATE` and naming
  the flag as the fix, not exit 1 — and an unevaluated step is never a pass: a
  title nothing checked is not a title known to be good.
- It records the `parity-run`, `pushed` and `pr-opened` progress boundaries
  itself. Nothing to record by hand here.
- **It never merges**: no merge argv, no `--auto`. Merge authority is the
  founder's, and this command stops at an open PR.

**Probe before you rely on it**, exactly as Build probes `round classify`:
this pack ships from the default branch while each repo pins a warden
VERSION, and a pin predating #201 has no `ship` subcommand at all.

```
: "${WARDEN:?is unset; run the resolution step first}"
if $WARDEN ship --help >/dev/null 2>&1; then
  $WARDEN ship --base origin/main --title "<title>" --body-file <path>
else
  : # fallback below — and say in the report that you ran it
fi
```

On the fallback the tail is hand-run, in this order, and each step is yours
to check because nothing prints a verdict for you: re-run verify at HEAD;
lint the title with `./scripts/commit-lint.sh --header-only` BEFORE the PR
exists; push the branch with its literal name typed out — never `HEAD`,
never command substitution — and confirm the remote took it; then
`gh pr create --base main`, idempotent: update the existing PR for this
branch rather than opening a second one.

- **Never merge. Never auto-merge. Never push to main.**

## 6 · Wrap — report like a person, not a log

- Comment the outcome on the item; leave it in progress (it closes on merge).
  Under `--no-tracker` there is no item: skip this line.
- Then tell the invoker, in prose: what you built, the PR link, what the
  review found and what you did about it, and anything you deliberately did
  not do. Lead with the outcome. If something is still open, say that first.
- `memory ingest` so the next round starts with this round's evidence.

## Boundaries

- **Never merges, never pushes to main, never force-pushes.**
- Never edits gate surface: `.warden/`, `repo.yaml`, `.github/`,
  `.githooks/`, `graph.yaml`, the policy file, or the paths the policy's
  `## Forbidden paths` names.
- **One item per invocation.** Finishing early is not permission to start
  another — report and let the invoker decide.
- Never weakens a rule, test, or baseline to reach green. If the gate is
  wrong, that is a finding worth raising, not an obstacle to route around.
- Never asks a question whose answer is in the repo, the policy, or the
  tracker.

## Small-PR contract

These four rules are the Nightgate platform repository's protocol for its
own PRs — "this repository", here and in every clause of this file that
cites this section, is the repo that ships this pack, never a consumer —
and they bind the PRs that repo opens against itself, not a consumer's.

1. **One tracked item per PR, at most 400 changed lines.** The count is the
   diff against the base branch, excluding files under `.warden/memory/`.
   There are no batch PRs. A change that would exceed the limit is split
   into ordered items before the first line is written, and each item ships
   as its own PR.
2. **A review finding becomes a tracked item only when it is a
   `judges: behaviour` finding on runtime code** — `warden/`, `cage/`,
   `scripts/` or `.github/`. A wording finding is fixed in the same PR when
   the fix is a line or two and a repair commit is being made anyway — it
   rides that commit; otherwise it is recorded `dismissed-with-reason`
   in the attestation payload, with the reason stated there. A wording
   finding is never filed. A behaviour finding outside that runtime set is
   fixed in the PR, and one still open at the repair cap is recorded
   `dismissed-with-reason` in the payload, never filed.
3. **After the final attestation nothing is committed on the branch**: no
   merge of main, no fixture fix, no retitle commit. A change the branch
   still needs gets a new round at the new head, whose attestation is then
   the final one, or a new PR.
4. **The light (`claims-auditor`) round is not used on this repository.**
   The closure round cannot be forbidden — `attest write` refuses
   `dismissed-with-reason` for a claim first raised in round 2, so a new
   round-2 record forces it — so round 2 is scoped to the round-1 repair
   and files a NEW record only for a behaviour finding on runtime code;
   anything else it raises is kept in the round directory and named in the
   PR body, as a scoped-out candidate is, and the closure round is reached
   only for a real defect. An unmapped record does not count when its
   `judges` field is absent or says `wording`, and counts on any other
   value; a declared rule's finding is what the rule's frontmatter says.

Where a sentence elsewhere in this file offers a wider path, on this
repository the rule above is the one that applies.

## Provenance

Earned steps and the incidents behind them. `as_of` is the
last date this protocol was checked against the system it describes. A step
citing a `tag:` class lets the retro notice if that class has gone quiet;
one citing a bead records why the step exists without claiming it is a
recurring class.

```yaml
as_of: 2026-09-27
assumes:
  skills: [pre-pr-review]
steps:
  - step: "re-branch off fresh main before the first commit of every item"
    evidence: agentops-g65
  - step: "--no-tracker takes the task from the prompt and records it in the commit body and the PR body, with follow-ups under the PR body's Follow-ups heading; without the flag an unreadable tracker stops rather than switching modes; the cage stays tracker-bound"
    evidence: agentops-hy6o.10
  - step: "review runs one round by default; a second, scoped re-review runs only when a round-one finding that counts was repaired, and it is handed every round-one finding still open, repaired or not; repair caps at 2 rounds, and a repo may set a lower cap, never a higher one"
    evidence: shard:20260914T2018550000-c740d097-2d471865
  - step: "the repair budget is read from repo.yaml's typed repair.budget key, never from policy prose; with no key, take the cap and say so"
    evidence: shard:20260915T0252230000-4dbe6b40-2d3779d1
  - step: "the cap counts change-defect rounds: a round counts when a counting finding (a behaviour finding, or a wording finding that does not rank below every blocking severity) is open on the original diff or above LOW; a scaffolding round ends the loop instead, and `$WARDEN round classify` decides which — never the builder"
    evidence: commit:2301beec
  - step: "the review crew runs flat across diff classes by ruling, enforcement-truth never dropped; the depth ruling in force is the one the Nightgate platform policy's `## Review charter` names, and superseded rulings live in their decide shards"
    evidence: commit:2301beec
  - step: "fail-closed in verify: a check that cannot evaluate behaves like a hit, not a pass"
    evidence: tag:fail-open
  - step: "never weaken a rule, test, or baseline to reach green"
    evidence: tag:test-cannot-fail
  - step: "a HIGH-tier change records a warden decide decision before Build; MEDIUM/LOW unaffected"
    evidence: commit:3747b2cc
  - step: "no third round runs, but the scoped re-review is a round DIRECTORY minted like any other, and `classify` exits 2 on a payload declaring a round the chain has no directory for"
    evidence: commit:410cd48e
  - step: "the ship tail is ONE command (`$WARDEN ship`) that refuses per step — verify naming THIS head, tip attestations clean, the title linted BEFORE the PR exists, gate parity, a push the remote confirms, then the PR; hand-run only behind a `--help` probe on a pin predating it"
    evidence: commit:1a4959a1
  - step: "the small-PR contract binds this repository's own PRs: one item per PR at most 400 changed lines, a tracked item only for a behaviour finding on runtime code, nothing committed after the final attestation, the light round unused and round 2 filing a new record only for a behaviour finding on runtime code"
    evidence: shard:20260927T0434390000-9189ab03-ca6028b6
```
