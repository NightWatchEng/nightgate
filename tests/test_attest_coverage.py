"""`attest check` refuses a commit the review never covered.

`check_range` used to ask MEMBERSHIP alone: does SOME commit in `base..head`
carry an attestation shard. Everything landing after the last attested commit
therefore shipped unreviewed behind a green required gate. Measured against the
real check, and reproduced verbatim by
`test_a_backdoor_after_a_clean_round_used_to_print_pass`: a clean round-2
shard, then a commit adding `backdoor()` that nobody reviewed, reported as
``attest check: PASS — 1 of 3 commit(s)``.

The rule these tests pin: a commit in the range that NO tip attestation is an
ancestor-or-self of must touch nothing outside `attest.EVIDENCE_CARVE_OUT`.
The carve-out is one prefix, `.warden/memory/attest/`, and it is the whole
safety margin — `test_the_carve_out_is_the_shard_directory_alone` makes
widening it a deliberate edit with a red test attached.
"""

import json
import subprocess

import pytest

from test_cli import _clone_fixture
from warden import attest as attest_mod
from warden import cli


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _write(root, rel, body):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _commit(root, message, *paths):
    _git(root, "add", *(paths or ("-A",)))
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _shard(root, sha, *, verdict="clean", name=None):
    """A committed attestation shard naming `sha`, in the store's own shape."""
    shard_dir = root / attest_mod.SHARD_DIR
    shard_dir.mkdir(parents=True, exist_ok=True)
    path = shard_dir / (name or f"{sha[:8]}.json")
    path.write_text(json.dumps(
        {"schema": 1, "source": "attest", "sha": sha, "base_sha": "0" * 40,
         "rules_version": "v1", "reviewed_at": "2026-09-21T00:00:00+00:00",
         "verdict": verdict, "records": []}))
    return path


def _commit_the_shard(root, message="ops(memory): commit the shard"):
    """The ingest commit, and the ONLY commit the carve-out admits after a
    round: the shard alone, which is what `memory ingest` + `git add` leaves.
    """
    return _commit(root, message, attest_mod.SHARD_DIR.as_posix())


