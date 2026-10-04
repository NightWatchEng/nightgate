"""warden plan — the task packet, and who ran it.

Orientation as an artifact. The property worth protecting: absent hints are
reported as UNKNOWN, never as "nothing to worry about".
"""

import json
import os
from pathlib import Path

import pytest
import yaml

from warden import plan as plan_mod
from warden import runs

HINTS = {
    "area": "payments",
    "risk": "HIGH",
    "scenarios": [
        {"name": "add a payment method",
         "source_files": ["src/payments/"],
         "test_files": ["tests/payments/"]},
    ],
}


class FakeStep:
    def __init__(self, run):
        self.run = run


class FakeConfig:
    def __init__(self, root):
        self.root = root
        self.repo = "sim"
        self.verify = {"app": (FakeStep("pytest -q"),)}
        self.risk_tiers = ()


def write_hints(root: Path, name="payments.yaml", doc=None) -> Path:
    d = root / ".warden" / "file-hints"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(yaml.safe_dump(doc if doc is not None else HINTS))
    return d


def test_packet_without_hints_says_unknown_not_empty(tmp_path):
    packet = plan_mod.build(FakeConfig(tmp_path), task="do a thing")
    assert packet["risk_tier"] == "UNKNOWN"
    assert packet["paths"] == []
    assert packet["caveats"], "absent hints must be stated, not implied"
    assert "unknown, not empty" in packet["caveats"][0]
    assert "UNKNOWN" in plan_mod.render(packet)


def test_packet_uses_hints_for_paths_and_risk(tmp_path):
    write_hints(tmp_path)
    packet = plan_mod.build(FakeConfig(tmp_path), task="t", area="payments")
    assert packet["risk_tier"] == "HIGH"
    assert "src/payments/" in packet["paths"]
    assert packet["scenarios"] == ["add a payment method"]
    assert packet["caveats"] == []


def test_area_lookup_is_case_insensitive(tmp_path):
    write_hints(tmp_path)
    packet = plan_mod.build(FakeConfig(tmp_path), task="t", area="PAYMENTS")
    assert packet["paths"]


def test_unknown_area_degrades_to_unknown(tmp_path):
    write_hints(tmp_path)
    packet = plan_mod.build(FakeConfig(tmp_path), task="t", area="nope")
    assert packet["risk_tier"] == "UNKNOWN" and packet["caveats"]


def test_malformed_hints_fail_loudly(tmp_path):
    d = tmp_path / ".warden" / "file-hints"
    d.mkdir(parents=True)
    (d / "bad.yaml").write_text("[not, a, mapping]")
    with pytest.raises(plan_mod.PlanError, match="expected a mapping"):
        plan_mod.build(FakeConfig(tmp_path), task="t", area="bad")


def test_packet_carries_verify_commands(tmp_path):
    packet = plan_mod.build(FakeConfig(tmp_path), task="t")
    assert packet["verify"]["app"] == ["pytest -q"]
    assert "pytest -q" in plan_mod.render(packet)


