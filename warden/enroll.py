"""`warden init`: enroll an existing repository in one command.

Detection is by file presence in the working directory, never the network.
Every manifest found enrolls its language: `pyproject.toml` is python,
`package.json` is node, `go.mod` is go. Each detected language gets its own
component, verify scope and CI verify step, and the starter rules that read
its source.

The verify commands pass on a repository with no tests and leave the tree as
they found it: python runs pytest against `uv.lock` when there is one and in a
throwaway environment when there is not, writes no bytecode, and takes pytest's
exit 5 (nothing collected) as a pass; node installs without writing a lockfile
and runs `npm test` only for a test script that is not npm init's placeholder;
go runs `go vet` and `go test`, which already pass with no test files. What a
build leaves behind is ignored instead: `*.egg-info/` for python, which a
setuptools project writes on every run, and `node_modules/` for node. Init
warns, and still enrolls, where it finds no test.

The generated gate DECLARES the toolchain those commands invoke and installs
no toolchain of the enrolled repository's own. Where there is
one to declare, its verify job carries a pre-flight step that names every
binary a verify scope needs and is missing, and exits 2 — the gate DID NOT
RUN — before the first scope executes, so an enrolled Go or Node repository
whose runner image no longer ships the toolchain gets a statement of cause
and a pointer to the setup action, not a bare exit 127 inside a step named
for a test. The one binary the gate installs for itself is `uv`, the
platform's own runtime (`GATE_INSTALLS`), and it is subtracted from what the
pre-flight requires, so no rendered pair can be one the gate already
satisfied — which is why a PYTHON-ONLY enrollment carries no pre-flight step
at all rather than one that cannot fail. `render_toolchain_step` has the
reasoning for declare over install.

A consumer repository must be private: the generated gate uploads the private
platform wheel as a one-day artifact any reader of the run can download. Init
cannot tell a public repository without the network, so the gate checks
instead: the install job reads `github.event.repository.private` from the
event and exits 2 before it touches the credential or builds the wheel when
the repository is not private.

Init writes only files that do not exist. If any of them is already there it
writes nothing, names every one, and exits 1. `.gitignore` is the one file it
may extend: missing entries are appended and existing lines are never
rewritten.

Init enrolls the working directory, which must be inside a git repository. At
the git root the gate workflow is `.github/workflows/warden.yml`. Below it, at
`svc/api`, every other file is written in `svc/api` and the gate is the git
root's `.github/workflows/warden-svc-api.yml`, because GitHub runs workflows
only from there, and its top-level `name:` is `warden (svc/api)`, which is how
certify tells it from the gate of an `svc-api` enrollment with the same file
name. Each of its jobs sets `defaults.run.working-directory` to `svc/api`, so
every `run:` step runs in the enrollment but two install steps that run at the
workspace root, and the action inputs that name a path carry the prefix. When
the gate file already exists and names another enrollment, init's refusal
names that enrollment.

The install job checks out `repo.yaml` alone and, before any step that runs in
the enrollment, exits 2 saying the gate DID NOT RUN when the commit has no
`repo.yaml` at the enrollment's path.
"""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import __version__
from . import catalog as catalog_mod
from . import packpin as packpin_mod
from . import config as config_mod
from . import gate_workflows
from . import mechanical as mechanical_mod
from . import rules as rules_mod
from .certify import PLATFORM_REPAIR_CAP
from . import yamlio

TEMPLATES = Path(__file__).resolve().parent / "templates" / "init"
WORKFLOW_DIR = ".github/workflows"
WORKFLOW_PATH = f"{WORKFLOW_DIR}/warden.yml"
POLICY_PATH = ".warden/skills-policy.md"
RULES_DIR = config_mod.DEFAULT_RULES_DIR
GITIGNORE_ENTRIES = (".warden/out/", ".warden/memory/findings.jsonl",
                     ".warden/memory/gate/")
