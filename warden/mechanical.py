"""Deterministic checkers for engine:python rules — the $0 CI gate.

One checker per rule id, operating on a DiffContext. Repo-specific facts
(companion files, symbols, fixture paths) come from the rule's frontmatter
`params`, never from this module — the anti-shadow-copy invariant holds.
This file is hashed into rules_version: a checker edit is a policy edit.

Checkers return findings WITHOUT rule_id/severity (review.py stamps those from
the rule). Precision over recall: the judgment tail belongs to pre-PR review.
"""

import json
import math
import re
from collections.abc import Callable

from .diffs import DiffContext
from .rules import glob_match

Finding = dict
Checker = Callable[[DiffContext, dict, list[str]], list[Finding]]

# --- secrets-in-diff ---------------------------------------------------------

_SECRET_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"), "Anthropic API key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "GitHub personal access token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
     "JWT"),
    (re.compile(r"postgres(?:ql)?://[^\s:/@]+:[^@\s]+@"), "DSN with embedded password"),
    (re.compile(r"""(?ix) \b (?:api[_-]?key|secret|token|password) \b
                    \s* [:=] \s* ["'][A-Za-z0-9+/_-]{16,}["']"""),
     "hardcoded credential assignment"),
)
_PLACEHOLDER = re.compile(r"(?i)xxx|example|placeholder|your_key|your-key|<[^>]+>")
# NOT bare "key" — Swift's forKey: label appears on every UserDefaults call.
_IOS_DEFAULTS = re.compile(r"(?i)UserDefaults.*(token|secret|password|api_?key|credential)")


def check_secrets(ctx: DiffContext, params: dict, files: list[str]) -> list[Finding]:
    findings = []
    for name in files:
        for lineno, text in ctx.added.get(name, ()):
            hit = False
            for pattern, label in _SECRET_PATTERNS:
                # Placeholder test applies to the MATCHED substring, not the
                # whole line — 'example.com' elsewhere on the line must not
                # whitelist a real key (review G2-5).
                if any(not _PLACEHOLDER.search(m.group(0))
                       for m in pattern.finditer(text)):
                    findings.append({
                        "file": name, "line": lineno,
                        "finding": f"Added line introduces a {label}.",
                        "evidence": text.strip()})
                    hit = True
                    break
            if (not hit and name.endswith(".swift") and _IOS_DEFAULTS.search(text)
                    and not _PLACEHOLDER.search(text)):
                findings.append({
                    "file": name, "line": lineno,
                    "finding": "Secret-like value written to UserDefaults — "
                               "secrets belong in Keychain (repo rule).",
                    "evidence": text.strip()})
    return findings

# --- prompt-eval-gate --------------------------------------------------------


def _extract_segment(content: str | None, symbol: str) -> str | None:
    """The top-level assignment or def block for `symbol`. Triple-quoted string
    assignments are matched to their closing quotes FIRST — prompt bodies live
    at column 0, so the next-top-level-statement heuristic would stop at the
    string's first line and miss interior edits (the G2-4 point of this rule).
    """
    if content is None:
        return None
    esc = re.escape(symbol)
    m = (re.search(rf'(?ms)^{esc}\s*=\s*[rbf]*""".*?"""', content)
         or re.search(rf"(?ms)^{esc}\s*=\s*[rbf]*'''.*?'''", content)
         or re.search(rf"(?ms)^(?:{esc}\s*=|def\s+{esc}\b).*?(?=^\S|\Z)", content))
    return m.group(0) if m else None


