"""`warden declare check` — does the gate's behaviour match what it declares?

The defect class this detects: the gate states, or relies on, something nothing
verifies. Each instance is individually fixable and drifts again, because
nothing can SEE the drift. Instances of the class:

- verify-scope reach INFERRED from `cwd` + `components:`, so a command that
  reaches outside its cwd is silently exempted from diffs outside it;
- a CI gate job that runs only some of the scopes the repo's diffs can require,
  so a PR shows a true and unclearable NO VERIFY RESULT line;
- `components:` is a gate input for that derivation and is not hashed into
  `rules_version`, so the gate's demands can move while the version stands
  still;
- a gate-surface directory (such as a cage source declaring the fences of an
  unattended runner) with no risk tier at all, classifying LOW by default.

So this module is the detector for the class, not a fix for one instance. Five
named checks, each
comparing a DECLARATION to the behaviour it is supposed to govern:

- **D-01 scope reach** — every verify/deploy step's reach is DECLARED
  (`covers:`) or INFERRED, and a step that NARROWS by inference is reported.
  The inference is still supported and still fail-closed; what is refused is
  its being invisible.
- **D-02 gate inputs** — every input the gate reads is in `GATE_INPUTS` with a
  `hashed` claim and a reason, and every claim is PROVED: repo.yaml's subtrees
  by perturbation (change the bytes, see whether `rules_version` moves), the
  package surfaces by set-equality against the tuple `rules.py` itself hashes.
  Either direction is drift — an input that is hashed and declared not to be,
  or declared hashed and silently dropped.
- **D-03 scope runners** — every declared verify scope is executed in the SAME
  CI job that runs `warden review`, or in a job that job takes the verify
  results of. Evidence pairs by SHA inside one runner's filesystem, so a scope
  verified in another job, with nothing carrying its result across, is
  invisible to the sticky comment and the gate says NO VERIFY RESULT forever.
- **D-04 gate-surface tiers** — every path the repo declares as gate surface
  (repo.yaml's `protected_paths`, the policy's Never-edit paragraph) classifies
  under a `risk_tiers` glob, not the unmatched LOW default. An explicit glob is
  the exemption: declaring `{glob: x, tier: LOW}` is a statement, and the
  default is not.
- **D-05 forge auto-merge** — where graph.yaml declares `review.delegation`,
  whose `permits` cannot spell auto-merge, the forge must not allow it
  either. It reads the repository's `allow_auto_merge` through `gh api`
  (reading GitHub is within the $0-infrastructure rule) and reports REFUSED
  when it is true, PASS when false, and UNREADABLE — never clean — when `gh`
  cannot answer. No certification rung claims D-05, so UNREADABLE prints and
  leaves the exit code where the other checks put it.

WHAT THIS CANNOT SEE, declared rather than implied — the same honesty it asks of
everything else:

- D-04 reads gate surface from the two declarations above. A gate-surface class
  NEITHER of them names is invisible here.
- D-02's package half is a set comparison against `rules._EXTRA_SURFACES`, not a
  perturbation: those files live at an absolute path inside the installed
  package and cannot be mutated in a scratch tree. It catches a surface added or
  dropped; it does not prove the bytes reach the hash.
- D-03 reads `run:` text out of the workflow YAML. It asks whether the scope is
  RUN in the gate job, never whether the step can gate: it does not evaluate
  `if:` conditions, trigger filters, reusable-workflow indirection, a matrix, or
  a swallowed exit (`|| true`, `set +e`). `certify`'s E-01 `ci_step_enforced` is
  the check that reads for the swallow, and it states its own residuals; D-03
  answers the narrower question — does a scope the gate
  REPORTS on have a run in the job whose filesystem the evidence pairs in.
  A result TAKEN from another job is read from structure: `needs`; an upload
  naming `verify-result.json` under the `.warden/out/` of the working
  directory where a step before it ran the scope; a download of that
  artifact by name to a `${{ runner.temp }}` path, with nothing but `name`
  and `path` under `with:` and no other download that can write into that
  path; and, before review, the one-line `warden take --from` step in the
  exact form `warden init` writes, with `shell: bash` and no
  `working-directory` of its own, in a job and workflow with no `env`, whose
  effective working directory (the step's own, else the job's
  `defaults.run.working-directory`, else the workflow's) is the `warden
  review` step's. Any other take step is drift, and the drift names why. The
  step that ran the scope must also have run in that same directory, since
  review pairs a result by commit and scope name alone; where the two differ,
  in either direction, the drift names both. It does not prove that no earlier
  step shadows `warden` on PATH (the same holds for `warden review`) or runs
  review spelled so its text does not read `warden review`, does not read
  `if:` on any job or step other than the take step, and does not check that
  the upload glob matches. It does not read what other steps before review
  do: one can plant results in the download directory or set `BASH_ENV`
  through `$GITHUB_ENV` before the take, or rewrite `.warden/out` after it,
  and `warden review` has the same exposure.
- D-05 reads one repository setting. It does not read workflows for a step
  running `gh pr merge --auto`, a ruleset's required checks (only classic
  branch protection's, and those as context, not verdict), or who holds the
  token that could turn the setting on tomorrow. It applies only where
  `review.delegation` is declared; a graph.yaml without that block is
  reported not-applicable.
- None of the five can tell a declaration that is WRONG from one that is
  merely present: `covers: ["**"]` on a step that runs nothing is declared and
  unverifiable. This checks that a declaration exists and that behaviour agrees
  with it, never that the author was honest.

Exit codes are the contract: 0 clean, 1 drift reported, 2 a check could not
evaluate — fail-closed, and a 2 is never "no drift". D-05's UNREADABLE is not
a 2: it is a verdict about the forge, not about the tree, and the render and
the JSON verdict both say `unreadable` rather than clean.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import config as config_mod
from . import gate_workflows
from . import graph as graph_mod
from . import rules as rules_mod
from . import verify as verify_mod
from . import yamlio

POLICY_PATH = Path(".warden") / "skills-policy.md"
WORKFLOWS_DIR = Path(".github") / "workflows"

CLEAN = "clean"
DRIFT = "drift"
SKIPPED = "not-applicable"
UNREADABLE = "unreadable"   # D-05 only: the forge did not answer, never clean


class DeclareError(Exception):
    """A check could not be evaluated — exit 2, never a verdict of clean."""


@dataclass(frozen=True)
class GateInput:
    """One input the gate reads, and whether it is hashed into rules_version.

    `where` is how the check LOCATES it: `repo.yaml:<key>` for a top-level key
    of repo.yaml (perturbable in a scratch tree), `rules:*.md` and
    `checkers:*.py` for the two root-relative directories, and `package:<name>`
    for a file inside the installed warden package.
    """
    where: str
    hashed: bool
    reason: str


# The INVENTORY. Every input the gate reads, with its hash claim and the reason
# for it. A new gate input enters here with its own claim — D-02 refuses one it
# has never heard of, in either direction.
GATE_INPUTS: tuple[GateInput, ...] = (
    GateInput("rules:*.md", True,
              "the rules themselves: a verdict is only comparable to a verdict "
              "judged under the same ruleset"),
    GateInput("checkers:*.py", True,
              "a project checker plugin IS policy execution; its edits change "
              "the repo's gate cohort"),
    GateInput("package:review-findings.schema.json", True,
              "the findings contract every reviewer and consumer parses"),
    GateInput("package:mechanical.py", True,
              "the deterministic checkers are policy written as code"),
    GateInput("repo.yaml:review", True,
              "the enforcement keys a verdict is judged under — rules_dir, "
              "blocking_severities, context_excludes"),
    GateInput("repo.yaml:components", False,
              "NOT hashed, by a recorded ruling (`warden decide list` "
              "carries the shard). It is a gate input — a verify scope narrows "
              "only where a component declares the area — but it changes which "
              "scopes are REPORTED as required and no exit code, while "
              "widening the hash fails certification S-05 for every enrolled "
              "consumer at once (a backtest must name the CURRENT version). "
              "A step's `covers:` is the real narrowing of the gap: a "
              "declared reach does not depend on `components:` at all"),
    GateInput("repo.yaml:verify", False,
              "NOT hashed: the commands are run and their results are "
              "artifacts stamped with their own SHAs, so the evidence says "
              "what ran. `covers:` rides here and takes the "
              "components ruling above with it"),
    GateInput("repo.yaml:risk_tiers", False,
              "NOT hashed: a tier routes human attention and the design "
              "record, and produces no finding and no exit code"),
    GateInput("repo.yaml:protected_paths", False,
              "NOT hashed: advisory context in `warden explain`; the rule that "
              "bites on a schema change is `schema-freeze`, which IS hashed"),
    GateInput("repo.yaml:deploy", False,
              "NOT hashed: the same shape as `verify:` and judged the same way "
              "— `warden deploy` refuses unless the checkout IS the commit and "
              "that commit's own diff carried a clean attestation, and it "
              "leaves a deploy-result artifact"),
    GateInput("repo.yaml:design", False,
              "NOT hashed: it declares which risk tiers owe a decision record. "
              "`warden explain` reports it and no checker fires on it — the "
              "obligation is carried by the protocol and by whether a shard "
              "exists, not by a finding"),
    GateInput("repo.yaml:platform", False,
              "NOT hashed: the pin is enforced by `enforce_platform_pin`, "
              "which fails closed on a mismatch — a separate mechanism with "
              "its own refusal, not a cohort key"),
    GateInput("repo.yaml:repair", False,
              "NOT hashed: the repair budget bounds how many repair rounds a "
              "delivery runs before it reverts or parks. It produces no finding "
              "and no `warden review` exit code; certify R-12 reads it and the "
              "schema bounds it, each with its own refusal"),
    GateInput("repo.yaml:proportion", False,
              "NOT hashed: `never_light` decides which paths the proportionate "
              "review round may never reach. It changes how much REVIEW a "
              "change buys, never what a checker finds or what `warden "
              "review` exits, so two verdicts judged either side of an edit "
              "to this block remain comparable. `proportion.classify` reads "
              "the list from repo.yaml at each end of the range and unions "
              "them, which is the refusal that bounds it; `prose_roots`, the "
              "permissive key this block once carried, is refused outright "
              "with the proportionate tier's own message. What it DOES move "
              "is `warden attest classify --enforce`: that step recomputes the "
              "tier over the pushed range and exits 1 on a committed "
              "light-crew shard the range does not earn, so widening the "
              "floor here can turn that step red. It still produces no "
              "finding and no `warden review` exit code"),
)


@dataclass(frozen=True)
class Finding:
    check: str
    detail: str


@dataclass(frozen=True)
class CheckResult:
    id: str
    label: str
    status: str            # CLEAN / DRIFT / SKIPPED
    detail: str
    findings: tuple[Finding, ...] = ()


@dataclass(frozen=True)
class Report:
    results: tuple[CheckResult, ...]

    @property
    def drifted(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status == DRIFT)


# --- D-01 ---------------------------------------------------------------------

_WALK_SKIP = {".git", ".venv", ".ruff_cache", ".pytest_cache", "__pycache__",
              "node_modules", ".dolt"}


def tree_paths(root: Path) -> tuple[str, ...]:
    """Every repo-relative file path in the tree, for matching a glob against.

    Walked off the filesystem rather than `git ls-files`: this runs on a
    consumer checkout that may not be a git repository, and "could not list the
    tree" would otherwise become "every glob is fine". Evidence dirs and caches
    are skipped — a `covers:` glob whose only match is `.warden/out/` matches
    nothing a diff can contain.
    """
    out: list[str] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = sorted(cur.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.name in _WALK_SKIP:
                continue
            if e.is_symlink():
                continue
            if e.is_dir():
                if cur == root and e.name == ".warden":
                    # `.warden/out` is evidence, not tree. Everything else under
                    # `.warden` IS tree — the policy file, the rules, the
                    # certification overlay. Files go to `out`, directories
                    # to the stack, here as everywhere else: a FILE pushed
                    # onto the stack raises in `iterdir()`, is swallowed, and
                    # drops out of the set every `covers:` glob is matched
                    # against.
                    for d in sorted(e.iterdir()):
                        if d.is_symlink() or d.name == "out":
                            continue
                        if d.is_dir():
                            stack.append(d)
                        elif d.is_file():
                            out.append(d.relative_to(root).as_posix())
                    continue
                stack.append(e)
            elif e.is_file():
                out.append(e.relative_to(root).as_posix())
    return tuple(sorted(out))


def check_scope_reach(config: config_mod.RepoConfig, root: Path) -> CheckResult:
    """Every step's reach is declared, its inference is visible, and a declared
    reach can actually fire.

    Two drifts, because a declaration can be unsound as well as absent:

    - a step that NARROWS by inference. A step whose reach is INFERRED_WIDE
      claims nothing and exempts nothing, so it is not drift — the drift is a
      scope claiming LESS than the tree on an inference no declaration in the
      file states.
    - a `covers:` glob that matches NO path in the tree. That is worse than the
      inference it replaces: it exempts the step from every diff forever, and
      `reach_of` reports `declared`, so the very check meant to make the reach
      visible would call it settled. `covers: ["backend"]` — a bare directory name,
      a legal glob matching one file — and any typo land here. The trailing-
      slash spelling is normalized instead (`verify.covers_globs`), because
      that one is repo.yaml's own convention for an area rather than a mistake.
    """
    inferred: list[Finding] = []
    declared = 0
    paths = tree_paths(root)
    if not paths:
        raise DeclareError(
            f"{root} has no readable files, so no `covers:` glob can be checked "
            "against the tree — a reach this command cannot verify is not one "
            "it may report as declared")
    for which in ("verify", "deploy"):
        for scope, steps in getattr(config, which).items():
            areas = [a for a in (verify_mod._norm(c.path)
                                 for c in config.components) if a]
            for step in steps:
                arm, why = verify_mod.step_reach(step, areas)
                if arm == verify_mod.DECLARED:
                    declared += 1
                    for glob in verify_mod.covers_globs(step):
                        if not any(rules_mod.glob_match(glob, p)
                                   for p in paths):
                            inferred.append(Finding(
                                f"{which}:{scope}",
                                f"`covers: [\"{glob}\"]` matches no path in "
                                "the tree, so it exempts this step from EVERY "
                                "diff while reporting a declared reach. A bare "
                                "directory name is a glob that matches one "
                                f"FILE — write `{glob}/**` for the subtree"))
                    continue
                if arm == verify_mod.INFERRED_WIDE:
                    continue  # claims nothing, exempts nothing
                inferred.append(Finding(
                    f"{which}:{scope}",
                    f"`{step.run[:60]}` narrows to {why} by INFERENCE — add "
                    "`covers: [glob, ...]` stating what this step exercises, or "
                    "`covers: [\"**\"]` if it reaches the whole tree"))
    if inferred:
        return CheckResult(
            "D-01", "every verify scope's reach is declared and can fire", DRIFT,
            f"{len(inferred)} unsound reach declaration(s) or inferred "
            f"narrowing(s), {declared} step(s) declaring a reach",
            tuple(inferred))
    return CheckResult("D-01", "every verify scope's reach is declared and can "
                       "fire", CLEAN,
                       f"{declared} step(s) declare `covers:`, every glob "
                       "matching the tree; no step narrows by inference")


# --- D-02 ---------------------------------------------------------------------

def _repo_yaml_keys(root: Path) -> set[str]:
    """repo.yaml's top-level keys, through the SHARED reader.

    `config.read_repo_yaml` and never a `read_text()` of the path: a direct
    blocking read parks forever on a FIFO in `repo.yaml`'s place, and a test in
    tests/test_config.py refuses a direct read anywhere in the package.
    """
    try:
        raw = yamlio.load(config_mod.read_repo_yaml(root).decode("utf-8"))
    except (OSError, UnicodeDecodeError, config_mod.ConfigError,
            yaml.YAMLError) as e:
        raise DeclareError(f"repo.yaml could not be read for the gate-input "
                           f"inventory ({e})") from e
    if not isinstance(raw, dict):
        raise DeclareError("repo.yaml is not a mapping, so its gate inputs "
                           "cannot be enumerated")
    return set(raw)


def _schema_keys() -> set[str]:
    """Every top-level key repo.yaml MAY declare, read off the shipped schema.

    The inventory's open end is here, not in any one repo's file:
    `repo.schema.json` sets `additionalProperties: false`, so a key no consumer
    can write cannot appear in a valid repo.yaml — which means a NEW gate input
    arrives by someone adding a property to the schema, and at that moment
    nothing else obliges anyone to say whether it is hashed. So the
    comparison is inventory-versus-SCHEMA, and a property added without a hash
    claim is drift at the commit that adds it.
    """
    path = Path(rules_mod.__file__).resolve().parent / "schemas" / "repo.schema.json"
    try:
        doc = json.loads(path.read_text())
        props = doc["properties"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
        raise DeclareError(f"{path}: the repo.yaml schema could not be read, so "
                           f"the gate-input inventory cannot be checked against "
                           f"it ({e})") from e
    return set(props)


def _version_moves_when(root: Path, scratch: Path, key: str) -> bool:
    """Perturb repo.yaml's `key` in a scratch copy; did `rules_version` move?

    The only honest way to answer "is this input hashed": change it and look.
    A claim read off the source is a claim about the code someone meant to
    write; this is a claim about the code that runs.
    """
    # The shared reader, for the reason `_repo_yaml_keys` states.
    raw = yamlio.load(config_mod.read_repo_yaml(root).decode("utf-8"))
    rules_src = root / config_mod.load(root).review.rules_dir
    work = scratch / f"perturb-{key}"
    work.mkdir(parents=True)
    rules_dir = work / ".warden" / "rules"
    rules_dir.parent.mkdir(parents=True)
    if rules_src.is_dir():
        shutil.copytree(rules_src, rules_dir)
    else:
        rules_dir.mkdir()
    (work / "repo.yaml").write_text(yaml.safe_dump(raw))
    before = rules_mod.rules_version(rules_dir, work)
    # A perturbation the hash cannot miss IF it reads the key at all: a new
    # nested value under it, which survives canonicalization.
    perturbed = dict(raw)
    perturbed[key] = {"__declare_probe__": "drift"} if not isinstance(
        raw[key], list) else [*raw[key], {"__declare_probe__": "drift"}]
    (work / "repo.yaml").write_text(yaml.safe_dump(perturbed))
    after = rules_mod.rules_version(rules_dir, work)
    return before != after


def _tree_surface_moves(root: Path, scratch: Path, what: str) -> bool:
    """Did adding a file to a root-relative hashed directory move the version?"""
    rules_src = root / config_mod.load(root).review.rules_dir
    work = scratch / f"perturb-{what}"
    work.mkdir(parents=True)
    rules_dir = work / ".warden" / "rules"
    rules_dir.parent.mkdir(parents=True)
    if rules_src.is_dir():
        shutil.copytree(rules_src, rules_dir)
    else:
        rules_dir.mkdir()
    shutil.copy(root / "repo.yaml", work / "repo.yaml")
    before = rules_mod.rules_version(rules_dir, work)
    if what == "rules:*.md":
        (rules_dir / "zz-declare-probe.md").write_text(
            "---\nid: zz-declare-probe\nseverity: LOW\nengine: claude\n"
            "applies_to: [\"**\"]\n---\nprobe\n")
    else:
        checkers = rules_dir.parent / "checkers"
        checkers.mkdir(exist_ok=True)
        (checkers / "zz_declare_probe.py").write_text("# probe\n")
    after = rules_mod.rules_version(rules_dir, work)
    return before != after


def check_gate_inputs(root: Path, scratch: Path) -> CheckResult:
    """Every gate input is declared with a hash claim, and every claim holds."""
    findings: list[Finding] = []
    declared_keys = {g.where.split(":", 1)[1] for g in GATE_INPUTS
                     if g.where.startswith("repo.yaml:")}
    present = _repo_yaml_keys(root)
    # `version` and `repo` are identity, not input: they name which contract and
    # which repo, and neither changes what the gate demands of a diff.
    for key in sorted(_schema_keys() - declared_keys - {"version", "repo"}):
        findings.append(Finding(
            "inventory",
            f"repo.schema.json allows `{key}:`, which no GATE_INPUTS entry "
            "claims as hashed or not-hashed. Add it with a reason — an input "
            "nobody stated a claim about can change the gate unnoticed"))
    for gate in GATE_INPUTS:
        kind, _, name = gate.where.partition(":")
        if kind == "repo.yaml":
            if name not in present:
                continue  # an optional key this repo does not declare
            moved = _version_moves_when(root, scratch, name)
        elif kind in ("rules", "checkers"):
            moved = _tree_surface_moves(root, scratch, gate.where)
        else:
            moved = name in {s.name for s in rules_mod._EXTRA_SURFACES}
        if moved != gate.hashed:
            claim = "hashed" if gate.hashed else "NOT hashed"
            saw = "moves" if moved else "does not move"
            findings.append(Finding(
                gate.where,
                f"declared {claim}, but changing it {saw} `rules_version`. "
                "Either the hash or the inventory changed without the other: "
                "correct whichever of the two is wrong"))
    if findings:
        return CheckResult("D-02", "every gate input's hash claim holds", DRIFT,
                           f"{len(findings)} input(s) disagree with the "
                           "inventory", tuple(findings))
    return CheckResult("D-02", "every gate input's hash claim holds", CLEAN,
                       f"{len(GATE_INPUTS)} declared input(s); "
                       f"{sum(1 for g in GATE_INPUTS if g.hashed)} hashed, "
                       "each claim proved")


# --- D-03 ---------------------------------------------------------------------

# A `warden verify ... --scope X` invocation, `--scope=X` included, matched
# against text that has already had its COMMENTS and its QUOTED SPANS removed
# (`_shell_text`). That is where the discrimination lives, and it has to: the
# invocation is almost always WRAPPED — `uv run --locked --project ../.. warden
# verify --scope app` is the spelling `docs/wiki/Quickstart.md` prescribes and
# `examples/hello-svc`'s own workflow uses — so anchoring on `warden` being the
# first word of a command would make every real consumer invocation invisible.
# Anchoring is the wrong tool for "is this quoted"; removing the quotes is the
# right one.
_SCOPE_RUN = re.compile(
    r"\bwarden\s+verify\b[^\n;&|]*?--scope[=\s]+([A-Za-z0-9_.-]+)")

# ...and WHERE in a command it may match. Quoting alone cannot tell a mention
# from a run: these three unquoted shapes would capture a scope the job never
# executes, and D-03 would render CLEAN over it — a silent pass in the check
# whose subject is a scope nobody ran:
#
#     echo warden verify --scope tests
#     if false; then warden verify --scope tests; fi
#     gate() { warden verify --scope tests; }
#
# So the second half of the discrimination is COMMAND POSITION, which is NOT
# asking whether `warden` is the first word: that would make every WRAPPED
# invocation invisible — `uv run --locked --project ../.. warden verify
# --scope app`, the spelling the docs prescribe and the example consumer uses.
# What is asked here is what the first word of the enclosing COMMAND is, which
# leaves the wrapper spelling a run and a mention-maker's argument not one.
_MENTION_MAKERS = frozenset({"echo", "printf", ":"})
# A command inside a compound statement is not an UNCONDITIONAL run of the
# scope, and D-03's question is whether the job runs it. Erring here reports
# drift (exit 1, loud) rather than passing in silence.
#
# The BLOCK, not the line. Testing only the first word of each command catches
# `if false; then warden verify --scope X; fi` and nothing else: written the
# way a `run: |` block actually is — body on its own line — the body is a
# top-level command again and D-03 would render CLEAN over a scope the job
# never runs (the multi-line `if` is the shape this repo's own gate job uses).
# So an opener pushes a block that its MATCHING closer pops, and everything
# between is inside.
#
# The openers are a STACK and not a counter, and a closer pops only when it
# MATCHES the block on top. A bare counter decremented on any `fi`/`done`/
# `esac` would let a CROSS-TYPE closer zero the depth, so an invocation
# genuinely inside a conditional would count as an unconditional run: D-03
# CLEAN over a scope the job never runs, which is the class this whole
# discriminator exists to refuse. An unmatched closer is IGNORED, so the block
# stays open and the reader errs toward reporting drift.
#
# WHAT THE STACK CANNOT REFUSE on its own: a heredoc body line reading `fi` is
# a MATCHED closer for the enclosing `if`, so it would pop the block and the
# invocation after it would read as a run. That is closed one layer up —
# `_shell_text` drops heredoc bodies before this reader sees a line of them,
# so a `fi` in a body is never a closer here.
_BLOCK_OPENERS = {"if": "fi", "while": "done", "until": "done", "for": "done",
                  "select": "done", "case": "esac"}
_BLOCK_CLOSERS = frozenset(_BLOCK_OPENERS.values())
_CONTINUATIONS = frozenset({"then", "elif", "else", "do", "in", "time", "!"})
_SHELL_KEYWORDS = (frozenset(_BLOCK_OPENERS) | _BLOCK_CLOSERS
                   | _CONTINUATIONS | {"function"})
_LEADING_ASSIGNMENTS = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _commands(text: str) -> list[tuple[str, int]]:
    """`_shell_text` output split into (command, brace depth) pairs.

    Separators are `;`, `&&`, `||`, `|`, `&` and newline. `_shell_text`
    writes a `;` of its own after each `)` that ends a group bash takes a
    COMMAND after — a subshell, a `name()` header, a case arm's pattern —
    so `a) (echo L) esac`, which bash 5.2 runs, ends its arm at the `)`
    rather than fusing the `esac` into it. It is that reader, not this one,
    that can tell such a `)` from the `)` of `echo !(a) case` or
    `echo $(date) case`, after which bash reads an ARGUMENT.

    A standalone `{` or
    `}` — brace-delimited by whitespace, so `${{ ... }}` in a workflow
    expression is not one — opens and closes a block, and a command inside one
    carries a non-zero depth: that is the function-body shape, and it is also
    every grouped command, which is the fail-loud direction. The KEYWORD blocks
    (`if`/`fi`, `for`/`done`, `case`/`esac`) are counted by `_scope_runs`,
    which reads these in order.
    """
    out: list[tuple[str, int]] = []
    buf: list[str] = []
    depth = 0

    def flush() -> None:
        cmd = "".join(buf).strip()
        del buf[:]
        if cmd:
            out.append((cmd, depth))

    i, n = 0, len(text)
    while i < n:
        if text[i:i + 2] in ("&&", "||"):
            flush()
            i += 2
            continue
        ch = text[i]
        if ch in ";&|\n":
            flush()
            i += 1
            continue
        if (ch in "{}" and (i == 0 or text[i - 1].isspace())
                and (i + 1 >= n or text[i + 1].isspace())):
            flush()
            depth = max(0, depth + (1 if ch == "{" else -1))
            i += 1
            continue
        buf.append(ch)
        i += 1
    flush()
    return out


def _scope_runs(text: str) -> set[str]:
    """The verify scopes ONE `run:` step actually RUNS, unconditionally.

    Per STEP and not per job: `_job_runs` keeps the steps apart so an
    unbalanced keyword in one step (a `case` arm quoted oddly, an unterminated
    heredoc)
    cannot leak a block depth into the next step and hide a real invocation
    there.
    """
    found: set[str] = set()
    open_blocks: list[str] = []
    for command, braces in _commands(text):
        # Leading `VAR=value` words are the shell's, not the command's, so
        # they are stripped rather than treated as a mention — `FOO=1 warden
        # verify --scope app` runs the scope. A command that is ONLY an
        # assignment (`CMD=warden ...`) is not a run of anything.
        stripped = _LEADING_ASSIGNMENTS.sub("", command)
        words = stripped.split()
        if not words:
            continue
        head = words[0]
        if head in _BLOCK_OPENERS:
            open_blocks.append(_BLOCK_OPENERS[head])
            continue
        if head in _BLOCK_CLOSERS:
            if open_blocks and open_blocks[-1] == head:
                open_blocks.pop()
            # an UNMATCHED closer closes nothing: the block stays open and the
            # commands after it stay inside it, which is the loud direction
            continue
        if head in _SHELL_KEYWORDS or head in _MENTION_MAKERS:
            continue
        if open_blocks or braces or _ASSIGNMENT.match(head):
            continue
        found.update(_SCOPE_RUN.findall(stripped))
    return found


def _shell_text(run: str, extglob_unknown: str | None = None) -> str:
    """A `run:` block reduced to the commands a shell would execute.

    Four things are removed:

    - backslash CONTINUATIONS are joined, so an invocation split across lines
      is seen rather than reported as a FALSE drift about a workflow that
      does run the scope;
    - `#` COMMENTS are dropped, so a commented-out `warden verify --scope X`
      does not read as a run — commenting the step out is precisely how this
      drift returns;
    - QUOTED SPANS are replaced by a placeholder, because that is what actually
      distinguishes a mention from a run. Anchoring `warden` to the start of a
      command instead would make every WRAPPED invocation invisible — `uv run
      ... warden verify --scope app`, the spelling the docs prescribe and the
      example consumer uses — while `echo 'do not run; warden verify --scope
      tests'` would still match, because the anchor reads separators inside
      quotes;
    - HEREDOC BODIES are dropped, because a body line reading `fi` would close
      a real block and an invocation inside a body would read as a run — D-03
      CLEAN over a scope the job never runs. The opener is detected INSIDE the
      scan that knows the lexical state, so it is anchored: `<<` opens a
      heredoc only when it is unquoted, outside a comment, not the `<<<`
      here-string, not the `<<=` shift-assignment, and not inside `(( ))`
      arithmetic, where it is the shift operator. An UNANCHORED search over
      the raw line would let a `<<` in a comment, in a quoted span or in a
      here-string start a PHANTOM heredoc that blanks the rest of the step,
      takes the `warden review` line with it, and turns a true DRIFT into
      `clean` at exit 0.

    The scan is ONE pass over the whole step, and its state — the open quote,
    the arithmetic depth, the `$( )` contexts, the heredocs waiting for their
    bodies — survives a newline, because bash's does. A line-by-line scan
    with every state reset at the line start diverges from bash on these
    shapes, several of them silencing a step's only `warden review` mention:
    a `<<` on the continuation line of a multi-line quoted string, a `$(( ))`
    written across lines, a body whose terminator is `EOF)` closing a `$( )`,
    a `--body "$(cat <<EOF ...)"` whose opener sits inside a quoted span, and
    a quoted-delimiter body whose last line ends in a backslash. What the
    scan models: the three quote kinds (`'`,
    `"`, `$'`) with their escapes — a backslash-escaped quote no longer ends
    a span early; backslash-newline as a continuation outside single quotes and
    outside a quoted-delimiter body, where bash keeps it literal; `$( )` as
    a fresh unquoted context, so a heredoc inside one opens even from inside
    double quotes, `EOF)` on a terminator line closes the substitution with
    the rest of that line still commands (measured on bash 3.2), a `)`
    closing a nested paren or a case arm inside it is not its closer —
    `case`/`esac` counted in command position only, since as an argument
    (`echo case`, `pytest -k case`) bash runs each as a word and the
    substitution's own `)` closes it (bash 3.2 and 5.2) — and a
    heredoc opened in a substitution that closes on the opener's own line is
    empty, as bash reads it; and `(( ))` arithmetic across lines.
    Tests in tests/test_declare_check.py keep every phantom-heredoc input
    reporting drift and hold the body shapes.

    Quoting is HALF the discrimination. What a command MEANS also depends on
    where it sits, and command position is the second question —
    `_scope_runs` asks it, over `_MENTION_MAKERS` and `_commands`. This
    function's job ends at "what text would a shell see".

    NOT a shell parser, stated as the limit it is. What remains unmodelled
    (`_commands` counts brace blocks and `_scope_runs` counts keyword blocks,
    so a compound statement written across lines is inside one either way):

    - an unterminated heredoc — a terminator never written — swallows the
      rest of its STEP, which is what bash reads too (it warns and takes the
      body to end-of-file). A delimiter written as `<<$END` is the literal
      word `$END` to bash and to this reader alike, so a `$END` line
      terminates it;
    - whatever a block this reader cannot balance swallows, it swallows only
      within its own STEP, because `_job_runs` keeps steps apart;
    - an unterminated quote swallows the rest of the STEP, because quote
      state survives a newline, which is again what bash reads (the step
      would not parse);
    - a backtick substitution, quoted or not, is taken WHOLE at its closing
      backtick and scanned as its own text (`_ShellScan._backtick`), which is
      the order bash reads one in, so a line inside one is never a top-level
      line and neither a `#` comment nor a heredoc inside it reaches the
      closer; a span with NO closing backtick swallows the rest of the step,
      as an unterminated quote does; what remains unmodelled is a nested
      substitution — an ESCAPED
      backtick is read as a literal rather than re-parsed as bash re-parses
      it, and bash rejects that shape anyway;
    - `$[ ]` arithmetic, and a `((` that opens two
      nested subshells rather than arithmetic are not modelled; a mixed-quoted
      delimiter (`<<E'O'F`) is read as the literal word rather than `EOF`;
    - `eval`, `xargs`, `bash -c` and every other indirection take their
      command as an ARGUMENT, so an invocation reached that way is read from
      the enclosing command's position: quoted, it is invisible; unquoted, it
      reads as a run of the wrapper.

    THE DIRECTION EACH ONE ERRS IN, each DRIVEN by a probe rather than
    reasoned, since a limit that invents a run matters more than one that
    hides an invocation, and binning by reasoning is easy to get backwards.
    A parametrized test in tests/test_declare_check.py holds the probes and
    refuses an entry binned under the wrong heading.
    Toward SILENCE — a scope counted as run that is not: an unquoted
    indirection (`bash -c warden verify --scope x` runs `bash -c warden` and
    nothing else, and reads as a run); a nested subshell `((`, read as
    arithmetic, whose heredoc then never opens and whose body reads as
    commands.
    Toward a false DRIFT — an invocation hidden, exit 1, loud: an
    unterminated quote; an unterminated heredoc; an unterminated backtick
    span, whose text is dropped because bash would not parse the step;
    a mixed-quoted delimiter;
    `$[ ]` arithmetic, whose `<<` opens a phantom heredoc; backtick and
    `$(...)` substitution under a mention-maker (`` echo `warden verify
    --scope x` `` runs the scope and reads as a mention); a quoted
    indirection (`bash -c "warden verify --scope x"`). The unterminated
    quote, the unterminated heredoc, the unterminated backtick span and the
    `$[ ]` shift are the ones to
    read twice: each hides the REST of the step, so where the job's only
    `warden review` mention sits after one, the job stops being a gate job
    at all — that is silence, reached from the other side, and it is why
    the escaped-quote and multi-line-quote spellings that would reach it
    are modelled rather than listed.
    """
    return _ShellScan(run, extglob_unknown).text()


# Where a heredoc delimiter WORD ends when it is not quoted: at whitespace or
# at the next operator, so `cat <<EOF > out` reads `EOF`, `<(cat <<A)` reads
# `A`, and `$(cat <<EOF)`'s delimiter is `EOF`.
_HEREDOC_WORD_END = frozenset(" \t\n;|&<>()")
# A word that makes extglob's state a question: the option itself, or the
# variable that sets it before a script's first line runs (bash 5.2 starts
# with extglob on under `BASHOPTS=extglob` in the environment).
_EXTGLOB_WORD = re.compile(r"\b(?:extglob|BASHOPTS)\b")
# The one spelling the scan honours, and only as a whole top-level line: see
# `_ShellScan._end_line`.
_TOP_LEVEL_SHOPT = re.compile(
    r"shopt[ \t]+-([su])[ \t]+(?:[A-Za-z_]+[ \t]+)*extglob(?:[ \t]+[A-Za-z_]+)*")
# Quote characters and backslashes, removed before a raw line is matched, so
# `shopt -s "extglob"` (which bash runs) is not missed.
_QUOTING = re.compile(r"[\"'\\]")
_WORD = re.compile(r"[A-Za-z_]+")
# The three ways a step's own text can take `shopt` away from the builtin, so
# that a later `shopt -s extglob` line does NOT turn extglob on. All three
# measured on bash 5.2, each followed by `builtin shopt extglob`:
#
#   shopt() { :; }            -> extglob off   (a function shadows the builtin)
#   alias shopt=:             -> extglob off   (with expand_aliases on)
#   enable -n shopt           -> extglob off   (`shopt: command not found`)
#
# Shadowing costs the line its extglob reading: the state goes UNKNOWN, and
# only a `!(` inside a `$( )` then turns that into exit 2.
#
# The alias entry does not ask whether `expand_aliases` is on. A script can
# have it from outside its own text — `BASHOPTS=expand_aliases` in the
# environment, `bash -O expand_aliases`, a `BASH_ENV` file — none of which
# this scan can read, so "the alias is inert" is not something it may assume.
_SHOPT_FUNCTION = re.compile(
    r"(?<![\w-])shopt[ \t]*\([ \t]*\)|\bfunction[ \t]+shopt\b")
# A word that is a leading `VAR=value` of the command, or a keyword after
# which the next word is still the command: both are skipped to reach the verb.
_SHOPT_SKIPPABLE = frozenset({"then", "do", "else", "!", "time", "{", "if",
                              "elif", "while", "until"})


def _unquoted_commands(raw: str) -> list[str]:
    """RAW, one line of a step, split where BASH would see a separator, with
    quotes removed inside each piece and a word-initial `#` ending the line.

    Written as a tokenizer rather than a regex bridge because the two are not
    the same reader, and the difference is a gate hole in both directions. A
    regex over the quote-STRIPPED line cannot tell a real separator from one
    inside a string, so `alias gs='git status | head' shopt=:` — which bash
    5.2 confirms does shadow `shopt` — reads as two commands and is missed,
    while `rm -rf build  # enable -n shopt if you must` reads as one and is
    blamed for a command that exists only in its comment.
    """
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if quote:
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < n:
                buf.append(raw[i + 1])
                i += 1
            else:
                buf.append(ch)
        elif ch in "\"'":
            quote = ch
        elif ch == "\\":
            if i + 1 < n:
                buf.append(raw[i + 1])
            i += 1
        elif ch == "#" and (not buf or buf[-1] in " \t"):
            break
        elif ch in ";&|()":
            out.append("".join(buf))
            del buf[:]
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    return out


def _shopt_shadow(raw: str) -> str:
    """How RAW, one line of a step read with its quotes still on, takes
    `shopt` away from the builtin, or `""` when it does not.

    The function spelling is matched wherever it sits, on the quote-stripped
    line, as it always has been: a definition that never runs is counted too,
    the loud direction. The `alias` and `enable` spellings are read at the
    VERB of a command instead, because they act only when they run — an
    `alias` word inside `echo`'s arguments or a loop's word list shadows
    nothing, and blaming it would refuse a step for a word it printed.

    THE LIMIT OF READING THE VERB, stated rather than implied. A shadow
    installed INDIRECTLY is unseen: `eval "alias shopt=:"`, `eval "enable -n
    shopt"`, and an alias set by a `. ./setup.sh` or `source` of a file this
    scan never opens. The verb on those lines is `eval` or `.`, not `alias` or
    `enable`, and the shadow they install is in text this function is not
    looking at. (The FUNCTION spelling escapes this, because it is matched
    anywhere on the line — `eval "shopt() { :; }"` is caught.)

    THE DIRECTION IS SILENT. Unseen, the scan honours a later `shopt -s
    extglob` that bash never ran, so a `!(` after it is READ as an extglob
    pattern on a state bash did not reach, instead of raising the
    `ExtglobUndecidable` that exists to refuse exactly that guess.

    Measured on bash 5.2, each followed by `builtin shopt extglob` against a
    bare `shopt -s extglob` control that reports on: the eval'd alias reports
    off; `eval "enable -n shopt"` removes the builtin outright; and a sourced
    file reports off when it defines a `shopt` function, when it turns
    expand_aliases on and then aliases `shopt`, and when it runs `enable -n
    shopt`. A sourced file holding a BARE `alias shopt=:` and nothing else is
    not a shadow — bash expands no aliases in a non-interactive shell, and
    that spelling still reports on. What the scan cannot see is the same
    either way; what the measurement settles is which of them it is wrong
    about. Same class as the eval/xargs/bash -c limit `_shell_text` already
    states for invocations.

    It is left open on purpose. Treating every `eval`, `source` and `.` as
    making the shopt state unknown from there on would refuse any workflow
    that sources a setup file — an over-refusal, which is the louder failure
    and the one that trains people to route around a scanner. The residual is
    pinned by a cell per STEP SPELLING — four of them, two `eval` shapes and
    the `.` and `source` verbs — and a count test beside them. The sourced
    shapes share their cells because the step text is identical whatever the
    file contains; what the contents change is the measurement above, not
    what the scan sees. So closing this is a deliberate edit, and widening it
    cannot happen in silence."""
    if _SHOPT_FUNCTION.search(_QUOTING.sub("", raw)):
        return "defines a `shopt` function, which shadows the builtin"
    for command in _unquoted_commands(raw):
        words = command.split()
        while words and (words[0] in _SHOPT_SKIPPABLE
                         or (_ASSIGNMENT.match(words[0])
                             and not words[0].startswith("shopt="))):
            words.pop(0)
        if not words:
            continue
        verb, rest = words[0], words[1:]
        if verb == "alias" and any(w.startswith("shopt=") for w in rest):
            return ("aliases `shopt`, which shadows the builtin wherever "
                    "alias expansion is on")
        if verb == "enable" and "shopt" in rest and any(
                w.startswith("-") and "n" in w[1:] for w in rest):
            return "runs `enable -n shopt`, which turns the builtin off altogether"
    return ""


# A step naming $GITHUB_ENV can append to the environment of every LATER step
# in its job, `BASHOPTS=extglob` or `BASH_ENV=...` included; what it appends
# cannot be read in general, so any mention is the question.
_GITHUB_ENV = re.compile(r"\bGITHUB_ENV\b")


class ExtglobUndecidable(DeclareError):
    """Whether extglob is on where bash parses a `!(` cannot be read from the
    step. Off, `!(cmd)` is a negated subshell; on, an extglob pattern; the
    two put the next word in different positions, so neither reading may be
    guessed. Exit 2, never a verdict."""

# Keywords after which the next word is still in COMMAND position.
_COMMAND_POSITION_KEYWORDS = frozenset({"then", "do", "else", "{", "!", "time",
                                        "if", "elif", "while", "until"})


def _in_command_position(s: str, i: int,
                         arm_closes: frozenset[int] | set[int] = frozenset()
                         ) -> bool:
    """Whether the word at `i` sits where bash reads a keyword: at the text's
    start or after a separator (`;`, `&`, `|`, `(`, a newline), with only
    blanks and `\\`-newline continuations between, straight after a `)` the
    scan read as closing a bare case arm or a PLAIN `(` (`arm_closes`: a
    `(linux)` pattern, a `name()` definition, or a subshell, after whose `)`
    bash takes only an operator, a redirect or a terminator such as `esac`,
    and rejects a bare word — bash 5.2 -n), or after a
    keyword that takes a command (`then`, `do`, `else`, `if`, `elif`,
    `while`, `until`, `{`, `!`, `time`). Anywhere else a word is an ARGUMENT —
    `echo case`, `pytest -k case`, and after the `)` of `<( )`, `>( )`, an
    array `=( )`, an extglob `@( )` or `$(( ))` — and bash runs it as one
    (measured on bash 3.2 and 5.2)."""
    if i > 0 and s[i - 1] not in " \t\n;&|(" and i - 1 not in arm_closes:
        return False
    j = i
    while j > 0:
        if s[j - 1] in " \t":
            j -= 1
        elif s[j - 1] == "\n" and j > 1 and s[j - 2] == "\\":
            j -= 2
        else:
            break
    if j == 0 or s[j - 1] in "\n;&|(" or j - 1 in arm_closes:
        return True
    k = j
    while k > 0 and s[k - 1] not in " \t\n;&|()":
        k -= 1
    return (s[k:j] in _COMMAND_POSITION_KEYWORDS
            and _in_command_position(s, k, arm_closes))


# The characters a backslash escapes inside double quotes (bash: `\$`, `` \` ``,
# `\"`, `\\`, `\`-newline); any other `\x` pair is two literal characters,
# which is the same thing to a scan that drops the span.
_ANSI_C = "$'"


def _heredoc_delimiter(text: str, i: int) -> tuple[str, int, bool]:
    """(the delimiter, the index after it, whether it was quoted), read at
    `i` on the raw text — raw because the quote stripper would have eaten
    the quotes of `<<'EOF'` and with them the fact that there was a
    delimiter at all. A quoted word ends at its closing quote; a bare word
    at whitespace or an operator, with a backslash quoting the character
    after it (`<<\\EOF`, which bash also treats as a quoted delimiter)."""
    n = len(text)
    if i < n and text[i] in "\"'":
        end = text.find(text[i], i + 1)
        if end < 0:
            return text[i + 1:].split("\n", 1)[0], n, True
        return text[i + 1:end], end + 1, True
    word: list[str] = []
    quoted = False
    while i < n and text[i] not in _HEREDOC_WORD_END:
        if text[i] == "\\" and i + 1 < n:
            quoted = True
            i += 1
        word.append(text[i])
        i += 1
    return "".join(word), i, quoted


class _ShellScan:
    """One pass over a `run:` step, carrying bash's lexical state across
    newlines — see `_shell_text`'s docstring for what is modelled and what
    is not. Everything a shell would not execute as a command word is dropped
    or replaced by a space: comments, quoted spans, heredoc bodies."""

    def __init__(self, run: str, extglob_unknown: str | None = None) -> None:
        self.s = run
        self.n = len(run)
        self.i = 0
        self.out: list[str] = []
        self.quote: str | None = None      # "'", '"', "$'" or None
        # one entry per open `$( )`: the quote outside it, the bare-paren
        # depth and the `case` depth inside it (a `)` closing either is not
        # the substitution's closer, and `case` counts only in command
        # position), and how
        # many heredocs were pending when it opened (those opened inside it
        # are EMPTY if it closes on the opener's own line)
        self.ctx: list[dict] = []
        self.arith = 0                     # depth inside `(( ... ))`
        # indices of the `)`s read as closing a bare case arm or a plain `(`:
        # a command position follows each of them
        self.arm_closes: set[int] = set()
        # the open bare parens OUTSIDE any `$( )`, the top level's half of
        # `_parens`: True for one a command follows, False for `<( )`, `>( )`,
        # an array `=( )` or an extglob
        self.parens: list[bool] = []
        # heredocs opened on the current line, in body order:
        # (delimiter, strips-leading-tabs, quoted-delimiter)
        self.pending: list[tuple[str, bool, bool]] = []
        # extglob as the scan can PROVE it, from top-level lines already
        # ended (`_end_line`): False, bash's default; True or False after an
        # honoured `shopt` line; None once anything else names it, with
        # `extglob_why` saying what. `extglob_unknown` is a reason from
        # outside the text: the step's environment or shell.
        self.extglob: bool | None = False
        self.extglob_why = ""
        self.extglob_unknown = extglob_unknown
        # set once a `shopt` function has been defined: a later `shopt` line
        # calls it, so it is never honoured from there on
        self.shopt_shadowed = ""
        # top-level structure, outside any `$( )`: the open blocks (`if`,
        # loops, `case`, `{`, `(`), and for the current top-level line where
        # it starts in `out` and in the raw text, the block depth it starts
        # at, and whether the line before it ended in `&&`, `||` or `|`
        self.frames: list[str] = []
        self.line_start = 0
        self.raw_line_start = 0
        self.line_depth = 0
        self.line_continues = False

    def text(self) -> str:
        while self.i < self.n:
            if self.quote:
                self._quoted()
            elif self.arith:
                self._arithmetic()
            else:
                self._command()
        return "".join(self.out)

    # -- inside a quoted span: dropped, but its END must be found correctly --
    def _quoted(self) -> None:
        s, i, q = self.s, self.i, self.quote
        ch = s[i]
        if q == "'":
            if ch == "'":
                self.quote = None
            self.i += 1
        elif q == _ANSI_C:
            if ch == "\\":
                self.i += 2               # `\'` does not end the span
            elif ch == "'":
                self.quote = None
                self.i += 1
            else:
                self.i += 1
        else:                             # double quotes
            if ch == "\\":
                self.i += 2               # `\"`, `\\`, `\$`, `\`-newline
            elif ch == "`":
                # a backtick substitution runs inside double quotes exactly as
                # `$( )` does, and its own `"` does not close the outer span
                self.out.append(" ")
                self._backtick()
            elif ch == '"':
                self.quote = None
                self.i += 1
            elif s.startswith("$(", i) and not s.startswith("$((", i):
                # a substitution opens a fresh, UNQUOTED context — the
                # `--body "$(cat <<EOF ... EOF)"` idiom lives here
                self._open_substitution()
                self.out.append(" $(")
                self.i += 2
            else:
                self.i += 1

    # -- inside `(( ))`: kept as text, but `<<` is the shift operator --------
    def _arithmetic(self) -> None:
        s, i = self.s, self.i
        if s.startswith("))", i):
            self.arith -= 1
            self.out.append("))")
            self.i += 2
        elif s.startswith("((", i):
            self.arith += 1
            self.out.append("((")
            self.i += 2
        else:
            self.out.append(s[i])
            self.i += 1

    # -- command text ----------------------------------------------------------
    def _command(self) -> None:
        s, i = self.s, self.i
        ch = s[i]
        if ch == "\\":
            if i + 1 < self.n and s[i + 1] == "\n":
                self.out.append(" ")      # a continuation joins the lines
            elif i + 1 < self.n:
                self.out.append(s[i + 1])  # `\x` is a literal x
            self.i += 2
            return
        if ch == "\n":
            top_level = not self.ctx
            if top_level:
                self._end_line()
            self.out.append("\n")
            self.i += 1
            if self.pending:
                self._bodies()
            if top_level:
                self.line_start = len(self.out)
                self.raw_line_start = self.i
            return
        if ch == "#" and (i == 0 or s[i - 1] in " \t\n;&|("):
            end = s.find("\n", i)
            self.i = self.n if end < 0 else end   # the newline is handled above
            return
        if ch == "`":
            self._backtick()
            return
        if s.startswith(_ANSI_C, i):
            self.quote = _ANSI_C
            self.out.append(" ")
            self.i += 2
            return
        if ch in "\"'":
            self.quote = ch
            self.out.append(" ")          # a placeholder, so words do not fuse
            self.i += 1
            return
        if s.startswith("$((", i):
            self.arith = 1                # arithmetic, whatever line it ends on
            self.out.append("$((")
            self.i += 3
            return
        if s.startswith("((", i):
            self.arith = 1
            self.out.append("((")
            self.i += 2
            return
        if s.startswith("$(", i):
            self._open_substitution()
            self.out.append("$(")
            self.i += 2
            return
        if not self.ctx:
            self._track_structure(s, i, ch)
        if ch == "(":
            # True for a plain `(`; False when it opens `<( )`, `>( )`, an
            # array `=( )` or an extglob, after whose `)` a word is an argument.
            # `!(` is an extglob only with extglob on; off, it is a negated
            # subshell, after whose `)` `esac` ends the case (bash 5.2).
            #
            # `prev` is a NEWLINE at the text's start, not the empty string:
            # `"" not in "<>=@!+*?"` is False in Python, so a step whose first
            # character is `(` would read its own subshell as an extglob.
            prev = s[i - 1] if i else "\n"
            self._parens().append(
                prev not in "<>=@!+*?"
                or (prev == "!" and self._bang_is_a_subshell()))
            self.out.append("(")
            self.i += 1
            return
        if ch == ")":
            parens = self._parens()
            # A `)` that ends a group bash takes a COMMAND after — a subshell,
            # a `name()` header, a case arm's pattern — is marked for
            # `_in_command_position`, and a `;` goes out with it so `_commands`
            # ends the command there too. Without that `;` the `esac` of
            # `a) (echo L) esac`, a last arm bash 5.2 runs, fuses into the
            # arm's text, `_scope_runs` never closes the `case`, and every
            # `warden verify` after it reads as conditional.
            if parens:
                command_follows = bool(parens.pop())
            elif self._case_depth():
                command_follows = True
            else:
                command_follows = False
                if self.ctx:
                    self._close_substitution()
            if command_follows:
                self.arm_closes.add(i)
            self.out.append(");" if command_follows else ")")
            self.i += 1
            return
        if self.ctx and s.startswith(("case", "esac"), i) and (
                i + 4 >= self.n or s[i + 4] in " \t\n;&|)") and (
                _in_command_position(s, i, self.arm_closes)):
            delta = 1 if s.startswith("case", i) else -1
            self.ctx[-1]["case"] = max(0, self.ctx[-1]["case"] + delta)
        if s.startswith("<<", i):
            if s.startswith("<<<", i) or s.startswith("<<=", i):
                self.out.append(s[i:i + 3])   # here-string, shift-assign
                self.i += 3
                return
            j = i + 2
            dash = j < self.n and s[j] == "-"
            if dash:
                j += 1
            while j < self.n and s[j] in " \t":
                j += 1
            delimiter, j, quoted = _heredoc_delimiter(s, j)
            if delimiter:
                self.pending.append((delimiter, dash, quoted))
            self.out.append(" ")
            self.i = j
            return
        self.out.append(ch)
        self.i += 1

    def _track_structure(self, s: str, i: int, ch: str) -> None:
        """Keep `frames`, the top-level blocks open at `i`, for `_end_line`.
        Errs toward MORE depth, which only ever makes a `shopt` line
        unhonoured: an opener counts as a bare word anywhere, a closer only in
        command position — which `arm_closes` reaches, so the `esac` after a
        top-level subshell or case arm pops as bash reads it — and only
        against its own opener, and inside a
        `case` a paren is not counted, since an arm's `)` would unbalance it."""
        frames = self.frames
        top = frames[-1] if frames else ""
        if ch == "(":
            if top != "case":
                frames.append("(")
        elif ch == ")":
            if top == "(":
                frames.pop()
        elif ch in "{}":
            if ((i == 0 or s[i - 1] in " \t\n;)")
                    and (i + 1 >= self.n or s[i + 1] in " \t\n;")):
                if ch == "{":
                    frames.append("{")
                elif top == "{":
                    frames.pop()
        elif ch.isalpha() and (i == 0 or s[i - 1] in " \t\n;&|()"):
            word = _WORD.match(s, i)
            end = word.end() if word else i
            if end < self.n and s[end] not in " \t\n;&|()":
                return
            name = s[i:end]
            if name in _BLOCK_OPENERS:
                frames.append(name)
            elif (name in _BLOCK_CLOSERS and _BLOCK_OPENERS.get(top) == name
                  and _in_command_position(s, i, self.arm_closes)):
                frames.pop()

    def _end_line(self) -> None:
        """End a top-level line for the extglob reading. Measured by running
        bash 5.2, not `bash -n`: a `shopt -s|-u extglob` governs how a later
        line parses only if it RAN before bash read that line, and none of
        these does, or none reliably: one inside `if`/`while`/`case`, a
        function body, a `( )` or `$( )`; after `&&` or `||`, even from the
        line above; as an argument (`echo shopt -s extglob`); on the same
        line as the `!(`, or inside the same multi-line block, which bash
        parses whole first; after a trailing `|&`, which makes it the last
        element of a pipeline and so a subshell; on a line inside a multi-line
        backtick substitution; or once `shopt` is no longer the builtin —
        shadowed by a function, by an alias, or turned off outright by
        `enable -n shopt`, all three measured. The scan honours one shape it
        can prove ran: the whole line is that `shopt`, at block depth 0, not
        continuing an operator, not inside a backtick substitution, with
        nothing above it shadowing `shopt`. Any other line naming extglob or
        BASHOPTS, quoted or in a comment included, leaves the state unknown
        from there on."""
        quoted = self.s[self.raw_line_start:self.i]
        raw = _QUOTING.sub("", quoted).strip()
        line = "".join(self.out[self.line_start:]).strip()
        if self.extglob is not None and _EXTGLOB_WORD.search(raw):
            shopt = _TOP_LEVEL_SHOPT.fullmatch(raw)
            if (shopt and self.line_depth == 0 and not self.line_continues
                    and not self.shopt_shadowed):
                self.extglob = shopt.group(1) == "s"
            else:
                self.extglob = None
                self.extglob_why = (
                    f"`{raw[:80]}` names extglob or BASHOPTS somewhere other "
                    "than an unconditional top-level `shopt -s|-u extglob` "
                    "line" + (f" ({self.shopt_shadowed})"
                              if self.shopt_shadowed else "")
                    + ", so whether extglob was on when bash parsed the "
                    "`!(` is unknown")
        # WITH its quotes, not `raw`: a `;` or `|` inside a string is not a
        # separator, and stripping the quotes first makes the two
        # indistinguishable in both directions (`_unquoted_commands`).
        shadow = "" if self.shopt_shadowed else _shopt_shadow(quoted)
        if shadow:
            self.shopt_shadowed = (
                f"`{raw[:80]}` {shadow} for every `shopt` line after it")
        if line:
            self.line_continues = line.endswith(("&&", "||", "|", "|&"))
        self.line_depth = len(self.frames)

    def _extglob_at_bang(self) -> bool:
        """Whether extglob is on where bash parses the `!(` at `self.i`.
        Raises `ExtglobUndecidable` when the scan cannot tell."""
        why = self.extglob_unknown or (
            self.extglob_why if self.extglob is None else "")
        here = _QUOTING.sub("", self.s[self.raw_line_start:self.i])
        if not why and _EXTGLOB_WORD.search(here):
            why = ("extglob or BASHOPTS is named on the line holding the `!(`, "
                   "which bash parses before running any of it")
        if why:
            raise ExtglobUndecidable(
                f"cannot tell whether `!(` is a negated subshell or an extglob: "
                f"{why}")
        return bool(self.extglob)

    def _parens(self) -> list[bool]:
        """The open bare-paren stack where the scan is: the innermost `$( )`
        context's, else the top level's. Kept per context because a `)` that
        closes a paren opened INSIDE a substitution is not the
        substitution's own closer."""
        return self.ctx[-1]["parens"] if self.ctx else self.parens

    def _bang_is_a_subshell(self) -> bool:
        """Whether the `!(` at `self.i` opens a NEGATED SUBSHELL, after whose
        `)` bash reads a command, rather than an extglob PATTERN, after whose
        `)` it reads an argument. The two readings differ in what this scan
        writes — a `)` a command follows is written `);` — so the answer is
        never guessed where it decides a verdict.

        Inside a `$( )` an unreadable extglob state is REFUSED, exit 2, as it
        always has been: there the answer also decides which `)` closes the
        substitution, and closing it in the wrong place swallows the rest of
        the step, `warden review` mention included, so the job stops being a
        gate job and D-03 reports not-applicable at exit 0. That is silence,
        and the refusal is what this module exists to put in its place.

        At the TOP LEVEL, where no substitution closer is at stake, an
        unreadable state takes the EXTGLOB reading instead of refusing. That
        choice is monotone rather than a guess: it emits no `;` and marks no
        command position, so it can only ever join commands and leave blocks
        open — hide an invocation, exit 1, loud — and never split a command
        or close a block early, which is what invents a run. Refusing here
        instead would cost a repository its whole `declare check`, exit 2
        over D-01..D-04, for writing the pair bash documents: `shopt -s
        extglob` then `rm -rf !(dist)`."""
        if self.ctx:
            return not self._extglob_at_bang()
        try:
            return not self._extglob_at_bang()
        except ExtglobUndecidable:
            return False

    def _case_depth(self) -> int:
        """Open `case` blocks where the scan is, so a bare arm's `)` is read
        as one. Inside `$( )` the context counts them; at the top level
        `_track_structure` already does, in `frames`."""
        return self.ctx[-1]["case"] if self.ctx else self.frames.count("case")

    def _backtick(self) -> None:
        """Consume a backtick substitution WHOLE, at `self.i`.

        Not a context like `$( )`, because bash does not read one that way.
        It finds the closing backtick FIRST, over the raw characters, and
        only then parses what it found as commands. Everything that would
        otherwise change where a span ends is therefore powerless inside
        one, each measured on bash 5.2:

        - a `#` comment does not reach the closer: `` echo "`true # c`" ``
          then `warden verify --scope x` then `"` echoes the verify line
          and runs nothing, and a THREE-LINE span whose middle line is a
          comment — `` echo "[`echo A `` / `# comment` / `` echo B`]" `` —
          prints `[A` and `B]` on two lines, so both the closer and the
          comment's line scope are bash's and not this reader's;
        - a heredoc opened inside a span is the span's own: `` X="`cat <<EOF
          `` / `body` / `` EOF`" `` sets X to `body` and the next line runs,
          and a heredoc whose terminator sits OUTSIDE the span is
          unterminated (bash warns and the body lines run as commands);
        - a quote does not protect a backtick: `` `echo 'a`b'` `` ends the
          span at the backtick inside the quotes, which is why bash rejects
          it;
        - a backslash does: `` `echo x\\`y` `` ends at the LAST backtick.

        So the span is scanned as its own text, with the extglob state it
        starts under carried in — a `shopt` line inside it still runs in the
        substitution, never in the main shell, and the outer `_end_line`
        sees the span's raw text and takes extglob unknown from there.
        A span with no closing backtick SWALLOWS the rest of the step, as an
        unterminated quote or heredoc does: bash would not parse the step at
        all, and hiding an invocation is the loud direction where reading one
        that never runs is the silent one.
        """
        j = self.i + 1
        while j < self.n:
            if self.s[j] == "\\":
                j += 2
                continue
            if self.s[j] == "`":
                break
            j += 1
        if j >= self.n:
            # No closing backtick: bash would not parse the step, so the rest
            # of it is DROPPED rather than read as top-level commands — the
            # same direction an unterminated quote or heredoc errs in, and
            # the safer one, since inventing a run is the silent failure.
            self.out.append(" ")
            self.i = self.n
            return
        span = _ShellScan(self.s[self.i + 1:j], self.extglob_unknown)
        span.extglob = self.extglob
        span.extglob_why = self.extglob_why
        span.shopt_shadowed = self.shopt_shadowed
        self.out.append("`")
        self.out.append(span.text())
        self.out.append("`")
        self.i = j + 1

    def _open_substitution(self) -> None:
        self.ctx.append({"quote": self.quote, "parens": [], "case": 0,
                         "pending_at": len(self.pending)})
        self.quote = None

    def _close_substitution(self) -> None:
        top = self.ctx.pop()
        self.quote = top["quote"]
        # a heredoc opened inside a substitution that closes on the opener's
        # own line has an EMPTY body: the lines after the `)` are commands
        # (bash: `X="$(cat <<EOF)"` then `echo ran` runs the echo)
        del self.pending[top["pending_at"]:]

    # -- heredoc bodies, consumed line by line until each terminator ----------
    def _bodies(self) -> None:
        s = self.s
        while self.pending:
            delimiter, dash, quoted = self.pending[0]
            if self.i >= self.n:
                break
            end = s.find("\n", self.i)
            end = self.n if end < 0 else end
            line = s[self.i:end]
            # an UNQUOTED delimiter's body is continued by `\`-newline — an
            # ODD run of trailing backslashes, since `\\` is a literal one —
            # which fuses the terminator into the line above it; bash reads
            # it so, and a quoted delimiter keeps every backslash literal
            while (not quoted and end < self.n
                   and (len(line) - len(line.rstrip("\\"))) % 2 == 1):
                nxt = s.find("\n", end + 1)
                nxt = self.n if nxt < 0 else nxt
                line = line[:-1] + s[end + 1:nxt]
                end = nxt
            start = self.i
            self.i = min(end + 1, self.n)
            body = line.lstrip("\t") if dash else line
            if body == delimiter:
                self.pending.pop(0)
                self.out.append("\n")
                continue
            # `EOF)` — the paren directly after the delimiter — terminates a
            # heredoc inside `$( )`, closes the substitution, and the REST of
            # that line is commands again: `EOF)"`, `EOF); next`, `EOF) | tr`
            # (measured on bash 3.2; `EOF )` with a space does not terminate)
            if self.ctx and body.startswith(delimiter + ")"):
                self.pending.pop(0)
                self._close_substitution()
                self.out.append(")")
                self.i = start + (len(line) - len(body)) + len(delimiter) + 1
                return


