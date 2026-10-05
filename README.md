# Nightgate

**A deterministic gate for agent-written code.** You declare the policy a
change must satisfy; Nightgate enforces it with something that can auto-reject,
and leaves evidence bound to the commit and to the exact policy revision that
judged it.

Agents write code faster than review absorbs it, and they are confidently
wrong. So the interesting question isn't *"can an agent write this?"* — it's
**"what had to be true before this change was allowed to merge, and can you
prove it afterward?"**

Enrolling a repo means writing config. Nobody forks the tooling.

## Install

You need read access to `NightWatchEng/nightgate` and git credentials that
can read it over https; `gh auth setup-git` sets those up. Then one command
installs both `warden` and `cage`, with no clone of the platform:

```sh
uv tool install git+https://github.com/NightWatchEng/nightgate@v3.0.2
warden --version
```

`warden --version` prints the tag's version, `warden 3.0.2`. If `uv` cannot
find `v3.0.2`, run
`git ls-remote --tags https://github.com/NightWatchEng/nightgate v3.0.2`.
A published tag, lightweight or annotated, prints one line: an object id, a
tab, and `refs/tags/v3.0.2`. No output means the release tag is not published
yet. Without access, git fails instead. Over https it asks
`Username for 'https://github.com':` when it has no credentials, prints
`Invalid username or token` and `Authentication failed` when a stored token
is invalid or expired, and prints `Repository not found` when valid
credentials belong to an account without access. Over ssh
(`git@github.com:NightWatchEng/nightgate.git`) it prints
`Permission denied (publickey)` when GitHub accepts no key you offer, and
`ERROR: Repository not found.` when the key's account has no access.
**[Installation](docs/wiki/Installation.md)** has the access check, the skill
pack, and the pinned form an enrolled repo commits.

## Try it

Access comes first: the install line needs the read access and git credentials
described under Install. Then, from the root of your own Python, Node or Go git
repository:

```sh
uv tool install git+https://github.com/NightWatchEng/nightgate@v3.0.2
warden init
warden certify --level 3
```

`warden init` writes `repo.yaml`, starter rules, a skills policy and the CI
gate, and prints what is left to do by hand: commit the enrollment, and store
the deploy key the gate installs the platform with. `warden certify --level 3`
then reports `certification: LEVEL 3 (Reviewed)` with no file written by hand.

Platform CI builds each PR's wheel, installs it in place of the tag, and runs
the other two commands, read from this block, on fresh Python, Node and Go
repositories, so the path you just ran cannot rot silently.
`examples/hello-svc` is a complete miniature consumer it enrolls as well.

→ **[Quickstart](docs/wiki/Quickstart.md)** · **[Adopting](docs/wiki/Adopting.md)**

## Use it

State what you want. The agents do the rest.

```
/nightgate-skills:ship  add rate limiting to the public API, 100 req/min per key
```

That decomposes the requirement into tracked items, shows you the order before
building anything, then takes each item to a merge-ready PR — plan, build,
verify, adversarial review, gate — reporting between items. You review and
merge. Nobody runs the pipeline by hand, including at 2am.

Changing this repository itself: [CONTRIBUTING.md, *Landing a change here*](CONTRIBUTING.md#landing-a-change-here).

## The four commitments

Everything else follows from these.

- **Policy → enforcement → evidence.** Every rule is declared in config,
  enforced by something that can auto-reject, and leaves an artifact bound to a
  commit.
- **Hard stops live outside the model.** Budgets, gates and kill switches are
  enforced by code the agent cannot edit mid-run.
- **Merge authority is human.** It never transfers. Its *execution* may be
  delegated by an explicit, current, revocable grant, on the terms
  `graph.yaml`'s `review.delegation` declares — the terms live there, the
  grant never does, because a tracked file has no clock and a session grant
  recorded in one outlives its session. No agent grants itself the button.
- **$0 standing infrastructure.** CI, cron, or a laptop. No servers, no paid
  ingestion APIs.

## The five products

| # | Product | What it does |
|---|---------|--------------|
| 1 | **warden** | the CLI. `repo.yaml` policy in → gated review, deterministic verification, attestations and evidence out |
| 2 | **The cage** | the unattended dev-loop runner, and every hard stop around it — quiet hours, kill switches, PR back-pressure, main-green, watchdog, forbidden paths |
| 3 | **The skill pack** | nine judgment protocols. `ship` is the only one you invoke; the rest are what it delegates to |
| 4 | **Memory + retro** | review findings become committed events. Reviewers get *patterns*, cross-examiners get *precedents* — different information, so the two judges don't correlate |
| 5 | **The graph layer** | the agent organization as declared config: who finds, who judges, who gates, who merges |

## Documentation

Everything lives in the **[wiki](docs/wiki/Home.md)**.

| Start with | For |
|---|---|
| [Why This Exists](docs/wiki/Why-This-Exists.md) | the argument, the costs, and what is still unproven |
| [Quickstart](docs/wiki/Quickstart.md) | zero to a gated PR |
| [Adopting](docs/wiki/Adopting.md) | full enrollment, plus the optional `## Build disciplines` craft layer |
| [Cost and Throughput](docs/wiki/Cost-and-Throughput.md) | what it costs to run (a subscription seat), which model, measured token usage, time to PR |
| [Architecture](docs/wiki/Architecture.md) | the C4 ladder |
| [Roadmap](docs/wiki/Roadmap.md) | open work, and what is deliberately refused |

Design records are in [docs/design/](docs/design/). Work is tracked as beads in
the `bd` tracker (`bd list`).

## Status

The platform gates its own PRs with the code inside them, and a built-in
example project is enrolled and gated by CI on every change — so portability is
a standing check rather than a claim.

What is **not** proven: that the memory loop keeps running without a person
starting it (five rules have gone through it, four proposed by one retro in
August), that unattended cage runs ship repeatedly (one has: run
20260921-070026 opened PR #279, which the founder merged; the redesign asks
for ten), and that someone outside this org can enroll from the docs alone.
[Why This Exists](docs/wiki/Why-This-Exists.md#what-is-not-proven-here)
has the full list, and [Roadmap](docs/wiki/Roadmap.md) has what would close it.

## License

Nightgate is **source-available, not open source**:
[PolyForm Shield License 1.0.0](LICENSE.md), copyright NightWatchEng.

- **You may** use it and copy it into your own repos — enrollment *requires*
  copying (`cage enroll` renders the runner into your project, adopters copy
  the rules pack, `uvx --from git+…` clones it).
- **You may not** offer the software, or anything substantially derived from
  it, as a competing product or service, remove the copyright notice, or
  present the work as your own.
- **Contributions**: issues need no paperwork; pull requests are accepted under
  the [contribution terms](CONTRIBUTING.md).

The license text is authoritative; this summary is not.
