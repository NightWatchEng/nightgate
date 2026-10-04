#!/bin/bash
# Cage runner — the generic cage around a project's unattended dev loop.
#
# GENERATED ARTIFACT: `cage enroll` copies this file verbatim from the
# agentops platform (cage/run.sh) next to a rendered cage.env. Do not
# edit either by hand — edit cage.toml and re-run `cage enroll`.
# A byte-identical runner across projects is the point: the copy in any cage
# dir can be hash-compared against the platform's.
#
# Every HARD stop condition lives HERE, outside the model: the session that
# runs inside cannot edit this file (it lives outside the project worktree and
# the forbidden-path post-check catches any attempt to reach it via the repo).
#
# Modes:
#   run.sh            normal run — started by whatever trigger the config
#                     declares: launchd on the plist schedule, a human, or a
#                     CI event. Every stop below holds for all three.
#   run.sh --check    pre-flight only, report what would happen, no session,
#                     never consumes the run-now override
# Overrides for supervised testing (bypass QUIET HOURS ONCE, consumed):
#   touch ~/.cage-run-now            any enrolled project
#   touch ~/.cage-<name>-run-now     this project only
# Kill switches (skip everything until removed):
#   touch ~/.cage-off                every enrolled project
#   touch ~/.cage-<name>-off         this project only
set -u

# ---------- per-project parameters (rendered from cage.toml) -----------
CAGE_SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -f "$CAGE_SELF/cage.env" ]; then
  echo "FATAL: $CAGE_SELF/cage.env missing — re-run 'cage enroll'" >&2
  exit 2
fi
# `set -a` so every CAGE_* assignment is EXPORTED, not just set: the caged
# session is a child process, and platform code that keys off the caged
# environment (memory's run-provenance derivation first) reads os.environ.
# Without this the variables exist only in the runner's own shell and every
# such derivation silently no-ops.
set -a
. "$CAGE_SELF/cage.env"
set +a
# Runner/env version skew (a newer runner copied beside an older env) must
# fail loudly here, not as an unbound-variable abort halfway through.
for V in CAGE_PROJECT CAGE_GITHUB CAGE_HOME CAGE_LIVE_REPO CAGE_WORKTREE CAGE_DIR CAGE_LOG \
         CAGE_BRANCH_PREFIX CAGE_MAX_OPEN_PRS CAGE_TIMEOUT_SECS CAGE_QUIET_START \
         CAGE_QUIET_END CAGE_PLATFORM_PROBE CAGE_FORBIDDEN_RE CAGE_SCRUB_PATHS \
         CAGE_MAX_RESUMES CAGE_AUTONOMY_CARVE_OUT CAGE_CARVE_OUT_STORES \
         CAGE_TOOLCHAIN_PATH CAGE_REQUIRED_TOOLS; do
  if [ -z "${!V+x}" ]; then
    echo "FATAL: $V missing from cage.env (runner/env skew) — re-run 'cage enroll'" >&2
    exit 2
  fi
done

# ---------- fixed environment (launchd gives us almost nothing) --------------
export HOME="${HOME:-$CAGE_HOME}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# Declared toolchain directories, PREPENDED. The fixed list
# above is what makes a launchd-started run behave like a hand-started one,
# and that is exactly why a toolchain installed anywhere else is INVISIBLE
# inside the cage: Go's own installer puts go in /usr/local/go/bin, which is
# on no developer's mental list of "the normal places". The first completed
# run here spent an hour building and then blocked at ship step 1 because
# `go: command not found` made one cell of the repo's own test suite fail at
# every HEAD. A cage that cannot run the project's tests is not a smaller
# cage; it is one that can never ship. So the directories a project needs are
# DECLARED in its cage.toml and prepended here, before services.sh is sourced
# — an assignment after this point would wipe the keg-only dirs a service
# setup block prepends, which is the bug the ordering comment below records.
[ -n "$CAGE_TOOLCHAIN_PATH" ] && export PATH="$CAGE_TOOLCHAIN_PATH:$PATH"
# A cage that declares a toolchain also PROMISED it, and the session is told
# so. A project's suite has two honest answers to a tool it
# shells out to being absent, and which one is correct is decided by who
# contracted for the binary: on a contributor's laptop, which promised
# nothing, a named SKIP; in an environment that said it would supply the
# toolchain, a named FAILURE, because the coverage lost is coverage that was
# paid for. `AGENTOPS_REQUIRE_TOOLCHAIN` is the switch this platform's suite
# reads for that (tests/toolchain.py) and CI is its fallback.
# A cage sets neither by default, so it ran as the laptop — the one
# environment whose own toml declares the tools, running its project's tests
# in the half that forgives their absence. Two layers already bit and the gap
# was between them, which is worth being exact about: the pre-flight below
# stops the run on an absent entry of `[toolchain].require`, and a verify
# scope whose own command sets the switch — this repo's `warden verify --scope
# tests`, the caged ship step — was strict at every head. What was LENIENT is
# a bare `pytest` a caged session runs directly, on a tool the SUITE requires
# that this toml does not name: green skip, inside the cage, for the exact
# condition the declared toolchain exists for.
# Conditional because the promise is what makes strictness honest: a cage
# with no `[toolchain]` at all declared nothing and keeps the laptop's
# lenient half. Unconditional (an assignment, not a default) for a cage that
# did declare one, exactly like the PATH reset above: the run's environment
# is what this toml says it is, not what the shell that happened to start it
# carried — an ambient `AGENTOPS_REQUIRE_TOOLCHAIN=0` from a debugging
# session must not follow a founder-started run into the cage.
if [ -n "$CAGE_REQUIRED_TOOLS" ] || [ -n "$CAGE_TOOLCHAIN_PATH" ]; then
  export AGENTOPS_REQUIRE_TOOLCHAIN=1
fi
# Declared services (rendered from [[service]]) hook in via three functions;
# no services.sh means no services — the runner behaves exactly as without.
# Sourced AFTER the PATH reset above: the setup block prepends keg-only
# binary dirs, and an assignment later would wipe them.
[ -f "$CAGE_SELF/services.sh" ] && . "$CAGE_SELF/services.sh"
LIVE_REPO="$CAGE_LIVE_REPO"
WORKTREE="$CAGE_WORKTREE"
LEDGER="$CAGE_DIR/ledger.csv"
REPORTS="$CAGE_DIR/reports"
LOG="$CAGE_LOG"
SUMMARY="$WORKTREE/.run-summary"        # the session's handoff file (worktree-
                                          # side: the cage stays sealed to it)
PROGRESS="$WORKTREE/.run-progress"      # mid-run checkpoint the session writes
RESUME_IN="$WORKTREE/.run-resume"       # what the cage tells a resuming session
RESUME_STATE="$CAGE_DIR/resume-state"           # cage-side: survives the worktree reset
MAX_OPEN_PRS_CAP="$CAGE_MAX_OPEN_PRS"
TIMEOUT_SECS="$CAGE_TIMEOUT_SECS"           # wall clock, enforced by watchdog
CHECK_MODE=0
[ "${1:-}" = "--check" ] && CHECK_MODE=1

# ---------- reporting surface (defined before the run-id claim) --------------
# Defined above the run-identity block below, so a run-id claim that gives up
# (an unwritable $REPORTS: a stray chown, a read-only or full volume; or a
# 99-way same-second race) still reaches skip() instead of exiting silently.
# What skip() leaves depends on what is unwritable. A chown of $REPORTS alone,
# or the race, leaves $CAGE_DIR writable, so the ledger row is written and the
# notification fires. A read-only or full volume under $CAGE_DIR fails the
# ledger append as well, because $LEDGER sits in $CAGE_DIR beside $REPORTS:
# that error also goes to stderr, and only the notification gets through (the
# osascript banner, plus its NOTIFY line in $LOG when $LOG is on a writable
# volume, as the default under ~/Library/Logs usually is). No case gets a
# report: under an unwritable $REPORTS the report append fails (its error goes
# to stderr), and the race empties REPORT so the report line goes to /dev/null.
#
# What is NOT covered: the two `exit 2`s above (missing env file, runner/env
# skew) precede $LOG, $LEDGER and $CAGE_PROJECT, so stderr is all they can
# ever reach. The non-numeric quiet-hours bound further down also exits 2 on
# stderr alone; routing it through skip() would change that hard stop's exit
# contract.
notify() { # $1 title, $2 body — ALWAYS fires, silence is never ambiguous
  # quotes AND backslashes stripped: either would break the osascript literal
  local T="${1//\"/}" B="${2//\"/}"
  T="${T//\\/}"; B="${B//\\/}"
  osascript -e "display notification \"$B\" with title \"$T\"" 2>/dev/null || true
  echo "[$(date '+%F %T')] NOTIFY: $1 — $2" >> "$LOG"
}

