# Installation

Two things install: the **CLI** (`warden`, plus `cage` for unattended runs) and
the **skill pack** (the judgment protocols an agent session follows). They are
independent — the gate works with no agent, and the skills degrade to reporting
if the CLI is missing.

## Access comes first

The repository is private and enrollment is invite-only, so you must be a
collaborator on `NightWatchEng/agentops`. What each install needs on top:

- **The CLI from a tag** (section 1) fetches over https with your git
  credentials. `gh auth setup-git` makes `gh` git's credential helper for
  github.com, which is what `uv` uses for a `git+https` source.
- **CI in an enrolled repo** installs over ssh with a read-only deploy key
  per repository (*CI access to the platform*).
- **The skill pack** (section 3) is cloned from the same https URL with the
  same credentials.

For the CLI:

```sh
gh auth login
gh auth setup-git
git ls-remote --tags https://github.com/NightWatchEng/agentops v3.0.0
```

The `ls-remote` line prints one line when `v3.0.0` is published, whether the
tag is lightweight or annotated: an object id, a tab, and `refs/tags/v3.0.0`.
For an annotated tag the id is the tag object's, not the commit's. No output,
with exit status 0, means the tag is not published yet. Without access, git
exits 128 instead. Over https it asks `Username for 'https://github.com':`
when it has no credentials. With a token that is invalid or expired, it
prints `Invalid username or token` and `Authentication failed`; run
`gh auth login` and `gh auth setup-git` again. With valid credentials for an
account that has no access, it prints `Repository not found`.

