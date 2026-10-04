"""warden certify --goal — the redesign's five run-time evidence items.

Every verdict of every item is driven from a tmp-path repo: PASS, FAIL and,
where the item can say it, UNMEASURED. Nothing here reads prose, and the one
CLI test runs the real `warden certify --goal --report-only` on a fixture
repo, because the report is what CI posts.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from warden import __version__
from warden import goal
from warden.goal import FAIL, PASS, UNMEASURED

ROOT = Path(__file__).parent.parent


# --------------------------------------------------------------------------- #
# Fixture writers: the shapes the checks read, nothing more
# --------------------------------------------------------------------------- #

def _attest(root: Path, name: str, *, reviewed_at: str, rounds=(1,),
            base_sha: str = "b" * 40, roster="verified", bead: str | None = None,
            record_rounds=(), patch_ids=()) -> Path:
    """One attest shard. `rounds` are the roster entries' round numbers;
    `roster=None` leaves `roster_verification` absent, the pre-field shape.
    `record_rounds` puts round numbers on finding records instead;
    `patch_ids` fills `range_patch_ids`."""
    shard_dir = root / goal.ATTEST_DIR
    shard_dir.mkdir(parents=True, exist_ok=True)
    doc = {"schema": 1, "source": "attest", "sha": "a" * 40, "base_sha": base_sha,
           "rules_version": "test", "reviewed_at": reviewed_at, "verdict": "clean",
           "reviewers": [{"role": "code-reviewer", "agent": "x", "returned": True,
                          "findings": 0, "output": "r.json",
                          **({"round": r} if r is not None else {})}
                         for r in rounds],
           "records": [{"round": r, "rule_id": "r", "id": f"i{r}"}
                       for r in record_rounds]}
    if roster is not None:
        doc["roster_verification"] = roster
    if bead:
        doc["bead"] = bead
    if patch_ids:
        doc["range_patch_ids"] = list(patch_ids)
    path = shard_dir / f"{name}.json"
    path.write_text(json.dumps(doc))
    return path


def _decide(root: Path, name: str, scope) -> Path:
    shard_dir = root / goal.DECIDE_DIR
    shard_dir.mkdir(parents=True, exist_ok=True)
    path = shard_dir / f"{name}.json"
    path.write_text(json.dumps({"schema": 1, "decided": "d", "why": "w",
                                "scope": scope}))
    return path


def _backtest(root: Path, name: str, action: str) -> Path:
    bt = root / goal.BACKTEST_DIR
    bt.mkdir(parents=True, exist_ok=True)
    path = bt / f"{name}.json"
    path.write_text(json.dumps({"schema": 1, "rule_id": "r", "action": action}))
    return path


def _workflow(root: Path) -> Path:
    path = root / goal.MEMORY_WATCH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("on:\n  schedule:\n    - cron: '0 6 * * 1'\njobs: {}\n")
    return path


def _ledger_row(outcome: str, pr: str, date: str = "2026-09-21") -> str:
    return f"{date},item-1,{outcome},{pr},note,builder,100,1,run-1"


def _ci_proof(root: Path, *, run: str = 'bash scripts/init-proof.sh "$RUNNER_TEMP/fresh"',
              name: str | None = "warden init proof", job: dict | None = None,
              step: dict | None = None, before: dict | None = None) -> Path:
    """One job whose step runs RUN; JOB/STEP add keys, BEFORE puts jobs first."""
    path = root / goal.CI_WORKFLOW
    path.parent.mkdir(parents=True, exist_ok=True)
    (root / goal.INIT_PROOF_SCRIPT).parent.mkdir(exist_ok=True)
    (root / goal.INIT_PROOF_SCRIPT).write_text("#!/bin/sh\n")
    proof = {"runs-on": "ubuntu-latest", **(job or {}),
             "steps": [{"uses": "actions/checkout@sha"},
                       {"name": "prove", "run": run, **(step or {})}]}
    proof.update({"name": name} if name else {})
    path.write_text(json.dumps({"on": ["pull_request"],
                                "jobs": {**(before or {}), "init-proof": proof}}))
    return path


def _readme(root: Path, certify: str = "warden certify --level 3") -> Path:
    path = root / goal.README
    path.write_text("# x\n\n## Try it\n\ntext\n\n```sh\nuv tool install x\nwarden init\n"
                    f"{certify}\n```\n\n## Next\n")
    return path


# --------------------------------------------------------------------------- #
# enrollment
# --------------------------------------------------------------------------- #

def test_enrollment_passes_on_a_decide_shard_scoped_goal_enrollment(tmp_path):
    _decide(tmp_path, "other", "binds the ship seam only")
    path = _decide(tmp_path, "run", "goal:enrollment https://example.test/run/1")
    v = goal.check_enrollment(tmp_path)
    assert v.state == PASS
    assert v.evidence.startswith("collaborator: ")
    assert path.relative_to(tmp_path).as_posix() in v.evidence


def test_enrollment_is_unmeasured_without_the_scope_and_names_it(tmp_path):
    v = goal.check_enrollment(tmp_path)
    assert v.state == UNMEASURED
    assert "goal:enrollment" in v.evidence and ".warden/memory/decide/" in v.evidence
    assert ".github/workflows/ci.yml is missing" in v.evidence, "the mechanics half is named too"
    _decide(tmp_path, "other", "something else")
    _decide(tmp_path, "not-a-string", ["goal:enrollment"])
    v = goal.check_enrollment(tmp_path)
    assert v.state == UNMEASURED, "a list scope is not the string the item reads"


def test_enrollment_reads_the_scope_token_not_a_mention_inside_prose(tmp_path):
    """A ruling that MENTIONS the literal is not the record: decide scopes
    are sentences, and an exclusion clause is their house style."""
    _decide(tmp_path, "exclusion", "Binds the ladder's roster exemption only; "
            "it does not touch goal:enrollment, which is still unrecorded")
    _decide(tmp_path, "prefix", "goal:enrollment-later is a different scope")
    assert goal.check_enrollment(tmp_path).state == UNMEASURED
    _decide(tmp_path, "run", "goal:enrollment https://example.test/run/1")
    assert goal.check_enrollment(tmp_path).state == PASS
    _decide(tmp_path, "bare", "goal:enrollment")
    assert goal.check_enrollment(tmp_path).state == PASS


@pytest.mark.parametrize("scope", [
    "goal:enrollment, https://example.test/run/1",
    "goal:enrollment: https://example.test/run/1",
    "goal:enrollment; the invited collaborator's run",
    "goal:enrollment."])
def test_enrollment_token_may_carry_trailing_punctuation(tmp_path, scope):
    """The house style writes the token with a comma or colon after it as
    readily as with a space; a record spelled that way is the record."""
    _decide(tmp_path, "run", scope)
    v = goal.check_enrollment(tmp_path)
    assert v.state == PASS, v.evidence


def test_enrollment_passes_on_the_ci_job_that_runs_the_readme_block(tmp_path):
    _ci_proof(tmp_path)
    _readme(tmp_path)
    v = goal.check_enrollment(tmp_path)
    assert v.state == PASS, v.evidence
    assert v.evidence.startswith("mechanics: CI job 'warden init proof' has a step "
                                 "invoking scripts/init-proof.sh as a gate")
    assert "`warden certify --level 3` asks Level 3; no collaborator run is recorded yet" in v.evidence
    assert goal.ci_proof(tmp_path)[0] == "warden init proof"
    _ci_proof(tmp_path, name=None)
    assert goal.ci_proof(tmp_path)[0] == "init-proof", "a job with no name is named by its id"
    _decide(tmp_path, "run", "goal:enrollment https://example.test/run/1")
    v = goal.check_enrollment(tmp_path)
    assert v.evidence.startswith("collaborator: ") and "mechanics" not in v.evidence


@pytest.mark.parametrize("certify, named", [
    ("warden certify --level 1", "asks Level 1, below 2"),
    ("warden certify", "no `warden certify --level N` line"),
    ("warden certify --level 3 || true", "no `warden certify --level N` line"),
])
def test_enrollment_ci_job_needs_the_readme_block_to_ask_level_2_or_above(
        tmp_path, certify, named):
    _ci_proof(tmp_path)
    _readme(tmp_path, certify)
    v = goal.check_enrollment(tmp_path)
    assert v.state == UNMEASURED and named in v.evidence, v.evidence


def test_enrollment_ci_job_must_run_the_init_proof_script(tmp_path):
    _ci_proof(tmp_path, run="uv run pytest tests/test_init.py")
    _readme(tmp_path)
    v = goal.check_enrollment(tmp_path)
    assert v.state == UNMEASURED
    assert "no job in .github/workflows/ci.yml has a step invoking scripts/init-proof.sh" \
        in v.evidence


@pytest.mark.parametrize("run", [
    "shellcheck scripts/init-proof.sh", "# bash scripts/init-proof.sh\necho skipped",
    "echo scripts/init-proof.sh", 'bash scripts/init-proof.sh "$RUNNER_TEMP/fresh" || true',
    'bash scripts/init-proof.sh "$RUNNER_TEMP/fresh"; true', "bash scripts/init-proof.sh x && echo ok",
    'bash scripts/init-proof.sh \\\n  "$RUNNER_TEMP/fresh" || true',
    "set +e\nbash scripts/init-proof.sh fresh\n",
    "bash scripts/init-proof.sh fresh ||true", "bash scripts/init-proof.sh fresh;true",
    "bash scripts/init-proof.sh fresh|true", "bash scripts/init-proof.sh fresh &",
    'bash "$GITHUB_WORKSPACE/scripts/init-proof.sh" || echo "proof failed"',
    'bash "scripts/init-proof.sh" fresh && echo "ok"', "bash scripts/init-proof.sh fresh2&",
    "bash ../platform/scripts/init-proof.sh fresh", "bash /opt/x/scripts/init-proof.sh",
])
def test_enrollment_ci_step_must_invoke_the_script_not_mention_it(tmp_path, run):
    """A mention (lint, comment, echo) or a run behind a form that owns the
    exit status — spaced or not, continued, `set +e`, `&&`, `&` — is no gate."""
    _ci_proof(tmp_path, run=run)
    _readme(tmp_path)
    v = goal.check_enrollment(tmp_path)
    assert v.state == UNMEASURED, v.evidence
    assert goal.ci_proof(tmp_path)[0] is None


@pytest.mark.parametrize("run", [
    'bash scripts/init-proof.sh "$RUNNER_TEMP/fresh"', "./scripts/init-proof.sh fresh",
    "set -euo pipefail\n# the proof\nscripts/init-proof.sh fresh\n",
    "sh scripts/init-proof.sh fresh", "bash -e scripts/init-proof.sh fresh",
    'bash "scripts/init-proof.sh" "$RUNNER_TEMP/fresh" > out 2>&1',
    'bash "$GITHUB_WORKSPACE/scripts/init-proof.sh" fresh',
    "env NIGHTGATE_REQUIRE_TOOLCHAIN=1 bash scripts/init-proof.sh fresh",
    "timeout 900 bash scripts/init-proof.sh fresh",
    'bash scripts/init-proof.sh \\\n  "$RUNNER_TEMP/fresh"', "bash scripts/init-'proof'.sh x",
    'bash "${GITHUB_WORKSPACE}/scripts/init-proof.sh" > "out 2"',
    "# echo scripts/init-proof.sh || true\nbash scripts/init-proof.sh fresh",
])
def test_enrollment_names_the_job_that_invokes_the_script_not_an_earlier_mention(tmp_path, run):
    """Each spelling counts; a job ahead that only names the path does not."""
    lint = {"runs-on": "ubuntu-latest", "name": "lint",
            "steps": [{"run": "shellcheck scripts/init-proof.sh scripts/readme-try-it.sh"}]}
    _ci_proof(tmp_path, run=run, before={"lint": lint})
    _readme(tmp_path)
    assert goal.ci_proof(tmp_path)[0] == "warden init proof"


@pytest.mark.parametrize("run", [
    "echo scripts/init-proof.sh || true\nbash scripts/init-proof.sh fresh",
    "ls scripts/init-proof.sh | tee out\nbash scripts/init-proof.sh fresh",
])
def test_enrollment_earlier_line_naming_the_script_does_not_swallow_the_invocation(tmp_path, run):
    """The swallow read is the invocation line's: an earlier `echo ... || true`
    or `| tee` naming the path does not disarm the real invocation after it."""
    _ci_proof(tmp_path, run=run)
    _readme(tmp_path)
    assert goal.ci_proof(tmp_path)[0] == "warden init proof"
    assert goal.check_enrollment(tmp_path).state == PASS


@pytest.mark.parametrize("run, swallow", [
    ("echo scripts/init-proof.sh\nbash scripts/init-proof.sh fresh || true", "|| true"),
    ("set +e\necho scripts/init-proof.sh\nbash scripts/init-proof.sh fresh", "set +e"),
    ("echo scripts/init-proof.sh; set +e\nbash scripts/init-proof.sh fresh", "set +e"),
])
def test_enrollment_swallowed_invocation_after_a_mention_still_reads_swallowed(
        tmp_path, run, swallow):
    """Reading the invocation line alone keeps its own suffix and the run's
    block forms: a swallowed invocation still names what swallows it."""
    _ci_proof(tmp_path, run=run)
    _readme(tmp_path)
    v = goal.check_enrollment(tmp_path)
    assert v.state == UNMEASURED and goal.ci_proof(tmp_path)[0] is None
    assert f"the line carries {swallow}" in v.evidence, v.evidence


@pytest.mark.parametrize("job, step, named", [
    ({"continue-on-error": True}, None, "the job sets continue-on-error: true"),
    (None, {"continue-on-error": True}, "the step sets continue-on-error: true"),
    ({"if": "false"}, None, "the job's `if` is false"),
    ({"if": False}, None, "the job's `if` is false"),
    (None, {"if": "${{ false }}"}, "the step's `if` is false"),
    ({"continue-on-error": "${{ true }}"}, None, "the job sets continue-on-error: true"),
    (None, {"continue-on-error": "true"}, "the step sets continue-on-error: true"),
    (None, {"continue-on-error": "${{ matrix.advisory }}"},
     "the step sets continue-on-error: ${{ matrix.advisory }}"),
])
def test_enrollment_ci_job_that_cannot_fail_is_not_the_proof(tmp_path, job, step, named):
    """A job whose failure cannot go red is a run, not a gate; the evidence says why."""
    _ci_proof(tmp_path, job=job, step=step)
    _readme(tmp_path)
    v = goal.check_enrollment(tmp_path)
    assert v.state == UNMEASURED, v.evidence
    assert f"job 'init-proof' runs it but {named}" in v.evidence


def test_enrollment_ci_job_with_a_conditional_if_still_counts(tmp_path):
    _ci_proof(tmp_path, job={"if": "always() && needs.triage.outputs.fast != 'true'"},
              step={"continue-on-error": False})
    _readme(tmp_path, "warden certify --level=2")
    name, evidence = goal.ci_proof(tmp_path)
    assert name == "warden init proof" and "`warden certify --level=2` asks Level 2" in evidence


    (tmp_path / goal.README).unlink(), (tmp_path / goal.README).mkdir()
    assert "README.md could not be read: Is a directory" in goal.check_enrollment(tmp_path).evidence
    (tmp_path / goal.INIT_PROOF_SCRIPT).unlink()
    assert "scripts/init-proof.sh is not in the tree" in goal.check_enrollment(tmp_path).evidence


def test_enrollment_undecodable_readme_or_workflow_is_unmeasured_not_a_traceback(tmp_path):
    _ci_proof(tmp_path)
    (tmp_path / goal.README).write_bytes(b"## Try it\n```\n\xff\xfe\n```\n")
    v = goal.check_enrollment(tmp_path)
    assert v.state == UNMEASURED and "README.md is not UTF-8 text" in v.evidence
    _readme(tmp_path)
    (tmp_path / goal.CI_WORKFLOW).write_bytes(b"jobs: \xff\n")
    assert ".github/workflows/ci.yml is not UTF-8 text" in goal.check_enrollment(tmp_path).evidence


@pytest.mark.parametrize("text", [
    None,  # the README itself
    "# x\n\n## Try it\n\nprose\n\n```sh\na\n\nb\n\n```\n\n## Next\n",
    "## Try it\n```sh\na\n\n\n```\n",
    "## Try it\n```sh\na\fb\n```",
    "## Try it\n```sh\na\r\n```\n",
    "## Try it\r\n```sh\r\na\r\n```\r\n",
    "## Try it\n```sh\na\n",
])
def test_try_it_block_is_what_the_shell_script_prints(tmp_path, text):
    """The proof runs the awk reading, `certify --goal` this one: equal as
    bytes on the README and where newline translation would part them."""
    readme = tmp_path / "README.md"  # a copy: this repository's README is never written
    text = (ROOT / "README.md").read_bytes().decode() if text is None else text
    readme.write_bytes(text.encode())
    result = subprocess.run(["bash", str(ROOT / "scripts" / "readme-try-it.sh"), str(readme)],
                            capture_output=True, timeout=60)
    block = goal.try_it_block(text)
    assert (result.returncode, result.stdout) == \
        ((1, b"") if block is None else (0, block.encode())), result


def test_enrollment_names_a_ci_job_without_a_readme_or_block(tmp_path):
    _ci_proof(tmp_path)
    assert "README.md is missing" in goal.check_enrollment(tmp_path).evidence
    (tmp_path / goal.README).write_text("# x\n\n## Try it\n\nno fence\n\n## Next\n")
    assert "has no fenced block under '## Try it'" in goal.check_enrollment(tmp_path).evidence
    # a CRLF README's heading is `## Try it\r`, which names no section to
    # the shell script, so it names none here either
    (tmp_path / goal.README).write_bytes(b"## Try it\r\n```sh\r\nwarden certify --level 3\r\n```\r\n")
    assert "has no fenced block" in goal.check_enrollment(tmp_path).evidence


@pytest.mark.parametrize("text", ["jobs: [", "- a list\n", "jobs:\n  init-proof: 3\n",
                                  "jobs:\n  init-proof:\n    steps: [3, {uses: x}]\n"])
def test_enrollment_ci_workflow_it_cannot_read_is_unmeasured_not_an_error(tmp_path, text):
    _ci_proof(tmp_path)
    (tmp_path / goal.CI_WORKFLOW).write_text(text)
    _readme(tmp_path)
    assert goal.check_enrollment(tmp_path).state == UNMEASURED


def test_try_it_block_is_byte_for_byte_and_none_without_a_closed_fence():
    """Blank lines inside the fence are kept; a fence closed by a heading
    or by the end of the file is not a block; a fence under another
    heading is not this block."""
    assert goal.try_it_block("## Try it\n```sh\na\n\nb\n\n```\n") == "a\n\nb\n\n"
    assert goal.try_it_block("## Try it\n\n```sh\na\n## Next\n```\n") is None
    assert goal.try_it_block("## Try it\n```sh\na\n") is None
    assert goal.try_it_block("## Other\n```sh\na\n```\n## Try it\n") is None
    assert goal.try_it_block("## Try it\n```\n```\n") == ""


def test_enrollment_names_an_unreadable_shard_instead_of_dropping_it(tmp_path):
    (tmp_path / goal.DECIDE_DIR).mkdir(parents=True)
    (tmp_path / goal.DECIDE_DIR / "broken.json").write_text("{not json")
    v = goal.check_enrollment(tmp_path)
    assert v.state == UNMEASURED
    assert "1 unreadable shard(s) skipped" in v.evidence
    assert "broken.json" in v.evidence


# --------------------------------------------------------------------------- #
# one-round
# --------------------------------------------------------------------------- #

def _branches(root: Path, highest_rounds: list[int], *, start_day: int = 1):
    """One branch per entry, each its own base_sha, newest last, with roster
    entries for every round up to the entry's highest."""
    for i, highest in enumerate(highest_rounds):
        day = start_day + i
        base = f"{i:040x}"
        for r in range(1, highest + 1):
            _attest(root, f"b{i}-r{r}", reviewed_at=f"2026-09-{day:02d}T00:00:00+00:00",
                    rounds=(r,), base_sha=base)


