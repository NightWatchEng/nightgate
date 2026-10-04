"""`warden init`: a fresh repository reaches Level 3 with no hand-written file,
init never overwrites, and the CI workflow it writes splits the gate so the
platform credential and the review never share a runner with code a pull
request controls.

The workflow's shell steps are executed here, extracted from the generated
YAML, rather than grepped: a message or an order that only reads right is not
evidence the step behaves.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import toolchain
import yaml
from conftest import _AMBIENT_GIT_CONFIG

from warden import catalog as catalog_mod
from warden import cli
from warden import config as config_mod
from warden import declared as declared_mod
from warden import enroll
from warden import mechanical
from warden import verify as verify_mod
from warden.audit import NO_VERIFY_BANNER
from warden.certify import PLATFORM_REPAIR_CAP

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "docs" / "wiki"
# How GitHub Actions runs a Linux `run:` step: `shell: bash` is
# `bash --noprofile --norc -eo pipefail {0}`, and no `shell:` is `bash -e {0}`.
SHELLS = {"bash": ("bash", "--noprofile", "--norc", "-eo", "pipefail", "-c"),
          None: ("bash", "-e", "-c")}

FIXTURES = {
    "python": {"pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.0"\n',
               "demo.py": "def add(a, b):\n    return a + b\n"},
    "node": {"package.json": '{"name": "demo", "version": "0.1.0", '
                             '"scripts": {"test": "node --test"}}\n',
             "index.js": "exports.add = (a, b) => a + b;\n"},
    "go": {"go.mod": "module example.com/demo\n\ngo 1.22\n",
           "demo.go": "package demo\n\nfunc Add(a, b int) int { return a + b }\n"},
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=repo, check=True, capture_output=True, text=True).stdout


def _repo(tmp_path: Path, files: dict[str, str], name: str = "demo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _warden(repo: Path, monkeypatch, *argv: str) -> int:
    monkeypatch.chdir(repo)
    return cli.main(list(argv))


def _snapshot(repo: Path) -> dict[str, bytes]:
    return {p.relative_to(repo).as_posix(): p.read_bytes()
            for p in sorted(repo.rglob("*")) if p.is_file() and ".git" not in p.parts}


def _workflow(langs=("python",), prefix: str = "") -> dict:
    chosen = tuple(lang for lang in enroll.LANGUAGES if lang.name in langs)
    return yaml.safe_load(enroll.render_workflow(chosen, prefix))


def _steps(doc: dict, job: str) -> list[dict]:
    return doc["jobs"][job]["steps"]


def _step(doc: dict, job: str, name: str) -> dict:
    found = [s for s in _steps(doc, job) if s.get("name") == name]
    assert len(found) == 1, [s.get("name") for s in _steps(doc, job)]
    return found[0]


def _uses(doc: dict, job: str, action: str) -> list[dict]:
    return [s for s in _steps(doc, job) if s.get("uses", "").startswith(action + "@")]


CREDENTIAL = "platform credential"
BUILD = "build warden at repo.yaml platform.pin"
REMOVE = "remove platform credential"
WHEEL_INSTALL = "install warden from the install job's wheel"
REQUIRE = "require the install and verify jobs"
TAKE = "take the verify results"
TAKE_RUN = 'warden take --from "$RUNNER_TEMP/warden-verify"'
PRIVATE = "require a private repository"
REPO_YAML = "require repo.yaml"


# ── enrollment ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", sorted(FIXTURES))
def test_init_takes_a_fresh_repo_to_level_3_with_no_hand_written_file(
        lang, tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES[lang])
    assert _warden(repo, monkeypatch, "init") == 0
    out = capsys.readouterr().out
    assert "Next steps:" in out and "warden certify --level 3" in out

    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll")
    planned = enroll.plan(repo).files
    tracked = set(_git(repo, "ls-files").split())
    assert tracked == set(FIXTURES[lang]) | set(planned) | {".gitignore"}, (
        "the enrollment commit holds a file neither the fixture nor init wrote")

    assert _warden(repo, monkeypatch, "explain") == 0
    assert _warden(repo, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    capsys.readouterr()
    assert _warden(repo, monkeypatch, "certify", "--level", "3") == 0
    report = capsys.readouterr().out
    assert "certification: LEVEL 3 (Reviewed)" in report, report


@pytest.mark.parametrize("lang, scope", [
    ("python", ["PYTHONDONTWRITEBYTECODE=1 uv run --isolated --with pytest "
                "python -m pytest -q || [ $? -eq 5 ]"]),
    ("node", ["npm install --no-package-lock", "npm test"]),
    ("go", ["go vet ./...", "go test ./..."])])
def test_init_detects_the_manifest_and_writes_its_verify_scope(
        lang, scope, tmp_path, monkeypatch):
    repo = _repo(tmp_path, FIXTURES[lang])
    assert _warden(repo, monkeypatch, "init") == 0
    config = config_mod.load(repo)
    assert {name: [s.run for s in steps] for name, steps in config.verify.items()} \
        == {lang: scope}
    assert [c.lang for c in config.components] == [lang]
    workflow = (repo / enroll.WORKFLOW_PATH).read_text()
    assert f"run: warden verify --scope {lang}" in workflow


@pytest.mark.parametrize("lang", sorted(FIXTURES))
def test_init_declares_runner_pytest_on_the_python_step_only(
        tmp_path, monkeypatch, lang):
    """Round 1 (consumer blast radius): verify stopped parsing the shell for
    pytest, so an enrolled python repo whose step declares no runner got no
    counts on the line deliver's Evidence step reads."""
    repo = _repo(tmp_path, FIXTURES[lang])
    assert _warden(repo, monkeypatch, "init") == 0
    runners = {s.runner for s in config_mod.load(repo).verify[lang]}
    assert runners == ({"pytest"} if lang == "python" else {None}), runners


def test_a_node_repo_with_a_lockfile_installs_with_npm_ci(tmp_path, monkeypatch):
    repo = _repo(tmp_path, {**FIXTURES["node"], "package-lock.json": "{}\n"})
    assert _warden(repo, monkeypatch, "init") == 0
    steps = config_mod.load(repo).verify["node"]
    assert [s.run for s in steps] == ["npm ci", "npm test"]


def test_a_repo_with_several_manifests_enrolls_every_language(tmp_path, monkeypatch):
    repo = _repo(tmp_path, {**FIXTURES["python"], **FIXTURES["go"]})
    assert _warden(repo, monkeypatch, "init") == 0
    config = config_mod.load(repo)
    assert sorted(config.verify) == ["go", "python"]
    workflow = yaml.safe_load((repo / enroll.WORKFLOW_PATH).read_text())
    runs = [s.get("run") for s in _steps(workflow, "verify")]
    assert "warden verify --scope python" in runs and "warden verify --scope go" in runs
    swallowed = (repo / ".warden/rules/swallowed-exceptions.md").read_text()
    assert "'**/*.py'" in swallowed and "*.go" not in swallowed


def test_init_refuses_a_directory_with_no_manifest_and_writes_nothing(
        tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, {"README.md": "hi\n"})
    before = _snapshot(repo)
    assert _warden(repo, monkeypatch, "init") == 2
    err = capsys.readouterr().err
    assert "pyproject.toml" in err and "package.json" in err and "go.mod" in err
    assert _snapshot(repo) == before


def test_repo_yaml_declares_a_repair_budget_within_the_platform_cap(tmp_path, monkeypatch):
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    raw = yaml.safe_load((repo / "repo.yaml").read_text())
    budget = raw["repair"]["budget"]
    assert isinstance(budget, int) and 1 <= budget <= PLATFORM_REPAIR_CAP
    assert raw["platform"]["pin"] == f"v{enroll.__version__}"
    config_mod.enforce_platform_pin(config_mod.load(repo))


def test_a_second_run_refuses_to_overwrite_and_names_the_file(
        tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES["go"])
    assert _warden(repo, monkeypatch, "init") == 0
    capsys.readouterr()
    before = _snapshot(repo)
    assert _warden(repo, monkeypatch, "init") == 1
    err = capsys.readouterr().err
    assert "refusing to overwrite" in err
    for rel in ("repo.yaml", enroll.WORKFLOW_PATH, enroll.POLICY_PATH,
                ".warden/rules/secrets-in-diff.md"):
        assert rel in err, err
    assert _snapshot(repo) == before


@pytest.mark.parametrize("existing", [enroll.WORKFLOW_PATH, enroll.POLICY_PATH,
                                      ".warden/rules/secrets-in-diff.md"])
def test_one_existing_target_refuses_the_whole_run(
        existing, tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, {**FIXTURES["python"], existing: "mine\n"})
    before = _snapshot(repo)
    assert _warden(repo, monkeypatch, "init") == 1
    err = capsys.readouterr().err
    assert existing in err and "repo.yaml" not in err
    assert _snapshot(repo) == before


def test_a_dangling_symlink_at_a_target_is_refused_not_followed(
        tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES["python"])
    outside = tmp_path / "outside.yaml"
    (repo / "repo.yaml").symlink_to(outside)
    assert _warden(repo, monkeypatch, "init") == 1
    assert "repo.yaml" in capsys.readouterr().err
    assert not outside.exists()


def test_the_derived_state_paths_are_gitignored(tmp_path, monkeypatch):
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    lines = (repo / ".gitignore").read_text().splitlines()
    for entry in enroll.GITIGNORE_ENTRIES:
        assert entry in lines
    for probe in (".warden/out/run/manifest.json", ".warden/memory/findings.jsonl",
                  ".warden/memory/gate/shard.json"):
        assert subprocess.run(["git", "check-ignore", "-q", probe],
                              cwd=repo).returncode == 0, probe
    assert {".warden/out/", ".warden/memory/findings.jsonl",
            ".warden/memory/gate/"} == set(enroll.GITIGNORE_ENTRIES)


def test_an_existing_gitignore_is_extended_never_rewritten(tmp_path, monkeypatch):
    original = "node_modules/\n.warden/out/"
    repo = _repo(tmp_path, {**FIXTURES["node"], ".gitignore": original})
    assert _warden(repo, monkeypatch, "init") == 0
    text = (repo / ".gitignore").read_text()
    assert text.startswith(original + "\n")
    lines = text.splitlines()
    assert lines.count(".warden/out/") == 1
    assert lines.count("node_modules/") == 1
    assert ".warden/memory/findings.jsonl" in lines and ".warden/memory/gate/" in lines


# ── starter rules and policy ────────────────────────────────────────────────

@pytest.mark.parametrize("lang, expected", [
    ("python", {"secrets-in-diff", "swallowed-exceptions"}),
    ("node", {"secrets-in-diff"}),
    ("go", {"secrets-in-diff"})])
def test_starter_rules_come_from_the_catalog_and_fit_the_language(
        lang, expected, tmp_path, monkeypatch):
    repo = _repo(tmp_path, FIXTURES[lang])
    assert _warden(repo, monkeypatch, "init") == 0
    from warden import rules as rules_mod
    rules = {r.id: r for r in rules_mod.load_rules(repo / enroll.RULES_DIR)}
    assert set(rules) == expected
    entries = {e.id: e for e in catalog_mod.load_catalog()}
    for entry_id, starter in enroll.STARTERS.items():
        if starter.rule_id not in rules:
            continue
        rule = rules[starter.rule_id]
        assert rule.implements == (entry_id,)
        if rule.engine == "declarative":
            assert [dict(c) for c in rule.checks] == entries[entry_id].starter["checks"]


def test_every_unconditional_enforceable_catalog_entry_is_classified_for_init():
    not_written = {
        "review-lens-complexity": "engine: python with no core checker; its "
                                  "starter is a sketch a repo must implement",
    }
    entries = {e.id: e for e in catalog_mod.load_catalog()}
    unconditional = {e.id for e in entries.values()
                     if e.applies_when.startswith("always")
                     and e.engine in ("declarative", "python")}
    assert unconditional == set(enroll.STARTERS) | set(not_written), (
        "an unconditional catalog entry is neither written by init nor "
        "excluded with a reason")
    for entry_id, starter in enroll.STARTERS.items():
        assert set(starter.languages) <= set(enroll.ALL_LANGUAGES)
        if entries[entry_id].engine == "python":
            assert starter.rule_id in mechanical.CHECKERS


