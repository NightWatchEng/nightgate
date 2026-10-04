"""Memory v2 — gate-source ingestion and provenance.

The design's record schema always said `source: attest|gate` and carried
optional pr/bead. v1 shipped attest only. The load-bearing property here:
a gate finding is a deterministic checker FIRING — a fact, never a judgment
— so it may inform a reviewer but must never touch precision math.
"""

import json
from pathlib import Path


from conftest import seed_rules

from warden import memory as memory_mod


def write_gate_artifact(root: Path, stamp: str, findings: list[dict]) -> None:
    d = root / ".warden" / "out" / f"{stamp}-review"
    d.mkdir(parents=True)
    (d / "review-findings.json").write_text(json.dumps({
        "rules_version": "v1", "engine": "warden", "base_sha": "a" * 40,
        "head_sha": "b" * 40, "findings": findings,
        "deferred_to_pre_pr": [], "paused": []}))


def write_attest_artifact(root: Path, stamp: str, findings: list[dict],
                          **extra) -> None:
    d = root / ".warden" / "out" / f"{stamp}-attest"
    d.mkdir(parents=True)
    (d / "attestation.json").write_text(json.dumps({
        "head_sha": "c" * 40, "base_sha": "a" * 40, "rules_version": "v1",
        "reviewed_at": "2026-08-22T00:00:00+00:00",
        "reviewers": [{"role": "r", "agent": "a"}],
        "findings": findings, "verdict": "findings-open", **extra}))


GATE_FINDING = {"rule_id": "secrets-in-diff", "severity": "HIGH",
                "file": "src/a.py", "line": 3, "finding": "key",
                "evidence": "redacted"}
JUDGED = {"rule_id": "scope-creep", "severity": "MEDIUM", "file": "src/b.py",
          "line": 1, "finding": "f", "evidence": "e", "status": "confirmed"}

# ingest resolves every finding's rule_id against the declared ruleset — a
# gate artifact's ids are stamped from that ruleset too.
FIXTURE_RULE_IDS = ("secrets-in-diff", "scope-creep", "custom-rule")


def ingest(root: Path, **kwargs) -> dict:
    return memory_mod.ingest(
        root, rules_dir=seed_rules(root, *FIXTURE_RULE_IDS), **kwargs)


def test_gate_findings_ingest_as_detections(tmp_path):
    write_gate_artifact(tmp_path, "20260822T000000Z", [GATE_FINDING])
    result = ingest(tmp_path)
    assert result["total_records"] == 1
    rec = memory_mod.load_records(tmp_path)[0]
    assert rec["source"] == "gate"
    assert rec["status"] == "detected"


def test_gate_shards_live_beside_attest_shards(tmp_path):
    write_gate_artifact(tmp_path, "20260822T000000Z", [GATE_FINDING])
    write_attest_artifact(tmp_path, "20260822T000100Z", [JUDGED])
    ingest(tmp_path)
    assert list((memory_mod.memory_dir(tmp_path) / "gate").glob("*.json"))
    assert list((memory_mod.memory_dir(tmp_path) / "attest").glob("*.json"))
    assert ingest(tmp_path)["new_shards"] == []  # idempotent


def test_detections_never_feed_precision_or_promotion(tmp_path):
    # 20 deterministic hits of one rule must NOT make it promotable —
    # a rule that is already deterministic cannot be promoted to
    # deterministic, and counting hits as confirmations is tautology.
    write_gate_artifact(tmp_path, "20260822T000000Z",
                        [dict(GATE_FINDING, line=i) for i in range(20)])
    ingest(tmp_path)
    doc = memory_mod.stats(tmp_path)
    assert doc["promotable"] == []
    assert "secrets-in-diff" not in doc["per_rule"]
    assert doc["gate_detections"]["secrets-in-diff"] == 20
    rendered = memory_mod.render_stats(doc)
    assert "NOT precision" in rendered


