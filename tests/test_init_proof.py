"""The CI proof that `warden init` works on a fresh repository, and what stands in
for a live run of the workflow init writes.

`scripts/init-proof.sh` runs the README's Try it commands, read from the README,
on fresh Python, Node, Go and Java repositories with whichever warden is on PATH; the
`init-proof` CI job puts the PR's wheel there, and `scripts/init-proof-isolation.sh`
first checks that warden is the uv tool's and that git cannot reach the platform.
`scripts/workflow-graph-check.py` follows the values the generated gate hands
between jobs. Each is run here as it runs in CI, and each check in them is shown
to fail on the defect it exists to catch.
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import toolchain
import yaml
from conftest import _AMBIENT_GIT_CONFIG, why_a_ci_job_might_not_run

from warden import __version__, enroll

ROOT = Path(__file__).resolve().parent.parent
PROOF = ROOT / "scripts" / "init-proof.sh"
TRY_IT = ROOT / "scripts" / "readme-try-it.sh"
ISOLATION = ROOT / "scripts" / "init-proof-isolation.sh"
GRAPH = ROOT / "scripts" / "workflow-graph-check.py"
VENV_BIN = Path(sys.executable).parent
INSTALL = f"uv tool install git+https://github.com/NightWatchEng/nightgate@v{__version__}"


def _try_it(readme: str) -> list[str]:
    section = readme.split("\n## Try it\n", 1)[1].split("\n## ", 1)[0]
    fence = section.split("```sh\n", 1)[1].split("```", 1)[0]
    return [line for line in fence.splitlines() if line.strip()]


def _proof(workdir: Path, *, path_first: Path = VENV_BIN, readme: Path | None = None):
    env = {**os.environ, **_AMBIENT_GIT_CONFIG,
           "PATH": f"{path_first}{os.pathsep}{VENV_BIN}{os.pathsep}{os.environ['PATH']}"}
    if readme is not None:
        env["INIT_PROOF_README"] = str(readme)
    return subprocess.run(["bash", str(PROOF), str(workdir)], capture_output=True,
                          text=True, env=env, timeout=600)


def _fake_warden(tmp_path: Path, body: str) -> Path:
    """A `warden` that runs BODY for the subcommand it intercepts and the real
    warden for everything else."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    fake = bindir / "warden"
    fake.write_text(f'#!/bin/sh\nREAL="{VENV_BIN / "warden"}"\n{body}\nexec "$REAL" "$@"\n')
    fake.chmod(0o755)
    return bindir


# ── the README's Try it ─────────────────────────────────────────────────────

def test_readme_try_it_is_install_init_certify_after_the_install_section():
    """The three commands follow `## Install`, whose tag check a reader runs
    when the first of them cannot find the release."""
    readme = (ROOT / "README.md").read_text()
    assert _try_it(readme) == [INSTALL, "warden init", "warden certify --level 3"]
    assert readme.index("\n## Install\n") < readme.index("\n## Try it\n"), (
        "Try it comes before the Install section that explains its first line")


# The stems of words that say who may read a repository, matched at a word's
# start so every inflection counts: privately, publicly, invitation,
# collaborators, accessible, permissions.
VISIBILITY = re.compile(r"\b(privat|public|invit|collaborat|visib|access|permission)",
                        re.IGNORECASE)


def _visibility_words(markdown: str) -> list[str]:
    """Every visibility word MARKDOWN says in its own voice. Left out are the
    `text` blocks, which quote another repository's CI output verbatim, and
    the demo's planted AWS access key id, which names a secret's shape. Its
    `sh` blocks are the README's own commands and are read."""
    own = re.sub(r"(?ms)^```text\n.*?^```[^\n]*$", "", markdown)
    own = re.sub(r"(?i)\bAWS access key id\b", "", own)
    return [m.group(0) for m in VISIBILITY.finditer(own)]


def test_readme_says_nothing_about_who_may_read_the_platform():
    """The README is written for a reader deciding whether to adopt; access to
    the platform repository is Installation.md's, which states it with the
    failures the client prints. A README sentence about it would be false the
    day the repository is public."""
    assert _visibility_words((ROOT / "README.md").read_text()) == []


