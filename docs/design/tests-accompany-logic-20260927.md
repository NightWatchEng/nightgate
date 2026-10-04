# `tests-accompany-logic`, replayed — 2026-09-27

The 2026-09-15 retro (`retro-20260915.md`, §2.4 and Proposal 8) kept
`tests-accompany-logic` on its RETIRE? list with the firing column
UNMEASURED: `warden rules lifecycle` does not replay a python rule, so its
silence could mean the checker was never violated or that it was inert. The
proposal asked for the checker to be replayed over history before a third
retro reads that silence. This file is that replay. It changes no rule, no
checker and no config; the keep, pause or rewrite call in §4 is a proposal
for the retro to read.

**Result: the checker flagged 0 of 233 non-merge commits touching
`warden/**`, and 0 of 181 first-parent diffs on main, 178 of them pull
requests.** A control run with the
companion condition disabled flags 180 of the same 233 commits, so the
logic detector is not inert: every commit that added logic under `warden/`
also changed a path under `tests/`.

## 0 · Method

Run on main at `29e5d1ae` (#339), in a fresh worktree, with the in-tree
launcher (`uv run`). The checker, the rule file and its params are the ones
at that head. None of them has changed since 2026-08-28: the last commit to
touch either the checker in `warden/mechanical.py` or
`.warden/rules/tests-accompany-logic.md` is `17408ee7`, on that day. The rule
reached main in #125 (`f854e5ac`, 2026-08-28).

The replay uses the gate's own code path, not a second implementation of it:

- `warden.diffs.get_context(root, base, head, context_excludes)` builds the
  diff the gate builds, with `repo.yaml`'s `context_excludes` (which hide
  `tests/**` from the scanned set).
- `warden.rules.applicable` selects the files the rule's `applies_to` admits,
  as `run_review` does.
- `warden.mechanical.check_tests_accompany_logic` runs with the rule's own
  params (`companion_prefix: "tests/"`, `exempt_globs: []`).

Two units were replayed, both limited to history that touches `warden/`:

- **C, per commit.** Every non-merge commit reachable from `29e5d1ae`
  whose diff touches `warden/`, each against its first parent. This is
  the unit the bead's acceptance names and the unit `rules lifecycle`
  counts.