BUILD_OUTPUT_IGNORES = {"python": ("*.egg-info/",), "node": ("node_modules/",)}
DEPLOY_KEY_SECRET = "AGENTOPS_DEPLOY_KEY"
GATE_CHECK = "warden gate"


class EnrollError(Exception):
    pass


@dataclass(frozen=True)
class Language:
    name: str
    manifest: str
    globs: tuple[str, ...]
    #: The binaries this language's verify commands invoke, which the
    #: generated gate requires on PATH before it runs them.
    #: A positional field on purpose: a language added without one does not
    #: import, so no verify command can reach a consumer's runner with its
    #: toolchain undeclared.
    tools: tuple[str, ...]


LANGUAGES = (
    Language("python", "pyproject.toml", ("**/*.py",), ("uv",)),
    Language("node", "package.json",
             ("**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs", "**/*.ts", "**/*.tsx"),
             ("npm",)),
    Language("go", "go.mod", ("**/*.go",), ("go",)),
)
ALL_LANGUAGES = tuple(lang.name for lang in LANGUAGES)


@dataclass(frozen=True)
class Starter:
    rule_id: str
    severity: str
    languages: tuple[str, ...]


# Catalog entries init writes. An entry qualifies only when its applies_when is
# unconditional, so file presence is enough to know it applies, and the
# platform enforces it with no code of the repo's own: a declarative starter
# whose patterns read the language, or a core checker. Conditional entries are
# what `warden rules recommend` measures against the tree afterwards.
STARTERS = {
    "hardcoded-credentials": Starter("secrets-in-diff", "HIGH", ALL_LANGUAGES),
    "swallowed-exceptions": Starter("swallowed-exceptions", "MEDIUM", ("python",)),
}


def detect(root: Path) -> tuple[Language, ...]:
    return tuple(lang for lang in LANGUAGES if (root / lang.manifest).is_file())


NPM_INIT_PLACEHOLDER = 'echo "Error: no test specified" && exit 1'
_NOT_SOURCE = frozenset({".git", ".venv", "venv", "node_modules", "vendor"})


def node_test_script(root: Path) -> bool:
    try:
        manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    scripts = manifest.get("scripts") if isinstance(manifest, dict) else None
    test = scripts.get("test") if isinstance(scripts, dict) else None
    return isinstance(test, str) and test.strip() not in ("", NPM_INIT_PLACEHOLDER)


def _has_file(root: Path, match) -> bool:
    for _, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _NOT_SOURCE and not d.startswith(".")]
        if any(match(name) for name in files):
            return True
    return False


def verify_commands(root: Path, lang: Language) -> tuple[str, ...]:
    if lang.name == "python":
        env = "--locked" if (root / "uv.lock").is_file() else "--isolated"
        return (f"PYTHONDONTWRITEBYTECODE=1 uv run {env} --with pytest "
                "python -m pytest -q || [ $? -eq 5 ]",)
    if lang.name == "node":
        install = "npm ci" if (root / "package-lock.json").is_file() \
            else "npm install --no-package-lock"
        return (install, "npm test") if node_test_script(root) else (install,)
    return ("go vet ./...", "go test ./...")