# One sentence per alternative of VISIBILITY, each naming that stem and no
# other, so deleting any alternative turns its row red. The README's own
# earlier sentences are among them.
@pytest.mark.parametrize("sentence", [
    "The platform repository is kept privately.",
    "Once the repository is publicly readable, anyone can install it.",
    "Enrollment is by invitation.",
    "Ask to be added as a collaborator.",
    "Its visibility decides who can install it.",
    "You need read access to `NightWatchEng/nightgate` and git credentials.",
    "Ask NightWatchEng for permission first.",
])
def test_the_visibility_check_refuses_a_sentence_about_access(sentence):
    """The check above, shown biting on sentences a rewrite could add."""
    assert _visibility_words(f"# x\n\n{sentence}\n") != []


def test_the_visibility_check_reads_the_readmes_own_commands():
    text = "```sh\n# needs read access to NightWatchEng/nightgate\nwarden init\n```\n"
    assert _visibility_words(text) == ["access"]


def test_the_visibility_check_passes_a_quoted_secret_and_quoted_output():
    """`access keys` in the README's own voice is still caught; only the
    demo's `AWS access key id` and a quoted `text` block are left out."""
    text = ("It commits a string shaped like an AWS access key id.\n\n"
            "```text\nrepository is private\n```\n")
    assert _visibility_words(text) == []
    assert _visibility_words("You need access keys from us.\n") == ["access"]


def _try_it_block(text: str) -> str | None:
    """The block scripts/readme-try-it.sh prints, read the way its awk reads
    it: records end at `\\n` alone (a CRLF heading is `## Try it\\r` and
    names no section; a form feed stays inside its line), the first fence
    under the heading opens it, the next closes it, and every line between
    is printed with its newline. None without a closed fence."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    in_section = in_fence = False
    block: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            in_section = line == "## Try it"
        elif in_section and line.startswith("```"):
            if in_fence:
                return "".join(f"{x}\n" for x in block)
            in_fence = True
        elif in_section and in_fence:
            block.append(line)
    return None


def _try_it_script(readme: Path) -> subprocess.CompletedProcess:
    """Bytes, not text: a text-mode read would translate the newlines the
    parity below is about."""
    return subprocess.run(["bash", str(TRY_IT), str(readme)], capture_output=True,
                          timeout=60)


def test_readme_try_it_script_prints_the_block_byte_for_byte():
    """scripts/readme-try-it.sh (awk) and _try_it_block (Python) are two
    readings of one block; the proof runs the first, and neither may drift
    from the README."""
    readme = ROOT / "README.md"
    result = _try_it_script(readme)
    assert result.returncode == 0, result.stderr
    assert result.stdout == _try_it_block(readme.read_bytes().decode()).encode()
    assert result.stdout == f"{INSTALL}\nwarden init\nwarden certify --level 3\n".encode()


@pytest.mark.parametrize("text", [
    "# x\n\n## Try it\n\nprose\n\n```sh\na\n\nb\n\n```\n\n## Next\n",
    "## Try it\n```sh\na\n\n\n```\n",
    "## Try it\n```\n```\n## Next\n```sh\nnot this\n```\n",
    "## Other\n```sh\nnot this\n```\n## Try it\n```sh\n  indented \n```\n",
    "## Try it\n```sh\na\fb\n```",
    "## Try it\n```sh\na\r\n```\n",
])
def test_readme_try_it_script_matches_the_python_reading_on_odd_blocks(text, tmp_path):
    """Trailing blank lines, a form feed, a stray CR inside the fence and a
    file with no final newline all read the same both ways, byte for
    byte."""
    readme = tmp_path / "README.md"
    readme.write_bytes(text.encode())
    result = _try_it_script(readme)
    assert result.returncode == 0, result.stderr
    assert result.stdout == _try_it_block(text).encode()


@pytest.mark.parametrize("text", [
    "## Try it\n```sh\na\n## Next\n```\n", "## Try it\n```sh\na\n",
    "## Other\n```sh\na\n```\n", "",
    "## Try it\r\n```sh\r\na\r\n```\r\n",
])
def test_readme_try_it_script_exits_1_printing_nothing_without_a_closed_block(text, tmp_path):
    """A CRLF README is the sharp case: its heading line is `## Try it\\r`,
    which names no section to either reader."""
    readme = tmp_path / "README.md"
    readme.write_bytes(text.encode())
    result = _try_it_script(readme)
    assert result.returncode == 1 and result.stdout == b"", result
    assert b"no closed fenced block" in result.stderr
    assert _try_it_block(text) is None
    assert _try_it_script(tmp_path / "missing.md").returncode == 1


# ── scripts/init-proof.sh ───────────────────────────────────────────────────

def _langs() -> list[str]:
    """Every language `warden init` enrolls, read from enroll.LANGUAGES, so a
    language added there is one the proof and its CI job must cover."""
    return [lang.name for lang in enroll.LANGUAGES]


@pytest.fixture(scope="module")
def fresh(tmp_path_factory) -> tuple[Path, subprocess.CompletedProcess]:
    """A run of the proof on the real README, shared by the tests that read
    what it left in WORKDIR: once per process, so once per xdist worker that
    collects one of them under `-n auto`."""
    workdir = tmp_path_factory.mktemp("proof") / "fresh"
    return workdir, _proof(workdir)


def test_init_proof_takes_fresh_python_node_go_and_java_repos_to_level_3(fresh):
    workdir, result = fresh
    assert result.returncode == 0, result.stdout + result.stderr
    assert (workdir / "try-it.txt").read_bytes() == \
        _try_it_block((ROOT / "README.md").read_bytes().decode()).encode()
    for lang in _langs():
        assert f"init proof: {lang}: " in result.stdout, result.stdout
        assert "certification: LEVEL 3" in (workdir / f"{lang}-certify.txt").read_text()
        assert (workdir / lang / enroll.WORKFLOW_PATH).is_file()


def test_the_init_proofs_java_repo_runs_its_junit_test_through_the_scope_init_wrote(fresh):
    """The proof itself starts no JVM: init, certify and declare check read
    files. This runs the verify scope init wrote for the Java fixture, as the
    generated gate's verify job would, so the fixture is shown to compile and
    its one JUnit 5 test to run and pass, not only to be detected. The JDK
    and Maven come from the suite jobs' toolchain (#428)."""
    workdir, result = fresh
    assert result.returncode == 0, result.stdout + result.stderr
    repo = workdir / "java"
    toolchain.require("mvn", "java")
    env = {**os.environ, **_AMBIENT_GIT_CONFIG,
           "PATH": f"{VENV_BIN}{os.pathsep}{os.environ['PATH']}"}
    run = subprocess.run(["warden", "verify", "--scope", "java"], cwd=repo, env=env,
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stdout + run.stderr
    report = repo / "target" / "surefire-reports" / "TEST-demo.DemoTest.xml"
    suite = ET.parse(report).getroot()
    assert (suite.get("tests"), suite.get("failures"), suite.get("errors")) == ("1", "0", "0")
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, env=env,
                            capture_output=True, text=True, timeout=60)
    assert status.stdout == "", "the build left the enrolled tree dirty: " + status.stdout


