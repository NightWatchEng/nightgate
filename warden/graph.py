"""Declared agent-organization graph.

Design of record: docs/design/graph.md. "Graph engineering" = the agent
ORGANIZATION as declared config, not prose in skills. The runtime is Claude
Code sessions — this layer is config + interpretation, never a framework.

graph.yaml (per consuming repo, at the root): nodes (role -> kind/impl/
authority/boundaries), edges (typed payloads with artifact names), TYPED
memory edges (patterns vs precedents — the split-memory design), and
verification (independent judges, convergence rules, per-node failure
policy).

`warden graph validate` checks structure AND the org's constitution:
- every merge-authority node is human ("humans hold every merge"),
- memory typing matches the split (patterns -> finders, precedents ->
  judges — disjoint priors decorrelate the judges),
- deterministic impls parse as commands; non-human nodes declare an impl,
- edge/memory/verification references resolve to declared nodes,
- the terminal closure round (`review.closure`) dispatches one finder or
  judge that no capped round already dispatches — it re-files what the
  capped rounds raised and buys no review budget,
- a delegation of merge authority's EXECUTION is granted by the authority
  holder alone (anything else is self-issue) and held by no node that
  already holds the button. The block declares TERMS and cannot hold a
  grant: `warden graph authority` answers who may merge right now.
`warden graph render` emits mermaid. Manifests stamp the node whose
deterministic impl matches the running command — org-chart telemetry.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from . import yamlio

GRAPH_FILENAME = "graph.yaml"

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "graph.schema.json"
_validator = Draft202012Validator(json.loads(_SCHEMA_PATH.read_text()))


class GraphError(Exception):
    pass


class CrewPastCap(GraphError):
    """A round past the declared cap: a well-formed question whose answer is
    no. `warden graph crew` exits 1 on it, and 2 on every other GraphError."""


def load(root: Path) -> dict:
    path = root / GRAPH_FILENAME
    if not path.is_file():
        raise GraphError(f"no {GRAPH_FILENAME} at {root} — the org graph is "
                         "declared config; see docs/design/graph.md")
    try:
        doc = yamlio.load(path.read_text())
    except yaml.YAMLError as exc:
        raise GraphError(f"invalid YAML in {path}: {exc}") from exc
    errors = sorted(_validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(f"{'/'.join(str(p) for p in e.path) or '<root>'}: "
                           f"{e.message[:120]}" for e in errors[:5])
        raise GraphError(f"graph.yaml schema: {detail}")
    return doc


def _local_skill_inventory(root: Path) -> set[str] | None:
    """`plugin:skill` ids this repo itself serves, or None if it serves none.

    Resolution is only possible for a marketplace living in this repo —
    user-scope installs are invisible to a CLI run, and pretending otherwise
    would turn an unverifiable claim into a green check.
    """
    manifest = root / ".claude-plugin" / "marketplace.json"
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    found: set[str] = set()
    for plugin in data.get("plugins", []):
        source = plugin.get("source", "")
        name = plugin.get("name", "")
        skills_dir = (root / source / "skills") if source.startswith("./") else None
        if skills_dir and skills_dir.is_dir():
            for d in skills_dir.iterdir():
                if (d / "SKILL.md").is_file():
                    found.add(f"{name}:{d.name}")
    return found or None


def validate(doc: dict, root: Path | None = None) -> list[str]:
    """Semantic validation beyond the schema. Returns advisory warnings;
    raises GraphError on violations of the org's constitution."""
    nodes = doc["nodes"]
    if "memory" in nodes:
        raise GraphError("node name 'memory' is reserved — the render's typed "
                         "memory edges use it as the store pseudo-node")

    def require_node(name: str, where: str) -> None:
        if name not in nodes:
            raise GraphError(f"{where} references undeclared node '{name}'")

    def require_renderable(value: str, where: str) -> None:
        # validate and render must agree: a graph validate blesses, render
        # must emit parseable mermaid — so reject the chars that break
        # quoted labels and edge tags.
        if any(c in value for c in '"|\n'):
            raise GraphError(f"{where} must not contain double quotes, pipes, "
                             f"or newlines (breaks the mermaid render): {value!r}")

    for i, edge in enumerate(doc["edges"]):
        require_node(edge["from"], f"edges[{i}].from")
        require_node(edge["to"], f"edges[{i}].to")
        if edge["from"] == edge["to"]:
            raise GraphError(f"edges[{i}]: self-edge on '{edge['from']}' — "
                             "iteration inside one node is a loop, not an edge")
        if edge.get("artifact"):
            require_renderable(edge["artifact"], f"edges[{i}].artifact")

    # Humans hold every merge — day-one principle, enforced in config.
    merge_nodes = [n for n, spec in nodes.items() if spec["authority"] == "merge"]
    if not merge_nodes:
        raise GraphError("no merge-authority node — someone must hold the button")
    for name in merge_nodes:
        if nodes[name]["kind"] != "human":
            raise GraphError(f"node '{name}' holds merge authority but is "
                             f"kind '{nodes[name]['kind']}' — merges are human-only")

    claimed_cmds: dict[str, str] = {}
    for name, spec in nodes.items():
        if spec["kind"] != "human" and not spec.get("impl"):
            raise GraphError(f"node '{name}' ({spec['kind']}) declares no impl")
        if spec.get("impl"):
            require_renderable(spec["impl"], f"node '{name}'.impl")
        if spec["kind"] == "deterministic":
            try:
                tokens = shlex.split(spec["impl"])
            except ValueError as exc:
                raise GraphError(f"node '{name}': impl does not parse as a "
                                 f"command: {exc}") from exc
            if not tokens:
                raise GraphError(f"node '{name}': empty deterministic impl")
            # two nodes claiming one warden command would make the manifest
            # graph_node stamp ambiguous — misdeclared org, fail closed
            if len(tokens) >= 2 and tokens[0] == "warden":
                cmd = tokens[1]
                if cmd in claimed_cmds:
                    raise GraphError(
                        f"nodes '{claimed_cmds[cmd]}' and '{name}' both claim "
                        f"'warden {cmd}' — the manifest stamp would be ambiguous")
                claimed_cmds[cmd] = name

    # Split memory is TYPED and role-matched: patterns feed finders,
    # precedents feed judges. Anything else re-correlates the judges.
    for i, m in enumerate(doc.get("memory", [])):
        require_node(m["to"], f"memory[{i}].to")
        authority = nodes[m["to"]]["authority"]
        want = "find" if m["type"] == "patterns" else "judge"
        if authority != want:
            raise GraphError(
                f"memory[{i}]: {m['type']} edge targets '{m['to']}' "
                f"(authority {authority}) — {m['type']} feed only "
                f"{'finders' if want == 'find' else 'judges'}")

    verification = doc.get("verification", {})
    for j in verification.get("independent_judges", []):
        require_node(j, "verification.independent_judges")
        if nodes[j]["authority"] != "judge":
            raise GraphError(f"independent judge '{j}' has authority "
                             f"'{nodes[j]['authority']}', not 'judge'")
    caps = verification.get("caps", {})
    total_retries = 0
    for name, policy in verification.get("failure", {}).items():
        require_node(name, "verification.failure")
        if policy["policy"] == "escalate":
            require_node(policy["to"], f"verification.failure.{name}.to")
            if policy["to"] == name:
                raise GraphError(f"verification.failure.{name}: escalates to "
                                 "itself — that is a retry, not an escalation")
        total_retries += int(policy.get("max", 0))
    # A per-node budget that exceeds the org's stated cap is not a budget.
    if caps.get("total_retries") and total_retries > caps["total_retries"]:
        raise GraphError(
            f"per-node retries sum to {total_retries} but "
            f"verification.caps.total_retries is {caps['total_retries']} — "
            "the cap must bound the parts, or it bounds nothing")
    # The cycle walk is UNCONDITIONAL: a cycle never terminates whether or
    # not a budget is declared, and a cap-guarded walk would exempt exactly
    # the graphs that declare no budget. Only the
    # depth-vs-cap comparison needs a cap to compare against.
    depth_cap = caps.get("escalation_depth")
    chains = {n: p["to"] for n, p in verification.get("failure", {}).items()
              if p["policy"] == "escalate"}
    for start in chains:
        seen, node, depth = {start}, chains[start], 1
        while node in chains:
            if node in seen:
                raise GraphError(
                    f"verification.failure: escalation cycle through "
                    f"'{node}' — escalation must terminate at a node that "
                    "does not escalate")
            seen.add(node)
            node, depth = chains[node], depth + 1
        if depth_cap and depth > depth_cap:
            raise GraphError(
                f"escalation from '{start}' is {depth} deep but "
                f"caps.escalation_depth is {depth_cap}")

    # The review block is optional and additive; when declared, every way it
    # can be wrong is a GraphError, the same as the constitution above.
    if "review" in doc:
        resolve_review(doc, root)

    warnings = []
    installed = _local_skill_inventory(root) if root else None
    for name, spec in nodes.items():
        # v1 narrowing (documented in docs/design/graph.md): skill existence
        # can't be resolved from here (plugin installs are user-scope) — we
        # check the reference FORM and warn on prose.
        if spec["kind"] == "skill":
            impl = spec.get("impl", "")
            if not re.match(r"^[a-z0-9-]+(:[a-z0-9-]+)?$", impl):
                warnings.append(
                    f"node '{name}': skill impl is prose, not a resolvable "
                    f"skill reference (expected plugin:skill or skill-name)")
            elif installed is not None and impl not in installed:
                # Only claimable where the marketplace is IN this repo; a
                # user-scope install is invisible from here, so silence
                # elsewhere is honest rather than a false all-clear.
                warnings.append(
                    f"node '{name}': skill '{impl}' is not in this repo's "
                    f"marketplace ({', '.join(sorted(installed)) or 'none'})")
    judges = [n for n, s in nodes.items() if s["authority"] == "judge"]
    if not verification.get("independent_judges"):
        warnings.append(
            "no independent_judges declared — single-verifier flows should "
            "stay loops, not graphs (design boundary)")
    unreachable = set(nodes) - {e["to"] for e in doc["edges"]} \
        - {e["from"] for e in doc["edges"]}
    if unreachable:
        warnings.append(f"node(s) on no edge: {', '.join(sorted(unreachable))}")
    if judges and not doc.get("memory"):
        warnings.append("judges declared but no typed memory edges — the "
                        "split-memory decorrelation is not wired")
    return warnings


