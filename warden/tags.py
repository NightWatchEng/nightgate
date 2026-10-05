"""Tag vocabulary — keeping the recall index from fragmenting.

Tags are a recall KEY, so they decide what a future review can find. Left
free-form, two sessions describing the same defect class as `fail-open` and
`failing-open` produce two islands, each too small to matter, and the recall
that should have fired never does. The failure is silent: nothing errors, the
index just quietly stops working.

So the vocabulary is declared, in `.warden/memory/tags.yaml`:

    tags:
      fail-open: a check that errs toward passing instead of blocking
      enforcement-claim: a comment or doc claiming a check that does not exist
      docs-drift: documentation describing behavior the code no longer has

Governance is deliberately soft. An unknown tag is a WARNING, never a
rejection: a genuinely new defect class must be nameable the moment it is
found, and a review is a bad time to argue about taxonomy. The warning plus
the near-duplicate report is what keeps drift visible, and the retro proposes
vocabulary changes like any other policy edit.

Soft governance still needs an OWNER, which is what the ceiling adds.
Visible drift is not acted-on drift: a warning can fire correctly for weeks
while the undeclared count climbs, because nothing obliges anyone to answer
it. So a name in the corpus has exactly
three honest dispositions, and a repo MAY require that every name have one:

    tags:              declared — this is a class, here is what it means
    aliases:           folded   — this is another spelling of a declared class
    left_undeclared:   left     — seen, and here is why it is neither, yet
    ceiling:
      max_undecided: 0
      rationale: >-
        why this number and not another

The ceiling bounds `undecided_tags`: names carrying NONE of the three. It is
deliberately NOT the count of undeclared names at the declaration bar,
because one review round can take several brand-new names past the bar
together. Bounding the
undecided count needs no taxonomy argument to clear: "n=1, coined this round,
watching" is a receipt, not a class judgment, so a new class stays nameable
the moment it is found while the SILENCE becomes impossible.

`undeclared_at_bar` survives as a REPORT — it names the `left_undeclared:`
entries whose evidence has outgrown the reason recorded for them.

This module reports; nothing here changes an exit code. `warden memory
ingest` in particular still exits 0 on a breach, because the cage reads that
exit code as "did the corpus rebuild" and would discard the run's evidence
shard if a vocabulary breach failed it. Enforcement is `warden memory
check-vocabulary` (warden/vocabulary.py), which reads
`ceiling_status` over the committed shards and exits 1 on a breach, 2 when it
cannot evaluate; this repo additionally pins the declared VALUE with a test,
which the command deliberately does not do.

When the retro merges two names for one class, the losing name becomes an
alias:

    aliases:
      test-determinism: wall-clock-dependence

Shards are committed evidence and never rewritten, so the alias is folded at
the read seams instead (cache rebuild, cache load, shard load): records keep
their historical name on disk while every index — stats, recall, candidate
ranking — sees exactly one key. An alias only applies when it cannot lose
information: its target must be declared, the alias name must not itself be
declared, and neither side may name something in the RULE namespace — a
declared rule id or a class a rule `covers:`. That last one is not a tag
question: `unmapped:<slug>` rule ids fold through this same table, so such an
alias would move candidate evidence onto a corpus key `attest write` refuses
to create. Anything else is reported by the audit and left
alone.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from . import yamlio

if TYPE_CHECKING:                       # the guard's import is runtime-local
    from .attest import _RuleIndex      # (see _rule_index); this is for types

VOCAB_FILE = "tags.yaml"

# The DECLARATION bar: a class earns a declared name at 3+ judged records,
# upheld more often than not. Deliberately not the
# promotion bar — naming a class is cheaper than automating one.
#
# ONE definition. `tests/test_tag_vocabulary_guards.py` imports
# it rather than re-typing the number, and the at-bar report below reads it.
# A second bar drawn beside the first is the shape `unmapped:candidate-bar-
# parity-partial` records: two readers of one rule, agreeing on the day they
# are written and diverging on the first edit to either.
DECLARE_MIN_N = 3

# Upheld, as `memory` reads it. A subset of `memory._JUDGED_STATUSES` — the
# ceiling and the precision rows must not disagree about what one record says.
_UPHELD_STATUSES = frozenset({"confirmed", "fixed"})

# What the ceiling bounds: undeclared tags carrying NO recorded decision.
# Not the undeclared classes AT THE DECLARATION BAR: one review round can
# take several brand-new names past the bar at once, so that count is no
# slower-moving than the raw one, it leaves the raw undeclared count bounded
# by nothing, and a bare integer cannot say WHICH class is allowed to sit
# there.
#
# Bounding the UNDECIDED count instead needs no taxonomy argument to clear:
# recording "coined this round, n=1, watching" is not a class judgment, it is
# a receipt. It bounds the exact number that drifted, it is 0 today, and it
# goes red the moment a name enters the corpus with nothing written about it.
CEILING_LIMIT = "max_undecided"

# The ceiling block may carry these and nothing else. An unknown key is a
# constraint this loader would silently drop, so it is refused rather than
# ignored (the shape `advisor._CEILING_KEYS` uses for the guardrail gap).
_CEILING_KEYS = {CEILING_LIMIT, "rationale"}

# Where a deliberately-undeclared name records its reason. Machine-readable
# on purpose: the first draft wrote these as YAML COMMENTS, which the next
# reconciler has to re-derive by hand — which is how the count reached 26.
LEFT_KEY = "left_undeclared"

# The other two declaration blocks, named rather than typed at each seam:
# `_BLOCK_SHAPES` below refuses a malformed one and `_load_doc`'s two callers
# read the same keys.
TAGS_KEY = "tags"
ALIASES_KEY = "aliases"


def vocab_path(root: Path) -> Path:
    from .memory import memory_dir
    return memory_dir(root) / VOCAB_FILE


def _load_doc(root: Path) -> dict:
    """The PERMISSIVE read: what does this file declare?

    An unreadable file declares nothing, and that is the safe answer here —
    the vocabulary only WARNS, so a repo whose file cannot be read gets every
    tag reported unknown, which is louder than the truth rather than quieter.

    The exception set matches that contract rather than only YAMLError.
    `is_file()` raises PermissionError on an unreadable parent, `read_text`
    raises UnicodeDecodeError (a ValueError) on a file that is not UTF-8, and
    either would otherwise escape as a traceback through every folding seam —
    `canonicalize_records`, `canonicalize_query`, `applicable_aliases` — and
    out of `memory ingest` and `memory stats`, which would then write no
    artifact at all.

    `_read_doc` is the FAIL-CLOSED counterpart, and the asymmetry is the point:
    "nothing is declared" is the right answer for a vocabulary and the wrong
    one for a ceiling.
    """
    try:
        path = vocab_path(root)
        if not path.is_file():
            return {}
        doc = yamlio.load(path.read_text()) or {}
    except (yaml.YAMLError, OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _read_doc(root: Path) -> tuple[object, str]:
    """(parsed document, complaint). The FAIL-CLOSED read of the vocabulary
    file, as against `_load_doc`'s deliberately permissive one.

    `_load_doc` answers "what is declared", where an unreadable file honestly
    declares nothing and the vocabulary only warns. This answers "can this
    file be examined at all", where it must not. Every way a real file
    resists being read lands here as a complaint rather than an exception:

    * `is_file()` is INSIDE the try — it raises PermissionError when the
      parent directory is unreadable, which would otherwise escape the guard
      whose whole point is to catch unreadable inputs;
    * `read_text` raises UnicodeDecodeError — a ValueError, not an OSError,
      so an OSError-only except tuple misses a file that exists and cannot be
      decoded;
    * a path that EXISTS but is not a regular file — a directory, or a
      symlink whose target is gone — is a broken declaration, not an absent
      one. Reporting it as "nothing declared" would render a repo whose
      ceiling cannot be resolved byte-identically to a repo that never
      declared one.
    """
    path = vocab_path(root)
    try:
        if not path.is_file():
            if path.exists() or path.is_symlink():
                return None, (f".warden/memory/{VOCAB_FILE} exists but is not "
                              f"a readable regular file (a directory, or a "
                              f"symlink whose target is missing) — if it "
                              f"declares a ceiling, that ceiling cannot be "
                              f"checked")
            return None, ""
        return yamlio.load(path.read_text()), ""
    except (yaml.YAMLError, OSError, ValueError) as e:
        return None, (f".warden/memory/{VOCAB_FILE} could not be read ({e}) — "
                      f"if it declares a ceiling, that ceiling cannot be "
                      f"checked")


def load_vocab(root: Path) -> dict[str, str]:
    """Declared tag -> description. Absent file means no vocabulary declared
    (and therefore nothing to warn against)."""
    tags = _load_doc(root).get(TAGS_KEY)
    if not isinstance(tags, dict):
        return {}
    return {str(k): str(v) for k, v in tags.items()}


def load_aliases(root: Path) -> dict[str, str]:
    """Retired name -> canonical tag, exactly as declared (unvalidated)."""
    aliases = _load_doc(root).get(ALIASES_KEY)
    if not isinstance(aliases, dict):
        return {}
    return {str(k): str(v) for k, v in aliases.items()}


# The two blocks this file declares beside `ceiling:` and `left_undeclared:`,
# with what each one's mapping means and what a name inside it earns.
_BLOCK_SHAPES = {
    TAGS_KEY: ("tag -> description", "declared"),
    ALIASES_KEY: ("retired name -> canonical tag", "folded"),
}


def _block_problem(root: Path, key: str) -> str | None:
    """Why `key:`'s block cannot be read, or None if it can.

    `load_vocab` and `load_aliases` above are the PERMISSIVE readers, and
    they must stay that way — every folding seam calls them and a traceback
    there destroys the run dir. What they cannot do is stay SILENT: both
    return `{}` for any non-mapping block, so one leading dash under `tags:`
    deletes the entire declared vocabulary: every name in the corpus reads as
    undecided, and a verdict computed from it would tell the author to declare
    names their file already declares. The same dash under `aliases:` drops
    every fold while the breach line prescribes "fold it with an `aliases:`
    entry" to an author whose entry is in the file just discarded. Without
    this function nothing would say the block was refused.

    So the complaint is what fails closed, exactly as `load_ceiling` and
    `load_left_undeclared` already do: the loaders keep returning `{}`, and
    this says so out loud to `ceiling_status` (which makes the verdict
    `cannot-evaluate` rather than a breach computed from a vocabulary nobody
    could read) and to `alias_problems` (the surface that PRESCRIBES the
    fold).

    ABSENT and EMPTY are not refusals, and that line is deliberate. `tags:`
    with nothing under it declares nothing, and `{}` is exactly what it says
    — nothing is silently dropped. Only a block carrying content this loader
    would throw away is a complaint.

    The FILE — unparseable, unreadable, or not a mapping at all — is not this
    function's fact either: `load_ceiling` already speaks for it, and
    `ceiling_status` already carries its complaint to every reader, so
    repeating it here would print one fact twice on every surface.
    """
    raw, complaint = _read_doc(root)
    if complaint or not isinstance(raw, dict):
        return None
    block = raw.get(key)
    if block is None or isinstance(block, dict):
        return None
    shape, earns = _BLOCK_SHAPES[key]
    return (f".warden/memory/{VOCAB_FILE}: {key!r} must be a mapping of "
            f"{shape} (got {type(block).__name__}) — as written, the loader "
            f"reads the whole block as EMPTY, so nothing in it is {earns} "
            f"and every name it names counts as undecided")


def block_problems(root: Path) -> tuple[str, ...]:
    """The `tags:` and `aliases:` blocks that cannot be read, each with its
    reason. Empty when both load — including when neither is declared."""
    return tuple(p for p in (_block_problem(root, TAGS_KEY),
                             _block_problem(root, ALIASES_KEY))
                 if p is not None)


def fold_problems(root: Path) -> tuple[str, ...]:
    """Every reason this repo's DECLARED VOCABULARY cannot be read.

    `block_problems` answers for the BLOCK and only for the block, and that is
    correct for what it is asked: it deliberately says nothing about the file,
    because `load_ceiling` already speaks for that and printing one fact twice
    on every surface is its own defect. But a caller asking "may I compute over
    this corpus" needs BOTH halves, and asking only the first is a fail-open:
    `load_aliases` goes through the PERMISSIVE `_load_doc`, which returns `{}`
    for an unparseable file, a top-level non-mapping and a chmod-000 file just
    as surely as for a non-mapping `aliases:` block — same empty fold table,
    same unfolded corpus, and `block_problems` returns `()` for all three.

    With `tags.yaml` unparseable, `memory check-vocabulary` exits 2 with
    VOCABULARY UNREADABLE, while a score computed without this check would
    clear the promotion bar over a corpus whose declared aliases silently
    folded nothing — one command confident and another refusing, reached
    through the FILE instead of the BLOCK.

    ONE TEST decides membership, and it is the reason the summary line above
    says VOCABULARY rather than FOLD: **does it stop either block that DECLARES
    the vocabulary — `tags:` and `aliases:` — from being read as declared?** The
    file is in scope because a file that cannot be parsed, or that is not a
    mapping at all, declares nothing through either block; `block_problems`
    covers the two blocks themselves.

    That test is why `tags:` is here beside `aliases:` even though only the
    second one FOLDS. A declaration file with a refused block is not a file any
    reading of it can be trusted from, `memory check-vocabulary` already exits 2
    on exactly that tree, and a promotion decision is the last place to be the
    one command that disagrees.

    It is also why a `ceiling:` with a token `rationale`, or an unreadable
    `left_undeclared:`, is NOT here. Those are real defects, and
    `ceiling_status` carries them to every reader — but they leave both
    declaration blocks readable, so the vocabulary they declare is intact and
    refusing a promotion score over them would be a wider refusal than the
    question asks.

    `tests/test_tag_vocabulary_guards.py` drives both sides of that line.
    """
    doc, file_problem = _read_doc(root)
    if file_problem:
        return (file_problem,) + block_problems(root)
    if doc is not None and not isinstance(doc, dict):
        # A top-level list or scalar PARSES, so `_read_doc` has no complaint —
        # and `_load_doc` still returns `{}` for it, so the fold table is
        # empty just the same. Asked for by name here rather than left to
        # `_read_doc`, whose contract is "can this file be examined at all".
        return (f".warden/memory/{VOCAB_FILE} is not a mapping (got "
                f"{type(doc).__name__}) — nothing it declares can be read, so "
                f"nothing is folded",) + block_problems(root)
    return block_problems(root)


# attest.py owns the prefix; mirrored here rather than imported so folding a
# record costs no import at all (tags are read on every recall). The alias
# GUARD below does read attest — but only inside the function, and only when
# an alias is actually declared, so the common path is unchanged.
_UNMAPPED_PREFIX = "unmapped:"


_RULE_INDEX_CACHE: dict = {}


def _rule_index(root: Path) -> tuple[_RuleIndex | None, str]:
    """(index, error) for the repo's declared ruleset, resolved from the ROOT.

    This is where rule-namespace knowledge enters the tag layer, and it enters
    through the one handle every seam already passes. `rules_dir` is not
    threaded through the signatures on purpose: a check only the seams holding
    a rules_dir could apply would fold differently per reader, which is worse
    than no check. It is DERIVED from the repo's own
    `review.rules_dir` declaration, never guessed.

    No ruleset declared -> (None, ""): nothing exists for an alias to collide
    with. A ruleset that exists but will not load, OR a repo whose declaration
    cannot be read at all -> (None, error): the alias cannot be shown safe
    against it, so it does not fire. Those last two must not collapse into the
    permissive answer: a corrupt repo.yaml falling back to the default path,
    where an enrolled consumer's real rules do not live, would read the empty
    listing as "no ruleset" and fire every alias.

    Both imports are function-local. The tag layer is read on every recall and
    must stay cheap to import; the ruleset is read only when an alias is
    actually declared.
    """
    from .config import declared_rules_dir, CONFIG_NAME
    rules_dir, problem = declared_rules_dir(root)
    if problem:
        return None, problem
    try:
        # iterdir, not glob: glob swallows a PermissionError and returns [],
        # which reads as "no ruleset" — the permissive answer for an input
        # nobody could examine.
        files = sorted(p for p in rules_dir.iterdir() if p.suffix == ".md")
    except FileNotFoundError:
        # An enrolled repo whose declared ruleset is missing is misconfigured,
        # not unconstrained. A repo with no repo.yaml at all is simply not
        # enrolled, and has nothing for an alias to collide with.
        if (root / CONFIG_NAME).exists():
            return None, (f"the declared ruleset dir {rules_dir} does not "
                          "exist, so no alias can be shown safe against it")
        return None, ""
    except OSError as e:
        return None, f"the ruleset in {rules_dir} cannot be listed ({e})"
    if not files:
        return None, ""
    # Parsing the ruleset costs ~100x what folding a record does, and several
    # seams fold per command. Keyed on what the files ARE, not on the clock:
    # a fixture that writes a rule between two calls (and a rules edit inside
    # a long-lived run) must be seen, or the guard answers from a ruleset that
    # no longer exists.
    from .attest import _RuleIndex
    from .rules import RuleError
    try:
        # The stat() calls belong INSIDE the guard: a broken symlink named
        # *.md is listed by iterdir and raises from stat, which would escape
        # `_split_aliases` and out through every folding seam. ValueError
        # covers UnicodeDecodeError, which `rules._parse`'s read_text raises
        # on a rule file that is not valid UTF-8.
        key = (str(rules_dir),
               tuple((f.name, st.st_mtime_ns, st.st_size)
                     for f in files for st in [f.stat()]))
        if key in _RULE_INDEX_CACHE:
            return _RULE_INDEX_CACHE[key]
        result = (_RuleIndex(rules_dir), "")
    except (RuleError, OSError, ValueError, yaml.YAMLError) as e:
        key = (str(rules_dir), ("unreadable",))
        result = (None,
                  f"the declared ruleset in {rules_dir} will not load ({e})")
    if len(_RULE_INDEX_CACHE) > 32:      # many roots in one process (pytest)
        _RULE_INDEX_CACHE.clear()
    _RULE_INDEX_CACHE[key] = result
    return result


def _split_aliases(root: Path) -> tuple[dict[str, str], list[str]]:
    """(applicable, problems). An alias applies only when it cannot lose
    information: the target must be declared, the alias name must not itself
    be declared — folding a live tag would silently vanish it — and NEITHER
    side may name something in the rule namespace.

    That last check is why this function reads the ruleset. `canonicalize_
    records` folds `unmapped:<slug>` rule ids through this same table, so an
    alias whose name or target is a declared rule id or a class a declared
    rule `covers:` moves candidate evidence onto a key `attest write` refuses
    to create: those judgments land as a 'covered' candidate row instead of
    counting toward the covering rule's precision. The
    predicate is `attest`'s own — one definition of "shadowed" — asked in
    its IDENTITY form: the writer's enforcement reading
    re-opens a paused rule's class, but an alias must hold across the
    pause, so the guard sees paused rules too (see _shadowed_by).
    """
    vocab = load_vocab(root)
    aliases = load_aliases(root)
    if not aliases:
        return {}, []
    index, unreadable = _rule_index(root)
    ok: dict[str, str] = {}
    problems: list[str] = []
    for name, target in aliases.items():
        if unreadable:
            problems.append(f"{name} -> {target}: {unreadable}, so the alias "
                            "cannot be shown safe against the rule namespace")
        elif name in vocab:
            problems.append(f"{name} -> {target}: {name!r} is itself declared")
        elif target not in vocab:
            problems.append(f"{name} -> {target}: target not declared")
        elif (shadow := _shadowed_by(index, name)) is not None:
            problems.append(
                f"{name} -> {target}: {name!r} is {shadow} — folding it "
                "detaches that rule's committed evidence from it")
        elif (shadow := _shadowed_by(index, target)) is not None:
            problems.append(
                f"{name} -> {target}: target {target!r} is {shadow} — folding "
                f"onto it writes 'unmapped:{target}', a key in the rule "
                "namespace (`attest write` refuses it whenever that rule is "
                "unpaused)")
        else:
            ok[name] = target
    return ok, problems


def _shadowed_by(index: _RuleIndex | None, slug: str) -> str | None:
    """How `slug` is spoken for in the rule namespace, or None if it is free.

    Phrased for the audit line, but the question is exactly the one
    `attest write` asks before refusing an `unmapped:` id — asked in its
    IDENTITY form (`include_paused=True`): the writer routes
    NEW evidence, so a paused rule's covered class stops shadowing there,
    but an alias folds at every read seam for as long as it is declared. A
    pause is reversible, so an alias admitted against a paused rule's class
    would fold that class's records today and unfold them the day the rule
    unpauses — recall flipping with pause state while the declared table
    never changed. A paused rule still OWNS its class; the alias stays
    refused.
    """
    if index is None:
        return None
    by = index.shadowing(_UNMAPPED_PREFIX + slug, include_paused=True)
    if by is None:
        return None
    if by == slug:
        return "a declared rule id"
    return f"a candidate class the declared rule {by!r} covers"


def alias_problems(root: Path) -> list[str]:
    """The declared aliases this loader REFUSED, each with its reason.

    The companion of `applicable_aliases`, and public for the same reason it
    is: a refused alias folds nothing, so a name the author already folded
    still counts as undecided. Every surface that prescribes folding as a
    remedy must be able to say that the remedy was already tried and
    silently dropped, including the one surface that can redden a consumer's
    CI.

    The refused BLOCK leads the list: `load_aliases` returns `{}` for a
    non-mapping `aliases:` and `_split_aliases` early-returns `({}, [])` on an
    empty table, so without it a whole malformed block would produce zero
    aliases AND zero problems — nothing folded and nothing said so, while the
    breach verdict went on prescribing the entry just discarded."""
    block = _block_problem(root, ALIASES_KEY)
    return ([block] if block else []) + _split_aliases(root)[1]


def applicable_aliases(root: Path) -> dict[str, str]:
    """The aliases that actually fold. Every seam that folds reads this, never
    `load_aliases`: a reader folding by the raw table would disagree with the
    corpus about what the records say."""
    return _split_aliases(root)[0]


def _fold_rule_id(aliases: dict[str, str], rule_id: str) -> str:
    if rule_id.startswith(_UNMAPPED_PREFIX):
        slug = rule_id[len(_UNMAPPED_PREFIX):]
        return _UNMAPPED_PREFIX + aliases.get(slug, slug)
    return rule_id


def canonicalize_query(root: Path, *, tags: list[str],
                       rules: list[str]) -> tuple[set[str], set[str]]:
    """Fold a recall query's tag and rule keys exactly as records fold.

    Records are canonicalized at the read seams, so a query still using the
    retired name — often copied straight out of an old shard or its reason
    text — would otherwise match nothing, silently: the exact
    silent-index-miss aliases exist to prevent."""
    aliases, _ = _split_aliases(root)
    return ({aliases.get(t, t) for t in tags},
            {_fold_rule_id(aliases, r) for r in rules})


def canonicalize_records(root: Path, records: list[dict]) -> list[dict]:
    """Fold aliased tags into their canonical name, in place. Idempotent —
    applied at every read seam, so a record may pass through twice.

    `unmapped:` candidate slugs fold too: candidates key on rule_id, not
    tags, so a merged class would otherwise keep splitting exactly the
    ranking the rule advisor mines. A DECLARED rule's id never folds — it is
    a promotion key bound to backtest artifacts, not vocabulary."""
    aliases, _ = _split_aliases(root)
    if not aliases:
        return records
    for record in records:
        tags = record.get("tags") or []
        folded = sorted({aliases.get(t, t) for t in tags})
        if folded != tags:
            record["tags"] = folded
        rid = record.get("rule_id", "")
        if rid != _fold_rule_id(aliases, rid):
            record["rule_id"] = _fold_rule_id(aliases, rid)
    return records


# Longest first: "deployment" must lose "ment", not "ed" from the middle.
_SUFFIXES = ("ment", "ing", "es", "ed", "s")


def _norm(tag: str) -> str:
    """Fold the differences that make two tags the same idea: case,
    separators, and the endings English hands out for free.

    Folded PER WORD, not per string — `fail-open` and `failing-open` are the
    same idea, and only the first word differs.
    """
    words = tag.lower().replace("_", "-").replace(" ", "-").split("-")
    out = []
    for w in words:
        for suffix in _SUFFIXES:
            if len(w) > 4 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        out.append(w)
    return "".join(out)


def unknown_tags(root: Path, tags: list[str]) -> list[str]:
    vocab = load_vocab(root)
    if not vocab:
        return []
    return sorted({t for t in tags if t not in vocab})


def near_duplicates(tags: list[str]) -> list[tuple[str, str]]:
    """Pairs that normalize to the same idea — 'deploy' vs 'deployment',
    'fail-open' vs 'failing_open'. Reported, never auto-merged: only a human
    knows whether two names are one concept or a distinction worth keeping."""
    buckets: dict[str, list[str]] = {}
    for tag in sorted(set(tags)):
        buckets.setdefault(_norm(tag), []).append(tag)
    out: list[tuple[str, str]] = []
    for names in buckets.values():
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                out.append((names[i], names[j]))
    return out


# The prefix every complaint about the `ceiling:` BLOCK carries, and nothing
# else does. `load_ceiling` speaks for two different facts and a reader has to
# tell them apart: a complaint carrying this marker means the block IS
# declared and will not load, where one without it means the FILE could not be
# examined at all and whether a ceiling is declared is unknowable. Labelling
# both "declared" would tell a repo with no ceiling that it had one, so the
# marker is shared rather than re-typed at the reader.
CEILING_WHERE = f".warden/memory/{VOCAB_FILE}: 'ceiling'"


def load_ceiling(root: Path) -> tuple[int | None, tuple[str, ...]]:
    """The declared drift ceiling from `.warden/memory/tags.yaml`.

    Returns (max_undecided, complaints). An absent file or an absent
    `ceiling:` key IS no ceiling — nothing was declared, so nothing is
    disarmed, and a repo that never declares one is unaffected by any of this.

    Everything else FAILS CLOSED, and that is why this reads the file itself
    instead of going through `_load_doc`: `_load_doc` swallows a YAMLError and
    returns `{}`, which is the right answer for "what tags are declared" (an
    unreadable file declares none, and the vocabulary only WARNS) and exactly
    the wrong one here. A ceiling that was declared and cannot be read must
    stop the run, never read as headroom — one typo must not disarm the only
    deterministic obligation this file carries.

    The rationale is REQUIRED, at the same substance floor a catalog answer's
    reason carries: a number set to whatever today's count happens to be
    ratchets nothing, and a length-only check gets gamed.
    """
    raw, complaint = _read_doc(root)
    if complaint:
        return None, (complaint,)
    where = CEILING_WHERE
    if raw is None:
        return None, ()
    if not isinstance(raw, dict):
        # A top-level list or scalar parses as valid YAML. The declaration may
        # be buried inside it, so "could not look" must not read as "nothing
        # declared".
        return None, (f".warden/memory/{VOCAB_FILE} is not a mapping (got "
                      f"{type(raw).__name__}) — if it declares a ceiling, "
                      f"that ceiling cannot be checked",)
    if "ceiling" not in raw:
        return None, ()
    ceiling = raw["ceiling"]
    if not isinstance(ceiling, dict):
        return None, (f"{where} must be a mapping (got "
                      f"{type(ceiling).__name__})",)
    unknown = set(ceiling) - _CEILING_KEYS
    if unknown:
        # repr, not the bare key: YAML mapping keys need not be strings, and
        # `sorted()` over a mix of str and int raises TypeError — so the
        # refusal path would crash on exactly the input it exists to refuse.
        return None, (f"{where}: unknown key(s) "
                      f"{sorted(map(repr, unknown))} — a constraint this "
                      f"loader does not understand would be silently "
                      f"dropped; it may only carry "
                      f"{sorted(_CEILING_KEYS)}",)
    value = ceiling.get(CEILING_LIMIT)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        # bool is an int subclass: `max_undecided: true` would otherwise
        # arrive as the ceiling 1.
        return None, (f"{where}: {CEILING_LIMIT!r} must be a "
                      f"non-negative integer (got {value!r})",)
    rationale = ceiling.get("rationale")
    from .declarative import _argument_has_substance
    if (not isinstance(rationale, str)
            or not _argument_has_substance(rationale)):
        return None, (f"{where}: 'rationale' must be an argument, not a "
                      f"token — say why the value is what it is, in a "
                      f"sentence a later reader can disagree with; a ceiling "
                      f"set to whatever today's count happens to be ratchets "
                      f"nothing",)
    return value, ()


def load_left_undeclared(root: Path) -> tuple[dict[str, str], tuple[str, ...]]:
    """(tag -> recorded reason, complaints) for the deliberately-left names.
    `load_triggers` reads the same block for each receipt's `trigger:` key.

    The third disposition, beside declaring and folding. A class below the
    declaration bar cannot honestly be declared and must not be folded on a
    guess, so the only truthful thing to do is write down that it was seen and
    why it was left — and write it where a machine can check it, not in a
    comment. This block is what the ceiling counts against.

    A reason carries the same substance floor as a catalog answer's: "n=1,
    coined this round, watching" clears it; "todo" does not. FAILS CLOSED
    exactly as `load_ceiling` does — a block that cannot be read yields no
    entries AND a complaint, so every name it would have excused is counted
    undecided rather than silently forgiven.
    """
    reasons, _, problems = _read_left(root)
    return reasons, problems


def load_triggers(root: Path) -> tuple[dict[str, dict], tuple[str, ...]]:
    """(tag -> trigger, complaints): each receipt's `trigger:` key, read by
    the same parser as its reason. A receipt stating none is absent."""
    _, triggers, problems = _read_left(root)
    return triggers, problems


# A receipt's re-read condition lives in ONE canonical key, never in prose:
#
#   name:
#     reason: >-
#       what was seen and why it is left
#     trigger: {judged_at_least: 3, upheld_at_least: 3, rule_live: rule-id}
#
# Any subset of the three, and the receipt fires when ANY one is met —
# a decision naming two conditions for its own expiry expires on either.
# `trigger: none` says the reason states no condition. The prose is the human
# reason and nothing reads a condition out of it: a reader
# guessing at phrasings is blind to every phrasing it has not met.
TRIGGER_KEY = "trigger"
_TRIGGER_COUNTS = ("judged_at_least", "upheld_at_least")
_TRIGGER_RULE = "rule_live"

# What a reason that states a re-read condition in words looks like: a re-read
# verb beside a number or a backticked name, anywhere in it, or a FUTURE COUNT
# whatever verb carries it ("a second occurrence is the retro's to weigh"
# names no re-read verb and is a condition all the same). Deliberately
# wide — the ruling is a REFUSAL, not a parse. A false refusal costs one key
# (`trigger: none` if the words were history, not a condition); a false pass
# is a condition nothing evaluates.
_REREAD_WORD = re.compile(
    r"\b(?:re-?read|re-?visit|re-?open|re-?argue|re-?decide|re-?examine"
    r"|re-?consider|re-?evaluate|re-?assess|fold it)", re.I)
_OPERAND = re.compile(r"\d|`[^`]+`|\b(?:one|two|three|four|five|another"
                      r"|first|second|third|fourth|fifth)\b", re.I)
_FUTURE_COUNT = re.compile(
    r"\b(?:another|second|third|fourth|fifth|next)\s+(?:[\w-]+\s+)?"
    r"(?:record|occurrence|instance|finding|round)s?\b", re.I)


def _trigger_problem(trigger: object) -> str | None:
    """Why a `trigger:` value is not the canonical key, or None."""
    if trigger == "none":
        return None
    if not isinstance(trigger, dict) or not trigger:
        return (f"{TRIGGER_KEY!r} must be `none` or a mapping of "
                f"{_TRIGGER_COUNTS + (_TRIGGER_RULE,)} (got {trigger!r})")
    unknown = set(trigger) - {*_TRIGGER_COUNTS, _TRIGGER_RULE}
    if unknown:
        return (f"{TRIGGER_KEY!r}: unknown condition(s) "
                f"{sorted(map(repr, unknown))} — a condition no reader "
                f"evaluates would pass as one; only "
                f"{list(_TRIGGER_COUNTS) + [_TRIGGER_RULE]} are read")
    for key in _TRIGGER_COUNTS:
        n = trigger.get(key)
        if key in trigger and (isinstance(n, bool) or not isinstance(n, int)
                               or n < 1):
            return f"{TRIGGER_KEY!r}: {key!r} must be a positive integer"
    rule = trigger.get(_TRIGGER_RULE)
    if _TRIGGER_RULE in trigger and not (isinstance(rule, str) and rule):
        return f"{TRIGGER_KEY!r}: {_TRIGGER_RULE!r} must be a rule id"
    return None


def _read_left(root: Path) -> tuple[dict[str, str], dict[str, dict],
                                    tuple[str, ...]]:
    """ONE parse of `left_undeclared:`, for both public readers."""
    from .declarative import _argument_has_substance
    raw, complaint = _read_doc(root)
    if complaint:
        return {}, {}, (complaint,)
    if not isinstance(raw, dict) or LEFT_KEY not in raw:
        return {}, {}, ()
    where = f".warden/memory/{VOCAB_FILE}: {LEFT_KEY!r}"
    block = raw[LEFT_KEY]
    if not isinstance(block, dict):
        return {}, {}, (f"{where} must be a mapping of tag -> reason (got "
                        f"{type(block).__name__})",)
    vocab = load_vocab(root)
    # The aliases that FOLD, not the raw table — the same rule every folding
    # seam obeys, stated at `applicable_aliases`. A REFUSED alias discharges
    # nothing, so a receipt for that name is the correct thing to have, and
    # reading the raw table would call it a contradiction, tell the author to
    # delete the one line clearing the breach, and leave the breach
    # unclearable.
    aliases = applicable_aliases(root)
    out: dict[str, str] = {}
    triggers: dict[str, dict] = {}
    problems: list[str] = []
    for name, reason in block.items():
        name = str(name)
        trigger, bad = None, None
        if isinstance(reason, dict):
            entry = reason
            reason, trigger = entry.get("reason"), entry.get(TRIGGER_KEY)
            if set(map(str, entry)) != {"reason", TRIGGER_KEY}:
                bad = (f"is a mapping, so it carries exactly `reason:` and "
                       f"`{TRIGGER_KEY}:` (got {sorted(map(str, entry))})")
            else:
                bad = _trigger_problem(trigger)
        if name in vocab:
            problems.append(f"{where}: {name!r} is DECLARED — a name cannot "
                            f"be both declared and left undeclared; delete "
                            f"one of the two entries")
        elif name in aliases:
            problems.append(f"{where}: {name!r} is FOLDED by an alias — the "
                            f"fold already discharges it; delete this entry")
        elif not isinstance(reason, str) or not _argument_has_substance(reason):
            problems.append(
                f"{where}: {name!r} has no reason a later reader can weigh — "
                f"say what was seen and why it is not declared or folded "
                f"yet, in a sentence; a token is not a decision")
        elif bad:
            problems.append(f"{where}: {name!r} {bad}")
        elif trigger is None and (_FUTURE_COUNT.search(reason) or (
                _REREAD_WORD.search(reason) and _OPERAND.search(reason))):
            problems.append(
                f"{where}: {name!r} states a re-read condition in prose, which "
                f"nothing evaluates — write it as `{TRIGGER_KEY}:` beside "
                f"`reason:` ({', '.join(_TRIGGER_COUNTS)}, {_TRIGGER_RULE}), "
                f"or `{TRIGGER_KEY}: none` if the words state no condition")
        else:
            out[name] = reason
            if isinstance(trigger, dict):
                triggers[name] = trigger
    return out, triggers, tuple(problems)


def tag_counts(records: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    """(judged, upheld) record counts per tag — ONE derivation, read by the
    declaration bar and by every receipt trigger."""
    from .memory import _JUDGED_STATUSES
    judged: dict[str, int] = {}
    upheld: dict[str, int] = {}
    for record in records:
        if record.get("status") not in _JUDGED_STATUSES:
            continue
        for tag in record.get("tags") or []:
            judged[tag] = judged.get(tag, 0) + 1
            if record.get("status") in _UPHELD_STATUSES:
                upheld[tag] = upheld.get(tag, 0) + 1
    return judged, upheld


def trigger_fired(trigger: dict, judged: int, upheld: int,
                  rules) -> list[str]:
    """Every condition of one receipt's trigger that has been met, each with
    why; empty while all hold. `rules` is the declared ruleset, paused rules
    included — a rule absent from it has been retired."""
    whys = []
    for key, have in (("judged_at_least", judged), ("upheld_at_least", upheld)):
        if key in trigger and have >= trigger[key]:
            whys.append(f"{key}: {trigger[key]} is met — {have} committed")
    rule_id = trigger.get(_TRIGGER_RULE)
    if rule_id is not None:
        rule = next((r for r in rules if r.id == rule_id), None)
        if rule is None:
            whys.append(f"{_TRIGGER_RULE}: {rule_id} — the ruleset no longer "
                        f"declares it (retired)")
        elif rule.paused:
            whys.append(f"{_TRIGGER_RULE}: {rule_id} — declared but PAUSED")
    return whys


def fired_triggers(triggers: dict[str, dict], records: list[dict],
                   rules) -> dict[str, list[str]]:
    """Every receipt whose trigger has fired over this corpus and ruleset."""
    judged, upheld = tag_counts(records)
    fired = {name: why for name, trigger in triggers.items()
             if (why := trigger_fired(trigger, judged.get(name, 0),
                                      upheld.get(name, 0), rules))}
    return fired


def undecided_tags(root: Path, records: list[dict]) -> list[str]:
    """Tags in the corpus with NO recorded disposition at all.

    Not declared, not folded, and not written down under `left_undeclared:`.
    This is what the ceiling bounds: without it nothing obliges anyone to
    write anything about a coined name, so the backlog grows silently.

    Clearing an entry costs one line and no taxonomy argument, which is the
    point: a reviewer who has just coined a name can record "n=1, coined this
    round, watching" without deciding what the class IS. That keeps the soft
    governance this file's header promises — a new class stays nameable the
    moment it is found — while making the SILENCE impossible.

    Records are expected CANONICALIZED, so a folded name has already become
    its target and never appears here.
    """
    vocab = load_vocab(root)
    left, _ = load_left_undeclared(root)
    used = {t for r in records for t in (r.get("tags") or [])}
    return sorted(t for t in used if t not in vocab and t not in left)


def undeclared_at_bar(root: Path, records: list[dict]) -> list[str]:
    """Undeclared tags whose evidence already clears the DECLARATION bar.

    REPORTED, never enforced: the at-bar count moves as fast as the raw one —
    one review round can take several brand-new names past the bar together —
    so bounding it would force a
    taxonomy argument inside the PR that coined them, whose cheapest remedy is
    to reuse a near-enough declared tag. That is the split-recall harm,
    inverted.

    What it IS good for is telling the retro which `left_undeclared:` entries
    have outgrown their reason: a class recorded as "n=1, watching" that now
    carries four upheld records is a re-read, and this line names it.
    What IS refused is narrower than the count: `attest write`, in a repo
    declaring a ceiling, refuses a payload whose own records would leave an
    at-bar receipt's premise broken (`outgrown_receipts`). The number of
    names at the bar stays unbounded.

    Records are expected CANONICALIZED (every read seam folds them), so a name
    resolved by an alias has already become its target and never appears here.
    """
    vocab = load_vocab(root)
    judged, upheld = tag_counts(records)
    return sorted(t for t, n in judged.items() if t not in vocab
                  and n >= DECLARE_MIN_N and upheld.get(t, 0) * 2 > n)


# What an at-bar receipt says to stay a live decision: that it argues FROM the
# bar, and the count it argues from. The words and the `n=` shape are this
# repo's guard's (tests/test_tag_vocabulary_guards.py), read here so `attest
# write` can refuse a payload that would break them before it is a shard.
AT_BAR_WORDS = "AT the declaration bar"
_STATED_N = re.compile(r"\bn=(\d+)")


def outgrown_receipts(root: Path, records: list[dict],
                      rules) -> dict[str, list[str]]:
    """Every name whose `left_undeclared:` premise this corpus has outgrown,
    each with why; empty while every premise holds.

    Two readings, the two the guard suite applies to the committed corpus:
    a receipt whose `trigger:` has fired (either side of the bar), and an
    undeclared name AT the declaration bar whose receipt is missing, does
    not say it argues from the bar, states no `n=<judged>`, or states an `n`
    below the judged count. A sub-bar receipt whose `n=` has crept is not
    read: the guard leaves it to its own tracked item, and so does this.

    Records are expected CANONICALIZED and restatement-folded, as every
    counting seam reads them.
    """
    left, _ = load_left_undeclared(root)
    triggers, _ = load_triggers(root)
    judged, _ = tag_counts(records)
    out = {name: list(why)
           for name, why in fired_triggers(triggers, records, rules).items()}
    for name in undeclared_at_bar(root, records):
        have = judged.get(name, 0)
        receipt = left.get(name)
        stated = _STATED_N.search(receipt or "")
        if not receipt:
            why = (f"AT the declaration bar ({have} judged) with no receipt "
                   f"under '{LEFT_KEY}:' to re-read")
        elif AT_BAR_WORDS not in receipt:
            why = (f"reaches the bar ({have} judged) and its receipt does not "
                   f"argue it — it never says '{AT_BAR_WORDS}'")
        elif not stated:
            why = (f"AT the declaration bar ({have} judged) and its receipt "
                   f"states no count as `n=<judged>`")
        elif int(stated.group(1)) < have:
            why = (f"its at-bar receipt argues from n={stated.group(1)} and "
                   f"the shards would hold {have} judged record(s)")
        else:
            continue
        out.setdefault(name, []).append(why)
    return out


# ── the complaint labeller: one answer to "which declaration is this about?" ─
#
# `ceiling_status` merges complaints from FOUR sources — the `ceiling:` BLOCK,
# the vocabulary FILE itself, the `left_undeclared:` receipts block, and the
# `tags:`/`aliases:` declaration blocks — and three readers print them:
# `_render_ceiling` below (the `memory stats` audit), `memory ingest`'s stderr
# line in cli.py, and `vocabulary.render_verdict` (`memory check-vocabulary`).
# Prefixing EVERY complaint "CEILING UNREADABLE" would assert a declaration
# that may not exist — telling a repo with NO ceiling and one bad receipt that
# its ceiling was declared and unreadable — on a surface whose whole job is
# telling an author which line to fix.
#
# So the source is labelled ONCE, here: one labeller with one caller, the
# merge in `ceiling_status`, and all three readers print the list it builds,
# so no complaint class can arrive labelled correctly on one surface and as
# the ceiling on another.
_LABELS = {
    # "TAG CEILING", because a repo has TWO ceilings in two files and the
    # bare word lets a reader conflate them: this is the tag-vocabulary drift
    # ceiling in `.warden/memory/tags.yaml`, not the guardrail-gap ceiling in
    # `.warden/catalog-answers.yaml` that `advisor.py` renders.
    "ceiling-block": "TAG CEILING UNREADABLE (declared, so this is not 'no "
                     "ceiling' and never headroom)",
    "vocab-file": "VOCABULARY UNREADABLE (so whether a ceiling is declared "
                  "cannot be known, and a declaration that cannot be read is "
                  "never headroom)",
    "declaration-block": "DECLARATION BLOCK UNREADABLE (declared and "
                         "unreadable is never 'nothing declared': nothing "
                         "inside it is declared or folded, so every name it "
                         "names counts as undecided and the count would be "
                         "computed from a block nobody could read)",
    "receipts": f"RECEIPTS UNREADABLE ('{LEFT_KEY}:' cannot be read, so "
                "nothing it names is discharged)",
}


def complaint_source(problem: str, *, ceiling_problems: set[str],
                     block_shape_problems: set[str]) -> str:
    """Which declaration this complaint speaks for.

    Order matters and is the reason this is one function: `CEILING_WHERE` is
    the ceiling LOADER's own marker (shared, never re-typed), and it separates
    the ceiling BLOCK from the FILE the loader also complains about — a
    distinction that matters, because calling the file case
    "declared" asserts a declaration nobody can know exists.
    """
    if problem.startswith(CEILING_WHERE):
        return "ceiling-block"
    if problem in ceiling_problems:
        return "vocab-file"
    if problem in block_shape_problems:
        return "declaration-block"
    return "receipts"


def label_complaint(problem: str, *, ceiling_problems: set[str],
                    block_shape_problems: set[str]) -> str:
    """One complaint, prefixed with the declaration it actually speaks for.

    Takes the two source SETS rather than a root, because the only caller is
    `ceiling_status`, which already holds them — a root-taking convenience
    wrapper would re-read both files on every render and would be a second way
    to ask one question. The renderers do not call this at all: they print the
    `labelled` list `ceiling_status` hands them, so there is exactly one place
    a complaint is classified.
    """
    source = complaint_source(problem, ceiling_problems=ceiling_problems,
                              block_shape_problems=block_shape_problems)
    return f"{_LABELS[source]} — {problem}"


def ceiling_status(root: Path, records: list[dict]) -> dict:
    """One reading of the corpus against the declared ceiling.

    ONE definition, so the audit render, `memory stats`, the shipped
    enforcement (`memory check-vocabulary`, warden/vocabulary.py) and the
    repo's own guard test cannot disagree about whether it is in breach.

    NEVER RAISES, and it needs no try/except to promise that: both readers
    beneath it return rather than throw (`_load_doc` permissively, `_read_doc`
    with a complaint). That matters because this is called from `memory
    ingest` and `memory stats`, both of which must still rebuild and report
    when the vocabulary file itself is broken — a traceback there destroys the
    run dir, and with it the `ingest-result.json` the reading travels in.

    An unreadable vocabulary therefore reads as "nothing is declared and
    nothing is decided": every tag in the corpus counts as undecided, and the
    complaint says why. That is the loud direction. A withheld or zeroed count
    would read exactly like a repo in perfect health.
    """
    ceiling, complaints = load_ceiling(root)
    problems = list(complaints)
    _, left_problems = load_left_undeclared(root)
    problems += [p for p in left_problems if p not in problems]
    # The `tags:` and `aliases:` blocks fail closed here too. They are the two
    # dispositions `left_undeclared:` sits beside: without this line a
    # non-mapping block loads as `{}` and the count is computed from a
    # vocabulary nobody could read — a RED with no explanation, which
    # `vocabulary.check` reports as cannot-evaluate instead.
    #
    # FOUR sources in one list — the ceiling BLOCK, the vocabulary FILE (both
    # `load_ceiling`'s), the receipts block, and these two — and the source of
    # each is knowable HERE and nowhere downstream. So this is where the label
    # is attached: `labelled` carries every complaint already
    # prefixed with the declaration it speaks for, and no renderer re-derives
    # it. `complaints` stays the raw list, unchanged, for the readers that
    # match on the payload.
    block_shape = block_problems(root)
    problems += [p for p in block_shape if p not in problems]
    ceiling_problems = set(complaints)
    undecided = undecided_tags(root, records)
    return {"ceiling": ceiling, "complaints": problems,
            "labelled": [label_complaint(
                p, ceiling_problems=ceiling_problems,
                block_shape_problems=set(block_shape)) for p in problems],
            "undecided": undecided,
            "at_bar": undeclared_at_bar(root, records),
            "breached": ceiling is not None and len(undecided) > ceiling}


def rule_namespace(rules_dir: Path, *, covers: bool = True) -> set[str]:
    """Every key an `unmapped:<slug>` fold could collide with: a declared rule
    id, and — unless `covers=False` — every class a declared rule `covers:`.
    Paused rules included — a pause suspends enforcement, never identity.

    `covers=False` is the ids alone, which is what a TAG check wants: a
    covered class is the right tag for a finding, not a misused rule id, and
    only this repo's guard requires it to be declared too.

    One definition, read by the repo's guard test (ids and covers) and by
    `attest write`'s tag check (ids only). The two agree in this repo only
    because its guard also requires every covered class to be declared
    (`test_every_covered_class_is_a_declared_tag`), and a declared tag is
    exempt from both readings."""
    from .rules import load_rules
    names: set[str] = set()
    for rule in load_rules(rules_dir):
        names.add(rule.id)
        if covers:
            names.update(rule.covers)
    return names


def rule_ids_used_as_tags(records: list[dict], namespace: set[str],
                          declared: set[str]) -> list[str]:
    """Tags that are declared RULE ids the vocabulary does not declare.

    A name that is BOTH a rule id and a declared tag (`lang-conventions`) is
    the shape tags.yaml allows on purpose, so it is exempt. What this reports
    is a rule id arriving in a record's `tags:` with nobody having declared it
    as a class name — a reviewer reaching for the rule's id where the
    vocabulary wanted the class the rule covers. A `left_undeclared:` receipt
    does not exempt it: declaring the id would split the class's recall key,
    and an alias for it is refused because the name is a rule id.

    Records are expected CANONICALIZED, like every other reader of this store.
    """
    seen: set[str] = set()
    for record in records:
        for tag in (record.get("tags") or []):
            if tag in namespace and tag not in declared:
                seen.add(str(tag))
    return sorted(seen)


def audit(root: Path, records: list[dict]) -> dict:
    """Vocabulary health over the corpus."""
    used: list[str] = []
    for r in records:
        used += r.get("tags", []) or []
    vocab = load_vocab(root)
    counts: dict[str, int] = {}
    for t in used:
        counts[t] = counts.get(t, 0) + 1
    problems = alias_problems(root)
    ceiling = ceiling_status(root, records)
    return {
        "declared": sorted(vocab),
        "used": dict(sorted(counts.items())),
        "unknown": sorted({t for t in used if vocab and t not in vocab}),
        "near_duplicates": near_duplicates(list(counts)),
        # A declared tag nothing uses is dead vocabulary — worth pruning, but
        # only the retro should propose that.
        "unused_declared": sorted(t for t in vocab if t not in counts),
        "invalid_aliases": problems,
        # The drift ceiling, so every reader of the audit sees
        # the same answer the exit code is computed from.
        "ceiling": ceiling,
    }


def render_audit(doc: dict) -> str:
    lines = ["tag vocabulary"]
    if not doc["declared"]:
        lines.append("  no vocabulary declared (.warden/memory/tags.yaml) — "
                     "tags are free-form and may fragment silently")
    else:
        lines.append(f"  declared: {len(doc['declared'])} · "
                     f"in use: {len(doc['used'])}")
    if doc["unknown"]:
        lines.append("  UNKNOWN (used but undeclared): "
                     + ", ".join(doc["unknown"]))
    if doc["near_duplicates"]:
        pairs = ", ".join(f"{a} ~ {b}" for a, b in doc["near_duplicates"])
        lines.append(f"  NEAR-DUPLICATES (same idea, two keys — recall splits "
                     f"between them): {pairs}")
    if doc["unused_declared"]:
        lines.append("  declared but unused: " + ", ".join(doc["unused_declared"]))
    if doc.get("invalid_aliases"):
        lines.append("  INVALID ALIASES (declared but never applied): "
                     + "; ".join(doc["invalid_aliases"]))
    lines += _render_ceiling(doc.get("ceiling") or {})
    return "\n".join(lines) + "\n"


def _render_ceiling(status: dict) -> list[str]:
    """The drift ceiling, rendered whether or not it is in breach.

    A ceiling nobody can see in the report is a ceiling nobody knows they are
    near, so the clean case prints too — the breach line is not the only time
    a reader needs the number.
    """
    lines = []
    # `labelled`, not `complaints`: three of these four complaint classes are
    # NOT the ceiling, and prefixing them with it would assert a declaration
    # that may not exist. Never rendered as headroom either way — a
    # declaration that cannot be read is reported as unreadable, and the reader
    # is told which declaration.
    labelled = status.get("labelled")
    if labelled is None:
        # A status dict built by hand rather than by `ceiling_status` — the
        # source of each complaint is not knowable from here, so the one label
        # TRUE OF ALL FOUR sources is used. Never the ceiling's: that is the
        # mislabel. And never silence: dropping the line would
        # make an unreadable declaration render as health.
        labelled = [f"DECLARATION UNREADABLE — {p}"
                    for p in status.get("complaints") or ()]
    for problem in labelled:
        lines.append(f"  {problem}")
    at_bar = status.get("at_bar") or []
    if at_bar:
        # Reported, never enforced. Its job is to tell the retro which
        # `left_undeclared:` reasons have been outgrown by their own evidence.
        lines.append(
            f"  UNDECLARED AT THE DECLARATION BAR ({DECLARE_MIN_N}+ judged, "
            f"upheld more often than not — RE-READ each one's recorded "
            f"reason, it may have been outgrown): " + ", ".join(at_bar))
    undecided = status.get("undecided") or []
    if undecided:
        lines.append(
            f"  UNDECIDED (in the corpus, with no recorded disposition — "
            f"neither declared, folded, nor written down under "
            f"'{LEFT_KEY}:'): " + ", ".join(undecided))
    if status.get("ceiling") is None:
        return lines
    if status.get("breached"):
        lines.append(
            f"  CEILING BREACHED: {len(undecided)} undecided tag(s) against a "
            f"declared ceiling of {status['ceiling']} — declare each in "
            f".warden/memory/{VOCAB_FILE}, fold it, or record why it is left "
            f"under '{LEFT_KEY}:'. Recording costs one line and decides "
            f"nothing about the class")
    else:
        lines.append(f"  drift ceiling: {len(undecided)} undecided of "
                     f"{status['ceiling']} allowed")
    return lines
