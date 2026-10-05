# Adopting

This is the **consumer enrollment guide**: how another repository enrolls on
the platform. To change Nightgate itself, see CONTRIBUTING.md, *Landing a change
here*, in the repository root.

**Start with `warden init`.** Run in a git repository, at its root or in the
directory to enroll below it ([Quickstart](Quickstart.md), *Below the git
root*), it writes step 1 (`repo.yaml`), step 2 (starter rules), the
`.gitignore` half of step 3, and step 5 (`.warden/skills-policy.md`). It writes
no shim, the other half of step 3, and sets no branch protection. Its CI
workflow, which installs the tag `platform.pin` names, is the workflow step 4 writes by hand, plus an install job and a certify step: it
installs the pinned platform, with no secret unless the pin is a private build,
in a job that runs no repository code, runs verify in a job with no secret, and runs `warden review`,
`warden attest check` and `warden certify` in a third, each whether or not
verify passed ([Installation](Installation.md), *CI access to the platform*). It is a
GitHub Actions workflow and there is no other: on any other CI, read
*Another CI: the GitHub-only limit* below before you enroll. A fresh
repository reaches Level 3 once you commit the files
([Quickstart](Quickstart.md)); platform CI proves that on every PR by running
the README's Try it block, read from the README, on fresh Python, Node, Go
and Java repositories. What follows is reference: what each file means, and how to
write it by hand.

```sh
warden init
```

