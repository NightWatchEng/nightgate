"""A shard readable at head that no counted commit added is reported.

`repair._added_shards` walks `rev-list --first-parent`, and it has to: the
full walk was what shipped before, and it was worse — an ordinary `git merge
sibling-branch` whose tip carried one shard numbered this change's FIRST
review as round 2, where round 2's declared crew is one reviewer and round
1's is two. The first-parent walk costs the other direction instead: a shard
whose ADD the walk never reaches raises no floor, so a builder who puts the
evidence commits out of its path gets the lower count and a fresh repair
budget. There are two such places and round 1 found the second: a side branch
the feature merged in, and the merge commit's own tree, where `diff-tree`
reports no diff at all.

Neither the walk nor the keying changes here. The SILENCE does, and the
reading is a TREE comparison for that reason — `_added_shards` refuses one
outright because it would lower a floor, while this lowers nothing and needs
exactly that blindness: shards the base branch already carries are the ones
that must not be named.

The other half of the acceptance is the quiet case: merging `origin/main`
mid-branch brings other changes' shards in by the hundred and must not fire.
It does not, and the base branch's own tree is what subtracts them.

WHAT THE REPORT DOES NOT SAY, and round 1 is why: WHY a shard is here. A head
that is a merge ref has its own honest commits off the walked line, and they
read exactly like arranged ones. The note describes; it does not accuse.
"""

import json
import subprocess
from pathlib import Path

import pytest

from warden import repair as repair_mod


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _shard(repo: Path, name: str, rnd: int) -> str:
    path = f"{repair_mod._SHARD_DIR}/{name}"
    (repo / repair_mod._SHARD_DIR).mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(json.dumps(
        {"schema": 1, "source": "attest", "sha": _git(repo, "rev-parse", "HEAD"),
         "verdict": "clean",
         "reviewers": [{"role": "code-reviewer", "returned": True,
                        "round": rnd}]}))
    return path


@pytest.fixture
def repo(tmp_path):
    """A feature branch off `main`, one commit of real work on it."""
    r = tmp_path / "r"
    r.mkdir()
    _git(r.parent, "init", "-q", "-b", "main", "r")
    (r / "a.txt").write_text("one\n")
    _commit(r, "base")
    base = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "-b", "feature")
    (r / "a.txt").write_text("two\n")
    _commit(r, "the change")
    return r, base


# --- the sibling-merge shape the bead names -------------------------------------


def test_a_shard_added_on_a_side_branch_and_merged_is_reported(repo):
    """THE EXPLOIT SHAPE. Round 1's shard is committed on a side branch and
    merged in, so the first-parent walk never sees the ADD and the floor
    stays 0 — the branch gets a fresh repair budget. The count is unchanged
    on purpose; what is new is that the shard is named."""
    r, base = repo
    _git(r, "checkout", "-q", "-b", "side")
    path = _shard(r, "round-1.json", 1)
    _commit(r, "ops(memory): round 1, off to one side")
    _git(r, "checkout", "-q", "feature")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", "side", "-m", "merge side")
    head = _git(r, "rev-parse", "HEAD")

    count = repair_mod.round_count(r, base, head)
    # The count is keyed on the ADD and the walk is first-parent: both
    # deliberate, both unchanged. The floor is still 0.
    assert count.from_shards == 0
    assert count.number == 1
    # And the shard is no longer invisible.
    assert list(count.uncounted) == [path]
    note = "\n".join(count.notes)
    assert path in note and "raise no floor" in note


def test_the_shard_the_report_names_is_readable_at_head(repo):
    """The report's whole claim is that a reader can go and open the file.
    Asserted against the tree rather than trusted."""
    r, base = repo
    _git(r, "checkout", "-q", "-b", "side")
    path = _shard(r, "round-1.json", 1)
    _commit(r, "ops(memory): round 1, off to one side")
    _git(r, "checkout", "-q", "feature")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", "side", "-m", "merge side")
    head = _git(r, "rev-parse", "HEAD")

    count = repair_mod.round_count(r, base, head)
    reported = count.uncounted[0]
    assert json.loads(_git(r, "show", f"{head}:{reported}"))["reviewers"]
    assert reported == path


