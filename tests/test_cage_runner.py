"""Execution harness for the cage runner.

`cage/run.sh` is the hard-stop surface — tiered HIGH in repo.yaml — and
parsing it (`bash -n`) or grepping it for strings never EXECUTES it. A stop
that is present in the file but not actually wired up looks
identical to one that works, right up until it matters. So every test here
runs the real runner against a fixture cage and asserts the stop FIRES, with
the ledger row and report it produces — `skip()` reports, writes a ledger row
and notifies, and "silence is never ambiguous" is a property, not a comment.

How the harness stays honest, hermetic and fast:

* `$HOME` is a tmp dir. The runner resets PATH to `$HOME/.local/bin:...`, so
  our stubs land FIRST on the runner's own PATH, and no real kill switch,
  LaunchAgent or `~/.cage-*` file is ever read or written.
* No network and no real `claude`: `claude`, `gh` and `osascript` are bash
  stubs driven by control files in a per-test `ctl/` dir; the git "remote" is
  a local bare repo. Everything else (git, the runner) is real.
* The watchdog's clock is compressed by a `sleep` stub that records the
  cadence run.sh asked for (30s poll, 15s grace) and returns in ~10ms. The
  control flow — TERM, then grace, then KILL — runs for real; only wall-clock
  time is scaled, so the watchdog can live in the normal suite.
* Stubs never see a compressed clock themselves: the claude stub sleeps via
  the absolute path to the real `sleep` binary.
* The stub scripts are written ONCE per session and hardlinked into each
  test's bin dir: macOS charges ~0.1s to scan every newly created executable
  the first time it runs, which is most of what a caged run costs.

What this file does NOT reach, so the claim stays honest:

* `skip "cannot cd into worktree"` (run.sh:226) — the runner creates or
  resets that directory a dozen lines earlier, so nothing but a race can
  make the cd fail.
* `command -v gh` (run.sh:125) — the runner's PATH tail is hard-coded, and
  gh sits in it on both target machines (/opt/homebrew/bin here,
  /usr/bin on GitHub-hosted ubuntu), so its absence cannot be staged. The
  `claude` arm of the same guard IS covered, as are the gh-auth and
  gh-api stops immediately after it.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
import toolchain

from conftest import _AMBIENT_GIT_CONFIG

from cage import config as cage_config
from cage import measure
from cage import render as cage_render
from cage.config import CageConfig

REAL_SLEEP = shutil.which("sleep") or "/bin/sleep"
REAL_DATE = shutil.which("date") or "/bin/date"

# run.sh names the branch, report and session log from RUN_ID: a second-
# resolution stamp plus a sequence suffix when two runs claim the same second.
RUN_BRANCH_RE = r"auto/\d{8}-\d{6}(_\d{2})?"

# The runner hard-codes this PATH tail; only the $HOME/.local/bin head is ours.
RUNNER_SYSTEM_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
                      "/usr/sbin", "/sbin")

# Quiet hours are the interval that must stay QUIET, read forward from start
# to end (5->23 does not cross midnight, 23->5 does), and start == end means
# none. No schema-valid pair is quiet at every wall-clock hour (covering all
# 24 would need end = 24), so ALWAYS_QUIET is a sentinel written straight
# into cage.env — which is all the runner reads — to pin the branch
# under test at any hour. NEVER_QUIET is a real, declarable value.
NEVER_QUIET = (0, 0)       # empty interval -> the guard is off
ALWAYS_QUIET = (0, 24)     # 0 <= HOUR < 24 -> quiet at every hour

# A real nine-to-five cage: quiet from home time round to the start of work,
# which is the crossing-midnight arm with values a config can declare. Used
# with Cage.freeze_hour(), never with the wall clock — an interval derived
# from `time.strftime` can stop covering the run when the hour rolls over
# between deriving it and run.sh reading `date +%H`, which is a flake in the
# hard-stop suite.
NINE_TO_FIVE_QUIET = (17, 9)
QUIET_HOUR = 20            # inside 17->9
WORKING_HOUR = 12          # outside it

# --------------------------------------------------------------------------
# stub bodies (bash). Each stub sources stub.env — written per test beside
# the hardlink — for CTL (the control/observation dir) and REAL_SLEEP.
# --------------------------------------------------------------------------

_CTL_HELPER = """\
_ctl() { if [ -f "$CTL/$1" ]; then cat "$CTL/$1"; else printf '%s' "$2"; fi; }
"""

GH_STUB = _CTL_HELPER + """\
echo "$*" >> "$CTL/gh.log"
case "$*" in
  "auth status"*)
    exit "$(_ctl gh_auth_exit 0)" ;;
  "api "*)
    exit "$(_ctl gh_api_exit 0)" ;;
  "pr close"*)
    exit 0 ;;
  "run list"*)
    E="$(_ctl gh_runlist_exit 0)"; [ "$E" -ne 0 ] && exit "$E"
    _ctl main_state success; exit 0 ;;
  *"--json headRefName"*)
    E="$(_ctl gh_prlist_exit 0)"; [ "$E" -ne 0 ] && exit "$E"
    _ctl open_prs 0; exit 0 ;;
  *"--json number"*)
    _ctl pr_number ''; exit 0 ;;
esac
exit 0
"""

# The session's behavior is a per-test file the stub sources, so installing a
# new one costs no new executable.
#
# The runner's usage pre-flight is a `claude` call too. It is told apart by its
# prompt and answered from ctl files: `probe.out` is its stream-json (default: a
# completed result, so usage is usable), `probe_exit` its exit code, and
# `probe_sleep` holds it open in real seconds. It never touches claude.argv,
# which stays the record of the SESSION.
USAGE_PROBE_USABLE = ('{"type":"result","subtype":"success","is_error":false,'
                      '"api_error_status":null,"result":"ok"}')
CLAUDE_STUB = _CTL_HELPER + """\
case "$*" in
  *"cage usage probe"*)
    echo "$*" >> "$CTL/probe.argv"
    pwd > "$CTL/probe.cwd"
    if [ -f "$CTL/probe_sleep" ]; then
      "$REAL_SLEEP" "$(cat "$CTL/probe_sleep")" & echo $! > "$CTL/probe_child.pid"; wait
    fi
    if [ -f "$CTL/probe.out" ]; then cat "$CTL/probe.out"; else echo '""" + USAGE_PROBE_USABLE + """'; fi
    exit "$(_ctl probe_exit 0)" ;;
esac
echo "$*" >> "$CTL/claude.argv"
pwd > "$CTL/claude.cwd"
. "$(dirname "$0")/claude.body"
"""

# Compressed clock: log the cadence run.sh asked for, then return fast. The
# optional gate makes the first poll wait for the session stub to finish its
# setup, so timeout tests are ordered by construction, not by luck. Cage.
# arm_gate() clears the readiness marker as it arms, so a SECOND gated run in
# one test does not sail past on the previous run's marker.
#
# The wait is UNCAPPED by default: on a loaded CI runner a session can take
# longer than a fixed budget to install its TERM trap and touch
# session_ready, and a gate that expires silently lets the watchdog's TERM
# land before the trap exists — "the watchdog never signalled" over what is
# only harness lag. Ordering by construction means no budget; the outer
# cage.run(timeout=...) is the bound if a session never becomes ready. A test
# may set gate_budget (ticks) to bound it deliberately — expiry then writes
# gate_expired so a bounded wait that gave up is a diagnosable fact, never a
# silent one.
SLEEP_STUB = _CTL_HELPER + """\
echo "$1" >> "$CTL/sleep.log"
if [ -f "$CTL/wait_for_ready" ]; then
  rm -f "$CTL/wait_for_ready"
  BUDGET="$(_ctl gate_budget 0)"
  W=0
  while [ ! -f "$CTL/session_ready" ]; do
    if [ "$BUDGET" -gt 0 ] && [ "$W" -ge "$BUDGET" ]; then
      echo expired >> "$CTL/gate_expired"
      break
    fi
    # An uncapped wait must still die with its runner: subprocess.run's
    # timeout kills only run.sh, so without this an orphaned stub would
    # fork REAL_SLEEP forever on a laptop (review round, F2).
    kill -0 "$PPID" 2>/dev/null || break
    "$REAL_SLEEP" 0.02; W=$((W + 1))
  done
fi
exec "$REAL_SLEEP" "$(_ctl sleep_real 0.01)"
"""

OSASCRIPT_STUB = """\
echo "$*" >> "$CTL/osascript.log"
exit 0
"""

SESSION_OK = 'echo "session complete"\n'
# A session that outlives its watchdog: ~30s of 50ms sleeps, bounded by a
# shell counter and NEVER by a command substitution. `for _ in $(seq 1 600)`
# computes its bound in a SUBSHELL — a child of the session, and therefore
# exactly what run.sh's `pkill -TERM -P "$CPID"` kills first. A subshell
# resets the session's TERM trap, so the kill landing during the expansion
# truncates the word list to nothing, the loop runs zero times, and the
# session falls through to its completion marker before the parent's own
# `kill -TERM "$CPID"` arrives — TERM logged AND `ran_to_completion` present.
# `test_a_session_loop_bounded_by_a_subshell_falls_through_when_the_
# watchdog_kills_its_children` reproduces it with the two kill lines run.sh
# uses.
SLOW_LOOP = ('N=0; while [ "$N" -lt 600 ]; do "$REAL_SLEEP" 0.05; '
             'N=$((N + 1)); done\n')
# The TERM trap the watchdog tests install: it records, AT THE MOMENT the
# handler runs, whether the completion marker already exists — so an ordering
# defect names itself in the signals log instead of surfacing as a bare
# `assert not exists()` after the process is gone.
TERM_TRAP = ('trap \'[ -f "$CTL/ran_to_completion" ] && '
             'echo COMPLETED_BEFORE_TERM >> "$CTL/signals"; '
             'echo TERM >> "$CTL/signals"; exit 143\' TERM\n')

STUBS = {"gh": GH_STUB, "claude": CLAUDE_STUB, "sleep": SLEEP_STUB,
         "osascript": OSASCRIPT_STUB}

# A declared-services cage. The runner sources this when it exists and guards
# every call with `declare -F`, so writing it is the only way to execute
# run.sh's service arms end to end.
SERVICES_SH = _CTL_HELPER + """\
services_preflight() {
  echo preflight >> "$CTL/services.log"
  [ -f "$CTL/svc_preflight_fail" ] && { echo "port 54329 in use and not ours"; return 1; }
  return 0
}
services_start() {
  echo start >> "$CTL/services.log"
  [ -f "$CTL/svc_start_fail" ] && { echo "pg_ctl start failed"; return 1; }
  export CAGE_DATABASE_URL="postgresql://fixture@127.0.0.1:54329/cage"
  return 0
}
services_stop() { echo stop >> "$CTL/services.log"; return 0; }
"""


def _on_runner_path(tool: str) -> bool:
    """Would the runner's fixed PATH find this tool outside our stub dir?"""
    return any(os.access(os.path.join(d, tool), os.X_OK) for d in RUNNER_SYSTEM_DIRS)


def _stub_env(ctl: Path) -> str:
    return f"CTL={shlex.quote(str(ctl))}\nREAL_SLEEP={shlex.quote(REAL_SLEEP)}\n"


@pytest.fixture(scope="session")
def stub_source(tmp_path_factory) -> Path:
    """The stub scripts, written and exec-warmed once for the whole session."""
    src = tmp_path_factory.mktemp("cage-stubs")
    (src / "stub.env").write_text(_stub_env(src))
    (src / "claude.body").write_text("exit 0\n")
    for name, body in STUBS.items():
        path = src / name
        path.write_text('#!/bin/bash\n. "$(dirname "$0")/stub.env"\n' + body)
        path.chmod(0o755)
        subprocess.run([str(path)], capture_output=True)   # pay the scan once
    return src