def test_one_round_is_unmeasured_below_five_branches(tmp_path):
    assert goal.check_one_round(tmp_path).state == UNMEASURED
    _branches(tmp_path, [1, 1, 1, 1])
    v = goal.check_one_round(tmp_path)
    assert v.state == UNMEASURED
    assert "4 attested branch(es)" in v.evidence and "5 needed" in v.evidence


def test_one_round_passes_at_the_bar_and_prints_the_fraction(tmp_path):
    _branches(tmp_path, [1, 1, 1, 1, 1, 1, 1, 2, 3, 2])   # 7 of 10
    v = goal.check_one_round(tmp_path)
    assert v.state == PASS
    assert "7 of the 10 most recent" in v.evidence and "= 0.70" in v.evidence


def test_one_round_fails_below_the_bar(tmp_path):
    _branches(tmp_path, [1, 1, 1, 1, 1, 1, 2, 3, 2, 2])   # 6 of 10
    v = goal.check_one_round(tmp_path)
    assert v.state == FAIL
    assert "6 of the 10 most recent" in v.evidence and "= 0.60" in v.evidence


def test_one_round_measures_only_the_twenty_most_recent_branches(tmp_path):
    # five old multi-round branches, then twenty one-round ones: the window
    # holds the twenty, so the rate is 1.00, not 20/25
    _branches(tmp_path, [3] * 5 + [1] * 20)
    v = goal.check_one_round(tmp_path)
    assert v.state == PASS
    assert "20 of the 20 most recent" in v.evidence
    # and the other way round: the five old one-round branches fall out
    for p in (tmp_path / goal.ATTEST_DIR).glob("*.json"):
        p.unlink()
    _branches(tmp_path, [1] * 5 + [2] * 20)
    v = goal.check_one_round(tmp_path)
    assert v.state == FAIL and "0 of the 20 most recent" in v.evidence


