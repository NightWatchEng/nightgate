"""warden deploy: declared deploy scopes run with provenance, and the gate
is a PRECONDITION of deploy, never a path around it.

Contract under test:
- exit 0: provenance preconditions passed and every scope command exited 0;
- exit 1: a determinate refusal (checkout is not the commit, dirty tree,
  unmerged commit, no clean attestation) or a scope command failure — each
  refusal NAMES its reason;
- exit 2: the check could not run (bad ref, unreadable shard store, unknown
  scope) — fail closed, never a pass and never folded into a refusal.

The artifact contract: deploy-result.json validates against the additive
deploy-result.schema.json, binds commit + rules_version, and a run manifest
records exit_status (deploy mutates state, so it writes evidence — the
memory.py evidence-dir convention).

The attestation binding: the commit's own first-parent diff must have
introduced a clean-verdict shard, read from the COMMIT'S TREE via git — the
shape a squash-merged PR (or an ingest commit) leaves on main. Filesystem
state never counts.
"""

import json
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from warden import cli

SCHEMA = (Path(__file__).resolve().parents[1] / "warden" / "schemas"
          / "deploy-result.schema.json")

REPO_YAML = """\
version: 1
repo: deployproj
components:
  app:
    path: app/
    lang: python
    description: application code
risk_tiers:
  - glob: app/**
    tier: MEDIUM
    reason: production code
verify:
  smoke:
    - run: "true"
deploy:
  staging:
    - run: "touch deployed.marker"
  broken:
    - run: "false"
review:
  rules_dir: .warden/rules
  blocking_severities: [HIGH]
"""

