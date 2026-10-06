"""This repo's own tag vocabulary, guarded against the merge that would
silently detach a rule from its evidence.

WHY THIS FILE EXISTS: merging the `overbroad-pattern` tag into
`false-positive` via an `aliases:` entry looks like vocabulary tidying, but
that class is the declared rule `pattern-fit` (`covers: [overbroad-pattern]`,
with `unmapped:overbroad-pattern` grandfathered in `.warden/certification.yaml`).

An alias validated only against the TAG vocabulary would accept that merge.
`tags.canonicalize_records` folds `unmapped:<slug>` rule ids through the same
table, so the fold would rewrite every `unmapped:overbroad-pattern` record to
`unmapped:false-positive`: the rule's judged records detach from the rule they
justify, `memory stats` starts
proposing a rule that already shipped, and the weaker class's precision is
laundered by the stronger class's clean records. `warden certify` stays
LEVEL 5 throughout — nothing in the ladder notices.

The loader closes that hole: `_split_aliases` resolves the repo's
declared `review.rules_dir` from the root it is already given, and refuses an
alias whose name or target is a declared rule id or a `covers:` class.

The vocabulary keeps its own guard here anyway, at the file the retro edits.
A refused alias is INVALID config that never fires — correct, but it is
reported in an audit line nobody has to read. These tests fail the suite, on
the offending line, at the moment the retro writes it.
"""

import json
import os
import re
import subprocess
import types
from pathlib import Path

import pytest
import yaml
from conftest import why_a_ci_job_might_not_run

from warden import cli
from warden import rules as rules_mod
from warden import tags as tags_mod
from warden import vocabulary as vocab_mod
from warden.memory import _JUDGED_STATUSES, records_from_shards
from private_evidence import needs_corpus, needs_receipts

ROOT = Path(__file__).parent.parent
RULES_DIR = ROOT / ".warden" / "rules"

# Tags the 2026-08-29 retro declared, each of which cleared the judged-record
# floor when it was declared. Counts are DERIVED from the
# committed shards below, never read from this table — the table only says
# which names have to keep earning their declaration.
DECLARED_BY_RETRO = ("overbroad-pattern", "breaking-consumer-migration",
                     "evidence-attribution")

# Declared by the 2026-09-20 re-read: four
# `left_undeclared:` receipts whose evidence had outgrown the count they
# recorded, each superseded by a decision shard. Same table discipline as the
# one above — it says which names must keep earning their declaration, and
# every count is derived from the shards.
DECLARED_BY_THE_RECEIPT_RE_READ = ("incomplete-cause-list", "unanchored-pattern",
                              "vacuous-guard", "reader-divergence")

# The bar a tag clears to be declared: the candidate bar, 3+ judged cases and
# more upheld than not. Not the promotion bar — naming a class is cheaper than
# automating one.
#
# IMPORTED, never re-typed. The at-bar report reads this same
# bar, and a second bar drawn beside the first is the shape
# `unmapped:candidate-bar-parity-partial` records: two readers of one rule,
# agreeing on the day they are written and diverging on the first edit.
MIN_JUDGED = tags_mod.DECLARE_MIN_N


def _rule_namespace() -> set[str]:
    """Every key an `unmapped:<slug>` fold could collide with: a declared rule
    id, and every class a declared rule `covers:`. Read through
    `tags.rule_namespace`; `attest write` reads its ids alone, and agrees
    here because every covered class is a declared tag (guarded below)."""
    return tags_mod.rule_namespace(RULES_DIR)


def test_no_alias_name_or_target_collides_with_the_rule_namespace():
    """No alias may fold a declared rule id or a class a rule covers.

    An alias whose NAME is a covered class folds that class's committed
    records off the rule that answers them; an alias whose TARGET is one folds
    foreign records onto it. `_split_aliases` refuses both; this
    is the independent second reading, against `load_rules` directly, and it
    says which alias is wrong instead of leaving one that quietly never fires.
    """
    reserved = _rule_namespace()
    offenders = []
    for name, target in tags_mod.load_aliases(ROOT).items():
        if name in reserved:
            offenders.append(
                f"{name} -> {target}: alias NAME {name!r} is a declared rule "
                "id or a covers: class — folding it detaches that rule's "
                "committed evidence")
        if target in reserved:
            offenders.append(
                f"{name} -> {target}: alias TARGET {target!r} is a declared "
                "rule id or a covers: class — folding onto it attributes "
                "foreign records to that rule")
    assert not offenders, "; ".join(offenders)


def test_declared_aliases_are_all_applicable():
    """An alias the loader refuses is dead config that reads as a merge —
    `render_audit` reports it as INVALID ALIASES and nothing else acts."""
    _, problems = tags_mod._split_aliases(ROOT)
    assert not problems, f"invalid aliases in tags.yaml: {problems}"


@pytest.mark.parametrize("tag", DECLARED_BY_RETRO
                         + DECLARED_BY_THE_RECEIPT_RE_READ)
@needs_corpus
def test_a_retro_declared_tag_keeps_its_evidence(tag):
    """A declaration is a claim about the corpus, so it is derived from the
    corpus rather than typed: a count typed into a test rots.
    """
    vocab = tags_mod.load_vocab(ROOT)
    assert tag in vocab, (
        f"{tag!r} was declared by the 2026-08-29 retro and is no longer in "
        ".warden/memory/tags.yaml — undeclaring it re-opens the UNKNOWN warning")
    judged = upheld = 0
    for rec in records_from_shards(ROOT):
        if tag not in (rec.get("tags") or []):
            continue
        if rec.get("status") not in _JUDGED_STATUSES:
            continue
        judged += 1
        if rec.get("status") in ("fixed", "confirmed"):
            upheld += 1
    assert judged >= MIN_JUDGED, (
        f"{tag}: {judged} judged record(s) in the committed shards, below the "
        f"{MIN_JUDGED}-case declaration bar this retro cited")
    assert upheld * 2 > judged, (
        f"{tag}: {upheld} upheld of {judged} judged — a declared class must be "
        "upheld more often than not")


def test_folding_a_covered_class_detaches_its_records(tmp_path):
    """The mechanism the guards above exist for, proved in isolation: an alias
    whose name is a class a rule `covers:` rewrites that class's `unmapped:`
    rule ids, so the records stop counting toward the rule.

    Staged in a repo with NO declared ruleset — nothing for the alias to
    collide with, so the fold happens and the damage is visible — then the
    covering rule ships and the same alias is refused. The
    pair is the argument for reading the ruleset: the tag table alone cannot
    tell these two repos apart.
    """
    mem = tmp_path / ".warden" / "memory"
    mem.mkdir(parents=True)
    (mem / "tags.yaml").write_text(
        "tags:\n"
        "  false-positive: a rule firing on something it was never meant to catch\n"
        "aliases:\n"
        "  overbroad-pattern: false-positive\n")
    applicable, problems = tags_mod._split_aliases(tmp_path)
    assert not problems and applicable == {"overbroad-pattern": "false-positive"}

    records = [{"rule_id": "unmapped:overbroad-pattern",
                "tags": ["overbroad-pattern"]}]
    folded = tags_mod.canonicalize_records(tmp_path, records)
    assert folded[0]["rule_id"] == "unmapped:false-positive"
    assert folded[0]["tags"] == ["false-positive"]

    # The rule that makes the class a rule's evidence ships:
    rules_dir = tmp_path / ".warden" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "pattern-fit.md").write_text(
        "---\nid: pattern-fit\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\ncovers: [overbroad-pattern]\n---\nbody\n')
    applicable, problems = tags_mod._split_aliases(tmp_path)
    assert applicable == {} and len(problems) == 1
    assert "pattern-fit" in problems[0]
    unfolded = tags_mod.canonicalize_records(
        tmp_path, [{"rule_id": "unmapped:overbroad-pattern",
                    "tags": ["overbroad-pattern"]}])
    assert unfolded[0]["rule_id"] == "unmapped:overbroad-pattern"


def test_alias_guard_holds_a_paused_rules_covered_class(tmp_path):
    """The alias guard asks an IDENTITY question, not an enforcement one: a
    pause is reversible, and aliases fold at every read seam, so an alias
    admitted while the rule is paused would fold the class's records one day
    and unfold them the day the rule unpauses — recall flipping with pause
    state while the declared table never changed. A paused rule still owns its
    class, so the alias stays refused (deliberately `include_paused=True`, the
    same identity reading as ingest's legacy-id migration)."""
    mem = tmp_path / ".warden" / "memory"
    mem.mkdir(parents=True)
    (mem / "tags.yaml").write_text(
        "tags:\n"
        "  false-positive: a rule firing on something it was never meant to catch\n"
        "aliases:\n"
        "  overbroad-pattern: false-positive\n")
    rules_dir = tmp_path / ".warden" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "pattern-fit.md").write_text(
        "---\nid: pattern-fit\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\ncovers: [overbroad-pattern]\n'
        "paused: true\npaused_reason: refutation streak\n---\nbody\n")
    applicable, problems = tags_mod._split_aliases(tmp_path)
    assert applicable == {} and len(problems) == 1
    assert "pattern-fit" in problems[0]
    unfolded = tags_mod.canonicalize_records(
        tmp_path, [{"rule_id": "unmapped:overbroad-pattern",
                    "tags": ["overbroad-pattern"]}])
    assert unfolded[0]["rule_id"] == "unmapped:overbroad-pattern"