def test_one_round_groups_a_branch_by_bead_before_base_sha(tmp_path):
    # two beads on ONE base: keyed by base_sha they would fold into a single
    # 2-round branch; keyed by bead they are one 1-round and one 2-round
    _branches(tmp_path, [1, 1, 1, 1])
    _attest(tmp_path, "x-r1", reviewed_at="2026-09-20T00:00:00+00:00", rounds=(1,),
            base_sha="c" * 40, bead="item-x")
    _attest(tmp_path, "y-r1", reviewed_at="2026-09-20T01:00:00+00:00", rounds=(1,),
            base_sha="c" * 40, bead="item-y")
    _attest(tmp_path, "y-r2", reviewed_at="2026-09-20T02:00:00+00:00", rounds=(2,),
            base_sha="c" * 40, bead="item-y")
    v = goal.check_one_round(tmp_path)
    assert "5 of the 6 most recent" in v.evidence, v.evidence


def test_one_round_keeps_a_rebased_branch_whole_by_its_patch_ids(tmp_path):
    """Two rounds of one branch whose base moved between them share the
    commits round 1 judged; keyed by base_sha alone the round-1 shard would
    read as a separate branch that finished in one round."""
    _branches(tmp_path, [2, 2, 2, 2])
    _attest(tmp_path, "r1", reviewed_at="2026-09-20T00:00:00+00:00", rounds=(1,),
            base_sha="1" * 40, patch_ids=("p-one",))
    _attest(tmp_path, "r2", reviewed_at="2026-09-20T01:00:00+00:00", rounds=(2,),
            base_sha="2" * 40, patch_ids=("p-one", "p-repair"))
    v = goal.check_one_round(tmp_path)
    assert "0 of the 5 most recent" in v.evidence, v.evidence
    assert "range_patch_ids" in v.evidence


