# Memory

Review findings become committed events, so past reviews sharpen future ones.
Reviewers get **patterns** ("this recurred here"); cross-examiners get
**precedents** ("this was refuted here") — deliberately different, so the two
judges don't agree for reasons unrelated to the diff.

There is no graph engine, no ingestion API, and no standing cost. At this
volume an append-only event log *is* the graph: the findings already carry the
structure an engine would be extracting. Full reasoning:
[docs/design/memory.md](../design/memory.md).

## The pipeline

`warden attest write` → `attestation.json` in a gitignored run dir →
`warden memory ingest` → a **committed, secret-redacted shard** under
`.warden/memory/attest/` → a rebuilt derived cache (`findings.jsonl`,
gitignored) → `memory recall` and `memory stats`.

Shard filenames are `<ts>-<sha8>-<digest8>.json`. Distinct by construction, so
concurrent branches never conflict.

### One finding, restated, counts once

A record's id names the **event** — it hashes the head sha — and that is
right: two shards are two reviews, and the corpus keeps both. But
re-attestation is a normal, correct act. The head binding refuses a stale
attestation after a repair round moves the head, so the builder re-mints and
re-attests, and the payload carried forward files every finding again under a
fresh id. Skill pack 0.13.0 narrows what is carried, and does not end it: one attestation now covers ONE round, so a finding CLOSED
in its round is filed once, while a finding still OPEN is re-raised VERBATIM
in the next round's payload — same rule, file, line and text, which is what
the fold below groups on — and files again. The copies that remain are the
open ones, and they fold. Measured on this repo's own corpus **at `1fdda8a` (2026-09-02, 125
shards / 979 records)**: 86 copies — 84 of them in the two most-reviewed PRs
(#167 three times, #172 twice) and 2 in an earlier #114 re-attestation, so
the inflation landed almost entirely where the promotion evidence looked
strongest. The measurement is pinned to a tree on purpose, and this page
states no second figure for "today": the denominator grows with every merged
shard, so any number written here for the current tree is wrong by the next
merge — which is exactly how the first draft of this sentence rotted, between
being written and the branch merging `main`. **Re-measure with
`warden memory ingest` and then `warden memory stats`** — in that order,
because `stats` renders the derived, gitignored cache, and a stale one will
happily reprint a number the shards no longer support. It now says so: every
`stats` run opens with a CORPUS line naming the cache, its rows and its mtime
beside the committed shard count, and warns STALE on stdout and stderr when a
shard is newer than the cache. `memory recall` carries the same
warning, because its empty answer is the one a builder acts on. It prints the current
fold beside the current count. Never check the pin against it.

So a finding has a second identity, derived from its **content** — the rule
as the artifact spelled it, the file, the line, the finding text — and every
seam that counts folds by it: `memory stats`, `memory recall`, certification
and the auto-pause streak all read the same fold. A finding restated in a
later shard counts once and the later shard's disposition wins (confirmed in
round one, fixed in round two, is one finding, fixed). The fold is a **read**;
no committed shard is rewritten to achieve it — shards are append-only
evidence, and editing one is falsifying the record. `memory stats` prints how
many restatements it folded, and says nothing when there were none.

The fold decides what **precision** counts, and nothing about what a **round
said**. Two consequences, both load-bearing:

- The survivor carries the judgments it stands for, not just their count, so
  a later round that *reverses* an earlier one is reported apart from one
  that merely repeats it — `memory stats` names how many folds reversed a
  judgment. A bare count would have moved the sign of a contribution
  invisibly. A reversal means crossing between **upheld** (`confirmed` and
  `fixed` together), **refuted** and **dismissed**, not any change of
  `status`: `confirmed` → `fixed` is the ordinary repair-round restatement,
  the shape the fold exists for, and both sit in the confidence floor's
  numerator, so it moves nothing and is not reported.
- The auto-pause streak reads those carried judgments back. A refutation
  restated across re-attestations stays folded (one judgment carried
  forward is not a fresh round refuting the rule), while an **upheld**
  judgment a later round restated as refuted is handed back to the round
  that filed it — otherwise a round that was mixed as it happened would read
  as all-refuted and the streak would *lengthen*, which is the direction
  that removes enforcement. The fold may shorten a streak and may never
  lengthen one; it is pinned by a property test over random corpora, not by
  this sentence.

`memory ingest` answers every reading of the corpus over the same fold — the
drift ceiling and the unknown-tag list both — so the two commands cannot
disagree about one store. Its `total_records` is the cache's row count (one
row per record id, the shards' complete index), which is a different question
and stays unfolded; it prints the fold beside it so the two numbers
reconcile.

What it does not fold: the same text at another line, in another file, or
under another rule; a gate `detected` record, which is a checker firing on a
commit — four commits it fired on are four facts; and two same-content
records filed by **one** round (the reviewer and the examiner disagreeing on
one finding), because a restatement is by definition in a later round. A
class that genuinely recurs is re-raised in a reviewer's own words; only a
payload copied forward reproduces the text byte for byte.

## Design commitments

- **Secrets never reach the committed corpus.** Secret-class rules store
  `<redacted:rule_id>`, and the three free-text fields a secret can reach —
  `finding`, `evidence`, `reason` — are scrubbed against the gate's own
  patterns, *imported* rather than copied, so the two can never drift. The
  shard envelope is scrubbed separately: `rules_version`, `base_sha`, reviewer
  labels. The structural fields beside them (`file`, `tags`, `severity`,
  `status`, `pr`, `bead`) are written verbatim — they are the record's keys,
  and a redacted key is a record nothing can recall. Tests grep the shards for
  planted keys, shape by shape.
