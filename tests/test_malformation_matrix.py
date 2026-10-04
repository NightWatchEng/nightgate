"""THE MALFORMATION MATRIX — one defect class, enumerated by execution.

The class: *a warden command meets input it did not expect and either dies
with a traceback or reports the wrong cause.* An enumeration of it
maintained BY HAND cannot close it: a hand-driven probe is right only about
the commands it happens to drive — a spelling harmless through `memory stats`
crashes through `memory recall`, and one needing two records in a shard is
invisible to a one-record probe.

This harness derives the count rather than trusting a tally. It drives more
commands and more fields than a hand-driven probe, and seeds the DERIVED
cache as well as the shards.

So NEITHER AXIS IS HAND-TYPED here:

* **Axis one — the malformable surface.** The shard record fields come from
  the dict literal in `memory.build_records`, the sole writer of shard
  records, read at test time out of its own AST. The envelope keys come the
  same way from `memory.ingest`'s `envelope = {...}` and
  `memory.build_review_event`. Add a field to the producer and the matrix
  mutates it on the next run, with no edit here.
* **Axis two — the commands.** Every leaf of `cli.build_parser()`'s own
  subparser table, positional `choices` expanded. Each leaf is either DRIVEN
  (a recipe below) or EXCLUDED with a written reason, and
  `test_every_cli_leaf_is_classified` fails on a leaf that is neither — so a
  subcommand added tomorrow, or a new reader inside an existing one, cannot
  slip through by being forgotten. A matrix that silently skips a command is
  the same defect one level up.

What it asserts, in three tiers:

1. **No traceback, ever** — `Traceback (most recent call last)` must never
   reach stderr, for any cell. Exit 1 and exit 2 are both fine: a refusal is
   the point, a crash is the defect.
2. **The refusal NAMES the subject** — for the readers declared
   `refuses=True`, the diagnostic must name the shard FILE and the FIELD at
   fault, not a generic complaint and not the wrong subject (a broken
   ruleset that blames the run dir, or a renderer that labels every
   vocabulary complaint `CEILING UNREADABLE`).
3. **The opposite direction** — `test_the_live_corpus_still_loads` reads this
   repo's own committed shards through the shared validator and every driven
   command. Making a reader stricter must not start refusing input the corpus
   legitimately contains, and that is measured here rather than asserted.

STATED REACH — what this matrix does NOT cover, so its claim is bounded
rather than implied complete:

* multi-record and multi-shard INTERACTIONS — one malformed record in one
  shard per cell;
* values of the right TYPE and the wrong FORMAT — a `ts` that is a string but
  not a timestamp, a `sha` of the wrong length;
* malformations nested BELOW one field (a `reviewers` list whose elements are
  malformed);
* consumers that call warden as a LIBRARY rather than through the CLI,
  including `cage/`;
* `warden attest check` — its reader lives in `warden/attest.py`, outside
  this matrix;
* `repo.yaml`'s own blocks beyond the top-level-type sweep in
  `test_a_malformed_repo_yaml_block_is_refused_by_name`: the per-key contract
  is `warden/schemas/repo.schema.json`'s, enforced by `config_mod` and
  covered by `tests/test_config.py`;
* nothing about the DERIVED cache (`.warden/memory/findings.jsonl`) is
  excluded: every record cell seeds the cache with the same malformed record
  as the shard, because `memory stats`, `memory recall` and `warden plan`
  read there and not from the shards.

FALSIFIER: if a single-field malformation of one committed shard, reachable
through a warden CLI subcommand, turns up while this matrix is green, the
matrix is the wrong mechanism and the design needs replacing rather than
extending.
"""

import ast
import contextlib
import inspect
import io
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from warden import cli
from warden import memory as memory_mod
from private_evidence import needs_corpus

REPO = Path(__file__).resolve().parent.parent
SHARD = "20260901T000000Z-aaaaaaaa-bbbbbbbb.json"
TRACEBACK = "Traceback (most recent call last)"

# The five shapes a single field can take when it is wrong. `absent` is the
# one a type check alone cannot see.
MUTATIONS = ("list", "dict", "int", "null", "absent")
_MUTANTS = {"list": ["x"], "dict": {"k": "v"}, "int": 7, "null": None}


# ── axis one: the malformable surface, from the producer's own AST ─────────

