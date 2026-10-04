"""`warden ship` — one named test per failure shape of the hand-run tail.

The four ship-tail failures the command refuses, each pinned here:

- a unit reporting gate parity green while its verify artifact names a
  commit that is not the shipped head
  (`test_a_verify_artifact_for_an_earlier_commit_is_refused`, and the
  dirty/unrecorded-tree siblings beside it, which are the same claim about a
  result that did not judge HEAD alone);
- a PR title that fails commit-lint AFTER the PR is open, by listing every
  bead id past the 100-char budget
  (`test_a_title_over_the_budget_is_refused_before_the_pr_exists`, driven
  through the REAL validator);
- finished work that was never pushed, its only copy local
  (`test_a_branch_the_remote_does_not_carry_is_refused`);
- a findings-open tip attestation shipping as a reviewed branch
  (`test_a_findings_open_tip_attestation_is_refused`).

Plus the two boundaries that are not failures but contracts:
`test_ship_builds_no_merge_argv_and_names_no_merge_literal` (merge
authority is the founder's) and
`test_the_required_scopes_come_from_the_shipped_reach_derivation` (one
spelling of the reach rule, never two).
"""

import ast
import copy
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from warden import attest as attest_mod
from warden import audit as audit_mod
from warden import config as config_mod
from warden import ship as ship_mod
from warden import verify as verify_mod

from conftest import REPO_ROOT, REPO_YAML


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                          *args], cwd=root, capture_output=True, text=True,
                         check=True)
    return out.stdout.strip()


@pytest.fixture
def shipping_repo(tmp_path: Path) -> Path:
    """A real git repo on a feature branch with one changed file.

    Real git, because `required_scopes` reads the range through
    `diffs.changed_files` and the whole point of these tests is that the
    derivation is the shipped one rather than a stub.
    """
    root = tmp_path / "unit"
    root.mkdir()
    (root / "repo.yaml").write_text(REPO_YAML)
    rules = root / ".warden" / "rules"
    rules.mkdir(parents=True)
    (rules / "tests-required.md").write_text(
        '---\nid: tests-required\nseverity: MEDIUM\nengine: claude\n'
        'applies_to: ["app/**"]\n---\nfixture rule\n')
    (root / "app").mkdir()
    (root / "app" / "thing.py").write_text("VALUE = 1\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "feat/x")
    (root / "app" / "thing.py").write_text("VALUE = 2\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "change")
    return root


def _write_verify(root: Path, *, scope: str, head: str, passed: bool = True,
                  dirty: bool | None = False, stamp: str = "") -> Path:
    """A verify artifact on disk, exactly as `warden verify` writes one."""
    run_dir = root / ".warden" / "out" / f"{stamp or scope}-verify"
    run_dir.mkdir(parents=True, exist_ok=True)
    doc = {"scope": scope, "passed": passed, "head_sha": head,
           "results": [{"cmd": "true", "cwd": ".", "exit_code": 0,
                        "duration_s": 0.0, "output_tail": ""}]}
    if dirty is not None:
        doc["dirty"] = dirty
    (run_dir / "verify-result.json").write_text(json.dumps(doc, indent=2))
    return run_dir


def _cfg(root: Path):
    return config_mod.load(root)


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD")


# ── the verify-at-head refusal ────────────────────────────────────────────

def test_a_verify_artifact_for_an_earlier_commit_is_refused(shipping_repo):
    """THE failure shape: run verify, commit the attestation shard, head
    moves, nothing re-verifies, and the unit reports gate parity green about
    a commit nothing judged.

    An attestation cannot name the commit containing it — but a verify result
    names whatever you run it on, so re-running after the shard commit is
    always available, and this is the refusal that makes it mandatory.
    """
    cfg = _cfg(shipping_repo)
    stale = _head(shipping_repo)
    for scope in ("smoke", "contracts"):
        _write_verify(shipping_repo, scope=scope, head=stale,
                      stamp=f"20260101T0000{len(scope)}Z-{scope}")
    # the shard commit: head moves, the artifacts keep naming the old sha
    (shipping_repo / ".warden" / "memory").mkdir(parents=True, exist_ok=True)
    (shipping_repo / ".warden" / "memory" / "shard.json").write_text("{}\n")
    _git(shipping_repo, "add", "-A", "--", ".warden/memory")
    _git(shipping_repo, "commit", "-q", "-m", "attestation shard")
    head = _head(shipping_repo)
    assert head != stale

    check = ship_mod.check_verify_at_head(cfg, base="main", head=head)
    assert not check.ok
    assert head[:12] in check.found
    assert "NO verify artifact" in check.found

    # and the same tree passes the moment a verify result names the shipped head
    for scope in ("smoke", "contracts"):
        _write_verify(shipping_repo, scope=scope, head=head,
                      stamp=f"20260102T0000{len(scope)}Z-{scope}")
    assert ship_mod.check_verify_at_head(cfg, base="main", head=head).ok


def test_a_verify_artifact_from_a_dirty_tree_is_refused(shipping_repo):
    """A PASS recorded on a tree with uncommitted content did not judge HEAD
    alone, and `verify` deliberately runs dirty — so the flag has to bite
    somewhere, and the tail is where."""
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)
    for scope in ("smoke", "contracts"):
        _write_verify(shipping_repo, scope=scope, head=head, dirty=True,
                      stamp=f"20260101T0000{len(scope)}Z-{scope}")
    check = ship_mod.check_verify_at_head(cfg, base="main", head=head)
    assert not check.ok and "DIRTY" in check.found


def test_a_verify_artifact_with_no_recorded_tree_state_is_refused(
        shipping_repo):
    """Tri-state, the house rule: an ABSENT `dirty` key means git could not
    answer, which is not the same as clean and must not be read as it."""
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)
    for scope in ("smoke", "contracts"):
        _write_verify(shipping_repo, scope=scope, head=head, dirty=None,
                      stamp=f"20260101T0000{len(scope)}Z-{scope}")
    check = ship_mod.check_verify_at_head(cfg, base="main", head=head)
    assert not check.ok and "no tree state" in check.found


