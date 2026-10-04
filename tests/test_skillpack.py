"""Skill pack + local marketplace integrity.

The marketplace manifest, plugin manifest, skill frontmatter, and the
policy-file contract are all consumer-facing surfaces — CI must catch a
rename or typo before a consumer's install does.
"""

import ast
import functools as _ft
import inspect
import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from conftest import tracked
from private_evidence import needs_corpus

ROOT = Path(__file__).parent.parent
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"

# The policy contract (docs/wiki/Skills-Policy.md): required H2 sections every
# repo's .warden/skills-policy.md must carry.
REQUIRED_POLICY_SECTIONS = [
    "## Verify",
    "## Autonomy scope",
    "## Forbidden paths",
    "## Review charter",
    "## Integrations",
    "## Shipping",
]

PACK_SKILLS = ["pre-pr-review", "autonomous-run", "review-queue", "retro",
               "deliver", "intake", "ship", "rule-advisor", "orchestrate"]


def load_marketplace() -> dict:
    return json.loads(MARKETPLACE.read_text())


def test_marketplace_manifest_shape():
    data = load_marketplace()
    assert data["name"] == "nightgate"
    assert data["owner"]["name"]
    assert data["plugins"], "marketplace lists no plugins"
    for plugin in data["plugins"]:
        assert plugin["name"] and plugin["source"].startswith("./")


def test_plugin_dirs_resolve_and_names_match():
    for plugin in load_marketplace()["plugins"]:
        plugin_dir = ROOT / plugin["source"]
        manifest = plugin_dir / ".claude-plugin" / "plugin.json"
        assert manifest.is_file(), f"{plugin['name']}: missing plugin.json"
        assert json.loads(manifest.read_text())["name"] == plugin["name"]


def test_pack_skills_present_with_valid_frontmatter():
    skills_dir = ROOT / "skills" / "nightgate-skills" / "skills"
    found = sorted(p.name for p in skills_dir.iterdir() if p.is_dir())
    assert found == sorted(PACK_SKILLS)
    for name in PACK_SKILLS:
        text = (skills_dir / name / "SKILL.md").read_text()
        assert text.startswith("---\n"), f"{name}: missing frontmatter"
        frontmatter = text.split("---")[1]
        assert f"name: {name}" in frontmatter
        assert "description:" in frontmatter


def test_contract_documented_and_example_satisfies_it():
    docs = (ROOT / "docs" / "wiki" / "Skills-Policy.md").read_text()
    example = (ROOT / "examples" / "hello-svc" / ".warden" / "skills-policy.md").read_text()
    for section in REQUIRED_POLICY_SECTIONS:
        assert f"`{section}`" in docs, f"contract section {section} undocumented"
        assert f"\n{section}\n" in example, (
            f"portability fixture missing required section {section}")


# ── Build disciplines: the craft layer ────────────────────────────────────
OPTIONAL_POLICY_SECTIONS = ["## Build disciplines"]


def test_autonomous_run_keeps_the_current_eligibility_and_handoff_names():
    """What the retired bridges leave behind must still be pinned: the
    CURRENT markers and handoff filenames are the contract a runner and a
    tracker actually speak."""
    text = (ROOT / "skills" / "nightgate-skills" / "skills" / "autonomous-run"
            / "SKILL.md").read_text()
    for marker in ("`auto-attempted:`", "`no-auto`"):
        assert marker in text, f"autonomous-run dropped eligibility marker {marker}"
    for handoff in (".run-summary", ".run-progress", ".run-resume"):
        assert handoff in text, f"autonomous-run dropped handoff file {handoff}"


def test_the_tracker_table_states_each_skills_need_and_the_cage_stays_tracker_bound(tmp_path):
    """agentops-hy6o.10: the gate needs no tracker, so the pack says which
    commands do. Skill-Pack.md's table gives each pack skill the need
    `need_of` records (a skill added without a row, or a row whose verdict
    moves, fails here); exactly the skills recorded "default" carry a
    `--no-tracker` section, and the commit form deliver's section names is one
    the platform's own validator accepts; and the cage says it has no such
    mode, in its skill and in its row, so nobody reads its absence as an
    oversight."""
    import subprocess
    # What each pack skill needs from the tracker, as the table states it:
    # "no" (runs without it), "partly", "default" (needs it unless invoked
    # with --no-tracker) or "bound" (always needs it). A skill added, or a
    # need changed, moves this map and the table together.
    need_of = {
        "rule-advisor": "no", "pre-pr-review": "partly",
        "deliver": "default", "ship": "default",
        "intake": "bound", "orchestrate": "bound", "review-queue": "bound",
        "retro": "bound", "autonomous-run": "bound",
    }
    cell_of = {"no": "**no**", "partly": "**partly**",
               "default": "**yes by default**", "bound": "**yes"}
    heading = "## Without a tracker — `--no-tracker`"
    assert set(need_of) == set(PACK_SKILLS), "need_of lost a pack skill"
    skills = ROOT / "skills" / "nightgate-skills" / "skills"
    wiki = (ROOT / "docs" / "wiki" / "Skill-Pack.md").read_text()
    section = wiki.split("## Which commands need a tracker", 1)
    assert len(section) == 2, "Skill-Pack.md lost its tracker section"
    table = section[1].split("\n## ", 1)[0]
    rows = {m.group(1): m.group(2) for m in
            re.finditer(r"^\| `([a-z-]+)`[^|]*\| (.*) \|$", table, re.M)}
    assert "warden" in rows and rows["warden"].startswith("**no**"), (
        "the table no longer says the gate needs no tracker")
    for name, need in need_of.items():
        assert name in rows, f"Skill-Pack.md's tracker table has no row for {name}"
        cell = rows[name]
        assert cell.startswith(cell_of[need]), (
            f"{name}'s row reads {cell[:40]!r}, not the {need!r} need")
        if need == "bound":
            assert not cell.startswith("**yes by default**"), (
                f"{name}'s row offers a --no-tracker mode it does not have")
    assert "no `--no-tracker` mode" in rows["autonomous-run"], (
        "the cage's row no longer says it has no --no-tracker mode")

    with_section = {n for n in PACK_SKILLS
                    if heading in (skills / n / "SKILL.md").read_text()}
    expected = {n for n, need in need_of.items() if need == "default"}
    assert with_section == expected, (
        f"--no-tracker sections in {sorted(with_section)}, but the table "
        f"records {sorted(expected)} as the skills that have one")

    deliver = (skills / "deliver" / "SKILL.md").read_text()
    form = re.search(r"for example\s+`(\(no-bead: [^)]+\))`", deliver)
    assert form, "deliver no longer names an example no-item subject form"
    msg = tmp_path / "msg"
    msg.write_text(f"docs(skills): a task taken from the prompt {form.group(1)}\n")
    lint = subprocess.run(["sh", str(ROOT / "scripts" / "commit-lint.sh"),
                           "--header-only", str(msg)], capture_output=True)
    assert lint.returncode == 0, (
        f"the subject deliver --no-tracker tells a builder to write fails "
        f"commit-lint: {lint.stderr.decode()}")

    cage = (skills / "autonomous-run" / "SKILL.md").read_text()
    assert "tracker-bound and has no `--no-tracker` mode" in cage, (
        "autonomous-run no longer says the cage needs the tracker")


def test_example_carries_the_optional_section_as_fixture():
    example = (ROOT / "examples" / "hello-svc" / ".warden"
               / "skills-policy.md").read_text()
    for section in OPTIONAL_POLICY_SECTIONS:
        assert f"\n{section}\n" in example, (
            f"portability fixture should demonstrate {section}")


# The repair-round budget. `deliver` states "a repo may set a lower cap, never
# a higher one". Consumer-side enforcement is certify's R-12, which reads
# repo.yaml's `repair.budget` (tests/test_repair_budget.py). What this checks
# is the other half: the cap the deliver SKILL states is the cap certify and
# the schema enforce, and every repo.yaml this repo ships sits within it.
_PLATFORM_CAP = re.compile(r"Repair runs in \*\*rounds\*\*, capped at (\d+)")

DELIVER_SKILL = (ROOT / "skills" / "nightgate-skills" / "skills" / "deliver"
                 / "SKILL.md")


def platform_repair_cap() -> int:
    match = _PLATFORM_CAP.search(DELIVER_SKILL.read_text())
    assert match, (f"{DELIVER_SKILL} no longer states the repair-round cap in "
                   "the form this derivation reads — reword the test with it, "
                   "never drop the assertion")
    return int(match.group(1))


def repo_yaml_files() -> list[Path]:
    """Every repo.yaml this repo ships. Derived, not listed: a hardcoded
    pair silently excludes the next fixture someone adds.

    From the git index, not a filesystem walk: a glob descends
    `.claude/worktrees/`, and a worktree left from an orchestrate run is a
    full second checkout whose branch may change the declared budget — the
    local-red/CI-green split this class always takes.
    """
    found = tracked("repo.yaml") + tracked("*/repo.yaml")
    assert len(found) >= 2, f"repo.yaml discovery broke: {found}"
    return found


def test_the_deliver_cap_is_the_cap_certify_enforces():
    from warden import certify as certify_mod
    assert platform_repair_cap() == certify_mod.PLATFORM_REPAIR_CAP


def test_every_repo_yaml_budget_is_within_the_deliver_cap():
    import yaml
    cap = platform_repair_cap()
    for path in repo_yaml_files():
        doc = yaml.safe_load(path.read_text())
        declared = (doc.get("repair") or {}).get("budget")
        assert isinstance(declared, int) and not isinstance(declared, bool), (
            f"{path.relative_to(ROOT)} declares no integer repair.budget")
        assert 1 <= declared <= cap, (
            f"{path.relative_to(ROOT)} declares a repair budget of "
            f"{declared}, but deliver caps repair at {cap} rounds and says a "
            "repo may set a LOWER cap, never a higher one")


# Writing a rule for a recurring candidate class invalidates every committed
# shard that filed under that class's `unmapped:` slug — attest refuses the
# slug once a rule covers it, and certify's E-02 calls the same validator over
# the corpus, so a rule landed without its grandfather entry drops the
# certified level. The two edits are a PAIR, and this says so.
def _committed_unmapped_slugs() -> set[str]:
    import json
    slugs = set()
    for shard in (ROOT / ".warden" / "memory" / "attest").glob("*.json"):
        try:
            doc = json.loads(shard.read_text())
        except (OSError, ValueError):
            continue
        for record in doc.get("records", []):
            rule_id = record.get("rule_id", "")
            if isinstance(rule_id, str) and rule_id.startswith("unmapped:"):
                slugs.add(rule_id)
    return slugs


def _declared_covers() -> dict[str, str]:
    import yaml
    covered = {}
    for path in sorted((ROOT / ".warden" / "rules").glob("*.md")):
        text = path.read_text(encoding="utf-8-sig")
        if not text.startswith("---\n"):
            continue
        meta = yaml.safe_load(text.split("\n---\n", 1)[0][4:]) or {}
        for slug in meta.get("covers") or []:
            covered[str(slug)] = str(meta.get("id"))
        covered.setdefault(str(meta.get("id")), str(meta.get("id")))
    return covered


def _grandfathered() -> set[str]:
    import yaml
    overlay = ROOT / ".warden" / "certification.yaml"
    if not overlay.is_file():
        return set()
    doc = yaml.safe_load(overlay.read_text()) or {}
    return {str(i) for i in (doc.get("grandfathered_rule_ids") or [])}


@needs_corpus
def test_a_rule_covering_a_class_grandfathers_that_class_committed_ids():
    """The pair that has to move together. A rule may only start covering a
    slug the corpus already used if those committed ids are declared — or
    E-02 fails across the whole corpus and certification drops."""
    covered = _declared_covers()
    grandfathered = _grandfathered()
    orphaned = sorted(
        rule_id for rule_id in _committed_unmapped_slugs()
        if rule_id[len("unmapped:"):] in covered and rule_id not in grandfathered)
    assert not orphaned, (
        "these committed rule_ids are now covered by a declared rule but are "
        f"not grandfathered in .warden/certification.yaml: {orphaned}. E-02 "
        "runs attest's validator over every shard, so it will refuse them and "
        "certification will drop. Declare them, or the rule must not cover "
        "that slug.")


@needs_corpus
def test_nothing_is_grandfathered_that_the_corpus_does_not_contain():
    """The escape stays honest in the other direction too: an entry for an id
    no shard carries is a pre-emptive waiver, not a record of history."""
    stale = sorted(_grandfathered() - _committed_unmapped_slugs())
    assert not stale, (
        f"grandfathered ids no committed shard carries: {stale} — the list "
        "records what the corpus already contains, it does not pre-authorize")


def test_every_grandfathered_id_is_actually_covered_by_a_rule():
    """The other half of the pair. The first test catches covering a slug
    without grandfathering it; this catches grandfathering a slug nothing
    covers — an inert waiver, and a sign `covers:` was dropped. Without it
    the suite stays green with `covers:` deleted while memory stats silently
    resumes proposing a rule that has already shipped."""
    covered = _declared_covers()
    inert = sorted(rule_id for rule_id in _grandfathered()
                   if rule_id[len("unmapped:"):] not in covered)
    assert not inert, (
        f"grandfathered ids no declared rule covers: {inert}. The waiver does "
        "nothing, and `memory stats` will re-propose the class as a candidate "
        "rule that already shipped — drop the entry, or restore the `covers:` "
        "that made it necessary.")


