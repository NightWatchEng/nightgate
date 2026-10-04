"""The terminal closure round.

`review.cap` used to be two things at once: the budget for REPAIR rounds, and
the ability to attest a final head. A repair commit answering a finding raised
at the cap seals that round with its findings still open, the cap refuses
another round, and `round_binding` refuses re-pointing the sealed round at the
new head — so the branch had NO attestable head at all and its required gate
was red forever (PR #266 is the live reproduction).

The closure round is the terminal, non-budgeted round minted one past the cap
that gives that head something to attest. The ONLY question worth asking about
it is whether it is provably unable to raise anything new — otherwise it is a
third review round with a fig leaf, and the cap is a fiction. So most of this
module is that proof, in both directions: a payload that files what no capped
round filed is refused, and a payload silent on a finding still confirmed at
the cap is refused too.
"""

import inspect
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import REPO_YAML, seed_rules
from warden import attest as attest_mod
from warden import cli
from warden import graph as graph_mod
from warden import memory as memory_mod
from warden import runs as runs_mod

CLOSURE_ROLE = "closure-attestor"


# --- a branch that repaired a round-2 finding ----------------------------------


def _repo_yaml() -> str:
    """The sample repo.yaml with an explicit `repair.budget`, which is the
    cap's one source — `resolve_review` refuses a cap that disagrees."""
    policy = yaml.safe_load(REPO_YAML)
    policy["repair"] = {"budget": 2}
    return yaml.safe_dump(policy)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                           *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()


def _shard(head: str, base: str, rounds: list[tuple[int, list[dict]]]) -> dict:
    """One committed attestation shard, in the shape `memory ingest` writes:
    the review event on the envelope, the findings as `records`."""
    roster, records = [], []
    for rnd, findings in rounds:
        roster.append({"role": "code-reviewer", "agent": "subagent",
                       "round": rnd, "returned": True,
                       "findings": len(findings),
                       "output": "code-reviewer.json"})
        for finding in findings:
            records.append({"source": "attest", "sha": head, "base_sha": base,
                            "round": rnd, "lens": "code-reviewer", **finding})
    return {"schema": 1, "source": "attest", "sha": head, "base_sha": base,
            "rules_version": "v1", "reviewed_at": "2026-09-21T00:00:00+00:00",
            "verdict": "findings-open", "reviewers": roster,
            "roster_verification": "verified", "round_binding": "bound",
            "records": records}


def _finding(rule_id: str, file: str, status: str, **extra) -> dict:
    out = {"rule_id": rule_id, "severity": "MEDIUM", "file": file, "line": 1,
           "finding": "f", "evidence": "e", "status": status}
    if status in ("dismissed-with-reason", "refuted"):
        out["reason"] = "r"
    return {**out, **extra}


@pytest.fixture
def branch(tmp_path):
    """A repo whose branch ran rounds 1 and 2, committed both shards, and then
    REPAIRED a round-2 finding — the exact state that had no attestable head.

    Round 1 raised `docs-drift` on `a.py` and `stale-guard` on `b.py`; round 2
    recorded `docs-drift` fixed, re-confirmed `stale-guard`, and raised
    `tests-bite` on `c.py`. Two repair commits then answered the two that were
    still open, so the head the closure round must attest is two commits past
    round 2's sealed head.
    """
    root = tmp_path / "repo"
    root.mkdir()
    seed_rules(root, "docs-drift", "stale-guard", "tests-bite", "scope-creep")
    (root / "repo.yaml").write_text(_repo_yaml())
    shards = root / ".warden" / "memory" / "attest"
    shards.mkdir(parents=True)
    (root / "a.py").write_text("1\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")

    (root / "b.py").write_text("1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "work")
    head1 = _git(root, "rev-parse", "HEAD")
    (shards / "r1.json").write_text(json.dumps(_shard(head1, base, [(1, [
        _finding("docs-drift", "a.py", "confirmed"),
        _finding("stale-guard", "b.py", "confirmed")])]), indent=2))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "attest round 1")

    (root / "a.py").write_text("2\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "repair round 1")
    head2 = _git(root, "rev-parse", "HEAD")
    (shards / "r2.json").write_text(json.dumps(_shard(head2, base, [(2, [
        _finding("docs-drift", "a.py", "fixed"),
        _finding("stale-guard", "b.py", "confirmed"),
        _finding("tests-bite", "c.py", "confirmed")])]), indent=2))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "attest round 2")

    (root / "b.py").write_text("2\n")
    (root / "c.py").write_text("1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "repair round 2 — the commit that stranded")
    return root, base, _git(root, "rev-parse", "HEAD")


def _crew(closure: dict | None = None) -> dict:
    """A synthetic crew, hardcoded rather than read from a graph.yaml,
    carrying only the three keys of `graph.declared_crew`'s reading this
    file exercises — `cap`, `rounds` and `closure` — with or without closure
    terms. The other four keys that reading carries (`lenses`,
    `merge_authority`, `light`, `delegation`) are not built."""
    return {"cap": 2,
            "rounds": {1: ["code-reviewer", "cross-examiner"],
                       2: ["scoped-re-reviewer"]},
            "closure": closure}


def _closure_terms() -> dict:
    return {"min_round": 3, "roles": [CLOSURE_ROLE],
            "payload": "prior-findings-only"}


def _round_dir(root: Path, head: str, rnd: int, roles: list[str]) -> Path:
    d = root / ".warden" / "out" / "rounds" / f"round-{rnd}"
    reviewers = d / "reviewers"
    reviewers.mkdir(parents=True)
    for role in roles:
        (reviewers / f"{role}.json").write_text(
            json.dumps({"role": role, "round": rnd, "head": head}))
    runs_mod.write_round_manifest(d, base="main", base_sha="a" * 40, head=head,
                                  branch="work", round_no=rnd)
    return reviewers


def _closure_payload(findings: list[dict], *, role: str = CLOSURE_ROLE,
                     rnd: int = 3, verdict: str = "clean") -> dict:
    return {
        "reviewers": [{"role": role, "agent": "subagent", "round": rnd,
                       "returned": True, "findings": len(findings),
                       "output": f"{role}.json"}],
        "findings": [{**f, "lens": role, "round": rnd} for f in findings],
        "verdict": verdict,
    }


def _repaired() -> list[dict]:
    """The two findings still confirmed at the cap, answered as repaired."""
    return [_finding("stale-guard", "b.py", "fixed"),
            _finding("tests-bite", "c.py", "fixed")]


def _build(root: Path, base: str, head: str, payload: dict, review_dir: Path,
           crew: dict) -> dict:
    return attest_mod.build(payload, head_sha=head, base_sha=base,
                            rules_version="v1",
                            rules_dir=root / ".warden" / "rules",
                            review_dir=review_dir, review_crew=crew, root=root)


# --- the acceptance: a repaired capped round reaches an attested head ----------


def test_a_repaired_capped_round_attests_its_head_through_the_closure_round(
        branch):
    """The whole point: a branch that REPAIRED
    a round-2 finding attests its final head, without a third review round and
    without exceeding the repair budget."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    doc = _build(root, base, head, _closure_payload(_repaired()), rd,
                 _crew(_closure_terms()))
    assert doc["verdict"] == "clean"
    assert doc["closure_verification"] == attest_mod.CLOSURE_VERIFIED
    assert [r["round"] for r in doc["reviewers"]] == [3]


def test_the_same_branch_has_no_attestable_head_without_the_closure_round(
        branch):
    """The defect, pinned: with no closure declared, round 3 is refused for
    being past the cap — which is the state PR #266 reproduced live, and the
    reason this feature exists rather than a cap bump."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    with pytest.raises(attest_mod.AttestError, match="past the cap of 2"):
        _build(root, base, head, _closure_payload(_repaired()), rd, _crew())


# --- the proof it cannot find --------------------------------------------------


def test_a_record_no_capped_round_filed_is_refused(branch):
    """A closure round that raises something new
    is refused, and the message names the record — this is the check that
    makes the round terminal rather than round 3 with a fig leaf."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    payload = _closure_payload(
        _repaired() + [_finding("scope-creep", "d.py", "confirmed")],
        verdict="findings-open")
    with pytest.raises(attest_mod.AttestError,
                       match="whose claim no round within the cap of 2 filed") as exc:
        _build(root, base, head, payload, rd, _crew(_closure_terms()))
    assert "'scope-creep'" in str(exc.value) and "'d.py'" in str(exc.value)


def test_a_known_rule_id_on_an_unraised_file_is_refused(branch):
    """The key is the PAIR. Re-using a rule id a capped round did raise, on a
    file it did not, is the cheapest way to dress a new finding as a re-file
    — `stale-guard` was raised, but never on `d.py`."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    payload = _closure_payload(
        _repaired() + [_finding("stale-guard", "d.py", "fixed")])
    with pytest.raises(attest_mod.AttestError, match="'stale-guard'"):
        _build(root, base, head, payload, rd, _crew(_closure_terms()))