def test_one_round_keeps_two_branches_on_one_base_apart_by_their_patch_ids(tmp_path):
    _branches(tmp_path, [1, 1, 1, 1])
    _attest(tmp_path, "a", reviewed_at="2026-09-20T00:00:00+00:00", rounds=(1,),
            base_sha="c" * 40, patch_ids=("p-a",))
    _attest(tmp_path, "b1", reviewed_at="2026-09-20T01:00:00+00:00", rounds=(1,),
            base_sha="c" * 40, patch_ids=("p-b",))
    _attest(tmp_path, "b2", reviewed_at="2026-09-20T02:00:00+00:00", rounds=(2,),
            base_sha="c" * 40, patch_ids=("p-b", "p-b-repair"))
    v = goal.check_one_round(tmp_path)
    assert "5 of the 6 most recent" in v.evidence, v.evidence


def test_one_round_leaves_out_a_branch_with_no_round_number(tmp_path):
    _branches(tmp_path, [1, 1, 1, 1, 1])
    _attest(tmp_path, "old", reviewed_at="2026-09-30T00:00:00+00:00", rounds=(None,),
            base_sha="d" * 40)
    v = goal.check_one_round(tmp_path)
    assert v.state == PASS
    assert "5 of the 5 most recent" in v.evidence
    assert "1 branch(es) carry no round number and are left out" in v.evidence