@pytest.mark.parametrize("block, named", [
    ([INSTALL, "warden init"], "holds 2 command(s)"),
    ([INSTALL, "warden init", "warden verify", "warden certify --level 3"], "holds 4 command(s)"),
    ([INSTALL.replace(__version__, "0.0.1"), "warden init", "warden certify --level 3"],
     f"not the v{__version__} release"),
    ([INSTALL, "warden certify --level 3", "warden init"], "not warden init"),
    ([INSTALL, "warden init", "warden certify --level 3 || true"], "needs a shell"),
    ([INSTALL, "", "warden init", "warden certify --level 3"], "holds 4 command(s)"),
    ([INSTALL, "warden init", "warden certify --level 3", "", ""], "holds 5 command(s)"),
])
def test_init_proof_refuses_a_try_it_block_it_cannot_run_as_written(block, named, tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# x\n\n## Try it\n\n```sh\n" + "\n".join(block) + "\n```\n\n## Next\n")
    result = _proof(tmp_path / "fresh", readme=readme)
    assert result.returncode != 0 and named in result.stderr, result.stderr


def test_init_proof_fails_when_certify_does_not_reach_level_3(tmp_path):
    bindir = _fake_warden(tmp_path, 'if [ "$1" = certify ]; then '
                                    'echo "certification: LEVEL 2 (Declared)"; exit 1; fi')
    result = _proof(tmp_path / "fresh", path_first=bindir)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "init proof: python: `warden certify --level 3` exited non-zero" in result.stderr


def test_init_proof_fails_when_certify_exits_0_below_level_3(tmp_path):
    bindir = _fake_warden(tmp_path, 'if [ "$1" = certify ]; then '
                                    'echo "certification: LEVEL 2 (Declared)"; exit 0; fi')
    result = _proof(tmp_path / "fresh", path_first=bindir)
    assert result.returncode == 1
    assert "certify did not report LEVEL 3 or above" in result.stderr


def test_init_proof_fails_when_a_file_appears_that_init_did_not_report(tmp_path):
    bindir = _fake_warden(tmp_path, 'if [ "$1" = init ]; then "$REAL" "$@" || exit; '
                                    'echo planted > hand-written.txt; exit 0; fi')
    result = _proof(tmp_path / "fresh", path_first=bindir)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "not exactly the ones init reported writing" in result.stderr
    assert "hand-written.txt" in result.stderr


def test_init_proof_fails_when_declare_check_reports_drift(tmp_path):
    bindir = _fake_warden(tmp_path, 'if [ "$1" = declare ]; then '
                                    'echo "✗ D-03 DRIFT"; exit 0; fi')
    result = _proof(tmp_path / "fresh", path_first=bindir)
    assert result.returncode == 1
    assert "reported DRIFT on what init wrote" in result.stderr


def test_init_proof_does_not_run_without_a_warden(tmp_path):
    env = {**os.environ, "PATH": "/usr/bin:/bin"}
    result = subprocess.run(["bash", str(PROOF), str(tmp_path)], capture_output=True,
                            text=True, env=env, timeout=60)
    assert result.returncode == 2 and "DID NOT RUN" in result.stderr, result.stderr


# ── scripts/workflow-graph-check.py ─────────────────────────────────────────

def _generated() -> dict:
    return yaml.safe_load(enroll.render_workflow(tuple(enroll.LANGUAGES)))


def _graph(tmp_path: Path, doc: dict) -> subprocess.CompletedProcess:
    path = tmp_path / "warden.yml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return subprocess.run([sys.executable, str(GRAPH), str(path)], capture_output=True,
                          text=True, timeout=60)