- **P, per first-parent diff.** Every first-parent commit up to
  `29e5d1ae` whose diff touches `warden/`, against its first parent. A
  squash merge and a merge commit both give the whole PR's diff this way,
  which is the range the CI gate (`warden review --base origin/<base>`)
  reads. 178 of the 181 subjects carry a `(#N)` suffix or read `Merge pull
  request #N`; the other three (`d656392c`, `e96c2603` and their revert
  `d3aea50a`, all before #125) were pushed directly.

**Control.** The same replay was run a second time with
`companion_prefix` replaced by a prefix no path carries (`__control__/`).
With the companion condition unsatisfiable, the checker reports every diff
whose added lines under `warden/` start a `def`, `if`, `elif`, `for`,
`while`, `with`, `try` or `except` statement. That count is how many diffs
the rule was in a position to fire on.

The corpus side was read with `warden memory ingest` (0 new shards, 3320
records, 856 restatements folded to 2464 findings), then `warden memory
stats` and `warden rules lifecycle`, and a search of
`.warden/memory/attest/` for the rule id.

The script, run from the worktree root as `uv run python replay.py`; the
control is the same file with the `rule.params` argument replaced by
`{**rule.params, "companion_prefix": "__control__/"}`:

```python
import subprocess
from pathlib import Path
from warden import config as cfg_mod, rules as rules_mod, diffs, mechanical

root = Path.cwd()
cfg = cfg_mod.load(root)
rules = rules_mod.load_rules(root / cfg.review.rules_dir)
rule = next(r for r in rules if r.id == "tests-accompany-logic")
exc = cfg.review.context_excludes

def git(*a):
    return subprocess.run(["git", *a], cwd=root, capture_output=True,
                          text=True, check=True).stdout

def replay(unit, sha, base):
    ctx = diffs.get_context(root, base, sha, exc)
    files = rules_mod.applicable((rule,), list(ctx.files)).get(rule.id, [])
    if not files:
        return
    fs = mechanical.check_tests_accompany_logic(ctx, rule.params, files)
    tests = any(f.startswith("tests/") for f in ctx.all_files)
    print(unit, sha[:10], len(fs), int(tests), sep="\t")

for unit, args in (("C", ("--no-merges",)), ("P", ("--first-parent",))):
    for sha in git("log", *args, "--format=%H", "29e5d1ae", "--", "warden/").split():
        parents = git("rev-list", "--parents", "-n1", sha).split()[1:]
        if parents:
            replay(unit, sha, parents[0])
```

## 1 · The replay

| window | selected | flagged | control: adds logic under `warden/` | no `tests/` change |
|---|---:|---:|---:|---:|
| C: every non-merge commit touching `warden/` (2026-08-21 to 2026-09-27) | 233 | **0** | 180 | 9 |
| C: the last 200 of those (oldest `e837856e15`, 2026-08-24) | 200 | **0** | 157 | — |
| C: `rules lifecycle`'s window, the last 200 non-merge commits of any kind (oldest `a7c2623a0f`, 2026-08-28) | 119 | **0** | 96 | — |
| P: every first-parent diff touching `warden/` | 181 | **0** | 142 | 8 |
| P: first-parent diffs since the rule landed (#125 and every later one) | 123 | **0** | 98 | 2 |

`selected` is a diff with at least one file the rule's `applies_to` admits.
The lifecycle window's 119 matches the `selected 119` that `warden rules
lifecycle` prints for this rule at `29e5d1ae`; the retro's 108 was the same
window at `bbca974`.

The 9 commits that touched `warden/` without touching `tests/` add no line
the detector reads as logic, which is why the control does not flag them
either:

| commit | subject |
|---|---|
| `26bb17e915` | docs(repo): three prose claims that outran the code (#285) |
| `a900e56810` | ops(repo): release v1.1.0 (#164) |
| `90346497c3` | ops(repo): release v1.0.0 |
| `f830f52026` | refactor(warden): drop the unreachable cage: branch in certify discovery |
| `75a46148e4` | ops(repo): warden v0.6.1 — R-09 parses list items |
| `06e4c64aa5` | ops(repo): warden v0.6.0 — R-09, parseable graph render |
| `1ff20053e4` | ops(repo): warden v0.5.0 — craft layer, memory-cache guard, level 5 |
| `8108a6ac50` | ops(repo): warden v0.4.0 — release, --version probe, hardened shim template |
| `1ebb076b21` | docs(repo): tracker IDs out of reader-facing content and code |

Eight are version bumps or prose, and `f830f52026` removes a branch rather
than adding one. At the PR level the same set is 8 diffs; the ninth commit
reached main inside a PR that also changed `tests/`.

## 2 · The corpus

Two shards in `.warden/memory/attest/` carry a record with `rule_id:
tests-accompany-logic`. Both belong to PR #292 and state one finding: two
records, which the ingest folds to a single finding:

| shard | round | lens | status | file |
|---|---:|---|---|---|
| `20260922T191759Z-d9988f8d-c619fc0c.json` | 2 | scoped-re-reviewer | confirmed | `tests/test_proportion.py` |
| `20260922T191925Z-658f96b0-de399f69.json` | 3 | closure-attestor | fixed | `tests/test_proportion.py` |

This is the `judged 1 (upheld 1)` that `rules lifecycle` and `memory stats`
(`n=1 fixed=1`) print for the rule. It is a reviewer's filing, not a checker
firing:

- The file it names is under `tests/`, which the rule's `applies_to`
  (`warden/**`) does not select.
- Its text is that three of round 1's repairs shipped with no regression
  test. Whether a repair carries a test that names it is the half the rule's
  body assigns to `tests-required` ("a checker cannot decide").

So the corpus holds no firing of the checker. The lifecycle row's
`gate detections 0` agrees with the replay's 0.

## 3 · What the replay does and does not show

It shows the checker is not inert. The detector recognises added logic in
180 of 233 commits, and the companion test (`any changed path under
tests/`) is what cleared every one of them. No commit in the window added
logic under `warden/` without a `tests/` change.

It does not separate deterrence from habit. A rule that is never violated
reads the same whether authors write tests because the rule reports, or
because the Definition of Done and the review crew already require it; no
count here tells those apart. It also measures only the condition the
checker states: a diff that edits any file under `tests/` clears it, whether
or not that edit covers the added logic. The 180 control hits all cleared
through that short-circuit, so the replay says nothing about coverage.

## 4 · Recommendation for the retro

**Keep, unchanged.** The row can move from UNMEASURED to measured: flagged
0 of 233 commits, 0 of 181 first-parent diffs, with 180 and 142 diffs
where the rule could have fired. Silence is now a measured outcome of a
working detector, not an absence of measurement.

- **Not pause.** The rule costs $0 per PR and reports at MEDIUM without
  blocking, so a pause saves nothing. It declares no `implements:` or
  `covers:`, so a pause would reopen no catalog entry either; it would
  only stop the report.
- **Not rewrite on this evidence.** A tighter companion condition (for
  example, a test file whose name matches the changed module) is a
  different rule with its own precision to earn, and nothing in this replay
  argues for it: there is no false positive to fix and no miss the current
  condition is shown to allow.

Two follow-ups a retro may choose to propose. Neither is made here, because
each is a change to code or to committed records outside this record's
scope:

1. `warden rules lifecycle` still prints `firing UNMEASURED` for this rule,
   because `warden/lifecycle.py`'s `_replay` returns early for every python
   rule. Replaying python checkers through `diffs.get_context`, as §0 does,
   would make the command print this count itself on the next run. That is
   a code change to `warden/lifecycle.py`.
2. The one finding filed under this rule id (§2) belongs to
   `tests-required`'s judgment half. Left as it is, it keeps giving the
   mechanical rule a precision row built from a finding the checker could
   not have produced. Re-attributing it is a corpus correction for the
   retro to decide; the shards are not edited here.
