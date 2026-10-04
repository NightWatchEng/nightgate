---
id: pattern-fit
severity: MEDIUM
engine: claude
judges: behaviour
applies_to: ["warden/**", "tests/**", ".warden/rules/**"]
# Where the candidate records landed: warden 6, tests 3, .warden 1. This
# rule DOES scan .warden/rules/** — pattern definitions are its subject, not
# an incidental quote source; engine claude judges a pattern against its
# stated intent rather than grepping for strings, so the self-reference trap
# that forces excludes on secrets-in-diff and enforcement-truth does not
# arise mechanically here.
# Not named `overbroad-pattern`: a rule id that equals a candidate slug
# invalidates every shard filed under `unmapped:<slug>`. The 10 legacy ids
# are grandfathered in .warden/certification.yaml.
covers: [overbroad-pattern]
---
Does each added or modified pattern — regex, glob, alternation, prefix test —
match exactly the set of things its name and use claim, in both directions?

Ten judged candidate records, ten upheld. The instances that earned the
rule, from those records: `except-pass` widened to `(pass|continue)` and
flagged the canonical retry loop — measured over 722 CPython stdlib
modules, +54 matches, 48 of them legitimate `continue`; sql-concat's
`[+%]\s*["']` fired on a SQL `LIKE` wildcard, reporting a constant
parameterless query as concatenation, and the half-repair left the commoner
leading-wildcard idiom still firing; a phantom-check-id scan matched any
`[A-Z]{1,2}-\d{2}`, so an unrelated `PR-01` failed the ladder test; a
`scope:`/`require:` comparison used EQUALITY, so `scope: file` — strictly
wider, a hardening the contract explicitly permits — failed as "narrower".

For every pattern in the diff, ask both questions and flag when either
answer is wrong — evidence must show a concrete string that is matched but
should not be, or should be matched and is not:

- **Too wide.** A safe or unrelated spelling satisfies it: unanchored
  fragments, an alternation branch that is a substring of a safe call, a
  missing word boundary so a near-miss matches, `.*` bridging across what
  should be two findings.
- **Too narrow.** A spelling of the target escapes it: one call shape of
  several, one naming convention, one directory of the surface it claims,
  case or whitespace variants of the same token, the dangerous alias missing
  from an alternation that lists the deprecated one.

Weigh each pattern against its blast radius: a pattern in a rule or checker
gates every future diff; a pattern in a test fixture gates one assertion.
The first kind deserves the FIRES/SILENT treatment `test_adopted_rules.py`
pins — a firing case AND a safe spelling that stays silent.

Do NOT flag: patterns whose looseness is stated and deliberate (a prefilter
ahead of a stricter check); fixture patterns matching fixture-controlled
input; a width the surrounding code visibly compensates for (verify the
compensation exists before staying silent — its absence is the finding).