def test_every_covered_class_is_a_declared_tag():
    """A rule's `covers:` slug is the name the rule itself tells reviewers to
    file the class under, so a slug absent from the declared vocabulary makes
    `warden memory ingest` WARN on every finding tagged with the rule's own
    name. Reviewers then improvise a neighbouring tag and the class's recall
    key splits — exactly the failure tags.yaml's header warns about — while
    the warning noise trains everyone to ignore the one unknown-tag warning
    that matters. Nothing else in the tree rejects a covered class that the
    vocabulary does not declare.
    """
    declared = set(tags_mod.load_vocab(ROOT))
    missing = sorted(
        f"{slug!r} (covered by rule {rule.id!r})"
        for rule in rules_mod.load_rules(RULES_DIR)
        for slug in rule.covers if slug not in declared)
    assert not missing, (
        "classes declared by rule covers: but absent from the tag vocabulary "
        f"(.warden/memory/tags.yaml): {'; '.join(missing)} — declare each so "
        "findings can be filed under the name the rule itself uses")


# The derivation lives in warden (`tags.rule_ids_used_as_tags`) so that
# `attest write` refuses the same shape at the write seam this guard reads
# at pre-push. Imported, never re-typed.
_rule_ids_used_as_tags = tags_mod.rule_ids_used_as_tags


@needs_corpus
def test_no_corpus_tag_is_an_undeclared_rule_id():
    """No committed record carries an undeclared rule id as a tag.

    A reviewer can tag findings with the RULE ids `pattern-fit` and
    `error-names-cause` instead of the class names those rules cover
    (`overbroad-pattern`, `ambiguous-diagnostic`). The tag ceiling alone does
    not stop that: it counts an undeclared name, but it says nothing about
    WHICH name, so the remedy it prints ("declare it or fold it") is the wrong
    remedy for this shape — declaring splits the class's recall key and the
    fold is refused outright, because the name is a rule id.

    Neither name is in the committed corpus, so this guard is vacuous on this
    tree today and would stay green if it were written wrong. The control
    below is therefore the load-bearing half: it drives the same derivation
    with a synthetic record and watches it report.
    """
    namespace = _rule_namespace()
    declared = set(tags_mod.load_vocab(ROOT))
    records = tags_mod.canonicalize_records(ROOT, records_from_shards(ROOT))
    offenders = _rule_ids_used_as_tags(records, namespace, declared)
    assert not offenders, (
        f"rule ids used as tags in the committed corpus: {offenders} — file "
        "the finding under the class the rule `covers:`, not under the rule's "
        "own id; declaring the id would split that class's recall key and an "
        "alias for it is refused because the name is a rule id")

    # The control. A guard that has never failed proves nothing about what it
    # guards, and this one cannot fail on the tree it ships with.
    assert "pattern-fit" in namespace, (
        "the rule namespace no longer contains `pattern-fit` — re-derive this "
        "control against a rule id that exists")
    assert "pattern-fit" not in declared, (
        "`pattern-fit` is now a DECLARED tag as well as a rule id. That is the "
        "`lang-conventions` shape and it is legal, but this control has to "
        "move to a rule id that is not declared, or it measures nothing")
    assert _rule_ids_used_as_tags(
        [_rec("pattern-fit")], namespace, declared) == ["pattern-fit"], (
        "the derivation does not report a rule id used as a tag")
    # …and the exemption still exempts, so the guard cannot start refusing the
    # one shape tags.yaml deliberately allows.
    assert "lang-conventions" in namespace and "lang-conventions" in declared, (
        "`lang-conventions` is no longer both a rule id and a declared tag — "
        "the exemption arm below now measures nothing")
    assert not _rule_ids_used_as_tags(
        [_rec("lang-conventions")], namespace, declared), (
        "a name that is BOTH a rule id and a declared tag is reported — that "
        "is the shape tags.yaml declares on purpose")


@needs_receipts
def test_both_rule_ids_carry_a_recorded_reason_and_no_alias():
    """The disposition for rule ids used as tags, pinned so it cannot be
    quietly reversed into the fold that is mechanically refused.

    `warden decide list` carries the ruling. The short of it: an alias is
    refused for each name on the NAME side (the name is a declared rule id),
    declaring the name splits a recall key that already exists, so a
    `left_undeclared:` receipt is the only disposition left.

    THE COVERED CLASS IS DERIVED, never typed. Hard-coding the pair
    `("error-names-cause", "ambiguous-diagnostic")` and asking only whether
    the second element is A declared tag would pass a receipt naming the
    wrong class, and stay green in both directions under a mutated `covers:`.
    So it reads `load_rules`, the way
    `test_every_covered_class_is_a_declared_tag` does, and requires the
    receipt to NAME what the rule actually covers.
    """
    left, problems = tags_mod.load_left_undeclared(ROOT)
    assert not problems, problems
    aliases = tags_mod.applicable_aliases(ROOT)
    declared = set(tags_mod.load_vocab(ROOT))
    by_id = {r.id: r for r in rules_mod.load_rules(RULES_DIR)}
    for name in ("pattern-fit", "error-names-cause"):
        assert name in by_id, (
            f"{name!r} is no longer a declared rule, so the receipt for it is "
            "about nothing — re-read the entry rather than deleting it")
        covers = by_id[name].covers
        assert len(covers) == 1, (
            f"rule {name!r} now covers {covers!r}; its receipt names one "
            "class, so decide which and rewrite the receipt with it")
        covered = covers[0]
        assert name in left, (
            f"{name!r} lost its recorded reason in tags.yaml — it is a rule "
            "id a review round used as a tag, and the receipt is what tells "
            "the next reviewer to file under the class instead")
        assert name not in declared and name not in aliases, (
            f"{name!r} is now declared or folded; both were ruled out by "
            "the vocabulary ruling and the fold is refused by the loader anyway")
        assert covered in declared, (
            f"the class {covered!r} that rule {name!r} covers is no longer a "
            f"declared tag, so the receipt for {name!r} points at nothing")
        # The ATTRIBUTING phrase, not a mention. The error-names-cause receipt
        # names two declared classes — the covered `diagnosability` and the
        # `ambiguous-diagnostic` it must not be confused with — so on THAT
        # receipt `covered in reason` is satisfied whichever way `covers:`
        # points, and a mutation that swaps them survives it. The
        # pattern-fit receipt names `overbroad-pattern` alone, so the
        # membership test WOULD fail on it under the swap; the phrase
        # check is the right shape for both anyway, because it binds `covers:`
        # to the class a receipt ATTRIBUTES rather than to any it mentions,
        # and a receipt that later names a neighbour keeps the bind
        # (`test_the_membership_test_was_blind_on_exactly_one_receipt` below
        # measures which receipt is which).
        claim = f"covers `{covered}`"
        others = [c for c in declared if c != covered
                  and f"covers `{c}`" in left[name]]
        assert claim in left[name], (
            f"the receipt for {name!r} does not say {claim!r}. `load_rules` "
            f"says that rule covers {covered!r}; the receipt reads "
            f"{left[name]!r}. A receipt that routes the next reviewer to the "
            "wrong class is the split-recall harm it exists to prevent")
        assert not others, (
            f"the receipt for {name!r} attributes `covers:` to {others} as "
            f"well as to {covered!r} — one rule, one covered class, one "
            "sentence saying which")


