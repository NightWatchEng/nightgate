"""Evidence run dirs: uniqueness and manifest shape."""

import json

import pytest

from warden import runs


def test_run_dirs_never_collide_regression(tmp_path):
    """Back-to-back runs must never share/clobber an evidence dir."""
    dirs = {runs.create_run_dir(tmp_path, "explain").name for _ in range(5)}
    assert len(dirs) == 5


def test_manifest_shape(tmp_path, sample_repo):
    run_dir = runs.create_run_dir(tmp_path, "verify")
    (run_dir / "verify-result.json").write_text("{}")
    runs.write_manifest(run_dir, root=sample_repo, cmd="verify",
                        rules_version="abc123def456", exit_status="pass",
                        extra={"note": "x"})
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["cmd"] == "verify"
    assert manifest["rules_version"] == "abc123def456"
    assert manifest["exit_status"] == "pass"
    assert manifest["files"] == ["verify-result.json"]
    assert manifest["note"] == "x"
    assert len(manifest["git_sha"]) == 40


def test_git_sha_unknown_outside_repo(tmp_path):
    run_dir = runs.create_run_dir(tmp_path, "explain")
    runs.write_manifest(run_dir, root=tmp_path, cmd="explain",
                        rules_version="x", exit_status="ok")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["git_sha"] == "unknown"


def test_head_sha_ignores_an_inherited_git_dir(tmp_path, sample_repo, monkeypatch):
    """git hooks export GIT_DIR, and a git subprocess honors it over cwd — so
    an inherited GIT_DIR would stamp the ambient repo's HEAD onto a run for a
    different root."""
    monkeypatch.setenv("GIT_DIR", str(sample_repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(sample_repo))
    assert runs.head_sha(tmp_path) == runs.UNKNOWN_SHA


def test_is_dirty_ignores_an_inherited_git_dir(tmp_path, sample_repo, monkeypatch):
    """Same root cause: dirtiness must describe the root being judged, never
    whatever repo happened to invoke warden."""
    monkeypatch.setenv("GIT_DIR", str(sample_repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(sample_repo))
    assert runs.is_dirty(tmp_path) is None


@pytest.mark.parametrize("bad", ["2", 2.0, 0, -1, True],
                         ids=["string", "float", "zero", "negative", "bool"])
def test_a_round_manifest_refuses_a_round_number_warden_cannot_read(tmp_path,
                                                                    bad):
    """The WRITE side of the round number, driven with every shape that is
    not a round.

    `attest write --review-dir` checks a roster's `round` labels against this
    field, so a manifest stating `"2"`, `2.0`, `0`, `-1` or `True` would have
    every later reader guess which round the directory is — and the crew a
    roster is measured against is chosen BY that number. `True` is the sharp
    one: `isinstance(True, int)` is True, so a plain int check passes it and
    the manifest states round `true`.
    """
    d = runs.create_round_dir(tmp_path, branch="feature", head="a" * 40)
    with pytest.raises(runs.RoundError, match="numbered from 1"):
        runs.write_round_manifest(d, base="main", base_sha="b" * 40,
                                  head="a" * 40, branch="feature",
                                  round_no=bad)
    assert not (d / "round.json").exists(), (
        "a refused round number still wrote a manifest")
