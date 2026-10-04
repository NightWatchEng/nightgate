# Review depth, measured — 2026-09-10

The reopening of the review-depth ruling (#185). The 2026-09-02 ruling in
[`.warden/skills-policy.md`](../../.warden/skills-policy.md) `## Review charter`
held review depth FLAT and wrote its own reopen condition into the paragraph:
per-lens and per-round attribution shipping (#184), roughly 20 PRs
of that data accruing, and refutation filing and restatement folding (#186)
fixed first. All
three are met, with one qualification stated here rather than in a footnote:
the PR count is **16**, not the "roughly 20" the ruling named, and §8 says why
16 is enough for two lenses and not enough for the other three.

**Verdict: flat stands.** Tiering by diff class, by size, or by declared risk
tier would each have skipped ZERO of the 16 PRs in the window. No lens has a
retirement case. The one thing the corpus does show is that depth pays through
round 2 and falls off from round 3 — which the existing `warden round classify`
cap already governs.

## 0 · What this measures, and what it cannot

The corpus is `.warden/memory/attest/` at `c46097f` — 176 shards, 1466 folded
findings, of which **327 carry a lens** and a round. Attribution starts
2026-09-02; nothing before it is retrofitted, so every number here is over the
16 PRs whose attestations carry it (#182 and #184 through #198).

Three limits, stated before the tables rather than after them:

- **Precision only.** A lens that prevented a defect from ever being written
  leaves no record. Nothing below can price cheap insurance, and the strongest
  argument for a lens with a thin record is one this corpus cannot make.
- **A lens that catches nothing writes nothing.** Absence of findings is
  invisible in the record store. It is visible only in the roster's dispatch
  denominator, which is why Table A leads with `dispatches` and `zero-yield`
  rather than with the finding count.
- **The window contains no docs-only PR and no PR under 200 lines.** The two
  classes the bead and the prior ruling each pointed at are unrepresented, so
  the corpus cannot say anything about them except that they did not occur.

## 1 · The bar applied

The repo promotes a rule to a blocking severity on **10+ judged cases AND a 0.7
Wilson lower bound**, plus a person agreeing. Reducing review is the same kind
of act in the opposite direction — it removes enforcement — so the bar here is
at least as serious, and asymmetric where the asymmetry is real:

| to do this | requires |
|---|---|
| retire a lens outright | 10+ dispatches on the class, a majority of them **zero-yield**, and 0 upheld HIGHs on record |
| narrow a lens to some diff classes | 10+ dispatches on the excluded class, all zero-yield |
| add a lens to the standing charter | the promotion bar as written: 10+ judged findings and a 0.7 Wilson lower bound |
| keep a lens | nothing — a lens stays until something is measured against it |

"Keep" needs no evidence on purpose. A lens is not a rule: its false positives
cost a paragraph, and its false negatives cost a shipped defect. That asymmetry
is why the burden sits on removal.

## 2 · Per lens — what each one actually catches

`dispatches` counts roster entries (a lens sent at a round on a PR).
`zero-yield` counts dispatches that came back with nothing. `Wilson LB` is the
95% lower bound on the upheld rate, the same statistic the promotion gate uses.

| lens | dispatches | zero-yield | findings | upheld | refuted | dismissed | upheld % | Wilson LB | upheld HIGH | PRs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `scoped-re-reviewer` | 31 | 2 | 115 | 84 | 2 | 29 | 73% | 0.64 | 3 | 12 |
| `code-reviewer` | 23 | 2 | 92 | 78 | 5 | 9 | 85% | 0.76 | 3 | 11 |
| `crew:consumer-blast-radius` | 16 | 0 | 47 | 40 | 6 | 1 | 85% | 0.72 | 3 | 10 |
| `crew:enforcement-truth` | 4 | 0 | 22 | 17 | 4 | 1 | 77% | 0.57 | 1 | 4 |
| `crew:fail-closed` | 5 | 0 | 16 | 16 | 0 | 0 | 100% | 0.81 | 0 | 4 |
| `crew:evidence-attribution` | 1 | 0 | 11 | 9 | 0 | 2 | 82% | 0.52 | 1 | 1 |
| `crew:evidence-intact` | 2 | 0 | 11 | 11 | 0 | 0 | 100% | 0.74 | 0 | 1 |
| `cross-examiner` | 26 | 4 | 8 | 1 | 0 | 7 | 12% | 0.02 | 0 | 3 |
| `crew:tests-bite` | 1 | 0 | 5 | 5 | 0 | 0 | 100% | 0.57 | 0 | 1 |

**No `crew:` lens has ever returned empty.** 29 crew-lens dispatches, 0
zero-yield. The eight zero-yield dispatches in the window all belong to the
three general roles, and four of those are the cross-examiner, whose job is to
refute rather than to raise.

The cross-examiner row is not a bad row read correctly: it files a record when
it **overturns** a candidate, so its 12% upheld rate is 7 dismissals and 1
survival — the arbiter working, not a lens misfiring. Reading it as precision
would conflate survival with precision in a new place.

Per lens, against the bar in §1:

- `crew:consumer-blast-radius` — 16 dispatches, 47 findings, 85% upheld, 3 of
  the window's 11 upheld HIGHs. It is the only charter lens with no rule behind
  it, and it has the strongest record in the crew. Nothing here supports
  narrowing it.
- `crew:fail-closed` — 16 findings, 16 upheld, 0 refuted, Wilson LB 0.81 —
  the highest Wilson floor of any lens in the table. Its upheld-per-dispatch is
  3.2, which is SIXTH of nine, so the case for it is the clean record and not
  the volume. 5 dispatches is under the bar for any conclusion about removal,
  and every one of them found something.
- `crew:enforcement-truth` — 22 findings over 4 dispatches, 1 upheld HIGH, and
  4 refutations, its weakest number and the second-most of any crew lens
  (`crew:consumer-blast-radius` has 6, over exactly four times the dispatches: 16 against 4). The
  prior ruling retained it in every round; nothing measured moves that, and 4
  dispatches could not move it either.
- `crew:evidence-attribution`, `crew:tests-bite`, `crew:evidence-intact` — one
  PR each. All three are **undeclared**: the charter's lens list does not name
  them, and they were dispatched by individual sessions. Their records are good
  (9/11, 5/5, 11/11 upheld) and only `crew:evidence-intact` clears the 10-judged
  / 0.7-floor bar. One PR is the thin-sample trap this bead's own text names, so
  none is promoted here; the question is filed rather than decided.

### Findings by lens and file class

Not a licence to narrow — the denominator is dispatches, not classes, and no
lens was ever dispatched on a docs-only diff. It is here because the bead asked
where each lens fires.

| lens | code | tests | docs | skills | evidence | gate | schema | other |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `scoped-re-reviewer` | 35 | 37 | 27 | 13 | 1 | 1 | 1 | 0 |
| `code-reviewer` | 22 | 34 | 18 | 5 | 9 | 3 | 1 | 0 |
| `crew:consumer-blast-radius` | 12 | 12 | 11 | 9 | 1 | 0 | 2 | 0 |
| `crew:enforcement-truth` | 5 | 8 | 2 | 2 | 1 | 2 | 2 | 0 |
| `crew:fail-closed` | 10 | 4 | 0 | 1 | 0 | 0 | 1 | 0 |
| `crew:evidence-attribution` | 0 | 3 | 0 | 0 | 5 | 1 | 0 | 2 |
| `crew:evidence-intact` | 7 | 0 | 4 | 0 | 0 | 0 | 0 | 0 |
| `cross-examiner` | 0 | 4 | 1 | 3 | 0 | 0 | 0 | 0 |
| `crew:tests-bite` | 2 | 3 | 0 | 0 | 0 | 0 | 0 | 0 |

`crew:fail-closed` has never raised a finding on a docs file. That is the one
row that looks like an argument for narrowing a lens, and it fails the bar in
§1: 5 dispatches, none of them on a docs-only diff, so what the row shows is
that the lens has not been given the chance to be idle rather than that it is.

## 3 · Does depth pay?

Findings-per-round is not value-per-round, so the table weights upheld findings
by severity (HIGH=9, MEDIUM=3, LOW=1) and divides by dispatches — what a round
returned, per reviewer sent.

| round | dispatches | findings | upheld | refuted | dismissed | upheld % | upheld HIGH | weighted value | value / dispatch |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 42 | 141 | 125 | 12 | 4 | 89% | 7 | 341 | 8.1 |
| 2 | 23 | 67 | 66 | 0 | 1 | 99% | 3 | 188 | 8.2 |
| 3 | 20 | 66 | 42 | 1 | 23 | 64% | 0 | 104 | 5.2 |
| 4 | 14 | 38 | 21 | 2 | 15 | 55% | 0 | 43 | 3.1 |
| 5 | 5 | 5 | 2 | 2 | 1 | 40% | 0 | 2 | 0.4 |
| 6 | 2 | 5 | 4 | 0 | 1 | 80% | 0 | 8 | 4.0 |
| 9 | 1 | 4 | 1 | 0 | 3 | 25% | 1 | 9 | 9.0 |
| 11 | 2 | 1 | 0 | 0 | 1 | 0% | 0 | 0 | 0.0 |

Rounds 9 and 11 are two PRs (#182, #190) whose sessions numbered rounds
continuously across a long day rather than per change; they are late rounds, and
they are kept in the table rather than dropped, because one of them carries the
finding that decides §5's last question.

Read across the rows:

- **Round 2 is worth exactly as much as round 1** — 8.2 against 8.1 per
  dispatch, and 99% of what it raised was upheld against 89%. Any proposal to
  stop after one round is refuted by this line.
- **The fall-off starts at round 3, not round 2.** Value per dispatch drops 36%
  at round 3 and 62% by round 4, and the mechanism is visible in the dismissed
  column: 4 dismissals in round 1, 23 in round 3, 15 in round 4. Later rounds do
  not raise less, they raise more that gets adjudicated away.
- **Severity collapses after round 2, not after round 1.** 10 of the window's
  11 upheld HIGHs arrived in rounds 1-2 — but 3 of those 10 arrived in ROUND 2,
  which the table above prints. So this measurement does NOT reproduce
  the #185 finding as that ruling stated it ("0 of 28 round-2+ findings
  were HIGH", over 3 PRs): on 16 PRs and 327 findings, rounds 2 and later carry
  5 HIGH findings, 4 of them upheld. What survives on the wider corpus is the
  weaker and still decisive claim — the collapse is real, and it happens one
  round later than #185 measured.
- **The eleventh HIGH is the exception that decides the case.** It arrived in
  #182's late round, from a `code-reviewer`, and it is that the merged protocol
  both mandated and forbade carrying a reviewer report forward between rounds —
  `pre-pr-review/SKILL.md` ordering the copy at one line and forbidding it 87
  lines later. That is a defect only a full lens re-read of the whole change
  finds; a scoped re-reviewer looking at the repair range would not have been
  looking at it.

The existing cap already prices this: `deliver` caps repair at 3 change-defect
rounds and `warden round classify` decides which rounds count. Rounds 4-6 exist
in the table because scaffolding rounds do not count against that cap, which is
the cap working rather than the cap leaking.

## 4 · Flat versus scaled on the same 16 PRs

Every PR in the window, with the three properties a tiering rule could key on.
`tier excluding evidence shards` re-runs the classification with
`.warden/memory/**` removed, to check whether the HIGH column is an artifact of
attesting rather than of the change.

| PR | lines | files | max declared tier | tier excluding evidence shards | docs-only | under 200 lines |
|---|---:|---:|---|---|---|---|
| #184 | 2103 | 18 | HIGH | HIGH | no | no |
| #185 | 2649 | 13 | HIGH | HIGH | no | no |
| #186 | 2816 | 19 | HIGH | MEDIUM | no | no |
| #182 | 1449 | 17 | HIGH | MEDIUM | no | no |
| #187 | 1152 | 14 | HIGH | MEDIUM | no | no |
| #188 | 990 | 3 | HIGH | LOW | no | no |
| #189 | 1308 | 2 | HIGH | LOW | no | no |
| #190 | 1665 | 10 | HIGH | MEDIUM | no | no |
| #191 | 1891 | 13 | HIGH | MEDIUM | no | no |
| #192 | 999 | 15 | HIGH | MEDIUM | no | no |
| #193 | 897 | 11 | HIGH | HIGH | no | no |
| #194 | 3419 | 30 | HIGH | HIGH | no | no |
| #195 | 3530 | 24 | HIGH | MEDIUM | no | no |
| #196 | 4444 | 15 | HIGH | MEDIUM | no | no |
| #197 | 1719 | 14 | HIGH | MEDIUM | no | no |
| #198 | 1818 | 14 | HIGH | MEDIUM | no | no |

Three things fall out of this table, and each kills one candidate policy:

1. **No PR is docs-only.** The class the bead was written to cheapen has not
   occurred once since the ruling. The disproportion it cited — PR #170's two
   design documents — has no successor in the window.
2. **No PR is small.** The smallest is 897 lines; the prior ruling's own SIZE
   lead pointed at a 200-line threshold and 30 PRs under it, and this window
   contains none of them.
3. **Every PR is HIGH by declared risk tier**, because attesting writes a shard
   under `.warden/**`, which `repo.yaml` declares HIGH. Four (#184, #185, #193,
   #194) are HIGH by a second path as well — over-determination, not a
   counter-example. The bead's preferred
   shape — "the tier must fall out of the paths touched, and the gate must
   compute it" — computes HIGH for every diff on this repo, forever, as a
   consequence of leaving evidence. Even with evidence shards excluded the
   column only reaches MEDIUM or LOW on 12 of 16, and two of those (#188, #189)
   are tests-and-evidence PRs whose reviews still returned 8 and 26 findings.

| policy | PRs it would have changed | findings not raised | upheld lost | upheld HIGH lost | dispatches saved |
|---|---:|---:|---:|---:|---:|
| flat, as run (status quo) | 0 | 0 | 0 | 0 | 0 of 109 |
| tier by diff class: reduced crew on docs-only PRs | 0 | 0 | 0 | 0 | 0 of 109 |
| tier by size: reduced crew under 200 lines | 0 | 0 | 0 | 0 | 0 of 109 |
| tier by declared risk tier: full crew only on HIGH | 0 | 0 | 0 | 0 | 0 of 109 |
| no full-crew lens after round 2 | 6 | 36 | 24 | 1 | 12 of 109 |
| drop `crew:consumer-blast-radius` | 10 | 47 | 40 | 3 | 16 of 109 |
| drop every `crew:` lens | 10 | 112 | 98 | 5 | 29 of 109 |

The first four rows are the answer to "what would flat-versus-scaled have cost
or saved, concretely": **nothing, in both directions.** The three tiering shapes
the bead proposed would have changed no PR in the window, so they cannot be
justified by savings and cannot be refuted by losses. A lever that has never
moved is not free — it is a builder-visible control over review depth, which is
the trap the bead itself named — so on this evidence it should not be built.

The last three rows are the levers that would have bitten, and each costs at
least one upheld HIGH.

## 4b · The harness comparison, reported by the units that measured it

Two figures arrived after this measurement was built and are **not re-derived
here** — they were measured by the units that ran those PRs, and they are stated
as reported:

| PR | review found | the three harnesses landed today found |
|---|---:|---:|
| #198 | 20 of its 20 findings | 0 of them |
| #199 | 19 confirmed findings, across six rounds | 0 of them |

**39 confirmed findings, none of them caught by the new harnesses.** It bears on
the depth question in both directions and is not spun here either way: it is
evidence that a deterministic harness does not substitute for a review lens on
this corpus, and it is also the cleanest available statement of what review is
buying per round. The founder should weigh it; this document does not.

## 5 · What the cost argument was built on, re-measured

The bead's cost premise was "5 to 6 independent-context reviewers, a
cross-examiner, and up to 3 repair rounds" per PR, and 75-95 minutes of wall
clock. The corpus carries no wall clock, so that half is unmeasurable here and
is not restated as though it were. The reviewer count is measurable:

| | bead's premise | measured | denominator |
|---|---|---|---|
| reviewers in round 1 | 5-6 plus a cross-examiner | exactly 3 (`code-reviewer`, `crew:consumer-blast-radius`, `cross-examiner`) on every one | the 6 of the last 8 PRs that carry a round-1 roster at all (#196 and #197 carry none) |
| all dispatches per PR | not stated | 6.8 | 109 dispatches over 16 PRs |
| repair rounds | up to 3 | 3 change-defect rounds, `warden round classify` deciding | the policy, not a measurement |

**The denominators are not the PR count, and the difference matters.** Only 11 of
the 16 PRs carry a round-1 roster in the corpus at all; #182's entire attributed
roster is a single late-round dispatch. Where a row above is measured over fewer
PRs than 16, the row says so rather than borrowing the larger N — the failure
mode this document exists to argue against.

The 5-6 crew is real in the three attributed PRs whose round-1 roster carries it
(#184 dispatched 5 distinct roles in round 1, #185 dispatched 7, #186 dispatched
6) and has not been dispatched since #189. The
shrink is what the charter says should happen: `enforcement-truth` and
`fail-closed` became `.warden/rules/` files, so they are enforced on every diff by the rules engine and their
charter entries are pointers rather than crew slots. **The cost the bead argues
against is roughly half what it was when the bead was written, and most of the
reduction already happened without tiering.**

## 6 · The instrument was broken

This reopening depends on reading the LENSES table, and the command that renders
it was answering from a corpus nine days old.

`warden memory stats` computes its precision rows from the derived cache
`.warden/memory/findings.jsonl`, which only `warden memory ingest` rewrites. In
a checkout whose cache was last built on 2026-09-01 21:22 — before the first
lens-carrying shard, written 2026-09-02 23:27 — the LENSES section renders no
rows at all and reports `893 judged record(s) carry no lens`. That is
arithmetically forced: the cache predates the field.

The same report's REVIEW EVENTS section reads the shard directory live, so one
run printed `151 review(s) on record` from 151 shards on disk beside precision
rows computed over a 980-row cache that knew about fewer. **Two corpora in one
report, with nothing saying so.** Neither the renderer nor the `judged` filter
is at fault: fed the shards, `stats()` produces the full LENSES table, and the
numbers in this document come from exactly that path.

`docs/wiki/Memory.md` already warned that a stale cache "will happily reprint a
number the shards no longer support". The hazard was documented; only the
command was silent about which state it was in. That is why the fix is a signal
at the command rather than more prose.

The staleness signal is an mtime comparison, and its error is one-sided by
design: a fresh clone stamps every file at checkout time, so a cache whose
content is current can read as stale after a pull. A false "run ingest" costs
one command; a false "current" cost this reopening a day. An exact check would
have to re-read every shard, which is the cost the derived cache exists to
avoid.

The fix in this change is the missing sentence, not a new source: `warden memory
stats` now opens with a CORPUS line naming the file it computed over, its
record count and its mtime, beside the shard count on disk — and warns when a
shard file is newer than the cache. The source stays the cache on purpose:
`memory recall` and `warden plan` read the same cache, and a `stats` that
silently read somewhere else would answer a different question than the priors
a builder is handed.

## 7 · Reconciliation with the 2026-09-10 hand count

A direct read of the shards in a checkout at `4939a43` (151 shards) counted 182
lens-carrying records — `code-reviewer` 52, `scoped-re-reviewer` 37,
`crew:consumer-blast-radius` 26, `crew:enforcement-truth` 22,
`crew:fail-closed` 16, `crew:evidence-attribution` 11, `crew:evidence-intact`
11, `crew:tests-bite` 5, `cross-examiner` 2 — with rounds 98/31/26/16/4/2/4/1.
Reproduced here exactly, to the record. This document's larger numbers differ
for two reasons and no others:

| | hand count | this document |
|---|---|---|
| checkout | `4939a43`, 151 shards | `c46097f`, 176 shards |
| records | raw | folded per #186 (362 raw lens-carrying → 327 findings) |

The five lenses that stopped being dispatched after #189 — `enforcement-truth`
22, `fail-closed` 16, `evidence-attribution` 11, `evidence-intact` 11,
`tests-bite` 5 — carry **identical** counts in both, which is the cross-check
that the difference is the 25 newer shards and the fold, and nothing else.

## 8 · What is not decided here

- Whether the three undeclared lenses (`crew:evidence-attribution`,
  `crew:tests-bite`, `crew:evidence-intact`) should join the charter's standing
  list. One PR each; only `evidence-intact` clears the promotion bar. Tracked
  separately.
- Whether the roster a session dispatches should be checkable against the
  charter. Today a builder chooses it, and the only reason that has not drifted
  is that the shrink tracked the two lens-to-rule promotions. Tracked
  separately, and it is the prerequisite for ever tiering depth: a tier a
  builder can talk its way into is not a tier.
- The reopen trigger is NOT on this list: the charter paragraph decides it, and
  changes it to 20 dispatches of a single lens on a single diff class. It is
  named here because the reason is a measurement — 16 attributed PRs cleared the
  "roughly 20" the prior ruling named for the two high-volume lenses and did not
  clear it for the specialists, which sit at 4 and 5 dispatches, and a PR-count
  trigger cannot tell those apart. A reader who wants to disagree should
  disagree with the charter, not with this section.
