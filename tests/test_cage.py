"""cage: cage.toml schema + rendered artifacts.

The load-bearing test is distill-equivalence: the golden cage.toml must
regenerate the semantics of the hand-built distill cage (profile permissions,
plist schedule, forbidden-path regex) — that is what 'generalized, not forked'
means.
"""

import json
import plistlib
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from cage import cli as cage_cli
from cage import config as cage_config
from cage import measure as cage_measure
from cage import render as cage_render

GOLDEN = Path(__file__).parent / "golden"

MINIMAL_TOML = """\
[project]
name = "sampleproj"
github = "acme/sampleproj"
live_checkout = "/work/sampleproj"
worktree = "/work/sampleproj-run"

[triggers.schedule]

[gate]
forbidden_paths = []
"""

# Same cage, started by hand instead of by launchd — no scheduler, so no plist.
MANUAL_TOML = MINIMAL_TOML.replace("[triggers.schedule]", "[triggers.manual]")


def write_toml(tmp_path: Path, body: str = MINIMAL_TOML) -> Path:
    path = tmp_path / "cage.toml"
    path.write_text(body)
    return path


# ---------- config schema ----------------------------------------------------

def test_minimal_config_loads_with_defaults(tmp_path):
    cfg = cage_config.load(write_toml(tmp_path))
    assert cfg.name == "sampleproj"
    assert cfg.branch_prefix == "auto/"
    assert cfg.triggers == ("schedule",)
    assert (cfg.schedule.hour, cfg.schedule.minute) == (23, 30)
    assert (cfg.quiet_start, cfg.quiet_end) == (5, 23)  # the old 23-5 window
    assert cfg.max_open_prs == 2 and cfg.timeout_secs == 7200
    assert cfg.cage_dir == tmp_path.resolve()  # defaults to the toml's directory
    assert cfg.label == "com.sampleproj.cage"
    assert cfg.forbidden_regex == ""  # no paths declared -> post-check disabled


@pytest.mark.parametrize("mutation, fragment", [
    ("missing-name", "[project].name"),
    ("bad-github", "owner/repo"),
    ("same-worktree", "must differ"),
    ("dotdot-worktree", "must differ"),  # unnormalized alias of live_checkout
    ("relative-path", "absolute path"),
    ("unknown-key", "fail-closed"),
    ("no-triggers", "at least one trigger"),
    ("empty-triggers", "at least one trigger"),
    ("unknown-trigger", "fail-closed"),
    ("unknown-trigger-key", "fail-closed"),
    ("hour-inside-quiet-hours", "always skip"),
    ("empty-quiet-interval", "must differ"),
    ("quiet-hours-off-with-hours", "enabled = false"),
    ("no-gate-section", "must be declared"),
    ("bad-regex", "not a valid POSIX ERE"),
    ("python-only-regex", "not a valid POSIX ERE"),  # lookahead: re-valid only
    ("bad-scrub", "literal relative path"),
    ("glob-scrub", "literal relative path"),
    ("prefix-no-slash", "branch_prefix"),
    # The scoped grant is as fail-closed as the bool was.
    ("empty-carve-out-list", "admits no store"),
    ("absolute-carve-out-store", "literal relative directory prefix"),
    ("unterminated-carve-out-store", "literal relative directory prefix"),
    ("glob-carve-out-store", "literal relative directory prefix"),
    ("escaped-carve-out-store", "literal relative directory prefix"),
    ("dot-slash-carve-out-store", "literal relative directory prefix"),
    # [toolchain] fails closed the same way.
    ("relative-toolchain-path", "absolute path"),
    ("colon-toolchain-path", "no ':' or whitespace"),
    ("path-shaped-tool", "bare command name"),
    ("unknown-toolchain-key", "fail-closed"),
])
def test_invalid_configs_fail_closed(tmp_path, mutation, fragment):
    body = MINIMAL_TOML
    if mutation == "missing-name":
        body = body.replace('name = "sampleproj"\n', "")
    elif mutation == "bad-github":
        body = body.replace('github = "acme/sampleproj"', 'github = "sampleproj"')
    elif mutation == "same-worktree":
        body = body.replace('worktree = "/work/sampleproj-run"',
                            'worktree = "/work/sampleproj"')
    elif mutation == "dotdot-worktree":
        body = body.replace('worktree = "/work/sampleproj-run"',
                            'worktree = "/work/sampleproj/../sampleproj"')
    elif mutation == "relative-path":
        body = body.replace('worktree = "/work/sampleproj-run"',
                            'worktree = "relative/run"')
    elif mutation == "unknown-key":
        body += "[limits]\nmax_open_pr = 3\n"  # typo of max_open_prs
    elif mutation == "no-triggers":
        body = body.replace("[triggers.schedule]\n", "")
    elif mutation == "empty-triggers":
        body = body.replace("[triggers.schedule]", "[triggers]")
    elif mutation == "unknown-trigger":
        body = body.replace("[triggers.schedule]", "[triggers.webhook]")
    elif mutation == "unknown-trigger-key":
        body = body.replace("[triggers.schedule]", "[triggers.schedule]\nhours = 9")
    elif mutation == "hour-inside-quiet-hours":
        # launchd would fire at noon and the runner would quiet-skip every time
        body = body.replace("[triggers.schedule]", "[triggers.schedule]\nhour = 12")
    elif mutation == "empty-quiet-interval":
        body += "[quiet_hours]\nstart = 9\nend = 9\n"
    elif mutation == "quiet-hours-off-with-hours":
        body += "[quiet_hours]\nenabled = false\nstart = 9\nend = 17\n"
    elif mutation == "no-gate-section":
        body = body.replace("[gate]\nforbidden_paths = []\n", "")
    elif mutation == "bad-regex":
        body = body.replace("forbidden_paths = []",
                            "forbidden_paths = ['([unclosed']")
    elif mutation == "python-only-regex":
        body = body.replace("forbidden_paths = []",
                            "forbidden_paths = ['(?=x)secrets/']")
    elif mutation == "bad-scrub":
        body += "scrub_paths = ['../outside']\n"
    elif mutation == "glob-scrub":
        body += "scrub_paths = ['*.xcresult']\n"
    elif mutation == "prefix-no-slash":
        body = body.replace("[project]", "[project]\nbranch_prefix = \"run\"")
    elif mutation == "empty-carve-out-list":
        body += "autonomy_carve_out = []\n"
    elif mutation == "absolute-carve-out-store":
        body += "autonomy_carve_out = ['/warden/rules/']\n"
    elif mutation == "unterminated-carve-out-store":
        # `.warden/rules` without the slash prefix-matches `.warden/rulesX`
        body += "autonomy_carve_out = ['.warden/rules']\n"
    elif mutation == "glob-carve-out-store":
        body += "autonomy_carve_out = ['.warden/*/']\n"
    elif mutation == "dot-slash-carve-out-store":
        # `./x/` matches no diff path, so the grant silently admits nothing
        body += "autonomy_carve_out = ['./.warden/rules/']\n"
    elif mutation == "escaped-carve-out-store":
        # a bash `case` pattern reads `\` as an escape, so this would match
        # `.warden/rules/` while reading as a different (narrower) grant
        body += "autonomy_carve_out = ['.warden/rul\\es/']\n"
    elif mutation == "relative-toolchain-path":
        body += "[toolchain]\npath = ['bin']\n"
    elif mutation == "colon-toolchain-path":
        body += "[toolchain]\npath = ['/opt/a:/opt/b']\n"
    elif mutation == "path-shaped-tool":
        body += "[toolchain]\nrequire = ['/usr/local/go/bin/go']\n"
    elif mutation == "unknown-toolchain-key":
        body += "[toolchain]\nrequires = ['go']\n"  # typo of require
    with pytest.raises(cage_config.CageConfigError) as exc:
        cage_config.load(write_toml(tmp_path, body))
    assert fragment in str(exc.value)


