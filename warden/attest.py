"""Pre-PR review attestation: the contract between Claude Code orchestration
and warden's evidence chain.

The pre-pr-review skill (code-reviewer + cross-examiner subagents) emits a
findings JSON; `warden attest write` validates it, stamps SHAs + rules_version
+ timestamp, and files it as evidence.

`warden attest check` is the CI end of that contract: it asserts that an
attestation was produced for the range. What it asserts is stated on
`check_range` — read that docstring before changing this, especially the two
things it is deliberately NOT.

Every finding's rule_id must resolve: either it names a rule declared in the
repo's rules_dir, or it carries the explicit `unmapped:` prefix. The schema
alone only says "a string", and per-rule precision, the promotion gate, and
`paused_rule_ids` all key on rule id, so a free-form id is a corpus key none
of them can see. `unmapped:<slug>` is not a loophole: "reviewers keep
finding something no rule covers" is the strongest input the rule advisor has,
and it only stays legible if it is structurally distinguishable.
"""

import hashlib
import json
import os
import stat
import subprocess
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

from . import runs
from .rules import load_rules

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "attestation.schema.json"
_validator = Draft202012Validator(json.loads(_SCHEMA_PATH.read_text()))

UNMAPPED_PREFIX = "unmapped:"
# The verdict vocabulary, READ from the schema rather than retyped. `memory
# ingest` never calls `build`, so it needs this list to check a findings-free
# artifact's verdict — and a second hand-copied tuple would drift exactly the
# way `_check_rule_ids` and `_SECRET_PATTERNS` are imported to prevent. Adding
# a value to the enum is the change the contract-freeze gate calls safe, so it
# is the one that must not silently break ingest.
VERDICTS = tuple(_validator.schema["properties"]["verdict"]["enum"])
# The one verdict `attest check` accepts on a TIP attestation of the range.
# A member of VERDICTS above, held by a test, so a vocabulary
# change cannot leave the gate comparing against a string the contract dropped.
CLEAN_VERDICT = "clean"
# SHAPE only — lowercase words joined by '-', so the id stays a comparable
# corpus key. It deliberately does not claim to govern VOCABULARY: nothing
# here stops 'unmapped:doc-drift' and 'unmapped:docs-drift' becoming two
# keys for one class. That governance already exists for tags
# (.warden/memory/tags.yaml + tags.unknown_tags/near_duplicates) and wiring
# unmapped slugs into it is not a claim this regex makes.
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
# A slug that names no defect class is the bare escape id wearing a prefix:
# 'unmapped:general' carries exactly as much signal as 'general' did.
_NON_DESCRIPTIVE_SLUGS = frozenset({
    "general", "generic", "other", "misc", "miscellaneous", "unknown",
    "none", "na", "n-a", "todo", "tbd", "various", "unmapped"})


class AttestError(Exception):
    """An attestation that does not validate, naming the id that raised it.

    `rule_id` lets a caller ask WHICH id failed instead of parsing the
    message: certify's E-02 must tell a shadowed class (a rule shipped for a
    candidate slug, whose remedy is a grandfather declaration) apart from a
    typo (whose remedy is emphatically not), and a message parse would break
    the moment the wording changed. Empty for errors that name no single id.
    """

    def __init__(self, message: str, *, rule_id: str = ""):
        super().__init__(message)
        self.rule_id = rule_id


# WHAT A HOME DIRECTORY IS, for the finding text `build` accepts. This is the
# tree scan's definition (tests/test_docs.py `HOME_RE`/`HOME_PLACEHOLDERS`),
# held equal to it by a parity test rather than retyped from memory: a shard
# attest accepted and the tree scan then flagged is the churn this exists to
# end, so the writer and the scan must not be able to disagree about a leak.
# One character of a directory's name, as HOME_RE reads a home's name.
_NAME_CHAR = r"[A-Za-z0-9_.@-]"
HOME_RE = re.compile(r"/(?:Users|home)/(" + _NAME_CHAR + "+)")
HOME_PLACEHOLDERS = frozenset({"me", "agent", "runner", "..."})
# Where a local absolute path may START in prose. The lookbehind is what keeps
# a rewrite from changing meaning: a `/` that continues a word or a relative
# path, the path of a URL (`file:///...`), a `host:` prefix or a drive letter
# (`C:`) does not begin a local path, so it is left exactly as written.
# `HOME_RE` carries no such lookbehind, so a prefixed home path still refuses.
_PATH_START = r"(?<![\w.~@/:-])"
# A home directory is refused wherever it starts, prefixed or not.
_HOME_START = r"(?<![\w.~@-])"
# One character of a path: anything but whitespace, a quote, a bracket or a
# separator, and a `/` unless it follows one of the separators `=`, `&`, `?`
# or `:`. A `/` there begins ANOTHER absolute path, so the path before it
# ends: two paths in one token are each rewritten at their own boundaries,
# and a colon-joined one is left as written instead of normalised across the
# colon. Every other character stays inside the path, so a folder such as
# `c++`, `C#` or `50%` is never cut in two.
_PATH_CHAR = r"(?:[^\s'\"`<>()\[\]{}|,;/]|(?<![=&?:])/)"
# The rest of a path after a prefix; its first `/` always continues it, even
# after a prefix ending on a bracket (`My Projects (copy)`).
_PATH_TAIL = re.compile(r"/?" + _PATH_CHAR + "*")
# The same path read as one, every `/` continuing it. The cut before a `/`
# after `=`, `&`, `?` or `:` is only a reading: the text is equally a folder
# whose name ends in that character, so a rewrite must hold on both readings,
# or a `..` after such a folder walks out unchecked.
_TOKEN_TAIL = re.compile(r"[^\s'\"`<>()\[\]{}|,;]*")
# A path the generic pattern can see (no space or bracket inside it).
_ABS_PATH = re.compile(_PATH_START + r"/" + _PATH_CHAR + "+")
# What ends the text a left-as-written path follows, reading backwards.
_FOLLOWS_STOP = frozenset(" \t\n\r'\"`<>()[]{}|,;")
# Sentence punctuation a path in prose often ends on; not part of the path.
_TRAILING_PUNCT = ".,:;!?"
# A final `..` segment: `..` not continued by a name character, so `...`
# and `..foo` are names, while `?`, `:12`, `&k=v` or `#L12` trail the path.
_DOTDOT_END = re.compile(r"\.\.(?!" + _NAME_CHAR + ")")
_LEADING_DOTS = re.compile(r"\.*")
# A final run of three or more dots not continued by a name character.
_DOTS_END = re.compile(r"\.{3,}(?!" + _NAME_CHAR + ")")