def missing_test_warnings(root: Path, langs: tuple[Language, ...]) -> list[str]:
    """What init found no test for, by the file names each runner collects."""
    out = []
    for lang in langs:
        if lang.name == "python" and not _has_file(
                root, lambda n: n.endswith(".py") and (n.startswith("test_")
                                                        or n.endswith("_test.py"))):
            out.append("no python test found (test_*.py or *_test.py): the python "
                       "scope passes with nothing collected until you add one")
        elif lang.name == "node" and not node_test_script(root):
            out.append("package.json has no test script (npm init's placeholder "
                       "counts as none), so the node scope only installs: add a "
                       "test script and a {run: \"npm test\"} step to repo.yaml")
        elif lang.name == "go" and not _has_file(root, lambda n: n.endswith("_test.go")):
            out.append("no go test found (*_test.go): go test passes with no test "
                       "files until you add one")
    return out


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_repo_yaml(root: Path, langs: tuple[Language, ...]) -> str:
    lines = [
        "# Written by `warden init`. The single source of truth for repo facts:",
        "# components, risk tiers, verify commands, review settings. warden",
        "# schema-validates it on every load and fails closed.",
        "version: 1",
        f"repo: {_q(root.name)}",
        "",
        "# The platform release this repo's gate runs, and the tag CI installs.",
        "platform:",
        f"  pin: v{__version__}",
        "",
        "components:",
    ]
    for lang in langs:
        lines.append(f"  {lang.name}: {{path: \".\", lang: {lang.name}, "
                     f"description: {_q('detected from ' + lang.manifest)}}}")
    lines += [
        "",
        "# First glob match wins, top to bottom; anything unmatched defaults to LOW.",
        "risk_tiers:",
        '  - {glob: "repo.yaml", tier: HIGH, reason: "the gate policy itself"}',
        '  - {glob: ".warden/**", tier: HIGH, reason: "the rules and skills policy the gate enforces"}',
        '  - {glob: ".github/**", tier: HIGH, reason: "CI runs the gate from here"}',
        '  - {glob: "**", tier: LOW}',
        "",
        "# Deterministic gates, detected from the manifests. Review them: they run",
        "# locally and in CI exactly as written.",
        "verify:",
    ]
    for lang in langs:
        lines.append(f"  {lang.name}:")
        # python's one step IS pytest, and says so: verify prints counts only
        # for a step declaring its runner
        runner = ", runner: pytest" if lang.name == "python" else ""
        lines += [f"    - {{run: {_q(cmd)}{runner}}}" for cmd in verify_commands(root, lang)]
    lines += [
        "",
        f"# Repair rounds a delivery may run: 1 to the platform cap of {PLATFORM_REPAIR_CAP}.",
        "repair:",
        f"  budget: {PLATFORM_REPAIR_CAP}",
        "",
        "review:",
        f"  rules_dir: {RULES_DIR}",
        "  blocking_severities: [HIGH]",
    ]
    return "\n".join(lines) + "\n"


def _rule_text(meta: dict, body: str) -> str:
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000)
    return f"---\n{front}---\n{body.strip()}\n"


def render_rules(langs: tuple[Language, ...]) -> dict[str, str]:
    entries = {e.id: e for e in catalog_mod.load_catalog()}
    out = {}
    for entry_id, starter in STARTERS.items():
        entry = entries[entry_id]
        fits = [lang for lang in langs if lang.name in starter.languages]
        if not fits:
            continue
        intro = (f"{entry.name}: {entry.guards}\n\n"
                 f"Written by `warden init` from the guardrail catalog entry "
                 f"`{entry.id}`; `warden catalog show {entry.id}` has its "
                 f"citations.\n\nFalse-positive cost: {entry.false_positive_cost}")
        if entry.engine == "declarative":
            globs = [g for lang in fits for g in lang.globs]
            meta = {"id": starter.rule_id, "severity": starter.severity,
                    "engine": "declarative", "applies_to": globs,
                    "implements": [entry.id],
                    "checks": [dict(c) for c in entry.starter["checks"]]}
            body = (intro + "\n\nA deliberate match is dismissed with a "
                    "`warden:allow(<check-id>): <reason>` comment on or above "
                    "the line; a bare marker suppresses nothing.")
        else:
            if starter.rule_id not in mechanical_mod.CHECKERS:
                raise EnrollError(f"starter {entry.id}: no core checker "
                                  f"{starter.rule_id!r}")
            meta = {"id": starter.rule_id, "severity": starter.severity,
                    "engine": entry.engine, "applies_to": ["**"],
                    "excludes": [f"{RULES_DIR}/**"],
                    "implements": [entry.id]}
            body = intro
        out[f"{RULES_DIR}/{starter.rule_id}.md"] = _rule_text(meta, body)
    return out


