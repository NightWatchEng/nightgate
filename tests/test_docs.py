"""Wiki + README integrity: links resolve, diagrams parse-ready.

The wiki lives in-repo (docs/wiki/) so it lands through the gate; these
checks keep it from rotting: every page listed on Home exists, every
relative link resolves, and mermaid fences are balanced.
"""

import ast
import inspect
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import _AMBIENT_GIT_CONFIG, tracked
from private_evidence import (EXPORT, needs_corpus, needs_design_records,
                              publish_excludes)

ROOT = Path(__file__).parent.parent
WIKI = ROOT / "docs" / "wiki"


def test_relative_links_resolve():
    link_re = re.compile(r"\]\((?!http|#)([^)]+?)(?:#[^)]*)?\)")
    excluded = []
    for md in [ROOT / "README.md", *WIKI.glob("*.md")]:
        for target in link_re.findall(md.read_text()):
            if target.startswith("../../wiki"):
                continue  # README's GitHub-wiki-tab style link
            resolved = (md.parent / target).resolve()
            if not resolved.exists() and EXPORT and publish_excludes(
                    rel := resolved.relative_to(ROOT).as_posix()):
                excluded.append(f"{md.name} -> {rel}")
                continue
            assert resolved.exists(), f"{md.name}: dead link -> {target}"
    if excluded:  # every other link resolved; these name what the export left out
        pytest.skip("public export: these links point at records publish.yaml "
                    "leaves out: " + ", ".join(excluded))


def _fence_check(text: str, name: str) -> None:
    """The cheap string-level fence check, a function of its own so the test
    below can exercise the REAL check rather than a retyped copy of it."""
    opens = text.count("```mermaid")
    closes = text.count("```") - opens
    assert closes >= opens, f"{name}: unbalanced mermaid fence"
    for block in re.findall(r"```mermaid\n(.*?)```", text, re.S):
        first = block.strip().splitlines()[0]
        assert first.startswith(("flowchart", "sequenceDiagram", "graph")), \
            f"{name}: mermaid block with unknown type: {first!r}"


def test_mermaid_fences_balanced_and_nonempty():
    for md in [ROOT / "README.md", *WIKI.glob("*.md")]:
        _fence_check(md.read_text(), md.name)


def test_fence_check_accepts_diagrams_the_real_parser_rejects():
    """The cheap fence check accepts diagrams the real Mermaid parser rejects.

    Both snippets below pass the string check and fail
    @mermaid-js/mermaid-cli:

      * `graph` is a reserved Mermaid keyword, so using it as a node id is a
        parse error — but the fence check only inspects the FIRST line.
      * a `;` inside a sequence message terminates the statement, leaving the
        rest of the line dangling.

    The assertion is deliberately that the cheap check PASSES them. If a
    future edit makes the string check strong enough to reject these, this
    test goes red and should be deleted along with its premise — but until
    then, `scripts/validate-diagrams.sh` is the only thing standing between a
    broken diagram and the wiki, and it must stay wired into CI (asserted by
    test_ci_runs_the_diagram_parser_enforced).
    """
    reserved_keyword = "```mermaid\nflowchart TB\n    graph[\"a node named graph\"]\n```\n"
    stray_semicolon = ("```mermaid\nsequenceDiagram\n    participant A\n"
                       "    participant B\n    A-->>B: verdicts; the arbiter\n```\n")
    for name, snippet in (("reserved-keyword", reserved_keyword),
                          ("stray-semicolon", stray_semicolon)):
        _fence_check(snippet, name)   # passes — that is the whole point


def test_ci_runs_the_diagram_parser_enforced():
    """A validator wired to nothing that can reject is a guardrail in name
    only: the script is a fence only while CI runs it in a step whose failure
    actually fails the job.

    A substring grep of ci.yml for the script path cannot tell that from a
    commented-out run line, a run line replaced by `run: 'true'` (the
    self-test step still names the path), the job deleted but for a comment
    naming the path, or `continue-on-error: true`. So this reuses
    the repo's own machinery instead of a second hand-rolled parser:
    `certify._run_check`'s `ci_step_enforced` parses the workflow YAML and
    rejects a shell-swallowed exit (`|| true`), step-level and job-level
    `continue-on-error`, and a workflow not triggered on the required event.
    One implementation of "is this step actually enforced", not two to drift
    apart.

    Limit, inherited from that checker and not claimed here: `if:`
    expressions are not evaluated, so `if: false` is NOT caught by this test.
    """
    from warden import certify as certify_mod

    assert (ROOT / "scripts" / "validate-diagrams.sh").is_file(), \
        "scripts/validate-diagrams.sh is gone"

    # Each step gets its OWN pattern. The self-test step also contains the
    # bare script path, so one pattern would match both steps and find the
    # self-test enforced while the wiki validation was neutered.
    for what, pattern in (
        ("validate the wiki's diagrams",
         "scripts/validate-diagrams.sh docs/wiki"),
        ("prove the gate rejects a known-bad diagram",
         "bash scripts/validate-diagrams.sh tests/fixtures/broken-diagram"),
    ):
        ok, detail = certify_mod._run_check(
            {"type": "ci_step_enforced", "pattern": pattern,
             "event": "pull_request"}, ROOT)
        assert ok, (
            f"CI does not ENFORCE the step that should {what} — the cheap "
            "fence check is then the only guard, and it demonstrably passes "
            f"diagrams that do not parse. certify says: {detail}")


def test_ci_proves_the_gate_bites_on_a_known_bad_diagram():
    """A gate nobody has watched reject anything is a gate on trust.

    CI runs the validator twice: once over docs/wiki (must pass) and once
    over tests/fixtures/broken-diagram (must exit 1, the rejection). That
    second run is the gate's own mutation proof, executed on every PR rather
    than once by hand at authoring time.

    The fixture is deliberately an INDENTED fence: GitHub renders indented
    fences, so an extractor anchored at column 0 would report green on a
    diagram that does not render. mermaid-cli reads the markdown whole and
    finds the fences itself.
    """
    fixture_dir = ROOT / "tests" / "fixtures" / "broken-diagram"
    fixtures = list(fixture_dir.glob("*.md"))
    assert fixtures, "the known-bad diagram fixture is gone"
    for md in fixtures:
        assert "```mermaid" in md.read_text(), f"{md.name}: no diagram to reject"

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert ("bash scripts/validate-diagrams.sh tests/fixtures/broken-diagram "
            '>"$log" 2>&1 || status=$?') in ci, (
        "CI no longer asserts the gate REJECTS the known-bad fixture — the "
        "validator could be silently blinded (an extractor regex, a changed "
        "mermaid invocation) and every run would still report green")
    # Negating the exit passes on ANY failure, a browser that never launched
    # included; tests/test_build_wiki.py drives the step's exit handling.
    assert "! bash scripts/validate-diagrams.sh" not in ci, (
        "CI negates the validator's exit, so a launch failure reads as a "
        "rejection of the known-bad diagram")


def test_the_diagram_toolchain_is_pinned_by_lockfile():
    """The mermaid toolchain is pinned by a committed integrity-hashed lockfile
    AND installed with lifecycle scripts disabled. Either half reverting
    reopens the gap: `npx -y pkg@ver` pins the top-level version while the
    transitive puppeteer peer floats and every resolved package's postinstall
    runs under the job token that writes attestations.

    The `pinned-run-invocations` rule cannot see this: it reads YAML under
    .github/, and the invocation lives in a shell script the workflow calls.
    This test is the named guard the rule body points at for that blind spot.
    """
    import json

    mermaid_dir = ROOT / "scripts" / "mermaid"
    manifest = json.loads((mermaid_dir / "package.json").read_text())
    for pkg, ver in manifest["dependencies"].items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", ver), (
            f"scripts/mermaid/package.json pins {pkg} as {ver!r} — a range "
            "(^ ~ ||) floats the resolution the lockfile is meant to freeze")
    lock = mermaid_dir / "package-lock.json"
    assert lock.is_file(), (
        "scripts/mermaid/package-lock.json is gone — without it `npm ci` "
        "cannot run and nothing pins the transitive tree")
    assert '"integrity"' in lock.read_text(), (
        "package-lock.json carries no integrity hashes — it pins names, "
        "not content")

    script = (ROOT / "scripts" / "validate-diagrams.sh").read_text()
    assert "npm ci" in script and "--ignore-scripts" in script, (
        "validate-diagrams.sh no longer installs with `npm ci "
        "--ignore-scripts` — the pinned-run-invocations decision requires "
        "lockfile-exact resolution AND no lifecycle-script execution")
    assert "node_modules/.bin/mmdc" in script, (
        "validate-diagrams.sh does not run the locally installed mmdc — "
        "whatever replaced it resolves outside the committed lockfile")
    # No line may INVOKE npx. Comments and echo'd guidance legitimately
    # mention it (the header quotes the removed shape; the browser error
    # names `npx puppeteer browsers install` as the local fix), yet a line
    # containing 'echo' can still invoke it (`if ! npx ...; then echo FAIL;
    # fi`). So anchor on COMMAND POSITION: npx counts as invoked when it
    # follows line start, a shell operator, command substitution, or a
    # then/do/else keyword. Residual: a line regex cannot fully parse shell,
    # so an anchor spelling outside this set could hide an invocation; the
    # mmdc assertion above independently pins what actually renders.
    npx_invocations = [
        ln for ln in script.splitlines()
        if not ln.lstrip().startswith("#")
        and re.search(r"(?:^|[|&;(!`]|\$\(|\bthen\b|\bdo\b|\belse\b)\s*npx\s", ln)]
    assert not npx_invocations, (
        "validate-diagrams.sh reverted to run-time resolution via npx — the "
        f"shape the pinned toolchain removed: {npx_invocations}")


def _fenced_blocks(text: str, lang: str) -> list[str]:
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        if lines[i].strip() == f"```{lang}":
            j = i + 1
            body = []
            while j < len(lines) and lines[j].strip() != "```":
                body.append(lines[j]); j += 1
            out.append("\n".join(body)); i = j
        i += 1
    return out


def test_adopting_repo_yaml_snippet_is_the_real_file():
    """Adopting.md is copy-pasteable by design, so a snippet that has drifted
    from the file CI enrolls hands the reader a config that fails the gate:
    one missing `platform:`, which baseline check G-03 requires, fails Level
    1, the first rung.
    """
    doc = (WIKI / "Adopting.md").read_text()
    real = (ROOT / "examples" / "hello-svc" / "repo.yaml").read_text().strip()
    blocks = [b.strip() for b in _fenced_blocks(doc, "yaml")]
    assert real in blocks, (
        "the repo.yaml snippet in Adopting.md is not examples/hello-svc/"
        "repo.yaml verbatim — one of them changed without the other")


def test_adopting_rule_snippet_is_the_real_rule():
    """The rule a reader copies must be the rule CI enrolls, whole.

    The whole file is pinned, not just its `excludes:` line, so no part of the
    frontmatter or body can drift independently.
    """
    doc = (WIKI / "Writing-Rules.md").read_text()
    real = (ROOT / "examples" / "hello-svc" / ".warden" / "rules"
            / "secrets-in-diff.md").read_text().strip()
    assert real in doc, (
        "the secrets-in-diff snippet in Writing-Rules.md is not the real rule file "
        "verbatim — one of them changed without the other")
    # the line whose absence self-blocks a reader's first rules-dir PR
    assert "excludes:" in real


def test_graph_layer_sample_is_real_generated_output():
    """The page teaching `graph render` shows real `graph render` output, not
    a hand-edited sample no graph could produce (an edge to an undeclared
    node, a missing `memory` pseudo-node, payloads matching no yaml).

    Pinning it to real output means the page cannot claim "generated, not
    drawn" while being drawn.
    """
    from warden import graph as graph_mod
    doc = (ROOT / "docs" / "wiki" / "Graph-Layer.md").read_text()
    rendered = graph_mod.render(graph_mod.load(ROOT)).strip()
    blocks = [b.strip() for b in _fenced_blocks(doc, "mermaid")]
    assert rendered in blocks, (
        "Graph-Layer.md's render sample is not `warden graph render` output — "
        "regenerate it")


def test_graph_layer_yaml_sample_is_the_real_graph():
    """The page's yaml block is the real graph.yaml, verbatim: a hand-kept copy
    can drift (a node name, the builder skill, a gate payload) while the
    render beside it stays correct.
    """
    doc = (ROOT / "docs" / "wiki" / "Graph-Layer.md").read_text()
    real = (ROOT / "graph.yaml").read_text().strip()
    blocks = [b.strip() for b in _fenced_blocks(doc, "yaml")]
    assert real in blocks, (
        "Graph-Layer.md's graph.yaml sample is not the real graph.yaml")


def test_adopting_workflow_snippet_carries_no_mutable_action_ref():
    """The workflow a reader copies must not be the shape our own gate rejects.

    The page sits outside `**/.github/**`, so the pinned-actions rule cannot
    see it, and the acceptance assertion walks workflow files only. Without
    this, the snippet's refs could drift back to `@v4` with the whole suite
    green.

    Enforced with the rule's own patterns, not a re-typed regex, for the same
    reason the acceptance assertion derives its own: a second copy of a
    pattern is a second thing to drift.
    """
    import re
    from warden import rules as rules_mod

    rule = next(r for r in rules_mod.load_rules(ROOT / ".warden" / "rules")
                if r.id == "pinned-actions")
    patterns = [re.compile(c["pattern"]) for c in rule.checks]

    doc = (WIKI / "Adopting.md").read_text()
    offenders = [f"{n}: {line.strip()}"
                 for n, line in enumerate(doc.splitlines(), 1)
                 if any(p.search(line) for p in patterns)]
    assert not offenders, (
        "Adopting.md hands the reader an unpinned action ref — the exact "
        "shape pinned-actions blocks, in the page wiki/Adopting.md calls "
        "'the authoritative, tested path':\n  " + "\n  ".join(offenders))


def test_adopting_workflow_snippet_shas_match_the_real_workflow():
    """And the pinned SHAs must be the ones this repo actually runs.

    A snippet pinned to a sha we do not use is still copy-pasteable and still
    wrong; drift here is invisible precisely because it looks correct.
    """
    import re
    ref = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)@([0-9a-f]{40})")
    def refs(text):
        return {m.group(1): m.group(2) for m in ref.finditer(text)}

    live = refs((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    doc = refs((WIKI / "Adopting.md").read_text())
    assert doc, "no pinned refs found in Adopting.md — the guard would be vacuous"

    drift = {a: (sha, live[a]) for a, sha in doc.items()
             if a in live and sha != live[a]}
    assert not drift, (
        "Adopting.md pins a different sha than .github/workflows/ci.yml "
        f"runs: {drift}")


# ── Adopting.md step 4: the hand-written gate a consumer copies ──────────────
#
# agentops-hy6o.51: the snippet called `.warden/bin/warden`, a shim nothing
# on the page installs, so a verbatim copy failed at its first warden step;
# and it ran verify and review in one job, the shape the hy6o.9 ruling refuses
# because a verify command a pull request controls could switch the gate off.

_PLATFORM_GIT = "git+https://github.com/NightWatchEng/nightgate@"
_WARDEN_LINE = re.compile(r"^\s*warden \w", re.M)


def _adopting_jobs() -> dict:
    from test_init import _adopting_snippets
    return _adopting_snippets()[1]["jobs"]


def _installs_warden(step: dict) -> bool:
    return f'"{_PLATFORM_GIT}' in step.get("run", "") and (
        "uv tool install --no-config --no-cache" in step["run"])


def test_the_adopting_gate_installs_warden_before_any_warden_step():
    """Every job installs warden from the public platform repository before
    its first step that runs warden, with no secret but the job token; a job
    has its own runner, so an install in another job puts nothing on PATH."""
    jobs = _adopting_jobs()
    assert set(jobs) == {"verify", "gate"}, sorted(jobs)
    installs = []
    for name, job in jobs.items():
        steps = job["steps"]
        uses_warden = [i for i, s in enumerate(steps)
                       if _WARDEN_LINE.search(s.get("run", ""))]
        install = [i for i, s in enumerate(steps) if _installs_warden(s)]
        assert uses_warden and len(install) == 1, (name, uses_warden, install)
        assert install[0] < uses_warden[0], (
            f"the {name} job runs warden before the step that installs it")
        installs.append(steps[install[0]])
    assert installs[0] == installs[1], "the two install steps have drifted apart"
    text = _adopting_snippets_text()
    assert ".warden/bin/warden" not in text, (
        "the snippet calls the shim, which nothing on the page installs")
    assert re.findall(r"secrets\.(\w+)", text) == ["GITHUB_TOKEN"], (
        "the snippet reads a secret: the public platform installs without one")


def _adopting_snippets_text() -> str:
    from test_init import _adopting_snippets
    return _adopting_snippets()[2]


def _run_install_step(tmp_path: Path, repo_yaml: str | None
                      ) -> tuple[subprocess.CompletedProcess, list[str], dict]:
    """The install step's bash, run with a `uv` that records its argv."""
    step = next(s for s in _adopting_jobs()["gate"]["steps"]
                if _installs_warden(s))
    work, bin_dir = tmp_path / "work", tmp_path / "bin"
    work.mkdir()
    bin_dir.mkdir()
    if repo_yaml is not None:
        (work / "repo.yaml").write_text(repo_yaml)
    log = tmp_path / "uv.log"
    uv = bin_dir / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s|%s\\n" "$GIT_TERMINAL_PROMPT" "$*" '
                  f'>> "{log}"\n'
                  '[ "$1 $2" = "tool dir" ] && echo /tools/bin\nexit 0\n')
    uv.chmod(0o755)
    env = {**_AMBIENT_GIT_CONFIG, "PATH": f"{bin_dir}:/usr/bin:/bin",
           "GITHUB_PATH": str(tmp_path / "github_path")}
    bash = ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]]
    result = subprocess.run(bash, cwd=work, env=env, capture_output=True,
                            text=True, timeout=30)
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls, env


