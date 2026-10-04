"""warden certify — the enrollment maturity ladder.

The load-bearing property: a consuming repo may EXTEND the ladder but can
never remove or redefine a baseline check. A ladder you can shorten
certifies nothing.
"""

import ast
import inspect
import json
import re
import textwrap
from pathlib import Path

import pytest
import yaml

from warden import __version__
from warden import certify as certify_mod
from warden import memory as memory_mod
from private_evidence import needs_corpus

ROOT = Path(__file__).parent.parent


def write_shard(root: Path, name: str, records: list[dict]) -> Path:
    """Commit-shaped attest shard: certify reads these, never the cache."""
    attest_dir = root / ".warden" / "memory" / "attest"
    attest_dir.mkdir(parents=True, exist_ok=True)
    shard = attest_dir / f"{name}.json"
    shard.write_text(json.dumps({
        "schema": 1, "source": "attest", "sha": "a" * 40, "base_sha": "b" * 40,
        "rules_version": "test", "reviewed_at": "2026-08-24T00:00:00+00:00",
        "verdict": "clean", "reviewers": [], "records": records}))
    return shard


def shard_record(rule_id: str, status: str = "fixed", seq: int = 0,
                 round_n: int = 0) -> dict:
    """`round_n` selects the review ROUND: records sharing one carry one
    (ts, sha) the way a real shard's do, and a streak counts rounds, not
    records."""
    return {"ts": f"2026-08-24T00:{round_n:02d}:00+00:00", "seq": seq,
            "source": "attest",
            "sha": f"{round_n:040x}", "rule_id": rule_id, "tags": [], "file": "x.py",
            "dir_prefix": ".", "line": 1, "severity": "LOW",
            # One text per judgment: an identical finding in a later round
            # reads as that judgment RESTATED and folds to one
            "finding": f"f{round_n}.{seq}",
            "evidence": "e", "status": status, "origin": "day",
            "id": f"{rule_id}-{round_n}-{seq}"}


def make_repo(tmp_path: Path, level: int) -> Path:
    """Build a synthetic repo satisfying the ladder up to `level`."""
    (tmp_path / ".warden" / "rules").mkdir(parents=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    repo_yaml = ("version: 1\nrepo: sim\n"
                 "components:\n  app: {path: app/, lang: python, description: d}\n"
                 "review:\n  rules_dir: .warden/rules\n"
                 "  blocking_severities: [HIGH]\n")
    if level >= 1:
        # track the running version: a hardcoded pin rots every release
        repo_yaml += f"platform:\n  pin: v{__version__}\n"
        (tmp_path / ".warden" / "rules" / "r.md").write_text(
            "---\nid: r\nseverity: LOW\nengine: claude\n"
            "applies_to: ['**']\n---\nbody\n")
        ci = ("on: [push, pull_request]\n"
              "jobs:\n  gate:\n    steps:\n      - run: warden review --event e\n")
    else:
        ci = "jobs: {}\n"
    if level >= 2:
        repo_yaml += "risk_tiers:\n  - {glob: '**', tier: LOW}\nverify:\n  app:\n    - run: 'true'\n"
        ci += "      - run: warden verify --scope app\n"
    if level >= 4:
        ci += ("      - run: warden attest check --base origin/main\n"
               "      - run: warden memory ingest\n")
    if level >= 5:
        repo_yaml = repo_yaml.replace(
            "risk_tiers:\n",
            "risk_tiers:\n  - {glob: 'graph.yaml', tier: HIGH}\n")
    if level >= 3:
        # R-12: the repair budget is a typed key, required at Level 3
        repo_yaml += "repair:\n  budget: 2\n"
    (tmp_path / "repo.yaml").write_text(repo_yaml)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(ci)
    if level >= 3:
        (tmp_path / ".warden" / "skills-policy.md").write_text(
            "\n".join(f"## {s}\n\ncontent\n" for s in
                      ("Verify", "Autonomy scope", "Forbidden paths",
                       "Review charter", "Integrations", "Shipping")))
    if level >= 4:
        # five shards whose rule_ids all resolve (E-02 and E-04)
        for i in range(5):
            write_shard(tmp_path, f"s{i}", [shard_record("r", seq=i),
                                            shard_record("unmapped:x", seq=i + 100)])
    if level >= 5:
        (tmp_path / "graph.yaml").write_text(yaml.safe_dump({
            "version": 1,
            "nodes": {"builder": {"kind": "skill", "impl": "x", "authority": "find"},
                      "judge": {"kind": "subagent", "impl": "y", "authority": "judge"},
                      "founder": {"kind": "human", "authority": "merge"}},
            "edges": [{"from": "builder", "to": "judge", "payload": "findings"},
                      {"from": "judge", "to": "founder", "payload": "pr"}]}))
        # a shard dense enough that declared rule "r" clears the promotion
        # bar (12/12 fixed -> wilson_lb ~= 0.757 >= 0.7, n >= 10)  [S-04]
        write_shard(tmp_path, "dense",
                    [shard_record("r", seq=200 + i) for i in range(12)])
        bt = tmp_path / ".warden" / "memory" / "backtests"
        bt.mkdir(parents=True)
        # S-05 binds the backtest to the ruleset it judged: the artifact must
        # name the CURRENT rules_version.
        from warden.rules import rules_version
        # S-04 counts this as a rule promoted through the loop: a retro
        # proposed it, its counts clear the bar, and it carries its backtest
        (bt / "r-promotion.json").write_text(json.dumps({
            "rule_id": "r", "action": "promote", "judged": 12, "upheld": 12,
            "wilson_lb": 0.757, "window": "2026-08-24", "note": "fixture",
            "derived_from": "the fixture's dense shard",
            # a promote claim at the current rules_version carries its
            # validation or says why it has none
            "validation": {"status": "unvalidated",
                           "reason": "level-5 fixture; no micro-test run"},
            "rules_version": rules_version(tmp_path / ".warden" / "rules", tmp_path)}))
        promotions = tmp_path / ".warden" / "memory" / "promotions"
        promotions.mkdir(parents=True)
        (promotions / "r-20260824.json").write_text(json.dumps({
            "schema": 1, "rule_id": "r", "backtest": "r-promotion.json",
            "proposed_by": {"source": "retro", "date": "2026-08-24"},
            "proposal": "the fixture retro's proposal row"}))
    return tmp_path


def test_every_heading_rung_is_exactly_anchored(tmp_path):
    """Every policy-heading rung is anchored at both ends.

    docs/wiki/Skills-Policy.md requires 'H2 headings, exact names — skills read them
    verbatim', so a rung satisfied by '## Verify everything twice' certifies
    a policy the skills cannot read. Every heading pattern must carry both
    anchors, and a sloppy heading must fail its rung."""
    ladder = certify_mod.load(ROOT)
    heading_checks = [c for c in ladder["levels"][3]["checks"]
                      if str(c.get("pattern", "")).startswith("^## ")]
    assert len(heading_checks) == 6, "the six policy-section rungs moved"
    for check in heading_checks:
        assert check["pattern"].endswith("$"), (
            f"{check['id']}: pattern {check['pattern']!r} has no right anchor")
    # behavior, not just spelling: a sloppy heading fails each rung
    root = make_repo(tmp_path / "sloppy", 3)
    policy = root / ".warden" / "skills-policy.md"
    exact = policy.read_text()
    for check in heading_checks:
        heading = check["pattern"][1:-1]  # strip ^ and $
        policy.write_text(exact.replace(f"{heading}\n", f"{heading} extras\n"))
        ok, _ = certify_mod._run_check(check, root)
        assert not ok, f"{check['id']}: '{heading} extras' satisfied the rung"
    policy.write_text(exact)
    for check in heading_checks:
        assert certify_mod._run_check(check, root)[0]


def test_derived_files_are_never_evidence_rungs_exist():
    """R-08 (review-memory cache) and R-10 (bd interaction log): derived
    files rebuilt whole by every run must not be tracked — tracking one
    makes every concurrent branch conflict with every other. The asymmetry is
    the point: attest shards ARE committed evidence, these are not."""
    ladder = certify_mod.load(ROOT)
    by_id = {c["id"]: c for c in ladder["levels"][3]["checks"]}
    assert by_id["R-08"]["type"] == "not_tracked"
    assert by_id["R-10"]["type"] == "not_tracked"
    assert by_id["R-10"]["path"] == ".beads/interactions.jsonl"


def test_baseline_ladder_is_valid_and_cumulative():
    ladder = certify_mod.load(ROOT)
    assert sorted(ladder["levels"]) == [1, 2, 3, 4, 5]
    ids = [c["id"] for lv in ladder["levels"].values() for c in lv["checks"]]
    assert len(ids) == len(set(ids)), "duplicate check ids in the baseline"


@pytest.mark.parametrize("level", [0, 1, 2, 3, 4, 5])
def test_attained_level_matches_what_the_repo_actually_has(tmp_path, level):
    root = make_repo(tmp_path / f"r{level}", level)
    doc = certify_mod.run(root)
    assert doc["attained_level"] == level, certify_mod.render(doc)


# ── evidence over cron — the restructured ladder ────────────────────────────

def test_l4_is_evidenced_and_the_cage_left_the_chain():
    """Unattended is a capability, not a maturity rung: no level anywhere in
    the chain may require a cage. The cage checks live on as a capability
    badge instead of leaving the ladder entirely."""
    ladder = certify_mod.load(ROOT)
    assert ladder["levels"][4]["name"] == "Evidenced"
    l4_ids = [c["id"] for c in ladder["levels"][4]["checks"]]
    assert l4_ids == ["E-01", "E-02", "E-03", "E-04"]
    for lv, spec in ladder["levels"].items():
        for check in spec["checks"]:
            assert not check["type"].startswith("cage_"), (
                f"level {lv} still requires the cage via {check['id']}")
            assert not check["id"].startswith("N-"), (
                f"cage check {check['id']} is still in the cumulative chain")
    badge = ladder.get("capabilities", {}).get("autonomous", {})
    badge_ids = [c["id"] for c in badge.get("checks", [])]
    assert badge_ids == ["N-01", "N-02", "N-03"], (
        "the cage checks must survive as the autonomous capability badge")


def test_l5_requires_a_real_promotion_bar_and_a_backtested_rule_change():
    """Level 5 requires a promotion bar and a backtested rule change, so one
    shard cannot certify 'self-improving'."""
    ladder = certify_mod.load(ROOT)
    by_id = {c["id"]: c for c in ladder["levels"][5]["checks"]}
    assert by_id["S-04"]["type"] == "rule_promoted_through_loop"
    assert by_id["S-05"]["type"] == "rule_change_backtested"


# ---------- S-04: a rule promoted through the loop, not a candidate row ------

def _s04() -> dict:
    return {"id": "S-04", "label": "l", "type": "rule_promoted_through_loop"}


def _edit(path: Path, **fields) -> Path:
    """Rewrite a fixture JSON file with `fields` changed; None removes one."""
    doc = json.loads(path.read_text()) if path.exists() else {}
    doc.update(fields)
    path.write_text(json.dumps({k: v for k, v in doc.items()
                                if v is not None}))
    return path


def _backtest(root: Path) -> Path:
    return root / ".warden" / "memory" / "backtests" / "r-promotion.json"


def _record(root: Path) -> Path:
    return root / ".warden" / "memory" / "promotions" / "r-20260824.json"


def test_s04_a_candidate_over_the_bar_with_no_promotion_fails_level_5(tmp_path):
    """The overclaim S-04 used to make: a candidate over the promotion bar is
    a class nobody has written a rule for yet, so it cannot be what makes an
    org self-improving. With the promotion removed and its backtest replaced
    by a reword (so S-05 still passes and only S-04 is under test), the level
    drops."""
    from warden.rules import rules_version
    root = make_repo(tmp_path / "cand", 5)
    write_shard(root, "cand",
                [shard_record("unmapped:klass", seq=i) for i in range(12)])
    _backtest(root).unlink()
    _record(root).unlink()
    (_backtest(root).parent / "r-reword.json").write_text(json.dumps({
        "rule_id": "r", "action": "reword", "judged": 12,
        "rules_version": rules_version(root / ".warden" / "rules", root)}))
    # the candidate really is over the bar — the old reading passes on it
    assert certify_mod._run_check(
        {"id": "S-04", "label": "l", "type": "promotion_bar_met"}, root)[0]
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok
    assert "no promotion record" in detail, detail
    doc = certify_mod.run(root)
    checks = {c["id"]: c for c in doc["checks"]}
    assert checks["S-05"]["passed"], checks["S-05"]["detail"]
    assert doc["attained_level"] == 4, doc["attained_level"]


def test_s04_a_promoted_rule_with_its_backtest_passes(tmp_path):
    root = make_repo(tmp_path / "promoted", 5)
    ok, detail = certify_mod._run_check(_s04(), root)
    assert ok, detail
    assert "r-20260824.json" in detail and "r-promotion.json" in detail, detail
    assert "retro" in detail, detail
    assert certify_mod.run(root)["attained_level"] == 5


def test_s04_a_promote_artifact_without_its_backtest_fails_naming_why(tmp_path):
    root = make_repo(tmp_path / "nobacktest", 5)
    _edit(_backtest(root), window=None, derived_from=None)
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok
    assert "r-20260824.json" in detail and "no backtest" in detail, detail
    # a record naming a backtest that is not there
    _edit(_record(root), backtest="gone.json")
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "no backtest" in detail and "gone.json" in detail, detail


def test_s04_a_promote_artifact_whose_rule_never_cleared_the_bar_fails_naming_why(
        tmp_path):
    root = make_repo(tmp_path / "underbar", 5)
    _edit(_backtest(root), judged=6, upheld=6, wilson_lb=0.61)
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok
    assert "never cleared the promotion bar" in detail, detail
    assert "6 judged" in detail, detail
    # the floor is recomputed from the counts: a stored floor cannot carry it
    _edit(_backtest(root), judged=10, upheld=8, wilson_lb=0.9)
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "never cleared the promotion bar" in detail, detail


def test_s04_a_promotion_no_retro_proposed_does_not_count(tmp_path):
    root = make_repo(tmp_path / "noretro", 5)
    _edit(_record(root), proposed_by=None)
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "not proposed by a retro" in detail, detail
    _edit(_record(root),
          proposed_by={"source": "rule-advisor", "date": "2026-08-24"})
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "not proposed by a retro" in detail, detail
    _edit(_record(root), proposed_by={"source": "retro", "date": "2026-08-24"},
          proposal=None)
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "not proposed by a retro" in detail, detail


def test_s04_a_blank_proposal_does_not_count(tmp_path):
    """`proposal` is the ONLY provenance safeguard S-04 has — the line a
    reviewer reads to find where the retro proposed the rule. Whitespace is
    not a citation, and a missing key is not the only way to write none."""
    root = make_repo(tmp_path / "blankproposal", 5)
    _edit(_record(root), proposal="   \n\t")
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "not proposed by a retro" in detail, detail


def test_s04_a_malformed_proposal_date_does_not_count(tmp_path):
    """The record says WHEN the retro ran. A date nothing can place in time
    is not that, and the shape is the only thing checkable here."""
    root = make_repo(tmp_path / "baddate", 5)
    _edit(_record(root), proposed_by={"source": "retro", "date": "someday"})
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "not proposed by a retro" in detail, detail
    _edit(_record(root), proposed_by={"source": "retro", "date": "2026-8-4"})
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "not proposed by a retro" in detail, detail


def test_s04_a_backtest_naming_a_path_out_of_the_store_does_not_count(tmp_path):
    """`backtest` names a file IN the append-only backtests store. A name
    climbing out of it could point at an artifact the record's own author
    still edits — one inside promotions/, say — so the evidence S-04 counts
    would no longer be append-only."""
    root = make_repo(tmp_path / "escape", 5)
    outside = _record(root).parent / "sub"
    outside.mkdir()
    (outside / "evil.json").write_text(_backtest(root).read_text())
    _edit(_record(root), backtest="../promotions/sub/evil.json")
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "must name a promote artifact under" in detail, detail
    _edit(_record(root), backtest="../../../../../../tmp/evil.json")
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "must name a promote artifact under" in detail, detail


def test_s04_a_boolean_or_negative_count_is_refused_by_name(tmp_path):
    """`isinstance(True, int)` is True, so `judged: true` would read as one
    judged case and be refused for the wrong reason — as under the bar,
    rather than as a count nobody can read. A negative count is the same."""
    root = make_repo(tmp_path / "boolcount", 5)
    _edit(_backtest(root), judged=True, upheld=True)
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok, detail
    assert "judged" in detail and "malformed" in detail, detail
    _edit(_backtest(root), judged=-12, upheld=-12)
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "malformed" in detail, detail


def test_s04_a_backtest_upholding_more_than_it_judged_does_not_count(tmp_path):
    """upheld > judged is not a promotion anyone can read: the floor computed
    from the pair is not a floor, and the artifact is claiming outcomes for
    cases it never judged."""
    root = make_repo(tmp_path / "upheld", 5)
    _edit(_backtest(root), judged=12, upheld=13)
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok, detail
    assert "upheld" in detail and "malformed" in detail, detail


def test_s04_the_pass_detail_says_the_provenance_is_recorded_not_verified(
        tmp_path):
    """S-04 takes the record's word for the proposer and the artifact's word
    for the counts, and can verify neither: a hand-written record pairing any
    over-bar artifact with `"source": "retro"` passes. The detail is where a
    reader learns that, so it must caveat the PROVENANCE, not only the
    counts — it is the line `certify` prints beside 'Self-improving'."""
    root = make_repo(tmp_path / "saysso", 5)
    ok, detail = certify_mod._run_check(_s04(), root)
    assert ok, detail
    assert "provenance and counts as recorded, not verified" in detail, detail


def test_the_adopting_upgrade_note_says_s04_cannot_prove_a_retro_ran():
    """Memory.md states the limit where the S-04 contract is taught. The
    upgrade note is where a consumer reads what Level 5 now needs, so it
    carries the same sentence: a reader who meets S-04 there and nowhere else
    must not take the rung for proof that a retro ran."""
    page = (ROOT / "docs" / "wiki" / "Adopting.md").read_text()
    section = page.split("**Upgrading past the S-04 change")[1].split("\n\n**")[0]
    assert "proves a retro ran" in section, section
    assert "reviewer reads the `proposal`" in section, section


@needs_corpus
def test_this_repos_promotion_records_cite_the_retro_not_their_own_backtest():
    """Memory.md requires `proposal` to cite where the RETRO proposed the
    rule. A record citing the artifact it is meant to corroborate is
    circular — it sends a reviewer back to the evidence under examination —
    and one quoting that artifact's row as the retro's own attributes to the
    retro a count the retro never recorded."""
    records = sorted((ROOT / ".warden" / "memory" / "promotions").glob("*.json"))
    assert records, "this repo committed no promotion record"
    for path in records:
        doc = json.loads(path.read_text())
        proposal = doc["proposal"]
        assert doc["backtest"] not in proposal, (
            f"{path.name}: the proposal cites the backtest it corroborates")
        assert "the backtest's note" not in proposal, (
            f"{path.name}: the proposal sources the retro's row to the "
            "backtest's note")
        cited = re.findall(r"docs/design/[\w.-]+\.md", proposal)
        assert cited, (
            f"{path.name}: the proposal cites no committed report for the "
            "retro that proposed this rule")
        for rel in cited:
            assert (ROOT / rel).is_file(), f"{path.name}: {rel} is not committed"


def test_s04_a_promotion_naming_no_declared_rule_fails(tmp_path):
    root = make_repo(tmp_path / "ghost", 5)
    # a record and a promote artifact that agree with each other, for a rule
    # the ruleset does not declare (a retired rule, say): only the declared-
    # rule clause can refuse it
    ghost_bt = json.loads(_backtest(root).read_text())
    ghost_bt["rule_id"] = "ghost"
    (_backtest(root).parent / "ghost-promotion.json").write_text(
        json.dumps(ghost_bt))
    _edit(_record(root), rule_id="ghost", backtest="ghost-promotion.json")
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "'ghost' names no declared rule" in detail, detail
    # a non-string id is refused by name, never a TypeError
    _edit(_record(root), rule_id=["r"])
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "names no declared rule" in detail, detail


def test_s04_a_record_whose_backtest_is_for_another_rule_does_not_count(
        tmp_path):
    root = make_repo(tmp_path / "otherrule", 5)
    (root / ".warden" / "rules" / "q.md").write_text(
        "---\nid: q\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\n---\nbody\n")
    _edit(_record(root), rule_id="q")
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "not a promote artifact for q" in detail, detail


def test_s04_an_unreadable_promotion_record_is_named_not_skipped(tmp_path):
    root = make_repo(tmp_path / "corrupt", 5)
    _record(root).write_text('{"rule_id": "r", "backtest"')
    ok, detail = certify_mod._run_check(_s04(), root)
    assert not ok and "r-20260824.json" in detail, detail
    # and beside a record that counts, the pass still says it is there
    (_record(root).parent / "r-20260825.json").write_text(json.dumps({
        "schema": 1, "rule_id": "r", "backtest": "r-promotion.json",
        "proposed_by": {"source": "retro", "date": "2026-08-24"},
        "proposal": "p"}))
    ok, detail = certify_mod._run_check(_s04(), root)
    assert ok and "do not count" in detail and "r-20260824.json" in detail, \
        detail


@needs_corpus
def test_this_repos_promotions_through_the_loop_are_the_five_on_record():
    """Five rules went through the whole loop here: a retro proposed each, the
    promote backtest it merged with cleared the bar, and a person merged the
    two together. Four were proposed by the 2026-08-26 retro; `attribution-
    holds` was proposed by the 2026-09-15 one for the `evidence-attribution`
    class. The other promote artifacts are not that: rename-complete (6
    judged, floor 0.61), error-names-cause (5, 0.566) and evidence-intact (9,
    0.701) are retro proposals whose counts never cleared the promotion bar,
    pattern-fit came from a synthesis pass, and wiki-fidelity's is a
    retroactive backtest for a rule written before the loop existed. None of
    those carries a promotion record."""
    promoted, refused = certify_mod.promotions_through_the_loop(ROOT)
    assert sorted(record["rule_id"] for _n, record, _bt in promoted) == [
        "attribution-holds", "enforcement-truth", "fail-closed", "tests-bite",
        "tests-required"], refused
    ok, detail = certify_mod._run_check(_s04(), ROOT)
    assert ok, detail


@pytest.mark.parametrize("field", ("rule_id", "proposed_by", "proposal",
                                   "backtest"))
@needs_corpus
def test_s04_this_repos_newest_promotion_record_needs_every_required_field(
        field):
    """The fixtures above prove each refusal arm against a synthetic record.
    This proves the arms bite on the record this repo actually committed for
    `attribution-holds`: that record counts, and the same record with any one
    required field removed does not.

    It is the record, not the fixture, that S-04 reads on this tree, and the
    record is the ONLY provenance the rung has — so a field quietly absent
    from this file is the failure that matters. Derived from the committed
    file rather than a copy typed here, which would stop measuring it the
    first time the real record changed."""
    from warden.rules import load_rules
    declared = {r.id for r in load_rules(ROOT / ".warden" / "rules")}
    path = (ROOT / ".warden" / "memory" / "promotions"
            / "evidence-attribution-20260915.json")
    record = json.loads(path.read_text())
    why, backtest = certify_mod._promotion_refusal(
        ROOT, path.name, record, declared)
    assert why is None, why
    assert backtest["rule_id"] == record["rule_id"]

    stripped = {k: v for k, v in record.items() if k != field}
    why, _ = certify_mod._promotion_refusal(ROOT, path.name, stripped,
                                            declared)
    assert why, (
        f"{path.name} still counts as a promotion through the loop with "
        f"{field!r} removed — S-04 would be counting a record that says "
        "nothing about who proposed the rule or what it shipped with")


def test_attest_rule_ids_resolve_pass_fail_and_absent(tmp_path):
    root = make_repo(tmp_path / "e02", 3)
    check = {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}
    # absent: no shards yet — vacuously true, corpus size is E-04's job
    ok, detail = certify_mod._run_check(check, root)
    assert ok and "no attest shards" in detail
    # pass: declared id and explicit unmapped: both resolve
    write_shard(root, "good", [shard_record("r"), shard_record("unmapped:x", seq=1)])
    ok, _ = certify_mod._run_check(check, root)
    assert ok
    # fail: a free-form id names no rule — and the message says which shard
    write_shard(root, "bad", [shard_record("ghost", seq=2)])
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "ghost" in detail and "bad" in detail
    # fail closed: shards exist but the rules dir is gone
    import shutil
    shutil.rmtree(root / ".warden" / "rules")
    ok, detail = certify_mod._run_check(check, root)
    assert not ok


def test_promotion_bar_met_pass_fail_and_absent(tmp_path):
    root = make_repo(tmp_path / "s04", 3)
    check = {"id": "S-04", "label": "l", "type": "promotion_bar_met"}
    # absent corpus: fail, and the message names the bar
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "10" in detail
    # thin corpus (n < 10): still fail
    write_shard(root, "thin", [shard_record("r", seq=i) for i in range(3)])
    ok, _ = certify_mod._run_check(check, root)
    assert not ok
    # dense corpus: a declared rule clears N>=10 and wilson_lb>=0.7
    write_shard(root, "dense", [shard_record("r", seq=100 + i) for i in range(12)])
    ok, detail = certify_mod._run_check(check, root)
    assert ok and "r" in detail
    # refutations drag the floor back under the bar
    write_shard(root, "refuted",
                [shard_record("r", status="refuted", seq=300 + i) for i in range(8)])
    ok, _ = certify_mod._run_check(check, root)
    assert not ok


def test_promotion_bar_met_counts_candidates_too(tmp_path):
    """A candidate that clears the promotion-shaped bar is corpus maturity:
    the slug is the proposed rule, and the bar means the corpus can carry a
    promotion decision — which is what 'self-improving' certifies."""
    root = make_repo(tmp_path / "s04c", 3)
    write_shard(root, "cand",
                [shard_record("unmapped:some-class", seq=i) for i in range(12)])
    ok, detail = certify_mod._run_check(
        {"id": "S-04", "label": "l", "type": "promotion_bar_met"}, root)
    assert ok and "some-class" in detail


def test_level5_fixture_repo_yaml_actually_parses(tmp_path):
    """The level-5 fixture's repo.yaml parses to a mapping with risk_tiers.

    V-01, V-03 and S-03 grep repo.yaml's text, so they pass over invalid YAML.
    This test parses the fixture itself, so a broken one fails here by name
    instead of surfacing as R-12 saying repo.yaml could not be read and the
    ruleset checks naming a declaration problem."""
    root = make_repo(tmp_path / "parse5", 5)
    doc = yaml.safe_load((root / "repo.yaml").read_text())
    assert isinstance(doc, dict) and "risk_tiers" in doc


def test_corrupt_shard_fails_closed_everywhere(tmp_path):
    """A corrupted or non-evidence shard must fail E-02 by name, make
    records_from_shards raise (so S-04 can never IMPROVE by losing a
    refutation shard), and never crash certify with a traceback."""
    from warden import memory as memory_mod
    root = make_repo(tmp_path / "corrupt", 3)
    write_shard(root, "dense", [shard_record("r", seq=i) for i in range(12)])
    e02 = {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}
    s04 = {"id": "S-04", "label": "l", "type": "promotion_bar_met"}
    assert certify_mod._run_check(e02, root)[0]
    assert certify_mod._run_check(s04, root)[0]
    # truncated JSON: the shard that (hypothetically) carried refutations
    bad = root / ".warden" / "memory" / "attest" / "refuted.json"
    bad.write_text('{"records": [{"rule_id": "r", "status"')
    ok, detail = certify_mod._run_check(e02, root)
    assert not ok and "refuted.json" in detail
    with pytest.raises(ValueError, match="refuted.json"):
        memory_mod.records_from_shards(root)
    ok, detail = certify_mod._run_check(s04, root)
    assert not ok and "unreadable" in detail
    # an empty JSON object is not an evidence shard (review_events parity)
    bad.write_text("{}")
    ok, detail = certify_mod._run_check(e02, root)
    assert not ok and "not an evidence shard" in detail
    # valid JSON, wrong top-level type: failed check, never a traceback
    bad.write_text("[]")
    ok, _ = certify_mod._run_check(e02, root)
    assert not ok
    with pytest.raises(ValueError):
        memory_mod.records_from_shards(root)


def test_promotion_bar_honors_the_retro_charter(tmp_path):
    """S-04 claims the retro's bar, so the retro's OTHER bars bind: an
    unreadable ruleset fails closed, and a candidate the retro would refuse
    (refutation streak -> noisy, or covered by a declared rule) must not
    certify the rung."""
    s04 = {"id": "S-04", "label": "l", "type": "promotion_bar_met"}
    # unreadable ruleset -> fail even with a bar-clearing candidate
    root = make_repo(tmp_path / "rerr", 3)
    write_shard(root, "cand",
                [shard_record("unmapped:klass", seq=i) for i in range(12)])
    (root / ".warden" / "rules" / "broken.md").write_text("no frontmatter")
    ok, detail = certify_mod._run_check(s04, root)
    assert not ok and "ruleset" in detail
    # refutation streak -> verdict noisy -> fail (dense enough to clear the
    # floor if only n/wilson were checked: 40 upheld, then three ROUNDS that
    # each refuted and upheld nothing — the streak is a trend across rounds)
    root2 = make_repo(tmp_path / "noisy", 3)
    write_shard(root2, "upheld",
                [shard_record("unmapped:klass", seq=i) for i in range(40)])
    for n in range(1, 1 + memory_mod.PAUSE_STREAK):
        write_shard(root2, f"refuted-{n}",
                    [shard_record("unmapped:klass", status="refuted",
                                  round_n=n)])
    ok, _ = certify_mod._run_check(s04, root2)
    assert not ok
    # covered by a declared rule -> the judged history belongs to the rule
    root3 = make_repo(tmp_path / "covered", 3)
    (root3 / ".warden" / "rules" / "owner.md").write_text(
        "---\nid: owner\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\ncovers: [klass]\n---\nbody\n")
    write_shard(root3, "cand",
                [shard_record("unmapped:klass", seq=i) for i in range(12)])
    ok, _ = certify_mod._run_check(s04, root3)
    assert not ok


def test_certify_surfaces_the_rules_dir_declaration_problem(tmp_path):
    """A repo whose rules_dir declaration EXISTS but cannot be resolved (here:
    `review:` as a list, one indent from valid) is not certified against the
    guessed default: a stale ruleset at `.warden/rules` would let every
    ruleset-keyed check (E-02, S-04, S-05) score the WRONG ruleset with
    nothing naming the problem. Fail closed: each of those checks fails and
    names the declaration problem. (In this fixture the
    declared dir and the default coincide; what is pinned is that a
    declaration problem REFUSES to score at all, even with a fully
    scoreable ruleset sitting at the default path — the refusal never
    reads the dir, so the mismatch variant cannot regress separately.)"""
    repo = make_repo(tmp_path, 5)
    baseline = {c["id"]: c for c in certify_mod.run(repo)["checks"]}
    for cid in ("E-02", "S-04", "S-05"):
        assert baseline[cid]["passed"], f"{cid} must pass before the corruption"

    path = repo / "repo.yaml"
    path.write_text(path.read_text().replace(
        "review:\n  rules_dir: .warden/rules\n  blocking_severities: [HIGH]\n",
        "review:\n  - rules_dir: .warden/rules\n"))
    # a fully scoreable ruleset still sits at the default path — the trap
    assert (repo / ".warden" / "rules" / "r.md").is_file()

    doc = certify_mod.run(repo)
    checks = {c["id"]: c for c in doc["checks"]}
    for cid in ("E-02", "S-04", "S-05"):
        assert not checks[cid]["passed"], (
            f"{cid} scored the guessed default as if the repo declared "
            "nothing — a fail-open the other readers of the ruleset refuse")
        assert "cannot be resolved" in checks[cid]["detail"], (
            f"{cid} must NAME the declaration problem: {checks[cid]['detail']}")


def test_declaration_problem_refuses_even_with_no_shards(tmp_path):
    """E-02's no-shards early pass must not outrank the declaration problem:
    with no shards, an unresolvable declaration still fails E-02 by name.
    Otherwise one cause splits into E-02 pass / S-04 fail on the same run."""
    repo = make_repo(tmp_path, 1)
    path = repo / "repo.yaml"
    path.write_text(path.read_text().replace(
        "review:\n  rules_dir: .warden/rules\n  blocking_severities: [HIGH]\n",
        "review:\n  - rules_dir: .warden/rules\n"))
    checks = {c["id"]: c for c in certify_mod.run(repo)["checks"]}
    assert not checks["E-02"]["passed"]
    assert "cannot be resolved" in checks["E-02"]["detail"]


def test_grandfathered_rule_ids_are_loud_and_narrow(tmp_path):
    """E-02 must not retroactively invalidate committed evidence: a consumer
    declares legacy ids in its overlay, E-02 names them aloud — and an
    UNDECLARED unresolvable id still fails."""
    root = make_repo(tmp_path / "gf", 3)
    write_shard(root, "legacy", [shard_record("general")])
    e02 = {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}
    ok, _ = certify_mod._run_check(e02, root)
    assert not ok, "undeclared legacy id must fail"
    (root / ".warden" / "certification.yaml").write_text(
        "version: 1\ngrandfathered_rule_ids: [general]\n")
    ok, detail = certify_mod._run_check(e02, root)
    assert ok and "grandfathered" in detail and "general" in detail
    # the declaration is narrow: a different bad id still fails
    write_shard(root, "fresh", [shard_record("ghost", seq=9)])
    ok, detail = certify_mod._run_check(e02, root)
    assert not ok and "ghost" in detail
    # and a grandfathered-only overlay must not break ladder loading
    assert certify_mod.load(root)["levels"]


def test_grandfathered_ids_never_satisfy_the_promotion_gate(tmp_path):
    """Grandfathering exempts LEGACY evidence from E-02 — it must never make
    that evidence promotion fuel. 12 judged records under a grandfathered id
    ('general') do not satisfy S-04, and stats leaves the id out of
    promotable. Promotion means automating a DECLARED rule; an id naming no
    rule has nothing to automate."""
    root = make_repo(tmp_path / "gfp", 3)
    write_shard(root, "legacy",
                [shard_record("general", seq=i) for i in range(12)])
    (root / ".warden" / "certification.yaml").write_text(
        "version: 1\ngrandfathered_rule_ids: [general]\n")
    # E-02 accepts the declared legacy ids (that is grandfathering's job)
    ok, _ = certify_mod._run_check(
        {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}, root)
    assert ok
    # ...but S-04 must not be satisfied by them
    ok, detail = certify_mod._run_check(
        {"id": "S-04", "label": "l", "type": "promotion_bar_met"}, root)
    assert not ok, f"grandfathered id satisfied the promotion gate: {detail}"
    # and stats itself excludes undeclared ids from promotable
    from warden import memory as memory_mod
    s = memory_mod.stats(root, root / ".warden" / "rules",
                         records=memory_mod.records_from_shards(root))
    assert "general" not in s["promotable"]
    # a DECLARED rule with the same history still promotes
    write_shard(root, "dense", [shard_record("r", seq=100 + i)
                                for i in range(12)])
    ok, detail = certify_mod._run_check(
        {"id": "S-04", "label": "l", "type": "promotion_bar_met"}, root)
    assert ok and "rule r" in detail


def test_overlay_may_not_reuse_capability_ids_or_extend_badges(tmp_path):
    """An overlay may not add a level check under a capability id (N-01), and
    may not add checks to a capability badge."""
    root = make_repo(tmp_path / "capid", 1)
    (root / ".warden" / "certification.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "levels": {1: {"checks": [
            {"id": "N-01", "label": "impostor", "type": "file_exists",
             "path": "repo.yaml"}]}}}))
    with pytest.raises(certify_mod.CertifyError, match="may only ADD"):
        certify_mod.run(root)
    (root / ".warden" / "certification.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "levels": {1: {"checks": []}},
        "capabilities": {"autonomous": {"checks": [
            {"id": "X-01", "label": "x", "type": "file_exists", "path": "z"}]}}}))
    with pytest.raises(certify_mod.CertifyError, match="not extensible"):
        certify_mod.run(root)