def test_detections_never_trigger_a_pause_streak(tmp_path):
    write_gate_artifact(tmp_path, "20260822T000000Z",
                        [dict(GATE_FINDING, line=i) for i in range(5)])
    ingest(tmp_path)
    assert memory_mod.stats(tmp_path)["pause_candidates"] == []
    # Asserted at the arithmetic, not only at the pause list: a detection is
    # not a judgment, so the rule must not appear in the streak table AT ALL.
    # The pause list alone would hold whether or not that were true, since
    # one round is one step whatever it holds.
    streaks = memory_mod.refutation_streaks(memory_mod.load_records(tmp_path))
    assert "secrets-in-diff" not in streaks


def test_detections_are_reviewer_priors_not_examiner_precedents(tmp_path):
    write_gate_artifact(tmp_path, "20260822T000000Z", [GATE_FINDING])
    ingest(tmp_path)
    rev = memory_mod.recall(tmp_path, files=["src/other.py"], role="reviewer")
    exam = memory_mod.recall(tmp_path, files=["src/other.py"], role="examiner")
    assert len(rev) == 1, "a past deterministic hit is a legitimate prior"
    assert exam == [], "a detection is not case law"


def test_provenance_rides_onto_records(tmp_path):
    write_attest_artifact(tmp_path, "20260822T000000Z", [JUDGED],
                          pr="41", bead="agentops-yt0.19")
    ingest(tmp_path)
    rec = memory_mod.load_records(tmp_path)[0]
    assert rec["pr"] == "41" and rec["bead"] == "agentops-yt0.19"


def test_attest_build_stamps_provenance(sample_repo):
    from warden import attest as attest_mod
    doc = attest_mod.build(
        {"reviewers": [{"role": "r", "agent": "a"}], "findings": [],
         "verdict": "clean"},
        head_sha="b" * 40, base_sha="a" * 40, rules_version="v1",
        rules_dir=sample_repo / ".warden" / "rules", pr="41", bead="x-1")
    assert doc["pr"] == "41" and doc["bead"] == "x-1"


def test_gate_secrets_are_redacted_on_the_gate_path_too(tmp_path):
    planted = 'API_KEY = "sk-live_GATEPATH1234567890abc"'
    write_gate_artifact(tmp_path, "20260822T000000Z",
                        [dict(GATE_FINDING, rule_id="custom-rule",
                              evidence=planted)])
    ingest(tmp_path)
    corpus = "".join(p.read_text() for p in
                     (memory_mod.memory_dir(tmp_path) / "gate").glob("*.json"))
    assert "sk-live_GATEPATH" not in corpus


# ---------- the mutation proof must not feed the corpus ---------------------

def _git_repo(root: Path) -> str:
    """A real repo with one commit; returns that commit's sha."""
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                    capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (root / "real.py").write_text("x = 1\n")
    run("add", "-A")
    run("commit", "-qm", "real")
    return run("rev-parse", "HEAD").stdout.strip()


def _gate_artifact_for(root: Path, stamp: str, sha: str) -> None:
    d = root / ".warden" / "out" / f"{stamp}-review"
    d.mkdir(parents=True)
    (d / "review-findings.json").write_text(json.dumps({
        # NO "sha" key: a real review-findings.json carries base_sha and
        # head_sha only. A fixture writing both would hide a guard reading
        # the wrong field.
        "rules_version": "v1", "engine": "warden",
        "base_sha": "a" * 40, "head_sha": sha, "findings": [GATE_FINDING],
        "deferred_to_pre_pr": [], "paused": []}))