def test_the_autonomy_carve_out_is_a_declared_grant(tmp_path):
    """It weakens a hard stop this project declared, so it is the CONSUMER's
    grant to give — never platform behaviour a `cage enroll` hands out on the
    platform's own authority (blast-radius round, F3). Default false; a
    non-boolean rejects rather than being read for truthiness, because "no"
    and "" are both true in Python and a typo must not quietly open the gate.
    """
    cfg = cage_config.load(write_toml(tmp_path))
    assert cfg.autonomy_carve_out is False, "the carve-out defaulted ON"
    env = cage_render.render_env(cfg, home=Path("/home/agent"))
    assert "CAGE_AUTONOMY_CARVE_OUT=0" in env

    granted = MINIMAL_TOML.replace("forbidden_paths = []",
                                   "forbidden_paths = []\nautonomy_carve_out = true")
    (tmp_path / "on").mkdir()
    cfg = cage_config.load(write_toml(tmp_path / "on", granted))
    assert cfg.autonomy_carve_out is True
    assert "CAGE_AUTONOMY_CARVE_OUT=1" in cage_render.render_env(
        cfg, home=Path("/home/agent"))

    typo = MINIMAL_TOML.replace("forbidden_paths = []",
                                'forbidden_paths = []\nautonomy_carve_out = "no"')
    with pytest.raises(cage_config.CageConfigError) as exc:
        (tmp_path / "typo").mkdir()
        cage_config.load(write_toml(tmp_path / "typo", typo))
    assert "must be true, false, or a list of" in str(exc.value)


def test_the_grant_can_name_its_own_stores_and_a_bare_true_still_loads(
        tmp_path):
    """The grant's SCOPE, and the back-compat that lets a consumer keep `true`.

    A bare bool carries no version and no path list, so what it admits is
    whatever the PINNED platform admits — and that moved under a granted
    consumer once already (the carve-out went from three `.warden/` writes
    to four). A list names the stores the consumer granted
    and renders them for the runner to narrow with; `true` renders an EMPTY
    store list, which the runner reads as "unscoped", so every config written
    against the bool keeps its exact old behaviour. `false` is still no grant.
    """
    for name, decl, carve, stores in [
            ("off", "", "0", ""),
            ("unscoped", "\nautonomy_carve_out = true", "1", ""),
            ("scoped", "\nautonomy_carve_out = ['.warden/rules/', "
                       "'.warden/memory/attest/']", "1",
             ".warden/rules/ .warden/memory/attest/")]:
        (tmp_path / name).mkdir()
        body = MINIMAL_TOML.replace("forbidden_paths = []",
                                    "forbidden_paths = []" + decl)
        cfg = cage_config.load(write_toml(tmp_path / name, body))
        env = cage_render.render_env(cfg, home=Path("/home/agent"))
        assert f"CAGE_AUTONOMY_CARVE_OUT={carve}" in env, name
        assert f"CAGE_CARVE_OUT_STORES={shlex.quote(stores)}" in env, (
            f"{name}: the runner reads the scope from cage.env and an empty "
            "value is what tells it the grant is the unscoped bool")
    assert cage_config.load(
        write_toml(tmp_path / "scoped",
                   MINIMAL_TOML.replace(
                       "forbidden_paths = []",
                       "forbidden_paths = []\nautonomy_carve_out = "
                       "['.warden/rules/']"))).autonomy_carve_out is True, (
        "a list is a GRANT — the runner's `CAGE_AUTONOMY_CARVE_OUT = 1` gate "
        "must still open, or the scope would narrow the grant to nothing")