def test_the_adopting_install_step_installs_the_tag_platform_pin_names(tmp_path):
    """Run as written against the page's own repo.yaml: one `uv tool install`
    of the public repository at `platform.pin`, with no prompt for a
    credential, and uv's tool bin directory put on PATH for later steps."""
    from test_init import _adopting_snippets
    repo_yaml = next(b for k, b in re.findall(
        r"```(yaml)\n(.*?)```", (WIKI / "Adopting.md").read_text(), re.S)
        if "verify:" in b)
    pin = _adopting_snippets()[0]["platform"]["pin"]
    result, calls, env = _run_install_step(tmp_path, repo_yaml)
    assert result.returncode == 0, result.stderr
    assert calls == [
        f"0|tool install --no-config --no-cache {_PLATFORM_GIT}v{pin.removeprefix('v')}",
        "|tool dir --bin"], calls
    assert Path(env["GITHUB_PATH"]).read_text() == "/tools/bin\n"


@pytest.mark.parametrize("repo_yaml", [
    None,
    "platform:\n  pin: main\n",
    "platform: {pin: v3.0.3}\n",
    "version: 1\n",
])
def test_the_adopting_install_step_refuses_a_pin_that_is_not_a_tag(tmp_path, repo_yaml):
    """No repo.yaml, a branch name, the flow form the awk does not read, or no
    pin at all: exit 2, DID NOT RUN, and nothing installed."""
    result, calls, _ = _run_install_step(tmp_path, repo_yaml)
    assert result.returncode == 2, result
    assert "DID NOT RUN" in result.stderr and calls == [], (result.stderr, calls)