# ── _cage_config discovery ───────────────────────────────────────────────────
# Each discovery arm is pinned here, so changing one cannot silently un-enroll
# a cage or leave dead code.

def _cage_repo(tmp_path: Path) -> Path:
    """A repo dir whose PARENT is controlled, so sibling discovery is real."""
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    return root


def test_cage_config_migrated_sibling_wins(tmp_path):
    root = _cage_repo(tmp_path)
    (tmp_path / "cage").mkdir()
    (tmp_path / "cage" / "cage.toml").write_text("[cage]\n")
    found = certify_mod._cage_config(root)
    assert found == tmp_path / "cage" / "cage.toml"


def test_legacy_night_config_names_no_longer_resolve(tmp_path):
    """A nightshift-named config resolves in no slot — sibling dir, `cage/`
    or in-repo. Such a cage on disk is un-enrolled by design; the fix is the
    rename, not eternal lookup."""
    root = _cage_repo(tmp_path)
    (tmp_path / "nightshift").mkdir()
    (tmp_path / "nightshift" / "nightshift.toml").write_text("[cage]\n")
    (tmp_path / "cage").mkdir()
    (tmp_path / "cage" / "nightshift.toml").write_text("[cage]\n")
    (root / "nightshift.toml").write_text("[cage]\n")
    assert certify_mod._cage_config(root) is None


def test_cage_config_skips_a_directory_named_like_the_config(tmp_path):
    """is_file(), not exists(): a stray DIRECTORY named cage.toml must fall
    through to a real config instead of being returned as one."""
    root = _cage_repo(tmp_path)
    (tmp_path / "cage").mkdir()
    (tmp_path / "cage" / "cage.toml").mkdir()  # a directory, not a config
    (root / "cage.toml").write_text("[cage]\n")
    assert certify_mod._cage_config(root) == root / "cage.toml"


def test_cage_config_directory_precedence(tmp_path):
    root = _cage_repo(tmp_path)
    (tmp_path / "cage").mkdir()
    (tmp_path / "cage" / "cage.toml").write_text("[cage]\n")
    (root / "cage.toml").write_text("[cage]\n")
    assert certify_mod._cage_config(root) == tmp_path / "cage" / "cage.toml"


# ── one workspace, two caged checkouts ──────────────────────────────────────
# `<repo>/../cage/` is ONE slot per workspace, so two caged checkouts side by
# side cannot both live in it. Discovery also tries `<repo>/../<checkout>-cage/`,
# and a sibling that DECLARES this root as its live_checkout beats one that
# does not, whatever its name.

def _owned_by(path: Path, owner: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'[project]\nlive_checkout = "{owner}"\n'
                    '[cage]\n[gate]\nforbidden_paths = [".warden/"]\n')
    (path.parent / "prompt.md").write_text("hand-authored\n")


def test_a_neighbours_cage_does_not_shadow_the_repos_own(tmp_path):
    root = _cage_repo(tmp_path)
    _owned_by(tmp_path / "cage" / "cage.toml", tmp_path / "some-other-repo")
    _owned_by(tmp_path / "repo-cage" / "cage.toml", root)
    assert certify_mod._cage_config(root) == tmp_path / "repo-cage" / "cage.toml"


def test_the_named_sibling_is_found_on_its_own(tmp_path):
    root = _cage_repo(tmp_path)
    _owned_by(tmp_path / "repo-cage" / "cage.toml", root)
    assert certify_mod._cage_config(root) == tmp_path / "repo-cage" / "cage.toml"


def test_a_claimant_beats_convention_even_from_the_generic_slot(tmp_path):
    """Ownership is the tie-breaker in BOTH directions: a foreign
    `<checkout>-cage/` must not shadow a `cage/` that claims this root."""
    root = _cage_repo(tmp_path)
    _owned_by(tmp_path / "repo-cage" / "cage.toml", tmp_path / "some-other-repo")
    _owned_by(tmp_path / "cage" / "cage.toml", root)
    assert certify_mod._cage_config(root) == tmp_path / "cage" / "cage.toml"


def test_unclaimed_candidates_keep_a_fixed_order(tmp_path):
    """No claimant: the named sibling, then the generic one, then in-repo —
    deterministic, and the owner check downstream still says whose cage it
    found."""
    root = _cage_repo(tmp_path)
    (root / "cage.toml").write_text("[cage]\n")
    assert certify_mod._cage_config(root) == root / "cage.toml"
    (tmp_path / "cage").mkdir()
    (tmp_path / "cage" / "cage.toml").write_text("[cage]\n")
    assert certify_mod._cage_config(root) == tmp_path / "cage" / "cage.toml"
    (tmp_path / "repo-cage").mkdir()
    (tmp_path / "repo-cage" / "cage.toml").write_text("[cage]\n")
    assert certify_mod._cage_config(root) == tmp_path / "repo-cage" / "cage.toml"


def test_a_foreign_named_sibling_still_refuses_the_badge(tmp_path):
    """The named slot is held to the owner check: a `<checkout>-cage/` that
    cages another repo is reported, not silently passed."""
    root = make_repo(tmp_path / "repo", 1)
    _owned_by(tmp_path / "repo-cage" / "cage.toml", tmp_path / "some-other-repo")
    doc = certify_mod.run(root)
    n01 = [c for c in doc["capabilities"]["autonomous"]["checks"]
           if c["id"] == "N-01"][0]
    assert n01["passed"] is False
    assert "repo-cage/" in n01["detail"] and "not this repo" in n01["detail"]


def test_the_badge_is_earned_beside_a_consumers_cage(tmp_path):
    """A consumer's cage in `../cage/` and the platform's own in
    `../<checkout>-cage/`: all three N checks earn from
    the platform's cage, and N-01 names the directory it found."""
    root = make_repo(tmp_path / "repo", 1)
    _owned_by(tmp_path / "cage" / "cage.toml", tmp_path / "consumer")
    _owned_by(tmp_path / "repo-cage" / "cage.toml", root)
    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    assert badge["earned"] is True
    assert [c["id"] for c in badge["checks"] if c["passed"]] == ["N-01", "N-02", "N-03"]
    n01 = [c for c in badge["checks"] if c["id"] == "N-01"][0]
    assert "repo-cage" in n01["detail"]


def test_an_in_repo_claimant_does_not_beat_a_foreign_sibling(tmp_path):
    """A `<root>/cage.toml` claiming this root does not win over a foreign
    sibling, which the owner check then refuses. The in-repo file is one the
    caged session can write, and one `cage enroll` can never render (its cage
    dir would be the checkout), so ownership is a tie-breaker between
    SIBLINGS only; in-repo stays the last fallback."""
    root = make_repo(tmp_path / "repo", 1)
    _owned_by(tmp_path / "cage" / "cage.toml", tmp_path / "some-other-repo")
    _owned_by(root / "cage.toml", root)
    assert certify_mod._cage_config(root) == tmp_path / "cage" / "cage.toml"
    n01 = [c for c in certify_mod.run(root)["capabilities"]["autonomous"]["checks"]
           if c["id"] == "N-01"][0]
    assert n01["passed"] is False and "not this repo" in n01["detail"]


def test_two_claimants_refuse_the_badge_naming_both(tmp_path):
    """Two sibling cages both declaring this checkout fail every badge check,
    naming both. Resolving by order could report the strict cage while one
    with the post-check DISABLED is the one that runs; a conflict certify
    alone is positioned to see is reported, not resolved."""
    root = make_repo(tmp_path / "repo", 1)
    _owned_by(tmp_path / "repo-cage" / "cage.toml", root)
    _owned_by(tmp_path / "cage" / "cage.toml", root)
    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    assert badge["earned"] is False
    for check in badge["checks"]:
        assert check["passed"] is False
        # the exact phrase, not two substrings: "cage/" is inside "repo-cage/",
        # so a refusal naming only the first claimant would pass a substring
        # check
        assert check["detail"].startswith(
            "cages at repo-cage/ (declares this checkout) and cage/ "
            "(declares this checkout) — one cage per checkout")
        assert "cannot tell which of them runs" in check["detail"]


def test_an_unreadable_sibling_counts_in_the_conflict(tmp_path):
    """A sibling whose cage.toml cannot be parsed declares no owner, but it
    cannot be disproved as this checkout's cage either, so it counts: beside
    a claimant, every badge check fails naming both, rather than the badge
    being earned from the claimant with the unreadable one named nowhere."""
    root = make_repo(tmp_path / "repo", 1)
    (tmp_path / "repo-cage").mkdir()
    (tmp_path / "repo-cage" / "cage.toml").write_text("this is = not [toml\n")
    (tmp_path / "repo-cage" / "prompt.md").write_text("theirs\n")
    _owned_by(tmp_path / "cage" / "cage.toml", root)
    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    assert badge["earned"] is False
    for check in badge["checks"]:
        assert check["passed"] is False
        assert check["detail"].startswith(
            "cages at cage/ (declares this checkout) and repo-cage/ "
            "(unreadable) — one cage per checkout")
    # alone, an unreadable sibling is no conflict: N-01 cannot disprove it
    # and passes, N-02 says exactly why it fails
    (tmp_path / "cage" / "cage.toml").unlink()
    checks = {c["id"]: c for c in certify_mod.run(root)["capabilities"]["autonomous"]["checks"]}
    assert checks["N-01"]["passed"] is True
    assert checks["N-02"]["passed"] is False and "unreadable" in checks["N-02"]["detail"]