def _step(doc: dict, job: str, *, name: str | None = None, uses: str | None = None,
          nth: int = 0) -> dict:
    found = [s for s in doc["jobs"][job]["steps"]
             if (name and s.get("name") == name) or (uses and s.get("uses", "").startswith(uses))]
    return found[nth]


def test_the_generated_gate_hands_every_output_and_artifact_to_a_job_that_needs_it(tmp_path):
    result = _graph(tmp_path, _generated())
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_generated_subdirectory_gate_passes_the_workflow_graph_check(tmp_path):
    doc = yaml.safe_load(enroll.render_workflow(tuple(enroll.LANGUAGES), "svc/api"))
    result = _graph(tmp_path, doc)
    assert result.returncode == 0, result.stdout + result.stderr


def _rename_wheel_upload(doc):
    _step(doc, "install", uses="actions/upload-artifact@")["with"]["name"] = "wheel"


def _rename_verify_upload(doc):
    _step(doc, "verify", uses="actions/upload-artifact@")["with"]["name"] = "results"


def _gate_forgets_install(doc):
    doc["jobs"]["gate"]["needs"] = ["verify"]


def _drop_sha_output(doc):
    del doc["jobs"]["install"]["outputs"]["sha256"]


def _rename_build_step(doc):
    _step(doc, "install", name="build warden at repo.yaml platform.pin")["id"] = "compile"


def _build_never_writes_sha(doc):
    step = _step(doc, "install", name="build warden at repo.yaml platform.pin")
    step["run"] = "\n".join(line for line in step["run"].splitlines() if "sha256=" not in line)


def _verify_needs_a_ghost(doc):
    doc["jobs"]["verify"]["needs"] = ["install", "setup"]


def _gate_does_not_need_verify(doc):
    doc["jobs"]["gate"]["needs"] = ["install"]


def _upload_reads_a_later_step(doc):
    steps = doc["jobs"]["install"]["steps"]
    upload = _step(doc, "install", uses="actions/upload-artifact@")
    steps.remove(upload)
    steps.insert(0, upload)