_SHAPES = {  # mermaid node shape by kind
    "skill": ('["{label}"]'),
    "subagent": ('("{label}")'),
    "human": ('(["{label}"])'),
    "deterministic": ('[["{label}"]]'),
}


def _mermaid_id(name: str) -> str:
    return name.replace("-", "_")


def render(doc: dict) -> str:
    """Mermaid flowchart of the declared org."""
    lines = ["flowchart LR"]
    for name, spec in doc["nodes"].items():
        label = f"{name}\\n{spec['kind']}"
        if spec.get("impl"):
            label += f": {spec['impl']}"
        # node labels are already quoted by the shape templates above
        lines.append(f"  {_mermaid_id(name)}{_SHAPES[spec['kind']].format(label=label)}")
    for edge in doc["edges"]:
        tag = edge["payload"]
        if edge.get("artifact"):
            tag += f" ({edge['artifact']})"
        # Quote the label: an artifact name puts parens inside it, and mermaid
        # reads a bare `(` in an edge label as the start of a node shape. The
        # renders that broke were exactly the edges carrying evidence.
        lines.append(f'  {_mermaid_id(edge["from"])} -->|"{tag}"| '
                     f'{_mermaid_id(edge["to"])}')
    if doc.get("memory"):
        lines.append('  memory[("review memory")]')
        for m in doc["memory"]:
            lines.append(f"  memory -. {m['type']} .-> {_mermaid_id(m['to'])}")
    return "\n".join(lines) + "\n"