class Cage:
    """A complete fixture cage: env, stubs, a real git remote, no network."""

    def __init__(self, tmp: Path, stub_source: Path, *,
                 quiet_hours: tuple[int, int] = NEVER_QUIET,
                 forbidden_paths: Sequence[str] = (),
                 scrub_paths: Sequence[str] = (),
                 timeout_secs: int = 7200,
                 max_open_prs: int = 2,
                 max_resumes: int = 2,
                 platform_probe: str = "",
                 autonomy_carve_out: bool = False,
                 carve_out_stores: Sequence[str] = (),
                 toolchain_path: Sequence[str] = (),
                 required_tools: Sequence[str] = (),
                 seed_files: dict[str, str] | None = None,
                 prompt: bool = True,
                 services: bool = False) -> None:
        self.tmp = tmp
        self.stub_source = stub_source
        self.home = tmp / "home"
        self.bin = self.home / ".local" / "bin"
        self.ctl = tmp / "ctl"
        self.dir = tmp / "cage"           # CAGE_DIR
        self.live = tmp / "live"          # CAGE_LIVE_REPO
        self.worktree = tmp / "run"     # CAGE_WORKTREE (never pre-created)
        self.origin = tmp / "origin.git"
        self.log = tmp / "logs" / "cage.log"
        for path in (self.bin, self.ctl, self.dir):
            path.mkdir(parents=True, exist_ok=True)

        # Built from scratch, never inherited: GIT_DIR / GIT_WORK_TREE from an
        # ambient hook would bind these git calls to the WRONG repo (the
        # concern warden/runs.py:git_env() exists for), and the system-level
        # gitconfig reaches git independently of $HOME — a host commit.gpgsign
        # or prompting credential.helper would fail the harness for reasons
        # that have nothing to do with run.sh.
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            # The config layers AND the background-work suppression, from the
            # one derivation in conftest — a hand-typed pair that omits
            # GIT_CONFIG_SYSTEM lets a host /etc/gitconfig reach the caged run.
            **_AMBIENT_GIT_CONFIG,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "cage run",
            "GIT_AUTHOR_EMAIL": "cage@example.invalid",
            "GIT_COMMITTER_NAME": "cage run",
            "GIT_COMMITTER_EMAIL": "cage@example.invalid",
        }
        self._seed_repo(seed_files or {})

        self.cfg = CageConfig(
            name="sampleproj",
            github="acme/sampleproj",
            live_checkout=self.live,
            worktree=self.worktree,
            cage_dir=self.dir,
            log=self.log,
            source=self.dir / "cage.toml",
            branch_prefix="auto/",
            quiet_start=quiet_hours[0],
            quiet_end=quiet_hours[1],
            max_open_prs=max_open_prs,
            timeout_secs=timeout_secs,
            max_resumes=max_resumes,
            platform_probe=platform_probe,
            autonomy_carve_out=autonomy_carve_out,
            carve_out_stores=list(carve_out_stores),
            forbidden_paths=list(forbidden_paths),
            scrub_paths=list(scrub_paths),
            toolchain_path=list(toolchain_path),
            required_tools=list(required_tools),
        )
        self._render(prompt=prompt, services=services)
        self._write_stubs()

    # ---- setup ----------------------------------------------------------
    def _git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, env=self.env,
                              check=True, capture_output=True, text=True)

    def _seed_repo(self, seed_files: dict[str, str]) -> None:
        self.live.mkdir()
        self._git(self.live, "init", "-q", "-b", "main")
        files = {"README.md": "# sampleproj\n", **seed_files}
        for rel, body in files.items():
            path = self.live / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        self._git(self.live, "add", "-A")
        self._git(self.live, "commit", "-q", "-m", "seed")
        self._git(self.tmp, "init", "-q", "-b", "main", "--bare",
                  str(self.origin))
        # IN THE RECEIVING REPO'S OWN CONFIG, and it has to be. The env layer
        # this fixture spreads suppresses git's background maintenance for
        # every git it runs — but `GIT_CONFIG_*` is in git's `local_repo_env`,
        # so git STRIPS it when it spawns into another repository: the
        # `git-receive-pack` behind the push below gets an environment without
        # it and forks a detached `git maintenance run --auto` into the origin.
        # `test_an_unreachable_remote_stops_the_run` then rmtree's that origin
        # as its first statement, which is the exact race that took the memory
        # watch test down on a loaded runner. Written here rather than relied
        # on from the environment because only the repository layer survives
        # the crossing.
        self._git(self.origin, "config", "maintenance.auto", "false")
        self._git(self.origin, "config", "gc.auto", "0")
        self._git(self.live, "remote", "add", "origin", str(self.origin))
        self._git(self.live, "push", "-q", "origin", "main")

    def _render(self, prompt: bool, services: bool) -> None:
        runner = self.dir / "run.sh"
        runner.write_text(cage_render.runner_script())   # the shipped artifact
        runner.chmod(0o755)
        self.runner = runner
        self.env_file = self.dir / "cage.env"
        self.env_file.write_text(cage_render.render_env(self.cfg, home=self.home))
        (self.dir / "cage-profile.json").write_text(
            json.dumps(cage_render.render_profile(self.cfg), indent=2) + "\n")
        if prompt:
            (self.dir / "prompt.md").write_text("run the autonomous-run skill\n")
        if services:
            (self.dir / "services.sh").write_text(
                "#!/bin/bash\n" + _stub_env(self.ctl) + SERVICES_SH)

    def _write_stubs(self) -> None:
        (self.bin / "stub.env").write_text(_stub_env(self.ctl))
        for name in STUBS:
            try:
                os.link(self.stub_source / name, self.bin / name)
            except OSError:                       # different filesystem
                shutil.copy(self.stub_source / name, self.bin / name)
        self.session(SESSION_OK)

    # ---- knobs ----------------------------------------------------------
    def session(self, body: str) -> None:
        """Install the caged session's behavior. stdout lands in the runlog."""
        (self.bin / "claude.body").write_text(body)

    def ctl_set(self, name: str, value: str) -> None:
        (self.ctl / name).write_text(value)

    def arm_gate(self) -> None:
        """Hold the watchdog's first poll until the session says it is ready.

        Clearing session_ready is load-bearing: a second gated run in the same
        test would otherwise see the previous run's marker and not wait at all.
        """
        (self.ctl / "session_ready").unlink(missing_ok=True)
        self.ctl_set("wait_for_ready", "1")

    def freeze_hour(self, hour: int) -> None:
        """Pin the wall-clock hour the runner reads, and only that.

        Installed on demand rather than with the other stubs: every test that
        does not ask for it keeps running against the real `date`, so this
        cannot quietly alter the run ids, ledger dates or report timestamps
        the rest of the file asserts on. `date +%H` is the single call the
        quiet-hours guard makes; everything else passes straight through.
        """
        stub = self.bin / "date"
        if not stub.exists():
            stub.write_text(
                '#!/bin/bash\n. "$(dirname "$0")/stub.env"\n'
                'if [ "$1" = "+%H" ] && [ -f "$CTL/fake_hour" ]; then\n'
                '  cat "$CTL/fake_hour"; exit 0\n'
                'fi\n'
                f'exec {shlex.quote(REAL_DATE)} "$@"\n')
            stub.chmod(0o755)
        self.ctl_set("fake_hour", f"{hour:02d}")

    def touch(self, name: str) -> Path:
        path = self.home / name
        path.write_text("")
        return path

    def env_line(self, var: str, value: str) -> None:
        """Rewrite one cage.env value (the runner reads nothing else)."""
        lines = [ln for ln in self.env_file.read_text().splitlines()
                 if not ln.startswith(f"{var}=")]
        lines.append(f"{var}={shlex.quote(value)}")
        self.env_file.write_text("\n".join(lines) + "\n")

    # ---- run ------------------------------------------------------------
    def run(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(self.runner), *args], cwd=self.tmp,
                              env=self.env, capture_output=True, text=True,
                              timeout=timeout)

    # ---- observations ---------------------------------------------------
    @property
    def ledger(self) -> list[list[str]]:
        path = self.dir / "ledger.csv"
        if not path.is_file():
            return []
        # the runner's own reader: csv, header skipped, notes unquoted
        rows, bad = measure.parse(path.read_text().splitlines())
        assert not bad, f"the runner wrote malformed ledger rows at {bad}"
        return rows

    @property
    def last_row(self) -> list[str]:
        assert self.ledger, "no ledger row written — the run was silent"
        return self.ledger[-1]

    @property
    def report(self) -> str:
        return "".join(p.read_text() for p in sorted((self.dir / "reports").glob("*.md")))

    @property
    def reports(self) -> list[Path]:
        """One report per run — run.sh names it from RUN_ID."""
        return sorted((self.dir / "reports").glob("*.md"))

    @property
    def session_logs(self) -> list[Path]:
        """One session log per run, oldest first."""
        return sorted((self.dir / "reports").glob("*.session.log"),
                      key=lambda p: p.stat().st_mtime)

    @property
    def runlog(self) -> str:
        """The session log of the LAST run — every run has its own (RUN_ID),
        so joining every match would let one run's text answer for another."""
        logs = self.session_logs
        return logs[-1].read_text() if logs else ""

    @property
    def notifications(self) -> str:
        return self.log.read_text() if self.log.is_file() else ""

    def log_text(self) -> str:
        """$CAGE_LOG in full. Same bytes `notifications` reads, under the name
        a test means when it asserts that a REASON reached the log a report
        points the reader at."""
        return self.log.read_text() if self.log.is_file() else ""

    def stub_log(self, name: str) -> str:
        path = self.ctl / name
        return path.read_text() if path.is_file() else ""

    @property
    def branch(self) -> str:
        return self._git(self.worktree, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def assert_skipped(self, proc: subprocess.CompletedProcess,
                       fragment: str) -> None:
        """A skip is a report + a ledger row + a notification, never silence."""
        assert proc.returncode == 0, proc.stderr
        row = self.last_row
        assert row[2] == "skipped", row
        assert fragment.replace(",", ";") in row[4], row   # note is comma-scrubbed
        assert "SKIPPED: " in self.report and fragment in self.report
        assert fragment in self.notifications
        assert "skipped" in self.notifications
        assert not self.stub_log("claude.argv"), "the session ran despite the stop"


@pytest.fixture
def make_cage(tmp_path, stub_source):
    def _make(**kwargs: object) -> Cage:
        return Cage(tmp_path, stub_source, **kwargs)
    return _make


@pytest.fixture
def cage(make_cage):
    return make_cage()


# ---------- kill switches ----------------------------------------------------

def test_global_kill_switch_stops_the_run(cage):
    cage.touch(".cage-off")
    proc = cage.run()
    cage.assert_skipped(proc, "kill switch (~/.cage-off) present")
    assert not cage.worktree.exists()          # nothing was mutated
    assert "display notification" in cage.stub_log("osascript.log")


def test_project_kill_switch_stops_only_that_project(cage):
    cage.touch(".cage-sampleproj-off")
    cage.assert_skipped(cage.run(), "kill switch (~/.cage-sampleproj-off) present")


def test_kill_switch_beats_the_quiet_hours_override(make_cage):
    # "honored even with the quiet-hours override" — and the one-shot override
    # must not be burned by a run the kill switch stopped.
    cage = make_cage(quiet_hours=ALWAYS_QUIET)
    cage.touch(".cage-off")
    override = cage.touch(".cage-run-now")
    cage.assert_skipped(cage.run(), "kill switch")
    assert override.exists(), "kill-switch run consumed the one-shot override"


def test_check_mode_reports_a_skip_without_writing_evidence(cage):
    cage.touch(".cage-off")
    proc = cage.run("--check")
    assert proc.returncode == 0
    assert "pre-flight would SKIP: kill switch" in proc.stdout
    assert cage.ledger == []                    # --check mutates nothing
    assert cage.report == ""


def test_check_mode_claims_no_run_id_and_leaves_no_evidence_file(cage):
    """`--check` must claim NOTHING: claiming a RUN_ID creates the report
    file under `noclobber`, and creating a file is a mutation.

    Asserts the directory ENTRY, not the text. The sibling test above asserts
    `cage.report == ""`, and `report` concatenates the reports' CONTENT — a
    zero-byte file created by a claim reads as `""` and slips straight past
    it. This test pins the `[ "$CHECK_MODE" -eq 0 ]` guard around the claim
    loop and its own stated invariant ("--check claims nothing — it must not
    mutate state"); deleting the guard must turn it red.
    """
    cage.touch(".cage-off")
    proc = cage.run("--check")
    assert proc.returncode == 0
    assert cage.reports == [], \
        "--check claimed a run id: it left a report file behind"
    assert cage.session_logs == []


# ---------- tooling pre-flight ----------------------------------------------

def test_missing_prompt_md_stops_the_run(make_cage):
    cage = make_cage(prompt=False)
    cage.assert_skipped(cage.run(), "prompt.md missing in cage dir")


# ── the prompt's skill must resolve, or the run starts fail-open ─────────────

def _seed_pack(cage, *skills: str, version: str = "0.2.0") -> None:
    for name in skills:
        d = (cage.home / ".claude" / "plugins" / "cache" / "agentops"
             / "agentops-skills" / version / "skills" / name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n")


def test_prompt_skill_missing_from_installed_pack_stops_the_run(cage):
    """A prompt skill the installed pack does not serve stops the run.

    The prompt hard-invokes a skill by name; a name the installed pack no
    longer serves (a rename is the usual way this happens) would start an
    unattended session with no protocol layer — only runner-side stops
    remain. The prompt below names a skill the seeded pack does not
    serve, which is the whole point: seeding it would delete the test."""
    (cage.dir / "prompt.md").write_text(
        "Invoke the `agentops-skills:no-such-skill` skill now.\n")
    _seed_pack(cage, "autonomous-run", "review-queue")
    cage.assert_skipped(cage.run(), "does not serve")


def test_a_stale_pack_version_does_not_satisfy_the_skill_preflight(cage):
    """The pre-flight must not search the WHOLE plugin cache: the cache keeps
    old pack versions beside new ones. A prompt naming a skill only the
    RETIRED version serves would pass pre-flight while Claude resolves the
    current version and the skill never loads — the exact fail-open the
    pre-flight exists to close, wearing the check's own green tick."""
    (cage.dir / "prompt.md").write_text(
        "Invoke the `agentops-skills:night-shift` skill now.\n")
    _seed_pack(cage, "night-shift", "morning-review", version="0.1.0")
    _seed_pack(cage, "autonomous-run", "review-queue")
    cage.assert_skipped(cage.run(), "does not serve")


def test_prompt_skill_present_in_pack_proceeds(cage):
    (cage.dir / "prompt.md").write_text(
        "Invoke the `agentops-skills:autonomous-run` skill now.\n")
    _seed_pack(cage, "autonomous-run")
    proc = cage.run()
    assert "does not serve" not in proc.stdout + proc.stderr + \
        (cage.log.read_text() if cage.log.exists() else "")


def test_prompt_skill_unverifiable_without_cache_logs_and_proceeds(cage):
    """No plugin cache on the machine: the check cannot verify either way.
    It must SAY so and continue — a silent pass here would be the vacuous
    check this repo's defining defect class warns about, and a hard stop
    would fail every machine with a different cache layout."""
    (cage.dir / "prompt.md").write_text(
        "Invoke the `agentops-skills:autonomous-run` skill now.\n")
    proc = cage.run()
    combined = proc.stdout + proc.stderr + \
        (cage.log.read_text() if cage.log.exists() else "")
    assert "does not serve" not in combined
    assert "skill resolution unverifiable" in combined, (
        "the blind spot must be logged, never silent. (And the needle must "
        "be a phrase, not a word: this test's own tmp-dir name contains "
        "'unverifiable', which satisfied the first draft — self-reference.)")


@pytest.mark.skipif(_on_runner_path("claude"),
                    reason="a real claude on the runner's fixed PATH shadows its absence")
def test_missing_claude_stops_the_run(cage):
    (cage.bin / "claude").unlink()
    cage.assert_skipped(cage.run(), "claude CLI not on PATH")


@pytest.mark.skipif(_on_runner_path("gh"),
                    reason="a real gh on the runner's fixed PATH shadows its absence")
def test_missing_gh_stops_the_run(cage):
    (cage.bin / "gh").unlink()
    cage.assert_skipped(cage.run(), "gh CLI not on PATH")


# ---------- the project's toolchain, declared --------------------------------
#
# The runner resets PATH to a fixed list so a launchd-started run behaves like
# a hand-started one. That reset is also why a toolchain installed anywhere
# else is INVISIBLE inside the cage: Go's own installer puts `go` in
# /usr/local/go/bin, on no such list. The first completed run of this repo's
# cage spent 27 minutes building a real fix and then refused at ship step 1,
# every time, because one cell of the project's own suite shells out to `go`
# and got exit 127 — a red `verify --scope tests` at every HEAD that no
# session could clear. So the project DECLARES what its PATH must carry, and
# an absent tool is a pre-flight skip that names it rather than an hour spent
# to reach a stop that was decided before the run began.

# A name no machine has, so the absence under test is the declared tool's and
# never some host's real binary.
ABSENT_TOOL = "cage-declared-absent-toolchain"


def test_a_declared_tool_absent_from_the_caged_path_skips_the_run(make_cage):
    cage = make_cage(required_tools=[ABSENT_TOOL])
    cage.assert_skipped(cage.run(), f"required tool '{ABSENT_TOOL}' is not on "
                                    "the caged PATH")


def test_a_declared_toolchain_dir_puts_the_tool_on_the_sessions_path(
        make_cage, tmp_path):
    """The other half, and the one that unblocks a run.

    The same tool name that skips the run above resolves once its directory
    is declared — and it resolves INSIDE THE SESSION, which is where it has
    to: the thing that could not find `go` was `uv run pytest`, running as a
    child of the caged claude, not the pre-flight. So the proof is the
    session's own `command -v`, read back out of the worktree.
    """
    toolbin = tmp_path / "toolchain"
    toolbin.mkdir()
    tool = toolbin / ABSENT_TOOL
    tool.write_text("#!/bin/sh\necho declared-toolchain\n")
    tool.chmod(0o755)
    cage = make_cage(toolchain_path=[str(toolbin)], required_tools=[ABSENT_TOOL])
    cage.session(f"command -v {ABSENT_TOOL} > resolved.txt\n"
                 f"{ABSENT_TOOL} >> resolved.txt\n")
    proc = cage.run()
    assert proc.returncode == 0, proc.stderr
    assert cage.last_row[2] != "skipped", (
        f"the declared toolchain dir did not satisfy the pre-flight: "
        f"{cage.last_row}")
    resolved = (cage.worktree / "resolved.txt").read_text().splitlines()
    assert resolved[0] == str(tool), (
        f"the caged session resolved {ABSENT_TOOL} to {resolved[0]!r}, not to "
        "the declared toolchain dir — a pre-flight that passes while the "
        "session still cannot run the tool is the vacuous half of this fix")
    assert resolved[1] == "declared-toolchain", "the session could not run it"


def test_the_toolchain_dir_never_displaces_the_cages_own_stubs(make_cage,
                                                                    tmp_path):
    """A prepend is a prepend, so a declared dir shadowing `gh` or `claude`
    would silently re-point the runner's own tooling at a project's checkout.
    It cannot be stopped by ordering — `$HOME/.local/bin` is first in the
    fixed list and the declared dirs go in front of all of it — so what is
    pinned here is that the pre-flight still SEES the substitution: a project
    that shadows `gh` gets a run that stops, never one that silently uses the
    wrong binary for the rest of the gauntlet.
    """
    toolbin = tmp_path / "toolchain"
    toolbin.mkdir()
    (toolbin / "gh").write_text("#!/bin/sh\nexit 7\n")
    (toolbin / "gh").chmod(0o755)
    cage = make_cage(toolchain_path=[str(toolbin)])
    cage.assert_skipped(cage.run(), "gh auth unavailable headless")


# ---------- the cage's toolchain promise, exported --------------------------
#
# The pre-flight above answers "is the declared tool here NOW"; this answers
# "what does the session's own suite do when one is not". tests/toolchain.py
# splits that by CONTRACT: where the environment PROMISED the toolchain an
# absence is a named FAILURE, and where it promised nothing — a
# contributor's laptop — it is a named SKIP. The switch is
# NIGHTGATE_REQUIRE_TOOLCHAIN, then its old name AGENTOPS_REQUIRE_TOOLCHAIN
# (read as an alias until v4.0.0), falling back to CI.
#
# A cage with a `[toolchain]` block is the promising kind by its own
# declaration, and the runner said so nowhere: it exported HOME and PATH and
# no strictness, so every command a caged session ran was the LENIENT half.
# Measured on efd4b1b under the cage's fixed PATH, which excludes the
# /usr/local/go/bin that Go's installer uses:
# `test_each_fixtures_verify_scope_passes_and_leaves_the_tree_clean[go]` is
# `1 skipped` (exit 0) without the variable and `1 failed` with it — the
# undeclared-toolchain condition, going green inside the one environment that contracted for
# the binary. The pre-flight's own list covered it only as long as two
# independently edited lists (`.cage/cage.toml`'s `require` and the suite's
# `[tool.nightgate.toolchain].require`) happen to agree.

def _session_env_probe(var: str) -> str:
    """A session that reports one inherited variable into the worktree."""
    return f'printf "%s\\n" "${{{var}-<unset>}}" > seen.txt\n'


# DERIVED, never re-typed (round 1, F1). `tests/toolchain.py` owns the switch's
# name — `strict()` reads `STRICT_ENV`, then the old name `LEGACY_STRICT_ENV`
# until v4.0.0, then `CI`, and nothing else — and every sibling arm
# of this contract (the repo.yaml, ci.yml and CONTRIBUTING.md guards in
# tests/test_toolchain.py) derives from it for the same reason: a rename there
# would leave run.sh exporting the dead name, every caged session back in the
# lenient half, and a re-typed literal here green on both sides of its own
# assertion. Driven as a mutation in review: renamed in toolchain.py and the
# four files that derive, the whole suite stayed at 4373 passed.
#
# THE OLD NAME, deliberately, until cage/run.sh moves. The switch was renamed
# to NIGHTGATE_REQUIRE_TOOLCHAIN and `strict()` reads the old name as an
# alias until v4.0.0; run.sh still exports the old one, and run.sh is a path
# no agent may edit, so its rename is a founder-directed change. These cells
# pin what run.sh actually exports and `strict()` still honours; when run.sh
# moves, this becomes `toolchain.STRICT_ENV` again. What they do NOT cover is
# the new name arriving from the shell, and that gap is open, not closed: see
# test_an_inherited_new_name_opt_out_does_not_make_the_cage_lenient below.
STRICT_ENV = toolchain.LEGACY_STRICT_ENV


def _seen(cage) -> str:
    return (cage.worktree / "seen.txt").read_text().strip()


# Each arm of the runner's condition, alone and together (round 1, F6). The
# export is gated on `[ -n "$CAGE_REQUIRED_TOOLS" ] || [ -n
# "$CAGE_TOOLCHAIN_PATH" ]`, and a cage may legally declare either key without
# the other: `require` alone is a project whose tools are already on the fixed
# PATH, `path` alone supplies a directory without naming a binary the run may
# not start without. Covering only the both-keys shape let either disjunct be
# deleted with every test still green, which is a require-only consumer cage
# silently losing strictness. Spelled inline rather than as a module-level
# tuple: `tests/test_guard_mutations.py`'s discovery reads every module-level
# vocabulary in this suite and demands a disposition for each, and a
# parametrize list is neither a guard's alternation nor a control table.
@pytest.mark.parametrize("declared", ["require-only", "path-only", "both"])
def test_a_declared_toolchain_makes_the_caged_session_strict(make_cage,
                                                                  tmp_path,
                                                                  declared):
    """The session inherits the contract the toml already declares.

    Read out of the SESSION, like its toolchain sibling above and for the same
    reason: the thing that skips green is `uv run pytest` running as a child
    of the caged claude, so a variable the runner sets only for itself would
    be the vacuous half of this fix.

    `require-only` names `python3`, which resolves off the runner's own fixed
    PATH — the HOST's /usr/bin/python3, not a stub: `STUBS` carries gh,
    claude, sleep and osascript, and nothing links a python3 into the cage's
    bin (round 2, F7, which corrected this sentence). The runner already
    depends on that same binary for its outcome classifier, so the arm needs
    no machinery of its own; what it needs is a required tool the pre-flight
    can RESOLVE, because an absent one stops the run before there is a
    session to read the variable out of (the arm above covers that stop) and
    `assert cage.last_row[2] != "skipped"` is what says so by name.
    """
    kwargs: dict[str, object] = {}
    if declared in ("path-only", "both"):
        toolbin = tmp_path / "toolchain"
        toolbin.mkdir()
        (toolbin / ABSENT_TOOL).write_text("#!/bin/sh\nexit 0\n")
        (toolbin / ABSENT_TOOL).chmod(0o755)
        kwargs["toolchain_path"] = [str(toolbin)]
    if declared == "require-only":
        kwargs["required_tools"] = ["python3"]
    if declared == "both":
        kwargs["required_tools"] = [ABSENT_TOOL]
    cage = make_cage(**kwargs)
    cage.session(_session_env_probe(STRICT_ENV))
    proc = cage.run()
    assert proc.returncode == 0, proc.stderr
    assert cage.last_row[2] != "skipped", (
        f"the {declared} cage never reached a session: {cage.last_row}")
    assert _seen(cage) == "1", (
        f"the caged session saw {STRICT_ENV}={_seen(cage)!r} from a cage "
        f"declaring {declared}: a cage that declares a toolchain still ran "
        "its project's suite in the lenient half, where an absent declared "
        "tool is a green skip")


def test_a_cage_that_declares_no_toolchain_stays_lenient(make_cage):
    """The other half of the same contract, and why the export is CONDITIONAL.

    Strictness is earned by the declaration, not by being inside a cage. A
    cage whose toml carries no `[toolchain]` promised nothing, so an absent
    tool there is the named skip — exporting 1 unconditionally would make the
    runner accuse every such project's machine of a breach it never
    contracted against, which is the false accusation the promised/unpromised split
    exists to avoid.
    """
    cage = make_cage()                      # no toolchain_path, no required_tools
    cage.session(_session_env_probe(STRICT_ENV))
    proc = cage.run()
    assert proc.returncode == 0, proc.stderr
    assert _seen(cage) == "<unset>", (
        f"the caged session saw {STRICT_ENV}={_seen(cage)!r} from a cage that "
        "declares no toolchain — strictness by location rather than by "
        "declaration")


def test_the_declaration_beats_an_inherited_lenient_setting(make_cage,
                                                                 tmp_path):
    """A founder-started run inherits a shell; the cage's own PATH reset a few
    lines above overrides whatever that shell carried, and this must too. An
    ambient `AGENTOPS_REQUIRE_TOOLCHAIN=0` (the old name of a contributor's
    escape hatch, per CONTRIBUTING.md) leaking into an unattended run would
    silently return the cage to the lenient half through an environment
    nothing here declares.

    This cell covers the OLD name only. The new name's opt-out,
    `NIGHTGATE_REQUIRE_TOOLCHAIN=0`, still leaks, because run.sh exports only
    the old name and `strict()` reads the new one first; the next cell pins
    that gap until agentops-hy6o.18 moves run.sh.
    """
    toolbin = tmp_path / "toolchain"
    toolbin.mkdir()
    (toolbin / ABSENT_TOOL).write_text("#!/bin/sh\nexit 0\n")
    (toolbin / ABSENT_TOOL).chmod(0o755)
    cage = make_cage(toolchain_path=[str(toolbin)], required_tools=[ABSENT_TOOL])
    cage.env[STRICT_ENV] = "0"
    cage.session(_session_env_probe(STRICT_ENV))
    proc = cage.run()
    assert proc.returncode == 0, proc.stderr
    assert _seen(cage) == "1", (
        f"an inherited {STRICT_ENV}=0 survived into the caged session "
        f"({_seen(cage)!r}) — the cage's declaration lost to the shell that "
        "happened to start it")


@pytest.mark.xfail(strict=True, reason=(
    "agentops-hy6o.18: cage/run.sh exports only the old AGENTOPS_ name, and "
    "strict() reads NIGHTGATE_REQUIRE_TOOLCHAIN first, so an inherited new-name "
    "opt-out wins inside the cage. run.sh is a forbidden path for an agent; "
    "when it moves this cell XPASSes, which strict=True turns red so the "
    "marker comes off with the fix"))
def test_an_inherited_new_name_opt_out_does_not_make_the_cage_lenient(
        make_cage, tmp_path):
    """The gap the old-name cell above cannot see, judged the way the suite
    inside the cage would judge it: `toolchain.strict()` over the session's own
    view of both names. A founder's shell carrying the opt-out CONTRIBUTING.md
    now advertises must not follow a run into a cage that declared a toolchain.
    """
    toolbin = tmp_path / "toolchain"
    toolbin.mkdir()
    (toolbin / ABSENT_TOOL).write_text("#!/bin/sh\nexit 0\n")
    (toolbin / ABSENT_TOOL).chmod(0o755)
    cage = make_cage(toolchain_path=[str(toolbin)], required_tools=[ABSENT_TOOL])
    cage.env[toolchain.STRICT_ENV] = "0"
    names = (toolchain.STRICT_ENV, toolchain.LEGACY_STRICT_ENV)
    cage.session("".join(
        f'printf "%s=%s\\n" {n} "${{{n}-}}" >> seen.txt\n' for n in names))
    proc = cage.run()
    assert proc.returncode == 0, proc.stderr
    seen = dict(line.split("=", 1) for line in _seen(cage).splitlines())
    assert set(seen) == set(names), seen
    assert toolchain.strict({k: v for k, v in seen.items() if v}) is True, (
        f"the caged session saw {seen} and its suite would run LENIENT — an "
        f"inherited {toolchain.STRICT_ENV}=0 beat the cage's declaration")


def test_a_python3_that_cannot_run_stops_the_run(cage):
    # the outcome classifier parses the session's result event with python3;
    # the stub on the runner's PATH head shadows the real one, as a broken
    # Command Line Tools shim would
    python3 = cage.bin / "python3"
    python3.write_text("#!/bin/sh\nexit 1\n")
    python3.chmod(0o755)
    cage.assert_skipped(cage.run(), "python3 cannot run")


def test_headless_gh_auth_failure_stops_the_run(cage):
    cage.ctl_set("gh_auth_exit", "1")
    cage.assert_skipped(cage.run(), "gh auth unavailable headless")


def test_unreachable_github_api_stops_the_run(cage):
    cage.ctl_set("gh_api_exit", "1")
    cage.assert_skipped(cage.run(), "GitHub API unreachable")


def test_failing_platform_probe_stops_the_run_before_a_bead_is_claimed(make_cage):
    cage = make_cage(platform_probe="test -f NOT-THERE")
    cage.assert_skipped(cage.run(), "platform probe failed")


def test_passing_platform_probe_runs_in_the_live_repo(make_cage):
    # the probe is eval'd in the live checkout — README.md only exists there
    cage = make_cage(platform_probe="test -f README.md")
    proc = cage.run()
    assert proc.returncode == 0
    assert cage.last_row[2] == "done", cage.last_row


# ---------- quiet hours / wake coalescing -----------------------------------

def test_quiet_hours_stop_the_run(make_cage):
    cage = make_cage(quiet_hours=ALWAYS_QUIET)
    cage.assert_skipped(cage.run(), "a human may be working (wake-coalesced fire?)")


def test_quiet_hours_that_cross_midnight_stop_the_run(make_cage):
    """An overnight window's shape, now just one interval among many.

    Real declarable hours rather than the sentinel, so the `start > end` arm
    of the runner's own interval test is the thing being executed.
    """
    cage = make_cage(quiet_hours=NINE_TO_FIVE_QUIET)
    cage.freeze_hour(QUIET_HOUR)
    cage.assert_skipped(cage.run(), "quiet hours 17:00-9:00")


# The hours where an off-by-one lives: each boundary of a 9-5 cage's quiet
# hours, its neighbours, and both sides of midnight.
BOUNDARY_HOURS = (0, 8, 9, 10, 16, 17, 18, 23)


@pytest.mark.parametrize("start, end", [
    (17, 9),   # a nine-to-five cage: quiet from home time to start of work
    (9, 17),   # its inverse: quiet through the working day
    (0, 0),    # the empty interval: never quiet
])
def test_the_runners_quiet_interval_agrees_with_the_configs(make_cage, start, end):
    """One rule, two implementations — bash in run.sh, Python in
    cage.config.in_quiet_hours() — and the config uses its copy to reject a
    schedule that would always be quieted. If they disagree, a cage either
    refuses a fire time that would have worked or accepts one that never
    runs, and nothing else in the suite would notice.
    """
    cage = make_cage(quiet_hours=(start, end))
    for hour in BOUNDARY_HOURS:
        cage.freeze_hour(hour)
        proc = cage.run("--check")
        assert proc.returncode == 0, proc.stderr
        quiet = cage_config.in_quiet_hours(hour, start, end)
        stops = "would SKIP: quiet hours" in proc.stdout
        assert stops is quiet, (
            f"{start}:00-{end}:00 at {hour:02d}:00 — runner "
            f"{'stopped' if stops else 'ran'}, config says "
            f"{'quiet' if quiet else 'not quiet'}: {proc.stdout!r}")
        assert cage.ledger == [] and cage.report == ""   # --check mutates nothing


def test_quiet_hours_elsewhere_in_the_day_let_the_run_through(make_cage):
    """A 9-5 cage is the point of this change: outside the declared quiet
    interval a run proceeds, whatever the wall clock says."""
    cage = make_cage(quiet_hours=NINE_TO_FIVE_QUIET)
    cage.freeze_hour(WORKING_HOUR)          # midday: the cage's working hours
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", cage.last_row
    assert "quiet hours" not in cage.report


def test_quiet_hours_declared_off_never_stop_a_run(make_cage):
    # start == end is the empty interval — the guard is off, by declaration
    cage = make_cage(quiet_hours=NEVER_QUIET)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", cage.last_row


def test_unreadable_quiet_hours_are_fatal_not_ignored(make_cage):
    """A bound the runner cannot parse must not evaluate to 'not quiet'.

    `[ "$HOUR" -ge "" ]` is a bash *error* that reads as false, so a skewed
    or truncated env would silently disable the guard — the one failure mode
    a hard stop may never have.
    """
    cage = make_cage(quiet_hours=ALWAYS_QUIET)
    cage.env_line("CAGE_QUIET_START", "midnight")
    proc = cage.run()
    assert proc.returncode == 2, proc.stdout
    assert "CAGE_QUIET_START='midnight' is not an hour" in proc.stderr
    assert not cage.stub_log("claude.argv"), "the session ran on an unreadable guard"


def test_global_run_now_override_is_consumed_by_a_real_run(make_cage):
    cage = make_cage(quiet_hours=ALWAYS_QUIET)
    override = cage.touch(".cage-run-now")
    proc = cage.run()
    assert proc.returncode == 0
    assert not override.exists(), "the one-shot override survived a real run"
    assert "quiet-hours override consumed" in cage.notifications
    assert cage.last_row[2] == "done", cage.last_row


def test_project_run_now_override_is_consumed_by_a_real_run(make_cage):
    cage = make_cage(quiet_hours=ALWAYS_QUIET)
    override = cage.touch(".cage-sampleproj-run-now")
    assert cage.run().returncode == 0
    assert not override.exists()
    assert str(override) in cage.notifications


def test_the_override_is_not_consumed_when_nothing_was_quiet(make_cage):
    """The override bypasses quiet hours and nothing else, so a run that was
    never quieted must leave it unspent for the run that needs it."""
    cage = make_cage(quiet_hours=NEVER_QUIET)
    override = cage.touch(".cage-run-now")
    assert cage.run().returncode == 0
    assert override.exists(), "a run that was never quieted burned the override"


def test_check_mode_never_consumes_the_override(make_cage):
    cage = make_cage(quiet_hours=ALWAYS_QUIET)
    override = cage.touch(".cage-run-now")
    proc = cage.run("--check")
    assert proc.returncode == 0
    assert "pre-flight OK" in proc.stdout and "quiet hours ok" in proc.stdout
    assert override.exists(), "--check burned the one-shot override"
    assert cage.ledger == [] and cage.report == ""
    assert not cage.stub_log("claude.argv")


def test_a_nine_to_five_cage_enrolls_and_its_quiet_hours_still_fire(make_cage, tmp_path):
    """A nine-to-five cage enrolls, and its quiet hours still fire.

    Config half: 09:00 with quiet hours 17->9 is not a `[schedule] window
    must cross midnight` error. It validates and enrolls, and the launchd
    trigger it renders fires in the morning.

    Runner half: the quiet-hours guard still stops a run during the hours a
    human declared theirs — executed, not read.
    """
    cage_toml = tmp_path / "nine-to-five" / "cage.toml"
    cage_toml.parent.mkdir()
    cage_toml.write_text(f"""\
[project]
name = "dayshift"
github = "acme/dayshift"
live_checkout = "{tmp_path}/day-live"
worktree = "{tmp_path}/day-run"

[triggers.schedule]
hour = 9
minute = 0

[quiet_hours]
start = 17
end = 9

[gate]
forbidden_paths = []
""")
    cfg = cage_config.load(cage_toml)                    # validates
    written = cage_render.enroll(cfg)                    # enrolls
    plist = cage_toml.parent / f"{cfg.label}.plist"
    assert plist in written and plist.is_file()
    assert plistlib.loads(plist.read_bytes())["StartCalendarInterval"] == \
        {"Hour": 9, "Minute": 0}

    runner_cage = make_cage(quiet_hours=NINE_TO_FIVE_QUIET)
    runner_cage.freeze_hour(QUIET_HOUR)                  # 20:00, after work
    runner_cage.assert_skipped(runner_cage.run(), "a human may be working")
    assert not runner_cage.worktree.exists(), "a quieted run still touched the worktree"

    # ...and the same cage runs during the hours it declared its own
    runner_cage.freeze_hour(WORKING_HOUR)
    assert runner_cage.run().returncode == 0
    assert runner_cage.last_row[2] == "done", runner_cage.last_row


# ---------- back-pressure and main-green ------------------------------------

def test_open_run_prs_cap_the_loop(make_cage):
    cage = make_cage(max_open_prs=2)
    cage.ctl_set("open_prs", "2")
    cage.assert_skipped(cage.run(), "2 auto/* PRs already open")


def test_pr_count_below_the_cap_proceeds(make_cage):
    cage = make_cage(max_open_prs=2)
    cage.ctl_set("open_prs", "1")
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done"


def test_unreadable_pr_count_fails_closed_at_the_cap(cage):
    # `|| echo 99` — a gh that cannot answer must cap the loop, not open it
    cage.ctl_set("gh_prlist_exit", "1")
    cage.assert_skipped(cage.run(), "99 auto/* PRs already open")


def test_red_main_stops_the_run(cage):
    cage.ctl_set("main_state", "failure")
    cage.assert_skipped(cage.run(), "main CI is 'failure', not success")


def test_unknown_main_state_stops_the_run(cage):
    cage.ctl_set("gh_runlist_exit", "1")
    cage.assert_skipped(cage.run(), "main CI is 'unknown', not success")


# ---------- worktree isolation and the host-artifact scrub -------------------

def test_the_live_checkout_is_never_touched(cage):
    (cage.live / "README.md").write_text("# locally edited, mid-review\n")
    (cage.live / "scratch.txt").write_text("untracked work in progress\n")
    proc = cage.run()
    assert proc.returncode == 0
    assert (cage.live / "README.md").read_text() == "# locally edited, mid-review\n"
    assert (cage.live / "scratch.txt").is_file()
    live_head = cage._git(cage.live, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert live_head == "main", "the runner moved the live checkout off main"
    # the session ran in the dedicated worktree, on a run-id branch
    assert Path(cage.stub_log("claude.cwd").strip()).resolve() == cage.worktree.resolve()
    assert re.fullmatch(RUN_BRANCH_RE, cage.branch), cage.branch


def test_host_artifacts_are_scrubbed_before_the_session(make_cage):
    # tracked in origin/main, so `clean -fdq` would NOT remove it: only the
    # scrub list can, and it must happen before the session sees the worktree
    cage = make_cage(scrub_paths=["_bmad-output"],
                     seed_files={"_bmad-output/leaked.md": "host artifact\n"})
    cage.session('[ -e _bmad-output ] && echo PRESENT > "$CTL/scrub.probe" '
                 '|| echo ABSENT > "$CTL/scrub.probe"\n')
    assert cage.run().returncode == 0
    assert cage.stub_log("scrub.probe").strip() == "ABSENT"
    assert not (cage.worktree / "_bmad-output").exists()
    assert (cage.live / "_bmad-output" / "leaked.md").is_file()  # host copy intact


def test_an_unreachable_remote_stops_the_run(cage):
    # never build on a stale base: fetch failing is a stop, not a warning
    shutil.rmtree(cage.origin)
    cage.assert_skipped(cage.run(), "git fetch failed")
    assert not cage.worktree.exists()


def test_a_worktree_that_cannot_be_created_stops_the_run(cage):
    cage.worktree.write_text("not a directory\n")   # `git worktree add` refuses
    cage.assert_skipped(cage.run(), "worktree create failed")


def test_a_worktree_that_cannot_be_reset_stops_the_run(cage):
    cage.worktree.mkdir()                            # a dir, but not a checkout
    (cage.worktree / "junk").write_text("x")
    cage.assert_skipped(cage.run(), "worktree branch reset failed")


def test_a_missing_resume_branch_stops_the_run(cage):
    assert cage.run().returncode == 0                # run 1 builds the worktree
    (cage.dir / "resume-state").write_text(
        "bead=agentops-r7\nbranch=auto/never-existed\nattempt=0\n")
    (cage.ctl / "claude.argv").unlink()               # assert_skipped: no session
    cage.assert_skipped(cage.run(), "resume branch auto/never-existed missing")


def test_a_reused_worktree_is_reset_and_cleaned(cage):
    cage.session('echo dirt > junk.txt\n' + SESSION_OK)
    assert cage.run().returncode == 0
    assert (cage.worktree / "junk.txt").is_file()
    cage.session(SESSION_OK)
    assert cage.run().returncode == 0
    assert not (cage.worktree / "junk.txt").exists(), "clean -fdq did not fire"


# ---------- declared services (only run when a services.sh exists) ----------

def test_a_service_the_machine_cannot_provide_stops_the_run(make_cage):
    # read-only pre-flight: the run dies BEFORE a bead is claimed, never
    # halfway through a session
    cage = make_cage(services=True)
    cage.ctl_set("svc_preflight_fail", "1")
    cage.assert_skipped(cage.run(), "service pre-flight: port 54329 in use and not ours")
    assert cage.stub_log("services.log").split() == ["preflight"]   # never started


def test_a_service_that_fails_to_start_stops_the_run_and_tears_down(make_cage):
    cage = make_cage(services=True)
    cage.ctl_set("svc_start_fail", "1")
    cage.assert_skipped(cage.run(), "service start failed")
    assert cage.stub_log("services.log").split() == ["preflight", "start", "stop"]


def test_services_reach_the_session_and_are_always_torn_down(make_cage):
    cage = make_cage(services=True)
    cage.session('echo "url=$CAGE_DATABASE_URL" > "$CTL/svc.env"\n' + SESSION_OK)
    assert cage.run().returncode == 0
    # started in the runner's own shell, so its exports reach the session
    assert "postgresql://fixture" in cage.stub_log("svc.env")
    assert cage.stub_log("services.log").split() == ["preflight", "start", "stop"]


def test_check_mode_runs_service_preflight_but_starts_nothing(make_cage):
    cage = make_cage(services=True)
    proc = cage.run("--check")
    assert proc.returncode == 0
    assert "services=preflight-ok" in proc.stdout
    assert cage.stub_log("services.log").split() == ["preflight"]


# ---------- the pure-bash watchdog ------------------------------------------

def test_watchdog_terminates_a_session_that_outlives_its_timeout(make_cage):
    cage = make_cage(timeout_secs=60)   # 2 compressed 30s polls
    cage.arm_gate()
    cage.session(
        TERM_TRAP
        + 'touch "$CTL/session_ready"\n'
        + SLOW_LOOP
        + 'touch "$CTL/ran_to_completion"\n')
    proc = cage.run(timeout=90)
    assert proc.returncode == 0
    signals = cage.stub_log("signals")
    assert "TERM" in signals, "the watchdog never signalled"
    assert "COMPLETED_BEFORE_TERM" not in signals, (
        "the session had already run to completion when its TERM handler "
        "ran — the watchdog ordering defect, named by the trap itself")
    assert not (cage.ctl / "ran_to_completion").exists()
    assert cage.last_row[2] == "timeout", cage.last_row
    assert "- outcome: timeout" in cage.report
    assert "timeout" in cage.notifications
    # the cadence the runner asked for, unscaled: 30s polls, TIMEOUT_SECS/30
    assert cage.stub_log("sleep.log").splitlines()[:2] == ["30", "30"]


def test_watchdog_escalates_to_kill_after_the_grace_period(make_cage):
    cage = make_cage(timeout_secs=60)
    cage.arm_gate()
    cage.session(
        'trap \'echo TERM >> "$CTL/signals"\' TERM\n'      # caught and ignored
        'touch "$CTL/session_ready"\n'
        + SLOW_LOOP
        + 'touch "$CTL/ran_to_completion"\n')
    proc = cage.run(timeout=120)
    assert proc.returncode == 0
    assert "TERM" in cage.stub_log("signals")
    assert not (cage.ctl / "ran_to_completion").exists(), "the session outlived the cage"
    # grace is polled at 15s; reaching it at all proves TERM did not suffice
    assert "15" in cage.stub_log("sleep.log").splitlines()
    assert cage.last_row[2] == "timeout", cage.last_row


# The subshell-bounded loop shape, byte for byte. Kept as
# the reproduction's control: it must fall through under the watchdog's two
# kills, or the harness has stopped reproducing and proves nothing.
SUBSHELL_BOUNDED_LOOP = 'for _ in $(seq 1 600); do "$REAL_SLEEP" 0.05; done\n'


def _slow_seq(tmp: Path) -> Path:
    """A PATH dir whose `seq` holds its output back for half a second, then
    execs the real one. The subshell running `seq` lives for a few
    milliseconds, so the watchdog's `pkill -P` lands inside them only under
    load; this widens that window without changing the session's own text,
    so whatever loop a test hands the harness runs exactly as written."""
    bindir = tmp / "slow-seq-bin"
    bindir.mkdir(parents=True, exist_ok=True)
    real_seq = shutil.which("seq") or "/usr/bin/seq"
    stub = bindir / "seq"
    stub.write_text(f'#!/bin/bash\n{shlex.quote(REAL_SLEEP)} 0.5\n'
                    f'exec {shlex.quote(real_seq)} "$@"\n')
    stub.chmod(0o755)
    return bindir


def _term_like_the_watchdog(body: str, tmp: Path, gap: float) -> tuple[str, bool]:
    """Run a session `body` under bash and stop it with the two lines run.sh's
    watchdog uses — `pkill -TERM -P "$CPID"` then `kill -TERM "$CPID"` — with
    `gap` seconds between them. Returns (signals log, whether the session
    touched its completion marker). The gap is the loaded-runner window,
    made deterministic: the few milliseconds between the two kills landing
    and the parent reaping its dead child."""
    ctl = tmp / "ctl"
    ctl.mkdir(parents=True, exist_ok=True)
    script = tmp / "session.sh"
    # The tail sleep keeps the session alive after its completion marker,
    # which is the other loaded-runner window made deterministic: the
    # parent's TERM can land while `touch` is still the foreground command.
    script.write_text(TERM_TRAP + 'touch "$CTL/session_ready"\n' + body
                      + 'touch "$CTL/ran_to_completion"\n'
                      + '"$REAL_SLEEP" 3\n')
    env = {**os.environ, "CTL": str(ctl), "REAL_SLEEP": REAL_SLEEP,
           "PATH": f"{_slow_seq(tmp)}{os.pathsep}{os.environ.get('PATH', '')}"}
    proc = subprocess.Popen(["bash", str(script)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while not (ctl / "session_ready").exists():
        assert time.monotonic() < deadline, "the session never became ready"
        assert proc.poll() is None, "the session died before it was ready"
        time.sleep(0.01)
    time.sleep(0.1)
    subprocess.run(["pkill", "-TERM", "-P", str(proc.pid)], check=False)
    time.sleep(gap)
    try:
        os.kill(proc.pid, 15)
    except ProcessLookupError:
        pass
    proc.wait(timeout=60)
    signals = (ctl / "signals").read_text() if (ctl / "signals").exists() else ""
    return signals, (ctl / "ran_to_completion").exists()


def test_a_session_loop_bounded_by_a_subshell_falls_through_when_the_watchdog_kills_its_children(
        tmp_path):
    """Named regression, the ordering defect itself and not a
    widened timeout or a retry (a retry is ruled out: it hides
    the ordering it should prove).

    A watchdog session can log TERM and still leave `ran_to_completion`
    present. A loop bounded by `for _ in $(seq 1 600)` runs the command
    substitution in a SUBSHELL, a child of the session, so it is what
    `pkill -TERM -P "$CPID"` kills first; a subshell resets the session's
    trap, so the kill ends the expansion with an empty word list; the loop
    runs zero times and the session touches its marker before the parent's
    own `kill -TERM` lands — and THEN the trap logs TERM. Both halves of the
    contradiction, in that order.

    Unaided, the window is the milliseconds between the two kills. Here the
    `seq` on PATH holds its output for half a second, so the subshell is
    alive when pkill lands, and the second kill is delayed: the reproduction
    is ordered by construction while each session runs its loop TEXT
    unchanged. So the control is the literal subshell-bounded loop, and the
    subject is `SLOW_LOOP` itself — put the literal subshell loop back into
    `SLOW_LOOP` and this test goes red.
    """
    signals, completed = _term_like_the_watchdog(
        SUBSHELL_BOUNDED_LOOP, tmp_path / "old", 0.8)
    assert completed and "COMPLETED_BEFORE_TERM" in signals, (
        "the subshell-bounded loop no longer falls through under the "
        "watchdog's two kills — this reproduction has stopped reproducing, "
        f"so it proves nothing about the fix: signals={signals!r}")

    signals, completed = _term_like_the_watchdog(SLOW_LOOP, tmp_path / "new", 0.8)
    assert "TERM" in signals and "COMPLETED_BEFORE_TERM" not in signals, signals
    assert not completed, (
        "the counter-bounded session ran to completion under the watchdog's "
        "two kills — SLOW_LOOP has regressed to a shape the kill truncates")


# ---------- the usage-limit classifier --------------------------------------

# Stream-json `result` events, trimmed from session logs a real cage recorded
# (2026-08-20 and 2026-08-24 for the 429; 2026-08-21 for the sleep). Every
# string value in a stream-json line is JSON-escaped, so the prompt's words
# can appear in the log but an unescaped `"api_error_status":429` key cannot.
RESULT_SESSION_LIMIT_429 = (
    '{"is_error":true,"duration_api_ms":0,"num_turns":1,'
    '"stop_reason":"stop_sequence","total_cost_usd":0,"permission_denials":[],'
    '"terminal_reason":"api_error","subtype":"success","api_error_status":429,'
    '"result":"You\'ve hit your session limit · resets 1:30am '
    '(America/Los_Angeles)","type":"result","duration_ms":719}')
RESULT_LAPTOP_SLEEP = (
    '{"is_error":true,"num_turns":26,"stop_reason":"stop_sequence",'
    '"permission_denials":[],"terminal_reason":"api_error","subtype":"success",'
    '"api_error_status":null,"result":"API Error: Your computer went to sleep '
    'mid-response. The response above may be incomplete.","type":"result"}')
RESULT_COMPLETED = (
    '{"is_error":false,"num_turns":22,"stop_reason":"end_turn",'
    '"permission_denials":[],"terminal_reason":"completed","subtype":"success",'
    '"api_error_status":null,"result":"Run complete: nothing hit a usage limit '
    'or was rate-limited until reset.","type":"result"}')
# The injected prompt, echoed back as the session's first user event.
PROMPT_ECHO = (
    '{"type":"user","message":{"role":"user","content":[{"type":"text",'
    '"text":"If `.run-resume` exists, a previous run was\\ninterrupted '
    '(timeout or usage limit) mid-bead. Claude usage limit reached, resets at '
    '3am; you are rate-limited until 04:00 UTC."}]}}')
DENIED_PUSH = "git push -u origin auto/20260823-233004"


def _result_denying(*commands: str, completed: bool = True) -> str:
    denials = ",".join(
        '{"tool_name":"Bash","tool_use_id":"toolu_%02d","tool_input":'
        '{"command":%s,"description":"Push the run branch"}}' % (i, json.dumps(c))
        for i, c in enumerate(commands))
    return ('{"is_error":false,"num_turns":201,"stop_reason":"end_turn",'
            f'"permission_denials":[{denials}],'
            '"terminal_reason":"completed","subtype":"success",'
            '"api_error_status":null,"result":"Blocked: the push was denied.",'
            '"type":"result"}')


def _emit(cage: Cage, *lines: str, exit_code: int = 0, extra: str = "") -> None:
    """A session whose log is exactly these stream-json lines."""
    cage.ctl_set("session.jsonl", "\n".join(lines) + "\n")
    cage.session(extra + 'cat "$CTL/session.jsonl"\n' + f"exit {exit_code}\n")


def test_a_session_limit_429_is_classified_usage_limit(cage):
    _emit(cage, PROMPT_ECHO, RESULT_SESSION_LIMIT_429, exit_code=1)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "usage-limit", cage.last_row
    assert "- outcome: usage-limit" in cage.report


CLASSIFIER_FAILED = "outcome classifier FAILED"


def _break_classifier_python(cage: Cage) -> None:
    # passes the runner's import probe and runs the usage pre-flight for real;
    # the classifier (handed the session log) prints a usage-limit line and
    # then fails, so reading a failed classifier's output bites
    python3 = cage.bin / "python3"
    python3.write_text(
        '#!/bin/sh\n'
        'case "$1 $2" in "- "*.session.log) printf "usage-limit\\t\\n"; exit 3 ;; esac\n'
        f'exec {shlex.quote(sys.executable)} "$@"\n')
    python3.chmod(0o755)


def _replace_own_log_with_a_directory(cage: Cage) -> str:
    cage.ctl_set("reports_dir", str(cage.dir / "reports"))
    return ('L=$(ls -t "$(cat "$CTL/reports_dir")"/*.session.log | head -1)\n'
            'rm -f "$L" && mkdir "$L"\n')


@pytest.mark.parametrize("failure", ["python3-exits-non-zero", "unreadable-log"])
@pytest.mark.parametrize("exit_code,expected", [(0, "done"), (1, "failed(1)")])
def test_a_classifier_that_fails_never_files_usage_limit(
        cage, failure, exit_code, expected):
    extra = ""
    if failure == "python3-exits-non-zero":
        _break_classifier_python(cage)
    else:
        extra = _replace_own_log_with_a_directory(cage)
    _emit(cage, RESULT_SESSION_LIMIT_429, exit_code=exit_code,
          extra='printf "phase: build\\nbead: agentops-r9\\n" > .run-progress\n'
                + extra)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == expected, cage.last_row
    assert not (cage.dir / "resume-state").exists(), "a failed classifier resumed the run"


@pytest.mark.parametrize("failure", ["python3-exits-non-zero", "unreadable-log"])
def test_a_classifier_that_fails_says_neither_label_was_evaluated(cage, failure):
    extra = ""
    if failure == "python3-exits-non-zero":
        _break_classifier_python(cage)
    else:
        extra = _replace_own_log_with_a_directory(cage)
    _emit(cage, _result_denying(DENIED_PUSH), extra=extra)
    assert cage.run().returncode == 0
    row = cage.last_row
    assert CLASSIFIER_FAILED in row[4], row
    assert "usage-limit and push-denied not evaluated" in row[4], row
    assert f"- {CLASSIFIER_FAILED}" in cage.report, cage.report


# A tool result quoting a whole 429 result event, as a session reading an
# old log would: stream-json escapes it inside the string value.
TOOL_OUTPUT_QUOTING_A_429 = json.dumps({
    "type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "toolu_q",
        "content": RESULT_SESSION_LIMIT_429 + "\nusage limit"}]}})


@pytest.mark.parametrize("lines,exit_code,expected", [
    ((PROMPT_ECHO, RESULT_COMPLETED), 0, "done"),
    ((PROMPT_ECHO, RESULT_LAPTOP_SLEEP), 1, "failed(1)"),
    ((PROMPT_ECHO, TOOL_OUTPUT_QUOTING_A_429, RESULT_COMPLETED), 0, "done"),
], ids=["prompt-echo-completed", "laptop-sleep", "tool-output-quotes-a-429"])
def test_a_log_echoing_the_prompts_usage_limit_words_is_not_usage_limit(
        cage, lines, exit_code, expected):
    _emit(cage, *lines, exit_code=exit_code)
    assert cage.run().returncode == 0
    assert "usage limit" in cage.runlog and "rate-limited until" in cage.runlog
    assert cage.last_row[2] == expected, cage.last_row


@pytest.mark.parametrize("exit_code", [0, 1])
def test_a_denied_push_is_recorded_as_push_denied_naming_the_push(cage, exit_code):
    _emit(cage, PROMPT_ECHO,
          _result_denying("ls -la", "grep -rn git push docs", DENIED_PUSH),
          exit_code=exit_code,
          extra='printf "bead: agentops-r1\\noutcome: blocked\\n'
                'notes: finished; push denied\\n" > .run-summary\n')
    assert cage.run().returncode == 0
    row = cage.last_row
    assert row[2] == "push-denied", row
    assert row[1] == "agentops-r1"
    assert f"push denied: {DENIED_PUSH}" in row[4], row
    assert "grep -rn" not in row[4], row
    assert "- outcome: push-denied" in cage.report


@pytest.mark.parametrize("command", [
    pytest.param('git add -A && git commit -m "feat: x" && git push -u origin auto/x',
                 id="and-list"),
    pytest.param("cd /w\ngit push origin auto/x", id="newline"),
    pytest.param("git -C /w push origin auto/x", id="dash-C"),
    pytest.param('git -C "/w s" push origin auto/x', id="quoted-dash-C-path-with-space"),
    pytest.param("git -c core.x=y push origin auto/x", id="dash-c-config"),
    pytest.param("  git push origin auto/x", id="leading-whitespace"),
    pytest.param("{ git push origin auto/x; }", id="brace-group"),
    pytest.param("if true; then git push origin auto/x; fi", id="then"),
    pytest.param("env X=1 git push origin auto/x", id="env-prefix"),
    pytest.param("X=1 git push origin auto/x", id="assignment-prefix"),
    pytest.param("$(git push origin auto/x)", id="command-substitution"),
    pytest.param("cd /w && git \\\n  push origin auto/x", id="line-continuation"),
])
def test_a_denied_push_inside_a_compound_command_is_push_denied(cage, command):
    _emit(cage, _result_denying(command))
    assert cage.run().returncode == 0
    row = cage.last_row
    assert row[2] == "push-denied", row
    assert "origin auto/x" in row[4], row


@pytest.mark.parametrize("command", [
    pytest.param('echo "x; git push origin auto/x"', id="double-quoted-after-separator"),
    pytest.param("printf '%s\\n' 'a && git push origin auto/x'",
                 id="single-quoted-after-separator"),
])
def test_a_push_inside_a_quoted_string_is_not_a_denied_push(cage, command):
    _emit(cage, _result_denying(command))
    assert cage.run().returncode == 0
    row = cage.last_row
    assert row[2] == "done", row
    assert "push denied" not in row[4], row


def _assert_not_a_denied_push(cage: Cage, command: str) -> None:
    _emit(cage, _result_denying(command))
    assert cage.run().returncode == 0
    row = cage.last_row
    assert row[2] == "done", row
    assert "push denied" not in row[4], row


def _assert_a_denied_push(cage: Cage, command: str) -> None:
    _emit(cage, _result_denying(command))
    assert cage.run().returncode == 0
    row = cage.last_row
    assert row[2] == "push-denied", row
    assert "push denied:" in row[4], row


@pytest.mark.parametrize("command", [
    pytest.param("cat <<'EOF' > notes.md\ngit push origin main\nEOF", id="quoted-delimiter"),
    pytest.param("cat <<EOF\ngit push origin main\nEOF\nls", id="bare-delimiter"),
    pytest.param("cat <<-\"EOF\"\n\tgit push origin main\n\tEOF", id="tab-stripped"),
    pytest.param("cat <<'EOF'\ndon't git push origin main\nEOF", id="body-with-apostrophe"),
    pytest.param("cat <<A <<B\ngit push origin main\nA\ngit push origin main\nB",
                 id="two-heredocs"),
    pytest.param("echo $((1<<2)); cat <<EOF\ngit push origin main\nEOF",
                 id="heredoc-after-arithmetic"),
])
def test_a_push_in_a_heredoc_body_is_not_a_denied_push(cage, command):
    _assert_not_a_denied_push(cage, command)


# Each is a literal `$[` to bash, not arithmetic, so the `<<` after it opens a
# heredoc and bash prints the push line rather than running it. A `$[` counter
# that fires on a quoted or escaped `$` stays above zero for the rest of the
# command, and every later heredoc body is read as commands.
@pytest.mark.parametrize("command", [
    pytest.param("echo \\$[ ; cat <<EOF\ngit push origin main\nEOF", id="backslash-escaped"),
    pytest.param('echo "$"[ ; cat <<EOF\ngit push origin main\nEOF', id="double-quoted"),
    pytest.param("echo '$'[ ; cat <<EOF\ngit push origin main\nEOF", id="single-quoted"),
    pytest.param("echo $$[ ; cat <<EOF\ngit push origin main\nEOF", id="after-dollar-dollar"),
    # control: the old counter got this right too, because `]` closed it
    pytest.param("echo \\$[USD] ; cat <<EOF\ngit push origin main\nEOF", id="escaped-and-matched-control"),
])
def test_a_quoted_or_escaped_dollar_bracket_leaves_a_later_heredoc_body_unread(cage, command):
    _assert_not_a_denied_push(cage, command)


def test_a_closed_dollar_bracket_leaves_a_later_heredoc_body_unread(cage):
    # the `<<` inside `$[...]` shifts; the `]` must close it, or the `<<` after
    # it stops opening a heredoc
    _assert_not_a_denied_push(cage, "echo $[1<<2]; cat <<EOF\ngit push origin main\nEOF")


@pytest.mark.parametrize("command", [
    pytest.param("ls # note; git push origin auto/x", id="trailing-comment"),
    pytest.param("# git push origin auto/x\nls", id="comment-line"),
    pytest.param("ls;# git push origin auto/x", id="comment-after-separator"),
    pytest.param("'2'>x git push origin auto/x", id="quoted-digit-is-a-command"),
])
def test_a_push_after_a_comment_is_not_a_denied_push(cage, command):
    _assert_not_a_denied_push(cage, command)


@pytest.mark.parametrize("command", [
    pytest.param("cat <<'EOF' > n.md\nbody\nEOF\ngit push origin auto/x", id="after-heredoc"),
    pytest.param("ls # note\ngit push origin auto/x", id="after-comment-line"),
    pytest.param("echo a#b; git push origin auto/x", id="hash-inside-a-word"),
    pytest.param("cat <<EOF\r\nbody\r\nEOF\r\ngit push origin auto/x", id="crlf-heredoc"),
    pytest.param("cat <<-EOF\n\tbody\n\tEOF\ngit push origin auto/x",
                 id="after-tab-stripped-heredoc"),
    pytest.param("cat <<< EOF\ngit push origin auto/x", id="after-here-string"),
])
def test_a_push_after_a_heredoc_or_comment_is_still_push_denied(cage, command):
    _assert_a_denied_push(cage, command)


@pytest.mark.parametrize("command", [
    pytest.param("2>/dev/null git push origin auto/x", id="fd-redirect"),
    pytest.param(">/dev/null 2>&1 git push origin auto/x", id="two-redirects"),
    pytest.param("cd /w && 2> err.txt git push origin auto/x", id="after-separator"),
    pytest.param("true&&>x git push origin auto/x", id="separator-joined-to-redirect"),
])
def test_a_push_behind_a_leading_redirect_is_push_denied(cage, command):
    _assert_a_denied_push(cage, command)


@pytest.mark.parametrize("command", [
    pytest.param("echo $((1<<2))\ngit push origin auto/x", id="dollar-double-paren"),
    pytest.param("x=$(( 1 << 20 ))\ngit push origin auto/x", id="spaced-dollar-double-paren"),
    pytest.param("(( y = 1 << 3 ))\ngit push origin auto/x", id="arithmetic-command"),
    pytest.param("echo $[1<<2]\ngit push origin auto/x", id="dollar-bracket"),
    pytest.param("echo $(( (1<<2) + 1 ))\ngit push origin auto/x", id="nested-parens"),
    # bash joins the continuation before it expands, so this is `$[1<<2]`
    pytest.param("echo $\\\n[1<<2]\ngit push origin auto/x", id="dollar-bracket-across-a-line-continuation"),
])
def test_a_push_after_an_arithmetic_shift_is_still_push_denied(cage, command):
    _assert_a_denied_push(cage, command)


def test_a_push_after_parens_nested_in_arithmetic_is_still_push_denied(cage):
    # the inner `(1)` and `(2)` close before the shift, so the `<<` is still
    # arithmetic only while parens inside `$(( ))` are counted
    _assert_a_denied_push(cage, "echo $(( (1) + (2) + (1<<2) ))\ngit push origin auto/x")


@pytest.mark.parametrize("command", [
    pytest.param("git -c alias.p=push p origin auto/x", id="dash-c-alias"),
    pytest.param("git -calias.p=push p origin auto/x", id="attached-dash-c-alias"),
    pytest.param("git -c 'alias.p=push -u' p origin auto/x", id="alias-with-flags"),
    pytest.param("git -c alias.p=push P origin auto/x", id="alias-name-ignores-case"),
    pytest.param("git -c ALIAS.p=push p origin auto/x", id="alias-section-uppercase"),
    pytest.param("git -c Alias.p=push p origin auto/x", id="alias-section-mixed-case"),
])
def test_a_push_through_a_command_line_git_alias_is_push_denied(cage, command):
    _assert_a_denied_push(cage, command)


def test_a_command_line_git_alias_for_another_command_is_not_a_denied_push(cage):
    _assert_not_a_denied_push(cage, "git -c alias.p=status p origin auto/x")


def test_a_push_through_a_config_env_alias_is_a_stated_miss(cage):
    _assert_not_a_denied_push(cage, "V=push git --config-env=alias.p=V p origin auto/x")
    wiki = Path(__file__).resolve().parents[1] / "docs" / "wiki" / "The-Cage.md"
    for text in (cage_render.runner_script(), wiki.read_text()):
        assert "one read from the environment by --config-env" in text.replace(
            "`", "").replace("\n", " ")


def _result_with_denials(*denials: dict, is_error: bool = False,
                         api_error_status: int | None = None) -> str:
    """A result event with these permission_denials, compact like stream-json."""
    return json.dumps({
        "is_error": is_error, "num_turns": 12, "stop_reason": "end_turn",
        "permission_denials": list(denials), "terminal_reason": "completed",
        "subtype": "success", "api_error_status": api_error_status,
        "result": "Run complete.", "type": "result"}, separators=(",", ":"))


@pytest.mark.parametrize("exit_code,expected", [(0, "done"), (1, "failed(1)")])
def test_a_forged_nested_429_in_a_denied_tools_input_is_not_usage_limit(
        cage, exit_code, expected):
    forged = _result_with_denials(
        {"tool_name": "mcp__notes__write", "tool_use_id": "toolu_f",
         "tool_input": {"is_error": True, "api_error_status": 429}},
        {"tool_name": "Bash", "tool_use_id": "toolu_g",
         "tool_input": {"command": "true", "is_error": True, "api_error_status": 429}})
    _emit(cage, PROMPT_ECHO, forged, exit_code=exit_code)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == expected, cage.last_row


def test_a_read_tools_command_field_is_not_a_denied_push(cage):
    _emit(cage, _result_with_denials(
        {"tool_name": "Read", "tool_use_id": "toolu_r",
         "tool_input": {"file_path": "/w/notes.md",
                        "command": "git push -u origin auto/x"}}))
    assert cage.run().returncode == 0
    row = cage.last_row
    assert row[2] == "done", row
    assert "push denied" not in row[4], row


def test_a_denied_push_the_session_got_past_is_not_push_denied(cage):
    cage.ctl_set("pr_number", "31")
    _emit(cage, _result_denying(DENIED_PUSH))
    assert cage.run().returncode == 0
    row = cage.last_row
    assert (row[2], row[3]) == ("done", "31"), row
    assert f"push denied: {DENIED_PUSH}" in row[4], row


def test_a_denied_push_is_not_push_denied_when_the_branch_reached_origin(cage):
    # the PR step failed, not the push: the remote holds the session's head
    _emit(cage, _result_denying(DENIED_PUSH),
          extra='echo w > w.txt && git add -A && git commit -q -m w && '
                'git push -q origin "$(git rev-parse --abbrev-ref HEAD)"\n')
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", cage.last_row


def test_a_429_with_a_denied_push_stays_usage_limit(cage):
    limited = RESULT_SESSION_LIMIT_429.replace(
        '"permission_denials":[]',
        '"permission_denials":[{"tool_name":"Bash","tool_input":'
        '{"command":"git push origin auto/x"}}]')
    _emit(cage, limited, exit_code=1)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "usage-limit", cage.last_row


def test_each_run_gets_its_own_session_log_so_another_runs_text_cannot_lie(cage):
    # The classifier greps $RUNLOG, so that log must belong to exactly ONE run.
    # RUN_ID gives each run its own path; '>' (not '>>') keeps the file clean
    # even if an id were ever reused. Both together are what stop a previous
    # run's 429 from classifying this one.
    _emit(cage, RESULT_SESSION_LIMIT_429, exit_code=1)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "usage-limit"
    first_log = cage.session_logs[-1]

    cage.session(SESSION_OK)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", "a previous run's text classified this one"
    second_log = cage.session_logs[-1]
    assert second_log != first_log, "two runs in one day shared a session log"
    assert '"api_error_status":429' in first_log.read_text(), "run 2 overwrote run 1's log"
    assert "api_error_status" not in cage.runlog


# ---------- the usage pre-flight --------------------------------------------
#
# No documented non-interactive reading of remaining usage exists, so the
# pre-flight is a minimal probe session, and the threshold is binary: the
# probe's result event is a 429 (exhausted), a clean result (usable), or
# anything else (unreadable, which skips like exhausted).

USAGE_SKIPPED = "usage-skipped"
RATE_LIMIT_REJECTED = (
    '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
    '"resetsAt":1787646600,"rateLimitType":"five_hour"}}')


def _assert_usage_skipped(cage: Cage, proc: subprocess.CompletedProcess,
                          fragment: str, *, probe_ran: bool = True) -> list[str]:
    assert proc.returncode == 0, proc.stderr
    row = cage.last_row
    assert row[2] == USAGE_SKIPPED, row
    assert fragment in row[4], row
    assert f"SKIPPED: {fragment}" in cage.report, cage.report
    assert f"cage (sampleproj): {USAGE_SKIPPED}" in cage.notifications
    assert bool(cage.stub_log("probe.argv")) is probe_ran, cage.stub_log("probe.argv")
    assert not cage.stub_log("claude.argv"), "the session launched despite the usage check"
    assert not cage.worktree.exists(), "a usage skip touched the worktree"
    return row


def test_exhausted_usage_skips_the_run_with_its_reason_and_launches_no_session(cage):
    state = cage.dir / "resume-state"
    state.write_text("bead=agentops-r9\nbranch=auto/20260101\nattempt=1\n")
    cage.ctl_set("probe.out", RATE_LIMIT_REJECTED + "\n" + RESULT_SESSION_LIMIT_429 + "\n")
    cage.ctl_set("probe_exit", "1")
    row = _assert_usage_skipped(cage, cage.run(), "usage exhausted")
    assert "hit your session limit · resets 1:30am" in row[4], row
    assert state.is_file(), "a usage skip spent the pending resume"
    probe_logs = list((cage.dir / "reports").glob("*.usage-probe.log"))
    assert len(probe_logs) == 1 and '"api_error_status":429' in probe_logs[0].read_text()


def test_usable_usage_launches_the_session(cage):
    proc = cage.run()
    assert proc.returncode == 0, proc.stderr
    assert Path(cage.stub_log("probe.cwd").strip()).resolve() == cage.dir.resolve()
    assert cage.stub_log("claude.argv"), "usable usage did not launch the session"
    row = cage.last_row
    assert row[2] == "done", row
    assert "usage" not in row[4], row


# Every flag the probe carries is load-bearing, and none of them shows up in
# any other assertion: the stub answers the probe by its PROMPT, not by its
# flags, so a dropped flag leaves the suite green. `--verbose` is the sharpest
# of them — `claude -p` refuses `--output-format stream-json` without it ("When
# using --print, --output-format=stream-json requires --verbose"), so a probe
# that lost it would print an arg error, no result event, and skip every run as
# unreadable forever while nothing failed. The EMPTY value after `--tools` is
# what gives the probe no tools (`--tools default` would hand it every built-in
# one) and `--no-session-persistence` is what keeps it from saving a transcript
# — the two things The-Cage.md promises about it. So pin the whole argv as one
# string. The stub records it as `"$*"`, joined with single spaces, which makes
# the empty `--tools` value the DOUBLE space before `--no-session-persistence`:
# dropping the value, or filling it in, is a different string.
PROBE_ARGV = ("-p cage usage probe: reply with the single word ok "
              "--safe-mode --tools  --no-session-persistence "
              "--output-format stream-json --verbose")


def test_the_probes_argv_is_pinned_flag_for_flag(cage):
    assert cage.run().returncode == 0
    assert cage.stub_log("probe.argv").splitlines() == [PROBE_ARGV]


@pytest.mark.parametrize("case", [
    "probe-exits-with-no-output", "unparseable-output", "non-429-error-result",
    "clean-result-but-probe-failed", "probe-never-finishes", "parser-fails",
])
def test_an_unreadable_usage_check_skips_with_usage_unreadable(cage, case):
    if case == "probe-exits-with-no-output":
        cage.ctl_set("probe.out", "")
        cage.ctl_set("probe_exit", "1")
    elif case == "unparseable-output":
        cage.ctl_set("probe.out", 'Error: something went wrong\n{"type":"result",\n')
    elif case == "non-429-error-result":
        cage.ctl_set("probe.out", RESULT_LAPTOP_SLEEP.replace(
            '"api_error_status":null', '"api_error_status":401') + "\n")
        cage.ctl_set("probe_exit", "1")
    elif case == "clean-result-but-probe-failed":
        cage.ctl_set("probe_exit", "2")
    elif case == "probe-never-finishes":
        cage.env_line("USAGE_PROBE_TIMEOUT", "1")
        cage.ctl_set("probe_sleep", "30")
    else:
        python3 = cage.bin / "python3"
        python3.write_text(
            '#!/bin/sh\n'
            'case "$1 $2" in "- "*.usage-probe.log*) printf "usable\\t\\n"; exit 3 ;; esac\n'
            f'exec {shlex.quote(sys.executable)} "$@"\n')
        python3.chmod(0o755)
    started = time.monotonic()
    # the parser-fails stub stands in for the probe's own python, so no probe runs
    _assert_usage_skipped(cage, cage.run(), "usage unreadable",
                          probe_ran=case != "parser-fails")
    assert time.monotonic() - started < 20, "the probe's timeout did not stop it"
    if case == "probe-never-finishes":
        child = int(cage.stub_log("probe_child.pid"))
        alive = subprocess.run(["kill", "-0", str(child)], capture_output=True).returncode == 0
        assert not alive, "the timed-out probe's child outlived it: not killed as a group"


def test_check_mode_starts_no_usage_probe(cage):
    proc = cage.run("--check")
    assert proc.returncode == 0
    assert "pre-flight OK" in proc.stdout and "usage not probed" in proc.stdout
    assert not cage.stub_log("probe.argv"), "--check spent usage on a probe"


# ---------- the per-run permission profile ----------------------------------

def test_the_session_runs_under_a_profile_bound_to_its_own_branch(cage):
    assert cage.run().returncode == 0
    run_id = cage.last_row[8]
    profile_path = cage.dir / "reports" / f"{run_id}.profile.json"
    argv = cage.stub_log("claude.argv")
    assert f"--settings {profile_path}" in argv
    refspec = f"{cage.branch}:refs/heads/{cage.branch}"
    told = [f"git push -u origin {refspec}", f"git push origin {refspec}",
            f"git push --set-upstream origin {refspec}"]
    assert " | ".join(told) in argv
    allow = json.loads(profile_path.read_text())["permissions"]["allow"]
    pushes = sorted(rule for rule in allow if "git push" in rule)
    assert pushes == sorted(f"Bash({command})" for command in told), pushes
    assert cage_render.RUN_BRANCH_TOKEN not in profile_path.read_text()
    template = (cage.dir / "cage-profile.json").read_text()
    assert profile_path.read_text() == template.replace(
        cage_render.RUN_BRANCH_TOKEN, cage.branch)


# The session commits, then runs every push its profile allows, verbatim.
PUSH_EVERY_ALLOWED_SPELLING = r'''
echo w > w.txt && git add -A && git commit -q -m w
B=$(git rev-parse --abbrev-ref HEAD)
[ -f "$CTL/redirect_remote_push" ] && git config remote.origin.push "refs/heads/$B:refs/heads/main"
while [ $# -gt 0 ]; do [ "$1" = --settings ] && P="$2"; shift; done
python3 -c 'import json, sys
for rule in json.load(open(sys.argv[1]))["permissions"]["allow"]:
    if rule.startswith("Bash(git push"):
        print(rule[len("Bash("):-1])' "$P" > "$CTL/allowed_pushes"
while IFS= read -r C; do
  eval "$C" >> "$CTL/push.out" 2>&1 || echo "FAILED: $C" >> "$CTL/push.out"
done < "$CTL/allowed_pushes"
'''


def _origin_ref(cage: Cage, ref: str) -> str:
    return cage._git(cage.tmp, "--git-dir", str(cage.origin),
                     "rev-parse", ref).stdout.strip()


@pytest.mark.parametrize("redirect", ["push-default-upstream", "remote-origin-push"])
def test_the_allowed_run_branch_push_never_moves_main_under_redirecting_git_config(
        cage, redirect):
    # Either setting reaches a run from $HOME/.gitconfig or the consumer's
    # .git/config, and remaps a push that names only the branch.
    if redirect == "push-default-upstream":
        cage._git(cage.live, "config", "push.default", "upstream")
    else:
        cage.ctl_set("redirect_remote_push", "1")
    main_before = _origin_ref(cage, "refs/heads/main")
    cage.session(PUSH_EVERY_ALLOWED_SPELLING)
    assert cage.run().returncode == 0
    pushes = cage.stub_log("allowed_pushes").splitlines()
    assert len(pushes) == 3, pushes
    assert "FAILED" not in cage.stub_log("push.out"), cage.stub_log("push.out")
    assert _origin_ref(cage, "refs/heads/main") == main_before, (
        f"an allowed run-branch push moved origin main: {pushes}")
    head = cage._git(cage.worktree, "rev-parse", "HEAD").stdout.strip()
    assert _origin_ref(cage, f"refs/heads/{cage.branch}") == head


def test_a_profile_that_does_not_bind_the_run_branch_stops_the_run(cage):
    profile = cage.dir / "cage-profile.json"
    profile.write_text(profile.read_text().replace(
        cage_render.RUN_BRANCH_TOKEN, "auto/"))
    cage.assert_skipped(cage.run(), "cage-profile.json does not bind the run branch")


@pytest.mark.parametrize("branch", [
    "main", "auto/", "auto/x:main", "auto/x y", 'auto/x"y', "auto/x|y",
    "auto/x&y", "auto/x.lock", "Auto/x", "release/auto/x",
])
def test_a_resume_branch_outside_the_prefix_is_granted_no_push(cage, branch):
    state = cage.dir / "resume-state"
    state.write_text(f"bead=agentops-r9\nbranch={branch}\nattempt=0\n")
    cage.assert_skipped(cage.run(), f"run branch '{branch}' is not an auto/ run branch")
    assert not state.exists(), "a refused resume would refuse every later run too"
    assert not list((cage.dir / "reports").glob("*.profile.json"))
    assert not cage.worktree.exists(), "refused after the worktree was touched"


def test_check_mode_refuses_a_resume_branch_outside_the_prefix(cage):
    state = cage.dir / "resume-state"
    state.write_text("bead=agentops-r9\nbranch=auto/x:main\nattempt=0\n")
    proc = cage.run("--check")
    assert proc.returncode == 0
    assert "pre-flight would SKIP: run branch 'auto/x:main'" in proc.stdout
    assert state.is_file(), "--check mutated the resume state"


# ---------- the forbidden-path post-check -----------------------------------

FORBIDDEN = [r"\.github/", r"repo\.yaml$"]

TOUCH_GATE_SURFACE = """\
mkdir -p .github/workflows
echo "on: push" > .github/workflows/ci.yml
git add -A
git commit -q -m "session edited the gate surface"
"""


def test_forbidden_path_edit_blocks_the_run_and_closes_the_pr(make_cage):
    cage = make_cage(forbidden_paths=FORBIDDEN)
    cage.ctl_set("pr_number", "42")
    cage.session(TOUCH_GATE_SURFACE)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", cage.last_row
    assert ".github/workflows/ci.yml" in cage.report
    assert "FORBIDDEN PATHS:" in cage.report
    assert "pr close 42" in cage.stub_log("gh.log")
    assert "BLOCKED" in cage.notifications


def test_an_unrelated_edit_is_not_blocked(make_cage):
    cage = make_cage(forbidden_paths=FORBIDDEN)
    cage.ctl_set("pr_number", "7")
    cage.session('echo hi > notes.md\ngit add -A\ngit commit -q -m "notes"\n')
    assert cage.run().returncode == 0
    row = cage.last_row
    assert row[2] == "done" and row[3] == "7", row
    assert "pr close" not in cage.stub_log("gh.log")
    assert "FORBIDDEN" not in cage.report


def test_a_regex_that_cannot_evaluate_fails_closed(make_cage):
    # config validates every pattern, but a hand-edited env (or a grep that
    # disagrees) must never turn the hard stop into a silent pass.
    cage = make_cage(forbidden_paths=FORBIDDEN)
    cage.env_line("CAGE_FORBIDDEN_RE", "^(unclosed")
    cage.ctl_set("pr_number", "13")
    cage.session('echo hi > notes.md\ngit add -A\ngit commit -q -m "notes"\n')
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", cage.last_row
    assert "regex failed to evaluate" in cage.report
    assert "pr close 13" in cage.stub_log("gh.log")


def test_an_empty_regex_disables_the_post_check(cage):
    cage.session(TOUCH_GATE_SURFACE)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", cage.last_row


# ---------- session handoff and the ledger row ------------------------------

def test_the_session_summary_refines_the_outcome_and_is_filed(cage):
    cage.ctl_set("pr_number", "99")
    cage.session(
        'printf "bead: agentops-x1\\noutcome: blocked\\nnode: reviewer\\n'
        'notes: gate red, needs a human\\n" > .run-summary\n')
    assert cage.run().returncode == 0
    date, bead, outcome, pr, note, node, duration, attempt, run_id = cage.last_row
    assert (bead, outcome, pr, node) == ("agentops-x1", "blocked", "99", "reviewer")
    assert note == "gate red; needs a human"    # commas scrubbed for the CSV
    assert attempt == "1" and duration.isdigit()
    # the run id names the branch, report and session log this row came from
    assert f"auto/{run_id}" == cage.branch
    assert (cage.dir / "reports" / f"{run_id}.md").is_file()
    assert (cage.dir / "reports" / f"{run_id}.session.log").is_file()
    assert run_id.startswith(date.replace("-", "")), (run_id, date)
    assert "## Session summary" in cage.report
    assert not (cage.worktree / ".run-summary").exists()   # consumed


def test_a_failing_session_is_recorded_as_failed(cage):
    cage.session('echo "boom" >&2\nexit 3\n')
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "failed(3)", cage.last_row


# ---------- two runs in one day (the cap RUN_ID lifts) ----------------------

def test_two_runs_in_one_day_collide_on_nothing(cage):
    """The structural cap: a BRANCH that is the calendar date puts a second
    run today on the first run's branch, report, session log and ledger
    row — papered over by `worktree add -B`. Two completed runs must
    leave two of each, in order, with the first run's evidence untouched.
    """
    cage.ctl_set("pr_number", "101")
    assert cage.run().returncode == 0
    first_branch, first_row = cage.branch, cage.last_row

    cage.ctl_set("pr_number", "102")
    assert cage.run().returncode == 0
    second_branch, second_row = cage.branch, cage.last_row

    if first_row[0] != second_row[0]:
        pytest.skip("the calendar day rolled mid-test — no same-day pair to judge")

    # distinct, and ordered: a later run always sorts after an earlier one
    assert re.fullmatch(RUN_BRANCH_RE, second_branch), second_branch
    assert first_branch != second_branch
    assert first_branch < second_branch

    # two real branches in the repo — the second run created its own rather
    # than resetting the first onto origin/main through the `-B` fallback
    live_branches = cage._git(cage.live, "branch", "--list", "auto/*",
                              "--format=%(refname:short)").stdout.split()
    assert sorted(live_branches) == sorted([first_branch, second_branch])

    # two reports, two session logs, two ledger rows — one set per run
    run_ids = [first_row[8], second_row[8]]
    assert run_ids[0] < run_ids[1] and run_ids[0] != run_ids[1]
    assert [p.stem for p in cage.reports] == run_ids
    assert [p.name for p in cage.session_logs] == [f"{r}.session.log" for r in run_ids]
    assert len(cage.ledger) == 2
    assert (first_row[3], second_row[3]) == ("101", "102")
    # same calendar day, two rows: the date column can no longer identify a run
    assert first_row[0] == second_row[0]
    for row, branch in ((first_row, first_branch), (second_row, second_branch)):
        assert f"auto/{row[8]}" == branch
        report = (cage.dir / "reports" / f"{row[8]}.md").read_text()
        assert f"- branch: {branch}" in report


def test_a_second_run_in_the_same_second_still_gets_its_own_id(cage):
    """Second resolution is not uniqueness. The report file is claimed under
    noclobber, so a run that finds its stamp taken takes the next sequence
    suffix — which still sorts after the run that claimed the bare stamp.
    """
    time.sleep(1.0 - (time.time() % 1.0))   # a whole second of headroom
    stamp = time.strftime("%Y%m%d-%H%M%S")
    reports = cage.dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    for taken in (stamp, f"{stamp}_02"):          # two ids already claimed
        (reports / f"{taken}.md").write_text("")
    assert cage.run().returncode == 0
    run_id = cage.last_row[8]
    if not run_id.startswith(stamp):
        pytest.skip("the runner started in a later second — no collision to judge")
    assert run_id == f"{stamp}_03", run_id
    assert run_id > f"{stamp}_02" > stamp, "the sequence suffix broke run ordering"
    assert cage.branch == f"auto/{run_id}"
    assert (reports / f"{run_id}.session.log").is_file()
    assert (reports / f"{stamp}.md").read_text() == "", "a claimed id was reused"


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root writes straight through a 0500 directory")
def test_an_unclaimable_reports_dir_stops_the_run_out_loud(cage):
    """A run id that cannot be claimed must SKIP, not vanish.

    A claim loop that gives up by exiting 2 leaves no ledger row and no
    notification: the run did not happen and nothing says so. The trigger is
    not only a 99-way same-second race — ANY unwritable `$REPORTS` (a bad
    `chown`, a full or read-only volume) reaches it, including through the
    `mkdir -p` above it. "Silence is never ambiguous" is the stated property
    of this surface; every other stop reaches `skip()`, which reports,
    ledgers AND notifies.

    A report cannot be written here by definition, so the evidence that must
    survive is the ledger row and the notification.
    """
    reports = cage.dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    reports.chmod(0o500)                      # readable, listable, NOT writable
    try:
        proc = cage.run()
    finally:
        reports.chmod(0o700)                  # so pytest can clean tmp_path up
    assert proc.returncode == 0, proc.stderr
    row = cage.last_row
    assert row[2] == "skipped", row
    assert "reports dir not writable" in row[4], row
    assert "reports dir not writable" in cage.notifications
    assert "skipped" in cage.notifications
    assert "display notification" in cage.stub_log("osascript.log")
    assert not cage.stub_log("claude.argv"), "the session ran despite the stop"
    assert cage.reports == [], "a report was claimed under an unwritable dir"


def test_an_exhausted_run_id_sequence_stops_the_run_out_loud(cage):
    """The other arm of the same claim: every id in this second taken.

    Also pins what the skip must NOT do — the loop leaves RUN_ID on the last
    id it tried (`_99`), which belongs to another run. The row names the bare
    stamp and no claimed report is appended to.
    """
    time.sleep(1.0 - (time.time() % 1.0))     # a whole second of headroom
    stamp = time.strftime("%Y%m%d-%H%M%S")
    reports = cage.dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    taken = [stamp] + [f"{stamp}_{n:02d}" for n in range(2, 100)]
    for name in taken:
        (reports / f"{name}.md").write_text("")
    proc = cage.run()
    # Clock-free, and deliberately not the ledger either: a runner that got a
    # free id (the second rolled) succeeds on its FIRST noclobber claim, so it
    # leaves a 100th report and runs the session. Both the same-second skip
    # and a silent exit-2 leave exactly the 99 pre-created files, so this
    # cannot self-skip when the bug is present — which a guard that sampled
    # the clock after the run returned could.
    if len(cage.reports) > len(taken):
        pytest.skip("the second rolled mid-run — the runner got a free id")
    assert proc.returncode == 0, proc.stderr
    row = cage.last_row
    assert row[2] == "skipped", row
    assert "cannot claim a run id" in row[4], row
    # NOT the bare stamp: that is the first name the loop tries, so reaching
    # exhaustion proves it belongs to another run, and the row would then name
    # that run's report, branch and session log.
    assert row[8] == f"{stamp}_unclaimed", "the row named an id another run owns"
    assert row[8] > f"{stamp}_99", "the sentinel broke run ordering"
    assert row[8] not in taken
    assert "cannot claim a run id" in cage.notifications
    assert not cage.stub_log("claude.argv"), "the session ran despite the stop"
    for name in taken:
        assert (reports / f"{name}.md").read_text() == "", \
            f"the skip appended into {name}.md, a report another run claimed"


# ---------- checkpoint / resume ---------------------------------------------

CHECKPOINT_THEN_HANG = """\
printf "bead: agentops-r1\\nstep: mid-gauntlet\\n" > .run-progress
echo work > feature.txt
git add -A
git commit -q -m "partial work"
touch "$CTL/session_ready"
""" + SLOW_LOOP


def _interrupt_with_checkpoint(cage: Cage) -> None:
    """One run that checkpoints, commits, and is killed by the watchdog."""
    cage.arm_gate()
    cage.session('trap \'exit 143\' TERM\n' + CHECKPOINT_THEN_HANG)
    proc = cage.run(timeout=90)
    assert proc.returncode == 0
    assert cage.last_row[2] == "timeout", cage.last_row


def test_an_interruption_with_a_checkpoint_becomes_resume_state(make_cage):
    cage = make_cage(timeout_secs=60)
    _interrupt_with_checkpoint(cage)
    state = (cage.dir / "resume-state").read_text()
    assert "bead=agentops-r1" in state
    assert "attempt=1" in state
    assert re.search("branch=" + RUN_BRANCH_RE, state), state
    assert "mid-gauntlet" in (cage.dir / "resume-progress").read_text()
    assert "resumable" in cage.notifications


def test_an_interruption_without_a_checkpoint_ends_the_chain(make_cage):
    # a resumed run that dies again writing nothing has no checkpoint to
    # continue from — the chain ends rather than looping on a bead forever
    cage = make_cage(timeout_secs=60)
    _interrupt_with_checkpoint(cage)
    assert (cage.dir / "resume-state").is_file()

    cage.arm_gate()
    cage.session('trap \'exit 143\' TERM\ntouch "$CTL/session_ready"\n'
                 + SLOW_LOOP)
    assert cage.run(timeout=90).returncode == 0
    assert cage.last_row[2] == "timeout"
    assert not (cage.dir / "resume-state").exists(), "resumed a bead with no checkpoint"


def test_the_next_run_continues_the_bead_and_keeps_the_work(make_cage):
    cage = make_cage(timeout_secs=60)
    _interrupt_with_checkpoint(cage)
    interrupted_branch = cage.branch
    interrupted_run_id = cage.last_row[8]

    # The gate is armed for THIS run too: at timeout_secs=60 the compressed
    # watchdog's whole patience is two ~10ms polls, and an ungated session
    # stub that starts slower than that on a loaded runner is TERM'd BEFORE
    # its `cp` lands — run.sh still exits 0 with its own ledger row, so the
    # failure would surface here as an empty resume.in that reads like a
    # content bug. session_ready is touched AFTER the observable writes, so
    # the watchdog cannot fire until they exist — ordered by construction,
    # not by machine speed.
    cage.arm_gate()
    cage.session('cp .run-resume "$CTL/resume.in"\n'
                 '[ -f feature.txt ] && echo KEPT > "$CTL/work" || echo LOST > "$CTL/work"\n'
                 'touch "$CTL/session_ready"\n'
                 + SESSION_OK)
    assert cage.run().returncode == 0
    # The resuming run has its OWN RUN_ID, so "it continued the right branch"
    # is a real claim, not a tautology: if both runs computed the same branch
    # name, the assertion below could not fail at all.
    resumed_run_id = cage.last_row[8]
    assert resumed_run_id != interrupted_run_id, "the second run reused a run id"
    assert f"auto/{resumed_run_id}" != interrupted_branch

    handoff = cage.stub_log("resume.in")
    assert handoff, ("resume.in never appeared — the session was interrupted "
                     "before its handoff copy landed, or the run resumed "
                     "nothing (no .run-resume to copy)")
    # the cage's own header, not the checkpoint copy appended after the '---'
    header = handoff.split("---")[0]
    assert "bead: agentops-r1" in header, handoff
    assert f"branch: {interrupted_branch}" in header, handoff
    assert "attempt: 2" in header and "max_attempts: 2" in header
    assert "mid-gauntlet" in handoff          # the checkpoint rides along
    assert cage.stub_log("work").strip() == "KEPT", "the resumed work was reset away"
    assert cage.branch == interrupted_branch  # same branch, same PR
    resumed_profile = (cage.dir / "reports" / f"{resumed_run_id}.profile.json").read_text()
    assert (f"git push origin {interrupted_branch}:refs/heads/{interrupted_branch})"
            in resumed_profile)
    assert f"auto/{resumed_run_id}" not in resumed_profile
    assert cage.last_row[7] == "2"            # attempt column
    assert not (cage.dir / "resume-state").exists()   # a done run ends the chain


def test_the_resume_budget_is_earned_run_by_run_then_spent(make_cage):
    # The increment is what makes the budget finite. Earn it across real
    # runs instead of writing attempt=2 into the state file by hand — a
    # runner that never increments would pass that.
    cage = make_cage(timeout_secs=60, max_resumes=2)
    state = cage.dir / "resume-state"

    _interrupt_with_checkpoint(cage)                     # run 1
    assert "attempt=1" in state.read_text(), state.read_text()
    _interrupt_with_checkpoint(cage)                     # run 2 resumes it
    earned = state.read_text()
    assert "attempt=2" in earned, earned
    assert "bead=agentops-r1" in earned
    assert cage.last_row[7] == "2", cage.last_row        # ledger agrees

    # Gated like the resumed run above: ungated, the
    # compressed watchdog could TERM this stub before it wrote handoff.
    cage.arm_gate()
    cage.session('[ -f .run-resume ] && echo HANDOFF > "$CTL/handoff" '
                 '|| echo NONE > "$CTL/handoff"\n'
                 'touch "$CTL/session_ready"\n' + SESSION_OK)
    assert cage.run().returncode == 0                    # run 3: budget spent
    assert "resume budget spent" in cage.notifications
    assert "needs a human" in cage.notifications
    assert not state.exists()
    assert cage.stub_log("handoff").strip() == "NONE", "a spent bead was resumed anyway"


def test_a_spent_resume_budget_returns_the_bead_and_starts_fresh(make_cage):
    cage = make_cage(timeout_secs=60, max_resumes=2)
    _interrupt_with_checkpoint(cage)
    state = cage.dir / "resume-state"
    state.write_text(state.read_text().replace("attempt=1", "attempt=2"))

    # Gated for the same reason as the sibling test above.
    cage.arm_gate()
    cage.session('[ -f .run-resume ] && echo HANDOFF > "$CTL/handoff" '
                 '|| echo NONE > "$CTL/handoff"\n'
                 'touch "$CTL/session_ready"\n' + SESSION_OK)
    assert cage.run().returncode == 0
    assert "resume budget spent" in cage.notifications
    assert "needs a human" in cage.notifications
    assert not state.exists()
    assert cage.stub_log("handoff").strip() == "NONE", "a spent bead was resumed anyway"


def test_check_mode_reports_a_pending_resume_without_mutating_it(cage):
    state = cage.dir / "resume-state"
    state.write_text("bead=agentops-r9\nbranch=auto/20260101\nattempt=1\n")
    proc = cage.run("--check")
    assert proc.returncode == 0
    assert "would RESUME agentops-r9 on auto/20260101 (attempt 2/2)" in proc.stdout
    assert state.read_text().startswith("bead=agentops-r9")   # untouched
    assert cage.ledger == []


def test_check_mode_reports_a_spent_budget_without_mutating_it(cage):
    state = cage.dir / "resume-state"
    state.write_text("bead=agentops-r9\nbranch=auto/20260101\nattempt=2\n")
    proc = cage.run("--check")
    assert proc.returncode == 0
    assert "resume budget SPENT for agentops-r9 (2 attempts)" in proc.stdout
    assert state.is_file()
    assert "resume budget spent" not in cage.notifications   # no notification either


def test_a_completed_run_clears_stale_resume_state(cage):
    (cage.dir / "resume-state").write_text("bead=agentops-old\nattempt=1\n")
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done"
    assert not (cage.dir / "resume-state").exists()


# ---------- runner / env skew (fatal, not skipped) --------------------------

def test_a_missing_env_file_is_fatal(cage):
    cage.env_file.unlink()
    proc = cage.run()
    assert proc.returncode == 2
    assert "cage.env missing" in proc.stderr
    assert cage.ledger == []


def test_runner_env_skew_is_fatal_not_an_unbound_variable_abort(cage):
    lines = [ln for ln in cage.env_file.read_text().splitlines()
             if not ln.startswith("CAGE_MAX_RESUMES=")]
    cage.env_file.write_text("\n".join(lines) + "\n")
    proc = cage.run()
    assert proc.returncode == 2
    assert "CAGE_MAX_RESUMES missing from cage.env (runner/env skew)" in proc.stderr


def test_cage_env_reaches_the_caged_session(cage):
    """cage.env holds BARE assignments, so sourced without `set -a` CAGE_*
    are the runner's own shell variables and the `claude -p` child never
    sees them. Anything platform-side that keys off the caged environment —
    memory's origin derivation first — would silently no-op.

    The probe runs INSIDE the real session, which is the only place this is
    answerable: asserting on the runner's own shell would pass while the
    child still saw nothing."""
    cage.session('echo "SEEN_PROJECT=${CAGE_PROJECT:-MISSING}"\n'
                 'echo "SEEN_DIR=${CAGE_DIR:-MISSING}"\n')
    cage.run()
    log = "\n".join(p.read_text() for p in cage.session_logs)
    assert "SEEN_PROJECT=MISSING" not in log, (
        "CAGE_PROJECT did not reach the caged session — the environment the "
        "platform derives run provenance from is empty")
    assert "SEEN_DIR=MISSING" not in log
    assert "SEEN_PROJECT=sampleproj" in log


def test_a_slow_session_start_is_diagnosable_never_a_silent_flake(make_cage):
    """A readiness gate that expires before the session installs its TERM
    trap lets the watchdog's TERM kill a trapless session, and the signal
    log stays empty — "the watchdog never signalled", indistinguishable
    from a watchdog failure.

    Two runs prove both halves:
      A. a deliberately BOUNDED gate (gate_budget) that expires writes
         gate_expired — the give-up is a fact on disk, never silent (a stub
         with no marker fails this half);
      B. the default UNCAPPED gate orders TERM after the trap by
         construction — the same slow-starting session lands its signal.
    """
    # A: the session refuses to become ready until it SEES the expiry
    # marker, so the bounded gate is guaranteed to expire — ordered by
    # construction (racing a 10-tick budget against a sleep would be the
    # timing-ordered shape this test exists to rule out).
    cage = make_cage(timeout_secs=60)
    cage.arm_gate()
    cage.ctl_set("gate_budget", "10")
    cage.session(
        'while [ ! -f "$CTL/gate_expired" ]; do "$REAL_SLEEP" 0.02; done\n'
        'trap \'echo TERM >> "$CTL/signals"; exit 143\' TERM\n'
        'touch "$CTL/session_ready"\n'
        + SLOW_LOOP)
    cage.run(timeout=90)
    assert (cage.ctl / "gate_expired").exists(), (
        "a bounded readiness gate gave up without saying so — the silent-timeout flake "
        "shape is back to being undiagnosable")

    # B: a slow-starting session under the default uncapped gate (budget 0)
    # — the trap always wins because the first poll cannot complete before
    # session_ready. Same cage, re-armed; A's markers cleared. The 1.5s
    # here is a real startup lag, and half B holds for ANY lag: the wait
    # has no budget to outrun.
    cage.ctl_set("gate_budget", "0")
    (cage.ctl / "gate_expired").unlink(missing_ok=True)
    (cage.ctl / "signals").unlink(missing_ok=True)
    cage.arm_gate()
    cage.session(
        '"$REAL_SLEEP" 1.5\n'
        'trap \'echo TERM >> "$CTL/signals"; exit 143\' TERM\n'
        'touch "$CTL/session_ready"\n'
        + SLOW_LOOP)
    proc = cage.run(timeout=90)
    assert proc.returncode == 0
    assert "TERM" in cage.stub_log("signals"), (
        "the slow-starting session lost its TERM signal — the uncapped "
        "ordering guarantee regressed")
    assert cage.last_row[2] == "timeout", cage.last_row


# ---------- runner-side corpus publication ------------------------------------
#
# A run can do the whole gauntlet — built, reviewed, attested CLEAN — and the
# organization still gets nothing if the session is left to ingest, because
# .warden/ is forbidden to the session. The forbidden freeze is CORRECT for
# the session; the RUNNER — outside the model — ingests after the session
# exits and commits the shard, so containment and the corpus both hold.

# The live-checkout shim the runner trusts. `autonomy carve-out` REFUSES here,
# which is the real command's answer for every diff that is not shaped like a
# `warden autonomy` write — the ordinary case. A shim that fell through to
# `exit 0` would clear the forbidden-path stop for every test in this
# file, so the refusal is modelled explicitly.
WARDEN_OK = """\
#!/bin/sh
if [ "$1 $2" = "memory ingest" ]; then
  mkdir -p .warden/memory/attest
  echo '{"records": []}' > .warden/memory/attest/run-shard.json
  echo "memory ingest: 1 new shard(s)"
fi
if [ "$1 $2" = "autonomy carve-out" ]; then
  cat > /dev/null
  echo "OUTSIDE the ladder's carve-out" >&2
  exit 1
fi
exit 0
"""

# Same shim, carve-out ANSWERING YES: the stand-in for a diff that really is a
# machine-tier pause or non-blocking adoption. What the shape check itself
# accepts is tested against real git trees in tests/test_autonomy_carve_out.py;
# what these tests hold is the RUNNER's wiring around its verdict.
WARDEN_CARVE_OUT_YES = WARDEN_OK.replace(
    '  echo "OUTSIDE the ladder\'s carve-out" >&2\n  exit 1\n',
    '  echo "inside the autonomy-ladder carve-out" \n  exit 0\n')

WARDEN_CARVE_OUT_CRASH = WARDEN_OK.replace(
    '  echo "OUTSIDE the ladder\'s carve-out" >&2\n  exit 1\n',
    '  echo "boom" >&2\n  exit 3\n')

WARDEN_BROKEN = "#!/bin/sh\necho 'ingest exploded' >&2\nexit 1\n"

COMMIT_WORK = ('echo work > work.txt\ngit add -A\n'
               'git commit -q -m "session work"\n')


def _executable_seed(cage, rel: str, body: str) -> None:
    """seed_files cannot carry an exec bit; re-commit with one and push —
    the runner cuts its worktree from origin/main, not from the checkout."""
    path = cage.live / rel
    path.chmod(0o755)
    cage._git(cage.live, "add", "-A")
    cage._git(cage.live, "commit", "-q", "-m", "chmod warden shim")
    cage._git(cage.live, "push", "-q", "origin", "main")


def _branch_tree(cage) -> tuple[str, list[str]]:
    """(run branch name, files on it) as the bare ORIGIN sees them — the
    proof standard is what actually got published, not the worktree."""
    refs = cage._git(cage.tmp, "--git-dir", str(cage.origin),
                     "for-each-ref", "--format=%(refname:short)",
                     "refs/heads/auto/").stdout.split()
    assert refs, "no run branch reached the origin"
    files = cage._git(cage.tmp, "--git-dir", str(cage.origin), "ls-tree",
                      "-r", "--name-only", refs[0]).stdout.split()
    return refs[0], files


def test_the_runner_publishes_the_shard_after_the_session(make_cage):
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_OK})
    _executable_seed(cage, ".warden/bin/warden", WARDEN_OK)
    cage.session(COMMIT_WORK)
    assert cage.run().returncode == 0
    branch, files = _branch_tree(cage)
    assert "work.txt" in files, "the session's own push is a different fix"
    assert ".warden/memory/attest/run-shard.json" in files, (
        "the run's shard never reached the origin — the corpus forgets "
        "this run")
    assert "ingest:" not in cage.report, cage.report
    assert cage.last_row[2] == "done", cage.last_row


