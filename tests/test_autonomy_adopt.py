"""Non-blocking adoption with a severity refusal.

The autonomy ladder lets the machine ADOPT a rule only because a non-blocking
rule cannot fail a build — it reports and accumulates a precision history, and
reverting is one commit. That safety property has exactly one load-bearing
guard: a rule whose severity is in `blocking_severities` is REFUSED, because
adopting it WOULD raise enforcement, which is the human-only tier. These tests
pin the guard, and pin that it keys on the repo's declared blocking set rather
than on the literal string "HIGH".
"""

import json
from pathlib import Path

import pytest

from warden import autonomy as autonomy_mod
from warden import rules as rules_mod


def _rule(severity: str, rid: str = "new-lens") -> str:
    return (f"---\nid: {rid}\nseverity: {severity}\nengine: claude\n"
            'applies_to: ["**"]\n---\nreport when the drafted lens matches.\n')


def test_adopt_refuses_a_high_blocking_rule(tmp_path):
    with pytest.raises(autonomy_mod.AutonomyError) as exc:
        autonomy_mod.adopt_rule(tmp_path, _rule("HIGH"), dest_name="new-lens",
                                blocking_severities=["HIGH"])
    assert "blocking_severities" in str(exc.value)
    # and it wrote nothing
    assert not (tmp_path / ".warden" / "rules" / "new-lens.md").exists()


def test_adopt_writes_a_non_blocking_rule(tmp_path):
    dest, _artifact = autonomy_mod.adopt_rule(tmp_path, _rule("MEDIUM"),
                                              dest_name="new-lens",
                                              blocking_severities=["HIGH"])
    assert Path(dest).exists()
    # it is a valid, loadable rule
    loaded = rules_mod.load_rules(tmp_path / ".warden" / "rules")
    assert [r.id for r in loaded] == ["new-lens"]
    assert loaded[0].severity == "MEDIUM"


def test_the_refusal_keys_on_blocking_severities_not_the_word_high(tmp_path):
    """A repo that declares MEDIUM blocking too must have a MEDIUM adoption
    refused there — the guard reads the config, it does not hardcode HIGH."""
    with pytest.raises(autonomy_mod.AutonomyError):
        autonomy_mod.adopt_rule(tmp_path, _rule("MEDIUM"), dest_name="new-lens",
                                blocking_severities=["HIGH", "MEDIUM"])
    assert not (tmp_path / ".warden" / "rules" / "new-lens.md").exists()


def test_adopt_refuses_a_malformed_rule(tmp_path):
    bad = "---\nid: broken\nseverity: NOPE\nengine: claude\napplies_to: []\n---\n"
    with pytest.raises(autonomy_mod.AutonomyError):
        autonomy_mod.adopt_rule(tmp_path, bad, dest_name="broken",
                                blocking_severities=["HIGH"])
    assert not (tmp_path / ".warden" / "rules" / "broken.md").exists()


def test_adopt_refuses_to_overwrite_an_existing_rule(tmp_path):
    autonomy_mod.adopt_rule(tmp_path, _rule("MEDIUM"), dest_name="new-lens",
                            blocking_severities=["HIGH"])
    with pytest.raises(autonomy_mod.AutonomyError) as exc:
        autonomy_mod.adopt_rule(tmp_path, _rule("MEDIUM", rid="new-lens"),
                                dest_name="new-lens",
                                blocking_severities=["HIGH"])
    assert "overwrite" in str(exc.value).lower()


# ---------- the declared rules dir, not the hardcoded default ---------------


