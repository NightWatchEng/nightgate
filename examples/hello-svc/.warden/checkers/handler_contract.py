"""hello-svc's project checker: the seam you reach for when a regex won't do.

The service's one hard structural contract: every route handler returns
``(status, body)`` — an int paired with a JSON-serializable dict — because
``route()`` feeds the pair straight into ``json.dumps``. A pattern cannot
see a return statement's SHAPE, so this rule is ``engine: python``: parse
the file, find the functions that declare the contract in their return
annotation, and flag returns that provably break it.

Contract for any project checker (platform side: warden/plugins.py): a
module in ``.warden/checkers/`` exporting ``CHECKERS`` mapping rule id to
``(ctx, params, files) -> list[finding-dict]``, the same signature as the
core pack. This code is repo-trusted — reviewed, gated, and hashed into
``rules_version`` like any other policy file; a checker edit is a policy
edit. Fail-closed rules the loader enforces: no shadowing core ids, no
duplicate ids, no silently skipped import failures.
"""

import ast

_CONTRACT_ANNOTATION = "tuple[int, dict]"


def _declares_contract(fn) -> bool:
    return fn.returns is not None and ast.unparse(fn.returns) == _CONTRACT_ANNOTATION


def _own_returns(fn):
    """Return statements belonging to `fn` itself — a nested function has
    its own contract (none), so its returns are not this function's."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Return):
            yield node
        stack.extend(ast.iter_child_nodes(node))


def _breaks_contract(value) -> str | None:
    """A reason string for a PROVABLE break, else None. Names, calls, and
    conditionals are not statically decidable — a deterministic gate stays
    quiet rather than guessing."""
    if isinstance(value, ast.Tuple):
        if any(isinstance(e, ast.Starred) for e in value.elts):
            # A starred element makes the runtime length undecidable —
            # arity-flagging it would be a guess, and a HIGH rule that
            # guesses blocks legitimate PRs (review round finding).
            return None
        if len(value.elts) != 2:
            return f"returns a {len(value.elts)}-tuple instead of (status, body)"
        return None
    if isinstance(value, ast.Dict):
        return "returns a bare dict — the status code is missing"
    if isinstance(value, ast.Constant):
        return f"returns the bare constant {value.value!r}"
    return None


def check_handler_contract(ctx, params, files):
    findings = []
    for name in files:
        content = ctx.read_head(name)
        if content is None:  # deleted at head — nothing to hold to a contract
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            # Not this rule's failure to report: the verify commands own
            # syntax, and a half-parsed guess would be nondeterministic.
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _declares_contract(node):
                continue
            for ret in _own_returns(node):
                if ret.value is None:
                    continue
                why = _breaks_contract(ret.value)
                if why:
                    findings.append({
                        "file": name,
                        "line": ret.lineno,
                        "finding": (f"{node.name}() declares the handler "
                                    f"contract -> tuple[int, dict] but {why}."),
                        "evidence": ast.unparse(ret),
                    })
    return findings


CHECKERS = {"handler-response-contract": check_handler_contract}
