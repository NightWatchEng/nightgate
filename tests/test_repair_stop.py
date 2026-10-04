"""The repair stop condition is computed, not argued.

A round counter cannot tell "the change still has defects" from "the repair
scaffolding has defects". So the cap counts CHANGE-DEFECT rounds — an open
finding on the ORIGINAL
diff, or any open finding above LOW — and a round whose open findings are all
LOW and all on files only the repair commits wrote is a SCAFFOLDING round that
ends the review loop. Both signals come from the round manifests'
base_sha/head_sha chain, which `warden round new` already writes, and from the
findings payload `attest write` already consumes. The builder never
classifies its own diff: `warden round classify` does, and these tests are
what make "computed" mean something.

The fixture is a real git repository, because the original-diff range is a
merge-base (three-dot) diff and the head chain is proved by ancestry — neither
of which a fake would exercise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from warden import repair as repair_mod
from warden import runs as runs_mod
from conftest import REPO_YAML  # noqa: E402

ROOT = Path(__file__).parent.parent


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout


def _sha(repo: Path, ref: str = "HEAD") -> str:
    return _git(repo, "rev-parse", ref).strip()


@pytest.fixture
def repo(tmp_path):
    """main: a.txt, b.txt. feature: C1 rewrites a.txt and adds new.py (the
    ORIGINAL diff); main then moves (b.txt) so a two-dot diff would mis-count
    it; C2 is the REPAIR: adds tests/test_new.py and touches a.txt again."""
    r = tmp_path / "r"
    (r / ".warden").mkdir(parents=True)
    _git(r.parent, "init", "-q", "-b", "main", "r")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("one\n")
    (r / "b.txt").write_text("b\n")
    (r / "repo.yaml").write_text(REPO_YAML)
    # As the real repo does: round dirs are evidence run dirs, never tracked.
    # Without this, every `add -A` below sweeps the minted rounds into a
    # commit, and a checkout then deletes the manifest a later test reads.
    (r / ".gitignore").write_text(".warden/out/\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    _git(r, "checkout", "-q", "-b", "feature")
    (r / "a.txt").write_text("two\n")
    (r / "new.py").write_text("x = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "C1: the change")
    # main advances underneath the branch — b.txt is NOT part of the change
    _git(r, "checkout", "-q", "main")
    (r / "b.txt").write_text("b2\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "main moves")
    _git(r, "checkout", "-q", "feature")
    return r


def _mint(repo: Path, head: str, base_sha: str, round_no: int = 1) -> Path:
    """A minted round. `round_no` is what `warden round new` stamps into the
    manifest; `classify` never reads it — the chain is proved by ancestry —
    so these fixtures leave it at 1 except where a test is about the number
    itself."""
    d = runs_mod.create_round_dir(repo, branch="feature", head=head)
    runs_mod.write_round_manifest(d, base="main", base_sha=base_sha,
                                  head=head, branch="feature",
                                  round_no=round_no)
    return d


def _repair(repo: Path) -> None:
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_new.py").write_text("def test(): pass\n")
    (repo / "a.txt").write_text("three\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "C2: repair")


def _two_rounds(repo: Path) -> tuple[Path, Path]:
    base = _sha(repo, "main~1")
    r1 = _mint(repo, _sha(repo), base)
    _repair(repo)
    r2 = _mint(repo, _sha(repo), base)
    return r1, r2


def _f(file, severity="LOW", status="confirmed", **kw) -> dict:
    d = {"rule_id": "x", "severity": severity, "finding": "f",
         "evidence": "e", "status": status}
    if file is not None:
        d["file"] = file
    d.update(kw)
    return d


# ---------- the two ranges ---------------------------------------------------

def test_the_original_range_is_the_first_rounds_merge_base_diff(repo):
    """The original file-set is base_sha...head_sha of round 1 — three-dot,
    so a file main changed after the branch point (b.txt) is not the change's.
    The repair file-set is round 1's head to the latest head."""
    r1, r2 = _two_rounds(repo)
    report = repair_mod.classify(repo, [r1, r2], [])
    assert report.original_files == ("a.txt", "new.py")
    assert report.repair_files == ("a.txt", "tests/test_new.py")


def test_a_single_round_has_no_repair_range(repo):
    base = _sha(repo, "main~1")
    r1 = _mint(repo, _sha(repo), base)
    report = repair_mod.classify(repo, [r1], [_f("tests/test_new.py")])
    assert report.repair_files == ()
    # nothing has been repaired yet, so every finding is on the change
    assert [c for _, c in report.rows] == ["original"]
    assert report.verdict == "counts"


def test_the_original_range_is_a_merge_base_diff_when_main_has_moved(repo):
    """Round 1 minted AFTER main moved records main's CURRENT tip as
    base_sha — what `warden round new --base main` writes. Three-dot then
    excludes b.txt; two-dot would carry it in as a defect in the change.
    A fixture that recorded the merge-base itself as base_sha would never
    exercise the three-dot, so `...` -> `..` would survive mutation."""
    r1 = _mint(repo, _sha(repo), _sha(repo, "main"))
    _repair(repo)
    r2 = _mint(repo, _sha(repo), _sha(repo, "main"))
    report = repair_mod.classify(repo, [r1, r2], [_f("b.txt")])
    assert report.original_files == ("a.txt", "new.py")
    assert "b.txt" not in report.original_files
    # and b.txt is in neither range, so it is the fail-closed `original`
    assert [c for _, c in report.rows] == ["original"]


def test_the_original_base_comes_from_the_first_round_not_the_last(repo):
    """`base` is round 1's, because the ORIGINAL diff is what round 1 judged.
    Reading the latest round's base instead survives mutation whenever both
    rounds record the same base, so this fixture gives them different ones."""
    r1 = _mint(repo, _sha(repo), _sha(repo, "main~1"))
    _repair(repo)
    r2 = _mint(repo, _sha(repo), _sha(repo, "main"))
    report = repair_mod.classify(repo, [r1, r2], [])
    assert report.base_sha == _sha(repo, "main~1")


def test_two_rounds_at_one_head_naming_different_bases_are_refused(repo):
    """The original diff has ONE base. Two manifests at the same head naming
    different ones cannot both be it, and picking either silently would put a
    file in or out of the change by coin flip."""
    head = _sha(repo)
    r1 = _mint(repo, head, _sha(repo, "main~1"))
    r1b = _mint(repo, head, _sha(repo, "main"))
    with pytest.raises(repair_mod.RepairError, match="different bases"):
        repair_mod.classify(repo, [r1, r1b], [])


# ---------- classification ---------------------------------------------------

def test_a_low_on_a_file_only_the_repair_wrote_is_scaffolding(repo):
    """A round whose open findings are all LOW and on files a prior repair
    created OR REWROTE to close an earlier finding is a scaffolding round.
    That round ends the loop."""
    r1, r2 = _two_rounds(repo)
    report = repair_mod.classify(repo, [r1, r2], [_f("tests/test_new.py")])
    assert [c for _, c in report.rows] == ["repair"]
    assert report.verdict == "scaffolding-stop"


def test_a_low_on_the_original_diff_still_counts(repo):
    """'Continue while findings land on the ORIGINAL diff' — severity does
    not excuse a defect in the change itself."""
    r1, r2 = _two_rounds(repo)
    report = repair_mod.classify(repo, [r1, r2], [_f("new.py")])
    assert [c for _, c in report.rows] == ["original"]
    assert report.verdict == "counts"


@pytest.mark.parametrize("severity", ["MEDIUM", "HIGH"])
def test_anything_above_low_counts_wherever_it_lands(repo, severity):
    """The severity clause is what stops the classification being a
    laundering path: a repair that rewrites every original file cannot make a
    MEDIUM stop counting."""
    r1, r2 = _two_rounds(repo)
    report = repair_mod.classify(
        repo, [r1, r2], [_f("tests/test_new.py", severity=severity)])
    assert [c for _, c in report.rows] == ["repair"]
    assert report.verdict == "counts"
    assert any("above LOW" in r for r in report.reasons)


def test_a_file_in_both_ranges_is_repair_written(repo):
    """a.txt is in the change AND the repair touched it. It classifies as
    `both`, which the verdict reads as repair-written — the measurement's
    own definition ('a file a prior repair commit wrote'), stated so the
    number the ruling cites applies to the mechanism that enforces it. The
    class is still reported as `both`, never folded, so a reader can see a
    repair that rewrote the whole change."""
    r1, r2 = _two_rounds(repo)
    report = repair_mod.classify(repo, [r1, r2], [_f("a.txt")])
    assert [c for _, c in report.rows] == ["both"]
    assert report.verdict == "scaffolding-stop"


@pytest.mark.parametrize("file", [None, "", "b.txt", "docs/never-touched.md"])
def test_an_unclassifiable_finding_is_original_and_counts(repo, file):
    """Fail-closed: a finding with no file, or on a file neither range
    changed (rename-complete and wiki-fidelity produce these by design), can
    never trigger the early stop. Unknown is not scaffolding."""
    r1, r2 = _two_rounds(repo)
    report = repair_mod.classify(repo, [r1, r2],
                                 [_f(file), _f("tests/test_new.py")])
    assert [c for _, c in report.rows] == ["original", "repair"]
    assert report.verdict == "counts"


def test_an_unknown_severity_is_read_as_above_low(repo):
    """A severity the schema does not name is not LOW. 'Could not rank' must
    not read as 'ranked lowest'."""
    r1, r2 = _two_rounds(repo)
    report = repair_mod.classify(
        repo, [r1, r2], [_f("tests/test_new.py", severity="low-ish")])
    assert report.verdict == "counts"


# ---------- which findings are open -----------------------------------------

@pytest.mark.parametrize("status", ["fixed", "refuted", "dismissed-with-reason"])
def test_a_closed_finding_is_not_open(repo, status):
    r1, r2 = _two_rounds(repo)
    report = repair_mod.classify(
        repo, [r1, r2],
        [_f("new.py", severity="HIGH", status=status, reason="r")])
    assert report.rows == ()
    assert report.verdict == "clean"


def test_a_finding_with_no_status_is_open(repo):
    """Fail-closed on the other axis: a finding that never says it was
    closed is open. Absence is not resolution."""
    r1, r2 = _two_rounds(repo)
    f = _f("new.py")
    del f["status"]
    report = repair_mod.classify(repo, [r1, r2], [f])
    assert [c for _, c in report.rows] == ["original"]
    assert report.verdict == "counts"


def test_no_open_findings_ends_the_loop_as_clean_not_as_scaffolding(repo):
    """An empty set satisfies 'every open finding is LOW and repair-written'
    vacuously. The verdict says CLEAN, so nobody reads a clean round as a
    scaffolding stop and goes looking for findings to adjudicate."""
    r1, r2 = _two_rounds(repo)
    report = repair_mod.classify(repo, [r1, r2], [])
    assert report.verdict == "clean"


def test_the_payload_shape_attest_consumes_is_accepted(repo):
    """The findings file is the attestation payload (`{"findings": [...]}`)
    or a bare list — the same file `attest write --findings` reads, so no
    second findings file exists to go stale."""
    r1, r2 = _two_rounds(repo)
    payload = {"reviewers": [], "verdict": "clean",
               "findings": [_f("tests/test_new.py")]}
    report = repair_mod.classify(repo, [r1, r2],
                                 repair_mod.findings_from(payload))
    assert report.verdict == "scaffolding-stop"
    assert repair_mod.findings_from([_f("x")]) == [_f("x")]
    for bad in ({"no": "findings"}, {"findings": "str"}, 3, [1]):
        with pytest.raises(repair_mod.RepairError):
            repair_mod.findings_from(bad)


# ---------- the round chain --------------------------------------------------

def test_rounds_are_ordered_by_ancestry_not_by_argument_order(repo):
    """The chain is proved from the shas: each round's head is a descendant
    of the previous. Argument order is not evidence, and no clock is read."""
    r1, r2 = _two_rounds(repo)
    a = repair_mod.classify(repo, [r1, r2], [_f("tests/test_new.py")])
    b = repair_mod.classify(repo, [r2, r1], [_f("tests/test_new.py")])
    assert a.verdict == b.verdict == "scaffolding-stop"
    assert a.original_files == b.original_files
    assert a.repair_files == b.repair_files


