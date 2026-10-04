"""The mutation engine behind `test_guard_mutations.py`.

NOT a test module: it holds the machinery, so the registry and the assertions
next door read as a list of claims about this repo's guards rather than as a
regex parser with some tests at the bottom.

WHAT A MUTATION IS HERE. Every mutation is applied IN PROCESS, to the module
object the guard lives on — `setattr(module, name, neutered)` and back again.
No source file is ever rewritten, which is why the stale-bytecode trap (two
same-size source mutations inside one timestamp second reusing a cached
`.pyc`, so the sweep reports a load-bearing alternative that is not) cannot
arise in this harness at all: there is no source edit for a `.pyc` to be stale
against. That is a property of the mechanism, not a discipline anyone has to
remember, and `test_the_sweep_writes_no_source_and_no_bytecode`
executes the claim rather than asserting it in prose.

WHAT IT CANNOT DO, stated here because a harness that silently skips a guard
is the same defect one level up. It mutates DATA a guard consults — a compiled
pattern, a vocabulary tuple, a page-set dict. It cannot mutate a CODE BRANCH:
an `ast.Attribute` arm inside a detector, an `except` tuple, a `compared += 1`
that is absent. Those have their own named regression tests, one per bug in
the family, and the registry next door is TOTAL over the suite,
so every vocabulary it does NOT sweep carries a written disposition and the gap
grows only by a reviewable edit.
"""

from __future__ import annotations

import ast
import inspect
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent

# The harness's own two files. `guard_mutation.py` holds the engine and
# `test_guard_mutations.py` holds the registry, so classifying the registry
# inside itself would be circular — and `REGISTRY` is a mapping of strings,
# which is exactly the shape discovery looks for. Excluded by NAME, and
# `test_the_ledger_states_the_harness_reach` asserts the exclusion is these
# two and nothing else, so no third file can hide behind it.
#
# THE EXCLUSION IS NOT AN EXEMPTION. Outside `REGISTRY` alone, vocabularies
# such as `_CONTAINER_CALLS`, the whole `ast.Call` branch beside it,
# `_GROUP_PREFIXES`, `WITNESSES` and `EXEMPT` would have no test able to fail
# on them: an auditor that cannot audit itself. `discover(exclude=())` is how
# the COMPANION ledger — `SELF_REGISTRY` and
# `test_the_harness_audits_its_own_two_files` — points discovery at these
# two instead, and it is total over them in both directions with every pinned
# row's witness RUN. A second exemption here would be the same mistake one
# level up.
SELF = ("guard_mutation.py", "test_guard_mutations.py")


# --------------------------------------------------------------------------
# 1. Discovery — what the suite declares
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """A module-level vocabulary a guard could be reading."""

    module: str          # "test_docs", "conftest"
    name: str            # "HOME_RE"
    lineno: int
    kind: str            # "regex" | "sequence" | "mapping" | "pairs"
    size: int            # alternatives visible at declaration (informational)

    @property
    def key(self) -> str:
        return f"{self.module}:{self.name}"


def _literal(node: ast.AST) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


# The attributes of a compound statement that hold statements running at the
# SAME scope as the statement itself. `cases` is `ast.Match`'s: a `match`
# stores its bodies under `cases[].body`, and without it a module-level name
# bound inside a case is invisible to all three readers while
# `_module_level`'s docstring says "every statement".
# `handlers` and `cases` hold `ExceptHandler` / `match_case` nodes rather than
# statements — they are yielded like any other node and the readers below
# ignore them, then their own `.body` is descended by the same recursion.
_SAME_SCOPE_BLOCKS = ("body", "orelse", "finalbody", "handlers", "cases")


def _module_level(block):
    """Every statement that runs at MODULE scope — `try:` / `if:` / `for:` /
    `while:` / `with:` / `match:` bodies included, function and class bodies
    excluded.

    ONE definition, two readers. A vocabulary bound inside a module-level
    compound statement runs at import exactly like a top-level one, so
    reading only `.body` would make it invisible, and reading every block but
    `ast.Match`'s would leave the same hole one construct over. A function or
    class body is NOT module scope: walking the whole tree with `ast.walk`
    would let a function-LOCAL `X = []` promote an unrelated module-level
    scalar `X = 1` into the registry's subject (pinned by
    `test_a_function_local_container_cannot_promote_a_module_scalar`).
    Two readers disagreeing about scope is what makes that possible, so
    there is only one reader of scope.

    The BOUND is `_SAME_SCOPE_BLOCKS` and it is a list, not a claim: a compound
    statement whose bodies live under some other attribute is invisible here,
    and `test_a_vocabulary_bound_inside_a_match_case_is_discovered` is
    where the next one gets a control.
    """
    for node in block:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            continue
        yield node
        for name in _SAME_SCOPE_BLOCKS:
            nested = getattr(node, name, None)
            if isinstance(nested, list):
                yield from _module_level(
                    [n for n in nested if isinstance(n, ast.AST)])


def _assigned_names(path: Path) -> dict[str, int]:
    """{name: line} for every module-level assignment to a bare name.

    The AST answers WHICH names a module declares — `vars(module)` also holds
    everything it imported, and a constant imported from `conftest` belongs to
    the module that declares it. The VALUE is then read off the imported
    module rather than evaluated here, which is the half that matters: a
    pattern assembled from a vocabulary (`re.compile("|".join(...))`) is not
    a literal, so an AST-only read would classify `_NEGATOR` and
    `_ONE_DIR_HOLDS_EVERY_ROUND` as nothing at all and the sweep would never
    reach the two patterns the mandate guard is actually built on.
    """
    out: dict[str, int] = {}
    for node in _module_level(ast.parse(path.read_text()).body):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if node.value is None:
            continue
        # Tuple and list targets too: `HEAD, BASE = ...` is live in five test
        # modules, and reading only `ast.Name` would make every name bound
        # that way invisible to a registry that calls itself TOTAL.
        for name in _bound_names(targets):
            out.setdefault(name, node.lineno)
    return out