def render_policy(root: Path, langs: tuple[Language, ...]) -> str:
    verify = "\n".join(f"- Any code change: `warden verify --scope {lang.name}`"
                       for lang in langs)
    text = (TEMPLATES / "skills-policy.md").read_text(encoding="utf-8")
    return text.replace("@@REPO@@", root.name).replace("@@VERIFY_LINES@@", verify)


def gate_check(prefix: str) -> str:
    """The gate job's name, the status check a branch rule requires."""
    return GATE_CHECK if not prefix else f"{GATE_CHECK} ({prefix})"


TOOLCHAIN_STEP = "require the toolchain the verify scopes run"

# The one binary the generated gate installs for itself. `uv` is the
# PLATFORM's runtime, not the enrolled repository's toolchain: warden is a uv
# tool, every job installs it with a pinned `astral-sh/setup-uv`, and the
# install job needs it before there is any repository code to verify. A gate
# that declared `uv` and refused on it would be refusing itself, and the
# pre-flight would carry a pair that cannot fire — which is what a review
# round found it doing. Subtracted here rather than left in with a
# caveat, so the rendered step is exactly what can still go wrong.
GATE_INSTALLS = ("uv",)


def render_toolchain_step(langs: tuple[Language, ...]) -> str:
    """The verify job's pre-flight: every binary the verify commands invoke
    that the gate does not install, checked before the first scope runs, and
    an absent one named. Empty — no step at all — when the gate installs
    everything the scopes run, which is a python-only enrollment.

    NO CONSUMER TOOLCHAIN IS INSTALLED, and that is the decision rather than
    the default. Installing one means the platform picks a
    consumer's Go or Node version and pins a setup action's commit sha on
    their behalf with no freshness mechanism behind it: this repo's dependabot
    watches `/` and `/examples/hello-svc`, not this template, whose pins stay
    current only because a test forces them to equal the pins of a workflow
    this repo itself runs. An action nothing here runs would have no such
    anchor and would decay unwatched inside every enrolled repository. What
    the gate DOES install is `GATE_INSTALLS` — its own runtime, above.

    ABSENT IS RED AND NAMED, never skipped. The question splits
    by whether the coverage is load-bearing: a REQUIRED tool absent is a
    failure, an OPTIONAL one a skip whose reason names the gap. Every binary
    here is in the first class, because a verify scope that could not run is
    not a scope that passed — naming the gap is how it is reported, not a
    licence to pass anyway. The exit code is the gate's own 2 (the gate DID
    NOT RUN) rather than 1 (it ran and failed), so a missing toolchain never
    reads as a test the pull request broke.
    """
    pairs = " ".join(f'"{tool}:{lang.name}"' for lang in langs
                     for tool in lang.tools if tool not in GATE_INSTALLS)
    if not pairs:
        return ""
    return f"""\
      - name: {TOOLCHAIN_STEP}
        shell: bash
        run: |
          missing=""
          for pair in {pairs}; do
            tool="${{pair%%:*}}"
            scope="${{pair#*:}}"
            command -v "$tool" > /dev/null 2>&1 && continue
            echo "warden gate: $tool is not on PATH, so the $scope verify scope could not be evaluated and the gate DID NOT RUN." >&2
            missing="$missing $tool"
          done
          if [ -n "$missing" ]; then
            echo "This gate declares the toolchain its verify commands run and installs no toolchain of this repository's own. Add a step to this workflow, above this one, that installs${{missing}} — actions/setup-go and actions/setup-node are the usual ones, each pinned to a commit sha — or change the verify commands in repo.yaml to a toolchain this runner already carries." >&2
            echo "PATH=$PATH" >&2
            exit 2
          fi
"""


CLASSIFY_STEP = "proportionate review tier"

# The newest release cut before the gate carried `warden attest classify
# --enforce`: a gate pinned there installs a warden with no such command.
LACKS_CLASSIFY_COMMAND = "v2.2.0"


def carries_classify_command(pin: str) -> bool:
    """Whether the warden release PIN names has `attest classify --enforce`."""
    return (packpin_mod._release_key("v" + pin.removeprefix("v"))
            > packpin_mod._release_key(LACKS_CLASSIFY_COMMAND))


