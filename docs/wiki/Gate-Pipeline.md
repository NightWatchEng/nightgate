# Gate Pipeline

Policy → enforcement → evidence → provenance. Every rule is declared in config,
enforced by something that can auto-reject, and leaves an artifact bound to
commit SHAs and a `rules_version`.

The runtime order is drawn once, at
[Architecture Level 4](Architecture-4-Sequence-Bead-to-PR.md). This page is
what each step *means*.

## Three judgments, and none of them is the builder grading itself

**1 · Deterministic.** `warden verify --scope X` runs the commands declared in
`repo.yaml`. Nothing else. Exit status per command, recorded.

The gate pairs a result to a review on **both** axes — the commit and the
scope. A verify result for another commit is not evidence about this one, and a verify result from other commands is not evidence
about this diff: a `--scope example` selfcheck once rendered a
green line for a diff that changed only `warden/`, and, on a commit that had
both, the newer of the two scopes displaced the stronger. The required scopes
are derived from the diff's changed paths, so what the runner happened to run
cannot decide what the gate asks for.

A step says what it exercises with `covers: [glob, ...]`, and where it does the
DECLARATION is the reach: the scope is required exactly when a
changed path matches one of those globs. This repo's `example` scope is declared
that way — `covers: ["examples/hello-svc/**"]` — and its `tests` scope declares
`covers: ["**"]`.

Where a step declares nothing, the reach is INFERRED, and an undeclared scope
claims less than the whole tree only when two declarations agree:
every one of its steps runs in a subdirectory, and each such `cwd` sits inside
a declared `components:` area — the component's own path, or an ancestor of
that `cwd` (before the declaration existed, this repo's `example` scope narrowed
on exactly that: cwd
`examples/hello-svc` inside the `examples/` component). A scope whose steps
sit in two declared areas claims both, and one step at the root claims
everything. A `cwd` on its own is a runner convenience an author
may have typed for any reason, and reading it alone as a reach declaration
would drop a scope whose commands exercise everything from that directory. A
missing scope is reported per scope, never folded into a neighbour's green
line, and everything unknown — an unresolvable range, an empty one, paths no
scope claims — requires every declared scope.

**2 · Mechanical rules.** `warden review` evaluates `engine: python` and
`engine: declarative` rules against the `base...head` diff. Findings at a
severity listed in `blocking_severities` exit 1. This is the half that runs in
CI, at $0, with no model call.

**3 · Adversarial judgment.** `engine: claude` rules defer to a pre-PR review
session: an independent reviewer proposes findings, and a cross-examiner that
**never sees the reviewer's reasoning** tries to refute each one. The reviewer
has `authority: find` and never decides; the cross-examiner has
`authority: judge` and its verdict is binding. A finding survives only if
confirmed.

Both judges read the same bytes — the package `warden diff` writes at a
per-round path, not a live `git diff`, so two concurrent rounds cannot argue
about different ranges. They are fed **disjoint** priors from review memory
(patterns to the reviewer, precedents to the examiner) so they do not start
agreeing for reasons unrelated to the diff. See [Memory](Memory.md).

## Rule engines

| Engine | Runs | Cost to own |
|--------|------|-------------|
| `declarative` | warden executes `checks:` declared in the rule's own frontmatter | a rule file — no Python, no platform change |
| `python` | a checker in warden's core pack or the project's `.warden/checkers/*.py` | a reviewed Python module |
| `claude` | deferred to the pre-PR review's reviewer + cross-examiner | judgment, recorded by `warden attest` |

Declarative checks fail at **load** time on a bad pattern, unknown key, or
duplicate id — enrollment breaks loudly instead of a rule silently never
matching. Full authoring contract: [Writing Rules](Writing-Rules.md).

## The attestation

`warden attest write` is a **fail-closed step, not a receipt**. It refuses:

- a **dirty tree** — an attestation stamps HEAD as what was reviewed, and a
  dirty tree means the review saw code HEAD does not contain;
- a `rule_id` that neither names a declared rule nor is a well-formed
  `unmapped:<defect class>`;
- a `clean` verdict with findings still marked confirmed, or `findings-open`
  with none;
