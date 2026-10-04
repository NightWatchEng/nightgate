# rule-advisor: the reasoning behind its steps

`skills/nightgate-skills/skills/rule-advisor/SKILL.md` is the steps. This page
is why they have the shape they do: the incidents and the arguments that
used to sit inside the steps. It was moved here on 2026-09-27 (#330),
grouped by the skill section it came from. The narrated
sentences are the skill's own words; what is not is the short "On …"
lead-in naming the step each passage belongs to, a clause restated where a
passage needed its subject back, the "That is" opening a passage the skill
had joined to its sentence with a dash, "the skill" where the skill said
"here" of itself, and — where a later change overtook a passage's argument
— that passage's past tense and the note after it. Every guard, command,
refusal and boundary stayed in the skill. Where this page and the skill
disagree, the skill governs, and this page is stale.

Where the skill kept the start of a sentence and moved its tail, the tail is
here, with enough of the sentence to read on its own.

## 2b · Audit the rule set you already have — subtract, don't only add

On why the audit subtracts as well as adds. Adding without ever retiring
produces rule debt, and rule debt is worse than a missing rule: a gate that
fires constantly on things nobody acts on trains every author to skim past
it, which silently disables the rules that DO matter.

## 4 · Backtest each drafted rule, then gate — ALL of it

On not copying the backtest `action` enum out of a skill. This is not
pedantry — the enum has gone stale twice in the skill, and the pack shipped
from the DEFAULT BRANCH while every repo pins a warden VERSION, so a list
written in the skill was wrong for a pinned consumer in both directions.

Overtaken since: #331 made the documented install put the pack at the
`platform.pin` tag, so the both-directions skew is no longer the default.
It also added `warden skills pin`, which exits non-zero when the installed
pack is not the one the pin serves, but as of 2026-09-27 no release carries
#331, so on a pinned release nothing flags a pack installed some other
way. The rule stands on the rest of
its argument: the pinned warden is the authority on the enum, and a list
typed into a skill is a second copy, one that has gone stale twice.

On raising an action skew as a finding, never a mislabelled `promote`: a
mislabelled `promote` is how a reword once shipped as a promotion and
dropped a repo a certification level with CI green.

On a covers-bearing rule failing certification if any half is missing. That
is the trap that took this repo LEVEL 5 → LEVEL 3 live.
