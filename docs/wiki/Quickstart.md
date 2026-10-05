# Quickstart

Enroll a repository you already have with one command, then watch the gate
work on the example consumer.

Prerequisites: [Installation](Installation.md) — the CLI from a release tag
(`warden --version`). `gh` and the skill pack are not required for this page.

## Enroll your repository with `warden init`

From a directory in a git repository that has a `pyproject.toml`, a
`package.json`, a `go.mod`, a `pom.xml` or a Gradle build or settings file in
it (`init` reads manifests only there). That is
usually the git root; below it, see *Below the git root* after the table:

```sh
warden init
git add -A && git commit -m "enroll in warden"
warden certify --level 3
```

`init` detects the language from those files, with no network, and writes:

| File | What it is |
|---|---|
| `repo.yaml` | components, risk tiers, detected verify commands, `platform.pin` set to the installed version, and `repair.budget` |
| `.warden/rules/*.md` | starter rules from the guardrail catalog whose `applies_when` is unconditional for the detected language: `secrets-in-diff` everywhere, plus `swallowed-exceptions` for Python |
| `.warden/skills-policy.md` | the six sections the skill pack and Level 3 read |
| `.github/workflows/warden.yml` | the gate in three jobs: `install` builds `platform.pin`, cloned with no secret, `verify` runs every verify scope with no secret, and `gate` runs `warden review`, `warden attest check` and `warden certify --level 3`, each whether or not verify passed, then fails if verify did |
| `.gitignore` | `.warden/out/`, `.warden/memory/findings.jsonl` and `.warden/memory/gate/`, plus `*.egg-info/` for Python and `node_modules/` for Node, appended where missing |

The verify commands pass on a repository with no tests and leave the tree
clean: Python runs pytest against `uv.lock` if there is one and in a throwaway
environment if not, and counts nothing collected as a pass; Node installs
without writing a lockfile and runs `npm test` only for a real test script; Go
runs `go vet` and `go test`. What a build writes is ignored rather than
avoided: a setuptools project's `*.egg-info/` and Node's `node_modules/`.
`init` warns where it finds no test.

Each manifest present enrolls its language, so a repository with both a
`pyproject.toml` and a `go.mod` gets a `python` and a `go` verify scope. If any
file it would write already exists, `init` writes nothing, names each one, and
exits 1. It prints the next steps: read the verify commands, commit, store a
deploy key only if you pin a private build ([Installation](Installation.md), *CI access to the platform*),
and make `warden gate` a required check. That check runs `warden attest check`,
so a pull request from the repository stays red until it carries a committed
pre-PR review attestation, which the skill pack's `pre-pr-review` writes
([Installation](Installation.md), *3. The skill pack, once per machine*). The commit is what `certify` needs:
Level 3 checks the derived files are not tracked.

**Below the git root.** Run in `svc/api`, `init` writes the files above in
`svc/api`, except the workflow: GitHub runs workflows only from the git root,
so it writes `.github/workflows/warden-svc-api.yml` there, named
`warden (svc/api)`. Each of its jobs runs its steps in `svc/api`, and the
required check is `warden gate (svc/api)`. Run `certify` from `svc/api`; it
credits that root workflow only while its `name:` is `warden (svc/api)`, and
no other workflow: not another root one, and not one nested in
`svc/api/.github/workflows/`, which GitHub never runs. The name is what tells
`svc/api`'s gate from that of an `svc-api` enrollment, which maps to the same
file name; `init` refuses the second of the two and names the enrollment the
file belongs to. From the git root, `certify` (G-04, V-02 and the E rungs) and
`declare check` (D-03) skip a root `warden-<name>.yml` when it is the gate
`init` names for a `repo.yaml` below the root, tracked or on disk,
`svc/api/repo.yaml` naming `warden-svc-api.yml` — an on-disk one counts
whether or not git ignores it, and only while that workflow's own `name:` is
`warden (svc/api)`; a tracked one is matched on its file name alone, so a root
gate named plainly `warden` stays the root's against an on-disk enrollment but
not against a tracked one —
and skip any root workflow whose jobs all run below the root. Every other root
workflow counts, including one you named `warden-ci.yml`. When that skip leaves G-04 with no
gate, its line names the skipped file and why. The gate reviews the paths
inside `svc/api`, named relative to it, and its own root workflow, listed as
`../../.github/workflows/warden-svc-api.yml` and tiered HIGH whatever
`risk_tiers` says; it posts its own pull request comment. A pull request that
edits a workflow still runs its own copy before any review reads it, so a
push ruleset on `.github/workflows/` ([Installation](Installation.md), *CI
access to the platform*) remains the defence in depth.

[Adopting](Adopting.md) explains each file, for editing what `init` wrote or
for enrolling by hand.