def test_a_round_from_another_line_of_history_is_refused(repo):
    """A round whose head is not on this chain describes another branch.
    Refused, never averaged in."""
    r1, r2 = _two_rounds(repo)
    _git(repo, "checkout", "-q", "-b", "other", "main")
    (repo / "c.txt").write_text("c\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "elsewhere")
    stray = _mint(repo, _sha(repo), _sha(repo, "main"))
    _git(repo, "checkout", "-q", "feature")
    with pytest.raises(repair_mod.RepairError, match="not a descendant"):
        repair_mod.classify(repo, [r1, r2, stray], [])


def test_two_rounds_at_one_head_are_one_point_on_the_chain(repo):
    """A scoped re-review at the same head is not a second repair range."""
    r1, r2 = _two_rounds(repo)
    r2b = _mint(repo, _sha(repo), _sha(repo, "main~1"))
    report = repair_mod.classify(repo, [r1, r2, r2b], [_f("tests/test_new.py")])
    assert report.verdict == "scaffolding-stop"
    assert report.heads == (_sha(repo, "HEAD~1"), _sha(repo))


def test_a_round_manifest_that_cannot_be_read_is_refused(repo, tmp_path):
    """The same contract `round_manifest_for` keeps: an unreadable identity
    is not an absent one, and an absent one is not a round."""
    r1, r2 = _two_rounds(repo)
    (r2 / "round.json").write_text("{not json")
    with pytest.raises(repair_mod.RepairError):
        repair_mod.classify(repo, [r1, r2], [])
    unminted = tmp_path / "somewhere"
    unminted.mkdir()
    with pytest.raises(repair_mod.RepairError, match="did not mint"):
        repair_mod.classify(repo, [r1, unminted], [])


def test_a_manifest_whose_sha_git_cannot_resolve_is_refused(repo):
    """A manifest naming a commit this repository does not have describes a
    round that happened somewhere else."""
    base = _sha(repo, "main~1")
    r1 = _mint(repo, "f" * 40, base)
    with pytest.raises(repair_mod.RepairError):
        repair_mod.classify(repo, [r1], [])


def test_no_rounds_is_refused(repo):
    with pytest.raises(repair_mod.RepairError, match="no round"):
        repair_mod.classify(repo, [], [])


def test_a_merge_of_the_base_branch_between_rounds_is_not_the_repair(repo):
    """THE regression. `git merge main` mid-repair is the only sync path the
    descendant check leaves open, and computing the repair set as `H1...Hn`
    would make every file main moved read as repair-written — so a LOW on a
    file NOBODY on this branch wrote would end the full-crew loop, the exact
    case the ruling says never triggers the early stop. The repair set is the
    union of the repair COMMITS' own diffs, and a merge contributes nothing."""
    r1 = _mint(repo, _sha(repo), _sha(repo, "main"))
    _repair(repo)
    _git(repo, "merge", "-q", "--no-edit", "main")
    r2 = _mint(repo, _sha(repo), _sha(repo, "main"))
    report = repair_mod.classify(repo, [r1, r2], [_f("b.txt")])
    assert "b.txt" not in report.repair_files, (
        "a file the base branch wrote is not repair scaffolding")
    assert report.repair_files == ("a.txt", "tests/test_new.py")
    assert [c for _, c in report.rows] == ["original"]
    assert report.verdict == "counts"
    # and the repair's own files still classify as the repair's
    scaffold = repair_mod.classify(repo, [r1, r2], [_f("tests/test_new.py")])
    assert scaffold.verdict == "scaffolding-stop"


@pytest.mark.parametrize("how", ["fast-forward", "squash", "cherry-pick"])
def test_no_sync_route_makes_the_base_branch_read_as_the_repair(repo, how):
    """`--first-parent` closes the ordinary merge and nothing else. A
    fast-forward `git merge main`, a `git merge --squash main`, and a
    `cherry-pick` off the base branch all put base-branch work on the
    FIRST-parent chain, and each would make a LOW on a file nobody on this
    branch wrote classify `repair` and end the full-crew loop. Two
    subtractions close them: a commit reachable from a recorded base, and a
    file the base branch itself moved between recorded bases."""
    first_base = _sha(repo, "main")
    r1 = _mint(repo, _sha(repo), first_base)
    _repair(repo)
    # main moves again, carrying a file this branch has never touched
    _git(repo, "checkout", "-q", "main")
    (repo / "docs.md").write_text("upstream\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main writes docs.md")
    upstream = _sha(repo)
    _git(repo, "checkout", "-q", "feature")
    if how == "fast-forward":
        # the branch's own work is already on main, so the sync is a
        # fast-forward and main's commits become first-parent commits here
        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "--no-ff", "--no-edit", "feature")
        _git(repo, "checkout", "-q", "feature")
        _git(repo, "merge", "-q", "--ff-only", "main")
    elif how == "squash":
        _git(repo, "merge", "-q", "--squash", "main")
        _git(repo, "commit", "-qm", "squash main")
    else:
        _git(repo, "cherry-pick", upstream)
    r2 = _mint(repo, _sha(repo), _sha(repo, "main"))
    report = repair_mod.classify(repo, [r1, r2], [_f("docs.md")])
    assert "docs.md" not in report.repair_files, (
        f"{how}: a file only the base branch wrote is not repair scaffolding")
    assert [c for _, c in report.rows] == ["original"]
    assert report.verdict == "counts"


def test_a_side_branch_merged_into_the_repair_is_not_first_parent(repo):
    """`--first-parent` is the load-bearing flag and this is what binds it.
    A side branch merged into the repair line is neither reachable from a
    recorded base nor a file the base branch moved, so neither subtraction
    can mask it — deleting `--first-parent` is caught here and nowhere
    else. The documented behaviour it pins: what a merge brought in is NOT
    repair-written, so it lands in `original` and counts."""
    base = _sha(repo, "main~1")
    r1 = _mint(repo, _sha(repo), base)
    _repair(repo)
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "side.md").write_text("from a side branch\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "side work")
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "side")
    r2 = _mint(repo, _sha(repo), base)
    report = repair_mod.classify(repo, [r1, r2], [_f("side.md")])
    assert "side.md" not in report.repair_files, (
        "a second parent's work is not on the first-parent chain")
    assert report.repair_files == ("a.txt", "tests/test_new.py")
    assert [c for _, c in report.rows] == ["original"]
    assert report.verdict == "counts"


def test_a_base_branch_commit_the_base_later_undid_is_still_not_the_repair(repo):
    """The two subtractions are not redundant. `git diff bases[0] bases[1]`
    compares TREES, so a file the base branch added and then removed does not
    appear in it — and a fast-forward puts the adding commit on the
    first-parent chain, where `diff-tree` names the file. Only the
    reachable-from-a-recorded-base check drops it. Without this case the
    check could be mutated out with the suite green."""
    first_base = _sha(repo, "main")
    r1 = _mint(repo, _sha(repo), first_base)
    _repair(repo)
    _git(repo, "checkout", "-q", "main")
    (repo / "transient.md").write_text("upstream\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main adds transient.md")
    (repo / "transient.md").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main removes transient.md again")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "feature")
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "merge", "-q", "--ff-only", "main")
    later_base = _sha(repo, "main")
    assert "transient.md" not in repair_mod._changed(repo, first_base, later_base), (
        "the fixture must exercise the file-set gap, not paper over it")
    r2 = _mint(repo, _sha(repo), later_base)
    report = repair_mod.classify(repo, [r1, r2], [_f("transient.md")])
    assert "transient.md" not in report.repair_files
    assert [c for _, c in report.rows] == ["original"]
    assert report.verdict == "counts"


def test_a_round_head_minted_on_the_base_branch_is_not_the_repair(repo):
    """The fourth route: round 2 minted on main after the branch was merged
    INTO it. main's own commits are then first-parent commits of the round-2
    head."""
    first_base = _sha(repo, "main")
    r1 = _mint(repo, _sha(repo), first_base)
    _repair(repo)
    _git(repo, "checkout", "-q", "main")
    (repo / "docs.md").write_text("upstream\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main writes docs.md")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "feature")
    r2 = _mint(repo, _sha(repo), _sha(repo, "main"))
    report = repair_mod.classify(repo, [r1, r2], [_f("docs.md")])
    assert "docs.md" not in report.repair_files
    assert report.verdict == "counts"


def test_a_first_round_whose_base_is_its_own_head_is_refused(repo):
    """An empty original diff makes EVERY later file repair-written, so any
    set of open LOWs would end the review loop over a change the checker
    never saw. Could-not-see is refused, not read as clean."""
    head = _sha(repo)
    r1 = _mint(repo, head, head)
    _repair(repo)
    r2 = _mint(repo, _sha(repo), _sha(repo, "main~1"))
    with pytest.raises(repair_mod.RepairError, match="names its own head"):
        repair_mod.classify(repo, [r1, r2], [_f("tests/test_new.py")])


def test_a_round_path_that_is_not_a_directory_is_refused(repo):
    """`round_manifest_for` falls back to the PARENT's round.json by design,
    so a mistyped `--round` would resolve to its parent round and produce a
    verdict. A `--round` that is not a directory is refused: could-not-look
    must not read as looked."""
    r1, r2 = _two_rounds(repo)
    for bad in (r2 / "does-not-exist", r2 / "reviewers" / "nope.json"):
        with pytest.raises(repair_mod.RepairError, match="no such round"):
            repair_mod.classify(repo, [r1, bad], [])


def test_a_later_rounds_unresolvable_base_is_refused_too(repo):
    """The 'happened somewhere else' refusal applies to every round, not only
    the round at the first head: a later round naming a base this repository
    does not have is refused too."""
    base = _sha(repo, "main~1")
    r1 = _mint(repo, _sha(repo), base)
    _repair(repo)
    r2 = _mint(repo, _sha(repo), "e" * 40)
    with pytest.raises(repair_mod.RepairError, match="is not a commit"):
        repair_mod.classify(repo, [r1, r2], [])


def test_the_manifest_clock_does_not_order_the_chain(repo):
    """The wall clock does not order the round chain. The order-independence
    test alone cannot tell ancestry from a `created` sort, since a timestamp
    order is argument-order independent too — so the manifests are written
    with `created` in the WRONG order and the chain must still come out
    right."""
    r1, r2 = _two_rounds(repo)
    for d, created in ((r1, "2030-01-01T00:00:00Z"), (r2, "2000-01-01T00:00:00Z")):
        doc = json.loads((d / "round.json").read_text())
        doc["created"] = created
        (d / "round.json").write_text(json.dumps(doc, indent=2) + "\n")
    report = repair_mod.classify(repo, [r2, r1], [_f("tests/test_new.py")])
    assert report.heads == (_sha(repo, "HEAD~1"), _sha(repo))
    assert report.original_files == ("a.txt", "new.py")
    assert report.verdict == "scaffolding-stop"


# ---------- the CLI ----------------------------------------------------------

def _cli(repo: Path, *argv: str, timeout: float | None = None
         ) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c",
         "import sys; from warden import cli; "
         f"sys.exit(cli.main({list(argv)!r}))"],
        cwd=repo, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "PYTHONPATH": str(ROOT)})


def test_round_classify_exit_codes_say_whether_the_round_counts(repo):
    """0: the review loop ends (clean, or scaffolding stop). 1: this round
    counts against the cap. 2: refused — the chain or the payload could not
    be read, which is never folded into a verdict."""
    r1, r2 = _two_rounds(repo)
    findings = repo / "payload.json"

    findings.write_text(json.dumps({"findings": [_f("tests/test_new.py")]}))
    out = _cli(repo, "round", "classify", "--findings", str(findings),
               "--round", str(r1), "--round", str(r2))
    assert out.returncode == 0, out.stderr
    assert "scaffolding-stop" in out.stdout
    assert "tests/test_new.py" in out.stdout and "repair" in out.stdout

    findings.write_text(json.dumps({"findings": [_f("new.py")]}))
    out = _cli(repo, "round", "classify", "--findings", str(findings),
               "--round", str(r1), "--round", str(r2))
    assert out.returncode == 1, out.stderr
    assert "counts" in out.stdout and "original" in out.stdout

    findings.write_text(json.dumps({"findings": []}))
    out = _cli(repo, "round", "classify", "--findings", str(findings),
               "--round", str(r1), "--round", str(r2))
    assert out.returncode == 0, out.stderr
    assert "clean" in out.stdout

    findings.write_text("{not json")
    out = _cli(repo, "round", "classify", "--findings", str(findings),
               "--round", str(r1), "--round", str(r2))
    assert out.returncode == 2
    assert "could not read" in out.stderr

    (r2 / "round.json").unlink()
    findings.write_text(json.dumps({"findings": []}))
    out = _cli(repo, "round", "classify", "--findings", str(findings),
               "--round", str(r1), "--round", str(r2))
    assert out.returncode == 2
    assert "did not mint" in out.stderr


def test_the_report_names_both_ranges_so_a_reader_can_recompute(repo):
    """The classification is only as trustworthy as its inputs are visible:
    the rendered report states the original and repair ranges by sha and
    lists both file-sets."""
    r1, r2 = _two_rounds(repo)
    report = repair_mod.classify(repo, [r1, r2], [_f("tests/test_new.py")])
    text = repair_mod.render(report)
    assert _sha(repo, "main~1")[:12] in text
    assert _sha(repo, "HEAD~1")[:12] in text and _sha(repo)[:12] in text
    assert "new.py" in text and "tests/test_new.py" in text
    assert "scaffolding-stop" in text

    # the fail-closed note: a finding in NEITHER range is reported as such,
    # not silently rendered as though it were on the change's own files
    outside = repair_mod.classify(repo, [r1, r2], [_f("docs/never-touched.md")])
    assert "outside both ranges" in repair_mod.render(outside)

    # and a single round says there is no repair range yet, rather than
    # printing an empty one that reads as "the repair wrote nothing"
    r_only = _mint(repo, _sha(repo, "HEAD~1"), _sha(repo, "main~1"))
    single = repair_mod.classify(repo, [r_only], [_f("new.py")])
    assert "nothing repaired yet" in repair_mod.render(single)


def test_a_scaffolding_stop_offers_no_further_review_round(repo):
    """Named regression: review is capped at two rounds and a scaffolding stop
    can only arrive at the second, so the stop's instruction must park what is
    open rather than send the builder to dispatch another re-reviewer."""
    r1, r2 = _two_rounds(repo)
    report = repair_mod.classify(repo, [r1, r2], [_f("tests/test_new.py")])
    assert report.verdict == "scaffolding-stop"
    verdict = [ln for ln in repair_mod.render(report).splitlines()
               if ln.strip().startswith("verdict:")]
    assert len(verdict) == 1
    assert "re-reviewer" not in verdict[0]
    assert "park" in verdict[0]

# ---------- the protocol text that binds the checker ------------------------

_POLICY = ROOT / ".warden" / "skills-policy.md"


def _flat(text: str) -> str:
    """Prose here is hard-wrapped; pins are about wording, not line breaks."""
    return " ".join(text.split())


def test_the_policy_defines_an_attempt_as_a_change_defect_round():
    """The definition lives in `## Review charter`'s round contract, where
    #378 moved it; `## Verify` keeps the budget key, a pointer at the charter
    and the measurement that scopes round 2. Each half is read in its own
    section, so deleting the definition from the charter is red even while
    `## Verify`'s one-line summary survives."""
    text = _POLICY.read_text()
    # Anchored on the line start: `## Verify` mentions `## Review charter`
    # in backticks, and a bare index() would start the slice there.
    charter = text[text.index("\n## Review charter"):text.index("\n## Integrations")]
    attempt = _flat(charter[charter.index("**What an attempt is**"):
                            charter.index("**Review depth")])
    for phrase in ("change-defect round", "open COUNTING finding on the original diff",
                   "warden round classify", "does not count"):
        assert phrase in attempt, f"the charter's attempt definition lost {phrase!r}"
    verify = _flat(text[text.index("## Verify"):text.index("## Autonomy scope")])
    # The budget itself is repo.yaml's `repair.budget`, read by certify R-12
    # (tests/test_repair_budget.py); `## Verify` points at the charter for
    # what it counts and keeps the measurement.
    for phrase in ("`## Review charter`", "0 of 28", "12 of 13"):
        assert phrase in verify, f"the policy's budget paragraph lost {phrase!r}"


# ---------- a round that was never minted ------------------------------------

def test_a_payload_declaring_more_rounds_than_were_minted_is_refused(repo):
    """If the crew's first round is never minted, the earliest MINTED round is
    round 2 and the original range silently becomes `base...C2` — the true
    original PLUS round 1's own repair commit. The repair's own file then
    reads as the change's and the round counts.

    An unminted round leaves no artifact, so nothing in the manifests can
    see it. What CAN see it is the payload: it declares the rounds the
    review ran. A declared round with no round directory behind it is a
    chain this command cannot measure, and it refuses rather than measuring
    the wrong range (exit 2 — never a stop condition met).
    """
    base = _sha(repo, "main~1")
    _repair(repo)                       # C2, the repair for round 1's findings
    r2 = _mint(repo, _sha(repo), base)  # the earliest MINTED round is round 2
    findings = [_f("new.py", status="fixed", round=1, lens="code-reviewer"),
                _f("tests/test_new.py", severity="MEDIUM", round=2,
                   lens="code-reviewer")]
    with pytest.raises(repair_mod.RepairError) as e:
        repair_mod.classify(repo, [r2], findings)
    msg = str(e.value)
    assert "round 2" in msg and "1 round director" in msg
    assert "warden round new" in msg, (
        "the refusal no longer says how to fix the chain: mint every round")