def _job_runs(doc: object) -> dict[str, list[str]]:
    """job id -> its steps' `run:` text, one entry per step.

    Kept apart rather than concatenated: a block depth
    this reader cannot balance inside one step must not swallow the next
    step's invocation, and a step is where a `run:` block's shell begins.
    """
    out: dict[str, list[str]] = {}
    if not isinstance(doc, dict):
        return out
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return out
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        text = []
        # a reason an EARLIER step of this job may have set extglob for the
        # steps after it, through $GITHUB_ENV
        env_written: str | None = None
        for n, step in enumerate(job.get("steps") or [], 1):
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                name = step.get("name") or f"#{n}"
                try:
                    text.append(_shell_text(
                        step["run"],
                        _extglob_from_outside(doc, job, step) or env_written))
                except ExtglobUndecidable as e:
                    raise ExtglobUndecidable(
                        f"job `{job_id}`, step `{name}`: {e}") from e
                if env_written is None and _GITHUB_ENV.search(step["run"]):
                    env_written = (
                        f"the earlier step `{name}` writes $GITHUB_ENV, which "
                        "can set BASHOPTS or BASH_ENV for every later step")
        out[str(job_id)] = text
    return out


def _extglob_from_outside(doc: dict, job: dict, step: dict) -> str | None:
    """A reason extglob may be on before a step's first line that its text
    does not show: `BASHOPTS` or `BASH_ENV` in the workflow, job or step
    `env` (or an `env` that is an expression, which cannot be read), or a
    `shell` (the step's, or a `defaults.run.shell`) that names extglob,
    BASHOPTS or BASH_ENV or passes `-O`. bash 5.2 starts with extglob on under
    `BASHOPTS=extglob` and under `-O extglob`, and sources a `BASH_ENV` file,
    which can run `shopt -s extglob`, before the script's first line.

    `_job_runs` adds the one reason that is not in a step's own YAML: an
    earlier `run:` step naming $GITHUB_ENV. NOT covered, stated as the limit
    it is: an earlier `uses:` step can export the same variables (an action's
    `core.exportVariable`), and nothing here reads what an action does."""
    for where, holder in (("workflow", doc), ("job", job), ("step", step)):
        env = holder.get("env")
        if isinstance(env, str):
            return f"the {where} `env` can set BASHOPTS"
        if isinstance(env, dict):
            for key in ("BASHOPTS", "BASH_ENV"):
                if key in env:
                    return f"the {where} `env` sets {key}"
            # An exported shell function reaches the step as
            # `BASH_FUNC_shopt%%`, and bash defines it before the first line:
            # `shopt -s extglob` then calls the function, so extglob stays
            # off. Measured on bash 5.2 — that spelling and no other.
            # `BASH_FUNC_shopt()` is the pre-2014 spelling, which bash 5.2
            # does NOT import (measured: `extglob on`); it is matched anyway,
            # because a runner on a bash that does import it would take the
            # shadow, and refusing a step is the loud direction.
            # Both are matched EXACTLY, not by prefix — a `BASH_FUNC_shopts%%`
            # exports a function named `shopts`, leaves `shopt` the builtin,
            # and bash 5.2 prints `extglob on` after it.
            for key in ("BASH_FUNC_shopt%%", "BASH_FUNC_shopt()"):
                if key in env:
                    return (f"the {where} `env` exports a `shopt` function as "
                            f"`{key}`, which shadows the builtin from the "
                            "first line")
        defaults = holder.get("defaults")
        run = defaults.get("run") if isinstance(defaults, dict) else None
        for shell in (holder.get("shell"),
                      run.get("shell") if isinstance(run, dict) else None):
            if isinstance(shell, str) and re.search(
                    r"(?:^|\s)-[A-Za-z]*O|extglob|BASHOPTS|BASH_ENV", shell):
                return f"the {where} shell `{shell}` can turn extglob on"
    return None