def test_the_runners_publication_push_never_moves_main_under_push_default_upstream(
        make_cage):
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_OK})
    _executable_seed(cage, ".warden/bin/warden", WARDEN_OK)
    cage._git(cage.live, "config", "push.default", "upstream")
    main_before = cage._git(cage.tmp, "--git-dir", str(cage.origin),
                            "rev-parse", "refs/heads/main").stdout.strip()
    cage.session(COMMIT_WORK)
    assert cage.run().returncode == 0
    assert cage._git(cage.tmp, "--git-dir", str(cage.origin), "rev-parse",
                     "refs/heads/main").stdout.strip() == main_before, (
        "the runner's own push of the run branch moved origin main")
    _, files = _branch_tree(cage)
    assert ".warden/memory/attest/run-shard.json" in files


def test_the_runners_publication_push_never_moves_main_under_remote_origin_push(
        make_cage):
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_OK})
    _executable_seed(cage, ".warden/bin/warden", WARDEN_OK)
    main_before = _origin_ref(cage, "refs/heads/main")
    cage.session('B=$(git rev-parse --abbrev-ref HEAD)\n'
                 'git config remote.origin.push "refs/heads/$B:refs/heads/main"\n'
                 + COMMIT_WORK)
    assert cage.run().returncode == 0
    assert _origin_ref(cage, "refs/heads/main") == main_before, (
        "the runner's own push of the run branch moved origin main")
    head = cage._git(cage.worktree, "rev-parse", "HEAD").stdout.strip()
    assert _origin_ref(cage, f"refs/heads/{cage.branch}") == head
    _, files = _branch_tree(cage)
    assert ".warden/memory/attest/run-shard.json" in files
    assert "ingest:" not in cage.report, cage.report


