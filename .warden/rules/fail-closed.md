---
id: fail-closed
severity: MEDIUM
engine: claude
judges: behaviour
applies_to: ["warden/**", "cage/**", "tests/**", "skills/**", "scripts/**",
             ".githooks/**"]
# Set from where the class actually landed across 39 records: warden 26,
# tests 5, cage 4, skills 2, .githooks 1, and docs 1 (docs/** is
# wiki-fidelity's surface, so it is counted here and not claimed here). `scripts/**` is the one entry with
# NO record behind it, included because commit-lint.sh and portability-sim.sh
# are gate code by function — a prospective guess, and said to be one rather
# than folded into the evidence above.
# The rule is NOT named `fail-open` on purpose. A rule whose id equals a
# candidate slug retroactively invalidates every shard that ever filed under
# `unmapped:<slug>` — attest refuses a slug that duplicates a declared rule
# id, so E-02 ("every attestation rule_id resolves") fails across the whole
# committed corpus and certification drops. Watched live: naming this
# `fail-open` took the repo from LEVEL 5 to LEVEL 3. `covers:` is the seam
# built for exactly this, and wiki-fidelity/docs-drift is the precedent.
covers: [fail-open]
implements: ["swallowed-exceptions"]
---
When a check cannot evaluate, does it behave like a HIT or like a PASS?

A check that errs toward passing is worse than no check, because it reports
green. It is one of this repo's three most recurrent classes — 39 judged
records, behind docs-drift at 61 and enforcement-claim at 48 — and it has
escaped review here twice, which the other two have not. A cage pre-flight
resolved RETIRED skill versions and reported OK — that one shipped in code
carrying a clean attestation and was caught two beads later. Graph cycle
detection ran only when an optional depth cap was declared — that one
predates this corpus and was caught by an audit, not by any review round.

Flag an ADDED or MODIFIED guard, checker, gate, pre-flight, or CI step whose
failure path is indistinguishable from success. The shapes below are the ones
that actually recurred here — not a general taxonomy:

- **Swallowed error.** `except: pass`, `except X: continue`, or a bare
  `return None`/`return {}` on failure, where the caller cannot tell "nothing
  found" from "could not look". Ask what the caller does with the empty value.
- **Unreadable input skipped.** One corrupt artifact, shard, or config
  silently dropped, so a scan reports a clean result over a partial set.
- **Existence checked, validity not.** Asserting a file, key, or version is
  PRESENT without asserting it RESOLVES to something usable.
- **Optional-guard.** A check gated on a config key, flag, or environment
  value being declared, so omitting the key skips the check rather than
  failing it. A cycle is a defect with or without a depth cap. (One incident,
  not a recurrence: it rests on the graph escape above, which predates this
  corpus. Listed on the same footing as `scripts/**` in the frontmatter —
  a prospective shape, said to be one.)
- **Scope-limited derivation.** A guard that recognises only one spelling of
  what it checks — one call shape, one naming convention, one indentation
  level — so a legitimate variant is invisible and reads as absent.
- **Unanchored or unterminated pattern.** A regex or glob that matches more
  than the thing it names, so a near-miss satisfies it.
- **Order dependence.** First-match-wins or sort-order-dependent verdicts,
  where an earlier permissive hit vetoes a later strict one.
- **Wrong exit direction.** A crash (AttributeError, TypeError) escaping
  instead of the check reporting failure; a rejection whose non-zero status
  never reaches the caller; an exit 0 after rejecting something WHERE THE
  REJECTION IS OTHERWISE SILENT. Read the carve-out below before filing that
  last one — it is the only pattern this corpus has explicitly refuted.

Evidence must quote the failing line AND name what a CALLER SEES. Two ways
that lands, because the shapes above split two ways. For a check that cannot
evaluate: "this errors and the caller reads it as a pass" is a finding, "this
could error" is not. For a check that evaluates fine and evaluates the wrong
thing — the derivation, pattern and ordering shapes — name the input it
silently accepts: "'## Autonomy scoped' satisfies this pattern" is a finding,
"this regex looks loose" is not. Demanding an error moment would have made
three of the eight shapes unreportable, and they are among the best evidenced.

Do NOT flag: **exit 0 after a rejection that is reported by other means.**
This is the class's single refuted record (warden/cli.py:261):
`memory ingest` exits 0 having rejected an artifact, and the cross-examiner
refuted it because exit 0 is the deliberate documented contract there — the
rejection reaches the caller as a REJECTED stderr line and an
`invalid_rule_ids` field in the result artifact. A non-zero exit is not what
makes a rejection visible; a caller that can observe it is. Also do not flag:
genuinely optional behaviour that reports its own absence;
degradation the caller is explicitly told about (an `unavailable`/`partial`
record, a warning the artifact carries); best-effort paths whose failure is
surfaced elsewhere; or code with no gating role at all. A `try/except` around
work that is not a check is not this rule.

MEDIUM is deliberate. `blocking_severities: [HIGH]`, so this reports without
blocking while it earns a precision history — the same footing wiki-fidelity
shipped on, and for the same reason: a day-one judgment rule that blocks is
how a gate loses its credibility.