_LENS_ROLE_PREFIX = "crew:"
_REVIEWER_AUTHORITIES = ("find", "judge")

# The lens id pattern, read from the schema and matched with `fullmatch`.
# NOT through the schema's own `$`, which is end of input under ECMA-262 —
# what the contract means — but under Python's `re` also matches BEFORE a
# trailing newline. So jsonschema accepts 'fail-closed\n' beside
# 'fail-closed' as two lenses with one name, each its own corpus key, and the
# duplicate check in `resolve_review` cannot see them as one. `warden/attest
# .py` documents the same trap for the attestation's `$defs.lens` and answers
# it the same way; this is that answer applied to the ids the review block
# declares.
_LENS_ID_PATTERN = (_validator.schema["properties"]["review"]["properties"]
                    ["lenses"]["items"]["properties"]["id"]["pattern"])
_LENS_ID = re.compile(_LENS_ID_PATTERN)


def _repo_policy(root: Path) -> dict:
    # Through the platform's one repo.yaml reader, never by name here.
    from . import config as config_mod
    try:
        raw = config_mod.read_repo_yaml(root)
    except FileNotFoundError as exc:
        raise GraphError(f"review.cap cannot be checked: no repo.yaml at "
                         f"{root}, so there is no repair.budget to agree "
                         "with") from exc
    except OSError as exc:
        raise GraphError(f"review.cap cannot be checked: repo.yaml is "
                         f"unreadable: {exc}") from exc
    try:
        doc = yamlio.load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise GraphError(f"review.cap cannot be checked: repo.yaml is "
                         f"unreadable: {exc}") from exc
    if not isinstance(doc, dict):
        raise GraphError("review.cap cannot be checked: repo.yaml is not a "
                         "mapping")
    return doc