ledger() { # outcome, bead, pr, note, node, duration_s, attempt — the file
  # is CSV with a header row, written once when the file is absent or empty
  # (a ledger that predates it stays headerless; both readers accept either).
  # Columns 6-9 are APPENDED so existing readers (which index 0-4) keep
  # working. duration and attempt make the trajectory measurable, not just
  # the outcome: "did runs get faster" and "how often do we need a second
  # pass" are questions the ledger could not answer before. run_id (column
  # 9, read from RUN_ID rather than passed) is what tells two rows written on
  # the SAME date apart, and names the branch, report and session log the
  # row came from.
  # ${RUN_ID:-unclaimed}, not $RUN_ID: these functions are defined before the
  # run identity exists, and under `set -u` a bare deref would abort the stop
  # mid-report — silently, which is the one thing this surface may not do.
  # The NOTE carries free text — `warden autonomy carve-out`'s own
  # multi-line stderr among it — so it is written as a quoted CSV field with
  # its quotes doubled, which a csv reader takes whole. Commas still become
  # ';' and newlines a space, so the row stays one physical line and a
  # `split(",")` or grep over the file still sees nine columns per run.
  # Outcome, bead and node are written UNQUOTED, and they too can come from
  # the session's .run-summary, so they lose any '"' as well as commas and
  # newlines: a stray quote there would open a field the csv reader closes
  # at the NEXT row's note, fusing two runs into one row.
  local note out bead node
  note=$(printf '%s' "${4:-}" | tr '\n,' ' ;')
  note="\"${note//\"/\"\"}\""
  out=$(printf '%s' "${1}" | tr -d '"\n,')
  bead=$(printf '%s' "${2:-none}" | tr -d '"\n,')
  node=$(printf '%s' "${5:-}" | tr -d '"\n,')
  [ -s "$LEDGER" ] || echo "date,bead,outcome,pr,note,node,duration_s,attempt,run_id" >> "$LEDGER"
  echo "$(date +%F),${bead},${out},${3:-},${note},${node},${6:-},${7:-1},${RUN_ID:-unclaimed}" >> "$LEDGER"
}

skip() { # $1 reason, $2 outcome (default skipped) — a skipped run still
  # reports, ledgers and notifies
  if [ "$CHECK_MODE" -eq 1 ]; then echo "pre-flight would SKIP: $1"; exit 0; fi
  # The report line is the one here that fails in the ordinary case: the
  # earliest callers run before a report file could be claimed at all. Its
  # error is left on stderr (launchd captures it). The ledger row below fails
  # too when $CAGE_DIR itself is read-only or full, with its error on stderr;
  # the notification does not write under $CAGE_DIR, so it is what keeps the
  # stop audible in every case.
  { echo "# Cage run ${RUN_ID:-unclaimed}"; echo; echo "SKIPPED: $1"; } >> "${REPORT:-/dev/null}"
  ledger "${2:-skipped}" "" "" "$1"
  notify "cage ($CAGE_PROJECT): ${2:-skipped}" "$1"
  exit 0
}

# ---------- run identity -----------------------------------------------------
# RUN_ID, not a date tag: a date-named branch would put a second run on the
# same day onto the first run's branch, report, session log and ledger row.
# RUN_ID is second-resolution and lexically sortable, so a later run always
# sorts after an earlier one, and every run owns its own evidence.
#
# Uniqueness is CLAIMED, never assumed: the report file is created under
# noclobber, so the winner of a same-second race keeps the bare stamp and the
# loser takes the next sequence suffix (_02.._99). Zero-padded, and '_' rather
# than '-': both sort AFTER the bare stamp and after the '.' that starts a
# file extension, so run ids and the file names built from them stay in the
# same order. --check claims nothing — it must not mutate state.
RUN_STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_ID="$RUN_STAMP"
REPORT="$REPORTS/$RUN_ID.md"   # provisional: skip() needs a path before the claim
mkdir -p "$REPORTS" "$(dirname "$LOG")"
# Checked, not assumed. An unusable reports dir means no run evidence can be
# written at all — exactly the condition that must not pass quietly.
[ -d "$REPORTS" ] && [ -w "$REPORTS" ] \
  || skip "reports dir not writable ($REPORTS) — no run evidence can be written"
if [ "$CHECK_MODE" -eq 0 ]; then
  RUN_SEQ=1
  until (set -o noclobber; : > "$REPORTS/$RUN_ID.md") 2>/dev/null; do
    RUN_SEQ=$((RUN_SEQ + 1))
    if [ "$RUN_SEQ" -gt 99 ]; then
      # Every name in this second is taken, so this is the one skip that
      # cannot own a report. The id must therefore be one the loop can NEVER
      # mint: reverting to the bare stamp would name run #1's report, branch
      # and session log (the stamp is the first name tried, so reaching here
      # proves it is claimed), and the run_id column exists precisely to stop
      # rows pointing at another run's evidence. '_unclaimed' still sorts
      # after '_99', so ordering holds. REPORT is emptied so skip() writes to
      # /dev/null rather than appending into a claimed report.
      RUN_ID="${RUN_STAMP}_unclaimed"; REPORT=""
      skip "cannot claim a run id under $REPORTS (99 tried from $RUN_STAMP)"
    fi
    RUN_ID="${RUN_STAMP}_$(printf '%02d' "$RUN_SEQ")"
  done
fi
REPORT="$REPORTS/$RUN_ID.md"
RUNLOG="$REPORTS/$RUN_ID.session.log"     # per-run: the usage-limit classifier
                                          # must never read another run's text
BRANCH="${CAGE_BRANCH_PREFIX}${RUN_ID}"

# ---------- pre-flight -------------------------------------------------------
# 1. Kill switches (honored even with the window override)
[ -f "$HOME/.cage-off" ] && skip "kill switch (~/.cage-off) present"
[ -f "$HOME/.cage-$CAGE_PROJECT-off" ] && skip "kill switch (~/.cage-$CAGE_PROJECT-off) present"

# 2. Quiet hours — do NOT run while a human may be working. launchd fires
#    missed jobs ON WAKE, so a sleeping Mac would otherwise run the loop into
#    the founder's session (wake-coalesced fire); the same collision is worth
#    refusing whatever started the run. The interval is read forward from
#    start to end and may cross midnight (5->23 does not, 23->5 does); an
#    empty interval (start == end) declares no quiet hours at all. Mirrors
#    cage.config.in_quiet_hours() exactly.
#    Non-numeric bounds FAIL CLOSED as a fatal env error rather than
#    evaluating to "not quiet" — a hard stop that cannot be read is not a
#    hard stop that passes.
for V in CAGE_QUIET_START CAGE_QUIET_END; do
  case "${!V}" in
    ''|*[!0-9]*)
      echo "FATAL: $V='${!V}' is not an hour (0-23) — re-run 'cage enroll'" >&2
      exit 2 ;;
  esac