@pytest.mark.parametrize("mutate, named", [
    (_rename_wheel_upload, "verify: downloads artifact 'warden-wheel', which no job it needs uploads"),
    (_rename_verify_upload, "gate: downloads artifact 'warden-verify', which no job it needs uploads"),
    (_gate_forgets_install, "gate: reads needs.install without listing install in needs"),
    (_drop_sha_output, "reads needs.install.outputs.sha256, which install does not declare"),
    (_rename_build_step, "install: output wheel reads steps.build, and the job has no step with id build"),
    (_build_never_writes_sha, "step build never writes sha256= to $GITHUB_OUTPUT"),
    (_verify_needs_a_ghost, "verify: needs `setup`, which the workflow does not define"),
    (_upload_reads_a_later_step, "reads steps.build, and no earlier step has id build"),
    (_gate_does_not_need_verify, "gate: downloads artifact 'warden-verify', which no job it needs uploads"),
], ids=lambda v: getattr(v, "__name__", "")[1:] if callable(v) else "")
def test_the_graph_check_names_each_broken_handoff(mutate, named, tmp_path):
    doc = copy.deepcopy(_generated())
    mutate(doc)
    result = _graph(tmp_path, doc)
    assert result.returncode == 1, result.stdout + result.stderr
    assert named in result.stdout, result.stdout


def _job(*, needs=(), uploads=(), downloads=(), **extra) -> dict:
    steps = [{"uses": "actions/upload-artifact@v4", "with": {"name": n, "path": "x"}} for n in uploads]
    steps += [{"uses": "actions/download-artifact@v4", "with": {"name": n}} for n in downloads]
    steps += extra.pop("steps", [])
    return {"runs-on": "ubuntu-latest", "needs": list(needs), "steps": steps or [{"run": "true"}],
            **extra}


def _jobs(**jobs) -> dict:
    return {"on": "push", "jobs": jobs}


@pytest.mark.parametrize("doc", [
    _jobs(up=_job(uploads=["results"]), mid=_job(), gate=_job(needs=["mid"], downloads=["results"])),
    _jobs(up=_job(uploads=["results"]), base=_job(), mid=_job(needs=["base"]),
          gate=_job(needs=["mid"], downloads=["results"])),
], ids=["direct", "transitive"])
def test_the_graph_check_refuses_a_download_from_a_job_the_reader_does_not_need(doc, tmp_path):
    result = _graph(tmp_path, doc)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "gate: downloads artifact 'results', which no job it needs uploads" in result.stdout


def test_the_graph_check_accepts_a_download_from_a_job_needed_only_through_another(tmp_path):
    doc = _jobs(up=_job(uploads=["results"]), mid=_job(needs=["up"]),
                gate=_job(needs=["mid"], downloads=["results"]))
    result = _graph(tmp_path, doc)
    assert result.returncode == 0, result.stdout + result.stderr


def _install_with_wheel_output(run: str = 'echo "wheel=w.whl" >> "$GITHUB_OUTPUT"') -> dict:
    return _job(outputs={"wheel": "${{ steps.build.outputs.wheel }}"},
                steps=[{"id": "build", "run": run}])


@pytest.mark.parametrize("quote", ["'", '"'])
def test_the_graph_check_reads_the_index_form_of_needs_and_steps(quote, tmp_path):
    q = quote
    good = _jobs(install=_install_with_wheel_output(), gate=_job(
        needs=["install"], steps=[{"id": "s", "run": f"echo ${{{{ needs[{q}install{q}].outputs[{q}wheel{q}] }}}}"},
                                  {"run": f"echo ${{{{ steps[{q}s{q}].outputs.x }}}}"}]))
    good["jobs"]["gate"]["steps"][0]["run"] += '\necho "x=1" >> "$GITHUB_OUTPUT"'
    result = _graph(tmp_path, good)
    assert result.returncode == 0, result.stdout + result.stderr

    bad = _jobs(install=_install_with_wheel_output(), gate=_job(steps=[
        {"run": f"echo ${{{{ needs[{q}install{q}].outputs.wheel }}}}"}]),
        late=_job(needs=["install"], steps=[
            {"run": f"echo ${{{{ needs[{q}install{q}].outputs[{q}nope{q}] }}}} ${{{{ steps[{q}b{q}].outputs.k }}}}"}]))
    result = _graph(tmp_path, bad)
    assert result.returncode == 1, result.stdout + result.stderr
    for named in ("gate: reads needs.install without listing install in needs",
                  "late: reads needs.install.outputs.nope, which install does not declare",
                  "reads steps.b, and no earlier step has id b"):
        assert named in result.stdout, result.stdout