def render_classify_step(pin: str) -> str:
    """The gate's binding of a declared light round: ci.yml's
    step, recomputing the verdict over the PUSHED range; exits 0 with no light
    round, required by certify's R-14 once one is. Below the git root every
    range reads FULL, so light attestations are refused there. PINNED to the
    release the gate installs: a pin without the command gets no step. The
    base ref reaches the shell through a quoted env var, never pasted into
    `run:`. No fork branch: the install job exits 2 when a fork PR gets no
    deploy key, and a fork that does get one is classified like any PR.
    """
    if not carries_classify_command(pin):
        return ""
    return """\
      - name: proportionate review tier
        shell: bash
        env:
          BASE_REF: ${{ github.base_ref }}
        run: |
          set -uo pipefail
          out="$RUNNER_TEMP/attest-classify.txt"
          status=0
          warden attest classify --enforce \\
            --base "origin/$BASE_REF" > "$out" 2>&1 || status=$?
          cat "$out"
          { echo '### proportionate review tier';
            echo '```'; cat "$out"; echo '```'; } >> "$GITHUB_STEP_SUMMARY"
          exit "$status"
"""


def render_workflow(langs: tuple[Language, ...], prefix: str = "",
                    pin: str = f"v{__version__}") -> str:
    """The gate workflow for the enrollment at PREFIX below the git root, `""`
    at the root, for the platform release PIN (the one `render_repo_yaml`
    writes). Below the root each job runs its `run:` steps in PREFIX
    through one job-level default, and the checkout, artifact paths and names
    carry it; the commands are the root gate's."""
    steps = render_toolchain_step(langs) + "\n".join(
        f"      - name: warden verify --scope {lang.name}\n"
        f"        shell: bash\n"
        f"        run: warden verify --scope {lang.name}"
        for lang in langs)
    text = (TEMPLATES / "workflow.yml").read_text(encoding="utf-8")
    text = text.replace("@@VERIFY_STEPS@@", steps)
    text = text.replace("@@CLASSIFY_STEP@@\n", render_classify_step(pin))
    if not prefix:
        return text
    defaults = f"    defaults:\n      run:\n        working-directory: {_q(prefix)}\n"
    # Two install steps run at the workspace root: the private check runs
    # before the checkout, when PREFIX does not exist, and the repo.yaml check
    # is the step that finds PREFIX missing. A runner refuses to start a `run:`
    # step whose working directory does not exist.
    private = "      - name: require a private repository\n        shell: bash\n"
    repo_yaml = "      - name: require repo.yaml\n        shell: bash\n"
    at_workspace = f"        working-directory: {_q('.')}\n"
    for old, new, count in (
            (private, private + at_workspace, 1),
            (repo_yaml, repo_yaml + at_workspace, 1),
            ("if [ ! -f repo.yaml ]; then", f"if [ ! -f {prefix}/repo.yaml ]; then", 1),
            ("warden gate: repo.yaml is missing", f"warden gate: {prefix}/repo.yaml is missing", 1),
            ("\nname: warden\n", f"\nname: warden ({prefix})\n", 1),
            ("    name: warden install\n", f"    name: warden install ({prefix})\n", 1),
            ("    name: warden verify\n", f"    name: warden verify ({prefix})\n", 1),
            (f"    name: {GATE_CHECK}\n", f"    name: {gate_check(prefix)}\n", 1),
            ("    runs-on: ubuntu-latest\n", "    runs-on: ubuntu-latest\n" + defaults, 3),
            ("sparse-checkout: /repo.yaml\n", f"sparse-checkout: /{prefix}/repo.yaml\n", 1),
            ("path: .warden/out/", f"path: {prefix}/.warden/out/", 2)):
        if text.count(old) != count:
            raise EnrollError(f"the workflow template holds {text.count(old)} of "
                              f"{old.strip()!r}, not {count}; nothing was written")
        text = text.replace(old, new)
    return text


