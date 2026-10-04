# Skills Policy

The skills are generic. **Everything project-specific lives in one file:**
`.warden/skills-policy.md` in the consuming repo.

The file sits **inside the gate surface** on purpose — the autonomous session
reads its policy and can never edit it, so policy is read-only to the agent it
governs. `examples/hello-svc` carries the minimal instance as the standing
portability fixture.

**A missing file, or a missing required section, stops the skills.** They
report it and halt. They never guess a project's policy.

## Required sections

Six H2 headings, exact names — the skills read them verbatim.

| Section | Declares |
|---|---|
| `## Verify` | the commands to run per change type (tests, lint, docs), and what a repair attempt counts. The budget itself is `repo.yaml`'s `repair.budget` key, not a line here ([below](#the-repair-budget)): platform cap: 2 attempts — a repo may set fewer, never more. Review runs one round by default; a second, scoped re-review runs only when a round-one finding that counts was repaired. It is handed every round-one finding still open, repaired or not, re-files each one not verdicted addressed as `confirmed` (an unrepaired one included), and makes no repair commit. At the cap, what is open is parked as a tracked item; the run reverts only when a counting finding on the original diff is still open. An attempt, **for a repair round**, is a **change-defect round**: an open finding on the original diff, or anything above LOW. A round whose open findings are all LOW and all on files the repair commits wrote (first-parent, non-merge, and base-branch work subtracted only where the branch CARRIES bytes the base branch itself put at that path, so a base move the branch never merged leaves the path in the set — a file in BOTH ranges reads as repair-written) is the crew reviewing its own scaffolding — it ends the loop and does not count, and `warden round classify` says which from the round manifests, never the builder — on a warden that HAS it; an older pin has no `round classify` and counts every round. A wording finding (its rule declares `judges: wording`, or an `unmapped:` record whose own `judges` field is absent or says `wording`) below every blocking severity does not count a round and is resolved in the same round: fixed only in a round-one repair commit, or filed as a tracked item, and raised or still open in round 2, which makes no repair commit, it is filed as a tracked item; every other finding counts, including an `unmapped:` record whose `judges` field says anything else and an unresolvable rule_id. A warden older than v2.3.0 counts every `unmapped:` record. |
| `## Autonomy scope` | project-specific hard-excludes and skip rules, **on top of** the platform baseline |
| `## Forbidden paths` | project paths the autonomous session must never EDIT, beyond the platform list. Keep in lockstep with the cage's `[gate].forbidden_paths`. `"none"` if empty |
| `## Review charter` | the review lenses beyond the repo's `engine: claude` rules, named and pointed at where each is stated — a rule file; or, for a lens no rule enforces, the `checklist` in `graph.yaml`'s `review.lenses` that `warden graph crew --round N` prints; **or the checklist written inline HERE**. A repo whose `graph.yaml` declares no `review` block loses the SECOND of those three and only the second — `graph crew` exits 2 there, so that key resolves to nothing — while rule files are read from the rules dir either way; so inline is what is left for a lens NO RULE ENFORCES. An inline checklist is read by `pre-pr-review`'s no-crew fallback and is not otherwise resolved, so a repo that DOES declare `review.lenses` should state the checklist there and point here. `"none"` if empty |
| `## Integrations` | optional named hooks, each `name: how to invoke` |
| `## Shipping` | PR body requirements and branch conventions beyond the platform defaults |

### The repair budget

The budget is not declared in this file. It is `repo.yaml`'s typed
`repair.budget` key ([Configuration](Configuration.md)):

```yaml
repair:
  budget: 2
```

The value is a whole number from 1 to the platform cap of 2.
`repo.schema.json` refuses anything else. `warden certify` rung **R-12**
reads the key and fails when it is undeclared, not a whole number from 1 to
the cap, or above the cap, naming the value to add. The key is **required
at Level 3**. `deliver` reads the same key.

A budget sentence in `## Verify` is documentation and gates nothing. R-12
does not read this file for a budget, so a sentence that disagrees with the
key is not caught. Point at the key rather than restating its number.

Why a key: R-12 used to read the budget out of this file's prose. Each
review round of that parser found a new phrasing that passed over the cap,
or a compliant one it refused: synonyms, a declaration split across lines,
invisible characters that display one number while another is read. A
schema-validated integer cannot be misread that way.

**On a pin bump.** A warden older than the key refuses a `repo.yaml` that
declares it, because the schema accepts no unknown key. A warden with the
key fails R-12 without it. Add the key in the same PR that moves the pin.

### The platform baseline is not relaxable

`## Autonomy scope` adds to it; nothing subtracts. The baseline lives in the
`autonomous-run` skill: no epics, no gate-surface edits, no production
migrations or deploys, no credentials, no human-decision items, no open-ended
spikes, **one item per session**.