def test_a_known_file_under_an_unraised_rule_is_refused(branch):
    """The pair, from the other side: `b.py` was raised, but never under
    `scope-creep`."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    payload = _closure_payload(
        _repaired() + [_finding("scope-creep", "b.py", "fixed")])
    with pytest.raises(attest_mod.AttestError, match="'scope-creep'"):
        _build(root, base, head, payload, rd, _crew(_closure_terms()))


def test_merging_the_base_branch_does_not_widen_what_may_be_filed(branch):
    """The one cheap widening this check would otherwise have: merge the base
    branch and every shard the platform ever committed becomes "added between
    base and head", so its whole corpus of rule_id+file pairs becomes
    admissible. Merged-in commits are SECOND parents, so the first-parent walk
    drops them — and the finding only main's history raised stays refused."""
    root, base, head = branch
    shards = root / ".warden" / "memory" / "attest"
    _git(root, "checkout", "-q", "-b", "other", base)
    (root / "d.py").write_text("1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "another change")
    other_head = _git(root, "rev-parse", "HEAD")
    shards.mkdir(parents=True, exist_ok=True)
    (shards / "elsewhere.json").write_text(json.dumps(_shard(
        other_head, base,
        [(1, [_finding("scope-creep", "d.py", "confirmed")])]), indent=2))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "another change's review")
    other = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "-m", "merge other", other)
    head = _git(root, "rev-parse", "HEAD")
    assert (shards / "elsewhere.json").exists(), "the merge did bring it in"
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    payload = _closure_payload(
        _repaired() + [_finding("scope-creep", "d.py", "fixed")])
    with pytest.raises(attest_mod.AttestError, match="'scope-creep'"):
        _build(root, base, head, payload, rd, _crew(_closure_terms()))


# --- the proof it cannot drop --------------------------------------------------


def test_a_finding_still_confirmed_at_the_cap_cannot_be_dropped(branch):
    """The mirror of the refusal above, and the half that keeps the round
    honest rather than merely narrow: an EMPTY closure payload raises nothing
    new and would otherwise attest `clean` over a finding still open at the
    cap."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    with pytest.raises(attest_mod.AttestError, match="silent on 2") as exc:
        _build(root, base, head, _closure_payload([]), rd,
               _crew(_closure_terms()))
    assert "'stale-guard'" in str(exc.value) and "'tests-bite'" in str(exc.value)


def test_a_finding_the_capped_rounds_closed_need_not_be_re_filed(branch):
    """`docs-drift` was raised at round 1 and recorded FIXED at round 2, so the
    closure round owes it nothing — open means confirmed at the highest round
    that spoke to the key, not raised at any point."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    doc = _build(root, base, head, _closure_payload(_repaired()), rd,
                 _crew(_closure_terms()))
    assert [f["rule_id"] for f in doc["findings"]] == ["stale-guard",
                                                       "tests-bite"]