def test_adopt_writes_into_the_declared_rules_dir(tmp_path):
    """Adopt writes into `review.rules_dir`, the dir the gate reads. A rule
    written into a hardcoded `.warden/rules` instead is INVISIBLE to the gate
    it was adopted into, a machine-tier action that silently no-ops. The gate
    must see what adopt writes."""
    (tmp_path / "repo.yaml").write_text(
        "review:\n  rules_dir: policy/rules\n")
    dest, _artifact = autonomy_mod.adopt_rule(tmp_path, _rule("MEDIUM"),
                                              dest_name="new-lens",
                                              blocking_severities=["HIGH"])
    assert dest == tmp_path / "policy" / "rules" / "new-lens.md"
    assert not (tmp_path / ".warden" / "rules").exists(), \
        "nothing may be written to the default dir the gate never reads"
    # the gate reads the declared dir — the adopted rule is visible there
    loaded = rules_mod.load_rules(tmp_path / "policy" / "rules")
    assert [r.id for r in loaded] == ["new-lens"]


def test_adopt_refuses_when_the_rules_dir_declaration_cannot_be_read(tmp_path):
    """Fail closed: an unreadable repo.yaml means the true rules dir is
    UNKNOWN, and writing into a guessed default is the same gate-invisible
    no-op with an extra step."""
    (tmp_path / "repo.yaml").write_text("review: [unclosed")
    with pytest.raises(autonomy_mod.AutonomyError) as exc:
        autonomy_mod.adopt_rule(tmp_path, _rule("MEDIUM"),
                                dest_name="new-lens",
                                blocking_severities=["HIGH"])
    assert "repo.yaml" in str(exc.value)
    assert not (tmp_path / ".warden" / "rules").exists()


def test_adopt_refuses_a_parseable_but_malformed_declaration(tmp_path):
    """A repo.yaml one indent error away from valid — `review:` as a LIST —
    parses fine and carries no problem. Writing into the guessed default there
    is the exact gate-invisible machine-tier write the refusal exists to
    stop."""
    (tmp_path / "repo.yaml").write_text("review:\n- rules_dir: policy/rules\n")
    with pytest.raises(autonomy_mod.AutonomyError, match="repo.yaml"):
        autonomy_mod.adopt_rule(tmp_path, _rule("MEDIUM"),
                                dest_name="new-lens",
                                blocking_severities=["HIGH"])
    assert not (tmp_path / ".warden" / "rules").exists()


# ---------- the adoption backtest -------------------------------------------


def _adopt(root, severity="MEDIUM", *, rid="new-lens", today="2026-09-01"):
    return autonomy_mod.adopt_rule(root, _rule(severity, rid),
                                   dest_name=rid,
                                   blocking_severities=["HIGH"], today=today)


def test_adopt_writes_an_adoption_backtest(tmp_path):
    """An adoption writes its own backtest artifact beside the rule file.

    Adding a rule file bumps rules_version, and certification S-05 requires
    SOME artifact stamped with the CURRENT version — so a tree where
    `warden autonomy adopt` wrote only the rule file would FAIL S-05, with no
    tooling remedy for the machine tier: a granted consumer that merged a
    machine adoption would drop a certification level. The adoption carries
    its own artifact, exactly as the pause arm does.
    """
    dest, artifact = _adopt(tmp_path)
    assert dest.exists() and artifact.exists()
    assert artifact.parent == tmp_path / ".warden" / "memory" / "backtests"
    doc = json.loads(artifact.read_text())
    assert doc["action"] == "adopt"
    assert doc["rule_id"] == "new-lens"
    assert doc["severity"] == "MEDIUM"
    # The version stamped is the one the ADDED rule produces, not the one it
    # left — the same rule apply_pause follows, and what S-05 matches on.
    assert doc["rules_version"] == rules_mod.rules_version(
        tmp_path / ".warden" / "rules", tmp_path)
    assert doc["note"].strip()


def test_the_adoption_backtest_declares_no_tally_it_cannot_derive(tmp_path):
    """The counts an adoption can honestly declare are none: the rule has
    never produced a finding, so there is no judged/refuted history, and an
    `engine: claude` rule cannot be replayed over the tree to measure hits.
    Declaring a tally here would be the fabricated-evidence shape the
    carve-out's narrowing exists to refuse — from the writer's side."""
    _dest, artifact = _adopt(tmp_path)
    doc = json.loads(artifact.read_text())
    assert doc["judged"] == 0, (
        "an adoption declared a judged tally it cannot have derived")
    for absent in ("upheld", "refuted", "dismissed", "rate", "wilson_lb"):
        assert absent not in doc, (
            f"the adoption artifact declares {absent!r} — an adoption has no "
            "precision history, and a zeroed tally reads as one that was "
            "measured")
    assert "no judged history" in doc["derived_from"]