def repo_roots(root: Path) -> list[str]:
    """Every directory a path in this repository can be quoted under.

    The checkout `root` plus every worktree `git worktree list` knows, each
    in its lexical AND its resolved spelling, longest first so a worktree
    nested inside the main checkout (`.claude/worktrees/<name>`) claims its
    own paths. A git that cannot list worktrees leaves just `root`: fewer
    rewrites, and the home refusal still stands behind them, so the failure
    direction is a refusal and never a leak.
    """
    listed = {str(root)}
    try:
        out = subprocess.run(["git", "worktree", "list", "--porcelain"],
                             cwd=root, capture_output=True, text=True,
                             check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        out = ""
    listed.update(line[len("worktree "):] for line in out.splitlines()
                  if line.startswith("worktree "))
    roots = {spelling for path in listed
             for spelling in (os.path.normpath(path), os.path.realpath(path))}
    roots.discard("/")
    return sorted(roots, key=len, reverse=True)


def _under(path: str, roots: list[str]) -> str | None:
    """`path` relative to the first root containing it, else None.

    Lexical first, so a path through a symlink INSIDE the repo keeps the name
    the reviewer used; resolved second, so a checkout quoted through a link
    to it (or `/var` for `/private/var`) still maps.
    """
    for candidate in (os.path.normpath(path), os.path.realpath(path)):
        for r in roots:
            if candidate == r:
                return "."
            if candidate.startswith(r + "/"):
                return candidate[len(r) + 1:]
    return None


def _render(rel: str, punct: str) -> str:
    """`rel`, then the punctuation that ended the quoted path.

    The bare root renders as `.`, and every dot after it is dropped:
    `the root <root>.` must not become `the root ..`, the parent directory,
    nor `<root>...` the name `...`.
    """
    if rel == ".":
        punct = punct.lstrip(".")
    return rel + punct


def _path_end(tail: str) -> tuple[str, str]:
    """`tail` split into the path and what trails it, the one rule for where
    a path ends. A final segment that is `..` ends the path whatever follows
    it, unless a name character (`_NAME_CHAR`) continues it: `...`,
    `...hidden` and `..foo` are names. Any other path ends before its
    trailing sentence punctuation, never inside the dots that begin its final
    segment. This is the reading a rewrite renders; `_readings` adds the one
    a final run of dots also allows."""
    start = tail.rfind("/") + 1
    if start and (dots := _DOTDOT_END.match(tail, start)):
        return tail[:dots.end()], tail[dots.end():]
    dots = _LEADING_DOTS.match(tail, start).end() if start else 0
    path = tail[:max(dots, len(tail.rstrip(_TRAILING_PUNCT)))]
    return path, tail[len(path):]


def _readings(path: str) -> list[str]:
    """Every path `path`, ended by `_path_end`, can be read as.

    A final segment of three or more dots that no name character continues
    (`...`, `...?x`, but not `...hidden`) is ambiguous: the name, or `..`
    before what trails it. Both are returned, and a path is rewritten only
    when every reading stays inside a worktree, so `<root>/internal/...` is
    rewritten and `<root>/x/../..` ending a sentence is not."""
    start = path.rfind("/") + 1
    if start and _DOTS_END.match(path, start):
        return [path, path[:start + 2]]
    return [path]


def _prefix_hits(text: str, prefixes: list[str], start: str):
    """Each literal occurrence of a prefix that begins a path in `text`.

    Yields `(at, prefix, core, punct)`: where the prefix starts, the prefix,
    the path after it, and the sentence punctuation that ended it. The path
    stops before a `/` that begins another path, and the search resumes past
    it, so no hit consumes the next one and no two overlap. LITERAL, so a
    checkout whose path holds a space or a bracket (`My Projects (copy)`) is
    recognised, which the generic `_ABS_PATH` cannot do.

    A hit must be the WHOLE prefix: `<root>-old` only begins with a root's
    spelling. At one position every prefix that matches is tried, longest
    first, so a worktree `r2` that only begins the quoted name `r24` falls
    back to the shorter checkout root. No `..` is resolved here: a path
    written from a home is a home path however it walks, and which worktree
    claims a path is decided on the normalised path (`_relativize`).
    """
    pattern = re.compile(start + "(?:" + "|".join(map(re.escape, prefixes))
                         + ")")
    pos = 0
    while (match := pattern.search(text, pos)) is not None:
        at = match.start()
        pos = at + 1
        for prefix in prefixes:
            if not text.startswith(prefix, at):
                continue
            tail = _PATH_TAIL.match(text, at + len(prefix)).group(0)
            core, punct = _path_end(tail)
            if core and not core.startswith("/"):
                continue
            yield at, prefix, core, punct
            pos = at + len(prefix) + len(tail)
            break


def _holding(path: str, roots: list[str]) -> str | None:
    """The first of `roots` (longest first: the deepest) that is `path` or
    contains it, else None. `path` is already normalised."""
    return next((r for r in roots
                 if path == r or path.startswith(r + "/")), None)


def _read_as_one(text: str, start: int) -> list[str]:
    """The readings (`_readings`) of the path from `start` to the end of its
    token (`_TOKEN_TAIL`), ended by `_path_end` as the cut reading is."""
    return _readings(_path_end(_TOKEN_TAIL.match(text, start).group(0))[0])


def _relativize(text: str, roots: list[str]) -> str:
    """`text` with every absolute path under a root made repo-relative.

    Two passes. Known roots first, matched as literal strings in both their
    written and resolved spellings. Then the generic pattern, resolved with
    realpath, for a root quoted through a spelling nobody listed (a symlink
    to the checkout, `/var` for `/private/var`).
    """
    if not roots or "/" not in text:
        return text
    out, pos = [], 0
    for at, written, core, punct in _prefix_hits(text, roots, _PATH_START):
        whole = os.path.normpath(written + core)
        # The deepest worktree holding the NORMALISED path claims it, so
        # `<r2>/../r24/x.py` is `x.py` when r24 is a worktree too.
        root = _holding(whole, roots)
        readings = _readings(core) + _read_as_one(text, at + len(written))
        if root is None or any(_holding(os.path.normpath(written + r), roots)
                               is None for r in readings):
            continue  # a `..` walked back out of every root
        out += [text[pos:at], _render(whole[len(root) + 1:] or ".", punct)]
        pos = at + len(written) + len(core) + len(punct)
    text = "".join(out) + text[pos:]

    def sub(match: re.Match) -> str:
        path, punct = _path_end(match.group(0))
        rel = _under(path, roots) if path else None
        if rel is not None and any(
                _under(r, roots) is None for r in _readings(path)
                + _read_as_one(match.string, match.start())):
            rel = None
        return match.group(0) if rel is None else _render(rel, punct)
    return _ABS_PATH.sub(sub, text)


def _home_roots() -> list[str]:
    """`$HOME`, when the `/Users|home/<name>` pattern does not already judge it.

    A pattern home (`/Users/<name>`, `/home/runner`) is left to `HOME_RE` and
    its placeholders, so the two definitions agree there exactly; `$HOME` adds
    only a home the pattern cannot see, such as a service account's `/root`.
    """
    home = os.environ.get("HOME", "")
    if not os.path.isabs(home):
        return []
    spellings = {os.path.normpath(home), os.path.realpath(home)}
    return sorted(h for h in spellings if h != "/" and not HOME_RE.match(h))


def _home_path_in(text: str, homes: list[str]) -> tuple[int, str] | None:
    """Where the first home path `text` quotes starts, and that path, or
    None."""
    for match in HOME_RE.finditer(text):
        if match.group(1) not in HOME_PLACEHOLDERS:
            return match.start(), match.group(0)
    if homes:
        for at, home, core, _punct in _prefix_hits(text, homes, _HOME_START):
            return at, home + core
    return None


def _written_behind(text: str, at: int, roots: list[str]) -> str | None:
    """The text before a worktree path holding position `at` that kept it
    from being rewritten, or None when no such path holds `at`.

    A path under a root is left as written when it does not begin a path: it
    follows a URL scheme, a `host:` or drive-letter prefix, a colon joining
    it to the text before, or a word it continues. The text returned runs
    back from the root to whitespace, a quote, a bracket or a separator, so
    a refusal can name it. A root that begins a path and was still not
    rewritten (`<root>/../x`, `<root>-old`) is not under the root at all,
    and is None.
    """
    begins = re.compile(_PATH_START)
    for root in roots:
        for match in re.finditer(re.escape(root), text):
            if not match.start() <= at < match.end():
                continue
            core = _path_end(_PATH_TAIL.match(text, match.end()).group(0))[0]
            if (core and not core.startswith("/")) or any(
                    _holding(os.path.normpath(root + r), roots) is None
                    for r in _readings(core)):
                continue
            if begins.match(text, match.start()):
                continue
            begin = match.start()
            while begin and text[begin - 1] not in _FOLLOWS_STOP:
                begin -= 1
            return text[begin:match.start()]
    return None


def relativize_strings(doc: dict, root: Path | None) -> dict:
    """`doc` with no string quoting a home directory, or AttestError.

    WALKS EVERY STRING, not a list of fields. `memory ingest` reads nothing
    but the artifact, and the artifact is this document serialized, so every
    string a shard can carry is a string here or is derived from one (a
    record's `dir_prefix` from its `file`). A hand list of fields is how the
    roster `agent`, a finding's `tags` and an unchecked roster `output`
    reached a shard unexamined. Keys are not walked: the attestation schema
    closes every object (`additionalProperties: false`), so each key is its
    own vocabulary, never reviewer text.

    A path under any worktree of the repository at `root` is rewritten
    repo-relative where it begins a path. One that does not (behind a URL
    scheme, a `host:` or drive-letter prefix, or joined by a colon to the
    text before it) is left as written, so under a checkout in a home
    directory it is refused, naming the text it follows. Any other home path (`HOME_RE` outside its placeholders,
    or a path under a `$HOME` the pattern cannot see) is REFUSED, naming
    where it sits and, inside a finding, that finding's rule_id. It is never
    stripped, because what the reviewer wrote is what is wrong, and a silent
    edit would change it.

    Unchanged values are returned as the SAME objects, so a document with no
    absolute paths reaches the artifact byte for byte, and the caller's
    payload is never mutated. `root=None` rewrites nothing and still refuses,
    so a caller that cannot name its checkout fails closed.
    """
    roots = repo_roots(root) if root is not None else []
    homes = _home_roots()

    def walk(value: object, where: str, rule_id: str) -> object:
        if isinstance(value, str):
            if "/" not in value:
                return value
            rewritten = _relativize(value, roots)
            leak = _home_path_in(rewritten, homes)
            if leak:
                at, leak = leak
                named = f"{where} (rule_id {rule_id!r})" if rule_id else where
                follows = _written_behind(rewritten, at, roots)
                if follows is not None:
                    raise AttestError(
                        f"{named} quotes {leak!r} inside a worktree path that "
                        f"follows {follows!r}, so it does not begin a path "
                        "and is left as written: only a path at its own "
                        "boundary is rewritten repo-relative, never one "
                        "behind a URL scheme, a `host:` or drive-letter "
                        "prefix, or a colon joining it to the text before. "
                        "Left as written it quotes a home directory, and the "
                        "attestation becomes a committed, append-only shard, "
                        "so write the path repo-relative and attest again")
                raise AttestError(
                    f"{named} quotes {leak!r}: a home directory outside every "
                    "worktree of this repository. The attestation becomes a "
                    "committed, append-only shard, so write the path "
                    "repo-relative (or as a placeholder home) and attest again")
            return value if rewritten == value else rewritten
        if isinstance(value, list):
            new = [walk(v, f"{where}[{i}]", rule_id)
                   for i, v in enumerate(value)]
            return value if all(a is b for a, b in zip(new, value)) else new
        if isinstance(value, dict):
            if isinstance(value.get("rule_id"), str):
                rule_id = value["rule_id"]
            new = {k: walk(v, f"{where}.{k}" if where else k, rule_id)
                   for k, v in value.items()}
            return value if all(new[k] is value[k] for k in value) else new
        return value

    return walk(doc, "", "")


def _slugify(rule_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", rule_id.lower()).strip("-") or "other"


class _RuleIndex:
    """The declared ruleset, indexed for the questions asked of it.

    Built from one `load_rules` read so that "shadowed" has exactly ONE
    definition. Certify asks "is the id that just raised shadowed?", not "is
    anything in the corpus shadowed?"; a second definition would let it answer
    a typo's failure with an unrelated class's remedy.
    `tests_shadowed_matches_validator` pins the two readings equal.
    """

    def __init__(self, rules_dir: Path):
        declared = load_rules(rules_dir)
        self.valid = {r.id for r in declared}
        # A rule may DECLARE the candidate class it answers when its id
        # differs from the slug (`wiki-fidelity` covers `docs-drift`).
        # Split the way memory.covered_classes splits: `answered` is the
        # ENFORCEMENT reading —
        # a paused rule produces no findings, so it answers nothing, and the
        # slug is a live candidate key again; `declared_covers` is the
        # IDENTITY reading — the paused rule still declares the class and
        # reclaims it on unpause.
        self.answered = {slug: r.id for r in declared if not r.paused
                         for slug in r.covers}
        self.declared_covers = {slug: r.id for r in declared
                                for slug in r.covers}

    def shadowing(self, rule_id: object, *,
                  include_paused: bool = False) -> str | None:
        """The declared rule answering `rule_id`'s class, or None.

        Both conditions that stop an `unmapped:` id resolving once a rule
        ships: the rule's id IS the slug (paused or not — a pause suspends
        enforcement, never identity, so the id stays taken), or the rule
        `covers:` it. The covers link follows the caller's question: the
        default asks what is ENFORCED — a paused rule's
        class is re-proposed by `memory stats` and must be able to accrue
        evidence under its own slug, so it does not shadow. A caller asking
        what is DECLARED (`include_paused=True`, the tags alias guard) sees
        paused rules too, because folding a class the rule reclaims on
        unpause would flip recall reads with pause state.
        """
        if not isinstance(rule_id, str) or not rule_id.startswith(UNMAPPED_PREFIX):
            return None
        slug = rule_id[len(UNMAPPED_PREFIX):]
        if slug in self.valid:
            return slug
        covers = self.declared_covers if include_paused else self.answered
        return covers.get(slug)


def shadowed_rule_ids(findings: list[dict], rules_dir: Path) -> dict[str, str]:
    """The `unmapped:` ids a declared rule now answers, mapped to that rule.

    The same `_RuleIndex.shadowing` predicate `_check_rule_ids` refuses on —
    one definition, two callers, the construction `VERDICTS` and
    `_check_rule_ids` itself already use here.

    This DESCRIBES a failure; it never decides one. Writing a rule for a
    recurring candidate class is the retro loop's normal path and it
    retroactively invalidates every shard that ever filed under that slug, so
    `certify` calls this to say which rule did it
    and how much evidence it touched, rather than reporting the first shard
    it tripped over.
    """
    index = _RuleIndex(rules_dir)
    shadowed = {}
    for f in findings:
        by = index.shadowing(f.get("rule_id"))
        if by is not None:
            shadowed[f["rule_id"]] = by
    return shadowed


def _check_rule_ids(findings: list[dict], rules_dir: Path) -> None:
    """Resolve every finding's rule_id against the declared ruleset.

    Paused rules still resolve: a pause keeps the rule's id and its history
    (rules.Rule.paused), so attesting against one is not an error.

    A paused rule's `covers:` link is the other way around: the pause
    re-opens the class as a candidate in `memory stats`, so
    `unmapped:<covered-slug>` is ACCEPTED while the
    covering rule is paused — otherwise the re-proposed candidate's fresh
    findings route onto a rule review.py skips and stats quarantines, and
    the row's tally stays frozen at pre-pause history. Unpausing restores
    the refusal, at a declared cost: slug records INGESTED during the pause
    are committed evidence, so certify's E-02 will direct them to the
    grandfather declaration once the rule re-answers the class (an
    attestation still sitting in .warden/out at unpause migrates onto the
    rule instead — the record's key follows the pause state at ingest, not
    at write). `memory ingest` reads the same seam, so the two ends of the
    pipe agree (its `_resolve_covered_legacy_ids` rescues only the
    spellings this function refuses).
    """
    if not findings:
        return
    # Both shadow conditions live in `_RuleIndex.shadowing`, which certify
    # reads too. Checking only id == slug would let a class a rule `covers:`
    # keep accepting `unmapped:` findings forever, so its judgments would
    # never reach the rule's precision.
    index = _RuleIndex(rules_dir)
    valid = index.valid
    listed = ", ".join(sorted(valid))
    for f in findings:
        rid = f["rule_id"]
        if rid in valid:
            continue
        if rid.startswith(UNMAPPED_PREFIX):
            slug = rid[len(UNMAPPED_PREFIX):]
            by = index.shadowing(rid)
            if by == slug:
                raise AttestError(
                    f"{f['file']}: rule_id {rid!r} hides declared rule "
                    f"{slug!r} behind the {UNMAPPED_PREFIX} prefix — file it "
                    f"as {slug!r} so its judgments count toward that rule's "
                    "precision", rule_id=rid)
            if by is not None:
                raise AttestError(
                    f"{f['file']}: rule_id {rid!r} names a candidate class "
                    f"the declared rule {by!r} already covers — "
                    f"file it as {by!r} so its judgments count "
                    "toward that rule's precision", rule_id=rid)
            if not _SLUG.match(slug):
                raise AttestError(
                    f"{f['file']}: rule_id {rid!r} is not a well-formed "
                    f"'{UNMAPPED_PREFIX}<slug>' — the slug must be lowercase "
                    "words joined by '-' (e.g. 'unmapped:docs-drift') so the "
                    "id stays a comparable corpus key", rule_id=rid)
            if slug in _NON_DESCRIPTIVE_SLUGS:
                raise AttestError(
                    f"{f['file']}: rule_id {rid!r} names no defect class — "
                    f"{slug!r} says only 'something'. Name the class the way "
                    ".warden/memory/tags.yaml names one (e.g. "
                    "'unmapped:docs-drift', 'unmapped:fail-open'); an "
                    "unnameable finding is the bare escape id in a prefix",
                    rule_id=rid)
            continue
        # The remedy must be an id this same function accepts: a near-miss
        # of a real id is a typo, and slugifying 'general'
        # or '???' would propose a reserved non-descriptive slug.
        slug = _slugify(rid)
        if slug in valid:
            remedy = f"did you mean the declared rule {slug!r}?"
        elif slug in _NON_DESCRIPTIVE_SLUGS:
            remedy = (f"a finding no rule covers is filed as "
                      f"'{UNMAPPED_PREFIX}<defect-class>' — name the class "
                      "the way .warden/memory/tags.yaml names one (e.g. "
                      "'unmapped:docs-drift'); that prefix is a first-class "
                      "signal the rule advisor mines, not a workaround")
        else:
            remedy = (f"a finding no rule covers is filed as "
                      f"'{UNMAPPED_PREFIX}{slug}' — that prefix is a "
                      "first-class signal the rule advisor mines for "
                      "candidate rules, not a workaround")
        raise AttestError(
            f"{f['file']}: rule_id {rid!r} names no declared rule in "
            f"{rules_dir}. Valid rule ids: {listed}. Remedy: {remedy}",
            rule_id=rid)


def _check_tags(findings: list[dict], root: Path | None,
                rules_dir: Path) -> None:
    """Refuse, before anything is written, a tag the vocabulary rejects.

    The same reading the repo's guard test applies at pre-push
    (tests/test_tag_vocabulary_guards.py), taken from the same functions in
    `tags`, over the committed corpus PLUS this payload: a tag with no
    recorded disposition (declared, folded, or a `left_undeclared:` receipt)
    that would take the undecided count past a DECLARED ceiling, and a rule id
    used as a tag the vocabulary does not declare. Refused here, the builder
    writes a receipt or re-tags and re-runs; refused at pre-push, the shard
    was already written and had to be deleted and re-attested.

    Scoped to a repo that DECLARES a ceiling. With none declared an undecided
    tag breaches nothing (`memory ingest` warns and that is the whole
    contract), so a consumer with no vocabulary is untouched. A declaration
    that cannot be read refuses any payload carrying a tag: a declaration
    nobody can read is a complaint, never headroom — the rule
    `memory check-vocabulary` applies, and it is checked first so the
    refusal names that cause rather than a symptom of it.
    """
    if root is None:
        return
    new = [{"tags": list(f.get("tags") or [])} for f in findings]
    if not any(r["tags"] for r in new):
        return
    from . import tags as tags_mod
    ceiling, ceiling_problems = tags_mod.load_ceiling(root)
    if ceiling is None and not ceiling_problems:
        return
    tags_mod.canonicalize_records(root, new)
    where = f".warden/memory/{tags_mod.VOCAB_FILE}"
    # The declaration's own complaints FIRST: an unreadable `tags:` block
    # loads as {}, and every later reading would then name a symptom (a
    # "misused" rule id, "Declared tags: (none)") instead of the cause.
    unreadable = tags_mod.ceiling_status(root, [])["labelled"]
    if unreadable:
        raise AttestError(
            f"this payload carries tags, and {where} cannot be read to show "
            f"they stay within its ceiling: " + "; ".join(unreadable))
    declared = sorted(tags_mod.load_vocab(root))
    listed = ", ".join(declared) or "(none)"
    # Rule IDS only: a class a rule `covers:` is the right tag for a finding.
    misused = tags_mod.rule_ids_used_as_tags(
        new, tags_mod.rule_namespace(rules_dir, covers=False), set(declared))
    if misused:
        raise AttestError(
            f"tag(s) {', '.join(misused)} are rule ids, not declared tags — "
            f"file the finding under the class the rule `covers:` (declaring "
            f"the id would split that class's recall key). Declared tags in "
            f"{where}: {listed}")
    fresh = tags_mod.undecided_tags(root, new)
    if not fresh:
        return
    from .memory import records_from_shards
    try:
        corpus = records_from_shards(root)
    except ValueError as e:
        raise AttestError(
            f"tag(s) {', '.join(fresh)} carry no recorded disposition, and "
            f"the committed corpus cannot be read to show they stay within "
            f"the declared ceiling: {e}") from e
    status = tags_mod.ceiling_status(root, corpus + new)
    if status["breached"]:
        raise AttestError(
            f"tag(s) {', '.join(fresh)} are not in the declared vocabulary "
            f"and would take the undecided count to "
            f"{len(status['undecided'])} against the declared ceiling of "
            f"{status['ceiling']}. Re-tag with a declared tag, fold it with "
            f"an `aliases:` entry, or record one line under "
            f"'{tags_mod.LEFT_KEY}:' in {where}, then re-run. Declared tags: "
            f"{listed}")


def _check_receipts(doc: dict, root: Path | None, rules_dir: Path) -> None:
    """Refuse a payload whose records would outgrow a `left_undeclared:`
    receipt, before the shard exists.

    `_check_tags` asks whether each tag HAS a disposition. This asks whether
    the disposition still holds once this round's records are counted: the
    guard suite (tests/test_tag_vocabulary_guards.py) holds every receipt to
    its premise over the committed corpus, and a builder may not edit the
    receipt, so a shard that breaks one is found only after it was written,
    ingested and committed, and the branch is rebuilt without it.

    Counted the way the suite will count it: the records `memory ingest`
    builds from this document, canonicalized and folded with the corpus, so a
    restatement of a committed finding is no new evidence. The fold keeps the
    later round's copy WHATEVER its tags, so a payload can change a name's
    counts without carrying it — re-filing a committed refutation as `fixed`
    under another tag drops one refuted record from that name. So every name
    is read, not only the ones the payload carries; the payload is the only
    thing that differs between the two readings, so whatever newly breaks is
    its doing. Refused is only what THIS payload breaks — a premise the
    committed shards already broke is the suite's to report, and refusing on
    it would block every later round that carries the name. Same scope as
    `_check_tags`: a repo that declares a ceiling. A corpus that cannot be
    read refuses any payload with findings there, since no tag — declared or
    none — rules out a restatement.
    """
    if root is None or not doc.get("findings"):
        return
    from . import tags as tags_mod
    ceiling, ceiling_problems = tags_mod.load_ceiling(root)
    if ceiling is None and not ceiling_problems:
        return
    from .memory import build_records, fold_restatements, records_from_shards
    where = f".warden/memory/{tags_mod.VOCAB_FILE}"
    try:
        corpus = records_from_shards(root)
    except ValueError as e:
        raise AttestError(
            f"{where} declares a ceiling, and the committed corpus cannot be "
            f"read to show this payload leaves its '{tags_mod.LEFT_KEY}:' "
            f"receipts standing: {e}") from e
    new = tags_mod.canonicalize_records(root, build_records(doc))
    from .rules import load_rules
    rules = load_rules(rules_dir)
    before = tags_mod.outgrown_receipts(root, corpus, rules)
    after = tags_mod.outgrown_receipts(
        root, fold_restatements(corpus + new), rules)
    broken = {name: why for name, why in after.items() if name not in before}
    if not broken:
        return
    detail = "; ".join(f"{name}: {', '.join(why)}"
                       for name, why in sorted(broken.items()))
    declared = ", ".join(sorted(tags_mod.load_vocab(root))) or "(none)"
    raise AttestError(
        f"tag(s) {', '.join(sorted(broken))} would outgrow the "
        f"'{tags_mod.LEFT_KEY}:' receipt in {where} once this shard is "
        f"counted, and the vocabulary guard suite refuses that corpus — "
        f"{detail}. A finding this payload files under the name, or a "
        f"committed one it re-files with a new status, moved the count: "
        f"re-tag with a declared tag (Declared tags: {declared}), or have the "
        f"receipt re-argued, the name declared or folded in {where}, then "
        f"re-run")

def range_binding(findings: list[dict], changed_paths: tuple[str, ...],
                  base_sha: str, head_sha: str) -> tuple[str, str]:
    """Warn when a findings set names no file the attested range changed.

    A findings write that fails silently leaves an EARLIER task's findings
    file at the same scratchpad path. Schema, rule_ids, clean tree and
    verdict all validate, so the result is a well-formed CLEAN attestation
    about unrelated code, committed as a shard and ingested as corpus —
    quietly distorting the per-rule precision, Wilson bounds and promotion
    bar that decide which guardrails ship. The scratchpad path is stable,
    which is what makes a leftover predecessor likely rather than exotic.

    It WARNS and does not refuse, because zero overlap is a real signal but
    not a conclusive one, and this repo declares two rules that produce
    out-of-range findings by construction: `rename-complete` ("a file the
    diff did not touch that still carries the old name is a filable
    finding") and `wiki-fidelity` (a page a code diff forgot to update). A
    rename is the same shape — `git diff --name-only` prints only the
    destination path. Refusing would block those honest rounds, and the only
    way through would be deleting a true finding: corpus damage in the
    opposite direction from the poisoning this guards.

    Returns (kind, message): kind is "ok", "out-of-range" or "empty-range",
    and message is "" when there is nothing to say. The kind is returned
    rather than re-derived by the caller so the run manifest records the same
    reading the terminal printed; a second classification could drift.

    THE RUN MANIFEST IS WHERE THIS SIGNAL ENDS, by a recorded ruling.
    Nothing carries the reading into a committed shard: the
    attestation schema declares no field for it and `memory ingest` builds its
    envelope from a fixed field list that does not include one, so `memory
    stats` and the retro CANNOT report how often the warning fires, and no doc
    may say they can. Corpus visibility would cost a HIGH-tier additive change
    to attestation.schema.json plus an ingest change, for a count nothing reads
    while the check is advisory. Shipping half of it would leave a declared
    field with zero consumers.
    """
    if not findings:
        # A findings-free attestation names no files, so it has zero overlap
        # by construction. "A clean review is still a review" — warning here
        # would train the reader to ignore the warning.
        return "ok", ""
    named = {f["file"] for f in findings if f.get("file")}
    if not named or named & set(changed_paths):
        return "ok", ""

    def sample(paths: set[str] | tuple[str, ...]) -> str:
        shown = sorted(paths)[:3]
        return ", ".join(shown) + (f" (+{len(paths) - 3} more)"
                                   if len(paths) > 3 else "")

    if not changed_paths:
        # A different cause with a different remedy, so it gets its own
        # message: nothing was reviewed at all. Reporting this as a stale
        # findings file would send the reader hunting through a file that is
        # fine (error-names-cause).
        return "empty-range", (
                f"WARNING: range {base_sha[:12]}...{head_sha[:12]} changed no "
                f"files, so there is nothing for these {len(findings)} "
                "finding(s) to be about. Attesting on the base branch itself, "
                "or a --base that already contains HEAD, produces an empty "
                "range — check --base names the branch point you reviewed.")
    return "out-of-range", (
            f"WARNING: none of the {len(named)} file(s) these findings name "
            f"was changed by {base_sha[:12]}...{head_sha[:12]}. Findings name: "
            f"{sample(named)}. The range changed: {sample(changed_paths)}. "
            "That is expected of any finding about a file the diff did not "
            "touch — rename-complete and wiki-fidelity produce them by design, "
            "and the one instance in this repo's own corpus is neither — but "
            "it is also what a "
            "STALE findings file looks like, left at the same path by a write "
            "that failed silently. Confirm these findings were written for "
            "THIS range before committing the shard.")


# --------------------------------------------------------------------------
# Roster verification: is the declared review crew observable?
# --------------------------------------------------------------------------

ROSTER_VERIFIED = "verified"
ROSTER_UNVERIFIED = "unverified-roster"
# READ from the schema, never retyped — the same argument VERDICTS carries a
# few lines up, and for the same seam: `memory ingest` never calls `build`, so
# it needs this list to decide whether an artifact's roster state is one the
# contract declares. A hand-copied second tuple drifts, and it drifts in the
# dangerous direction: an enum ADDITION is the schema edit the contract-freeze
# gate calls safe, and it would silently stop riding into shards.
ROSTER_STATES = tuple(
    _validator.schema["properties"]["roster_verification"]["enum"])
# The round-binding states, read from the schema for the same reason and at the
# same seam. `memory ingest` never calls `build`, so it
# needs the declared list to decide whether an artifact's round state is one the
# contract knows; a hand-copied tuple would drift the moment the enum grew.
ROUND_STATES = tuple(
    _validator.schema["properties"]["round_binding"]["enum"])
# The closure-round state, read from the schema at the same seam and for the
# same reason — `memory ingest` never calls `build`, so it needs the declared
# list to decide whether an artifact's closure state is one the contract
# knows. One value today, and the tuple is still read rather than retyped:
# the enum is the edit the contract-freeze gate calls safe, and a hand-copied
# constant would silently stop riding into shards the moment it grew.
CLOSURE_STATES = tuple(
    _validator.schema["properties"]["closure_verification"]["enum"])
# The base-ancestry readings, read from the schema at the same seam and for the
# same reason as the three tuples above. `memory ingest` never calls `build`,
# so it needs the declared list to decide whether an artifact's ancestry
# reading is one the contract knows.
ANCESTRY_STATES = tuple(
    _validator.schema["properties"]["base_ancestry"]["enum"])
ANCESTRY_UNAVAILABLE = "unavailable"
assert ANCESTRY_UNAVAILABLE in ANCESTRY_STATES
CLOSURE_VERIFIED = "verified"
assert CLOSURE_VERIFIED in CLOSURE_STATES
# The lens vocabulary, read from the schema's `$defs.lens` for the reason the
# three tuples above are: one definition, in the contract consumers pin. Two
# shapes, and `lens_is_valid` is the ONLY judge of both — the enum of platform
# roles is exposed for messages and tests, the `crew:<slug>` pattern is not
# retyped here at all. A closed vocabulary keeps a per-lens statistic to one
# key per lens; free-text roles would split one lens across many spellings.
_LENS_SCHEMA = _validator.schema["$defs"]["lens"]
LENS_ROLES = tuple(_LENS_SCHEMA["anyOf"][0]["enum"])
# The dispatch-outcome vocabulary, read from the schema at the
# same seam and for the same reason: `memory stats` buckets rosters by it
# without ever calling `build`, and a retyped tuple drifts the moment the
# enum grows.
OUTCOMES = tuple(_validator.schema["properties"]["reviewers"]["items"]
                 ["properties"]["outcome"]["enum"])
OUTCOME_CLEAN, OUTCOME_FINDINGS, OUTCOME_NONE = (
    "reviewed-clean", "reviewed-findings", "no-review")
assert set(OUTCOMES) == {OUTCOME_CLEAN, OUTCOME_FINDINGS, OUTCOME_NONE}
# The `crew:<slug>` shape, read from the schema and matched with `fullmatch`.
# NOT `_validator`/`re.search`: the schema's pattern ends in `$`, which is end
# of input under ECMA-262 (what the contract means, and what a conformant
# consumer validator applies) but under Python `re` also matches before a
# trailing newline — so jsonschema accepts 'crew:fail-closed\n' as a member,
# a second corpus key for one lens, the exact defect the vocabulary exists to
# end. `fullmatch` cannot end before the newline, so
# warden's reading is the strict one, and `build` applies it to every lens
# after the schema pass rather than trusting the schema's.
_LENS_CREW = re.compile(_LENS_SCHEMA["anyOf"][1]["pattern"])


def lens_is_valid(value: object) -> bool:
    """Whether `value` is a vocabulary member: a platform role or `crew:<slug>`."""
    return isinstance(value, str) and (
        value in LENS_ROLES or _LENS_CREW.fullmatch(value) is not None)


# Named in every refusal a pre-0.9.0 skill pack meets under --review-dir.
# Lens and round are required under a flag consumers already pass, so a
# consumer that bumps its warden pin while its plugin cache still holds an
# older pack is refused on the first write. That case is accepted by
# decision, and the message names it so the operator updates the pack
# instead of hand-editing a roster the pack will write correctly once updated.
_STALE_PACK = ("If this payload came from a skill pack (nightgate-skills) older "
               "than 0.9.0, update the pack (`claude plugin marketplace update`) — "
               "these fields are required under --review-dir, and a pack that "
               "predates them writes none")


def _lens_vocabulary_hint() -> str:
    return (f"one of {', '.join(LENS_ROLES)}, or crew:<slug> for a lens the "
            "repo's review charter declares (lowercase words joined by '-', "
            "e.g. crew:fail-closed)")


def verify_roster(reviewers: list, review_dir: Path | None) -> tuple[str, str]:
    """Cross-check a declared roster against the round's own artifacts.

    WHAT THIS EXISTS FOR. Without it `build` would take `reviewers` verbatim,
    so a shard could assert a reviewer that never ran and nothing in the
    platform could see it: permanent, append-only false provenance in a
    corpus whose entire value is that it is true.

    WHAT "verified" CLAIMS, EXACTLY, AND WHAT IT DOES NOT. It claims every
    reviewer asserting `returned: true` named a distinct, non-empty file that
    existed in the round directory at attest time. That refuses a fabricated
    reviewer: it produced no artifact, because no subagent ran to produce
    one. It does NOT claim a reviewer really ran, and
    no doc, message or schema description may say it does — a caller that
    writes plausible files itself passes this check. The guarantee is "the
    claim was backed by an artifact", one rung above "the claim was typed";
    the rung above THAT is signing, and this platform has nowhere to keep a
    key ($0 standing infrastructure).

    BOTH DIRECTIONS. Walking only the declared roster catches a reviewer
    NAMED who never ran; listing the directory catches a reviewer who RAN and
    was quietly left out. Both are enforced, which is why the directory
    passed in must hold only reviewer reports.

    FAIL-CLOSED WITHIN THE EVIDENCE OFFERED. Once a caller offers a review
    dir, every way the roster cannot be evaluated is a refusal, not a pass: a
    review dir that is empty-as-a-path, missing or unlistable; a roster entry
    that is not an object; a missing `returned`, a non-boolean one, a missing
    `findings` count, `returned: true` with no `output`; an `output` that is
    not a plain basename, is a symlink, is absent, is not a regular file, or
    is empty; two reviewers resolving to one file; a roster where nobody
    returned; and a report in the directory that no entry claims. A check
    that cannot evaluate behaves like a hit.

    THE ABSENT-DIR CASE IS A MARKER, NOT A REFUSAL, and that is a decided
    trade rather than an oversight: requiring `--review-dir` unconditionally
    would break every enrolled
    consumer's existing invocation at once, on a HIGH-tier contract, in a
    platform whose portability is a standing CI check. So the write proceeds
    and says what it is — `unverified-roster` rides into the committed shard,
    so the corpus records permanently and per shard which rounds were checked
    and which were only asserted.

    Returns (state, detail). Raises AttestError when a claim is disproven.
    """
    if review_dir is None:
        return ROSTER_UNVERIFIED, (
            "no --review-dir given, so nothing cross-checked this roster: it "
            "is exactly as reliable as whatever wrote it. Pass the round "
            "directory holding each reviewer's raw output to have the claim "
            "checked")
    # An EMPTY --review-dir is a refusal, never the unverified downgrade.
    # `Path("")` is `PosixPath(".")`, which is a real
    # directory, so without this an unset shell variable expanded into the
    # flag would silently verify the roster against the CURRENT WORKING
    # DIRECTORY — a caller that asked for the check, did not get it, and read
    # exit 0 as though it had. The distinction the CLI must preserve is
    # "did not ask" versus "asked with an unusable value"; only the first is
    # the marker.
    if not str(review_dir).strip() or str(review_dir).strip() == ".":
        raise AttestError(
            "--review-dir was given but names no directory (empty, or the "
            "bare current directory). That is an unset variable expanded "
            "into the flag, not a round directory — refused rather than "
            "downgraded to unverified, because a caller that asked for the "
            "check and silently did not get it is the fail-open shape")
    if not review_dir.is_dir():
        raise AttestError(
            f"--review-dir {review_dir} is not a directory, so the roster "
            "cannot be checked against anything. A refusal rather than a "
            "quiet fall back to unverified: a caller that asked for the check "
            "and silently did not get it is the fail-open shape")
    # Keyed on the FILE ITSELF — (st_dev, st_ino) — never the declared name
    # and never the path. Two names can be one file, and there are two ways to
    # arrange that: `ln -s` (refused outright below) and `ln` (a hard link,
    # which no path comparison can see, because both names are equally real).
    # A name-keyed check would let `ln -s code-reviewer.json second.json`
    # mint a second reviewer out of one genuine output while the summary
    # called them distinct; a path-keyed one closes the symlink spelling and
    # leaves the hard-link spelling open. The identity that actually answers
    # "is this the same artifact" is the inode.
    claimed_outputs: dict[tuple[int, int], str] = {}
    for index, reviewer in enumerate(reviewers):
        # Type-checked before anything is read off it. A string or list here
        # would otherwise reach `.get` and raise AttributeError: a traceback
        # naming `.get` instead of a message naming the cause, bypassing
        # AttestError's handling in main(). The schema would reject the shape
        # too, but schema validation runs LATER in build() than this does, so
        # a malformed payload meets the roster check first.
        if not isinstance(reviewer, dict):
            raise AttestError(
                f"roster entry #{index} is {type(reviewer).__name__}, not an "
                "object — a reviewer is declared as "
                '{"role": ..., "agent": ..., "returned": ...}, and a bare '
                "string names nobody this check can look for")
        who = reviewer.get("role") or f"reviewer #{index}"
        if "returned" not in reviewer:
            raise AttestError(
                f"{who}: roster entry has no `returned` field, so there is no "
                "claim to check. Under --review-dir every reviewer states "
                "whether it came back — one that did not is declared "
                '`"returned": false`, never dropped from the roster')
        if not isinstance(reviewer["returned"], bool):
            raise AttestError(
                f"{who}: `returned` must be true or false, not "
                f"{reviewer['returned']!r}")
        if not reviewer["returned"]:
            # An honest disclosure, and the reason `returned` is a declared
            # field rather than a filter: a dispatched reviewer that fell over
            # is part of what the round WAS, and dropping it leaves a roster
            # that reads complete.
            if reviewer.get("findings"):
                raise AttestError(
                    f"{who}: declared `returned: false` but also "
                    f"{reviewer['findings']} finding(s) — a reviewer that did "
                    "not come back returned nothing to count")
            if reviewer.get("output"):
                raise AttestError(
                    f"{who}: declared `returned: false` but names an output "
                    f"file ({reviewer['output']!r}) — one of the two is wrong, "
                    "and guessing which would be inventing the answer")
            continue
        if "findings" not in reviewer:
            raise AttestError(
                f"{who}: returned, but the roster records no `findings` "
                "count. Under --review-dir the count is part of the claim; a "
                "clean reviewer records 0, which is a different statement "
                "from not saying")
        output = reviewer.get("output") or ""
        if not output:
            raise AttestError(
                f"{who}: claims `returned: true` but names no `output` file, "
                "so the claim rests on nothing observable. Name the reviewer's "
                "report file in `output`, or declare `returned: false`")
        if (output != Path(output).name or output in (".", "..")
                or "\\" in output):
            raise AttestError(
                f"{who}: `output` must be a plain basename inside the review "
                f"dir, got {output!r}. A roster that can name a path can point "
                "the check at another round's file, or outside the repo")
        path = review_dir / output
        # Symlinks are refused OUTRIGHT, before anything follows one. The
        # basename rule above only stops a roster naming a PATH; a symlink is
        # how a plain basename still resolves to another round's file or to
        # anything on disk. It is also the aliasing route: N links to one
        # real output would otherwise verify as N reviewers.
        if path.is_symlink():
            raise AttestError(
                f"{who}: output {output!r} is a symlink. A reviewer's output "
                "is the artifact the round produced, not a pointer to one — a "
                "link makes a plain basename resolve to another round's file, "
                "and lets one real output stand in for several reviewers")
        if not path.exists():
            raise AttestError(
                f"{who}: claims `returned: true` with output {output!r}, but "
                f"no such file is in {review_dir}. Either the reviewer did not "
                "run or its output was never written — both mean this roster "
                "asserts a review that left no trace")
        if not path.is_file():
            # Named apart from absence deliberately: reporting a directory as
            # a missing file sends the reader hunting for something that is
            # sitting right there (error-names-cause).
            raise AttestError(
                f"{who}: output {output!r} exists in {review_dir} but is not "
                "a regular file, so there is no reviewer report to point at")
        # Containment rests on exactly two rules, and both are tested: the
        # plain-basename requirement and the symlink refusal above. Together
        # they make a `resolved.parent != root` check unreachable, so there is
        # none. Anyone relaxing either rule has to re-derive containment
        # rather than assume a backstop.
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if identity in claimed_outputs:
            raise AttestError(
                f"{who}: output {output!r} is the same file as the one "
                f"{claimed_outputs[identity]} already claimed — one artifact "
                "cannot be evidence that two reviewers ran, under any two "
                "names for it, including two hard links to it")
        claimed_outputs[identity] = who
        if stat.st_size == 0:
            raise AttestError(
                f"{who}: output {output!r} is empty. An empty file is what a "
                "write that failed silently leaves behind, and it is no more "
                "evidence of a review than an absent one")

    if not any(r.get("returned") for r in reviewers):
        raise AttestError(
            "no reviewer in this roster returned, so there was no review to "
            "attest. Declaring the failures is right; attesting on top of "
            "them is not")

    # THE OTHER DIRECTION: walking only the DECLARED roster catches a
    # reviewer named who never ran, and is blind to a reviewer who ran and
    # was quietly left out. Listing the directory catches the second: a
    # silently shorter roster is the same lie in the other direction.
    #
    # Only non-empty regular files count as an unclaimed report. An empty one
    # is a failed write, which says nothing about whether a reviewer was
    # dropped; a symlink or a directory is not a report at all. This is why
    # the round directory passed here must hold ONLY reviewer outputs — put
    # scratch in the round dir itself, never in its `reviewers/`.
    try:
        present = {(s.st_dev, s.st_ino)
                   for p in review_dir.iterdir()
                   if p.is_file() and not p.is_symlink()
                   for s in (p.stat(),) if s.st_size}
        by_identity = {(p.stat().st_dev, p.stat().st_ino): p.name
                       for p in review_dir.iterdir()
                       if p.is_file() and not p.is_symlink()}
    except OSError as e:
        raise AttestError(
            f"--review-dir {review_dir} could not be listed ({e}), so a "
            "dropped reviewer cannot be ruled out. Refused rather than "
            "passed: a check that cannot evaluate behaves like a hit") from e
    # Compared by inode, matching how the roster's claims are keyed: two hard
    # links to one report are one artifact, so claiming either one claims it.
    unclaimed = sorted(by_identity[i] for i in present - set(claimed_outputs))
    if unclaimed:
        raise AttestError(
            f"{len(unclaimed)} report(s) in {review_dir} that no roster entry "
            f"claims: {', '.join(unclaimed)}. Either a reviewer ran and was "
            "left out of the roster — the silently shorter roster, which is "
            "the same lie as naming one that never ran — or the directory "
            "holds files that are not reviewer reports, in which case move "
            "them out: this directory is the evidence, so everything in it "
            "is a claim about the round")

    returned = sum(1 for r in reviewers if r.get("returned"))
    return ROSTER_VERIFIED, (
        f"{returned} of {len(reviewers)} declared reviewer(s) returned, each "
        f"backed by a distinct non-empty output in {review_dir}, and no "
        "unclaimed report left in it")


def derive_outcomes(reviewers: list, review_dir: Path | None,
                    changed: tuple[str, ...] | None) -> list[dict]:
    """What each dispatch came back WITH, read off the artifacts.

    `verify_roster` proves a `returned: true` claim is backed by a distinct,
    non-empty file. It cannot tell a review from a refusal: a file holding
    "I can't help with that", a classifier decline, a truncated run or a
    "nothing to review" written without reading the diff is a distinct
    non-empty file, and the roster read `returned: true, findings: 0` —
    exactly as a clean review does. The review-depth ledger counted both as
    zero-yield, and that number is the denominator every lens-retirement bar
    rests on. This is the check-that-looks-like-a-check shape one layer up.

    THE OBSERVABLE is whether the output names any file the attested range
    changed: a review that read nothing cannot cite what it read, and a
    refusal does not. So, ordered by what needs no reading first:

      returned: false          -> no-review
      findings > 0             -> reviewed-findings
      output names a changed   -> reviewed-clean
        file
      otherwise                -> no-review

    Where `changed` is None the third test cannot run: nothing is derived
    for such an entry (absent is UNKNOWN, never upgraded), and a declared
    reviewed-clean is refused as uncorroborated exactly as it would be on a
    range that names nothing. The CLI always passes the list it also hands
    `range_binding`.

    A DECLARED outcome may lower, never raise: `no-review` stands whatever
    the derivation says — the builder can know a run was truncated where the
    text does not show it — while a declared `reviewed-clean` the output
    cannot corroborate, or any declaration the derivation contradicts, is an
    AttestError. A `no-review` is RECORDED, never refused: the roster stays
    complete, and the protocol's answer to it is one re-dispatch, declared
    either way. Residual, named: a refusal that happens to quote a changed
    path reads as reviewed-clean, which is today's state and no worse.

    Without a review dir there are no outputs to read: a declared outcome
    rides through as the builder's word (the roster is `unverified-roster`
    there in any case) and an undeclared one stays absent.
    """
    out: list[dict] = []
    for index, reviewer in enumerate(reviewers):
        entry = dict(reviewer) if isinstance(reviewer, dict) else reviewer
        out.append(entry)
        if not isinstance(entry, dict):
            continue          # verify_roster refuses the shape under a dir
        who = entry.get("role") or f"reviewer #{index}"
        declared = entry.get("outcome")
        if declared is not None and declared not in OUTCOMES:
            raise AttestError(
                f"{who}: outcome {declared!r} is not one of "
                f"{', '.join(OUTCOMES)} — the vocabulary is the schema's, "
                "and a spelling outside it is a bucket nothing counts")
        if review_dir is None:
            continue
        if not entry.get("returned"):
            derived: str | None = OUTCOME_NONE
        elif entry.get("findings"):
            derived = OUTCOME_FINDINGS
        elif changed is None:
            derived = None
        else:
            try:
                text = (review_dir / str(entry.get("output"))).read_text(
                    errors="replace")
            except OSError as e:
                raise AttestError(
                    f"{who}: output {entry.get('output')!r} could not be "
                    f"read ({e}), so whether it reviewed anything cannot be "
                    "told — refused rather than recorded as a clean review"
                ) from e
            named = [f for f in changed if f and f in text]
            derived = OUTCOME_CLEAN if named else OUTCOME_NONE
        if declared == OUTCOME_NONE:
            continue          # lowered by the builder; the artifact cannot argue
        if declared is not None and declared != derived:
            if declared == OUTCOME_CLEAN and derived in (OUTCOME_NONE, None):
                raise AttestError(
                    f"{who}: declared outcome reviewed-clean, but its output "
                    f"{entry.get('output')!r} names no file the attested "
                    "range changed, so warden cannot corroborate that it "
                    "reviewed anything. A reviewer that read the diff cites "
                    "what it read; record no-review and re-dispatch instead")
            raise AttestError(
                f"{who}: declared outcome {declared!r} contradicts what the "
                f"roster and its output show ({derived!r}: "
                f"{entry.get('findings', 0)} finding(s) counted)")
        if derived is not None:
            entry["outcome"] = derived
    return out


def round_binding(review_dir: "Path | None", head_sha: str) -> tuple[str, str]:
    """Bind a reviewer's outputs to the commit their round actually judged.

    DEFENCE IN DEPTH, and named as that: the mechanism that binds a round to
    its commit is `warden round new` minting a directory the agent cannot
    choose and cannot collide on. This is the second line, for the artifact
    that reaches attest anyway.

    It is worth having because it is an IDENTITY comparison and the two checks
    already here are not. `verify_roster` proves each declared reviewer has a
    distinct, non-empty, non-symlink regular file — it cannot tell that the
    file describes a DIFFERENT diff, so a foreign package at the expected path
    passes roster verification. `range_binding` compares findings to the
    changed-path set and is SILENT whenever the two branches overlap, the
    normal case for two builders in one repo. A foreign package can share its
    BASE sha with the round it lands in and differ only in head, so head is
    what gets compared.

    Returns (state, detail); raises AttestError on a disproven binding.
    `unminted` is a real state, recorded and printed, never a silent pass:
    warden has no identity for a directory it did not mint, and saying so is
    the difference between "checked and fine" and "could not look".

    WHERE THE SIGNAL GOES. `build` STAMPS this state into the attestation,
    `memory ingest` reads it into the shard, and `render_summary` prints it,
    the same path `roster_verification` takes. A reader holding only the
    committed shards can tell a warden-minted round from any directory handed
    to `--review-dir`. The refusal above is the part that BINDS; this state is
    the part that RECORDS, somewhere a later reader can find it.

    `range_binding` keeps its own bound, which ends at the run manifest — that
    one is a heuristic over changed paths, not an identity, and folding it in
    is a separate decision that has not been made.
    """
    if review_dir is None:
        return "no-review-dir", (
            "no --review-dir given, so no round identity was available to "
            "bind these findings to a commit")
    try:
        doc = runs.round_manifest_for(review_dir)
    except runs.RoundError as e:
        raise AttestError(
            f"{e}. Refused rather than downgraded: a round manifest that "
            "cannot be read is not a round that has none") from e
    if doc is None:
        return "unminted", (
            f"{review_dir} carries no round.json, so warden did not mint it "
            "and cannot say which commit its reports describe. Mint rounds "
            "with `warden round new` to have this bound")
    got = str(doc.get("head_sha") or "")
    if got != head_sha:
        raise AttestError(
            f"the round in {review_dir} was minted for head {got[:12]}, but "
            f"this attestation stamps {head_sha[:12]}. These reviewer reports "
            "describe a different commit than the one being attested — which "
            "is how a foreign branch's review package gets attested under a "
            "MATCHING base sha. Mint a fresh "
            "round for this head (`warden round new --base <ref>`) and re-run "
            "the reviewers — a round is never re-pointed at a new commit, "
            "because its reports describe the old one")
    return "bound", (
        f"the round was minted for head {got[:12]}, which is the commit this "
        "attestation stamps")


# The states `base_binding` reports, and what each one means for the
# `base_sha` the attestation records. The first two are the ones where warden
# took the base off the round the attestation is bound to rather than off a
# name it re-resolved at write time; they differ only in whether `--base`
# still resolves to that same commit.
BASE_FROM_ROUND = "round"
BASE_ROUND_DIFFERS = "round-base-differs"
BASE_NAMED = "named"
BASE_NO_ROUND = "no-round"
BASE_ROUND_UNREADABLE = "round-unreadable"
BASE_ROUND_UNRESOLVABLE = "round-base-unresolvable"


def base_binding(root: Path, review_dir: "Path | None", named: str,
                 head_sha: str) -> tuple[str, str, str]:
    """Which commit this attestation records as the base of what was reviewed.

    THE DEFECT THIS CLOSES. `warden round new --base origin/main` resolves
    that NAME once, records the sha in `round.json`, and builds the review
    package from it — so the reviewers read exactly that range. `warden attest
    write --base origin/main` then re-resolved the same name a second time,
    hours later. In a repo where other changes land while a branch is in
    flight the two resolutions are different commits, and the shard recorded
    the later one: a base that is not even an ancestor of the branch, whose
    declared range reports an unrelated merged change's work as DELETIONS.
    Measured live, in a shard that reached main: the recorded range listed 10
    files where the round the shard attests had 5.

    So the base is taken FROM THE ROUND the attestation is bound to, whenever
    there is one — `round.json` is warden's own record of the range its
    reviewers were shown, and nothing that happens to a ref afterwards can
    move it. Only where no round was minted is the name resolved here.

    THE HEAD GUARD, and why it is not redundant with `round_binding`. The
    round's base is taken only when the round was minted for THIS head.
    `round_binding` refuses a mismatch — but it runs inside `build`, after
    this, and taking a foreign round's base first would have this function
    silently import a range from the very artifact the next check is about to
    disprove. On a mismatch the name is resolved as before and `round_binding`
    raises the refusal worth reading.

    WHAT THE STATE NAMES CLAIM, held down to what the code establishes. Two
    shas differing is ALL this function measures, and `round-base-differs`
    says only that. It deliberately does not say the ref moved: a round minted
    `--base origin/main` and attested `--base main` differs for a second
    reason entirely, and that is what this repo's own pre-pr-review skill
    does today. An annotated tag is a third: `round new` records the tag
    object's sha and this peels to the commit. So the detail names both
    spellings — `round.json` carries the base NAME beside the sha — and
    leaves the cause to the reader (round 1).

    WHAT IT DOES NOT PROVE. `round.json` is a file under a directory the
    CALLER names, so a hand-written one with a matching `head_sha` lends
    whatever base it likes, and `round`/`round-base-differs` cannot tell a
    minted round from a written one. That is the platform's standing trust
    boundary, not a new hole — `--base` was equally the caller's, and
    `verify_roster` states the boundary for the whole surface: "a caller that
    writes plausible files itself passes this check". Named here because the
    state names read like warden's own provenance and are not (round 1).

    WHAT THIS DELIBERATELY DOES NOT DO: refuse a base that is not an ancestor
    of the head. Measured against this repo's own corpus before it was
    decided — 88 of 353 committed shards record a base that is not an
    ancestor of the head they stamp, a quarter of the platform's own
    attestations. WHAT THAT MEASUREMENT SETTLES is the COST of a refusal, not
    the innocence of those 88: the round manifests that would tell a base
    minted against an already-advanced `origin/main` from one this very defect
    moved are gitignored and gone, so the shards cannot be classified either
    way (round 1). A legitimate non-ancestor base is reachable — any branch
    whose base branch moved past its fork point before the mint — which is
    why the refusal is wrong; how many of the 88 are that is not established
    here and is not claimed. The ancestry is a READING instead
    (`base_ancestry`), recorded next to the state.

    Returns `(base_sha, state, detail)`. It NEVER refuses. Every reading it
    cannot complete falls back to `--base` under a state of its own, so
    "could not look" never reads as "checked and fine" — and the refusals
    that belong to a round warden cannot read stay with `round_binding`
    inside `build`, which makes them AFTER `verify_roster` for the reason
    `build` gives: both can refuse, the roster is the stronger claim, and its
    message is the one worth reading first (round 1).
    """
    from . import diffs as diffs_mod

    if review_dir is None:
        return named, BASE_NO_ROUND, (
            "no --review-dir given, so the base was resolved from --base at "
            "write time")
    try:
        doc = runs.round_manifest_for(review_dir)
    except runs.RoundError as e:
        # REPORTED here, refused by `round_binding`. Raising out of this
        # function put the manifest's refusal ahead of `verify_roster`'s,
        # inverting the order `build` argues for (round 1). `build` calls
        # `round_binding` unconditionally, so the same unreadable manifest
        # still refuses the write — after the roster, with the roster's
        # message when both would fire.
        return named, BASE_ROUND_UNREADABLE, (
            f"{e}. --base was resolved at write time; the round binding "
            "refuses this document")
    if doc is None:
        return named, BASE_NO_ROUND, (
            f"{review_dir} carries no round.json, so warden has no minted "
            "base to stamp and --base was resolved at write time")
    if str(doc.get("head_sha") or "") != head_sha:
        # `round_binding` refuses this, out of `build`, with the message worth
        # reading. Falling back to the name here keeps that refusal first.
        return named, BASE_NAMED, (
            "the round was minted for another head, so its base was not "
            "taken; the round binding refuses this document")
    recorded = str(doc.get("base_sha") or "")
    if not recorded:
        return named, BASE_NO_ROUND, (
            f"{review_dir}'s round.json records no base_sha, so --base was "
            "resolved at write time")
    try:
        minted = diffs_mod.run_git(
            root, "rev-parse", "--verify", f"{recorded}^{{commit}}").strip()
    except diffs_mod.DiffError:
        # REPORTED, not refused, and the distinction is argued rather than
        # assumed. A base this repository does not have cannot be stamped —
        # a shard recording an unresolvable sha declares a range nobody can
        # read — so `--base` is what remains, which is the behaviour that
        # predates this function. Refusing instead would buy nothing, for the
        # trust-boundary reason the docstring gives above.
        return named, BASE_ROUND_UNRESOLVABLE, (
            f"{review_dir}'s round.json records base {recorded[:12]}, which "
            "is not a commit this repository has, so it cannot be stamped "
            "and --base was resolved at write time instead")
    if minted != named:
        # The NAME the round recorded, printed beside the sha, because the
        # commonest reason two resolutions differ is two different names.
        minted_name = str(doc.get("base") or "")
        under = f" under the name {minted_name!r}" if minted_name else ""
        return minted, BASE_ROUND_DIFFERS, (
            f"the round this attestation is bound to was minted against "
            f"{minted[:12]}{under}, and --base now resolves to "
            f"{named[:12]}. Its reviewers read the round's range, so that is "
            "what is stamped — a shard declares the range that was reviewed, "
            "not one the flag happens to name at write time. Warden measures "
            "only that the two differ: a moved ref, a different ref name, "
            "and a tag resolving to its commit all look like this here")
    return minted, BASE_FROM_ROUND, (
        f"the round was minted against base {minted[:12]}, which is what "
        "--base still names")


def base_ancestry(root: Path, base_sha: str, head_sha: str) -> str:
    """`ancestor`, `not-ancestor`, or `unavailable` for this recorded base.

    A READING, never a refusal — see `base_binding` for the corpus
    measurement that settled that, and for what that measurement does and
    does not establish.

    `ancestor` means the recorded base is REACHABLE from the head, so
    `git diff base..head` over a shard's own figures is a range that HOLDS.
    It is NOT a claim that the range is only this change: a branch that
    merged its base branch forward is still `ancestor`, and the range then
    reports the base branch's own work alongside this change's.
    `not-ancestor` does not make the range meaningless either, and it is not
    one artefact a reader subtracts: `base..head` diffs against a tree the
    branch never had, so the base-only work comes back INVERTED — a file the
    base deleted prints as an addition, one it added as a deletion — and a
    line both sides touched prints the BASE's line as the one this change
    removed. The reading that is not lossy is the three-dot `base...head`,
    which diffs from the fork point and is the range the round's reviewers
    read. Separating this change from work merged INTO it is a third question
    no state answers — the third dot included, since merged-forward work is
    in the branch's history — and `range_patch_ids` does not either
    (`--no-merges base..head` lists both sides). The nearest reading is
    `git log --first-parent --no-merges base_sha..head_sha`, off the two sha
    fields every shard carries: it walks the FIRST-PARENT line, so it drops a
    commit that reached the branch through a merge of the branch's own topic
    branch too.

    `unavailable` is its own state for the reason every other "could not
    look" here is: it must not read as "checked and fine", and it must not
    read as checked and found bad either — every caller renders it on its own
    branch. It is not only "no git and no repository to ask": ANY
    `git merge-base --is-ancestor` exit other than 0 or 1 lands here, and a
    base sha THIS clone cannot resolve — a shallow clone, a pruned fork
    point, a base a force-push dropped — exits 128. That cause is the one a
    committed shard most plausibly carries.
    """
    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", base_sha, head_sha],
            cwd=root, capture_output=True, text=True, env=runs.git_env())
    except OSError:
        # No git on PATH, or it could not be executed. `diffs.run_git` turns
        # this into a DiffError and `range_patch_ids` answers its own
        # "could not compute" state for it; this answers that state directly,
        # rather than raising an OSError out of a function documented to
        # return one of three readings (round 1).
        return "unavailable"
    if proc.returncode == 0:
        return "ancestor"
    if proc.returncode == 1:
        return "not-ancestor"
    return "unavailable"


def minted_round(review_dir: "Path | None") -> int | None:
    """The round NUMBER warden minted for `review_dir`, or None when it did
    not mint it.

    The roster's `round` labels are the builder's text. This is warden's own
    record of which round the directory IS — written by `warden round new`,
    derived there from the review chain — and comparing the two is what stops
    a first and only review labelling itself `round: 2` to be checked against
    round 2's declared crew instead of round 1's.

    None means UNMINTED: no round.json at all, the same state `round_binding`
    records, and what it means is the caller's to decide. A manifest that
    cannot be READ raises, as it does there; so does one whose `round` is
    present in a shape warden cannot read, for the reason `_round_of` refuses
    a stated round that is not an int — a value in the wrong shape is not a
    missing one, and reading it as missing would skip the comparison.
    """
    if review_dir is None:
        return None
    try:
        doc = runs.round_manifest_for(review_dir)
    except runs.RoundError as e:
        raise AttestError(
            f"{e}. Refused rather than downgraded: a round manifest that "
            "cannot be read is not a round that has none") from e
    if doc is None:
        return None
    value = doc.get("round")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AttestError(
            f"the round manifest governing {review_dir} states round "
            f"{value!r}, which is not a round number as warden reads one (a "
            "whole number at or above 1). A round that cannot say which round "
            "it is cannot have its roster checked against the crew graph.yaml "
            "declares for that round")
    return value


def _round_dir_for(review_dir: Path) -> Path | None:
    """The minted round directory `review_dir` belongs to, or None when warden
    did not mint it. Mirrors `runs.round_manifest_for`'s search — the manifest
    sits beside the reviewers dir or one level up — and raises the same way it
    does, so an unreadable identity is never read as an absent one."""
    doc = runs.round_manifest_for(review_dir)
    if doc is None:
        return None
    return review_dir if (review_dir / "round.json").exists() else review_dir.parent


def carry_forward(reviewers: list, review_dir: Path | None) -> tuple[str, str]:
    """Refuse a reviewer output that is another round's output, byte for byte.

    THE GAP THIS CLOSES. A later round's directory can hold a byte-identical
    copy of an earlier round's `code-reviewer.json`. `verify_roster` is
    satisfied by it: a distinct, non-empty, non-symlink regular file in the
    right directory is all it asks, and a copy is exactly that.
    `round_binding` is satisfied too: each round WAS minted for the head it
    attested. Without this check, the roster check that stops a shard
    claiming a review that did not happen would be satisfied by a stale
    artifact.

    WHAT IS COMPARED, AND WHY CONTENT. Each claimed output in this round is
    hashed and compared against every non-empty regular file under every
    sibling round's `reviewers/` beneath the same rounds root, and against the
    other claimed outputs of this round. Content is the identity that answers
    "is this the same artifact" in every spelling: `cp` (two inodes, one
    content — invisible to the inode check, which is scoped to one round),
    `ln` (one inode under two rounds), and a re-write of the same bytes. Any
    hit is a refusal naming the reviewer, the file, and the round it matches.

    WHY IDENTICAL BYTES CAN BE REFUSED OUTRIGHT. Warden does not read the
    report; identity is the whole test. What keeps an honest report from ever
    reproducing another one's bytes is the pre-pr-review skill (0.12.0), which
    has every reviewer name three things in its raw output — the head it
    `judged`, the `round` it ran in, and its own `role` — a PROTOCOL, and this
    function does not verify it. Three fields because a report's
    identity has three coordinates and `judged` carries one: it separates reports across different HEADS,
    and leaves two HONEST states colliding. On a clean PR both reviewers of one
    round write `{"judged": H, "findings": []}`, so the second would read as a
    copy of the first; and two rounds minted for the SAME head — a re-run whose
    whole point is a fresh reading, where identical bytes are no more evidence
    than across heads — would reproduce each other. Both are the protocol's
    own golden path. With the round id and the role
    named too, an honest report is distinguishable in both comparisons this
    function makes and only a genuine copy collides — a copy carries its source's
    round and role, which is exactly what makes it one. A reviewer on an older
    skill that writes a canonical clean `[]` twice is still refused, and the
    message says what to change rather than asserting the fields were there.
    Refusing rather than marking needs no schema field, so it keeps the
    artifact contract (HIGH-tier, additive-only) untouched.

    EVERY SIBLING, NOT THE PREVIOUS ONE. Several later rounds can all carry
    round 1's bytes; a check against only the preceding round would let round
    1's bytes into round 4 the moment rounds 2 and 3 were cleaned up.

    FAIL-CLOSED WITHIN THE EVIDENCE OFFERED. A rounds root or a sibling's
    `reviewers/` that cannot be listed or read is a refusal: a sibling that
    cannot be examined cannot be ruled out as the source of a copy. A sibling
    with NO `reviewers/` — a round minted and abandoned, a stray file — has
    nothing to have been copied from, and is skipped: absence is not
    unreadability — but a `reviewers/` that is a link to NOTHING is not
    absence either, though `iterdir()` raises the same FileNotFoundError for
    both, so the link is separated from the empty seat with `lstat` and
    refuses. The WITHIN-ROUND half runs before the round is identified at all,
    because it needs nothing from the manifest: gating it on minted-ness
    would switch the content check off in `unminted` — the one state where
    `verify_roster`, keyed on the inode and blind to `cp`, cannot cover for it.
    The review dir is RESOLVED before any parent of it is walked, since
    `Path("reviewers").parent.parent` is `.` and would make the round dir its
    own rounds root (zero siblings, copy accepted); and every entry is examined
    with `lstat`/`stat`, which raise, never `is_file()`/`is_dir()`, which on
    CPython 3.13+ swallow EACCES and answer False — a sibling `reviewers/` at
    0o444 lists its names, every name reads as "not a file", and the sibling
    would count as compared. A sibling that is a symlink is compared THROUGH,
    never skipped: following it adds a refusal wherever it resolves to another
    round's reports. A link resolving back to the round UNDER attestation is
    excluded by RESOLVED IDENTITY, not path equality, so this round's own
    reports never match themselves. A link that dangles cannot be examined and
    refuses.

    Returns (state, detail). `distinct` says what was compared. `unminted` and
    `no-review-dir` are the states `round_binding` already stamps, repeated
    here so a reader of the run manifest sees WHICH comparison ran. They are
    not "nothing ran": `no-review-dir` is nothing offered to compare, but
    `unminted` still compares this round's claimed reports against each other
    and can refuse there — only the sibling scan needs a rounds root, and the
    detail string for that state names the count it did compare. Raises
    AttestError on a disproven claim. Ordered after `verify_roster` (every
    claimed output is known to be a regular non-empty file) and after
    `round_binding` — which does not mean the round is known minted, since
    `round_binding` RETURNS `unminted` rather than raising and only a round
    minted for a DIFFERENT head is a refusal there. So a refusal here is about
    the bytes and nothing else, in every one of those states.
    """
    if review_dir is None:
        return "no-review-dir", (
            "no --review-dir given, so no reviewer output could be compared "
            "against any other round")
    # Resolved BEFORE any parent is taken. A relative one-component path has
    # `.` for a parent and `.` for a grandparent, which would make the round
    # dir its own rounds root and hide every sibling.
    review_dir = Path(os.path.realpath(review_dir))

    def digest(path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as e:
            raise AttestError(
                f"{path} could not be read ({e}), so it cannot be ruled out "
                "as a copy of another round's report — refused rather than "
                "passed: a check that cannot evaluate behaves like a hit") from e

    # This round's claimed outputs, hashed. Within-round identity is the same
    # claim as cross-round: one artifact backs ONE roster claim, once.
    claimed: dict[str, str] = {}
    for reviewer in reviewers:
        if not reviewer.get("returned"):
            continue
        who = reviewer.get("role") or "reviewer"
        output = str(reviewer["output"])
        d = digest(review_dir / output)
        if d in claimed:
            raise AttestError(
                f"{who}: output {output!r} is byte-identical to the report "
                f"{claimed[d]} already claimed in this round. One report "
                "cannot be evidence that two reviewers ran, whether it was "
                "linked or copied to the second name. If both reviewers "
                "genuinely ran and both came back clean, have each name its "
                "own `role` and the `round` it ran in alongside the head it "
                "judged (the pre-pr-review skill's report shape), so two "
                "honest reports are never the same bytes")
        claimed[d] = f"{who} ({output})"

    # Only NOW is the round's own identity needed. The comparison above needs
    # nothing from the manifest, so a dir warden did not mint — `unminted`, a
    # state the protocol blesses at exit 0 for a warden predating `round new`
    # — must not switch it off. There `verify_roster` cannot cover for it: its
    # key is the inode and `cp` makes two, which is the seam this function
    # exists to close.
    try:
        round_dir = _round_dir_for(review_dir)
    except runs.RoundError as e:
        raise AttestError(
            f"{e}. Refused rather than downgraded: a round manifest that "
            "cannot be read is not a round that has none") from e
    if round_dir is None:
        return "unminted", (
            f"{len(claimed)} claimed report(s) compared against each other "
            f"and found distinct; {review_dir} was not minted by warden, so "
            "it has no rounds root and no sibling rounds to compare them "
            "against")

    rounds_root = round_dir.parent
    # Excluded by RESOLVED IDENTITY, never by path equality. A symlink in the
    # rounds root that resolves BACK to the round being attested is not
    # `!= round_dir` as a path, so it would be walked as a sibling, its
    # `reviewers/` would be this round's own, and every claimed report would
    # match itself: one honest round plus
    # `os.symlink(round_dir, rounds_root / "latest")` would turn a clean
    # `distinct` into a refusal naming a copy that does not exist. Symlinked
    # siblings are followed because following can only add a refusal, and the
    # self-pointing link is the one case where it would add a FALSE one. A
    # symlink to a DIFFERENT round is still compared through.
    def is_this_round(entry: Path) -> bool:
        if entry == round_dir:
            return True
        try:
            return os.path.realpath(entry) == os.path.realpath(round_dir)
        except OSError:
            # A path that cannot even be resolved is not excluded; the sibling
            # scan below examines it with `stat` and refuses if it cannot be
            # read, which is the fail-closed answer rather than a silent skip.
            return False

    try:
        entries = sorted(p for p in rounds_root.iterdir()
                         if not is_this_round(p))
    except OSError as e:
        raise AttestError(
            f"the rounds root {rounds_root} could not be listed ({e}), so no "
            "sibling round can be ruled out as the source of a copied report "
            "— refused rather than passed") from e
    # `stat`, following a link, and never `is_dir()`: the question is "is
    # there a directory here to compare through", and a probe that answers
    # False for "could not look" is the fail-open shape.
    siblings = []
    for entry in entries:
        try:
            st = entry.stat()
        except OSError as e:
            raise AttestError(
                f"{entry} under the rounds root could not be examined ({e}), "
                "so it cannot be ruled out as a round holding a copied report "
                "— refused rather than passed: a check that cannot evaluate "
                "behaves like a hit") from e
        if stat.S_ISDIR(st.st_mode):
            siblings.append(entry)
    compared = 0
    for sibling in siblings:
        reports_dir = sibling / "reviewers"
        try:
            names = sorted(reports_dir.iterdir())
        except FileNotFoundError as e:
            # Absence is not unreadability — and a DANGLING SYMLINK is not
            # absence. `iterdir()` raises FileNotFoundError for both, and they
            # are opposite answers: nothing to have been copied from, versus
            # cannot be ruled out. `lstat` sees the link itself, so it
            # separates them.
            try:
                reports_dir.lstat()
            except OSError:
                # A bare `continue`: `compared` is NOT incremented, because
                # nothing was compared. That detail string is load-bearing
                # evidence — it is what an auditor reads to know how much of the
                # rounds root was examined — and an inflated count reads as a
                # wider scan than ran, the same "a partial scan renders as a
                # clean one" shape this function's fail-closed arms exist for.
                # `test_an_abandoned_sibling_is_not_counted_as_compared`
                # asserts the READING, so `compared += 1` here turns it red.
                continue
            raise AttestError(
                f"{reports_dir} could not be read ({e}) — it is a link to "
                f"nothing, so the round {sibling.name} cannot be ruled out as "
                "the source of a copied report — refused rather than passed: "
                "a check that cannot evaluate behaves like a hit") from e
        except OSError as e:
            raise AttestError(
                f"{reports_dir} could not be listed ({e}), so the round "
                f"{sibling.name} cannot be ruled out as the source of a "
                "copied report — refused rather than passed: a check that "
                "cannot evaluate behaves like a hit") from e
        # `lstat` per entry, which raises on a directory that lists but cannot
        # be searched (0o444) on every interpreter; `is_file()` swallows that
        # EACCES on CPython 3.13+ and would count the sibling as compared. A
        # symlink here is not a report (S_ISREG is false for it), matching
        # verify_roster; an empty file is a failed write, not a report.
        reports = []
        for p in names:
            try:
                st = p.lstat()
            except OSError as e:
                raise AttestError(
                    f"{p} could not be examined ({e}), so the round "
                    f"{sibling.name} cannot be ruled out as the source of a "
                    "copied report — refused rather than passed") from e
            if stat.S_ISREG(st.st_mode) and st.st_size:
                reports.append(p)
        compared += 1
        for report in reports:
            d = digest(report)
            if d in claimed:
                raise AttestError(
                    f"{claimed[d]}: this report is byte-identical to "
                    f"{report.name} in round {sibling.name} — a reviewer "
                    "output carried forward from another round, which is no "
                    "evidence that the reviewer ran in THIS one. Re-run the "
                    "reviewer in this round and have it name the head it "
                    "judged, the `round` it ran in and its `role` in its "
                    "output (the pre-pr-review skill's report shape), so a "
                    "fresh report never reproduces another round's bytes — "
                    "including a re-run at the same head")
    return "distinct", (
        f"{len(claimed)} claimed report(s) compared against {compared} "
        f"sibling round(s) under {rounds_root}; none is a copy of another")


def _round_of(value: object, *, who: str) -> int | None:
    """A declared round, or None when NONE is stated.

    Absent is the only way to get None. A stated value that is not a Python
    int is refused by name: JSON Schema's `type: integer` accepts `2.0`, and
    reading that as "no round" would let a finding skip the join — attested
    against a lens never dispatched in that round, with ingest then dropping
    the round on its own `isinstance(int)` test. The schema
    still owns the range (>= 1); this owns the one gap between the two
    languages' idea of an integer.
    """
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise AttestError(
        f"{who}: round {value!r} is not an integer as warden reads one — "
        "write the round as a whole number (2, not 2.0 or \"2\"). Refused "
        "rather than read as absent: a stated value in the wrong shape is "
        "not a missing one, and reading it as missing let the join compare "
        "nothing")


def verify_attribution(reviewers: list, findings: list, *,
                       required: bool) -> None:
    """Join every attributed finding to the dispatch that raised it, and
    under --review-dir require the attribution.

    WHAT THIS EXISTS FOR. The roster makes "who reviewed, and how many raw
    findings each returned" a checked claim. It does not say which reviewer
    a RECORDED finding came from, so without this join per-lens outcome — how
    often each lens's findings end fixed, refuted or dismissed — is
    unrecoverable from the committed corpus. The vocabulary is the schema's
    `$defs.lens` (read into `lens_is_valid`), and this is the join.

    REQUIRED WHERE THE CHECK RUNS, OPTIONAL IN THE CONTRACT — the exact trade
    `returned` made. The schema keeps the fields optional because shards
    committed before them exist and warden/schemas is additive-only. Under
    `--review-dir` they are required: an optional field nobody is made to
    fill stays empty, and every round attested without them is one the
    per-lens statistics can never count. Without `--review-dir`, a finding
    that VOLUNTEERS a lens is still joined — a lens the roster never declared
    is a contradiction whether or not a directory was offered.

    THE JOIN. A finding's (lens, round) must name a roster entry whose `role`
    is that lens, dispatched in that round, that `returned: true`. A round
    is compared only when both sides state one: a legacy roster with no
    rounds joins on the lens alone rather than refusing what it cannot
    check. What is deliberately NOT checked: the roster's raw `findings`
    count against the attributed findings. One raw finding naming two files
    is honestly filed as two records, so that inequality would refuse honest
    rounds.

    Runs AFTER schema validation, but does NOT inherit its reading: the
    schema is the floor here, not the contract. Two shapes it accepts are
    refused in this function, on purpose — a lens with a trailing newline
    (`$defs.lens`'s `$` matches before a final newline; `lens_is_valid`
    fullmatches) and a round of `2.0` (`{"type": "integer"}` accepts a
    float whose value is integral; `_round_of` refuses it). What the
    schema pass DOES settle before this point: a round below 1 is a schema
    error naming the field, and every value read here is of the declared JSON
    type. Runs BEFORE the verdict checks, so a misattributed finding on a
    `findings-open` verdict names the lens, which is the actionable error,
    rather than the verdict.
    """
    hint = _lens_vocabulary_hint()
    # (role, round) -> whether any entry with that key returned. A lens that
    # ran in two rounds is two entries; one that was re-dispatched in the same
    # round after not returning is two entries too, and either returning is
    # enough for the dispatch to have raised something.
    dispatched: dict[tuple[str, int | None], bool] = {}
    for index, reviewer in enumerate(reviewers):
        role = reviewer.get("role")
        who = role if isinstance(role, str) and role else f"reviewer #{index}"
        rnd = _round_of(reviewer.get("round"), who=who)
        if required:
            if not lens_is_valid(role):
                raise AttestError(
                    f"{who}: role {role!r} is not in the lens vocabulary. "
                    f"Under --review-dir every roster entry names its lens as "
                    f"{hint}, so a per-lens count can be read off the corpus. "
                    f"{_STALE_PACK}")
            if rnd is None:
                raise AttestError(
                    f"{who}: roster entry states no `round`. Under --review-dir "
                    "every dispatch says which review round it ran in — 1 for "
                    "the first pass over the diff, incremented on each "
                    "re-review — so a finding can be joined to the dispatch "
                    f"that raised it. {_STALE_PACK}")
        # Absent `returned` is a legacy roster, where listing a reviewer meant
        # it reviewed; under --review-dir `verify_roster` has already refused
        # an entry that does not say.
        returned = reviewer.get("returned", True) is True
        key = (role, rnd)
        dispatched[key] = dispatched.get(key, False) or returned

    for f in findings:
        where = f"{f.get('file')}:{f.get('line', '?')}"
        if required:
            for field in ("lens", "round"):
                if field not in f:
                    raise AttestError(
                        f"{where}: finding carries no `{field}`. Under "
                        "--review-dir every finding names the lens that raised "
                        f"it ({hint}) and the round it arrived in — a finding "
                        "nobody is recorded as raising cannot be counted against "
                        f"any lens. {_STALE_PACK}")
        # A stated round is checked for shape whether or not it is required,
        # and so is a stated lens — the schema's `$` is not the strict
        # reading, so the schema pass is not enough here.
        rnd = _round_of(f.get("round"), who=where)
        if "lens" not in f:
            continue
        lens = f["lens"]
        if not lens_is_valid(lens):
            raise AttestError(
                f"{where}: lens {lens!r} is not in the lens vocabulary — "
                f"{hint}. The schema's pattern is read strictly here: a "
                "trailing newline or other invisible suffix would be a second "
                "corpus key for one lens")
        same_lens = {k: v for k, v in dispatched.items() if k[0] == lens}
        if not same_lens:
            declared = ", ".join(sorted({k[0] for k in dispatched
                                         if isinstance(k[0], str)})) or "nobody"
            raise AttestError(
                f"{where}: finding is attributed to lens {lens!r}, but no "
                "roster entry declares that lens — a finding cannot have been "
                "raised by a reviewer that was not dispatched. The roster "
                f"declares: {declared}")
        # Round compared only where both sides state one.
        in_round = {k: v for k, v in same_lens.items()
                    if rnd is None or k[1] is None or k[1] == rnd}
        if not in_round:
            ran = ", ".join(str(k[1]) for k in sorted(
                same_lens, key=lambda k: k[1] or 0))
            raise AttestError(
                f"{where}: finding is attributed to lens {lens!r} in round "
                f"{rnd}, but that lens was dispatched only in round(s) {ran}. "
                "A finding arrives in a round its reviewer ran in; if this "
                "one came from a re-dispatch, declare that dispatch in the "
                "roster with its own round and output")
        if not any(in_round.values()):
            raise AttestError(
                f"{where}: finding is attributed to lens {lens!r}"
                f"{f' in round {rnd}' if rnd is not None else ''}, but that "
                "reviewer is declared `returned: false` — a reviewer that did "
                "not return raised nothing. Either the roster is wrong about "
                "the return or the finding is attributed to the wrong lens; "
                "guessing which would be inventing the answer")


def prove_inert(root, base: str, head: str) -> "LightProof":
    """Recompute, from the two trees, whether `base..head` can alter
    behaviour.

    A thin seam over the classifier so that attest never imports its
    internals and a failure to decide becomes a refusal rather than a crash:
    anything the classifier cannot prove comes back as not-light, carrying
    the cause, and the expensive path follows.
    """
    from . import proportion
    verdict = proportion.classify(str(root), base, head)
    return LightProof(verdict.light, verdict.reason)


@dataclass(frozen=True)
class LightProof:
    """A recomputed verdict that a range cannot alter behaviour.

    It is built by the caller from the two trees, never from the payload. The
    `reason` is carried so a refusal can say WHICH file stopped the proof
    rather than only that one did.
    """

    light: bool
    reason: str


def _is_light_roster(reviewers: list, crew: dict) -> bool:
    """Whether this roster is the light crew and nothing else.

    Exact and total: every entry's role must be a light role, and every light
    role must appear. A roster that mixes the light crew with a capped round's
    is not a light round and falls through to the ordinary comparison, where
    it is refused by round like any other mismatch.
    """
    light = crew.get("light")
    if not light:
        return False
    roles = {r.get("role") for r in reviewers if isinstance(r, dict)}
    return bool(roles) and roles == set(light["roles"])


def _refuse_unproven_light(reviewers: list, crew: dict, minted: int | None,
                           proof: "LightProof | None") -> None:
    """Refuse a light roster the diff does not earn, or a round warden did not
    mint as this one.

    Fail closed at every end. No proof offered at all is a refusal, not a
    pass: it means the caller could not compute one, and a tier that defaults
    to itself when its evidence is missing is not a tier.

    The minted number binds here exactly as it does on the full path, and for
    the same reason: the round label decides what a roster is measured
    against, so a label nothing checks is a number the builder chooses. The
    light round is the FIRST and only round, so it is round 1 — a light crew
    labelled round 2 would be a cheap round riding behind a real one, and a
    light crew in an unminted directory would be a round warden never made.
    """
    declared = ", ".join(crew["light"]["roles"])
    if minted is None:
        raise AttestError(
            f"the roster dispatches the light crew ({declared}), but this "
            "round carries no minted round number, so the label nothing "
            "checks would be the builder's to choose. Mint the round with "
            "`warden round new`, which records the number in its round.json, "
            "and write the attestation with `--review-dir <that round>`: the "
            "light crew is the one roster no write may assert, so leaving the "
            "flag off is not a way to reach it either")
    if minted != 1:
        raise AttestError(
            f"the roster dispatches the light crew ({declared}) in round "
            f"{minted}, and the light round is round 1 — the FIRST and only "
            "round for a change that needs no adversarial pass, never a cheap "
            "round minted behind a real one or bought past the cap of "
            f"{crew['cap']}. A head a repair moved is what the closure round "
            "is for")
    # Through `_round_of`, exactly as the full path reads a label. A raw
    # `.get("round")` here would take `2.0` and `True` — both of which JSON
    # Schema's `type: integer` lets through — and would raise TypeError rather
    # than a named refusal the moment one entry stated a round and another did
    # not. The claim above this function is that the light round binds its
    # labels the way the numbered rounds do; reusing their reader is what
    # makes that true rather than nearly true.
    labels = set()
    for index, reviewer in enumerate(reviewers):
        if not isinstance(reviewer, dict):
            continue
        role = reviewer.get("role")
        who = role if isinstance(role, str) and role else f"reviewer #{index}"
        label = _round_of(reviewer.get("round"), who=who)
        if label is None:
            raise AttestError(
                f"{who}: roster entry states no `round`, so it cannot be "
                f"held to the round warden minted ({minted}) — and the light "
                "round is the only round its change gets")
        labels.add(label)
    if labels != {minted}:
        raise AttestError(
            f"the roster dispatches the light crew ({declared}) but labels "
            f"its entries with round(s) {sorted(labels)}, and warden minted "
            f"this round as round {minted}. Every entry states the round it "
            "was dispatched in, and the light round has exactly one")
    if proof is None:
        raise AttestError(
            f"the roster dispatches the light crew ({declared}), but warden "
            "could not recompute whether this range is provably inert, so the "
            "proof that admits that crew is missing. Run `warden attest "
            "classify --base <base>` to see why, and review this change with "
            "the full declared crew")
    if not proof.light:
        raise AttestError(
            f"the roster dispatches the light crew ({declared}), which is "
            "declared only for a change that provably cannot alter behaviour "
            f"— and this range can: {proof.reason}. Eligibility is recomputed "
            "from the diff, so it is not the builder's to assert. Run `warden "
            "attest classify --base <base>` for the file-by-file verdict, and "
            "dispatch the full declared crew")


def verify_crew(reviewers: list, crew: dict | None,
                minted: int | None, *,
                light_proof: "LightProof | None" = None) -> None:
    """Refuse a roster that is not the crew graph.yaml declares for its round.

    `crew` is `graph.declared_crew`'s reading: `{"cap": int, "rounds": {n:
    [roles]}, "closure": {...} | None}`, or None when the repo's graph.yaml
    declares no `review` block, in which case nothing is compared and the
    roster is taken as before.

    A round past the cap is the CLOSURE round, and only where the org
    declares `review.closure`: a single declared role, dispatched to attest a
    head a capped round's repair moved. Its NUMBER is not pinned to cap+1 and
    nothing counts how many closure rounds a branch attests — see the body
    below for why pinning it would fail the branches it exists to rescue.
    What makes it terminal is not a count: no REVIEW runs past the cap,
    because the only roster accepted there is the closure crew, and
    `verify_closure` refuses any record of its that does not re-file a claim a
    capped round already committed. Its ROSTER is checked here like any other
    round's; what it may FILE is `verify_closure`'s question.

    `minted` is `minted_round`'s reading of the round's own record — WHICH
    round warden minted this directory as. It is required because the round
    labels decide which declared crew each entry is measured against, and a
    label nothing checks is a number the builder chooses: a first and only
    review labelled `round: 2` used to be held to round 2's crew, the short
    one, and write CLEAN at exit 0. The roster's HIGHEST round must be the
    minted one — lower and the round being attested was reviewed by nobody,
    higher and the roster claims a round warden never minted — while lower
    rounds may still appear beneath it, because a roster spanning rounds is a
    shape older shards carry and the attestation schema still describes.

    Each roster entry's `round` says which round it was dispatched in, and
    the roles of each round must be exactly the declared ones: a declared role
    with no entry is missing, and an entry whose role that round does not
    declare is extra. Both are named. A role listed twice (a re-dispatch) is
    one role. A dispatch that did not return still counts as dispatched here;
    its `no-review` outcome is stamped by `derive_outcomes`, not hidden.

    `light_proof` carries the recomputed verdict for the range this roster
    claims to have reviewed, where the org declares `review.light`. A roster
    that IS the light crew is accepted only if that verdict proves the range
    inert; otherwise it is refused and the first blocking file is named. The
    verdict is recomputed from the two trees by the caller, never read from
    the payload, so a builder cannot declare their own change eligible — which
    is the whole reason the tier can exist at all.

    THE CONTRACT, and it changed. Every comparison in this function is still
    asked only where a round dir was offered, because every one of them needs
    the minted number this function refuses to do without. The LIGHT branch is
    not: `build` calls this for a light roster whether or not `--review-dir`
    was given, so the tier's one check cannot be reached around by omitting
    the flag. A caller with no round dir therefore meets
    `_refuse_unproven_light`'s missing-mint refusal rather than an exit 0 —
    which is the point, since a light roster nothing checked is the
    self-certification the tier exists to refuse. A roster that is anything
    else is untouched by this and still passes without a round dir.
    """
    if crew is None:
        return
    if _is_light_roster(reviewers, crew):
        _refuse_unproven_light(reviewers, crew, minted, light_proof)
        return
    cap, declared = crew["cap"], crew["rounds"]
    by_round: dict[int, list[str]] = {}
    for index, reviewer in enumerate(reviewers):
        if not isinstance(reviewer, dict):
            continue          # verify_roster refuses the shape first
        role = reviewer.get("role")
        who = role if isinstance(role, str) and role else f"reviewer #{index}"
        if not isinstance(role, str) or not role:
            raise AttestError(
                f"{who}: roster entry names no `role`, so it cannot be "
                "compared with the crew graph.yaml declares")
        rnd = _round_of(reviewer.get("round"), who=who)
        if rnd is None:
            raise AttestError(
                f"{who}: roster entry states no `round`, so it cannot be "
                "compared with the crew graph.yaml declares for its round "
                "(`warden graph crew --round N`)")
        by_round.setdefault(rnd, []).append(role)
    if not by_round:
        raise AttestError(
            "the roster dispatches nobody, and graph.yaml declares a review "
            "crew for every round — there is no roster to compare with it")
    # The labels against warden's own record of the round, BEFORE the crew
    # comparison: which crew a roster is measured against is decided by the
    # round it claims, so a claim nobody checks decides the check.
    if minted is None:
        raise AttestError(
            "this round carries no minted round number, so the roster's round "
            "labels can be checked against nothing — and graph.yaml declares "
            "a crew per round, which is chosen BY that label. Mint the round "
            "with `warden round new`, which records the number in its "
            "round.json; a directory minted by a warden that predates the "
            "number, or one whose manifest has been edited since, must be "
            "re-minted and its reviewers re-run")
    highest = max(by_round)
    if highest != minted:
        # The closure round's crew is not in `rounds` — it is declared past
        # the cap — so the hint is read from whichever declaration governs
        # the minted number, or the message would tell a builder standing in
        # the closure round that it declares nobody.
        closure_crew = (crew.get("closure") or {})
        want = ", ".join(
            (closure_crew.get("roles") if minted is not None and minted > cap
             else declared.get(minted)) or []) or "no declared crew"
        raise AttestError(
            f"the roster's rounds run to round {highest}, but warden minted "
            f"this round as round {minted} (its round.json). The round label "
            "decides which declared crew the roster is checked against, so it "
            f"is not the builder's to choose: round {minted} declares {want}. "
            "Attest each round against the round `warden round new` minted "
            "for it")
    closure = crew.get("closure")
    for rnd in sorted(by_round):
        if rnd > cap:
            # Past the cap is the CLOSURE round wherever the org declares
            # one. Its roster is held to the closure crew exactly as a capped
            # round's is held to that round's, and the single declared role is
            # what makes "it raises nothing" a shape rather than a promise:
            # there is no finder here to find with. What it may FILE is
            # checked separately, in `verify_closure` — this half only decides
            # who ran.
            #
            # ANY number past the cap, not cap+1 alone. The cap's refusal
            # fires HERE, after `warden round new` has already created the
            # directory, so a builder who hits this trap is holding a stray
            # empty round at cap+1 and their closure round is cap+2. Pinning the number would
            # fail exactly the branches the round exists to rescue, and it
            # would buy nothing: a closure round at any number spends no
            # review budget, because it cannot find.
            if closure is None:
                raise AttestError(
                    f"the roster dispatches round {rnd}, past the cap of "
                    f"{cap} graph.yaml declares — no review round runs past it")
            want = list(closure["roles"])
        else:
            want = declared.get(rnd)
        if want is None:
            raise AttestError(
                f"graph.yaml declares no crew for round {rnd}, so the round "
                f"{rnd} roster cannot be checked")
        roles = by_round[rnd]
        missing = [r for r in want if r not in roles]
        extra = [r for r in dict.fromkeys(roles) if r not in want]
        if missing or extra:
            parts = []
            if missing:
                parts.append(f"missing declared role(s): {', '.join(missing)}")
            if extra:
                parts.append("role(s) not declared for this round: "
                             f"{', '.join(extra)}")
            raise AttestError(
                f"the round {rnd} roster is not the crew graph.yaml declares "
                f"({', '.join(want)}): {'; '.join(parts)}. `warden graph crew "
                f"--round {rnd}` prints the declared crew; dispatch exactly "
                "that, or change the review block in a reviewed PR")


# --------------------------------------------------------------------------
# The closure round: terminal, non-budgeted, and provably unable to find
# --------------------------------------------------------------------------
#
# THE DEFECT IT CLOSES. `review.cap` conflates two things: the
# budget for REPAIR rounds, and the ability to attest a final head. A repair
# commit answering a round at the cap seals that round with its findings still
# open, the cap refuses another round, and `round_binding` refuses re-pointing
# the sealed round at the new head — so the branch has NO attestable head at
# all and its required gate is red forever. PR #266 is the live reproduction.
#
# THE ANSWER, and the ONE property that decides whether it is worth anything:
# a round minted past the cap, a single declared role, whose payload may only
# RE-FILE claims a round at or under the cap already raised. If that is asked
# rather than enforced it is a third review round with a fig leaf, and the cap
# becomes a fiction. So it is enforced here, claim by claim, against evidence
# that is already committed to this branch when the closure round runs: the
# attestation shards the capped rounds produced. The identity compared is the
# whole claim — rule_id, file, line, severity and the finding text — because
# round 1 of this feature's own review proved that a (rule_id, file) key is
# not one: the charter lenses file against the change's own files every round,
# so a pair is cheap to reuse and a new defect could ride in under it.
#
# WHY THE COMMITTED SHARDS AND NOT THE ROUND DIRECTORIES. `.warden/out/` is
# gitignored and per-worktree: a chain that exists on the machine that ran the
# rounds does not exist in CI, in a fresh clone, or in the worktree a later
# session opens. The shards are in the branch's history, which is the artifact
# the gate, the corpus and the human reviewer all read, and which cannot be
# changed for this check without changing the PR's diff.
#
# WHAT IS DELIBERATELY *NOT* CLAIMED. This does not out-argue the platform's
# standing trust boundary (`verify_roster`: "a caller that writes plausible
# files itself passes this check"). A builder who commits a fabricated round
# shard can widen what the closure round may file. What it removes is every
# way to widen it SILENTLY: each admissible key traces to a record in a shard
# that is in the PR, attributed to a round, and that a reviewer reads.


def _closure_shards(root: Path, base_sha: str, head_sha: str) -> list[dict]:
    """The attestation shards THIS branch's rounds committed, newest last.

    Two filters, and both are load-bearing:

    - ADDED between base and head. A shard that is already on the base branch
      is some other change's review, and its findings are not this change's to
      re-file.
    - attesting a commit on head's FIRST-PARENT chain past the base. That is
      what refuses the one cheap widening this check would otherwise have:
      merge origin/main into the branch and every shard the platform has ever
      committed becomes "added between base and head" and its whole corpus of
      rule_id+file pairs becomes admissible. Merged-in commits are second
      parents, so first-parent is exactly the change's own line of commits —
      the same discrimination `repair._repair_files` draws, drawn on the
      evidence rather than the diff.

    Raises AttestError when the history cannot be read or a shard in range
    cannot be parsed: a closure round rests entirely on this evidence, and
    evidence warden could not read is not evidence that there is none.
    """
    from . import diffs as diffs_mod
    try:
        # The commits that ADDED each shard, walked first-parent and skipping
        # merges. What identifies a shard as this change's review is where
        # the FILE landed, never the sha the shard records about itself: a
        # rebase, an amend or a squash rewrites every commit on the branch,
        # and a check keyed on the recorded sha then drops every shard sitting
        # in the PR's own diff and reports that the capped rounds attested
        # nothing. `--no-merges` is the other
        # half: a merge diffs against its first parent under `--first-parent`,
        # so without it merging the base branch would present its whole shard
        # corpus as "added here" and admit every claim the platform has ever
        # filed.
        #
        # WHAT `--no-merges` DOES NOT BUY, said because the first version of
        # this comment claimed more than it delivers (round 2, R2-3): it
        # discriminates only while the merge is still a merge COMMIT. Rebase
        # the branch and the merged commits flatten onto the first-parent
        # line, so the shards they carried become "added here" and their
        # claims become admissible; a cherry-pick of another branch's review
        # commit does the same in one step. Both leave an ordinary commit in
        # the PR's own diff that adds another change's attestation shards —
        # visible, and strange to a reader — but nothing git still knows
        # afterwards distinguishes them from this branch's work. The
        # guarantee is "not silently", not "not at all": the same bound the
        # rest of this check carries.
        added = [p for p in diffs_mod.run_git(
            root, "log", "--first-parent", "--no-merges", "--no-renames",
            "--diff-filter=A", "--name-only", "--format=", "-z",
            f"{base_sha}..{head_sha}", "--",
            SHARD_DIR.as_posix()).split("\0") if p.endswith(".json")]
    except diffs_mod.DiffError as e:
        raise AttestError(
            f"the closure round's evidence could not be read from history "
            f"({e}) — a closure round may only re-file what the rounds within "
            "the cap already raised, and warden could not see what that was. "
            "Refused rather than allowed through: this is the check that "
            "makes a closure round terminal") from e
    shards: list[dict] = []
    for path in sorted(dict.fromkeys(added)):
        try:
            raw = diffs_mod.run_git(root, "show", f"{head_sha}:{path}")
        except diffs_mod.DiffError:
            # Added on this line and gone at head. Nothing this branch is
            # standing behind any more, so it vouches for nothing — and it is
            # not a refusal, because a shard that no longer exists cannot be
            # read by the reviewer the refusal would be addressed to.
            continue
        try:
            doc = json.loads(raw)
        except ValueError as e:
            raise AttestError(
                f"{path}: this branch committed it under {SHARD_DIR}, but it "
                f"cannot be read ({e}), so what the rounds within the cap "
                "raised cannot be established and no closure round can be "
                "checked against it") from e
        if not isinstance(doc, dict) or doc.get("source", "attest") != "attest":
            continue          # a gate shard is a checker firing, not a round
        shards.append(doc)
    return shards


CLOSURE_STATUSES = ("fixed", "dismissed-with-reason")


def _claim_of(record: dict) -> tuple | None:
    """A finding's CLAIM, as the identity a re-file is held to, or None when
    the record does not state one warden can read.

    `(rule_id, file, line, severity, finding)` — the whole assertion, not a
    key that indexes it. A review round broke the first version of
    this, which keyed on `(rule_id, file)` alone: this repo's charter lenses
    file `fail-closed` and `enforcement-truth` findings against the change's
    own files in almost every round, so after any ordinary round 1 a closure
    round could raise a brand-new HIGH at a new line with new prose, reuse the
    pair, and be stamped `closure_verification: verified`. A new finding is
    NOT by construction a new pair; it IS by construction a new claim.

    `evidence` is deliberately NOT part of the identity, and that is the one
    field a closure round is supposed to move: the claim is what the capped
    round asserted, the evidence is the proof of what became of it, and a
    closure round exists to record that the repair landed. `status`, `reason`,
    `lens` and `round` move for the same reason — they are the disposition,
    which is the round's whole output. This is the protocol's own word
    "verbatim" (pre-pr-review, on re-filing a carried finding), enforced.
    """
    rule_id, file = record.get("rule_id"), record.get("file")
    finding, severity = record.get("finding"), record.get("severity")
    if not all(isinstance(v, str) for v in (rule_id, file, finding, severity)):
        return None
    line = record.get("line")
    if line is None:
        # ABSENT READS AS 0, because that is what the committed shard says.
        # `line` is optional in the attestation contract, and
        # `memory.build_records` writes `f.get("line", 0)` into the record —
        # so a capped finding filed without one is committed as line 0 while
        # the payload re-filing it VERBATIM (which means omitting it, as the
        # skill instructs) reads None. Comparing those two spellings as
        # different claims refused the re-file BOTH ways at once: as a claim
        # no capped round filed, and as silence on that same claim still open.
        # No payload could then close the branch — the stranded-at-the-cap
        # defect re-created by this feature's own fix.
        line = 0
    elif not isinstance(line, int) or isinstance(line, bool):
        return None
    return (rule_id, file, line, severity, finding)


def _render_claim(claim: tuple) -> str:
    rule_id, file, line, severity, finding = claim
    where = f"{file}:{line}" if line is not None else file
    return f"{severity} {rule_id!r} @ {where!r} — {finding[:90]!r}"


def _closure_evidence(shards: list[dict], cap: int) -> tuple[
        set[int], set[tuple], dict[tuple, int]]:
    """What the capped rounds raised: rounds covered, admissible claims, open ones.

    A finding's identity is `_claim_of`'s tuple — the whole assertion. See
    there for why a `(rule_id, file)` key is not one.

    COVERAGE IS CREDITED TO THE SHARD THAT ATTESTS THE ROUND, which is the
    shard whose HIGHEST roster round is that round — the same number
    `verify_crew` pins equal to the minted round, so on the honest path every
    shard covers exactly its own round. Reading coverage off ANY roster entry
    was the other half of round 1's breakage (F1): a roster spanning lower
    rounds is an explicitly supported shape that warden itself writes, so a
    round-2 shard mentioning round 1 in its roster vouched for a round that
    had committed nothing, and round 1's open HIGH — carried in no `records`
    array anywhere — was neither admissible nor demanded. The round whose
    findings are inconvenient could simply never be ingested.

    ROUND-LABELLED RECORDS ONLY, and the same filter governs both directions.
    A record whose `round` warden cannot read as a whole number at or under the
    cap neither admits a claim nor demands one. It is the fail-closed reading on
    the admitting side, which is the side that matters, and refusing to DEMAND
    from a label nobody can read keeps the two halves reading one field one
    way. Under `--review-dir` every record carries a round, so this only ever
    excludes shards written before that field shipped.

    OPEN means `confirmed` at the HIGHEST round that spoke to the claim: a
    finding raised at round 1 and fixed at round 2 is closed, one re-confirmed
    at round 2 is open. Within that round, ANY `confirmed` record opens it —
    a claim filed twice with two dispositions is open, because the permissive
    reading of a contradiction is how an open HIGH disappears.
    """
    covered: set[int] = set()
    # claim -> {round: [statuses]}, so "the highest round that spoke" is a max
    # over rounds actually recorded, never an assumption about which ran.
    seen: dict[tuple, dict[int, list[str]]] = {}
    for doc in shards:
        rounds = [r.get("round") for r in (doc.get("reviewers") or [])
                  if isinstance(r, dict)]
        rounds = [r for r in rounds
                  if isinstance(r, int) and not isinstance(r, bool)]
        if rounds and 1 <= max(rounds) <= cap:
            covered.add(max(rounds))
        for record in doc.get("records") or doc.get("findings") or []:
            if not isinstance(record, dict):
                continue
            # A round is ALSO covered by a shard that CARRIES its records.
            # Crediting coverage to the roster alone was F1's fix; crediting
            # it to the roster ONLY was that fix's own cost, because a single
            # shard whose roster spans rounds 1 and 2 and carries both rounds'
            # records — a shape `verify_crew` accepts and the suite pins as
            # supported — was told round 1 "attested nothing to this branch"
            # while round 1's records sat in this function's own `admissible`
            # set (round 2, R2-1). Records are the stronger of the two
            # anchors: what F1 closed was a roster LABEL vouching for records
            # that existed nowhere, and a record cannot vouch for itself
            # without being present. A round that found nothing carries no
            # records and is still covered by the roster arm above.
            rnd = record.get("round")
            if not isinstance(rnd, int) or isinstance(rnd, bool) \
                    or not 1 <= rnd <= cap:
                continue
            covered.add(rnd)
            claim = _claim_of(record)
            if claim is None:
                continue
            status = record.get("status")
            seen.setdefault(claim, {}).setdefault(rnd, []).append(
                status if isinstance(status, str) else "")
    admissible = set(seen)
    still_open = {claim: max(rounds) for claim, rounds in seen.items()
                  if "confirmed" in rounds[max(rounds)]}
    return covered, admissible, still_open


def verify_closure(findings: list, *, root: Path | None, base_sha: str,
                   head_sha: str, cap: int) -> str:
    """Refuse a closure round that raises anything, or that drops anything.

    THE REFUSALS, in the order a builder meets them:

    1. No root. The check reads git history; a call that cannot reach it has
       not run the check, and a closure round is exactly the write that must
       not proceed on an unasked question.
    2. A round within the cap has no committed shard on this branch. The
       closure round's whole licence is "the capped rounds already raised
       this", and a round that never attested raised nothing warden can see.
       This is also what stops the cheapest laundering: leave the round whose
       findings are inconvenient uncommitted, and its open findings vanish
       from what the closure round must answer for.
    3. A record whose CLAIM no capped round filed — `_claim_of`'s tuple, the
       whole assertion rather than a key that indexes it. THIS is the check
       the feature stands or falls on, and it is stated as what it enforces
       rather than as a guarantee it cannot make: every record must re-file a
       claim already committed to this branch by a round at or under the cap.
       A builder cannot widen that set from inside the closure round, because
       the shards it is read from are ancestors of the commit being attested
       and sit in the PR's own diff. What it does NOT claim: the platform's
       standing trust boundary is unchanged (`verify_roster`: "a caller that
       writes plausible files itself passes this check"), so a builder who
       commits a fabricated round shard widens what may be re-filed. What is
       removed is every way to widen it SILENTLY.
    4. A record whose `status` is not `fixed` or `dismissed-with-reason`. The
       closure round answers for a finding; it does not re-judge one.
       `refuted` says the capped round was WRONG, which is a judgement, and
       this round holds `find` authority, runs past the cap, and has no judge
       after it — so re-filing every open finding as `refuted` was a one-field
       path to a clean verdict over an open HIGH (round 1, F2). `confirmed`
       is refused for the opposite reason: it cannot reach a clean verdict,
       and a terminal round that ends findings-open strands the branch it
       exists to rescue, so it is refused here with the remedy named rather
       than three checks later with the verdict.
    5. A finding still `confirmed` at the cap that the closure payload does
       not speak to — and speaking to it means re-filing THAT claim, not a
       record that merely shares its rule and file. Without this the closure
       round is a way to launder an open HIGH into a clean head by filing
       nothing at all: an empty payload raises nothing new, and `clean` with
       no confirmed findings passes every other check in `build`. The mirror
       of (3), and it is the half that keeps the round honest rather than
       merely narrow.

    Returns the state stamped into the shard on success. It cannot return a
    failing state: a disproven closure refuses the write, so no shard records
    one — the rule `round_binding` states for a disproven binding.
    """
    if root is None:
        raise AttestError(
            "a closure round cannot be attested without a repository root: "
            "its payload is checked against the shards the rounds within the "
            "cap committed to this branch, and that history is what says the "
            "closure round raises nothing new")
    shards = _closure_shards(root, base_sha, head_sha)
    covered, admissible, still_open = _closure_evidence(shards, cap)
    missing_rounds = [n for n in range(1, cap + 1) if n not in covered]
    if missing_rounds:
        raise AttestError(
            "round(s) " + ", ".join(map(str, missing_rounds)) + f" of the cap "
            f"of {cap} attested nothing to this branch — no committed shard "
            f"under {SHARD_DIR} carries a roster whose highest round is that "
            "round — so what they raised cannot be read and a closure round "
            "has nothing it is entitled to re-file. Attest each round within "
            "the cap and commit its shard before closing: the closure round "
            "re-files that evidence, it does not replace it. If the shards "
            "ARE committed here, check that each was added by a commit on "
            "this branch's own first-parent line: one that arrived by a merge "
            "commit is another change's review and does not count")
    # The payload's paths through the SAME rewriting the document gets a few
    # steps later, before the claims are compared. The shards' records were
    # relativized when they were written, so comparing a raw payload against
    # them reported an absolute path as a finding no capped round raised —
    # a refusal that pointed at the wrong thing entirely (round 1, F5).
    normalised = relativize_strings({"findings": list(findings)},
                                    root)["findings"]
    unfiled, misjudged = [], []
    for index, finding in enumerate(normalised):
        if not isinstance(finding, dict):
            # NOT "the schema refuses the shape first", which is what this
            # comment said and was false: `verify_closure` runs inside `build`
            # well before `_validator.iter_errors(doc)`, so on the closure
            # path a bare string in `findings` reaches here first. Skipped
            # rather than refused because the schema DOES refuse it a few
            # steps later with the better message; what must not happen is the
            # traceback the sibling below produced (round 2, R2-5).
            continue
        claim = _claim_of(finding)
        if claim is None or claim not in admissible:
            rule_id, file = finding.get("rule_id"), finding.get("file")
            unfiled.append(f"#{index} {rule_id!r} @ {file!r} "
                           f"({finding.get('severity')!r}, line "
                           f"{finding.get('line')!r})")
        if finding.get("status") not in CLOSURE_STATUSES:
            misjudged.append(f"#{index} {finding.get('status')!r}")
    if unfiled:
        raise AttestError(
            f"the closure round files {len(unfiled)} record(s) whose claim no "
            f"round within the cap of {cap} filed: {'; '.join(unfiled)}. A "
            "closure round is minted PAST the repair budget and exists only "
            "because it re-files: every record must repeat a claim a capped "
            "round already committed here — same rule_id, file, line, "
            "severity and finding text, with only the evidence and the "
            "disposition moving. A record that differs in any of those is a "
            "new finding however familiar its rule and file look, and a new "
            "finding belongs to a round within the cap or to a tracked item, "
            "never here: a closure round that can raise is the extra review "
            "budget the cap denies")
    if misjudged:
        raise AttestError(
            f"the closure round files {len(misjudged)} record(s) with a "
            f"status it may not use: {'; '.join(misjudged)}. A closure round "
            f"answers for a finding — {' or '.join(CLOSURE_STATUSES)}, the "
            "reason naming a tracked item — and does not re-judge one. "
            "'refuted' says the capped round was wrong, which is a judgement "
            "this round holds no authority to make and no judge follows it to "
            "check; 'confirmed' cannot reach a clean verdict, and a terminal "
            "round that ends findings-open strands the branch it exists to "
            "rescue")
    filed = {claim for claim in (
        _claim_of(f) for f in normalised if isinstance(f, dict))
        if claim is not None}
    dropped = sorted(f"{_render_claim(claim)} (open at round {rnd})"
                     for claim, rnd in still_open.items() if claim not in filed)
    if dropped:
        raise AttestError(
            f"the closure round is silent on {len(dropped)} finding(s) still "
            f"confirmed at the cap: {'; '.join(dropped)}. The closure round "
            "attests the head those findings' repair produced, so it must say "
            "what became of each, by re-filing that claim with its "
            "disposition — a record sharing only its rule and file answers a "
            "different finding. Dropping one would close the branch clean "
            "over an open finding, which is the one thing a terminal round "
            "must never be able to do")
    return CLOSURE_VERIFIED


# The statuses that ANSWER a finding rather than raise one. A record carrying
# one of these is a disposition on something an earlier round already found —
# which is a claim about the record chain, and the claim `verify_chain` holds
# to the identity `_claim_of` defines. `confirmed` is deliberately absent: a
# round-N record may legitimately be a brand-new finding, and warden cannot
# tell a new `confirmed` from a re-confirmed one by its status. So the chain
# is enforced exactly where the payload asserts history, never where it
# asserts a finding.
CARRIED_STATUSES = ("fixed", "dismissed-with-reason", "refuted")

# The state `verify_chain` reaches when every carried record chains. Not a
# schema value: unlike `roster_verification` and `closure_verification` this
# is never stamped into the shard (see `verify_chain`). It reaches the run
# manifest instead, under `chain_verification`, for the same reason
# `carry_forward`'s state reaches it: an unattended run that has no terminal
# still leaves a record of which of the non-refusing states carried the
# write. SAME REASON, NOT THE SAME ROUTE: the within-cap
# chain rule is scoped this way. `warden attest write` calls `carry_forward`
# unconditionally; it re-derives THIS state only where `build` asks the chain
# question — a `--review-dir` payload that is not a closure round — and
# records the literal `no-review-dir` or `closure-round` under THAT SAME KEY
# where it does not. Same key on purpose: those two literals sit beside the
# states `verify_chain` itself returns, so the one field an auditor reads
# never presents a question that was not asked as a state this constant
# could take.
CHAIN_VERIFIED = "chained"


def _chain_round(reviewers: list, review_dir: "Path | None") -> int | None:
    """Which round `verify_chain` holds this payload to: the HIGHER of
    warden's own minted number and the highest round the roster declares.

    THE HIGHER OF THE TWO, and that is the whole point. The first cut read
    the roster alone, and the roster is written by the party being checked:
    labelling every entry `round: 1` drove `round_n <= 1` and the check
    SKIPPED, silently, on the exact restated-claim payload it exists to
    refuse. That defence held in this repo only because `graph.yaml` declares
    a `review` block, so `verify_crew` pins the roster's highest round to the
    minted one and runs first — and the configuration the first cut named as
    its REASON for reading the roster, a consumer with no `review` block, is
    the one where nothing pinned it (round 1, F2).

    `minted_round` is warden's own record, written by `round new` into
    `round.json`, and it is consulted here regardless of whether a crew is
    declared. It is not read ALONE either: it returns None for a directory
    warden did not mint, where the roster's label is the only number there
    is, and `round_binding` has already stamped `unminted` into the shard for
    a reader who needs to know which one carried it.

    The HIGHEST roster round, matching `_closure_evidence`'s reading of which
    round a shard attests: a roster spanning lower rounds is a shape warden
    itself writes, and the round the payload speaks FOR is the last one in it.
    """
    rounds = [_round_of(r.get("round"), who="roster entry")
              for r in reviewers if isinstance(r, dict)]
    stated = [r for r in rounds if r is not None]
    candidates = [r for r in (minted_round(review_dir),
                              max(stated) if stated else None)
                  if r is not None]
    return max(candidates) if candidates else None


def verify_chain(findings: list, reviewers: list, *, root: Path | None,
                 base_sha: str, head_sha: str,
                 review_dir: "Path | None") -> str:
    """Hold a round WITHIN the cap to the claim identity the closure round is.

    THE ASYMMETRY THIS CLOSES (found live closing the closure round's own
    branch). `pre-pr-review` requires a carried finding to
    be re-filed VERBATIM in the next round's payload — same `rule_id`, `file`,
    `line`, `severity` and finding text, with only the lens, round, status,
    reason and evidence moving. `verify_closure` enforces exactly that for the
    terminal closure round. NOTHING enforced it for a round within the cap, so
    a repair round could restate a claim differently and warden would not
    notice.

    Reproduced: a round-2 payload filed the scoped-re-reviewer's
    VERDICT text (`"[F0] Addressed, both halves, ..."`) as each finding's
    text instead of re-filing round 1's claim. Under `_claim_of`'s identity
    those are DIFFERENT claims, so round 1's seven claims were never closed in
    the record — and the defect surfaced only at the closure round, where the
    remedy was to re-file everything the whole chain had left open. That
    refusal was right; discovering the break three rounds late is the bug.

    WHAT IS REFUSED: a record in round N > 1 whose `status` ANSWERS a finding
    (`CARRIED_STATUSES`) and whose claim no round below N committed to this
    branch. Three deliberate narrowings, each of which is the difference
    between a check that bites and one that refuses honest work:

    1. ROUND N > 1 ONLY. Round 1 has nothing before it to chain to, and a
       round-1 record may legitimately arrive `fixed` when the reviewer read a
       diff that already contained the repair. N comes from `_chain_round`,
       never from the roster alone: the roster is written by the party being
       checked, so a payload could otherwise skip this entirely by labelling
       itself round 1.
    2. ANSWERING STATUSES ONLY. A round-2 `confirmed` record is a new finding
       far more often than it is a fold — one measured round 2 raised five
       — and warden cannot tell the two apart from the record. Demanding a
       prior claim for every record would refuse the protocol's own golden
       path. What a `fixed` record asserts, by contrast, is that an earlier
       round found this; that assertion is checkable and this checks it.
    3. THE SAME EVIDENCE THE CLOSURE ROUND READS. `_closure_shards` — shards
       ADDED by commits on head's own first-parent line — so merging the base
       branch cannot widen the admissible set with the platform's whole
       corpus, and a rebase does not strand the branch. Its trust boundary is
       inherited verbatim, including what it does not claim.

    WHY A REFUSAL AND NOT A WARNING. The 979 records already committed are
    never re-read by this: it runs on the PAYLOAD at `attest write`, so no
    historical shard is retroactively illegal. What it costs a builder is
    committing round 1's shard before attesting round 2 — which the protocol
    already requires, one shard per round — and the alternative is discovering
    a broken chain at the closure round, which is the bug being fixed.

    Returns the state reached. STAMPS NOTHING into the shard, like
    `carry_forward` and for its reason: this is a write-path refusal about the
    payload, and `warden/schemas` is a HIGH-tier additive-only contract that a
    fix does not need to touch. `no-root` is the state of a caller that could
    not be asked — `warden attest write` always passes `config.root`, so it is
    reachable only from a library caller, and it is reported rather than
    guessed about.

    `review_dir` is REQUIRED, with no default, and that is deliberate. It
    shipped as `review_dir=None` for one round, which sends `_chain_round`
    back to the roster alone — the exact skip this function's own fix exists
    to close, restored by omitting a keyword. A guard whose defence is opt-in
    has no defence; a caller holding no directory passes `None` and SAYS so
    (round 2, R2-2).
    """
    round_n = _chain_round(reviewers, review_dir)
    if round_n is None or round_n <= 1:
        return "first-round"
    if root is None:
        return "no-root"
    normalised = relativize_strings({"findings": list(findings)},
                                    root)["findings"]
    carried = [(i, f) for i, f in enumerate(normalised)
               if isinstance(f, dict) and f.get("status") in CARRIED_STATUSES]
    if not carried:
        return "nothing-carried"
    shards = _closure_shards(root, base_sha, head_sha)
    # Rounds strictly BELOW this one. A claim this same round filed cannot
    # license this round's disposition on it: that is the record vouching for
    # itself, and it is the shape a shard committed mid-round would produce.
    _covered, admissible, _open = _closure_evidence(shards, round_n - 1)
    unchained = []
    for index, finding in carried:
        claim = _claim_of(finding)
        if claim is None or claim not in admissible:
            unchained.append(
                f"#{index} {finding.get('rule_id')!r} @ "
                f"{finding.get('file')!r} ({finding.get('severity')!r}, line "
                f"{finding.get('line')!r}, {finding.get('status')!r})")
    if unchained:
        raise AttestError(
            f"round {round_n} files {len(unchained)} record(s) disposing of a "
            f"claim no earlier round committed to this branch: "
            f"{'; '.join(unchained)}. A status of "
            f"{' / '.join(CARRIED_STATUSES)} says an EARLIER round found "
            "this, so the record must re-file that round's claim VERBATIM — "
            "same rule_id, file, line, severity and finding text, with only "
            "the lens, round, status, reason and evidence moving. Two things "
            "produce this refusal and the remedy differs: the claim was "
            "restated (re-file the earlier round's text, not this round's "
            "verdict prose), or the earlier round's shard is not committed on "
            f"this branch's first-parent line (`warden memory ingest` it and "
            "commit it — one shard per round). A disposition that chains to "
            "nothing leaves the finding open in the record, which is what the "
            "closure round refuses three rounds later")
    return CHAIN_VERIFIED


def build(payload: dict, *, head_sha: str, base_sha: str,
          rules_version: str, rules_dir: Path,
          pr: str = "", bead: str = "",
          range_patch_ids: tuple[str, ...] = (),
          review_dir: Path | None = None,
          changed: tuple[str, ...] | None = None,
          root: Path | None = None,
          review_crew: dict | None = None) -> dict:
    """Stamp provenance onto the orchestration's payload and validate.

    payload carries reviewers/findings/verdict; provenance fields are stamped
    here so the skill cannot mislabel what was reviewed. rules_dir is required
    (never defaulted): a caller that could omit it would silently reopen the
    free-form rule_id hole.

    Range binding is deliberately NOT here: `range_binding` is
    advisory, and a validator that returns a document is the wrong place for
    a check that does not decide anything.

    Roster verification IS here, and the same sentence says why: it DECIDES.
    It can refuse the document, and the state it reaches is stamped INTO the
    document, so a reader holding only the shard can tell a checked roster
    from an asserted one. `roster_verification` is stamped, never accepted
    from the payload — the `unexpected` guard below rejects a caller trying
    to supply its own verdict on itself.

    Closure verification is here for the same reason again, and is the
    strongest of the three: on the terminal closure round it re-reads the
    capped rounds' committed shards and refuses a payload that raises
    anything they did not, or that drops anything they left open. The state
    it reaches is stamped into the document, so a reader holding only the
    shard can tell a closure round that was proved terminal from one that
    merely said so.

    Round binding is here for exactly the same reason, and is ordered AFTER
    the roster on purpose: both can refuse, the roster is the stronger claim,
    and when both would fire the roster's message is the one worth reading
    first. Stamping it here is what carries `unminted` out of stderr and the
    gitignored run manifest and into the committed shard.

    Base ancestry is stamped on the same argument and decides nothing: it is a
    READING, it never refuses, and it is here because `base_sha` is the one
    provenance field a reader can act on wrongly without being told. A round
    minted against a base that had already moved past this branch records a
    base that is not on it, and `base_sha..head_sha` over this shard's own
    figures then reports another merged change's work as this one's. With
    `root` None there is no repository to ask, so the reading is
    `unavailable` — "could not look", never "looked and found it fine".

    THE LIGHT CREW'S PROOF runs on BOTH paths, and that is a contract change.
    It used to sit inside the `--review-dir` branch with the rest of
    `verify_crew`, so a roster naming only the light crew — the tier that
    exists precisely because its eligibility is recomputed and never asserted
    — was held to no proof at all when the flag was omitted. Omitting an
    argument is not how a tier is earned. A light roster is now refused or
    proved either way; without a round dir there is no minted number, so it is
    refused, naming the mint. Every other roster is untouched: the proof is
    computed only for a roster that IS the light crew, so the ordinary
    no-`--review-dir` write pays nothing and behaves exactly as before.
    """
    unexpected = set(payload) - {"reviewers", "findings", "verdict"}
    if unexpected:
        # repr by the warden-wide rule; see decide.build.
        raise AttestError(f"payload may only carry reviewers/findings/verdict; "
                          f"got extra keys {sorted(map(repr, unexpected))}")
    roster_state, _roster_detail = verify_roster(
        payload.get("reviewers") or [], review_dir)
    # The declared crew, after the roster's outputs are proved real and before
    # the round binding: a roster that is not the round's declared crew names
    # its missing and extra roles, which is the actionable error. Under
    # --review-dir only, where every entry is required to state its round.
    closure_state = None
    # The light round's admission ticket, recomputed here from the two trees
    # rather than read from anything the payload carries. It is computed only
    # when it can matter — a roster that is not the light crew never reaches
    # the proof — so every other write pays nothing.
    #
    # OUTSIDE the `--review-dir` branch, and that placement is the whole of
    # the fix. The tier exists because its eligibility is recomputed and never
    # asserted; while this sat inside the branch, a roster naming the light
    # crew and nothing else was held to NO proof when the flag was left off,
    # so the one check the tier is made of was reachable around by omitting an
    # argument. That is the self-certification the tier exists to refuse.
    is_light = _is_light_roster(payload.get("reviewers") or [],
                                review_crew or {})
    light_proof = (prove_inert(root, base_sha, head_sha) if is_light else None)
    if review_dir is not None:
        # The minted number is read only where a crew is declared: it is the
        # other half of that comparison, and a repo with no `review` block
        # has nothing to compare it with.
        minted = minted_round(review_dir) if review_crew is not None else None
        verify_crew(payload.get("reviewers") or [], review_crew, minted,
                    light_proof=light_proof)
        # The closure round's payload, checked the moment `verify_crew` has
        # settled that this IS the closure round — the roster decides which
        # round a payload belongs to, so the payload rule cannot be applied
        # before the round label has been held to warden's own minted number.
        # Ordered before `round_binding` for `verify_crew`'s reason: a payload
        # that raises something new is the actionable error, not the binding
        # it happens to ride on.
        closure = (review_crew or {}).get("closure")
        if closure is not None and minted is not None \
                and minted > review_crew["cap"]:
            closure_state = verify_closure(
                payload.get("findings") or [], root=root, base_sha=base_sha,
                head_sha=head_sha, cap=review_crew["cap"])
        # The chain rule for a round WITHIN the cap, and only
        # there: on the closure round `verify_closure` has just applied the
        # same claim identity to EVERY record, which is the stricter reading,
        # so running both would re-refuse what it already accepted. Ordered
        # after it for that reason and before `round_binding` for
        # `verify_closure`'s: a payload whose disposition chains to nothing is
        # the actionable error, not the binding it rides on.
        if closure_state is None:
            verify_chain(payload.get("findings") or [],
                         payload.get("reviewers") or [], root=root,
                         base_sha=base_sha, head_sha=head_sha,
                         review_dir=review_dir)
    elif is_light:
        # No round dir, and the roster is the light crew. Everything else a
        # write without `--review-dir` gives up is RECORDED as given up —
        # `roster_verification: unverified-roster`, `round_binding:
        # no-review-dir` — and a reader of the shard can see it. The light
        # tier cannot be recorded that way, because the roster IS the
        # eligibility claim: a shard saying "one auditor reviewed this,
        # unverified" is a change that took the smallest crew the org declares
        # on nobody's word but the builder's. So the same check runs, through
        # the same function, with `minted` None — which is the truth, there is
        # no minted round here — and `_refuse_unproven_light` refuses it and
        # names the mint. The proof is computed above either way, so the
        # refusal a caller meets is about the round and not about the range.
        verify_crew(payload.get("reviewers") or [], review_crew, None,
                    light_proof=light_proof)
    round_state, _round_detail = round_binding(review_dir, head_sha)
    # Third, and last, because it is about the BYTES: after the roster (each
    # output is a real file) and the binding (the round is this head's), a
    # refusal here can only mean the report is another round's.
    carry_forward(payload.get("reviewers") or [], review_dir)
    # Fourth: what each dispatch came back WITH, derived from the outputs the
    # three checks above have just proved real. Stamped onto
    # the roster entries, so the committed shard carries it.
    reviewers = derive_outcomes(payload.get("reviewers") or [], review_dir,
                                changed)
    doc = {
        **({"pr": pr} if pr else {}),
        **({"bead": bead} if bead else {}),
        "head_sha": head_sha,
        "base_sha": base_sha,
        # Recorded, never recomputed later: after a rebase the pre-rebase
        # commits are unreachable, and in CI's fresh clone they do not exist
        # at all. Omitted when empty so a shard that could not
        # compute one is distinguishable from one that computed none.
        **({"range_patch_ids": list(range_patch_ids)} if range_patch_ids else {}),
        "rules_version": rules_version,
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Always stamped, in BOTH states. An absent field means "written
        # before this check existed"; a reader must not be able to confuse
        # that with "checked and found fine".
        "roster_verification": roster_state,
        # Always stamped, in ALL THREE states, for the reason the sibling above
        # is: an absent field means "written before this check existed", and no
        # reader may confuse that with "checked and found minted".
        "round_binding": round_state,
        # The ancestry of the base this shard records, stamped for the reason
        # its two siblings above are, and it took a live false record to get
        # here. `base_binding` takes the base FROM THE ROUND, which is right —
        # but a round minted against an `origin/main` that had already moved
        # past this branch records a base that is not on the branch at all, so
        # anything recomputing figures from `base_sha..head_sha` reads the
        # other merged change's work as this one's. Warden already computed
        # that reading and already printed it; it stopped at stderr and the
        # gitignored run manifest, so the COMMITTED shard carried the
        # misleading base with no committed record of the caveat. It rides
        # into the shard now. Always stamped, in all three states: an absent
        # field means "written before this field shipped", which no reader may
        # confuse with "checked and found on the branch".
        "base_ancestry": (base_ancestry(root, base_sha, head_sha)
                          if root is not None else ANCESTRY_UNAVAILABLE),
        # Stamped ONLY on a closure round, and only in the one state that can
        # be reached — a payload that raises anything new refuses the write,
        # so `verified` is the only value a shard can carry and a non-closure
        # shard carries none. Absent therefore means "not a closure round, or
        # written before the check existed", never "checked and found fine".
        **({"closure_verification": closure_state} if closure_state else {}),
        **payload,
        "reviewers": reviewers,
    }
    errors = sorted(_validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(e.message[:150] for e in errors[:5])
        raise AttestError(f"attestation invalid: {detail}")
    # Before the verdict checks: an unresolvable rule_id is the actionable
    # error to hand back, not the verdict it happens to ride on.
    _check_rule_ids(doc["findings"], rules_dir)
    # Right behind it, for the same reason: a tag the vocabulary rejects is
    # refused here, before the artifact exists, rather than at pre-push after
    # the shard was written.
    _check_tags(doc["findings"], root, rules_dir)
    # The lens/round join: required exactly where the roster
    # check ran — a review dir was offered — and a volunteered attribution is
    # checked either way. After the schema — which is the floor and not the
    # whole reading: verify_attribution refuses a trailing-newline lens and a
    # float round that the schema accepts (see its docstring) — and before the
    # verdict, so a misattributed finding is the error handed back rather than
    # the verdict it rides on.
    verify_attribution(doc["reviewers"], doc["findings"],
                       required=review_dir is not None)
    # Every string, fixed HERE rather than at ingest: this is where the
    # document is validated, the artifact is written from it, and `memory
    # ingest` copies its strings into a content-addressed shard. One point
    # means the artifact and the shard cannot disagree about a path.
    doc = relativize_strings(doc, root)
    # Over the RELATIVIZED document, because that is what `memory ingest`
    # copies into the shard: a restatement folds on the file it names, so the
    # count has to be taken from the strings the shard will carry.
    _check_receipts(doc, root, rules_dir)
    open_findings = [f for f in doc["findings"] if f["status"] == "confirmed"]
    if doc["verdict"] == "clean" and open_findings:
        raise AttestError(f"verdict 'clean' but {len(open_findings)} finding(s) "
                          "still 'confirmed' — fix or dismiss-with-reason first")
    if doc["verdict"] == "findings-open" and not open_findings:
        raise AttestError("verdict 'findings-open' but no confirmed findings")
    return doc


def _render_reviewer(r: dict) -> str:
    """One roster entry, with its structured claim when it carries one.

    The claim is rendered from the FIELDS, never re-parsed out of `agent`:
    `agent` is free text nothing can check, and a renderer that read the
    claim from it would teach writers to put the claim there.
    """
    line = f"{r['role']} ({r['agent']})"
    if r.get("returned") is False:
        # The round is a declared field and a dispatch that fell over is
        # still a dispatch in some round.
        tail = (f", round {r['round']}" if r.get("round") is not None else "")
        tail += (f", outcome {r['outcome']}" if r.get("outcome") else "")
        return line + f" [DID NOT RETURN{tail}]"
    bits = []
    if r.get("returned"):
        bits.append("returned")
    if r.get("findings") is not None:
        bits.append(f"{r['findings']} findings")
    if r.get("output"):
        bits.append(r["output"])
    if r.get("round") is not None:
        bits.append(f"round {r['round']}")
    # Rendered when the shard carries it; an older shard says nothing here
    # rather than a default, because absent is UNKNOWN.
    if r.get("outcome"):
        bits.append(f"outcome {r['outcome']}")
    return line + (f" [{', '.join(bits)}]" if bits else "")


def _render_lenses(findings: list[dict]) -> str:
    """Per-lens finding counts, and how many findings name no lens.

    Rendered whenever any finding carries the attribution. A field that rides
    into the corpus and is never printed is a declared field with zero
    consumers. Findings with no lens are COUNTED beside the
    others, never folded into one: absent means the question was not asked
    of that finding, and `code-reviewer` is not the default answer.
    """
    by_lens: dict[str, int] = {}
    unattributed = 0
    for f in findings:
        if f.get("lens"):
            by_lens[f["lens"]] = by_lens.get(f["lens"], 0) + 1
        else:
            unattributed += 1
    if not by_lens:
        return ""
    line = "  lenses: " + ", ".join(f"{k} {v}" for k, v in sorted(by_lens.items()))
    if unattributed:
        line += f" ({unattributed} unattributed)"
    return line + "\n"


def render_summary(doc: dict) -> str:
    counts: dict[str, int] = {}
    for f in doc["findings"]:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    status = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "no findings"
    reviewers = ", ".join(_render_reviewer(r) for r in doc["reviewers"])
    lenses = _render_lenses(doc["findings"])
    # Rendered on every summary, including `attest show` on an old artifact.
    # A verification state nobody prints is a declared field with zero
    # consumers. Absent is its own line, not silence: a shard written before this check
    # existed was never asked the question, and saying nothing would read as
    # a pass.
    state = doc.get("roster_verification")
    if state == ROSTER_VERIFIED:
        roster = "roster: VERIFIED against per-reviewer outputs"
    elif state == ROSTER_UNVERIFIED:
        roster = ("roster: UNVERIFIED — builder-asserted, no --review-dir was "
                  "given, so nothing checked it")
    else:
        roster = ("roster: NOT RECORDED — written before roster verification "
                  "shipped; this is not the same as verified")
    # Same argument, same shape, one field over. A state
    # that rides into the corpus and is never rendered is legible to a parser
    # and invisible to the person reading `attest show`, which is where an
    # unminted round most needs to be noticed.
    binding = doc.get("round_binding")
    if binding == "bound":
        round_line = "round: BOUND — warden minted it for this head"
    elif binding == "unminted":
        round_line = ("round: UNMINTED — the review dir was not minted by "
                      "warden, so nothing can say which commit its reports "
                      "describe")
    elif binding == "no-review-dir":
        round_line = "round: NO REVIEW DIR — no round identity was offered"
    else:
        round_line = ("round: NOT RECORDED — written before round binding "
                      "shipped; this is not the same as bound")
    # Same argument again, one field over. This one is the reading a person
    # acts on wrongly when it is missing: they run `git diff base..head` over
    # the shard's own two shas and read another merged change's work as this
    # one's.
    ancestry = doc.get("base_ancestry")
    if ancestry == "ancestor":
        base_line = ("base: ON THE BRANCH — base..head over these shas is a "
                     "range that HOLDS; it is not a claim the range is only "
                     "this change, since a branch that merged its base "
                     "branch forward is still on it")
    elif ancestry == "not-ancestor":
        base_line = ("base: NOT ON THE BRANCH — the base branch had moved "
                     "past this branch when the round was minted, so "
                     "base..head over these shas reports more than this "
                     "change; the review judged base...head")
    elif ancestry == ANCESTRY_UNAVAILABLE:
        base_line = ("base: NOT EVALUATED — git could not say whether this "
                     "base is on the branch; this is not a clean reading")
    else:
        base_line = ("base: NOT RECORDED — written before base ancestry "
                     "shipped; this is not the same as on the branch")
    # Rendered only where it applies, and NOT as a "not recorded" line: a
    # closure round is a rare shape, so a line on every ordinary shard saying
    # it is not one would be noise, while its absence here says nothing a
    # reader could misread as a pass.
    closure_line = ("  closure: VERIFIED — minted past the repair cap; every "
                    "record re-files a claim a capped round committed here, "
                    "and none is re-judged\n"
                    if doc.get("closure_verification") == CLOSURE_VERIFIED
                    else "")
    return (f"attestation: {doc['verdict'].upper()} — {status}\n"
            f"  {roster}\n"
            f"  {round_line}\n"
            f"  {base_line}\n"
            f"{closure_line}"
            f"  reviewers: {reviewers}\n"
            f"{lenses}"
            f"  head {doc['head_sha'][:12]} · rules_version {doc['rules_version']} "
            f"· {doc['reviewed_at']}\n")


# --------------------------------------------------------------------------
# CI enforcement: does this PR carry an attestation?
# --------------------------------------------------------------------------

# The COMMITTED record, not the run dir. `warden attest write` lands in
# .warden/out/<ts>-attest/, which is gitignored and lives on one runner's
# filesystem; this repo's CI runs verify and review in different jobs, so an
# artifact there is not even readable from the job that would check it.
# `warden memory ingest` turns it into a shard here, and
# the shard is what the review corpus is made of — so checking the shard
# checks the thing this requirement exists to grow. An attestation that never
# became a shard never fed a single recall.
#
# A shard with NO records satisfies this check, deliberately.
# The question here is "was this reviewed", not "did the review find
# something": a clean round is the outcome the whole gate exists to produce,
# and requiring records would answer NO ATTESTATION for precisely the PRs
# that passed review. The shard is evidence
# of an EVENT; its records are evidence of findings. Anyone tempted to
# "optimise away" an empty shard is deleting the clean-review attestation —
# tests/test_memory_clean_review.py goes red on that.
SHARD_DIR = Path(".warden") / "memory" / "attest"

# THE EVIDENCE CARVE-OUT, and the whole safety margin of the coverage rule
# below. It is an ALLOWLIST: a commit landing after the round that attested
# its line of history is refused unless everything it touches sits under one
# of these prefixes.
#
# One prefix, and it is `SHARD_DIR` itself, because exactly one commit after a
# round is FORCED BY THE PROTOCOL: `attest write` stamps the head it reviewed,
# `memory ingest` writes the shard from that attestation, and committing the
# shard MOVES head. That commit's whole diff is the round's own evidence, and
# it lands here by construction. Nothing else is forced, so nothing else is
# admitted.
#
# MEASURED, at both ends, over the 39 merged PRs in `pr/230..pr/269` that
# still have refs:
#
#   `.warden/memory/attest/`  -> 16 refused   (this one)
#   `.warden/memory/`         -> 14 refused
#
# The two PRs the wider prefix lets through are #243 and #266, and both ride
# free on a `.warden/memory/decide/` shard committed after the last round. A
# decision shard is a claim about what was RULED — reviewable content, and the
# most consequential prose this repo commits. Admitting it would mean the one
# artifact that records a founder-level ruling is the one artifact no reviewer
# has to have seen. `warden/autonomy.py` draws this same line for the cage's
# carve-out (PR #273): `.warden/memory/attest/` admitted by
# shape, `.warden/memory/decide/` "out by decision, not omission". Two
# enforcers, one boundary.
EVIDENCE_CARVE_OUT: tuple[str, ...] = (SHARD_DIR.as_posix() + "/",)


def in_evidence_carve_out(path: str) -> bool:
    """Whether `path` is evidence a round leaves behind rather than content.

    Prefix on the POSIX spelling, matching a directory boundary: the trailing
    slash is what keeps a sibling named `.warden/memory/attestations-notes.md`
    out of a carve-out written for `.warden/memory/attest/`.
    """
    return any(path.startswith(prefix) for prefix in EVIDENCE_CARVE_OUT)


def _commit_paths(root: Path, sha: str) -> list[str]:
    """Every path a commit changes against its FIRST parent.

    First-parent, not the combined diff, and that choice is what makes the
    merge case bite. `git show --name-only` on a merge prints the COMBINED
    diff, which lists only the paths that differed from *every* parent — on a
    clean merge of the base branch, nothing at all. A merge is how most of the
    refused history smuggles content past a round (11 of the 16 measured PRs),
    so reading it as an empty change would carve out the single largest hole
    this check exists to close. Against the first parent the merge's change is
    what it brought ONTO this branch, which is exactly the content no round
    saw — and an evil merge's own edits are in that diff too.

    Raises AttestError when git cannot answer. A commit whose contents cannot
    be read is not a commit that changed nothing: the exit-code contract keeps
    "the gate did not run" (2) apart from "it ran and found nothing" (1), and
    guessing here would file the wrong one.
    """
    from . import diffs as diffs_mod
    try:
        raw = diffs_mod.run_git(
            root, "show", "--format=", "--name-only", "-m", "--first-parent",
            "--no-renames", "-z", sha)
    except diffs_mod.DiffError as e:
        raise AttestError(
            f"cannot read the paths commit {sha[:12]} changes ({e}) — that "
            "commit lands after the review covering its line of history, so "
            "whether it ships unreviewed content is the question this check "
            "exists to answer. The check did NOT run") from e
    return sorted({p for p in raw.split("\0") if p})


def _covered_commits(root: Path, base: str, tips: list[str]) -> set[str]:
    """Every commit in `base..head` that an attested tip descends from, or is.

    ANCESTRY, never list order and never a count. An attestation names one
    commit; what it can honestly be said to cover is that commit and its
    history — the tree the reviewers read. `git rev-list base..<tip>` is that
    set, and it contains the tip itself, so a tip covers itself.

    A merged range has one tip per line of history (`_latest_attested`), and
    the union is right there too: a commit reviewed on either line was
    reviewed.
    """
    from . import diffs as diffs_mod
    covered: set[str] = set()
    for tip in tips:
        try:
            raw = diffs_mod.run_git(root, "rev-list", f"{base}..{tip}")
        except diffs_mod.DiffError as e:
            raise AttestError(
                f"cannot establish what the attestation of {tip[:12]} covers "
                f"({e}) — coverage is read from ancestry in {base}..{tip}, "
                "and a range git cannot walk is not one this check may assume "
                "is fully covered. The check did NOT run") from e
        covered.update(c.strip() for c in raw.splitlines() if c.strip())
    return covered


def coverage_rows(root: Path, uncovered: list[str]) -> list[dict]:
    """Each uncovered commit with the paths it changes, and which of them the
    evidence carve-out does not excuse.

    A commit with an EMPTY first-parent diff — an empty commit, or a merge of
    something already an ancestor — has nothing outside the carve-out because
    it has nothing at all, and a commit that ships no content ships no
    unreviewed content. Stated rather than special-cased.
    """
    rows = []
    for sha in uncovered:
        paths = _commit_paths(root, sha)
        rows.append({"sha": sha, "paths": paths,
                     "outside": [p for p in paths
                                 if not in_evidence_carve_out(p)]})
    return rows


def _accept_shard(doc: object) -> str | None:
    """The attestation sha a DIAGNOSTIC candidate carries, or None.

    Used only by `_uncommitted_shards`, where a malformed on-disk file
    warrants no claim at all. The verdict path (`attested_shas`) keeps its own
    field handling: a shard MISSING its `sha` key is UNREADABLE (KeyError —
    refused, not guessed about), while a present-but-null-or-empty `sha` is
    skipped as not an attestation. Both readers treat the second spelling as
    a non-attestation; making it unreadable here alone would put the two
    readers of one store in disagreement.

    This does not mirror `memory.review_events` field for field. What IS
    shared is the ENVELOPE definition — `attested_shas` calls
    `validate_shard_envelope`, which is the same validator `memory.read_shard`
    calls — and the rest of this function's handling is its own, stated above
    rather than asserted by reference.
    """
    if not isinstance(doc, dict):
        return None
    sha = doc.get("sha")
    if doc.get("source", "attest") != "attest":
        return None
    if not isinstance(sha, str) or not sha:
        return None
    return sha


def range_patch_ids(root: Path, base: str, head: str
                    ) -> tuple[tuple[str, str], ...] | None:
    """The stable patch-ids of every commit in `base..head`, sorted.

    A patch-id is a hash of the DIFF a commit introduces, ignoring its sha,
    parents, message, author and timestamps — so a content-preserving rebase
    yields the identical set. `--stable` is required: the default algorithm is explicitly
    documented as unstable across git versions, which would make an attestation
    verifiable only by the git that wrote it.

    RECORDED at `attest write`, never recomputed at check time. After a rebase
    the pre-rebase commits are unreachable, and in CI's fresh clone they do not
    exist at all — so the only moment this can be computed for the reviewed
    range is while that range is still checked out.

    Returns a `(patch_id, commit_sha)` pair per commit, `()` for a genuinely
    EMPTY range, and **None when the computation could not run** — an
    unresolvable ref, no git, a non-zero exit. Three states, kept apart
    because "could not look" is not an answer: collapsing it into the empty
    case would make `check` report NO ATTESTATION with a remedy ("run the
    review again") for a range whose fallback join failed to execute.
    """
    from . import diffs as diffs_mod
    try:
        # PER-COMMIT diffs, never the aggregate `base..head` tree diff. The
        # aggregate is a diff against the base TIP, so it changes the moment
        # main moves — which is precisely the situation this exists to
        # survive. Each commit's own diff does not care where the branch is
        # rooted.
        diff = diffs_mod.run_git(root, "log", "-p", "--no-color", "--no-merges",
                                 f"{base}..{head}")
    except diffs_mod.DiffError:
        return None
    if not diff.strip():
        return ()
    try:
        proc = subprocess.run(["git", "patch-id", "--stable"], cwd=root,
                              input=diff, capture_output=True, text=True,
                              env=diffs_mod.git_env())
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    # `git patch-id` emits "<patch-id> <commit-id>"; the commit half is what
    # lets `check` say how many commits in a matched range the attestation did
    # NOT cover, instead of asserting there are none.
    pairs = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            pairs.append((parts[0], parts[1]))
    return tuple(sorted(pairs))


def attested_shas(root: Path, rev: str
                  ) -> tuple[dict[str, str], dict[str, str],
                             dict[tuple[str, ...], str], dict[str, str]]:
    """Three indexes over the attestation shards COMMITTED at `rev`, plus the
    shards that could not be read, each mapped to the CAUSE.

    The second element maps each unreadable path to its CAUSE: the one error
    that can block a consumer's PR has to name the defect and not only the
    file. `check_range` puts a list of paths in its `unreadable` doc key and
    the causes in `unreadable_causes` beside it; a LIBRARY caller binds to
    this signature.

    `head_sha -> path` is the primary join, LAST-wins: when two shards name
    one commit the later attestation supersedes the earlier, and the path
    this check names must be the same shard whose `verdict` it reads, or an
    evidence line would point at a file that contradicts it.
    `patch-id set -> path` is the fallback a content-preserving rebase needs:
    the rebase rewrites every sha, so the sha index misses entirely while the
    recorded patch-ids still match. Shards that record no patch-ids appear in
    the sha index only.

    `sha -> verdict` is what `check_range` needs to ask whether the review
    covering a branch CLOSED; "" means the shard records no
    verdict, which is a state and never a guess. It is last-wins for the same
    reason and in the same step as the sha index, so the two never disagree
    about which shard is speaking.

    Enumerated from git's tree and read with `git show`, never from the
    filesystem: CI clones the repo and sees only committed files, so a check
    whose output says "committed" must read exactly what a clone would — a
    filesystem glob would let an untracked shard print a local PASS one push
    before a CI failure. `rev` is required on purpose: a defaulted
    filesystem fallback would reopen that. Tree paths are kept in full, so a
    shard in a subdirectory resolves instead of being flattened to a
    basename that exists nowhere.

    Unreadable shards are RETURNED, never swallowed: "I could not read the
    store" and "the store holds no attestation" are different states, and
    only the caller knows whether a match elsewhere makes the difference
    moot (see check_range).

    Raises AttestError when the tree itself cannot be listed — that is
    "the check did not run", never "no attestation".
    """
    from . import diffs as diffs_mod
    found: dict[str, str] = {}
    by_patch: dict[tuple[str, ...], str] = {}
    verdicts: dict[str, str] = {}
    # path -> the CAUSE, not a bare path list. `validate_shard_envelope`
    # raises messages that NAME the defect ("record 0 carries a `status` that
    # is not a string (['fixed'])"), and the one error that can block a
    # consumer's PR must carry that cause, not only the file. The doc below still carries `unreadable` as a list of paths, so nothing a
    # consumer reads changes shape — the causes go into the two messages a
    # human actually reads.
    unreadable: dict[str, str] = {}
    try:
        raw = diffs_mod.run_git(root, "ls-tree", "-r", "--name-only", "-z",
                                rev, "--", SHARD_DIR.as_posix())
    except diffs_mod.DiffError as e:
        raise AttestError(
            f"cannot list attestation shards at {rev}: {e}. The check did "
            "NOT run — it is not reporting a missing attestation") from e
    paths = sorted(p for p in raw.split("\0") if p.endswith(".json"))
    from .memory import validate_shard_envelope
    for path in paths:
        try:
            doc = json.loads(diffs_mod.run_git(root, "show", f"{rev}:{path}"))
            # The SHARED definition, not a hand-roll: every reader that
            # COUNTS the store uses `memory.read_shard` /
            # `validate_shard_envelope`. Checking only for a usable `sha` and
            # a `source` would let a shard with malformed records, a
            # non-string `status`, or no `records` key satisfy the BLOCKING
            # attestation gate and be refused one step later by
            # `memory ingest`. The shards are read with
            # `git show` rather than from disk, so `read_shard` itself (which
            # takes a path) is not the caller — the envelope validator behind
            # it is, and that is the definition both halves share.
            validate_shard_envelope(Path(path).name, doc)
            sha = doc["sha"]
            source = doc.get("source", "attest")
        except KeyError as e:
            # A bare KeyError stringifies to the KEY — `'sha'`, four characters
            # — which tells the reader what was received (nothing) and never
            # what was REQUIRED, so this arm says the sentence itself. A
            # missing `sha` is the shape
            # `test_r1f6_review_events_agrees_with_attested_shas_on_a_sha_less
            # _shard` pins, so it is not hypothetical.
            unreadable[path] = (
                f"names no {e} — an attestation shard must carry the commit "
                "it attests")
            continue
        except (diffs_mod.DiffError, ValueError, TypeError) as e:
            # ValueError covers JSONDecodeError AND the validator's refusals
            # AND UnicodeDecodeError.
            unreadable[path] = str(e) or type(e).__name__
            continue
        # A gate shard is a deterministic checker firing — a fact, not a
        # judgment. It must never stand in for the orchestrated review, even
        # if one is filed into this directory by mistake.
        if source != "attest" or not isinstance(sha, str) or not sha:
            continue
        # Both last-wins, assigned together: `paths` is sorted and a shard's
        # name carries its mint timestamp, so the last one naming a commit is
        # its latest attestation — and the path and the verdict below can
        # never come from two different shards.
        found[sha] = path
        verdict = doc.get("verdict")
        verdicts[sha] = verdict if isinstance(verdict, str) else ""
        ids = doc.get("range_patch_ids")
        if isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids):
            by_patch.setdefault(tuple(sorted(ids)), path)
    return found, unreadable, by_patch, verdicts


def _uncommitted_shards(root: Path, rev: str, commits: list[str]) -> list[dict]:
    """Shards on DISK that name a commit in range but are invisible at `rev`
    — the exact state of a builder who ran the review and forgot the commit
    step. Each carries its own cause: `untracked` (never `git add`ed) or
    `unreachable` (tracked, but `rev` does not contain the ingest commit —
    either it is not committed yet, or the checked head predates it; both
    are named, because "staged" alone would be a wrong cause with a no-op
    remedy).

    "Visible at rev" is the FULL ls-tree listing, not the deduplicated
    sha->shard map — `attested_shas` keeps one path per sha, and judging
    visibility off that map would call a second committed shard for the same
    sha "not at head" with a no-op remedy.
    """
    from . import diffs as diffs_mod
    shard_dir = root / SHARD_DIR
    if not shard_dir.is_dir():
        return []
    committed = set(diffs_mod.run_git(
        root, "ls-tree", "-r", "--name-only", "-z",
        rev, "--", SHARD_DIR.as_posix()).split("\0"))
    tracked = set(diffs_mod.run_git(
        root, "ls-files", "-z", "--", SHARD_DIR.as_posix()).split("\0"))
    in_range = set(commits)
    out: list[dict] = []
    for path in sorted(shard_dir.rglob("*.json")):
        rel = path.relative_to(root).as_posix()
        if rel in committed:
            continue
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            # ValueError, not `json.JSONDecodeError`: a shard that is not
            # UTF-8 raises UnicodeDecodeError straight out of `read_text`,
            # which is a ValueError but not a JSONDecodeError.
            continue                     # corrupt AND uncommitted: no claim
        sha = _accept_shard(doc)
        if sha is None or sha not in in_range:
            continue
        out.append({"path": rel, "sha": sha,
                    "state": "untracked" if rel not in tracked
                    else "unreachable"})
    return out


def _latest_attested(root: Path, base: str, matches: list[dict]) -> list[dict]:
    """The matches no other match descends from — the last attestation on
    each line of history the range contains.

    NOT `matches[0]`. `git rev-list --topo-order` linearises a PARTIAL order:
    on a range containing a merge it emits one parent's whole line before the
    other's, so the first element can be a sibling line's shard while the
    branch's own is never read: a branch whose own last shard says
    `findings-open`, merged with a line carrying a `clean` one, would print
    PASS. Ancestry is the real relation, so ancestry is what gets asked.

    On a linear range this is one match, the newest. On a merged range it is
    one per line, and the verdict rule binds EVERY one: a line whose review
    never closed rode its content into this range whichever side it came
    from. The remedy collapses them — re-reviewing the merged head attests a
    commit that descends from all of them, leaving a single tip.

    Reachability is read from `base..<sha>`, which contains the commit itself,
    so X is an ancestor of Y exactly when X is in Y's set and X is not Y.
    Raises AttestError when git cannot answer: an ordering the check cannot
    establish is not one it may assume.
    """
    from . import diffs as diffs_mod
    reach: dict[str, set[str]] = {}
    for m in matches:
        try:
            raw = diffs_mod.run_git(root, "rev-list", f"{base}..{m['sha']}")
        except diffs_mod.DiffError as e:
            raise AttestError(
                f"cannot order the attested commits in {base}..{m['sha']}: "
                f"{e}. The check did NOT run — it is not reporting a missing "
                "attestation") from e
        reach[m["sha"]] = {c.strip() for c in raw.splitlines() if c.strip()}
    return [m for m in matches
            if not any(sha != m["sha"] and m["sha"] in seen
                       for sha, seen in reach.items())]


def check_range(root: Path, *, base: str, head: str) -> dict:
    """Assert a committed attestation names a commit in `base..head`, that
    every TIP attestation of that range closed CLEAN, and that no commit
    those tips do not cover ships anything but a round's own evidence.

    Three questions, and the third is the one membership cannot answer. A
    shard joins to the range by membership — "a commit in this range carries
    an attestation" — which settles that a review RAN here and says nothing
    about what landed afterwards. Before the coverage rule that was the whole
    test, so a commit pushed after a clean round shipped unreviewed at exit 0.
    The coverage rule (below, at the point it is applied) closes it: a commit
    no tip attestation is an ancestor-or-self of must touch nothing outside
    `EVIDENCE_CARVE_OUT`, and the remedy is to re-review this head.

    Membership alone is satisfiable by a branch ABANDONED after round 1: one
    attestation covers one round, so an early round's `verdict:
    findings-open` shard is committed BY DESIGN, and a check that did not
    read the field would pass with review unfinished and findings unfixed.
    The rule enforced
    here is the review protocol's own, "one shard per round, the last of them
    CLEAN", so it binds the TIPS and only the tips — the matched shards no
    other matched shard descends from, which `_latest_attested` computes and
    which a merged range has one of per line of history. Requiring every
    shard to be clean would refuse the honest multi-round branch the protocol
    itself produces, which is the worse defect. A shard
    that records no verdict predates the field; it is reported as unrecorded
    and does not block, because old evidence does not become illegal and
    inferring a verdict would fabricate committed evidence.

    Two things this deliberately is NOT:

    1. NOT `shard.sha == head`. Strict equality can never pass: the review
       attests HEAD, `memory ingest` then writes the shard, and committing
       that shard MOVES head. Every shard in this repo names the commit
       immediately before its own ingest commit. So the binding is
       membership in the PR's own commit range — the attestation covers a
       commit on this branch.
    2. NOT `<pull_request.base.sha>..HEAD`. `base` must be the base BRANCH
       TIP (`origin/main`) so that commits already on main — and the shards
       attesting them — fall outside the range. Anchoring on the base sha
       recorded at PR-open time would let a branch that merges main inherit
       main's attestations and pass having reviewed nothing.

    `head` is the caller's to resolve, for the same reason `verify` records
    `pr_head_sha`: on a pull_request event `git rev-parse HEAD` is the merge
    commit actions/checkout built, not the commit CI reports about.

    Raises AttestError when the answer cannot be determined — an unreadable
    store is not an absent attestation, and the exit-code contract keeps the
    two apart (2 = the gate did not run; 1 = it ran and found nothing).
    """
    from . import diffs as diffs_mod
    try:
        # `--topo-order` so a commit never precedes its own descendants and
        # `matches`, which inherits this order, reads newest-first. That is
        # PRESENTATION only: the verdict rule takes its tips from
        # `_latest_attested`, which asks ancestry directly, because a
        # linearisation of a partial order cannot answer it.
        # Membership, the count and `_uncommitted_shards` are all
        # order-independent, so nothing else changes either way.
        raw = diffs_mod.run_git(root, "rev-list", "--topo-order",
                                f"{base}..{head}")
    except diffs_mod.DiffError as e:
        raise AttestError(
            f"cannot resolve the commit range {base}..{head}: {e}. The check "
            "did NOT run — it is not reporting a missing attestation") from e
    commits = [line.strip() for line in raw.splitlines() if line.strip()]

    found, unreadable, by_patch, verdicts = attested_shas(root, head)
    matches = [{"sha": sha, "shard": found[sha], "matched_by": "sha",
                "verdict": verdicts.get(sha, "")}
               for sha in commits if sha in found]
    # THE REBASE FALLBACK, tried only when the sha join found nothing. Branch
    # protection requires a PR be up to date with main;
    # `gh pr update-branch --rebase` rewrites every sha in the range, so a
    # valid attestation of a byte-identical tree would stop matching and the
    # required gate would report NO ATTESTATION.
    #
    # The substitution is RECORDED, never silent: `matched_by: patch-id` rides
    # the result and `render_check` says so, naming the sha the shard actually
    # attested. A patch-id match proves the same CHANGE was reviewed, not that
    # this COMMIT was — a rebase onto a moved main can change what a patch
    # means — so a reader is told which join carried the pass rather than
    # having the binding quietly loosened underneath them.
    fallback_unavailable = ""
    uncovered_shas: list[str] = []
    if not matches:
        # MERGES DISQUALIFY THE FALLBACK ENTIRELY. Patch-ids come from
        # `git log -p --no-merges`, so a merge commit's own contribution —
        # conflict resolution, or an evil merge — enters neither the subset
        # test nor the uncovered count. A branch that cherry-picks one
        # reviewed commit and then merges in a backdoor would pass at exit 0
        # while the gate printed "Every commit in this range was covered by
        # that review". That range fails closed without the fallback, so the
        # fallback would turn a refusal into an affirmative all-clear.
        #
        # What this does and does not buy. It restores the COUNT's completeness:
        # with merges out, every commit in a matched range is one this join
        # can see, so `uncovered` is exact and the report is true. It does NOT
        # make the pass stricter — an unreviewed commit sitting beside a
        # reviewed one still passes, here and on the sha path, because both
        # ask membership ("a commit in this range carries an attestation").
        # `git merge --squash` smuggles the same content linearly and passes
        # too. The merge case is worse only because the gate cannot SEE the
        # smuggled content and would say so affirmatively.
        # Refusing costs nothing real: `gh pr update-branch --rebase` produces
        # a LINEAR range by construction.
        try:
            merges = [c for c in diffs_mod.run_git(
                root, "rev-list", "--merges", f"{base}..{head}").split() if c]
        except diffs_mod.DiffError:
            merges = None
        if merges is None:
            fallback_unavailable = (
                "the patch-id fallback did not run — git could not list the "
                f"merge commits in {base}..{head}, and a range whose merges "
                "cannot be enumerated cannot be content-matched safely")
            pairs = ()
        elif merges:
            fallback_unavailable = (
                f"the patch-id fallback does not run on a range containing "
                f"merge commits ({len(merges)} here). A merge's own "
                "contribution is invisible to a per-commit content match, so "
                "matching one could pass content no review ever saw. Re-run "
                "the pre-PR review on this head, or rebase the branch linearly")
            pairs = ()
        else:
            pairs = range_patch_ids(root, base, head)
        if pairs is None:
            # NOT "no attestation": the content join could not run at all, and
            # saying otherwise sends the reader to re-run a review that already
            # happened.
            fallback_unavailable = (
                "the patch-id fallback could not run — git could not produce "
                f"per-commit diffs for {base}..{head}. This is not a statement "
                "about whether a review happened")
            pairs = ()
        current = {pid for pid, _ in pairs}
        # SUBSET, not equality. The attestation is written before the shard
        # that records it is committed, so the range always grows by at least
        # that ingest commit — an equality match could never fire. What this
        # asserts is that every commit the attestation covered is still present
        # in this range BY CONTENT. It does not assert that nothing was added
        # afterwards, and neither does the sha join, whose contract is likewise
        # membership: "a commit in this range carries an attestation".
        hit = next(((ids, path) for ids, path in sorted(by_patch.items())
                    if ids and set(ids) <= current), None) if current else None
        if hit:
            ids, shard = hit
            try:
                shard_doc = json.loads(
                    diffs_mod.run_git(root, "show", f"{head}:{shard}"))
                attested_sha = shard_doc["sha"]
                # This shard's OWN verdict, not `verdicts[attested_sha]`: the
                # patch-id join matched THIS file, and a same-sha sibling is
                # a different attestation.
                shard_verdict = shard_doc.get("verdict")
            except (diffs_mod.DiffError, json.JSONDecodeError, KeyError, TypeError):
                attested_sha = ""
                shard_verdict = None
            # How many commits in this range the attestation did NOT cover.
            # "Same change, different commits" is false whenever anything was
            # added after the review; counting them is what makes the record
            # the compensating control this design claims it is.
            # The COMMITS, not merely how many. The count is what the pass
            # line reports; the shas are what the coverage rule below reads
            # paths from, and on this path they are the only handle on
            # coverage there is — the shard's own attested commit is not in
            # this range, so ancestry cannot be asked of it.
            uncovered_shas = [c for pid, c in pairs if pid not in set(ids)]
            uncovered = len(uncovered_shas)
            # `sha` is the shard's OWN attested commit, never `head`. On the
            # sha path that field names a commit genuinely carrying an
            # attestation; putting HEAD there would claim a commit was
            # attested when it was not.
            matches = [{"sha": attested_sha, "shard": shard,
                        "matched_by": "patch-id",
                        "verdict": (shard_verdict
                                    if isinstance(shard_verdict, str) else ""),
                        "uncovered_commits": uncovered}]
    if not matches and unreadable:
        named = "; ".join(f"{p} ({why})"
                          for p, why in list(unreadable.items())[:3])
        raise AttestError(
            f"{len(unreadable)} attestation shard(s) under {SHARD_DIR} could "
            f"not be read ({named}) and no other shard "
            "matched — this check cannot tell 'no attestation' from 'cannot "
            "read the store', so it refuses to guess")
    uncommitted = _uncommitted_shards(root, head, commits)
    # THE VERDICT RULE. It binds the last attestation on every line of
    # history in the range (one, on the linear range this is nearly always),
    # never every shard: an earlier round's open verdict is what per-round
    # attestation is supposed to leave behind. A single match needs no
    # ordering, which is also what keeps the patch-id path — whose match
    # names a commit OUTSIDE this range — out of the ancestry question.
    latest = (matches if len(matches) < 2
              else _latest_attested(root, base, matches))
    open_verdict = next((m for m in latest
                         if m["verdict"] and m["verdict"] != CLEAN_VERDICT),
                        None)
    # THE COVERAGE RULE. Membership is the JOIN — it settles
    # that a review ran on this branch. It was also, until this rule, the
    # whole test, and MEMBERSHIP IS NOT COVERAGE: any commit landing after the
    # last attested one satisfied it and shipped unreviewed, with the gate
    # printing PASS. Measured on a real branch: a clean round-2 shard, then a
    # commit adding `backdoor()` that nobody reviewed, reported as
    # "PASS — 1 of 3 commit(s)".
    #
    # What is asked instead: which commits in the range is NO tip attestation
    # an ancestor-or-self of, and does any of them touch content. Ancestry is
    # the relation because that is what a review can honestly be said to have
    # seen; the carve-out is what keeps the honest golden path passing, since
    # the shard commit necessarily lands after the round it records.
    #
    # Only on the SHA join. The patch-id fallback's match names a commit
    # OUTSIDE this range — a pre-rebase sha — so `base..<sha>` is not a
    # question about this range at all; its uncovered set comes from the
    # patch-id subtraction the fallback already computes, which is exact
    # because that path refuses any range containing a merge.
    if matches and all(m.get("matched_by", "sha") == "sha" for m in matches):
        covered = _covered_commits(root, base, [m["sha"] for m in latest])
        uncovered_shas = [c for c in commits if c not in covered]
    uncovered = coverage_rows(root, uncovered_shas) if matches else []
    coverage_gap = [row for row in uncovered if row["outside"]]
    return {"base": base, "head": head, "commits": commits,
            "attested": matches, "unreadable": list(unreadable),
            "unreadable_causes": dict(unreadable),
            "uncommitted": uncommitted, "shard_count": len(found),
            "latest": latest, "open_verdict": open_verdict,
            "uncovered": uncovered, "coverage_gap": coverage_gap,
            "fallback_unavailable": fallback_unavailable}


def render_check(doc: dict) -> str:
    n = len(doc["commits"])
    head = doc["head"][:12]
    latest = doc.get("latest") or []
    open_verdict = doc.get("open_verdict")
    if doc["attested"] and open_verdict:
        # A distinct headline from NO ATTESTATION, because the state is
        # distinct and the remedies do not overlap: a review HAPPENED here
        # and did not close.
        which = ("the newest attestation" if len(latest) < 2 else
                 f"one of the {len(latest)} tip attestations")
        lines = [
            f"attest check: FINDINGS OPEN — {which} in "
            f"{doc['base']}..{head} carries verdict "
            f"'{open_verdict['verdict']}', not '{CLEAN_VERDICT}'",
            f"  {(open_verdict['sha'] or '?')[:12]} -> "
            f"{open_verdict['shard']}  [verdict: {open_verdict['verdict']}]",
            "  One attestation covers one round, so an EARLIER round's "
            "findings-open shard is expected; the LAST round on each line of "
            "history this range carries is what must close clean.",
            "  Fix or adjudicate the open findings, re-review the resulting "
            "head, and commit that round's shard.",
        ]
        if len(latest) > 1:
            lines.append(
                f"  This range joins {len(latest)} lines of history, each "
                "with its own last attestation. Re-reviewing the merged head "
                "attests a commit descending from all of them, which leaves "
                "one.")
    elif doc["attested"] and doc.get("coverage_gap"):
        # A THIRD headline, distinct from both siblings because the state and
        # the remedy are distinct: a review happened here, it closed clean,
        # and then content landed on top of it. Nothing is fixed by
        # adjudicating a finding (FINDINGS OPEN) or by committing a shard
        # (NO ATTESTATION) — the head itself has to be reviewed again.
        gap = doc["coverage_gap"]
        lines = [
            f"attest check: UNREVIEWED COMMITS — {len(gap)} commit(s) in "
            f"{doc['base']}..{head} land after the attestation covering their "
            f"line of history and change content no round has read"]
        for row in gap[:5]:
            shown = ", ".join(row["outside"][:4])
            rest = (f" (+{len(row['outside']) - 4} more)"
                    if len(row["outside"]) > 4 else "")
            lines.append(f"  {row['sha'][:12]} -> {shown}{rest}")
        if len(gap) > 5:
            lines.append(f"  ... and {len(gap) - 5} more such commit(s)")
        lines.extend([
            "  Membership is not coverage: an attestation covers the commit "
            "it names and that commit's ancestors, never whatever landed "
            "after it.",
            f"  The ONLY commit admitted after a round is the one carrying "
            f"that round's own evidence under {SHARD_DIR.as_posix()}/ — a "
            "decision shard, a doc, a test fixture or a merge of the base "
            "branch is content, and content is reviewed.",
            f"  REMEDY: re-review this head. Run the pre-pr-review skill on "
            f"{head}, then `warden memory ingest`, and COMMIT the shard it "
            "writes.",
        ])
        for m in doc["attested"]:
            lines.append(f"  covered through {m['sha'][:12]} -> {m['shard']}"
                         if m.get("matched_by", "sha") == "sha" else
                         f"  matched by PATCH-ID: {(m['sha'] or '?')[:12]} -> "
                         f"{m['shard']}")
    elif doc["attested"]:
        by_patch_only = all(m.get("matched_by") == "patch-id"
                            for m in doc["attested"])
        # The sha-path header is false on the patch-id path: zero commits in
        # the range carry an attestation there, which the line below it says
        # outright. Two lines contradicting each other is worse than either.
        extra = sum(m.get("uncovered_commits", 0) for m in doc["attested"])
        lines = [f"attest check: PASS — the reviewed content is present in "
                 f"{doc['base']}..{head}, attested by a commit outside it"
                 + (f"; {extra} commit(s) here were NOT covered by that review"
                    if extra else "") if by_patch_only else
                 f"attest check: PASS — {len(doc['attested'])} of {n} commit(s) "
                 f"in {doc['base']}..{head} carry a committed attestation"]
        for m in doc["attested"]:
            if m.get("matched_by", "sha") == "sha":
                lines.append(f"  {m['sha'][:12]} -> {m['shard']}")
                continue
            # Everything on this line is established by the match. What is NOT
            # established — that the range contains nothing the review never
            # saw — is stated as a count rather than asserted away.
            n_extra = m.get("uncovered_commits", 0)
            lines.append(
                f"  {(m['sha'] or '?')[:12]} -> {m['shard']}"
                f"  [matched by PATCH-ID, not by commit sha. That commit is "
                f"NOT in this range — its content is, so the reviewed change "
                f"survived a rebase. "
                + (f"{n_extra} commit(s) in this range were NOT covered by "
                   f"that review." if n_extra else
                   "Every commit in this range was covered by that review.")
                + "]")
        # WHAT THE CARVE-OUT EXCUSED, said on the pass rather than left
        # silent. Every uncovered commit here is one the rule looked at and
        # let through because its whole diff is a round's own evidence — the
        # ingest commit, on the golden path. A pass that did not name them
        # made the carve-out invisible exactly where a reader would audit it,
        # and CLI-Reference.md's claim that "the pass line counts them" was
        # false for the sha join, which is the default one (round 2, R2-1).
        excused = doc.get("uncovered") or []
        if excused and not by_patch_only:
            lines.append(
                f"  {len(excused)} commit(s) here land after the attestation "
                f"covering their line of history and were EXCUSED: every path "
                f"each touches is a round's own evidence under "
                f"{SHARD_DIR.as_posix()}/ — "
                + ", ".join(row["sha"][:12] for row in excused[:5])
                + (f", and {len(excused) - 5} more" if len(excused) > 5 else ""))
        blind = [m for m in latest if not m["verdict"]]
        if blind:
            # Said out loud rather than guessed: such a shard predates the
            # `verdict` field, so the check cannot tell whether the review it
            # records closed, and old evidence stays valid.
            lines.append(
                f"  NOTE: {len(blind)} attestation(s) at the tip of this "
                "range record no `verdict` — they predate the field, so this "
                "check could not confirm the last round closed clean")
    else:
        lines = [f"attest check: NO ATTESTATION — none of the {n} commit(s) in "
                 f"{doc['base']}..{head} is named by a shard committed at "
                 f"{head} ({doc['shard_count']} shard(s) in that tree)",
                 "  Run the pre-pr-review skill, then `warden memory ingest`, "
                 "and COMMIT the shard it writes.",
                 "  An attestation left in .warden/out/ is gitignored: it never "
                 f"reaches {SHARD_DIR} and never feeds a recall."]
    if doc.get("fallback_unavailable"):
        lines.append(f"  NOTE: {doc['fallback_unavailable']}")
    # The forgot-to-commit state, named by path — a shard on disk satisfies
    # nothing (CI clones the repo and cannot see it), and this line is
    # exactly the diagnostic that state needs.
    for row in doc.get("uncommitted", ()):
        if row["state"] == "untracked":
            lines.append(
                f"  ON DISK but untracked: {row['path']} names "
                f"{row['sha'][:12]} (in range) — `git add {row['path']}` "
                "and commit it; CI sees only committed shards")
        else:
            lines.append(
                f"  ON DISK, tracked, but not at {head}: {row['path']} names "
                f"{row['sha'][:12]} (in range) — commit it, or the checked "
                "head predates the shard's ingest commit")
    if doc["unreadable"]:
        # The CAUSE beside the path, and the remedy that names it. The generic
        # remedies this renderer offers elsewhere ("run the pre-pr-review
        # skill, then `warden memory ingest`, and COMMIT the shard",
        # "`git add <path>`") are both wrong for a shard that is already
        # committed and malformed, which is the shape envelope validation
        # refuses.
        causes = doc.get("unreadable_causes") or {}
        for path in doc["unreadable"]:
            why = causes.get(path)
            lines.append(f"  WARNING: unreadable shard: {path}"
                         + (f" — {why}" if why else ""))
        # WHAT INGEST ACTUALLY REFUSES, and no wider. `read_shard` and
        # `validate_shard_envelope` never look at `sha`, so ingest accepts a
        # shard missing it, prints nothing, and exits 0. A remedy promising
        # that ingest refuses every shape above would send the author to a
        # command that tells them nothing while the warning persists.
        lines.append("  A shard that is committed and malformed is repaired or "
                     "removed AT ITS PRODUCER — never edited in place, which "
                     "breaks the digest its filename carries. `warden memory "
                     "ingest` names and refuses the ENVELOPE defects above. A "
                     "shard that names no `sha` is one `ingest` will not "
                     "mention, because it never reads that field — but "
                     "`warden certify` (E-02), `warden deploy` and `memory "
                     "stats` all do, so leaving one in place costs a "
                     "certification rung and a deploy as well as this check.")
    return "\n".join(lines) + "\n"
