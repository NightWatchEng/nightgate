# Nightgate

**A merge gate for agent-written code that refuses what fails and records what passed.**

Nightgate is a CLI, a CI workflow and a skill pack. You declare in config
what a change must satisfy. The gate refuses a pull request that does not
satisfy it, and every review leaves a record committed with the code. There
is no server to run and no paid API to call.

**[Wiki](docs/wiki/Home.md)** · [Quickstart](docs/wiki/Quickstart.md) ·
[Installation](docs/wiki/Installation.md) · [Adopting](docs/wiki/Adopting.md) ·
[Cost and Throughput](docs/wiki/Cost-and-Throughput.md) ·
[Architecture](docs/wiki/Architecture.md) ·
[Why This Exists](docs/wiki/Why-This-Exists.md)

## See it work

[NightWatchEng/nightgate-demo](https://github.com/NightWatchEng/nightgate-demo)
is a small Node service. It was enrolled at v3.0.1 with the three
commands under [Try it](#try-it), then moved to v3.0.2. Its rule and its
skills policy are what `warden init` wrote. Every line quoted below is
copied from its CI on 2026-10-05, as recorded in
[the demo record](docs/design/demo-run-20261005-v302.md). A `[...]` line
marks where lines were left out.

**A refused PR.**
[PR #2](https://github.com/NightWatchEng/nightgate-demo/pull/2) raises a
length cap, which breaks an existing test. It also commits a string shaped
like an AWS access key id. It carries no review record. The verify job, in
[run 37263095706](https://github.com/NightWatchEng/nightgate-demo/actions/runs/37263095706):

```text
verify --scope node: FAIL
  [ok] (4.86s) npm install --no-package-lock
  [exit 1] (0.51s) npm test
    failing tests (1):
      - GET /slug with overlong text is a 400
[...]
```

The review comment the gate posted on the PR:

```text
[...]
**1 finding(s)** — 1 blocking (HIGH).
| severity | rule | location | finding |
|---|---|---|---|
| HIGH | `secrets-in-diff` | `src/config.js:8` | Added line introduces a AWS access key id. |
[...]
rules_version `958ad70ff7a5` · deterministic gate ($0, no API) · `d79e31ce0921...6123038955a1`
```

The gate job's attestation step:

```text
warden attest check: this PR carries no committed attestation
[...]
```

The gate job's last step:

```text
warden gate: the verify job ended failure; the gate fails until every verify scope passes.
```

The first three blocks are three refusals from one run. The first names
the broken test. The second names the rule, the file, the line and the
hash of the rules, from a gate that calls no API. The third says no
committed record shows a review. The last is the gate job's final step,
which fails because verify failed. PR #2 stays open and red.

**A passed PR.**
[PR #5](https://github.com/NightWatchEng/nightgate-demo/pull/5) adds an
optional `separator` to `GET /slug`. It was reviewed with the skill pack's
`pre-pr-review`. Round 1 confirmed two low-severity findings. A repair
commit fixed both, with regression tests, and round 2 attested clean. From
[run 37263530985](https://github.com/NightWatchEng/nightgate-demo/actions/runs/37263530985):

```text
verify --scope node: PASS
  [ok] (1.45s) npm install --no-package-lock
  [ok] (0.45s) npm test
```

```text
[...]
✅ No findings. All applicable rules passed.
[...]
```

```text
[...]
attest check: PASS — 2 of 3 commit(s) in origin/main..6271425f04f7 carry a committed attestation
  e92d8a40c3d6 -> .warden/memory/attest/20261005T042618Z-e92d8a40-f799821a.json
  a9b9c3b596cb -> .warden/memory/attest/20261005T042439Z-a9b9c3b5-24572d5a.json
[...]
```

The tests passed and no rule fired. The gate found both rounds' committed
records. The third commit only adds round 2's record. The PR was merged.

## The problem

The agent wrote the change. The agent wrote the tests. The agent says they
pass. A person skims the diff and merges it. The review that would catch a
real defect is the step that gets skipped, because the agent is fast and
the diff is long.

Afterwards the PR shows green CI and an approval. It does not show which
rules applied, who reviewed the change, what they found, or whether the
findings were fixed.

## What it does

- **The gate.** `warden review` runs your rules over the PR's diff, and
  `warden verify` runs the test and lint commands you declare in
  `repo.yaml`. Both run in CI on every PR. A finding at a blocking
  severity, or a failing command, fails the job. The rules are files under
  `.warden/rules/`, and their hash is printed with every review verdict.
- **The two judges.** The `pre-pr-review` skill runs before a PR is opened.
  A finder reads the diff and lists candidate defects. A judge, in a
  separate context, confirms or refutes each one. Confirmed findings are
  fixed, then reviewed again. Repair stops after two rounds.
- **The attestation.** The review ends in `warden attest write`, which
  records the commit, the reviewers and every finding with its outcome.
  That record is committed on the branch. In CI, `warden attest check`
  refuses a PR when no committed record names its commits. It cannot prove
  a review was careful. It proves a review ran on that commit and states
  what it found.
- **The memory.** `warden memory ingest` turns each review's findings into
  committed events. The next `warden plan` for that part of the code
  lists where it went wrong before. The finder is given the patterns that
  recurred, and the judge is given the findings that were refuted. The
  `retro` skill proposes new rules from that record, citing the cases
  behind each, and a person merges them.

`warden certify` places a repository on a five-level ladder and names what
the next level needs. [The Cage](docs/wiki/The-Cage.md) runs the same loop
unattended, under hard stops it cannot edit.

## Install

```sh
uv tool install git+https://github.com/NightWatchEng/nightgate@v3.0.2
warden --version
```

That installs `warden` and `cage`. `warden --version` prints
`warden 3.0.2`. If `uv` cannot find `v3.0.2`, run
`git ls-remote --tags https://github.com/NightWatchEng/nightgate v3.0.2`.
It prints one line when the tag is published and nothing when it is not.
[Installation](docs/wiki/Installation.md) covers the skill pack, the
deploy key CI installs with, and the cage.

## Try it

From the root of your own Python, Node or Go git repository:

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

## Use

**Enroll.** Run the three commands above and commit what `warden init`
wrote. Set up the deploy key the gate installs the platform with, as
[Installation](docs/wiki/Installation.md) describes. Make the gate's check
required on `main`.
[Adopting](docs/wiki/Adopting.md) explains each file and how to write it
by hand.

**What a PR goes through.** The workflow `warden init` writes has three
jobs. `install` builds the pinned platform. `verify` runs every command
your `repo.yaml` declares. `gate` runs `warden review`, `warden attest
check` and `warden certify`, whether or not verify passed. It then fails if
verify failed. So one run shows every reason a PR is refused.

Before you push, review the change with the skill pack. In Claude Code:

```
/nightgate-skills:deliver --no-tracker add a --json flag to the status command
```

`deliver` plans, builds, tests, runs `pre-pr-review`, commits the
attestation and opens the PR. It never merges. `--no-tracker` runs it
without the beads tracker. `ship` splits a larger requirement into items
and runs `deliver` on each. [Skill Pack](docs/wiki/Skill-Pack.md) has the rest.

**What red looks like.** PR #2 above. The failing test is in the verify
job, a rule finding is in the PR comment, and a missing attestation is in
the gate job's attestation step.

**What green looks like.** PR #5 above. Verify passes, the comment says no
findings, and `attest check` lists each round's record. A person merges.

## Design rules

- **Policy, enforcement, evidence, provenance.** Every rule is declared in
  config. Something that can reject a change enforces it. Every verdict
  leaves an artifact bound to a commit.
- **Hard stops live outside the model.** Budgets, gates and kill switches
  are code the agent cannot rewrite during a run.
- **A person holds merge authority.** An agent may press the button only
  under an explicit, current, revocable grant from that person, on the
  terms `graph.yaml`'s `review.delegation` declares. No agent grants itself
  one.
- **No standing infrastructure.** Everything runs in CI, on a schedule, or
  on a laptop.
- **No paid ingestion APIs.** The platform reads git, GitHub and local
  files.
- **Config, never a fork.** A repository enrolls by writing `repo.yaml`,
  rules and a skills policy. Nobody copies and edits the platform.
- **Portability is a CI check.** This repository's CI enrolls
  `examples/hello-svc` on every PR and certifies it as a standalone
  repository. `scripts/portability-sim.sh` plants a secret in it and checks
  that the gate refuses it.

## Status

v3.0.2 is the current release. This repository gates its own PRs.

- **Review rounds.** A merged PR here ran a median of 2 review rounds,
  over the 78 PRs merged from 2026-09-26 to 2026-10-04.
- **CI minutes.** The same PRs used a median of 56 GitHub Actions minutes
  each, counting the PR's runs and the runs after its merge.
- **Unattended runs.** The 13 cage runs from 2026-09-21 to 09-27 that
  opened a PR took a median of 24 minutes each.
- **Certification.** `examples/hello-svc` certifies LEVEL 3 (Reviewed) in
  CI on every PR. shortfall, a separate Go repository, certified LEVEL 3
  (Reviewed) in its own CI on 2026-09-01, on an earlier pin.
  nightgate-demo certified LEVEL 3 (Reviewed) on 2026-10-05. It needs
  E-03 and E-04 for Level 4.
- **The standing refusal.** nightgate-demo's PR #2 stays open and red.

[Cost and Throughput](docs/wiki/Cost-and-Throughput.md) has the sources, and
[Why This Exists](docs/wiki/Why-This-Exists.md#what-is-not-proven-here) what is unproven.

## License

Nightgate is **source-available, not open source**:
[PolyForm Shield License 1.0.0](LICENSE.md), copyright NightWatchEng.

- **You may** use it and copy it into your own repositories. Enrolling
  copies parts of it: `cage enroll` renders the runner into your project,
  and adopters copy the rules pack.
- **You may not** offer the software, or anything substantially derived
  from it, as a competing product or service. You may not remove the
  copyright notice or present the work as your own.
- **Contributions.** Issues need no paperwork. Pull requests are accepted
  under the [contribution terms](CONTRIBUTING.md), and
  [Landing a change here](CONTRIBUTING.md#landing-a-change-here) is how a
  change reaches `main`.

The license text is authoritative; this summary is not.