def resolve_review(doc: dict, root: Path | None) -> dict:
    """Check the `review` block and return its resolved reading.

    Returns `{"cap", "rounds": {n: [roles]}, "lenses", "merge_authority",
    "closure", "delegation"}`, each lens either `{"id", "checklist"}` or
    `{"id", "rule": <path>}`, `closure` the terminal closure round's checked
    declaration or None, and `delegation` the checked terms or None.
    Raises GraphError, naming the cause, when the block is absent or wrong:
    a role that is not a declared finder or judge (or `crew:<id>` naming no
    declared lens), a rule pointer at a missing file, a cap that disagrees
    with repo.yaml `repair.budget` (the cap's one source), a round past the
    cap or a round within it with no crew, a merge node without merge
    authority, and delegation terms whose granter is not that merge node or
    whose eligible list names an undeclared one. The root is required: the
    cap and the pointers are checked against files, and a check that cannot
    reach them does not pass.
    """
    review = doc.get("review")
    if review is None:
        raise GraphError("graph.yaml declares no `review` block, so there is "
                         "no declared crew to resolve — add `review:` with "
                         "rounds, cap, lenses and merge_authority "
                         "(docs/wiki/Graph-Layer.md)")
    if root is None:
        raise GraphError("the `review` block needs the repo root: its cap is "
                         "checked against repo.yaml repair.budget and its "
                         "rule pointers against the rules dir")
    nodes = doc["nodes"]
    policy = _repo_policy(root)

    # The rules dir from the platform's one derivation, so a pointer is
    # checked in the dir the gate and attest read.
    from . import config as config_mod
    rules_path, problem = config_mod.declared_rules_dir(root)
    if problem:
        raise GraphError(f"review.lenses cannot be checked: {problem}")
    lenses: list[dict] = []
    for i, lens in enumerate(review["lenses"]):
        lens_id = lens["id"]
        if not _LENS_ID.fullmatch(lens_id):
            raise GraphError(
                f"review.lenses[{i}]: lens id {lens_id!r} is not a slug as "
                f"warden reads one ({_LENS_ID_PATTERN}) — the schema's `$` "
                "also matches before a trailing newline under Python's re, so "
                "an invisible suffix would declare a SECOND lens wearing an "
                "existing lens's name")
        if any(seen["id"] == lens_id for seen in lenses):
            raise GraphError(f"review.lenses[{i}]: lens '{lens_id}' is "
                             "declared twice")
        if "rule" in lens:
            target = rules_path / f"{lens['rule']}.md"
            try:
                rel = target.relative_to(root).as_posix()
            except ValueError:
                rel = str(target)
            if not target.is_file():
                raise GraphError(
                    f"review.lenses[{i}]: lens '{lens_id}' points at {rel}, "
                    "which does not exist — a pointer at a missing rule leaves "
                    "the lens enforced by nothing")
            lenses.append({"id": lens_id, "rule": rel})
        else:
            lenses.append({"id": lens_id, "checklist": list(lens["checklist"])})
    lens_ids = [lens["id"] for lens in lenses]

    cap = review["cap"]
    repair = policy.get("repair")
    budget = repair.get("budget") if isinstance(repair, dict) else None
    if isinstance(budget, bool) or not isinstance(budget, int):
        raise GraphError(
            f"review.cap {cap} cannot be checked: repo.yaml declares no integer "
            f"repair.budget (got {budget!r}), and that key is the cap's source")
    if cap != budget:
        raise GraphError(
            f"review.cap {cap} disagrees with repo.yaml repair.budget {budget} "
            "— the round cap has one source; make the two equal")

    # A declared role must also be a roster role attest write accepts under
    # --review-dir, or graph validate would bless a crew no attestation of it
    # could ever carry. One vocabulary, read from the attestation schema.
    from .attest import lens_is_valid, _lens_vocabulary_hint

    rounds: dict[int, list[str]] = {}
    for i, entry in enumerate(review["rounds"]):
        n = entry["round"]
        where = f"review.rounds[{i}] (round {n})"
        if n in rounds:
            raise GraphError(f"{where}: round {n} is declared twice")
        if n > cap:
            raise GraphError(f"{where}: round {n} is past the cap of {cap} — "
                             "no round runs past it, so none declares a crew")
        roles: list[str] = []
        for role in entry["roles"]:
            if role in roles:
                raise GraphError(f"{where}: role '{role}' is listed twice")
            if not lens_is_valid(role):
                raise GraphError(
                    f"{where}: role '{role}' is outside the attestation's role "
                    f"vocabulary ({_lens_vocabulary_hint()}), so no attest "
                    "write --review-dir roster could ever carry it")
            if role.startswith(_LENS_ROLE_PREFIX):
                if role[len(_LENS_ROLE_PREFIX):] not in lens_ids:
                    raise GraphError(
                        f"{where}: role '{role}' names no declared lens "
                        f"(review.lenses: {', '.join(lens_ids)})")
            elif role not in nodes:
                raise GraphError(f"{where}: role '{role}' is not a declared "
                                 "node")
            elif nodes[role]["authority"] not in _REVIEWER_AUTHORITIES:
                raise GraphError(
                    f"{where}: role '{role}' has authority "
                    f"'{nodes[role]['authority']}' — a review round dispatches "
                    "finders and judges, never a gate or a merge")
            roles.append(role)
        rounds[n] = roles
    for n in range(1, cap + 1):
        if n not in rounds:
            raise GraphError(
                f"review.rounds declares no crew for round {n}, within the cap "
                f"of {cap} — every round the cap allows names who reviews it")

    merge = review["merge_authority"]
    if merge not in nodes:
        raise GraphError(f"review.merge_authority references undeclared node "
                         f"'{merge}'")
    if nodes[merge]["authority"] != "merge":
        raise GraphError(
            f"review.merge_authority names '{merge}', whose authority is "
            f"'{nodes[merge]['authority']}', not 'merge'")

    closure = _resolve_closure(review, nodes, lens_ids, rounds, cap)
    light = _resolve_light(review, nodes, lens_ids, rounds, cap)
    delegation = _resolve_delegation(review, nodes, merge)
    return {"cap": cap, "rounds": rounds, "lenses": lenses,
            "merge_authority": merge, "closure": closure,
            "light": light, "delegation": delegation}