def test_a_failing_ingest_is_said_in_the_report_never_absorbed(make_cage):
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_BROKEN})
    _executable_seed(cage, ".warden/bin/warden", WARDEN_BROKEN)
    cage.session(COMMIT_WORK)
    assert cage.run().returncode == 0
    assert "ingest: warden memory ingest FAILED" in cage.report, (
        "an ingest failure vanished — the run reports success and the "
        "organization gets nothing, the exact silent direction of 17.8")
    assert cage.last_row[2] == "done", "the note must not reclassify the run"


def test_a_missing_warden_shim_is_said_not_skipped_silently(make_cage):
    cage = make_cage()
    cage.session(COMMIT_WORK)
    assert cage.run().returncode == 0
    assert "ingest: no executable .warden/bin/warden" in cage.report


def test_a_workless_run_does_not_ingest(make_cage):
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_OK})
    _executable_seed(cage, ".warden/bin/warden", WARDEN_OK)
    cage.session(SESSION_OK)   # no commits
    assert cage.run().returncode == 0
    assert "ingest" not in cage.report.lower()


def test_a_forbidden_path_run_never_publishes(make_cage):
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_OK},
                     forbidden_paths=FORBIDDEN)
    _executable_seed(cage, ".warden/bin/warden", WARDEN_OK)
    cage.session(TOUCH_GATE_SURFACE)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH"
    assert "run-shard.json" not in cage.report
    # nothing of this run's shard reaches the origin
    refs = cage._git(cage.tmp, "--git-dir", str(cage.origin),
                     "for-each-ref", "--format=%(refname:short)",
                     "refs/heads/auto/").stdout.split()
    for ref in refs:
        files = cage._git(cage.tmp, "--git-dir", str(cage.origin), "ls-tree",
                          "-r", "--name-only", ref).stdout.split()
        assert ".warden/memory/attest/run-shard.json" not in files


