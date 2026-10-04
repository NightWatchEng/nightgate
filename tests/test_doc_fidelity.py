"""The ruleset probe in Adopting.md, driven against the code it describes.

Adopting.md's ruleset-probe table is what a consumer sizes a platform bump
against: each row pairs an input with what `warden memory ingest` exits and
prints. The tests here build each input as a real tree, drive the probe, and
hold every cell of the table to what the tree produces, so the table is a
measurement rather than a remembered claim.
"""

from __future__ import annotations

import atexit
import contextlib
import io
import json
import os
import tempfile
from pathlib import Path

import pytest

from warden import cli as cli_mod
from warden import attest as attest_mod
from warden import memory as memory_mod

ROOT = Path(__file__).parent.parent
WIKI = ROOT / "docs" / "wiki"
ADOPTING = WIKI / "Adopting.md"


_PROBE_RULE = ("---\nid: ripe\nseverity: MEDIUM\nengine: claude\n"
               'applies_to: ["**"]\n---\nfixture rule\n')

# Enough `repo.yaml` for `cli.main` to resolve a rules dir and reach `ingest`.
_PROBE_REPO_YAML = (
    "version: 1\nrepo: doc-probe-fixture\n"
    "components:\n  app:\n    path: app/\n    lang: python\n"
    "    description: application code\n"
    "risk_tiers:\n  - glob: app/**\n    tier: MEDIUM\n"
    "    reason: production code\n"
    "verify:\n  smoke:\n    - run: \"true\"\n"
    "review:\n  rules_dir: .warden/rules\n"
    "  blocking_severities: [HIGH]\n")


def _probe_repo(root: Path, *, out_dir: bool, how: str | None) -> Path:
    """A minimal enrolled tree whose ruleset carries defect `how`.

    Deliberately hand-built rather than imported from another suite's fixture:
    this guard has to be able to say what the tree contained, and `out_dir`
    (whether `.warden/out` exists at all) is the axis the whole paragraph turns
    on.
    """
    rules = root / ".warden" / "rules"
    rules.mkdir(parents=True)
    (root / ".warden" / "memory").mkdir(parents=True)
    rule = rules / "ripe.md"
    body = _PROBE_RULE.replace("MEDIUM", "CRITICAL") if how is None \
        else _PROBE_RULE
    rule.write_bytes(body.encode() if how != "non-utf8"
                     else body.encode() + "café\n".encode("cp1252"))
    (root / "app").mkdir(parents=True)
    (root / "app" / "x.py").write_text("x = 1\n")
    (root / "repo.yaml").write_text(_PROBE_REPO_YAML)
    if out_dir:
        (root / ".warden" / "out").mkdir(parents=True)
    if how == "chmod-000":
        os.chmod(rule, 0o000)
    elif how == "dangling-symlink":
        rule.unlink()
        rule.symlink_to(rules / "gone.md")
    elif how == "unlistable-dir":
        os.chmod(rules, 0o000)
    return rules


# One temp root for the whole module, removed when the session ends, so no
# probe tree is left behind. `pytest`'s `tmp_path` is not used because these
# helpers are called from two tests and from a nested loop; a single fixture
# root keeps the paths distinct without threading it through.
_TMP_HOLDER = tempfile.TemporaryDirectory(prefix="warden-doc-probe-")
_TMP = Path(_TMP_HOLDER.name)
atexit.register(_TMP_HOLDER.cleanup)


def _plant_artifact(root: Path) -> None:
    """One attestation under `.warden/out`, so the sweep has a rule_id to
    resolve and the VALIDITY question is asked."""
    run = root / ".warden" / "out" / "20260901T000000Z-attest"
    run.mkdir(parents=True, exist_ok=True)
    (run / "attestation.json").write_text(json.dumps({
        "reviewed_at": "2026-09-01T00:00:00+00:00", "head_sha": "a" * 40,
        "base_sha": "b" * 40, "verdict": "clean",
        "findings": [{"rule_id": "ripe", "file": "app/x.py", "finding": "f",
                      "severity": "MEDIUM"}]}))


