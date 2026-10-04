"""The autonomy-ladder carve-out, judged by SHAPE.

The cage's forbidden-path post-check closes the PR on any committed write to
the gate surface. The ladder gives its machine tier permission to write
exactly four things under `.warden/` — a pause field in an existing rule file,
a NEW non-blocking rule file, the backtest artifact that change carries, and
the attestation shard a review round leaves under
`.warden/memory/attest/` — via a PR a human merges. Auto-closing those would
make the permission theoretical, and auto-closing the fourth made every
protocol-following unattended run impossible: `warden gate` refuses a PR with
no committed shard, and the freeze refused the shard.

What this file holds is the DECISION: given the committed diff, is every
forbidden path inside that carve-out? The runner's wiring around the verdict —
fail-closed on a missing shim, a crash, an unevaluable regex — is tested in
tests/test_cage_runner.py by the carve-out tests there
(test_a_crashing_carve_out_fails_closed, test_no_live_shim_means_no_carve_out,
test_a_regex_that_cannot_evaluate_is_never_carve_out_material and the rest).

Provenance is decided by shape, never by a marker, because a marker is
something a session can write. So every test here is about a TREE, built with
real git, and the checker reads it the way certify does: through `git show`,
never the filesystem.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from warden import autonomy as autonomy_mod

REASON = "auto-paused by the autonomy ladder on a 3-round refuting streak"


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _rule(rule_id: str, severity: str = "MEDIUM") -> str:
    return (f"---\nid: {rule_id}\nseverity: {severity}\nengine: claude\n"
            "applies_to: ['**']\n---\nbody\n")


def _backtest(rule_id: str, action: str = "pause", **over) -> str:
    """What `_write_pause_backtest` emits, in the fields the check reads."""
    doc = {"schema": 1, "rule_id": rule_id, "action": action, "judged": 12,
           "refuted": 9, "streak": 3, "note": REASON, "rules_version": "abc123"}
    doc.update(over)
    return json.dumps(doc, indent=2) + "\n"


ADOPT_REASON = ("adopted by the autonomy ladder's machine tier: a "
                "non-blocking rule that reports and cannot fail a build")


def _adopt_backtest(rule_id: str, severity: str = "MEDIUM", **over) -> str:
    """What `_write_adopt_backtest` emits, in the fields the check reads."""
    doc = {"schema": 1, "rule_id": rule_id, "action": "adopt",
           "severity": severity, "judged": 0, "note": ADOPT_REASON,
           "rules_version": "abc123"}
    doc.update(over)
    return json.dumps(doc, indent=2) + "\n"


def _attest_shard(sha: str, **over) -> str:
    """The shard envelope `warden memory ingest` writes, in the fields the
    check reads. The field set is copied from a REAL one — the round-2 shard
    the first completed unattended run committed on
    `auto/20260920-205434` — so a change to what ingest emits shows up here
    as a mismatch rather than as a fixture that agrees with nothing."""
    doc = {"schema": 1, "source": "attest", "sha": sha,
           "base_sha": "0" * 40, "rules_version": "4ca02b6a2eeb",
           "reviewed_at": "2026-09-21T04:12:28+00:00",
           "range_patch_ids": [], "verdict": "clean",
           "reviewers": [{"role": "code-reviewer", "returned": True}],
           "roster_verification": "verified", "round_binding": "bound",
           "records": []}
    # An override of None DELETES the key. `null` and ABSENT are different
    # shards: the envelope validator refuses a present-but-null `verdict`
    # before the event check ever runs, so a test for a MISSING field has to
    # be able to produce a missing one.
    for key, value in over.items():
        if value is None:
            doc.pop(key, None)
        else:
            doc[key] = value
    return json.dumps(doc, indent=2) + "\n"


@pytest.fixture
def repo(tmp_path):
    """A repo with one declared rule, committed on main."""
    root = tmp_path / "r"
    (root / ".warden" / "rules").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "repo.yaml").write_text(
        "version: 1\nrepo: sim\n"
        "components:\n  app: {path: app/, lang: python, description: d}\n"
        "review:\n  rules_dir: .warden/rules\n"
        "  blocking_severities: [HIGH]\n")
    (root / ".warden" / "rules" / "noisy.md").write_text(_rule("noisy"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _commit(root, message="change"):
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def _problems(root, paths):
    """No policy is passed: `carve_out_problems` reads blocking_severities and
    rules_dir from the COMMITTED repo.yaml, which is the source under test."""
    return autonomy_mod.carve_out_problems(root, paths, base="main")


def _pause_on_branch(root, rule_id="noisy", *, with_backtest=True):
    """What `warden autonomy pause` produces, on a branch off main."""
    _git(root, "checkout", "-q", "-b", "auto/run")
    path = root / ".warden" / "rules" / f"{rule_id}.md"
    path.write_text(autonomy_mod.with_pause_frontmatter(path.read_text(), REASON))
    paths = [f".warden/rules/{rule_id}.md"]
    if with_backtest:
        bt = root / ".warden" / "memory" / "backtests"
        bt.mkdir(parents=True, exist_ok=True)
        (bt / f"{rule_id}-pause-20260831.json").write_text(_backtest(rule_id))
        paths.append(f".warden/memory/backtests/{rule_id}-pause-20260831.json")
    _commit(root, "autonomy pause")
    return paths


def _run_shaped_branch(repo, *, sha=None, name=None, **over):
    """What a protocol-following unattended run commits, and the shape the
    real blocked run had: ordinary source changes in one commit, then the
    attestation shard for that commit in the next. The shard is always a
    commit BEHIND the head, because attesting HEAD and then committing the
    shard is what moves head.

    Returns the handed path list — the shard alone, which is all the runner's
    forbidden-path grep matches. `app/thing.py` is ordinary work the freeze
    never objected to and the carve-out is never asked about.
    """
    _git(repo, "checkout", "-q", "-b", "auto/run")
    (repo / "app").mkdir(exist_ok=True)
    (repo / "app" / "thing.py").write_text("def thing():\n    return 1\n")
    _commit(repo, "the session's work")
    work_sha = _git(repo, "rev-parse", "HEAD").strip()
    shard_dir = repo / ".warden" / "memory" / "attest"
    shard_dir.mkdir(parents=True, exist_ok=True)
    shard = name or f"20260921T041228Z-{work_sha[:8]}-2987f6a9.json"
    (shard_dir / shard).write_text(_attest_shard(sha or work_sha, **over))
    _commit(repo, "ops(memory): the round's attestation shard")
    return [f".warden/memory/attest/{shard}"]


# ---------- inside ----------------------------------------------------------

def test_a_pause_with_its_backtest_is_inside_the_carve_out(repo):
    paths = _pause_on_branch(repo)
    assert _problems(repo, paths) == []


def test_what_apply_pause_actually_writes_clears_the_carve_out(repo):
    """The drift guard for this whole file. Every other test builds the pause
    by hand, so all of them could agree with each other and disagree with the
    WRITER — and then the cage would auto-close the one diff the carve-out
    exists to let through. Here `apply_pause` produces the rule edit and the
    artifact, and the checker reads exactly that, field for field.
    """
    _git(repo, "checkout", "-q", "-b", "auto/run")
    action = autonomy_mod.PauseAction(
        rule_id="noisy", streak=3, judged=11, upheld=2, refuted=8,
        dismissed=1, rate=0.182, wilson_lb=0.052)
    rule_path, artifact = autonomy_mod.apply_pause(repo, action,
                                                   today="2026-08-31")
    _commit(repo, "warden autonomy pause")
    paths = [rule_path.relative_to(repo).as_posix(),
             artifact.relative_to(repo).as_posix()]
    assert _problems(repo, paths) == [], (
        "the carve-out refuses what `warden autonomy pause` writes — the "
        "reader and the writer have drifted apart")


def _adopt_on_branch(repo, rule_id="quiet", *, with_backtest=True, **over):
    """What `warden autonomy adopt` produces, on a branch off main."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    (repo / ".warden" / "rules" / f"{rule_id}.md").write_text(_rule(rule_id))
    paths = [f".warden/rules/{rule_id}.md"]
    if with_backtest:
        bt = repo / ".warden" / "memory" / "backtests"
        bt.mkdir(parents=True, exist_ok=True)
        (bt / f"{rule_id}-adopt-20260901.json").write_text(
            _adopt_backtest(rule_id, **over))
        paths.append(f".warden/memory/backtests/{rule_id}-adopt-20260901.json")
    _commit(repo, "autonomy adopt")
    return paths