def test_one_round_reads_a_round_number_from_a_finding_record_too(tmp_path):
    _branches(tmp_path, [1, 1, 1, 1])
    _attest(tmp_path, "rec", reviewed_at="2026-09-30T00:00:00+00:00", rounds=(None,),
            base_sha="e" * 40, record_rounds=(1, 2))
    v = goal.check_one_round(tmp_path)
    assert "4 of the 5 most recent" in v.evidence, v.evidence


# --------------------------------------------------------------------------- #
# roster
# --------------------------------------------------------------------------- #

def test_roster_passes_when_the_newest_shard_says_verified(tmp_path):
    _attest(tmp_path, "z-old", reviewed_at="2026-09-01T00:00:00+00:00", roster=None)
    new = _attest(tmp_path, "a-new", reviewed_at="2026-09-02T00:00:00+00:00")
    v = goal.check_roster(tmp_path)
    assert v.state == PASS
    assert new.relative_to(tmp_path).as_posix() in v.evidence, (
        "newest is by reviewed_at, not by file name")


@pytest.mark.parametrize("value, found", [
    (None, "absent"), ("unverified-roster", "'unverified-roster'"),
    ("no-review-dir", "'no-review-dir'")])
def test_roster_fails_naming_the_value_found(tmp_path, value, found):
    _attest(tmp_path, "old", reviewed_at="2026-09-01T00:00:00+00:00")
    new = _attest(tmp_path, "new", reviewed_at="2026-09-02T00:00:00+00:00", roster=value)
    v = goal.check_roster(tmp_path)
    assert v.state == FAIL
    assert found in v.evidence and new.name in v.evidence


