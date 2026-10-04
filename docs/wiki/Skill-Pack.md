# Skill Pack

Judgment protocols are shared; project facts are config. The pack ships nine
generic skills from this repo's plugin marketplace, added by its GitHub URL,
and each consuming repo declares its specifics in one policy file the skills
read.

Install: [Installation](Installation.md) section 3. The policy file:
[Skills Policy](Skills-Policy.md).

## Start here

```
/nightgate-skills:ship  add rate limiting to the public API, 100 req/min per key
```

`ship` runs `intake` then `deliver` per item, reporting between them. Every
other skill is invocable directly — `/nightgate-skills:pre-pr-review` — but you
rarely need to.

## The nine

| Skill | Protocol |
|-------|----------|
| `ship` | **the only one you invoke**: a requirement in plain language becomes tracked items (or, under `--no-tracker`, an ordered task list in its report), then PRs. Runs `intake`, then `deliver` per item, reporting between them |
| `intake` | requirement → tracked items with acceptance criteria, dependencies, and a reported execution order. Writes no code |
| `deliver` | **one item, end to end**: ready → merge-ready PR. Plan packet with priors → build → verify → review crew → `pre-pr-review` → gate parity → PR with its evidence chain |
| `pre-pr-review` | dispatches the crew `warden graph crew --round N` prints, in that order: one independent subagent per declared role, each fed what the graph's edge into it declares — so a judging role receives **only** the findings and tries to refute each → triage → `warden attest write`. CLEAN is required before a PR opens |
| `autonomous-run` | the unattended session inside the cage: pick ONE eligible item, build, survive the gauntlet, push, PR, `.run-summary` handoff. Platform hard-excludes are not relaxable by any policy |
| `review-queue` | the founder's walk through each run's evidence to a per-PR decision. Merges only on the founder's explicit word |
| `retro` | weekly, evidence-cited proposals over the review corpus plus the guardrail gap. Proposes only |
| `rule-advisor` | on-demand rule-set audit: `mine` → `rules recommend --backtest` → `rules lifecycle` → drafts real rule files with a backtest each and a gate run proving the tree still passes → a PR |
| `orchestrate` | the parallel counterpart to `ship`: partitions independent items into PR-sized units, dispatches one worktree-isolated builder per unit — each invoking `deliver` — then owns collection and the merge-order/conflict loop |

**Who calls whom.** `ship` → `intake`, `deliver`. `deliver` → `pre-pr-review`.
`autonomous-run` → `deliver`. `orchestrate` → `deliver`, one per builder.

Two things nothing in the pack does: **merge**, and **relax the platform
baseline.** Every skill that could open a PR stops there.

## Which commands need a tracker

The tracker is beads (`bd`). The gate does not need it, and neither does
`rule-advisor`; `pre-pr-review` needs it only to file what it does not fix.
Five skills always need it. `deliver` and `ship` need it by default and run
without it under `--no-tracker`.

| Command | Needs the tracker? |
|---------|--------------------|
| `warden`, every subcommand | **no**. `--bead` on `attest write` and `decide record` is optional provenance |
| `pre-pr-review` | **partly**: the review runs without it, but a backlog finding, an undeclared lens's finding and a finding parked at the cap are each filed as a tracker issue. Under `deliver --no-tracker` those go to the PR body's `## Follow-ups` |
| `rule-advisor` | **no** |
| `deliver` | **yes by default**; `--no-tracker` takes the task from the prompt |
| `ship` | **yes by default** (it runs `intake`); `--no-tracker` decomposes in its report and runs `deliver --no-tracker` per task |
| `intake` | **yes**: filing tracked items is its output |
| `orchestrate` | **yes**: it partitions items read from the tracker |
| `review-queue` | **yes**: a rejection is commented on the bead, and the bead closes on merge |
| `retro` | **yes**: it reads `bd list` for escape suspects and files a tracker issue per accepted proposal |
| `autonomous-run`, and so the cage | **yes, with no `--no-tracker` mode** |

```
/nightgate-skills:deliver --no-tracker add a --json flag to the status command
/nightgate-skills:ship --no-tracker add rate limiting to the public API
```