### The four hooks

Named in `## Integrations`. **A hook that is absent is skipped — never
improvised.**

| Hook | Owns |
|---|---|
| `plan-gauntlet` | an advisory pre-plan council |
| `dev-executor` | the skill that owns spec → implement → review → commit |
| `review-crew` | an extra review pass before `pre-pr-review` |
| `memory` | how `review-queue` records the runs' narrative |

Narrative memory belongs to `review-queue` alone. Hooks or no hooks, the
autonomous session writes nothing beyond its own run bookkeeping —
`.run-summary` (the handoff), `.run-progress` (the phase checkpoint) and
`RUN-ABORT.md` (why it stopped) — plus tracker comments. A fourth name shows up
beside them: `.run-resume`, which the **cage runner** writes before the session
starts and the session only reads. All four are worth knowing when you write
`## Forbidden paths`: they land in the worktree.

## Optional sections

Declared if the project wants them. **Omitted, behaviour is exactly what it was
before the section existed** — a repo is never given a new obligation by a
platform pull.

### `## Build disciplines`

The gate enforces what a diff must satisfy. It says nothing about *how* the
agent got there — and "implement to the Definition of Done, ≤2 repair
iterations" is an outcome and a budget with no method between them. This
section names the methods that bind during BUILD and repair.

```markdown
## Build disciplines

- test-driven-development: any change under src/ — failing test first, watched failing.
- systematic-debugging: any red test or unexpected behavior — root cause named before a fix.
- verification-before-completion: before any "passes"/"done" claim — fresh full-command output.
```

Each discipline is **one markdown list item**; wrap continuation lines indented
under it. Everything else in the section is prose the policy may use to explain
itself, and is ignored. (An earlier parser accepted any `word: text` line, so a
wrapped sentence ending `...without\n  asking: generated code` was read as
declaring a discipline called `asking`. Prose wraps; a list item does not.)

Four names are recognized, and each obliges something specific:

| Discipline | Obligation |
|---|---|
| `test-driven-development` | the failing test comes first, and is *watched* failing — a test that has never failed proves nothing about what it tests |
| `systematic-debugging` | no fix before the root cause is named; a fix that treats a symptom is a failure even when the symptom disappears |
| `verification-before-completion` | no completion claim without fresh output from the full command in hand — never a prior run, never an inference from a partial check |
| `design-approval` | no code before the human partner approves what you intend to build. Pairs with `deliver`'s Design phase, where a HIGH-tier change records a `warden decide` decision before Build — the discipline is the human sign-off, the phase is where it lands |

`warden certify` check **R-09** fails a policy declaring anything else, and
names the valid set in the message. A name with no scope after the colon fails
the same way — a discipline nobody can tell the extent of cannot be honored.
The failure mode this prevents is silent: a repo declaring `root-cause-first`
believes it bound a discipline while nothing ever reads it.

**Distinct from the `dev-executor` hook**, which names *who* builds.
Disciplines name *how*. A repo that points `dev-executor` at an agent still has
no stated method for that agent's repair loop.

The names match skills published by
[obra/superpowers](https://github.com/obra/superpowers) (MIT), installable as
`superpowers@claude-plugins-official` alongside this pack. Install it and the
agent gets the full protocol for each; without it the obligation above still
binds. The platform's contract is the table, not the third-party skill. Why
these four and what was deliberately refused:
[docs/design/methodology.md](../design/methodology.md).

**Omit the section and BUILD behaves exactly as it did before it existed.** A
repo is never given a new obligation by a platform pull — and because the
policy file lives inside the gate surface, the agent it governs cannot add the
section on its own either.

## The cage connection

`autonomous-run` and `review-queue` reference the project's **cage** — the
unattended runner enrolled via `cage.toml`. The run branch prefix, the cage
directory (`reports/`, `ledger.csv`), and runtime forbidden-path enforcement
all come from that enrollment.

A project without a cage can still run `pre-pr-review`, run `review-queue`
against PRs alone (no reports or ledger to gather), and run the retro from PRs
and the tracker alone — it says so in its report. `autonomous-run` assumes the
cage; it is invoked by the runner.

The cage is also **tracker-bound**: `autonomous-run` takes its work from
`bd ready` and has no `--no-tracker` mode, because an unattended run has no
prompt to take a task from. `deliver` and `ship` do have one; which
commands need the tracker is tabled in
[Skill Pack](Skill-Pack.md#which-commands-need-a-tracker).

---

Next: [Skill Pack](Skill-Pack.md) · [The Cage](The-Cage.md) ·
[Adopting](Adopting.md)