def test_a_failed_verify_artifact_is_refused_and_says_so(shipping_repo):
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)
    for scope in ("smoke", "contracts"):
        _write_verify(shipping_repo, scope=scope, head=head, passed=False,
                      stamp=f"20260101T0000{len(scope)}Z-{scope}")
    check = ship_mod.check_verify_at_head(cfg, base="main", head=head)
    assert not check.ok and "FAIL" in check.found


def test_the_required_scopes_come_from_the_shipped_reach_derivation(
        shipping_repo, monkeypatch):
    """One spelling of the reach rule, never two.

    `warden declare check` D-01, `warden review` and `audit` all resolve which
    verify scopes a diff requires through `verify.scopes_for_paths`. A second
    derivation inside ship could let one scope answer for a diff it never
    exercised, so this asserts ship CALLS that function rather than agreeing
    with it by coincidence.
    """
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)
    seen = {}
    real = verify_mod.scopes_for_paths

    def spy(config, paths):
        seen["paths"] = tuple(paths) if paths is not None else None
        return real(config, paths)

    monkeypatch.setattr(ship_mod.verify_mod, "scopes_for_paths", spy)
    scopes, paths = ship_mod.required_scopes(cfg, "main", head)
    assert seen["paths"] == tuple(paths), (
        "ship did not route the changed paths through scopes_for_paths — a "
        "second spelling of the reach rule is the drift this bead forbids")
    assert "app/thing.py" in paths
    assert scopes == real(cfg, paths)


def test_a_range_that_will_not_resolve_cannot_be_evaluated(shipping_repo):
    """A step that cannot evaluate REFUSES. Exit 2, not a guessed scope set."""
    cfg = _cfg(shipping_repo)
    with pytest.raises(ship_mod.ShipError) as e:
        ship_mod.required_scopes(cfg, "no-such-ref", _head(shipping_repo))
    assert "will not resolve" in str(e.value)


# ── the title refusal: a title past the budget ────────────────────────────

def test_a_title_over_the_budget_is_refused_before_the_pr_exists(
        shipping_repo):
    """A title past commit-lint's budget is refused before the PR exists,
    rather than failing after it is open; listing every bead id is the usual
    way over.

    Driven through the REAL `scripts/commit-lint.sh`, so the character budget
    and the forge's " (#N)" strip stay the validator's and are not counted a
    second time here.
    """
    cfg = _cfg(shipping_repo)
    linter = REPO_ROOT / "scripts" / "commit-lint.sh"
    long_title = ("fix(warden): the ship tail, the progress table and the four "
                  "checks that had to be run by hand "
                  "(agentops-fyo6, agentops-4a8r, agentops-nvd)")
    assert len(long_title) > 100, "the fixture title must exceed the budget"
    check = ship_mod.check_title(cfg, title=long_title, linter=linter)
    assert not check.ok
    assert "exceeds 100 chars" in check.found

    ok_title = "feat(warden): one command for the ship tail (agentops-fyo6)"
    assert ship_mod.check_title(cfg, title=ok_title, linter=linter).ok


def test_a_title_off_the_convention_is_refused(shipping_repo):
    cfg = _cfg(shipping_repo)
    linter = REPO_ROOT / "scripts" / "commit-lint.sh"
    check = ship_mod.check_title(cfg, title="ship the thing", linter=linter)
    assert not check.ok and "REFUSED" in check.found


def test_a_title_nothing_could_lint_is_never_reported_as_linted(
        shipping_repo):
    """Fail-closed: a missing validator is exit 2, not a pass. Passing here
    would simply move the red from this step into CI, which is the round trip
    the command exists to remove."""
    cfg = _cfg(shipping_repo)
    with pytest.raises(ship_mod.ShipError) as e:
        ship_mod.check_title(cfg, title="feat(warden): x (agentops-1)",
                             linter=shipping_repo / "nope.sh")
    assert "cannot be linted" in str(e.value)


def test_an_empty_title_is_refused(shipping_repo):
    check = ship_mod.check_title(_cfg(shipping_repo), title="   ",
                                 linter=REPO_ROOT / "scripts" / "commit-lint.sh")
    assert not check.ok and "empty" in check.found


# ── the attestation refusal ───────────────────────────────────────────────

def test_a_findings_open_tip_attestation_is_refused(shipping_repo,
                                                         monkeypatch):
    """One attestation covers one round, so an early `findings-open` shard is
    expected — what must never ship is a TIP that still carries one."""
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)
    monkeypatch.setattr(
        ship_mod.attest_mod, "check_range",
        lambda root, *, base, head: {
            "attested": True,
            "open_verdict": {"sha": head, "shard": "s.json",
                             "verdict": "findings-open"}})
    check = ship_mod.check_attest_clean(cfg, base="main", head=head)
    assert not check.ok and "findings open" in check.found