def test_the_adopting_gate_keeps_review_out_of_the_job_that_runs_verify():
    """The hy6o.9 ruling, held on the hand-written gate: the job that runs the
    repository's verify commands runs no review and no attestation check, and
    the job that does runs no repository code — no verify, no repo-local
    checkers — takes the verify results rather than re-running them, and
    still fails on a verify that did not pass.

    Round 1 found the first version reading only lines that START with
    `warden`: `make lint` or a local action in the gate job, or `warden
    verify ... && warden review ...` in the verify job, passed it. So every
    warden subcommand each job runs is read wherever it sits on a line, the
    gate job's actions are a whitelist, and the programs its steps run are an
    exact set, so a new one reddens here.
    """
    from test_init import _invoked_binaries
    jobs = _adopting_jobs()

    def warden_lines(job: dict) -> list[str]:
        return [line.strip() for s in job["steps"]
                for line in s.get("run", "").splitlines()
                if _WARDEN_LINE.match(line)]

    def subcommands(job: dict) -> set[str]:
        return {m for s in job["steps"] for m in re.findall(
            r"\bwarden (?!gate:)([a-z-]+)", s.get("run", ""))}

    assert subcommands(jobs["verify"]) == {"verify"}, subcommands(jobs["verify"])
    assert subcommands(jobs["gate"]) == {"take", "review", "attest"}, (
        subcommands(jobs["gate"]))
    allowed = ("actions/checkout@", "astral-sh/setup-uv@",
               "actions/download-artifact@", "actions/upload-artifact@")
    for step in jobs["gate"]["steps"]:
        assert step.get("uses", allowed[0]).startswith(allowed), step
    programs = {b for s in jobs["gate"]["steps"]
                for b in _invoked_binaries(s.get("run", ""))}
    assert programs == {"echo", "exit", "fi", "grep", "if", "then", "uv",
                        "warden"}, sorted(programs)
    verify, gate = warden_lines(jobs["verify"]), warden_lines(jobs["gate"])
    assert verify and all(line.startswith("warden verify ") for line in verify), verify
    assert not any(line.startswith("warden verify") for line in gate), gate
    review = [line for line in gate if line.startswith("warden review")]
    assert review == [
        'warden review --no-project-checkers --event "$GITHUB_EVENT_PATH"'], gate
    assert any(line.startswith("warden attest check") for line in gate), gate
    assert gate[0].startswith("warden take --from"), gate
    assert jobs["gate"]["needs"] == "verify", jobs["gate"]
    checkout = jobs["gate"]["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["persist-credentials"] is False, checkout
    assert any("needs.verify.result" in str(s.get("env", {}))
               for s in jobs["gate"]["steps"]), (
        "the gate job never reads whether verify passed")


def test_the_adopting_step_says_it_is_the_hand_written_emitted_gate():
    """The page says what the snippet is, why it is two jobs, and what the
    gate job will not run: the fixture run that proved the snippet found
    review exiting 2 on hello-svc's `engine: python` rule, whose checker is
    repository code the gate job refuses to import."""
    page = (WIKI / "Adopting.md").read_text()
    section = page[page.index("## 4 · Add the CI job"):]
    section = " ".join(section[:section.index("\n## 5 ")].split())
    for needle in ("hand-written form of the one `warden init` emits",
                   "a language `init` does not detect",
                   "One job that runs both is a gate the pull request can switch off",
                   "with no secret",
                   "The gate runs no project checker."):
        assert needle in section, needle
    # The rules step 2 copies that the gate job cannot run, derived: every
    # `engine: python` rule of hello-svc's that no core checker serves.
    from warden import plugins
    from warden import rules as rules_mod
    unserved = sorted(
        r.id for r in rules_mod.load_rules(
            ROOT / "examples" / "hello-svc" / ".warden" / "rules")
        if r.engine == "python" and r.id not in plugins.CORE_CHECKERS)
    assert unserved == ["handler-response-contract"], unserved
    assert all(f"leave `{rule_id}` out" in section for rule_id in unserved), unserved
    step2 = page[page.index("## 2 · Add the rules"):page.index("## 3 · ")]
    step2 = " ".join(step2.split())
    assert all(f"except `{rule_id}`" in step2 for rule_id in unserved), (
        "step 2 still copies a rule step 4's gate cannot run")


def test_the_example_invokes_no_local_file_it_does_not_ship():
    """Every local file the example workflow's `run:` steps execute is a file
    the example ships.

    GitHub executes workflows only from the ROOT `.github/`, and
    scripts/portability-sim.sh invokes the platform directly, so nothing else
    exercises the shipped template that claims to be the copy a consumer
    takes.

    A `run:` command containing a slash is a path — no shell PATH-resolves
    one — so every such command must be a file the example SHIPS, read from
    the git index rather than the filesystem, and must resolve INSIDE the
    example. Three constraints on how:

      * only the first word of each line of a `run:` script is read. A key
        such as `path: .warden/out/` in an upload-artifact step is not a
        command, and that directory is gitignored working state, so reading
        it would pass locally and fail on a fresh clone.
      * any token with a slash counts, not only `./` and `.name/` prefixes,
        so a bare `scripts/foo.sh` is checked too.
      * a token is resolved and must stay inside the example, so a `../` path
        to a platform file cannot pass as something the example ships.

    Today no `run:` command in the example is a path — every step invokes `uv`.
    That is the correct state, not a hole: this guard exists to catch the
    regression.
    """
    example_root = ROOT / "examples" / "hello-svc"
    wf = example_root / ".github" / "workflows" / "ci.yml"
    raw = wf.read_text()
    doc = yaml.safe_load(raw)

    root = example_root.resolve()
    shipped = {p.resolve().relative_to(root).as_posix()
               for p in tracked("examples/hello-svc/*")}
    assert shipped, "the example ships no tracked files — derivation broke"

    run_steps, missing = 0, []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            script = step.get("run")
            if not script:
                continue
            run_steps += 1
            for line in script.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                # Collapse GitHub expressions FIRST: `${{ github.workspace
                # }}/x.sh` otherwise splits to the bare `${{`, which carries no
                # slash and would skip the whole check.
                words = re.sub(r"\$\{\{[^}]*\}\}", "${{EXPR}}",
                               stripped).split()
                # `out="$RUNNER_TEMP/x"` assigns and executes nothing; the
                # command, if any, is the first word after the assignments.
                while words and re.match(r"[A-Za-z_]\w*=", words[0]):
                    words.pop(0)
                if not words:
                    continue
                token = words[0]
                if "/" not in token:
                    continue          # uv, echo, if, ... — resolved on PATH
                if "${{" in token:
                    missing.append(
                        f"{wf.name}: `run:` executes {token}, an expression "
                        "this guard cannot resolve to a shipped file — spell "
                        "the path literally in the example")
                    continue
                resolved = (example_root / token).resolve()
                if not resolved.is_relative_to(root):
                    missing.append(
                        f"{wf.name}: `run:` executes {token}, which resolves "
                        "OUTSIDE the example — a consumer copying the "
                        "directory out has no such path")
                    continue
                if resolved.relative_to(root).as_posix() not in shipped:
                    missing.append(
                        f"{wf.name}: `run:` executes {token}, which the "
                        "example does not ship")

    # Vacuity floor, DERIVED rather than a hardcoded count of today's file: a
    # parse that silently reads no steps would pass whatever the workflow does.
    declared = len(re.findall(r"(?m)^\s*-?\s*run:", raw))
    assert run_steps == declared and run_steps > 0, (
        f"parsed {run_steps} `run:` step(s) but the file declares {declared} "
        "— the guard is not reading what it claims to read")

    assert not missing, (
        "the example's workflow executes a local file it does not ship — a "
        "consumer copying it out gets a workflow that cannot run:\n  "
        + "\n  ".join(missing))


def test_example_workflow_pins_match_the_real_workflow():
    """The shipped example is 'the copy consumers take' (pinned-actions'
    own frontmatter), and nothing else binds its pins to the root workflow,
    so a Dependabot bump to the root alone would leave it on a SHA nobody
    updates. This pins the parity for actions the two workflows share."""
    import re
    ref = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)@([0-9a-f]{40})")

    def refs(text):
        return {m.group(1): m.group(2) for m in ref.finditer(text)}

    live = refs((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    example = refs((ROOT / "examples" / "hello-svc" / ".github" /
                    "workflows" / "ci.yml").read_text())
    assert example, "no pinned refs in the example workflow — vacuous guard"
    drift = {a: (sha, live[a]) for a, sha in example.items()
             if a in live and sha != live[a]}
    assert not drift, (
        "the example workflow pins a different sha than the root workflow "
        f"for a shared action: {drift}")


def _home_table_region(home: str) -> str:
    """Just the page table, not the whole page.

    The "Beyond the wiki" prose also links `[Adopting](Adopting.md)`, so a
    search over all of Home.md would stay green with a table row deleted.
    """
    head = "| Page | System |"
    assert head in home, "Home.md's page table header moved -- this guard is blind"
    region = home[home.index(head):]
    end = region.find("\n## ")
    return region if end == -1 else region[:end]


def test_home_table_lists_every_wiki_source_page():
    """Derivation guard: the Home table is the wiki's only index, so a page
    that lands without a row is a page nobody finds.

    Derived from the directory, so a new page needs a row the moment it
    lands and the guard grows with the wiki. `scripts/build-wiki.sh` builds
    the published sidebar off this table, so a missing row is a page the
    published wiki cannot reach.

    Its limit: a derived check cannot see a page being deleted, because the
    glob shrinks with it. Nothing here pins a fixed page list.
    """
    home = (WIKI / "Home.md").read_text()
    sources = sorted(p.stem for p in WIKI.glob("*.md")
                     if p.stem != "Home" and not p.name.startswith("_"))
    assert sources, "no wiki source pages found — the guard would be vacuous"
    linked = set(re.findall(r"\]\((?!http)([^)/#]+?)\.md(?:#[^)]*)?\)",
                            _home_table_region(home)))
    missing = [s for s in sources if s not in linked]
    assert not missing, (
        "docs/wiki/ pages that Home.md's table does not link: "
        f"{missing} — add a row, or the page is unreachable from the index "
        "and from the generated wiki sidebar built off it")


# --- What the tree shows a stranger --------------------------------------------
#
# The repo is read by people who did not write it, so the tree must not ship a
# maintainer's home directory — a check rather than an intention.

HOME_RE = re.compile(r"/(?:Users|home)/([A-Za-z0-9_.@-]+)")

# Placeholders, not people. `me` is the wiki's own convention
# (docs/wiki/Configuration.md), `agent` names the caged session's home in the
# cage render tests, `runner` is the GitHub Actions runner's home (a machine
# account), and `...` is an ellipsis inside prose. `/home/<name>` is matched as
# well as `/Users/<name>` on purpose — a Linux contributor's home is
# `/home/<name>`, and a guard that skipped it would be macOS-only.
#
# `runner` is earned by a committed attestation shard whose finding prose
# quotes `/home/runner`. Shards are immutable, so the name is declared a
# placeholder rather than the shard exempted, which would widen the hole.
HOME_PLACEHOLDERS = {"me", "agent", "runner", "..."}

# The committed shards that quote a real home path, each exempted BY PATH.
#
# Review evidence is append-only and content-addressed: the third component of
# a shard's filename is a digest of its bytes (warden/memory.py _shard_name),
# so editing one to tidy a path both falsifies the record and breaks the name
# that addresses it (.warden/rules/evidence-intact.md). Attest evidence is free
# reviewer text, and each shard below quotes a maintainer's checkout path in a
# finding's `evidence` prose.
#
# Exempted path by path rather than as the `.warden/memory/` prefix,
# deliberately. That directory grows on every merged PR, so a prefix skip would
# leave every FUTURE shard unchecked — a scan reporting clean over a partial
# set, which is the shape `.warden/rules/fail-closed.md` names. A shard is
# immutable, so this list can only ever be appended to knowingly.
HOME_PATH_EXEMPT = frozenset({
    ".warden/memory/attest/20260824T232450Z-07c8a542-3542fe9b.json",
    ".warden/memory/attest/20260914T235841Z-06fd5e68-7162d90c.json",
})


def _home_offenders(named_texts) -> list[str]:
    """The detector itself, over `(label, text)` pairs.

    Separate from the tree scan so a FIRES case can drive the REAL matching
    logic over inline strings. The tree scan is an absence assertion — it
    passes because the tree is clean — so without this seam the pattern and
    the allowlist could be neutered with the suite green.
    `.warden/rules/pattern-fit.md` asks for exactly this on a pattern that
    gates every future diff: a FIRES case and a SILENT one.
    """
    offenders = []
    for label, text in named_texts:
        for who in sorted(set(HOME_RE.findall(text))):
            if who not in HOME_PLACEHOLDERS:
                offenders.append(f"{label}: {who}")
    return sorted(offenders)


def _scan_paths(items) -> tuple[list, list]:
    """Read `(rel, Path)` pairs for scanning: `(named_texts, unreadable)`.

    Separate from the tree scan for the same reason `_home_offenders` is: the
    tree holds no undecodable tracked file, so the tree scan has no FIRES case
    for that branch, and an `except: continue` there would keep the suite
    green.

    A tracked SYMLINK is read with `readlink`, never through the link. For a
    symlink git stores the TARGET STRING as the blob, so the target is what a
    reader of this repo actually sees, and an absolute target is precisely the
    shape that leaks a home path. Following the link would also make the scan
    environment-dependent: a link pointing outside the repo reads fine on the
    author's laptop and raises OSError on a CI runner, so the guard would be
    green locally and red in CI for a reason unrelated to any leak.
    """
    named_texts, unreadable = [], []
    for rel, path in items:
        try:
            text = os.readlink(path) if path.is_symlink() \
                else path.read_bytes().decode()
        except (UnicodeDecodeError, OSError) as exc:
            unreadable.append(f"{rel}: {type(exc).__name__}")
            continue
        named_texts.append((rel, text))
    return named_texts, unreadable


def _exempt_set_problems(exempt, seen, texts) -> tuple[list, list]:
    """`(stale, idle)` for an exemption set.

    STALE: declared but never seen by the scan, so git does not track it.
    IDLE: seen, but carrying no real home path — the exemption buys nothing
    and only widens the hole.

    Pure, so the FIRES cases can drive it without mutating the repo: the tree
    scan alone cannot show that either check still fires.
    """
    stale = sorted(set(exempt) - set(seen))
    idle = sorted(rel for rel in sorted(set(exempt) - set(stale))
                  if not _home_offenders([(rel, texts[rel])]))
    return stale, idle


def test_scan_paths_fires_on_undecodable_and_reads_symlink_targets(tmp_path):
    """FIRES/SILENT on the reader, because the tree scan cannot prove it.

    Two branches: an undecodable file is reported rather than dropped, and a
    tracked symlink is scanned as its target string, so an absolute home path
    in the target is visible.
    """
    leak = "/Users/" + "somebody" + "/workspace/cage"

    good = tmp_path / "good.toml"
    good.write_text(f'dir = "{leak}"\n')
    binary = tmp_path / "probe.bin"
    binary.write_bytes(b"\xff\xfe\x00\x80binary")
    link = tmp_path / "link"
    link.symlink_to(leak)  # target need not exist; git stores the string

    named, unreadable = _scan_paths([
        ("good.toml", good), ("probe.bin", binary), ("link", link)])

    # FIRES: the undecodable file is REPORTED, never silently dropped
    assert unreadable == ["probe.bin: UnicodeDecodeError"], unreadable

    # the symlink is scanned as its TARGET STRING, so the leak is visible
    assert dict(named)["link"] == leak
    assert _home_offenders(named) == ["good.toml: somebody", "link: somebody"]

    # SILENT: an ordinary utf-8 file with nothing to find
    clean = tmp_path / "clean.md"
    clean.write_text("no home paths here\n")
    assert _scan_paths([("clean.md", clean)]) == ([("clean.md",
                                                    "no home paths here\n")], [])


def test_exempt_set_problems_fire_and_stay_silent():
    """FIRES/SILENT on the exemption guards, which were themselves deletable."""
    leak = "/Users/" + "somebody" + "/workspace"

    # FIRES on STALE: declared, never seen by the scan
    stale, idle = _exempt_set_problems(
        {"a.json", "gone.json"}, {"a.json"}, {"a.json": leak})
    assert (stale, idle) == (["gone.json"], [])

    # FIRES on IDLE: seen, but the file carries no real home path
    stale, idle = _exempt_set_problems(
        {"a.json"}, {"a.json"}, {"a.json": "nothing to exempt"})
    assert (stale, idle) == ([], ["a.json"])

    # SILENT: the shipped shape — one path, seen, carrying a real leak
    assert _exempt_set_problems(
        {"a.json"}, {"a.json"}, {"a.json": leak}) == ([], [])

def test_home_path_detector_fires_and_stays_silent():
    """FIRES/SILENT on the detector, because the tree scan cannot prove it.

    The tree-scan guard below asserts an absence, so a broken detector (a
    HOME_RE that can never match a real home path) and a clean tree are the
    same observation. These cases separate them.
    """
    # FIRES: a real home path under either root, named in the offender line.
    # Composed rather than written literally — a literal here would be a real
    # home path in a tracked file, and the scan below would flag this file.
    mac = "/Users/" + "somebody"
    linux = "/home/" + "someone"
    fires = _home_offenders([
        ("fixture.toml", f'dir = "{mac}/workspace/cage"'),
        ("ci.yml", f"path: {linux}/.cache/uv"),
    ])
    assert fires == ["ci.yml: someone", "fixture.toml: somebody"], fires

    # SILENT: every declared placeholder, and a path that is not a home at all
    assert _home_offenders([
        ("wiki.md", '/Users/me/workspace/hello-svc'),
        ("test_cage.py", 'home=Path("/home/agent")'),
        ("prose.py", "'/Users/...' became query"),
        ("unrelated.py", "/usr/local/bin, /var/root, /home"),
    ]) == []

    # ONE FIRES CASE PER CHARACTER CLASS MEMBER of the username. Each member
    # covers a real account name — a capitalised login, a numbered one, and
    # the punctuation characters macOS and Linux allow. Deleting a member makes
    # the pattern match a PREFIX of the name or nothing at all, so the offender
    # line stops naming the account — which is the only thing the failure
    # message gives the author to go and remove.
    for member, who in (("A-Z", "Somebody"), ("a-z", "somebody"),
                        ("0-9", "dev2"), ("_", "some_body"),
                        (".", "some.body"), ("@", "some@body"),
                        ("-", "some-body")):
        assert _home_offenders([("f.toml", "/Users/" + who + "/workspace")]) \
            == [f"f.toml: {who}"], (
            f"the username class member {member!r} is an alternative no "
            f"control exercises: the account {who!r} is not reported whole, so "
            "the failure message cannot name what to remove")


def _home_path_problems(items, exempt) -> list[str]:
    """Every reason a tracked file set fails the home-path contract.

    ONE aggregate catcher rather than separate assertions in the caller:
    pinning the helpers does not pin their USE — a unit test of
    `_exempt_set_problems` stays green when the caller stops calling it.
    Collapsing them into one function that is itself driven by a FIRES case
    shrinks the surface: deleting this call empties the test visibly instead
    of weakening it quietly.

    Residual, stated rather than implied: no test can stop someone deleting an
    assertion outright. What is achievable is that every BRANCH here fires
    somewhere, and that the caller has exactly one thing to delete.
    """
    scan, exempt_seen = [], set()
    for rel, path in items:
        if rel in exempt:
            exempt_seen.add(rel)
            continue
        scan.append((rel, path))

    named_texts, unreadable = _scan_paths(scan)
    problems = [f"unreadable, so the scan saw only part of the tree: {u}"
                for u in sorted(unreadable)]

    exempt_texts, exempt_unreadable = _scan_paths(
        [(rel, path) for rel, path in items if rel in exempt_seen])
    problems += [f"exempt path unreadable, so its exemption is unjustified: {u}"
                 for u in sorted(exempt_unreadable)]
    if not exempt_unreadable:
        stale, idle = _exempt_set_problems(
            exempt, exempt_seen, dict(exempt_texts))
        problems += [f"exempt but git does not track it: {s}" for s in stale]
        problems += [f"exempt but carries no real home path: {i}" for i in idle]

    problems += [f"real home directory: {o}"
                 for o in _home_offenders(named_texts)]
    return problems


def test_home_path_problems_fires_on_every_branch(tmp_path):
    """FIRES on all four branches, and SILENT on the shape the tree ships.

    The tree scan can only assert an absence, so each branch is proved here or
    not at all.
    """
    leak = "/Users/" + "somebody" + "/workspace"

    leaky = tmp_path / "leaky.toml"
    leaky.write_text('dir = "' + leak + '"\n')
    binary = tmp_path / "probe.bin"
    binary.write_bytes(b"\xff\xfe\x00\x80")
    clean_exempt = tmp_path / "clean-exempt.json"
    clean_exempt.write_text("{}\n")
    real_exempt = tmp_path / "real-exempt.json"
    real_exempt.write_text('{"evidence": "' + leak + '"}\n')
    placeholder = tmp_path / "wiki.md"
    placeholder.write_text("/Users/me/workspace/hello-svc\n")

    problems = _home_path_problems(
        [("leaky.toml", leaky), ("probe.bin", binary),
         ("clean-exempt.json", clean_exempt),
         ("real-exempt.json", real_exempt)],
        {"clean-exempt.json", "gone.json"})
    joined = "\n".join(problems)
    assert "real home directory: leaky.toml: somebody" in joined, joined
    assert "unreadable" in joined and "probe.bin" in joined, joined
    assert "git does not track it: gone.json" in joined, joined
    assert "carries no real home path: clean-exempt.json" in joined, joined

    # SILENT: the shape the tree actually ships — a placeholder path, plus one
    # exempt file that really does carry a leak, so the exemption is earned.
    assert _home_path_problems(
        [("wiki.md", placeholder), ("real-exempt.json", real_exempt)],
        {"real-exempt.json"}) == []


def test_no_maintainer_home_path_is_tracked():
    """A tracked `/Users/<someone>` is an operator detail the repo does not need.

    Nothing in the tree is a function of a maintainer's home prefix, so a real
    one in a tracked file tells every reader the author's username and
    directory layout and buys nothing, at any level of visibility.

    Written as an ALLOWLIST of placeholder names rather than a search for a
    known username. A guard keyed on a name would have to spell it out,
    putting into the tree the string it exists to keep out, and it would pass
    for the next contributor's home directory. This one fails for any name
    not declared a placeholder — a property
    `test_home_path_detector_fires_and_stays_silent` exercises directly,
    because this test alone could not.

    Reading is delegated to `_scan_paths`, which is pinned by its own
    FIRES/SILENT test: undecodable files are collected and asserted-empty
    rather than skipped ("binary, so it cannot carry a home path" is false — a
    PNG or PDF can carry an authoring path in its metadata), and a tracked
    symlink is scanned as its target STRING, which is what git stores and what
    a reader of the repo sees. The exemption-minimality check is likewise
    delegated to `_exempt_set_problems` and pinned there. Every branch this
    test relies on has a firing case somewhere, because this test itself can
    only ever assert an absence.
    """
    # The public export carries none of the exempted shards (publish.yaml
    # leaves .warden/memory/ out), so there they are not stale, only absent.
    exempt = frozenset(rel for rel in HOME_PATH_EXEMPT
                       if not (EXPORT and publish_excludes(rel)))
    problems = _home_path_problems(
        [(p.relative_to(ROOT).as_posix(), p) for p in tracked("*")], exempt)
    assert not problems, (
        "the tree fails the home-path contract. Replace a real home directory "
        "with a placeholder (`/Users/me`, matching "
        "docs/wiki/Configuration.md), or add the name to HOME_PLACEHOLDERS if "
        "it is one (`runner` for a CI runner path, say). Drop an exemption "
        "that is stale or buys nothing:\n  " + "\n  ".join(problems))


def test_the_root_carries_exactly_one_license_and_every_surface_agrees():
    """One license file at the root, and every surface states the same license.

    Two license files with incompatible terms (PolyForm Shield forbids
    redistribution as a competing offering; Apache-2.0 permits it) are not a
    duplicate but an unresolvable statement about what rights an adopter has.
    Nothing else in the tree can see that: `bd`, the review rules and the gate
    all look at diffs and rules, and none of them knows the repo is supposed to
    answer the license question exactly once. This is that check.

    It pins the POSTURE, deliberately, not merely the arithmetic. Asserting
    only "exactly one license file" would stay green through a silent swap of
    the terms, an outward-facing and effectively one-way change. So the
    machine-readable surfaces (LICENSE.md, pyproject.toml, the marketplace
    manifest, the README) must all say the same thing, and a founder who
    genuinely changes the license moves them together in one commit that also
    edits this test. That is the intended cost: a license change should be
    impossible to make by accident, and cheap to make on
    purpose.
    """
    import json

    # `LICENCE`/`COPYING` are the spellings a well-meaning tool reaches for.
    root_licenses = sorted(
        p.name for p in ROOT.iterdir()
        if p.is_file() and re.match(r"(?i)^(licen[cs]e|copying)\b", p.name))
    assert root_licenses == ["LICENSE.md"], (
        "the repo root must carry exactly one license file, LICENSE.md — "
        f"found {root_licenses}. Two license files is not a duplication, it "
        "is two incompatible answers to 'what may an adopter do'. A license "
        "CHANGE removes the old file; it never adds a second beside it")

    license_text = (ROOT / "LICENSE.md").read_text()
    assert "PolyForm Shield License 1.0.0" in license_text, (
        "LICENSE.md no longer carries the PolyForm Shield 1.0.0 text the "
        "license decision chose. If this is a deliberate license change, it "
        "supersedes that decision and moves "
        "every surface below in the same commit")
    assert "Copyright (c) 2026 NightWatchEng" in license_text, (
        "LICENSE.md lost its Required Notice copyright line — Shield's "
        "notice clause is the half that stops the work being presented as "
        "someone else's, which is one of the four founder requirements "
        "the license decision records")

    pyproject = (ROOT / "pyproject.toml").read_text()
    assert 'license = { text = "PolyForm-Shield-1.0.0" }' in pyproject, (
        "pyproject.toml's license field disagrees with LICENSE.md — the "
        "package metadata is what a `uvx --from git+` consumer resolves")

    marketplace = json.loads(
        (ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert marketplace["license"] == "PolyForm-Shield-1.0.0", (
        "the marketplace manifest disagrees with LICENSE.md — this is the "
        "string a consumer sees before installing the plugin")

    readme = (ROOT / "README.md").read_text()
    assert "source-available, not open source" in readme, (
        "the README stopped stating the posture in the words that separate "
        "it from OSI open source, which is the exact confusion this guard "
        "exists to prevent")
    assert "(LICENSE.md)" in readme, (
        "the README's license section no longer links the authoritative text")


def _placeholder_use_subject() -> list[tuple[str, Path]]:
    """The tracked files whose home paths COUNT as earning a placeholder.

    ONE derivation, two callers — the test below and its named regression
    test. A regression test that re-typed the filter would stay green with the
    exclusions here reverted: pinning a rule's shape does not pin the rule the
    caller actually uses.

    Two exclusions, each for a different reason, and NEITHER is an exemption
    from being scanned — `test_no_maintainer_home_path_is_tracked` still reads
    both. They are exclusions from counting as USE:

    - **this module**, because it DECLARES `HOME_PLACEHOLDERS`. Without this,
      the comment declaring a name is what earns it, and the failure message
      below instructs a widener to write exactly that comment.
    - **`HOME_PATH_EXEMPT`**, because those paths are exempted precisely for
      carrying a REAL maintainer name. Counting them would mean the one real
      username in the tree is already "used", so adding exactly that name to
      `HOME_PLACEHOLDERS` — the bad addition this whole test exists to make
      expensive — would satisfy minimality on the strength of the very file
      that forced the exemption.

    Non-exempt memory shards stay IN, deliberately. A shard is not a
    self-declaration; it is immutable committed content the scan must
    tolerate, and for `runner` one such shard is the entire reason the
    placeholder is declared.
    """
    return [(p.relative_to(ROOT).as_posix(), p) for p in tracked("*")
            if p != Path(__file__)
            and p.relative_to(ROOT).as_posix() not in HOME_PATH_EXEMPT]


def test_the_home_placeholder_set_is_pinned_and_every_name_is_earned():
    """The last way to defeat the home-path guard without touching its logic.

    `HOME_PATH_EXEMPT` is guarded both ways by `_exempt_set_problems`, and
    `HOME_PLACEHOLDERS` needs a guard of its own. Adding one plausible-looking
    username to it turns `test_no_maintainer_home_path_is_tracked` green over
    a tree that leaks that exact name — a one-word edit, in a set whose whole
    purpose is to be edited when a genuine placeholder appears.

    There is no machine oracle for "is this a placeholder or a person", so
    this does not attempt one. It makes the widening EXPENSIVE and VISIBLE
    instead, the same trade
    `test_the_root_carries_exactly_one_license_and_every_surface_agrees` takes
    on the licence: the set is
    pinned literally, so widening it edits an assertion whose failure message
    says what the set is for, and the diff shows a reviewer both halves of the
    change at once. Stated residual, because the alternative is implying more
    than is true: someone editing both can still do it. What they cannot do is
    do it by accident, or unseen.

    The second half is minimality, matching `_exempt_set_problems`: every
    declared placeholder must actually appear as a home path somewhere in the
    tree. It reads every tracked file EXCEPT this module, the one that
    DECLARES the set, and the exempt shards. Without the first exclusion the
    declaring comment would earn the name, so complying with the failure
    message above ("say which in the comment above the set") would itself
    satisfy minimality.

    The other memory shards are deliberately NOT excluded. A shard is not a
    self-declaration; it is independent, immutable, committed content that the
    scan above must tolerate, and for `runner` it is the whole reason the
    placeholder exists — one attestation shard quotes `/home/runner` in a
    finding's prose and cannot be edited, so the name is EARNED by exactly the
    file that forced it. Excluding shards would report `runner` idle and invite
    dropping the placeholder that keeps the tree green, which is the opposite
    of what minimality is for. (`.github/` carries no `/home/runner` at all;
    the shard does.)
    """
    assert HOME_PLACEHOLDERS == {"me", "agent", "runner", "..."}, (
        "HOME_PLACEHOLDERS changed. Every name here switches OFF the "
        "de-personalization guard for that name across the whole tree, so a "
        "person's username added here silently re-permits the leak "
        "the de-personalization sweep removed. Add a name only if it is genuinely not a "
        "person — a placeholder the wiki uses, or a machine account like a CI "
        "runner — and say which in the comment above the set. Then update "
        "this assertion in the same commit, so the widening is one reviewable "
        "change and not two")

    subject = _placeholder_use_subject()
    named_texts, _ = _scan_paths(subject)

    # The caller's OWN scan, asserted here rather than only in the helper's
    # regression test: pinning the derivation's shape leaves the call site
    # bypassable, since an inline `tracked("*")` scan here would leave the
    # helper and its test untouched. These two assertions on the local
    # `subject` fail for ANY scan that does not apply the exclusions, however
    # it was obtained.
    scanned = {rel for rel, _ in subject}
    assert "tests/test_docs.py" not in scanned, (
        "this test is counting its own declaring module as use, so the "
        "comment above HOME_PLACEHOLDERS earns the name it declares — the "
        "exact defect `_placeholder_use_subject` exists to prevent")
    assert not (HOME_PATH_EXEMPT & scanned), (
        "this test is counting the home-path-exempt files as use. They are "
        "exempted for carrying a real maintainer name, so that name would "
        f"already be 'used': {sorted(HOME_PATH_EXEMPT & scanned)}")
    # Unreadable files are not asserted on here. That is deliberate and it is
    # fail-CLOSED: a file that does not decode cannot contribute a USE, so it
    # can only make `idle` longer and this test stricter, never laxer. The
    # tree-wide unreadable failure belongs to
    # `test_no_maintainer_home_path_is_tracked`, which reports it once with a
    # message about the thing that is actually wrong.
    used = {who for _, text in named_texts for who in HOME_RE.findall(text)}
    idle = sorted(HOME_PLACEHOLDERS - used)
    assert not idle, (
        f"declared placeholders that no tracked file uses: {idle}. A "
        "placeholder nobody writes cannot be protecting anything, and it "
        "stays behind to cover a name it was never granted for. Writing the "
        "name into the comment above the set does NOT satisfy this — this "
        "file and the memory shards are excluded from counting as use, so "
        "the name must be earned by a fixture, a doc, or a workflow that "
        "genuinely needs it. If nothing does, drop it from HOME_PLACEHOLDERS")


def test_the_placeholder_minimality_check_is_not_self_satisfying():
    """Named regression: the minimality check must not certify itself.

    If `used` were computed over `tracked("*")`, which includes this module, a
    placeholder would be "earned" by the comment declaring it — and the
    failure message instructs a widener down exactly that path.

    The exclusions are narrow, and both halves need pinning: too narrow and
    the check is inert, too wide and it reports a genuinely-earned name as
    idle — which for `runner` would invite dropping the placeholder that
    keeps `test_no_maintainer_home_path_is_tracked` green over an immutable
    shard. So this asserts the declaring module and the exempt set are out
    AND that ordinary tracked files, memory shards included, are still in.
    """
    # Driven through the SHARED derivation, not a re-typed copy of it: a
    # re-typed filter stays green when the caller's exclusions are reverted.
    subject = {rel for rel, _ in _placeholder_use_subject()}
    assert "tests/test_docs.py" not in subject, (
        "the minimality check reads its own declaring file again, so a "
        "placeholder written into the comment above the set earns itself")
    assert not (HOME_PATH_EXEMPT & subject), (
        "the minimality check counts the home-path-EXEMPT files as use. Those "
        "are exempted for carrying a real maintainer name, so counting them "
        "means that name is already 'used' and adding it to "
        "HOME_PLACEHOLDERS would pass minimality on the strength of the very "
        "file that forced the exemption")
    assert "tests/test_cage.py" in subject, (
        "the exclusion has widened past the declaring module and the exempt "
        "set, and is hiding ordinary fixtures from the use count")
    assert any(s.startswith(".warden/memory/") for s in subject), (
        "every memory shard was excluded from the use count. Non-exempt "
        "shards are not self-declarations — they are immutable committed "
        "content the scan must tolerate, and the shard quoting `/home/runner` "
        "is the whole reason that placeholder is declared")

    # And the derivation itself, driven over composed strings. Composed
    # rather than written literally for the reason `_home_offenders`'s own
    # FIRES case gives: a literal here is a real home path in a tracked file,
    # and the tree scan above would flag this module.
    name = "jenk" + "ins"
    fake = f"/home/{name}"

    def earned(named_texts):
        return {who for _, text in named_texts for who in HOME_RE.findall(text)}

    assert earned([("tests/golden/cage.toml", f'home = "{fake}"')]) == {name}
    assert earned([("unrelated.md", "no home paths here")]) == set()


def test_the_stats_dispatches_footnote_states_all_three_causes_of_no_review(
        tmp_path):
    """Memory.md is not the only place `no-review` is defined: `memory stats`
    prints its own footnote under DISPATCHES, and a reader of the report
    never opens the wiki. Defining `no-review` as an output naming no file the
    diff changed is false for a dispatch that did not return (it has no
    output) and for a builder-declared no-review (it stands whatever the
    output names). The rendered footnote must carry the same three causes as
    the page."""
    from warden import memory as memory_mod
    doc = memory_mod.stats(tmp_path, records=[])
    doc["dispatches"] = {"code-reviewer": {
        "dispatches": 1, "returned": 0, "reviewed-clean": 0,
        "reviewed-findings": 0, "no-review": 1, "unknown": 0}}
    out = memory_mod.render_stats(doc)
    footnote = out[out.index("DISPATCHES"):]
    footnote = " ".join(footnote[:footnote.index("\n  dispatch ")].split())
    assert "`no-review` is an output naming no file" not in footnote, (
        "the stats footnote still defines no-review by one cause:\n" + footnote)
    assert "names no file the diff changed" in footnote, footnote
    assert "did not return" in footnote, (
        "the stats footnote omits the unreturned dispatch:\n" + footnote)
    assert "declared" in footnote and "stands" in footnote, (
        "the stats footnote omits that a builder-declared no-review "
        "stands:\n" + footnote)


@needs_design_records
def test_roadmap_credits_the_crossing_to_the_reading_that_caught_it():
    """The Roadmap's promotion paragraphs say who caught the crossing, and
    the retro they rest on is the check.

    Round 1 of the attribution-holds branch wrote "sat there unnoticed while
    no retro ran" and "with nobody told — the 2026-09-15 retro found it"
    twelve lines apart. The retro's own file refuses both: its table carries
    a tracking-item column, dated a day before the retro ran, in which this
    class's crossing is already read. Crediting the retro with the discovery
    is the fourth shape of the rule that branch promotes — an act credited to
    an actor whose own record does not carry it — on the page that narrates
    the promotion.

    Derived: the date comes out of the retro's table header and the reading
    out of the row under it, so the page is held to the retro rather than to
    a date typed here. Both erasures are named, because fixing one left the
    other standing."""
    retro = (ROOT / "docs" / "design" / "retro-20260915.md").read_text()
    lines = retro.splitlines()
    header = next(line for line in lines if "tracking item," in line)
    cells = [c.strip() for c in header.strip().strip("|").split("|")]
    column = next(i for i, c in enumerate(cells) if c.startswith("tracking item,"))
    date = re.search(r"(\d{4}-\d{2}-\d{2})", cells[column]).group(1)
    row = next(line for line in lines
               if line.startswith("| `evidence-attribution`"))
    reading = [c.strip() for c in row.strip().strip("|").split("|")][column]
    assert "judged" in reading, (
        f"the retro's {date} column holds no reading for this class "
        f"({reading!r}) — the Roadmap's attribution has lost its source")

    road = (WIKI / "Roadmap.md").read_text()
    assert date in road, (
        f"docs/wiki/Roadmap.md does not name {date}, the day the tracking "
        f"item read the crossing ({reading}) — the retro that ran the day "
        "after did not find it")
    for erased in ("sat there unnoticed", "with nobody told",
                   "the 2026-09-15 retro found it"):
        assert erased not in road, (
            f"docs/wiki/Roadmap.md says {erased!r}, which the retro's own "
            f"{date} column contradicts")


def test_graph_layer_names_both_bypasses_the_round_binding_leaves_open():
    """The page enumerates what the round binding does NOT close, and it
    named only half of it.

    `--review-dir` is the one it named: omit the flag and nothing is
    compared. The closer one is the rounds root itself — a directory under
    the gitignored `.warden/out/rounds/` holding a `head_sha` of an ancestor
    raises the number `round new` mints, and a higher number buys round 2's
    smaller crew. `repair.next_round_number`'s docstring states it plainly;
    the page a consumer reads must too, or the page teaches a binding
    stronger than the one that ships.
    """
    from warden import repair as repair_mod

    doc = (WIKI / "Graph-Layer.md").read_text()
    binding = doc[doc.index("The round a roster claims is itself checked"):]
    binding = binding[:binding.index("\n## ")]
    assert "--review-dir" in binding, "the page lost the flag bypass"
    assert ".warden/out/rounds/" in binding, (
        "the page still omits the rounds-directory bypass: anything that can "
        "write under the gitignored rounds root raises the minted number")
    assert "rounds root is an ordinary" in repair_mod.next_round_number.__doc__, (
        "the docstring the page is held equal to no longer states the bypass")


def test_graph_layer_crew_sample_is_real_graph_crew_output(tmp_path,
                                                           monkeypatch,
                                                           capsys):
    """The page's `warden graph crew --round 1` sample is what the COMMAND
    prints for this repo's graph.

    The skills dispatch the roster that command returns, so the only worked
    example of it a reader can check has to be OUTPUT rather than a retyped
    copy — and not a retyped copy of the CLI's own
    SERIALIZATION either. This asserted `json.dumps(graph.crew(...),
    indent=2)`, which is the CLI's formatting written out a second time: drop
    the `indent` from `warden/cli.py` and the page's fenced block stops
    matching the command's stdout with this test and its authority sibling
    both green. The sibling is
    `tests/test_graph_delegation.py::test_graph_layer_authority_sample_is_real_command_output`,
    which had the identical shape; both are driven through `cli.main` now.

    THE FIXTURE CARRIES THIS REPO'S GRAPH, and the equality below is what
    makes that legitimate rather than a second retyping: the crew resolution
    reads the root, so the fixture's answer is asserted equal to the real
    root's before its stdout is compared with the page.
    """
    import json as json_mod

    import yaml as yaml_mod

    from warden import cli as cli_mod
    from warden import graph as graph_mod
    from test_graph_delegation import _repo

    doc = (WIKI / "Graph-Layer.md").read_text()
    real = graph_mod.crew(graph_mod.load(ROOT), ROOT, 1)
    root = _repo(tmp_path, None)
    graph_text = (ROOT / "graph.yaml").read_text()
    (root / "graph.yaml").write_text(graph_text)
    # `warden graph` validates before it answers, and a lens pointing at a
    # rule file that is not there is a refusal — correctly. Seed the files the
    # real graph's lenses name, derived from the document rather than listed.
    rules = root / ".warden" / "rules"
    for lens in (yaml_mod.safe_load(graph_text)["review"].get("lenses") or []):
        if lens.get("rule"):
            (rules / f"{lens['rule']}.md").write_text(
                f"---\nid: {lens['rule']}\n---\n")
    monkeypatch.chdir(root)
    assert cli_mod.main(["graph", "crew", "--round", "1"]) == 0, \
        capsys.readouterr().err
    printed = capsys.readouterr().out
    assert json_mod.loads(printed) == real, (
        "the fixture resolves a different round-1 crew than this "
        "repository's own root, so its stdout is not the sample the page "
        f"claims to show: {json_mod.loads(printed)} != {real}")
    blocks = [b.strip() for b in _fenced_blocks(doc, "json")]
    assert printed.strip() in blocks, (
        "Graph-Layer.md's crew sample is not `warden graph crew --round 1` "
        "STDOUT — the values may be right while the serialization is not. "
        "Regenerate it by running the command")


def test_graph_layer_states_the_trigger_the_ship_roster_step_actually_reads():
    """The page and the code agree about what turns ship's roster step ON.

    The first cut asked a function that fully validates graph.yaml before it
    could answer "no review key", so a consumer whose graph.yaml this
    warden's schema rejects lost `warden ship` entirely — every run,
    `--checks-only` included — while this page told it twice that a repo
    declaring no crew was untouched. The trigger is now the narrow one, and
    the page says which document states it is: the `review` key.
    """
    doc = (WIKI / "Graph-Layer.md").read_text()
    section = doc[doc.index("**Which commands require `--review-dir`.**"):]
    # Flattened: the page is hard-wrapped, so a claim this guard reads as one
    # sentence is a line break away from being unreadable to it — and a guard
    # that goes green on a reflow is the shape this whole round is about.
    section = " ".join(section[:section.index("\n## ")].split())
    for needle in ("DECLARE a `review` block", "ships exactly as it did before",
                   "cannot decode or parse"):
        assert needle in section, (
            f"the page does not state the shipped trigger ({needle!r}): a "
            "consumer reading it cannot tell whether an invalid graph.yaml "
            "that declares no crew still ships")
    ship_src = (ROOT / "warden" / "ship.py").read_text()
    assert "declares_review" in ship_src, (
        "ship no longer asks the document whether it DECLARES a review "
        "block, so the narrow trigger this page describes is not the one "
        "that ships")


def test_no_page_claims_a_skill_records_which_roster_source_it_took():
    """'both fall back to the documented default and say so in the
    attestation' was false twice over, and this holds both halves.

    (1) The attestation schema is closed — `additionalProperties: false`, a
    fixed top-level property list — so no shard can record that its roster
    was a skill's default; the one roster field records whether warden
    COMPARED the roster, never where it came from. (2) Only `pre-pr-review`
    falls back; `deliver` carries no no-crew branch and delegates the
    dispatch to it.
    """
    import json as json_mod

    schema = json_mod.loads((ROOT / "warden" / "schemas"
                             / "attestation.schema.json").read_text())
    assert schema["additionalProperties"] is False, (
        "the attestation schema is no longer closed — the claim these pages "
        "used to make may now be recordable, so re-read them before relaxing "
        "this guard")
    assert set(schema["properties"]["roster_verification"]["enum"]) == {
        "verified", "unverified-roster"}, (
        "the roster field's vocabulary moved; it may now carry a source")
    page = (WIKI / "Graph-Layer.md").read_text()
    regions = {
        "Graph-Layer.md": page[page.index("The skill pack dispatches"):
                               page.index("`warden attest write --review-dir`"
                                          " checks the roster")],
        "Skill-Pack.md 0.20.0": next(
            line for line in (WIKI / "Skill-Pack.md").read_text().splitlines()
            if line.startswith("| 0.20.0 ")),
    }
    for where, text in regions.items():
        assert "in the attestation" not in text, (
            f"{where} claims a skill records its roster source in the "
            "attestation; the schema is closed and carries no such field")
        assert "pre-pr-review" in text and "delegat" in text, (
            f"{where} does not say which skill falls back and that the other "
            "delegates to it — 'both fall back' was the false half")


def test_the_skill_pack_row_lens_fallback_is_the_one_the_skill_ships():
    """The LENS half of the 0.20.0 row, joined to the skill that implements it.

    The row's roster half has been held to the pack
    since it shipped (the guard above), and the lens half had nothing: the
    repair that gave `pre-pr-review`'s no-crew fallback a LENS source — the
    policy file's `## Review charter` — could be reverted, or the row's
    paragraph rewritten, with every joining test green, because those tests
    read the roster half alone. That is the drift the row exists to prevent,
    on the clause added to prevent it.

    THREE STATEMENTS, ONE FACT. The row tells a consumer what changed, the
    skill is what runs, and `Skills-Policy.md` defines the section the skill
    is sent to. A charter that stopped admitting an inline checklist would
    send the fallback to a place a repo with no `review` block cannot state a
    lens in, which is the case the fallback exists for.
    """
    row = next(line for line in (WIKI / "Skill-Pack.md").read_text().splitlines()
               if line.startswith("| 0.20.0 "))
    skill = (ROOT / "skills" / "nightgate-skills" / "skills" / "pre-pr-review"
             / "SKILL.md").read_text()
    policy_row = next(
        line for line in (WIKI / "Skills-Policy.md").read_text().splitlines()
        if line.startswith("| `## Review charter`"))

    assert "LENSES fall back" in row, (
        "the 0.20.0 row no longer states that the lenses fall back beside the "
        "roster, so a consumer bumping its pin reads the roster fallback as "
        "the whole of the no-crew behaviour")
    for where, text in (("Skill-Pack.md's 0.20.0 row", row),
                        ("pre-pr-review/SKILL.md", skill)):
        assert "lenses fall back" in text.lower(), (
            f"{where} does not carry the lens fallback at all")
        assert "Review charter" in text, (
            f"{where} states a lens fallback without naming where it reads "
            "the checklists from — `## Review charter` is the only other "
            "source, and a fallback with no source reaches no reviewer")
    assert "resolves a crew" in skill, (
        "pre-pr-review no longer SCOPES the printed lenses to the case where "
        "`graph crew` resolves a crew, which is the scoping the row states "
        "— an unscoped claim there is the absolute the row withdrew")
    # The ADMISSION, not the word: `policy_row` says "inline" twice more
    # while explaining the rule — three times in all, one of them the
    # admission — so `"inline" in policy_row` passes with the admission
    # itself deleted. Measured, and the reason this reads the clause that
    # GRANTS the inline checklist rather than the word.
    #
    # NAMED, never "the row": `row` is bound at the top of this function to
    # Skill-Pack.md's 0.20.0 row, where the word "inline" never occurs at
    # all, so a lead-in that says "the row" sends a reader to the wrong
    # binding and makes a true sentence read as a false one. A review caught
    # the same slip in the EXPRESSION here before it caught it in the prose;
    # both now name which row they mean.
    assert "checklist written inline" in policy_row, (
        "`## Review charter` no longer admits an inline checklist as one of "
        "its sources, so the skill's no-crew fallback is sent to a section "
        "that cannot state a lens for the repo the fallback exists for")


def test_graph_layer_certify_exemption_matches_what_certify_actually_reads():
    """The certify half of the roster acceptance is scoped out, and the page
    is not the record of it.

    Two things must stay true together: no certify rung reads
    `roster_verification` (or the exemption is stale), and the page points at
    the committed ruling rather than being it — this repo records a ruling
    that changes what ships as a decision shard, and `.warden/**` is HIGH
    tier here.
    """
    from warden import certify as certify_mod

    src = Path(certify_mod.__file__).read_text()
    assert "roster_verification" not in src, (
        "a certify rung now reads the roster field, so Graph-Layer.md's "
        "'`warden certify` deliberately does not' is false and the recorded "
        "ruling behind it needs re-deciding")
    page = (WIKI / "Graph-Layer.md").read_text()
    para = page[page.index("`warden certify` deliberately does not"):]
    para = para[:para.index("\n\n")]
    assert "unverified-roster" in para, (
        "the exemption still weighs only the widest rung: the narrow one, "
        "keyed to the builder-asserted state, is the option that was declined")
    assert "warden decide" in para, (
        "the page is the only record of the scope-out again — name the "
        "decision store the ruling is committed in")


def test_no_shipped_text_claims_ship_and_attest_draw_one_crew_boundary():
    """`warden ship` and `warden attest write --review-dir` do not turn their
    roster check on at the same document, and nothing shipped says they do.

    The claim was made where it did the most damage: inside
    `ship._declared_crew`'s own docstring, as the narrowing's recorded
    rationale, and again on Graph-Layer.md for the attest seam. ship asks
    `graph.declares_review` — the document's KEYS. The `--review-dir` branch
    asks `graph.declared_crew`, which loads and schema-validates the whole
    document before it can answer "no review key", so it refuses a graph.yaml
    this schema rejects while declaring no crew — which ship ships.

    THE ASYMMETRY IS NOW RULED rather than tracked, so what the three surfaces
    owe has changed with it: they used to have to name the open item, and they
    now have to name the DECISION STORE the ruling is committed in. The
    substance of the requirement is unchanged and is the reason the guard
    exists — a reader must not be able to take one seam's boundary for the
    other's — but pointing at an item that is closed would send that reader
    nowhere.

    Three things are held together, because the ruling is only honest while all
    three hold: the divergence is still real (or the texts are stale and must
    be re-read, not the guard relaxed), the withdrawn sentence has not come
    back, and every place that states either seam's boundary points at the
    ruling instead of letting a reader infer the other seam from it.
    """
    from warden import ship as ship_mod

    cli_src = (ROOT / "warden" / "cli.py").read_text()
    branch = cli_src[cli_src.index("    review_crew = None"):
                     cli_src.index("    doc = attest_mod.build(")]
    assert "declared_crew(config.root)" in branch, (
        "the `--review-dir` branch no longer resolves the declared crew, so "
        "what the withdrawn claim got wrong may have moved — re-read the "
        "`warden decide` ruling on the divergence before relaxing this guard")
    assert "declares_review" not in branch, (
        "the `--review-dir` branch now asks the NARROW question too, so the "
        "divergence the ruling records may be closed: re-read "
        "`_declared_crew`'s docstring, Graph-Layer.md and the Skill-Pack "
        "0.20.0 row, which all still say the seams differ")

    docstring = ship_mod._declared_crew.__doc__ or ""
    assert "boundary both Graph-Layer.md and" not in docstring, (
        "the withdrawn claim — that ship and `attest write --review-dir` draw "
        "one boundary — is back in `_declared_crew`'s docstring")
    page = (WIKI / "Graph-Layer.md").read_text()
    row = next(line for line in (WIKI / "Skill-Pack.md").read_text().splitlines()
               if line.startswith("| 0.20.0 "))
    for where, text in (("_declared_crew's docstring", docstring),
                        ("Graph-Layer.md", page),
                        ("Skill-Pack.md's 0.20.0 row", row)):
        assert "warden decide" in text, (
            f"{where} states a crew boundary without pointing at the store the "
            "ruling on the divergence is committed in, so a reader is left to "
            "take one seam's boundary for the other's")
        assert not re.search(r"\bagentops-[a-z0-9]{2,5}(?:\.[0-9]+)*(?![\w-])",
                             text), (
            f"{where} still sends a reader to a tracked item, which the "
            "ruling closed — the record is the decision shard now")


def test_the_pack_changelog_states_which_versions_it_logs():
    """A missing row must read as a decision, not as drift.

    The table's newest row was three versions behind `plugin.json` and a reader
    could not tell which of two things that meant: a log of notable changes
    working as intended, or a per-version obligation failing three times in a
    row. Nothing enforced either reading — the pack-version invariant requires
    the MANIFEST to move when a skill's text moves and says nothing about the
    page — so the ambiguity was the defect, whichever answer was right.

    THE RULING is the first: the table logs the versions a pinned consumer has
    to ACT on, which is the invocation surface and the judgment contract, the
    two kinds the section above it already names. A patch that corrects prose
    owes no row because there is nothing to act on. This holds the paragraph
    that says so, and the sentence that stops a reader expecting the newest row
    to equal `plugin.json` — both are the whole mechanism on the option that
    was chosen, so deleting either returns the page to the state that could not
    be read.
    """
    page = (WIKI / "Skill-Pack.md").read_text()
    head = page[page.index("## Versioning"):page.index("| Version | Changed |")]
    assert "NOT ONE ROW PER VERSION" in head, (
        "Skill-Pack.md no longer says the changelog logs only the versions a "
        "consumer must act on, so a version with no row reads as drift again "
        "and three gaps look like a per-version obligation failing")
    assert "owes no row" in head, (
        "the page states the rule without stating what it EXCUSES, which is "
        "the half a reader needs: a prose patch bumps the manifest and owes "
        "nothing here")
    assert "older than" in head and "plugin.json" in head, (
        "the page no longer warns that the newest row may be older than the "
        "manifest's version, which is the expectation that made the gaps look "
        "like a failure")


def test_the_pack_changelog_logs_no_version_the_manifest_never_reached():
    """The table may lag `plugin.json`; it may never run AHEAD of it.

    The paragraph above the table licenses gaps in one direction only — the
    newest row may be older than the manifest, because a prose patch owes no
    row. A row NEWER than the manifest is the opposite shape and has no honest
    reading: it tells a pinned consumer to act on a release the pack has not
    cut, describing skill text that is not in the tree.

    WHY IT IS HELD HERE. The direction the page licenses was the only one
    anything checked, so the other one was free. It arrived for real: a
    salvage lifted the commit that added a row without the commits that bumped
    the manifest and moved the skill, and the table advertised a version the
    pack did not have while the whole suite stayed green. Cherry-picks and
    reverts both produce that shape, and a reader cannot detect it from the
    page — the manifest is a different file.

    Versions are compared as release tuples, not strings, so `0.9.0` does not
    read as newer than `0.22.0`.
    """
    import json
    import re

    page = (WIKI / "Skill-Pack.md").read_text()
    manifest = json.loads(
        (ROOT / "skills" / "nightgate-skills" / ".claude-plugin"
         / "plugin.json").read_text())

    def release(text: str) -> tuple[int, ...]:
        return tuple(int(part) for part in text.split("."))

    shipped = release(manifest["version"])
    rows = re.findall(r"^\| (\d+\.\d+\.\d+) \|", page, re.M)
    assert rows, (
        "the pack changelog table has no version rows at all, so this guard "
        "reads nothing — the table moved or its row shape changed")
    ahead = [r for r in rows if release(r) > shipped]
    assert not ahead, (
        f"the changelog logs {ahead}, which `plugin.json` has never reached "
        f"(it is at {manifest['version']}). A row newer than the manifest "
        "tells a pinned consumer to act on a release that does not exist and "
        "describes skill text that is not in this tree — the shape a "
        "cherry-pick or a revert leaves when the row survives the change it "
        "documents")


def test_the_skill_pack_row_carves_out_the_graph_that_refuses_whatever_it_declares():
    """The 0.20.0 row's "ships as it did before" is not an absolute, and the
    row that a consumer reads when it bumps its pin says so.

    It used to end "valid or not". A graph.yaml warden cannot decode, parse or
    read at all refuses the WHOLE ship tail (exit 2) whatever keys it carries,
    and that state shipped on the base commit — so the absolute was false for
    exactly the case Graph-Layer.md is careful to carve out, on the one page
    written for this class of skew. The carve-out is held to that page rather
    than restated here.
    """
    row = next(line for line in (WIKI / "Skill-Pack.md").read_text().splitlines()
               if line.startswith("| 0.20.0 "))
    assert "valid or not" not in row, (
        "the withdrawn absolute is back in the 0.20.0 row: a graph.yaml "
        "warden cannot decode or parse refuses the ship tail whatever it "
        "declares")
    assert "cannot decode" in row and "cannot parse" in row, (
        "the 0.20.0 row names no exception for a graph.yaml warden cannot "
        "decode or parse, so it tells a consumer its ship tail is untouched "
        "when it is not")
    assert "refuses whatever it declares" in (WIKI / "Graph-Layer.md").read_text(), (
        "Graph-Layer.md no longer states the carve-out the 0.20.0 row is "
        "matched to — the two pages can no longer be read together")


@needs_corpus
def test_graph_layer_absent_shard_figure_is_derived_from_the_committed_corpus():
    """The certify-exemption paragraph turns on one number, and the page is
    held to the corpus for it rather than to a figure someone retyped.

    It shipped as "119 of this repo's 287 committed shards". 119 was right;
    287 was the total at the DECISION SHARD's head and was already wrong at
    the commit that wrote the sentence — the page restated a head-anchored
    measurement without its anchor. The two halves behave differently: a
    total moves with every merge, while the absent tally cannot, because
    every shard written since the field shipped carries it. So the page
    states the tally and leaves the total to the ruling that measured it,
    and this guard keeps the tally equal to what is committed.
    """
    import json as json_mod

    shards = tracked(".warden/memory/attest/*.json")
    assert shards, "no committed attestation shards to measure"
    absent = sum(1 for p in shards
                 if "roster_verification" not in json_mod.loads(p.read_text()))
    page = (WIKI / "Graph-Layer.md").read_text()
    para = page[page.index("`warden certify` deliberately does not"):]
    # Flattened: the page is hard-wrapped, so a claim this guard reads as one
    # sentence is a line break away from being unreadable to it.
    para = " ".join(para[:para.index("\n\n")].split())
    assert f"{absent} of this repo's committed shards predate the field" in para, (
        f"the page's absent-shard figure is not the committed corpus's "
        f"({absent} shard(s) carry no `roster_verification`) — the WIDE rung "
        "the paragraph declines is sized by exactly this number")
    assert not re.search(r"this repo's \d+ committed shards", para), (
        "the paragraph restates a corpus TOTAL, which moves with every merge "
        "and was already stale the day it was written — leave the total to "
        "the ruling in the decision store, which anchors it to a head")


# ── the hand-copied pre-flight step, pinned to the one the platform renders ──
#
# Three surfaces hand a consumer a gate job: `warden init`'s generated
# workflow, `examples/hello-svc`'s CI, and Adopting.md section 4 — the one a
# consumer copies BY HAND. The page says outright that its step's body is the
# one `warden init` renders, and until now nothing held it to that: the guard
# over that section derives from the page's own `repo.yaml` and pins the
# step's PRESENCE, ORDERING and the install/declare partition, never a byte of
# what it runs.

# The two places the page DELIBERATELY departs from the render, and — this is
# the claim — the only two. Each is (what the render says, what the page says
# instead), asserted on BOTH sides before it is applied: normalising a
# difference away without first checking it is still there is how a pin stops
# pinning.
_ADOPTING_ADAPTATIONS = (
    # The pair list. The page's gate runs hello-svc's `repo.yaml`, whose
    # verify commands invoke `python3`; the render is taken for Go because a
    # python-only enrollment renders no step at all. WHICH pairs the page
    # declares is already derived from the page's own repo.yaml by
    # `test_the_adopting_page_declares_the_toolchain_its_own_verify_runs`,
    # so it is the one thing this guard may normalise rather than re-derive.
    ('for pair in "go:go"; do', 'for pair in "python3:app"; do'),
    # The remediation hint. setup-go/setup-node is right for the enrollment
    # the render is rendered for and wrong for the one the page describes, so
    # the page names the pair a python3 consumer actually needs.
    ("actions/setup-go, actions/setup-node, actions/setup-java and "
     "gradle/actions/setup-gradle", "astral-sh/setup-uv and actions/setup-python"),
)


def _preflight_body(text: str, step_name: str) -> str:
    """The pre-flight step in `text`, from its `- name:` line to the end of
    its `run:` block — read as the BYTES a consumer would paste, never as
    parsed YAML, because what drifts here is the shell the step runs."""
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines)
                 if line.strip() == f"- name: {step_name}")
    end = next((i for i in range(start + 1, len(lines))
                if re.match(r"^ {0,6}[-#]", lines[i])), len(lines))
    return "".join(lines[start:end])


def _toolchain_step_for_go():
    """`render_toolchain_step` for the smallest enrollment that renders one."""
    from warden import enroll
    return enroll.TOOLCHAIN_STEP, enroll.render_toolchain_step(
        tuple(lang for lang in enroll.LANGUAGES if lang.name == "go"))


def test_the_adopting_pages_preflight_body_is_the_step_the_platform_renders():
    """Adopting.md section 4's pre-flight body, held to `render_toolchain_step`.

    It got worse rather than better when the page was last repaired. Before
    that the page's string and the rendered string were identical, so a drift
    was visible to anyone reading both; the repair adapted the remediation
    hint for a python3 consumer, so the two now differ DELIBERATELY in one
    sentence — and a deliberate divergence with no pin is the shape the
    template-header guard exists to refuse one file away.

    BYTE-EQUAL after exactly two declared substitutions, so the pin covers the
    whole body — the loop, the message, the `exit 2`, the indentation — and
    not a handful of needles someone can satisfy while the step stops working.
    """
    import difflib

    step_name, rendered = _toolchain_step_for_go()
    page = _preflight_body((WIKI / "Adopting.md").read_text(), step_name)
    for render_says, page_says in _ADOPTING_ADAPTATIONS:
        assert render_says in rendered, (
            f"`render_toolchain_step` no longer says {render_says!r}, so this "
            "guard would normalise away a difference that is no longer there "
            "and pass on a page that has drifted")
        assert page_says in page, (
            f"Adopting.md section 4 no longer says {page_says!r} — either the "
            "page dropped a deliberate adaptation, or this list has outlived "
            "it")
        rendered = rendered.replace(render_says, page_says)

    assert page == rendered, (
        "Adopting.md section 4's pre-flight step is no longer the step "
        "`warden init` renders, and the page claims it is — so the consumer "
        "copying it by hand gets a gate the platform stopped shipping:\n"
        + "".join(difflib.unified_diff(
            rendered.splitlines(keepends=True), page.splitlines(keepends=True),
            "render_toolchain_step", "docs/wiki/Adopting.md §4")))


def test_the_shipped_example_matches_the_render_after_its_two_adaptations():
    """The example's copy, held to the render byte-for-byte once the two
    adaptations the page declares are applied — and nothing else.

    `examples/hello-svc` is the second hand-written copy of the same step, and
    Adopting.md tells a reader what the example adapts: the pair list and the
    setup-action names, both of which follow from its own verify commands. It
    used to differ in one sentence more — "installs none of it" where the
    render says "installs no toolchain of this repository's own" — which was
    never an adaptation, because a consumer's repository is a consumer's
    repository either way. It was drift, it was pinned here as the one
    measured gap so it could not widen in silence, and the example has since
    been repaired to the render's wording. There is no third substitution
    left, which is why this asserts plain equality: any new difference is a
    new defect, not a known one.
    """
    import difflib

    step_name, rendered = _toolchain_step_for_go()
    example = _preflight_body(
        (ROOT / "examples" / "hello-svc" / ".github" / "workflows"
         / "ci.yml").read_text(), step_name)
    # the pair list is the example's own, DERIVED from its repo.yaml the way
    # test_the_shipped_example_gate_declares_the_toolchain_it_runs
    # derives it, so a verify command that starts invoking a new binary
    # reddens here too; only the ORDER is read from the example. The
    # setup-action names are the same adaptation the page makes, for the
    # same python3 reason.
    from test_init import _invoked_binaries

    from warden import config as config_mod

    config = config_mod.load(ROOT / "examples" / "hello-svc")
    derived = {f"{tool}:{scope}"
               for scope, steps in config.verify.items()
               for step in steps
               for tool in _invoked_binaries(step.run)}
    pair_line = re.search(r"for pair in ((?:\"[^\"]+\" ?)+); do", example)
    assert pair_line, "examples/hello-svc's pre-flight has no pair list"
    assert set(re.findall(r'"([^"]+)"', pair_line.group(1))) == derived, (
        pair_line.group(0), sorted(derived))
    rendered = rendered.replace(
        'for pair in "go:go"; do', pair_line.group(0))
    rendered = rendered.replace(*_ADOPTING_ADAPTATIONS[1])

    assert example == rendered, (
        "examples/hello-svc's pre-flight step differs from the one "
        "`warden init` renders by more than the two adaptations Adopting.md "
        "declares — the pair list and the setup-action names:\n"
        + "".join(difflib.unified_diff(
            rendered.splitlines(keepends=True),
            example.splitlines(keepends=True),
            "render_toolchain_step", "examples/hello-svc ci.yml")))


# --------------------------------------------------------------------------
# Gate-Pipeline.md § The base ancestry — the table, the caveat, and the
# docstring the table is derived from, each held to what git actually does.
#
# WHY THESE EXIST AND WHY THEY MEASURE. This section has now been edited
# twice into a NEW false claim: the edit that removed `ancestor`'s "this
# change and nothing else" rewrote the `not-ancestor` row into the opposite
# overclaim, and the remedy it added pointed at a gitignored directory. Both
# passed review because prose about git reads plausible. So every claim below
# is executed against a repository built in the test, and the page is held to
# the measurement rather than to a sentence someone typed.
# --------------------------------------------------------------------------

def _dgit(root, *args):
    """git in `root`, never in the checkout the suite runs in."""
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _dcommit(root, message):
    _dgit(root, "add", "-A")
    _dgit(root, "-c", "user.email=t@t", "-c", "user.name=t",
          "commit", "-q", "-m", message)
    return _dgit(root, "rev-parse", "HEAD")


def _diverged(tmp_path, merge_forward: bool):
    """The two shapes the section is about, returning `(root, fork, base, head)`.

    `main` forks at `fork`; the branch adds `mine.txt` and edits `f.txt`;
    `main` advances to `base`, which adds `theirs.txt`, DELETES `shared.txt`,
    edits `base.txt` (a file the branch never touches) and edits the same
    line of `f.txt`. With `merge_forward` the branch merges that advance —
    the ordinary answer to a moved `origin/main`, and still `ancestor`;
    without it the branch is simply behind, which is `not-ancestor`.

    All four shapes of base-only work are here on purpose. A fixture whose
    two sides only ever ADD files in disjoint paths can produce nothing but
    `A`/`D` in a two-dot range, so a guard over it reads "the base's work
    arrives as deletions" as true by construction — which is exactly the
    overclaim this section has now been edited into twice. The deletion, the
    addition, the base-only edit and the same-line edit are what make the
    range's real shape (inversion, and a fused hunk) observable at all.
    """
    root = tmp_path / ("merged" if merge_forward else "behind")
    root.mkdir()
    _dgit(root, "init", "-q", "-b", "main")
    (root / "base.txt").write_text("base\n")
    (root / "f.txt").write_text("FORK\n")
    (root / "shared.txt").write_text("shared\n")
    fork = _dcommit(root, "base")
    _dgit(root, "checkout", "-q", "-b", "work")
    (root / "mine.txt").write_text("mine\n")
    (root / "f.txt").write_text("MINE\n")
    head = _dcommit(root, "mine")
    _dgit(root, "checkout", "-q", "main")
    (root / "theirs.txt").write_text("theirs\n")
    (root / "f.txt").write_text("THEIRS\n")
    (root / "base.txt").write_text("base advanced\n")
    _dgit(root, "rm", "-q", "shared.txt")
    base = _dcommit(root, "theirs")
    _dgit(root, "checkout", "-q", "work")
    if merge_forward:
        # `-X ours` because the two sides edited one line: the merge is the
        # builder resolving in favour of the branch, which is a resolution
        # and not an evasion — the branch still reaches the base, so the
        # state under test is unchanged.
        _dgit(root, "-c", "user.email=t@t", "-c", "user.name=t",
              "merge", "-q", "--no-ff", "-X", "ours", "main",
              "-m", "merge main")
        head = _dgit(root, "rev-parse", "HEAD")
    return root, fork, base, head


def _merged_own_topic(tmp_path):
    """A branch that merged its base forward AND merged a topic of its own,
    returning `(root, fork, head)`.

    The shape the first-parent remedy is not exact on: `subwork` landed on
    this branch, and `--first-parent` does not walk to it.
    """
    root = tmp_path / "own-topic"
    root.mkdir()
    _dgit(root, "init", "-q", "-b", "main")
    (root / "base.txt").write_text("base\n")
    fork = _dcommit(root, "base")
    _dgit(root, "checkout", "-q", "-b", "work")
    (root / "m1.txt").write_text("1\n")
    _dcommit(root, "mine1")
    _dgit(root, "checkout", "-q", "main")
    (root / "t1.txt").write_text("1\n")
    _dcommit(root, "theirs1")
    _dgit(root, "checkout", "-q", "work")
    _dgit(root, "-c", "user.email=t@t", "-c", "user.name=t",
          "merge", "-q", "--no-ff", "main", "-m", "merge main")
    (root / "m2.txt").write_text("2\n")
    _dcommit(root, "mine2")
    _dgit(root, "checkout", "-q", "-b", "topic")
    (root / "s.txt").write_text("s\n")
    _dcommit(root, "subwork")
    _dgit(root, "checkout", "-q", "work")
    _dgit(root, "-c", "user.email=t@t", "-c", "user.name=t",
          "merge", "-q", "--no-ff", "topic", "-m", "merge topic")
    return root, fork, _dgit(root, "rev-parse", "HEAD")


def _ancestry_section() -> str:
    page = (WIKI / "Gate-Pipeline.md").read_text()
    start = page.index("### The base ancestry")
    return page[start:page.index("\n### ", start + 1)]


def _row_for(section: str, state: str) -> str:
    """The table row documenting `state`, or a failure naming the loss.

    Asked for by state name so a row that is DELETED is a red here rather
    than a silently satisfied `not in` on a string that is gone with it.
    """
    rows = [line for line in section.splitlines()
            if line.startswith(f"| `{state}` |")]
    assert len(rows) == 1, (
        f"Gate-Pipeline.md's base_ancestry table has {len(rows)} rows for "
        f"`{state}`, expected exactly one")
    return rows[0]


def _returned_states() -> set[str]:
    """Every state `base_ancestry` can return, read off the function itself.

    From the AST rather than from a list typed here: a fourth state, or a
    renamed one, changes this set without anyone remembering to, which is
    what makes the table guard fail on the drift instead of on a reviewer
    happening to notice it.
    """
    import ast

    src = (ROOT / "warden" / "attest.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "base_ancestry")
    states = {n.value.value for n in ast.walk(fn)
              if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
              and isinstance(n.value.value, str)}
    assert states, (
        "`base_ancestry` returns no string literal any more, so this reader "
        "is blind and the two guards below would pass on an empty set")
    return states


def test_the_ancestry_table_names_every_state_base_ancestry_can_return():
    """The documented states ARE the returnable ones, in both directions.

    The page carries the table because it is "where the contract is taught",
    and `base_ancestry`'s docstring is where the table's wording comes from.
    A fourth state added to the function, or a rename, has to reach both or
    this is red — a string-presence check on the three known names could not
    tell either case from a clean tree.

    The docstring's PROSE is held too, where it was once false: the round-1
    `not-ancestor` sentence said the range reports this change whole plus
    base-only commits as spurious deletions. Measured mutation: restoring
    that sentence left every state name in place and the suite green; the
    retired-phrase check reddens on it.
    """
    from warden import attest as attest_mod

    import json

    states = _returned_states()
    documented = set(re.findall(r"^\| `([a-z-]+)` \|", _ancestry_section(),
                                re.M))
    assert documented == states, (
        "Gate-Pipeline.md's base_ancestry table and the states the function "
        f"returns disagree: documented-only {sorted(documented - states)}, "
        f"returnable-only {sorted(states - documented)}")
    doc = attest_mod.base_ancestry.__doc__ or ""
    unsaid = sorted(s for s in states if "`" + s + "`" not in doc)
    assert not unsaid, (
        f"`base_ancestry`'s docstring does not name {unsaid} — and the "
        "docstring is where the page's table gets re-derived from, so a "
        "state missing here is a state missing from the next table")
    flat = " ".join(doc.split())
    for retired in ("reports this change whole", "spurious deletions"):
        assert retired not in flat, (
            f"`base_ancestry`'s docstring is back to {retired!r}: `base..head` "
            "against a base the branch never had comes back INVERTED, not "
            "whole plus deletions, and the page's table is re-derived from here")
    schema = json.loads((ROOT / "warden" / "schemas"
                         / "attestation.schema.json").read_text())
    enum = set(schema["properties"]["base_ancestry"]["enum"])
    assert enum == states, (
        "the shipped schema — the contract an enrolled consumer validates "
        "its shards against — and the states the function returns disagree: "
        f"schema-only {sorted(enum - states)}, returnable-only "
        f"{sorted(states - enum)}")


def test_the_ancestry_rows_hold_for_a_branch_that_merged_its_base_forward(
        tmp_path):
    """`ancestor` survives a merge-forward, and the range then carries the
    base branch's work too — so neither the row, the caveat, nor the line
    `attest show` prints may promise "this change and nothing else".

    The rendered line is in here because it is the one a reader of a
    committed shard actually sees: the page and the docstring can both be
    right while the renderer says the retired thing.
    """
    from warden import attest as attest_mod

    root, fork, base, head = _diverged(tmp_path, merge_forward=True)
    assert attest_mod.base_ancestry(root, fork, head) == "ancestor"
    assert attest_mod.base_ancestry(root, base, head) == "ancestor"
    ranged = _dgit(root, "diff", "--name-only", f"{fork}..{head}").split()
    assert "theirs.txt" in ranged, (
        "the merge-forward range no longer carries the base branch's own "
        f"file, so this fixture stopped reproducing the shape: {ranged}")
    # and the third dot is no escape here: the base IS the merge base
    three = _dgit(root, "diff", "--name-only", f"{fork}...{head}").split()
    assert three == ranged, (three, ranged)

    section = _ancestry_section()
    row = _row_for(section, "ancestor")
    assert "not a claim that the range is only this change" in row, (
        "the `ancestor` row promises a range of this change alone again, "
        f"which the merge-forward repo just refuted: {ranged}")
    caveat = section[section.index("**`ancestor` does not mean"):]
    caveat = caveat[:caveat.index("\n\n")]
    assert "alongside this change's" in caveat, (
        "the caveat no longer says an `ancestor` range can carry the base "
        f"branch's own work, which this range does: {ranged}")

    rendered = attest_mod.render_summary({
        "verdict": "clean", "head_sha": head, "base_sha": fork,
        "rules_version": "0" * 12, "reviewed_at": "2026-01-01T00:00:00+00:00",
        "reviewers": [], "findings": [], "base_ancestry": "ancestor"})
    base_line = next(ln for ln in rendered.splitlines() if "base:" in ln)
    assert "base..head is this change" not in base_line, (
        "`attest show` tells a reader of an `ancestor` shard that the range "
        "IS this change, which the merge-forward range just refuted "
        f"({ranged}) and which the docstring beside it disclaims:\n"
        + base_line)

    # The third surface, and the one an enrolled consumer reads instead of
    # either: the shipped schema's own description of the field.
    import json

    described = json.loads(
        (ROOT / "warden" / "schemas" / "attestation.schema.json").read_text()
    )["properties"]["base_ancestry"]["description"]
    assert "over this shard's own figures is the change this shard attests" \
        not in described, (
            "the shipped schema still defines `ancestor` as the range being "
            f"this change, refuted by the same merge-forward range {ranged}")
    assert "not a claim" in described or "never a claim" in described, (
        "the schema describes `ancestor` without the disclaimer the page "
        "and the docstring carry, and it is the copy a pinned consumer "
        "reads the field's meaning from")


def test_the_not_ancestor_row_states_what_the_two_dot_range_actually_prints(
        tmp_path):
    """`not-ancestor` is neither a meaningless range nor a range with one
    artefact to subtract. Measured over all four shapes of base-only work:
    the base's DELETION comes back as an addition, its addition as a
    deletion, its edit to a file this change never touched as an edit this
    change made, and the line both sides touched comes back with the BASE's
    content as what this change removed — so the row has to send a reader to
    the three-dot range rather than to a subtraction that does not exist.
    """
    from warden import attest as attest_mod

    root, fork, base, head = _diverged(tmp_path, merge_forward=False)
    assert attest_mod.base_ancestry(root, base, head) == "not-ancestor"
    status = dict(line.split("\t")[::-1] for line in
                  _dgit(root, "diff", "--name-status",
                        f"{base}..{head}").splitlines())
    assert status == {"base.txt": "M", "f.txt": "M", "mine.txt": "A",
                      "shared.txt": "A", "theirs.txt": "D"}, status
    # INVERSION, both directions: the base deleted shared.txt and added
    # theirs.txt, and the range reports the opposite of each.
    assert status["shared.txt"] == "A" and status["theirs.txt"] == "D", status
    # and the base's edit to a file this change never touched reads as an
    # edit this change made, reverting the base's line
    assert "-base advanced" in _dgit(root, "diff", f"{base}..{head}", "--",
                                     "base.txt")
    # FUSION: this change's own before-state is not in the two-dot hunk.
    fused = _dgit(root, "diff", f"{base}..{head}", "--", "f.txt")
    own = _dgit(root, "diff", f"{fork}..{head}", "--", "f.txt")
    assert "-THEIRS" in fused and "-FORK" not in fused, fused
    assert "-FORK" in own, own
    # And the three dots are the reading that is not lossy.
    three = dict(line.split("\t")[::-1] for line in
                 _dgit(root, "diff", "--name-status",
                       f"{base}...{head}").splitlines())
    assert three == {"f.txt": "M", "mine.txt": "A"}, three

    row = _row_for(_ancestry_section(), "not-ancestor")
    assert "INVERTED" in row, (
        "the `not-ancestor` row no longer says the base-only work comes "
        f"back inverted, which the measured range does: {status}")
    assert "base...head" in row, (
        "the `not-ancestor` row no longer sends the reader to the three-dot "
        f"range, which is the only one that printed this change: {three}")
    assert "whole and correct" not in row and "ONE artefact" not in row, (
        "the `not-ancestor` row is back to promising a range that reports "
        "this change whole with one artefact to subtract — refuted by the "
        f"fused hunk, which reports no before-state of this change:\n{fused}")


def test_the_schema_not_ancestor_sentence_sends_the_reader_to_the_three_dot_range():
    """The `ancestor` half of the shipped description is pinned above; the
    `not-ancestor` half was not, and it was rewritten twice into a fresh false
    claim — "reading more than this change", the additive reading the measured
    range refutes — while the row test reached only the wiki. This holds the
    copy a pinned consumer reads to the same reading the row is held to."""
    import json

    described = json.loads(
        (ROOT / "warden" / "schemas" / "attestation.schema.json").read_text()
    )["properties"]["base_ancestry"]["description"]
    half = described[described.index("'not-ancestor':"):
                     described.index("'unavailable':")]
    for retired in ("reading more than this change",
                    "reports more than this change"):
        assert retired not in half, (
            "the schema's `not-ancestor` sentence is back to the additive "
            f"reading ({retired!r}), refuted by the fused hunk the row test "
            "measures")
    assert "INVERTED" in half, (
        "the schema's `not-ancestor` sentence no longer says the base's own "
        "advance comes back inverted, which the measured range does")
    assert "base...head" in half, (
        "the schema's `not-ancestor` sentence no longer sends the reader to "
        "the three-dot range, the only one that printed this change")


def test_the_unavailable_row_covers_a_base_this_clone_cannot_resolve(
        tmp_path):
    """`unavailable` is not only "no repository to ask". Any
    `--is-ancestor` exit other than 0 or 1 lands there, and an unresolvable
    base sha exits 128 inside a perfectly good repository."""
    from warden import attest as attest_mod

    root, _fork, _base, head = _diverged(tmp_path, merge_forward=False)
    probe = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "d" * 40, head],
        cwd=root, capture_output=True, text=True)
    assert probe.returncode == 128, probe
    assert attest_mod.base_ancestry(root, "d" * 40, head) == "unavailable"

    row = _row_for(_ancestry_section(), "unavailable")
    assert "cannot resolve" in row, (
        "the `unavailable` row is back to a missing repository alone, and "
        "a base sha this clone cannot resolve reached it at exit "
        f"{probe.returncode} in a repository that answered every other "
        "question")


def test_the_ancestry_remedy_is_an_invocation_the_shard_can_actually_drive(
        tmp_path):
    """The caveat's closing remedy, executed.

    Three things have to hold together: the invocation the page prints
    separates this change from the merged-forward side, the fields it names
    are fields a shard carries, and `range_patch_ids` — the one thing the
    page says cannot do it — indeed cannot.

    The first-parent caveat is held in both directions: the page still
    says the reading is NOT every commit this branch wrote, and does not
    promise it returns the branch's (or this branch's) own commits. Measured
    mutations: the closing sentence rewritten to "Read it as the
    first-parent line: it returns the branch's own commits." (the F3
    overclaim), to "...: it returns this branch's own commits.", and to
    "Read it as every commit the branch wrote." each kept the FIRST-PARENT
    spelling and passed the presence check alone; each reddens here.
    """
    import json

    from warden import attest as attest_mod

    section = _ancestry_section()
    printed = re.search(r"`git log ([^`]*?)<base_sha>\.\.<head_sha>`", section)
    assert printed, (
        "the ancestry caveat prints no `git log ... <base_sha>..<head_sha>` "
        "invocation any more — a reader of a moved-base shard is back to "
        "being told the reading is impossible")
    flags = printed.group(1).split()

    root, fork, _base, head = _diverged(tmp_path, merge_forward=True)
    own = _dgit(root, "log", *flags, "--format=%s", f"{fork}..{head}").split()
    assert own == ["mine"], (
        f"the invocation the page prints, {' '.join(['git', 'log', *flags])}, "
        f"does not exclude the merged-forward side: {own}")

    # the spelling `range_patch_ids` is built from, which cannot separate
    both = _dgit(root, "log", "--no-merges", "--format=%s",
                 f"{fork}..{head}").split()
    assert sorted(both) == ["mine", "theirs"], both

    # WHAT THE INVOCATION IS NOT: the branch's own commits. It walks the
    # first-parent line, so a commit that reached this branch through a
    # merge of the branch's own topic branch is not in the output — and a
    # page that promised "the branch's own commits" would be promising a
    # list git does not produce.
    topic_root, topic_fork, topic_head = _merged_own_topic(tmp_path)
    walked = _dgit(topic_root, "log", *flags, "--format=%s",
                   f"{topic_fork}..{topic_head}").split()
    listed = _dgit(topic_root, "log", "--no-merges", "--format=%s",
                   f"{topic_fork}..{topic_head}").split()
    assert "subwork" in listed and "subwork" not in walked, (walked, listed)
    assert "FIRST-PARENT" in section or "first-parent line" in section, (
        "the caveat prints the first-parent invocation without saying it "
        "walks the first-parent LINE, and a commit this branch carries "
        f"({sorted(set(listed) - set(walked))}) is missing from what it "
        "returns")
    flat = " ".join(section.split())
    assert ("not as every commit" in flat
            and not re.search(r"returns (?:the|this) branch's own commits",
                              flat)), (
        "the caveat no longer says the first-parent invocation is NOT every "
        "commit this branch wrote, or promises it returns the branch's own "
        f"commits, and measured it drops {sorted(set(listed) - set(walked))}, "
        "which this branch carries")
    assert "--no-merges" in inspect.getsource(attest_mod.range_patch_ids), (
        "`range_patch_ids` no longer walks `--no-merges base..head`, so the "
        "page's reason it cannot separate merged-in work is stale")

    schema = json.loads((ROOT / "warden" / "schemas"
                         / "attestation.schema.json").read_text())
    assert {"base_sha", "head_sha"} <= set(schema["properties"]), (
        "the shard no longer carries the two fields the printed invocation "
        "is driven from")


def test_the_ancestry_caveat_sends_no_reader_to_the_gitignored_package():
    """A round's package is not in any clone, so it cannot be where a later
    reader's reading comes from — and the section may mention it only while
    saying so."""
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".warden/out/rounds"],
        cwd=ROOT).returncode
    assert ignored == 0, (
        "`.warden/out/rounds` is tracked now — this guard's premise, and the "
        "section's, both need re-deriving")
    for sentence in re.split(r"(?<=\.)\s+", " ".join(
            _ancestry_section().split())):
        if "package" in sentence:
            assert "gitignored" in sentence, (
                "the ancestry section points a reader at a round's package "
                "without saying it is gitignored, and `git check-ignore` "
                f"just confirmed no clone has one:\n  {sentence}")


