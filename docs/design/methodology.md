# The craft layer — design of record

Our skills declare a *workflow*: intake → plan → build → verify → review →
gate → PR. Until now they said nothing about **craft** — the method an agent
follows inside BUILD and repair. `deliver` said "implement with tests per the
repo's Definition of Done, ≤3 repair iterations": an outcome and a budget,
with no method between them. A budget with no method is thrashing with a
stopping rule.

`.warden/skills-policy.md` gains an **optional** `## Build disciplines`
section (contract in [docs/wiki/Skills-Policy.md](../wiki/Skills-Policy.md)). Omitted, everything
behaves exactly as it did before the section existed — a platform pull never
hands an enrolled repo a new obligation.

## Why a section and not a dependency

The disciplines are named after skills published by
[obra/superpowers](https://github.com/obra/superpowers) (MIT, Jesse Vincent),
installable as `superpowers@claude-plugins-official`. It installs cleanly
beside `nightgate-skills` and `workspace-skills` — plugin skills are
namespaced, so nothing collides.

But the platform's contract is **the obligation table, not the third-party
skill**. Install superpowers and an agent gets the full protocol for each
discipline; without it the obligation still stands and still binds. We depend
on a vocabulary, not on someone else's release cadence.

## What we adopted, and the evidence

Each of these was chosen against a failure this platform's own construction
actually produced — not from the README:

| discipline | the failure it would have caught |
|---|---|
| `systematic-debugging` | an exit-2 failure was diagnosed as SSH rate limiting and a retry was shipped. The root cause was that zsh does not word-split unquoted variables, so the test harness passed one argument where it meant two. The symptom fix was defensible; the diagnosis was invented. |
| `test-driven-development` | tests were shipped that passed for the wrong reason. A test never watched failing proves nothing about what it tests. |
| `verification-before-completion` | `pytest 2>&1 \| tail -1 && git commit` reported success from `tail`'s exit code. A failing suite was committed and pushed under a green claim. |
| `design-approval` | the platform built HIGH-tier gate surface — rules, schemas, the mechanical checker, the ladder — with no design stage; E4, "the missing front half," is that gap named. This one is adopted against a STRUCTURAL absence rather than a single incident: it is the approval GATE — no code before the human approves the intent — NOT the brainstorming workflow (refused below), and it lands in `deliver`'s Design phase, where a HIGH-tier change records a `warden decide` decision before Build. |

`writing-skills` is adopted for authoring *this pack* — a house style for
skill prose, not a runtime discipline, so it earns no policy section.

## What we refused, and why — settled, reopened only by a written decision (see *Superseded* below)

- **`requesting-code-review` / `receiving-code-review`.** Ours ends in
  `warden attest write`: an attestation bound to head SHA, base SHA, and
  `rules_version`, ingested into review memory. Theirs dispatches a reviewer
  and returns prose. Adopting it would mean two review protocols where only
  one leaves an evidence chain, and the gate would still demand ours.
  Worth recording: it independently arrives at "the reviewer gets crafted
  context, never your session's history" — the same insight as our
  independent-context reviewer. Convergence is a good sign for the design;
  it is not a reason to run both.
- **`brainstorming` / `writing-plans` / `executing-plans`.** These overlap
  `intake`, `warden plan`, and `deliver` end to end. Two competing workflow
  protocols is the principal risk in adopting any external pack, and it is
  exactly what the night-shift refactor removed when it deleted the
  duplicated VERIFY and SHIP phases. One workflow protocol, ours. (The
  approval GATE `brainstorming` carries — no code before the human approves
  the intent — IS adopted, as the `design-approval` discipline above: a gate
  is not craft, and this one is admitted on the evidence it leaves — a
  provenance-stamped `warden decide` record — not on any hard stop it enforces;
  the workflow around it stays refused. See *Superseded* below.)
- **`using-git-worktrees`.** The cage enrolls worktrees and enforces
  isolation outside the model. A skill that asks an agent to please use a
  worktree is a weaker version of a hard stop we already have.
- **`subagent-driven-development` / `dispatching-parallel-agents`.** The
  agent organization is declared in `graph.yaml` and validated by
  `warden graph validate`. Fan-out belongs in the declared graph, where it
  can be checked, not in prose an agent interprets at runtime.

The line: **we take craft, we do not take workflow.** Anything that tells an
agent *how to do the work well* is a candidate. Anything that tells it *what
order to do work in*, or *who reviews it*, collides with a protocol that
already emits evidence — and the one with the evidence chain wins.

### Superseded — the goal changed, so the `brainstorming` approval GATE reopened

The refusals above were settled when Nightgate was a **merge gate**: the value
was the deterministic check at the END, so a front-half protocol was pure
overlap with `intake` / `warden plan` / `deliver`. v1.0's replan changed the
goal — **F4** found the lifecycle had a hole at BOTH ends (no design stage, no
architect stage, no deploy), and **E4, "the missing front half,"** is the part
this reopened: a HIGH-tier change reached Build with nothing on record about
the intent.

What reopened is ONE thing: the approval **GATE** `brainstorming` carries — *no
code before the human approves what you intend* — adopted as the
`design-approval` discipline (in *What we adopted* above), landing in
`deliver`'s Design phase and a `warden decide` record.

Be honest about WHY it is adopted, because the two easy answers are both
wrong. It is **not craft** by this doc's own test three paragraphs up: an
approval gate is an ORDER rule (approval *before* code) and a WHO rule (*the
human* approves) — by that criterion it is workflow, not craft. And it is
**not an enforced hard stop**: nothing machine-checks that a human actually
approved — R-09 validates only that the discipline is *declared*, and an agent
can author the `warden decide` record itself and proceed to Build. Claiming
*hard stops live outside the model* for this gate would be an overclaim; the
approval act is not one.

What it IS admitted on is the platform's actual currency — **evidence and
provenance**. The refused planning workflows emit prose an agent interprets;
`design-approval` lands in a MECHANICAL artifact bound to commit +
`rules_version`, its provenance stamped by warden rather than supplied by the
author (`warden decide`). That is precisely the gap E4 named — *nothing on
record about the intent* — and the one thing the brainstorming WORKFLOW cannot
leave behind. The craft-not-workflow line is not bent to fit it; the line does
not decide gates, and this gate is admitted because it leaves the evidence the
platform runs on, not because it enforces anything the model cannot rewrite.
Recorded here so the reopening is a written decision, not a silent drift.

**What stays refused, unchanged by the goal change:**

- `brainstorming` / `writing-plans` / `executing-plans` AS WORKFLOWS — the
  order-of-work ceremony that overlaps `intake` / `warden plan` / `deliver`
  and emits no evidence chain. Only the GATE reopened; the two planning skills
  did not move, and the brainstorming workflow around the gate did not either.
- the two `-code-review` skills (ours ends in an attestation bound to
  head/base SHA + `rules_version`; theirs returns prose),
- `using-git-worktrees` (the cage enforces isolation outside the model),
- `subagent-driven-development` as a workflow (fan-out belongs in the declared
  `graph.yaml`, where it is checked).

None of those turns on the merge-gate-vs-front-half goal; each is refused for a
reason the goal change does not touch.

## Measurement — the instrument, and why there is no finding yet

`cage measure <ledger.csv> [--since YYYY-MM-DD]` turns the ledger's
`attempt` and `pr` columns into the two numbers the question needs: mean
repair attempts and first-pass rate, optionally split into before/after arms.

Three decisions in it are load-bearing, and each came from running it against
the real ledger rather than from designing it:

- **Landing is keyed to the PR column, not an outcome word.** The first
  version counted `outcome == "shipped"`. The runner emits `done`,
  `timeout`, `usage-limit`, `push-denied`, `failed(N)`, `FORBIDDEN-PATH`, `skipped`, `usage-skipped` — never
  `shipped` — and `.run-summary` can override the value outright. That
  metric would have read 0% forever and looked like a result.
- **Skipped nights are excluded from the repair statistics.** A night that
  found no eligible bead attempted nothing; counting it either way moves a
  rate that describes work it never did. It gets its own count instead.
- **An empty arm reports NO DATA, never 0.0.** "Not measured" and "measured
  as zero" must not look alike, and a 0% after-arm is precisely how a
  measurement gets mistaken for a refutation.

**The finding is not available.** Current corpus: 2 worked nights carrying
the `attempt` column at all, and **zero nights run under declared
disciplines** — the section was declared the same day. Splitting what exists
yields n=1 per arm, which the tool correctly refuses to treat as a
comparison.

What would make it answerable: ~10 worked nights per arm on the same repo.
Even then the confounder stands and must be stated with the number — task
difficulty is not controlled for, beads are not randomized into arms, and
the platform changes underneath the measurement. This is a direction to
watch, not an experiment with a control.