@needs_receipts
def test_the_evidence_attribution_fold_is_refused_by_the_rule_covering_it(
        tmp_path):
    """`unsupported-source-claim -> evidence-attribution` folded one judged
    record onto that candidate row for as long as no rule answered the class.
    A rule's `covers:` puts its class INTO the rule namespace, so the alias
    would now fold onto `unmapped:evidence-attribution` — a key `attest write`
    refuses — and the loader refuses the alias instead. Refused config that
    reads as a live merge is what this file's neighbouring guard forbids, so
    the entry is gone and a receipt carries the name, the same disposition the
    two rule-id receipts have.

    THE COVERING RULE IS DERIVED, never typed: read from `load_rules`, so the
    receipt stays bound to the rule under a rename and this fails if the class
    stops being covered at all.
    """
    by_class = {slug: rule.id for rule in rules_mod.load_rules(RULES_DIR)
                for slug in rule.covers}
    assert "evidence-attribution" in by_class, (
        "no declared rule covers `evidence-attribution` any more — if the "
        "rule was retired the fold re-opens, and the receipt below is stale")
    covering = by_class["evidence-attribution"]

    assert "unsupported-source-claim" not in tags_mod.applicable_aliases(ROOT), (
        "the fold is live while a rule covers the class it targets — it would "
        "move that record's `unmapped:` id onto a key attest refuses to write")
    left, problems = tags_mod.load_left_undeclared(ROOT)
    assert not problems, problems
    assert "unsupported-source-claim" in left, (
        "`unsupported-source-claim` is a tag in the committed corpus with no "
        "disposition left: not declared, no longer folded, and no receipt — "
        "the drift ceiling counts it undecided and the suite goes red")
    assert f"`{covering}`" in left["unsupported-source-claim"], (
        f"the receipt does not name {covering!r}, the rule whose `covers:` is "
        "what refuses the fold — a reader cannot check the stated reason")

    # The control. Everything above passes on a tree where the alias was
    # simply never written, so it cannot show that COVERING the class is what
    # refuses it. Restore the alias beside the real rule file and watch the
    # loader refuse it, naming that rule.
    mem = tmp_path / ".warden" / "memory"
    mem.mkdir(parents=True)
    (mem / "tags.yaml").write_text(
        "tags:\n  evidence-attribution: evidence cited to a source or actor "
        "that does not support it\n"
        "aliases:\n  unsupported-source-claim: evidence-attribution\n")
    applicable, alias_problems = tags_mod._split_aliases(tmp_path)
    assert applicable == {"unsupported-source-claim": "evidence-attribution"}, (
        "with no ruleset to collide with, the fold is what it always was")
    rules_dir = tmp_path / ".warden" / "rules"
    rules_dir.mkdir(parents=True)
    source = {r.id: r for r in rules_mod.load_rules(RULES_DIR)}[covering]
    (rules_dir / source.path.name).write_text(source.path.read_text())
    applicable, alias_problems = tags_mod._split_aliases(tmp_path)
    assert applicable == {}, "the covering rule does not refuse the fold"
    assert len(alias_problems) == 1 and covering in alias_problems[0], (
        f"the refusal does not name {covering!r}: {alias_problems}")


@needs_receipts
def test_the_membership_test_was_blind_on_exactly_one_receipt():
    """The comment above says WHICH receipt the retired `covered in reason`
    test could not distinguish, and this measures it: a receipt is
    indistinguishable under a swapped `covers:` only when it names more than
    one declared class. That is exactly the
    error-names-cause receipt, and not the pattern-fit one."""
    left, problems = tags_mod.load_left_undeclared(ROOT)
    assert not problems, problems
    declared = set(tags_mod.load_vocab(ROOT))
    named = {name: {c for c in declared if f"`{c}`" in left[name]}
             for name in ("pattern-fit", "error-names-cause")}
    assert named["pattern-fit"] == {"overbroad-pattern"}, named
    assert named["error-names-cause"] == {"diagnosability",
                                          "ambiguous-diagnostic"}, named
    blind = {name for name, classes in named.items() if len(classes) > 1}
    assert blind == {"error-names-cause"}, (
        f"the receipts naming more than one declared class are {blind}; the "
        "comment on the attributing-phrase check says which receipt the "
        "membership test could not distinguish, and it has to say this one")


# ── The drift ceiling ───────────────────────────────────────────────────────
#
# Reconciling the vocabulary reactively does not hold. The detector prints
# the undeclared names on every `memory stats`; what it cannot supply is an
# obligation to answer them.
#
# WHAT THE CEILING BOUNDS is the count of tags carrying NO recorded
# disposition — neither declared, nor folded, nor written down under
# `left_undeclared:`. It is NOT the count of undeclared classes AT the
# declaration bar: new names reach the bar in bursts (one review round can
# take two brand-new names past it together), so that count moves no slower
# than the raw one, it forces a taxonomy argument inside the PR that coined
# the names, and it leaves the undeclared total bounded by nothing.
# Recording a receipt decides nothing about a class, so the undecided count
# can sit at 0 with no ratchet pressure.


def _vocab_repo(tmp_path, body: str) -> Path:
    mem = tmp_path / ".warden" / "memory"
    mem.mkdir(parents=True)
    (mem / "tags.yaml").write_text(body)
    return tmp_path


def _rec(tag, status: str = "fixed", rule_id: str = "some-rule") -> dict:
    """One record. `tag` may be a str or a list, so the multi-tag path is
    exercised rather than assumed."""
    return {"rule_id": rule_id, "status": status,
            "tags": [tag] if isinstance(tag, str) else list(tag)}


# A rationale that actually clears `_argument_has_substance` (8+ words over
# two characters, 6+ distinct). Every case below that is testing something
# OTHER than the rationale carries it, or the loader would complain about the
# rationale and the case would pass for a reason unrelated to its own claim —
# the guard-narrower-than-claim shape, in the guard for the ceiling.
_ARGUES = ("frozen at the count that exists today so that any further drift "
           "has to be answered rather than quietly absorbed")


def test_a_declared_ceiling_loads_with_its_rationale(tmp_path):
    root = _vocab_repo(tmp_path, (
        "tags:\n  fail-open: errs toward passing\n"
        f"ceiling:\n  {tags_mod.CEILING_LIMIT}: 2\n"
        f"  rationale: {_ARGUES}\n"))
    assert tags_mod.load_ceiling(root) == (2, ())


@pytest.mark.parametrize("body,why", [
    ("tags:\n  fail-open: x\n",
     "no ceiling: key at all is nothing declared, not a breach"),
    ("", "an empty file declares nothing"),
])
def test_no_declaration_is_no_ceiling_not_a_complaint(tmp_path, body, why):
    assert tags_mod.load_ceiling(_vocab_repo(tmp_path, body)) == (None, ()), why


def test_an_absent_vocabulary_file_declares_no_ceiling(tmp_path):
    assert tags_mod.load_ceiling(tmp_path) == (None, ())


@pytest.mark.parametrize("body,why", [
    ("tags: [\n",
     "unparseable YAML: `_load_doc` swallows YAMLError and returns {}, which "
     "would read as 'no ceiling declared' — a typo must not disarm the one "
     "deterministic obligation this file carries"),
    (f"- ceiling:\n    max_undecided: 0\n    rationale: {_ARGUES}\n",
     "a top-level LIST parses fine and is not a mapping; the declaration may "
     "be buried in it, so 'could not look' must not read as 'no ceiling'"),
    ("ceiling: 3\n", "a scalar ceiling is not a mapping"),
    (f"ceiling:\n  max_undecided: 1\n  rationale: {_ARGUES}\n"
     "  max_unknown: 4\n",
     "an unknown key is a constraint this loader would silently drop"),
    (f"ceiling:\n  max_undecided: 1\n  rationale: {_ARGUES}\n"
     "  7: seven\n  zzz: z\n",
     "YAML keys need not be strings, and sorting a str/int mix raises "
     "TypeError — the refusal path used to crash on the very input it "
     "refuses (round-1 review, F2)"),
    (f"ceiling:\n  rationale: {_ARGUES}\n",
     "a ceiling with no number bounds nothing"),
    (f"ceiling:\n  max_undecided: -1\n  rationale: {_ARGUES}\n",
     "a negative ceiling can never be met and is a typo, not a policy"),
    (f"ceiling:\n  max_undecided: true\n  rationale: {_ARGUES}\n",
     "bool is an int subclass in Python; `true` must not read as 1"),
    (f"ceiling:\n  max_undecided: '2'\n  rationale: {_ARGUES}\n",
     "a quoted number is a string and compares nothing"),
    ("ceiling:\n  max_undecided: 1\n",
     "a ceiling with no rationale ratchets nothing"),
    ("ceiling:\n  max_undecided: 1\n  rationale: x\n",
     "a token is not an argument — the substance floor an answer's reason "
     "carries, because length-only checks get gamed"),
])
def test_a_broken_ceiling_declaration_is_a_complaint_never_no_ceiling(
        tmp_path, body, why):
    """FAIL CLOSED, in the direction a gate needs. Returning (None, ()) here
    would let one typo silence the obligation — the failure the ceiling exists
    to prevent, achieved by the ceiling itself."""
    value, complaints = tags_mod.load_ceiling(_vocab_repo(tmp_path, body))
    assert value is None and complaints, why


