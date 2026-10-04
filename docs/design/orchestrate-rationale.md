# orchestrate: the reasoning behind its steps

`skills/nightgate-skills/skills/orchestrate/SKILL.md` is the steps. This page
is why they have the shape they do: the incidents and the arguments that
used to sit inside the steps. It was moved here on 2026-09-27 (#330),
grouped by the skill section it came from. The narrated
sentences are the skill's own words; what is not is the short "On …"
lead-in naming the step each passage belongs to, and a clause restated where
a passage needed its subject back. Every guard, command, refusal and
boundary stayed in the skill. Where this page and the skill disagree, the
skill governs, and this page is stale.

Where the skill kept the start of a sentence and moved its tail, the tail is
here, with enough of the sentence to read on its own.

## 0 · The one rule that earns this skill its place

On why a builder brief names the skill to invoke and never restates the
protocol's steps. Four parallel PRs once failed the gate on the same missing
step because the orchestrator hand-wrote each brief from memory — every
brief said "run the review" and "the gate must pass", and every builder did,
yet all four dropped the one step with no immediate feedback (committing the
attestation shard). The protocol already lived, correct and complete, in
`nightgate-skills:deliver`; the prose paraphrase is where it went missing.
A brief that restates steps is a fork of the protocol that drifts the day
it is written. Even a brief that says "follow deliver" in prose is not
enough on its own — the follow-up wave showed builders still stalling
before Ship — which is why collection below re-verifies the ship tail
instead of trusting that it ran.

## 2 · Dispatch

On running `.warden/bin/warden skills preflight` first. A brief naming a
skill the installed pack no longer serves has happened on record.

On the isolated working directory keyed to each builder's branch. This is
where the collision actually happens, because fanning builders out is what
creates it. On record: two builders both wrote
`scratchpad/candidates.json`, and one's cross-examiner read the OTHER's
candidate list — well-formed, plausible, about a different PR — which would
have produced a schema-valid attestation whose findings describe another
branch's code. Caught by a harness notice, not by anything in the platform.

## 3 · Collect — the ship-stage checklist, per PR

On why collection is a checklist at all. Builders stall most often at the
very end, where the steps have no immediate feedback.

On each probe branching. A bare probe line would not: `>/dev/null 2>&1`
throws away the `invalid choice` diagnostic, so a probe whose status nothing
reads leaves a failure byte-identical to a success, and one status for two
commands cannot say WHICH half is missing when the halves are supposed to
degrade one at a time.

On the board's AGE column. The age is the signal: a unit ten minutes past
`round-attested` and a unit three hours past it are identical in every other
artifact on disk, and that difference is the only question you were asking.

On the committed-attestation check. An attestation left sitting in the
gitignored `.warden/out/` instead reads as success locally and fails the
gate one push later — this exact silence is the incident this skill exists
to close.

On what `--checks-only` cannot re-verify. `deliver`'s Ship lints the title
BEFORE `gh pr create`, and that ordering is the round trip being removed.

On the hand-run fallback. These are the checks this skill has always
carried.
