"""engine:declarative rules — mechanical gating without writing Python.

The cheapest CI-enforced rule a project can own. A rule file declares its
own checks in frontmatter; this module executes them. No checker module, no
registry entry, no code review of Python — and because rule files are
already hashed into rules_version, a declarative check IS policy the moment
it lands.

    ---
    id: no-print-in-service
    severity: MEDIUM
    engine: declarative
    applies_to: ["src/**/*.py"]
    checks:
      - id: no-print
        pattern: '^\\s*print\\('
        message: "print() in service code — use the logger"
        globs: ["src/**/*.py"]     # optional: narrow within applies_to
        scope: added                # added (default) | file
        flags: [i, m]               # optional: i, m, s
        require: false              # true = the pattern MUST be present
    ---
    Prose body: why this rule exists (read by humans and by claude rules).

Severity stays rule-level, never per-check: severity decides what BLOCKS,
and a rule whose checks disagree about blocking has no coherent contract.

Trust model: patterns come from the consuming repo's own gate config —
reviewed, versioned, HIGH-tier by convention. A pathological pattern can
burn CI time the same way a pathological test can; that is a review
concern, not a sandbox concern.
"""

from __future__ import annotations

import re

from .diffs import DiffContext

# Only these regex flags: the rest either change match semantics in ways a
# reviewer would not expect (VERBOSE eating whitespace) or are locale-bound.
_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}
_SCOPES = ("added", "file")


def compile_check(check: dict, where: str) -> re.Pattern:
    """Validate one check dict and return its compiled pattern.

    Raises ValueError with a rule-file-anchored message — load-time
    validation, so a typo fails enrollment instead of silently never
    matching at 3am.
    """
    for field in ("id", "pattern", "message"):
        if not isinstance(check.get(field), str) or not check[field]:
            raise ValueError(f"{where}: check missing non-empty {field!r}")
    unknown = set(check) - {"id", "pattern", "message", "globs", "scope",
                            "flags", "require"}
    if unknown:
        # repr: a check block is YAML out of a rule file's frontmatter, whose
        # mapping keys need not be strings; sorted() over a mix raises
        # TypeError on the input the branch exists to refuse.
        raise ValueError(
            f"{where}: unknown check keys {sorted(map(repr, unknown))}")

    scope = check.get("scope", "added")
    if scope not in _SCOPES:
        raise ValueError(f"{where}: scope {scope!r} not in {_SCOPES}")
    if check.get("require") and scope != "file":
        # "must be present" is a statement about the file, not about the
        # lines this diff happened to touch.
        raise ValueError(f"{where}: require:true needs scope:file")

    globs = check.get("globs", [])
    if not isinstance(globs, list) or not all(isinstance(g, str) for g in globs):
        raise ValueError(f"{where}: globs must be a list of glob strings")

    flags = check.get("flags", [])
    if not isinstance(flags, list) or not all(f in _FLAGS for f in flags):
        raise ValueError(f"{where}: flags must be a subset of {sorted(_FLAGS)}")

    bits = 0
    for f in flags:
        bits |= _FLAGS[f]
    try:
        return re.compile(check["pattern"], bits)
    except re.error as e:
        raise ValueError(f"{where}: check {check['id']!r} pattern does not "
                         f"compile: {e}") from e


def _argument_has_substance(prose: str) -> bool:
    """SUBSTANCE, not length: forty dots and a bare URL both satisfied a
    len() >= 40 check. URLs are stripped BEFORE counting — a citation may
    SUPPORT an argument; it cannot be one. One floor for every field that
    can quiet the gate: an answer's reason, a ceiling's rationale, and an
    allow marker's reason (a literal '.' must not waive a live finding while
    the catalog demands 8 words to answer an entry)."""
    words = [w for w in re.split(r"\W+", re.sub(r"https?://\S+", " ", prose))
             if len(w) > 2]
    return len(words) >= 8 and len(set(words)) >= 6


# Per-line carve-out: `# warden:allow(<check-id>): <reason>`, on the matching
# line or the line directly above. The reason is REQUIRED and carries the
# same substance floor as an answer's reason — a bare or token reason
# suppresses nothing, because a carve-out with no argument is exactly the
# unexplained waiver this engine exists to refuse (each
# adjudicated line carries its verdict next to the code, and rule-level
# globs can only hide whole files). A marker naming a different check does
# not suppress. The marker must directly follow a comment leader (#, //,
# /*, <!--, ;, --): a marker buried in a string literal, docstring, or URL
# is camouflaged suppression a diff reader sees as data.
# Residual limit, stated: a comment-shaped marker INSIDE a string that
# includes the leader still matches — a line regex cannot parse host-language
# strings; that shape at least reads as a marker to a human.
_ALLOW = re.compile(r"(?:#|//|/\*|<!--|;|--)\s*"
                    r"warden:\s*allow\(([A-Za-z0-9_-]+)\)[\s:—-]*(\S.*)?")


def _allowed(check_id: str, lines: list[str], lineno: int) -> bool:
    """True when line `lineno` (1-based) or the line above carries a
    comment-anchored, substantively-reasoned allow marker for this check."""
    for idx in (lineno - 1, lineno - 2):
        if 0 <= idx < len(lines):
            m = _ALLOW.search(lines[idx])
            if (m and m.group(1) == check_id
                    and _argument_has_substance(m.group(2) or "")):
                return True
    return False


def run(rule, ctx: DiffContext, files: list[str]) -> list[dict]:
    """Execute a declarative rule's checks over the files it applies to."""
    from .rules import glob_match

    findings: list[dict] = []
    for check in rule.checks:
        pattern = compile_check(dict(check), f"{rule.path}")
        globs = check.get("globs") or []
        targets = [f for f in files
                   if not globs or any(glob_match(g, f) for g in globs)]

        for name in targets:
            # Read head lazily: it is a `git show` subprocess per call, and
            # the no-match majority of (check, file) pairs never needs it.
            head_lines: list[str] | None = None

            def _head_lines() -> list[str]:
                nonlocal head_lines
                if head_lines is None:
                    head_lines = (ctx.read_head(name) or "").splitlines()
                return head_lines

            if check.get("scope", "added") == "added":
                for lineno, text in ctx.added.get(name, ()):
                    if not pattern.search(text):
                        continue
                    # The head content locates the line above; the added
                    # line's own text is checked directly so a same-line
                    # marker works even if head is unavailable.
                    if (_ALLOW.search(text)
                            and _allowed(check["id"], [text], 1)):
                        continue
                    if _allowed(check["id"], _head_lines(), lineno):
                        continue
                    findings.append(_finding(check, name, text, lineno))
                continue

            content = ctx.read_head(name)
            if content is None:  # deleted in this diff — nothing to assert
                continue
            head_lines = content.splitlines()
            if check.get("require"):
                if pattern.search(content) is None:
                    findings.append(_finding(
                        check, name, f"pattern {check['pattern']!r} not found"))
                continue
            for match in pattern.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                if _allowed(check["id"], head_lines, line):
                    # An allowed match must not mask a later unallowed one —
                    # keep scanning; report the first that carries no marker.
                    continue
                findings.append(_finding(
                    check, name, match.group(0)[:200], line))
                break  # one finding per file for scope:file, as before
    return findings


def _finding(check: dict, file: str, evidence: str, line: int | None = None) -> dict:
    out = {"file": file, "finding": f"[{check['id']}] {check['message']}",
           "evidence": evidence.strip()[:300] or "(empty match)"}
    if line is not None:
        out["line"] = line
    return out
