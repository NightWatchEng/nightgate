"""Gap analysis: which guardrails this repo is missing, and on what evidence.

Three sources join here. The catalog says what guardrails exist and why
anyone believes they matter. The review corpus says what has actually gone
wrong HERE. `.warden/rules/` says what this repo already enforces. This module
reads all three, so a repo is checked against its own catalog without a human
remembering to type `warden catalog`.

**Evidence is not prior art, and the report never blurs them.** An entry the
corpus has records for is recommended ON EVIDENCE: this went wrong here, N
times. An entry with no local signal is recommended ON PRIOR ART: published
sources say it matters, and this repo has no opinion yet. Both are worth
reading; only the first has earned authority. Conflating them would let an
unevidenced rule borrow a measured one's standing, which is the most damaging
thing this command could do.

**Enforced means ENFORCING.** The `implements:` link is necessary but not
sufficient: a PAUSED rule is skipped by review.py before it can produce a
finding, so its entry is NOT reported enforced — it reopens into the gap
carrying the pause and the recorded reason. Reporting a paused rule's entry
as covered would be a fail-open in the one command whose job is to say what
this repo does not enforce.

**Every link is declared.** A rule names the catalog entry it `implements:`;
a catalog entry names the `corpus_classes:` it corresponds to. Nothing is
matched by name similarity — the catalog speaks CWE and OWASP, the corpus
speaks this repo's own defect vocabulary, and guessing the join is exactly
the unevidenced claim this report exists to replace.

**A recommendation states its cost.** Where an entry ships a mechanical
starter, the checks are RUN against the current tree and the hit count is
reported. A rule that fires noisily is worse than no rule, because it teaches
people to ignore the gate — so the noise is measured before adoption, not
discovered after. Where no starter exists the count is None, never 0: zero
would claim a clean tree nobody examined.
"""

from __future__ import annotations

import os
import re
import textwrap
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from . import backtest as backtest_mod
from .catalog import CATALOG_PATH, LANGS, Entry, load_catalog
from .memory import _EXAMINER_STATUSES, _JUDGED_STATUSES, _REVIEWER_STATUSES
from . import yamlio

# A finding that was JUDGED and upheld. memory.py separates these on purpose:
# `detected` is a deterministic checker firing — a fact, not a verdict — and
# it is excluded from _JUDGED_STATUSES there for exactly the reason it must be
# excluded here. The rationale calls these "judged record(s)", so counting a
# gate hit among them would make the sentence false.
_UPHELD_STATUSES = _REVIEWER_STATUSES & _JUDGED_STATUSES
from .rules import Rule, load_rules

# Enough recurrence to call a class evidenced rather than incidental. Matches
# the retro's candidate bar: below this, one reviewer's bad afternoon looks
# identical to a pattern.
EVIDENCE_MIN_N = 3

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".warden",
              "dist", "build", ".mypy_cache", ".pytest_cache"}
_SCANNED_SUFFIXES = {".py", ".sh", ".yml", ".yaml", ".toml", ".js", ".ts",
                     ".tsx", ".jsx", ".go", ".rb", ".java", ".sql", ".md"}
_MAX_SCAN_FILES = 4000
_SAMPLES = 3
# Files that QUOTE the patterns being measured. The catalog stores every
# starter's regex as data, so scanning it makes each starter fire on its own
# definition — the declared `self-reference` class, and a false hit count is
# worse than none because the whole point of measuring is to state a real
# noise cost. Rule files are already skipped with the rest of .warden/.
# The last two entries are TEST CORPORA: tests/test_adopted_rules.py pins
# fires/silent cases for this repo's adopted rules, and
# tests/test_catalog_starters.py holds the dangerous-spelling corpus every
# starter must catch. Both quote every pattern by
# construction, so scanning them counts fixtures as real hits — and those
# counts are what a consumer weighs when deciding whether to adopt.
#
# NO FIGURES ARE QUOTED HERE, deliberately: an inflation count written into a
# comment goes stale in the commit that grows the corpora. The measurement lives in
# tests/test_catalog_starters.py, which asserts these prefixes never reach the
# scan set at all; run it, or re-measure, rather than trusting prose.
_SELF_REFERENTIAL = ("warden/guardrails/", "warden/catalog.py",
                     "warden/advisor.py", "tests/test_catalog.py",
                     "tests/test_advisor.py", "tests/test_catalog_starters.py",
                     "tests/test_adopted_rules.py")


@dataclass(frozen=True)
class Recommendation:
    entry_id: str
    taxonomy: str
    name: str
    engine: str
    basis: str            # "evidence" | "prior-art" — never blurred
    score: float
    rationale: str        # one line, interrogable
    corpus_n: int
    measured_hits: int | None   # None = no mechanical starter, so unmeasurable
    hit_samples: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()   # why the count is not the rule's firing
    #                                 count — and why an entry an
    #                                 `implements:` link points at is in the
    #                                 gap anyway (its rule is paused)
    # The history backtest, when a window was requested: the
    # rule replayed over past commits, flags split against the revert record.
    # None means no backtest was run this call, NOT a clean history.
    backtest: dict | None = None

    def to_dict(self) -> dict:
        return {"entry_id": self.entry_id, "taxonomy": self.taxonomy,
                "name": self.name, "engine": self.engine, "basis": self.basis,
                "score": round(self.score, 3), "rationale": self.rationale,
                "corpus_n": self.corpus_n, "measured_hits": self.measured_hits,
                "hit_samples": list(self.hit_samples),
                "caveats": list(self.caveats), "backtest": self.backtest}


@dataclass(frozen=True)
class Enforced:
    entry_id: str
    rule_id: str

    def to_dict(self) -> dict:
        return {"entry_id": self.entry_id, "rule_id": self.rule_id}


@dataclass(frozen=True)
class Unsupported:
    rule_id: str
    reason: str

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "reason": self.reason}


ANSWERS_PATH = ".warden/catalog-answers.yaml"
_ANSWERS_VERSION = 1
_ANSWER_KEYS = {"id", "verdict", "reason", "bead", "decided_in"}
_VERDICTS = ("not-applicable", "deferred")


@dataclass(frozen=True)
class Answer:
    """A recorded decision about a catalog entry this repo does not enforce."""
    entry_id: str
    verdict: str
    reason: str
    bead: str = ""
    decided_in: str = ""
    # Measured hits for the entry's starter, re-computed on every run. None
    # when the entry ships no mechanical starter, so there is nothing to
    # measure — never 0, which would claim a clean tree nobody examined.
    hits: int | None = None
    # One of: no-starter | unmeasured | contradicted | clean. `contradicted`
    # is kept as a derived convenience for existing readers.
    state: str = "no-starter"

    @property
    def contradicted(self) -> bool:
        return self.state == "contradicted"

    def to_dict(self) -> dict:
        return {"entry_id": self.entry_id, "verdict": self.verdict,
                "reason": self.reason, "bead": self.bead,
                "decided_in": self.decided_in, "hits": self.hits,
                "state": self.state, "contradicted": self.contradicted}


