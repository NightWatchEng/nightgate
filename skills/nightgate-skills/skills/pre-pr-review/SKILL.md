---
name: pre-pr-review
description: Orchestrated pre-PR code review — the crew graph.yaml declares for the round, dispatched in the order it declares, ending in a warden attestation. Run before opening or marking-ready any PR in a repo enrolled on the Nightgate platform.
---

# Pre-PR review (the declared crew → attest)

The incidents and reasoning behind these steps live in the Nightgate
platform's docs/design/pre-pr-review-rationale.md; this page is the steps.

You are orchestrating the judgment half of this repo's review gate. CI's
deterministic `$WARDEN gate` checks the mechanical HIGH rules; **this skill
owns the `engine: claude` rules** plus general defect-hunting.

Project specifics come from `.warden/skills-policy.md` (the policy file
contract, see the Nightgate platform's docs/wiki/Skills-Policy.md). No policy file →
stop and report; never guess.

## Which warden — resolve it before any other step

Run this from the repo root before anything else; every command below is
written `$WARDEN <subcommand>`.

```sh
if [ -x .warden/bin/warden ]; then echo .warden/bin/warden
elif ! command -v warden >/dev/null 2>&1; then
  echo "REFUSED: no .warden/bin/warden in this repo and none on PATH; install the release repo.yaml's platform.pin names" >&2; false
else
  pin=$(awk '/^platform:/ {p=1; next} p && /^[^[:space:]#]/ {exit} p && /^[[:space:]]+pin:/ {sub(/^[[:space:]]+pin:[[:space:]]*/, ""); sub(/[[:space:]].*$/, ""); gsub(/["'\'']/, ""); sub(/^v/, ""); print; exit}' repo.yaml 2>/dev/null)
  have=$(warden --version 2>/dev/null | awk '{sub(/^v/, "", $2); print $2}')
  if [ -n "$pin" ] && [ "$have" = "$pin" ]; then echo warden
  else echo "REFUSED: PATH has warden ${have:-of no readable version}, repo.yaml's platform.pin is ${pin:-unreadable}; install the pinned release" >&2; false; fi
fi
```

It prints this repo's `.warden/bin/warden` launcher when there is one, else
`warden` on PATH when its `--version` is `repo.yaml`'s `platform.pin` (warden
init enrolls a consumer with no launcher). A refusal is a stop: report it and
never substitute another warden. Put the printed word wherever `$WARDEN`
appears, or start each shell command with `WARDEN=<that word>;` — a variable
does not survive between tool calls. Each probe fence below opens with the
line `: "${WARDEN:?is unset; run the resolution step first}"`, which stops on
an unset `$WARDEN` that would otherwise fall silently into the probe's
fallback arm. Run that line first, too, before any other step that reads a
failed command as an older pin: unset, `$WARDEN rules recommend` runs `rules
recommend`, exits 127 and reads as a subcommand the pin lacks.

## Procedure

### 1. Scope

Run `$WARDEN explain --base origin/main` — note the risk tiers of
changed files and which rules fire. Read the `engine: claude` rule bodies
from the repo's rules dir (they are the review charter, verbatim). Where the
graph resolves a crew, the extra checklists are the `lenses` the crew command
below prints; the policy file's `## Review charter` names them and says where
each one is stated.

If the repo declares an org graph (`graph.yaml`), read it first —
`$WARDEN graph render` — and honor what it says rather than what
this skill assumes: which nodes hold `judge` authority, the
`verification.convergence` rules, and each node's failure policy (retry
budgets, escalation targets). The graph is the org's declared topology; this
skill is its default shape. Where they differ, the graph wins and you say so
in the attestation. If `graph validate` warns that no independent judge is
declared, the round is a loop, not a gauntlet — report it as such.

**A convergence rule that NAMES a node is read against the round's declared
crew, and the crew wins.** Where the round's crew does not name that node,
the rule does not bind that round, and what governs instead is the boundary
of the role the round DOES declare: a re-review role that "re-files every
round-one finding it does not see addressed as confirmed" is that round's
whole adjudication, stated in the graph and quoted into its dispatch by
step 2. Reading the rule the other way — as an instruction to produce the
judge the round lacks — is how a session dispatches a role the graph does not
declare and then cannot file what it raised: the graph names who runs and
nothing else does, in whichever tier this range takes — `$WARDEN
graph crew --round N` for a numbered round, `--light` for a range that earns
the proportionate tier (step 2), and, where the org declares a closure round,
`--round N` past the cap (step 5). `attest write --review-dir` refuses a
roster that is not exactly what that tier's crew command printed, and the
finding has nowhere to go.
Say which convergence rules applied to the round in the attestation, and see
step 4 for the finding an undeclared lens has already raised.

**The round's crew is the graph's to name, not this page's.** Ask it, per
round, and dispatch exactly what it prints:

```
: "${WARDEN:?is unset; run the resolution step first}"
if $WARDEN graph crew --help >/dev/null 2>&1; then
  CREW="$($WARDEN graph crew --round 1)"
else
  CREW=""
fi
```

It prints that round's `roles` in dispatch order, the `lenses` the crew runs
as checklists, the round `cap`, and the node holding merge authority. **Exit 1
means the command has no round to answer with** — the number is past the cap
and the org declares no closure round, or `--light` was asked of an org that
declares none. Where a closure round IS declared, `--round N` past the cap
prints it with `"closure": true` instead of exiting 1, and step 5 is where
that round is run. **Exit 2 means there is no crew to resolve**: a `review`
block that does not resolve, from either invocation, or none at all under
`--round N` — under `--light`, no block is the exit 1 above. Dispatch every
role it names and no role it does not:
`attest write --review-dir` refuses a roster that is not that round's declared
crew, so a roster you compose yourself is a refused write and a role you drop
is a review that did not happen.

**With no crew to resolve** — the repo declares no `review` block, or the
pinned warden predates the subcommand and the probe above took the `else`
branch — run the platform default: one independent finder over the diff, then
one independent judge over its candidates. This is the pack's ONLY fallback:
`deliver` carries no no-crew branch of its own and delegates its dispatch
here, so what this paragraph says is what a repo with no graph.yaml gets.

**The lenses fall back too, and to a different place than the roster.** The
crew command prints `lenses` only where it resolves a crew, so with none there
is nothing printed to read — and what to read instead is a VALUE here rather
than a sentence: one entry per lens shape `graph.yaml` accepts, saying where
the no-crew path reads that shape from.

