"""warden certify — enrollment maturity as an executable ladder.

A caveat written as prose ("a repo with no tests certifies nothing") cannot
go red. This turns the ladder into checks
a consuming repo runs — and can put in CI, so a repo that silently loses a
capability stops claiming the level.

Five cumulative levels (warden/certification/baseline.yaml):
  1 Gated · 2 Verified · 3 Reviewed · 4 Evidenced · 5 Self-improving
Unattended operation is a CAPABILITY BADGE (`capabilities: autonomous`),
not a rung: a repo with no cage can climb the whole ladder.

A project may EXTEND the ladder in `.warden/certification.yaml` (same
shape) but can never remove or redefine a baseline check — a ladder you can
shorten certifies nothing. Extra checks are additive per level.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import yamlio

BASELINE = Path(__file__).resolve().parent / "certification" / "baseline.yaml"
PROJECT_OVERLAY = ".warden/certification.yaml"

_TYPES = ("file_exists", "dir_exists", "glob_min", "file_contains",
          "any_file_contains", "file_absent", "not_tracked", "cage_enrolled",
          "cage_forbidden_paths_nonempty", "cage_file_exists", "graph_valid",
          "policy_disciplines_valid", "attest_rule_ids_resolve",
          "promotion_bar_met", "rule_promoted_through_loop",
          "rule_change_backtested",
          "ci_step_enforced", "corpus_min_reviews",
          "policy_repair_budget_capped", "light_round_enforced")

BACKTEST_SUBDIR = ".warden/memory/backtests"
# `reword` records a rule-BODY edit that neither promotes, pauses, nor
# retires: it still bumps rules_version, so S-05 still demands a fresh
# artifact naming the new version, and without an honest label the only ways
# through would be a mislabeled `promote` or a hard S-05 failure.
#
# `adopt` records a rule ADDITION, which bumps rules_version for the same
# reason. The addition is what `warden autonomy adopt` performs, so without
# this action the machine tier of the ladder would produce a tree that could
# not satisfy this rung at all. An adoption is not a measurement, and the
# artifact declares no tally (see `_write_adopt_backtest`).
#
# `retire` deletes the rule file, so its artifact names a rule the ruleset no
# longer declares. S-05 checks that id against git history instead (see
# `_committed_rule_ids`).
#
# `demote` (a severity moved down, out of the blocking severities), `narrow`
# (a smaller `applies_to`) and `unpause` (the pause fields removed) each edit
# a rule file, so each moves rules_version exactly as a reword does. They are
# what `warden rules lifecycle` proposes and what lifting a pause does, and no
# warden command performs any of them, so each artifact is written by hand.
BACKTEST_ACTIONS = ("promote", "pause", "retire", "reword", "adopt",
                    "demote", "narrow", "unpause")

# Who writes the S-05 backtest for each action, as data the missing-backtest
# message renders. A route has a `writer`, the warden command that writes the
# artifact, or None when the author writes it (BACKTEST_HAND_WRITER), and
# cases of (action, facts). A fact is the route's condition as data, never a
# phrase:
#   ("refuting_rounds_at_least", n)                  the streak pause acts on
#   ("blocking_severities", "inside" | "outside")    the rule's severity
#   ("rule_file", "added_by_command" | "added_by_hand" | "edited_by_hand")
# The route test compares these facts against its own table and checks the
# message carries each one, so the prose around them can change freely.
#
# The route follows how the rule file changed, not the action alone.
# `warden autonomy adopt` writes an artifact only for a rule it adds: it
# refuses a rule at a blocking severity and a rule file that already exists,
# so a rule file added by hand at any severity (the rule-advisor path) carries
# a hand-written artifact. A pause edited into the rule file writes none;
# only `warden autonomy pause` writes one with its edit.
BACKTEST_HAND_WRITER = "write it by hand at the new rules_version"

_RULE_FILE_PHRASES = {
    "added_by_command": "of a rule it adds",
    "added_by_hand": "of a rule file added by hand",
    "edited_by_hand": "edited into the rule file by hand",
}


@dataclass(frozen=True)
class BacktestRoute:
    writer: str | None
    cases: tuple[tuple[str, tuple[tuple, ...]], ...]


def backtest_routes(streak: int) -> tuple[BacktestRoute, ...]:
    """Every action's backtest routes; `streak` is memory.PAUSE_STREAK, passed
    in because the check imports memory lazily."""
    return (
        BacktestRoute("warden autonomy pause", (
            ("pause", (("refuting_rounds_at_least", streak),)),)),
        BacktestRoute("warden autonomy adopt", (
            ("adopt", (("rule_file", "added_by_command"),
                       ("blocking_severities", "outside"))),)),
        BacktestRoute(None, (
            ("promote", ()), ("retire", ()), ("reword", ()),
            ("demote", ()), ("narrow", ()), ("unpause", ()),
            ("pause", (("rule_file", "edited_by_hand"),)),
            ("adopt", (("rule_file", "added_by_hand"),)),
            ("adopt", (("blocking_severities", "inside"),)),
        )),
    )


def backtest_fact_phrase(fact: tuple) -> str:
    """The message phrase for one route fact."""
    key, value = fact
    if key == "refuting_rounds_at_least":
        return f"on {value}+ consecutive refuting rounds"
    if key == "blocking_severities":
        return f"{value} blocking_severities"
    if key == "rule_file":
        return _RULE_FILE_PHRASES[value]
    raise ValueError(f"unknown backtest route fact {fact!r}")


def backtest_case_text(action: str, facts: tuple[tuple, ...]) -> str:
    """One route case as message text: the action, then each fact's phrase."""
    article = "an" if action[0] in "aeiou" else "a"
    return " ".join([f"{article} {action}",
                     *(backtest_fact_phrase(f) for f in facts)])


def backtest_route_clause(route: BacktestRoute) -> str:
    """One route as one message clause: its writer, then every case."""
    covered = ", ".join(backtest_case_text(a, f) for a, f in route.cases)
    if route.writer is None:
        return f"{BACKTEST_HAND_WRITER} for {covered}"
    return f"`{route.writer}` writes one for {covered}"


def _committed_rule_ids(root: Path, rules_dir: Path
                        ) -> tuple[frozenset[str], bool, tuple[str, ...],
                                   tuple[str, ...]]:
    """(ids some rule file in HEAD's git history declared, whether the clone
    is shallow, the rule files committed as symlinks named below, the layouts
    history holds that are not read, each as a phrase), read from git objects
    alone, never the working tree. Paths are named from the repo root, which
    may sit below the top of the git tree.

    NO COMMITTED CORPUS STATE REACHES THIS TODAY, and the decision that
    follows from that is recorded here rather than left to be rediscovered.
    There is exactly one call site, taken only when a backtest artifact carries
    `action == "retire"` AND names a rule id no current ruleset declares.
    `.warden/memory/backtests/` holds 14 artifacts: 10 promote, 4 reword, ZERO
    retire. Measured three ways — a spy on this function during `certify.run()`
    records 0 calls at attained level 5, the same instrumented over the full
    worktree, and a real `git clone --depth 1` of the branch head certifies
    LEVEL 5 with no failing check.

    SO CI'S TWO SHALLOW JOBS DO NOT UNDER-CERTIFY, and a comment asserting they
    did was written and then reverted on that evidence. The artifact set is read
    from the WORKING TREE, so clone depth cannot reach the condition at all.

    THE RULING, so it is deliberate rather than an accident of what the corpus
    happens to hold: a NOTE here, not `fetch-depth: 0` on those two jobs. Full
    history on every run of two jobs is a standing cost paid for a path no
    committed artifact reaches, and the day one does the failure is loud — this
    function's refusal names the shallow clone. `tests/test_certify.py::
    test_s05_retire_refusal_blames_a_shallow_clone_only_in_one` covers the path
    synthetically, which is the right answer for live code the corpus does not
    exercise. If a retire artifact is ever committed, the corpus and enrollment
    jobs are where this bites, and THAT is the moment to re-decide the depth.

    A retire deletes the rule file, so only history can show the rule existed.
    A rule file is a `.md` directly inside a rules dir: `rules_dir`, or any
    `review.rules_dir` the repo.yaml at the repo root declared in a commit
    (the default where it declared none), so a moved rules dir keeps its
    history. Every blob such a file held is read, whichever commit wrote it,
    so the answer does not turn on how a change was split into commits.

    Some layouts are not read, and these are returned for a refusal to name
    when history holds them: a rules dir outside the repo root; a rules dir,
    or any directory above it, committed as a symlink or a submodule; and
    another path where a version of this repo.yaml was committed and which
    no longer holds one. Git records a move as a delete and an add, and
    reading the rules dir such a path declared could read another project's
    rules. What marks the path as this project's is one commit that took a
    version away from it and wrote that same version at this repo.yaml, as
    `git mv` does: a sibling's repo.yaml or a fixture, even one copied from
    this one, is named only when such a commit removed it. They are not every
    unread layout. A repo.yaml moved and edited in one commit is not named,
    nor one deleted and added in separate commits when no single diff of a
    commit against one parent holds both. A merge is diffed against each
    parent, so a delete and an add it brings in are named only when both
    are new relative to one parent: that parent still holds the version at
    the old path and does not yet hold it at this repo.yaml. A branch
    holding both, merged with --no-ff, is named; a merge whose one parent
    brings the delete and the other the add is not.

    A rule file committed as a symlink is not followed: its blob is a path,
    not a rule. Its path is returned when its latest committed state before
    any deletion was a symlink, or when no regular blob at that path yielded
    an id. A path whose last committed file was regular and was read as a
    rule is left out; git objects cannot tell whether the id being retired
    was the one the symlink pointed at. A shallow clone's history ends early,
    and the flag lets a refusal say so.

    An id is read from each file's frontmatter leniently: a rule file from an
    older schema still names the rule it was. Raises DiffError when git cannot
    answer (not a repository, no commit, an object the store lacks), so the
    caller refuses rather than reading a missing history as a ruleset that
    never held the rule.
    """
    import posixpath

    import yaml

    from . import diffs as diffs_mod
    from .autonomy import _frontmatter_of
    from .config import CONFIG_NAME, DEFAULT_RULES_DIR

    pathspec_env = dict.fromkeys(
        ("GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS",
         "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"), "0")

    def written(pathspecs: list[str], relative: bool = True
                ) -> list[tuple[str, str, str, str, int]]:
        # (mode, blob sha, path, prior blob sha, diff) of every file version a
        # commit reachable from HEAD wrote under `pathspecs`, newest commit
        # first; entries of one commit's diff against one parent share `diff`.
        # `-m` diffs merges too, so a blob written while resolving one is not
        # skipped.
        # `--relative` names paths from the repo root; without it paths are
        # named from the top of the git tree. Each pathspec carries its own
        # magic, so the environment's pathspec settings are cleared.
        if not pathspecs:
            return []
        tokens = diffs_mod.run_git(
            root, "log", "--root", "-m", "--raw", "--no-abbrev",
            "--no-renames", *(["--relative"] if relative else []), "-z",
            "--format=%H", "HEAD", "--", *pathspecs,
            env=pathspec_env).split("\0")
        found, i, diff = [], 0, 0
        while i < len(tokens) - 1:
            token = tokens[i].lstrip("\n")
            if token.startswith(":"):
                fields = token[1:].split()
                found.append((fields[1], fields[3], tokens[i + 1], fields[2],
                              diff))
                i += 2
            else:
                diff += 1
                i += 1
        return found

    def latest(entries: list[tuple[str, ...]]
               ) -> dict[str, tuple[str, str]]:
        state: dict[str, tuple[str, str]] = {}
        for mode, sha, path, *_ in entries:
            state.setdefault(path, (mode, sha))
        return state

    shallow = diffs_mod.run_git(
        root, "rev-parse", "--is-shallow-repository").strip() == "true"
    prefix = diffs_mod.run_git(
        root, "rev-parse", "--show-prefix").removesuffix("\n")
    here = posixpath.join(prefix, CONFIG_NAME)
    yamls = written([f":(top,glob)**/{CONFIG_NAME}"], relative=False)
    arrived = {(diff, sha) for mode, sha, path, _, diff in yamls
               if path == here and mode != "000000"}
    configs = {sha for _, sha in arrived}
    # What git records for a move: one diff takes a version from a path and
    # writes that same version here.
    departed = {path for _, sha, path, prior, diff in yamls
                if prior != sha and (diff, prior) in arrived}
    moved = sorted(posixpath.relpath(path, prefix or ".")
                   for path, (_, sha) in latest(yamls).items()
                   if path != here and sha not in configs
                   and path in departed)
    declared_dirs = {os.path.relpath(rules_dir, root)}
    for data in diffs_mod.git_blobs(root, sorted(configs)).values():
        try:
            doc = yamlio.load(data.decode("utf-8"))
        except (ValueError, yaml.YAMLError):
            continue
        review = doc.get("review") if isinstance(doc, dict) else None
        declared = (review.get("rules_dir", DEFAULT_RULES_DIR)
                    if isinstance(review, dict) else DEFAULT_RULES_DIR)
        if isinstance(declared, str) and declared.strip():
            declared_dirs.add(os.path.relpath(root / declared.strip(), root))
    dirs, outside = set(), set()
    for declared in declared_dirs:
        declared = Path(declared).as_posix()
        (outside if declared == ".." or declared.startswith("../")
         else dirs).add(declared)
    entries = written([f":(literal){d}" for d in sorted(dirs)])
    above = {"/".join(d.split("/")[:i])
             for d in dirs for i in range(1, d.count("/") + 1)}
    # An exclude drops every path under it, deeper ancestors too, so each
    # depth is its own log. A literal `A/` exclude also drops a submodule at
    # A, so the exclude is the glob `A/**` with A escaped.
    ancestry = [entry for depth in sorted({a.count("/") for a in above})
                for entry in written([
                    spec for a in sorted(above) if a.count("/") == depth
                    for spec in (f":(literal){a}", ":(exclude,glob)"
                                 + re.sub(r"([*?[\\])", r"\\\1", a) + "/**")])]
    kinds = {"120000": "symlink", "160000": "submodule"}
    linked = sorted({(path, kinds[mode])
                     for mode, _, path, *_ in [*entries, *ancestry]
                     if mode in kinds and (path in dirs or path in above)})
    rule_files = [(mode, sha, path) for mode, sha, path, *_ in entries
                  if path.endswith(".md") and posixpath.dirname(path) in dirs]
    regular = [(sha, path) for mode, sha, path in rule_files
               if mode in ("100644", "100755")]
    ids_by_blob = {}
    for sha, data in diffs_mod.git_blobs(
            root, sorted({s for s, _ in regular})).items():
        doc = _frontmatter_of(
            data.decode("utf-8", "replace").replace("\r\n", "\n"))
        if doc is not None and isinstance(doc.get("id"), str):
            ids_by_blob[sha] = doc["id"]
    read = {path for sha, path in regular if sha in ids_by_blob}
    last = latest([e for e in rule_files if e[0] != "000000"])
    symlinked = tuple(sorted({path for mode, _, path in rule_files
                              if mode == "120000"
                              and (last[path][0] == "120000"
                                   or path not in read)}))

    def on_path(path: str, kind: str) -> str:
        if path in dirs:
            return (f"the rules dir {path} is committed as a {kind} and is "
                    "not followed")
        below = min(d for d in dirs if d.startswith(path + "/"))
        return (f"{path}, above the rules dir {below}, is committed as a "
                f"{kind} and is not followed")

    unread = (
        *(f"the rules dir {d} is outside the repo root and its history is "
          "not read" for d in sorted(outside)),
        *(on_path(path, kind) for path, kind in linked),
        *(f"this {CONFIG_NAME} was once committed at {p}, and rule files "
          "under the rules dir it declared there are not read"
          for p in moved))
    return frozenset(ids_by_blob.values()), shallow, symlinked, unread