def _parse_answers_file(path: Path):
    """One parse for both readers of `.warden/catalog-answers.yaml`.

    One parse keeps the two loaders from reading one file under two
    contradictory contracts — load_answers refusing a version it does not
    know while load_ceiling enforces a ceiling from the same refused file.
    Raises yaml.YAMLError/OSError; shape and version judgments stay with
    the callers, whose fail-closed directions differ.
    """
    return yamlio.load(path.read_text(), unique_keys=True) or {}


# One substance floor for every field that can quiet the gate — defined in
# declarative.py (the third consumer, the allow marker, lives there and
# advisor already sits above declarative in the import graph).
from .declarative import _argument_has_substance  # noqa: E402


def load_answers(root: Path) -> tuple[dict[str, Answer], list[str]]:
    """Read `.warden/catalog-answers.yaml`. Returns (answers, complaints).

    FAILS LOUD, NOT SILENT: an unreadable or malformed file yields NO answers
    and a complaint, so every entry falls back to UNANSWERED. That is the
    safe direction — the report gets noisier, never quieter. Treating an
    unreadable file as "everything is answered" would let a typo silence the
    whole gap, which is the failure this file exists to prevent, achieved by
    the file itself.
    """
    path = root / ANSWERS_PATH
    if not path.is_file():
        return {}, []                      # no answers declared: all unanswered
    try:
        raw = _parse_answers_file(path)
    except (yaml.YAMLError, OSError) as e:
        return {}, [f"{ANSWERS_PATH} could not be read ({e}) — every entry is "
                    f"reported UNANSWERED, which is louder than the truth, "
                    f"not quieter"]
    complaints: list[str] = []
    answers: dict[str, Answer] = {}
    if not isinstance(raw, dict):
        # A top-level list or scalar parses as valid YAML and then died on
        # .get — a traceback and exit 2, not the "noisier report" this
        # function's contract promises.
        return {}, [f"{ANSWERS_PATH} is not a mapping (got "
                    f"{type(raw).__name__}) — every entry is reported "
                    f"UNANSWERED"]
    # `version:` is declared in the shipped file and in the ADOPTING example,
    # and it is read so that a file written against a future schema does not
    # parse silently as v1. catalog.py validates exactly this field for the
    # catalog; this is the same contract on a file that can lower a gate.
    version = raw.get("version")
    if version is not None and version != _ANSWERS_VERSION:
        return {}, [f"{ANSWERS_PATH}: version {version!r} is not "
                    f"{_ANSWERS_VERSION} — refusing to read a schema this "
                    f"warden does not know"]
    items = raw.get("answers") or []
    if not isinstance(items, list):
        return {}, [f"{ANSWERS_PATH}: 'answers' is not a list (got "
                    f"{type(items).__name__})"]
    for i, item in enumerate(items):
        where = f"{ANSWERS_PATH} answer #{i + 1}"
        if not isinstance(item, dict):
            complaints.append(f"{where}: not a mapping")
            continue
        # UNKNOWN KEYS ARE REFUSED, not ignored. A founder who writes
        # `expires:` or `scope:` believing it narrows a waiver would have had
        # it silently dropped and the waiver made total. `attest.build`
        # already refuses extra keys for exactly this reason.
        unknown = set(item) - _ANSWER_KEYS
        if unknown:
            # repr, not the bare key: a YAML mapping key need not be a
            # string, and sorted() over a mix of str and int raises
            # TypeError — the refusal path crashing on exactly the input it
            # exists to refuse.
            complaints.append(
                f"{where}: unknown key(s) {sorted(map(repr, unknown))} — an "
                f"answer may "
                f"only carry {sorted(_ANSWER_KEYS)}. A constraint this loader "
                f"does not understand would be silently dropped and the "
                f"waiver made total")
            continue
        eid, verdict = item.get("id"), item.get("verdict")
        # Every scalar is type-checked, not just the containers: `reason: 42`
        # would crash on .strip(), a list `id` would die as a dict key, and an
        # int `id` would load CLEAN and then kill `sorted()` later — a crash
        # where the contract promises a complaint.
        if not isinstance(eid, str) or not eid.strip():
            complaints.append(f"{where}: 'id' must be a non-empty string")
            continue
        eid = eid.strip()
        if not isinstance(item.get("reason", ""), str):
            complaints.append(f"{where} ({eid}): 'reason' must be a string")
            continue
        reason = (item.get("reason") or "").strip()
        decided_in = item.get("decided_in", "")
        if not isinstance(decided_in, str):
            # Typed like `bead`: an untyped `decided_in: true` would reach the
            # JSON artifact as a boolean where a tracker id belongs.
            complaints.append(f"{where} ({eid}): 'decided_in' must be a string")
            continue
        if verdict not in _VERDICTS:
            complaints.append(
                f"{where} ({eid}): verdict {verdict!r} not in {list(_VERDICTS)}")
            continue
        # SUBSTANCE, not length: a len(reason) >= 40 floor is satisfied by
        # forty dots or a bare URL, so an entry could leave the gap, and lower
        # the number that gates, on punctuation. URLs are stripped BEFORE
        # counting: a link splits into plenty of distinct tokens. A
        # citation may SUPPORT an argument; it cannot be one.
        if not _argument_has_substance(reason):
            complaints.append(
                f"{where} ({eid}): reason is not an argument — say what makes "
                f"the class inapplicable, or what defers it, in a sentence a "
                f"later reader can disagree with")
            continue
        bead = item.get("bead")
        if not isinstance(bead, str):
            bead = ""
        if verdict == "deferred" and not bead.strip():
            # `bead: '   '` and `bead: true` both satisfy a bare truthiness
            # test, and the boolean would reach the JSON artifact as `true`.
            complaints.append(
                f"{where} ({eid}): a deferral needs a 'bead' naming a tracker "
                f"id, or it is just a gap with a nicer name")
            continue
        if eid in answers:
            # The EARLIER block wins: last-write-wins would silently drop the
            # stronger verdict and its tracker. A human reads the file
            # top-down, so the tool does too.
            complaints.append(
                f"{where} ({eid}): duplicate answer — the EARLIER block is in "
                f"force ({answers[eid].verdict}"
                + (f", bead {answers[eid].bead}" if answers[eid].bead else "")
                + f") and this one ({verdict}"
                + (f", bead {bead.strip()}" if bead.strip() else "")
                + ") is IGNORED. Appending a correction at the bottom does "
                  "not replace the block above it — edit that one.")
            continue
        answers[eid] = Answer(eid, verdict, reason, bead.strip(),
                              decided_in.strip())
    return answers, complaints


_CEILING_KEYS = {"max_unanswered", "rationale"}