done
HOUR=$((10#$(date +%H)))
QUIET=0
if [ "$CAGE_QUIET_START" -lt "$CAGE_QUIET_END" ]; then
  [ "$HOUR" -ge "$CAGE_QUIET_START" ] && [ "$HOUR" -lt "$CAGE_QUIET_END" ] && QUIET=1
elif [ "$CAGE_QUIET_START" -gt "$CAGE_QUIET_END" ]; then
  { [ "$HOUR" -ge "$CAGE_QUIET_START" ] || [ "$HOUR" -lt "$CAGE_QUIET_END" ]; } && QUIET=1
fi
if [ "$QUIET" -eq 1 ]; then
  # The one-shot overrides exist ONLY to bypass this guard, so they are read
  # here and nowhere else. --check never consumes one.
  RUN_NOW=""
  [ -f "$HOME/.cage-run-now" ] && RUN_NOW="$HOME/.cage-run-now"
  [ -f "$HOME/.cage-$CAGE_PROJECT-run-now" ] && RUN_NOW="$HOME/.cage-$CAGE_PROJECT-run-now"
  if [ -n "$RUN_NOW" ]; then
    if [ "$CHECK_MODE" -eq 0 ]; then
      rm -f "$RUN_NOW"
      echo "[quiet-hours override consumed: $RUN_NOW]" >> "$LOG"
    fi
  else
    skip "quiet hours $CAGE_QUIET_START:00-$CAGE_QUIET_END:00 — a human may be working (wake-coalesced fire?)"
  fi
fi

# 3. Tooling + network + gh auth + GitHub reachable
command -v claude >/dev/null 2>&1 || skip "claude CLI not on PATH"
command -v gh >/dev/null 2>&1 || skip "gh CLI not on PATH"
# The post-run outcome classifier parses the session's result event with it.
python3 -c 'import json, os, re' >/dev/null 2>&1 || skip "python3 cannot run — the outcome classifier needs it"
# The project's DECLARED toolchain, checked before a bead is claimed. This is
# the stop that matters: the alternative is not "a run without go", it
# is a run that works for an hour and then finds its own verify scope red at
# ship step 1, every time, with nothing the session can do about it. A missing
# toolchain is an environment fact, so it belongs to the same pre-flight as a
# missing gh or an unreachable platform — named, ledgered, and cheap.
#
# Deliberately NOT a reason to weaken the test that needs it. The corpus has
# already ruled on this shape in tests/test_commit_lint.py, and
# the ruling has TWO halves: a REQUIRED tool absent is a RED suite and a
# `shutil.which` filter over it is forbidden; an OPTIONAL one absent is a skip
# whose reason names the gap. This list is where a project declares which of
# its tools are in the first class — every name in it takes the same hard skip.
# What puts a tool in that first class is the CONTRACT, not the tool:
# the same `go` absent on a laptop that promised nothing is a
# named skip, and a `shutil.which` filter there is the honest report rather
# than the forbidden one. Declaring the list is the promise, which is why the
# strict switch is exported above for a cage that carries one.
# For the platform's own cage `go` is the load-bearing one: the only executable
# proof its enrollment surface works for a language it is not written in. A
# filter over one of THOSE turns a scope green on a run that proved nothing.
for TOOL in $CAGE_REQUIRED_TOOLS; do
  command -v "$TOOL" >/dev/null 2>&1 || skip "required tool '$TOOL' is not on the caged PATH ($PATH) — declare its directory in [toolchain].path and re-enroll, or install it"
done
[ -f "$CAGE_DIR/prompt.md" ] || skip "prompt.md missing in cage dir — the session prompt is hand-authored"
# The profile's push rules name the run branch through a token this runner
# replaces; a profile without it grants no run-branch push at all.
grep -qF '__CAGE_RUN_BRANCH__' "$CAGE_DIR/cage-profile.json" 2>/dev/null \
  || skip "cage-profile.json does not bind the run branch (runner/profile skew) — re-run 'cage enroll'"
# The prompt hard-invokes a skill by name; a name the installed pack no
# longer serves (a rename, an uninstall) starts a session with NO protocol
# layer — only runner-side stops remain. Best-effort with the blind spot said
# out loud: no
# plugin cache on this machine means we cannot verify either way, and a
# silent pass here would be the vacuous check this platform's corpus warns
# about, so the log says so.
PROMPT_SKILL=$(grep -oE 'agentops-skills:[a-z][a-z0-9-]*' "$CAGE_DIR/prompt.md" | head -1 || true)
if [ -n "$PROMPT_SKILL" ]; then
  SKILL_NAME="${PROMPT_SKILL#agentops-skills:}"
  PLUGIN_CACHE="$HOME/.claude/plugins/cache"
  if [ -d "$PLUGIN_CACHE" ]; then
    # Scope to the NEWEST installed pack version. The cache keeps retired
    # versions beside current ones, so a cache-wide search resolves a skill
    # only the retired version serves — pre-flight goes green while Claude
    # loads the current version and the skill never loads, which is the
    # fail-open this check exists to close.
    PACK_DIR=$(find "$PLUGIN_CACHE" -type d -path "*/agentops-skills/*/skills" 2>/dev/null \
               | sort -V | tail -1)
    if [ -n "$PACK_DIR" ]; then
      [ -d "$PACK_DIR/$SKILL_NAME" ] || skip "prompt invokes $PROMPT_SKILL but the installed pack does not serve it (checked $PACK_DIR) — update the marketplace or re-enroll before running unattended"
    else
      echo "[skill resolution unverifiable (no agentops-skills pack under $PLUGIN_CACHE) — $PROMPT_SKILL taken on faith]" >> "$LOG"
    fi
  else
    echo "[skill resolution unverifiable (no plugin cache at $PLUGIN_CACHE) — $PROMPT_SKILL taken on faith]" >> "$LOG"
  fi
fi
# Machine-reality check for declared services (read-only): a service the
# machine can't actually provide must skip the run here, not die mid-run.
SVC_STATE="none"
if declare -F services_preflight >/dev/null; then
  SVC_MSG=$(services_preflight) || skip "service pre-flight: $SVC_MSG"
  SVC_STATE="preflight-ok"
fi
# Platform reachability: a pinned-shim consumer resolves the platform via
# uvx/ssh — the headless launchd env must prove it BEFORE a bead is claimed,
# or the whole gauntlet dies mid-run. Empty CAGE_PLATFORM_PROBE disables.
if [ -n "$CAGE_PLATFORM_PROBE" ]; then
  (cd "$LIVE_REPO" && eval "$CAGE_PLATFORM_PROBE") >/dev/null 2>&1 \
    || skip "platform probe failed ($CAGE_PLATFORM_PROBE) — check ssh identity / uv cache"
fi
gh auth status >/dev/null 2>&1 || skip "gh auth unavailable headless"
gh api -X GET rate_limit >/dev/null 2>&1 || skip "GitHub API unreachable"

# 4. Back-pressure: unreviewed automated PRs cap the loop
OPEN_AUTO_PRS=$(gh pr list --repo "$CAGE_GITHUB" --state open --json headRefName \
  -q "[.[] | select(.headRefName|startswith(\"$CAGE_BRANCH_PREFIX\"))] | length" 2>/dev/null || echo 99)
[ "$OPEN_AUTO_PRS" -ge "$MAX_OPEN_PRS_CAP" ] && skip "$OPEN_AUTO_PRS ${CAGE_BRANCH_PREFIX}* PRs already open — review them first"

# 5. Main must be green — never build on a red base
MAIN_STATE=$(gh run list --repo "$CAGE_GITHUB" --branch main --limit 1 \
  --json conclusion -q '.[0].conclusion' 2>/dev/null || echo unknown)
[ "$MAIN_STATE" = "success" ] || skip "main CI is '$MAIN_STATE', not success"

# Resume DETECTION is read-only, so --check can report it honestly. The
# mutations it implies (clearing a spent budget, notifying) happen after the
# check-mode exit, never during it.
RESUME_BEAD=""; RESUME_ATTEMPT=0; RESUME_BRANCH=""; RESUME_SPENT=0
if [ -f "$RESUME_STATE" ]; then
  RESUME_BEAD=$(grep -m1 '^bead=' "$RESUME_STATE" | cut -d= -f2-)
  RESUME_BRANCH=$(grep -m1 '^branch=' "$RESUME_STATE" | cut -d= -f2-)
  RESUME_ATTEMPT=$(grep -m1 '^attempt=' "$RESUME_STATE" | cut -d= -f2-)
  RESUME_ATTEMPT=${RESUME_ATTEMPT:-0}
  [ "$RESUME_ATTEMPT" -ge "$CAGE_MAX_RESUMES" ] && RESUME_SPENT=1
fi
RESUME_NOTE="no resume pending"
if [ -n "$RESUME_BEAD" ] && [ "$RESUME_SPENT" -eq 1 ]; then
  RESUME_NOTE="resume budget SPENT for $RESUME_BEAD ($RESUME_ATTEMPT attempts) — would return it to the founder"
elif [ -n "$RESUME_BEAD" ]; then
  RESUME_NOTE="would RESUME $RESUME_BEAD on $RESUME_BRANCH (attempt $((RESUME_ATTEMPT + 1))/$CAGE_MAX_RESUMES), worktree preserved"
fi

# The session may push exactly the run branch. A resume state naming a branch
# outside the prefix (a changed prefix, or a tampered state), or any name with
# a character that could escape the JSON or the sed below, is refused here,
# where --check reports it too; the state is cleared so it cannot refuse every
# later run.
RUN_BRANCH="$BRANCH"
[ -n "$RESUME_BEAD" ] && [ "$RESUME_SPENT" -eq 0 ] && [ -n "$RESUME_BRANCH" ] \
  && RUN_BRANCH="$RESUME_BRANCH"
case "$RUN_BRANCH" in
  *[!A-Za-z0-9/_-]*|"$CAGE_BRANCH_PREFIX") BRANCH_OK=0 ;;
  "$CAGE_BRANCH_PREFIX"*) BRANCH_OK=1 ;;
  *) BRANCH_OK=0 ;;
esac
if [ "$BRANCH_OK" -ne 1 ]; then
  [ "$CHECK_MODE" -eq 0 ] && rm -f "$RESUME_STATE"
  skip "run branch '$RUN_BRANCH' is not an $CAGE_BRANCH_PREFIX run branch — no push can be granted for it (prefix changed or resume state tampered; resume state cleared)"
fi

# --check stops HERE: everything past this point mutates state (fetch,
# worktree reset, clean, scrub) and "report what would happen" must not.
if [ "$CHECK_MODE" -eq 1 ]; then
  echo "pre-flight OK: quiet hours ok, no kill switch, claude+gh+python3+prompt ok, services=$SVC_STATE, $OPEN_AUTO_PRS open ${CAGE_BRANCH_PREFIX}* PRs, main=$MAIN_STATE; $RESUME_NOTE; usage not probed (the probe is a session, and --check starts none)"
  exit 0