MALICIOUS_SHIM = (
    '#!/bin/sh\n'
    '# a session-planted shim: if the runner ever executes worktree content\n'
    '# outside the profile, this fires.\n'
    'touch pwned.txt\n'
    'exit 0\n')


def test_the_runner_never_executes_a_worktree_planted_shim(make_cage):
    """The publish step must run the LIVE checkout's warden (Write-denied to
    the session), never the worktree's session-writable copy. The
    forbidden-path check reads only the committed diff, so a session can
    overwrite $WORKTREE/.warden/bin/warden on disk (uncommitted, invisible
    to that check), and a runner that executed it would do so with ambient
    privileges.

    Here the LIVE shim is the honest WARDEN_OK; the session overwrites the
    WORKTREE shim with one that drops pwned.txt. The run must publish the
    real shard (live shim ran) and leave no pwned.txt (worktree shim did
    not)."""
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_OK})
    _executable_seed(cage, ".warden/bin/warden", WARDEN_OK)
    # The session (cwd = worktree) swaps its own copy of the shim, uncommitted
    # — exactly what the committed-diff forbidden check cannot see — then
    # commits one innocuous file to clear the workless guard.
    cage.session(
        'cat > .warden/bin/warden <<"SHIM"\n' + MALICIOUS_SHIM + 'SHIM\n'
        'chmod +x .warden/bin/warden\n'
        'echo work > work.txt\ngit add work.txt\n'
        'git commit -q -m "session work"\n')
    assert cage.run().returncode == 0
    assert not (cage.worktree / "pwned.txt").exists(), (
        "the runner executed the session-planted worktree shim — the HIGH is "
        "back")
    _, files = _branch_tree(cage)
    assert ".warden/memory/attest/run-shard.json" in files, (
        "the trusted live shim did not publish the shard")