def test_a_new_non_blocking_rule_is_inside_the_carve_out(repo):
    """With the artifact it carries. An adoption carrying nothing bumps
    rules_version with no backtest naming it — a tree that FAILS certification
    S-05. The carve-out and the writer agree on both halves."""
    assert _problems(repo, _adopt_on_branch(repo)) == []


def test_what_adopt_rule_actually_writes_clears_the_carve_out(repo):
    """The drift guard for the adoption arm, exactly as its pause sibling is
    for the pause arm: every other adoption test builds the artifact by hand,
    so all of them could agree with each other and disagree with the WRITER —
    and then the cage would auto-close the one diff the carve-out exists to
    let through."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    dest, artifact = autonomy_mod.adopt_rule(
        repo, _rule("quiet"), dest_name="quiet",
        blocking_severities=["HIGH"], today="2026-09-01")
    _commit(repo, "warden autonomy adopt")
    paths = [dest.relative_to(repo).as_posix(),
             artifact.relative_to(repo).as_posix()]
    assert _problems(repo, paths) == [], (
        "the carve-out refuses what `warden autonomy adopt` writes — the "
        "reader and the writer have drifted apart")


def test_an_adoption_without_its_backtest_is_refused(repo):
    """The ladder's own text is "the pause/adopt backtest artifact that change
    carries", and certification S-05 demands one for the rules_version the
    ADDITION bumps. An adoption with no artifact is a rule change with no
    evidence, and merging it drops a granted consumer a certification level."""
    problems = _problems(repo, _adopt_on_branch(repo, with_backtest=False))
    assert problems and "no VALID adoption backtest" in problems[0], problems


def test_an_adopt_artifact_for_a_rule_this_diff_did_not_add_is_refused(repo):
    """The BINDING is the point. Pass 2 is narrowed so a fabricated `promote`
    artifact cannot ride through the freeze beside a benign rule; an adopt
    artifact accepted on its own, or naming some other rule, would reopen
    exactly that hole under a new action name."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    (repo / ".warden" / "rules" / "quiet.md").write_text(_rule("quiet"))
    bt = repo / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "elsewhere-adopt-20260901.json").write_text(
        _adopt_backtest("elsewhere"))
    _commit(repo, "adopt one rule, claim another")
    problems = _problems(repo, [
        ".warden/rules/quiet.md",
        ".warden/memory/backtests/elsewhere-adopt-20260901.json"])
    assert problems and "this diff did not add" in problems[0], problems
    # and the adoption itself is still unevidenced — a rejected artifact must
    # not also satisfy the obligation it failed
    assert any("no VALID adoption backtest" in p for p in problems), problems


@pytest.mark.parametrize("over,expect", [
    ({"judged": 5}, "its 'judged' is 5"),
    ({"judged": True}, "its 'judged' is True"),
    ({"note": "  "}, "records no `note`"),
    ({"severity": "LOW"}, "declares severity 'LOW'"),
])
def test_a_stub_or_overclaiming_adopt_artifact_is_refused(repo, over,
                                                                expect):
    """An adoption may declare NOTHING it cannot derive. The rule has produced
    no findings, so a judged tally is fabricated evidence — the same shape
    pass 2 refuses under the `promote` name — and a severity
    disagreeing with the committed rule file is evidence for a change the tree
    does not contain."""
    problems = _problems(repo, _adopt_on_branch(repo, **over))
    assert problems, "a stub adopt artifact cleared the carve-out"
    assert any(expect in p for p in problems), problems
    assert any("no VALID adoption backtest" in p for p in problems), problems


# ---------- outside ---------------------------------------------------------

def test_a_pause_without_its_backtest_is_refused(repo):
    """The ladder's own text is "the pause/adopt backtest artifact that change
    carries", and certification S-05 demands the same. A pause
    with no recorded reason to believe it is a rule change with no evidence."""
    paths = _pause_on_branch(repo, with_backtest=False)
    problems = _problems(repo, paths)
    assert len(problems) == 1
    assert "no pause backtest" in problems[0], problems


def test_a_rule_edit_riding_along_with_the_pause_is_refused(repo):
    """The whole point of re-deriving rather than inspecting: the pause is
    there, and so is a severity change that would raise enforcement."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    path = repo / ".warden" / "rules" / "noisy.md"
    paused = autonomy_mod.with_pause_frontmatter(path.read_text(), REASON)
    path.write_text(paused.replace("severity: MEDIUM", "severity: HIGH"))
    _commit(repo, "pause plus a severity move")
    problems = _problems(repo, [".warden/rules/noisy.md"])
    assert problems and "changed more than the pause" in problems[0], problems


def test_a_body_edit_dressed_as_a_pause_is_refused(repo):
    _git(repo, "checkout", "-q", "-b", "auto/run")
    path = repo / ".warden" / "rules" / "noisy.md"
    paused = autonomy_mod.with_pause_frontmatter(path.read_text(), REASON)
    path.write_text(paused.replace("body\n", "a whole new charter\n"))
    _commit(repo, "pause plus a body rewrite")
    problems = _problems(repo, [".warden/rules/noisy.md"])
    assert problems and "changed more than the pause" in problems[0], problems


def test_a_rule_edit_that_is_not_a_pause_at_all_is_refused(repo):
    _git(repo, "checkout", "-q", "-b", "auto/run")
    path = repo / ".warden" / "rules" / "noisy.md"
    path.write_text(_rule("noisy").replace("body\n", "reworded\n"))
    _commit(repo, "reword")
    problems = _problems(repo, [".warden/rules/noisy.md"])
    assert problems and "without becoming paused" in problems[0], problems


def test_a_blocking_severity_adoption_is_refused(repo):
    """The same refusal `adopt_rule` makes, RE-DERIVED from the committed
    bytes: between the write and the PR a session could have edited the file,
    and the cage judges what got committed."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    (repo / ".warden" / "rules" / "loud.md").write_text(_rule("loud", "HIGH"))
    _commit(repo, "adopt")
    problems = _problems(repo, [".warden/rules/loud.md"])
    assert problems and "blocking_severities" in problems[0], problems


def test_an_added_rule_whose_id_does_not_match_its_file_is_refused(repo):
    _git(repo, "checkout", "-q", "-b", "auto/run")
    (repo / ".warden" / "rules" / "quiet.md").write_text(_rule("something-else"))
    _commit(repo, "adopt")
    problems = _problems(repo, [".warden/rules/quiet.md"])
    assert problems and "declares id" in problems[0], problems


def test_a_promote_artifact_beside_an_adoption_is_refused(repo):
    """Fail-closed: the accepted actions are the writer's, not
    certification's enum (promote/pause/retire/reword).

    The writer this check mirrors never emits `promote`. Accepting it would
    let a session ship a benign non-blocking rule PLUS a fabricated `promote`
    artifact — the record S-05 and the retro read as "this rule was measured" —
    straight through the gate-surface freeze.
    """
    _git(repo, "checkout", "-q", "-b", "auto/run")
    (repo / ".warden" / "rules" / "quiet.md").write_text(_rule("quiet"))
    bt = repo / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "quiet-promote-20260901.json").write_text(
        _backtest("quiet", action="promote", judged=50, wilson_lb=0.92))
    _commit(repo, "adopt plus a promotion claim")
    problems = _problems(repo, [
        ".warden/rules/quiet.md",
        ".warden/memory/backtests/quiet-promote-20260901.json"])
    assert problems and "action: pause" in problems[0], problems


