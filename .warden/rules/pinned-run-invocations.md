---
id: pinned-run-invocations
severity: MEDIUM
engine: declarative
# Same scope argument as pinned-actions, verified there with glob_match:
# `**/.github/**`, not `.github/**`, so the shipped example's workflow
# (examples/hello-svc/.github/, the copy consumers take) is gated too.
applies_to: ["**/.github/**"]
implements: ["supply-chain-pinning"]
checks:
  - id: unlocked-uv-run
    # YAML only, for the reason pinned-actions records: handing a rule every
    # prose file under .github/ makes a PR template that QUOTES a workflow
    # snippet a finding, which is the false-positive class that costs a gate
    # its credibility.
    globs: ["**/.github/**/*.yml", "**/.github/**/*.yaml"]
    # `uv run` resolves the project's dependency tree at run time; without
    # --locked it may re-resolve rather than honour the committed uv.lock,
    # so the code CI runs is not the code the lockfile reviewed.
    #
    # The lookahead is CONFINED TO THE COMMAND SEGMENT — the scan stops at
    # ANY of `&`, `|` or `;` (single chars, which also terminate at && and
    # ||) — so `--locked` anywhere within the SAME invocation satisfies it
    # (flag order is a style choice, not a pin), while a later command's
    # --locked cannot vouch for an earlier unlocked one. The first cut
    # scanned the whole line remainder, and `uv run pytest && uv run
    # --locked verify` was SILENT — the unlocked first invocation borrowed
    # the second's pin (review round 1, reproduced against the engine);
    # round 2 found the same borrow surviving across a single `|` or `&`
    # (pipeline/background), so the stop set is the characters, not the
    # two-character operators. Cost, accepted: a redirection like `2>&1`
    # also ends the scan, so --locked must precede it — which is where a
    # flag belongs anyway.
    pattern: '\buv run\b(?![^\n&|;]*--locked)'
    message: "uv run without --locked resolves dependencies at run time — add --locked so CI runs exactly the committed uv.lock"
  - id: unpinned-uvx
    globs: ["**/.github/**/*.yml", "**/.github/**/*.yaml"]
    # uvx fetches and executes a package in one step. The pinned forms are:
    # an ==-exact version, uvx's own `tool@X.Y.Z` request syntax (the
    # lookahead's `@\d` — the first cut flagged uv's most idiomatic pinned
    # spelling as floating, review round 1), or a --from ref carrying a
    # 40-hex commit sha (the Adopting.md shim shape). `@latest`/`@main`
    # start with a letter and correctly stay flagged. Segment-confined for
    # the same borrowed-pin reason as unlocked-uv-run, with the same
    # single-character stop set (round 2: a `|`-pipeline sibling's `==`
    # vouched for a bare uvx under the two-character operators).
    pattern: '\buvx\b(?![^\n&|;]*(?:==|@[0-9a-f]{40}|@\d))'
    message: "uvx without an exact version (==X.Y.Z or tool@X.Y.Z) or a 40-hex sha ref runs whatever resolves today — pin the target"
  - id: unpinned-with
    globs: ["**/.github/**/*.yml", "**/.github/**/*.yaml"]
    # `--with pkg` injects an EXTRA package the lockfile does not govern —
    # --locked on the same line does not pin it (live case: this repo's lint
    # step floated ruff for a month while reading as locked).
    #
    # The tail class kills backtracking: the name must run to a character
    # that is neither a name character nor `=`, so `--with ruff==0.16.5`
    # cannot re-split itself into a shorter name that sneaks past. It also
    # makes the RANGE operators fire: `--with ruff>=0.16`, `--with
    # 'ruff>=0.16'` (quoted) and `--with=ruff` (the equals form) all end the
    # name at `>`, `>` and `\s`/`$` respectively — the first cut's `(\s|$)`
    # tail let every explicitly floating specifier escape exactly like a
    # pinned one (review round 1, reproduced). A `~=`/`<`/`!=`
    # specifier fires the same way.
    #
    # `+`, `:` and `/` are ALSO excluded from the tail (round 2): a value
    # continuing with them is a DIRECT REFERENCE (`git+...`, `https://...`,
    # `./local`), not a registry name, and round 2 caught the tail firing on
    # a sha-pinned git ref — a fix-introduced false positive. This check
    # therefore judges REGISTRY specs only. Residuals, named: a QUOTED
    # ==-pin is silent (correct — it is pinned); a direct reference in the
    # `git+...`/URL/path spellings is not judged at all, floating
    # (`git+...@main`) or pinned — a line regex cannot honestly grade ref
    # pinned-ness, the same refusal the removed pip check records below.
    # The PEP 508 spelling (`--with "pkg @ git+...@sha"`) still FIRES, its
    # leading token being registry-name-shaped (round 3, pre-existing in
    # every cut): for that rare pinned form the recourse is a warden:allow
    # marker, and saying so beats a claim wider than the pattern.
    pattern: '--with[=\s]+["'']?[A-Za-z0-9_.\[\]-]+(?=[^A-Za-z0-9_.\[\]=+:/-]|$)'
    message: "--with without an ==exact version floats an extra package past the lockfile — pin it (--with pkg==X.Y.Z)"
  - id: npx-at-run-time
    globs: ["**/.github/**/*.yml", "**/.github/**/*.yaml"]
    # Any npx in a run: step, pinned-looking or not: `npx pkg@1.2.3` still
    # floats the transitive tree and executes every resolved package's
    # lifecycle scripts under the job token. The pinned form this repo uses
    # is a committed package-lock.json + `npm ci --ignore-scripts` + the
    # local binary (scripts/mermaid/, adopted in #158).
    pattern: '\bnpx\s'
    message: "npx resolves and executes at run time even with a version tag — install from a committed lockfile (npm ci --ignore-scripts) and run the local binary"
