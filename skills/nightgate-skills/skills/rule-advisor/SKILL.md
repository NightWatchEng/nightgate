---
name: rule-advisor
description: On-demand rule advisor for a repo enrolled on the Nightgate platform — one invocation goes from "what guardrails am I missing" to a reviewable PR containing real rule files, each with a backtest over the repo's own history and a gate run proving the tree still passes. Proposes only, never merges, and NEVER auto-adopts even a rule with a perfect backtest. Distinct from the weekly retro: on-demand, works from full PR history plus prior art, audits the RULE SET rather than the org.
---

# Rule advisor — from "what am I missing" to a reviewable PR of rules

The incidents and reasoning behind these steps live in the Nightgate
platform's docs/design/rule-advisor-rationale.md; this page is the steps.

The skill a founder actually invokes when they want the guardrails a repo
lacks, written down as real rule files they can review — so nobody
hand-transcribes a rule out of a recommendation table and gets the frontmatter
wrong. One invocation produces a PR; a human merges it, or not.

Project specifics come from `.warden/skills-policy.md` (the policy contract).
Missing file or missing section → stop and report; never guess a project's
policy.

## How this differs from the retro (say so when they overlap)

The **retro** (`nightgate-skills:retro`) is weekly, works from this repo's own
recorded review MEMORY, and audits the organization — rules, skills, graph,
vocabulary. The **rule advisor** is on-demand, works from full PR HISTORY plus
the grounded prior-art catalog, and audits the RULE SET only. The retro points
at this skill rather than growing a second, weaker copy of it: when the retro
wants to propose adopting a guardrail, it hands the class to the advisor.

## 1 · Mine the history

`.warden/bin/warden mine [--since <date>] [--repo <owner/name>] [--pr-limit N]` — defect
signals from history that never produced an attestation: reverts and what they
reverted, fix-after-merge, review comments, CI failures. A class it could not
read is reported UNREAD, never counted as zero. No git/GH history → say so and
work from the catalog and corpus alone; a thin mine is a stated limit, not a
silent one.

## 2 · Recommend, backtested

`.warden/bin/warden rules recommend --backtest` — the guardrail GAP (applicable catalog
entries this repo does not enforce) joined against the corpus, and for each
recommendation the **history backtest**: flagged / true-positive /
projected-false-positive over the stated window, or an `UNBACKTESTED` marker
for a rule no regex can replay. Read the rows exactly as the retro's
`## Propose` section reads them:

- **EVIDENCE-BASED** rows have judged findings here; **PRIOR ART** rows rest on
  published sources alone and carry a higher bar — say which, every time.
- A `backtest: REJECTED` row (fired and caught nothing) is reported WITH its
  numbers and is NOT drafted — the rejection is evidence that stops the same
  bad rule being re-proposed. An `UNBACKTESTED` row is drafted only on its
  other evidence, and says the precision is unmeasured, never a fabricated
  number. The projected-FP is a revert-coincidence ESTIMATE, not a bound.

## 2b · Audit the rule set you already have — subtract, don't only add

`.warden/bin/warden rules lifecycle [--window N]` — the other half of the audit, and the
half a recommender-only tool never does. Read three sections, all measured
over the two windows the report prints (the commit replay and the corpus):

- **RETIRE?** — never fired. It says WHICH silence it found: `never-selected`
  (the applies_to matched no commit — check the glob before the rule) or
  `evaluated-silent` (selected repeatedly and silent every time).
- **DEMOTE?** — the corpus argues these down: a rule near-always refuted or
  dismissed-with-reason is actively harmful. Propose demotion out of the
  blocking severities, or a narrowing — never keep it at strength for
  appearances. This is the CHRONIC read; `.warden/bin/warden autonomy pause` is the acute
  one (a streak of consecutive refuting review rounds) and it cannot see a rule that is
  steadily dismissed without ever streaking.
- **NARROW?** — fires on most diffs it is selected on: a convention the repo
  has not adopted, or a scope error. Propose narrowing `applies_to`, never
  deletion.