def classify(value: object) -> tuple[str, int] | None:
    """(kind, alternative count) if `alternatives()` can take `value` apart.

    GATED ON THE ENUMERATOR, not on a guess about shape. A shape gate — `|`
    in a pattern, at least two members, all-str-or-all-tuple members and str
    keys — lets live guard vocabularies fall through in silence while the
    registry next door calls itself TOTAL: a heading pattern with no `|` at
    all still has a character class `regex_alternatives` takes apart into
    two alternatives.

    So: a PATTERN is classifiable when the enumerator yields at least one
    alternative — or when it REFUSES the pattern, since a pattern this cannot
    model must be accounted for rather than skipped. A COLLECTION or MAPPING of
    any size is classifiable, because deleting a member is type-agnostic and a
    one-member vocabulary is exactly where a guard's whole reach can sit.

    An EMPTY container is a CONTAINER. Reading emptiness as absence would
    make discovery depend on the machine: `MISSING_SHELLS`
    in `test_commit_lint.py` is `tuple(s for s in ALL_SHELLS if not
    shutil.which(s))`, which is `()` where every shell resolves and non-empty
    where one does not — so the registry would report TOTAL on one box and
    fire on another. "Total over tests/" has to be a property of
    the TREE, not of the box. Nothing to delete is a real answer about a
    vocabulary and the registry records it, exactly as it already does for a
    pattern the enumerator finds no alternative in.

    None is returned only for a value that is not a vocabulary AT ALL — a
    scalar, a function, a Path. That is a fact about the value's TYPE, which
    no environment changes, not a judgment about whether it is a guard; the
    registry makes the judgment.
    """
    if isinstance(value, re.Pattern) and isinstance(value.pattern, str):
        # EVERY compiled pattern, including one the enumerator finds nothing
        # to delete in. Returning None for a pattern with no `|` and no
        # multi-member character class would let a live guard with a
        # single-branch pattern reach no registry entry with nothing going
        # red. "No deletable alternative" is a real answer about a guard, and
        # the registry is where it gets recorded; it is not the enumerator's
        # to swallow.
        try:
            return "regex", len(regex_alternatives(value))
        except PatternUnreadable:
            return "regex", 0        # accounted for; the sweep will refuse it
    if isinstance(value, str):
        # A guard vocabulary spelled as a regex SOURCE STRING rather than a
        # compiled pattern. A separator vocabulary spelled as source strings
        # and joined into a live pattern at its call site would classify to
        # None as plain `str`, reaching the suite with no disposition while
        # the totality test stays green.
        #
        # `discover` decides WHICH strings reach here, by reading how the
        # module USES the name — see `_regex_source_names`. Content alone
        # cannot: a shell-script fixture holds `|` and `[` exactly as a
        # character class does, and keying on that would make every such
        # fixture a candidate and tax every future PR that adds one.
        try:
            found = len(regex_alternatives(re.compile(value)))
        except (re.error, PatternUnreadable):
            return None
        return ("regex-source", found) if found else None
    if isinstance(value, dict):
        return "mapping", len(value)
    if isinstance(value, (tuple, list, set, frozenset)):
        if value and all(isinstance(x, tuple) for x in value):
            return "pairs", len(value)
        return "sequence", len(value)
    return None


_CONTAINER_CALLS = ("tuple", "list", "set", "frozenset", "dict", "sorted")

# The expression nodes that ARE a container, with nothing to prove.
_CONTAINER_LITERALS = (ast.Tuple, ast.List, ast.Set, ast.Dict,
                       ast.ListComp, ast.SetComp, ast.DictComp)


def _assign_targets(node):
    """(targets, value) for a module-level assignment, or None."""
    if isinstance(node, ast.Assign):
        return node.targets, node.value
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [node.target], node.value
    return None


def _bound_names(targets) -> list[str]:
    """Every bare name a target list binds, tuple and list targets unpacked."""
    out, stack = [], list(targets)
    while stack:
        target = stack.pop()
        if isinstance(target, ast.Name):
            out.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            stack.extend(target.elts)
    return out


def _own_nodes(func):
    """Every node of `func`'s OWN body — nested defs, lambdas and classes cut
    off, because their statements belong to a different scope."""
    stack = list(func.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _returns_a_value(func) -> bool:
    """Does calling `func` yield what its `return` statements say?

    THREE WAYS IT DOES NOT, each able to promote a non-container into the
    registry's subject:

    * `ast.walk` descends into a nested `def`, so a nested function's `return`
      read as the outer helper's would make `OUTER = _outer()` proved while
      `_outer()` returns None;
    * a GENERATOR function returns a generator whatever its `return` says;
    * an exit that FALLS OFF THE END contributes no `ast.Return` node at all,
      so quantifying over the nodes never sees the implicit `None` it yields
      — the same hole an explicit bare `return` has.

    The last is answered CONSERVATIVELY rather than by control-flow analysis:
    the body's final statement must itself be a `return <value>`. A helper
    whose every branch returns but whose last statement is an `if` is not
    proved, and that is the direction this errs in on purpose — proving less
    costs a disposition the runtime half of the union still supplies, while
    proving too much puts a scalar in the registry's subject.

    TWO GUARDS ARE DELIBERATELY ABSENT: a `not returns` clause, which no
    input can distinguish from the trailing clause below (an empty `returns`
    means the last statement is not a `Return`, and the trailing clause
    already refuses that), and a `bool(func.body)` clause, which the parser
    makes unreachable (a `def` has at least one statement). Neither could be
    pinned because neither could execute a different path, and a branch that
    cannot execute is not a bound. The valueless-exit clause STAYS and is
    pinned directly, by
    `test_returns_a_value_refuses_a_valueless_exit_on_its_own`: through
    `_container_shaped` it is unobservable, because the quantifier there
    asks `_proves` of a `None` value and gets `False` either way, but it is
    this function's own contract.
    """
    own = list(_own_nodes(func))
    if any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in own):
        return False                       # a generator, not its return value
    returns = [node for node in own if isinstance(node, ast.Return)]
    if any(node.value is None for node in returns):
        return False                       # an exit that yields None
    return isinstance(func.body[-1], ast.Return)


def _paired(target, value):
    """(name, the expression BOUND TO IT) for one assignment target.

    Unpacking is not the same question as assignment: `VOCAB, LIMIT =
    ('a','b'), 3` assigns an ELEMENT to each name, and proving the whole value
    and then marking every unpacked name would put the int `LIMIT` in the
    registry's subject. Elementwise where both sides are sequences and
    neither side is starred; nothing at all where they are not, since an
    element of an opaque value is not something the AST settles.

    THERE IS NO LENGTH GUARD. Refusing two sides of different lengths would
    add nothing beside the starred guard: that case is a runtime `ValueError`
    — an unstarred unpacking of the wrong length cannot execute, so a module
    carrying one cannot import. A guard only dead source can reach is not a
    bound, and nothing could pin it. On such dead source `zip` pairs the
    aligned prefix, which proves
    names that never get a value; that costs a disposition, never a scalar
    in the subject. The STARRED guard is what does the live work: `*A, B =
    X, Y, Z` is legal, its sides align on nothing the AST can see, and
    `_UNPACK_PROBE` pins each side of the refusal separately.
    """
    if isinstance(target, ast.Name):
        yield target.id, value
        return
    if not isinstance(target, (ast.Tuple, ast.List)):
        return
    if not isinstance(value, (ast.Tuple, ast.List)):
        return
    if any(isinstance(e, ast.Starred) for e in target.elts + value.elts):
        return
    for element, bound in zip(target.elts, value.elts):
        yield from _paired(element, bound)


