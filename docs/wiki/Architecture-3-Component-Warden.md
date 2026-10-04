# Level 3 — Component: inside warden

Zoom into the `warden` container: the modules behind the gate, and which
subcommand reaches which. This is the level where "policy → enforcement →
evidence → provenance" stops being a slogan and becomes a call graph.

> **Arrow convention on this page:** an arrow means **A hands data or work to
> B** — data flow, not Python import direction. The two disagree in places
> (`memory.py` imports from `attest.py`, but the shards flow the other way),
> and a page that mixes the two conventions cannot be used to reason about
> direction at all. Dashed arrows are advisory inputs: they inform a step
> without gating it.

```mermaid
flowchart TB
    cli["<b>cli.py</b><br/><i>the only entry point</i><br/>explain · verify · deploy · review · diff · attest<br/>decide · audit · plan · certify · rules · autonomy<br/>mine · catalog · graph · skills · memory"]

    subgraph policy["policy — what is declared"]
        config["<b>config.py</b><br/>repo.yaml: components, risk tiers, protected<br/>paths, verify scopes, review settings, platform.pin"]
        rulesmod["<b>rules.py</b><br/>loads .warden/rules/*.md<br/>computes <b>rules_version</b>"]
        mech["<b>mechanical.py</b><br/>the shipped engine:python checker pack"]
        plugins["<b>plugins.py</b><br/>registry = core pack + .warden/checkers/*.py"]
    end

    subgraph enforce["enforcement — what can auto-reject"]
        diffs["<b>diffs.py</b><br/>base…head three-dot DiffContext"]
        decl["<b>declarative.py</b><br/>engine:declarative — checks in frontmatter"]
        review["<b>review.py</b><br/>runs the rules gate<br/>blocking severities come from repo.yaml"]
        verify["<b>verify.py</b><br/>runs repo.yaml's verify map"]
        deploy["<b>deploy.py</b><br/>deploy preconditions: checkout binding,<br/>branch reachability, committed attestation"]
    end

    subgraph evidence["evidence — what is left behind"]
        runs["<b>runs.py</b><br/>.warden/out/&lt;ts&gt;-&lt;cmd&gt;/manifest.json<br/><i>binds command, argv, SHAs, rules_version</i>"]
        attest["<b>attest.py</b><br/>write · show · check<br/><i>refuses a dirty tree, a finding still<br/>confirmed under a clean verdict,<br/>or an unresolvable rule_id</i>"]
        decide["<b>decide.py</b><br/>durable decision record<br/>bound to commit + rules_version"]
        github["<b>github.py</b><br/>event parsing + the sticky PR comment<br/><i>stdlib urllib, no gh dependency</i>"]
    end

    subgraph learn["provenance — what the corpus teaches"]
        memory["<b>memory.py</b><br/>ingest sweeps run dirs into committed shards<br/>recall splits patterns from precedents"]
        plan["<b>plan.py</b><br/>task packet: paths, risk,<br/>must-pass commands, priors"]
        mine["<b>mine.py</b><br/>every merged PR, not just attested ones"]
        catalog["<b>catalog.py</b><br/>guardrails/catalog.yaml — cited prior art"]
        advisor["<b>advisor.py</b><br/>joins catalog × corpus × .warden/rules"]
        backtest["<b>backtest.py</b><br/>replay a proposed rule over real history"]
        autonomy["<b>autonomy.py</b><br/>pause · adopt — the safe half of the ladder"]
        tags["<b>tags.py</b><br/>keeps the recall index from fragmenting"]
    end

    certify["<b>certify.py</b> + <b>certification/baseline.yaml</b><br/><i>scores the repo 1–5 on the enrollment ladder</i>"]
    graphc["<b>graph.py</b><br/>graph.yaml validate · render"]

    cli --> config
    cli --> verify
    cli --> deploy
    deploy --> runs
    cli --> review
    cli --> plan
    cli --> certify
    cli --> graphc
    cli --> attest
    cli --> decide
    cli --> advisor
    cli --> mine
    cli --> memory
    cli --> autonomy
    cli -->|"CI mode only"| github
    config --> rulesmod
    config -->|"blocking_severities<br/>context_excludes"| review
    rulesmod --> decl
    rulesmod --> review
    mech --> plugins
    plugins --> review
    diffs --> review
    decl --> review
    review --> runs
    verify --> runs
    certify --> runs
    plan --> runs
    attest --> runs
    decide --> runs
    runs -->|"ingest sweeps<br/>attest + decide artifacts"| memory
    memory -.->|"priors"| plan
    memory --> advisor
    catalog --> advisor
    advisor --> backtest
    tags -.->|"vocabulary"| memory
    autonomy --> rulesmod

    classDef c fill:#1168bd,stroke:#0b4884,color:#fff;
    classDef db fill:#2e6fb0,stroke:#0b4884,color:#fff;
    class cli,config,rulesmod,mech,plugins,diffs,decl,review,verify,deploy,attest,decide,github,memory,plan,mine,catalog,advisor,backtest,autonomy,tags,certify,graphc c;
    class runs db;
```

