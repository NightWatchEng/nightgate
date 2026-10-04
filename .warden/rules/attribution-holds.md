---
id: attribution-holds
severity: MEDIUM
engine: claude
judges: behaviour
applies_to: ["**"]
# This body quotes miscitations as worked examples, so it must not scan
# itself. Deliberately NOT the whole rules dir, which is what
# enforcement-truth and wiki-fidelity exclude: two of this class's judged
# records landed on OTHER rule bodies (tests-bite.md and pattern-fit.md, each
# miscrediting the corpus record it drew on), and a dir-wide exclude would
# blind the rule to its own evidence.
excludes: [".warden/rules/attribution-holds.md"]
# applies_to is ** because the class landed nearly everywhere — thirty-two
# judged records across five top-level directories and one commit: decision
# shards 11, other .warden files 6 (tags.yaml, two rule bodies, the policy
# contract), tests/ 6, docs/design 5, warden/ 2, cage/run.sh 1, and one
# commit message. No directory carve-out is honest. Stated rather than
# hidden: this repo's own repo.yaml context_excludes hides tests/** from the
# scan, so six of those records sit where this repo's gate cannot look — the
# scope is set by where the class lands, not by what one repo scans.
# The rule is NOT named `evidence-attribution`: a rule id equal to a candidate
# slug retroactively invalidates every shard filed under `unmapped:<slug>`
# (watched live on fail-open — LEVEL 5 to LEVEL 3). The legacy ids are
# grandfathered in .warden/certification.yaml. Covering the class also puts it
# in the rule namespace, which refuses the `unsupported-source-claim` alias
# that used to fold onto it; its receipt is in .warden/memory/tags.yaml, and
# the one record that fold carried is no longer counted below.
covers: [evidence-attribution]
---
Does every claim in this diff that rests on a source say what that source
says?

Thirty-two judged records, twenty-nine upheld, none ever refuted. It is the
counterpart of `evidence-intact`: that rule asks whether a record reached the
corpus whole, this one asks whether what a record SAYS about its source is
true. Where it lands is why it matters — eleven of the thirty-two are on
append-only decision shards, which the next ruling reads as the reason of
record and which cannot be edited in place.

Flag an added or modified line attributing a figure, a quotation, a property
or an act to a named source that does not carry it. Evidence must name the
CLAIM and the SOURCE and say what the source actually says; "this citation
looks thin" is not a finding. The shapes below are the ones that recurred
here, not a general taxonomy:

- **The citation that resolves to nothing.** A symbol, test id, tracked item,
  commit or file named as the authority for a claim, which does not exist or
  is not on the history being described. A function cited three times as the
  sole reason for a routing decision, in a tree where that name had never
  existed; a reopen condition naming a tracked item the tracker does not
  hold, copied into a policy file, a decision shard and a test pin, so the
  pin then resisted the correction; a comment citing the commit where a
  defect "reproduced", on a reverted line of history where a reader who
  follows it gets the opposite result.
- **The count its own source states differently.** A figure quoted from a
  shard, a page, a tracked item or a sibling artifact that the source gives
  as another number — "11 upheld" where the cited shard holds 15, and it was
  the acceptance criterion for a proposed change; `n=98` beside a sibling
  artifact's 103 for one measurement in one PR; "11 of 14 AI-judged rules"
  against a tree holding eleven.
- **Two samples fused into one measurement.** A number measured over one
  population attached to another: a wall clock measured on eight PRs paid on
  thirty PRs it never covered; a RANK from one index paired with a COUNT from
  a different index; a corpus-wide N attached to a conclusion whose own
  source scopes it narrower.
- **The corroboration credited to an artifact or actor that does not carry
  it.** A measurement attributed to the reviewer who did not make it; a
  property asserted to have been "watched" in a round whose artifacts do not
  hold it; work described in the past tense as filed, with nothing filed.
  Three of these landed in successive CORRECTIONS of one another — the shape
  survives the fix that names it, so read a correction's own claims as
  closely as the thing it corrects.
- **The worked example that inverts its record.** A rule body, docstring or
  comment presenting a corpus record as its example while reversing which
  side was the defect, or resting a quoted precision figure on instances that
  are not among the records it was measured over.
- **Coverage claimed against an external source.** A header asserting a set
  is complete against a cited page that lists more than the set. Its only
  corpus instance is NOT among the thirty-two: it was folded onto this class
  by the `unsupported-source-claim` alias, and covering the class refuses
  that alias, so the record this shape is drawn from sits just outside the
  sample every count above is over.

Do NOT flag: a claim in an ALREADY-PUSHED commit message whose correction is
carried on every mutable surface and in a later commit body — two of this
class's three dismissals are exactly that, and the protocol forbids the
force-push that would edit the message; a citation whose source lies outside
the diff and does support it (verify before flagging — reading the source is
cheap, and a wrong flag here is expensive); an append-only record left
standing with its original figure where a later corrective artifact states
the correction, which is how this repo is required to fix a shard; and a
figure its source states as approximate, or marked as a snapshot "as of" a
date. The third dismissal was not a carve-out: the finding held and was
parked at the repair cap as a tracked item.

Read the counts through the caveats the retro that proposed this rule
recorded. ROUND CONCENTRATION: the records come from fourteen review rounds
and one of them contributed eleven. The retro read that round as carrying the
crossing, on the thirty judged it saw; on the corpus as it now stands the
remainder still clears the bar (21 judged, 19 upheld, floor 0.711), thinner
than the whole. Worth knowing before this rule's history is read as fourteen
independent occurrences. And precision here is over FILED judgments — 50 of
the 272 rounds that had filed a record when the retro read the corpus filed a
refutation — so every rate reads high by the share of raised findings that
nobody refuted. Recall is unobservable from this corpus.

MEDIUM is deliberate. `blocking_severities: [HIGH]`, so this reports while it
earns a precision history under its own id; the candidate class's counts do
not carry over to it, and raising it to a blocking severity is a separate
human decision with its own evidence.