def _probe_refuses(tmp: Path, *, out_dir: bool, how: str | None,
                   artifact: bool = False) -> bool:
    """Does `memory.ingest` refuse this tree by name, or sweep it?

    BEHAVIOUR, not the source. Reading the `validate=` ARGUMENT of each
    `_assert_ruleset_readable` call out of the AST and concluding "one is
    unconditional" from a literal `False` cannot see the call being wrapped in
    an `if`, and cannot see the two sites being made to AGREE either. Nor can
    it see the per-file read arm narrowed to `except OSError`, which is the
    decode half the page teaches. Driving the probe answers all three.

    `artifact` puts one attestation in `.warden/out`, which is the ONLY thing
    the second question is gated on. Without it the validity half never runs;
    with it, it does.
    """
    root = tmp / (f"{how or 'unusable'}-{'out' if out_dir else 'noout'}"
                  f"-{'art' if artifact else 'bare'}")
    root.mkdir()
    rules = _probe_repo(root, out_dir=out_dir or artifact, how=how)
    if artifact:
        _plant_artifact(root)
    try:
        memory_mod.ingest(root, rules_dir=rules)
        return False
    except attest_mod.AttestError:
        return True
    finally:
        os.chmod(rules, 0o755)
        rule = rules / "ripe.md"
        if rule.is_file():
            os.chmod(rule, 0o644)


def _probe_exit(tmp: Path, *, how: str | None) -> tuple[int, str, Path]:
    """`warden memory ingest`'s EXIT CODE, its output, and the rules dir it
    ran against, for this tree.

    Through `cli.main`, not through `memory.ingest`, because each of the
    table's cells is an exit code plus whether the ruleset is NAMED in what is
    printed — both `cli.main`'s contract (the code, and the `warden: <message>`
    line it prints for an `AttestError`), not `ingest`'s, which raises.
    `_probe_refuses` above answers the narrower "did the probe
    refuse"; this answers what a consumer sees. The tree here always has no
    `.warden/out`, which is the shape the table states.
    """
    root = tmp / f"exit-{how or 'unusable'}"
    root.mkdir()
    rules = _probe_repo(root, out_dir=False, how=how)
    out, err = io.StringIO(), io.StringIO()
    cwd = os.getcwd()
    os.chdir(root)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli_mod.main(["memory", "ingest"])
            except SystemExit as e:
                code = int(e.code or 0)
    finally:
        os.chdir(cwd)
        os.chmod(rules, 0o755)
        rule = rules / "ripe.md"
        if rule.is_file():
            os.chmod(rule, 0o644)
    return code, out.getvalue() + err.getvalue(), rules


def _table_rows(page: str) -> list[list[str]]:
    """The data rows of the ruleset-probe table, cell by cell."""
    marker = "| the ruleset | `warden memory ingest` |"
    if marker not in page:
        return []
    body = page.split(marker, 1)[1].split("\n\n", 1)[0]
    rows = []
    for line in body.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and set(cells[0]) != {"-"}:
            rows.append(cells)
    return rows