- any key in the payload beyond reviewers, findings, and verdict;
- any string in the attestation (finding text and tags, a roster `agent` or
  `output`, and every other string `memory ingest` copies into a shard)
  quoting a **home directory path** outside every worktree of the
  repository: `/Users/<name>` or `/home/<name>` (the placeholders the tree's
  home-path scan allows aside), or a path under a `$HOME` that pattern does
  not cover. The refusal names where the string sits and, inside a finding,
  its rule_id. A path under the checkout or any linked worktree, including
  one whose folder names hold spaces or brackets, is not refused where it
  begins a path; it is rewritten repo-relative, in the attestation and so in
  the shard. Each path is rewritten at its own boundaries
  (`a=<root>/x&b=<root>/y` becomes `a=x&b=y`), though a path that walks out
  of every worktree when read as one, as after a folder named `a=`
  (`<root>/a=/../../x`), is not rewritten. A final `..` is a segment
  whatever follows it, unless a name character continues it (an ASCII
  letter or digit, `_`, `.`, `@` or `-`, the characters of a home
  directory's name): punctuation, a `:12` line suffix, or an `&k=v` or
  `#L12` tail after it stays outside the path, so `<root>/x/..` becomes `.`
  and `<root>/x/../..?` walks out as `<root>/x/../..` does. A final segment
  of three or more dots that no name character continues is read both ways,
  as a name and as `..` before what trails it, and is rewritten, as the
  name, only when both readings stay inside a worktree (`...hidden` is just
  a name): `<root>/internal/...` becomes
  `internal/...`, while `<root>/...` and `<root>/x/../...` walk out. A worktree whose name only
  begins the quoted folder's (`r2` for `r24`) falls back to the checkout. A
  path that does not begin one is left as written: behind a URL scheme, a
  `host:` prefix or a drive letter, or joined by a colon to the text before
  it (the second entry of `PATH=<root>/bin:<root>/lib`). Under a checkout in
  a home directory, the usual `/Users/<name>/...`, such a path still quotes
  that home, so it is refused, and the refusal names the text it follows;

- a **roster it can disprove**, when `--review-dir` names the round directory
  holding each reviewer's raw output. In full, because a
  refusal the wiki does not name is one a consumer meets as an unexplained
  exit 2: a `--review-dir` that is empty as a path, missing, not a directory,
  or unlistable; a roster entry that is not an object; a reviewer with no
  `returned`, a non-boolean `returned`, or (having returned) no `findings`
  count and no `output`; an `output` that is not a plain basename, is a
  symlink, is absent, is not a regular file, or is empty; two reviewers
  resolving to one file; a roster where nobody returned; and a report left in
  the directory that no roster entry claims. Also a
  claimed report that is byte-identical to a report in any sibling round
  under the rounds root, or to another report claimed in the same round —
  one artifact backs one roster claim, once;
- under `--review-dir`, **an attribution it can disprove**: a
  roster `role` outside the lens vocabulary, a roster entry with no `round`,
  a finding with no `lens` or `round`, or a finding attributed to a lens no
  entry declares, to a reviewer that did not return, or to a round that lens
  was not dispatched in.

Verified means the claim was backed by an artifact that existed at attest
time. Evidence for a claim, never proof that a reviewer ran.

### The reviewer roster

Before #168 the roster was taken verbatim, so a shard could name a
reviewer that never ran and no check in the platform could see it. PR #166
produced exactly that and a human caught it one commit before the false
provenance became permanent in an append-only corpus.

Each roster entry now carries `returned`, a `findings` count and the
`output` basename of that reviewer's raw report, and `--review-dir` checks
the claim against the files. A reviewer that was dispatched and did NOT come
back is declared `returned: false` rather than dropped — a silently shorter
roster is the same lie in the other direction, and it is ENFORCED the same
way: the check lists the directory as well as walking the roster, so a report
no entry claims is a refusal naming the file.

Omitting `--review-dir` does not refuse; it stamps `roster_verification:
unverified-roster` into the attestation, and that marker rides into the
committed shard, so the corpus records permanently which rounds were checked
and which were only asserted. Absent on shards written before the field
shipped, and absent is **not** verified: it means the question was never
asked.

What `verified` proves, exactly: the roster's claim was backed by an artifact
that existed at attest time. That is what refuses a reviewer asserted out of
nothing. It does **not** prove a reviewer ran — a caller that writes
plausible files itself passes — and nothing in warden claims otherwise.

### The round binding

A second of three markers of the same shape rides beside it — the third,
`base_ancestry`, is below. `round_binding` records
whether the reviewer outputs came from a round **warden itself minted**
(`warden round new`), and whether that round was minted for **this** commit:

| state | means |
|---|---|
| `bound` | warden minted the review dir and its `head_sha` is the attested head |
| `unminted` | a directory was passed to `--review-dir` that warden did not mint, so warden has no identity for it and cannot say which commit its reports describe |
| `no-review-dir` | none was offered, so the question was never asked |

A round minted for a **different** head is not a state — it is a refusal, exit
2, because those reports describe another commit (a
foreign branch's review package once landed in a builder's round with a *matching*
base sha and read as plausible on every axis anything checked).

Like `roster_verification` and unlike `range_binding`, this one does not stop
at stderr and the gitignored run manifest: `attest write` stamps it into the
attestation, `memory ingest` carries it into the committed shard, and `attest
show` prints it. So the corpus records permanently which attested rounds
warden actually minted. Absent on shards written before
the field shipped, and absent is **not** `bound`.

### The base binding

Which base the shard names is decided before the two markers above are
stamped and before the ancestry reading below is taken, and it is not a marker
of their shape: it rides the run manifest only and is never stamped into the
attestation, because the `base_sha` it chooses IS the stamped field. What it
records is where that field came from.

The defect it closes (PR #291): `warden round new --base
origin/main` resolves that name once, records the sha in `round.json`, and
builds the review range from it, so the reviewers read exactly that range.
`warden attest write --base origin/main` then resolved the same name a second
time, hours later. In a repo where other work lands while a branch is in
flight the two resolutions are different commits, and the shard recorded the
later one — a base that was not an ancestor of the branch, whose declared
two-dot range reported an unrelated merged change's work as deletions.
Measured live, in a shard that reached main: 10 files recorded where the round
the shard attests had 5.

So the base is taken **from the round** whenever `--review-dir` names one that
warden minted for this head, and `--base` is resolved and stamped only where
there is no such round to take it from. `base_binding` records which happened:

| state | means |
|---|---|
| `round` | the round's `round.json` names a base, it resolves in this repository, and `--base` still resolves to the same commit — the round's base is stamped, and the flag agrees |
| `round-base-differs` | the round names a base and `--base` now resolves to a different commit — the ROUND's is stamped, because a shard declares the range that was reviewed and not one the flag happens to name at write time. Warden measures only that the two shas differ: a ref that moved, a round minted `--base origin/main` and attested `--base main`, and an annotated tag whose object sha the round recorded all look like this, so the detail names both shas and the name the round recorded, and leaves the cause to the reader |
| `named` | the round was minted for ANOTHER head, so its base is not taken and `--base` is stamped — and `round_binding`, which runs inside `build` after the roster check, refuses this document. Taking a foreign round's base first would import a range from the very artifact the next check is about to disprove |
| `no-round` | there was no round to take a base from — no `--review-dir`, a directory with no `round.json`, or a `round.json` that records no `base_sha` — so `--base` is stamped, which is the behaviour that predates the reading |
| `round-unreadable` | the round's `round.json` is there and cannot be read — `--base` is stamped here, and `round_binding` refuses the document from inside `build`, after `verify_roster`, so the roster's message is the one a reader meets first when both would fire |
| `round-base-unresolvable` | the round names a base that is not a commit this repository has — a shallow clone, or a base a force-push dropped — so it cannot be stamped and `--base` is stamped instead. Reported, not refused: refusing would buy nothing at the trust boundary below |

`base_binding_detail` rides beside the state with the sentence behind it: for
`round`, the base sha the round was minted against; for `round-base-differs`,
both shas and the name the round recorded; for `named`, that the round was
minted for another head; and for the three fall-back states, what could not be
used — the flag that was not given, the directory with no `round.json` or no
`base_sha` in it, the manifest error, or the recorded sha this repository
cannot resolve. Both are written to the run manifest, never to the
attestation.

It never refuses, and in two directions that is deliberate. A base that is not
an ancestor of the head is a READING, `base_ancestry` below, because 88 of
353 committed shards recorded one when the field shipped and a legitimate
non-ancestor base is reachable by any branch whose base moved past its fork
point before the mint. And `round.json` is a file under a directory the
CALLER names, so a hand-written one with a matching `head_sha` lends whatever
base it likes: `round` and `round-base-differs` cannot tell a minted round
from a written one. That is the platform's standing trust boundary — `--base`
was equally the caller's — and `verify_roster` states it for the whole
surface. The state names read like warden's own provenance and are not.

**What the attest run manifest carries**, in full, because a reading recorded
for an auditor and taught nowhere is one the auditor does not know to look
for. Every non-refusing reading `attest write` takes is written to
`manifest.json` in its run dir under the gitignored `.warden/out/`, beside the
fields every command's manifest has, for the reason the platform gives
throughout: the unattended cage has no terminal, and a signal that lives only
in scrollback cannot be audited for afterwards. Manifest only — a reading that
needs no schema field keeps the HIGH-tier artifact contract untouched:
`range_binding` (`ok | out-of-range | empty-range`, § The range binding);
`patch_binding` (`recorded | empty-range | unavailable` — whether the
per-commit patch-ids that let a content-preserving rebase still match this
attestation were computed, § A rebase does not invalidate an attestation);
`base_binding` and `base_binding_detail` (this section); `carry_forward`
(`distinct | unminted | no-review-dir`) and `carry_forward_detail` (the
re-run paragraphs under the base ancestry); and `chain_verification`, the
state `verify_chain` reached — `first-round` for a round numbered 1, which
has nothing before it to chain to (the number is the higher of the round
`warden round new` minted and the highest `round` the roster states, so in a
directory warden did not mint it is the roster's own label; `verify_chain`
also returns `first-round` when neither gives a number, which only a library
caller reaches, because under `--review-dir` `warden attest write` refuses a
roster entry that states no `round`); `nothing-carried` for a
later round whose payload carries no `fixed`, `dismissed-with-reason` or
`refuted` record, so the question did not arise; `chained` when every such record restates a
claim an earlier round committed to this branch (a record that does not is a
refusal, not a state); `no-review-dir` when none was offered, so it was never
asked; `closure-round` when `verify_closure` applied the stricter rule
instead; and `no-root`, which `warden attest write` cannot produce because it
always passes the repository root, and a library caller can. In the manifest AND
stamped into the attestation, so the committed shard carries them too:
`roster_verification` (§ The reviewer roster), `round_binding` (§ The round
binding), `base_ancestry` (§ The base ancestry), and, on a closure round
only, `closure_verification`. All four that reach the shard are read off the
document rather than recomputed for the manifest, so the printed note, the
manifest and the shard cannot disagree about one round.

### The base ancestry

A THIRD marker of the same shape, and this page is where the contract is
taught, so it carries the table. `base_ancestry` records whether the base the
attestation names is an ancestor of the head it stamps:

| state | means |
|---|---|
| `ancestor` | the base the shard names is reachable from the head it stamps, so `git diff base..head` over this shard's figures is a range that HOLDS — it is not a claim that the range is only this change, and the caveat below says why |
| `not-ancestor` | the round was minted against a base that had already moved past the branch, so the base the shard faithfully records is not reachable from the head, and `base..head` diffs against a tree this branch never had: the base-only work arrives INVERTED — a file the base added as a deletion, one it deleted as an addition, one it modified as the opposite modification — and a line both sides touched is fused into one hunk reporting the BASE's line as the one this change removed. Not meaningless, and not correctable file by file either: the fix is the three-dot `base...head`, which diffs from the fork point and is the range the round's reviewers read |
| `unavailable` | the question could not be answered: no git to run, no repository to ask, or a base sha THIS clone cannot resolve — a shallow clone, a pruned fork point, or a base a force-push dropped. The last of those is the one a committed shard most plausibly carries, and it is never read as `ancestor` |

**`ancestor` does not mean "this change and nothing else".** Ancestry says the
base is reachable from the head; it says nothing about what ELSE the head
reached. A branch that merged its base branch forward — the ordinary answer to
a moved `origin/main` — is still `ancestor`, and `base..head` then reports the
base branch's own work alongside this change's.

**And `not-ancestor` does not mean the range is meaningless.** It means
`base..head` is a diff against a tree the branch never had. Measured, with the
base advanced and not merged forward: the base-only work comes back inverted —
a file the base DELETED prints as `A`, one it added prints as `D` — and where
the base and this change touched the same line, one hunk prints the base's
line as removed and this change's as added, so this change's own before-state
is nowhere in the output. The reading that is not lossy is the three-dot
`base...head`: it diffs from the fork point, it is what the round's reviewers
read, and it is computed from the same two fields the shard already carries.
So neither state hands a reader a range that needs no thought: `ancestor` can
carry the base branch's merged-forward work, and `not-ancestor` needs the
third dot.

Separating this change from work merged INTO it is a third question, and
ancestry answers it in neither state — the third dot does not either, because
merged-forward work IS in the branch's history. `range_patch_ids` cannot
answer it: it is built from `git log --no-merges base..head`, which lists the
merged-forward side alongside this branch's own commits. The nearest reading
off the shard's own fields is
`git log --first-parent --no-merges <base_sha>..<head_sha>`, and it is nearest
rather than exact: it walks the branch's FIRST-PARENT line, so it excludes the
merged-forward side, and, measured, it also excludes a commit that reached
this branch through a merge of the branch's own topic branch. Read it as the
first-parent line, not as every commit this branch wrote. Those two sha fields
are what a later reader has, because the round's own package lives under
`.warden/out/` and is gitignored.

It is a READING and it never refuses: 88 of 353 committed shards recorded a
non-ancestor base when the field shipped, and failing on the reading would
have failed history that cannot be re-attested. What changed is where the
reading lives. Warden always computed it and printed it to stderr, into a run
manifest under `.warden/out/`, which is gitignored — so the committed shard
carried the moved base with no committed caveat. `attest write` stamps it into
the attestation, `memory ingest` carries it into the shard envelope, and
`attest show` reads it off the document rather than recomputing, so the printed
note, the manifest and the shard cannot disagree.

Absent on shards written before the field shipped, and absent is **not**
`ancestor` — the same rule the two markers above carry.

**A round does not survive a post-review commit.** The binding is bound to
the head `round new` minted the round for, so any commit between the mint and
`attest write` — the fix for a finding the round raised, a test-fixture fix, a
comment, a merge of `origin/main` — moves head and the write is refused.

**And a committed shard does not cover a later commit.** This paragraph used
to say the opposite, as a stated limitation: membership was the whole join, so
a commit landing after the shard passed CI on the earlier attestation and
whether it got a fresh round was the protocol's rule, not a check. It is a
check now. `attest check` computes the **uncovered set** —
the commits in `origin/main..HEAD` that no TIP attestation is an
ancestor-or-self of — and exits 1 when any of them touches a path outside
`.warden/memory/attest/`, naming the commit, the paths, and the remedy:
re-review this head. The carve-out is that one directory because exactly one
commit after a round is forced by the protocol — `attest write` stamps the
head, `memory ingest` writes the shard from it, committing that shard moves
head — and its whole diff is the round's own evidence. A decision shard, a
doc, a test fixture or a merge of `origin/main` is content, and content is
reviewed. Measured before it shipped, over the 39 merged PRs in
`pr/230..pr/269` that still carry refs: **16 would have been refused**, 11 of
them for merging the base branch (18–94 files arriving behind a round that
read none of them). The wider spelling `.warden/memory/` refuses 14 — the two
it lets through, #243 and #266, both ride free on a `.warden/memory/decide/`
shard, so the narrower prefix is the correct one and not merely the stricter
one. Every one of the 16 was at `cap: 2` with no way to re-attest, which is
why this waited on the closure round and shipped after it.

The corpus records the re-mints builders chose, and refusals leave no shard at
all. That cost was measured before being accepted rather than narrowed
(recorded with `warden decide record` and corrected once by a
second record): over the 132 committed attestation shards, 4 were re-attestations of an earlier round on the same branch line: 3 of the 4 carried a change under `tests/**` on the branch itself — reviewed surface, and the class the ruling's own hazard note names (a test fixture *is* reviewed; PR #172's CI red was in exactly that class) — and 1 of the 4 was forced by a merge of `origin/main` alone, whose branch-side diff was the attestation shard and nothing else. One merge-only re-mint in 132 shards does not buy a
narrowing; a refusal that can be argued down is not a refusal, and narrowing
what invalidates a round waits for a count that says otherwise.

The re-mint is a **re-run**, never a copy. `verify_roster` is satisfied by
any distinct non-empty regular file, and a file copied from an earlier round
is exactly that: PR #172's `code-reviewer.json` was byte-identical across
rounds 2, 3 and 4 — round 1's output carried forward — and every one of them
verified. `attest write` now hashes each claimed report and
refuses one whose bytes match a report in **any** sibling round still under
the rounds root, or another report claimed in the same round, naming the
reviewer, the file and the round it matches. Every sibling, not the previous
one: a check against only the preceding round would let round 1's bytes into
round 4 the moment rounds 2 and 3 were cleaned up — and a source round that
is gone, or lives under another clone's rounds root, is out of the check's
reach, which is why the protocol's re-run rule is the bound and the check is
the backstop. Warden does not read the report — identity is the whole test —
so the 0.12.0 skill has every reviewer name three things in its raw output: the
head it judged (`judged`), the round it ran in (`round`) and its own role
(`role`). Those are the three
coordinates of a report's identity, and `judged` alone covers only one of them: on a clean
PR both reviewers of one round write the same `{"judged": H, "findings": []}`,
and a round re-minted at the same head reproduces the earlier round's bytes —
two honest states that the refusal would otherwise read as copies. Named all
three, an honest report is distinguishable in both comparisons and only a
genuine copy collides; a reviewer on an older skill that writes a canonical clean `[]`
twice is refused as well, and the message says what to change. A sibling whose
`reviewers/` cannot be listed or read is a refusal, not a pass — the path is
resolved before its parents are walked, and entries are examined with
`lstat`, which raises where `is_file()` on CPython 3.13+ swallows EACCES; a
sibling with no `reviewers/` at all has nothing to have been copied from and
is skipped, while one whose `reviewers/` is a **link to nothing** is refused
rather than read as absent — `iterdir()` raises the same error for both, and
they are opposite answers. The within-round half of the comparison runs even
for a directory warden did not mint: it needs no round identity, and
`unminted` is precisely where `verify_roster`'s inode key cannot see a `cp`. The reading (`carry_forward: distinct | unminted | no-review-dir`,
with its detail) is recorded in the run manifest only — the refusal is the
mechanism, and it needs no field on the HIGH-tier attestation contract.


### The lens and the round

The next structured claim on a roster, and the first on a finding. The
verified roster says how many raw findings each reviewer returned;
nothing said which reviewer a RECORDED finding came from, so
per-lens outcome — how often each lens's findings end fixed, refuted or
dismissed — was unrecoverable from the committed corpus (measured 2026-09-02:
0 of 979 records named a lens, 0 a round), and the roster's `role` was free
text with 22 spellings for about 7 lenses.

Every finding now carries the `lens` that raised it and the `round` it
arrived in, and every roster entry the `round` it was dispatched in. The
vocabulary is the schema's `$defs.lens`: an enum of the four roles the
platform's own skills dispatch (`code-reviewer`, `cross-examiner`,
`scoped-re-reviewer`, `builder`) plus `crew:<slug>` for a lens the repo's
`## Review charter` declares. Two shapes on purpose: the enum members are the
same in every enrolled repo, while charter lenses are each repo's own, and a
platform enum of this repo's three would make a consumer's charter lens a
platform schema change. The shape governs spelling — exactly one of
`crew:fail-closed`, `crew-fail-closed`, `fail-closed` and `fail-closed review
(independent, post-hoc)` is valid — not which lenses exist.

Under `--review-dir` all of it is required and joined: a `role` outside the
vocabulary, a roster entry with no `round`, a finding with no `lens` or
`round`, or a finding attributed to a lens no entry declares, to a reviewer
that did not return, or to a round that lens was not dispatched in, is a
refusal (exit 2). Without `--review-dir` the fields are optional — the
committed corpus predates them and the schema is additive-only — but a finding
that volunteers a lens is still joined to the roster. Deliberately not
checked: the roster's raw `findings` count against the attributed findings,
because one raw finding naming two files is honestly filed as two records.

One skew cell is a refusal by design, and it is the cell the roster check (#168) did
not have: that change hung `returned` on a NEW flag, so an older skill pack's
invocation stayed valid; this hangs `lens` and `round` on a flag consumers
already pass. A consumer that bumps its platform pin past #184
while its plugin cache still holds a pack older than 0.9.0 is refused on the
first `attest write --review-dir` — exit 2, and the message names the cause
and the remedy (update the pack). Update the pack in the same step as the
pin; the pack's own probe handles the other direction.

`memory ingest` carries `lens` and `round` onto each record and `memory stats`
tallies outcome per lens and per round — outcome rows, not precision: no
Wilson bound, no promotion — and under them an accounting line per gap, each
printed only when it has records: the records that carry no lens, counted
apart and folded into none, and the lens-attributed record(s) that carry no
round. The second counts what the FINDING stated, never the roster —
`ingest` copies `round` off the finding only. A `DISPATCHES` section beneath
reads the rosters instead: per lens, dispatches, returned, and what each came
back with — `reviewed-clean`, `reviewed-findings`, `no-review`, or `unknown`
for an entry with no `outcome`, which is never upgraded. Nothing is retrofitted:
per-lens data accrues from the date this shipped (#184).

### The range binding

`range_binding` is the third marker, and the weakest of the three by design: a
heuristic over changed paths rather than an identity check.

`attest write` **warns without refusing** when the findings name no file the
attested range changed — the shape a stale findings file takes when a write
failed silently. It warns rather than blocks because some rules produce
out-of-range findings by construction, and refusing would kill honest rounds.

The reading is also written into the **run manifest** as `range_binding: ok |
out-of-range | empty-range`, so an unattended run can be audited for it after
the fact rather than the signal living only in scrollback.

`memory ingest` applies the same check at the shard boundary, at the same
strength. It never calls `attest.build`, so an artifact that
was hand-written or hand-edited used to reach the corpus with no binding at
all — the same argument that put the `rule_id` check at that seam. It **warns
and never refuses**: refusing at the reader would be strictly worse than at the
writer, because by then the review is done and the only way through is deleting
a true finding. EVERY attest shard gets a reading in the ingest run dir's
`range_bindings`, one of five kinds — `ok`, `out-of-range`, `empty-range`,
`no-range` (the artifact declares no range) and `unresolved` (git could not
read the one it declares) — because a list holding only problems cannot tell a
clean sweep from one that could check nothing. Only the two that are WARNINGS
carry a message and reach stderr. A range whose shas no longer resolve is
recorded as `unresolved`, never as the `empty-range` that would send a reader
hunting a stale findings file that is fine.

That run dir is where the signal ends: it is gitignored, nothing carries the
reading into a committed shard, and neither `memory stats` nor the retro
reports how often the warning fires. That is a recorded decision, not an oversight
 — corpus visibility would need an `attestation.schema.json`
field plus a change to ingest's fixed envelope field list, and no consumer
wants the number yet.

### A rebase does not invalidate an attestation

Branch protection commonly requires a PR be up to date with its base, and
`gh pr update-branch --rebase` rewrites **every commit SHA in the range**. The
check joins shards to commits by SHA, so a valid attestation of a
byte-identical tree stopped matching and the required gate reported NO
ATTESTATION — three PRs on
[shortfall](https://github.com/NightWatchEng/shortfall), the live consumer,
were re-reviewed from scratch before this was understood.

`attest write` therefore records the **stable per-commit patch-ids** of the
range it attested, and the shard carries them. When no SHA matches, the check
asks whether every patch-id the attestation covered is still present in the
range by content. Per-commit, never the aggregate range diff: the aggregate is
a diff against the base *tip*, so it moves the moment the base does — which is
the situation this exists to survive.

The substitution is **recorded, never silent**, and the record states only
what the match established. A pass carried by content says `matched by
PATCH-ID`, names the SHA the shard actually attested — a commit no longer in
this range — and **counts the commits in the range that review did not cover**.

That count is the point. The match asks whether every patch-id the attestation
covered is still present; it does **not** ask whether anything else was added
since. This is the **attestation** surviving by content; a review **round**
survives nothing — see [the round binding](#the-round-binding) — because its
reports describe one head and `attest write` is refused at any other. Neither does the SHA join, whose contract is likewise membership: *a*
commit in this range carries an attestation. A first cut of this narrated "the
same change, different commits" unconditionally, which is false the moment
anything lands after the review — a reviewer reproduced a PASS with ten
unreviewed commits alongside. A compensating control that tells the reader
something untrue is not a control.

**A range containing a merge commit is refused outright.** Patch-ids come from
per-commit diffs excluding merges, so a merge's own contribution — conflict
resolution, or an evil merge — would be invisible to both the match and the
count. A branch that cherry-picks one reviewed commit and then merges in
unreviewed content passed at exit 0 under a first cut, with the gate printing
*"Every commit in this range was covered by that review"*; without the fallback
that same range fails closed. Refusing costs nothing real, because `--rebase`
produces a linear range by construction.

Be precise about what that buys: it restores the **count's completeness**, so a
matched range's report is true. On its own it does not make the pass stricter —
`git merge --squash` smuggles the same content linearly, and the merge case was
worse only because the gate could not see the smuggled content and said so
affirmatively. What makes it bite is the third rule below, which reads that
same uncovered set and refuses it.

Membership is the **join**, not the whole rule. Two more rules run on whichever
join carried it.

The LAST attestation on each line of history in the range must record
`verdict: clean`: a branch abandoned after round 1 used to
pass on that round's committed `findings-open` shard, because per-round
attestation commits one BY DESIGN and the check never read the field. Only the
tips are asked — an earlier round's open verdict is exactly what the protocol
is supposed to leave behind — so what this closes is the abandoned branch.

And no **uncovered** commit may ship content. Uncovered means
no tip attestation is an ancestor-or-self of it; ancestry, because that is what
a review can honestly be said to have seen. Such a commit is refused unless
every path it touches sits under `.warden/memory/attest/`, the one commit the
protocol forces after a round. This is what closes the unreviewed commit
sitting beside a reviewed one, which under membership alone passed on both
joins. A merge is read against its **first parent**, never the combined diff:
`git show --name-only` on a clean merge prints nothing, so a check reading
that would call a merge of `origin/main` an empty change and wave through the
largest class of all — 11 of the 16 refusals measured on this repo's own
history.

"Last" is by **ancestry**: a matched shard no other matched shard descends
from. Not list order — `git rev-list --topo-order` linearises a partial
order, and on a merged range it emits one parent's whole line before the
other's, so reading the first entry let a sibling line's `clean` shard answer
for a branch whose own round left findings open (the stacked-branch shape
`orchestrate` produces). A merged range therefore has one tip per line and
every one of them binds, in either direction: content whose review never
closed rode in whichever side it came from. Re-reviewing the merged head
attests a commit that descends from all of them, which leaves a single tip.

So what a patch-id match proves: the reviewed change survived a rebase, in a
linear range. What it does not prove: that this commit was reviewed, or that
nothing else rode in beside it. Both are on the line the gate prints.

Shards written before this shipped carry no patch-ids and match by SHA exactly
as before.

**`attest write` does not produce the committed evidence.** It writes
`attestation.json` into a gitignored run dir. `warden memory ingest` is what
sweeps that into the committed shard under `.warden/memory/attest/`. Skip
ingest and there is nothing to commit — and CI's `attest check` reports a
commit carrying no attestation. A pull request from a fork is the one PR the
platform's own CI (`.github/workflows/ci.yml`) does not ask for one, because
the review cannot run on it there: a maintainer applies the change to the
private repository, where it earns its attestation (CONTRIBUTING.md,
*Contributing from outside*). The gate `warden init` writes for a consumer
runs the same fail-closed check, with the same fork exemption; a fork PR,
which gets no secret, installs the public platform with no key and reaches it.

A **clean** review is still a review. A findings-free attestation files a shard
recording the event (SHAs, `rules_version`, reviewers, verdict) and adds no
record: it never appears in recall, never adds to a rule's `n`, and never moves
precision. Before that, a clean round produced no shard at all, so the gate
punished exactly the PRs that had passed review cleanly.

## `rules_version` — which verdicts are comparable

`rules_version` hashes the rule files, the checker code, the findings schema,
your project checkers, and `repo.yaml`'s `review:` subtree. A rule edit, a
checker edit, or an enforcement-key flip is a *policy* edit: the version
stamped on every subsequent artifact moves.

That stamp is what makes verdicts comparable. Two reviews judged under the same
`rules_version` were judged by the same policy, so their results can be counted
together — a **cohort**. Two under different versions cannot, even on the same
file: the rules moved underneath them. The memory layer never averages a
finding from one policy with a finding from another.

Deliberately excluded: review memory (context, never policy) and the graph
(observation, never enforcement).

## The sticky comment

One PR comment the gate rewrites in place on every run, instead of appending a
new one each time — so a PR carries the current verdict, not an archaeology of
past ones. warden posts it over stdlib HTTPS with `GITHUB_TOKEN` and has no
`gh` dependency, so CI behaviour does not vary with the runner's bundled `gh`.
`warden audit` re-renders it from artifacts on disk without re-running the
review.

A `pull_request` run from a fork gets a read-only `GITHUB_TOKEN` by default, so
there is no comment to post. `warden review --event` reads the fork off the payload
(`pull_request.head.repo` is not the base repository, or is null because the
fork was deleted) and does not try. It writes the findings to the step summary
and the job log, prints one line saying why no comment was posted, and exits
on the verdict alone: 0 clean, 1 blocking. When the payload does not say where
the head lives, a 403 on the post gets the same treatment. On a payload that
names the base repository, a 403 on a clean review is still exit 2, because a
same-repository token can write and the workflow is missing
`pull-requests: write`; with a blocking finding the exit is 1, as for any
failed post.
`pull_request_target` runs with the base repository's token and posts as usual.

## Exit codes

`0` clean · `1` blocking finding / failed gate · `2` infra or config error —
the gate DID NOT RUN, never conflated with a verdict.

## What this repo adopted from its own catalog

A worked example of `rules recommend` used honestly, on the platform itself.

`.warden/catalog-answers.yaml` is where a repo answers a gap — a verdict
(`not-applicable` or `deferred`), the argument for it, and a tracker id for
anything deferred. The ceiling that gates the count lives in the same file, and
the bare `warden rules recommend` enforces it. This repo's CI runs that command
on every PR into main, so an unanswered gap goes red without a human choosing
to look.

Adjudicated against the 7 catalog entries that ship a **declarative starter** —
the entries whose noise cost can be measured before adoption. That is an
**engine count, not an applicability claim**: entries outside this table are
not implied inapplicable, and most are not adjudicated at all. Read
`rules recommend` for the live list rather than inferring one from here.

| Entry | Verdict | Why |
|---|---|---|
| `unsafe-deserialization` | **adopted** | 0 hits at adoption; warden parses YAML supplied by the repo under review |
| `weak-cryptography` | **adopted** | 0 hits; `rules_version` and attestation digests rest on collision resistance |
| `security-misconfiguration` | **adopted** | 0 hits; Python surface only — see the rule body for what it does not cover |
| `sql-injection` | **declined — not applicable** | no database, no SQL surface. The catalog's own false-positive cost says an SQL rule on a repo with no database is pure noise, and a rule that fires noisily teaches people to ignore the gate |
| `xss-unescaped-output` | **declined — not applicable** | no HTML rendering surface; warden emits JSON artifacts and terminal text |
| `os-command-injection` | **adopted** | 1 hit, kept deliberately: `warden/verify.py` runs repo-declared verify scopes through a shell by design, under a reasoned `warden:allow` marker recording the trust boundary at the line |
| `swallowed-exceptions` | **adopted** (starter) + already enforced (`fail-closed`) | the judgment rule covers every fail-open shape; the starter is the $0 mechanical floor for the one recurring spelling — 4 hits, each adjudicated and kept under a reasoned marker |

The two declines are **applicability** judgments, not disagreements with the
catalog. They flip the moment this repo grows a database or an HTML surface,
and adopting either then is a fresh measurement.

The five adopted rules ship at MEDIUM against `blocking_severities: [HIGH]` —
they report while they earn a precision history. Their checks are pinned by a
test asserting each pattern both **fires** on the violation and stays **silent**
on the safe spelling; the second half is what catches an overbroad pattern.

---

Next: [Writing Rules](Writing-Rules.md) · [Memory](Memory.md) ·
[CLI Reference](CLI-Reference.md)