def _resolve_closure(review: dict, nodes: dict, lens_ids: list[str],
                     rounds: dict[int, list[str]], cap: int) -> dict | None:
    """The terminal closure round's declaration, checked, or None.

    WHAT IT DECLARES. A round minted PAST the cap, with a crew of one, for one
    thing: to give a head that a capped round's REPAIR moved something that
    can be attested.

    ITS NUMBER IS NOT PINNED TO `cap + 1`, and that is a correction rather
    than a looseness. The cap's refusal fires at `attest write`, AFTER `warden
    round new` has already created the directory — so every builder who
    hits this trap is left holding a stray, empty round at cap+1, and
    their closure round is numbered cap+2.
    Pinning the number would make the feature fail for exactly the branches it
    exists to rescue. `min_round` is reported for messages; what is ENFORCED
    is "past the cap", the condition a closure round exists for, and it
    costs nothing: a closure round buys no review budget at any number,
    because it cannot find.

    WHY IT IS NOT A THIRD REVIEW ROUND, which is the whole of the design and
    the only thing worth checking here. `cap` stays the repair budget. The
    closure round spends none of it because it cannot find: `warden attest
    write --review-dir` refuses its payload unless every record's rule_id +
    file was already filed by a round at or under the cap. This function
    guards the DECLARATION end of that — the payload rule is enforced in
    `attest`, and no declaration can relax it, because `payload` has exactly
    one representable value.

    Two refusals live here rather than in the schema, because both need the
    rest of the document to see:

    - a role with neither find nor judge authority, the rule every capped
      round's roles already answer to;
    - a role that is ALSO declared for a round within the cap. That is the
      feature's failure mode in its most plausible dress: declaring
      `code-reviewer` as the closure crew reads as housekeeping and is in
      fact a third adversarial pass, bought past the budget. The payload rule
      would still bind it, but a declaration that reads as extra review is
      refused where it is written rather than where it is caught.

    Absent block = nothing runs past the cap, the state every repo was in
    before this key existed. Silence is not permission.
    """
    closure = review.get("closure")
    if closure is None:
        return None
    from .attest import lens_is_valid, _lens_vocabulary_hint
    capped = {role for roles in rounds.values() for role in roles}
    roles: list[str] = []
    for i, role in enumerate(closure["roles"]):
        where = f"review.closure.roles[{i}]"
        if not lens_is_valid(role):
            raise GraphError(
                f"{where}: role '{role}' is outside the attestation's role "
                f"vocabulary ({_lens_vocabulary_hint()}), so no attest write "
                "--review-dir roster could ever carry it")
        if role.startswith(_LENS_ROLE_PREFIX):
            if role[len(_LENS_ROLE_PREFIX):] not in lens_ids:
                raise GraphError(
                    f"{where}: role '{role}' names no declared lens "
                    f"(review.lenses: {', '.join(lens_ids)})")
        elif role not in nodes:
            raise GraphError(f"{where}: role '{role}' is not a declared node")
        elif nodes[role]["authority"] not in _REVIEWER_AUTHORITIES:
            raise GraphError(
                f"{where}: role '{role}' has authority "
                f"'{nodes[role]['authority']}' — the closure round dispatches "
                "a finder or a judge, never a gate or a merge")
        if role in capped:
            raise GraphError(
                f"{where}: role '{role}' is already declared for a round "
                f"within the cap of {cap}, so declaring it here re-dispatches "
                "the review crew past the budget — which is the extra review "
                "round the closure round exists NOT to be. Declare a role of "
                "its own, whose boundary says it re-files and never finds")
        roles.append(role)
    return {"min_round": cap + 1, "roles": roles,
            "payload": closure["payload"]}