- **Recall keys are (dir_prefix, tags, rule_id)** — never file paths, so
  renames survive; never bare rule_id, because 81% of the original corpus
  shared one id. No embeddings until the corpus outgrows a context window.
- **Split memory.** The reviewer and the examiner get *disjoint* record sets.
  `fixed` records are never injected as open instances.
- **Budgeted injection.** K ≤ 10 records, each rendered as a bounded
  excerpt of at most **200 characters** of the finding text — one line,
  cut at a sentence end or a word and marked `…` — so one finding's length
  never decides whether its neighbours are recalled. The budget is derived
  from K rather than guessed: a 220-character header plus 10 lines of at most
  404 characters (key, excerpt, note, record id, recurrence suffix) is
  **4260 characters** of rendered context, and every allowance is pinned
  against what the renderer emits. The key — rule id and directory — is
  never cut and nothing caps its width, so K lines always fit while every
  key sits inside its 96-character allowance (the widest on this repo's
  corpus was 84 at `c9abf27`); a wider key spends the slack, and a line
  that does not fit is skipped rather than ending the recall. The skip is
  reported, never silent: the header reads `N record(s) of R relevant, S
  skipped: line over budget`, so a recall thinned by wide keys — even to
  zero — never reads as an empty corpus; `no relevant history` is printed
  only when nothing was relevant. Every line ends with `id=<record id>`,
  and the header names the cache: the whole finding is the row with that
  id in `.warden/memory/findings.jsonl` (ensure_ascii JSON, so the id is
  the grep key that always matches where an em dash or a quote in the
  excerpt's own words would not) and in the committed shard. Records are
  ranked by **recurrence key** before recency: the (rule, directory) shapes
  with a fresh instance first, the most-recurrent shape leading, one record
  per shape per pass, so ten restatements of one shape do not crowd out nine
  others. Each line's `[recurred Nx here]` counts the shape over every
  relevant record, not only the ones shown. `warden plan` renders its priors
  through the same excerpt, each with its `id=`; its JSON packet keeps the
  full text, the markdown says so, the command prints the packet's path,
  and a prior skipped for budget is named in the packet's **Caveats**.
  (a flat 1500-character budget against whole findings of
  500-1500 characters returned 0 or 1 record over 505 relevant ones.)
- **Memory is context, never policy.** Nothing under `.warden/memory/` reaches
  `rules_version`.

## Two sources, never mixed

`ingest` sweeps attestations (judgment: confirmed / refuted / fixed /
dismissed) and gate artifacts (deterministic hits, status `detected`).
Detections inform the reviewer as recurrence priors and appear in stats as
hotspots, but they are **excluded** from the confidence-floor math, from
promotion, and from pause streaks.

### An artifact is ingested only if its commit is still reachable

Both stores are guarded, in **opposite directions**, and the asymmetry is the
point.

A **gate** artifact fails **closed**. Validating a gate rule means a mutation
proof — plant the pattern, watch the gate fire, revert — and without the check
that procedure filed synthetic findings as real detections against a commit no
longer in the branch. The check is three-valued, and an *undetermined* answer
skips too, because a fabricated detection is worse than a lost one: the next
`warden review` re-derives a real detection anyway.

An **attest** artifact skips only on a **definite** "no ref contains this".
Nothing re-derives a judgment, so dropping a review round is the worse error
and an undetermined answer ingests, saying so. What it does refuse is the
orphan: an attestation whose branch was squash-merged or abandoned names a SHA
nobody can resolve back to code in the tree, and re-minting its shard on every
sweep dirtied a clean tree until someone deleted it by hand.

**Both** guards run *after* the event-identity check, and the order is the
whole mechanism. Asked first, the check reported artifacts whose event was
already a committed shard as unreachable — 9 of 11 on this repo — and told the
reader to re-run, which would have filed a second shard for a review already on
record. Asked after, it only ever decides whether to *mint* a shard the store
does not hold.

One loss is accepted rather than hidden: an attestation orphaned between
`attest write` and ingest (an amended commit) is dropped. That shard was
already dead to the gate — `attest check` reads the PR's own range and the
pre-amend SHA is not in it — so the recovery is the re-attest you would have
had to run anyway, and the skip message says so.

It goes inert in exactly one case — there is no git repo at all, where there
are no revert semantics to guard. A repo git cannot *read* keeps the guard
active, because "could not look" must not become a clean sweep.

### One run dir, one shard, ever

The identity check asks the **shard store** whether an event is already filed
— and that store is committed, so it is **branch-scoped**, while `.warden/out/`
is gitignored and **branch-independent**. Switching branches flipped the answer
to "no", so ingest re-minted every run dir whose shard lived on another branch.

That is worse than duplication: a shard for another PR's review round appears
staged-ready in an unrelated branch's tree, and one `git add -A` files it into
that PR's range, where `attest check` credits a review that never ran there.

So a run dir records which shard it produced, and a sweep that finds the event
absent from *this* store reports **NOT RE-FILED** instead of minting. The
marker is a hint, never the authority — a shard's digest moves when its records
reserialize, so the normalized-identity check still decides, and the marker is
rewritten to whatever shard actually holds the event. A shard genuinely lost
before it was committed is re-filable by deleting the marker, which the refusal
message names by path.

Three properties worth knowing:

- **Attest only.** Gate shards are gitignored working state the next
  `warden review` re-derives; marking them removed that path and made a
  deleted gate directory unrecoverable by hand.
- **It lives beside the run dirs, not inside one.** A run dir is sealed
  evidence whose manifest records its file list; writing into it would make
  that manifest wrong for every ingested run.
- **A failed write is reported, not swallowed.** The marker *is* the guard, so
  a silent failure leaves it unarmed and the next sweep on another branch
  re-mints the thing it exists to prevent.

**What this does not cover**, stated because the fix landed with the gap open:
a run dir that predates the marker, on a branch whose store lacks its shard and
whose commit is still reachable — an open parallel PR — mints once, and is
marked from then on.

Every run dir predating the fix starts unmarked, but *unmarked* is not the same
as *exposed*: the first sweep marks every one whose event the store already
holds. Measured on this repo at landing, that was 86 of 88, and the two it left
are the genuine orphans, unreachable from any ref and therefore inert on every
branch. The reachability guard covers that squash-merged subset, which is the
shape that was actually recurring.

Gate shards are re-derived by the next `warden review` and `attest check`
ignores them, so they are local working state. **Never commit them** — rung
**R-11** fails certification for a repo that tracks one, the same way **R-08**
does for the derived cache. The asymmetry with the attest shards beside them is
the whole point: an attestation is evidence; a gate shard is a checker firing,
possibly on a probe.

## A clean review is still a review

A shard is a review **event** carrying records, not a container that exists
only because there were findings. While records were the only reason to write
one, a clean round produced none — so `attest check` answered NO ATTESTATION
for exactly the PRs that had passed review cleanly. The gate punished the good
outcome.

A findings-free attestation now files a shard recording the event, satisfying
`attest check` while adding **no record**: it never appears in recall, never
adds to a rule's `n`, and never moves precision. Trading a fail-open for a
corrupted denominator would be the worse bargain, so both halves are
test-pinned.

Shards written before the envelope carried a verdict are **not** migrated. The
tally counts them apart as *no verdict recorded* rather than guessing — a
derived value written into committed evidence stops being evidence.

## One `rule_id` policy, enforced at both ends

`attest write` rejects a finding whose `rule_id` neither resolves against the
repo's rules dir nor is a well-formed `unmapped:<defect-class>`. That guards
only the *writing* end, and the promotion gate reads the **corpus**. So
`ingest` applies the same check — imported from the same module, never
re-implemented — to every artifact it sweeps.

One reader-side exception: an id a declared rule already **answers** —
a bare covered slug, `unmapped:<covered-slug>`, or `unmapped:<declared-id>` —
**migrates** onto that rule instead of rejecting, keeping the original as
`legacy_rule_id` provenance, and every migration is reported. Write-side
refusal reaches an author who can fix the id; ingest's rejection hits legacy
artifacts with no author present, which meant permanent corpus loss.

A rejected artifact is rejected **whole**, and the sweep continues. A shard is
one review event, so dropping just the offending finding would file a shard
whose records disagree with the artifact they name.

The **rule_id** guard sits at the artifact → shard seam only, and that scope
is the whole of the claim. A shard committed before a rule existed keeps
loading however its ids now resolve: this stops new unresolvable ids arriving,
and it does not retroactively invalidate committed evidence.

Read it as being about rule_ids and nothing wider, because the SHAPE guard
below it is deliberately retroactive. The readers of this store share ONE
definition of what a shard is, and that definition is a declared table rather
than a hand-kept list of three
fields: `warden/memory.py`'s `RECORD_FIELD_CONTRACT` names the type of every
field `build_records` writes — all 22 of them — and a PRESENT value of the wrong
type is refused for each. Three of the 22 are refused when ABSENT OR EMPTY too —
`ts`, `rule_id`, `status` — because every counting seam buckets by them and the
empty default folds distinct records into one bucket, which makes a promotion
bar easier to clear rather than harder. The other 19 DEFAULT at their readers: a
render or filter field has an honest empty value, and refusing it would be a
blocking refusal measured only against this repo's own corpus. `memory ingest`'s
cache rebuild additionally requires a usable `id`, because it is the only reader
that indexes by one.

**Five readers, not four**, and the fifth is the one a consumer meets most
easily. `records_from_shards`, `review_events`, `memory ingest`'s cache rebuild
and certification's E-02 read the committed shards; `load_records` reads the
DERIVED cache at `.warden/memory/findings.jsonl`, which `memory recall`,
`memory stats` and `warden plan` go through. It validates against the same
table. The cache is gitignored and survives a branch switch, a half-finished
sweep and a hand edit, so it is the likelier source of a malformed row, not the
safer one — and because it is derived, its remedy is different: delete it and
run `warden memory ingest`, which is what the refusal says. `warden plan` is the
one reader that does not refuse at all: priors are advisory, so it plans anyway
and records the reason in its **Caveats** block.

A committed shard that fails the table is refused by name at every reader, and
the corpus cannot be rebuilt until it is repaired at its producer. No platform
version has ever written such a shard; a hand-written or hand-repaired one may
be one. [Adopting](Adopting.md) states the upgrade cost, shape by shape,
including what "repaired at its producer" does and does not mean for a shard
already committed.

## The promotion bar

A rule earns promotion to a deterministic checker only when the corpus shows it
is **reliably** right, not luckily right. Two conditions, both required:
at least 10 judged cases, and a 95% confidence floor of 0.70 or better.

The confidence floor (a Wilson score lower bound) answers: *given this many
observations, what is the worst true rate consistent with what we've seen?* It
exists because raw percentages lie at small sample sizes.

| Evidence | Raw score | Confidence floor | Verdict |
|---|---|---|---|
| 3 of 3 | 100% | 0.44 | held — perfect, but on almost nothing |
| 9 of 10 | 90% | 0.60 | held |
| 10 of 10 | 100% | 0.72 | **promote** |
| 45 of 50 | 90% | 0.79 | **promote** |

A perfect 3-for-3 loses to a 45-of-50 with a *worse* raw score. Three lucky
hits shouldn't make policy.

Symmetrically, a rule refuted three times running becomes a **pause**
candidate — cheap and reversible, because being wrong loudly beats being wrong
quietly. "Times running" counts **review rounds, never findings**: a round
extends the streak only if every finding it judged for that rule was refuted,
and one upheld or dismissed judgment resets it. Counted over findings, the
streak measured the order a single reviewer wrote its list in — permuting one
round's findings moved the number between 0 and 5 over an unchanged corpus.

### Noticing a crossing

`warden memory watch` compares the corpus the working tree shows now against
the newest watch record committed under `.warden/memory/watch/` and reports
what crossed a bar since:
a candidate over the promotion bar, a rule in pause or demote territory, a
skill file over the skill-change propose bar. It edits nothing;
`--record` writes a dated record for a person to commit, and refuses while
any input it read is uncommitted. The baseline is read from HEAD and the state
from the working tree, so when the two are not the same tree the note names
every one of the four inputs that differs from HEAD — repo.yaml, the rule
files, the tag vocabulary and the attest shards — with the count of INPUTS
and the count of PATHS said apart, and a rename named by both of its paths,
each half credited to the input it lands in when the rename spans two. Every differing INPUT is named; an input with many
differing paths names the first three and says how many it held back, because
an unnamed input is a reading nobody knows moved while an unnamed path sits
under an input already named.
All four move the reading, so naming only the shards left an uncommitted
`covers:` line in a rule file silently dropping a candidate from the report. An input git cannot be asked about at all — a
`rules_dir` declared outside the repo — gets its own sentence and is reported
UNKNOWN, never folded into the count that differs. A corpus or record it cannot read is
UNREAD (exit 3), never a quiet run. A subject already in the record is not reported
again, even after leaving the bar and coming back, until a newer record drops
it, and a record takes in everything over a bar when it is written.
`.github/workflows/memory-watch.yml` runs it weekly and reports on one GitHub
issue, because a bead cannot be filed from CI.

### What a promotion proposal must carry

The bar says **when** a rule may be promoted. The **micro-test protocol** says
what evidence the proposal itself carries: a **no-guidance control arm** first
— if the control does not exhibit the failure there is nothing to fix, stop —
then **5+ reps per variant**, variance reported as a metric, and every flagged
match manually read rather than trusted from automated counts.

A proposal that ran no micro-test is still shippable, but only by saying so: it
is explicitly marked unvalidated, with the reason. Certification **S-05**
refuses an artifact carrying neither the evidence nor the marker.

## Backtest artifacts

Every rule change — promote, pause, retire, reword, adopt, demote, narrow, or
unpause — carries a committed JSON record under `.warden/memory/backtests/`
showing what the corpus said about the class when the change was made.
**S-05** requires at least one at the current `rules_version`.

**S-04 counts a promotion through the loop.** That is a promotion record
under `.warden/memory/promotions/` whose `rule_id` names a declared rule, which
records `"proposed_by": {"source": "retro", "date": "YYYY-MM-DD"}` and a
`proposal` citing where the retro proposed it, and whose `backtest` names a
`promote` artifact here for the same rule. That artifact must carry `window`
and `derived_from`, and its `judged` and `upheld` must clear the promotion bar
(the floor is recomputed from those two, not read from `wilson_lb`). The
record is a separate file because backtests are append-only. A candidate over
the bar is not a promotion, and neither is a rule written by hand or by a
synthesis pass. Nothing proves a retro ran — the provenance and the counts are
taken as recorded, which is what the pass detail says — so a reviewer reads
the `proposal`, which cites the retro's own record rather than the artifact it
corroborates. In this repo five records count: `fail-closed`,
`enforcement-truth`, `tests-bite`, `tests-required` and `attribution-holds`.

Required fields: `rule_id` (must name a declared rule, or for a `retire` the
rule it removed), `action`, and `judged` (an integer case count). Record the rest of what you derived — counts, rate,
floor, window, method — so the decision can be re-examined.

**Pauses are bound both ways**. A `pause` artifact stamped
with the **current** `rules_version` must name a rule that ruleset actually
shows `paused:` — evidence for a change the tree does not contain is refused.
And every paused rule must carry its own pause artifact, so one artifact cannot
evidence a second pause bundled beside it.

That second obligation is deliberately about **existence, not freshness**: an
artifact at an *older* `rules_version` still counts. A standing pause needs
evidence, not fresh evidence. `rules_version` is a hash over every rule file
plus `repo.yaml`'s `review:` subtree, so demanding a current-version artifact
per paused rule would mean re-stamping every standing pause on every unrelated
ruleset edit — evidence for a change that did not happen in that revision, which
is exactly what the first obligation refuses. `warden autonomy pause` writes the
rule edit and its artifact together; a **hand pause needs the artifact written
by hand**, once, or S-05 fails naming the rule.

**An adoption declares nothing it did not derive**. Adding
a rule file bumps `rules_version` exactly as pausing one does, so
`warden autonomy adopt` writes an `action: adopt` artifact beside the rule —
without it the tree that command produces fails S-05 and a merged machine
adoption silently drops a certification level. The artifact records `judged: 0`
and omits the precision fields a `promote` artifact carries: the rule has
produced no findings yet, and an `engine: claude` rule cannot be replayed over
the tree, so a zeroed tally would read as one that was measured. The command
writes that artifact only for a rule it adds itself: it refuses a rule whose
severity is in `blocking_severities`, and it refuses a rule file that already
exists. So a rule file added by hand, at any severity (the rule-advisor path),
and a rule at a blocking severity each carry an `action: adopt` artifact
written by hand, as a promote, retire or reword artifact is. When no artifact
exists at all, S-05's failure lists who writes one for each action.

**Demote, narrow and unpause are rule changes too.** Each edits a rule file —
a severity moved out of the blocking severities, a smaller `applies_to`, the
pause fields removed — so each moves `rules_version`. They are what
`warden rules lifecycle` proposes and what lifting a pause does, and no warden
command performs any of them, so each carries an artifact written by hand at
the new `rules_version`, under its own action.

**A retire names a rule the tree no longer has.** Retiring a rule deletes its
file, so its `rule_id` names no declared rule. S-05 reads that id from git
objects instead, never the working tree: some `.md` file committed in HEAD's
history, directly inside a rules dir, must have declared it. A rules dir is
the one `repo.yaml` declares now or any `review.rules_dir` the `repo.yaml`
beside it declared in a commit, so moving the rules dir keeps its history.
The verdict reads that outcome, not which commit removed the rule, so one
change split into several commits or squashed into one is judged alike. It
follows that a retire stamped with the current `rules_version` is not tied to
a rule removed in that version. A rule file committed as a symlink is not
followed. A refused retire counts the symlinked rule files it could not read
and names up to three, saying how many more there are only when it cuts the
list. A symlinked rule file counts when its last committed state before any
deletion was a symlink, or when no regular file at its path yielded an id; a
path whose last committed file was a regular rule history read is left out.
Some layouts are not read, and a refused retire names each of these that
history holds: a rules dir outside the repo root, such as `../shared-rules`;
a rules dir, or any directory above it, committed as a symlink or a submodule;
and another path where a version of this `repo.yaml` was committed and which
no longer holds one. Git records a move as a delete and an add, so reading
the rules dir that path declared could read another project's rules. What
marks the path as this project's is one commit that took a version away from
it and wrote that same version at this `repo.yaml`, as `git mv` does: a
sibling project's `repo.yaml` or a fixture, even one copied from this one, is
named only when such a commit removed it. These are not every layout history
cannot read. A `repo.yaml` moved and edited in one commit is not named, nor
one deleted and added in separate commits when no single diff of a commit
against one parent holds both. A merge is diffed against each parent, so a
delete and an add it brings in are named only when both are new relative to
one parent: that parent still holds the version at the old path and does not
yet hold it at this `repo.yaml`. A branch holding both, merged with `--no-ff`,
is named; a merge whose one parent brings the delete and the other the add is
not.
Rules dirs are named from the directory holding `repo.yaml`, also when it
sits below the top of its git tree. When git
cannot answer — not a repository, no commit, an object the store lacks — the
artifact is refused and the failure says so; when the id is missing from a
shallow clone's history, the failure says the clone is shallow.

**Current changes are bound to the tree they claim.** As a `pause` stamped
with the current `rules_version` must name a rule the ruleset shows paused, a
`retire` must name a rule the ruleset no longer declares, an `unpause` a rule
it shows unpaused, and a `demote` a rule whose severity is outside
`blocking_severities`.

Other change classes — a body edit, a narrower `applies_to` — are *not* bound to the
diff: detecting them needs the previous ruleset, and the platform keeps no
ruleset history of its own. Nor does S-05 audit an artifact's
arithmetic. S-05's pass line says which bound it applied.

A **promote**-action artifact stamped with the *current* `rules_version` — the
live promotion claim — additionally carries `validation`: either the micro-test
record or `{"status": "unvalidated", "reason": "..."}`. Artifacts at older
versions are committed history, judged by the bar of their day, never
revalidated retroactively.

Backtests are **append-only, never regenerated in place**: each decision gets a
new file, `<rule>-<action>-<date>.json`. Refreshing one from a newer corpus
would make two branches conflict on one path — the exact defect R-08 exists to
prevent — and would rewrite the evidence a past decision was judged on.

## Lenses and rounds — outcome, not precision

A record can carry the `lens` that raised the finding
(`code-reviewer`, `cross-examiner`, `scoped-re-reviewer`, `builder`, or
`crew:<slug>` for a charter lens — the attestation schema's `$defs.lens`) and
the `round` it arrived in. `memory stats` tallies outcome per lens and per
round in a `LENSES` section: confirmed / fixed / refuted / dismissed counts,
and nothing more — no Wilson bound, no promotion, no pause, because a lens is
not a rule. Below the rows the section accounts for each field's gap on its
own line, printed only when that gap has records in it, so neither one can
hide inside the other. Records that carry no lens are counted on their own
line and folded into no lens: every record written before the field shipped
is one, and `code-reviewer` is not the default answer to a question those
records were never asked. Records that DO carry a lens but no usable round
get the other line — `N lens-attributed record(s) carry no round` — and they
sit in a lens row above while appearing in no round row. That line is about
the FINDING, not the roster: `memory ingest` copies `round` off the finding
and never off the roster entry, so a finding that stated no round (or stated
one warden cannot read — `0`, a float, a bool) lands here even when the
roster it joins to stated a round. On a corpus with no such records the line
is absent rather than zero, which is why this repo's own `memory stats` shows
one accounting line today and not two. The section exists to make
one question — should review depth scale to risk? — answerable once enough
attributed rounds have accrued; it decides nothing itself.

Beneath `LENSES`, a `DISPATCHES` section reads the committed ROSTERS rather
than the records: per lens, how many roster entries were
dispatched, how many returned, and what each came back with — `reviewed-clean`,
`reviewed-findings`, `no-review` or `unknown`. `no-review` is stamped three
ways: a dispatch that did not return; a returned output with no findings that
names no file the diff changed — a definition by what the output SHOWS, not by
what the reviewer did; and a `no-review` the builder declared, which stands
whatever the output names, because lowering an outcome needs no corroboration.
The middle case is usually a refusal, a classifier decline or a truncated run, which until the field shipped was indistinguishable from
a clean review inside the zero-yield denominator the review-depth ledger leads
with. But it is also every genuine clean review run from a 0.16.x pack against
a warden carrying the field: that pack never tells a reviewer to name what it
read, so its clean reviews land here too, and nothing is refused (see the
0.17.0 row in [Skill Pack](Skill-Pack.md)). `unknown` counts entries with no
`outcome`, or with one nothing cross-checked — every shard written before the
field, or attested without `--review-dir` — and is never upgraded to
`reviewed-clean`.

Beneath `DISPATCHES`, a `REVIEWER SEATS` section scores the crew the way
`DECLARED RULES` scores rules. Its seats are the roles `graph.yaml`'s `review`
block declares (every round's roles, then the closure and light rounds'), and
each gets one row, at `n=0` when it raised nothing: n raised, confirmed /
fixed / refuted / dismissed, `wilson_lb` over confirmed + fixed exactly as a
rule row computes it, and `unique` — how many of its findings no other
declared seat raised on the same rule, file and line in the same review round,
with that count's share of n. A seat that runs alone in its round (the
scoped re-review, the closure round) reads 100% by construction, so the share
compares only seats that share a round. A finding belongs to the seat that
FIRST raised it — the lens on its earliest copy — and carries the fold's
final disposition; a round-2 or closure re-filing never moves it into the
re-filer's row. At `n=0` the floor and the share print `-`, never a measured
`0.0`. A lens no
seat declares (older `crew:<slug>` records, `builder`) is counted on one line
and given no row, as is a finding whose earliest copy names no lens. The rows are read-only: nothing promotes, pauses or
re-seats off them, and a crew change stays a `graph.yaml` proposal the founder
merges. A repo with no `review` block gets one line saying there is no seat
to score; a `graph.yaml` warden cannot resolve withholds every row and prints
the cause (`SEATS WITHHELD`), so an unreadable crew never reads as a crew that
raised nothing. A consumer on a pin that predates the section sees no
`REVIEWER SEATS` header at all; nothing else in `memory stats` moves.

## Candidate rules — the report's other half

`memory stats` prints two precision sections, because the corpus makes two
different kinds of statement.

- **Declared rules** — precision of the rules that exist.
  `lang-conventions: n=3 … wilson_lb=0.438` is a *measurement*.
- **Candidate rules** — the `unmapped:` namespace: recurring defect classes
  **no rule covers**. `unmapped:fail-open` at n=7 is not a rule scoring 0.646;
  it is seven findings reviewers kept making with no rule behind them. That is
  a *proposal*.

A candidate is **never** promotable to a checker — it has no rule to automate.
Its promotion is *"write this rule"*: a different action, with a different bar.

| | Declared rule | Candidate |
|---|---|---|
| The action | automate it — a checker that auto-rejects, hashed into `rules_version` | write it — a prose lens, founder-merged, pausable |
| Cost of being wrong | PRs rejected by a checker nobody can argue with | one more lens a reviewer weighs, and pauses if it misfires |
| The bar | 10+ cases AND the confidence floor ≥ 0.7 | **3 or more judged cases** and more than **0.5** upheld — a raw majority |
| Ranked by | precision | recurrence |

The candidate bar is lower on purpose: the bar tracks the **cost of being
wrong**, and writing a rule is the same cheap, reversible act as pausing one.
It uses a raw majority rather than the statistical floor because the floor's
job is to stop a small sample from setting policy that auto-rejects — and a
candidate line sets no policy at all. Applied here, the floor would have
silenced the corpus's most recurrent class (`enforcement-claim`, 10 of 11
upheld, floor 0.623). Classes under the recurrence bar collapse into a
`watching` roll-up with no floor: below three cases a confidence bound is a
number with nothing behind it.