@pytest.mark.parametrize("over,expect", [
    ({"judged": True}, "'judged' is True"),
    ({"streak": 1}, "streak is 1"),
    ({"note": "  "}, "records no `note`"),
])
def test_a_stub_pause_artifact_is_refused(repo, over, expect):
    """The ladder makes a pause conditional on a RECORDED REASON. A two-key
    file costs nothing to fabricate and would satisfy both this check and
    S-05, so the artifact must at least carry the counts
    the pause was derived from. Note what this does NOT do — see
    `carve_out_problems`' docstring: it does not re-derive the streak from the
    corpus, and it does not claim to."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    path = repo / ".warden" / "rules" / "noisy.md"
    path.write_text(autonomy_mod.with_pause_frontmatter(path.read_text(), REASON))
    bt = repo / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "noisy-pause-20260831.json").write_text(_backtest("noisy", **over))
    _commit(repo, "pause with a stub artifact")
    problems = _problems(repo, [
        ".warden/rules/noisy.md",
        ".warden/memory/backtests/noisy-pause-20260831.json"])
    assert problems, "a stub pause artifact cleared the carve-out"
    assert any(expect in p for p in problems), problems
    # and the rejected artifact does not also satisfy the pause's obligation
    assert any("no pause backtest" in p for p in problems), problems


def test_pausing_a_blocking_rule_is_refused(repo):
    """Deliberately NARROWER than the ladder text. A pause is machine-eligible
    because it "only ever makes the gate quieter", which `warden autonomy
    pause` earns with a streak check and the UNANSWERED-ceiling guard — neither
    of which this check can re-derive from a worktree whose corpus dir the
    session can write. Without them the reachable abuse is precise: pause the
    one BLOCKING rule that would have failed this very diff, and the gate runs
    quiet on it."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    loud = repo / ".warden" / "rules" / "loud.md"
    loud.write_text(_rule("loud", "HIGH"))
    _commit(repo, "a blocking rule exists")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "auto/run")
    _git(repo, "checkout", "-q", "-b", "auto/run2")
    loud.write_text(autonomy_mod.with_pause_frontmatter(loud.read_text(), REASON))
    bt = repo / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True, exist_ok=True)
    (bt / "loud-pause-20260831.json").write_text(_backtest("loud"))
    _commit(repo, "pause the blocking rule")
    problems = _problems(repo, [
        ".warden/rules/loud.md",
        ".warden/memory/backtests/loud-pause-20260831.json"])
    assert problems and "blocking_severities" in problems[0], problems


def test_a_backtest_on_its_own_is_refused(repo):
    """An artifact alone would widen `.warden/` writes for free — it is in the
    carve-out only as the artifact a rule change in the SAME diff carries."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    bt = repo / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "noisy-pause-20260831.json").write_text(_backtest("noisy"))
    _commit(repo, "artifact only")
    problems = _problems(repo, [".warden/memory/backtests/noisy-pause-20260831.json"])
    assert problems and "this diff did not pause" in problems[0], problems


def test_a_deleted_rule_is_refused(repo):
    _git(repo, "checkout", "-q", "-b", "auto/run")
    (repo / ".warden" / "rules" / "noisy.md").unlink()
    _commit(repo, "delete a rule")
    problems = _problems(repo, [".warden/rules/noisy.md"])
    assert problems and "never deletes" in problems[0], problems


@pytest.mark.parametrize("path,content", [
    (".warden/certification.yaml", "version: 1\ngrandfathered_rule_ids: [x]\n"),
    (".warden/skills-policy.md", "# rewritten\n"),
    (".warden/catalog-answers.yaml", "version: 1\n"),
])
def test_every_other_warden_write_is_refused(repo, path, content):
    """The carve-out is an enumeration of what is IN, so anything else is out
    by construction — including the files that would waive a check, rewrite the
    protocol, or answer the gap on the machine's own authority."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _commit(repo, "gate surface")
    problems = _problems(repo, [path])
    assert problems and "outside the carve-out" in problems[0], problems


def test_a_path_outside_the_diff_is_refused(repo):
    """The verdict is about the COMMITTED diff. A path the diff does not carry
    cannot be judged, and 'cannot judge' is a refusal."""
    _pause_on_branch(repo)
    problems = _problems(repo, [".warden/rules/ghost.md"])
    assert problems and "not changed in" in problems[0], problems


def test_an_empty_path_list_is_refused(repo):
    """Nothing to clear must not read as a clean bill: the caller asks this
    only when its own stop already fired."""
    _pause_on_branch(repo)
    problems = _problems(repo, ["", "   "])
    assert problems and "nothing to clear" in problems[0], problems


def test_one_bad_path_refuses_the_whole_set(repo):
    """The runner clears or does not clear the run; there is no partial pass,
    so a legitimate pause beside an illegitimate write must not launder it."""
    paths = _pause_on_branch(repo)
    (repo / ".warden" / "certification.yaml").write_text("version: 1\n")
    _commit(repo, "and a waiver")
    problems = _problems(repo, [*paths, ".warden/certification.yaml"])
    assert len(problems) == 1 and "certification.yaml" in problems[0], problems


def test_an_unreadable_committed_policy_refuses(repo):
    """Fail closed on the declaration itself: judging a rules write against a
    guessed policy is the unresolvable-declaration fail-open with an extra
    step. Broken on
    MAIN, because that is where the policy is read from."""
    (repo / "repo.yaml").write_text("version: 1\nreview: [not, a, mapping]\n")
    _commit(repo, "break the declaration")
    _pause_on_branch(repo)
    problems = _problems(repo, [".warden/rules/noisy.md"])
    assert problems and "declares no `review:` mapping" in problems[0], problems


def test_a_policy_with_no_blocking_severities_refuses(repo):
    """The machine-adoption refusal keys on `blocking_severities`. A bound
    that cannot be read is not a bound, so it refuses rather than defaulting
    to "nothing blocks" — which would clear every adoption at any severity."""
    (repo / "repo.yaml").write_text(
        "version: 1\nrepo: sim\nreview:\n  rules_dir: .warden/rules\n")
    _commit(repo, "drop the severities")
    _pause_on_branch(repo)
    problems = _problems(repo, [".warden/rules/noisy.md"])
    assert problems and "blocking_severities" in problems[0], problems


# ---------- the policy comes from the COMMITTED tree ------------------------

def test_an_uncommitted_repo_yaml_cannot_widen_the_carve_out(repo):
    """The policy inputs come from the committed tree, never the FILESYSTEM.

    `config.load()` / `declared_rules_dir` read `repo.yaml` off the
    FILESYSTEM. The runner calls this with
    cwd = the session-writable worktree and never cleans it after the session,
    and an UNCOMMITTED repo.yaml never appears in the committed diff — so it is
    never a forbidden path and the carve-out is never asked about it. A session
    could commit a blocking-severity rule, leave `blocking_severities: [LOW]`
    uncommitted beside it, and have the one bound the ladder rests on judged
    against a threshold it wrote itself.
    """
    _git(repo, "checkout", "-q", "-b", "auto/run")
    (repo / ".warden" / "rules" / "loud.md").write_text(_rule("loud", "HIGH"))
    _commit(repo, "adopt a blocking rule")
    # the lever: edited in the worktree, deliberately NOT committed
    (repo / "repo.yaml").write_text(
        (repo / "repo.yaml").read_text().replace(
            "blocking_severities: [HIGH]", "blocking_severities: [LOW]"))
    assert _git(repo, "status", "--porcelain").strip(), "fixture did not dirty"

    problems = _problems(repo, [".warden/rules/loud.md"])
    assert problems and "blocking_severities" in problems[0], (
        "an uncommitted repo.yaml moved the severity bound: " + str(problems))


