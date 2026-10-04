# Nightgate v1.0 — the replan, design of record

**The plan, rendered:**
<https://claude.ai/code/artifact/12e0d461-3cc4-4d85-bf14-228a684da2d3>
("Past the Merge Gate", 23 Aug 2026 — sequel to the engineering note
"Proof Before Merge")

Founder-approved 2026-08-23. This file is the durable, in-repo record: the
artifact is the readable form, the beads are the executable form, and this is
what a future session reads to know *why* the shape changed.

## The goal

> Nightgate makes a team ship software faster over time without craftsmanship
> decaying, because every change carries a provenance-bound proof against a
> rule set the repo itself grows.

Three testable commitments:

1. **Every lifecycle stage leaves an artifact bound to a policy version** — not
   just the merge gate. Plan, design and deploy decisions get the evidence chain
   review verdicts already have.
2. **Rule-set evolution is driven by evidence volume, not by the calendar.** Any
   trigger that produces a PR feeds the corpus. Unattended-ness is a deployment
   option, never a maturity level.
3. **The floor rises measurably.** A repo can show that the rule set it has today
   catches what last month's would have missed — backtested, not asserted.

## Why the shape had to change

Until v1.0 the platform answered one question well — *what had to be true before
this change was allowed to merge* — and was organized entirely around that
moment. The goal above needs the whole lifecycle, with the gate as one station.

Five findings, each verified at source, drove the restructure:

| | finding | the load-bearing detail |
|---|---|---|
| **F1** | Night shift is a schedule that became an architecture | `cage/config.py` rejected any window that did not cross midnight; `cage/run.sh` set `BRANCH` from `DATE_TAG`, making one run per calendar day *structural* |
| **F2** | The ladder made Unattended a precondition for Self-improving | levels are cumulative and cannot be skipped, so no repo reached L5 without a launchd plist; R-03 forced even cage-less repos to declare night eligibility |
| **F3** | The promotion pipeline could not promote | attestation `rule_id`s were free-form and never validated, so per-rule precision was computed over a namespace that did not exist — nothing could be promoted *even with a full corpus* |
| **F4** | The lifecycle had a hole at both ends | no design stage, no architect stage, and no deploy anywhere in the platform |
| **F5** | The hard-stop surface had no execution test | `cage/run.sh` — 337 lines, HIGH tier — was covered only by `bash -n` and string greps |

F3 is the one worth remembering. The bug and the opportunity were the same
object: a free-form `rule_id` that names no declared rule is precisely *"a
reviewer keeps finding something no rule covers"* — the strongest self-evolution
signal available, sitting in the corpus, discarded as noise.

## On obra/superpowers

Re-read at source during this replan (v6.3.0). It has **zero mechanical
enforcement** — no `PreToolUse`/`PostToolUse`/`Stop` hooks exist; path
enforcement was designed and never shipped — and it **deliberately destroys its
own evidence** at merge (`rm -rf <workspace>`, "the git history is the record
now"). Its own `CLAUDE.md` draws our boundary for us: *"if it's enforceable with
regex/validation, automate it — save documentation for judgment calls."*

So the layers compose rather than compete: superpowers owns in-session judgment,
warden owns out-of-session mechanics. Taken forward: the `Ruling: what — why —
what it costs if wrong` line (as `warden decide`), the RED/GREEN report
contract, and the micro-test protocol (control arm, 5+ reps, variance as a
metric) as validation for rule promotion.

The earlier refusal of `brainstorming` / `writing-plans` / `executing-plans` in
[methodology.md](methodology.md) was **correct under the old goal** and is
reopened under the new one — see that file's superseded section, never a silent
reversal.

## The seven workstreams

| epic | workstream |
|---|---|
| **E1** | Deprecate the schedule, keep the cage |
| **E2** | Evidence over cron — restructure the ladder |
| **E3** | Make promotion possible — rule identity and corpus fuel |
| **E4** | The missing front half — design and architect |
| **E5** | Deploy provenance |
| **E6** | Inner loop speed and cleanup |
| **E7** | Wiki fidelity — the wiki is the product |

The rule-advisor epic survives intact, re-parented under **E3**
behind rule identity: its children were well specified, but the premise that the
corpus worked was the flaw.

## The wiki is the product

Founder's standing requirement, recorded here because it outlives this replan:

> The wiki must reflect what Nightgate **is**, 100% accurately.

Read strictly, that is not "keep docs updated" — a wiki page that misdescribes
the system is a **defect**, on the same footing as a failing checker. Hence two
mechanisms rather than a habit: four derivation tests (code is the source, the
wiki must contain it), and `docs/wiki/**` raised off LOW tier with a
`wiki-fidelity` rule that can block.

Ground truth beats prose: where this record and the code disagree, the code and
its tests win. File a bead.