def test_a_refused_adoption_writes_no_artifact(tmp_path):
    """A refusal leaves NOTHING behind: an artifact for a rule that was never
    adopted is evidence for a change the tree does not contain."""
    with pytest.raises(autonomy_mod.AutonomyError):
        _adopt(tmp_path, "HIGH")
    bt = tmp_path / ".warden" / "memory" / "backtests"
    assert not bt.exists() or not list(bt.glob("*.json"))


# ---------- both halves or neither ------------------------------------------


def test_c14_a_failed_artifact_write_rolls_the_rule_file_back(tmp_path,
                                                              monkeypatch):
    """`adopt_rule` writes the rule file first and the artifact second, so an
    OSError on the artifact must roll the rule file back and surface as an
    `AutonomyError`, not escape as a bare OSError past the CLI's handler. The
    alternative is a rule file on disk with no artifact and no run artifact
    recording it — exactly the rule-without-evidence tree the adoption
    backtest exists to prevent, produced by the command that writes it."""
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(autonomy_mod, "_write_adopt_backtest", boom)
    with pytest.raises(autonomy_mod.AutonomyError) as exc:
        _adopt(tmp_path)
    assert "rolled back" in str(exc.value) and "disk full" in str(exc.value)
    assert not (tmp_path / ".warden" / "rules" / "new-lens.md").exists(), (
        "the rule file survived an adoption whose artifact could not be "
        "written — a rules_version bump with nothing evidencing it")


def test_c14_adopt_refuses_to_overwrite_an_existing_backtest(tmp_path):
    """The artifact write refuses to overwrite, as the rule write does; a
    silent clobber is not allowed. An adoption ADDS its evidence; it never
    edits evidence already on disk."""
    _adopt(tmp_path)
    (tmp_path / ".warden" / "rules" / "new-lens.md").unlink()
    with pytest.raises(autonomy_mod.AutonomyError) as exc:
        _adopt(tmp_path)
    assert "overwrite an existing backtest" in str(exc.value)


def test_c14_adopt_refuses_a_rule_whose_id_is_not_its_file_name(tmp_path):
    """The gate loads rules BY FILE and the carve-out refuses the mismatch, so
    a writer that allowed one produced a tree its own reader auto-closes."""
    with pytest.raises(autonomy_mod.AutonomyError) as exc:
        autonomy_mod.adopt_rule(tmp_path, _rule("MEDIUM", rid="other"),
                                dest_name="new-lens",
                                blocking_severities=["HIGH"])
    assert "declares id 'other'" in str(exc.value)
    assert not (tmp_path / ".warden" / "rules").exists()


@pytest.mark.parametrize("rid", ["a/b", "..", "../escape", ".hidden"])
def test_c14_adopt_refuses_an_id_that_is_not_a_plain_file_name(tmp_path, rid):
    """The id becomes a file name for BOTH halves an adoption writes, and
    `rules._parse` validates its presence, never its shape.

    The assertion is on the OUTCOME, not the wording: a separator-bearing id
    is refused earlier, by the loader that cannot find the rule where the id
    put it, and a leading dot by the shape guard. Both refuse and both leave
    the tree untouched, which is the property; pinning which message fires
    would pin an accident of ordering."""
    with pytest.raises(autonomy_mod.AutonomyError):
        autonomy_mod.adopt_rule(tmp_path, _rule("MEDIUM", rid=rid),
                                dest_name=rid, blocking_severities=["HIGH"])
    assert not list(tmp_path.rglob("*.md")), "a refused adoption wrote a rule"
    assert not list(tmp_path.rglob("*.json")), (
        "a refused adoption wrote a backtest artifact")