def test_a_branch_with_no_attestation_is_refused(shipping_repo,
                                                      monkeypatch):
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)
    monkeypatch.setattr(ship_mod.attest_mod, "check_range",
                        lambda root, *, base, head: {"attested": False,
                                                     "open_verdict": None})
    check = ship_mod.check_attest_clean(cfg, base="main", head=head)
    assert not check.ok and "NO committed attestation" in check.found


def test_an_unreadable_shard_store_is_not_an_absent_attestation(
        shipping_repo, monkeypatch):
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)

    def boom(root, *, base, head):
        raise attest_mod.AttestError("the shard store could not be read")

    monkeypatch.setattr(ship_mod.attest_mod, "check_range", boom)
    with pytest.raises(ship_mod.ShipError):
        ship_mod.check_attest_clean(cfg, base="main", head=head)


# ── the push refusal: a branch the remote does not carry ──────────────────

def test_a_branch_the_remote_does_not_carry_is_refused(shipping_repo,
                                                            monkeypatch):
    """Finished work that was never pushed can die with its only copy
    local.

    The confirmation is deliberately a `ls-remote` against the remote rather
    than the local remote-tracking ref: a tracking ref is this checkout's
    BELIEF about the remote, and "I believe I pushed" is exactly the claim
    this refusal must not trust.
    """
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)
    calls = []

    def fake(argv, cwd):
        calls.append(argv)
        if argv[:2] == ["git", "push"]:
            return 0, ""
        if argv[:2] == ["git", "ls-remote"]:
            return 0, f"{'0' * 40}\trefs/heads/feat/x"
        raise AssertionError(f"unexpected argv {argv}")

    monkeypatch.setattr(ship_mod, "run_cmd", fake)
    check = ship_mod.push(cfg, branch="feat/x", head=head)
    assert not check.ok
    assert "NOT on the remote" in check.found
    assert ["git", "push", "--set-upstream", "origin", "feat/x"] in calls, (
        "the branch must be pushed by its LITERAL name")
    assert not any("--force" in a or "-f" in a for a in calls)

    monkeypatch.setattr(
        ship_mod, "run_cmd",
        lambda argv, cwd: (0, f"{head}\trefs/heads/feat/x")
        if argv[:2] == ["git", "ls-remote"] else (0, ""))
    assert ship_mod.push(cfg, branch="feat/x", head=head).ok


def test_ship_refuses_to_push_the_default_branch(shipping_repo,
                                                      monkeypatch):
    def fake(argv, cwd):
        raise AssertionError(f"nothing should run: {argv}")

    monkeypatch.setattr(ship_mod, "run_cmd", fake)
    for branch in ("main", "master", "HEAD"):
        check = ship_mod.push(_cfg(shipping_repo), branch=branch, head="a" * 40)
        assert not check.ok and "refusing to push" in check.found


def test_an_unverifiable_push_cannot_be_evaluated(shipping_repo,
                                                       monkeypatch):
    monkeypatch.setattr(
        ship_mod, "run_cmd",
        lambda argv, cwd: (0, "") if argv[:2] == ["git", "push"] else (2, "no remote"))
    with pytest.raises(ship_mod.ShipError) as e:
        ship_mod.push(_cfg(shipping_repo), branch="feat/x", head="a" * 40)
    assert "unconfirmed push" in str(e.value)


# ── merge authority ───────────────────────────────────────────────────────

def test_ship_builds_no_merge_argv_and_names_no_merge_literal(
        shipping_repo, monkeypatch):
    """Merge authority is the founder's, and ship must not be ABLE to merge.

    Two halves. The argv half drives the PR step and asserts nothing it builds
    names a merge. The source half reads every STRING LITERAL in
    `warden/ship.py` out of its own AST — literals, not prose, because this
    module's docstrings discuss merging at length and a grep over the text
    would be a guard that can only pass.
    """
    cfg = _cfg(shipping_repo)
    body = shipping_repo / "body.md"
    body.write_text("evidence chain\n")
    argvs = []

    def fake(argv, cwd):
        argvs.append(argv)
        if argv[:3] == ["gh", "pr", "view"]:
            return 1, "no pull requests found"
        return 0, "https://github.com/o/n/pull/7"

    monkeypatch.setattr(ship_mod, "run_cmd", fake)
    check = ship_mod.open_pr(cfg, branch="feat/x", title="feat(warden): x "
                            "(agentops-fyo6)", body_file=body)
    assert check.ok and "pull/7" in check.found
    assert any(a[:3] == ["gh", "pr", "create"] for a in argvs)
    for argv in argvs:
        assert "merge" not in argv and "--auto" not in argv and "--admin" not in argv

    tree = ast.parse((Path(ship_mod.__file__)).read_text())
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    # Docstrings are literals too, so the assertion is over ARGUMENT-shaped
    # literals: an argv element is a single word or a single flag, never prose.
    argish = [s for s in literals if len(s.split()) == 1]
    for bad in ("merge", "--auto", "--admin", "--merge", "--squash", "--rebase"):
        assert bad not in argish, (
            f"warden/ship.py carries the literal {bad!r} — ship opens a PR and "
            "stops; merge authority is the founder's")