**Moving `platform.pin` later** is not a re-run of `warden init`, which
writes no file that already exists. The order a bump takes, including how the
workflow it wrote catches up, is in
[Installation](Installation.md#upgrading-an-enrolled-repository),
*Upgrading an enrolled repository*.

The enrollment of `examples/hello-svc` written down. Platform CI runs this
path against that example on every PR, so it cannot rot silently. Follow it top
to bottom; every snippet is copy-pasteable.

Four steps get you a gate. Two more get you agents and unattended runs.

| # | Step | You get |
|---|---|---|
| 1 | `repo.yaml` | the policy: components, risk tiers, verify commands |
| 2 | `.warden/rules/` | rules that can auto-reject a diff |
| 3 | the shim + `.gitignore` | one local entrypoint, and generated files kept out of git |
| 4 | a CI job + branch protection | a gate the merge button enforces |
| 5 | `.warden/skills-policy.md` | the agent side — six required sections, plus the optional `## Build disciplines` craft layer ([Skills Policy](Skills-Policy.md)) |
| 6 | `cage.toml` | unattended runs. Note one thing no tool can do for you: dropping `[triggers.schedule]` deletes the plist but cannot unload a job launchd already bootstrapped — run `launchctl bootout gui/$UID/com.<name>.cage` yourself ([The Cage](The-Cage.md)) |

Prerequisites and the shim itself: [Installation](Installation.md).

## 1 · Write `repo.yaml`

The single source of truth for repo facts. warden schema-validates it on every
load and fails closed — a typo is a hard error, not a silent default.

This **is** `examples/hello-svc/repo.yaml`, verbatim; a test asserts the two
stay identical, so what you copy is what CI actually enrolls:

```yaml
# hello-svc enrollment: the smallest honest repo.yaml. This file is the single
# source of truth for repo facts — components, risk tiers, verify commands,
# review settings. warden schema-validates it on every load and fails closed.
version: 1
repo: hello-svc

# The platform release this repo's gate is built on. Verdicts are only
# comparable to other verdicts judged under the same release. Real consumers
# pin an immutable SHA in their shim and the semantic version here; the two
# must agree or warden fails closed. Bumped together with the platform's own
# version at release time (see the tagged GitHub release's notes).
platform:
  pin: v3.0.4

components:
  app:   {path: hello_svc/, lang: python, description: "stdlib HTTP JSON service (/health, /greet)"}
  tests: {path: tests/,     lang: python, description: "pytest-compatible plain-assert tests"}

# First glob match wins, top to bottom; anything unmatched defaults to LOW.
risk_tiers:
  - {glob: "hello_svc/app.py", tier: HIGH, reason: "the entire service surface: routing + input validation of untrusted query data"}
  - {glob: "**",               tier: LOW}

# Deterministic gates. Commands live here, not in CI YAML or code.
# cwd defaults to the repo root (the directory holding this file).
# `runner: pytest` puts pytest's counts on `warden verify`'s summary line. It
# needs v2.3.0 or later; v2.2.0's schema refuses the key, so on that pin
# delete it and the step runs the same, printing no counts.
verify:
  app:
    - {run: "python3 -m hello_svc.app --selfcheck"}
    - {run: "uv run --with pytest==9.1.1 python -m pytest tests -q", runner: pytest}

# Repair rounds a delivery may run before it reverts: 1 to the platform cap
# of 2. Required for certification Level 3.
repair:
  budget: 2

review:
  rules_dir: .warden/rules
  blocking_severities: [HIGH]
```

Five conventions worth following:

- **Give every HIGH tier a `reason`.** The tier table doubles as agent
  orientation — `warden explain` renders it, reasons included.
- **Tier your own policy HIGH once you enroll.** `repo.yaml`, `.warden/**` and
  your rules dir *are* the policy.
- **Pin tools inside the verify command**, not in the runner's environment
  (`uv run --with pytest==9.1.1 …`). Otherwise local PASS and CI PASS can mean
  different things.
- **Declare the repair budget.** `repair.budget`, a whole number from 1 to
  the platform cap of 2, is optional to the schema but **required for Level
  3**: `warden certify` rung R-12 fails without it. A warden older than the
  key refuses a `repo.yaml` that declares it, so an enrolled repo adds it in
  the same PR that moves its pin.
- **Optional keys are genuinely optional.** `protected_paths`, `deploy` and
  `design` are byte-identical no-ops when absent. Every key the schema
  accepts is in [Configuration](Configuration.md).

## 2 · Add the rules

Rules are markdown files with YAML frontmatter under `review.rules_dir`.
Three engines, cheapest first: `declarative` (a regex in the rule file, runs in
CI at $0), `python` (a checker module), `claude` (judgment, evaluated pre-PR).

The full contract — frontmatter fields, the `checks:` DSL, per-line exception
markers, pausing, and the shipped guardrail catalog — is
[Writing Rules](Writing-Rules.md). To start, copy the rules from
`examples/hello-svc/.warden/rules/` except `handler-response-contract`, which
step 4's gate cannot run, and scope their `applies_to` to your source
directories.

Then find out what you are missing:

```sh
.warden/bin/warden rules recommend
```

It joins the shipped catalog, your review corpus, and the rules you already
have, and leads with **UNANSWERED: N** — entries that are applicable,
unenforced, and carry no recorded decision. Reading that number does not shrink
it. Adopting an entry does, and so does answering it in
`.warden/catalog-answers.yaml`.

The report splits its answer and never blurs the halves: a row backed by
`EVIDENCE_MIN_N` (3) or more judged findings in your own corpus is
**EVIDENCE-BASED**; a row with fewer is **PRIOR ART**, recommended from
published sources with its local count shown. Evidence outranks prior art, and
the report never lets the second borrow the first's authority.
[Writing Rules](Writing-Rules.md) has the rest.

## 3 · Add the shim, and ignore the generated files

The shim is in [Installation](Installation.md). Then:

```sh
printf '.warden/out/\n.warden/memory/findings.jsonl\n.warden/memory/gate/\n' >> .gitignore
```

Three paths, and the asymmetry between them is load-bearing:

| Path | Committed? | Why |
|---|---|---|
| `.warden/memory/attest/` | **yes** | the evidence. Distinct filenames, so concurrent branches never collide |
| `.warden/out/` | no | one dir per run; upload as a CI artifact instead — `warden certify`'s R-13 refuses a repo that tracks it |
| `.warden/memory/findings.jsonl` | no | a derived cache rebuilt whole by every ingest — committing it conflicts on every reviewing branch. **R-08** fails certification if tracked |
| `.warden/memory/gate/` | no | shards from deterministic checkers firing, *including* the mutation probe used to validate a new rule. Commit one and you have filed a fabricated finding into the shared corpus. **R-11** fails certification if tracked |

Every command that produces new evidence writes a run dir under `.warden/out/`:
`warden explain`, `warden plan`, `warden verify`, `warden deploy`,
`warden review`, `warden attest` write, `warden decide` record,
`warden certify`, `warden memory` ingest and check-vocabulary, `warden mine`,
`warden rules` recommend, `warden autonomy` pause, and `warden ship`, which
writes one either way because a refusal is evidence too.
These write no run dir: `attest show`,
`attest check`, `audit`, `graph`, `catalog`, `skills`, `diff`, `progress`,
`memory recall`/`stats` — they only read, or re-render what is already on
disk. (`diff` writes the file you name with `-o` and nothing else.)

## 4 · Add the CI job and make it required

This workflow is the hand-written form of the one `warden init` emits, for a
language `init` does not detect or a repository you enroll by hand. Save it
as `.github/workflows/warden.yml`. It has the emitted gate's split: **verify**
runs your verify commands, which a pull request controls, and **gate** runs
`warden review` and the attestation check in a job that executes no code from
the repository. One job that runs both is a gate the pull request can switch
off: a verify command could replace the `warden` on `PATH`, or rewrite the
rules or `.warden/out/`, before review reads them. Each job installs warden
itself, before its first warden step, from the public platform repository at
the tag `platform.pin` names, with no secret. It leaves out two things the
emitted workflow has: the install job, which exists to hold the deploy key a
pin of a private build needs, and the `warden certify --level 3` step, which
you add once `warden certify` reports Level 3.

```yaml
name: warden
on:
  pull_request:

permissions:
  contents: read

jobs:
  verify:
    name: warden verify
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7  # v10.2.0
        with:
          version: "0.12.1"
          enable-cache: false   # a cache this job wrote, the gate job would read
      # The same step in both jobs. --no-config keeps a uv.toml or
      # pyproject.toml the pull request adds from steering the install.
      - name: install warden at repo.yaml platform.pin
        id: warden
        shell: bash
        run: |
          pin="$(awk '/^platform:/ {inside = 1; next} inside && /^[^[:space:]#]/ {inside = 0} inside && $1 == "pin:" {print $2; exit}' repo.yaml | tr -d "\"'\r")" || pin=""
          if ! printf '%s\n' "$pin" | grep -Eqx 'v?[0-9]+\.[0-9]+\.[0-9]+'; then
            echo "warden gate: repo.yaml platform.pin is ${pin:-missing}, not a release tag like v1.2.3 written as a block (platform: then an indented pin:); the gate DID NOT RUN." >&2
            exit 2
          fi
          GIT_TERMINAL_PROMPT=0 uv tool install --no-config --no-cache \
            "git+https://github.com/NightWatchEng/nightgate@v${pin#v}"
          uv tool dir --bin >> "$GITHUB_PATH"
      # The toolchain your verify commands invoke, DECLARED here and installed
      # by nothing. This gate installs its OWN runtime — the
      # setup-uv step above, because warden is itself a uv tool — and no
      # toolchain of yours, because that would pin a version and a setup
      # action's sha on your behalf with no freshness mechanism behind it.
      # Add the setup action you want ABOVE this step. One pair per binary a
      # verify command runs that the gate does not install: the repo.yaml in
      # step 1 runs `python3` and `uv`, and `uv` is installed above, so
      # `python3` is what is left. Exit 2 is this gate's code for DID NOT RUN,
      # as against 1, ran and failed — so an absent toolchain never reads as a
      # test the pull request broke. The step's BODY is the one
      # `warden init` renders into every generated gate (warden/enroll.py's
      # `render_toolchain_step`), with two things read off this page instead:
      # the pair list, and the setup actions the message names — the generated
      # one names setup-go, setup-node, setup-java and setup-gradle because it
      # is rendered for a Go, Node or Java enrollment. `examples/hello-svc`
      # adapts those same two and nothing else.
      # This page's copy is PINNED: a test makes the two substitutions and
      # asserts what is left is byte-identical to the render, so editing
      # either end alone goes red rather than drifting quietly. The example's
      # copy is pinned the same way, against the same two KINDS of
      # adaptation — each reads its pair list off its own verify commands,
      # so the lists themselves differ.
      #
      # A gate `warden init` GENERATES for a python-only repo has no such step,
      # because the verify commands IT writes invoke only `uv` and the setup-uv
      # step above installs it. That is a fact about the commands init writes,
      # not about python. Step 1 above keeps hello-svc's own `repo.yaml`, whose
      # verify commands run `python3` as well — so this gate needs the step.
      # Read the pair list off YOUR verify commands, not off your language.
      - name: require the toolchain the verify scopes run
        shell: bash
        run: |
          missing=""
          for pair in "python3:app"; do
            tool="${pair%%:*}"
            scope="${pair#*:}"
            command -v "$tool" > /dev/null 2>&1 && continue
            echo "warden gate: $tool is not on PATH, so the $scope verify scope could not be evaluated and the gate DID NOT RUN." >&2
            missing="$missing $tool"
          done
          if [ -n "$missing" ]; then
            echo "This gate declares the toolchain its verify commands run and installs no toolchain of this repository's own. Add a step to this workflow, above this one, that installs${missing} — astral-sh/setup-uv and actions/setup-python are the usual ones, each pinned to a commit sha — or change the verify commands in repo.yaml to a toolchain this runner already carries." >&2
            echo "PATH=$PATH" >&2
            exit 2
          fi
      # One step per name under `verify:` in repo.yaml.
      - name: warden verify --scope app
        run: warden verify --scope app
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v7.0.1
        if: always()
        with:
          name: warden-verify
          path: .warden/out/*-verify/verify-result.json
          if-no-files-found: ignore
          retention-days: 1

  gate:
    name: warden gate
    needs: verify
    if: ${{ !cancelled() }}   # runs when verify failed, and fails at its end
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
      issues: write     # the sticky comment goes through the issues API; an
      #                   explicit permissions block zeroes unlisted scopes
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
        with:
          fetch-depth: 0   # the gate diffs base...head; a shallow clone has no merge-base
          persist-credentials: false
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7  # v10.2.0
        with:
          version: "0.12.1"
          enable-cache: false
      - name: install warden at repo.yaml platform.pin
        id: warden
        shell: bash
        run: |
          pin="$(awk '/^platform:/ {inside = 1; next} inside && /^[^[:space:]#]/ {inside = 0} inside && $1 == "pin:" {print $2; exit}' repo.yaml | tr -d "\"'\r")" || pin=""
          if ! printf '%s\n' "$pin" | grep -Eqx 'v?[0-9]+\.[0-9]+\.[0-9]+'; then
            echo "warden gate: repo.yaml platform.pin is ${pin:-missing}, not a release tag like v1.2.3 written as a block (platform: then an indented pin:); the gate DID NOT RUN." >&2
            exit 2
          fi
          GIT_TERMINAL_PROMPT=0 uv tool install --no-config --no-cache \
            "git+https://github.com/NightWatchEng/nightgate@v${pin#v}"
          uv tool dir --bin >> "$GITHUB_PATH"
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c  # v8.0.1
        with:
          name: warden-verify
          path: ${{ runner.temp }}/warden-verify
      # The verify results fill the review comment's verify lines and decide
      # nothing: the last step below is what fails the gate on a red verify.
      - name: take the verify results
        shell: bash
        run: warden take --from "$RUNNER_TEMP/warden-verify"
      # Each step that can refuse runs once warden is installed, whatever
      # verify or another of them said, so a pull request shows every refusal.
      # --no-project-checkers: review imports nothing under .warden/checkers/.
      - name: warden review
        if: ${{ !cancelled() && steps.warden.outcome == 'success' }}
        shell: bash
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: warden review --no-project-checkers --event "$GITHUB_EVENT_PATH"
      # Binds a light round declared in graph.yaml: the verdict is recomputed
      # over the pushed range. It exits 0 with none declared; certify's R-14
      # (Level 3) requires it once one is. v2.2.0 has no `attest classify`:
      # on that pin leave it out, as `warden init` does.
      - name: proportionate review tier
        if: ${{ !cancelled() && steps.warden.outcome == 'success' }}
        shell: bash
        env:
          BASE_REF: ${{ github.base_ref }}
        run: warden attest classify --enforce --base "origin/$BASE_REF"
      # Assert the PR left a pre-PR review attestation in the COMMITTED corpus,
      # not just the gitignored run dir. Fork PRs are exempt: the orchestrated
      # review needs credentials they lack.
      - name: pre-PR review attestation
        if: ${{ !cancelled() && steps.warden.outcome == 'success' }}
        shell: bash
        env:
          HEAD_REPO: ${{ github.event.pull_request.head.repo.full_name }}
          REPOSITORY: ${{ github.repository }}
          BASE_REF: ${{ github.base_ref }}
        run: |
          if [ "$HEAD_REPO" != "$REPOSITORY" ]; then
            echo "fork PR: attestation not required"; exit 0
          fi
          warden attest check --base "origin/$BASE_REF"
      - name: require the verify job
        if: ${{ !cancelled() }}
        shell: bash
        env:
          VERIFY_RESULT: ${{ needs.verify.result }}
        run: |
          if [ "$VERIFY_RESULT" != success ]; then
            echo "warden gate: the verify job ended $VERIFY_RESULT; the gate fails until every verify scope passes." >&2
            exit 1
          fi
      - name: upload evidence run dirs
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v7.0.1
        if: always()
        with:
          name: warden-out
          path: .warden/out/
          if-no-files-found: ignore
```

**The gate runs no project checker.** `warden review --no-project-checkers`
exits 2, the gate DID NOT RUN, on a module under `.warden/checkers/` and on an
`engine: python` rule that no core checker serves, because either one is code
from the checkout running in the gate job. The emitted gate refuses both the
same way. That is why step 2 says to leave `handler-response-contract`
out: it is hello-svc's demonstration of the project-checker seam, and its
checker is a module under `.warden/checkers/`.

**The gate declares its toolchain and installs no consumer one.** The
pre-flight step above names every binary your verify commands invoke that the
gate does not install, resolves each with `command -v` before the first scope
runs, and exits **2** — the gate DID NOT RUN — naming what is absent and the
`PATH` it searched. Without it an enrolled Go or Node repository whose runner
image drops the toolchain gets a bare `exit 127` inside a step named
"warden verify", with no statement of cause. A **required** tool absent is a
red gate and never a skip: a verify scope that could not run its commands
proved nothing, and naming the gap is how that is reported, not a licence to
pass anyway. Keep the pair list in step with your `repo.yaml` — a pair naming
a binary the gate installs is a check that cannot fire, and a binary in
neither half is the hole the step exists to close.

Every verify scope a diff can REQUIRE must run in the verify job — one
`warden verify --scope <name>` step each — and reach the gate job through the
uploaded results and `warden take`. The gate pairs a verify result to the
review on two axes, the commit and the scope, and it reads the evidence from
its own filesystem: a scope whose result never arrives there renders
`NO VERIFY RESULT` on every PR. Which scopes a given diff requires follows from your
`repo.yaml`, and the honest way to say what a step exercises is to DECLARE it:
`covers: [glob, ...]` on the step, and the scope is required exactly when a
changed path matches one of those globs. Declare it wherever a
command reaches outside its `cwd` — `pytest ../..`, a tool whose config sits at
your repo root — and write `covers: ["**"]` for a step that exercises the whole
tree. Where a step declares nothing the reach is INFERRED, and an undeclared
scope narrows to the subtrees its steps run in only when
EVERY step's `cwd` sits inside a declared `components:` area (the component's
own path, or an ancestor of that `cwd`); anything else is required by every
diff. `warden declare check` D-01 reports every step still narrowing by that
inference, and refuses a `covers` glob that matches no path in your tree — a
glob that can never fire would exempt the step from every diff while reporting
a declared reach. Running a scope in a per-component build job as well is fine;
that copy is for the builders, not for the gate.