Under `--no-tracker` the protocol is the same, gates and review rounds
included; only where the work is recorded moves. The commit subject takes
the no-item form, `(no-bead: <reason>)` where the platform's
`scripts/commit-lint.sh` is the validator, and the commit body quotes the
task on a `Task:` line. Follow-ups that would have been filed go under the
PR body's `## Follow-ups` heading. Without the flag, a tracker that cannot
be read stops the run and names the flag; neither skill switches modes on
its own. The skill pages carry the full rules.

**The cage stays tracker-bound.** An unattended run has no prompt to take a
task from: `autonomous-run` selects with `bd ready`, claims with
`bd update --claim`, and leaves its trail as bead comments, and the cage's
rendered permission profile allows `bd` for it. A repo without beads runs
`deliver` and `ship` interactively; a cage enrolled there has no way to
select work.

## What a consuming repo declares

One file, `.warden/skills-policy.md`: six required sections, plus the optional
`## Build disciplines` section that names which methods bind while an agent
builds and repairs. Full contract: [Skills Policy](Skills-Policy.md).

## Why a marketplace

Claude Code resolves *project* skills from the project cwd only, so the first
unattended worktrees could not see shared skills at all. User-scope plugin
skills resolve everywhere — any checkout, any worktree, headless `claude -p`
sessions included.

The marketplace is added at the tag `platform.pin` names (`<url>#v2.3.0`),
so the pack a machine serves is the pin's and `claude plugin marketplace
update nightgate` does not move it. `warden skills pin`, which v2.3.0 carries,
refuses a machine whose marketplace ref or installed pack version disagrees
with `repo.yaml` ([Installation](Installation.md) section 3). A
pin bump re-adds the marketplace at the new tag ([Installation](Installation.md)
section 3), then `warden skills preflight --pack <installed cache>` checks that
what the harness serves is what the skills assume.

## Two hard-won rules about briefs

**A dispatched builder is given the skill name, never a paraphrase of its
steps.** `orchestrate`'s brief says *invoke `nightgate-skills:deliver`* and
stops. A hand-written protocol paraphrase is where the attestation-commit step
went missing four PRs in a row.

**Degradation is observable, never silent.** `orchestrate` falls back to
`ship`'s sequential queue when the harness serves no subagents, and says so.

## Provenance blocks

Each `SKILL.md` ends with a `## Provenance` block: `as_of` (the last date the
protocol was checked against the system it describes), and `steps` citing the
incident or corpus class each earned step answers. The same block declares the
skill's **harness assumptions** — `assumes: skills:` lists sibling skills it
names, `assumes: subagents: true` says it launches them.

`warden skills preflight` resolves those declarations against the pack it is
pointed at and fails closed with the names it could not resolve. A declared
`subagents: true` is surfaced as *assumed*, never verified — the harness is not
visible from there, so the skill text owes an observable degradation path
instead.

## Where the BMAD suite fits

Workspace-level BMAD skills are the first *implementations* of the policy
hooks: a consumer points `dev-executor` at `bmad-dev-auto` and
`review-crew` / `plan-gauntlet` at `bmad-party-mode` parties. They are
workspace assets, not platform code.

## Versioning

The pack rides this repo's releases. Skills are prose contracts, not code — the
enforcing layers (the warden gate, the cage runner) stay pinned by SHA via
`platform.pin`, so a stale skill can degrade judgment quality but **cannot
bypass enforcement**.

A change to a skill's contract bumps the plugin version in
`skills/nightgate-skills/.claude-plugin/plugin.json` — the one machine-readable
signal an installer sees. Two kinds qualify:

- the **invocation surface** — a skill renamed or removed, so an existing
  invocation stops resolving;
- the **judgment contract** — what a skill gathers, proposes, or forbids, so an
  unchanged invocation returns materially different work.