def test_a_vocabulary_file_that_cannot_be_decoded_is_a_complaint(tmp_path):
    """`read_text` raises UnicodeDecodeError — a ValueError, not an OSError —
    so an except tuple naming only OSError would let it escape as a
    traceback through every caller."""
    root = _vocab_repo(tmp_path, "tags: {}\n")
    (root / ".warden" / "memory" / "tags.yaml").write_bytes(b"tags:\n  a: \xff\xfe\n")
    value, complaints = tags_mod.load_ceiling(root)
    assert value is None and complaints and "could not be read" in complaints[0]


def test_a_vocabulary_file_that_cannot_be_opened_is_a_complaint(tmp_path):
    """The `OSError` arm of the same except tuple. A permission error is the
    one an operator actually hits — a 0600 file owned by CI, read by a
    different uid — and it must fail closed like the decode arm rather than
    escape as a traceback."""
    root = _vocab_repo(tmp_path, "tags: {}\nceiling:\n  max_undecided: 0\n")
    doc = root / ".warden" / "memory" / "tags.yaml"
    doc.chmod(0o000)
    if os.access(doc, os.R_OK):  # pragma: no cover - root ignores the mode
        pytest.skip("running as root: chmod cannot make a file unreadable")
    try:
        value, complaints = tags_mod.load_ceiling(root)
    finally:
        doc.chmod(0o644)
    assert value is None, "an unreadable declaration is never headroom"
    assert complaints and "could not be read" in complaints[0]


def test_a_declaration_that_is_not_a_regular_file_is_not_no_declaration(
        tmp_path):
    """A dangling symlink and a directory both make `is_file()` False, so both
    could render byte-identically to a repo that declared nothing — the
    reader could not tell "declared nothing" from "could not resolve it"."""
    mem = tmp_path / ".warden" / "memory"
    mem.mkdir(parents=True)
    (mem / "tags.yaml").symlink_to(mem / "gone.yaml")
    dangling = tags_mod.load_ceiling(tmp_path)
    assert dangling[0] is None and dangling[1], "a dangling link is not silence"
    other = tmp_path / "other"
    (other / ".warden" / "memory" / "tags.yaml").mkdir(parents=True)
    a_dir = tags_mod.load_ceiling(other)
    assert a_dir[0] is None and a_dir[1], "a directory is not silence"
    absent = tags_mod.load_ceiling(tmp_path / "nothing-here")
    assert absent == (None, ()), "genuine absence still reads as silence"
    # `VOCABULARY UNREADABLE`, not `CEILING UNREADABLE`. The FILE is what
    # cannot be read here, so whether a ceiling is declared cannot be known at
    # all — demanding the renderer say "declared" would assert a declaration
    # nobody can see. One shared labeller (`tags.label_complaint`) answers
    # which declaration a complaint speaks for, for all three renderers at
    # once.
    assert "VOCABULARY UNREADABLE" in tags_mod.render_audit(
        tags_mod.audit(tmp_path, [_rec("anything")]))


# ── what counts as decided ─────────────────────────────────────────────────


_LEFT_OK = ("tags:\n  fail-open: errs toward passing\n"
            "left_undeclared:\n"
            "  seen-once: >-\n"
            "    coined once this round at n=1, nothing else uses it yet, so "
            "there is nothing to decide about the class today\n")


def test_a_recorded_reason_discharges_a_name_and_a_token_does_not(tmp_path):
    root = _vocab_repo(tmp_path, _LEFT_OK)
    left, problems = tags_mod.load_left_undeclared(root)
    assert set(left) == {"seen-once"} and not problems
    records = [_rec("seen-once"), _rec("never-mentioned")]
    assert tags_mod.undecided_tags(root, records) == ["never-mentioned"]


@pytest.mark.parametrize("body,why", [
    ("tags:\n  fail-open: x\nleft_undeclared:\n  a: todo\n",
     "a token is not a decision — the same substance floor a ceiling "
     "rationale and a catalog answer carry"),
    ("tags:\n  fail-open: x\nleft_undeclared:\n  a: 7\n",
     "a non-string reason is not a decision"),
    ("tags:\n  fail-open: x\nleft_undeclared: [a, b]\n",
     "the block must be a mapping of tag -> reason"),
    ("tags:\n  fail-open: errs toward passing\n"
     "left_undeclared:\n  fail-open: >-\n"
     "    a name cannot be declared and left undeclared at the same time, so "
     "this entry contradicts the declaration above it\n",
     "a DECLARED name here is a contradiction, not a decision"),
    ("tags:\n  fail-open: errs toward passing\n"
     "aliases:\n  failing-open: fail-open\n"
     "left_undeclared:\n  failing-open: >-\n"
     "    the fold already discharges this name, so recording it again says "
     "two different things about one tag\n",
     "a FOLDED name here is a contradiction, not a decision"),
])
def test_a_left_undeclared_entry_that_decides_nothing_is_refused(
        tmp_path, body, why):
    left, problems = tags_mod.load_left_undeclared(_vocab_repo(tmp_path, body))
    assert problems, why
    assert "a" not in left and "fail-open" not in left


def test_a_refused_alias_does_not_make_a_receipt_a_contradiction(tmp_path):
    """`load_left_undeclared` reads the APPLICABLE alias table, as every
    folding seam does, not the RAW one. A refused alias folds nothing, so a
    receipt for that name is exactly the right thing to have — a raw read
    would call it a contradiction, tell the author to delete the only line
    clearing the breach, and leave the breach unclearable."""
    root = _vocab_repo(tmp_path, (
        "tags:\n  fail-open: errs toward passing\n"
        # Refused: the target is not declared, so this alias never fires.
        "aliases:\n  orphan: not-a-declared-class\n"
        "left_undeclared:\n  orphan: >-\n"
        "    seen once, and the alias written for it is refused by the loader, "
        "so this receipt is what actually discharges the name\n"))
    assert tags_mod.applicable_aliases(root) == {}, "the alias is refused"
    left, problems = tags_mod.load_left_undeclared(root)
    assert not problems, problems
    assert "orphan" in left
    assert tags_mod.undecided_tags(root, [_rec("orphan")]) == []


def test_a_malformed_receipts_reason_reaches_the_caller(tmp_path):
    """The left-block complaint propagation in `ceiling_status` is the ONLY
    path by which a malformed receipt's reason reaches any caller — the
    render, the CLI, and the guard all read it from there — so deleting it
    must turn this red."""
    root = _vocab_repo(tmp_path, (
        "tags:\n  fail-open: errs toward passing\n"
        "left_undeclared:\n  seen-once: todo\n"
        f"ceiling:\n  {tags_mod.CEILING_LIMIT}: 0\n"
        f"  rationale: {_ARGUES}\n"))
    status = tags_mod.ceiling_status(root, [_rec("seen-once")])
    assert any("not a decision" in p for p in status["complaints"]), (
        "the reason the receipt was rejected must travel, or the author is "
        "told only that they are in breach and not why the line they wrote "
        "does not clear it")
    assert status["breached"], "a rejected receipt discharges nothing"
    # `RECEIPTS UNREADABLE`, not `CEILING UNREADABLE`: the ceiling here is
    # declared and perfectly readable, and it is the RECEIPT that was
    # rejected, so labelling it as the ceiling would send the author to the
    # wrong three lines of their own file.
    assert "RECEIPTS UNREADABLE" in tags_mod.render_audit(
        tags_mod.audit(root, [_rec("seen-once")]))


def test_an_unreadable_left_block_never_excuses_a_name(tmp_path):
    """FAIL CLOSED: a block that cannot be read yields no entries AND a
    complaint, so every name it would have excused is counted undecided
    rather than quietly forgiven."""
    root = _vocab_repo(tmp_path, "left_undeclared: [\n")
    left, problems = tags_mod.load_left_undeclared(root)
    assert left == {} and problems
    assert tags_mod.undecided_tags(root, [_rec("anything")]) == ["anything"]


_FOLD_BASE = ("tags:\n  fail-open: errs toward passing\n"
              "aliases:\n  retired-name: fail-open\n")