def test_the_same_review_with_round_1_minted_classifies_normally(repo):
    """The control: mint round 1 and the identical findings measure the way
    the ruling says they should — the repair's own file is repair-written,
    not the change's. The guard fires on the missing mint, not on the
    findings."""
    r1, r2 = _two_rounds(repo)
    findings = [_f("new.py", status="fixed", round=1, lens="code-reviewer"),
                _f("tests/test_new.py", round=2, lens="code-reviewer")]
    report = repair_mod.classify(repo, [r1, r2], findings)
    assert [c for _, c in report.rows] == ["repair"]
    assert report.verdict == "scaffolding-stop"


def test_a_payload_that_declares_no_round_is_measured_as_before(repo):
    """A legacy payload — a pinned warden predating the lens/round fields
    writes findings with no `round` at all — declares nothing about the
    chain, so there is nothing to disprove. It classifies exactly as it did
    before this guard existed."""
    r1, r2 = _two_rounds(repo)
    report = repair_mod.classify(repo, [r1, r2], [_f("tests/test_new.py")])
    assert report.verdict == "scaffolding-stop"


def test_a_stated_round_warden_cannot_read_is_refused_not_ignored(repo):
    """`2.0` and `"2"` are stated rounds in the wrong shape, and reading a
    stated value as absent is what lets the count skip the comparison —
    `attest._round_of` refuses the same two spellings for the same reason."""
    r1, r2 = _two_rounds(repo)
    for bad in (2.0, "2", True):
        with pytest.raises(repair_mod.RepairError) as e:
            repair_mod.classify(repo, [r1, r2],
                                [_f("new.py", round=bad)])
        assert "whole number" in str(e.value)


def test_a_round_below_one_is_refused_not_counted(repo):
    """The count compares the HIGHEST declared round against the rounds
    minted, so a payload numbering its rounds from 0 would declare `1` for
    its second round and clear a chain of one with the guard passing. The
    schema's floor (`minimum: 1`) is owned by a schema `round classify`
    never runs, so the floor is asserted here."""
    base = _sha(repo, "main~1")
    _repair(repo)
    r2 = _mint(repo, _sha(repo), base)
    for bad in (0, -3):
        with pytest.raises(repair_mod.RepairError) as e:
            repair_mod.classify(repo, [r2], [_f("new.py", round=bad),
                                             _f("tests/test_new.py", round=bad + 1)])
        assert "round 1" in str(e.value) or "below 1" in str(e.value)


def test_one_round_directory_passed_twice_is_one_round(repo):
    """The dedupe is load-bearing — the same directory named twice is one
    round, and without it a payload declaring round 2 clears a chain of one
    round passed twice.

    The second spelling goes THROUGH a child and back (`<round>/reviewers/..`),
    which pathlib keeps as written. `<round>/.` would not do: pathlib
    normalises it away at parse time, so the set alone would dedupe it and
    `.resolve()` could be deleted with the file green."""
    base = _sha(repo, "main~1")
    _repair(repo)
    r2 = _mint(repo, _sha(repo), base)
    same = r2 / "reviewers" / ".."       # a different Path, the same round
    assert same != r2, "pathlib normalised the spelling away — pick another"
    with pytest.raises(repair_mod.RepairError) as e:
        repair_mod.classify(repo, [r2, same],
                            [_f("tests/test_new.py", round=2)])
    assert "1 round directory was given" in str(e.value)


def test_the_report_names_how_many_commits_the_original_range_holds(repo):
    """The original range is rendered at COMMIT granularity too, so a reader
    can see what it spans rather than only which files it touched — the
    number an unminted round inflates."""
    r1, r2 = _two_rounds(repo)
    text = repair_mod.render(repair_mod.classify(repo, [r1, r2], []))
    assert "1 commit" in text


# ---------- the chain's own preconditions ------------------------------------
#
# Both subtractions in `_repair_files` depend on some round having recorded a
# base that already contains the synced base-branch work. These tests pin that
# precondition: a stale recorded base, bases that do not advance, a base that
# absorbed the branch, and a ref base that did.


def _advance_main(repo: Path, name: str) -> str:
    """Move main by one commit that writes `name`, and come back. Returns the
    new main sha. The file is one NOBODY on the branch has ever touched, so a
    finding on it classifying `repair` is unambiguous evidence of the defect."""
    _git(repo, "checkout", "-q", "main")
    (repo / name).write_text("upstream\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"main writes {name}")
    sha = _sha(repo)
    _git(repo, "checkout", "-q", "feature")
    return sha


@pytest.mark.parametrize("how", ["fast-forward", "squash", "cherry-pick"])
def test_a_stale_recorded_base_does_not_make_the_base_branch_the_repair(
        repo, how):
    """The recorded-base subtractions close the fast-forward, the squash and
    the cherry-pick only when a round RECORDED a base that already contains
    the synced work. Minting with `--base main` — the LOCAL ref — while a
    fetch moves only `origin/main` leaves every recorded base stale, and with
    stale bases a LOW on a file only the base branch wrote would classify
    `repair` and end the full-crew loop: exit 0 over a change nobody had
    finished reviewing.

    A THIRD subtraction does not depend on the recorded sha: the manifest
    also records the base REF NAME, so classify resolves it as it stands now.
    Strictly fail-closed — it can only move a file OUT of the repair set and
    into `original`, which counts."""
    stale = _sha(repo, "main")
    r1 = _mint(repo, _sha(repo), stale)
    _repair(repo)
    upstream = _advance_main(repo, "docs.md")
    if how == "fast-forward":
        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "--no-ff", "--no-edit", "feature")
        _git(repo, "checkout", "-q", "feature")
        _git(repo, "merge", "-q", "--ff-only", "main")
    elif how == "squash":
        _git(repo, "merge", "-q", "--squash", "main")
        _git(repo, "commit", "-qm", "squash main")
    else:
        _git(repo, "cherry-pick", upstream)
    # BOTH rounds record the stale sha; only the recorded ref name `main` reaches the synced work.
    r2 = _mint(repo, _sha(repo), stale)
    report = repair_mod.classify(repo, [r1, r2], [_f("docs.md")])
    assert "docs.md" not in report.repair_files, (
        f"{how}: a stale recorded base let a base-branch file into the repair "
        "set")
    assert [c for _, c in report.rows] == ["original"]
    assert report.verdict == "counts"


def test_a_later_round_naming_an_older_base_than_round_one_is_refused(repo):
    """`_changed` is a three-dot diff, so subtracting what the base branch
    moved between
    `bases[0]` and an OLDER base is a silent no-op — the subtraction runs,
    removes nothing, and the verdict reads as if it had been checked.

    A chain whose recorded bases do not advance is a chain classify cannot
    bound, so it REFUSES (exit 2) rather than classifying on the assumption.
    A 2 is never a stop condition met."""
    r1 = _mint(repo, _sha(repo), _sha(repo, "main"))
    _repair(repo)
    r2 = _mint(repo, _sha(repo), _sha(repo, "main~1"))
    with pytest.raises(repair_mod.RepairError, match="do not advance"):
        repair_mod.classify(repo, [r1, r2], [_f("tests/test_new.py")])


def test_an_unrelated_recorded_base_is_refused_not_averaged(repo):
    """The same refusal's other arm: a later round whose base is on no line of
    history the first round's base is on. Neither is an ancestor of the other,
    so nothing can say what the base branch did between them."""
    _git(repo, "checkout", "-q", "--orphan", "other")
    (repo / "z.txt").write_text("z\n")
    _git(repo, "add", "z.txt")
    _git(repo, "commit", "-qm", "unrelated root")
    unrelated = _sha(repo)
    _git(repo, "checkout", "-q", "feature")
    r1 = _mint(repo, _sha(repo), _sha(repo, "main"))
    _repair(repo)
    r2 = _mint(repo, _sha(repo), unrelated)
    with pytest.raises(repair_mod.RepairError, match="do not advance"):
        repair_mod.classify(repo, [r1, r2], [_f("tests/test_new.py")])


def test_an_absorbed_base_collapses_the_repair_set_fail_closed(repo):
    """A known limit, pinned rather than closed. Where the base branch has
    ABSORBED the branch and a round RECORDS that base, every repair commit is
    reachable from it and every repair path is inside `bases[0]...that base`,
    so `repair_files` collapses to `()` and the scaffolding stop cannot fire
    for that chain at all. The mechanism is dead in that shape — and it is
    dead in the fail-CLOSED direction: the findings land in `original`, which
    counts, so the cap keeps counting.

    Closing it means either a fail-OPEN widening (stop subtracting what an
    absorbed base moved, which lets squash- and cherry-picked base content
    back into the repair set) or refusing three sync routes this command
    classifies correctly. Pinned here so the limit is a tested property
    rather than a docstring sentence — and so a later change to it is a
    visible change of behaviour.

    An assertion that only the base-branch file is ABSENT from the repair set
    cannot see this, because an empty set satisfies it."""
    first_base = _sha(repo, "main")
    r1 = _mint(repo, _sha(repo), first_base)
    _repair(repo)
    # The branch syncs, then main absorbs it by fast-forward, so the repair's
    # own commits are reachable from the base the next round records AND sit on
    # the first-parent chain — nothing but the subtractions removes them.
    _git(repo, "merge", "-q", "--no-edit", "main")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "feature")
    _git(repo, "checkout", "-q", "feature")
    absorbed = _sha(repo, "main")
    assert absorbed == _sha(repo), "the fixture must actually absorb the branch"
    r2 = _mint(repo, _sha(repo), absorbed)
    report = repair_mod.classify(repo, [r1, r2], [_f("tests/test_new.py")])
    assert report.repair_files == (), (
        "the declared limit moved — an absorbed recorded base now leaves "
        "repair files, which widens the early stop and needs its own ruling")
    # The consequence, which is what makes it fail-CLOSED: a LOW the repair
    # itself wrote is read as the change's and the round counts.
    assert [c for _, c in report.rows] == ["original"]
    assert report.verdict == "counts"


def test_a_ref_base_that_absorbed_the_branch_is_left_out(repo):
    """The third subtraction must not make that limit WORSE. `_ref_bases`
    resolves the recorded base NAME as it stands now, and by the time a repair
    is reviewed that ref may have absorbed the branch — running classify after
    a merge, for instance. Such a ref is dropped: a base that contains the
    first round's head cannot separate the base branch's work from the repair's,
    so adding it would delete the repair rather than the base branch. Recorded
    bases are declarations and are not filtered this way."""
    first_base = _sha(repo, "main")
    r1 = _mint(repo, _sha(repo), first_base)
    _repair(repo)
    r2 = _mint(repo, _sha(repo), first_base)
    # main absorbs the branch AFTER both rounds were minted, so only the ref
    # name sees it.
    _git(repo, "merge", "-q", "--no-edit", "main")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "feature")
    _git(repo, "checkout", "-q", "feature")
    assert repair_mod._ref_bases(repo, [{"base": "main"}]) == [_sha(repo, "main")]
    report = repair_mod.classify(repo, [r1, r2], [_f("tests/test_new.py")])
    assert report.repair_files == ("a.txt", "tests/test_new.py"), (
        "an absorbed ref base deleted the repair's own work")
    assert report.verdict == "scaffolding-stop"


def test_a_base_move_the_branch_never_merged_leaves_the_repair_set_intact(
        repo):
    """The rule: subtract what the branch CARRIES from the base branch, never
    every path the base branch happened to touch.

    `warden round new --base origin/main` resolves the base AT MINT TIME, so a
    sibling PR merging between rounds gives later rounds a different recorded
    base. The subtraction tolerates a mid-repair SYNC, but it must not fire on
    a base-branch MOVE the branch has not merged at all. Subtracting BY PATH
    would drop any file the moved base happened to touch from the repair set
    even though this branch's own repair commits wrote it, putting a
    repair-written file in `original` — which is the REVERT clause's premise.
    A trigger the builder may not argue with must not fire on a base-branch
    move.

    `git show --name-only <repair sha>` stays the authority on what a repair
    commit wrote: b.txt is in this repair commit, so it is repair-written."""
    # round 1's base predates main's own move of b.txt, so the base branch
    # moves that path BETWEEN the two recorded bases — the shape the PATH
    # subtraction reads.
    first_base = _sha(repo, "main~1")
    r1 = _mint(repo, _sha(repo), first_base)
    # the repair rewrites b.txt, which the base branch also moved
    (repo / "b.txt").write_text("repair rewrote this\n")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_new.py").write_text("def test(): pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "C2: repair touches a shared file")
    later = _sha(repo, "main")
    assert "b.txt" in repair_mod._changed(repo, first_base, later), (
        "the fixture must exercise the PATH subtraction, not paper over it")
    assert "b.txt" in _git(repo, "show", "--name-only", "--format=", _sha(repo)), (
        "the repair commit must actually write the shared file")
    r2 = _mint(repo, _sha(repo), later)
    report = repair_mod.classify(repo, [r1, r2], [_f("b.txt")])
    assert "b.txt" in report.repair_files, (
        "a base-branch move this branch never merged deleted the repair's own "
        "work from the repair set")
    assert report.verdict == "scaffolding-stop"