def _locally_bound(func) -> set[str]:
    """Every name `func`'s own scope binds: parameters and the targets it
    stores to, nested scopes excluded and `global`/`nonlocal` honoured. Also
    called for a CLASS body, which has no parameters and binds the same way.

    THE BOUND IS THE `elif` CHAIN BELOW and it is a list, not a claim: a
    construct that binds a name some other way is invisible here, and a name
    it misses is a name this treats as the module's. `match` captures are in
    the chain because `_module_level` descends `match`.

    THE CENSUS is by MUTATION rather than by reading, because a count by
    reading is a sample that reads as an enumeration: single-branch
    mutations over the six AST readers (`_locally_bound`, `seed`,
    `_regex_parameters`, `_proves`, `_paired`, `_returns_a_value`) — every
    `elif` arm, every member of every `isinstance` tuple, every clause of
    every compound guard, plus pair mutations where guards overlap — each
    deleted alone and measured two-sided: `pytest tests/test_guard_mutations.py`
    green AND the three readers' names identical over every module in
    `tests/`, under PYTHONDONTWRITEBYTECODE=1, `__pycache__` cleared,
    `-p no:cacheprovider`. Arms are the wrong unit to count: an `isinstance`
    tuple is one arm and N deletable members, and `(ast.For, ast.AsyncFor)`
    can be pinned on `For` alone.

    A branch only dead source can reach, or one no input can tell from its
    absence, is not a bound and is not carried: `_returns_a_value` has no
    `not returns` or `bool(func.body)` clause, `_paired` no length guard,
    `_proves` no Constant-operand guards, and this function no
    `optional_vars is not None`, which `_bound_names` already answers. Every
    single-branch mutation of what remains takes a named test RED: the
    binding fixtures in
    `test_a_regex_source_is_a_module_name_not_a_local_collision` and
    `test_a_vocabulary_compiled_through_a_helper_is_discovered` (one
    construct per arm, colliding with a regex read at argument 0), the rows
    of `_SHAPE_PROBE` and `_UNPACK_PROBE`, and two direct tests for the
    guards the seed cannot observe —
    `test_returns_a_value_refuses_a_valueless_exit_on_its_own` and
    `test_locally_bound_honours_nonlocal_and_never_binds_a_nameless_form`.

    Why `SELF_REGISTRY` cannot find this: its subject is module-level DATA,
    and an `elif` chain of node kinds is a vocabulary in CODE form —
    `guard_mutation`'s own stated limit ("it cannot mutate a CODE BRANCH")
    one level up. What stays in the tree is one control per branch, and the
    next arm added here without one is found by a reader, not by the suite.

    Enumerated by TARGET KIND rather than by "any `ast.Name` in `Store`
    context", because the two disagree in the direction that costs coverage:
    a comprehension's `for` target is a `Store` inside a scope of its own and
    binds nothing here, so a blanket read would shadow a module-level name
    that a comprehension merely reuses — and a shadowed name is a regex source
    this drops, which is a vocabulary reaching the suite with no disposition.
    """
    bound: set[str] = set()
    args = getattr(func, "args", None)
    if args is not None:
        bound |= {a.arg for a in
                  args.posonlyargs + args.args + args.kwonlyargs}
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                bound.add(extra.arg)
    freed: set[str] = set()
    body = func.body if isinstance(func.body, list) else [func.body]
    stack: list = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            bound.add(node.name)        # the def's NAME binds here…
            continue                    # …its body is a different scope
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            freed |= set(node.names)    # explicitly NOT this scope's
            continue
        if isinstance(node, ast.Assign):
            bound.update(_bound_names(node.targets))
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            bound.update(_bound_names([node.target]))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bound.update(_bound_names([node.target]))
        elif isinstance(node, ast.NamedExpr):
            bound.update(_bound_names([node.target]))
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.withitem):
            bound.update(_bound_names([node.optional_vars]))   # None binds
                                                              # nothing there
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bound.add(node.name)           # `case (X,):` binds X here
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
        stack.extend(ast.iter_child_nodes(node))
    return bound - freed


