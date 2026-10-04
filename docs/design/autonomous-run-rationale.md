# autonomous-run: the reasoning behind its steps

`skills/nightgate-skills/skills/autonomous-run/SKILL.md` is the steps. This
page is why they have the shape they do: the history and the arguments that
used to sit inside the steps. It was moved here on 2026-09-27 (#330),
grouped by the skill section it came from. The narrated
sentences are the skill's own words, verbatim; what is not is the short
"On …" lead-in naming the step each passage belongs to. Every guard,
command, refusal and boundary stayed in the skill. Where this page and the
skill disagree, the skill governs, and this page is stale.

## 0.5 · RESUME — continue, don't restart

On continuing an interrupted bead instead of starting it over. Restarting
throws away real work; the old behavior — marking a bead attempted and
skipping it forever — abandoned it.

## 1 · DISCOVER — pick exactly one bead

On overwriting `.run-progress` after each phase. It costs one file write per
phase and is the difference between a 2-hour timeout losing everything and
losing nothing.

## 2–5 · PLAN, BUILD, VERIFY, SHIP — run `deliver`

On the protocol living in exactly one place. Two copies drift, and the copy
that drifts unattended is the one nobody notices.

On writing the named root cause into the bd comment, not just into your
reasoning. Nobody is present to hear it, and a symptom fix that goes green
unattended is the one failure the review queue cannot distinguish from a
real one.

## 6 · WRAP — evidence, always

On the `loops:` line of `.run-summary`. The loop count is trajectory
evidence — the retro reads it to ask whether runs are getting more
efficient, which outcome alone can never answer.