def test_an_unreadable_sibling_certify_did_not_choose_still_refuses(tmp_path):
    """With NO claimant, a readable sibling that declares no owner is CHOSEN
    from the first slot, and an unreadable sibling in the second slot still
    refuses the badge: it cannot be disproved as this checkout's cage, so it
    is named rather than skipped. This is a platform's `agentops-cage/` in
    the first slot beside a consumer's `cage/` in the second."""
    root = make_repo(tmp_path / "repo", 1)
    # readable, parses, declares NO owner -> cannot be disproved, and is
    # first in convention order, so _cage_config chooses it
    (tmp_path / "repo-cage").mkdir()
    (tmp_path / "repo-cage" / "cage.toml").write_text(
        '[project]\nname = "x"\n[gate]\nforbidden_paths = [".warden/"]\n')
    (tmp_path / "repo-cage" / "prompt.md").write_text("hand-authored\n")
    (tmp_path / "cage").mkdir()
    (tmp_path / "cage" / "cage.toml").write_text("this is = not [toml\n")
    (tmp_path / "cage" / "prompt.md").write_text("theirs\n")

    assert certify_mod._cage_config(root) == tmp_path / "repo-cage" / "cage.toml"
    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    assert badge["earned"] is False, badge
    for check in badge["checks"]:
        assert check["passed"] is False
        # every named cage carries WHY it could not be ruled out, the chosen
        # one included: "cages at A and B" without a reason
        # leaves the operator guessing which to fix.
        assert check["detail"].startswith(
            "cages at repo-cage/ (declares no owner) and cage/ (unreadable) — "
            "one cage per checkout")
        assert "cannot tell which of them runs" in check["detail"]


def test_two_unreadable_siblings_are_both_named(tmp_path):
    """With every sibling unreadable, the refusal names both. Leaning on N-02
    to fail closed would be safe but would report the second sibling
    nowhere; failing closed is not the same as saying what could not be
    read."""
    root = make_repo(tmp_path / "repo", 1)
    for d in ("repo-cage", "cage"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "cage.toml").write_text("this is = not [toml\n")
        (tmp_path / d / "prompt.md").write_text("x\n")
    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    assert badge["earned"] is False
    for check in badge["checks"]:
        assert check["passed"] is False
        assert check["detail"].startswith(
            "cages at repo-cage/ (unreadable) and cage/ (unreadable) — one "
            "cage per checkout")


# ── an UNCHOSEN non-disprovable sibling, readable or not ────────────────────
# A readable sibling declaring NO OWNER cannot be disproved either, so it
# counts in a conflict just as an unreadable one does. The ruling is a
# committed `warden decide` shard; these are the layouts it governs.

def _no_owner(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[project]\nname = "x"\n[cage]\n'
                    '[gate]\nforbidden_paths = [".warden/"]\n')
    (path.parent / "prompt.md").write_text("hand-authored\n")


def test_two_no_owner_siblings_refuse_the_badge_naming_both(tmp_path):
    """Two readable siblings, both with `[gate] forbidden_paths` and a
    prompt.md, neither declaring `live_checkout`, fail every badge check
    naming both — not earned from the slot-1 one with `cage/` named nowhere.

    Both can be the one that fires and they can declare DIFFERENT forbidden
    paths, which is the hazard the two-claimant refusal exists for."""
    root = make_repo(tmp_path / "repo", 1)
    _no_owner(tmp_path / "repo-cage" / "cage.toml")
    _no_owner(tmp_path / "cage" / "cage.toml")
    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    assert badge["earned"] is False, badge
    for check in badge["checks"]:
        assert check["passed"] is False
        assert check["detail"].startswith(
            "cages at repo-cage/ (declares no owner) and cage/ (declares no "
            "owner) — one cage per checkout")
        assert "cannot tell which of them runs" in check["detail"]


def test_a_no_owner_sibling_beside_an_unreadable_chosen_one_is_named(
        tmp_path):
    """Unreadable in slot 1, readable no-owner in slot 2: N-02 on the CHOSEN
    unreadable cage would fail on its own, but every check's refusal must
    also name the non-disprovable sibling. Failing closed is not the same as
    saying what could not be ruled out."""
    root = make_repo(tmp_path / "repo", 1)
    (tmp_path / "repo-cage").mkdir()
    (tmp_path / "repo-cage" / "cage.toml").write_text("this is = not [toml\n")
    (tmp_path / "repo-cage" / "prompt.md").write_text("x\n")
    _no_owner(tmp_path / "cage" / "cage.toml")
    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    assert badge["earned"] is False
    for check in badge["checks"]:
        assert check["passed"] is False
        assert check["detail"].startswith(
            "cages at repo-cage/ (unreadable) and cage/ (declares no owner) — "
            "one cage per checkout")


def test_a_claimant_beside_a_no_owner_sibling_refuses(tmp_path):
    """An explicit CLAIMANT (a sibling declaring this checkout, and the one
    `_cage_config` chooses) beside a stale sibling that merely declares no
    owner refuses the badge.

    That is the same rule that refuses a claimant beside an UNREADABLE
    sibling, but it must be stated, tested, and true in the message: the
    claimant is named because it DECLARES this checkout, so a refusal saying
    certify "cannot rule them out" would be false of it."""
    root = make_repo(tmp_path / "repo", 1)
    _owned_by(tmp_path / "repo-cage" / "cage.toml", root)     # the claimant
    _no_owner(tmp_path / "cage" / "cage.toml")                # cannot be ruled out
    assert certify_mod._cage_config(root) == tmp_path / "repo-cage" / "cage.toml"
    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    assert badge["earned"] is False, badge
    for check in badge["checks"]:
        assert check["passed"] is False
        assert check["detail"].startswith(
            "cages at repo-cage/ (declares this checkout) and cage/ (declares "
            "no owner) — one cage per checkout")


def test_every_named_cage_says_why_it_is_named(tmp_path):
    """The property the message must hold, checked here on a claimant beside
    a no-owner sibling: no cage is listed without the reason it could not be
    ruled out — and a claimant's reason is that it declares this checkout,
    never silence."""
    root = make_repo(tmp_path / "repo", 1)
    _owned_by(tmp_path / "repo-cage" / "cage.toml", root)
    _no_owner(tmp_path / "cage" / "cage.toml")
    detail = certify_mod.run(root)["capabilities"]["autonomous"]["checks"][0]["detail"]
    named = re.findall(r"(?:repo-)?cage/(?: \(([^)]+)\))?", detail)
    assert named and all(reason for reason in named), (
        f"a cage is named with no reason in: {detail}")


def test_one_no_owner_sibling_alone_still_earns_the_badge(tmp_path):
    """The bound: a config declaring no owner cannot be disproved and PASSES
    when it is the only candidate — one non-disprovable candidate is not
    ambiguous, two are. Extending the refusal to this layout would refuse
    ordinary enrolment on consumers, which the ruling weighed and rejected."""
    root = make_repo(tmp_path / "repo", 1)
    _no_owner(tmp_path / "cage" / "cage.toml")
    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    assert badge["earned"] is True, badge
    n01 = [c for c in badge["checks"] if c["id"] == "N-01"][0]
    assert "cage/cage.toml" in n01["detail"]


def test_a_disproved_sibling_is_not_counted_as_unruled(tmp_path):
    """The other bound: a sibling that declares a DIFFERENT live_checkout IS
    disproved — it is somebody else's cage, not an unruled candidate here — so
    the platform enrolled beside its consumer's cage still earns the badge.
    Without this the ruling would refuse the two-checkout layout the named
    sibling slot exists to support."""
    root = make_repo(tmp_path / "repo", 1)
    _owned_by(tmp_path / "cage" / "cage.toml", tmp_path / "consumer")
    _owned_by(tmp_path / "repo-cage" / "cage.toml", root)
    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    assert badge["earned"] is True, badge


@needs_corpus
def test_the_ruling_is_recorded_before_the_code_that_implements_it():
    """The ruling on unchosen siblings is on record: a committed `warden
    decide` shard for it names `live_checkout` in what it decided. Whether the
    single-candidate pass extends to an UNCHOSEN SECOND candidate is a
    consumer-blast-radius question, so the decision is committed evidence,
    not a commit message."""
    shards = sorted((ROOT / ".warden" / "memory" / "decide").glob("*.json"))
    ruling = [json.loads(p.read_text()) for p in shards]
    mine = [d for d in ruling if d.get("bead") == "agentops-2ky"]
    assert mine, "no warden decide shard records the unchosen-sibling ruling"
    # ANY of them, not the first: one bead can carry a ruling AND its later
    # amendment, and reading `mine[0]` would tie this guard to whichever
    # shard sorts first. The claim is that the ruling is ON RECORD, so the
    # quantifier is `any`.
    assert any("live_checkout" in d["decided"] for d in mine), \
        [d["decided"][:120] for d in mine]


# ── the retired-tuple residue disclosure ────────────────────────────────────

def test_the_residue_line_names_what_is_still_hand_rolled():
    """certify.py's `RETIRED-TUPLE RESIDUE:` line appears once and names
    exactly which of `attest._uncommitted_shards` and
    `memory._event_already_filed` still catch `JSONDecodeError` by hand, the
    retired `(OSError, json.JSONDecodeError)` read. A stale line sends a
    reader to fix what is fixed, and miscounts the sites a sweep for the
    retired tuple should find.

    Derived from both readers' own source, so the line cannot go stale in
    either direction: repairing or regressing either reader turns this red
    until the line is updated."""
    import ast

    def hand_rolls(module: str, func: str) -> bool:
        tree = ast.parse((ROOT / "warden" / f"{module}.py").read_text())
        node = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == func)
        return any("JSONDecodeError" in ast.unparse(h.type)
                   for h in ast.walk(node)
                   if isinstance(h, ast.ExceptHandler) and h.type is not None)

    residue = {f"{mod}.{fn}"
               for mod, fn in (("attest", "_uncommitted_shards"),
                               ("memory", "_event_already_filed"))
               if hand_rolls(mod, fn)}
    line = [ln for ln in (ROOT / "warden" / "certify.py").read_text().splitlines()
            if "RETIRED-TUPLE RESIDUE:" in ln]
    assert len(line) == 1, "the disclosure names its residue exactly once"
    declared = {name.strip() for name in
                line[0].split("RETIRED-TUPLE RESIDUE:")[1].split(",")
                if name.strip()}
    assert declared == residue, (
        f"the disclosure names {sorted(declared)}; the source says "
        f"{sorted(residue)} still hand-roll the retired tuple")


def test_cage_config_absent_fails_every_cage_check_closed(tmp_path):
    root = _cage_repo(tmp_path)
    assert certify_mod._cage_config(root) is None
    for kind in ("cage_enrolled", "cage_forbidden_paths_nonempty",
                 "cage_file_exists"):
        ok, detail = certify_mod._run_check(
            {"id": "N-XX", "label": "l", "type": kind, "path": "prompt.md"},
            root)
        assert not ok and "not enrolled" in detail


def test_cage_checks_read_the_migrated_cage(tmp_path):
    """cage_file_exists keys on cage.parent and forbidden-paths parses raw
    TOML — proven against a `../cage/cage.toml` sibling."""
    root = _cage_repo(tmp_path)
    cage = tmp_path / "cage"
    cage.mkdir()
    (cage / "cage.toml").write_text(
        '[cage]\n[gate]\nforbidden_paths = ["supabase/migrations/"]\n')
    (cage / "prompt.md").write_text("hand-authored\n")
    ok, _ = certify_mod._run_check(
        {"id": "N-01", "label": "l", "type": "cage_enrolled"}, root)
    assert ok
    ok, _ = certify_mod._run_check(
        {"id": "N-03", "label": "l", "type": "cage_file_exists",
         "path": "prompt.md"}, root)
    assert ok, "prompt.md must resolve relative to the cage config's own dir"
    ok, detail = certify_mod._run_check(
        {"id": "N-02", "label": "l", "type": "cage_forbidden_paths_nonempty"},
        root)
    assert ok and "1 forbidden path" in detail


# ── semantic check types, not grep proxies ──────────────────────────────────

def test_ci_step_enforced_pass_fail_and_absent(tmp_path):
    """E-01/E-03 depth: a grep sees only that CI DECLARES a step; this
    type sees that the step can actually gate — a continue-on-error step or
    a workflow missing the trigger event must fail, and the message names
    the fix."""
    root = make_repo(tmp_path / "ci", 1)
    wf = root / ".github" / "workflows" / "ci.yml"
    check = {"id": "E-01", "label": "l", "type": "ci_step_enforced",
             "pattern": "warden attest check", "event": "pull_request"}
    # absent: no workflow mentions the step
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "warden attest check" in detail
    # pass: enforced step in a workflow triggered on the event
    wf.write_text(
        "on: [push, pull_request]\n"
        "jobs:\n  gate:\n    steps:\n"
        "      - run: warden attest check --base origin/main\n")
    ok, _ = certify_mod._run_check(check, root)
    assert ok
    # fail: the step is advisory — and the message says what to change
    wf.write_text(
        "on: [push, pull_request]\n"
        "jobs:\n  gate:\n    steps:\n"
        "      - run: warden attest check --base origin/main\n"
        "        continue-on-error: true\n")
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "continue-on-error" in detail
    # fail: right step, wrong trigger — never fires on the required event
    wf.write_text(
        "on: [push]\n"
        "jobs:\n  gate:\n    steps:\n"
        "      - run: warden attest check --base origin/main\n")
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "pull_request" in detail
    # the GitHub Actions YAML-1.1 quirk: bare `on:` parses as boolean True
    wf.write_text(
        "on:\n  pull_request:\n    branches: [main]\n"
        "jobs:\n  gate:\n    steps:\n"
        "      - run: warden attest check --base origin/main\n")
    ok, _ = certify_mod._run_check(check, root)
    assert ok, "mapping-form `on:` (parsed as the True key) must count"
    # fail closed: unparseable workflow is a failed check, not a pass
    wf.write_text("on: [pull_request]\njobs: [broken")
    ok, _ = certify_mod._run_check(check, root)
    assert not ok
    # JOB-level continue-on-error is the same advisory escape one level up
    wf.write_text(
        "on: [push, pull_request]\n"
        "jobs:\n  gate:\n    continue-on-error: true\n    steps:\n"
        "      - run: warden attest check --base origin/main\n")
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "continue-on-error" in detail
    # an advisory copy in an earlier-sorted file must not veto an enforced
    # one elsewhere (order independence)
    (root / ".github" / "workflows" / "a-advisory.yml").write_text(
        "on: [pull_request]\n"
        "jobs:\n  g:\n    steps:\n"
        "      - run: warden attest check --base origin/main\n"
        "        continue-on-error: true\n")
    wf.write_text(
        "on: [push, pull_request]\n"
        "jobs:\n  gate:\n    steps:\n"
        "      - run: warden attest check --base origin/main\n")
    ok, _ = certify_mod._run_check(check, root)
    assert ok, "an enforced step must certify regardless of file sort order"
    (root / ".github" / "workflows" / "a-advisory.yml").unlink()
    # the dict-inside-list `on:` shape is a workflow GitHub itself rejects:
    # never a crash, never certified (only string entries count)
    wf.write_text(
        "on: [push, {pull_request: {branches: [main]}}]\n"
        "jobs:\n  gate:\n    steps:\n"
        "      - run: warden attest check --base origin/main\n")
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "pull_request" in detail
    # shell-swallowed exit is the same advisory escape in the run script
    wf.write_text(
        "on: [push, pull_request]\n"
        "jobs:\n  gate:\n    steps:\n"
        "      - run: warden attest check --base origin/main || true\n")
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "swallowed" in detail
    # .yaml extension workflows are first-class
    wf.unlink()
    (root / ".github" / "workflows" / "ci.yaml").write_text(
        "on: [pull_request]\n"
        "jobs:\n  gate:\n    steps:\n"
        "      - run: warden attest check --base origin/main\n")
    ok, _ = certify_mod._run_check(check, root)
    assert ok


def _swallow_check_repo(tmp_path, run_block: str, name: str = "ci"):
    """A repo whose one workflow runs the E-01 pattern inside `run_block`."""
    root = make_repo(tmp_path / name, 1)
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "on: [push, pull_request]\n"
        "jobs:\n  gate:\n    steps:\n"
        "      - run: |\n"
        + "".join(f"          {line}\n" for line in run_block.splitlines()))
    check = {"id": "E-01", "label": "l", "type": "ci_step_enforced",
             "pattern": "warden attest check", "event": "pull_request"}
    return certify_mod._run_check(check, root)


def test_or_exit_0_is_swallowed(tmp_path):
    """`|| exit 0` swallows failure exactly like `|| true`: a step that can
    never fail must not certify as enforced, and the detail names the
    marker."""
    ok, detail = _swallow_check_repo(
        tmp_path, "warden attest check --base origin/main || exit 0")
    assert not ok and "swallowed" in detail and "|| exit 0" in detail


def test_semicolon_true_is_swallowed(tmp_path):
    """A trailing `; true` replaces the command's exit with true's."""
    ok, detail = _swallow_check_repo(
        tmp_path, "warden attest check --base origin/main; true")
    assert not ok and "swallowed" in detail and "; true" in detail


def test_set_plus_e_is_swallowed(tmp_path):
    """`set +e` earlier in the same run
    block disarms errexit, so a later failing line cannot fail the step
    (the step's verdict is the LAST command's)."""
    ok, detail = _swallow_check_repo(
        tmp_path,
        "set +e\nwarden attest check --base origin/main\necho done")
    assert not ok and "swallowed" in detail and "set +e" in detail


def test_spacing_variants_are_swallowed(tmp_path):
    """The marker match is on shell meaning, not on one spacing: `||exit 0`
    and `;true` swallow identically."""
    ok, detail = _swallow_check_repo(
        tmp_path, "warden attest check --base origin/main ||exit 0")
    assert not ok and "swallowed" in detail
    ok, detail = _swallow_check_repo(
        tmp_path, "warden attest check --base origin/main ;true", name="second")
    assert not ok and "swallowed" in detail


def test_or_echo_is_swallowed(tmp_path):
    """`|| echo failed` swallows exactly like `|| true` — a swallowed exit in
    its most common real-CI spelling."""
    ok, detail = _swallow_check_repo(
        tmp_path, 'warden attest check --base origin/main || echo "failed"')
    assert not ok and "swallowed" in detail and "|| echo" in detail


def test_unpiped_gate_with_pipe_swallow(tmp_path):
    """GitHub's DEFAULT run shell is `bash -e` with no pipefail, so
    `<gate> | tee log` exits with tee's status — the step can never fail, so
    it must not certify."""
    ok, detail = _swallow_check_repo(
        tmp_path, "warden attest check --base origin/main | tee gate.log")
    assert not ok and "pipefail" in detail


def test_pipe_with_pipefail_still_certifies(tmp_path):
    """The pipe flag is about the missing pipefail, so setting it (or
    `shell: bash`, which GitHub runs `-eo pipefail`) clears it."""
    ok, detail = _swallow_check_repo(tmp_path, (
        "set -o pipefail\n"
        "warden attest check --base origin/main | tee gate.log"))
    assert ok, detail
    root = make_repo(tmp_path / "second", 1)
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "on: [push, pull_request]\n"
        "jobs:\n  gate:\n    steps:\n"
        "      - shell: bash\n"
        "        run: warden attest check --base origin/main | tee gate.log\n")
    check = {"id": "E-01", "label": "l", "type": "ci_step_enforced",
             "pattern": "warden attest check", "event": "pull_request"}
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail


def test_suffix_marker_on_a_non_gate_line_still_certifies(tmp_path):
    """Suffix swallowers are scoped to the gate command's own logical line —
    under the default `bash -e` a `; true` on a LATER cleanup line swallows
    nothing, and flagging it would fail genuinely gating consumer steps."""
    ok, detail = _swallow_check_repo(tmp_path, (
        "warden attest check --base origin/main\n"
        'gh pr comment "$PR" --body done; true'))
    assert ok, detail


def test_suffix_marker_split_across_a_continuation_is_caught(tmp_path):
    """Line scoping must not reopen the line-break escape: `<gate> ||` at
    end of line continuing to `true` on the next is one logical line."""
    ok, detail = _swallow_check_repo(
        tmp_path, "warden attest check --base origin/main ||\n  true")
    assert not ok and "swallowed" in detail


def test_set_plus_o_errexit_is_swallowed(tmp_path):
    ok, detail = _swallow_check_repo(tmp_path, (
        "set +o errexit\n"
        "warden attest check --base origin/main\necho done"))
    assert not ok and "set +o errexit" in detail


def test_same_line_semicolon_exit_0_is_swallowed(tmp_path):
    """`<gate>; exit 0` on ONE line is an unconditional swallow — distinct
    from the exempt bare `exit 0` on its own line (conditional early-exit)."""
    ok, detail = _swallow_check_repo(
        tmp_path, "warden attest check --base origin/main; exit 0")
    assert not ok and "; exit 0" in detail


def test_pipefail_off_and_comment_spellings_do_not_disarm(tmp_path):
    """Neither `set +o pipefail` (the DISABLING spelling) nor a comment
    mentioning pipefail is protection: a bare 'pipefail' substring match
    would certify a swallowing pipe."""
    ok, detail = _swallow_check_repo(tmp_path, (
        "set +o pipefail\n"
        "warden attest check --base origin/main | tee gate.log"))
    assert not ok and "pipefail" in detail
    ok, detail = _swallow_check_repo(tmp_path, (
        "# note: no pipefail here\n"
        "warden attest check --base origin/main | tee gate.log"), name="second")
    assert not ok and "pipefail" in detail


