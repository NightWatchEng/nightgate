"""publish-public.yml and scripts/publish-sync.py: every main commit lands once on the
public repository, fail-closed; a bare local repository stands in for it."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import yaml

from warden import declarative
from warden import rules as rules_mod
from warden.diffs import DiffContext

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "publish-public.yml"
_spec = importlib.util.spec_from_file_location("publish_sync", ROOT / "scripts" / "publish-sync.py")
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def _job() -> dict:
    (job,) = yaml.safe_load(WORKFLOW.read_text())["jobs"].values()
    return job


def test_publish_workflow_runs_only_on_main_push():
    doc = yaml.safe_load(WORKFLOW.read_text())
    assert doc.get("on", doc.get(True)) == {"push": {"branches": ["main"]}}
    assert doc["permissions"] == {"contents": "read"} and not doc["concurrency"]["cancel-in-progress"]
    assert _job()["if"] == "github.repository == 'NightWatchEng/agentops'"
    run = _job()["steps"][3]["run"]  # its own clone in $RUNNER_TEMP, the declared floor
    for needle in ('--work "$RUNNER_TEMP/nightgate"', "--tags-from v3.0.0", "trap 'rm -f",
                   "git@github.com:NightWatchEng/nightgate.git", "StrictHostKeyChecking=yes"):
        assert needle in run and "--no-push" not in run, needle
    assert _job()["steps"][-1]["with"]["path"] == "${{ runner.temp }}/parity/"


def test_publish_workflow_fails_when_the_deploy_key_is_missing():
    job = _job()
    guard = job["steps"][0]
    assert guard["env"] == {"DEPLOY_KEY": "${{ secrets.NIGHTGATE_EXPORT_DEPLOY_KEY }}"}
    for step in job["steps"]:  # a skip or a swallowed failure would read as green
        assert "secrets" not in str(step.get("if", "")) and not step.get("continue-on-error")
    bash = ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", guard["run"]]
    missing = subprocess.run(bash, env={"DEPLOY_KEY": ""}, capture_output=True, text=True)
    assert missing.returncode == 1 and "NIGHTGATE_EXPORT_DEPLOY_KEY" in missing.stdout
    assert subprocess.run(bash, env={"DEPLOY_KEY": "k"}, capture_output=True).returncode == 0


def test_publish_workflow_pins_actions_by_sha():
    """The gate's own pinned-actions rule, run over this file, finds nothing."""
    (rule,) = [r for r in rules_mod.load_rules(ROOT / ".warden" / "rules") if r.id == "pinned-actions"]
    rel = str(WORKFLOW.relative_to(ROOT))
    lines = tuple(enumerate(WORKFLOW.read_text().splitlines(), 1))
    ctx = DiffContext(base="a" * 40, head="b" * 40, files=(rel,), added={rel: lines}, removed={},
                      read_base=lambda f: None, read_head=lambda f: None)
    assert not declarative.run(rule, ctx, [rel])
    uses = [s for s in _job()["steps"] if "uses" in s]
    assert len(uses) == 3 and uses[0]["with"]["persist-credentials"] is False


def _git(repo: Path, *args: str) -> str:
    return sync.git(repo, *args).decode().strip()


def _commit(repo: Path, files: dict, message: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
         "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repos(tmp_path: Path) -> tuple[Path, Path, str]:
    private, public = tmp_path / "private", tmp_path / "public.git"
    private.mkdir()
    _git(private, "init", "-q", "-b", "main")
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(public))
    first = _commit(private, {
        "publish.yaml": "top_level:\n  include: [README.md, publish.yaml]\n  exclude: [.beads]\n",
        "README.md": "one\n", ".beads/issues.jsonl": "{}\n"}, "feat(x): one (agentops-ab1) (#1)")
    return private, public, first


def _sync(private: Path, public: Path, tmp_path: Path, head: str, *flags: str) -> int:
    work = tmp_path / f"work-{len(list(tmp_path.glob('work-*')))}"
    return sync.main(["--repo", str(private), "--public", str(public), "--work", str(work),
                      "--head", head, "--report", str(tmp_path / "parity.txt"), *flags])


def _public_log(public: Path) -> list[tuple[str, str | None]]:
    shas = _git(public, "rev-list", "--reverse", "main").split()
    return [(_git(public, "log", "-1", "--format=%s", s), sync.source_of(public, s)) for s in shas]