def _container_shaped(path: Path) -> set[str]:
    """Module-level names whose ASSIGNED EXPRESSION is PROVABLY a container.

    Read from the SOURCE, so it answers the same on every machine. The runtime
    look in `discover` is what catches a container this cannot prove; this is
    what catches one whose runtime value is missing or unrecognisable on the
    box the suite happens to run on. The two are a UNION for that reason —
    either alone leaves a hole that only opens somewhere else.

    MODULE SCOPE, through the shared `_module_level`. Walking the whole tree
    would collect container assignments from function and class bodies too,
    so a function-LOCAL `X = []` would promote an unrelated module-level
    scalar `X = 1` into the registry's subject.

    WHAT COUNTS AS PROOF, and the word is load-bearing. A shape is proved
    only where the AST alone settles it. The whole proved set:

      * a literal or comprehension;
      * a call to one of the six bare builtin names in `_CONTAINER_CALLS`;
      * a call to a module-level SYNCHRONOUS non-generator helper whose every
        OWN `return` carries a value that is itself proved WITHOUT resolving a
        bare name, AND whose body ENDS in one of those returns — the last
        clause is how an exit that falls off the end is refused without
        control-flow analysis, and without it in this paragraph the stated
        set would be wider than the code by a `try:`/`finally:` and a `with:`
        helper — `HELPER_CALL = _mk()`, the most idiomatic vocabulary spelling
        in a suite. Each qualifier closes a real hole, not caution: `ast.walk`
        reads a nested def's return as the outer helper's, a bare `return` is
        the `None` it yields, an `async def` yields a COROUTINE whatever it
        returns, and a helper's local or parameter that merely SPELLS a proved
        module name must not resolve against it — a local conflated with the
        module's binding. Each would promote a scalar into the registry's
        subject, and `_SHAPE_PROBE` carries a row for each;
      * a binop with a proved operand (`BASE + ("c",)`), an if-expression with
        two proved arms, or a bare alias of a proved name.

    THE RESIDUAL IS STATED because a bound described more widely than it is,
    is this harness's own defect class: an ATTRIBUTE call
    (`collections.Counter()`), a METHOD call (`base.copy()`), a SUBSCRIPT
    (`GROUPS[0]`), a non-`Add` binop, an UNPACKING whose two sides are not
    both sequences or where either side is starred, and every helper the
    qualifiers above rule out. These are NOT proved, deliberately —
    `ROOT / "x"` and `Path(__file__).parents[1]` are binops and subscripts
    too, and shaping every expression a module-level assignment can carry
    would promote 76 Paths and scalars in this tree alone into the registry's
    subject as vocabularies they are not.

    WHAT PINS THE BOUND, stated to what it does rather than to what would be
    reassuring: `_SHAPE_PROBE` and `_UNPACK_PROBE` in
    `test_guard_mutations.py` carry a row per shape ANYONE HAS NAMED, and
    `test_the_container_shaping_bound_is_pinned` asserts both directions
    over exactly those rows. A shape no row names is pinned by nothing, and a
    docstring saying the probe makes drift impossible would be false. So the
    probe keeps this paragraph honest about the shapes it lists, and the next
    escaping shape is found by a reader, not by the suite.

    Latent, not broken: `tests/` today has no name that reaches the residual,
    which is why the bound is driven by a synthetic fixture rather than by
    the tree.
    """
    tree = ast.parse(path.read_text())
    statements = list(_module_level(tree.body))
    # SYNCHRONOUS defs only, and only at module level. Calling an `async def`
    # yields a COROUTINE whatever the body returns, so proving it a container
    # is proving the wrong object.
    helpers = {node.name: node for node in tree.body
               if isinstance(node, ast.FunctionDef)
               and _returns_a_value(node)}
    proved: set[str] = set()

    def _proves(node, *, seen: frozenset, module_scope: bool) -> bool:
        """Is `node` provably a container?

        `module_scope` says whether a bare `ast.Name` here can be resolved.
        At module scope it is the module's own binding and `proved` answers;
        inside a HELPER's body it may be that helper's local or parameter,
        which merely SPELLS a module-level name — resolving it against
        `proved` there conflates a local with the module's binding and can
        promote a scalar. Unresolvable
        is not proved: the fail-closed direction here is to prove LESS, since
        a false proof puts a scalar in the registry's subject while a missed
        one is caught by the runtime half of the union.
        """
        if isinstance(node, _CONTAINER_LITERALS):
            return True
        if isinstance(node, ast.Name):
            return module_scope and node.id in proved
        if isinstance(node, ast.BinOp):
            # CONCATENATION only. `"%s and %s" % LITERAL` has a proved
            # right-hand side and produces a `str`; `ROOT / "x"` a
            # proved-looking shape and produces a Path. There are no
            # `Constant`-operand guards beside the `Add` test: a `Constant`
            # added to a container is a runtime `TypeError`, so only dead
            # source could reach them, and each of the three would be
            # deletable alone — `MOD_FORMAT` is refused by whichever is left.
            # One guard that can be pinned beats three that cover for each
            # other.
            return (isinstance(node.op, ast.Add)
                    and (_proves(node.left, seen=seen,
                                 module_scope=module_scope)
                         or _proves(node.right, seen=seen,
                                    module_scope=module_scope)))
        if isinstance(node, ast.IfExp):
            return (_proves(node.body, seen=seen, module_scope=module_scope)
                    and _proves(node.orelse, seen=seen,
                                module_scope=module_scope))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _CONTAINER_CALLS:
                return True
            helper = helpers.get(node.func.id)
            if helper is None or node.func.id in seen:
                return False        # unknown, or a recursive helper
            # `helpers` already refused a generator, a helper with a valueless
            # exit and one that falls off the end — see `_returns_a_value`.
            deeper = seen | {node.func.id}
            return all(
                _proves(exit_.value, seen=deeper, module_scope=False)
                for exit_ in _own_nodes(helper)
                if isinstance(exit_, ast.Return))
        return False

    # To a fixed point: `ALIAS = LITERAL` is proved only once `LITERAL` is, and
    # source order does not have to cooperate. Terminates because `proved` only
    # ever grows and a module's names are finite.
    grew = True
    while grew:
        grew = False
        for node in statements:
            pair = _assign_targets(node)
            if pair is None:
                continue
            targets, value = pair
            for target in targets:
                for name, bound in _paired(target, value):
                    if name in proved:
                        continue
                    if _proves(bound, seen=frozenset(), module_scope=True):
                        proved.add(name)
                        grew = True
    return proved