def load_ceiling(root: Path) -> tuple[int | None, tuple[str, ...]]:
    """Read the declared gap ceiling from `.warden/catalog-answers.yaml`.

    Returns (max_unanswered, complaints). The fail-closed direction here is
    the opposite of load_answers': a ceiling that was DECLARED and cannot be
    read is a complaint that must stop the run, never silently "no ceiling" —
    a typo must not disarm the one deterministic obligation this file
    carries. An absent file or absent `ceiling:` key IS no
    ceiling: nothing was declared, so nothing is disarmed.

    The rationale is REQUIRED: a ceiling that is just today's count ratchets
    nothing, so the value must travel with its argument the way an answer
    travels with its reason.
    """
    path = root / ANSWERS_PATH
    if not path.is_file():
        return None, ()
    try:
        raw = _parse_answers_file(path)
    except (yaml.YAMLError, OSError) as e:
        return None, (f"{ANSWERS_PATH} could not be read ({e}) — if it "
                      f"declares a ceiling, that ceiling cannot be checked",)
    if not isinstance(raw, dict):
        # The absent-key case below is genuinely "nothing declared"; a file
        # that parses to the wrong SHAPE is not — the declaration may be
        # buried inside it, and "could not look" must not read as "no
        # ceiling".
        return None, (f"{ANSWERS_PATH} is not a mapping (got "
                      f"{type(raw).__name__}) — if it declares a ceiling, "
                      f"that ceiling cannot be checked",)
    version = raw.get("version")
    if version is not None and version != _ANSWERS_VERSION:
        # Same refusal load_answers makes, in the fail-closed direction a
        # gate needs: enforcing a ceiling from a schema this warden does not
        # know would apply v1 semantics to a declaration that may not carry
        # them.
        return None, (f"{ANSWERS_PATH}: version {version!r} is not "
                      f"{_ANSWERS_VERSION} — refusing to enforce a ceiling "
                      f"from a schema this warden does not know",)
    if "ceiling" not in raw:
        return None, ()
    ceiling = raw["ceiling"]
    where = f"{ANSWERS_PATH}: 'ceiling'"
    if not isinstance(ceiling, dict):
        return None, (f"{where} must be a mapping (got "
                      f"{type(ceiling).__name__})",)
    unknown = set(ceiling) - _CEILING_KEYS
    if unknown:
        # repr, for the reason above.
        return None, (f"{where}: unknown key(s) "
                      f"{sorted(map(repr, unknown))} — a "
                      f"constraint this loader does not understand would be "
                      f"silently dropped; it may only carry "
                      f"{sorted(_CEILING_KEYS)}",)
    value = ceiling.get("max_unanswered")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None, (f"{where}: 'max_unanswered' must be a non-negative "
                      f"integer (got {value!r})",)
    rationale = ceiling.get("rationale")
    if (not isinstance(rationale, str)
            or not _argument_has_substance(rationale)):
        # The same substance floor an answer's reason carries — a non-blank
        # check alone accepts `rationale: x`, and length-only checks get
        # gamed.
        return None, (f"{where}: 'rationale' must be an argument, not a "
                      f"token — say why the value is what it is, in a "
                      f"sentence a later reader can disagree with; a "
                      f"ceiling set to whatever today's count happens to "
                      f"be ratchets nothing",)
    return value, ()


# Caveats that mean the measurement for THIS entry was incomplete, as opposed
# to merely qualified. A scope:added note qualifies a real count; an
# unreadable file or an uncompilable pattern means the check did not run.
_BLOCKING_CAVEAT = ("unreadable", "did not compile")


def answer_state(entry: Entry, ans: "Answer", root: Path,
                 targets: list[Path]) -> tuple[int | None, str]:
    """Decide what the CODE says about an answer. One place, one rule.

    Returns (hits, state) where state is one of:

      no-starter     the entry ships nothing mechanical, so no measurement is
                     possible EVER. Disclosed, and the answer stands on its
                     argument — 25 of the 32 catalog entries are here.
      unmeasured     a measurement was possible and did not happen: the tree
                     scan hit its bound, or a file could not be read, or a
                     pattern did not compile. FAILS CLOSED — "could not look"
                     is not "clean", which is this repo's oldest doctrine.
      contradicted   a `not-applicable` verdict whose starter matches real
                     code. The answer is refuted.
      clean          measured, and the answer holds.

    ONE FUNCTION, ONE RULE. Three signals decide an answer and each is easy
    to compute and then discard: whether the answer can be refuted at all
    (it must be measured, not short-circuited before measure()); whether a
    refutation keeps the entry in the gated count; and whether the
    measurement itself FAILED — dropping measure()'s caveats would render a
    truncated scan as `(starter matches 0)`, a zero nobody measured. Keeping
    the decision in one function gives a new signal exactly one place to be
    ignored, and it is here.
    """
    if not (entry.starter and entry.starter.get("checks")):
        return None, "no-starter"
    if len(targets) >= _MAX_SCAN_FILES:
        # The scan is alphabetical, so a large tree can stop before it ever
        # reaches the directories a waiver is claiming are clean.
        return None, "unmeasured"
    hits, _samples, caveats = measure(entry, root, targets)
    if any(k in c for c in caveats for k in _BLOCKING_CAVEAT):
        return None, "unmeasured"
    if ans.verdict == "not-applicable" and hits:
        return hits, "contradicted"
    return hits, "clean"


@dataclass(frozen=True)
class Report:
    recommendations: tuple[Recommendation, ...] = ()
    enforced: tuple[Enforced, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()
    declined: tuple[str, ...] = ()
    answered: tuple[Answer, ...] = ()
    notes: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    # LAST on purpose: `recommend` constructs this positionally, so a field
    # inserted mid-list silently shifts every argument after it (`notes`
    # into `gap_known`, `limits` into `notes`).
    gap_known: bool = True
    # After gap_known for the same reason; recommend passes these by keyword.
    declared_ceiling: int | None = None
    ceiling_complaints: tuple[str, ...] = ()
    # (entry id, the languages its starter reads) for each entry set aside
    # because this repo declares none of them, and the catalog languages it
    # DOES declare. Passed by keyword, like the two above.
    set_aside: tuple[tuple[str, tuple[str, ...]], ...] = ()
    repo_langs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"schema": 1,
                "recommendations": [r.to_dict() for r in self.recommendations],
                "enforced": [e.to_dict() for e in self.enforced],
                "unsupported": [u.to_dict() for u in self.unsupported],
                "declined": list(self.declined),
                "answered": [a.to_dict() for a in self.answered],
                "unanswered": len(self.recommendations) if self.gap_known else None,
                "gap_known": self.gap_known,
                "declared_ceiling": self.declared_ceiling,
                "ceiling_complaints": list(self.ceiling_complaints),
                "notes": list(self.notes),
                "limits": list(self.limits),
                "repo_langs": list(self.repo_langs),
                "set_aside_by_language": {eid: list(langs)
                                          for eid, langs in self.set_aside}}