# ---------- the writer refuses what its own reader refuses ------------------


def _rule_with(field: str, value: str, rid: str = "new-lens") -> str:
    """A MEDIUM draft that ALSO declares `covers:` or `implements:` — the two
    frontmatter fields that raise enforcement at a severity that cannot fail
    a build."""
    return _rule("MEDIUM", rid).replace(
        "---\nreport", f'{field}: ["{value}"]\n---\nreport')


@pytest.mark.parametrize("field,value", [
    ("covers", "docs-drift"),
    ("implements", "supply-chain-pinning"),
])
def test_a_covers_or_implements_draft_is_refused_at_the_writer(
        tmp_path, field, value):
    """`_adoption_problem` (the reader the carve-out and certify run) refuses
    a rule that declares `covers:` or `implements:`, because either raises
    enforcement whatever the severity. The writer refuses the same draft up
    front, with the SAME sentence the reader would print, and writes nothing,
    so the operator does not first learn of the refusal when the carve-out
    auto-closes the PR."""
    src = _rule_with(field, value)
    with pytest.raises(autonomy_mod.AutonomyError) as exc:
        autonomy_mod.adopt_rule(tmp_path, src, dest_name="new-lens",
                                blocking_severities=["HIGH"])
    assert field in str(exc.value), exc.value
    # the same sentence — one helper, two seams, so they cannot drift
    problem, _severity = autonomy_mod._adoption_problem(
        src, "new-lens", frozenset(["HIGH"]))
    assert problem and problem in str(exc.value), (
        f"the writer's refusal does not carry the reader's sentence:\n"
        f"  writer: {exc.value}\n  reader: {problem}")
    assert not list(tmp_path.rglob("*.md")), "a refused adoption wrote a rule"
    assert not list(tmp_path.rglob("*.json")), (
        "a refused adoption wrote a backtest artifact")


def test_a_plain_draft_still_writes_both_halves(tmp_path):
    """The other half of the guard: a draft that declares NEITHER field —
    including one that spells them out as empty lists — still writes the rule
    and its artifact, and the reader clears the same bytes. Nothing changed
    in what the reader accepts."""
    src = _rule("MEDIUM").replace(
        "---\nreport", "covers: []\nimplements: []\n---\nreport")
    dest, artifact = autonomy_mod.adopt_rule(tmp_path, src, dest_name="new-lens",
                                             blocking_severities=["HIGH"])
    assert dest.exists() and artifact.exists()
    problem, severity = autonomy_mod._adoption_problem(
        dest.read_text(), "new-lens", frozenset(["HIGH"]))
    assert problem is None and severity == "MEDIUM"


@pytest.mark.parametrize("target", ["rule", "artifact"])
def test_c14_a_dangling_symlink_is_not_an_absent_file(tmp_path, target):
    """A dangling symlink at either destination is refused, not written
    through.

    `Path.exists()` FOLLOWS a symlink, so a dangling one reads as absent: the
    overwrite refusal would not fire, the write would go THROUGH the link to a
    path outside the dirs the carve-out covers, and the rollback would remove
    only the link — leaving behind exactly the file the "both halves or
    neither" guarantee says cannot exist. An adoption writes a new regular
    file or it refuses.
    """
    import os

    outside = tmp_path / "OUTSIDE.txt"
    if target == "rule":
        link = tmp_path / ".warden" / "rules" / "new-lens.md"
    else:
        link = (tmp_path / ".warden" / "memory" / "backtests"
                / "new-lens-adopt-20260901.json")
    link.parent.mkdir(parents=True)
    os.symlink(outside, link)
    assert not link.exists() and link.is_symlink(), "fixture is not dangling"

    with pytest.raises(autonomy_mod.AutonomyError) as exc:
        _adopt(tmp_path)
    assert "symlink" in str(exc.value), exc.value
    assert not outside.exists(), (
        f"the adoption wrote through a dangling symlink to {outside}")