def test_a_committed_repo_yaml_cannot_widen_the_carve_out_either(repo):
    """The committed variant of the same trick. Reading the policy at HEAD
    would close the uncommitted hole and leave this one: commit
    `blocking_severities: [LOW]` and a `severity: HIGH` rule in ONE commit, and
    the rule reads as non-blocking against the policy the session just wrote.
    On a real consumer that is held shut only by `repo\\.yaml$` being in
    forbidden_paths — by config, not by this checker. So the policy comes from
    the MERGE BASE, the last commit the session did not write."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    (repo / ".warden" / "rules" / "loud.md").write_text(_rule("loud", "HIGH"))
    (repo / "repo.yaml").write_text(
        (repo / "repo.yaml").read_text().replace(
            "blocking_severities: [HIGH]", "blocking_severities: [LOW]"))
    _commit(repo, "adopt a blocking rule and lower the bar in one commit")
    problems = _problems(repo, [".warden/rules/loud.md"])
    assert problems and "blocking_severities" in problems[0], (
        "a committed repo.yaml moved the severity bound: " + str(problems))


@pytest.mark.parametrize("field", ["covers", "implements"])
def test_an_adoption_that_raises_enforcement_is_refused(repo, field):
    """Severity is not the only way a rule raises enforcement. An UNPAUSED
    rule that `covers:` a slug
    makes `attest write` REFUSE `unmapped:<slug>` — enforcement raised at the
    write seam, at a severity that cannot fail a build. `implements:` credits a
    catalog entry as ENFORCED and removes it from the UNANSWERED count CI exits
    1 above. Both are decisions a person makes."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    value = "supply-chain-pinning" if field == "implements" else "docs-drift"
    (repo / ".warden" / "rules" / "quiet.md").write_text(
        _rule("quiet").replace("---\nbody\n", f'{field}: ["{value}"]\n---\nbody\n'))
    _commit(repo, "adopt a rule that answers a class")
    problems = _problems(repo, [".warden/rules/quiet.md"])
    assert problems and field in problems[0], problems


def test_a_negative_count_in_a_pause_artifact_is_refused(repo):
    """A count is a count. Left untested, `or value < 0` would be free to
    delete."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    path = repo / ".warden" / "rules" / "noisy.md"
    path.write_text(autonomy_mod.with_pause_frontmatter(path.read_text(), REASON))
    bt = repo / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True)
    (bt / "noisy-pause-20260831.json").write_text(_backtest("noisy", refuted=-1))
    _commit(repo, "pause with a negative count")
    problems = _problems(repo, [
        ".warden/rules/noisy.md",
        ".warden/memory/backtests/noisy-pause-20260831.json"])
    assert problems and "'refuted' is -1" in problems[0], problems


def test_a_rules_dir_that_escapes_the_repo_is_refused(repo):
    """Deleting the traversal guard in `_committed_policy` must not be
    silent — it is the only thing stopping a declared `rules_dir: ../../etc`
    from redefining what counts as a rule path."""
    (repo / "repo.yaml").write_text(
        (repo / "repo.yaml").read_text().replace(
            "rules_dir: .warden/rules", "rules_dir: ../../etc"))
    _commit(repo, "escape the repo")
    _pause_on_branch(repo)
    problems = _problems(repo, [".warden/rules/noisy.md"])
    assert problems and "inside the repo" in problems[0], problems


def test_an_uncommitted_rules_dir_repoint_cannot_widen_the_carve_out(repo):
    """The other half of the same lever: `rules_dir` decides which added .md
    files are judged as rule files at all."""
    paths = _pause_on_branch(repo)
    (repo / "repo.yaml").write_text(
        (repo / "repo.yaml").read_text().replace(
            "rules_dir: .warden/rules", "rules_dir: policy/rules"))
    # The pause is still judged against the COMMITTED rules_dir, so it still
    # clears — the point is that the uncommitted repoint changed nothing.
    assert _problems(repo, paths) == []


# ---------- the carve-out judges what it was handed and
# ---------- BINDS against what was committed
def _adopt_split(repo, *, hand):
    """An adoption committed WHOLE, with only `hand` handed to the check —
    the cage pipes it the forbidden-path grep matches, and a consumer's
    `forbidden_paths` regex and `review.rules_dir` are independent config."""
    paths = _adopt_on_branch(repo)
    return _problems(repo, [p for p in paths if hand in p])


def test_c2_an_adoption_whose_rule_file_is_not_a_forbidden_path_is_cleared(repo):
    """A split adoption where only the artifact is handed over still clears.

    `warden autonomy adopt` writes the rule to the DECLARED `review.rules_dir`
    and the artifact to `.warden/memory/backtests/`. A consumer whose
    forbidden-path regex covers the second and not the first hands this check
    only the artifact — and binding against the handed paths alone would
    refuse it as unbound, so the cage would auto-close the one PR shape the
    carve-out exists to let stand. On that consumer a plain rule write fires
    nothing, so such a refusal would be a regression caused purely by adopt
    writing an artifact.
    """
    assert _adopt_split(repo, hand="backtests") == []


def test_c2_an_adoption_whose_artifact_is_not_a_forbidden_path_is_cleared(repo):
    """The mirror case, and the one the pass-3 obligation must get right: only
    the rule file is handed over, and the artifact IS committed beside it. The
    obligation is satisfied from the committed diff, not from the handed
    half."""
    assert _adopt_split(repo, hand="rules/") == []


def _pause_split(repo, *, hand):
    """A pause committed WHOLE, with only `hand` handed to the check — the
    pause mirror of `_adopt_split`. `warden autonomy pause` writes the rule to
    the DECLARED `review.rules_dir` and the artifact to
    `.warden/memory/backtests/`, and a consumer's `forbidden_paths` regex
    covering one and not the other hands over half the change."""
    paths = _pause_on_branch(repo)
    return _problems(repo, [p for p in paths if hand in p])


def test_a_pause_whose_rule_file_is_not_a_forbidden_path_is_cleared(repo):
    """The split direction for pauses. Only the ARTIFACT is handed over; the
    pause edit IS committed beside it. Binding against the handed half alone
    would refuse the artifact as naming a rule "this diff did not pause" —
    auto-closing `warden autonomy pause`'s own output on exactly the
    declared-`rules_dir` consumer shape the carve-out must support. Identical
    in kind to the split adoption case, one arm over."""
    assert _pause_split(repo, hand="backtests") == []


def test_a_pause_whose_artifact_is_not_a_forbidden_path_is_cleared(repo):
    """The mirror case, and the one the pass-3 obligation must get right: only
    the RULE FILE is handed over, and the artifact IS committed beside it. The
    obligation is satisfied from the committed diff, not from the handed
    half."""
    assert _pause_split(repo, hand="rules/") == []


def test_a_stub_pause_artifact_in_the_diff_does_not_evidence_the_pause(repo):
    """Widening the evidence lookup to the committed diff must widen it to
    VALID artifacts only. A two-key stub committed beside the pause and never
    handed over must leave the pause unevidenced, exactly as a handed stub
    does — otherwise reading the committed diff would trade a false refusal
    for a false
    clearance, and `existence checked, validity not` is a named shape of this
    repo's fail-closed rule."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    path = repo / ".warden" / "rules" / "noisy.md"
    path.write_text(autonomy_mod.with_pause_frontmatter(path.read_text(), REASON))
    bt = repo / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True, exist_ok=True)
    (bt / "noisy-pause-20260831.json").write_text(
        json.dumps({"action": "pause", "rule_id": "noisy"}) + "\n")
    _commit(repo, "autonomy pause with a stub artifact")
    problems = _problems(repo, [".warden/rules/noisy.md"])
    assert any("no pause backtest" in p for p in problems), problems