Those action pins are immutable by design — a SHA cannot be force-moved — but a
pin nobody updates decays into an unpatched dependency. The example ships
`.github/dependabot.yml` as its **freshness mechanism**, and it *travels*: copy
hello-svc out and the config comes with it, proposing one grouped monthly PR
that bumps each SHA and its version comment together. (GitHub reads Dependabot
config only from a repo root, so it has to sit beside the workflows a consumer
takes.) Keep it or replace it with your own cadence — leaving pins with no
freshness mechanism is exactly the decayed state the `pinned-actions` rule
names.

**Make it required.** The gate only gates if the merge button enforces it. The
status-check context is the job's `name:`:

```sh
gh api -X PUT "repos/OWNER/REPO/branches/main/protection" --input - <<'JSON'
{
  "required_status_checks": {"strict": true, "contexts": ["warden gate"]},
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null
}
JSON
```

## Another CI: the GitHub-only limit

**The gate's CI half is GitHub Actions only.** `warden init` writes one
workflow, `.github/workflows/warden.yml` (`warden-svc-api.yml` for an
enrollment at `svc/api`), and has no flag for any other CI.
No renderer exists for GitLab CI or any other system. The checks that read
CI configuration read GitHub workflow files and nothing else. The commands
themselves are not tied to GitHub: `warden verify`, `warden review --base`,
`warden attest check` and `warden attest classify` read git and local files,
and run in any CI that has the CLI installed and full history. The parts
below are what you lose and what you do by hand instead.

| Check | What it reads | With no GitHub workflow |
|---|---|---|
| `certify` G-04, V-02 | text in `.github/workflows/*.yml`: `warden review`, `warden verify` | **fails**, even when `.gitlab-ci.yml` runs both. Level 1 needs G-04, so `warden certify` reports LEVEL 0, and an overlay cannot remove a baseline check |
| `certify` E-01, E-03 | GitHub workflow grammar: a step running `warden attest check` (on `pull_request`) and `warden memory ingest` (on `push`), with no `continue-on-error` | fails, so Level 4 is out of reach as well |
| `certify` R-14 | the same parser, for `warden attest classify --enforce` | passes only while `graph.yaml` declares no light round; once one is declared it fails, and Level 3 fails with it |
| `declare check` D-03 | the jobs in `.github/workflows/` that run `warden review`, and whether each runs every verify scope | **not-applicable**, and the summary still reads clean. A scope your CI never runs is not reported |
| `declare check` D-05 | the repository's `allow_auto_merge` setting, through `gh api` | only when `graph.yaml` declares `review.delegation`. `gh` cannot answer for another forge, so it prints UNREADABLE, which leaves the exit code alone |

Do not keep the generated workflow in a repository whose CI GitHub does not
run. G-04 and V-02 match its text, not a run, so with it in
place `warden certify` credits a gate that never executes.

**The manual steps.** Copy the three jobs the generated workflow has, each
triggered by the event your forge raises for a merge request or pull
request. Replace `<scope>` with each name under `verify:` in `repo.yaml` and
`<target>` with the branch the change merges into.

1. **install** is the only job that can hold a secret, the deploy key a pin
   of a private build needs, and it runs nothing from the repository. It builds the platform wheel at
   `platform.pin` and passes the wheel and its hash on.
