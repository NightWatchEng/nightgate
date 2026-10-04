"""The rules written from the corpus's own proposal bar.

WHY THIS FILE EXISTS: counts typed into prose rot, reliably. The backtest
artifacts for these rules carry judged/upheld/refuted/dismissed counts, and
an artifact nothing recomputes can quote a rate and a wilson_lb taken from
two different denominators. So this file DERIVES every count from the
committed shards and refuses an artifact that disagrees. The
`unmapped:<slug>` record set is frozen by construction — once the rule
ships, `attest write` refuses the slug for new attestations — so equality is
stable, not a race with a growing corpus.

A rule ADOPTED through `warden autonomy adopt` is the other shape this file
holds: it draws on a declared tag whose records were all filed under existing
rules, covers nothing (the carve-out refuses a covers-bearing adoption), so
it has no `unmapped:` history to derive a promote tally from, and the
artifact beside it is an `adopt` record that declares `judged: 0`. Its
guards are the readers the carve-out applies to its two files, plus the tag
bar, derived from the same shards.

The guard must be wider than a spot-check: the equality below covers every
field the artifact quotes, not one of them, and
test_backtest_numbers_cannot_drift fails if EITHER side moves — the artifact
edited without the corpus, or a shard added under a covered slug.
"""

import json
import re
from pathlib import Path

import pytest

import yaml

from warden import rules as rules_mod
from warden import tags as tags_mod
from warden.memory import (_JUDGED_STATUSES, records_from_shards,
                           wilson_lower_bound)
from private_evidence import needs_corpus

ROOT = Path(__file__).parent.parent
RULES_DIR = ROOT / ".warden" / "rules"
BACKTESTS = ROOT / ".warden" / "memory" / "backtests"

# rule id -> the candidate class it was written to answer (a class past the
# corpus's own proposal bar). The id deliberately differs from the slug: a
# rule id that equals a candidate slug invalidates every committed shard that
# filed under `unmapped:<slug>`.
CORPUS_RULES = {
    "enforcement-truth": "enforcement-claim",
    "tests-bite": "test-cannot-fail",
    "pattern-fit": "overbroad-pattern",
    "rename-complete": "incomplete-rename",
    "error-names-cause": "diagnosability",
    "evidence-intact": "corpus-integrity",
    "attribution-holds": "evidence-attribution",
    "blast-radius-named": "consumer-blast-radius",
}

# rule id -> the declared TAG a machine adoption draws its evidence from.
# The rule declares no `covers:`: the carve-out refuses a covers-bearing
# adoption (covering a slug makes `attest write` refuse `unmapped:<slug>`,
# enforcement raised at the write seam whatever the severity), so the tag
# stays a tag and findings of the class file under the rule id carrying it.
ADOPTED_RULES = {
    "subject-required": "unanchored-pattern",
}


def _tag_counts(slug: str) -> dict[str, int]:
    """Judged history of a declared TAG, derived from the committed shards —
    the sample a machine adoption is argued from, since the class it draws on
    has no `unmapped:` rows. Same status split as `_class_counts`."""
    upheld = refuted = dismissed = 0
    for rec in records_from_shards(ROOT):
        if slug not in (rec.get("tags") or []):
            continue
        status = rec.get("status")
        if status not in _JUDGED_STATUSES:
            continue
        if status in ("fixed", "confirmed"):
            upheld += 1
        elif status == "refuted":
            refuted += 1
        else:
            dismissed += 1
    return {"judged": upheld + refuted + dismissed, "upheld": upheld,
            "refuted": refuted, "dismissed": dismissed}


def _rules_by_id() -> dict[str, rules_mod.Rule]:
    return {r.id: r for r in rules_mod.load_rules(RULES_DIR)}


def _class_counts(slug: str) -> dict[str, int]:
    """Judged history of a candidate class, derived from committed shards —
    the same source certification's Wilson math reads, never the cache.

    Statuses outside _JUDGED_STATUSES (e.g. `detected`, the default for a
    finding nothing judged) are excluded exactly as warden's own Wilson math
    excludes them — routed to detections, never precision. Counting them
    here would let never-judged records satisfy the proposal bar."""
    upheld = refuted = dismissed = 0
    for rec in records_from_shards(ROOT):
        if rec.get("rule_id") != f"unmapped:{slug}":
            continue
        status = rec.get("status")
        if status not in _JUDGED_STATUSES:
            continue
        if status in ("fixed", "confirmed"):
            upheld += 1
        elif status == "refuted":
            refuted += 1
        else:  # dismissed-with-reason — the only remaining judged status
            dismissed += 1
    return {"judged": upheld + refuted + dismissed, "upheld": upheld,
            "refuted": refuted, "dismissed": dismissed}