```json
{"rule": "the rule file itself, which step 1 reads either way",
 "checklist": "the policy file's `## Review charter`, at the place that entry says the checklist is stated"}
```

Resolve every entry and carry what it names into the finder's dispatch
exactly as step 2 requires of a printed lens. What this protects is the narrow
class the command is the only other source of — a lens no rule file enforces,
whose checklist would otherwise reach no reviewer at all.

The role spellings the default roster writes are values, and the payload is
where they belong — these two entries, in this order, with the rest of each entry's
fields as step 5's payload example shows them:

```json
[{"role": "code-reviewer", "round": 1},
 {"role": "cross-examiner", "round": 1}]
```

Nothing records that the roster was this skill's default rather than a
declared crew, and no shard can: the attestation schema is closed
(`additionalProperties: false`) and carries no field for a roster's SOURCE.
What it does carry is the one claim warden can check — run
`$WARDEN attest write` without `--review-dir` and the shard is
stamped `unverified-roster`, a roster nothing cross-checked, which is exactly
what a default roster is.

Then load **split memory** for the changed files — the two roles get
DISJOINT priors on purpose, which is what keeps the judges decorrelated:

```
$WARDEN memory recall --files <changed files> --for reviewer
$WARDEN memory recall --files <changed files> --for examiner
```

Keep the two outputs separate. Never show the examiner's precedents to the
reviewer, or the reviewer's patterns to the examiner. If memory is empty,
both print "no relevant history" and the passes run exactly as before.

### 2. One round directory, then the round's crew (subagents)

**First ask which tier this range takes.** One command, before the crew is
chosen:

```
$WARDEN attest classify --base origin/main
```

`FULL` — the answer for almost every change — means the numbered rounds below,
unchanged. Stop reading this box and continue.

`LIGHT` means warden has PROVEN, from the two git trees, that this range cannot
alter behaviour: every changed file is Python that normalises to the same AST
once docstrings are removed, and nothing is under the gate's own machinery.
That is the whole proof. Every other surface takes the numbered rounds —
Markdown included, this page among it: a document carries commands, links and
raw HTML that no reader short of a full parser tells from its sentences, so
none is trusted to. It prints the verdict file by file, with the reason each
one earned.

A `LIGHT` range takes ONE round with ONE role — `$WARDEN graph crew
--light` names it — instead of the numbered rounds (not on this repository,
where the Small-PR contract below keeps every range on the numbered
rounds). Everything else is
identical: mint the round the same way, dispatch that one role as a subagent in
its own context, triage and disposition its findings the same way, and write
and commit the attestation the same way. `$WARDEN attest check` is
unchanged and still requires a clean attestation covering every commit.

**The light round's brief is not a shorter adversarial review — it is a
different question.** The proof says no executable logic changed, so there is
no logic to attack. What such a diff can still get wrong is a CLAIM: a comment
or a docstring that describes behaviour the code does not have. So the single
role audits exactly that: **is every claim this diff edits true of the tree?**

You cannot choose this tier. `$WARDEN attest write` recomputes the
proof from the trees and refuses the light roster over a range that does not clear it, and
CI recomputes it again over the pushed range. Asking for it is `attest
classify`; earning it is the diff's business.


**Let warden mint the round directory, and put EVERY file this round writes
inside it.** Not just the package — the candidate findings, the
judging pass's verdicts, the per-reviewer outputs and the attestation
payload too:

```
git fetch origin
ROUND="$($WARDEN round new --base origin/main)" || exit 1
PKG="$ROUND/review-package.md"
```

**`origin/main`, not `main` — and the `git fetch` is part of the fix, not
hygiene.** The base is the REMOTE-TRACKING ref, because `git fetch` moves only
that one: a local `main` can sit many commits behind it while every command here
reports success.

**What is machine-enforced, and what is yours.** A warden at or past #194
REFUSES a `--base` that is behind its own upstream (exit 2, and the
`|| exit 1` above stops the round) — that catches the LOCAL-branch spelling,
`--base main`, the one that minted rounds against a stale local main. It cannot catch an
unfetched `origin/main`: a remote-tracking ref has no upstream of its own, so
there is nothing to compare it against and `stale_base` returns "no claim to
read". Nothing in this protocol fetches for you, which is why the fetch is on
the line above and why this paragraph does not claim the refusal covers it.
Substitute your repo's default branch where it is not `main`.

**The `|| exit 1` is not decoration.** `round new` refuses a collision, an
unresolvable range and a rounds root it cannot own; without it the shell keeps
going with `ROUND=""`, and every path below becomes `/review-package.md` —
a round that reads as running while writing to the filesystem root. A refusal
must stop the round, on the primary line and not only in the fall-back.

`$WARDEN round new` derives the directory from the UTC microsecond,
your branch, your head sha and the pid, creates it with `exist_ok=False`,
refuses loudly rather than reusing one that exists, writes the package into it
and mints an empty `$ROUND/reviewers/`. It prints the path and nothing else on
stdout, so the command substitution above is the whole of the ceremony.

**If your pinned warden has no `round new`**, it predates #172.
Probe for the SUBCOMMAND, then run it for real — never skip the isolation:

```
: "${WARDEN:?is unset; run the resolution step first}"
if $WARDEN round --help >/dev/null 2>&1; then
  ROUND="$($WARDEN round new --base origin/main)" || exit 1
else
  ROUND="$(mktemp -d)"; mkdir -p "$ROUND/reviewers"
  $WARDEN diff --base origin/main -o "$ROUND/review-package.md"