# ---------- the autonomy-ladder carve-out -----------------------------------
#
# `warden autonomy pause|adopt` and the carve-out in .warden/skills-policy.md
# grant the machine tier a .warden/ write; a post-check that closed the PR for
# every .warden/ write would auto-close that permission in practice.
# These tests hold the RUNNER's wiring around the verdict; what the shape check
# itself accepts is tested against real git trees in test_autonomy_carve_out.py.
#
# The property that must survive all of them: the carve-out can only ever CLEAR
# a stop the post-check already raised, and a cleared run still opens a PR a
# human merges — no auto-merge exists on this path.

def test_a_cleared_carve_out_lets_the_run_stand(make_cage):
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_CARVE_OUT_YES},
                     forbidden_paths=FORBIDDEN, autonomy_carve_out=True)
    _executable_seed(cage, ".warden/bin/warden", WARDEN_CARVE_OUT_YES)
    cage.ctl_set("pr_number", "42")
    cage.session(TOUCH_GATE_SURFACE)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", cage.last_row
    assert "pr close" not in cage.stub_log("gh.log"), \
        "a cleared carve-out still closed the PR"
    assert "FORBIDDEN PATHS:" not in cage.report
    assert "CLEARED" in cage.report, \
        f"a cleared gate-surface stop must be SAID, not inferred: {cage.report}"
    # ...and there is still no merge: the runner has no such path.
    assert "pr merge" not in cage.stub_log("gh.log")
    # ...and the cleared run PUBLISHES. It used to publish
    # nothing, on the argument that the shard would land after the verdict was
    # computed and make it unreproducible on the pushed head. The argument is
    # answered by re-deriving the stop over the head about to be pushed rather
    # than by declining to push: what a reviewer fetches is a head the runner
    # actually judged. Before this, a run cleared by a pause or an adoption
    # alone left its attestation in gitignored .warden/out/ forever and its own
    # PR then failed `warden attest check` with NO ATTESTATION.
    assert "ingest:" not in cage.report, cage.report
    _, files = _branch_tree(cage)
    assert ".warden/memory/attest/run-shard.json" in files, (
        "a carve-out-cleared run published nothing — the corpus never learns "
        "the round happened and the PR fails the attestation gate")


