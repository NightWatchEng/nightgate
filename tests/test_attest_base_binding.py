"""The base a shard records is the round's, not whatever the name resolves to.

THE LIVE DEFECT, and it reached main. `warden round new --base origin/main`
resolves that name once, records the sha in `round.json`, and builds the
review package from it — the reviewers read exactly that range. `warden attest
write --base origin/main` then resolved the SAME NAME a second time, hours
later. Another change landed in between, so the shard recorded a tip that is
not even an ancestor of the branch, and its declared two-dot range reported
the other change's merged work as DELETIONS: 10 files where the round the
shard attests had 5.

Every test here drives that sequence — mint, move the ref, attest — and holds
the recorded base to the round's.

WHAT IS DELIBERATELY NOT ENFORCED, because it was measured before it was
rejected: a base that is not an ancestor of the head is REPORTED and never
refused. 88 of this repository's 353 committed shards record one — a quarter of
the platform's own attestations, which is the COST of a refusal. That figure
does not say those 88 are innocent, and this file does not: the round manifests
that would tell a base minted against an already-advanced `origin/main` from
one this defect moved are gitignored and gone. What settles the refusal is that
the legitimate shape is reachable at all, and
`test_a_non_ancestor_base_is_a_reading_and_never_a_refusal` builds it.

THE SECOND DEFECT, one seam over, and the last three tests here are its. The
first was a base RE-RESOLVED at write time; this one is a base the round
itself minted against a branch that had already moved, which `base_binding`
faithfully stamps because it is the range the reviewers read. The reading was
computed and printed and then stopped at stderr and a gitignored run manifest,
so the committed shard carried the moved base with no committed caveat. It is
stamped into the document now, in all three states, on `round_binding`'s
precedent — and it still never refuses.
"""

import json
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_YAML, seed_rules
from warden import attest as attest_mod
from warden import cli as cli_mod
from warden import runs as runs_mod

ROLE = "scoped-re-reviewer"


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(root, message):
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    """A branch off `main`, plus a way to move `main` under it.

    Returns `(root, fork_point, advance)`. `advance()` lands an unrelated
    commit on `main` — the sibling PR that merged while this branch was in
    flight — and returns its sha, leaving the branch checked out.
    """
    root = tmp_path / "repo"
    root.mkdir()
    seed_rules(root, "docs-drift")
    (root / "repo.yaml").write_text(REPO_YAML)
    (root / ".gitignore").write_text(".warden/out/\n")
    (root / attest_mod.SHARD_DIR).mkdir(parents=True)
    (root / "a.py").write_text("1\n")
    _git(root, "init", "-q", "-b", "main")
    fork_point = _commit(root, "base")

    _git(root, "checkout", "-q", "-b", "work")
    (root / "b.py").write_text("1\n")
    _commit(root, "work")

    def advance():
        _git(root, "checkout", "-q", "main")
        (root / "sibling.py").write_text("landed elsewhere\n")
        moved = _commit(root, "a sibling PR lands")
        _git(root, "checkout", "-q", "work")
        return moved

    return root, fork_point, advance


def _mint(root, base="main", rnd=1):
    """`warden round new`, through the CLI, so `round.json` is warden's own."""
    import io
    import contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli_mod.main(["round", "new", "--base", base])
    assert code == 0, out.getvalue()
    round_dir = Path(out.getvalue().strip())
    reviewers = round_dir / "reviewers"
    reviewers.mkdir(parents=True, exist_ok=True)
    (reviewers / f"{ROLE}.json").write_text(json.dumps(
        {"role": ROLE, "round": rnd, "reviewed": ["b.py"]}))
    return round_dir, reviewers


def _payload(rnd=1):
    return {
        "reviewers": [{"role": ROLE, "agent": "subagent", "round": rnd,
                       "returned": True, "findings": 0,
                       "output": f"{ROLE}.json", "outcome": "reviewed-clean"}],
        "findings": [],
        "verdict": "clean",
    }


def _latest_run(root):
    runs = sorted(p for p in (root / ".warden" / runs_mod.OUT_DIRNAME).iterdir()
                  if p.is_dir() and p.name.endswith("-attest"))
    return runs[-1]


