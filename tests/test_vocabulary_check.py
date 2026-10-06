"""`warden memory check-vocabulary` — the shipped half of the drift ceiling.

This repo declares a ceiling in `.warden/memory/tags.yaml` and enforces it
with `tests/test_tag_vocabulary_guards.py`, which lives in THIS repo and runs
in THIS repo's CI. A consumer that adopts the `ceiling:` block gets the
reporting half (`memory ingest` and `memory stats` render the breach) and,
without this action, nothing that can reject: the pytest guard is not
shipped, and the ingest exit code is spoken for — `cage/run.sh` reads it as
"was the corpus fed", so failing it would drop the attest shard of the very
round that coined the name. A policy surface every consumer can declare and
only the platform can enforce is the policy-enforcement-gap class, on the
platform's own governance feature.

This action is the enforcement. Its exit code means ONE thing:

    0  a declared ceiling holds
    1  a declared ceiling is breached — every undecided name is printed
    2  the check could not evaluate: nothing declared, a declaration or
       receipts block that cannot be read, an unreadable committed shard,
       or an unenrolled repo

It reads the COMMITTED attest shards only, through the same
`tags.ceiling_status` definition `memory stats` renders — but with the same
INPUT as the platform's guard test (`records_from_shards`), so those two
always agree; `stats` reads the gitignored local cache, which can lag a
`git pull` or carry gate shards, and may say something else.
"""

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from private_evidence import needs_receipts

from warden import cli
from warden import tags as tags_mod
from warden import vocabulary as vocab_mod
from warden.memory import records_from_shards

ROOT = Path(__file__).parent.parent
EXAMPLE = ROOT / "examples" / "hello-svc"

# A rationale that clears `_argument_has_substance`, so every case below is
# testing what it claims and not tripping the rationale floor by accident.
_ARGUES = ("frozen at the count that exists today so that any further drift "
           "has to be answered rather than quietly absorbed")

_CEILING_0 = ("tags:\n  fail-open: errs toward passing\n"
              "ceiling:\n  max_undecided: 0\n"
              f"  rationale: {_ARGUES}\n")

_RECEIPT = ("left_undeclared:\n  ripe: >-\n"
            "    seen three times this round, recorded so the backlog cannot "
            "grow in silence, and decided nothing about the class yet\n")