_SAFE_FOLDER = re.compile(r"[A-Za-z0-9._-]+")


def enrollment_prefix(root: Path) -> str:
    """The path from the git root to ROOT, `""` at the root. Refused outside a
    git repository, since review and certify read git, and below the root when
    a folder name is not letters, digits, `.`, `_` or `-`: the gate names the
    path in a file name, a sparse-checkout pattern and YAML."""
    try:
        _, prefix = gate_workflows.git_location(root)
    except gate_workflows.GitLocationError as e:
        if "not a git repository" in str(e):
            raise EnrollError(
                f"{root} is not inside a git repository; warden reads git history "
                "to review and certify, so run git init first") from e
        raise EnrollError(f"git could not find the repository root for {root}, so "
                          f"nothing was written: {e}") from e
    if prefix and not all(_SAFE_FOLDER.fullmatch(part) for part in prefix.split("/")):
        raise EnrollError(
            f"{root} is at {prefix!r} below the git root; the gate names that path in "
            "its workflow file name, a sparse-checkout pattern and YAML, so each "
            "folder name in it must be letters, digits, '.', '_' or '-'. Nothing "
            "was written")
    return prefix


def workflow_path(prefix: str) -> str:
    """Where the gate is written, relative to the enrolled directory."""
    up = "".join("../" for _ in prefix.split("/")) if prefix else ""
    return f"{up}{WORKFLOW_DIR}/{gate_workflows.workflow_name(prefix)}"


@dataclass(frozen=True)
class Plan:
    langs: tuple[Language, ...]
    files: dict[str, str]  # path relative to the enrolled directory -> text
    prefix: str = ""


def plan(root: Path) -> Plan:
    """The detected languages and every file init would create, validated."""
    prefix = enrollment_prefix(root)
    langs = detect(root)
    if not langs:
        raise EnrollError(
            f"no pyproject.toml, package.json or go.mod in {root}; init reads "
            "manifests only in the directory it enrolls, so run it in the "
            "directory that holds one")
    files = {config_mod.CONFIG_NAME: render_repo_yaml(root, langs)}
    files.update(render_rules(langs))
    files[POLICY_PATH] = render_policy(root, langs)
    workflow = workflow_path(prefix)
    files[workflow] = render_workflow(langs, prefix)

    config_mod.parse(files[config_mod.CONFIG_NAME].encode(), root,
                     config_mod.CONFIG_NAME)
    rules_mod.rules_from_bytes([(root / rel, text.encode()) for rel, text in files.items()
                                if rel.startswith(RULES_DIR + "/")], RULES_DIR)
    yamlio.load(files[workflow])
    return Plan(langs, files, prefix)


def gitignore_entries(langs: tuple[Language, ...]) -> tuple[str, ...]:
    return GITIGNORE_ENTRIES + tuple(
        e for lang in langs for e in BUILD_OUTPUT_IGNORES.get(lang.name, ()))


def _gitignore_addition(root: Path, langs: tuple[Language, ...]) -> bytes:
    """Bytes, because git takes any bytes in a .gitignore: an existing line
    that is not UTF-8 is compared and kept exactly as it is."""
    path = root / ".gitignore"
    existing = path.read_bytes() if path.is_file() else b""
    present = {line.strip() for line in existing.splitlines()}
    missing = [e for e in gitignore_entries(langs) if e.encode() not in present]
    if not missing:
        return b""
    lead = b"" if not existing or existing.endswith(b"\n") else b"\n"
    return (lead + (b"\n" if existing else b"")
            + b"# warden: evidence run dirs, derived review-memory state, build output\n"
            + "".join(f"{e}\n" for e in missing).encode())


