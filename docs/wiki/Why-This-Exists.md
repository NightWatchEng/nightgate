# Why This Exists

For engineers deciding whether any of this is worth their time. It states the
problem, the bet, what it costs, the alternatives, and — at length, because it
matters most — what remains unproven.

If you read one section, read [Open questions](#open-questions).

## The problem

Agents write code faster than review absorbs it, and they are *confidently*
wrong: fluent, well-structured, plausible, incorrect. Volume goes up and
attention per change goes down, at exactly the moment each change deserves
more scrutiny.

The usual answers don't survive that. *Review it all yourself* doesn't scale,
and degrades fastest when volume is highest. *Trust the model* works until it
doesn't, and the failures are silent. *Add another AI reviewer* leaves you
with two confident opinions and no way to adjudicate between them.

So the question stops being "can an agent write this?" and becomes:

> **What had to be true before this change was allowed to merge — and can you
> prove it afterward?**

## The bet

**Judgment is admissible in a gate if — and only if — the judgment is
attributable.**

The industry's position is roughly the opposite: keep models out of the
allow/deny loop, because a model's verdict isn't reproducible and therefore
isn't auditable. That's reasonable. It is also self-fulfilling — if you never
make judgment attributable, it never becomes admissible.

Attribution is a solvable engineering problem, in four steps. The order
matters; the model comes last on purpose.

1. **Declare the policy in the repo.** Components, risk tiers, rules,
   verification commands — reviewable, diffable, versioned like code.
2. **Hash the whole policy into a `rules_version`.** Rule files, checker code,
   schemas, and `repo.yaml`'s `review:` keys. Editing a checker — or flipping
   `blocking_severities` — *is* editing policy, and the version moves.
3. **Stamp that version on every verdict**, beside the commit SHAs it judged.
   Now "which exact policy, at which revision, produced this verdict?" has an
   answer.
4. **Only then** let a model be one of the engines producing verdicts, because
   its output is now bound to a reviewable artifact instead of floating free.

### What is actually verified about that gap

Only what we confirmed at the source:

| Claim | Status |
|---|---|
| OPA's decision logs bind a bundle `revision` to every decision | **Verified** — the field exists, documented as *"Revision of the bundle at the time of evaluation."* |
| That revision is a *label*, not a content hash | **Verified** — OPA's Status API documents `active_revision` as *"Opaque revision identifier"*, and `cmd/build.go` sets it from a caller-supplied `-r` flag with no content-derived fallback. |
| Binding a verdict to a policy digest is already standardized — elsewhere | **Verified** — SLSA's Verification Summary Attestation carries `policy: {uri, digest}`: it *"MUST contain a uri identifying which policy was applied and SHOULD contain a digest to indicate the exact version of that policy."* |

Read that table as the honest version of the pitch: the slot exists, the
identifier in it isn't trustworthy, and the fix is already standardized in a
neighbouring domain. **We are porting a known idea, not
inventing one.**

## What you get

- **A gate that can auto-reject.** Mechanical rules block; judgment rules are
  evaluated pre-PR and recorded. Exit codes separate *clean*, *blocking
  finding*, and *the gate did not run* — that last one matters, because a gate
  that fails open is worse than no gate: it manufactures confidence.
- **An evidence artifact per run**, bound to commit SHAs and `rules_version`,
  so a verdict is reproducible and attributable months later.
- **Adversarial review, not just review.** A reviewer proposes findings; an
  independent cross-examiner that never sees the reviewer's reasoning tries to
  *refute* each one. A finding survives only if confirmed.
- **Hard stops for unattended work** that live outside the model — time
  windows, kill switches, back-pressure caps, watchdogs, forbidden paths.
- **A maturity ladder** (`warden certify`) that scores enrollment 1–5 and can
  fail CI when a repo slips below the bar it claims.

## What it costs

- **You must write policy.** ~20 lines of `repo.yaml` minimum, plus rules. A
  repo that declares nothing gates nothing.
- **You must own a decision about severity.** Which findings block is a
  judgment no tool can make for you.
- **Judgment rules consume model tokens**, on every review round. On a
  subscription that shows up as usage limits rather than a bill — see
  [Cost and Throughput](Cost-and-Throughput.md) for the measured volumes and
  what actually stopped runs.
- **The gate will be wrong sometimes.** A false positive on a HIGH rule blocks
  a correct change. You need a dismissal path with a written reason, and the
  discipline to use it rather than weakening the rule.
- **This is a governance system.** If your team doesn't want one, the tooling
  won't create the appetite.

## Alternatives

**Do not trust the table below. Verify current state yourself.** This space
moves monthly, vendor docs contradict vendor marketing, and research for this
page once produced confident, cited-looking claims with nothing behind them.
What follows is a map of categories and the questions worth asking — not a
capability matrix.

| Category | Examples | The question to ask |
|---|---|---|
| PR rule bots | Danger, reviewdog | Is there a *rule schema*, or just imperative code? |
| Policy-as-code | OPA/Rego, Conftest, Kyverno, Checkov | Can a rule call code *your repo* owns? Is the policy version bound to the result? |
| AI PR reviewers | CodeRabbit, Greptile, Qodo, Ellipsis, cubic, and many more | Can its verdict block a merge, or only comment? Where do the rules live — your repo, or their dashboard? |
| Merge platforms | Mergify, Aviator, Trunk, merge queues | Does it evaluate the change, or only decide when to press merge? |
| Platform-native | GitHub rulesets + CodeQL + Copilot review | Can the AI half block? Can the ruleset live in a file you review? |
| Supply-chain attestation | SLSA, in-toto, Sigstore | The closest *conceptual* prior art — read it before adopting anything here. |

Five questions separate these quickly:

1. Where does the policy live — your repo, or someone's dashboard?
2. Can a rule dispatch to code you own, or does extensibility end at a DSL?
3. Can it block, or only advise?
4. Is any verdict bound to a policy version, so you can reproduce it later?
5. Can you declare one component riskier than another, and have that change
   what happens?

Plenty of tools answer 1–3 well. We have not found one that answers 4, and
would rather be shown wrong than rebuild it.

## What is NOT proven here

This system gates itself, plus
[shortfall](https://github.com/NightWatchEng/shortfall) — the live consumer,
a Go multi-module repo on a pinned platform SHA — and distill, which enrolled
first and has been quiet since August. That is a start, not a track record.

- **The memory corpus is days old.** Shards accumulate with every PR and the
  first rules clear the promotion bar, but the retro does not run on a
  schedule, only five rules have been promoted *through* the loop
  (`fail-closed`, `enforcement-truth`, `tests-bite` and `tests-required`,
  proposed by the 2026-08-26 retro, and `attribution-holds`, proposed by the
  2026-09-15 one), and days of data cannot show decay or drift.
- **Unattended runs have shipped once, not repeatedly.** The consumer ledger
  records seven runs between 2026-08-19 and 08-24, and its outcome column
  misfiled five of them. Read against the session logs: two died at the
  session limit (a 429,
  filed `failed(1)`), two carried fully attested work that never became a PR
  because the permission profile denied the run's own `git push` (filed
  `usage-limit`), one was a laptop that slept mid-run (also filed
  `usage-limit`), one found no eligible item, and one recorded `done` against
  no item at all. The runner now classifies by the real cause and allows the
  run's own push. On this repo's own cage, run 20260920-205434 stopped at a
  forbidden path on 2026-09-20 with its work unpushed, and run 20260921-070026
  opened PR #279 on 2026-09-21 and left it for a human; the founder
  squash-merged it. The loop has closed once; the redesign asks for ten.
- **Nobody outside this org has enrolled from the docs alone.** Portability is
  proven twice over — by an in-repo example CI enrolls on every PR, and by
  shortfall, a separate repo running the platform against its own config in
  its own CI, which certified LEVEL 3 (Reviewed) on 2026-09-01. Both are real.
  Neither is a stranger succeeding from the docs alone, and that is the thing
  still unproven. Two calibrations, because the pin is the whole point of a
  pin: shortfall runs an EARLIER platform pin
  ([Cost and Throughput](Cost-and-Throughput.md) has the window), so its
  evidence is evidence about that version and not this tree; and the ladder
  position is dated here for the same reason `CLAUDE.md` dates it — a
  portability proof stated with no date and no rung reads as a finished one.
  This bullet used to deny any external enrollment at all, which stopped
  being true the day shortfall enrolled.
- **No cost data that isolates the platform's own overhead.**
  [Cost and Throughput](Cost-and-Throughput.md) reports measured token volume
  and time per merged PR on a real consumer, and what a subscription seat
  worked out to. What it cannot tell you is how much of that the gate caused
  versus the work itself — a repo making the same changes with no gate would
  consume most of it.

## Open questions

Unresolved, and the interesting part.

**1. Does compounding review memory improve review quality?** The premise is
that past findings sharpen future reviews. We have no evidence, and the metric
is structurally hard: we can measure *precision* (what fraction of findings
survived cross-examination), but **recall is unobservable** — you cannot count
the bugs a review didn't find. Escapes are the only recall signal, and they
are lagging and sparse.

**2. Is the promotion bar right?** A judgment rule becomes a deterministic
checker at ≥10 judged cases and a 0.70 confidence floor. Both numbers are
defensible; neither is derived from anything.

**3. Does the reviewer/examiner split actually decorrelate?** They are given
disjoint priors so they fail differently — but they are often the same model
family with the same training. **Two instances of one model may share a blind
spot no prompt separation removes.** If so, the independent judge is theatre.
Testable, untested.

**4. Can a model's verdict ever be genuinely admissible?** The counter-argument
is serious: the same `rules_version` and the same diff can produce different
verdicts on different days. A digest says *which policy* ran, not *what it will
decide*. We think attribution is enough without reproducibility, because human
reviewers aren't reproducible either and we admit their judgment — but
that's an argument, not a proof.

**5. Does a maturity ladder measure anything, or is it Goodhart bait?** The
moment a number is a target it stops being a measurement. Consumers can extend
the ladder but never remove a baseline check, which blunts it — a repo
optimizing for Level 5 is still not the same as a repo that is safe.

**6. What is the right unit of memory?** Findings are keyed by directory
prefix, tags, and rule id. Do those keys survive a refactor that moves
everything? Is a "finding" the right granularity, versus a pattern, a rule, or
a component's history?

**7. Does unattended work pay for itself?** An agent that opens PRs a human
must review has moved work, not removed it. Break-even depends on acceptance
rate. Instrumented, no data yet.

**8. When should a human stop reviewing?** The honest end-state of a trust
ladder is that some class of change stops needing human eyes. Nobody has a
principled answer for which class. Every merge here is human, deliberately —
but refusing to ask doesn't make the question go away.

**9. Does declaring the org as a graph earn its complexity?** `graph.yaml`
catches real errors (a non-human merge node, a memory edge feeding the wrong
role). It might also be an elaborate way of writing down what everyone knows.

**10. What does this cost, and is it worth it?** Model usage, CI minutes, and
the human time to author and maintain policy, against defects caught before
merge. [Cost and Throughput](Cost-and-Throughput.md) has the first half of that
arithmetic and not the second — and the first half turned out to be a question
about usage limits, not money.

## How to disagree with this

The most useful thing you can do is falsify something.

- **Find the tool that binds verdicts to a policy digest.** We would rather
  adopt it than rebuild it.
- **Show the reviewer/examiner split is illusory** — same model, same blind
  spot. That invalidates a load-bearing design choice.
- **Show the promotion bar is miscalibrated** on a real corpus.
- **Show the whole apparatus loses to "one good reviewer and a test suite"** at
  your team's scale. It might.

---

Next: [Architecture](Architecture.md) for the machinery ·
[Quickstart](Quickstart.md) to try it · [Memory](Memory.md), where most of the
open questions live.