def _write(root, tmp_path, base, reviewers):
    findings = tmp_path / "payload.json"
    findings.write_text(json.dumps(_payload()))
    return cli_mod.main(["attest", "write", "--findings", str(findings),
                         "--base", base, "--review-dir", str(reviewers)])


# --- the reproduction ---------------------------------------------------------


def test_a_base_ref_that_moves_between_the_mint_and_the_write_is_not_stamped(
        repo, tmp_path, monkeypatch, capsys):
    """THE LIVE BUG, in the order it sprang: mint, the ref moves, write.

    The assertion the bead asked for is the ancestry one — the recorded base
    must be an ancestor of the head it stamps — and it is made here against a
    fixture that is checked to still reproduce: the moved `main` is asserted
    NOT to be an ancestor, so the test cannot quietly stop being about this.
    """
    root, fork_point, advance = repo
    monkeypatch.chdir(root)
    round_dir, reviewers = _mint(root)
    assert json.loads((round_dir / "round.json").read_text())[
        "base_sha"] == fork_point

    moved = advance()
    head = _git(root, "rev-parse", "HEAD")
    # The fixture still reproduces: the name now resolves to a commit that is
    # not on this branch's line at all.
    assert subprocess.run(["git", "merge-base", "--is-ancestor", moved, head],
                          cwd=root).returncode == 1

    assert _write(root, tmp_path, "main", reviewers) == 0
    doc = json.loads((_latest_run(root) / "attestation.json").read_text())
    assert doc["base_sha"] == fork_point
    assert doc["base_sha"] != moved
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", doc["base_sha"], head],
        cwd=root).returncode == 0
    capsys.readouterr()


def test_the_moved_ref_is_reported_and_recorded_not_silently_corrected(
        repo, tmp_path, monkeypatch, capsys):
    """A base taken from somewhere other than the flag is a fact about the
    run, and the unattended cage has no terminal — so it rides the run
    manifest as well as stderr, the way every other reading here does."""
    root, fork_point, advance = repo
    monkeypatch.chdir(root)
    _round_dir, reviewers = _mint(root)
    moved = advance()

    assert _write(root, tmp_path, "main", reviewers) == 0
    err = capsys.readouterr().err
    assert moved[:12] in err and fork_point[:12] in err

    manifest = json.loads((_latest_run(root) / "manifest.json").read_text())
    assert manifest["base_binding"] == attest_mod.BASE_ROUND_DIFFERS
    assert manifest["base_ancestry"] == "ancestor"
    # The state measures that two resolutions differ and says only that: a
    # moved ref, a second ref name and a peeled tag all look alike here
    # (round 1).
    assert "moved ref, a different ref name" in manifest["base_binding_detail"]
    assert "ref moved between" not in err


def test_the_declared_range_does_not_report_a_sibling_change_as_deletions(
        repo, tmp_path, monkeypatch, capsys):
    """THE SYMPTOM ITSELF, which is what made the live shard a false record:
    `git diff <recorded base>..<head>` reported the sibling change's merged
    work as DELETIONS — 10 files where the round had 5."""
    root, fork_point, advance = repo
    monkeypatch.chdir(root)
    _round_dir, reviewers = _mint(root)
    moved = advance()
    head = _git(root, "rev-parse", "HEAD")

    assert _write(root, tmp_path, "main", reviewers) == 0
    doc = json.loads((_latest_run(root) / "attestation.json").read_text())

    declared = _git(root, "diff", "--name-status",
                    f"{doc['base_sha']}..{head}").split()
    assert declared == ["A", "b.py"], declared
    # And the range the name NOW resolves to, which is what was recorded
    # before this fix: the sibling's file, as a deletion this branch never
    # made. Asserted so the fixture cannot quietly stop reproducing.
    would_have = _git(root, "diff", "--name-status", f"{moved}..{head}").split()
    assert "sibling.py" in would_have and "D" in would_have, would_have
    capsys.readouterr()


# --- the states, each one its own -------------------------------------------


def test_with_no_review_dir_the_named_base_is_what_is_stamped(repo):
    """No round, no minted base. The flag is all there is, and the state says
    so rather than reading as a round that agreed."""
    root, fork_point, _advance = repo
    head = _git(root, "rev-parse", "HEAD")
    base, state, detail = attest_mod.base_binding(root, None, fork_point, head)
    assert (base, state) == (fork_point, attest_mod.BASE_NO_ROUND)
    assert "--review-dir" in detail