def check_prompt_eval_gate(ctx: DiffContext, params: dict,
                           files: list[str]) -> list[Finding]:
    companion = params["companion_file"]
    symbols = params["surface_symbols"]
    if companion in ctx.files:
        return []
    findings = []
    for name in files:
        base_content = ctx.read_base(name)
        head_content = ctx.read_head(name)
        changed_symbols = []
        extracted_any = False
        for sym in symbols:
            old_seg = _extract_segment(base_content, sym)
            new_seg = _extract_segment(head_content, sym)
            if old_seg is not None or new_seg is not None:
                extracted_any = True
                if old_seg != new_seg:
                    changed_symbols.append(sym)
        if not extracted_any:
            # Revision content unavailable or symbols not defined here: fall
            # back to the changed-line heuristic rather than staying silent.
            touched = any(any(sym in text for sym in symbols)
                          for _, text in ctx.added.get(name, ()))
            touched |= any(any(sym in text for sym in symbols)
                           for text in ctx.removed.get(name, ()))
            if touched:
                changed_symbols = ["<changed-line heuristic>"]
        if changed_symbols:
            findings.append({
                "file": name,
                "finding": "Prompt surface changed "
                           f"({', '.join(changed_symbols)}) but the eval "
                           f"baseline ({companion}) is absent from this diff — "
                           "prompt edits are eval-gated with a same-commit "
                           "ratchet.",
                "evidence": f"surface segments differ between base and head: "
                            f"{', '.join(changed_symbols)}"})
    return findings

# --- baseline-ratchet --------------------------------------------------------


def _parse_baseline(content: str | None) -> float | None:
    if content is None:
        return None
    try:
        value = float(content.strip())
    except ValueError:
        return None
    # nan defeats every comparison (nan<old and nan>old are both False), which
    # would silently disable the ratchet forever (review G2-3).
    return value if math.isfinite(value) else None


def check_baseline_ratchet(ctx: DiffContext, params: dict,
                           files: list[str]) -> list[Finding]:
    findings = []
    for name in files:
        old = _parse_baseline(ctx.read_base(name))
        new = _parse_baseline(ctx.read_head(name))
        if new is None:
            findings.append({"file": name,
                             "finding": "Baseline is no longer a parseable number.",
                             "evidence": (ctx.read_head(name) or "<deleted>")[:100]})
            continue
        if old is not None and new < old:
            findings.append({
                "file": name,
                "finding": f"Baseline lowered from {old} to {new} — the ratchet "
                           "may only rise.",
                "evidence": f"{old} -> {new}"})
        elif old is not None and new > old:
            justified = any(
                any(glob_match(g, f) for g in params["justification_globs"])
                for f in ctx.files)
            if not justified:
                findings.append({
                    "file": name,
                    "finding": f"Baseline raised from {old} to {new} with no "
                               "accompanying eval/prompt change in this diff to "
                               "justify it.",
                    "evidence": f"{old} -> {new}"})
    return findings

# --- contract-freeze ---------------------------------------------------------


def _schema_breaks(old: dict, new: dict, path: str = "") -> list[str]:
    """Structural breaking changes: required additions/removals of properties,
    type changes, enum value removals."""
    breaks = []
    if isinstance(old, dict) and isinstance(new, dict):
        old_req = set(old.get("required", [])) if isinstance(old.get("required"), list) else set()
        new_req = set(new.get("required", [])) if isinstance(new.get("required"), list) else set()
        for added in sorted(new_req - old_req):
            breaks.append(f"{path or '<root>'}: '{added}' became required")
        old_props = old.get("properties", {})
        new_props = new.get("properties", {})
        if isinstance(old_props, dict) and isinstance(new_props, dict):
            for name in sorted(set(old_props) - set(new_props)):
                breaks.append(f"{path or '<root>'}: property '{name}' removed")
            for name in sorted(set(old_props) & set(new_props)):
                breaks += _schema_breaks(old_props[name], new_props[name],
                                         f"{path}/{name}")
        if "type" in old and "type" in new and old["type"] != new["type"]:
            breaks.append(f"{path or '<root>'}: type {old['type']} -> {new['type']}")
        if isinstance(old.get("enum"), list) and isinstance(new.get("enum"), list):
            for gone in [v for v in old["enum"] if v not in new["enum"]]:
                breaks.append(f"{path or '<root>'}: enum value {gone!r} removed")
        # Numeric constraint tightening is breaking too (review G2-8): raising
        # a floor or lowering a ceiling can invalidate existing documents.
        for key in ("minLength", "minItems", "minProperties", "minimum"):
            if (isinstance(old.get(key), (int, float))
                    and isinstance(new.get(key), (int, float))
                    and new[key] > old[key]):
                breaks.append(f"{path or '<root>'}: {key} {old[key]} -> {new[key]}")
        for key in ("maxLength", "maxItems", "maxProperties", "maximum"):
            if (isinstance(old.get(key), (int, float))
                    and isinstance(new.get(key), (int, float))
                    and new[key] < old[key]):
                breaks.append(f"{path or '<root>'}: {key} {old[key]} -> {new[key]}")
        if isinstance(old.get("items"), dict) and isinstance(new.get("items"), dict):
            breaks += _schema_breaks(old["items"], new["items"], f"{path}[]")
    return breaks