def test_the_python_starter_fires_on_a_swallowed_exception(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll")
    (repo / "demo.py").write_text("def add(a, b):\n    try:\n        return a + b\n"
                                  "    except Exception:\n        pass\n")
    _git(repo, "commit", "-qam", "swallow")
    capsys.readouterr()
    assert _warden(repo, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    assert "except-pass" in capsys.readouterr().out


def test_the_generated_policy_carries_every_heading_level_3_checks(tmp_path):
    baseline = yaml.safe_load(
        (ROOT / "warden" / "certification" / "baseline.yaml").read_text())
    heading_checks = [c for c in baseline["levels"][3]["checks"]
                      if c["type"] == "file_contains"
                      and c["path"] == enroll.POLICY_PATH]
    assert {c["id"] for c in heading_checks} == {f"R-0{i}" for i in range(2, 8)}
    langs = tuple(lang for lang in enroll.LANGUAGES if lang.name == "python")
    policy = enroll.render_policy(tmp_path, langs)
    for check in heading_checks:
        assert re.search(check["pattern"], policy, re.M), check["id"]


# ── the generated workflow ──────────────────────────────────────────────────

def test_the_workflow_is_valid_yaml_gated_on_pull_requests_in_three_jobs():
    doc = _workflow(("python", "node", "go"))
    triggers = doc.get("on", doc.get(True))
    assert "pull_request" in triggers
    jobs = doc["jobs"]
    assert set(jobs) == {"install", "verify", "gate"}
    assert jobs["verify"]["needs"] == "install"
    assert jobs["gate"]["needs"] == ["install", "verify"]
    # A job skipped because a job it needs failed reports success to a
    # required check, so the gate runs whatever its needs ended as.
    assert jobs["gate"]["if"] == "${{ !cancelled() }}"
    assert jobs["gate"]["name"] == enroll.GATE_CHECK
    for name, job in jobs.items():
        for checkout in _uses(doc, name, "actions/checkout"):
            assert checkout["with"]["persist-credentials"] is False, name
    assert _uses(doc, "gate", "actions/checkout")[0]["with"]["fetch-depth"] == 0


def _declared_shell(doc: dict, job: str, step: dict) -> str | None:
    for defaults in (step, (doc["jobs"][job].get("defaults") or {}).get("run") or {},
                     (doc.get("defaults") or {}).get("run") or {}):
        if defaults.get("shell"):
            return defaults["shell"]
    return None


def _run_step(doc: dict, job: str, step: dict, env: dict[str, str], cwd: Path,
              shell: tuple[str, ...] | None = None) -> subprocess.CompletedProcess:
    argv = shell or SHELLS[_declared_shell(doc, job, step)]
    return subprocess.run([*argv, step["run"]], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **_AMBIENT_GIT_CONFIG, **env},
                          timeout=60)


def test_every_run_step_declares_bash_so_actions_runs_it_with_pipefail():
    doc = _workflow(("python", "node", "go"))
    undeclared = [(job, s.get("name")) for job in doc["jobs"] for s in _steps(doc, job)
                  if "run" in s and s.get("shell") != "bash"]
    assert undeclared == []


def test_credential_step_exits_2_with_the_fork_message_when_the_secret_is_empty(tmp_path):
    doc = _workflow()
    step = _step(doc, "install", CREDENTIAL)
    assert step["env"][enroll.DEPLOY_KEY_SECRET] == (
        "${{ secrets.%s }}" % enroll.DEPLOY_KEY_SECRET)
    result = _run_step(doc, "install", step, {enroll.DEPLOY_KEY_SECRET: "",
                                             enroll.DEPLOY_KEY_SECRET_ALIAS: "",
                                             "RUNNER_TEMP": str(tmp_path),
                                             "GITHUB_OUTPUT": str(tmp_path / "out")}, tmp_path)
    assert result.returncode == 2, result
    assert "fork" in result.stderr
    assert enroll.DEPLOY_KEY_SECRET in result.stderr
    assert "DID NOT RUN" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_credential_step_writes_an_owner_only_key_when_the_secret_is_set(tmp_path):
    doc = _workflow()
    step = _step(doc, "install", CREDENTIAL)
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"
    runner = tmp_path / "runner"
    runner.mkdir()
    result = _run_step(doc, "install", step, {enroll.DEPLOY_KEY_SECRET: key,
                                             enroll.DEPLOY_KEY_SECRET_ALIAS: "",
                                             "RUNNER_TEMP": str(runner),
                                             "GITHUB_OUTPUT": str(tmp_path / "out")}, tmp_path)
    assert result.returncode == 0, result
    assert key not in result.stdout + result.stderr
    written = runner / "nightgate_deploy_key"
    assert written.read_text() == key + "\n"
    assert written.stat().st_mode & 0o777 == 0o600
    assert "github.com ssh-ed25519" in (runner / "nightgate_known_hosts").read_text()


def _credential(tmp_path: Path, new: str, old: str) -> tuple[subprocess.CompletedProcess, str]:
    doc = _workflow()
    output = tmp_path / "github_output"
    result = _run_step(doc, "install", _step(doc, "install", CREDENTIAL), {
        enroll.DEPLOY_KEY_SECRET: new, enroll.DEPLOY_KEY_SECRET_ALIAS: old,
        "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(output)}, tmp_path)
    return result, output.read_text() if output.exists() else ""


def test_the_emitted_gate_reads_the_new_deploy_key_secret(tmp_path):
    step = _step(_workflow(), "install", CREDENTIAL)
    assert enroll.DEPLOY_KEY_SECRET == "NIGHTGATE_DEPLOY_KEY"
    assert list(step["env"]) == [enroll.DEPLOY_KEY_SECRET, enroll.DEPLOY_KEY_SECRET_ALIAS]
    for name in step["env"]:
        assert step["env"][name] == "${{ secrets.%s }}" % name
    # Both set: the new name wins, and nothing warns.
    result, output = _credential(tmp_path, "new-key", "old-key")
    assert result.returncode == 0, result
    assert (tmp_path / "nightgate_deploy_key").read_text() == "new-key\n"
    assert output == f"repo={enroll.PLATFORM_REPO}\n"
    assert "::warning" not in result.stdout + result.stderr


def test_the_emitted_gate_falls_back_to_the_old_secret_and_warns(tmp_path):
    """A consumer that stored only the old secret (shortfall) keeps a working
    gate until the alias leaves: its key is a deploy key on the repository the
    platform was installed from before, so the fallback installs from there."""
    result, output = _credential(tmp_path, "", "old-key")
    assert result.returncode == 0, result
    assert (tmp_path / "nightgate_deploy_key").read_text() == "old-key\n"
    assert output == f"repo={enroll.PLATFORM_REPO_BEFORE}\n"
    warning = [line for line in result.stdout.splitlines() if line.startswith("::warning")]
    assert len(warning) == 1, result.stdout
    for phrase in (enroll.DEPLOY_KEY_SECRET_ALIAS, enroll.DEPLOY_KEY_SECRET,
                   enroll.DEPLOY_KEY_ALIAS_ENDS, enroll.PLATFORM_REPO):
        assert phrase in warning[0], phrase
    assert "old-key" not in result.stdout + result.stderr


def test_the_emitted_gate_installs_the_platform_from_the_public_repo(tmp_path, monkeypatch):
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    doc = yaml.safe_load((repo / enroll.WORKFLOW_PATH).read_text())
    naming_old = [s.get("name") for job in doc["jobs"] for s in _steps(doc, job)
                  if enroll.PLATFORM_REPO_BEFORE in yaml.safe_dump(s)]
    assert naming_old == [CREDENTIAL, BUILD], "the old repository outside the alias"
    assert _step(doc, "install", CREDENTIAL)["id"] == "credential"
    build = _step(doc, "install", BUILD)
    assert build["env"] == {"PLATFORM_REPO": "${{ steps.credential.outputs.repo }}"}
    result, log, _ = _build(repo, tmp_path)
    assert result.returncode == 0, result
    assert "git@github.com:NightWatchEng/nightgate.git platform-src" in log, log
    assert enroll.PLATFORM_REPO == "NightWatchEng/nightgate"


@pytest.mark.parametrize("pin, clones", [("v3.9.9", True), ("v4.0.0", False), ("v12.0.0", False)])
def test_build_step_clones_the_old_repository_for_the_old_secret_until_the_alias_ends(
        pin, clones, tmp_path):
    """The fallback's clone is the old repository, where the old key is a
    deploy key, and the committed workflow itself ends the alias at
    DEPLOY_KEY_ALIAS_ENDS: a pin bump rewrites no workflow, so nothing else could."""
    assert enroll.DEPLOY_KEY_ALIAS_ENDS == "v4.0.0"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "repo.yaml").write_text(f"version: 1\nplatform:\n  pin: {pin}\n")
    result, log, output = _build(repo, tmp_path, platform=enroll.PLATFORM_REPO_BEFORE)
    if clones:
        assert result.returncode == 0, result
        assert (f"--branch {pin} git@github.com:{enroll.PLATFORM_REPO_BEFORE}.git "
                "platform-src") in log, log
    else:
        assert result.returncode == 2, result
        assert log == "" and output == ""
        for phrase in ("DID NOT RUN", enroll.DEPLOY_KEY_SECRET_ALIAS, enroll.DEPLOY_KEY_SECRET):
            assert phrase in result.stderr, phrase
    # The new secret is unaffected by the alias's end.
    shutil.rmtree(tmp_path / "runner")
    result, log, _ = _build(repo, tmp_path, platform=enroll.PLATFORM_REPO)
    assert result.returncode == 0, result
    assert f"git@github.com:{enroll.PLATFORM_REPO}.git" in log, log


@pytest.mark.parametrize("platform", ["", "NightWatchEng/other", "x;touch pwned"])
def test_build_step_refuses_a_platform_repository_the_credential_step_did_not_name(
        platform, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "repo.yaml").write_text("version: 1\nplatform:\n  pin: v1.2.3\n")
    result, log, output = _build(repo, tmp_path, platform=platform)
    assert result.returncode == 2, result
    assert "DID NOT RUN" in result.stderr and "platform repository" in result.stderr
    assert log == "" and output == ""
    assert not (repo / "pwned").exists()


def test_the_install_job_executes_nothing_from_the_checkout(tmp_path):
    doc = _workflow(("python", "go"))
    steps = _steps(doc, "install")
    allowed_actions = ("astral-sh/setup-uv@", "actions/checkout@", "actions/upload-artifact@")
    for s in steps:
        if "uses" in s:
            assert s["uses"].startswith(allowed_actions), s["uses"]
    names = [s.get("name") for s in steps if "run" in s]
    assert names == [PRIVATE, REPO_YAML, CREDENTIAL, BUILD, REMOVE], (
        "a step in the job holding the key that is not the visibility check, "
        "the repo.yaml check, the credential, the build or the removal")
    assert steps[0]["name"] == PRIVATE
    checkout = _uses(doc, "install", "actions/checkout")
    assert len(checkout) == 1
    assert checkout[0]["with"]["sparse-checkout"] == "/repo.yaml"
    assert checkout[0]["with"]["sparse-checkout-cone-mode"] is False
    order = [s.get("name") or s["uses"].split("@")[0] for s in steps]
    assert order.index("astral-sh/setup-uv") < order.index("actions/checkout")
    assert order.index("actions/checkout") < order.index(REPO_YAML) < order.index(CREDENTIAL)
    assert order.index(CREDENTIAL) < order.index(BUILD) < order.index(REMOVE) \
        < order.index("actions/upload-artifact")
    remove = _step(doc, "install", REMOVE)
    assert remove["if"] == "always()"

    (tmp_path / "nightgate_deploy_key").write_text("secret\n")
    result = _run_step(doc, "install", remove, {"RUNNER_TEMP": str(tmp_path)}, tmp_path)
    assert result.returncode == 0, result
    assert not (tmp_path / "nightgate_deploy_key").exists()


def test_no_job_but_install_references_a_secret():
    doc = _workflow(("python", "node", "go"))

    def referenced(node) -> list[str]:
        """Every `secrets` context an expression reads; `toJSON(secrets)` is ''."""
        text = yaml.safe_dump(node, width=10_000)
        return [name for expr in re.findall(r"\$\{\{(.*?)\}\}", text, re.S)
                for name in re.findall(r"\bsecrets\b(?:\.(\w+))?", expr)]

    assert referenced({k: v for k, v in doc.items() if k != "jobs"}) == []

    assert sorted(referenced(doc["jobs"]["install"])) == sorted(
        [enroll.DEPLOY_KEY_SECRET, enroll.DEPLOY_KEY_SECRET_ALIAS])
    assert referenced(doc["jobs"]["verify"]) == []
    gate = doc["jobs"]["gate"]
    assert referenced(gate) == ["GITHUB_TOKEN"]
    assert referenced(_step(doc, "gate", "warden review")) == ["GITHUB_TOKEN"]
    runs_verify = {job for job in doc["jobs"] for s in _steps(doc, job)
                   if "warden verify" in (s.get("run") or "")}
    assert runs_verify == {"verify"}


def test_the_gate_job_runs_no_pull_request_code():
    doc = _workflow(("python", "go"))
    allowed_actions = ("actions/checkout@", "astral-sh/setup-uv@",
                       "actions/download-artifact@", "actions/upload-artifact@")
    for s in _steps(doc, "gate"):
        if "uses" in s:
            assert s["uses"].startswith(allowed_actions), s["uses"]
    names = [s.get("name") for s in _steps(doc, "gate") if "run" in s]
    classify = ([enroll.CLASSIFY_STEP]
                if enroll.carries_classify_command(enroll.__version__) else [])
    assert names == [REQUIRE, WHEEL_INSTALL, TAKE, "warden review", *classify,
                     "warden certify"]
    assert _step(doc, "gate", "warden review")["run"] == (
        'warden review --no-project-checkers --event "$GITHUB_EVENT_PATH"')
    assert _step(doc, "gate", "warden certify")["run"] == "warden certify --level 3"
    assert _step(doc, "gate", TAKE)["run"] == TAKE_RUN
    assert _uses(doc, "gate", "actions/download-artifact")[1]["with"]["path"] == (
        "${{ runner.temp }}/warden-verify")

    wheel_upload = _uses(doc, "install", "actions/upload-artifact")
    assert len(wheel_upload) == 1
    wheel = wheel_upload[0]["with"]["name"]
    verify_uploads = {s["with"]["name"] for s in _uses(doc, "verify", "actions/upload-artifact")}
    assert wheel not in verify_uploads, "the verify job could supply the gate's warden"
    downloads = [s["with"]["name"] for s in _uses(doc, "gate", "actions/download-artifact")]
    assert downloads == [wheel, "warden-verify"]
    assert verify_uploads == {"warden-verify"}
    assert _uses(doc, "verify", "actions/upload-artifact")[0]["with"]["path"] == (
        ".warden/out/*-verify/verify-result.json")

    install = _step(doc, "gate", WHEEL_INSTALL)
    assert install["env"] == {"WHEEL": "${{ needs.install.outputs.wheel }}",
                              "WHEEL_SHA256": "${{ needs.install.outputs.sha256 }}"}
    assert install == _step(doc, "verify", WHEEL_INSTALL)
    assert doc["jobs"]["install"]["outputs"] == {
        "wheel": "${{ steps.build.outputs.wheel }}",
        "sha256": "${{ steps.build.outputs.sha256 }}"}
    order = [s.get("name") or s["uses"] for s in _steps(doc, "gate")]
    assert order.index(REQUIRE) < min(i for i, s in enumerate(_steps(doc, "gate"))
                                      if s.get("uses", "").startswith(
                                          "actions/download-artifact@"))


@pytest.mark.parametrize("install, verify, code", [
    ("success", "success", 0), ("failure", "skipped", 2), ("skipped", "skipped", 2),
    ("success", "failure", 1), ("success", "cancelled", 1)])
def test_the_gate_fails_unless_install_and_verify_succeeded(install, verify, code, tmp_path):
    doc = _workflow()
    step = _step(doc, "gate", REQUIRE)
    assert step["env"] == {"INSTALL_RESULT": "${{ needs.install.result }}",
                           "VERIFY_RESULT": "${{ needs.verify.result }}"}
    result = _run_step(doc, "gate", step,
                       {"INSTALL_RESULT": install, "VERIFY_RESULT": verify}, tmp_path)
    assert result.returncode == code, result


def _fakes(tmp_path: Path) -> tuple[Path, Path]:
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "fake.log"
    (bindir / "git").write_text(
        '#!/bin/sh\n'
        'printf "git args=%s\\n" "$*" >> "$FAKE_LOG"\n'
        'printf "git cwd=%s\\n" "$PWD" >> "$FAKE_LOG"\n'
        'printf "git ssh=%s\\n" "$GIT_SSH_COMMAND" >> "$FAKE_LOG"\n')
    (bindir / "uv").write_text(
        '#!/bin/sh\n'
        'printf "uv args=%s\\n" "$*" >> "$FAKE_LOG"\n'
        'printf "uv cwd=%s\\n" "$PWD" >> "$FAKE_LOG"\n'
        'printf "uv ssh=%s\\n" "$GIT_SSH_COMMAND" >> "$FAKE_LOG"\n'
        'if [ "$1" = build ]; then\n'
        '  while [ $# -gt 0 ]; do [ "$1" = --out-dir ] && out="$2"; shift; done\n'
        '  mkdir -p "$out"; i=0\n'
        '  while [ "$i" -lt "${FAKE_WHEELS:-1}" ]; do\n'
        '    printf "wheel %s" "$i" > "$out/warden-9.9.$i-py3-none-any.whl"; i=$((i + 1))\n'
        '  done\n'
        'fi\n'
        'if [ "$1" = tool ] && [ "$2" = dir ]; then echo /fake/tool/bin; fi\n')
    for tool in ("git", "uv"):
        (bindir / tool).chmod(0o755)
    return bindir, log


def _build(repo: Path, tmp_path: Path, *, shell=None, wheels: int = 1,
           doc: dict | None = None, platform: str = enroll.PLATFORM_REPO
           ) -> tuple[subprocess.CompletedProcess, str, str]:
    bindir, log = _fakes(tmp_path)
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir(exist_ok=True)
    output = tmp_path / "github_output"
    doc = doc or _workflow()
    result = _run_step(doc, "install", _step(doc, "install", BUILD), {
        **_AMBIENT_GIT_CONFIG,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(log),
        "FAKE_WHEELS": str(wheels), "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(output), "PLATFORM_REPO": platform}, repo, shell)
    return (result, log.read_text() if log.exists() else "",
            output.read_text() if output.exists() else "")


def test_build_step_reads_the_tag_from_repo_yaml_platform_pin(tmp_path, monkeypatch):
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0

    result, log, output = _build(repo, tmp_path)
    assert result.returncode == 0, result
    runner = tmp_path / "runner"
    assert (f"git args=clone --quiet --depth 1 --branch v{enroll.__version__} "
            "git@github.com:NightWatchEng/nightgate.git platform-src") in log, log
    assert f"git cwd={runner}" in log, (
        "the clone ran inside the checkout, where a pull request's files are read")
    assert f"-i {runner}/nightgate_deploy_key" in log
    assert "StrictHostKeyChecking=yes" in log
    assert "uv args=build --no-config --no-cache --wheel --out-dir warden-wheel platform-src" in log
    assert f"uv cwd={runner}" in log
    assert "uv ssh=\n" in log, "the key's ssh command outlived the clone"
    wheel = runner / "warden-wheel" / "warden-9.9.0-py3-none-any.whl"
    assert output == (f"wheel={wheel.name}\n"
                      f"sha256={hashlib.sha256(wheel.read_bytes()).hexdigest()}\n")

    repo_yaml = repo / "repo.yaml"
    repo_yaml.write_text(repo_yaml.read_text().replace(
        f"pin: v{enroll.__version__}", "pin: v9.8.7  # bumped"))
    shutil.rmtree(runner)
    (tmp_path / "fake.log").unlink()
    (tmp_path / "github_output").unlink()
    result, log, _ = _build(repo, tmp_path)
    assert result.returncode == 0, result
    assert "--branch v9.8.7 git@github.com:NightWatchEng/nightgate.git" in log


def test_build_step_refuses_unless_the_build_leaves_exactly_one_wheel(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "repo.yaml").write_text("version: 1\nplatform:\n  pin: v1.2.3\n")
    for wheels in (0, 2):
        shutil.rmtree(tmp_path / "runner", ignore_errors=True)
        result, _, output = _build(repo, tmp_path, wheels=wheels)
        assert result.returncode == 2, (wheels, result)
        assert "exactly one warden wheel" in result.stderr
        assert "sha256=" not in output


# Under `bash -e` too, so the refusal cannot lean on pipefail: a step whose
# declared shell is ever dropped still refuses.
@pytest.mark.parametrize("shell", [None, SHELLS[None]], ids=["declared", "no-pipefail"])
@pytest.mark.parametrize("platform_block", [
    None,
    "",
    "platform:\n  pin: main\n",
    "platform:\n  pin: 'v1.2.3; touch pwned'\n",
    "platform:\n  pin: v1.2.3$(touch${IFS}pwned)\n",
    "platform:\n  other: 1\nnested:\n  pin: v1.2.3\n"])
def test_build_step_refuses_a_pin_that_is_not_a_release_tag(platform_block, shell, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    if platform_block is not None:
        (repo / "repo.yaml").write_text(f"version: 1\nrepo: demo\n{platform_block}")
    result, log, output = _build(repo, tmp_path, shell=shell)
    assert result.returncode == 2, result
    assert "platform.pin" in result.stderr
    assert log == "" and output == ""
    assert not (repo / "pwned").exists()


def test_build_step_reads_a_pin_from_a_crlf_repo_yaml(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "repo.yaml").write_bytes(b"version: 1\r\nplatform:\r\n  pin: v3.1.4\r\n")
    result, log, _ = _build(repo, tmp_path)
    assert result.returncode == 0, result
    assert "--branch v3.1.4 git@" in log, log


def _wheel_install(tmp_path: Path, sha: str) -> tuple[subprocess.CompletedProcess, str, Path]:
    bindir, log = _fakes(tmp_path)
    runner = tmp_path / "runner"
    wheels = runner / "warden-wheel"
    wheels.mkdir(parents=True, exist_ok=True)
    wheel = wheels / "warden-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"the install job's wheel")
    github_path = tmp_path / "github_path"
    doc = _workflow()
    result = _run_step(doc, "gate", _step(doc, "gate", WHEEL_INSTALL), {
        **_AMBIENT_GIT_CONFIG,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(log),
        "RUNNER_TEMP": str(runner), "GITHUB_PATH": str(github_path),
        "WHEEL": wheel.name, "WHEEL_SHA256": sha}, tmp_path)
    return result, log.read_text() if log.exists() else "", wheel


def test_the_wheel_install_takes_the_install_jobs_wheel_at_its_hash(tmp_path):
    good = hashlib.sha256(b"the install job's wheel").hexdigest()
    result, log, wheel = _wheel_install(tmp_path, good)
    assert result.returncode == 0, result
    assert f"uv args=tool install --no-config --no-cache {wheel}" in log, log
    assert (tmp_path / "github_path").read_text() == "/fake/tool/bin\n"


@pytest.mark.parametrize("sha", ["", "0" * 64,
                                 hashlib.sha256(b"a wheel the verify job swapped in").hexdigest()])
def test_the_wheel_install_refuses_a_wheel_whose_hash_is_not_the_install_jobs(sha, tmp_path):
    result, log, _ = _wheel_install(tmp_path, sha)
    assert result.returncode != 0, result
    assert "uv args=" not in log


def _take(checkout: Path, evidence: Path) -> subprocess.CompletedProcess:
    """The generated take step, run through bash with this checkout's warden
    first on PATH, where the gate job's installed warden would be."""
    doc = _workflow()
    bin_dir = Path(sys.executable).parent
    assert (bin_dir / "warden").is_file(), "no warden entry point beside this python"
    return _run_step(doc, "gate", _step(doc, "gate", TAKE),
                     {**_AMBIENT_GIT_CONFIG, "RUNNER_TEMP": str(evidence.parent),
                      "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
                     checkout)


def test_the_gate_takes_only_regular_verify_result_files(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    evidence = tmp_path / "warden-verify"
    for run, name in (("1-verify", "verify-result.json"), ("2-verify", "verify-result.json"),
                      ("3-review", "review-findings.json"), ("4-review", "verify-result.json")):
        (evidence / run).mkdir(parents=True)
        if run != "2-verify":
            (evidence / run / name).write_text('{"scope": "python"}\n')
    outside = tmp_path / "runner-file"
    outside.write_text("not evidence\n")
    (evidence / "2-verify" / "verify-result.json").symlink_to(outside)

    result = _take(checkout, evidence)
    assert result.returncode == 0, result
    taken = sorted(p.relative_to(checkout).as_posix()
                   for p in (checkout / ".warden" / "out").rglob("*") if p.is_file())
    assert taken == [".warden/out/1-verify/verify-result.json"]

    empty = tmp_path / "empty" / "warden-verify"
    empty.mkdir(parents=True)
    result = _take(tmp_path / "checkout", empty)
    assert result.returncode == 2 and "no verify result" in result.stderr


@pytest.mark.parametrize("planted", ["result-link", "out-dir-link", "warden-dir-link"])
def test_a_checkout_symlink_cannot_redirect_the_take_step_onto_the_runner(planted, tmp_path):
    """A pull request commits a symlink where the take step writes, aimed at
    the gate runner's installed warden, and its verify job uploads a payload.
    The take step must write only into directories it created itself."""
    sentinel = tmp_path / "home" / "bin" / "warden"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("#!/bin/sh\necho REAL WARDEN\n")
    sentinel.chmod(0o755)
    original = sentinel.read_bytes()

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    aliased = tmp_path / "aliased"
    if planted == "result-link":
        (checkout / ".warden" / "out" / "x-verify").mkdir(parents=True)
        (checkout / ".warden" / "out" / "x-verify" / "verify-result.json").symlink_to(sentinel)
    elif planted == "out-dir-link":
        (aliased / "x-verify").mkdir(parents=True)
        (aliased / "x-verify" / "verify-result.json").symlink_to(sentinel)
        (checkout / ".warden").mkdir()
        (checkout / ".warden" / "out").symlink_to(aliased, target_is_directory=True)
    else:
        (aliased / "out" / "x-verify").mkdir(parents=True)
        (aliased / "out" / "x-verify" / "verify-result.json").symlink_to(sentinel)
        (checkout / ".warden").symlink_to(aliased, target_is_directory=True)

    evidence = tmp_path / "warden-verify"
    (evidence / "x-verify").mkdir(parents=True)
    payload = "#!/bin/sh\necho PR CODE RAN IN GATE\n"
    (evidence / "x-verify" / "verify-result.json").write_text(payload)

    result = _take(checkout, evidence)
    assert sentinel.read_bytes() == original, "the take step wrote through a checkout symlink"
    if planted == "warden-dir-link":
        assert result.returncode == 2, result
        assert ".warden" in result.stderr and "symlink" in result.stderr, result.stderr
        return
    assert result.returncode == 0, result
    out = checkout / ".warden" / "out"
    taken = out / "x-verify" / "verify-result.json"
    assert not out.is_symlink() and not taken.is_symlink()
    assert taken.read_text() == payload
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*")) == [
        "x-verify", "x-verify/verify-result.json"]


def test_review_in_another_checkout_pairs_the_verify_result_the_gate_took(
        tmp_path, monkeypatch, capsys):
    """The handoff end to end: verify in one clone, the gate's take step, and
    review in a second clone render the scope's result, not NO VERIFY RESULT."""
    origin = _repo(tmp_path, FIXTURES["python"], name="origin")
    assert _warden(origin, monkeypatch, "init") == 0
    repo_yaml = origin / "repo.yaml"
    repo_yaml.write_text(re.sub(r'\{run: ".*pytest.*", runner: pytest\}', '{run: "true"}',
                                repo_yaml.read_text()))
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "enroll")
    verify_clone, gate_clone = tmp_path / "verify-job", tmp_path / "gate-job"
    for clone in (verify_clone, gate_clone):
        _git(tmp_path, "clone", "-q", "--branch", "main", str(origin), str(clone))

    assert _warden(verify_clone, monkeypatch, "verify", "--scope", "python") == 0
    evidence = tmp_path / "artifact" / "warden-verify"
    for result in (verify_clone / ".warden" / "out").glob("*-verify/verify-result.json"):
        (evidence / result.parent.name).mkdir(parents=True)
        shutil.copy(result, evidence / result.parent.name / result.name)

    capsys.readouterr()
    assert _warden(gate_clone, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    assert NO_VERIFY_BANNER in capsys.readouterr().out, "the control: nothing taken yet"

    assert _take(gate_clone, evidence).returncode == 0
    assert _warden(gate_clone, monkeypatch, "review", "--base", "HEAD~1", "--no-comment",
                   "--no-project-checkers") == 0
    comment = capsys.readouterr().out
    assert "**verify --scope python**" in comment and NO_VERIFY_BANNER not in comment, comment
    assert _warden(gate_clone, monkeypatch, "certify", "--level", "3") == 0


@pytest.mark.parametrize("lang", sorted(FIXTURES))
def test_declare_check_reports_no_drift_on_the_workflow_init_writes(
        lang, tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES[lang])
    assert _warden(repo, monkeypatch, "init") == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll")
    capsys.readouterr()
    code = _warden(repo, monkeypatch, "declare", "check")
    out = capsys.readouterr().out
    assert code == 0, out
    assert "D-03" in out and "DRIFT" not in out, out


@pytest.mark.parametrize("broken", [
    "no-take-step", "gate-does-not-need-verify", "another-artifact",
    "take-after-review", "upload-carries-no-verify-result", "verify-runs-no-scope",
    "take-step-only-echoes", "download-lands-where-take-does-not-read",
    "upload-before-verify"])
def test_d03_still_reports_verify_evidence_the_gate_job_never_takes(
        broken, tmp_path, monkeypatch):
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    path = repo / enroll.WORKFLOW_PATH
    doc = yaml.safe_load(path.read_text())
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    config = config_mod.load(repo)
    assert declared_mod.check_scope_runners(repo, config).status == declared_mod.CLEAN, (
        "the control: the generated handoff, re-serialized, satisfies D-03")

    gate, verify = doc["jobs"]["gate"], doc["jobs"]["verify"]
    take = _step(doc, "gate", TAKE)
    if broken == "no-take-step":
        gate["steps"].remove(take)
    elif broken == "gate-does-not-need-verify":
        gate["needs"] = ["install"]
    elif broken == "another-artifact":
        _uses(doc, "gate", "actions/download-artifact")[1]["with"]["name"] = "something-else"
    elif broken == "take-after-review":
        gate["steps"].remove(take)
        gate["steps"].insert(gate["steps"].index(_step(doc, "gate", "warden review")) + 1,
                             take)
    elif broken == "upload-carries-no-verify-result":
        _uses(doc, "verify", "actions/upload-artifact")[0]["with"]["path"] = "dist/"
    elif broken == "take-step-only-echoes":
        take["run"] = 'echo "would read verify-result.json into .warden/out, but does not"\n'
    elif broken == "download-lands-where-take-does-not-read":
        _uses(doc, "gate", "actions/download-artifact")[1]["with"]["path"] = "/dev/null/nowhere"
    elif broken == "upload-before-verify":
        upload = _uses(doc, "verify", "actions/upload-artifact")[0]
        verify["steps"].remove(upload)
        upload["with"]["path"] = "nothing/verify-result.json"
        verify["steps"].insert(0, upload)
    else:
        verify["steps"] = [s for s in verify["steps"]
                           if "warden verify" not in (s.get("run") or "")]
    path.write_text(yaml.safe_dump(doc, sort_keys=False))

    result = declared_mod.check_scope_runners(repo, config)
    assert result.status == declared_mod.DRIFT, result
    assert "--scope python" in result.findings[0].detail


def test_workflow_action_pins_match_the_platform_workflow():
    ref = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)@([0-9a-f]{40})")
    live = dict(ref.findall((ROOT / ".github" / "workflows" / "ci.yml").read_text()))
    # Actions the platform's own CI does not use, pinned to the commit of the
    # release tag named in the template's comment.
    live.setdefault("actions/download-artifact", "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c")
    text = (enroll.TEMPLATES / "workflow.yml").read_text()
    template = ref.findall(text)
    assert template, "no pinned action in the workflow template"
    assert {a: sha for a, sha in template if live.get(a) != sha} == {}
    assert len(re.findall(r"uses:", text)) == len(template), "an action not pinned to a sha"


def _warden_argvs(doc: dict) -> list[list[str]]:
    """Each warden command's arguments, a `\\`-continued line joined and the
    argv ended at the first redirection or list operator."""
    argvs = []
    for job in doc["jobs"].values():
        for step in job["steps"]:
            run = (step.get("run") or "").replace("\\\n", " ")
            for line in run.splitlines():
                if line.strip().startswith("warden "):
                    words = shlex.split(line)[1:]
                    stop = next((i for i, w in enumerate(words)
                                 if w[:1] in "><|;&" or w[:2] == "2>"), len(words))
                    argvs.append(words[:stop])
    return argvs


_PARSE_PROBE = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import warden, warden.cli
assert Path(warden.__file__).resolve().parent == Path(sys.argv[1]).resolve() / "warden"
parser = warden.cli.build_parser()
bad = []
for argv in json.loads(sys.argv[2]):
    try:
        parser.parse_args(argv)
    except SystemExit:
        bad.append(argv)
print(json.dumps(bad))
"""


def test_the_generated_gate_invokes_only_cli_the_pinned_release_has(tmp_path):
    """The gate runs the platform built at repo.yaml platform.pin, not this
    checkout. Every warden command the workflow runs must parse under the CLI of
    that release: this checkout's when the tag is not cut yet (the release tag
    check forces it to be cut from this version), the tag's own when it is."""
    pin = yaml.safe_load(enroll.render_repo_yaml(tmp_path, enroll.LANGUAGES))["platform"]["pin"]
    assert pin == f"v{enroll.__version__}"
    argvs = _warden_argvs(_workflow(enroll.ALL_LANGUAGES))
    assert {argv[0] for argv in argvs} - {"attest"} == {
        "verify", "review", "certify", "take"}, argvs

    def unparsed(tree: Path) -> list[list[str]]:
        probe = subprocess.run([sys.executable, "-c", _PARSE_PROBE, str(tree), json.dumps(argvs)],
                               capture_output=True, text=True, cwd=tmp_path)
        assert probe.returncode == 0, probe.stderr
        return json.loads(probe.stdout)

    assert unparsed(ROOT) == []
    tag = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{pin}^{{commit}}"],
                         cwd=ROOT, capture_output=True, text=True)
    if tag.returncode == 0:
        archive = subprocess.run(["git", "archive", tag.stdout.strip(), "warden"], cwd=ROOT,
                                 capture_output=True, check=True).stdout
        released = tmp_path / "released"
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(released, filter="data")
        assert unparsed(released) == [], (
            f"the gate init writes needs a newer CLI than {pin}: bump warden.__version__")


# ── docs ────────────────────────────────────────────────────────────────────

def _section(page: str, heading: str) -> str:
    text = (WIKI / page).read_text()
    assert f"\n## {heading}\n" in text, f"{page} has no '## {heading}' section"
    body = text.split(f"\n## {heading}\n", 1)[1]
    return body.split("\n## ", 1)[0]


def test_installation_documents_the_deploy_key_setup_in_one_short_section():
    body = _section("Installation.md", "CI access to the platform")
    assert len(body.strip().splitlines()) <= 30, "the section is meant to be short"
    for phrase in (enroll.DEPLOY_KEY_SECRET, enroll.DEPLOY_KEY_SECRET_ALIAS,
                   enroll.PLATFORM_REPO, enroll.DEPLOY_KEY_ALIAS_ENDS,
                   "read-only", "deploy key", "platform.pin", "fork",
                   "push ruleset", "required reviewers"):
        assert phrase in body, phrase


def test_quickstart_and_adopting_lead_with_warden_init():
    for page in ("Quickstart.md", "Adopting.md"):
        text = (WIKI / page).read_text()
        first_h2 = text.index("\n## ")
        assert "warden init" in text[:text.index("\n## ", first_h2 + 1)], (
            f"{page} does not lead with warden init")
    adopting = (WIKI / "Adopting.md").read_text()
    assert adopting.index("warden init") < adopting.index("## 1 · Write `repo.yaml`")


# ── review-round regressions ────────────────────────────────────────────────

def test_no_job_restores_or_keeps_a_uv_cache(tmp_path, monkeypatch):
    """A cache one job wrote, another job of the same pull request would read:
    the verify job runs pull request code, and the install job holds the key."""
    doc = _workflow()
    for job in doc["jobs"]:
        for setup_uv in _uses(doc, job, "astral-sh/setup-uv"):
            assert setup_uv["with"]["enable-cache"] is False, job
            assert setup_uv["with"]["version"] == "0.12.1", job
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    result, log, _ = _build(repo, tmp_path)
    assert result.returncode == 0, result
    assert "uv args=build --no-config --no-cache" in log, log
    assert "--no-config --no-cache" in _step(doc, "gate", WHEEL_INSTALL)["run"]


def test_the_empty_secret_message_names_the_dependabot_route(tmp_path):
    doc = _workflow()
    step = _step(doc, "install", CREDENTIAL)
    result = _run_step(doc, "install", step, {enroll.DEPLOY_KEY_SECRET: "",
                                             enroll.DEPLOY_KEY_SECRET_ALIAS: "",
                                             "RUNNER_TEMP": str(tmp_path),
                                             "GITHUB_OUTPUT": str(tmp_path / "out")}, tmp_path)
    assert result.returncode == 2
    assert "--app dependabot" in result.stderr
    assert "--app dependabot" in _section("Installation.md", "CI access to the platform")


@pytest.mark.parametrize("blocker, named", [
    ("github-file", ".github"),
    ("gitignore-dir", ".gitignore"),
    ("warden-symlink", ".warden")])
def test_a_path_init_would_write_through_is_refused_before_anything_is_written(
        blocker, named, tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES["python"])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if blocker == "github-file":
        (repo / ".github").write_text("not a directory\n")
    elif blocker == "gitignore-dir":
        (repo / ".gitignore").mkdir()
    else:
        (repo / ".warden").symlink_to(elsewhere, target_is_directory=True)
    before = _snapshot(repo)
    assert _warden(repo, monkeypatch, "init") == 1
    err = capsys.readouterr().err
    assert named in err and "nothing was written" in err, err
    assert _snapshot(repo) == before
    assert not (repo / "repo.yaml").exists()
    assert list(elsewhere.iterdir()) == []


def test_a_write_that_fails_midway_leaves_nothing_behind(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES["python"])
    before = _snapshot(repo)
    real_open = open
    calls = {"n": 0}

    def failing_open(path, mode="r", *args, **kwargs):
        if mode == "x":
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("disk full")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    assert _warden(repo, monkeypatch, "init") == 2
    assert "disk full" in capsys.readouterr().err
    monkeypatch.undo()
    assert _snapshot(repo) == before
    assert not (repo / ".warden").exists() and not (repo / ".github").exists()


def test_a_gitignore_that_is_not_utf8_is_extended_byte_for_byte(tmp_path, monkeypatch):
    original = b"build/\ncaf\xe9.log\n"
    repo = _repo(tmp_path, {**FIXTURES["node"]})
    (repo / ".gitignore").write_bytes(original)
    assert _warden(repo, monkeypatch, "init") == 0
    data = (repo / ".gitignore").read_bytes()
    assert data.startswith(original)
    for entry in enroll.GITIGNORE_ENTRIES:
        assert entry.encode() in data.splitlines()


def test_a_value_error_while_writing_rolls_the_enrollment_back(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES["python"])
    before = _snapshot(repo)
    real_open = open

    def failing_open(path, mode="r", *args, **kwargs):
        if mode == "ab":
            raise UnicodeDecodeError("utf-8", b"\xe9", 0, 1, "invalid continuation byte")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    assert _warden(repo, monkeypatch, "init") == 2
    assert "nothing was left behind" in capsys.readouterr().err
    monkeypatch.undo()
    assert _snapshot(repo) == before
    assert not (repo / ".warden").exists() and not (repo / ".github").exists()


def test_init_outside_a_git_repository_refuses_and_writes_nothing(tmp_path, monkeypatch, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "package.json").write_text(FIXTURES["node"]["package.json"])
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert _warden(plain, monkeypatch, "init") == 2
    assert "not inside a git repository" in capsys.readouterr().err
    assert [p.name for p in plain.iterdir()] == ["package.json"]


def test_init_shows_gits_own_error_for_a_dubious_ownership_repository(
        tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES["python"])
    before = _snapshot(repo)
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    assert _warden(repo, monkeypatch, "init") == 2
    err = capsys.readouterr().err
    assert "dubious ownership" in err and "safe.directory" in err, err
    assert "not inside a git repository" not in err and "git init" not in err, err
    assert _snapshot(repo) == before


def test_init_at_a_root_with_the_manifest_only_below_it_says_to_run_it_there(
        tmp_path, monkeypatch, capsys):
    mono = _repo(tmp_path, {f"svc/{k}": v for k, v in FIXTURES["python"].items()},
                 name="mono")
    before = _snapshot(mono)
    assert _warden(mono, monkeypatch, "init") == 2
    err = capsys.readouterr().err
    assert "in the directory it enrolls" in err and "not supported" not in err, err
    assert _snapshot(mono) == before


# ── enrollment below the git root ───────────────────────────────────────────

def _mono(tmp_path: Path, below: str = "svc/api", lang: str = "python") -> Path:
    return _repo(tmp_path, {f"{below}/{k}": v for k, v in FIXTURES[lang].items()},
                 name="mono")


def _certify(where: Path, monkeypatch, capsys) -> tuple[int, str]:
    capsys.readouterr()
    code = _warden(where, monkeypatch, "certify", "--level", "3")
    return code, capsys.readouterr().out


def _check_line(report: str, check_id: str) -> str:
    return next(line for line in report.splitlines() if f" {check_id} " in line)


@pytest.mark.parametrize("below, name", [("svc/api", "warden-svc-api.yml"),
                                         ("3.10", "warden-3.10.yml")])
def test_init_below_the_git_root_writes_the_gate_at_the_root_named_for_the_enrollment(
        below, name, tmp_path, monkeypatch, capsys):
    mono = _mono(tmp_path, below)
    assert _warden(mono / below, monkeypatch, "init") == 0
    out = capsys.readouterr().out
    depth = "/".join([".."] * len(below.split("/")))
    assert f"wrote {depth}/.github/workflows/{name}" in out, out
    assert (mono / ".github" / "workflows" / name).is_file()
    assert not (mono / below / ".github").exists()
    assert not (mono / enroll.WORKFLOW_PATH).exists(), (
        "a subdirectory enrollment also claimed the root gate's file name")
    for rel in ("repo.yaml", enroll.POLICY_PATH, ".gitignore"):
        assert (mono / below / rel).is_file(), rel
    assert f'"warden gate ({below})"' in out or f"warden gate ({below})" in out, out


def test_a_second_run_below_the_git_root_names_the_gate_above_it_and_claims_no_other(
        tmp_path, monkeypatch, capsys):
    """Re-running init in an enrolled subdirectory refuses idempotently. The
    gate it would overwrite sits above the enrollment, so the refusal names it
    by the path from the enrollment — `../../.github/...`, not a path relative
    to the git root nothing there can open. And since the gate is this
    enrollment's own, the refusal must NOT add the "is the gate of the
    enrollment at ..." clause that a `svc/api` / `svc-api` collision earns."""
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    capsys.readouterr()
    before = _snapshot(mono)

    assert _warden(api, monkeypatch, "init") == 1
    err = capsys.readouterr().err
    assert "refusing to overwrite" in err, err
    assert "../../.github/workflows/warden-svc-api.yml" in err, err
    for rel in ("repo.yaml", enroll.POLICY_PATH, ".warden/rules/secrets-in-diff.md"):
        assert rel in err, err
    assert "is the gate of" not in err, (
        "the refusal told the enrollment its own gate belongs to someone else\n" + err)
    assert _snapshot(mono) == before


def test_init_below_the_git_root_reaches_level_3_from_the_subdirectory(
        tmp_path, monkeypatch, capsys):
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    _git(mono, "add", "-A")
    _git(mono, "commit", "-qm", "enroll svc/api")
    assert _warden(api, monkeypatch, "explain") == 0
    assert _warden(api, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0

    code, report = _certify(api, monkeypatch, capsys)
    assert code == 0 and "certification: LEVEL 3 (Reviewed)" in report, report
    for check in ("G-04", "V-02"):
        assert "✓" in _check_line(report, check), report
        assert "warden-svc-api.yml" in _check_line(report, check), report

    gate = mono / ".github" / "workflows" / "warden-svc-api.yml"
    gate.rename(gate.with_name("parked.txt"))
    code, report = _certify(api, monkeypatch, capsys)
    assert code != 0 and "✗" in _check_line(report, "G-04"), (
        "the control: without the root gate the subdirectory must not certify")


def test_a_root_enrollment_certifies_from_its_own_workflow_unchanged(
        tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    text = (repo / enroll.WORKFLOW_PATH).read_text()
    assert "working-directory" not in text and "defaults:" not in text
    doc = yaml.safe_load(text)
    assert doc["name"] == "warden"
    assert [job["name"] for job in doc["jobs"].values()] == [
        "warden install", "warden verify", enroll.GATE_CHECK]
    assert _uses(doc, "install", "actions/checkout")[0]["with"]["sparse-checkout"] == "/repo.yaml"
    assert [s["with"]["path"] for s in _uses(doc, "verify", "actions/upload-artifact")
            + _uses(doc, "gate", "actions/upload-artifact")] == [
        ".warden/out/*-verify/verify-result.json", ".warden/out/"]
    _enroll_a_subdirectory_too(repo, monkeypatch)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll")
    assert _warden(repo, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    code, report = _certify(repo, monkeypatch, capsys)
    assert code == 0 and "certification: LEVEL 3 (Reviewed)" in report, report
    assert "warden.yml" in _check_line(report, "G-04"), report


def _enroll_a_subdirectory_too(repo: Path, monkeypatch) -> Path:
    """Enroll `svc/api` in REPO by init, which writes its gate beside the root
    one as `.github/workflows/warden-svc-api.yml`. Returns that file."""
    for rel, text in FIXTURES["python"].items():
        (repo / "svc" / "api" / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / "svc" / "api" / rel).write_text(text)
    assert _warden(repo / "svc" / "api", monkeypatch, "init") == 0
    gate = repo / ".github" / "workflows" / "warden-svc-api.yml"
    assert "warden review" in gate.read_text() and "warden verify" in gate.read_text()
    return gate


def test_root_certify_credits_its_own_gate_beside_a_subdirectory_gate(
        tmp_path, monkeypatch, capsys):
    """Both gates init writes sit in the root's workflow directory, and
    `warden-svc-api.yml` sorts before `warden.yml`. The root credited the
    subdirectory's gate for G-04 and V-02."""
    from warden import certify as certify_mod
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    _enroll_a_subdirectory_too(repo, monkeypatch)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll")
    assert _warden(repo, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    code, report = _certify(repo, monkeypatch, capsys)
    assert code == 0 and "certification: LEVEL 3 (Reviewed)" in report, report
    for check in ("G-04", "V-02"):
        line = _check_line(report, check)
        assert "warden.yml" in line and "warden-svc-api.yml" not in line, report
    passed, detail = certify_mod._run_check(
        {"type": "ci_step_enforced", "pattern": "warden review"}, repo)
    assert passed and "warden-svc-api.yml" not in detail, detail


def test_root_certify_does_not_credit_a_subdirectory_gate_for_its_own(
        tmp_path, monkeypatch, capsys):
    """With the root's `warden.yml` deleted, the gate init wrote for `svc/api`
    was the only workflow left, and root certify reached LEVEL 3 on it."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    _enroll_a_subdirectory_too(repo, monkeypatch)
    (repo / enroll.WORKFLOW_PATH).unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll")
    assert _warden(repo, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    code, report = _certify(repo, monkeypatch, capsys)
    assert code != 0 and "LEVEL 3" not in report, report
    for check in ("G-04", "V-02"):
        assert "✗" in _check_line(report, check), report


@pytest.mark.parametrize("beside", [False, True], ids=["alone", "beside-svc-api"])
def test_root_certify_credits_a_hand_named_warden_ci_gate(beside, tmp_path, monkeypatch, capsys):
    """A root gate the user named `warden-ci.yml` is not the gate init writes
    for any enrollment below the root, so it certifies, with or without a
    tracked `svc/api` enrollment whose own gate is skipped."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    if beside:
        _enroll_a_subdirectory_too(repo, monkeypatch)
    root_gate = repo / enroll.WORKFLOW_PATH
    root_gate.rename(root_gate.with_name("warden-ci.yml"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll")
    assert _warden(repo, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    code, report = _certify(repo, monkeypatch, capsys)
    assert code == 0 and "certification: LEVEL 3 (Reviewed)" in report, report
    for check in ("G-04", "V-02"):
        line = _check_line(report, check)
        assert "warden-ci.yml" in line and "warden-svc-api.yml" not in line, report
    capsys.readouterr()
    assert _warden(repo, monkeypatch, "declare", "check") == 0
    assert "warden-ci.yml:gate" in _check_line(capsys.readouterr().out, "D-03")


def test_root_certify_names_the_subdirectory_gate_it_skipped_when_g04_fails(
        tmp_path, monkeypatch, capsys):
    """With `svc/api/repo.yaml` tracked, the root still skips
    `warden-svc-api.yml`, and with `warden.yml` deleted G-04 says why."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    _enroll_a_subdirectory_too(repo, monkeypatch)
    (repo / enroll.WORKFLOW_PATH).unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll")
    code, report = _certify(repo, monkeypatch, capsys)
    assert code != 0 and "LEVEL 3" not in report, report
    line = _check_line(report, "G-04")
    assert "✗" in line, report
    assert ("the root `warden-svc-api.yml` was skipped as the gate of the enrollment "
            "at `svc/api`") in line, report


def test_root_certify_credits_a_warden_gate_no_enrollment_is_named_for(
        tmp_path, monkeypatch, capsys):
    """The root's own gate named `warden-svc-api.yml`, with no `svc/api/repo.yaml`
    tracked or on disk, and the tracked `svc/web/repo.yaml` naming
    `warden-svc-web.yml`. The root skips only the gate init names for an
    enrollment below it, so this one certifies."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    root_gate = repo / enroll.WORKFLOW_PATH
    root_gate.rename(root_gate.with_name("warden-svc-api.yml"))
    (repo / "svc" / "api").mkdir(parents=True)
    (repo / "svc" / "api" / "notes.txt").write_text("not an enrollment\n")
    (repo / "svc" / "web").mkdir(parents=True)
    (repo / "svc" / "web" / "repo.yaml").write_text("# another enrollment\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll")
    assert _warden(repo, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    code, report = _certify(repo, monkeypatch, capsys)
    assert code == 0 and "certification: LEVEL 3 (Reviewed)" in report, report
    assert "warden-svc-api.yml" in _check_line(report, "G-04"), report


def _unrelated_enrollment(mono: Path, gate: Path) -> None:
    gate.rename(gate.with_name("warden-svc-web.yml"))


def _root_enrollments_gate(mono: Path, gate: Path) -> None:
    gate.rename(gate.with_name("warden.yml"))


@pytest.mark.parametrize("unrelated", [_unrelated_enrollment, _root_enrollments_gate],
                         ids=["named-for-another-enrollment", "the-root-enrollments-gate"])
def test_certify_below_the_git_root_ignores_a_root_workflow_not_named_for_it(
        unrelated, tmp_path, monkeypatch, capsys):
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    unrelated(mono, mono / ".github" / "workflows" / "warden-svc-api.yml")
    _git(mono, "add", "-A")
    _git(mono, "commit", "-qm", "enroll")
    assert _warden(api, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    code, report = _certify(api, monkeypatch, capsys)
    assert code != 0, report
    for check in ("G-04", "V-02"):
        assert "✗" in _check_line(report, check), report
    from warden import certify as certify_mod
    passed, detail = certify_mod._run_check(
        {"type": "ci_step_enforced", "pattern": "warden review"}, api)
    assert not passed, detail


def test_the_e_rungs_read_the_root_gate_workflow_below_the_git_root(tmp_path, monkeypatch):
    from warden import certify as certify_mod
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    passed, detail = certify_mod._run_check(
        {"type": "ci_step_enforced", "pattern": "warden review"}, api)
    assert passed and "warden-svc-api.yml" in detail, detail


def test_declare_check_reads_the_root_gate_clean_for_a_subdirectory_enrollment(
        tmp_path, monkeypatch, capsys):
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    _git(mono, "add", "-A")
    _git(mono, "commit", "-qm", "enroll")
    capsys.readouterr()
    code = _warden(api, monkeypatch, "declare", "check")
    out = capsys.readouterr().out
    assert code == 0, out
    d03 = _check_line(out, "D-03")
    assert "warden-svc-api.yml:gate" in d03 and "DRIFT" not in out and "SKIPPED" not in d03, out


def test_the_subdirectory_gate_runs_every_step_in_the_enrollment():
    """One job-level `defaults.run.working-directory` per job, so every `run:`
    step, the take step and `warden review` included, runs in the enrollment;
    the action inputs that name a path, which that key does not reach, carry
    the prefix themselves."""
    doc = _workflow(("python", "go"), "svc/api")
    assert doc["name"] == "warden (svc/api)"
    # The two install steps that must not need the enrollment directory run at
    # the workspace root: the private check runs before the checkout, and the
    # repo.yaml check is what finds the directory missing.
    at_the_workspace = {("install", PRIVATE), ("install", REPO_YAML)}
    for name, job in doc["jobs"].items():
        assert job["defaults"] == {"run": {"working-directory": "svc/api"}}, name
        assert job["name"].endswith(" (svc/api)"), name
        for step in job["steps"]:
            if (name, step.get("name")) in at_the_workspace:
                assert step["working-directory"] == ".", (name, step.get("name"))
            else:
                assert "working-directory" not in step, (name, step.get("name"))
    assert doc["jobs"]["gate"]["name"] == "warden gate (svc/api)"
    assert _uses(doc, "install", "actions/checkout")[0]["with"]["sparse-checkout"] == (
        "/svc/api/repo.yaml")
    assert _uses(doc, "verify", "actions/upload-artifact")[0]["with"]["path"] == (
        "svc/api/.warden/out/*-verify/verify-result.json")
    assert _uses(doc, "gate", "actions/upload-artifact")[0]["with"]["path"] == (
        "svc/api/.warden/out/")
    root = _workflow(("python", "go"))
    # The repo.yaml check names the enrollment's path; every other command is
    # the root gate's.
    assert [s.get("run") for job in root["jobs"] for s in _steps(root, job)
            if s.get("name") != REPO_YAML] == [
        s.get("run") for job in doc["jobs"] for s in _steps(doc, job)
        if s.get("name") != REPO_YAML], (
        "a subdirectory gate runs different commands from a root gate")


def test_review_below_the_git_root_scans_the_enrollments_added_lines(
        tmp_path, monkeypatch, capsys):
    """From `svc/api`, git lists changed paths from the git root, while a
    per-file diff run there reads its pathspec from `svc/api`. Every file came
    back with no added line, so a planted credential passed the subdirectory
    gate."""
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    _git(mono, "add", "-A")
    _git(mono, "commit", "-qm", "enroll")
    (api / "settings.py").write_text('AWS_ACCESS_KEY_ID = "AKIAQ3EGRAW7XK2PLMNB"\n')
    (mono / "top.txt").write_text("outside the enrollment\n")
    _git(mono, "add", "-A")
    _git(mono, "commit", "-qm", "leak")
    capsys.readouterr()
    assert _warden(api, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 1
    out = capsys.readouterr().out
    assert "secrets-in-diff" in out and "settings.py:1" in out, out
    assert "top.txt" not in out, out


# Names git C-quotes in a plain listing: a non-ASCII byte (quoted unless
# core.quotepath=false) and a `"` (quoted whatever core.quotepath says). The
# third is a directory named `:`, whose path a pathspec reads as `:/` magic,
# the top of the git tree, rather than as the file.
@pytest.mark.parametrize("where", ["root", "below"])
@pytest.mark.parametrize("name", ["café.py", 'q"x.py', ":/leak.py"],
                         ids=["non-ascii", "double-quote", "colon-dir"])
def test_review_blocks_a_planted_key_in_a_file_git_quotes_the_name_of(
        name, where, tmp_path, monkeypatch, capsys):
    """git C-quotes these names in a `--name-only` listing, and the quoted
    string, passed back as a pathspec, matched nothing: the file had no added
    line, and a planted AWS key in it passed review with exit 0."""
    if where == "root":
        enrolled = top = _repo(tmp_path, FIXTURES["python"])
    else:
        top = _mono(tmp_path)
        enrolled = top / "svc" / "api"
    assert _warden(enrolled, monkeypatch, "init") == 0
    _git(top, "add", "-A")
    _git(top, "commit", "-qm", "enroll")
    (enrolled / name).parent.mkdir(parents=True, exist_ok=True)
    (enrolled / name).write_text('AWS_ACCESS_KEY_ID = "AKIAQ3EGRAW7XK2PLMNB"\n')
    _git(top, "add", "-A")
    _git(top, "commit", "-qm", "leak")
    capsys.readouterr()
    assert _warden(enrolled, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 1
    out = capsys.readouterr().out
    assert "secrets-in-diff" in out and f"{name}:1" in out, out


def test_explain_below_the_git_root_tiers_the_enrollments_own_policy(
        tmp_path, monkeypatch, capsys):
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    _git(mono, "add", "-A")
    _git(mono, "commit", "-qm", "enroll")
    (api / "repo.yaml").write_text((api / "repo.yaml").read_text() + "# edited\n")
    (mono / "top.txt").write_text("outside the enrollment\n")
    _git(mono, "add", "-A")
    _git(mono, "commit", "-qm", "edit the policy")
    capsys.readouterr()
    assert _warden(api, monkeypatch, "explain", "--base", "HEAD~1") == 0
    out = capsys.readouterr().out
    assert "- HIGH: `repo.yaml`" in out and "top.txt" not in out, out


@pytest.mark.parametrize("below", ["svc/api v2", "svc/[x]", "svc/#x", "svc/!x"])
def test_init_refuses_a_directory_name_a_sparse_checkout_pattern_would_misread(
        below, tmp_path, monkeypatch, capsys):
    mono = _mono(tmp_path, below)
    before = _snapshot(mono)
    assert _warden(mono / below, monkeypatch, "init") == 2
    err = capsys.readouterr().err
    assert "letters, digits" in err and "Nothing was written" in err, err
    assert _snapshot(mono) == before


# ── repo.yaml platform.pin is the one version source ────────────────────────

@pytest.mark.parametrize("below", ["", "svc/api"], ids=["root", "subdirectory"])
def test_repo_yaml_platform_pin_is_the_one_version_source_the_install_job_clones(
        below, tmp_path, monkeypatch, capsys):
    semver = re.compile(r"\bv?\d+\.\d+\.\d+\b")
    # A version-shaped token may sit only in an action's pin comment or in
    # setup-uv's own version: tool versions, not the platform's.
    tool_version_line = re.compile(
        r'^\s*(?:- )?uses: [\w./-]+@[0-9a-f]{40}  # v\d+\.\d+\.\d+$|^\s*version: "\d+\.\d+\.\d+"$')
    monkeypatch.setattr(enroll, "__version__", "7.13.29")
    mono = _mono(tmp_path, below) if below else _repo(tmp_path, FIXTURES["python"])
    where = mono / below if below else mono
    assert _warden(where, monkeypatch, "init") == 0
    written = [where / line.removeprefix("  wrote ")
               for line in capsys.readouterr().out.splitlines() if line.startswith("  wrote ")]
    holders = {p.name: p.read_text().count("7.13.29") for p in written
               if "7.13.29" in p.read_text()}
    assert holders == {"repo.yaml": 1}, holders
    assert "  pin: v7.13.29\n" in (where / "repo.yaml").read_text()

    workflow = next(p for p in written if p.suffix == ".yml")
    # `v1.2.3` is the example in the build step's refusal message, not a tag.
    others = [line for line in workflow.read_text().splitlines()
              if semver.search(line.replace("like v1.2.3 written", "")
                               .replace(f"of {enroll.DEPLOY_KEY_ALIAS_ENDS}", "")
                               .replace(f"from {enroll.DEPLOY_KEY_ALIAS_ENDS}", ""))
              and not tool_version_line.match(line)]
    assert others == [], "a platform version written into the workflow: " + repr(others)

    doc = yaml.safe_load(workflow.read_text())
    cwd = mono / ((doc["jobs"]["install"].get("defaults") or {}).get("run") or {}).get(
        "working-directory", ".")
    assert cwd.resolve() == where.resolve()
    result, log, _ = _build(cwd, tmp_path, doc=doc)
    assert result.returncode == 0, result
    assert "--branch v7.13.29 git@github.com:NightWatchEng/nightgate.git" in log, log

    repo_yaml = where / "repo.yaml"
    repo_yaml.write_text(repo_yaml.read_text().replace("pin: v7.13.29", "pin: v9.8.7"))
    shutil.rmtree(tmp_path / "runner")
    (tmp_path / "fake.log").unlink()
    (tmp_path / "github_output").unlink()
    result, log, _ = _build(cwd, tmp_path, doc=doc)
    assert result.returncode == 0, result
    assert "--branch v9.8.7 git@github.com:NightWatchEng/nightgate.git" in log, log


def test_every_doc_adds_the_skill_pack_marketplace_by_its_github_url():
    """...at the tag `platform.pin` names: the `#ref` on
    the URL is the one place the installer lets a version be spelled."""
    from warden import __version__, packpin
    command = re.compile(r"(?:claude plugin|/plugin) marketplace add\s+'?([^'\s]+)'?")
    pages = [ROOT / "README.md", *sorted(WIKI.glob("*.md")),
             *sorted((ROOT / "skills").rglob("*.md"))]
    found = {page.relative_to(ROOT).as_posix(): command.findall(page.read_text())
             for page in pages}
    sources = {page: args for page, args in found.items() if args}
    assert sources.get("docs/wiki/Installation.md"), "Installation.md adds no marketplace"
    wrong = {page: args for page, args in sources.items()
             if set(args) != {f"{packpin.MARKETPLACE_URL}#v{__version__}"}}
    assert wrong == {}, wrong


def test_review_with_no_project_checkers_imports_no_checker_module(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    tripwire = tmp_path / "imported"
    (repo / ".warden" / "checkers").mkdir()
    (repo / ".warden" / "checkers" / "trip.py").write_text(
        f"open({str(tripwire)!r}, 'w').close()\n"
        "CHECKERS = {'trip': lambda ctx, params, files: []}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll with a checker")

    capsys.readouterr()
    assert _warden(repo, monkeypatch, "review", "--base", "HEAD~1", "--no-comment",
                   "--no-project-checkers") == 2
    err = capsys.readouterr().err
    assert "DID NOT RUN" in err and "trip.py" in err, err
    assert not tripwire.exists()

    assert _warden(repo, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    assert tripwire.exists(), "the control: without the flag the module is imported"


# ── verify defaults run on the fixtures ─────────────────────────────────────

def _enrolled(tmp_path: Path, monkeypatch, files: dict[str, str]) -> tuple[Path, str]:
    repo = _repo(tmp_path, files)
    assert _warden(repo, monkeypatch, "init") == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll")
    return repo, config_mod.load(repo)


def _verify(repo: Path, monkeypatch, scope: str) -> tuple[int, dict]:
    # These tests RUN the verify scope `warden init` wrote, so
    # this is where the suite shells out to `go`, `npm` and `uv`. The check
    # lives in the shared helper rather than at each call site so it cannot be
    # forgotten: a fourth language added to `enroll.LANGUAGES` arrives here
    # with its toolchain already named. The requirement is DERIVED from the
    # enrolled repo's own repo.yaml — never a retyped list, which would be a
    # second source of truth for the same fact and would drift.
    #
    # `toolchain.require` decides skip-vs-fail; see tests/toolchain.py for why
    # a laptop gets a named skip and CI gets a named failure. Either way the
    # old behaviour — five cells reading `assert 1 == 0` over a buried
    # `/bin/sh: npm: command not found` — is gone.
    steps = config_mod.load(repo).verify.get(scope, ())
    needed = [b for b in (verify_mod.first_binary(s.run) for s in steps) if b]
    toolchain.require(*dict.fromkeys(needed))
    code = _warden(repo, monkeypatch, "verify", "--scope", scope)
    results = sorted((repo / ".warden" / "out").glob("*-verify/verify-result.json"))
    assert results, "verify wrote no result"
    return code, json.loads(results[-1].read_text())


@pytest.mark.parametrize("lang", sorted(FIXTURES))
def test_each_fixtures_verify_scope_passes_and_leaves_the_tree_clean(
        lang, tmp_path, monkeypatch):
    repo, _ = _enrolled(tmp_path, monkeypatch, FIXTURES[lang])
    code, result = _verify(repo, monkeypatch, lang)
    assert code == 0, result
    assert result["dirty"] is False, _git(repo, "status", "--porcelain")
    assert _git(repo, "status", "--porcelain") == ""


def test_a_python_test_that_imports_the_code_verifies_with_no_lock_or_bytecode_left(
        tmp_path, monkeypatch):
    files = {**FIXTURES["python"],
             "test_demo.py": "from demo import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"}
    repo, _ = _enrolled(tmp_path, monkeypatch, files)
    code, result = _verify(repo, monkeypatch, "python")
    assert code == 0 and result["dirty"] is False, _git(repo, "status", "--porcelain")


_SETUPTOOLS = ('[project]\nname = "demo"\nversion = "0.1.0"\n'
               '[build-system]\nrequires = ["setuptools>=64"]\n'
               'build-backend = "setuptools.build_meta"\n')


@pytest.mark.parametrize("layout", ["src", "flat"])
def test_a_setuptools_project_verifies_with_its_egg_info_ignored(layout, tmp_path, monkeypatch):
    package = "src/demo" if layout == "src" else "demo"
    pyproject = _SETUPTOOLS + ("" if layout == "src"
                               else '[tool.setuptools]\npackages = ["demo"]\n')
    files = {"pyproject.toml": pyproject,
             f"{package}/__init__.py": "def add(a, b):\n    return a + b\n",
             "test_demo.py": "from demo import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"}
    repo, _ = _enrolled(tmp_path, monkeypatch, files)
    code, result = _verify(repo, monkeypatch, "python")
    assert code == 0, result
    egg_info = repo / ("src" if layout == "src" else ".") / "demo.egg-info"
    assert egg_info.is_dir(), "the control: setuptools wrote its egg-info"
    assert result["dirty"] is False, _git(repo, "status", "--porcelain")
    assert _git(repo, "status", "--porcelain") == ""


def test_a_node_dependency_install_verifies_with_node_modules_ignored(tmp_path, monkeypatch):
    files = {"package.json": json.dumps({"name": "demo", "version": "0.1.0",
                                         "dependencies": {"dep": "file:./dep"},
                                         "scripts": {"test": "node -e 0"}}) + "\n",
             "dep/package.json": '{"name": "dep", "version": "1.0.0"}\n',
             "index.js": "exports.add = (a, b) => a + b;\n"}
    repo, _ = _enrolled(tmp_path, monkeypatch, files)
    code, result = _verify(repo, monkeypatch, "node")
    assert code == 0, result
    assert (repo / "node_modules").is_dir(), "the control: npm installed the dependency"
    assert result["dirty"] is False, _git(repo, "status", "--porcelain")
    assert _git(repo, "status", "--porcelain") == ""


def test_a_failing_python_test_still_fails_the_python_scope(tmp_path, monkeypatch):
    files = {**FIXTURES["python"], "test_demo.py": "def test_add():\n    assert 1 == 2\n"}
    repo, _ = _enrolled(tmp_path, monkeypatch, files)
    code, _ = _verify(repo, monkeypatch, "python")
    assert code != 0


def test_a_python_repo_with_a_uv_lock_verifies_against_the_lock(tmp_path, monkeypatch):
    repo = _repo(tmp_path, FIXTURES["python"])
    subprocess.run(["uv", "lock", "-q"], cwd=repo, check=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "lock")
    assert _warden(repo, monkeypatch, "init") == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll")
    (step,) = config_mod.load(repo).verify["python"]
    assert "--locked" in step.run and "--isolated" not in step.run
    code, result = _verify(repo, monkeypatch, "python")
    assert code == 0 and result["dirty"] is False


@pytest.mark.parametrize("scripts", [None, {"test": 'echo "Error: no test specified" && exit 1'}])
def test_a_node_repo_with_no_test_script_gets_no_test_step_and_a_warning(
        scripts, tmp_path, monkeypatch, capsys):
    manifest = {"name": "demo", "version": "0.1.0"}
    if scripts:
        manifest["scripts"] = scripts
    files = {**FIXTURES["node"], "package.json": json.dumps(manifest) + "\n"}
    repo, config = _enrolled(tmp_path, monkeypatch, files)
    assert "npm test" not in [s.run for s in config.verify["node"]]
    out = capsys.readouterr().out
    assert "no test script" in out and "npm test" in out, out
    code, result = _verify(repo, monkeypatch, "node")
    assert code == 0 and result["dirty"] is False


def test_init_warns_when_it_finds_no_python_or_go_test(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, {**FIXTURES["python"], **FIXTURES["go"]})
    assert _warden(repo, monkeypatch, "init") == 0
    out = capsys.readouterr().out
    assert "no python test" in out and "no go test" in out, out


def test_init_does_not_warn_about_tests_a_repo_has(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, {**FIXTURES["python"], "tests/test_demo.py": "def test_x():\n    pass\n",
                            **FIXTURES["go"], "demo_test.go": "package demo\n"})
    assert _warden(repo, monkeypatch, "init") == 0
    out = capsys.readouterr().out
    assert "no python test" not in out and "no go test" not in out, out


# ── a consumer repository must be private ───────────────────────────────────

def test_init_next_steps_say_the_repository_must_be_private(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES["go"])
    assert _warden(repo, monkeypatch, "init") == 0
    steps = capsys.readouterr().out.split("Next steps:", 1)[1]
    assert "must be private" in steps and "wheel" in steps, steps


def test_installation_says_a_consumer_repository_must_be_private():
    body = " ".join(_section("Installation.md", "CI access to the platform").split())
    assert "consumer repository must be private" in body, body
    assert "one-day wheel artifact" in body


@pytest.mark.parametrize("private, code", [("true", 0), ("false", 2), ("", 2)])
def test_the_install_job_refuses_a_repository_that_is_not_private(private, code, tmp_path):
    doc = _workflow()
    step = _step(doc, "install", PRIVATE)
    assert step["env"] == {"REPOSITORY_PRIVATE": "${{ github.event.repository.private }}"}
    assert "${{" not in step["run"]
    result = _run_step(doc, "install", step, {"REPOSITORY_PRIVATE": private}, tmp_path)
    assert result.returncode == code, result
    if code:
        assert "not private" in result.stderr and "DID NOT RUN" in result.stderr, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_installation_says_the_gate_checks_the_repository_is_private():
    body = " ".join(_section("Installation.md", "CI access to the platform").split())
    assert "install job refuses" in body and "not private" in body, body


# ── warden take ─────────────────────────────────────────────────────────────

def _result(evidence: Path, rel: str, body: str = '{"scope": "python"}\n') -> Path:
    path = evidence / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _take_cli(checkout: Path, source: Path, monkeypatch, capsys) -> tuple[int, str, str]:
    checkout.mkdir(exist_ok=True)
    capsys.readouterr()
    code = _warden(checkout, monkeypatch, "take", "--from", str(source))
    out = capsys.readouterr()
    return code, out.out, out.err


def _taken(checkout: Path) -> list[str]:
    out = checkout / ".warden" / "out"
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*"))


def test_warden_take_copies_verify_results_at_depth_one_and_two(tmp_path, monkeypatch, capsys):
    evidence = tmp_path / "warden-verify"
    _result(evidence, "a-verify/verify-result.json", "A\n")
    _result(evidence, "nested/b-verify/verify-result.json", "B\n")
    checkout = tmp_path / "checkout"
    code, out, err = _take_cli(checkout, evidence, monkeypatch, capsys)
    assert code == 0, err
    assert _taken(checkout) == ["a-verify", "a-verify/verify-result.json",
                                "b-verify", "b-verify/verify-result.json"]
    assert (checkout / ".warden/out/a-verify/verify-result.json").read_text() == "A\n"
    assert (checkout / ".warden/out/b-verify/verify-result.json").read_text() == "B\n"
    assert len(out.splitlines()) == 2 and "a-verify" in out and "b-verify" in out, out


def test_warden_take_skips_a_symlinked_result_file(tmp_path, monkeypatch, capsys):
    evidence = tmp_path / "warden-verify"
    outside = _result(tmp_path, "runner-file", "not evidence\n")
    (evidence / "a-verify").mkdir(parents=True)
    (evidence / "a-verify" / "verify-result.json").symlink_to(outside)
    _result(evidence, "b-verify/verify-result.json")
    checkout = tmp_path / "checkout"
    code, _, err = _take_cli(checkout, evidence, monkeypatch, capsys)
    assert code == 0, err
    assert _taken(checkout) == ["b-verify", "b-verify/verify-result.json"]


def test_warden_take_skips_a_result_that_is_not_a_regular_file(tmp_path, monkeypatch, capsys):
    evidence = tmp_path / "warden-verify"
    (evidence / "a-verify").mkdir(parents=True)
    os.mkfifo(evidence / "a-verify" / "verify-result.json")
    _result(evidence, "b-verify/verify-result.json")
    checkout = tmp_path / "checkout"
    code, _, err = _take_cli(checkout, evidence, monkeypatch, capsys)
    assert code == 0, err
    assert _taken(checkout) == ["b-verify", "b-verify/verify-result.json"]


def test_warden_take_skips_a_result_whose_parent_does_not_end_in_verify(
        tmp_path, monkeypatch, capsys):
    evidence = tmp_path / "warden-verify"
    _result(evidence, "3-review/verify-result.json")
    _result(evidence, "verify-result.json")
    _result(evidence, "x-verify/review-findings.json")
    _result(evidence, "b-verify/verify-result.json")
    checkout = tmp_path / "checkout"
    code, _, err = _take_cli(checkout, evidence, monkeypatch, capsys)
    assert code == 0, err
    assert _taken(checkout) == ["b-verify", "b-verify/verify-result.json"]


def test_warden_take_does_not_follow_a_symlinked_subdirectory(tmp_path, monkeypatch, capsys):
    evidence = tmp_path / "warden-verify"
    _result(tmp_path, "outside/c-verify/verify-result.json")
    evidence.mkdir()
    (evidence / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    (evidence / "d-verify").symlink_to(tmp_path / "outside" / "c-verify",
                                       target_is_directory=True)
    _result(evidence, "b-verify/verify-result.json")
    checkout = tmp_path / "checkout"
    code, _, err = _take_cli(checkout, evidence, monkeypatch, capsys)
    assert code == 0, err
    assert _taken(checkout) == ["b-verify", "b-verify/verify-result.json"]


def test_warden_take_replaces_a_committed_out_directory(tmp_path, monkeypatch, capsys):
    evidence = tmp_path / "warden-verify"
    _result(evidence, "b-verify/verify-result.json", "TAKEN\n")
    checkout = tmp_path / "checkout"
    _result(checkout, ".warden/out/planted.txt", "planted\n")
    _result(checkout, ".warden/out/b-verify/verify-result.json", "PLANTED\n")
    _result(checkout, ".warden/out/z-verify/verify-result.json", "PLANTED\n")
    code, _, err = _take_cli(checkout, evidence, monkeypatch, capsys)
    assert code == 0, err
    assert _taken(checkout) == ["b-verify", "b-verify/verify-result.json"]
    assert (checkout / ".warden/out/b-verify/verify-result.json").read_text() == "TAKEN\n"


@pytest.mark.parametrize("planted", ["symlink", "file"])
def test_warden_take_unlinks_a_committed_out_symlink_or_file_without_touching_a_target(
        planted, tmp_path, monkeypatch, capsys):
    evidence = tmp_path / "warden-verify"
    _result(evidence, "b-verify/verify-result.json")
    target = tmp_path / "target"
    kept = _result(target, "x-verify/verify-result.json", "the target's own bytes\n")
    checkout = tmp_path / "checkout"
    (checkout / ".warden").mkdir(parents=True)
    if planted == "symlink":
        (checkout / ".warden" / "out").symlink_to(target, target_is_directory=True)
    else:
        (checkout / ".warden" / "out").write_text("a file\n")
    code, _, err = _take_cli(checkout, evidence, monkeypatch, capsys)
    assert code == 0, err
    out = checkout / ".warden" / "out"
    assert out.is_dir() and not out.is_symlink()
    assert _taken(checkout) == ["b-verify", "b-verify/verify-result.json"]
    assert kept.read_text() == "the target's own bytes\n"
    assert sorted(p.name for p in target.iterdir()) == ["x-verify"]


@pytest.mark.parametrize("planted", ["symlink", "file"])
def test_warden_take_refuses_a_warden_dir_that_is_a_symlink_or_a_file(
        planted, tmp_path, monkeypatch, capsys):
    evidence = tmp_path / "warden-verify"
    _result(evidence, "b-verify/verify-result.json")
    aliased = tmp_path / "aliased"
    aliased.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    if planted == "symlink":
        (checkout / ".warden").symlink_to(aliased, target_is_directory=True)
    else:
        (checkout / ".warden").write_text("a file\n")
    code, _, err = _take_cli(checkout, evidence, monkeypatch, capsys)
    assert code == 2, err
    assert "warden take: .warden" in err and "DID NOT RUN" in err, err
    assert list(aliased.iterdir()) == []


@pytest.mark.parametrize("source, reason", [("missing", "not a directory"),
                                            ("file", "not a directory"),
                                            ("symlink", "symlink")],
                         ids=["missing", "file", "symlink"])
def test_warden_take_refuses_a_source_that_is_missing_a_file_or_a_symlink(
        source, reason, tmp_path, monkeypatch, capsys):
    real = tmp_path / "real-evidence"
    _result(real, "b-verify/verify-result.json")
    given = tmp_path / "warden-verify"
    if source == "file":
        given.write_text("not a directory\n")
    elif source == "symlink":
        given.symlink_to(real, target_is_directory=True)
    checkout = tmp_path / "checkout"
    code, _, err = _take_cli(checkout, given, monkeypatch, capsys)
    assert code == 2, err
    assert reason in err and "DID NOT RUN" in err, err
    assert _taken(checkout) == [], "nothing is taken from a refused source"


def test_warden_take_exits_2_with_the_message_when_nothing_is_taken(
        tmp_path, monkeypatch, capsys):
    evidence = tmp_path / "warden-verify"
    _result(evidence, "3-review/verify-result.json")
    checkout = tmp_path / "checkout"
    code, _, err = _take_cli(checkout, evidence, monkeypatch, capsys)
    assert code == 2
    assert ("warden take: the verify job uploaded no verify result; "
            "the gate DID NOT RUN.") in err, err


@pytest.mark.parametrize("order", ["filesystem", "reversed"])
def test_warden_take_the_later_result_in_sorted_walk_order_wins(
        order, tmp_path, monkeypatch, capsys):
    """take.py's docstring: two results under one parent name, the later in
    sorted walk order wins. `z/a-verify` sorts after `a-verify`. `reversed`
    hands take each directory's entries in reverse, as a filesystem may, so
    only take's own sort puts them back in order."""
    evidence = tmp_path / "warden-verify"
    _result(evidence, "a-verify/verify-result.json", "TOP\n")
    _result(evidence, "z/a-verify/verify-result.json", "NESTED\n")
    if order == "reversed":
        real_walk = os.walk

        def reversed_walk(top, *args, **kwargs):
            for dirpath, dirnames, filenames in real_walk(top, *args, **kwargs):
                dirnames.reverse()
                yield dirpath, dirnames, filenames
        monkeypatch.setattr(os, "walk", reversed_walk)
    checkout = tmp_path / "checkout"
    code, out, err = _take_cli(checkout, evidence, monkeypatch, capsys)
    assert code == 0, err
    assert (checkout / ".warden/out/a-verify/verify-result.json").read_text() == "NESTED\n"
    assert len(out.splitlines()) == 2, out
    assert out.splitlines()[-1].endswith(str(evidence / "z" / "a-verify")), out


_ROOT_READS_ANYTHING = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads a chmod 000 path, so nothing is denied")


@_ROOT_READS_ANYTHING
@pytest.mark.parametrize("denied", ["source", "verify-subdirectory", "result-file"])
def test_warden_take_refuses_a_path_it_cannot_read_and_names_it(
        denied, tmp_path, monkeypatch, capsys):
    evidence = tmp_path / "warden-verify"
    _result(evidence, "a-verify/verify-result.json")
    _result(evidence, "b-verify/verify-result.json")
    target = {"source": evidence, "verify-subdirectory": evidence / "b-verify",
              "result-file": evidence / "b-verify" / "verify-result.json"}[denied]
    target.chmod(0)
    try:
        checkout = tmp_path / "checkout"
        code, _, err = _take_cli(checkout, evidence, monkeypatch, capsys)
    finally:
        target.chmod(0o755)
    assert code == 2, err
    assert f"could not read {target}" in err and "Permission denied" in err, err
    assert "DID NOT RUN" in err and "uploaded no verify result" not in err, err


# ── D-03 reads the take step by its exact form ──────────────────────────────

_OLD_TAKE_RUN = """\
if [ -L .warden ] || { [ -e .warden ] && [ ! -d .warden ]; }; then
  echo "warden gate: .warden is a symlink or not a directory; the gate DID NOT RUN." >&2
  exit 2
fi
mkdir -p .warden
rm -rf -- .warden/out
mkdir .warden/out
taken=0
while IFS= read -r -d '' result; do
  run="$(basename "$(dirname "$result")")"
  case "$run" in
    *-verify) ;;
    *) continue ;;
  esac
  mkdir -p ".warden/out/$run"
  cp -- "$result" ".warden/out/$run/verify-result.json"
  taken=$((taken + 1))
done < <(find "$RUNNER_TEMP/warden-verify" -type f -name verify-result.json -print0)
if [ "$taken" -eq 0 ]; then
  echo "warden gate: the verify job uploaded no verify result; the gate DID NOT RUN." >&2
  exit 2
fi
"""


def _d03_edited(tmp_path: Path, monkeypatch, edit) -> declared_mod.CheckResult:
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    path = repo / enroll.WORKFLOW_PATH
    doc = yaml.safe_load(path.read_text())
    edit(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return declared_mod.check_scope_runners(repo, config_mod.load(repo))


def _take_run(run: str, download: str | None = None):
    def edit(doc: dict) -> None:
        _step(doc, "gate", TAKE)["run"] = run
        if download is not None:
            _uses(doc, "gate", "actions/download-artifact")[1]["with"]["path"] = download
    return edit


def _take_key(key: str, value):
    def edit(doc: dict) -> None:
        _step(doc, "gate", TAKE)[key] = value
    return edit


def _review_key(key: str, value):
    def edit(doc: dict) -> None:
        _step(doc, "gate", TAKE)["run"] = TAKE_RUN
        _step(doc, "gate", "warden review")[key] = value
    return edit


def _both_key(key: str, value):
    def edit(doc: dict) -> None:
        _step(doc, "gate", TAKE)[key] = value
        _step(doc, "gate", "warden review")[key] = value
    return edit


def _defaults(where: str, run: dict):
    def edit(doc: dict) -> None:
        take = _step(doc, "gate", TAKE)
        take["run"] = TAKE_RUN
        take.pop("shell")
        holder = doc if where == "workflow" else doc["jobs"]["gate"]
        holder["defaults"] = {"run": run}
    return edit


def _wd_default(where: str, directory: str):
    def edit(doc: dict) -> None:
        holder = doc if where == "workflow" else doc["jobs"]["gate"]
        holder["defaults"] = {"run": {"working-directory": directory}}
    return edit


def _env(where: str, env):
    def edit(doc: dict) -> None:
        _step(doc, "gate", TAKE)["run"] = TAKE_RUN
        (doc if where == "workflow" else doc["jobs"]["gate"])["env"] = env
    return edit


def _review_before_take(spelling: str):
    def edit(doc: dict) -> None:
        steps = doc["jobs"]["gate"]["steps"]
        _step(doc, "gate", TAKE)["run"] = TAKE_RUN
        _step(doc, "gate", "warden review")["run"] = "warden review --help"
        steps.insert(steps.index(_step(doc, "gate", TAKE)),
                     {"name": "early review", "shell": "bash", "run": spelling})
    return edit


def _move_take(after: str):
    def edit(doc: dict) -> None:
        steps = doc["jobs"]["gate"]["steps"]
        take = _step(doc, "gate", TAKE)
        take["run"] = TAKE_RUN
        steps.remove(take)
        if after == "review":
            steps.insert(steps.index(_step(doc, "gate", "warden review")) + 1, take)
        else:
            steps.insert(steps.index(_uses(doc, "gate", "actions/download-artifact")[1]), take)
    return edit


def _download_with(key: str, value):
    def edit(doc: dict) -> None:
        _uses(doc, "gate", "actions/download-artifact")[1]["with"][key] = value
    return edit


def _add_download(doc: dict, path: str, name: str | None = None) -> None:
    """Another download-artifact step, after the take's download and before
    the take."""
    steps = doc["jobs"]["gate"]["steps"]
    take_download = _uses(doc, "gate", "actions/download-artifact")[1]
    spec = {"path": path} if name is None else {"name": name, "path": path}
    steps.insert(steps.index(take_download) + 1,
                 {"uses": take_download["uses"], "with": spec})


_T = "warden-verify"


@pytest.mark.parametrize("edit", [
    pytest.param(_take_run(TAKE_RUN), id="generated"),
    pytest.param(_take_run(TAKE_RUN + "\n"), id="block-scalar-newline"),
    pytest.param(_take_run(f"warden take --from $RUNNER_TEMP/{_T}"), id="unquoted-env"),
    pytest.param(_take_run(f'warden take --from "${{RUNNER_TEMP}}/{_T}"'), id="quoted-braced-env"),
    pytest.param(_take_run(f"warden take --from ${{RUNNER_TEMP}}/{_T}"), id="unquoted-braced-env"),
    pytest.param(_take_run(f'warden take --from "${{{{ runner.temp }}}}/{_T}"'),
                 id="quoted-expression"),
    pytest.param(_take_run(f"warden take --from ${{{{runner.temp}}}}/{_T}"),
                 id="unquoted-expression-no-spaces"),
    pytest.param(_take_run(TAKE_RUN, download=f"${{{{runner.temp}}}}/{_T}"),
                 id="download-expression-no-spaces"),
    pytest.param(_take_run(f'warden take --from "$RUNNER_TEMP/a.b/{_T}"',
                           download=f"${{{{ runner.temp }}}}/a.b/{_T}"), id="nested-path"),
    pytest.param(_take_key("id", "take"), id="step-id"),
    pytest.param(lambda doc: _add_download(doc, name=_T, path="${{ runner.temp }}/elsewhere"),
                 id="second-download-of-the-name-elsewhere"),
    pytest.param(lambda doc: _step(doc, "gate", "warden review").update(
        run=f"/usr/local/bin/{_step(doc, 'gate', 'warden review')['run']}"),
                 id="review-invoked-by-path"),
])
def test_d03_reads_the_exact_take_form_as_a_take(edit, tmp_path, monkeypatch):
    def with_generated_workflow(doc: dict) -> None:
        _step(doc, "gate", TAKE)["run"] = TAKE_RUN
        edit(doc)
    result = _d03_edited(tmp_path, monkeypatch, with_generated_workflow)
    assert result.status == declared_mod.CLEAN, result


@pytest.mark.parametrize("edit", [
    pytest.param(_take_run(_OLD_TAKE_RUN), id="old-multi-line-shell-take"),
    pytest.param(_take_run(TAKE_RUN + "; cp -r /evil .warden/out"), id="semicolon-then-cp"),
    pytest.param(_take_run(TAKE_RUN + " && cp -r /evil .warden/out"), id="and-then-cp"),
    pytest.param(_take_run(TAKE_RUN + " & cp -r /evil .warden/out"), id="background-then-cp"),
    pytest.param(_take_run(TAKE_RUN + "\ncp -r /evil .warden/out"), id="second-line"),
    pytest.param(_take_run("cd /tmp\n" + TAKE_RUN), id="line-before"),
    pytest.param(_take_run(TAKE_RUN + " # comment"), id="trailing-comment"),
    pytest.param(_take_run(f'warden take --from "$RUNNER_TEMP/{_T}&&cd /tmp"'), id="and-in-path"),
    pytest.param(_take_run(f'warden take --from "$RUNNER_TEMP/{_T}|tee"'), id="pipe-in-path"),
    pytest.param(_take_run('warden take --from "$RUNNER_TEMP/$(echo warden-verify)"'),
                 id="command-substitution-in-path"),
    pytest.param(_take_run('warden take --from "$RUNNER_TEMP/`echo warden-verify`"'),
                 id="backtick-in-path"),
    pytest.param(_take_run('warden take --from "$RUNNER_TEMP/elsewhere"'),
                 id="path-differs-from-download"),
    pytest.param(_take_run('warden take --from "$RUNNER_TEMP/x/../warden-verify"',
                           download="${{ runner.temp }}/x/../warden-verify"), id="dotdot-in-path"),
    pytest.param(_take_run('warden take --from "$RUNNER_TEMP/warden-*"',
                           download="${{ runner.temp }}/warden-*"), id="glob-in-path"),
    pytest.param(_take_run('warden take --from "$RUNNER_TEMP/warden-verif?"',
                           download="${{ runner.temp }}/warden-verif?"), id="question-glob-in-path"),
    pytest.param(_take_run(f'warden take --from "$RUNNER_TEMP/{_T}/"'), id="trailing-slash"),
    pytest.param(_take_run(f'warden take --from "$HOME/{_T}"', download=f"$HOME/{_T}"),
                 id="root-is-not-runner-temp"),
    pytest.param(_take_run(TAKE_RUN, download=f"$RUNNER_TEMP/{_T}"),
                 id="download-path-is-not-expanded-by-actions"),
    pytest.param(_take_run(f'warden take --from "$RUNNER_TEMP/{_T}'), id="unclosed-quote"),
    pytest.param(_take_run(f"warden take --from '$RUNNER_TEMP/{_T}'"), id="single-quotes"),
    pytest.param(_take_run(f"warden take --from \"$RUNNER_TEMP/{_T}'"), id="mismatched-quotes"),
    pytest.param(_take_run(f'warden  take --from "$RUNNER_TEMP/{_T}"'), id="double-space"),
    pytest.param(_take_run(f'warden take --from="$RUNNER_TEMP/{_T}"'), id="from-equals"),
    pytest.param(_take_run(TAKE_RUN + " --from /evil"), id="second-from"),
    pytest.param(_take_run("RUNNER_TEMP=/evil " + TAKE_RUN), id="prefix-assignment"),
    pytest.param(_take_run("warden explain"), id="not-a-take"),
    pytest.param(_take_key("working-directory", "sub"), id="working-directory-on-take-only"),
    pytest.param(_review_key("working-directory", "sub"), id="working-directory-on-review-only"),
    pytest.param(_both_key("working-directory", "sub"), id="working-directory-on-take-and-review"),
    pytest.param(_take_key("env", {"X": "1"}), id="env-on-take"),
    pytest.param(_take_key("if", "false"), id="if-on-take"),
    pytest.param(_take_key("shell", "sh"), id="shell-sh"),
    pytest.param(_take_key("shell", "pwsh"), id="shell-pwsh"),
    pytest.param(_take_key("shell", "bash -O extglob {0}"), id="shell-bash-with-options"),
    pytest.param(_take_key("continue-on-error", True), id="continue-on-error"),
    pytest.param(lambda doc: _step(doc, "gate", TAKE).pop("shell"), id="shell-absent"),
    pytest.param(_defaults("job", {"shell": "bash"}), id="shell-absent-job-default-bash"),
    pytest.param(_defaults("job", {"shell": "sh"}), id="shell-absent-job-default-sh"),
    pytest.param(_download_with("run-id", "123"), id="download-run-id"),
    pytest.param(_download_with("repository", "someone/else"), id="download-repository"),
    pytest.param(_download_with("github-token", "x"), id="download-github-token"),
    pytest.param(lambda doc: _add_download(doc, path=f"${{{{ runner.temp }}}}/{_T}"),
                 id="nameless-download-to-the-take-path"),
    pytest.param(lambda doc: _add_download(doc, path="${{ runner.temp }}"),
                 id="nameless-download-above-the-take-path"),
    pytest.param(lambda doc: _add_download(doc, name="other", path=f"${{{{ runner.temp }}}}/{_T}/x"),
                 id="other-download-below-the-take-path"),
    pytest.param(lambda doc: _add_download(doc, name="other", path="$RUNNER_TEMP/warden-verify"),
                 id="other-download-path-not-read"),
    pytest.param(_review_before_take('/usr/local/bin/warden review --event "$GITHUB_EVENT_PATH"'),
                 id="review-invoked-by-path-before-take"),
    pytest.param(_defaults("workflow", {"shell": "pwsh"}), id="shell-absent-workflow-default-pwsh"),
    pytest.param(_env("job", {"RUNNER_TEMP": "/evil"}), id="job-env-sets-runner-temp"),
    pytest.param(_env("workflow", {"RUNNER_TEMP": "/evil"}), id="workflow-env-sets-runner-temp"),
    pytest.param(_env("job", "${{ fromJSON(inputs.env) }}"), id="job-env-is-an-expression"),
    pytest.param(_env("job", {"BASH_FUNC_warden%%": "() { :; }"}), id="job-env-exports-a-function"),
    pytest.param(_env("workflow", {"BASH_ENV": "./x.sh"}), id="workflow-env-sets-bash-env"),
    pytest.param(_env("job", {"GITHUB_TOKEN": "x"}), id="job-env-any-key"),
    # The install job stays at the root: a workflow whose jobs all run below
    # the root is another enrollment's, which D-03 does not read at all.
    pytest.param(lambda doc: (_wd_default("workflow", "sub")(doc), doc["jobs"]["install"].update(
        defaults={"run": {"working-directory": "."}})),
                 id="workflow-default-working-directory-upload-outside-it"),
    pytest.param(_review_before_take('warden  review --event "$GITHUB_EVENT_PATH"'),
                 id="review-with-two-spaces-before-take"),
    pytest.param(_review_before_take('warden\treview --event "$GITHUB_EVENT_PATH"'),
                 id="review-with-a-tab-before-take"),
    pytest.param(_review_before_take('warden \\\n  review --event "$GITHUB_EVENT_PATH"'),
                 id="review-across-a-joined-line-before-take"),
    pytest.param(_move_take("review"), id="take-after-review"),
    pytest.param(_move_take("download"), id="take-before-download"),
])
def test_d03_reports_drift_for_a_take_step_not_in_the_exact_form(edit, tmp_path, monkeypatch):
    result = _d03_edited(tmp_path, monkeypatch, edit)
    assert result.status == declared_mod.DRIFT, result
    assert "--scope python" in result.findings[0].detail


@pytest.mark.parametrize("edit, cause", [
    pytest.param(_env("workflow", {"FORCE_COLOR": "1"}), "the workflow has an `env`",
                 id="workflow-env"),
    pytest.param(_env("job", {"TZ": "UTC"}), "the job has an `env`", id="job-env"),
    pytest.param(_take_key("shell", "bash {0}"), "the take step's shell is `bash {0}`, not `bash`",
                 id="shell-bash-template"),
    pytest.param(lambda doc: _step(doc, "gate", TAKE).pop("shell"),
                 "the take step has no `shell: bash`", id="shell-absent"),
    pytest.param(_take_run('warden take --from "$RUNNER_TEMP/elsewhere"'),
                 f"the take reads `${{{{ runner.temp }}}}/elsewhere` but the `{_T}` download "
                 f"writes to `${{{{ runner.temp }}}}/{_T}`", id="path-mismatch"),
    pytest.param(_download_with("run-id", "123"),
                 f"the `{_T}` download sets `run-id` under `with:`", id="download-run-id"),
    pytest.param(lambda doc: _add_download(doc, path=f"${{{{ runner.temp }}}}/{_T}"),
                 "another download-artifact step can write into the take path",
                 id="nameless-download-to-the-take-path"),
])
def test_d03_drift_names_why_the_take_step_does_not_count(edit, cause, tmp_path, monkeypatch):
    result = _d03_edited(tmp_path, monkeypatch, edit)
    assert result.status == declared_mod.DRIFT, result
    detail = result.findings[0].detail
    assert "--scope python" in detail, detail
    assert cause in detail, detail
    assert "one-line form `warden init` writes" in detail and "CLI-Reference" in detail, detail


def test_d03_drift_with_no_take_step_names_no_take_cause(tmp_path, monkeypatch):
    def edit(doc: dict) -> None:
        doc["jobs"]["gate"]["steps"].remove(_step(doc, "gate", TAKE))
    detail = _d03_edited(tmp_path, monkeypatch, edit).findings[0].detail
    assert "a `warden take` step is present" not in detail, detail


def test_d03_limits_say_the_same_in_the_three_lists():
    """The module docstring, `_taken_scopes` and the CLI-Reference D-03 row each
    state what D-03 does not read about a take, and state it alike."""
    limits = ("`if:` on any job or step other than the take step",
              "plant results in the download directory",
              "set `BASH_ENV` through `$GITHUB_ENV` before the take",
              "rewrite `.warden/out` after it",
              "`warden review` has the same exposure",
              "what other steps before review do")
    row = next(line for line in (WIKI / "CLI-Reference.md").read_text().splitlines()
               if line.startswith("| `declare check` |"))
    lists = {"module docstring": declared_mod.__doc__,
             "_taken_scopes": declared_mod._taken_scopes.__doc__,
             "CLI-Reference": row}
    for where, text in lists.items():
        flat = " ".join(text.split())
        for limit in limits:
            assert limit in flat, f"{where} does not say: {limit}"
        assert "`if:` on the job" not in flat, where
        # A step before the download can write into the take path as well.
        assert "between the download and review" not in flat, where


# ── D-03 reads the effective working directory ─────────────────────────────

def _d03_sub_edited(tmp_path: Path, monkeypatch, edit) -> declared_mod.CheckResult:
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    path = mono / ".github" / "workflows" / "warden-svc-api.yml"
    doc = yaml.safe_load(path.read_text())
    edit(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return declared_mod.check_scope_runners(api, config_mod.load(api))


def test_d03_a_review_step_that_leaves_the_job_working_directory_is_drift(
        tmp_path, monkeypatch):
    result = _d03_sub_edited(tmp_path, monkeypatch, lambda doc: _step(
        doc, "gate", "warden review").__setitem__("working-directory", "."))
    assert result.status == declared_mod.DRIFT, result
    detail = result.findings[0].detail
    assert "the `warden review` step runs in `.` and the take step in `svc/api`" in detail, detail


def test_d03_a_workflow_working_directory_default_for_both_steps_reads_clean(
        tmp_path, monkeypatch):
    def hoist(doc: dict) -> None:
        for job in doc["jobs"].values():
            assert job.pop("defaults") == {"run": {"working-directory": "svc/api"}}
        doc["defaults"] = {"run": {"working-directory": "svc/api"}}
    result = _d03_sub_edited(tmp_path, monkeypatch, hoist)
    assert result.status == declared_mod.CLEAN, result


def test_d03_a_root_gate_with_no_working_directory_default_reads_clean(tmp_path, monkeypatch):
    def no_defaults(doc: dict) -> None:
        assert "defaults" not in doc
        assert all("defaults" not in job for job in doc["jobs"].values())
    result = _d03_edited(tmp_path, monkeypatch, no_defaults)
    assert result.status == declared_mod.CLEAN, result


def test_d03_an_upload_outside_the_verify_jobs_working_directory_is_drift(
        tmp_path, monkeypatch):
    def unprefixed(doc: dict) -> None:
        _uses(doc, "verify", "actions/upload-artifact")[0]["with"]["path"] = (
            ".warden/out/*-verify/verify-result.json")
    result = _d03_sub_edited(tmp_path, monkeypatch, unprefixed)
    assert result.status == declared_mod.DRIFT, result
    detail = result.findings[0].detail
    assert ("the `warden-verify` upload path `.warden/out/*-verify/verify-result.json` "
            "is not under `svc/api/.warden/out/`") in detail, detail


# ── D-03 compares the verify job's directory with the gate's ────────────────

def test_d03_a_gate_in_a_subdirectory_taking_from_a_verify_job_at_the_workspace_is_drift(
        tmp_path, monkeypatch):
    """Review pairs results by commit and scope name only, so a gate reviewing
    `sub` would show the workspace's verify result as its own."""
    result = _d03_edited(tmp_path, monkeypatch, _wd_default("job", "sub"))
    assert result.status == declared_mod.DRIFT, result
    detail = result.findings[0].detail
    assert ("job `verify` runs `warden verify` in the workspace and the gate takes "
            "and reviews in `sub`") in detail, detail


def test_d03_a_gate_at_the_workspace_taking_from_a_verify_job_in_a_subdirectory_is_drift(
        tmp_path, monkeypatch):
    def gate_at_the_workspace(doc: dict) -> None:
        doc["jobs"]["gate"].pop("defaults")
    result = _d03_sub_edited(tmp_path, monkeypatch, gate_at_the_workspace)
    assert result.status == declared_mod.DRIFT, result
    detail = result.findings[0].detail
    assert ("job `verify` runs `warden verify` in `svc/api` and the gate takes and "
            "reviews in the workspace") in detail, detail


@pytest.mark.parametrize("below, spelled", [("", None), ("svc/api", None),
                                            ("svc/api", "./svc/api/")],
                         ids=["root", "subdirectory", "subdirectory-spelled-differently"])
def test_d03_a_verify_job_and_gate_in_the_same_directory_read_clean(
        below, spelled, tmp_path, monkeypatch):
    def respell(doc: dict) -> None:
        if spelled:
            doc["jobs"]["gate"]["defaults"] = {"run": {"working-directory": spelled}}
    edited = _d03_sub_edited if below else _d03_edited
    result = edited(tmp_path, monkeypatch, respell)
    assert result.status == declared_mod.CLEAN, result


# ── the root skip reads repo.yaml from the tree ─────────────────────────────

def _jobs_at_the_workspace(gate: Path) -> None:
    doc = yaml.safe_load(gate.read_text())
    for job in doc["jobs"].values():
        job.pop("defaults")
    gate.write_text(yaml.safe_dump(doc, sort_keys=False))


def _root_without_a_gate(tmp_path: Path, monkeypatch) -> Path:
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    (repo / enroll.WORKFLOW_PATH).unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll the root with no gate of its own")
    return repo


def _d03_line(repo: Path, monkeypatch, capsys) -> str:
    capsys.readouterr()
    _warden(repo, monkeypatch, "declare", "check")
    return _check_line(capsys.readouterr().out, "D-03")


@pytest.mark.parametrize("jobs", ["as-init-wrote-them", "at-the-workspace"])
def test_root_certify_skips_the_gate_of_an_untracked_subdirectory_enrollment(
        jobs, tmp_path, monkeypatch, capsys):
    """Right after `warden init` in `svc/api`, before the commit, its
    `repo.yaml` is on disk and not in git's index."""
    repo = _root_without_a_gate(tmp_path, monkeypatch)
    gate = _enroll_a_subdirectory_too(repo, monkeypatch)
    if jobs == "at-the-workspace":
        _jobs_at_the_workspace(gate)
    assert "svc/api/repo.yaml" not in _git(repo, "ls-files")
    code, report = _certify(repo, monkeypatch, capsys)
    assert code != 0 and "LEVEL 3" not in report, report
    for check in ("G-04", "V-02"):
        assert "✗" in _check_line(report, check), report
    assert ("the root `warden-svc-api.yml` was skipped as the gate of the enrollment "
            "at `svc/api`") in _check_line(report, "G-04"), report
    d03 = _d03_line(repo, monkeypatch, capsys)
    assert "✓" not in d03 and "warden-svc-api.yml:gate" not in d03, d03
    assert "`warden-svc-api.yml` was skipped" in d03, d03


def test_root_certify_skips_the_gate_of_a_subdirectory_enrollment_whose_repo_yaml_was_deleted(
        tmp_path, monkeypatch, capsys):
    repo = _root_without_a_gate(tmp_path, monkeypatch)
    _enroll_a_subdirectory_too(repo, monkeypatch)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll svc/api")
    _git(repo, "rm", "-q", "svc/api/repo.yaml")
    _git(repo, "commit", "-qm", "delete the svc/api enrollment's repo.yaml")
    code, report = _certify(repo, monkeypatch, capsys)
    assert code != 0 and "LEVEL 3" not in report, report
    line = _check_line(report, "G-04")
    assert "✗" in line and "every job in it runs below the git root, in `svc/api`" in line, report
    d03 = _d03_line(repo, monkeypatch, capsys)
    assert "✓" not in d03 and "warden-svc-api.yml:gate" not in d03, d03
    assert "`warden-svc-api.yml` was skipped" in d03, d03


def test_a_root_workflow_whose_jobs_all_run_in_a_subdirectory_is_never_credited_to_the_root(
        tmp_path, monkeypatch, capsys):
    """Named for no enrollment, so no repo.yaml skips it: its jobs are what
    say it is not the root's gate."""
    repo = _root_without_a_gate(tmp_path, monkeypatch)
    gate = _enroll_a_subdirectory_too(repo, monkeypatch)
    gate.rename(gate.with_name("api-ci.yml"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll svc/api")
    code, report = _certify(repo, monkeypatch, capsys)
    assert code != 0 and "LEVEL 3" not in report, report
    for check in ("G-04", "V-02"):
        assert "✗" in _check_line(report, check), report
    assert ("the root `api-ci.yml` was skipped because every job in it runs below the "
            "git root, in `svc/api`") in _check_line(report, "G-04"), report
    from warden import certify as certify_mod
    passed, detail = certify_mod._run_check(
        {"type": "ci_step_enforced", "pattern": "warden review"}, repo)
    assert not passed, detail


def test_root_certify_credits_a_gate_whose_name_no_on_disk_enrollment_claims(
        tmp_path, monkeypatch, capsys):
    """The root's own gate, renamed `warden-svc-api.yml` but still named
    `warden`, beside an `svc/api/repo.yaml` that is on disk and untracked. The
    file name alone cannot say whose gate a file is, so the on-disk skip also
    reads the workflow's `name:`: this one is the root's."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    root_gate = repo / enroll.WORKFLOW_PATH
    root_gate.rename(root_gate.with_name("warden-svc-api.yml"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll")
    (repo / "svc" / "api").mkdir(parents=True)
    (repo / "svc" / "api" / "repo.yaml").write_text("# enrolled here, not committed\n")
    assert "svc/api/repo.yaml" not in _git(repo, "ls-files")
    assert _warden(repo, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    code, report = _certify(repo, monkeypatch, capsys)
    assert code == 0 and "certification: LEVEL 3 (Reviewed)" in report, report
    for check in ("G-04", "V-02"):
        assert "warden-svc-api.yml" in _check_line(report, check), report


def test_root_certify_skips_the_gate_of_an_ignored_on_disk_enrollment_that_names_it(
        tmp_path, monkeypatch, capsys):
    """An `svc/api/repo.yaml` git IGNORES is an enrollment on disk like any
    other, and its gate — `name: warden (svc/api)`, so it really is that
    enrollment's — is skipped at the root. The jobs run at the workspace, so
    the on-disk reading is the only rule that can skip it."""
    repo = _root_without_a_gate(tmp_path, monkeypatch)
    gate = _enroll_a_subdirectory_too(repo, monkeypatch)
    _jobs_at_the_workspace(gate)
    (repo / ".git" / "info" / "exclude").write_text("/svc/\n")
    assert "svc/api/repo.yaml" not in _git(repo, "ls-files")
    assert "svc/api" not in _git(repo, "status", "--porcelain", "--untracked-files=all")
    code, report = _certify(repo, monkeypatch, capsys)
    assert code != 0 and "LEVEL 3" not in report, report
    assert ("the root `warden-svc-api.yml` was skipped as the gate of the enrollment "
            "at `svc/api`") in _check_line(report, "G-04"), report


def test_a_root_workflow_whose_jobs_run_in_an_absolute_directory_is_still_the_roots(
        tmp_path, monkeypatch, capsys):
    """`working-directory: /tmp` is not a path below the git root, so the skip
    that reads a workflow's jobs does not claim the file for an enrollment
    below the root."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    gate = repo / enroll.WORKFLOW_PATH
    doc = yaml.safe_load(gate.read_text())
    for job in doc["jobs"].values():
        job["defaults"] = {"run": {"working-directory": "/tmp"}}
    gate.write_text(yaml.safe_dump(doc, sort_keys=False))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "run every gate job in an absolute directory")
    assert _warden(repo, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    code, report = _certify(repo, monkeypatch, capsys)
    assert code == 0 and "certification: LEVEL 3 (Reviewed)" in report, report
    assert "warden.yml" in _check_line(report, "G-04"), report


def test_on_disk_enrollment_gives_up_past_max_dashes_and_leaves_the_name_to_the_index(
        tmp_path):
    """The disk is asked for each reading of the `-`s in a gate's name, so the
    number of readings is capped: a name with more than `MAX_DASHES` of them is
    looked up in git's index alone, however deep the tree really is."""
    from warden import gate_workflows as gw

    capped = "/".join(f"s{i}" for i in range(gw.MAX_DASHES + 1))
    (tmp_path / capped).mkdir(parents=True)
    (tmp_path / capped / "repo.yaml").write_text("# an enrollment on disk\n")
    assert capped.count("/") == gw.MAX_DASHES
    assert gw.on_disk_enrollment(tmp_path, gw.workflow_name(capped)) == capped

    over = f"{capped}/s{gw.MAX_DASHES + 1}"
    (tmp_path / over).mkdir(parents=True)
    (tmp_path / over / "repo.yaml").write_text("# one segment deeper\n")
    assert gw.on_disk_enrollment(tmp_path, gw.workflow_name(over)) is None


# ── D-03 refuses a lost gate rather than reading nothing as clean ───────────
#
# A pull request that adds a tracked `ci/repo.yaml` would make the root's only
# gate, hand-named `warden-ci.yml`, read as `ci`'s on its file name alone.
# D-03 would then have no workflow left and render not-applicable, and
# declare check would print clean at exit 0 while certify fell to LEVEL 0.

def _root_gate_as_warden_ci(repo: Path, name: str | None) -> Path:
    """Rename the root gate init wrote to `warden-ci.yml` and set its top-level
    `name:` to NAME. Returns the file."""
    gate = repo / enroll.WORKFLOW_PATH
    moved = gate.with_name("warden-ci.yml")
    doc = yaml.safe_load(gate.read_text())
    doc["name"] = name
    moved.write_text(yaml.safe_dump(doc, sort_keys=False))
    gate.unlink()
    return moved


def _declare(repo: Path, monkeypatch, capsys) -> tuple[int, str, str]:
    capsys.readouterr()
    code = _warden(repo, monkeypatch, "declare", "check")
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.mark.parametrize("name", ["warden", "warden (pr)", "warden (CI)", "CI"],
                         ids=["root-name", "another-prefix", "case-differs", "no-claim"])
def test_d03_refuses_when_a_tracked_repo_yaml_takes_the_roots_only_gate_by_file_name(
        name, tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    _root_gate_as_warden_ci(repo, name)
    (repo / "ci").mkdir()
    (repo / "ci" / "repo.yaml").write_text("# a subdirectory enrollment\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add ci/repo.yaml beside the hand-named root gate")
    assert "ci/repo.yaml" in _git(repo, "ls-files")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code == 2, (out, err)
    assert "clean:" not in out, out
    assert "D-03" in err and "`warden-ci.yml`" in err, err
    assert "as the gate of the enrollment at `ci`" in err, err


def test_d03_keeps_not_applicable_when_the_skipped_gate_names_that_enrollment(
        tmp_path, monkeypatch, capsys):
    """`name: warden (ci)` beside a tracked `ci/repo.yaml`: the file says it is
    `ci`'s gate, so the root genuinely has none of its own."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    _root_gate_as_warden_ci(repo, "warden (ci)")
    (repo / "ci").mkdir()
    (repo / "ci" / "repo.yaml").write_text("# a subdirectory enrollment\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the gate belongs to ci")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code != 2, err
    d03 = _check_line(out, "D-03")
    assert d03.startswith("–"), d03
    assert "the root `warden-ci.yml` was skipped as the gate of the enrollment at `ci`" in d03, d03


def test_d03_credits_the_root_gate_beside_an_on_disk_repo_yaml_it_does_not_name(
        tmp_path, monkeypatch, capsys):
    """The on-disk reading already asks the workflow's `name:`, so an untracked
    `ci/repo.yaml` never took the root's `warden`-named gate. Pinned so the
    refusal above does not reach it."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    _root_gate_as_warden_ci(repo, "warden")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "hand-name the root gate")
    (repo / "ci").mkdir()
    (repo / "ci" / "repo.yaml").write_text("# on disk, not committed\n")
    assert "ci/repo.yaml" not in _git(repo, "ls-files")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code == 0, (out, err)
    d03 = _check_line(out, "D-03")
    assert d03.startswith("✓") and "warden-ci.yml:gate" in d03, d03


def test_d03_does_not_refuse_a_disowned_skip_when_another_gate_is_credited(
        tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    gate = repo / enroll.WORKFLOW_PATH
    shutil.copy(gate, gate.with_name("warden-ci.yml"))
    (repo / "ci").mkdir()
    (repo / "ci" / "repo.yaml").write_text("# a subdirectory enrollment\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a second copy of the root gate, taken by ci")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code == 0, (out, err)
    d03 = _check_line(out, "D-03")
    assert d03.startswith("✓") and "warden.yml:gate" in d03, d03


def test_d03_refuses_a_root_named_gate_skipped_for_running_below_the_root(
        tmp_path, monkeypatch, capsys):
    """The other skip: every job runs in `app`, so the file reads as the gate
    of an enrollment below the root, but its `name:` is the root's."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    gate = repo / enroll.WORKFLOW_PATH
    doc = yaml.safe_load(gate.read_text())
    for job in doc["jobs"].values():
        job["defaults"] = {"run": {"working-directory": "app"}}
    gate.write_text(yaml.safe_dump(doc, sort_keys=False))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "run every gate job in app")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code == 2, (out, err)
    assert "`warden.yml`" in err and "every job in it runs below the git root" in err, err


def test_d03_keeps_not_applicable_when_the_gate_names_the_other_enrollment_sharing_its_file_name(
        tmp_path, monkeypatch, capsys):
    """`svc/api` and `svc-api` both tracked share `warden-svc-api.yml`, and the
    index keeps one of them per file name. The file names `svc-api`, a real
    enrollment whose gate that file name is, so it is not the root's."""
    repo = _root_without_a_gate(tmp_path, monkeypatch)
    gate = _enroll_a_subdirectory_too(repo, monkeypatch)
    doc = yaml.safe_load(gate.read_text())
    doc["name"] = "warden (svc-api)"
    gate.write_text(yaml.safe_dump(doc, sort_keys=False))
    (repo / "svc-api").mkdir()
    (repo / "svc-api" / "repo.yaml").write_text("# the dash enrollment\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll svc/api and svc-api")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code != 2, err
    d03 = _check_line(out, "D-03")
    assert d03.startswith("–") and "`warden-svc-api.yml` was skipped" in d03, d03


def test_d03_refuses_when_the_dash_enrollment_the_gate_names_has_no_repo_yaml(
        tmp_path, monkeypatch, capsys):
    """The cell above without `svc-api/repo.yaml`: the file names `svc-api`,
    whose gate that file name is, but no enrollment is there, so nothing the
    file names fits and the root's lost gate is refused."""
    repo = _root_without_a_gate(tmp_path, monkeypatch)
    gate = _enroll_a_subdirectory_too(repo, monkeypatch)
    doc = yaml.safe_load(gate.read_text())
    doc["name"] = "warden (svc-api)"
    gate.write_text(yaml.safe_dump(doc, sort_keys=False))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "enroll svc/api, its gate naming svc-api")
    assert not (repo / "svc-api").exists()
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code == 2, (out, err)
    assert "D-03" in err and "`warden-svc-api.yml`" in err, err


@pytest.mark.parametrize("outside", ["dot-dot", "absolute"])
def test_d03_refuses_a_gate_naming_an_owner_outside_the_repo(
        outside, tmp_path, monkeypatch, capsys):
    """A `name:` whose prefix leaves the checkout — `warden (../x)`, or an
    absolute path — on a file whose name a tracked enrollment gives it. A
    `repo.yaml` really is at that owner, outside the repo; it is no
    enrollment of this repository, so the skip is disowned and refused."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "repo.yaml").write_text("# another checkout's enrollment\n")
    owner = "../x" if outside == "dot-dot" else (tmp_path / "x").as_posix()
    tracked = owner.replace("/", "-")
    gate = repo / enroll.WORKFLOW_PATH
    doc = yaml.safe_load(gate.read_text())
    doc["name"] = f"warden ({owner})"
    moved = gate.with_name(f"warden-{tracked}.yml")
    moved.write_text(yaml.safe_dump(doc, sort_keys=False))
    gate.unlink()
    (repo / tracked).mkdir()
    (repo / tracked / "repo.yaml").write_text("# a subdirectory enrollment\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the root gate names an owner outside the repo")
    assert f"{tracked}/repo.yaml" in _git(repo, "ls-files")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code == 2, (out, err)
    assert "D-03" in err and f"`{moved.name}`" in err, err


def test_an_owner_outside_the_repo_never_names_an_enrollment(tmp_path):
    """The helper itself, for the reading the index cannot reach: with git
    unable to list the tracked files, the file name and the `name:` are all
    there is, and neither `..` nor an absolute owner may probe the disk."""
    from warden import gate_workflows as gw

    top = tmp_path / "demo"
    top.mkdir()
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "repo.yaml").write_text("# outside the checkout\n")
    (top / "svc" / "api").mkdir(parents=True)
    (top / "svc" / "api" / "repo.yaml").write_text("# inside\n")
    for owner in ("../x", (tmp_path / "x").as_posix(), "svc/../../x"):
        assert os.path.lexists(top / owner / "repo.yaml"), owner
        path = top / gw.WORKFLOWS_DIR / gw.workflow_name(owner)
        assert not gw._names_an_enrollment_for(top, path, owner), owner
    path = top / gw.WORKFLOWS_DIR / gw.workflow_name("svc/api")
    assert gw._names_an_enrollment_for(top, path, "svc/api")


def test_a_symlinked_owner_outside_the_repo_never_names_an_enrollment(tmp_path):
    """Plain segments are not enough: `x` spelled plainly is a symlink to a
    directory outside the checkout holding a `repo.yaml`. Neither the `name:`
    reading nor the on-disk one may take it for an enrollment."""
    from warden import gate_workflows as gw

    top = tmp_path / "demo"
    top.mkdir()
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "repo.yaml").write_text("# outside the checkout\n")
    (top / "x").symlink_to(tmp_path / "out", target_is_directory=True)
    (top / "inside").mkdir()
    (top / "inside" / "repo.yaml").write_text("# inside\n")
    (top / "alias").symlink_to(top / "inside", target_is_directory=True)
    assert os.path.lexists(top / "x" / "repo.yaml")
    path = top / gw.WORKFLOWS_DIR / gw.workflow_name("x")
    assert not gw._names_an_enrollment_for(top, path, "x")
    assert gw.on_disk_enrollment(top, gw.workflow_name("x")) is None
    # warden run from `alias` is told git's physical prefix, `inside`, never
    # `alias`, so the alias is no enrollment path either.
    assert gw.on_disk_enrollment(top, gw.workflow_name("alias")) is None
    assert gw.on_disk_enrollment(top, gw.workflow_name("inside")) == "inside"


def test_a_symlinked_segment_inside_the_repo_never_names_an_enrollment(tmp_path):
    """A plain segment that is a symlink back into the checkout — to the git
    root itself, or to another directory holding a `repo.yaml`, or on the way
    to one — is not the path of an enrollment: the `repo.yaml` it reaches is
    the one at the physical path, which is where git places warden."""
    from warden import gate_workflows as gw

    top = tmp_path / "demo"
    top.mkdir()
    (top / "repo.yaml").write_text("# the root enrollment\n")
    (top / "svc" / "api").mkdir(parents=True)
    (top / "svc" / "api" / "repo.yaml").write_text("# inside\n")
    (top / "x").symlink_to(".", target_is_directory=True)
    (top / "alias").symlink_to("svc/api", target_is_directory=True)
    (top / "via").symlink_to("svc", target_is_directory=True)
    for owner in ("x", "alias", "via/api", "x/svc/api"):
        assert os.path.lexists(top / owner / "repo.yaml"), owner
        path = top / gw.WORKFLOWS_DIR / gw.workflow_name(owner)
        assert not gw._names_an_enrollment_for(top, path, owner), owner
        assert gw.on_disk_enrollment(top, gw.workflow_name(owner)) is None, owner
    path = top / gw.WORKFLOWS_DIR / gw.workflow_name("svc/api")
    assert gw._names_an_enrollment_for(top, path, "svc/api")


@pytest.mark.parametrize("target", ["root", "inside"])
def test_d03_credits_the_root_gate_named_for_a_symlink_into_the_repo(
        target, tmp_path, monkeypatch, capsys):
    """The root's only gate is `warden-x.yml`, `name: warden (x)`, and `x` is a
    tracked symlink to the git root itself, or to `inside`, a tracked
    enrollment. `x/repo.yaml` is on disk either way, but `x` is no
    enrollment's path, so the root keeps its gate and D-03 reads it — never
    not-applicable over the lost gate."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    if target == "inside":
        (repo / "inside").mkdir()
        (repo / "inside" / "repo.yaml").write_text("# a subdirectory enrollment\n")
    (repo / "x").symlink_to("." if target == "root" else "inside",
                            target_is_directory=True)
    gate = repo / enroll.WORKFLOW_PATH
    doc = yaml.safe_load(gate.read_text())
    doc["name"] = "warden (x)"
    gate.with_name("warden-x.yml").write_text(yaml.safe_dump(doc, sort_keys=False))
    gate.unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the root gate named for a symlink into the repo")
    assert os.path.lexists(repo / "x" / "repo.yaml")
    assert "x/repo.yaml" not in _git(repo, "ls-files")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code == 0, (out, err)
    d03 = _check_line(out, "D-03")
    assert d03.startswith("✓") and "warden-x.yml:gate" in d03, d03


def _skip_unless_case_insensitive(directory: Path) -> None:
    probe = directory / "case-probe"
    probe.write_text("")
    try:
        if not os.path.lexists(directory / "CASE-PROBE"):
            pytest.skip("the filesystem under tmp_path is case-sensitive")
    finally:
        probe.unlink()


def test_a_case_variant_prefix_never_names_an_enrollment(tmp_path):
    """On a case-insensitive filesystem `SVC/api` reaches `svc/api/repo.yaml`
    and resolves to itself, because resolving never folds case. warden run
    from it is told git's physical prefix, `svc/api`, so `SVC/api` is an
    alias exactly as a symlink is, and neither reading may take it."""
    from warden import gate_workflows as gw

    top = tmp_path / "demo"
    top.mkdir()
    _skip_unless_case_insensitive(top)
    (top / "svc" / "api").mkdir(parents=True)
    (top / "svc" / "api" / "repo.yaml").write_text("# inside\n")
    for owner in ("SVC/api", "svc/API", "Svc/Api"):
        assert os.path.lexists(top / owner / "repo.yaml"), owner
        path = top / gw.WORKFLOWS_DIR / gw.workflow_name(owner)
        assert not gw._names_an_enrollment_for(top, path, owner), owner
        assert gw.on_disk_enrollment(top, gw.workflow_name(owner)) is None, owner
    path = top / gw.WORKFLOWS_DIR / gw.workflow_name("svc/api")
    assert gw._names_an_enrollment_for(top, path, "svc/api")
    assert gw.on_disk_enrollment(top, gw.workflow_name("svc/api")) == "svc/api"


def test_d03_credits_the_root_gate_named_for_a_case_variant_of_a_tracked_enrollment(
        tmp_path, monkeypatch, capsys):
    """The root's only gate is `warden-SVC-api.yml`, `name: warden (SVC/api)`,
    beside a tracked `svc/api/repo.yaml`. On a case-insensitive filesystem
    `SVC/api/repo.yaml` is on disk, but `SVC/api` is no enrollment's path, so
    the root keeps its gate and D-03 reads it — never not-applicable over the
    lost gate."""
    repo = _repo(tmp_path, FIXTURES["python"])
    _skip_unless_case_insensitive(repo)
    assert _warden(repo, monkeypatch, "init") == 0
    (repo / "svc" / "api").mkdir(parents=True)
    (repo / "svc" / "api" / "repo.yaml").write_text("# a subdirectory enrollment\n")
    gate = repo / enroll.WORKFLOW_PATH
    doc = yaml.safe_load(gate.read_text())
    doc["name"] = "warden (SVC/api)"
    gate.with_name("warden-SVC-api.yml").write_text(yaml.safe_dump(doc, sort_keys=False))
    gate.unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the root gate named for a case variant of svc/api")
    assert os.path.lexists(repo / "SVC" / "api" / "repo.yaml")
    assert "svc/api/repo.yaml" in _git(repo, "ls-files")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code == 0, (out, err)
    d03 = _check_line(out, "D-03")
    assert d03.startswith("✓") and "warden-SVC-api.yml:gate" in d03, d03


def test_d03_credits_the_root_gate_named_for_a_symlink_out_of_the_repo(
        tmp_path, monkeypatch, capsys):
    """The root's only gate is `warden-x.yml`, `name: warden (x)`, and `x` is a
    tracked symlink to a directory outside the checkout that holds a
    `repo.yaml`. That is no enrollment of this repository, so the root keeps
    its gate and D-03 reads it."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "repo.yaml").write_text("# another checkout's enrollment\n")
    (repo / "x").symlink_to(tmp_path / "out", target_is_directory=True)
    gate = repo / enroll.WORKFLOW_PATH
    doc = yaml.safe_load(gate.read_text())
    doc["name"] = "warden (x)"
    gate.with_name("warden-x.yml").write_text(yaml.safe_dump(doc, sort_keys=False))
    gate.unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the root gate named for a symlink out of the repo")
    assert "x/repo.yaml" not in _git(repo, "ls-files")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code == 0, (out, err)
    d03 = _check_line(out, "D-03")
    assert d03.startswith("✓") and "warden-x.yml:gate" in d03, d03


def test_d03_keeps_not_applicable_when_the_jobs_run_inside_the_enrollment_the_gate_names(
        tmp_path, monkeypatch, capsys):
    """`name: warden (app)` with every job in `app/src`: warden finds
    `app/repo.yaml` walking up from there, so the file is `app`'s gate."""
    repo = _repo(tmp_path, FIXTURES["python"])
    assert _warden(repo, monkeypatch, "init") == 0
    gate = repo / enroll.WORKFLOW_PATH
    doc = yaml.safe_load(gate.read_text())
    doc["name"] = "warden (app)"
    for job in doc["jobs"].values():
        job["defaults"] = {"run": {"working-directory": "app/src"}}
    gate.with_name("ci-app.yml").write_text(yaml.safe_dump(doc, sort_keys=False))
    gate.unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the only gate is app's, run from app/src")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code != 2, err
    d03 = _check_line(out, "D-03")
    assert d03.startswith("–") and "`ci-app.yml` was skipped because every job" in d03, d03


def test_d03_does_not_refuse_a_disowned_skip_that_runs_no_review(
        tmp_path, monkeypatch, capsys):
    """Only a GATE can be lost: a `warden-ci.yml` that runs no `warden review`,
    taken by a tracked `ci/repo.yaml`, leaves D-03 not-applicable."""
    repo = _root_without_a_gate(tmp_path, monkeypatch)
    lint = {"name": "lint", "on": ["pull_request"],
            "jobs": {"lint": {"runs-on": "ubuntu-latest",
                              "steps": [{"run": "echo lint"}]}}}
    (repo / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (repo / ".github" / "workflows" / "warden-ci.yml").write_text(
        yaml.safe_dump(lint, sort_keys=False))
    (repo / "ci").mkdir()
    (repo / "ci" / "repo.yaml").write_text("# a subdirectory enrollment\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a lint workflow named for ci")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code != 2, err
    d03 = _check_line(out, "D-03")
    assert d03.startswith("–") and "`warden-ci.yml` was skipped" in d03, d03


def test_d03_refuses_a_disowned_skip_it_cannot_parse(tmp_path, monkeypatch, capsys):
    repo = _root_without_a_gate(tmp_path, monkeypatch)
    (repo / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (repo / ".github" / "workflows" / "warden-ci.yml").write_text("jobs: [unclosed\n")
    (repo / "ci").mkdir()
    (repo / "ci" / "repo.yaml").write_text("# a subdirectory enrollment\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "an unreadable workflow named for ci")
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code == 2, (out, err)
    assert "warden-ci.yml" in err and "could not be parsed" in err, err


def test_d03_stays_not_applicable_for_a_root_that_declares_no_gate(
        tmp_path, monkeypatch, capsys):
    """A repository with no gate workflow at all is not a lost gate: nothing
    was skipped, so the verdict is unchanged."""
    repo = _root_without_a_gate(tmp_path, monkeypatch)
    code, out, err = _declare(repo, monkeypatch, capsys)
    assert code != 2, err
    d03 = _check_line(out, "D-03")
    assert d03.startswith("–") and "skipped" not in d03, d03


# ── a root gate is credited only to the enrollment it names ─────────────────

def test_certify_from_svc_dash_api_does_not_pass_g04_or_v02_on_the_svc_slash_api_gate(
        tmp_path, monkeypatch, capsys):
    """`svc/api` and `svc-api` both map to `warden-svc-api.yml`. The file's
    `name:` is `warden (svc/api)`, so only `svc/api` is credited with it."""
    from warden import certify as certify_mod
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    dash = mono / "svc-api"
    shutil.copytree(api, dash)
    _git(mono, "add", "-A")
    _git(mono, "commit", "-qm", "enroll svc/api, and svc-api by copying it")
    for where in (api, dash):
        assert _warden(where, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0

    code, report = _certify(dash, monkeypatch, capsys)
    assert code != 0 and "LEVEL 3" not in report, report
    for check in ("G-04", "V-02"):
        assert "✗" in _check_line(report, check), report
    assert ("the root `warden-svc-api.yml` was skipped as the gate of the enrollment "
            "at `svc/api`") in _check_line(report, "G-04"), report
    passed, detail = certify_mod._run_check(
        {"type": "ci_step_enforced", "pattern": "warden review"}, dash)
    assert not passed, detail
    d03 = _d03_line(dash, monkeypatch, capsys)
    assert "✓" not in d03 and "warden-svc-api.yml:gate" not in d03, d03
    assert "`warden-svc-api.yml` was skipped" in d03, d03

    code, report = _certify(api, monkeypatch, capsys)
    assert code == 0 and "certification: LEVEL 3 (Reviewed)" in report, report
    assert "warden-svc-api.yml" in _check_line(report, "G-04"), report


def test_a_root_gate_whose_name_only_contains_the_enrollment_is_not_credited_to_it(
        tmp_path, monkeypatch, capsys):
    """Credit needs the whole top-level `name:` to be the one `init` writes,
    not a `warden (svc/api)` sitting inside a longer name: `ci warden (svc/api)
    nightly` is some other workflow that mentions the enrollment."""
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    gate = mono / ".github" / "workflows" / "warden-svc-api.yml"
    text = gate.read_text()
    assert text.count("\nname: warden (svc/api)\n") == 1, text[:400]
    gate.write_text(text.replace("\nname: warden (svc/api)\n",
                                 "\nname: ci warden (svc/api) nightly\n"))
    _git(mono, "add", "-A")
    _git(mono, "commit", "-qm", "enroll svc/api")
    code, report = _certify(api, monkeypatch, capsys)
    assert code != 0 and "LEVEL 3" not in report, report
    for check in ("G-04", "V-02"):
        assert "✗" in _check_line(report, check), report
    assert ("its top-level `name:` is not `warden (svc/api)`"
            in _check_line(report, "G-04")), report


def test_init_refusal_names_the_enrollment_the_existing_gate_belongs_to(
        tmp_path, monkeypatch, capsys):
    mono = _mono(tmp_path)
    assert _warden(mono / "svc" / "api", monkeypatch, "init") == 0
    dash = mono / "svc-api"
    for rel, text in FIXTURES["python"].items():
        (dash / rel).parent.mkdir(parents=True, exist_ok=True)
        (dash / rel).write_text(text)
    before = _snapshot(mono)
    capsys.readouterr()
    assert _warden(dash, monkeypatch, "init") == 1
    err = capsys.readouterr().err
    assert "../.github/workflows/warden-svc-api.yml" in err, err
    assert ("../.github/workflows/warden-svc-api.yml is the gate of the enrollment "
            "at `svc/api`") in err, err
    assert _snapshot(mono) == before


# ── a subdirectory gate reviews its own root workflow ───────────────────────

def test_a_pr_editing_only_the_root_gate_workflow_is_reviewed_at_high_by_the_subdirectory_gate(
        tmp_path, monkeypatch, capsys):
    from warden import diffs as diffs_mod
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    other = mono / ".github" / "workflows" / "other.yml"
    other.write_text("name: other\non: push\njobs: {}\n")
    _git(mono, "add", "-A")
    _git(mono, "commit", "-qm", "enroll")
    gate = mono / ".github" / "workflows" / "warden-svc-api.yml"
    gate.write_text(gate.read_text().replace(
        "run: warden certify --level 3", "run: warden certify --level 3 || true"))
    other.write_text(other.read_text() + "# edited\n")
    _git(mono, "commit", "-qam", "weaken the svc/api gate")
    path = "../../.github/workflows/warden-svc-api.yml"

    capsys.readouterr()
    assert _warden(api, monkeypatch, "explain", "--base", "HEAD~1") == 0
    out = capsys.readouterr().out
    assert f"- HIGH: `{path}`" in out, out
    assert "other.yml" not in out, out

    ctx = diffs_mod.get_context(api, "HEAD~1")
    assert ctx.files == (path,) and ctx.scope_files == (path,), ctx.files
    assert any("|| true" in text for _, text in ctx.added[path]), ctx.added
    assert "|| true" in ctx.read_head(path) and "|| true" not in ctx.read_base(path)
    package = diffs_mod.review_package(api, "HEAD~1")
    assert "warden-svc-api.yml" in package and "|| true" in package, package
    assert "other.yml" not in package, package
    assert _warden(api, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0

    # The root enrollment's own review is unchanged: it reads the file at its
    # own path, and no `../` path.
    assert diffs_mod.range_paths(mono, "HEAD~1", "HEAD") == (
        ".github/workflows/other.yml", ".github/workflows/warden-svc-api.yml")


# ── the install job refuses a missing repo.yaml ─────────────────────────────

@pytest.mark.parametrize("present", [True, False], ids=["present", "absent"])
@pytest.mark.parametrize("below", ["", "svc/api"], ids=["root", "subdirectory"])
def test_the_install_job_refuses_a_missing_enrollment_repo_yaml_with_did_not_run(
        below, present, tmp_path):
    doc = _workflow(("python",), below)
    step = _step(doc, "install", REPO_YAML)
    path = f"{below}/repo.yaml" if below else "repo.yaml"
    if below:
        assert step["working-directory"] == "."
    if present:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("version: 1\n")
    result = _run_step(doc, "install", step, {}, tmp_path)
    if present:
        assert result.returncode == 0, result
        assert result.stderr == "", result.stderr
    else:
        assert result.returncode == 2, result
        assert f"warden gate: {path} is missing" in result.stderr, result.stderr
        assert "the gate DID NOT RUN." in result.stderr, result.stderr
    # Below the root, every `run:` step that runs in the enrollment directory
    # comes after the check; the private check runs before it at the root of
    # the workspace. At the git root no step needs a directory the checkout
    # creates.
    steps = _steps(doc, "install")
    at = steps.index(step)
    for i, other in enumerate(steps):
        if below and "run" in other and other.get("working-directory") != ".":
            assert i > at, other.get("name")


# ── certify credits no nested workflow below the git root ───────────────────

def _nested_gate(tmp_path: Path, monkeypatch, keep_root: bool) -> tuple[Path, Path]:
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    gate = mono / ".github" / "workflows" / "warden-svc-api.yml"
    nested = api / ".github" / "workflows" / "never-runs.yml"
    nested.parent.mkdir(parents=True)
    nested.write_text(gate.read_text())
    if not keep_root:
        gate.unlink()
    _git(mono, "add", "-A")
    _git(mono, "commit", "-qm", "enroll")
    assert _warden(api, monkeypatch, "review", "--base", "HEAD~1", "--no-comment") == 0
    return mono, api


def test_a_subdirectory_enrollment_with_only_a_nested_workflow_fails_g04(
        tmp_path, monkeypatch, capsys):
    """GitHub never runs `svc/api/.github/workflows/never-runs.yml`."""
    from warden import certify as certify_mod
    _, api = _nested_gate(tmp_path, monkeypatch, keep_root=False)
    code, report = _certify(api, monkeypatch, capsys)
    assert code != 0 and "LEVEL 3" not in report, report
    for check in ("G-04", "V-02"):
        assert "✗" in _check_line(report, check), report
    passed, detail = certify_mod._run_check(
        {"type": "ci_step_enforced", "pattern": "warden review"}, api)
    assert not passed, detail


def test_a_subdirectory_enrollment_with_its_root_gate_passes_g04(
        tmp_path, monkeypatch, capsys):
    _, api = _nested_gate(tmp_path, monkeypatch, keep_root=True)
    code, report = _certify(api, monkeypatch, capsys)
    assert code == 0 and "certification: LEVEL 3 (Reviewed)" in report, report
    for check in ("G-04", "V-02"):
        line = _check_line(report, check)
        assert "✓" in line and "warden-svc-api.yml" in line, report
        assert "never-runs.yml" not in line, report


def test_hello_svc_certified_standalone_as_ci_runs_it_reaches_level_3(tmp_path):
    """The `portability proof` job's certify step, run as CI runs it: the
    example copied out as its own repository, where its `.github/workflows` is
    the git root's."""
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = ci["jobs"]["enrollment"]
    assert job["name"] == "portability proof"
    [step] = [s for s in job["steps"] if "warden certify --level 3" in s.get("run", "")
              and "example-standalone.sh" in s["run"]]
    assert "working-directory" not in step, step
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]], cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, **_AMBIENT_GIT_CONFIG, "RUNNER_TEMP": str(tmp_path),
             "GITHUB_WORKSPACE": str(ROOT)}, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "certification: LEVEL 3 (Reviewed)" in result.stdout, result.stdout
    copy = tmp_path / "hello-svc"
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=copy,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert Path(top).resolve() == copy.resolve()
    assert "✓ G-04" in result.stdout and "ci.yml" in _check_line(result.stdout, "G-04")


# ── the path a subdirectory review lists its own gate by ────────────────────

def test_a_unicode_enrollment_path_tiers_its_own_root_gate_high(tmp_path, monkeypatch):
    """The HIGH tier is decided by comparing the path with the one
    `gate_workflows.root_gate_path` gives this enrollment, so a prefix holding
    a character no file-name pattern allows is tiered like any other. `init`
    refuses such a prefix; renaming a directory after it reaches this state,
    and `review` and `explain` take the prefix from git, not from `init`."""
    from warden import gate_workflows as gw

    mono = _mono(tmp_path)
    assert _warden(mono / "svc" / "api", monkeypatch, "init") == 0
    _git(mono, "add", "-A")
    _git(mono, "commit", "-qm", "enroll svc/api")
    (mono / "café").mkdir()
    _git(mono, "mv", "svc/api", "café/api")
    api = mono / "café" / "api"
    assert gw.git_location(api)[1] == "café/api"
    listed = gw.root_gate_path("café/api")
    assert listed == "../../.github/workflows/warden-café-api.yml", listed

    tier = config_mod.classify(config_mod.load(api), listed)
    assert tier.tier == "HIGH", tier
    assert tier.glob == "<root gate workflow>", tier


def test_another_enrollments_root_gate_is_not_tiered_high_for_this_one(
        tmp_path, monkeypatch):
    """Only the file that IS this enrollment's gate is HIGH before the globs
    are read. Another enrollment's gate, and a path climbing past the git root,
    are tiered by `risk_tiers` like any other path."""
    mono = _mono(tmp_path)
    api = mono / "svc" / "api"
    assert _warden(api, monkeypatch, "init") == 0
    config = config_mod.load(api)
    own = config_mod.classify(config, "../../.github/workflows/warden-svc-api.yml")
    assert own.tier == "HIGH" and own.glob == "<root gate workflow>", own
    for path in ("../../.github/workflows/warden-svc-web.yml",
                 "../../../../.github/workflows/warden-svc-api.yml",
                 "../../.github/workflows/warden-SOMETHING-ELSE.yml"):
        tier = config_mod.classify(config, path)
        assert tier.glob != "<root gate workflow>" and tier.tier != "HIGH", (path, tier)


# ── the toolchain the gate declares and installs none of ────────────────────

EXAMPLE_GATE = ROOT / "examples" / "hello-svc" / ".github" / "workflows" / "ci.yml"

def _invoked_binaries(command: str) -> set[str]:
    """Every binary COMMAND puts at the head of a pipeline or a list.

    Derived from the command text rather than listed by hand: that is what
    makes the declaration checkable. A verify command that starts invoking
    something new is caught by the same reading that produced the old answer.

    No table of shell builtins to keep in step with anything — a head that
    cannot be a program name is the shell's own, which is what `[ $? -eq 5 ]`,
    the exit-code fixup the python scope ends with, is. Anything that COULD be
    a program name counts, so a new spelling errs towards demanding a
    declaration rather than towards missing one.

    It reads the HEAD of each part, which is the conservative direction: `sh
    -c 'npm ci'` reports `sh`, so a wrapper is declared as itself rather than
    silently resolving to what it wraps. What it must never do is report
    FEWER binaries than the command runs, which is why a lone `&` is in the
    split alternation beside `&&` — round 1 (F3) found `go vet ./... & npm
    test` reporting `{'go'}` alone, and `test_every_separator_a_verify_
    command_can_use_is_split` is the control.
    """
    found = set()
    for part in re.split(r"\|\||&&|[|;&\n]", command):
        try:
            words = shlex.split(part)
        except ValueError:
            continue
        while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
            words.pop(0)
        if words and re.fullmatch(r"[A-Za-z][\w.+-]*", words[0]):
            found.add(words[0])
    return found


def _preflight_steps(doc: dict, job: str) -> list[dict]:
    return [s for s in doc["jobs"][job]["steps"]
            if s.get("name") == enroll.TOOLCHAIN_STEP]


def _declared_toolchain(doc: dict, job: str) -> set[tuple[str, str]]:
    """The (binary, scope) pairs a gate's pre-flight step declares. The EMPTY
    set where the job renders no pre-flight — which is a real state since the
    round 1 (F0) repair, not an oversight: a gate that installs everything
    its scopes run has nothing left to refuse on, and renders no step rather
    than one whose body cannot fail."""
    steps = _preflight_steps(doc, job)
    if not steps:
        return set()
    assert len(steps) == 1, (
        f"{job} has {len(steps)} toolchain pre-flight steps, not 1")
    assert steps[0].get("shell") == "bash", steps[0]
    lines = [ln for ln in steps[0]["run"].splitlines()
             if ln.strip().startswith("for pair in ")]
    assert len(lines) == 1, steps[0]["run"]
    return set(re.findall(r'"([\w.+-]+):([\w.+-]+)"', lines[0]))


def _toolbin(tmp_path: Path, tools: tuple[str, ...]) -> Path:
    """A PATH holding exactly TOOLS — plus `bash`, which is the step's own
    interpreter and not a toolchain claim. The narrow PATH is the point: a
    check run against the developer's own PATH proves nothing about a runner
    image that dropped the toolchain."""
    bindir = tmp_path / "toolbin"
    bindir.mkdir(exist_ok=True)
    (bindir / "bash").symlink_to(shutil.which("bash"))
    for tool in tools:
        (bindir / tool).write_text("#!/bin/sh\n")
        (bindir / tool).chmod(0o755)
    return bindir


@pytest.mark.parametrize("langs", [("python",), ("go",), ("node", "go"),
                                   enroll.ALL_LANGUAGES])
def test_the_generated_gate_accounts_for_every_binary_its_scopes_invoke(
        langs, tmp_path):
    """THE invariant, and it is a partition rather than a single set: every
    binary the verify commands init writes will invoke is either INSTALLED by
    a step in the same job or DECLARED in the pre-flight for the scope that
    needs it — and never both, which is the round 1 (F0) repair. Derived from
    the commands, so a new language or a changed command cannot enroll with a
    binary in neither half."""
    doc = _workflow(langs)
    chosen = [lang for lang in enroll.LANGUAGES if lang.name in langs]
    invoked = {(tool, lang.name)
               for lang in chosen
               for cmd in enroll.verify_commands(tmp_path, lang)
               for tool in _invoked_binaries(cmd)}
    assert invoked, langs
    declared = _declared_toolchain(doc, "verify")
    installed = _gate_installs(enroll.render_workflow(tuple(chosen)))

    unaccounted = {p for p in invoked if p not in declared and p[0] not in installed}
    assert unaccounted == set(), (
        f"{langs}: the gate neither installs nor declares {sorted(unaccounted)}")
    assert declared <= invoked, (
        f"{langs}: the pre-flight declares {sorted(declared - invoked)}, which "
        "no verify command runs")
    assert {tool for tool, _ in declared} & installed == set(), (
        f"{langs}: a declared binary is installed in the same job")

    # And it runs before the first scope, not after a scope has already
    # failed on an exit 127 the reader then has to diagnose.
    names = [s.get("name") for s in _steps(doc, "verify")]
    if enroll.TOOLCHAIN_STEP in names:
        assert names.index(enroll.TOOLCHAIN_STEP) < min(
            i for i, n in enumerate(names) if n and n.startswith("warden verify"))
    else:
        assert declared == set(), "a pre-flight was dropped with pairs still to check"


def test_every_language_declares_what_its_verify_commands_invoke(tmp_path):
    """`Language.tools` is the declaration; the commands are the behaviour."""
    for lang in enroll.LANGUAGES:
        invoked = set().union(*(_invoked_binaries(cmd) for cmd in
                                enroll.verify_commands(tmp_path, lang)))
        assert set(lang.tools) == invoked, lang.name
    # A language without the field does not import, which is why no test can
    # be the only thing standing between a new one and an undeclared runner.
    with pytest.raises(TypeError):
        enroll.Language("rust", "Cargo.toml", ("**/*.rs",))


@pytest.mark.parametrize("absent, scope", [("go", "go"), ("npm", "node")])
def test_the_preflight_exits_2_naming_the_absent_binary(absent, scope, tmp_path):
    """Executed, not grepped: the step is what a consumer's runner runs."""
    doc = _workflow(enroll.ALL_LANGUAGES)
    step = _step(doc, "verify", enroll.TOOLCHAIN_STEP)
    bindir = _toolbin(tmp_path, tuple(t for t in ("go", "npm") if t != absent))
    result = _run_step(doc, "verify", step, {**_AMBIENT_GIT_CONFIG, "PATH": str(bindir)}, tmp_path)
    assert result.returncode == 2, result
    assert f"warden gate: {absent} is not on PATH" in result.stderr
    assert f"the {scope} verify scope could not be evaluated" in result.stderr
    assert "DID NOT RUN" in result.stderr
    assert f"installs {absent}" in result.stderr, "no fix is named"
    assert f"PATH={bindir}" in result.stderr, "the PATH it searched is unstated"


def test_the_preflight_names_every_absent_binary_not_just_the_first(tmp_path):
    doc = _workflow(enroll.ALL_LANGUAGES)
    result = _run_step(doc, "verify", _step(doc, "verify", enroll.TOOLCHAIN_STEP),
                       {**_AMBIENT_GIT_CONFIG, "PATH": str(_toolbin(tmp_path, ()))}, tmp_path)
    assert result.returncode == 2, result
    for tool in ("go", "npm"):
        assert f"warden gate: {tool} is not on PATH" in result.stderr, tool
    assert "installs npm go" in result.stderr


def test_the_preflight_passes_and_says_nothing_when_the_toolchain_is_there(
        tmp_path):
    doc = _workflow(enroll.ALL_LANGUAGES)
    bindir = _toolbin(tmp_path, ("go", "npm"))
    result = _run_step(doc, "verify", _step(doc, "verify", enroll.TOOLCHAIN_STEP),
                       {**_AMBIENT_GIT_CONFIG, "PATH": str(bindir)}, tmp_path)
    assert result.returncode == 0, result
    assert result.stdout == "" and result.stderr == ""


def test_the_example_gate_declares_nothing_it_installs_either():
    """Round 2 (N2): the never-installed-AND-declared partition was pinned
    for the generated gate alone, so round 1's F0 shape could be reintroduced
    on the OTHER shipped consumer surface with all fourteen toolchain tests
    green.
    Proven by the re-reviewer's mutation — adding `astral-sh/setup-uv` to the
    example gate makes its `uv:app` pair dead, and nothing noticed."""
    doc = yaml.safe_load(EXAMPLE_GATE.read_text())
    installed = _gate_installs(EXAMPLE_GATE.read_text())
    declared = {tool for tool, _ in _declared_toolchain(doc, "gate")}
    assert declared, "the example gate declares no toolchain at all"
    assert declared & installed == set(), (
        f"the example gate requires {sorted(declared & installed)}, which a "
        "step in the same job installs — a pair that cannot fire")


def test_the_shipped_example_gate_declares_the_toolchain_it_runs():
    """The other consumer surface: the gate `examples/hello-svc` ships is the
    file a consumer copies, and it installs no toolchain either — `uv` is on
    no GitHub-hosted image at all. Same derivation, against the real tree."""
    doc = yaml.safe_load(EXAMPLE_GATE.read_text())
    example = ROOT / "examples" / "hello-svc"
    config = config_mod.load(example)
    expected = {(tool, scope)
                for scope, steps in config.verify.items()
                for step in steps
                for tool in _invoked_binaries(step.run)}
    assert expected, "the example declares no verify command"
    assert _declared_toolchain(doc, "gate") == expected
    names = [s.get("name") for s in doc["jobs"]["gate"]["steps"]]
    assert names.index(enroll.TOOLCHAIN_STEP) < names.index(
        "deterministic checks declared in repo.yaml")


def test_the_example_gates_preflight_exits_2_naming_what_is_absent(tmp_path):
    doc = yaml.safe_load(EXAMPLE_GATE.read_text())
    step = _step(doc, "gate", enroll.TOOLCHAIN_STEP)
    bindir = _toolbin(tmp_path, ("python3",))
    result = _run_step(doc, "gate", step,
                       {**_AMBIENT_GIT_CONFIG, "PATH": str(bindir)}, tmp_path)
    assert result.returncode == 2, result
    assert "warden gate: uv is not on PATH" in result.stderr
    assert "python3" not in result.stderr.split("PATH=")[0]
    assert "DID NOT RUN" in result.stderr


def _gate_actions() -> dict[str, tuple[str, ...]]:
    """Every action a shipped consumer gate uses, and the binaries it puts on
    PATH. A WHITELIST, which is the whole repair: round 1 (F1) found this
    guard written as a four-name DENYLIST of `actions/setup-*`, which was
    green while the template installed a declared tool through
    `astral-sh/setup-uv` and would have stayed green for any installer nobody
    thought to list. An action missing from this map reddens the guard
    whatever it is called, and adding a row is the deliberate act."""
    return {"actions/checkout": (),
            "actions/upload-artifact": (),
            "actions/download-artifact": (),
            "astral-sh/setup-uv": ("uv",)}


def _gate_installs(text: str) -> set[str]:
    """The binaries the actions in a gate workflow install, refusing one this
    suite has never classified rather than reading it as installing nothing.

    It reads EVERY `uses:` value, not only an `owner/repo@ref` one: round 2
    (N4) found the narrower pattern silently skipping a local composite
    action (`uses: ./.github/actions/setup-toolchain`) and a container action
    (`uses: docker://golang:1.22`), either of which can install a whole
    toolchain. Those spellings now land in `unknown` and refuse, which is the
    direction the promise above requires."""
    actions = _gate_actions()
    used = {value.split("@")[0]
            for value in re.findall(r"^\s*-?\s*uses:\s*(\S+)", text, re.M)}
    unknown = sorted(used - set(actions))
    assert unknown == [], (
        f"a shipped consumer gate uses {unknown}, which `_gate_actions` does "
        "not classify — say what it installs before the toolchain guards can "
        "mean anything")
    return {tool for action in used for tool in actions[action]}


def test_no_shipped_consumer_gate_installs_a_consumer_toolchain():
    """The decision, pinned: the gate installs its OWN runtime and no
    toolchain of the enrolled repository's. A setup action rendered into a
    consumer's gate would pin a commit sha on their behalf with no freshness
    behind it — this repo's dependabot watches `/` and `/examples/hello-svc`,
    not `warden/templates/init/`. If that decision is ever reversed, reverse
    it here too and say why in the bead."""
    consumer_tools = {tool for lang in enroll.LANGUAGES for tool in lang.tools
                      if tool not in enroll.GATE_INSTALLS}
    assert consumer_tools, "no consumer toolchain to be installed by mistake"
    for text, where in (((enroll.TEMPLATES / "workflow.yml").read_text(), "the init template"),
                        (EXAMPLE_GATE.read_text(), "the example gate")):
        installed = _gate_installs(text)
        assert installed & consumer_tools == set(), (
            f"{where} installs {sorted(installed & consumer_tools)}: the gate "
            "declares its toolchain and installs no consumer one")
        assert installed <= set(enroll.GATE_INSTALLS), (
            f"{where} installs {sorted(installed)}, which is more than the "
            f"platform runtime {list(enroll.GATE_INSTALLS)}")


def test_the_preflight_never_carries_a_pair_the_gate_already_installs():
    """The round 1 (F0) regression. `uv` is installed by the verify job's own
    `astral-sh/setup-uv`, so a `uv:python` pair was a check that could not go
    red: delete it, or break uv detection, and no generated gate noticed. A
    declared pair must name something that can still be absent."""
    assert "uv" in enroll.GATE_INSTALLS, "the fixture of this test moved"
    for langs in (("python",), ("python", "go"), enroll.ALL_LANGUAGES):
        doc = _workflow(langs)
        installed = _gate_installs(enroll.render_workflow(
            tuple(lang for lang in enroll.LANGUAGES if lang.name in langs)))
        declared = {tool for tool, _ in _declared_toolchain(doc, "verify")}
        assert declared & installed == set(), (
            f"{langs}: the pre-flight requires {sorted(declared & installed)}, "
            "which a step in the same job installs — a pair that cannot fire")

    # A python-only enrollment therefore renders NO pre-flight at all, rather
    # than a step whose whole body is a check that cannot fail.
    assert enroll.render_toolchain_step(
        tuple(lang for lang in enroll.LANGUAGES if lang.name == "python")) == ""
    assert _preflight_steps(_workflow(("python",)), "verify") == [], (
        "a python-only gate renders a pre-flight with nothing it can refuse on")


def _adopting_snippets() -> tuple[dict, dict, str]:
    """Adopting.md's hand-copied `repo.yaml` and gate job: both parsed, plus
    the gate's raw text for the `uses:` read.

    The page is the THIRD consumer-gate surface — the one copied by hand, and
    the one `.github/workflows/ci.yml` names as the prescription hello-svc
    follows — so it is read as YAML rather than grepped: a snippet that no
    longer parses is a snippet nobody can copy. The gate block is a FRAGMENT
    of a `jobs:` mapping (the page shows the job, not a whole workflow), so
    it is wrapped rather than assumed to carry its own `jobs:` key.
    """
    text = (WIKI / "Adopting.md").read_text()
    blocks = re.findall(r"```(yaml|sh)\n(.*?)```", text, re.S)
    repo_yaml = next(yaml.safe_load(body) for kind, body in blocks
                     if kind == "yaml" and "verify:" in body)
    gate_text = next(body for kind, body in blocks
                     if kind == "yaml" and "warden gate" in body)
    doc = yaml.safe_load(gate_text)
    return repo_yaml, doc if "jobs" in doc else {"jobs": doc}, gate_text


def test_the_adopting_page_declares_the_toolchain_its_own_verify_runs():
    """The third consumer-gate surface, held to the same partition as the two
    the platform generates.

    Three surfaces hand a consumer a gate job: `warden init`'s generated
    workflow, `examples/hello-svc/.github/workflows/ci.yml`, and this page's
    section 4. A toolchain pre-flight on the first two alone would leave the
    third — the one a consumer copies BY HAND — going straight from
    `setup-uv` to `warden verify --scope app`, prescribing a gate the
    platform no longer ships. Nothing else would go red: every other
    toolchain guard reads a file the platform WRITES, and this one is prose.

    DERIVED FROM THE PAGE'S OWN `repo.yaml`, both directions, exactly as
    `test_the_shipped_example_gate_declares_the_toolchain_it_runs` is
    derived from hello-svc's: every binary the page's verify commands invoke
    is either installed by a step in the same job or declared in the
    pre-flight, and never both. So the page cannot drift from itself either —
    editing its `repo.yaml` to run a new binary reddens here.
    """
    repo_yaml, gate, gate_text = _adopting_snippets()
    names = [s.get("name") for s in gate["jobs"]["gate"]["steps"]]
    assert enroll.TOOLCHAIN_STEP in names, (
        "Adopting.md section 4 hands a consumer a gate job with no toolchain "
        "pre-flight, while `warden init` and examples/hello-svc both render "
        "one — so the page a consumer copies by hand prescribes a gate the "
        "platform does not ship")
    assert names.index(enroll.TOOLCHAIN_STEP) < min(
        i for i, n in enumerate(names) if n == "warden verify"), (
        "the page's pre-flight runs after a scope has already failed on an "
        "exit 127 the reader then has to diagnose")

    invoked = {(tool, scope)
               for scope, cmds in repo_yaml["verify"].items()
               for cmd in cmds
               for tool in _invoked_binaries(cmd["run"])}
    assert invoked, "the page's repo.yaml declares no verify command"
    declared = _declared_toolchain(gate, "gate")
    installed = _gate_installs(gate_text)

    unaccounted = {p for p in invoked
                   if p not in declared and p[0] not in installed}
    assert unaccounted == set(), (
        f"the page's gate neither installs nor declares {sorted(unaccounted)}, "
        "which its own repo.yaml runs")
    assert declared <= invoked, (
        f"the page's pre-flight declares {sorted(declared - invoked)}, which "
        "no verify command on the page runs — a pair that cannot fire")
    assert {tool for tool, _ in declared} & installed == set(), (
        f"the page requires {sorted({t for t, _ in declared} & installed)}, "
        "which a step in the same job installs — the round 1 (F0) shape")

    # And the CONTRACT, not just the step: the page says the gate declares
    # and installs nothing of the consumer's, which is the half a reader needs
    # in order to know the absent step is a decision rather than a gap.
    section = (WIKI / "Adopting.md").read_text()
    section = section[section.index("## 4 · Add the CI job"):]
    section = " ".join(section[:section.index("\n## 5 ")].split())
    for needle in ("declares its toolchain and installs no consumer one",
                   "DID NOT RUN", "exits **2**"):
        assert needle in section, (
            f"Adopting.md section 4 does not state the declare-not-install "
            f"contract ({needle!r}): a consumer reading it cannot tell "
            "whether the gate will install their Go or Node for them")


def test_the_template_header_states_the_conditionality_the_render_has():
    """The header the platform WRITES INTO other people's repositories is held
    to `render_toolchain_step`'s conditionality.

    The header once claimed a pre-flight step unconditionally while a
    python-only enrollment renders none. The sentence reads "WHERE THERE IS
    ONE TO DECLARE" and names the python-only case outright — prose that
    nothing but this test holds true, and this
    file is not documentation ABOUT the platform, it is a file the platform
    writes into every enrolled repository, so drift here reaches every
    consumer with no page for them to cross-check it against.

    DERIVED FROM THE RENDER, in both directions, because each direction is a
    different failure. If the conditionality is REMOVED (every enrollment
    renders a step), the header's python-only sentence becomes a false
    absolute. If the step stops rendering at all, "appears below"
    names a step no generated gate has. The pairing is what makes this a pin
    rather than a grep: the header's two claims are asserted against the set
    of language sets that actually render one.
    """
    # FLATTENED: the header is a hard-wrapped comment block, so a claim this
    # guard reads as one sentence is a rewrap away from being unreadable to
    # it — and a guard that goes green on a reflow is the shape this bead is
    # about.
    header = " ".join(
        line.lstrip("#").strip()
        for line in (enroll.TEMPLATES / "workflow.yml").read_text().splitlines()
        if line.startswith("#")).replace("  ", " ")
    assert "WHERE THERE IS ONE TO DECLARE" in header, (
        "the template header no longer scopes the pre-flight step to an "
        "enrollment that has a toolchain to declare, so it claims one "
        "unconditionally again")
    assert enroll.TOOLCHAIN_STEP in header, (
        "the template header no longer names the pre-flight step it says "
        "appears below, so a reader cannot join the claim to the step")
    assert "A python-only enrollment has NO such step" in header, (
        "the template header no longer names the python-only case as the one "
        "with no pre-flight step")

    # Every non-empty language set `warden init` can enroll, and whether it
    # renders a step. `render_workflow` is the caller, so this reads the
    # rendered workflow rather than the helper alone.
    rendered = {}
    for size in range(1, len(enroll.LANGUAGES) + 1):
        for combo in itertools.combinations(enroll.LANGUAGES, size):
            names = tuple(lang.name for lang in combo)
            rendered[names] = enroll.TOOLCHAIN_STEP in enroll.render_workflow(combo)
    assert rendered, "no language set to enroll, so this proves nothing"

    stepless = sorted(k for k, has in rendered.items() if not has)
    assert stepless == [("python",)], (
        f"the generated gate renders no toolchain pre-flight for {stepless}, "
        "and the template header names the python-only enrollment as the only "
        "such case — the header ships verbatim into every enrolled repository, "
        "so fix them together")
    assert any(rendered.values()), (
        "no language set renders a toolchain pre-flight at all, so the "
        "header's 'the step ... appears below' names a step that never "
        "appears in a generated gate")


def test_every_separator_a_verify_command_can_use_is_split():
    """The round 1 (F3) regression: a lone `&` was not in the alternation, so
    `go vet ./... & npm test` reported `go` alone and `npm` could have gone
    undeclared with every guard green."""
    assert _invoked_binaries("go vet ./... & npm test") == {"go", "npm"}
    assert _invoked_binaries("npm ci && npm test") == {"npm"}
    assert _invoked_binaries("go build | tee log; go test ./...") == {"go", "tee"}
    assert _invoked_binaries("uv run pytest -q || [ $? -eq 5 ]") == {"uv"}


# ── the proportionate tier's enforcement, shipped to consumers ──────────────

CARRIES = "v9.0.0"  # any release past LACKS_CLASSIFY_COMMAND


def _ci_yml_step(name: str) -> dict:
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    found = [s for job in doc["jobs"].values() for s in job.get("steps") or []
             if s.get("name") == name]
    assert len(found) == 1, f"ci.yml holds {len(found)} steps named {name!r}"
    return found[0]


@pytest.mark.parametrize("prefix", ["", "svc/api"])
def test_init_emits_the_classify_enforce_step_this_repos_ci_runs(prefix):
    """Only this repo's ci.yml bound a light round. The gate init writes now
    runs ci.yml's `run:` byte for byte (installed `warden` for the shim),
    after the review and before certify."""
    chosen = tuple(lang for lang in enroll.LANGUAGES if lang.name == "python")
    doc = yaml.safe_load(enroll.render_workflow(chosen, prefix, pin=CARRIES))
    names = [s.get("name") for s in _steps(doc, "gate")]
    assert names.index("warden review") < names.index(enroll.CLASSIFY_STEP) \
        < names.index("warden certify"), names
    live = _ci_yml_step(enroll.CLASSIFY_STEP)["run"]
    assert ".warden/bin/warden attest classify --enforce" in live
    assert _step(doc, "gate", enroll.CLASSIFY_STEP)["run"] == live.replace(
        ".warden/bin/warden", "warden")


def _run_steps(doc: dict) -> list[dict]:
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []
            if "run" in s]


def _gate_workflows(prefix: str) -> dict[str, dict]:
    """The three copies of the gate that must stay one text: the workflow
    `warden init` renders at PREFIX, hello-svc's, and this repo's own."""
    chosen = tuple(lang for lang in enroll.LANGUAGES if lang.name == "python")
    docs = {"warden init": yaml.safe_load(
        enroll.render_workflow(chosen, prefix, pin=CARRIES))}
    for rel in (".github/workflows/ci.yml",
                "examples/hello-svc/.github/workflows/ci.yml"):
        docs[rel] = yaml.safe_load((ROOT / rel).read_text())
    return docs


@pytest.mark.parametrize("prefix", ["", "svc/api"])
def test_no_run_block_pastes_a_github_expression_into_the_shell(prefix):
    """A `${{ github.* }}` inside `run:` is substituted into the script text
    before the shell parses it, so a ref name carrying `$(...)` or a quote
    would be executed; every one reaches the shell through `env:` and a quoted
    variable instead. The classify step is the one that pasted
    `github.base_ref`, so it is pinned to read `BASE_REF` from `env:` and
    pass it quoted."""
    for where, doc in _gate_workflows(prefix).items():
        pasted = [s.get("name") for s in _run_steps(doc)
                  if re.search(r"\$\{\{\s*github\.", s["run"])]
        assert not pasted, f"{where}: `${{{{ github.* }}}}` pasted into run: {pasted}"
        classify = [s for s in _run_steps(doc)
                    if s.get("name") == enroll.CLASSIFY_STEP]
        assert len(classify) == 1, where
        assert classify[0]["env"]["BASE_REF"] == "${{ github.base_ref }}", where
        assert '"origin/$BASE_REF"' in classify[0]["run"], where


@pytest.mark.parametrize("prefix", ["", "svc/api"])
def test_the_classify_step_has_no_fork_branch_and_no_exit_0_path(prefix):
    """The classify step had a fork branch that exited 0: dead in the emitted
    gate, where the install job's credential step exits 2 on a fork PR before
    this step runs, and a pass path in the hello-svc and ci.yml copies, where
    the verdict needs no credential and a fork PR is classified like any
    other. A step that cannot run does not get to claim a pass, so no copy
    carries the branch or any `exit 0`."""
    for where, doc in _gate_workflows(prefix).items():
        classify = [s for s in _run_steps(doc)
                    if s.get("name") == enroll.CLASSIFY_STEP]
        assert len(classify) == 1, where
        run = classify[0]["run"]
        assert "fork" not in run, f"{where}: the classify step still has a fork branch:\n{run}"
        assert not re.search(r"\bexit\s+0\b", run), (
            f"{where}: the classify step has an exit 0 path:\n{run}")


def test_the_classify_step_is_pinned_to_the_release_the_gate_installs():
    """The gate installs the pinned warden: a release without the command
    gets no step, never one its gate rejects; a later one gets a step that
    parses on the CLI it is cut from."""
    chosen = tuple(lang for lang in enroll.LANGUAGES if lang.name == "python")
    lacks = yaml.safe_load(enroll.render_workflow(
        chosen, pin=enroll.LACKS_CLASSIFY_COMMAND))
    assert enroll.CLASSIFY_STEP not in [s.get("name") for s in _steps(lacks, "gate")]
    assert "@@" not in enroll.render_workflow(chosen, pin=enroll.LACKS_CLASSIFY_COMMAND)
    carries = yaml.safe_load(enroll.render_workflow(chosen, pin=CARRIES))
    argvs = [a for a in _warden_argvs(carries) if a[:1] == ["attest"]]
    assert argvs == [["attest", "classify", "--enforce", "--base",
                      "origin/$BASE_REF"]], argvs
    assert cli.build_parser().parse_args(argvs[0]).enforce is True
    assert enroll.render_workflow(chosen) == enroll.render_workflow(
        chosen, pin=f"v{enroll.__version__}")