def test_an_open_finding_may_be_dismissed_rather_than_fixed(branch):
    """Speaking to an open finding is what is required, not fixing it: the
    declared disposition at the cap is a reason naming a tracked item."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    doc = _build(root, base, head, _closure_payload(
        [_finding("stale-guard", "b.py", "fixed"),
         _finding("tests-bite", "c.py", "dismissed-with-reason")]), rd,
        _crew(_closure_terms()))
    assert doc["verdict"] == "clean"


def test_a_capped_round_that_committed_no_shard_refuses_the_closure(
        tmp_path):
    """The closure round's licence is "the capped rounds already raised this",
    so a round that never attested leaves it nothing it is entitled to re-file
    — and leaving the inconvenient round uncommitted is the cheapest
    laundering there is, because its open findings would vanish from what the
    closure round must answer for."""
    root = tmp_path / "repo"
    root.mkdir()
    seed_rules(root, "docs-drift")
    (root / "repo.yaml").write_text(_repo_yaml())
    shards = root / ".warden" / "memory" / "attest"
    shards.mkdir(parents=True)
    (root / "a.py").write_text("1\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "a.py").write_text("2\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "work")
    head1 = _git(root, "rev-parse", "HEAD")
    (shards / "r1.json").write_text(json.dumps(_shard(head1, base, [(1, [
        _finding("docs-drift", "a.py", "confirmed")])]), indent=2))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "attest round 1 only")
    head = _git(root, "rev-parse", "HEAD")
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    with pytest.raises(attest_mod.AttestError, match="round.s. 2 of the cap"):
        _build(root, base, head, _closure_payload(
            [_finding("docs-drift", "a.py", "fixed")]), rd,
            _crew(_closure_terms()))


# --- the roster half -----------------------------------------------------------


def test_the_closure_round_takes_only_its_declared_role(branch):
    """A closure round dispatching the review crew is the extra review budget
    the cap denies, in its most plausible dress."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, ["code-reviewer"])
    payload = _closure_payload(_repaired(), role="code-reviewer")
    with pytest.raises(attest_mod.AttestError,
                       match="not the crew graph.yaml declares"):
        _build(root, base, head, payload, rd, _crew(_closure_terms()))


def test_the_closure_round_is_reachable_at_cap_plus_two(branch):
    """The number is NOT pinned to cap+1, and this is the case that proves why
    it must not be: the cap's refusal fires at `attest write`, after `warden
    round new` has already created the directory, so every builder who
    discovers this trap the way PR #266 did is holding a
    stray empty round at cap+1 and their closure round is numbered cap+2.
    Pinning the number would fail exactly the branches the round rescues."""
    root, base, head = branch
    _round_dir(root, head, 3, [CLOSURE_ROLE])      # the stray from the refusal
    rd = _round_dir(root, head, 4, [CLOSURE_ROLE])
    doc = _build(root, base, head, _closure_payload(_repaired(), rnd=4), rd,
                 _crew(_closure_terms()))
    assert doc["closure_verification"] == attest_mod.CLOSURE_VERIFIED


def test_a_closure_round_at_cap_plus_two_still_cannot_raise(branch):
    """Relaxing the NUMBER relaxes nothing else: the payload rule binds every
    round past the cap, whichever number it carries."""
    root, base, head = branch
    _round_dir(root, head, 3, [CLOSURE_ROLE])
    rd = _round_dir(root, head, 4, [CLOSURE_ROLE])
    payload = _closure_payload(
        _repaired() + [_finding("scope-creep", "d.py", "fixed")], rnd=4)
    with pytest.raises(attest_mod.AttestError,
                       match="whose claim no round within the cap of 2 filed"):
        _build(root, base, head, payload, rd, _crew(_closure_terms()))


def test_a_repo_declaring_no_closure_is_unchanged(branch):
    """Additive: a graph with no `closure` key behaves exactly as before, and
    a crew dict that predates the key (no `closure` at all) is read the same
    way — which is what keeps a pinned consumer's graph.yaml working."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    legacy = {"cap": 2, "rounds": {1: ["code-reviewer", "cross-examiner"],
                                   2: ["scoped-re-reviewer"]}}
    with pytest.raises(attest_mod.AttestError, match="past the cap of 2"):
        _build(root, base, head, _closure_payload(_repaired()), rd, legacy)


def test_a_capped_round_is_not_checked_as_a_closure_round(branch):
    """The payload rule binds the closure round ALONE. A round within the cap
    raises what it finds, so applying it there would refuse every honest
    round-2 dispatch that found something new.

    `scope-creep` on `d.py` is exactly that: a claim NO capped round filed,
    arriving `confirmed`. The closure rule would refuse it; round 2 must not.
    Filed beside a `fixed` record that re-files round 1's `docs-drift` claim,
    which is what an honest round 2 carries and what the chain rule
    does hold it to — the two rules are distinguished here in
    one payload, so neither can quietly grow into the other.
    """
    root, base, head = branch
    rd = _round_dir(root, head, 2, ["scoped-re-reviewer"])
    payload = _closure_payload(
        [_finding("scope-creep", "d.py", "confirmed"),
         _finding("docs-drift", "a.py", "fixed")],
        role="scoped-re-reviewer", rnd=2, verdict="findings-open")
    doc = _build(root, base, head, payload, rd, _crew(_closure_terms()))
    assert "closure_verification" not in doc


# --- provenance ----------------------------------------------------------------


def test_the_closure_state_rides_into_the_committed_shard(branch):
    """A check whose result stops at stderr is a check the corpus cannot be
    asked about. `closure_verification` is stamped into the artifact and
    carried across the ingest boundary, exactly as its two siblings are."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    doc = _build(root, base, head, _closure_payload(_repaired()), rd,
                 _crew(_closure_terms()))
    event = memory_mod.build_review_event(doc)
    assert event["closure_verification"] == attest_mod.CLOSURE_VERIFIED


