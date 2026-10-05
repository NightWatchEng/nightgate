---
name: orchestrate
description: Fan independent tracked items out to parallel worktree-isolated builder agents, each invoking deliver, then own the collection — per-PR ship checklist, merge order, and the conflict-reconciliation loop as PRs land. The parallel counterpart to ship's one-at-a-time queue. Never merges.
---

# Orchestrate — parallel builders without a hand-written protocol

The incidents and reasoning behind these steps live in the Nightgate
platform's docs/design/orchestrate-rationale.md; this page is the steps.

Use this when a batch of tracked items is genuinely independent and worth
building concurrently. One item at a time is `nightgate-skills:ship`'s
queue; this is the fan-out. If the harness serves no subagents, do not
imitate parallelism — degrade, observably, to the sequential queue and say
so in the report.

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

## 0 · The one rule that earns this skill its place

**A builder brief names the skill to invoke. It never restates the
protocol's steps.**

## 1 · Partition

Read `.warden/skills-policy.md` and the tracker. Cluster the batch into
units that can each become ONE merge-ready PR:

- Items in one unit must be buildable together; units must be independent
  of each other (no shared files where avoidable, no dependency edges
  between units).
- Default is one item per unit. **Bundling several items into one PR is a
  founder-granted deviation** — do it only when the founder has said so
  for these items, and write it into the brief as a deviation by name, so
  the builder knows the default it is departing from.
- Anything the policy's `## Autonomy scope` excludes stays out.

Report the partition before dispatching: units, the reasoning, and the
expected merge order.

## 2 · Dispatch

First run `$WARDEN skills preflight` — a brief must not name a skill the
installed pack no longer serves. The
default checks a repo-local pack source; on a repo that consumes the pack
via the plugin marketplace, point `--pack` at the installed plugin
cache's `skills/` directory, which is the pack builders will actually
resolve against.

