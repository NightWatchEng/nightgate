# Review-memory subsystem — design of record

Two deep-research rounds (Aug 20; Graphiti/Mem0/Cognee/Kuzu landscape +
coding-agent memory precedents + adversarial stress). Verdict: at structured,
grep-scale volume, the append-only event log IS the graph — engines exist to
LLM-extract structure our findings already have. This matches the argument in
"Experience Graphs: The Data Foundation for Self-Improving Agents" (Liao et
al., arXiv:2606.29823) — verified at source, title quoted so the next reader
does not have to take a bare identifier on faith. No engines, $0, no ingestion APIs.

## Store
- Sharded, committed: one JSON per review event at
  `.warden/memory/attest/<utc-ts>-<sha8>.json` (distinct filenames = zero
  merge conflicts across concurrent sessions; survives CI-artifact expiry).
- Derived, gitignored cache `findings.jsonl`, rebuilt by `memory ingest`
  (idempotent by content-hash id).
- A shard is a review EVENT that carries records, not a container that only
  exists because there were findings. Envelope: {schema,
  source, sha, base_sha, rules_version, reviewed_at, verdict, reviewers,
  records[]}. A clean round files a findings-free shard — that is what
  `attest check` reads, and while it did not exist the gate answered NO
  ATTESTATION for exactly the PRs that passed review. A findings-free shard
  adds no record: never recalled, never in a rule's n, never in precision or
  the Wilson bound. An artifact that cannot name what was reviewed, when, by
  whom and with what verdict — or whose verdict is outside the schema's
  `clean | findings-open` enum, READ from attestation.schema.json rather than
  retyped — is refused, not sharded (fail closed). Ingest
  never calls `attest.build`, and `_check_rule_ids` returns immediately with no
  findings, so this is the only verdict check on that path. A gate run that
  fires nothing writes nothing; a verdict nobody gave is omitted, never written
  as an empty string. Redaction covers the ENVELOPE as well as the records.
- Shards predating the event envelope are NOT migrated, deliberately. Nothing
  rejects them: `attest check` still reads them, ingest still loads their
  records, and the REVIEW EVENTS tally counts them apart as "no verdict
  recorded". This is not the forward-only-policy trap (a `covers:` rule that
  made existing shards illegal under the rule that reads them) — no shard
  becomes invalid here. The verdict IS derivable from the records (`attest
  build` enforces clean <=> no confirmed finding), and it is still not
  back-filled: a derived value written into committed evidence stops being
  evidence and starts being an inference wearing its costume.
- Record: {id, ts, source: attest|gate, sha, base_sha, pr?, bead?, rule_id,
  tags[], dir_prefix, file, severity, finding, evidence, status:
  confirmed|refuted|fixed|dismissed-with-reason, reason?,
  origin: interactive|unattended — presence, not the clock (records written day|night before the rename stay readable, the reader
  never validates the vocabulary)}.
- **Secrets REDACTED at ingest** (secret-class rules store
  `<redacted:pattern>`) — the real corpus already contained a verbatim
  planted key (CodeRabbit precedent: redact before storage).
- **rule_id RESOLVED at ingest** by `attest`'s own check (imported, not
  copied): an id `attest write` refuses is rejected whole, reported, and the
  sweep continues. The promotion gate reads the corpus, not the CLI, so the
  invariant has to hold at the reading end too. Guard sits at the
  artifact → shard seam only — shards committed before it existed still load.
- NOT hashed into rules_version — memory is context, never policy.

## Keys & recall
- Recall keys: (dir_prefix, tags, rule_id) — NOT file paths (renames/new
  files) and NOT bare rule_id (81% of real findings shared one id). Tags are
  a short taxonomy applied at attest time. NO embeddings until the corpus
  outgrows a context window (~years away).