def test_a_side_branch_shard_deleted_before_head_is_not_reported(repo):
    """Keyed on PRESENCE AT HEAD, never on the ADD alone. A side branch that
    added a shard and dropped it again ships no evidence, and naming it would
    send a reader to a file that is not there."""
    r, base = repo
    _git(r, "checkout", "-q", "-b", "side")
    path = _shard(r, "round-1.json", 1)
    _commit(r, "ops(memory): round 1, off to one side")
    _git(r, "checkout", "-q", "feature")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", "side", "-m", "merge side")
    (r / path).unlink()
    head = _commit(r, "drop it again")

    count = repair_mod.round_count(r, base, head)
    assert count.uncounted == ()
    assert all("READABLE AT HEAD" not in n for n in count.notes)


def test_a_shard_on_the_first_parent_line_is_counted_not_reported(repo):
    """The ordinary shape stays ordinary: an ADD on the walked line raises
    the floor and says nothing about side branches."""
    r, base = repo
    _shard(r, "round-1.json", 1)
    head = _commit(r, "ops(memory): round 1")

    count = repair_mod.round_count(r, base, head)
    assert count.from_shards == 1
    assert count.uncounted == ()


# --- the quiet case the acceptance requires --------------------------------------


def test_merging_the_base_branch_mid_branch_does_not_fire(repo):
    """THE SECOND HALF OF THE ACCEPTANCE. `main` advances carrying other
    changes' shards, the branch merges it to stay current, and the report
    stays quiet — because those commits are ancestors of the base every
    warden caller resolves, so they are not in `base..head` at all."""
    r, _base = repo
    _git(r, "checkout", "-q", "main")
    for n in (1, 2, 3):
        _shard(r, f"someone-else-{n}.json", n)
        _commit(r, f"ops(memory): another change's round {n}")
    moved = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "feature")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", "main", "-m", "merge origin/main")
    head = _git(r, "rev-parse", "HEAD")

    # The base is the ref as it stands, which is how `warden round count` and
    # `warden round new` both resolve it.
    count = repair_mod.round_count(r, moved, head)
    assert count.uncounted == ()
    assert count.from_shards == 0
    # The three shards ARE in the tree — the report is quiet because they are
    # not this change's range, not because nothing looked.
    assert len(_git(r, "ls-tree", "-r", "--name-only", head, "--",
                    repair_mod._SHARD_DIR).splitlines()) == 3


# --- the CLI surface -------------------------------------------------------------