def test_an_unminted_review_dir_is_not_a_round_with_a_base(repo, tmp_path):
    """A directory warden did not mint has no base to lend. Same fallback,
    its own sentence — `round_binding` records `unminted` for the same
    directory and the two readings must not contradict each other."""
    root, fork_point, _advance = repo
    head = _git(root, "rev-parse", "HEAD")
    handed = tmp_path / "handed"
    handed.mkdir()
    base, state, detail = attest_mod.base_binding(
        root, handed, fork_point, head)
    assert (base, state) == (fork_point, attest_mod.BASE_NO_ROUND)
    assert "round.json" in detail
    assert attest_mod.round_binding(handed, head)[0] == "unminted"


def test_a_round_minted_for_another_head_does_not_lend_its_base(
        repo, tmp_path):
    """The ordering argument, pinned. Taking a foreign round's base first
    would import a range from the very artifact `round_binding` is about to
    disprove — so on a head mismatch the flag is used and the refusal worth
    reading is the one that fires."""
    root, fork_point, _advance = repo
    head = _git(root, "rev-parse", "HEAD")
    foreign = tmp_path / "foreign"
    (foreign / "reviewers").mkdir(parents=True)
    runs_mod.write_round_manifest(foreign, base="main", base_sha="b" * 40,
                                  head="c" * 40, branch="elsewhere",
                                  round_no=1)
    base, state, _detail = attest_mod.base_binding(
        root, foreign, fork_point, head)
    assert (base, state) == (fork_point, attest_mod.BASE_NAMED)
    with pytest.raises(attest_mod.AttestError):
        attest_mod.round_binding(foreign, head)


def test_a_round_base_this_repository_does_not_have_falls_back_by_name(
        repo, tmp_path):
    """REPORTED, not refused, and the reason is argued in `base_binding`: the
    round directory is the builder's own tree, so refusing a base it cannot
    resolve closes no hole — it only loses the one base that can be stamped."""
    root, fork_point, _advance = repo
    head = _git(root, "rev-parse", "HEAD")
    minted = tmp_path / "minted"
    (minted / "reviewers").mkdir(parents=True)
    runs_mod.write_round_manifest(minted, base="main", base_sha="a" * 40,
                                  head=head, branch="work", round_no=1)
    base, state, detail = attest_mod.base_binding(
        root, minted, fork_point, head)
    assert (base, state) == (fork_point,
                             attest_mod.BASE_ROUND_UNRESOLVABLE)
    assert "aaaaaaaaaaaa" in detail


def test_an_unreadable_round_manifest_reports_and_leaves_the_refusal(
        repo, tmp_path):
    """ROUND 1. Refusing here put the manifest's message ahead of
    `verify_roster`'s, inverting the order `build` argues for — both can
    refuse, the roster is the stronger claim. So this REPORTS, and
    `round_binding` (which `build` calls after the roster) still refuses the
    same manifest."""
    root, fork_point, _advance = repo
    head = _git(root, "rev-parse", "HEAD")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "round.json").write_text("{not json")
    base, state, detail = attest_mod.base_binding(
        root, broken, fork_point, head)
    assert (base, state) == (fork_point, attest_mod.BASE_ROUND_UNREADABLE)
    assert "round binding refuses this document" in detail
    # The refusal itself is undisturbed, and it is the one `build` makes.
    with pytest.raises(attest_mod.AttestError):
        attest_mod.round_binding(broken, head)