def _nested_checkout_dirs(root: Path) -> set[Path]:
    """Directories under `root` that are their own git checkout.

    A NESTED CHECKOUT is a full copy of a repo living inside another one: a
    git worktree, a submodule, a vendored clone. Scanning into one measures
    the same corpus N+1 times, so every starter's hit count is inflated by a
    factor nobody can see — and hit counts are what the advisor recommends
    guardrails from. A quiet measurement error in the thing that proposes
    guardrails is worse than a crash.

    `.git` in _SKIP_DIRS does NOT cover this. That entry matches a path
    PART, which works for a normal clone whose `.git` is a directory — but a
    git worktree's `.git` is a FILE, so the worktree's own contents carry no
    `.git` part and would be walked in full — parallel agent worktrees under
    .claude/worktrees/ are exactly this shape. The platform's own cage runs in
    a worktree, so the unattended loop can create this condition.
    """
    nested: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        # Check for `.git` BEFORE pruning _SKIP_DIRS. Pruning first removes
        # `.git` (it is in _SKIP_DIRS) from `dirnames` and so could only ever
        # see the FILE form — the clone-shaped nested checkout, whose `.git`
        # is a directory, would walk straight through.
        is_checkout = ".git" in dirnames or ".git" in filenames
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if here != root and is_checkout:
            nested.add(here)
            dirnames[:] = []          # do not descend into it
    return nested


def _scan_targets(root: Path, catalog_file: Path | None = None,
                  limits: list[str] | None = None) -> list[Path]:
    """Files to measure starters against.

    `catalog_file` is excluded by RESOLVED PATH, not by the prefix list: the
    prefixes only know where the shipped catalog lives, and a catalog read
    from anywhere else would still be measured against its own contents.
    """
    skip_exact = set()
    if catalog_file is not None:
        try:
            skip_exact.add(catalog_file.resolve())
        # warden:allow(except-pass): a failed resolve means the catalog file is scanned rather than skipped, the noisier direction never the quieter
        except OSError:
            pass
    nested = _nested_checkout_dirs(root)
    out: list[Path] = []
    truncated = False
    for path in sorted(root.rglob("*")):
        if any(parent in nested for parent in path.parents):
            continue
        if len(out) >= _MAX_SCAN_FILES:
            # sorted() means truncation is ALPHABETICAL, so a large tree can
            # stop before reaching warden/ or tests/ entirely. A silent cap
            # would make a partial scan read as a complete one.
            truncated = True
            break
        if not path.is_file() or path.suffix not in _SCANNED_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        try:
            if path.resolve() in skip_exact:
                continue
        # warden:allow(except-pass): a failed resolve means this file is scanned rather than skipped, the noisier direction never the quieter
        except OSError:
            pass
        rel = path.relative_to(root).as_posix()
        if any(rel.startswith(prefix) for prefix in _SELF_REFERENTIAL):
            continue
        out.append(path)
    if limits is not None:
        if nested:
            # The only OTHER dynamic narrowing of the scan set — truncation —
            # gets both a limit line and a fail-closed `unmeasured` verdict.
            # This one gets a limit line too, or it would silently remove an
            # arbitrary subtree from every hit count: a measurement that
            # changes without saying so.
            limits.append(
                "these nested checkouts were excluded — their files belong to "
                "another repo and would inflate every count below: "
                + ", ".join(sorted(d.relative_to(root).as_posix()
                                   for d in nested)))
        if truncated:
            limits.append(
                f"the tree scan stopped at its bound of {_MAX_SCAN_FILES} "
                "files, alphabetically — every match count below is over that "
                "subset, not the whole tree")
        limits.append(
            "only these file types are scanned: "
            + ", ".join(sorted(_SCANNED_SUFFIXES)))
        limits.append(
            "these directories are never scanned: "
            + ", ".join(sorted(_SKIP_DIRS)))
        limits.append(
            "these paths are excluded because they QUOTE the patterns being "
            "measured, which would make a starter fire on its own definition: "
            + ", ".join(_SELF_REFERENTIAL) + ", and the catalog file in use")
    return out


def measure(entry: Entry, root: Path,
            targets: list[Path]) -> tuple[int | None, tuple[str, ...], tuple[str, ...]]:
    """How many places in the CURRENT TREE this starter's patterns match.

    Returns (count, samples, caveats). None when there is nothing mechanical
    to run — reporting 0 there would claim a clean tree nobody examined, one
    level down from the same distinction the report makes at the top.

    WHAT THIS NUMBER IS, stated because the honest version is narrower than
    the useful-sounding one: it is the count of existing matches in the tree,
    NOT the number of findings the rule would produce on a diff. The engine
    that runs a copied starter (`declarative.run`) honours `scope`, `globs`,
    `flags` and `require`; this honours the first three and refuses the
    fourth. The gap that remains is `scope: added`, which fires only on lines
    a DIFF adds — an existing match is a place the rule COULD fire, not a
    finding it will. Every caveat is returned rather than absorbed.
    """
    if entry.engine != "declarative" or not entry.starter:
        return None, (), ()
    from .rules import glob_match

    hits: list[str] = []
    caveats: list[str] = []
    for check in entry.starter.get("checks", []):
        if check.get("require"):
            # require:true fires when the pattern is ABSENT. Counting presences
            # would report the exact inverse of the rule's behaviour.
            caveats.append(f"check {check.get('id')!r} uses require:true and is "
                           "not measurable by counting matches — excluded")
            continue
        bits = 0
        for flag in check.get("flags", []):
            bits |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}.get(flag, 0)
        try:
            pattern = re.compile(check["pattern"], bits)
        except re.error as e:
            caveats.append(f"check {check.get('id')!r} did not compile ({e}) — "
                           "excluded from the count")
            continue
        scope = check.get("scope", "added")
        if scope == "added":
            caveats.append(f"check {check.get('id')!r} is scope:added, so the "
                           "rule fires only on lines a DIFF adds, so the "
                           "match count reported for it is existing matches "
                           "in the tree — an upper bound, not a firing count")
        globs = check.get("globs") or []
        for path in targets:
            rel = path.relative_to(root).as_posix()
            if globs and not any(glob_match(g, rel) for g in globs):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as e:
                caveats.append(f"{rel} unreadable ({e}) — not scanned")
                continue
            if scope == "file":
                # declarative.run yields at most ONE finding per file here.
                match = pattern.search(text)
                if match:
                    hits.append(f"{rel}:{text.count(chr(10), 0, match.start()) + 1}")
                continue
            for match in pattern.finditer(text):
                hits.append(f"{rel}:{text.count(chr(10), 0, match.start()) + 1}")
    return len(hits), tuple(hits[:_SAMPLES]), tuple(dict.fromkeys(caveats))


def _upheld(record: dict) -> bool:
    """Did this finding survive judgment?

    A refuted or dismissed-with-reason record is the corpus arguing the class
    DOWN. Counting it as evidence FOR a recommendation would let a class this
    repo rejected three times outrank every prior-art entry — the reader-side
    version of manufacturing a score out of the wrong denominator. The status
    split is memory.py's, imported rather than re-typed.
    """
    return (record.get("status") or "") in _UPHELD_STATUSES