---
A `run:` step's package-manager invocation executes third-party code under
the same job token as a `uses:` step — including the job that writes this
repo's attestations — and a floating resolution means the code that runs
tomorrow is not the code that was reviewed today (OWASP Top 10:2025 — A03
Software Supply Chain Failures). `pinned-actions` closes exactly this for
`uses:` refs and deliberately not for `run:` steps: its precision history is
measured on the `uses:` shape, and folding a second pattern family into it
would make that history unreadable (#158, scoped out of #140's
cross-examination). This rule is the separate, separately-backtested other
half.

The live instances that motivated it, all fixed in the adopting change:
`npx -y @mermaid-js/mermaid-cli@11.16.0` in the diagram gate — top-level
version pinned, transitive puppeteer peer (`^23 || ^24 || ^25`) floating,
every resolved package's postinstall executing in CI; `--with ruff` floating
the linter the tests job runs; four `uv run` invocations without `--locked`.

**MEDIUM, non-blocking, unlike pinned-actions' HIGH — deliberately.** These
patterns judge free-form shell inside `run:` strings, not a closed `uses:`
grammar: a comment mentioning `npx`, an unusual-but-pinned spelling, or a
tool whose flag happens to be `--with` are all conceivable, so the rule
reports while it earns a precision history (`blocking_severities: [HIGH]`
means MEDIUM cannot fail a build). It was adopted through `warden autonomy
adopt`, the machine rung of the autonomy ladder, which refuses blocking
severities by design — promotion to HIGH is a human decision with a
precision record behind it.

**What this rule does NOT cover, stated so nobody reads it as broader than
it is.** It reads YAML under `.github/` directories only. A package-manager
invocation inside a shell script that a workflow calls (`scripts/*.sh`) is
invisible to it — the diagram toolchain's pinning is guarded by
`tests/test_docs.py::test_the_diagram_toolchain_is_pinned_by_lockfile`
instead. `pip install` lines are deliberately NOT checked: a first-cut pip
check keyed on `==` appearing anywhere after the command, so
`pip install requests==2.32.3 flask` read as pinned while flask floated
(#158's review round 1) — judging per-package pinning on a multi-package line
is beyond what a line regex can do honestly, and a check that half-sees is
an enforcement claim; if pip ever enters these workflows, that is an
`engine: python` checker's job. Dependency manifests themselves
(`pyproject.toml`, `package.json`, lockfiles) and container tags outside
`uses:` are still out of scope of both this rule and `pinned-actions` — the
`supply-chain-pinning` catalog entry stays wider than the pair of us, per
the disclaimer convention pinned-actions established.

**Backtest over this repo's history (#158, at adoption), reported
with the verdict the tooling gave, not the one that reads best:** replayed
over the last 200 non-merge commits with `warden.backtest`: flagged 1,
true-positive 0, projected false-positive 1 — verdict **REJECTED** under the
revert-coincidence proxy. The one flag is commit 59a4b61,
which added `uv run warden certify --level 4` to ci.yml without `--locked` —
exactly the invocation class this rule refuses, pinned by the adopting
change rather than ever reverted, which is why the proxy cannot count it a
catch. The module's own caveat applies squarely: a defect fixed without a
revert is not counted. Adopted anyway, with that verdict on the record,
because the rule is preventive like pinned-actions (adopted at zero hits):
its value is refusing the next floating invocation, and at MEDIUM its
precision record accrues without blocking anyone. Zero hits on the tree at
adoption — the same change pinned every instance. The review round's
pattern amendments (segment-confined lookaheads, the `@X.Y.Z` uvx form, the
backtrack-killing `--with` tail, the pip check's removal) were re-replayed
over the same window: identical numbers, same single flagged commit.

**Dismissing a true match.** A deliberately unpinned invocation in a
workflow (say, a canary job that MUST test latest) carries a
`# warden:allow(<check-id>): <reason>` marker with a substantive reason on
or above the line — the engine's per-line carve-out — not a rule edit.