def test_corpus_rules_dict_matches_the_tree():
    """CORPUS_RULES is this file's coverage spec, and a hand-typed dict can
    silently shed a key. This guard makes any drift loud:
    every declared rule with a `covers:` class must be a key here, except
    the two precedent rules whose backtest artifacts predate this file and
    use a tag-based denominator the unmapped-only shard derivation cannot
    reproduce (fail-closed judged=39 is the class's full tag history, not
    the 27 candidate records; wiki-fidelity likewise). A future
    covers:-bearing rule fails here until it is added above and inherits
    all three guards. A machine-adopted rule (ADOPTED_RULES) is never in
    this set: the carve-out refuses an adoption that covers a class, so one
    that appears here was edited by hand after adoption."""
    covering = {r.id for r in rules_mod.load_rules(RULES_DIR) if r.covers}
    precedent = {"fail-closed", "wiki-fidelity"}
    assert precedent <= covering, (
        f"precedent rules missing from the tree: {sorted(precedent - covering)}")
    assert covering - precedent == set(CORPUS_RULES), (
        f"rules with covers: in the tree: {sorted(covering - precedent)} != "
        f"CORPUS_RULES keys: {sorted(CORPUS_RULES)} — a covers:-bearing rule "
        "is missing its drift guards, or a key was dropped")
    assert not covering & set(ADOPTED_RULES), (
        f"{sorted(covering & set(ADOPTED_RULES))} declares covers: — the "
        "carve-out refuses that shape, so the adoption is no longer one")


@pytest.mark.parametrize("rule_id,slug", sorted(CORPUS_RULES.items()))
def test_rule_is_declared_and_covers_its_class(rule_id, slug):
    """Each rule loads, reports without blocking, and covers its slug — the
    seam that stops `memory stats` re-proposing a rule that already shipped."""
    rules = _rules_by_id()
    assert rule_id in rules, (
        f"{rule_id} is not declared in {RULES_DIR} — the corpus class "
        f"{slug!r} cleared the proposal bar and has no rule again")
    rule = rules[rule_id]
    assert rule.engine == "claude", (
        f"{rule_id}: these classes need judgment, not a regex — engine was "
        f"{rule.engine!r}")
    assert rule.severity == "MEDIUM", (
        f"{rule_id}: corpus rules ship non-blocking (blocking_severities is "
        f"[HIGH]) while they earn a precision history; severity was "
        f"{rule.severity!r}")
    assert slug in rule.covers, (
        f"{rule_id} does not cover {slug!r}; `memory stats` will re-propose "
        "a rule that already shipped")


@pytest.mark.parametrize("rule_id,slug", sorted(CORPUS_RULES.items()))
def test_legacy_ids_are_grandfathered(rule_id, slug):
    """`covers:` makes attest's validator refuse `unmapped:<slug>`, and E-02
    calls the same validator over every committed shard. Without the
    grandfather entry the rule retroactively invalidates the evidence that
    justified writing it, and certification drops."""
    overlay = (ROOT / ".warden" / "certification.yaml").read_text()
    assert f"unmapped:{slug}" in overlay, (
        f"unmapped:{slug} is covered by {rule_id} but not grandfathered in "
        ".warden/certification.yaml — every committed shard that filed under "
        "it now fails E-02")


