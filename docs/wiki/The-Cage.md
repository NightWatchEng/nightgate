# The Cage

An unattended dev loop wrapped in hard stops the model cannot rewrite. The
session inside builds one item, survives the gauntlet, opens a PR, and reports.
The cage decides *whether* a run happens and *what happens after* — all of it
outside the model.

A trigger is how a run **starts**, not what the cage is. Every stop below holds
identically whichever trigger fired.

## The hard stops, in order

`run.sh` executes these in order, before the model gets a turn. Most either
proceed or produce a SKIP with a report, a ledger row, and a notification.

**Three do not, and the runner says so about itself.** A missing `cage.env`, a
runner/env version skew, and a quiet-hours bound the runner cannot parse all
`exit 2` on stderr alone — the first two fire before the log, the ledger and
the notifier exist, and the third is left as-is rather than have an unrelated
change rewrite its exit contract. All three fail closed, so no run happens; a
schedule-triggered run captures the line in the cage log, but a manual or
ci-event run leaves it on a terminal nobody is watching. Everywhere else,
silence is unambiguous.

| # | Stop | Fails how |
|---|---|---|
| 1 | source `cage.env`, completeness check | a runner/env version skew is fatal |
| 2 | claim `RUN_ID` under `noclobber` | an unwritable reports dir is a normal SKIP |
| 3 | kill switches — `~/.cage-off`, `~/.cage-<name>-off` | honored ahead of every *negotiable* stop, the quiet-hours override included — but after the two above, which can end a run before either switch is read |
| 4 | quiet hours | a bound the runner cannot parse is fatal, never "not quiet" |
| 5 | tooling probes — `claude`, `gh`, a runnable `python3` (the outcome classifier parses the session's result event with it), every name in `[toolchain].require`, `prompt.md`, platform probe, services preflight | any missing → SKIP, **before** an item is claimed |
| 6 | PR back-pressure — open run PRs under the cap | unreviewed work pauses the loop |
| 7 | main-green | never build on a red base |
| 7a | usage pre-flight — a minimal probe session, skipped by `--check` | exhausted or unreadable usage → `usage-skipped`, no session launched ([below](#the-usage-pre-flight)) |
| 8 | reset the dedicated worktree to `origin/main` | the live checkout is never touched |
| 9 | run `claude -p` under the caged permission profile | a pure-bash watchdog caps the wall clock (stock macOS has no `timeout`/`setsid`) |
| 10 | forbidden-path post-check on the diff | a hit auto-closes the PR; a regex evaluation error **fails closed** |
| 10a | on a hit, and only if the project granted `[gate].autonomy_carve_out`, the **autonomy-ladder carve-out** (`warden autonomy carve-out`) | clears the stop only if every **forbidden path** in the committed diff is a machine-tier pause, a non-blocking adoption, or a review round's attestation shard — and, where the grant names stores, sits under one of them; a missing shim, a refusal, a crash, or a regex or `git diff` that could not evaluate all leave the stop standing. It never merges |
| 11 | publish the corpus — on a **cleared** run, only if 10a admits the commit it makes — then ledger + report + notify | every failure is stated, never absorbed |

Two more live outside `run.sh` entirely, and the difference between them
matters:

- The **permission profile** is the caged session's allow/deny list — no
  merges, no writes to the cage dir or the live checkout, no escalation tools,
  and exactly one `git push`: the run's own branch. `cage-profile.json` writes
  that push as exact rules over a `__CAGE_RUN_BRANCH__` token, and `run.sh`
  hands the session a per-run copy (`reports/<run id>.profile.json`) with the
  run's branch in its place. The session may type
  `git push origin <branch>:refs/heads/<branch>`,
  `git push -u origin <branch>:refs/heads/<branch>` or
  `git push --set-upstream origin <branch>:refs/heads/<branch>` and nothing
  wider — no other refspec, no force flag, no other branch, no `main` —
  unless `[profile].extra_allow` or another Claude settings scope adds a push
  rule. The destination is spelled out because a push naming only the branch
  lets git config choose it: `push.default=upstream` (or `tracking`) and a
  `remote.origin.push` refspec, from `~/.gitconfig` or the consumer's
  `.git/config`, both remap `git push origin <branch>` onto `main`. With
  `<branch>:refs/heads/<branch>`, the only branch those three commands can
  update on `origin` is `<branch>`, whatever that config says. The runner also
  cuts the branch with `--no-track` and pushes its own corpus commit with the
  same refspec. The runner states those three spellings in the prompt. A profile
  without the token, or a resumed branch outside the prefix, skips the run
  (`--check` reports it). The earlier `git push origin <prefix>:*` rule never matched a real
  run-branch push (`:*` is a trailing space and wildcard), which is how
  finished, attested runs ended with no PR. The rest of the allow list is
  prefix rules, and prefix rules are not a fence: `uv` and `python3` can run
  git themselves. The seal on the cage dir is the same kind of thing: its
  `Read`/`Edit`/`Write` denies govern those tools and not `Bash`. What stands
  between a session and re-rendering the live runner from its worktree is a
  deny on the spellings that would do it (`uv run --project`,
  `uv run --locked --project`, `uvx`, `uv tool`, `python3 -m cage.cli`), and those
  are spellings, not a class: `uv run --no-sync cage enroll` starts with the
  allowed `uv` and matches none of them.
- **Branch protection** is what actually protects `main` and prevents history
  rewrites, because GitHub rejects the push server-side no matter how it was
  spelled. Count one fence here, not two.

## The usage pre-flight

Before it touches the worktree, the runner checks that usage is left, so it
does not start a session the usage limit will kill. Claude Code documents no
way to read remaining usage non-interactively. The statusline's
`rate_limits.five_hour` and `seven_day` percentages exist only inside an
interactive session, after its first API response. So the check is a one-turn
`claude -p` probe under `--safe-mode`, which loads no CLAUDE.md, hooks, plugins
or MCP servers, runs with no tools, and saves no transcript. The runner reads
the top-level fields of the probe's final stream-json `result` event, as the
outcome classifier does for a session, and also checks the probe's exit code:

- a top-level `"is_error":true` with `"api_error_status":429` means
  **exhausted**;
- a clean result from a probe that exited 0 means **usable**, and the session
  launches;
- anything else means **unreadable**, and the run is skipped the same way.
  That covers a probe that fails, prints no parseable result, returns a
  different error, or runs past its timeout (300s, or
  `USAGE_PROBE_TIMEOUT`). That variable is read from the environment of a
  manual `bash run.sh`. A scheduled run does not inherit your shell, and the
  rendered launchd job declares no environment variables of its own; a line
  hand-added to `cage.env` is read, but `cage enroll` regenerates that file,
  so it is not a place to keep one.

**The threshold is binary: usable or not.** There is no percentage to compare
against, so `cage.toml` has no threshold key. A usable probe does not say how
much is left, so a run can still hit the limit mid-session. When that happens,
the resume path below continues the run only if the session left a checkpoint.
Any 429 on the probe counts as exhausted, including a short API rate limit.

A skip writes the ledger outcome `usage-skipped`, with the reason `usage
exhausted — <the result's text, e.g. its reset time>` or `usage unreadable —
<why>`. It also writes a report, sends a notification, and keeps the probe's log
at `reports/<run id>.usage-probe.log`. No session is launched, and a pending
resume is left as it was. `--check` does not probe, because the probe spends a
turn.

## Publishing the corpus outside the model

After a run whose stop stands clear and which produced commits, the runner
itself runs `warden memory ingest`, commits the attest shard, and pushes the
run branch. A session's review rounds are not lost just because `.warden/` is a
forbidden path to the session that produced them.

It never publishes a forbidden-path run or a run with no commits. And the
warden binary runs from the **live checkout**, never the session-writable
worktree — the live checkout is Write-denied to the session, so the runner never
executes worktree-supplied code with its own privileges.

**On a cleared run, the shard must fit the grant that cleared it**. The commit lands *after* the verdict, so before pushing it
the runner asks whether that commit is admissible under the same grant — the
consumer's declared stores, then `warden autonomy carve-out` — and pushes only
if it is. Where it is, the cleared verdict still holds over the pushed head and
a reviewer re-running it gets what the cage got. Where it is not — a scoped
grant admitting `.warden/rules/` and not `.warden/memory/attest/` — nothing is
pushed, the commit is **undone**, and the report names the grant that refused.

Undone rather than left local — and undone on **any** path that does not end
in a successful push, a failed push included — because a *resumed* run checks
the same branch out and keeps its work: a commit that never reached the remote
would otherwise join the next session's diff, and on a project that forbids
`.warden/` that is a FORBIDDEN-PATH verdict closing the resumed run's PR, a
session's work destroyed by the previous run's housekeeping. The undo is a
`--mixed` reset to the sha recorded immediately before the commit, with the
store alone restored and cleaned: a `--hard` reset would also discard every
uncommitted tracked change the session left, and a cleared run can be a
resumable one, so the undo would delete part of what the resume exists to keep.

**This check runs on a cleared run and nowhere else**, and the narrowing is the
point. The forbidden-path freeze's subject is the **session**; this commit is
the runner's, made by trusted code from the live checkout after the session
exited. Judging the whole head against the freeze on every run would refuse the
push for the consumer shape this very page documents — `.warden/` forbidden
with no carve-out grant — on every run forever, withdrawing runner-side
publication from exactly the projects it exists for and reinstating the NO
ATTESTATION failure one layer up.

Two things bind the commit on **every** run, cleared or not, and they are what
make that trust argument true of the commit's **content** rather than only of
the code that makes it:

- **The store must be clean before `warden memory ingest` runs.** `git add`
  stages a directory in the session-writable worktree, and the freeze reads
  only the *committed* diff — so a file the session dropped under
  `.warden/memory/attest/` is invisible to it and would be published over the
  runner's name onto the head a reviewer fetches. `warden gate` and
  `attest check` both read that store. Uncommitted content there refuses the
  publication and says so.
- **The commit must contain nothing outside that store.**

**A resumable run publishes nothing.** On `timeout` or `usage-limit` the
branch is kept for a resume that continues the same work, so a shard committed
now would join the *resumed* session's own diff — and on a project that forbids
`.warden/` its freeze closes that PR over the previous run's housekeeping. The
run that finishes the bead publishes; if the resume budget is spent first the
attestation is lost, and the deferral note is where that is said.

This replaced a blanket skip on carve-out-cleared runs. That skip cost nothing
on a run cleared by the *attestation* arm (the session had already committed
its own shard, so ingest found nothing left) and cost a run cleared by a
*pause* or an *adoption* alone its entire attestation: it stayed in gitignored
`.warden/out/` forever, the corpus never learned the round happened, and the
PR then failed `warden attest check` with NO ATTESTATION — the same vice
#273 closed, in the one shape that change did not reach.

## What the caged `PATH` carries

`run.sh` resets `PATH` to a fixed list — `~/.local/bin`, Homebrew, the system
directories — so a run started by launchd behaves like one started by hand.
That reset is also why a toolchain installed anywhere else is **invisible
inside the cage**. `[toolchain].path` declares directories to prepend (before
`services.sh` is sourced, so a service's keg-only prepend still wins over the
base list), and `[toolchain].require` declares the bare command names a run may
not start without, each resolved with `command -v` at stop 5.

**Declaring `[toolchain]` has a second, session-visible effect, and it is not
the `PATH`.** `run.sh` exports `AGENTOPS_REQUIRE_TOOLCHAIN=1` whenever either
key carries anything, and that switch is how this platform's
own suite decides what an absent declared tool MEANS. `tests/toolchain.py`
splits the question by CONTRACT rather than by tool: **strict** (the switch
set, which CI sets too) makes an absent tool a named FAILURE, because the
coverage lost is coverage the environment promised; **lenient** (the default —
a contributor's laptop, which promised nothing) makes it a named SKIP carrying
the install command. Either way the absence is NAMED and the collection count
does not move, which is the half both halves share. A cage that declares a
toolchain has made the promise, so a caged run is strict; a cage with no
`[toolchain]` block at all declared nothing and keeps the lenient half. The
export is an assignment and not a default, exactly like the `PATH` reset
above: an ambient `AGENTOPS_REQUIRE_TOOLCHAIN=0` from a debugging shell must
not follow a founder-started run into the cage.

The bead that filed this is worth stating. Go's installer
writes `/usr/local/go/bin`, which is on no such list, and this repo's own suite
enrolls a Go fixture and runs the verify scope `warden init` wrote for it. So
inside the cage `warden verify --scope tests` was red at **every** head, and
the first completed unattended run here built and reviewed a fix in 27 minutes and
then refused at ship step 1 — a stop no session could ever clear.

The answer is not to skip the test when the toolchain is absent. This corpus
already ruled on that shape in `tests/test_commit_lint.py`,
and the ruling has two halves: a **required** tool absent is a red suite, and
a `shutil.which` filter over it is forbidden; an **optional** one absent is a
skip whose reason names the gap. `[toolchain].require` is where a project says
which of its tools are in the first class. For this repo `go` is: the platform
is Python and its live consumer is Go, so that cell is the only executable
proof in the suite that the enrollment surface works for a language the
platform is not written in — the portability claim itself, and a filter over
it would have turned the scope green on a run that proved nothing. So the
toolchain is **supplied**, and its absence is a **named skip before a bead is
claimed** rather than an hour spent reaching a stop that was decided before the
run began.

## The autonomy-ladder carve-out

The ladder's machine tier may write exactly four things under the otherwise
forbidden `.warden/`: `paused: true` plus a `paused_reason:` in an existing rule
file, a **new non-blocking** rule file, the backtest artifact that change
carries under `.warden/memory/backtests/` — a `pause` record for the first, an
`adopt` record for the second — and the **attestation shard** a review round
leaves under `.warden/memory/attest/`. The post-check's regex cannot tell those from a session rewriting the
gate, so it used to close the PR for all of them — the permission existed on
paper and was auto-closed in practice.

**Why an attestation is in it**. The first three write rules
and the artifacts that justify them; a shard changes nothing the gate enforces.
It is the committed record that a review ROUND ran, which `warden attest check`
then requires of every PR — so freezing it put the platform's two halves in
contradiction: the gate refuses a PR with no committed shard, the freeze refused
the shard, and there was no path between them. The first completed unattended
run, `20260920-205434`, proved it live: it picked a bead, built the fix, ran two
review rounds, fixed what round 1 found, committed its shards exactly as the
protocol says — and stopped at a forbidden path for it, with its work unpushed.

It does **not** make a shard true. `verify_roster`'s own docstring is explicit
that a caller writing plausible reviewer files passes it, so nothing in warden
tells a real round from a fabricated one. What keeps that from being a new hole
is that the runner already carried session-authored attestation content into the
committed corpus without this arm — the publication step below runs `warden
memory ingest` over the session's own `.warden/out/` artifact and commits the
shard it derives. Same data, trusted code; the arm only moves who commits it.
The bound is elsewhere and unchanged: `graph.yaml` (which fixes the crew a
roster must match) stays frozen, the carve-out clears a stop and never merges,
and a human merges every PR.

Two narrowings past what `warden attest write` itself permits: the shard must
record `roster_verification: verified` and `round_binding: bound` — a roster
cross-checked against the round's own reports, from a directory `warden round
new` minted for the head being attested. `unverified-roster` and `unminted` are
legal shards for a human running warden by hand; they are bare assertions, and a
bare assertion is not what gets past a hard stop. The shard's `sha` must name a
commit in the judged range, so an imported one is refused.

**Both of `memory ingest`'s refusals are re-applied, and so is its redaction** —
that is what "the same data ingest would have committed" has to mean if the
equivalence above is to hold. Only the FIRST of the three below is scoped to
a half of a shard; the other two apply to every one:

- a shard **with records** has every record's `rule_id` resolved against the
  ruleset committed at the **merge base** (`attest._check_rule_ids`, the same
  checker ingest calls), so a hand-written shard naming an undeclared rule is
  refused — those records feed `memory stats`, the pause candidates and
  certification **E-02**;
- a shard with **no records** must carry the fields that make a review an
  EVENT — `sha`, `base_sha`, `reviewed_at`, `reviewers`, and a `verdict` in
  the vocabulary (`memory._event_defects`). This is applied to **every** shard,
  records or not, which is stricter than ingest, where a records-bearing
  artifact is exempt because its records carry the provenance;
- and the shard must be **unchanged by ingest's scrubbers** (`_redact_value`
  over the envelope, `_redact` over each record's finding/evidence/reason).
  They are idempotent, so a shard that came through ingest is a fixed point;
  one that is not is a shard ingest would have REWRITTEN, which is not the
  same claim as "it carries a credential". `_redact` keys on the rule id, so
  a record filed under a rule whose id contains `secret` — this repo's own
  `secrets-in-diff` — is rewritten wholesale with nothing secret-shaped in it
  at all. The check refuses, names the field, and says which of the two it
  found, so the writer is not sent hunting a credential that is not there.
  It never rewrites committed bytes. What it reads on the record side is
  exactly `finding`, `evidence` and `reason` — the three `_redact` itself
  takes; a string planted in a record's `file` or `tags` is admitted, at
  parity with ingest, which copies those unscrubbed too.

What is **not** checked is the verdict's VALUE: an early round's
`findings-open` shard is committed by design, and `attest check` owns the
tip-clean rule over the whole range. A verdict that is *absent* or outside the
vocabulary is a different thing and is refused, above.

`.warden/memory/decide/` is **not** admitted, by decision rather than omission:
a decision shard records what was *ruled* — policy the skills refer back to —
and no run is blocked for want of one.

**It is off unless this project granted it.** `[gate].autonomy_carve_out`
defaults to `false` in `cage.toml`, so a repo that never granted it keeps its
forbidden-path stop whole however new the platform gets — turning it on is the
consumer's act, and takes effect after the next `cage enroll` re-renders the
cage dir.

**The grant may pin its own SCOPE**. Written as a bare
`true` it does not: what it admits is whatever the pinned platform admits, so a
consumer who granted it against the three-write carve-out and then re-pins to
this SHA gets the attestation arm too, with no new grant to give. #273
is the first widening and the reason this page's earlier sentence ("enrolling a
newer platform never weakens a hard stop a project already declared") is gone —
it was false for a granted consumer on exactly that commit. Written as a **list
of store prefixes** it does pin:

```toml
autonomy_carve_out = [".warden/rules/", ".warden/memory/backtests/",
                      ".warden/memory/attest/"]
```

The runner then clears a forbidden path only when the platform's shape check
**and** the consumer's list both admit it — two authorities, AND-ed, and the
consumer's half can only ever narrow. A store a newer platform grows is outside
every named prefix, so it stays stopped; the run's report names the path and
the grant, so the widening arrives as something a human reads and decides,
rather than as silence. A bare `true` keeps loading so no existing config
breaks, and `false` still restores the whole stop. This repo's own cage is
written as a list — self-hosting is the credibility test.

Granted, on a hit the runner asks `warden autonomy carve-out` whether every
**forbidden path** in the committed diff is inside the carve-out. It judges the
paths the post-check matched, not the whole diff; the rest is ordinary session
work the freeze never objected to.

**Provenance is decided by shape, not by a marker**: anything a session can
write proves only that the session wrote it, while a diff shaped like the one
`warden autonomy` produces *is* the change it is permitted to make. A modified
rule file is re-derived from its base bytes with the writer's own insertion and
must match the committed bytes exactly, so nothing rides along; an added rule is
re-parsed and refused at any blocking severity, and also if it declares
`covers:` or `implements:` — both raise enforcement at a severity that cannot
fail a build; the paired backtest must be an `action: pause` record naming a
rule this diff **paused** or an `action: adopt` record naming a rule this diff
**added** — no other action, and never unbound. A pause record carries
*declared* counts (checked for shape and against the ladder's streak bar, never
recomputed from the corpus); an adopt record must declare `"judged": 0` and a
severity matching the committed rule file, because at adoption time the rule has
produced no findings and any tally would be a measurement claim nothing backs. Each rule change must carry its own artifact: an adoption
alone bumps `rules_version` with nothing naming the version it produces, which
is a tree that fails certification **S-05**.

**The policy it judges against — `blocking_severities` and `rules_dir` — is read
from the committed `repo.yaml` at the MERGE BASE**, the last commit the session
did not write. Reading it from the worktree let an uncommitted `repo.yaml` set
the bound (it never appears in the diff, so it is never a forbidden path);
reading it at the branch head left the same trick one commit away.

Two deliberate narrowings past the ladder text. Pausing a **blocking** rule is
outside the carve-out: this check cannot re-derive the refutation streak and the
UNANSWERED-ceiling guard that make a pause safe, and without them the reachable
abuse is precise — pause the one rule that would have failed this diff. And the
carve-out **verifies shape, not permission**: it does not re-derive the corpus
justification, which is the reviewing human's job on a PR that exists so a human
sees it.

It runs from the same trusted live checkout as the ingest above, and it **fails
closed everywhere**: it is consulted only when the grep and the `git diff`
before it both succeeded, and a missing shim, a refusal, or any unexpected exit
leaves the stop standing. It can only ever *clear* a stop, never create one.

A cleared run **publishes like any other run**, and the ordering objection that
used to stop it is answered rather than avoided: the runner asks this same
check about the commit it is about to push, and pushes only if the grant that
cleared the run admits it too. See
[Publishing the corpus](#publishing-the-corpus-outside-the-model) for what
happens when it does not.

**It does not merge, and there is no auto-merge path.** A cleared run opens a PR
exactly like any other run, and a human merges it. Merge authority is the
founder's, delegated only by an explicit, current, revocable grant, on the
terms `graph.yaml`'s `review.delegation` declares — the terms live there, the
grant never does, because a tracked file has no clock and a session grant
recorded in one outlives its session.

## Enrollment

`cage.toml` is the source of truth; `cage enroll` renders the derived artifacts
into the cage directory. The schema fails closed: unknown keys reject,
`forbidden_paths` must be declared, and `[triggers]` must declare at least one
trigger. Keys: [Configuration](Configuration.md).

| Artifact | Content |
|----------|---------|
| `run.sh` | a **byte-identical copy** of the platform runner — hash-comparable against `cage/run.sh` |
| `cage.env` | the shell-quoted per-project parameters the runner sources |
| `com.<name>.cage.plist` | the launchd trigger — rendered only when `[triggers.schedule]` is declared, deleted from the cage dir when it stops being, and never loaded by enroll (a deliberate human step) |
| `cage-profile.json` | the caged session's permission profile: a push to the run's own branch only (bound per run by `run.sh`), sealed cage dir and live checkout, denied escalation tools |
| `services.sh` | rendered exactly when `[[service]]` entries are declared, deleted when they stop being |

Enroll owns exactly what it renders: it rewrites those files and removes the
ones a config change makes obsolete, naming each removal. Anything else in the
directory is a human's file and is never touched. `prompt.md` stays
hand-authored; its absence is a pre-flight stop.

The cage directory lives **beside the checkout**, and `warden certify` finds it
there by convention: `<repo>/../<checkout>-cage/` or `<repo>/../cage/`. Among
the candidates present, the one whose `live_checkout` declares this checkout
wins whatever its name (in-repo `cage.toml` is never a claimant — enroll could
not render it); with no claimant the named directory is tried first, and a
cage that declares a *different* checkout refuses the badge rather than
passing silently.

Where certify cannot tell **which** cage would run, it refuses naming every
candidate rather than picking one. Two siblings that both declare this
checkout are one way in. The other is any sibling certify did not choose and
cannot disprove, and the test for that is one question — reading its
`live_checkout` yields nothing (`_cage_owner(p) is None`) — which a
`cage.toml` that will not parse and one that parses but declares no
`live_checkout` satisfy alike. Both were once separate
sentences here, and only the unreadable half was counted; a rendered `run.sh`
may fire from either. So the refusal labels each cage it names with one of
four reasons — `(unreadable)`, `(declares no owner)`, `(declares this
checkout)` or `(declares another checkout)` — above a detail that ends *one
cage per checkout, and certify cannot tell which of them runs*. Each label is
read off the file it names: the third used to be a fallback that printed for
any cage the first two did not catch, so a chosen cage declaring some OTHER
checkout was labelled as declaring this one; the fourth is
what it says now.

A LONE non-disprovable candidate still clears this refusal — one is not
ambiguous, two are. Clearing it is not the badge,
and the two halves of the class part company here: a lone sibling declaring
no owner earns it, while a lone one whose `cage.toml` will not parse is
refused a step later by the forbidden-paths check, *unreadable cage config*.

`../cage/` alone was one slot per workspace: the platform enrolled itself
beside its consumer's cage, discovery stopped at the consumer's, and its own
was never consulted.

### The platform's own cage

The platform declares, for itself, the cage it ships. The source is versioned
in this repo — `.cage/cage.toml` and `.cage/prompt.md` — so a PR reviews it and
`tests/test_self_cage.py` holds its `forbidden_paths` in lockstep with the
policy's `## Forbidden paths`: forward under the runner's own `grep -E`, and in
reverse by equality with the canonical rendering of the policy's paths. It does
not bind from there: enroll renders only beside its own toml, so it is
installed by copying both files into `../agentops-cage/` and enrolling that
copy, and re-copied after every change exactly as any toml change needs a
re-enroll. Manual trigger, no quiet hours, the carve-out granted because the
policy grants it.

What that earns, once the copy is installed, is the declaration — N-01, N-02
and N-03 on the machine that holds the copy — and only that. The install is a
human step and it has been done: the cage has run twice from that copy. One
run stopped at a forbidden path with its work unpushed; one opened PR #279,
which the founder merged. The counter-argument is real and stays stated:
the cage forbids `.warden/`, `repo.yaml`, `.github/` and `.claude/`, which is
where much of this repo's own gate work lands. What it does not forbid is the
rest of the tree, and that remainder is larger than the fence list reads,
because the runner's post-check is ONE START-ANCHORED alternation: each name
binds at the repo root only. The fence reaches every path `repo.yaml` tiers
HIGH, including the gate's own machinery under `warden/` —
`warden/mechanical.py`, `warden/plugins.py` and `warden/schemas/`. It did not
until #268: the fence closed only `warden/certification/`, the
prompt kept its own hand-written list of the reserved files, that list named
two of the three, and `warden/plugins.py` was therefore fenced nowhere and
handed to an unattended session by the one document binding it. The fix is
that there is no second list: the prompt's eligibility sentence points at the
fence, and `tests/test_self_cage.py::test_the_fence_and_the_prompt_close_
every_high_tiered_path` derives the required set from `repo.yaml` and reddens
if a HIGH glob is added that either the fence or the prompt misses. The fence
still closes MORE than HIGH — `warden/certification/` is tiered MEDIUM by the
`warden/**` catch-all — and only the narrow direction is a defect. The
example consumer's `.warden/`, `repo.yaml` and
`.github/` under `examples/` are outside the fence deliberately: that tree is
the portability fixture, tiered MEDIUM, and a test pins it as ordinary work.
The tracker labels the open items a run may take `cage-eligible`; the prompt
points a run at that shortlist, tells it the label is a hint and never a
grant, and sends it to the policy's `## Autonomy scope` as well, for the
exclusions — release mechanics — that no path check can see. A truthful
"nothing eligible" is still an honest answer for a run to report; it is no
longer the expected one.

**Dropping `[triggers.schedule]` does not unload a running launchd job.** The
job was bootstrapped from the *copy* in `~/Library/LaunchAgents`, and it
keeps firing `run.sh` on the old schedule until a human unloads it:

```
launchctl bootout gui/$UID/com.<name>.cage
```

`cage enroll` prints that line. Nothing in the platform can run it for you.

## Triggers

| Trigger | Renders | Means |
|---------|---------|-------|
| `[triggers.schedule]` | the launchd plist (`hour`, `minute`, default 23:30) | the machine starts it on a clock |
| `[triggers.manual]` | nothing | a human starts it: `bash run.sh` (or `--check`) |
| `[triggers.ci-event]` | nothing | an external event starts it |

A cage may declare more than one. This used to be a single `[schedule]` section
whose window **had to cross midnight** — a nine-to-five cage was a hard config
error, and launchd was the only way in.

## Quiet hours

`[quiet_hours]` declares the hours a run must **not** happen in, read forward,
crossing midnight or not (`23`→`5` is an overnight window; `9`→`17` is "not
during my working day"). Default 05:00–23:00; turning it off has to be said out
loud (`enabled = false`).

It is a **human-collision guard**, not a claim that any hour is special:
launchd re-fires missed jobs on wake, so a sleeping Mac would otherwise run the
loop into a working session. The one-shot override files
(`~/.cage-run-now`, `~/.cage-<name>-run-now`) bypass only this guard, are
consumed when they do, and are never consumed by `--check`.

## Run identity

Every run owns its own evidence. `RUN_ID` is a second-resolution stamp
(`YYYYmmdd-HHMMSS`) naming the branch, the report, the session log, and the
ledger's ninth column.

The ledger is CSV: its header row is written when the file is absent or empty,
its note is a quoted field, and a ledger that predates the header needs no
migration, because `cage measure` and `warden certify --goal` read either shape
and name by line any row they cannot count.

It replaced a date tag, which made "one run per day" **structural** rather than
a policy anyone chose: a second run the same day landed on the first run's
branch, report, log and ledger row.

Uniqueness is claimed, never assumed — the report file is created under
`noclobber`, so the loser of a same-second race takes the next sequence suffix.
A run id that cannot be claimed is a normal SKIP with a report and a
notification. It used to be a bare `exit 2` above the point where the notify
and skip functions were even defined, so a run could fail to happen and say
nothing.

## Resuming an interrupted run

A timeout used to lose everything — worse, the item came back marked attempted,
so every later run skipped it: the work wasn't restarted, it was **abandoned**.

A run's outcome is read from the session's final stream-json `result` event,
parsed as JSON, never from words in the log, because the injected prompt
itself says "usage limit". `usage-limit` means that event's own top-level
fields report an error with `"api_error_status":429` (the session limit); the
same keys inside a denied tool's input do not count. `push-denied` means one
of the event's refused Bash commands runs `git push` and no PR is open for
the branch; the ledger note names the command, and a push whose commits did
reach the remote does not count. The command is split into shell words, so a
push after `&&`, a newline, `{`, `then`, `env`, `VAR=` or a redirect such as
`2>/dev/null`, behind `git -C "<dir>"` or `git -c k=v`, or run through a
`git -c alias.<name>=push` alias, is found. One inside a quoted string, a
heredoc body or a `#` comment is not a command and does not count. A push
handed to another program as an argument (`bash -c`, `eval`, `xargs`, `sudo`),
written inside a double-quoted `$(...)` or an unquoted heredoc's `$(...)`, in a
heredoc body piped to a shell, or run through a shell (`!`) alias, an alias
defined in git config or one read from the environment by `--config-env` is
still not found, and the run is filed without the
note. If the
classifier itself fails, including when it cannot read the session log, the
report and ledger note say that neither outcome was evaluated, and the run is
never filed `usage-limit`. Otherwise a non-zero exit is `failed(N)` — including
an API error that is not a 429, such as a laptop sleeping mid-response, which
is therefore not resumed.

The session now checkpoints `.run-progress` after each phase. When a run ends in
`timeout` or `usage-limit` *and* a checkpoint exists, the cage saves a resume
state and the next run continues **the same branch and the same PR** instead of
resetting to `origin/main`.

Budgeted like every other stop: `[limits].max_resumes` (default 2), after which
the state is cleared and the item goes back to the founder. Only interruptions
resume — a `blocked` run already said why, and a forbidden-path run must not
continue. Every pre-flight stop still runs before a resume is considered.

The session is told to verify what the checkpoint claims before trusting it: a
checkpoint is a memo from a session that died. It records intent, not proof.

## Services

`[[service]]` entries render a `services.sh` with three hooks the runner calls
only when the file exists (default off = byte-identical behaviour):
`services_preflight` (read-only machine-reality checks — a service the machine
cannot provide skips the run **before** an item is claimed), `services_start`
(parent shell; exports connection env), and `services_stop` (post-run, always,
timeout runs included).

First provider: `postgres-ephemeral` — a throwaway cluster per run, loopback
plus a private socket, stale instances from hard-killed runs reclaimed at start.
Trust auth is documented as a single-user-laptop tradeoff.

## Reviewing the output

The `review-queue` skill walks the founder through each run's evidence — PRs,
reports, ledger — to a per-PR decision. Merges happen only on the founder's
explicit word. Acceptance rate (merged from GitHub ÷ opened from the ledger) is
the trust-ladder signal; demotion below 70%.

**One unattended run has shipped a PR.** On the platform's own cage, run
20260920-205434 stopped at a forbidden path with its work unpushed, and run
20260921-070026 opened PR #279, which the founder merged. The seven earlier
runs were on a consumer and none opened a PR; see
[Why This Exists](Why-This-Exists.md) for what they did.

---

Next: [Skills Policy](Skills-Policy.md) · [Configuration](Configuration.md) ·
[Cost and Throughput](Cost-and-Throughput.md)
