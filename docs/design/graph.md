# Graph layer — design of record

"Graph engineering" = the agent ORGANIZATION as declared config, not prose in
skills. Our org graph already runs (builder → council → finder → independent
judge → deterministic gate → founder-merge), with real handoff artifacts
(attestations, .run-summary) and edge-typed state (split memory). This layer
declares it.

## graph.yaml (per consuming repo)
- nodes: role name → {kind: skill|subagent|human|deterministic, impl (skill
  name / gate cmd), authority (find|judge|gate|merge), boundaries[]}
- edges: from → to with {payload: intent|diff|findings|attestation|pr,
  format/artifact name}; memory edges are TYPED (patterns vs precedents).
- verification: which judges are independent; convergence rules (e.g.
  "finding survives only if judge confirms"); failure policy per node
  (retry N | escalate | abort) and caps.
- Renders to mermaid via `warden graph render`; `graph validate` checks
  nodes/edges/impls resolve (skills exist, gate commands parse).

## Consumers
- Skills interpret the declared graph instead of embedding topology.
- Manifests/ledger stamp node names → org-chart telemetry.
- The retro may propose TOPOLOGY changes (add/drop a lens) with evidence —
  the org self-improves, founder merges.

## v1 implementation notes — review round
- Skill resolution: `graph validate` checks the reference FORM and, where
  the marketplace lives IN the repo, resolves the id against it. A
  user-scope install is invisible to a CLI run, so elsewhere the check stays
  silent rather than issuing a green light it cannot justify.
- Caps: per-node `retry.max`, `escalate.to` (declared target, no self-
  escalation, no cycles), and org-wide `verification.caps`
  (`total_retries`, `escalation_depth`). A cap that does not bound the sum
  of the parts is refused — it would bound nothing.
- Consumers: pre-pr-review defers to the declared judges, convergence rules,
  and failure policies over its own defaults; autonomous-run records the node
  it ran as in `.run-summary`, and the cage appends it to the ledger as a
  sixth column (existing readers index 0-4 and keep working).
- validate() and render() agree by construction: impl/artifact strings that
  would break the mermaid render (quotes, pipes, newlines) are rejected at
  validate time; 'memory' is a reserved node name (the render's store
  pseudo-node).

## Boundaries
- NOT a framework (no LangGraph/AutoGen): runtime = Claude Code sessions;
  the graph is config + interpretation.
- Single-verifier flows stay loops (the source material's own warning).
- A consuming repo declares its own topology in its own graph.yaml (P3 exit
  test: rendered graph matches observed behavior). Consumer-side files exist:
  shortfall ships one at its root, and `warden certify` there reports S-01
  found and S-02 validates (run 2026-09-01). That is the DECLARATION
  validating, which is not the exit test — nothing anywhere yet compares a
  rendered graph against an observed run.