def _blocked_paths(root: Path, files: dict[str, str]) -> list[str]:
    """Paths init would have to write through that are not plain directories
    (a file or a symlink where a folder belongs), or a `.gitignore` that is not
    a plain file. Refused up front so a failure never leaves half an enrollment."""
    blocked = set()
    for rel in files:
        for parent in Path(rel).parents:
            folder = root / parent
            if parent == Path(".") or not os.path.lexists(folder):
                continue
            if folder.is_symlink() or not folder.is_dir():
                blocked.add(parent.as_posix())
    gitignore = root / ".gitignore"
    if os.path.lexists(gitignore) and (gitignore.is_symlink() or not gitignore.is_file()):
        blocked.add(".gitignore")
    return sorted(blocked)


def run(root: Path) -> int:
    try:
        enrollment = plan(root)
    except (EnrollError, config_mod.ConfigError, rules_mod.RuleError,
            catalog_mod.CatalogError) as e:
        print(f"warden init: {e}", file=sys.stderr)
        return 2
    langs, files = enrollment.langs, enrollment.files

    existing = [rel for rel in files if os.path.lexists(root / rel)]
    if existing:
        # `svc/api` and `svc-api` share one gate file name: say whose it is.
        whose = ""
        workflow = workflow_path(enrollment.prefix)
        if workflow in existing:
            owner = gate_workflows.named_enrollment(root / workflow)
            if owner is not None and owner != enrollment.prefix:
                whose = (f". {workflow} is the gate of "
                         + (f"the enrollment at `{owner}`" if owner else "the root enrollment"))
        print("warden init: refusing to overwrite existing file(s): "
              + ", ".join(existing) + " — nothing was written" + whose, file=sys.stderr)
        return 1
    blocked = _blocked_paths(root, files)
    if blocked:
        print("warden init: refusing to write through: " + ", ".join(blocked)
              + " — nothing was written", file=sys.stderr)
        return 1
    try:
        addition = _gitignore_addition(root, langs)
    except OSError as e:
        print(f"warden init: could not read .gitignore ({e}); nothing was written",
              file=sys.stderr)
        return 2

    created: list[Path] = []
    try:
        for rel, text in files.items():
            target = root / rel
            for parent in reversed(Path(rel).parents):
                folder = root / parent
                if parent != Path(".") and not folder.exists():
                    folder.mkdir()
                    created.append(folder)
            with open(target, "x", encoding="utf-8") as fh:
                created.append(target)
                fh.write(text)
        if addition:
            with open(root / ".gitignore", "ab") as fh:
                fh.write(addition)
    except (OSError, ValueError) as e:
        for path in reversed(created):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        print(f"warden init: could not write the enrollment ({e}); "
              "nothing was left behind", file=sys.stderr)
        return 2

    names = ", ".join(f"{lang.name} ({lang.manifest})" for lang in langs)
    print(f"warden init: enrolled {root.name}: {names}")
    for rel in files:
        print(f"  wrote {rel}")
    if addition:
        print("  updated .gitignore")
    for warning in missing_test_warnings(root, langs):
        print(f"  warning: {warning}")
    scopes = " ".join(f"warden verify --scope {lang.name};" for lang in langs)
    print(f"""
Next steps:
  1. Read the verify commands in repo.yaml, then run them: {scopes.rstrip(';')}
  2. Commit the enrollment: git add -A && git commit -m "enroll in warden"
  3. Keep this repository private. The gate uploads the private platform wheel as
     a one-day artifact any reader of a run can download, so a consumer
     repository must be private: its install job refuses one that is not.
  4. Ask the Nightgate owner for a read-only deploy key for this repository and
     store it as the {DEPLOY_KEY_SECRET} secret (Installation, "CI access to the platform").
  5. Make "{gate_check(enrollment.prefix)}" a required status check on your default branch.
  6. warden certify --level 3
  7. warden rules recommend: the catalog entries that depend on what your code does.
  8. To run the skill pack, install it at the same tag, once per machine:
     {"; ".join(packpin_mod.install_commands("v" + __version__))}
     {textwrap.fill(packpin_mod.check_step("v" + __version__), 78, subsequent_indent="     ",
                    break_on_hyphens=False, break_long_words=False)}""")
    return 0