2. **verify** runs with no secret. It installs that wheel, runs every scope,
   and passes the `.warden/out/*-verify/verify-result.json` files on:

   ```sh
   warden verify --scope <scope>   # once per scope
   ```

3. **gate** runs even when verify failed, and its first step fails it unless
   install and verify both succeeded. A forge may not count a skipped
   required job as a failure, and `warden review`'s exit does not read
   whether verify passed. It needs full history and the target branch
   fetched, installs the wheel after checking its hash, and runs no code
   from the repository:

   ```sh
   warden take --from <dir the verify results were downloaded to>
   warden review --no-project-checkers --base origin/<target>  # 0 pass, 1 blocking, 2 did not run
   warden attest check --base origin/<target>                   # the committed pre-PR review attestation
   ```

What each assumes:

- **Merge request code stays out of the gate job.** `warden verify` runs
  commands a merge request controls. In the job that runs `review`, such a
  command could replace the `warden` on `PATH`, or rewrite the rules or
  `.warden/out/`. Without `--no-project-checkers`, `review` also imports any
  module under `.warden/checkers/`. One job that runs both is a gate the
  merge request can switch off.
- **Verify results are taken, not re-run.** `warden review` pairs a verify
  result by commit and scope name, read from `.warden/out/` in its own
  filesystem, and `warden take` copies them there. A scope with no result
  reads `NO VERIFY RESULT`. D-03 is the check that would catch a scope your
  CI never runs, and it cannot see your CI.
- **Every nonzero exit fails the job.** Exit 2 means the check did not run,
  which is not a pass. Nothing reads your CI file for `|| true`,
  `allow_failure` or a job that was skipped. On GitHub, E-01 reads the
  `warden attest check` step for a swallowed exit, and no other step.
- **The commit under review.** `warden attest check` reads the PR head from
  GitHub's event file, and without one it checks `HEAD`. If your CI checks
  out a merge commit, pass `--head <source branch tip sha>`. Otherwise the
  attestation is looked up against a commit nobody reviewed.
- **No PR comment.** `warden review --base` prints the comment to the job
  log and never posts it. `--event`, which posts, reads a GitHub Actions
  `pull_request` event and calls the GitHub API.
- **A light round.** If `graph.yaml` declares one, add
  `warden attest classify --enforce --base origin/<target>` to the gate job
  after `review`. R-14 cannot see that step.
- **Evidence on merge.** For what E-03 asks of GitHub, run
  `warden memory ingest` in a job on pushes to the default branch.
- **The merge button.** Make the gate job required before merge in your
  forge's settings. If `graph.yaml` declares `review.delegation`, turn off
  your forge's merge-when-the-pipeline-succeeds setting by hand. D-05 cannot
  read it.

## 5 · Day one, before anything has recorded itself

Review memory only sees PRs that produced an attestation, so a freshly enrolled
repo has none. `mine` reads the history you already have:

```sh
.warden/bin/warden mine --since 2026-01-01
```

It extracts defect signals from every merged PR — reverts and what they
reverted, fixes that replaced lines a merged PR introduced, review comments,
CI failures by check name, and the merging actor. It needs no `repo.yaml`.

It reads within **stated bounds**, not exhaustively: the PR list is capped at
200 and per-PR requests at `--pr-limit` (default 40), and the artifact records
each cap whenever it truncates. A class it could not read is named **UNREAD**
and omitted from the counts — never reported as a zero, because a zero means
"looked, found nothing".

## The maturity ladder

`warden certify` turns "how enrolled are we, really?" into a number that can go
red in CI.

| Level | Name | Means |
|-------|------|-------|
| 1 | Gated | a PR cannot merge without the deterministic gate speaking |
| 2 | Verified | deterministic checks exist and CI actually runs them |
| 3 | Reviewed | judgment is orchestrated and leaves an attestation |
| 4 | Evidenced | every review leaves evidence, CI enforces it, and the corpus is real |
| 5 | Self-improving | the org is declared, its reviews compound, and rule changes carry proof |

Levels are cumulative and cannot be skipped. Projects extend the ladder in
`.warden/certification.yaml` but can never remove or redefine a baseline check
— a ladder you can shorten certifies nothing.

Unattended operation is a **capability badge** (`--capability autonomous`), not
a rung: a repo with no cage can climb the whole ladder, and a caged Level-1
repo still earns the badge. Check it on the machine that hosts the cage — the
cage lives beside the checkout by design (`../<checkout>-cage/` or `../cage/`,
the one declaring this checkout as its `live_checkout` winning), so a CI clone
has no sibling cage.

`warden certify --goal` is a different question from the ladder: not "how
enrolled", but whether the platform's own success criteria hold — five items
read from committed shards, two workflow files, the README's Try it block
and, with `--ledger PATH`, the cage's ledger, each `PASS`, `FAIL` or `UNMEASURED` with the file or number it
was read from. `--report-only` exits 0 so a CI step can post the table
without gating on it; see [CLI-Reference](CLI-Reference.md) for the items.

**Reaching Level 4** adds two obligations. The attestation step above must run
*enforced* — no `continue-on-error` at step or job level, in a workflow
triggered on `pull_request`; **E-01** parses your workflows and refuses an
advisory step. And a `push`-triggered job must run `warden memory ingest`
(**E-03**), so the corpus is rebuilt and validated on every merge:

```yaml
  corpus:
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
      - run: .warden/bin/warden memory ingest
      - run: .warden/bin/warden certify --level 4
```

