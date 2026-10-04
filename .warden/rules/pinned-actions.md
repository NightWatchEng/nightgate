---
id: pinned-actions
severity: HIGH
engine: declarative
# `**/.github/**`, not `.github/**`: glob_match anchors with .match(), so the
# unprefixed form matches ONLY the repo-root directory and left the shipped
# example — examples/hello-svc/.github/workflows/ci.yml, the copy consumers
# take — gated by no rule at all (review round 2). Verified both ways with
# glob_match rather than assumed, which is how the previous scope argument
# went wrong.
#
# Not just workflows: repo.yaml tiers the whole directory HIGH
# ("CI is the enforcement layer"), and a COMPOSITE action at
# .github/actions/x/action.yml runs `uses:` steps under the same job token as
# the gate. A review round found the narrower scope was
# justified by a self-reference argument that does not survive checking:
# `.github/**` matches neither this rule file (.warden/rules/), nor the test
# fixtures (tests/), nor the catalog (warden/guardrails/) — verified with
# warden's own glob_match. The trap is real against `**`; it is not against
# this glob, so the scope was narrow for a reason that was not true.
applies_to: ["**/.github/**"]
implements: ["supply-chain-pinning"]
checks:
  - id: unpinned-action
    # YAML only. Widening applies_to to the whole directory (round 1, F4)
    # handed this BLOCKING rule every prose file under .github/ — a PR or
    # issue template showing a workflow snippet, a CODEOWNERS, a
    # CONTRIBUTING page — and the pattern matches `uses:` anywhere on a
    # line, including inside a comment or a `run:` string. That would block
    # every PR touching a template, which is the false-positive cost the
    # catalog warns takes a gate's credibility (round 2). Composite actions
    # at .github/actions/*/action.yml stay gated, which is what the
    # widening was for.
    globs: ["**/.github/**/*.yml", "**/.github/**/*.yaml"]
    # An action reference whose ref is NOT a 40-hex commit sha.
    #
    # The optional QUOTE is load-bearing: `uses: "actions/checkout@v4"` is
    # legal YAML and a common style, and without it the owner class could not
    # cross the opening quote — so two quote characters were a total bypass of
    # a HIGH blocking rule (review round 1).
    #
    # The optional trailing path segment is load-bearing too: a REUSABLE
    # WORKFLOW is `owner/repo/.github/workflows/x.yml@ref`, and without it the
    # pattern stopped at `owner/repo` and never reached the `@`. That one was
    # caught by this rule's own FIRES case while the body claimed to catch it.
    #
    # A LOCAL action (`uses: ./.github/actions/x`) has no `@` and correctly
    # never matches. A docker ref is handled by the check below, not here.
    pattern: 'uses:\s*["'']?[\w.-]+/[\w.-]+(?:/[^\s@"'']+)?@(?![0-9a-f]{40}(?:["'']|\s|$))\S+'
    message: "action pinned to a mutable ref — pin to a 40-char commit SHA, version in a trailing comment"
  - id: unpinned-docker-image
    # YAML only. Widening applies_to to the whole directory (round 1, F4)
    # handed this BLOCKING rule every prose file under .github/ — a PR or
    # issue template showing a workflow snippet, a CODEOWNERS, a
    # CONTRIBUTING page — and the pattern matches `uses:` anywhere on a
    # line, including inside a comment or a `run:` string. That would block
    # every PR touching a template, which is the false-positive cost the
    # catalog warns takes a gate's credibility (round 2). Composite actions
    # at .github/actions/*/action.yml stay gated, which is what the
    # widening was for.
    globs: ["**/.github/**/*.yml", "**/.github/**/*.yaml"]
    # `uses: docker://image:tag` runs third-party code under the same job
    # token, and a tag is as mutable here as anywhere — the catalog entry this
    # rule implements names "images tagged latest" explicitly. The first
    # version of this rule left docker silent and its body presented that
    # silence as correct scoping rather than a gap (review round 1).
    # A digest ref (`docker://image@sha256:...`) is the pinned form and does
    # not match; a bare `docker://image` with no tag resolves to :latest and
    # does match.
    pattern: 'uses:\s*["'']?docker://(?![^\s"'']+@sha256:[0-9a-f]{64})\S+'
    message: "docker image pinned to a mutable tag — pin to @sha256:<digest>"