def test_the_closure_state_is_stamped_never_accepted_from_the_payload(
        branch):
    """A caller cannot hand warden its own verdict on itself — the same guard
    `roster_verification` and `round_binding` sit behind."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    payload = {**_closure_payload(_repaired()),
               "closure_verification": "verified"}
    with pytest.raises(attest_mod.AttestError, match="extra keys"):
        _build(root, base, head, payload, rd, _crew(_closure_terms()))


def test_a_non_closure_shard_carries_no_closure_state():
    """Absent means "not a closure round, or written before the check existed"
    — never "checked and found fine". No reader may upgrade it, and the
    envelope builder only carries a value the artifact states."""
    assert "closure_verification" not in memory_mod.build_review_event(
        {"verdict": "clean", "reviewers": []})
    assert "closure_verification" not in memory_mod.build_review_event(
        {"verdict": "clean", "reviewers": [], "closure_verification": "yes"})


def test_the_closure_states_are_read_from_the_schema():
    """One definition, in the contract consumers pin: `memory ingest` never
    calls `build`, so a hand-copied tuple would drift the moment the enum
    grew and the state would silently stop riding into shards."""
    schema = json.loads(
        (Path(attest_mod.__file__).parent / "schemas"
         / "attestation.schema.json").read_text())
    assert set(attest_mod.CLOSURE_STATES) == set(
        schema["properties"]["closure_verification"]["enum"]) == {"verified"}


# --- the declaration end -------------------------------------------------------


def _doc(closure: dict | None) -> dict:
    doc = {
        "version": 1,
        "nodes": {
            "builder": {"kind": "skill", "impl": "x", "authority": "find"},
            "code-reviewer": {"kind": "subagent", "impl": "r",
                              "authority": "find"},
            "scoped-re-reviewer": {"kind": "subagent", "impl": "s",
                                   "authority": "find"},
            "cross-examiner": {"kind": "subagent", "impl": "y",
                               "authority": "judge"},
            CLOSURE_ROLE: {"kind": "subagent", "impl": "c",
                           "authority": "find"},
            "gate": {"kind": "deterministic", "impl": "warden review",
                     "authority": "gate"},
            "founder": {"kind": "human", "authority": "merge"},
        },
        "edges": [
            {"from": "builder", "to": "code-reviewer", "payload": "diff"},
            {"from": "code-reviewer", "to": "cross-examiner",
             "payload": "findings"},
            {"from": "builder", "to": "scoped-re-reviewer", "payload": "diff"},
            {"from": "builder", "to": CLOSURE_ROLE, "payload": "findings"},
            {"from": "cross-examiner", "to": "founder", "payload": "pr"},
        ],
        "memory": [{"to": "code-reviewer", "type": "patterns"},
                   {"to": "cross-examiner", "type": "precedents"}],
        "verification": {"independent_judges": ["cross-examiner"]},
        "review": {
            "rounds": [
                {"round": 1, "roles": ["code-reviewer", "cross-examiner"]},
                {"round": 2, "roles": ["scoped-re-reviewer"]},
            ],
            "cap": 2,
            "lenses": [{"id": "fail-closed", "rule": "fail-closed"}],
            "merge_authority": "founder",
        },
    }
    if closure is not None:
        doc["review"]["closure"] = closure
    return doc


@pytest.fixture
def graph_repo(tmp_path):
    root = tmp_path / "graphrepo"
    root.mkdir()
    seed_rules(root, "fail-closed")
    (root / "repo.yaml").write_text(_repo_yaml())
    return root


def _resolve(root: Path, closure: dict | None) -> dict:
    return graph_mod.resolve_review(_doc(closure), root)


def test_a_graph_with_no_closure_key_still_resolves(graph_repo):
    """Optional and additive — the pinned-consumer invariant. Absent means
    nothing runs past the cap, the state every repo was in before the key
    existed; silence is not permission."""
    assert _resolve(graph_repo, None)["closure"] is None


def test_the_closure_round_number_is_derived_not_declared(graph_repo):
    """The declaration names WHO, never WHICH round: the number is derived
    from the cap, and `min_round` is the first round past it — reported for
    messages, while what is enforced is simply "past the cap"."""
    resolved = _resolve(graph_repo, {"roles": [CLOSURE_ROLE],
                                     "payload": "prior-findings-only"})
    assert resolved["closure"] == {"min_round": 3, "roles": [CLOSURE_ROLE],
                                   "payload": "prior-findings-only"}


def test_a_closure_role_already_in_a_capped_round_is_refused(graph_repo):
    """The feature's failure mode in its most plausible dress: declaring
    `code-reviewer` as the closure crew reads as housekeeping and is in fact a
    third adversarial pass, bought past the budget. Refused where it is
    written, not only where it is caught."""
    with pytest.raises(graph_mod.GraphError,
                       match="already declared for a round within the cap"):
        _resolve(graph_repo, {"roles": ["code-reviewer"],
                              "payload": "prior-findings-only"})


def test_an_undeclared_closure_role_is_refused(graph_repo):
    """A closure crew naming a node the org does not declare is a round
    dispatched at nobody."""
    doc = _doc({"roles": [CLOSURE_ROLE], "payload": "prior-findings-only"})
    del doc["nodes"][CLOSURE_ROLE]
    doc["edges"] = [e for e in doc["edges"] if e["to"] != CLOSURE_ROLE]
    with pytest.raises(graph_mod.GraphError, match="not a declared node"):
        graph_mod.resolve_review(doc, graph_repo)


def test_a_closure_role_that_is_not_a_finder_or_judge_is_refused(
        graph_repo):
    """The rule every capped round's roles already answer to: a round
    dispatches finders and judges, never the gate or the merge button."""
    doc = _doc({"roles": [CLOSURE_ROLE], "payload": "prior-findings-only"})
    doc["nodes"][CLOSURE_ROLE] = {"kind": "deterministic",
                                  "impl": "warden review", "authority": "gate"}
    with pytest.raises(graph_mod.GraphError,
                       match="never a gate or a merge"):
        graph_mod.resolve_review(doc, graph_repo)


def test_a_closure_role_outside_the_lens_vocabulary_is_refused(graph_repo):
    """A declared role must be a roster role `attest write --review-dir`
    accepts, or `graph validate` would bless a crew no attestation of it could
    ever carry. Both shapes: a name outside the enum, and a `crew:<slug>`
    naming no declared lens."""
    with pytest.raises(graph_mod.GraphError,
                       match="outside the attestation's role vocabulary"):
        _resolve(graph_repo, {"roles": ["closure-attester"],
                              "payload": "prior-findings-only"})
    with pytest.raises(graph_mod.GraphError, match="names no declared lens"):
        _resolve(graph_repo, {"roles": ["crew:nope"],
                              "payload": "prior-findings-only"})


def test_the_schema_refuses_a_second_closure_role_and_a_grant(graph_repo):
    """`maxItems: 1` and `additionalProperties: false`: a closure round with a
    crew is a review round, and a review round past the cap is the budget this
    block exists not to grant."""
    (graph_repo / "graph.yaml").write_text(yaml.safe_dump(
        _doc({"roles": [CLOSURE_ROLE, "cross-examiner"],
              "payload": "prior-findings-only"})))
    with pytest.raises(graph_mod.GraphError):
        graph_mod.validate(graph_mod.load(graph_repo), graph_repo)
    (graph_repo / "graph.yaml").write_text(yaml.safe_dump(
        _doc({"roles": [CLOSURE_ROLE], "payload": "prior-findings-only",
              "rounds": 3})))
    with pytest.raises(graph_mod.GraphError):
        graph_mod.validate(graph_mod.load(graph_repo), graph_repo)


def test_crew_resolves_the_closure_round_and_refuses_past_it(graph_repo):
    """`warden graph crew --round 3` is how a builder discovers the round
    exists and who it dispatches. It answers with `closure: true` and the
    payload rule, so no caller can read a closure crew as another review."""
    doc = _doc({"roles": [CLOSURE_ROLE], "payload": "prior-findings-only"})
    resolved = graph_mod.crew(doc, graph_repo, 3)
    assert resolved["roles"] == [CLOSURE_ROLE]
    assert resolved["closure"] is True
    assert resolved["payload"] == "prior-findings-only"
    assert "closure" not in graph_mod.crew(doc, graph_repo, 2)
    assert graph_mod.crew(doc, graph_repo, 4)["closure"] is True
    with pytest.raises(graph_mod.CrewPastCap, match="no review round runs"):
        graph_mod.crew(_doc(None), graph_repo, 3)


# --- this repo's own declaration ------------------------------------------------


def test_this_repo_declares_the_closure_round_with_a_role_of_its_own():
    """The platform gates itself with its own warden, so its graph.yaml is the
    first consumer of the key — and the node's boundary says in prose what
    `verify_closure` enforces in code."""
    root = Path(__file__).resolve().parents[1]
    resolved = graph_mod.resolve_review(graph_mod.load(root), root)
    assert resolved["closure"] == {"min_round": resolved["cap"] + 1,
                                   "roles": [CLOSURE_ROLE],
                                   "payload": "prior-findings-only"}
    node = graph_mod.load(root)["nodes"][CLOSURE_ROLE]
    assert node["authority"] == "find"
    assert any("re-files, and cannot find" in b for b in node["boundaries"])


def test_the_summary_names_the_closure_round(branch):
    """A state that rides into the corpus and is never rendered is legible to
    a parser and invisible to the person reading `attest show` — and a round
    minted past the repair cap is the one a reader most needs to be told
    about. Ordinary shards get no line at all: a "not recorded" line on every
    non-closure round would be noise, and its absence cannot read as a pass.
    """
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    doc = _build(root, base, head, _closure_payload(_repaired()), rd,
                 _crew(_closure_terms()))
    assert "closure: VERIFIED" in attest_mod.render_summary(doc)
    ordinary = _round_dir(root, head, 2, ["scoped-re-reviewer"])
    plain = _build(root, base, head, _closure_payload(
        [_finding("docs-drift", "a.py", "fixed")],
        role="scoped-re-reviewer", rnd=2), ordinary, _crew(_closure_terms()))
    assert "closure" not in attest_mod.render_summary(plain)


# --- round-1 regressions: every finding the review confirmed --------------------


def test_r1_f0_a_new_claim_under_a_reused_rule_and_file_is_refused(branch):
    """Round-1 F0 (HIGH, confirmed): the first version keyed admissibility on
    `(rule_id, file)` alone, so a brand-new defect at a new line with new
    prose rode in under a pair a capped round had touched — and this repo's
    charter lenses file against the change's own files nearly every round, so
    such a pair is always available. `stale-guard @ b.py` WAS raised; this
    claim was not."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    smuggled = _finding("stale-guard", "b.py", "fixed")
    smuggled |= {"line": 9999, "severity": "HIGH",
                 "finding": "BRAND NEW DEFECT nobody raised"}
    with pytest.raises(attest_mod.AttestError,
                       match="whose claim no round within the cap") as exc:
        _build(root, base, head, _closure_payload(_repaired() + [smuggled]),
               rd, _crew(_closure_terms()))
    assert "'stale-guard'" in str(exc.value) and "9999" in str(exc.value)