def test_fold_problems_reports_exactly_the_unreadable_vocabulary(tmp_path):
    """The single criterion `fold_problems`' docstring states, driven.

    Two incompatible tests for what belongs in that tuple — "a declaration
    file with a refused block cannot be trusted" (which includes `tags:`)
    beside "it does not touch the FOLD" (which excludes it) — would leave the
    next author with no single test. This is the one criterion as an
    executable test, so the docstring and the function cannot drift apart.

    IN: anything that stops either block DECLARING the vocabulary — `tags:`
    and `aliases:` — from being read as declared, the file around them
    included. OUT: a complaint about `ceiling:` or `left_undeclared:`, which
    leave both declaration blocks readable.
    """
    inside = {
        "the file cannot be parsed": "tags: [\n",
        "the file is not a mapping at all": "- just\n- a list\n",
        "the `tags:` block is not a mapping":
            "tags:\n  - fail-open\naliases:\n  retired-name: fail-open\n",
        "the `aliases:` block is not a mapping":
            "tags:\n  fail-open: errs toward passing\naliases:\n  - x\n",
    }
    for why, body in inside.items():
        problems = tags_mod.fold_problems(_vocab_repo(tmp_path / why, body))
        assert problems, (
            f"{why}: `fold_problems` is silent, so `certify`'s S-04 scores a "
            "LEVEL-5 rung over a vocabulary nobody could read")

    outside = {
        "a `ceiling:` whose rationale is a token":
            _FOLD_BASE + "ceiling:\n  max_undecided: 0\n  rationale: todo\n",
        "an unreadable `left_undeclared:` block":
            _FOLD_BASE + "left_undeclared: [a, b]\n",
    }
    for why, body in outside.items():
        root = _vocab_repo(tmp_path / why, body)
        assert not tags_mod.fold_problems(root), (
            f"{why}: `fold_problems` reports it, which refuses a promotion "
            "score over a vocabulary that reads perfectly — wider than the "
            "question asks, and the criterion in the docstring says out")
        # …and the complaint is REAL, so this half is measuring the boundary
        # rather than a body nothing objects to.
        assert (tags_mod.ceiling_status(root, []).get("complaints")
                or tags_mod.load_left_undeclared(root)[1]), (
            f"{why}: nothing complains about this body at all, so excluding "
            "it from `fold_problems` measures nothing")

    # The summary line and the criterion agree, and both are about the
    # VOCABULARY rather than the FOLD — the FOLD reading is the narrower one
    # and would contradict the criterion.
    doc = tags_mod.fold_problems.__doc__ or ""
    assert "DECLARED VOCABULARY" in doc.splitlines()[0], (
        "the summary line no longer names the vocabulary; if the criterion "
        "moved, move this test with it rather than deleting the pin")
    retired = "does not touch the FOLD"
    if retired in doc:
        # The docstring may QUOTE the retired test while saying it is retired.
        # What must not happen is the quotation standing as a live criterion
        # beside the current one, so the pin is on the word that marks it
        # dead, in the same paragraph.
        para = next(b for b in doc.split("\n\n") if retired in b)
        assert "retired" in para, (
            "the docstring states the retired criterion without marking it "
            "retired, so it reads as a second live test for membership — "
            "that pair is two live membership tests")


def test_a_folded_name_is_decided_by_the_fold_alone(tmp_path):
    """An alias discharges the obligation as completely as a declaration or a
    recorded reason does: the records join the canonical class, so nothing is
    split any more and nothing needs writing down."""
    root = _vocab_repo(tmp_path, (
        "tags:\n  fail-open: errs toward passing\n"
        "aliases:\n  failing-open: fail-open\n"))
    folded = tags_mod.canonicalize_records(
        root, [_rec("failing-open") for _ in range(4)])
    assert tags_mod.undecided_tags(root, folded) == []


# ── the at-bar REPORT (not enforced; it is the retro's re-read list) ────────


def test_the_declaration_bar_reads_judged_and_upheld_as_the_corpus_does(
        tmp_path):
    """Every branch of `undeclared_at_bar` discriminated by a shape that only
    it explains. A branch no fixture exercises survives mutation: without a
    `confirmed` record, a denominator shape for the judged filter, and an
    exactly-half case for the majority test, three of them would."""
    root = _vocab_repo(tmp_path, "tags:\n  fail-open: errs toward passing\n")
    records = (
        # at the bar on `confirmed` alone, so narrowing upheld to {"fixed"}
        # cannot go unnoticed.
        [_rec("ripe-confirmed", "confirmed") for _ in range(3)]
        # freshly coined: 1 judged. Soft governance — never at the bar.
        + [_rec("just-coined")]
        # 2 judged is under the bar however clean
        + [_rec("nearly") for _ in range(2)]
        # 3 judged, 1 upheld: not upheld more often than not
        + [_rec("mostly-wrong"),
           _rec("mostly-wrong", "refuted"),
           _rec("mostly-wrong", "dismissed-with-reason")]
        # EXACTLY half — 2 upheld of 4 judged. `> n` excludes it; `>= n`
        # would include it, and nothing else in the fixture tells them apart.
        + [_rec("dead-heat") for _ in range(2)]
        + [_rec("dead-heat", "refuted") for _ in range(2)]
        # refuted and dismissed statuses ARE judged: narrowing the judged set
        # to the upheld one would read this as 3-judged/3-upheld and put a
        # class the corpus keeps refuting AT the bar.
        + [_rec("refuted-heavy") for _ in range(3)]
        + [_rec("refuted-heavy", "refuted") for _ in range(9)]
        # unjudged statuses are not evidence, and the filter's real load is
        # the DENOMINATOR: counting these would drop this class off the bar.
        + [_rec("ripe-with-noise") for _ in range(3)]
        + [_rec("ripe-with-noise", "open") for _ in range(4)]
        # a DECLARED class is never undeclared, however large
        + [_rec("fail-open") for _ in range(9)]
        # a record carrying several tags counts for each of them, and a
        # record with no `tags` key at all must not raise
        + [_rec(["multi-a", "multi-b"]) for _ in range(3)]
        + [{"rule_id": "r", "status": "fixed"}])
    assert tags_mod.undeclared_at_bar(root, records) == [
        "multi-a", "multi-b", "ripe-confirmed", "ripe-with-noise"]


def test_the_upheld_set_is_a_subset_of_the_judged_set():
    """One definition of 'judged', one of 'upheld'. If these diverged, the
    ceiling and the precision rows would disagree about the same record — the
    reader-divergence shape this corpus records."""
    assert tags_mod._UPHELD_STATUSES < _JUDGED_STATUSES
    assert tags_mod._UPHELD_STATUSES == {"confirmed", "fixed"}


# ── reporting ──────────────────────────────────────────────────────────────


def test_the_status_reports_a_breach_and_the_audit_names_every_line(tmp_path):
    root = _vocab_repo(tmp_path, (
        "tags:\n  fail-open: errs toward passing\n"
        f"ceiling:\n  {tags_mod.CEILING_LIMIT}: 0\n"
        f"  rationale: {_ARGUES}\n"))
    clean = tags_mod.render_audit(tags_mod.audit(root, [_rec("fail-open")]))
    assert "CEILING BREACHED" not in clean
    assert "drift ceiling: 0 undecided of 0 allowed" in clean

    records = [_rec("ripe-a") for _ in range(3)] + [_rec("ripe-b")]
    status = tags_mod.ceiling_status(root, records)
    assert status["breached"] and status["undecided"] == ["ripe-a", "ripe-b"]
    assert status["at_bar"] == ["ripe-a"], "at-bar is reported beside it"
    rendered = tags_mod.render_audit(tags_mod.audit(root, records))
    # Line PREFIXES, not bare names: `render_audit` already prints every
    # undeclared name on its UNKNOWN line, so a name-only needle is satisfied
    # by a line this block did not write.
    assert "  CEILING BREACHED: 2 undecided tag(s)" in rendered
    assert "  UNDECIDED (in the corpus" in rendered
    assert "  UNDECLARED AT THE DECLARATION BAR" in rendered
    assert "ripe-a" in rendered and "ripe-b" in rendered


def test_an_unreadable_ceiling_withholds_the_count_rather_than_reporting_zero(
        tmp_path):
    root = _vocab_repo(tmp_path, "ceiling:\n  max_undecided: 1\n")
    status = tags_mod.ceiling_status(root, [_rec("ripe") for _ in range(3)])
    assert status["ceiling"] is None and status["complaints"]
    assert "CEILING UNREADABLE" in tags_mod.render_audit(
        tags_mod.audit(root, [_rec("ripe")]))