# --------------------------------------------------------------------------
# The enumerations the wiki calls COMPLETE are held to the
# code they enumerate.
#
# `CLI-Reference.md`'s `round count` row says it prints "the whole reading"
# and lists the keys; the `round new` row says "every reading behind the
# number prints on stderr and none is resolved away" and lists them; the
# `attest write/show` row and Gate-Pipeline.md teach which readings ride the
# run manifest. Each of those is a complete-by-claim list, and each fell
# behind PR #291 without any test going red: the
# report gained `uncounted`, the manifest gained `base_binding` and its
# detail, and the base a shard stamps stopped being `--base`. The prose is
# unpinned by design (wiki-fidelity), so the pin here is the SHAPE: every key
# the code emits is named, and a key added to the code without a sentence in
# the wiki is a red here rather than a reviewer happening to notice.
# --------------------------------------------------------------------------

def _cli_function(name: str) -> ast.FunctionDef:
    src = (ROOT / "warden" / "cli.py").read_text()
    return next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _dict_literal_keys(node: ast.Dict) -> set[str]:
    """Constant string keys of a dict literal, INCLUDING those behind a
    `**({...} if cond else {})` splat — the closure-only manifest key rides
    in exactly that shape."""
    out: set[str] = set()
    for key, value in zip(node.keys, node.values):
        if key is None:
            for inner in ast.walk(value):
                if isinstance(inner, ast.Dict):
                    out |= _dict_literal_keys(inner)
        elif isinstance(key, ast.Constant) and isinstance(key.value, str):
            out.add(key.value)
    return out