@needs_corpus
def test_every_backtest_artifact_is_internally_consistent():
    """A backtest is evidence for a rule change, and certify prints its
    judged count, so its arithmetic is recomputed: a rate and a wilson_lb
    over different denominators are two figures no single sample produces.

    Scoped to the fields an artifact actually carries. S-05 requires only
    action, rule_id and judged, so a contract-minimum `pause` artifact must
    pass here rather than die on a bare KeyError that never says what a
    backtest must hold.
    """
    import json

    from warden.memory import wilson_lower_bound

    artifacts = sorted((ROOT / ".warden" / "memory" / "backtests").glob("*.json"))
    assert artifacts, "no backtest artifacts — derivation broke"
    for path in artifacts:
        doc = json.loads(path.read_text())
        name = path.name
        for required in ("rule_id", "action", "judged"):
            assert required in doc, (
                f"{name}: missing {required!r} — S-05 reads it, so an artifact "
                "without it is not a backtest")
        judged = doc["judged"]
        assert isinstance(judged, int) and judged >= 0, (
            f"{name}: judged must be a non-negative int, got {judged!r}")

        counts = [k for k in ("upheld", "refuted", "dismissed") if k in doc]
        if len(counts) == 3:
            parts = doc["upheld"] + doc["refuted"] + doc["dismissed"]
            assert parts == judged, (
                f"{name}: upheld+refuted+dismissed={parts} but judged={judged}")
        if "rate" in doc and judged:
            assert abs(doc["rate"] - doc["upheld"] / judged) < 0.001, (
                f"{name}: rate {doc['rate']} != {doc['upheld']}/{judged}")
        if "wilson_lb" in doc and judged:
            expected = wilson_lower_bound(doc["upheld"], judged)
            assert abs(doc["wilson_lb"] - expected) < 0.001, (
                f"{name}: wilson_lb {doc['wilson_lb']} but the repo's own "
                f"wilson_lower_bound({doc['upheld']}, {judged}) is "
                f"{expected:.3f}")


@needs_corpus
def test_a_backtest_from_a_candidate_keeps_that_class_covered():
    """The link nothing else records. `from_candidate` says which candidate
    class a rule was written to answer, so the rule must still cover it.

    Without this, deleting BOTH `covers:` and the grandfather entry leaves the
    whole suite green while `memory stats` quietly resumes proposing a rule
    that has already shipped — a coordinated two-file edit neither other
    guard can see.
    """
    import json

    covered = _declared_covers()
    for path in sorted((ROOT / ".warden" / "memory" / "backtests").glob("*.json")):
        doc = json.loads(path.read_text())
        origin = doc.get("from_candidate")
        if not origin:
            continue
        slug = origin[len("unmapped:"):] if origin.startswith("unmapped:") else origin
        assert covered.get(slug) == doc["rule_id"], (
            f"{path.name} records that {doc['rule_id']!r} was written to answer "
            f"the candidate class {slug!r}, but no declared rule covers it "
            f"(covered by: {covered.get(slug)!r}). `memory stats` will "
            "re-propose a rule that already shipped.")


RETRO = (ROOT / "skills" / "nightgate-skills" / "skills" / "retro" / "SKILL.md")


def _flat(text: str) -> str:
    """Collapse whitespace, so a guard pins the PHRASE rather than where the
    author happened to wrap the line."""
    return " ".join(text.split())


def _retro_section(heading: str) -> str:
    text = RETRO.read_text()
    start = text.index(heading)
    nxt = text.find("\n## ", start + 1)
    return text[start:nxt if nxt != -1 else len(text)]


# ── Skill-step provenance and the quiet-step report ────────────────────────
from warden import provenance as _prov  # noqa: E402
from warden import tags as _tags  # noqa: E402

SKILLS_DIR = ROOT / "skills" / "nightgate-skills" / "skills"


def test_every_pack_skill_declares_provenance_with_a_valid_as_of():
    """Each skill declares an as_of — the last date the
    protocol was checked against the system it describes. A missing or
    malformed ## Provenance block raises, so a stub cannot pass as one."""
    for name in PACK_SKILLS:
        text = (SKILLS_DIR / name / "SKILL.md").read_text()
        doc = _prov.parse_provenance_section(text)
        assert doc is not None, f"{name}: no ## Provenance section"
        assert _prov.ISO_DATE_RE.match(doc["as_of"]), (
            f"{name}: as_of {doc['as_of']!r} is not YYYY-MM-DD")


def test_every_provenance_evidence_ref_is_well_formed():
    """The citation convention: every cited evidence ref classifies to a
    known kind, and a `tag:` ref names a DECLARED class. A malformed ref or a
    tag naming no class is a provenance defect the retro would surface — the
    pack's own blocks must be clean."""
    vocab = _tags.load_vocab(ROOT)
    aliases = _tags.load_aliases(ROOT)
    problems = []
    for name in PACK_SKILLS:
        doc = _prov.parse_provenance_section((SKILLS_DIR / name / "SKILL.md").read_text())
        for step in doc["steps"]:
            kind, _ = _prov.classify_evidence(step["evidence"], vocab, aliases)
            if kind in ("malformed", "unrecognized-tag"):
                problems.append(f"{name}: {step['evidence']!r} -> {kind}")
    assert not problems, f"malformed provenance refs: {problems}"


def test_the_pack_cites_at_least_one_measurable_class():
    """The quiet-step mechanism is inert with no `tag:` citation anywhere —
    only tag refs are measurable against recurrence. At least one earned step
    must cite a corpus class, or the quiet-step report has nothing to act on."""
    vocab = _tags.load_vocab(ROOT)
    aliases = _tags.load_aliases(ROOT)
    measurable = 0
    for name in PACK_SKILLS:
        doc = _prov.parse_provenance_section((SKILLS_DIR / name / "SKILL.md").read_text())
        for step in doc["steps"]:
            if _prov.classify_evidence(step["evidence"], vocab, aliases)[0] == "tag":
                measurable += 1
    assert measurable >= 1, "no step cites a measurable tag: class — quiet-step report is inert"


def test_stats_surfaces_the_quiet_step_section():
    """End to end: memory stats over this repo carries the quiet-step rows and
    renders the section header the retro reads."""
    from warden import memory
    doc = memory.stats(ROOT, rules_dir=ROOT / ".warden" / "rules")
    assert "quiet_steps" in doc
    assert doc["quiet_steps"], "the pack's provenance produced no rows"
    rendered = memory.render_stats(doc)
    assert "SKILL-STEP PROVENANCE" in rendered
    # the guarantee the whole mechanism rests on: never an auto-removal
    assert "NEVER a removal" in rendered


# ── Harness assumptions across the pack ─────────────────────────────────────


def test_every_sibling_reference_is_a_declared_assumption():
    """Skill dependencies are declarations rather than prose, so they can be
    checked: any
    skill whose body names a sibling as `nightgate-skills:<name>` must carry
    that name in its `assumes: skills:` list, and may not assume a skill the
    pack does not ship."""
    ref_re = re.compile(r"nightgate-skills:([a-z][a-z0-9-]*)")
    skills_dir = ROOT / "skills" / "nightgate-skills" / "skills"
    for name in PACK_SKILLS:
        text = (skills_dir / name / "SKILL.md").read_text()
        doc = _prov.parse_provenance_section(text)
        declared = set(doc["assumes"]["skills"])
        referenced = set(ref_re.findall(text)) - {name}
        undeclared = sorted(referenced - declared)
        assert not undeclared, (
            f"{name}: references {undeclared} in prose but does not declare "
            "the assumption — prose is exactly what the pre-flight existed to "
            "replace")
        unknown = sorted(declared - set(PACK_SKILLS))
        assert not unknown, f"{name}: assumes skills the pack does not ship: {unknown}"


def test_the_packs_own_assumptions_preflight_clean():
    """End to end over the real pack: every declared and referenced sibling
    resolves against the pack as shipped. This is the platform-side proof the
    consumer-facing `warden skills preflight` rests on."""
    rows = _prov.preflight_assumptions(SKILLS_DIR)
    problems = _prov.preflight_problems(rows)
    assert not problems, f"the shipped pack has stale assumptions: {problems}"
    # and the check saw the pack, rather than vacuously passing an empty dir
    assert sum(1 for r in rows if r["status"] == "resolved") >= 5


def test_subagent_dependent_skills_declare_it():
    """The two protocols that cannot run without subagents say so where the
    pre-flight can see it, not only in prose a harness never reads."""
    skills_dir = ROOT / "skills" / "nightgate-skills" / "skills"
    for name in ("pre-pr-review", "orchestrate"):
        doc = _prov.parse_provenance_section(
            (skills_dir / name / "SKILL.md").read_text())
        assert doc["assumes"]["subagents"] is True, (
            f"{name}: launches subagents but does not declare the assumption")


# ── the retro reads the whole tag audit section ────────────────────────────

# Labels that are DESCRIPTIONS rather than section names — the counted status
# line every clean report opens with, and the clean ceiling reading. The retro
# does not need either by name; it needs the lines that say something is
# WRONG, plus the ceiling headroom it must not read as absence. Listed rather
# than filtered by a rule, because a rule ("skip anything lowercase") would
# quietly skip the next lowercase problem line.
_AUDIT_LABELS_NOT_ENUMERATED = {"declared", "drift ceiling"}


def _rendered_audit_lines() -> list[str]:
    """Every line `warden memory stats`'s tag section can print, rendered.

    RENDERED FROM BOTH RENDERERS, never retyped. An enumeration typed into
    prose rots: a label added to `warden/tags.py` goes unnamed in the skill,
    so a retro can run to completion having never looked at it. A test that
    retyped the same labels would rot the same way on the next line added,
    which is why these come out of the renderer.

    BOTH renderers: driving `_render_ceiling` alone would leave a line added
    to `render_audit` — the half that prints UNKNOWN, NEAR-DUPLICATES and,
    loudest of all, "no vocabulary declared" — unnamed with this test green.

    The fixtures below drive every branch of both, INCLUDING `_render_ceiling`'s
    `ceiling is None` early return: that is the state of any repo that has not
    declared a ceiling, and a label added there would otherwise be invisible.
    """
    breached = _tags.render_audit({
        "declared": ["a"], "used": {"a": 1},
        "unknown": ["b"], "near_duplicates": [("a", "b")],
        "unused_declared": ["c"], "invalid_aliases": ["d -> e"],
        # `labelled` as well as `complaints`, because that is what
        # `ceiling_status` hands the renderer: the four complaint sources are
        # labelled apart at the merge site, where the source is known. A
        # fixture that passed only `complaints` would drive the hand-built-dict
        # fallback and the proof would read a label no real run can print.
        "ceiling": {"complaints": ["tags.yaml: could not be read"],
                    "labelled": ["TAG CEILING UNREADABLE (declared, so this "
                                 "is not 'no ceiling' and never headroom) — "
                                 "tags.yaml: could not be read"],
                    "at_bar": ["some-class"], "undecided": ["another-class"],
                    "ceiling": 1, "breached": True},
    })
    clean = _tags.render_audit({
        "declared": ["a"], "used": {"a": 1}, "unknown": [],
        "near_duplicates": [], "unused_declared": [], "invalid_aliases": [],
        "ceiling": {"complaints": [], "at_bar": [], "undecided": [],
                    "ceiling": 3, "breached": False},
    })
    undeclared = _tags.render_audit({
        "declared": [], "used": {}, "unknown": [], "near_duplicates": [],
        "unused_declared": [], "invalid_aliases": [],
        "ceiling": {"complaints": [], "at_bar": [], "undecided": [],
                    "ceiling": None, "breached": False},
    })
    return [ln for text in (breached, clean, undeclared)
            for ln in text.splitlines()[1:]]


def _audit_labels() -> list[str]:
    """The label at the head of each rendered line, deduplicated in order."""
    labels: list[str] = []
    for line in _rendered_audit_lines():
        # `-` inside the uppercase branch: NEAR-DUPLICATES truncated to
        # "NEAR" without it, and the enumeration then "named" a label that
        # does not exist while missing the one that does.
        m = re.match(r"([A-Z][A-Z -]*[A-Z]|[a-z][a-z -]*[a-z])",
                     line.strip())
        assert m, f"an audit line has no readable label: {line!r}"
        if m.group(1) not in labels:
            labels.append(m.group(1))
    return labels


def _enumerated_audit_labels() -> list[str]:
    """The labels the retro's enumeration is obliged to name."""
    return [lab for lab in _audit_labels()
            if lab not in _AUDIT_LABELS_NOT_ENUMERATED]


def _unnamed_audit_labels(text: str) -> list[str]:
    """Labels `memory stats` can print that `text` does not name.

    Extracted so a FIRES case can drive the real comparison. Inlined in the
    caller it was a comprehension whose result the assertion read, and
    replacing it with `[]` was a green mutation — the
    comprehension-with-no-non-empty-guarantee shape
    `.warden/rules/tests-bite.md` names.
    """
    flat = " ".join(text.split())
    return [lab for lab in _enumerated_audit_labels() if lab not in flat]


def _reconcile_bullet() -> str:
    """The audit-line enumeration itself, flattened — not the whole file.

    SCOPED so a label parked ANYWHERE else in the skill cannot satisfy the
    check whose message says "the retro's audit-line enumeration does not name
    them". A whole-file read would pass with `CEILING BREACHED` deleted from
    the bullet and the bare words parked in an HTML comment before
    `## 3 · Propose`.

    The scope is NOT justified by the phrase pins below: each pinned phrase
    occurs exactly once in the file, inside this bullet. What the slice buys
    is the label check above it.
    """
    propose = _retro_section("## 3 · Propose")
    start = propose.index("- **Reconcile the tag vocabulary**")
    rest = propose[start:]
    end = rest.index("\n- **", 1)
    return _flat(rest[:end])


# ── the retro reads every `memory stats` SECTION, by its header ───────────
#
# The same failure the audit-label guards below catch, one
# level up: the Gather step described `memory stats` as "a REVIEW EVENTS
# tally, then two sections" while the renderer printed nine, and LENSES and
# DISPATCHES were never named at all. The section list is therefore derived
# from warden's own renderers, never typed here — the retro's prose is
# checked against the code, which is what makes this a contract pin and not
# a wording pin (the 2026-09-14 prose-pin ruling, amended: contract pins on
# CLI output formats stay).

