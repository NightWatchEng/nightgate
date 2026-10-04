"""A release is installable from its tag alone.

Two halves. The tag check: a `v*` tag must name exactly `warden.__version__`,
because every consumer's `platform.pin` is compared against that string. The
wheel install: the built wheel, installed into a fresh venv outside any
checkout, runs warden and cage against a fixture repo, which proves the data
files those commands read at runtime ship in the package.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

from warden import __version__

ROOT = Path(__file__).resolve().parent.parent
TAG_CHECK = ROOT / "scripts" / "release-tag-check.py"


def _tag_check(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TAG_CHECK), *args],
                          capture_output=True, text=True, timeout=60)


def test_release_tag_check_accepts_the_tag_that_names_the_version():
    result = _tag_check(f"v{__version__}")
    assert result.returncode == 0, result.stderr
    assert f"matches warden {__version__}" in result.stdout


def test_release_tag_check_refuses_a_tag_that_differs_from_the_version():
    result = _tag_check("v999.0.0")
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert f"warden.__version__ is {__version__}" in result.stderr


def test_release_tag_check_refuses_a_name_that_is_not_a_v_tag():
    for tag in (__version__, "v", "release-2"):
        result = _tag_check(tag)
        assert result.returncode == 2, (tag, result.stdout, result.stderr)


def test_release_tag_check_reads_the_package_under_its_root(tmp_path):
    pkg = tmp_path / "warden"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('__version__ = "3.1.4"\n')
    assert _tag_check("v3.1.4", "--root", str(tmp_path)).returncode == 0
    assert _tag_check(f"v{__version__}", "--root", str(tmp_path)).returncode == 1

    empty = tmp_path / "empty"
    empty.mkdir()
    result = _tag_check(f"v{__version__}", "--root", str(empty))
    assert result.returncode == 2, (
        "a root with no warden package must not borrow the version of the "
        f"warden installed in the running interpreter: {result.stdout}")


def test_ci_refuses_a_release_tag_that_is_not_the_version():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    triggers = ci.get("on", ci.get(True))
    assert "v*" in triggers["push"]["tags"], "CI no longer runs on v* tag pushes"
    job = ci["jobs"]["release-tag"]
    assert job["if"] == "startsWith(github.ref, 'refs/tags/v')"
    runs = [s for s in job["steps"] if "run" in s]
    check = [s for s in runs if "scripts/release-tag-check.py" in s["run"]]
    assert check, "the release-tag job no longer runs the tag check"
    assert check[0]["env"]["TAG"] == "${{ github.ref_name }}"
    assert '"$TAG"' in check[0]["run"]
    for neutralizer in ("|| true", "|| :", "; true", "set +e", "|| exit 0"):
        assert neutralizer not in check[0]["run"], check[0]["run"]


def _clean_env(venv: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME",
                        "UV_PROJECT_ENVIRONMENT")}
    env["PATH"] = f"{venv / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    return env


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=600)


def test_the_built_wheel_runs_warden_and_cage_outside_any_checkout(tmp_path):
    uv = os.environ.get("UV") or shutil.which("uv")
    assert uv, "uv is required to build and install the wheel"
    assert ROOT not in tmp_path.resolve().parents

    dist = tmp_path / "dist"
    built = subprocess.run([uv, "build", "--wheel", "--out-dir", str(dist), str(ROOT)],
                           capture_output=True, text=True, timeout=600)
    assert built.returncode == 0, built.stderr
    wheels = list(dist.glob("*.whl"))
    assert [w.name for w in wheels] == [f"warden-{__version__}-py3-none-any.whl"]

    tracked = subprocess.run(["git", "ls-files", "warden", "cage"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
    data = {p for p in tracked.stdout.split() if not p.endswith(".py")}
    assert {"cage/run.sh", "warden/certification/baseline.yaml",
            "warden/guardrails/catalog.yaml",
            "warden/schemas/repo.schema.json",
            "warden/templates/init/workflow.yml",
            "warden/templates/init/skills-policy.md"} <= data
    shipped = set(zipfile.ZipFile(wheels[0]).namelist())
    assert data <= shipped, f"the wheel is missing data files: {sorted(data - shipped)}"

    venv = tmp_path / "venv"
    made = subprocess.run([uv, "venv", "--python", sys.executable, str(venv)],
                          capture_output=True, text=True, timeout=300)
    assert made.returncode == 0, made.stderr
    installed = subprocess.run(
        [uv, "pip", "install", "--python", str(venv / "bin" / "python"), str(wheels[0])],
        capture_output=True, text=True, timeout=600)
    assert installed.returncode == 0, installed.stderr

    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "examples" / "hello-svc", repo,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    for git in (["init", "-q", "-b", "main"], ["add", "-A"],
                ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"]):
        subprocess.run(["git", *git], cwd=repo, check=True, capture_output=True)
    cage_dir = tmp_path / "cagecfg"
    cage_dir.mkdir()
    (cage_dir / "cage.toml").write_text(
        '[project]\nname = "sampleproj"\ngithub = "acme/sampleproj"\n'
        'live_checkout = "/work/sampleproj"\nworktree = "/work/sampleproj-run"\n\n'
        '[triggers.manual]\n\n[gate]\nforbidden_paths = []\n')

    env = _clean_env(venv)
    where = _run([str(venv / "bin" / "python"), "-c",
                  "import warden, cage; print(warden.__file__); print(cage.__file__)"],
                 repo, env)
    assert where.returncode == 0, where.stderr
    for line in where.stdout.split():
        assert Path(line).resolve().is_relative_to(venv.resolve()), (
            f"the package imported from {line}, not from the installed wheel")

    version = _run(["warden", "--version"], repo, env)
    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == f"warden {__version__}"

    explain = _run(["warden", "explain"], repo, env)
    assert explain.returncode == 0, explain.stdout + explain.stderr
    assert "hello-svc" in explain.stdout

    certify = _run(["warden", "certify"], repo, env)
    assert certify.returncode == 0, certify.stdout + certify.stderr
    assert "certification: LEVEL" in certify.stdout
    assert "G-01" in certify.stdout

    validate = _run(["cage", "validate", str(cage_dir / "cage.toml")], repo, env)
    assert validate.returncode == 0, validate.stdout + validate.stderr
    assert validate.stdout.startswith("ok: sampleproj")

    enroll = _run(["cage", "enroll", str(cage_dir / "cage.toml")], repo, env)
    assert enroll.returncode == 0, enroll.stdout + enroll.stderr
    assert (cage_dir / "run.sh").is_file(), enroll.stdout