def test_roster_fails_with_no_shard_at_all(tmp_path):
    v = goal.check_roster(tmp_path)
    assert v.state == FAIL and ".warden/memory/attest/" in v.evidence


def test_roster_fails_when_the_newest_shard_cannot_be_read(tmp_path):
    """The newest file is the newest shard whether or not it parses; a
    verified shard before it is not a fall-back."""
    _attest(tmp_path, "20260901T000000Z-old", reviewed_at="2026-09-01T00:00:00+00:00")
    (tmp_path / goal.ATTEST_DIR / "20260902T000000Z-new.json").write_text(
        '{"schema": 1, "roster_verification": "unverified-roster"')
    v = goal.check_roster(tmp_path)
    assert v.state == FAIL
    assert "20260902T000000Z-new.json (newest) could not be read" in v.evidence


def test_roster_pass_still_names_an_unreadable_older_shard(tmp_path):
    (tmp_path / goal.ATTEST_DIR).mkdir(parents=True)
    (tmp_path / goal.ATTEST_DIR / "20260901T000000Z-old.json").write_text("{nope")
    _attest(tmp_path, "20260902T000000Z-new", reviewed_at="2026-09-02T00:00:00+00:00")
    v = goal.check_roster(tmp_path)
    assert v.state == PASS
    assert "1 unreadable shard(s) skipped" in v.evidence
    assert "20260901T000000Z-old.json" in v.evidence


# --------------------------------------------------------------------------- #
# memory
# --------------------------------------------------------------------------- #

def test_memory_passes_with_the_workflow_and_an_adopt_artifact(tmp_path):
    _workflow(tmp_path)
    _backtest(tmp_path, "r-promote-20260901", "promote")
    adopted = _backtest(tmp_path, "r-adopt-20260902", "adopt")
    v = goal.check_memory(tmp_path)
    assert v.state == PASS
    assert "memory-watch.yml" in v.evidence and adopted.name in v.evidence


