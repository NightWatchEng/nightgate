# v3.0.0 release notes

The product is now called Nightgate. v3.0.0 is a breaking, major release
because two things a consumer installs change name: the plugin marketplace and
the skill pack. What it carries since v2.3.0: 16 commits on `main`, one PR
each, PR #381 to PR #396, listed below by the scope each commit names. The
procedure that cuts the release is [Releasing](../wiki/Releasing.md).

## For a consumer moving its pin from v2.3.0

Breaking:

- **The marketplace is `nightgate`, and the pack is `nightgate-skills`.**
  It used to be `agentops`, with a pack called `agentops-skills`.
  Every skill is now invoked as `nightgate-skills:<name>`, for example
  `nightgate-skills:deliver`. Remove the old marketplace with
  `claude plugin marketplace remove agentops`, then add and install at
  `#v3.0.0` as [Installation](../wiki/Installation.md) section 3 shows.
  `warden skills pin` looks for `nightgate-skills@nightgate`, so it reports a
  machine still on the old pack as `unpinned` until you reinstall.
- **Skill names you wrote down change with the pack.** Edit each
  `agentops-skills:<name>` in your own `graph.yaml` (`impl:`) and in your cage's
  `prompt.md` to `nightgate-skills:<name>`.
- **Known gap in the cage until agentops-hy6o.18 lands.** The shipped
  `cage/run.sh` checks before a run that the installed pack serves the skill
  `prompt.md` invokes. It still looks for the `agentops-skills:` prefix, so for
  a prompt that invokes `nightgate-skills:` it finds nothing and skips the
  check without logging it. The run still starts and loads the skill. Only
  the pre-flight refusal for a skill the pack does not serve is missing.
  `tests/test_self_cage.py::test_the_runners_skill_preflight_reads_the_skill_the_prompt_invokes`
  is a strict xfail that pins this gap.

Not breaking, because the old spellings still work until v4.0.0
(agentops-hy6o.12):

- The toolchain switch is now `NIGHTGATE_REQUIRE_TOOLCHAIN`, and the
  pyproject table is now `[tool.nightgate.toolchain]`. The old
  `AGENTOPS_REQUIRE_TOOLCHAIN` and `[tool.agentops.toolchain]` are still
  read, and each read prints a DEPRECATED line naming v4.0.0. They stop
  being read in v4.0.0 (agentops-hy6o.19).

Unchanged: the install URL (`github.com/NightWatchEng/agentops`), the
`AGENTOPS_DEPLOY_KEY` secret that the gate `warden init` writes reads, and the
`warden` and `cage` command names.

## For this repository

- `.cage/cage.toml` now names the project `nightgate`, and the runner derives
  three things from that name. Each takes effect at the next `cage enroll`:
  - the kill switch becomes `~/.cage-nightgate-off`, so an
    `~/.cage-agentops-off` left in place no longer stops a run;
  - the run-now flag becomes `~/.cage-nightgate-run-now`;
  - the default log becomes `~/Library/Logs/nightgate-cage.log`.

  The checkout and worktree paths have not changed.

## repo

- #396 the product is Nightgate: marketplace `nightgate`, pack
  `nightgate-skills` 0.23.0
- #388 the toolchain switch is `NIGHTGATE_REQUIRE_TOOLCHAIN` and the table
  `[tool.nightgate.toolchain]`; the old names are read until v4.0.0
- #389 `publish.yaml` and one script export the public tree and its message
- #392 each main push carries its commits to the public repository,
  fail-closed; a tag from v3.0.0 on is mirrored by the first main push after
  the tag exists ([Releasing](../wiki/Releasing.md) step 8)
- #394 the export commit message passes the commit-lint the public repository
  runs
- #383 names and comments say what the code does
- docs: #381, #386

## ci

- #382 the emitted gate reads the base ref from a quoted env var, with no fork
  exit 0 (a consumer re-copies the step at its next bump: Releasing step 9)
- #393 the exported public tree skips the evidence it cannot carry, naming why
- #384 `astral-sh/setup-uv` 10.0.1 to 10.2.0 (dependabot)

## memory

- #387 `warden memory stats` scores each reviewer seat graph.yaml declares
- #385 rule comments say what they check

## skills

- #391 deliver and ship take `--no-tracker`; the cage stays tracker-bound

## docs

- #390 the GitHub-only CI limit and the manual gate steps
- #395 Cost-and-Throughput states a PR's review rounds, tokens and CI minutes
