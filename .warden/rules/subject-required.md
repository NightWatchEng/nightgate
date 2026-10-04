---
id: subject-required
severity: MEDIUM
engine: claude
judges: behaviour
# Where the class landed, read at adoption on 2026-09-26: tests 3 of the 5
# judged records (two detector
# helpers in one test module, a quotation reader in another) and
# warden/schemas 2 (one schema pattern, filed under two rules — the one
# defect the body below assigns to `pattern-fit`). So warden/** rests on no
# record this rule claims as its own shape; it is included because a
# detector predicate in gate code has the blast radius of a rule — a guess,
# and said to be one. The gate's own scan hides tests/** through repo.yaml's
# context_excludes, so three of the five sit where this repo's gate cannot
# look; the scope is set by where the class lands, not by what one repo
# scans.
applies_to: ["warden/**", "tests/**"]
# The class is the declared tag `unanchored-pattern` (.warden/memory/tags.yaml):
# a pattern matching without requiring the thing that identifies its
# subject. Every one of its records at that reading was filed under a
# declared rule (`pattern-fit` four, `fail-closed` one), so the class never held an
# `unmapped:` candidate row and no committed shard names it as one.
# This rule declares no `covers:` and no `implements:`, on purpose. The
# machine tier's carve-out refuses an adoption carrying either: covering a
# slug makes `attest write` refuse `unmapped:<slug>` from that commit on,
# which raises enforcement at the write seam whatever the severity, and
# crediting a catalog entry moves a count CI gates on. Both are a person's
# decisions, so a rule carrying them is a person's PR. Findings of this
# class file under this rule's id, tagged `unanchored-pattern`, which stays
# a declared tag; the id is not the tag's name, so the tag never enters the
# rule namespace under a rule's own name.
---
Does each added or modified detector — a predicate function, a pattern, a
schema constraint — require the thing that identifies its subject before it
fires?

Read at adoption on 2026-09-26: five judged records, five upheld, across
three review heads, on four distinct defects (one schema line was filed
under two rules in one round), all tagged `unanchored-pattern`. Wilson floor 0.566 at that count: above the bar for writing a rule (3+
judged, upheld more often than not), well short of the bar for blocking.
Precision is unmeasured — an `engine: claude` rule does not replay over
history — so the 5/5 is corpus history, and the adoption artifact beside
this rule records `judged: 0`, which is the only figure an adoption can
honestly declare.

This is the mechanism behind a class `pattern-fit` already names by its
effect. `pattern-fit` asks whether a PATTERN — regex, glob, alternation,
prefix test — matches exactly the set it claims, in both directions. Three of
the four defects here were not patterns at all but predicate logic in
detector functions, filed under `pattern-fit` because no rule named what
they had in common: each fired on a SHAPE without first establishing that
the thing in front of it was its subject. The line between the two: a
finding is this rule's when the repair is adding the identifying predicate;
it is `pattern-fit`'s when the subject is already established and the repair
is tightening the shape matched. File under one, never both. The fourth of
the four defects — a schema pattern ending in `$` where the engine lets `$`
match before a trailing newline — falls on `pattern-fit`'s side of that line:
its subject was established and its repair was the anchor. It is counted in
the five records above and is not a shape of this rule.

Flag when a detector fires on something that is not its subject, with the
concrete input named — "this looks loose" is not a finding. The shapes below
are the three that recurred here:

- **Admitted by shape alone.** A guard for git invocations that accepted any
  argv carrying a dash flag or a leading subcommand, so a package
  initialiser, a vocabulary tuple, a prose list and an argparse call were
  all reported as unpinned repository starts. The repair required
  git-identifying evidence, and every real instance was still caught.
- **Neither predicate its message names.** A closed-environment detector
  whose message spoke of an `env=` argument on a git subprocess, and which
  verified neither: it fired on a non-git environment and on a plain
  data dict keyed by `HOME`.
- **A scope wider than the subject.** A reader that judged a quoted command
  by every word on the line rather than by the argv inside the quotation
  marks, so a flag mentioned in the surrounding prose was attributed to the
  command it sat beside — and the guard that depended on the reading was
  never asked the question it existed to ask.

Do NOT flag: a prefilter whose looseness is stated and whose downstream
check establishes the subject (verify the downstream check exists — its
absence is the finding); a fixture detector over fixture-controlled input;
a detector whose subject is genuinely defined by the shape it matches, where
there is no further predicate to require.

MEDIUM is deliberate. `blocking_severities: [HIGH]`, so this reports while it
earns a precision history under its own id; raising it to a blocking severity
is a separate human decision with its own evidence.