def test_enroll_says_an_unscoped_grant_is_unscoped(tmp_path, capsys):
    """The migration signal, without which the fix is itself silent.

    A bare `true` is a grant whose meaning can change with nobody told.
    Shipping the scoped form and saying nothing to the consumers still on
    `true` would repeat that exact defect: `cage enroll` is the moment they re-pin, and it
    is the one moment they can be told. Scoped and ungranted cages say
    nothing — a notice that fires for everyone is a notice nobody reads.
    """
    granted = MINIMAL_TOML.replace("forbidden_paths = []",
                                   "forbidden_paths = []\nautonomy_carve_out = true")
    cfg = cage_config.load(write_toml(tmp_path, granted))
    capsys.readouterr()
    assert cage_cli.main(["validate", str(tmp_path / "cage.toml")]) == 0
    err = capsys.readouterr().err
    assert "UNSCOPED" in err and "widened once already" in err, err
    assert "list of store prefixes" in err, err
    assert cfg.autonomy_carve_out and not cfg.carve_out_stores

    for name, decl in [("scoped", "\nautonomy_carve_out = ['.warden/rules/']"),
                       ("off", "")]:
        (tmp_path / name).mkdir()
        write_toml(tmp_path / name,
                   MINIMAL_TOML.replace("forbidden_paths = []",
                                        "forbidden_paths = []" + decl))
        capsys.readouterr()
        assert cage_cli.main(["validate", str(tmp_path / name / "cage.toml")]) == 0
        assert "UNSCOPED" not in capsys.readouterr().err, name


def test_the_toolchain_is_declared_and_renders_for_the_runner(tmp_path):
    """What the caged PATH must carry, declared rather than assumed.

    The runner resets PATH to a fixed list so a launchd-started run behaves
    like a hand-started one. Everything outside that list is invisible inside
    the cage — Go's installer puts `go` in `/usr/local/go/bin`, which is on
    no such list — and the run that found this out spent an hour building
    before its own verify scope refused at ship step 1.
    Defaults are empty, so a cage that declares nothing behaves exactly as it
    did before the section existed.
    """
    cfg = cage_config.load(write_toml(tmp_path))
    assert cfg.toolchain_path == [] and cfg.required_tools == []
    env = cage_render.render_env(cfg, home=Path("/home/agent"))
    assert "CAGE_TOOLCHAIN_PATH=''" in env and "CAGE_REQUIRED_TOOLS=''" in env

    (tmp_path / "go").mkdir()
    body = MINIMAL_TOML + ("\n[toolchain]\npath = ['/usr/local/go/bin', "
                           "'~/.cargo/bin']\nrequire = ['go', 'uv']\n")
    cfg = cage_config.load(write_toml(tmp_path / "go", body))
    assert cfg.toolchain_path[0] == "/usr/local/go/bin"
    assert not cfg.toolchain_path[1].startswith("~"), (
        "`~` must expand at enroll time — the runner joins these into PATH, "
        "where a literal tilde resolves nothing")
    env = cage_render.render_env(cfg, home=Path("/home/agent"))
    assert f"CAGE_TOOLCHAIN_PATH={shlex.quote(':'.join(cfg.toolchain_path))}" in env, (
        "PATH is ':'-joined, so cage.env must carry it that way")
    assert "CAGE_REQUIRED_TOOLS='go uv'" in env


def test_forbidden_regex_is_anchored_alternation(tmp_path):
    body = MINIMAL_TOML.replace("forbidden_paths = []",
                                "forbidden_paths = ['\\.github/', 'repo\\.yaml$']")
    cfg = cage_config.load(write_toml(tmp_path, body))
    assert cfg.forbidden_regex == r"^(\.github/|repo\.yaml$)"


# ---------- rendering --------------------------------------------------------

def test_env_renders_every_runner_parameter(tmp_path):
    cfg = cage_config.load(write_toml(tmp_path))
    env = cage_render.render_env(cfg, home=Path("/home/agent"))
    # every CAGE_* the runner references must be defined, or set -u kills the run
    runner = cage_render.runner_script()
    import re
    referenced = set(re.findall(r"\$\{?(CAGE_[A-Z_]+)", runner)) - {"CAGE_SELF"}
    defined = {line.split("=", 1)[0] for line in env.splitlines()
               if line.startswith("CAGE_")}
    assert referenced <= defined, f"runner references undefined vars: {referenced - defined}"


def test_env_values_are_shell_quoted(tmp_path):
    body = MINIMAL_TOML.replace('live_checkout = "/work/sampleproj"',
                                'live_checkout = "/work/my proj"')
    cfg = cage_config.load(write_toml(tmp_path, body))
    env = cage_render.render_env(cfg, home=Path("/home/agent"))
    assert "CAGE_LIVE_REPO='/work/my proj'" in env


def test_plist_schedule_and_label(tmp_path):
    body = MINIMAL_TOML.replace(
        "[triggers.schedule]", "[triggers.schedule]\nhour = 1\nminute = 15")
    cfg = cage_config.load(write_toml(tmp_path, body))
    payload = plistlib.loads(cage_render.render_plist(cfg).encode())
    assert payload["Label"] == "com.sampleproj.cage"
    assert payload["StartCalendarInterval"] == {"Hour": 1, "Minute": 15}
    assert payload["ProgramArguments"] == ["/bin/bash", str(cfg.cage_dir / "run.sh")]
    assert payload["RunAtLoad"] is False
    # Log wiring, both streams, pinned here because the distill-equivalence
    # test has no schedule to render. A launchd job with no captured stderr
    # is the "a run could simply not happen and say nothing" failure the
    # runner's own comments name.
    assert payload["StandardOutPath"] == str(cfg.log)
    assert payload["StandardErrorPath"] == str(cfg.log)


# ---------- triggers: the schedule is one option, not the architecture -------

def test_a_nine_to_five_cage_validates_and_renders_its_plist(tmp_path):
    """A cage scheduled at 09:00 validates and renders its plist with a
    quiet-hours window of 17 to 9, which crosses midnight."""
    body = MINIMAL_TOML.replace(
        "[triggers.schedule]", "[triggers.schedule]\nhour = 9\nminute = 0"
    ) + "[quiet_hours]\nstart = 17\nend = 9\n"
    cfg = cage_config.load(write_toml(tmp_path, body))
    assert (cfg.schedule.hour, cfg.schedule.minute) == (9, 0)
    assert (cfg.quiet_start, cfg.quiet_end) == (17, 9)
    payload = plistlib.loads(cage_render.render_plist(cfg).encode())
    assert payload["StartCalendarInterval"] == {"Hour": 9, "Minute": 0}