def test_an_existing_pr_is_updated_rather_than_duplicated(shipping_repo,
                                                               monkeypatch):
    """Idempotent: a builder re-running the tail after a refusal is the normal
    case, and a second `gh pr create` opens a duplicate."""
    cfg = _cfg(shipping_repo)
    body = shipping_repo / "body.md"
    body.write_text("evidence\n")
    argvs = []

    def fake(argv, cwd):
        argvs.append(argv)
        if argv[:3] == ["gh", "pr", "view"]:
            return 0, "OPEN main https://github.com/o/n/pull/7"
        return 0, ""

    monkeypatch.setattr(ship_mod, "run_cmd", fake)
    check = ship_mod.open_pr(cfg, branch="feat/x", title="t", body_file=body)
    assert check.ok and "updated" in check.found
    assert any(a[:3] == ["gh", "pr", "edit"] for a in argvs)
    assert not any(a[:3] == ["gh", "pr", "create"] for a in argvs)


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_a_closed_or_merged_pr_on_the_branch_is_not_the_one_to_update(
        shipping_repo, monkeypatch, capsys, state):
    """`fail-closed`: an existing PR is checked for validity, not only
    existence.

    `gh pr view <branch>` resolves a CLOSED or MERGED PR for a reused branch
    name — this repo squash-merges, so a recycled branch is the ordinary path —
    and taking the edit branch there would report `pr-opened OK — updated
    <url>` at exit 0 with the pushed commits in no open PR at all.
    """
    cfg = _cfg(shipping_repo)
    body = shipping_repo / "body.md"
    body.write_text("evidence\n")
    argvs = []

    def fake(argv, cwd):
        argvs.append(argv)
        if argv[:3] == ["gh", "pr", "view"]:
            return 0, f"{state} main https://github.com/o/n/pull/199"
        return 0, "https://github.com/o/n/pull/200"

    monkeypatch.setattr(ship_mod, "run_cmd", fake)
    check = ship_mod.open_pr(cfg, branch="feat/x", title="t", body_file=body)
    assert check.ok and "pull/200" in check.found
    assert any(a[:3] == ["gh", "pr", "create"] for a in argvs), (
        f"ship edited a {state} PR instead of opening a new one")
    assert not any(a[:3] == ["gh", "pr", "edit"] for a in argvs)
    assert state in capsys.readouterr().err, (
        "the reason a new PR was opened must be said, not inferred")
    # the state is read from the forge, not assumed
    view = next(a for a in argvs if a[:3] == ["gh", "pr", "view"])
    assert "url,state,baseRefName" in view


def test_an_open_pr_into_another_base_is_refused_not_retargeted(
        shipping_repo, monkeypatch):
    """`fail-closed`: the BASE axis is checked as well as the state, one
    field over in the same query.

    The step's own `checked` line claims "a PR for <branch> into <base>";
    reading only `state` would check two thirds of that sentence. Silently
    retargeting someone's PR would be worse than refusing, so it refuses and
    names both bases.
    """
    cfg = _cfg(shipping_repo)
    body = shipping_repo / "body.md"
    body.write_text("evidence\n")
    argvs = []

    def fake(argv, cwd):
        argvs.append(argv)
        if argv[:3] == ["gh", "pr", "view"]:
            return 0, "OPEN release https://github.com/o/n/pull/7"
        return 0, ""

    monkeypatch.setattr(ship_mod, "run_cmd", fake)
    check = ship_mod.open_pr(cfg, branch="feat/x", title="t", body_file=body,
                             base="main")
    assert not check.ok
    assert "release" in check.found and "main" in check.found
    assert not any(a[:3] == ["gh", "pr", "edit"] for a in argvs)
    assert not any(a[:3] == ["gh", "pr", "create"] for a in argvs)
    view = next(a for a in argvs if a[:3] == ["gh", "pr", "view"])
    assert "url,state,baseRefName" in view


def test_a_gh_banner_on_a_successful_call_never_rides_into_the_url(
        shipping_repo, monkeypatch):
    """`unmapped:subprocess-noise-parsed-as-value`.

    `run_cmd` returns stdout and stderr concatenated, and `gh` writes its "A
    new release of gh is available" banner to stderr on calls that succeed. A
    `partition(" ")` taking everything after the first space — following lines
    included — would make the banner part of the PR URL this step reports and
    writes into `ship-result.json`.
    """
    cfg = _cfg(shipping_repo)
    body = shipping_repo / "body.md"
    body.write_text("evidence\n")

    def fake(argv, cwd):
        if argv[:3] == ["gh", "pr", "view"]:
            return 0, ("OPEN main https://github.com/o/n/pull/7\n"
                       "A new release of gh is available: 2.0.0 → 2.1.0")
        return 0, ""

    monkeypatch.setattr(ship_mod, "run_cmd", fake)
    check = ship_mod.open_pr(cfg, branch="feat/x", title="t", body_file=body)
    assert check.found == "updated https://github.com/o/n/pull/7", check.found