# `render_stats` indents this one's output under TAGS, so a literal in it is
# never a top-level section header. Subtracted BY NAME, with the reason, the
# way test_the_retro_names_every_audit_line_stats_can_print subtracts its
# own delegation — a rule ("skip anything in
# tags.py") would quietly skip the next renderer that IS a section.
_INDENTED_DELEGATES = {"render_audit"}


# A name the scan could not resolve, told apart from a name bound to None:
# `from .x import gone` binds None, and a scan that reads that as "not a
# delegate" is silent on exactly the call it cannot see (round 1 of this
# PR's review).
MISSING = object()


def _package_target(delegate, package):
    """`delegate` unwrapped, when it is this package's own code, else None.

    UNWRAPPED, because a renderer's shape is not its spelling either: the
    first cut asked `inspect.isfunction`, so the same section behind
    `@functools.lru_cache` or reached as a method on a module-level object
    was dropped with no assertion — mutated live in round 1, 40 tests passed
    both times. A builtin, a stdlib callable and a plain value print nothing
    of their own and come back None.
    """
    import functools
    import inspect

    target = delegate
    if isinstance(target, functools.partial):
        target = target.func
    target = inspect.unwrap(target)          # @lru_cache, @wraps, ...
    if inspect.ismethod(target):
        target = target.__func__
    if not (inspect.isfunction(target) or inspect.isclass(target)):
        return None
    if getattr(target, "__module__", "").split(".")[0] != package:
        return None
    return target


def _visible_names(tree, module):
    """The names visible inside the function: the module's globals plus what
    the function imports.

    `render_stats` imports inside its own body — `from . import provenance as
    _prov`, `from .tags import render_audit` — so the module's globals alone
    do not see every delegate. Those imports are resolved the way Python
    resolves them, which is what lets a call be looked up by its real name
    rather than matched by its spelling.
    """
    import ast
    import importlib

    ns = dict(vars(module))
    package = module.__package__ or module.__name__
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if node.module:
                    mod = importlib.import_module(
                        "." * node.level + node.module, package)
                    # MISSING, never None: `from .x import gone` binds the
                    # name to None and the caller then skips the call
                    # silently, which is the "resolves to nothing is
                    # skipped" the scan exists to refuse
                    ns[alias.asname or alias.name] = getattr(
                        mod, alias.name, MISSING)
                else:                        # `from . import provenance`
                    ns[alias.asname or alias.name] = importlib.import_module(
                        "." * node.level + alias.name, package)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                ns[alias.asname or alias.name.split(".")[0]] = \
                    importlib.import_module(alias.name)
    return ns


# A name bound in a way no static scan can see through — a parameter, a
# with/except/match target, a loop over anything but a literal tuple. A call
# through one is skipped, as always; `delegates to nothing` notices a
# function whose EVERY delegate arrives so
LOCAL = object()

# The nodes that open a scope of their own — a comprehension is one too
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
           ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _split(scope: ast.AST) -> tuple[list, list]:
    """(what Python evaluates in the ENCLOSING scope — decorators, defaults,
    bases, a comprehension's first iterable — and what it evaluates inside)."""
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [*scope.decorator_list, scope.args], scope.body
    if isinstance(scope, ast.Lambda):
        return [scope.args], [scope.body]
    if isinstance(scope, ast.ClassDef):
        return [*scope.decorator_list, *scope.bases, *scope.keywords], scope.body
    first, *rest = scope.generators
    elts = ([scope.key, scope.value] if isinstance(scope, ast.DictComp)
            else [scope.elt])
    return [first.iter], [*elts, first.target, *first.ifs, *rest]


def _walk(roots: list) -> Iterator[ast.AST]:
    """`roots` and their descendants in source order, never entering a nested
    scope (it binds its own names) — the scope node itself is yielded."""
    for root in roots:
        yield root
        if not isinstance(root, _SCOPES):
            yield from _walk(list(ast.iter_child_nodes(root)))