- `memory recall --files ... --for reviewer|examiner`: **split memory** —
  reviewer gets confirmed|fixed PATTERN priors ("recurred 3× here — verify,
  don't assume"; never candidate findings); examiner gets
  refuted|dismissed PRECEDENTS as non-binding case law. Disjoint priors =
  decorrelated judges; kills the re-litigation tax (~24% dismissal rate).
- Budget: K≤10 records, each a bounded excerpt of the finding (≤200 chars,
  one line, `…`-marked, ending in its record id), the budget derived from K
  such lines, the header counting relevant and skipped so a thinned recall
  never reads as an empty corpus; ranked by
  recurrence key (rule, dir_prefix) before recency, one record per shape per
  pass (the flat ~1.5KB, recency-only budget it replaced
  returned 0-1 records over hundreds; `docs/wiki/Memory.md` carries the
  numbers and the derivation test pins them). `fixed` never injected as open
  instances (same-area history is the worst semantic distractor —
  context-rot findings).

## Stats & retro coupling
- `memory stats` opens with a REVIEW EVENTS tally — rounds on record, how many
  came back clean, how many carried findings — counted from the attest shards,
  never from records. Reported ALONGSIDE precision, never inside it: precision
  counts findings, this counts rounds. Before clean rounds were sharded the
  question was unanswerable, so every review-level rate was computed over a
  self-selected sample whose denominator excluded every clean round.
- `memory stats`: per-rule/per-tag confirmed/refuted/dismissed counts +
  a 95% confidence floor on the true rate. Promotion to deterministic checker requires
  **N≥10 AND confidence floor ≥0.7**; pause a rule after 3 straight refuting
  review ROUNDS (cheap, reversible — Tricorder's >10%-not-useful precedent). Stats measure
  PRECISION ONLY — every retro output carries the "recall is unobservable"
  caveat and mines escapes separately.
- The report is SPLIT: declared rules are *measured*;
  `unmapped:` candidates are *proposed*. A candidate has no rule, so it is
  never promotable to a checker — its promotion is "write this rule", ranked
  by recurrence and gated at CANDIDATE_MIN_N judged cases with a raw majority
  upheld. Deliberately not the checker bar: the bar tracks the cost of being
  wrong, and the Wilson floor is calibrated for automating a rule that
  auto-rejects, not for opening a PR a human reads. Applied to candidates it
  would have silenced the most recurrent class in the corpus
  (`enforcement-claim`, 10 of 11 upheld, floor 0.623). The floor is still
  printed on every candidate the report calls out individually — reported,
  not enforcing; the sub-threshold `watching` roll-up prints upheld/judged and
  no floor, because a bound over fewer than CANDIDATE_MIN_N cases says nothing
  worth over-reading (review round R1/F1).
- A refutation streak is a second bar, independent of the rate: PAUSE_STREAK
  straight refuting review ROUNDS make a declared rule a pause candidate and
  block a candidate's proposal. Rounds, never records: a round extends the
  streak only if every finding it judged for that subject was refuted, so no
  permutation of one round's findings can manufacture a streak
  (#145). Dropping candidates from the pause list without
  carrying the streak into the proposal decision lost that signal outright
  (R1/F3) — a 7-upheld-then-3-refuted class scored 0.7 and was proposed.
- A candidate the ruleset already answers renders `covered`, never proposed.
  The link is DECLARED, never inferred: a rule id equal to the slug, or
  `covers: [<slug>]` in the rule's frontmatter (`wiki-fidelity` covers
  `docs-drift`). Inferring subsumption would be exactly the unevidenced claim
  the report exists to avoid. Both ends of the pipe enforce the link: once a
  rule covers a class, `attest write` and `memory ingest` reject new
  `unmapped:<slug>` findings for it, so the judgments accrue to the rule
  rather than piling up under a class it already answers (R1/F2).
- Stats is a pure read, but a ruleset that will not LOAD is reported, not
  swallowed: promotion and pause candidacy are withheld (we cannot know what
  is paused) and the render says so. Swallowing it was strictly quieter — a
  paused rule surfaced as promotable (R1/F4).

## Acceptance (from the approved plan)
Ingest recovers the historical 14-finding round; planted-key artifact ingests
redacted (grep proves no secret in committed shards); concurrent writers no
conflict; role-split recall within budget; stats show no rule qualifies for
promotion at current N (guardrail visibly working).

## v1 implementation notes — review round
- The literal historical 14-finding round predates the attestation artifact
  format and was never persisted — the acceptance test recovers a synthetic
  equivalent (10 confirmed / 3 refuted / 1 fixed) with the same shape.
- `source: gate` ingestion and `pr`/`bead` provenance shipped in v2. Gate
  findings carry status `detected` and land in their own shard directory: a
  deterministic checker firing is a FACT, not a judgment, so a detection is
  a legitimate reviewer prior ("this fired here before") but never touches
  precision math — a rule that is already deterministic cannot be promoted
  to deterministic, and counting its hits as confirmations would manufacture
  a perfect score out of tautology. Detections are reported separately as
  hotspots for the retro.
- `origin` is stamped per ingest sweep (--origin), not per event.
- Rule pause is advisory in stats output; the retro owns coupling
  it to enforcement.
- Redaction covers finding, evidence AND reason, reusing mechanical.py's
  secret patterns by import so gate and ingest can never drift.
- Shard filenames carry a content digest (<utc-ts>-<sha8>-<digest8>.json):
  ts+sha ties between distinct events must never overwrite records.