def test_a_gate_that_did_not_run_is_never_reported_as_a_refusal(
        shipping_repo, monkeypatch):
    """`enforcement-truth`: the exit codes pass through; they do not
    collapse.

    `warden review` returns 2 for "gate DID NOT RUN" and 1 only for a blocking
    finding. Testing `if code != 0` would render both as `REFUSED at step
    4/4`, exit 1 — "the tail ran and refused" said about a gate that never
    ran, which is exactly what this module's own ShipError docstring says a
    unit must not be able to do. Reachable from ship's own
    default `--base origin/main` in a worktree that has not fetched.
    """
    cfg = _cfg(shipping_repo)
    monkeypatch.setattr(ship_mod, "run_cmd",
                        lambda argv, cwd: (2, "warden review: gate DID NOT "
                                              "RUN: bad revision"))
    with pytest.raises(ship_mod.ShipError) as e:
        ship_mod.check_parity(cfg, base="origin/main")
    assert "DID NOT RUN" in str(e.value)
    # exit 1 is still a refusal, not a could-not-run
    monkeypatch.setattr(ship_mod, "run_cmd",
                        lambda argv, cwd: (1, "2 blocking finding(s)"))
    check = ship_mod.check_parity(cfg, base="origin/main")
    assert not check.ok and "exit 1" in check.found


def test_the_narration_reaches_a_piped_reader_in_order(shipping_repo):
    """stdout is block-buffered when it is not a terminal, so an unflushed
    piped run prints the stderr refusal line FIRST and the step narration
    after it — backwards, in the one command whose product IS its narration.
    """
    tree = ast.parse(Path(ship_mod.__file__).read_text())
    run_fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "run")
    renders = [n for n in ast.walk(run_fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "print"
               and any(isinstance(a, ast.Call)
                       and getattr(a.func, "id", "") == "render_check"
                       for a in n.args)]
    assert renders, "the step narration is no longer printed by run()"
    for call in renders:
        flush = next((k for k in call.keywords if k.arg == "flush"), None)
        assert flush is not None and flush.value.value is True, (
            "the step narration must be flushed as it is produced, or a piped "
            "run reads the refusal before the step it refused")


def test_a_pr_with_no_body_file_cannot_be_evaluated(shipping_repo):
    with pytest.raises(ship_mod.ShipError) as e:
        ship_mod.open_pr(_cfg(shipping_repo), branch="feat/x", title="t",
                         body_file=shipping_repo / "missing.md")
    assert "evidence chain" in str(e.value)


# ── the tail as a whole ───────────────────────────────────────────────────

def test_the_tail_stops_at_the_first_refusal(shipping_repo, monkeypatch,
                                                  capsys):
    """Ordered cheapest-correction-first, and nothing after a refusal runs:
    pushing a branch whose verify artifact names the wrong commit is how a
    green claim about the wrong sha reaches a PR."""
    cfg = _cfg(shipping_repo)
    monkeypatch.setattr(ship_mod, "check_attest_clean",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("step 2 must not run")))
    checks, code = ship_mod.run(cfg, base="main", title="t", body_file=None,
                                checks_only=True)
    assert code == 1
    assert [c.step for c in checks] == ["verify-at-head"]
    out = capsys.readouterr()
    assert "ship step 1/4 verify-at-head: REFUSED" in out.out
    assert "checked:" in out.out and "found:" in out.out
    assert "nothing after it ran" in out.err


def test_every_step_prints_what_it_checked_and_what_it_found(
        shipping_repo, monkeypatch, capsys):
    """All four checks green, each narrating itself — the property that makes
    a unit's "parity green" claim auditable instead of asserted."""
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)
    for scope in ("smoke", "contracts"):
        _write_verify(shipping_repo, scope=scope, head=head,
                      stamp=f"20260101T0000{len(scope)}Z-{scope}")
    monkeypatch.setattr(ship_mod.attest_mod, "check_range",
                        lambda root, *, base, head: {"attested": True,
                                                     "open_verdict": None})
    monkeypatch.setattr(ship_mod, "run_cmd", lambda argv, cwd: (0, ""))
    checks, code = ship_mod.run(
        cfg, base="main", title="feat(warden): x (agentops-fyo6)",
        body_file=None, checks_only=True,
        linter=REPO_ROOT / "scripts" / "commit-lint.sh")
    assert code == 0
    assert [c.step for c in checks] == ["verify-at-head", "attest-clean",
                                        "pr-title", "gate-parity"]
    out = capsys.readouterr().out
    for index in (1, 2, 3, 4):
        assert f"ship step {index}/4" in out
    assert all(c.checked and c.found for c in checks)


def test_a_passing_parity_step_records_a_progress_boundary(
        shipping_repo, monkeypatch):
    """Ship and progress meet here: ship records the boundaries it PASSES,
    so the progress table reflects the tail without a builder remembering to
    say so.

    It records `parity-run` and not `verify-run`: step 1 reads an artifact
    another command produced, and claiming a boundary ship did not pass would
    make the table say something untrue about where the unit is.
    """
    from warden import progress as progress_mod
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)
    for scope in ("smoke", "contracts"):
        _write_verify(shipping_repo, scope=scope, head=head,
                      stamp=f"20260101T0000{len(scope)}Z-{scope}")
    monkeypatch.setattr(ship_mod.attest_mod, "check_range",
                        lambda root, *, base, head: {"attested": True,
                                                     "open_verdict": None})
    monkeypatch.setattr(ship_mod, "run_cmd", lambda argv, cwd: (0, ""))
    ship_mod.run(cfg, base="main", title="feat(warden): x (agentops-fyo6)",
                 body_file=None, checks_only=True,
                 linter=REPO_ROOT / "scripts" / "commit-lint.sh")
    entry = progress_mod.last_boundary(shipping_repo)
    assert entry and entry["boundary"] == "parity-run"
    assert entry["head"] == head