The rest of this page tours `examples/hello-svc`, a complete miniature
consumer — a stdlib HTTP service, a test suite, four rules, a CI job — that
platform CI enrolls on **every PR**.

## 1 · Copy the example out

`hello-svc` is enrolled as a **standalone** repo, so run it as one. Copying it
out is also the honest demo: it is what a consumer does.

```sh
git clone https://github.com/NightWatchEng/nightgate
export NIGHTGATE=$PWD/nightgate                # the platform checkout

cp -r "$NIGHTGATE/examples/hello-svc" .
cd hello-svc && rm -rf .warden/out
git init -q && git add -A && git commit -qm "enroll hello-svc"
```

A real consumer runs the `warden` installed from its `platform.pin` tag
([Installation](Installation.md)). To try the example against the platform
checkout instead, every command below is `uv run --project "$NIGHTGATE" warden …`.

## 2 · Orient

```sh
uv run --project "$NIGHTGATE" warden explain
```

`explain` renders `repo.yaml` as a brief: the components, the risk tiers with
their stated reasons, the protected paths, the verify scopes, and which rules
would fire. It is the first thing an agent session runs, and the fastest way to
see what a repo has actually declared.

## 3 · Run the deterministic gates

```sh
python3 -m hello_svc.app --selfcheck                        # selfcheck: ok (7 checks)
uv run --project "$NIGHTGATE" warden verify --scope app      # PASS + an evidence dir
```

`verify` runs the commands declared under `verify: app:` in `repo.yaml` —
nowhere else. Commands live in the policy file, not in CI YAML, so local and CI
run the identical list. The run writes `.warden/out/<timestamp>-verify/` with
each command's exit status and a manifest binding the commit SHA, the
`rules_version`, and the platform pin.

## 4 · Watch a rule block a real diff

`no-debug-artifacts` is an `engine: declarative` rule — regexes and messages
declared in the rule file's own frontmatter, no Python anywhere. One of its
checks hunts debugger hooks:

```sh
printf '\nbreakpoint()\n' >> hello_svc/app.py
git add -A && git commit -qm wip
uv run --project "$NIGHTGATE" warden review --base HEAD~1 --no-comment
echo "exit=$?"
```

```
| severity | rule | location | finding |
|---|---|---|---|
| HIGH | `no-debug-artifacts` | `hello_svc/app.py:112` | [no-breakpoint] debugger hook left in the diff |
exit=1
```

Undo it:

```sh
git reset --hard HEAD~1
```

Exit codes are the contract, and the third one is the point:

| Exit | Means |
|---|---|
| `0` | clean |
| `1` | a blocking finding, or a failed gate |
| `2` | infra or config error — **the gate did not run** |

A gate that fails open is worse than no gate, because it produces confidence.
`2` is never folded into either verdict.

## 5 · See where you stand

```sh
uv run --project "$NIGHTGATE" warden certify
```

`certify` scores the repo 1–5 on the enrollment ladder (Gated → Verified →
Reviewed → Evidenced → Self-improving) and names the exact check blocking the
next level — here, `LEVEL 3 (Reviewed)`, needing E-03 and E-04 to go further.
`--level N` exits 1 below N, which is what a CI job runs so a repo can never
quietly slip below the bar it claims.

## 6 · Enroll your own repo

`warden init` (top of this page) writes all of it. Its CI job installs the tag
`platform.pin` names. The skill pack runs that `warden` from PATH, refusing
one at another release, so put the pinned release on PATH before you add the
agents; a repo that will run the cage also keeps a `.warden/bin/warden`
launcher ([Installation](Installation.md), *Migrating from the pinned shim*).
By hand, enrollment is two files plus a CI job:

| File | What it is |
|---|---|
| `repo.yaml` | components, risk tiers, verify commands, `platform.pin`, and `repair.budget` (required for Level 3) |
| `.warden/rules/*.md` | the rules that gate a diff |
| a CI job | `warden review --event "$GITHUB_EVENT_PATH"` |

→ **[Adopting](Adopting.md)** walks through each of them, in order, with the
caveats that matter before you trust a green check.

## 7 · Add the agents

The gate works with no agent at all. If you want the agent side, install the
skill pack ([Installation](Installation.md) section 3), write
`.warden/skills-policy.md` ([Skills Policy](Skills-Policy.md)), and state what
you want:

```
/nightgate-skills:ship  add a /metrics endpoint with a smoke test
```

That runs `intake` (requirement → tracked items with an order, reported before
anything is built) then `deliver` per item (plan → build → verify → adversarial
review → gate parity → PR with its evidence chain). You review and merge.

---

Next: [Adopting](Adopting.md) · [Gate Pipeline](Gate-Pipeline.md) ·
[Cost and Throughput](Cost-and-Throughput.md)
