"""Graph layer: schema, constitution checks, mermaid render, manifest stamp.
Design of record: docs/design/graph.md."""

import shutil
from pathlib import Path

import pytest
import yaml

from warden import graph as graph_mod
from warden import runs

ROOT = Path(__file__).parent.parent
GOLDEN = ROOT / "tests" / "golden"


def load_fixture(tmp_path: Path, name="distill-graph.yaml") -> dict:
    shutil.copy(GOLDEN / name, tmp_path / "graph.yaml")
    return graph_mod.load(tmp_path)


def write_graph(tmp_path: Path, doc: dict) -> Path:
    (tmp_path / "graph.yaml").write_text(yaml.safe_dump(doc))
    return tmp_path


# ---------- the two real graphs ----------------------------------------------

def test_own_graph_validates_selfhosting():
    """The platform's own org must clear the bar it sets for consumers.

    It holds because the topology declares a genuinely separate
    cross-examiner, not because the check is loose: a graph declaring a
    single-verifier loop gets a warning from validate.
    """
    doc = graph_mod.load(ROOT)  # this repo's own graph.yaml
    # With the root: the review block's cap is checked against repo.yaml.
    assert graph_mod.validate(doc, ROOT) == [], "the platform's own graph warns"


def test_own_graph_declares_the_two_judges_as_decorrelated():
    """The whole value of a second judge is that it is not the first one.

    A cross-examiner fed the same priors as the reviewer converges for
    reasons unrelated to the diff, and the graph would still validate — so
    pin the disjointness, not merely the presence of two nodes.
    """
    doc = graph_mod.load(ROOT)
    assert doc["verification"]["independent_judges"] == ["cross-examiner"]
    feeds = {e["to"]: e["type"] for e in doc.get("memory", [])}
    assert feeds == {"code-reviewer": "patterns",
                     "cross-examiner": "precedents"}, (
        f"the split-memory feeds are not disjoint: {feeds}")
    assert doc["nodes"]["cross-examiner"]["authority"] == "judge"


def test_distill_fixture_validates_with_full_topology(tmp_path):
    doc = load_fixture(tmp_path)
    warnings = graph_mod.validate(doc)
    assert warnings == []  # judges + typed memory: nothing to warn about
    mermaid = graph_mod.render(doc)
    assert mermaid.startswith("flowchart LR")
    assert 'founder(["founder\\nhuman"])' in mermaid          # human = stadium
    assert "gate[[" in mermaid                                # deterministic
    assert "memory -. patterns .-> code_reviewer" in mermaid  # typed, dashed
    assert "memory -. precedents .-> cross_examiner" in mermaid
    # quoted on purpose: bare parens in an edge label break the parser
    assert '-->|"attestation (attestation.json)"|' in mermaid


# ---------- constitution violations ------------------------------------------

def base_doc() -> dict:
    return {
        "version": 1,
        "nodes": {
            "builder": {"kind": "skill", "impl": "x", "authority": "find"},
            "judge": {"kind": "subagent", "impl": "y", "authority": "judge"},
            "founder": {"kind": "human", "authority": "merge"},
        },
        "edges": [
            {"from": "builder", "to": "judge", "payload": "findings"},
            {"from": "judge", "to": "founder", "payload": "pr"},
        ],
    }