A refutation streak is a second, separate bar. Three straight refutations make
a *declared* rule a pause candidate; a candidate has no rule to pause, so the
streak blocks the proposal instead — a class reviewers just rejected in three
rounds running is not one to go write a rule about, however good its older
history looks.

**Candidate counts never carry over into a promotion.** Evidence for automating
a rule has to be earned *as* that rule, under its wording. Enforced at both
ends: once a declared rule covers a class, `attest write` and `ingest` reject
`unmapped:<slug>` findings for it and name the rule to file under — for as long
as that rule is unpaused. A paused rule's re-opened class accepts the slug
again, so the re-proposed row can accrue fresh evidence.

### Writing a proposed rule is a coordinated change

The slug names the **class**, never the rule id. The moment a rule's id *is*
the slug, or a rule `covers:` it, every shard that ever filed under
`unmapped:<slug>` stops resolving and certification **E-02** fails. That is not
an edge case — it happened on the first rule this loop ever proposed, and
certification fell from Level 5 to Level 3.

So writing one is three steps, and `memory stats` prints them beside every
`PROPOSE RULE` line:

1. give the rule an id that **differs** from the slug, and declare
   `covers: [<slug>]` in its frontmatter;
2. **grandfather the committed ids** — add `unmapped:<slug>` under
   `grandfathered_rule_ids:` in `.warden/certification.yaml`. Committed
   evidence is never re-filed to satisfy a check. The declaration is loud on
   purpose: the file is HIGH-tier, E-02 names every id it waives, and the
   promotion gate can never count them. It is not a write-side floor, though —
   `attest write` refuses the slug only while an **unpaused** rule answers the
   class, so pausing the covering rule re-opens it and new records file under
   the waived spelling and are absorbed on unpause;
