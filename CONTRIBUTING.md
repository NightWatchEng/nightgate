# Contributing to Nightgate

Issues, bug reports, and design discussion are welcome with no paperwork —
open an issue and say what you saw.

Pull requests are welcome too, under the contribution terms below. They exist
so the licensing posture in [LICENSE.md](LICENSE.md) stays true as outside
contributions arrive: NightWatchEng holds all rights in the codebase, and a
merged contribution must not quietly change that.

> These terms have not yet been reviewed by counsel. They bind contributions
> accepted while this notice stands and may be replaced by a counsel-reviewed
> version; the decision record for this posture lives in the repo's evidence
> store (`warden decide`, PR #154).

## Contribution terms — copyright assignment

By submitting a pull request or otherwise contributing code, documentation,
configuration, or any other material to this repository, you agree that:

1. **Assignment.** You assign to NightWatchEng all right, title, and interest
   — including copyright — in and to your contribution, effective upon
   submission.
2. **Fallback license.** Where such assignment is not legally effective in
   your jurisdiction, you instead grant NightWatchEng a perpetual,
   irrevocable, worldwide, royalty-free, exclusive, sublicensable, and
   transferable license to use, reproduce, modify, distribute, and relicense
   your contribution for any purpose, and you waive, to the maximum extent
   permitted by law, any moral rights you hold in it.
3. **License back.** NightWatchEng grants you a perpetual, non-exclusive,
   worldwide, royalty-free license to use your own contribution for any
   purpose.
4. **Originality.** You represent that the contribution is your original work,
   that you have the right to submit it under these terms, and that it is not
   subject to third-party claims or licenses you have not disclosed.
5. **No obligation.** Submitting a contribution does not obligate NightWatchEng
   to merge, use, or retain it.

Every pull request must confirm these terms via the checkbox in the PR
template. A PR that does not confirm them will not be merged.

## Reading the tracker

Work here is tracked as beads (`bd`), and a fresh clone has none of it: the
Dolt database under `.beads/` is gitignored. The tracker's history is
published on the git remote as `refs/dolt/data`, and `.beads/config.yaml`
names that remote. With [bd](https://github.com/gastownhall/beads) installed
(`brew install beads`, or the release tarball `.github/workflows/ci.yml`
pins), the first command in a clone is:

```sh
bd bootstrap --yes
```

It clones the Dolt history into a local database (about 6 seconds), and
`bd ready`, `bd show <id>` and `bd prime` work from there; `bd dolt pull`
refreshes it later. `bd dolt pull` before that first command fails with "no
beads database found", and `bd init` is not the command either — in a clone
it commits and rewrites CLAUDE.md. Pushing is the founder's: say what you are
taking in the PR. CI runs the block above, as written, in a clone of every
pull request (`scripts/tracker-from-a-clone.sh`).

## What the suite needs on your machine

Beyond Python and [uv](https://docs.astral.sh/uv/), this repo's tests need
**Go** and **npm** installed:

```sh
brew install go node          # macOS; see go.dev/dl and nodejs.org otherwise
```

They are not build dependencies of the platform — Nightgate is Python. They are
there because `tests/test_init.py` enrolls a fixture repo *per language* and
then RUNS the verify scope `warden init` wrote for it, so the suite shells out
to a real `go` and a real `npm`. That is the only executable proof here that
the enrollment surface works for languages the platform is not written in,
which is the portability claim itself, so it is not something to stub.

Without them the suite still runs: the five affected cells arrive as **named
skips** telling you exactly which binary is missing and how to get it, and
everything else passes.

Where the toolchain was *promised*, the same absence is a **named failure**
instead — that is CI, and this repo's own gate, `warden verify --scope tests`.
The switch is the `NIGHTGATE_REQUIRE_TOOLCHAIN` environment variable, and it
falls back to `CI` when unset. So if your shell exports `CI` (direnv, some tool
wrappers, a nested harness) you will get those five as failures rather than
skips; `NIGHTGATE_REQUIRE_TOOLCHAIN=0 uv run pytest -q` opts back out. The old
name, `AGENTOPS_REQUIRE_TOOLCHAIN`, is still read when the new one is unset,
and the run prints a deprecation line saying so; it stops being read in v4.0.0.

This list is not prose — it is `[tool.nightgate.toolchain].require` in
`pyproject.toml` (the old `[tool.agentops.toolchain]` table is read in its
place until v4.0.0), and `tests/test_toolchain.py` holds it to what
`warden init` actually runs, to what CI actually installs, and to this page.

## Landing a change here

This is the one sequence; the other pages that mention landing point here.

1. `bd bootstrap --yes` once per clone (above), then pick or file the bead:
   `bd ready`, `bd show <id>`, `bd update <id> --claim`.
2. Branch off `main`, and `git config core.hooksPath .githooks` once per clone.
3. `.warden/bin/warden plan --task "<intent>" --path <file>` (one `--path` per
   file you will touch) prints the risk tier. A HIGH-tier change needs a
   decision record first: `warden decide record`, `warden memory ingest`, and
   commit the shard. Nothing later on this path checks that you did.
4. Run `/nightgate-skills:deliver <id>`, which does steps 5 to 8 and never
   merges. The steps it wraps, if you run them by hand:
5. Build it, with tests, and commit in the format [CLAUDE.md](CLAUDE.md)
   gives (`scripts/commit-lint.sh` checks it).
6. One review round: `/nightgate-skills:pre-pr-review`, ending in a clean
   `warden attest write` whose shard you commit.
7. `.warden/bin/warden verify --scope <scope>` at that final head, once for
   every scope the diff reaches: `tests` always, `example` too when it touches
   `examples/hello-svc/`. Committing the shard moved HEAD, so a verify run
   before it does not count. The pre-push hook accepts that `tests` PASS on
   the pushed commit instead of running the suite again.
8. `.warden/bin/warden ship --base origin/main --title "<headline>" --body-file <path>`
   pushes and opens the PR. The founder merges.

## The mechanics

The engineering bar for a PR is the repo's own gate — see
[CLAUDE.md](CLAUDE.md) for the Definition of Done (tests, adversarial review,
attestation evidence, commit format) and *Landing a change here* above for
the order the gate's steps run in. In short: `uv run pytest -q` green, commit messages in
the validated format, and the warden gate passing on your branch.

That suite command is the **single-process** one, and it is spelled that way on
purpose: it is the bar your own clone has to clear, and it is what a bare
`uv run pytest` does here, in an enrolled consumer, and in the shipped example.
The gate runs the same tests in parallel, and CLAUDE.md's `## Build & test`
block writes both commands with that difference marked. Same tests, same pass
count; if the two disagree, the parallel run has found an isolation bug.

## Contributing from outside

This repository is private and is where every change is reviewed and landed.
The public repository, [NightWatchEng/nightgate](https://github.com/NightWatchEng/nightgate),
is a filtered export of it, and that is where an outside pull request is
opened. The contribution terms above bind it there, through the same PR
template checkbox. This is the arrangement being set up, not one running yet:
NightWatchEng/nightgate is still private, and the export that publishes this
repository's `main` to it does not run yet, so until both are in place no
outside pull request can arrive.

**What CI runs on it.** The deterministic gate, and nothing that needs a
credential the PR is not given: the commit-message lint, every verify scope
(ruff and the suite), `warden review`'s mechanical rules (with no sticky
comment, since a fork PR's token is read-only; findings land in the job log),
and the review-tier and round-count checks. CI does not run the adversarial
review, and the `pre-PR review attestation` step in
`.github/workflows/ci.yml` does not ask a fork PR for one: the review is a
Claude Code session, and the review corpus it reads and writes
(`.warden/memory/`) is not part of the export. A commit with no tracked item
uses the `(no-bead: <reason>)` form of the subject.

**What a maintainer does.** When the change is wanted, a maintainer carries it
here rather than merging it there:

1. Check that the contributor ticked the contribution-terms box on the
   public PR. The PR opened here is the maintainer's, so its box is not the
   contributor's confirmation; without theirs the change is not applied.
2. On a branch off `main` here, apply the contributor's commits with
   `gh pr diff <N> --repo NightWatchEng/nightgate --patch | git am`. `git am`
   keeps the contributor as the author of those branch commits. A squash
   merge here builds the landed commit's message from the branch's commit
   messages, not from the PR body, so a trailer in the PR body never reaches
   `main`. If the maintainer rewrites an applied commit's message to this
   repo's format, they keep its Author field and put any
   `Co-authored-by: <name> <email>` trailer in that commit's message.
3. Land it by *Landing a change here* above: the bead, the review round and
   its committed attestation, verify at the final head, and the PR, whose
   body names the public PR it came from.
4. The merge is the founder's, as for every PR here.
5. The export publishes the merged commit to the public repository. The
   maintainer then closes the public PR with a comment naming that public
   commit; it is not merged there.
