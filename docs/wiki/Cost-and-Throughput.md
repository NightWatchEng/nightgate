# Cost and Throughput

What running Nightgate costs, which model to point it at, and how long one
tracked item takes to become a PR.

Every number here is **measured**, and the window is named beside it. Where a
number cannot be separated from the work it rode along with, the page says so
rather than guessing.

## One PR, in numbers

Measured on this repository over the 78 PRs merged from 2026-09-26 to
2026-10-04 (#302–#391); nothing below is projected. The method, every
command and the raw output are in `docs/design/cost-per-pr-20261004.md`,
a design record the public export does not carry.

| What a typical merged PR costs | Median | Window, n | Source |
|---|---:|---|---|
| Review rounds | **2** | 78 merged PRs | committed attest shards |
| Claude tokens, one unattended PR | **106 K** output, **14.3 M** cache-read | 13 cage runs, 2026-09-21 to 09-27 | the cage's session logs |
| Actions minutes, PR plus merge | **56** | 78 merged PRs | `gh api` workflow jobs |

**Review rounds.** 63 of the 78 PRs ran two review rounds and 15 ran one;
32 also ran the closure round, which re-files findings past the cap and
reviews nothing. Of the eight in the window merged on 2026-10-04
(#382–#391), #384 ran one review round and seven ran two, #388 and #390
with the closure round. The count is the highest `round` in the shards
each PR's squash commit added. On `main` the same date, `warden certify
--goal` read 3 of the 20 most recent attested branches as one-round.

**Claude usage.** The committed evidence records no tokens: no attest shard
carries a token field, and no schema under `warden/schemas/` defines one.
The harness's transcripts do, and the cage keeps one per unattended run,
ending in usage totals that include every subagent. Of the 23 runs on this
repository's cage ledger from 2026-09-20 to 09-27, 13 ended `done` with a
PR, and 12 of those PRs merged. Per run that opened a PR, the medians are:

| Output | Cache read | Cache write | Fresh input | Wall clock | Subagents |
|---:|---:|---:|---:|---:|---:|
| 106 K | 14.3 M | 0.42 M | 286 | 24 min | 4 |

Output ranged from 42 K to 338 K. The 10 runs that shipped nothing drew
tokens too: counting all 23 against the 12 merged PRs gives 162 K output
and 20.7 M cache-read per merged PR. The model was `claude-opus-5-5` in 9
of the 13 runs, `claude-fable-5-1` in 3 and `claude-opus-5` in 1. The
harness prices each session at list rates: a median $7.95 per run that
opened a PR, $15.83 per merged PR with every run counted. Nobody paid it;
the runs used a subscription seat.

Reviewer input tokens per round come from the same logs: each subagent's
API calls carry the id of the dispatch that started it, and every call
summed, builder's and dispatches', is 97.7–98.8% of a session's cache-read.

| Dispatch | n | Cache-read tokens, median |
|---|---:|---:|
| Round 1 code-reviewer | 13 | 0.88 M |
| Round 1 cross-examiner | 13 | 0.42 M |
| Round 2 scoped re-review | 10 | 0.71 M |
| Closure | 7 | 0.19 M |

Round 1's two dispatches together are a median 1.32 M per run (0.54–2.89
M). Review is a median **16%** of a run's cache-read tokens (7–30%); the
builder's own session is the rest. Output per dispatch cannot be read: the
log records each call's usage when the call starts. For attended PRs the
round count is the proxy, since the shards record rounds exactly.

**Actions minutes.** Every job of every workflow run, all attempts, read
with `gh api`. A median PR ran CI once before merging (mean 1.3, max 8),
for **20.5** job-minutes. All jobs here run on `ubuntu-latest`, and GitHub
bills a Linux job by rounding each job up to a whole minute. Rounded that
way, the PR-side runs come to **27** minutes. After the merge, CI on `main`
and `wiki-sync` add a median of 28, for **56** per PR (p90 84). The
maximum, 218, is #384, a Dependabot bump whose CI failed six times in
eight runs. Counting every run that started in the window, including PRs
that never merged and scheduled jobs, gives 5,140 minutes, or 66 per
merged PR. On 2026-09-30 at 00:45 UTC, GitHub refused to start jobs, with
the annotation "recent account payments have failed or your spending limit
needs to be increased". #383 and #385 merged in the next 16 minutes with
every CI job refused, so they sit in the 78 at 0 minutes, each counted as
one run. No other PR merged until 2026-10-04. The allowance used up is not
known here: reading the org's plan needs an `admin:org` token.

## The short answer

**A Claude Code subscription seat. What you will run out of is the usage
allowance, not money.**

The measured window below — 114 merged PRs over four days — ran on one
subscription seat, with builders working in parallel worktrees under
`orchestrate`. That capacity is the useful figure, and it is directly
checkable: **one seat, 114 merged PRs, four days.**

**There is no honest per-PR dollar figure on a subscription, and this page will
not invent one.** A seat is consumed against a usage allowance, not against the
calendar, so dividing a monthly price by elapsed days is an assumption wearing
a measurement's clothes — and the evidence below says the assumption is wrong
in the direction that would flatter this page. An earlier cut of it did exactly
that and produced $0.23/PR; treat any such number, including that one, as a
floor with nothing holding it up.

If you need a metered number because you are billing through the API, there is
one [further down](#if-you-meter-this-through-the-api-instead) — as a ceiling.

### What actually stops a run

Not cost — and stating that plainly matters, because the runner **has no
cost-termination outcome at all**. It can end a run `done`, `timeout`,
`usage-limit`, `push-denied`, `failed(N)`, `FORBIDDEN-PATH`, `skipped` or `usage-skipped`, and a session may
report `done`, `blocked` or `skipped` for itself. Nothing anywhere meters
spend — not the runner, not `cage.toml`, whose `[limits]` are open PRs, a wall
clock and resume attempts. So "no run ended on cost" would be a null result
from a mechanism that does not exist.

What it does have is `usage-limit`. The cage ledger for
**distill** — a *different* repo and window from the measurement below,
2026-08-19 to 08-24, unattended runs rather than the attended `orchestrate`
sessions that produced the 114 PRs — records seven runs:

| Outcome | Runs |
|---|---|
| `usage-limit` | 3 |
| `failed(1)` | 2 |
| `done` | 1 |
| `skipped` (no eligible item) | 1 |

That outcome column was wrong. The runner then filed any run whose log
contained the words "usage limit" as `usage-limit`, and the injected prompt
contains them. Read against the session logs, none of the three `usage-limit`
rows was a usage limit: two were finished, attested runs whose `git push` the
permission profile denied, and one was a laptop that slept mid-response. Both
`failed(1)` rows were the real stop, a 429 session limit. The runner now
classifies from the session's final result event and files a refused push as
`push-denied`. So two runs died at the session limit, and that is the
constraint to plan against. It is also a small sample from one repo, so read
it as the only direct evidence available rather than as a rate.

## Which model

| Role | Model | Why |
|---|---|---|
| Builder, reviewer, cross-examiner | **Claude Opus 5** (`claude-opus-5`) | the default. Review judgment is the thing this platform admits into a gate; it is the wrong place to economize |
| Fan-out subagents, mechanical passes, doc sweeps | **Claude Sonnet 5** (`claude-sonnet-5`) | cheaper per token, and these passes are bounded and checkable. Whether a cheaper model draws proportionally less from a subscription allowance is **not something this repo has measured** |
| The hardest long-horizon items | **Claude Fable 5** (`claude-fable-5`) | more capable, and roughly 2x Opus per token. It carried 28% of the window's tokens for a minority of the work — and 47% of the metered dollar total below, because of that 2x — so reach for it deliberately, not by default |
| Anything | **not Haiku** | it does not carry review judgment, and the gate's value is entirely in the judgment |

Two settings matter more than the model choice:

- **Effort.** `xhigh` for building and reviewing; `low` for a subagent doing a
  bounded lookup. Effort trades thoroughness against tokens *within* one model.
- **Prompt caching.** Cache reads are 98.5% of all input volume below. If your
  harness is not caching, fix that before touching anything else — nothing else
  here comes close.

## Measured: a real consumer, 114 merged PRs in four days

The [`shortfall`](https://github.com/NightWatchEng/shortfall) repo is enrolled
on an earlier platform pin. Between **2026-08-27 and 2026-08-30** it merged
**114 PRs** — every one built by the skill pack, reviewed adversarially, and
merged by a human.

Token totals across every session, subagent, and worktree in that window, read
from the harness's own per-message `usage` records:

| Model | Fresh input | Cache write | Cache read | Output |
|---|---|---|---|---|
| `claude-opus-5` | 23,716 | 28.1 M | 2,571.6 M | 2.74 M |
| `claude-fable-5` | 14,584 | 35.6 M | 1,603.4 M | 4.32 M |
| `claude-opus-4-8` | 8,494 | 23.1 M | 1,575.5 M | 3.88 M |
| `claude-sonnet-5` | 418 | 0.8 M | 15.0 M | 0.16 M |
| **Total** | **47,212** | **87.7 M** | **5,765.5 M** | **11.10 M** |

### Per merged PR

| Metric | Value |
|---|---|
| Cache-read tokens | **50.6 M** |
| Cache-write tokens | 0.77 M |
| Output tokens | **97 K** |
| Fresh (uncached) input tokens | 414 |

Cache reads dominate by two orders of magnitude, which is why caching is the
first lever and every other one is a rounding error beside it.

### Time

| Metric | Value |
|---|---|
| Wall clock for 114 PRs | 84.8 hours |
| Median gap between merges | **18 minutes** |
| Mean gap between merges | 45 minutes |
| One builder session, start to PR | **45–75 minutes** |

The mean is 45 minutes because 84.8 hours spans 113 intervals; the median is
18, about 40% of that, because the gaps are skewed by a handful of long ones.
Either way it is a *throughput* number, not a latency number: builders ran in
parallel worktrees under `orchestrate`. The page's own figures bound the
average concurrency rather than the peak — 114 sessions at 45–75 minutes over
84.8 hours of wall clock is 1.0–1.7 running at once on average, with bursts
well above that, which is what the 18-minute median gap reflects. If you run
one item at a time, **45 to 75 minutes from a ready item to an open PR** is the figure to
plan against — including plan, build, verify, two adversarial review passes,
repairs, gate parity, and the PR itself.

### What the gate actually caught

Over the same window, 122 committed review events:

| | Count |
|---|---|
| Findings raised | 595 |
| **Fixed before the PR opened** | **527** |
| Dismissed with a written reason | 65 |
| Still confirmed and open at attestation | 3 |
| Clean verdicts | 121 of 122 |

That is **4.6 defects repaired per merged PR, before a human looked at
anything.** It is also the strongest argument on this page and the weakest
evidence: nothing here tells you how many of those 527 would have survived an
ordinary review, or shipped harmlessly. Precision is measurable; recall is not.
See [Memory](Memory.md).

## Measured on this repo: where the hours go

A second window: this repo's own 202 merged PRs to commit `c9abf27`,
measured on 2026-09-14 from the three timestamps GitHub keeps per PR (first
commit, opened, merged), with phases read from the per-round attestation
shards and the crew shape from the committed rosters. The audit is a design
record the public export does not carry; its figures are the ones below.

| Lines changed | PRs | Median, first commit to merge | p90 |
|---|---:|---:|---:|
| 0–99 | 33 | 0.03 h | 0.18 h |
| 100–299 | 45 | 0.15 h | 0.60 h |
| 300–999 | 75 | 0.73 h | 2.33 h |
| 1,000+ | 49 | 2.29 h | 11.75 h |

Wall clock is size (log-log r = 0.70 over the 202). Two things the audit
found **not** to be the cost, each with its own window: the PR-open-to-merge
tail, 0.05 h median over the same 202 PRs; and the review crew, which on
the 17 PRs #187–#203 whose shards carry rosters ran 6.4 dispatches per PR —
one full-crew round, then a lone scoped re-reviewer in 27 of the 43 later
rounds. What makes a PR large is the repair loop: on those 17 PRs the merged
diff is a median 1.64x the round-1 diff. The recommendation that came out
of the audit is a size budget at intake, not a change to
review.

A third window, one PR, measured the other end of the scale: on 2026-09-27
an agent given only the repository's own files took a fresh clone to an
open PR for a one-line docs fix (#320) in **22 minutes 19 seconds**, 10 of
them the review round. That is a floor for an outside contributor: two of
the run's three failures were worked around by reading the founder's own
checkout. Three full suite runs and about 4,200 lines of repository files
were read for the one line changed. These figures come from the newcomer
trace record of 2026-09-27, which the public export does not carry.

## If you meter this through the API instead

Some readers will run this on API keys rather than seats — a CI-hosted gate, or
an org with no Claude Code subscriptions. For them, and **only** for them:
priced at first-party list rates, the window above comes to roughly **$4,830,
about $42 per merged PR** (by day: $20, $41, $105, $33), split by model:

| Model | Cost at list | Share |
|---|---|---|
| `claude-fable-5` | $2,264 | 47% |
| `claude-opus-5` | $1,530 | 32% |
| `claude-opus-4-8` | $1,029 | 21% |
| `claude-sonnet-5` | $7 | 0.1% |

Read that as a **ceiling, not a forecast**, for four reasons:

- **Nobody paid it.** It is list rates applied to measured tokens. The same
  work on a subscription cost the seat price and nothing more.
- **The mix was expensive on purpose.** Fable 5 is roughly 2x Opus per token,
  so its 28% of the tokens became 47% of this total. A Sonnet-heavy fan-out
  moves the figure a long way.
- **Everything in the window is counted**, including exploration, abandoned
  attempts, and work that never became one of the 114 PRs.
- **No tuning was applied** — no effort step-down on bounded subagent work, no
  batch API on anything asynchronous.

If a metered per-PR figure is load-bearing for your decision, measure your own
repo for a week rather than adopting this one. And note what the number still
cannot tell you, which is the honest gap: **it does not separate the platform's
overhead from the work itself.** A repo making the same 114 changes with no gate
would still spend most of it. What *is* bounded is the deterministic half — the
mechanical rules gate, `verify`, `certify`, `attest check` and every CI job are
**$0 in model spend**, because none of them calls a model.

## Sizing your own usage

Three levers, in the order they pay off:

1. **Caching.** Non-negotiable, and worth roughly 10x on input volume.
2. **Diff size.** A review pass costs tokens proportional to the *diff*, not
   the repo: `warden diff` writes a byte-deterministic package the reviewer
   reads from disk instead of inheriting through context. Ten small PRs cost
   far less than one PR ten times the size — and gate better.
3. **Effort and model, per role.** Drop subagents to Sonnet 5 at low effort
   before you drop the reviewer off Opus.

One thing not to economize on: **the second judge.** The cross-examiner is one
extra pass over findings already written down — a small fraction of a round —
and removing it removes the only mechanism that stops a confidently wrong
finding from becoming a confidently wrong fix.

## Reproducing this

Token usage is in the harness's transcript records; PR counts come from git;
run outcomes come from the cage ledger.

```sh
git log --format='%cI %s' | grep '(#' | awk '{print substr($1,1,10)}' | sort | uniq -c
ls .warden/memory/attest/ | wc -l          # committed review events
warden memory stats                        # findings, precision, candidates
cage measure ledger.csv                    # loop counts and first-pass rate
awk -F, 'NR>1{print $3}' ledger.csv | sort | uniq -c   # what actually stopped runs
```

---

Next: [Why This Exists](Why-This-Exists.md) · [Memory](Memory.md) ·
[Roadmap](Roadmap.md)
