"""The tracker is readable from a fresh clone.

The 2026-09-27 newcomer trace measured the failure: CLAUDE.md says `bd show`,
the Dolt database under .beads/ is gitignored, and the git remote held no
refs/dolt/*. The founder has since published it — `git ls-remote origin
'refs/dolt/*'` answers refs/dolt/data — and .beads/config.yaml names the
remote, so `bd bootstrap --yes` in a clone clones the history. These cells pin
the parts of that which the CI job, scripts/tracker-from-a-clone.sh, cannot
see about itself: the config, the one command, and the pin on bd.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from private_evidence import needs_tracker

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "tracker-from-a-clone.sh"
HEADING = "## Reading the tracker"
REMOTE = "git+ssh://git@github.com/NightWatchEng/agentops.git"


def _block(text: str, heading: str) -> str:
    """The script's awk, in Python: the first closed fence under `heading`."""
    in_section = in_fence = False
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if in_section:
                break
            in_section = line == heading
            continue
        if in_section and line.startswith("```"):
            if in_fence:
                return "\n".join(lines) + "\n"
            in_fence = True
            continue
        if in_section and in_fence:
            lines.append(line)
    raise AssertionError(f"no closed fenced block under {heading!r}")


@needs_tracker
def test_config_yaml_names_the_published_dolt_remote():
    """Measured with bd 1.1.2 on 2026-09-27: with no `sync.remote`, `bd bootstrap`
    in a clone has nothing to clone from; with the remote named, it clones
    refs/dolt/data in about six seconds. The key is read case-insensitively
    by bd, flat or nested, so it is checked the same way."""
    config = yaml.safe_load((ROOT / ".beads" / "config.yaml").read_text(encoding="utf-8")) or {}
    flat = {str(key).lower(): value for key, value in config.items()}
    nested = flat.get("sync") if isinstance(flat.get("sync"), dict) else {}
    nested = {str(key).lower(): value for key, value in nested.items()}
    remote = flat.get("sync.remote", nested.get("remote"))
    assert remote == REMOTE, (
        f".beads/config.yaml names {remote!r} as the sync remote; a clone's "
        f"`bd bootstrap --yes` clones the tracker from it, and the founder pushes "
        f"to it — expected {REMOTE!r}")


def test_contributing_gives_the_block_the_proof_runs():
    block = _block((ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"), HEADING)
    commands = [line for line in block.splitlines() if line.strip() and not line.startswith("#")]
    assert commands == ["bd bootstrap --yes"], (
        f"the block under {HEADING!r} is what a clone runs first, and CI runs it "
        f"as written: {commands}")
    assert "bd bootstrap --yes" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8"), (
        "CLAUDE.md's Beads block no longer names the clone's first command")


def test_ci_runs_the_proof_with_bd_pinned_and_a_token_remote():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    runners = {name: job for name, job in ci["jobs"].items()
               if any("scripts/tracker-from-a-clone.sh" in str(step.get("run", ""))
                      for step in job.get("steps", []))}
    assert len(runners) == 1, f"expected one job to run the proof, found {sorted(runners)}"
    (name, job), = runners.items()
    installs = [step for step in job["steps"] if "BD_SHA256" in (step.get("env") or {})]
    assert len(installs) == 1, f"job `{name}` has no step installing bd by checksum"
    env, run = installs[0]["env"], str(installs[0]["run"])
    assert re.fullmatch(r"[0-9a-f]{64}", str(env["BD_SHA256"])), env["BD_SHA256"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(env["BD_VERSION"])), env["BD_VERSION"]
    assert "sha256sum --check --strict" in run, "the checksum is stated but never checked"
    assert "v${BD_VERSION}/beads_${BD_VERSION}_" in run, "the download does not use the pin"
    proof, = [step for step in job["steps"] if "scripts/tracker-from-a-clone.sh" in str(step.get("run", ""))]
    remote = str((proof.get("env") or {}).get("TRACKER_PROOF_REMOTE", ""))
    assert remote == ("git+https://x-access-token:${{ secrets.GITHUB_TOKEN }}"
                      "@github.com/${{ github.repository }}.git"), (
        "the runner has no ssh key, so the proof reaches THIS repository through the "
        f"job token over https; the step sets TRACKER_PROOF_REMOTE to {remote!r}")
    assert job.get("permissions") == {"contents": "read"}, (
        f"the token the proof uses should read refs/dolt/data and nothing more; the job "
        f"declares permissions {job.get('permissions')!r}")


@needs_tracker
@pytest.mark.skipif(shutil.which("bd") is None, reason="no bd on PATH")
def test_the_proof_passes_with_the_override_the_ci_job_uses(tmp_path):
    """The CI job hands bd a different spelling of the remote through
    TRACKER_PROOF_REMOTE, and bd 1.1.2 persists that spelling into the clone's
    config.yaml — round 1 of this change found the job could never pass on
    that. This drives the override path the job takes, over a bare mirror of
    this checkout carrying refs/dolt/* fetched from the remote the config
    names, so the script's only-this-change-then-restore branch runs here."""
    mirror = tmp_path / "mirror.git"
    # bd's git-backed remote wants a branch on the remote, so the mirror gets
    # this checkout's HEAD as `main` beside the Dolt ref it exists to carry.
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(mirror)], check=True)
    subprocess.run(["git", "-C", str(mirror), "fetch", "-q", str(ROOT), "HEAD:refs/heads/main"], check=True)
    git_remote = REMOTE.removeprefix("git+")
    subprocess.run(["git", "-C", str(mirror), "fetch", "-q", git_remote, "+refs/dolt/*:refs/dolt/*"],
                   check=True, timeout=600)
    env = dict(os.environ, TRACKER_PROOF_REMOTE=f"git+file://{mirror}")
    result = subprocess.run(["bash", str(SCRIPT), str(tmp_path / "clone")], env=env,
                            cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HEAD and tree unchanged" in result.stdout, result.stdout
    config = (tmp_path / "clone" / ".beads" / "config.yaml").read_bytes()
    assert config == (ROOT / ".beads" / "config.yaml").read_bytes(), (
        "the override outlived the run in the clone's config.yaml")


@needs_tracker
@pytest.mark.skipif(shutil.which("bd") is None, reason="no bd on PATH")
def test_the_proof_passes_against_this_checkout(tmp_path):
    """The CI job's step, run here over the remote the config names: clone HEAD,
    run the block, read the bead, pull. Skipped without bd; the `tracker` job
    installs it and cannot skip. Fails, rather than skips, when the remote is
    unreachable from this machine — that is the tracker being unreadable."""
    result = subprocess.run(["bash", str(SCRIPT), str(tmp_path / "clone")],
                            cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HEAD and tree unchanged" in result.stdout, result.stdout