Per unit, one builder in its own isolated worktree (never two builders in
one tree; never a builder in the orchestrator's tree). The brief carries:

- the item id(s), claimed before dispatch so two builders cannot collide;
- the instruction to invoke `nightgate-skills:deliver` and follow it END
  TO END — Ship and Wrap included (in the platform repo, where the pack
  source lives in-tree, the brief may also point at
  `skills/nightgate-skills/skills/deliver/SKILL.md`);
- every founder-granted deviation, stated as a deviation (e.g. "one PR
  covers these two items"; "do not close the bead — it closes on merge");
- the report-back contract: PR URL, per-item status, findings and their
  dispositions;
- **an isolated working directory keyed to that builder's branch, for every
  file it writes outside the repo.** A worktree separates the CODE; it does
  not separate the scratch. Parallel builders under one session share one
  scratchpad root, so every unqualified filename in it is a collision
  candidate: never the shared scratchpad root, and never a bare filename in
  it.

Nothing else about method. If you are tempted to add a protocol step to
the brief, that step belongs in `deliver` itself — file the item.

## 3 · Collect — the ship-stage checklist, per PR

**Probe both commands before you rely on either**, the same way `deliver`
probes `$WARDEN ship` and `pre-pr-review` probes `round new`. This pack ships
from the DEFAULT BRANCH while every consumer repo pins a warden by version or
SHA, and `$WARDEN progress` and `$WARDEN ship` landed together
(#201) — so a pin that predates them has neither, and `argparse`
exits 2 on `invalid choice` exactly as both commands exit 2 on a state they
cannot evaluate. Probe the SUBCOMMAND with `--help`; never by running it and
reading the exit code, which cannot tell "this warden has no such command"
from "this command refused":

```
: "${WARDEN:?is unset; run the resolution step first}"
if $WARDEN progress --help >/dev/null 2>&1; then BOARD=yes; else BOARD=no; fi
if $WARDEN ship --help     >/dev/null 2>&1; then CHECKS=yes; else CHECKS=no; fi
```

Each probe BRANCHES, and the two branch separately. That is the same `if ... else ... fi`
shape `deliver` uses for `$WARDEN ship` and `pre-pr-review` for `round new` —
one house rule, not three.

Where a probe fails, that half degrades to the **hand-run fallback at the end
of this section** — never to nothing, and never to the builder's word — and
you say in the report which halves you ran.

Read the board before you ask anyone anything:

```
$WARDEN progress show
```

One row per unit — its branch, its head, the head its last boundary applies
to, that boundary, and the **AGE** of it. The age is the signal. A row whose `at` is starred `*` has committed work and recorded
no boundary since — the signature of the unit that finished a fix and died
with it local. `NONE RECORDED` (nothing yet), `UNREADABLE` (a log that will
not parse) and a missing row are three different states and the table keeps
them apart. The default root is the repo's `.claude/worktrees` when it has
one, else the repo itself; `--root <dir>` scans another layout and `--unit
<name>`, repeatable, narrows it.

The table says where a unit IS. It never says the unit is RIGHT. So for every
unit, before reporting it shipped, verify — with fresh command output, not
the builder's word:

1. **The four ship checks, re-run against the unit's current head**, from
   inside that unit's worktree:

   ```
   $WARDEN ship --checks-only --base origin/main \
     --title "$(gh pr view <branch> --json title --jq .title)"
   ```

   `--checks-only` runs the four checks and STOPS: it pushes nothing and
   opens nothing, so running it on a collected unit cannot change what the
   PR says. Step by step, it prints what it checked and what it found — a
   verify artifact naming THIS head and passing under every scope the diff
   requires; every tip attestation in `origin/main..HEAD` committed under
   `.warden/memory/attest/` and verdict-clean (an attestation left sitting in
   the gitignored `.warden/out/` instead reads as success locally and fails
   the gate one push later); the title accepted by
   `./scripts/commit-lint.sh --header-only`; and `$WARDEN review --base
   origin/main --no-comment` at exit 0, which is exactly what CI will say.
   Exit 1 is a refusal naming its step; exit 2 is a step that could not be
   evaluated, which is never a pass.

   One thing this canNOT re-verify, stated so it is not read as covered.
   `deliver`'s Ship lints the title BEFORE `gh pr create`, and that ordering
   cannot be re-checked after the fact,
   only relied on. What you are re-linting here is the title the PR carries
   NOW, which is worth the second look because `gh pr edit` can change a
   title after ship and CI lints the squash headline whatever wrote it.

   Run it AFTER reading the table, never before: its gate-parity step
   records a `parity-run` boundary in that unit's own log, so it refreshes
   the age column you were about to read.

2. **PR is open** against the default branch, body carrying the evidence
   chain, and `gh pr view <n> --json mergeable` does not report a conflict.
   Nothing in check 1 asks either question: `$WARDEN ship` opens a PR and
   never looks at it again, and whether main has moved underneath it since
   is the whole reason §4 exists.

A unit that fails a check goes back to its builder (or is finished by
you, following `deliver`'s Ship section) before it is reported. Report
units as they complete; never batch the report silently.

**The hand-run fallback**, for a pinned warden that failed either probe.
The checks below depend on nothing newer than `$WARDEN review`, and
they are what collection means on such a pin.
No board: ask each builder where it is, and say in the report that the age
signal was unavailable. No `--checks-only`, so per unit, with fresh output:

1. **Attestation shard committed on the branch**:
   `git log --oneline origin/main..<branch> -- .warden/memory/attest/` names
   at least one commit.
2. **Title lints**: the PR's title passes
   `./scripts/commit-lint.sh --header-only` — on this pin it is the ONLY
   place the title is linted before CI sees the squash headline.
3. **Gate parity**: `$WARDEN review --base origin/main --no-comment` exited 0
   on the branch.
4. Check 2 above — PR open, and `--json mergeable` reporting no conflict —
   is the same on every pin and is not part of the fallback.

What the fallback does NOT give you is check 1's first step: nothing here
asks whether a verify artifact names the head being shipped, which is the
failure `$WARDEN ship` exists to refuse. Say so when you report a unit
collected this way.

## 4 · Reconcile as PRs land

Parallel PRs conflict by construction — same test files, same doc tables,
same shard directories. Own the loop:

- Propose a merge order that minimizes rework (smallest blast radius
  first, shared-surface PRs last) and hand it to the founder — **the
  founder merges; you never do**.
- After each merge, for every remaining open PR: merge `origin/main` into
  the branch — keep both sides when the changes are independent, resolve
  honestly when they are not — re-run the policy's `## Verify` commands
  and gate parity, then push. Never force-push; never rebase a pushed
  branch.
- A conflict that changes what a PR means (not just where its lines sit)
  goes back through review, not just through git.

## 5 · Report

Lead with the outcome: per unit — PR link, item status, findings and
dispositions; then the merge order you propose, anything still open, and
anything you deliberately did not dispatch.

## Boundaries

- **Never merges. Never pushes to main. Never force-pushes.**
- Never hand-writes protocol steps into a brief — the brief names
  `nightgate-skills:deliver`; a step worth adding goes into the skill, via
  a tracked item, so every future builder gets it.
- Never dispatches two builders into one worktree, or onto one item, or
  into one scratch directory — the third is as real as the first two and
  the only one with no `git status` to notice it.
- Never reports a unit shipped that has not passed the collection
  checklist with fresh output in hand.
- Bundling items into one PR only on the founder's explicit say-so —
  never self-granted for convenience.
- No subagents in the harness → degrade to the sequential queue
  (`nightgate-skills:ship` / `nightgate-skills:deliver`) and say so; never
  fake the fan-out by interleaving builds in one context.

## Provenance

Earned steps and the incidents behind them. `as_of` is
the last date this protocol was checked against the system it describes.
The `assumes:` block declares this skill's harness assumptions:
the siblings it invokes by name must resolve in the
installed pack (`$WARDEN skills preflight` checks this), and it needs
subagents — degrading as the Boundaries state when the harness has none.

```yaml
as_of: 2026-09-13
assumes:
  skills: [deliver, ship]
  subagents: true
steps:
  - step: "a builder brief names the skill to invoke; it never restates the protocol's steps"
    evidence: commit:f06bd818
  - step: "collection re-verifies the ship tail with fresh output rather than trusting the builder's word: `$WARDEN ship --checks-only` at the unit's current head (verify naming that head, tip attestations clean, title lint, gate parity), plus the PR-open-and-mergeable check no command covers"
    evidence: commit:f06bd818
  - step: "read `$WARDEN progress show` before asking a builder anything, and before running checks-only, whose gate-parity step records a `parity-run` boundary that refreshes the age column"
    evidence: commit:1a4959a1
  - step: "both commands are probed with `--help` before collection leans on them, each probe BRANCHES (a bare probe line discards the diagnostic and leaves failure byte-identical to success), and a failed probe degrades to the four pin-agnostic hand-run checks rather than to nothing — the pack ships from the default branch and no consumer pin carries either command yet"
    evidence: commit:c9abf277
  - step: "pre-flight the pack before dispatch, so a brief cannot name a skill the pack no longer serves"
    evidence: commit:f06bd818
  - step: "each builder gets an isolated scratch directory keyed to its branch — a worktree separates code, not scratch"
    evidence: commit:0cc2a84e
```
