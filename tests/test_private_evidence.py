"""scripts/private-evidence.py decides which CI steps the public export skips.

The export (publish.yaml) carries ci.yml but not .warden/memory/ or .beads/,
so the steps that read them skip there, naming why, and nowhere else: a tree
that has the evidence, or a PR whose base has it, still runs every step.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from private_evidence import CORPUS, ROOT, TRACKER

SCRIPT = ROOT / "scripts" / "private-evidence.py"
REASON = "no committed review corpus in this tree"


def _repo(tmp_path: Path, files: dict) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _commit(repo, "base")
    return repo


def _commit(repo: Path, message: str) -> None:
    for argv in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm",
                  message, "--allow-empty"]):
        if argv[0] == "init" and (repo / ".git").exists():
            continue
        subprocess.run(["git", "-C", str(repo), *argv], check=True,
                       capture_output=True)


def _run(repo: Path, tmp_path: Path, *args: str):
    runs = tmp_path / f"run{len(list(tmp_path.glob('run*')))}"  # fresh files per run
    runs.mkdir()
    out, summary = runs / "output", runs / "summary"
    env = {**os.environ, "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)}
    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), *args],
                            capture_output=True, text=True, env=env, check=False)
    read = lambda p: p.read_text() if p.exists() else ""  # noqa: E731
    return result, read(out), read(summary)


def _steps(job: str) -> list[dict]:
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"][job]["steps"]


def _gated(job: str, needle: str, condition: str) -> None:
    steps = _steps(job)
    ids = [s.get("id") for s in steps]
    assert "evidence" in ids, f"{job}: no step decides which evidence the tree carries"
    hits = [s for s in steps if needle in str(s.get("run", ""))]
    assert hits, f"{job}: no step runs {needle!r}"
    for step in hits:
        assert steps.index(step) > ids.index("evidence"), (job, needle)
        assert step.get("if") == condition, (
            f"{job}: the step running {needle!r} must carry `if: {condition}` — "
            "it skips only on an explicit 'absent'")


def test_gate_skips_attestation_with_a_reason_when_no_corpus_is_committed(tmp_path):
    repo = _repo(tmp_path, {"README.md": "x\n", ".warden/memory/tags.yaml": "tags: {}\n"})
    result, out, summary = _run(repo, tmp_path, "--base", "main")
    assert result.returncode == 0, result.stderr
    assert out == "corpus=absent\ntracker=absent\n"
    assert REASON in summary and "no committed tracker in this tree" in summary
    assert "skipped, not passed" in summary
    corpus = "steps.evidence.outputs.corpus != 'absent'"
    for needle in ("warden attest check", "warden attest classify", "warden round count"):
        _gated("gate", needle, corpus)
    for needle in ("warden memory ingest", "warden certify --level 4"):
        _gated("corpus", needle, corpus)
    _gated("enrollment", "warden certify --level 4", corpus)
    _gated("enrollment", "uv run --locked warden certify --level 3",
           "steps.evidence.outputs.corpus == 'absent'")
    _gated("tracker", "tracker-from-a-clone.sh", "steps.evidence.outputs.tracker != 'absent'")
    gate_evidence = next(s for s in _steps("gate") if s.get("id") == "evidence")
    assert '--base "origin/$BASE_REF"' in gate_evidence["run"]


@pytest.mark.parametrize("name, path", [
    ("corpus", ".warden/memory/attest/shard.json"), ("tracker", ".beads/config.yaml")])
def test_gate_does_not_skip_attestation_when_a_corpus_exists(tmp_path, name, path):
    repo = _repo(tmp_path, {".warden/memory/attest/shard.json": "{}\n",
                            ".beads/config.yaml": "x: 1\n"})
    result, out, summary = _run(repo, tmp_path, "--base", "main")
    assert (result.returncode, out, summary) == (0, "corpus=present\ntracker=present\n", "")
    # A change that deletes either kind does not earn the skip: refused, and
    # nothing is written for a step to read as `absent`.
    subprocess.run(["git", "-C", str(repo), "rm", "-q", path], check=True)
    _commit(repo, f"drop the {name}")
    result, out, _ = _run(repo, tmp_path, "--base", "main~1")
    assert result.returncode == 1 and out == "", result.stdout
    assert f"{name}: main~1 carries" in result.stderr, result.stderr


def test_a_rev_git_cannot_read_is_exit_2_not_an_answer(tmp_path):
    repo = _repo(tmp_path, {"README.md": "x\n"})
    result, out, _ = _run(repo, tmp_path, "--base", "origin/main")
    assert result.returncode == 2 and out == "", result.stdout
    assert "could not answer" in result.stderr


def test_the_working_repo_never_reads_as_the_export():
    """Tests skip only when BOTH are absent, so this repo, which tracks both,
    can never reach a skip by losing one: it is red here instead."""
    assert CORPUS == TRACKER, (
        f"corpus committed: {CORPUS}, tracker committed: {TRACKER} — a tree "
        "with one and not the other is neither this repo nor its export")
