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
**Go** and **npm** installed, and for the Java cells a JDK (`java`), Maven
(`mvn`) and Gradle (`gradle`):

```sh
brew install go node          # macOS; see go.dev/dl and nodejs.org otherwise
brew install openjdk@17 maven gradle   # adoptium.net, maven.apache.org, gradle.org otherwise
```

They are not build dependencies of the platform — Nightgate is Python. They are
there because `tests/test_init.py` enrolls a fixture repo *per language* and
then RUNS the verify scope `warden init` wrote for it, so the suite shells out
to a real `go`, `npm` and `mvn`. That is the only executable proof here that
the enrollment surface works for languages the platform is not written in,
which is the portability claim itself, so it is not something to stub.

Without them the suite still runs: the affected cells arrive as **named
skips** telling you exactly which binary is missing and how to get it, and
everything else passes.

Where the toolchain was *promised*, the same absence is a **named failure**
instead — that is CI, and this repo's own gate, `warden verify --scope tests`.
The switch is the `NIGHTGATE_REQUIRE_TOOLCHAIN` environment variable, and it
falls back to `CI` when unset. So if your shell exports `CI` (direnv, some tool
wrappers, a nested harness) you will get those cells as failures rather than
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

## The scripts

Everything under `scripts/` is plumbing this repository uses to gate itself;
none of it is part of what an adopter installs. Each row says what the script
proves or does, which CI job or hook runs it, and whether you ever run it
yourself. A job is named by its workflow file and its job id.
`tests/test_docs.py` fails when an entry lands under `scripts/` without a row
here.

| Script | What it proves or does | Who runs it | By hand |
| --- | --- | --- | --- |
| `ast-identity.py` | Proves a change touches only comments and docstrings: every changed `.py` file has the same AST before and after, docstrings removed. | No job or hook. An author or reviewer runs it on a diff that claims to change only comments. | Yes, for such a diff |
| `build-wiki.sh` | Renders `docs/wiki/*.md` into `build/wiki/` for the GitHub wiki tab, rewriting the links that would not resolve there. | `wiki-sync.yml` `sync`, on a push to `main` that touches the wiki or its scripts. | No |
| `ci-event-scope.sh` | Decides what a CI event invalidates: the whole run, or only the job that reads the PR title, when the title is all that changed. | `ci.yml` `triage`, whose answer most of the other jobs wait on. | No |
| `commit-lint.sh` | Checks a commit message against the format in [CLAUDE.md](CLAUDE.md), or a PR title alone with `--header-only`. | `.githooks/commit-msg` (advisory), `ci.yml` `commits` on every pushed commit and the PR title, and `warden ship` on the title before the PR exists. | No, the hook runs it |
| `example-standalone.sh` | Copies `examples/hello-svc` out as its own committed git repository, the state the Quickstart starts from. | `ci.yml` `enrollment`, and `portability-sim.sh`. | No |
| `init-proof-isolation.sh` | Fails unless `warden` on PATH is the uv tool install, then turns off every git transport so the init proof cannot fetch the platform. | `ci.yml` `init-proof`, in the step before the proof runs. | No, it changes the global git config |
| `init-proof.sh` | Runs the README's Try it commands, `warden init` and `warden certify --level 3`, on fresh Python, Node, Go and Java repositories, and checks each reaches Level 3 with no file written by hand. | `ci.yml` `init-proof`, and `tests/test_init_proof.py`. | No, the suite runs it |
| `memory-watch-issue.py` | Reports one `warden memory watch` run on a standing GitHub issue, and fails the job when the watch could not be read. | `memory-watch.yml` `watch`, every Monday and on dispatch. | No |
| `mermaid/` | `package.json` and `package-lock.json`, which pin the whole mermaid-cli dependency tree the diagram check installs. | `validate-diagrams.sh`, with `npm ci --ignore-scripts`. | Only to move the pin |
| `portability-sim.sh` | Proves the gate gates on the example project: a clean change passes, a planted secret and a debugger hook are refused, and the tag-vocabulary check keeps its exit codes. | `ci.yml` `enrollment`, on every PR. | Yes, when a change touches `examples/`, `docs/wiki/`, rules or schemas |
| `private-evidence.py` | Says whether a commit carries the review corpus and the tracker that the public export leaves out, so CI skips only the steps that need them. | `ci.yml` `gate`, `corpus`, `enrollment` and `tracker`. | No |
| `publish-public.py` | Writes the export of one commit, filtered by `publish.yaml`, and the commit message the public repository gets. | `publish-sync.py`, which loads it. | No |
| `publish-sync.py` | Carries every `main` commit the public repository lacks, one commit each, checks each against `publish.yaml`'s selection, and mirrors `v*` tags. | `publish-public.yml` `export`, on every push to `main`. | No |
| `publish-wiki.sh` | Reconciles a wiki clone with `build/wiki/`, deleting only the pages it published before. | `wiki-sync.yml` `sync`. | No |
| `readme-try-it.sh` | Prints the README's Try it block byte for byte, so CI runs what the README shows. | `ci.yml` `init-proof`, and `init-proof.sh`. | No |
| `release-tag-check.py` | Refuses a release tag whose name is not `v` + `warden.__version__`. | `ci.yml` `release-tag`, on a pushed `v*` tag. | No |
| `tracker-from-a-clone.sh` | Runs the block under *Reading the tracker* in a fresh clone, and checks that `bd show`, `bd ready` and `bd dolt pull` work without changing the clone. | `ci.yml` `tracker`, on every PR whose tree carries `.beads/`. | No |
| `validate-diagrams.sh` | Renders every Mermaid block under a directory with the real Mermaid engine, so a diagram that will not render cannot reach the wiki. | `ci.yml` `diagrams`, on `docs/wiki` and on a known-bad fixture, and `wiki-sync.yml` `sync` before it publishes. | Yes, after editing a diagram (it needs npm) |
| `workflow-graph-check.py` | Follows every output and artifact one job of a workflow hands another, and reports a value read from a job that does not provide it. | `ci.yml` `init-proof`, on the gate workflow `warden init` writes into each fresh repository. | No |

## Contributing from outside

This repository is private and is where every change is reviewed and landed.
The public repository, [NightWatchEng/nightgate](https://github.com/NightWatchEng/nightgate),
is a filtered export of it, and that is where an outside pull request is
opened. The contribution terms above bind it there, through the same PR
template checkbox. The export runs on every push to `main`: the
`publish-sync.py` row of *The scripts* above.

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