3. carry the backtest artifact at the **new** `rules_version` (S-05).

Steps 1 and 2 are both required — `covers:` is itself one of the conditions
that invalidates the history, so step 1 alone leaves E-02 red. `rule-advisor`
drafts all three together, which is why the retro hands an accepted proposal to
it rather than writing the rule file.

## Lifecycle

A corpus with no lifecycle ends up with old findings outranking current ones.
Two soft horizons: past **90 days** a record is de-ranked — it still exists, it
just stops outranking current evidence — and
past **365 days** it is no longer recalled at all.

Nothing is deleted. Shards are committed evidence, and evidence does not expire
because attention does. `memory stats` reports the age distribution so
staleness is visible rather than guessed at. An unparseable timestamp counts as
fresh: a clock problem must not silently erase history.

## Tag vocabulary

Tags are a recall key, so two names for one defect class split the index and
the recall that should have fired never does. Nothing errors; it just stops
working. The vocabulary is declared in `.warden/memory/tags.yaml`.

- `ingest` **warns** on a tag outside the vocabulary, never rejects — a
  genuinely new defect class must be nameable the moment it is found, and a
  review is a bad time to argue about taxonomy.
- `stats` reports **near-duplicates** (`fail-open ~ failing-open`) by folding
  case, separators and word endings. Reported, never auto-merged: only a human
  knows whether two names are one concept.
