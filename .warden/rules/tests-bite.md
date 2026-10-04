---
id: tests-bite
severity: MEDIUM
engine: claude
judges: behaviour
applies_to: ["tests/**", "warden/**", "cage/**"]
# Candidate records land entirely in tests/ (11 of 11); the tag index adds
# warden 6 — guards in gate code that nothing could fail. cage/** carries no
# candidate record and is included prospectively because its guards are gate
# code by function, the same footing scripts/** has in fail-closed — a guess,
# and said to be one.
# Distinct from tests-required, which asks whether tests EXIST; this asks
# whether they BITE. Merging them would let "a test was added" satisfy "the
# test works".
# Not named `test-cannot-fail`: a rule id that equals a candidate slug
# invalidates every shard filed under `unmapped:<slug>`. The 11 legacy ids
# are grandfathered in .warden/certification.yaml.
covers: [test-cannot-fail]
---
For each added or modified test or guard: would it fail if the behaviour it
names were broken or removed? And is that verdict decided by the commit, not
by the host the suite happens to run on?

This repo's signature defect — 11 judged candidate records, 11 upheld, a tag
history of 46, and seven instances in one day during the v1 build. It
recurs in fresh code written to close the previous instance: six review
rounds watched a guard written to certify X verify a narrower thing than X,
four and three rounds running (#103, finding 3). The worked examples
below were watched live in this repo's rounds; some were judged under
neighbouring classes (tests-required, fail-closed) — they illustrate the
shapes, and the 11/11 precision above was measured over the candidate
records only.

Flag, with the mutation named — say what could change in the subject without
this check noticing:

- **The mocked subject.** A test asserting on a mock, monkeypatch, or
  fixture reimplementation of the unit under test. Watched here: both
  `--online` tests monkeypatched `resolve_source` — the unit itself — away;
  the repair drove the real function with only its collaborator `urlopen`
  scripted, which is the pattern the Do-NOT-flag list below protects.
- **The vacuous assertion.** Comprehensions over collections with no
  non-empty guarantee, so the loop body never runs; needles that are single
  common words; a grep that matches the test's own comment or docstring.
- **The deletable subject.** A coverage or presence guard whose subject can
  be removed wholesale while the suite stays green — proved by deletion:
  five catalog entries came out and nothing noticed.
- **The guard narrower than the claim.** A check that certifies one
  dimension, spelling, directory, or field of a thing that varies in more —
  a glob narrower than the rule it certifies, a re-typed regex beside the
  real one, an anti-widening guard that asked what a pattern looked like
  instead of what it matched, per-field iteration that skips objects lacking
  the field.
- **The host-dependent verdict (hermeticity).** A test or guard whose
  result comes from the machine, the clock or the moment rather than the
  commit: red with nothing broken, or green only on hosts where the defect
  does not show. docs/design/retro-20260915.md §1.2 counts seven escapes of
  this class repaired by PRs merged between 2026-08-29 and 2026-09-15, three
  of them filed inside this rule. Four shapes:
  - *reads the filesystem where the committed tree is meant* — `rglob`,
    `glob("**/…")` or `Path.exists()` over the checkout, which also sees
    leftover worktrees, build output and untracked files that a fresh clone
    lacks (#96→#137, #100→#160). `tests/conftest.py::tracked` lists what
    git tracks (the index) instead of walking the disk. An exact count
    pinned to the live corpus is the same shape: it moves when a shard
    lands, not when the code breaks (#136→#162).
  - *depends on the wall clock* — a date or hour derived twice across a
    rollover, second-resolution stamps compared as if they ordered two
    writes, a needle that matches a digit of today's age (#49→#158,
    #172→#176).
  - *depends on host path resolution or the ambient environment* — macOS
    `/tmp` resolving to `/private/tmp` (#196→#208), a bare `git init` taking
    its branch name from host config, a git call reading `~/.gitconfig` or
    an identity the CI runner lacks, an inherited `GITHUB_OUTPUT`.
  - *depends on process timing* — two events ordered by a sleep margin, or
    a tick budget raced against a subprocess (#48→#208).
  For this shape the mutation to name is the host condition that flips the
  verdict: a date, a leftover worktree, a host config key, a loaded runner.

Do NOT flag: tests that mock collaborators AROUND the unit while the unit
runs real; deliberate characterization tests that say they pin current
behaviour; a narrow test that is one of several jointly covering the
dimensions, when the others are in the tree (verify before flagging). For
hermeticity: a test that reads the live tree or corpus on purpose, derives it
from git, and asserts a property that holds as it grows rather than an exact
count; a time bound that is itself the assertion (`finishes_within`) with
headroom for a loaded runner; a git call made under the suite's
`clean_git_env`, which points the global and system config at /dev/null.
Verify it reaches the call: a fixture that builds a closed env dict for
git replaces os.environ rather than inheriting it, so it must spread
`_AMBIENT_GIT_CONFIG` itself. Know what it does not cover: it sets no
identity, so git still guesses a committer from the host name, and a call
that creates a commit or an annotated tag with no identity from its
repository's config, `-c user.name=… -c user.email=…`, or the
`GIT_AUTHOR_*` and `GIT_COMMITTER_*` variables is still the
ambient-identity shape above.