def test_checks_only_never_reaches_push_or_pr(shipping_repo, monkeypatch):
    """--checks-only is the form a builder runs mid-iteration, and it must not
    be able to push or open anything."""
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)
    for scope in ("smoke", "contracts"):
        _write_verify(shipping_repo, scope=scope, head=head,
                      stamp=f"20260101T0000{len(scope)}Z-{scope}")
    monkeypatch.setattr(ship_mod.attest_mod, "check_range",
                        lambda root, *, base, head: {"attested": True,
                                                     "open_verdict": None})
    argvs = []

    def fake(argv, cwd):
        argvs.append(argv)
        return 0, ""

    monkeypatch.setattr(ship_mod, "run_cmd", fake)
    checks, code = ship_mod.run(
        cfg, base="main", title="feat(warden): x (agentops-fyo6)",
        body_file=None, checks_only=True,
        linter=REPO_ROOT / "scripts" / "commit-lint.sh")
    assert code == 0 and len(checks) == 4
    assert not any(a[0] == "gh" for a in argvs)
    assert not any(a[:2] == ["git", "push"] for a in argvs)


def test_opening_a_pr_with_no_body_file_cannot_be_evaluated(shipping_repo,
                                                                 monkeypatch):
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)
    for scope in ("smoke", "contracts"):
        _write_verify(shipping_repo, scope=scope, head=head,
                      stamp=f"20260101T0000{len(scope)}Z-{scope}")
    with pytest.raises(ship_mod.ShipError) as e:
        ship_mod.run(cfg, base="main", title="t", body_file=None,
                     checks_only=False)
    assert "--body-file is required" in str(e.value)


# ── the one-definition seam ───────────────────────────────────────────────

def test_the_review_pairing_and_the_ship_refusal_share_one_definition(
        shipping_repo, monkeypatch):
    """`audit.verify_for_review` and `warden ship` must answer "did a verify run
    judge THIS commit under THIS scope" identically.

    There is one function, and this asserts the delegation rather than the
    agreement: two implementations that agree today are two that can drift
    tomorrow.
    """
    seen = {}
    real = audit_mod.verify_for_commit

    def spy(root, head, scopes):
        seen["args"] = (head, tuple(scopes))
        return real(root, head, scopes)

    monkeypatch.setattr(audit_mod, "verify_for_commit", spy)
    audit_mod.verify_for_review(shipping_repo, {"head_sha": "a" * 40},
                                ["smoke"])
    assert seen["args"] == ("a" * 40, ("smoke",))


def test_ship_writes_its_evidence_either_way(shipping_repo, monkeypatch):
    """A refusal is evidence too — the `deploy` convention. The artifact names
    which step refused, so a unit's report can be checked against it."""
    from warden import cli
    monkeypatch.chdir(shipping_repo)
    code = cli.main(["ship", "--base", "main", "--title", "nope",
                     "--checks-only"])
    assert code == 1
    runs = sorted((shipping_repo / ".warden" / "out").glob("*-ship"))
    assert runs, "ship wrote no run dir"
    doc = json.loads((runs[-1] / "ship-result.json").read_text())
    assert doc["passed"] is False
    assert doc["checks"][0]["step"] == "verify-at-head"
    manifest = json.loads((runs[-1] / "manifest.json").read_text())
    assert manifest["refused_at"] == "verify-at-head"


def test_the_age_of_the_artifact_is_not_provenance(shipping_repo):
    """A NEWER verify artifact for another commit must not answer for this one.

    The sharper half of the verify-at-head failure: recency is not
    provenance, and `audit.latest_artifact`'s `match` is what keeps a
    130ms-newer run of a cheap scope from standing in.
    """
    cfg, head = _cfg(shipping_repo), _head(shipping_repo)
    for scope in ("smoke", "contracts"):
        _write_verify(shipping_repo, scope=scope, head=head,
                      stamp=f"20260101T0000{len(scope)}Z-{scope}")
    assert ship_mod.check_verify_at_head(cfg, base="main", head=head).ok
    # a LATER run, same scopes, a different commit
    for scope in ("smoke", "contracts"):
        _write_verify(shipping_repo, scope=scope, head="b" * 40,
                      stamp=f"20991231T0000{len(scope)}Z-{scope}")
    assert ship_mod.check_verify_at_head(cfg, base="main", head=head).ok, (
        "a newer artifact for another commit displaced the one that judged "
        "HEAD — that is recency standing in for provenance")


def test_render_check_names_the_step_and_both_halves():
    check = ship_mod.Check("a-step", False, "what it checked", "what it found")
    text = ship_mod.render_check(check, index=2, total=6)
    assert "ship step 2/6 a-step: REFUSED" in text
    assert "what it checked" in text and "what it found" in text
    assert "OK" in ship_mod.render_check(
        ship_mod.Check("s", True, "c", "f"), index=1, total=1)


def test_as_json_carries_every_step_and_the_verdict():
    checks = [ship_mod.Check("one", True, "c", "f", {"scopes": ["smoke"]}),
              ship_mod.Check("two", False, "c", "f")]
    doc = json.loads(ship_mod.as_json(checks, base="main", head="a" * 40,
                                      branch="feat/x", title="t"))
    assert doc["passed"] is False
    assert [c["step"] for c in doc["checks"]] == ["one", "two"]
    assert doc["checks"][0]["scopes"] == ["smoke"]
    assert doc["branch"] == "feat/x" and doc["base"] == "main"