def _regex_source_names(path: Path) -> set[str]:
    """MODULE-LEVEL names whose STRING value this module feeds to `re`.

    Read from how the name is USED, not from what it holds: a name passed to
    any `re.*` call, or joined into one with `.join(...)`, is a regex source;
    a shell-script fixture that merely contains `|` is not, and no read of the
    characters can separate them.

    THE PROPERTIES, each of which can go wrong in one direction or the other:

    * THE SEED may be found anywhere, function bodies included. A
      `GLUE.join(...)` can happen at a call site inside a test, and that is how
      a separator vocabulary joined into a pattern is reached at all.
    * A ONE-HOP HELPER counts. `GUARD = _mk(_VOCAB)` with
      `def _mk(src): return re.compile(src)` feeds a vocabulary to `re` just
      as surely as `re.compile(_VOCAB)` does; seeing only the second would let
      `_VOCAB` reach the suite with no disposition while its structurally
      identical twin beside it is found, and the totality test would stay
      green. Which PARAMETERS of a module-level helper reach `re` is read off
      that helper's own body.
    * THE PATTERN, NOT THE SUBJECT. Only an `re.*` call's FIRST positional
      argument is read: every one of them takes the pattern there. Reading
      them all would make the haystack a regex source too, an accidental
      registry row.
    * THE SEED SKIPS A SHADOWED NAME. A bare name inside a function body is
      that function's local or parameter unless the function does not bind it,
      and collecting it anyway then intersecting with the module's own names
      re-admits any module-level name that merely SHARES THE SPELLING,
      however narrow the propagation is: a shell fixture named `script`
      beside a test whose local `script` reaches `re.search` would be a regex
      source. `_locally_bound` is the bound, and it honours
      `global`/`nonlocal`.
    * THE PROPAGATION and the RESULT are module scope only. Walking every
      `ast.Assign` in the tree returns LOCAL names: a local passed to
      `re.search` in one test would make every other test binding a local of
      that name propagate whatever it was built from, so shell fixtures would
      become registry candidates by that collision alone while structurally
      identical ones stayed out. The parts a regex source is BUILT from are
      still propagated — `GLUE = SPACE + WORDS + SPACE` is a module-level
      assignment — through the SAME `_assign_targets` / `_bound_names`
      normalisation the two sibling readers use, so an annotated or
      tuple-target binding propagates like any other. Repeated to a fixed
      point, which terminates because `used` only ever grows and the
      module's names are finite.

    THE BOUND: propagation runs over module-level ASSIGNMENTS. A source built
    inside a function and returned, or assembled by a call this cannot read,
    propagates nothing — the parts reach the suite with no disposition, and
    only a reader finds it.
    """
    tree = ast.parse(path.read_text())
    used: set[str] = set()

    def names_in(node) -> set[str]:
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}

    def _regex_parameters(func) -> set[str]:
        """Which of `func`'s parameters reach `re` inside its own body."""
        args = func.args
        declared = {a.arg for a in
                    args.posonlyargs + args.args + args.kwonlyargs}
        reached: set[str] = set()
        for node in ast.walk(func):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)):
                continue
            if (isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "re"):
                # THE FIRST POSITIONAL ARGUMENT ONLY. Every `re.*` function
                # takes the pattern there and the SUBJECT after it, and
                # reading every argument would make the haystack a regex
                # source too: prose such as
                # `test_review_round_isolation:_MANDATE_AS_SHIPPED`, reaching
                # `re.split` as the string being split, would become a
                # registry candidate while structurally identical siblings
                # stayed out.
                for arg in node.args[:1]:
                    reached |= names_in(arg) & declared
            elif node.func.attr == "join":
                reached |= names_in(node.func.value) & declared
        return reached

    helpers: dict[str, tuple[list[str], set[str]]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        reached = _regex_parameters(node)
        if reached:
            args = node.args
            helpers[node.name] = (
                [a.arg for a in args.posonlyargs + args.args], reached)

    def seed(node, shadowed: frozenset, in_class: frozenset) -> None:
        """Collect regex-source names from `node`, skipping SHADOWED ones.

        The seed may be found anywhere — `GLUE.join(...)` happens at a call
        site inside a test — but a bare name inside a function body is that
        function's local or parameter unless the function does not bind it.
        Collecting it anyway and then intersecting with the module's own names
        re-admits any module-level name that merely SHARES THE SPELLING, so
        shell fixtures become registry candidates by a name collision — and
        narrowing only the propagation leaves the same harm reachable through
        the seed.
        """
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            # A nested function DOES close over an enclosing function's
            # locals and does NOT see an enclosing CLASS body's names — a bare
            # name in a method resolves to the module global, never to a
            # sibling class attribute. So the function chain accumulates and
            # the class chain resets here.
            shadowed, in_class = shadowed | _locally_bound(node), frozenset()
        elif isinstance(node, ast.ClassDef):
            in_class = in_class | _locally_bound(node)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                               ast.GeneratorExp)):
            # A comprehension is its own scope in Python 3, so its targets
            # shadow inside it and nowhere else.
            shadowed = shadowed | {n.id for generator in node.generators
                                   for n in ast.walk(generator.target)
                                   if isinstance(n, ast.Name)}
        hidden = shadowed | in_class
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                reads_regex = (isinstance(func.value, ast.Name)
                               and func.value.id == "re")
                if reads_regex:
                    for arg in node.args[:1]:     # the pattern, not the
                        used.update(names_in(arg) - hidden)   # subject
                elif func.attr == "join":
                    # `GLUE.join(...)` — the separator IS the pattern's glue.
                    used.update(names_in(func.value) - hidden)
            elif (isinstance(func, ast.Name) and func.id in helpers
                  and func.id not in hidden):
                positions, reached = helpers[func.id]
                for index, arg in enumerate(node.args):
                    if index < len(positions) and positions[index] in reached:
                        used.update(names_in(arg) - hidden)
                for keyword in node.keywords:
                    if keyword.arg in reached:
                        used.update(names_in(keyword.value) - hidden)
        for child in ast.iter_child_nodes(node):
            seed(child, shadowed, in_class)

    seed(tree, frozenset(), frozenset())

    statements = list(_module_level(tree.body))
    grew = True
    while grew:
        grew = False
        for node in statements:
            pair = _assign_targets(node)
            if pair is None:
                continue
            # THE SHARED NORMALISATION, so an annotated or tuple-target
            # binding propagates like any other. Reading raw `ast.Assign` with
            # bare `ast.Name` targets would leave `GLUE: str = SPACE + "|" +
            # WORDS` and `_TGAP, _OTHER = ...` propagating nothing, so the
            # vocabulary parts they are built from would reach the suite with
            # no disposition while the totality test stays green.
            if set(_bound_names(pair[0])) & used:
                parts = names_in(pair[1]) - used
                if parts:
                    used |= parts
                    grew = True
    return used & set(_assigned_names(path))


def discover(tests_dir: Path | None = None, *,
             exclude: tuple[str, ...] = SELF) -> list[Candidate]:
    """Every module-level vocabulary the suite declares, with its shape read
    off the imported module.

    Derived, never enumerated: a hand-written list of guards is the exact
    shape this harness exists to refuse, so the registry next door must
    ACCOUNT for this list rather than define it.

    `exclude` defaults to `SELF` — the harness's own two files, which
    `REGISTRY` cannot classify without circularity. It is a PARAMETER rather
    than a hard-coded skip so the companion ledger
    (`test_the_harness_audits_its_own_two_files`) can point discovery AT
    those two files instead: without that, vocabularies in the harness itself
    would ship unpinned because nothing could audit them.
    """
    import importlib

    tests_dir = tests_dir or TESTS_DIR
    out: list[Candidate] = []
    for path in sorted(tests_dir.glob("*.py")):
        if path.name in exclude:
            continue            # see SELF: the harness does not audit itself
        module = importlib.import_module(path.stem)
        sources = _regex_source_names(path)
        shaped = _container_shaped(path)
        for name, lineno in sorted(_assigned_names(path).items()):
            value = getattr(module, name, None)
            if isinstance(value, str) and name not in sources:
                continue        # a string this module never feeds to `re`
            shape = classify(value)
            if shape is None and name in shaped:
                # The SOURCE says container, the runtime value did not answer.
                # Discoverable anyway: a name absent or unrecognisable at
                # runtime must not silently leave the registry's subject on one
                # machine and rejoin it on another.
                shape = ("sequence", 0)
            if shape is not None:
                out.append(Candidate(path.stem, name, lineno, *shape))
    return out


# --------------------------------------------------------------------------
# 2. Alternatives — what a mutation can delete
# --------------------------------------------------------------------------

class PatternUnreadable(Exception):
    """The enumerator met regex syntax it does not model.

    RAISED, never skipped. A pattern this cannot take apart is a pattern
    whose alternatives nothing here proves load-bearing, and reporting that
    as coverage is the failure the whole harness is against.
    """


@dataclass(frozen=True)
class Alternative:
    """One deletable piece of a guard, and the guard without it."""

    label: str           # human-readable, e.g. "branch 'moving'"
    mutated: object      # the neutered value to install

    def __str__(self) -> str:            # pragma: no cover - message sugar
        return self.label


