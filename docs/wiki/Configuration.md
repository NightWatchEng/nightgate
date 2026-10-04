# Configuration

Every file a consuming repo writes. All of it is schema-validated and
fails closed: an unknown key or a wrong type is a hard error, never a silent
default. Every YAML read goes through `warden/yamlio.py`: libyaml for speed,
PyYAML's pure-Python parser as the only verdict, so whether a file is accepted
never depends on which parser the install has.

| File | Read by | Reference |
|---|---|---|
| `repo.yaml` | `warden` | this page |
| `.warden/rules/*.md` | `warden review` | [Writing Rules](Writing-Rules.md) |
| `.warden/catalog-answers.yaml` | `warden rules recommend` | [Writing Rules](Writing-Rules.md) |
| `.warden/certification.yaml` | `warden certify` | [Adopting](Adopting.md) |
| `.warden/skills-policy.md` | the skill pack | [Skills Policy](Skills-Policy.md) |
| `cage.toml` | `cage enroll` | this page |
| `graph.yaml` | `warden graph` | [Graph Layer](Graph-Layer.md) |

## `repo.yaml`

Eleven top-level keys. A copy-pasteable complete file is in
[Adopting](Adopting.md).

| Key | Required | What it declares |
|---|---|---|
| `version` | yes | schema version — `1` |
| `repo` | yes | the repo's short name, stamped into every artifact |
| `platform` | yes | `pin:` — the platform release this gate is built on. Must agree with the shim's `PIN_SHA`, and is stamped into every run manifest, so a version bump can never silently change which policy a verdict was judged under |
| `components` | yes | named areas: `{path, lang, description}`. `warden explain` renders them; `plan` maps a task onto them. **Also gate input**: a `verify` scope narrows to a subdirectory only when a component area contains that `cwd`, so adding or removing a component can change which scopes a diff is required to have been verified under. `components` is NOT part of the `review` subtree hashed into `rules_version`, so that change does not move the version stamp — a RULING, not a disclosure (`warden decide list` carries the shard): widening the hash fails certification S-05 for every enrolled consumer at once, because S-05 wants a backtest naming the CURRENT version, and the derivation affects which scopes are REPORTED as required, never an exit code. What keeps the claim honest is `warden declare check` D-02, which proves it by perturbation instead of asserting it here; and `verify`'s `covers:` below is the real narrowing of the gap, since a declared reach does not consult `components` at all |
| `risk_tiers` | yes | ordered `{glob, tier, reason}` — HIGH/MEDIUM/LOW. **First match wins, top to bottom**; anything unmatched is LOW |
| `verify` | yes | scope name → ordered `[{run, cwd?, covers?, runner?}]`. `cwd` defaults to the repo root. **`runner: pytest`** declares that the step runs pytest, and only a step declaring it has its pass/skip/fail counts printed on `warden verify`'s summary line, since the command text is never parsed to guess a runner. **`covers: [glob, ...]` DECLARES what a step exercises**, and a declaration beats the inference below: the scope is required exactly when a changed path matches one of those globs. Use it wherever a command reaches outside its `cwd` — `pytest ../..`, a tool whose config sits at the repo root, a language server that walks up — which the inference silently exempted from every diff outside that `cwd`; `covers: ["**"]` says "the whole tree" out loud. A glob ending in `/` means the SUBTREE (`docs/` reads as `docs/**`), which is the trailing-slash spelling `components:` and `protected_paths` already use for an area; a bare directory NAME is left alone, because it is a legal glob matching one file, and D-01 refuses a `covers` glob that matches no path in your tree rather than guessing what you meant. Absent, the reach is INFERRED exactly as before, byte-identically, and `warden declare check` D-01 reports any step still narrowing that way so the inference is visible rather than assumed. The two arms compose per STEP: a declared step answers for itself and never silences an inferred one beside it. Commands live here, not in CI YAML. **`cwd` also declares the scope's REACH**: the gate derives which scopes a diff must have been verified under from the changed paths, and a scope NARROWS to the subtrees its steps run in only when EVERY step's `cwd` sits inside a declared `components:` area (the component's own path, or an ancestor of that `cwd`); the scope is then required by a diff touching any of those subtrees. Anything else — a root `cwd`, an absolute one, one climbing out through `..`, or a subdirectory inside no declared component — covers the whole tree and is required by every diff. It fails closed on every unknown: a diff whose paths no scope claims at all, an empty range, and a range git cannot resolve each require **every** declared scope. Narrow a scope's `cwd` only when its commands really do exercise nothing outside it |
| `review` | yes | `rules_dir`, `blocking_severities`, `context_excludes` — the keys that decide a verdict, and the only `repo.yaml` subtree hashed into `rules_version` |
| `protected_paths` | no | `{path, reason}` for surfaces that are additive-only or otherwise not free to edit. **Orientation, not enforcement** — `explain` renders it so a session reads the fence; blocking an edit is a rule's job |
| `deploy` | no | same shape as `verify:`. Declares deploy scopes for the back end of the lifecycle; `warden deploy --scope` runs one through the identical runner, behind preconditions |
| `design` | no | `{decision_required: [HIGH], charter?}` — which risk tiers must carry a `warden decide` record before Build. A declaration the skills read, not a mechanical gate |
| `repair` | no, but **required for certify Level 3** | `{budget: N}` — the repair budget: how many counting repair rounds a delivery runs before what is open reverts or is parked. `N` is a whole number from 1 to the platform cap of 2; the schema refuses anything else. `warden certify` rung R-12 reads it and fails when it is undeclared or out of range, and the `deliver` skill reads it. A budget written in `.warden/skills-policy.md` prose gates nothing ([Skills Policy](Skills-Policy.md#the-repair-budget)) |

Absent optional keys are byte-identical to the behaviour before those keys
existed, with one stated exception: an absent `repair` costs certification
Level 3.

### What `rules_version` covers, and what it deliberately does not

Hashed: the rule files, warden's checker code, the findings schema, your
`.warden/checkers/*.py`, and `repo.yaml`'s `review:` subtree, canonicalized.
A rule edit, a `params` change, a checker edit, or a `blocking_severities`
flip all move it.

Not hashed: `components`, `risk_tiers`, `protected_paths`, `verify`, `deploy`,
`design`, `platform`, `repair` — they steer planning and verification, not the review
verdict, and hashing them would churn attestation cohorts on unrelated edits.
Also not hashed: review memory (context, never policy) and the graph
(observation, never enforcement).

This paragraph used to be the only statement of that split, which is how
`components` became a gate input with nothing obliging the prose to change
with it. The split is now a DECLARED inventory in
`warden/declared.py`, and `warden declare check` D-02 proves every entry —
repo.yaml's subtrees by perturbation (change the bytes in a scratch copy and see
whether `rules_version` moves), the package surfaces by set-equality against
what `rules.py` hashes. Drift in either direction is reported: an input that is
hashed while declared not to be, or one declared hashed that silently stopped
being. A property added to `repo.schema.json` with no `hashed` claim is reported
too — that is the commit at which a new gate input arrives.

## `cage.toml`

Lives in the cage directory, **outside the repo worktree**, so a caged session
cannot edit the thing that constrains it. `cage enroll <toml>` renders the
runner, its env file, the permission profile, and — only if a schedule is
declared — a launchd plist. A repo may version the *source* of its toml and
prompt in-tree for review (the platform keeps its own under `.cage/`), but
the copy in the cage directory is the one that binds: enroll renders only
beside its own toml, and a change to the source is a re-copy and a re-enroll.

| Section | Keys | Notes |
|---|---|---|
| `[project]` | `name`, `github`, `live_checkout`, `worktree`, `branch_prefix` | `branch_prefix` defaults to `auto/`. The worktree is where the session works; the live checkout is Write-denied to it |
| `[cage]` | `dir`, `log` | the cage directory and the runner's log |
| `[triggers]` | `schedule` / `manual` / `ci-event` | at least one required. Only `schedule` renders anything (a plist, `hour`/`minute`, default 23:30) |
| `[quiet_hours]` | `enabled`, `start`, `end` | hours a run must **not** happen in, read forward, crossing midnight or not. Defaults to 05:00–23:00; turning it off must be said out loud (`enabled = false`) |
| `[limits]` | `max_open_prs`, `timeout_secs`, `max_resumes` | defaults 2, 7200, 2 |
| `[gate]` | `platform_probe`, `forbidden_paths`, `scrub_paths`, `autonomy_carve_out` | `forbidden_paths` is required — regexes the post-run check tests the diff against. `autonomy_carve_out` defaults to **false**; see below |
| `[profile]` | `extra_allow`, `extra_deny`, `additional_directories` | additions to the caged session's permission profile |
| `[toolchain]` | `path`, `require` | `path` = absolute directories prepended to the runner's fixed `PATH`; `require` = bare command names a run may not start without. Both default empty. Declaring **either** also exports `AGENTOPS_REQUIRE_TOOLCHAIN=1` into the caged session; see below |
| `[[service]]` | provider-specific | renders `services.sh` with preflight/start/stop hooks. Omit and no file is rendered |

Keep `[gate].forbidden_paths` in lockstep with the `## Forbidden paths` section
of `.warden/skills-policy.md`: the runner's post-check closes violating PRs
using this list, and the policy section is the session-side statement of the
same fence.

`[gate].autonomy_carve_out = true` is a **grant, and it is yours to give**. It
lets the runner ask `warden autonomy carve-out` whether a forbidden-path hit is
in fact the narrow autonomy-ladder write — a pause field in an existing rule
file, a new non-blocking rule, the pause or adoption backtest that change
carries under `.warden/memory/backtests/`, or the attestation shard a review
round leaves under `.warden/memory/attest/` — and, if so, leave the PR **open
for a human to merge** instead of closing it. Nothing about merge changes
either way.

It defaults to `false`, so a repo that never grants it keeps its
forbidden-path stop whole. **Write it as a list to pin its own scope**:

```toml
autonomy_carve_out = [".warden/rules/", ".warden/memory/backtests/",
                      ".warden/memory/attest/"]
```

Each entry is a literal repo-relative directory prefix ending in `/`. The
runner then clears a forbidden path only when the platform's carve-out **and**
your list both admit it, so it can only ever narrow — a store a newer platform
grows arrives as a named refusal in the run's report, and you add the line.

A bare `true` is the unscoped reading the key shipped with, kept so existing
configs keep loading: it admits whatever the platform you are pinned to admits,
and that set has widened once — #273 added the attestation shard,
the fourth item above, to a carve-out that had three, so a consumer who granted
`true` against the three-write version and then re-pins gets the fourth with no
new grant to give. Setting it back to `false` restores the whole stop. See
[The Cage](The-Cage.md#the-autonomy-ladder-carve-out) for what the check
verifies and what it deliberately does not.

`[toolchain]` is what the caged `PATH` must carry. The runner resets `PATH` to
a fixed list (`~/.local/bin`, Homebrew, the system directories) so a run
started by launchd behaves like one started by hand — which also means a
toolchain installed anywhere else is invisible inside the cage. Go's installer
writes `/usr/local/go/bin`, so a repo whose tests shell out to `go` declares:

```toml
[toolchain]
path = ["/usr/local/go/bin"]
require = ["uv", "go", "npm"]
```

`path` entries are prepended, in order, before `services.sh` is sourced; `~`
expands at enroll time. Each name in `require` is resolved with `command -v` at
pre-flight, on the machine that will run, and a missing one **skips the run and
names it** — before a bead is claimed, rather than an hour later when the
project's own verify scope fails at the ship gate.

Declaring the block does a **second** thing, which is not about `PATH` at all:
the runner exports `AGENTOPS_REQUIRE_TOOLCHAIN=1` into the session whenever
either key carries anything. A project's suite has two
honest answers to a tool it shells out to being absent, and which one is
correct is decided by who contracted for the binary — so the switch is read
by the suite, not by warden. In this platform's own suite (`tests/toolchain.py`)
it selects **strict**: an absent declared tool is a named FAILURE, because the
coverage lost is coverage the environment promised. Unset — a contributor's
laptop, which promised nothing — it is **lenient**: a named SKIP carrying the
install command. A cage with no `[toolchain]` block declared nothing and keeps
the lenient half, so the strictness follows the promise. See
[The Cage](The-Cage.md#what-the-caged-path-carries).

```toml
[project]
name = "hello-svc"
github = "acme/hello-svc"
live_checkout = "/Users/me/workspace/hello-svc"
worktree = "/Users/me/workspace/hello-svc-cage"

[cage]
dir = "/Users/me/workspace/hello-svc-cage-dir"
log = "/Users/me/Library/Logs/hello-svc-cage.log"

[gate]
platform_probe = ".warden/bin/warden --help"
forbidden_paths = ['\.warden/', 'repo\.yaml$', '\.github/']

[triggers.manual]
```

Validate before enrolling:

```sh
cage validate cage.toml
cage enroll cage.toml
```

`prompt.md` stays **hand-authored** and `cage enroll` never rewrites it. Its
absence is a pre-flight stop.

---

Next: [Writing Rules](Writing-Rules.md) · [The Cage](The-Cage.md) ·
[CLI Reference](CLI-Reference.md)