def test_the_ruleset_probe_behaves_as_the_table_says():
    """The ruleset probe's BEHAVIOUR, driven against the page.

    Three things are pinned here:

    1. The two QUESTIONS. Reading the `validate=` argument of each probe call
       out of `warden/memory.py`'s AST and concluding "one is unconditional"
       from a literal `False` cannot see the call being wrapped in an `if`,
       and cannot see the two sites being made to AGREE. Nor can it see the
       read arm's exception tuple narrowed. So the probe is DRIVEN and the
       source is not read at all.
    2. WHICH SIDE each input lands on. That is not a stable fact: a change to
       the probe can move an input (the non-UTF-8 rule file, say) from the
       read side to the validity side, and this guard names the input when
       the page goes false.
    3. The TABLE. Its rows are what a reader plans a bump against, so every
       cell is checked, not only the bullets around it.

    WHAT IS NOT DRIVEN, and cannot be: the page's before/after sentence is a
    claim about two platform revisions, and one tree can only answer for one of
    them. Stubbing the probe out to stand for the "before" column stops meaning
    anything as soon as a refusal is added OUTSIDE the probe. The page states
    that half as prose and says why.

    SKIPS AS ROOT: two of the three read inputs are privilege-dependent.
    """
    if os.geteuid() == 0:
        pytest.skip("root reads a chmod-000 path and lists a chmod-000 dir, "
                    "so two of the three read inputs cannot be driven here")
    # The inputs, split by which QUESTION answers each. FUNCTION-LOCAL because
    # a module-level vocabulary needs a disposition in
    # `tests/test_guard_mutations.py`'s REGISTRY; moving both to module level
    # takes a FIXTURE entry there beside them. The sizes are asserted below,
    # so neither can quietly empty.
    read_inputs = ("chmod-000", "dangling-symlink", "unlistable-dir")
    unusable_inputs = ("non-utf8", None)
    assert len(read_inputs) == 3 and len(unusable_inputs) == 2, (
        "the driven input lists changed size; the page's two bullets and its "
        "five-row table are written against these, so move them together")
    tmp = Path(tempfile.mkdtemp(dir=str(_TMP)))

    # QUESTION ONE, on BOTH tree shapes — no `.warden/out` at all, and one that
    # exists and is empty. The second is the shape a source-reading guard
    # cannot distinguish.
    for out_dir in (False, True):
        for how in read_inputs:
            assert _probe_refuses(tmp, out_dir=out_dir, how=how), (
                f"a ruleset that cannot be READ ({how}) is swept without a "
                f"refusal on a tree with .warden/out "
                f"{'present and empty' if out_dir else 'absent'} — "
                "Adopting.md's first bullet says this half is asked "
                "UNCONDITIONALLY, and it no longer is. That is the R2-01 "
                "defect returning; rewrite the bullet or restore the probe")

    # QUESTION TWO is gated: with nothing to sweep it does not fire, and with
    # one artifact it does. `cage/run.sh` reads exit 0 as "was the corpus fed",
    # which is what the first half of this buys.
    for how in unusable_inputs:
        for out_dir in (False, True):
            assert not _probe_refuses(tmp, out_dir=out_dir, how=how), (
                f"an UNUSABLE but perfectly readable ruleset ({how}) is "
                "refused on a tree with nothing to ingest — Adopting.md's "
                "second bullet promises exit 0 there, and `cage/run.sh` reads "
                "a non-zero as 'the corpus was not fed'")
        assert _probe_refuses(tmp, out_dir=True, how=how, artifact=True), (
            f"an UNUSABLE ruleset ({how}) is NOT refused even with an "
            "artifact to resolve rule_ids against, so the second question "
            "never fires at all and the page describes a check that is gone")

    # THE TABLE. One row per input, in the page's own order, each cell checked
    # against what `warden memory ingest` really exits and prints — and each
    # LABEL checked against the input it is paired with, so the order is
    # verified rather than assumed.
    _assert_probe_table_is_driven(ADOPTING.read_text(),
                                  read_inputs + unusable_inputs, tmp)


def _assert_probe_table_is_driven(page: str, driven: tuple, tmp: Path) -> None:
    """Every cell of the ruleset-probe table on `page`, BOTH columns, against
    the tree.

    Pairing rows to inputs by position and asserting on the right-hand cell
    only would leave the label reaching nothing but the failure message, so a
    row relabelled `chmod 000` beside `exit 0`, or the two exit-2 rows
    swapped, would pass while the page contradicted its own bullets. Each
    label must carry the phrase that names its input. And "is the ruleset
    named" is the rules dir PATH in the output, not the word `ruleset`, which
    sits in the fixed prefix of every read-half refusal and so is a function
    of the exit code alone.
    """
    phrases = {"chmod-000": "chmod 000", "dangling-symlink": "dangling symlink",
               "unlistable-dir": "cannot be listed", "non-utf8": "not UTF-8",
               None: "UNUSABLE"}
    rows = _table_rows(page)
    assert len(rows) == len(driven), (
        f"{ADOPTING.name}'s ruleset-probe table has {len(rows)} data row(s), "
        f"and this guard drives {len(driven)}. Add the input here in the same "
        "commit that adds the row, or the table goes back to being prose "
        "nobody checks")
    for (label, cell), how in zip(rows, driven):
        assert phrases[how] in label, (
            f"{ADOPTING.name}'s table row {label!r} sits where the row for "
            f"the {how or 'unusable'} input goes and does not say "
            f"{phrases[how]!r}. The rows are paired to inputs in order, and "
            "the label is what proves the pairing — a row moved or relabelled "
            "is a cell measured against the wrong input")
        code, text, rules = _probe_exit(tmp, how=how)
        assert f"exit {code}" in cell, (
            f"{ADOPTING.name}'s table row {label!r} says {cell!r}; the tree "
            f"gives exit {code}. A consumer sizes a bump off these cells — "
            "re-measure and rewrite the row rather than moving this guard")
        named = str(rules) in text or str(rules.resolve()) in text
        assert ("naming the ruleset" in cell) == named, (
            f"{ADOPTING.name}'s table row {label!r}: the cell "
            f"{'claims' if 'naming the ruleset' in cell else 'does not claim'} "
            f"the ruleset is named and the tree {'does' if named else 'does not'} "
            f"print its path {str(rules)!r}. That is the DIAGNOSTIC half of "
            "what this change moved")