def test_an_unreadable_vocabulary_reports_and_never_reads_as_healthy(tmp_path):
    """`ceiling_status` is called from `memory ingest` and `memory stats`,
    both of which must still rebuild and report when this file is broken — a
    traceback there destroys the run dir, and with it the
    `ingest-result.json` the reading travels in. It must also not answer
    ZERO: an unreadable vocabulary declares and decides nothing, so every tag
    in the corpus counts undecided, which is the loud direction."""
    root = _vocab_repo(tmp_path, "tags: {}\n")
    (root / ".warden" / "memory" / "tags.yaml").write_bytes(b"\xff\xfe bad\n")
    status = tags_mod.ceiling_status(root, [_rec("anything")])
    assert status["complaints"], "the failure is reported"
    assert status["undecided"] == ["anything"], (
        "nothing is decided by a file nobody can read")
    rendered = tags_mod.render_audit(tags_mod.audit(root, [_rec("anything")]))
    # The FILE case again — see the dangling-symlink test above.
    assert "VOCABULARY UNREADABLE" in rendered and "UNDECIDED" in rendered


# ── this repo's own live obligation ────────────────────────────────────────


# Pinned, not merely non-None. Raising the number with the rationale
# untouched must turn the suite red: the substance floor reads the TEXT and
# can say nothing about the NUMBER, so without this pin the ratchet every
# surface describes would be enforced by nothing. Editing this line is the
# deliberate act; the rationale beside it in tags.yaml is the argument.
THIS_REPOS_CEILING = 0


@needs_receipts
def test_this_repos_tag_ceiling_is_declared_and_holds():
    """The live obligation, and the half that blocks a PR: pytest runs on
    `pull_request` as well as `push`, so a branch whose own review round coins
    a name goes red in its OWN CI, which is the moment a receipt is cheapest
    to write.

    Same shape as tests/test_gap_answers.py::
    test_this_repos_ceiling_is_declared_and_holds. When it goes red: declare
    the class in .warden/memory/tags.yaml, fold it with an `aliases:` entry,
    or record one line under `left_undeclared:` saying what was seen. Never
    delete the declaration, and never raise the number without replacing both
    the rationale and the value pinned above.
    """
    ceiling, complaints = tags_mod.load_ceiling(ROOT)
    assert not complaints, complaints
    assert ceiling is not None, (
        ".warden/memory/tags.yaml declares no ceiling — deleting it un-wires "
        "the obligation and the vocabulary drifts unowned again")
    assert ceiling == THIS_REPOS_CEILING, (
        f"the declared ceiling moved to {ceiling}. Raising it is a deliberate "
        f"human edit that must carry a rationale arguing for the NEW value — "
        f"update THIS_REPOS_CEILING in the same commit, so the change is "
        f"visible in the diff rather than absorbed")
    undecided = tags_mod.undecided_tags(ROOT, records_from_shards(ROOT))
    assert len(undecided) <= ceiling, (
        f"{len(undecided)} tag(s) in the corpus have no recorded "
        f"disposition against a declared ceiling of {ceiling}: "
        f"{', '.join(undecided)} — declare each in .warden/memory/tags.yaml, "
        f"fold it, or record one line under 'left_undeclared:' saying what "
        f"was seen. Recording decides nothing about the class")


@needs_receipts
def test_every_recorded_decision_is_readable_and_carries_an_argument():
    """The `left_undeclared:` block is what discharges undecided names. A
    malformed entry silently stops discharging its name, which
    reads as drift rather than as the config error it is."""
    left, problems = tags_mod.load_left_undeclared(ROOT)
    assert not problems, problems
    assert left, "the recorded decisions vanished from tags.yaml"


@needs_corpus
def test_every_at_bar_class_has_a_recorded_decision_to_re_read():
    """The at-bar line is the retro's re-read list, which only works if every
    name on it is a name someone wrote something about. A class at the bar
    that is neither declared nor recorded would be pure drift."""
    at_bar = tags_mod.undeclared_at_bar(ROOT, records_from_shards(ROOT))
    left, _ = tags_mod.load_left_undeclared(ROOT)
    orphans = [t for t in at_bar if t not in left]
    assert not orphans, (
        f"at the declaration bar with no recorded reason: {orphans}")


@needs_corpus
def test_an_at_bar_receipt_states_a_count_its_evidence_has_not_outgrown():
    """An at-bar receipt outgrown by its evidence, and the half the guard above
    cannot see.

    `test_every_at_bar_class_has_a_recorded_decision_to_re_read` asks only
    that a name on the RE-READ line HAS a receipt. It passed while four
    receipts argued from counts the corpus had left behind —
    `incomplete-cause-list` reading "n=1 ... one record is not enough to coin
    one" over four records from three rounds, `unanchored-pattern` and
    `vacuous-guard` reading "below the bar either way" from the bar, and
    `reader-divergence` reading "simply not yet at the declaration bar" while
    at it. A receipt is only a decision while its premise is true, so a name
    that STAYS on that line has to be argued FROM the bar, at a count the
    shards still support.

    Scoped to the at-bar names on purpose: that line is the re-read list, and
    it is where a stale premise costs a decision. A sub-bar receipt whose `n=`
    has crept is a lesser defect with its own tracked item, not a reason to
    widen this guard until it fails on something nobody is reading.
    """
    records = records_from_shards(ROOT)
    at_bar = tags_mod.undeclared_at_bar(ROOT, records)
    left, problems = tags_mod.load_left_undeclared(ROOT)
    assert not problems, problems
    assert at_bar, ("no name is at the declaration bar, so this guard is "
                    "reading an empty list and proves nothing")

    judged: dict[str, int] = {}
    for rec in records:
        if rec.get("status") not in _JUDGED_STATUSES:
            continue
        for tag in (rec.get("tags") or []):
            judged[tag] = judged.get(tag, 0) + 1

    for name in at_bar:
        receipt = left.get(name)
        assert receipt, f"{name}: at the bar with no receipt at all"
        assert "AT the declaration bar" in receipt, (
            f"{name!r} is on the UNDECLARED AT THE DECLARATION BAR line and "
            "its receipt does not argue the bar — a receipt that reasons from "
            "a count below the bar has been outgrown by its own evidence "
            "Declare it, fold it, or re-argue the "
            "receipt from the bar with its current count")
        claimed = re.search(r"\bn=(\d+)", receipt)
        assert claimed, (
            f"{name!r}: an at-bar receipt must state the count it argues "
            "from, as `n=<judged>`, or a reader cannot tell a live decision "
            "from one the corpus has moved past")
        assert int(claimed.group(1)) >= judged.get(name, 0), (
            f"{name!r}: the receipt argues from n={claimed.group(1)} and the "
            f"committed shards hold {judged.get(name, 0)} judged record(s) — "
            "the decision was taken on less evidence than exists, which is "
            "exactly the drift the 2026-09-20 re-read re-decided")


# A receipt states its re-read condition in ONE canonical key, `trigger:`,
# parsed and evaluated by `warden/tags.py` — the same reader `memory
# check-vocabulary` validates it through. The prose is the human reason and
# nothing here reads a condition out of it: the sentence
# reader this replaced enumerated phrasings, and each review round found more
# it could not see.


@needs_corpus
def test_a_receipt_is_held_to_the_re_read_trigger_it_states():
    """A receipt that names a condition for re-reading itself is held to it,
    over EVERY receipt and on either side of the declaration bar: a fired
    trigger is a premise that has stopped holding. `dangling-reference`'s
    "fold it when another record lands" had fired unseen once."""
    triggers, problems = tags_mod.load_triggers(ROOT)
    assert not problems, problems
    assert triggers, ("no receipt carries a `trigger:` key, so this guard "
                      "reads nothing and proves nothing")
    fired = tags_mod.fired_triggers(triggers, records_from_shards(ROOT),
                                    rules_mod.load_rules(RULES_DIR))
    assert not fired, (
        "these receipts state a condition for re-reading themselves and it "
        f"has been met, so each premise has stopped holding: {fired}. "
        "Re-argue the receipt from what is committed now, take the "
        "disposition it promised, or restate its `trigger:`")


