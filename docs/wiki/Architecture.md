# Architecture

One platform repo, five products, every consumer on config alone.

This page is the hub. The structure is drawn once, on the
[C4 ladder](https://c4model.com/) below — four levels, one shared palette, and
you stop at the level that answers your question. **No other wiki page draws a
structural diagram**, so there is no second picture to drift out of agreement
with these.

| # | Level | Zoom | Answers |
|---|-------|------|---------|
| 1 | [System Context](Architecture-1-System-Context.md) | highest | who uses Nightgate, and which external systems it touches |
| 2 | [Containers](Architecture-2-Containers.md) | ↓ | the separately runnable pieces, where each executes, and what a consuming repo holds |
| 3 | [Component — warden](Architecture-3-Component-Warden.md) | ↓ | inside the gate: policy → enforcement → evidence → provenance, as a call graph |
| 4 | [Sequence — one item to a PR](Architecture-4-Sequence-Bead-to-PR.md) | runtime | the order every change goes through, and what each step fails on |

**C4 in one paragraph.** C4 is Context, Containers, Components, Code. Each
diagram has one audience: a reader evaluating the platform stops at Context; an
engineer keeps going. The structural levels are styled Mermaid flowcharts on
the standard C4 palette (person `#08427b`, system `#1168bd`, store `#2e6fb0`,
external `#8a8a8a`) rather than Mermaid's native `C4*` types, which overlap
labels and do not route edges. Runtime flow uses `sequenceDiagram`. Level 4
"Code" is deliberately skipped — the source *is* that diagram, and a drawing of
it would go stale the day it was made.

**Keeping them honest.** Updating an architecture-affecting diagram is part of
the Definition of Done for the work that changes it — same PR as the code.
Where a page and the code disagree, the code and its tests win, and the diagram
is the bug.

## The five products

| Product | What it is | Page |
|---|---|---|
| **warden** | the CLI: `repo.yaml` policy in, gated review, deterministic verification, attestations and evidence out. The checker registry is pluggable — a project drops Python checkers in `.warden/checkers/` for its own `engine: python` rules | [Gate Pipeline](Gate-Pipeline.md) |
| **The cage** | the unattended dev-loop runner, and every hard stop around it | [The Cage](The-Cage.md) |
| **The skill pack** | nine judgment protocols, served from this repo's plugin marketplace | [Skill Pack](Skill-Pack.md) |
| **Memory + retro** | committed review events, split recall, and the bar a rule must clear | [Memory](Memory.md) |
| **The graph layer** | the agent org as declared, validated config | [Graph Layer](Graph-Layer.md) |

The platform pin lives in `repo.yaml` (`platform.pin`) and is stamped into
every run manifest, so a version bump can never silently change which policy a
verdict was judged under.

## Repo layout

```
nightgate/
├── warden/             # the CLI package (+ schemas/)
├── cage/               # cage.toml schema, renderer, generic run.sh
├── skills/nightgate-skills/   # the plugin (skills/<name>/SKILL.md)
├── .claude-plugin/     # marketplace manifest
├── .warden/            # this repo's own gate config, rules, and corpus
├── examples/hello-svc/ # the standing portability fixture
├── docs/wiki/          # every page of this documentation
├── docs/design/        # design records
├── graph.yaml          # this repo's own declared org
└── repo.yaml           # this repo's own policy (dogfood)
```

## Self-hosting, and the standing proof

- **warden gates warden.** This repo's PRs run the same gate it ships —
  `repo.yaml` at the root, and CI jobs for the gate, the tests, commit
  messages, and the portability proof.
- **`examples/hello-svc` is a complete miniature consumer**, enrolled from the
  [Adopting](Adopting.md) path by platform CI on **every PR**. If enrollment
  mechanics break, the platform goes red before a real consumer feels it.
- **The cage is declared for the platform itself.** `.cage/` is the versioned
  source of nightgate's own `cage.toml` and prompt, held in lockstep with the
  policy by test; the founder installs it beside the checkout. Two unattended
  runs have completed on it: one stopped at a forbidden path with its work
  unpushed, and one opened PR #279, which the founder merged. Seven earlier
  runs on a consumer opened no PR
  ([The Cage](The-Cage.md#the-platforms-own-cage)).

## Versioning and distribution

| Surface | Mechanism | Drift protection |
|---------|-----------|------------------|
| CLI | `uv tool install` from a release tag; the gate `warden init` writes builds the tag `platform.pin` names | pin in `repo.yaml`, stamped in every manifest, CI pin-match check |
| Skills | plugin marketplace added by its GitHub URL, user-level install | prose contracts; enforcement stays in the pinned CLI and the cage |
| Cage | `cage enroll` re-render | generated artifacts carry banners; the runner is byte-identical and hash-comparable |
| Schemas | shipped in the package | additive-only, and a protected path |

---

Next: [Level 1 · System Context](Architecture-1-System-Context.md) ·
[Configuration](Configuration.md)