# Scoped inline flags: at least one flag letter, or a negation, before the
# colon. `[aiLmsux]*` — zero letters allowed — would match a plain `(?:` too,
# so this arm would swallow EVERY non-capturing group and `("?:", 2)` in
# `_GROUP_PREFIXES` below could be deleted with the enumeration byte-identical
# for every pattern: the two arms advance `body` past the same colon, since
# `pattern.index(":", body)` always lands at `body + 1` there, so the
# behavioural control for `?:` could not fail and only the table-equality
# assertion beside it would hold the member up. With the `+` the arms are
# disjoint: `(?:` is the prefix table's, `(?i:` / `(?-s:` are
# this one's, and deleting either takes a named control red.
# `L` is deliberately absent from the letter class: `re.compile(r"(?L:a|b)")`
# refuses with "cannot use 'L' flag with a str pattern", and every guard this
# engine enumerates is a str pattern — so a `(?L:` group cannot reach here at
# all, and carrying the letter would leave one alternative of this vocabulary
# undriveable by any case. `a` and `u` DO scope and are kept, each with a case.
_SCOPED_FLAGS = r"\?(?:[aimsux]+(?:-[imsx]+)?|-[imsx]+):"

_GROUP_PREFIXES = (
    # (literal after "(", characters to skip past)
    ("?:", 2), ("?=", 2), ("?!", 2), ("?<=", 3), ("?<!", 3), ("?>", 2),
)


def _class_end(pattern: str, start: int) -> int:
    """Index of the `]` closing the character class opening at `start`."""
    i = start + 1
    if i < len(pattern) and pattern[i] == "^":
        i += 1
    if i < len(pattern) and pattern[i] == "]":
        i += 1                           # a literal `]` first is not a closer
    while i < len(pattern):
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] == "]":
            return i
        i += 1
    raise PatternUnreadable(f"unterminated character class at {start}")


def _class_members(pattern: str, open_at: int, close_at: int
                   ) -> list[tuple[int, int]]:
    """Spans of the individual members of one character class.

    An escape (`\\d`, `\\u00b6`) is one member; `a-z` is one member; every
    other character is one. A leading `^` is not a member.
    """
    i = open_at + 1
    if pattern[i] == "^":
        i += 1
    spans: list[tuple[int, int]] = []
    while i < close_at:
        start = i
        if pattern[i] == "\\":
            i += 2
            if i <= close_at and pattern[start + 1] in "xuUN":
                # \xNN, \uNNNN, \UNNNNNNNN, \N{...} — consume the payload so a
                # hex digit is never mistaken for a separate member.
                if pattern[start + 1] == "x":
                    i = start + 4
                elif pattern[start + 1] == "u":
                    i = start + 6
                elif pattern[start + 1] == "U":
                    i = start + 10
                else:
                    brace = pattern.find("}", i)
                    if brace == -1 or brace > close_at:
                        raise PatternUnreadable(
                            f"unterminated \\N{{...}} at {start}")
                    i = brace + 1
        else:
            i += 1
        if i < close_at and pattern[i] == "-" and i + 1 < close_at:
            i += 2                       # a range is one member
            if pattern[i - 1] == "\\":
                i += 1
        spans.append((start, i))
    return spans


def _branch_levels(pattern: str) -> list[list[tuple[int, int]]]:
    """Every alternation level in `pattern`, as the spans of its branches.

    Level 0 is the pattern itself; one more level per group that alternates.
    A level with fewer than two branches is not returned — there is nothing
    to delete from it.
    """
    levels: list[list[tuple[int, int]]] = []

    def walk(i: int, inside_group: bool) -> int:
        start = i
        cuts: list[int] = []
        while i < len(pattern):
            c = pattern[i]
            if c == "\\":
                i += 2
                continue
            if c == "[":
                i = _class_end(pattern, i) + 1
                continue
            if c == "(":
                body = i + 1
                if pattern.startswith("?", body):
                    for literal, width in _GROUP_PREFIXES:
                        if pattern.startswith(literal, body):
                            body += width
                            break
                    else:
                        if pattern.startswith("?P<", body):
                            close = pattern.find(">", body)
                            if close == -1:
                                raise PatternUnreadable(
                                    f"unterminated group name at {i}")
                            body = close + 1
                        elif re.match(_SCOPED_FLAGS, pattern[body:]):
                            # Scoped inline flags — `(?i:...)`, `(?-s:...)`.
                            # The flags are not an alternation and the group
                            # body is.
                            body += pattern.index(":", body) - body + 1
                        else:
                            raise PatternUnreadable(
                                f"group extension {pattern[body:body + 4]!r} "
                                f"at {i} is not modelled")
                i = walk(body, True)
                continue
            if c == ")":
                if inside_group:
                    break
                raise PatternUnreadable(f"unbalanced ) at {i}")
            if c == "|":
                cuts.append(i)
                i += 1
                continue
            i += 1
        else:
            if inside_group:
                raise PatternUnreadable(f"unterminated group from {start}")
        if len(cuts) >= 1:
            bounds = [start] + [c + 1 for c in cuts]
            ends = cuts + [i]
            levels.append(list(zip(bounds, ends)))
        return i + 1 if inside_group else i

    walk(0, False)
    return levels


def regex_alternatives(compiled: re.Pattern) -> list[Alternative]:
    """Every branch and every character-class member of `compiled`, each as
    the same pattern with that piece deleted.

    Both shapes, because both are how this repo's guards lose reach. A branch
    is a spelling of a separator or a verb (`moving`, `<br\\s*/?>`); a class
    member is a character a class admits (the `@` in `HOME_RE`'s user name).
    """
    source = compiled.pattern
    out: list[Alternative] = []
    for level in _branch_levels(source):
        whole = source[level[0][0]:level[-1][1]]
        where = "" if whole == source else f" of {whole!r}"
        for index, (start, end) in enumerate(level):
            if index:
                cut_from = level[index - 1][1]          # the `|` before it
            else:
                cut_from, end = start, level[1][0]      # …or the one after
            mutated = source[:cut_from] + source[end:]
            out.append(Alternative(
                f"branch {source[start:level[index][1]]!r}{where}",
                re.compile(mutated, compiled.flags)))
    i = 0
    while i < len(source):
        if source[i] == "\\":
            i += 2
            continue
        if source[i] != "[":
            i += 1
            continue
        close = _class_end(source, i)
        members = _class_members(source, i, close)
        if len(members) >= 2:
            for start, end in members:
                mutated = source[:start] + source[end:]
                out.append(Alternative(
                    f"class member {source[start:end]!r} "
                    f"in {source[i:close + 1]!r}",
                    re.compile(mutated, compiled.flags)))
        i = close + 1
    return _disambiguated(out)