@needs_receipts
def test_each_existing_receipt_round_trips_to_its_key():
    """Every receipt the refusal reads as stating a condition carries it as
    the key, with the meaning it had: each holds at the corpus state its
    words argued from and fires one step past it. The four marked `none`
    state history ("counts re-read from the shards") or a pointer ("revisit
    both together"), not a condition. The list is what the refusal saw; a
    condition worded outside both of its shapes is not on it, and nothing
    here can say none exists."""
    triggers, problems = tags_mod.load_triggers(ROOT)
    assert not problems, problems
    live = [types.SimpleNamespace(id="attribution-holds", paused=False)]
    # name: (key, (judged, upheld) that holds, (judged, upheld) that fires)
    for name, key, holds, fires in (
            # "n=2 ... fold it when another record lands"
            ("dangling-reference", {"judged_at_least": 3}, (2, 2), (3, 3)),
            # "at n=2 ... revisit when a third record says which way"
            ("false-attribution", {"judged_at_least": 3}, (2, 2), (3, 3)),
            # "re-read when it earns a third upheld record"
            ("unwired-gate", {"upheld_at_least": 3}, (3, 2), (4, 3)),
            # "a second occurrence is the retro's to weigh" had FIRED
            # unread (2 judged); re-argued at n=2 with the bar as its key
            ("fix-without-test", {"judged_at_least": 3}, (2, 2), (3, 3)),
            # "re-read if that rule is ever paused or retired"
            ("unsupported-source-claim",
             {"rule_live": "attribution-holds"}, (5, 4), None)):
        assert triggers.get(name) == key, (name, triggers.get(name))
        assert tags_mod.trigger_fired(key, *holds, live) == [], name
        if fires:
            assert tags_mod.trigger_fired(key, *fires, live), name
    doc = yaml.safe_load(tags_mod.vocab_path(ROOT).read_text())
    left = doc[tags_mod.LEFT_KEY]
    assert sorted(n for n, e in left.items() if isinstance(e, dict)
                  and e[tags_mod.TRIGGER_KEY] == "none") == [
        "duplicated-derivation", "handoff-contract-skew",
        "scope-limited-derivation", "unwired-provenance-field"]
    assert sorted(triggers) == sorted(
        n for n, e in left.items() if isinstance(e, dict)
        and e[tags_mod.TRIGGER_KEY] != "none")


def test_the_canonical_key_is_evaluated(tmp_path):
    """Each condition fires at its threshold and not below; `rule_live`
    fires on a pause and on a retirement, each named as what it is."""
    rule = types.SimpleNamespace(id="live-rule", paused=False)
    fired = tags_mod.trigger_fired
    assert fired({"judged_at_least": 3}, 2, 2, []) == []
    assert fired({"judged_at_least": 3}, 3, 0, [])
    assert fired({"upheld_at_least": 3}, 9, 2, []) == [], (
        "judged records are not upheld ones")
    assert fired({"upheld_at_least": 3}, 3, 3, [])
    assert fired({"rule_live": "live-rule"}, 0, 0, [rule]) == []
    paused = fired({"rule_live": "live-rule"}, 0, 0,
                   [types.SimpleNamespace(id="live-rule", paused=True)])
    assert paused and "PAUSED" in paused[0], paused
    retired = fired({"rule_live": "live-rule"}, 0, 0, [])
    assert retired and "retired" in retired[0], retired
    # And end to end, from the file through the loader to the evaluation.
    root = _vocab_repo(tmp_path, (
        "left_undeclared:\n  seen-twice:\n"
        "    trigger: {judged_at_least: 3}\n"
        f"    reason: {_ARGUES}\n"))
    triggers, problems = tags_mod.load_triggers(root)
    assert not problems and triggers == {"seen-twice": {"judged_at_least": 3}}
    assert not tags_mod.fired_triggers(triggers, [_rec("seen-twice")] * 2, [])
    assert tags_mod.fired_triggers(triggers, [_rec("seen-twice")] * 3, [])


def test_two_conditions_are_both_evaluated():
    """A trigger naming two conditions fires on EITHER, and reports each
    that has fired — never only the first it reads."""
    both = {"judged_at_least": 3, "upheld_at_least": 3}
    fired = tags_mod.trigger_fired
    assert fired(both, 2, 2, []) == []
    assert [w.split(":")[0] for w in fired(both, 3, 2, [])] == [
        "judged_at_least"]
    assert [w.split(":")[0] for w in fired(both, 2, 3, [])] == [
        "upheld_at_least"]
    assert len(fired(both, 3, 3, [])) == 2
    rule_too = {"judged_at_least": 9, "rule_live": "gone-rule"}
    assert [w.split(":")[0] for w in fired(rule_too, 1, 1, [])] == [
        "rule_live"]


# One case per member of each refusal pattern, so no verb, operand or count
# word can be deleted with this test green: every verb beside the backticked
# operand, every operand beside `revisit`, and every future-count word bare.
@pytest.mark.parametrize("receipt", [
    *(f"watching this class closely; {verb} the receipt around `some-rule` "
      "at the retro later" for verb in (
          "re-read", "reread", "revisit", "reopen", "re-argue", "redecide",
          "re-examine", "reconsider", "re-evaluate", "reassess", "fold it")),
    *(f"watching this class closely; revisit the receipt at the retro "
      f"once {operand} arrives" for operand in (
          "3 records", "one record", "two records", "three more", "four",
          "five", "another", "the first", "the second",
          "the third", "the fourth", "the fifth")),
    *(f"watching this class closely and the retro weighs it once the "
      f"{word} {noun} lands here" for word, noun in (
          ("another", "record"), ("second", "occurrence"),
          ("third", "upheld instance"), ("fourth", "finding"),
          ("fifth", "round"), ("next", "records"))),
])
def test_a_prose_only_trigger_is_refused_naming_the_key(
        tmp_path, receipt):
    """A receipt whose words pair a re-read verb with a number or a rule
    name, and carry no key, is REFUSED — not parsed, and not discharged —
    and the refusal names the key. Seen by `memory check-vocabulary` too."""
    body = ("tags:\n  fail-open: errs toward passing\n"
            f"ceiling:\n  max_undecided: 0\n  rationale: {_ARGUES}\n"
            f"left_undeclared:\n  prose-only: {receipt}\n")
    root = _vocab_repo(tmp_path, body)
    left, problems = tags_mod.load_left_undeclared(root)
    assert "prose-only" not in left
    assert len(problems) == 1 and "`trigger:`" in problems[0], problems
    verdict = vocab_mod.check(root)
    assert verdict["status"] == vocab_mod.STATUS_CANNOT_EVALUATE, verdict
    # The same words under `trigger: none` are the author saying they state
    # no condition, and discharge the name.
    root2 = _vocab_repo(tmp_path / "none", body.replace(
        f"prose-only: {receipt}",
        f"prose-only:\n    trigger: none\n    reason: {receipt}"))
    assert tags_mod.load_left_undeclared(root2) == ({"prose-only": receipt},
                                                    ())
    # A re-read verb with neither a number nor a rule name sets no
    # evaluable condition, so it is not refused.
    root3 = _vocab_repo(tmp_path / "bare", body.replace(
        receipt, "watching this closely; revisit it at the retro once the "
        "class is understood better by whoever reads it"))
    assert not tags_mod.load_left_undeclared(root3)[1]


@pytest.mark.parametrize("entry", [
    "trigger: {judged_after: 3}",          # an unknown condition
    "trigger: {judged_at_least: 0}",       # not a positive count
    "trigger: {upheld_at_least: true}",    # a bool is not a count
    "trigger: {rule_live: ''}",            # names no rule
    "trigger: {}",                         # states nothing
    "trigger: sometimes",                  # neither `none` nor a mapping
    "trigger: none\n    note: x",          # a key beside the two
])
def test_a_malformed_trigger_key_is_a_complaint(tmp_path, entry):
    root = _vocab_repo(tmp_path, (
        f"left_undeclared:\n  bad:\n    {entry}\n    reason: {_ARGUES}\n"))
    left, problems = tags_mod.load_left_undeclared(root)
    assert left == {} and len(problems) == 1, problems


@needs_corpus
def test_the_ceiling_is_a_limit_and_the_count_is_derived():
    """Counts are derived, never stored — applied to the ceiling
    itself: the number in tags.yaml is the LIMIT and nothing else. Nowhere
    does the tree store how many names are undecided — that is read off the
    shards on every call, so it cannot be stale or hand-adjusted."""
    raw = (ROOT / ".warden" / "memory" / "tags.yaml").read_text()
    assert tags_mod.CEILING_LIMIT in raw and tags_mod.LEFT_KEY in raw
    for record in records_from_shards(ROOT):
        assert "undecided" not in record and "at_bar" not in record