fi

# 6. Usage — never start a session the usage limit will kill. Claude Code
#    documents no non-interactive reading of remaining usage: the statusline's
#    rate_limits percentages exist only inside an interactive session, after its
#    first API response. So the check is a minimal probe session (no tools, no
#    saved transcript) whose final stream-json result event is read the way the
#    outcome classifier reads a session's: a top-level `"is_error":true` with
#    `"api_error_status":429` is EXHAUSTED, a clean result from a probe that
#    exited 0 is USABLE, and anything else, including a probe that fails, hangs
#    past its timeout, or prints no parseable result, is UNREADABLE and skips
#    exactly like exhausted. The threshold is therefore binary: usable or not.
#    A usable probe says nothing about how much is left, so a run can still
#    reach the limit mid-session; the resume path above covers that. After the
#    --check exit, because the probe spends a turn, and before the worktree
#    is touched, so a skip leaves a pending resume exactly as it was.
USAGE_PROBE_LOG="$REPORTS/$RUN_ID.usage-probe.log"
usage_probe() { # $1 probe log, $2 timeout secs -> "usable|exhausted|unreadable" TAB detail
  (cd "$CAGE_DIR" && python3 - "$1" "$2") <<'PY'
import json, os, signal, subprocess, sys

log_path, timeout = sys.argv[1], int(sys.argv[2])


def say(verdict, detail=""):
    print(verdict + "\t" + " ".join(str(detail).split()))
    sys.exit(0)


# --safe-mode: no CLAUDE.md, hooks, plugins or MCP servers, so the probe costs a
# bare turn and nothing slow can hold it to its timeout; auth still works.
# Never --bare: it reads only ANTHROPIC_API_KEY, so a subscription login would
# read as unreadable every time.
command = ["claude", "-p", "cage usage probe: reply with the single word ok",
           "--safe-mode", "--tools", "", "--no-session-persistence",
           "--output-format", "stream-json", "--verbose"]
try:
    with open(log_path, "wb") as log:
        # Its own process group, so a hung probe is killed with its children.
        probe = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = probe.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(probe.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            probe.wait()
            say("unreadable", f"the probe did not finish in {timeout}s")
except OSError as exc:
    say("unreadable", f"the probe could not run: {exc}")

result = None
with open(log_path, encoding="utf-8", errors="replace") as log:
    for line in log:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            result = event
if result is None:
    say("unreadable", f"no result event from the probe (exit {code})")
status = result.get("api_error_status")
if result.get("is_error") is True and type(status) is int and status == 429:
    text = result.get("result")
    say("exhausted", text if isinstance(text, str) and text.strip() else "429 from the probe")
if result.get("is_error") is False and code == 0:
    say("usable")
say("unreadable", f"probe result is_error={result.get('is_error')!r} "
                  f"api_error_status={status!r}, exit {code}")
PY
}
# USAGE_PROBE_TIMEOUT is a runner default, not a cage.toml parameter (so not
# CAGE_*): an environment or cage.env line may shorten it, and the tests do.
USAGE=$(usage_probe "$USAGE_PROBE_LOG" "${USAGE_PROBE_TIMEOUT:-300}" 2>>"$LOG") \
  || USAGE="unreadable"$'\t'"the probe's parser failed (exit $?)"
USAGE_DETAIL="${USAGE#*$'\t'}"
case "${USAGE%%$'\t'*}" in
  usable) ;;
  exhausted) skip "usage exhausted — $USAGE_DETAIL (probe log: $USAGE_PROBE_LOG)" usage-skipped ;;
  *) skip "usage unreadable — ${USAGE_DETAIL:-no verdict}; failing closed (probe log: $USAGE_PROBE_LOG)" usage-skipped ;;
esac

# 7. Dedicated worktree — the live checkout (and anything reading it) is NEVER
#    touched. Inner-repo worktree is allowed.
#
#    A run interrupted mid-bead (timeout / usage limit) leaves a resume
#    state. Continuing it beats both alternatives: restarting throws away
#    real work, and the old behavior — marking the bead auto-attempted and
#    skipping it forever — ABANDONED it. Resuming is budgeted, and every
#    hard stop above still ran before we got here.
if [ -n "$RESUME_BEAD" ] && [ "$RESUME_SPENT" -eq 1 ]; then
  rm -f "$RESUME_STATE"
  notify "cage ($CAGE_PROJECT): resume budget spent" \
    "bead $RESUME_BEAD needs a human — $RESUME_ATTEMPT attempt(s)"
  RESUME_BEAD=""
elif [ -n "$RESUME_BEAD" ] && [ -n "$RESUME_BRANCH" ]; then
  BRANCH="$RESUME_BRANCH"      # continue the same branch, same PR
fi
RUN_PROFILE="$REPORTS/$RUN_ID.profile.json"
sed "s|__CAGE_RUN_BRANCH__|$BRANCH|g" "$CAGE_DIR/cage-profile.json" > "$RUN_PROFILE" \
  || skip "cannot write the run's permission profile ($RUN_PROFILE)"

git -C "$LIVE_REPO" fetch origin --quiet || skip "git fetch failed"
if [ -n "$RESUME_BEAD" ] && [ -d "$WORKTREE" ]; then
  # continuation: keep the work, do NOT reset to origin/main
  git -C "$WORKTREE" checkout "$BRANCH" --quiet || skip "resume branch $BRANCH missing"
elif [ ! -d "$WORKTREE" ]; then
  # -B: a fresh run's RUN_ID branch never exists yet, so this only matters
  # for a resume whose worktree DIR was removed while its branch survived.
  git -C "$LIVE_REPO" worktree add "$WORKTREE" --no-track -B "$BRANCH" origin/main --quiet \
    || skip "worktree create failed"
else
  git -C "$WORKTREE" checkout --no-track -B "$BRANCH" origin/main --quiet || skip "worktree branch reset failed"
  git -C "$WORKTREE" clean -fdq
fi
# Host artifacts must never leak into the worktree (per-project scrub list)
for SCRUB in $CAGE_SCRUB_PATHS; do
  [ -e "$WORKTREE/$SCRUB" ] && rm -rf "$WORKTREE/${SCRUB:?}"
done
rm -f "$SUMMARY"
rm -f "$RESUME_IN"
if [ -n "$RESUME_BEAD" ]; then
  # worktree-side handoff — the cage stays sealed to the session
  { echo "bead: $RESUME_BEAD"; echo "attempt: $((RESUME_ATTEMPT + 1))";
    echo "branch: $BRANCH";
    echo "max_attempts: $CAGE_MAX_RESUMES"; } > "$RESUME_IN"
  [ -f "$PROGRESS" ] && { echo "---"; cat "$PROGRESS"; } >> "$RESUME_IN"
fi
rm -f "$PROGRESS"

# ---------- the session ------------------------------------------------------
{ echo "# Cage run $RUN_ID"; echo; echo "- branch: $BRANCH"; echo "- start: $(date '+%F %T')"; } >> "$REPORT"

cd "$WORKTREE" || skip "cannot cd into worktree"
# Services come up last, exporting connection env (e.g. CAGE_DATABASE_URL)
# the session inherits. Runs in THIS shell — a subshell would lose the exports.
if declare -F services_start >/dev/null; then
  if ! services_start >> "$LOG" 2>&1; then
    services_stop >> "$LOG" 2>&1 || true
    skip "service start failed — see $LOG"
  fi
fi
# Truncate, not append: RUN_ID already gives this run its own log path, and
# '>' keeps that true even if an id were ever reused — the classifier below
# must only ever read THIS run's text.
# The profile matches literal text, so the session is told the exact spellings.
PUSH_REF="$BRANCH:refs/heads/$BRANCH"
PUSH_NOTE="This run's branch is $BRANCH. The permission profile allows exactly one push, typed literally as one of: git push -u origin $PUSH_REF | git push origin $PUSH_REF | git push --set-upstream origin $PUSH_REF. Every other push is refused."
claude -p "$(cat "$CAGE_DIR/prompt.md"; printf '\n%s\n' "$PUSH_NOTE")" \
  --settings "$RUN_PROFILE" \
  --output-format stream-json --verbose > "$RUNLOG" 2>&1 &
CPID=$!

# Pure-bash watchdog: setsid/timeout don't exist on stock macOS.
WAITED=0
while kill -0 "$CPID" 2>/dev/null && [ "$WAITED" -lt "$TIMEOUT_SECS" ]; do
  sleep 30; WAITED=$((WAITED + 30))
done
TIMED_OUT=0
if kill -0 "$CPID" 2>/dev/null; then
  TIMED_OUT=1
  pkill -TERM -P "$CPID" 2>/dev/null; kill -TERM "$CPID" 2>/dev/null
  GRACE=0
  while kill -0 "$CPID" 2>/dev/null && [ "$GRACE" -lt 300 ]; do sleep 15; GRACE=$((GRACE + 15)); done
  kill -0 "$CPID" 2>/dev/null && { pkill -KILL -P "$CPID" 2>/dev/null; kill -KILL "$CPID" 2>/dev/null; }