@needs_corpus
@pytest.mark.parametrize("rule_id,slug", sorted(CORPUS_RULES.items()))
def test_backtest_numbers_cannot_drift(rule_id, slug):
    """The backtest artifact's counts equal the corpus, field by field.

    Derived, not typed: the counts come from `records_from_shards` at test
    time. rate and wilson_lb are recomputed from the same sample, so two
    figures from denominators that could not both be true cannot ship
    silently.
    """
    candidates = sorted(BACKTESTS.glob(f"{rule_id}-promote-*.json"))
    assert candidates, (
        f"no promote backtest for {rule_id} under {BACKTESTS} — S-05 needs "
        "one, and hue's proposal bar demands measured cost before adoption")
    doc = json.loads(candidates[-1].read_text())
    assert doc["action"] == "promote"
    assert doc["rule_id"] == rule_id
    assert doc["from_candidate"] == f"unmapped:{slug}"

    derived = _class_counts(slug)
    assert derived["judged"] >= 3, (
        f"{slug}: fewer than 3 judged records in the committed shards — the "
        "proposal bar was never met and this rule has no evidence")
    for field in ("judged", "upheld", "refuted", "dismissed"):
        assert doc[field] == derived[field], (
            f"{rule_id} backtest {field}={doc[field]} but the committed "
            f"shards derive {derived[field]} — a typed count has rotted")
    assert abs(doc["rate"] - derived["upheld"] / derived["judged"]) < 0.001
    assert abs(doc["wilson_lb"]
               - wilson_lower_bound(derived["upheld"], derived["judged"])) < 0.001
    # These backtests carry a rules_version, but this
    # test does NOT require it to be CURRENT: S-05 needs SOME backtest at the
    # current version, which a rule change supplies with its own fresh
    # artifact — pinning these backtests to every later rule change would
    # break append-only. That "some backtest is current" invariant lives in
    # test_certify::test_this_repos_own_backtests_name_the_current_rules_version.
    assert isinstance(doc.get("rules_version"), str) and doc["rules_version"]


@pytest.mark.parametrize("rule_id,slug", sorted(ADOPTED_RULES.items()))
def test_adopted_rule_is_the_shape_the_carve_out_admits(rule_id, slug):
    """The adopted rule file passes the reader the cage's carve-out applies
    to an ADDED rule file — `_adoption_problem`, on the committed bytes —
    which refuses a blocking severity, a `covers:` and an `implements:`,
    each a way to raise enforcement below the severity check. Beyond that
    it is a claude rule (the class needs judgment) and its tag is in the
    vocabulary, so findings filed under it carry a name ingest accepts."""
    from warden import autonomy as autonomy_mod

    rules = _rules_by_id()
    assert rule_id in rules, f"{rule_id} is not declared in {RULES_DIR}"
    rule = rules[rule_id]
    assert rule.engine == "claude", (
        f"{rule_id}: the class needs judgment, not a regex — engine was "
        f"{rule.engine!r}")
    blocking = frozenset(
        yaml.safe_load((ROOT / "repo.yaml").read_text())["review"]
        ["blocking_severities"])
    problem, severity = autonomy_mod._adoption_problem(
        (RULES_DIR / f"{rule_id}.md").read_text(), rule_id, blocking)
    assert problem is None, f"{rule_id}: {problem}"
    assert severity == rule.severity
    vocab = yaml.safe_load(
        (ROOT / ".warden" / "memory" / "tags.yaml").read_text())
    assert slug in (vocab.get("tags") or {}), (
        f"{slug!r} is not a declared tag in .warden/memory/tags.yaml")
    assert slug in (RULES_DIR / f"{rule_id}.md").read_text(), (
        f"{rule_id} never names the tag {slug!r} it was adopted for")


@needs_corpus
@pytest.mark.parametrize("rule_id,slug", sorted(ADOPTED_RULES.items()))
def test_adopted_rules_tag_clears_the_bar_for_writing_a_rule(rule_id, slug):
    """The bar for WRITING a rule is the candidate bar — 3+ judged, upheld
    more often than not — read over the tag, since the class has no
    candidate row. Derived, never typed: the rule body states its count as
    a dated snapshot, and this is the floor under it."""
    derived = _tag_counts(slug)
    assert derived["judged"] >= tags_mod.DECLARE_MIN_N, (
        f"{slug}: {derived['judged']} judged record(s) carry the tag, below "
        f"the {tags_mod.DECLARE_MIN_N}-case bar {rule_id} was adopted on")
    assert derived["upheld"] * 2 > derived["judged"], (
        f"{slug}: {derived['upheld']} upheld of {derived['judged']} — the "
        f"class {rule_id} draws on is no longer upheld more often than not")