def _resolve_light(review: dict, nodes: dict, lens_ids: list[str],
                   rounds: dict[int, list[str]], cap: int) -> dict | None:
    """The proportionate round's declaration, checked, or None.

    WHAT IT DECLARES. The one crew dispatched for a change that has been
    PROVEN, from the diff alone, unable to alter behaviour: every changed
    file is Python that parses to the same tree once docstrings are
    stripped. Every other surface takes the full rounds, and the gate's own
    machinery is refused whatever its trees say.

    WHY IT IS NOT A CHEAPER FULL ROUND. The proof is what admits the roster,
    and the proof says the executable logic is untouched — so there is no
    logic for an adversarial crew to attack. What a proven-inert diff CAN
    still get wrong is a claim: a comment, a docstring or a line of prose that
    is not true of the tree. That is one question, and it takes one reader.
    The round is still minted, dispatched, dispositioned and committed; only
    the crew is smaller.

    ELIGIBILITY IS NOT DECLARED HERE OR ANYWHERE. This function checks the
    DECLARATION — who may run the light round. Whether a given range QUALIFIES
    is recomputed from the two trees by `warden attest write`, and again by
    CI over the pushed range. No declaration can relax that, and a builder
    cannot assert it.

    The same two refusals the closure round carries, for the same reasons: a
    role with neither find nor judge authority, and a role ALSO declared for a
    round within the cap. The second matters more here than there. Naming
    `code-reviewer` as the light crew would make the light tier and the full
    tier the same review at two prices, and a later reader of the shard could
    no longer tell which one had run.

    Absent block = every change takes the full rounds, the state every repo
    was in before this key existed. Silence is not permission.
    """
    light = review.get("light")
    if light is None:
        return None
    from .attest import lens_is_valid, _lens_vocabulary_hint
    capped = {role for roles in rounds.values() for role in roles}
    roles: list[str] = []
    for i, role in enumerate(light["roles"]):
        where = f"review.light.roles[{i}]"
        if not lens_is_valid(role):
            raise GraphError(
                f"{where}: role '{role}' is outside the attestation's role "
                f"vocabulary ({_lens_vocabulary_hint()}), so no attest write "
                "--review-dir roster could ever carry it")
        if role.startswith(_LENS_ROLE_PREFIX):
            if role[len(_LENS_ROLE_PREFIX):] not in lens_ids:
                raise GraphError(
                    f"{where}: role '{role}' names no declared lens "
                    f"(review.lenses: {', '.join(lens_ids)})")
        elif role not in nodes:
            raise GraphError(f"{where}: role '{role}' is not a declared node")
        elif nodes[role]["authority"] not in _REVIEWER_AUTHORITIES:
            raise GraphError(
                f"{where}: role '{role}' has authority "
                f"'{nodes[role]['authority']}' — the light round dispatches "
                "a finder or a judge, never a gate or a merge")
        elif nodes[role]["kind"] != "subagent":
            # The light round is the ONLY round its change gets, so the one
            # role it dispatches has to be an independent context. A `skill`
            # node is the builder's own execution path: declaring it here
            # would make the builder the sole reviewer of their own change,
            # and the shard would record that as a review. Every other round
            # has an adversarial pass beside it; this one has nothing.
            raise GraphError(
                f"{where}: role '{role}' is a '{nodes[role]['kind']}' node, "
                "and the light round dispatches a subagent — an independent "
                "context. It is the only round its change gets, so a role "
                "that runs inside the builder's own session would make the "
                "builder the sole reviewer of their own work")
        if role in capped:
            raise GraphError(
                f"{where}: role '{role}' is already declared for a round "
                f"within the cap of {cap}, so the light round and the full "
                "rounds would dispatch the same crew and a shard could no "
                "longer say which tier reviewed it. Declare a role of its "
                "own, whose boundary says it audits claims")
        roles.append(role)
    return {"roles": roles, "payload": light["payload"]}