def test_an_artifact_cannot_bind_to_a_rule_edit_that_is_not_a_pause(repo):
    """The binding side has to re-derive the pause, not just notice the rule
    file was modified. Dropping `_pause_edit_problem` from the committed
    pre-pass must redden this test, because the hole it leaves is the one the
    carve-out exists to shut — a BODY
    REWRITE committed to a rule file, handed over as nothing but a plausible
    `action: pause` artifact, would clear as machine-tier work and the cage
    would leave that PR open. `paused_in_commit` must mean "this diff paused
    it", never "this diff touched it"."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    path = repo / ".warden" / "rules" / "noisy.md"
    path.write_text(_rule("noisy").replace("body\n", "a rewritten body\n"))
    bt = repo / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True, exist_ok=True)
    (bt / "noisy-pause-20260831.json").write_text(_backtest("noisy"))
    _commit(repo, "a body rewrite dressed as a pause")
    problems = _problems(
        repo, [".warden/memory/backtests/noisy-pause-20260831.json"])
    assert any("did not pause" in p for p in problems), problems


def test_the_committed_pause_is_re_derived_from_the_merge_base(repo):
    """Main advancing under a running session is the NORMAL case in the
    unattended loop, not the exotic one. The committed pre-pass re-derives the
    pause from the MERGE BASE, never from the tip of `base`: someone editing
    the very rule the session paused makes the tip a pair the session never
    saw, the re-derivation fails against bytes it did not start from, and a
    legitimate autonomy PR is auto-closed. The adopt arm has the same
    shape."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    path = repo / ".warden" / "rules" / "noisy.md"
    path.write_text(autonomy_mod.with_pause_frontmatter(path.read_text(), REASON))
    bt = repo / ".warden" / "memory" / "backtests"
    bt.mkdir(parents=True, exist_ok=True)
    (bt / "noisy-pause-20260831.json").write_text(_backtest("noisy"))
    _commit(repo, "autonomy pause")
    # main moves on, touching the same rule file the session paused
    _git(repo, "checkout", "-q", "main")
    path.write_text(_rule("noisy").replace("body\n", "an edited body\n"))
    _commit(repo, "someone edits the rule main-side")
    _git(repo, "checkout", "-q", "auto/run")
    assert _problems(
        repo, [".warden/memory/backtests/noisy-pause-20260831.json"]) == []


def test_c3_a_rejected_artifact_does_not_read_as_an_absent_one(repo):
    """error-names-cause: when the artifact is committed but not ACCEPTED,
    pass 3 says the diff carries no VALID one and points at the reason above
    it. Saying it was not "added ... in the same diff" would send the reader
    to write a file already committed beside the rule."""
    problems = _problems(repo, _adopt_on_branch(repo, judged=7))
    assert any("its 'judged' is 7" in p for p in problems), problems
    unevidenced = [p for p in problems if "adoption backtest" in p]
    assert unevidenced, problems
    assert "no VALID adoption backtest" in unevidenced[0], unevidenced
    assert "reported above" in unevidenced[0], (
        "the refusal does not point the reader at why the artifact it CAN see "
        "was not accepted")


@pytest.mark.parametrize("field", ["upheld", "refuted", "dismissed", "rate",
                                   "wilson_lb"])
def test_c4_a_fabricated_precision_tally_beside_judged_zero_is_refused(repo,
                                                                       field):
    """A fabricated precision tally on an adoption is refused.

    `judged: 0` with `upheld: 41, rate: 1.0` and a note claiming "measured
    over the whole corpus" must not clear the carve-out — it is the
    fabricated-measurement shape pass 2 refuses under the `promote` name,
    wearing the `adopt` label. The writer omits these fields entirely."""
    problems = _problems(repo, _adopt_on_branch(repo, **{field: 41}))
    assert problems, f"an adopt artifact declaring {field!r} cleared the check"
    assert any(f"declares {field!r}" in p for p in problems), problems