@needs_corpus
@pytest.mark.parametrize("rule_id,slug", sorted(ADOPTED_RULES.items()))
def test_adopted_rule_carries_the_artifact_the_writer_emits(rule_id, slug):
    """The artifact beside an adopted rule is the `adopt` record `warden
    autonomy adopt` writes, held to the reader the carve-out applies to an
    ADDED backtest — `_adopt_artifact_problem`, on the committed bytes:
    `judged: 0`, no precision field, a severity equal to the rule file's own.
    A promote-shaped tally here would be a measurement nothing backs. The
    diff-binding half of the carve-out (the artifact names a rule the same
    diff added) is a property of a range, not of a tree, and is not asked
    here."""
    from warden import autonomy as autonomy_mod

    candidates = sorted(BACKTESTS.glob(f"{rule_id}-adopt-*.json"))
    assert candidates, (
        f"no adopt artifact for {rule_id} under {BACKTESTS} — the rule bumps "
        "rules_version with nothing evidencing it (S-05)")
    doc = json.loads(candidates[-1].read_text())
    assert doc["action"] == "adopt"
    assert doc["rule_id"] == rule_id
    assert "from_candidate" not in doc, (
        f"{rule_id}: an adopt artifact names no candidate row — the class "
        "it draws on never held one")
    problem = autonomy_mod._adopt_artifact_problem(
        doc, _rules_by_id()[rule_id].severity)
    assert problem is None, f"{candidates[-1].name}: {problem}"


def test_enforcement_truth_excludes_rule_bodies():
    """The rule hunts claim-language, and rule bodies quote the very claims
    they hunt (the self-reference trap). The
    exclude ships with the rule on day one, not after the first self-hit."""
    rule = _rules_by_id().get("enforcement-truth")
    if rule is None:
        pytest.fail("enforcement-truth not declared")
    assert ".warden/rules/**" in rule.excludes, (
        "enforcement-truth must exclude .warden/rules/** — its body and every "
        "other rule body quote enforcement claims as examples (the "
        "secrets-in-diff precedent)")


def test_every_covers_bearing_rule_reports_without_blocking():
    """The autonomy ladder's foundational sentence: 'every covers-bearing
    rule ships MEDIUM, enforced by test'. Pinning only CORPUS_RULES would let
    fail-closed and wiki-fidelity drift to HIGH with the suite green while
    the sentence stayed present-tense, so the whole set is pinned: a
    covers-bearing rule that blocks is a promotion, and promotions are a
    human act with a precision record, never a severity edit."""
    for rule in rules_mod.load_rules(RULES_DIR):
        if rule.covers:
            assert rule.severity == "MEDIUM", (
                f"{rule.id} covers {list(rule.covers)} and ships "
                f"{rule.severity} — corpus-written rules report while they "
                "earn a precision history; promotion to blocking needs 10+ "
                "judged, a 0.7 floor, and a person")


ATTRIBUTION_RULE = RULES_DIR / "attribution-holds.md"
ATTRIBUTION_BACKTEST = BACKTESTS / "attribution-holds-promote-20260915.json"


def _top_level_distribution(slug: str) -> dict[str, int]:
    """Judged records of a candidate class per top-level area, derived from
    the committed shards — the same sample `_class_counts` totals."""
    dist: dict[str, int] = {}
    for rec in records_from_shards(ROOT):
        if rec.get("rule_id") != f"unmapped:{slug}":
            continue
        if rec.get("status") not in _JUDGED_STATUSES:
            continue
        where = rec.get("file") or ""
        top = where.split("/")[0] if "/" in where else where
        dist[top] = dist.get(top, 0) + 1
    return dist


def _rule_distribution_run() -> str:
    """The frontmatter sentence that justifies `applies_to: ["**"]`, comment
    markers stripped, cut at the claim it supports."""
    out: list[str] = []
    for line in ATTRIBUTION_RULE.read_text().splitlines():
        if not line.startswith("#"):
            if out:
                break
            continue
        body = line.lstrip("#").strip()
        if body.startswith("applies_to is **"):
            out.append(body)
        elif out:
            out.append(body)
        if out and "No directory carve-out" in body:
            break
    run = " ".join(out)
    return run[:run.index("No directory carve-out")] if run else ""


def _note_distribution_run() -> str:
    """The same sentence in the promote artifact's `note` — the half of the
    artifact no count field re-derives."""
    note = json.loads(ATTRIBUTION_BACKTEST.read_text())["note"]
    start = note.index("Distribution by top-level directory:")
    return note[start:note.index("which is what set applies_to", start)]