def test_r1_f0_each_field_of_the_claim_is_part_of_the_identity(branch):
    """The identity is the whole assertion, so changing ANY of rule_id, file,
    line, severity or finding text makes it a different claim. Evidence and
    the disposition are what a closure round is meant to move, so they do
    not."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    for field, value in (("line", 42), ("severity", "HIGH"),
                         ("finding", "a different claim")):
        altered = _finding("stale-guard", "b.py", "fixed") | {field: value}
        with pytest.raises(attest_mod.AttestError,
                           match="whose claim no round within the cap"):
            _build(root, base, head, _closure_payload(
                [altered, _finding("tests-bite", "c.py", "fixed")]), rd,
                _crew(_closure_terms()))
    moved = _finding("stale-guard", "b.py", "dismissed-with-reason")
    moved["evidence"] = "rewritten to show what became of it"
    doc = _build(root, base, head, _closure_payload(
        [moved, _finding("tests-bite", "c.py", "fixed")]), rd,
        _crew(_closure_terms()))
    assert doc["closure_verification"] == attest_mod.CLOSURE_VERIFIED


def test_r1_f0_an_open_claim_is_not_answered_by_a_sibling_on_its_key(
        branch):
    """The mirror of the same defect: the drop check required only the KEY to
    appear, so an open finding could be 'answered' by a record about something
    else. Here every record IS an admissible claim — `docs-drift @ a.py` is
    one round 2 filed and closed — so the admissibility check passes and only
    the drop check can catch that the open `stale-guard` was never answered."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    with pytest.raises(attest_mod.AttestError, match="silent on 1") as exc:
        _build(root, base, head, _closure_payload(
            [_finding("docs-drift", "a.py", "fixed"),
             _finding("tests-bite", "c.py", "fixed")]), rd,
            _crew(_closure_terms()))
    assert "'stale-guard'" in str(exc.value)


def test_r1_f1_a_spanning_roster_does_not_vouch_for_the_round_it_mentions(
        tmp_path):
    """Round-1 F1 (HIGH, confirmed): coverage was read off ANY roster entry,
    and a roster spanning lower rounds is a shape warden itself writes and
    the suite tests. So committing only round 2's shard — with a round-1
    roster entry but round-2 records — vouched for a round that had attested
    nothing, and round 1's open HIGH, carried in no records array anywhere,
    was neither admissible nor demanded. Coverage is now credited only to the
    shard whose HIGHEST roster round is that round."""
    root = tmp_path / "repo"
    root.mkdir()
    seed_rules(root, "docs-drift", "stale-guard")
    (root / "repo.yaml").write_text(_repo_yaml())
    shards = root / ".warden" / "memory" / "attest"
    shards.mkdir(parents=True)
    (root / "a.py").write_text("1\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "a.py").write_text("2\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "work")
    head1 = _git(root, "rev-parse", "HEAD")
    # ONE shard, roster spanning rounds 1 and 2, records round-2 only. Round
    # 1's HIGH exists in the review that ran and in no committed record.
    shard = _shard(head1, base, [(2, [_finding("stale-guard", "b.py", "fixed")])])
    shard["reviewers"].insert(0, {"role": "code-reviewer", "agent": "subagent",
                                  "round": 1, "returned": True, "findings": 1,
                                  "output": "code-reviewer.json"})
    (shards / "r2-only.json").write_text(json.dumps(shard, indent=2))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "attest round 2 only, spanning roster")
    head = _git(root, "rev-parse", "HEAD")
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    with pytest.raises(attest_mod.AttestError, match="round.s. 1 of the cap"):
        _build(root, base, head, _closure_payload(
            [_finding("stale-guard", "b.py", "fixed")]), rd,
            _crew(_closure_terms()))