def test_c5_an_adopt_artifact_naming_no_ruleset_is_refused(repo):
    """The pass-3 refusal justifies itself by S-05 freshness, so the check
    reads `rules_version`: an artifact with the key deleted must not clear the
    carve-out while the tree it produces still fails the rung. What this CAN
    check is shape: an artifact naming no ruleset is not the record the writer
    emits. What it cannot check is whether the version named is the CURRENT
    one; the message says so."""
    paths = _adopt_on_branch(repo, rules_version=None)
    bt = repo / ".warden" / "memory" / "backtests" / "quiet-adopt-20260901.json"
    doc = json.loads(bt.read_text())
    del doc["rules_version"]
    bt.write_text(json.dumps(doc, indent=2) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--amend", "--no-edit")
    problems = _problems(repo, paths)
    assert problems and any("names no `rules_version`" in p for p in problems), \
        problems


def test_c6_an_adoption_of_a_bom_prefixed_rule_clears_the_carve_out(repo):
    """A BOM-prefixed rule binds the same way it loads.

    `rules._parse` reads with `utf-8-sig`, so a BOM-prefixed rule loads and
    `warden autonomy adopt` accepts and writes it. A SECOND parser for the
    severity binding that requires the first line to be exactly `---` would
    get None and refuse — auto-closing the tree the writer had just produced.
    One parser answers both."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    dest, artifact = autonomy_mod.adopt_rule(
        repo, "\ufeff" + _rule("quiet"), dest_name="quiet",
        blocking_severities=["HIGH"], today="2026-09-01")
    _commit(repo, "adopt a BOM-prefixed rule")
    assert _problems(repo, [dest.relative_to(repo).as_posix(),
                            artifact.relative_to(repo).as_posix()]) == [], (
        "the carve-out refuses what `warden autonomy adopt` wrote from a "
        "BOM-prefixed source")


# ---------- the attestation shard --------------------------------------------
#
# THE VICE THIS CLOSES, both halves proven live on 2026-09-20. `warden gate`
# refuses a PR carrying no committed attestation shard (PR #262 failed exactly
# that way). The carve-out refused ANY write under `.warden/` beyond a pause,
# a new non-blocking rule or a backtest. Shards land in
# `.warden/memory/attest/`, so a run that followed the review protocol
# committed two files it was forbidden to write and the cage closed its own
# PR; a run that skipped the attestation produced a PR the gate refused. There
# was no path between them.
#
# The founder's ruling: a shard is EVIDENCE that review happened, not a rule
# that changes what passes, so it is in. What it is NOT is proof the review
# was real — `verify_roster`'s own docstring says a caller writing plausible
# files passes it — and admitting the path does not widen that, because
# `cage/run.sh` already ingested the session's own `.warden/out/` artifact and
# committed the shard on its behalf. Same data, different committer.


def test_a_run_shaped_branch_carrying_its_attestation_shard_is_cleared(repo):
    """THE named regression test. A run-shaped branch — ordinary source
    changes plus the attestation shard for them — is NOT refused as a
    gate-surface edit. Both mutation arms are below, and they are the other
    half of the proof: the same branch with a `.warden/rules/` edit or a
    `repo.yaml` edit still IS refused."""
    paths = _run_shaped_branch(repo)
    assert _problems(repo, paths) == []


def test_a_rules_edit_riding_on_the_run_shaped_branch_is_still_refused(repo):
    """Mutation arm one. The shard clears; a rule-body edit beside it does
    not, and because the runner clears or does not clear the whole run, the
    legitimate shard cannot launder it."""
    paths = _run_shaped_branch(repo)
    rule = repo / ".warden" / "rules" / "noisy.md"
    rule.write_text(rule.read_text() + "\nand a new line the gate reads\n")
    _commit(repo, "and a rule edit")
    problems = _problems(repo, [*paths, ".warden/rules/noisy.md"])
    assert len(problems) == 1 and "noisy.md" in problems[0], problems


def test_a_repo_yaml_edit_on_the_run_shaped_branch_is_still_refused(repo):
    """Mutation arm two. `repo.yaml` IS the policy; nothing about admitting
    evidence admits the declaration the evidence is judged against."""
    paths = _run_shaped_branch(repo)
    (repo / "repo.yaml").write_text(
        (repo / "repo.yaml").read_text().replace("[HIGH]", "[LOW]"))
    _commit(repo, "and a policy edit")
    problems = _problems(repo, [*paths, "repo.yaml"])
    assert len(problems) == 1, problems
    assert "repo.yaml" in problems[0] and "outside the carve-out" in problems[0]


def test_rewriting_a_shard_already_on_main_is_refused(repo):
    """Only a NEW shard. The corpus is append-only evidence, so rewriting one
    that is ALREADY COMMITTED rewrites the record of a review that already
    happened — a different act from leaving a new one, and the one an
    unattended session must never be able to perform. A branch that changes
    an earlier verdict to `clean`, or an earlier finding's disposition, would
    be editing history the gate and the retro both read.

    The state is read from the MERGE BASE, so `A` here means "not on main",
    which is the question that matters. A shard a branch adds and then amends
    before pushing still reads `A` and is judged on its final bytes; what is
    refused is touching one the base already carries.
    """
    shard_dir = repo / ".warden" / "memory" / "attest"
    shard_dir.mkdir(parents=True)
    name = "20260901T000000Z-abcdef12-deadbeef.json"
    (shard_dir / name).write_text(_attest_shard("0" * 40,
                                                verdict="findings-open"))
    _commit(repo, "an earlier round's shard, on main")
    paths = _run_shaped_branch(repo)
    (shard_dir / name).write_text(_attest_shard("0" * 40, verdict="clean"))
    _commit(repo, "rewrite the earlier verdict")
    problems = _problems(repo, [*paths, f".warden/memory/attest/{name}"])
    assert len(problems) == 1, problems
    assert "only a NEW attestation shard" in problems[0], problems


def test_a_shard_attesting_a_commit_outside_the_diff_is_refused(repo):
    """The binding that makes a shard THIS branch's review record. A shard
    naming a commit the branch never produced was imported, not earned —
    the attest analogue of a backtest bound to no rule change in the diff."""
    base = _git(repo, "rev-parse", "main").strip()
    paths = _run_shaped_branch(repo, sha=base)
    problems = _problems(repo, paths)
    assert problems and "not a commit in this diff" in problems[0], problems


@pytest.mark.parametrize("over,expect", [
    ({"roster_verification": "unverified-roster"}, "roster_verification"),
    ({"roster_verification": None}, "roster_verification"),
    ({"round_binding": "unminted"}, "round_binding"),
    ({"round_binding": None}, "round_binding"),
])
def test_an_unchecked_roster_or_unminted_round_is_refused(repo, over,
                                                               expect):
    """The two narrowings past what `warden attest write` itself permits.
    Both states are LEGAL shards for a human running warden by hand — the
    absent-review-dir marker and the unminted-round marker are recorded, not
    refused, there. They are bare assertions, and a bare assertion is not what
    gets past a hard stop unattended."""
    paths = _run_shaped_branch(repo, **over)
    problems = _problems(repo, paths)
    assert problems and expect in problems[0], problems


def test_an_open_verdict_shard_is_cleared(repo):
    """NOT a narrowing: round 1's `findings-open` shard is committed BY
    DESIGN — the protocol is one shard per round, the last of them clean —
    and `attest.check_range` owns the tip-clean rule over the whole range.
    Refusing it here would auto-close the honest multi-round branch the
    review protocol produces, which is the bug this bead exists for wearing
    a different hat."""
    paths = _run_shaped_branch(repo, verdict="findings-open")
    assert _problems(repo, paths) == []


def test_a_gate_shard_smuggled_into_the_attest_dir_is_refused(repo):
    """`source: gate` records a deterministic checker firing, including on a
    mutation proof the DoD tells you to run and then revert. Those are
    gitignored on purpose so a stray `git add -A` cannot file
    synthetic defect records into the shared corpus; the carve-out must not
    be the way back in."""
    paths = _run_shaped_branch(repo, source="gate")
    problems = _problems(repo, paths)
    assert problems and "not an attestation" in problems[0], problems


@pytest.mark.parametrize("raw,expect", [
    ("{not json", "not readable as an attestation shard"),
    ('[{"sha": "x"}]', "not an evidence shard"),
    ('{"source": "attest", "sha": "x"}', "records"),
    ('{"source": "attest", "sha": "x", "records": [], "verdict": ["c"]}',
     "verdict"),
])
def test_a_shard_no_corpus_reader_could_read_is_refused(repo, raw, expect):
    """The ENVELOPE is checked with `memory.validate_shard_envelope`, the
    same definition `memory.read_shard` and `attest.attested_shas` call — so
    a file this arm clears is one the corpus can actually read. A private
    shape check here would drift into admitting a file the readers choke on,
    and the choke would land on whoever ran `warden memory ingest` next."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    shard_dir = repo / ".warden" / "memory" / "attest"
    shard_dir.mkdir(parents=True)
    (shard_dir / "20260921T041228Z-deadbeef-2987f6a9.json").write_text(raw)
    _commit(repo, "a shard nothing can read")
    problems = _problems(
        repo, [".warden/memory/attest/20260921T041228Z-deadbeef-2987f6a9.json"])
    assert problems and expect in problems[0], problems


def test_a_nested_path_under_the_attest_dir_is_refused(repo):
    """`warden memory ingest` writes flat `<ts>-<sha8>-<digest>.json` names.
    A path shaped unlike the writer's output is not the writer's output, and
    the carve-out is an enumeration of shapes, never of directories."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    nested = repo / ".warden" / "memory" / "attest" / "sub"
    nested.mkdir(parents=True)
    (nested / "shard.json").write_text(_attest_shard("0" * 40))
    _commit(repo, "a nested shard")
    problems = _problems(repo, [".warden/memory/attest/sub/shard.json"])
    assert problems and "flat `.json` shards" in problems[0], problems


def test_the_decide_store_is_not_admitted(repo):
    """By DECISION, not omission. A decision shard records what was RULED —
    policy `.warden/skills-policy.md` and the skills refer back to — where an
    attestation records that a process ran. And no run is BLOCKED for want of
    a decision shard, so admitting it would widen the fence with no vice to
    close."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    decide = repo / ".warden" / "memory" / "decide"
    decide.mkdir(parents=True)
    (decide / "20260921-ruling.json").write_text('{"records": []}\n')
    _commit(repo, "a ruling")
    problems = _problems(repo, [".warden/memory/decide/20260921-ruling.json"])
    assert problems and "outside the carve-out" in problems[0], problems


def _record(rule_id: str = "noisy", **over) -> dict:
    """One committed record, in the fields the carve-out's rule_id check
    reads. `ts`, `rule_id` and `status` are what RECORD_FIELD_CONTRACT
    requires present; `file` is optional there and required by the
    attestation schema, which is the asymmetry the check keys on."""
    doc = {"ts": "2026-09-21T04:12:28+00:00", "rule_id": rule_id,
           "file": "warden/autonomy.py", "line": 1, "severity": "MEDIUM",
           "finding": "f", "evidence": "e", "status": "confirmed"}
    doc.update(over)
    return doc


# ---------- the ingest refusal a hand-written shard would skip ---------------
#
# "Same data, trusted code" was the whole safety argument for the attestation
# arm, and the first version of it did not hold: `memory ingest` REFUSES an
# artifact whose findings name a rule the declared ruleset does not carry
# (`attest._check_rule_ids`), and `memory.validate_shard_envelope` checks
# field TYPES only. So a session could hand-write a shard it never ingested,
# and its records — which `records_from_shards` -> `stats` ->
# `pause_candidates` and certification E-02 all read — would land in the
# committed corpus unresolvable.


def test_r1f2_a_shard_naming_no_declared_rule_is_refused(repo):
    """The named regression test for round 1's `enforcement-truth` finding.
    A shard whose record names a rule id in no ruleset is exactly what
    `warden memory ingest` refuses, so a shard carrying one was not
    ingest-derived and does not clear the freeze."""
    paths = _run_shaped_branch(repo, records=[_record("not-a-declared-rule")])
    problems = _problems(repo, paths)
    assert problems, problems
    assert "would refuse" in problems[0] and "not-a-declared-rule" in problems[0]


def test_r1f2_a_shard_whose_records_resolve_is_cleared(repo):
    """The other arm, so the check above is not passing by refusing
    everything: the fixture's declared rule and a well-formed `unmapped:`
    slug both resolve, and a shard carrying them clears."""
    paths = _run_shaped_branch(repo, records=[
        _record("noisy"), _record("unmapped:carve-out-drift")])
    assert _problems(repo, paths) == []


def test_r1f2_a_bare_unmapped_slug_is_refused(repo):
    """`_check_rule_ids` is THE definition, not a re-typed subset of it: the
    non-descriptive-slug refusal it owns fires here too, unchanged. A second
    implementation would have had to re-derive this and would have drifted."""
    paths = _run_shaped_branch(repo, records=[_record("unmapped:general")])
    problems = _problems(repo, paths)
    assert problems and "names no defect class" in problems[0], problems


def test_r1f2_the_ruleset_is_read_at_the_merge_base(repo):
    """The session must not supply the declaration its own shard is judged
    against. A rule file committed on the BRANCH beside the shard does not
    make the id resolve — the same bound `_committed_policy` keeps one level
    up, and the reachable case is a consumer whose `forbidden_paths` regex
    does not cover its own rules dir, so the rule file is never handed over
    as a forbidden path at all."""
    _git(repo, "checkout", "-q", "-b", "auto/run")
    (repo / ".warden" / "rules" / "invented.md").write_text(_rule("invented"))
    _commit(repo, "a rule the session wrote for itself")
    (repo / "app").mkdir(exist_ok=True)
    (repo / "app" / "thing.py").write_text("x = 1\n")
    _commit(repo, "the session's work")
    work_sha = _git(repo, "rev-parse", "HEAD").strip()
    shard_dir = repo / ".warden" / "memory" / "attest"
    shard_dir.mkdir(parents=True, exist_ok=True)
    shard = f"20260921T041228Z-{work_sha[:8]}-2987f6a9.json"
    (shard_dir / shard).write_text(
        _attest_shard(work_sha, records=[_record("invented")]))
    _commit(repo, "the shard")
    # ONLY the shard is handed over, exactly as a consumer regex that covers
    # .warden/memory/ and not .warden/rules/ would hand it over.
    problems = _problems(repo, [f".warden/memory/attest/{shard}"])
    assert problems and "would refuse" in problems[0], problems


def test_r1f2_a_record_with_no_file_is_refused(repo):
    """`file` is optional in RECORD_FIELD_CONTRACT and REQUIRED on every
    finding by the attestation schema, so a record without one did not come
    from an attestation. Named rather than left to raise KeyError three
    frames down inside the shared checker."""
    paths = _run_shaped_branch(repo, records=[_record(file="")])
    problems = _problems(repo, paths)
    assert problems and "names no `file`" in problems[0], problems


def test_r1f2_an_unreadable_merge_base_ruleset_refuses(repo):
    """Fail closed on the ruleset itself. A shard cannot be judged against a
    declaration that could not be read, and `_problems` returning [] there
    would clear the freeze on the strength of a check that never ran."""
    import warden.autonomy as mod
    paths = _run_shaped_branch(repo, records=[_record("noisy")])
    real = mod._materialize_committed_ruleset
    try:
        mod._materialize_committed_ruleset = (
            lambda *a, **k: "the ruleset could not be listed (simulated)")
        problems = _problems(repo, paths)
    finally:
        mod._materialize_committed_ruleset = real
    assert problems and "simulated" in problems[0], problems


# ---------- the findings-free shard ------------------------------------------
#
# Round 1 closed `_check_rule_ids` and then stated the residue as "of the
# three ingest-seam invariants, only `_check_rule_ids` is a REFUSAL". There
# is a FOURTH, and it governs exactly the half `_check_rule_ids` returns
# early on: `memory._event_defects`, which `memory ingest` runs fail-closed
# on the FINDINGS-FREE path. Its own comment says why — with no findings,
# `_check_rule_ids` returns immediately, "so without this nothing between a
# hand-written artifact and committed evidence would look at the verdict at
# all".
#
# The consequence was not cosmetic. `attest.check_range` reads a shard with
# no verdict as "unrecorded ... does not block", so a hand-written six-field
# `records: []` file cleared the forbidden-path stop AND satisfied the
# attestation gate — on a path `warden memory ingest` would have refused to
# mint.


@pytest.mark.parametrize("missing", ["base_sha", "reviewed_at", "reviewers"])
def test_r2f1_a_records_free_shard_missing_an_event_field_is_refused(
        repo, missing):
    """The named regression test for round 2's HIGH. A shard with no records
    is the WHOLE evidence `attest check` reads, so one that cannot say what
    was reviewed, against what, when and by whom is an empty file with a
    shard's name on it."""
    over = {"records": [], missing: None}
    paths = _run_shaped_branch(repo, **over)
    problems = _problems(repo, paths)
    assert problems, problems
    assert "not a review EVENT" in problems[0] and missing in problems[0]


def test_r2f1_a_shard_with_no_verdict_is_refused(repo):
    """The sharp end of it: `check_range` treats a verdict-less shard as
    unrecorded and does NOT block, so this is the one missing field that
    clears the gate as well as the freeze."""
    paths = _run_shaped_branch(repo, records=[], verdict=None)
    problems = _problems(repo, paths)
    assert problems and "not a review EVENT" in problems[0], problems
    assert "verdict" in problems[0]


def test_r2f1_a_verdict_outside_the_vocabulary_is_refused(repo):
    """A DIFFERENT problem from an absent verdict and refused with a
    different message, exactly as `memory._event_defects` distinguishes them:
    telling someone who wrote `verdict: "CLEAN"` that they supplied no
    verdict sends them to supply what they already supplied. The vocabulary
    is IMPORTED from `attest.VERDICTS` (which reads the schema), so a verdict
    added there is legal here the same day."""
    paths = _run_shaped_branch(repo, records=[], verdict="CLEAN")
    problems = _problems(repo, paths)
    assert problems and "is not one of" in problems[0], problems


def test_r2f1_the_event_fields_are_required_of_a_records_bearing_shard(
        repo):
    """STRICTER than ingest, deliberately. There a records-bearing artifact is
    exempt because its records carry the provenance; here a shard that cannot
    say what it reviewed is not evidence whichever half it falls in, and the
    exemption would be a second shape to keep in step."""
    paths = _run_shaped_branch(repo, records=[_record("noisy")],
                               reviewers=None)
    problems = _problems(repo, paths)
    assert problems and "not a review EVENT" in problems[0], problems


def test_r2f1_a_records_free_shard_that_is_a_real_event_is_cleared(repo):
    """The other arm: a clean round's shard is committed BY DESIGN and must
    still clear. `tests/test_memory_clean_review.py` goes red on anyone who
    optimises the empty shard away, and this is its carve-out counterpart."""
    paths = _run_shaped_branch(repo, records=[])
    assert _problems(repo, paths) == []


def test_r2f7_an_unreadable_merge_base_ruleset_names_its_cause(repo):
    """`_check_rule_ids` raises `rules.RuleError` — NOT `AttestError` — when
    the materialized ruleset holds no `*.md`, which is what a merge base with
    an empty or absent rules dir produces. Uncaught it escaped to the CLI's
    blanket handler, and the only thing the reader was handed was a temp dir
    that never existed in their repo and was already deleted."""
    _git(repo, "rm", "-q", ".warden/rules/noisy.md")
    _commit(repo, "a base with no declared rules")
    paths = _run_shaped_branch(repo, records=[_record("noisy")])
    problems = _problems(repo, paths)
    assert problems, problems
    assert "could not be read" in problems[0], problems
    assert "merge base" in problems[0], problems
    assert "/var/folders" not in problems[0] and "warden-carve-out" not in \
        problems[0], "the deleted temp dir is named as the cause"


# ---------- re-formed round 1, finding 1: ingest's redaction --------------
#
# The arm's safety argument is that admitting the path only moves WHICH
# process commits bytes the session already authored. `memory ingest` does
# not only REFUSE on the way in — it also SCRUBS, over the whole shard
# envelope (`_redact_value`) and over each record's finding/evidence/reason
# (`_redact`), because "the corpus must never carry a live credential no
# matter which field carried it in". A session hand-writing a shard bypasses
# both, so the bytes this arm cleared were not the bytes the runner's
# publication step would have committed — the one place the equivalence had
# to hold and did not.

SECRET = "AKIAIOSFODNN7EXAMPLE"          # secret-SHAPED, not a live key


def test_reformed_r1f1_a_secret_in_the_envelope_is_refused(repo):
    """The named regression test. A credential planted anywhere in the
    envelope — here `rules_version`, which is not a field anyone reads for
    prose — is scrubbed by ingest and must not reach the corpus through the
    carve-out instead."""
    from warden import memory as memory_mod
    assert memory_mod._has_secret_shape(SECRET), "the fixture is not secret-shaped"
    paths = _run_shaped_branch(repo, rules_version=SECRET)
    problems = _problems(repo, paths)
    assert problems, problems
    assert "secret-shaped" in problems[0] and "rules_version" in problems[0]
    assert SECRET not in problems[0], "the refusal echoes the credential"


def test_reformed_r1f1_a_secret_in_a_records_evidence_is_refused(repo):
    """Where a quoted credential actually lands: a finding's own text.
    `_redact` is the record-side scrubber and keys on the rule_id, so this is
    a different call from the envelope's and needs its own arm."""
    paths = _run_shaped_branch(
        repo, records=[_record("noisy", evidence=f"the line reads {SECRET}")])
    problems = _problems(repo, paths)
    assert problems, problems
    assert "secret-shaped" in problems[0] and "evidence" in problems[0]
    assert SECRET not in problems[0], "the refusal echoes the credential"


def test_reformed_r1f1_a_secret_named_rule_redacts_wholesale(repo):
    """`_redact` redacts a `secret`-named rule's text WHOLESALE, secret-shaped
    or not — so re-running it is not the same as asking `_has_secret_shape`,
    and a re-implementation here would have missed this. The rule id resolves
    at the merge base, so the refusal is the redaction one and not the
    rule_id one."""
    (repo / ".warden" / "rules" / "secrets-in-diff.md").write_text(
        _rule("secrets-in-diff"))
    _commit(repo, "a secret-scanning rule on main")
    paths = _run_shaped_branch(repo, records=[
        _record("secrets-in-diff", evidence="an ordinary sentence")])
    problems = _problems(repo, paths)
    assert problems, problems
    assert "WHOLESALE" in problems[0] and "secrets-in-diff" in problems[0]


def test_the_refusal_names_the_rule_id_not_a_secret_that_is_not_there(repo):
    """Held to `error-names-cause`. The record-side refusal
    used to say "carries a secret-shaped string in `evidence`" for BOTH of
    `_redact`'s branches. Under a `secret`-named rule the text needs no
    credential in it at all, so that message sent the writer hunting one. The
    rule id is the actionable cause and the message must say so — and must not
    describe the record as carrying a secret."""
    (repo / ".warden" / "rules" / "secrets-in-diff.md").write_text(
        _rule("secrets-in-diff"))
    _commit(repo, "a secret-scanning rule on main")
    clean = "an ordinary sentence with nothing secret in it"
    from warden import memory as memory_mod
    assert not memory_mod._has_secret_shape(clean), "the fixture is secret-shaped"
    paths = _run_shaped_branch(repo, records=[
        _record("secrets-in-diff", evidence=clean)])
    problems = _problems(repo, paths)
    assert problems, problems
    assert "`secrets-in-diff`" in problems[0], problems[0]
    assert "carries a secret-shaped string" not in problems[0], problems[0]
    assert "no credential here to hunt" in problems[0], problems[0]


def test_a_real_secret_still_gets_the_secret_shaped_message(repo):
    """The other side of the split, so the fix is a DISCRIMINATION and not a
    rename: under a rule whose id says nothing about secrets, a secret-shaped
    `evidence` still gets the message that names the shape and the field."""
    paths = _run_shaped_branch(
        repo, records=[_record("noisy", evidence=f"the line reads {SECRET}")])
    problems = _problems(repo, paths)
    assert problems, problems
    assert "carries a secret-shaped string in `evidence`" in problems[0]
    assert "WHOLESALE" not in problems[0], problems[0]
    assert SECRET not in problems[0], "the refusal echoes the credential"


def test_a_secret_under_a_secret_named_rule_names_the_secret(repo):
    """Round 1's `error-names-cause` finding, named. A record BOTH filed under
    a `secret`-named rule AND carrying a credential — in `evidence`, while
    `finding` is clean prose.

    `_redact` rewrites EVERY field of such a record, so deciding the two
    reasons field by field answered "the rule id is the cause ... there is no
    credential here to hunt" off `finding` and returned before it ever reached
    the credential in `evidence`. The shape question is asked of the whole
    record first, so this shape gets the SECRET-SHAPED message. The earlier
    version of this test asserted only that the shard was refused and that the
    credential was not echoed — both of which held while the message was
    exactly backwards, which is why it is asserted on here.
    """
    (repo / ".warden" / "rules" / "secrets-in-diff.md").write_text(
        _rule("secrets-in-diff"))
    _commit(repo, "a secret-scanning rule on main")
    paths = _run_shaped_branch(repo, records=[
        _record("secrets-in-diff", finding="an ordinary sentence",
                evidence=f"the line reads {SECRET}")])
    problems = _problems(repo, paths)
    assert problems, problems
    assert "carries a secret-shaped string in `evidence`" in problems[0], problems[0]
    assert "no credential here to hunt" not in problems[0], problems[0]
    assert SECRET not in problems[0], "the refusal echoes the credential"


def test_the_record_half_reads_exactly_three_fields(repo):
    """Held to `enforcement-truth`. The residue paragraph in
    `_attest_shard_problem` used to say a fabricated record could get past
    "with no secret in it", which reads as though the record half covered the
    whole record. It does not: `_redaction_problem` re-runs `_redact` over
    `finding`, `evidence` and `reason` only, so a credential in `tags` is
    ADMITTED — at parity with `memory ingest`, whose `build_records` copies
    `tags` unscrubbed too.

    This pins the residue as it is, so that a future widening reddens here and
    the docstring is corrected with it rather than drifting behind it. If this
    ever goes red because the check grew wider, that is good news and the
    paragraph naming the three fields is what needs the edit.
    """
    paths = _run_shaped_branch(
        repo, records=[_record("noisy", tags=["lens", SECRET])])
    assert _problems(repo, paths) == [], (
        "the record half now reads a field outside finding/evidence/reason — "
        "widen `_attest_shard_problem`'s residue paragraph to match")


def test_reformed_r1f1_an_ordinary_shard_is_a_fixed_point(repo):
    """The other arm, and the property the whole check rests on: ingest's
    scrubbers are IDEMPOTENT on real values, so a shard that came through
    ingest is unchanged by them. If this went red the check would be refusing
    every legitimate run instead of the planted one."""
    paths = _run_shaped_branch(repo, records=[
        _record("noisy", evidence="def thing():\n    return 1")])
    assert _problems(repo, paths) == []