def test_a_refused_carve_out_leaves_the_stop_standing(make_cage):
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_OK},
                     forbidden_paths=FORBIDDEN, autonomy_carve_out=True)
    _executable_seed(cage, ".warden/bin/warden", WARDEN_OK)
    cage.ctl_set("pr_number", "43")
    cage.session(TOUCH_GATE_SURFACE)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", cage.last_row
    assert "pr close 43" in cage.stub_log("gh.log")
    assert "REFUSED" in cage.report, cage.report
    # The REASON reaches the REPORT, not just a log the reader has to go find.
    # Asserted on the report specifically: $LOG is the wrong surface for this,
    # because the harness and launchd both fold the runner's own stderr into
    # that same file, so a log assertion passes on leaked text even when the
    # capture is empty (a log-based version of this assertion stays green
    # against a deliberately broken redirect).
    assert "OUTSIDE the ladder's carve-out" in cage.report, (
        "the carve-out's refusal reason never reached the report — the "
        "capture is empty, so the reader is told a stop fired and not why")


def test_a_crashing_carve_out_fails_closed(make_cage):
    """An exit this runner does not understand is a refusal, never a pass —
    the same rule the grep's own exit>=2 arm follows. An older warden without
    the subcommand lands here too (argparse exits 2 on an invalid choice)."""
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_CARVE_OUT_CRASH},
                     forbidden_paths=FORBIDDEN, autonomy_carve_out=True)
    _executable_seed(cage, ".warden/bin/warden", WARDEN_CARVE_OUT_CRASH)
    cage.ctl_set("pr_number", "44")
    cage.session(TOUCH_GATE_SURFACE)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", cage.last_row
    assert "exit 3" in cage.report, cage.report
    assert "pr close 44" in cage.stub_log("gh.log")


def test_no_live_shim_means_no_carve_out(make_cage):
    """The trusted binary is the LIVE checkout's, and its absence is not a
    reason to trust the worktree's — the run stays blocked and says why."""
    cage = make_cage(forbidden_paths=FORBIDDEN, autonomy_carve_out=True)
    cage.ctl_set("pr_number", "45")
    cage.session(TOUCH_GATE_SURFACE)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", cage.last_row
    assert "not evaluated" in cage.report, cage.report
    assert "pr close 45" in cage.stub_log("gh.log")


def test_a_regex_that_cannot_evaluate_is_never_carve_out_material(make_cage):
    """The hard case. When the forbidden-path grep itself fails to evaluate,
    VIOLATION holds a synthetic message rather than a path list — feeding that
    to the carve-out check would ask it about a path that does not exist, and
    a check answering YES there would convert a fail-closed stop into a pass.
    The carve-out is consulted only when the grep SUCCEEDED, so the most
    permissive possible shim cannot clear this run."""
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_CARVE_OUT_YES},
                     forbidden_paths=FORBIDDEN, autonomy_carve_out=True)
    _executable_seed(cage, ".warden/bin/warden", WARDEN_CARVE_OUT_YES)
    cage.env_line("CAGE_FORBIDDEN_RE", "^(unclosed")
    cage.ctl_set("pr_number", "46")
    cage.session('echo hi > notes.md\ngit add -A\ngit commit -q -m "notes"\n')
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", cage.last_row
    assert "regex failed to evaluate" in cage.report
    assert "CLEARED" not in cage.report, \
        "a regex that could not evaluate was cleared by the carve-out"
    assert "pr close 46" in cage.stub_log("gh.log")


def test_a_clean_run_never_consults_the_carve_out(make_cage):
    """It can only ever CLEAR a stop, so it is not asked when none fired."""
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_CARVE_OUT_YES},
                     forbidden_paths=FORBIDDEN, autonomy_carve_out=True)
    _executable_seed(cage, ".warden/bin/warden", WARDEN_CARVE_OUT_YES)
    cage.session('echo hi > notes.md\ngit add -A\ngit commit -q -m "notes"\n')
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", cage.last_row
    assert "carve-out" not in cage.report, cage.report


def test_the_carve_out_is_off_unless_the_project_granted_it(make_cage):
    """The grant is the consumer's to give, so `[gate].autonomy_carve_out`
    defaults to false and the most permissive possible shim changes nothing
    until it is turned on — a repo that never granted it keeps its
    forbidden-path stop whole however new the platform gets.

    WHAT THIS TEST DOES NOT SAY: it used
    to give its reason as "enrolling a newer platform must never weaken a hard
    stop a project already declared", which is true of a repo that never
    granted and false of one that did. The flag carries no version and no path
    list, so what a standing `true` admits widens with the pin — it has
    already widened from three `.warden/` writes to four. The property under test here is
    the DEFAULT, and only that. The scope of a given grant is pinned by the
    scoped-grant tests below.
    """
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_CARVE_OUT_YES},
                     forbidden_paths=FORBIDDEN)   # not granted
    _executable_seed(cage, ".warden/bin/warden", WARDEN_CARVE_OUT_YES)
    cage.ctl_set("pr_number", "47")
    cage.session(TOUCH_GATE_SURFACE)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", cage.last_row
    assert "pr close 47" in cage.stub_log("gh.log")
    assert "carve-out" not in cage.report, cage.report


# ---------- the grant may pin its own scope ---------------------------------
#
# `warden autonomy carve-out` decides SHAPE, and that definition lives on the
# platform, so it moves when the pin moves. A bare `true` therefore admits
# whatever the pinned platform admits — that set has gone from three
# `.warden/` writes to four, and every granted consumer got the fourth by
# re-pinning, with no new grant and nothing in their own cage.toml to read.
# A LIST of store prefixes is the scoped form of the same grant: the runner
# clears a forbidden path only when the platform AND the consumer both admit
# it. The most permissive possible shim is used throughout, so what is being
# measured is the consumer's half and nothing else.

WARDEN_STORE = """\
#!/bin/sh
if [ "$1 $2" = "memory ingest" ]; then
  mkdir -p .warden/memory/attest
  echo '{"records": []}' > .warden/memory/attest/run-shard.json
fi
if [ "$1 $2" = "autonomy carve-out" ]; then
  cat > /dev/null
  echo "inside the autonomy-ladder carve-out"
  exit 0
fi
exit 0
"""

# A session writing the one .warden/ store the platform admits and the grant
# below may or may not name.
TOUCH_ATTEST = """\
mkdir -p .warden/memory/attest
echo '{"records": []}' > .warden/memory/attest/session-shard.json
git add -A
git commit -q -m "session left its attestation"
"""


def test_a_store_the_grant_does_not_name_is_not_cleared(make_cage):
    """The platform says yes; the consumer never granted THAT store.

    Fails closed toward the narrower set, which is the whole property: a
    platform that grows a fifth store cannot widen a grant already given. The
    refusal names the path and the grant, because "your pinned platform
    admits a store you never did" is only actionable if the human can see
    which line to add.
    """
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_STORE},
                     forbidden_paths=[r"\.warden/"],
                     autonomy_carve_out=True,
                     carve_out_stores=[".warden/rules/"])
    _executable_seed(cage, ".warden/bin/warden", WARDEN_STORE)
    cage.ctl_set("pr_number", "60")
    cage.session(TOUCH_ATTEST)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", cage.last_row
    assert "pr close 60" in cage.stub_log("gh.log")
    assert "REFUSED by this project's grant" in cage.report, cage.report
    assert ".warden/memory/attest/session-shard.json" in cage.report, (
        "the refusal must name the PATH that fell outside the grant")
    assert ".warden/rules/" in cage.report, (
        "the refusal must name the grant, or the reader cannot tell which "
        "line of their own cage.toml to widen")
    assert "CLEARED" not in cage.report, (
        "the platform's yes reached the verdict past the consumer's scope")


def test_a_store_the_grant_names_still_clears(make_cage):
    """The control. Without it the test above passes on a cage that refuses
    everything, which would be a scope that had eaten the grant whole."""
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_STORE},
                     forbidden_paths=[r"\.warden/"],
                     autonomy_carve_out=True,
                     carve_out_stores=[".warden/rules/",
                                       ".warden/memory/attest/"])
    _executable_seed(cage, ".warden/bin/warden", WARDEN_STORE)
    cage.ctl_set("pr_number", "61")
    cage.session(TOUCH_ATTEST)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", cage.last_row
    assert "pr close" not in cage.stub_log("gh.log")
    assert "CLEARED" in cage.report, cage.report


def test_an_unscoped_grant_keeps_the_behaviour_the_bool_shipped_with(
        make_cage):
    """Back-compat, asserted rather than assumed. An empty store list is how
    `autonomy_carve_out = true` renders, and it must mean "the pin decides" —
    the reading every config written against the bool already has. A scope
    that defaulted to something would silently narrow those cages instead,
    which is the mirror of the bug and just as much a surprise."""
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_STORE},
                     forbidden_paths=[r"\.warden/"], autonomy_carve_out=True)
    _executable_seed(cage, ".warden/bin/warden", WARDEN_STORE)
    cage.ctl_set("pr_number", "62")
    cage.session(TOUCH_ATTEST)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", cage.last_row
    assert "CLEARED" in cage.report, cage.report
    assert "REFUSED by this project's grant" not in cage.report, (
        "an unscoped grant narrowed something — cage.env carries no stores")


# ---------- the verdict describes the head that is pushed -------------------

def test_a_pause_cleared_run_publishes_its_attestation(make_cage):
    """The gap the attestation arm does not reach.

    A run cleared by the ATTESTATION arm loses nothing when publication is
    skipped — the session already committed its own shard. A run cleared by a
    PAUSE or an ADOPTION alone left its attestation in gitignored
    `.warden/out/` forever: the corpus never learned the round happened and
    the PR then failed `warden attest check` with NO ATTESTATION, which is
    the same vice the carve-out's attest store closed, one shape over.

    Staged as the real thing: the session commits a rule pause and NO shard,
    the grant admits both stores, and the runner's own ingest is what has to
    reach the origin.
    """
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_STORE},
                     forbidden_paths=[r"\.warden/"], autonomy_carve_out=True,
                     carve_out_stores=[".warden/rules/",
                                       ".warden/memory/attest/"])
    _executable_seed(cage, ".warden/bin/warden", WARDEN_STORE)
    cage.ctl_set("pr_number", "63")
    cage.session("mkdir -p .warden/rules\n"
                 "printf 'paused: true\\n' > .warden/rules/r.md\n"
                 "git add -A\ngit commit -q -m \"pause a rule\"\n")
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", cage.last_row
    _, files = _branch_tree(cage)
    assert ".warden/rules/r.md" in files, "the session's own push is elsewhere"
    assert ".warden/memory/attest/run-shard.json" in files, (
        "a pause-cleared run published no attestation — its own PR then "
        "fails the attestation gate with NO ATTESTATION")
    assert "ingest:" not in cage.report, cage.report