def _corpus_index(records: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    """defect-class slug -> (upheld count, judged-against count)."""
    upheld: dict[str, int] = {}
    against: dict[str, int] = {}
    for record in records:
        status = record.get("status") or ""
        if status in _UPHELD_STATUSES:
            bucket = upheld
        elif status in _EXAMINER_STATUSES:
            bucket = against
        else:
            continue
        slugs = set(record.get("tags") or ())
        rule_id = record.get("rule_id") or ""
        if rule_id.startswith("unmapped:"):
            slugs.add(rule_id[len("unmapped:"):])
        for slug in slugs:
            bucket[slug] = bucket.get(slug, 0) + 1
    return upheld, against


def _rule_record_counts(records: list[dict]) -> dict[str, int]:
    """rule id -> UPHELD findings. A rule whose entire history is refutation
    has nothing behind it, and belongs in the unsupported list rather than
    being shielded from it by the very findings that argued it down."""
    counts: dict[str, int] = {}
    for record in records:
        rule_id = record.get("rule_id") or ""
        if rule_id and not rule_id.startswith("unmapped:") and _upheld(record):
            counts[rule_id] = counts.get(rule_id, 0) + 1
    return counts


def _score(basis: str, corpus_n: int, hits: int | None) -> float:
    """Evidence outranks prior art, always. Within each, recurrence leads.

    A flat ordering on taxonomy position would rank a CWE-79 rule above a
    boring convention rule in a repo that renders no HTML — the bead's own
    example of the wrong answer.
    """
    score = 100.0 if basis == "evidence" else 0.0
    score += min(corpus_n, 50) * 2.0
    if hits:
        # Firing on real lines today is applicability evidence in its own
        # right, but weaker than a judged finding: a hit may be a false
        # positive, and that is precisely what has not been judged yet.
        score += min(hits, 20) * 0.5
    return score


def _pause_note(rule_id: str, reason: str, *, reopened: bool) -> str:
    """Why an entry an `implements:` link points at is NOT enforced.

    The outcome is state-dependent, so the sentence must be too. An entry that
    builds a recommendation row really does land in the gap; one on the
    declined (`engine: not-a-rule`) or answered-clean path `continue`s before
    a row exists and is NOT counted in UNANSWERED — and those two paths are
    the only reason the LIMITS line exists. Claiming "counted UNANSWERED"
    there would put the report's own header (`UNANSWERED: 0`) in direct
    contradiction with its LIMITS line.
    """
    why = reason.strip() or "no reason recorded"
    outcome = ("so the entry reopens into the gap" if reopened else
               "so the entry is not enforced, though it is answered or "
               "declined here rather than counted in UNANSWERED")
    return (f"rule {rule_id!r} implements this entry but is PAUSED ({why}) — "
            f"a paused rule produces no findings, {outcome}")


# `lang:` is free text in repo.yaml's schema, and a hand-written one says
# what its author calls the language — `python3.12`, `python-3.12`,
# `CPython-3.11`, `TypeScript`, `node.js`, `golang 1.22`. A spelling is read
# as its WORDS: runs of letters, where a word may end in `++` or `#` (`c++`,
# `c#`). Anything else separates words, so `python3.12` is the word `python`
# and `typescript+python` is two words. A word is matched whole, so `gopher`
# is not go and `pythonic` is not python.
_CATALOG_WORDS = {
    "python": "python", "cpython": "python", "pypy": "python", "py": "python",
    "node": "node", "nodejs": "node", "javascript": "node", "js": "node",
    "typescript": "node", "ts": "node", "deno": "node", "bun": "node",
    "go": "go", "golang": "go", "java": "java",
}
# Words that name something no catalog starter reads. A spelling made only
# of these declares a known other language, which is what lets a node repo
# with a `lang: md` docs component still set its Python-idiom entries aside.
_OTHER_WORDS = frozenset((
    "md", "markdown", "docs", "doc", "text", "txt", "rst", "asciidoc",
    "html", "css", "scss", "sass", "yaml", "yml", "json", "toml", "xml",
    "shell", "sh", "bash", "zsh", "fish", "powershell", "sql",
    "terraform", "hcl", "docker", "dockerfile", "make", "makefile", "nix",
    "proto", "protobuf", "graphql", "rust", "kotlin", "scala",
    "groovy", "ruby", "php", "swift", "c", "c++", "cpp", "c#", "csharp",
    "f#", "dotnet", "elixir", "erlang", "haskell", "ocaml", "clojure",
    "dart", "lua", "perl", "r", "julia", "zig", "nim", "solidity",
))
_LANG_WORD = re.compile(r"[a-z]+(?:\+\+|#)?")


def _read_lang(spelling: str) -> frozenset[str] | None:
    """The catalog languages `spelling` names, or None when it is unread.

    Every word must be a catalog word or a known other one; a single word
    that is neither makes the whole spelling unread. `Go/Django` could be a
    Django (Python) component, and the reader cannot tell a harmless
    qualifier from a language it does not know, so `Python/Django` is None
    as well, as are `cobol-ng` and `3.12`. The caller then sets nothing
    aside. Otherwise every catalog
    language a word names is read, so `TypeScript/Python` is both, and a
    spelling of known other words alone is an empty set.
    """
    words = _LANG_WORD.findall(spelling.lower())
    if not words or any(w not in _CATALOG_WORDS and w not in _OTHER_WORDS
                        for w in words):
        return None
    return frozenset(_CATALOG_WORDS[w] for w in words if w in _CATALOG_WORDS)


def _catalog_lang(spelling: str) -> str | None:
    return min(_read_lang(spelling) or (), default=None)


def declared_languages(root: Path) -> tuple[frozenset[str], str]:
    """The catalog languages repo.yaml's components declare, and a problem.

    Lenient, like `config.declared_rules_dir`, and for the same reason: the
    advisor holds a root, not a validated config. An empty set means the
    filter cannot know which languages apply, and the caller then sets
    NOTHING aside — a row too many is the safe direction for a report whose
    job is the gap. `problem` is non-empty when repo.yaml could not be read,
    its components are not a mapping, or any component's language cannot be
    read (no `lang` string, or one `_read_lang` cannot classify), so the
    caller can say why. An unread component fails closed: the set is then
    EMPTY, never the other components' languages, because the unread one
    may be the language a set-aside entry reads.
    """
    from .config import CONFIG_NAME, read_repo_yaml
    try:
        doc = yamlio.load(read_repo_yaml(root).decode("utf-8"))
    except FileNotFoundError:
        return frozenset(), f"no {CONFIG_NAME}"
    except (OSError, ValueError, yaml.YAMLError) as e:
        return frozenset(), f"{CONFIG_NAME} cannot be read ({e})"
    comps = doc.get("components") if isinstance(doc, dict) else None
    if not isinstance(comps, dict):
        return frozenset(), f"{CONFIG_NAME} declares no components mapping"
    langs: set[str] = set()
    unread = []
    for comp, spec in comps.items():
        lang = spec.get("lang") if isinstance(spec, dict) else None
        read = _read_lang(lang) if isinstance(lang, str) else None
        if read is None:
            unread.append(f"component {str(comp)!r} declares " + (
                f"lang {lang!r}, which this reader cannot classify"
                if isinstance(lang, str) else "no lang string"))
        else:
            langs |= read
    if unread:
        return frozenset(), (f"{'; '.join(unread)} (catalog languages: "
                             f"{', '.join(LANGS)})")
    return frozenset(langs), ""


def _matches_nothing_here(entry: Entry, root: Path,
                          targets: list[Path]) -> bool:
    """True only when setting `entry` aside hides no match in THIS tree.

    A starter's patterns are written for one language's idioms, but nothing
    confines them to it: js-yaml's load call and the md5 package's are
    JavaScript the Python checks match, and a node repo can carry loose `.py`
    scripts that `warden init` never declared. So an entry is set aside only on a MEASURED zero, and the
    measurement fails closed exactly as `answer_state` does — a truncated
    scan, an unreadable file or an uncompilable pattern keeps the row. An
    entry whose starter is a python-engine SKETCH has no pattern to run: what
    it reads is prose in its language's idioms, and there is nothing in any
    tree for it to match.
    """
    if not (entry.starter and entry.starter.get("checks")):
        return True
    if len(targets) >= _MAX_SCAN_FILES:
        return False
    hits, _samples, caveats = measure(entry, root, targets)
    if any(k in c for c in caveats for k in _BLOCKING_CAVEAT):
        return False
    return not hits


def recommend(root: Path, *, catalog_path: Path | None = None,
              records: list[dict] | None = None,
              rules_dir: Path | None = None,
              corpus_unread: str = "",
              backtest_window: int | None = None,
              paused_extra: frozenset[str] | set[str] | None = None) -> Report:
    """`corpus_unread`, when set, says the corpus could not be read.

    `paused_extra` names rules to treat as PAUSED for this run without
    touching a file — how `warden autonomy pause` learns what a pause would
    cost BEFORE writing it. Measuring the cost by editing the
    rule file first is the opposite of a guard: the breach would already be on
    disk by the time the guard could see it. It names RULES, not entries, and
    a rule nobody declares changes nothing, so a typo cannot manufacture a
    breach and refuse a pause that is fine.


    Without it a caller that hands over an empty list gets rows reading "no
    local evidence (0 corpus record(s))" — a measurement that never happened,
    written into a committed artifact as though it had. Same distinction the
    miner draws between a real zero and UNREAD, one command over.
    """
    root = Path(root)
    entries = load_catalog(catalog_path)
    # ABOVE the ruleset read: a malformed answers file must produce its
    # diagnostic even when the rules are also unreadable, so a reader meets
    # both problems in one run.
    answers, answer_complaints = load_answers(root)
    ceiling, ceiling_complaints = load_ceiling(root)
    if rules_dir is None:
        # The DECLARED dir, not a hardcoded default: a pinned `.warden/rules`
        # on a consumer layout (`rules_dir: policy/rules`) would read a rule
        # the gate enforces as absent and recommend its entry as a gap.
        # A declaration that
        # cannot be READ fails closed exactly like an unreadable ruleset —
        # enforcement claims about a guessed dir are claims about a ruleset
        # that may not be the repo's.
        from .config import declared_rules_dir

        rules_dir, dir_problem = declared_rules_dir(root)
        if dir_problem:
            return Report(
                notes=(f"the rules-dir declaration could not be read "
                       f"({dir_problem}), so nothing is reported as enforced "
                       "and no gap is claimed",),
                limits=tuple(answer_complaints), gap_known=False,
                declared_ceiling=ceiling,
                ceiling_complaints=ceiling_complaints)
    try:
        declared: tuple[Rule, ...] = load_rules(rules_dir)
    except Exception as e:  # noqa: BLE001 — an unreadable ruleset must not be
        # reported as "this repo enforces nothing", which would recommend
        # every entry in the catalog with false confidence. load_rules raises
        # a path-anchored, actionable message; discarding it leaves the one
        # place a reader would act on with nothing to act on.
        return Report(notes=(f"rules could not be read ({e}), so nothing is "
                             "reported as enforced and no gap is claimed",),
                      limits=tuple(answer_complaints), gap_known=False,
                      declared_ceiling=ceiling,
                      ceiling_complaints=ceiling_complaints)
    if records is None:
        records = []

    # A PAUSED rule enforces NOTHING: review.py skips it before it can
    # produce a finding, so crediting its `implements:` entry as ALREADY
    # ENFORCED would be a fail-open in the one command whose job is "what does
    # this repo not enforce". The entry REOPENS into the gap, and the
    # pause is named rather than merely dropped, or the reader loses the trail
    # from "why is this row back" to "because that rule is paused".
    implemented: dict[str, str] = {}
    paused_by: dict[str, tuple[str, str]] = {}
    simulated = frozenset(paused_extra or ())
    for rule in declared:
        for entry_id in rule.implements:
            if rule.paused or rule.id in simulated:
                reason = (rule.paused_reason if rule.paused
                          else "simulated: this pause has not been written")
                paused_by.setdefault(entry_id, (rule.id, reason))
            else:
                implemented.setdefault(entry_id, rule.id)
    # An entry some ACTIVE rule also implements is still enforced, and must
    # not be reopened by a second, paused rule that names it.
    paused_by = {eid: v for eid, v in paused_by.items() if eid not in implemented}

    by_class, argued_against = _corpus_index(records)
    by_rule = _rule_record_counts(records)
    limits: list[str] = []
    targets = _scan_targets(root, catalog_file=catalog_path or CATALOG_PATH,
                            limits=limits)

    # The history backtest reads git ONCE for the whole run,
    # not per entry. If git cannot be read, every backtest is UNAVAILABLE with
    # the reason — never a fabricated number, and the gap is a limit note.
    bt_commits = bt_defects = None
    bt_error = ""
    if backtest_window is not None:
        try:
            bt_commits = backtest_mod.read_commit_additions(root, backtest_window)
            bt_defects = backtest_mod.read_reverted_shas(root)
        except backtest_mod.BacktestError as e:
            bt_error = str(e)
            limits.append(f"history backtest could not run ({bt_error}) — every "
                          "recommendation below is reported UNBACKTESTED, not "
                          "as a clean history")

    recs: list[Recommendation] = []
    enforced: list[Enforced] = []
    declined: list[str] = []
    set_aside: list[tuple[str, tuple[str, ...]]] = []
    repo_langs, langs_problem = declared_languages(root)
    if not repo_langs and any(e.langs for e in entries):
        why = langs_problem or ("no component declares a language the "
                                f"catalog tags ({', '.join(LANGS)})")
        limits.append(f"{why}, so no entry is set aside by language — every "
                      "row below is shown whatever language its starter reads")

    limits.extend(answer_complaints)
    # Reported here as well as on the row, because the row is not guaranteed
    # to exist: an entry the catalog calls NOT-A-RULE is declined and an
    # answered-clean entry is skipped, and in both paths the pause would
    # otherwise vanish from the report entirely.
    # Deferred until the entry loop has run: an entry that built a
    # recommendation row already carries the note as a caveat, and emitting
    # both would print the identical sentence twice, verbatim.
    reopened_ids: set[str] = set()
    # An answer for something already enforced, or for an entry the catalog
    # no longer ships, is stale — reported so the file cannot quietly rot.
    for eid in sorted(answers):
        if eid in implemented:
            limits.append(f"{ANSWERS_PATH}: {eid} is ANSWERED but also "
                          f"enforced by a rule — the answer is stale")
        elif eid not in {e.id for e in entries}:
            limits.append(f"{ANSWERS_PATH}: {eid} is not a catalog entry")
    answered: list[Answer] = []

    for entry in entries:
        if entry.id in implemented:
            enforced.append(Enforced(entry.id, implemented[entry.id]))
            continue
        if entry.id in answers and entry.id not in implemented:
            # An answer is a record, not a mute — so it stays CONTRADICTABLE.
            # Short-circuiting here, BEFORE measure(), would delete the only
            # mechanical signal that could refute it: a `not-applicable`
            # verdict would survive forever even once the class started firing
            # on real code. Measure first, then decide.
            ans = answers[entry.id]
            hits, state = answer_state(entry, ans, root, targets)
            if state == "contradicted":
                limits.append(
                    f"{ANSWERS_PATH}: {entry.id} is answered "
                    f"'not-applicable' but its starter matches {hits} "
                    f"place(s) in the tree — the answer is CONTRADICTED by "
                    f"the code and must be re-argued or withdrawn")
            elif state == "unmeasured":
                limits.append(
                    f"{ANSWERS_PATH}: {entry.id}'s answer could NOT be "
                    f"checked this run (the scan was truncated, or a file or "
                    f"pattern could not be read) — counted as unanswered, "
                    f"because 'could not look' is not 'clean'")
            answered.append(replace(ans, hits=hits, state=state))
            if state in ("clean", "no-starter"):
                continue
            # contradicted / unmeasured fall THROUGH into the gap.
            # Safe for deferrals by construction: `contradicted` is only ever
            # set for a `not-applicable` verdict.

        if entry.engine == "not-a-rule":
            # Recommending a rule for a class the catalog says cannot be one
            # would be the checklist-of-things-that-technically-pass failure
            # the NOT-A-RULE class exists to prevent.
            declined.append(entry.id)
            continue

        off_language = bool(repo_langs and entry.langs
                            and not repo_langs & set(entry.langs))
        # Set aside only what nothing else claims. An ANSWERED entry reached
        # here because its answer was contradicted or unmeasured, and that
        # must count in the gap; a PAUSED rule's entry reopens into it; and a
        # starter that matches this tree is evidence the idiom is here,
        # whatever the repo declares. Named, never dropped.
        if (off_language and entry.id not in answers
                and entry.id not in paused_by
                and _matches_nothing_here(entry, root, targets)):
            set_aside.append((entry.id, entry.langs))
            continue

        corpus_n = sum(by_class.get(slug, 0) for slug in entry.corpus_classes)
        refuted_n = sum(argued_against.get(slug, 0) for slug in entry.corpus_classes)
        hits, samples, caveats = measure(entry, root, targets)
        if entry.id in paused_by:
            caveats = (_pause_note(*paused_by[entry.id], reopened=True),
                       ) + caveats
            reopened_ids.add(entry.id)
        if off_language:
            caveats = (f"its starter reads {'/'.join(entry.langs)}, which "
                       f"this repo does not declare "
                       f"({', '.join(sorted(repo_langs))}) — kept because it "
                       f"is answered, its rule is paused, or the starter "
                       f"matches this tree or could not be measured",
                       ) + caveats
        basis = "evidence" if corpus_n >= EVIDENCE_MIN_N else "prior-art"

        if basis == "evidence":
            classes = ", ".join(entry.corpus_classes)
            rationale = (f"{corpus_n} judged record(s) in this repo's corpus "
                         f"under {classes}")
        else:
            rationale = (f"no local evidence ({corpus_n} corpus record(s)) — "
                         f"recommended from published prior art, {entry.taxonomy}")
        if refuted_n:
            rationale += (f"; {refuted_n} finding(s) in this class were refuted "
                          "or dismissed and are NOT counted above")
        if hits is not None:
            rationale += (f"; its pattern matches {hits} existing place(s) in "
                          "the tree")

        bt_result = None
        if backtest_window is not None:
            if bt_error:
                bt_result = backtest_mod.Backtest(
                    engine=entry.engine, backtestable=False, window="",
                    commits_scanned=0, flagged=0, true_positive=0,
                    false_positive=0, projected_fp_rate=None,
                    verdict="unbacktested",
                    reason=f"history unreadable ({bt_error})").to_dict()
            else:
                bt_result = backtest_mod.backtest_starter(
                    entry, root, backtest_window,
                    commits=bt_commits, defect_shas=bt_defects).to_dict()

        recs.append(Recommendation(
            entry_id=entry.id, taxonomy=entry.taxonomy, name=entry.name,
            engine=entry.engine, basis=basis,
            score=_score(basis, corpus_n, hits), rationale=rationale,
            corpus_n=corpus_n, measured_hits=hits, hit_samples=samples,
            caveats=caveats, backtest=bt_result))

    recs.sort(key=lambda r: (-r.score, r.entry_id))

    unsupported: list[Unsupported] = []
    for rule in declared:
        if rule.implements:
            continue
        if by_rule.get(rule.id):
            continue
        if any(by_class.get(slug) for slug in rule.covers):
            continue
        unsupported.append(Unsupported(
            rule.id,
            "no catalog entry declares it and the corpus holds no judged "
            "finding under it — review it, do not assume it is dead"))

    notes = ["prior-art rows are NOT evidence: they say published sources "
             "consider the class important and this repo has no judged "
             "finding either way"]
    if corpus_unread:
        notes.insert(0, (
            "THE CORPUS WAS NOT READ (" + corpus_unread + ") — every row below "
            "says 'no local evidence' because nothing could be looked up, NOT "
            "because this repo has no history. No row here is a measurement."))
    notes = tuple(notes)
    # Only entries that built no recommendation row need a LIMITS line: the
    # ones that did already carry the note as a caveat, and printing both
    # would repeat the identical sentence verbatim.
    for eid in sorted(set(paused_by) - reopened_ids):
        limits.append(f"{eid}: {_pause_note(*paused_by[eid], reopened=False)}")

    return Report(tuple(recs), tuple(enforced), tuple(unsupported),
                  tuple(declined), tuple(answered), notes,
                  tuple(limits), declared_ceiling=ceiling,
                  ceiling_complaints=ceiling_complaints,
                  set_aside=tuple(set_aside),
                  repo_langs=tuple(sorted(repo_langs)))


def render(report: Report) -> str:
    lines = ["warden rules recommend — what this repo does not enforce"]
    if report.notes:
        lines.append("")
        for note in report.notes:
            lines.append(f"  NOTE: {note}")

    evidence = [r for r in report.recommendations if r.basis == "evidence"]
    prior = [r for r in report.recommendations if r.basis == "prior-art"]

    # The headline. Everything below is detail;
    # THIS is the number that must not quietly grow. It sits at the top
    # because a count buried under 14 rows is a count nobody reads.
    total = len(report.recommendations)
    lines.append("")
    if not report.gap_known:
        lines.append("UNANSWERED: UNKNOWN — the ruleset could not be read, so "
                     "the gap was never computed. This is NOT zero.")
    else:
        lines.append(f"UNANSWERED: {total} — applicable, unenforced, and no "
                     f"decision on record")
    if total and report.gap_known:
        lines.append(f"  Reading this does not shrink it. Adopt the entry, or "
                     f"record why not in {ANSWERS_PATH}.")
    if report.gap_known and report.declared_ceiling is not None:
        lines.append(f"  Declared ceiling: {report.declared_ceiling} — "
                     f"`rules recommend` exits 1 above it; the rationale "
                     f"lives in {ANSWERS_PATH}")
    for complaint in report.ceiling_complaints:
        # "GAP CEILING", not "CEILING". This is the GUARDRAIL-GAP ceiling in
        # `.warden/catalog-answers.yaml`, and the repo has a second, unrelated
        # thing called a ceiling — the tag-vocabulary drift ceiling in
        # `.warden/memory/tags.yaml`. The complaints here speak for exactly the
        # ceiling the label claims, and the label says which, so a reader does
        # not conflate two ceilings in two files.
        lines.append(f"  GAP CEILING UNREADABLE ({ANSWERS_PATH}): {complaint}")

    lines.append("")
    if not report.gap_known:
        # Under a headline that says "this is NOT zero", the sub-sections
        # must not assert measured zeros from a path that computed nothing —
        # the real-zero-vs-UNREAD distinction this module is built around.
        lines.append("EVIDENCE-BASED (—) · PRIOR ART (—) — not computed; see "
                     "the note above")
        for limit in report.limits:
            lines.append(f"  - {limit}")
        return "\n".join(lines) + "\n"
    lines.append(f"EVIDENCE-BASED ({len(evidence)}) — this went wrong HERE")
    if not evidence:
        lines.append("  none: no unenforced catalog entry has judged records "
                     "in this corpus")
    for rec in evidence:
        lines.append(f"  {rec.entry_id} [{rec.taxonomy}, engine:{rec.engine}]")
        lines.append(f"      {rec.rationale}")
        for sample in rec.hit_samples:
            lines.append(f"      e.g. {sample}")
        for caveat in rec.caveats:
            lines.append(f"      caveat: {caveat}")
        if rec.backtest:
            lines.append(f"      {backtest_mod.render_from_dict(rec.backtest)}")

    lines.append("")
    lines.append(f"PRIOR ART ({len(prior)}) — published sources, no local verdict")
    if (report.repo_langs and "python" not in report.repo_langs
            and any(r.engine == "python" for r in report.recommendations)):
        # On a repo with no Python, `engine:python` reads as "a Python rule".
        lines.append("  engine:python names the language the CHECKER is "
                     "written in (a warden checker), not the language it "
                     "reads")
    for rec in prior:
        lines.append(f"  {rec.entry_id} [{rec.taxonomy}, engine:{rec.engine}]")
        lines.append(f"      {rec.rationale}")
        for sample in rec.hit_samples:
            lines.append(f"      e.g. {sample}")
        for caveat in rec.caveats:
            lines.append(f"      caveat: {caveat}")
        if rec.backtest:
            lines.append(f"      {backtest_mod.render_from_dict(rec.backtest)}")

    if report.enforced:
        lines.append("")
        lines.append(f"ALREADY ENFORCED ({len(report.enforced)})")
        for item in report.enforced:
            lines.append(f"  {item.entry_id} -> rule '{item.rule_id}'")

    if report.answered:
        lines.append("")
        lines.append(f"ANSWERED ({len(report.answered)}) — considered and "
                     f"decided, with the reason on record")
        for a in sorted(report.answered, key=lambda x: x.entry_id):
            tail = f" [{a.bead}]" if a.bead else ""
            flag = ("  ** CONTRADICTED BY THE CODE **" if a.contradicted
                    else "  ** UNVERIFIED **" if a.state == "unmeasured"
                    else "")
            # An entry with no measurable starter cannot be contradicted at
            # all, and a blank suffix read as "measured, clean". 25 of the 32
            # catalog entries are in that state — including every
            # engine:claude one, the most expensive classes to silence — so
            # the row says so rather than letting silence imply a check.
            seen = {
                "clean": f"  (starter matches {a.hits})",
                "contradicted": f"  (starter matches {a.hits})",
                "no-starter": ("  (NOT MECHANICALLY CHECKABLE — no starter; "
                               "this verdict rests entirely on its argument)"),
                "unmeasured": ("  (NOT CHECKED THIS RUN — the scan could not "
                               "reach it; counted as unanswered)"),
            }[a.state]
            lines.append(f"  {a.entry_id} — {a.verdict}{tail}{seen}{flag}")
            # WRAPPED, never truncated. A reason's tail often carries the flip
            # condition the answers file's own header makes mandatory, or the
            # sentence saying what would END a deferral. The claim is that the
            # argument travels with the entry; truncation of any length
            # contradicts it.
            # break_long_words=False: a citation URL chopped mid-token
            # cannot be copied, and a reason's whole job is to be followable.
            for line in textwrap.wrap(" ".join(a.reason.split()), width=96,
                                      break_long_words=False,
                                      break_on_hyphens=False):
                lines.append(f"      {line}")
        lines.append("  an answer is a RECORD, not an exemption. An entry "
                     "with a mechanical starter is re-measured every run, and "
                     "a 'not-applicable' verdict whose starter starts "
                     "matching is CONTRADICTED — counted as unanswered again "
                     "and shown here with its refutation. An entry with NO "
                     "starter cannot be checked that way at all: it rests on "
                     "its argument and on review. An answer the scan could "
                     "not REACH this run is counted as unanswered rather "
                     f"than believed. See {ANSWERS_PATH}.")

    if report.set_aside:
        declared = ", ".join(report.repo_langs)
        lines.append("")
        lines.append(f"OTHER LANGUAGES ({len(report.set_aside)}) — each "
                     f"starter reads a language this repo does not declare "
                     f"(it declares {declared}), so none is recommended")
        lines.append("  " + ", ".join(f"{eid} [{'/'.join(langs)}]"
                                      for eid, langs in report.set_aside))
        lines.append(f"  The class may still matter here: no {declared} "
                     "starter exists for these yet. `warden catalog show "
                     "<id>` says what each one guards.")

    if report.declined:
        lines.append("")
        lines.append(f"NOT A RULE ({len(report.declined)}) — the catalog says no "
                     "engine can check these; see `warden catalog show <id>`")
        lines.append("  " + ", ".join(report.declined))

    if report.limits:
        lines.append("")
        lines.append("LIMITS — what the match counts above do and do not cover:")
        for limit in report.limits:
            lines.append(f"  - {limit}")

    if report.unsupported:
        lines.append("")
        lines.append(f"UNSUPPORTED RULES ({len(report.unsupported)}) — enforced "
                     "here, nothing behind them")
        for item in report.unsupported:
            lines.append(f"  {item.rule_id}: {item.reason}")
    return "\n".join(lines)