@pytest.mark.parametrize("mutate, fragment", [
    (lambda d: d["nodes"]["founder"].update(kind="subagent", impl="bot"),
     "merges are human-only"),
    (lambda d: d["nodes"]["founder"].update(authority="gate"),
     "no merge-authority node"),
    (lambda d: d["edges"].append({"from": "builder", "to": "ghost",
                                  "payload": "diff"}),
     "undeclared node 'ghost'"),
    (lambda d: d["edges"].append({"from": "builder", "to": "builder",
                                  "payload": "diff"}),
     "self-edge"),
    (lambda d: d.update(memory=[{"to": "judge", "type": "patterns"}]),
     "patterns feed only finders"),
    (lambda d: d.update(memory=[{"to": "builder", "type": "precedents"}]),
     "precedents feed only judges"),
    (lambda d: d["nodes"]["builder"].pop("impl"),
     "declares no impl"),
    (lambda d: d["nodes"].update(gate={"kind": "deterministic",
                                       "impl": "warden 'unclosed",
                                       "authority": "gate"}),
     "does not parse"),
    (lambda d: d.update(verification={"independent_judges": ["builder"]}),
     "not 'judge'"),
    # regression: validate and render must agree — impl/artifact
    # strings that break mermaid are rejected, not rendered broken
    (lambda d: d["nodes"]["builder"].update(impl='run "the thing"'),
     "mermaid"),
    (lambda d: d["edges"][0].update(artifact="a|b.json"),
     "mermaid"),
    # regression: 'memory' collides with the render's pseudo-node
    (lambda d: d["nodes"].update(memory={"kind": "subagent", "impl": "m",
                                         "authority": "judge"}),
     "reserved"),
    # regression: two nodes claiming one command = ambiguous stamp
    (lambda d: d["nodes"].update(
        g1={"kind": "deterministic", "impl": "warden review --base a",
            "authority": "gate"},
        g2={"kind": "deterministic", "impl": "warden review --base b",
            "authority": "gate"}),
     "ambiguous"),
])
def test_constitution_violations_raise(tmp_path, mutate, fragment):
    doc = base_doc()
    mutate(doc)
    root = write_graph(tmp_path, doc)
    with pytest.raises(graph_mod.GraphError) as exc:
        graph_mod.validate(graph_mod.load(root))
    assert fragment in str(exc.value)


def test_schema_rejects_retry_without_max(tmp_path):
    doc = base_doc()
    doc["verification"] = {"failure": {"builder": {"policy": "retry"}}}
    root = write_graph(tmp_path, doc)
    with pytest.raises(graph_mod.GraphError, match="schema"):
        graph_mod.load(root)


def test_missing_graph_is_a_clean_error(tmp_path):
    with pytest.raises(graph_mod.GraphError, match="no graph.yaml"):
        graph_mod.load(tmp_path)


def test_unreadable_graph_never_blocks_the_stamp(tmp_path):
    # regression: a PermissionError from graph.yaml must not crash
    # write_manifest — the graph never blocks the tools it observes
    path = tmp_path / "graph.yaml"
    path.write_text("version: 1\n")
    path.chmod(0o000)
    try:
        assert graph_mod.node_for_cmd(tmp_path, "review") is None
    finally:
        path.chmod(0o644)


def test_schema_rejects_max_on_abort(tmp_path):
    doc = base_doc()
    doc["verification"] = {"failure": {"builder": {"policy": "abort", "max": 5}}}
    root = write_graph(tmp_path, doc)
    with pytest.raises(graph_mod.GraphError, match="schema"):
        graph_mod.load(root)


def test_orphan_node_and_missing_memory_warn(tmp_path):
    doc = base_doc()
    doc["nodes"]["bystander"] = {"kind": "subagent", "impl": "z",
                                 "authority": "find"}
    doc["verification"] = {"independent_judges": ["judge"]}
    root = write_graph(tmp_path, doc)
    warnings = graph_mod.validate(graph_mod.load(root))
    assert any("bystander" in w for w in warnings)
    assert any("split-memory" in w for w in warnings)


# ---------- manifest stamping ------------------------------------------------

