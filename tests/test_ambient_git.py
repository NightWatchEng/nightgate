"""The suite must not depend on git state the runner does not share.

Two halves of one class, and a single failure shows only the first.

THE INSTANCE. A suite can be green locally and red on a CI runner on the same
sha when a fixture calls a bare `git init`: the base branch then comes from
the AMBIENT `init.defaultBranch` — `main` on a developer's Mac, `master` on
the runner — and a test that asks for `--base main` meets a repo with no such
branch. Nothing about the diff is wrong; the fixture is reading a machine.

THE CLASS is wider than the instance. It is not "the checkout is shallow" —
these fixtures build their own repos and never touch the ambient one. It
is: **a test may silently depend on any git configuration or repository state
the runner does not share.** Depth and refs are one case; `init.defaultBranch`
is another; `user.name`, `core.autocrlf` and `commit.gpgsign` are more.

So the fix is two-layered, and each layer covers what the other cannot:

1. ISOLATION (`conftest.clean_git_env`). The user and system config layers are
   pointed at /dev/null for the whole session, so no `git` the suite spawns can
   read `~/.gitconfig` or `/etc/gitconfig`. Local and CI then read the same
   (empty) configuration, which is what makes local-green and CI-green mean the
   same thing. The repository layer is deliberately untouched.

2. THE GUARD (below). Isolation removes the ambient CONFIG, not the ambient
   ASSUMPTION: a bare `git init` still takes git's BUILT-IN default, which is a
   property of the git binary on the machine and has been announced as due to
   change. A fixture that does not name its branch is still reading its
   environment, just a different part of it — so a test that starts a repo
   without pinning the branch fails loudly here, in the same shape as the
   AST guard for `repo.yaml` readers.
"""

import ast
import os
import re
import sys
import subprocess
from pathlib import Path

import pytest
from conftest import _AMBIENT_GIT_CONFIG

TESTS_DIR = Path(__file__).resolve().parent


# --------------------------------------------------------------------------
# 1 · isolation: the ambient config layers cannot reach the suite
# --------------------------------------------------------------------------

# The call forms that WRITE an environment, as opposed to reading, deleting or
# merely mentioning one. `setenv` and `delenv` are
# monkeypatch's pair and only the first belongs here; `setitem`/`delitem` are
# its mapping pair and the same rule applies; `setdefault` and `update` are the
# mapping writers this suite uses on `os.environ`; `putenv` and `__setitem__`
# are the stdlib spellings. Deliberately NOT here: `get`, `pop`, `delenv`,
# `delitem`, `print` — every one of them fires on an ungated positional scan.
#
# BUILT FROM BOTH SIDES: from the false positives an ungated scan reports AND
# from the true positives it CATCHES. A set drawn only from the false
# positives would miss `monkeypatch.setitem(os.environ, "GIT_CONFIG_GLOBAL",
# …)` — pytest's documented mapping writer, the direct sibling of the
# `setenv` this suite calls its house form — and `os.environ.__setitem__(…)`.
# Every member below
# has a FIRES case in the planted set: an unproved member is a comment, and
# an unproved member of a set that REPLACED a branch is a regression waiting.
_ENV_WRITERS = {"setenv", "setitem", "setdefault", "update", "putenv",
                "__setitem__"}