@pytest.mark.parametrize("quote", ["'", '"'])
def test_the_graph_check_reads_index_form_outputs_of_a_step_that_does_not_exist(quote, tmp_path):
    q = quote
    doc = _jobs(build=_job(steps=[{"run": f"echo ${{{{ steps[{q}z{q}][{q}outputs{q}][{q}k{q}] }}}}"}]))
    result = _graph(tmp_path, doc)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "build: step 'a step' reads steps.z, and no earlier step has id z" in result.stdout, \
        result.stdout


@pytest.mark.parametrize("quote", ["'", '"'])
def test_the_graph_check_reads_index_form_outputs_a_needed_job_does_not_declare(quote, tmp_path):
    q = quote
    doc = _jobs(**{"build-it": _install_with_wheel_output(), "late": _job(
        needs=["build-it"],
        steps=[{"run": f"echo ${{{{ needs[{q}build-it{q}][{q}outputs{q}][{q}nope{q}] }}}}"}])})
    result = _graph(tmp_path, doc)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "late: reads needs.build-it.outputs.nope, which build-it does not declare" \
        in result.stdout, result.stdout


def test_the_graph_check_does_not_count_a_comment_line_as_a_write(tmp_path):
    run = '# wheel= is written by the build tool, not here >> $GITHUB_OUTPUT\necho done'
    result = _graph(tmp_path, _jobs(install=_install_with_wheel_output(run)))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "step build never writes wheel= to $GITHUB_OUTPUT" in result.stdout, result.stdout


def test_the_graph_check_refuses_a_file_that_is_not_a_workflow(tmp_path):
    path = tmp_path / "x.yml"
    path.write_text("name: not a workflow\n")
    result = subprocess.run([sys.executable, str(GRAPH), str(path)], capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 2 and "nothing was checked" in result.stderr


# ── scripts/init-proof-isolation.sh ─────────────────────────────────────────

def _isolation(tmp_path: Path, *, tool_warden: bool = True, config: str = ""):
    """Run the isolation script with a scratch uv tool bin dir first on PATH and
    a scratch file as the global git config, holding CONFIG beforehand."""
    bindir = tmp_path / "toolbin"
    bindir.mkdir()
    if tool_warden:
        (bindir / "warden").write_text("#!/bin/sh\necho warden 0.0.0\n")
        (bindir / "warden").chmod(0o755)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(config)
    env = {**os.environ, **_AMBIENT_GIT_CONFIG, "UV_TOOL_BIN_DIR": str(bindir),
           "PATH": f"{bindir}{os.pathsep}{VENV_BIN}{os.pathsep}{os.environ['PATH']}"}
    global_layer = next(k for k in _AMBIENT_GIT_CONFIG if k.endswith("_GLOBAL"))
    env[global_layer] = str(gitconfig)
    result = subprocess.run(["bash", str(ISOLATION)], capture_output=True, text=True,
                            env=env, timeout=120)
    return result, gitconfig


# The spellings the isolation script checks: case, port, scp form with a
# leading slash, and the network transports git ships a URL scheme for:
# https, http, ftps, ftp, ssh (scp form, ssh:// and git+ssh://) and git://.
SPELLINGS = (
    "https://github.com/NightWatchEng/nightgate",
    "https://github.com/nightwatcheng/nightgate",
    "http://github.com/NightWatchEng/nightgate",
    "ftps://github.com/NightWatchEng/nightgate.git",
    "ftp://github.com/NightWatchEng/nightgate.git",
    "git@github.com:NightWatchEng/nightgate.git",
    "git@github.com:nightwatcheng/nightgate.git",
    "git@github.com:/NightWatchEng/nightgate.git",
    "ssh://git@github.com/NightWatchEng/nightgate.git",
    "ssh://git@github.com/nightwatcheng/nightgate.git",
    "ssh://git@github.com:22/NightWatchEng/nightgate.git",
    "git+ssh://git@github.com/NightWatchEng/nightgate.git",
    "git://github.com/NightWatchEng/nightgate.git",
)


def _ls_remote(url: str, gitconfig: Path) -> subprocess.CompletedProcess:
    global_layer = next(k for k in _AMBIENT_GIT_CONFIG if k.endswith("_GLOBAL"))
    env = {**os.environ, **_AMBIENT_GIT_CONFIG, global_layer: str(gitconfig),
           "GIT_TERMINAL_PROMPT": "0",
           "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=5"}
    return subprocess.run(["git", "ls-remote", "--tags", url], capture_output=True,
                          text=True, env=env, timeout=60)


def test_isolation_checks_exactly_the_listed_spellings(tmp_path):
    result, gitconfig = _isolation(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    refused = [line.removeprefix("refused: ") for line in result.stdout.splitlines()
               if line.startswith("refused: ")]
    assert refused == list(SPELLINGS), result.stdout
    assert "allow = never" in gitconfig.read_text()


@pytest.mark.parametrize("url", SPELLINGS, ids=SPELLINGS)
def test_isolation_leaves_no_route_through_this_spelling(url, tmp_path):
    """A route to a real repository is planted for URL first, the stand-in for
    an ssh key or credential that reaches the platform, and shown to work.
    After isolation git refuses the transport before connecting."""
    reachable = tmp_path / "platform"
    subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(reachable)], check=True,
                   capture_output=True, env={**os.environ, **_AMBIENT_GIT_CONFIG})
    planted = f'[url "{reachable}"]\n\tinsteadOf = {url}\n'
    control = tmp_path / "control-gitconfig"
    control.write_text(planted)
    assert _ls_remote(url, control).returncode == 0, "the control: the planted route reaches"

    result, gitconfig = _isolation(tmp_path, config=planted)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"refused: {url}\n" in result.stdout, result.stdout
    after = _ls_remote(url, gitconfig)
    assert after.returncode != 0, after
    assert "not allowed" in after.stderr, after.stderr