def _uses(step: dict, action: str) -> bool:
    return str(step.get("uses") or "").startswith(action + "@")


# The one take step D-03 counts: `warden take --from` and a path in the
# runner's temp directory made of plain segments, optionally in one pair of
# double quotes. Nothing a shell would expand, split, glob or run fits it.
_TEMP_SEGMENTS = r"(?P<segments>(?:/[A-Za-z0-9._-]+)+)"
_TAKE_RUN = re.compile(
    r'warden take --from (?P<q>"?)'
    r"(?:\$RUNNER_TEMP|\$\{RUNNER_TEMP\}|\$\{\{ *runner\.temp *\}\})"
    + _TEMP_SEGMENTS + r"(?P=q)")
# An action's `with.path` is not shell: only the expression expands there.
_DOWNLOAD_PATH = re.compile(r"\$\{\{ *runner\.temp *\}\}" + _TEMP_SEGMENTS)
_TAKE_STEP_KEYS = frozenset({"name", "id", "run", "shell", "timeout-minutes"})
_DOWNLOAD_KEYS = frozenset({"name", "path"})
# `warden review` as words: blanks, tabs and a joined line between them count.
# Anything may precede `warden`, as in the gate-job mention test: a review run
# by path (`/usr/local/bin/warden review`) is the review step, so a take after
# it cannot read as a take before the review.
_REVIEW_WORDS = re.compile(r"warden\s+review(?![\w.-])")
_RELATIVE_PATH = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*/?")