def test_memory_fails_naming_the_missing_half(tmp_path):
    _workflow(tmp_path)
    _backtest(tmp_path, "r-promote-20260901", "promote")
    v = goal.check_memory(tmp_path)
    assert v.state == FAIL
    assert "action: adopt" in v.evidence and "memory-watch.yml is missing" not in v.evidence
    (tmp_path / goal.MEMORY_WATCH).unlink()
    _backtest(tmp_path, "r-adopt-20260902", "adopt")
    v = goal.check_memory(tmp_path)
    assert v.state == FAIL
    assert "memory-watch.yml is missing" in v.evidence and "action: adopt" not in v.evidence


def test_memory_names_an_unreadable_artifact_instead_of_a_missing_adoption(tmp_path):
    _workflow(tmp_path)
    (tmp_path / goal.BACKTEST_DIR).mkdir(parents=True)
    (tmp_path / goal.BACKTEST_DIR / "r-adopt-20260901.json").write_text(
        '{"schema": 1, "rule_id": "r", "action": "adopt"')
    v = goal.check_memory(tmp_path)
    assert v.state == FAIL
    assert "no readable artifact" in v.evidence
    assert "1 unreadable shard(s) skipped, first .warden/memory/backtests/r-adopt-20260901.json" in v.evidence


def test_memory_fails_naming_both_halves_when_both_are_missing(tmp_path):
    v = goal.check_memory(tmp_path)
    assert v.state == FAIL
    assert "memory-watch.yml is missing" in v.evidence
    assert "no readable artifact under .warden/memory/backtests/ records action: adopt" in v.evidence


def test_memory_adopt_shape_is_the_one_autonomy_writes():
    """The item keys on `action: adopt` in the backtests dir — the two facts
    `warden autonomy adopt`'s artifact writer fixes."""
    src = (ROOT / "warden" / "autonomy.py").read_text()
    assert '"action": "adopt"' in src
    assert goal.BACKTEST_DIR.as_posix() == ".warden/memory/backtests"
    assert '"backtests"' in src


# --------------------------------------------------------------------------- #
# cage
# --------------------------------------------------------------------------- #

def test_cage_is_unmeasured_without_a_ledger(tmp_path):
    v = goal.check_cage(tmp_path, None)
    assert v.state == UNMEASURED and "--ledger" in v.evidence


def test_cage_passes_at_ten_done_rows_with_a_pr_and_prints_n_of_10(tmp_path):
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("\n".join([_ledger_row("done", str(200 + i)) for i in range(10)]
                                + [_ledger_row("done", ""),          # no PR
                                   _ledger_row("FORBIDDEN-PATH", "300"),
                                   _ledger_row("skipped", ""),
                                   "2026-09-21,item,done,301",      # truncated
                                   ""]) + "\n")
    v = goal.check_cage(tmp_path, ledger)
    assert v.state == PASS
    assert "10/10 done rows with a PR number" in v.evidence
    assert "13 well-formed rows read" in v.evidence
    assert "1 malformed rows not counted, at line 14" in v.evidence


def test_cage_fails_below_ten(tmp_path):
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("\n".join(_ledger_row("done", str(i)) for i in range(9)) + "\n")
    v = goal.check_cage(tmp_path, ledger)
    assert v.state == FAIL and "9/10" in v.evidence
    ledger.write_text("")
    v = goal.check_cage(tmp_path, ledger)
    assert v.state == FAIL and "0/10" in v.evidence


def test_cage_reads_a_header_and_a_multi_line_quoted_note(tmp_path):
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("date,bead,outcome,pr,note,node,duration_s,attempt,run_id\n"
                      + "\n".join(_ledger_row("done", str(i)) for i in range(9))
                      + '\n2026-09-22,item-2,done,99,"refused, then\nfixed",'
                        "builder,100,1,run-2\n")
    v = goal.check_cage(tmp_path, ledger)
    assert v.state == PASS and "10/10" in v.evidence
    assert "(10 well-formed rows read)" in v.evidence, "the header is no row"


def test_cage_names_the_legacy_rows_it_cannot_read(tmp_path):
    """A pre-header ledger's unquoted multi-line note splits one run into
    three physical lines; the evidence line says so instead of dropping them."""
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("2026-09-20,item-1,done,7,refused: outside\n"
                      "  a.json: outside\n"
                      "  b.json: outside,builder,1620,1,run-1\n"
                      + _ledger_row("done", "8") + "\n")
    v = goal.check_cage(tmp_path, ledger)
    assert v.state == FAIL and "1/10" in v.evidence
    assert "3 malformed rows not counted, at line 1, 2, 3" in v.evidence


def test_cage_fails_on_a_ledger_it_cannot_read(tmp_path):
    v = goal.check_cage(tmp_path, tmp_path / "absent.csv")
    assert v.state == FAIL and "absent.csv" in v.evidence


def test_cage_columns_match_the_cage_measure_reader():
    """One ledger, two readers: the goal item's column indices are the
    runner's own, as `cage/measure.py` documents and indexes them."""
    from cage import measure
    assert (goal.LEDGER_DATE, goal.LEDGER_OUTCOME, goal.LEDGER_PR) == (
        measure._DATE, measure._OUTCOME, measure._PR)