fi
```

**Probe with `--help`, never by running the command and catching failure**, and
this is the whole reason the shape above is what it is. `round new` exits 2 for
a collision, for an unresolvable range and for a rounds root it cannot own —
and argparse also exits 2 on `invalid choice: 'round'`. To `||` they are one
value. A `2>/dev/null` around the real call therefore converts the refusal this
protocol exists to produce into a silent `mktemp -d`, and deletes the message
naming why. A genuine refusal must STOP the round.

The fallback is weaker on purpose and you should say so in the attestation:
`mktemp -d` isolates, but nothing binds the reviewers' output to the commit
they judged. On a warden that HAS the binding, `attest write` records
`round_binding: unminted` for such a directory; on the older pin the fallback
exists for, there is no such field and no such line — silence there means the
check does not exist, not that it passed.

The package is the bytes being judged; the candidates and the attestation
payload are the JUDGMENTS, and those are what `$WARDEN attest write` turns
into committed, append-only evidence. The file with the worse failure mode
must not be the one with the weaker rule.

Tell every subagent to keep its own scratch inside `$ROUND` too.

The package is byte-deterministic for a fixed range (resolved shas, commit
log, `--stat`, `-U10` diff), so both passes below judge the SAME bytes —
point each subagent at the file path and instruct it to read full files from
the repo for anything the diff context alone cannot settle.

Launch ONE subagent with independent context (general-purpose) PER ROLE this
round's crew names, in the order the command printed them, and dispatch no
role it did not name. What a role RECEIVES is the payload the graph's edge
into it declares: a role fed `diff` reads the review package (this step), a
role fed `findings` reads the candidate list and never the reasoning that
produced it (step 3). What a role is FOR is its own node's `impl` and
`boundaries` — quote those into its prompt rather than restating them here,
because the graph is where they are declared and reviewed.

Each `diff`-fed dispatch's prompt
must contain, verbatim: every `engine: claude` rule body, every lens the
crew command printed under `lenses` as a checklist — each lens runs inside this
one dispatch, never as a separate one — the review-package path, the path it
must write its raw output to (`$ROUND/reviewers/<role>.json`), the shape of
that file — **one shape for every reviewer this protocol dispatches**: a JSON
object `{"judged": "<the package's head sha>", "round": "<the round id, the
basename of $ROUND>", "role": "<this reviewer's role>", "findings": [...]}`,
never a bare list — and the **reviewer** memory recall under a heading that
states what it is:

> Prior confirmed patterns in this area — verify these, do not assume them,
> and do NOT report them as findings unless you find them in THIS diff.

Instruct it to:
- review the range in the review-package file (its `base:`/`head:` lines
  name the exact shas judged), reading full files from the repo — never
  rate from the diff hunk alone;
- **name the head it judged, the round it ran in, and its own role in its
  raw output** — the top-level `judged` (the package's `head:` sha), `round`
  ($ROUND's basename) and `role` fields;
- hunt real defects beyond the rules too (correctness, edge cases, security);
- return its `findings` as a JSON list: `{rule_id, severity (HIGH/MEDIUM/LOW
  per the rule file; judgment for an unmapped finding), file, line, finding,
  evidence}` — evidence quotes the actual code, and every path in any of
  these fields is repo-relative (`attest write` rewrites one under a worktree
  of this repo and refuses any other home-directory path);
- **`rule_id` must resolve**: either the `id` of a rule in the policy's
  `rules_dir`, or `unmapped:<slug>` (lowercase words joined by `-`, e.g.
  `unmapped:docs-drift`) for a real defect no rule covers — a charter lens
  no rule enforces files as `unmapped:<the lens's slug>`, and a lens whose
  `rule:` pointer names a rule files under THAT rule's id, not the lens's
  (this repo's `consumer-blast-radius` lens files as `blast-radius-named`;
  `unmapped:consumer-blast-radius` is refused). `$WARDEN attest
  write` rejects any other id, a slug that names no defect class
  (`unmapped:general`, `unmapped:misc`), and a slug that duplicates a
  declared rule id (`unmapped:tests-required` — file it as the rule), and a
  slug an **unpaused** declared rule already `covers:` (the error names the
  rule to file it under; a PAUSED rule's class is a live candidate key —
  file under the slug so the re-opened candidate row accrues fresh
  evidence) — name the class the way
  `.warden/memory/tags.yaml` names one. An `unmapped:`
  record with no `judges` field is a wording finding that does not count
  toward a repair round; write `judges: behaviour` in it to make it count;
- return an empty `findings` list if genuinely clean — the file still
  carries `judged`, `round` and `role`, so a clean report is never a bare
  `[]` and never the same bytes as the other reviewer's clean report;
- NAME the changed files it read, by path, in its output (a `reviewed` list
  beside `findings`, or file:line citations) — that is how warden tells a
  clean review from a refusal: a zero-finding output that names no file the
  diff changed is recorded `no-review`, not `reviewed-clean`;
  finding-count quotas are forbidden — and
  so is the opposite thumb on the scale: **never pre-judge findings for a
  reviewer**. If YOUR OWN words in the prompt contain "do not flag X",
  "don't treat X as a defect", "at most MEDIUM", or "the plan already chose
  this" — stop: you are pre-judging, usually to spare yourself a review
  loop. A rule BODY quoted verbatim is not pre-judging: its "Do NOT flag"
  lines are declared scope — policy, reviewed, hashed into
  `rules_version` — and quoting them unedited is exactly what step 2
  requires. The line this rule draws is between scoping the org declared
  and curation you improvised.

Write the reviewer's returned findings to `$ROUND/candidates.json` — the
`findings` list from its raw output — inside the round directory, for the
reason step 2 states. Never the shared
scratchpad root, and never a bare `candidates.json`.

### 3. The judging pass (subagent, separate context)

Launch a SECOND subagent for each role the crew names that the graph feeds
`findings`. **That set is EMPTY in some rounds, and an empty judging pass is a
declaration rather than an omission** — round 2 of this repo's own graph
declares `[scoped-re-reviewer]`, which the graph feeds `diff`, so step 2
dispatches it and this step dispatches nobody. Do not supply a judge the round
does not declare to fill the gap: there is no gap, the re-reviewer's own
boundary is that round's adjudication, and the roster you would write is one
`attest write --review-dir` refuses. Each judging subagent receives ONLY
`$ROUND/candidates.json` and the
review-package path — never the finder's reasoning. It writes its own raw
output to `$ROUND/reviewers/<its role>.json`, in the same shape step 2
names — `{"judged": "<the package's head sha>", "round": "<the round id>",
"role": "<this reviewer's role>", "findings": [...]}` — so it too **names the head it
judged, the round it ran in, and its own role in its raw output**: a judge
handed an empty candidate list would otherwise write the same
`{"judged": H, "findings": []}` bytes as a finder's clean report in
the same round, and on two rounds at one head the same bytes as its own
earlier one — refused as a carry-forward either way. Instruct it to adversarially verify each finding
against the actual code in the repo: try to REFUTE it; check the claimed
file:line; re-derive the failure scenario. Include the **examiner** memory
recall — the `precedents` feed, which the graph types to `judge` authority —
in its prompt under a heading that states its weight:

> Precedents from earlier rounds — non-binding case law, not verdicts. A
> finding matching a past refutation still needs to be judged on THIS code.

Its `findings` carries each candidate tagged
`confirmed | refuted (reason) | scoped-out (reason)`. **Scoped-out is a
third verdict, and the examiner returns it explicitly** — not a defect in
this range, wrong file, or a duplicate of another candidate. It is not a
judgment of the rule that raised the candidate; it says the candidate was
never this rule's to answer for, and step 4 must not have to infer that
after the fact. A finding survives only if confirmed —
severity may not be raised or lowered by either agent (the rule file is
authoritative for rule findings). Survives means it is ACTED on; it does not
mean a refuted candidate leaves the round. Step 4 files it.

### 4. Triage and fix

For each confirmed finding: fix now (with a named regression test per the
repo's DoD), file a tracker issue (backlog), or dismiss with a written
reason. Which findings count toward a repair round, which are resolved in
THIS round, and what round 2 may file are the round contract, stated once in
the Nightgate platform's `.warden/skills-policy.md` under `## Review
charter`; the Small-PR contract below narrows the dispositions on this
repository. Every fix is a commit (`attest write` refuses a dirty tree), and a
round does not survive a commit — so **attest THIS round first** (step 5),
then commit the fix. Mint round 2 and run the scoped re-review in it (step 5,
**Rounds**) only when the repair commit repaired a finding that COUNTS;
"substantial" is not the test, head is. Attesting first is what keeps the
round attestable at all: after the commit its head is gone and no attestation
can ever be written for it.

**After the repair commit lands, record the boundary** — probed, like every
other subcommand this protocol leans on, because the pack ships from the
default branch while each repo pins a warden VERSION and a pin predating #201
has no `progress` at all:

```
: "${WARDEN:?is unset; run the resolution step first}"
if $WARDEN progress --help >/dev/null 2>&1; then
  $WARDEN progress record repair-committed \
    --detail "<what this round fixed>"
else
  : # pinned warden predates the boundary log — say so in your report
fi
```

It is one line and it is the ONE boundary in this loop no command passes for
you. `$WARDEN round new` records `round-minted` and `attest write`
records `round-attested` as they finish, so a repair loop that skips this
leaves a unit looking, from outside, exactly like one that attested a round
and then stopped — and the AGE of the last boundary is the only thing that
tells stalled from working (`progress show`). The boundary
defaults to HEAD, which after the commit is the repair commit; `--head <sha>`
names another. An unknown boundary name is refused with the vocabulary
printed, and recording is advisory in the commands that do it for you — never
in this one, where the record IS the call. Probe the SUBCOMMAND with
`--help`, never by running it and reading the exit code: `argparse` exits 2
on `invalid choice: 'progress'` exactly as `record` exits on a boundary it
refuses, and to `||` those are one value.

**Every candidate the examiner refuted is filed too**, as a finding
with `status: refuted` and the examiner's refutation as its `reason` —
never dropped from the payload.

**A candidate the examiner SCOPED OUT is not filed as a record** — it is
recorded in the round directory and named in the PR body instead.

**A finding raised by a lens the round does not DECLARE is filed as a tracker
issue, named in the PR body, and dropped from nothing.** `attest write
--review-dir` refuses the roster that role would appear in, and refuses a
finding attributed to a lens that was not dispatched in that round — both
correctly, because a shard may not record a review the org did not declare. So
the payload has no slot for it, and leaving it there as "no finding" is the one
thing this protocol's own rule forbids. Do all three: keep the
raw output in the round directory, where it is evidence the dispatch happened;
file a tracker issue carrying the finding whole, with its file, line and
failure scenario; and name the issue AND the undeclared lens in the PR body,
saying which round dispatched it and why it is not in that round's payload.
This is the scoped-out treatment applied for a different reason and it must
not be confused with one: a scoped-out candidate was JUDGED not to be this
rule's question, while this one may be perfectly real and is out of the payload
only because no declared dispatch can carry it. If a LATER round declares that
lens, raise it there, by that lens, in that round's payload, and it reaches the
corpus normally. What is never acceptable is a confirmed defect that exists
only in a session transcript.

**Tag every finding** with a short taxonomy before attesting — tags are a
recall key, so they decide what future reviews can find. Reuse a tag from the declared
vocabulary (`.warden/memory/tags.yaml`, echoed by `$WARDEN memory stats`)
whenever one fits; coin a new one only for a genuinely new class, and say so
in the attestation so the retro can adopt or merge it. Where that file declares
a `ceiling:` with no room left (at `max_undecided: 0`, always), a coined tag
needs a one-line receipt under its `left_undeclared:` key in the head the
round judged, and that line must be a reason (eight or more words of three
letters or more, six of them distinct): a token such as "n=1, watching" makes
the whole block unreadable, which refuses every tagged payload. A receipt committed after the mint moves head, so it costs a
fresh round (step 5). A second name for an
existing class splits recall between them. Two to four words, kebab-case, describing the
DEFECT CLASS, not the file: `fail-open`, `enforcement-claim`,
`docs-drift`, `test-cannot-fail`.

### 5. Attest

Write the final state to `$ROUND/attestation-payload.json` — inside the round
directory, never the shared scratchpad root, for the reason step 2 states.
This file is what becomes committed, append-only evidence; it is the last
place a stale or foreign file may be picked up.

```json
{
  "reviewers": [
    {"role": "code-reviewer", "agent": "<subagent model/desc>", "round": 1,
     "returned": true, "findings": 6, "output": "code-reviewer.json"},
    {"role": "cross-examiner", "agent": "<subagent model/desc>", "round": 1,
     "returned": true, "findings": 6, "output": "cross-examiner.json"}
  ],
  "findings": [ { "...finding...", "lens": "code-reviewer", "round": 1, "status": "fixed | confirmed | dismissed-with-reason | refuted", "reason": "..." } ],
  "verdict": "clean | findings-open"
}
```

The `findings` list is what the round RAISED, not what survived it: one
entry per candidate the examiner JUDGED — confirmed and refuted alike, never
survivors only — each carrying its disposition. A candidate the examiner
scoped out was not judged and has no entry; it is recorded in the round
directory and the PR body. (`reason` is
required on `refuted` and `dismissed-with-reason` — the schema refuses one
without it.)

**Every finding names the lens that raised it and the round it arrived in**.
`lens` and `round` on a finding, `round` on every roster
entry, and `role` spelled from ONE vocabulary — the schema's `$defs.lens`:
`code-reviewer`, `cross-examiner`, `scoped-re-reviewer`, `builder`,
`closure-attestor` (the closure round's role, below), `claims-auditor` (the
light round's role: the single lens dispatched for a range that
`$WARDEN attest classify` has PROVEN cannot alter behaviour — every
changed file Python and identical once docstrings are stripped, and nothing
under the gate's own machinery. It audits the one thing such a diff can still get wrong, which is
whether the claims it edits are true of the tree. Eligibility is recomputed
from the diff by warden, never asserted by the builder, and every other range
takes the full rounds below), or
`crew:<slug>` for a lens the graph declares under `review.lenses`, which is
what the crew command prints
(`crew:fail-closed`, `crew:consumer-blast-radius` — lowercase words joined by
`-`, never a bare `fail-closed`, never `crew-fail-closed`). Spell `role` from the
vocabulary on EVERY pin, enforced or not: the corpus is one corpus.

Rounds: **one by default.** Round 1 is the crew `$WARDEN graph
crew --round 1` prints, dispatched in that order, carrying that command's
`lenses` as checklists. Round 2 runs only when a round-1 finding that counts
was repaired, and its crew is whatever `--round 2` prints — a SMALLER crew
than round 1 wherever the graph declares one, and the command's answer is
what decides, never this page's memory of it. A roster that adds a role
round 2 does not declare is an extra role, and `attest write --review-dir`
refuses it. Round 2's payload re-files every round-1 finding it does not
verdict addressed as `confirmed`, because `$WARDEN round classify`
reads only the payload it is given. What else round 2 is handed and may
file, and what happens at the cap, are the round contract (step 4 names
where it is stated). **Mint every one of them, round 1
included** (step 2), and that includes the round the scoped re-reviewer runs
in — the directory is what the chain is counted from. `$WARDEN round classify` reads the repair chain
from the manifests `round new` writes, so a round nobody minted is invisible
to it: its repair commit lands inside what classify then measures as the
ORIGINAL diff, and a file that repair wrote is read as the change's. A warden at or past #191 refuses a chain
carrying fewer round directories than the payload's findings declare rounds.

**If you DID commit a repair at the cap, the closure round is the way out —
and the only one.** A round does not survive a commit, so the round the
repair answers was attested and sealed before it, with its findings still
open; the cap refuses another review round; and a sealed round is never
re-pointed at the new head. The branch then has no attestable head at all and
its gate is red forever, which is why the round contract has the round at the cap make no repair
commit. Where the org declares `review.closure` (check with `$WARDEN graph
crew --round <any number past the cap>`, which prints `"closure": true` and
the single role instead of exiting 1), mint one more round at the repaired
head and dispatch that role alone. Its NUMBER does not matter, only that it
is past the cap: if you already hit the refusal above, `round new` left a
stray empty round there and yours is cap+2. It is non-budgeted — it spends no
repair budget, and no REVIEW round follows it. Nothing counts closure rounds:
if a later commit moves head again, close again. What makes the round
terminal is the payload rule below, not a limit on how many times you may
re-state the same findings.

Its payload may ONLY re-file what the rounds at or under the cap already
raised, and this is enforced, not asked. `attest write --review-dir` refuses
the write if any record does not repeat a CLAIM a capped round committed to
this branch — same `rule_id`, `file`, `line`, `severity` and `finding` text,
with only `evidence`, `status`, `reason`, `lens` and `round` moving — if any
round within the cap attested nothing here, if a record carries a status
other than `fixed` or `dismissed-with-reason`, or if the payload is silent on
a claim still `confirmed` at the cap. So: re-file each still-open finding
VERBATIM, now `fixed` (the repair closed it) or `dismissed-with-reason`
naming a tracker issue, with the evidence rewritten to show what became of
it, and file nothing else. **Do not use it to review the repair.** If the
closure round makes you want to raise something new, that finding belongs to
a tracker issue — warden refuses the shard rather than let you file it, and a
closure round that could raise is extra review budget the cap denies. You may
not `refuted` your way out either: that is a judgement, and no judge follows
this round. The shard records `closure_verification: verified` so the corpus
can tell a closure round from an ordinary one.

**One attestation covers ONE round** — a recorded ruling, and
`$WARDEN decide list` carries its shard. `--review-dir` names THAT round's
`reviewers/`; the roster declares only the dispatches that ran in that round;
every finding it carries names that round; and no earlier round's report is
copied, moved or renamed into it. A finding's `round` is the round
whose dispatch put it in this payload: for a finding raised here, it is
the round it was FIRST raised in; for one RE-RAISED while still open (below),
the round that re-confirmed it, which is the only round `attest write` will
join it to. One roster
entry is still one dispatch: a lens re-dispatched inside one round, because
it did not return the first time, is two entries with that round and two
outputs. Earlier rounds are not
dropped by being left out — each is evidence in its OWN committed shard, and
the PR body's evidence chain names them in order. **A finding still OPEN when a round ends is
RE-RAISED in the next round's payload** — by the lens that re-confirms it
there, with that round's number, which is what `attest write` joins and what
keeps `$WARDEN round classify` able to see it. Leave it out and classify reads
that round clean over a defect the review never closed, and the cap's revert
trigger goes blind to everything older than the current round. A finding
CLOSED in its round (`fixed`, `refuted`, `dismissed-with-reason`) drops out;
a finding the next round's re-review verdicts as addressed is filed THERE as
`fixed`, so the fix reaches the corpus in the shard of the round that
verified it rather than the shard of the round that raised it. Re-file it VERBATIM
— same `rule_id`, `file`, `line` and `finding` text, with only `lens`,
`round`, `status` and, where the new status requires one, `reason` moving
(the schema requires a `reason` on `refuted` and `dismissed-with-reason`, and
`reason` is not part of what the fold groups on, so carrying it costs
nothing).

**ENFORCED from round 2 on** (#278), not only asked for. `attest
write` refuses a payload whose record carries `fixed`, `dismissed-with-reason`
or `refuted` for a claim no EARLIER round committed to this branch — same
identity the closure round is held to: `rule_id`, `file`, `line`, `severity`
and `finding` text. A `confirmed` record is exempt, because a round within the
cap raises what it finds and warden cannot tell a new finding from a
re-confirmed one. Two ways to meet it, and the refusal names both: the claim
was restated (file the earlier round's text, never this round's verdict
prose — `"[F0] Addressed, both halves"` is a different claim), or the earlier
round's shard is not committed on this branch's first-parent line yet.

**So attest each round BEFORE the next commit.** `attest write` stamps the
CURRENT head, so a round left unattested when the repair commit lands can
never be attested at all. A round with findings still open attests with `verdict:
findings-open` and exits 1; that exit is the honest verdict, not a failure to
write, and the shard is written. Its findings are `confirmed` there, because
the fix commit has not happened yet — so `$WARDEN round classify`, reading
that payload, sees them OPEN. Whether that makes the round COUNT is
classify's to say and never yours, by the round contract's counting
definition. What the timing settles is only that the findings are open when
classify reads them, and that is the honest arithmetic: a payload marking a
finding `fixed` before the commit that fixes it would attest to a fix that
does not exist. The `fixed` marker lands one round later, written by the
re-review that verified it. `memory ingest` it, `git add` it, and commit
it — with the repair commit or before it. The branch then carries one shard
per round, the last of them CLEAN, and `attest check` passes. It asks three
things, and all three are the gate's requirements, not only this protocol's:
that a committed shard names a commit in this PR's range — not that exactly
one does; that the TIP shard of each line of history in the range carries
`verdict: clean` (#192); and, on a warden carrying #278, that
no commit the tips do not cover ships anything but a round's own evidence.

That third one is why the **order** of the last two steps matters. A commit
landing after the shard is UNCOVERED — no tip attestation is its ancestor —
and it is refused unless every path it touches sits under
`.warden/memory/attest/`. So the shard commit may carry the shard and nothing
else, and the repair commit goes BEFORE the round that verifies it, never
after. Commit a doc fix, a test fixture, a decision shard or a merge of
`origin/main` after the last round and the gate goes red naming that commit;
the remedy it prints is the only one there is — re-review this head, ingest,
commit the shard. Budget a round for it rather than discovering it in CI.

For the last round to BE clean, every finding still open at the loop's end
has to be parked the way `deliver` says and then FILED that way:
`dismissed-with-reason` naming the tracked item that carries it, or on
this repository the reason, where the Small-PR contract files none. Left
`confirmed`, it makes `clean` impossible — `attest write` refuses a `clean`
verdict with a confirmed finding, and the re-raise rule above carries it into
every later payload — so the loop could not be closed at all. Parking is
what closes a finding; the status is how the corpus learns it happened.

The membership pass is no longer a residual on this point, on a warden that
carries #192: `attest check` reads the TIP attestation's `verdict` —
a matched shard no other matched shard descends from, so a merged range has
one per line of history and all of them bind — and exits 1 unless it is
`clean`. A branch ABANDONED after an early round no longer satisfies the
required gate on that round's `findings-open` shard. Only the tips, never
every shard: an earlier round's open verdict is what per-round attestation is
supposed to leave behind. On a pin PREDATING #192 the check is
membership-only and this protocol's own rule is the only thing closing it, so
check what your warden does before reporting the gate as the enforcement.
Whether every commit was reviewed is no longer left to membership either, on
a warden carrying #278: a commit no tip attestation covers is
REFUSED unless every path it touches sits under `.warden/memory/attest/`, so
a commit landing after the last clean shard no longer passes on it. Budget a
round for the re-review. On a pin predating #278 that commit still
passes, and re-running after a fix is this protocol's rule alone — so check
what your warden does here too before reporting the gate as the enforcement.

`attest write --review-dir` joins each finding's (`lens`, `round`) to a roster
entry with that `role` and `round` that `returned: true`, and REFUSES a finding
attributed to a lens nobody was dispatched as, to a reviewer that did not
return, or to a round that lens did not run in — a finding cannot have been
raised by a review that did not happen. It does not compare the roster's raw
`findings` count to the attributed findings: one raw finding naming two files
is honestly two records. A REFUTED finding is attributed exactly like an
upheld one — it was raised by a lens in a round, and that is the whole point
of filing it.

**One entry per reviewer, and the roster must be TRUE**. Each
entry says whether that reviewer `returned`, how many raw `findings` it came
back with, and the basename of its raw `output` inside `$ROUND/reviewers/`.
Do not jam that into the `agent` string. A reviewer you dispatched that did
NOT come back is declared `"returned": false`, never dropped: a silently
shorter roster is the same lie in the other direction.

**A reviewer that returned no review is not a clean review**.
A file holding a refusal, a classifier decline, a truncated run, or a
"nothing to review" written without reading the diff is a distinct,
non-empty output — it satisfies the roster check exactly as a clean report
does. Warden now DERIVES each
entry's `outcome` from the artifacts under `--review-dir` and stamps it —
`reviewed-findings` when findings were counted, `reviewed-clean` only when a
zero-finding output names at least one changed file, `no-review` otherwise or
when the reviewer did not return. You do not write it; you READ it back off
`attest show`, and you may only ever LOWER it: declare `"outcome":
"no-review"` for a run you know was truncated, never `reviewed-clean` — a
declared reviewed-clean the output cannot corroborate is refused. **When a
lens comes back `no-review`, re-dispatch it ONCE** in the same round — a
second roster entry, same `round`, its own `output` — and record the
`no-review` either way: the first entry stays in the roster with its outcome,
because a roster that dropped it would be the silently shorter roster #168
ended. Two `no-review`s from one lens in one round is a fact
about the round the retro needs, not a reason for a third.

**Check what your pinned warden supports before you write the payload.** This
pack ships from the default branch while each repo pins a warden VERSION, so
a consumer can be running a warden older than this protocol. On such a warden
the structured roster is refused outright — `--review-dir` is an unrecognized
argument, and `returned`/`findings`/`output` fail the older schema as
"additional properties" — which would mean no attestation, and a red
`attest check` on every PR:

```
: "${WARDEN:?is unset; run the resolution step first}"
if $WARDEN attest write --help 2>&1 | grep -q -- '--review-dir'; then
  ROSTER=structured
  if $WARDEN attest write --help 2>&1 | grep -q 'lens'; then
    ATTRIBUTION=lens   # the schema knows lens/round (#184)
  else
    ATTRIBUTION=none   # pinned warden predates #184
  fi
  if $WARDEN attest write --help 2>&1 | grep -q 'outcome'; then
    OUTCOME=derived    # warden stamps reviewers[].outcome
  else
    OUTCOME=none       # pinned warden predates the outcome field
  fi
else
  ROSTER=legacy      # pinned warden predates #168 —
  ATTRIBUTION=none   # and so predates #184 as well
  OUTCOME=none       # and the outcome field
fi
```

`structured`: write the roster above and run

```
$WARDEN attest write --findings "$ROUND/attestation-payload.json" \
  --base origin/main --review-dir "$ROUND/reviewers"
```

Where `.warden/memory/tags.yaml` declares a `ceiling:`, a warden carrying the
tag check refuses (exit 2, nothing written) a payload whose tags the file
neither declares, folds under `aliases:`, nor receipts under `left_undeclared:`
once they would pass that ceiling, and a declared rule id used as a tag; the
message lists the declared tags. Re-tag with one that genuinely fits, or add
the receipt (step 4) and re-run in a fresh round.

`legacy`: emit reviewer entries with `role` and `agent` ONLY, no `lens` or
`round` on any finding either (that schema is closed and refuses both as
additional properties), omit `--review-dir`, and say in your report that the
roster went unchecked because the repo's pinned platform predates the check —
that skew is a finding worth raising, not a detail to swallow. This is the LIVE
cell for a consumer pinned to a tagged release: every tag through v1.1.0
predates #168, and the pack ships from the default branch ahead of it.

`ATTRIBUTION=none`: OMIT `lens` and `round` from every finding and every
roster entry — that schema is closed and refuses them as additional
properties, which would mean no attestation — keep `role` spelled from the
vocabulary anyway, and say in your report that the round went unattributed
because the pinned platform predates the field. That skew is a finding worth
raising, not a detail to swallow.

`OUTCOME=none`: never write `outcome` on a roster entry — that schema is
closed and refuses it as an additional property, which would mean no
attestation — and say in your report that the round's dispatch outcomes went
unrecorded because the pinned platform predates the field. The re-dispatch
rule above still binds: a returned output that names no changed file is a
no-review whether or not warden can stamp it.

`--review-dir` is what makes the roster more than an assertion, and it checks
BOTH directions. Warden REFUSES the write if any reviewer claiming
`returned: true` has no distinct, non-empty file there — and equally if the
directory holds a report no roster entry claims, which is the dropped
reviewer. So `$ROUND/reviewers/` holds reviewer reports and NOTHING else:
everything in it is read as a claim about this round, and your own scratch
belongs one level up in `$ROUND`.

Omit the flag and the attestation is stamped `unverified-roster`,
permanently, in the committed shard — so pass it. Pass it with a real path:
an empty value is refused, not downgraded.

**A round does not survive a commit.** `attest write` binds the round to the
head `round new` minted it for, and ANY commit between the mint and the write
— the fix for a finding, a test-fixture fix, a comment, a merge of
`origin/main` — moves head, so the write is refused. Once the shard is
committed, a later commit is NOT unrefused: `attest check`
refuses one no tip attestation covers that touches anything outside
`.warden/memory/attest/` (#278), so re-running after a fix is a
check as well as this protocol's rule. On a pin predating it, this protocol
is the only thing there. When it fires, mint a fresh round with `$WARDEN
round new` and **re-run every reviewer in it**. **Never copy a `reviewers/`
file forward from an earlier round**: `attest write` compares each claimed
report's bytes against every sibling round still under the rounds root and
REFUSES a byte-identical one. The
three fields every reviewer writes — the head it `judged`, the `round` it ran
in and its `role` — are what keep an honest report from ever reproducing
another one's bytes, in both comparisons the check makes; warden does
not read them, so a reviewer that omits them and writes a canonical clean
`[]` twice is refused too.

Know what it proves and do not overstate it in any report: the claim was
backed by an artifact that existed at attest time. It cannot tell that a
reviewer really ran.

warden also stamps head/base SHAs + rules_version; `clean` with
confirmed-open findings is rejected. Exit 0 = CLEAN — you may open/ready the
PR. Report the attestation summary.

Then **feed the corpus** so the next review starts smarter:

```
$WARDEN memory ingest
```

Ingest redacts secrets at the shard boundary, is idempotent, and WRITES the
event shards under `.warden/memory/attest/` — `git add` them and include them
in the PR. A review that judges but never ingests is a review the organization
forgets, and the attestation left in `.warden/out/` is gitignored: it never
reaches the corpus. `$WARDEN attest check` is the CI step that says so out
loud — it passes only when a committed shard names a commit on this branch.

## Boundaries

- This skill never merges PRs and never pushes to main.
- Do not weaken a rule to get to CLEAN — rule edits are HIGH-tier changes
  that go through their own review.
- If the diff is trivial (docs typo), steps 2–3 may be one combined
  subagent, but the attestation is still written.
- Memory is context, never authority: a recalled pattern is a place to
  look, and a recalled precedent is an argument to weigh. Neither one
  decides a finding — only the code in front of you does.
- `$ROUND` came from `$WARDEN round new`, not from a name you chose, and no
  file a round writes lives outside it — not the package, not the candidates,
  not the reviewer outputs, not the attestation payload. A file whose contents
  reach an attestation gets the strongest path rule this protocol has, not the
  weakest.
- Before attesting, confirm every reviewer report names only files inside your
  own `base...head`. On a warden that has the binding, `attest write` checks
  the round's head sha for you and prints `round_binding: unminted` when it
  could not — read that line rather than assuming it said nothing because all
  was well. On an older pin neither the line nor the check exists, so the
  by-hand confirmation is the only thing standing there.
- Never name a reviewer in the roster that you did not dispatch, and never
  drop one you did. The roster is a factual claim about this round, and
  `attest write --review-dir` is the check that it is one.
- **Lint the PR title before the PR exists.** This skill ends at the
  attestation and the PR opens next, so the title is the last thing checked
  here: `./scripts/commit-lint.sh --header-only <file-holding-the-title>`, the
  SAME validator CI runs. A squash merge lands the title verbatim as the
  commit header, so CI's "commit messages" check validates it, and the budget
  is **100 characters** for what you write — the forge appends ` (#N)` and the
  validator strips that first. Over budget → shorten the title; never widen
  the budget. `$WARDEN ship` runs this step for you before it opens anything
  (that is the only path on which it is already covered); on the hand-run
  tail, or any other route to `gh pr create`, it is yours.

## Small-PR contract

These four rules are the Nightgate platform repository's protocol for its
own PRs — "this repository", here and in every clause of this file that
cites this section, is the repo that ships this pack, never a consumer —
and they bind the PRs that repo opens against itself, not a consumer's.

1. **One tracked item per PR, at most 400 changed lines.** The count is the
   diff against the base branch, excluding files under `.warden/memory/`.
   There are no batch PRs. A change that would exceed the limit is split
   into ordered items before the first line is written, and each item ships
   as its own PR.
2. **A review finding becomes a tracked item only when it is a
   `judges: behaviour` finding on runtime code** — `warden/`, `cage/`,
   `scripts/` or `.github/`. A wording finding is fixed in the same PR when
   the fix is a line or two and a repair commit is being made anyway — it
   rides that commit; otherwise it is recorded `dismissed-with-reason`
   in the attestation payload, with the reason stated there. A wording
   finding is never filed. A behaviour finding outside that runtime set is
   fixed in the PR, and one still open at the repair cap is recorded
   `dismissed-with-reason` in the payload, never filed.
3. **After the final attestation nothing is committed on the branch**: no
   merge of main, no fixture fix, no retitle commit. A change the branch
   still needs gets a new round at the new head, whose attestation is then
   the final one, or a new PR.
4. **The light (`claims-auditor`) round is not used on this repository.**
   The closure round cannot be forbidden — `attest write` refuses
   `dismissed-with-reason` for a claim first raised in round 2, so a new
   round-2 record forces it — so round 2 is scoped to the round-1 repair
   and files a NEW record only for a behaviour finding on runtime code;
   anything else it raises is kept in the round directory and named in the
   PR body, as a scoped-out candidate is, and the closure round is reached
   only for a real defect. An unmapped record does not count when its
   `judges` field is absent or says `wording`, and counts on any other
   value; a declared rule's finding is what the rule's frontmatter says.

Where a sentence elsewhere in this file offers a wider path, on this
repository the rule above is the one that applies.

## Provenance

Earned steps and the incidents behind them. `as_of` is the
last date this protocol was checked against the system it describes.

```yaml
as_of: 2026-09-27
assumes:
  subagents: true
steps:
  - step: "a convergence rule naming a node is read against the round's declared crew — where the round does not name that node the rule does not bind it, and the declared role's own boundary is that round's adjudication; a finding an undeclared lens already raised is filed as a tracker issue and named in the PR body, never dropped"
    evidence: commit:658d782b
  - step: "review runs one round by default — the charter lenses as checklists inside the diff-fed dispatch, then the judging pass — and a second, scoped round only when a round-one finding that counts was repaired, handed every round-one finding still open, repaired or not"
    evidence: shard:20260914T2018550000-c740d097-2d471865
  - step: "the round's crew is read from `warden graph crew --round N` and dispatched exactly — those roles, that order, no roster stated on this page — because attest write --review-dir refuses a roster that is not that round's declared crew"
    evidence: shard:20260915T235428Z-bbf6efce-2850b2b7
  - step: "the membership pass is no longer the whole gate: on a warden carrying it, `attest check` requires the TIP attestation of each line of history in the range to carry `verdict: clean`, so an abandoned round-1 branch stops passing"
    evidence: commit:ec7f19cc
  - step: "one attestation covers ONE round: its roster names only that round's dispatches, no earlier round's report is copied in, and every round is attested before the next commit"
    evidence: commit:410cd48e
  - step: "a finding still open when a round ends is re-raised VERBATIM in the next round's payload under the re-confirming lens and round, so the cap's revert trigger can still see it and the corpus folds the copies"
    evidence: commit:410cd48e
  - step: "every round is minted, round 1 included — an unminted round is invisible to `$WARDEN round classify`, which reads the chain from the manifests"
    evidence: commit:410cd48e
  - step: "every finding names the lens that raised it and the round it arrived in, from the schema's vocabulary; --review-dir joins each to a returned dispatch"
    evidence: commit:fc55eb71
  - step: "rule_id must resolve — never the unresolvable 'general' escape id"
    evidence: commit:fdf71fdd
  - step: "never pre-judge a finding for the reviewer (the thumb-on-the-scale that curates the corpus)"
    evidence: commit:f5b90a72
  - step: "enforcement-truth lens: flag a comment/doc/message claiming a check that does not exist"
    evidence: tag:enforcement-claim
  - step: "exclude rule bodies from the enforcement lens — a rule body quotes the claims it hunts"
    evidence: tag:self-reference
  - step: "warden MINTS the round directory (`$WARDEN round new`) and holds every file a round writes — package, candidates, reviewer outputs, attestation payload; a collision is refused, never reused"
    evidence: commit:1fdda8a0
  - step: "the roster is one entry per reviewer, structured, and checked against its outputs by --review-dir"
    evidence: commit:0cc2a84e
  - step: "every reviewer report names the head it judged, the round it ran in and its own role, and a report byte-identical to another's is refused at attest — never copy reviewers/ forward"
    evidence: commit:c04733b4
  - step: "a round does not survive a commit before attest write; the re-mint cost was measured (4 re-attestations in 132 shards, 1 forced by a merge of main alone) and accepted rather than the refusal narrowed"
    evidence: commit:c04733b4
  - step: "every candidate the examiner refuted is filed with status refuted and its reason — the payload is what the round RAISED, so precision is measured over raised, not over survivors"
    evidence: commit:280c5084
  - step: "each repair commit records `$WARDEN progress record repair-committed`, behind the same `--help` subcommand probe every other command here gets — round-minted and round-attested are recorded by the commands that pass them, and this is the only boundary in the loop that is not"
    evidence: commit:1a4959a1
  - step: "the small-PR contract binds this repository's own PRs: one item per PR at most 400 changed lines, a tracked item only for a behaviour finding on runtime code, nothing committed after the final attestation, the light round unused and round 2 filing a new record only for a behaviour finding on runtime code"
    evidence: shard:20260927T0434390000-9189ab03-ca6028b6
```