def _callee_name(node: ast.Call) -> str:
    """The bare name of what a call calls: `x.setenv(...)` -> `setenv`.

    ONE definition, shared by both sections. A second copy with an identical
    body breaks nothing visibly: the later `def` wins, which makes the
    documented copy the dead one and any future edit to it a silent no-op.
    Ruff's F811 does not see it when the first binding is referenced before
    the redefinition.

    An expression this cannot read (a call on a subscript, a lambda) returns
    "", which reads as "not a writer" in section 1 and as "callee unknown" in
    section 2 — the narrowing direction in both, and the residual
    `_ambient_key_writes` states out loud.
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _ambient_key_writes(node: ast.AST) -> list[str]:
    """The ambient-config key names this node WRITES, in any spelling.

    FOUR shapes, written out rather than summarised because each is easy to
    miss: a dict-literal key, a call KEYWORD, a call POSITIONAL constant, and
    a subscript STORE. A scan that knows only the dict literal is green over
    `dict(KEY=...)`; one that knows only keywords is green over
    `monkeypatch.setenv("KEY", ...)`, `env.setdefault("KEY", ...)` and
    `os.environ.update([("KEY", ...)])` — and `monkeypatch.setenv` is this
    suite's house form for git environment variables (tests/test_runs.py sets
    GIT_DIR that way), so it is the shape most likely to arrive next.

    WRITES, and only writes. A subscript branch that ignores `ctx` reports
    `x = os.environ["GIT_CONFIG_GLOBAL"]`, `del env["..."]` and even an
    ASSERTION reading the variable as hand-typing, under a function whose
    name says writes and an assertion message that tells the author to spread
    a constant — advice that fits a read not at all. A guard that fires on the
    obvious way to check the thing it guards teaches people to delete it.

    The CALL branch carries the same defect in another spelling: walking
    every positional argument for a constant equal to a key name, with no
    notion of write versus read, fires — all wrongly — on
    `os.environ.get("GIT_CONFIG_GLOBAL")`,
    `os.environ.pop("GIT_CONFIG_SYSTEM", None)` (the exact spelling
    `conftest.clean_git_env` itself uses), `monkeypatch.delenv(...)`,
    `assert env.get("GIT_CONFIG_GLOBAL") == os.devnull` and even
    `print("GIT_CONFIG_GLOBAL")`. So the positional branch is gated on a
    callee that actually WRITES an environment.

    The KEYWORD branch is deliberately not gated the same way: a keyword
    ARGUMENT NAMED for an ambient key is a mapping being constructed
    (`dict(GIT_CONFIG_GLOBAL=...)`) whatever the callee is, and there is no
    read, delete or print spelled that way.

    RESIDUAL, stated rather than left for the next reader to find: a write
    performed through a callee `_ENV_WRITERS` does not name — a project helper
    called `_export(...)`, say — is silent in the positional spelling. That is
    the price of the false-positive class, and it is bounded, because the three
    spellings this suite actually uses (dict literal, `dict(KEY=...)` keyword,
    subscript store) are covered by branches that need no callee at all.
    Bounded is not zero: real writes can fall into that residual unnoticed,
    so the set is drawn from what the ungated branch CAUGHT as well as from
    what it wrongly flagged, and every member carries a FIRES case.

    THE RESIDUAL IN THE OTHER DIRECTION: the Dict branch is ungated, so a dict
    LITERAL carrying an ambient key is reported wherever it appears, a read
    position included — `assert env == {"GIT_CONFIG_GLOBAL": os.devnull}`
    fires. That is the same false-positive class the subscript and call
    branches are gated against, still standing in the third spelling. It is
    left rather than fixed, and written down here rather than left to be
    rediscovered.

    Reading the constant's own keys is deliberate: any key added to
    `_AMBIENT_GIT_CONFIG` becomes scanned-for at once, without an edit here.
    """
    names = set(_AMBIENT_GIT_CONFIG)
    if isinstance(node, ast.Dict):
        return [k.value for k in node.keys
                if isinstance(k, ast.Constant) and k.value in names]
    if isinstance(node, ast.Call):
        hits = [k.arg for k in node.keywords if k.arg in names]
        # Positional too, but ONLY under a callee that writes an environment:
        # the argument may be nested (a tuple inside a list handed to
        # `update`), so the whole argument subtree is walked.
        if _callee_name(node) in _ENV_WRITERS:
            # ARGS AND KEYWORD VALUES both: `monkeypatch.setenv(name="KEY",
            # value=…)` puts the key name in a keyword VALUE, and a branch
            # that walked only positionals would be silent on it.
            for arg in list(node.args) + [k.value for k in node.keywords]:
                hits += [n.value for n in ast.walk(arg)
                         if isinstance(n, ast.Constant) and n.value in names]
        return hits
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
        s = node.slice
        return [s.value] if isinstance(s, ast.Constant) and s.value in names else []
    return []


def _unspread_closed_envs(tree: ast.AST) -> list[ast.AST]:
    """Dict literals that build a CLOSED env for a git subprocess without
    spreading `_AMBIENT_GIT_CONFIG`.

    The quiet half of the same leak the re-typing scan catches loudly. A
    fixture that hands git `env={"HOME": …, "PATH": …}` replaces os.environ, so
    the session fixture's neutralisation cannot reach it — and unlike a
    hand-typed subset, there is no key name to grep for. Without this, such a
    fixture reads /etc/gitconfig with both guards green.

    WIDE ON PURPOSE, and that is a RULING rather than an oversight. This
    reports any PATH/HOME dict without asking whether the dict is an `env=`
    argument or whether the subprocess is git, so its message can be false in
    both halves. Narrowing it loses true positives faster than it drops false
    ones: a narrowing that reads `bash` as a readable non-git program leaves
    the whole module GREEN with the `**_AMBIENT_GIT_CONFIG` spread deleted
    from `tests/test_build_wiki.py` — the exact defect this guard exists for —
    because that fixture hands its env to `["bash", script]`. A narrowing also
    loses every closed env reached under a name its resolver cannot follow.

    What this guard does NOT cover: the `dict(HOME=…, PATH=…)` display. This
    walks `ast.Dict`, so the CALL spelling is invisible here; a future
    narrowing would have to ADD it.

    So the tax stays and the silence does not. Until something can be
    narrowed without going quiet on a live fixture, a guard
    that over-reports on a data dict is the cheaper of the two failures — the
    author reads one message, where the other direction reads a green suite
    over a fixture whose git is reading /etc/gitconfig.

    CLOSED is the whole point, and it is what keeps this from firing on every
    dict in the suite: a dict that spreads os.environ (`{**os.environ, …}`) or
    one that carries no environment-shaped keys is not a replacement env. The
    two markers are a `PATH` or `HOME` key — the pair every closed env in this
    tree carries, because a subprocess needs them once os.environ is gone.
    """
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        if not ({"PATH", "HOME"} & set(keys)):
            continue
        spreads = [ast.unparse(v) for k, v in zip(node.keys, node.values)
                   if k is None]
        if any("_AMBIENT_GIT_CONFIG" in s or "os.environ" in s
               for s in spreads):
            continue
        out.append(node)
    return out


def _branch(repo: Path) -> str:
    proc = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"],
                          cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def test_a_bare_git_init_cannot_inherit_an_ambient_default_branch(tmp_path):
    """An ambient default branch reaches a bare init, and not the session.

    The first half is the MECHANISM, in three lines: hand git a global config
    that names a default branch and a bare `git init` takes it. That is what
    differs between a developer's Mac (`main`) and an ubuntu runner
    (`master`), and why such a CI failure does not reproduce locally without
    setting the same knob.

    The second half is the ISOLATION: the session's own environment cannot pick
    that up, because `clean_git_env` points both config layers at /dev/null.
    Without the first half this test would pass on a git that had stopped
    reading `init.defaultBranch` at all, proving nothing — the demonstration is
    what keeps the assertion honest.
    """
    fake = tmp_path / "gitconfig"
    fake.write_text("[init]\n\tdefaultBranch = zzz-ambient\n")

    leaky = dict(os.environ, GIT_CONFIG_GLOBAL=str(fake))
    leaky.pop("GIT_CONFIG_NOSYSTEM", None)
    exposed = tmp_path / "exposed"
    exposed.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=exposed, env=leaky, check=True,
                   capture_output=True)
    assert _branch(exposed) == "zzz-ambient", (
        "this git no longer reads init.defaultBranch, so the second half of "
        "this test can no longer prove anything about isolation")

    isolated = tmp_path / "isolated"
    isolated.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=isolated, check=True,
                   capture_output=True)
    assert _branch(isolated) != "zzz-ambient", (
        "a fixture's git read the ambient global config — the suite is back "
        "to reading the machine it runs on, which is how PR #172 went green "
        "locally and red in CI on the same sha")


def test_both_ambient_config_layers_are_neutralised_for_the_session():
    """`clean_git_env` is session-scoped and autouse, so this asserts the state
    every other test runs under.

    ALL THREE CONFIG-LAYER keys, named individually here rather than read out
    of the constant. `_AMBIENT_GIT_CONFIG` also carries the background-work
    suppression, which is a second concern with its own guard module
    (`tests/test_git_background_maintenance.py`) and is deliberately not
    re-asserted here — what this test is about is the layers.
    Iterating `_AMBIENT_GIT_CONFIG.items()` and comparing each to
    os.environ is TAUTOLOGICAL about the SET: deleting a key from the constant
    deletes the assertion with it, so the guard would pass with
    GIT_CONFIG_NOSYSTEM removed. The names have to be written here, in the
    checker, or the thing being checked gets to decide what is checked.
    Neutralising only the global layer leaves `/etc/gitconfig`, the layer an
    image or a corporate laptop sets and a developer never sees; dropping
    NOSYSTEM leaves it on the git versions that predate GIT_CONFIG_SYSTEM.

    The one derivation is also asserted, not assumed: every closed-env fixture
    must spread the constant rather than re-type a subset of it, which is what
    conftest's docstring claims and what a hand-typed copy would contradict.
    """
    for key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"):
        assert key in _AMBIENT_GIT_CONFIG, (
            f"{key} left the neutralised set — a layer the suite used to be "
            "shielded from is readable again")
        assert os.environ.get(key) == _AMBIENT_GIT_CONFIG[key], (
            f"{key} is {os.environ.get(key)!r} during the run, not "
            f"{_AMBIENT_GIT_CONFIG[key]!r} — the session is reading the "
            "caller's git configuration again")

    got = subprocess.run(["git", "config", "--global", "--list"],
                         capture_output=True, text=True)
    assert got.stdout.strip() == "", (
        "the global git configuration is visible to the suite: "
        f"{got.stdout.strip()[:200]}")

    # One derivation, checked. A fixture that builds a CLOSED env dict escapes
    # the session mutation above, so it must spread the constant rather than
    # hand-type a subset of it.
    #
    # OVER THE AST, not a substring. Matching only the `"KEY":` dict-literal
    # spelling would leave `dict(GIT_CONFIG_GLOBAL=...)` green — a guard that
    # recognises one spelling of what it checks, which is the shape it is
    # written to close, one spelling over.
    #
    # TWO defects, not one. Re-typing a key is the loud way to break the
    # invariant; OMITTING all three is the quiet one, and it leaks the same
    # layers: a closed `env={"HOME":…, "PATH":…}` handed to a `git init` reads
    # /etc/gitconfig. So the scan asks both questions — does any file re-type
    # a key, and does any CLOSED env dict handed to a subprocess fail to
    # spread the constant — because the docstring in conftest promises one
    # derivation for every caller and only a check of the omission half can
    # keep that true.
    offenders = []
    for src in sorted(TESTS_DIR.rglob("*.py")):
        if src.name in ("conftest.py", Path(__file__).name):
            continue
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            for name in _ambient_key_writes(node):
                offenders.append(f"{src.name}:{node.lineno} re-types {name}")
        for node in _unspread_closed_envs(tree):
            offenders.append(
                f"{src.name}:{node.lineno} builds a closed env for git "
                "without spreading _AMBIENT_GIT_CONFIG")
    assert not offenders, (
        "these fixtures do not take the ambient-config keys from the one "
        "derivation in conftest, and a hand-typed or absent subset is how "
        "three of them each shielded a different pair of layers:\n  "
        + "\n  ".join(sorted(set(offenders))))

    # Proved before trusted, in every spelling the detector covers, plus the
    # omission.
    #
    # ONE PER `_ENV_WRITERS` MEMBER. A member with no case here is
    # individually deletable from the set with the full suite green.
    for spelling in ('env = {"GIT_CONFIG_GLOBAL": "/dev/null"}',
                     'env = dict(GIT_CONFIG_SYSTEM="/dev/null")',
                     'env["GIT_CONFIG_NOSYSTEM"] = "1"',
                     'monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")',
                     'monkeypatch.setenv(name="GIT_CONFIG_GLOBAL", value="x")',
                     'monkeypatch.setitem(os.environ, "GIT_CONFIG_GLOBAL", "x")',
                     'os.environ.__setitem__("GIT_CONFIG_SYSTEM", "/dev/null")',
                     'os.environ.update([("GIT_CONFIG_GLOBAL", "/dev/null")])',
                     'os.putenv("GIT_CONFIG_NOSYSTEM", "1")',
                     'env.setdefault("GIT_CONFIG_SYSTEM", "/dev/null")'):
        planted = ast.parse(f"def f(monkeypatch, env):\n    {spelling}\n")
        assert any(_ambient_key_writes(n) for n in ast.walk(planted)), (
            f"the re-typing detector cannot see {spelling!r}, so it proves "
            "nothing about the spellings it does not happen to match")
    # ...and it must NOT fire on a read, a del, or the assertion someone would
    # naturally write to check this very isolation — IN BOTH SPELLINGS. The
    # pairs are the point: a proof set that knows only subscripts cannot see
    # the identical false positives one branch over, in the call spelling.
    # Planting both spellings of each is what makes a one-branch repair
    # impossible to call finished.
    for benign in ('x = os.environ["GIT_CONFIG_GLOBAL"]',
                   'v = os.environ.get("GIT_CONFIG_GLOBAL")',
                   'del env["GIT_CONFIG_SYSTEM"]',
                   'os.environ.pop("GIT_CONFIG_SYSTEM", None)',
                   'monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)',
                   'monkeypatch.delitem(os.environ, "GIT_CONFIG_SYSTEM")',
                   'assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"',
                   'assert env.get("GIT_CONFIG_GLOBAL") == os.devnull',
                   'print("GIT_CONFIG_GLOBAL")'):
        planted = ast.parse(f"def f(env, monkeypatch):\n    {benign}\n")
        assert not any(_ambient_key_writes(n) for n in ast.walk(planted)), (
            f"{benign!r} is reported as hand-typing — the guard fires on the "
            "obvious way to CHECK the thing it guards, and its message tells "
            "the author to spread a constant, which fits a read not at all")

    omitting = ast.parse(
        'def f(root):\n'
        '    env = {"HOME": str(root), "PATH": os.environ["PATH"]}\n'
        '    subprocess.run(["git", "init", "-q", "-b", "main"], env=env)\n')
    assert _unspread_closed_envs(omitting), (
        "a closed env that OMITS the ambient keys entirely is invisible, so "
        "'one derivation, every caller' is a claim nothing can reject — the "
        "quiet half of the same leak")
    spreading = ast.parse(
        'def f(root):\n'
        '    env = {**_AMBIENT_GIT_CONFIG, "HOME": str(root)}\n'
        '    subprocess.run(["git", "init", "-q", "-b", "main"], env=env)\n')
    assert not _unspread_closed_envs(spreading), (
        "the omission detector fires on a fixture that DOES spread the "
        "constant, so it would tax every correct caller")


# --------------------------------------------------------------------------
# 2 · the guard: no test starts a repo without naming its branch
# --------------------------------------------------------------------------

_REPO_STARTERS = ("init", "clone")
_BRANCH_PINS = ("-b", "--initial-branch", "--branch")


# The one FUNCTION in the tree that may start a repo without naming its branch,
# named the way the `repo.yaml` reader guard names its one allowed reader: by
# (module, function), never by module alone.
#
# WHAT THE EXEMPTION ACTUALLY COVERS, stated at the granularity the code has.
# The exemption is whole-FUNCTION: the named
# test contains TWO bare `git init` calls, the leaky demonstration and the
# isolated one, and both are skipped — as would a third added inside it. It has
# to contain both, because the demonstration is only evidence if the same bare
# command is run under the two environments. What the function key buys is not
# a per-call bound but a per-function one: a bare init in any OTHER function in
# this module still fails, which a module-level exemption would not give, and
# `test_the_exemption_does_not_reach_past_its_own_function` proves it.
# The guard scans tests/, and this module is in tests/ — so it necessarily
# holds the shapes it forbids, as its DEMONSTRATION and as its planted proof
# material. Each exemption is keyed by (module, function) and carries the
# reason it exists, and `test_every_exemption_names_a_function_that_exists`
# resolves every key, so an entry cannot outlive the function it excuses.
_DEMONSTRATION = "test_a_bare_git_init_cannot_inherit_an_ambient_default_branch"
# `test_no_test_starts_a_git_repo_without_pinning_its_branch` and
# `test_the_exemption_does_not_reach_past_its_own_function` need no entry:
# their planted material is a plain string bound to a local, and
# `_command_strings` reads a shell command only where it is a CALL ARGUMENT,
# so neither function reaches the guard. Every key here is one whose
# deletion reddens `test_every_exemption_excuses_a_line_the_guard_would_
# otherwise_catch`, which executes that claim rather than asserting it.
_GUARD_EXEMPTIONS = {
    ("test_ambient_git.py", _DEMONSTRATION):
        "runs a bare `git init` under two environments — that IS the "
        "demonstration, and it needs both of them to be evidence",
    ("test_ambient_git.py", "test_the_guard_names_the_line_it_rejects"):
        "parametrized offending lines, quoted so the guard can reject them — "
        "a decorator's list is part of the function it decorates",
}
_DEMONSTRATES_THE_BUILT_IN_DEFAULT = {("test_ambient_git.py", _DEMONSTRATION)}

# Callees that DRIVE git in this suite. Keyed as an ADDITIONAL trigger, never
# as the only one: `_git`, `run`, `subprocess.run` and a bare tuple are all in
# the tree, so a purely name-keyed detector goes blind on a fourth helper —
# but a purely argv-keyed one goes blind on the commonest spelling there is.
# `_git(dst, "clone", str(src), str(dst))` carries neither the literal `git`
# nor a flag, and a `git clone` argv usually has no flags at all, so `clone`
# would be enforced only for clones that happen to pass one.
#
# EVERY MEMBER HERE IS GENERIC, and `git` / `_git` are deliberately NOT in it
# any more. `unpinned_repo_starts` tests `"git" in
# callee.lower()` one disjunct EARLIER, which subsumes both spellings, so
# neither could ever be the sole trigger: with the two dropped, `git(root,
# "init", "-q")` is still caught and the scan over the whole of `tests/`
# returns the identical result. They were two alternatives of a swept guard
# that could not change a verdict, which is the one case the sweep's own
# message says to delete rather than control.
_GIT_CALLEES = ("run", "_run", "call", "check_call",
                "check_output", "Popen", "getoutput")

# One PLANTED CALL per member of `_GIT_CALLEES`, written out rather than
# generated from the tuple. Each is the shape that reaches the generic-driver
# disjunct and no other — a bare subcommand with a flag, under a callee whose
# name says nothing about git — so deleting its member makes exactly this line
# invisible to the scan, which is the BEHAVIOURAL control the sweep asks for.
# A control generated from the vocabulary could not be one: it would shrink
# with the tuple and stay green.
#
# WHAT THAT LEAVES, stated rather than claimed away:
# a member and its plant deleted TOGETHER pass every scan in the guard below,
# because nothing is left to be unpinned. Only a COUNT sees that, and it is
# `SWEPT_ALTERNATIVES["test_ambient_git:_GIT_CALLEES"] == 7` in
# tests/test_guard_mutations.py — NOT the arity assertion at the foot of the
# guard, which reads 6 == 6 and passes (round 2 caught the first cut of this
# paragraph's sibling claiming otherwise). That assertion's own catch is the
# other direction: a plant deleted with its member kept. Round 1 found it
# standing FIRST, where it intercepted every single-member deletion before a
# scan ran — so the guard was passing an arity comparison off as the
# behavioural control. It is last now.
_GENERIC_DRIVER_PLANTS = (
    'run("init", "-q")',
    '_run("init", "-q")',
    'call("init", "-q")',
    'check_call("init", "-q")',
    'check_output("init", "-q")',
    'Popen("init", "-q")',
    'getoutput("init", "-q")',
)
# The same command written as SHELL text rather than an argv — `shell=True`,
# an `sh -c` payload, a heredoc in a fixture script. Nothing in the tree writes
# one today; the guard covers it because "the tree does not do that yet" is the
# reason a scanner is narrow, not a reason it is right.
_SHELL_REPO_START = re.compile(r"\bgit\s+(?:-[^\s]+\s+)*(?:init|clone)\b")
_SHELL_BRANCH_PIN = re.compile(r"(?:-b\s|--initial-branch[=\s]|--branch[=\s])")


def _string_runs(tree: ast.AST) -> list[tuple[int, list[str], str]]:
    """Every argv-shaped run of string constants in `tree`: line, run, callee.

    Three shapes, because the tree writes all three and a detector that saw one
    would have proved nothing about the others:

      subprocess.run(["git", "init", "-q"], ...)   a list literal
      _git(root, "init", "-q")                     a helper, git implied
      for args in (("init", "-q"), ...): _git(root, *args)

    The third is why LITERALS are scanned and not only call arguments: the argv
    there is a tuple that never appears inside a call node at all. The CALLEE
    rides along because the second shape can carry neither the literal `git`
    nor a flag, and then nothing about the run itself says it is git.
    """
    runs: list[tuple[int, list[str], str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            items, callee = node.elts, ""
        elif isinstance(node, ast.Call):
            items, callee = node.args, _callee_name(node)
        else:
            continue
        consts = [e.value for e in items
                  if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if consts:
            runs.append((node.lineno, consts, callee))
    return runs


def _command_strings(tree: ast.AST) -> list[ast.Constant]:
    """String constants that are PASSED TO A CALL — the only ones that can be a
    command.

    POSITIVE, never a subtraction. Scanning every string in the module and
    subtracting docstrings leaves prose: a comment is invisible to the AST but
    a dict value explaining an exemption is not, and the guard would flag its
    own reason text for containing the phrase `git init`.
    Subtracting the places prose lives is an open-ended list; naming the one
    place a command lives is a closed one. This module and the modules it scans
    describe the defect in words constantly, and the obvious repair for a guard
    that flags the explanation — deleting the explanation — is the worst
    outcome available (the same reasoning the config.py AST guard gives for
    not grepping that module's source).

    The whole ARGUMENT SUBTREE is walked, not just its top node: a shell
    command is as often built as `"git clone " + str(src)` or an f-string as it
    is passed as one literal, and reading only the top node would miss both.
    """
    out: list[ast.Constant] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for arg in node.args:
            out += [e for e in ast.walk(arg) if isinstance(e, ast.Constant)
                    and isinstance(e.value, str)]
    return out


def unpinned_repo_starts(source: str, module: str) -> list[str]:
    """Every place in `source` that starts a git repo without naming a branch.

    A run counts as a git argv when it names `init` or `clone` AND any one of
    four things says it is git: the literal `git` in the argv, a flag beside
    it, a callee that drives git, or `init`/`clone` in the argv's FIRST
    position. Shell text carrying the same command is scanned separately.

    FOUR TRIGGERS, NOT ONE PAIR: requiring the literal `git` or a flag would
    miss three real spellings: `_git(root, "init")`,
    `_git(dst, "clone", str(src), str(dst))` — where the `str()` args are Call
    nodes, not constants, so the run is just `["clone"]` — and
    `subprocess.run("git init", shell=True)`. A flagless `git clone` is the
    ordinary spelling, so `clone` would be enforced only for clones that
    happen to carry `-q`. A mutation proof whose every planted case carries
    `-q` never touches that boundary, so the planted set includes a flagless
    helper call and a shell string.
    """
    tree = ast.parse(source)
    # Innermost enclosing function per line, so the exemption below can be
    # keyed to ONE function rather than a whole module. The span starts at the
    # first DECORATOR, not at `def`: a `@pytest.mark.parametrize` list is part
    # of the function it decorates, and reading it as module-level code would
    # put a parametrized offending line outside every span.
    spans = []
    for n in ast.walk(tree):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = min([n.lineno] + [d.lineno for d in n.decorator_list])
        spans.append((start, n.end_lineno or n.lineno, n.name))

    def enclosing(lineno: int) -> str:
        best_start, best_name = -1, ""
        for start, end, name in spans:
            if start <= lineno <= end and start > best_start:
                best_start, best_name = start, name
        return best_name

    def exempt(lineno: int) -> bool:
        return (module, enclosing(lineno)) in _GUARD_EXEMPTIONS

    offenders: list[str] = []
    for lineno, consts, callee in _string_runs(tree):
        if not any(c in _REPO_STARTERS for c in consts):
            continue
        # SOMETHING has to say the run is git. Letting `any(c.startswith("-"))`
        # or `consts[0] in _REPO_STARTERS` admit an argv on its own would
        # report `["npm", "init", "-y"]`, a vocabulary tuple
        # `("init", "clone", "verify")` and even
        # `parser.add_argument("init", "--help")` as unpinned repo starts — in
        # a guard that scans every file under tests/ and gates every future
        # one. A flag or a leading subcommand alone says nothing about git.
        #
        # The evidence has to be one of these, in descending strength:
        looks_like_git = (
            # the literal in the argv — `["git", "init", "-q"]`
            "git" in consts
            # a callee that IS git — `_git(root, "init")`; a name containing
            # "git" is evidence on its own, a generic driver is not
            or "git" in callee.lower()
            # a generic subprocess driver (`run`, `Popen`, …) whose FIRST word
            # is the subcommand — `run("init", "-q")`, the shape the tree uses
            or (callee in _GIT_CALLEES and consts[0] in _REPO_STARTERS)
            # a BARE literal (no enclosing call to name a driver) that opens
            # with the subcommand AND carries a flag — `("init", "-q")`, the
            # `for args in (...)` shape the tree writes. The flag is what
            # separates an argv from a word list: `("init", "clone", "verify")`
            # and `["init", "the repo"]` have none, and are prose.
            or (not callee and consts[0] in _REPO_STARTERS
                and any(c.startswith("-") for c in consts)))
        if not looks_like_git:
            continue
        if any(c in _BRANCH_PINS or c.startswith("--initial-branch=")
               or c.startswith("--branch=") for c in consts):
            continue
        if exempt(lineno):
            continue
        offenders.append(f"{module}:{lineno} {consts}")

    # The same command written as shell TEXT rather than an argv, read only
    # where a command can actually be: an argument to a call.
    for node in _command_strings(tree):
        if exempt(node.lineno):
            continue
        if (_SHELL_REPO_START.search(node.value)
                and not _SHELL_BRANCH_PIN.search(node.value)):
            offenders.append(f"{module}:{node.lineno} shell: {node.value[:60]!r}")
    return sorted(set(offenders))


def test_no_test_starts_a_git_repo_without_pinning_its_branch():
    """The guard that makes the isolation above an invariant rather than a
    lucky state of the tree.

    Isolation removes the ambient CONFIG; it does not remove the ambient
    ASSUMPTION. A bare `git init` under an empty configuration still takes
    git's built-in default, which is a property of the binary on the machine —
    a fact that has already been announced as due to change, and that a fixture
    asking for `main` would then be wrong about everywhere at once.

    The detector is proved before it is trusted. SIX planted shapes, because a
    scanner that cannot fire reports a clean tree forever — the defect class
    this repo calls tests-bite — and because a proof whose every case shares
    one property cannot test the boundary that property draws: planted shapes
    that all carry `-q` never remove the flag half of the trigger. The
    FLAGLESS cases below are the ones that matter: they are the ordinary way
    a `git clone` is written.
    """
    planted = (
        'import subprocess\n'
        'def one(root):\n'
        '    subprocess.run(["git", "init", "-q"], cwd=root)\n'
        'def two(root):\n'
        '    _git(root, "init", "-q", "--bare")\n'
        'def three(root):\n'
        '    for args in (("init", "-q"), ("add", ".")):\n'
        '        _git(root, *args)\n'
        'def four(root):\n'
        '    _git(root, "init")\n'
        'def five(src, dst):\n'
        '    _git(dst, "clone", str(src), str(dst))\n'
        'def six(root):\n'
        '    subprocess.run("git init", shell=True, cwd=root)\n')
    caught = unpinned_repo_starts(planted, "planted.py")
    assert len(caught) == 6, (
        f"the detector misses an unpinned repo start, so it proves nothing: "
        f"{caught}")

    pinned = (
        'import subprocess\n'
        'def one(root):\n'
        '    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root)\n'
        'def two(root):\n'
        '    _git(root, "init", "-q", "--bare", "--initial-branch=main")\n'
        'def three(root):\n'
        '    for args in (("init", "-q", "-b", "main"),):\n'
        '        _git(root, *args)\n'
        'def four(root):\n'
        '    _git(root, "init", "-b", "main")\n'
        'def five(src, dst):\n'
        '    _git(dst, "clone", "--branch", "main", str(src), str(dst))\n'
        'def six(root):\n'
        '    subprocess.run("git init -b main", shell=True, cwd=root)\n')
    assert unpinned_repo_starts(pinned, "pinned.py") == [], (
        "the detector fires on a repo start that DOES name its branch, so it "
        "would tax every correct fixture")

    offenders: list[str] = []
    for src in sorted(TESTS_DIR.rglob("*.py")):
        offenders += unpinned_repo_starts(src.read_text(), src.name)
    assert not offenders, (
        "these fixtures start a git repo without naming its branch, so the "
        "branch comes from the machine — `main` on a developer's Mac, "
        "`master` on the runner, and whatever git's built-in default becomes "
        "next. Write `git init -b main`:\n  "
        + "\n  ".join(offenders))


def test_every_generic_subprocess_driver_reads_as_git():
    """One control per member of `_GIT_CALLEES`, so none can be deleted in
    silence.

    The tuple was UNSWEPT with the measurement that said why:
    9 of 9 members deletable with this module green, because every planted
    case in `test_no_test_starts_a_git_repo_without_pinning_its_branch`
    carries the literal `git` or a `_git` callee and is caught by an EARLIER
    disjunct, so the generic-driver trigger was exercised by nothing. A
    detector arm nothing exercises is the defect class this suite calls
    tests-bite, one layer down: the arm that catches `run("init", "-q")` —
    the shape the tree actually uses — was a claim about the scan.

    Two of the nine were not controllable and are gone rather than exempted:
    `git` and `_git` are subsumed by `"git" in callee.lower()` one disjunct
    above, so no input could distinguish their presence. See `_GIT_CALLEES`.

    The pinned counterpart is asserted too, but it is NOT the control: a
    branch-pinned argv returns nothing whether or not its callee is in the
    tuple. It is here so the control cannot pass by the scan going blanket-red.
    """
    for expr in _GENERIC_DRIVER_PLANTS:
        source = f"def f(root):\n    {expr}\n"
        assert unpinned_repo_starts(source, "m.py"), (
            f"`{expr}` is a repo start that names no branch, under a driver "
            "this suite uses, and the scan does not see it — the member of "
            "`_GIT_CALLEES` that makes it visible has been deleted or the "
            "generic-driver disjunct has gone")
        pinned = source.replace('"-q"', '"-q", "-b", "main"')
        assert unpinned_repo_starts(pinned, "m.py") == [], (
            f"`{expr}` with `-b main` added is reported unpinned, so the "
            "control above would pass on a scan that fires on everything")
    # LAST, not first. With this assertion ahead of
    # the loop a member deletion failed HERE, on arity, and the per-plant scans
    # were never reached — so the thing standing behind a member was a count of
    # two lists, which is the restatement the comment on `_GENERIC_DRIVER_PLANTS`
    # disparages.
    #
    # WHAT IT CATCHES, measured rather than asserted (round 2): a PLANT deleted
    # with its member kept. The loop is then short one driver and stays green,
    # and this is the only thing in the file that notices. What it does NOT
    # catch is a member and its plant deleted TOGETHER — that leaves 7 == 7
    # reading 6 == 6 and the whole module at 12 passed. The catch there is one
    # level out, `SWEPT_ALTERNATIVES["test_ambient_git:_GIT_CALLEES"] == 7` in
    # tests/test_guard_mutations.py, which goes 2 failed. Round 2 caught the
    # first cut of this comment claiming that catch for the line below.
    assert len(_GENERIC_DRIVER_PLANTS) == len(_GIT_CALLEES), (
        f"{len(_GENERIC_DRIVER_PLANTS)} planted drivers for "
        f"{len(_GIT_CALLEES)} members — a member with no plant is a member "
        "the sweep reports unpinned, and a plant with no member is dead")


def test_every_branch_pin_spelling_is_recognised():
    """Every branch-pin spelling the detector accepts is exercised. The
    pinned planted set above spells `-b` in shell text and
    `--initial-branch=` / `--branch` as argv tokens, and nothing else — so
    without this, `--initial-branch` as its own argv token and every shell
    spelling but `-b` could be deleted from `_BRANCH_PINS` and
    `_SHELL_BRANCH_PIN` with this module green.

    One shell string and one argv per spelling, each of which the detector
    must read as PINNED, and two near-misses it must not: the `[=\\s]` after
    each long flag is what separates `--branch=main` from `--branches`.
    """
    for shell in ("git init -b main",
                  "git init --initial-branch=main",
                  "git init --initial-branch main",
                  "git clone --branch=main x y",
                  "git clone --branch main x y"):
        source = f"def f(root):\n    subprocess.run({shell!r}, shell=True)\n"
        assert unpinned_repo_starts(source, "m.py") == [], (
            f"{shell!r} names its branch and the shell detector reports it "
            "as unpinned")
    for argv in ('"init", "-b", "main"',
                 '"init", "--initial-branch", "main"',
                 '"init", "--initial-branch=main"',
                 '"clone", "--branch", "main", "x", "y"',
                 '"clone", "--branch=main", "x", "y"'):
        source = f"def f(root):\n    _git(root, {argv})\n"
        assert unpinned_repo_starts(source, "m.py") == [], (
            f"argv [{argv}] names its branch and the detector reports it as "
            "unpinned")
    for shell in ("git init --initial-branchy main",
                  "git clone --branches x y"):
        source = f"def f(root):\n    subprocess.run({shell!r}, shell=True)\n"
        assert unpinned_repo_starts(source, "m.py"), (
            f"{shell!r} merely CONTAINS a pin spelling and the detector reads "
            "it as pinned")


def test_every_exemption_names_a_function_that_exists():
    """An allowlist entry that resolves to nothing is a hole with a name on it.

    A key naming a renamed function is the same defect as a docstring that
    sends a reader to one: the cheapest guard against it is to RESOLVE what
    the list points at. Every key here must name a function this
    module actually defines, and every entry must carry a reason, so an
    exemption cannot outlive the code it excuses or arrive without one.
    """
    defined = {n.name for n in ast.walk(ast.parse(Path(__file__).read_text()))
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for (module, func), reason in _GUARD_EXEMPTIONS.items():
        assert module == Path(__file__).name, (
            f"the exemption list excuses {module}, which is not this module — "
            "an allowlist that reaches into another file is a hole nobody "
            "reading that file can see")
        assert func in defined, (
            f"exemption names {func!r}, which this module does not define — "
            "the function was renamed or removed and the hole outlived it")
        assert len(reason) > 30, f"exemption for {func!r} carries no reason"
    assert _DEMONSTRATES_THE_BUILT_IN_DEFAULT <= set(_GUARD_EXEMPTIONS)


def test_every_exemption_excuses_a_line_the_guard_would_otherwise_catch(
        monkeypatch):
    """An exemption is justified only if the guard would otherwise flag its
    function. Planted material that is a plain string bound to a local never
    trips the guard, because `_command_strings` reads a shell command only
    where it is a CALL ARGUMENT — an entry for such a function excuses
    nothing. An allowlist
    entry that resolves to a function the guard would never flag is a hole
    with a name on it, one rename away from excusing something real.

    Executed, not asserted: each key is removed alone and THIS module is
    re-scanned; the scan must then report a line inside the function the
    key names. The row in tests/test_guard_mutations.py is SWEPT on the
    same measurement.
    """
    source = Path(__file__).read_text()
    module = Path(__file__).name
    assert unpinned_repo_starts(source, module) == [], (
        "this module trips its own guard with every exemption in place — "
        "the exemptions no longer cover what they name")
    for key in list(_GUARD_EXEMPTIONS):
        without = {k: v for k, v in _GUARD_EXEMPTIONS.items() if k != key}
        monkeypatch.setattr(sys.modules[__name__], "_GUARD_EXEMPTIONS", without)
        caught = unpinned_repo_starts(source, module)
        _, func = key
        assert caught, (
            f"deleting the exemption for {func!r} changes nothing: the guard "
            "never reaches that function, so the entry excuses nothing "
            "")
        monkeypatch.undo()


def test_the_exemption_does_not_reach_past_its_own_function():
    """The bound the exemption really has.

    The exemption is whole-function, not per-call: every bare init inside the
    named test is skipped, the isolation one included, and so would a third
    call be.

    What the (module, function) key DOES buy is the bound this test pins — a
    module-level exemption would let a bare init anywhere in this file through,
    and this one does not. Both directions are checked against a synthetic
    module, so the proof does not depend on what this file happens to contain
    today.
    """
    module, exempted = next(iter(_DEMONSTRATES_THE_BUILT_IN_DEFAULT))

    inside = (f'def {exempted}(tmp_path):\n'
              '    subprocess.run(["git", "init", "-q"], cwd=tmp_path)\n'
              '    subprocess.run(["git", "init", "-q"], cwd=tmp_path)\n'
              '    subprocess.run(["git", "init", "-q"], cwd=tmp_path)\n')
    assert unpinned_repo_starts(inside, module) == [], (
        "the exemption no longer covers every bare init in the function it "
        "names — the demonstration needs two of them (leaky and isolated) to "
        "be evidence at all")

    outside = (f'def {exempted}(tmp_path):\n'
               '    subprocess.run(["git", "init", "-q"], cwd=tmp_path)\n'
               'def some_other_test(tmp_path):\n'
               '    subprocess.run(["git", "init", "-q"], cwd=tmp_path)\n')
    caught = unpinned_repo_starts(outside, module)
    assert len(caught) == 1 and "some_other_test" not in caught[0], caught
    assert caught[0].startswith(f"{module}:4"), (
        f"the exemption leaked past the function it names, or caught the "
        f"wrong line: {caught}")

    elsewhere = ('def any_test(tmp_path):\n'
                 '    subprocess.run(["git", "init", "-q"], cwd=tmp_path)\n')
    assert unpinned_repo_starts(elsewhere, "some_other_module.py"), (
        "the exemption is not keyed on the MODULE as well, so the same "
        "function name in another file would inherit it")


@pytest.mark.parametrize("fixture_line", [
    'subprocess.run(["git", "init", "-q"], cwd=root)',
    '_git(root, "clone", "-q", str(src), str(dst))',
    '_git(root, "clone", str(src), str(dst))',
    'subprocess.run("git clone " + str(src), shell=True)',
])
def test_the_guard_names_the_line_it_rejects(fixture_line):
    """error-names-cause, applied to this guard: a reader who trips it must be
    told which argv is the problem, not merely that one exists."""
    source = f"def f(root, src, dst):\n    {fixture_line}\n"
    caught = unpinned_repo_starts(source, "m.py")
    assert caught, f"the guard no longer catches {fixture_line!r}"
    assert "init" in caught[0] or "clone" in caught[0], (
        f"the report does not show the offending argv: {caught[0]!r}")