def test_a_non_ascii_path_is_read_as_the_repair_wrote_it(repo):
    """A path holding a non-ASCII byte is read as the repair wrote it.

    `git diff --name-only` C-QUOTES a path holding a non-ASCII byte unless
    `core.quotePath=false`. Read quoted, the repair set carries
    `"docs/\\303\\251.md"`, `git rev-parse <rev>:"docs/\\303\\251.md"` fails
    on BOTH sides, the two Nones compare equal, and a file the repair wrote
    is subtracted into `original`: the misclassification the by-content rule
    exists to close, reachable on any repo whose docs are not ASCII."""
    (repo / "docs").mkdir()
    (repo / "docs" / "é.md").write_text("v0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "C1b: a non-ascii path in the change")
    first_base = _sha(repo, "main")
    r1 = _mint(repo, _sha(repo), first_base)
    (repo / "docs" / "é.md").write_text("the repair rewrote this\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "C2: repair rewrites the non-ascii path")
    # the base branch moves the same path, never merged here
    _git(repo, "checkout", "-q", "main")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "é.md").write_text("v1 from main\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main moves the non-ascii path")
    _git(repo, "checkout", "-q", "feature")
    r2 = _mint(repo, _sha(repo), _sha(repo, "main"))
    report = repair_mod.classify(repo, [r1, r2], [_f("docs/é.md")])
    assert "docs/é.md" in report.repair_files, (
        "a C-quoted path defeated the content test and the repair's own file "
        "was read as the change's")
    assert report.verdict == "scaffolding-stop"


def test_a_path_the_branch_carries_verbatim_from_the_base_is_still_subtracted(
        repo):
    """The bound on the ruling above, and the reason it is not a widening: what
    leaves the repair set is what the branch holds VERBATIM from the base
    branch. Here the repair commit writes b.txt with exactly the base branch's
    own content — a squash or a cherry-pick of the base's work, whose commit is
    new and whose content is not the branch's — and it still subtracts.

    So the discriminator moved from "did the base branch ever touch this path"
    to "does this branch carry the base branch's bytes there", which is the
    question the subtraction was always trying to ask."""
    first_base = _sha(repo, "main~1")
    r1 = _mint(repo, _sha(repo), first_base)
    # the repair commit carries the base branch's own bytes for b.txt — the
    # squash / cherry-pick shape, where the COMMIT is new and the CONTENT is
    # the base branch's
    (repo / "b.txt").write_text(_git(repo, "show", "main:b.txt"))
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_new.py").write_text("def test(): pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "C2: repair, plus the base branch's own b.txt")
    later = _sha(repo, "main")
    assert (repo / "b.txt").read_text() == _git(repo, "show", "main:b.txt"), (
        "the fixture must carry the base branch's bytes, not paper over it")
    assert "b.txt" in _git(repo, "show", "--name-only", "--format=", _sha(repo)), (
        "the repair commit must actually write the shared path")
    assert "b.txt" in repair_mod._changed(repo, first_base, later)
    r2 = _mint(repo, _sha(repo), later)
    report = repair_mod.classify(repo, [r1, r2], [_f("b.txt")])
    assert "b.txt" not in report.repair_files, (
        "a path the branch carries verbatim from the base branch is the base "
        "branch's work, whatever route it took here")
    assert report.verdict == "counts"


@pytest.mark.parametrize("how", ["absorbing-commit", "squash"])
def test_a_base_path_moved_again_after_the_branch_absorbed_it_subtracts(
        repo, how):
    """A path the base branch moved again after the branch absorbed it still
    subtracts.

    Measuring the content test against the LATEST recorded base only would
    leave the branch holding base-branch bytes that match no current version:
    the path would stay in the repair set, and a LOW on work nobody here
    authored would end the full-crew loop at exit 0. Fail-OPEN — the one
    direction the module docstring says this mechanism never errs in.

    Every version the base branch put there counts, so the absorbed bytes are
    recognised however stale they are."""
    first_base = _sha(repo, "main")
    r1 = _mint(repo, _sha(repo), first_base)
    # the branch absorbs main's b.txt (b2), on its own commit
    if how == "squash":
        _git(repo, "merge", "-q", "--squash", "main")
        (repo / "tests").mkdir(exist_ok=True)
        (repo / "tests" / "test_new.py").write_text("def test(): pass\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "C2: squash main + repair")
    else:
        (repo / "b.txt").write_text(_git(repo, "show", "main:b.txt"))
        (repo / "tests").mkdir(exist_ok=True)
        (repo / "tests" / "test_new.py").write_text("def test(): pass\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "C2: carry main's b.txt + repair")
    # ...and THEN main moves that same path again, before round 2 is minted
    _git(repo, "checkout", "-q", "main")
    (repo / "b.txt").write_text("b3 — main moved it again\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main moves b.txt again")
    _git(repo, "checkout", "-q", "feature")
    later = _sha(repo, "main")
    assert (repo / "b.txt").read_text() != _git(repo, "show", "main:b.txt"), (
        "the fixture must leave the branch on a STALE base version, which is "
        "what defeats a tip-only content test")
    r2 = _mint(repo, _sha(repo), later)
    report = repair_mod.classify(repo, [r1, r2], [_f("b.txt")])
    assert "b.txt" not in report.repair_files, (
        f"{how}: base-branch bytes the base branch later re-moved read as the "
        "repair's own work — the early stop fired on work nobody here wrote")
    assert [c for _, c in report.rows] == ["original"]
    assert report.verdict == "counts"


def test_an_unresolvable_base_ref_name_is_skipped_not_a_refusal(repo):
    """The third subtraction reads the manifest's base REF NAME, which is a
    name in someone's clone and may be gone. Unresolvable means it contributes
    nothing — the chain is still classified from the recorded shas, exactly as
    before. Fail-closed in the other direction would be refusing every chain
    whose base branch was deleted."""
    base = _sha(repo, "main~1")
    r1 = _mint(repo, _sha(repo), base)
    _repair(repo)
    r2 = _mint(repo, _sha(repo), base)
    for d in (r1, r2):
        doc = json.loads((d / "round.json").read_text())
        doc["base"] = "refs/heads/branch-that-never-existed"
        (d / "round.json").write_text(json.dumps(doc))
    report = repair_mod.classify(repo, [r1, r2], [_f("tests/test_new.py")])
    assert report.verdict == "scaffolding-stop"


# ---------- the verdict is recorded, not discarded ---------------------------

def test_classify_records_its_verdict_in_the_round_it_classified(repo):
    """`classify` records what it computed. Without a record a builder could
    read exit 1 and mint a fourth round with nothing downstream able to tell.

    The verdict is an ARTIFACT in the round it classified, bound to the
    chain and to a digest of the findings payload it read — so a claimed
    scaffolding stop can be checked against the findings that were in front of
    the checker when it said so."""
    r1, r2 = _two_rounds(repo)
    findings = [_f("tests/test_new.py")]
    report = repair_mod.classify(repo, [r1, r2], findings)
    path = repair_mod.record(report, r2, findings)
    assert path == r2 / "classify.json"
    doc = json.loads(path.read_text())
    assert doc["verdict"] == "scaffolding-stop"
    assert doc["exit_code"] == 0
    assert doc["base_sha"] == report.base_sha
    assert doc["heads"] == list(report.heads)
    assert doc["open_findings"] == 1
    assert doc["findings_digest"] == repair_mod.findings_digest(findings)
    assert doc["reasons"] == list(report.reasons)


def test_the_record_is_bound_to_the_findings_it_judged(repo):
    """A digest, so the record cannot be read as a verdict over some other
    payload. Two different findings sets over one chain produce two different
    digests — the property that makes the record checkable at all."""
    r1, r2 = _two_rounds(repo)
    scaffold = [_f("tests/test_new.py")]
    counts = [_f("new.py", severity="HIGH")]
    assert (repair_mod.findings_digest(scaffold)
            != repair_mod.findings_digest(counts))
    a = json.loads(repair_mod.record(
        repair_mod.classify(repo, [r1, r2], scaffold), r2, scaffold).read_text())
    b = json.loads(repair_mod.record(
        repair_mod.classify(repo, [r1, r2], counts), r2, counts).read_text())
    assert a["verdict"] == "scaffolding-stop" and a["exit_code"] == 0
    assert b["verdict"] == "counts" and b["exit_code"] == 1
    assert a["findings_digest"] != b["findings_digest"]


def test_the_record_is_written_by_the_command_not_only_the_library(repo):
    """The library function records nothing unless something CALLS it, so
    `warden round classify` writes the
    record itself, into the latest round directory it was given, and names the
    file in its own output."""
    r1, r2 = _two_rounds(repo)
    payload = repo / "payload.json"
    payload.write_text(json.dumps({"findings": [_f("tests/test_new.py")]}))
    proc = subprocess.run(
        [sys.executable, "-m", "warden.cli", "round", "classify",
         "--findings", str(payload), "--round", str(r1), "--round", str(r2)],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert proc.returncode == 0, proc.stderr
    assert (r2 / "classify.json").is_file(), (
        "the command computed a verdict and recorded nothing")
    assert "classify.json" in proc.stdout
    doc = json.loads((r2 / "classify.json").read_text())
    assert doc["verdict"] == "scaffolding-stop"


# ---------- the base a round is minted against -------------------------------

def test_round_new_refuses_a_base_that_is_behind_its_own_upstream(repo):
    """The precondition every subtraction above needs, enforced where it is
    cheapest: at the MINT.

    Minting with `--base main` — the LOCAL ref — while `git fetch` moves only
    `origin/main` records a stale base whenever local main is behind its
    upstream, and `classify`'s subtractions then have nothing fresh to
    measure against.
    Nothing in the manifests can see that, because the manifests are the
    classifier's only input — so the refusal belongs here, where the ref is
    still in hand.

    Refused, not warned: a round whose base is behind its upstream records a
    base that describes a state nobody is reviewing against."""
    _git(repo, "branch", "-q", "upstream-main", "main")
    _git(repo, "config", "branch.main.remote", ".")
    _git(repo, "config", "branch.main.merge", "refs/heads/upstream-main")
    # upstream moves; local main does not
    _git(repo, "checkout", "-q", "upstream-main")
    (repo / "upstream.md").write_text("moved\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "upstream moves ahead of local main")
    _git(repo, "checkout", "-q", "feature")

    proc = subprocess.run(
        [sys.executable, "-m", "warden.cli", "round", "new", "--base", "main"],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "behind" in proc.stderr, proc.stderr
    assert "upstream-main" in proc.stderr, (
        "the refusal must name the ref that is ahead, or the reader cannot act "
        f"on it: {proc.stderr}")
    assert not list((repo / ".warden" / "out" / "rounds").glob("*")), (
        "a refused mint must leave no round directory behind")

    # The same command against the ref that is actually the base mints fine.
    proc = subprocess.run(
        [sys.executable, "-m", "warden.cli", "round", "new",
         "--base", "upstream-main"],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_base_with_no_upstream_and_one_that_is_ahead_both_mint(repo):
    """The refusal is narrow on purpose. A base with no upstream configured —
    `origin/main` itself, a bare sha, a local-only branch — makes no staleness
    claim this check can read, and a base AHEAD of its upstream is not stale.
    Neither is refused: failing closed on "cannot tell" here would refuse every
    mint in a clone with no remote."""
    for base in ("main", _sha(repo, "main")):
        proc = subprocess.run(
            [sys.executable, "-m", "warden.cli", "round", "new",
             "--base", base],
            cwd=repo, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT)})
        assert proc.returncode == 0, f"{base}: {proc.stdout}{proc.stderr}"

    # ahead of its upstream: allowed
    _git(repo, "branch", "-q", "upstream-main", "main~1")
    _git(repo, "config", "branch.main.remote", ".")
    _git(repo, "config", "branch.main.merge", "refs/heads/upstream-main")
    proc = subprocess.run(
        [sys.executable, "-m", "warden.cli", "round", "new", "--base", "main"],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_skill_says_what_the_refusal_covers_and_what_it_does_not():
    """The skill must not claim the staleness refusal covers the
    remote-tracking base it prescribes: `stale_base` returns None for a
    remote-tracking ref, because such a ref has no upstream of its own, so an
    unfetched `origin/main` is as stale as a stale local `main`. The claim is
    split where the mechanism splits, and the fetch is in the prescribed
    command."""
    text = (ROOT / "skills/nightgate-skills/skills/pre-pr-review/SKILL.md"
            ).read_text()
    assert "git fetch origin" in text, (
        "the protocol relies on a fresh origin/main and still does not fetch")
    assert "on a current pin this is enforced rather than remembered" not in text
    assert "It cannot catch an\nunfetched `origin/main`" in text, (
        "the skill does not say what the refusal fails to cover")
    # and the claim it DOES make is true of the code
    from warden import config as config_mod
    from warden import diffs as diffs_mod
    root = config_mod.find_repo_root(ROOT)
    assert diffs_mod.stale_base(root, "origin/main") is None, (
        "a remote-tracking ref has no upstream, so there is no staleness claim "
        "to read — which is exactly what the skill now says")

# ---------- the prose is driven by the subtraction ---------------------------


# The CONDITION the by-content regime states, not the bare word: `carries`
# on its own matches "examples/hello-svc carries the minimal instance" eight
# lines above the Skills-Policy row, and `warden decide list carries the
# shard` inside the policy's own paragraph.


# ---------- the ASCII half of the quoting mismatch ---------------------------

@pytest.mark.parametrize("name", ['we"ird.md', "tab\tx.md", "back\\slash.md"],
                         ids=["double-quote", "tab", "backslash"])
def test_a_path_git_quotes_whatever_quotepath_says_is_read_as_the_repair_wrote_it(
        repo, name):
    """A path git quotes whatever `core.quotePath` says is read as the repair
    wrote it.

    `core.quotePath=false` closes only the NON-ASCII half of the path-quoting
    mismatch (the non-ASCII test above): git C-quotes a path holding a double
    quote, a backslash or a control character WHATEVER the flag says —
    `git -c core.quotePath=false diff --name-only` returns `é.md` raw beside
    `"we\\"ird.md"`, `"tab\\tx.md"` and `"back\\\\slash.md"` still quoted.
    For such a path `_blob` fails on BOTH sides, the two Nones compare equal,
    and the repair's own file is subtracted into `original`.
    `--name-only -z` is quoting-free by construction, and the listing
    below is asserted RAW before the classification is, so the reader and
    the verdict are pinned separately."""
    (repo / "docs").mkdir()
    (repo / "docs" / name).write_text("v0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "C1b: a path git would C-quote, in the change")
    first_base = _sha(repo, "main")
    r1 = _mint(repo, _sha(repo), first_base)
    (repo / "docs" / name).write_text("the repair rewrote this\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "C2: repair rewrites the quoted path")
    assert (f"docs/{name}",) == tuple(
        p for p in repair_mod._changed(repo, first_base, _sha(repo))
        if p.startswith("docs/")), (
        "the listing came back C-quoted — the reader is not quoting-free")
    # the base branch moves the same path, never merged here
    _git(repo, "checkout", "-q", "main")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / name).write_text("v1 from main\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main moves the quoted path")
    _git(repo, "checkout", "-q", "feature")
    r2 = _mint(repo, _sha(repo), _sha(repo, "main"))
    report = repair_mod.classify(repo, [r1, r2], [_f(f"docs/{name}")])
    assert f"docs/{name}" in report.repair_files, (
        "a C-quoted path defeated the content test and the repair's own file "
        "was read as the change's")
    assert report.verdict == "scaffolding-stop"


def _quoting_free(calls: list[tuple[str, ...]]) -> None:
    """The quoting guard's judgment over the git invocations the readers
    made, as a function so its DIAGNOSTIC can be tested under the mutation it
    refuses. A listing is any call carrying `diff` or `diff-tree` anywhere:
    filtering listings by their FIRST argument would make a flag re-added as
    a leading global option say 'neither listing ran' about two listings
    that ran."""
    listings = [c for c in calls if "diff" in c or "diff-tree" in c]
    assert listings, "neither listing ran — the guard is vacuous"
    flagged = [c for c in calls if any("quotepath" in a.lower() for a in c)]
    assert not flagged, (
        f"a reader still passes core.quotePath in some spelling: {flagged}")
    for args in listings:
        assert "-z" in args and "--name-only" in args, (
            f"a listing is not read with --name-only -z: {args}")


def test_the_listings_no_longer_lean_on_the_quotepath_flag(repo, monkeypatch):
    """`core.quotePath=false` is not a one-flag statement of what `-z` does
    (the test above measures why), and a flag left beside `-z` would do
    nothing while inviting the next reader to believe it is what closes the
    case.

    Pinned on what the two listing calls actually PASS to git, not on the
    source text: git reads the config key case-insensitively, so a
    case-sensitive substring test would pass `core.quotepath=false`
    (lowercase, the spelling cage/run.sh's own comment uses) re-added beside
    `-z`. Every git invocation the two readers make is captured here; each
    listing must carry `-z`, and none may carry the flag in any spelling."""
    calls: list[tuple[str, ...]] = []
    real = repair_mod.run_git

    def spy(root, *args):
        calls.append(tuple(args))
        return real(root, *args)

    monkeypatch.setattr(repair_mod, "run_git", spy)
    first = _sha(repo)
    _repair(repo)
    repair_mod._changed(repo, _sha(repo, "main~1"), _sha(repo))
    repair_mod._repair_files(repo, first, _sha(repo), (_sha(repo, "main~1"),))
    _quoting_free(calls)


def test_the_flag_guard_names_the_flag_under_its_own_mutation():
    """Under the exact mutation the guard exists to refuse — the flag re-added
    as a LEADING global option, `git -c core.quotepath=false diff ...` — the
    guard's diagnostic must name the flag, not say 'neither listing ran' about
    a listing that did run."""
    mutated = [("-c", "core.quotepath=false", "diff", "--name-only", "-z", "a...b"),
               ("-c", "core.quotepath=false", "diff-tree", "--no-commit-id",
                "--name-only", "-z", "-r", "abc")]
    with pytest.raises(AssertionError, match="spelling"):
        _quoting_free(mutated)
    with pytest.raises(AssertionError, match="vacuous"):
        _quoting_free([("rev-list", "a..b")])


# ---------- the ref-base docstring vs the comprehension -----------------------

def test_a_ref_base_that_has_not_absorbed_the_branch_feeds_the_file_subtraction(
        repo):
    """A ref base that has not absorbed the branch feeds the file
    subtraction, pinned on its own rather than inferred from the squash cell
    of the stale-recorded-base test above.

    The docstrings of `_ref_bases`, `_chain` and `_repair_files` in
    warden/repair.py must not say a resolved ref base drives the reachability
    subtraction only and 'never' the file subtraction. The
    comprehension reads `(*bases[1:], *(b for b in ref_bases if not
    _is_ancestor(root, first, b)))`: a ref base that has NOT absorbed the
    branch DOES feed the file subtraction, and it has to — a squash of the
    base branch under a stale recorded base is a NEW commit reachability
    cannot see, both rounds record the stale base so `bases[1:]` is empty,
    and nothing but the ref base can subtract the squashed file. This test
    drives `_repair_files` with and without the ref base so that reversing
    the comprehension is a visible change of behaviour, whichever way the
    docstring is later read."""
    stale = _sha(repo, "main")
    first = _sha(repo)
    _repair(repo)
    _advance_main(repo, "docs.md")
    _git(repo, "merge", "-q", "--squash", "main")
    _git(repo, "commit", "-qm", "squash main")
    last = _sha(repo)
    now = _sha(repo, "main")
    assert not repair_mod._is_ancestor(repo, first, now), (
        "the fixture's ref base must NOT have absorbed the branch")
    without = repair_mod._repair_files(repo, first, last, (stale,), ())
    assert "docs.md" in without, (
        "the fixture must leave the squashed file for the ref base to remove")
    with_ref = repair_mod._repair_files(repo, first, last, (stale,), (now,))
    assert "docs.md" not in with_ref, (
        "a non-absorbed ref base did not feed the file subtraction — the "
        "docstring's 'never' would then be the code, and the squash cell of "
        "the stale-recorded-base test could not pass")
    assert "tests/test_new.py" in with_ref, "the repair's own file left the set"


def test_the_docstrings_state_the_bound_the_comprehension_implements():
    """None of the docstrings of `_ref_bases`, `_chain` and `_repair_files` may
    claim the reachability-only bound, and the one on `_repair_files` must
    state the bound the comprehension implements. A docstring is what a
    reader trusts, so the guard is on the docstrings."""
    for fn in (repair_mod._ref_bases, repair_mod._chain,
               repair_mod._repair_files):
        low = fn.__doc__.lower()
        assert "reachability subtraction only" not in low, (
            f"{fn.__name__}: still claims the ref base joins reachability only")
        assert "never the file subtraction" not in low, (
            f"{fn.__name__}: still claims the ref base never feeds the file "
            "subtraction")
        assert "drives only the reachability" not in low, fn.__name__
    doc = repair_mod._repair_files.__doc__
    assert "has NOT absorbed" in doc, (
        "_repair_files' docstring does not state the bound: a ref base joins "
        "the file subtraction only when it has NOT absorbed the branch")


# ---------- wording findings below the blocking severities do not count ----

_RULES = ".warden/rules"


def _commit_rules(repo: Path, kinds: dict) -> None:
    """Commit one engine:claude rule per id; a kind of None declares nothing."""
    d = repo / ".warden" / "rules"
    d.mkdir(parents=True, exist_ok=True)
    for rid, kind in kinds.items():
        extra = f"judges: {kind}\n" if kind else ""
        (d / f"{rid}.md").write_text(
            f"---\nid: {rid}\nseverity: MEDIUM\nengine: claude\n"
            f"applies_to: [\"**\"]\n{extra}---\nbody\n")
    if kinds:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "rules")


def _ruled_rounds(repo: Path) -> tuple[Path, Path]:
    _commit_rules(repo, {"wiki-fidelity": "wording", "fail-closed": "behaviour",
                         "secrets": None})
    return _two_rounds(repo)


def test_a_medium_wording_finding_on_the_original_diff_classifies_clean(repo):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="MEDIUM", rule_id="wiki-fidelity")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "clean"
    assert report.rows == ()
    assert [f["rule_id"] for f in report.wording] == ["wiki-fidelity"]
    assert repair_mod.exit_code(report) == 0


def test_the_same_finding_under_a_behaviour_rule_counts(repo):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="MEDIUM", rule_id="fail-closed")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "counts"
    assert report.wording == ()


def test_a_wording_finding_at_a_blocking_severity_counts(repo):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="HIGH", rule_id="wiki-fidelity")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "counts"
    assert len(report.rows) == 1
    assert report.wording == ()


@pytest.mark.parametrize("blocking", [None, ()])
def test_a_wording_finding_counts_when_no_blocking_severity_is_known(repo,
                                                                     blocking):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="MEDIUM", rule_id="wiki-fidelity")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=blocking)
    assert report.verdict == "counts"