def _declared_blocking_severities(root: Path) -> tuple[frozenset[str], str]:
    """(review.blocking_severities, problem) from the working tree's repo.yaml,
    read leniently: a certify run reports a problem, it does not crash."""
    import yaml

    from .config import CONFIG_NAME, read_repo_yaml

    try:
        doc = yamlio.load(read_repo_yaml(root).decode("utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as e:
        return frozenset(), f"{CONFIG_NAME} cannot be read ({e})"
    review = doc.get("review") if isinstance(doc, dict) else None
    severities = (review.get("blocking_severities")
                  if isinstance(review, dict) else None)
    if (not isinstance(severities, list)
            or not all(isinstance(v, str) for v in severities)):
        return frozenset(), (f"{CONFIG_NAME} declares no usable "
                             "review.blocking_severities")
    return frozenset(severities), ""


def _promotion_validation_problem(doc: dict) -> str | None:
    """What is wrong with a promote artifact's validation block, or None.

    The micro-test protocol: a promotion claim carries a
    no-guidance CONTROL arm (a control that never exhibits the failure means
    there is nothing to fix — stop), MICROTEST_MIN_REPS+ reps per variant,
    variance reported as a metric, and an attestation that every flagged
    match was manually read — or it is explicitly marked unvalidated with
    the reason. The Wilson bar says WHEN to promote; this is the evidence
    the proposal carries. Applied only to promote-action artifacts at the
    CURRENT rules_version: older artifacts are committed history and are
    never revalidated retroactively.
    """
    from .memory import MICROTEST_MIN_REPS
    v = doc.get("validation")
    if not isinstance(v, dict):
        return ("promote artifact carries no validation block — a promotion "
                "claim needs micro-test evidence (control arm, "
                f"{MICROTEST_MIN_REPS}+ reps per variant, variance, flagged "
                "matches read) or an explicit validation of "
                "{status: unvalidated, reason: ...}")
    if v.get("status") == "unvalidated":
        reason = v.get("reason")
        if not (isinstance(reason, str) and reason.strip()):
            return ("validation is marked unvalidated but names no reason — "
                    "the marker exists so the gap is stated, not waved past")
        return None
    control = v.get("control")
    if not isinstance(control, dict):
        return ("validation names no control arm — without a no-guidance "
                "control there is no evidence the failure occurs at all")
    exhibited = control.get("exhibited")
    if not isinstance(exhibited, int) or isinstance(exhibited, bool):
        return ("control arm reports no integer 'exhibited' count — the "
                "control's failure count is the reason a fix exists")
    if exhibited == 0:
        return ("control arm exhibited the failure 0 times — nothing to fix; "
                "a promotion cannot stand on a failure the control never "
                "shows")
    variants = v.get("variants")
    if not isinstance(variants, dict) or not variants:
        return ("validation names no treatment variant — a control with "
                "nothing compared against it validates nothing")
    if "control" in variants:
        # Refused outright rather than validated alongside: merging the two
        # namespaces would let a variant named 'control' SHADOW the real
        # control arm, so a 1-rep control could clear the floor this
        # function's own refusal message promises.
        return ("a variant may not be named 'control' — the control arm is "
                "declared in its own field, and a duplicate name shadows it")
    # Iterated as pairs, never merged into one dict: every arm — the control
    # AND each variant — must record both its reps (the floor) and its
    # exhibited count (the outcome). An arm with reps but no outcome is a
    # run that cannot be compared, which is the whole point of the protocol.
    for name, arm in [("control", control), *variants.items()]:
        reps = arm.get("reps") if isinstance(arm, dict) else None
        if isinstance(reps, bool) or not isinstance(reps, int) \
                or reps < MICROTEST_MIN_REPS:
            return (f"arm {name!r} ran reps={reps!r} — the micro-test "
                    f"protocol needs {MICROTEST_MIN_REPS}+ reps per variant")
        shown = arm.get("exhibited")
        if isinstance(shown, bool) or not isinstance(shown, int):
            return (f"arm {name!r} records no integer 'exhibited' count — "
                    "an arm without its outcome cannot be compared against "
                    "the control")
    variance = v.get("variance")
    if variance is None or (isinstance(variance, str) and not variance.strip()):
        return ("validation reports no variance — a mean without its spread "
                "overstates the measurement")
    if v.get("flagged_matches_read") is not True:
        return ("validation does not attest flagged_matches_read: true — "
                "under this protocol automated match counts are read by "
                "hand, never trusted")
    return None

# The build disciplines a policy may declare. Enforced here and documented in
# docs/wiki/Skills-Policy.md.
RECOGNIZED_DISCIPLINES = ("test-driven-development", "systematic-debugging",
                          "verification-before-completion", "design-approval")

# The repair-round ceiling deliver enforces ("capped at 2"). A repo declares
# its budget as repo.yaml's `repair.budget`, an integer from 1 to this cap;
# repo.schema.json states the same bound, and a test holds the two equal. A
# repo may declare a LOWER budget, never a higher one.
PLATFORM_REPAIR_CAP = 2
REPAIR_BUDGET_KEY = "repair.budget"


def check_repair_budget(doc: object) -> tuple[bool, str]:
    """Whether parsed repo.yaml declares a repair budget within the cap.

    The budget is `repair: {budget: N}`, N an integer from 1 to
    PLATFORM_REPAIR_CAP. An undeclared budget FAILS: the key is required at
    Level 3 (founder ruling of 2026-09-15). A value the schema refuses is
    still named here, so the rung says what is wrong rather than only that
    the config is invalid. A boolean is not an integer, and neither is `2.0`.

    Nothing else is read. A budget stated in `.warden/skills-policy.md` prose
    gates nothing: reading one out of prose was tried through five review
    rounds, and every round found a new false pass or false refusal.
    """
    add = (f"add `repair:` with `budget: {PLATFORM_REPAIR_CAP}` (an integer "
           f"from 1 to the platform cap of {PLATFORM_REPAIR_CAP})")
    if not isinstance(doc, dict):
        return False, (f"not a mapping, so no {REPAIR_BUDGET_KEY} can be "
                       f"read — {add}")
    repair = doc.get("repair")
    if repair is None or (isinstance(repair, dict) and "budget" not in repair):
        return False, (f"no {REPAIR_BUDGET_KEY} declared, and Level 3 "
                       f"requires one — {add}")
    if not isinstance(repair, dict):
        return False, f"`repair:` is {repair!r}, not a mapping — {add}"
    budget = repair["budget"]
    if isinstance(budget, bool) or not isinstance(budget, int):
        return False, (f"{REPAIR_BUDGET_KEY} is {budget!r}, not an integer "
                       f"— {add}")
    if budget < 1:
        return False, f"{REPAIR_BUDGET_KEY} is {budget}, below 1 — {add}"
    if budget > PLATFORM_REPAIR_CAP:
        return False, (f"{REPAIR_BUDGET_KEY} is {budget}, above the platform "
                       f"cap of {PLATFORM_REPAIR_CAP} — repo.schema.json "
                       f"refuses it too; a repo may set a lower budget, never "
                       f"a higher one, and deliver runs no round past the cap")
    return True, f"{REPAIR_BUDGET_KEY} {budget} (<= cap {PLATFORM_REPAIR_CAP})"


def _disciplines_section(text: str) -> list[str] | None:
    """Lines of the `## Build disciplines` section, or None if absent.

    Stops at the next H2 so a following section's prose is never read as a
    declaration.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower() == "## build disciplines":
            body = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("## "):
                    break
                body.append(nxt)
            return body
    return None


def check_disciplines(text: str) -> tuple[bool, str]:
    """Validate a policy's declared disciplines against the vocabulary.

    Absent is valid: the section is optional, and failing on absence would
    fail every repo enrolled before it existed.
    """
    body = _disciplines_section(text)
    if body is None:
        return True, "no build disciplines declared (the section is optional)"

    valid = ", ".join(RECOGNIZED_DISCIPLINES)
    declared, problems = [], []
    # Only markdown list items declare: `- name: when it applies`. Prose
    # wraps, and a wrapped continuation beginning `word:` was being read as
    # a declaration named after that word — a policy that explained itself
    # in a sentence got rejected for a discipline it never declared. A list
    # item is unambiguous, and its continuation lines are indented.
    for raw in body:
        if not raw.lstrip().startswith(("- ", "* ")):
            continue  # prose, blank, or the continuation of a list item
        line = raw.lstrip()[2:].strip()
        if ":" not in line:
            problems.append(f"{line[:40]!r}: not a `name: when it applies` "
                            "declaration")
            continue
        name, _, when = line.partition(":")
        name = name.strip().strip("`*_")
        if name not in RECOGNIZED_DISCIPLINES:
            problems.append(f"unrecognized discipline {name!r}")
        elif not when.strip():
            problems.append(f"{name}: declared with no scope — say when it "
                            "applies, or it cannot be honored")
        else:
            declared.append(name)

    if problems:
        return False, ("; ".join(problems) + f". Recognized: {valid}")
    return True, f"{len(declared)} discipline(s) declared: " + ", ".join(declared)


class CertifyError(Exception):
    pass


@dataclass
class CheckResult:
    id: str
    label: str
    level: int
    passed: bool
    detail: str


def _load_ladder(path: Path, *, require_levels: bool = True) -> dict:
    try:
        doc = yamlio.load(path.read_text())
    except yaml.YAMLError as e:
        raise CertifyError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(doc, dict) or doc.get("version") != 1:
        raise CertifyError(f"{path}: expected a mapping with version: 1")
    levels = doc.get("levels")
    if not isinstance(levels, dict) or not levels:
        # An overlay may carry only `grandfathered_rule_ids:` — a migration
        # declaration with no extra checks is a legitimate overlay.
        if require_levels:
            raise CertifyError(f"{path}: no levels declared")
        levels = {}
        doc["levels"] = levels
    def _validate_checks(checks: list) -> None:
        for check in checks:
            missing = {"id", "label", "type"} - set(check)
            if missing:
                raise CertifyError(f"{path}: check missing {sorted(missing)}")
            if check["type"] not in _TYPES:
                raise CertifyError(
                    f"{path}: check {check['id']!r} has unknown type "
                    f"{check['type']!r} (known: {', '.join(_TYPES)})")
            # Type-specific required fields fail at LOAD, not as a runtime
            # traceback: the loader is where malformation is
            # a named error.
            if check["type"] in ("ci_step_enforced", "light_round_enforced"):
                if not isinstance(check.get("pattern"), str):
                    raise CertifyError(
                        f"{path}: check {check['id']!r} ({check['type']}) "
                        "needs a string `pattern`")
                flags = check.get("flags", [])
                if not isinstance(flags, list) or not all(
                        isinstance(f, str) and f.startswith("--") for f in flags):
                    raise CertifyError(
                        f"{path}: check {check['id']!r} `flags` must be a "
                        "list of `--option` strings")
                if "event" in check and not isinstance(check["event"], str):
                    raise CertifyError(
                        f"{path}: check {check['id']!r} `event` must be a "
                        "string event name")
            if "min" in check and (isinstance(check["min"], bool)
                                   or not isinstance(check["min"], int)):
                raise CertifyError(
                    f"{path}: check {check['id']!r} `min` must be an integer")

    for lv, spec in levels.items():
        if not isinstance(lv, int):
            raise CertifyError(f"{path}: level key {lv!r} must be an integer")
        _validate_checks(spec.get("checks", []))
    # Capability badges (e.g. `autonomous`): same check shape, evaluated on
    # demand rather than in the cumulative chain (surfaced by --capability).
    for name, spec in (doc.get("capabilities") or {}).items():
        if not isinstance(spec, dict):
            raise CertifyError(f"{path}: capability {name!r} must be a mapping")
        _validate_checks(spec.get("checks", []))
    return doc


def load(root: Path) -> dict:
    """Baseline ladder plus any project extension, merged additively."""
    ladder = _load_ladder(BASELINE)
    overlay_path = root / PROJECT_OVERLAY
    if not overlay_path.is_file():
        return ladder

    overlay = _load_ladder(overlay_path, require_levels=False)
    if overlay.get("capabilities"):
        # Fail closed rather than validate-then-drop: a consumer extending a
        # capability badge deserves an error, not silence.
        raise CertifyError(
            f"{overlay_path}: capability badges are not extensible yet "
            "— remove the capabilities block")
    # Capability check ids count as baseline ids too: N-01 leaving the
    # cumulative chain must not free its id for an overlay to reuse.
    baseline_ids = {c["id"] for lv in ladder["levels"].values()
                    for c in lv.get("checks", [])}
    baseline_ids |= {c["id"] for cap in (ladder.get("capabilities") or {}).values()
                     for c in cap.get("checks", [])}
    for lv, spec in overlay["levels"].items():
        for check in spec.get("checks", []):
            # Fail closed: a consumer that could redefine a baseline check
            # could certify itself by deleting the thing being certified.
            if check["id"] in baseline_ids:
                raise CertifyError(
                    f"{overlay_path}: check id {check['id']!r} redefines a "
                    "baseline check — project checks may only ADD")
        target = ladder["levels"].setdefault(lv, {"name": f"Level {lv}",
                                                  "checks": []})
        target.setdefault("checks", []).extend(spec.get("checks", []))
    return ladder


def _rules_dir(root: Path) -> tuple[Path, str]:
    """(declared rules dir, problem) — repo.yaml review.rules_dir, default in
    place, read leniently: certify must be able to SAY a repo is immature, so
    an unreadable repo.yaml falls back to the default location rather than
    crashing. The derivation itself lives in `config` so the tag layer's
    alias guard resolves the SAME dir from the same root.

    The problem travels WITH the path. Discarding it would bet that
    certify's own checks complain about an unreadable ruleset — a bet that
    holds only while the fallback location is ABSENT. A malformed
    declaration plus a stale dir at the default would score every
    ruleset-keyed check against the wrong ruleset with nothing naming the
    problem, the same fail-open autonomy, advisor and lifecycle refuse.
    Every caller must refuse to score when the
    problem is non-empty; the path is then for naming the fallback in
    messages, never an answer to build on.
    """
    from .config import declared_rules_dir
    return declared_rules_dir(root)


def _grandfathered_rule_ids(root: Path) -> frozenset[str]:
    """Legacy rule_ids a consumer has EXPLICITLY declared as pre-invariant.

    The rule-identity invariant is forward-only at the write seam (memory
    ingest's decision): committed evidence written before it existed
    must not be retroactively invalidated, and rewriting evidence to satisfy
    a check would be worse. The escape is deliberately loud: the ids live in
    the consumer's own `.warden/certification.yaml` under
    `grandfathered_rule_ids:`, reviewed like any gate change, named in
    E-02's output, and never usable by the promotion gate.

    WHAT THE WAIVER DOES NOT DO, stated because this docstring used to claim
    it did. It promised a grandfathered id was "still rejected
    by `attest write` for NEW attestations" — a write-side floor that made the
    waiver a one-way migration door. That floor holds only while a rule
    UNPAUSED answers the class. `attest write` never reads
    certification.yaml; it refuses a covered slug because a declared rule
    answers it (`attest._RuleIndex.answered`), and a PAUSE removes that answer
    on purpose, so the class re-opens as a candidate and fresh
    `unmapped:<covered-slug>` records are ACCEPTED for as long as the pause
    lasts. Those records carry no era marker — no
    `legacy_rule_id`, nothing — so on unpause they are byte-indistinguishable
    from pre-invariant ones, and the loop below strips them under the waiver
    and reports them as "(grandfathered, declared in the overlay)" beside the
    genuinely legacy ones. Nothing flags the mixture.

    So the honest reading is: the waiver is a declaration that a slug's
    records are not to be resolved against the ruleset, and while a covering
    rule is paused it absorbs new records as readily as old ones. Making the
    eras distinguishable would need an era marker at the write or ingest seam;
    by ruling, this list stays E-02's single reviewed seam and no such marker
    is built, so the mixture is a KNOWN, accepted
    behaviour rather than a promise this code keeps.
    """
    overlay = root / PROJECT_OVERLAY
    if not overlay.is_file():
        return frozenset()
    try:
        doc = yamlio.load(overlay.read_text())
    except (OSError, yaml.YAMLError):
        return frozenset()
    if not isinstance(doc, dict):
        return frozenset()
    ids = doc.get("grandfathered_rule_ids")
    if not isinstance(ids, list):
        return frozenset()
    return frozenset(i for i in ids if isinstance(i, str))


def _shadow_remedy(shards: list[Path], rules_dir: Path,
                   grandfathered: frozenset[str], raised: str) -> str:
    """Describe an E-02 failure caused by a rule that answers a candidate class.

    `memory stats` proposes "write a rule for unmapped:<slug>"; the moment
    that rule exists, every shard that ever filed under the slug stops
    resolving. A bare validator error against ONE shard would tell the reader
    to re-file the finding, but committed evidence is exactly what must NOT
    be re-filed, so this names the cause — the rule just written — and the
    remedy instead.

    Returns "" when the failure is anything else. An id that names no rule and
    no covered class is a typo, and advertising the waiver for it would teach
    the escape hatch as the routine fix.

    The question is whether the id that ACTUALLY raised is shadowed — not
    whether anything in the corpus is. The looser question would describe a
    class that had not fired as the cause of one that had, and its remedy
    would leave E-02 red after being followed verbatim.
    """
    from . import memory as memory_mod
    from .attest import shadowed_rule_ids

    if not shadowed_rule_ids([{"rule_id": raised}], rules_dir):
        return ""
    hits: dict[str, tuple[str, int, set[str]]] = {}
    for shard in shards:
        # A second shard reader in this file, and it reads through
        # `read_shard` for the same reason E-02 does: a hand-rolled
        # `except (OSError, json.JSONDecodeError)` misses UnicodeDecodeError,
        # which subclasses ValueError and not JSONDecodeError. This runs from
        # INSIDE E-02's `except AttestError` handler and sweeps EVERY shard,
        # including ones the caller's loop has not reached, so a non-UTF-8
        # shard sorting after the one that raised would take the whole of
        # `warden certify` down with a traceback: no verdict, no
        # `certification.json`.
        #
        # `read_shard` is the one definition every reader that COUNTS this
        # store names — `records_from_shards`, `review_events`, `memory
        # ingest`'s cache rebuild, E-02 and this remedy sweep. It is not yet
        # every reader that TOUCHES it, and what is left is one:
        #
        #   RETIRED-TUPLE RESIDUE: memory._event_already_filed
        #
        # The line names what is LEFT, so a sweep for the retired tuple finds
        # exactly the sites that still hand-roll it. A test derives the line
        # from the named readers' source rather than trusting this paragraph,
        # so fixing the residue turns it red instead of leaving it stale.
        try:
            doc = memory_mod.read_shard(shard)
        except ValueError:
            # Skipping is right HERE and only here: this is a best-effort
            # remedy scan running inside a failure path that has already
            # decided to report, and the caller's own loop refuses the same
            # shard by name. What must not happen is the skip being decided
            # by which ValueError subclass the shard happened to raise.
            continue
        records = [r for r in doc["records"] if isinstance(r, dict)
                   and r.get("rule_id") not in grandfathered]
        try:
            shadowed = shadowed_rule_ids(records, rules_dir)
        except Exception:  # unreadable ruleset: the caller fails closed on it
            return ""
        for r in records:
            rid = r.get("rule_id")
            if rid not in shadowed:
                continue
            by, n, seen = hits.get(rid, (shadowed[rid], 0, set()))
            seen.add(shard.name)
            hits[rid] = (by, n + 1, seen)
    if not hits:
        return ""
    parts = []
    for rid in sorted(hits):
        by, n, seen = hits[rid]
        parts.append(f"{n} record(s) across {len(seen)} shard(s) file under "
                     f"{rid!r}, which the declared rule {by!r} now answers")
    ids = ", ".join(sorted(hits))
    return ("; ".join(parts)
            + ". That evidence was judged while no unpaused rule answered "
            "the class — before the rule existed, or while it was paused "
            "(a paused rule's class files under its own slug) "
            "— and shipping or unpausing the rule invalidated it. Committed "
            "evidence is never re-filed: declare the "
            "legacy ids in .warden/certification.yaml under "
            f"grandfathered_rule_ids: [{ids}] — reviewed like any gate "
            "change, and never usable by the promotion gate. Note what the "
            "waiver does NOT do: `attest write` refuses these spellings only "
            "while a rule UNPAUSED answers the class, so pausing the rule "
            "re-opens the slug and new records file under it and are absorbed "
            "here on unpause, indistinguishable from the legacy ones.")


# WHY A cage.toml CANNOT BE READ — one tuple, three call sites.
#
# `_cage_owner`, `_cage_unreadable` and the `cage_forbidden_paths_nonempty` arm
# of `_run_check` each parse the same file. `UnicodeDecodeError` belongs in the
# tuple: `Path.read_text()` raises it on non-UTF-8 bytes and it is a ValueError
# rather than an OSError, so without it a cage.toml written as UTF-16 (or with
# one stray byte) escapes `certify.run` and the ladder does not render at all.
#
# One tuple because three copies is how two of them get fixed: `_cage_owner`
# runs FIRST, from inside `_cage_candidates`, so repairing the other two alone
# would change nothing an operator could see.
_CAGE_UNREADABLE = (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError)


def _cage_candidates(root: Path) -> tuple[list[Path], list[Path]]:
    """(claimants, present): the cage.toml candidates that exist, in
    convention order, and the SIBLING ones among them that declare this
    root as their live_checkout.

    In-repo is never a claimant: `<root>/cage.toml` is a file the caged
    session can write, and one `cage enroll` can never render — its cage
    dir would be the checkout itself, which cage.config refuses — so
    letting it out-rank a sibling would certify a cage that cannot exist.
    """
    here = root.resolve()
    siblings = (here.parent / f"{here.name}-cage", here.parent / "cage")
    present = [p for d in (*siblings, here) if (p := d / "cage.toml").is_file()]
    claimants = [p for p in present if p.parent != here and _cage_owner(p) == here]
    return claimants, present


def _cage_unreadable(cage: Path) -> bool:
    """Does the config fail to PARSE as TOML? Distinct from "declares no
    owner": an unreadable sibling cannot be disproved as this checkout's
    cage, so the conflict check counts it rather than dropping it.

    "Unreadable by any cause": `_CAGE_UNREADABLE` includes the
    `UnicodeDecodeError` that `Path.read_text()` raises on a non-UTF-8
    cage.toml — a ValueError, not an OSError — which would otherwise escape
    `certify.run` with no badge and no certification.json. The three call
    sites that parse the file share that one tuple.
    """
    try:
        tomllib.loads(cage.read_text())
    except _CAGE_UNREADABLE:
        return True
    return False


def _cage_doubt(cage: Path, unreadable: list[Path],
                undisprovable: list[Path], here: Path) -> str:
    """Why certify names this config in the refusal — said out loud, because
    "cages at A and B" without a reason leaves the operator to guess which one
    to fix. Four reasons, and a claimant's is the one that must not be
    silence: it is named because it DECLARES this checkout, so a message
    saying certify "cannot rule them out" would be false of it.

    Every arm is a TEST, none a fallback: the owner is read off the file. A
    fallback reason for whatever the first two arms miss would label a chosen
    cage declaring some OTHER checkout, beside a no-owner sibling, as
    declaring this one — and the owner-mismatch refusal downstream never runs
    because the conflict arm returns first.
    """
    if cage in unreadable:
        return " (unreadable)"
    if cage in undisprovable:
        return " (declares no owner)"
    if _cage_owner(cage) == here:
        return " (declares this checkout)"
    return " (declares another checkout)"


def _cage_config(root: Path) -> Path | None:
    """The project's cage.toml, if the repo points at a cage.

    Looked up by convention only: a sibling checkout —
    `<repo>/../<checkout>-cage/`, then `<repo>/../cage/` — or in-repo;
    absence is simply "not enrolled". Among the SIBLINGS present, one that
    DECLARES this root as its `live_checkout` wins whatever its name; with
    no claimant the order above stands and the owner check downstream says
    whose cage it found. `../cage/` alone would be one slot per workspace: a
    checkout enrolled beside another project's cage would stop discovery at
    that cage and never consult its own. Two claimants are a conflict the
    cage checks refuse by name, not a tie this order may break.

    Pre-rename cage names are not looked up: a cage still carrying them is
    un-enrolled by design — the fix is to rename it.

    There is no explicit `cage:` key in repo.yaml: repo.schema.json declares
    the root `additionalProperties: false`, so a repo.yaml carrying that key
    fails config validation before certify ever runs.
    """
    claimants, present = _cage_candidates(root)
    if claimants:
        return claimants[0]
    return present[0] if present else None


def _cage_owner(cage: Path) -> Path | None:
    """The live checkout a cage config declares itself for, if any.

    Unreadable configs return None here — ownership cannot be established
    either way, and the per-check parsers already fail closed on their own
    fields (forbidden-paths says "unreadable cage config").
    """
    try:
        data = tomllib.loads(cage.read_text())
    except _CAGE_UNREADABLE:
        # The FIRST raiser of the three, so the tuple has to be right here or
        # the other two never run.
        return None
    declared = data.get("project", {}).get("live_checkout")
    if not isinstance(declared, str) or not declared:
        return None
    return Path(declared).expanduser().resolve()


# Shell forms that replace a command's failing exit with success, matched on
# shell meaning rather than one literal spacing (listing only `|| true` would
# certify `|| exit 0` as enforced).
# Each pattern pairs with the canonical spelling the failure message names.
#
# Scope: SUFFIX forms are checked only on the
# gate command's own logical line — GitHub's default run shell is `bash -e`,
# which fails the step at the failing line, so a `; true` on some LATER
# cleanup line swallows nothing and must not fail a step that gates.
# BLOCK forms (`set +e` / `set +o errexit`) disarm errexit for the whole
# block, so they are checked block-wide. A pipe after the gate command is a
# swallow of its own: without pipefail the step exits with the LAST pipeline
# command's status (the default shell sets no pipefail; `shell: bash` does).
#
# This is an enumerated DENYLIST of common spellings, not a proof the step
# gates: `|| <any other command that succeeds>`, grouped forms
# (`|| { log; exit 0; }`), and a captured-but-never-re-raised
# `|| status=$?` all still certify — declared as open residuals in
# baseline.yaml alongside the `if:` and bare-`exit 0` limits.
_SUFFIX_SWALLOWS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\|\|\s*true\b"), "|| true"),
    (re.compile(r"\|\|\s*:(?!=)"), "|| :"),
    (re.compile(r"\|\|\s*exit\s+0\b"), "|| exit 0"),
    (re.compile(r"\|\|\s*echo\b"), "|| echo"),
    (re.compile(r"\|\|\s*printf\b"), "|| printf"),
    (re.compile(r";\s*true\b"), "; true"),
    (re.compile(r";\s*exit\s+0\b"), "; exit 0"),
)
_BLOCK_SWALLOWS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bset\s+\+\w*e"), "set +e"),
    (re.compile(r"\bset\s+\+o\s+errexit\b"), "set +o errexit"),
)
_SINGLE_PIPE = re.compile(r"(?<!\|)\|(?!\|)")
# Quoted segments are stripped before marker matching: a marker inside a
# string argument (`--note "pass|fail"`, `echo "never use || true"`) is
# text, not shell (escaped quotes stay a declared residual — this is a
# denylist, not a shell parser).
_QUOTED = re.compile(r'"[^"\n]*"|\'[^\'\n]*\'')
# pipefail is SET only by `set -o pipefail` (any short-option bundle on the
# same set line) and UNSET by `set +o pipefail`; a bare substring test would
# count the DISABLING spelling and mere comments as protection.
_PIPEFAIL_ON = re.compile(r"\bset\s[^\n;|&]*-\w*o\s+pipefail")
_PIPEFAIL_OFF = re.compile(r"\bset\s[^\n;|&]*\+o\s+pipefail")


def _logical_lines(run_text: str) -> list[str]:
    """Shell lines with continuations joined: a line ending in `\\`, `||`,
    `&&`, or `|` continues onto the next, so a marker split across a YAML
    line break is still seen on the gate command's line."""
    out: list[str] = []
    buf = ""
    for raw in run_text.splitlines():
        piece = raw.strip()
        buf = f"{buf} {piece}" if buf else piece
        if buf.endswith("\\"):
            buf = buf[:-1].rstrip()
            continue
        if buf.endswith(("||", "&&", "|")):
            continue
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return out


def _own_words(rest: str) -> set[str]:
    """The words of the one command REST continues, up to the first `;`,
    `&`, `|` or comment; a `$(...)` or backtick substitution is one word.
    Unbalanced quoting or parentheses give no words, so earn no flag."""
    flat, depth, quote, tick = "", 0, "", False
    for i, ch in enumerate(rest):  # each substitution, nested too, is a word
        outside = not depth and not tick
        if quote == "'":  # single quotes: nothing expands until they close
            quote = "" if ch == "'" else quote
        elif ch == "'" and not quote and outside:
            quote = "'"
        elif ch == '"' and outside:
            quote = "" if quote else '"'
        elif ch == "`" and not depth:
            flat, tick = (flat if tick else flat + "SUBST"), not tick
            continue
        elif ch == "(" and (depth or rest[i - 1:i] == "$"):
            flat, depth = (flat[:-1] + "SUBST") if not depth else flat, depth + 1
            continue
        elif ch == ")" and depth:
            depth -= 1
            continue
        if outside:
            flat += ch
    if depth or tick or quote:
        return set()
    lex = shlex.shlex(flat, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    words: set[str] = set()
    try:
        for word in lex:
            if word and set(word) <= set(";&|()"):
                break
            words.add(word)
    except ValueError:
        return set()
    return words


def _swallow_marker(run_text: str, pattern: str, *,
                    shell: str) -> str | None:
    """The canonical name of the first form that can swallow the gate
    command's failing exit, or None when none of the enumerated forms is
    present.

    Suffix markers are matched only from the gate pattern ONWARD on its own
    logical line, with quoted segments stripped — `x || echo ok; <gate>`
    guards x, not the gate, and a `|` inside a string argument is not a
    pipeline. A pipe after
    the gate command counts only when nothing turns pipefail on: GitHub's
    DEFAULT shell is `bash -e` with no pipefail; the exact keyword
    `shell: bash` runs `-eo pipefail`; a CUSTOM template (`bash {0}`) runs
    verbatim with neither, so it gets no pipefail credit.
    """
    bare = _QUOTED.sub("", run_text)
    for regex, name in _BLOCK_SWALLOWS:
        if regex.search(bare):
            return name
    pipefail = (shell.strip() == "bash"
                or bool(_PIPEFAIL_ON.search(bare)
                        and not _PIPEFAIL_OFF.search(bare)))
    for line in _logical_lines(run_text):
        if pattern not in line:
            continue
        tail = _QUOTED.sub("", line[line.index(pattern):])
        for regex, name in _SUFFIX_SWALLOWS:
            if regex.search(tail):
                return name
        if not pipefail and _SINGLE_PIPE.search(tail):
            return "| (no pipefail: the step exits with the last pipeline command)"
    return None


# S-04 reads two stores. A promotion RECORD under PROMOTION_SUBDIR says who
# proposed the promotion; the PROMOTE ARTIFACT it names under BACKTEST_SUBDIR
# is the backtest the promotion shipped with. They are kept apart because
# backtests are append-only: provenance written down later must not rewrite
# the evidence a past decision was judged on.
#
# The loop is: the corpus shows a class, a retro proposes the rule, the rule's
# counts clear the bar, a person merges it with its backtest. A rule written
# by a synthesis pass or by hand can be a good rule, but it did not come
# through the loop. Nothing here can prove a retro ran: `proposal` must cite
# where the proposal is recorded, and a reviewer reads it.
PROMOTION_SUBDIR = ".warden/memory/promotions"
LOOP_PROPOSER = "retro"
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PLAIN_JSON_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.json")


def _count(value: object) -> int | None:
    # `isinstance(True, int)` is True, so a boolean is refused by name
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _promotion_refusal(root: Path, name: str, record: object,
                       declared: set[str]) -> tuple[str | None, dict | None]:
    """(why this promotion record does not count, or None; its backtest)."""
    from .memory import PROMOTE_MIN_N, PROMOTE_WILSON_LB, wilson_lower_bound
    if not isinstance(record, dict):
        return f"{name}: not a promotion record (not a JSON object)", None
    rule_id = record.get("rule_id")
    if not isinstance(rule_id, str) or rule_id not in declared:
        return f"{name}: rule_id {rule_id!r} names no declared rule", None
    proposer = record.get("proposed_by")
    if not (isinstance(proposer, dict)
            and proposer.get("source") == LOOP_PROPOSER
            and isinstance(proposer.get("date"), str)
            and _ISO_DATE.fullmatch(proposer["date"])
            and isinstance(record.get("proposal"), str)
            and record["proposal"].strip()):
        return (f"{name}: {rule_id} was not proposed by a retro — the record "
                'carries no `"proposed_by": {"source": "retro", "date": '
                '"YYYY-MM-DD"}` with a `proposal` citing it'), None
    bt_name = record.get("backtest")
    if not (isinstance(bt_name, str) and _PLAIN_JSON_NAME.fullmatch(bt_name)):
        return (f"{name}: carries no backtest — `backtest` must name a promote "
                f"artifact under {BACKTEST_SUBDIR}/"), None
    bt_path = root / BACKTEST_SUBDIR / bt_name
    try:
        doc = json.loads(bt_path.read_text())
    except FileNotFoundError:
        return (f"{name}: carries no backtest — {bt_name} does not exist "
                f"under {BACKTEST_SUBDIR}/"), None
    except (OSError, ValueError) as e:
        return f"{name}: carries no backtest — {bt_name} is unreadable ({e})", None
    if (not isinstance(doc, dict) or doc.get("action") != "promote"
            or doc.get("rule_id") != rule_id):
        return (f"{name}: carries no backtest — {bt_name} is not a promote "
                f"artifact for {rule_id}"), None
    judged, upheld = _count(doc.get("judged")), _count(doc.get("upheld"))
    missing = [field for field, ok in (
        ("judged", judged is not None),
        ("upheld", upheld is not None and judged is not None
         and upheld <= judged),
        ("window", isinstance(doc.get("window"), str)
         and doc["window"].strip()),
        ("derived_from", isinstance(doc.get("derived_from"), str)
         and doc["derived_from"].strip())) if not ok]
    if missing:
        return (f"{name}: carries no backtest — {bt_name} has "
                f"{', '.join(missing)} missing or malformed, and those fields "
                "record what the promotion's counts were derived over"), None
    floor = round(wilson_lower_bound(upheld, judged), 3)
    if judged < PROMOTE_MIN_N or floor < PROMOTE_WILSON_LB:
        return (f"{name}: {rule_id} never cleared the promotion bar ({judged} "
                f"judged, {upheld} upheld, floor {floor} in {bt_name}; the bar "
                f"is {PROMOTE_MIN_N}+ judged AND a {PROMOTE_WILSON_LB} floor)"
                ), None
    return None, doc


def promotions_through_the_loop(root: Path
                                ) -> tuple[list[tuple[str, dict, dict]],
                                           list[str]]:
    """(promoted, refused): each promotion record that counts, with its record
    and its promote artifact, and why each other record does not.

    Raises ValueError when the ruleset the records must name cannot be
    resolved or read: matching them against a guessed ruleset proves nothing.
    """
    from .rules import RuleError, load_rules
    rules_dir, dir_problem = _rules_dir(root)
    if dir_problem:
        raise ValueError(f"rules_dir declaration cannot be resolved "
                         f"({dir_problem}) — refusing to match promotion "
                         f"records against the default {rules_dir.name}")
    try:
        declared = ({r.id for r in load_rules(rules_dir)}
                    if rules_dir.is_dir() else set())
    except (RuleError, OSError) as e:
        raise ValueError(f"ruleset unreadable: {str(e)[:120]}") from e
    promoted: list[tuple[str, dict, dict]] = []
    refused: list[str] = []
    for path in sorted((root / PROMOTION_SUBDIR).glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            refused.append(f"{path.name}: unreadable ({e})")
            continue
        why, backtest = _promotion_refusal(root, path.name, record, declared)
        if why:
            refused.append(why)
        else:
            promoted.append((path.name, record, backtest))
    return promoted, refused


def _run_check(check: dict, root: Path) -> tuple[bool, str]:
    kind = check["type"]
    path = check.get("path", "")

    if kind == "file_exists":
        p = root / path
        return p.is_file(), f"{path}: {'found' if p.is_file() else 'missing'}"
    if kind == "file_absent":
        p = root / path
        return not p.exists(), f"{path}: {'absent' if not p.exists() else 'present'}"
    if kind == "dir_exists":
        p = root / path
        return p.is_dir(), f"{path}: {'found' if p.is_dir() else 'missing'}"
    if kind == "glob_min":
        hits = list(root.glob(path))
        need = int(check.get("min", 1))
        return len(hits) >= need, f"{path}: {len(hits)} match(es), need {need}"
    if kind in ("file_contains", "any_file_contains"):
        from . import gate_workflows
        pattern = re.compile(check["pattern"], re.MULTILINE)
        folder, _, name = path.rpartition("/")
        if kind == "file_contains":
            files = [root / path]
        elif folder == gate_workflows.WORKFLOWS_DIR.as_posix():
            # G-04, V-02: only workflows GitHub runs; below the git root, the
            # root gate that names this enrollment.
            files = gate_workflows.enrollment_workflows(root, (name,))
        else:
            files = sorted(root.glob(path))
        for f in files:
            try:
                if pattern.search(f.read_text()):
                    return True, f"{f.name}: matched {check['pattern']!r}"
            except OSError:
                continue
        detail = f"{path}: no file matched {check['pattern']!r}"
        if kind == "any_file_contains" and folder == gate_workflows.WORKFLOWS_DIR.as_posix():
            skipped = gate_workflows.skipped_gates(root, (name,))
            if skipped:
                detail += " (" + "; ".join(
                    f"the root `{f.name}` was skipped {why}" for f, why in skipped) + ")"
        return False, detail
    if kind == "not_tracked":
        # Derived files must not be committed: they are rebuilt from the
        # source of truth, so tracking them means every branch that
        # regenerates one conflicts with every other branch that did.
        import subprocess
        try:
            proc = subprocess.run(["git", "ls-files", "--error-unmatch", path],
                                  cwd=root, capture_output=True)
        except (OSError, FileNotFoundError):
            return True, f"{path}: no git available to check"
        tracked = proc.returncode == 0
        return not tracked, (f"{path}: TRACKED — it is derived and will conflict "
                             "on every concurrent branch; gitignore it and "
                             "`git rm --cached` it" if tracked
                             else f"{path}: not tracked (correct)")
    if kind == "policy_disciplines_valid":
        target = root / path
        if not target.is_file():
            # R-01 already owns "the policy file exists"; do not double-fail
            return True, f"{path}: no policy file (covered by the contract check)"
        ok, detail = check_disciplines(target.read_text())
        return ok, f"{path}: {detail}"

    if kind == "policy_repair_budget_capped":
        # Through the shared reader: a direct read of repo.yaml is refused
        # package-wide (it parks forever on a FIFO). The key is required at
        # Level 3, so a missing or unreadable repo.yaml fails here rather
        # than passing as covered by G-01.
        from . import config as config_mod
        try:
            doc = yamlio.load(
                config_mod.read_repo_yaml(root).decode("utf-8"))
        except FileNotFoundError:
            return False, (f"{path}: missing, so no {REPAIR_BUDGET_KEY} is "
                           f"declared")
        except (OSError, UnicodeDecodeError, config_mod.ConfigError,
                yaml.YAMLError) as e:
            return False, (f"{path}: could not be read ({e}), so no "
                           f"{REPAIR_BUDGET_KEY} can be read")
        ok, detail = check_repair_budget(doc)
        return ok, f"{path}: {detail}"

    if kind == "graph_valid":
        from . import graph as graph_mod
        try:
            graph_mod.validate(graph_mod.load(root), root)
            return True, "graph.yaml validates"
        except graph_mod.GraphError as e:
            return False, f"graph invalid: {str(e)[:120]}"

    if kind == "light_round_enforced":
        # R-14: a declared light round needs the CI step that
        # recomputes its verdict, read with E-01's parser. None declared
        # passes; a graph.yaml that cannot say fails, never reads as "none".
        from . import graph as graph_mod
        try:
            crew = graph_mod.declared_crew(root)
        except graph_mod.GraphError as e:
            return False, (f"graph.yaml's review crew cannot be resolved, so "
                           f"whether it declares a light round is unknown: "
                           f"{str(e)[:120]}")
        light = (crew or {}).get("light")
        if not light:
            return True, "graph.yaml declares no light round: nothing to bind"
        ok, detail = _run_check({**check, "type": "ci_step_enforced"}, root)
        roles = ", ".join(light["roles"])
        return ok, f"light round ({roles}) declared; {detail}"

    if kind == "ci_step_enforced":
        # E-01/E-03 depth: a grep would only see that a workflow SAYS
        # the command; this parses the workflows and verifies SOME step can
        # actually gate — not continue-on-error (step OR job level), in a
        # workflow triggered on the required event. Every match is examined
        # (an advisory copy elsewhere must not veto an enforced one), and an
        # unreadable workflow is reported without hiding a valid match in a
        # sibling file. NOT evaluated (documented in the baseline): if:
        # expressions, trigger branch/path filters, reusable-workflow
        # (`uses:`) indirection, and a bare `exit 0` on its own line — that
        # form is how a legitimate conditional early-exit is written (this
        # repo's fork-PR exemption), so flagging it would poison the one
        # honest use; only the forms _swallow_marker enumerates are claimed.
        from . import gate_workflows
        pattern, event = check["pattern"], check.get("event", "pull_request")
        # `flags`: options the command must carry, read as the command's own
        # words (`_own_words`) in any order and across a `\` continuation —
        # never as part of the pattern, where the order a consumer wrote its
        # arguments in would decide the rung.
        flags = check.get("flags", [])
        shown = " ".join([pattern, *flags])
        problems: list[str] = []
        workflows = gate_workflows.enrollment_workflows(root, ("*.yml", "*.yaml"))
        for wf in workflows:
            try:
                doc = yamlio.load(wf.read_text())
            except (OSError, yaml.YAMLError) as e:
                problems.append(f"{wf.name}: unreadable workflow ({e})")
                continue
            if not isinstance(doc, dict):
                continue
            # YAML 1.1 parses a bare `on:` key as boolean True
            triggers = doc.get("on", doc.get(True, {}))
            if isinstance(triggers, str):
                trigger_names = {triggers}
            elif isinstance(triggers, list):
                # array-form `on:` accepts only event-name strings; a dict
                # entry is a workflow GitHub itself rejects — never counted
                trigger_names = {t for t in triggers if isinstance(t, str)}
            elif isinstance(triggers, dict):
                trigger_names = {k for k in triggers if isinstance(k, str)}
            else:
                trigger_names = set()
            jobs = doc.get("jobs")
            if not isinstance(jobs, dict):
                continue
            for job in jobs.values():
                if not isinstance(job, dict):
                    continue
                for step in job.get("steps") or []:
                    if not isinstance(step, dict) or \
                            pattern not in str(step.get("run", "")):
                        continue
                    if flags and not any(
                            pattern in line and set(flags) <= _own_words(
                                line.split(pattern, 1)[1])
                            for line in _logical_lines(str(step["run"]))):
                        continue
                    run_text = str(step.get("run", ""))
                    swallowed = _swallow_marker(
                        run_text, pattern,
                        shell=str(step.get("shell", "")))
                    if swallowed:
                        problems.append(
                            f"{wf.name}: the '{shown}' command's exit can be "
                            f"shell-swallowed (`{swallowed}`) — the step "
                            "cannot be shown to gate; let the command's exit "
                            "code stand")
                    elif step.get("continue-on-error"):
                        problems.append(
                            f"{wf.name}: the '{shown}' step is "
                            "continue-on-error — an advisory step cannot "
                            "gate; remove continue-on-error to certify")
                    elif job.get("continue-on-error"):
                        problems.append(
                            f"{wf.name}: the job holding '{shown}' is "
                            "continue-on-error — an advisory job cannot "
                            "gate; remove continue-on-error to certify")
                    elif event not in trigger_names:
                        problems.append(
                            f"{wf.name}: '{shown}' found, but the workflow "
                            f"is not triggered on {event} — add it to `on:`")
                    else:
                        return True, (f"{wf.name}: '{shown}' runs enforced "
                                      f"on {event}")
        if problems:
            more = f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""
            return False, "; ".join(problems[:3]) + more
        return False, (f"no workflow step runs '{shown}' — add it to a "
                       f"job triggered on {event}")

    if kind == "corpus_min_reviews":
        # E-04 depth: count the reviews the evidence pipeline itself
        # counts — memory.review_events, the single definition of what a
        # review event IS — never raw files (five '{}' files are zero
        # reviews).
        from . import memory as memory_mod
        tally = memory_mod.review_events(root)
        need = int(check.get("min", 1))
        ok = tally["total"] >= need
        shard_files = len(list(
            (memory_mod.memory_dir(root) / memory_mod.SHARD_SUBDIR).glob("*.json")))
        notes = []
        if tally["unreadable"]:
            notes.append(f"{tally['unreadable']} unreadable file(s) in the "
                         "shard dir — not counted; fix or remove them")
        skipped = shard_files - tally["total"] - tally["unreadable"]
        if skipped > 0:
            notes.append(f"{skipped} shard(s) skipped (not source=attest "
                         "review events) — only orchestrated reviews count")
        suffix = (", " + "; ".join(notes)) if notes else ""
        return ok, f"{tally['total']} review event(s), need {need}{suffix}"

    if kind == "attest_rule_ids_resolve":
        # Same validator as `attest write` and `memory ingest` — a third
        # caller, never a third definition (the promotion gate keys on
        # rule_id, so certify must agree with the writing end byte-for-byte).
        # Shard ACCEPTANCE mirrors memory.review_events, field for field: a
        # file that the evidence pipeline cannot count as a review must not
        # count toward certification either.
        from .attest import AttestError, _check_rule_ids
        from . import memory as memory_mod
        shards = sorted(
            (memory_mod.memory_dir(root) / memory_mod.SHARD_SUBDIR).glob("*.json"))
        rules_dir, dir_problem = _rules_dir(root)
        if dir_problem:
            # Fail closed: a declaration that exists but
            # cannot be resolved must never be scored as "declares nothing"
            # — a stale ruleset at the default would resolve every rule_id
            # against the wrong ruleset.
            # Checked before the no-shards early return: an unresolvable declaration
            # refuses to make ANY ruleset-keyed claim, shards or none —
            # passing E-02 while S-04/S-05 refuse would split one cause
            # into two verdicts.
            return False, (f"rules_dir declaration cannot be resolved "
                           f"({dir_problem}) — refusing to resolve rule_ids "
                           f"against the default {rules_dir.name}")
        if not shards:
            return True, ("no attest shards yet — nothing to resolve "
                          "(corpus size is E-04's check)")
        if not rules_dir.is_dir():
            return False, (f"{len(shards)} shard(s) but no rules dir at "
                           f"{rules_dir.name} — rule_ids cannot resolve")
        grandfathered = _grandfathered_rule_ids(root)
        grandfathered_hits: set[str] = set()
        for shard in shards:
            # The READ and the ENVELOPE, through the same definition the
            # other three readers call, so a non-UTF-8 shard (a
            # UnicodeDecodeError, not a JSONDecodeError) is refused by name
            # here exactly as `records_from_shards` names it.
            try:
                doc = memory_mod.read_shard(shard)
            except ValueError as e:
                return False, str(e)
            if not isinstance(doc.get("sha"), str) or not doc["sha"]:
                # The message says what the shard IS, not what another
                # reader would do: `review_events` reads a shard whose `sha`
                # is present-but-empty or non-string without complaint, then
                # SKIPS it as "not an orchestrated review" — `unreadable`
                # stays 0. Only an ABSENT `sha` reaches its unreadable bucket.
                return False, (f"{shard.name}: not an evidence shard — its "
                               f"`sha` is {doc.get('sha')!r}, so nothing can "
                               "say which commit this review judged")
            # The RECORDS ride inside `read_shard` above — same validator,
            # one call — so each record is a mapping with a string `rule_id`
            # before `r.get("rule_id")` runs. Without it a `records` list of
            # strings would raise a bare AttributeError, and a non-string
            # `rule_id` would be reported as "ruleset unreadable" when one
            # committed shard is the problem. E-02 runs BEFORE S-04, so this
            # is where a consumer meets the failure first.
            records = []
            for r in doc["records"]:
                if r.get("rule_id") in grandfathered:
                    grandfathered_hits.add(r["rule_id"])
                else:
                    records.append(r)
            try:
                _check_rule_ids(records, rules_dir)
            except AttestError as e:
                # A rule that answers a candidate class invalidates the whole
                # history of that class at once, so report the class and its
                # remedy rather than the first shard that tripped.
                remedy = _shadow_remedy(shards, rules_dir, grandfathered,
                                        e.rule_id)
                if remedy:
                    return False, remedy
                # Not truncated: any cap can cut the `Remedy:` part of the
                # message. The message is bounded by the ruleset and this is
                # a failure path: a long detail is not a defect, a detail
                # missing its remedy is.
                return False, f"{shard.name}: {e}"
            except Exception as e:  # unreadable ruleset: fail closed
                return False, f"ruleset unreadable: {str(e)[:120]}"
        note = (" (grandfathered, declared in the overlay: "
                + ", ".join(sorted(grandfathered_hits)) + ")"
                if grandfathered_hits else "")
        return True, f"every rule_id in {len(shards)} shard(s) resolves{note}"

    if kind == "rule_promoted_through_loop":
        # S-04. "Self-improving" means the loop has changed a rule, not that
        # it could: a candidate over the promotion bar is a class nobody has
        # written a rule for yet. So this counts a promotion record whose rule
        # a retro proposed, naming the promote artifact it shipped with, whose
        # counts cleared the bar. The PROVENANCE is taken as recorded and so
        # are the counts; like S-05, this re-derives neither, and nothing here
        # can prove a retro ran. The pass detail says so, because that line is
        # what a reader sees beside "Self-improving".
        from .memory import (PROMOTE_MIN_N, PROMOTE_WILSON_LB,
                             wilson_lower_bound)
        try:
            promoted, refused = promotions_through_the_loop(root)
        except ValueError as e:
            return False, str(e)
        if promoted:
            name, record, doc = promoted[0]
            floor = round(wilson_lower_bound(doc["upheld"], doc["judged"]), 3)
            others = (f"; {len(promoted) - 1} more" if len(promoted) > 1
                      else "")
            unread = (f"; {len(refused)} other record(s) do not count: "
                      + "; ".join(refused[:2]) if refused else "")
            return True, (f"{name}: {record['rule_id']} proposed by the retro "
                          f"of {record['proposed_by']['date']}, promoted with "
                          f"{record['backtest']} at {doc['judged']} judged, "
                          f"floor {floor}{others}{unread} (provenance and "
                          "counts as recorded, not verified)")
        if refused:
            more = f" (+{len(refused) - 3} more)" if len(refused) > 3 else ""
            return False, ("no promotion record counts as a rule promoted "
                           "through the loop: " + "; ".join(refused[:3])
                           + more)
        return False, (f"no promotion record under {PROMOTION_SUBDIR}/ — a "
                       "candidate over the promotion bar is not a promotion. "
                       "Level 5 needs a rule a retro proposed, whose promote "
                       f"artifact under {BACKTEST_SUBDIR}/ cleared the bar "
                       f"({PROMOTE_MIN_N}+ judged AND a {PROMOTE_WILSON_LB} "
                       "floor)")

    if kind == "promotion_bar_met":
        # Not a baseline rung since S-04 moved to `rule_promoted_through_loop`;
        # kept as a type an overlay can still declare.
        # "Self-improving" means the corpus can carry a promotion decision:
        # at least one declared rule OR candidate class holds N>=10 judged
        # cases at a 0.7 Wilson floor — the retro's own bar, computed by the
        # same code that computes it for `memory stats`. The retro's OTHER
        # bars bind here too: a candidate the retro would refuse (covered,
        # or on a refutation streak) must not certify the rung, and an
        # unreadable ruleset fails closed exactly as stats does for
        # promotable.
        from . import memory as memory_mod
        from . import tags as tags_mod
        try:
            records = memory_mod.records_from_shards(root)
        except ValueError as e:
            return False, f"corpus unreadable — {str(e)[:140]}"
        # CANNOT EVALUATE, the third of the three this rung already has — see
        # `rules_dir` below and `rules_error` further down. A `tags:` or
        # `aliases:` block that is not a mapping loads as `{}` in
        # `load_aliases` (deliberately: it is the permissive reader every
        # folding seam calls), so `canonicalize_records` early-returns on the
        # empty table and `records_from_shards` hands this rung an UNFOLDED
        # corpus. Two declared spellings of one class would then split n and
        # the Wilson floor — a promotion bar computed over the wrong
        # denominator — while `memory check-vocabulary` exits 2 on the same
        # tree saying it cannot evaluate the vocabulary.
        #
        # This does not change what the rung REQUIRES. It keeps the rung from
        # ANSWERING a question it could not evaluate, which is the same
        # fail-closed direction the two arms below already take, in the same
        # words ("refusing to score the promotion bar against the default").
        # `fold_problems`, not `block_problems`: `load_aliases` returns the
        # same empty fold table for an unparseable, non-mapping or unreadable
        # FILE as for a bad BLOCK, so asking only about the block would score
        # the rung over a corpus whose declared aliases folded nothing.
        #
        # The DETAIL says "cannot be applied", not "unfolded corpus": a refused
        # `tags:` block leaves the alias fold perfectly correct, and asserting a
        # broken fold there would name the wrong consequence on a surface whose
        # job is telling an author which line to fix.
        fold_problems = tags_mod.fold_problems(root)
        if fold_problems:
            return False, (
                "the declared vocabulary cannot be applied to this corpus, so "
                "it cannot be folded as declared — refusing to score the "
                "promotion bar over it: "
                f"{'; '.join(fold_problems)[:220]}")
        rules_dir, dir_problem = _rules_dir(root)
        if dir_problem:
            # fail closed: the promotion bar is computed
            # against a ruleset; a guessed one is the wrong ruleset
            return False, (f"rules_dir declaration cannot be resolved "
                           f"({dir_problem}) — refusing to score the "
                           f"promotion bar against the default")
        s = memory_mod.stats(root, rules_dir, records=records)
        if s["rules_error"]:
            return False, f"ruleset unreadable: {str(s['rules_error'])[:120]}"
        # Named in the detail, so it reaches certification.json: this rung
        # is scored over FINDINGS, not records — a finding re-attested under
        # a moved head counts once — so n and the Wilson floor can move on a
        # corpus that did not change, and a consumer bumping its platform pin
        # can cross this bar in either direction. Without the count the only
        # visible symptom is a rung moving with no cause named.
        folded = s.get("restatements_folded") or 0
        note = (f" ({folded} restatement(s) folded — scored over findings, "
                "not records)" if folded else "")
        if s["promotable"]:
            return True, (f"rule {s['promotable'][0]} clears the promotion "
                          f"bar{note}")
        over = memory_mod.promotion_candidates(s)
        if over:
            row = over[0]
            return True, (f"candidate {row['slug']} clears the promotion "
                          f"bar (n={row['n']}, floor {row['wilson_lb']})"
                          f"{note}")
        return False, ("no rule or candidate clears the promotion bar "
                       f"(needs {memory_mod.PROMOTE_MIN_N}+ judged cases AND "
                       f"a {memory_mod.PROMOTE_WILSON_LB} confidence floor) — "
                       f"keep reviewing and ingesting{note}")

    if kind == "rule_change_backtested":
        from .rules import RuleError, load_rules, rules_version
        rules_dir, dir_problem = _rules_dir(root)
        if dir_problem:
            # fail closed: a backtest is bound to a ruleset;
            # matching it against a guessed one proves nothing
            return False, (f"rules_dir declaration cannot be resolved "
                           f"({dir_problem}) — refusing to match backtests "
                           f"against the default {rules_dir.name}")
        try:
            loaded = tuple(load_rules(rules_dir)) if rules_dir.is_dir() else ()
            declared = {r.id for r in loaded}
            # The one rule change the COMMITTED TREE can prove on its own: the
            # frontmatter field is right there. Everything
            # else — a body edit, a severity move — needs the previous ruleset
            # to detect, and the platform keeps no ruleset history by ruling,
            # so the binding below covers pauses and
            # says so rather than implying more.
            paused_ids = {r.id for r in loaded if r.paused}
            current_rv = rules_version(rules_dir, root) if rules_dir.is_dir() else ""
        except RuleError as e:
            # fail closed: with no readable ruleset, no backtest can be
            # shown to name a declared rule at the current version
            return False, f"ruleset unreadable: {str(e)[:120]}"
        artifacts = sorted((root / BACKTEST_SUBDIR).glob("*.json"))
        if not artifacts:
            from .memory import PAUSE_STREAK
            routes = "; ".join(backtest_route_clause(r)
                               for r in backtest_routes(PAUSE_STREAK))
            return False, (f"no backtest artifact under {BACKTEST_SUBDIR}/ — "
                           f"a rule change ({'/'.join(BACKTEST_ACTIONS)}) must "
                           "carry one before the org counts as self-improving. "
                           f"{routes}, as the retro and rule-advisor skills "
                           "describe")
        # Three bindings:
        #  1. A backtest is BOUND to the ruleset it judged: it must name the
        #     CURRENT rules_version. A rule change bumps that version, so a
        #     stale backtest (a different version, or none) no longer
        #     satisfies S-05 — "some change once was backtested" is not "this
        #     change is".
        #  2. A malformed artifact is a HARD problem reported regardless of a
        #     later valid one — discarding accumulated problems on the first
        #     success would be a sort-order-dependent verdict that lets a
        #     corrupt artifact earlier be masked by a valid one later.
        #  3. A current-version backtest is bound to a rule change the TREE
        #     SHOWS, not just to the ruleset. Accepting any current-version
        #     artifact naming any declared rule would let one artifact
        #     evidence the whole ruleset: an auto-pause of rule A would
        #     satisfy S-05 for an unrelated human edit of rule B bundled into
        #     the same rules_version, and `warden autonomy pause` produces
        #     exactly such artifacts. A pause is the one
        #     change the committed tree can prove alone, so pauses are bound
        #     both ways — a pause artifact stamped with the CURRENT version
        #     must name a rule the ruleset shows paused, and every paused rule
        #     must carry a pause artifact of its own at ANY version. The second
        #     is existence, not freshness, deliberately: rules_version moves for
        #     unrelated reasons, so demanding a fresh artifact per standing
        #     pause would de-evidence pauses that never changed (see the
        #     collection point below). The other changes whose outcome the
        #     tree shows are bound the same way at the current version: a
        #     retire names a rule the tree no longer declares and git history
        #     shows declared, an unpause a rule not paused, a demote a rule
        #     outside blocking_severities. Other change classes stay unbound,
        #     and the pass line says so rather than implying a coverage the
        #     check does not have.
        problems: list[str] = []
        matched = None
        evidenced_pauses: set[str] = set()
        # A retire's rule is gone from the tree, so its id is read from git
        # history, once and only when a retire needs it.
        history: (tuple[frozenset[str], bool, tuple[str, ...],
                        tuple[str, ...]] | str | None) = None
        blocking: tuple[frozenset[str], str] | None = None
        bound_retires: set[str] = set()
        rules_rel = Path(os.path.relpath(rules_dir, root)).as_posix()
        for artifact in artifacts:
            try:
                doc = json.loads(artifact.read_text())
            except (OSError, json.JSONDecodeError) as e:
                problems.append(f"{artifact.name}: unreadable ({e})")
                continue
            if not isinstance(doc, dict):
                problems.append(f"{artifact.name}: not a backtest record")
                continue
            if doc.get("action") not in BACKTEST_ACTIONS:
                problems.append(f"{artifact.name}: action must be one of "
                                f"{', '.join(BACKTEST_ACTIONS)}")
                continue
            removed = False
            if doc.get("rule_id") not in declared:
                if doc.get("action") != "retire":
                    problems.append(f"{artifact.name}: rule_id "
                                    f"{doc.get('rule_id')!r} names no "
                                    "declared rule")
                    continue
                # Retiring a rule deletes its file, so the rule a retire names
                # is one an earlier ruleset declared, and git shows which.
                if history is None:
                    from .diffs import DiffError
                    try:
                        history = _committed_rule_ids(root, rules_dir)
                    except (DiffError, OSError, ValueError) as e:
                        history = str(e)[:120]
                if isinstance(history, str):
                    problems.append(
                        f"{artifact.name}: rule_id {doc.get('rule_id')!r} "
                        "names no declared rule, and the git history that "
                        f"would show it retired cannot be read ({history})")
                    continue
                if doc.get("rule_id") not in history[0]:
                    shallow = (" — this clone is shallow, so its history may "
                               "end before the rule was retired: fetch the "
                               "full history (`git fetch --unshallow`)"
                               if history[1] else "")
                    # A symlink's blob is its target path, so the id it held
                    # cannot be read; naming the skipped files is the cause.
                    more = len(history[2]) - 3
                    linked = (" — rule files committed as symlinks are not "
                              f"followed, and history holds {len(history[2])}"
                              f": {', '.join(history[2][:3])}"
                              + (f" and {more} more" if more > 0 else "")
                              if history[2] else "")
                    unread = "".join(f" — {n}" for n in history[3])
                    problems.append(
                        f"{artifact.name}: rule_id {doc.get('rule_id')!r} "
                        "names no declared rule, and no rule file committed "
                        f"under {rules_rel} or an earlier rules_dir declares "
                        f"it{shallow}{unread}{linked}")
                    continue
                removed = True
            # `isinstance(True, int)` is True in Python, so `"judged": true`
            # would otherwise read as a case count.
            if (not isinstance(doc.get("judged"), int)
                    or isinstance(doc.get("judged"), bool)):
                problems.append(f"{artifact.name}: no judged case count")
                continue
            # A STANDING pause needs evidence, not FRESH evidence. Collected
            # before the rules_version skip on purpose: rules_version is a hash
            # over every rule file plus the repo.yaml review subtree, so ANY
            # later ruleset edit invalidates the artifact of a pause that never
            # changed. Requiring a current-version artifact per paused rule would
            # make every consumer holding a pause drop a level the next time
            # they touched rules at all, with the only remedy being a
            # hand-written artifact for a change that did not happen in that
            # revision — "evidence for a change the tree does not contain",
            # which is what the arm below exists to refuse.
            if doc.get("action") == "pause" and doc["rule_id"] in paused_ids:
                evidenced_pauses.add(doc["rule_id"])
            # A well-formed backtest whose rules_version differs (or is
            # absent) is not a defect — it is evidence for an earlier ruleset,
            # kept as history. It simply is not the match S-05 needs.
            if doc.get("rules_version") != current_rv:
                continue
            # A promote artifact at the CURRENT rules_version is the live
            # promotion claim, and the claim carries its validation:
            # micro-test evidence or an explicit
            # unvalidated marker. Checked for EVERY current promote artifact
            # — a problem here cannot be masked by a valid artifact beside
            # it, exactly like a corrupt one. History (older versions,
            # skipped above) is judged by the bar of its day, and the other
            # actions only quiet the gate or edit a body, so neither is held
            # to a promotion bar.
            if doc.get("action") == "promote":
                bad = _promotion_validation_problem(doc)
                if bad:
                    problems.append(f"{artifact.name}: {bad}")
                    continue
            if doc.get("action") == "pause":
                # Stamped against THIS ruleset while the rule it names is not
                # paused in it: the artifact is evidence for a change the tree
                # does not contain. A pause that was later lifted is not this
                # case — unpausing bumps rules_version, so its artifact is
                # history and was skipped above.
                if doc["rule_id"] not in paused_ids:
                    problems.append(
                        f"{artifact.name}: claims a pause of "
                        f"{doc['rule_id']!r} at the current rules_version, but "
                        "that rule is not paused in the ruleset — a backtest "
                        "evidences a change the tree shows, not one it does "
                        "not")
                    continue
            # The pause arm's mirror for the other changes whose outcome the
            # tree shows. A retire is judged by that outcome, never by which
            # commit removed the rule: a change split into commits and the
            # same change squashed must get one verdict.
            outcome = ""
            if doc.get("action") == "retire":
                if removed:
                    bound_retires.add(doc["rule_id"])
                else:
                    outcome = "that rule is still declared in the ruleset"
            elif (doc.get("action") == "unpause"
                    and doc["rule_id"] in paused_ids):
                outcome = "that rule is still paused in the ruleset"
            elif doc.get("action") == "demote":
                if blocking is None:
                    blocking = _declared_blocking_severities(root)
                severity = next(r.severity for r in loaded
                                if r.id == doc["rule_id"])
                if blocking[1]:
                    outcome = ("its blocking_severities cannot be read: "
                               f"{blocking[1]}")
                elif severity in blocking[0]:
                    outcome = (f"that rule's severity {severity} is still in "
                               "blocking_severities")
            if outcome:
                problems.append(
                    f"{artifact.name}: {doc['action']} of {doc['rule_id']!r} "
                    f"is stamped with the current rules_version, but "
                    f"{outcome} — a backtest evidences a change the tree "
                    "shows, not one it does not")
                continue
            if matched is None:
                matched = (artifact.name, doc)
        if problems:
            # A corrupt artifact cannot be masked by a valid one later.
            return False, "; ".join(problems[:3])
        unevidenced = sorted(paused_ids - evidenced_pauses)
        if unevidenced:
            # Reported before the "no match" arm: a paused rule with no
            # artifact of its own is a NAMED gap, and falling through to the
            # generic message would send the reader looking for a stale
            # rules_version instead.
            return False, (
                f"{len(unevidenced)} paused rule(s) carry no pause backtest "
                f"under {BACKTEST_SUBDIR}/ at any rules_version: "
                f"{', '.join(unevidenced)}. A pause is a rule change, so it "
                "carries its own artifact — one artifact cannot evidence a "
                "second pause bundled beside it. `warden autonomy pause` "
                "writes the rule edit and the artifact together; a HAND pause "
                "needs the artifact written by hand, naming that rule_id with "
                '"action": "pause" and the counts it was derived from. An '
                "existing artifact at an older rules_version still counts: a "
                "standing pause needs evidence, not fresh evidence")
        if matched is None:
            return False, (
                f"no backtest names the current rules_version {current_rv} — "
                "every committed backtest is for a different (or unstamped) "
                "ruleset, so this rule change carries no fresh evidence; "
                "re-derive a backtest and stamp the current rules_version")
        name, doc = matched
        # The pass says what it bound. S-05 proves every PAUSE in the ruleset
        # carries its OWN artifact, and that a current-version pause artifact
        # names a rule the tree shows paused. It does NOT prove that every rule
        # changed in a diff carries one — detecting the other change classes
        # needs a previous ruleset and the platform keeps no ruleset history
        # — and it does not audit an artifact's
        # arithmetic. Stating the bound is the difference between a check and
        # an enforcement claim.
        bound = (f"{len(evidenced_pauses)} pause(s) bound"
                 if evidenced_pauses else "no pause to bind")
        if bound_retires:
            bound += (f", {len(bound_retires)} retire(s) bound to a rule git "
                      "history declared and the tree does not")
        # An adoption has no judged history by construction, so the generic
        # phrasing would read "backtested over 0 judged case(s)" — a
        # measurement claim for a measurement nobody made.
        measured = (f"backtested over {doc['judged']} judged case(s)"
                    if doc["judged"] else "recorded with no judged history")
        return True, (f"{name}: {doc['action']} of {doc['rule_id']} {measured} "
                      f"at rules_version "
                      f"{current_rv} ({bound}; other change classes are not "
                      "bound to the diff)")

    # cage checks — the runner lives outside the repo by design
    claimants, present = _cage_candidates(root)
    here = root.resolve()
    unreadable = [p for p in present if p.parent != here and _cage_unreadable(p)]
    cage = _cage_config(root)
    # Two cages declaring one checkout: the first in convention order could
    # be the strict one while the other — post-check disabled, say — is the
    # one that actually fires. Certify is the only tool positioned to see
    # both, so it says so instead of picking.
    #
    # An unreadable sibling counts the same way: it cannot be DISPROVED as
    # this checkout's cage, and a rendered `run.sh` may still fire from it.
    # The test is therefore whether certify CHOSE it — not whether a
    # claimant exists. Gating on `claimants` would leave the no-claimant case
    # open. `_cage_candidates` lists the `<checkout>-cage/` slot first, so with
    # no claimant whichever sibling sits there is chosen: a readable sibling
    # there declaring no owner passes, and the badge would be earned from it
    # while an unreadable sibling in the `../cage/` slot appeared in no
    # check's detail.
    #
    # NON-DISPROVABLE IS THE TEST, NOT UNREADABLE (the ruling is a
    # `warden decide` shard). A readable sibling declaring NO OWNER "cannot be
    # disproved and passes as before", which makes it exactly as
    # non-disprovable as an unreadable one. Counting only UNREADABLE unchosen
    # siblings would let TWO readable no-owner siblings earn the badge from
    # the slot-1 one while naming the slot-2 one in no check's detail, and
    # two no-owner siblings can declare DIFFERENT `[gate] forbidden_paths`,
    # which is the hazard the two-claimant refusal exists for. A LONE
    # non-disprovable candidate still passes.
    # A CLAIMANT beside a non-disprovable sibling refuses too, and that is the
    # same rule rather than a new one: a claimant beside an UNREADABLE sibling
    # refuses, and "declares no owner" is the same doubt through a second
    # door. A `warden decide` shard records this population, and a test pins
    # it.
    undisprovable = [p for p in present
                     if p.parent != here and _cage_owner(p) is None]
    unruled = [p for p in undisprovable if p != cage]
    if len(claimants) > 1 or unruled:
        chosen = claimants if len(claimants) > 1 else [p for p in (cage,) if p]
        names = [f"{p.parent.name}/"
                 + _cage_doubt(p, unreadable, undisprovable, here)
                 for p in chosen + unruled]
        return False, ("cages at " + " and ".join(names)
                       + " — one cage per checkout, and certify cannot tell "
                       "which of them runs")
    if cage is None:
        return False, "no cage.toml found (cage not enrolled)"
    # Discovery is by convention, so a sibling cage may belong to a DIFFERENT
    # repo. A declared owner that is not this root refuses the
    # badge; a config declaring no owner cannot be disproved and passes as
    # before.
    owner = _cage_owner(cage)
    if owner is not None and owner != root.resolve():
        return False, (f"sibling cage at {cage.parent.name}/ cages "
                       f"{owner} — not this repo")
    if kind == "cage_enrolled":
        return True, f"cage config at {cage}"
    if kind == "cage_file_exists":
        p = cage.parent / path
        return p.is_file(), f"{path}: {'found' if p.is_file() else 'missing'} in cage dir"
    if kind == "cage_forbidden_paths_nonempty":
        try:
            data = tomllib.loads(cage.read_text())
        except _CAGE_UNREADABLE as e:
            return False, f"unreadable cage config: {e}"
        paths = data.get("gate", {}).get("forbidden_paths", [])
        return bool(paths), f"{len(paths)} forbidden path pattern(s) declared"
    return False, f"unhandled check type {kind}"


def run(root: Path) -> dict:
    """Evaluate the whole ladder. Attained level = highest level L where
    every check at L and below passes (cumulative, no skipping)."""
    ladder = load(root)
    results: list[CheckResult] = []
    for lv in sorted(ladder["levels"]):
        for check in ladder["levels"][lv].get("checks", []):
            passed, detail = _run_check(check, root)
            results.append(CheckResult(check["id"], check["label"], lv,
                                       passed, detail))

    attained = 0
    for lv in sorted(ladder["levels"]):
        if all(r.passed for r in results if r.level == lv):
            attained = lv
        else:
            break

    # Capability badges: orthogonal to the cumulative chain. A Level-1 repo
    # with a cage earns `autonomous`; a Level-5 repo without one simply does
    # not carry the badge — neither statement moves the other.
    capabilities = {}
    for cap_name, spec in (ladder.get("capabilities") or {}).items():
        cap_checks = []
        for check in spec.get("checks", []):
            passed, detail = _run_check(check, root)
            cap_checks.append({"id": check["id"], "label": check["label"],
                               "passed": passed, "detail": detail})
        capabilities[cap_name] = {
            "name": spec.get("name", cap_name),
            "earned": bool(cap_checks) and all(c["passed"] for c in cap_checks),
            "checks": cap_checks,
        }

    return {
        "attained_level": attained,
        "levels": {str(lv): {"name": ladder["levels"][lv].get("name", ""),
                             "description": ladder["levels"][lv].get("description", "")}
                   for lv in sorted(ladder["levels"])},
        "checks": [{"id": r.id, "label": r.label, "level": r.level,
                    "passed": r.passed, "detail": r.detail} for r in results],
        "capabilities": capabilities,
    }


def render(doc: dict) -> str:
    lines = []
    for lv in sorted(doc["levels"], key=int):
        spec = doc["levels"][lv]
        rows = [c for c in doc["checks"] if c["level"] == int(lv)]
        mark = "PASS" if all(c["passed"] for c in rows) else "FAIL"
        lines.append(f"\nLevel {lv} — {spec['name']} [{mark}]")
        for c in rows:
            lines.append(f"  {'✓' if c['passed'] else '✗'} {c['id']} "
                         f"{c['label']} — {c['detail']}")
    for cap_name, cap in (doc.get("capabilities") or {}).items():
        mark = "EARNED" if cap["earned"] else "not earned"
        lines.append(f"\nCapability — {cap['name']} [{mark}]")
        for c in cap["checks"]:
            lines.append(f"  {'✓' if c['passed'] else '✗'} {c['id']} "
                         f"{c['label']} — {c['detail']}")
    attained = doc["attained_level"]
    name = doc["levels"].get(str(attained), {}).get("name", "none")
    earned = sorted(n for n, c in (doc.get("capabilities") or {}).items()
                    if c["earned"])
    lines.append(f"\ncertification: LEVEL {attained}"
                 + (f" ({name})" if attained else " — not yet gated")
                 + " · badges: " + (", ".join(earned) if earned else "none"))
    nxt = str(attained + 1)
    if nxt in doc["levels"]:
        missing = [c for c in doc["checks"]
                   if c["level"] == attained + 1 and not c["passed"]]
        lines.append(f"  to reach level {nxt} ({doc['levels'][nxt]['name']}): "
                     + ", ".join(c["id"] for c in missing))
    return "\n".join(lines) + "\n"