def _temp_segments(match: re.Match[str] | None) -> str | None:
    if match is None or ".." in match.group("segments").split("/"):
        return None
    return match.group("segments")


def _download_overlaps(path: object, segments: str) -> bool:
    """Whether a download-artifact `path` can write into the `${{ runner.temp }}`
    path `segments`: it is that path, above it or below it, or it is a path
    this does not read (anything but a relative path of plain segments or
    `${{ runner.temp }}` and plain segments). An absent `path` is the
    workspace."""
    if path is None:
        return False
    if not isinstance(path, str):
        return True
    path = path.strip()
    if _RELATIVE_PATH.fullmatch(path) and ".." not in path.split("/"):
        return False
    if re.fullmatch(r"\$\{\{ *runner\.temp *\}\}/?", path):
        return True
    other = _temp_segments(_DOWNLOAD_PATH.fullmatch(path.rstrip("/")))
    if other is None:
        return True
    a, b = other.split("/"), segments.split("/")
    return a[:len(b)] == b or b[:len(a)] == a


def _working_directory(doc: dict, job: dict, step: dict) -> object:
    """Where a `run:` step runs: its own `working-directory`, else the job's
    `defaults.run.working-directory`, else the workflow's; None for the
    workspace."""
    if "working-directory" in step:
        return step["working-directory"]
    for holder in (job, doc):
        defaults = holder.get("defaults")
        run = defaults.get("run") if isinstance(defaults, dict) else None
        if isinstance(run, dict) and "working-directory" in run:
            return run["working-directory"]
    return None