def _disambiguated(alts: list[Alternative]) -> list[Alternative]:
    """Make every label unique, so no alternative can hide behind another.

    `cop(?:y|ies|ied|ying)` and `carr(?:y|ies|ied|ying)` contribute the same
    three labels, and a sweep that keyed its verdicts on the label alone
    recorded the first as pinned and read the second's finding off the same
    key — an alternative going quiet inside the harness built to find exactly
    that. The suffix is the occurrence number in enumeration order, which is
    deterministic for a given pattern.
    """
    seen: dict[str, int] = {}
    out = []
    for alt in alts:
        seen[alt.label] = seen.get(alt.label, 0) + 1
        out.append(alt if seen[alt.label] == 1
                   else Alternative(f"{alt.label} (#{seen[alt.label]})",
                                    alt.mutated))
    return out


def vocabulary_alternatives(value: object) -> list[Alternative]:
    """Every member of a sequence or mapping, each as the value without it."""
    if isinstance(value, dict):
        return [Alternative(f"key {k!r}",
                            {q: v for q, v in value.items() if q != k})
                for k in value]
    kind = type(value)
    members = list(value)
    out = []
    for index, member in enumerate(members):
        rest = members[:index] + members[index + 1:]
        out.append(Alternative(f"member {member!r}", kind(rest)))
    return _disambiguated(out)


def alternatives(value: object) -> list[Alternative]:
    if isinstance(value, re.Pattern):
        return regex_alternatives(value)
    if isinstance(value, (tuple, list, set, frozenset, dict)):
        return vocabulary_alternatives(value)
    raise PatternUnreadable(f"{type(value).__name__} is not a vocabulary "
                            "this engine can take apart")


# --------------------------------------------------------------------------
# 3. Running the tests of a module, for real
# --------------------------------------------------------------------------

class Unrunnable(Exception):
    """A test function the harness cannot call — it needs a fixture this
    shim cannot build. Counted and listed, never quietly dropped."""


def _fixture_function(obj):
    """The raw function behind an `@pytest.fixture`, or None if `obj` is not
    one.

    Both spellings, because pytest changed it: through pytest 8 the decorator
    returned the function with `_pytestfixturefunction` attached; pytest 9
    returns a `FixtureFunctionDefinition` holding `_fixture_function`. Reading
    only the older one made every `repo`-taking test in
    `test_round_isolation.py` unrunnable here — 18 tests the sweep would have
    looked for a witness among and not found, which is how a harness invents
    findings and hides coverage in one move.
    """
    for attribute in ("_fixture_function", "__wrapped__"):
        found = getattr(obj, attribute, None)
        if found is not None:
            return found
    if getattr(obj, "_pytestfixturefunction", None) is not None:
        return obj
    return None


def _resolve(module, name: str, stack: list):
    """One fixture value, built the way pytest would build it, or raise."""
    if name == "tmp_path":
        # RESOLVED, as pytest's own tmp_path is: on macOS the temp root is
        # `/var/...`, a symlink to `/private/var/...`, and an unresolved path
        # would make tests that compare resolved paths red under this shim
        # only.
        d = Path(tempfile.mkdtemp(prefix="guard-mut-")).resolve()
        # REMOVED on teardown, unlike pytest's own tmp_path. pytest keeps the
        # last few trees for a human to inspect after ONE run; a sweep calls
        # this once per alternative per test, and keeping them would leave
        # thousands of directories behind.
        stack.append(lambda: shutil.rmtree(d, ignore_errors=True))
        return d
    if name == "monkeypatch":
        mp = pytest.MonkeyPatch()
        stack.append(mp.undo)
        return mp
    raw = _fixture_function(getattr(module, name, None))
    if raw is None:
        raise Unrunnable(f"no fixture {name!r} reachable from "
                         f"{module.__name__}")
    kwargs = {p: _resolve(module, p, stack)
              for p in inspect.signature(raw).parameters}
    produced = raw(**kwargs)
    if inspect.isgenerator(produced):
        value = next(produced)
        stack.append(lambda g=produced: next(g, None))
        return value
    return produced


# What `pytest.param(...)` returns. Detected BY TYPE, using only public API,
# because the attribute it used to be detected by is not its own: a plain dict
# also carries `.values` — the builtin mapping method — so a parametrize case
# that is a dict read as a ParameterSet, `case = case.values` bound the method,
# and the next subscript raised. That crash took the WHOLE sweep down rather
# than reporting one guard, so any module holding a dict-valued case was
# silently out of reach of a harness whose contract is that its reach is
# counted.
_PARAM_SET = type(pytest.param(None))


def _parametrisations(func) -> list[dict]:
    """The argument dicts `@pytest.mark.parametrize` would run `func` with."""
    sets: list[dict] = [{}]
    for mark in reversed(getattr(func, "pytestmark", [])):
        if mark.name != "parametrize":
            continue
        names = mark.args[0]
        names = [n.strip() for n in names.split(",")] \
            if isinstance(names, str) else list(names)
        expanded = []
        for case in mark.args[1]:
            # `pytest.param(...)` is unwrapped BEFORE the single-name wrap, not
            # after: wrapping first put the param object itself in a 1-tuple and
            # the test then answered about the tuple, so a single-name
            # `pytest.param` was passed to the test raw.
            if isinstance(case, _PARAM_SET):
                case = case.values
                values = case if len(names) > 1 else case[0:1]
            else:
                values = case if len(names) > 1 else (case,)
            for base in sets:
                expanded.append({**base, **dict(zip(names, values))})
        sets = expanded
    return sets


def _unbuildable(module, names, seen: frozenset = frozenset()) -> list[str]:
    """The fixture names among `names` — TRANSITIVELY — that `_resolve` would
    raise on: not one of the two it builds itself, and not a fixture reachable
    from `module`.

    Transitive, because `_resolve` is: a module-local fixture's own parameters
    are resolved the same way, so a test taking `built` where `built` takes
    `tmp_path_factory` is as unrunnable as one taking `tmp_path_factory`
    directly. Reading only the test's own signature would report it runnable
    and then raise mid-sweep, crashing the whole sweep on the first such test
    rather than listing them.
    """
    out: list[str] = []
    for name in names:
        if name in ("tmp_path", "monkeypatch") or name in seen:
            continue
        raw = _fixture_function(getattr(module, name, None))
        if raw is None:
            out.append(name)
            continue
        out += _unbuildable(module, inspect.signature(raw).parameters,
                            seen | {name})
    return out


