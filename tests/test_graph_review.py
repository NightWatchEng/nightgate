"""The review block in graph.yaml and `warden graph crew --round N`.

The block declares, as checkable config, what used to live only in skill
prose: which roles review a diff in each round, the round cap, the charter
lenses and the node holding merge authority. `crew` resolves one round of it
as JSON. Every way the block can be wrong fails closed with a named cause.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

from conftest import REPO_YAML
from warden import cli
from warden import graph as graph_mod

ROOT = Path(__file__).parent.parent


def _checklist() -> list[str]:
    return ["does this change a shipped schema a pinned consumer reads?"]


def _review() -> dict:
    return {
        "rounds": [
            {"round": 1, "roles": ["code-reviewer", "cross-examiner"]},
            {"round": 2, "roles": ["scoped-re-reviewer"]},
        ],
        "cap": 2,
        "lenses": [
            {"id": "consumer-blast-radius", "checklist": _checklist()},
            {"id": "secrets-in-diff", "rule": "secrets-in-diff"},
        ],
        "merge_authority": "founder",
    }


def _graph(review: dict | None) -> dict:
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
        },
        "edges": [
            {"from": "builder", "to": "code-reviewer", "payload": "diff"},
            {"from": "code-reviewer", "to": "cross-examiner",
             "payload": "findings"},
            {"from": "builder", "to": "scoped-re-reviewer", "payload": "diff"},
            {"from": "cross-examiner", "to": "founder", "payload": "pr"},
        ],
        "memory": [{"to": "code-reviewer", "type": "patterns"},
                   {"to": "cross-examiner", "type": "precedents"}],
        "verification": {"independent_judges": ["cross-examiner"]},
    }
    if review is not None:
        doc["review"] = review
    return doc


def _repo(tmp_path: Path, review: dict | None = None, *, budget=2,
          graph: dict | None = None) -> Path:
    root = tmp_path / "repo"
    rules = root / ".warden" / "rules"
    rules.mkdir(parents=True)
    (rules / "secrets-in-diff.md").write_text("---\nid: secrets-in-diff\n---\n")
    # The sample fixture's repo.yaml: schema-valid, so `warden graph crew`
    # through the CLI loads it, with `review.rules_dir: .warden/rules`.
    repo = yaml.safe_load(REPO_YAML)
    if budget is not None:
        repo["repair"] = {"budget": budget}
    (root / "repo.yaml").write_text(yaml.safe_dump(repo))
    doc = graph if graph is not None else _graph(review)
    (root / "graph.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return root


def _crew(root: Path, rnd: int) -> dict:
    return graph_mod.crew(graph_mod.load(root), root, rnd)


# --- resolution ----------------------------------------------------------------


def test_crew_round_1_resolves_roles_in_order_with_lenses_and_cap(tmp_path):
    root = _repo(tmp_path, _review())
    got = _crew(root, 1)
    assert got["round"] == 1
    assert got["roles"] == ["code-reviewer", "cross-examiner"]
    assert got["cap"] == 2
    assert got["merge_authority"] == "founder"
    assert got["lenses"] == [
        {"id": "consumer-blast-radius", "checklist": _checklist()},
        {"id": "secrets-in-diff", "rule": ".warden/rules/secrets-in-diff.md"},
    ]


def test_crew_round_2_resolves_to_the_scoped_re_reviewer(tmp_path):
    root = _repo(tmp_path, _review())
    got = _crew(root, 2)
    assert got["round"] == 2
    assert got["roles"] == ["scoped-re-reviewer"]
    assert got["cap"] == 2


def test_crew_cli_prints_the_resolved_round_as_json(tmp_path, monkeypatch,
                                                    capsys):
    root = _repo(tmp_path, _review())
    monkeypatch.chdir(root)
    assert cli.main(["graph", "crew", "--round", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["roles"] == ["code-reviewer", "cross-examiner"]
    assert out["cap"] == 2


# --- fail closed ---------------------------------------------------------------


def test_crew_round_past_the_cap_refuses_naming_the_cap(tmp_path, monkeypatch,
                                                        capsys):
    root = _repo(tmp_path, _review())
    with pytest.raises(graph_mod.CrewPastCap, match="cap of 2"):
        _crew(root, 3)
    monkeypatch.chdir(root)
    assert cli.main(["graph", "crew", "--round", "3"]) == 1
    assert "cap of 2" in capsys.readouterr().err


def test_crew_missing_review_block_fails_closed(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path, None)
    with pytest.raises(graph_mod.GraphError, match="no `review` block"):
        _crew(root, 1)
    monkeypatch.chdir(root)
    assert cli.main(["graph", "crew", "--round", "1"]) == 2
    assert "no `review` block" in capsys.readouterr().err


def test_review_block_unknown_role_fails(tmp_path):
    """A role the attestation vocabulary accepts but the graph never declares
    as a node: the node check, not the vocabulary check, is what refuses it."""
    doc = _graph(_review())
    del doc["nodes"]["scoped-re-reviewer"]
    doc["edges"] = [e for e in doc["edges"] if e["to"] != "scoped-re-reviewer"]
    root = _repo(tmp_path, graph=doc)
    unknown = "'scoped-re-reviewer' is not a declared node"
    with pytest.raises(graph_mod.GraphError, match=unknown):
        graph_mod.validate(graph_mod.load(root), root)
    with pytest.raises(graph_mod.GraphError, match=unknown):
        _crew(root, 2)


def test_review_block_missing_rule_pointer_fails(tmp_path):
    review = _review()
    review["lenses"][1]["rule"] = "no-such-rule"
    root = _repo(tmp_path, review)
    with pytest.raises(graph_mod.GraphError, match="no-such-rule.md"):
        graph_mod.validate(graph_mod.load(root), root)


def test_review_block_cap_disagreeing_with_repair_budget_fails(tmp_path):
    root = _repo(tmp_path, _review(), budget=1)
    with pytest.raises(graph_mod.GraphError,
                       match=r"cap 2.*repair\.budget.*1"):
        graph_mod.validate(graph_mod.load(root), root)


def test_review_block_cap_with_no_repair_budget_fails(tmp_path):
    root = _repo(tmp_path, _review(), budget=None)
    with pytest.raises(graph_mod.GraphError,
                       match=r"declares no integer repair\.budget"):
        graph_mod.validate(graph_mod.load(root), root)


def test_crew_round_zero_fails(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path, _review())
    with pytest.raises(graph_mod.GraphError, match="round 0"):
        _crew(root, 0)
    monkeypatch.chdir(root)
    assert cli.main(["graph", "crew", "--round", "0"]) == 2
    assert "round 0" in capsys.readouterr().err


@pytest.mark.parametrize("mutate, fragment", [
    (lambda r: r["rounds"].pop(), "round 2"),                   # cap uncovered
    (lambda r: r["rounds"].append({"round": 3, "roles": ["code-reviewer"]}),
     "round 3"),                                                # past the cap
    (lambda r: r["rounds"][0]["roles"].append("code-reviewer"),
     "twice"),                                                  # duplicate
    (lambda r: r.update(merge_authority="builder"), "'builder'"),
    (lambda r: r["lenses"].append({"id": "consumer-blast-radius",
                                   "checklist": ["x"]}), "twice"),
    (lambda r: r["rounds"][0]["roles"].append("crew:ghost-lens"),
     "'crew:ghost-lens'"),                                      # undeclared lens
])
def test_review_block_other_invalid_shapes_fail(tmp_path, mutate, fragment):
    review = _review()
    mutate(review)
    root = _repo(tmp_path, review)
    with pytest.raises(graph_mod.GraphError, match=re.escape(fragment)):
        graph_mod.validate(graph_mod.load(root), root)


def test_review_block_role_outside_the_attest_vocabulary_fails(tmp_path):
    """A crew `attest write --review-dir` could never accept is refused here:
    every declared role must also be a roster role the attestation takes."""
    doc = _graph(_review())
    doc["nodes"]["security-reviewer"] = {"kind": "subagent", "impl": "z",
                                         "authority": "find"}
    doc["edges"].append({"from": "builder", "to": "security-reviewer",
                         "payload": "diff"})
    doc["review"]["rounds"][0]["roles"].append("security-reviewer")
    root = _repo(tmp_path, graph=doc)
    with pytest.raises(graph_mod.GraphError,
                       match="'security-reviewer'.*vocabulary"):
        graph_mod.validate(graph_mod.load(root), root)


def test_review_block_lens_id_must_be_a_slug_attest_accepts(tmp_path):
    review = _review()
    review["lenses"].append({"id": "blast--radius", "checklist": ["x"]})
    root = _repo(tmp_path, review)
    with pytest.raises(graph_mod.GraphError, match="blast--radius"):
        graph_mod.load(root)


def test_declared_crew_is_none_without_a_graph_or_a_review_block(tmp_path):
    root = _repo(tmp_path, None)
    assert graph_mod.declared_crew(root) is None
    (root / "graph.yaml").unlink()
    assert graph_mod.declared_crew(root) is None


def test_declared_crew_on_an_undecodable_graph_yaml_is_a_graph_error(tmp_path):
    root = _repo(tmp_path, _review())
    (root / "graph.yaml").write_bytes(b"review: \xff\n")
    with pytest.raises(graph_mod.GraphError, match="cannot be read"):
        graph_mod.declared_crew(root)


def test_declared_crew_on_a_misspelled_review_key_fails_closed(tmp_path):
    """A document that does not validate cannot say whether it declares a crew.

    `reveiw:` is a schema error `graph validate` refuses outright, but the
    top level then carries no `review` key — so a reading taken from the raw
    bytes answers the permissive "this repo declares no crew" and `attest
    write --review-dir` skips the crew check on a graph.yaml warden itself
    rejects. The skip is the fail-open half: nobody sees it.
    """
    root = _repo(tmp_path, _review())
    doc = yaml.safe_load((root / "graph.yaml").read_text())
    doc["reveiw"] = doc.pop("review")
    (root / "graph.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    with pytest.raises(graph_mod.GraphError, match="reveiw"):
        graph_mod.declared_crew(root)


def test_declared_crew_on_an_empty_graph_yaml_fails_closed(tmp_path):
    """The same inversion in its smallest spelling: an empty graph.yaml parses
    to None, which is not a mapping and therefore says nothing at all — least
    of all that the repo declares no crew."""
    root = _repo(tmp_path, _review())
    (root / "graph.yaml").write_text("")
    with pytest.raises(graph_mod.GraphError):
        graph_mod.declared_crew(root)


def test_declared_crew_validates_the_constitution_not_only_the_review_block(
        tmp_path):
    """`declared_crew` fully validates the document, constitution included.

    The fault here is OUTSIDE the review block — a `patterns` memory edge
    typed to a judge, which re-correlates the judges the split-memory design
    keeps apart — and `graph validate` refuses it. Without the `validate`
    call, `declared_crew` returns a crew from a graph warden rejects, and
    `attest write` then checks a roster against a declaration nothing blesses.
    """
    doc = _graph(_review())
    doc["memory"] = [{"to": "cross-examiner", "type": "patterns"}]
    root = _repo(tmp_path, graph=doc)
    with pytest.raises(graph_mod.GraphError, match="patterns edge targets"):
        graph_mod.declared_crew(root)


def test_review_merge_authority_naming_no_node_is_a_named_error(tmp_path):
    """A merge_authority naming nobody is a GraphError, not a bare KeyError.

    The authority check one line below it reads `nodes[merge]`, so without
    the membership guard `warden graph validate` ends in
    `KeyError: 'nobody'` — a traceback out of a checker, which is the wrong
    exit direction rather than a refusal a reader can act on.
    """
    review = _review()
    review["merge_authority"] = "nobody"
    root = _repo(tmp_path, review)
    with pytest.raises(graph_mod.GraphError, match="nobody"):
        graph_mod.validate(graph_mod.load(root), root)


def test_review_cap_cannot_be_checked_without_a_repo_yaml(tmp_path):
    """The cap's source is repo.yaml `repair.budget`; a root without one
    cannot answer, and says which file is missing rather than reporting it as
    unreadable."""
    root = _repo(tmp_path, _review())
    (root / "repo.yaml").unlink()
    with pytest.raises(graph_mod.GraphError, match="no repo.yaml"):
        graph_mod.declared_crew(root)


def test_review_lens_id_with_a_trailing_newline_is_refused(tmp_path):
    """The schema's `^...$` is not the strict reading under Python's `re`.

    `re.search` matches `consumer-blast-radius\\n` against a pattern ending
    in `$`, so the id validates and `graph crew` prints it — and
    `fail-closed` and `fail-closed\\n` live side by side as two lenses with
    one name, each its own corpus key. `warden/attest.py` answers the same
    trap for the attestation's `$defs.lens` with `fullmatch`.
    """
    review = _review()
    review["lenses"][0]["id"] = "consumer-blast-radius\n"
    root = _repo(tmp_path, review)
    with pytest.raises(graph_mod.GraphError, match="consumer-blast-radius"):
        graph_mod.validate(graph_mod.load(root), root)


def test_review_block_role_without_reviewer_authority_fails(tmp_path):
    """A vocabulary role whose node is a gate: the authority check, not the
    vocabulary check, refuses it."""
    doc = _graph(_review())
    doc["nodes"]["builder"] = {"kind": "deterministic",
                               "impl": "warden review --base main",
                               "authority": "gate"}
    doc["review"]["rounds"][0]["roles"].append("builder")
    root = _repo(tmp_path, graph=doc)
    with pytest.raises(graph_mod.GraphError,
                       match="'builder' has authority 'gate'"):
        graph_mod.validate(graph_mod.load(root), root)


def test_review_block_needs_the_repo_root_to_check_its_cap(tmp_path):
    root = _repo(tmp_path, _review())
    with pytest.raises(graph_mod.GraphError, match="repo root"):
        graph_mod.validate(graph_mod.load(root))


def test_a_lens_dispatch_role_may_name_a_declared_lens(tmp_path):
    review = _review()
    review["rounds"][0]["roles"].append("crew:consumer-blast-radius")
    root = _repo(tmp_path, review)
    assert _crew(root, 1)["roles"][-1] == "crew:consumer-blast-radius"


def test_a_graph_without_a_review_block_still_validates_and_renders(tmp_path):
    root = _repo(tmp_path, None)
    doc = graph_mod.load(root)
    assert graph_mod.validate(doc, root) == []
    assert graph_mod.render(doc).startswith("flowchart LR")


# --- this repo -----------------------------------------------------------------


def _charter_lens_ids() -> list[str]:
    text = (ROOT / ".warden" / "skills-policy.md").read_text()
    # Anchored on the heading line: the policy quotes `## Review charter`
    # inline in other sections, and the first such mention is not the section.
    section = re.split(r"^## Review charter$", text, flags=re.M)[1]
    section = re.split(r"^## ", section, maxsplit=1, flags=re.M)[0]
    names = re.findall(r"^- \*\*([^*]+)\*\*", section, flags=re.M)
    return [re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-") for n in names]


def test_this_repos_graph_resolves_and_its_lens_ids_equal_the_charters():
    doc = graph_mod.load(ROOT)
    charter = _charter_lens_ids()
    assert charter, "the charter parse found no lens bullets"
    one = graph_mod.crew(doc, ROOT, 1)
    two = graph_mod.crew(doc, ROOT, 2)
    assert [lens["id"] for lens in one["lenses"]] == charter
    # The live protocol, as the recent attestation rosters record it.
    assert one["roles"] == ["code-reviewer", "cross-examiner"]
    assert two["roles"] == ["scoped-re-reviewer"]
    assert one["cap"] == 2
    # A charter entry that points at a rule file is a pointer lens here too.
    policy = (ROOT / ".warden" / "skills-policy.md").read_text()
    # Lenses graph.yaml points at a rule before their charter entry says so.
    # The policy is the founder's to edit, so the graph moves first and the
    # charter follows. Empty since the charter pointed consumer-blast-radius
    # at `blast-radius-named`.
    charter_lags_graph: set[str] = set()
    for lens in one["lenses"]:
        if "rule" not in lens:
            continue
        if lens["id"] in charter_lags_graph:
            # Strict the other way: once the founder's edit names the rule
            # file, this fails until the id is dropped from the set.
            assert f"`{lens['rule']}`" not in policy, (
                f"the charter now points {lens['id']} at {lens['rule']}; "
                "drop it from charter_lags_graph")
            continue
        assert f"`{lens['rule']}`" in policy, lens


def test_every_lens_rule_pointer_names_a_rule_that_covers_the_lens():
    """A lens `rule:` in this repo's graph.yaml names a file under
    `.warden/rules/` that exists, and that rule either IS the lens's id or
    lists it in `covers:`, and is not paused — the condition under which
    `attest write` refuses `unmapped:<the lens's slug>` and the lens's
    findings land on the rule. A pointer at a rule that does not cover the lens would send
    reviewers to file under a rule while the unmapped id still resolved,
    splitting one class across two corpus keys."""
    from warden.rules import load_rules

    rules_dir = ROOT / ".warden" / "rules"
    rules = {rule.id: rule for rule in load_rules(rules_dir)}
    pointers = [lens for lens in graph_mod.load(ROOT)["review"]["lenses"]
                if "rule" in lens]
    assert pointers, "graph.yaml declares no rule-pointer lens — this reads nothing"
    for lens in pointers:
        assert (rules_dir / f"{lens['rule']}.md").is_file(), lens
        rule = rules[lens["rule"]]
        # A PAUSED rule's covered slug is a live candidate key again, so
        # `unmapped:<the lens's slug>` resolves and the pointer misleads.
        assert not rule.paused, f"lens {lens['id']!r} points at paused {rule.id!r}"
        assert lens["id"] == rule.id or lens["id"] in rule.covers, (
            f"lens {lens['id']!r} points at {rule.id!r}, which does not "
            f"cover it (covers: {list(rule.covers)})")
    ids = {lens["id"]: lens.get("rule") for lens in pointers}
    assert ids.get("consumer-blast-radius") == "blast-radius-named"


def test_review_lenses_are_not_checked_against_an_unresolvable_rules_dir(tmp_path):
    """A `rules_dir` declaration that EXISTS and cannot be resolved must stop
    the lens check, not fall through to the default dir.

    `declared_rules_dir` is the platform's one derivation and it answers
    (default, problem): the default is for naming the fallback in messages,
    never an answer to build on. Ignoring `problem` here would check every
    `rule:` pointer against `.warden/rules` for a repo that declares
    `policy/rules` — a pointer that resolves against the wrong ruleset is a
    lens blessed as enforced by a file the gate never reads.
    """
    root = _repo(tmp_path, _review())
    # A mapping (so the cap is readable) whose `review.rules_dir` is declared
    # and unresolvable — one indent error from valid.
    (root / "repo.yaml").write_text(
        "repair:\n  budget: 2\nreview:\n  rules_dir: ['a', 'b']\n")
    with pytest.raises(graph_mod.GraphError,
                       match="review.lenses cannot be checked"):
        graph_mod.crew(graph_mod.load(root), root, 1)


# --- declares_review: the narrow question, asked of the document -----------


def test_declares_review_answers_from_the_keys_not_from_validation(tmp_path):
    """Whether a repo DECLARES a crew is a question about the document's
    KEYS, and a document that parses can answer it while failing every other
    check warden makes of it.

    This is the narrow trigger `warden ship` reads. `declared_crew` stays
    strict — a declared block that does not resolve raises there, and that
    strictness is the hole its docstring exists to close — but a repo that
    declares nothing must never be refused for a graph it made no claim
    with. A non-mapping document carries no key either, so it declares none.
    """
    root = _repo(tmp_path, _review())
    assert graph_mod.declares_review(root) is True
    (root / "graph.yaml").write_text("version: 1\nnodes: {}\n")
    assert graph_mod.declares_review(root) is False
    with pytest.raises(graph_mod.GraphError):
        graph_mod.declared_crew(root)          # still invalid, still refused
    (root / "graph.yaml").write_text(
        "version: 1\nnodes: {}\nreview:\n  rounds: []\n")
    assert graph_mod.declares_review(root) is True
    (root / "graph.yaml").write_text("- not\n- a mapping\n")
    assert graph_mod.declares_review(root) is False


def test_declares_review_is_false_where_there_is_no_graph_at_all(tmp_path):
    """No graph.yaml is the commonest consumer state (`examples/hello-svc`
    is exactly that) and it declares no crew."""
    root = _repo(tmp_path, _review())
    (root / "graph.yaml").unlink()
    assert graph_mod.declares_review(root) is False


@pytest.mark.parametrize("raw", [b"\xff\xfe\x00bad", b"version: 1\nnodes: {\n"],
                         ids=["undecodable", "unparseable"])
def test_declares_review_raises_for_a_document_it_cannot_read(tmp_path, raw):
    """The one file that cannot answer. A document warden cannot decode or
    parse cannot be asked which keys it carries, so "could not say" is raised
    rather than returned as the permissive "declares none"."""
    root = _repo(tmp_path, _review())
    (root / "graph.yaml").write_bytes(raw)
    with pytest.raises(graph_mod.GraphError, match="cannot be read"):
        graph_mod.declares_review(root)
