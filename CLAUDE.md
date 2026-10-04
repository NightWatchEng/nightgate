# Nightgate

Cross-project agent-development platform. Components: **warden** (deterministic
PR gate CLI: `repo.yaml` policy in, gated review + evidence artifacts out),
**cage** (unattended run cage), **skills** (shared skill pack),
**memory** (review memory), **graph** (declared agent-organization layer).
Built once here, consumed by every project via config — never forked.
Live consumer: [shortfall](https://github.com/NightWatchEng/shortfall) — a Go
multi-module repo (`go.work`, nested adapter modules) enrolled on a pinned
platform SHA.

## Working agreements

- Every change lands via branch → PR. No direct pushes to main. The founder
  merges by default; an agent merges only under an explicit, current grant
  from the founder, which is revocable and never self-issued.
- This repo gates itself with its own warden (self-hosting is the credibility
  test — the platform must survive its own gate), and it declares its own
  cage: `.cage/` is the versioned source. Installing it beside
  the checkout is the founder's three commands in the toml's header, and
  `warden certify` earns N-01..N-03 there only once they have run.
  Two unattended runs have completed here: 20260920-205434
  built and reviewed a fix and stopped at a forbidden path for committing
  the attestation its review protocol required;
  20260921-070026 built and reviewed a fix, opened PR #279 and left it for a
  human, and the founder squash-merged it as 8cf3c1b. Seven earlier runs were
  on a consumer checkout and none opened a PR.
- **Beads**: task tracking uses `bd`, run from THIS repo — the namespace here is
  `agentops-*`, not `workspace-*`. In a fresh clone, `bd bootstrap --yes` comes
  first: it clones the tracker's history from `refs/dolt/data` on the git remote
  (CONTRIBUTING.md, Reading the tracker). `bd prime` for workflow; `bd ready` /
  `bd show` / `bd update --claim` / `bd close`. No TodoWrite or markdown TODO lists.

The sequence a change lands by (bead, branch, deliver or its manual steps,
PR) is CONTRIBUTING.md, *Landing a change here*.

## Definition of Done (every task, no exceptions)

1. **Tests**: new logic carries unit tests; bugs found live or in review get a
   *named* regression test. Suite green locally: `uv run pytest`.
2. **Review**: any non-trivial diff gets an adversarial delta review
   (independent-context reviewers on the diff range) before or immediately after
   landing; findings triaged into beads. No self-graded merges of substantial work.
3. **CI green** on the pushed commit. A red main is a stop-the-line event.
4. **Evidence in the bead**: `bd close` reasons cite the proof (test names,
   run output) — never just "done".
5. **Local gate**: `git config core.hooksPath .githooks` once per clone; the
   pre-push hook runs ruff (F,E9) + pytest, unless a clean `warden verify
   --scope tests` PASS on the exact pushed commit is already on disk, which
   it names and accepts. Do not bypass with --no-verify.

## Commit structure (every commit, enforced)

```
type(scope): summary <=100 chars (agentops-id)           # or (no-bead: reason)

Why: 1-3 sentences — the problem, finding, or founder ask.

- what changed, one bullet per meaningful change

Evidence: N tests passed; review round ran               # REQUIRED feat/fix
```

The forge appends ` (#N)` when a PR is squash-merged; the validator strips
that first, so the 100 is the budget for what you write. It counts
characters, not bytes — an em dash costs 1.

types: feat fix test docs ops tooling refactor · scopes: warden cage skills
memory graph examples ci docs repo. Validated by `scripts/commit-lint.sh` —
one validator, two callers: `.githooks/commit-msg` (advisory) and CI (fence).
Merge/Revert/Reapply/fixup/squash exempt.

## Principles (day one, non-negotiable)

- **Policy → enforcement → evidence → provenance**: every rule is declared in
  config, enforced by something that can auto-reject, and leaves an artifact.
- **$0 standing infrastructure**: no always-on servers; everything runs in CI,
  cron, or a laptop.
- **No paid ingestion APIs**: the platform reads git, GitHub, and local files.
- **Hard stops live outside the model**: budgets, gates, and kill-switches are
  enforced by code the agent cannot rewrite mid-run.
- **Portability is a standing CI check**, not a claim — consumer configs must
  keep validating against the shipped schemas. This repo's CI enrolls
  `examples/hello-svc` on every PR, and `scripts/portability-sim.sh` proves
  the gate bites on it; the live consumer, **shortfall**, runs the pinned
  platform against its own config in its own CI. What shortfall adds is the
  language — it is Go, this platform is Python — so its config carrying
  across is evidence the layer generalizes, not a restatement. Not the whole
  claim: it certified LEVEL 3 (Reviewed) on 2026-09-01, blocked from Level 4
  by E-02, and as of that date nobody outside this org had enrolled from the
  docs alone.

## Build & test

```bash
uv run pytest -q -n auto                                # test suite — what the gate runs
uv run pytest -q                                        # same suite single-process
uv run --locked --with ruff==0.16.5 ruff check warden cage tests --select F,E9  # lint gate
```

`-n auto` is what the gate runs, here and in CI: `repo.yaml`'s `tests` scope,
the CI interpreter legs and `.githooks/pre-push` all carry it, and the flag
moves in all of them or in none. Same tests, same pass count; the saving is
not one number, because the two machines are not alike. On this laptop it is
about a third of the wall clock (634s serial against 144-196s). On a GitHub
runner, measured on this repo's own runs, it is about seven tenths — the
runner has far fewer cores to spread across, and the honest figure is the one
that came off the runner.

It is deliberately NOT in `addopts`, and not in `PYTEST_ADDOPTS` either, so a
bare `uv run pytest` — a contributor's, an enrolled consumer's, the shipped
example's — is still single-process and nothing the suite shells out to
inherits the flag. Parallelism is written in the invocation, where whoever
reads the command can see it.

A test that passes serially and fails under `-n auto` is an isolation bug, not
a reason to drop the flag.