def test_the_roster_refusal_still_fires_first_on_an_unreadable_round(
        repo, tmp_path, monkeypatch, capsys):
    """The ordering, end to end through the CLI: an unreadable round.json AND
    a roster naming an output that is not there. `build` orders the roster
    first on purpose, and the base reading must not jump the queue."""
    root, _fork_point, _advance = repo
    monkeypatch.chdir(root)
    round_dir, reviewers = _mint(root)
    (round_dir / "round.json").write_text("{not json")
    (reviewers / f"{ROLE}.json").unlink()

    findings = tmp_path / "payload.json"
    findings.write_text(json.dumps(_payload()))
    assert cli_mod.main(["attest", "write", "--findings", str(findings),
                         "--base", "main", "--review-dir", str(reviewers)]) == 2
    err = capsys.readouterr().err
    assert f"{ROLE}.json" in err and "no such file" in err
    # AND NOTHING PRECEDES IT (round 2). The refusal ordering held, but the
    # base reading printed its round.json complaint above it, which is the
    # same reading order by another channel — a human reads the top line.
    # Held back until `build` returns, it is not printed at all on a run
    # `build` refuses.
    assert "round manifest" not in err
    # The only `attest write` line on this run is the refusal itself.
    assert [ln for ln in err.splitlines()
            if ln.startswith("warden attest write:")] == []


# --- the ancestry reading ------------------------------------------------------


def test_a_non_ancestor_base_is_a_reading_and_never_a_refusal(
        repo, tmp_path, monkeypatch, capsys):
    """Measured on this repository's own corpus before it was decided: 88 of
    353 committed shards record a base that is not an ancestor of the head
    they stamp. Refusing would refuse a quarter of the platform's own
    attestations, so the ancestry is recorded and the write proceeds."""
    root, _fork_point, advance = repo
    monkeypatch.chdir(root)
    moved = advance()
    # Minted AGAINST the moved tip, which is the ordinary shape: the branch
    # forked earlier and `main` is already ahead of it.
    _round_dir, reviewers = _mint(root)
    head = _git(root, "rev-parse", "HEAD")
    assert subprocess.run(["git", "merge-base", "--is-ancestor", moved, head],
                          cwd=root).returncode == 1

    assert _write(root, tmp_path, "main", reviewers) == 0
    manifest = json.loads((_latest_run(root) / "manifest.json").read_text())
    assert manifest["base_binding"] == attest_mod.BASE_FROM_ROUND
    assert manifest["base_ancestry"] == "not-ancestor"
    assert "is NOT an ancestor of" in capsys.readouterr().err


def test_the_not_ancestor_reading_reaches_the_attestation_not_only_stderr(
        repo, tmp_path, monkeypatch, capsys):
    """THE SECOND DEFECT, one seam over from this file's first.

    `base_binding` closed "attest re-resolved the name". This is the other
    one: the ROUND ITSELF was minted against an `origin/main` that had already
    moved past the branch, so the base the shard faithfully records is not on
    the branch at all. Warden computed that reading and printed it — and the
    run manifest it also rode is under `.warden/out/`, which is gitignored, so
    the COMMITTED shard carried the misleading base with no committed record
    of the caveat. Round 1 of one branch would record a real ancestor and
    round 2 a moved tip, two shards disagreeing about the base with nothing in
    either saying which one a reader may recompute figures from.

    So the reading is stamped into the document. The write still proceeds —
    refusing was measured and rejected, and this asserts that too.
    """
    root, _fork_point, advance = repo
    monkeypatch.chdir(root)
    moved = advance()
    _round_dir, reviewers = _mint(root)
    head = _git(root, "rev-parse", "HEAD")
    # The fixture still reproduces.
    assert subprocess.run(["git", "merge-base", "--is-ancestor", moved, head],
                          cwd=root).returncode == 1

    assert _write(root, tmp_path, "main", reviewers) == 0
    doc = json.loads((_latest_run(root) / "attestation.json").read_text())
    assert doc["base_sha"] == moved
    assert doc["base_ancestry"] == "not-ancestor", (
        "the shard records a base that is not on the branch and says nothing "
        "about it, so a reader recomputing base..head reads another merged "
        "change's work as this one's")
    assert "NOT ON THE BRANCH" in attest_mod.render_summary(doc)
    capsys.readouterr()


def test_an_ancestor_base_is_recorded_too_so_absent_is_not_a_pass(
        repo, tmp_path, monkeypatch, capsys):
    """The clean reading rides as well. Stamping only the bad one would make
    absence ambiguous between "on the branch" and "written before the field",
    which is the confusion every other stamped state here refuses."""
    root, fork_point, _advance = repo
    monkeypatch.chdir(root)
    _round_dir, reviewers = _mint(root)

    assert _write(root, tmp_path, "main", reviewers) == 0
    doc = json.loads((_latest_run(root) / "attestation.json").read_text())
    assert doc["base_sha"] == fork_point
    assert doc["base_ancestry"] == "ancestor"
    capsys.readouterr()