fi
wait "$CPID" 2>/dev/null; CLAUDE_EXIT=$?
DURATION_S="$WAITED"

# ---------- post-run (always; no errexit here) -------------------------------
# Services die with the session — timeout and forbidden-path runs included.
if declare -F services_stop >/dev/null; then
  services_stop >> "$LOG" 2>&1 || true
fi
# Classified from the session's final stream-json `result` event, parsed as
# JSON, never from words: the injected prompt says "usage limit", and every
# run echoes it. A session limit is the event's own top-level
# `"is_error":true` with `"api_error_status":429`; a denied tool's input is a
# nested object whose keys the session chooses, so it is never read for them.
# The denied push is the first Bash denial whose command, split into shell
# words, runs `git ... push` in command position (after a separator, `{`,
# `then`, `env`/VAR=, a redirect and the like, skipping git's -C/-c options,
# and through a `-c alias.<name>=push`); a push inside a quoted string is one
# word, and heredoc bodies and `#` comments are not commands. Still not found:
# a push handed to another program as an argument (bash -c, eval, xargs,
# sudo), inside a double-quoted $(...) or an unquoted heredoc's $(...), in a
# heredoc body piped to a shell, or through a shell (`!`) alias, an alias
# defined in git config or one read from the environment by --config-env. A
# command with an unbalanced quote is matched loosely. The note keeps the
# command's JSON escapes.
# Per-run log: another run's event can't lie here.
classify_result() { # $1 session log -> "usage-limit|-" TAB denied push
  python3 - "$1" <<'PY'
import json, os, re, sys

OPS = ";&|()`<>"
OPERATORS = sorted(["&>>", ";;&", "<<<", "<<", ">>", "&&", "||", ";;", ";&",
                    "|&", "&>", ">&", "<&", "<>", ">|", ";", "&", "|", "(",
                    ")", "`", "<", ">"], key=len, reverse=True)
KEYWORDS = {"{", "!", "if", "then", "else", "elif", "do", "while", "until",
            "time", "command", "exec", "nohup"}
GIT_OPTS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                     "--config-env"}
ENV_OPTS_WITH_ARG = {"-u", "--unset", "-C", "--chdir"}
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
PUSH_ALIAS = re.compile(r"(?i:alias)\.([^=]+)=\s*push(\s.*)?", re.S)


def tokens(text):
    """("op", run) and ("word", text) pairs, without comments or heredoc
    bodies. Raises ValueError on an unbalanced quote."""
    out, word, in_word, quoted, heredocs, delim_strip = [], [], False, False, [], None
    i, n, arith, bracket, dollar = 0, len(text), 0, 0, False

    def end_word():
        nonlocal word, in_word, quoted, delim_strip
        if in_word:
            value = "".join(word)
            if delim_strip is not None:
                heredocs.append((value, delim_strip))
                delim_strip = None
            out.append(("word", value))
        word, in_word, quoted = [], False, False

    while i < n:
        c = text[i]
        # Did the previous character start an expansion: an unquoted,
        # unescaped `$` that is not the second half of `$$`? Only that `$`
        # makes a following `[` arithmetic; `\$[`, `"$"[`, `'$'[` and `$$[`
        # are a literal `[`.
        after_dollar, dollar = dollar, False
        if c == "\\":
            if text[i + 1:i + 2] != "\n":
                word.append(text[i + 1:i + 2])
                in_word = quoted = True
            else:
                dollar = after_dollar   # a line continuation joins `$` to `[`
            i += 2
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise ValueError("unbalanced '")
            word.append(text[i + 1:j])
            in_word = quoted = True
            i = j + 1
        elif c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and text[i + 1:i + 2] in ('$', '`', '"', '\\', '\n'):
                    word.append(text[i + 1] if text[i + 1] != "\n" else "")
                    i += 2
                else:
                    word.append(text[i])
                    i += 1
            if i >= n:
                raise ValueError('unbalanced "')
            in_word = quoted = True
            i += 1
        elif c in " \t\r":
            end_word()
            i += 1
        elif c == "#" and not in_word:
            while i < n and text[i] != "\n":
                i += 1
        elif c == "\n":
            end_word()
            out.append(("op", c))
            i += 1
            for delim, strip in heredocs:
                while i < n:
                    j = text.find("\n", i)
                    j = n if j < 0 else j
                    line, i = text[i:j], j + 1
                    line = line.rstrip("\r")
                    if (line.lstrip("\t") if strip else line) == delim:
                        break
            heredocs.clear()
        elif c in OPS:
            op = next(o for o in OPERATORS if text.startswith(o, i))
            # $((, (( and $[ are arithmetic: a << there shifts, not a heredoc.
            if c == "(" and (arith or text.startswith("((", i)
                             and (not in_word or word[-1:] == ["$"])):
                arith += 1
            elif c == ")" and arith:
                arith -= 1
            if c in "<>" and in_word and not quoted and "".join(word).isdigit():
                word, in_word = [], False
            end_word()
            i += len(op)
            if op == "<<" and not arith and not bracket:
                delim_strip = text[i:i + 1] == "-"
                i += delim_strip
            out.append(("op", op))
        else:
            if c == "[" and (bracket or after_dollar):
                bracket += 1
            elif c == "]" and bracket:
                bracket -= 1
            dollar = c == "$" and not after_dollar
            word.append(c)
            in_word = True
            i += 1
    end_word()
    return out


def is_git_push(command):
    try:
        toks = tokens(command)
    except ValueError:
        return re.search(r"\bgit\b.*\bpush\b", command.replace("\\\n", "")) is not None

    def word_at(k):
        return toks[k][1] if k < len(toks) and toks[k][0] == "word" else None

    start, i = True, 0
    while i < len(toks):
        kind, word = toks[i]
        i += 1
        if kind == "op":
            if "<" in word or ">" in word:
                i += word_at(i) is not None
            else:
                start = True
            continue
        if not start or word in KEYWORDS or ASSIGN.match(word):
            continue
        if word == "env":
            while word_at(i) is not None and (word_at(i).startswith("-") or ASSIGN.match(word_at(i))):
                i += 2 if word_at(i) in ENV_OPTS_WITH_ARG else 1
            continue
        start = False
        if os.path.basename(word) == "git":
            aliases = set()
            while word_at(i) is not None and word_at(i).startswith("-"):
                opt = word_at(i)
                value = (word_at(i + 1) or "") if opt == "-c" else opt[2:] if opt.startswith("-c") else ""
                alias = PUSH_ALIAS.fullmatch(value)
                if alias:
                    aliases.add(alias.group(1).lower())
                i += 2 if opt in GIT_OPTS_WITH_ARG else 1
            sub = word_at(i)
            if sub is not None and (sub == "push" or sub.lower() in aliases):
                return True
    return False


result = None
with open(sys.argv[1], encoding="utf-8", errors="replace") as log:
    for line in log:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            result = event
result = result or {}
status = result.get("api_error_status")
limit = result.get("is_error") is True and type(status) is int and status == 429
push = ""
denials = result.get("permission_denials")
for denial in denials if isinstance(denials, list) else []:
    if not isinstance(denial, dict) or denial.get("tool_name") != "Bash":
        continue
    tool_input = denial.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if isinstance(command, str) and is_git_push(command):
        push = json.dumps(command, ensure_ascii=False)[1:-1]
        break
print(("usage-limit" if limit else "-") + "\t" + push)
PY
}
CLASSIFY_NOTE=""
if CLASSIFIED=$(classify_result "$RUNLOG" 2>>"$LOG"); then
  RESULT_FLAG="${CLASSIFIED%%$'\t'*}"; PUSH_DENIED="${CLASSIFIED#*$'\t'}"
else
  RESULT_FLAG="-"; PUSH_DENIED=""
  CLASSIFY_NOTE="outcome classifier FAILED — usage-limit and push-denied not evaluated; see $LOG"
fi
OUTCOME="done"
if [ "$TIMED_OUT" -eq 1 ]; then
  OUTCOME="timeout"
elif [ "$RESULT_FLAG" = "usage-limit" ]; then
  OUTCOME="usage-limit"
elif [ "$CLAUDE_EXIT" -ne 0 ]; then
  OUTCOME="failed($CLAUDE_EXIT)"
fi