def test_gate_artifact_on_a_reverted_probe_is_not_ingested(tmp_path):
    """Validating a gate rule with a mutation proof must leave no corpus record.

    Planting patterns in a real file, running the gate, and reverting the
    commit must not leave gate records naming rules that never fired on real
    code, on a commit no longer in the branch. `.warden/memory/gate/` is
    untracked-but-not-ignored, so one `git add -A` would commit that synthetic
    defect history into the shared corpus.
    """
    import subprocess
    _git_repo(tmp_path)
    # The probe: a commit that fires the gate, then is reverted away.
    (tmp_path / "probe.py").write_text("secret = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "probe"], cwd=tmp_path, check=True,
                   capture_output=True)
    probe = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                           check=True, capture_output=True,
                           text=True).stdout.strip()
    _gate_artifact_for(tmp_path, "20260826T000000Z", probe)
    subprocess.run(["git", "reset", "--hard", "-q", "HEAD~1"], cwd=tmp_path,
                   check=True, capture_output=True)

    result = ingest(tmp_path)

    assert memory_mod.load_records(tmp_path) == [], (
        "a reverted probe's synthetic findings reached the corpus")
    assert not list((memory_mod.memory_dir(tmp_path) / "gate").glob("*.json"))
    assert result["unreachable"], "the skip was silent — nothing reported it"
    assert probe[:12] in result["unreachable"][0]


def test_gate_artifact_on_a_live_commit_still_ingests(tmp_path):
    # The guard must not swallow real detections: same shape, reachable sha.
    sha = _git_repo(tmp_path)
    _gate_artifact_for(tmp_path, "20260826T000000Z", sha)
    result = ingest(tmp_path)
    assert len(memory_mod.load_records(tmp_path)) == 1
    assert not result["unreachable"]


def test_reachability_guard_is_scoped_to_git_checkouts(tmp_path):
    # A consuming repo that is not a git checkout has no revert semantics.
    # Failing closed there would refuse every gate artifact rather than guard
    # anything — the guard must be inert, not maximally strict.
    assert not memory_mod.is_git_repo(tmp_path)
    _gate_artifact_for(tmp_path, "20260826T000000Z", "d" * 40)
    result = ingest(tmp_path)
    assert len(memory_mod.load_records(tmp_path)) == 1
    assert not result["unreachable"]


def test_unreachable_is_three_valued_and_fails_closed(tmp_path, monkeypatch):
    # "could not determine" must not read as "safe to ingest" — that is the
    # fail-open the repo's own fail-closed rule names. None => skipped.
    sha = _git_repo(tmp_path)
    _gate_artifact_for(tmp_path, "20260826T000000Z", sha)
    monkeypatch.setattr(memory_mod, "sha_reachable", lambda root, s: None)
    result = ingest(tmp_path)
    assert memory_mod.load_records(tmp_path) == []
    assert result["unreachable"]
    assert "could not be determined" in result["unreachable"][0]


def test_gate_fixture_matches_the_real_artifact_shape(tmp_path):
    """The fixture must not carry keys `warden review` never writes.

    A reachability guard reading `doc["sha"]`, which a real
    review-findings.json does not have, would fail closed on EVERY gate
    artifact and silently drop every legitimate detection, while unit tests
    over a fixture writing both `sha` and `head_sha` stayed green. A fixture
    richer than reality tests a system that does not exist.

    Asserted on the PRODUCED artifact, not the fixture's source text: a read
    of the source would match its own explanatory comment.
    """
    _gate_artifact_for(tmp_path, "20260826T000000Z", "e" * 40)
    doc = json.loads((tmp_path / ".warden" / "out" /
                      "20260826T000000Z-review" /
                      "review-findings.json").read_text())
    assert "sha" not in doc, (
        "fixture writes a `sha` key; a real review-findings.json carries "
        "base_sha/head_sha only, so a guard reading `sha` would look correct "
        "here and fail closed on every real artifact")
    assert {"base_sha", "head_sha"} <= set(doc)


