"""`review.delegation` in graph.yaml, and `warden graph authority`.

Every policy file here says the same sentence: merge authority is the
founder's, *delegated only by an explicit, current, revocable grant*. Until
this block existed, `graph.yaml` could not hold any part of that — it carried
`merge_authority: founder` and nothing else — so a real, founder-issued
session grant lived in a conversation and the artifact the policy pointed at
could not have recorded it.

The block records the TERMS and refuses the GRANT, and the refusal is the
design rather than a shortfall in it. `graph.yaml` is tracked and has no
clock: a session grant written into it outlives the session, and the next
session reads a permission the founder issued once, months ago, as its own.
So the tests below pin two things above all —

- the block is structurally incapable of holding a live grant
  (`test_a_grant_shaped_key_is_refused_by_the_schema`), and
- `may_merge_now` is the merge authority whatever the terms say
  (`test_may_merge_now_is_the_authority_whatever_the_terms_declare`).

The rest is fail-closed housekeeping: a granter who is not the authority
holder is self-issue, an eligible node that does not exist is a typo that
would silently widen nothing, and an absent block permits no delegation at
all.
"""

import json
from pathlib import Path

import pytest
import yaml

from conftest import REPO_YAML
from warden import cli
from warden import graph as graph_mod

ROOT = Path(__file__).parent.parent


def _delegation(**over) -> dict:
    terms = {
        "permits": "merge-on-green",
        "grantable_by": "founder",
        "eligible": [],
        "max_lifetime": "session",
        "recorded_in": "decide",
    }
    terms.update(over)
    return terms


def _review(delegation: dict | None = None) -> dict:
    review = {
        "rounds": [
            {"round": 1, "roles": ["code-reviewer", "cross-examiner"]},
            {"round": 2, "roles": ["scoped-re-reviewer"]},
        ],
        "cap": 2,
        "lenses": [{"id": "secrets-in-diff", "rule": "secrets-in-diff"}],
        "merge_authority": "founder",
    }
    if delegation is not None:
        review["delegation"] = delegation
    return review