def test_isolation_fails_when_warden_is_not_the_uv_tools(tmp_path):
    result, _ = _isolation(tmp_path, tool_warden=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "not the uv tool's" in result.stderr, result.stderr


def test_isolation_fails_when_a_spelling_still_reaches_a_repository(tmp_path):
    reachable = tmp_path / "platform"
    subprocess.run(["git", "init", "-q", "-b", "main", str(reachable)], check=True, capture_output=True)
    config = (f'[protocol "file"]\n\tallow = always\n'
              f'[url "{reachable}"]\n\tinsteadOf = ssh://git@github.com/NightWatchEng/nightgate.git\n')
    result, _ = _isolation(tmp_path, config=config)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "git reached the platform through ssh://git@github.com/NightWatchEng/nightgate.git" \
        in result.stderr, result.stderr


def test_isolation_fails_when_a_spelling_fails_without_gits_transport_refusal(tmp_path):
    config = (f'[protocol "file"]\n\tallow = always\n'
              f'[url "{tmp_path / "absent"}"]\n\tinsteadOf = git@github.com:NightWatchEng/nightgate.git\n')
    result, _ = _isolation(tmp_path, config=config)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "git failed on git@github.com:NightWatchEng/nightgate.git without refusing its " \
        "transport" in result.stderr, result.stderr


# ── the CI job ──────────────────────────────────────────────────────────────

def test_ci_runs_the_init_proof_from_the_built_wheel_with_no_secret():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = ci["jobs"]["init-proof"]
    # One admissible condition, declared once in conftest: a title-only edit
    # over a sha this workflow has already passed. Everything else — including
    # the same clause without its `always()` — is "the init proof might not
    # run on a trigger it must", which is what this line has always said.
    why = why_a_ci_job_might_not_run(job)
    assert why is None, f"the init proof must run on every trigger, fork PRs included: {why}"
    assert "secrets." not in yaml.safe_dump(job)
    runs = [s["run"] for s in job["steps"] if "run" in s]
    joined = "\n".join(runs)
    order = [next(i for i, r in enumerate(runs) if needle in r) for needle in (
        "uv build --wheel", "uv tool install", "bash scripts/init-proof-isolation.sh",
        "bash scripts/readme-try-it.sh", "bash scripts/init-proof.sh",
        "sha256sum --check --strict", "scripts/workflow-graph-check.py")]
    assert order == sorted(order), runs
    assert "uv tool dir --bin >> \"$GITHUB_PATH\"" in joined
    assert "command -v shellcheck" in joined
    for lang in _langs():
        for step in ("actionlint", "workflow-graph-check"):
            text = next(r for r in runs if step in r and "fresh/" in r)
            assert f"fresh/{lang}/{enroll.WORKFLOW_PATH}" in text, (step, lang)
    for neutralizer in ("|| true", "|| :", "set +e", "|| exit 0", "continue-on-error"):
        assert neutralizer not in yaml.safe_dump(job), neutralizer