def _named_directory(directory: object) -> str:
    return "the workspace" if directory is None else f"`{directory}`"


def _results_prefix(directory: object) -> str | None:
    """The workspace-relative `.warden/out/` a step running in DIRECTORY
    writes, or None for a directory that is not a relative path of plain
    segments."""
    if directory is None:
        return ".warden/out/"
    if not isinstance(directory, str) or not _RELATIVE_PATH.fullmatch(directory.strip()):
        return None
    parts = [p for p in directory.strip().split("/") if p not in ("", ".")]
    if ".." in parts:
        return None
    return "/".join(parts + [".warden/out/"])


def _takes_from(doc: dict, job: dict, step: dict, download_at: int,
                review: dict) -> str | None:
    """None when `step` is the take `warden init` writes, reading the
    download at `download_at` in the job's steps; otherwise why it is not.

    All must hold: the step has no key but `name`, `id`, `run`, `shell` and
    `timeout-minutes`, so no `working-directory` of its own; its shell is
    `bash`, named on the step (a job or workflow default is not read, and a
    Windows runner's default is pwsh); neither the job nor the workflow has an
    `env` (bash reads `BASH_ENV` and `BASH_FUNC_*` from it); the take and the
    review step run in the same effective working directory
    (`_working_directory`), which is where `.warden/out` is written and read,
    so a subdirectory gate's job default counts; `run`, stripped, is exactly `_TAKE_RUN`
    with no `..` segment; the download sets only `name` and `path` under
    `with:` (not `run-id`, `repository` or `github-token`, which fetch another
    run's artifact), and its path is the take's; and no other download step
    can write into that path."""
    steps = job.get("steps") or []
    spec = steps[download_at].get("with") or {}
    name = spec.get("name")
    extra = sorted(set(step) - _TAKE_STEP_KEYS)
    if extra:
        return (f"the take step sets {', '.join(f'`{k}`' for k in extra)}; only "
                "`name`, `id`, `run`, `shell` and `timeout-minutes` may be set")
    for where, holder in (("job", job), ("workflow", doc)):
        if holder.get("env") is not None:
            return (f"the {where} has an `env`, which bash reads `BASH_ENV` and "
                    "`BASH_FUNC_*` from; set it on the steps that need it")
    shell = step.get("shell")
    if shell is None:
        return "the take step has no `shell: bash`"
    if shell != "bash":
        return f"the take step's shell is `{shell}`, not `bash`"
    here, there = _working_directory(doc, job, step), _working_directory(doc, job, review)
    if here != there:
        return (f"the `warden review` step runs in {_named_directory(there)} and the "
                f"take step in {_named_directory(here)}")
    source = _temp_segments(_TAKE_RUN.fullmatch(str(step.get("run")).strip()))
    if source is None:
        return ("the take step's `run` is not `warden take --from` and one "
                "`$RUNNER_TEMP` path of plain segments")
    extra = sorted(set(spec) - _DOWNLOAD_KEYS)
    if extra:
        return (f"the `{name}` download sets {', '.join(f'`{k}`' for k in extra)} "
                "under `with:`; only `name` and `path` may be set")
    path = spec.get("path")
    written = _temp_segments(_DOWNLOAD_PATH.fullmatch(path.strip())) \
        if isinstance(path, str) else None
    if written != source:
        return (f"the take reads `${{{{ runner.temp }}}}{source}` but the `{name}` "
                f"download writes to `{path}`")
    for i, other in enumerate(steps):
        if (i != download_at and isinstance(other, dict)
                and _uses(other, "actions/download-artifact")
                and _download_overlaps((other.get("with") or {}).get("path"), source)):
            return "another download-artifact step can write into the take path"
    return None