---
A tag is mutable. Pinning to one records provenance the upstream can rewrite,
so `@v4` today and `@v4` tomorrow are not required to be the same code
(OWASP Top 10:2025 — A03 Software Supply Chain Failures).

This repo shipped that advice to consumers in its own guardrail catalog while
every one of its own workflow references used a floating tag — the
self-hosting contradiction this rule closed (#100). `.github/**` is HIGH tier in
this repo's `repo.yaml` and described there as "CI is the enforcement layer":
a retagged action runs with the permissions of the job that validates every PR,
including the one that writes attestations.

Adopted at **zero hits** on the tree at adoption. The same change pinned
**12 workflow references** — 10 in `.github/workflows/ci.yml` and 2 in the
shipped example — plus 4 in `docs/wiki/Adopting.md`'s copy-paste snippet, which
this rule cannot see and `tests/test_docs.py` guards instead. The bead
measured 8: `ci.yml` grew two more before this landed, and the example and
the enrollment page were never counted at all, which is itself the argument
for a rule over a one-time sweep.

**HIGH, and blocking, unlike the day-one MEDIUM this repo usually ships.**
Two reasons, and the first is not a preference: the adopting change's acceptance
requires that *"a rule fails the gate when one is added"*, and
`blocking_severities: [HIGH]` means MEDIUM would report without failing —
the rule would not meet the requirement it exists to satisfy. Second, the
usual MEDIUM argument is that a day-one *judgment* rule needs a precision
history before it blocks. This is not judgment: a ref either is 40 hex
characters or it is not. A false positive here is a pattern bug, fixed in the
pattern, not an argument in a finding.

**What this rule does NOT cover, stated so nobody reads it as broader than it
is.** It checks **`uses:` references inside `.github/**`** — actions, reusable
workflows, and docker image steps. The catalog entry it `implements:` is
wider: `supply-chain-pinning` also covers **dependency manifests** —
`pyproject.toml`, lockfiles, `package.json` — and images pinned by tag
anywhere outside a `uses:` step. Nothing here reads those.

It also does not reach `uses:` references **outside a `.github/` directory**
— `docs/wiki/Adopting.md`'s snippet is the live case, guarded by
`tests/test_docs.py::test_adopting_workflow_snippet_carries_no_mutable_action_ref`
rather than by this rule.

That gap matters more than a usual scoping note, because `implements:`
removes the whole entry from `warden rules recommend`: an enforced entry is
never re-reported, so manifest pinning now sits behind an ALREADY ENFORCED
row rather than in the gap list. Partial implementation is the norm here —
`wiki-fidelity` implements `review-lens-documentation` the same way — but the
norm travels with a disclaimer like this one, and the first version of this
rule shipped without it while separately claiming a single known blind spot
(its review round, finding F6). An earlier revision of this paragraph said
the manifest half was recorded as open — stale twice over:
the adopting change was scoped to `uses:` refs only and is merged (eac246d,
PR #100). The package-manager half of a `run:` step is covered by
`pinned-run-invocations` (#158), its own rule with its own backtest;
dependency manifests themselves (`pyproject.toml`, `package.json`,
lockfiles) remain outside both rules, and the catalog entry stays wider
than the pair of us.

**Dismissing a true match.** A reusable workflow called by ref
(`uses: owner/repo/.github/workflows/x.yml@ref`) is matched and should be —
the same mutability applies to a workflow as to an action.

**On pin freshness — the honest counter-argument, answered rather than
ignored.** A SHA nobody updates decays into an unpatched dependency, which is
a real cost of pinning and the reason people avoid it. The trailing `# vX.Y.Z`
comment keeps the pin human-readable so an update is a legible diff rather
than an opaque hash swap — but a legible pin is not a fresh one, and a
documented intention is not a mechanism. The mechanism is
`.github/dependabot.yml` (#106): monthly, grouped into one PR, sha
and trailing comment bumped together; it proposes and never merges, so every
bump still takes the full review round. `tests/test_adopted_rules.py` pins
the config's shape so deleting it un-decides the bead loudly.