def test_quiet_hours_need_not_cross_midnight_either(tmp_path):
    """'Do not run during my working day' is the honest form of the guard."""
    body = MINIMAL_TOML.replace(
        "[triggers.schedule]", "[triggers.manual]"
    ) + "[quiet_hours]\nstart = 9\nend = 17\n"
    cfg = cage_config.load(write_toml(tmp_path, body))
    assert (cfg.quiet_start, cfg.quiet_end) == (9, 17)
    assert cfg.quiet_enabled
    assert "CAGE_QUIET_START=9" in cage_render.render_env(cfg, home=Path("/h"))
    assert "CAGE_QUIET_END=17" in cage_render.render_env(cfg, home=Path("/h"))


def test_quiet_hours_can_be_declared_off_but_never_omitted_into_off(tmp_path):
    body = MANUAL_TOML + "[quiet_hours]\nenabled = false\n"
    cfg = cage_config.load(write_toml(tmp_path, body))
    assert not cfg.quiet_enabled
    # the runner reads an empty interval, never a missing variable
    env = cage_render.render_env(cfg, home=Path("/h"))
    assert "CAGE_QUIET_START=0" in env and "CAGE_QUIET_END=0" in env
    # omitting the section keeps the guard ON, at the hours it always had
    assert cage_config.load(write_toml(tmp_path, MANUAL_TOML)).quiet_enabled


def test_every_trigger_is_declarable_and_ordered(tmp_path):
    body = MINIMAL_TOML.replace(
        "[triggers.schedule]",
        "[triggers.ci-event]\n\n[triggers.manual]\n\n[triggers.schedule]")
    cfg = cage_config.load(write_toml(tmp_path, body))
    assert cfg.triggers == ("schedule", "manual", "ci-event")


def test_a_cage_with_no_schedule_trigger_enrolls_without_a_plist(tmp_path, capsys):
    toml = write_toml(tmp_path, MANUAL_TOML)
    cfg = cage_config.load(toml)
    assert cfg.triggers == ("manual",) and cfg.schedule is None
    written = cage_render.enroll(cfg)
    assert not any(p.name.endswith(".plist") for p in written), written
    assert not (tmp_path / f"{cfg.label}.plist").exists()
    # and the runner + env + profile are still rendered in full
    assert {p.name for p in written} >= {"run.sh", "cage.env",
                                         "cage-profile.json"}
    # rendering a plist for a cage that declared no scheduler is a bug, not a default
    with pytest.raises(cage_config.CageConfigError, match="schedule"):
        cage_render.render_plist(cfg)


def test_dropping_the_schedule_trigger_removes_the_stale_plist(tmp_path, capsys):
    toml = write_toml(tmp_path)
    cage_render.enroll(cage_config.load(toml))
    plist = tmp_path / "com.sampleproj.cage.plist"
    assert plist.is_file()
    toml.write_text(MANUAL_TOML)
    assert cage_cli.main(["enroll", str(toml)]) == 0
    assert not plist.exists(), "a scheduler the config no longer declares survived"
    out = capsys.readouterr().out
    # said out loud: a rendered artifact disappearing silently is how a cage
    # ends up with a launchd job nobody can account for
    assert f"removed {plist}" in out
    assert "launchctl bootout" in out, "enroll removed the plist without saying how to unload the job"


def test_enroll_ignores_night_named_files(tmp_path, capsys):
    """Enroll runs no pre-rename cleanup: night-named files are bystanders.

    A night-named file left in a cage dir, a stale
    `com.<name>.nightshift.plist` included, is a BYSTANDER — a human's
    archive, not enroll's to delete — so enroll must leave it exactly alone
    and say nothing about it.
    """
    toml = write_toml(tmp_path)
    bystanders = {
        tmp_path / "com.sampleproj.nightshift.plist": "archived\n",
        tmp_path / "nightshift.env": "archived\n",
        tmp_path / "nightshift-profile.json": "archived\n",
        tmp_path / "nightshift.toml": "# old config kept for reference\n",
    }
    for path, body in bystanders.items():
        path.write_text(body)
    assert cage_cli.main(["enroll", str(toml)]) == 0
    for path, body in bystanders.items():
        assert path.read_text() == body, (
            f"enroll touched {path.name} — night-named files are bystanders "
            "now, not artifacts it owns")
    out = capsys.readouterr().out
    assert "pre-rename" not in out and "nightshift" not in out, (
        "the retired legacy sweep is still talking about night artifacts")


def test_enroll_never_promises_a_bypass_for_a_guard_that_is_off(tmp_path, capsys):
    """With quiet hours off, enroll must not tell a cage 'quiet hours still
    apply, bypass them once with touch ~/.cage-*-run-now'.

    With `enabled = false` the runner reads the override file ONLY inside the
    quiet-hours branch, so that file is never consumed and never does
    anything — the message would offer a bypass for a stop that cannot fire,
    two lines under a header saying the stop is off.
    """
    quiet_off = "[quiet_hours]\nenabled = false\n"
    # BOTH branches: gating only the manual one would leave the scheduled
    # cage told to touch a file its runner never reads — which then waits in
    # $HOME to spend itself on the first stop a later `enabled = true`
    # declares.
    for label, base in (("manual", MANUAL_TOML), ("scheduled", MINIMAL_TOML)):
        toml = write_toml(tmp_path, base + quiet_off)
        assert cage_cli.main(["enroll", str(toml)]) == 0
        out = capsys.readouterr().out
        assert "quiet hours: none (declared off)" in out, label
        assert "run-now" not in out, \
            f"{label} cage: offered a one-shot bypass for a disabled guard"
        if label == "manual":
            # only the no-schedule branch has this sentence to lose; asserting
            # it for the scheduled shape would be an assertion that cannot
            # fail, which reads as coverage nobody has
            assert "Quiet hours still apply" not in out

        # ...and the same cage WITH quiet hours is still told how to bypass
        # them once — the instruction is gated, not deleted
        toml.write_text(base)
        assert cage_cli.main(["enroll", str(toml)]) == 0
        assert "run-now" in capsys.readouterr().out, label


