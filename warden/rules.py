"""AI review rules: .md files with YAML frontmatter, plus the rules_version hash.

rules_version hashes what decides the review VERDICT: every rule file, the
findings schema, the mechanical checker code, the consuming repo's checker
plugins, and repo.yaml's `review:` subtree (blocking_severities is what turns
a finding into exit 1; context_excludes is what the gate can even see;
rules_dir is which ruleset loads). A checker edit is a policy
edit. Auto-derived, impossible to forget to bump.

Deliberately NOT hashed: repo.yaml keys
outside `review:` — components, risk_tiers, protected_paths, verify — steer
planning, design discipline, and verify scopes, never the review verdict for
a given diff, and hashing them would churn attestation cohorts on unrelated
edits. If such a key ever grows verdict influence, add it HERE consciously.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import yamlio

SEVERITIES = ("HIGH", "MEDIUM", "LOW")
# python = deterministic checker in mechanical.py runs in CI ($0);
# claude = judgment rule, evaluated pre-PR inside a Claude Code session.
ENGINES = ("python", "claude", "declarative")
# What a rule's findings are about. `warden round classify` counts a repair
# round only for behaviour findings; a rule that declares nothing is behaviour.
JUDGES = ("behaviour", "wording")

# Review surfaces beyond the rule files, hashed into rules_version when present.
# mechanical.py IS policy now — a checker edit changes gate behavior, so it must
# change the version cohort exactly like a rule edit would.
_EXTRA_SURFACES = (
    Path(__file__).resolve().parent / "schemas" / "review-findings.schema.json",
    Path(__file__).resolve().parent / "mechanical.py",
)


class RuleError(Exception):
    pass


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    engine: str
    applies_to: tuple[str, ...]
    body: str
    path: Path
    params: dict | None = None  # checker parameters — repo facts stay in the
    #                             rule file (policy layer), never in code
    excludes: tuple[str, ...] = ()  # per-rule carve-outs (global excludes
    #                                 would collide with checker params)
    checks: tuple[dict, ...] = ()   # engine:declarative — the rule file IS
    #                                 the checker (see declarative.py)
    paused: bool = False            # retro-proposed, founder-merged: the rule
    #                                 stops producing findings but keeps its
    #                                 history and its place in rules_version
    paused_reason: str = ""         # why — required when paused
    implements: tuple[str, ...] = ()  # guardrail catalog entry ids this rule
    #                                 answers. Declared, never inferred: the
    #                                 gap analysis reports an entry ENFORCED
    #                                 on this link AND only while the rule is
    #                                 unpaused (a paused rule produces no
    #                                 findings, so it enforces nothing),
    #                                 because guessing which rule subsumes
    #                                 which guardrail is the unevidenced
    #                                 claim the whole report exists to avoid.
    covers: tuple[str, ...] = ()    # `unmapped:<slug>` candidate classes this
    #                                 rule answers. Nothing but a declaration
    #                                 can link a corpus slug to a rule id when
    #                                 the two differ (docs-drift ->
    #                                 wiki-fidelity), and without the link
    #                                 `memory stats` keeps proposing a rule
    #                                 that already shipped — but the link
    #                                 stops the proposal only while the rule
    #                                 is unpaused (a paused rule enforces
    #                                 nothing, so its class re-opens as a
    #                                 candidate naming it).
    judges: str = "behaviour"       # one of JUDGES


_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a path glob with ** semantics to a regex over posix paths."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 3] == "**/":
                out.append(r"(?:[^/]+/)*")
                i += 3
            elif pattern[i:i + 2] == "**":
                out.append(r".*")
                i += 2
            else:
                out.append(r"[^/]*")
                i += 1
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(pattern: str, path: str) -> bool:
    return bool(_glob_to_regex(pattern).match(path))


def _parse(path: Path, raw: bytes | None = None) -> Rule:
    """One rule, read from `path` on disk — or from `raw`, the file's bytes
    read somewhere else (a commit), with `path` only naming it.

    The bytes route decodes exactly as the disk route does: utf-8-sig, then
    universal newlines, which is what `read_text` applies.
    """
    # utf-8-sig: a BOM would otherwise defeat the \A anchor with a misleading
    # "missing frontmatter" error.
    #
    # A file that OPENS and merely does not decode is an INVALID rule, not an
    # unreadable one, and the difference is load-bearing: `memory.ingest` asks
    # readability unconditionally and validity only when a sweep must resolve
    # rule_ids, and `cage/run.sh` keys on the exit code of the no-sweep case.
    # Refused HERE, as a `RuleError` naming the FILE, because
    # `UnicodeDecodeError` is a `ValueError` and neither of this loader's
    # callers' exception arms names it — unrefused, it would reach `cli.main`
    # as a traceback carrying a byte offset and no path.
    try:
        if raw is None:
            text = path.read_text(encoding="utf-8-sig")
        else:
            text = raw.decode("utf-8-sig").replace("\r\n", "\n") \
                .replace("\r", "\n")
    except UnicodeDecodeError as e:
        raise RuleError(f"{path}: not valid UTF-8 ({e})") from e
    m = _FRONTMATTER.match(text)
    if not m:
        raise RuleError(f"{path}: missing '---' YAML frontmatter block")
    try:
        meta = yamlio.load(m.group(1))
    except yaml.YAMLError as e:
        raise RuleError(f"{path}: frontmatter is not valid YAML: {e}") from e
    if not isinstance(meta, dict):
        raise RuleError(f"{path}: frontmatter must be a YAML mapping")
    missing = {"id", "severity", "engine", "applies_to"} - meta.keys()
    if missing:
        raise RuleError(f"{path}: frontmatter missing {sorted(missing)}")
    # Presence is not enough: `id` is the one field every downstream reader
    # treats as a comparable corpus KEY. `id: 7` yields an int and a blank
    # `id:` yields None — both ordinary authoring slips — and
    # `attest._check_rule_ids` would then raise TypeError on
    # `", ".join(sorted(valid))`. `memory ingest` catches that in the arm that
    # reports a malformed ARTIFACT, which names the run dir, no actionable
    # cause, and exits 0 — which `cage/run.sh` reads as "the corpus rebuilt".
    # Refused HERE, naming the rule FILE, because this is the only place that
    # knows which file the id came out of.
    if not isinstance(meta["id"], str) or not meta["id"].strip():
        raise RuleError(f"{path}: id must be a non-empty string "
                        f"({meta['id']!r}) — every reader of the corpus keys "
                        "on it")
    if meta["severity"] not in SEVERITIES:
        raise RuleError(f"{path}: severity {meta['severity']!r} not in {SEVERITIES}")
    if meta["engine"] not in ENGINES:
        raise RuleError(f"{path}: engine {meta['engine']!r} not in {ENGINES}")
    judges = meta.get("judges", "behaviour")
    if judges not in JUDGES:
        raise RuleError(f"{path}: judges {judges!r} not in {JUDGES}")
    params = meta.get("params")
    if params is not None and not isinstance(params, dict):
        raise RuleError(f"{path}: params must be a mapping")
    excludes = meta.get("excludes", [])
    if (not isinstance(excludes, list)
            or not all(isinstance(g, str) for g in excludes)):
        raise RuleError(f"{path}: excludes must be a list of glob strings")
    paused = meta.get("paused", False)
    if not isinstance(paused, bool):
        raise RuleError(f"{path}: paused must be true or false")
    paused_reason = meta.get("paused_reason", "")
    if not isinstance(paused_reason, str):
        raise RuleError(f"{path}: paused_reason must be a string")
    if paused and not paused_reason.strip():
        # A pause with no stated reason is indistinguishable from a rule
        # someone quietly switched off.
        raise RuleError(f"{path}: paused: true requires a paused_reason "
                        "(cite the refutation streak or the retro proposal)")
    if paused_reason and not paused:
        raise RuleError(f"{path}: paused_reason without paused: true")
    covers = meta.get("covers", [])
    if (not isinstance(covers, list)
            or not all(isinstance(s, str) for s in covers)):
        raise RuleError(f"{path}: covers must be a list of "
                        "'unmapped:' candidate slugs")
    # Same shape check the attest side applies to `unmapped:<slug>` (imported,
    # never re-implemented): a covers entry that is not a well-formed slug can
    # never match a corpus key, so it would sit there silently covering
    # nothing. Function-level import — attest imports this module.
    from .attest import UNMAPPED_PREFIX, _SLUG
    for slug in covers:
        bare = slug[len(UNMAPPED_PREFIX):] if slug.startswith(UNMAPPED_PREFIX) \
            else slug
        if not _SLUG.match(bare):
            raise RuleError(
                f"{path}: covers entry {slug!r} is not a well-formed candidate "
                "slug (lowercase words joined by '-', e.g. 'docs-drift') — it "
                "would match no corpus key and cover nothing")
    covers = [s[len(UNMAPPED_PREFIX):] if s.startswith(UNMAPPED_PREFIX) else s
              for s in covers]
    implements = meta.get("implements", [])
    if (not isinstance(implements, list)
            or not all(isinstance(s, str) for s in implements)):
        raise RuleError(f"{path}: implements must be a list of catalog entry ids")
    if implements:
        # A typo'd link is a link to nothing, and it would read as coverage
        # in the gap analysis — the one place a silent miss becomes "this
        # repo already guards that".
        from .catalog import CatalogError, shipped_entry_ids
        try:
            known = shipped_entry_ids()
        except CatalogError as e:
            raise RuleError(f"{path}: cannot validate implements: {e}") from e
        for entry_id in implements:
            if entry_id not in known:
                raise RuleError(
                    f"{path}: implements entry {entry_id!r} names no guardrail "
                    "catalog entry — see `warden catalog list`")
    checks = meta.get("checks", [])
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        raise RuleError(f"{path}: checks must be a list of mappings")
    if meta["engine"] == "declarative":
        if not checks:
            raise RuleError(f"{path}: engine:declarative needs a non-empty "
                            "'checks' list — the rule file is the checker")
        from .declarative import compile_check
        seen = set()
        for c in checks:
            try:
                compile_check(dict(c), str(path))
            except ValueError as e:
                raise RuleError(str(e)) from e
            if c["id"] in seen:
                raise RuleError(f"{path}: duplicate check id {c['id']!r}")
            seen.add(c["id"])
    elif checks:
        raise RuleError(f"{path}: 'checks' is only meaningful for "
                        f"engine:declarative, not engine:{meta['engine']}")
    globs = meta["applies_to"]
    if (not isinstance(globs, list) or not globs
            or not all(isinstance(g, str) for g in globs)):
        raise RuleError(f"{path}: applies_to must be a non-empty list of glob strings")
    body = m.group(2).strip()
    if not body:
        raise RuleError(f"{path}: rule body (the prompt text) is empty")
    return Rule(id=meta["id"], severity=meta["severity"], engine=meta["engine"],
                applies_to=tuple(globs), body=body, path=path, params=params,
                excludes=tuple(excludes), checks=tuple(checks),
                paused=paused, paused_reason=paused_reason,
                covers=tuple(covers), implements=tuple(implements),
                judges=judges)


def load_rules(rules_dir: Path) -> tuple[Rule, ...]:
    files = sorted(rules_dir.glob("*.md"))
    if not files:
        raise RuleError(f"no rule files in {rules_dir}")
    return _ruleset(tuple(_parse(f) for f in files))


def rules_from_bytes(files: list[tuple[Path, bytes]], where: str
                     ) -> tuple[Rule, ...]:
    """The ruleset in `files` — (name, bytes) pairs read from somewhere other
    than the working tree — parsed and validated exactly as `load_rules`
    does it from disk. `where` names the source when there is nothing."""
    if not files:
        raise RuleError(f"no rule files in {where}")
    return _ruleset(tuple(_parse(path, raw)
                          for path, raw in sorted(files, key=lambda f: f[0])))


def _ruleset(rules: tuple[Rule, ...]) -> tuple[Rule, ...]:
    ids = [r.id for r in rules]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise RuleError(f"duplicate rule ids: {sorted(dupes)}")
    # Check ids are the key the inline allow marker suppresses by, so they
    # must be unique across the whole ruleset, not just within one file —
    # two rules sharing a check id would both be silenced by one marker the
    # author wrote for only one of them.
    check_ids = [c["id"] for r in rules for c in r.checks]
    check_dupes = {i for i in check_ids if check_ids.count(i) > 1}
    if check_dupes:
        raise RuleError(
            f"duplicate check id(s) across rules: {sorted(check_dupes)} — "
            f"a warden:allow marker keys on the check id alone, so a shared "
            f"id would let one marker silence more than one rule's check")
    # Two rules claiming one candidate class makes "which rule answered this?"
    # unanswerable — `memory stats` builds one slug->rule map, so it would
    # report whichever rule file sorted LAST.
    covered = [s for r in rules for s in r.covers]
    clashes = {s for s in covered if covered.count(s) > 1}
    if clashes:
        raise RuleError(f"candidate class covered by more than one rule: "
                        f"{sorted(clashes)}")
    # A covers entry naming a declared rule is the same ambiguity through a
    # second door: that class is not a candidate, it IS
    # that rule, and the slug->rule map would attribute it by sort order.
    for rule in rules:
        for slug in rule.covers:
            if slug in ids:
                raise RuleError(
                    f"{rule.path}: covers entry {slug!r} names the declared "
                    f"rule {slug!r} — a rule that exists is not a candidate "
                    "class; drop the entry (findings belong under that id)")
    return rules


def _enforcement_surface(root: Path) -> bytes | None:
    """Canonical bytes of repo.yaml's `review:` subtree, the enforcement keys
    a verdict is judged under — or None when the repo declares
    no repo.yaml at all.

    Canonicalized (parse, then json.dumps with sorted keys) so a comment or
    whitespace reflow inside the block is not a new cohort, while any value
    change is. The return shapes are kept distinct on purpose:

    - None — no repo.yaml exists (FileNotFoundError from the read itself,
      never a stat that would collapse "cannot look" into "absent");
      nothing declared, nothing hashed.
    - ``unreadable`` — the file is there but cannot be read (permissions, a
      directory in its place, or a FIFO/socket/device whose read could never
      finish and is refused rather than waited on). Hashed, so
      the state is not silently identical to "no repo.yaml".
    - ``unparseable\\0<raw>`` / ``malformed\\0<raw>`` — invalid YAML, or
      YAML that is not a mapping. Raw bytes are hashed: failing closed,
      because skipping would freeze the version while the file — and
      possibly the enforcement inside it — keeps changing.
    - ``absent`` — a well-formed repo.yaml with no review block.
    - the canonical JSON of the review subtree — the normal case; if a
      YAML-native value in it has no JSON form (an unquoted date), the raw
      bytes are hashed instead of crashing every rules_version caller.
    """
    from .config import read_repo_yaml
    try:
        # The shared reader, so this docstring's promise — the None comes from
        # the READ, "never a stat that would collapse 'cannot look' into
        # 'absent'" — holds for inputs that never come back either. A plain
        # `read_bytes()` on a FIFO would block forever.
        raw = read_repo_yaml(root)
    except FileNotFoundError:
        return None
    except OSError:
        return b"unreadable"
    return enforcement_surface_of(raw)


def enforcement_surface_of(raw: bytes) -> bytes:
    """The hashed form of repo.yaml's `review:` subtree, from the file's
    bytes — wherever they were read (see `_enforcement_surface`)."""
    try:
        doc = yamlio.load(raw.decode("utf-8", errors="replace"))
    except yaml.YAMLError:
        return b"unparseable\0" + raw
    if not isinstance(doc, dict):
        return b"malformed\0" + raw
    review = doc.get("review")
    if review is None:
        return b"absent"
    try:
        return json.dumps(review, sort_keys=True, ensure_ascii=True).encode()
    except (TypeError, ValueError):
        return b"uncanonical\0" + raw


_UNREADABLE_TAIL = ("it is hashed into rules_version, so the version this "
                    "gate would judge a diff under cannot be computed")


def _hashed_files(directory: Path, suffix: str, what: str) -> list[Path]:
    """The files of `directory` this hash reads, or a named refusal.

    An ABSENT directory is not refused: a repo with no ruleset (or no
    checkers dir) hashes fine, and refusing it would turn every un-ruled
    consumer's gate red on a platform bump. Every other `OSError` — an
    unlistable dir, a file where a dir is declared — is named.
    """
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise RuleError(f"{what} {directory} cannot be listed ({exc}) — "
                        f"{_UNREADABLE_TAIL}") from exc
    return sorted(p for p in entries if p.name.endswith(suffix))


def _hashed_bytes(path: Path, what: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RuleError(f"{what} {path} cannot be read ({exc}) — "
                        f"{_UNREADABLE_TAIL}") from exc


def rules_version(rules_dir: Path, root: Path) -> str:
    """sha256 over sorted rule files + findings schema + checker code + the
    repo.yaml review subtree, first 12 hex.

    `root` (the directory holding repo.yaml) is REQUIRED, not defaulted: a
    version computed with the enforcement keys quietly missing would collide
    with one computed with them present — the exact invisible-policy-change
    this hash exists to prevent.

    NUL separators around name and content make file boundaries unambiguous —
    without them, distinct rulesets can concatenate to identical bytes.

    A SURFACE IT CANNOT READ IS REFUSED HERE, BY NAME. This function reads
    four surfaces raw and its many call sites do not catch `OSError`, so an
    unreadable surface (a `chmod 000` `.warden/checkers/*.py`, a documented
    consumer surface) would otherwise escape as a bare `PermissionError`
    traceback — and in `memory ingest`, after the shard, the cache row and the
    `.consumed` marker are already on disk. The refusal lives here rather
    than in any one caller's probe: a caller that walks its own subset of
    these surfaces is the defect, not the fix. `RuleError` is what `cli.main`
    renders as `warden: <message>` at exit 2.

    LISTING IS `iterdir`, NOT `glob`, for the same reason: `Path.glob`
    SWALLOWS `PermissionError` and returns `[]`, so an unlistable rules dir
    would hash as though the repo declared no rules at all — a different
    cohort, with nothing said. The two listings are otherwise the same set
    in the same order.

    repo.yaml is the one surface that does NOT refuse: `_enforcement_surface`
    hashes `unreadable` for it on purpose, so that "cannot look" is never
    silently identical to "absent". Every caller loads the config before it
    gets this far, so an unreadable repo.yaml has already refused upstream.
    """
    rule_files = ((f.name, _hashed_bytes(f, "rule file"))
                  for f in _hashed_files(rules_dir, ".md", "the rules dir"))
    # Project checker plugins are policy surface too: a
    # consuming repo's .warden/checkers/*.py edits change its gate cohort.
    plug_dir = rules_dir.parent / "checkers"

    def checkers():
        if plug_dir.is_dir():
            for f in _hashed_files(plug_dir, ".py", "the checkers dir"):
                yield f.name, _hashed_bytes(f, "checker plugin")
    return version_from(rule_files, checkers(), lambda: _enforcement_surface(root))


def version_from(rule_files, checker_files, surface) -> str:
    """The rules_version digest over surfaces already read: `rule_files` and
    `checker_files` are (name, bytes) pairs in name order, `surface` a callable
    returning `enforcement_surface_of` repo.yaml, or None when there is none.

    The ONE place the hash is composed. `rules_version` feeds it the working
    tree; `warden round classify` feeds it blobs read at a round head — so the
    two cannot compute the version two ways.
    """
    h = hashlib.sha256()
    for name, data in rule_files:
        h.update(name.encode() + b"\0")
        h.update(data + b"\0")
    for extra in _EXTRA_SURFACES:
        if extra.is_file():
            h.update(extra.name.encode() + b"\0")
            h.update(_hashed_bytes(extra, "review surface") + b"\0")
    for name, data in checker_files:
        h.update(("checkers/" + name).encode() + b"\0")
        h.update(data + b"\0")
    surface = surface()
    if surface is not None:
        h.update(b"repo.yaml:review\0")
        h.update(surface + b"\0")
    return h.hexdigest()[:12]


def applicable(rules: tuple[Rule, ...], changed_files: list[str]) -> dict[str, list[str]]:
    """Map rule id -> changed files it applies to (empty-matched rules omitted).

    Per-rule excludes are subtracted here so a carve-out for one rule (e.g. the
    gate's own fake-secret test fixtures) never blinds a different rule.
    """
    out: dict[str, list[str]] = {}
    for rule in rules:
        hits = [f for f in changed_files
                if any(glob_match(g, f) for g in rule.applies_to)
                and not any(glob_match(g, f) for g in rule.excludes)]
        if hits:
            out[rule.id] = hits
    return out