# --------------------------------------------------------------------------- #
# run and render
# --------------------------------------------------------------------------- #

def _all_pass_repo(tmp_path: Path) -> Path:
    _decide(tmp_path, "run", "goal:enrollment https://example.test/run/1")
    _branches(tmp_path, [1] * 5)
    _workflow(tmp_path)
    _backtest(tmp_path, "r-adopt-20260902", "adopt")
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("\n".join(_ledger_row("done", str(i)) for i in range(10)) + "\n")
    return ledger


def test_run_orders_the_five_items_and_counts_passes(tmp_path):
    ledger = _all_pass_repo(tmp_path)
    doc = goal.run(tmp_path, ledger)
    assert [i["item"] for i in doc["items"]] == list(goal.ITEMS)
    assert doc["passed"] == 5 and doc["total"] == 5 and goal.all_pass(doc)
    doc = goal.run(tmp_path, None)
    assert doc["passed"] == 4 and not goal.all_pass(doc)
    assert doc["items"][4] == {"item": "cage", "state": UNMEASURED,
                               "evidence": doc["items"][4]["evidence"]}


def test_this_repository_ci_proves_the_readme_block_at_level_3():
    name, evidence = goal.ci_proof(ROOT)
    assert name == "warden init proof", evidence
    assert "`warden certify --level 3` asks Level 3" in evidence


def test_render_is_a_fixed_width_table_with_one_summary_line(tmp_path):
    text = goal.render(goal.run(tmp_path, None))
    lines = text.splitlines()
    assert len(lines) == 7 and lines[-1] == "GOAL: 0 of 5"
    assert lines[0].startswith("item        verdict     evidence")
    # every verdict starts in the same column, whatever the item's length
    starts = {line.index(state) for line in lines[1:6]
              for state in (PASS, FAIL, UNMEASURED) if f"  {state}  " in line}
    assert starts == {12}, lines
    assert [line.split()[0] for line in lines[1:6]] == list(goal.ITEMS)


# --------------------------------------------------------------------------- #
# the CLI, for real
# --------------------------------------------------------------------------- #

def _fixture_repo(tmp_path: Path) -> Path:
    root = tmp_path / "platform-src"
    (root / ".warden" / "rules").mkdir(parents=True)
    (root / ".warden" / "out").mkdir()
    (root / ".warden" / "rules" / "r.md").write_text(
        "---\nid: r\nseverity: LOW\nengine: claude\napplies_to: ['**']\n---\nbody\n")
    (root / "repo.yaml").write_text(
        "version: 1\nrepo: sim\n"
        "components:\n  app: {path: app/, lang: python, description: d}\n"
        "review:\n  rules_dir: .warden/rules\n  blocking_severities: [HIGH]\n"
        "risk_tiers:\n  - {glob: '**', tier: LOW}\n"
        "verify:\n  app:\n    - run: 'true'\n"
        f"platform:\n  pin: v{__version__}\n")
    return root


def _warden(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "warden.cli", *args], cwd=root,
                          capture_output=True, text=True)


def test_cli_goal_report_only_prints_the_table_and_exits_zero(tmp_path):
    root = _fixture_repo(tmp_path)
    _attest(root, "new", reviewed_at="2026-09-02T00:00:00+00:00")
    proc = _warden(root, "certify", "--goal", "--report-only")
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0].startswith("item        verdict     evidence")
    assert [line.split()[0] for line in lines[1:6]] == list(goal.ITEMS)
    assert lines[6] == "GOAL: 1 of 5", proc.stdout
    assert "roster      PASS" in proc.stdout
    assert "cage        UNMEASURED" in proc.stdout
    # the run dir carries the same verdicts as JSON
    art = sorted((root / ".warden" / "out").glob("*-certify/goal.json"))
    assert art and json.loads(art[-1].read_text())["passed"] == 1


def test_cli_goal_exits_one_unless_all_five_pass(tmp_path):
    root = _fixture_repo(tmp_path)
    proc = _warden(root, "certify", "--goal")
    assert proc.returncode == 1
    assert "goal not met" in proc.stderr and "cage=UNMEASURED" in proc.stderr
    ledger = _all_pass_repo(root)
    proc = _warden(root, "certify", "--goal", "--ledger", str(ledger))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert proc.stdout.splitlines()[-1] == "GOAL: 5 of 5"


def test_cli_goal_flags_do_not_mix_with_the_ladder_flags(tmp_path):
    root = _fixture_repo(tmp_path)
    assert _warden(root, "certify", "--goal", "--level", "2").returncode == 2
    assert _warden(root, "certify", "--goal", "--capability", "autonomous").returncode == 2
    assert _warden(root, "certify", "--report-only").returncode == 2
    assert _warden(root, "certify", "--ledger", "x.csv").returncode == 2