def test_priors_come_from_the_reviewer_split_only(tmp_path):
    write_hints(tmp_path)
    mem = tmp_path / ".warden" / "memory"
    mem.mkdir(parents=True)
    rows = [
        {"id": "a", "ts": "2026-08-01T00:00:00+00:00", "seq": 0, "sha": "s",
         "rule_id": "r1", "tags": [], "dir_prefix": "src/payments",
         "file": "src/payments/x.py", "line": 1, "severity": "HIGH",
         "finding": "confirmed thing", "evidence": "e",
         "status": "confirmed", "origin": "day"},
        {"id": "b", "ts": "2026-08-02T00:00:00+00:00", "seq": 0, "sha": "s",
         "rule_id": "r2", "tags": [], "dir_prefix": "src/payments",
         "file": "src/payments/y.py", "line": 1, "severity": "LOW",
         "finding": "refuted thing", "evidence": "e", "reason": "no",
         "status": "refuted", "origin": "day"},
    ]
    (mem / "findings.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))

    packet = plan_mod.build(FakeConfig(tmp_path), task="t", area="payments")
    findings = [p["finding"] for p in packet["priors"]]
    assert "confirmed thing" in findings
    assert "refuted thing" not in findings, "precedents are the judge's, not the planner's"
    assert "do not assume" in plan_mod.render(packet)


def test_available_areas(tmp_path):
    write_hints(tmp_path)
    write_hints(tmp_path, "auth.yaml", {"area": "auth"})
    assert plan_mod.available_areas(tmp_path) == ["auth", "payments"]


# ---------- who ran it ----------------------------------------------------

def test_manifest_stamps_session_and_model_when_env_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("WARDEN_SESSION_ID", "sess-123")
    monkeypatch.setenv("WARDEN_AGENT_MODEL", "claude-fable-5")
    run_dir = runs.create_run_dir(tmp_path, "plan")
    p = runs.write_manifest(run_dir, root=tmp_path, cmd="plan",
                            rules_version="v1", exit_status="ok")
    m = json.loads(p.read_text())
    assert m["session_id"] == "sess-123"
    assert m["agent_model"] == "claude-fable-5"


def test_absent_env_means_absent_keys_never_guesses(tmp_path, monkeypatch):
    monkeypatch.delenv("WARDEN_SESSION_ID", raising=False)
    monkeypatch.delenv("WARDEN_AGENT_MODEL", raising=False)
    run_dir = runs.create_run_dir(tmp_path, "plan")
    p = runs.write_manifest(run_dir, root=tmp_path, cmd="plan",
                            rules_version="v1", exit_status="ok")
    m = json.loads(p.read_text())
    assert "session_id" not in m and "agent_model" not in m


def test_latest_symlink_points_at_the_newest_run(tmp_path):
    first = runs.create_run_dir(tmp_path, "plan")
    latest = first.parent / "latest"
    assert latest.is_symlink() and (latest.resolve() == first.resolve())
    second = runs.create_run_dir(tmp_path, "explain")
    assert latest.resolve() == second.resolve()


# ---------- --path: the tier explain reads --------------------------------

def test_plan_path_reads_the_glob_tier_explain_reads_regression(
        sample_repo, tmp_path, monkeypatch, capsys):
    """`warden plan` printed `Risk tier: UNKNOWN` for a file repo.yaml tiers
    MEDIUM by glob, because it tiered only hinted paths. A path named with
    `--path` is tiered by `config.classify`, the function `explain --base`
    tiers a diff with, so the two cannot disagree."""
    from conftest import copy_sample_repo
    from warden import cli
    from warden import config as config_mod

    root = copy_sample_repo(sample_repo, tmp_path / "platform-src")
    cfg = config_mod.load(root)
    covered = "app/prompt.py"  # `app/**` is MEDIUM
    assert config_mod.classify(cfg, covered).tier == "MEDIUM"

    packet = plan_mod.build(cfg, task="t", given_paths=[covered])
    assert packet["risk_tier"] == "MEDIUM"
    assert packet["paths"] == [covered]
    assert packet["caveats"] == []

    monkeypatch.chdir(root)
    assert cli.main(["plan", "--task", "t", "--path", covered]) == 0
    assert "**Risk tier**: MEDIUM" in capsys.readouterr().out


def test_plan_path_no_glob_covers_is_the_default_low_not_unknown(
        sample_repo, tmp_path):
    """A named path no glob covers takes repo.yaml's unmatched default,
    LOW, as explain prints it. UNKNOWN stays reserved for a packet that
    names no path at all, and that caveat now says how to name one."""
    from conftest import copy_sample_repo
    from warden import config as config_mod

    root = copy_sample_repo(sample_repo, tmp_path / "platform-src")
    cfg = config_mod.load(root)
    uncovered = "notes/readme.txt"
    assert config_mod.classify(cfg, uncovered).glob == "<default>"

    (root / "notes").mkdir()
    (root / uncovered).write_text("x")
    packet = plan_mod.build(cfg, task="t", given_paths=[uncovered])
    assert packet["risk_tier"] == "LOW"
    assert packet["caveats"] == []

    bare = plan_mod.build(cfg, task="t")
    assert bare["risk_tier"] == "UNKNOWN"
    assert "--path" in bare["caveats"][0]


def test_plan_path_any_spelling_of_a_file_takes_its_glob_tier_regression(
        sample_repo, tmp_path):
    """Regression: `--path` was classified raw, so
    `./db/migrations/x.sql`, an absolute path, or a name typed from inside a
    subdirectory missed the HIGH glob and read LOW with no caveat. Each is
    now the repo-relative spelling `git diff` gives explain."""
    from conftest import copy_sample_repo
    from warden import config as config_mod

    root = copy_sample_repo(sample_repo, tmp_path / "platform-src")
    cfg = config_mod.load(root)
    (root / "db" / "migrations").mkdir(parents=True, exist_ok=True)
    (root / "db" / "migrations" / "x.sql").write_text("x")
    for spelling, cwd in (("./db/migrations/x.sql", None),
                          (str(root / "db/migrations/x.sql"), None),
                          ("x.sql", root / "db" / "migrations"),
                          ("migrations/../migrations/x.sql", root / "db")):
        packet = plan_mod.build(cfg, task="t", given_paths=[spelling], cwd=cwd)
        assert packet["paths"] == ["db/migrations/x.sql"], spelling
        assert packet["risk_tier"] == "HIGH", spelling


def test_plan_path_outside_the_repo_is_refused_and_absent_is_said(
        sample_repo, tmp_path):
    from conftest import copy_sample_repo
    from warden import config as config_mod

    root = copy_sample_repo(sample_repo, tmp_path / "platform-src")
    cfg = config_mod.load(root)
    for outside in ("../elsewhere.py", str(tmp_path), "."):
        with pytest.raises(plan_mod.PlanError, match="not a file inside"):
            plan_mod.build(cfg, task="t", given_paths=[outside])

    packet = plan_mod.build(cfg, task="t", given_paths=["app/prompt.pyy"])
    assert packet["risk_tier"] == "MEDIUM"
    assert any("not in the working tree" in c and "app/prompt.pyy" in c
               for c in packet["caveats"])


def test_plan_path_case_variant_directory_and_symlinked_prefix_regression(
        sample_repo, tmp_path):
    """Regression: on a case-insensitive disk `REPO.YAML`
    exists, missed the case-sensitive glob and read LOW with no caveat; so
    did a directory. Both are refused now, naming why. An absolute path
    through a symlinked prefix is inside the repo and is tiered, not refused
    as outside it."""
    from conftest import copy_sample_repo
    from warden import config as config_mod

    root = copy_sample_repo(sample_repo, tmp_path / "platform-src")
    cfg = config_mod.load(root)
    # Never silent LOW: refused where the disk folds case, and named in a
    # caveat where it does not (the variant is then simply absent).
    if (root / "REPO.YAML").exists():
        with pytest.raises(plan_mod.PlanError, match="not spelled as it is on disk"):
            plan_mod.build(cfg, task="t", given_paths=["REPO.YAML"])
    else:
        packet = plan_mod.build(cfg, task="t", given_paths=["REPO.YAML"])
        assert any("REPO.YAML" in c for c in packet["caveats"])
    for d in ("app", "app/", "./app"):
        with pytest.raises(plan_mod.PlanError, match="is a directory"):
            plan_mod.build(cfg, task="t", given_paths=[d])

    link = tmp_path / "linked"
    link.symlink_to(root)
    packet = plan_mod.build(cfg, task="t",
                            given_paths=[str(link / "app" / "prompt.py")])
    assert packet["paths"] == ["app/prompt.py"]
    assert packet["risk_tier"] == "MEDIUM"


def test_plan_path_case_variant_refused_on_a_case_sensitive_disk_regression(
        sample_repo, tmp_path, monkeypatch):
    """Regression: the case-variant refusal only
    ran where the disk folds case, so on CI's case-sensitive disk deleting it
    left every test green. Fold case in `Path.exists` alone, as a
    case-insensitive disk would, and the spelling check must still refuse."""
    from conftest import copy_sample_repo

    root = copy_sample_repo(sample_repo, tmp_path / "platform-src")
    real_exists = Path.exists

    def folded_exists(self, *a, **k):
        parent = self.parent
        if real_exists(parent) and parent.is_dir():
            return self.name.lower() in (n.lower() for n in os.listdir(parent))
        return real_exists(self, *a, **k)

    monkeypatch.setattr(Path, "exists", folded_exists)
    with pytest.raises(plan_mod.PlanError, match="not spelled as it is on disk"):
        plan_mod.repo_relative(root, "REPO.YAML")
    with pytest.raises(plan_mod.PlanError, match="not spelled as it is on disk"):
        plan_mod.repo_relative(root, "App/prompt.py")
    assert plan_mod.repo_relative(root, "app/prompt.py") == "app/prompt.py"


def test_plan_path_symlinked_folder_reads_its_real_tier_regression(
        sample_repo, tmp_path):
    """Regression: a path through a
    folder symlinked INSIDE the repo kept the link spelling and fell to LOW
    with no caveat (`alias/migrations/x.sql` for the HIGH `db/migrations/**`),
    and one through a link pointing OUT of the repo was accepted. The folder
    part is resolved first now: the first tiers as its real path, the second
    is refused as outside the enrollment."""
    from conftest import copy_sample_repo
    from warden import config as config_mod

    root = copy_sample_repo(sample_repo, tmp_path / "platform-src")
    cfg = config_mod.load(root)
    assert config_mod.classify(cfg, "db/migrations/x.sql").tier == "HIGH"
    (root / "alias").symlink_to(root / "db")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.py").write_text("x")
    (root / "vend").symlink_to(outside)

    packet = plan_mod.build(cfg, task="t", given_paths=["alias/migrations/x.sql"])
    assert packet["paths"] == ["db/migrations/x.sql"]
    assert packet["risk_tier"] == "HIGH"
    with pytest.raises(plan_mod.PlanError, match="not a file inside"):
        plan_mod.build(cfg, task="t", given_paths=["vend/f.py"])


def test_plan_path_cli_reads_the_path_relative_to_the_cwd_regression(
        sample_repo, tmp_path, monkeypatch, capsys):
    """Regression: the only CLI test
    ran from the root, so dropping `cwd=Path.cwd()` in _cmd_plan left the
    suite green. Run from `db/migrations`, a bare `x.sql` must tier as
    `db/migrations/x.sql`, HIGH, which only the cwd reading gives."""
    from conftest import copy_sample_repo
    from warden import cli

    root = copy_sample_repo(sample_repo, tmp_path / "platform-src")
    sub = root / "db" / "migrations"
    sub.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(sub)
    assert cli.main(["plan", "--task", "t", "--path", "x.sql"]) == 0
    assert "**Risk tier**: HIGH" in capsys.readouterr().out

