# Level 2 — Containers

Zoom into the Nightgate box: the separately runnable pieces, where each actually
executes, and what a consuming repo holds. In C4 a "container" is a runtime unit —
an app, a service, a store — not a Docker container. There is no long-running
process anywhere on this diagram; every unit is a CLI invocation, a CI job, or a
cron-fired script.

```mermaid
flowchart TB
    founder(["👤 <b>Founder</b>"])

    subgraph platform["Nightgate — the platform repo"]
        direction LR
        warden["⚖️ <b>warden</b><br/><i>Python CLI · policy in, evidence out</i><br/>explain · verify · deploy · review · diff · attest<br/>decide · audit · plan · certify · rules · autonomy<br/>mine · catalog · graph · skills · memory"]
        cage["🔒 <b>The cage</b><br/><i>bash run.sh + rendered cage.env</i><br/>quiet hours · kill switches · PR back-pressure<br/>main-green · watchdog · forbidden paths"]
        skills["🧠 <b>The skill pack</b><br/><i>Claude Code plugin, installed per machine</i><br/>ship · intake · deliver · pre-pr-review · orchestrate<br/>autonomous-run · review-queue · retro · rule-advisor"]
        schemas["📐 <b>Artifact schemas</b><br/><i>warden/schemas/ · additive-only</i>"]
        graphl["🕸️ <b>The graph layer</b><br/><i>warden graph validate · render</i><br/>who finds · who judges · who gates · who merges"]
    end

    subgraph consumer["a consuming repo"]
        direction LR
        ry["<b>repo.yaml</b><br/><i>components · risk tiers · verify scopes<br/>protected paths · platform.pin</i>"]
        rules["<b>.warden/rules/*.md</b><br/><b>.warden/checkers/*.py</b><br/><i>declarative regex · engine:python · engine:claude</i>"]
        policy["<b>.warden/skills-policy.md</b><br/><i>six required sections + build disciplines</i>"]
        gy["<b>graph.yaml</b><br/><i>who finds · judges · gates · merges</i>"]
        ctoml["<b>cage.toml</b><br/><i>triggers: schedule | manual | ci-event</i>"]
    end

    shards[("📚 <b>Review memory</b><br/>attest/*.json — <b>committed evidence</b><br/>gate/*.json — <b>ignored: synthetic probe defects</b><br/>findings.jsonl — <b>derived, rebuilt whole</b>")]
    out[("🧾 <b>Evidence</b><br/>.warden/out/&lt;ts&gt;-&lt;command&gt;/<br/><i>run manifest stamps the pin + rules_version</i>")]

    claude["<b>Claude Code sessions</b>"]
    gh["<b>GitHub</b><br/><i>PR · Actions · sticky comment</i>"]
    launchd["<b>launchd</b><br/><i>optional schedule trigger</i>"]

    founder --> skills
    skills -->|"reads the contract"| policy
    skills -->|"runs as sessions"| claude
    cage -->|"cage enroll renders the runner"| ctoml
    launchd -.->|"only if a schedule is declared"| cage
    cage -->|"starts one caged session"| claude
    claude -->|"branch → PR"| gh
    warden -->|"loads policy"| ry
    warden -->|"loads + versions"| rules
    graphl -->|"validate / render"| gy
    warden -->|"writes every run"| out
    warden -->|"attest write · memory ingest"| shards
    shards -.->|"patterns"| claude
    shards -.->|"precedents"| claude
    warden -->|"verdict on the diff"| gh
    schemas -.->|"contract consumers parse"| out
    gh --> founder

    classDef person fill:#08427b,stroke:#052e56,color:#fff;
    classDef c fill:#1168bd,stroke:#0b4884,color:#fff;
    classDef db fill:#2e6fb0,stroke:#0b4884,color:#fff;
    classDef ext fill:#8a8a8a,stroke:#5f5f5f,color:#fff;
    class founder person;
    class warden,cage,skills,schemas,graphl,ry,rules,policy,gy,ctoml c;
    class shards,out db;
    class claude,gh,launchd ext;
```

**Reading it:** the platform column ships **behaviour**; the consumer column is
**only config**. That split is the portability claim, and it is a standing CI
check rather than a promise — consumer configs must keep validating against the
shipped schemas.

Three details on the diagram are load-bearing:

- **The two memory edges carry different information.** Reviewers get *patterns*
  ("this recurred here"); cross-examiners get *precedents* ("this was refuted
  here"). Feeding both the same history would make the two judges agree for a
  reason unrelated to the diff, which destroys the decorrelation the whole review
  shape is built on.
- **"Shards are committed" is the wrong rule; "attest shards are committed" is the
  right one.** `attest/*.json` shards have collision-free names and are evidence,
  so they land in git. `findings.jsonl` is rebuilt whole by every `warden memory
  ingest` — tracking it makes every reviewing branch conflict with every other.
  And `gate/*.json` shards are *also* shards and are deliberately ignored: they
  record synthetic defects from mutation probes, so one `git add -A` after a probe
  would commit fabricated findings into the shared corpus. Three paths, and
  [Adopting](Adopting.md) tells a reader to ignore two of them by name.
- **`launchd` is a dashed, optional edge.** `cage enroll` renders a plist only when
  a `schedule` trigger is declared; `manual` and `ci-event` are the other two, and
  every hard stop in the runner holds however the run started.

| Unit | Runs where | Lifetime |
|---|---|---|
| warden | Laptop and CI. **Consumers** run the release tag their `platform.pin` names; this repo runs its own package via `uv run --project`, unpinned — a platform cannot pin itself to a past version of itself | One invocation |
| The cage | Laptop, outside the project worktree so a caged session cannot edit it | One run, resumable from a checkpoint |
| The skill pack | Installed once per machine from this repo's plugin marketplace, added by its GitHub URL, so it resolves in any checkout or worktree | One session |
| Review memory | Committed shards in the repo; the query cache is rebuilt on demand | Permanent / derived |
| Evidence | `.warden/out/`, one timestamped directory per command | Permanent |
| The graph layer | Wherever warden runs — `graph validate` is a CI gate, `graph render` draws the org chart | One invocation |
| Artifact schemas | Additive-only — consumers parse these, so a removal is a breaking change | Versioned with the platform pin |