**Absence of evidence is the hardest case here, and it is not a footnote.**
A rule that has
never fired may be the REASON the thing it guards has never happened. Zero
findings is consistent with the guard HOLDING (authors stopped writing the
hazard) and with the guard being INERT (stale pattern, moved surface), and no
count separates them — deterrence leaves no trace in a findings table. So:
carry the report's own caveat into the PR verbatim, quote the window with every
count, and where the row says the hazard is still PRESENT IN THE TREE unflagged,
propose a FIX rather than a retirement. Never delete a rule file on the strength
of this table: a retirement is a PROPOSAL in the PR body for a human to decide.

## 3 · Draft the rule files

For each proposal that clears the bar, WRITE an actual
`.warden/rules/<id>.md` with correct frontmatter for its engine — not a
description of one:

- **id** ≠ any candidate slug it covers (a rule id equal to a slug retroactively
  invalidates every committed shard filed under `unmapped:<slug>` and drops
  certification); use `covers: [<slug>]` and grandfather the committed ids in
  `.warden/certification.yaml`.
- **engine**: `declarative` (a regex/glob checker), `python` (a checker in
  `warden/mechanical.py` — HIGH-tier, its own reviewed change), `claude` (a
  judgment lens, body IS the charter), or `not-a-rule` (carries `instead:`).
- **severity**: MEDIUM while a rule earns its precision history — blocking
  severity is a promotion, a human act needing 10+ judged, a 0.7 floor, and a
  person. Never ship a covers-bearing rule blocking.
- The body is written from the earned instances, and states its own hard line
  and Do-NOT-flag carve-outs so it stays useful rather than noisy.

Editing an existing rule in place is allowed only when you SAY so in the PR —
never a silent rewrite.

## 4 · Backtest each drafted rule, then gate — ALL of it

Draft each rule's **backtest artifact** under `.warden/memory/backtests/`,
naming the NEW `rules_version` (the rule files are written, so `warden` can
compute it) — certification S-05 counts a backtest only at the current
version. The artifact's SHAPE depends on the rule:

- A **declarative** rule replays as a regex, so the backtest carries the
  `rules recommend --backtest` numbers: `action`, `judged`, and the flagged /
  true-positive / projected-false-positive over the window.
- A **covers-bearing / claude** rule does NOT replay as a regex (its
  `--backtest` row is `UNBACKTESTED`), so its artifact is a corpus-precision
  DECISION record, and every field is load-bearing to S-05: `rule_id`,
  `action`, an integer `judged`, and
  `upheld` / `refuted` / `dismissed` / `rate` / `wilson_lb` **DERIVED from the
  committed corpus shards for `unmapped:<slug>`** — never fabricated, never the
  regex numbers. Certify re-derives these on every run and drops if they drift.
- **`action` names an enum this skill deliberately does not retype.** The
  authority is the warden the CONSUMER has pinned, and the way to ask it is
  `.warden/bin/warden certify --help` plus the S-05 line the run prints; where an action
  is actually rejected, the failure message interpolates the accepted set
  rather than a hand-written list. Do NOT expect a clean run to recite the
  enum — S-05 names it when it refuses something, so on a passing tree you
  will see a pass line and no list. If you cannot get the set out of the
  binary, read `BACKTEST_ACTIONS` in the warden you are pinned to; what you
  must not do is copy a list out of a skill, including this one. If the
  change you are proposing needs an action their warden refuses, that skew
  is a FINDING to raise with the platform pin — never a mislabelled
  `promote`.
- A **promote**-action artifact at the current `rules_version` is the live
  promotion claim, so it also carries `validation`: the
  micro-test record — a **no-guidance control arm run first** (control does
  not exhibit the failure → stop, there is nothing to fix), **5+ reps per
  variant**, **variance as a metric**, and `flagged_matches_read: true` only
  after actually reading every flagged match — or the explicit marker
  `{status: unvalidated, reason: ...}`. S-05 refuses one with neither.
  Never fabricate a micro-test: a protocol that was not run is
  `unvalidated` with its reason, exactly like an UNBACKTESTED rule.