To check an ssh key, such as a deploy key, run the same line against
`git@github.com:NightWatchEng/agentops.git`. With no key GitHub accepts, it
fails with `Permission denied (publickey)`; with a key whose account has no
access, it prints `ERROR: Repository not found.`

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| **uv** | runs warden, and usually your verify commands | `curl -LsSf https://astral.sh/uv/0.12.1/install.sh \| sh` — a *versioned* installer URL; the unversioned one runs whatever shipped last |
| **gh** | git credentials for the private repository (`gh auth setup-git`), the PR back-pressure check and branch protection (`gh auth status`) | [cli.github.com](https://cli.github.com) |
| **git** | the gate diffs `base...head`, so CI checkouts need full history (`fetch-depth: 0`) | — |
| **Claude Code** | only if you want the skill pack | [claude.com/claude-code](https://claude.com/claude-code) |

## GitHub only

The platform and its CI half assume GitHub. The repository is hosted there,
`gh` is how you get credentials for it, and `warden init` writes a GitHub
Actions workflow and nothing else. There is no GitLab CI or other renderer.
The checks in `warden certify` that look for CI read only
`.github/workflows/`, so a repository whose gate runs anywhere else
certifies LEVEL 0. `warden declare check` cannot see that gate either: D-03
reports not-applicable, and, where `graph.yaml` declares
`review.delegation`, D-05 cannot read the forge's auto-merge setting. The
CLI itself runs in any CI. The commands a non-GitHub consumer copies, and
what each check assumes, are in [Adopting](Adopting.md), *Another CI: the
GitHub-only limit*.

## 1. The CLI, from a release tag

```sh
uv tool install git+https://github.com/NightWatchEng/agentops@v3.0.0
warden --version
```

One package, both commands: `warden` and `cage` land on your PATH, and there is
no clone of the platform. `warden --version` prints the tag's version, here
`warden 3.0.0`. If the install cannot find `v3.0.0`, run the `ls-remote` check
under *Access comes first*: no output means the release tag is not published
yet.

Nothing stops a mismatched tag from being pushed. A pushed `v*` tag whose name
differs from `warden.__version__` turns the `release-tag` CI job red, and the
tag must then be deleted and re-cut.

That version is not only a banner. Every command compares `repo.yaml`'s
`platform.pin` against it and fails closed on a mismatch, so a repo pinned to
`v3.0.0` runs under this install and refuses any other.

`platform.pin` is the one version an enrolled repository declares. The
workflow `warden init` writes reads it from `repo.yaml` when it runs and
builds that tag, so bumping the pin is a one-line change. A bump can change
what `repo.yaml` must declare; make that change in the same PR.

**A bump does not rewrite a workflow you already have.** `init` writes no file
that already exists, so a fix to the generated workflow reaches an enrolled
repository only when that repository regenerates or hand-edits its own copy.
One such fix matters now: a gate `warden init` 2.2.0 wrote for an enrollment
BELOW the git root — `.github/workflows/warden-svc-api.yml`, whose jobs
default to running in `svc/api` — carries an install step that runs before
`actions/checkout`, when `svc/api` does not yet exist on the runner. A runner
refuses to start a `run:` step whose working directory is missing, so that
install job fails at its first step; the verify and gate jobs `need` it, and
the gate never runs at all. Raising `platform.pin` alone repairs nothing. The
repair is to copy into the workflow you have the two steps the generator now
writes with `working-directory: "."`: `require a private repository` and
`require repo.yaml`. Re-running `warden init` is not a route: it refuses when
any file it would write already exists, and an enrolled directory still holds
`repo.yaml` and its rules, so it writes nothing and leaves you as you were.

**Trust note.** `warden explain`, `warden review` and `warden rules lifecycle` load
`.warden/checkers/*.py` from the repo they run in, executing that code at the
same trust level as running the repo's tests. Don't point warden at a clone you
wouldn't run tests in.

## 2. Migrating from the pinned shim

Repositories enrolled before `warden init` committed a launcher at
`.warden/bin/warden` that ran the platform at a commit SHA (`PIN_SHA`) over
ssh, bumped together with `platform.pin`. The workflow `warden init` writes
does not use it. To migrate, replace the CI job that calls the shim with that
workflow and drop `PIN_SHA`. The skill pack still calls `.warden/bin/warden`,
so a repository that runs the skills keeps that path as a launcher for the
warden from section 1:

```sh
#!/bin/sh
exec warden "$@"
```

## CI access to the platform

The workflow `warden init` writes installs the platform over ssh, so each
consumer repository needs its own read-only deploy key. Set it up once per
repository: the founder adds the public half to `NightWatchEng/agentops` as a
deploy key, with write access left off, and the consumer stores the private
half as the `AGENTOPS_DEPLOY_KEY` repository secret.

```sh
ssh-keygen -t ed25519 -N '' -C 'OWNER/REPO CI' -f agentops_deploy_key
gh repo deploy-key add agentops_deploy_key.pub --repo NightWatchEng/agentops --title 'OWNER/REPO CI'
gh secret set AGENTOPS_DEPLOY_KEY --repo OWNER/REPO < agentops_deploy_key
rm agentops_deploy_key agentops_deploy_key.pub
```

Only the workflow's `install` job reads the key. It checks out `repo.yaml`
alone, builds the platform wheel at `platform.pin` with no uv cache, and runs
nothing from your repository. `verify` runs your verify commands with no
secret. `gate` runs `warden review` and `warden certify` with no code from your
repository, on the wheel whose hash `install` recorded; verify results reach it
as pull request output that decides nothing. It reads `repo.yaml` and
`.warden/rules` from the pull request, which can change both. Pull request code
can read the platform at the pinned tag, as can any reader of the run's one-day
wheel artifact, so a consumer repository must be private; the install job refuses one that is
not private (`github.event.repository.private`) with exit 2. A fork's pull request gets no
secrets, so the gate exits 2 and says so; Dependabot reads Dependabot secrets, so add `--app dependabot` to the `gh secret set` line. Deleting the deploy key revokes that one repository.

None of this stops a writer who edits the workflow: a same-repository pull
request runs its own copy, with secrets, when it opens, and a pushed branch can
add an `on: push` workflow. CODEOWNERS and review rules gate only the merge.
Add a push ruleset restricting `.github/workflows/`, or keep the key in an
environment with required reviewers that the `install` job names.

## 3. The skill pack, once per machine

The skills ship as a Claude Code plugin from this repository's marketplace.
Install at user scope: Claude Code resolves *project* skills from the project
cwd only, so a worktree or a headless session cannot see them, while
user-scope plugin skills resolve everywhere.

```bash
claude plugin marketplace add 'https://github.com/NightWatchEng/agentops#v3.0.0'
claude plugin install nightgate-skills@nightgate
```

Then check it with `warden skills pin`, which v3.0.0 carries.

Claude Code clones the marketplace with your git credentials, so the https
URL uses the `gh auth setup-git` helper above; the `NightWatchEng/agentops`
shorthand would clone over ssh instead. The `#v3.0.0` is `platform.pin`:
neither command takes a version flag, but a ref on the URL clones the
marketplace at that tag and the pack installs from that clone. `warden skills
pin` refuses (exit 1) a machine whose marketplace ref or installed pack version
is not the pin's, and prints the `--pack` path for the preflight below when it
is. A repository still pinned at v2.2.0 installs a warden without the command.
One Claude config dir holds one tag (`CLAUDE_CONFIG_DIR`).

The names above are the ones a v3.0.0 or later tag serves. A tag below v3.0.0
serves the same pack under its earlier names: the marketplace is `agentops`
and the pack `agentops-skills`, so a repository pinned there installs
`agentops-skills@agentops`, removes the marketplace as `agentops`, and invokes
`/agentops-skills:<name>` ([release notes v3.0.0](../design/release-notes-v3.0.0.md)).

Invoke namespaced — every skill directly, not only through `ship`:

```
/nightgate-skills:ship
/nightgate-skills:pre-pr-review
```

The pack does not update itself, and `claude plugin marketplace update
nightgate` keeps a marketplace added with a ref on that ref. The pack moves
when the pin does: a marketplace name refuses a changed source, so a bump is

```bash
claude plugin marketplace remove nightgate
```

followed by the two lines above at the new tag. Then check that what the
harness serves is what the skills assume:

```sh
warden skills preflight --pack ~/.claude/plugins/…/nightgate-skills/skills
```

`--pack` names the **`skills/` directory**, not the plugin root — the root
holds `.claude-plugin/` and `skills/`, neither of which contains a `SKILL.md`,
and pointing at it exits 2 with "nothing was examined, which is not a pass"
rather than quietly reporting success.

It fails closed with the names it could not resolve, so a skill that references
a sibling the installed cache no longer serves is a failed check rather than a
run-time surprise.

## 4. The cage, only for unattended runs

`cage` ships in the same package as `warden`. Nothing to install separately —
see [The Cage](The-Cage.md) for enrollment.

## Verify the install

```sh
warden --version
warden explain          # needs a repo.yaml; see Quickstart
cage validate cage.toml # only if you enrolled one
```

---

Next: [Quickstart](Quickstart.md) — a repo from zero to a gated PR.