- Declared-but-unused tags surface too. Dead vocabulary is worth pruning.
- When the retro **merges** two names, the loser becomes an `aliases:` entry.
  Shards are never rewritten, so the alias folds at the read seams instead. An
  alias applies only when it cannot lose information — neither side may name
  anything in the **rule namespace**. Anything else is reported as INVALID
  ALIASES and left alone.

### Three dispositions, and the drift ceiling

A warning nobody is obliged to answer is a warning the count outgrows. This
repo's vocabulary was reconciled reactively three times — 4 undeclared names,
then 6, then 26 — while the warning fired correctly the whole time.

So a name in the corpus has exactly three honest dispositions:

| block | meaning |
|---|---|
| `tags:` | **declared** — this is a class, and here is what it means |
| `aliases:` | **folded** — this is another spelling of a declared class |
| `left_undeclared:` | **left** — seen, and here is why it is neither, yet |

The third is the one this repo added. A class below the declaration bar cannot
honestly be declared and must not be folded on a guess, so the truthful thing
is to write down that it was seen — machine-readably, not in a comment. Its
reason carries the same substance floor an answer's does. A receipt that
names a condition for re-reading itself states it in one key, never in prose —
`trigger:` beside `reason:`, carrying any of `judged_at_least`,
`upheld_at_least` and `rule_live`, or `trigger: none` — and a plain reason
pairing a re-read verb with a number or a backticked name is refused, naming
the key.