def test_a_wording_finding_at_an_unknown_severity_counts(repo):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="low-ish", rule_id="wiki-fidelity")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "counts"


def test_a_wording_finding_beside_a_behaviour_finding_does_not_change_the_verdict(
        repo):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="MEDIUM", rule_id="wiki-fidelity"),
                _f("tests/test_new.py", rule_id="fail-closed")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "scaffolding-stop"
    assert [c for _, c in report.rows] == ["repair"]
    assert len(report.wording) == 1


# An `unmapped:` finding names no rule to say what it judges. `kind_of` reads
# it as wording unless the record itself declares `judges: behaviour`; a
# finding under a declared rule is what the rule says, whatever it carries.


def test_an_unmapped_finding_with_no_judges_field_does_not_count(repo):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="MEDIUM", rule_id="unmapped:docs-drift")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "clean"
    assert report.rows == ()
    assert [f["rule_id"] for f in report.wording] == ["unmapped:docs-drift"]
    assert dict(report.rule_kinds) == {"unmapped:docs-drift": "wording"}


def test_an_unmapped_finding_declaring_behaviour_counts(repo):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="MEDIUM", rule_id="unmapped:docs-drift",
                   judges="behaviour")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "counts"
    assert report.wording == ()
    assert dict(report.rule_kinds) == {"unmapped:docs-drift": "behaviour"}


def test_an_unmapped_finding_declaring_wording_is_wording(repo):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="MEDIUM", rule_id="unmapped:docs-drift",
                   judges="wording")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "clean"
    assert len(report.wording) == 1


@pytest.mark.parametrize("value", ["behavior", "Behaviour", "", None, 1])
def test_an_unmapped_finding_with_an_unrecognised_judges_value_counts(repo,
                                                                     value):
    """`round classify` validates no schema, so a `judges` value the
    attestation schema would refuse must read as the counting kind here: a
    misspelling of `behaviour` is not a way to end the loop."""
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="MEDIUM", rule_id="unmapped:docs-drift",
                   judges=value)]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "counts"
    assert report.wording == ()


def test_an_unmapped_finding_at_a_blocking_severity_counts_like_any_wording(
        repo):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="HIGH", rule_id="unmapped:docs-drift")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "counts"
    assert report.wording == ()


def test_a_declared_behaviour_rules_finding_without_the_field_counts_unchanged(
        repo):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="MEDIUM", rule_id="fail-closed")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "counts"
    assert report.wording == ()
    assert dict(report.rule_kinds) == {"fail-closed": "behaviour"}


def test_a_declared_wording_rules_finding_does_not_count_whatever_it_carries(
        repo):
    """The rule's frontmatter decides a declared rule's finding; a record
    saying `judges: behaviour` under a wording rule does not make it count."""
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="MEDIUM", rule_id="wiki-fidelity",
                   judges="behaviour")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "clean"
    assert [f["rule_id"] for f in report.wording] == ["wiki-fidelity"]
    assert dict(report.rule_kinds) == {"wiki-fidelity": "wording"}


def test_an_unmapped_id_whose_records_disagree_is_recorded_as_behaviour(repo):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="MEDIUM", rule_id="unmapped:docs-drift",
                   judges="behaviour"),
                _f("new.py", severity="MEDIUM", rule_id="unmapped:docs-drift")]
    report = repair_mod.classify(repo, [r1, r2], findings, rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "counts"
    assert len(report.rows) == 1 and len(report.wording) == 1
    assert dict(report.rule_kinds) == {"unmapped:docs-drift": "behaviour"}


def test_the_seam_reads_a_rule_before_the_record_and_the_prefix_last():
    judges = {"strict": "behaviour", "soft": "wording"}
    assert repair_mod.kind_of({"rule_id": "soft", "judges": "behaviour"},
                              judges) == "wording"
    assert repair_mod.kind_of({"rule_id": "strict", "judges": "wording"},
                              judges) == "behaviour"
    assert repair_mod.kind_of({"rule_id": "unmapped:x"}, judges) == "wording"
    assert repair_mod.kind_of({"rule_id": "unmapped:x", "judges": "behaviour"},
                              judges) == "behaviour"
    assert repair_mod.kind_of({"rule_id": "unmapped:x"}, {}) == "wording"
    assert repair_mod.kind_of({"rule_id": "no-such-rule"}, judges) == "behaviour"
    assert repair_mod.kind_of({"rule_id": None, "judges": "wording"},
                              judges) == "behaviour"


@pytest.mark.parametrize("rule_id", ["no-such-rule", "", None, 7])
def test_a_rule_id_that_resolves_to_no_rule_counts_as_behaviour(repo, rule_id):
    r1, r2 = _ruled_rounds(repo)
    finding = _f("new.py", severity="MEDIUM")
    finding["rule_id"] = rule_id
    report = repair_mod.classify(repo, [r1, r2], [finding], rules_dir=_RULES,
                                 blocking=("HIGH",))
    assert report.verdict == "counts"
    assert report.wording == ()


def test_classify_without_a_rules_dir_counts_every_finding(repo):
    r1, r2 = _ruled_rounds(repo)
    findings = [_f("new.py", severity="MEDIUM", rule_id="wiki-fidelity")]
    report = repair_mod.classify(repo, [r1, r2], findings, blocking=("HIGH",))
    assert report.verdict == "counts"


def test_classify_json_records_each_rules_kind(
        repo):
    r1, r2 = _ruled_rounds(repo)
    from warden import rules as rules_mod
    expected_version = rules_mod.rules_version(repo / ".warden" / "rules", repo)
    payload = repo / "payload.json"
    payload.write_text(json.dumps({"findings": [
        _f("new.py", severity="MEDIUM", rule_id="wiki-fidelity"),
        _f("new.py", severity="LOW", rule_id="unmapped:docs-drift"),
        _f("new.py", severity="LOW", rule_id="secrets", status="fixed")]}))
    out = _cli(repo, "round", "classify", "--findings", str(payload),
               "--round", str(r1), "--round", str(r2))
    assert out.returncode == 0, out.stdout + out.stderr
    doc = json.loads((r2 / "classify.json").read_text())
    assert doc["rules_version"] == expected_version
    assert doc["rule_kinds"] == {"wiki-fidelity": "wording",
                                 "unmapped:docs-drift": "wording",
                                 "secrets": "behaviour"}


def test_classify_output_and_record_list_the_wording_findings(repo):
    r1, r2 = _ruled_rounds(repo)
    payload = repo / "payload.json"
    payload.write_text(json.dumps({"findings": [
        _f("new.py", severity="MEDIUM", rule_id="wiki-fidelity")]}))
    out = _cli(repo, "round", "classify", "--findings", str(payload),
               "--round", str(r1), "--round", str(r2))
    assert out.returncode == 0, out.stderr
    assert "verdict: clean" in out.stdout
    assert [ln for ln in out.stdout.splitlines()
            if "wiki-fidelity" in ln and "new.py" in ln], out.stdout
    assert "wording findings" in out.stdout
    doc = json.loads((r2 / "classify.json").read_text())
    assert doc["verdict"] == "clean"
    assert doc["open_findings"] == 0
    assert [f["rule_id"] for f in doc["wording_findings"]] == ["wiki-fidelity"]
    assert doc["wording_findings"][0]["file"] == "new.py"

    # the command reads the repo's blocking severities: a HIGH one counts
    payload.write_text(json.dumps({"findings": [
        _f("new.py", severity="HIGH", rule_id="wiki-fidelity")]}))
    out = _cli(repo, "round", "classify", "--findings", str(payload),
               "--round", str(r1), "--round", str(r2))
    assert out.returncode == 1, out.stdout + out.stderr


def test_a_ruleset_that_cannot_load_refuses_classify(repo):
    rules_dir = repo / ".warden" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "broken.md").write_text("no frontmatter")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a broken rule, committed")
    r1, r2 = _two_rounds(repo)
    payload = repo / "payload.json"
    payload.write_text(json.dumps({"findings": []}))
    out = _cli(repo, "round", "classify", "--findings", str(payload),
               "--round", str(r1), "--round", str(r2))
    assert out.returncode == 2
    assert "broken.md" in out.stderr