def _taken_scopes(doc: dict, job_id: str,
                  job_runs: dict[str, list[str]]) -> tuple[set[str], str | None]:
    """The scopes a gate job TAKES from a job it `needs`, read from structure,
    and why the results were not taken (None when nothing was refused): that
    job runs the scope in a step BEFORE an upload whose `path` names
    `verify-result.json` under the `.warden/out/` of the working directory the
    step ran in (`_working_directory`), which is also the directory the gate
    job's take and review run in; the gate job downloads that artifact
    by name (the first download of a name is the one read); and after the
    download and before the `warden review` step sits the take in its exact
    form (`_takes_from`). Not proved: that no earlier step shadows `warden`
    on PATH (the same holds for `warden review`) or runs review in a spelling
    whose text does not read `warden review` (`"warden" review`), an `if:` on
    any job or step other than the take step, or that the upload glob matches
    what verify wrote. Nor what other steps before review do: one can plant
    results in the download directory or set `BASH_ENV` through
    `$GITHUB_ENV` before the take, or rewrite `.warden/out` after it;
    `warden review` has the same exposure."""
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    job = jobs.get(job_id) if isinstance(jobs, dict) else None
    if not isinstance(job, dict):
        return set(), None
    needs = job.get("needs")
    needs = [needs] if isinstance(needs, str) else needs if isinstance(needs, list) else []
    texts = iter(job_runs.get(job_id, []))
    review_at: int | None = None
    review: dict = {}
    downloads: dict[str, int] = {}
    runs: list[tuple[int, dict]] = []
    for i, step in enumerate(job.get("steps") or []):
        if not isinstance(step, dict):
            continue
        if isinstance(step.get("run"), str):
            if review_at is None and _REVIEW_WORDS.search(next(texts, "")):
                review_at, review = i, step
            runs.append((i, step))
        elif _uses(step, "actions/download-artifact"):
            spec = step.get("with") or {}
            name = spec.get("name") if isinstance(spec, dict) else None
            if isinstance(name, str) and name not in downloads:
                downloads[name] = i
    if review_at is None:
        return set(), None
    review_prefix = _results_prefix(_working_directory(doc, job, review))
    why: list[str] = []
    upload_why: list[str] = []

    def taken_into_out(name: str) -> bool:
        at = downloads[name]
        for i, step in runs:
            is_take = str(step["run"]).strip().startswith("warden take")
            if not at < i < review_at:
                if is_take:
                    why.append(f"the take step is not after the `{name}` download "
                               "and before `warden review`")
                continue
            reason = _takes_from(doc, job, step, at, review)
            if reason is None:
                return True
            if is_take:
                why.append(reason)
        return False

    taken: set[str] = set()
    for need in needs:
        other = jobs.get(need) if isinstance(need, str) else None
        if need == job_id or not isinstance(other, dict):
            continue
        ran: list[tuple[set[str], str | None, object]] = []
        other_texts = iter(job_runs.get(need, []))
        for step in other.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if isinstance(step.get("run"), str):
                scopes = _scope_runs(next(other_texts, ""))
                if scopes:
                    directory = _working_directory(doc, other, step)
                    ran.append((scopes, _results_prefix(directory), directory))
                continue
            if not _uses(step, "actions/upload-artifact"):
                continue
            spec = step.get("with") or {}
            name, path = spec.get("name"), spec.get("path")
            if not (isinstance(name, str) and name in downloads and isinstance(path, str)
                    and "verify-result.json" in path):
                continue
            uploaded: set[str] = set()
            elsewhere = False
            for scopes, prefix, directory in ran:
                if prefix is not None and path.strip().startswith(prefix):
                    # Review pairs a result by commit and scope name alone, so
                    # one verified in another directory would read as its own.
                    if prefix == review_prefix:
                        uploaded |= scopes
                    else:
                        elsewhere = True
                        upload_why.append(
                            f"job `{need}` runs `warden verify` in "
                            f"{_named_directory(directory)} and the gate takes and "
                            "reviews in "
                            f"{_named_directory(_working_directory(doc, job, review))}")
                elif prefix is None:
                    upload_why.append(
                        f"job `{need}` runs `warden verify` in {_named_directory(directory)}, "
                        "which is not a relative path of plain segments")
                else:
                    upload_why.append(
                        f"the `{name}` upload path `{path.strip()}` is not under "
                        f"`{prefix}`, where job `{need}` runs `warden verify`")
            if uploaded and taken_into_out(name):
                taken |= uploaded
            elif elsewhere:
                # Credits nothing: read only so a take step that does not
                # count names its own reason, which is the more exact one.
                taken_into_out(name)
    if taken:
        return taken, None
    if why:
        return taken, (f"A `warden take` step is present but does not count: {why[0]}. "
                       "Use the one-line form `warden init` writes (the D-03 row of "
                       "the CLI-Reference wiki page)")
    return taken, (f"The verify results are not taken: {upload_why[0]}"
                   if upload_why else None)