def test_node_for_cmd_and_manifest_stamp(tmp_path):
    doc = base_doc()
    doc["nodes"]["gate"] = {"kind": "deterministic",
                            "impl": "warden review --base origin/main",
                            "authority": "gate"}
    doc["edges"].append({"from": "builder", "to": "gate", "payload": "diff"})
    root = write_graph(tmp_path, doc)
    assert graph_mod.node_for_cmd(root, "review") == "gate"
    assert graph_mod.node_for_cmd(root, "verify") is None
    assert graph_mod.node_for_cmd(tmp_path / "nowhere", "review") is None

    run_dir = runs.create_run_dir(root, "review")
    manifest_path = runs.write_manifest(run_dir, root=root, cmd="review",
                                        rules_version="v1", exit_status="ok")
    import json
    assert json.loads(manifest_path.read_text())["graph_node"] == "gate"
    # a command no node claims carries no stamp
    run_dir2 = runs.create_run_dir(root, "explain")
    manifest2 = runs.write_manifest(run_dir2, root=root, cmd="explain",
                                    rules_version="v1", exit_status="ok")
    assert "graph_node" not in json.loads(manifest2.read_text())


# ---------- escalation, caps, and consumers (graph consumers) --------------

def test_escalation_needs_a_declared_target(tmp_path):
    doc = base_doc()
    doc["verification"] = {"failure": {"builder": {"policy": "escalate",
                                                   "to": "ghost"}}}
    with pytest.raises(graph_mod.GraphError, match="undeclared node 'ghost'"):
        graph_mod.validate(graph_mod.load(write_graph(tmp_path, doc)))


def test_escalating_to_yourself_is_a_retry(tmp_path):
    doc = base_doc()
    doc["verification"] = {"failure": {"builder": {"policy": "escalate",
                                                   "to": "builder"}}}
    with pytest.raises(graph_mod.GraphError, match="escalates to itself"):
        graph_mod.validate(graph_mod.load(write_graph(tmp_path, doc)))


def test_schema_requires_a_target_for_escalate(tmp_path):
    doc = base_doc()
    doc["verification"] = {"failure": {"builder": {"policy": "escalate"}}}
    with pytest.raises(graph_mod.GraphError, match="schema"):
        graph_mod.load(write_graph(tmp_path, doc))


def test_caps_must_bound_the_parts(tmp_path):
    doc = base_doc()
    doc["verification"] = {
        "caps": {"total_retries": 3},
        "failure": {"builder": {"policy": "retry", "max": 2},
                    "judge": {"policy": "retry", "max": 5}}}
    with pytest.raises(graph_mod.GraphError, match="the cap must bound the parts"):
        graph_mod.validate(graph_mod.load(write_graph(tmp_path, doc)))


def test_escalation_cycle_is_refused(tmp_path):
    doc = base_doc()
    doc["nodes"]["second"] = {"kind": "subagent", "impl": "s", "authority": "judge"}
    doc["edges"].append({"from": "judge", "to": "second", "payload": "findings"})
    doc["verification"] = {
        "caps": {"escalation_depth": 5},
        "failure": {"judge": {"policy": "escalate", "to": "second"},
                    "second": {"policy": "escalate", "to": "judge"}}}
    with pytest.raises(graph_mod.GraphError, match="escalation cycle"):
        graph_mod.validate(graph_mod.load(write_graph(tmp_path, doc)))


def test_escalation_cycle_is_refused_without_any_depth_cap(tmp_path):
    """A two-node escalate cycle never terminates whether or not a budget
    is declared, so the cycle check is unconditional: guarding the walk by
    the cap's presence would give exactly the graphs that declare no budget
    no termination check. The depth
    check stays cap-guarded (no cap, no depth bound to enforce); the CYCLE
    check does not."""
    doc = base_doc()
    doc["nodes"]["second"] = {"kind": "subagent", "impl": "s", "authority": "judge"}
    doc["edges"].append({"from": "judge", "to": "second", "payload": "findings"})
    doc["verification"] = {
        "failure": {"judge": {"policy": "escalate", "to": "second"},
                    "second": {"policy": "escalate", "to": "judge"}}}
    with pytest.raises(graph_mod.GraphError, match="escalation cycle"):
        graph_mod.validate(graph_mod.load(write_graph(tmp_path, doc)))