# ── the roster refusal: a builder-asserted roster under a declared crew ───
#
# `attest write` without `--review-dir` records the roster as the builder's
# word (`unverified-roster`) and exits 0, and nothing downstream read the
# field: a session could skip BOTH the roster check and the declared-crew
# check by leaving one flag off, and the PR still shipped. For a repo that
# declares a crew, that shard is now a ship refusal.

_CREW_GRAPH = {
    "version": 1,
    "nodes": {
        "builder": {"kind": "skill", "impl": "x", "authority": "find"},
        "code-reviewer": {"kind": "subagent", "impl": "r",
                          "authority": "find"},
        "scoped-re-reviewer": {"kind": "subagent", "impl": "s",
                               "authority": "find"},
        "cross-examiner": {"kind": "subagent", "impl": "y",
                           "authority": "judge"},
        "founder": {"kind": "human", "authority": "merge"},
    },
    "edges": [
        {"from": "builder", "to": "code-reviewer", "payload": "diff"},
        {"from": "code-reviewer", "to": "cross-examiner",
         "payload": "findings"},
        {"from": "builder", "to": "scoped-re-reviewer", "payload": "diff"},
        {"from": "cross-examiner", "to": "founder", "payload": "pr"},
    ],
    "review": {
        "rounds": [{"round": 1, "roles": ["code-reviewer", "cross-examiner"]},
                   {"round": 2, "roles": ["scoped-re-reviewer"]}],
        "cap": 2,
        "lenses": [{"id": "consumer-blast-radius",
                    "checklist": ["a shipped schema changed?"]}],
        "merge_authority": "founder",
    },
}


def _declare_crew(root: Path, *, review: bool = True,
                  graph: dict | None = None) -> None:
    """graph.yaml as an enrolled repo declares it, plus the `repair.budget`
    the review cap must equal. Committed, because `attest check`'s range and
    the shard beside it are read out of git."""
    doc = copy.deepcopy(_CREW_GRAPH) if graph is None else graph
    if not review:
        doc.pop("review", None)
    (root / "graph.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    repo = yaml.safe_load((root / "repo.yaml").read_text())
    repo["repair"] = {"budget": 2}
    (root / "repo.yaml").write_text(yaml.safe_dump(repo, sort_keys=False))
    _git(root, "add", "-A", "--", "graph.yaml", "repo.yaml")
    _git(root, "commit", "-q", "-m", "declare the review crew")


def _commit_shard(root: Path, *, sha: str, roster: str | None,
                  verdict: str = "clean") -> str:
    """A committed attestation shard naming `sha`, as `memory ingest` writes
    one. `roster` is the `roster_verification` state the shard carries; None
    is a shard written before the field existed."""
    rel = ".warden/memory/attest/20260101T000000Z-fixture.json"
    doc = {"schema": 1, "source": "attest", "sha": sha, "verdict": verdict,
           "reviewers": [{"role": "code-reviewer", "agent": "subagent",
                          "round": 1, "returned": True, "findings": 0,
                          "output": "code-reviewer.json"}],
           "records": []}
    if roster is not None:
        doc["roster_verification"] = roster
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(json.dumps(doc, indent=2) + "\n")
    _git(root, "add", "-A", "--", ".warden/memory")
    _git(root, "commit", "-q", "-m", "attestation shard")
    return rel


@pytest.mark.parametrize("roster, phrase", [
    ("unverified-roster", "builder-asserted"),
    (None, "records no roster state"),
])
def test_ship_refuses_a_builder_asserted_roster_under_a_declared_crew(
        shipping_repo, roster, phrase):
    """THE hole this closes: the roster check and the declared-crew check are
    both bought by ONE flag, so leaving it off skipped both and the shard
    still said `verdict: clean`. Ship reads what the shard says about its own
    roster, names the shard, and names the flag to re-run with.

    The absent state is refused too and for the same reason: a shard written
    before the field existed cannot say its roster was checked, and "could
    not say" must never read as "checked and fine".
    """
    _declare_crew(shipping_repo)
    attested = _head(shipping_repo)
    shard = _commit_shard(shipping_repo, sha=attested, roster=roster)
    check = ship_mod.check_attest_clean(_cfg(shipping_repo), base="main",
                                        head=_head(shipping_repo))
    assert not check.ok, check.found
    assert shard in check.found, check.found
    assert phrase in check.found, check.found
    assert "--review-dir" in check.found, (
        "the refusal does not name what to re-run: " + check.found)


def test_ship_passes_a_verified_roster_under_the_same_declared_crew(
        shipping_repo):
    """The control. The refusal must bite on the roster state and nothing
    else about the fixture, or it is a check on the wrong thing."""
    _declare_crew(shipping_repo)
    attested = _head(shipping_repo)
    _commit_shard(shipping_repo, sha=attested, roster="verified")
    check = ship_mod.check_attest_clean(_cfg(shipping_repo), base="main",
                                        head=_head(shipping_repo))
    assert check.ok, check.found


def test_ship_reads_no_roster_for_a_repo_that_declares_no_crew(shipping_repo):
    """A repo with no `review` block has no declared crew to have been
    dispatched, so there is nothing for the roster to be checked against and
    the shard ships exactly as it did before — the same boundary
    `attest write --review-dir` draws."""
    _declare_crew(shipping_repo, review=False)
    attested = _head(shipping_repo)
    _commit_shard(shipping_repo, sha=attested, roster="unverified-roster")
    check = ship_mod.check_attest_clean(_cfg(shipping_repo), base="main",
                                        head=_head(shipping_repo))
    assert check.ok, check.found


# ── what turns the roster step on: a DECLARED `review` block, and nothing
# wider ───────────────────────────────────────────────────────────────────
#
# The first cut asked `declared_crew` — which fully validates graph.yaml —
# before it could answer "no review key", so a consumer whose graph.yaml this
# warden's schema rejects lost `warden ship` entirely, every run, including
# `--checks-only`, where the base commit shipped it. The trigger is now the
# narrow one the wiki describes: the document must DECLARE a `review` block.
# A document that parses can be asked which keys it carries without being
# asked to be valid; only one that cannot be decoded or parsed at all cannot
# answer, and that one still refuses.


def _rewrite_graph(root: Path, text: str | bytes) -> None:
    path = root / "graph.yaml"
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)
    _git(root, "add", "-A", "--", "graph.yaml")
    _git(root, "commit", "-q", "-m", "rewrite the graph")