def _refuse_lost_gate(root: Path) -> None:
    """Raise when no credited job runs `warden review` and a root workflow that
    does was skipped although its own `name:` does not name the enrollment the
    skip gave it to. A tracked `ci/repo.yaml` takes a root
    gate hand-named `warden-ci.yml` on its file name alone; with it gone D-03
    has nothing to read, and not-applicable there would print clean over a
    check that read nothing. A skip whose `name:` agrees keeps not-applicable:
    that file says it is another enrollment's gate."""
    lost: list[str] = []
    for path, why in gate_workflows.disowned_gates(root, ("*.y*ml",)):
        try:
            doc = yamlio.load(path.read_text())
        except (OSError, yaml.YAMLError) as e:
            raise DeclareError(f"{path}: workflow could not be parsed ({e}) — it "
                               "was skipped as another enrollment's gate and its "
                               "`name:` does not say so, so D-03 cannot tell "
                               "whether it is the root's") from e
        try:
            job_runs = _job_runs(doc)
        except ExtglobUndecidable as e:
            raise ExtglobUndecidable(
                f"{path}: {e}. D-03 renders no verdict from a guess") from e
        if any("warden review" in "\n".join(steps) for steps in job_runs.values()):
            name = doc.get("name") if isinstance(doc, dict) else None
            claim = (f"is `{name}`" if isinstance(name, str)
                     else "is not a string" if name is not None else "is absent")
            lost.append(f"the root `{path.name}` runs `warden review` and was "
                        f"skipped {why}, but its top-level `name:` {claim}")
    if lost:
        raise DeclareError(
            "D-03 has no gate job to read: " + "; ".join(lost) + ". A skipped "
            "file whose `name:` does not name the enrollment it was skipped for "
            "may be the root's own gate, so no verdict is rendered. If it is "
            "that enrollment's gate, set its `name:` to `warden (<path>)`; if it "
            "is the root's, give it a file name no enrollment below the root "
            "is named for")


def check_scope_runners(root: Path, config: config_mod.RepoConfig) -> CheckResult:
    """Every declared verify scope runs in the job that runs `warden review`,
    or in a job whose verify results that job takes (`_taken_scopes`).

    Not a style point: `warden audit` pairs a verify result to the reviewed
    commit by SHA out of the runner's own filesystem. A scope verified in a
    DIFFERENT job, with nothing carrying its result across, is
    invisible to the sticky comment, which then renders
    `**verify --scope X**: NO VERIFY RESULT for the reviewed commit` — a true
    line about a real gap that the PR author cannot clear by doing anything
    right.
    """
    # Below the git root, the root gate that names this enrollment.
    workflows = gate_workflows.enrollment_workflows(root, ("*.y*ml",))
    # A gate skipped as another enrollment's is named wherever this renders
    # not-applicable, so a skip never reads as a repository with no gate.
    skipped = gate_workflows.skipped_gates(root, ("*.y*ml",))
    named = "" if not skipped else " (" + "; ".join(
        f"the root `{p.name}` was skipped {why}" for p, why in skipped) + ")"
    if not workflows and not (root / WORKFLOWS_DIR).is_dir():
        return CheckResult("D-03", "CI runs every declared verify scope",
                           SKIPPED, f"no {WORKFLOWS_DIR} — nothing to pair "
                           f"verify evidence with{named}")
    findings: list[Finding] = []
    gate_jobs: list[str] = []
    for path in workflows:
        try:
            doc = yamlio.load(path.read_text())
        except (OSError, yaml.YAMLError) as e:
            raise DeclareError(f"{path}: workflow could not be parsed ({e}) — "
                               "a gate job this check cannot read is not a gate "
                               "job it may assume is complete")
        try:
            job_runs = _job_runs(doc)
        except ExtglobUndecidable as e:
            raise ExtglobUndecidable(
                f"{path}: {e}. D-03 renders no verdict from a guess") from e
        for job_id, steps in job_runs.items():
            text = "\n".join(steps)
            # A MENTION test on purpose, where `_scope_runs` below is not:
            # the two questions fail closed in opposite directions. This
            # repo's own gate job runs `warden review` inside an `if` (fork
            # PRs take the `--no-comment` arm), and reading that as "no gate
            # job here" would drop every scope obligation in silence, while
            # counting one extra job as a gate job only ever asks for more
            # evidence.
            if "warden review" not in text:
                continue
            gate_jobs.append(f"{path.name}:{job_id}")
            ran = set().union(*(_scope_runs(step) for step in steps)) \
                if steps else set()
            taken, why = _taken_scopes(doc, job_id, job_runs)
            ran |= taken
            cause = "" if why is None else f". {why}"
            for scope in sorted(set(config.verify) - ran):
                findings.append(Finding(
                    f"{path.name}:{job_id}",
                    f"runs `warden review` but never `warden verify --scope "
                    f"{scope}`, and takes no verify result for it from a job "
                    "it needs, so a diff requiring that scope renders NO "
                    "VERIFY RESULT — verify evidence pairs inside ONE job's "
                    f"filesystem{cause}"))
    if not gate_jobs:
        _refuse_lost_gate(root)
        return CheckResult("D-03", "CI runs every declared verify scope",
                           SKIPPED, "no workflow job runs `warden review`, so "
                           f"no job owes paired verify evidence{named}")
    if findings:
        return CheckResult("D-03", "CI runs every declared verify scope", DRIFT,
                           f"{len(findings)} scope(s) unrun in a gate job",
                           tuple(findings))
    return CheckResult("D-03", "CI runs every declared verify scope", CLEAN,
                       f"{', '.join(gate_jobs)} runs or takes every declared scope "
                       f"({', '.join(sorted(config.verify))})")