A repo **may** then declare a ceiling in the same file, in the shape
`.warden/catalog-answers.yaml` already uses for the guardrail gap:

```yaml
ceiling:
  max_undecided: 0
  rationale: >-
    why this number and not another
```

It bounds the names carrying **none of the three** — deliberately *not* the
count of undeclared classes at the declaration bar. Measured over this repo's
own corpus, five of the eight at-bar classes were coined in the three days
before the reconcile and a single review round took two brand-new names past
the bar together, so bounding that count would force a taxonomy argument
inside the PR that coined them, against the soft governance above. Clearing an
**undecided** entry costs one line and decides nothing about the class — "n=1,
coined this round, watching" is a receipt — so the ceiling can sit at 0.

- `memory stats` renders the ceiling whether or not it is in breach, lists the
  undecided names, and separately lists the classes **at the declaration bar**.
  That second line is a re-read list: it names the `left_undeclared:` entries
  whose evidence has outgrown the reason recorded for them.
- `memory ingest` **reports** a breach on stderr and in its
  `ingest-result.json`, and still **exits 0**. That is deliberate:
  [`cage/run.sh`](The-Cage.md) gates publication of a run's attestation shard
  on this exit code, so failing here would discard the evidence of the very
  round that coined the drifting name; and the retro reads a non-zero as "the
  corpus is unread" and would stop before the ritual that clears the breach.
