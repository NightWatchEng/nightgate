---
name: intake
description: Turn a requirement stated in plain language into well-formed tracked items with dependencies and a reported execution order. The founder describes what they want; this does the decomposition, the acceptance criteria, and the sequencing. Writes no code.
---

# Intake — a requirement becomes a plan someone else could execute

Someone described what they want. Your job is to turn that into tracked items
precise enough that a stranger — or an unattended session at 2am — could
execute them without asking a follow-up question.

You write no code here. The output is items and an order.

## Which warden — resolve it before any other step

Run this from the repo root before anything else; every command below is
written `$WARDEN <subcommand>`.

```sh
if [ -x .warden/bin/warden ]; then echo .warden/bin/warden
elif ! command -v warden >/dev/null 2>&1; then
  echo "REFUSED: no .warden/bin/warden in this repo and none on PATH; install the release repo.yaml's platform.pin names" >&2; false
else
  pin=$(awk '/^platform:/ {p=1; next} p && /^[^[:space:]#]/ {exit} p && /^[[:space:]]+pin:/ {sub(/^[[:space:]]+pin:[[:space:]]*/, ""); sub(/[[:space:]].*$/, ""); gsub(/["'\'']/, ""); sub(/^v/, ""); print; exit}' repo.yaml 2>/dev/null)
  have=$(warden --version 2>/dev/null | awk '{sub(/^v/, "", $2); print $2}')
  if [ -n "$pin" ] && [ "$have" = "$pin" ]; then echo warden
  else echo "REFUSED: PATH has warden ${have:-of no readable version}, repo.yaml's platform.pin is ${pin:-unreadable}; install the pinned release" >&2; false; fi
fi
```

It prints this repo's `.warden/bin/warden` launcher when there is one, else
`warden` on PATH when its `--version` is `repo.yaml`'s `platform.pin` (warden
init enrolls a consumer with no launcher). A refusal is a stop: report it and
never substitute another warden. Put the printed word wherever `$WARDEN`
appears, or start each shell command with `WARDEN=<that word>;` — a variable
does not survive between tool calls. Each probe fence below opens with the
line `: "${WARDEN:?is unset; run the resolution step first}"`, which stops on
an unset `$WARDEN` that would otherwise fall silently into the probe's
fallback arm. Run that line first, too, before any other step that reads a
failed command as an older pin: unset, `$WARDEN rules recommend` runs `rules
recommend`, exits 127 and reads as a subcommand the pin lacks.

## 1 · Understand before decomposing

Read the repo first, not the requester's mind: `$WARDEN explain` for
components and risk tiers, `.warden/skills-policy.md` for how work is
verified and shipped, and the existing tracker for items that already cover
part of this.

**Never ask a question whose answer is in the repo, the tracker, or the
policy.** Look it up. Every question you ask is attention you are spending
from someone who came here to spend less of it.

Ask only where an answer would change the decomposition itself — a genuine
fork in scope, an irreversible choice, a business call you cannot infer.
Otherwise pick the sensible default, proceed, and *say which default you
picked* so it can be corrected cheaply.

## 2 · Decompose

One item = one outcome a person can verify and a reviewer can judge in a
single PR. Split when a change spans components or mixes risk tiers; do not
split what only makes sense reviewed together.

Each item carries:
- **title** — the outcome, not the activity. "Ratchet the baseline so a
  regression fails CI", not "work on baselines".
- **description** — why it exists, what changes, and the constraints that
  are not obvious from the title. Write it for someone who was not in this
  conversation, because that is who will read it.
- **type / priority** — priority reflects consequence-of-delay, not
  enthusiasm.
- **acceptance** — how a reviewer knows it is done. If you cannot state a
  test or an observable outcome, the item is not ready to be an item; say
  so instead of writing a vague one.

Reuse an existing item rather than filing a near-duplicate. A tracker that
lies about what is outstanding is worse than no tracker.

## 3 · Sequence

Wire real dependencies so "ready" means ready. A dependency is real when the
later item genuinely cannot start — not merely when it would be tidier to do
first.

Then report the order and the *reasoning*, in prose: what comes first and
why, what is parallel, what is deliberately last. The requester should be
able to disagree with your ordering without reading the items.

Call out explicitly:
- anything you deliberately did **not** file, and why
- anything that needs a human decision before it can be worked
- any item you would drop if the budget were smaller

## 4 · Hand off

Say what was filed, in what order it will run, and what happens next. If the
requester wants execution, `nightgate-skills:deliver` takes one item from
ready to a merge-ready PR — offer that rather than describing commands.

## Boundaries

- **Writes no code, opens no PRs, changes no config.** Filing is not doing.
- Never invents requirements to look thorough. Two honest items beat six
  speculative ones, and speculative items are read as commitments later.
- Never files an item it cannot state acceptance for — that is a question to
  ask or a default to name, not an item to file.
- Never silently reinterprets the request. If your decomposition drifts from
  what was asked, say where and why.

## Provenance

`as_of` is the last date this protocol was checked against the system it
describes. intake is first-principles decomposition, not a
protocol accreted from incidents, so it carries no incident-earned steps; if
a filing failure mode ever recurs, the step that answers it cites the bead
here.

```yaml
as_of: 2026-08-30
assumes:
  skills: [deliver]
steps: []
```
