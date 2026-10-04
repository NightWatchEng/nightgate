# Nightgate

**A deterministic gate for agent-written code.** You declare the policy a
change must satisfy; Nightgate enforces it with something that can auto-reject,
and leaves an evidence artifact bound to the commit and to the exact policy
revision that judged it.

Enrolling a repo means writing config. Nobody forks the tooling.

```
/nightgate-skills:ship  add rate limiting to the public API, 100 req/min per key
```

That decomposes the requirement into tracked items, shows you the order, then
takes each item to a merge-ready PR — plan, build, verify, adversarial review,
gate. You review and merge. **Merge authority never transfers.**

New here? → **[Why This Exists](Why-This-Exists.md)** for the argument, or
**[Quickstart](Quickstart.md)** to gate a repo in fifteen minutes.

> **Never hand-edit a page on the GitHub wiki tab.** Every page here is a file
> under `docs/wiki/` in the nightgate repo, reviewed and merged like code. The
> wiki tab is a rendered **mirror**, republished one-way on merge, so a
> hand-edit there is overwritten by the next publish. Change the file, open a
> PR.
>
> **Publish status:** the publish mechanism is wired in this tree —
> `scripts/build-wiki.sh` plus a workflow that runs it. That is a statement
> about this repo's files: no check here can see the wiki tab either way, and a
> GitHub wiki repo does not exist until someone creates its first page by hand.

| Page | System |
|------|--------|
| **Introduction** | |
| [Why This Exists](Why-This-Exists.md) | The problem, the bet, the costs, and what is still unproven |
| [Installation](Installation.md) | Prerequisites, the pinned CLI shim, the skill pack |
| [Quickstart](Quickstart.md) | Zero to a gated PR, on a real repo |
| **Usage** | |
| [Adopting](Adopting.md) | Full enrollment, step by step, with the caveats |
| [Writing Rules](Writing-Rules.md) | The three engines, rule frontmatter, and the guardrail catalog |
| [Gate Pipeline](Gate-Pipeline.md) | What runs on a PR, what blocks, what evidence lands |
| [Skill Pack](Skill-Pack.md) | The nine skills and which one you actually invoke |
| [The Cage](The-Cage.md) | Unattended runs, and every hard stop around them |
| [Memory](Memory.md) | How review findings compound into rules |
| [Graph Layer](Graph-Layer.md) | The agent org as declared config |
| **Reference** | |
| [Architecture](Architecture.md) | The C4 ladder — hub |
| ↳ [1 · System Context](Architecture-1-System-Context.md) | Who uses it, what it touches |
| ↳ [2 · Containers](Architecture-2-Containers.md) | The runnable pieces |
| ↳ [3 · Component: warden](Architecture-3-Component-Warden.md) | Inside the gate |
| ↳ [4 · Sequence: item → PR](Architecture-4-Sequence-Bead-to-PR.md) | The runtime order |
| [CLI Reference](CLI-Reference.md) | Every `warden` and `cage` command |
| [Configuration](Configuration.md) | `repo.yaml` and `cage.toml`, key by key |
| [Skills Policy](Skills-Policy.md) | The `.warden/skills-policy.md` contract |
| [Cost and Throughput](Cost-and-Throughput.md) | What it costs to run, model choice, measured token usage, time to PR |
| [Releasing](Releasing.md) | Cutting a release: what moves in lockstep, the tag, the consumer pin bump |
| [Roadmap](Roadmap.md) | Open work, and what is deliberately not built |

## Vocabulary

A **bead** is one tracked work item. This platform tracks work with the `bd`
tracker; the skills say *tracked item* when speaking to someone who does not
use `bd`. Same thing.

An **attestation** is the record that a pre-PR adversarial review happened:
who reviewed, what they found, the verdict, the commit SHAs, and the
`rules_version` in force. It is committed evidence, not a receipt.

`rules_version` is a hash over the whole policy — rule files, checker code,
schemas, and `repo.yaml`'s `review:` keys. Two verdicts are comparable only
if they carry the same one.

## Ground rules for these pages

- **Where a page and the code disagree, the code and its tests win.** File a
  bead.
- **Diagrams live on the C4 ladder only.** Four levels, one shared palette. A
  page that needs a picture links to the level that already draws it rather
  than drawing a second one that can drift.
- Design records — why memory, the graph, and the craft layer are shaped the
  way they are, and what was deliberately refused — live outside the wiki in
  [docs/design/](../design/).