@pytest.fixture
def branch(sample_repo, tmp_path, monkeypatch):
    """A feature branch with one reviewed commit and its CLEAN shard committed.

    Returns `(root, reviewed)`. `attest check --base main` passes here, and
    every test below adds exactly one more commit to say what happens next.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.chdir(root)
    _git(root, "switch", "-q", "-c", "feature")
    _write(root, "app/feature.py", "def feature():\n    return 1\n")
    reviewed = _commit(root, "the reviewed change")
    _shard(root, reviewed)
    _commit_the_shard(root)
    return root, reviewed


# --- the acceptance ------------------------------------------------------------


def test_the_shard_commit_alone_still_passes(branch, capsys):
    """The golden path must survive the rule that refuses everything else.

    The ingest commit ALWAYS lands after the round it records — `attest write`
    stamps the head, `memory ingest` writes the shard from it, and committing
    that shard moves head — so a rule with no carve-out would refuse every
    well-formed PR this platform has ever merged.
    """
    root, _reviewed = branch
    assert cli.main(["attest", "check", "--base", "main"]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "UNREVIEWED COMMITS" not in out


def test_a_backdoor_after_a_clean_round_used_to_print_pass(branch, capsys):
    """THE REPRODUCTION. A clean round, then a commit nobody reviewed.

    Under membership this exited 0 with ``PASS — 1 of 3 commit(s) in
    main..<head> carry a committed attestation``, which is a true sentence
    about a range shipping a backdoor. The refusal names the commit, the file,
    and the remedy.
    """
    root, _reviewed = branch
    _write(root, "app/feature.py",
           "def feature():\n    return 1\n\n\ndef backdoor():\n    return 42\n")
    sneak = _commit(root, "nobody reviewed this")

    assert cli.main(["attest", "check", "--base", "main"]) == 1, (
        "a commit landing after the last attested one shipped at exit 0")
    out = capsys.readouterr()
    assert "UNREVIEWED COMMITS" in out.out
    assert "PASS" not in out.out, (
        "the gate printed PASS beside its own refusal")
    assert sneak[:12] in out.out and "app/feature.py" in out.out
    assert "re-review this head" in out.out.lower()
    assert "Re-review this head" in out.err, (
        "the blocking line on stderr does not name the remedy")


def test_the_carve_out_does_not_excuse_a_code_change_beside_a_shard(
        branch, capsys):
    """The carve-out is judged per PATH, never per commit.

    A commit that touches the shard directory AND a source file is not
    evidence; it is a code change wearing evidence as cover. Reading the
    carve-out as "this commit looks like an ingest commit" would make
    `git add -A` after an edit the one-line bypass for the whole rule.
    """
    root, _reviewed = branch
    _write(root, "app/feature.py", "def feature():\n    return 666\n")
    _shard(root, _git(root, "rev-parse", "HEAD"), name="rides-along.json")
    riding = _commit(root, "ops(memory): commit the shard")   # and a source file

    assert cli.main(["attest", "check", "--base", "main"]) == 1, (
        "a source change riding in the same commit as a shard was excused")
    out = capsys.readouterr().out
    assert riding[:12] in out and "app/feature.py" in out
    # What is NAMED is the path outside the carve-out, not the shard beside it:
    # the reader is told what has to be reviewed.
    gap_line = next(ln for ln in out.splitlines()
                    if ln.startswith(f"  {riding[:12]} -> "))
    assert "app/feature.py" in gap_line and "rides-along.json" not in gap_line


def test_a_decision_shard_after_the_round_is_not_carved_out(branch, capsys):
    """WHY THE CARVE-OUT IS `.warden/memory/attest/` AND NOT `.warden/memory/`.

    Measured over the 39 merged PRs in `pr/230..pr/269` that still carry refs:
    the wide prefix flags 14, the narrow one 16. The two PRs the wide prefix
    lets through — #243 and #266 — both ride free on a decision shard
    committed after the last round, which is this shape.

    A decision shard records what was RULED. It is the most consequential
    prose this repo commits, and admitting it would make the one artifact
    carrying a founder-level ruling the one artifact no reviewer must have
    read. `warden/autonomy.py` draws the same line for the cage's carve-out:
    attest in, decide "out by decision, not omission".
    """
    root, _reviewed = branch
    _write(root, ".warden/memory/decide/20260921T000000Z-aaaa-bbbb.json",
           json.dumps({"schema": 1, "source": "decide", "decision": "ship it"}))
    ruled = _commit(root, "ops(memory): record the decision")

    assert cli.main(["attest", "check", "--base", "main"]) == 1, (
        "a decision shard committed after the round rode in unreviewed — the "
        "carve-out widened from .warden/memory/attest/ to .warden/memory/")
    out = capsys.readouterr().out
    assert ruled[:12] in out and ".warden/memory/decide/" in out


def test_a_merge_of_the_base_branch_after_the_round_is_reported(
        branch, capsys):
    """Merging the base branch is how most of the refused history smuggles.

    11 of the 16 measured PRs are this shape, with 18 to 94 files arriving
    behind a round that read none of them. It is also the case a naive
    implementation misses: `git show --name-only` on a merge prints the
    COMBINED diff, which lists nothing at all for a clean merge, so a check
    reading that would call the merge an empty change and pass.
    """
    root, _reviewed = branch
    _git(root, "switch", "-q", "main")
    _write(root, "app/from_main.py", "m = 1\n")
    _commit(root, "main moves")
    _git(root, "switch", "-q", "feature")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", "-m", "merge main", "main")
    merge = _git(root, "rev-parse", "HEAD")

    assert cli.main(["attest", "check", "--base", "main"]) == 1, (
        "a merge of the base branch after the round passed — its first-parent "
        "diff is content this head's review never read")
    out = capsys.readouterr().out
    assert merge[:12] in out and "app/from_main.py" in out, (
        "the merge was read through the COMBINED diff, which is empty here")


def test_re_reviewing_the_merged_head_collapses_the_tips(branch, capsys):
    """The named remedy actually works, on the hardest shape it is offered for.

    A merge of a sibling line leaves two tip attestations and a merge commit
    under neither one's ancestry. Attesting the merged head produces a commit
    that descends from both, so one tip covers the whole range. Without this
    the refusal would be a dead end, which is worse than the hole.
    """
    root, _reviewed = branch
    _git(root, "switch", "-q", "-c", "sibling", "main")
    _write(root, "app/sibling.py", "s = 1\n")
    sibling = _commit(root, "the sibling change")
    _shard(root, sibling, name="sibling.json")
    _commit_the_shard(root, "ops(memory): sibling round")
    _git(root, "switch", "-q", "feature")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", "-m", "merge sibling", "sibling")
    assert cli.main(["attest", "check", "--base", "main"]) == 1, (
        "fixture wrong: the merged range should be refused before the remedy")
    capsys.readouterr()

    # THE REMEDY, exactly as the check prints it.
    _shard(root, _git(root, "rev-parse", "HEAD"), name="merged-head.json")
    _commit_the_shard(root, "ops(memory): re-review the merged head")

    assert cli.main(["attest", "check", "--base", "main"]) == 0, (
        "re-reviewing the merged head did not collapse the tips — the remedy "
        "the refusal names does not resolve it")
    assert "PASS" in capsys.readouterr().out


# --- the rule's own shape ------------------------------------------------------


def test_the_carve_out_is_the_shard_directory_alone():
    """The allowlist is pinned, because it is the entire safety margin.

    Widening it is a one-line edit with a large blast radius and no other
    alarm: `.warden/memory/` instead of `.warden/memory/attest/` measured 14
    refusals instead of 16 over the same 39 PRs, which reads like a smaller
    disruption and is in fact two PRs of unreviewed content. Any change here
    turns this test red and has to be argued for in the diff.
    """
    assert attest_mod.EVIDENCE_CARVE_OUT == (".warden/memory/attest/",)
    assert attest_mod.in_evidence_carve_out(
        ".warden/memory/attest/20260921T000000Z-a-b.json")
    assert not attest_mod.in_evidence_carve_out(
        ".warden/memory/decide/20260921T000000Z-a-b.json")
    # The trailing slash is a directory boundary, not a string prefix: a
    # sibling whose name merely starts with the store's is not the store.
    assert not attest_mod.in_evidence_carve_out(
        ".warden/memory/attestations-notes.md")


def test_coverage_is_ancestry_not_list_order(branch):
    """An attestation covers the commit it names AND that commit's ancestors.

    Not "the commits before it in `rev-list` order", which linearises a
    partial order and would credit a sibling line's work to this one. Two
    commits reviewed together, one shard: both are covered, and the check
    reports no gap.
    """
    root, _reviewed = branch
    _write(root, "app/second.py", "s = 1\n")
    _commit(root, "a second change")
    _write(root, "app/third.py", "t = 1\n")
    third = _commit(root, "a third change")
    _shard(root, third, name="round-2.json")
    head = _commit_the_shard(root)

    doc = attest_mod.check_range(root, base="main", head=head)
    assert doc["coverage_gap"] == [], (
        "commits the attested head descends from were reported as uncovered")
    # The shard commit IS uncovered — reported, and excused by the carve-out.
    assert [row["sha"] for row in doc["uncovered"]] == [head]
    assert doc["uncovered"][0]["outside"] == []


def test_an_empty_commit_after_the_round_ships_nothing(branch, capsys):
    """A commit with an empty first-parent diff is uncovered and harmless.

    Stated rather than special-cased: the rule asks what a commit SHIPS, and a
    commit that ships nothing ships no unreviewed content. Refusing it would
    be theatre with a remedy that changes nothing.
    """
    root, _reviewed = branch
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "an empty commit")

    assert cli.main(["attest", "check", "--base", "main"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_an_unreadable_commit_does_not_read_as_an_empty_change(branch,
                                                                    monkeypatch):
    """git failing to list a commit's paths is exit 2, never a silent pass.

    "Could not look" is not "changed nothing". Collapsing the two would make
    every gate outage an affirmative all-clear, which is the exact inversion
    the exit-code contract exists to prevent (2 = did not run, 1 = ran and
    refused).
    """
    root, _reviewed = branch
    _write(root, "app/feature.py", "x = 2\n")
    _commit(root, "nobody reviewed this")
    from warden import diffs as diffs_mod
    real = diffs_mod.run_git

    def boom(root_, *args, **kw):
        if args[:1] == ("show",) and "--name-only" in args:
            raise diffs_mod.DiffError("git exploded")
        return real(root_, *args, **kw)

    monkeypatch.setattr(diffs_mod, "run_git", boom)
    with pytest.raises(attest_mod.AttestError) as e:
        attest_mod.check_range(root, base="main",
                               head=_git(root, "rev-parse", "HEAD"))
    assert "did NOT run" in str(e.value)


# --- gate parity: every in-process consumer asks the same three questions ------


def test_ship_refuses_the_range_attest_check_refuses(branch, capsys):
    """`warden ship`'s parity step must ask the coverage question too.

    ROUND 1, F1. The rule shipped wired only into `warden attest check`'s exit
    code, and `ship.check_attest_clean` — whose docstring says it applies
    "`warden attest check`'s rule, in process", and which `deliver` and
    `orchestrate` run as the pre-PR gate-parity step — read `attested` and
    `open_verdict` and stopped. So a builder was told `attest-clean: PASS`
    locally on exactly the range CI's required step exits 1 on, which is worse
    than no parity check: a green light certifying the red one was not asked.
    """
    from warden import config as config_mod
    from warden import ship as ship_mod
    root, _reviewed = branch
    _write(root, "app/feature.py", "def feature():\n    return 1\n\n\ndef b():\n    return 42\n")
    sneak = _commit(root, "nobody reviewed this")

    cli_code = cli.main(["attest", "check", "--base", "main"])
    capsys.readouterr()
    check = ship_mod.check_attest_clean(config_mod.load(root), base="main",
                                        head=_git(root, "rev-parse", "HEAD"))
    assert cli_code == 1, "fixture wrong: attest check should refuse here"
    assert not check.ok, (
        "ship reported attest-clean PASS on a range `warden attest check` "
        "exits 1 on — the two are supposed to be one rule")
    assert sneak[:12] in check.found and "app/feature.py" in check.found, (
        f"ship refused without naming the commit or the path: {check.found}")


def test_ship_still_passes_the_range_attest_check_passes(branch):
    """The other half of parity, and the one that would hurt more if it broke:
    the golden path must not start failing locally."""
    from warden import config as config_mod
    from warden import ship as ship_mod
    root, _reviewed = branch
    check = ship_mod.check_attest_clean(config_mod.load(root), base="main",
                                        head=_git(root, "rev-parse", "HEAD"))
    assert check.ok, check.found


def test_the_pass_names_the_commits_the_carve_out_excused(branch, capsys):
    """ROUND 2, R2-1. A pass that excuses a commit must say which.

    The carve-out is the rule's entire safety margin, and the sha join — the
    default one — printed nothing about what it let through: its PASS line
    counts commits that CARRY an attestation, a different quantity. The
    carve-out was invisible exactly where a reader would audit it, and
    `CLI-Reference.md`'s claim that "the pass line counts them" was false for
    that join.
    """
    root, _reviewed = branch
    head = _git(root, "rev-parse", "HEAD")     # the shard commit, excused
    assert cli.main(["attest", "check", "--base", "main"]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "EXCUSED" in out and head[:12] in out, (
        f"the pass excused a commit without naming it: {out}")
    assert ".warden/memory/attest/" in out, (
        "the pass does not say WHY the commit was excused")