`memory ingest` here is a blocking step, and it stays one about the corpus
alone: a breached tag-vocabulary `ceiling:` is reported, never failed
([Memory](Memory.md#three-dispositions-and-the-drift-ceiling)), because this
step's exit code is what tells the cage whether the corpus rebuilt. If you
declare a ceiling, enforce it with `warden memory check-vocabulary` from a
job that runs on `pull_request`, so the obligation lands on the branch that
drifted rather than on `main` after the merge — exit `1` on a breach, `2`
when the declaration cannot be read or none exists, never a silent pass:

```yaml
      - name: tag-vocabulary drift ceiling
        run: .warden/bin/warden memory check-vocabulary
```

`examples/hello-svc` ships that step beside its gate, and the platform's
`scripts/portability-sim.sh` proves it reddens on a planted undecided tag
using nothing but the shipped command.

**E-04** additionally needs 5+ committed review-event shards, and **E-02** needs
every shard's `rule_id` to resolve. A gate reachable only via `merge_group` or
`workflow_call` does not satisfy E-01 today.

**Upgrading past #167 — one thing to check if you hold a paused
rule.** Certification **S-05** now requires every rule carrying `paused: true`
to have a pause backtest of its own under `.warden/memory/backtests/` naming
that `rule_id` with `"action": "pause"`. Any `rules_version` will do — an
artifact you already have still counts — so a rule paused through
`warden autonomy pause` needs nothing: that command writes the edit and the
artifact together. A rule paused **by hand** with no artifact fails the rung and
drops the repo to Level 4, with the failure naming the rule. Write the artifact
once (see [Memory](Memory.md#backtest-artifacts) for the required fields) and it
stays satisfied.

**Upgrading past the S-04 change: Level 5 needs a promotion, not a
candidate.** S-04 used to pass when any rule or candidate cleared the promotion
bar. It now passes only on a promotion record under
`.warden/memory/promotions/` that records a retro as the proposer
(`"proposed_by": {"source": "retro", "date": "YYYY-MM-DD"}` and a `proposal`
citing it) and names, in `backtest`, a `promote` artifact for the same rule
whose `judged` and `upheld` clear the bar and which carries `window` and
`derived_from`. A repo with no such record drops from Level 5 to Level 4, and
the S-04 detail says why each record it found does not count. A promotion a
retro really did propose needs only a new record; the backtest is left as it
is. The old corpus reading is still available as the `promotion_bar_met` check
type for an overlay.

Nothing here proves a retro ran. S-04 takes the record's word for the
proposer and its date, and the artifact's word for the counts; it re-derives
neither, so any over-bar promote artifact paired with a hand-written record
passes. The pass detail says as much — *provenance and counts as recorded,
not verified* — and a reviewer reads the `proposal` to see where the retro
proposed the rule.

**Upgrading past #186 — your per-rule `n` can move on a corpus that
did not change.** Review memory now counts FINDINGS, not records: a finding
re-attested under a moved head (the head binding refuses a stale attestation,
so a repair round means a re-attest) counted once per shard before and counts
once in total now. No committed shard is touched — the fold is a read. What
moves is arithmetic downstream of it: a rule's `n`, its Wilson floor, and
therefore **S-04**. A repo whose only promotable rule sat at n=10–12 with
restated copies can fall under the bar and drop from Level 5 to Level 4; the
S-04 detail names the fold count so `certification.json` says why, and
`warden memory stats` prints `<N> restatement(s) folded` beside the count.
There is no remedy to apply and none is wanted — the old number counted one
finding several times. Read the new one, and re-read any promotion decision
taken on the old one. `warden autonomy pause` moves the same way and only
ever gets quieter: a streak built partly on a refutation carried forward
shortens, so a rule that was a pause candidate on the old pin may stop being
one. The fold can never LENGTHEN a streak, and can never LOWER the suppressor
that stops a candidate being proposed — both directions would remove a
guard, and both are refused by construction.

**Three more readings move on the same bump, and none of them is a defect to
fix — they are numbers to re-read.**
1. **The tag drift ceiling.** `warden memory ingest` now scores
   `tag_ceiling` and `unknown_tags` over the folded corpus, so a tag carried
   only by a folded-away copy stops counting. `ingest-result.json`'s
   `breached` and the `TAG CEILING BREACHED` line on stderr can flip
   **true → false** on an unchanged store, and a per-tag count at the
   3-judged declaration bar can drop below it. If your CI enforces the
   ceiling, expect it to get quieter, never louder.
2. **The review-event count, and with it E-04.** A shard whose `records` is
   not a list of objects is now counted `unreadable` rather than as a review
   that filed nothing, so `review_events["total"]` falls by the number of
   such shards — and **E-04 needs 5+**: a repo sitting exactly on its minimum
   with a malformed shard drops from Level 4 to Level 3. Unlike the others
   this one has a remedy, because the shard is genuinely broken and every
   reader now says so by name, in one of two messages depending on the
   shape: `<shard>: not an evidence shard (no records list)` when `records`
   is missing or is not a list, `<shard>: record N is not a mapping (str)`
   when the list's elements are not objects. `warden memory ingest` exits 2
   on either and E-02 fails naming the same file. Repair it at its source
   (the producer that wrote it) or remove it; do not edit a committed shard
   to make a number move.
3. **Four readers of the corpus now agree about what a shard IS.**
   `records_from_shards`, `review_events`, `memory ingest`'s cache rebuild
   and certification's E-02 each had their own idea, and for a `records` list
   whose ELEMENTS were not objects three of them raised an uncaught
   `AttributeError` or `TypeError` from deep inside — so `warden certify` and
   `warden memory ingest` died with a traceback naming neither the shard nor
   the reason, rather than the corpus-unreadable verdict they documented.
   All four now refuse it by name. A consumer who was seeing a traceback will
   see a verdict; a consumer who was seeing neither had a shard nobody had
   tried to read yet.

**Upgrading past #190 — the agreement above now
covers the ENVELOPE and the `status` field, not just the records list.** The
paragraph above was true of a shard's `records` list and not of the object
around it, and `memory ingest` was the reader that disagreed in all three
directions at once. FIVE more shapes are refused by name where they were
previously accepted or fatal. Three of them are shapes `ingest` alone
mishandled, so a store read only by `ingest` may fail for the first time on
this bump; the other two (`status` and `verdict`) also tighten
`review_events` and E-02, so a store your `warden certify` already reads is
affected too:

- a shard whose top level is **not a JSON object** (an array, a scalar) —
  `ingest` raised `AttributeError: 'list' object has no attribute 'get'`;
- a shard with **no `records` key at all** — `ingest` defaulted it to empty
  and said nothing, while the other three refused it. This is the fail-OPEN
  direction, and it is the one to expect: the command a consumer is told to
  run to find the bad shard was the one staying quiet about it;
- a record with **no usable `id`** — `ingest`'s cache rebuild died on
  `KeyError: 'id'`. This one is `ingest`-only and stays so: it is the only
  reader that INDEXES by the field, and requiring it of readers that never
  touch it would refuse committed evidence over a key they do not read;
- a record whose **`status` is not a string, or is absent** — the one field
  no reader guarded. A list or a dict raised `TypeError: unhashable type` out
  of the set-membership test in `tags.undeclared_at_bar`, past `warden memory
  check-vocabulary`'s handler: a traceback, exit 2, no run dir and no
  artifact. `memory ingest` now refuses the ARTIFACT carrying it before the
  shard is written, so a hand-written or third-party `attestation.json`
  cannot poison the store in the first place.

- a shard whose **`verdict` is present and not a string** — the one ENVELOPE
  key a reader dereferences by HASH (`review_events` does
  `_VERDICT_BUCKETS.get(doc.get("verdict", ""))`), on a line outside the try
  whose `unreadable` bucket exists for it. A list raised `TypeError:
  unhashable type` past `certify.run`'s bare check loop and out through the
  CLI's blanket handler — traceback, exit 2, no verdict, no
  `certification.json` — while `records_from_shards` accepted the same shard.
  An ABSENT `verdict` stays legal, measured rather than assumed: 9 of this
  repo's 150 committed shards carry none (they predate the field) and
  `review_events` buckets those as `verdict_unrecorded` on purpose.

**The absent-`status` half of the fourth one is the widest change here, and it
is deliberate.** On the old pin such a record was counted as a perfectly valid
review event by all four SHARD READERS — `None` is hashable, so it slipped
every one of them — while `warden certify` died on it anyway, with an uncaught
`KeyError: 'status'` reaching the S-04 arm — `promotion_bar_met`, a
LEVEL-5 rung — through `memory.stats`. (An earlier
draft of this paragraph said an absent `status` "never crashed anything";
that was measured wrong and is corrected here.) So the change is not turning a
harmless shape into a fatal one — it is turning a crash with no name into a
refusal that names the shard. It is refused rather than defaulted because
there is no honest default to fall back on: `recall` reads `record["status"]` directly,
every counting seam buckets by it, and `attestation.schema.json` has always
made it REQUIRED. A store carrying one gets `memory ingest` exit 2 and E-02
red — **Level 4 → Level 3** — on an otherwise unchanged tree. E-04 goes short
only if the store was already sitting on its five-review-event minimum: the
status-less shard moves from counted to `unreadable`, which costs one event,
not the rung. (An earlier draft claimed E-04 short unconditionally; the
independent judge overturned it by execution, and the corrected form is the
one to plan against.) No
platform version has ever produced such a record: `build_records` is the only
writer of shard records and has always defaulted `status` to `detected`. The
exposure is a hand-written or hand-repaired shard.

Measured before shipping, on this repo's own corpus at 150 shards / 1512
records (at `48f2b12`, the last merge before this PR's tip): **zero shards change validity in either direction.** Nothing the
producer writes is newly refused, and nothing previously refused is newly
accepted. If your own store trips one of these, the shard is genuinely
malformed — repair it at the producer, never by editing committed evidence.

**Upgrading past #195 — the record field contract.** The class the
section above left open is closed, and closed by a mechanism rather than by an
enumeration. `warden/memory.py` now declares `RECORD_FIELD_CONTRACT`: one table
naming the type of every field `build_records` writes, consulted by the single
shared validator every reader of the store calls. Measured on this repo at
`5a9d14b` with the harness described below: **29 distinct single-field shapes
ended in an uncaught traceback before; 0 do after** (81 command/shape pairs
down to 0), over 165 malformations
(22 record fields + 11 envelope keys, x `{list, dict, int, null, absent}`)
driven through 9 corpus-reading subcommands.

Three fields are REFUSED when ABSENT **OR EMPTY** — `ts`, `rule_id`, `status` —
because every counting seam buckets by them and the empty default folds
distinct records into ONE bucket, which is fail-OPEN in the Wilson math. Empty
is the producer-reachable half: `build_records` derives `ts` from
`attestation.get("reviewed_at", "")`, so an artifact with findings and no
`reviewed_at` used to file records with `ts: ""` and every one of them bucketed
into `("", sha)`. Every other
field DEFAULTS at its reader instead (`dir_prefix`, `file`, `finding` and the
optional provenance fields), deliberately: refusing an absent render or filter
field would be widening a blocking refusal measured only against this repo's
corpus. A PRESENT value of the wrong type is refused for every field, with one
exception stated in the table: `tags: null` stays legal, as an older shard's
spelling of "no tags", and now actually gets the `[]` default its readers
always claimed to apply.

**The same contract now covers the DERIVED cache.** `.warden/memory/findings.jsonl`
was the fifth reader of the store and the last one with no shape check after
its `json.loads`, so `memory recall`, `memory stats` and `warden plan` read
malformed rows straight into a traceback. It validates through the same
definition and raises naming the file and the field; because the cache is
derived, the message says to delete it and re-run `warden memory ingest`
rather than to edit it. `warden plan` is the one reader that does not refuse:
priors are advisory, so it plans anyway and records the reason in its
**Caveats** block — an empty priors section would otherwise read as "no priors
on record".

Measured in BOTH directions on this repo's corpus at **162 shards / 1614
records** (at `6184e63`; the count moves with every round this branch attests,
so it is quoted with its sha): **zero newly refused and zero newly accepted.** Every field every
record carries is already of the declared type, and the three newly-required
fields are present and non-empty on all 1614 of them — so typing them refuses nothing this producer
has ever written. If your own store trips one of these, the shard is genuinely
malformed: repair it at the producer, never by editing committed evidence.

**The mechanism, because the enumeration is what kept failing.**
`tests/test_malformation_matrix.py` derives BOTH of its axes from the code:
the field list out of `memory.build_records`' own AST, the command list out of
`cli.build_parser()`'s subparser table. Every CLI leaf is either driven against
malformed input or carries a written exclusion, and the test fails on a leaf
that is neither — so a subcommand added tomorrow, or a field added to the
producer, is covered with nothing here to remember to edit. It runs in CI as
part of `uv run pytest`.

Still NOT covered, and not claimed: multi-record or multi-shard interactions,
values of the right type and the wrong format (a `ts` that is a string but not
a timestamp), malformations nested below one field, consumers that call warden
as a LIBRARY rather than through the CLI, and `repo.yaml`'s per-key contract
(which is `repo.schema.json`'s, enforced by `config`).

**`warden attest check` now checks them too — see the #196 block
below.** Until that change its `attested_shas` read the envelope for a usable
`sha` and `source` only, so the shard shapes above satisfied the BLOCKING
attestation gate and were refused one step later, at `memory ingest`.
`check_range` reads the `verdict` of each TIP shard of the range — a matched
shard no other matched shard descends from, so a merged range has one per
line of history and all of them bind.

**Upgrading past #195 — S-04 refuses to score over an unfolded
corpus.** `warden certify`'s S-04 (`promotion_bar_met`, a LEVEL-5 rung) used
to compute the promotion bar with no complaint when the vocabulary could not
be folded: `load_aliases` returns `{}` for a non-mapping `aliases:` block — it
is the permissive reader every folding seam calls — so `canonicalize_records`
early-returned on the empty table and two declared spellings of one class split
`n` and the Wilson floor. One command said "cannot evaluate the vocabulary",
another handed the same tree a confident badge off the same broken block. S-04
now takes the `cannot evaluate` arm it already had two of (an unresolvable
`rules_dir`, an unreadable ruleset), naming the reason. It asks about the whole
FOLD, not only the block: an unparseable `tags.yaml`, a top-level non-mapping
and an unreadable file all leave `load_aliases` returning the same empty table,
and scoping the arm to the block alone left three of the four causes still
scoring the rung (found in this change's own review round).

**This can drop an existing consumer from Level 5 to Level 4** on an unchanged
tree. Two causes, and the second is the record contract above, not this arm:

1. a `tags.yaml` that cannot be read or whose `tags:`/`aliases:` block is not a
   mapping — the same tree `warden memory check-vocabulary` already exits 2 on,
   so run that before you bump; it is the cheaper signal and it predates this
   rung arm;
2. a committed shard carrying a field of the wrong type, or missing or empty in
   one of the three required ones. `records_from_shards` refuses it, so S-04 reports
   `corpus unreadable` and E-02 goes red with it. Nothing this platform has
   ever written is such a shard, and this repo's own 162 (at `6184e63`) are all clean — but a
   hand-written or hand-repaired one is exactly the case, and it drops the rung
   through the record contract rather than through the vocabulary.

The rung's REQUIREMENT is unchanged in both cases: what changed is that it no
longer answers a question it could not evaluate.

**"Repair it at the producer" — what that means for a shard already
committed.** The producer is the artifact under `.warden/out/*-attest/`, and
re-running `warden memory ingest` over a corrected artifact mints a NEW shard;
it does not rewrite the old one, and there is no `warden memory repair`. So the
sequence for a store that trips the contract is: find the shard the refusal
names, correct the ARTIFACT it came from if you still have it, and remove the
bad shard in a reviewed commit that says what was wrong with it — a deletion
with a stated reason, in the branch's history, rather than an edit that leaves
committed evidence saying something nobody filed. The rule the phrase is
guarding is narrower than it reads: never silently EDIT a record's content to
move a number.

**Upgrading past #195 — four complaint labels where there was one.**
`.warden/memory/tags.yaml` complaints are merged from four sources, and two
of the three renderers that print them (`memory stats` and `memory ingest`'s
stderr line) prefixed every one of them `CEILING UNREADABLE` — asserting a
declaration that may not exist. One shared labeller
(`tags.label_complaint`) now answers which declaration a complaint speaks for,
and all three renderers, `memory check-vocabulary` included, print its answer.
If you grep logs or CI output for
`CEILING UNREADABLE`, widen it: the labels are `TAG CEILING UNREADABLE` (the
`ceiling:` block), `VOCABULARY UNREADABLE` (the whole file, so whether a
ceiling is declared cannot be known), `DECLARATION BLOCK UNREADABLE` (the
`tags:`/`aliases:` blocks) and `RECEIPTS UNREADABLE` (`left_undeclared:`).
`warden rules recommend`'s unrelated ceiling — the guardrail-gap ceiling in
`.warden/catalog-answers.yaml` — now prints `GAP CEILING UNREADABLE` with the
file named, because two different things called "the ceiling" in two different
files is how that line came to be named as a defect site when it was not
one.

**Upgrading past #195 — a broken RULESET names
itself.** `warden/rules.py` checked a rule's frontmatter `id` for PRESENCE
only, so `id: 7` or a blank `id:` made `attest._check_rule_ids` raise
`TypeError` — which `memory ingest` caught in the arm that reports a malformed
ARTIFACT. It named the run dir (the one thing that was fine) and **exited 0**,
the code `cage/run.sh` reads as "the corpus rebuilt". A rule file that cannot
be READ at all (chmod 000, a dangling symlink) was fatal but raised a bare
`OSError` mid-sweep: a traceback, no `ingest-result.json`, and the store left
written with the cache below never rebuilt. A non-string or blank `id` is now
a `RuleError` naming the RULE FILE, and the ruleset is resolved ONCE before the
sweep, so one that cannot be read exits 2 naming the ruleset with nothing
half-written.

Read "cannot be read" as the probe asks it: TWO questions with DIFFERENT
conditions, and they must be planned for separately. An earlier version of this
paragraph gave both the one bound below, which was already retired for the
first half when it was written.

- **Can the ruleset be READ at all?** — the rules dir cannot be listed, or one
  of the surfaces the reader opens cannot be opened: a rule file at chmod 000,
  a dangling symlink, and — since #197 — a `.warden/checkers/*.py`
  the probe used not to look at, because the probe now CALLS
  `rules.rules_version` instead of walking one surface by hand. Asked
  **unconditionally**. Exit **2** naming the ruleset, with nothing
  half-written, whether or not the sweep has an artifact to resolve rule_ids
  against — a tree with no `.warden/out` at all included. Gating this half on
  "is there something to sweep" moved the traceback rather than removing it,
  because `cli._cmd_memory` reads the ruleset again for the run manifest
  *after* `ingest` returns. Pinned by
  `tests/test_malformation_matrix.py::test_r201_an_unreadable_ruleset_names_itself_with_nothing_to_ingest`,
  and driven on both tree shapes by
  `tests/test_doc_fidelity.py::test_the_ruleset_probe_behaves_as_the_table_says`.
- **It opens, and is UNUSABLE** — a bad `severity` or `engine`, a missing
  frontmatter key, a non-string `id`, a duplicate id, **or bytes that are not
  UTF-8**. Asked **only when the sweep has at least one artifact** to resolve
  rule_ids against. A tree with an unusable ruleset and nothing to ingest still
  exits **0**, which matters because `cage/run.sh` reads that code as "was the
  corpus fed". The decode case is here rather than above and that placement
  MOVED: #197 put it here, because a file that is not UTF-8 opens
  perfectly and is merely unusable, and reading BYTES on the read side keeps it
  that way by construction rather than by a maintained exception tuple. Pinned
  by
  `tests/test_malformation_matrix.py::test_cr05_an_invalid_ruleset_with_nothing_to_ingest_still_exits_zero`
  and
  `tests/test_ruleset_surfaces.py::test_a_non_utf8_rule_file_with_nothing_to_ingest_still_exits_zero`.

Neither REFUSAL is new in kind — such a ruleset already reddens the gate, which
calls `load_rules` too. What `memory ingest` ITSELF does is worth a table,
because the two questions land on different exit codes. **On a tree with
nothing to ingest**, driven against the platform as it stands:

| the ruleset | `warden memory ingest` |
| --- | --- |
| chmod 000 | exit 2 naming the ruleset |
| a dangling symlink | exit 2 naming the ruleset |
| a rules dir that cannot be listed | exit 2 naming the ruleset |
| bytes that are not UTF-8 | exit 0 |
| opens and is otherwise UNUSABLE | exit 0 |

Every row is driven rather than remembered, by
`tests/test_doc_fidelity.py::test_the_ruleset_probe_behaves_as_the_table_says`
— the prose around it went wrong in three consecutive review rounds while only
the bullets were guarded, and the last two rows moved again when
#197 landed. Put an artifact in `.warden/out` and the last two rows
become exit 2 as well; that is the gate, and it is the whole of what the second
bullet's condition buys.

WHAT THIS COSTS YOU ON THE BUMP, stated in prose because it is a claim about
two platform revisions and no test on one tree can drive it: the top three rows
used to reach `cli.main`'s blanket handler as a traceback, or — for the
unlistable dir — exit **0** in silence, which is the code `cage/run.sh` reads
as "was the corpus fed". So this is a step that can go from GREEN to red for
the unlistable-dir case, and from an unnamed traceback to a named refusal for
the other two. An ABSENT or empty rules dir is unchanged: neither question
fires on one.

**Upgrading past #195 — a non-UTF-8 `cage.toml` is unreadable, not a
crash.** Three call sites in `warden/certify.py` caught
`(TOMLDecodeError, OSError)` around `tomllib.loads(cage.read_text())`, and
`Path.read_text()` raises `UnicodeDecodeError` — a `ValueError` that tuple does
not cover. `warden certify` raised before any badge rendered: the ladder did
not print at all. The three now share one tuple, so such a config reads as
unreadable and the cage checks fail closed with a detail naming it.

**Upgrading past #196 — `warden attest check` now refuses a shard
every other reader already refused. Read that as a step that can go from GREEN
to red.** `attested_shas` — the read behind `warden attest check`, which is the
step that reddens a PR — validated a shard's ENVELOPE only: it asked for a
usable `sha` and a `source` and looked at the `records` not at all. So a
committed shard whose ENVELOPE or RECORDS were malformed SATISFIED the blocking
gate and was refused one step later by `memory ingest`. Existence checked,
validity not. It now goes through `memory.validate_shard_envelope`, the same
definition `memory.read_shard` calls, so every shape that validator refuses
lands in `unreadable`.

**Read that as the validator's whole refusal set, not as a list you can audit
against.** Eight shapes move: a top-level non-object, `records` not a list, no
`records` key, a record that is not a mapping, a record with an absent OR
non-string `status`, a non-string `rule_id`, a `tags` field that is PRESENT,
NON-NULL and not a list of strings, and a non-string `verdict`. `tags: null` is
the one carve-out and it is deliberate — it is an older shard's spelling of "no
tags", and `RECORD_FIELD_CONTRACT` types the field `list[str]|null`. It
protects a shape an OLDER producer could write, not one this corpus contains:
no record in the committed store carries `tags: null`, and none omits `tags`,
so refusing the shape today would refuse nothing here. The carve-out is kept
for the producer;
`tests/test_vocabulary_check.py::test_a_record_with_no_tags_is_a_record_not_a_refusal`
pins that such a record stays a record. The first
wording here said refusing it "would refuse the corpus" — a clause lifted from
the `warden/memory.py` comment beside it, where that IS measured of `lens` and
`pr`, and reused where it was not. Each was verified refused by
`memory.validate_shard_envelope` itself, and the list stands on THAT and on
nothing else. It used to be credited to the shapes the envelope section above
enumerates, which was wrong in both directions — that section's five bullets
enumerate neither a non-string `rule_id` nor a `tags` field that is not a list
of strings, and they do enumerate the id-less record, which the paragraph after
this one says is NOT one of these. The cross-reference is dropped rather than
corrected: a list pinned to a neighbouring section goes wrong when either one
moves, and this one is read off the validator. It is a floor rather than a
count, for the reason the record-field-contract section above gives: the class it
closes was "closed by a mechanism rather than by an enumeration", because "the
enumeration is what kept failing". `attested_shas` now applies that same shared
validator, so what it refuses is whatever `memory.validate_shard_envelope`
refuses — the `RECORD_FIELD_CONTRACT` table for every record field, PLUS the
structural refusals that table does not own: a top-level non-object, a present
non-string `verdict`, and (in `validate_shard_records`) a `records` that is
absent, is not a list, or holds a non-mapping. That second half is the larger
one here, and the number is measured rather than asserted: with
`RECORD_FIELD_CONTRACT` emptied, FIVE of the eight shapes above are still
refused. So auditing a store against the table alone would clear five of the
eight shapes this block lists. If you are auditing a
store before bumping, run `warden memory ingest` against it: it applies the
same definition and names what it refuses, which is a stronger check than
reading any list here.

**The id-less record is NOT one of them, and stays `ingest`-only.**
`attested_shas` calls the validator with `require_ids` at its default `False`,
so the third shape in the envelope section's list above — a record with no
usable `id` — still passes `attest check` exactly as that section says it does. Keep
expecting `memory ingest` to be the step that finds it.

**What that costs you, and the exit code for each state.** Both are numbered,
because repos treat exit **1** (a gate finding, expected on a red PR) and exit
**2** (an infra error, often alerted or exempted) very differently:

- **A shard matching your range trips the envelope floor.** It lands in
  `unreadable`, so nothing matches, and `check_range` raises: `warden attest
  check` goes from **0** to **2**, not to 1. There is no path where this state
  is the ordinary "no attestation" refusal.
- **No shard matches your range yet** — your first PR after enrolling — **and
  any historic malformed shard sits in the store.** The exit moves from **1**
  ("no attestation", a determinate answer) to **2** ("cannot tell", the
  fail-closed one).

The timing moves too, and that is the half worth planning for: `memory ingest`
is documented above as a step on a PUSH-triggered job, so before this change a
corpus defect of this shape surfaced AFTER merge; now it blocks the PR.

**`warden deploy` moves with it, and it is the sharper change of the two.**
`deploy._parse_shard` was a fifth hand-rolled reader of the same store whose
comment claimed the parity it had just lost, so it is on the shared definition
now. Measured on both revisions: a commit whose own first-parent diff
introduces a clean-verdict shard with no `records` key used to parse fine and
grant deploy eligibility (exit 0, or exit 1 with a named refusal), and now
raises out of `clean_attestation` — `warden deploy` exits **2** with
`exit_status: infra_error` and writes **no `deploy-result.json` at all**. So a
consumer with a `deploy:` scope can lose the refusal artifact the schema calls
the evidence, in a state that previously wrote one. If you gate on that
artifact's presence, handle its absence before you bump.

**The remedy, and what it is not.** The failure names the shard AND the cause
("`record 0 carries a `status` that is not a string`"). Repair the shard AT ITS
PRODUCER — never by editing committed evidence, which
`.warden/rules/evidence-intact.md` refuses and which breaks the
content-addressed filename. `warden memory ingest` applies the same validator
and names what it refuses.

**The one shape `ingest` will NOT mention is a shard that names no `sha`** — it
never reads that field. That does not make the shard harmless, and the earlier
wording here said it was refused by `attest check` alone, which was wrong in
three places: `warden certify`'s **E-02** fails the rung on it, `warden
deploy`'s `_parse_shard` raises and the CLI exits **2** with no
`deploy-result.json`, and `memory.review_events` counts it unreadable, so
`warden memory stats` sees it too. Leaving one in place costs a certification
rung and a deploy as well as this check.

Neither `git add` nor re-running the review is the fix for any of this; both
are the remedies for the OTHER two states `attest check` reports.

**Measured before it shipped: over every shard this repo had committed at the
time — 161 when the change was written, 164 once main merged three more while
it was in review, re-measured at both — ZERO change verdict.** That measurement
is over `attested_shas`' rev-wide
read. It does NOT cover `warden deploy`'s per-commit
`diff-tree --diff-filter=A` read, which asks a different question of a
different subset — if you deploy, measure that side against your own store
before bumping.

**Upgrading past #190 — a malformed `tags:` or `aliases:` block is
`cannot-evaluate`, not a breach.** `.warden/memory/tags.yaml`'s `tags:` and
`aliases:` blocks used to load as `{}` for ANY non-mapping value, so one
leading dash deleted the whole declared vocabulary — every name in the corpus
read as undecided, `warden memory check-vocabulary` **exited 1**, and the
breach line told the author to declare names their file already declared, or
to "fold it with an `aliases:` entry" whose entry was in the block that had
just been discarded. Both blocks now fail closed the way `ceiling:` and
`left_undeclared:` already did: the check **exits 2** with `DECLARATION BLOCK
UNREADABLE` naming the block, and a refused `aliases:` block is also reported
under `INVALID ALIASES` beside the remedy it defeats.

**Read that as a step that can go from GREEN to red, not only from red to
red.** Where the old behaviour was a breach verdict the exit code moves
1 → 2, which is the same CI failure with a usable message. But a repo whose
ceiling happened to hold anyway — a small or clean corpus, or a malformed
`aliases:` block over names that were all declared — **exited 0** on the old
pin and exits 2 now. That is the fail-closed direction and the point of the
change, and it is the case to check before you bump: the CI step in
`examples/hello-svc/.github/workflows/ci.yml` is exactly this shape. A block
that is absent, empty (`tags: {}`), or written with no entries under it is
unchanged: it declares nothing, `{}` is exactly what it says, and nothing is
silently dropped.

**Upgrading past #171 — machine adoptions now certify.**
`warden autonomy adopt` writes an `action: adopt` backtest beside the rule it
adds, and S-05 accepts it. Before that the command produced a tree that failed
S-05 outright: adding a rule bumps `rules_version` and nothing named the new
version, so a granted consumer merging a machine adoption dropped from Level 5
to Level 4 with no tooling remedy.

**A hand-added rule is NOT in the same position a hand pause is**, and the
difference is worth stating because the obligations are opposite in both
directions. S-05 binds *pauses* per rule — every paused rule needs its own
artifact, at any `rules_version`, and the failure names the rule. It has no
per-added-rule arm at all: the committed tree cannot show that a rule is
*new* without a ruleset history, which the platform does not keep. So adding a rule by hand fails S-05 only through the
generic freshness arm — *some* artifact must name the current `rules_version`
— which any action for any declared rule satisfies. And an adopt artifact is
**not** write-once the way a pause artifact is: it satisfies that arm only
while its `rules_version` is current, so the next unrelated ruleset edit
stales it. Writing one is still the honest thing to do, and it is what
`warden autonomy adopt` does; just do not read it as a standing per-rule
obligation the rung enforces.

**Migrating an older corpus.** Evidence written before the rule-identity
invariant existed may carry ids no rule answers. Do not rewrite it — declare
those ids in `.warden/certification.yaml` under `grandfathered_rule_ids:` (e.g.
`grandfathered_rule_ids: [general]`). The declaration is reviewed like any gate
change, named aloud in E-02's output, and the promotion gate never counts the
waived ids.

Read the waiver for what it is: a declaration that a slug's records are not
resolved against the ruleset. It is **not** a write-side floor. `attest write`
never reads `certification.yaml` — it refuses a covered slug because some
**unpaused** rule answers the class, so while that rule is `paused:` the class
re-opens as a candidate and fresh `unmapped:<slug>` records are accepted. Those records carry no era marker, so on unpause E-02 absorbs
them under the same waiver and reports them beside the genuinely legacy ones,
with nothing flagging the mixture. Grandfather a slug knowing
that; if you pause a covering rule, expect the waiver to widen for the duration.

## Read before you trust a green check

- **No tests = vacuous verify.** `verify` runs the commands you declared and
  nothing else. A scope whose command is `true` passes forever. The gate
  certifies "declared commands exited 0", not "this code is tested".
- **`engine: claude` rules do not run in CI.** They are attested pre-PR and
  show as *deferred* in the sticky comment. If nobody runs the pre-PR review,
  those rules check nothing. A green gate means the mechanical rules passed.
- **Toolchain drift.** Verify commands inherit the runner's shell. Pin versions
  inside the command, or local PASS and CI PASS mean different things.
- **Main-green pre-flight needs history.** Anything that asks "is main green?"
  needs at least one completed CI run on main to read.
- **Commit vocabulary is per-project.** warden imposes no commit convention.
  Bring your own, and your own enforcement.
- **A local hook gate can be silently switched off.** `core.hooksPath` is one
  per-clone value, so whichever tool sets it last wins and the losers vanish
  without a word — this happened to the platform's own repo. `warden explain`
  reports the state in its **Local gate** section; check it after installing
  anything that manages hooks.

---

Next: [Writing Rules](Writing-Rules.md) · [Configuration](Configuration.md) ·
[Gate Pipeline](Gate-Pipeline.md)