def test_ship_ships_a_graph_that_does_not_validate_and_declares_no_review_block(
        shipping_repo):
    """THE consumer-blast-radius case. A graph.yaml warden's schema rejects,
    carrying no `review` key, shipped on the base commit and must ship now:
    the repo declares no crew, so there is nothing for a roster to be checked
    against, and a roster check is not a licence to re-validate a document
    the step was never about.

    `warden graph validate` still refuses this file — this is ship declining
    to enforce a rule that is another command's, on a repo that declares no
    crew, not warden blessing the graph.
    """
    _declare_crew(shipping_repo)
    # The graph is rewritten BEFORE the round attests it, and the shard
    # commit is last. Rewriting it after put a content commit outside every
    # tip's ancestry, which the coverage rule refuses on its
    # own — a true refusal, but not this test's subject, which is the ROSTER
    # arm declining to re-validate a document it was never about.
    _rewrite_graph(shipping_repo, "version: 1\nnodes: {}\n")
    attested = _head(shipping_repo)
    _commit_shard(shipping_repo, sha=attested, roster="unverified-roster")
    check = ship_mod.check_attest_clean(_cfg(shipping_repo), base="main",
                                       head=_head(shipping_repo))
    assert check.ok, check.found


def test_ship_refuses_a_declared_review_block_the_graph_cannot_resolve(
        shipping_repo):
    """Fail-closed where the question APPLIES: the document declares a
    `review` block, so a crew is claimed and the roster must be checked
    against it — and a block that does not resolve cannot say what that crew
    is. Exit 2, naming the cause, never a pass as a repo that declares none.
    """
    _declare_crew(shipping_repo)
    attested = _head(shipping_repo)
    _commit_shard(shipping_repo, sha=attested, roster="verified")
    _rewrite_graph(shipping_repo,
                   "version: 1\nnodes: {}\nreview:\n  rounds: []\n")
    with pytest.raises(ship_mod.ShipError, match="declares a review crew"):
        ship_mod.check_attest_clean(_cfg(shipping_repo), base="main",
                                    head=_head(shipping_repo))


def test_ship_refuses_a_declared_review_block_whose_cap_has_no_repair_budget(
        shipping_repo):
    """Which side this one falls on, decided and pinned: the REFUSAL side.

    The block is declared, so the repo claims a crew; `review.cap` has one
    source and with no `repair.budget` to agree with, the cap — and so the
    crew — does not resolve. `warden graph validate` refuses this graph for
    the same reason, so the repo is not one that was shipping a valid
    declaration.
    """
    _declare_crew(shipping_repo)
    attested = _head(shipping_repo)
    _commit_shard(shipping_repo, sha=attested, roster="verified")
    repo = yaml.safe_load((shipping_repo / "repo.yaml").read_text())
    repo.pop("repair", None)
    (shipping_repo / "repo.yaml").write_text(yaml.safe_dump(repo,
                                                            sort_keys=False))
    _git(shipping_repo, "add", "-A", "--", "repo.yaml")
    _git(shipping_repo, "commit", "-q", "-m", "drop the repair budget")
    with pytest.raises(ship_mod.ShipError, match="repair.budget"):
        ship_mod.check_attest_clean(_cfg(shipping_repo), base="main",
                                    head=_head(shipping_repo))


@pytest.mark.parametrize("raw, ids", [
    (b"\xff\xfe\x00bad", "undecodable"),
    ("version: 1\nnodes: {\n", "unparseable"),
], ids=["undecodable-bytes", "unparseable-yaml"])
def test_ship_cannot_evaluate_a_graph_yaml_it_cannot_read_at_all(
        shipping_repo, raw, ids):
    """The one file that refuses whatever it declares. A document warden
    cannot decode or parse cannot be asked which keys it carries, so whether
    this repo declares a crew is genuinely unknown — and "could not say" is
    never "declares none"."""
    _declare_crew(shipping_repo)
    attested = _head(shipping_repo)
    _commit_shard(shipping_repo, sha=attested, roster="verified")
    _rewrite_graph(shipping_repo, raw)
    with pytest.raises(ship_mod.ShipError, match="cannot be read"):
        ship_mod.check_attest_clean(_cfg(shipping_repo), base="main",
                                    head=_head(shipping_repo))