def test_the_suite_that_enforces_the_ceiling_runs_on_prs_into_main():
    """The wiring is half the deliverable. `warden memory ingest` deliberately
    does NOT fail on a breach (see the CLI comment and `cage/run.sh`), so what
    stops a drifting PR in THIS repo's CI is this suite — which is worth
    nothing if the job that runs it stops running on pull requests. (The
    shipped enforcement consumers run is `warden memory check-vocabulary`,
    tested in tests/test_vocabulary_check.py; this repo's own
    workflow still runs the suite, and the suite is what pins the VALUE.)

    Parsed, not grepped: the sibling guard's docstring
    (tests/test_gap_answers.py::test_ci_runs_the_ceiling_on_prs_into_main)
    lists five ways a substring version is defeated. Read-only — this
    asserts about `.github/`, it does not write to it.
    """
    wf = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text())
    # yaml parses the `on:` key as the boolean True.
    assert "pull_request" in wf[True], (
        "the workflow no longer triggers on pull_request, so nothing runs "
        "this guard before a merge")
    assert wf[True]["pull_request"]["branches"] == ["main"]
    tests = wf["jobs"]["tests"]
    # The one admissible condition, declared once in conftest: a title-only
    # edit over a sha this workflow has already passed green. Anything else —
    # including that clause with its `always()` dropped — is a guard that may
    # not run, which is not a guard.
    why = why_a_ci_job_might_not_run(tests)
    assert why is None, f"the tests job became conditional: {why}"
    steps = [s for s in tests["steps"] if "pytest" in s.get("run", "")]
    assert steps, "the tests job no longer runs pytest"
    for step in steps:
        assert "continue-on-error" not in step, (
            "red that cannot fail the job is not red")


# ── the CLI seam: reported, never enforced ─────────────────────────────────
#
# `warden memory ingest` reports a breach and still exits 0, deliberately.
# `cage/run.sh` gates publication of the run's attest shard on this exit code,
# reading non-zero as "the corpus was not fed": a vocabulary breach feeds the
# corpus perfectly and would then have the shard discarded, so the run whose
# review round coined the drifting name is exactly the run whose evidence is
# thrown away — and the PR would go red two hops later on "commit carries no
# attestation". `retro/SKILL.md` reads the same code as "the corpus is
# unread" and would halt the one ritual that clears a breach. The exit code
# that DOES carry the verdict is `warden memory check-vocabulary`'s
# (tests/test_vocabulary_check.py).


def _ingest_repo(tmp_path, tags_yaml: str, records: list[dict]) -> Path:
    """A minimal enrolled repo whose corpus is one committed shard."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "repo.yaml").write_text(
        "version: 1\nrepo: ceiling-fixture\n"
        "components:\n  app:\n    path: app/\n    lang: python\n"
        "    description: application code\n"
        "risk_tiers:\n  - glob: app/**\n    tier: MEDIUM\n"
        "    reason: production code\n"
        "verify:\n  smoke:\n    - run: \"true\"\n"
        "review:\n  rules_dir: .warden/rules\n"
        "  blocking_severities: [HIGH]\n")
    (root / ".warden" / "rules").mkdir(parents=True)
    (root / ".gitignore").write_text(".warden/out/\n")
    mem = root / ".warden" / "memory"
    (mem / "attest").mkdir(parents=True)
    (mem / "tags.yaml").write_text(tags_yaml)
    (mem / "attest" / "20260901T000000Z-aaaaaaaa-bbbbbbbb.json").write_text(
        json.dumps({"records": records}))
    for args in (("init", "-q", "-b", "main"),
                 ("config", "user.email", "t@t"), ("config", "user.name", "t"),
                 ("add", "."),
                 ("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def _shard_records(tag: str, n: int, status: str = "fixed") -> list[dict]:
    return [{"id": f"{tag}{i:04x}", "ts": f"2026-09-01T00:00:{i:02d}+00:00",
             "seq": i, "source": "attest", "sha": "a" * 40, "base_sha": "b" * 40,
             "rule_id": f"unmapped:{tag}", "tags": [tag], "dir_prefix": "app",
             "file": "app/x.py", "line": 1, "severity": "MEDIUM",
             # one text per judgment — identical text in a later round is
             # that judgment restated, and folds to one
             "finding": f"f{i}", "evidence": "e", "status": status}
            for i in range(n)]


_CEILING_0 = ("tags:\n  fail-open: errs toward passing\n"
              "ceiling:\n  max_undecided: 0\n"
              f"  rationale: {_ARGUES}\n")


def _manifests(root):
    return [p for p in (root / ".warden" / "out").glob("*/manifest.json")
            if p.parent.name != "latest"]


def test_ingest_reports_a_breach_without_changing_what_its_exit_code_means(
        tmp_path, monkeypatch, capsys):
    """Exiting 1 here would make `cage/run.sh` drop the run's attest shard
    and report `corpus not fed` — false, and self-perpetuating."""
    root = _ingest_repo(tmp_path, _CEILING_0, _shard_records("ripe", 3))
    monkeypatch.chdir(root)
    assert cli.main(["memory", "ingest"]) == 0, (
        "a vocabulary breach must not read as a failed corpus rebuild")
    err = capsys.readouterr().err
    assert "TAG CEILING BREACHED" in err
    assert "no recorded disposition" in err
    # The NAMES, pinned against the message that carries them and not against
    # the unknown-tag warning printed above it, which also contains "ripe" —
    # a check over all of stderr would stay green with the names dropped.
    breach = [ln for ln in err.splitlines() if "TAG CEILING BREACHED" in ln]
    assert breach and "ripe" in breach[0], (
        "a breach that does not name the classes tells the author they are in "
        "breach and not which line to write")
    manifests = _manifests(root)
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["exit_status"] == "ok"
    result = json.loads(
        (manifests[0].parent / "ingest-result.json").read_text())
    assert result["tag_ceiling"]["breached"] is True, (
        "the reading must travel in the artifact — the unattended cage has no "
        "terminal to read")


def test_ingest_says_nothing_about_a_ceiling_that_holds(tmp_path, monkeypatch,
                                                        capsys):
    root = _ingest_repo(
        tmp_path,
        _CEILING_0 + "left_undeclared:\n  coined-once: >-\n"
        "    seen once this round at n=1, recorded so the backlog cannot grow "
        "in silence, and decided nothing about the class\n",
        _shard_records("coined-once", 1))
    monkeypatch.chdir(root)
    assert cli.main(["memory", "ingest"]) == 0
    err = capsys.readouterr().err
    assert "CEILING" not in err
    assert "is not in the declared vocabulary" in err, (
        "the unknown-tag warning is unchanged — soft governance survives")
    assert json.loads(
        _manifests(root)[0].read_text())["exit_status"] == "ok"


def test_ingest_reports_an_unreadable_ceiling_and_still_rebuilds(
        tmp_path, monkeypatch, capsys):
    """A declared ceiling that cannot be read is never headroom. It is also
    never a reason to lose the corpus rebuild this command exists for."""
    root = _ingest_repo(tmp_path, "ceiling:\n  max_undecided: 1\n  rationale: x\n",
                        _shard_records("ripe", 3))
    monkeypatch.chdir(root)
    assert cli.main(["memory", "ingest"]) == 0
    assert "TAG CEILING UNREADABLE" in capsys.readouterr().err
    from warden import memory as memory_mod
    assert len(memory_mod.load_records(root)) == 3, "the corpus was still fed"


def test_a_repo_declaring_no_ceiling_is_unaffected(tmp_path, monkeypatch,
                                                   capsys):
    """The consumer blast radius, pinned. Every enrolled repo runs this
    command; one that never declares a ceiling must see exactly the behaviour
    it saw before — warnings, exit 0, no ceiling line at all."""
    root = _ingest_repo(tmp_path, "tags:\n  fail-open: errs toward passing\n",
                        _shard_records("ripe", 9))
    monkeypatch.chdir(root)
    assert cli.main(["memory", "ingest"]) == 0
    err = capsys.readouterr().err
    assert "is not in the declared vocabulary" in err, "the warning stays"
    assert "CEILING" not in err


def test_a_fold_discharges_the_obligation_the_same_way_a_receipt_does(
        tmp_path, monkeypatch, capsys):
    """The ceiling is read AFTER canonicalization, so an alias clears it.
    Reading it before would leave a folded class counting forever, and the
    only way to green would be to write a receipt for a name the repo had just
    decided to retire."""
    root = _ingest_repo(
        tmp_path, _CEILING_0 + "aliases:\n  ripe: fail-open\n",
        _shard_records("ripe", 3))
    monkeypatch.chdir(root)
    assert cli.main(["memory", "ingest"]) == 0
    assert "CEILING BREACHED" not in capsys.readouterr().err