RULE = """\
---
id: sample-rule
severity: MEDIUM
engine: claude
applies_to: ["**"]
---
fixture rule
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _shard(sha: str, verdict: str = "clean") -> str:
    return json.dumps({
        "schema": 1,
        "source": "attest",
        "sha": sha,
        "base_sha": "0" * 40,
        "rules_version": "abc123def456",
        "reviewed_at": "2026-08-30T00:00:00+00:00",
        "verdict": verdict,
        "reviewers": [{"role": "code-reviewer", "agent": "claude"}],
        "records": [],
    }, indent=1) + "\n"


@pytest.fixture()
def deploy_repo(tmp_path: Path) -> Path:
    """A git repo with deploy scopes declared and one initial commit on main."""
    root = tmp_path / "deployproj"
    root.mkdir()
    # An enrolled repo gitignores its evidence dir (as this repo does) — the
    # dirty-tree precondition must see the USER's tree, not warden's own
    # freshly created run dir.
    (root / ".gitignore").write_text(".warden/out/\ndeployed.marker\n")
    (root / "repo.yaml").write_text(REPO_YAML)
    rules = root / ".warden" / "rules"
    rules.mkdir(parents=True)
    (rules / "sample-rule.md").write_text(RULE)
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("print('hi')\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def _commit_shard(root: Path, sha: str, verdict: str = "clean",
                  name: str = "20260830T000000Z-aaaaaaaa-bbbbbbbb.json") -> str:
    """Commit an attestation shard naming `sha` (the ingest-commit shape:
    the shard lands in the commit AFTER the one it names); returns new HEAD."""
    store = root / ".warden" / "memory" / "attest"
    store.mkdir(parents=True, exist_ok=True)
    (store / name).write_text(_shard(sha, verdict))
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "carry attestation shard")
    return _git(root, "rev-parse", "HEAD")


def _artifact(root: Path) -> dict:
    paths = sorted((root / ".warden" / "out").glob("*-deploy/deploy-result.json"))
    assert paths, "no deploy-result.json written"
    return json.loads(paths[-1].read_text())


def _manifest(root: Path) -> dict:
    paths = sorted((root / ".warden" / "out").glob("*-deploy/manifest.json"))
    assert paths, "no deploy manifest written"
    return json.loads(paths[-1].read_text())


# ---- the pass path --------------------------------------------------------

@pytest.mark.parametrize("mangle, why", [
    (lambda d: d.pop("records"), "no records key"),
    (lambda d: d.update(records={"not": "a list"}), "records not a list"),
    (lambda d: d.update(verdict=["clean"]), "a verdict that is not a string"),
    (lambda d: d.update(
        records=[{"id": "r", "rule_id": "x", "status": ["fixed"]}]),
     "a record status that is not a string"),
], ids=["no-records-key", "records-not-a-list", "verdict-not-a-string",
        "status-not-a-string"])
def test_deploy_refuses_the_shard_attest_check_calls_unreadable(
        deploy_repo, monkeypatch, capsys, mangle, why):
    """`deploy._parse_shard` is the FIFTH reader of the attest store, and it
    must apply the same filter `attest.attested_shas` applies, through the
    shared envelope definition.

    A reader that drifts from that definition drifts in the PERMISSIVE
    direction: a committed shard that the BLOCKING `attest check` calls
    unreadable would still be read here and credit the commit with a clean
    attestation for deploy eligibility, so "attested" would mean two
    different things to two commands a consumer runs.

    Driven through the real CLI, so it is the shipped path and not the helper.
    """
    c1 = _git(deploy_repo, "rev-parse", "HEAD")
    doc = json.loads(_shard(c1))
    mangle(doc)
    store = deploy_repo / ".warden" / "memory" / "attest"
    store.mkdir(parents=True, exist_ok=True)
    (store / "20260830T000000Z-aaaaaaaa-bbbbbbbb.json").write_text(
        json.dumps(doc, indent=1) + "\n")
    _git(deploy_repo, "add", ".")
    _git(deploy_repo, "commit", "-q", "-m", "carry attestation shard")
    c2 = _git(deploy_repo, "rev-parse", "HEAD")
    monkeypatch.chdir(deploy_repo)

    # An unreadable store is exit 2 — "cannot tell", never "no attestation".
    assert cli.main(["deploy", "--scope", "staging", "--commit", c2]) == 2, (
        f"a shard with {why} still credits the commit with a clean "
        "attestation, so `warden deploy` reads the store more permissively "
        "than the gate that blocks the PR")
    out = capsys.readouterr()
    said = out.out + out.err
    assert "could not be read" in said and "refuses to guess" in said, said
    # …and the refusal NAMES the cause, not only the file: the shard parses
    # fine, so "could not be read" alone would send the reader to look for
    # corrupt JSON that is not there.
    assert "records" in said or "status" in said or "verdict" in said, said


def test_deploy_pass_writes_schema_valid_artifact_bound_to_the_commit(
        deploy_repo, monkeypatch, capsys):
    c1 = _git(deploy_repo, "rev-parse", "HEAD")
    c2 = _commit_shard(deploy_repo, c1)  # HEAD carries its shard in its diff
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", c2]) == 0

    doc = _artifact(deploy_repo)
    Draft202012Validator(json.loads(SCHEMA.read_text())).validate(doc)
    assert doc["commit"] == c2
    assert doc["head"] == c2
    assert doc["scope"] == "staging"
    assert doc["rules_version"], "rules_version must be stamped on the artifact"
    assert doc["dirty"] is False
    assert doc["reachable_from_branch"] is True
    assert doc["clean_attestation"]["via"] == "introduced-by-commit"
    assert doc["clean_attestation"]["sha"] == c1
    assert doc["refusals"] == []
    assert doc["deployed"] is True
    assert doc["run"]["passed"] is True
    assert _manifest(deploy_repo)["exit_status"] == "pass"
    assert "deploy --scope staging: PASS" in capsys.readouterr().out
    assert (deploy_repo / "deployed.marker").exists()


def test_deploy_default_commit_is_head(deploy_repo, monkeypatch):
    c1 = _git(deploy_repo, "rev-parse", "HEAD")
    _commit_shard(deploy_repo, c1)
    monkeypatch.chdir(deploy_repo)
    assert cli.main(["deploy", "--scope", "staging"]) == 0


def test_deploy_failing_scope_command_exits_1_and_records_fail(
        deploy_repo, monkeypatch):
    c1 = _git(deploy_repo, "rev-parse", "HEAD")
    c2 = _commit_shard(deploy_repo, c1)
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "broken", "--commit", c2]) == 1

    doc = _artifact(deploy_repo)
    assert doc["deployed"] is False
    assert doc["run"]["passed"] is False
    assert _manifest(deploy_repo)["exit_status"] == "fail"


# ---- the named refusals ---------------------------------------------------

def test_deploy_refuses_an_unmerged_commit_naming_the_reason(
        deploy_repo, monkeypatch, capsys):
    """'Humans hold every merge': a commit main does not carry must not
    deploy, and the refusal names reachability, not a vague failure."""
    _git(deploy_repo, "checkout", "-q", "-b", "side")
    (deploy_repo / "app" / "side.py").write_text("x = 1\n")
    _git(deploy_repo, "add", ".")
    _git(deploy_repo, "commit", "-q", "-m", "unmerged work")
    side = _git(deploy_repo, "rev-parse", "HEAD")
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", side]) == 1

    out = capsys.readouterr()
    assert "not reachable" in out.out + out.err
    assert "main" in out.out + out.err
    doc = _artifact(deploy_repo)
    assert doc["reachable_from_branch"] is False
    assert any("not reachable" in r for r in doc["refusals"])
    assert doc["deployed"] is False and doc["run"] is None
    assert _manifest(deploy_repo)["exit_status"] == "refused"
    # the refusal happened BEFORE any deploy command ran
    assert not (deploy_repo / "deployed.marker").exists()


def test_deploy_refuses_a_merged_commit_with_no_clean_attestation(
        deploy_repo, monkeypatch, capsys):
    c1 = _git(deploy_repo, "rev-parse", "HEAD")  # on main, but unattested
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", c1]) == 1

    out = capsys.readouterr()
    assert "attestation" in out.out + out.err
    doc = _artifact(deploy_repo)
    assert doc["clean_attestation"] is None
    assert any("attestation" in r for r in doc["refusals"])
    assert _manifest(deploy_repo)["exit_status"] == "refused"
    assert not (deploy_repo / "deployed.marker").exists()


def test_deploy_refuses_when_the_only_attestation_is_findings_open(
        deploy_repo, monkeypatch):
    """A findings-open shard is a review that did NOT come back clean — it
    must not satisfy the clean-attestation precondition."""
    c1 = _git(deploy_repo, "rev-parse", "HEAD")
    c2 = _commit_shard(deploy_repo, c1, verdict="findings-open")
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", c2]) == 1
    doc = _artifact(deploy_repo)
    assert doc["clean_attestation"] is None
    assert any("attestation" in r for r in doc["refusals"])


def test_deploy_accepts_the_squash_commit_that_carries_its_shard(
        deploy_repo, monkeypatch):
    """After a squash merge no shard sha survives onto main — the binding
    that DOES survive is the shard file the squash commit itself
    introduces."""
    _git(deploy_repo, "checkout", "-q", "-b", "feature")
    (deploy_repo / "app" / "feat.py").write_text("y = 2\n")
    _git(deploy_repo, "add", ".")
    _git(deploy_repo, "commit", "-q", "-m", "feature work")
    branch_sha = _git(deploy_repo, "rev-parse", "HEAD")
    _commit_shard(deploy_repo, branch_sha)  # the PR's committed shard
    _git(deploy_repo, "checkout", "-q", "main")
    _git(deploy_repo, "merge", "--squash", "-q", "feature")
    _git(deploy_repo, "commit", "-q", "-m", "feature (squash)")
    squash = _git(deploy_repo, "rev-parse", "HEAD")
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", squash]) == 0
    doc = _artifact(deploy_repo)
    assert doc["clean_attestation"]["via"] == "introduced-by-commit"
    assert doc["clean_attestation"]["sha"] == branch_sha
    assert doc["deployed"] is True


# ---- the checkout IS what deploys ------------------------------------------

def test_deploy_refuses_when_the_checkout_is_not_the_commit_regression(
        deploy_repo, monkeypatch, capsys):
    """run_scope shells out in the working tree, so certifying one sha while
    another is checked out would ship unreviewed code under an attested
    commit's label. HEAD must BE the
    commit, and the mismatch is a named refusal."""
    c1 = _git(deploy_repo, "rev-parse", "HEAD")
    _commit_shard(deploy_repo, c1)  # HEAD is now c2, c1 stays attested-looking
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", c1]) == 1

    out = capsys.readouterr()
    assert "checkout" in out.out + out.err
    doc = _artifact(deploy_repo)
    assert doc["head"] != doc["commit"]
    assert any("checkout" in r for r in doc["refusals"])
    assert not (deploy_repo / "deployed.marker").exists()


def test_deploy_refuses_a_dirty_tree_naming_the_reason_regression(
        deploy_repo, monkeypatch, capsys):
    """An uncommitted edit means what ships is not the commit —
    refused by name, before any scope command runs."""
    c1 = _git(deploy_repo, "rev-parse", "HEAD")
    c2 = _commit_shard(deploy_repo, c1)
    (deploy_repo / "app" / "evil.py").write_text("EVIL = 1\n")  # uncommitted
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", c2]) == 1

    assert "dirty" in capsys.readouterr().out
    doc = _artifact(deploy_repo)
    assert doc["dirty"] is True
    assert any("dirty" in r for r in doc["refusals"])
    assert not (deploy_repo / "deployed.marker").exists()


def test_deploy_ignores_an_uncommitted_forged_shard_regression(
        deploy_repo, monkeypatch):
    """A shard git has never seen must not
    satisfy the 'committed attestation' precondition — the store is read
    from the commit's tree, never the filesystem. (The forged file also
    dirties the tree; both refusals are honest, a pass is not.)"""
    c1 = _git(deploy_repo, "rev-parse", "HEAD")
    store = deploy_repo / ".warden" / "memory" / "attest"
    store.mkdir(parents=True)
    (store / "forged.json").write_text(_shard(c1))  # never git-added
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", c1]) == 1
    doc = _artifact(deploy_repo)
    assert doc["clean_attestation"] is None
    assert not (deploy_repo / "deployed.marker").exists()


def test_deploy_a_tag_shadowing_the_branch_does_not_bypass_reachability_regression(
        deploy_repo, monkeypatch, capsys):
    """Bare rev-parse resolves refs/tags/main BEFORE
    refs/heads/main, so a pushed tag named 'main' at the unmerged commit
    would flip the reachability answer. --branch resolves only in the
    branch namespaces."""
    _git(deploy_repo, "checkout", "-q", "-b", "side")
    (deploy_repo / "app" / "side.py").write_text("x = 1\n")
    _git(deploy_repo, "add", ".")
    _git(deploy_repo, "commit", "-q", "-m", "unmerged work")
    side = _git(deploy_repo, "rev-parse", "HEAD")
    _git(deploy_repo, "tag", "main", side)  # the shadowing tag
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", side]) == 1
    out = capsys.readouterr()
    assert "not reachable" in out.out + out.err
    doc = _artifact(deploy_repo)
    assert doc["reachable_from_branch"] is False
    tip = _git(deploy_repo, "rev-parse", "refs/heads/main")
    assert doc["branch_tip"] == tip, "the tag's sha must never become the tip"


def test_deploy_refuses_a_non_branch_ref_as_the_branch_regression(
        deploy_repo, monkeypatch, capsys):
    """Passing any refs/... argument straight through would let --branch
    refs/tags/v1.0 (exactly what $GITHUB_REF holds on a tag push) make a TAG
    the reachability target while help, wiki, and the refusal text all promise
    that is impossible. Only refs/heads/ and refs/remotes/ count; anything
    else under refs/ is exit 2."""
    _git(deploy_repo, "checkout", "-q", "-b", "side")
    (deploy_repo / "app" / "side.py").write_text("x = 1\n")
    _git(deploy_repo, "add", ".")
    _git(deploy_repo, "commit", "-q", "-m", "unmerged work")
    side = _git(deploy_repo, "rev-parse", "HEAD")
    _commit_shard(deploy_repo, side)  # even a shard-carrying tip must not pass
    tip = _git(deploy_repo, "rev-parse", "HEAD")
    _git(deploy_repo, "tag", "v1.0", tip)
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", tip,
                     "--branch", "refs/tags/v1.0"]) == 2
    assert "not a branch ref" in capsys.readouterr().err
    assert not (deploy_repo / "deployed.marker").exists()


def test_deploy_a_non_json_file_in_the_store_is_not_an_unreadable_shard_regression(
        deploy_repo, monkeypatch):
    """A diff-tree pathspec sweeping every path under the store would feed a
    committed .gitkeep to json.loads and report an UNREADABLE store (exit 2)
    where the determinate answer is 'no clean attestation' (exit 1) — the
    exact split the module promises to keep. Shards are *.json, the same
    membership attest.attested_shas globs."""
    store = deploy_repo / ".warden" / "memory" / "attest"
    store.mkdir(parents=True)
    (store / ".gitkeep").write_text("")
    _git(deploy_repo, "add", "-f", ".warden/memory/attest/.gitkeep")
    _git(deploy_repo, "commit", "-q", "-m", "create the store dir")
    head = _git(deploy_repo, "rev-parse", "HEAD")
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", head]) == 1
    doc = _artifact(deploy_repo)
    assert any("attestation" in r for r in doc["refusals"])


def test_deploy_reads_a_non_ascii_shard_filename_regression(
        deploy_repo, monkeypatch):
    """Git C-quotes non-ASCII paths in --name-only
    output unless -z is used, and the quoted string then fails `git show`.
    An honest deploy whose shard filename carries non-ASCII characters must
    pass, not exit 2 as an unreadable store."""
    c1 = _git(deploy_repo, "rev-parse", "HEAD")
    c2 = _commit_shard(deploy_repo, c1,
                       name="20260830T000000Z-café中-bbbbbbbb.json")
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", c2]) == 0
    doc = _artifact(deploy_repo)
    assert doc["clean_attestation"]["via"] == "introduced-by-commit"


def test_first_parent_raises_on_an_unresolvable_commit_regression(deploy_repo):
    """_first_parent must not read ANY git failure as 'root commit', which
    would diff the entire tree as introduced — an indeterminate state
    collapsed into a permissive one. A failure must
    raise (exit 2 at the CLI), and only a genuine root commit returns None."""
    from warden import deploy as deploy_mod
    root_commit = _git(deploy_repo, "rev-list", "--max-parents=0", "HEAD")
    assert deploy_mod._first_parent(deploy_repo, root_commit) is None
    with pytest.raises(deploy_mod.DeployError):
        deploy_mod._first_parent(deploy_repo, "deadbeef" * 5)


def test_deploy_halts_the_scope_at_the_first_failed_step_regression(
        deploy_repo, monkeypatch):
    """A deploy scope is an ordered, state-mutating pipeline —
    'flip traffic' must not run after 'run migrations' failed. The artifact
    records exactly the steps that ran (verify keeps run-everything)."""
    import yaml
    raw = yaml.safe_load((deploy_repo / "repo.yaml").read_text())
    raw["deploy"]["pipeline"] = [{"run": "false"},
                                 {"run": "touch traffic-flipped.marker"}]
    (deploy_repo / "repo.yaml").write_text(yaml.safe_dump(raw))
    _git(deploy_repo, "add", ".")
    _git(deploy_repo, "commit", "-q", "-m", "declare pipeline scope")
    c_cfg = _git(deploy_repo, "rev-parse", "HEAD")
    c_tip = _commit_shard(deploy_repo, c_cfg)
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "pipeline", "--commit", c_tip]) == 1
    assert not (deploy_repo / "traffic-flipped.marker").exists()
    doc = _artifact(deploy_repo)
    assert len(doc["run"]["results"]) == 1, "later steps must not be recorded as run"
    assert doc["run"]["results"][0]["exit_code"] != 0


def test_deploy_branch_resolves_a_remote_tracking_ref(deploy_repo, monkeypatch):
    """A pipeline pins --branch origin/main; refs/remotes/ is the second
    namespace tried."""
    c1 = _git(deploy_repo, "rev-parse", "HEAD")
    c2 = _commit_shard(deploy_repo, c1)
    _git(deploy_repo, "update-ref", "refs/remotes/origin/main", c2)
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", c2,
                     "--branch", "origin/main"]) == 0


# ---- fail closed: cannot-run is exit 2, never a pass -----------------------

def test_deploy_unresolvable_branch_exits_2_never_a_pass(
        deploy_repo, monkeypatch, capsys):
    monkeypatch.chdir(deploy_repo)
    assert cli.main(["deploy", "--scope", "staging",
                     "--branch", "no-such-branch"]) == 2
    assert "no-such-branch" in capsys.readouterr().err
    assert not (deploy_repo / "deployed.marker").exists()


def test_deploy_unresolvable_commit_exits_2_never_a_pass(
        deploy_repo, monkeypatch, capsys):
    monkeypatch.chdir(deploy_repo)
    assert cli.main(["deploy", "--scope", "staging",
                     "--commit", "deadbeef" * 5]) == 2
    assert capsys.readouterr().err
    assert not (deploy_repo / "deployed.marker").exists()


def test_deploy_unreadable_store_exits_2_when_nothing_else_matches(
        deploy_repo, monkeypatch, capsys):
    """'No attestation' and 'cannot read the store' are different states —
    the same distinction attest.check_range refuses to blur."""
    store = deploy_repo / ".warden" / "memory" / "attest"
    store.mkdir(parents=True)
    (store / "corrupt.json").write_text("{ not json")
    _git(deploy_repo, "add", ".")
    _git(deploy_repo, "commit", "-q", "-m", "corrupt shard")
    head = _git(deploy_repo, "rev-parse", "HEAD")
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", head]) == 2
    err = capsys.readouterr().err
    assert "corrupt.json" in err or "could not be read" in err
    assert not (deploy_repo / "deployed.marker").exists()


def test_deploy_unreadable_store_exits_2_even_when_already_refused_regression(
        deploy_repo, monkeypatch, capsys):
    """An unreadable store must not fold into a reachability refusal (exit 1,
    clean_attestation: null) — that tells a pipeline 'policy refusal' where
    the truth is 'infra cannot answer', and tells schema readers 'no
    attestation exists' where the truth is unknown. Indeterminate is ALWAYS
    exit 2."""
    _git(deploy_repo, "checkout", "-q", "-b", "side")
    store = deploy_repo / ".warden" / "memory" / "attest"
    store.mkdir(parents=True)
    (store / "corrupt.json").write_text("{ not json")
    _git(deploy_repo, "add", ".")
    _git(deploy_repo, "commit", "-q", "-m", "unmerged corrupt shard")
    side = _git(deploy_repo, "rev-parse", "HEAD")
    monkeypatch.chdir(deploy_repo)

    assert cli.main(["deploy", "--scope", "staging", "--commit", side]) == 2
    assert "could not be read" in capsys.readouterr().err
    assert not (deploy_repo / "deployed.marker").exists()


def test_deploy_unknown_scope_exits_2(deploy_repo, monkeypatch, capsys):
    monkeypatch.chdir(deploy_repo)
    assert cli.main(["deploy", "--scope", "no-such-scope"]) == 2
    assert "unknown deploy scope" in capsys.readouterr().err


def test_deploy_with_no_declared_scopes_exits_2(tmp_path, monkeypatch, capsys):
    """A repo that declares no deploy: block cannot run a deploy at all —
    that is 'cannot run' (2), never a refusal about a specific commit."""
    root = tmp_path / "nodeploy"
    root.mkdir()
    (root / "repo.yaml").write_text(REPO_YAML.split("deploy:")[0]
                                    + "review:\n  rules_dir: .warden/rules\n"
                                      "  blocking_severities: [HIGH]\n")
    rules = root / ".warden" / "rules"
    rules.mkdir(parents=True)
    (rules / "sample-rule.md").write_text(RULE)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    monkeypatch.chdir(root)

    assert cli.main(["deploy", "--scope", "staging"]) == 2
    assert "deploy" in capsys.readouterr().err