def _round_count_report_keys() -> set[str]:
    """Every key `warden round count` prints, read off the `report = {...}`
    literal in `_cmd_round_count` rather than typed here."""
    fn = _cli_function("_cmd_round_count")
    reports = [n.value for n in ast.walk(fn)
               if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
               and any(isinstance(t, ast.Name) and t.id == "report"
                       for t in n.targets)]
    assert len(reports) == 1, (
        f"`_cmd_round_count` builds {len(reports)} `report` dict literals; "
        "this reader expects exactly one")
    keys = _dict_literal_keys(reports[0])
    assert keys, "the `report` literal has no constant keys — reader is blind"
    return keys


def _round_count_note_fields() -> set[str]:
    """The `RoundCount` fields `notes` turns into stderr lines, read off the
    `if self.<field>` tests in the property body."""
    src = (ROOT / "warden" / "repair.py").read_text()
    cls = next(n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.ClassDef) and n.name == "RoundCount")
    prop = next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == "notes")
    fields = {a.attr for stmt in ast.walk(prop) if isinstance(stmt, ast.If)
              for a in ast.walk(stmt.test)
              if isinstance(a, ast.Attribute)
              and isinstance(a.value, ast.Name) and a.value.id == "self"}
    assert fields, "`RoundCount.notes` guards no `self.<field>` — reader is blind"
    return fields