# ---------- every classify input is read at the round head ------------------
#
# The verdict is ABOUT a commit, so what decides it — each rule's `judges:`,
# repo.yaml's rules_dir and blocking_severities, and the rules_version recorded
# beside it — is read at the head the latest round was minted for, never off
# the working tree. An uncommitted edit changes nothing.

def _rule(rid: str, kind: str | None, severity: str = "MEDIUM") -> str:
    extra = f"judges: {kind}\n" if kind else ""
    return (f"---\nid: {rid}\nseverity: {severity}\nengine: claude\n"
            f"applies_to: [\"**\"]\n{extra}---\nbody\n")


def _classify_cli(root: Path, findings: list[dict], *rounds: Path,
                  timeout: float | None = None) -> subprocess.CompletedProcess:
    payload = root / "payload.json"
    payload.write_text(json.dumps({"findings": findings}))
    argv = ["round", "classify", "--findings", str(payload)]
    for r in rounds:
        argv += ["--round", str(r)]
    return _cli(root, *argv, timeout=timeout)


def _tree(top: Path) -> set[str]:
    """Every path under `top`, round evidence and git internals aside."""
    return {str(p.relative_to(top)) for p in top.rglob("*")
            if ".git" not in p.parts and "out" not in p.parts}


@pytest.mark.parametrize("committed, uncommitted, expected", [
    ("wording", "behaviour", 0),
    ("behaviour", "wording", 1),
])
def test_an_uncommitted_flip_of_a_rules_judges_does_not_change_the_verdict(
        repo, committed, uncommitted, expected):
    _commit_rules(repo, {"wiki-fidelity": committed})
    r1, r2 = _two_rounds(repo)
    (repo / ".warden" / "rules" / "wiki-fidelity.md").write_text(
        _rule("wiki-fidelity", uncommitted))
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1, r2)
    assert out.returncode == expected, out.stdout + out.stderr
    doc = json.loads((r2 / "classify.json").read_text())
    assert doc["rule_kinds"] == {"wiki-fidelity": committed}


def test_an_uncommitted_change_to_blocking_severities_does_not_change_the_verdict(
        repo):
    _commit_rules(repo, {"wiki-fidelity": "wording"})
    r1, r2 = _two_rounds(repo)
    yml = repo / "repo.yaml"
    assert "blocking_severities: [HIGH]" in yml.read_text()
    yml.write_text(yml.read_text().replace(
        "blocking_severities: [HIGH]", "blocking_severities: [MEDIUM, HIGH]"))
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1, r2)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "verdict: clean" in out.stdout


def test_classify_json_records_the_round_heads_rules_version_not_the_working_trees(
        repo):
    from warden import rules as rules_mod
    (repo / ".warden" / "checkers").mkdir(parents=True)
    (repo / ".warden" / "checkers" / "plug.py").write_text("X = 1\n")
    _commit_rules(repo, {"wiki-fidelity": "wording", "secrets": None})
    r1, r2 = _two_rounds(repo)
    rules_dir = repo / ".warden" / "rules"
    at_head = rules_mod.rules_version(rules_dir, repo)  # a clean checkout
    (rules_dir / "secrets.md").write_text(_rule("secrets", None, "LOW"))
    (rules_dir / "extra.md").write_text(_rule("extra", "wording"))
    (repo / ".warden" / "checkers" / "plug.py").write_text("X = 2\n")
    yml = repo / "repo.yaml"
    yml.write_text(yml.read_text().replace(
        "blocking_severities: [HIGH]", "blocking_severities: [MEDIUM, HIGH]"))
    assert rules_mod.rules_version(rules_dir, repo) != at_head
    out = _classify_cli(repo, [], r1, r2)
    assert out.returncode == 0, out.stdout + out.stderr
    assert json.loads((r2 / "classify.json").read_text())["rules_version"] \
        == at_head


def _subdir_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A git root holding a repo enrolled from `svc/`, plus a DECOY enrollment
    at the git root whose rule reads the opposite way, so a classify that
    reads repo.yaml or the rules at the git root instead of through the
    prefix gets the wrong answer rather than the same one."""
    top = tmp_path / "top"
    sub = top / "svc"
    (sub / ".warden" / "rules").mkdir(parents=True)
    (top / ".warden" / "rules").mkdir(parents=True)
    _git(tmp_path, "init", "-q", "-b", "main", "top")
    _git(top, "config", "user.email", "t@example.com")
    _git(top, "config", "user.name", "t")
    (sub / "repo.yaml").write_text(REPO_YAML)
    (sub / ".gitignore").write_text(".warden/out/\n")
    (sub / "a.txt").write_text("one\n")
    (sub / ".warden" / "rules" / "wiki-fidelity.md").write_text(
        _rule("wiki-fidelity", "wording"))
    (top / "repo.yaml").write_text(REPO_YAML.replace(
        "blocking_severities: [HIGH]", "blocking_severities: [MEDIUM, HIGH]"))
    (top / ".warden" / "rules" / "wiki-fidelity.md").write_text(
        _rule("wiki-fidelity", "behaviour"))
    _git(top, "add", "-A")
    _git(top, "commit", "-qm", "base")
    base = _sha(top)
    _git(top, "checkout", "-q", "-b", "feature")
    (sub / "new.py").write_text("x = 1\n")
    _git(top, "add", "-A")
    _git(top, "commit", "-qm", "C1")
    return top, sub, _mint(sub, _sha(top), base)


def test_a_repo_rooted_below_the_git_root_classifies(tmp_path):
    _, sub, r1 = _subdir_repo(tmp_path)
    out = _classify_cli(sub, [_f("svc/new.py", severity="MEDIUM",
                                 rule_id="wiki-fidelity")], r1)
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_repo_below_the_git_root_reads_its_inputs_at_the_head_through_its_prefix(
        tmp_path):
    _, sub, r1 = _subdir_repo(tmp_path)
    (sub / ".warden" / "rules" / "wiki-fidelity.md").write_text(
        _rule("wiki-fidelity", "behaviour"))
    yml = sub / "repo.yaml"
    yml.write_text(yml.read_text().replace(
        "blocking_severities: [HIGH]", "blocking_severities: [MEDIUM, HIGH]"))
    out = _classify_cli(sub, [_f("svc/new.py", severity="MEDIUM",
                                 rule_id="wiki-fidelity")], r1)
    assert out.returncode == 0, out.stdout + out.stderr
    assert json.loads((r1 / "classify.json").read_text())["rule_kinds"] \
        == {"wiki-fidelity": "wording"}


@pytest.mark.parametrize("declared", ["../x", "ABSOLUTE"])
def test_a_rules_dir_outside_the_repository_is_refused_and_writes_nothing(
        repo, tmp_path, declared):
    outside = tmp_path / "x"
    outside.mkdir()
    (outside / "wiki-fidelity.md").write_text(_rule("wiki-fidelity", "wording"))
    rules_dir = str(outside) if declared == "ABSOLUTE" else declared
    yml = repo / "repo.yaml"
    yml.write_text(yml.read_text().replace(
        "rules_dir: .warden/rules", f"rules_dir: {rules_dir}"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "rules outside the repository")
    r1, r2 = _two_rounds(repo)
    before = _tree(tmp_path)
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1, r2)
    assert out.returncode == 2, out.stdout + out.stderr
    assert rules_dir in out.stderr
    assert _tree(tmp_path) - {"r/payload.json"} == before - {"r/payload.json"}
    assert not (r2 / "classify.json").exists()


# The symlink rule: a committed symlink — a rule file, or the rules directory
# itself — is followed WITHIN the repository's tree at the round head, the way
# the working-tree loader follows it on disk; a symlink whose target leaves the
# repository, dangles or loops is refused with exit 2 naming it.

def test_a_committed_symlinked_rule_file_is_read_through_to_its_target_at_the_head(
        repo):
    (repo / "shared").mkdir()
    (repo / "shared" / "wiki-fidelity.md").write_text(
        _rule("wiki-fidelity", "wording"))
    (repo / ".warden" / "rules").mkdir(parents=True)
    (repo / ".warden" / "rules" / "wiki-fidelity.md").symlink_to(
        "../../shared/wiki-fidelity.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a symlinked rule")
    r1, r2 = _two_rounds(repo)
    (repo / "shared" / "wiki-fidelity.md").write_text(
        _rule("wiki-fidelity", "behaviour"))
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1, r2)
    assert out.returncode == 0, out.stdout + out.stderr
    assert json.loads((r2 / "classify.json").read_text())["rule_kinds"] \
        == {"wiki-fidelity": "wording"}


def test_a_committed_symlinked_rules_dir_is_read_through_to_its_target_at_the_head(
        repo):
    (repo / "policy" / "rules").mkdir(parents=True)
    (repo / "policy" / "rules" / "wiki-fidelity.md").write_text(
        _rule("wiki-fidelity", "wording"))
    (repo / ".warden" / "rules").symlink_to("../policy/rules")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a symlinked rules dir")
    r1, r2 = _two_rounds(repo)
    (repo / "policy" / "rules" / "wiki-fidelity.md").write_text(
        _rule("wiki-fidelity", "behaviour"))
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1, r2)
    assert out.returncode == 0, out.stdout + out.stderr
    assert json.loads((r2 / "classify.json").read_text())["rule_kinds"] \
        == {"wiki-fidelity": "wording"}


@pytest.mark.parametrize("what", ["file", "dir"])
def test_a_symlink_whose_target_leaves_the_repository_is_refused(
        repo, tmp_path, what):
    (tmp_path / "outside-rules").mkdir()
    (tmp_path / "outside-rules" / "wiki-fidelity.md").write_text(
        _rule("wiki-fidelity", "wording"))
    if what == "file":
        (repo / ".warden" / "rules").mkdir(parents=True)
        link = repo / ".warden" / "rules" / "wiki-fidelity.md"
        link.symlink_to("../../../outside-rules/wiki-fidelity.md")
    else:
        link = repo / ".warden" / "rules"
        link.symlink_to("../../outside-rules")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a symlink out of the repository")
    r1, r2 = _two_rounds(repo)
    before = _tree(tmp_path)
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1, r2)
    assert out.returncode == 2, out.stdout + out.stderr
    assert str(link.relative_to(repo)) in out.stderr
    assert _tree(tmp_path) - {"r/payload.json"} == before - {"r/payload.json"}


@pytest.mark.parametrize("rules_dir", ["policy", "policy/rules"])
def test_a_rules_dir_in_a_git_submodule_is_refused_not_read_as_no_rules(
        repo, tmp_path, rules_dir):
    """git reports a path inside a gitlink as `missing` at a commit, which
    would read as "declares no rules" and count every wording finding."""
    lib = tmp_path / "lib"
    (lib / "rules").mkdir(parents=True)
    _git(tmp_path, "init", "-q", "-b", "main", "lib")
    _git(lib, "config", "user.email", "t@example.com")
    _git(lib, "config", "user.name", "t")
    (lib / "rules" / "wiki-fidelity.md").write_text(
        _rule("wiki-fidelity", "wording"))
    (lib / "wiki-fidelity.md").write_text(_rule("wiki-fidelity", "wording"))
    _git(lib, "add", "-A")
    _git(lib, "commit", "-qm", "rules")
    _git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
         str(lib), "policy")
    yml = repo / "repo.yaml"
    yml.write_text(yml.read_text().replace(
        "rules_dir: .warden/rules", f"rules_dir: {rules_dir}"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "rules in a submodule")
    r1, r2 = _two_rounds(repo)
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1, r2)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "policy" in out.stderr
    assert not (r2 / "classify.json").exists()


def _submodule(tmp_path: Path, top: Path, at: str) -> None:
    """Add a one-commit repository as a git submodule of `top` at `at`."""
    lib = tmp_path / "lib"
    if not lib.exists():
        lib.mkdir()
        _git(tmp_path, "init", "-q", "-b", "main", "lib")
        _git(lib, "-c", "user.email=t@example.com", "-c", "user.name=t",
             "commit", "-q", "--allow-empty", "-m", "lib")
    _git(top, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
         str(lib), at)


# A gitlink refuses a read only when it IS the path or one of the path's own
# parent folders. `git ls-tree` on a path's parents also lists their other
# entries, and a sibling submodule is none of the path's business.

@pytest.mark.parametrize("layout", ["git-root", "below-git-root"])
def test_a_missing_checkers_dir_beside_an_unrelated_submodule_classifies(
        repo, tmp_path, layout):
    if layout == "git-root":
        _submodule(tmp_path, repo, ".warden/vendor")
        _commit_rules(repo, {"wiki-fidelity": "wording"})
        root, file, rounds = repo, "new.py", _two_rounds(repo)
    else:
        top, root, r1 = _subdir_repo(tmp_path / "sub")
        _submodule(tmp_path, top, "svc/vendor")
        _git(top, "commit", "-qm", "a submodule beside the enrollment")
        rounds = (r1, _mint(root, _sha(top), _sha(top, "main")))
        file = "svc/new.py"
    assert not (root / ".warden" / "checkers").exists()
    out = _classify_cli(root, [_f(file, severity="MEDIUM",
                                  rule_id="wiki-fidelity")], *rounds)
    assert out.returncode == 0, out.stdout + out.stderr


def test_with_two_sibling_submodules_the_rules_dirs_own_submodule_is_named(
        repo, tmp_path):
    _submodule(tmp_path, repo, "vendor/aaa")
    _submodule(tmp_path, repo, "vendor/pack")
    yml = repo / "repo.yaml"
    yml.write_text(yml.read_text().replace(
        "rules_dir: .warden/rules", "rules_dir: vendor/pack/rules"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "rules in the second of two submodules")
    r1, r2 = _two_rounds(repo)
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1, r2)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "vendor/pack" in out.stderr
    assert "vendor/aaa" not in out.stderr


def test_an_untracked_repo_yaml_beside_a_submodule_is_reported_as_not_committed(
        tmp_path):
    top, sub, r1 = _subdir_repo(tmp_path / "sub")
    _submodule(tmp_path, top, "svc/vendor")
    _git(top, "rm", "-q", "--cached", "svc/repo.yaml")
    _git(top, "commit", "-qm", "repo.yaml untracked beside a submodule")
    assert (sub / "repo.yaml").is_file()
    r2 = _mint(sub, _sha(top), _sha(top, "main"))
    out = _classify_cli(sub, [_f("svc/new.py", severity="MEDIUM",
                                 rule_id="wiki-fidelity")], r1, r2)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "repo.yaml" in out.stderr
    assert "svc/vendor" not in out.stderr
    assert not (r2 / "classify.json").exists()


def test_an_uncommitted_retarget_of_an_in_repo_symlink_under_an_absolute_rules_dir_changes_no_verdict(
        repo):
    """An absolute rules_dir is resolved on disk only up to the repository;
    from there it is resolved at the head, so the committed `link -> strict`
    decides, not a working tree that points it at `soft`."""
    for name, kind in (("strict", "behaviour"), ("soft", "wording")):
        (repo / name).mkdir()
        (repo / name / "wiki-fidelity.md").write_text(
            _rule("wiki-fidelity", kind))
    link = repo / "link"
    link.symlink_to("strict")
    yml = repo / "repo.yaml"
    yml.write_text(yml.read_text().replace(
        "rules_dir: .warden/rules", f"rules_dir: {link}"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "an absolute rules_dir through a link")
    r1, r2 = _two_rounds(repo)
    link.unlink()
    link.symlink_to("soft")
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1, r2)
    assert out.returncode == 1, out.stdout + out.stderr
    assert json.loads((r2 / "classify.json").read_text())["rule_kinds"] \
        == {"wiki-fidelity": "behaviour"}


def test_an_absolute_rules_dir_through_a_symlink_loop_outside_the_repository_is_refused(
        repo, tmp_path):
    """The walk to the repository follows symlinks on disk, so it is bounded:
    `loop -> loop` is refused, not followed forever. The timeout turns a walk
    that never ends into a failure instead of a hung suite."""
    loop = tmp_path / "loop"
    loop.symlink_to("loop")
    declared = str(loop / "rules")
    yml = repo / "repo.yaml"
    yml.write_text(yml.read_text().replace(
        "rules_dir: .warden/rules", f"rules_dir: {declared}"))
    _commit_rules(repo, {"wiki-fidelity": "wording"})
    r1, r2 = _two_rounds(repo)
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1, r2,
                        timeout=10)
    assert out.returncode == 2, out.stdout + out.stderr
    assert declared in out.stderr
    assert not (r2 / "classify.json").exists()


def test_an_absolute_rules_dir_that_climbs_out_of_the_repository_and_back_in_classifies(
        repo, tmp_path):
    """`<repo>/../alias/.warden/rules` with `alias -> <repo>`: the `..` right
    where the walk enters the repository climbs a real directory, so the walk
    leaves it and comes back in through the alias, and is not refused."""
    (tmp_path / "alias").symlink_to(repo)
    declared = f"{repo}/../alias/.warden/rules"
    yml = repo / "repo.yaml"
    yml.write_text(yml.read_text().replace(
        "rules_dir: .warden/rules", f"rules_dir: {declared}"))
    _commit_rules(repo, {"wiki-fidelity": "wording"})
    r1, r2 = _two_rounds(repo)
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1, r2)
    assert out.returncode == 0, out.stdout + out.stderr
    report = json.loads((r2 / "classify.json").read_text())
    assert report["verdict"] == "clean"
    assert report["rule_kinds"] == {"wiki-fidelity": "wording"}


@pytest.mark.parametrize("target, expected", [("r", 0), ("x", 2)])
def test_an_absolute_rules_dir_through_a_symlinked_parent_is_resolved_first(
        repo, tmp_path, target, expected):
    """The repo's own rules dir spelled through a symlinked parent is inside
    the repository; a path that resolves outside it is still refused."""
    (tmp_path / "x" / ".warden" / "rules").mkdir(parents=True)
    (tmp_path / "x" / ".warden" / "rules" / "wiki-fidelity.md").write_text(
        _rule("wiki-fidelity", "wording"))
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / target)
    declared = str(alias / ".warden" / "rules")
    yml = repo / "repo.yaml"
    yml.write_text(yml.read_text().replace(
        "rules_dir: .warden/rules", f"rules_dir: {declared}"))
    _commit_rules(repo, {"wiki-fidelity": "wording"})
    r1, r2 = _two_rounds(repo)
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1, r2)
    assert out.returncode == expected, out.stdout + out.stderr
    if expected == 0:
        assert json.loads((r2 / "classify.json").read_text())["rule_kinds"] \
            == {"wiki-fidelity": "wording"}


def test_a_repo_yaml_absent_at_the_round_head_is_refused_not_read_off_disk(
        repo):
    """The working tree holds repo.yaml; the round head does not. Reading the
    working tree's copy instead is the fallback this refusal exists to close."""
    _commit_rules(repo, {"wiki-fidelity": "wording"})
    _git(repo, "rm", "-q", "--cached", "repo.yaml")
    _git(repo, "commit", "-qm", "repo.yaml untracked at the head")
    assert (repo / "repo.yaml").is_file()
    r1 = _mint(repo, _sha(repo), _sha(repo, "main~1"))
    out = _classify_cli(repo, [_f("new.py", severity="MEDIUM",
                                  rule_id="wiki-fidelity")], r1)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "repo.yaml" in out.stderr
    assert not (r1 / "classify.json").exists()