def _bindings(scope: ast.AST) -> dict[str, list]:
    """name -> every binding of it in `scope`'s OWN body, in source order:
    LOCAL (a parameter, a with/except/match target, a nested def or class —
    whose body `_calls` descends into), or the expression bound by `=`,
    `:=`, an augmented assignment, a paired unpack, or a loop over a literal
    tuple, which binds EACH element in turn; what pairs with nothing is bound
    to the whole statement, which `_follow` refuses. Per SCOPE, because the
    dropped fix's `ast.walk` applied an inner def's alias at the OUTER call
    site."""
    out: dict[str, list] = {}

    def bind(target, value):
        if isinstance(target, ast.Name):
            out.setdefault(target.id, []).append(value)
        elif isinstance(target, ast.Starred):
            bind(target.value, value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            paired = (isinstance(value, (ast.Tuple, ast.List))
                      and len(value.elts) == len(target.elts)
                      and not any(isinstance(e, ast.Starred)
                                  for e in (*target.elts, *value.elts)))
            for i, elt in enumerate(target.elts):
                bind(elt, value.elts[i] if paired else value)
        # `self.x = ...`, `d[k] = ...` bind no name

    def each(target, iterable):
        literal = isinstance(iterable, (ast.Tuple, ast.List, ast.Set)) and \
            not any(isinstance(e, ast.Starred) for e in iterable.elts)
        for elt in iterable.elts if literal else [LOCAL]:
            bind(target, elt)

    if isinstance(scope, ast.ClassDef):
        pass
    elif hasattr(scope, "args"):      # EVERY parameter kind
        for a in ast.iter_child_nodes(scope.args):
            if isinstance(a, ast.arg):
                out.setdefault(a.arg, []).append(LOCAL)
    else:                             # a comprehension
        each(scope.generators[0].target, scope.generators[0].iter)
    stored, annotated = set(), set()
    for node in _walk(_split(scope)[1]):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            stored.add(node.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                bind(target, node.value)
        elif isinstance(node, ast.AnnAssign):
            if node.value:
                bind(node.target, node.value)
            elif isinstance(node.target, ast.Name):
                annotated.add(node.target.id)   # `x: T` alone binds nothing
        elif isinstance(node, ast.AugAssign):
            bind(node.target, node)
        elif isinstance(node, ast.NamedExpr):
            bind(node.target, node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            each(node.target, node.iter)
        elif isinstance(node, ast.withitem) and node.optional_vars:
            bind(node.optional_vars, LOCAL)
        # bindings Python spells as a str field, not a Store Name — listed by
        # hand, since the net below has no node to catch them with
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            if node.name:
                out.setdefault(node.name, []).append(LOCAL)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            out.setdefault(node.rest, []).append(LOCAL)
        elif isinstance(node, ast.Global):
            for name in node.names:
                out.setdefault(name, []).append(node)
        elif isinstance(node, _SCOPES) and hasattr(node, "name"):
            out.setdefault(node.name, []).append(LOCAL)
    for node in ast.walk(scope):      # a nested `nonlocal` rebinds from outside
        if isinstance(node, ast.Nonlocal):   # the chain a call is read with
            for name in node.names:
                out.setdefault(name, []).append(node)
    # fail closed on the shape the net CAN see: a Store-context Name reached
    # by a statement not enumerated above would look unbound — a phantom
    unknown = stored - set(out) - annotated
    assert not unknown, f"bound by a statement this scan does not read: {unknown}"
    return out


def _resolve(name: str, chain: tuple, ns: dict, where: str,
             seen: tuple = ()) -> list:
    """Everything a call through `name` can reach, innermost scope outward:
    what each binding `_follow`s — ALL of them when the name is bound more
    than once, since any may be the one called — or the module's or
    builtins' object when no scope binds it."""
    import builtins

    binds = next((b[name] for b in chain if name in b), None)
    if binds is None:
        if name in ns:
            assert ns[name] is not MISSING, (
                f"{where} calls {name}(), which this module imports "
                "from somewhere that does not define it — the scan "
                "would silently skip whatever it prints")
            return [ns[name]]
        assert hasattr(builtins, name), (
            f"{where} calls {name}() and it resolves neither in "
            f"{where.rpartition('.')[0]}, nor in what the function imports, "
            "nor in the builtins — the scan would silently skip whatever it "
            "prints")
        return [getattr(builtins, name)]
    return [obj for value in binds
            for obj in _follow(name, value, chain, ns, where, seen)]


def _follow(name: str, value: object, chain: tuple, ns: dict, where: str,
            seen: tuple) -> list:
    """What ONE binding of `name` reaches: LOCAL for a parameter, a target or
    a lambda (its body is inside `fn`, being read); a name or an attribute of
    one, resolved; anything else — a call, an index, a conditional,
    `nonlocal`, an unpaired unpack — or a cycle, refused by name."""
    if value is LOCAL or isinstance(value, ast.Lambda):
        return [LOCAL]
    if isinstance(value, ast.Name):
        assert value.id not in (name, *seen), (
            f"{where} binds {name} in a cycle ({' = '.join((*seen, name))} "
            f"= {value.id}) — nothing to resolve")
        return _resolve(value.id, chain, ns, where, (*seen, name))
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
        assert value.value.id not in (name, *seen), (
            f"{where} binds {name} in a cycle — nothing to resolve")
        return [LOCAL if owner is LOCAL      # an attribute of a local value
                else _attribute(owner, value.value.id, value.attr, where)
                for owner in _resolve(value.value.id, chain, ns, where,
                                      (*seen, name))]
    assert False, (
        f"{where} binds {name} by `{ast.unparse(value)}` and reads through "
        "it — a binding the scan cannot follow, so a delegate behind it "
        "would go unread; bind the delegate by its name instead")


def _attribute(owner: object, holder: str, attr: str, where: str) -> object:
    """`holder.attr` on a resolved owner — a MODULE or a module-level OBJECT
    alike: `_ESCAPES.section(doc)` prints a section."""
    delegate = getattr(owner, attr, MISSING)
    assert delegate is not MISSING, (
        f"{where} calls {holder}.{attr}() and {holder} has no such attribute")
    return delegate


def _calls(scope: ast.AST, chain: tuple = ()) -> Iterator[tuple]:
    """(call, scope chain) for every call in `scope`, innermost scope first.
    What Python evaluates OUTSIDE the scope (`_split`) is read with the
    enclosing chain, and a class body is invisible from the defs inside it."""
    outer, body = _split(scope)
    inner = (_bindings(scope), *chain)
    for roots, seen_from in ((outer, chain), (body, inner)):
        for node in _walk(roots):
            if isinstance(node, ast.Call):
                yield node, seen_from
            elif isinstance(node, _SCOPES):
                hidden = isinstance(scope, ast.ClassDef) and seen_from is inner
                yield from _calls(node, chain if hidden else seen_from)


def _delegates_of(fn, skip=()):
    """Every function `fn` CALLS, resolved from ITS OWN source.

    ANY SPELLING, and that is the whole point. The first cut read the source
    with `(?:(\\w+)\\.)?(\\w*[Rr]ender\\w*)\\(`, which is not "any spelling"
    but "any name containing render" — a delegate named `_corpus_section` or
    `_lines_for` was unscanned, and the scanned/emitted equality below
    rescued that only for a section the hand-built `_rendered_stats` fixture
    happens to reach. A tenth section that is BOTH named another way AND
    behind a doc key the fixture omits — the ordinary idiom, `_render_
    candidates` is already behind `if doc.get("candidates")` — was invisible
    to both readings.

    So the calls are read as CALLS, off the AST, and resolved through the
    namespace `render_stats` actually runs in. What is kept is every call
    whose SOURCE lives in this package, however the callable is wrapped —
    a decorated one (`@lru_cache`, `functools.partial`), a class, a method
    reached through a module-level object — because the first cut kept only
    `inspect.isfunction` on a module holder, and a tenth section behind
    either of the first two shipped with the whole suite green (round 1 of
    this PR's review mutated both and watched 40 tests pass). A builtin
    (`sorted`, `len`) and a method on a LOCAL value (`lines.append`,
    `doc.get`) print nothing of their own and are skipped for what they are,
    not for how they are spelled. A name that resolves to NOTHING raises
    rather than being skipped, `from .x import gone` included: an
    unresolvable delegate is a narrower scan, and a narrower scan is the
    defect.
    """
    import importlib
    import inspect
    import textwrap

    module = importlib.import_module(fn.__module__)
    where = f"{module.__name__}.{fn.__name__}"
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    ns = _visible_names(tree, module)
    # a top-level module's `__package__` is "" — its own name is the package
    package = (module.__package__ or module.__name__).split(".")[0]

    def _keep(delegate, out):
        target = _package_target(delegate, package)
        if target is None:      # prints no lines of its own: not a delegate
            return
        # keyed by the delegate's OWN name, not the spelling at the call
        # site: `renderer = _escapes_section; renderer(doc)` is a call to
        # `_escapes_section`, and `skip=` names delegates, not aliases
        name = target.__name__
        if name in (fn.__name__, *skip) or name in out:
            return
        # No try: this package's own code has source on disk, and swallowing
        # an OSError here would be the silent skip the whole scan refuses
        out[name] = inspect.getsource(target)

    out = {}
    for node, chain in _calls(tree.body[0]):
        func = node.func
        if isinstance(func, ast.Name):
            # a name the body binds is followed or refused, never skipped as
            # "already being read": `renderer = _escapes_section` leaves the
            # delegate's source at module level
            for delegate in _resolve(func.id, chain, ns, where):
                if delegate is not LOCAL:
                    _keep(delegate, out)
        elif isinstance(func, ast.Attribute) and isinstance(func.value,
                                                            ast.Name):
            holder = func.value.id
            for owner in _holder(holder, chain, ns, where):
                if owner is not LOCAL:    # `lines.append`, `doc.get` are LOCAL
                    _keep(_attribute(owner, holder, func.attr, where), out)
        # anything else (`", ".join(...)`, a call on a call) prints through
        # a value, not through a name a section could hide behind
    assert out, f"{where} delegates to nothing — the scan is blind"
    return out


def _holder(holder: str, chain: tuple, ns: dict, where: str) -> list:
    """The objects `holder.attr()` can be called on, or LOCAL for a local
    VALUE — a literal, an operator expression, a parameter's or a with-
    target's derivative, whose methods print nothing of their own: a skip,
    even when it shadows a module global (cross-examiner finding 4). Any
    other binding is `_follow`ed or refused exactly as a called name is:
    `holder = _HOLDERS[k]; holder.section(doc)` hid the same section behind
    `name.attr()` that `name()` already refused (round 1)."""
    import sys

    binds = next((b[holder] for b in chain if holder in b), None)
    if binds is None:
        owner = ns.get(holder, MISSING)
        if owner is not MISSING:
            return [owner]
        assert holder in sys.modules, (
            f"{where} calls {holder}.…() and {holder!r} is bound nowhere "
            "this scan can see — widen the namespace, or a section printed "
            "through it goes unread")
        return [LOCAL]            # a loaded module the function never names
    values = (ast.Constant, ast.JoinedStr, ast.List, ast.Tuple, ast.Set,
              ast.Dict, ast.ListComp, ast.SetComp, ast.DictComp,
              ast.GeneratorExp, ast.BinOp, ast.BoolOp, ast.UnaryOp,
              ast.Compare, ast.AugAssign)
    return [obj for value in binds for obj in (
        [LOCAL] if value is LOCAL or isinstance(value, values)
        else _follow(holder, value, chain, ns, where, ()))]


def _stats_delegates():
    """`render_stats`'s delegates: the scan above, pointed at the renderer.

    `render_audit` is subtracted here rather than inside the scan, so the
    reach and the one documented exception stay separate readings.
    """
    from warden import memory as _mem
    return _delegates_of(_mem.render_stats, skip=_INDENTED_DELEGATES)


def _stats_render_source() -> str:
    """The source of everything `warden memory stats` prints through:
    `render_corpus_provenance` (the CLI prints it above the rest),
    `render_stats`, and every renderer it delegates to."""
    import inspect

    from warden import memory as _mem
    return "\n".join([inspect.getsource(_mem.render_corpus_provenance),
                       inspect.getsource(_mem.render_stats),
                       *_stats_delegates().values()])


def _scanned_section_headers() -> list[str]:
    """Section-header literals read off those renderers' source."""
    found = re.findall(r'"([A-Z][A-Z -]*[A-Z]) — ', _stats_render_source())
    return sorted(set(found))


def _rendered_stats() -> str:
    """`memory stats` rendered over a fixture that reaches every section."""
    from warden import memory as _mem
    prov = {"cache": ".warden/memory/findings.jsonl", "rows": 1, "shards": 1,
            "written_at": "2026-09-15T00:00:00+00:00", "problem": ""}
    lens_row = {"n": 1, "confirmed": 1, "fixed": 0, "refuted": 0,
                "dismissed": 0}
    doc = {
        "total_records": 1, "corpus_age": {},
        "restatements_folded": 0, "restatements_changed": 0,
        "review_events": {"total": 1, "clean": 0, "findings_open": 1,
                          "with_records": 1, "with_refutation": 1},
        "rules_error": "", "per_rule": {}, "promotable": [],
        "paused": [], "pause_candidates": [],
        "candidates": [{"slug": "s", "n": 3, "upheld": 3, "refuted": 0,
                        "dismissed": 0, "rate": 1.0, "wilson_lb": 0.4,
                        "verdict": "propose", "streak": 0}],
        "skill_recurrence": [{"file": "f.md", "n": 3, "upheld": 3,
                              "refuted": 0, "dismissed": 0, "rate": 1.0,
                              "verdict": "propose", "streak": 0}],
        "quiet_steps": [{"verdict": "review", "skill": "retro", "step": "s",
                         "evidence": "tag:x", "judged_total": 1}],
        "per_lens": {"code-reviewer": lens_row}, "per_round": {},
        "lens_unrecorded": 0, "round_unrecorded": 0,
        "dispatches": {"code-reviewer": {
            "dispatches": 1, "returned": 1, "reviewed-clean": 1,
            "reviewed-findings": 0, "no-review": 0, "unknown": 0}},
        "seats": {"declared": True, "error": "", "undeclared": {},
                  "rows": [{"seat": "code-reviewer", "wilson_lb": 0.2,
                            "unique": 1, "unique_share": 1.0, **lens_row}]},
        "per_tag": {}, "tags": None,
    }
    return _mem.render_corpus_provenance(prov) + _mem.render_stats(doc)


def _emitted_section_headers() -> list[str]:
    """The header of every section that fixture actually PRINTS, top level.

    The SAME shape the scan reads — an all-caps label then ` — ` — so the
    two sets are comparable at all. That shape is what makes a section
    header: the corpus-size line above them is lowercase and the standing
    `CAVEAT:` breaks on its colon, and neither is a section.
    """
    return sorted({m.group(1) for m in
                   (re.match(r"([A-Z][A-Z -]*[A-Z]) — ", line)
                    for line in _rendered_stats().splitlines()
                    if not line.startswith(" ")) if m})


def _section_disagreement(scanned: list[str], emitted: list[str]) -> str:
    """Why the two readings differ, or "" when they agree.

    Extracted so a FIRES case can drive the real comparison — inlined in the
    assertion it was a comparison no test could ever see come back non-empty,
    which is the shape `.warden/rules/tests-bite.md` names.
    """
    if scanned == emitted:
        return ""
    return (f"scanned off the renderer source: {scanned}\n"
            f"actually printed by the fixture: {emitted}\n"
            "the two must agree — a header in the first and not the second "
            "is a section this fixture does not reach (so the enumeration "
            "check is not covering it), and one in the second and not the "
            "first is a header the scan cannot see (so a retro is never told "
            "to read it)")


def _stats_section_headers() -> list[str]:
    """The section headers, with the two readings held equal.

    BOTH DIRECTIONS, because each one alone is blind where the other sees.
    Scanned-only misses a header the fixture prints and the scan's regex
    does not match. Emitted-only misses a header whose section this fixture
    never reaches — which is the state a NEW section starts in.
    """
    scanned, emitted = _scanned_section_headers(), _emitted_section_headers()
    problem = _section_disagreement(scanned, emitted)
    assert not problem, problem
    return scanned


def _gather_stats_bullet() -> str:
    """The `memory stats` bullet of step 1, flattened — not the whole file,
    for the reason `_reconcile_bullet` gives."""
    gather = _retro_section("## 1 · Gather")
    start = gather.index("- `.warden/bin/warden memory ingest`")
    rest = gather[start:]
    end = rest.index("\n- ", 1)
    return _flat(rest[:end])


def _unnamed_sections(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [h for h in _stats_section_headers() if h not in flat]


def test_the_retro_names_every_section_memory_stats_prints(capsys):
    """Gather must name each section by the header the renderer prints, so a
    retro cannot run to completion having never looked at one.

    Derived, not typed: a tenth section added to `render_stats` reddens this
    the day it ships rather than the day someone notices the skill is a
    section short.
    """
    headers = _stats_section_headers()
    assert len(headers) >= 9, headers
    for expected in ("CORPUS", "REVIEW EVENTS", "DECLARED RULES",
                     "CANDIDATE RULES", "SKILL RECURRENCE",
                     "SKILL-STEP PROVENANCE", "LENSES", "DISPATCHES",
                     "REVIEWER SEATS", "TAGS"):
        assert expected in headers, (
            f"{expected!r} is not among the headers this proof extracts, so "
            "the enumeration check below passes without covering it")

    missing = _unnamed_sections(_gather_stats_bullet())
    assert not missing, (
        "`warden memory stats` prints " + ", ".join(missing)
        + " and the retro's Gather step does not name them — LENSES and "
          "DISPATCHES went unread for their whole existence exactly this way")


# ── "any spelling" has to mean any spelling ──────────────────────────────
#
# The section a tenth renderer prints is only read if the scan RESOLVES that
# renderer. A scan keyed on the substring `render` resolves the ones spelled
# that way and no others, and the scanned/emitted equality below only catches
# the miss for a section the hand-built fixture reaches — so a section that is
# both named another way AND behind a doc key the fixture omits went unread by
# both. These two stand in for that tenth renderer.

def _escapes_section(doc):
    """A section renderer named nothing like `render`, conditional on a key
    the `_rendered_stats` fixture does not carry — the shape that was
    invisible to both readings."""
    return ["ESCAPES — findings the gate missed, mined by the retro.",
            f"  {len(doc['escapes'])} mined"]


def _stats_with_a_tenth_section(doc):
    lines = ["memory stats — 0 finding(s)"]
    if doc.get("escapes"):
        lines += _escapes_section(doc)
    return "\n".join(lines) + "\n"


def test_the_scan_resolves_a_delegate_named_nothing_like_render():
    """The scan's claim is ANY SPELLING, so a delegate is resolved by what it
    resolves to and not by its name."""
    spelled = {name for _, name in re.findall(
        r"(?:(\w+)\.)?(\w*[Rr]ender\w*)\(",
        inspect.getsource(_stats_with_a_tenth_section))}
    assert "_escapes_section" not in spelled, (
        "the substring reading sees this delegate after all, so it no longer "
        "stands in for the one that went unread")

    delegates = _delegates_of(_stats_with_a_tenth_section)
    assert "_escapes_section" in delegates, sorted(delegates)
    # and its header reaches the header scan, which is what a retro is told
    # to read — the whole point of resolving the delegate at all
    assert "ESCAPES" in re.findall(r'"([A-Z][A-Z -]*[A-Z]) — ',
                                   "\n".join(delegates.values()))
    # the calls that print nothing of their own are still skipped for what
    # they are, not for how they are spelled
    assert "get" not in delegates and "join" not in delegates, \
        sorted(delegates)


def test_a_delegate_that_resolves_to_nothing_raises():
    """An unresolvable call is a narrower scan, and a narrower scan is the
    defect — so it fails loudly rather than being skipped."""
    def _calls_a_ghost(doc):
        return _no_such_renderer(doc)      # noqa: F821 — that is the point

    with pytest.raises(AssertionError, match="resolves neither in"):
        _delegates_of(_calls_a_ghost)


# Round 1 of this PR's review mutated three more shapes into `render_stats`
# and watched the suite stay green on two of them: the delegate's SPELLING
# was no longer the hole, its SHAPE was. These stand in for those.

@_ft.lru_cache(maxsize=None)
def _decorated_section(doc):
    return ["ESCAPES — findings the gate missed, mined by the retro."]


class _SectionHolder:
    def section(self, doc):
        return ["ARRIVALS — what reached the corpus since the last retro."]

    def other(self, doc):
        return ["DEPARTURES — what the corpus dropped since the last retro."]


_HOLDER = _SectionHolder()


def _stats_through_wrapped_delegates(doc):
    lines = ["memory stats — 0 finding(s)"]
    if doc.get("escapes"):
        lines += _decorated_section(doc)
    if doc.get("arrivals"):
        lines += _HOLDER.section(doc)
    return "\n".join(lines) + "\n"


def test_r1_a_decorated_or_held_delegate_is_resolved_too():
    """A section renderer is resolved by what it RESOLVES TO, not by its
    shape either: `@lru_cache` and a method on a module-level object both
    print sections, and both were dropped with no assertion while `out`
    stayed non-empty from the other delegates (round 1 of this PR)."""
    delegates = _delegates_of(_stats_through_wrapped_delegates)
    assert "_decorated_section" in delegates, sorted(delegates)
    assert "section" in delegates, sorted(delegates)
    headers = re.findall(r'"([A-Z][A-Z -]*[A-Z]) — ',
                         "\n".join(delegates.values()))
    assert {"ESCAPES", "ARRIVALS"} <= set(headers), headers
    # the calls that print nothing of their own are still skipped
    assert "get" not in delegates and "join" not in delegates, \
        sorted(delegates)


def test_r1_a_parameter_is_not_reported_as_resolving_nowhere():
    """Every parameter KIND is a locally bound name. Reading `args.args`
    alone made a positional-only, keyword-only or star parameter look like a
    name resolving nowhere, and the refusal named the wrong cause."""
    def _kwonly(doc, *, renderer=_escapes_section, **rest):
        return renderer(doc)

    def _posonly(doc, renderer=_escapes_section, /):
        return renderer(doc)

    def _star(doc, *renderers):
        return renderers[0](doc)

    # a delegate reached only through a PARAMETER is genuinely unreadable
    # here — the scan says so, in the blind-scan words, and no longer claims
    # the parameter resolves nowhere
    for fn in (_kwonly, _posonly, _star):
        with pytest.raises(AssertionError) as raised:
            _delegates_of(fn)
        said = str(raised.value)
        assert "delegates to nothing" in said, said
        assert "resolves neither in" not in said, (
            "the refusal names a parameter as a name resolving nowhere, "
            "which is the wrong cause: " + said)


def test_r1_an_import_of_a_name_that_does_not_exist_raises():
    """`from .x import gone` binds the name to None, and reading that as
    "not a delegate" is silence on exactly the call the scan cannot see."""
    def _imports_a_ghost(doc):
        from warden.memory import no_such_renderer  # noqa: F401
        return no_such_renderer(doc)                # noqa: F821

    with pytest.raises(AssertionError,
                       match="does not define it"):
        _delegates_of(_imports_a_ghost)


# ── an earlier `if func.id in bound: continue` skipped every call through
# a name the body binds, "its own source is inside fn, already being read" —
# false for `renderer = _escapes_section; renderer(doc)`, never read at all.

def _stats_through_local_aliases(doc):
    """Every alias shape, each the ONLY path to its own delegate."""
    lines = ["memory stats — 0 finding(s)"]
    renderer = _escapes_section                # the idiom the comment denied
    first = _decorated_section
    second = first                             # chained: one hop was not enough
    tenth, wrapped = _stats_with_a_tenth_section, _stats_through_wrapped_delegates
    holder, other = _HOLDER, _HOLDER.other     # a holder, and an attribute
    render = lambda d: _flat(d)                # noqa: E731 — the shape under test
    age = doc.get("corpus_age") or {}          # local VALUES: methods are skips
    if doc.get("escapes") and (walrus := _section_disagreement):
        lines += [*renderer(doc), *second(doc), tenth(doc), wrapped(doc),
                  walrus([], []), render("x"), *other(doc), age.get("x")]
    for part in (_gather_stats_bullet, _reconcile_bullet):   # each in turn
        lines.append(part())
    lines += [ln for sec in [_retro_section] for ln in sec("## 1 · Gather")]
    twice = _unnamed_sections                  # bound twice: BOTH are read
    if doc.get("arrivals"):
        twice = _flat
        lines += holder.section(doc)
    return "\n".join(lines) + twice("")


def test_a_delegate_reached_through_a_local_alias_is_resolved():
    """Every alias shape above resolves to the renderer under its OWN name,
    so `skip=` and the header scan see the delegate."""
    delegates = _delegates_of(_stats_through_local_aliases)
    assert {"_escapes_section", "_decorated_section", "section", "_flat",
            "_stats_with_a_tenth_section", "_stats_through_wrapped_delegates",
            "_section_disagreement", "other", "_gather_stats_bullet",
            "_reconcile_bullet", "_retro_section", "_unnamed_sections",
            } <= set(delegates), sorted(delegates)
    assert not {"renderer", "first", "second", "tenth", "wrapped", "holder",
                "render", "walrus", "part", "sec", "twice", "age"
                } & set(delegates), sorted(delegates)
    headers = set(re.findall(r'"([A-Z][A-Z -]*[A-Z]) — ',
                             "\n".join(delegates.values())))
    assert {"ESCAPES", "ARRIVALS", "DEPARTURES"} <= headers, headers
    assert not {"get", "join", "append"} & set(delegates), sorted(delegates)


def _rebinds_the_renderers_name(doc):
    lines = ["memory stats — 0 finding(s)"]
    _escapes_section = _decorated_section   # noqa: F811 — the shape under test
    if doc.get("escapes"):
        lines += _escapes_section(doc)
    return "\n".join(lines) + "\n"


def test_a_re_bound_renderer_name_resolves_to_its_local_binding():
    """The headline: a renderer whose name the body ALSO binds — the call
    reaches the local binding, never the shadowed global, never nothing."""
    delegates = _delegates_of(_rebinds_the_renderers_name)
    assert "_decorated_section" in delegates, sorted(delegates)
    assert "_escapes_section" not in delegates, sorted(delegates)


def _scopes_as_python_has_them(doc):
    lines = ["memory stats — 0 finding(s)"]
    lines += [d for _escapes_section in doc["rs"] for d in _escapes_section(doc)]
    lines += _escapes_section(doc)         # the module's: a comprehension is
                                           # a scope, its target never leaks
    def _inner(d, _decorated_section, extra=_decorated_section(doc)):  # noqa: E306
        _escapes_section = _flat           # noqa: F811 — inner only
        return [*extra, *_decorated_section(d), *_escapes_section(d)]

    class _C:
        _reconcile_bullet = _unnamed_sections   # class scope: unseen below

        def m(self, d):
            return _reconcile_bullet()          # the module's

    return lines + _inner(doc) + _C().m(doc)


def test_scopes_are_read_as_python_scopes_them():
    """Per scope, with Python's edges: an inner def's alias does not reach the
    outer call (the dropped fix's finding 2); a comprehension target never
    leaks; a default is evaluated OUTSIDE its def; a class body is invisible
    from its methods (round 1, finding 4). The class-scope alias is NOT read."""
    delegates = _delegates_of(_scopes_as_python_has_them)
    assert {"_escapes_section", "_decorated_section", "_flat",
            "_reconcile_bullet"} <= set(delegates), sorted(delegates)
    assert "_unnamed_sections" not in delegates, sorted(delegates)


def test_a_parameter_or_target_sharing_a_delegates_name_stays_local():
    """A parameter or target spelled like a renderer is NOT that renderer: a
    phantom, or a wrong-cause refusal on its attributes (findings 1 and 4)."""
    def _targets_shadow(doc, _escapes_section):
        lines = [*_escapes_section(doc), *_decorated_section(doc)]
        for _escapes_section in doc["renderers"]:   # noqa: F402
            lines += _escapes_section(doc)
        with open(doc["path"]) as _HOLDER:           # noqa: F811
            lines += _HOLDER.readlines()
        try:                                         # bound as a str field,
            lines += doc["x"]                        # not a Name (round 1)
        except KeyError as _flat:                    # noqa: F841
            _flat.add_note("no x")
        match doc["r"]:
            case _escapes_section:                   # a capture pattern
                lines += _escapes_section(doc)
        return lines

    delegates = _delegates_of(_targets_shadow)  # no phantom, no wrong cause
    assert set(delegates) == {"_decorated_section"}, sorted(delegates)


def test_a_local_the_scan_cannot_follow_is_refused_not_skipped():
    """What the scan cannot resolve it refuses, naming the binding: a call, an
    unpaired unpack, `nonlocal`, a cycle — and a HOLDER built by an index,
    which round 1 found skipped while the same expression called by name was
    refused. Each hid a delegate with the suite green."""
    def _bound_to_a_call(doc):
        renderer = _ft.partial(_escapes_section, doc)
        return renderer()

    def _bound_by_an_unpaired_unpack(doc):
        renderer, *rest = doc["renderers"]
        return renderer(doc)

    def _rebound_by_nonlocal(doc):
        renderer = _escapes_section

        def swap():
            nonlocal renderer
            renderer = _decorated_section
        swap()
        return renderer(doc)

    def _bound_in_a_cycle(doc):
        first = second               # noqa: F821 — UnboundLocalError live
        second = first
        return second(doc)

    def _holder_indexed_from_a_table(doc):
        holder = doc["holders"][0]
        return holder.section(doc)

    for fn, said in ((_bound_to_a_call, "cannot follow"),
                     (_bound_by_an_unpaired_unpack, "cannot follow"),
                     (_rebound_by_nonlocal, "nonlocal renderer"),
                     (_bound_in_a_cycle, "cycle"),
                     (_holder_indexed_from_a_table, "cannot follow")):
        with pytest.raises(AssertionError, match=said):
            _delegates_of(fn)


def test_a_store_the_walk_does_not_enumerate_is_refused():
    """The fail-closed net: a Store-context Name reached by a statement the
    walk does not enumerate is refused, never left unbound (round 1)."""
    fn = ast.parse("def f():\n    x = 1\n").body[0]
    fn.body[0] = ast.Expr(ast.Name("x", ast.Store()))   # no statement binds it
    with pytest.raises(AssertionError, match="does not read"):
        _bindings(fn)


def test_the_section_scan_and_the_render_are_held_equal():
    """The equality `_stats_section_headers` asserts, exercised head-on and
    shown to be able to FAIL in BOTH directions."""
    scanned, emitted = _scanned_section_headers(), _emitted_section_headers()
    assert not _section_disagreement(scanned, emitted)
    assert len(emitted) >= 9, emitted

    # every renderer render_stats reaches is resolved, including the one
    # spelled through another module — the spelling the old `_render_*`-only
    # scan went blind to
    delegates = _stats_delegates()
    assert "render_quiet_steps" in delegates, sorted(delegates)
    assert sum(1 for n in delegates if n.startswith("_render_")) >= 4, \
        sorted(delegates)

    # FIRES both ways, over the real comparison
    assert _section_disagreement(scanned + ["NEW SECTION"], emitted), (
        "a header the scan sees and the render never prints is not caught")
    assert _section_disagreement(scanned, sorted(emitted + ["NEW SECTION"])), (
        "a header the render prints and the scan cannot see is not caught")
    assert not _section_disagreement(scanned, emitted)


def test_the_section_enumeration_proof_fires_and_stays_silent():
    """The catcher's catcher: the comparison must be able to come back
    non-empty, and the bullet slice must really be a slice."""
    headers = _stats_section_headers()
    assert sorted(_unnamed_sections("nothing about stats here")) == \
        sorted(headers)
    all_but_one = " ".join(h for h in headers if h != "DISPATCHES")
    assert _unnamed_sections(all_but_one) == ["DISPATCHES"], (
        "the comparison cannot see a single missing section, which is "
        "exactly how LENSES and DISPATCHES went unnamed for their whole "
        "existence")
    assert not _unnamed_sections(" ".join(headers))

    bullet = _gather_stats_bullet()
    assert bullet.startswith("- `.warden/bin/warden memory ingest`")
    assert len(bullet) < len(_flat(RETRO.read_text())) / 4, (
        "the bullet slice is most of the file, so scoping bought nothing")


def test_the_retro_names_every_audit_line_stats_can_print():
    """The retro must be able to see the ceiling it is the only consumer of.

    Both halves matter. A longer list is not enough — a label newer than a
    consumer's pinned warden would send that consumer hunting for output
    their `memory stats` never prints.
    So the enumeration also has to name the AUTHORITY, and it must not tell a
    reader what to CONCLUDE from silence, because silence has two causes.

    KEPT, not retired, under the 2026-09-14 prose-pin ruling as amended
    (`.warden/memory/decide/20260914T1803530000-25053766-dcea0a27.json`),
    and the reason is recorded here because this guard is where it was
    asked. The ruling retires tests that read a skill file and assert on
    WORDING; it keeps contract pins on CLI output formats. This one derives
    every expected label from `tags.render_audit` and `tags._render_ceiling`
    and only asks that the skill NAME each one — it pins what warden prints,
    not how the retro phrases it, so it moves with the renderer instead of
    against the author. That is the same line the section-header guard above
    draws: name what the output HAS, never restate what a line SAYS.
    """
    # EVERY LINE-EMITTING STATEMENT IN BOTH RENDERERS MUST HAVE BEEN REACHED,
    # counted off the source rather than typed here. Exact, not a floor with
    # slack: a floor absorbs a new line that no fixture reaches, which is the
    # whole failure mode — the renderer grows a line, the fixtures do not, and
    # the enumeration check goes on passing over a label it never saw.
    #
    # ALL THREE SPELLINGS, not just `lines.append`. The source this counts
    # already ends with `lines += _render_ceiling(...)`, so `lines += [...]`
    # is established idiom in these functions and a report line added that
    # way would slip past a count that knew one spelling. The delegation
    # itself is subtracted BY NAME, because it emits no line of its own;
    # every other `+=` or `extend` is a line.
    #
    # RESIDUAL, and it fails toward RED: two statements sharing one head label
    # would make this count high and redden with nothing wrong. The fix then
    # is to give the new line its own label, which is what the retro needs
    # anyway — a red test asking for a distinguishable label is the safe way
    # to be wrong here.
    #
    # SECOND RESIDUAL, measured: replacing the whole expression with
    # `src.count("lines.append")` is a GREEN mutation TODAY, because the one
    # `+=` in these functions is the delegation that is subtracted back out —
    # the two counts are numerically equal until a `+=` or `extend` report
    # line exists. It cannot be reddened on a clean tree; what pins the
    # spelling is that adding a line in EITHER spelling reddens this, and
    # both are exercised as mutations rather than left to the count's shape.
    src = inspect.getsource(_tags.render_audit) + inspect.getsource(
        _tags._render_ceiling)
    emits = (src.count("lines.append") + src.count("lines.extend")
             + src.count("lines +=") - src.count("lines += _render_ceiling("))
    assert len(_audit_labels()) == emits, (
        f"the renderers have {emits} line-emitting statements and this "
        f"proof extracted {len(_audit_labels())} labels {_audit_labels()} — "
        "it is driving fewer branches than the report has, so a label could "
        "go unnamed")

    # READ OVER THE BULLET, not the whole file. A `RETRO.read_text()` read
    # would let a label deleted from the enumeration and parked anywhere else
    # in the skill — an HTML comment, a worked example — satisfy it. No
    # whole-file read sits beside this one: the bullet check is strictly
    # stronger, and a second weaker assertion over the same fact is how the
    # weaker one becomes the one people edit.
    missing = _unnamed_audit_labels(_reconcile_bullet())
    assert not missing, (
        f"`memory stats` prints {missing} in its tag audit section and the "
        "retro's enumeration does not name them, so a retro can run to "
        "completion having never looked at them")

    bullet = _reconcile_bullet()
    for phrase, why in (
        ("`.warden/bin/warden memory stats` is the authority",
         "the enumeration must name the authority for which lines exist "
         "rather than stand as the list itself, or it rots again on the "
         "next line added"),
        ("pinned before the ceiling shipped",
         "the enumeration must say the lines it names depend on the warden "
         "the consumer pinned — the #168 HIGH is a skill outrunning a pin"),
        ("has two causes",
         "a MISSING ceiling READING does not mean an old warden: a current "
         "warden prints no reading when no ceiling is declared, while "
         "UNDECLARED AT THE DECLARATION BAR, UNDECIDED and CEILING "
         "UNREADABLE render either way — so a skill that rules the absence a "
         "pin defect orders a false finding on the commonest consumer state, "
         "and one that rules it a clean ceiling loses the signal"),
        ("never as a ceiling with nothing to say",
         "whatever the cause, the absence must not be read as a clean "
         "ceiling — that is the direction that loses the signal"),
    ):
        assert phrase in bullet, (
            f"retro/SKILL.md's tag-reconcile bullet: {why} "
            f"(missing {phrase!r})")


def test_the_retro_names_every_unreadable_declaration_label():
    """The four complaint labels, derived from `tags._LABELS` rather than typed.

    The test above can only ever see ONE of them, and that is structural rather
    than fixable there: `_render_ceiling` emits every complaint from a single
    statement, so `_audit_labels` extracts one head label per fixture however
    many labels the code can print, and the emits-count assertion forbids more.
    So three of the four could be deleted from `retro/SKILL.md` with that test
    green.

    This is the missing half, and it reads the labeller's own table: the retro's
    enumeration must name the head of every label `tags.label_complaint` can
    attach, so a fifth complaint source added tomorrow reddens this rather than
    reaching a consumer as a label their retro was never told to look for.

    KEPT for the reason recorded on the guard above: its
    expectation comes from `tags._LABELS`, warden's own table, so it is a
    contract pin the amended prose-pin ruling keeps rather than a wording pin
    it retires.
    """
    bullet = _reconcile_bullet()
    heads = sorted({label.split(" (")[0] for label in _tags._LABELS.values()})
    assert len(heads) >= 4, heads
    missing = [h for h in heads if h not in bullet]
    assert not missing, (
        "retro/SKILL.md's tag-reconcile enumeration does not name "
        + ", ".join(missing)
        + " — `tags.label_complaint` can print each of them, and a label the "
          "retro is not told about is a line its only human consumer will not "
          "read")


def test_the_audit_label_proof_fires_and_stays_silent():
    """The catcher's catcher: the label reader must read labels, and the
    comparison above must be able to come back non-empty."""
    labels = _enumerated_audit_labels()
    for expected in ("UNKNOWN", "NEAR-DUPLICATES", "declared but unused",
                     "INVALID ALIASES", "no vocabulary declared",
                     "TAG CEILING UNREADABLE", "UNDECIDED",
                     "CEILING BREACHED",
                     "UNDECLARED AT THE DECLARATION BAR"):
        assert expected in labels, (
            f"{expected!r} is not among the labels this proof extracts, so "
            "the enumeration check above passes without covering it")
    # …and the two the retro is NOT obliged to enumerate are extracted and
    # then excluded deliberately, rather than never seen at all.
    for descriptive in ("declared", "drift ceiling"):
        assert descriptive in _audit_labels()
        assert descriptive not in labels

    # FIRES: prose that names none of them, and prose that names all but one.
    assert sorted(_unnamed_audit_labels("nothing about tags here")) == \
        sorted(labels)
    all_but_one = " ".join(lab for lab in labels if lab != "UNDECIDED")
    assert _unnamed_audit_labels(all_but_one) == ["UNDECIDED"], (
        "the comparison cannot see a single missing label, which is exactly "
        "how one line gets added to the report and never to the skill")
    # SILENT: prose that names every one.
    assert not _unnamed_audit_labels(" ".join(labels))

    # …and the SCOPING itself, driven over the real helper.
    #
    # STATED RESIDUAL: swapping `_reconcile_bullet()` back to
    # `RETRO.read_text()` in the check above is a GREEN mutation on a clean
    # tree, and no test can make it red, because on a clean tree the labels
    # appear only inside the bullet and the two reads agree. It takes a label
    # deleted from the enumeration AND parked elsewhere for them to differ —
    # which is exactly the escape being guarded. So the distinction is
    # planted here directly, over the real helper, rather than left to a
    # mutation that cannot exist.
    bullet = _reconcile_bullet()
    assert bullet.startswith("- **Reconcile the tag vocabulary**")
    assert len(bullet) < len(_flat(RETRO.read_text())) / 4, (
        "the bullet slice is most of the file, so scoping bought nothing")
    gutted = bullet.replace("CEILING BREACHED", "")
    assert _unnamed_audit_labels(gutted) == ["CEILING BREACHED"]
    assert _unnamed_audit_labels(gutted + " <!-- CEILING BREACHED -->") == [], (
        "a label parked OUTSIDE the enumeration satisfies the check, so "
        "reading the whole file instead of the bullet would pass a retro "
        "that cannot see the line")

    # the three render fixtures must take three different paths: the
    # undeclared one exists to reach `_render_ceiling`'s `ceiling is None`
    # early return, and giving it a number instead renders headroom and never
    # takes the branch a future label would live in.
    rendered = _rendered_audit_lines()
    assert any("no vocabulary declared" in ln for ln in rendered)
    assert sum(1 for ln in rendered
               if ln.strip().startswith("drift ceiling")) == 1, (
        "the ceiling-is-None fixture is declaring a ceiling, so the early "
        "return is never taken and a label added inside it is invisible")


# ── the ship tail's flags, derived from the parser rather than remembered ──
#
# `deliver`, `pre-pr-review` and `orchestrate` INVOKE `warden ship` and
# `warden progress` instead of describing a hand-run tail,
# so the pack ships argv a builder copies verbatim. A flag named in a skill
# that argparse does not accept fails at the one moment the skill exists to
# make cheap — and reads, until then, exactly like a flag that works. Every
# expectation below is derived from `warden.cli.build_parser()`; nothing here
# restates a flag, which is the whole point.


def _warden_subparser(*path: str):
    """The `warden <path...>` parser, or an assertion naming what is missing.

    Walks real `_SubParsersAction` choices so a renamed or deleted subcommand
    fails here rather than being silently treated as having no options — an
    empty option set would make every flag in every skill "unknown", which is
    a confusing red, or (worse, if inverted) vacuously fine.
    """
    import argparse as _argparse
    from warden import cli as _cli
    parser = _cli.build_parser()
    for name in path:
        subs = [a for a in parser._actions
                if isinstance(a, _argparse._SubParsersAction)]
        assert subs, f"`warden {' '.join(path)}`: {name!r} has no subcommands"
        choices = subs[0].choices
        assert name in choices, (
            f"`warden {' '.join(path)}` does not exist: {name!r} is not among "
            f"{sorted(choices)} — a skill naming it ships argv that cannot run")
        parser = choices[name]
    return parser


def _options(*path: str) -> set[str]:
    opts = {o for a in _warden_subparser(*path)._actions
            for o in a.option_strings if o.startswith("--")}
    assert opts, f"`warden {' '.join(path)}` exposes no long options at all"
    return opts


# Flags in these sections that belong to another tool. Enumerated, never
# inferred: an unknown flag must be classified by a human once rather than
# waved through by a pattern that also waves through a typo.
_FOREIGN_FLAGS = {
    "--header-only",   # scripts/commit-lint.sh
    "--json", "--jq",  # gh
    "--auto",          # `gh pr merge --auto`, named in deliver only to forbid it
    "--oneline",       # git log, in orchestrate's hand-run fallback
}
# `--help` is deliberately NOT here: argparse gives every warden subparser one,
# so `_options` already carries it and an entry would be an alternative no test
# can make fail (tests/test_guard_mutations.py sweeps this set).

# Every warden command the wired sections invoke, longest spelling first so
# `warden progress show` is never read as a prefix of something else. The value
# is the subparser PATH, which is what makes each command's own option set
# reachable. Bare `warden progress` is deliberately absent: its only flag is
# `--help`, which every argparse parser carries, so an entry for it could not
# change a verdict, and an unfailable alternative is what the guard-mutation
# sweep exists to refuse.
#
# Per COMMAND, not pooled. A union of every subcommand's flags answers "does
# some warden command somewhere accept this", which is the question nobody is
# asking: the sections name four different commands, so a cross-command
# mix-up — `warden ship --head <sha>`, say, where `--head` belongs to
# `progress record` — is the most likely way one of these argv lines goes
# wrong, and a union passes it.
_WIRED_COMMANDS = (
    ("warden progress record", ("progress", "record")),
    ("warden progress show", ("progress", "show")),
    ("warden review", ("review",)),
    ("warden ship", ("ship",)),
)

# The wired sections: (skill, start marker, end marker, minimum flags).
#
# Each floor is per SECTION. One aggregate floor over all of them lets a slice
# stop covering its subject in silence, because the other slices carry the
# count on their own: the pre-pr-review paragraph could lose every flag it has
# and the aggregate would still clear.
_WIRED_SECTIONS = (
    ("deliver", "## 5 · Ship", "## 6 · Wrap", 8),
    ("orchestrate", "## 3 · Collect", "## 4 · Reconcile", 8),
    ("pre-pr-review", "**After the repair commit lands, record the boundary**",
     "**Every candidate the examiner refuted is filed too**", 3),
)

_FLAG_RE = re.compile(r"--[A-Za-z][A-Za-z0-9-]*")
_SUBST_RE = re.compile(r"\$\([^)]*\)")


def _section(skill: str, start: str, end: str) -> str:
    text = (ROOT / "skills" / "nightgate-skills" / "skills" / skill
            / "SKILL.md").read_text()
    assert start in text and end in text, (
        f"{skill}: the {start!r}..{end!r} slice moved, so this guard is "
        "reading something other than the wired section")
    body = text[text.index(start):text.index(end)]
    assert body.strip(), f"{skill}: the {start!r} slice is empty"
    return body


def _command_spans(body: str) -> list[tuple[tuple[str, ...], str]]:
    r"""(subparser path, the argv text) for every warden command in `body`.

    The argv text runs to the end of the LOGICAL line — a trailing `\` carries
    on to the next — because that is where a flag belonging to this command
    can appear, and reading further would blame one command for the next
    one's flags.
    """
    spans, claimed = [], []
    for literal, path in _WIRED_COMMANDS:
        for m in re.finditer(re.escape(literal), body):
            if any(s <= m.start() < e for s, e in claimed):
                continue          # already read as a longer command
            i = m.end()
            while True:
                nl = body.find("\n", i)
                if nl == -1:
                    i = len(body)
                    break
                if body[:nl].rstrip().endswith("\\"):
                    i = nl + 1
                    continue
                i = nl
                break
            claimed.append((m.start(), i))
            # `$( ... )` is ANOTHER tool's argv embedded in this one — the only
            # reason a foreign flag ever sits inside a warden command, and the
            # only place the foreign allowlist may apply. Allowing it across
            # the whole span would wave `warden ship --json title` through,
            # which argparse refuses outright.
            spans.append((path, _SUBST_RE.sub(" ", body[m.end():i])))
    return spans


def test_every_flag_the_wired_sections_name_exists_in_the_parser():
    """The wiring's own failure mode: a
    sentence in a shipped skill naming a flag the CLI does not have.

    A flag written ON a command is checked against THAT command's parser. A
    flag mentioned in prose, with no command attached, is checked against the
    commands that section actually invokes — never against every subcommand
    warden ships.
    """
    problems = []
    # Every wired section, by NAME: dropping one from `_WIRED_SECTIONS` would
    # otherwise leave this guard green while part of the wiring goes unread,
    # and a guard that silently stops inspecting a file is the defect one
    # level up.
    assert {s for s, _, _, _ in _WIRED_SECTIONS} == {
        "deliver", "orchestrate", "pre-pr-review"}, (
        "the wired sections are deliver's Ship, orchestrate's Collect and "
        "pre-pr-review's repair boundary; this guard reads "
        f"{sorted(s for s, _, _, _ in _WIRED_SECTIONS)}")
    for skill, start, end, floor in _WIRED_SECTIONS:
        body = _section(skill, start, end)
        flags = set(_FLAG_RE.findall(body))
        assert len(flags) >= floor, (
            f"{skill} {start}: {len(flags)} flag(s) in the slice, under its "
            f"floor of {floor} — the section boundaries moved and this guard "
            "is reading almost nothing")
        spans = _command_spans(body)
        assert spans, f"{skill} {start}: names no warden command at all"
        attached = set()
        for path, argv in spans:
            # No foreign allowlist here: a flag written ON a warden command,
            # outside any `$( )`, is that command's or it is a defect.
            allowed = _options(*path)
            for flag in _FLAG_RE.findall(argv):
                attached.add(flag)
                if flag not in allowed:
                    problems.append(
                        f"{skill} {start}: `warden {' '.join(path)}` is "
                        f"written with {flag!r}, which it does not accept")
        # prose mentions: no command attached, so the section's OWN commands
        # are the reference — not every subcommand warden ships.
        named = set()
        for path, _ in spans:
            named |= _options(*path)
        problems += [f"{skill} {start}: {flag!r} is named in prose but no "
                     "command this section invokes accepts it"
                     for flag in sorted(flags - attached
                                        - named - _FOREIGN_FLAGS)]
    assert not problems, "\n".join(problems)


def test_the_boundary_the_repair_loop_records_is_in_the_vocabulary():
    """`warden progress record` refuses a boundary outside its closed
    vocabulary, so a skill naming one exits 1 at the moment it is called."""
    from warden import progress as progress_mod
    ppr = (ROOT / "skills" / "nightgate-skills" / "skills" / "pre-pr-review"
           / "SKILL.md").read_text()
    named = set(re.findall(r"warden progress record ([a-z-]+)", ppr))
    assert named, "pre-pr-review records no boundary at all"
    assert named <= set(progress_mod.BOUNDARIES), (
        f"pre-pr-review names {sorted(named - set(progress_mod.BOUNDARIES))}, "
        f"which `record` refuses — the vocabulary is "
        f"{list(progress_mod.BOUNDARIES)}")
    # the two the commands record for the unit must NOT be re-recorded by hand
    for automatic in ("round-minted", "round-attested"):
        assert f"warden progress record {automatic}" not in ppr, (
            f"pre-pr-review records {automatic} by hand — `warden round new` "
            "and `warden attest write` already pass it, and a duplicate "
            "boundary makes the age column report a step twice")


# --- one spelling of warden, pack-wide --------------------------

SKILL_FILES = sorted((ROOT / "skills" / "nightgate-skills" / "skills").glob("*/SKILL.md"))
# A bare `warden <sub>` in command position: `.warden/bin/warden` is excluded
# by the `/` before its second word. One lookbehind and no character class, so
# the guard-mutation sweep has nothing to take apart — its reach is pinned by
# the guard that reads it, which reddens the moment a bare spelling is back.
_BARE_WARDEN = re.compile(r"(?<!/)warden\s+\w")
CONSUMER_WARDEN = ".warden/bin/warden"


def _fenced_lines(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every line INSIDE a ``` fence, with a trailing
    shell comment stripped — a comment that says `pinned warden predates` is
    prose, not an invocation."""
    out, fenced, shell = [], False, False
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            # a ```yaml provenance block or a ```json payload is data whose
            # strings are prose; only a shell fence carries invocations
            shell = fenced and line.strip()[3:].strip() in ("", "bash", "sh")
            continue
        if fenced and shell:
            out.append((n, line.split("#", 1)[0]))
    return out


def _inline_spans(text: str) -> list[tuple[int, str]]:
    """(line number the span opens on, span) for every `inline code span` in
    prose. Read a PARAGRAPH at a time, because markdown wraps a span with the
    prose around it: one line at a time, a span whose closing backtick is on
    the next line was never seen. A blank line or a fence ends the paragraph,
    so an unpaired backtick cannot pair with one further down the page."""
    out: list[tuple[int, str]] = []
    para: list[tuple[int, str]] = []

    def flush() -> None:
        if not para:
            return
        joined = "\n".join(line for _, line in para)
        starts, offset = [], 0
        for n, line in para:
            starts.append((offset, n))
            offset += len(line) + 1
        for m in re.finditer(r"`([^`]+)`", joined):
            line_no = max(n for off, n in starts if off <= m.start())
            out.append((line_no, m.group(1)))
        para.clear()

    fenced = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            flush()
            fenced = not fenced
            continue
        if fenced:
            continue
        if not line.strip():
            flush()
            continue
        para.append((n, line))
    flush()
    return out


def test_an_inline_span_that_wraps_a_line_break_is_still_scanned():
    """Markdown wraps prose, and a code span wraps with it: "mint a fresh
    round with `warden\nround new`" renders as one span. Read one line at a
    time, the opening backtick's line has no closing one and the span is
    never seen. A span crossing a line break inside a
    paragraph is read whole; a blank line still ends it, so an unpaired
    backtick cannot swallow the next paragraph."""
    text = ("Prose first.\n"
            "When it fires, mint a fresh round with `warden\n"
            "round new` and re-run every reviewer.\n"
            "\n"
            "An unpaired ` backtick here\n"
            "\n"
            "must not reach `this span`.\n"
            "```\n"
            "`warden inside a fence` is the fence scan's job\n"
            "```\n")
    spans = _inline_spans(text)
    wrapped = [(n, s) for n, s in spans if _BARE_WARDEN.match(" ".join(s.split()))]
    assert wrapped and wrapped[0][0] == 2, (
        f"a bare `warden` span wrapped across a line break was not read: {spans}")
    assert (7, "this span") in spans, (
        f"an unpaired backtick swallowed a span past a blank line: {spans}")
    assert not any("inside a fence" in s for _, s in spans), spans


def test_every_warden_invocation_in_the_pack_is_spelled_from_the_consumer_bin():
    """docs/wiki/Installation.md makes `.warden/bin/warden` the consumer
    contract (a consumer never installs warden globally), so a bare `warden`
    in a skill exits 127 on a checkout that has the command. Inside a probe
    the `>/dev/null 2>&1` swallows `command not found`, so the wrong spelling
    does not fail loudly — it silently selects a branch.

    One spelling, pack-wide, guarded here: every fenced invocation and every
    inline-code invocation in skills/**/SKILL.md names the consumer bin.
    Prose ("a pinned warden older than...") is the tool's name, not a
    command, and is not scanned.
    """
    offenders, spelled = [], 0
    for path in SKILL_FILES:
        text = path.read_text()
        for n, line in _fenced_lines(text):
            if _BARE_WARDEN.search(line):
                offenders.append(f"{path.parent.name}:{n} fence: {line.strip()[:70]}")
        for n, span in _inline_spans(text):
            if _BARE_WARDEN.match(span):
                offenders.append(f"{path.parent.name}:{n} span: `{span[:70]}`")
        spelled += text.count(CONSUMER_WARDEN)
    assert spelled >= 30, (
        f"only {spelled} `{CONSUMER_WARDEN}` invocation(s) across the pack — "
        "the scan below is checking a pack that no longer invokes warden, so "
        "its empty offender list proves nothing")
    assert not offenders, (
        "bare `warden` invocations — a consumer has no warden on PATH, and "
        f"inside a probe the failure is silent. Spell `{CONSUMER_WARDEN}`:\n  "
        + "\n  ".join(offenders))


# --- the probe fences are pinned structurally -----------------

# `if <cmd> --help >/dev/null 2>&1; then <A>; else <B>; fi`, on one line or
# several. Both arms are captured so the guard can read them, not merely find
# the left half of the `if`.
_PROBE_FENCE = re.compile(
    r"if\s+(?P<cmd>\S*warden(?:\s+\w+)+)\s+--help\s+>/dev/null\s+2>&1;\s*then"
    r"\s*(?P<then>.*?)\s*;?\s*\belse\b\s*(?P<else>.*?)\s*;?\s*\bfi\b",
    re.DOTALL)
# Which skills the structural guard reads, and WHICH command each fence
# probes, in order of appearance. Named rather than counted: a floor of 2 left
# a skill carrying three fences with one of them unpinned, so `graph crew`'s
# probe — the only thing between a consumer on a warden that predates the
# subcommand and an unrunnable instruction — could be deleted outright with
# the suite green. Naming them makes a deleted probe red AND a new probe red
# until it is listed, where a count can absorb either.
#
# Kept in lockstep with the TREE by the guard itself: a skill whose fences
# probe with `--help >/dev/null 2>&1` must be listed here, so a fourth probing
# skill — or a deleted row — is red, not silently unread.
PROBED_SKILLS = {
    "deliver": (".warden/bin/warden round classify", ".warden/bin/warden ship"),
    "orchestrate": (".warden/bin/warden progress", ".warden/bin/warden ship"),
    "pre-pr-review": (".warden/bin/warden graph crew",
                      ".warden/bin/warden round",
                      ".warden/bin/warden progress"),
}
_PROBE_MARK = "--help >/dev/null 2>&1"


def _probe_fences(skill: str) -> list[tuple[str, str, str]]:
    text = (ROOT / "skills" / "nightgate-skills" / "skills" / skill
            / "SKILL.md").read_text()
    fenced = "\n".join(line for _, line in _fenced_lines(text))
    return [(m.group("cmd").strip(), m.group("then").strip(),
             m.group("else").strip()) for m in _PROBE_FENCE.finditer(fenced)]


def test_every_probe_fence_branches_with_two_distinct_arms_of_the_right_polarity():
    """Substrings such as `; then BOARD=` pin only the LEFT half of the `if`:
    an inverted polarity (`then BOARD=no; else BOARD=yes`), a deleted else arm
    (`then BOARD=yes; fi`), or an if/else/fi fence reduced to a bare two-line
    probe would each pass.

    This reads the fence STRUCTURALLY in all three skills that probe: both
    arms present, distinct, and the right way round — the arm that USES the
    probed command (or sets `=yes`) is the `then` arm, and the fallback arm
    never invokes it.

    And it reads them BY NAME. A per-skill count was satisfied at two-of-three
    while `pre-pr-review`'s crew probe was deleted outright, so the fences a
    skill must carry are listed as the commands they probe, in order.
    """
    probing = {path.parent.name for path in SKILL_FILES
               if any(_PROBE_MARK in line
                      for _, line in _fenced_lines(path.read_text()))}
    assert probing == set(PROBED_SKILLS), (
        f"the skills whose fences probe a subcommand are {sorted(probing)}, "
        f"and this guard reads {sorted(PROBED_SKILLS)} — a probe this guard "
        "does not read is the half-pinned-probe hole one skill over")
    for skill, expected in PROBED_SKILLS.items():
        fences = _probe_fences(skill)
        assert tuple(cmd for cmd, _, _ in fences) == expected, (
            f"{skill} probes {[cmd for cmd, _, _ in fences]} in if/then/else/"
            f"fi fences, and this guard is declared to read {list(expected)} — "
            "a probe was un-branched, lost its else arm, was deleted, or was "
            "added without being listed: a bare probe line discards the "
            "diagnostic and leaves failure byte-identical to success")
        for cmd, then_arm, else_arm in fences:
            where = f"{skill}: probe of `{cmd}`"
            assert then_arm and else_arm, f"{where}: an arm is empty"
            assert then_arm != else_arm, f"{where}: both arms are the same"
            # the probed subcommand — the first word after the binary
            sub = cmd.split()[1]
            uses_then = sub in then_arm or "=yes" in then_arm
            uses_else = (re.search(rf"\b{re.escape(sub)}\b", else_arm)
                         and "warden" in else_arm) or "=yes" in else_arm
            assert uses_then, (
                f"{where}: the `then` arm neither runs the probed command nor "
                f"records it available — polarity inverted? then={then_arm!r}")
            assert not uses_else and "=no" not in then_arm, (
                f"{where}: the fallback arm relies on the command the probe "
                f"just found missing, or the arms are inverted: "
                f"then={then_arm!r} else={else_arm!r}")


def test_pre_pr_review_attributes_every_finding_to_a_lens_and_round():
    """The payload contract is what decides whether per-lens data ever
    accrues. A record names the lens that raised it only if the skill asks,
    so the skill writes `lens` and `round` on every finding, `round` on every
    roster entry, spells `role` from the schema's vocabulary, and probes the
    pinned warden before writing fields an older closed schema would
    refuse."""
    text = (ROOT / "skills" / "nightgate-skills" / "skills" / "pre-pr-review"
            / "SKILL.md").read_text()
    for needle in ('"lens": "code-reviewer"', '"round": 1', "crew:<slug>",
                   "grep -q 'lens'", "ATTRIBUTION=none", "round it was FIRST"):
        assert needle in text, f"pre-pr-review lost the attribution contract: {needle!r}"
    # The LIVE cell for every release-pinned consumer is a warden that
    # predates the structured roster, where the skill takes the `legacy`
    # branch. ATTRIBUTION set only inside `structured` would have the builder
    # write lens/round into a closed schema: "Additional properties are not
    # allowed ('lens', 'round' were unexpected)", exit 2, no attestation. So
    # the legacy branch sets it too.
    probe = text[text.index("ROSTER=legacy"):]
    assert "ATTRIBUTION=none" in probe[:probe.index("fi\n")], (
        "the legacy branch of the probe does not set ATTRIBUTION=none, so a "
        "pre-277 warden is handed lens/round it refuses (R1-14)")
    legacy = text[text.index("`legacy`: emit"):]
    assert "lens" in legacy[:500] and "round" in legacy[:500], (
        "the legacy paragraph covers the roster only; findings still carry "
        "the fields an older closed schema refuses (R1-14)")
    # the four platform roles the schema enumerates are the ones the skill names
    from warden import attest as attest_mod
    for role in attest_mod.LENS_ROLES:
        assert f"`{role}`" in text, f"the skill does not name lens {role!r}"


def test_pre_pr_review_re_dispatches_a_no_review_lens_once_and_records_it():
    """A reviewer that comes back with a refusal, a truncated run or a
    'nothing to review' written without reading the diff is not a clean
    dispatch. The protocol (a) has every reviewer NAME the files it
    read, which is the observable warden derives `outcome` from; (b)
    re-dispatches a `no-review` lens once and keeps the first entry in the
    roster; and (c) probes the pin for `outcome` before writing it, the
    shape the `lens` and `--review-dir` probes take."""
    text = (ROOT / "skills" / "nightgate-skills" / "skills" / "pre-pr-review"
            / "SKILL.md").read_text()
    flat = _flat(text)
    for phrase in ("NAME the changed files it read",
                   "is recorded `no-review`, not `reviewed-clean`",
                   "re-dispatch it ONCE",
                   "record the `no-review` either way",
                   "you may only ever LOWER it",
                   "grep -q 'outcome'", "OUTCOME=derived", "OUTCOME=none"):
        assert phrase in flat, f"pre-pr-review lost: {phrase!r}"
    # the else arm of the pin probe disables the field, never enables it
    probe = text[text.index("grep -q 'outcome'"):]
    then_arm, else_arm = probe.split("else", 1)
    assert "OUTCOME=derived" in then_arm and "OUTCOME=derived" not in else_arm[:200]
    assert "OUTCOME=none" in else_arm[:200], (
        "a pinned warden older than the field must be told NOT to write it")
    # the LEGACY branch is the live cell for every release-pinned consumer
    # whose warden predates the structured roster, and it predates `outcome`
    # too, so it must disable the field as well
    legacy = text[text.index("ROSTER=legacy"):]
    assert "OUTCOME=none" in legacy[:legacy.index("fi\n")], (
        "the legacy branch of the probe does not set OUTCOME=none, so a "
        "warden older than the field is handed an `outcome` it refuses")
    # the roster's outcome is warden's to stamp, so the example payload does
    # not teach a builder to assert it
    example = text[text.index('"reviewers": ['):text.index('"findings": [')]
    assert '"outcome"' not in example, (
        "the payload example writes `outcome` — the field is derived by "
        "warden, and a builder-typed reviewed-clean is the claim this closes")


# ── the crew comes from the graph, not from the protocol prose ─────────────
#
# graph.yaml declares who reviews each round and `warden graph crew --round N`
# prints it, so a roster written into a skill is a SECOND source that can
# disagree with the one `attest write --review-dir` enforces — which is what
# happened: the protocols said round 2 was the scoped re-review THEN the
# examiner, while the graph declares the re-review alone, and a builder
# following the prose would have its write refused.

_REVIEW_PROTOCOLS = {
    "pre-pr-review": ROOT / "skills" / "nightgate-skills" / "skills"
                          / "pre-pr-review" / "SKILL.md",
    "deliver": ROOT / "skills" / "nightgate-skills" / "skills" / "deliver"
                    / "SKILL.md",
    "skills-policy": ROOT / ".warden" / "skills-policy.md",
}


def _declared_review_roles() -> set[str]:
    """The reviewer roles THIS repo's graph declares — read from the graph,
    never retyped here, so the guard follows the declaration it is about."""
    from warden import graph as graph_mod

    review = graph_mod.resolve_review(graph_mod.load(ROOT), ROOT)
    roles = {role for roles in review["rounds"].values() for role in roles}
    assert roles, "the graph declares no review roles — this guard reads nothing"
    return roles


def _prose_only(text: str) -> str:
    """The file's PROSE: ```json blocks and inline code spans removed.

    A role name inside a payload example or a backticked list is the
    attestation's `role` SPELLING, which every protocol still has to write.
    A role name in prose is a ROSTER — a statement of who reviews — and that
    is the claim graph.yaml now owns. What this cannot see is a roster
    re-introduced inside code spans; it is a prose grep and says so.
    """
    kept, fenced, lang = [], False, ""
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced, lang = (not fenced), (line.strip()[3:].strip()
                                          if not fenced else "")
            continue          # the delimiter itself is never prose, and
            # leaving it in lets the span pairing below straddle a fence
        if fenced and lang == "json":
            continue          # a payload example is a value, not a roster
        kept.append(line)
    return re.sub(r"`[^`]+`", "", "\n".join(kept), flags=re.S)


def test_the_review_protocol_set_is_the_three_the_crew_rule_binds():
    """The vocabulary above, pinned. Both guards below iterate it, so a
    protocol quietly dropped from it would stop being read while they stayed
    green — the silent-skip shape one level up. The set is the two skills that
    dispatch a review and the policy contract they read it from."""
    assert set(_REVIEW_PROTOCOLS) == {"pre-pr-review", "deliver",
                                      "skills-policy"}
    for name, path in _REVIEW_PROTOCOLS.items():
        assert path.is_file(), f"{name}: {path} is not a file"


def test_the_review_protocols_dispatch_the_crew_the_graph_declares():
    """Each protocol reads the round's crew from the command rather than
    naming one: `warden graph crew --round N` is the single dispatch list."""
    for name, path in _REVIEW_PROTOCOLS.items():
        assert "graph crew --round" in path.read_text(), (
            f"{name} does not read its crew from `warden graph crew --round "
            "N`, so the roster it dispatches is prose again")


def test_no_shipped_protocol_states_which_roles_review_a_round():
    """No protocol prose names a declared reviewer role.

    The mechanical line is: prose that names a role IS a roster, because the
    only reason to name one in a dispatch protocol is to say who runs. The
    spelling survives where it is a value — a payload example, a backticked
    vocabulary entry.

    What this does NOT check is a roster written inside code spans:
    `_prose_only` strips them, so a fully backticked dispatch sentence passes
    here. The helper's docstring discloses that hole and this one does not
    claim past it — the shipped fallback is kept out of the hole by
    `test_pre_pr_review_pins_its_no_crew_fallback_and_keeps_the_roster_in_a_payload`,
    which reads that paragraph's prose with the code spans left IN.
    """
    roles = _declared_review_roles()
    problems = []
    for name, path in _REVIEW_PROTOCOLS.items():
        for line in _prose_only(path.read_text()).splitlines():
            named = sorted(r for r in roles if r in line)
            if named:
                problems.append(f"{name}: {', '.join(named)} in {line.strip()[:80]!r}")
    assert not problems, (
        "protocol prose names the crew the graph declares — dispatch the "
        "roster `warden graph crew --round N` prints instead:\n  "
        + "\n  ".join(problems))


# The spellings a prose restatement of the no-crew lens source would need,
# lowercase. The guard below bans them from the fallback's prose so the source
# is stated once, in the value it reads. Measured, not guessed: a round rewrote
# the paragraph to say "the policy file's charter section, at the place that
# section says each is stated" while a narrower two-entry list ("review
# charter", "skills-policy") stayed green, so these are the words that
# restatement could not avoid. `review charter` is NOT here: `charter`
# subsumes it, and a subsumed member is an alternative no control can pin
# alone. The list is not exhaustive and the guard's docstring says so.
_LENS_SOURCE_SPELLINGS = ("charter", "policy file", "skills-policy")

# One restatement per spelling, each caught by exactly that one — written out
# rather than built from the tuple, because a control derived from the
# vocabulary it tests shrinks with it and pins nothing.
_LENS_SOURCE_RESTATEMENTS = (
    "each lens is stated in the charter section the policy names",
    "read the extra checklists off the policy file, where it says each is stated",
    "the checklists live in .warden/skills-policy.md, under the section it declares",
)


def _lens_sources_named(prose: str) -> list[str]:
    """The lens-source spellings `prose` carries — the (c) bound, one place."""
    low = prose.lower()
    return [spelling for spelling in _LENS_SOURCE_SPELLINGS if spelling in low]


def test_the_lens_source_spellings_each_catch_a_restatement_nothing_else_does():
    """The control for `_LENS_SOURCE_SPELLINGS`, and the reason it is a list.

    The guard below bans a prose restatement of the no-crew lens source by
    spelling. A spelling no restatement exercises is an alternative that could
    be deleted with the suite green, so each one has a restatement here that
    ONLY it catches — asserted in both directions, because a control caught by
    two spellings pins neither.
    """
    assert len(_LENS_SOURCE_RESTATEMENTS) == len(_LENS_SOURCE_SPELLINGS), (
        "a spelling has no restatement to pin it, or a restatement pins no "
        "spelling — the two move together")
    for restatement in _LENS_SOURCE_RESTATEMENTS:
        caught = _lens_sources_named(restatement)
        assert len(caught) == 1, (
            f"{restatement!r} is caught by {caught} — a control that fires on "
            "two spellings cannot show either one is load-bearing")
    assert {c for r in _LENS_SOURCE_RESTATEMENTS
            for c in _lens_sources_named(r)} == set(_LENS_SOURCE_SPELLINGS), (
        "the restatements do not cover every spelling one-to-one")


def _no_crew_fallback() -> str:
    """`pre-pr-review`'s no-crew fallback region, start to end.

    Both guards below read the same slice, and a drift between two copies of
    the bounds would let one of them read a region the other does not.
    """
    text = (ROOT / "skills" / "nightgate-skills" / "skills" / "pre-pr-review"
            / "SKILL.md").read_text()
    start = text.index("**With no crew to resolve**")
    return text[start:text.index("Then load **split memory**", start)]


def test_pre_pr_review_pins_its_no_crew_fallback_and_keeps_the_roster_in_a_payload():
    """The ONE documented default in the pack, pinned — and its role
    spellings kept where they are a value.

    Two things this paragraph carries that nothing else does. It is the only
    no-crew branch in the pack: `deliver` has no `graph crew` probe and no
    fallback of its own, it delegates the dispatch to this skill, so deleting
    this paragraph leaves a consumer with no graph.yaml following a command
    that exits 2 and nothing else — and it left the whole suite green.

    And it is where the roster came back. `_prose_only` strips code spans, so
    a backticked roster in a dispatch sentence is invisible to the prose
    guard above; this one reads the paragraph with the spans left IN and
    exempts only the payload example, which is where a builder who needs the
    spellings should find them.
    """
    fallback = _no_crew_fallback()
    for needle in ("one independent finder over the diff",
                   "one independent judge over its candidates",
                   "unverified-roster"):
        assert needle in fallback, (
            f"pre-pr-review's no-crew fallback lost {needle!r} — it is the "
            "only documented default the pack has, and `deliver` delegates "
            "its dispatch here rather than carrying one")
    prose = re.sub(r"```json.*?```", "", fallback, flags=re.S)
    named = sorted(r for r in _declared_review_roles() if r in prose)
    assert not named, (
        "the fallback's dispatch prose names " + ", ".join(named) + " — a "
        "roster in a code span is the hole the prose guard cannot see. Put "
        "the spellings in the ```json payload example, which is a value")
    assert '"role": "code-reviewer"' in fallback, (
        "the fallback names no roster at all now: a builder taking the "
        "default still has to spell the roles in the payload, and the "
        "example is where that spelling belongs")
    assert "in the attestation" not in fallback, (
        "the fallback tells a builder to record its roster SOURCE in the "
        "attestation — the schema is closed (additionalProperties: false) "
        "and carries no field for it, so the only place that sentence can go "
        "is a free-text `agent` string")


def test_pre_pr_review_no_crew_fallback_declares_a_lens_source_per_shape():
    """With no crew to resolve, the fallback has to answer TWO questions, and
    it used to answer only one.

    `warden graph crew` became the single source of both the roster and the
    lens checklists, and the line that used to send a reviewer to the policy's
    `## Review charter` for the extra checklists went with it. On the no-crew
    path the command resolves nothing, so a consumer whose charter states a
    lens as a checklist — one no rule file enforces — got that lens into no
    reviewer's prompt at all, silently, with the roster fallback right above
    it reading as complete. Invisible here: `examples/hello-svc`'s charter is
    `none`, so this repo's own portability run cannot see it.

    WHY THIS READS A VALUE AND NOT THE SENTENCE. The first guard here asserted
    that `## Review charter` and the word `lens` appeared somewhere in the
    slice, which bit on DELETION and not on INVERSION: a measured rewrite
    saying the lenses do NOT fall back, naming the same charter section as the
    place NOT to read, passed it. Two later attempts widened the needles and a
    round measured eight rewrites that still passed — prose negation has
    unboundedly many positions and no substring list closes it, and each new
    needle pinned more wording on a file the pack SHIPS, which the prose-pin
    ruling retires. So the source is stated as a VALUE the skill and this
    guard both read, and what is checked is the source's IDENTITY and
    COMPLETENESS rather than the sentence around it:

    (a) one entry per lens SHAPE, and the shapes come from
        `graph.schema.json`'s `review.lenses` — a third shape added there
        reddens this until the fallback grows a source for it;
    (b) the `checklist` entry names the policy file's charter section. Two
        claims, and they are established differently: that the crew command
        answers NOTHING on this path is PROVEN, by running the resolution the
        command runs against this repo's own graph with its `review` block
        removed; that the value names the charter is an ordinary assertion on
        a string. Nothing here proves the charter is the only source left —
        that is the shipped contract's claim, not this test's. And the value
        DEFERS to the charter entry for where the checklist sits rather than
        pinning a place: it must say "the place that entry says" and must not
        say `inline`. Measured mutations: the round-1 value "the policy
        file, inline under `## Review charter`", and the same with "directly
        under" for "inline", each still carry the section name and passed
        the presence check; each reddens here;
    (c) with the fences removed, the region's prose contains none of the
        spellings listed in `_LENS_SOURCE_SPELLINGS`, so the source is stated
        once and in the value this guard reads.

    THE RESIDUAL, stated rather than hidden, and it has two parts. (c) is a
    ban on a LIST of spellings, not a proof that no source is named: a second,
    complete statement of the source that avoids every listed word passes, and
    the list is widened when one is found rather than claimed exhaustive. And
    no deterministic text guard closes prose RETRACTION — a sentence saying
    "ignore the block below" leaves every parsed value intact.
    """
    from warden import graph as graph_mod

    fallback = _no_crew_fallback()
    declared = [json.loads(block) for block in
                re.findall(r"```json\n(.*?)```", fallback, flags=re.S)]
    sources = [block for block in declared if isinstance(block, dict)]
    assert len(sources) == 1, (
        f"the no-crew fallback carries {len(sources)} lens-source blocks, not "
        "one — it states where each lens SHAPE is read from as a ```json "
        "object, and this guard reads that object rather than the prose "
        "around it, which negates freely")
    stated = sources[0]

    schema = json.loads((ROOT / "warden" / "schemas"
                         / "graph.schema.json").read_text())
    lenses = schema["properties"]["review"]["properties"]["lenses"]["items"]
    shapes = {key for branch in lenses["oneOf"] for key in branch["required"]}
    assert shapes, "graph.schema.json declares no lens shapes — this reads nothing"
    assert set(stated) == shapes, (
        f"the fallback states a source for {sorted(stated)} and the schema "
        f"accepts the lens shapes {sorted(shapes)} — a shape with no entry is "
        "a lens the no-crew path reads from nowhere, which is the silent gap "
        "this fallback exists to close")

    doc = dict(graph_mod.load(ROOT))
    doc.pop("review", None)
    with pytest.raises(graph_mod.GraphError) as refusal:
        graph_mod.crew(doc, ROOT, 1)
    assert "no `review` block" in str(refusal.value), (
        "resolving a crew without a `review` block no longer refuses for that "
        "reason, so the premise of this whole fallback is unproven here")
    assert "## Review charter" in stated["checklist"], (
        f"the fallback reads a checklist lens from {stated['checklist']!r} — "
        "with the command refusing (above), the charter section of the policy "
        "file is the only place such a lens is stated, so any other value "
        "sends the reviewer somewhere that holds nothing")
    assert ("the place that entry says" in stated["checklist"]
            and "inline" not in stated["checklist"].lower()), (
        f"the fallback's checklist source {stated['checklist']!r} pins a "
        "statement place instead of deferring to where the charter entry "
        "says the checklist is, and a charter entry can point elsewhere")

    prose = re.sub(r"```json.*?```", "", fallback, flags=re.S)
    named = _lens_sources_named(prose)
    assert not named, (
        f"the fallback's prose names the lens source ({', '.join(named)}) as "
        "well as the block does. Two statements of one instruction is how "
        "the inversion got in: leave the source in the value, where this "
        "guard reads it, and let the prose point at it")