def test_the_manifests_reading_is_read_off_the_document(
        repo, tmp_path, monkeypatch, capsys):
    """One computation. The manifest is what an auditor reads for a run that
    produced no shard, so it keeps the field — but it is taken from the
    document rather than recomputed, and the two can never disagree about one
    round."""
    root, _fork_point, advance = repo
    monkeypatch.chdir(root)
    advance()
    _round_dir, reviewers = _mint(root)

    assert _write(root, tmp_path, "main", reviewers) == 0
    run = _latest_run(root)
    doc = json.loads((run / "attestation.json").read_text())
    manifest = json.loads((run / "manifest.json").read_text())
    assert manifest["base_ancestry"] == doc["base_ancestry"] == "not-ancestor"
    capsys.readouterr()


def test_the_ancestry_states_are_three_and_unavailable_is_one_of_them(
        repo):
    """`unavailable` is its own answer for the reason every other "could not
    look" in this module is: it must not read as "checked and fine"."""
    root, fork_point, _advance = repo
    head = _git(root, "rev-parse", "HEAD")
    assert attest_mod.base_ancestry(root, fork_point, head) == "ancestor"
    assert attest_mod.base_ancestry(root, head, fork_point) == "not-ancestor"
    assert attest_mod.base_ancestry(root, "d" * 40, head) == "unavailable"


def test_the_file_list_and_the_patch_ids_come_from_the_rounds_base(
        repo, tmp_path, monkeypatch, capsys):
    """ROUND 1, F-1. The other half of one range resolved once was pinned by
    nothing: reverting `range_paths` and `range_patch_ids` to the write-time
    name left the whole suite green, because the earlier fixtures' two bases
    span the same commits.

    THE SHAPE THAT BITES: the branch has MERGED the advanced `main`, so the
    two bases disagree about what is in range. `sibling.py` is in the round's
    `fork_point...head` and out of `main...head`, and the patch-id sets differ
    by exactly the merged commit.
    """
    root, fork_point, advance = repo
    monkeypatch.chdir(root)
    advance()
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", "main", "-m", "merge main")
    # Minted against the fork point, which is the range the reviewers read.
    _round_dir, reviewers = _mint(root, base=fork_point)
    moved = _git(root, "rev-parse", "main")
    head = _git(root, "rev-parse", "HEAD")

    # The fixture reproduces only while the two bases disagree about the range.
    from warden import attest as a
    from warden import diffs as d
    assert "sibling.py" in d.range_paths(root, fork_point, head)
    assert "sibling.py" not in d.range_paths(root, moved, head)
    assert (tuple(p for p, _ in a.range_patch_ids(root, fork_point, head))
            != tuple(p for p, _ in a.range_patch_ids(root, moved, head)))

    # THE FILE LIST, pinned separately (round 2). It never reaches the shard —
    # it feeds `build`'s reviewer check — so the only way to bite it is to
    # make a reviewer cite a file that is in the round's range and out of the
    # flag's. `sibling.py` is exactly that file, and a reviewer that cites it
    # is reviewed-clean under the round's base and REFUSED under the flag's.
    (reviewers / f"{ROLE}.json").write_text(json.dumps(
        {"role": ROLE, "round": 1, "reviewed": ["sibling.py"]}))
    findings = tmp_path / "payload.json"
    payload = _payload()
    payload["reviewers"][0]["outcome"] = "reviewed-clean"
    findings.write_text(json.dumps(payload))
    assert cli_mod.main(["attest", "write", "--findings", str(findings),
                         "--base", "main", "--review-dir", str(reviewers)]) == 0

    doc = json.loads((_latest_run(root) / "attestation.json").read_text())
    assert doc["base_sha"] == fork_point
    # Recorded from the ROUND's base, which is the range its reviewers read.
    assert tuple(doc["range_patch_ids"]) == tuple(
        p for p, _ in a.range_patch_ids(root, fork_point, head))
    capsys.readouterr()