def test_r1_f2_a_closure_round_answers_for_a_finding_but_never_rejudges_it(
        branch):
    """Round-1 F2 (MEDIUM, confirmed): `refuted` reached a clean verdict.
    Refutation asserts the capped round was WRONG — a judgement this round
    holds no authority to make, past the cap, with no judge after it. So the
    one field between an open HIGH and a clean branch is closed."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    refuted = [_finding("stale-guard", "b.py", "refuted"),
               _finding("tests-bite", "c.py", "refuted")]
    with pytest.raises(attest_mod.AttestError,
                       match="status it may not use") as exc:
        _build(root, base, head, _closure_payload(refuted), rd,
               _crew(_closure_terms()))
    assert "'refuted'" in str(exc.value)
    with pytest.raises(attest_mod.AttestError, match="status it may not use"):
        _build(root, base, head, _closure_payload(
            [_finding("stale-guard", "b.py", "confirmed"),
             _finding("tests-bite", "c.py", "fixed")],
            verdict="findings-open"), rd, _crew(_closure_terms()))


def test_r1_f4_a_rebase_does_not_re_strand_the_branch(branch):
    """Round-1 F4 (MEDIUM, confirmed): shards were admitted by the `sha` they
    RECORD, so a rebase — which this repo's own session protocol prescribes —
    rewrote every one, dropped every shard sitting in the PR's own diff, and
    reported that the capped rounds had attested nothing. A shard is now
    admitted by the commit that ADDED it being on head's first-parent line,
    which a rebase preserves."""
    root, base, head = branch
    _git(root, "checkout", "-q", "-b", "work-branch")
    _git(root, "checkout", "-q", "main")
    _git(root, "reset", "--hard", "-q", base)
    (root / "unrelated.py").write_text("1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "main moved")
    new_base = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "work-branch")
    _git(root, "rebase", "-q", new_base)
    rebased = _git(root, "rev-parse", "HEAD")
    assert rebased != head, "the rebase rewrote the branch"
    rd = _round_dir(root, rebased, 3, [CLOSURE_ROLE])
    doc = _build(root, new_base, rebased, _closure_payload(_repaired()), rd,
                 _crew(_closure_terms()))
    assert doc["closure_verification"] == attest_mod.CLOSURE_VERIFIED


def test_r1_f5_an_absolute_path_is_not_reported_as_an_unraised_finding(
        branch):
    """Round-1 F5 (LOW, confirmed): the key comparison ran before
    `relativize_strings`, so a payload spelling `file` as an absolute worktree
    path was refused with a claim about the capped rounds that was false. The
    payload is now normalised the same way the document is, one step earlier,
    so a path spelling can never read as an unraised finding."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    absolute = _finding("stale-guard", str(root / "b.py"), "fixed")
    doc = _build(root, base, head, _closure_payload(
        [absolute, _finding("tests-bite", "c.py", "fixed")]), rd,
        _crew(_closure_terms()))
    assert doc["closure_verification"] == attest_mod.CLOSURE_VERIFIED
    assert doc["findings"][0]["file"] == "b.py"


def test_r1_f6_the_closure_check_refuses_when_it_cannot_read_its_evidence(
        branch, tmp_path):
    """Round-1 F6 (LOW, confirmed): the fail-closed refusals the docstrings
    LEAD with were reachable by no test in the repository, and three
    simultaneous fail-open mutants passed 1009 tests. Each refusal now bites:
    evidence warden could not read is never evidence that there is none."""
    root, base, head = branch
    # 1. no root: the check reads git history and cannot without one.
    with pytest.raises(attest_mod.AttestError, match="without a repository root"):
        attest_mod.verify_closure([], root=None, base_sha=base, head_sha=head,
                                  cap=2)
    # 2. history that cannot be read at all.
    with pytest.raises(attest_mod.AttestError,
                       match="could not be read from history"):
        attest_mod.verify_closure([], root=root, base_sha="no-such-ref",
                                  head_sha=head, cap=2)
    # 3. a committed shard under the store that will not parse.
    shards = root / ".warden" / "memory" / "attest"
    (shards / "broken.json").write_text("{not json")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "a shard that does not parse")
    broken_head = _git(root, "rev-parse", "HEAD")
    with pytest.raises(attest_mod.AttestError, match="but it cannot be read"):
        attest_mod.verify_closure([], root=root, base_sha=base,
                                  head_sha=broken_head, cap=2)


def test_r1_f6_a_record_warden_cannot_read_neither_admits_nor_demands(
        branch):
    """The three record-shape skips, which are the fail-closed reading on the
    side that matters: a record whose round, shape or claim warden cannot read
    admits nothing. It is checked on the ADMITTING side, where a permissive
    read would hand a builder a free claim."""
    root, base, head = branch
    shards = root / ".warden" / "memory" / "attest"
    junk = _shard(head, base, [(1, [])])
    junk["records"] = [
        "not a dict",
        {"round": "2", "rule_id": "docs-drift", "file": "a.py",
         "severity": "MEDIUM", "finding": "f", "status": "confirmed"},
        {"round": 1, "rule_id": "docs-drift", "file": 7, "severity": "MEDIUM",
         "finding": "f", "status": "confirmed"},
        {"round": 99, "rule_id": "docs-drift", "file": "z.py",
         "severity": "MEDIUM", "finding": "f", "status": "confirmed"},
    ]
    (shards / "junk.json").write_text(json.dumps(junk, indent=2))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "a shard of unreadable records")
    head = _git(root, "rev-parse", "HEAD")
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    # None of those records made `docs-drift @ z.py` (or a.py at round "2")
    # admissible, and none is demanded either.
    with pytest.raises(attest_mod.AttestError,
                       match="whose claim no round within the cap"):
        _build(root, base, head, _closure_payload(
            _repaired() + [_finding("docs-drift", "z.py", "fixed")]), rd,
            _crew(_closure_terms()))
    doc = _build(root, base, head, _closure_payload(_repaired()), rd,
                 _crew(_closure_terms()))
    assert doc["closure_verification"] == attest_mod.CLOSURE_VERIFIED