- **`memory check-vocabulary` enforces it.** Its exit code means one thing:
  `0` the declared ceiling holds, `1` it is breached (every undecided name on
  one line, with the three remedies), `2` it could not evaluate — no ceiling
  declared, any of the FOUR declaration blocks unreadable, or an unreadable
  committed shard. All four, not the two `ceiling:` and `left_undeclared:`
  this sentence used to name: `tags:` and `aliases:` joined them in
  #190, because a non-mapping block loaded as `{}` and every name it
  declared or folded then read as undecided — a RED with no explanation, and
  a verdict prescribing the `aliases:` entry the loader had just discarded. `2` is never a pass: a step whose only job is
  this check must not go green with nothing to check, which is why a repo
  with no `ceiling:` gets `2` here where `rules recommend` would report and
  exit `0`. It reads the **committed** attest shards only — never the derived
  cache or the gate shards, which CI cannot see — the same input this repo's
  guard test reads, so the check and the guard cannot disagree. `memory
  stats` shares the *definition* but not the input: it renders the local
  cache, which lags a `git pull` until the next ingest and carries gate
  shards too, so its ceiling line can differ from the check's verdict. The
  check writes `vocabulary-check.json` into a run dir so the verdict leaves
  evidence. Its reading also names any **declared alias the loader refused**
  — a typo'd target, or every alias at once when a rule file stops parsing —
  because a refused alias folds nothing, so a name you already folded is
  still counted as undecided, and the breach line's "fold it with an
  `aliases:` entry" would otherwise send you to re-write an entry that is
  already there. Run
  it from a job triggered on `pull_request` and the obligation lands on the
  branch that drifted. `examples/hello-svc` does exactly that, and
  `scripts/portability-sim.sh` proves the step bites — clean `0`, a planted
  undecided tag `1`, a one-line receipt `0` again, a broken declaration `2`. This repo additionally keeps its pytest guard, which pins
  the declared **value** — something the command deliberately does not do.