def check_contract_freeze(ctx: DiffContext, params: dict,
                          files: list[str]) -> list[Finding]:
    schema_file = params["schema_file"]
    if schema_file not in ctx.files:
        return []
    findings = []
    missing = [f for f in params["fixture_files"] if f not in ctx.files]
    if missing:
        findings.append({
            "file": schema_file,
            "finding": "Contract schema changed but fixtures did not move in the "
                       "same diff: " + ", ".join(missing),
            "evidence": f"changed files lack: {', '.join(missing)}"})
    try:
        old = json.loads(ctx.read_base(schema_file) or "null")
        new = json.loads(ctx.read_head(schema_file) or "null")
    except json.JSONDecodeError:
        old = new = None
    if isinstance(old, dict) and isinstance(new, dict):
        for break_desc in _schema_breaks(old, new):
            findings.append({
                "file": schema_file,
                "finding": f"Breaking schema change: {break_desc}. The contract "
                           "is additive-only (shipped clients).",
                "evidence": break_desc})
    return findings

# --- migration-safety --------------------------------------------------------

_DESTRUCTIVE = re.compile(
    r"(?is)\b(?:drop\s+table|drop\s+column|truncate\s)")
_DELETE = re.compile(r"(?is)\bdelete\s+from\b")
_NOT_NULL_ADD = re.compile(
    r"(?is)\b(?:add\s+column\s+.*\bnot\s+null|alter\s+column\s+\S+\s+set\s+not\s+null)")
# Marker requires a reason and covers ONLY the next statement — one marker must
# not launder every destructive statement in the file (review G2-6).
_ALLOW_MARKER = re.compile(r"--\s*warden:allow-destructive\s+\S+")


def _statements(content: str) -> list[tuple[str, bool]]:
    """(statement text, allowed) pairs. Statements split on ';' with EOF as a
    terminator (G2-7); '--' comments stripped from statement text so 'where'
    in a comment can't suppress a finding. `allowed` is true only when a
    marker comment sits between the previous statement and this one."""
    out: list[tuple[str, bool]] = []
    buf: list[str] = []
    buf_allow = False
    pending_allow = False
    for line in content.splitlines():
        if _ALLOW_MARKER.search(line):
            pending_allow = True
        code = line.partition("--")[0]
        if not code.strip():
            continue
        parts = code.split(";")
        for finished in parts[:-1]:
            if not buf:
                buf_allow, pending_allow = pending_allow, False
            buf.append(finished)
            stmt = "\n".join(buf).strip()
            if stmt:
                out.append((stmt, buf_allow))
            buf, buf_allow = [], False
        if parts[-1].strip():
            if not buf:
                buf_allow, pending_allow = pending_allow, False
            buf.append(parts[-1])
    stmt = "\n".join(buf).strip()
    if stmt:
        out.append((stmt, buf_allow))
    return out