def test_custom_shell_template_earns_no_pipefail_credit(tmp_path):
    """Only the exact `shell: bash` keyword runs
    `-eo pipefail`; a custom template like `bash {0}` runs verbatim with
    neither option, so a piped gate under it must not certify."""
    root = make_repo(tmp_path / "ci", 1)
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "on: [push, pull_request]\n"
        "jobs:\n  gate:\n    steps:\n"
        "      - shell: bash {0}\n"
        "        run: warden attest check --base origin/main | tee gate.log\n")
    check = {"id": "E-01", "label": "l", "type": "ci_step_enforced",
             "pattern": "warden attest check", "event": "pull_request"}
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "pipefail" in detail


def test_marker_before_the_gate_command_does_not_flag(tmp_path):
    """`x || echo ok; <gate>` guards x,
    not the gate — suffix markers count only from the gate pattern onward
    on its logical line."""
    ok, detail = _swallow_check_repo(tmp_path, (
        'git fetch origin main || echo "no cache"; '
        "warden attest check --base origin/main"))
    assert ok, detail


def test_marker_inside_a_quoted_argument_does_not_flag(tmp_path):
    """A `|` or `|| true` inside a string argument is
    text, not shell — quoted segments are stripped before matching."""
    ok, detail = _swallow_check_repo(
        tmp_path, 'warden attest check --base origin/main --note "pass|fail"')
    assert ok, detail
    ok, detail = _swallow_check_repo(
        tmp_path,
        'warden attest check --base origin/main --note \'never || true\'',
        name="second")
    assert ok, detail


def test_legit_early_exit_and_status_reraise_still_certify(tmp_path):
    """The documented limit stays a limit, not a false positive: a
    conditional early-exit `exit 0` on its own line (the fork-PR exemption
    shape) and an `|| status=$?` capture that re-raises via `exit "$status"`
    are how this repo's own attest step is written — both must certify."""
    ok, detail = _swallow_check_repo(tmp_path, (
        'set -uo pipefail\n'
        'if [ "$FORK" = yes ]; then\n'
        '  echo "fork PR: attestation not required"\n'
        '  exit 0\n'
        'fi\n'
        'status=0\n'
        'warden attest check --base origin/main || status=$?\n'
        'exit "$status"'))
    assert ok, detail


def test_check_field_malformations_fail_at_load_not_runtime(tmp_path):
    """A check missing its type-specific field is a named
    loader error, never a KeyError traceback mid-run."""
    root = make_repo(tmp_path / "fields", 1)
    (root / ".warden" / "certification.yaml").write_text(yaml.safe_dump({
        "version": 1, "levels": {1: {"checks": [
            {"id": "P-01", "label": "x", "type": "ci_step_enforced"}]}}}))
    with pytest.raises(certify_mod.CertifyError, match="pattern"):
        certify_mod.run(root)
    (root / ".warden" / "certification.yaml").write_text(yaml.safe_dump({
        "version": 1, "levels": {1: {"checks": [
            {"id": "P-01", "label": "x", "type": "glob_min",
             "path": "*.md", "min": "five"}]}}}))
    with pytest.raises(certify_mod.CertifyError, match="integer"):
        certify_mod.run(root)
    # bool subclasses int — `min: true` is still not an integer count
    (root / ".warden" / "certification.yaml").write_text(yaml.safe_dump({
        "version": 1, "levels": {1: {"checks": [
            {"id": "P-01", "label": "x", "type": "glob_min",
             "path": "*.md", "min": True}]}}}))
    with pytest.raises(certify_mod.CertifyError, match="integer"):
        certify_mod.run(root)
    # a non-string event is a load-time error, not a runtime TypeError
    (root / ".warden" / "certification.yaml").write_text(yaml.safe_dump({
        "version": 1, "levels": {1: {"checks": [
            {"id": "P-01", "label": "x", "type": "ci_step_enforced",
             "pattern": "warden attest check", "event": ["pull_request"]}]}}}))
    with pytest.raises(certify_mod.CertifyError, match="event"):
        certify_mod.run(root)


def test_corpus_min_reviews_pass_fail_and_absent(tmp_path):
    """E-04 depth: count the reviews the evidence pipeline itself
    counts (memory.review_events), not files — five '{}' files are zero
    reviews."""
    root = make_repo(tmp_path / "corpus", 3)
    check = {"id": "E-04", "label": "l", "type": "corpus_min_reviews", "min": 5}
    # absent: no shard dir
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "0" in detail and "5" in detail
    # fail: five files that are not evidence — review_events refuses them
    attest_dir = root / ".warden" / "memory" / "attest"
    attest_dir.mkdir(parents=True)
    for i in range(5):
        (attest_dir / f"junk{i}.json").write_text("{}")
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "unreadable" in detail
    # pass: five real shards
    for i in range(5):
        write_shard(root, f"s{i}", [shard_record("r", seq=i)])
    ok, _ = certify_mod._run_check(check, root)
    assert ok


def test_baseline_e_rungs_use_the_semantic_types():
    """E-01 and E-03 are `ci_step_enforced` and E-04 `corpus_min_reviews`:
    the ladder verifies enforcement, not declaration."""
    ladder = certify_mod.load(ROOT)
    by_id = {c["id"]: c for c in ladder["levels"][4]["checks"]}
    assert by_id["E-01"]["type"] == "ci_step_enforced"
    assert by_id["E-03"]["type"] == "ci_step_enforced"
    assert by_id["E-04"]["type"] == "corpus_min_reviews"


def test_platform_attest_step_is_actually_enforcing():
    """Self-hosting: E-01 requires an enforcing attest step, so the
    platform's own must not be continue-on-error."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    doc = yaml.safe_load(text)
    found = False
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            if "warden attest check" in str(step.get("run", "")):
                found = True
                assert not step.get("continue-on-error"), (
                    "the platform's attest check step is advisory — E-01 "
                    "would be certified by a step that cannot gate")
    assert found, ("no attest check step in ci.yml at all — this test would "
                   "otherwise pass vacuously while the gate disappears")
    assert "(advisory)" not in text, (
        "the step is enforcing; no artifact it writes may still say advisory")


def _has_token(text: str, token: str) -> bool:
    """`token` appears in `text` as a whole token, not inside a longer word."""
    return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", text) is not None


def _has_stem(text: str, stem: str) -> bool:
    """`stem` appears in `text` as a word, bare or inflected (adds, added)."""
    return re.search(rf"(?<![\w-]){re.escape(stem)}(?:s|ed|ing)?(?![\w-])",
                     text) is not None


def test_s05_missing_backtest_remedy_names_who_writes_each_action(tmp_path):
    """The missing-backtest remedy says who writes each action's backtest.
    `warden autonomy pause` writes one only past the streak, never for a
    rule file edited by hand; `warden autonomy adopt` writes one only for a
    rule file added by command, outside blocking_severities; every other
    case, and every other action, is written by hand. A remedy naming a
    command for a case it does not cover points at a command that cannot
    help.

    Two halves. The routes certify declares must EQUAL the table below, which
    is written here and not derived from certify: per action, each writer and
    its exact condition facts. And the message must carry every route, each
    action in the clause of its writer, and each fact's value (the streak with
    its direction, inside or outside blocking_severities, the rule file's
    provenance). No connecting word is asserted, and the by-hand writer is
    read from certify, so rewording it keeps this green.

    The rule file's provenance is checked against words this test names per
    provenance, not against certify's own phrases: reading them from certify
    would let a swap of "added by command" and "added by hand" stay green."""
    from warden.memory import PAUSE_STREAK
    hand = None
    expected = {
        "pause": [("warden autonomy pause",
                   {("refuting_rounds_at_least", PAUSE_STREAK)}),
                  (hand, {("rule_file", "edited_by_hand")})],
        "adopt": [("warden autonomy adopt",
                   {("rule_file", "added_by_command"),
                    ("blocking_severities", "outside")}),
                  (hand, {("rule_file", "added_by_hand")}),
                  (hand, {("blocking_severities", "inside")})],
        "promote": [(hand, set())],
        "retire": [(hand, set())],
        "reword": [(hand, set())],
        "demote": [(hand, set())],
        "narrow": [(hand, set())],
        "unpause": [(hand, set())],
    }
    # Each rule-file provenance as the words its clause must and must not
    # carry, written here and not read from certify's phrases: a message that
    # swapped "added by command" and "added by hand", or called a hand edit
    # an addition, would otherwise be checked against its own wording. Word
    # stems only, so the connecting words around them stay free.
    provenance_words = {
        "added_by_command": ({"add"}, {"hand", "edit"}),
        "added_by_hand": ({"add", "hand"}, {"edit"}),
        "edited_by_hand": ({"edit", "hand"}, {"add"}),
    }
    assert set(expected) == set(certify_mod.BACKTEST_ACTIONS), (
        "an action was added or removed: say who writes its backtest here")

    def canonical(pairs):
        return sorted(((w or "", sorted(facts)) for w, facts in pairs),
                      key=repr)

    routes = certify_mod.backtest_routes(PAUSE_STREAK)
    actual: dict[str, list] = {}
    for route in routes:
        for action, facts in route.cases:
            actual.setdefault(action, []).append((route.writer, set(facts)))
    assert ({a: canonical(p) for a, p in actual.items()}
            == {a: canonical(p) for a, p in expected.items()}), (
        "a backtest route changed its writer or its condition")

    root = make_repo(tmp_path / "s05remedy", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    ok, detail = certify_mod._run_check(check, root)
    assert not ok
    hand_writer = certify_mod.BACKTEST_HAND_WRITER.strip()
    assert hand_writer and "warden" not in hand_writer, (
        "the by-hand route names no author")
    opposite = {"inside": "outside", "outside": "inside"}
    for route in routes:
        clause = certify_mod.backtest_route_clause(route)
        assert clause in detail, f"the message does not render {clause!r}"
        marker = route.writer or hand_writer
        assert marker in clause, f"{clause!r} lost its writer"
        # the writer's own words cannot stand in for an action it covers, and
        # the clause may name no action it has no case for
        body = clause.replace(marker, "")
        named = {a for a in certify_mod.BACKTEST_ACTIONS if _has_token(body, a)}
        assert named == {a for a, _ in route.cases}, (
            f"{clause!r} names actions {sorted(named)}, not its cases")
        for action, facts in route.cases:
            case = certify_mod.backtest_case_text(action, facts)
            assert case in clause and _has_token(case, action), case
            for key, value in facts:
                if key == "refuting_rounds_at_least":
                    assert re.search(rf"(?<![\d.]){value}\+", case), (
                        f"{case!r} does not say at least {value} rounds")
                elif key == "blocking_severities":
                    assert (_has_token(case, "blocking_severities")
                            and _has_token(case, value)
                            and not _has_token(case, opposite[value])), (
                        f"{case!r} does not say {value} blocking_severities")
                else:
                    assert key == "rule_file", f"no expectation for {key!r}"
                    say, deny = provenance_words[value]
                    for word in say:
                        assert _has_stem(case, word), (
                            f"{case!r} does not say {value}: no {word!r}")
                    for word in deny:
                        assert not _has_stem(case, word), (
                            f"{case!r} is not {value}, yet says {word!r}")
        if route.writer is None:
            assert _has_token(clause, "rules_version"), (
                "the by-hand route does not say which rules_version it names")
    for skill in ("retro", "rule-advisor"):
        assert _has_token(detail, skill), (
            f"the remedy does not point at the {skill} skill")


def test_rule_change_backtested_pass_fail_and_absent(tmp_path):
    root = make_repo(tmp_path / "s05", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    # absent: fail, message names the fix
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "backtest" in detail
    # fail: artifact names a rule that does not exist
    bt.mkdir(parents=True)
    (bt / "ghost.json").write_text(json.dumps(
        {"rule_id": "ghost", "action": "promote", "judged": 12}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "ghost" in detail
    (bt / "ghost.json").unlink()
    # fail: unrecognized action
    (bt / "weird.json").write_text(json.dumps(
        {"rule_id": "r", "action": "vibes", "judged": 12}))
    ok, _ = certify_mod._run_check(check, root)
    assert not ok
    (bt / "weird.json").unlink()
    # pass: one valid artifact naming a declared rule AT the current
    # rules_version; a bare rule_id is not enough. A malformed artifact left
    # in the dir would block the pass — corrupt cannot be masked — so the
    # failing cases are cleared before this one.
    (bt / "r.json").write_text(json.dumps(
        {"rule_id": "r", "action": "promote", "judged": 12,
         "validation": {"status": "unvalidated", "reason": "fixture"},
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok and "r.json" in detail


def test_levels_do_not_skip(tmp_path):
    # a repo with level-3 artifacts but no level-1 gate is NOT level 3
    root = make_repo(tmp_path / "skip", 3)
    text = (root / "repo.yaml").read_text().replace(
        f"platform:\n  pin: v{__version__}\n", "")
    (root / "repo.yaml").write_text(text)  # drop the pin
    doc = certify_mod.run(root)
    assert doc["attained_level"] == 0
    assert any(c["id"] == "G-03" and not c["passed"] for c in doc["checks"])


def test_project_may_extend_the_ladder(tmp_path):
    root = make_repo(tmp_path / "ext", 1)
    (root / ".warden" / "certification.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "levels": {1: {"name": "Gated", "checks": [
            {"id": "P-01", "label": "project charter present",
             "type": "file_exists", "path": "CHARTER.md"}]}}}))
    doc = certify_mod.run(root)
    assert any(c["id"] == "P-01" for c in doc["checks"])
    assert doc["attained_level"] == 0, "the added check is not satisfied yet"
    (root / "CHARTER.md").write_text("ours\n")
    assert certify_mod.run(root)["attained_level"] == 1


def test_project_may_not_redefine_a_baseline_check(tmp_path):
    root = make_repo(tmp_path / "cheat", 1)
    # the attack: redefine G-03 as a check that trivially passes
    (root / ".warden" / "certification.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "levels": {1: {"name": "Gated", "checks": [
            {"id": "G-03", "label": "totally fine", "type": "file_exists",
             "path": "repo.yaml"}]}}}))
    with pytest.raises(certify_mod.CertifyError, match="may only ADD"):
        certify_mod.run(root)


def test_unknown_check_type_fails_loudly(tmp_path):
    root = make_repo(tmp_path / "bad", 1)
    (root / ".warden" / "certification.yaml").write_text(yaml.safe_dump({
        "version": 1, "levels": {1: {"checks": [
            {"id": "X-01", "label": "x", "type": "vibes"}]}}}))
    with pytest.raises(certify_mod.CertifyError, match="unknown type"):
        certify_mod.run(root)


def test_render_names_what_blocks_the_next_level(tmp_path):
    doc = certify_mod.run(make_repo(tmp_path / "r1", 1))
    out = certify_mod.render(doc)
    assert "certification: LEVEL 1" in out
    assert "to reach level 2" in out and "V-01" in out


def test_platform_certifies_itself_at_least_level_3():
    """The platform is its own consumer; it must climb its own ladder."""
    doc = certify_mod.run(ROOT)
    assert doc["attained_level"] >= 3, certify_mod.render(doc)


# ── the autonomous capability badge — orthogonal to the ladder ───────────────

def _cage_beside(root: Path) -> None:
    cage = root.parent / "cage"
    cage.mkdir()
    (cage / "cage.toml").write_text(
        '[cage]\n[gate]\nforbidden_paths = [".warden/"]\n')
    (cage / "prompt.md").write_text("hand-authored\n")


def test_caged_repo_earns_the_badge_at_any_level(tmp_path):
    """A Level-1 repo with a cage earns autonomous — capability is orthogonal
    to maturity."""
    root = make_repo(tmp_path / "repo", 1)
    _cage_beside(root)
    doc = certify_mod.run(root)
    assert doc["attained_level"] == 1
    badge = doc["capabilities"]["autonomous"]
    assert badge["earned"] is True
    assert [c["id"] for c in badge["checks"]] == ["N-01", "N-02", "N-03"]
    out = certify_mod.render(doc)
    assert "badges: autonomous" in out


def test_uncaged_level5_certifies_without_the_badge(tmp_path):
    """Self-improving needs no cage, and the render says the badge was not
    earned rather than hiding it."""
    root = make_repo(tmp_path / "repo", 5)
    doc = certify_mod.run(root)
    assert doc["attained_level"] == 5
    assert doc["capabilities"]["autonomous"]["earned"] is False
    out = certify_mod.render(doc)
    assert "LEVEL 5" in out and "badges: none" in out
    # the render names what blocks the badge, like it names the next level
    assert "N-01" in out


def test_partial_cage_does_not_earn_the_badge(tmp_path, monkeypatch, capsys):
    """A cage failing ONE check (empty forbidden_paths) must not earn, and the
    CLI names the failing check. An all-or-nothing fixture cannot tell
    `earned` from an any() over the checks; this one can."""
    from warden import cli
    root = make_repo(tmp_path / "repo", 2)
    cage = tmp_path / "cage"
    cage.mkdir()
    (cage / "cage.toml").write_text('[cage]\n[gate]\nforbidden_paths = []\n')
    (cage / "prompt.md").write_text("hand-authored\n")
    doc = certify_mod.run(root)
    badge = doc["capabilities"]["autonomous"]
    assert badge["earned"] is False, "2 of 3 checks passing must not earn"
    assert [c["id"] for c in badge["checks"] if c["passed"]] == ["N-01", "N-03"]
    (root / ".warden" / "out").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(root)
    assert cli.main(["certify", "--capability", "autonomous"]) == 1
    assert "N-02" in capsys.readouterr().err, (
        "the not-earned message must name the failing check")


def test_sibling_cage_for_another_repo_does_not_earn(tmp_path):
    """Discovery is by convention, so a sibling cage declaring a DIFFERENT
    live_checkout must refuse the badge, or a checkout earns it from a
    neighbouring consumer's cage. A cage declaring this root still earns."""
    root = make_repo(tmp_path / "repo", 1)
    cage = tmp_path / "cage"
    cage.mkdir()
    other = tmp_path / "some-other-repo"
    other.mkdir()
    (cage / "cage.toml").write_text(
        f'[project]\nlive_checkout = "{other}"\n'
        '[cage]\n[gate]\nforbidden_paths = [".warden/"]\n')
    (cage / "prompt.md").write_text("hand-authored\n")
    doc = certify_mod.run(root)
    assert doc["capabilities"]["autonomous"]["earned"] is False
    n01 = [c for c in doc["capabilities"]["autonomous"]["checks"]
           if c["id"] == "N-01"][0]
    assert "not this repo" in n01["detail"]
    # and a cage declaring THIS root still earns
    (cage / "cage.toml").write_text(
        f'[project]\nlive_checkout = "{root}"\n'
        '[cage]\n[gate]\nforbidden_paths = [".warden/"]\n')
    assert certify_mod.run(root)["capabilities"]["autonomous"]["earned"] is True


def test_cli_capability_gate(tmp_path, monkeypatch, capsys):
    from warden import cli
    root = make_repo(tmp_path / "repo", 2)
    (root / ".warden" / "out").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(root)
    assert cli.main(["certify", "--capability", "autonomous"]) == 1
    assert "autonomous" in capsys.readouterr().err
    _cage_beside(root)
    assert cli.main(["certify", "--capability", "autonomous"]) == 0
    # unknown badge is a usage error, not a silent pass
    assert cli.main(["certify", "--capability", "ghost"]) == 2
    assert "ghost" in capsys.readouterr().err


def test_cli_level_gate(tmp_path, monkeypatch, capsys):
    from warden import cli
    root = make_repo(tmp_path / "cli", 2)
    (root / ".warden" / "out").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(root)
    assert cli.main(["certify", "--level", "2"]) == 0
    assert cli.main(["certify", "--level", "4"]) == 1
    assert "required level 4" in capsys.readouterr().err
    art = sorted((root / ".warden" / "out").glob("*-certify/certification.json"))
    assert art and json.loads(art[0].read_text())["attained_level"] == 2


def test_tracked_derived_cache_is_caught(tmp_path):
    """The bug this check exists for: a consumer commits the derived memory
    cache, and every branch that reviews anything then conflicts."""
    import subprocess
    root = make_repo(tmp_path / "tracked", 3)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    mem = root / ".warden" / "memory"
    mem.mkdir(parents=True)
    (mem / "findings.jsonl").write_text("{}\n")

    # untracked -> passes
    doc = certify_mod.run(root)
    r08 = [c for c in doc["checks"] if c["id"] == "R-08"][0]
    assert r08["passed"], r08["detail"]

    # committed -> caught, with actionable detail
    subprocess.run(["git", "add", "-f", ".warden/memory/findings.jsonl"],
                   cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "oops"], cwd=root, check=True)
    doc = certify_mod.run(root)
    r08 = [c for c in doc["checks"] if c["id"] == "R-08"][0]
    assert not r08["passed"]
    assert "TRACKED" in r08["detail"] and "git rm --cached" in r08["detail"]
    assert doc["attained_level"] < 3, "a tracked cache must block Level 3"


def test_a_tracked_evidence_run_root_is_caught(tmp_path):
    """R-13, a Level 3 not_tracked rung, refuses a repo that TRACKS
    `.warden/out/`, the root every run writes its artifacts under. A writer
    minting a uniquely-named run dir there conflicts with nothing, but the
    progress boundary log is a FIXED path appended to on every branch, which
    is the conflict shape R-08 exists for: a tracked root conflicts on every
    reviewing branch."""
    import subprocess
    ladder = certify_mod.load(ROOT)
    by_id = {c["id"]: c for c in ladder["levels"][3]["checks"]}
    rung = by_id.get("R-13")
    assert rung and rung["type"] == "not_tracked", (
        "no Level 3 not_tracked rung for the evidence run root")
    assert rung["path"] == ".warden/out/", rung

    root = make_repo(tmp_path / "tracked-out", 3)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    out = root / ".warden" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "progress.jsonl").write_text("{}\n")

    doc = certify_mod.run(root)
    r13 = [c for c in doc["checks"] if c["id"] == "R-13"][0]
    assert r13["passed"], r13["detail"]

    subprocess.run(["git", "add", "-f", ".warden/out/progress.jsonl"],
                   cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "oops"], cwd=root, check=True)
    doc = certify_mod.run(root)
    r13 = [c for c in doc["checks"] if c["id"] == "R-13"][0]
    assert not r13["passed"]
    assert "TRACKED" in r13["detail"] and "git rm --cached" in r13["detail"]
    assert doc["attained_level"] < 3, "a tracked run root must block Level 3"