def callable_tests(module) -> tuple[list[str], dict[str, str]]:
    """(test names this harness can run, {name: why it cannot} for the rest).

    The second half is the honest one. A sweep that looked for a red test
    among only the tests it happened to be able to call, and reported the
    rest as absent, would manufacture findings and hide coverage at the same
    time — so the unrunnable set is returned and the registry has to account
    for it.
    """
    runnable, blocked = [], {}
    for name, func in vars(module).items():
        if not (name.startswith("test") and inspect.isfunction(func)):
            continue
        params = set(inspect.signature(func).parameters)
        supplied = set().union(*[set(p) for p in _parametrisations(func)]) \
            if _parametrisations(func) != [{}] else set()
        unknown = _unbuildable(module, sorted(params - supplied))
        if unknown:
            blocked[name] = f"needs fixture(s) {sorted(set(unknown))}"
        else:
            runnable.append(name)
    return sorted(runnable), blocked


def run_test(module, name: str) -> str | None:
    """Run one test of `module` for every parametrisation.

    Returns None when it passed, or the failure's first line when it went
    red. `pytest.skip` inside a test is not a red: a test that did not
    evaluate pins nothing, which is the fail-closed reading.
    """
    func = getattr(module, name)
    signature = inspect.signature(func)
    for case in _parametrisations(func):
        stack: list = []
        kwargs = dict(case)
        try:
            for param in signature.parameters:
                if param not in kwargs:
                    kwargs[param] = _resolve(module, param, stack)
            try:
                func(**kwargs)
            except Unrunnable:
                raise
            except (KeyboardInterrupt, SystemExit):
                # NOT a red test. Reading an interrupt as a witness would let a
                # Ctrl-C at the wrong moment report an unpinned alternative as
                # load-bearing — the sweep answering the question it was asked
                # with something that is not an answer.
                raise
            except pytest.skip.Exception:
                continue
            except BaseException as exc:          # noqa: BLE001 — the verdict
                first = str(exc).strip().splitlines()
                return f"{type(exc).__name__}: {first[0] if first else ''}"
        finally:
            for undo in reversed(stack):
                try:
                    undo()
                except Exception:                 # noqa: BLE001
                    pass
    return None


# --------------------------------------------------------------------------
# 4. The sweep
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    guard: str
    alternative: str

    def __str__(self) -> str:
        return f"{self.guard}: {self.alternative} is pinned by no test"


def sweep(module, name: str, *, pins: tuple[str, ...] = (),
          exempt: dict[str, str] | None = None,
          witnesses: list | None = None) -> tuple[list[Finding], dict[str, str]]:
    """Neuter each alternative of `module.name` and find a test it reddens.

    Returns (findings, {alternative: "module::test" that went red}).

    `witnesses` are the modules whose tests may be the witness, and it
    defaults to the guard's own. It is not cosmetic: `conftest.py` declares
    guards that only tests in OTHER modules consult, and a sweep that looked
    for a witness among conftest's own tests — it has none — reported every
    one of them unpinned. A harness that manufactures findings is as useless
    as one that hides them.

    `pins` is an ORDER hint only: the declared tests run first because they
    are the likely witnesses, and every other runnable test runs after, so a
    stale pin list costs time and never coverage. `exempt` maps an
    alternative label to the written reason it is knowingly unpinned; an
    exempt label that turns out to BE pinned is itself a finding, so no
    exemption can quietly outlive its reason.
    """
    exempt = exempt or {}
    original = getattr(module, name)
    order: list[tuple] = []
    for where in (witnesses or [module]):
        runnable, _ = callable_tests(where)
        order += [(where, t) for t in pins if t in runnable]
        order += [(where, t) for t in runnable if t not in pins]
    # EVERY binding of the same object, not only the declaring module's.
    # `from conftest import NAME` copies the reference into the consumer's
    # namespace, so setting it on `conftest` alone changed nothing the
    # consumer's tests read — and the sweep reported every alternative of a
    # working guard unpinned. Scoped to the declaring module plus the
    # declared witnesses, so nothing is rebound that this file did not name.
    bindings = [(module, name)]
    for where in (witnesses or []):
        bindings += [(where, attr) for attr, value in vars(where).items()
                     if value is original and (where, attr) not in bindings]
    findings: list[Finding] = []
    found: dict[str, str] = {}
    stale: list[str] = []
    guard = f"{module.__name__}:{name}"
    # The witnesses already found move to the front. Most alternatives of one
    # guard are pinned by the same test, so after the first the sweep usually
    # stops on its first try — which is the difference between a check a PR can
    # afford and one nobody runs. It changes the cost and never the verdict:
    # the whole order is still tried before an alternative is called unpinned.
    seen: list[tuple] = []
    # A RED IS A WITNESS ONLY IF THE SAME TEST IS GREEN WITH THE GUARD
    # RESTORED. A test that fails at baseline under this shim — one that needs
    # pytest's session fixtures, say — fails under every mutation too, and
    # reading that red as a witness would count every alternative of every
    # guard in its module as pinned, by a test that may never read the guard
    # at all. A shim difference is one source (a `tmp_path` left unresolved
    # on macOS, where pytest's is resolved — `_resolve` resolves it); a bare
    # interpreter with no session fixtures is another, reporting
    # `test_ambient_git`'s environment test red although it is green inside
    # the pytest session the sweep runs in. This check refuses either kind.
    # Checked once per test, on first sight, and only for a test that
    # went red — a test that stayed green under a mutation needs no
    # baseline.
    green_at_baseline: dict[tuple, bool] = {}
    for alt in alternatives(original):
        for holder, attr in bindings:
            setattr(holder, attr, alt.mutated)
        try:
            for where, test in seen + [o for o in order if o not in seen]:
                if run_test(where, test) is None:
                    continue
                if (where, test) not in green_at_baseline:
                    for holder, attr in bindings:
                        setattr(holder, attr, original)
                    green_at_baseline[(where, test)] = \
                        run_test(where, test) is None
                    for holder, attr in bindings:
                        setattr(holder, attr, alt.mutated)
                if not green_at_baseline[(where, test)]:
                    continue        # red with the guard intact: not a witness
                found[alt.label] = f"{where.__name__}::{test}"
                if (where, test) not in seen:
                    seen.insert(0, (where, test))
                break
        finally:
            for holder, attr in bindings:
                setattr(holder, attr, original)
        if alt.label in found:
            if alt.label in exempt:
                stale.append(alt.label)
        elif alt.label not in exempt:
            findings.append(Finding(guard, alt.label))
    for label in stale:
        findings.append(Finding(
            guard,
            f"{label} is listed as knowingly unpinned, but "
            f"{found[label]} goes red without it — delete the exemption"))
    return findings, found