# --- D-04 ---------------------------------------------------------------------

def policy_gate_paths(root: Path) -> tuple[str, ...]:
    """The backticked paths in the FIRST paragraph of the policy's
    `## Forbidden paths` — the Never-edit list. The carve-out prose below it
    names paths that are illustrations, not fence entries, which is why the
    paragraph and not the section is read (the same reading
    tests/test_self_cage.py applies).

    FAIL-CLOSED on a policy it cannot read. An ABSENT policy file declares no
    gate surface and returns `()`; a policy file that is THERE and whose
    Never-edit paragraph this regex cannot find raises, because the two states
    are not the same one. Returning `()` for both would let a reworded heading
    — `## Forbidden paths (the fence)` — silently narrow D-04 to the
    `protected_paths` entries while it reported `✓ clean` at exit 0: the check
    losing most of its inputs and saying nothing, which is the class it exists
    to refuse.
    """
    policy = root / POLICY_PATH
    if not policy.is_file():
        return ()   # nothing declared here — distinct from "cannot read it"
    try:
        text = policy.read_text()
    except (OSError, UnicodeDecodeError) as e:
        raise DeclareError(
            f"{POLICY_PATH} is present but could not be read ({e}), so the "
            "gate-surface paths it declares cannot be checked — refused rather "
            "than read as declaring none") from e
    m = re.search(r"^## Forbidden paths\s*$\n+(.*?)(?:\n\s*\n|\Z)",
                  text, re.S | re.M)
    if not m:
        raise DeclareError(
            f"{POLICY_PATH} is present but has no `## Forbidden paths` section "
            "this can read, so every path it declares would silently leave "
            "D-04's input set — refused rather than reported clean over a "
            "partial surface. Rewording the heading is how that happens")
    # A paragraph that IS read and names no backticked path declares no fenced
    # path, which is a real and documented state: `docs/wiki/Skills-Policy.md`
    # shows `none`, and `examples/hello-svc` writes exactly that. Folding it
    # into the refusal above would make `warden declare check` exit 2 on the
    # shipped example consumer with nothing they could do about it.
    # Read-and-empty and cannot-read are different states and are answered
    # differently.
    return tuple(re.findall(r"`([^`]+)`", m.group(1)))


def check_gate_surface_tiers(root: Path,
                             config: config_mod.RepoConfig) -> CheckResult:
    """Every declared gate-surface path carries an explicit risk tier.

    A directory such as `.cage/`, declaring the fences and the prompt of an
    unattended runner, that no `risk_tiers` glob matches is reported LOW by
    `warden explain` — the tier a docs page gets. An explicit glob
    IS the exemption, including an explicit `tier: LOW`: what is refused is the
    unmatched DEFAULT, because a default is not a statement.
    """
    surfaces: list[tuple[str, str]] = [
        (p.path, "repo.yaml protected_paths") for p in config.protected_paths]
    surfaces += [(p, "the policy's Never-edit paragraph")
                 for p in policy_gate_paths(root)]
    if not surfaces:
        return CheckResult("D-04", "every gate-surface path is tiered", SKIPPED,
                           "no protected_paths and no policy Never-edit "
                           "paragraph — nothing declares a gate surface")
    findings: list[Finding] = []
    seen: set[str] = set()
    for path, source in surfaces:
        if path in seen:
            continue
        seen.add(path)
        probe = path + "probe" if path.endswith("/") else path
        tier = config_mod.classify(config, probe)
        if tier.glob == "<default>":
            findings.append(Finding(
                source,
                f"`{path}` is gate surface and classifies by the UNMATCHED "
                f"tier default ({tier.tier}). Declare a `risk_tiers` glob for "
                "it — an explicit tier, even LOW, is a statement; the default "
                "is the absence of one"))
    if findings:
        return CheckResult("D-04", "every gate-surface path is tiered", DRIFT,
                           f"{len(findings)} of {len(seen)} gate-surface "
                           "path(s) untiered", tuple(findings))
    return CheckResult("D-04", "every gate-surface path is tiered", CLEAN,
                       f"{len(seen)} declared gate-surface path(s), each "
                       "matched by a risk_tiers glob")


# --- D-05 ---------------------------------------------------------------------

# Bounded, as `github.DEFAULT_TIMEOUT` bounds the REST calls: a stalled `gh`
# must turn into UNREADABLE, not a hung command.
GH_TIMEOUT = 30
D05_LABEL = "the forge does not allow the auto-merge the delegation excludes"


def _gh(root: Path, *args: str) -> tuple[bool, str]:
    """(ok, stdout) of one `gh api` call run in ROOT, or (False, why).
    `{owner}/{repo}` in a path is `gh`'s own placeholder, and `gh` resolves
    it by its own precedence — `GH_REPO`, then `gh repo set-default`, then the
    remotes (upstream before origin) — not necessarily the repository the
    tree belongs to. So the caller reads `.full_name` back and names it in
    PASS, REFUSED and an UNREADABLE whose answer carried it. A call that
    FAILS names only the path `gh` was asked, because `gh` says nothing of
    which repository it resolved; that verdict is UNREADABLE, never clean."""
    try:
        proc = subprocess.run(["gh", "api", *args], cwd=root,
                              capture_output=True, text=True,
                              timeout=GH_TIMEOUT)
    except FileNotFoundError:
        return False, "`gh` is not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"`gh api` did not answer within {GH_TIMEOUT}s"
    if proc.returncode != 0:
        why = (proc.stderr.strip() or proc.stdout.strip()
               or f"exit {proc.returncode}").splitlines()[0]
        return False, f"`gh api {args[0]}` failed: {why}"
    return True, proc.stdout


def check_forge_auto_merge(root: Path) -> CheckResult:
    """Where graph.yaml declares `review.delegation`, the repository must not
    allow auto-merge. The enum refuses `auto-merge` as a declaration; this is
    the half that reads the forge, because a setting that merges on its own
    contradicts the declaration while every check in the tree passes.

    The verdict is `allow_auto_merge` alone. The default branch's required
    status checks are read as CONTEXT — what an auto-merge would wait for —
    and a failure to read them is said in the detail, never a verdict."""
    if not (root / graph_mod.GRAPH_FILENAME).is_file():
        return CheckResult("D-05", D05_LABEL, SKIPPED,
                           "no graph.yaml — no delegation is declared")
    try:
        doc = graph_mod.load(root)
    except graph_mod.GraphError as e:
        raise DeclareError(f"D-05 cannot read the delegation graph.yaml "
                           f"declares: {e}") from e
    delegation = (doc.get("review") or {}).get("delegation")
    if delegation is None:
        return CheckResult("D-05", D05_LABEL, SKIPPED,
                           "graph.yaml declares no review.delegation")
    permits = delegation["permits"]
    ok, out = _gh(root, "repos/{owner}/{repo}", "--jq",
                  ".full_name, .allow_auto_merge, .default_branch")
    # `--jq` prints a null field as an EMPTY line, so the lines are read by
    # position and never whitespace-split: a missing setting would otherwise
    # shift the branch name into its place.
    lines = out.splitlines() if ok else []
    if not ok or len(lines) != 3 or lines[1] not in ("true", "false"):
        why = out if not ok else (
            f"`gh api` answered {len(lines)} line(s), not the three asked "
            "for" if len(lines) != 3 else
            f"`allow_auto_merge` on {lines[0] or 'the repository'} came back "
            + (f"as {lines[1]!r}" if lines[1] else "empty")
            + ", not true or false (a token without admin on the repository "
            "is served no value)")
        return CheckResult("D-05", D05_LABEL, UNREADABLE,
                           f"UNREADABLE — delegation permits '{permits}' and "
                           f"the forge setting could not be read: {why}. Not "
                           "clean; no certification rung claims D-05, so the "
                           "exit code does not move")
    repo, allowed, branch = lines[0], lines[1] == "true", lines[2]
    ok, checks = _gh(root, f"repos/{repo}/branches/{branch}/"
                     "protection/required_status_checks",
                     "--jq", ".contexts[]")
    names = [c.strip() for c in checks.splitlines() if c.strip()] if ok else []
    required = (f"required checks on `{branch}`: {', '.join(names) or 'none'}"
                if ok else f"required checks on `{branch}` unreadable ({checks})")
    if allowed:
        return CheckResult(
            "D-05", D05_LABEL, DRIFT,
            f"REFUSED — allow_auto_merge: true on {repo}", (Finding(
                "forge setting allow_auto_merge",
                f"is true, while graph.yaml review.delegation permits "
                f"'{permits}', which excludes auto-merge. A PR set to "
                f"auto-merge merges with no one pressing the button once its "
                f"requirements pass ({required}). Turn it off in the "
                "repository's settings (`gh repo edit --enable-auto-merge="
                "false`); widening the delegation has no spelling"),))
    return CheckResult("D-05", D05_LABEL, CLEAN,
                       f"PASS — allow_auto_merge: false on {repo}; delegation permits "
                       f"'{permits}'; {required}")


# --- the command --------------------------------------------------------------

def run(root: Path, scratch: Path) -> Report:
    config = config_mod.load(root)
    return Report(results=(
        check_scope_reach(config, root),
        check_gate_inputs(root, scratch),
        check_scope_runners(root, config),
        check_gate_surface_tiers(root, config),
        check_forge_auto_merge(root),
    ))


def render(report: Report) -> str:
    lines = ["# warden declare check — declaration vs behaviour", ""]
    mark = {CLEAN: "✓", DRIFT: "✗", SKIPPED: "–", UNREADABLE: "?"}
    for r in report.results:
        lines.append(f"{mark[r.status]} {r.id} {r.label} — {r.detail}")
        for f in r.findings:
            lines.append(f"    {f.check}: {f.detail}")
    lines.append("")
    if report.drifted:
        ids = ", ".join(r.id for r in report.drifted)
        lines.append(f"DRIFT: {ids} — a declaration and the behaviour it "
                     "governs have parted company")
    elif _unread(report):
        lines.append(f"unverified: {', '.join(_unread(report))} could not read "
                     "the forge — no drift found, and this is not a clean "
                     "result")
    else:
        lines.append("clean: every declaration above matches the behaviour it "
                     "governs")
    return "\n".join(lines) + "\n"


def _unread(report: Report) -> list[str]:
    return [r.id for r in report.results if r.status == UNREADABLE]


def exit_code(report: Report) -> int:
    """0 clean, 1 drift. 2 is reserved for a DeclareError and never derived
    from a report — a check that could not evaluate is not a clean one.
    D-05's UNREADABLE leaves the code alone: no certification rung claims
    D-05, and the render and JSON verdict name it rather than calling it
    clean."""
    return 1 if report.drifted else 0


def as_json(report: Report) -> str:
    return json.dumps({
        "schema": 1,
        "checks": [
            {"id": r.id, "label": r.label, "status": r.status,
             "detail": r.detail,
             "findings": [{"where": f.check, "detail": f.detail}
                          for f in r.findings]}
            for r in report.results],
        "verdict": (DRIFT if report.drifted
                    else UNREADABLE if _unread(report) else CLEAN),
    }, indent=2) + "\n"