def test_unreadable_repo_does_not_disable_the_guard(tmp_path, monkeypatch):
    """A repo git refuses to read must FAIL CLOSED, not go inert.

    `is_git_repo` must not collapse "not a checkout" and "there IS a checkout
    but git refused" into False — both are exit 128 (`not a git repository`
    vs `detected dubious ownership in repository at ...`). False sets
    guard_active=False, which hardcodes reachable=True for every gate
    artifact and leaves `unreachable` empty: a result byte-identical to a
    clean sweep, in the container / bind-mount case where dubious-ownership
    is the ORDINARY failure. A corrupted .git/config stands in for it here.
    """
    import subprocess
    _git_repo(tmp_path)
    (tmp_path / "probe.py").write_text("secret = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "probe"], cwd=tmp_path, check=True,
                   capture_output=True)
    probe = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                           check=True, capture_output=True,
                           text=True).stdout.strip()
    _gate_artifact_for(tmp_path, "20260826T000000Z", probe)
    subprocess.run(["git", "reset", "--hard", "-q", "HEAD~1"], cwd=tmp_path,
                   check=True, capture_output=True)

    # Corrupt the repo the way a container does: git errors, .git still there.
    (tmp_path / ".git" / "config").write_text("this is not valid ini\n[\n")
    assert memory_mod.is_git_repo(tmp_path) is None, \
        "an unreadable repo must be None (cannot look), never False (no repo)"

    result = ingest(tmp_path)
    assert memory_mod.load_records(tmp_path) == [], (
        "a repo git could not read silently ingested the synthetic record")
    assert result["unreachable"], "the guard went inert AND said nothing"


def test_absent_repo_is_distinguished_from_unreadable_one(tmp_path):
    # False is reserved for "no repo here" — the only inert case. If this
    # collapsed back into None, every non-git consumer would stop ingesting.
    assert memory_mod.is_git_repo(tmp_path) is False
    assert not memory_mod._dot_git_present(tmp_path)


def test_missing_git_binary_still_fails_closed_inside_a_repo(tmp_path,
                                                             monkeypatch):
    # The discriminator must not depend on git working: with no git binary,
    # a repo on disk is still a repo, and must not go inert.
    _git_repo(tmp_path)

    def _no_git(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(memory_mod.subprocess, "run", _no_git)
    assert memory_mod.is_git_repo(tmp_path) is None


def test_bare_repo_keeps_the_guard_active(tmp_path):
    """rc==0 with stdout 'false' must be None, not inert.

    Returning `False` on this branch must turn this test red; no other test
    covers it. A bare repo
    (or a cwd inside `.git/`) is not a work tree, but it HAS refs, and
    `git for-each-ref --contains` answers correctly there, so the guard must
    stay active rather than silently go inert.
    """
    import subprocess
    bare = tmp_path / "bare.git"
    bare.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "--bare"], cwd=bare,
                   check=True,
                   capture_output=True)
    proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                          cwd=bare, capture_output=True, text=True)
    assert proc.returncode == 0 and proc.stdout.strip() == "false", \
        "precondition: a bare repo answers rc=0 'false'"
    assert memory_mod.is_git_repo(bare) is None, \
        "a bare repo has refs — the guard must not go inert there"


def test_ingest_warns_when_a_gate_shard_is_committed(tmp_path):
    """The runtime half of R-11: caught when written, not only at certify.

    R-08 is enforced twice — a rung AND a warning from ingest
    with the `git rm --cached` recovery — because the rung fires at
    certification while the warning fires when the person can still act. The
    gate dir had neither; this is the second half.
    """
    import subprocess
    _git_repo(tmp_path)
    shard_dir = memory_mod.memory_dir(tmp_path) / "gate"
    shard_dir.mkdir(parents=True, exist_ok=True)
    (shard_dir / "committed.json").write_text('{"schema":1,"records":[]}')
    subprocess.run(["git", "add", "-f", "-A"], cwd=tmp_path, check=True,
                   capture_output=True)

    assert memory_mod._gate_dir_is_tracked(tmp_path) is True
    assert ingest(tmp_path)["gate_tracked"] is True


def test_ingest_does_not_warn_when_gate_shards_are_untracked(tmp_path):
    _git_repo(tmp_path)
    shard_dir = memory_mod.memory_dir(tmp_path) / "gate"
    shard_dir.mkdir(parents=True, exist_ok=True)
    (shard_dir / "local.json").write_text('{"schema":1,"records":[]}')
    assert memory_mod._gate_dir_is_tracked(tmp_path) is False
    assert ingest(tmp_path)["gate_tracked"] is False
