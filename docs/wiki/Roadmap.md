# Roadmap

What is built, what is half-built, and what is deliberately refused. Tracker
ids are `bd` items in this repo (`bd show <id>`).

The organizing question for everything below is the same one the platform is
built around: **can a system that reviews its own work safely change its own
rules?** Each row states how far that has actually got, not how far it could.

## Status at a glance

| Capability | State |
|---|---|
| Deterministic gate, evidence, attestation | **shipped**, self-hosted, and standing-CI-proven on a built-in consumer |
| Adversarial review with a decorrelated second judge | **shipped**; whether the decorrelation is real is [open question 3](Why-This-Exists.md#open-questions) |
| Review memory, precision stats, promotion bar | **shipped**; five rules promoted through it, no cadence yet |
| Guardrail catalog, gap analysis, ceiling enforcement | **shipped** |
| Rule lifecycle — retire, demote, narrow | **shipped as proposals**; never acts |
| Autonomy ladder — a machine changing rules | **half shipped**; see below |
| Unattended cage runs | **shipped**; one run has shipped a PR (#279), against the ten the redesign asks for |
| Self-authored skills | **not built**; see below |
| Cross-repo learning | **not built**; the question comes before the mechanism |

## Self-learning: the corpus that teaches the gate

**Where it stands.** Findings become committed shards; `memory stats` computes
per-rule precision with a confidence floor and ranks candidate rules by
recurrence; the promotion bar and the pause streak are implemented and
test-pinned. The mechanism works.

**What has happened.** Five rules have gone through the whole loop: a retro
proposed each, the promote backtest it merged with cleared the bar, and a
person merged the two together. Four were proposed by the 2026-08-26
retro. `fail-closed` (39 judged, floor 0.831) and `enforcement-truth` (45,
0.821) are judgment rules written for the fail-open and enforcement-claim
classes; `tests-bite` covers the test-cannot-fail class (11, 0.741), proposed
below the promotion bar at 6 judged and over it by the time it shipped; and
`tests-required` had its mechanical half promoted to the checker
`tests-accompany-logic` (23, 0.857). The fifth, `attribution-holds` (32,
0.758), covers the evidence-attribution class and was proposed by the
2026-09-15 retro — the first promotion of a class that had already crossed
the bar while no retro ran, its crossing read on the tracking item on
2026-09-14, the day before. Each carries a promotion record under
`.warden/memory/promotions/`, which is what certification S-04 counts — on the
record's word, which it cannot verify. The remaining promote artifacts are not
this: `rename-complete` (6 judged, floor 0.61), `error-names-cause` (5, 0.566)
and `evidence-intact` (9, 0.701) were retro proposals whose counts never
cleared the promotion bar, `pattern-fit` came from a synthesis pass, and
`wiki-fidelity`'s is a retroactive backtest for a rule written before the loop
existed. `blast-radius-named` (21, 0.5) joins the retro proposals below the
bar: the 2026-09-27 retro proposed writing it for the consumer-blast-radius
class, the one charter lens no rule enforced, and it carries no promotion
record because its counts never cleared the bar.

**What has not happened.** The loop has no cadence. It went from 2026-08-29 to
2026-09-15 with no retro, and `evidence-attribution` crossed the bar in that
gap — the crossing was detected on 2026-09-14 by a tracking-item reading of
`memory stats`, and the 2026-09-15 retro then proposed the promotion, the
fifth above. `.github/workflows/memory-watch.yml` now checks weekly
and opens an issue when something crosses a bar; a person still runs the
retro. One rule has been adopted through the machine tier: on 2026-09-26
`warden autonomy adopt` wrote `subject-required` — non-blocking at MEDIUM,
no `covers:`, drawn from the declared `unanchored-pattern` tag (5 judged, 5
upheld, floor 0.566 on the day) — and the `adopt` artifact S-05 reads, which
records `judged: 0` because an `engine: claude` rule has no hit count to
measure; `warden autonomy carve-out` clears that diff, a caller opened the PR,
and a person merges it.

## Self-evolving rules: the autonomy ladder

The ladder draws its line not at human-versus-machine but at a sharper
question: **does the action raise enforcement on anyone?**

| Tier | Actions | Who |
|---|---|---|
| Safe | `pause` a rule the corpus refuted in three rounds running; `adopt` a **non-blocking** rule carrying no `covers:` or `implements:`, with an artifact that records no noise cost — the adopt record declares `judged: 0` for every rule and its reader refuses any other figure | a machine may perform these — `warden autonomy` |
| Human-only | raise a severity into `blocking_severities`; promote a judgment rule to a checker; retire, demote, or narrow anything | a person, through a reviewed PR |

Both safe actions only ever mutate the working tree. They never commit, push,
or merge. A caller opens a PR; a person merges it.

**Wired, opt-in:** the cage's forbidden-path post-check used
to auto-close a PR for any `.warden/` write, so the machine tier's permission
was theoretical. Where a project sets `[gate].autonomy_carve_out = true` (it
defaults to false — the grant is the consumer's), the runner asks
`warden autonomy carve-out` whether every **forbidden path** in the diff is
inside the narrow carve-out — pause fields, new non-blocking rule files,
the pause or adoption backtest each carries, and the
attestation shard a review round leaves under `.warden/memory/attest/` — deciding by the SHAPE of the
committed diff, judged
against the committed `repo.yaml`, rather than by any marker a session could
write. It fails closed on a missing shim, a refusal, a crash, or a regex or
`git diff` that could not evaluate, and it can only ever clear a stop, never
create one. See
[The Cage](The-Cage.md#the-autonomy-ladder-carve-out).

**Still true, and deliberately: nothing here merges.** A cleared run opens a PR
exactly like any other, and a human merges it. Auto-merge-on-green would be a
further grant — taken never assumed — and no code path in the runner or the
ladder can perform one.

**Narrowed:** certification S-05 used to accept any backtest
at the current `rules_version`, so one artifact evidenced the whole ruleset.
Pauses are now bound both ways — a `pause` artifact stamped with the *current*
version must name a rule the ruleset shows paused, and every paused rule
carries a pause artifact of its own at *any* version (existence, not freshness:
`rules_version` moves for unrelated reasons, and demanding a fresh artifact per
standing pause would de-evidence pauses that never changed). A retire, whose
rule file is gone, is checked against the rule files git history holds, and a
current unpause or demote against the state the tree shows. Other change
classes stay unbound: detecting them needs a previous
ruleset, and the platform keeps no ruleset history by ruling. S-05's pass line states that bound rather than
implying a coverage it does not have.

## Self-authored skills

**Not built, and not scheduled.** The judgment protocols in
[the skill pack](Skill-Pack.md) are hand-written prose, reviewed and merged
like code. Nothing in the platform proposes, edits, or versions a skill.

The bar a proposal would have to clear before that changes, stated now so it is
not quietly lowered later: a skill edit changes *how every future review is
conducted*, so the evidence standard is strictly higher than a rule's, not
lower. A rule that misfires costs one noisy finding a reviewer weighs; a skill
that misfires silently degrades every judgment made under it, with no artifact
recording the degradation. There is no measurement in the platform today that
could distinguish a skill improvement from a skill regression — which is
the question below, one layer up.

The nearest honest step is **measuring the craft layer we already declare**:
do the declared build disciplines actually reduce repair
loops and raise first-pass gate success? The instrument exists
(`cage measure`); the data does not, and the confounder (task difficulty,
un-randomized) has to be stated with any number that comes out of it.

## Cross-repo learning

**The question, deliberately before the mechanism.** Once
two repos run the rule advisor, does a rule that earned its place in one
transfer to the other? The honest answer may be that only the security catalog
transfers and the convention rules do not.

A mechanism built before that answer is known would just be a way to spread one
repo's noise to another. Two hard constraints on whatever the answer is:
transfer means **a file a human moves**, or a new entry in the shipped catalog
— never a service, never telemetry. And a rule that transfers cleanly is
evidence it belongs in the platform catalog rather than in any single repo's
rules directory.

## Scale

**The corpus decays linearly, and never breaks.** Every
cost is `O(all shards ever)` and nothing compacts. Three linear taxes: `ingest`
re-parses every shard on every run; `certify` walks them all on every push; and
`recall` parses the whole cache to return ten records, filtering the stale
horizon *after* loading.

At a few thousand shards this is seconds. At fifty thousand it is tens of
seconds per merge, while useful signal plateaus at roughly the fresh window —
compute grows forever, value does not.

Direction, when it is worth doing: archive whole shards past the stale horizon
into a committed archive with a summarized index carrying the counts `stats`
needs, and make `ingest` append rather than rebuild-the-world, keeping the full
rebuild as a repair path. What must not change: shards stay append-only
committed evidence, ids stay content-derived, `attest check` keeps finding its
one shard in one range, and an unreadable shard keeps failing closed.

## Closing the unattended loop

**The cage has closed the loop once.** The consumer ledger records seven
runs, and what stopped them is in [Why This Exists](Why-This-Exists.md). The stops
that fired held closed; one fired wrongly, denying the run's own branch-scoped
push. Resume-from-checkpoint and run-identity fixes have landed since. On this
repo's own cage, run 20260920-205434 stopped at a forbidden path with its work
unpushed, and run 20260921-070026 opened PR #279, which the founder merged. One
run has shipped; the redesign asks for ten.

## Deliberately not built

Not a backlog. These are refusals, and each one is load-bearing.

| Not built | Why |
|---|---|
| **Auto-merge, at any trust level** | merge authority is human and does not transfer. Its *execution* may be delegated by an explicit, current, revocable grant, on the terms `graph.yaml`'s `review.delegation` declares — the terms live there, the grant never does, because a tracked file has no clock and a session grant recorded in one outlives its session. No agent grants itself the button, and no skill assumes it |
| **A hosted service, dashboard, or queue** | $0 standing infrastructure. Everything is a CLI invocation, a CI job, or a cron entry |
| **Paid ingestion APIs** | the platform reads git, GitHub, and local files |
| **A graph runtime** (LangGraph, AutoGen, …) | the runtime is Claude Code sessions. The graph is config plus interpretation, and a framework would make the org harder to audit, not easier |
| **A rule the advisor adopts on its own backtest** | a clean backtest measures the past; the cost of a bad rule is paid by every future PR author |
| **Retiring a rule on silence** | a rule that has never fired may be the reason the thing it guards has never happened |
| **A "human review no longer required" tier** | nobody has a principled answer for which class of change qualifies, or what evidence would justify the move — [open question 8](Why-This-Exists.md#open-questions) |

## Known defects

Open bugs are tracked, not hidden: `pre-pr-review` hardening its review-package
path against collision but not its findings file. Two entries
left this paragraph by being RULED ON rather than fixed or forgotten — the
kernel-blocking `repo.yaml` read is now a stated limitation
below, and the round-directory collision it sat beside is
fixed: `warden round new` mints the round so the path is not the agent's to
choose. **`bd list` is the live view, and it is the
authority** — this page is a snapshot and goes stale between edits, which is
exactly what it did: it named two bugs that had
already been fixed and closed.

## Stated limitations

Not bugs awaiting a fix — behaviours ruled on, and written into every seam that
describes them. A limitation that is only tracked is indistinguishable from one
nobody has noticed.

- **The grandfather waiver widens while a covering rule is paused**. `attest write` refuses a covered slug because an *unpaused*
  rule answers the class, so a pause re-opens it, fresh records file under the
  waived spelling, and E-02 absorbs them on unpause beside the genuinely legacy
  ones. Distinguishing the eras needs a marker a standing ruling rules
  against building; the docstring, the E-02 remedy, Adopting and Memory all say
  so now.
- **A `repo.yaml` behind an uninterruptible kernel wait still parks**
  (ruled and then RE-RULED 2026-09-01 — `warden decide show`).
  A wedged NFS or FUSE mount parks `find_repo_root`'s `stat` before any open,
  and warden ships no timeout guard for it today. **This entry is a deferral,
  not an impossibility, and it says so because its first version got that
  wrong.** That version claimed no bound was available; review refuted it and
  the refutation reproduces. A child process doing the read, with a parent that
  waits on a deadline, reports and `os._exit`s WITHOUT reaping it, bounds this
  fine — measured exiting 3 after 1.00s with the child still blocked. Reaping
  is not what a bound needs: an orphaned child blocks nothing in its parent,
  where `exit_group` must stop a D-state thread in the same process, which is
  what kills every in-process variant. Verifying it surfaced the trap: without
  `DEVNULL` stdio and `start_new_session`, the orphan holds the caller's stdout
  pipe and anything CAPTURING warden's output — CI, a git hook, the cage —
  hangs anyway (re-measured: exit 3 after 1.02s under a
  capturing caller with them, still hung at 8s without).

  A second ruling then measured the COST and ruled against shipping it, so
  this deferral now carries a price rather than a promise. The in-process read
  takes 0.03ms; the cheapest possible child takes 10.75ms (322x), and one that
  imports `warden.config` so there is still one reader takes 75.8ms (2273x).
  `warden explain` performs four such reads — +43ms on a 0.71s command — and
  one suite run performs 1006 in-process, a floor of +10.8s on a 112s suite,
  before every warden process the suite spawns pays the 43ms again. Paying
  322x forever on warden's most load-bearing read path, on every command, hook
  and cage run, to convert a hang nobody here has hit into `exit 3` is refused;
  an opt-in flag is refused too, because a default-off branch nothing reaches
  is this repo's own zero-consumer defect one noun over. The command still
  visibly never returns, and an operator diagnoses it from outside.

  Still refused on the merits, and unchanged: `SIGALRM` interrupted a blocking
  FIFO open in 0.51s and a daemon-thread watchdog exited 3 in 0.55s, but a FIFO
  open is an INTERRUPTIBLE wait and those cases are already fixed by
  the `O_NONBLOCK` classification — a signal is never delivered to
  a thread in an uninterruptible wait, so a FIFO-based test goes green over a
  bound that does not exist. A non-daemon worker hung the interpreter outright.
  Refusing a symlink off the repo's own device misses `repo.yaml` sitting ON
  the wedged mount.
- **A review round does not survive a commit before `attest write`**. `attest write --review-dir` binds the round to the head it
  was minted for and refuses any other, so a fix, a comment, or a merge of
  `origin/main` between the mint and the write forces a fresh `warden round
  new` and a full re-run of the reviewers. What USED to be listed here as the
  limitation — that after the shard is committed nothing refuses a later
  commit, so re-running after a fix was the protocol's rule and not a check —
  is RETIRED: `attest check` now computes the uncovered set and refuses a
  commit no tip attestation covers that touches anything outside
  `.warden/memory/attest/`, alongside the tip-verdict rule
  that closed the branch abandoned mid-review. What stands
  here is the re-MINT cost alone, measured before
  being accepted: over the 132 committed attestation shards, 4 were re-attestations of an earlier round on the same branch line: 3 of the 4 carried a change under `tests/**` on the branch itself — reviewed surface, and the class the ruling's own hazard note names (a test fixture *is* reviewed; PR #172's CI red was in exactly that class) — and 1 of the 4 was forced by a merge of `origin/main` alone, whose branch-side diff was the attestation shard and nothing else. The refusal stays unnarrowed until a measured count
  says otherwise.
- **The range-binding signal stops at the run dir**. The
  run-manifest record is the whole mechanism, so `memory stats` and the retro do
  not count how often the warning fires and no doc claims they do. Carrying it
  into the corpus would need a field on `attestation.schema.json` plus a change
  to ingest's fixed envelope field list, for a number nothing reads today.

---

Next: [Why This Exists](Why-This-Exists.md) · [Memory](Memory.md) ·
[Cost and Throughput](Cost-and-Throughput.md)
