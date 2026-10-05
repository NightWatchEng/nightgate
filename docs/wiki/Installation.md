# Installation

Two things install: the **CLI** (`warden`, plus `cage` for unattended runs) and
the **skill pack** (the judgment protocols an agent session follows). They are
independent — the gate works with no agent, and the skills degrade to reporting
if the CLI is missing.

## Access comes first

Every install below reads `NightWatchEng/nightgate`, so your account needs
read access to it. What each install needs on top:

- **The CLI from a tag** (section 1) fetches over https with your git
  credentials. `gh auth setup-git` makes `gh` git's credential helper for
  github.com, which is what `uv` uses for a `git+https` source.
- **CI in an enrolled repo** clones the platform over https with no secret,
  and over ssh with a read-only deploy key only for a pin of a private build
  (*CI access to the platform*).
- **The skill pack** (section 3) is cloned from the same https URL with the
  same credentials.

For the CLI:

```sh
gh auth login
gh auth setup-git
git ls-remote --tags https://github.com/NightWatchEng/nightgate v3.0.2
```

The `ls-remote` line prints one line when `v3.0.2` is published, whether the
tag is lightweight or annotated: an object id, a tab, and `refs/tags/v3.0.2`.
For an annotated tag the id is the tag object's, not the commit's. No output,
with exit status 0, means the tag is not published yet. Without access, git
exits 128 instead. Over https it asks `Username for 'https://github.com':`
when it has no credentials. With a token that is invalid or expired, it
prints `Invalid username or token` and `Authentication failed`; run
`gh auth login` and `gh auth setup-git` again. With valid credentials for an
account that has no access, it prints `Repository not found`.