def test_r1_f3_the_docstrings_do_not_claim_a_count_nothing_enforces():
    """Round-1 F3 (LOW, confirmed, CLAIM change): `verify_crew`'s docstring
    said 'Exactly one round runs past the cap ... at cap+1' while its own body
    eighty lines below said the opposite, with a reason the examiner verified.
    Pinning the number was refuted as a remedy; the sentence was the defect."""
    doc = attest_mod.verify_crew.__doc__
    assert "Exactly one round runs past the cap" not in doc
    assert "is not pinned" in doc and "not a count" in doc
    summary = attest_mod.render_summary({
        "verdict": "clean", "findings": [], "reviewers": [],
        "head_sha": "a" * 40, "rules_version": "v1", "reviewed_at": "now",
        "closure_verification": attest_mod.CLOSURE_VERIFIED})
    assert "re-files a claim a capped round committed here" in summary
    assert "every record re-files a finding a capped round raised" not in summary


# --- round-2 regressions: what the round-1 repair broke -------------------------


def test_r2_2_a_capped_finding_with_no_line_can_still_be_closed(tmp_path):
    """Round-2 R2-2 (MEDIUM, confirmed): the claim identity compared an
    ATTESTATION-shaped payload against a MEMORY-RECORD-shaped shard, and the
    two spell an absent `line` differently — `memory.build_records` writes 0,
    the payload reads None. A capped round that filed any finding without a
    line number therefore left the branch permanently unclosable, refused both
    as a claim no capped round filed AND as silence on that same open claim.
    That is the unattestable-head defect re-created by this feature's own fix, so the shard
    here is built by the real `build_records` rather than by hand."""
    root = tmp_path / "repo"
    root.mkdir()
    seed_rules(root, "docs-drift")
    (root / "repo.yaml").write_text(_repo_yaml())
    shards = root / ".warden" / "memory" / "attest"
    shards.mkdir(parents=True)
    (root / "a.py").write_text("1\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "a.py").write_text("2\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "work")
    head1 = _git(root, "rev-parse", "HEAD")
    # A finding with NO `line` — valid under the attestation contract.
    lineless = {"rule_id": "docs-drift", "severity": "MEDIUM", "file": "a.py",
                "finding": "no line number on this one", "evidence": "e",
                "status": "confirmed", "lens": "code-reviewer", "round": 1}
    attestation = {"head_sha": head1, "base_sha": base, "rules_version": "v1",
                   "reviewed_at": "2026-09-21T00:00:00+00:00",
                   "verdict": "findings-open",
                   "reviewers": [{"role": "code-reviewer", "agent": "subagent",
                                  "round": 1, "returned": True, "findings": 1,
                                  "output": "code-reviewer.json"}],
                   "findings": [lineless]}
    for rnd in (1, 2):
        doc = dict(attestation)
        doc["reviewers"] = [dict(attestation["reviewers"][0], round=rnd)]
        doc["findings"] = [dict(lineless, round=rnd)]
        records = memory_mod.build_records(doc)
        assert records[0]["line"] == 0, (
            "build_records no longer writes 0 for an absent line; the "
            "normalisation in _claim_of is keyed to that and must follow it")
        shard = {"schema": 1, "source": "attest", "sha": head1,
                 "base_sha": base, "rules_version": "v1",
                 "reviewed_at": doc["reviewed_at"],
                 **memory_mod.build_review_event(doc), "records": records}
        (shards / f"r{rnd}.json").write_text(json.dumps(shard, indent=2))
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", f"attest round {rnd}")
    head = _git(root, "rev-parse", "HEAD")
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    # Re-filed VERBATIM, which for this finding means carrying no `line` —
    # exactly what the skill instructs, and what used to be unclosable.
    closing = {k: v for k, v in lineless.items() if k != "round"}
    closing["status"] = "fixed"
    doc = _build(root, base, head, _closure_payload([closing]), rd,
                 _crew(_closure_terms()))
    assert doc["closure_verification"] == attest_mod.CLOSURE_VERIFIED


def test_r2_1_a_spanning_roster_that_carries_both_rounds_records_closes(
        tmp_path):
    """Round-2 R2-1 (MEDIUM, confirmed): F1's fix anchored coverage on the
    roster alone, so a single shard whose roster spans rounds 1 and 2 AND
    carries both rounds' records was refused with 'round 1 attested nothing to
    this branch' while round 1's records sat in the same function's admissible
    set. A branch with everything committed was told nothing was — F4's
    failure mode from the other side. A round is now covered by a shard that
    carries its records too, and the F1 attack (roster label, records absent)
    stays closed because a record cannot vouch for itself without existing."""
    root = tmp_path / "repo"
    root.mkdir()
    seed_rules(root, "docs-drift", "stale-guard")
    (root / "repo.yaml").write_text(_repo_yaml())
    shards = root / ".warden" / "memory" / "attest"
    shards.mkdir(parents=True)
    (root / "a.py").write_text("1\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "a.py").write_text("2\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "work")
    head1 = _git(root, "rev-parse", "HEAD")
    spanning = _shard(head1, base, [
        (1, [_finding("docs-drift", "a.py", "fixed")]),
        (2, [_finding("stale-guard", "b.py", "confirmed")])])
    (shards / "spanning.json").write_text(json.dumps(spanning, indent=2))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "one shard, roster and records spanning")
    head = _git(root, "rev-parse", "HEAD")
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    doc = _build(root, base, head, _closure_payload(
        [_finding("stale-guard", "b.py", "fixed")]), rd,
        _crew(_closure_terms()))
    assert doc["closure_verification"] == attest_mod.CLOSURE_VERIFIED


def test_r2_5_a_non_dict_finding_is_refused_not_a_traceback(branch):
    """Round-2 R2-5 (LOW, confirmed): the round-1 repair dropped an isinstance
    guard the pre-image carried, so a bare string in `findings` raised an
    uncaught AttributeError out of the closure check. `verify_closure` runs
    inside `build` BEFORE schema validation, so the shape does reach it."""
    root, base, head = branch
    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    payload = _closure_payload(_repaired())
    payload["findings"].append("not a dict at all")
    with pytest.raises(attest_mod.AttestError):
        _build(root, base, head, payload, rd, _crew(_closure_terms()))