def _resolve_delegation(review: dict, nodes: dict, merge: str) -> dict | None:
    """The delegation TERMS, checked, or None when none are declared.

    The block says on what terms the EXECUTION of merge authority may be
    delegated. It never says that a delegation is live: it has no field that
    could name a holder or an expiry, and the schema closes it, so a grant
    written here is refused before this function runs. That is deliberate and
    it is the whole design. graph.yaml is a TRACKED file with no clock — a
    session-scoped grant recorded in it survives the session, the branch and
    the year, and a later session reading a grant the founder gave once as
    its own live permission is a worse failure than the grant never having
    been recorded. So the durable artifact carries the terms; the grant
    itself is out-of-band and its exercise is recorded past-tense
    (`recorded_in`).

    Absent block = no delegation permitted. Fail-closed: silence is not
    permission, here least of all.
    """
    delegation = review.get("delegation")
    if delegation is None:
        return None
    granter = delegation["grantable_by"]
    if granter not in nodes:
        raise GraphError(f"review.delegation.grantable_by references "
                         f"undeclared node '{granter}'")
    if granter != merge:
        raise GraphError(
            f"review.delegation.grantable_by names '{granter}', but merge "
            f"authority is '{merge}' — a delegation issued by anyone but the "
            "authority holder is self-issued, which no grant here may be")
    eligible: list[str] = []
    for i, name in enumerate(delegation["eligible"]):
        where = f"review.delegation.eligible[{i}]"
        if name not in nodes:
            raise GraphError(f"{where} references undeclared node '{name}'")
        if name in eligible:
            raise GraphError(f"{where}: node '{name}' is listed twice")
        if nodes[name]["authority"] == "merge":
            raise GraphError(
                f"{where}: node '{name}' already declares authority 'merge', "
                "so it holds the button rather than a delegation of it — "
                "delegating merge to a merge-authority node records nothing")
        eligible.append(name)
    return {"permits": delegation["permits"], "grantable_by": granter,
            "eligible": eligible,
            "max_lifetime": delegation["max_lifetime"],
            "recorded_in": delegation["recorded_in"]}


def authority(doc: dict, root: Path | None) -> dict:
    """Who may merge right now, and on what basis — the whole answer.

    `may_merge_now` is ALWAYS exactly the merge_authority node, and no
    declaration can change that, because warden reads no live grant anywhere.
    That is not a gap this function papers over; it is the enforced shape of
    the design. A grant is explicit, current and revocable, and none of the
    three survives being written into a tracked file: the file has no clock,
    so "current" cannot be evaluated from it, and a revocation is another
    commit that may never be made. So the file declares the terms, the
    standing answer stays the authority holder, and a grant that was
    exercised is recorded after the fact where a reader meets it as history.

    The honest residual, which the basis line does not hide: warden cannot
    observe a merge ACTOR either. A delegated merge run with the founder's
    forge credentials is recorded by the forge as the founder, so no
    artifact distinguishes the two. This command answers who
    MAY merge, never who DID.
    """
    review = resolve_review(doc, root)
    holder = review["merge_authority"]
    delegation = review["delegation"]
    if delegation is None:
        basis = (
            f"graph.yaml review.merge_authority names '{holder}', and the "
            "review block declares no delegation terms, so the execution of "
            "merge authority may not be delegated in this org at all.")
    else:
        basis = (
            f"graph.yaml review.merge_authority names '{holder}'. "
            f"review.delegation declares TERMS, never a grant: at most "
            f"'{delegation['permits']}', grantable only by '{holder}', "
            f"holdable only by "
            f"{', '.join(delegation['eligible']) or 'no declared node'}, "
            f"for at most one {delegation['max_lifetime']}. No live grant is "
            "readable here, and none can be recorded here: graph.yaml is "
            "tracked and has no clock, so a grant written into it would "
            f"outlive the {delegation['max_lifetime']} it was given for. A "
            f"grant that was exercised is recorded after the fact "
            f"(`warden {delegation['recorded_in']} list`), past-tense, never "
            "as a live permission.")
    return {"merge_authority": holder, "may_merge_now": [holder],
            "basis": basis, "delegation": delegation}