- A **promote** of a rule a RETRO proposed also carries a **promotion
  record**, `.warden/memory/promotions/<rule>-<the retro's date>.json`, with
  `rule_id`, `backtest` (this artifact's file name), `"proposed_by":
  {"source": "retro", "date": "<the retro's date>"}` and a `proposal` citing
  where that retro proposed it — a committed report, never this artifact's
  own note, which is the evidence the citation is meant to corroborate.
  Certification S-04 counts the RECORD, not the artifact: a rule an accepted
  retro proposal routed here, drafted with a backtest and no record, is a
  promotion the loop cannot show it made. A rule this skill proposed on its
  own is NOT one — leave the record out rather than writing down a retro that
  did not run.

A **covers-bearing** rule is a coordinated change that fails certification if
any half is missing: grandfather the committed `unmapped:<slug>` ids in
`.warden/certification.yaml` (E-02), and register the rule in the repo's
corpus-rule drift guards (its `CORPUS_RULES` map, or the equivalent) with the
same derived counts, so a covers-bearing rule that is written but not
registered is caught HERE, not in production.

Then run the gate on the result — **the whole gate, not two commands**:

- `.warden/bin/warden review --base <main> --no-comment` exits 0 (the deterministic gate);
- `.warden/bin/warden certify` still reports its current level (E-02 + S-05 hold);
- **and the policy file's `## Verify` commands** — the repo's own test suite
  and linters. `.warden/bin/warden review` and `certify` do NOT run the repo's tests, and
  on a self-hosting repo the tests are exactly what catch an unregistered
  covers-bearing rule or a drifted count. A PR that says "the tree passes" on
  review + certify alone is overclaiming; run Verify and say what actually ran.

A drafted rule that fails ANY of these is not proposed — it is reported as a
rejected draft with the failure, never forced onto a green-looking PR.

## 5 · Open the PR

`gh pr create --base main`, idempotent. The body carries, PER RULE: the
proposal's basis (evidence vs prior art), the backtest numbers (or the
`UNBACKTESTED` reason), the rule file, and the evidence the tree still passes —
naming what actually ran (`.warden/bin/warden review`, `.warden/bin/warden certify`, AND the policy's
`## Verify` suite), not an unqualified "passes". A suggested review order.
**Never merges.**

## Boundaries

- **Proposes only. Never merges, never pushes to main, never auto-merges.**
- **Never auto-adopts, even a rule with a perfect backtest.** A clean backtest
  measures the PAST; the cost of a bad rule is paid by every future PR author,
  and that is not a cost this tool gets to accept on their behalf. The output
  is a PR a human decides on.
- Never edits a rule, checker, or baseline in place without saying so in the
  PR body — a silent rewrite of gate surface is forbidden.
- Never ships a covers-bearing rule at a blocking severity — promotion to
  blocking is a separate, human decision with its own evidence bar.
- Never fabricates a backtest number: an unmeasurable rule is `UNBACKTESTED`
  with its reason, and a REJECTED backtest is reported with its numbers.
- **Never retires, demotes or narrows a rule itself.** Every lifecycle row is a
  proposal in the PR body carrying its windows and counts. A rule that has
  never fired is never deleted on the strength of silence alone — absence of
  evidence here may be the guard working.
- Never asks a question whose answer is in the repo, the policy, the catalog,
  or the corpus.

## Provenance

`as_of` is the last date this protocol was checked against the system it
describes.

```yaml
as_of: 2026-08-30
assumes:
  skills: [retro]
steps:
  - step: "backtest every drafted rule over history and never auto-adopt even a perfect one"
    evidence: commit:f1bc13d4
  - step: "a REJECTED backtest is reported with its numbers, an UNBACKTESTED rule never gets a fabricated one"
    evidence: commit:988dd3d3
  - step: "draft id != covered slug and grandfather the committed ids, or certification drops"
    evidence: commit:1bdc51d6
  - step: "audit the rule set for dead, noisy and over-broad rules, and never delete one on silence alone"
    evidence: commit:dc04089b
  - step: "a promote artifact carries micro-test validation or an explicit unvalidated marker with the reason"
    evidence: commit:959025ac
```
