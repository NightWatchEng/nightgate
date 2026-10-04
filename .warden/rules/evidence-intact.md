---
id: evidence-intact
severity: MEDIUM
engine: claude
judges: behaviour
# Scope is set by where the class has actually landed — every one of the nine
# judged records sits on the surface that PRODUCES this platform's evidence
# (mine.py x5, advisor.py x2, the pre-pr-review protocol, a backtest
# artifact). A repo-wide judgment lens is paid on every review forever;
# this one is paid only where the evidence chain is written.
applies_to:
  - "warden/memory.py"
  - "warden/mine.py"
  - "warden/advisor.py"
  - "warden/attest.py"
  - "warden/certify.py"
  - ".warden/memory/**"
  - "skills/nightgate-skills/skills/pre-pr-review/**"
  - "skills/nightgate-skills/skills/retro/**"
  - "skills/nightgate-skills/skills/rule-advisor/**"
# Nine judged candidate records, nine upheld — the class is
# `corpus-integrity`: the review corpus or its evidence chain corrupted,
# colliding, or silently dropping records. HONEST CAVEATS, from the retro
# that proposed this rule: recurrence is
# ROUND-concentrated — the nine records come from four review rounds, not
# nine independent occurrences; read it as "four times, the code that
# produces this platform's evidence was found corrupting it". Precision is
# unmeasured: an engine:claude rule cannot be replayed over history, so the
# 9/9 is corpus history, not a backtest. The rule reports (MEDIUM against
# blocking_severities [HIGH]) while it earns a precision history.
# Not named `corpus-integrity`: a rule id equal to a candidate slug
# retroactively invalidates every shard filed under `unmapped:<slug>` (the
# LEVEL 5 -> LEVEL 3 trap). `covers:` is the seam; the
# nine legacy ids are grandfathered in .warden/certification.yaml.
# `corpus-integrity` remains a declared TAG in tags.yaml — a different
# namespace from this slug.
covers: [corpus-integrity]
---
Does this diff let a record, a count, or a file leave the evidence chain
without saying so? This code's output is what every later judgment — recall,
promotion, certification, the retro — trusts as ground truth, so a silent
drop here is not a bug in one feature: it is corrupt evidence in every
decision downstream.

Nine judged records, nine upheld, all on the evidence-producing surface.
The seams, from those records:

- **A key or path that silently matches nothing.** File paths parsed from
  `--- a/` diff headers missed git's C-quoted form, so a non-ASCII path
  matched no header and the parser kept the PREVIOUS file's name — records
  attributed to the wrong file, nothing reported missing. The repair
  introduced the twin: a filename passed as a git pathspec is a wildmatch
  pattern, so `?` `*` `[` in a name attributed OTHER files' hunks to it.
  Flag a parse or match whose miss is indistinguishable from an empty
  result.
- **A budget that truncates while still reporting a full count.** A
  blame-lines cap `break`-ed out and skipped the remaining files, yet the
  output still listed the FULL file set as covered. Flag any cap, timeout,
  or pagination exit that narrows what was read without narrowing what the
  artifact claims was read.
- **A filter applied to one half of a corpus.** `--since` bounded the
  git-side classes while the GitHub-side classes counted PRs from all time
  — under one window stamped at the top of the artifact. Flag a bound,
  window, or exclusion that some of the merged sources never saw.
- **A judged status counted as the wrong evidence.** Refuted and
  dismissed-with-reason findings counted FOR a recommendation — a class the
  repo argued down three times outranking all prior art. Flag any tally
  where a record's judged status could flip its contribution's sign without
  the reader seeing it.
- **A shared fixed path two concurrent writers collide on.** The review
  protocol packaged every round to one literal `/tmp` path, so two
  concurrent rounds could silently clobber each other and still produce a
  validly-stamped attestation. Flag a fixed temp path or shared mutable
  location on the evidence path.
- **An artifact the chain's own validator refuses.** A backtest stamped
  with an action the S-05 enum lacked hard-failed the rung and silently
  dropped certification with CI green. Flag committed evidence whose shape
  the pipeline that consumes it will reject or skip.

Do NOT flag: a truncation, cap, or filter that REPORTS itself in the same
artifact it narrows (a stated `truncated: true`, a warning line, a count of
what was skipped — the honesty is the fix, not the cap); code outside the
evidence chain that merely reads the corpus without writing or summarizing
it; a deliberate, documented exclusion the artifact's schema declares; test
fixtures that fabricate shards on purpose. The line is "leaves the chain
without saying so" — loud narrowing is fine, silent narrowing is the class.