def test_deep_chain_without_cap_validates(tmp_path):
    """The other half, pinned: an acyclic chain with
    no declared cap has no depth bound to enforce — it must validate."""
    doc = base_doc()
    doc["nodes"]["second"] = {"kind": "subagent", "impl": "s", "authority": "judge"}
    doc["edges"].append({"from": "judge", "to": "second", "payload": "findings"})
    doc["verification"] = {
        "failure": {"builder": {"policy": "escalate", "to": "judge"},
                    "judge": {"policy": "escalate", "to": "second"}}}
    graph_mod.validate(graph_mod.load(write_graph(tmp_path, doc)))


def test_skill_impl_resolves_against_this_repos_marketplace():
    # nightgate IS a marketplace, so a skill node naming a real pack skill
    # must resolve, and a fictional one must warn.
    inv = graph_mod._local_skill_inventory(ROOT)
    assert inv and "nightgate-skills:pre-pr-review" in inv


def test_unresolvable_skill_warns_only_where_resolution_is_possible(tmp_path):
    doc = base_doc()
    doc["nodes"]["builder"] = {"kind": "skill", "impl": "ghost-plugin:ghost",
                               "authority": "find"}
    root = write_graph(tmp_path, doc)
    # no marketplace in tmp_path -> silence is honest, not a false all-clear
    assert not any("not in this repo's marketplace" in w
                   for w in graph_mod.validate(graph_mod.load(root), root))


def test_autonomous_run_records_its_node_and_cage_ledgers_it():
    skill = (ROOT / "skills" / "nightgate-skills" / "skills" / "autonomous-run"
             / "SKILL.md").read_text()
    # the doc wraps mid-phrase; assert on the durable tokens
    assert "`node: <the" in skill and "graph.yaml node you ran as" in skill
    runner = (ROOT / "cage" / "run.sh").read_text()
    assert "S_NODE=$(grep -m1 '^node:'" in runner
    assert '"$S_NOTES" "$S_NODE"' in runner


def test_edge_labels_with_artifacts_render_as_parseable_mermaid():
    """`(` and `)` inside an unquoted edge label break the mermaid parser.

    Every edge that names an artifact carries one, so an unquoted label makes
    `graph render` emit output that cannot be drawn — while the docs promise
    validate and render agree by construction.
    """
    doc = {
        "version": 1,
        "nodes": {"a": {"kind": "skill", "impl": "p:s", "authority": "find"},
                  "b": {"kind": "human", "authority": "merge"}},
        "edges": [{"from": "a", "to": "b", "payload": "pr",
                   "artifact": "review-findings.json"}],
    }
    mermaid = graph_mod.render(doc)
    label = [ln for ln in mermaid.splitlines() if "-->" in ln][0]
    assert '|"pr (review-findings.json)"|' in label, (
        f"artifact label is unquoted and will not parse: {label}")


def test_node_labels_carrying_parens_still_render_quoted():
    """Node labels are quoted by the shape templates — pin that, so the
    edge-label quoting cannot be "helpfully" generalized into double quotes."""
    doc = {
        "version": 1,
        "nodes": {"a": {"kind": "deterministic",
                        "impl": "warden review (base origin/main)",
                        "authority": "gate"}},
        "edges": [],
    }
    line = [ln for ln in graph_mod.render(doc).splitlines() if "a[[" in ln][0]
    assert line.count('"') == 2, f"node label quoting is wrong: {line}"


def test_own_graph_renders_parseable_mermaid():
    """The platform's own graph must survive its own renderer."""
    mermaid = graph_mod.render(graph_mod.load(ROOT))
    for line in mermaid.splitlines():
        if "-->|" in line:
            body = line.split("-->|", 1)[1].rsplit("|", 1)[0]
            assert body.startswith('"') and body.endswith('"'), (
                f"unquoted edge label: {line}")