def test_an_ungranted_consumer_still_feeds_its_corpus(make_cage):
    """The regression guard for the fix itself.

    The consumer config this platform's own docs ship forbids `.warden/` and
    grants no carve-out. Its runner's publication commit lands under
    `.warden/memory/attest/`, which that fence matches — so a check that
    re-judged the WHOLE head before pushing would refuse this push on every
    run, forever, and the PR would fail `warden attest check` with NO
    ATTESTATION: the exact failure this publication exists to remove, generalised
    from one cleared run to every run of every non-granting project.

    The freeze's subject is the SESSION. This commit is the runner's, made by
    trusted code from the live checkout after the session exited — the same
    distinction the ingest step's own trust argument turns on — so it is not
    the freeze's to judge.
    """
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_OK},
                     forbidden_paths=[r"\.warden/"])   # no grant, on purpose
    _executable_seed(cage, ".warden/bin/warden", WARDEN_OK)
    cage.session(COMMIT_WORK)
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", cage.last_row
    _, files = _branch_tree(cage)
    assert ".warden/memory/attest/run-shard.json" in files, (
        "an ungranted consumer's corpus went unfed — the runner's own "
        "publication commit was judged by a fence whose subject is the session")
    assert "ingest:" not in cage.report, cage.report


def test_a_cleared_run_whose_grant_excludes_the_shard_publishes_nothing(
        make_cage):
    """The reachable half of the ordering objection, and the only one.

    The grant admits `.warden/rules/`, so a pause clears the run — and does
    NOT admit `.warden/memory/attest/`, so the shard the runner would publish
    is outside it. Pushing anyway would leave a head on which a reviewer
    re-running `warden autonomy carve-out` gets a refusal the cage did not,
    which is precisely what the old blanket skip was protecting. So nothing is
    pushed, the commit is UNDONE so a resumed run cannot inherit it, and the
    note names the grant that refused.
    """
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_STORE},
                     forbidden_paths=[r"\.warden/"], autonomy_carve_out=True,
                     carve_out_stores=[".warden/rules/"])
    _executable_seed(cage, ".warden/bin/warden", WARDEN_STORE)
    cage.ctl_set("pr_number", "64")
    cage.session("mkdir -p .warden/rules\n"
                 "printf 'paused: true\\n' > .warden/rules/r.md\n"
                 "git add -A\ngit commit -q -m \"pause a rule\"\n")
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", cage.last_row
    assert "CLEARED" in cage.report, cage.report
    refs = cage._git(cage.tmp, "--git-dir", str(cage.origin),
                     "for-each-ref", "--format=%(refname:short)",
                     "refs/heads/auto/").stdout.split()
    for ref in refs:
        files = cage._git(cage.tmp, "--git-dir", str(cage.origin), "ls-tree",
                          "-r", "--name-only", ref).stdout.split()
        assert ".warden/memory/attest/run-shard.json" not in files, (
            "the runner pushed a shard the grant that cleared the run does "
            "not admit — the cleared verdict no longer holds on that head")
    assert "NOT published" in cage.report, (
        f"an unfed corpus must be SAID, never absorbed: {cage.report}")
    assert "autonomy_carve_out" in cage.report and ".warden/rules/" in cage.report

    # UNDONE, not merely unpushed: a resumed run checks this branch out and
    # keeps its work, so a commit already judged unpushable would join the
    # next session's diff and close its PR as a forbidden path.
    head_files = cage._git(cage.worktree, "log", "-1", "--name-only",
                           "--format=").stdout.split()
    assert not any(f.startswith(".warden/memory/attest/") for f in head_files), (
        f"the refused shard commit is still on the branch: {head_files}")
    assert cage._git(cage.worktree, "status", "--porcelain").stdout == "", (
        "the reset left the worktree dirty, which the next run would carry")


# Clears the SESSION's paths and refuses the RUNNER's shard — the shape the
# platform arm actually refuses on (a shard whose roster was never verified,
# whose round was not minted, whose rule_id does not resolve, or that ingest's
# scrubbers would have changed). For this repo's own cage, where the
# attestation store IS granted, this is the only reachable refusal left.
WARDEN_SHARD_REFUSED = """\
#!/bin/sh
if [ "$1 $2" = "memory ingest" ]; then
  mkdir -p .warden/memory/attest
  echo '{"records": []}' > .warden/memory/attest/run-shard.json
fi
if [ "$1 $2" = "autonomy carve-out" ]; then
  PATHS=$(cat)
  case "$PATHS" in
    *memory/attest*)
      echo "records roster_verification 'unverified-roster', not 'verified'" >&2
      exit 1 ;;
  esac
  echo "inside the autonomy-ladder carve-out"
  exit 0
fi
exit 0
"""


def test_a_shard_the_platform_refuses_is_not_pushed_and_says_why(make_cage):
    """The second authority, and the reason the refusal carries its own text.

    The consumer's grant admits the attestation store, so `stores_admit`
    passes and the SHAPE check is what refuses. A fixed "grant the attestation
    store in [gate].autonomy_carve_out" sentence would tell the reader to do
    the thing they have already done, and the real reason would live only in
    the runner's log. That is the failure mode the run's own carve-out refusal
    was written to avoid a hundred lines up, and this arm makes the same
    choice.
    """
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_SHARD_REFUSED},
                     forbidden_paths=[r"\.warden/"], autonomy_carve_out=True,
                     carve_out_stores=[".warden/rules/",
                                       ".warden/memory/attest/"])
    _executable_seed(cage, ".warden/bin/warden", WARDEN_SHARD_REFUSED)
    cage.ctl_set("pr_number", "65")
    cage.session("mkdir -p .warden/rules\n"
                 "printf 'paused: true\\n' > .warden/rules/r.md\n"
                 "git add -A\ngit commit -q -m \"pause a rule\"\n")
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "done", cage.last_row
    assert "CLEARED" in cage.report, "the session's own paths were not cleared"
    assert "NOT published" in cage.report, cage.report
    assert "roster_verification" in cage.report, (
        "the refusal's REASON stayed in the log — the report tells the reader "
        f"nothing they can act on: {cage.report}")
    refs = cage._git(cage.tmp, "--git-dir", str(cage.origin),
                     "for-each-ref", "--format=%(refname:short)",
                     "refs/heads/auto/").stdout.split()
    for ref in refs:
        files = cage._git(cage.tmp, "--git-dir", str(cage.origin), "ls-tree",
                          "-r", "--name-only", ref).stdout.split()
        assert ".warden/memory/attest/run-shard.json" not in files


def test_a_session_planted_file_in_the_store_is_never_published(make_cage):
    """The CONTENT half of the publication step's trust argument.

    The step argues that its commit is the runner's, made by trusted code from
    the live checkout — and that is true of the CODE. `git add` stages the
    whole store in the SESSION-WRITABLE worktree, and the forbidden-path
    freeze reads only the COMMITTED diff, so a file the session dropped there
    is invisible to it and would be published over the runner's name, on a
    head a reviewer fetches, with no authority consulted. `warden gate` and
    `attest check` both read that store.
    """
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_OK},
                     forbidden_paths=[r"\.warden/"])
    _executable_seed(cage, ".warden/bin/warden", WARDEN_OK)
    # UNCOMMITTED, which is the whole shape: a committed one is caught by the
    # freeze (the control below), and this one the freeze never sees.
    cage.session(COMMIT_WORK
                 + "mkdir -p .warden/memory/attest\n"
                 + "echo '{\"records\": []}' > .warden/memory/attest/forged.json\n")
    assert cage.run().returncode == 0
    refs = cage._git(cage.tmp, "--git-dir", str(cage.origin),
                     "for-each-ref", "--format=%(refname:short)",
                     "refs/heads/auto/").stdout.split()
    for ref in refs:
        files = cage._git(cage.tmp, "--git-dir", str(cage.origin), "ls-tree",
                          "-r", "--name-only", ref).stdout.split()
        assert ".warden/memory/attest/forged.json" not in files, (
            "a file the SESSION wrote reached the branch as the runner's own "
            "publication, behind the freeze that exists to decide it")
    assert "left uncommitted content" in cage.report, (
        f"the refusal must be SAID, never absorbed: {cage.report}")
    assert cage.last_row[2] == "done", (
        f"the freeze never saw it — that is the premise, not a failure: "
        f"{cage.last_row}")


def test_a_resumable_run_defers_publication(make_cage):
    """A run that may RESUME publishes nothing.

    The resume path keeps this branch and its work, so a shard committed now
    joins the RESUMED session's own diff — and on a project that forbids
    `.warden/` its freeze closes that PR over the previous run's housekeeping.
    A session's work destroyed by the step meant to preserve one.
    """
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_OK},
                     forbidden_paths=[r"\.warden/"], timeout_secs=1)
    _executable_seed(cage, ".warden/bin/warden", WARDEN_OK)
    cage.arm_gate()
    cage.session(COMMIT_WORK
                 + 'printf "bead: agentops-x\n" > .run-progress\n'
                 + 'touch "$CTL/session_ready"\n'
                 + 'trap "exit 0" TERM\nwhile true; do sleep 0.2; done\n')
    assert cage.run(timeout=180).returncode == 0
    assert cage.last_row[2] == "timeout", cage.last_row
    assert "deferred" in cage.report, (
        f"a resumable run published without saying so: {cage.report}")
    refs = cage._git(cage.tmp, "--git-dir", str(cage.origin),
                     "for-each-ref", "--format=%(refname:short)",
                     "refs/heads/auto/").stdout.split()
    for ref in refs:
        files = cage._git(cage.tmp, "--git-dir", str(cage.origin), "ls-tree",
                          "-r", "--name-only", ref).stdout.split()
        assert ".warden/memory/attest/run-shard.json" not in files, (
            "a resumable run published a shard the resumed run's own freeze "
            "would then close its PR over")


def test_a_commit_the_runner_cannot_read_back_is_not_pushed(make_cage):
    """The fail-closed arm: judged nothing, so published nothing.

    Every other refusal here is a judgement. This one is the absence of one —
    the runner committed, then could not read back WHAT it committed, so it
    has no basis for the claim the push would make. A hard stop that cannot
    evaluate must behave like a hit, and the run says which.

    Staged with a `git` wedge that fails exactly the publication step's own
    `diff --name-only <sha>..HEAD`; the freeze's diff carries `-z` and is
    untouched, so the run reaches this step normally.
    """
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_OK},
                     forbidden_paths=[r"\.warden/"])
    _executable_seed(cage, ".warden/bin/warden", WARDEN_OK)
    real_git = shutil.which("git") or "/usr/bin/git"
    wedge = cage.bin / "git"
    wedge.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do [ \"$a\" = '-z' ] && exec " + real_git + " \"$@\"; done\n"
        "case \"$*\" in *'diff --name-only'*) exit 128 ;; esac\n"
        "exec " + real_git + " \"$@\"\n")
    wedge.chmod(0o755)
    cage.session(COMMIT_WORK)
    assert cage.run().returncode == 0
    refs = cage._git(cage.tmp, "--git-dir", str(cage.origin),
                     "for-each-ref", "--format=%(refname:short)",
                     "refs/heads/auto/").stdout.split()
    for ref in refs:
        files = cage._git(cage.tmp, "--git-dir", str(cage.origin), "ls-tree",
                          "-r", "--name-only", ref).stdout.split()
        assert ".warden/memory/attest/run-shard.json" not in files, (
            "the runner pushed a commit it could not read back")
    assert "could not be read" in cage.report, (
        f"a publication that judged nothing must SAY so: {cage.report}")


def test_the_ledger_survives_a_multi_line_refusal(make_cage):
    """One run, one ledger row — whatever the refusal's text looks like.

    The note now carries `warden autonomy carve-out`'s raw stderr, which is
    multi-line. A newline in column 5 SPLITS the row: columns 6-9 land on the
    last physical line and every `split(",")` reader — cage/measure.py, and
    this harness's own `last_row` — then reads a different run's row.
    """
    shim = WARDEN_SHARD_REFUSED.replace(
        '      echo "records roster_verification \'unverified-roster\', not \'verified\'" >&2\n',
        '      echo "record 1: round_binding missing, comma here" >&2\n'
        '      echo "record 2: rule_id does not resolve" >&2\n')
    cage = make_cage(seed_files={".warden/bin/warden": shim},
                     forbidden_paths=[r"\.warden/"], autonomy_carve_out=True,
                     carve_out_stores=[".warden/rules/",
                                       ".warden/memory/attest/"])
    _executable_seed(cage, ".warden/bin/warden", shim)
    cage.session("mkdir -p .warden/rules\n"
                 "printf 'paused: true\\n' > .warden/rules/r.md\n"
                 "git add -A\ngit commit -q -m \"pause a rule\"\n")
    assert cage.run().returncode == 0
    lines = [ln for ln in (cage.dir / "ledger.csv").read_text().splitlines() if ln]
    assert lines[0] == ",".join(measure.HEADER), f"no header row: {lines[0]}"
    rows = lines[1:]
    assert len(rows) == 1, f"the refusal split the ledger into {len(rows)} rows: {rows}"
    assert rows[0].split(",")[-1].startswith("2026"), (
        f"the run id is not in the last column: {rows[0]}")
    assert "round_binding missing" in rows[0], (
        "the reason did not reach the ledger note at all")


def test_a_diff_that_cannot_be_read_fails_closed(make_cage):
    """`rev-parse origin/main` succeeding does not mean `A...B` resolves.
    Unchecked, git errors, CHANGED comes back EMPTY, grep exits 1 and the whole
    gate-surface freeze reports clean without ever having evaluated — a
    pass-shaped failure.

    Staged with the real shape: the session leaves HEAD on an unrelated
    history, so `git rev-parse origin/main` still answers while
    `git diff origin/main...HEAD` exits 128 with "no merge base". A force-push
    of main or a shallow fetch produces the same thing on a real runner.
    """
    cage = make_cage(seed_files={".warden/bin/warden": WARDEN_CARVE_OUT_YES},
                     forbidden_paths=FORBIDDEN, autonomy_carve_out=True)
    _executable_seed(cage, ".warden/bin/warden", WARDEN_CARVE_OUT_YES)
    cage.ctl_set("pr_number", "48")
    cage.session(
        'echo hi > notes.md\ngit add -A\ngit commit -q -m "notes"\n'
        'git checkout -q --orphan detached\n'
        'git commit -q --allow-empty -m "unrelated history"\n')
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", cage.last_row
    assert "diff failed" in cage.report, cage.report
    assert "CLEARED" not in cage.report, \
        "a diff that could not be read was cleared by the carve-out"


# ---------- a path git would C-quote still trips the freeze -----------------

@pytest.mark.parametrize("name", ["naïve.yml", 'we"ird.yml', "tab\tx.yml"],
                         ids=["non-ascii", "double-quote", "tab"])
def test_a_forbidden_path_git_would_c_quote_still_closes_the_pr(make_cage,
                                                                   name):
    """A forbidden path git would C-quote still closes the PR.

    `git diff origin/main...HEAD --name-only` C-quotes a path holding a
    non-ASCII byte under the default `core.quotepath`, and wraps it in
    double quotes — `".github/workflows/na\\303\\257ve.yml"` — while every
    pattern cage/config.py renders is anchored `^(...)`. The leading quote
    character defeats the anchor, so a caged session that named one file
    under a forbidden directory with one accented character would pass the
    freeze The-Cage.md says closes the PR. The double-quote and tab cells
    are the half `core.quotepath=false` alone would NOT close: git C-quotes
    those whatever the flag says, and a hard stop the model could route
    around by choosing a filename is not a hard stop. The runner reads the
    listing with `-z`, quoting-free by construction."""
    cage = make_cage(forbidden_paths=FORBIDDEN)
    cage.ctl_set("pr_number", "42")
    cage.session("mkdir -p .github/workflows\n"
                 f"echo 'on: push' > {shlex.quote('.github/workflows/' + name)}\n"
                 "git add -A\n"
                 "git commit -q -m 'session edited the gate surface'\n")
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", (
        f"a session edit under .github/ named {name!r} passed the freeze: "
        f"{cage.last_row}")
    assert f".github/workflows/{name}" in cage.report, cage.report
    assert "pr close 42" in cage.stub_log("gh.log")
    assert "BLOCKED" in cage.notifications


def test_a_forbidden_path_with_a_newline_fails_closed_not_open(make_cage):
    """A newline-bearing path does not "over-close" merely because one split
    half still starts with its fenced prefix. For an END-anchored fence that
    is false — `\\.github/.*\\.yml$` matches neither `.github/workflows/a`
    nor `b.yml`, so a line-split read sees the freeze CLEAN over a path a
    session chose. The runner keeps the newline distinguishable and fails
    closed on it: the PR is closed with a reason naming the newline, under a
    pattern that would otherwise see nothing."""
    cage = make_cage(forbidden_paths=[r"\.github/.*\.yml$"])
    cage.ctl_set("pr_number", "44")
    cage.session("mkdir -p .github/workflows\n"
                 f"echo 'on: push' > {shlex.quote('.github/workflows/a' + chr(10) + 'b.yml')}\n"
                 "git add -A\n"
                 "git commit -q -m 'session edited the gate surface'\n")
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", cage.last_row
    assert "newline in its name" in cage.report, cage.report
    assert "pr close 44" in cage.stub_log("gh.log")


def test_an_end_anchored_fence_still_closes_on_a_plain_name(make_cage):
    """The control for the newline cell: the same end-anchored fence closes
    the PR on the ordinary spelling of the same path, through the regex arm
    and not the fail-closed one — so the newline arm is what closed the cell
    above, not the pattern."""
    cage = make_cage(forbidden_paths=[r"\.github/.*\.yml$"])
    cage.ctl_set("pr_number", "45")
    cage.session("mkdir -p .github/workflows\n"
                 "echo 'on: push' > .github/workflows/ab.yml\n"
                 "git add -A\n"
                 "git commit -q -m 'session edited the gate surface'\n")
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", cage.last_row
    assert "newline in its name" not in cage.report
    assert ".github/workflows/ab.yml" in cage.report
    assert "pr close 45" in cage.stub_log("gh.log")


def test_a_diff_that_fails_under_the_z_read_still_fails_closed(make_cage):
    """The exit the fail-closed arm reads must stay GIT's after the listing
    goes through `tr`: a plain `$(git ... | tr ...)` would report tr's exit
    and turn a failed diff into an EMPTY, clean-looking listing — the
    pass-shaped failure the arm exists to refuse.
    Driven by pointing origin/main at a history the worktree's HEAD shares
    no merge base with, so `A...B` cannot resolve while `rev-parse
    origin/main` still succeeds."""
    cage = make_cage(forbidden_paths=FORBIDDEN)
    cage.ctl_set("pr_number", "9")
    cage.session(
        'echo hi > notes.md\ngit add -A\ngit commit -q -m "notes"\n'
        # an unrelated root, pushed over origin/main after the session's work;
        # the run branch is returned to by NAME (`checkout -` has no
        # previous branch to go back to after an orphan checkout)
        'RUN_BRANCH=$(git rev-parse --abbrev-ref HEAD) && '
        'git checkout -q --orphan other && git rm -rfq . && '
        'echo o > o.txt && git add o.txt && git commit -q -m other && '
        'git push -q -f origin HEAD:main && git fetch -q origin && '
        'git checkout -q "$RUN_BRANCH"\n')
    assert cage.run().returncode == 0
    assert cage.last_row[2] == "FORBIDDEN-PATH", cage.last_row
    assert "forbidden-path diff failed" in cage.report, cage.report
    assert "pr close 9" in cage.stub_log("gh.log")