# Forbidden-path check — gate-surface freeze, outside the model. The regex is
# rendered from cage.toml [gate].forbidden_paths; empty disables (enroll
# validates each pattern against grep -E and warns loudly on empty).
VIOLATION=""
GREP_EXIT=2   # fail-closed default: "not evaluated" is never "evaluated clean"
if [ -n "$CAGE_FORBIDDEN_RE" ] && git -C "$WORKTREE" rev-parse "origin/main" >/dev/null 2>&1; then
  # git's own status is checked: `rev-parse origin/main` succeeding does not
  # mean `A...B` resolves (unrelated histories after a force-push, a shallow
  # fetch with no merge base, a locked index). Unchecked, git errored, CHANGED
  # came back EMPTY, grep exited 1, and the whole gate-surface freeze reported
  # clean without ever having evaluated — the pass-shaped failure the exit>=2
  # arm below already refuses for grep.
  #
  # `-z`, NUL-terminated, then NUL -> newline. `--name-only` alone C-QUOTES a
  # path holding a non-ASCII byte under the default core.quotepath, and one
  # holding a double quote, a backslash or a control character whatever that
  # setting says — wrapped in double quotes, so the `^(` anchor every rendered
  # pattern carries never matched it, and a session that named ONE file under
  # a fenced directory with one such character would pass the freeze. A bash
  # variable cannot hold NUL, hence the `tr`; `pipefail`
  # in the substitution's own subshell keeps DIFF_EXIT git's, so a failed diff
  # still takes the fail-closed arm below and never reads as an empty, clean
  # listing. A path holding a NEWLINE would be split by that translation into
  # two lines, and an END-anchored fence (`\.warden/rules/.*\.md$` is an
  # ordinary consumer spelling) matches neither half, so the freeze would read
  # CLEAN over `.warden/rules/x<newline>y.md`. So the newline
  # is kept distinguishable instead: `tr` maps NUL to newline and newline to
  # \001, and a listing carrying \001 is a listing the anchored regex cannot
  # evaluate — the freeze fails CLOSED on it, like a diff that fails. A name
  # that itself holds \001 trips the same arm, which over-closes, the one
  # direction a hard stop may err in.
  CHANGED=$(set -o pipefail; git -C "$WORKTREE" diff origin/main...HEAD --name-only -z 2>&1 | tr '\000\012' '\012\001')
  DIFF_EXIT=$?
  if [ "$DIFF_EXIT" -ne 0 ]; then
    VIOLATION="(forbidden-path diff failed: git exit $DIFF_EXIT — failing closed; the freeze could not be evaluated)"
  elif printf '%s\n' "$CHANGED" | grep -q "$(printf '\001')"; then
    VIOLATION="(forbidden-path listing holds a path with a newline in its name — failing closed; the anchored regex cannot evaluate a split path)"
  else
    VIOLATION=$(printf '%s\n' "$CHANGED" | grep -E "$CAGE_FORBIDDEN_RE")
    GREP_EXIT=$?
    # exit 1 = clean; >=2 = the regex failed to evaluate — FAIL CLOSED: a hard
    # stop that errors must behave like a hit, never like a pass.
    [ "$GREP_EXIT" -eq 1 ] && VIOLATION=""
    [ "$GREP_EXIT" -ge 2 ] && VIOLATION="(forbidden-path regex failed to evaluate: grep exit $GREP_EXIT — failing closed; check [gate].forbidden_paths)"
  fi
fi

# The consumer's SCOPE on the carve-out grant. `warden
# autonomy carve-out` decides SHAPE — is this diff the change the machine
# tier is permitted to make — and that definition lives on the platform, so
# it moves when the pin moves: adding the attestation store took it from three admitted
# .warden/ writes to four, and every granted consumer got the fourth by
# re-pinning, with no new grant and nothing in their own cage.toml to read.
#
# So the grant may name its own stores, and this is where the naming BITES:
# every forbidden path the carve-out cleared must also sit under a prefix the
# consumer wrote down. Two authorities, AND-ed, and this one can only ever
# NARROW — a platform that grows a fifth store writes outside every granted
# prefix and stays refused until a human edits the toml.
#
# EMPTY means unscoped: a bare `autonomy_carve_out = true`, the form the flag
# shipped with, which reads as "whatever the pinned platform admits". That is
# the widening the bead was filed for, and it is kept because a consumer
# config written against the bool must keep loading; the list is the fix they
# migrate to on their own schedule.
stores_admit() {   # $1 = newline-separated paths; 0 = every one is in scope
  [ -z "$CAGE_CARVE_OUT_STORES" ] && return 0
  local PATH_IN STORE HIT
  while IFS= read -r PATH_IN; do
    [ -z "$PATH_IN" ] && continue
    HIT=0
    for STORE in $CAGE_CARVE_OUT_STORES; do
      case "$PATH_IN" in "$STORE"*) HIT=1; break ;; esac
    done
    if [ "$HIT" -eq 0 ]; then
      OUT_OF_SCOPE="$PATH_IN"
      return 1
    fi
  done <<< "$1"
  return 0
}

# Autonomy-ladder carve-out. The ladder's machine tier MAY
# write exactly four things under the otherwise-forbidden .warden/ — a pause
# field in an existing rule file, a new NON-BLOCKING rule file, the
# backtest artifact that change carries, and the attestation shard a review
# round leaves under .warden/memory/attest/ — via a PR a human
# merges. The regex
# above cannot tell those from a session rewriting the gate, so without this
# step it would close the PR for all of them. The fourth is why a run can ship
# at all: `warden gate` refuses a PR carrying no committed shard, and the
# freeze refused the shard, so every protocol-following run closed its own PR.
#
# OFF UNLESS THIS PROJECT GRANTED IT. `[gate].autonomy_carve_out` defaults to
# false in cage.toml, so a repo that never granted it keeps its forbidden-path
# stop whole however new the platform gets.
#
# THE GRANT MAY NOW PIN ITS OWN SCOPE. Written as a bare
# bool it still does not: what `true` admits is whatever the PINNED platform
# admits, and adding the attestation store widened that from three .warden/ writes to four,
# so a consumer who granted it against three got the fourth by re-pinning.
# This comment used to claim the opposite ("enrolling a newer platform never
# weakens a hard stop a project already declared"), which was false for a
# granted consumer on exactly that commit. Written as a LIST of store
# prefixes it does pin: `stores_admit` above refuses to clear anything
# outside them, so a fifth store the platform grows stays stopped until a
# human adds it here. Setting the flag false restores the whole stop.
#
# Provenance is decided by SHAPE, never by a marker: anything a session can
# write proves nothing, while a diff shaped like the one `warden autonomy`
# produces IS the change it is permitted to make. `warden autonomy carve-out`
# re-derives that shape from the COMMITTED diff — including the policy it
# judges against, read from the committed repo.yaml rather than the
# session-writable worktree — and exits 0 only if every path handed to it is
# inside the carve-out.
#
# TRUST: the same argument the ingest step below makes and states — the warden
# BINARY comes from the LIVE checkout (Write-denied to the session by
# render.py), run with cwd = the session-writable worktree, so the CODE is
# trusted and only the DATA is the session's.
#
# FAILS CLOSED at every step: consulted only when the grep itself SUCCEEDED
# (a regex or a git diff that could not evaluate is never carve-out material),
# skipped with the violation intact when the live shim is missing, and any
# non-zero exit — refusal, crash, missing subcommand on an older warden —
# leaves the violation standing. It can only ever CLEAR a stop, never create one.
#
# It does NOT merge, and it adds no auto-merge path: a cleared run opens a PR
# exactly like any other run, and a human merges it.
CARVE_OUT=""
CARVE_CLEARED=0
OUT_OF_SCOPE=""
if [ -n "$VIOLATION" ] && [ "$GREP_EXIT" -eq 0 ] && [ "$CAGE_AUTONOMY_CARVE_OUT" = "1" ]; then
  if [ -x "$LIVE_REPO/.warden/bin/warden" ]; then
    # The 2>&1 is INSIDE the substitution, on the command, so stderr — which is
    # where the CLI prints every refusal line — is captured.
    #
    # Of the three placements `$( cmd 2>&1 )`, `$( (sub) 2>&1 )` and
    # `$( ... ) 2>&1`, only the LAST redirects the ASSIGNMENT and loses
    # stderr. This line is the first form (the redirect is on the command
    # inside the subshell), so the refusal reasons are kept.
    CARVE_OUT_OUT=$(printf '%s\n' "$VIOLATION" | (cd "$WORKTREE" && \
      "$LIVE_REPO/.warden/bin/warden" autonomy carve-out \
        --base origin/main --paths-from - 2>&1))
    CARVE_EXIT=$?
    printf '%s\n' "$CARVE_OUT_OUT" >> "$LOG"
    if [ "$CARVE_EXIT" -eq 0 ] && ! stores_admit "$VIOLATION"; then
      # The PLATFORM said yes and the CONSUMER did not. Fails closed toward
      # the narrower set, and names the path AND the grant, because "your
      # pinned platform admits a store your grant never did" is only
      # actionable if the human can see which store to add.
      CARVE_OUT="autonomy carve-out REFUSED by this project's grant: the platform's carve-out cleared $OUT_OF_SCOPE, but [gate].autonomy_carve_out admits only $CAGE_CARVE_OUT_STORES — a newer platform may not widen a grant already given"
    elif [ "$CARVE_EXIT" -eq 0 ]; then
      CARVE_CLEARED=1
      CARVE_OUT="autonomy carve-out CLEARED the forbidden-path stop — every forbidden path in the diff is a machine-tier pause, a non-blocking adoption, or a review round's attestation shard; the rest of the diff is ordinary session work, unjudged here. PR left open for a HUMAN to merge"
      VIOLATION=""
    else
      # The REASON travels with the refusal, into the report the human reads,
      # rather than "see $LOG" sending them to find it. It also makes the
      # capture OBSERVABLE: an empty capture is now visible in the report and
      # in a test, where before nothing downstream would have noticed one.
      CARVE_OUT="autonomy carve-out REFUSED (exit $CARVE_EXIT): ${CARVE_OUT_OUT:-(no reason captured — the carve-out said nothing, which is itself a defect)}"
    fi
  else
    CARVE_OUT="autonomy carve-out not evaluated: no executable .warden/bin/warden in the live checkout — failing closed"
  fi