def _repo(tmp_path, tags_yaml: str | None, records: list[dict],
          *, shard_name: str = "20260901T000000Z-aaaaaaaa-bbbbbbbb.json") -> Path:
    """A minimal enrolled repo whose corpus is one committed shard."""
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    (root / "repo.yaml").write_text(
        "version: 1\nrepo: ceiling-fixture\n"
        "components:\n  app:\n    path: app/\n    lang: python\n"
        "    description: application code\n"
        "risk_tiers:\n  - glob: app/**\n    tier: MEDIUM\n"
        "    reason: production code\n"
        "verify:\n  smoke:\n    - run: \"true\"\n"
        "review:\n  rules_dir: .warden/rules\n"
        "  blocking_severities: [HIGH]\n")
    (root / ".warden" / "rules").mkdir(parents=True)
    (root / ".gitignore").write_text(
        ".warden/out/\n.warden/memory/findings.jsonl\n.warden/memory/gate/\n")
    mem = root / ".warden" / "memory"
    (mem / "attest").mkdir(parents=True)
    if tags_yaml is not None:
        (mem / "tags.yaml").write_text(tags_yaml)
    if records:
        (mem / "attest" / shard_name).write_text(
            json.dumps({"records": records}))
    for args in (("init", "-q", "-b", "main"),
                 ("config", "user.email", "t@t"), ("config", "user.name", "t"),
                 ("add", "."),
                 ("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def _records(tag: str, n: int, status: str = "fixed") -> list[dict]:
    return [{"id": f"{tag}{i:04x}", "ts": f"2026-09-01T00:00:{i:02d}+00:00",
             "seq": i, "source": "attest", "sha": "a" * 40, "base_sha": "b" * 40,
             "rule_id": f"unmapped:{tag}", "tags": [tag], "dir_prefix": "app",
             "file": "app/x.py", "line": 1, "severity": "MEDIUM",
             # One text PER JUDGMENT. `n` here means n distinct judgments of
             # the class, and every one of them sits in its own round (each
             # carries its own `ts`), so a single literal "f" repeated would
             # read as one finding RESTATED n times and fold to one — the
             # corpus size beside the verdict would then be 2 where the
             # fixture builds 4, because restatements fold at every counting
             # seam.
             "finding": f"f{i}", "evidence": "e", "status": status}
            for i in range(n)]


def _run(root, monkeypatch, capsys) -> tuple[int, str, str]:
    monkeypatch.chdir(root)
    code = cli.main(["memory", "check-vocabulary"])
    out = capsys.readouterr()
    return code, out.out, out.err


def _artifacts(root) -> list[Path]:
    return [p for p in (root / ".warden" / "out").glob(
        "*-memory-check-vocabulary/vocabulary-check.json")]


# ── the exit-code contract ────────────────────────────────────────────────


def test_a_held_ceiling_exits_0_and_prints_the_reading(tmp_path, monkeypatch,
                                                       capsys):
    root = _repo(tmp_path, _CEILING_0, _records("fail-open", 2))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 0, err
    assert "drift ceiling: 0 undecided of 0 allowed" in out, (
        "the clean case prints the number too — a ceiling nobody can see is "
        "a ceiling nobody knows they are near")
    assert "BREACHED" not in out + err


def test_a_breach_exits_1_and_names_every_undecided_tag(tmp_path, monkeypatch,
                                                        capsys):
    root = _repo(tmp_path, _CEILING_0,
                 _records("ripe", 3) + _records("coined-once", 1))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 1
    breach = [ln for ln in err.splitlines() if "CEILING BREACHED" in ln]
    assert breach, err
    # The names, on the line that carries the verdict — not on the audit
    # lines above it, which also name them.
    assert "ripe" in breach[0] and "coined-once" in breach[0]
    # The FILE and the two other remedies — not the word "declare", which
    # "against a declared ceiling" also satisfies.
    for remedy in ("Declare each in .warden/memory/tags.yaml", "fold",
                   "left_undeclared"):
        assert remedy in breach[0], (
            f"the breach line must name the remedy {remedy!r} — a red step "
            "that does not say which line to write is diagnosability debt")


def test_a_receipt_clears_the_breach(tmp_path, monkeypatch, capsys):
    """Recording costs one line and decides nothing about the class."""
    root = _repo(tmp_path, _CEILING_0 + _RECEIPT, _records("ripe", 3))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 0, err


def test_a_fold_clears_the_breach(tmp_path, monkeypatch, capsys):
    """Read AFTER canonicalization, like every other seam: an alias is a
    disposition, so a folded name never counts as undecided."""
    root = _repo(tmp_path, _CEILING_0 + "aliases:\n  ripe: fail-open\n",
                 _records("ripe", 3))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 0, err


def test_a_ceiling_at_the_count_holds_and_one_below_breaches(
        tmp_path, monkeypatch, capsys):
    """The limit is inclusive: N undecided of N allowed holds."""
    two = ("tags:\n  fail-open: errs toward passing\n"
           f"ceiling:\n  max_undecided: 2\n  rationale: {_ARGUES}\n")
    root = _repo(tmp_path, two, _records("ripe", 1) + _records("raw", 1))
    assert _run(root, monkeypatch, capsys)[0] == 0
    one = two.replace("max_undecided: 2", "max_undecided: 1")
    (root / ".warden" / "memory" / "tags.yaml").write_text(one)
    assert _run(root, monkeypatch, capsys)[0] == 1


# ── could not evaluate is 2, never a pass ─────────────────────────────────


def test_no_declared_ceiling_is_cannot_evaluate_not_a_pass(
        tmp_path, monkeypatch, capsys):
    """A CI step named for this check that goes green with nothing declared
    is the enforcement-claim shape. `rules recommend` exits 0 with no
    ceiling because it is a report first; this action IS the check."""
    root = _repo(tmp_path, "tags:\n  fail-open: errs toward passing\n",
                 _records("ripe", 3))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2
    assert "no ceiling" in err.lower() and "tags.yaml" in err, (
        "the refusal must name the file and the block to write")


def test_an_absent_vocabulary_file_is_cannot_evaluate(tmp_path, monkeypatch,
                                                     capsys):
    root = _repo(tmp_path, None, _records("ripe", 3))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2
    assert "tags.yaml" in err


@pytest.mark.parametrize("body", [
    "ceiling:\n  max_undecided: 1\n  rationale: x\n",
    "ceiling:\n  max_undecided: -1\n  rationale: " + _ARGUES + "\n",
    "ceiling: 3\n",
    "- ceiling:\n    max_undecided: 0\n",
    "tags: {\n",
])
def test_an_unreadable_declaration_exits_2_never_headroom(
        tmp_path, monkeypatch, capsys, body):
    """One typo must not disarm the only deterministic obligation the
    vocabulary file carries — and it must not pass either."""
    root = _repo(tmp_path, body, _records("ripe", 3))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2, (body, err)
    assert "UNREADABLE" in err or "cannot be checked" in err, err


def test_an_unreadable_receipts_block_exits_2(tmp_path, monkeypatch, capsys):
    """A malformed `left_undeclared:` entry silently stops discharging its
    name. Under the ceiling that would read as drift; here it is named as
    the config error it is, and the check refuses rather than guessing."""
    root = _repo(tmp_path, _CEILING_0 + "left_undeclared:\n  ripe: x\n",
                 _records("ripe", 3))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2, err
    assert "left_undeclared" in err


def test_receipts_complaints_are_labelled_as_receipts_not_as_the_ceiling(
        tmp_path, monkeypatch, capsys):
    """Named regression: a repo with NO ceiling and one malformed
    `left_undeclared:` entry must not be told its ceiling is declared and
    unreadable with the NO CEILING line withheld — the reader would fix the
    receipt, re-run, and only then learn there was never a ceiling to
    check."""
    root = _repo(tmp_path, "tags:\n  fail-open: errs toward passing\n"
                 "left_undeclared: nope\n", _records("ripe", 1))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2
    assert "RECEIPTS UNREADABLE" in err
    assert "CEILING UNREADABLE" not in err, (
        "the message asserted a declaration that does not exist")
    assert "NO CEILING" in err, "both facts, in one run"


def test_a_ceiling_complaint_is_still_labelled_as_the_ceiling(
        tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path, "ceiling:\n  max_undecided: 1\n  rationale: x\n",
                 _records("ripe", 1))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2
    assert "CEILING UNREADABLE" in err and "RECEIPTS UNREADABLE" not in err


def test_an_unreadable_committed_shard_exits_2_and_names_it(
        tmp_path, monkeypatch, capsys):
    """Silently skipping a shard deletes its records from the count, and the
    shard most likely to matter is the one carrying the coined name."""
    root = _repo(tmp_path, _CEILING_0, _records("fail-open", 1))
    bad = root / ".warden" / "memory" / "attest" / "20260901T000001Z-cc-dd.json"
    bad.write_text("{not json")
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2
    assert bad.name in err, "the refusal must name the shard"
    # A REFUSAL, not a crash: an escaping exception also exits 2 with the
    # name in its traceback, so without this line the test stays green with
    # the handler deleted.
    assert "CANNOT EVALUATE" in err and "Traceback" not in err
    artifacts = _artifacts(root)
    assert len(artifacts) == 1
    assert json.loads(artifacts[0].read_text())["status"] == "cannot-evaluate"


def test_a_shard_that_is_not_utf8_is_named_in_the_refusal(tmp_path, monkeypatch,
                                                          capsys):
    """Named regression: `read_text` raises UnicodeDecodeError, a ValueError
    that a wrap of only two narrower classes in records_from_shards lets
    through — the refusal would say 'byte 0xe9 in position 40' and name no
    file in a directory of dozens."""
    root = _repo(tmp_path, _CEILING_0, _records("fail-open", 1))
    bad = root / ".warden" / "memory" / "attest" / "20260901T000001Z-cc-dd.json"
    bad.write_bytes(b'{"records": [{"finding": "caf\xe9"}]}')
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2
    assert bad.name in err and "CANNOT EVALUATE" in err


@pytest.mark.parametrize("records, why", [
    (["not-a-record"], "a record that is not a mapping"),
    ([None], "a null record"),
    ([{"id": "x", "tags": "ripe"}], "tags as a string iterates as letters"),
    ([{"id": "x", "tags": [1, 2]}], "tags that are not strings crash the render"),
])
def test_a_malformed_record_inside_a_shard_is_a_named_refusal_with_an_artifact(
        tmp_path, monkeypatch, capsys, records, why):
    """Named regression: every record inside the envelope is checked, not
    trusted whole. Unchecked, a non-mapping record escapes as an
    AttributeError with no artifact; integer tags write a `breached` artifact
    and then crash the render, so the evidence on disk contradicts the exit;
    a string tag reddens the step telling the author to declare
    `e, i, p, r`."""
    root = _repo(tmp_path, _CEILING_0, _records("fail-open", 1))
    bad = root / ".warden" / "memory" / "attest" / "20260901T000001Z-cc-dd.json"
    bad.write_text(json.dumps({"records": records}))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2, (why, err)
    assert bad.name in err and "CANNOT EVALUATE" in err, (why, err)
    assert "Traceback" not in err, why
    artifacts = _artifacts(root)
    assert len(artifacts) == 1, why
    assert json.loads(artifacts[0].read_text())["status"] == "cannot-evaluate"


def test_a_record_with_no_tags_is_a_record_not_a_refusal(tmp_path, monkeypatch,
                                                         capsys):
    """The guard above must not refuse the committed corpus: a record may
    carry no tags at all (older shards do), and that is an empty list, not
    a malformed one — `tags: null` included, which is the spelling
    `RECORD_FIELD_CONTRACT` singles out as nullable for exactly this reason.

    Both records carry `ts`: the contract refuses a record with no `ts`,
    because every counting seam buckets by it and the empty default folds
    distinct rounds into one — fail-open in the Wilson math. They stand in
    for records `build_records` writes, and it always writes `ts`; the claim
    this test makes is about `tags`.
    """
    root = _repo(tmp_path, _CEILING_0,
                 [{"id": "x", "ts": "2026-09-01T00:00:00+00:00",
                   "rule_id": "r", "status": "fixed"},
                  {"id": "y", "ts": "2026-09-01T00:00:01+00:00",
                   "rule_id": "r", "status": "fixed", "tags": None}])
    assert _run(root, monkeypatch, capsys)[0] == 0


@pytest.mark.parametrize("shape", ["regular-file", "dangling-symlink",
                                   "unlistable"])
def test_an_attest_dir_that_cannot_be_listed_is_cannot_evaluate_not_holds(
        tmp_path, monkeypatch, capsys, shape):
    """Named regression: `Path.glob` swallows the directory's own error and
    returns [], so an attest dir that is a plain file, a dangling symlink, or
    chmod 000 would read as an EMPTY corpus — `drift ceiling: 0 undecided of
    0 allowed`, exit 0, artifact `holds` — from a fixture that exits 1 when
    the dir is readable.
    'Unreadable input skipped', the fail-closed shape by name."""
    import os
    import shutil
    if shape == "unlistable" and os.geteuid() == 0:
        pytest.skip("root can list a chmod 000 directory")
    root = _repo(tmp_path, _CEILING_0, _records("ripe", 3))
    attest = root / ".warden" / "memory" / "attest"
    shutil.rmtree(attest)
    if shape == "regular-file":
        attest.write_text("not a directory")
    elif shape == "dangling-symlink":
        attest.symlink_to(root / "nowhere")
    else:
        attest.mkdir()
        (attest / "20260901T000000Z-aa-bb.json").write_text(
            json.dumps({"records": _records("ripe", 3)}))
        attest.chmod(0)
    try:
        code, out, err = _run(root, monkeypatch, capsys)
    finally:
        if shape == "unlistable":
            attest.chmod(0o755)
    assert code == 2, (shape, out, err)
    assert "CANNOT EVALUATE" in err and "holds" not in out, (shape, out, err)
    if shape == "dangling-symlink":
        # The DIAGNOSTIC, not just the exit code: every assertion above
        # survives deleting the symlink-resolution arm and falling through to
        # iterdir's ENOENT — which reports "no such file" about a path that
        # does exist as a link. This pins the message the arm exists to
        # produce, not only the fail-closed contract.
        assert "dangling symlink" in err, (shape, err)


@pytest.mark.parametrize("shape", ["symlink-into-unsearchable",
                                   "memory-dir-unsearchable"])
def test_a_stat_that_raises_is_a_named_refusal_with_an_artifact(
        tmp_path, monkeypatch, capsys, shape):
    """Named regression: the directory guard asks `Path.exists()`, and on
    3.12 `exists()` PROPAGATES an OSError that is not ENOENT/ENOTDIR — a
    symlink whose target sits under a chmod 000 parent, or `.warden/memory`
    itself unsearchable. PermissionError is not a ValueError, so uncaught it
    escapes `check()`, `cli.main`'s blanket handler prints a traceback, and
    `_cmd_check_vocabulary` never reaches `create_run_dir`: exit 2 with NO
    run dir and NO artifact — a crash instead of a reported refusal, reached
    through the directory guard itself."""
    import os
    import shutil
    if os.geteuid() == 0:
        pytest.skip("root can search a chmod 000 directory")
    root = _repo(tmp_path, _CEILING_0, _records("ripe", 3))
    attest = root / ".warden" / "memory" / "attest"
    if shape == "symlink-into-unsearchable":
        shutil.rmtree(attest)
        locked = root / "locked"
        (locked / "attest").mkdir(parents=True)
        (locked / "attest" / "20260901T000000Z-aa-bb.json").write_text(
            json.dumps({"records": _records("ripe", 3)}))
        attest.symlink_to(locked / "attest")
    else:
        locked = root / ".warden" / "memory"
    locked.chmod(0)
    try:
        code, out, err = _run(root, monkeypatch, capsys)
    finally:
        locked.chmod(0o755)
    assert code == 2, (shape, out, err)
    assert "Traceback" not in err, (shape, err)
    assert "CANNOT EVALUATE" in err and "CORPUS UNREAD" in err, (shape, err)
    artifacts = _artifacts(root)
    assert len(artifacts) == 1, (shape, artifacts)
    assert json.loads(artifacts[0].read_text())["status"] == "cannot-evaluate"


@pytest.mark.parametrize("rule_id,label", [
    (None, "null"), (7, "an int"), (["unmapped:x"], "a list")])
def test_a_record_whose_rule_id_is_not_a_string_is_a_named_refusal(
        tmp_path, monkeypatch, capsys, rule_id, label):
    """Named regression: the per-record guard covers not only the field the
    RENDERER touches (`tags`) but the one the function's own tail touches —
    `records_from_shards` ends in `canonicalize_records`, whose
    `_fold_rule_id` calls `rule_id.startswith`. Unguarded, a committed shard
    carrying `"rule_id": null` escapes as an AttributeError: traceback, exit
    2, no run dir and no artifact. Refused by name here, before the fold, so
    every caller of `records_from_shards` — certify, `rules recommend`,
    autonomy, all of which catch only ValueError — gets the refusal instead
    of the crash."""
    root = _repo(tmp_path, _CEILING_0, [])
    (root / ".warden" / "memory" / "attest" / "20260901T000001Z-cc-dd.json"
     ).write_text(json.dumps({"records": [
         {"id": "x", "status": "fixed", "rule_id": rule_id, "tags": ["ripe"]}]}))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2, (label, out, err)
    assert "Traceback" not in err, (label, err)
    assert "20260901T000001Z-cc-dd.json" in err and "rule_id" in err, err
    assert json.loads(_artifacts(root)[0].read_text())["status"] == \
        "cannot-evaluate"


def test_an_applicable_alias_cannot_turn_a_bad_rule_id_into_a_traceback(
        tmp_path, monkeypatch, capsys):
    """The shape needed to reach the fold path at all: the fold is skipped
    entirely unless an alias APPLIES, which needs a loadable ruleset, a
    declared target and no rule-namespace collision. With that real repo
    shape a bad `rule_id` reaches `_fold_rule_id`. The guard runs before the
    fold, so the verdict is the same refusal either way."""
    import shutil
    declared = ("tags:\n  ripened: a name the retro merged this one onto\n"
                "ceiling:\n  max_undecided: 0\n"
                f"  rationale: {_ARGUES}\n"
                "aliases:\n  ripe: ripened\n")
    root = _repo(tmp_path, declared, [])
    shutil.rmtree(root / ".warden" / "rules")
    shutil.copytree(ROOT / ".warden" / "rules", root / ".warden" / "rules")
    assert tags_mod.applicable_aliases(root) == {"ripe": "ripened"}, \
        "the alias must APPLY, or this fixture is not testing the fold path"
    (root / ".warden" / "memory" / "attest" / "20260901T000001Z-cc-dd.json"
     ).write_text(json.dumps({"records": [
         {"id": "x", "status": "fixed", "rule_id": None, "tags": ["ripe"]}]}))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2 and "Traceback" not in err, (out, err)
    assert "CANNOT EVALUATE" in err and "rule_id" in err, err
    assert len(_artifacts(root)) == 1, "a refusal still leaves its evidence"


def test_an_alias_that_folds_nothing_is_named_beside_the_remedy_naming_it(
        tmp_path, monkeypatch, capsys):
    """Named regression: the breach line prescribes 'fold it with an
    `aliases:` entry' — and an alias the loader REFUSES (typo'd target, or
    any alias at all once an unrelated rule file stops parsing) folds
    nothing. Reported only by `memory stats`, the surface that CANNOT fail a
    build, the author would re-write the entry they already wrote and stay
    red. The refusal is named beside the remedy that names it."""
    root = _repo(tmp_path, _CEILING_0 + "aliases:\n  ripe: fail-opne\n",
                 _records("ripe", 3))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 1, (out, err)
    assert "ripe -> fail-opne" in out, \
        "the reading names the alias that folded nothing"
    assert "target not declared" in out, "and why it folded nothing"
    assert "aliases:" in err and "declared alias" in err, \
        "the breach line points at it rather than prescribing it blind"
    doc = json.loads(_artifacts(root)[0].read_text())
    assert doc["invalid_aliases"], "and the artifact carries it too"


@pytest.mark.parametrize("body,label", [
    ("tags: {\n", "a file that does not parse"),
    ("- ceiling:\n    max_undecided: 0\n", "a document that is not a mapping")])
def test_a_file_that_cannot_be_read_never_claims_a_ceiling_was_declared(
        tmp_path, monkeypatch, capsys, body, label):
    """Named regression: `load_ceiling` speaks for two different facts, so
    not every complaint from it is 'CEILING UNREADABLE (declared, ...)'. When
    the whole file will not parse, or is not a mapping, whether a ceiling is
    declared is UNKNOWABLE, and a 'declared' label would contradict a payload
    saying 'if it declares a ceiling'. The reader would fix the YAML, re-run,
    and only then learn there was never a ceiling — the harm the
    receipts-label regression pins, reached through the other loader."""
    root = _repo(tmp_path, body, _records("ripe", 1))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2, (label, out, err)
    assert "(declared," not in err, (label, err)
    assert "VOCABULARY UNREADABLE" in err, (label, err)
    assert "whether a ceiling is declared" in err, (label, err)


def test_a_malformed_ceiling_block_is_still_labelled_declared(
        tmp_path, monkeypatch, capsys):
    """The other side of the line above, so the fix cannot be 'stop saying
    declared'. A `ceiling:` block that IS there and will not load is a
    declaration that cannot be read: never 'no ceiling', never headroom."""
    root = _repo(tmp_path, ("tags:\n  fail-open: x\n"
                            "ceiling:\n  max_undecided: none\n"
                            f"  rationale: {_ARGUES}\n"), _records("ripe", 1))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 2, (out, err)
    assert "CEILING UNREADABLE (declared," in err, err
    assert "NO CEILING" not in err, err


def test_an_absent_attest_dir_is_an_empty_corpus_that_holds(tmp_path,
                                                             monkeypatch, capsys):
    """hello-svc ships no attest dir and its clean probe expects 0: ABSENT is
    'no review has run yet', which is a real, empty corpus — not 'could not
    look'. The line between the two is what the test above pins."""
    root = _repo(tmp_path, _CEILING_0, [])
    import shutil
    shutil.rmtree(root / ".warden" / "memory" / "attest")
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 0, err
    assert "0 record(s)" in out


def test_an_unenrolled_repo_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["memory", "check-vocabulary"]) == 2
    assert "repo.yaml" in capsys.readouterr().err


# ── what it reads ─────────────────────────────────────────────────────────


def test_the_check_reads_committed_shards_never_the_cache_or_gate_shards(
        tmp_path, monkeypatch, capsys):
    """CI has only the committed tree. The derived cache and the gate shards
    are gitignored working state (R-08, R-11); a verdict that depended on
    either would pass locally and fail in CI, or the reverse."""
    root = _repo(tmp_path, _CEILING_0, _records("fail-open", 1))
    mem = root / ".warden" / "memory"
    (mem / "findings.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in _records("stale-cache-tag", 2)))
    (mem / "gate").mkdir()
    (mem / "gate" / "20260901T000002Z-ee-ff.json").write_text(
        json.dumps({"records": _records("local-gate-tag", 2)}))
    code, out, err = _run(root, monkeypatch, capsys)
    assert code == 0, err
    assert "stale-cache-tag" not in out + err
    assert "local-gate-tag" not in out + err


def test_the_check_and_the_guard_test_read_one_definition(tmp_path):
    """ONE definition AND one input: the platform's own guard reads
    `records_from_shards` through `undecided_tags`, exactly as this action
    does, so the two cannot disagree. (`memory stats` shares the definition
    but reads the local cache — a different input, which may lag.)"""
    root = _repo(tmp_path, _CEILING_0,
                 _records("ripe", 3) + _records("fail-open", 1))
    doc = vocab_mod.check(root)
    assert doc["undecided"] == tags_mod.undecided_tags(
        root, records_from_shards(root)) == ["ripe"]
    assert doc["breached"] is True and doc["ceiling"] == 0
    assert doc["records"] == 4, "the corpus size is reported beside the verdict"


def test_the_verdict_is_a_status_a_reader_can_switch_on(tmp_path):
    held = vocab_mod.check(_repo(tmp_path / "a", _CEILING_0, []))
    breached = vocab_mod.check(_repo(tmp_path / "b", _CEILING_0,
                                     _records("ripe", 3)))
    unread = vocab_mod.check(_repo(tmp_path / "c", "tags: {}\n", []))
    assert (held["status"], breached["status"], unread["status"]) == (
        "holds", "breached", "cannot-evaluate")
    assert vocab_mod.exit_code(held) == 0
    assert vocab_mod.exit_code(breached) == 1
    assert vocab_mod.exit_code(unread) == 2


# ── the verdict leaves evidence ───────────────────────────────────────────


@pytest.mark.parametrize("records, status, code", [
    ([], "holds", 0),
    (_records("ripe", 3), "breached", 1),
])
def test_every_verdict_leaves_an_artifact(tmp_path, monkeypatch, capsys,
                                          records, status, code):
    """Policy -> enforcement -> evidence: the unattended cage has no terminal,
    and a verdict living only in scrollback cannot be audited afterwards."""
    root = _repo(tmp_path, _CEILING_0, records)
    assert _run(root, monkeypatch, capsys)[0] == code
    artifacts = _artifacts(root)
    assert len(artifacts) == 1
    doc = json.loads(artifacts[0].read_text())
    assert doc["status"] == status
    manifest = json.loads((artifacts[0].parent / "manifest.json").read_text())
    assert manifest["cmd"] == "memory-check-vocabulary"
    assert manifest["exit_status"] == status


def test_a_cannot_evaluate_verdict_leaves_an_artifact_too(tmp_path,
                                                          monkeypatch, capsys):
    root = _repo(tmp_path, "tags: {}\n", _records("ripe", 1))
    assert _run(root, monkeypatch, capsys)[0] == 2
    doc = json.loads(_artifacts(root)[0].read_text())
    assert doc["status"] == "cannot-evaluate"
    assert doc["complaints"], "the artifact says WHY it could not evaluate"


# ── the platform under its own shipped surface ────────────────────────────


@needs_receipts
def test_this_repos_ceiling_holds_under_the_shipped_command(monkeypatch,
                                                            capsys):
    """Self-hosting: the obligation this repo enforces with a pytest guard
    must ALSO hold under the surface it ships to consumers, or the platform
    would be asking consumers to pass a gate it does not pass itself."""
    # The reader, not the CLI: the CLI writes a run dir, and a suite that
    # writes into the live checkout's .warden/out/ on every run is state
    # outside its own setup.
    doc = vocab_mod.check(ROOT)
    assert doc["status"] == "holds", doc
    assert vocab_mod.exit_code(doc) == 0
    assert "drift ceiling:" in vocab_mod.render_report(doc)


# ── the consumer fixture demonstrates it ──────────────────────────────────


def test_hello_svc_declares_a_ceiling_the_shipped_loader_reads():
    ceiling, complaints = tags_mod.load_ceiling(EXAMPLE)
    assert not complaints, complaints
    assert ceiling == 0, (
        "hello-svc is the copy consumers take; its ceiling is the one they "
        "inherit, and 0 is the value whose rationale is written down")


def test_hello_svc_ci_runs_the_check_as_a_step_that_can_fail():
    """The fixture's workflow is the copy consumers take. A step that cannot
    fail the job is not enforcement (E-01's own standard)."""
    wf = yaml.safe_load(
        (EXAMPLE / ".github" / "workflows" / "ci.yml").read_text())
    assert "pull_request" in wf[True]
    steps = [s for job in wf["jobs"].values() for s in job["steps"]
             if "memory check-vocabulary" in (s.get("run") or "")]
    assert len(steps) == 1, "hello-svc's workflow must run the check once"
    assert "continue-on-error" not in steps[0]
    assert "if" not in steps[0]
    # The exit must reach the job: `... || true`, `; true`, or a `set +e`
    # block all satisfy the three lines above.
    run = steps[0]["run"].strip()
    assert run.endswith("warden memory check-vocabulary"), run
    assert "\n" not in run, "a multi-line run: block can swallow the exit"


def test_the_portability_sim_proves_the_step_bites():
    """The gate must be shown to gate, both directions, in a copied-out
    consumer using only shipped surfaces. Parsed off the script's probe
    calls so a deleted probe goes red here rather than silently narrowing
    the proof."""
    import re
    script = (ROOT / "scripts" / "portability-sim.sh").read_text()
    probes = re.findall(r"^run_vocabulary_check (\d) (\S+)", script, re.M)
    # The labelled SEQUENCE, not the set of codes: `clean` already supplies
    # a 0, so deleting the `receipted` probe leaves the set intact.
    assert probes == [("0", "clean"), ("1", "breached"),
                      ("0", "receipted"), ("2", "unreadable")], probes
    # The receipt itself, at column 0 inside the appended block — the word
    # alone matches the sim's own comment.
    assert re.search(r"^left_undeclared:$", script, re.M), (
        "the sim must show the cheap remedy clearing the breach, not only "
        "the breach")