@needs_corpus
@pytest.mark.parametrize("where", ("rule-frontmatter", "promote-note"))
def test_attribution_distribution_prose_matches_the_shards(where):
    """A stated distribution must total the sample it is stated over.

    Both surfaces enumerate WHERE the evidence-attribution class landed, and
    that enumeration is the only stated evidence for `applies_to: ["**"]`.
    Round 1 of this branch shipped both summing to 33 over a 32-record
    sample, by counting a record the rule's own `covers:` refusal drops — the
    rule's own second shape ("the count its own source states differently")
    inside the rule being promoted. Nothing re-derived either sentence:
    test_backtest_numbers_cannot_drift reads the artifact's count FIELDS and
    never its note.

    Derived, not typed: the buckets come from `records_from_shards` at test
    time. Parentheticals restate a bucket already counted (".warden 17 (11 of
    them decision shards)") so they are dropped before summing, which is also
    why `.warden` is checked by the total rather than by name — the rule
    splits it across two buckets and the note does not."""
    dist = _top_level_distribution("evidence-attribution")
    judged = sum(dist.values())
    commit_records = sum(n for k, n in dist.items() if k.startswith("COMMIT"))
    in_files = judged - commit_records

    run = (_rule_distribution_run() if where == "rule-frontmatter"
           else _note_distribution_run())
    assert run.strip(), (
        f"{where}: no distribution sentence found — the guard cannot check a "
        "claim that has been reworded out of reach, and silence here would "
        "be indistinguishable from a passing check")
    flat = re.sub(r"\([^)]*\)", "", run)
    numbers = [int(n) for n in re.findall(r"\d+", flat)]
    assert commit_records == 1 and "one commit message" in flat, (
        f"{where}: the class holds {commit_records} commit-message records "
        "and the sentence spells one in words — the arithmetic below assumes "
        "that spelling")
    assert sum(numbers) + commit_records == judged, (
        f"{where}: the stated distribution {numbers} plus the commit message "
        f"sums to {sum(numbers) + commit_records}, over a class the shards "
        f"derive as {judged} judged records ({dist}) — a bucket counts "
        "something the sample does not")
    assert sum(numbers) == in_files, (
        f"{where}: {sum(numbers)} records stated in files, {in_files} derived")
    for area in ("tests", "docs", "warden", "cage"):
        assert re.search(rf"(?<![.\w]){area}[\w./-]* {dist[area]}\b", flat), (
            f"{where}: the sentence does not give {area}/ as {dist[area]} — "
            f"the shards derive {dist} and a bucket has drifted from them")


@needs_corpus
def test_the_external_source_shape_stands_outside_the_thirty_two():
    """The rule's sixth shape has no instance among the records it is listed
    under, and says so.

    Its only corpus instance is the `unsupported-source-claim` record on
    warden/guardrails/catalog.yaml, which the rule's own `covers:` refusal
    pushes out of the class — so the shape is kept (a real judged record
    would otherwise be lost) with a clause placing it outside the counted
    sample. The body introduces the list as "the ones that recurred here",
    ten lines under "Thirty-two judged records", so a shape sourced only from
    an excluded record would read as one of the thirty-two.

    Derived: the instance still exists, is judged, and shares no file with
    the counted class."""
    records = records_from_shards(ROOT)
    counted = [r for r in records
               if r.get("rule_id") == "unmapped:evidence-attribution"
               and r.get("status") in _JUDGED_STATUSES]
    instance = [r for r in records
                if r.get("rule_id") == "unmapped:unsupported-source-claim"
                and r.get("status") in _JUDGED_STATUSES]
    assert instance, (
        "no judged `unsupported-source-claim` record in the corpus — the "
        "sixth shape of attribution-holds now rests on nothing at all")
    assert not ({r.get("file") for r in instance}
                & {r.get("file") for r in counted}), (
        "the coverage-claim record shares a file with the counted class — "
        "the shape's disclaimer is now the wrong correction")

    body = ATTRIBUTION_RULE.read_text()
    head = "- **Coverage claimed against an external source.**"
    assert head in body, f"the sixth shape is gone from {ATTRIBUTION_RULE}"
    shape = body[body.index(head):]
    shape = shape[:shape.index("\n\n")]
    assert "thirty-two" in shape and "outside" in shape, (
        "the sixth shape no longer says its instance sits outside the "
        f"thirty-two: {shape!r} — a reader counts it among them")
