---
id: enforcement-truth
severity: MEDIUM
engine: claude
judges: behaviour
applies_to: ["**"]
# Rule bodies quote the very claims this rule hunts — never scan them
# (the secrets-in-diff precedent, applied on day one rather than after the
# first self-hit).
excludes: [".warden/rules/**"]
# applies_to is ** because the corpus put the class nearly everywhere:
# candidate records land warden 15, tests 12, cage 6, docs 4, .warden 4,
# .github 2, skills 1 — no directory carve-out is honest. The name is the
# review charter's own lens ("Enforcement truth"), promoted from prose a
# reviewer must be handed by hand into a rule hashed into rules_version.
# The rule is NOT named `enforcement-claim`: a rule id that equals a
# candidate slug retroactively invalidates every shard filed under
# `unmapped:<slug>` (watched live on fail-open — LEVEL 5 to LEVEL 3).
# The 45 legacy ids are grandfathered in .warden/certification.yaml.
covers: [enforcement-claim]
---
Does anything in this diff say a check exists that does not?

The single most recurrent defect class in this corpus — 45 judged records,
42 upheld, floor 0.821; ahead of fail-open, which got a rule at 39 — and the
one that most directly attacks what this platform is for: the gap between
what is claimed to be enforced and what can actually reject.

Flag two shapes. Both need evidence quoting the claim AND naming the absent
or unreachable mechanism — a hunch that "this is probably unenforced" is not
a finding.

- **The claim without the mechanism.** A comment, docstring, error message,
  README or wiki line, commit message, or config comment asserting that
  something is checked, enforced, validated, gated, required, or guaranteed,
  where nothing in the tree can reject a violation. "The pre-push hook runs
  the suite" when no hook path is configured; "consumers must declare X"
  when nothing refuses a consumer that does not; a ceiling documented to
  users that no caller passes to the code that would enforce it.

- **The signal that never reaches an outcome.** Code that computes a
  violation, contradiction, or failure and then does not let it change an
  exit code, a verdict, or a gate — computed and discarded. The worked
  instances: a contradicted waiver that still left the gated count; a
  falsified answer that flipped a red build green; a `--max-unanswered`
  ceiling shipped wired to no caller. Ask of every newly computed signal:
  what decision does this value reach, and what happens when it is bad?

Do NOT flag: aspirational language clearly marked as such ("should",
"eventually", "not yet enforced" — saying a gap out loud is the fix, not the
defect); claims whose mechanism lives outside the diff when the mechanism
actually exists (verify before flagging); historical records (beads,
attestation notes, dated retrospectives) describing what once held.