**THE TABLE BELOW IS NOT ONE ROW PER VERSION, and that is the ruling rather
than a backlog.** It logs the versions a PINNED CONSUMER HAS TO ACT ON — the two
kinds above, reaching the invocation surface or the judgment contract. A patch
that corrects prose in one skill, restates a claim, or rejoins a stranded
pronoun bumps the manifest (the pack-version invariant requires it whenever a
skill's text moves at all) and owes no row, because there is nothing for a
consumer to do about it. So a version missing here reads as "nothing you must
know", never as drift, and three consecutive gaps are not a table falling
behind. `tests/test_docs.py::test_the_pack_changelog_states_which_versions_
it_logs` holds this paragraph in place, because without it the same three gaps
look exactly like the other case failing.

What the table is NOT is a substitute for the manifest: the newest row may be
older than `plugin.json`'s version and usually is.

| Version | Changed |
|---|---|
| 0.23.0 | The pack is `nightgate-skills`, served by the `nightgate` marketplace, and every skill is invoked as `nightgate-skills:<name>`; it was `agentops-skills` from the `agentops` marketplace. Nothing a skill gathers, proposes or forbids moves. A consumer edits each `agentops-skills:<name>` it wrote down (its `graph.yaml` `impl:`, its cage's `prompt.md`) and reinstalls: [release notes v3.0.0](../design/release-notes-v3.0.0.md). **Pack/pin skew**: the new names are served from the v3.0.0 tag on; a pin below it serves the old ones (Installation section 3) |
| 0.22.20 | `deliver` and `ship` take `--no-tracker`: the task comes from the prompt, the commit subject takes the no-item form and the body a `Task:` line, and follow-ups go under the PR body's `## Follow-ups` heading. Without the flag, a tracker that cannot be read stops the run and names the flag. `autonomous-run` states the cage is tracker-bound. A repo with no beads can now run both skills; one with beads sees no change |
| 0.22.19 | `retro`'s Gather table names the `REVIEWER SEATS` section `warden memory stats` now prints — per seat `graph.yaml` declares, the findings it first raised, their precision floor and the share no other seat raised in the same round — as the evidence a **crew composition** proposal cites. Read-only: a seat is not a rule and nothing promotes or pauses one. On a pin predating the section the header is absent, which the Gather step already reads as unknown, never as clean |
| 0.22.14 | `deliver`'s Plan step passes `warden plan --path` once for each file the item names, so the packet's risk tier is classified rather than `UNKNOWN`, and its Build step reads an `Evidence:` line's pass count off `warden verify`'s summary line (`PASS — pytest: N passed, M skipped, K failed` for each pytest scope), citing the command's own output when that line prints no counts. **Pack/pin skew, and it is EVERY pin today**: no tagged release carries `plan --path` or the summary-line counts. `deliver` probes `plan --help` for `--path` and runs without it on such a pin; the counts are simply absent there. |
| 0.22.10 | `pre-pr-review`'s reviewer brief and `deliver`'s Small-PR contract say what `warden round classify` now reads off an `unmapped:` record: it names no rule to say what it judges, so it is a wording finding that does not count toward a repair round when its `judges` field (optional on a finding in `attestation.schema.json`) is absent or says `wording`, and counts on any other value — `behaviour` is what a reviewer writes; a declared rule's finding is what the rule's `judges:` says, whatever the record carries. **Pack/pin skew**: a warden predating the field refuses a finding carrying `judges` at `attest write` (its finding object is closed) and its `round classify` still counts an unmapped record with no field as behaviour, so on such a pin file no `judges` and expect every unmapped record to count. |
| 0.22.7 | `pre-pr-review` describes the proportionate tier's proof as Python-only: every changed file identical once docstrings are stripped, nothing under the gate's machinery. The Markdown arm is removed from the runtime and its `proportion.prose_roots` key admits nothing (the schema keeps the key so an older `repo.yaml` still parses); Markdown always takes the numbered rounds, and a `repo.yaml` still declaring the key is refused with a message naming the removal. Pack/pin skew, one direction: on a platform pin predating the removal, a `repo.yaml` declaring `prose_roots` still earns LIGHT for a Markdown-only range that this pack describes as Python-only, so move the pin forward before adopting this pack. The other direction is safe: an older pack's Markdown paragraph describes a tier this pin never grants, and the declared key is refused before any round runs. |
| 0.22.1 | `pre-pr-review` lints the **PR title** before the PR exists, as the last step of the round it owns: `./scripts/commit-lint.sh --header-only`, the same validator CI runs, against a 100-character budget for what you write (the forge appends ` (#N)` and the validator strips it first). A squash merge lands the title verbatim as the commit header, so CI's "commit messages" check has always validated it — and nothing prompted a builder to check it locally, so the first CI round was spent discovering it. Two PRs were refused this way on one day with every commit message clean (#289). **Pack/pin skew**: none in either direction — no warden command, key or field moves, the validator is the repo's own shell script and has carried `--header-only` since #142, and `warden ship` already ran this check before opening a PR (a pin carrying `ship` was covered on that path and on no other). A repo that keeps its validator elsewhere passes the path, exactly as `ship --commit-lint` takes it |
| 0.22.0 | `pre-pr-review` states the third condition `warden attest check` now enforces (#278): a commit no tip attestation covers is refused unless every path it touches sits under `.warden/memory/attest/`. The protocol change is in the ORDER — the shard commit carries the shard and nothing else, and the repair commit goes BEFORE the round that verifies it, never after. It also states the verbatim re-file rule as ENFORCED from round 2 on (#278): `attest write` refuses a payload whose record carries `fixed`, `dismissed-with-reason` or `refuted` for a claim no earlier round committed to this branch. **Pack/pin skew, both directions**: on a pin PREDATING either check, both are protocol only and a builder who commits a doc fix after the last round discovers nothing; on a pin CARRYING them, a builder following the 0.21.x ordering meets a red required gate and a refused `attest write`. Budget a round for the re-review rather than finding it in CI: 16 of the 39 PRs merged before this shipped would have been refused |
| 0.21.0 | `pre-pr-review` and `deliver` carry the terminal **closure round** (#272): a round minted past the repair cap, one declared role, whose payload may only RE-FILE a claim a capped round already committed to this branch, with every record `fixed` or `dismissed-with-reason`. It is what un-strands a branch whose repair at the cap left no attestable head. **Pack/pin skew**: a warden predating `review.closure` resolves no closure terms, so the round cannot be attested there and the branch stays stranded; a `graph.yaml` declaring `closure` on such a pin is refused by its schema. Row added late — it was missing when 0.21.0 shipped |
| 0.20.1 | `retro`'s Gather step names every section `warden memory stats` prints, by the header the renderer prints, each with the proposal it feeds — LENSES and DISPATCHES among them, which nothing in the skill had ever named. The paragraphs restating what a rendered line means are gone: the render says what a line MEANS, the skill says which PROPOSAL the section feeds. What survives is the reading the render cannot give — an ABSENT line's two causes (an older pin, or nothing in the corpus reaching it), which is still never read as clean. The section list is derived from warden's renderers by a guard, so a tenth section reddens the pack rather than going unread. No warden command, key or field moves, so any pin that runs 0.20.0 runs this |
| 0.20.0 | `pre-pr-review` and `deliver` dispatch the crew `warden graph crew --round N` prints — those roles, in that order — instead of naming a roster themselves, and WHERE THAT COMMAND RESOLVES A CREW the lens checklists come from its `lenses` (graph.yaml's `review.lenses`) rather than from policy prose. Round 2 is whatever round 2 declares (the scoped re-review alone on this repo), where both skills said the re-review THEN the examiner: a builder following the old prose is now REFUSED, because `warden attest write --review-dir` requires the roster to be exactly that round's declared crew. **Pack/pin skew**: a warden predating `graph crew` fails the probe, and a repo that declares no `review` block resolves no crew. `pre-pr-review` is the one that falls back — one finder over the diff, then one judge over its candidates — and `deliver` delegates its dispatch to that skill rather than carrying a no-crew branch of its own. **The LENSES fall back too, and to a different place than the roster**: the command prints `lenses` only where it resolves a crew, so with none the extra checklists come off the policy file's `## Review charter`, each lens at the place that section says it is stated, carried into the finder's dispatch exactly as a printed lens would be. The `engine: claude` rule bodies are unaffected either way — they come from the repo's rules dir. What the lens fallback protects is the narrow class the command is the only other source of: a charter lens no rule enforces, whose checklist would otherwise reach no reviewer at all. No shard records which roster source was taken: the attestation schema is closed, so a roster written under the fallback is stamped `unverified-roster` like any other roster warden compared against nothing. `warden ship` refuses a builder-asserted tip shard only where graph.yaml DECLARES a `review` block — a consumer whose graph.yaml carries no `review` key ships as it did before, including one `warden graph validate` refuses. One exception, the carve-out Graph-Layer.md states: a graph.yaml warden cannot decode, cannot parse, or cannot read at all refuses the WHOLE ship tail (exit 2) whatever keys it carries, because a document that does not parse cannot be asked which keys it has. `warden attest write --review-dir` is stricter than both — it resolves the crew before it can answer the key question, so it refuses documents ship ships; that divergence is a DECIDED asymmetry, ruled in the decision store (`warden decide list`) because the flag is the caller asking for a check that must fail closed when it cannot be performed, and it is not a boundary to read off this row |
| 0.19.2 | `retro` **and** `rule-advisor` draft a promotion record under `.warden/memory/promotions/` beside the promote artifact of a rule a retro proposed, naming that artifact and that retro, because certification S-04 now counts only a promotion a retro proposed, over the bar, with its backtest. Both skills, because an accepted retro proposal is drafted by `rule-advisor`: a rule that arrives there with no record ships a promote artifact S-04 does not count. `rule-advisor` writes no record for a rule it proposed itself. One new record file per promotion; no key or cap moves |
| 0.19.1 | `autonomous-run` stops (revert, record, stop) when a step cannot complete within `repo.yaml`'s `repair.budget`, the key `deliver` reads, where it named the policy-line budget term that 0.19.0 retired and left defined nowhere. The Provenance step for the one-round default, in `deliver` and in `pre-pr-review`, says round 2 runs when a round-one finding that counts was repaired and is handed every round-one finding still open, where it read as reviewing only the repaired finding. No key, cap or payload field moves |
| 0.19.0 | `deliver` reads the repair budget from `repo.yaml`'s typed `repair.budget` key (a whole number from 1 to the platform cap of 2), not from a line in the policy's `## Verify`; with no key it takes the cap and says so in the attestation. `warden certify` R-12 reads the same key, fails when it is undeclared (required at Level 3) or above the cap, and no longer reads policy prose for a budget. **Pack/pin skew**: a warden older than the key refuses a `repo.yaml` that declares it, so add the key in the same PR as the pin bump; on an older pin this pack finds no key and takes the cap |
| 0.18.2 | `pre-pr-review` states that every path in a finding's text is repo-relative. `warden attest write` rewrites a path under any worktree of the repository to repo-relative and refuses any other home-directory path, naming the finding and field; a pin whose warden predates that check accepts the path as written. No payload field moves |
| 0.18.1 | `deliver` and `pre-pr-review` state that round 2 makes no repair commit: no round follows it to review one and no payload follows it to file one in, so an open wording finding that `warden round classify` does not count, raised or still open in round 2, is filed as a tracked item and recorded `dismissed-with-reason` naming it in round 2's payload. The same-round fix in the repair commit, filed `fixed` in the next payload, is round one's alone. The 0.18.0 row's round-2 clause now says what 0.18.0 shipped: round 2 is handed every round-one finding still open and re-files each one not addressed as `confirmed`. No warden command or field moves, so any pin that runs 0.18.0 runs this |
| 0.18.0 | `deliver` and `pre-pr-review` default to ONE review round: the code-reviewer carries every `## Review charter` lens as a checklist inside its one dispatch, then the cross-examiner. A second round runs only when a round-one finding that `warden round classify` counts was repaired, and it is a scoped re-review with the cross-examiner over its candidates, handed every round-one finding still open, repaired or not; its payload re-files each one not addressed as `confirmed`, because `warden round classify` reads only that payload. The platform cap drops from 3 rounds to 2 (`deliver`'s cap and `warden certify`'s R-12 ceiling). At the cap, or at a scaffolding stop, what is open is parked as a tracked item and recorded `dismissed-with-reason` naming it; revert still wins when a counting finding on the original diff is open. A charter lens no rule enforces files its findings as `unmapped:<the lens's slug>`, so the lens stays countable once rosters no longer carry a dispatch per lens. **Pack/pin skew**: a warden older than this change still certifies a declared budget of 3 and still offers another scoped re-reviewer at a scaffolding stop; follow the pack. A consumer policy declaring `Iteration budget before a run reverts: 3 attempts` fails R-12 once its pin moves past this change |
| 0.17.1 | `deliver` and `pre-pr-review` resolve an open wording finding that `warden round classify` does not count (its rule declares `judges: wording`, below every blocking severity) in the SAME round: fixed in the repair commit and filed `fixed`, or filed as a tracked item and recorded `dismissed-with-reason` naming it. It does not wait for the cap and is not a scaffolding-stop adjudication, because a finding that does not count may never reach either. **Pack/pin skew**: a warden that predates `judges:` counts every finding, so on it the step never applies and nothing else changes |
| 0.17.0 | One spelling of warden, pack-wide: every fenced and inline invocation in every skill is `.warden/bin/warden`, the consumer contract [Installation](Installation.md) states (a consumer never installs warden globally). `deliver`, `orchestrate`, `intake`, `retro` and `rule-advisor` spelled it bare; reproduced in the platform's own worktree, which HAS the commands, `warden ship --help` exits 127, so `orchestrate`'s probe fence read BOARD=no CHECKS=no and `deliver` took its hand-run fallback on a checkout that had the command — inside a probe, `>/dev/null 2>&1` swallows `command not found` and the wrong spelling silently selects a branch. The probe fences of `deliver`, `orchestrate` and `pre-pr-review` are now pinned STRUCTURALLY — both arms present, distinct, and the right way round — where the first guard pinned the left half of the `if` and passed an inverted or half-deleted fence. `pre-pr-review`'s judgment contract also moves: every reviewer NAMES the changed files it read, warden stamps each roster entry's `outcome` (`reviewed-clean`, `reviewed-findings`, `no-review`) from the artifacts under `--review-dir`, a lens that comes back `no-review` is re-dispatched ONCE and recorded either way, and the pin is probed for `outcome` before the field is written. **Pack/pin skew runs both ways here.** A warden OLDER than the field refuses it, which is what the probe is for. The other direction is silent: a consumer on a NEW warden with a 0.16.x pack never tells a clean reviewer to name the files it read, so every clean review is stamped `no-review` — nothing is refused, and `memory stats` counts real reviews as non-reviews until the pack is updated to 0.17.0. Update the pack in the same step as the pin |
| 0.16.3 | The ship tail is wired to the commands that enforce it. `deliver`'s Ship is `warden ship --base origin/main --title ... --body-file ...` — one command running six steps that each print what they checked and refuse rather than pass: a verify artifact naming THIS head, tip attestations committed and clean, the PR title linted BEFORE the PR exists, gate parity, a push the remote confirms, then the PR. `pre-pr-review` records `warden progress record repair-committed` after each repair commit — the one loop boundary `round new` and `attest write` do not pass for you. `orchestrate`'s collection reads `warden progress show` first and re-runs `warden ship --checks-only` at the unit's current head in place of three hand-run checks, keeping the PR-open-and-mergeable check, which no command covers. **Pack/pin skew, and it is EVERY pin today**: `warden ship` and `warden progress` landed in one commit (#201), so no tagged release carries either — a consumer installing 0.16.3 against any existing pin has neither command until it raises its warden pin. Each of the three therefore probes the SUBCOMMAND with `--help` before relying on it and name a fallback that is not nothing: `deliver` falls back to the hand-run tail (re-verify at HEAD, lint the title before the PR exists, push by literal branch name, `gh pr create`), `orchestrate` to the four pin-agnostic collection checks it has always carried — which do NOT ask whether a verify artifact names the shipped head, the one thing only `ship` refuses — and `pre-pr-review` records no boundary and says so. A pack pinned to 0.16.2 against a warden that HAS the commands runs the old hand-run tail with no probe: the fix there is to update the pack, not the warden (#203) |
| 0.16.2 | `deliver`'s provenance step carries the #200 RE-RULING instead of the condition it replaced. The 2026-09-02 ruling's trigger — "reopens on ~20 PRs of per-lens data after #184" — had fired and been answered, so a session reading the old step would wait for a threshold that is already behind it; the step now states that class, size and risk-tier tiering would each have skipped 0 of the 16 attributed PRs, and that the successor trigger is 20 dispatches of one lens on one diff class. A pack pinned to 0.16.1 runs the same depth — the ruling changed no lens set and no budget — and differs only in which reopen condition it reports (#200) |
| 0.16.1 | `retro`'s tag-reconcile step names FOUR unreadable-declaration labels where it named one. `tags.ceiling_status` merges complaints from four sources, and two of the three renderers that print them prefixed every one of them `CEILING UNREADABLE`, asserting a declaration that may not exist; one shared labeller now answers which declaration each speaks for, so the retro — the ceiling's only human consumer — must be told to read `TAG CEILING UNREADABLE`, `VOCABULARY UNREADABLE`, `DECLARATION BLOCK UNREADABLE` and `RECEIPTS UNREADABLE` apart. A retro pinned to 0.16.0 against a warden carrying the change still finds the block each complaint names in its payload, so it degrades to reading the right file under a label it was not told about (#195) |
| 0.16.0 | `pre-pr-review` mints every round against the REMOTE-TRACKING base (`warden round new --base origin/main`), not the local `main` a fetch never moves — a stale base made `warden round classify`'s subtractions measure against a state nobody was reviewing against, and a warden at or past #194 now REFUSES a `--base` behind its own upstream (exit 2), so the `|| exit 1` stops the round. It also stops stating a divergence the attestation schema no longer carries: `findings[].round` describes the RE-RAISE rule in the schema's own words now (#194), with a guard reading that description the way the #193 guard reads `reviewers[].round`'s. **Pin/pack skew, both directions** (the 0.9.0 row's precedent): an OLDER pinned warden has no such refusal, so the remote-tracking base is protocol rather than enforcement there and the `git fetch` this row's step adds is the whole of it; a NEWER pin served a CACHED 0.15.0 pack still mints `--base main`, which that warden refuses at exit 2 — the `|| exit 1` stops the round, correctly, and the fix is to update the pack, not the warden |
| 0.15.0 | `deliver` names more than one cause of `warden round classify`'s exit 2, as an OPEN list rather than the closed pair it used to name — it could not read the chain or the payload, or the payload declares more rounds than the chain carries round directories, which reads fine and is still refused by a warden at or past #191 (a pin at or after #185 but before #191 has `classify` and not that refusal, and measures the unminted round silently; an older pin has no `classify` at all and falls back to counting every round), and a `round` warden will not read as one — and states what its own cap sentence forbids: `never mint a fourth full round` is a fourth full CREW round, never a round DIRECTORY, so the scoped re-reviewer's round is minted with `warden round new` like any other. The two together were the cap's escape hatch handing `classify` a chain it refuses at exactly the moment the cap needs a verdict (#193) |
| 0.14.0 | `pre-pr-review` states that the membership pass is no longer the whole of `attest check` (#192): a warden carrying that change reads the tip attestation's `verdict` and exits 1 unless it is `clean`, so a branch abandoned after an early round no longer satisfies the required gate on that round's `findings-open` shard. Only the tip is asked — an earlier round's open verdict is what per-round attestation is supposed to leave behind — and on a pin predating #192 the check is membership-only, so the protocol's own "the last of them CLEAN" is what closes it there |
| 0.13.0 | `pre-pr-review` rules that **one attestation covers ONE round** (#191): `--review-dir` names that round's `reviewers/`, its roster declares only the dispatches that ran in it, no earlier round's report is carried into it, and every round is attested BEFORE the next commit. The pack shipped both halves of a contradiction after PR #184 merged into PR #182's branch — copy every earlier round's reports into the final round, and never copy a `reviewers/` file forward — and warden enforces both, so a review of more than one round could not be attested at all; the copy is the half that gave way, because a copied report is a distinct non-empty file and no mechanism can tell it from a fabrication. A finding still OPEN when a round ends is RE-RAISED verbatim in the next round's payload under the lens and round that re-confirm it — same `rule_id`, `file`, `line` and text, so the corpus folds the copies — because a payload that carried only its own round's findings would hide an earlier round's open defect from `warden round classify` and blind the cap's revert trigger. It also has every round MINTED, round 1 included: an unminted round is invisible to `warden round classify`, which then measures the original diff as the true original plus the repair commit for the round nobody minted, and a warden at or past #191 refuses a chain carrying fewer round directories than the payload declares rounds |
| 0.12.0 | `pre-pr-review` has every reviewer name the head it judged, the round it ran in and its own role in its raw output, forbids copying a `reviewers/` file forward from an earlier round — `attest write` now refuses a report byte-identical to any sibling round's (#182) — and states that a round does not survive a post-review commit, with the measured re-mint cost that ruling accepted (#182) |
| 0.11.0 | `pre-pr-review` files every candidate the cross-examiner refuted as a finding with `status: refuted` and the refutation as its `reason` — the payload is what the round RAISED, not what survived it, so per-rule precision is measured over raised findings rather than over the survivors of a refute-first arbiter (#186). The examiner returns a third verdict, `scoped-out`, and a scoped-out candidate is NOT filed: every payload status is a JUDGED status, so filing one scored a rule as wrong for a question that was never its own. `retro` reads the two new `memory stats` lines — the refutation-rate bias line and the restatement fold — and must quote them beside any rate it proposes a promotion on. The two absences do NOT read the same way, and the retro is told which is which: each line's absence has TWO causes and neither may be read as clean — the bias line is absent on a pin predating #186 OR on a corpus where no round filed a record at all, and the fold line is absent on an older pin OR on a corpus with nothing folded — the same ruling the 0.8.0 row makes for the ceiling reading (#186) |
| 0.10.0 | `deliver`'s repair cap counts **change-defect** rounds, not rounds: a round with an open finding on the original diff or anything above LOW counts; one whose open findings are all LOW and all on files the repair COMMITS wrote (first-parent, non-merge, base-branch work subtracted; a file in both ranges reads as repair-written) is a scaffolding round that ends the loop (its open findings adjudicated, not reverted), and `warden round classify` decides which from the round manifests — with a `--help` probe and a count-every-round fallback for a pin that predates it (#185). **Warden-pin skew, this row's own mechanism** (no pack version moves with it, because the protocol sentence is unchanged and only the warden's answer to it is): the file subtraction above is by PATH on a pin predating #197 and by CONTENT at or past it. At or past #197 a path the base branch moved between the recorded bases leaves the set only where the branch CARRIES bytes the base branch itself put there, so a base move the branch never merged — a sibling PR touching the same shared surface, which `warden round new --base origin/main` makes reachable by resolving the base at mint time — keeps the path and the round can still read `scaffolding-stop`. On an older pin that same layout subtracts by path, puts a repair-written file in `original`, and reads `counts` — which is the revert clause's premise, so the skew is in the direction that costs a consumer a change, not a missed defect. Provenance also records that the review crew runs flat across diff classes by ruling (#185) |
| 0.9.0 | `pre-pr-review`'s attestation payload names, on every finding, the lens that raised it and the round it arrived in, and on every roster entry the round it was dispatched in, with `role` spelled from the schema's lens vocabulary (`code-reviewer`, `cross-examiner`, `scoped-re-reviewer`, `builder`, `crew:<slug>`); it probes `attest write --help` for `lens` and omits the fields on a warden that predates them. The reverse skew is a refusal: a warden at or past #184 REQUIRES the fields under `--review-dir`, so a consumer bumping its platform pin must update this pack in the same step (`claude plugin marketplace update`) or its next `attest write --review-dir` from the cached 0.8.0 pack exits 2 — the message names the cause (#184) |
| 0.8.0 | `retro`'s tag-reconcile step names `memory stats` as the authority for which audit lines exist, and names the drift-ceiling lines its enumeration had gone stale against; it also rules that a MISSING ceiling reading has TWO causes (an older pin, or a repo that declared no ceiling) and may be read as neither a pin defect nor a clean ceiling, and says which lines render either way (#178) |
| 0.7.0 | `pre-pr-review` mints its round with `warden round new` instead of a `mktemp -d` in prose, and carries a probe-and-fall-back for a warden that predates it (#172); `rule-advisor` and `retro` stop retyping the backtest-action enum and send the agent to the S-05 message its own pinned warden interpolates (#172) |
| 0.6.0 | `pre-pr-review` gained the structured reviewer roster, the `--review-dir` probe for a warden that predates it, and the `unverified-roster` fallback (#168) |
| 0.5.0 | `orchestrate` added; every skill's `## Provenance` block may declare harness assumptions, checked by `warden skills preflight` |
| 0.4.0 | `rule-advisor` gained its subtractive half (`rules lifecycle`, RETIRE?/DEMOTE?/NARROW?) and a hard stop: it never retires, demotes or narrows a rule itself |
| 0.3.0 | `retro` gained the guardrail-gap input and a proposal type for it |
| 0.2.0 | the runner and review-companion skills took their current names; the required policy section became `## Autonomy scope` |

**A cage's `prompt.md` is hand-authored and `cage enroll` never rewrites it.**
Re-enrolling does not fix a prompt that invokes a skill by a retired name, and
the plugin cache keeps old versions beside new ones. Read the prompt yourself
before re-adding the marketplace at a new pin on a machine with an enrolled
cage.

---

Next: [Skills Policy](Skills-Policy.md) · [The Cage](The-Cage.md) ·
[Memory](Memory.md)