def _attest_manifest_extra_keys() -> set[str]:
    """Every reading `attest write` adds to the run manifest, read off the
    `extra={...}` literal of its one `write_manifest` call."""
    fn = _cli_function("_cmd_attest")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "write_manifest"]
    assert len(calls) == 1, (
        f"`_cmd_attest` calls `write_manifest` {len(calls)} times; this "
        "reader expects exactly one")
    extra = next(kw.value for kw in calls[0].keywords if kw.arg == "extra")
    assert isinstance(extra, ast.Dict), "`extra=` is no longer a dict literal"
    keys = _dict_literal_keys(extra)
    assert keys, "the manifest `extra` literal has no constant keys"
    return keys


def _base_binding_states() -> set[str]:
    """Every state `base_binding` can return, read off its `return` tuples
    and resolved through the module's `BASE_*` constants, and held equal to
    that constant set so an orphan constant or an unnamed return both show."""
    from warden import attest as attest_mod

    constants = {name: value for name, value in vars(attest_mod).items()
                 if name.startswith("BASE_") and isinstance(value, str)}
    assert constants, "attest.py declares no `BASE_*` string constants"
    src = (ROOT / "warden" / "attest.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "base_binding")
    returned: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
            state = node.value.elts[1]
            assert isinstance(state, ast.Name) and state.id in constants, (
                "`base_binding` returns a state that is not one of the "
                f"module's `BASE_*` constants: {ast.dump(state)}")
            returned.add(constants[state.id])
    assert returned == set(constants.values()), (
        "the `BASE_*` constants and what `base_binding` returns disagree: "
        f"constant-only {sorted(set(constants.values()) - returned)}, "
        f"returned-only {sorted(returned - set(constants.values()))}")
    return returned


def _cli_reference_row(command: str) -> str:
    rows = [line for line in (WIKI / "CLI-Reference.md").read_text().splitlines()
            if line.startswith(f"| `{command}` |")]
    assert len(rows) == 1, (
        f"CLI-Reference.md has {len(rows)} rows for `{command}`, expected one")
    return rows[0]


def _base_binding_section() -> str:
    page = (WIKI / "Gate-Pipeline.md").read_text()
    marker = "### The base binding"
    assert marker in page, (
        "Gate-Pipeline.md has no `### The base binding` section — the page "
        "that teaches the attestation's markers teaches nothing about which "
        "base a shard stamps, or about the run manifest's readings")
    start = page.index(marker)
    return page[start:page.index("\n### ", start + 1)]


def test_the_round_count_row_names_every_key_its_json_carries():
    """The row lists what `round count` prints as "the whole reading". Held
    to the report literal: a key the JSON carries that the row does not name
    in backticks is a red, whatever the key is called this month."""
    row = _cli_reference_row("round count")
    missing = sorted(k for k in _round_count_report_keys()
                     if f"`{k}`" not in row)
    assert not missing, (
        "CLI-Reference.md's `round count` row claims to list the whole JSON "
        f"reading and does not name {missing}")


def test_every_stderr_reading_has_a_json_key_and_both_rows_name_it():
    """Each `RoundCount.notes` line is a fact about the evidence, and the
    `round new` row says every one prints and none is resolved away. Two
    guards: each note field is ALSO a key of the `round count` JSON (a
    reading that reaches stderr but not the report would be one an unattended
    run cannot audit for), and each is named in both rows."""
    fields = _round_count_note_fields()
    report = _round_count_report_keys()
    unreported = sorted(fields - report)
    assert not unreported, (
        f"`RoundCount.notes` prints {unreported} on stderr and `round count` "
        "does not carry them in its JSON — the cage has no terminal")
    for command in ("round new", "round count"):
        row = _cli_reference_row(command)
        missing = sorted(f for f in fields if f"`{f}`" not in row)
        assert not missing, (
            f"CLI-Reference.md's `{command}` row says every reading behind "
            f"the number prints and none is resolved away, and names none of "
            f"{missing}")


def test_the_base_binding_table_names_every_state_the_function_returns():
    """Gate-Pipeline.md carries the `base_binding` table on the base
    ancestry table's precedent, and the documented states ARE the returnable
    ones in both directions."""
    section = _base_binding_section()
    documented = set(re.findall(r"^\| `([a-z-]+)` \|", section, re.M))
    states = _base_binding_states()
    assert documented == states, (
        "Gate-Pipeline.md's base_binding table and the states the function "
        f"returns disagree: documented-only {sorted(documented - states)}, "
        f"returnable-only {sorted(states - documented)}")
    assert "`base_binding_detail`" in section, (
        "the section names the state and not the detail that rides beside it")


def test_the_manifest_readings_are_each_named_where_the_page_teaches_them():
    """Every reading `attest write` adds to its run manifest is named in
    Gate-Pipeline.md's base-binding section (the one place the manifest's
    readings are listed together) and in the CLI-Reference `attest write/show`
    row. A reading recorded for an auditor and taught nowhere is one the
    auditor does not know to look for."""
    keys = _attest_manifest_extra_keys()
    section = _base_binding_section()
    row = _cli_reference_row("attest write/show")
    for name, text in (("Gate-Pipeline.md's base-binding section", section),
                       ("CLI-Reference.md's `attest write/show` row", row)):
        missing = sorted(k for k in keys if f"`{k}`" not in text)
        assert not missing, f"{name} does not name {missing}"


def _first_round_clause() -> str:
    section = _base_binding_section()
    start = section.index("`first-round`")
    return " ".join(section[start:section.index("`nothing-carried`",
                                                start)].split())


def test_the_first_round_clause_names_who_numbered_the_round(
        tmp_path: Path):
    """The manifest paragraph said `first-round` is "a round warden numbers
    1". The number is the HIGHER of the minted round and the roster's highest
    label, so in a directory warden did not mint it is the roster's label —
    and `verify_chain` also answers `first-round` with no number at all,
    which `attest write` never records because it refuses an unlabelled
    roster under `--review-dir`. Each of those is driven here through the
    real functions, and the page is held to the clause that states them."""
    from warden import attest

    def chain(reviewers: list, review_dir: Path) -> str:
        return attest.verify_chain([], reviewers, root=ROOT, base_sha="",
                                   head_sha="", review_dir=review_dir)

    labelled = [{"role": "code-reviewer", "agent": "a", "round": 1}]
    unlabelled = [{"role": "code-reviewer", "agent": "a", "returned": True}]
    unminted = tmp_path / "unminted" / "reviewers"
    unminted.mkdir(parents=True)
    minted = tmp_path / "minted" / "reviewers"
    minted.mkdir(parents=True)
    (minted.parent / "round.json").write_text(
        json.dumps({"round": 2, "head_sha": "0" * 40}))

    # Unminted: the roster's own label is the number.
    assert attest._chain_round(labelled, unminted) == 1
    assert chain(labelled, unminted) == "first-round"
    # Minted: the higher of the two wins over the roster's label.
    assert attest._chain_round(labelled, minted) == 2
    # Neither gives a number: still `first-round`, library-only.
    assert attest._chain_round(unlabelled, unminted) is None
    assert chain(unlabelled, unminted) == "first-round"
    # ...because the CLI's `--review-dir` path refuses that roster first.
    with pytest.raises(attest.AttestError, match="states no `round`"):
        attest.verify_attribution(unlabelled, [], required=True)

    clause = _first_round_clause()
    assert "warden numbers 1" not in clause, (
        "Gate-Pipeline.md still says warden numbers the `first-round` round, "
        f"when an unminted directory's number is the roster's: {clause!r}")
    for needle in ("the higher of the round", "the roster's own label",
                   "only a library caller reaches",
                   "refuses a roster entry that states no `round`"):
        assert needle in clause, (
            f"Gate-Pipeline.md's `first-round` clause does not say "
            f"{needle!r}: {clause!r}")


def test_the_attest_row_says_the_base_comes_from_the_round():
    """The user-visible change of PR #291: with a round bound to this head,
    `--base` is no longer what gets stamped — the round's recorded base is.
    A row that still teaches `--base` as the stamped base teaches the defect."""
    row = _cli_reference_row("attest write/show")
    assert "`round.json`" in row and "`base_sha`" in row, (
        "the `attest write/show` row never says the stamped base is taken "
        "from the round's `round.json` `base_sha`")
    assert "`round-base-differs`" in row, (
        "the row does not name the state a reader meets when `--base` and "
        "the round's base disagree")


def test_hello_svc_carries_the_classify_enforce_step():
    """The shipped example is the gate a consumer copies, so it carries the
    step that binds a light round: ci.yml's `run:`, byte for byte, with the
    shim path swapped for the example's in-place call, after the review and
    before the attestation check, as in ci.yml."""
    def steps(path):
        doc = yaml.safe_load(path.read_text())
        return [s for job in doc["jobs"].values() for s in job.get("steps") or []]

    name = "proportionate review tier"
    live = [s for s in steps(ROOT / ".github" / "workflows" / "ci.yml")
            if s.get("name") == name]
    example = steps(ROOT / "examples" / "hello-svc" / ".github" / "workflows"
                    / "ci.yml")
    names = [s.get("name") for s in example]
    assert len(live) == 1 and names.count(name) == 1, names
    assert names.index("the AI rules gate (HIGH findings exit 1)") \
        < names.index(name) < names.index("pre-PR review attestation"), names
    assert example[names.index(name)]["run"] == live[0]["run"].replace(
        ".warden/bin/warden", "uv run --locked --project ../.. warden")


def _github_only_section() -> str:
    page = (WIKI / "Adopting.md").read_text()
    start = page.index("## Another CI: the GitHub-only limit")
    return page[start:page.index("\n## ", start + 1)]


def _baseline_checks_reading_github_workflows() -> list[dict]:
    """Every certification baseline check that reads CI, derived from the
    ladder rather than listed: a path under `.github/workflows/`, or a type
    that parses the workflows (`ci_step_enforced`, and `light_round_enforced`,
    which runs it)."""
    ladder = yaml.safe_load(
        (ROOT / "warden" / "certification" / "baseline.yaml").read_text())
    return [c for lv in ladder["levels"].values() for c in lv["checks"]
            if str(c.get("path", "")).startswith(".github/workflows/")
            or c["type"] in ("ci_step_enforced", "light_round_enforced")]


def test_the_github_only_limit_names_every_check_that_reads_github_workflows():
    """Adopting.md's GitHub-only section is what a consumer on another CI
    copies from, so it must name every check that cannot see that CI. The set
    is derived from the baseline ladder, and only by the two shapes it uses
    today: a `path:` under `.github/workflows/`, or one of the two check types
    that parse the workflows. A new check of either shape turns this red until
    the page names it; a new check TYPE that reads the workflows does not, and
    D-03 and D-05 are named here by hand."""
    section = _github_only_section()
    ids = [c["id"] for c in _baseline_checks_reading_github_workflows()]
    assert set(ids) >= {"G-04", "V-02", "E-01", "E-03", "R-14"}, ids
    missing = [i for i in ids + ["D-03", "D-05"] if i not in section]
    assert not missing, f"the GitHub-only section does not name {missing}"
    install = " ".join((WIKI / "Installation.md").read_text().split())
    assert "*Another CI: the GitHub-only limit*" in install, (
        "Installation.md does not point at the GitHub-only section")


def test_the_github_only_limit_holds_a_gitlab_gate_is_invisible_to_certify(
        tmp_path):
    """The section's claim, driven against the code: a `.gitlab-ci.yml` that
    runs every gate command satisfies none of the CI-reading checks, so the
    page's LEVEL 0 is what certify says. Certify reading GitLab CI turns this
    red and the page must change with it. A GitLab renderer in `warden init`
    would not: nothing here runs init."""
    from warden import certify as certify_mod
    (tmp_path / ".gitlab-ci.yml").write_text(
        "warden:\n  script:\n"
        "    - warden verify --scope app\n"
        "    - warden review --base origin/main\n"
        "    - warden attest classify --enforce --base origin/main\n"
        "    - warden attest check --base origin/main\n"
        "    - warden memory ingest\n")
    for check in _baseline_checks_reading_github_workflows():
        if check["type"] == "light_round_enforced":
            check = {**check, "type": "ci_step_enforced"}
        ok, detail = certify_mod._run_check(check, tmp_path)
        assert not ok, f"{check['id']} passed on a GitLab-only gate: {detail}"


# ── CONTRIBUTING's map of scripts/ ──────────────────────────────────────────

def _scripts_rows(text: str) -> dict[str, list[str]]:
    """The rows of CONTRIBUTING.md's `## The scripts` table, keyed by the
    entry the first cell names (a directory keeps its trailing `/`), each
    with its cells. A missing section yields no rows."""
    start = text.find("\n## The scripts\n")
    if start < 0:
        return {}
    end = text.find("\n## ", start + 1)
    section = text[start:end if end >= 0 else len(text)]
    rows = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows[cells[0].strip("`")] = cells
    return rows


def _scripts_entries() -> set[str]:
    """Every entry git tracks directly under scripts/: a file by its name, a
    directory (scripts/mermaid) once, as `name/`."""
    entries = set()
    for p in tracked("scripts/"):
        parts = p.relative_to(ROOT / "scripts").parts
        entries.add(parts[0] + ("/" if len(parts) > 1 else ""))
    return entries


def test_contributing_maps_every_entry_under_scripts():
    """A new script cannot land unlisted, and a deleted one cannot leave its
    row behind: the table names exactly what git tracks under scripts/, and
    every row fills its four columns. The README's sentence on the repository
    gating itself links the table by its heading."""
    rows = _scripts_rows((ROOT / "CONTRIBUTING.md").read_text())
    entries = _scripts_entries()
    assert "mermaid/" in entries and "commit-lint.sh" in entries, entries
    assert set(rows) - entries == set(), (
        f"CONTRIBUTING.md's scripts table names {sorted(set(rows) - entries)}, "
        "which scripts/ does not hold")
    assert entries - set(rows) == set(), (
        f"scripts/ holds {sorted(entries - set(rows))} with no row in "
        "CONTRIBUTING.md's `## The scripts` table")
    short = sorted(n for n, c in rows.items() if len(c) != 4 or not all(c))
    assert not short, f"rows without four filled cells: {short}"
    assert "(CONTRIBUTING.md#the-scripts)" in (ROOT / "README.md").read_text()


def test_the_scripts_map_refuses_a_missing_row():
    """The check above reads rows, not prose: a table with one row cut out no
    longer covers scripts/, and a document without the section has no rows."""
    text = (ROOT / "CONTRIBUTING.md").read_text()
    cut = "\n".join(line for line in text.splitlines()
                    if not line.startswith("| `readme-try-it.sh`"))
    assert "readme-try-it.sh" in _scripts_rows(text)
    assert _scripts_entries() - set(_scripts_rows(cut)) == {"readme-try-it.sh"}
    assert _scripts_rows(text.replace("## The scripts", "## Scripts")) == {}


# A workflow file followed by the job ids the row names in it:
# "`ci.yml` `gate`, `corpus`, `enrollment` and `tracker`".
_ROW_JOBS = re.compile(r"`([\w-]+\.yml)` (`[\w-]+`(?:(?:, | and )`[\w-]+`)*)")
# A file that calls the script: a hook, a test, or another script.
_ROW_FILE_CALLER = re.compile(
    r"`((?:\.githooks|tests)/[\w.-]+|[\w-]+\.(?:sh|py))`")


def _code_only(rel: str, text: str) -> str:
    """TEXT with what does not run left out: for Python, comments and
    docstrings (by way of the AST); otherwise, every line that is a comment."""
    if rel.endswith(".py"):
        tree = ast.parse(text)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (isinstance(body, list) and body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0] = ast.Pass()
        return ast.unparse(tree)
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def _stale_script_callers(rows: dict[str, list[str]],
                          read) -> tuple[list[str], int]:
    """Every caller a row's `Who runs it` names that does not run the script,
    and how many callers were checked. A named job must exist in the named
    workflow and carry the script's path in a step's `run`, outside a
    comment; a named hook, test or script must carry its name outside a
    comment or docstring. READ maps a repo-relative path to its text."""
    stale, checked = [], 0
    for name, cells in rows.items():
        script = name.rstrip("/")
        who = cells[2]
        for workflow, jobs in _ROW_JOBS.findall(who):
            doc = yaml.safe_load(read(f".github/workflows/{workflow}"))
            for job in re.findall(r"`([\w-]+)`", jobs):
                checked += 1
                steps = (doc.get("jobs") or {}).get(job, {}).get("steps", [])
                runs = "\n".join(_code_only("run", s.get("run", ""))
                                 for s in steps)
                if f"scripts/{script}" not in runs:
                    stale.append(f"{name} -> {workflow} {job}")
        for caller in _ROW_FILE_CALLER.findall(who):
            rel = caller if "/" in caller else f"scripts/{caller}"
            checked += 1
            if script not in _code_only(rel, read(rel)):
                stale.append(f"{name} -> {rel}")
    return stale, checked


def _read_repo(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_each_scripts_row_names_a_caller_that_runs_it():
    """Where a row's `Who runs it` names a workflow job, a hook, a test or
    another script, that caller runs the script: the job's steps name its
    path in a `run`, and the file names it outside a comment. A path left
    only in a comment or an `on.push.paths` filter does not count, so the
    column cannot go on naming a job that stopped running it."""
    rows = _scripts_rows((ROOT / "CONTRIBUTING.md").read_text())
    stale, checked = _stale_script_callers(rows, _read_repo)
    assert not stale, f"rows naming a caller that does not run them: {stale}"
    # Exact, so a reader that stops seeing a caller is red: a row that adds
    # or drops a caller changes this number with it.
    assert checked == 26, f"{checked} callers were checked, not 26"


def _replacing(rel: str, old: str, new: str):
    """A reader of the repository in which REL's one OLD reads NEW."""
    def read(path: str) -> str:
        text = _read_repo(path)
        if path != rel:
            return text
        assert text.count(old) == 1, f"{rel} no longer holds {old!r} once"
        return text.replace(old, new)
    return read


@pytest.mark.parametrize("rel, old, new, row", [
    # the step stops running it; the path survives in a comment above the job
    (".github/workflows/ci.yml", "run: bash scripts/readme-try-it.sh",
     "run: echo none", "readme-try-it.sh"),
    # the step stops running it; the path survives in the on.push.paths filter
    (".github/workflows/wiki-sync.yml", "run: bash scripts/build-wiki.sh",
     "run: echo none", "build-wiki.sh"),
    (".githooks/commit-msg", "/scripts/commit-lint.sh\"", "/scripts/x.sh\"",
     "commit-lint.sh"),
    # the call is gone; the name survives in the header comments
    ("scripts/init-proof.sh", '/readme-try-it.sh" "$readme"',
     '/x.sh" "$readme"', "readme-try-it.sh"),
    # the load is gone; the name survives in the module docstring
    ("scripts/publish-sync.py", '/ "publish-public.py")', '/ "x.py")',
     "publish-public.py"),
])
def test_the_caller_check_refuses_a_caller_that_stopped_running_it(
        rel, old, new, row):
    rows = _scripts_rows((ROOT / "CONTRIBUTING.md").read_text())
    assert not _stale_script_callers(rows, _read_repo)[0]
    stale, _ = _stale_script_callers(rows, _replacing(rel, old, new))
    assert any(s.startswith(f"{row} -> ") for s in stale), stale


def test_the_caller_check_reads_the_job_id_the_row_names():
    """A row moved to a job that does not run the script, or to one that
    does not exist, is refused."""
    rows = _scripts_rows((ROOT / "CONTRIBUTING.md").read_text())
    for job in ("gate", "no-such-job"):
        moved = dict(rows)
        moved["portability-sim.sh"] = [
            c.replace("`ci.yml` `enrollment`", f"`ci.yml` `{job}`")
            for c in rows["portability-sim.sh"]]
        assert moved["portability-sim.sh"] != rows["portability-sim.sh"]
        stale, _ = _stale_script_callers(moved, _read_repo)
        assert stale == [f"portability-sim.sh -> ci.yml {job}"], stale