def test_an_undeclared_repair_budget_blocks_level_3(tmp_path):
    """A repo.yaml with no `repair.budget` fails R-12 and does not certify
    Level 3: the key is required, whatever the policy prose says."""
    root = make_repo(tmp_path / "budget", 3)
    assert certify_mod.run(root)["attained_level"] == 3
    text = (root / "repo.yaml").read_text()
    (root / "repo.yaml").write_text(text.replace("repair:\n  budget: 2\n", ""))
    policy = root / ".warden" / "skills-policy.md"
    policy.write_text(policy.read_text()
                      + "\n- Iteration budget before a run reverts: 2 attempts.\n")
    doc = certify_mod.run(root)
    r12 = [c for c in doc["checks"] if c["id"] == "R-12"][0]
    assert not r12["passed"], r12
    assert doc["attained_level"] < 3, "an undeclared budget must block Level 3"


# ── Build-disciplines vocabulary ──────────────────────────────────────────

def _with_disciplines(tmp_path, body: str, name: str) -> Path:
    """A level-3 repo whose policy file carries `body` appended."""
    root = make_repo(tmp_path / name, 3)
    policy = root / ".warden" / "skills-policy.md"
    policy.write_text(policy.read_text() + "\n" + body)
    return root


def _r09(root: Path) -> dict:
    return [c for c in certify_mod.run(root)["checks"] if c["id"] == "R-09"][0]


def test_unrecognized_discipline_name_is_caught(tmp_path):
    """The silent failure this exists to prevent: a repo declares
    'root-cause-first', believes it bound a discipline, and nothing ever
    reads it because the contract does not know that name."""
    root = _with_disciplines(
        tmp_path, "## Build disciplines\n\n- root-cause-first: any red test.\n",
        "unknown")
    c = _r09(root)
    assert not c["passed"]
    assert "root-cause-first" in c["detail"]
    # the message must name what IS valid, or the reader cannot fix it
    assert "systematic-debugging" in c["detail"]


def test_recognized_names_pass(tmp_path):
    root = _with_disciplines(
        tmp_path,
        "## Build disciplines\n\n"
        "- test-driven-development: changes under src/.\n"
        "- systematic-debugging: any red.\n"
        "- verification-before-completion: before claims.\n",
        "known")
    c = _r09(root)
    assert c["passed"], c["detail"]
    assert "3" in c["detail"]


def test_absent_section_passes_because_it_is_optional(tmp_path):
    """Absent must never fail — that is the whole contract of an optional
    section, and breaking it would fail every already-enrolled consumer."""
    root = make_repo(tmp_path / "absent", 3)
    c = _r09(root)
    assert c["passed"]
    # say WHY it passed, so a reader does not think the check is broken
    assert "optional" in c["detail"].lower()


def test_prose_and_blank_lines_do_not_count_as_disciplines(tmp_path):
    """A section may carry an explanatory sentence. Only 'name: when' lines
    are declarations; treating prose as a name would fail honest policies."""
    root = _with_disciplines(
        tmp_path,
        "## Build disciplines\n\n"
        "These bind whoever builds, hook or not.\n\n"
        "- test-driven-development: changes under src/.\n",
        "prose")
    c = _r09(root)
    assert c["passed"], c["detail"]


def test_a_declaration_missing_its_when_clause_is_caught(tmp_path):
    """'test-driven-development' with no 'when it applies' is a name with no
    scope — unenforceable, and the reader cannot tell what it covers."""
    root = _with_disciplines(
        tmp_path, "## Build disciplines\n\n- test-driven-development:\n",
        "noscope")
    c = _r09(root)
    assert not c["passed"]
    assert "scope" in c["detail"].lower() or "when" in c["detail"].lower()


def test_a_later_section_does_not_leak_into_the_disciplines_block(tmp_path):
    """Section parsing must stop at the next H2. A policy that declares
    disciplines before ## Shipping must not read Shipping's prose as names."""
    root = _with_disciplines(
        tmp_path,
        "## Build disciplines\n\n- test-driven-development: src/.\n\n"
        "## Extra\n\n- not-a-discipline: should be ignored.\n",
        "bounded")
    c = _r09(root)
    assert c["passed"], c["detail"]


def test_r09_accepts_design_approval(tmp_path):
    """A repo can declare design-approval and R-09 accepts it (recognized
    alongside the other three)."""
    from warden.certify import RECOGNIZED_DISCIPLINES
    assert "design-approval" in RECOGNIZED_DISCIPLINES
    root = _with_disciplines(
        tmp_path,
        "## Build disciplines\n\n- design-approval: any HIGH-tier change.\n",
        "designapproval")
    c = _r09(root)
    assert c["passed"], c["detail"]


def test_wrapped_prose_is_not_read_as_a_declaration(tmp_path):
    """A wrapped line whose continuation happens to start `word:` is not a
    discipline named after that word.

    This is distill's real policy text, whose continuation line starting
    `asking:` must not declare a discipline called 'asking'. Prose wraps; a
    parser that assumes otherwise
    fails the honest policies and passes nothing extra.
    """
    body = """## Build disciplines

These bind whoever builds — including the executor inside its repair loops.

- test-driven-development: any change under `worker/` or `ios/` — the failing
  test comes first and is watched failing. Exceptions worth taking without
  asking: generated code, and `supabase/migrations/**` (forward-only SQL a
  run may not touch at all).
- systematic-debugging: any red test, flake, or unexpected behavior — name the
  root cause before changing a line.
"""
    root = _with_disciplines(tmp_path, body, "wrapped")
    c = _r09(root)
    assert c["passed"], c["detail"]
    assert "asking" not in c["detail"]
    assert "2 discipline(s)" in c["detail"]


def test_prose_declaring_a_name_outside_a_list_item_is_ignored(tmp_path):
    """Only list items declare. A sentence mentioning a discipline in
    passing must not be read as declaring it — nor as an error."""
    body = """## Build disciplines

We considered systematic-debugging: it is covered by the executor already.

- test-driven-development: changes under src/.
"""
    root = _with_disciplines(tmp_path, body, "mention")
    c = _r09(root)
    assert c["passed"], c["detail"]
    assert "1 discipline(s)" in c["detail"]


# ---------- S-05 binds the backtest to the rule change ------------------------

def _rv(root: Path) -> str:
    from warden.rules import rules_version
    return rules_version(root / ".warden" / "rules", root)


