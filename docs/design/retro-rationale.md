# retro: the reasoning behind its steps

`skills/nightgate-skills/skills/retro/SKILL.md` is the steps. This page is
why they have the shape they do: the incidents, the measurements and the
arguments that used to sit inside the steps. It was moved here on
2026-09-27 (#330), grouped by the skill section it came from.
The narrated sentences are the skill's own words; what is not is the short
"On …" lead-in naming the step each passage belongs to, a clause restated
where a passage needed its subject back, the "That is" opening a passage
the skill had joined to its sentence with a dash, "the skill" where the
skill said "this file" or "here" of itself, the citation lines, and — where
a later change overtook a passage's argument — that passage's past tense
and the note after it. Every guard, command, refusal and boundary stayed
in the skill. Where this page and the skill disagree, the skill governs,
and this page is stale.

Where the skill kept the start of a sentence and moved its tail, the tail
is here, with enough of the sentence to read on its own. A bead id the skill
cited beside a step is listed under that section as a citation.

## 1 · Gather (evidence before opinions)

On reporting an unread or failed-ingest corpus as unread, never as clean.
That is the step-1 discipline turned on the retro's own first input
(#150; hit live by the 2026-08-29 retro).

On reading every `memory stats` section by its header. Restating a rendered
line in the skill is how the skill's description of warden's output goes
false — twelve of the nineteen upheld findings against `retro/SKILL.md` were
exactly that, and the two sections the skill had never named, LENSES and
DISPATCHES, went unread for as long as they have existed (#265).

On `rules recommend` being unavailable. The step ships in the skill pack,
which installed independently of a repo's `platform.pin`, so a consumer on
an older pin had a warden without this subcommand. That skew is a recorded
incident class here, not a hypothetical.

Overtaken since: #331 made the documented install put the pack at the
`platform.pin` tag, so the documented install no longer skews. It also
added `warden skills pin`, which exits non-zero when the installed pack is
not the one the pin serves — but as of 2026-09-27 no release carries #331,
so a consumer on a pinned release has no such subcommand and nothing flags
a pack installed some other way. The skill's fallback still covers that
pack, and a machine installed before #331.

On gathering the guardrail gap at all. Without this input the retro can only
learn from defects that already bit HERE, which is how a 32-entry catalog of
this platform's own advice went a release without ever being checked against
the repo that ships it (#97).

Citation: the `catalog check --online` CURRENCY report — #119.

## 2 · Mine escapes (the recall side)

Citation: export the token for `warden mine` rather than reporting around the
gap — #150.

## 3 · Propose (each with citations, or not at all)

On quoting the two caveats beside a promotion's stats line. A promotion
proposal that quotes the rate without them is quoting a number the platform
itself prints a warning next to (#186).

On never proposing a rule below either bar, however obvious it feels. The
gate exists because feelings overfit.

On reporting variance as a metric in the micro-test protocol. Their own
release notes caught a regression this way: control 8/10 → treatment 5/10.

Citation: the micro-test protocol, and the `validation` field a promote
artifact carries — #155.

On pausing a noisy rule by default. A noisy rule taxes every review: the
>10%-not-useful precedent.

Citation: a rule id equal to its slug shadows the committed evidence that
justified it and drops certification — #143.

Citation: backtest a rule proposal first — #120.

On handing an accepted rule proposal to `rule-advisor`. The two overlap only
on `rules recommend`, and this step is where the retro points at the advisor
instead of growing a second, weaker copy of it.

On reading every tag audit line your own pinned warden prints, not a list
typed into the skill. The skill's enumeration has gone stale once already —
the drift ceiling landed (#174) and nothing sent a retro to look
at it, so the ceiling's only human consumer did not know it existed
(#178).

On the four unreadable-declaration labels. Until #195 all four
printed as the ceiling's, which sent an author to a block that was fine.

Citation: change a skill, the craft-layer half of the loop — #112.