- **`attest write` refuses** (exit 2, before anything is written), in a repo
  whose vocabulary declares a ceiling, a finding tag that would breach it or
  a declared rule id used as an undeclared tag, naming the declared tags, and
  refuses any tagged payload while that declaration cannot be read — the
  ceiling reading the guard applies at pre-push, moved to where no shard
  exists yet to delete. In the same repo it also refuses (exit 2) a payload
  whose own records — filed, or a committed finding re-filed with a new
  status — would break a `left_undeclared:` receipt's premise: fire its
  `trigger:`, or take its name AT the declaration bar (3+ judged, more upheld
  than not) where the receipt is missing, does not say `AT the declaration
  bar`, states no `n=<judged>`, or states an `n` below the judged count. So
  "n=1, coined this round, watching" is a receipt until the class reaches the
  bar; the round that takes it there names it, and the receipt is re-argued
  from the bar, the name declared or folded, or the finding re-tagged. A
  premise the committed shards already broke is not refused, the number of
  names at the bar stays unbounded, and `memory check-vocabulary` is
  unchanged. An unreadable committed corpus refuses any payload with
  findings, since no tag rules out a restatement. A warden pinned before
  this refusal writes such a payload, and a repo with no guard of its own
  never hears of it.
- A repo that declares **no** ceiling has nothing to breach. There is no flag
  that overrides a declared one — a ceiling you can pass on the command line
  is not a declaration.
- **Check your pin before declaring one.** A warden older than this feature
  reads only `tags:` and `aliases:` and ignores unknown top-level keys, so a
  `ceiling:` block on an older `platform.pin` is silently inert — you would
  believe you had an obligation and have none. Confirm the version your
  `.warden/bin/warden` shim resolves to before you rely on it.
- A declared ceiling that cannot be parsed is a **complaint, never headroom**,
  and an unreadable vocabulary makes every tag count undecided rather than
  none: one typo must not read as a clean bill of health.
- Raising the number is a human edit that should replace the rationale with
  one arguing for the new value. The loader can only refuse a rationale that
  is not an argument; it cannot tell whether the argument fits the number, so
  this repo additionally pins the value in its guard test.

## Stats measure precision only

**Recall is unobservable from the review corpus**, and every stats and retro
output says so verbatim. Escapes — defects review saw and passed, later fixed
on main — are mined separately as the only recall signal, with an explicit
guard against blaming drift introduced after the reviewed diff.

**And precision is over filed judgments.** The attestation is written after
adjudication, so a candidate the cross-examiner refuted reaches the corpus
only if the round files it — and for most of this repo's history the
protocol did not say to: when it was measured (2026-09-02, #186), 107 of
125 shards carried zero refutations while the examiner's own verdicts ran at
roughly a third refuted. A rate computed
over survivors measures what a refute-first arbiter let through, not what the
rule raised, and reads high by the arbiter's kill rate. The pre-pr-review
skill now files every refuted candidate with its reason (`status: refuted`
is a first-class disposition; the schema requires the reason), the examiner
reads those records back as precedents, and `memory stats` prints how many
rounds that FILED a record filed a refutation, beside the numbers that bias
touches — a denominator deliberately distinct from the `with findings`
verdict bucket printed two lines above it, which counts something else. It cannot see the examiner's verdicts, only what reached the shard,
so it names the gap without asserting a cause for any one round.

## The retro loop

Weekly. It gathers the policy contract, `memory ingest` **then** `memory stats`
(stats reads the gitignored cache; an unrun ingest is SAID
rather than silent — stats opens with a CORPUS line naming the cache it read,
its row count and mtime, and the shard count beside it, and prints a STALE line
there and on stderr when a committed shard is newer than the cache, so it can
no longer render an empty or behind corpus as a clean one), the cage ledger, `rules recommend`, bug-type items,
`graph validate` warnings, and the previous retro. Then it mines escapes.

It proposes with citations or not at all: promotion (only above the bar), pause
(by default on streaks), rewording (citing dismissal reasons), new lenses
(citing the escapes they would have caught), **adopting a missing guardrail**
(saying whether the row rests on local evidence or prior art, and what it
costs), tag reconciliation, topology changes, and dropping what caught nothing.

Two inputs, two kinds of learning. The corpus says what went wrong **here**;
the gap analysis says what the platform already knows goes wrong everywhere and
this repo does not yet guard. Reading only the first is how a loop learns
solely from its own scars.

The retro **proposes, never edits.** Every accepted proposal becomes a tracked
item landing through a gated, founder-merged PR. A null retro on a thin corpus
is a valid retro.

---

Next: [Writing Rules](Writing-Rules.md) · [Graph Layer](Graph-Layer.md) ·
[Roadmap](Roadmap.md)
