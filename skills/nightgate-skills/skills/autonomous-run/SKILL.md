---
name: autonomous-run
description: Unattended dev-loop session protocol for a repo enrolled on the Nightgate platform — pick one eligible bead, build it, survive the verification gauntlet, open a PR, report. Never merges. Invoked headless by the cage runner, whatever the trigger (schedule, manual, ci-event).
---

# Autonomous Run — session protocol

The incidents and reasoning behind these steps live in the Nightgate
platform's docs/design/autonomous-run-rationale.md; this page is the steps.

You are running **unattended** inside a dedicated worktree on a run branch,
invoked by the cage runner. Nobody can answer questions. Every
step either completes with evidence or ends the run with a written reason —
never wait, never improvise around a blocked step, never end your turn to
"await" a background task (run subagents synchronously only).

Project specifics come from `.warden/skills-policy.md` (see the Nightgate
platform's docs/wiki/Skills-Policy.md for the contract). The file is part of the gate
surface — you read it, you never edit it. Missing file or missing required
section → write `RUN-ABORT.md` with what's missing, stop.

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

## 0 · Orient

1. Confirm you are in a worktree on the project's run branch prefix
   (declared in its cage enrollment, `cage.toml`; default `auto/`)
   with a clean tree (`git status`, `git branch --show-current`). Not true →
   write `RUN-ABORT.md` with what you found, stop.
2. `$WARDEN explain` — load the risk map.
3. Read `.warden/skills-policy.md` in full.
4. If `graph.yaml` exists, read it — it declares which node you are, which
   judges are independent, and your failure policy. Honor the declared
   topology over this skill's defaults, and record the node in the handoff.

## 0.5 · RESUME — continue, don't restart

If `.run-resume` exists in the worktree root, a previous run was
interrupted (timeout or usage limit) mid-bead and left a checkpoint. Read
it: it names the `bead`, the `attempt` number, the `branch` you are already
on, and — after a `---` line — the checkpoint that run wrote.

**Continue that bead. Do not pick a new one, and do not start it over.**
- Re-read the checkpoint's `done:` and `next:` lines and pick up at `next:`.
- The branch and any PR already exist: keep using them. `gh pr create` is
  idempotent by the SHIP rules — update the existing PR rather than opening
  a second one, and do NOT re-add the `auto-attempted:` bd comment.
- Verify what the checkpoint CLAIMS before trusting it: run the tests, look
  at `git log`/`git diff`. A checkpoint is a memo from a session that died —
  it records intent, not proof.
- Then continue at the phase the checkpoint names, and keep checkpointing.

The cage budgets this: after `max_attempts` the resume state is cleared and
the bead goes back to the founder.

## 1 · DISCOVER — pick exactly one bead

This protocol is tracker-bound and has no `--no-tracker` mode. An
unattended run has no prompt to take a task from: the tracker is where its
work comes from, and the claim, the `auto-attempted:` marker and the
blocked-run comments are where it leaves its trail. A repo without beads
runs `deliver` and `ship` with `--no-tracker`, and not the cage.

Run `bd ready`. Walk candidates in priority order and select the FIRST that
passes every test; record each rejection reason briefly for the report:

- **Skip** beads whose notes contain `auto-attempted:` — or the older
  `night-attempted:`, which real trackers still carry on beads written
  before the rename (the founder clears these by hand — no retry
  grinding). A tracker search does not read comments, so "no legacy
  markers left" is a claim that must be checked bead by bead before this
  clause is ever dropped.
- **Skip** beads labeled `no-auto`, or the older `no-night` (founder veto).
- **Platform hard-excludes** (never eligible, no policy may relax them):
  epics; gate surface (`.warden/`, `repo.yaml`, `.github/`, `.githooks/`,
  `.claude/`); production migrations, deploys, or infrastructure; beads
  needing accounts, credentials, purchases, or any human decision;
  open-ended explorations/spikes ("explore", "investigate", "HUMAN
  DECISION").
- **Project hard-excludes**: everything the policy's `## Autonomy scope`
  section adds (e.g. UI/design surfaces, client code the founder owns).
- **When unsure, skip** — and add a one-line bd comment saying why. A
  skipped bead is a good outcome; a misjudged one is not.

No eligible bead → bd is untouched, write the report ("no eligible bead"
plus the rejection list), stop cleanly.

On selection: `bd update <id> --claim`, add a bd comment
`auto-attempted:<YYYY-MM-DD> — autonomous run picked this up`, and record
`bead: <id>` for the report.

**Checkpoint as you go.** After each phase completes, overwrite
`.run-progress` in the worktree root:

```
bead: <id>
phase: <plan|execute|verify|ship>
done: <what is finished and committed — one line>
next: <the exact next action>
loops: <repair iterations spent so far>
```

The cage reads this only when a run is interrupted; a clean run deletes it.

## 2–5 · PLAN, BUILD, VERIFY, SHIP — run `deliver`

Invoke `nightgate-skills:deliver` for the bead you selected. It owns the
chain: plan packet with memory priors, the `dev-executor` hook (or direct
implementation), the policy's verify commands, the review crew, pre-pr-review
ending in a CLEAN attestation, gate parity, and the PR with its evidence.

The protocol lives in exactly one place on purpose.

**What is different here, and only this:**
- Nobody is present. Where `deliver` may ask, you may not: take the
  documented default, record which one, and continue.
- The cage's hard stops already ran before you started, and its
  forbidden-path check runs after you finish. Do not test them.
- One bead, this session, full stop.
- The policy's `## Build disciplines` bind through `deliver` as always —
  but write the named **root cause into the bd comment**, not just into
  your reasoning. **No disciplines declared → nothing changes here.**
- Blocked at any phase → revert uncommitted debris, bd comment the verbatim
  condition, go to WRAP. Never improvise past a blocked step.

## 6 · WRAP — evidence, always

- bd: comment the outcome on the bead (done + PR number, or blocked +
  reason). Leave the bead in_progress — the founder closes it on merge.
- Handoff: write `.run-summary` in the worktree root, with exactly:
  `bead: <id|none>` / `outcome: <done|blocked|skipped>` / `pr: <num|none>` /
  `notes: <one line incl. findings counts and skips>` / `node: <the
  graph.yaml node you ran as, or 'builder' when no graph is declared>` /
  `loops: <repair iterations spent>`. The cage runner reads and removes
  it — this is how the run gets attributed in the ledger and in the org
  chart.
- Write nothing beyond `.run-summary`, `.run-progress`, and bd comments:
  no session-state, no memlogs, no narrative files. The
  review-queue session owns narrative memory.
- End the session cleanly.

## Boundaries

- **Never merges a PR, never pushes to main, never force-pushes.**
- **Never edits gate surface or its own cage**: `.warden/`, `repo.yaml`,
  `.github/`, `.githooks/`, `.claude/`, the policy file, anything in the
  cage directory, PLUS every path the policy's `## Forbidden paths` section
  lists. (The cage runner independently closes any PR whose diff touches
  its configured forbidden paths — do not test it.)
- **Never works outside the current worktree**; the project's live checkout
  is off-limits.
- **One bead per session.** No network beyond git/gh/bd. No secrets: never
  read `.env`, never echo tokens.
- **Never weakens a rule, test, or baseline to get to green.**
- When any step cannot complete within `repo.yaml`'s `repair.budget`:
  revert, record, stop. A truthful "blocked" beats a creative workaround, every
  run.

## Provenance

Earned steps and the incidents behind them. `as_of` is the
last date this protocol was checked against the system it describes.

```yaml
as_of: 2026-09-15
assumes:
  skills: [deliver]
steps:
  - step: "honor legacy eligibility markers (night-attempted:/no-night) alongside the renamed ones"
    evidence: commit:2dd96b21
  - step: "consult the policy section by its real name '## Autonomy scope'"
    evidence: commit:54eebf26
  - step: "delegate build/review/ship to deliver — the protocol lives in exactly one place"
    evidence: commit:d32f08ad
```