# ---------- the number `round new` mints, counted from this same chain --------

def test_the_next_round_number_counts_the_rounds_of_this_chain(repo):
    """`warden round new` numbers a round from the chain `classify` proves,
    never from a counter of its own: the first round of a base..head is 1,
    and the next round on that line is 2."""
    base = _sha(repo, "main")
    assert repair_mod.next_round_number(repo, base, _sha(repo)) == 1
    _mint(repo, _sha(repo), base)
    _repair(repo)          # the repair commit between the rounds
    assert repair_mod.next_round_number(repo, base, _sha(repo)) == 2


def test_a_re_mint_at_the_same_head_is_the_same_round(repo):
    """One head is one round, however many directories it was minted into.

    A round is re-minted whenever a reviewer crashes or the round is
    abandoned, and no commit need land in between. If that second mint
    advanced the number, minting twice would buy the SHORTER crew a later
    round declares — the dodge the minted number exists to close, reopened
    at the counter that closes it.
    """
    base = _sha(repo, "main")
    _mint(repo, _sha(repo), base)
    _mint(repo, _sha(repo), base)
    assert repair_mod.next_round_number(repo, base, _sha(repo)) == 1


def test_the_next_round_number_refuses_a_round_it_cannot_read(repo):
    """Fail-closed: a round.json warden cannot parse is not a round that is
    not there. Counting on regardless would number the new round from a chain
    warden could not read."""
    base = _sha(repo, "main")
    d = _mint(repo, _sha(repo), base)
    (d / "round.json").write_text("{ not json")
    with pytest.raises(repair_mod.RepairError):
        repair_mod.next_round_number(repo, base, _sha(repo))


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root reads a 0o000 directory, so the wall the "
                           "test builds denies nothing")
def test_an_unlistable_rounds_root_refuses_as_a_repair_error(repo):
    """A rounds root warden cannot list must refuse as `RepairError`.

    `next_round_number`'s own docstring promises it ("Raises RepairError when
    a round directory cannot be read"), and `cli._cmd_round` catches exactly
    that class. A bare `sorted(rounds_root.iterdir())` raises `PermissionError`
    straight past that handler: the operator gets a traceback instead of the
    exit-2 refusal, on the one path whose job is to say the chain could not be
    read rather than to number a round from a chain nobody saw.
    """
    rounds_root = repo / ".warden" / "out" / "rounds"
    rounds_root.mkdir(parents=True)
    base, head = _sha(repo, "main~1"), _sha(repo)
    rounds_root.chmod(0o000)
    try:
        with pytest.raises(repair_mod.RepairError, match="could not be listed"):
            repair_mod.next_round_number(repo, base, head)
    finally:
        rounds_root.chmod(0o700)


# ---------- the count the COMMITTED shards carry ----------------------------

def _shard(repo: Path, name: str, rounds: list[int | None], *,
           sha: str | None = None, body: str | None = None) -> str:
    """One committed attestation shard, as `warden memory ingest` writes one.

    `rounds` are the `reviewers[].round` labels the shard carries; a None is
    a legacy reviewer entry that carries no round at all, which 141 of this
    repo's 302 committed shards are.
    """
    (repo / ".warden" / "memory" / "attest").mkdir(parents=True, exist_ok=True)
    path = f".warden/memory/attest/{name}"
    if body is None:
        body = json.dumps({
            "schema": 1, "source": "attest", "sha": sha or _sha(repo),
            "verdict": "clean",
            "reviewers": [{"role": "code-reviewer", "returned": True,
                           **({} if r is None else {"round": r})}
                          for r in rounds]})
    (repo / path).write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"shard: {name}")
    return path


def test_the_round_count_survives_a_rebase_that_resets_the_rounds_dir(
        repo):
    """THE REGRESSION. A branch that has spent its rounds cannot mint a fresh
    round 1 by rebasing.

    Measured before the fix, on a fixture shaped like this one: the count
    came from the gitignored `.warden/out/rounds/` alone, a rebase left every
    recorded head off this line of history, and the next round numbered 1
    again — for either base, with nothing recording that it had happened. A
    cap a rebase silently resets is advice, not a hard stop. The committed
    shards are IN the range, so the rewritten commits carry them: the count
    holds, and the directory's collapse to zero surfaces as a reported
    disagreement instead of as a fresh budget.
    """
    base = _sha(repo, "main~1")
    _mint(repo, _sha(repo), base)               # round 1
    _repair(repo)                               # its repair commit
    _shard(repo, "round-1.json", [1])           # round 1's evidence, COMMITTED
    _mint(repo, _sha(repo), base)               # round 2
    _shard(repo, "round-2.json", [2])           # round 2's evidence, COMMITTED
    before = repair_mod.round_count(repo, base, _sha(repo))
    assert (before.from_shards, before.from_rounds_dir) == (2, 2)
    assert before.number == 3 and before.disagreement is None

    _git(repo, "rebase", "-q", "main")
    head = _sha(repo)
    for base_ref in ("main~1", "main"):
        after = repair_mod.round_count(repo, _sha(repo, base_ref), head)
        assert after.from_rounds_dir == 0, (
            "the fixture no longer reproduces the defect: the rebase has to "
            "leave every minted round off this line of history")
        assert after.from_shards == 2, after
        assert after.number == 3, (
            f"the rebase bought a fresh round {after.number} against "
            f"{base_ref} — the cap is advice again")
        assert repair_mod.next_round_number(
            repo, _sha(repo, base_ref), head) == 3
        assert after.disagreement is not None and \
            "SHARDS are ahead" in after.disagreement


def test_the_count_is_keyed_on_the_add_not_on_the_sha_the_shard_names(
        repo):
    """The trap: a shard's own `sha` does not survive the rebase its rounds
    have to.

    A shard records the commit it judged. A rebase rewrites that commit, so
    "shards whose `sha` is in the range" finds none of them and the count
    resets exactly as the directory count did. The key is the shard FILE
    being ADDED by a commit in `base..head` — which the rewritten commit
    carries, whatever its sha becomes. The assertion on `rev-list` is what
    keeps this test honest: if the sha the shard names ever IS in the range,
    the fixture stopped reproducing the trap and the rest proves nothing.
    """
    judged = _sha(repo)
    path = _shard(repo, "round-1.json", [1], sha=judged)
    _git(repo, "rebase", "-q", "main")
    head, base = _sha(repo), _sha(repo, "main")
    assert judged not in _git(repo, "rev-list", f"{base}..{head}").split(), (
        "the fixture no longer reproduces the trap: the sha the shard names "
        "is still in the range, so a sha-keyed count would pass here")
    count = repair_mod.round_count(repo, base, head)
    assert count.shards == ((path, 1),)
    assert count.from_shards == 1 and count.number == 2


def test_a_disagreement_between_the_two_counts_is_reported(repo):
    """Never silently resolved: a silent reconciliation is how this class of
    bug hides. Both counts, the direction of the gap, and every shard counted
    are named, and the number is the higher of the two plus one."""
    base = _sha(repo, "main~1")
    _shard(repo, "round-1.json", [1])
    _shard(repo, "round-2.json", [2])
    count = repair_mod.round_count(repo, base, _sha(repo))
    assert (count.from_shards, count.from_rounds_dir) == (2, 0)
    assert count.number == 3
    report = count.disagreement
    assert report is not None
    assert "2 prior round(s)" in report and "holds 0" in report
    assert "SHARDS are ahead" in report, report
    for name in ("round-1.json (round 1)", "round-2.json (round 2)"):
        assert name in report, report


def test_a_round_whose_shard_is_not_committed_yet_is_the_other_gap(repo):
    """The ordinary mid-review direction: the round is minted, its shard is
    not committed yet, and the DIRECTORY is the only witness. It raises the
    floor — taking the shards alone would number round 2 as round 1 all
    through a live review — and the gap is reported as what it is."""
    base = _sha(repo, "main~1")
    _mint(repo, _sha(repo), base)
    _repair(repo)
    count = repair_mod.round_count(repo, base, _sha(repo))
    assert (count.from_shards, count.from_rounds_dir) == (0, 1)
    assert count.number == 2
    assert count.disagreement is not None
    assert "DIRECTORY is ahead" in count.disagreement


def test_a_shard_that_records_no_round_reads_as_unrecorded(repo):
    """141 of this repo's 302 committed shards predate `reviewers[].round`.
    They lower nothing and refuse nothing — the same precedent as an absent
    `verdict` — so promoting the field to load-bearing rewrites no shard."""
    base = _sha(repo, "main~1")
    path = _shard(repo, "legacy.json", [None])
    count = repair_mod.round_count(repo, base, _sha(repo))
    assert count.from_shards == 0 and count.number == 1
    assert count.unrecorded == (path,) and count.shards == ()


def test_a_shard_deleted_later_in_the_range_still_counts(repo):
    """Each shard is read AT THE COMMIT THAT ADDED IT, so a delete inside the
    range does not un-count the round it recorded. What a delete does is put
    the reset on the `.warden/memory/**` surface `evidence-intact` judges,
    where reading the tree at the head would have hidden it."""
    base = _sha(repo, "main~1")
    path = _shard(repo, "round-1.json", [1])
    _git(repo, "rm", "-q", path)
    _git(repo, "commit", "-qm", "drop the shard")
    count = repair_mod.round_count(repo, base, _sha(repo))
    assert count.from_shards == 1 and count.number == 2


def test_a_shard_the_base_branch_carries_is_not_this_changes_round(repo):
    """The BASE-BRANCH half of "another PR's rounds are not this change's",
    and no more than that: a shard reachable from the base is outside
    `base..head` however the branch came by it. The sibling-branch half —
    the one the range does not exclude for free — is the test below, which
    is where the derivation has to do the work (round-1 finding 2)."""
    _git(repo, "checkout", "-q", "main")
    _shard(repo, "someone-elses.json", [2])
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "merge", "-q", "--no-edit", "main")
    count = repair_mod.round_count(repo, _sha(repo, "main"), _sha(repo))
    assert count.from_shards == 0 and count.number == 1