fi
PR_NUM=$(gh pr list --repo "$CAGE_GITHUB" --head "$BRANCH" --state open \
  --json number -q '.[0].number' 2>/dev/null || true)
# Finished work that never became a PR because a push was refused is filed
# as that, not as the session's own "blocked" or a bare exit code.
if [ -n "$PUSH_DENIED" ] && [ -z "$PR_NUM" ]; then
  REMOTE_SHA=$(git -C "$WORKTREE" ls-remote --heads origin "refs/heads/$BRANCH" 2>/dev/null | cut -f1)
  if [ "$REMOTE_SHA" != "$(git -C "$WORKTREE" rev-parse HEAD 2>/dev/null)" ]; then
    case "$OUTCOME" in done|failed\(*) OUTCOME="push-denied" ;; esac
  fi
fi
if [ -n "$VIOLATION" ]; then
  OUTCOME="FORBIDDEN-PATH"
  [ -n "$PR_NUM" ] && gh pr close "$PR_NUM" --repo "$CAGE_GITHUB" \
    --comment "cage runner: closed automatically — diff touches protected gate surface:
$VIOLATION" 2>/dev/null || true
  notify "cage ($CAGE_PROJECT): BLOCKED" "gate-surface edit detected and PR closed — inspect $BRANCH"
fi

# Corpus publication — OUTSIDE the model. The session
# is barred from .warden/ by the forbidden-path freeze, and correctly so —
# but the review rounds it ran leave an attestation that must reach the
# committed corpus or the organization forgets the run happened. So the
# RUNNER ingests after the session exits, commits the shard, and pushes the
# branch. Never on a forbidden-path run; never when the session produced no
# commits. Failures are SAID — in the report and the ledger note — because
# "run reports success and the corpus gets nothing" is the silent direction
# this step must never take.
#
# TRUST: the warden BINARY is run from the LIVE checkout, never the
# worktree. The worktree is session-writable, and the forbidden-path check
# above only reads the COMMITTED diff — a session could overwrite
# $WORKTREE/.warden/bin/warden (uncommitted, or committed on a consumer
# that does not forbid .warden/) and the runner would then execute
# arbitrary code with its own ambient privileges, outside the session's
# permission profile. The live checkout is Write-denied to the session
# (render.py seals Edit/Write on it), so its shim is trusted. It runs with
# cwd = the worktree, so it ingests the worktree's committed shards (data,
# validated by ingest) using trusted code.
INGEST_NOTE=""
# ON A CARVE-OUT-CLEARED RUN TOO. It used to be skipped
# there, for two reasons that have both been answered. The first — that the
# shard this step commits is itself a forbidden path outside the carve-out —
# stopped being true once .warden/memory/attest/ was admitted
# INTO the carve-out. The second was ORDERING: the verdict was computed over
# the diff as it stood, and this step appends a commit AFTER it, onto a
# branch whose PR is already open, so the cleared verdict would no longer
# describe the head a reviewer sees.
#
# That one is answered by ANSWERING IT, not by declining to publish: on a
# CLEARED run the runner asks whether the commit it just made is admissible
# under the same grant that cleared the run, and pushes only if it is. Where
# the answer is yes, the cleared verdict still holds over the pushed head and
# a reviewer re-running it gets what the cage got. Where it is no — a scoped
# grant that admits `.warden/rules/` and not the attestation store
# — nothing is pushed and the run says which grant refused it.
#
# What it cost before: a run cleared by the ATTESTATION arm lost nothing (the
# session had already committed its own shard, so ingest found nothing left),
# but a run cleared by a PAUSE or an ADOPTION alone left its attestation in
# gitignored .warden/out/ forever — the corpus never learned the round
# happened, and the PR failed `warden attest check` with NO ATTESTATION. That
# is the same vice admitting the attestation store closed, in the one shape
# that change did not reach.
PUBLISH_STORE=".warden/memory/attest"
if [ -z "$VIOLATION" ] && git -C "$WORKTREE" rev-parse origin/main >/dev/null 2>&1 \
   && [ "$(git -C "$WORKTREE" rev-list --count origin/main..HEAD 2>/dev/null || echo 0)" -gt 0 ]; then
 case "$OUTCOME" in
  timeout|usage-limit)
    # A RESUMABLE run does not publish. The resume path below keeps this
    # branch and its work, so a shard committed now becomes part of the
    # RESUMED session's own diff — and on a project that forbids `.warden/`
    # its freeze closes that PR over the previous run's housekeeping, which
    # is a session's work destroyed by a step meant to preserve one. The run
    # that finishes the bead publishes; if the resume budget is spent first
    # the attestation is lost, and this note is where that is said.
    INGEST_NOTE="ingest: deferred — '$OUTCOME' leaves the branch resumable, and a shard committed now would join the resumed session's own diff; the run that finishes the bead publishes" ;;
  *)
   if [ -x "$LIVE_REPO/.warden/bin/warden" ]; then
    # THE STORE MUST BE CLEAN BEFORE INGEST. `git add` below stages the whole
    # directory in the SESSION-WRITABLE worktree, and the freeze reads only
    # the COMMITTED diff, so a file the session dropped here is invisible to
    # it and would be published as the runner's own — signed by trusted code
    # it never came from. The trust argument this step makes is about the
    # CODE; this is what makes it true of the CONTENT.
    if [ -n "$(git -C "$WORKTREE" status --porcelain -- "$PUBLISH_STORE" 2>/dev/null)" ]; then
      INGEST_NOTE="ingest: refused — the session left uncommitted content under $PUBLISH_STORE/, which this step would publish as the runner's own; corpus not fed"
    elif (cd "$WORKTREE" && "$LIVE_REPO/.warden/bin/warden" memory ingest) >> "$LOG" 2>&1; then
      if [ -n "$(git -C "$WORKTREE" status --porcelain -- "$PUBLISH_STORE" 2>/dev/null)" ]; then
        # The sha the undo below resets to, read BEFORE the commit so it can
        # only ever name this step's own parent. Empty means `rev-parse` on a
        # repo the runner has already used successfully a dozen times failed,
        # which no test here reaches; it guards the undo against resetting to
        # nothing rather than claiming to be a checked path.
        PRE_PUBLISH="$(git -C "$WORKTREE" rev-parse HEAD 2>/dev/null)"
        if [ -n "$PRE_PUBLISH" ] \
           && git -C "$WORKTREE" add "$PUBLISH_STORE" >> "$LOG" 2>&1 \
           && git -C "$WORKTREE" commit -q -m "ops(memory): cage runner commits the run's attest shard (no-bead: runner-side publication)" >> "$LOG" 2>&1; then
          PUBLISH_OK=1
          PUBLISH_WHY=""
          MINE=$(git -C "$WORKTREE" diff --name-only "$PRE_PUBLISH..HEAD" 2>/dev/null)
          OUTSIDE=$(printf '%s\n' "$MINE" | grep -v "^$PUBLISH_STORE/")
          if [ -z "$MINE" ]; then
            PUBLISH_OK=0
            PUBLISH_WHY="the commit it just made could not be read, so nothing judged it"
          elif [ -n "$OUTSIDE" ]; then
            # The commit must be what this step is FOR and nothing else.
            # UNREACHABLE TODAY and said to be: `git add "$PUBLISH_STORE"`
            # cannot stage a path outside that pathspec, so no test here
            # trips this arm and none claims to. It is a belt on the line
            # above — if that pathspec ever widens, this is what refuses
            # instead of the push carrying whatever came with it.
            PUBLISH_OK=0
            PUBLISH_WHY="the commit also carries $(printf '%s' "$OUTSIDE" | head -1), outside $PUBLISH_STORE/"
          elif [ "$CARVE_CLEARED" -eq 1 ]; then
            # ON A CLEARED RUN the commit must be admissible under the SAME
            # grant that cleared the run — or the verdict on the PR stops
            # describing the head a reviewer fetches, which is the whole of
            # the ordering objection that used to skip this step.
            #
            # ONLY on a cleared run, and that is the narrowing this check
            # earns its keep by. Re-judging the whole head on every run would
            # ask the freeze about the runner's own commit, and for the
            # consumer config this platform's docs ship — `.warden/`
            # forbidden, no carve-out grant — would refuse the push EVERY
            # time, withdrawing runner-side publication from exactly the
            # projects it exists for and reinstating NO ATTESTATION one layer
            # up. What keeps the narrowing honest is the `$OUTSIDE` check
            # above, which binds the commit's CONTENT on every run.
            #
            # What makes it REACHABLE rather than ceremony is the scoped grant:
            # a SCOPED grant can admit `.warden/rules/` and not
            # `.warden/memory/attest/`. A run cleared on the rules arm would
            # push a shard its own grant never admitted, and a reviewer
            # re-running `warden autonomy carve-out` over the PR would get a
            # refusal the cage did not.
            if ! stores_admit "$MINE"; then
              PUBLISH_OK=0
              PUBLISH_WHY="[gate].autonomy_carve_out admits only $CAGE_CARVE_OUT_STORES, and the shard lands at $OUT_OF_SCOPE"
            else
              # The REASON is captured, not sent to $LOG for the reader to go
              # find — the same choice the run's own refusal makes a hundred
              # lines up, and for the same reason. It is also the refusal that
              # actually reaches a founder on THIS repo's cage, where the
              # store IS granted: what is left is the SHAPE arm, which refuses
              # a shard over `roster_verification`, `round_binding`, an
              # unresolvable rule_id or a redaction mismatch — none of which a
              # fixed "grant the attestation store" sentence would name.
              PUBLISH_WHY=$(printf '%s\n' "$MINE" | (cd "$WORKTREE" && \
                "$LIVE_REPO/.warden/bin/warden" autonomy carve-out \
                  --base origin/main --paths-from - 2>&1))
              if [ $? -eq 0 ]; then
                PUBLISH_WHY=""
              else
                PUBLISH_OK=0
                printf '%s\n' "$PUBLISH_WHY" >> "$LOG"
                PUBLISH_WHY="warden autonomy carve-out refused the shard: ${PUBLISH_WHY:-(no reason captured — the carve-out said nothing, which is itself a defect)}"
              fi
            fi
          fi
          if [ "$PUBLISH_OK" -eq 1 ]; then
            git -C "$WORKTREE" push origin "$BRANCH:refs/heads/$BRANCH" >> "$LOG" 2>&1 || {
              PUBLISH_OK=0
              PUBLISH_WHY="the push itself failed; see $LOG"
            }
          fi
          if [ "$PUBLISH_OK" -eq 0 ]; then
            # NOT pushed, and the commit is UNDONE — including when the PUSH
            # is what failed. A RESUMED run checks out this same branch and
            # KEEPS its work (see the worktree reset above), so a commit that
            # never reached the remote would join the next session's diff, and
            # on a project that forbids `.warden/` that is a FORBIDDEN-PATH
            # verdict closing the resumed run's PR.
            #
            # `--mixed` to the sha recorded immediately before the commit, then
            # the store alone restored and cleaned: `--hard` would ALSO discard
            # every uncommitted tracked change the session left, and a cleared
            # run can be a resumable one, so the undo would delete part of what
            # the resume exists to keep. Scoped, it touches this step's work
            # and nothing else.
            if git -C "$WORKTREE" reset -q --mixed "$PRE_PUBLISH" >> "$LOG" 2>&1; then
              git -C "$WORKTREE" checkout -q -- "$PUBLISH_STORE" >> "$LOG" 2>&1 || true
              git -C "$WORKTREE" clean -fdq -- "$PUBLISH_STORE" >> "$LOG" 2>&1 || true
            else
              INGEST_NOTE="ingest: could not undo the refused shard commit — a resumed run would inherit it; see $LOG"
            fi
            # APPENDED, never gated: the reason the shard was refused is the
            # point of capturing it, and a failed undo must not swallow it.
            INGEST_NOTE="${INGEST_NOTE:+$INGEST_NOTE; }ingest: shard NOT published — $PUBLISH_WHY. Corpus not fed"
          fi
        else
          INGEST_NOTE="ingest: shard commit FAILED — corpus not fed; see $LOG"
        fi
      fi
    else
      INGEST_NOTE="ingest: warden memory ingest FAILED — corpus not fed; see $LOG"
    fi
   else
    INGEST_NOTE="ingest: no executable .warden/bin/warden in the live checkout — corpus not fed"
   fi ;;
 esac