def test_round_count_prints_the_report_and_carries_it_in_the_json(
        repo, monkeypatch, capsys):
    """`warden round count` prints every reading behind the number and
    carries each one in its JSON report. A reading that reaches neither is
    one CI cannot act on."""
    from conftest import REPO_YAML
    import yaml
    from warden import cli as cli_mod

    r, _base = repo
    policy = yaml.safe_load(REPO_YAML)
    policy["repair"] = {"budget": 2}
    (r / "repo.yaml").write_text(yaml.safe_dump(policy))
    (r / ".gitignore").write_text(".warden/out/\n")
    rules = r / ".warden" / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "secrets-in-diff.md").write_text("---\nid: secrets-in-diff\n---\n")
    _commit(r, "policy")

    _git(r, "checkout", "-q", "-b", "side")
    path = _shard(r, "round-1.json", 1)
    _commit(r, "ops(memory): round 1, off to one side")
    _git(r, "checkout", "-q", "feature")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", "side", "-m", "merge side")

    monkeypatch.chdir(r)
    assert cli_mod.main(["round", "count", "--base", "main"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["uncounted"] == [path]
    assert path in out.err and "READABLE AT HEAD" in out.err


# --- the shapes round 1 found -----------------------------------------------------


def test_a_shard_written_into_a_merge_commit_is_reported(repo):
    """ROUND 1, F-2. The cheaper dodge, and it used to be completely silent:
    `git merge --no-ff --no-commit`, write the shard into the index, commit.
    The ADD belongs to a merge commit, `diff-tree` reports no diff for one,
    and the commit is ON the first-parent line, so neither the old walk nor
    the old off-line walk could see it."""
    r, base = repo
    _git(r, "checkout", "-q", "-b", "side")
    (r / "side.txt").write_text("side\n")
    _commit(r, "side work")
    _git(r, "checkout", "-q", "feature")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", "--no-commit", "side")
    path = _shard(r, "round-1.json", 1)
    head = _commit(r, "merge side, and an evil shard with it")

    count = repair_mod.round_count(r, base, head)
    # The ADD is invisible to a walk keyed on diff-tree, so the floor is 0 —
    # which is exactly why the report has to be a tree comparison.
    assert count.from_shards == 0
    assert list(count.uncounted) == [path]
    assert json.loads(_git(r, "show", f"{head}:{path}"))["reviewers"]


def test_the_note_describes_the_shape_and_does_not_accuse(repo):
    """ROUND 1, F-3. A head that is a MERGE REF has the base tip as its first
    parent, so the branch's own honest commits are all off the walked line and
    every shard it committed is reported. The note used to say that arranging
    evidence onto a side branch is how this is bought; on that head it was an
    accusation against a branch that did nothing of the kind."""
    r, base = repo
    path = _shard(r, "round-1.json", 1)
    branch_head = _commit(r, "ops(memory): round 1, on the line")
    # The merge ref `actions/checkout` leaves behind: first parent is the base.
    _git(r, "checkout", "-q", "--detach", base)
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", branch_head, "-m", "Merge pull request")
    merge_ref = _git(r, "rev-parse", "HEAD")
    assert _git(r, "rev-parse", f"{merge_ref}^1") == base

    count = repair_mod.round_count(r, base, merge_ref)
    assert list(count.uncounted) == [path]
    note = "\n".join(count.notes)
    assert "merge ref whose" in note
    # All three ways in are named, and the honest one is named beside them
    # (round 2: the note used to list two where the docstring named three).
    assert "three ways in" in note
    for way in ("on a side branch", "ADD made by a merge commit itself",
                "re-added at a path the base branch also carries"):
        assert way in note, way
    # The accusing sentence is gone.
    assert "is how that is bought deliberately" not in note


def test_a_shard_re_added_at_a_base_branch_path_is_still_reported(repo):
    """ROUND 2, F-2. Subtracting the base tree BY PATH made every name the
    base branch carries a place to hide: drop one of its shard paths on a side
    branch, re-add it saying whatever you like, merge, and both trees hold the
    path. The comparison is (path, blob) for that reason."""
    r, _base = repo
    _git(r, "checkout", "-q", "main")
    path = _shard(r, "already-on-main.json", 1)
    _commit(r, "ops(memory): another change's round 1")
    moved = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "feature")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", "main", "-m", "merge origin/main")
    # Inherited byte-identical: quiet, which is the case that must stay quiet.
    assert repair_mod.round_count(
        r, moved, _git(r, "rev-parse", "HEAD")).uncounted == ()

    _git(r, "checkout", "-q", "-b", "side")
    (r / path).unlink()
    _commit(r, "drop it")
    _shard(r, "already-on-main.json", 7)
    _commit(r, "and put something else at the same name")
    _git(r, "checkout", "-q", "feature")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", "side", "-m", "merge side")
    head = _git(r, "rev-parse", "HEAD")

    count = repair_mod.round_count(r, moved, head)
    assert count.from_shards == 0
    assert list(count.uncounted) == [path]
    # And the note says BYTES, not the path: this shard's path IS one the base
    # branch carries, so the older wording was false of the very shape the
    # same paragraph goes on to name.
    note = "\n".join(count.notes)
    assert "whose bytes the base branch does not carry" in note
    # The bytes at head are the branch's, not the ones it inherited.
    assert json.loads(_git(r, "show", f"{head}:{path}")
                      )["reviewers"][0]["round"] == 7