def _dict_literal_keys(fn, varname: str) -> set[str]:
    """Every key the function writes into `varname`, read off its AST.

    Three shapes, because the producers use all three: the dict LITERAL
    (`record = {...}`, including dicts nested in a `**{...}` spread), a
    constant SUBSCRIPT store (`record["id"] = ...`), and a subscript store
    through a loop variable bound to a tuple of constants (`for field in
    ("pr", "bead"): record[field] = ...`). Anything else is not derived and
    would be invisible, which is why `test_the_derived_surface_is_plausible`
    holds a floor under the count.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == varname:
                    for sub in ast.walk(node.value):
                        if isinstance(sub, ast.Dict):
                            keys |= {k.value for k in sub.keys
                                     if isinstance(k, ast.Constant)}
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == varname):
                    if isinstance(target.slice, ast.Constant):
                        keys.add(target.slice.value)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) \
                and isinstance(node.iter, ast.Tuple):
            names = {c.value for c in node.iter.elts
                     if isinstance(c, ast.Constant) and isinstance(c.value, str)}
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Subscript)
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id == varname
                        and isinstance(sub.slice, ast.Name)
                        and sub.slice.id == node.target.id):
                    keys |= names
    return keys


RECORD_FIELDS = sorted(_dict_literal_keys(memory_mod.build_records, "record"))
ENVELOPE_KEYS = sorted(
    _dict_literal_keys(memory_mod.ingest, "envelope")
    | _dict_literal_keys(memory_mod.build_review_event, "event"))


# ── axis two: the commands, from the CLI's own subparser table ─────────────

def _cli_leaves(parser, prefix: tuple = ()) -> list[tuple]:
    import argparse
    subs = [a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)]
    if subs:
        return [leaf for a in subs for name, sp in a.choices.items()
                for leaf in _cli_leaves(sp, prefix + (name,))]
    # A positional with `choices` is a subcommand wearing a different hat —
    # `memory ingest` and `memory recall` are different readers of different
    # files and must be separate cells of the matrix.
    pos = [a for a in parser._actions if not a.option_strings and a.choices]
    if pos:
        return [prefix + (c,) for c in pos[0].choices]
    return [prefix]


CLI_LEAVES = sorted(_cli_leaves(cli.build_parser()))


class Driven:
    """A leaf the matrix actually runs, with the argv that runs it.

    Two independent tier-2 obligations, because the commands genuinely differ
    and one flag conflated them:

    * `names` — when `RECORD_FIELD_CONTRACT` refuses the shape, this command's
      output must NAME the file at fault and the FIELD at fault. That is the
      whole point of the second tier: a generic complaint, or one naming the
      wrong subject, is the defect this tier exists to catch.
    * `exits` — and it must exit non-zero. `warden certify` deliberately does
      not: it renders a badge and reports the refusal in the rung's detail,
      and `--level N` is the form that gates. Demanding a non-zero exit of it
      would be demanding a different command.

    A command that reads the corpus only to ENRICH a report carries neither
    (`rules recommend` says `corpus unread` and recommends on prior art;
    `plan` degrades to no priors). Those are documented degradations, and the
    matrix still holds them to tier 1.
    """

    def __init__(self, argv, *, names: bool, exits: bool, why: str = ""):
        self.argv, self.names, self.exits, self.why = argv, names, exits, why


class Excluded:
    def __init__(self, why: str):
        self.why = why


# EVERY leaf of the table above appears here exactly once. Adding a
# subcommand to `cli.build_parser()` reddens
# `test_every_cli_leaf_is_classified` until it is classified here.
COVERAGE: dict[tuple, object] = {
    # ---- driven: readers of the committed shards -------------------------
    ("certify",): Driven(
        ["certify"], names=True, exits=False,
        why="renders the ladder and reports the refusal in the rung detail; "
            "`certify --level N` is the gating form, so a non-zero exit is "
            "not this command's contract"),
    ("memory", "ingest"): Driven(["memory", "ingest"], names=True, exits=True),
    ("memory", "check-vocabulary"): Driven(["memory", "check-vocabulary"],
                                           names=True, exits=True),
    ("memory", "watch"): Driven(["memory", "watch"], names=True, exits=True),
    ("autonomy", "pause"): Driven(["autonomy", "pause", "--dry-run"],
                                  names=True, exits=True),
    ("rules", "recommend"): Driven(
        ["rules", "recommend"], names=False, exits=False,
        why="reports `corpus unread` and recommends on prior art instead — a "
            "degradation it names, not a refusal (cli.py _cmd_rules)"),
    ("rules", "lifecycle"): Driven(
        ["rules", "lifecycle"], names=False, exits=False,
        why="same degradation seam as `recommend`"),
    # ---- driven: readers of the DERIVED cache the shards feed -------------
    ("memory", "stats"): Driven(["memory", "stats"], names=True, exits=True),
    ("memory", "recall"): Driven(
        ["memory", "recall", "--files", "app/x.py", "--for", "reviewer"],
        names=True, exits=True),
    ("plan",): Driven(
        ["plan", "--task", "probe", "--area", "app"], names=True, exits=False,
        why="priors are advisory: `plan` records the reason in its Caveats "
            "block and plans anyway, so it names the file and the field "
            "without refusing. `--area app` is load-bearing — `plan` calls "
            "`recall` only when the area's file hints yield paths, so the "
            "first cut of this cell passed without reaching a record reader "
            "at all and a live `KeyError: 'finding'` in plan.py stayed green "
            "(round-1 code-reviewer, CO-02)"),
    # ---- excluded, each with its reason -----------------------------------
    ("init",): Excluded(
        "writes a fresh enrollment from the manifests in the working "
        "directory; reads no shard, artifact, corpus or cage. Its refusal and "
        "detection contract is pinned by tests/test_init.py"),
    ("explain",): Excluded(
        "renders repo.yaml only; reads no shard, no artifact, no cage. Its "
        "config surface is swept by test_a_malformed_repo_yaml_block_*"),
    ("verify",): Excluded(
        "runs the declared verify scope's shell commands; reads no corpus"),
    ("take",): Excluded(
        "copies verify results from a downloaded artifact directory into a "
        "fresh .warden/out; reads no shard, corpus or cage. Its refusals are "
        "pinned by tests/test_init.py"),
    ("deploy",): Excluded(
        "reads attest SHAS via warden/attest.py (the attest reader's surface) "
        "and requires a clean checkout at a reachable "
        "commit — not constructible per-cell here"),
    ("review",): Excluded(
        "runs the rules gate on a diff; reads the ruleset and the diff, not "
        "the corpus. The ruleset surface is covered by the named ruleset "
        "cases below"),
    ("diff",): Excluded("writes a review package from git; reads no corpus"),
    ("round", "new"): Excluded("mints a round dir from git; reads no corpus"),
    ("round", "count"): Excluded(
        "reads the shard ENVELOPE (`reviewers[].round`, `closure_"
        "verification`) out of GIT OBJECTS — each shard at the commit that "
        "ADDED it in `base..head` — never the working tree, the shard store "
        "on disk or the derived cache. This fixture's repository has no "
        "commits, so a shard seeded here is invisible to it and every cell "
        "would answer the same exit 2 about the range, asserting nothing "
        "about the malformation. Its malformed-shard contract IS exit 2 and "
        "is pinned by name in tests/test_repair_stop.py (every roster and "
        "`round` shape) and tests/test_round_count_gate.py (through the CLI)"),
    ("round", "classify"): Excluded(
        "reads round manifests and a findings payload, not the corpus; its "
        "own malformed-input contract is exit 2, pinned by "
        "tests/test_repair_stop.py"),
    ("attest", "write"): Excluded(
        "warden/attest.py is a sibling unit's file this round; the write-side "
        "artifact surface is covered by the malformed-artifact cases below"),
    ("attest", "show"): Excluded("renders the latest run artifact, not the corpus"),
    ("attest", "classify"): Excluded(
        "derives the review tier from the two git trees in the range; it "
        "reads no corpus and writes nothing"),
    ("attest", "check"): Excluded(
        "out of this matrix's reach; its reader is warden/attest.py, covered "
        "by the attest tests"),
    ("decide", "record"): Excluded("writes a decision; reads no corpus"),
    ("decide", "show"): Excluded("renders the decision store, not the corpus"),
    ("decide", "list"): Excluded("renders the decision store, not the corpus"),
    ("declare", "check"): Excluded(
        "compares each declaration to the behaviour it governs — the ruleset, "
        "repo.yaml and the risk tiers, in a scratch copy it perturbs. Reads no "
        "shard and no cache; its own cannot-evaluate contract (exit 2) is "
        "pinned by tests/test_declare_check.py"),
    ("audit",): Excluded(
        "re-renders the sticky comment from the latest review/attest run "
        "artifacts; reads no shard"),
    ("autonomy", "adopt"): Excluded(
        "writes a rule file from --source; reads no corpus"),
    ("autonomy", "carve-out"): Excluded(
        "judges a path list against the ladder; reads no corpus"),
    ("mine",): Excluded("reads git history and the GitHub API, not the corpus"),
    ("catalog", "list"): Excluded("renders the shipped catalog"),
    ("catalog", "show"): Excluded("renders one shipped catalog entry"),
    ("catalog", "check"): Excluded("validates the shipped catalog's own shape"),
    ("graph", "validate"): Excluded("reads graph.yaml only"),
    ("graph", "render"): Excluded("reads graph.yaml only"),
    ("graph", "crew"): Excluded(
        "reads graph.yaml, repo.yaml and the rules dir's file names; its "
        "fail-closed causes are pinned by tests/test_graph_review.py"),
    ("graph", "authority"): Excluded(
        "reads the same three and no corpus. It reads no live merge grant "
        "either — there is none to read, by design — so its answer is a "
        "constant a malformed corpus cannot move; its fail-closed causes are "
        "pinned by tests/test_graph_delegation.py"),
    ("skills", "preflight"): Excluded("reads the skill pack only"),
    ("skills", "pin"): Excluded(
        "reads the installer's plugin records under the Claude config dir and "
        "repo.yaml's platform.pin; no corpus. Its own contract is pinned by "
        "tests/test_packpin.py"),
    ("ship",): Excluded(
        "runs the ship tail: it reads verify-result artifacts, the committed "
        "attest SHAS through warden/attest.py, and git — never a shard record "
        "and never the derived cache. Its own cannot-evaluate contract (exit "
        "2) is pinned by tests/test_ship.py"),
    ("progress", "show"): Excluded(
        "reads the gitignored boundary log under .warden/out/ and git; reads "
        "no corpus. Its own malformed-input contract — an unreadable log is "
        "UNREADABLE and never an unreached unit — is pinned by "
        "tests/test_progress.py"),
    ("progress", "record"): Excluded(
        "appends one boundary to that log; reads no corpus"),
}

DRIVEN = {leaf: spec for leaf, spec in COVERAGE.items()
          if isinstance(spec, Driven)}
NAMERS = {leaf: spec for leaf, spec in DRIVEN.items() if spec.names}


# ── the FIVE hand-declared knobs, named independently ─────────────────────
#
# CLI_LEAVES, RECORD_FIELDS and ENVELOPE_KEYS are genuinely derived from the
# code, and the module docstring above rests on that. Five things beside them
# are not, and each is a declaration wearing a derivation's clothes:
#
#   MUTATIONS          hand-typed — the shapes axis
#   `Driven`/`Excluded` a hand-written constructor per leaf — which covered
#                      leaves the matrix actually drives
#   `names=`           a hand-declared flag per row — which driven leaves must
#                      NAME the shard and the field at fault
#   `exits=`           a hand-declared flag per row — which must exit non-zero
#   `argv`             a hand-typed argument list per row — WHICH COMMAND the
#                      row actually runs
#
# Every assertion over the first four ITERATES them, so deleting a member or
# flipping a flag would narrow the axis and its assertions together and
# nothing would redden; the fifth is not iterated at all, so a row could be
# repointed at another leaf entirely, leaving the leaf it was filed under
# driven by no cell of the matrix.
#
# So the members are named HERE, in literals nothing derives, and
# `test_no_declared_axis_can_shrink_in_silence` compares the two in both
# directions. `argv` needs no literal: what it must agree with is the COVERAGE
# KEY it is filed under, which IS derived — from `cli.build_parser()`.
# The pattern matches `tests/test_round_isolation.py`'s `_ADD_METHODS` marker
# control: name the members independently of the table the assertions read.

_MALFORMATION_SHAPES: dict[str, str] = {
    "list": "the wrong container where a scalar belongs",
    "dict": "the wrong container the other way, and the shape a nested "
            "structure arrives as when a producer changes",
    "int": "the right slot, the wrong primitive — what a type check exists "
           "for",
    "null": "present and empty, which every `.get(...) or ''` reader turns "
            "into a silent default",
    "absent": "THE ONE A TYPE CHECK ALONE CANNOT SEE. The cheapest member to lose and the "
              "dearest: a matrix over the other four still reports full "
              "coverage while that malformation class ships unguarded",
}

_MUST_DRIVE: tuple[tuple[str, ...], ...] = (
    ("certify",),
    ("memory", "ingest"),
    ("memory", "check-vocabulary"),
    ("memory", "watch"),
    ("memory", "stats"),
    ("memory", "recall"),
    ("autonomy", "pause"),
    ("rules", "recommend"),
    ("rules", "lifecycle"),
    ("plan",),
)

# `Driven.exits` is the FOURTH knob of the same shape: unpinned, flipping
# `('memory', 'ingest')` to `exits=False` would leave this module green while
# the tier-2 non-zero-exit obligation for the corpus's main mutating sweep
# silently stopped being asked. `certify` and `plan` are deliberately absent —
# both NAME the fault and deliberately do not exit non-zero, which the
# `Driven` docstring above states and their `why=` lines record.
_MUST_EXIT: tuple[tuple[str, ...], ...] = (
    ("memory", "ingest"),
    ("memory", "check-vocabulary"),
    ("memory", "watch"),
    ("memory", "stats"),
    ("memory", "recall"),
    ("autonomy", "pause"),
)

_MUST_NAME: tuple[tuple[str, ...], ...] = (
    ("certify",),
    ("memory", "ingest"),
    ("memory", "check-vocabulary"),
    ("memory", "watch"),
    ("memory", "stats"),
    ("memory", "recall"),
    ("autonomy", "pause"),
    ("plan",),
)


# ── the fixture: a minimal enrolled repo with one committed shard ──────────

_ARGUES = ("frozen at the count that exists today so that any further drift "
           "has to be answered rather than quietly absorbed")

_REPO_YAML = (
    "version: 1\nrepo: matrix-fixture\n"
    "components:\n  app:\n    path: app/\n    lang: python\n"
    "    description: application code\n"
    "risk_tiers:\n  - glob: app/**\n    tier: MEDIUM\n"
    "    reason: production code\n"
    "verify:\n  smoke:\n    - run: \"true\"\n"
    "review:\n  rules_dir: .warden/rules\n"
    "  blocking_severities: [HIGH]\n")


def _record(i: int = 0, **extra) -> dict:
    r = {"id": f"rec-{i}", "ts": f"2026-09-01T00:00:0{i}+00:00", "seq": i,
         "source": "attest", "sha": "a" * 40, "base_sha": "b" * 40,
         "rule_id": "unmapped:ripe", "tags": ["ripe"], "dir_prefix": "app",
         "file": "app/x.py", "line": 1, "severity": "MEDIUM",
         "finding": f"f{i}", "evidence": "e", "status": "fixed",
         "origin": "interactive", "reason": "r", "legacy_rule_id": "",
         "lens": "code-reviewer", "round": 1, "pr": "1", "bead": "b"}
    r.update(extra)
    return r


def _envelope(records: list[dict], **extra) -> dict:
    doc = {"schema": 1, "source": "attest", "sha": "a" * 40,
           "base_sha": "b" * 40, "rules_version": "c" * 12,
           "reviewed_at": "2026-09-01T00:00:00+00:00", "verdict": "clean",
           "reviewers": [{"role": "code-reviewer", "returned": True}],
           "roster_verification": "unverified-roster",
           "round_binding": "unminted", "range_patch_ids": ["p"],
           "records": records}
    doc.update(extra)
    return doc


# A ceiling the fixture declares, so `memory check-vocabulary` EVALUATES
# instead of exiting 2 on "NO CEILING" — a probe that never reaches the corpus
# reader proves nothing about it. The limit is deliberately loose: this matrix
# is about malformed input, not about drift.
_CEILING = ("ceiling:\n  max_undecided: 99\n  rationale: " + _ARGUES + "\n")


def _repo(tmp_path: Path, *, shard: object = None,
          tags_yaml: str | None = None,
          repo_yaml: str | None = None,
          cache: object = None) -> Path:
    root = tmp_path / "repo"
    (root / ".warden" / "memory" / "attest").mkdir(parents=True)
    (root / ".warden" / "rules").mkdir(parents=True)
    (root / "app").mkdir(parents=True)
    (root / "app" / "x.py").write_text("x = 1\n")
    # The file hint `plan --area app` needs to reach `recall` at all. Without
    # it `plan` yields no paths, never calls a record reader, and its matrix
    # cell passes while proving nothing.
    (root / ".warden" / "file-hints").mkdir(parents=True)
    (root / ".warden" / "file-hints" / "app.yaml").write_text(
        "scenarios:\n  - name: probe\n    source_files: [app/x.py]\n")
    (root / "repo.yaml").write_text(repo_yaml if repo_yaml is not None
                                    else _REPO_YAML)
    (root / ".warden" / "rules" / "ripe.md").write_text(
        "---\nid: ripe\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\n---\nfixture rule\n')
    (root / ".warden" / "memory" / "tags.yaml").write_text(
        tags_yaml if tags_yaml is not None else _CEILING)
    if shard is not None:
        (root / ".warden" / "memory" / "attest" / SHARD).write_text(
            json.dumps(shard))
    # The DERIVED cache, seeded with the same records. `memory stats`, `memory
    # recall` and `warden plan` read here and not from the shards, so a matrix
    # that wrote only shards would drive three commands past the malformation
    # entirely and call them covered.
    if cache is not None:
        rows = cache if isinstance(cache, list) else (
            shard.get("records", []) if isinstance(shard, dict) else [])
        (root / ".warden" / "memory" / memory_mod.CACHE_NAME).write_text(
            "".join(json.dumps(r) + "\n" for r in rows))
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True,
                   capture_output=True)
    return root


# Every command this module has actually put through `drive`, as (root, argv).
# The store guard reads it to WITNESS that the sweep it watches really ran: a
# fixture that returned a plausible table without calling `drive` leaves no
# trace here, and nothing it can hand the guard forges one.
#
# THE ROOT IS HALF THE WITNESS, not decoration. `_assert_no_crash` drives the
# same argv as every DRIVEN leaf, and 170 malformation cells call it, so an
# argv alone is evidence that SOMEBODY drove that command — under `-n auto`,
# quite possibly another cell sharing this worker. Only the sweep drives them
# against the sweep's own session tmp dir, so pairing the two is what makes
# the trace attributable to the run the guard is actually watching.
_DRIVEN_ARGV: list[tuple[str, tuple[str, ...]]] = []


def drive(root: Path, argv: list[str]) -> tuple[int, str]:
    """Run one warden command in-process against `root`, as the CLI would.

    In-process rather than a subprocess because this module drives close to
    two thousand warden commands — the 170 malformation cells against each of
    the ten `DRIVEN` leaves, plus the rest — and a subprocess each would make
    it a nightly rather than a CI check. Derived from the two constants
    rather than stated as a round number, because the earlier "~1000 cells"
    matched neither reading: the module collects 221 tests and drove 1973
    commands when this was measured. The signal is identical: `cli.main`'s
    blanket handler prints the traceback to stderr and returns 2, so a crash
    is visible in exactly the text a consumer would see.
    """
    _DRIVEN_ARGV.append((str(root), tuple(argv)))
    out, err = io.StringIO(), io.StringIO()
    cwd = os.getcwd()
    os.chdir(root)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(argv)
            except SystemExit as e:        # argparse's own exits
                code = int(e.code or 0)
    finally:
        os.chdir(cwd)
    return code, out.getvalue() + err.getvalue()


# ── tier 0: the axes are real ──────────────────────────────────────────────

def test_every_cli_leaf_is_classified():
    """A subcommand the matrix has never heard of is the defect one level up.

    Derived set versus declared set, both directions: a new leaf is unclassified
    (fail), a deleted leaf leaves a stale entry (fail).
    """
    declared, actual = set(COVERAGE), set(CLI_LEAVES)
    assert actual - declared == set(), (
        "unclassified CLI leaves — drive them or exclude them with a reason: "
        + ", ".join(" ".join(leaf) for leaf in sorted(actual - declared)))
    assert declared - actual == set(), (
        "COVERAGE names leaves the CLI no longer has: "
        + ", ".join(" ".join(leaf) for leaf in sorted(declared - actual)))


def test_the_derived_surface_is_plausible():
    """A floor under both derivations, so a refactor that empties one is
    caught as a defect rather than read as a green matrix.

    Not an exact list — an exact list is the hand-maintained enumeration this
    file exists to replace. The floor is the shape: `build_records` writes the
    fields every reader keys on, and the envelope carries the event.
    """
    assert len(RECORD_FIELDS) >= 20, RECORD_FIELDS
    assert {"ts", "sha", "rule_id", "status", "file", "dir_prefix", "tags",
            "finding", "id"} <= set(RECORD_FIELDS), RECORD_FIELDS
    assert {"verdict", "source", "sha", "reviewed_at",
            "roster_verification"} <= set(ENVELOPE_KEYS), ENVELOPE_KEYS
    assert len(NAMERS) >= 6, sorted(NAMERS)


def test_no_declared_axis_can_shrink_in_silence():
    """The five knobs this file DECLARES — four against the
    literals above that nothing derives, and `argv` against the derived
    COVERAGE key it is filed under.

    Deleting a member of `MUTATIONS`, flipping a row from `Driven` to
    `Excluded`, flipping a `names=` or `exits=` flag, or repointing a row's
    `argv` at another subcommand would otherwise narrow the matrix and every
    assertion over it at once — the self-referential shape both this file and
    `tests/test_guard_mutations.py` exist to close.
    Each comparison runs in BOTH directions: a member that leaves is named, and
    one that arrives with no reason typed beside it is named too.
    """
    lost = sorted(set(_MALFORMATION_SHAPES) - set(MUTATIONS))
    assert not lost, (
        f"the malformation shape(s) {lost} left the matrix's axis — every cell "
        "for them is gone and every assertion that iterated them went with it, "
        "so this file now reports full coverage over a narrower matrix. "
        + "; ".join(f"{shape}: {_MALFORMATION_SHAPES[shape]}"
                    for shape in lost))
    arrived = sorted(set(MUTATIONS) - set(_MALFORMATION_SHAPES))
    assert not arrived, (
        f"MUTATIONS carries {arrived} with no entry in `_MALFORMATION_SHAPES` "
        "saying what it is for")

    undriven = sorted(set(_MUST_DRIVE) - set(DRIVEN))
    assert not undriven, (
        "CLI leaf/leaves the matrix no longer DRIVES: "
        + ", ".join(" ".join(leaf) for leaf in undriven)
        + " — a `Driven(...)` row became `Excluded(...)`, or went, and every "
        "cell of the matrix stopped reaching that reader while the count of "
        "cells fell with it")
    surprise = sorted(set(DRIVEN) - set(_MUST_DRIVE))
    assert not surprise, (
        "the matrix drives leaves this control does not name: "
        + ", ".join(" ".join(leaf) for leaf in surprise))

    silent = sorted(set(_MUST_NAME) - set(NAMERS))
    assert not silent, (
        "driven leaf/leaves no longer required to NAME the shard and the "
        "field at fault: " + ", ".join(" ".join(leaf) for leaf in silent)
        + " — a `names=` flag was flipped, and tier 2 (a refusal that blames "
        "the wrong subject) stopped being asked of it")
    extra = sorted(set(NAMERS) - set(_MUST_NAME))
    assert not extra, (
        "leaves are held to tier 2 that this control does not name: "
        + ", ".join(" ".join(leaf) for leaf in extra))

    exiting = {leaf for leaf, spec in DRIVEN.items() if spec.exits}
    lax = sorted(set(_MUST_EXIT) - exiting)
    assert not lax, (
        "driven leaf/leaves no longer required to EXIT non-zero when the "
        "contract refuses the record: "
        + ", ".join(" ".join(leaf) for leaf in lax)
        + " — an `exits=` flag was flipped, and `assert code != 0` stopped "
        "being asked of a command that must refuse. `certify` and `plan` are "
        "the two that legitimately do not, and their rows say why")
    strict = sorted(exiting - set(_MUST_EXIT))
    assert not strict, (
        "leaves are required to exit non-zero that this control does not "
        "name: " + ", ".join(" ".join(leaf) for leaf in strict))

    # THE FIFTH KNOB, and the one that needs no literal beside it: a row's
    # hand-typed `argv` must RUN the leaf it is filed under, and the leaf is
    # derived from `cli.build_parser()`. Without comparing the two, a row
    # could be repointed at another subcommand with the matrix reporting the
    # same coverage over a command it no longer drives.
    for leaf, spec in sorted(DRIVEN.items()):
        assert tuple(spec.argv[:len(leaf)]) == leaf, (
            f"the `{' '.join(leaf)}` row runs `warden "
            f"{' '.join(spec.argv)}` — a different leaf. Every cell filed "
            "under this key drives that other command instead, and the "
            "coverage this matrix reports for "
            f"`{' '.join(leaf)}` is a claim about a command it never runs")

    # …and each named shape is EXERCISED, not merely restated: the contract has
    # to refuse it on a required field, or the axis carries a shape no cell of
    # tier 2 ever reaches.
    for shape in _MALFORMATION_SHAPES:
        assert _contract_refuses("ts", shape), (
            f"`RECORD_FIELD_CONTRACT` does not refuse `ts` = {shape}, so that "
            "column of the matrix asserts nothing beyond tier 1")


# ── tier 1 + 2: the matrix itself ──────────────────────────────────────────

def _mutate(base: dict, field: str, mutation: str) -> dict:
    out = dict(base)
    if mutation == "absent":
        out.pop(field, None)
    else:
        out[field] = _MUTANTS[mutation]
    return out


def _assert_no_crash(root: Path, subject: str) -> dict[tuple, tuple[int, str]]:
    seen = {}
    for leaf, spec in DRIVEN.items():
        code, text = drive(root, list(spec.argv))
        assert TRACEBACK not in text, (
            f"`warden {' '.join(spec.argv)}` CRASHED on {subject}\n{text}")
        seen[leaf] = (code, text)
    return seen


def _contract_refuses(field: str, mutation: str) -> bool:
    """Does `RECORD_FIELD_CONTRACT` refuse this shape?

    Asked of the CONTRACT, never restated here. That is the whole point: the
    expectation and the enforcement come from one value, so a field whose
    classification changes changes both at once and a hand-typed expectation
    cannot drift from the validator.
    """
    contract = memory_mod.RECORD_FIELD_CONTRACT
    if field not in contract:
        return False
    if mutation == "absent":
        return contract[field][1]
    return bool(memory_mod._field_type_problem(field, _MUTANTS[mutation]))


@pytest.mark.parametrize("mutation", MUTATIONS)
@pytest.mark.parametrize("field", RECORD_FIELDS)
def test_no_malformed_record_field_crashes_any_command(tmp_path, field,
                                                       mutation):
    """THE MATRIX. Axis one x the five shapes x every driven leaf.

    Tier 1 for every cell; tier 2 for the cells `RECORD_FIELD_CONTRACT`
    refuses, asked of the contract rather than restated here.

    Two records, not one: `_streaks_by_round` and the candidate tally only
    bucket when there is something to bucket with, and a one-record probe
    undercounts the shapes that crash.
    """
    good, bad = _record(0), _mutate(_record(1), field, mutation)
    shard = _envelope([good, bad])
    root = _repo(tmp_path, shard=shard, cache=[good, bad])
    subject = f"record field `{field}` = {mutation}"
    seen = _assert_no_crash(root, subject)
    if not _contract_refuses(field, mutation):
        return
    for leaf, spec in NAMERS.items():
        code, text = seen[leaf]
        where = f"`warden {' '.join(spec.argv)}` on {subject}"
        if spec.exits:
            assert code != 0, f"{where}: ACCEPTED it (exit 0)\n{text}"
        assert SHARD in text or memory_mod.CACHE_NAME in text, (
            f"{where}: refused without naming the FILE at fault\n{text}")
        assert field in text, (
            f"{where}: refused without naming the FIELD at fault\n{text}")


def test_every_field_the_producer_writes_is_classified():
    """The fence on axis one, and the reason the table is a mechanism.

    `build_records` is the sole writer of shard records; `RECORD_FIELD_CONTRACT`
    is the sole reader-side gate on them. A field in the first and not the
    second reaches every counting seam unchecked, which is the defect class in
    one sentence. Derived from the producer's AST, so a field
    added tomorrow reddens this test rather than a consumer's terminal.
    """
    missing = set(RECORD_FIELDS) - set(memory_mod.RECORD_FIELD_CONTRACT)
    assert not missing, (
        "`build_records` writes fields `RECORD_FIELD_CONTRACT` does not "
        "classify, so no reader checks them: " + ", ".join(sorted(missing)))
    stale = set(memory_mod.RECORD_FIELD_CONTRACT) - set(RECORD_FIELDS)
    assert not stale, (
        "`RECORD_FIELD_CONTRACT` classifies fields the producer no longer "
        "writes: " + ", ".join(sorted(stale)))


def test_a_malformed_cache_degrades_plan_loudly(tmp_path):
    """`warden plan` must plan anyway — and SAY why the priors are empty.

    Priors are advisory, so a corpus that cannot be read must not stop a plan.
    But "no priors on record" and "the priors could not be read" send a builder
    to opposite places, and an empty section says the first. The caveat carries
    the reader's own sentence, which names the file and the field.
    """
    from warden import plan as plan_mod
    # The fixture already ships the `app` hint the recipe needs (see `_repo`).
    root = _repo(tmp_path, cache=[_mutate(_record(0), "ts", "dict")])
    code, text = drive(root, ["plan", "--task", "probe", "--area", "app"])
    assert TRACEBACK not in text, text
    assert code == 0, text
    assert "## Caveats" in text, text
    assert memory_mod.CACHE_NAME in text and "ts" in text, text
    assert plan_mod  # imported to state the seam this covers


@pytest.mark.parametrize("mutation", MUTATIONS)
@pytest.mark.parametrize("key", ENVELOPE_KEYS)
def test_no_malformed_envelope_key_crashes_any_command(tmp_path, key, mutation):
    """Tier 1 over axis one's envelope half.

    Tier 2 is NOT asserted for the envelope: most of its keys are optional by
    design and absence is legal and present in the committed corpus (shards
    with no `verdict` at all), so "every reader refuses" would be the wrong
    claim. The invariant is that no shape crashes.
    """
    shard = _envelope([_record(0)])
    if mutation == "absent":
        shard.pop(key, None)
    else:
        shard[key] = _MUTANTS[mutation]
    root = _repo(tmp_path, shard=shard)
    _assert_no_crash(root, f"envelope key `{key}` = {mutation}")


@pytest.mark.parametrize("mutation", ("list", "int", "null", "absent"))
@pytest.mark.parametrize("block", ("components", "risk_tiers", "verify",
                                   "review", "version", "repo"))
def test_a_malformed_repo_yaml_block_is_refused_by_name(tmp_path, block,
                                                        mutation):
    """The config surface every command loads before it does anything else.

    Bounded on purpose (see the module docstring): the top-level TYPE of each
    declared block, not the per-key contract, which is
    `warden/schemas/repo.schema.json`'s.

    TWO assertions: "no traceback" alone cannot earn a name promising "refused
    by name", because stubbing `drive` to return `(0, "ok")` — a warden that
    accepts every malformed block and names nothing — would leave every cell
    green. Every one of these shapes is in fact refused by `config`, so the
    test can and must say so: exit non-zero, with `repo.yaml` and the
    offending block both named.
    """
    import yaml
    doc = yaml.safe_load(_REPO_YAML)
    if mutation == "absent":
        doc.pop(block, None)
    else:
        doc[block] = _MUTANTS[mutation]
    root = _repo(tmp_path, repo_yaml=yaml.safe_dump(doc),
                 shard=_envelope([_record(0)]))
    subject = f"repo.yaml block `{block}` = {mutation}"
    for leaf, spec in DRIVEN.items():
        code, text = drive(root, list(spec.argv))
        where = f"`warden {' '.join(spec.argv)}` on {subject}"
        assert TRACEBACK not in text, f"{where} CRASHED\n{text}"
        assert code != 0, f"{where} ACCEPTED it (exit 0)\n{text}"
        assert "repo.yaml" in text, f"{where} did not name the file\n{text}"
        assert block in text, f"{where} did not name the block\n{text}"


# ── tier 3: the named non-shard surfaces, one test per surface ────────────

def test_a_non_utf8_cage_toml_refuses_rather_than_raising(tmp_path):
    """A non-UTF-8 cage.toml is refused, never raised.

    `Path.read_text()` on non-UTF-8 bytes raises `UnicodeDecodeError` — a
    ValueError that `except (TOMLDecodeError, OSError)` around
    `tomllib.loads(cage.read_text())` does not cover, and escaping
    `certify.run` it would stop the ladder rendering at all. Three call sites
    read cage.toml: `_cage_owner`, `_cage_unreadable`, and the
    `cage_forbidden_paths_nonempty` arm of `_run_check`.
    """
    root = _repo(tmp_path, shard=_envelope([_record(0)]))
    cage = tmp_path / "cage"
    cage.mkdir()
    (cage / "cage.toml").write_bytes(b"\xff\xfe\x00bad")
    (cage / "prompt.md").write_text("prompt\n")
    code, text = drive(root, ["certify"])
    assert TRACEBACK not in text, text
    assert "unreadable" in text.lower(), text
    assert "cage" in text.lower(), text


def test_a_non_string_rule_id_names_the_ruleset_not_the_run_dir(tmp_path):
    """A non-string rule id names the ruleset, not the run dir.

    A frontmatter `id` checked for PRESENCE only lets `id: 7` yield an int and
    a blank `id:` yield None. `attest._check_rule_ids` would then raise
    TypeError from `", ".join(sorted(valid))`, which `memory ingest` catches
    in the arm that reports a malformed ARTIFACT: naming the run dir — the one
    thing that is fine — and EXITING 0, which `cage/run.sh` reads as "the
    corpus rebuilt".
    """
    root = _repo(tmp_path)
    (root / ".warden" / "rules" / "ripe.md").write_text(
        "---\nid: 7\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\n---\nfixture rule\n')
    run = root / ".warden" / "out" / "20260901T000000Z-attest"
    run.mkdir(parents=True)
    (run / "attestation.json").write_text(json.dumps({
        "reviewed_at": "2026-09-01T00:00:00+00:00", "head_sha": "a" * 40,
        "base_sha": "b" * 40, "verdict": "clean",
        "findings": [{"rule_id": "ripe", "file": "app/x.py",
                      "finding": "f", "severity": "MEDIUM"}]}))
    code, text = drive(root, ["memory", "ingest"])
    assert TRACEBACK not in text, text
    assert code != 0, f"ingest exited 0 over an unreadable ruleset\n{text}"
    assert "ripe.md" in text, text
    assert "20260901T000000Z-attest" not in text, (
        "blamed the run dir for the RULESET's defect\n" + text)


def test_an_unreadable_rule_file_names_the_ruleset(tmp_path):
    """An unreadable rule file names the ruleset, like a non-string rule id.

    A rule file that cannot be READ (chmod 000, a dangling symlink) must not
    raise a bare OSError out of ingest's per-artifact loop into `cli.main`'s
    blanket handler: a traceback, exit 2, no ingest-result.json, and a message
    naming a rule file rather than saying the corpus was not fed. That path
    fails closed; what it lacks is diagnosability, and it aborts a MUTATING
    sweep part-way.
    """
    root = _repo(tmp_path)
    victim = root / ".warden" / "rules" / "ripe.md"
    victim.unlink()
    victim.symlink_to(root / ".warden" / "rules" / "nowhere.md")
    run = root / ".warden" / "out" / "20260901T000000Z-attest"
    run.mkdir(parents=True)
    (run / "attestation.json").write_text(json.dumps({
        "reviewed_at": "2026-09-01T00:00:00+00:00", "head_sha": "a" * 40,
        "base_sha": "b" * 40, "verdict": "clean",
        "findings": [{"rule_id": "ripe", "file": "app/x.py",
                      "finding": "f", "severity": "MEDIUM"}]}))
    code, text = drive(root, ["memory", "ingest"])
    assert TRACEBACK not in text, text
    assert code != 0, text
    assert "ripe.md" in text, text
    assert "20260901T000000Z-attest" not in text, (
        "blamed the run dir for the RULESET's defect\n" + text)


# `null` is deliberately absent from this list: `tags:` with nothing under it
# DECLARES NOTHING, and absent and empty are states rather than refusals
# ("only a block carrying content this loader would throw away is a
# complaint"). A matrix case asserting otherwise would be asserting against
# that decision.
@pytest.mark.parametrize("block", ("tags", "aliases"))
@pytest.mark.parametrize("mutation", ("list", "int"))
def test_a_refused_declaration_block_stops_every_corpus_score(tmp_path,
                                                                 block,
                                                                 mutation):
    """A refused declaration block stops every score over the folded corpus.

    `load_aliases` returns `{}` for a non-mapping `aliases:` block — it is the
    permissive reader every folding seam calls — so `canonicalize_records`
    early-returns on the empty table and `records_from_shards` hands its
    caller an UNFOLDED corpus. Two spellings of one class split n and the
    Wilson floor. When `memory check-vocabulary` says "cannot evaluate the
    vocabulary", a score over that corpus must complain too.

    S-04 used to be that score inside `certify`; it now counts a promote
    artifact and computes no floor over the corpus, so the `promotion_bar_met`
    type it left behind (still declarable in an overlay) and `memory watch`
    are the surfaces that must name the block.
    """
    value = {"list": "[a, b]", "int": "7"}[mutation]
    root = _repo(tmp_path, shard=_envelope([_record(0)]),
                 tags_yaml=f"{block}: {value}\n")
    code, text = drive(root, ["certify"])
    assert TRACEBACK not in text, text
    from warden import certify as certify_mod
    ok, detail = certify_mod._run_check(
        {"id": "S-04", "label": "l", "type": "promotion_bar_met"}, root)
    assert not ok and block in detail, detail
    code, text = drive(root, ["memory", "watch"])
    assert TRACEBACK not in text, text
    assert code == 3 and block in text, (
        f"memory watch scored the corpus without naming the refused "
        f"`{block}:` block\n{text}")


def test_a_complaint_is_labelled_for_the_declaration_it_speaks_for(
        tmp_path):
    """A complaint is labelled for the declaration it speaks for.

    `tags.ceiling_status` merges complaints from four sources — the ceiling
    block, the ceiling FILE, the receipts block, and the `tags:`/`aliases:`
    blocks. Three renderers print them: `vocabulary.py`,
    `tags._render_ceiling` and `cli`'s `memory ingest` line. Before one shared
    labeller, two of the three prefixed EVERY one with `CEILING UNREADABLE`,
    which asserts a declaration that may not exist; `advisor.py`'s ceiling
    line reads `.warden/catalog-answers.yaml` and never printed these. This
    test drives the `memory ingest` line. Every complaint payload names its
    own block, so no reader is sent to the wrong file — a blanket PREFIX is
    what would lie, on a surface whose whole job is telling an author which
    line to fix.
    """
    root = _repo(tmp_path, shard=_envelope([_record(0)]),
                 tags_yaml="aliases: [not, a, mapping]\n")
    code, text = drive(root, ["memory", "ingest"])
    assert TRACEBACK not in text, text
    assert "CEILING UNREADABLE" not in text, (
        "an `aliases:` complaint labelled as the CEILING\n" + text)
    assert "aliases" in text, text


# ── named regressions at the edges of the record contract ─────────────────

def test_co01_plan_renders_a_prior_whose_optional_fields_are_absent(tmp_path):
    """`warden plan` renders a prior whose optional fields are absent.

    `RECORD_FIELD_CONTRACT` deliberately accepts a record with no `finding` and
    no `dir_prefix` — they are render fields with honest empty values. Every
    reader has to default them, not only `memory.render_line`: a `plan.build`
    prior projection subscripting both would make `warden plan` on a hinted
    area die with a bare `KeyError: 'finding'` over a shard `memory ingest`
    accepts at exit 0 — the matrix's own written FALSIFIER, met by the
    matrix's own sibling reader.
    """
    # `dir_prefix` is KEPT: `plan` queries `recall` by FILE only, so a record
    # with no `dir_prefix` matches nothing, the priors come back empty and the
    # projection under test never runs — without it this test would pass with
    # plan.py reverted.
    thin = _record(0)
    thin.pop("finding")
    root = _repo(tmp_path, shard=_envelope([thin]), cache=[thin])
    code, text = drive(root, ["plan", "--task", "probe", "--area", "app"])
    assert TRACEBACK not in text, text
    assert code == 0, text
    assert "Verify these, do not assume them" in text, (
        "the prior did not render, so the projection under test never ran"
        f"\n{text}")
    assert thin["rule_id"] in text, text


def test_co05_an_empty_required_field_is_refused_like_an_absent_one(tmp_path):
    """An empty required field is refused like an absent one.

    `build_records` derives `ts` from `attestation.get("reviewed_at", "")`, so
    an artifact with findings and no `reviewed_at` files records with `ts: ""`.
    A contract refusing only ABSENCE would accept those, and `_round_key`
    would bucket every one of them into `("", sha)` — the exact
    one-bucket fold the `required` column exists to prevent, reached by the
    empty string instead.
    """
    empty = _mutate(_record(0), "ts", "absent")
    empty["ts"] = ""
    with pytest.raises(ValueError) as raised:
        memory_mod.validate_shard_records(SHARD, [empty])
    assert "empty" in str(raised.value) and "ts" in str(raised.value), raised.value
    root = _repo(tmp_path, shard=_envelope([empty]), cache=[empty])
    code, text = drive(root, ["memory", "ingest"])
    assert TRACEBACK not in text and code != 0, text
    assert SHARD in text and "ts" in text, text


@pytest.mark.parametrize("how", ["unparseable", "not-a-mapping", "bad-block"])
def test_cr01_s04_refuses_to_score_over_any_unfoldable_vocabulary(tmp_path, how):
    """S-04 refuses to score over any unfoldable vocabulary.

    `tags.block_problems` speaks for the BLOCK alone. `load_aliases` goes
    through the permissive `_load_doc` and returns the same empty fold table
    for an unparseable file and a top-level non-mapping, so an S-04 arm asking
    only the block would score the promotion bar `ok=True` over an unfolded
    corpus while `memory check-vocabulary` exits 2 saying it cannot evaluate
    the vocabulary — the refused-block harm, reached through the FILE. All
    three causes must refuse.
    """
    body = {"unparseable": "tags:\n  a: b\n   bad indent: c\n",
            "not-a-mapping": "- just\n- a list\n",
            "bad-block": "aliases: [not, a, mapping]\n"}[how]
    root = _repo(tmp_path, shard=_envelope([_record(0)]), tags_yaml=body)
    from warden import certify as certify_mod
    ok, detail = certify_mod._run_check(
        {"id": "S-04", "label": "l", "type": "promotion_bar_met"}, root)
    assert not ok, detail
    assert "cannot be applied" in detail, detail
    # The command surface: S-04 no longer reads the corpus (it counts a
    # promote artifact), so the CLI that still scores over the folded corpus
    # and must refuse by name is `memory watch`.
    code, text = drive(root, ["memory", "watch"])
    assert TRACEBACK not in text, text
    assert code == 3 and "cannot be applied" in text, text


def _assert_nothing_to_ingest(root: Path) -> None:
    """The nothing-to-ingest precondition, evaluated every time it is asked.

    `warden memory ingest` behaves differently when there IS something to
    sweep, so a case about the NOTHING-to-sweep path has to know its fixture
    seeded no run dir. Unconditional on purpose: a conditional spelling can
    collapse to `assert True` and be unable to fail.
    """
    out_dir = root / ".warden" / "out"
    queued = sorted(p.name for p in out_dir.glob("*")) if out_dir.is_dir() \
        else []
    assert not queued, (
        f"the fixture seeded {queued} under .warden/out, so `memory ingest` "
        "has something to sweep and this case no longer probes the "
        "nothing-to-ingest path R2-01 is about")


def test_the_nothing_to_ingest_precondition_can_fail(tmp_path):
    """The precondition above, shown failing.

    A guard that has never been able to fail is a written claim, not a check —
    which is the whole family this module and its sibling harness are in. So
    the run dir the fixture must not have is seeded here, and the helper must
    say so and name what it found.
    """
    root = _repo(tmp_path)
    _assert_nothing_to_ingest(root)                      # clean, as shipped
    run = root / ".warden" / "out" / "20260901T000000Z-attest"
    run.mkdir(parents=True)
    with pytest.raises(AssertionError) as raised:
        _assert_nothing_to_ingest(root)
    assert "20260901T000000Z-attest" in str(raised.value), raised.value


@pytest.mark.parametrize("how", ["chmod-000", "dangling-symlink",
                                 "unlistable-dir"])
def test_r201_an_unreadable_ruleset_names_itself_with_nothing_to_ingest(
        tmp_path, how):
    """An unreadable ruleset names itself even with nothing to ingest.

    The probe answers two questions — can the ruleset be READ, and is it
    VALID — and only VALID may be gated on "is there anything to sweep".
    Gating BOTH would move the traceback rather than remove it:
    `cli._cmd_memory` calls `rules.rules_version` for the run manifest AFTER
    `ingest` returns, so a chmod-000 or dangling rule file with nothing queued
    would come back as a bare `PermissionError`, and an unlistable rules dir
    would exit 0 in silence. The READ half is unconditional.

    The sibling `test_cr05_...` cannot see any of this: it uses a rule file that
    reads perfectly and is merely invalid, which is the other half.

    The precondition is UNCONDITIONAL. A spelling like `assert not list(...)
    if (root/".warden"/"out").is_dir() else True` parses as `assert (X if C
    else True)`; `_repo` never creates `.warden/out`, so C would be
    statically False and the statement `assert True` — a check with no
    subject. `_assert_nothing_to_ingest` evaluates on every run, and
    `test_the_nothing_to_ingest_precondition_can_fail` seeds a run dir and
    watches it fire.
    """
    import os
    if os.geteuid() == 0:
        pytest.skip("root can read a chmod 000 path")
    root = _repo(tmp_path)
    rule = root / ".warden" / "rules" / "ripe.md"
    rules_dir = rule.parent
    if how == "chmod-000":
        os.chmod(rule, 0o000)
    elif how == "dangling-symlink":
        rule.unlink()
        rule.symlink_to(rules_dir / "gone.md")
    else:
        os.chmod(rules_dir, 0o000)
    try:
        _assert_nothing_to_ingest(root)
        code, text = drive(root, ["memory", "ingest"])
    finally:
        os.chmod(rules_dir, 0o755)
        if rule.is_file():
            os.chmod(rule, 0o644)
    assert TRACEBACK not in text, text
    assert code != 0, f"an unreadable ruleset exited 0 in silence\n{text}"
    assert "ruleset" in text and "NOT fed" in text, text
    if how != "unlistable-dir":
        assert "ripe.md" in text, text


def test_cr05_an_invalid_ruleset_with_nothing_to_ingest_still_exits_zero(
        tmp_path):
    """An invalid ruleset with nothing to ingest still exits zero.

    The ruleset probe exists to protect a MUTATING sweep. Run unconditionally
    it would also catch a ruleset that reads perfectly and is merely INVALID,
    moving a tree with an invalid rule file and NO artifacts to ingest from
    exit 0 to exit 2 — and `cage/run.sh` reads that code as "the corpus was
    not fed". A sweep that resolves nothing cannot be harmed by a ruleset it
    never reads.
    """
    root = _repo(tmp_path)
    (root / ".warden" / "rules" / "ripe.md").write_text(
        "---\nid: ripe\nseverity: CRITICAL\nengine: claude\n"
        'applies_to: ["**"]\n---\nfixture rule\n')
    code, text = drive(root, ["memory", "ingest"])
    assert TRACEBACK not in text, text
    assert code == 0, f"an invalid ruleset with nothing to sweep now fails\n{text}"
    # …and it is still fatal the moment there IS something to sweep.
    run = root / ".warden" / "out" / "20260901T000000Z-attest"
    run.mkdir(parents=True)
    (run / "attestation.json").write_text(json.dumps({
        "reviewed_at": "2026-09-01T00:00:00+00:00", "head_sha": "a" * 40,
        "base_sha": "b" * 40, "verdict": "clean",
        "findings": [{"rule_id": "ripe", "file": "app/x.py",
                      "finding": "f", "severity": "MEDIUM"}]}))
    code, text = drive(root, ["memory", "ingest"])
    assert TRACEBACK not in text, text
    assert code != 0 and "ripe.md" in text, text


# ── the other direction: what the corpus legitimately contains ─────────────

def _live_shards() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", ".warden/memory/attest/*.json",
         ".warden/memory/gate/*.json"],
        cwd=REPO, capture_output=True, text=True, check=True)
    return sorted(REPO / rel for rel in out.stdout.split("\0") if rel)


@needs_corpus
def test_the_live_corpus_still_loads():
    """DIRECTION MATTERS BOTH WAYS: a reader made stricter must not start
    refusing input the corpus legitimately contains.

    Every committed shard, through the shared validator every reader calls.
    This is the opposite-error guard for the whole matrix, and it is measured
    rather than asserted: the count is printed on failure so a reader can see
    which shard the change would have dropped.
    """
    shards = _live_shards()
    assert len(shards) >= 100, (
        f"only {len(shards)} committed shards found — the opposite-direction "
        "guard has nothing to measure against")
    refused = []
    for shard in shards:
        try:
            memory_mod.read_shard(shard, require_ids=True)
        except ValueError as e:
            refused.append(str(e))
    assert not refused, (
        f"{len(refused)} of {len(shards)} committed shards would now be "
        "REFUSED — this change is stricter than the corpus:\n"
        + "\n".join(refused[:10]))


def _live_corpus_copy(dst: Path) -> Path:
    """This repo's committed corpus and config, in a throwaway tree.

    A COPY, and that is safety rather than tidiness. `memory ingest` is a
    MUTATING sweep: driven at the real checkout with one
    `.warden/out/<stamp>-attest/` present — the ordinary state between `attest
    write` and the commit that carries the shard, which is exactly when the
    pre-push hook runs this suite — a single test would mint a tracked shard
    into `.warden/memory/attest/` AND write the one-run-dir-one-shard-ever
    marker beside the artifact. `.warden/out/` is
    gitignored so `git clean -fd` leaves the marker, while the new untracked
    shard it points at is removed; `memory.ingest` then reads the marker as
    "already ingested elsewhere" and never re-mints. A test run could silently
    retire a real review event from the corpus every claim this platform makes
    is computed from.

    So: copy what the commands read, drive them there, and let the real store be
    read-only for the whole suite — which `test_the_live_store_is_untouched`
    then proves rather than assumes.
    """
    root = dst / "live-copy"
    (root / ".warden").mkdir(parents=True)
    shutil.copy2(REPO / "repo.yaml", root / "repo.yaml")
    for rel in (".warden/rules", ".warden/memory", ".warden/file-hints"):
        src = REPO / rel
        if src.is_dir():
            shutil.copytree(src, root / rel)
    for rel in (".warden/certification.yaml", ".warden/catalog-answers.yaml",
                ".warden/skills-policy.md"):
        if (REPO / rel).is_file():
            shutil.copy2(REPO / rel, root / rel)
    # The components repo.yaml declares must exist for `verify`/`explain` to
    # resolve paths; an empty dir is enough for every driven reader.
    for name in ("warden", "cage", "tests", "examples", "skills", "docs"):
        (root / name).mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True,
                   capture_output=True)
    return root


def _live_store_fingerprint() -> list[tuple[str, int, float]]:
    store = REPO / ".warden" / "memory"
    return sorted((str(p.relative_to(REPO)), p.stat().st_size, p.stat().st_mtime)
                  for p in store.rglob("*") if p.is_file())


# TAKEN AT IMPORT, before a single test in this module has run. Taken inside
# the guard itself, at the START of the last test, every earlier test's
# mutation would already be baked into the baseline and the guard would
# compare a dirtied store against itself. A guard that cannot fail is worse
# than none: it is a written claim that nothing happened.
_STORE_AT_IMPORT = _live_store_fingerprint()


@pytest.fixture(scope="session")
def live_corpus_sweep(clean_git_env, tmp_path_factory):
    """One copy of the live corpus, with every driven leaf run over it once.

    The two tests below both need the same work done — the real committed
    corpus in a throwaway tree, every DRIVEN leaf driven across it — and then
    ask different questions OF THAT ONE SWEEP: one about what the commands
    said, one about what the committed store looks like afterwards. What the
    second sweep added was a repeat of each leaf against a pristine copy; what
    it answered, the first had already answered.

    NOTHING IS SAMPLED AND NOTHING IS STANDING IN. The copy still carries
    every committed shard and the sweep still runs every driven leaf, so each
    test reads exactly what it read before; only the second identical
    execution is gone.

    Session-scoped, so the sweep happens once per worker rather than once per
    test, and BOTH tests request it — which is what keeps the store guard
    honest under `-n auto`. Its claim is that no command in this session
    touched the committed store, so the drives it watches have to have run in
    the session doing the watching. A worker that runs only the guard still
    executes the whole sweep here, before the guard fingerprints anything,
    because the fixture it depends on is what performs it.

    `clean_git_env` is named rather than left to autouse ordering: the copy
    runs `git init`, and with `GIT_DIR` still set that would retarget the real
    checkout — the hazard that fixture exists for. Depending on it puts the
    ordering in the signature instead of in pytest's autouse-first rule.

    The corpus floor is asserted HERE, before the drives, because `memory
    ingest` is a mutating sweep: read afterwards, the shard count is what the
    copy carried PLUS what the sweep minted, which is a postcondition standing
    in for the precondition the assertions below actually need.

    READING A DURATIONS TABLE: the sweep is charged to fixture SETUP, against
    whichever of the two tests requested it first — under `--dist load` that
    is not deterministic. The other then reads as a test that does nothing.
    Neither is a test that got cheap; the cost moved, and the pair's real cost
    is the setup line.
    """
    root = _live_corpus_copy(tmp_path_factory.mktemp("live-corpus"))
    assert len(list((root / ".warden" / "memory" / "attest").glob("*.json"))) \
        >= 100, "the copy did not carry the corpus"
    results = {}
    for leaf, spec in DRIVEN.items():
        results[leaf] = drive(root, list(spec.argv))
    return root, results


@needs_corpus
def test_the_live_corpus_drives_every_command_clean(live_corpus_sweep):
    """The same direction, at the COMMAND level rather than the validator's.

    A stricter validator that no command can reach is not evidence. Every
    driven leaf runs against this repo's real committed corpus — in a copy, for
    the reason `_live_corpus_copy` states — and none may crash or refuse it.
    """
    _root, results = live_corpus_sweep
    for leaf, spec in DRIVEN.items():
        code, text = results[leaf]
        assert TRACEBACK not in text, (
            f"`warden {' '.join(spec.argv)}` crashed on the LIVE corpus"
            f"\n{text}")
        for refusal in ("not an evidence shard", "carries no `",
                        "carries an empty `", "that is not a string",
                        "that is not an integer", "not a list of strings",
                        "unreadable shard"):
            assert refusal not in text, (
                f"`warden {' '.join(spec.argv)}` REFUSED this repo's own "
                f"corpus ({refusal!r}) — the change is stricter than the "
                f"evidence the repo already carries\n{text}")


def test_the_store_guard_names_the_session_not_the_module():
    """The store guard's failure sentence, pinned where it can regress.

    `_STORE_AT_IMPORT` is taken at COLLECTION, so the window this guard watches
    opens before any test in ANY module has run. A
    `tests/test_aaa_other_module.py`, sorted first, writing one shard into
    `.warden/memory/attest/` reddens the guard, and a message saying "a test
    in this module" would send the reader to a thousand-line file to hunt a
    `drive(REPO, ...)` that is not there. The window is right; the sentence
    has to say session. Nothing but this test
    reads a failure message, so this is where it stays fixed — read off the
    module's own AST, the derivation discipline the rest of the file uses.
    """
    subject = "test_the_live_store_is_untouched"
    tree = ast.parse(Path(__file__).read_text())
    guard = next((node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef)
                  and node.name == subject), None)
    # A bare `next(...)` here would abort with an unadorned `StopIteration`
    # naming nothing the moment that function is renamed.
    assert guard is not None, (
        f"this module no longer defines `{subject}`, so the store guard whose "
        "failure message this pins has been renamed or removed — rename the "
        "subject here too, or the sentence stops being checked by anything")
    # THE FINGERPRINT ASSERT, not every assert in the function. Joining them
    # all made this check satisfiable by a SIBLING assertion's prose: the
    # moment the guard grew a second message containing the word, the
    # sentence this pins could be de-sessioned and nothing reddened. So the
    # subject is identified by what only it does — comparing against
    # `_STORE_AT_IMPORT` — and a sibling's wording cannot stand in for it.
    stores = [node for node in ast.walk(guard)
              if isinstance(node, ast.Assert) and node.msg is not None
              and any(isinstance(n, ast.Name) and n.id == "_STORE_AT_IMPORT"
                      for n in ast.walk(node.test))]
    assert len(stores) == 1, (
        f"expected exactly one assertion in `{subject}` comparing against "
        f"`_STORE_AT_IMPORT`, found {len(stores)} — this test pins THAT "
        "assertion's wording, and it can no longer tell which one it is")
    messages = " ".join(
        part.value for part in ast.walk(stores[0].msg)
        if isinstance(part, ast.Constant) and isinstance(part.value, str))
    assert "session" in messages, (
        "the store guard's failure message no longer names the SESSION, and "
        "its observation window is the whole session: " + messages)
    assert "in this module" not in messages, (
        "the store guard blames `this module` for a write its baseline cannot "
        "attribute — the baseline is taken at collection, before any module "
        "has run: " + messages)


@needs_corpus
def test_the_live_store_is_untouched(live_corpus_sweep):
    """Nothing in this pytest SESSION may write into the committed evidence
    store.

    The guard for the hazard `_live_corpus_copy` describes, and it is an
    assertion rather than a convention because a convention cannot catch one
    `drive(REPO, ...)` that reads as a read. Run LAST in file order on
    purpose — pytest executes top to bottom within a module, so by the time
    this runs every cell above it has had its chance to mutate — and compared
    against `_STORE_AT_IMPORT`, taken at collection, which is what makes those
    chances visible.

    ITS SUBJECT IS THE SESSION, not this module. The baseline is taken at
    COLLECTION, so the observation window opens before any test in ANY module
    has run: a `tests/test_aaa_other_module.py` — sorted first — that writes a
    shard into `.warden/memory/attest/` reddens this guard. That wide window
    is the RIGHT behaviour and is what makes the guard able to fail at all;
    the sentence has to match it, because "a test in this module" names the
    one subject the evidence cannot support. The name and the message both
    say session.

    The sweep it watches is `live_corpus_sweep`'s, which has already driven
    every leaf by the time this body runs — requesting the fixture is what
    orders the drives before the fingerprint, in this worker, whether or not
    the test above it ran here too.
    """
    root, _results = live_corpus_sweep
    # WITNESSED, not taken on the fixture's word. Comparing `results`' keys
    # against the `DRIVEN` they were built from is a tautology, and checking
    # their shape is barely better — `{leaf: (0, "") for leaf in DRIVEN}`
    # satisfies both while driving nothing. This file's own rule two screens
    # up is that a guard which cannot fail is worse than none, so the
    # evidence has to be something only a real `drive` call can leave:
    # `_DRIVEN_ARGV`, appended inside `drive` itself, AGAINST THIS SWEEP'S OWN
    # ROOT — `_assert_no_crash` drives the same argv from 170 other cells,
    # so the argv alone would pass on their work. A stub, a
    # pre-canned table or a replayed recording reddens here rather than
    # fingerprinting a store no command was ever given the chance to touch.
    ran = {argv for where, argv in _DRIVEN_ARGV if where == str(root)}
    missing = sorted(tuple(spec.argv) for spec in DRIVEN.values()
                     if tuple(spec.argv) not in ran)
    assert not missing, (
        "the sweep this session guard watches never ran these commands, so a "
        f"store write by any of them would pass unseen: {missing}")
    now = _live_store_fingerprint()
    changed = sorted({n for n, _, _ in _STORE_AT_IMPORT} ^ {n for n, _, _ in now}) \
        or sorted({n for n, s, m in set(now) - set(_STORE_AT_IMPORT)})
    assert now == _STORE_AT_IMPORT, (
        "something in this pytest session wrote into .warden/memory since "
        "collection — the committed evidence store is read-only to the whole "
        "suite, and the baseline this compares against was taken before any "
        "test ran, so the writer may be in ANY module rather than this one: "
        + ", ".join(changed[:5]))