**Reading it:** the four sub-graphs are the four commitments, in order — and
two of the arrows crossing between them are the ones worth arguing about.

- **`rules.py` is what makes `rules_version` meaningful.** The hash is
  computed over the rule files, the findings schema, `mechanical.py`, the
  project's own `.warden/checkers/*.py`, and repo.yaml's `review:` subtree
  (canonicalized) — so a rule edit, a `params` change, a checker edit, or a
  `blocking_severities` / `context_excludes` flip all move it. What it
  deliberately does **not** cover: repo.yaml keys outside `review:` —
  components, risk tiers, protected paths, verify commands steer planning
  and verification, not the review verdict, and hashing them would churn
  attestation cohorts on unrelated edits.
- **No repo-specific fact lives in a module.** Tiers, paths, symbols and fixture
  names arrive from `repo.yaml` or a rule's frontmatter `params` — never from
  `config.py` or `mechanical.py`. A test greps the package to keep it that way;
  the failure mode it prevents is a shadow copy of the policy inside the code
  that enforces the policy.
- **Every command that produces new evidence writes a run directory.** Not every
  subcommand: `attest show`, `attest check`, `audit`, `graph`, `catalog`, and
  `memory recall`/`stats` are pure reads and deliberately write nothing —
  [Adopting](Adopting.md) enumerates that exemption list, and a derivation test holds
  it against `cli.py`'s real call sites. What the invariant does say is that an
  action which *changes* the record leaves one.
- **The shard is not written by `attest write`.** `attest write` writes
  `attestation.json` into a gitignored run directory; `warden memory ingest`
  sweeps that into the committed shard under `.warden/memory/attest/`. Skipping
  ingest leaves nothing to commit — see [Level 4](Architecture-4-Sequence-Bead-to-PR.md).

| Module | Responsibility | Answers to |
|---|---|---|
| `config.py` | loads and schema-validates `repo.yaml` | fails closed on any unknown key |
| `rules.py` | loads the rules dir; computes `rules_version` | the hash every verdict is stamped with |
| `mechanical.py` · `plugins.py` | the shipped checker pack, plus the project's own | `engine: python` rules |
| `diffs.py` · `declarative.py` | the `base...head` context, and frontmatter-declared checks | `engine: declarative` rules |
| `review.py` · `verify.py` · `deploy.py` | the three things that can auto-reject | `blocking_severities`, the verify map, deploy preconditions |
| `runs.py` | one run dir per state-changing command | the manifest binding command, argv, SHAs, `rules_version` |
| `attest.py` · `decide.py` | the fail-closed evidence writers | refuse a dirty tree |
| `github.py` | event parsing and the sticky comment | stdlib HTTPS, no `gh` dependency |
| `memory.py` · `tags.py` | shards, split recall, the vocabulary index | context, never policy |
| `mine.py` · `catalog.py` · `advisor.py` · `backtest.py` | history, prior art, the gap, and the replay that costs a rule its hope | `rules recommend` / `lifecycle` |
| `autonomy.py` | the safe half of the ladder | never commits, pushes, or merges |
| `certify.py` · `graph.py` | the ladder score and the org constitution | `baseline.yaml`, `graph.yaml` |

Per-command behaviour, exit codes and artifacts: [CLI Reference](CLI-Reference.md).