def test_enroll_says_the_plist_removal_does_not_unload_launchd(tmp_path, capsys):
    """Deleting the cage-dir plist cannot stop
    a job launchd already bootstrapped from ~/Library/LaunchAgents — it keeps
    firing this same run.sh on the old schedule. Enroll must say so, because
    only a human can run `launchctl bootout`."""
    toml = write_toml(tmp_path)
    cage_render.enroll(cage_config.load(toml))
    toml.write_text(MANUAL_TOML)
    assert cage_cli.main(["enroll", str(toml)]) == 0
    out = capsys.readouterr().out
    assert "does NOT unload" in out
    assert "launchctl bootout gui/$UID/com.sampleproj.cage" in out


def test_cli_validate_reports_triggers_and_quiet_hours(tmp_path, capsys):
    assert cage_cli.main(["validate", str(write_toml(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "schedule 23:30" in out
    assert "quiet hours 05:00-23:00" in out


def _bash_rule_matches(rules: list[str], command: str) -> bool:
    """A `Bash(<prefix>:*)` rule matches the prefix itself or the prefix
    followed by a space. Claude Code documents `:*` as a trailing ` *`, and
    the space counts: `Bash(uv tool:*)` does not match `uv toolx`, and
    `Bash(python3 -m cage:*)` does not match `python3 -m cage.cli enroll`.
    A rule with no wildcard matches that exact command and nothing else."""
    for rule in rules:
        if rule.startswith("Bash(") and rule.endswith(":*)"):
            prefix = rule[len("Bash("):-len(":*)")]
            if command == prefix or command.startswith(prefix + " "):
                return True
        elif rule.startswith("Bash(") and rule.endswith(")") and "*" not in rule:
            if command == rule[len("Bash("):-1]:
                return True
    return False


def _permitted(profile: dict, command: str) -> bool:
    """Deny wins over allow; a command no allow matches is refused headless."""
    return (_bash_rule_matches(profile["allow"], command)
            and not _bash_rule_matches(profile["deny"], command))


_RUN_BRANCH = "auto/dev/20260915-120000_02"
_PREVIOUS_DENIES = [
    "Bash(git push origin main:*)", "Bash(git push --force:*)",
    "Bash(git push -f:*)", "Bash(gh pr merge:*)", "Bash(gh api:*)",
]


def _bound_profile(tmp_path: Path) -> tuple[dict, cage_config.CageConfig]:
    """The profile as run.sh hands it to the session: the rendered template
    with the run's branch substituted for the token."""
    body = MINIMAL_TOML.replace("[project]", '[project]\nbranch_prefix = "auto/dev/"')
    cfg = cage_config.load(write_toml(tmp_path, body))
    text = json.dumps(cage_render.render_profile(cfg))
    bound = text.replace(cage_render.RUN_BRANCH_TOKEN, _RUN_BRANCH)
    return json.loads(bound)["permissions"], cfg


_RUN_REFSPEC = f"{_RUN_BRANCH}:refs/heads/{_RUN_BRANCH}"


@pytest.mark.parametrize("command", [
    f"git push origin {_RUN_REFSPEC}",
    f"git push -u origin {_RUN_REFSPEC}",
    f"git push --set-upstream origin {_RUN_REFSPEC}",
])
def test_profile_allows_the_run_branch_push(tmp_path, command):
    profile, _ = _bound_profile(tmp_path)
    assert _permitted(profile, command), command
    pushes = sorted(r for r in profile["allow"] if "git push" in r)
    assert pushes == sorted([
        f"Bash(git push origin {_RUN_REFSPEC})",
        f"Bash(git push -u origin {_RUN_REFSPEC})",
        f"Bash(git push --set-upstream origin {_RUN_REFSPEC})",
    ]), pushes


@pytest.mark.parametrize("command", [
    # a destination git config chooses: push.default=upstream or a
    # remote.origin.push refspec maps these onto main
    f"git push origin {_RUN_BRANCH}",
    f"git push -u origin {_RUN_BRANCH}",
    f"git push --set-upstream origin {_RUN_BRANCH}",
    f"git push origin {_RUN_BRANCH}:{_RUN_BRANCH}",
    f"git push origin {_RUN_BRANCH}:refs/heads/main",
    f"git push origin HEAD:refs/heads/{_RUN_BRANCH}",
    # main
    "git push origin main",
    "git push -u origin main",
    "git push --set-upstream origin main",
    "git push origin HEAD:main",
    "git push origin HEAD:refs/heads/main",
    f"git push origin {_RUN_BRANCH}:main",
    f"git push origin {_RUN_BRANCH} main",
    f"git push -u origin {_RUN_BRANCH}:main",
    # force, in every spelling git accepts
    f"git push --force origin {_RUN_BRANCH}",
    f"git push -f origin {_RUN_BRANCH}",
    f"git push origin {_RUN_BRANCH} --force",
    f"git push origin {_RUN_BRANCH} -f",
    f"git push -u origin {_RUN_BRANCH} --force",
    f"git push -uf origin {_RUN_BRANCH}",
    f"git push -fu origin {_RUN_BRANCH}",
    f"git push --force-with-lease origin {_RUN_BRANCH}",
    f"git push --force-with-lease={_RUN_BRANCH} origin {_RUN_BRANCH}",
    f"git push --force-with-lease --force-if-includes origin {_RUN_BRANCH}",
    f"git push origin +{_RUN_BRANCH}",
    f"git push -u origin +{_RUN_BRANCH}",
    f"git push origin +{_RUN_BRANCH}:{_RUN_BRANCH}",
    f"git push origin +HEAD:{_RUN_BRANCH}",
    "git push --mirror origin",
    # another branch, including another run's
    "git push origin auto/dev/20260101-000000",
    "git push -u origin auto/dev/20260101-000000",
    f"git push origin {_RUN_BRANCH}x",
    f"git push origin {_RUN_BRANCH}:auto/dev/other",
    f"git push origin {_RUN_BRANCH} auto/dev/other",
    "git push origin release",
    "git push --all origin",
    f"git push origin --delete {_RUN_BRANCH}",
    "git push",
    "git push origin",
])
def test_profile_denies_every_other_push(tmp_path, command):
    profile, _ = _bound_profile(tmp_path)
    assert not _permitted(profile, command), (
        f"the run profile lets a caged session run: {command}")


def test_profile_keeps_every_deny_and_seals_the_cage(tmp_path):
    profile, cfg = _bound_profile(tmp_path)
    assert set(_PREVIOUS_DENIES) <= set(profile["deny"])
    # a project's extra_allow cannot reopen the leading main or force spellings
    for command in ("git push -u origin main", "git push --set-upstream origin main",
                    f"git push --force-with-lease origin {_RUN_BRANCH}",
                    "git push --mirror origin"):
        assert _bash_rule_matches(profile["deny"], command), command
    assert not any(r.endswith("auto/dev/:*)") for r in profile["allow"])
    assert f"Read(/{cfg.cage_dir}/**)" in profile["deny"]
    assert f"Write(/{cfg.live_checkout}/**)" in profile["deny"]
    assert "additionalDirectories" not in profile  # none configured


@pytest.mark.parametrize("rule,command,matches", [
    ("Bash(uv tool:*)", "uv tool run --from . cage", True),
    ("Bash(uv tool:*)", "uv tool", True),
    ("Bash(uv tool:*)", "uv toolx", False),
    ("Bash(uvx:*)", "uvxx --from . cage", False),
    ("Bash(python3 -m cage:*)", "python3 -m cagex", False),
    ("Bash(python3 -m cage:*)", "python3 -m cage.cli enroll /cage/cage.toml",
     False),
    ("Bash(python3 -m cage.cli:*)",
     "python3 -m cage.cli enroll /cage/cage.toml", True),
])
def test_the_rule_matcher_counts_the_space_before_the_wildcard(
        rule, command, matches):
    """The deny cells below are only as true as this matcher. A plain
    prefix test passed a command no rule denies, so the matcher is held to
    the documented reading, in both directions."""
    assert _bash_rule_matches([rule], command) is matches, (rule, command)


@pytest.mark.parametrize("command,allowed_by_base", [
    ("uv run --project . cage enroll /cage/cage.toml", True),
    ("uv run --locked --project . cage enroll /cage/cage.toml", True),
    # `Bash(uv:*)` is `uv *`, which `uvx` does not match: the base never
    # allowed it, and the deny holds against a project's extra_allow.
    ("uvx --from . cage enroll /cage/cage.toml", False),
    ("uv tool run --from . cage enroll /cage/cage.toml", True),
    # the one `python3 -m` spelling that runs: cage/ has no __main__.py
    ("python3 -m cage.cli enroll /cage/cage.toml", True),
])
def test_profile_denies_the_re_spelled_platform_invocations(
        tmp_path, command, allowed_by_base):
    """The base allow is `Bash(uv:*)` and `Bash(python3:*)`, so every one of
    these would run: from a worktree that CONTAINS the cage package (the
    platform's own cage), each renders the worktree's run.sh — which the
    session can have edited — into the live cage dir. The Write/Edit denies
    on that dir are tool-level and do not govern Bash. No caged session
    needs these prefixes: the warden shims call uv internally, which
    permission matching never sees, and the platform probe runs in run.sh
    before the session starts."""
    profile = cage_render.render_profile(
        cage_config.load(write_toml(tmp_path)))["permissions"]
    assert _bash_rule_matches(profile["allow"], command) is allowed_by_base, (
        f"the base allow list changed under this cell: {command}")
    assert _bash_rule_matches(profile["deny"], command), (
        f"the rendered profile lets a caged session run: {command}")


@pytest.mark.parametrize("command", [
    ".warden/bin/warden verify --scope tests",
    "uv run --locked pytest -q",
    "uv run pytest -q",
    "uv sync --locked",
    "python3 -m pytest -q",
])
def test_profile_still_allows_what_a_caged_session_runs(tmp_path, command):
    """The bound on those denies: the spellings a caged session does type —
    the shim, the test suite, a sync — stay allowed and match no deny."""
    profile = cage_render.render_profile(
        cage_config.load(write_toml(tmp_path)))["permissions"]
    assert _bash_rule_matches(profile["allow"], command), command
    assert not _bash_rule_matches(profile["deny"], command), (
        f"a deny caught an ordinary session command: {command}")


def test_runner_is_valid_bash():
    script_path = Path(cage_render.__file__).parent / "run.sh"
    subprocess.run(["bash", "-n", str(script_path)], check=True)


# ---------- CLI --------------------------------------------------------------

def test_cli_validate_ok_and_config_error(tmp_path, capsys):
    toml = write_toml(tmp_path)
    assert cage_cli.main(["validate", str(toml)]) == 0
    assert "sampleproj" in capsys.readouterr().out
    toml.write_text(MINIMAL_TOML + "[bogus]\nx = 1\n")
    assert cage_cli.main(["validate", str(toml)]) == 2


def test_cli_enroll_renders_cage_dir(tmp_path, capsys):
    toml = write_toml(tmp_path)  # cage dir defaults to the toml's directory
    assert cage_cli.main(["enroll", str(toml)]) == 0
    err = capsys.readouterr().err
    assert "prompt.md" in err  # hand-authored prompt missing -> loud warning
    assert "DISABLED" in err   # empty forbidden_paths -> loud warning
    runner = tmp_path / "run.sh"
    assert runner.read_text() == cage_render.runner_script()  # byte-identical
    assert runner.stat().st_mode & 0o111
    assert (tmp_path / "cage.env").is_file()
    assert (tmp_path / "com.sampleproj.cage.plist").is_file()
    profile = json.loads((tmp_path / "cage-profile.json").read_text())
    assert profile["permissions"]["defaultMode"] == "acceptEdits"


def test_enroll_is_idempotent(tmp_path):
    toml = write_toml(tmp_path)
    first = {p.name: p.read_bytes() for p in cage_render.enroll(cage_config.load(toml))}
    second = {p.name: p.read_bytes() for p in cage_render.enroll(cage_config.load(toml))}
    assert first == second


def test_enroll_refuses_foreign_cage_dir(tmp_path, capsys):
    # A toml written elsewhere (e.g. by a caged session in its worktree) must
    # not be able to point [cage].dir at an existing cage and re-render it.
    victim = tmp_path / "victim-cage"
    victim.mkdir()
    body = MINIMAL_TOML + f'[cage]\ndir = "{victim}"\n'
    toml = write_toml(tmp_path, body)
    assert cage_cli.main(["enroll", str(toml)]) == 2
    assert "beside its own toml" in capsys.readouterr().err
    assert not (victim / "run.sh").exists()


def test_cli_filesystem_error_exits_2(tmp_path, capsys):
    toml = write_toml(tmp_path)
    (tmp_path / "run.sh").mkdir()  # write_text on a directory -> OSError
    assert cage_cli.main(["enroll", str(toml)]) == 2
    assert "filesystem error" in capsys.readouterr().err


# ---------- distill equivalence (the generalization acceptance) --------------

def test_distill_cage_regenerates_from_config(tmp_path):
    toml = tmp_path / "cage.toml"
    shutil.copy(GOLDEN / "distill-cage.toml", toml)
    cfg = cage_config.load(toml)

    # profile: exactly the hand-built distill profile, no drift either way.
    # SET comparison is the contract, on purpose: the
    # golden is an INDEPENDENT target (see the toml's header), so it is NOT
    # byte-reproducible by render_profile — rule ORDER is semantics-free for
    # allow/deny lists and the golden's ordering predates the renderer's.
    # Do not "refresh" this file from a render: a byte-diff against a fresh
    # render is expected, and regenerating would collapse the oracle into
    # the thing it checks.
    golden = json.loads((GOLDEN / "distill-cage-profile.json").read_text())
    rendered = cage_render.render_profile(cfg)
    for key in ("allow", "deny"):
        got = set(rendered["permissions"][key])
        want = set(golden["permissions"][key])
        assert got == want, (f"{key} drift — missing: {sorted(want - got)}, "
                             f"extra: {sorted(got - want)}")
    assert rendered["permissions"]["additionalDirectories"] == \
        golden["permissions"]["additionalDirectories"]
    assert rendered["permissions"]["defaultMode"] == golden["permissions"]["defaultMode"]

    # No scheduler: the cage this mirrors declared [triggers.manual] only, so
    # there is no plist to render and enroll must refuse to invent one
    # (the schedule is deprecated, not renamed). The
    # scheduled-render path keeps its own coverage in the render_plist tests.
    assert cfg.triggers == ("manual",)
    assert cfg.schedule is None
    with pytest.raises(cage_config.CageConfigError):
        cage_render.render_plist(cfg)
    assert cfg.cage_dir == Path("/Users/me/workspace/cage")
    assert cfg.log == Path("/Users/me/Library/Logs/distill-cage.log")

    # forbidden-path regex: byte-identical to the cage this fixture mirrors.
    # graph.yaml is in there because that cage guards the org constitution
    # too, and validate-nightshift-docs.sh names a REAL file in distill's own
    # tree — that rename is distill's to make, not this platform's.
    assert cfg.forbidden_regex == (
        r"^(\.warden/|tools/warden/|repo\.yaml$|graph\.yaml$|\.github/"
        r"|supabase/migrations/|\.claude/"
        r"|scripts/validate-nightshift-docs\.sh$|\.githooks/)")

    # env: the platform probe and scrub list that cage was configured with
    env = cage_render.render_env(cfg, home=Path("/Users/me"))
    assert "CAGE_PLATFORM_PROBE='.warden/bin/warden --help'" in env
    assert "CAGE_SCRUB_PATHS=_bmad-output" in env
    assert "CAGE_GITHUB=NightWatchEng/distill" in env


# ---------- resumability: an interrupted run continues, never abandons ----

def test_resume_budget_renders_and_is_bounded(tmp_path):
    body = MINIMAL_TOML + "[limits]\nmax_resumes = 3\n"
    cfg = cage_config.load(write_toml(tmp_path, body))
    assert cfg.max_resumes == 3
    assert "CAGE_MAX_RESUMES=3" in cage_render.render_env(cfg, home=Path("/h"))
    with pytest.raises(cage_config.CageConfigError, match="max_resumes"):
        cage_config.load(write_toml(tmp_path, MINIMAL_TOML +
                                    "[limits]\nmax_resumes = 99\n"))


def test_resume_defaults_to_two(tmp_path):
    cfg = cage_config.load(write_toml(tmp_path))
    assert cfg.max_resumes == 2


def test_runner_resumes_only_on_interruption_with_a_checkpoint():
    r = cage_render.runner_script()
    # only timeout/usage-limit are resumable — a blocked bead already said why
    assert "timeout|usage-limit)" in r
    assert '*) rm -f "$RESUME_STATE" ;;' in r
    # the bead comes from the checkpoint: a killed session wrote no summary
    assert "P_BEAD=$(grep -m1 '^bead:' \"$PROGRESS\"" in r
    # budget enforced before any worktree decision
    assert '[ "$RESUME_ATTEMPT" -ge "$CAGE_MAX_RESUMES" ]' in r
    # a continuation must NOT reset the branch to origin/main
    assert 'git -C "$WORKTREE" checkout "$BRANCH" --quiet' in r


def test_runner_preserves_every_hard_stop_around_resume():
    r = cage_render.runner_script()
    for stop in ('cage-off', 'wake-coalesced fire',
                 'PRs already open', "main CI is",
                 'FORBIDDEN-PATH', 'prompt.md missing'):
        assert stop in r, f"resume work dropped a hard stop: {stop!r}"


def test_reporting_functions_survive_an_unbound_run_id(tmp_path):
    """skip() must still ledger and notify when RUN_ID does not exist yet.

    notify()/ledger()/skip() sit above the run-identity block so the run-id
    claim cannot fail silently. That leaves a ~20-line window where the
    functions are callable but RUN_ID is not yet assigned, and the runner sets
    `set -u`: a stop added in that window would abort the loudest-stop
    function with "RUN_ID: unbound variable" and produce no report, no ledger
    row and no notification — the silent failure that placement exists to
    prevent.

    Executes the real function bodies, lifted out of the shipped runner, with
    RUN_ID deliberately never set.
    """
    runner = cage_render.runner_script()
    start = runner.index("# ---------- reporting surface")
    end = runner.index("# ---------- run identity")
    block = runner[start:end]
    assert "notify()" in block and "ledger()" in block and "skip()" in block

    ledger = tmp_path / "ledger.csv"
    log = tmp_path / "cage.log"
    script = tmp_path / "early-stop.sh"
    script.write_text(
        "#!/bin/bash\nset -u\n"
        # shell-quoted: a basetemp or TMPDIR with a space in it would
        # otherwise fail this test for a reason it does not pin (the suite's
        # own convention — see test_env_values_are_shell_quoted)
        f"LOG={shlex.quote(str(log))}\nLEDGER={shlex.quote(str(ledger))}\n"
        "CAGE_PROJECT=sampleproj\nCHECK_MODE=0\n"
        + block +
        '\nskip "a stop raised before the run identity exists"\n')
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    assert "unbound variable" not in proc.stderr, proc.stderr
    lines = ledger.read_text().splitlines()
    assert lines[0] == ",".join(cage_measure.HEADER), lines
    [row], bad = cage_measure.parse(lines)
    assert not bad, lines
    assert row[2] == "skipped", row
    assert "before the run identity exists" in row[4], row
    assert row[8] == "unclaimed", "an unbound RUN_ID reached the ledger raw"
    assert "NOTIFY:" in log.read_text()


def _ledger_fn(tmp_path, calls: str) -> subprocess.CompletedProcess:
    """Run the shipped runner's own ledger() with LEDGER in TMP_PATH."""
    runner = cage_render.runner_script()
    start = runner.index("ledger() {")
    body = runner[start:runner.index("\n}\n", start) + 3]
    script = tmp_path / "ledger-fn.sh"
    script.write_text("#!/bin/bash\nset -u\n"
                      f"LEDGER={shlex.quote(str(tmp_path / 'ledger.csv'))}\n"
                      "RUN_ID=20260927-120000\n" + body + calls)
    return subprocess.run(["bash", str(script)], capture_output=True, text=True)


def test_ledger_writes_its_header_once_into_a_new_file(tmp_path):
    proc = _ledger_fn(tmp_path, 'ledger done item-1 7 "first"\n'
                                'ledger skipped "" "" "second"\n')
    assert proc.returncode == 0, proc.stderr
    lines = (tmp_path / "ledger.csv").read_text().splitlines()
    assert lines[0] == ",".join(cage_measure.HEADER)
    assert len(lines) == 3, "the header is written once, not per row"
    rows, bad = cage_measure.parse(lines)
    assert not bad and [r[4] for r in rows] == ["first", "second"]


def test_ledger_leaves_a_legacy_headerless_ledger_headerless(tmp_path):
    legacy = "2026-09-20,item-1,done,5,old note,builder,10,1,20260920-000000\n"
    (tmp_path / "ledger.csv").write_text(legacy)
    assert _ledger_fn(tmp_path, 'ledger done item-2 6 "new"\n').returncode == 0
    lines = (tmp_path / "ledger.csv").read_text().splitlines()
    assert lines[0] == legacy.strip(), "a header mid-file would be a row"
    rows, bad = cage_measure.parse(lines)
    assert not bad and [r[3] for r in rows] == ["5", "6"]


def test_ledger_quotes_a_note_with_quotes_commas_and_newlines(tmp_path):
    note = 'refused: "x", then\nline two'
    proc = _ledger_fn(tmp_path, f"ledger failed item-1 '' {shlex.quote(note)} "
                                "builder 30 2\n")
    assert proc.returncode == 0, proc.stderr
    lines = (tmp_path / "ledger.csv").read_text().splitlines()
    assert len(lines) == 2, f"the note split the row: {lines}"
    [row], bad = cage_measure.parse(lines)
    assert not bad
    assert row[4] == 'refused: "x"; then line two'
    assert row[5:] == ["builder", "30", "2", "20260927-120000"]


def test_ledger_strips_a_stray_quote_from_the_unquoted_fields(tmp_path):
    """Round-1 regression: node, bead and outcome come from the session's
    .run-summary as awk's second token, so `node: "builder agent"` hands
    ledger() '"builder'. Written raw it fused this run with the next."""
    proc = _ledger_fn(tmp_path, 'ledger \'"done\' \'"item-1\' 10 "first" \'"builder\' 100 1\n'
                                'ledger done item-2 12 "second" builder 100 1\n')
    assert proc.returncode == 0, proc.stderr
    rows, bad = cage_measure.parse(
        (tmp_path / "ledger.csv").read_text().splitlines())
    assert not bad and len(rows) == 2, rows
    assert rows[0][1:3] == ["item-1", "done"] and rows[0][5] == "builder"


def test_ledger_appends_trajectory_without_moving_existing_columns():
    r = cage_render.runner_script()
    line = [ln for ln in r.splitlines() if ln.strip().startswith('echo "$(date +%F),')][0]
    # date,bead,outcome,pr,note,node,duration,attempt,run_id — readers
    # indexing 0-4 hold, and run_id lands LAST so 0-7 keep holding too
    assert line.count("${") >= 8
    # ${RUN_ID:-unclaimed}, not ${RUN_ID}: the reporting functions are defined
    # before the run identity exists, so a bare deref would abort them under
    # `set -u`. Column position unchanged.
    assert '${6:-},${7:-1},${RUN_ID:-unclaimed}' in line