def _probe_page_with(rows: str) -> str:
    """Adopting.md with its ruleset-probe table's data rows replaced."""
    page = ADOPTING.read_text()
    marker = "| the ruleset | `warden memory ingest` |\n| --- | --- |\n"
    assert marker in page, "the ruleset-probe table's header moved"
    head, rest = page.split(marker, 1)
    body, tail = rest.split("\n\n", 1)
    return head + marker + rows + "\n\n" + tail


def _probe_inputs() -> tuple:
    return ("chmod-000", "dangling-symlink", "unlistable-dir", "non-utf8", None)


def test_a_row_relabelled_for_another_input_fails_the_table_guard():
    """Row 4 rewritten to `| a rule file at chmod 000 | exit 0 |`. The page
    then contradicts its own row 1, and a positional guard that never read a
    label would stay green."""
    rows = _table_rows(ADOPTING.read_text())
    assert len(rows) == 5, rows
    rows[3] = ["a rule file at chmod 000", "exit 0"]
    page = _probe_page_with("\n".join(f"| {a} | {b} |" for a, b in rows))
    tmp = Path(tempfile.mkdtemp(dir=str(_TMP)))
    with pytest.raises(AssertionError, match=r"does not say 'not UTF-8'"):
        _assert_probe_table_is_driven(page, _probe_inputs(), tmp)


def test_swapping_two_rows_with_equal_cells_fails_the_table_guard():
    """The two exit-2 rows swapped.
    Every cell is still true of SOME input, so a right-column-only guard
    cannot tell; the label binding can."""
    rows = _table_rows(ADOPTING.read_text())
    rows[0], rows[1] = rows[1], rows[0]
    page = _probe_page_with("\n".join(f"| {a} | {b} |" for a, b in rows))
    tmp = Path(tempfile.mkdtemp(dir=str(_TMP)))
    with pytest.raises(AssertionError, match=r"does not say 'chmod 000'"):
        _assert_probe_table_is_driven(page, _probe_inputs(), tmp)


def test_the_ruleset_is_named_by_its_path_not_by_the_word(monkeypatch):
    """The refusal's fixed prefix is "the declared ruleset could not be
    read", so `"ruleset" in text` is true of every exit-2 row whatever the
    message goes on to say. With the
    PATH stripped out of the message the word survives, and the guard has to
    notice the ruleset is no longer named."""
    from warden import memory as memory_mod
    real = memory_mod._assert_ruleset_readable

    def path_stripped(rules_dir, root):
        try:
            real(rules_dir, root)
        except memory_mod.AttestError as exc:
            text = str(exc).replace(str(rules_dir.resolve()), "<path>")
            raise memory_mod.AttestError(
                text.replace(str(rules_dir), "<path>")) from None

    monkeypatch.setattr(memory_mod, "_assert_ruleset_readable", path_stripped)
    tmp = Path(tempfile.mkdtemp(dir=str(_TMP)))
    with pytest.raises(AssertionError, match=r"does not print its path"):
        _assert_probe_table_is_driven(ADOPTING.read_text(), _probe_inputs(), tmp)