def check_migration_safety(ctx: DiffContext, params: dict,
                           files: list[str]) -> list[Finding]:
    findings = []
    for name in files:
        existed = ctx.read_base(name) is not None
        content = ctx.read_head(name)
        if existed:
            findings.append({
                "file": name,
                "finding": "Edits an existing migration — history is immutable; "
                           "fixes go in a new migration.",
                "evidence": "file exists at merge-base and is modified"})
            continue
        if content is None:
            continue  # deleted file; the 'edits existing' rule is the guard
        for stmt, allowed in _statements(content):
            if allowed:
                continue
            if _DESTRUCTIVE.search(stmt):
                findings.append({
                    "file": name,
                    "finding": "Destructive DDL without an immediately preceding "
                               "'-- warden:allow-destructive <reason>' marker.",
                    "evidence": stmt[:200]})
            elif _DELETE.search(stmt) and not re.search(r"(?i)\bwhere\b", stmt):
                findings.append({
                    "file": name,
                    "finding": "DELETE without WHERE in a migration.",
                    "evidence": stmt[:200]})
            elif _NOT_NULL_ADD.search(stmt) and not re.search(r"(?i)\bdefault\b", stmt):
                findings.append({
                    "file": name,
                    "finding": "NOT NULL added without a DEFAULT/backfill in the "
                               "same statement — existing rows will fail.",
                    "evidence": stmt[:200]})
        if (re.search(r"(?i)\bcreate\s+table\b", content)
                and "ROW LEVEL SECURITY" not in content.upper()):
            findings.append({
                "file": name,
                "finding": "New table without ENABLE ROW LEVEL SECURITY + "
                           "policy in the same migration.",
                "evidence": "CREATE TABLE present; no ROW LEVEL SECURITY"})
    return findings


# --- tests-accompany-logic -------------------------------------------------

# The MECHANICAL half of the Definition of Done, promoted out of the
# tests-required claude rule: added logic under the scoped tree with NO change
# under the companion (tests) tree in the same diff. The generic shape of
# "added logic" — a function definition or a control-flow keyword at statement
# start — lives here, not in params (it is not repo-specific, the same footing
# as the secret patterns above); the repo-specific companion tree and exempt
# paths come from params. A line is logic only when the keyword STARTS the
# statement (after indentation), so a comment or a string that merely contains
# `if`/`def` is not matched. The regression-NAMING half a checker cannot decide
# stays engine:claude on tests-required.
# The keywords are all HARD keywords (cannot be identifiers), so a plain
# variable never false-fires. `case`/`match` are deliberately EXCLUDED: they
# are SOFT keywords, so `case = 5` is a valid identifier assignment that would
# misfire, and a match/case arm's enclosing block is normally a def/if that is
# matched anyway.
_ADDED_LOGIC = re.compile(
    r"^\s*(?:async\s+)?def\s|^\s*(?:if|elif|for|while|with|try|except)\b")


def check_tests_accompany_logic(ctx: DiffContext, params: dict,
                                files: list[str]) -> list[Finding]:
    companion = params["companion_prefix"]
    exempt = params.get("exempt_globs", [])
    # The companion check reads ctx.all_files (every changed path), NOT
    # ctx.files: this repo's context_excludes hides tests/ from the scanned
    # set, so `files` never contains a test file even when one changed. Reading
    # all_files is what lets the checker see the companion moved with the logic
    # without scanning its excluded content. If the
    # companion tree changed anywhere, the DoD is satisfied mechanically — this
    # checker asks only "did tests move with the logic", never "does the test
    # actually cover it" (that judgment stays claude on tests-required).
    if any(f.startswith(companion) for f in ctx.all_files):
        return []
    findings = []
    for name in files:
        if any(glob_match(g, name) for g in exempt):
            continue
        for lineno, text in ctx.added.get(name, ()):
            if _ADDED_LOGIC.match(text):
                findings.append({
                    "file": name,
                    "finding": (f"Adds logic under {name} with no change under "
                                f"{companion} in the same diff — new logic "
                                "carries a test (Definition of Done)."),
                    "evidence": f"{name}:{lineno}: {text.strip()[:120]}"})
                break  # one finding per file is enough to name the gap
    return findings


CHECKERS: dict[str, Checker] = {
    "secrets-in-diff": check_secrets,
    "prompt-eval-gate": check_prompt_eval_gate,
    "baseline-ratchet": check_baseline_ratchet,
    "contract-freeze": check_contract_freeze,
    "migration-safety": check_migration_safety,
    "tests-accompany-logic": check_tests_accompany_logic,
}