def test_s05_requires_a_backtest_naming_the_current_rules_version(tmp_path):
    """S-05 requires a backtest stamped with the CURRENT rules_version, not any
    backtest naming a declared rule, which a year-old one would satisfy
    forever. A rule change (which bumps the version) is not evidenced until a
    fresh backtest names the new version."""
    root = make_repo(tmp_path / "s05rv", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    base = {"rule_id": "r", "action": "promote", "judged": 12,
            "validation": {"status": "unvalidated", "reason": "fixture"}}

    # a valid backtest with a STALE rules_version does NOT satisfy S-05
    (bt / "stale.json").write_text(json.dumps({**base, "rules_version": "OLD"}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok, "a stale-rules_version backtest satisfied S-05"
    assert "rules_version" in detail

    # a valid backtest naming the CURRENT rules_version passes
    (bt / "current.json").write_text(
        json.dumps({**base, "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail
    assert _rv(root) in detail


def test_s05_fails_when_no_backtest_carries_any_rules_version(tmp_path):
    """A valid backtest for a declared rule, but no rules_version field: it
    does not match the current version, so S-05 fails."""
    root = make_repo(tmp_path / "s05none", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "unstamped.json").write_text(
        json.dumps({"rule_id": "r", "action": "promote", "judged": 12}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "rules_version" in detail


def test_s05_corrupt_artifact_cannot_be_masked_by_a_valid_one(tmp_path):
    """A corrupt artifact must fail the rung even when a valid, current one
    sorts after it: a later success must not discard the problems found
    before it. Names chosen so the corrupt one sorts FIRST."""
    root = make_repo(tmp_path / "s05mask", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "a-corrupt.json").write_text("{not json")
    (bt / "z-valid.json").write_text(json.dumps(
        {"rule_id": "r", "action": "promote", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok, "a corrupt artifact was masked by a later valid one"
    assert "a-corrupt" in detail


def test_s05_corrupt_artifact_after_a_valid_one_still_fails(tmp_path):
    """The OTHER sort direction: a corrupt artifact sorting AFTER a valid
    current one must also fail — the claim is order-independence, not just
    corrupt-first. Guards against a `break`-on-match optimization that would
    stop scanning at the valid one and never see the corrupt one."""
    root = make_repo(tmp_path / "s05mask2", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "a-valid.json").write_text(json.dumps(
        {"rule_id": "r", "action": "promote", "judged": 12,
         "rules_version": _rv(root)}))
    (bt / "z-corrupt.json").write_text("{not json")
    ok, detail = certify_mod._run_check(check, root)
    assert not ok, "a corrupt artifact sorting after a valid one was masked"
    assert "z-corrupt" in detail


@needs_corpus
def test_this_repos_own_backtests_name_the_current_rules_version():
    """The platform gates itself: at least one committed backtest must name
    this repo's CURRENT rules_version, or its own S-05 fails."""
    from warden.rules import rules_version
    root = Path(__file__).resolve().parents[1]
    current = rules_version(root / ".warden" / "rules", root)
    stamped = []
    for p in (root / ".warden" / "memory" / "backtests").glob("*.json"):
        doc = json.loads(p.read_text())
        if doc.get("rules_version") == current:
            stamped.append(p.name)
    assert stamped, (
        f"no committed backtest names the current rules_version {current} — "
        "S-05 would fail on this repo")


def _pause(root: Path, rule_id: str) -> None:
    """Pause a fixture rule the way the writer does — frontmatter only."""
    path = root / ".warden" / "rules" / f"{rule_id}.md"
    path.write_text(path.read_text().replace(
        "applies_to: ['**']\n", "applies_to: ['**']\npaused: true\n"
        "paused_reason: fixture\n", 1))


def test_a_pause_backtest_must_name_a_rule_the_tree_shows_paused(tmp_path):
    """A current-version `pause` backtest does not satisfy S-05 while the rule
    it names sits unpaused — that is evidence for a change that did not
    happen.

    An action in the enum and a rule_id naming a declared rule are not enough
    on their own. A pause is a rule change the committed tree can prove on
    its own (the frontmatter field is right there), so it is bound here: the
    artifact's claim must match the ruleset it was stamped against.
    """
    root = make_repo(tmp_path / "s05pausebind", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "r-pause.json").write_text(json.dumps(
        {"rule_id": "r", "action": "pause", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok, "a pause backtest satisfied S-05 with the rule not paused"
    assert "r" in detail and "not paused" in detail, detail

    # pause the rule for real — that bumps rules_version, so the artifact is
    # re-stamped exactly as `warden autonomy pause` re-stamps it post-edit
    _pause(root, "r")
    (bt / "r-pause.json").write_text(json.dumps(
        {"rule_id": "r", "action": "pause", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail


def test_every_paused_rule_carries_its_own_pause_backtest(tmp_path):
    """Each paused rule needs its own pause artifact: one pause backtest does
    not evidence a second pause bundled into the same rules_version, even
    though `warden autonomy pause` produces current-version pause artifacts
    automatically.

    This binding covers pauses, not every rule change. A pause shows in its
    rule file's frontmatter, so S-05 can bind every one. A body edit or a
    severity move needs the previous ruleset to detect, and the platform keeps
    no ruleset history, so S-05 does not bind those change kinds to the diff:
    one current-version artifact still satisfies the rung for them.
    """
    root = make_repo(tmp_path / "s05eachpause", 3)
    (root / ".warden" / "rules" / "r2.md").write_text(
        "---\nid: r2\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\n---\nbody\n")
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)

    _pause(root, "r")
    _pause(root, "r2")
    (bt / "r-pause.json").write_text(json.dumps(
        {"rule_id": "r", "action": "pause", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok, "one pause artifact satisfied S-05 for two paused rules"
    assert "r2" in detail, detail

    (bt / "r2-pause.json").write_text(json.dumps(
        {"rule_id": "r2", "action": "pause", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail


def test_a_standing_pause_is_not_re_evidenced_at_every_ruleset_edit(tmp_path):
    """A standing pause stays evidenced by its artifact at an older
    rules_version; a pause with no artifact at any version still fails.

    Requiring a CURRENT-rules_version pause artifact per paused rule would be
    a standing ratchet: rules_version is a hash over every rule file plus the
    repo.yaml review subtree, so adding an unrelated rule — exactly what
    `warden autonomy adopt` does — would invalidate the artifact of a pause
    that never changed. Every consumer holding a pause would drop a level the
    next time they touched rules at all, with no waiver (certify.load refuses
    an overlay that redefines a baseline check) and no tooling remedy
    (`apply_pause` refuses an already-paused rule). The only fix would be
    hand-writing evidence for a change that did not happen in that revision —
    which the same check's other arm exists to refuse.
    """
    root = make_repo(tmp_path / "s05standing", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    _pause(root, "r")
    (bt / "r-pause.json").write_text(json.dumps(
        {"rule_id": "r", "action": "pause", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail

    # An unrelated rule lands, carrying its own current-version evidence. The
    # standing pause is untouched; its artifact is now at an older version.
    #
    # The artifact beside the added rule is an `adopt` one, which is what
    # `warden autonomy adopt` emits, so the fixture is a tree the tooling can
    # actually produce.
    (root / ".warden" / "rules" / "q.md").write_text(
        "---\nid: q\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\n---\nbody\n")
    (bt / "q-adopt.json").write_text(json.dumps(
        {"rule_id": "q", "action": "adopt", "judged": 0, "severity": "LOW",
         "note": "adopted by the machine tier", "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, (
        "an unrelated ruleset edit de-evidenced a standing pause: " + detail)

    # what still fails is a pause with NO artifact anywhere — the real gap
    _pause(root, "q")
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "q" in detail and "any rules_version" in detail, detail


def test_an_adoption_and_its_backtest_keep_the_certification_level(tmp_path):
    """The tree `warden autonomy adopt` produces passes S-05, and fails it
    once the adoption's backtest is deleted.

    Adding a rule file bumps rules_version, and S-05 requires SOME artifact
    stamped with the CURRENT version, so `adopt_rule` writes a backtest under
    `.warden/memory/backtests/` along with the rule it writes to the declared
    rules dir. Without it, a granted consumer merging a machine adoption
    would drop a certification level with no tooling remedy. The second half
    of this test deletes exactly that artifact.
    """
    from warden import autonomy as autonomy_mod

    root = make_repo(tmp_path / "s05adopt", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "r-reword.json").write_text(json.dumps(
        {"rule_id": "r", "action": "reword", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail

    # The machine tier adopts a non-blocking rule, writing both halves.
    _dest, artifact = autonomy_mod.adopt_rule(
        root,
        "---\nid: new-lens\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\n---\nbody\n",
        dest_name="new-lens", blocking_severities=["HIGH"],
        today="2026-09-01")
    ok, detail = certify_mod._run_check(check, root)
    assert ok, ("the tree `warden autonomy adopt` produces fails S-05: "
                + detail)
    assert "new-lens" in detail, detail

    # Delete just the artifact and S-05 fails.
    artifact.unlink()
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "no backtest names the current rules_version" in detail, (
        "an adoption with no artifact passed S-05 — the rung stopped noticing "
        "an unevidenced ruleset bump: " + detail)


def test_c9_s05_no_artifact_message_names_every_action_it_accepts(tmp_path):
    """S-05's no-artifact failure names every action in BACKTEST_ACTIONS.

    A hand-typed list goes stale as the constant grows, and a stale list on
    this surface tells a reader the action their tooling just wrote is not one
    the rung accepts, which is the enforcement claim in reverse.
    """
    root = make_repo(tmp_path / "s05enum", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    (root / ".warden" / "memory" / "backtests").mkdir(parents=True)
    ok, detail = certify_mod._run_check(check, root)
    assert not ok, detail
    for action in certify_mod.BACKTEST_ACTIONS:
        assert action in detail, (
            f"S-05's no-artifact message does not name {action!r}, which it "
            f"accepts: {detail}")


def test_s05_does_not_call_a_zero_judged_adoption_backtested(tmp_path):
    """Enforcement truth on the rung's own success line. An adoption carries
    no judged history by construction, and a pass reading "backtested over 0
    judged case(s)" would claim a measurement that never happened."""
    root = make_repo(tmp_path / "s05adoptline", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "r-adopt.json").write_text(json.dumps(
        {"rule_id": "r", "action": "adopt", "judged": 0, "severity": "LOW",
         "note": "adopted by the machine tier", "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail
    assert "backtested over 0" not in detail, detail
    assert "no judged history" in detail, detail


def test_a_boolean_is_not_a_judged_case_count(tmp_path):
    """`isinstance(True, int)` is True in Python, so an isinstance check alone
    would read `"judged": true` as a case count. The rung refuses it and says
    it wants a judged case count."""
    root = make_repo(tmp_path / "s05bool", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "r.json").write_text(json.dumps(
        {"rule_id": "r", "action": "reword", "judged": True,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "judged case count" in detail, detail


def test_s05_says_what_it_bound_and_what_it_did_not(tmp_path):
    """Enforcement truth on the rung's own success line. The binding covers
    pauses; it does NOT prove that every rule changed in a diff carries its
    own backtest, and a pass that implied otherwise would be the claim this
    repo files as an enforcement claim."""
    root = make_repo(tmp_path / "s05says", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "r-reword.json").write_text(json.dumps(
        {"rule_id": "r", "action": "reword", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail
    assert "pause" in detail, (
        "S-05's pass must name the change class it actually binds: " + detail)


def test_s05_accepts_a_reword_action(tmp_path):
    """A rule-BODY edit that neither promotes, pauses, nor retires still bumps
    rules_version, and S-05 demands a fresh artifact naming the new version,
    so `reword` is an accepted action. Refusing it would make the artifact
    malformed (hard-failing the rung, unmaskable by the valid ones beside it),
    while relabeling it `promote` would misstate the action taken. S-05 is a
    Level 5 rung and CI gates only `--level 4`, so that hard failure would
    drop a repo below Level 5 with CI green. The enum widens by one honest
    label; it does not open."""
    root = make_repo(tmp_path / "s05reword", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "r-reword-1.json").write_text(json.dumps(
        {"rule_id": "r", "action": "reword", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail

    # an unknown action is still refused, and still unmaskable
    (bt / "z-bogus.json").write_text(json.dumps(
        {"rule_id": "r", "action": "amended", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "z-bogus" in detail


# ── a promotion claim carries its validation ────────────────────────────────
# A promote-action backtest stamped with the CURRENT rules_version is the
# live promotion claim, and the claim carries micro-test evidence — a
# no-guidance control arm, MICROTEST_MIN_REPS+ reps per variant, variance
# reported, every flagged match manually read — or an explicit
# {status: unvalidated, reason} marker. Artifacts at older rules_versions
# are committed history, judged by the bar of their day, never retroactively.

def _s05_fixture(tmp_path, name):
    root = make_repo(tmp_path / name, 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    return root, check, bt


def _microtest_block():
    """A complete micro-test validation block — every requirement met."""
    return {
        "method": "micro-test",
        "control": {"reps": 5, "exhibited": 4},
        "variants": {"with-rule": {"reps": 5, "exhibited": 1}},
        "variance": "control 4/5, 3/5, 4/5; with-rule 1/5, 0/5, 1/5",
        "flagged_matches_read": True,
    }


def test_s05_bare_promote_at_current_version_fails_naming_validation(tmp_path):
    root, check, bt = _s05_fixture(tmp_path, "s05val")
    (bt / "bare.json").write_text(json.dumps(
        {"rule_id": "r", "action": "promote", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok, "a promote claim with no validation block satisfied S-05"
    assert "validation" in detail and "unvalidated" in detail, detail


def test_s05_promote_marked_unvalidated_with_reason_passes(tmp_path):
    root, check, bt = _s05_fixture(tmp_path, "s05unval")
    (bt / "r.json").write_text(json.dumps(
        {"rule_id": "r", "action": "promote", "judged": 12,
         "rules_version": _rv(root),
         "validation": {"status": "unvalidated",
                        "reason": "corpus-precision record only; no "
                                  "micro-test was run"}}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail


def test_s05_promote_unvalidated_without_reason_fails(tmp_path):
    root, check, bt = _s05_fixture(tmp_path, "s05norea")
    for bad_reason in ("", "   ", None):
        v = {"status": "unvalidated"}
        if bad_reason is not None:
            v["reason"] = bad_reason
        (bt / "r.json").write_text(json.dumps(
            {"rule_id": "r", "action": "promote", "judged": 12,
             "rules_version": _rv(root), "validation": v}))
        ok, detail = certify_mod._run_check(check, root)
        assert not ok and "reason" in detail, (bad_reason, detail)


def test_s05_promote_with_full_microtest_passes(tmp_path):
    root, check, bt = _s05_fixture(tmp_path, "s05micro")
    (bt / "r.json").write_text(json.dumps(
        {"rule_id": "r", "action": "promote", "judged": 12,
         "rules_version": _rv(root), "validation": _microtest_block()}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail


@pytest.mark.parametrize("mutate,expect", [
    (lambda v: v.pop("control"), "control"),
    (lambda v: v["control"].update(exhibited=0), "nothing to fix"),
    (lambda v: v["control"].update(reps=4), "reps"),
    (lambda v: v["variants"]["with-rule"].update(reps=4), "reps"),
    (lambda v: v["variants"]["with-rule"].pop("exhibited"), "exhibited"),
    (lambda v: v.update(variants={}), "variant"),
    (lambda v: v.pop("variants"), "variant"),
    (lambda v: v.pop("variance"), "variance"),
    (lambda v: v.update(variance="  "), "variance"),
    (lambda v: v.update(flagged_matches_read=False), "flagged"),
    (lambda v: v.pop("flagged_matches_read"), "flagged"),
])
def test_s05_microtest_missing_pieces_fail_naming_the_piece(
        tmp_path, mutate, expect):
    """Each protocol requirement is individually enforced, and the refusal
    names the missing piece (error-names-cause)."""
    root, check, bt = _s05_fixture(tmp_path, "s05piece")
    v = _microtest_block()
    mutate(v)
    (bt / "r.json").write_text(json.dumps(
        {"rule_id": "r", "action": "promote", "judged": 12,
         "rules_version": _rv(root), "validation": v}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok, f"mutation {expect!r} still satisfied S-05"
    assert expect in detail, detail


def test_s05_variant_without_exhibited_count_is_not_validated(tmp_path):
    """A treatment arm recording NO outcome at all — `{"reps": 5}` with no
    `exhibited` — is not micro-test evidence, however many reps it has: the
    control-vs-treatment comparison the protocol exists for cannot be made
    from such an artifact."""
    root, check, bt = _s05_fixture(tmp_path, "s05noout")
    v = _microtest_block()
    v["variants"]["with-rule"] = {"reps": 5}
    (bt / "r.json").write_text(json.dumps(
        {"rule_id": "r", "action": "promote", "judged": 12,
         "rules_version": _rv(root), "validation": v}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok, "a variant with no exhibited count certified as validated"
    assert "exhibited" in detail, detail


def test_s05_variant_named_control_cannot_shadow_the_control_arm(tmp_path):
    """A variant literally named 'control' is refused, so merging the arms as
    {"control": control, **variants} cannot let it overwrite a 1-rep control
    arm and clear the floor the refusal message promises. The control arm is
    declared in its own field."""
    root, check, bt = _s05_fixture(tmp_path, "s05shadow")
    v = _microtest_block()
    v["control"]["reps"] = 1
    v["variants"]["control"] = {"reps": 5, "exhibited": 1}
    (bt / "r.json").write_text(json.dumps(
        {"rule_id": "r", "action": "promote", "judged": 12,
         "rules_version": _rv(root), "validation": v}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok, "a variant named 'control' shadowed the control arm"
    assert "control" in detail, detail


def test_s05_stale_promote_artifact_is_history_never_revalidated(tmp_path):
    """A promote artifact can predate the validation requirement: an
    artifact at an OLDER rules_version is history, not a problem — it simply
    is not the current match. A current reword beside it still passes."""
    root, check, bt = _s05_fixture(tmp_path, "s05hist")
    (bt / "a-old.json").write_text(json.dumps(
        {"rule_id": "r", "action": "promote", "judged": 12,
         "rules_version": "OLD"}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "rules_version" in detail and "validation" not in detail
    (bt / "b-now.json").write_text(json.dumps(
        {"rule_id": "r", "action": "reword", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail


def test_s05_nonpromote_actions_need_no_validation(tmp_path):
    """Pause/retire/reword make the gate quieter or edit a body — none is a
    promotion claim, so the micro-test bar does not apply (the autonomy
    pause path keeps writing bare pause artifacts).

    The pause arm pauses the rule first and the retire arm removes one:
    each artifact is bound to the change the tree shows, which is a
    different bar from the validation one this test is about. Leaving the
    rule in place here would have made this test assert that binding away."""
    root, check, bt = _s05_fixture(tmp_path, "s05quiet")
    (bt / "r.json").write_text(json.dumps(
        {"rule_id": "r", "action": "reword", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, ("reword", detail)
    # a retire names a rule the tree no longer declares, so it retires one
    rules = root / ".warden" / "rules"
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(root, "rules")
    (rules / "gone.md").unlink()
    (bt / "r.json").write_text(json.dumps(
        {"rule_id": "gone", "action": "retire", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, ("retire", detail)
    _pause(root, "r")
    (bt / "r.json").write_text(json.dumps(
        {"rule_id": "r", "action": "pause", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, ("pause", detail)


_GONE_RULE = ("---\nid: {id}\nseverity: LOW\nengine: claude\n"
              "applies_to: ['**']\n---\nbody\n")


def _commit_all(root: Path, message: str) -> None:
    import subprocess
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=root,
                       check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True)


def _retire_artifact(bt: Path, rule_id: str, rules_version: str) -> Path:
    path = bt / f"{rule_id}-retire-{rules_version}.json"
    path.write_text(json.dumps(
        {"rule_id": rule_id, "action": "retire", "judged": 12,
         "rules_version": rules_version}))
    return path


def test_s05_accepts_a_retire_whose_rule_file_is_removed(tmp_path):
    """Retiring a rule deletes its file, so the retire artifact S-05's own
    message asks for names a rule the tree no longer declares. S-05 accepts
    it because a rule file in the committed history declared it: before the
    retire is committed, after, and after a later rule change."""
    root, check, bt = _s05_fixture(tmp_path, "s05retire")
    rules = root / ".warden" / "rules"
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(root, "rules")

    # the retire, before its commit: HEAD still holds the rule file
    (rules / "gone.md").unlink()
    _retire_artifact(bt, "gone", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail

    # committed: an earlier commit holds it
    _commit_all(root, "retire gone")
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail

    # a later rule change: the retire artifact is history, still well-formed
    (rules / "r.md").write_text((rules / "r.md").read_text() + "more\n")
    (bt / "r-reword.json").write_text(json.dumps(
        {"rule_id": "r", "action": "reword", "judged": 12,
         "rules_version": _rv(root)}))
    _commit_all(root, "reword r")
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail


def test_s05_refuses_a_retire_no_committed_rule_file_declared(tmp_path):
    """The retire carve-out is bound to git history, not opened: an id no
    committed rule file declared stays refused, and with no readable history
    there is nothing to bind it to. A rule retired long before this
    rules_version stands at it: the verdict reads the outcome, a rule absent
    from the tree that history declared, never which commit removed it."""
    root, check, bt = _s05_fixture(tmp_path, "s05noretire")
    rules = root / ".warden" / "rules"

    # no git history at all: refused, and the message says why
    ghost = _retire_artifact(bt, "ghost", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "ghost" in detail and "history" in detail, detail
    ghost.unlink()

    (rules / "old.md").write_text(_GONE_RULE.format(id="old"))
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(root, "rules")
    (rules / "old.md").unlink()
    _commit_all(root, "retire old")
    (rules / "gone.md").unlink()
    _commit_all(root, "retire gone")
    current = _rv(root)
    _retire_artifact(bt, "gone", current)
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail

    # never declared by any rule file the history holds
    ghost = _retire_artifact(bt, "ghost", current)
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and ghost.name in detail, detail
    ghost.unlink()

    _retire_artifact(bt, "old", current)
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail


@pytest.mark.parametrize("shape", ["split", "squashed"])
def test_s05_retire_verdict_does_not_depend_on_commit_shape(tmp_path, shape):
    """A retire commit followed by a reword commit passes S-05, as the same
    change in one commit does. Checking the retire against the parent of the
    newest commit that changed a rule file would fail the split shape, where
    that parent already lacks the rule. Both artifacts sit at the final
    rules_version in either shape."""
    root, check, bt = _s05_fixture(tmp_path, f"s05{shape}")
    rules = root / ".warden" / "rules"
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(root, "rules")
    (rules / "gone.md").unlink()
    if shape == "split":
        _commit_all(root, "retire gone")
    (rules / "r.md").write_text((rules / "r.md").read_text() + "more\n")
    _commit_all(root, "reword r")
    current = _rv(root)
    _retire_artifact(bt, "gone", current)
    (bt / "r-reword.json").write_text(json.dumps(
        {"rule_id": "r", "action": "reword", "judged": 12,
         "rules_version": current}))
    _commit_all(root, "backtests")
    ok, detail = certify_mod._run_check(check, root)
    assert ok, (shape, detail)


@pytest.mark.parametrize("layout", ["symlink", "crlf"])
def test_s05_retire_reads_git_objects_not_worktree_bytes(tmp_path, layout):
    """A committed retire passes for a rule stored differently from its
    checkout (a symlink, whose blob is the target path; an eol=crlf checkout,
    whose blob is LF): comparing rule files on disk with git blobs would read
    the retire as naming no earlier rule."""
    import subprocess
    root, check, bt = _s05_fixture(tmp_path, f"s05{layout}")
    rules = root / ".warden" / "rules"
    if layout == "symlink":
        (root / "shared").mkdir()
        (root / "shared" / "s.md").write_text(_GONE_RULE.format(id="s"))
        (rules / "s.md").symlink_to(Path("..") / ".." / "shared" / "s.md")
    else:
        (root / ".gitattributes").write_text("*.md text eol=crlf\n")
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(root, "rules")
    if layout == "crlf":
        subprocess.run(["git", "rm", "-rq", "--cached", "."], cwd=root,
                       check=True)
        subprocess.run(["git", "reset", "-q", "--hard"], cwd=root, check=True)
        assert b"\r\n" in (rules / "r.md").read_bytes(), "no CRLF checkout"
    (rules / "gone.md").unlink()
    _commit_all(root, "retire gone")
    _retire_artifact(bt, "gone", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, (layout, detail)


def test_s05_a_missing_history_object_is_a_refusal_not_a_crash(tmp_path):
    """`git cat-file --batch` answers `<sha> missing`, a line with no size,
    for an object the store lacks (a partial or pruned clone). S-05 fails
    naming the rule and the sha rather than raising on that line."""
    import subprocess
    root, check, bt = _s05_fixture(tmp_path, "s05missing")
    rules = root / ".warden" / "rules"
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(root, "rules")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD:.warden/rules/gone.md"], cwd=root,
        check=True, capture_output=True, text=True).stdout.strip()
    (rules / "gone.md").unlink()
    _commit_all(root, "retire gone")
    (root / ".git" / "objects" / sha[:2] / sha[2:]).unlink()
    _retire_artifact(bt, "gone", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and "gone" in detail and sha in detail, detail


def test_s05_retire_survives_a_moved_rules_dir(tmp_path):
    """After a `git mv` of the rules dir and a new `rules_dir` in repo.yaml,
    an older retire still passes: a rule file under any rules_dir repo.yaml
    declared in history counts, not only one under the current path."""
    import subprocess
    from warden.rules import rules_version
    root, check, bt = _s05_fixture(tmp_path, "s05moved")
    rules = root / ".warden" / "rules"
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(root, "rules")
    (rules / "gone.md").unlink()
    _retire_artifact(bt, "gone", _rv(root))
    _commit_all(root, "retire gone")
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail

    subprocess.run(["git", "mv", ".warden/rules", ".warden/policy"], cwd=root,
                   check=True)
    config = root / "repo.yaml"
    config.write_text(config.read_text().replace(
        "rules_dir: .warden/rules", "rules_dir: .warden/policy"))
    (bt / "r-reword.json").write_text(json.dumps(
        {"rule_id": "r", "action": "reword", "judged": 12,
         "rules_version": rules_version(root / ".warden" / "policy", root)}))
    _commit_all(root, "move the rules dir")
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail


def test_s05_retire_refusal_blames_a_shallow_clone_only_in_one(tmp_path):
    """A retire refusal names a shallow clone only when git reports the clone
    shallow; blaming one in a full clone sends the reader after the wrong
    cause."""
    import subprocess
    root, check, bt = _s05_fixture(tmp_path, "s05full")
    rules = root / ".warden" / "rules"
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(root, "rules")
    (rules / "gone.md").unlink()
    _commit_all(root, "retire gone")
    _retire_artifact(bt, "gone", "OLD")
    (bt / "r-reword.json").write_text(json.dumps(
        {"rule_id": "r", "action": "reword", "judged": 12,
         "rules_version": _rv(root)}))
    _commit_all(root, "backtests")
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail

    ghost = _retire_artifact(bt, "ghost", "OLD")
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and ghost.name in detail, detail
    assert "shallow" not in detail, detail
    ghost.unlink()

    clone = tmp_path / "s05shallow"
    subprocess.run(["git", "clone", "-q", "-b", "main", "--depth", "1", root.as_uri(),
                    str(clone)], check=True)
    ok, detail = certify_mod._run_check(check, clone)
    assert not ok and "gone-retire-OLD.json" in detail, detail
    assert "shallow" in detail, detail


@pytest.mark.parametrize("action", ["demote", "narrow", "unpause"])
def test_s05_accepts_a_demote_narrow_and_unpause_backtest(tmp_path, action):
    """Demote, narrow and unpause each edit a rule file, so each moves
    rules_version and S-05 demands an artifact at the new version; each is an
    accepted action, so the honest artifact is not refused as malformed. No
    warden command performs them, so each is written by hand.

    Each arm asserts its edit moved rules_version: an arm whose setup put the
    rule back as it found it would test the action enum and nothing else."""
    root, check, bt = _s05_fixture(tmp_path, f"s05{action}")
    rule = root / ".warden" / "rules" / "r.md"
    if action == "demote":
        # from inside the blocking severities the fixture declares ([HIGH])
        rule.write_text(rule.read_text().replace("severity: LOW",
                                                 "severity: HIGH"))
    elif action == "unpause":
        _pause(root, "r")
    before = _rv(root)
    if action == "demote":
        rule.write_text(rule.read_text().replace("severity: HIGH",
                                                 "severity: LOW"))
    elif action == "narrow":
        rule.write_text(rule.read_text().replace("applies_to: ['**']",
                                                 "applies_to: ['app/**']"))
    else:
        rule.write_text(rule.read_text().replace(
            "paused: true\npaused_reason: fixture\n", ""))
    assert _rv(root) != before, f"the {action} arm changed no rule file"
    (bt / f"r-{action}.json").write_text(json.dumps(
        {"rule_id": "r", "action": action, "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert ok, (action, detail)


def test_s05_retire_reads_history_of_a_repo_below_the_git_root(tmp_path):
    """`git log` names paths from the top of the git tree, while the rules
    dirs and repo.yaml are named from the repo root. With repo.yaml in a
    subdirectory, a retire still matches its committed rule file, including
    after a move known only from a committed repo.yaml."""
    import subprocess
    from warden.rules import rules_version
    top = tmp_path / "s05nested"
    root = make_repo(top / "svc", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    rules = root / ".warden" / "rules"
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(top, "rules")
    (rules / "gone.md").unlink()
    _retire_artifact(bt, "gone", _rv(root))
    _commit_all(top, "retire gone")
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail

    # the old rules dir is known only from a committed repo.yaml
    subprocess.run(["git", "mv", ".warden/rules", ".warden/policy"], cwd=root,
                   check=True)
    config = root / "repo.yaml"
    config.write_text(config.read_text().replace(
        "rules_dir: .warden/rules", "rules_dir: .warden/policy"))
    (bt / "r-reword.json").write_text(json.dumps(
        {"rule_id": "r", "action": "reword", "judged": 12,
         "rules_version": rules_version(root / ".warden" / "policy", root)}))
    _commit_all(top, "move the rules dir")
    ok, detail = certify_mod._run_check(check, root)
    assert ok, detail


def test_s05_retire_refusal_names_a_symlinked_rule_file(tmp_path):
    """A rule file committed as a symlink is not followed, so no retire of its
    rule can pass. The refusal names the symlinked rule files it skipped,
    and mentions none when history holds none."""
    root, check, bt = _s05_fixture(tmp_path, "s05symretire")
    rules = root / ".warden" / "rules"
    _commit_all(root, "rules")
    ghost = _retire_artifact(bt, "ghost", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and ghost.name in detail, detail
    assert "symlink" not in detail, detail
    ghost.unlink()

    (root / "shared").mkdir()
    (root / "shared" / "s.md").write_text(_GONE_RULE.format(id="s"))
    (rules / "s.md").symlink_to(Path("..") / ".." / "shared" / "s.md")
    _commit_all(root, "symlinked rule")
    (rules / "s.md").unlink()
    _commit_all(root, "retire s")
    retire = _retire_artifact(bt, "s", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert "symlink" in detail and ".warden/rules/s.md" in detail, detail


def test_s05_retire_refusal_says_the_symlink_list_is_cut(tmp_path):
    """With five symlinked rule files the refusal names the first three and
    says how many more it cut."""
    root, check, bt = _s05_fixture(tmp_path, "s05symcut")
    rules = root / ".warden" / "rules"
    (root / "shared").mkdir()
    for name in "abcde":
        (root / "shared" / f"{name}.md").write_text(_GONE_RULE.format(id=name))
        (rules / f"{name}.md").symlink_to(
            Path("..") / ".." / "shared" / f"{name}.md")
    _commit_all(root, "symlinked rules")
    for name in "abcde":
        (rules / f"{name}.md").unlink()
    _commit_all(root, "retire them")
    retire = _retire_artifact(bt, "e", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert detail.endswith(
        "history holds 5: .warden/rules/a.md, .warden/rules/b.md, "
        ".warden/rules/c.md and 2 more"), detail


def test_s05_retire_refusal_skips_a_symlink_later_read_as_a_rule(tmp_path):
    """A rule file committed as a symlink and later as a regular file whose id
    history reads is not named as a symlink, even in the refusal of an
    unrelated id."""
    root, check, bt = _s05_fixture(tmp_path, "s05symreplaced")
    rules = root / ".warden" / "rules"
    (root / "shared").mkdir()
    (root / "shared" / "s.md").write_text(_GONE_RULE.format(id="s"))
    (rules / "s.md").symlink_to(Path("..") / ".." / "shared" / "s.md")
    _commit_all(root, "symlinked rule")
    (rules / "s.md").unlink()
    (rules / "s.md").write_text(_GONE_RULE.format(id="s"))
    _commit_all(root, "regular rule")
    typo = _retire_artifact(bt, "typo", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and typo.name in detail, detail
    assert "symlink" not in detail, detail


@pytest.mark.parametrize("layout", ["inside-the-git-tree",
                                    "outside-the-git-tree"])
def test_s05_retire_refusal_names_a_rules_dir_outside_the_repo_root(
        tmp_path, layout):
    """repo.yaml may declare a rules dir outside the repo root, such as
    ../shared-rules. History under it is not read, so the refusal names the
    rules dir outside the root rather than saying git history cannot be
    read."""
    import shutil
    from warden.rules import rules_version
    top = tmp_path / f"s05outside-{layout}"
    root = make_repo(top / "svc", 3)
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    shared = top / "shared-rules"
    shared.mkdir()
    shutil.move(root / ".warden" / "rules" / "r.md", shared / "r.md")
    config = root / "repo.yaml"
    config.write_text(config.read_text().replace(
        "rules_dir: .warden/rules", "rules_dir: ../shared-rules"))
    repo = top if layout == "inside-the-git-tree" else root
    (shared / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(repo, "rules")
    (shared / "gone.md").unlink()
    if repo == top:
        _commit_all(repo, "retire gone")
    retire = _retire_artifact(bt, "gone", rules_version(shared, root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert ("the rules dir ../shared-rules is outside the repo root and its "
            "history is not read") in detail, detail
    assert "cannot be read" not in detail, detail


@pytest.mark.parametrize("move", ["down", "up"])
def test_s05_retire_refusal_names_a_repo_yaml_moved_from_another_path(
        tmp_path, move):
    """A rule deleted before repo.yaml and its rules dir moved to another
    depth sits under a path no repo.yaml at the repo root ever declared.
    git records a delete and an add, not a move, so the rules dir the old
    repo.yaml declared is not read; the refusal names the old repo.yaml."""
    import subprocess
    top = tmp_path / f"s05yamlmoved-{move}"
    old = make_repo(top if move == "down" else top / "svc", 3)
    rules = old / ".warden" / "rules"
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(top, "rules")
    (rules / "gone.md").unlink()
    _commit_all(top, "retire gone")
    root = top / "svc" if move == "down" else top
    if move == "down":
        (top / "svc").mkdir()
    for name in ("repo.yaml", ".warden"):
        subprocess.run(["git", "mv", str(old / name), str(root / name)],
                       cwd=top, check=True)
    _commit_all(top, "move repo.yaml")
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    retire = _retire_artifact(bt, "gone", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    moved = "../repo.yaml" if move == "down" else "svc/repo.yaml"
    assert (f"this repo.yaml was once committed at {moved}, and rule files "
            "under the rules dir it declared there are not read"
            ) in detail, detail


def test_s05_retire_refusal_names_a_symlinked_rules_dir(tmp_path):
    """A rules dir committed as a symlink is not followed, as a symlinked rule
    file is not, so a rule deleted from its target is never read; the
    refusal says the rules dir is a symlink that is not followed."""
    root, check, bt = _s05_fixture(tmp_path, "s05symdir")
    rules = root / ".warden" / "rules"
    rules.rename(root / "shared-rules")
    rules.symlink_to(Path("..") / "shared-rules")
    (root / "shared-rules" / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(root, "symlinked rules dir")
    (root / "shared-rules" / "gone.md").unlink()
    _commit_all(root, "retire gone")
    retire = _retire_artifact(bt, "gone", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert ("the rules dir .warden/rules is committed as a symlink and is not "
            "followed") in detail, detail


def test_s05_retire_refusal_names_a_rule_file_last_committed_as_a_symlink(
        tmp_path):
    """A rule file committed as a regular file with one id, then as a symlink
    to a shared rule, then deleted, is named in the refusal although its
    earlier regular blob yielded an id: its last committed state was a
    symlink."""
    root, check, bt = _s05_fixture(tmp_path, "s05symlast")
    rules = root / ".warden" / "rules"
    (rules / "s.md").write_text(_GONE_RULE.format(id="old"))
    _commit_all(root, "regular rule")
    (root / "shared").mkdir()
    (root / "shared" / "new.md").write_text(_GONE_RULE.format(id="new"))
    (rules / "s.md").unlink()
    (rules / "s.md").symlink_to(Path("..") / ".." / "shared" / "new.md")
    _commit_all(root, "symlinked rule")
    (rules / "s.md").unlink()
    _commit_all(root, "retire new")
    retire = _retire_artifact(bt, "new", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert detail.endswith("history holds 1: .warden/rules/s.md"), detail


def test_s05_retire_refusal_counts_one_symlink_with_no_cut(tmp_path):
    """The symlink list says "and N more" only when it cuts names: one
    symlinked rule file is named alone, with no count of more after it."""
    root, check, bt = _s05_fixture(tmp_path, "s05symone")
    rules = root / ".warden" / "rules"
    (root / "shared").mkdir()
    (root / "shared" / "s.md").write_text(_GONE_RULE.format(id="s"))
    (rules / "s.md").symlink_to(Path("..") / ".." / "shared" / "s.md")
    _commit_all(root, "symlinked rule")
    (rules / "s.md").unlink()
    _commit_all(root, "retire s")
    retire = _retire_artifact(bt, "s", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert detail.endswith(
        "not followed, and history holds 1: .warden/rules/s.md"), detail


def test_s05_retire_refusal_names_a_symlink_replaced_by_a_file_with_no_id(
        tmp_path):
    """A regular file that yielded no id does not stand in for the symlink
    before it: history read nothing at that path, so the refusal names it."""
    root, check, bt = _s05_fixture(tmp_path, "s05symnoid")
    rules = root / ".warden" / "rules"
    (root / "shared").mkdir()
    (root / "shared" / "s.md").write_text(_GONE_RULE.format(id="s"))
    (rules / "s.md").symlink_to(Path("..") / ".." / "shared" / "s.md")
    _commit_all(root, "symlinked rule")
    (rules / "s.md").unlink()
    (rules / "s.md").write_text("no frontmatter\n")
    _commit_all(root, "regular file with no id")
    (rules / "s.md").unlink()
    _commit_all(root, "retire s")
    retire = _retire_artifact(bt, "s", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert detail.endswith("history holds 1: .warden/rules/s.md"), detail


def test_s05_retire_refusal_names_exactly_three_symlinks_with_no_cut(tmp_path):
    """The cut boundary: three symlinked rule files are all named, with no
    count of more after them."""
    root, check, bt = _s05_fixture(tmp_path, "s05symthree")
    rules = root / ".warden" / "rules"
    (root / "shared").mkdir()
    for name in "abc":
        (root / "shared" / f"{name}.md").write_text(_GONE_RULE.format(id=name))
        (rules / f"{name}.md").symlink_to(
            Path("..") / ".." / "shared" / f"{name}.md")
    _commit_all(root, "symlinked rules")
    for name in "abc":
        (rules / f"{name}.md").unlink()
    _commit_all(root, "retire them")
    retire = _retire_artifact(bt, "c", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert detail.endswith(
        "history holds 3: .warden/rules/a.md, .warden/rules/b.md, "
        ".warden/rules/c.md"), detail


def test_s05_retire_refuses_a_rule_only_a_sibling_projects_rules_dir_held(
        tmp_path):
    """A sibling project's rules_dir, resolved against this root, does not
    make a dir readable that no repo.yaml of this project declared: the
    retire of a rule committed only there is refused. Only this project's
    config history counts."""
    top = tmp_path / "s05sibling"
    root = make_repo(top / "a", 3)
    (top / "b").mkdir()
    (top / "b" / "repo.yaml").write_text(
        "version: 1\nrepo: b\nreview:\n  rules_dir: rules\n")
    (root / "rules").mkdir()
    (root / "rules" / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(top, "rules")
    (root / "rules" / "gone.md").unlink()
    _commit_all(top, "retire gone")
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    retire = _retire_artifact(bt, "gone", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail


def test_s05_retire_refusal_skips_a_path_holding_this_repo_yaml_again(
        tmp_path):
    """A repo.yaml path is judged by its latest committed state: a path that
    held this repo.yaml, was deleted, and holds it again is not named."""
    import shutil
    root, check, bt = _s05_fixture(tmp_path, "s05yamlreadded")
    copy = root / "svc" / "repo.yaml"
    copy.parent.mkdir()
    shutil.copy(root / "repo.yaml", copy)
    _commit_all(root, "copy repo.yaml")
    copy.unlink()
    _commit_all(root, "delete the copy")
    shutil.copy(root / "repo.yaml", copy)
    _commit_all(root, "re-add the copy")
    ghost = _retire_artifact(bt, "ghost", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and ghost.name in detail, detail
    assert "svc/repo.yaml" not in detail, detail


@pytest.mark.parametrize("layout", ["sibling", "fixture"])
def test_s05_retire_refusal_skips_a_deleted_repo_yaml_of_another_project(
        tmp_path, layout):
    """A deleted repo.yaml of another project — a sibling project's, or a
    test fixture's that declares nothing — is not named as though this
    project's rules dir had moved with it. Only a path that held this
    repo.yaml is named."""
    top = tmp_path / f"s05yamlother-{layout}"
    root = make_repo(top / "a" if layout == "sibling" else top, 3)
    other = (top / "b" / "repo.yaml" if layout == "sibling"
             else top / "tests" / "fixtures" / "x" / "repo.yaml")
    other.parent.mkdir(parents=True)
    other.write_text("version: 1\nrepo: b\nreview:\n  rules_dir: rules\n"
                     if layout == "sibling" else "not: warden\n")
    _commit_all(top, "another repo.yaml")
    other.unlink()
    _commit_all(top, "delete it")
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    ghost = _retire_artifact(bt, "ghost", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and ghost.name in detail, detail
    assert "committed at" not in detail, detail


def test_s05_retire_refusal_names_a_repo_yaml_path_a_new_project_took(
        tmp_path):
    """After repo.yaml and its rules dir move into svc/ and a new repo.yaml is
    committed at the old path, that path's latest state is an add. The old
    path still held this repo.yaml, so the refusal names it."""
    import subprocess
    top = tmp_path / "s05yamlconverted"
    make_repo(top, 3)
    rules = top / ".warden" / "rules"
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(top, "rules")
    (rules / "gone.md").unlink()
    _commit_all(top, "retire gone")
    root = top / "svc"
    root.mkdir()
    for name in ("repo.yaml", ".warden"):
        subprocess.run(["git", "mv", name, f"svc/{name}"], cwd=top,
                       check=True)
    _commit_all(top, "move the project into svc")
    (top / "repo.yaml").write_text("version: 1\nrepo: mono\n")
    _commit_all(top, "a new top-level repo.yaml")
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    retire = _retire_artifact(bt, "gone", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert ("this repo.yaml was once committed at ../repo.yaml, and rule "
            "files under the rules dir it declared there are not read"
            ) in detail, detail


@pytest.mark.parametrize("layout", ["symlink-above", "submodule"])
def test_s05_retire_refusal_names_a_symlink_or_submodule_on_the_rules_path(
        tmp_path, layout):
    """A directory above the rules dir committed as a symlink, or the rules
    dir committed as a submodule, holds no rule file git history can read,
    and the refusal names that path and why it is not followed."""
    import subprocess
    root, check, bt = _s05_fixture(tmp_path, f"s05path-{layout}")
    if layout == "symlink-above":
        (root / "shared").mkdir()
        (root / ".warden").rename(root / "shared" / ".warden")
        (root / ".warden").symlink_to(Path("shared") / ".warden")
        gone = root / "shared" / ".warden" / "rules" / "gone.md"
        gone.write_text(_GONE_RULE.format(id="gone"))
        _commit_all(root, "symlinked .warden")
        gone.unlink()
        _commit_all(root, "retire gone")
        named = (".warden, above the rules dir .warden/rules, is committed "
                 "as a symlink and is not followed")
    else:
        _commit_all(root, "rules")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              check=True, capture_output=True,
                              text=True).stdout.strip()
        for args in (["rm", "-r", "-q", "--cached", ".warden/rules"],
                     ["update-index", "--add", "--cacheinfo",
                      f"160000,{head},.warden/rules"],
                     ["commit", "-qm", "rules dir as a submodule"]):
            subprocess.run(["git", *args], cwd=root, check=True)
        named = ("the rules dir .warden/rules is committed as a submodule "
                 "and is not followed")
    retire = _retire_artifact(bt, "gone", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert named in detail, detail


def _retire_then_move_rules_dir(repo: Path, root: Path) -> tuple[bool, str]:
    """S-05 on a retire committed under .warden/rules after the rules dir
    moved to .warden/policy, where only config history shows the old dir."""
    import subprocess
    from warden.rules import rules_version
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    rules = root / ".warden" / "rules"
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(repo, "rules")
    (rules / "gone.md").unlink()
    _retire_artifact(bt, "gone", _rv(root))
    _commit_all(repo, "retire gone")
    subprocess.run(["git", "mv", ".warden/rules", ".warden/policy"], cwd=root,
                   check=True)
    config = root / "repo.yaml"
    config.write_text(config.read_text().replace(
        "rules_dir: .warden/rules", "rules_dir: .warden/policy"))
    (bt / "r-reword.json").write_text(json.dumps(
        {"rule_id": "r", "action": "reword", "judged": 12,
         "rules_version": rules_version(root / ".warden" / "policy", root)}))
    _commit_all(repo, "move the rules dir")
    return certify_mod._run_check(check, root)


def test_s05_retire_reads_config_history_with_literal_pathspecs_set(
        tmp_path, monkeypatch):
    """Config history is found with pathspec magic, which
    GIT_LITERAL_PATHSPECS=1 in the environment would turn off: with it set, a
    moved rules dir's history is still read and a valid retire passes."""
    monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    root = make_repo(tmp_path / "s05literal", 3)
    ok, detail = _retire_then_move_rules_dir(root, root)
    assert ok, detail


def test_s05_retire_reads_config_history_below_a_dir_named_with_a_space(
        tmp_path):
    """The repo root's path below the git top keeps the leading space of its
    dir name, so the committed repo.yaml matches this project's, a moved
    rules dir's history is read, and a valid retire passes."""
    top = tmp_path / "s05space"
    root = make_repo(top / " lead", 3)
    ok, detail = _retire_then_move_rules_dir(top, root)
    assert ok, detail


@pytest.mark.parametrize("layout", ["missing", "directory", "not-yaml",
                                    "not-utf8"])
def test_declared_blocking_severities_refuses_an_unreadable_repo_yaml(
        tmp_path, layout):
    """A repo.yaml that cannot be read is a named problem with no severities,
    never a crash and never an empty blocking set read as a declared one."""
    config = tmp_path / "repo.yaml"
    if layout == "directory":
        config.mkdir()
    elif layout == "not-yaml":
        config.write_text("review: [\n")
    elif layout == "not-utf8":
        config.write_bytes(b"review: {blocking_severities: [\xff]}\n")
    severities, problem = certify_mod._declared_blocking_severities(tmp_path)
    assert severities == frozenset(), (layout, severities)
    assert problem.startswith("repo.yaml cannot be read ("), (layout, problem)


@pytest.mark.parametrize("text", [
    "version: 1\n",
    "review: [HIGH]\n",
    "review: {rules_dir: .warden/rules}\n",
    "review: {blocking_severities: HIGH}\n",
    "review: {blocking_severities: [HIGH, 3]}\n",
])
def test_declared_blocking_severities_refuses_no_usable_list(tmp_path, text):
    """A readable repo.yaml whose review.blocking_severities is absent or is
    not a list of strings is a named problem with no severities."""
    (tmp_path / "repo.yaml").write_text(text)
    severities, problem = certify_mod._declared_blocking_severities(tmp_path)
    assert severities == frozenset(), (text, severities)
    assert problem == ("repo.yaml declares no usable "
                       "review.blocking_severities"), (text, problem)


def test_s05_demote_refusal_says_blocking_severities_are_unusable(tmp_path):
    """A demote is bound to a severity outside blocking_severities, so with
    none usable it is refused, and the refusal says why."""
    root, check, bt = _s05_fixture(tmp_path, "s05nosev")
    config = root / "repo.yaml"
    config.write_text(config.read_text().replace(
        "  blocking_severities: [HIGH]\n", ""))
    name = "r-demote.json"
    (bt / name).write_text(json.dumps(
        {"rule_id": "r", "action": "demote", "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and name in detail, detail
    assert "no usable review.blocking_severities" in detail, detail


@pytest.mark.parametrize("action", ["demote", "unpause", "retire"])
def test_s05_refuses_a_change_the_tree_does_not_show(tmp_path, action):
    """A current-version unpause for a rule still paused, a demote for a rule
    still at a blocking severity, and a retire of a rule the tree still
    declares are each refused, as a pause for an unpaused rule is: each is
    bound to the state the tree shows."""
    root, check, bt = _s05_fixture(tmp_path, f"s05no{action}")
    rule = root / ".warden" / "rules" / "r.md"
    if action == "demote":
        rule.write_text(rule.read_text().replace("severity: LOW",
                                                 "severity: HIGH"))
    elif action == "unpause":
        _pause(root, "r")
        # the standing pause is evidenced, so only the unpause can refuse
        (bt / "r-pause-OLD.json").write_text(json.dumps(
            {"rule_id": "r", "action": "pause", "judged": 12,
             "rules_version": "OLD"}))
    name = f"r-{action}.json"
    (bt / name).write_text(json.dumps(
        {"rule_id": "r", "action": action, "judged": 12,
         "rules_version": _rv(root)}))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and name in detail and action in detail, detail


@needs_corpus
def test_this_repos_own_s05_passes():
    """The platform gates itself at the rung CI does not run: S-05 sits in
    LEVEL 5 while CI gates `certify --level 4` only, so an artifact S-05
    refuses de-certifies this repo silently — CI green, suite green (every
    other S-05 test runs on tmp_path fixtures), badge gone. Run the real
    check against the real tree so the drop is loud."""
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    root = Path(__file__).resolve().parents[1]
    ok, detail = certify_mod._run_check(check, root)
    assert ok, f"this repo's own S-05 fails: {detail}"


def test_e02_names_the_rule_that_shadowed_the_ids_and_the_remedy(tmp_path):
    """Writing a rule for a candidate class is the retro's normal path, and the
    moment the rule exists every historical shard filed under
    `unmapped:<slug>` becomes invalid. A bare validator error against ONE
    shard shows the reader an unresolvable id, not "the rule you just wrote is
    why, and here is the declaration that fixes it". So the message must name
    the shadowing rule, count every affected shard and record, and name the
    remedy the platform already ships — and following that remedy clears E-02.
    """
    root = make_repo(tmp_path / "shadow", 3)
    for n in range(3):
        write_shard(root, f"legacy{n}",
                    [shard_record("unmapped:fail-open", seq=n * 10 + i)
                     for i in range(4)])
    e02 = {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}
    ok, detail = certify_mod._run_check(e02, root)
    assert ok, detail  # no rule answers the class yet

    # the rule ships, named for the slug exactly as the report proposed
    (root / ".warden" / "rules" / "fail-open.md").write_text(
        "---\nid: fail-open\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\n---\nbody\n")
    ok, detail = certify_mod._run_check(e02, root)
    assert not ok
    assert "unmapped:fail-open" in detail, detail
    assert "fail-open" in detail and "3 shard" in detail, \
        f"E-02 must count every affected shard, not stop at the first: {detail}"
    assert "12" in detail, f"E-02 must count the affected records: {detail}"
    assert "grandfathered_rule_ids" in detail and "certification.yaml" in detail, \
        f"E-02 must name the remedy the platform ships: {detail}"

    # the same holds when the rule answers the class through `covers:`
    (root / ".warden" / "rules" / "fail-open.md").unlink()
    (root / ".warden" / "rules" / "fail-closed.md").write_text(
        "---\nid: fail-closed\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\ncovers: [fail-open]\n---\nbody\n")
    ok, detail = certify_mod._run_check(e02, root)
    assert not ok
    assert "fail-closed" in detail and "unmapped:fail-open" in detail, detail
    assert "grandfathered_rule_ids" in detail, detail

    # declaring them clears it — the remedy the message names actually works
    (root / ".warden" / "certification.yaml").write_text(
        "version: 1\ngrandfathered_rule_ids: [unmapped:fail-open]\n")
    ok, detail = certify_mod._run_check(e02, root)
    assert ok, detail


def test_the_grandfather_waiver_promises_no_write_side_refusal(tmp_path):
    """The grandfather waiver is not a write-side floor, and no surface that
    describes it says it is.

    A grandfathered id is not always "still rejected by `attest write` for
    NEW attestations": while a covering rule is PAUSED the write seam accepts
    it, so that promise would make the waiver a one-way migration door it is
    not. The list stays E-02's single reviewed seam with no era marker, so
    what a test can hold is exactly two things: the real behaviour, and that
    each surface retracts the claim or does not make it.

    The behaviour, end to end: pause the covering rule and the write seam
    ACCEPTS the covered slug; unpause it and the seam refuses; with the slug
    waived, E-02 then absorbs the pause-era record and reports it as
    grandfathered, indistinguishable from a genuinely pre-invariant one.
    """
    from warden import attest as attest_mod

    root = make_repo(tmp_path / "pause-era", 3)
    rules_dir = root / ".warden" / "rules"
    rule = rules_dir / "fail-closed.md"
    paused_body = ("---\nid: fail-closed\nseverity: LOW\nengine: claude\n"
                   "applies_to: ['**']\ncovers: [fail-open]\n"
                   "paused: true\npaused_reason: noisy\n---\nbody\n")
    live_body = paused_body.replace("paused: true\npaused_reason: noisy\n", "")
    finding = [{"rule_id": "unmapped:fail-open", "file": "x.py"}]

    # 1. while the covering rule is PAUSED the write seam accepts the slug —
    #    this is how a pause-era record legitimately arises.
    rule.write_text(paused_body)
    attest_mod._check_rule_ids(finding, rules_dir)   # must not raise

    # 2. unpaused, the same spelling is refused. The refusal keys on the
    #    RULESET, never on certification.yaml — which is why the waiver is
    #    not a write-side floor.
    rule.write_text(live_body)
    with pytest.raises(attest_mod.AttestError):
        attest_mod._check_rule_ids(finding, rules_dir)

    # 3. the pause-era record is committed evidence by now, and on unpause
    #    E-02 absorbs it under the waiver, reported like any legacy id.
    write_shard(root, "pause_era", [shard_record("unmapped:fail-open")])
    (root / ".warden" / "certification.yaml").write_text(
        "version: 1\ngrandfathered_rule_ids: [unmapped:fail-open]\n")
    e02 = {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}
    ok, detail = certify_mod._run_check(e02, root)
    assert ok and "grandfathered" in detail, detail

    # 4. and the seam's own prose RETRACTS the promise rather than making it.
    #    The docstring necessarily quotes the promise in order to disown it,
    #    so what is checked is that the retraction is present and comes first.
    #    The remedy string is user-facing output on the E-02 failure path, so
    #    it is checked below too: it must not make the promise.
    #    Asserted POSITIVELY, over a FULLY normalized string, and both of
    #    those matter. The promise cannot be asserted ABSENT from the
    #    docstring, because retracting the promise means quoting it. And
    #    Python 3.13+ strips common leading whitespace from docstrings while
    #    3.12 does not, so a hand-rolled `.replace("\n    ", " ")` unwrap is a
    #    no-op on one and not the other. `" ".join(doc.split())` collapses
    #    every run of whitespace, so the result is identical on every
    #    interpreter — never hand-roll the unwrap.
    norm = " ".join((certify_mod._grandfathered_rule_ids.__doc__ or "").split())
    promise = "still rejected by `attest write` for NEW attestations"
    assert "WHAT THE WAIVER DOES NOT DO" in norm and "used to claim" in norm, \
        "the docstring no longer retracts the write-side promise"
    assert "never reads certification.yaml" in norm, \
        "the docstring must say WHY the waiver is not a write-side floor"
    assert "paused" in norm, \
        "the docstring must say the pause is what removes the refusal"
    # The promise may appear ONLY as the thing being retracted, so it has to
    # sit AFTER the sentence that disowns it. Residual, stated: this
    # binds the FIRST occurrence, so a second, bare re-assertion later in the
    # docstring would pass — the retraction's presence is what is pinned here,
    # not the whole prose.
    assert promise in norm, \
        "the docstring must QUOTE the promise it retracts, or a reader cannot " \
        "tell what changed"
    assert norm.index("used to claim") < norm.index(promise), \
        "the docstring states the write-side promise before retracting it"

    root2 = make_repo(tmp_path / "remedy", 3)
    write_shard(root2, "legacy", [shard_record("unmapped:fail-open")])
    (root2 / ".warden" / "rules" / "fail-open.md").write_text(
        "---\nid: fail-open\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\n---\nbody\n")
    ok, remedy = certify_mod._run_check(e02, root2)
    assert not ok
    assert "still refused by `attest write` for NEW attestations" not in remedy, \
        f"E-02's remedy still promises a refusal `attest write` no longer makes: {remedy}"
    assert "unpaused" in remedy, \
        f"E-02's remedy must state the condition the refusal depends on: {remedy}"

    # 5. and this repo's OWN overlay retracts it too. It is the file
    #    Adopting.md tells a consumer to CREATE and the worked example
    #    rule-advisor reads when drafting one, so a false promise here is the
    #    one most likely to be copied forward. Asserted with the same shape as
    #    the docstring above: the retraction is present, and the promise it
    #    quotes comes after it.
    overlay_text = (ROOT / ".warden" / "certification.yaml").read_text()
    overlay = " ".join(line.lstrip("#") for line in overlay_text.splitlines()).split()
    overlay = " ".join(overlay)
    assert "WHAT THIS LIST DOES NOT DO" in overlay and "used to claim" in overlay, \
        "the overlay comment no longer retracts the write-side promise"
    assert "never reads this file" in overlay, \
        "the overlay must say WHY the waiver is not a write-side floor"
    assert "paused" in overlay, \
        "the overlay must say the pause is what removes the refusal"
    overlay_promise = "rejected by `attest write` for new attestations"
    assert overlay_promise in overlay, \
        "the overlay must QUOTE the promise it retracts, or a reader copying " \
        "this file cannot tell what changed"
    assert overlay.index("used to claim") < overlay.index(overlay_promise), \
        "the overlay states the write-side promise before retracting it"


def test_e02_still_reports_an_ordinary_unresolvable_id_as_itself(tmp_path):
    """The shadow remedy must not swallow every other E-02 failure: an id
    that names no rule and no covered class is a typo, not a grandfather
    candidate, and telling its author to waive it would be wrong.

    The mixed corpus is the point. With only the typo present,
    `_shadow_remedy` returns "" for a reason unrelated to the claim, so that
    case alone passes against code that swallows the typo whenever ANY OTHER
    shard carries a shadowed id. Shards are read sorted, so 'aaa' raises while
    'zzz' supplies the remedy — a message naming only the shadowed class
    would name a cause that had not fired, and following it verbatim would
    leave E-02 red.
    """
    root = make_repo(tmp_path / "typo", 3)
    write_shard(root, "bad", [shard_record("ghost")])
    e02 = {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}
    ok, detail = certify_mod._run_check(e02, root)
    assert not ok and "ghost" in detail
    assert "grandfathered_rule_ids" not in detail, \
        f"a typo must not be advertised as waivable: {detail}"

    # now the corpus ALSO carries a genuinely shadowed class, in a shard that
    # sorts after the typo's. The typo is what raised; it must stay named.
    root2 = make_repo(tmp_path / "mixed", 3)
    write_shard(root2, "aaa_typo", [shard_record("ghost")])
    write_shard(root2, "zzz_legacy",
                [shard_record("unmapped:fail-open", seq=i) for i in range(4)])
    (root2 / ".warden" / "rules" / "fail-open.md").write_text(
        "---\nid: fail-open\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\n---\nbody\n")
    ok, detail = certify_mod._run_check(e02, root2)
    assert not ok
    assert "ghost" in detail, \
        f"the id that actually failed must be named, not an unrelated one: {detail}"

    # and following the message must actually clear what it describes
    (root2 / ".warden" / "certification.yaml").write_text(
        "version: 1\ngrandfathered_rule_ids: [unmapped:fail-open]\n")
    ok, detail = certify_mod._run_check(e02, root2)
    assert not ok and "ghost" in detail  # the typo is still the real failure


@pytest.mark.parametrize("n_rules", [1, 4, 8, 9, 12, 30])
def test_e02_never_truncates_the_remedy_off_its_own_message(tmp_path, n_rules):
    """The `Remedy:` clause is the one part of an E-02 failure that says what
    to do, so no length cap may cut it: a fixed cap cuts it once the ruleset
    is large enough, and eliding the middle cuts it for any ruleset whose
    remedy begins before the limit. The sample points include 4, 8 and 9,
    the band a middle elision gets wrong.
    """
    root = make_repo(tmp_path / f"trunc{n_rules}", 3)
    for i in range(n_rules):
        (root / ".warden" / "rules" / f"a-fairly-long-rule-id-{i:02d}.md").write_text(
            f"---\nid: a-fairly-long-rule-id-{i:02d}\nseverity: LOW\n"
            "engine: claude\napplies_to: ['**']\n---\nbody\n")
    write_shard(root, "bad", [shard_record("ghost")])
    ok, detail = certify_mod._run_check(
        {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}, root)
    assert not ok
    assert "ghost" in detail.split("Remedy:")[0], \
        f"the head must name the id that failed: {detail}"
    assert "unmapped:ghost" in detail, \
        f"the remedy must arrive whole and name the resolving id: {detail}"


def test_a_check_detail_arrives_byte_identical(tmp_path):
    """Byte-identity, pinned as byte-identity: the detail equals
    `<shard>.json: <validator message>` exactly. A check that no ellipsis
    appears would also pass a silent `[:160]` slice — it would certify the
    ellipsis, not the passthrough."""
    from warden import attest as attest_mod
    root = make_repo(tmp_path / "whole", 3)
    write_shard(root, "bad", [shard_record("unmapped:Not A Slug")])
    ok, detail = certify_mod._run_check(
        {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}, root)
    assert not ok
    try:
        attest_mod._check_rule_ids(
            [{"rule_id": "unmapped:Not A Slug", "file": "x.py"}],
            root / ".warden" / "rules")
    except attest_mod.AttestError as e:
        assert detail == f"bad.json: {e}", \
            f"the validator's message must arrive whole:\n{detail}\n!=\n{e}"
    else:
        raise AssertionError("fixture no longer raises")


def test_s04_detail_names_the_restatement_fold_when_one_happened(tmp_path):
    """S-04 is scored over FINDINGS, with restatements folded. On one platform
    the same corpus always gives the same n, but a platform change in how
    S-04 counts can move a rule's n and Wilson floor on a corpus that did not
    change, so a consumer bumping its platform pin can cross this rung in
    either direction. The count belongs in the detail,
    which is what reaches certification.json, or the only visible symptom is
    a level moving with no cause named."""
    root = make_repo(tmp_path / "s04fold", 3)
    s04 = {"id": "S-04", "label": "l", "type": "promotion_bar_met"}
    # 12 distinct findings in one round: over the bar, nothing folded
    write_shard(root, "dense",
                [shard_record("r", seq=i, round_n=1) for i in range(12)])
    ok, detail = certify_mod._run_check(s04, root)
    assert ok and "restatement" not in detail
    # a later round restating three of them: same corpus of judgments, a
    # smaller n — and the detail says so
    write_shard(root, "again",
                [{**shard_record("r", seq=i, round_n=1),
                  "ts": "2026-08-25T00:01:00+00:00", "sha": "b" * 40,
                  "id": f"restated-{i}"} for i in range(3)])
    ok, detail = certify_mod._run_check(s04, root)
    assert "3 restatement(s) folded" in detail
    assert "scored over findings" in detail, (
        "the detail no longer says what the bar is scored over once a fold happened")


# ---------- the cage-conflict refusal: every reason is reached ---------------
# `_cage_doubt` names why each cage in a conflict could not be ruled out. The
# layouts below are held to reach every reason it can return, so a reason
# added to the code without a layout that reaches it fails here.

def _unparseable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is = not [toml\n")
    (path.parent / "prompt.md").write_text("hand-authored\n")


def _conflict_layouts(tmp_path: Path) -> dict[str, str]:
    """Layouts that reach the cage-conflict refusal, run through `certify.run`,
    keyed by layout and valued by the detail it EMITTED.

    Not a claim to be exhaustive. What the harvest needs is every reason
    `_cage_doubt` can emit, and that is MEASURED against the code rather than
    assumed of these layouts: `_assert_layouts_reach_every_reason` reads every
    reason off `_cage_doubt`'s return statements and fails when one of them is
    reached by none of the five here. `emitted` is harvested from these five
    alone, so a reason reachable only from an un-enumerated layout fails that
    check until a layout reaching it is added here."""
    out: dict[str, str] = {}
    builds = {
        "two claimants":
            lambda t, r: (_owned_by(t / "repo-cage" / "cage.toml", r),
                          _owned_by(t / "cage" / "cage.toml", r)),
        "an unchosen sibling that will not parse":
            lambda t, r: (_no_owner(t / "repo-cage" / "cage.toml"),
                          _unparseable(t / "cage" / "cage.toml")),
        "an unchosen sibling that declares no owner":
            lambda t, r: (_no_owner(t / "repo-cage" / "cage.toml"),
                          _no_owner(t / "cage" / "cage.toml")),
        "a claimant beside a no-owner sibling":
            lambda t, r: (_owned_by(t / "repo-cage" / "cage.toml", r),
                          _no_owner(t / "cage" / "cage.toml")),
        # the chosen cage declares a DIFFERENT checkout: the one layout that
        # reaches the "(declares another checkout)" reason
        "a foreign-owner sibling beside a no-owner one":
            lambda t, r: (_owned_by(t / "repo-cage" / "cage.toml",
                                    t / "some-other-repo"),
                          _no_owner(t / "cage" / "cage.toml")),
    }
    for name, build in builds.items():
        home = tmp_path / name.replace(" ", "-")
        home.mkdir(parents=True)
        root = make_repo(home / "repo", 1)
        build(home, root)
        badge = certify_mod.run(root)["capabilities"]["autonomous"]
        assert badge["earned"] is False, f"{name} should refuse the badge"
        details = {c["detail"] for c in badge["checks"]}
        assert len(details) == 1, f"{name}: the cage checks disagree"
        out[name] = details.pop()
    return out


def test_a_chosen_cage_declaring_another_checkout_is_labelled_so(tmp_path):
    """`(declares this checkout)` is a tested label, not a fallback for any
    cage neither unreadable nor owner-less. Repo at /repo,
    /repo-cage/cage.toml declaring live_checkout = some-other-repo,
    /cage/cage.toml declaring no owner: no claimant exists, so `_cage_config`
    chooses repo-cage/ by convention order and cage/ is unruled. The conflict
    refusal is the one operator-facing message for that layout — the
    owner-mismatch refusal downstream never runs because the conflict arm
    returns first — so it labels repo-cage/ as declaring another checkout,
    and a cage whose owner is this root keeps `(declares this checkout)`."""
    root = make_repo(tmp_path / "repo", 1)
    _owned_by(tmp_path / "repo-cage" / "cage.toml", tmp_path / "some-other-repo")
    _no_owner(tmp_path / "cage" / "cage.toml")
    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    assert badge["earned"] is False
    details = {c["detail"] for c in badge["checks"]}
    assert len(details) == 1, details
    detail = details.pop()
    assert "repo-cage/ (declares another checkout)" in detail, detail
    assert "(declares this checkout)" not in detail, (
        "the chosen cage declares some-other-repo and is still labelled as "
        f"declaring this one: {detail}")
    assert "cage/ (declares no owner)" in detail, detail
    # and a cage that DOES declare this checkout keeps its label — the
    # another-checkout arm does not swallow the this-checkout one
    _owned_by(tmp_path / "repo-cage" / "cage.toml", root)
    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    detail = {c["detail"] for c in badge["checks"]}.pop()
    assert "repo-cage/ (declares this checkout)" in detail, detail


def _doubt_reasons(source: str) -> set[str]:
    """Every reason `_cage_doubt` can RETURN, read off the `return` statements
    of its source — the one surface a new reason is added to. Fails closed on
    an arm whose return is not a parenthesised string literal: a reason the
    harvest cannot read is not one it may skip."""
    reasons: set[str] = set()
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if not isinstance(node, ast.Return):
            continue
        value = node.value
        assert isinstance(value, ast.Constant) and isinstance(value.value, str), (
            "a `_cage_doubt` arm returns something other than a string "
            f"literal ({ast.dump(value) if value else None}); the harvest "
            "cannot read the reason off it")
        match = re.fullmatch(r"\s*\((.+)\)\s*", value.value)
        assert match, (f"a `_cage_doubt` arm returns {value.value!r}, not a "
                       "parenthesised reason")
        reasons.add(match.group(1))
    assert reasons, "no return statement found in `_cage_doubt`"
    return reasons


def _assert_layouts_reach_every_reason(source: str, emitted: set[str]) -> None:
    """The layouts `_conflict_layouts` enumerates reach every reason
    `_cage_doubt` can return, and nothing else — so a reason added to the
    code that no layout reaches fails HERE, not silently at the page."""
    declared = _doubt_reasons(source)
    assert declared == emitted, (
        f"`_cage_doubt` can return {sorted(declared - emitted)} and no "
        "enumerated layout reaches it (or a layout emits "
        f"{sorted(emitted - declared)}, which no arm returns). Add the layout "
        "to `_conflict_layouts`, or the arm is dead — either way the page is "
        "being held to a list that is not certify's")


def test_a_reason_no_enumerated_layout_reaches_is_caught(tmp_path):
    """A `_cage_doubt` reason no enumerated layout reaches fails the harvest
    check. The mutation adds a reason reachable only when the CHOSEN cage is
    the unreadable one — a layout the five enumerated ones do not include —
    and the check must name it. An arm whose return the harvest cannot read
    is refused too."""
    source = inspect.getsource(certify_mod._cage_doubt)
    details = _conflict_layouts(tmp_path)
    emitted = set()
    for detail in details.values():
        emitted.update(re.findall(r"\(([^)]+)\)", detail))
    _assert_layouts_reach_every_reason(source, emitted)
    tails = {d.split("— ", 1)[1] for d in details.values()}
    assert len(tails) == 1, f"the refusals do not share one tail: {tails}"
    arm = "    if cage in unreadable:\n"
    assert source.count(arm) == 1, "the arm this mutation extends moved"
    mutated = source.replace(arm, (
        '    if cage in unreadable and cage.parent.name == "repo-cage":\n'
        '        return " (no hand-authored prompt)"\n' + arm))
    with pytest.raises(AssertionError, match="no hand-authored prompt"):
        _assert_layouts_reach_every_reason(mutated, emitted)
    # ...and an arm the harvest cannot read is a refusal, not a skip.
    with pytest.raises(AssertionError, match="cannot read the reason"):
        _doubt_reasons(source.replace(arm, arm + "        return reason\n"))




def _s05_rules_at(tmp_path: Path, name: str, rules_dir: str):
    """An S-05 fixture whose rules dir is `rules_dir`, uncommitted."""
    root, check, bt = _s05_fixture(tmp_path, name)
    config = root / "repo.yaml"
    config.write_text(config.read_text().replace(
        "rules_dir: .warden/rules", f"rules_dir: {rules_dir}"))
    return root, check, bt


@pytest.mark.parametrize("kind", ["symlink", "submodule"])
@pytest.mark.parametrize("link, rules_dir", [
    ("config", "config/warden/rules"),
    ("config/warden", "config/warden/rules"),
    ("config/warden", "config/warden/team/rules")])
def test_s05_retire_refusal_names_a_link_at_any_ancestor(
        tmp_path, kind, link, rules_dir):
    """Named regression: each ancestor of the rules dir was read with an
    exclude for everything under it, and git drops a path matching any
    exclude, so the exclude for config/ hid a symlink or submodule at
    config/warden, and an exclude for config/ also hid a submodule at config.
    The refusal named nothing."""
    import shutil
    import subprocess
    from warden.rules import rules_version
    root, check, bt = _s05_rules_at(
        tmp_path, f"s05deep-{kind}-{link.count('/')}-{rules_dir.count('/')}",
        rules_dir)
    if kind == "symlink":
        target = root / "shared" / link / rules_dir.removeprefix(link + "/")
        target.mkdir(parents=True)
        shutil.move(root / ".warden" / "rules" / "r.md", target / "r.md")
        (root / link).parent.mkdir(parents=True, exist_ok=True)
        (root / link).symlink_to(
            Path(*[".."] * link.count("/")) / "shared" / link)
        _commit_all(root, "symlinked ancestor")
    else:
        (root / rules_dir).mkdir(parents=True)
        shutil.move(root / ".warden" / "rules" / "r.md", root / rules_dir / "r.md")
        _commit_all(root, "rules")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              check=True, capture_output=True,
                              text=True).stdout.strip()
        for args in (["rm", "-r", "-q", "--cached", link],
                     ["update-index", "--add", "--cacheinfo",
                      f"160000,{head},{link}"],
                     ["commit", "-qm", "ancestor as a submodule"]):
            subprocess.run(["git", *args], cwd=root, check=True)
    retire = _retire_artifact(bt, "gone", rules_version(root / rules_dir, root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert (f"{link}, above the rules dir {rules_dir}, is committed as "
            f"a {kind} and is not followed") in detail, detail


@pytest.mark.parametrize("then", ["edited", "edited-then-deleted"])
@pytest.mark.parametrize("layout", ["template-sibling", "copied-fixture"])
def test_s05_retire_refusal_skips_an_edited_copy_of_this_repo_yaml(
        tmp_path, layout, then):
    """Named regression: a path was named as this repo.yaml's old path when it
    once held a version identical to this repo.yaml, so a sibling project
    started from the same template repo.yaml, or a fixture copied from this
    one, and later edited, was named as though this project had moved."""
    import shutil
    top = tmp_path / f"s05copy-{layout}-{then}"
    if layout == "template-sibling":
        root = make_repo(top / "a", 3)
        copy = top / "b" / "repo.yaml"
        copy.parent.mkdir(parents=True)
        shutil.copy(root / "repo.yaml", copy)
        _commit_all(top, "two projects from one template")
    else:
        root = make_repo(top, 3)
        _commit_all(top, "project")
        copy = top / "tests" / "fixtures" / "x" / "repo.yaml"
        copy.parent.mkdir(parents=True)
        shutil.copy(root / "repo.yaml", copy)
        _commit_all(top, "fixture copied from repo.yaml")
    copy.write_text(copy.read_text().replace("repo: sim", "repo: other"))
    _commit_all(top, "edit the copy")
    if then == "edited-then-deleted":
        copy.unlink()
        _commit_all(top, "delete the copy")
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    ghost = _retire_artifact(bt, "ghost", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and ghost.name in detail, detail
    assert "committed at" not in detail, detail


def test_s05_retire_refusal_names_a_git_mv_of_this_repo_yaml_edited_later(
        tmp_path):
    """A git mv of repo.yaml and its rules dir, with repo.yaml edited in a
    later commit, is still named: the move commit took the version away from
    the old path and wrote the same version here."""
    import subprocess
    top = tmp_path / "s05mvedited"
    old = make_repo(top / "old", 3)
    rules = old / ".warden" / "rules"
    (rules / "gone.md").write_text(_GONE_RULE.format(id="gone"))
    _commit_all(top, "rules")
    (rules / "gone.md").unlink()
    _commit_all(top, "retire gone")
    subprocess.run(["git", "mv", "old", "svc"], cwd=top, check=True)
    _commit_all(top, "move the project")
    root = top / "svc"
    config = root / "repo.yaml"
    config.write_text(config.read_text().replace("repo: sim", "repo: svc"))
    _commit_all(top, "edit repo.yaml after the move")
    check = {"id": "S-05", "label": "l", "type": "rule_change_backtested"}
    bt = root / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    retire = _retire_artifact(bt, "gone", _rv(root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert ("this repo.yaml was once committed at ../old/repo.yaml, and rule "
            "files under the rules dir it declared there are not read"
            ) in detail, detail


def test_s05_retire_refusal_escapes_glob_characters_in_an_ancestor_exclude(
        tmp_path):
    """Named regression: an ancestor's exclude is a glob, and a directory
    named `**` unescaped gives `**/**`, which excludes every path, so a
    symlink at x, read in the same log, went unnamed."""
    import shutil
    from warden.rules import rules_version
    root, check, bt = _s05_rules_at(tmp_path, "s05globdir", "x/rules")
    target = root / "shared" / "x" / "rules"
    target.mkdir(parents=True)
    shutil.move(root / ".warden" / "rules" / "r.md", target / "r.md")
    (root / "x").symlink_to(Path("shared") / "x")
    _commit_all(root, "rules dir under a symlink")
    config = root / "repo.yaml"
    config.write_text(config.read_text().replace(
        "rules_dir: x/rules", "rules_dir: '**/rules'"))
    (root / "**" / "rules").mkdir(parents=True)
    shutil.copy(target / "r.md", root / "**" / "rules" / "r.md")
    _commit_all(root, "rules dir under a directory named **")
    retire = _retire_artifact(
        bt, "gone", rules_version(root / "**" / "rules", root))
    ok, detail = certify_mod._run_check(check, root)
    assert not ok and retire.name in detail, detail
    assert ("x, above the rules dir x/rules, is committed as a symlink and is "
            "not followed") in detail, detail


# ── R-14: a declared light round needs its CI enforcement ──────────────────

def _r14_repo(tmp_path: Path, *, light: bool, workflow: str) -> Path:
    from warden import enroll
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    langs = tuple(lang for lang in enroll.LANGUAGES if lang.name == "python")
    text = enroll.render_workflow(
        langs, pin="v9.0.0")  # a release that carries the step
    if workflow == "without":
        doc = yaml.safe_load(text)
        steps = doc["jobs"]["gate"]["steps"]
        doc["jobs"]["gate"]["steps"] = [
            s for s in steps if s.get("name") != "proportionate review tier"]
        assert len(doc["jobs"]["gate"]["steps"]) == len(steps) - 1
        text = yaml.safe_dump(doc, sort_keys=False)
    (tmp_path / ".github" / "workflows" / "warden.yml").write_text(text)
    (tmp_path / "repo.yaml").write_text(enroll.render_repo_yaml(tmp_path, langs))
    graph = {
        "version": 1,
        "nodes": {n: {"kind": k, "impl": "x", "authority": "find"}
                  for n, k in (("builder", "skill"), ("code-reviewer", "subagent"),
                               ("claims-auditor", "subagent"))}
        | {"founder": {"kind": "human", "authority": "merge"}},
        "edges": [{"from": "builder", "to": "code-reviewer", "payload": "diff"},
                  {"from": "builder", "to": "claims-auditor", "payload": "diff"},
                  {"from": "code-reviewer", "to": "founder", "payload": "pr"}],
        "review": {"rounds": [{"round": r, "roles": ["code-reviewer"]}
                              for r in (1, 2)],
                   "cap": 2, "merge_authority": "founder",
                   "lenses": [{"id": "consumer-blast-radius",
                               "checklist": ["a shipped schema changed?"]}]},
    }
    if light:
        graph["review"]["light"] = {"roles": ["claims-auditor"],
                                    "payload": "claims-only"}
    (tmp_path / "graph.yaml").write_text(yaml.safe_dump(graph, sort_keys=False))
    return tmp_path


def _r14() -> dict:
    ladder = certify_mod.load(ROOT)
    [check] = [c for c in ladder["levels"][3]["checks"] if c["id"] == "R-14"]
    return check


def test_r14_reads_the_classify_step_from_the_workflow_init_emits(tmp_path):
    """A declared light round and the gate init writes certify R-14."""
    check = _r14()
    assert check["type"] == "light_round_enforced"
    assert (check["pattern"], check["flags"]) == ("warden attest classify",
                                                  ["--enforce"])
    ok, detail = certify_mod._run_check(
        check, _r14_repo(tmp_path, light=True, workflow="emitted"))
    assert ok, detail
    assert "claims-auditor" in detail and "runs enforced" in detail


def test_r14_a_light_round_with_no_classify_step_fails(tmp_path):
    """The fail-open this closes: a light round with no step fails R-14."""
    ok, detail = certify_mod._run_check(
        _r14(), _r14_repo(tmp_path, light=True, workflow="without"))
    assert not ok
    assert "no workflow step runs 'warden attest classify --enforce'" in detail


@pytest.mark.parametrize("run, ok", [
    ('warden attest classify --base "origin/main" --enforce', True),
    ('warden attest classify \\\n  --base origin/main --enforce > "$out"', True),
    ('warden attest classify --base origin/main', False),
    ('warden attest classify --base origin/main\necho --enforce', False),
    ('warden attest classify --base origin/main; echo --enforce', False),
    ('warden attest classify --base origin/main && echo --enforce', False),
    ('warden attest classify --base origin/main &&\n  echo --enforce', False),
    ('warden attest classify --base origin/main  # TODO --enforce', False),
    ('warden attest classify --base "origin/main --enforce"', False),
    ('warden attest classify --base $(git merge-base origin/main HEAD) '
     '--enforce', True),
    ('warden attest classify --base $(echo $(git rev-parse x)) --enforce', True),
    ('warden attest classify --base `git merge-base a b` --enforce', True),
    ('warden attest classify --base $(git merge-base a b --enforce', False),
    ("warden attest classify --base '$(x'; echo ')' --enforce", False),
    ("warden attest classify --base '`'; echo '`' --enforce", False),
    ('warden attest classify --base "$(git rev-parse x)" --enforce', True),
])
def test_r14_reads_enforce_as_a_flag_in_any_order(tmp_path, run, ok):
    """`--enforce` is the classify command's own word, in any order: not on
    another line, after `;`/`&&`, in a comment or quotes; `$(...)` is one
    word (#372 rounds 1-2, rebuild round 1)."""
    root = _r14_repo(tmp_path, light=True, workflow="without")
    wf = root / ".github" / "workflows" / "warden.yml"
    doc = yaml.safe_load(wf.read_text())
    doc["jobs"]["gate"]["steps"].append({"name": "tier", "run": run})
    wf.write_text(yaml.safe_dump(doc, sort_keys=False))
    got, detail = certify_mod._run_check(_r14(), root)
    assert got is ok, detail


def test_r14_passes_with_no_light_round_and_fails_on_an_unreadable_graph(
        tmp_path):
    """No light round is nothing to bind; an unresolvable graph is not 'none'."""
    root = _r14_repo(tmp_path, light=False, workflow="without")
    ok, detail = certify_mod._run_check(_r14(), root)
    assert ok and "declares no light round" in detail, detail
    (root / "graph.yaml").write_text("version: 1\nreview: [not, a, map]\n")
    ok, detail = certify_mod._run_check(_r14(), root)
    assert not ok and "cannot be resolved" in detail, detail