def crew(doc: dict, root: Path | None, round_no: int) -> dict:
    """The resolved crew for one review round: roles in order, the lenses,
    the cap and the merge node. CrewPastCap past the cap; GraphError for a
    round below 1 or any fault in the block.

    A round past the cap resolves instead of refusing, when the org declares
    `review.closure`: the closure round, which answers with `closure: true`
    and the payload rule that binds it. No review runs there — the closure
    crew is one role that can only re-file — so the cap is not widened by
    this answering at cap+2 as readily as at cap+1.
    """
    if isinstance(round_no, bool) or not isinstance(round_no, int) \
            or round_no < 1:
        raise GraphError(f"round {round_no!r} is not a review round — rounds "
                         "are numbered from 1")
    review = resolve_review(doc, root)
    closure = review["closure"]
    if round_no > review["cap"]:
        # Past the cap is the CLOSURE round wherever the org declares one.
        # It answers with `closure: true` and its own roles rather than the
        # capped shape, so a caller cannot read a closure crew as another
        # review round: the key is what says "this round may re-file what the
        # capped rounds raised, and nothing else".
        if closure is not None:
            return {"round": round_no, "roles": list(closure["roles"]),
                    "cap": review["cap"], "lenses": review["lenses"],
                    "merge_authority": review["merge_authority"],
                    "closure": True, "payload": closure["payload"]}
        raise CrewPastCap(
            f"round {round_no} is past the cap of {review['cap']} "
            "(review.cap, equal to repo.yaml repair.budget) — no review round "
            "runs past it")
    return {"round": round_no, "roles": review["rounds"][round_no],
            "cap": review["cap"], "lenses": review["lenses"],
            "merge_authority": review["merge_authority"]}


def declares_review(root: Path) -> bool:
    """Whether graph.yaml DECLARES a `review` block — the document's keys,
    asked without asking the document to be valid.

    The NARROW question, and a different one from `declared_crew` below.
    "What is this repo's crew" cannot be answered without validating the
    document; "is a crew claimed at all" can be answered by any document that
    PARSES, and conflating the two is how a roster check came to refuse the
    entire ship tail for a consumer whose graph.yaml this schema rejects while
    declaring no crew at all — enforcement landing on a repo that made no
    claim. `warden ship` reads this; `attest write --review-dir` still reads
    the strict one, where the flag makes the strictness opt-in.

    Raises only where the document cannot answer: bytes that do not decode, or
    YAML that does not parse. "Could not say" is never RETURNED as the
    permissive False — that is the hole `declared_crew`'s docstring exists to
    close, and it stays closed here.
    """
    path = root / GRAPH_FILENAME
    if not path.exists() and not path.is_symlink():
        return False
    try:
        doc = yamlio.load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise GraphError(f"{GRAPH_FILENAME} cannot be read, so whether it "
                         f"declares a review crew is unknown: {exc}") from exc
    # A document that is not a mapping carries no key, so it declares none —
    # and `graph validate` refuses it on its own account, which is that
    # command's job and not this question's.
    return isinstance(doc, dict) and "review" in doc


def declared_crew(root: Path) -> dict | None:
    """The review crew `attest write` checks a roster against, or None.

    None only when the repo declares no crew: no graph.yaml, or one that
    LOADS and carries no `review` key — the roster is then taken as before.

    The `review` question is decided from `load`, never from the raw bytes.
    A document that does not validate cannot say whether it declares a crew
    any more than one that cannot be decoded can: a misspelled `reveiw:` and
    an empty file both lack the key while being documents `warden graph
    validate` refuses outright, and answering the permissive "this repo
    declares no crew" for either made the crew check skip itself silently on
    a graph.yaml warden itself rejects. A declared block is then fully
    validated, constitution included.
    """
    path = root / GRAPH_FILENAME
    if not path.exists() and not path.is_symlink():
        return None
    try:
        path.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        raise GraphError(f"{GRAPH_FILENAME} cannot be read, so whether it "
                         f"declares a review crew is unknown: {exc}") from exc
    doc = load(root)
    if "review" not in doc:
        return None
    validate(doc, root)
    return resolve_review(doc, root)


def node_for_cmd(root: Path, cmd: str) -> str | None:
    """The deterministic node whose impl runs this warden command — stamped
    into run manifests for org-chart telemetry. None when no graph or no
    match: the graph layer never blocks the tools it observes."""
    try:
        doc = load(root)
    except (GraphError, OSError):
        # unreadable graph.yaml included — a telemetry stamp must never be
        # able to crash the gate
        return None
    for name, spec in doc["nodes"].items():
        if spec["kind"] != "deterministic":
            continue
        try:
            tokens = shlex.split(spec.get("impl", ""))
        except ValueError:
            continue
        if len(tokens) >= 2 and tokens[0] == "warden" and tokens[1] == cmd:
            return name
    return None
