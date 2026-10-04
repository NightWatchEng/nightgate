---
name: ship
description: The single entry point — state a requirement in plain language and get open PRs back. Chains intake (requirement to tracked items with an order) and deliver (each item to a merge-ready PR), reporting between items. The founder states intent and reviews results; nothing else.
---

# Ship — requirement in, pull requests out

This is the one thing a founder needs to invoke. Everything below it is
plumbing they should never have to remember.

```
/nightgate-skills:ship  <what you want, in plain language>
```

## Without a tracker — `--no-tracker`

```
/nightgate-skills:ship --no-tracker <what you want, in plain language>
```

`intake`'s output is items filed in the tracker, so under `--no-tracker` it
does not run. Decompose here by its rules instead: each task a testable
outcome with acceptance criteria, PR-sized, with its dependencies and an
order whose reasoning is stated. Show the list, as step 1 shows the order,
and file nothing. Then step 2 runs `nightgate-skills:deliver --no-tracker`
once per task, passing the task and its acceptance criteria as the prompt;
`deliver`'s `--no-tracker` section says what that changes. Everything else
on this page holds, with "item" read as "task".

The cost is stated, not hidden: the list lives in this session and its
reports, and nowhere else. An interrupted queue resumes from the last
report, not from `bd ready`, and an idea discovered mid-queue goes into the
closing summary's "what you would do next" rather than into a tracker.

Without `--no-tracker`, a tracker that cannot be read stops the queue before
step 1 and names the flag; ship never switches modes on its own.

## 1 · Decompose

Run `nightgate-skills:intake` on the requirement. It reads the repo, files
tracked items with acceptance criteria, wires dependencies, and reports the
execution order with its reasoning.

**Show the order before executing anything.** Filing is cheap and reversible;
building the wrong thing is neither. If the requester is present, this is the
moment they can redirect at almost no cost — so make the order easy to
disagree with. If they are away, proceed, and make the ordering rationale
part of the final report.

## 2 · Execute, one item at a time

For each ready item in dependency order, run `nightgate-skills:deliver`.

**Report between items; never batch silently.** After each: what landed, the
PR link, what review found. Someone who sees five PRs appear at once cannot
tell which decision to question. Someone who sees them arrive one at a time
can stop you at the second.

Honor a stated budget — "just the first two", "nothing that touches the
schema". With no budget stated, stop after the last item that has no open
question in front of it.

**Stop the queue when an item comes back blocked.** The next item was
sequenced after something that did not happen; continuing means building on
an assumption that just failed. Report and let the requester choose.

## 3 · Close the loop

End with a summary they can act on without opening the tracker:

- what shipped, one line and a PR link each
- what is blocked, and on what
- what you chose not to attempt, and why
- what you would do next

Lead with the outcome. If everything is waiting on a decision, say that
first — do not bury it under what went well.

## Boundaries

- **Never merges.** Every PR waits for a human, in every repo.
- Never expands scope mid-queue. An idea discovered while building is an
  item to file, not work to add silently.
- Never skips a blocked item to make progress on unrelated ones — the
  requester chose an order and deserves to know it broke.
- Never calls a PR done that has not passed its gate. "Open" and "green" are
  different words.

## Provenance

`as_of` is the last date this protocol was checked against the system it
describes. ship is a thin first-principles orchestrator over
intake and deliver, not a protocol accreted from incidents; its one step is
the `--no-tracker` mode, and a step added to answer a recurring queue failure
would cite its bead here.

```yaml
as_of: 2026-10-04
assumes:
  skills: [intake, deliver]
steps:
  - step: "--no-tracker decomposes in the report instead of running intake, and runs deliver --no-tracker per task; without the flag an unreadable tracker stops the queue"
    evidence: agentops-hy6o.10
```
