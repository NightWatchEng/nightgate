# deliver: the reasoning behind its steps

`skills/nightgate-skills/skills/deliver/SKILL.md` is the steps. This page is
why they have the shape they do: the measurements and the arguments that
used to sit inside the steps. It was moved here on 2026-09-27 (#330),
grouped by the skill section it came from. The narrated
sentences are the skill's own words; what is not is the short "On …"
lead-in naming the step each passage belongs to, and the "That was" or
"That is" opening a passage the skill had joined to its sentence with a dash
or a "which". Every guard, command, refusal and boundary stayed in the
skill. Where this page and the skill disagree, the skill governs, and this
page is stale.

Where the skill kept the start of a sentence and moved its tail, the tail is
here, with enough of the sentence to read on its own.

## 2.5 · Design — HIGH-tier only, before a line is written

On MEDIUM and LOW tiers skipping this step. A Design phase that taxed every
change would be a ritual, not a gate. The line is drawn where a wrong move
is most expensive: the gate surface, the schemas, CI, the cage — the
HIGH-tier paths where a decision the org can later read is worth the minute
it costs.

## 3 · Build

On `round classify` exiting 2 because it could not read the chain or the
payload. That was the cause this skill used to name alone.

On the repair budget being a count of change-defect rounds. Why this shape:
on the three PRs with round-level data, 0 of 28 round-2+ findings were HIGH
and 12 of 13 cited a file a prior repair commit wrote, so a round counter
stopped on the wrong thing three times in one session with the change right
every time (#185).

## 4 · Verify — all of it, in order

On gate parity. Discovering it here costs a minute, discovering it in CI
costs a round trip.

## 5 · Ship

On linting the title before the PR is created. That is the round trip this
removes: a title that lists every item id is how titles failed lint after
the PR was already open.
