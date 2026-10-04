---
name: review-queue
description: Founder's interactive companion for reviewing the cage's autonomous-run PRs — gathers each run's evidence chain, optionally convenes the project's review crew, walks each PR to a decision, and records outcomes. The founder holds the merge button.
---

# Review Queue — founder + evidence

Interactive session, founder present. The job: get every open autonomous-run
PR to a decision (merge / request changes / reject) in 10–20 minutes,
evidence first.

Project specifics come from `.warden/skills-policy.md`; the cage directory
(reports, ledger) is named in the project's cage enrollment
(`cage.toml`). No cage enrollment → gather evidence from the PRs
alone and say so in the digest.

## Procedure

1. **Gather the runs' evidence** (before any opinions):
   - `gh pr list --state open` filtered to the project's run branch
     prefix; for each PR: the diff, the body (bead id, attestation summary,
     executor result, findings dispositions, Suggested Review Order), and
     `gh pr checks`.
   - The cage runner report(s) in the cage dir's `reports/` and the last
     ledger lines in its `ledger.csv` — including skipped or blocked runs
     (those need eyes too: WHY did it skip?).
   - Present a 5-line digest first: what happened, what's waiting, anything
     red.
2. **Convene the crew** — only if the policy's `## Integrations` names a
   `review-crew` hook: invoke it as written there, seeded with the digest
   and the first PR's diff. No hook → the founder and you walk the evidence
   directly.
3. **Walk each PR** in the Suggested Review Order, founder deciding.
   Surface disagreement — don't sand it down.
4. **Record every decision**:
   - Merge: only on the founder's explicit word in this session
     (`gh pr merge` is founder-authorized here and only here).
   - Request changes / reject: PR comment with the reason AND a bd comment
     on the bead (rejection reasons are ledger food — they teach the loop).
   - Close the bead only when its PR is merged.
5. **Wrap**: if the policy's `## Integrations` names a `memory` hook,
   record the runs' narrative exactly as written there (this session —
   not the autonomous run — owns narrative memory). Note the running
   acceptance rate (merged ÷ opened, from the ledger) and flag when it dips
   below 70% — the pre-agreed trust-ladder demotion signal, and the trigger
   to drop any per-diff crew pass to high-risk-only if usage cost is the
   culprit. ("opened" counts from the ledger; "merged" from
   `gh pr list --state merged` on the run branch prefix — the ledger
   records runs, never review-queue merge decisions.)

## Boundaries

- **Merges happen only on the founder's explicit instruction, in-session,
  per PR** — never inferred, never batched without a per-PR yes.
- Never edits code during review — findings go back through a bead or a
  request-changes comment, not live patches on main.
- Never modifies the cage, gate surface, or ledger history
  (append-only; corrections are new entries).
- If the founder is absent (no responses), this skill does nothing
  autonomous: summarize state and stop.

## Provenance

Earned steps and the incidents behind them. `as_of` is the
last date this protocol was checked against the system it describes.

```yaml
as_of: 2026-08-28
steps:
  - step: "never rewrite ledger history — corrections are new append-only entries"
    evidence: tag:corpus-integrity
```