def _repo(tmp_path: Path, review: dict | None) -> Path:
    root = tmp_path / "repo"
    rules = root / ".warden" / "rules"
    rules.mkdir(parents=True)
    (rules / "secrets-in-diff.md").write_text("---\nid: secrets-in-diff\n---\n")
    repo = yaml.safe_load(REPO_YAML)
    repo["repair"] = {"budget": 2}
    (root / "repo.yaml").write_text(yaml.safe_dump(repo))
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
            "founder": {"kind": "human", "authority": "merge"},
            "deputy": {"kind": "human", "authority": "merge"},
        },
        "edges": [
            {"from": "builder", "to": "code-reviewer", "payload": "diff"},
            {"from": "code-reviewer", "to": "cross-examiner",
             "payload": "findings"},
            {"from": "builder", "to": "scoped-re-reviewer", "payload": "diff"},
            {"from": "cross-examiner", "to": "founder", "payload": "pr"},
            {"from": "cross-examiner", "to": "deputy", "payload": "pr"},
        ],
        "memory": [{"to": "code-reviewer", "type": "patterns"},
                   {"to": "cross-examiner", "type": "precedents"}],
        "verification": {"independent_judges": ["cross-examiner"]},
    }
    if review is not None:
        doc["review"] = review
    (root / "graph.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return root


def _authority(root: Path) -> dict:
    return graph_mod.authority(graph_mod.load(root), root)


# --- the staleness fence -------------------------------------------------------


@pytest.mark.parametrize("key, value", [
    ("granted_to", "builder"),
    ("granted_at", "2026-09-20T00:00:00Z"),
    ("expires_at", "2026-09-21T00:00:00Z"),
    ("holder", "builder"),
    ("active", True),
    ("session", "abc123"),
])
def test_a_grant_shaped_key_is_refused_by_the_schema(tmp_path, key, value):
    """A LIVE grant cannot be written into the tracked file, by construction.

    This is the crux of the whole block. A session grant recorded in
    `graph.yaml` survives the session that justified it, and a later agent
    reading it cannot tell a grant issued this morning from one issued in
    March — so it reads a stale permission as current and merges on it. A
    permanent record of a temporary grant does not merely fail to prove
    authority; it manufactures it.

    The block therefore has no field that could name a holder, an issue time
    or an expiry, and `additionalProperties: false` refuses one added later.
    The refusal names the key, so the author sees WHICH field was rejected.
    """
    root = _repo(tmp_path, _review(_delegation(**{key: value})))
    with pytest.raises(graph_mod.GraphError) as exc:
        graph_mod.load(root)
    assert key in str(exc.value), (
        "the schema refused the grant-shaped key without naming it")


def test_may_merge_now_is_the_authority_whatever_the_terms_declare(tmp_path):
    """Declaring a node eligible grants it nothing.

    Eligibility is standing policy; a grant is a separate, out-of-band act
    with a session's life. If declaring `eligible` widened `may_merge_now`,
    the block would BE a grant under another name, and every failure it was
    built to prevent would be back. So the answer is the authority holder
    with an empty eligible list and with a populated one alike.
    """
    empty = _authority(_repo(tmp_path / "a", _review(_delegation())))
    peopled = _authority(_repo(tmp_path / "b",
                               _review(_delegation(eligible=["builder"]))))
    assert empty["may_merge_now"] == ["founder"]
    assert peopled["may_merge_now"] == ["founder"]
    assert peopled["delegation"]["eligible"] == ["builder"]


def test_no_lifetime_longer_than_a_session_is_representable(tmp_path):
    """`session` is the only value. A standing grant has no spelling here."""
    root = _repo(tmp_path, _review(_delegation(max_lifetime="standing")))
    with pytest.raises(graph_mod.GraphError, match="max_lifetime"):
        graph_mod.load(root)


def test_auto_merge_is_not_a_declarable_permit(tmp_path):
    """`.warden/skills-policy.md`: auto-merge-on-green "would be a further
    grant, taken never assumed". It is not in the enum, so a repo cannot
    declare it as a term at all."""
    root = _repo(tmp_path, _review(_delegation(permits="auto-merge-on-green")))
    with pytest.raises(graph_mod.GraphError, match="permits"):
        graph_mod.load(root)


# --- fail closed ---------------------------------------------------------------


def test_a_review_block_without_delegation_permits_none(tmp_path):
    """Silence is not permission. The block is optional — so every graph.yaml
    written before it keeps validating — and its absence declares that the
    execution of merge authority may not be delegated at all, which the
    `authority` basis says in those words rather than leaving to inference."""
    root = _repo(tmp_path, _review(None))
    answer = _authority(root)
    assert answer["delegation"] is None
    assert answer["may_merge_now"] == ["founder"]
    assert "may not be delegated" in answer["basis"]


@pytest.mark.parametrize("block", [{}, None, "founder"])
def test_a_delegation_key_that_declares_no_terms_is_refused_not_ignored(
        tmp_path, block):
    """An empty, null or scalar `delegation:` is a malformed declaration, and
    reading it as "no terms declared" would answer a question the document
    never answered. It is refused instead; "could not say" is not "declares
    none". (The permissive direction is the restrictive one here — absent
    terms permit no delegation — so this is about reading a document
    honestly, not about a widened permission.)"""
    review = _review()
    review["delegation"] = block
    root = _repo(tmp_path, review)
    with pytest.raises(graph_mod.GraphError, match="graph.yaml schema"):
        graph_mod.load(root)


def test_grantable_by_must_be_the_merge_authority(tmp_path):
    """A delegation issued by anyone but the authority holder is self-issued.

    `deputy` is a declared, human, merge-authority node here, so nothing
    cheaper than this check catches it: the reference resolves and the node
    could plausibly grant. What refuses it is that it is not the node
    `review.merge_authority` names.
    """
    root = _repo(tmp_path, _review(_delegation(grantable_by="deputy")))
    doc = graph_mod.load(root)
    with pytest.raises(graph_mod.GraphError, match="self-issued"):
        graph_mod.validate(doc, root)


def test_grantable_by_must_be_a_declared_node(tmp_path):
    root = _repo(tmp_path, _review(_delegation(grantable_by="nobody")))
    doc = graph_mod.load(root)
    with pytest.raises(graph_mod.GraphError,
                       match="grantable_by references undeclared node"):
        graph_mod.validate(doc, root)


def test_eligible_must_name_declared_nodes(tmp_path):
    root = _repo(tmp_path, _review(_delegation(eligible=["ghost"])))
    doc = graph_mod.load(root)
    with pytest.raises(graph_mod.GraphError,
                       match=r"eligible\[0\] references undeclared node"):
        graph_mod.validate(doc, root)


def test_eligible_may_not_name_a_merge_authority_node(tmp_path):
    """Delegating merge to a node that already holds it records nothing, and
    naming the authority itself would read as a grant to the granter."""
    root = _repo(tmp_path, _review(_delegation(eligible=["deputy"])))
    doc = graph_mod.load(root)
    with pytest.raises(graph_mod.GraphError, match="already declares authority"):
        graph_mod.validate(doc, root)


def test_eligible_may_not_repeat_a_node(tmp_path):
    root = _repo(tmp_path, _review(_delegation(eligible=["builder",
                                                         "builder"])))
    doc = graph_mod.load(root)
    with pytest.raises(graph_mod.GraphError, match="listed twice"):
        graph_mod.validate(doc, root)


def test_graph_validate_is_what_refuses_bad_terms(tmp_path, monkeypatch,
                                                  capsys):
    """Declared means enforced: the terms are checked by the command the gate
    and the hooks already run, not only by the resolver a new subcommand
    calls."""
    root = _repo(tmp_path, _review(_delegation(grantable_by="deputy")))
    monkeypatch.chdir(root)
    assert cli.main(["graph", "validate"]) == 2
    assert "self-issued" in capsys.readouterr().err


def test_authority_cli_prints_the_standing_answer_as_json(tmp_path, monkeypatch,
                                                          capsys):
    """The command's stdout IS the answer, so the command is what this drives.

    Named regression test for the round-1 finding: every other happy-path
    assertion in this module calls `graph_mod.authority` directly, and so does
    the wiki pin, so deleting the CLI's `print(json.dumps(...))` left all of
    them green while `warden graph authority` printed nothing at exit 0 — a
    command that answers the question by saying nothing. Its sibling `crew`
    has carried this test since it shipped.
    """
    root = _repo(tmp_path, _review(_delegation(eligible=["builder"])))
    monkeypatch.chdir(root)
    assert cli.main(["graph", "authority"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["merge_authority"] == "founder"
    assert out["may_merge_now"] == ["founder"]
    assert out["delegation"]["eligible"] == ["builder"]
    assert "no live grant is readable" in out["basis"].lower()


def test_authority_cli_refuses_a_graph_that_declares_no_review_block(
        tmp_path, monkeypatch, capsys):
    """"Could not say" is never printed as an answer."""
    root = _repo(tmp_path, None)
    monkeypatch.chdir(root)
    assert cli.main(["graph", "authority"]) == 2
    assert "no `review` block" in capsys.readouterr().err


# --- the consumer contract -----------------------------------------------------


def test_the_crew_command_is_unchanged_by_delegation(tmp_path):
    """`warden graph crew` is what the skill pack dispatches from and what
    `attest write --review-dir` checks a roster against. Its shape is a
    consumer contract, so the new block adds no key to it."""
    a = _repo(tmp_path / "a", _review(_delegation()))
    b = _repo(tmp_path / "b", _review(None))
    with_terms = graph_mod.crew(graph_mod.load(a), a, 1)
    without = graph_mod.crew(graph_mod.load(b), b, 1)
    assert with_terms == without
    assert "delegation" not in with_terms


# --- this repo's own declaration -----------------------------------------------


def test_this_repo_declares_terms_whose_granter_is_its_merge_authority():
    """nightgate dogfoods the block: the terms are declared, and the only node
    that may issue a grant is the one holding the button."""
    review = graph_mod.resolve_review(graph_mod.load(ROOT), ROOT)
    terms = review["delegation"]
    assert terms is not None, "graph.yaml declares no delegation terms"
    assert terms["grantable_by"] == review["merge_authority"] == "founder"
    assert terms["max_lifetime"] == "session"


def test_graph_layer_authority_sample_is_real_command_output(tmp_path,
                                                             monkeypatch,
                                                             capsys):
    """The wiki's `warden graph authority` block is the COMMAND's stdout, not
    a retyped copy — and not a retyped copy of the SERIALIZATION either.

    This test once said the same sentence while asserting
    `json.dumps(graph.authority(...), indent=2)`, which re-types the CLI's own
    formatting beside the real one. The mutation: dropping `indent` from
    `warden/cli.py`'s `json.dumps(answer, indent=2)` — the indent only — left
    EVERY test green across the delegation module and all three docs modules,
    while `warden graph authority`'s stdout stopped matching the page's fenced
    block. A page pinned to a function's return value is not pinned to the
    command a reader is told to run.

    RE-DERIVED 2026-09-22 at 4dc253d, because the figure that stood here was
    not: it read "149 tests green", a number carried over from the bead rather
    than measured, and the four modules collect 194 there — 24 + 33 + 88 + 49,
    and no subset sums to 149. The correction makes the case stronger, not
    weaker (nothing was red, so the mutation was invisible to all 194), and it
    is written as a count that cannot rot: with this guard in place the same
    mutation is 1 failed, 194 passed.

    THE FIXTURE CARRIES THIS REPO'S GRAPH, and the equality below is what
    makes that legitimate rather than a second retyping: `graph authority`
    reads more than `graph.yaml`, so the fixture's answer is asserted equal
    to the real root's before its stdout is compared with the page. If the
    two ever diverge this says so instead of pinning the wrong document.
    """
    doc = (ROOT / "docs" / "wiki" / "Graph-Layer.md").read_text()
    real = graph_mod.authority(graph_mod.load(ROOT), ROOT)
    root = _repo(tmp_path, None)
    graph_text = (ROOT / "graph.yaml").read_text()
    (root / "graph.yaml").write_text(graph_text)
    # `warden graph` validates before it answers, and a lens pointing at a
    # rule file that is not there is a refusal — correctly. Seed the files the
    # real graph's lenses name, derived from the document rather than listed,
    # so a new lens does not have to be remembered here.
    rules = root / ".warden" / "rules"
    for lens in (yaml.safe_load(graph_text)["review"].get("lenses") or []):
        if lens.get("rule"):
            (rules / f"{lens['rule']}.md").write_text(
                f"---\nid: {lens['rule']}\n---\n")
    monkeypatch.chdir(root)
    assert cli.main(["graph", "authority"]) == 0, capsys.readouterr().err
    printed = capsys.readouterr().out
    assert json.loads(printed) == real, (
        "the fixture answers a different standing question than this "
        "repository's own root, so its stdout is not the sample the page "
        f"claims to show: {json.loads(printed)} != {real}")
    assert printed.rstrip("\n") in doc, (
        "Graph-Layer.md's `warden graph authority` sample is not that "
        "command's STDOUT — the values may be right while the serialization "
        "is not. Regenerate it by running the command")