To check an ssh key, such as a deploy key, run the same line against
`git@github.com:NightWatchEng/nightgate.git`. With no key GitHub accepts, it
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
uv tool install git+https://github.com/NightWatchEng/nightgate@v3.0.2
warden --version
```

One package, both commands: `warden` and `cage` land on your PATH, and there is
no clone of the platform. `warden --version` prints the tag's version, here
`warden 3.0.2`. If the install cannot find `v3.0.2`, run the `ls-remote` check
under *Access comes first*: no output means the release tag is not published
yet.

Nothing stops a mismatched tag from being pushed. A pushed `v*` tag whose name
differs from `warden.__version__` turns the `release-tag` CI job red, and the
tag must then be deleted and re-cut.

That version is not only a banner. Every command compares `repo.yaml`'s
`platform.pin` against it and fails closed on a mismatch, so a repo pinned to
`v3.0.2` runs under this install and refuses any other.

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
repair is to replace the whole workflow file with the one the generator now
writes, whose `require repo.yaml` step carries `working-directory: "."`:
*Upgrading an enrolled repository* below says
how. Re-running `warden init` in place is not a route: it refuses when any
file it would write already exists, and an enrolled directory still holds
`repo.yaml` and its rules, so it writes nothing and leaves you as you were.

A gate `warden init` 3.0.1 or earlier wrote has no `warden attest check` step,
so certify's E-01 fails in its own CI, and it stops before `warden review`
when verify failed, so a pull request with failing tests shows no rule
finding. The repair is the same: replace the whole workflow file with the
one `warden init` 3.0.2 writes for the same directory, as *Upgrading an
enrolled repository* below shows. That changes the `gate` job, the `warden
verify --scope` steps in the `verify` job, which now copy the summary naming
the failing tests into the step summary, and the header comment that
describes the gate. The new `warden attest check` step is fail-closed: a pull
request from your repository that carries no committed pre-PR review
attestation is red, a Dependabot one included, and a fork's is exempt.

A gate `warden init` 3.0.2 or earlier wrote exits 2 at `install` in a public repository or with no `NIGHTGATE_DEPLOY_KEY`,
and raising `platform.pin` repairs neither: replace the whole workflow file with the one 3.0.3 writes.

**Trust note.** `warden explain`, `warden review` and `warden rules lifecycle` load
`.warden/checkers/*.py` from the repo they run in, executing that code at the
same trust level as running the repo's tests. Don't point warden at a clone you
wouldn't run tests in.

## 2. Migrating from the pinned shim

Repositories enrolled before `warden init` committed a launcher at
`.warden/bin/warden` that ran the platform at a commit SHA (`PIN_SHA`) over
ssh, bumped together with `platform.pin`. The workflow `warden init` writes
does not use it. To migrate, replace the CI job that calls the shim with that
workflow and drop `PIN_SHA`. Every skill in the pack resolves warden in its
first step: it runs `.warden/bin/warden` when the repository has one, else
`warden` on PATH when its `--version` is `platform.pin`, and refuses
otherwise. So outside the cage the shim can go once section 1's warden is on
PATH. A repository that runs the cage (section 4) keeps the path: the caged
session's permission profile admits warden only as `.warden/bin/warden`, so
with no launcher every warden call in an unattended run is denied. Keep it as
a launcher for the warden from section 1:

```sh
#!/bin/sh
exec warden "$@"
```

## CI access to the platform

The workflow `warden init` writes needs no secret: its `install` job clones
`https://github.com/NightWatchEng/nightgate` at `platform.pin` over https with no credential,
and exits 2 naming the pin only if that clone fails. A deploy key is optional, only if you pin
a private build: a read-only key per repository, public half added to `NightWatchEng/nightgate`
(write access off), private half stored as the `NIGHTGATE_DEPLOY_KEY` secret; `install` then
clones over ssh. Until v4.0.0 an `AGENTOPS_DEPLOY_KEY` alone installs from `NightWatchEng/agentops` and warns.

```sh
ssh-keygen -t ed25519 -N '' -C 'OWNER/REPO CI' -f nightgate_deploy_key
gh repo deploy-key add nightgate_deploy_key.pub --repo NightWatchEng/nightgate --title 'OWNER/REPO CI'
gh secret set NIGHTGATE_DEPLOY_KEY --repo OWNER/REPO < nightgate_deploy_key
rm nightgate_deploy_key nightgate_deploy_key.pub
```

Only the workflow's `install` job reads the key. It checks out `repo.yaml` alone,
builds the platform wheel at `platform.pin` with no uv cache, and runs nothing from
your repository. `verify` runs your verify commands with no secret. `gate` runs `warden
review`, `warden attest check` and `warden certify`, each whether or not verify passed,
with no code from your repository, on the wheel whose hash `install` recorded; verify
results reach it as pull request output that decides nothing. It reads `repo.yaml` and
`.warden/rules` from the pull request, which can change both. A private pin's build is
readable by pull request code and any reader of the run's one-day wheel artifact. A fork's
pull request gets no secrets and installs with no key, as does a Dependabot one unless the
key is also set with `--app dependabot`. Deleting the deploy key revokes that one repository.

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
claude plugin marketplace add 'https://github.com/NightWatchEng/nightgate#v3.0.2'
claude plugin install nightgate-skills@nightgate
```

Then check it with `warden skills pin`, which v3.0.2 carries.

Claude Code clones the marketplace with your git credentials, so the https
URL uses the `gh auth setup-git` helper above; the `NightWatchEng/nightgate`
shorthand would clone over ssh instead. The `#v3.0.2` is `platform.pin`:
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
Its tag exists only on `NightWatchEng/agentops`, since `NightWatchEng/nightgate`
carries tags from v3.0.0 on, so every command on this page that names
`NightWatchEng/nightgate` names `NightWatchEng/agentops` for that pin.

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

followed by the two lines above at the new tag. Leave out the `remove` when
the marketplace was never added: it fails then. *Upgrading an enrolled
repository* below puts this in the order a bump takes. Then check that what the
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

## Upgrading an enrolled repository

This is the order a `platform.pin` bump takes on a repository `warden init`
enrolled, written for the move to v3.0.2. Read the release's notes first
([Releasing](Releasing.md) lists them): they say what changes in `repo.yaml`
and in the workflow `warden init` writes.

**1. Install the new CLI and the new skill pack, before the bump.**

```sh
uv tool install --force git+https://github.com/NightWatchEng/nightgate@v3.0.2
warden --version
claude plugin marketplace remove nightgate
claude plugin marketplace add 'https://github.com/NightWatchEng/nightgate#v3.0.2'
claude plugin install nightgate-skills@nightgate
```

Leave out the `marketplace remove` line when the marketplace was never
added: it fails then. The pack comes first because the bump PR needs a pre-PR
review (step 4), and the pack and the CLI are one release: before v3.0.2,
`pre-pr-review` ran only the `.warden/bin/warden` launcher, which `warden init`
does not write. A plugin installed during a Claude Code session does not load
in it, so start a new session. From here this machine's `warden` refuses a
repository still pinned at the old tag.

**2. On a branch, move the pin.** Set `repo.yaml`'s `platform.pin` to
`v3.0.2`, with any key the release notes say the schema now requires. Then
`warden skills pin` reads the new pin and exits 0.

**3. Replace the whole workflow file with the one the new release writes.**
A bump does not rewrite a workflow, and `warden init` writes no file that
already exists; no command rewrites an enrolled repository's workflow in
place. So enroll a scratch repository at the same directory below the git
root, with the same manifests, and copy the file it writes. From the git
root, with `DIR` set to the enrolled directory (`.` at the root):

```sh
DIR=.
scratch=$(mktemp -d)
git init -q "$scratch"
mkdir -p "$scratch/$DIR"
for m in pyproject.toml package.json go.mod; do
  if [ -f "$DIR/$m" ]; then cp "$DIR/$m" "$scratch/$DIR/"; fi
done
(cd "$scratch/$DIR" && warden init)
f=$(ls "$scratch/.github/workflows")
diff -u ".github/workflows/$f" "$scratch/.github/workflows/$f"
cp "$scratch/.github/workflows/$f" .github/workflows/
```

The workflow depends only on the directory, the manifests found there and the
release, so the copied file is byte for byte what a fresh enrollment at
v3.0.2 writes. Copy the whole file, not a job: a release can change the
header comment and steps outside the `gate` job (v3.0.2 changed the `warden
verify --scope` steps in the `verify` job and added an `id: warden` to its
install step). The `diff` shows what the copy discards: re-apply any edit you
made to your own copy, such as a `warden memory ingest` step on push. A
manifest added since you enrolled adds a verify step for a scope your
`repo.yaml` does not declare; leave it out of the scratch repository. A
hand-written gate is not this file: [Releasing](Releasing.md) step 9 says
which of its steps can go in the bump PR.

**4. Expect the bump PR to need its own attestation.** The workflow runs on
`pull_request` from the PR's copy and reads `repo.yaml` from the PR, so the
bump PR is judged by the new gate at the new pin. From v3.0.2 that gate runs
`warden attest check`, so the bump PR stays red until it carries a committed
pre-PR review attestation: run the verify scopes, then
`/nightgate-skills:pre-pr-review`, then `warden memory ingest`, and commit the
shard it writes under `.warden/memory/attest/`.

**5. After the bump merges, bring each open PR onto the new gate.**
Re-running an open PR's old run reuses its old merge ref and the old
workflow. Merge `main` into the PR's branch and push. A PR that conflicts with
`main` gets no `pull_request` run at all, so an empty commit does not start
one: resolve the conflict in that merge. No attestation covers the merge
commit, which carries `main`'s changes, so the new gate's `warden attest
check` refuses it: re-run the pre-PR review on the merged head and commit its
shard.

## Verify the install

```sh
warden --version
warden explain          # needs a repo.yaml; see Quickstart
cage validate cage.toml # only if you enrolled one
```

---

Next: [Quickstart](Quickstart.md) — a repo from zero to a gated PR.