fi

# Session handoff: the skill writes .run-summary in the worktree (bead:,
# outcome:, pr:, notes:) — the cage reads it, files it, removes it. The
# session's self-reported outcome refines a bare exit-0 "done".
BEAD=""; S_NOTES=""; S_NODE=""
if [ -f "$SUMMARY" ]; then
  BEAD=$(grep -m1 '^bead:' "$SUMMARY" | awk '{print $2}')
  S_NODE=$(grep -m1 '^node:' "$SUMMARY" | awk '{print $2}')
  S_OUTCOME=$(grep -m1 '^outcome:' "$SUMMARY" | awk '{print $2}')
  S_NOTES=$(grep -m1 '^notes:' "$SUMMARY" | cut -d' ' -f2- | tr ',' ';')
  [ "$OUTCOME" = "done" ] && [ -n "$S_OUTCOME" ] && OUTCOME="$S_OUTCOME"
  { echo; echo "## Session summary"; cat "$SUMMARY"; } >> "$REPORT"
  rm -f "$SUMMARY"
fi
# Interrupted mid-bead WITH a checkpoint -> resumable. Anything else (done,
# blocked, forbidden-path) ends the attempt chain: only an interruption is
# worth continuing, and a blocked bead has already said why.
case "$OUTCOME" in
  timeout|usage-limit)
    # A killed session never wrote .run-summary — that is the whole point
    # of the checkpoint, so the bead comes from PROGRESS here, not from the
    # handoff that does not exist.
    P_BEAD=$(grep -m1 '^bead:' "$PROGRESS" 2>/dev/null | awk '{print $2}')
    [ -z "$P_BEAD" ] && P_BEAD="${BEAD:-}"
    if [ -f "$PROGRESS" ] && [ -n "$P_BEAD" ]; then
      { echo "bead=$P_BEAD"; echo "branch=$BRANCH";
        echo "attempt=$((RESUME_ATTEMPT + 1))"; } > "$RESUME_STATE"
      cp "$PROGRESS" "$CAGE_DIR/resume-progress" 2>/dev/null || true
      notify "cage ($CAGE_PROJECT): resumable" \
        "bead $P_BEAD interrupted with a checkpoint — the next run continues it"
    else
      rm -f "$RESUME_STATE"
    fi ;;
  *) rm -f "$RESUME_STATE" ;;
esac

# A hard stop that quietly did not fire is the shape of an unenforced gate, so
# the carve-out verdict is SAID — report, ledger note, and (when it cleared a
# stop) the run's notification — never inferred from the absence of a
# FORBIDDEN-PATH row.
[ -n "$PUSH_DENIED" ] && S_NOTES="${S_NOTES:+$S_NOTES; }push denied: $PUSH_DENIED"
[ -n "$CLASSIFY_NOTE" ] && S_NOTES="${S_NOTES:+$S_NOTES; }$CLASSIFY_NOTE"
[ -n "$CARVE_OUT" ] && S_NOTES="${S_NOTES:+$S_NOTES; }$CARVE_OUT"
[ -n "$INGEST_NOTE" ] && S_NOTES="${S_NOTES:+$S_NOTES; }$INGEST_NOTE"
{ echo "- end: $(date '+%F %T')"; echo "- outcome: $OUTCOME";
  echo "- duration: ${DURATION_S}s"; echo "- attempt: $((RESUME_ATTEMPT + 1))"; echo "- pr: ${PR_NUM:-none}";
  [ -n "$VIOLATION" ] && { echo "- FORBIDDEN PATHS:"; echo "$VIOLATION" | sed 's/^/    /'; };
  [ -n "$CLASSIFY_NOTE" ] && echo "- $CLASSIFY_NOTE";
  [ -n "$CARVE_OUT" ] && echo "- $CARVE_OUT";
  [ -n "$INGEST_NOTE" ] && echo "- $INGEST_NOTE";
  echo "- session log: $RUNLOG"; } >> "$REPORT"
ledger "$OUTCOME" "${BEAD:-unknown}" "${PR_NUM:-}" "$S_NOTES" "$S_NODE" \
       "$DURATION_S" "$((RESUME_ATTEMPT + 1))"
notify "cage ($CAGE_PROJECT): $OUTCOME" \
       "PR ${PR_NUM:-none} · report: $REPORT${CARVE_OUT:+ · $CARVE_OUT}"
exit 0