def test_first_run_imports_then_each_main_commit_lands_once_with_its_source(tmp_path):
    private, public, first = _repos(tmp_path)
    assert _sync(private, public, tmp_path, first) == 0
    assert _public_log(public) == [(sync.IMPORT_SUBJECT, first)]
    (tmp_path / "msg").write_text(_git(public, "log", "-1", "--format=%B", "main") + "\n")
    assert subprocess.run([str(ROOT / "scripts" / "commit-lint.sh"), str(tmp_path / "msg")]).returncode == 0
    second = _commit(private, {"README.md": "two\n"}, "fix(x): two (agentops-cd2) (#2)")
    third = _commit(private, {".beads/issues.jsonl": "[]\n"}, "ops(x): tracker (no-bead: t) (#3)")
    assert _sync(private, public, tmp_path, third) == 0  # catch-up: two commits in one run
    assert _public_log(public)[1:] == [("fix(x): two (no-bead: private tracker) (#2)", second),
                                       ("ops(x): tracker (no-bead: private tracker) (#3)", third)]
    assert "parity ok" in (tmp_path / "parity.txt").read_text()
    assert _git(public, "ls-tree", "-r", "--name-only", "main").split() == ["README.md", "publish.yaml"]
    before = _git(public, "rev-parse", "main")
    assert _sync(private, public, tmp_path, third) == 0  # a rerun carries nothing
    assert _sync(private, public, tmp_path, second) == 0  # a stale run carries nothing
    assert _git(public, "rev-parse", "main") == before


def test_a_parity_failure_refuses_and_pushes_nothing(tmp_path, monkeypatch):
    private, public, first = _repos(tmp_path)
    real = sync.publish.export  # the export also writes a file the manifest did not select
    monkeypatch.setattr(sync.publish, "export", lambda repo, sha, out: (
        real(repo, sha, out), (out / "leak.txt").write_text("leaked\n"))[0])
    assert _sync(private, public, tmp_path, first) == 1
    assert "parity failed" in (tmp_path / "parity.txt").read_text()
    assert _git(public, "for-each-ref") == ""


def test_a_public_tip_this_export_did_not_write_refuses(tmp_path):
    private, public, first = _repos(tmp_path)
    stray = tmp_path / "stray"
    _git(tmp_path, "init", "-q", "-b", "main", str(stray))
    _commit(stray, {"x": "x\n"}, "pushed by hand")
    _git(stray, "push", "-q", str(public), "HEAD:main")
    assert _sync(private, public, tmp_path, first) == 1
    assert "no Source trailer" in (tmp_path / "parity.txt").read_text()


def test_the_work_clone_must_sit_outside_the_private_tree(tmp_path):
    private, public, first = _repos(tmp_path)
    code = sync.main(["--repo", str(private), "--public", str(public), "--head", first,
                      "--work", str(private / "out"), "--report", str(tmp_path / "r.txt")])
    assert code == 2 and "outside" in (tmp_path / "r.txt").read_text()
    assert _git(private, "status", "--porcelain") == ""


def test_tags_mirror_from_the_first_public_release_onto_carried_commits(tmp_path):
    private, public, first = _repos(tmp_path)
    _git(private, "tag", "v2.9.0", first)
    _git(private, "-c", "user.email=t@t", "-c", "user.name=t", "tag", "-a", "-m", "r", "v3.0.0")
    assert _sync(private, public, tmp_path, first, "--tags-from", "v3.0.0") == 0
    assert _git(public, "tag").split() == ["v3.0.0"]
    assert sync.source_of(public, "v3.0.0") == first
    _git(public, "-c", "user.email=t@t", "-c", "user.name=t", "tag", "-f", "-a", "-m", "by hand",
         "v3.0.0", "main")  # annotated, at the right commit: accepted, peeled
    assert _sync(private, public, tmp_path, first) == 0
    later = _commit(private, {"README.md": "later\n"}, "fix(x): later (#4)")
    _git(private, "tag", "v3.1.0", later)
    _git(public, "tag", "v3.1.0", "main")  # a public tag at the wrong commit
    assert _sync(private, public, tmp_path, later) == 1
    assert "public tag v3.1.0 is at" in (tmp_path / "parity.txt").read_text()
    assert len(_public_log(public)) == 2  # the tag refuses; the commit still landed
    _git(public, "tag", "-d", "v3.1.0")
    _git(private, "checkout", "-q", "-b", "side")  # a merged branch tip: never carried
    _git(private, "tag", "v3.2.0", _commit(private, {"README.md": "side\n"}, "side"))
    _git(private, "checkout", "-q", "main")
    _git(private, "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "--no-ff", "-m", "m", "side")
    assert _sync(private, public, tmp_path, _git(private, "rev-parse", "HEAD")) == 1
    assert "v3.2.0 is on" in (tmp_path / "parity.txt").read_text()
    assert sync.source_of(public, "v3.1.0") == later  # the deleted tag was re-mirrored