def test_r2_3_the_merge_claim_says_only_what_it_delivers():
    """Round-2 R2-3 (LOW, confirmed): `--no-merges` discriminates only while
    the merge is still a merge COMMIT — a rebase flattens it and a cherry-pick
    sidesteps it in one step. The remedy is the claim, not a guarantee git
    cannot give: the bound is 'not silently', which is what the rest of this
    check carries too."""
    source = inspect.getsource(attest_mod._closure_shards)
    assert "WHAT `--no-merges` DOES NOT BUY" in source
    assert '"not silently", not "not at all"' in source
    # and the refusal names a merge COMMIT, which is what actually
    # discriminates (the literal is split across source lines, so the
    # assertion is on the fragments rather than on a rendered message no
    # honest fixture reaches without first failing the coverage check)
    refusal = inspect.getsource(attest_mod.verify_closure)
    assert "arrived by a merge " in refusal and "commit is another" in refusal


def test_r2_4_the_cli_help_does_not_claim_the_cap_always_exits_1():
    """Round-2 R2-4 (LOW, confirmed): `warden graph --help` is the site a
    builder reads first and the only one shipping inside the tool, and it
    still said a round past the cap exits 1 — false in any repo declaring
    review.closure, this one included."""
    parser = cli._build_parser() if hasattr(cli, "_build_parser") else None
    text = ""
    if parser is None:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                cli.main(["graph", "--help"])
            except SystemExit:
                pass
        text = buf.getvalue()
    assert "review.closure" in text, (
        "graph --help does not mention the closure round, so the only "
        "in-tool statement of the cap's behaviour is still the false one")


# --- the closure round through the CLI, and the state it records ---------------


def test_a_closure_round_through_the_cli_records_the_chain_question_unasked(
        branch, monkeypatch, tmp_path, capsys):
    """`attest write` re-derives the chain rule's STATE for the
    run manifest, and that re-derivation has THREE arms — `chained`,
    `no-review-dir` and `closure-round`. The first two are covered by
    `test_the_chain_state_reaches_the_run_manifest` and
    `test_a_payload_with_no_review_dir_is_not_gated_by_the_chain_rule`;
    nothing covered the third, and nothing anywhere drove a closure round
    through `cli.main(["attest", "write", ...])` at all — every test above
    calls `attest.build` directly, which never touches the manifest.

    MUTATION-CONFIRMED gap (the closure-attestor of feat/coverage-bites):
    deleting `and "closure_verification" not in doc` from the guard leaves
    every closure test in this module green while the manifest silently
    records `chained` for a rule that was never applied as a gate here —
    `verify_closure` applied the stricter one. An auditor reading that
    manifest would be told a check ran that did not.

    So the assertion is the manifest's own word, exit 0, and the closure
    state beside it: the run says which of the two rules gated this round.

    AND THE SCOPING, not the label alone. The label half above
    was this test's whole subject, and `warden/cli.py`'s comment calls the
    SCOPE of that `if` load-bearing in its own right: called unconditionally,
    the re-derivation became a second gate over two paths `build`
    deliberately excludes, and on this one `verify_closure` has already
    applied the stricter rule. MEASURED before this arm existed: reinserting
    an unconditional `attest_mod.verify_chain(...)` above the guard, with the
    label logic untouched, left `tests/test_closure_round.py` +
    `tests/test_attest_chain.py` at 58 passed, rc 0 — so a refactor could
    restore a gate that can raise `AttestError` on a document `build` has
    already accepted and no test would notice. The spy below is the witness:
    on a closure round the chain question is not asked, by anyone.
    """
    from warden import cli as cli_mod
    root, base, _ = branch
    # The declared crew has to be READ FROM THE REPO on this path — `cli`
    # resolves it through `graph.declared_crew` rather than taking a dict —
    # and evidence dirs have to be ignored, because `attest write` refuses a
    # dirty tree and a minted round dirties it.
    seed_rules(root, "fail-closed")
    # `min_round` is DERIVED (cap + 1), so the declaration carries the two
    # keys graph.yaml itself carries and the schema refuses a third.
    declared = {"roles": [CLOSURE_ROLE], "payload": "prior-findings-only"}
    (root / "graph.yaml").write_text(yaml.safe_dump(_doc(declared)))
    (root / ".gitignore").write_text(".warden/out/\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "declare the closure round")
    head = _git(root, "rev-parse", "HEAD")

    rd = _round_dir(root, head, 3, [CLOSURE_ROLE])
    findings = tmp_path / "closure-payload.json"
    findings.write_text(json.dumps(_closure_payload(_repaired())))
    monkeypatch.chdir(root)
    # The chain question must not be asked on this path — by `cli`'s
    # re-derivation or by anything `build` reaches. Patched on the module
    # `warden.cli` resolves it through, so a call from either side is caught.
    asked: list[tuple] = []
    real_verify_chain = attest_mod.verify_chain

    def _spy(*a, **kw):
        asked.append((a, kw))
        return real_verify_chain(*a, **kw)

    monkeypatch.setattr(attest_mod, "verify_chain", _spy)
    code = cli_mod.main(["attest", "write", "--findings", str(findings),
                         "--base", base, "--review-dir", str(rd)])
    assert code == 0, capsys.readouterr().err
    capsys.readouterr()
    assert asked == [], (
        f"`verify_chain` was called {len(asked)} time(s) on a closure round. "
        "The chain rule is not the gate here — `verify_closure` applied the "
        "stricter one — so a call is a second gate over a document `build` "
        "has already accepted, and it can raise AttestError on it. "
        "Keep the re-derivation scoped the way `build` "
        "scopes it")

    run_dir = max((root / ".warden" / "out").glob("*-attest"),
                  key=lambda d: d.name)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["chain_verification"] == "closure-round", (
        "a closure round's manifest does not say the chain rule was never "
        "asked here: the state is "
        f"{manifest.get('chain_verification')!r}. `verify_closure` applied "
        "the stricter rule and `build` never asked the chain question, so "
        "recording `chained` claims a gate that did not run")
    assert manifest["closure_verification"] == attest_mod.CLOSURE_VERIFIED, (
        f"the run was not a closure round at all: {manifest}")
    attestation = json.loads((run_dir / "attestation.json").read_text())
    assert attestation["closure_verification"] == attest_mod.CLOSURE_VERIFIED