def test_a_sibling_branch_merged_in_does_not_raise_this_changes_round(
        repo):
    """ROUND-1 FINDING 1. A sibling's shard is not this change's round, and
    an ordinary `git merge` of that sibling must not number this change's
    FIRST review as round 2.

    The sibling is NOT on the base, so the range does not exclude its commit
    for free: `rev-list base..head` lists it, its shard's ADD is in the
    range, and the count rose to 2 before the repair. Round 2's declared
    crew is one reviewer where round 1's is two (graph.yaml), so an accident
    — merging a branch that happens to carry review evidence — bought the
    smaller crew the round binding exists to protect. The walk is
    first-parent now, so another line's adds are not this change's.
    """
    base = _sha(repo, "main")
    _git(repo, "checkout", "-q", "-b", "sibling", "main")
    _shard(repo, "siblings.json", [1])
    sibling_shard = _sha(repo)
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "merge", "-q", "--no-edit", "sibling")
    head = _sha(repo)
    assert sibling_shard in _git(repo, "rev-list", f"{base}..{head}").split(), (
        "the fixture no longer reproduces the defect: the sibling's shard "
        "commit has to BE in the two-dot range, or nothing here is tested")
    count = repair_mod.round_count(repo, base, head)
    assert count.from_shards == 0 and count.shards == (), count
    assert count.number == 1, (
        f"a merged sibling numbered this change's first review as "
        f"{count.number} — round 2's crew is one reviewer, round 1's is two")


def test_a_shard_warden_cannot_read_refuses_rather_than_numbering_past(
        repo):
    """Fail-closed, as the round directories already are: a shard warden
    cannot parse is not an absent round, and numbering past it hands the
    branch the fresh budget this derivation exists to refuse."""
    base = _sha(repo, "main~1")
    _shard(repo, "broken.json", [], body="{ not json")
    with pytest.raises(repair_mod.RepairError, match="not JSON warden can"):
        repair_mod.round_count(repo, base, _sha(repo))


@pytest.mark.parametrize("value", ["2", 2.0, 0, True])
def test_a_reviewer_round_in_a_shape_warden_does_not_read_refuses(
        repo, value):
    """`2.0`, `"2"`, `0` and `true` are refused rather than read as absent —
    one reader, `_round_stated`, for the payload's `round` labels and the
    shards', because a stated value in the wrong shape is not a missing one
    in either place."""
    base = _sha(repo, "main~1")
    _shard(repo, "odd.json", [], body=json.dumps(
        {"reviewers": [{"role": "code-reviewer", "round": value}]}))
    with pytest.raises(repair_mod.RepairError, match="states round"):
        repair_mod.round_count(repo, base, _sha(repo))


def test_round_new_prints_the_disagreement_it_numbered_from(repo):
    """The report has to reach the operator, not just the dataclass: they are
    the one who can tell a shard not committed yet from a chain that lost its
    rounds to a rebase."""
    _shard(repo, "round-1.json", [1])
    out = _cli(repo, "round", "new", "--base", "main")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "as round 2" in out.stderr, out.stderr
    assert "committed shards" in out.stderr, out.stderr
    assert "round-1.json (round 1)" in out.stderr, out.stderr
    minted = json.loads((Path(out.stdout.strip()) / "round.json").read_text())
    assert minted["round"] == 2, minted


def test_an_unrecorded_shard_is_reported_even_when_the_counts_agree(
        repo):
    """ROUND-1 FINDING 5. The reading that had no surface.

    A range whose only committed shards record no round is the shape where
    both counts are 0, they AGREE, and the number reads as a clean round 1
    over evidence that a review already happened. The unrecorded shards were
    computed and then reported only inside the disagreement string, which is
    built only when the counts differ — so `warden round new` said nothing at
    all. `notes` carries every reading, and the CLI prints them all.
    """
    base = _sha(repo, "main~1")
    path = _shard(repo, "legacy.json", [None])
    count = repair_mod.round_count(repo, base, _sha(repo))
    assert count.disagreement is None and count.unrecorded == (path,)
    assert any(path in n and "raise no floor" in n for n in count.notes), (
        f"the unrecorded shard reaches no surface: {count.notes}")

    out = _cli(repo, "round", "new", "--base", "main")
    assert out.returncode == 0, out.stdout + out.stderr
    assert path in out.stderr and "record no round" in out.stderr, out.stderr


def test_a_shard_with_no_reviewers_at_all_is_the_legacy_shape(repo):
    """9 of this repo's 302 committed shards carry no `reviewers` key. ABSENT
    is the tolerated shape — it reads as unrecorded, exactly as a roster with
    no `round` does — and it is the ONLY one, per the test below."""
    base = _sha(repo, "main~1")
    path = _shard(repo, "ancient.json", [], body=json.dumps(
        {"schema": 1, "source": "attest", "verdict": "clean"}))
    count = repair_mod.round_count(repo, base, _sha(repo))
    assert count.from_shards == 0 and count.unrecorded == (path,)


@pytest.mark.parametrize("reviewers", [
    {"role": "code-reviewer", "round": 2},      # an object, not an array
    "code-reviewer round 2",                    # a string
    ["code-reviewer"],                          # an array of non-objects
])
def test_a_reviewers_roster_warden_cannot_read_refuses(repo, reviewers):
    """ROUND-1 FINDING 5, second half. Each of these was silently read as "no
    round" and lowered the floor to whatever the rest of the range carried.

    The attestation schema types `reviewers` as a required array of objects,
    so every shape here is schema-invalid — and the module's own policy for a
    `round` in the wrong shape is refusal, for the same reason: a stated
    value warden does not read is not a missing one. 0 of this repo's 302
    committed shards carry any of these shapes, so nothing legacy is refused
    by this.
    """
    base = _sha(repo, "main~1")
    _shard(repo, "odd-roster.json", [], body=json.dumps(
        {"schema": 1, "source": "attest", "reviewers": reviewers}))
    with pytest.raises(repair_mod.RepairError, match="attestation schema"):
        repair_mod.round_count(repo, base, _sha(repo))


# ---------- the two roster shapes the refusal must reach --------------------

def test_a_reviewers_explicitly_null_is_refused_not_read_as_legacy(repo):
    """`reviewers: null` is schema-invalid and used to lower the floor.

    The guard read `doc.get("reviewers")` and then tested `is None`, which
    cannot tell an ABSENT key — the legacy shape, 9 of this repo's 302
    committed shards — from a key explicitly set to `null`. Measured then:
    `RoundCount(number=1, from_shards=0, unrecorded=('...odd.json',))`, no
    refusal, while the page promised exit
    2 for "a `reviewers` that is not an array". The schema types `reviewers`
    as a REQUIRED array, so a stated `null` is exactly the shape a stated
    string is, and it is refused the same way. The test is now on the KEY.
    """
    base = _sha(repo, "main~1")
    _shard(repo, "null-roster.json", [], body=json.dumps(
        {"schema": 1, "source": "attest", "reviewers": None}))
    with pytest.raises(repair_mod.RepairError, match="NoneType"):
        repair_mod.round_count(repo, base, _sha(repo))


def test_an_empty_reviewers_array_is_refused_against_min_items_one(repo):
    """`reviewers: []` is a list, so it cleared the isinstance check,
    contributed no entry, and fell through to `unrecorded` — a roster stated
    as NOBODY, read as the legacy shape that predates the field. The shipped
    schema's floor is `minItems: 1`; a review with no reviewer is not a
    review, and no shard `attest write` produces can carry one."""
    base = _sha(repo, "main~1")
    _shard(repo, "empty-roster.json", [], body=json.dumps(
        {"schema": 1, "source": "attest", "reviewers": []}))
    with pytest.raises(repair_mod.RepairError, match="EMPTY array"):
        repair_mod.round_count(repo, base, _sha(repo))


def test_an_absent_reviewers_key_is_still_the_tolerated_legacy_shape(
        repo):
    """The other half of the fix, and the one that must NOT move: 9 real
    committed shards carry no `reviewers` key at all, and they still read as
    unrecorded rather than refusing. Tightening the two stated shapes above
    is only safe because this one is distinguished from them, not because
    refusing more is better."""
    base = _sha(repo, "main~1")
    path = _shard(repo, "ancient-2.json", [], body=json.dumps(
        {"schema": 1, "source": "attest", "verdict": "clean"}))
    count = repair_mod.round_count(repo, base, _sha(repo))
    assert count.unrecorded == (path,) and count.from_shards == 0


# ---------- the rewrite that drops the shard commits -------------------------

def _drop_the_shard_commits(repo: Path) -> tuple[str, str, str]:
    """A history rewrite that DROPS the commits which added the shards.

    `rebase --onto`, the drop shape: the work commit before
    the shards stays, the two shard commits are replayed over nothing, and
    the branch's new history carries no `.warden/memory/**` change at all.
    Returns (base, the head before the rewrite, the head after).
    """
    base = _sha(repo, "main")
    keep = _sha(repo)                 # C1, the work, stays
    _shard(repo, "round-1.json", [1])
    _shard(repo, "round-2.json", [2])
    (repo / "more.py").write_text("y = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "W2: more work")
    before = _sha(repo)
    _git(repo, "rebase", "-q", "--onto", keep, f"{before}~1", "feature")
    return base, before, _sha(repo)


def test_a_rebase_onto_that_drops_the_shard_commits_does_not_reset(repo):
    """THE REGRESSION. The count is keyed on the shard file being ADDED by a
    commit in the range, so a rewrite that drops those commits leaves the
    range with nothing to count — and, unlike a delete, nothing to report
    either: `evidence-intact` is diff-scoped, and the rewritten range carries
    no `.warden/memory/**` change to dispatch it on. Measured before the fix:
    `number=1, from_shards=0, disagreement=None`, a fresh budget bought in
    silence.

    The witness that survives is OUTSIDE the branch: a head the change
    pushed earlier, which the forge holds and the rewrite does not reach.
    With it supplied the floor holds at 2 and the drop is REPORTED; without
    it the residual is unchanged, which this test pins in both directions so
    the mechanism is never mistaken for a property of the range.
    """
    base, before, head = _drop_the_shard_commits(repo)
    # NO COMMIT in the rewritten range touches the shard store at all. That
    # is the drop's signature, and the per-commit view is the one that can
    # tell it from a delete: `warden round new`'s review package carries the
    # range's LOG, and a rule is dispatched on a file the range changed.
    carried = _git(repo, "log", "--name-status", "--format=", f"{base}..{head}")
    assert ".warden/memory" not in carried, (
        "the fixture no longer reproduces the defect: a commit in the "
        f"rewritten range still touches the shard store\n{carried}")

    blind = repair_mod.round_count(repo, base, head)
    assert (blind.from_shards, blind.number) == (0, 1), blind
    assert blind.rewrite is None and blind.notes == ()

    seen = repair_mod.round_count(repo, base, head, prior_heads=(before,))
    assert seen.from_shards == 0, "the RANGE still carries nothing — it was rewritten"
    assert seen.from_prior_heads == 2 and seen.number == 3, seen
    assert [(r.head, r.rounds) for r in seen.prior_heads] == [(before, 2)]
    # the SHARDS ride with the reading, because they are the only thing the
    # refusal can name when the range itself carries none (round-1 F-5)
    assert sorted(p for p, _ in seen.prior_heads[0].shards) == [
        ".warden/memory/attest/round-1.json",
        ".warden/memory/attest/round-2.json"], seen.prior_heads
    assert seen.rewrite is not None and "REWRITE" in seen.rewrite
    assert seen.rewrite in seen.notes


def test_the_rewrite_report_is_not_the_delete_report(repo):
    """The two reset shapes stay DISTINCT rather than being flattened into
    one.

    A shard DELETED inside the range still counts off the range alone — the
    ADD is the key — and its deletion is a `.warden/memory/**` change the
    reviewed diff carries, which is the surface `evidence-intact` judges. No
    outside witness is needed and none is reported. Only the DROP needs one.
    """
    base = _sha(repo, "main~1")
    path = _shard(repo, "round-1.json", [1])
    before_delete = _sha(repo)
    _git(repo, "rm", "-q", path)
    _git(repo, "commit", "-qm", "drop the shard")
    head = _sha(repo)
    carried = _git(repo, "log", "--name-status", "--format=", f"{base}..{head}")
    assert f"D\t{path}" in carried, (
        "the delete has to be carried by a commit in the range as a "
        f".warden/memory/** change, or the two shapes really are one\n{carried}")
    count = repair_mod.round_count(repo, base, head)
    assert count.from_shards == 1 and count.number == 2
    assert count.rewrite is None, (
        "the delete case reported a rewrite — the distinction the docs and "
        "the docstring draw has collapsed back into one claim")
    # And with a witness actually consulted, so the assertion above is not
    # carried by `prior_heads` being empty (round-1 F-6, granted residual):
    # the pre-delete head AGREES with the range, which is the property that
    # tells the delete apart from the drop.
    witnessed = repair_mod.round_count(repo, base, head,
                                       prior_heads=(before_delete,))
    assert witnessed.from_prior_heads == 1 and witnessed.from_shards == 1
    assert witnessed.rewrite is None, (
        "a witness that AGREES with the range reported a rewrite — the "
        "report no longer distinguishes a dropped floor from an intact one")


def test_a_prior_head_this_repository_does_not_have_refuses(repo):
    """Fail-closed, as every other chain read here is: a witness warden could
    not read is not a witness that agreed. Skipping it would silently return
    the blind count under a flag whose whole purpose is not to be blind."""
    base = _sha(repo, "main~1")
    with pytest.raises(repair_mod.RepairError, match="prior head"):
        repair_mod.round_count(repo, base, _sha(repo),
                               prior_heads=("0" * 40,))


def test_a_prior_head_already_in_this_history_adds_nothing(repo):
    """An ancestor's `base..prior_head` is a subset of this range, so it can
    only agree. The ordinary push — where `before` is simply the last commit
    — must therefore cost nothing and report nothing, or CI would narrate a
    rewrite on every PR."""
    base = _sha(repo, "main~1")
    _shard(repo, "round-1.json", [1])
    before = _sha(repo)
    (repo / "more.py").write_text("y = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "W2: the ordinary next push")
    count = repair_mod.round_count(repo, base, _sha(repo),
                                   prior_heads=(before,))
    assert count.from_shards == 1 and count.from_prior_heads == 1
    assert count.number == 2 and count.rewrite is None
    assert [(r.head, r.rounds) for r in count.prior_heads] == [(before, 1)]
