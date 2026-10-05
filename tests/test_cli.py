"""CLI exit-code contract."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from conftest import finishes_within
from warden import attest as attest_mod
from warden import cli
from warden import github as github_mod
from warden import review as review_mod
from warden.diffs import DiffContext


def test_explain_bad_ref_exits_2_regression(sample_repo, monkeypatch, capsys):
    """An infra failure (bad git ref) must exit 2, never 1 —
    exit 1 is reserved for blocking findings."""
    monkeypatch.chdir(sample_repo)
    assert cli.main(["explain", "--base", "no-such-ref-xyz"]) == 2
    assert "warden:" in capsys.readouterr().err


def test_verify_unknown_scope_exits_2(sample_repo, monkeypatch, capsys):
    monkeypatch.chdir(sample_repo)
    assert cli.main(["verify", "--scope", "no-such-scope"]) == 2
    assert "unknown verify scope" in capsys.readouterr().err


def test_autonomy_pause_exits_2_on_an_unreadable_corpus_regression(
        sample_repo, tmp_path, monkeypatch, capsys):
    """Fail-closed: a corrupt COMMITTED corpus must fail CLOSED
    — pause nothing and exit 2, never exit 0 (which reads 'nothing to pause')
    and never a raw traceback. certify guards the identical
    records_from_shards call this way; `warden autonomy pause` must too."""
    import yaml
    raw = yaml.safe_load((sample_repo / "repo.yaml").read_text())
    raw.pop("platform", None)  # no pin -> enforce_platform_pin is a no-op here
    (tmp_path / "repo.yaml").write_text(yaml.safe_dump(raw))
    shutil.copytree(sample_repo / ".warden" / "rules",
                    tmp_path / ".warden" / "rules")
    attest = tmp_path / ".warden" / "memory" / "attest"
    attest.mkdir(parents=True)
    (attest / "corrupt.json").write_text("{ not json")  # unreadable shard
    monkeypatch.chdir(tmp_path)

    assert cli.main(["autonomy", "pause"]) == 2
    assert "corpus unreadable" in capsys.readouterr().err
    # the run artifact still records WHY, rather than vanishing on the error
    results = list((tmp_path / ".warden" / "out").glob(
        "*-autonomy/autonomy-result.json"))
    assert results and json.loads(results[0].read_text())["error"]


def test_autonomy_pause_skips_an_unpausable_candidate_and_exits_1_regression(
        sample_repo, tmp_path, monkeypatch, capsys):
    """Atomicity: a pause candidate we cannot pause (here a
    rule with a 3-refutation streak in the corpus but no rule file) must be
    SKIPPED and recorded, not abort the run with an uncaught AutonomyError and
    no artifact. Exit 1 so a partial result never reads as 'all done'."""
    import yaml
    raw = yaml.safe_load((sample_repo / "repo.yaml").read_text())
    raw.pop("platform", None)
    (tmp_path / "repo.yaml").write_text(yaml.safe_dump(raw))
    rules_dir = tmp_path / ".warden" / "rules"
    rules_dir.mkdir(parents=True)
    # a valid ruleset (so stats does not fail-closed on an empty dir) that
    # nonetheless has no `ghost.md` — the streak rule cannot be paused.
    (rules_dir / "keeper.md").write_text(
        '---\nid: keeper\nseverity: MEDIUM\nengine: claude\n'
        'applies_to: ["**"]\n---\nbody\n')
    attest = tmp_path / ".warden" / "memory" / "attest"
    attest.mkdir(parents=True)
    # Three committed shards: three separate review ROUNDS, each refuting a
    # rule that has no file. One shard holding three refutations would be one
    # round's opinion and no streak at all.
    for i in range(3):
        (attest / f"s{i}.json").write_text(json.dumps({"records": [
            {"rule_id": "ghost", "status": "refuted", "ts": f"2026-08-2{i}",
             "seq": 0, "sha": f"{i:040x}", "file": "src/x.py", "line": i,
             "tags": []}]}))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["autonomy", "pause"]) == 1
    assert "skipped ghost" in capsys.readouterr().err
    result = json.loads(next((tmp_path / ".warden" / "out").glob(
        "*-autonomy/autonomy-result.json")).read_text())
    assert result["paused"] == []
    assert [s["rule_id"] for s in result["skipped"]] == ["ghost"]


def test_comment_failure_keeps_blocking_exit_1_regression(sample_repo, tmp_path,
                                                          monkeypatch, capsys):
    """A blocking verdict must stay exit 1 when only the
    comment post fails — never relabeled as 'gate did not run' (exit 2)."""
    shutil.copy(sample_repo / "repo.yaml", tmp_path / "repo.yaml")
    shutil.copytree(sample_repo / ".warden" / "rules", tmp_path / ".warden" / "rules")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({
        "repository": {"full_name": "o/r"},
        "pull_request": {"number": 1, "base": {"sha": "a" * 40},
                         "head": {"sha": "b" * 40}}}))
    monkeypatch.chdir(tmp_path)

    ctx_obj = DiffContext(base="a" * 40, head="b" * 40, files=("app/x.py",),
                          added={"app/x.py": ((1, "bad"),)}, removed={},
                          read_base=lambda p: None, read_head=lambda p: None)
    finding = {"rule_id": "secrets-in-diff", "severity": "HIGH",
               "file": "app/x.py", "line": 1, "finding": "key", "evidence": "+bad"}
    doc = {"rules_version": "v", "engine": "deterministic", "base_sha": ctx_obj.base,
           "head_sha": ctx_obj.head, "findings": [finding],
           "deferred_to_pre_pr": []}
    monkeypatch.setattr(cli.diffs_mod, "get_context", lambda *a, **k: ctx_obj)
    monkeypatch.setattr(review_mod, "run_review", lambda *a, **k: doc)

    def boom(ctx, body, root):
        raise github_mod.GitHubError("post failed")
    monkeypatch.setattr(github_mod, "upsert_sticky_comment", boom)

    assert cli.main(["review", "--event", str(event)]) == 1
    err = capsys.readouterr().err
    assert "verdict stands" in err

    # the same failure on a CLEAN review has no durable audit record -> exit 2
    monkeypatch.setattr(review_mod, "run_review",
                        lambda *a, **k: dict(doc, findings=[]))
    assert cli.main(["review", "--event", str(event)]) == 2


def _review_with_a_high_finding(sample_repo: Path, tmp_path: Path,
                                monkeypatch: pytest.MonkeyPatch,
                                deferred: tuple[str, ...] = ()
                                ) -> tuple[Path, list[str]]:
    """A review fixture whose diff carries one HIGH `secrets-in-diff` finding,
    driven through the real `review` command with an event payload."""
    shutil.copy(sample_repo / "repo.yaml", tmp_path / "repo.yaml")
    shutil.copytree(sample_repo / ".warden" / "rules", tmp_path / ".warden" / "rules")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({
        "repository": {"full_name": "o/r"},
        "pull_request": {"number": 2, "base": {"sha": "a" * 40},
                         "head": {"sha": "b" * 40}}}))
    monkeypatch.chdir(tmp_path)
    ctx_obj = DiffContext(base="a" * 40, head="b" * 40, files=("app/x.py",),
                          added={"app/x.py": ((7, "bad"),)}, removed={},
                          read_base=lambda p: None, read_head=lambda p: None)
    finding = {"rule_id": "secrets-in-diff", "severity": "HIGH",
               "file": "app/x.py", "line": 7, "finding": "AWS key in diff",
               "evidence": "+AKIA-shaped-token"}
    doc = {"rules_version": "v", "engine": "deterministic", "base_sha": ctx_obj.base,
           "head_sha": ctx_obj.head, "findings": [finding],
           "deferred_to_pre_pr": list(deferred)}
    monkeypatch.setattr(cli.diffs_mod, "get_context", lambda *a, **k: ctx_obj)
    monkeypatch.setattr(review_mod, "run_review", lambda *a, **k: doc)
    posted: list[str] = []
    monkeypatch.setattr(github_mod, "upsert_sticky_comment",
                        lambda ctx, body, root: posted.append(body))
    return event, posted


def test_review_writes_its_findings_to_the_step_summary_when_the_variable_is_set(
        sample_repo, tmp_path, monkeypatch):
    """The demo's PR #2: a HIGH finding reached only the sticky comment, so a
    reader of the Actions run saw verify's refusal and not the review's. With
    `$GITHUB_STEP_SUMMARY` set, the findings table is appended there too — and
    the comment is byte-for-byte the one posted with the variable unset."""
    event, posted = _review_with_a_high_finding(sample_repo, tmp_path, monkeypatch)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert cli.main(["review", "--event", str(event)]) == 1
    comment_without = posted[-1]

    summary = tmp_path / "step-summary.md"
    summary.write_text("## verify\n\nearlier step\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cli.main(["review", "--event", str(event)]) == 1
    assert posted[-1] == comment_without, "the sticky comment changed"

    text = summary.read_text()
    assert text.startswith("## verify\n\nearlier step\n"), (
        "the summary was overwritten, not appended to")
    assert "| HIGH | `secrets-in-diff` | `app/x.py:7` | AWS key in diff |" in text
    assert "1 blocking (HIGH)" in text
    # The table, not the evidence quotes the comment folds away.
    assert "AKIA-shaped-token" not in text
    assert "AKIA-shaped-token" in comment_without


def test_review_writes_no_step_summary_when_the_variable_is_unset(
        sample_repo, tmp_path, monkeypatch):
    event, _ = _review_with_a_high_finding(sample_repo, tmp_path, monkeypatch)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    before = sorted(p.name for p in tmp_path.iterdir())
    assert cli.main(["review", "--event", str(event)]) == 1
    after = sorted(p.name for p in tmp_path.iterdir())
    assert after == before


def test_the_step_summary_names_the_rules_ci_deferred(
        sample_repo, tmp_path, monkeypatch):
    """CI evaluates no `engine: claude` rule; it defers them. A summary that
    printed the table alone would read "all applicable rules passed" over
    rules that never ran, so the deferred line goes with it."""
    event, _ = _review_with_a_high_finding(
        sample_repo, tmp_path, monkeypatch, deferred=("enforcement-truth",))
    summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cli.main(["review", "--event", str(event)]) == 1
    assert ("Deferred to pre-PR review (Claude Code session): "
            "`enforcement-truth`") in summary.read_text()


def test_the_suite_does_not_inherit_the_runners_step_summary():
    """CI sets GITHUB_STEP_SUMMARY for the job running this suite. Inherited,
    every fixture review that sets nothing itself would append its findings
    to the real summary; the session fixture removes it, as it removes
    GITHUB_EVENT_PATH."""
    import os
    assert "GITHUB_STEP_SUMMARY" not in os.environ


def test_a_step_summary_that_cannot_be_written_leaves_the_verdict(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The summary is a mirror; the artifact and comment are the record. An
    unwritable path is named on stderr and the blocking exit 1 stands."""
    event, posted = _review_with_a_high_finding(sample_repo, tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "no" / "such" / "f"))
    assert cli.main(["review", "--event", str(event)]) == 1
    assert posted, "the sticky comment was not posted"
    err = capsys.readouterr().err
    assert "step summary not written" in err and "no/such/f" in err


def _clone_fixture(sample_repo, tmp_path):
    """A committed, clean copy of the fixture repo — `attest write` refuses a
    dirty tree, and writing evidence must not dirty the session fixture."""
    root = tmp_path / "repo"
    shutil.copytree(sample_repo, root, ignore=shutil.ignore_patterns(".git"))
    # evidence run dirs are gitignored in a real enrolled repo; without this
    # the first `attest write` would dirty the tree and the next one exits 2
    (root / ".gitignore").write_text(".warden/out/\n")
    # Tracked at the branch point and never touched by the branch: the shape
    # that separates a three-dot range from `git ls-files`.
    (root / "app").mkdir(exist_ok=True)
    (root / "app" / "y.py").write_text("y = 1\n")
    # Identity in the repo CONFIG, not just per-command `-c`: tests that build
    # further commits on this fixture call plain `git commit`, and CI runners
    # carry no global git identity — `git commit` there exits 128 while a
    # developer machine, which has one, passes.
    for args in (("init", "-q", "-b", "main"),
                 ("config", "user.email", "t@t"),
                 ("config", "user.name", "t"), ("add", "."),
                 ("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    # A real range on a branch off main. Without it `main...HEAD` is empty, so
    # the range-warning tests would exercise the EMPTY-RANGE warning rather
    # than the out-of-range one they are about — `attest write` does not
    # refuse either (it warns), so the failure would be a wrong-signal one,
    # not a refusal.
    (root / "app").mkdir(exist_ok=True)
    (root / "app" / "x.py").write_text("x = 1\n")
    for args in (("checkout", "-q", "-b", "work"), ("add", "app/x.py"),
                 ("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "-m", "touch app/x.py")):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def _latest_attest_manifest(root) -> dict:
    """The newest `attest` run manifest, by cmd then RUN DIR NAME.

    Not by the manifest's `finished` stamp: write_manifest records it with
    `timespec="seconds"`, so two attest writes in the same second carry an
    IDENTICAL stamp and `max` returns the first — the earlier run. A test
    reading it that way passes only when the two calls happen to straddle a
    second boundary, which is ~1 run in 4 (unmapped:wall-clock-dependence).
    `create_run_dir` names
    dirs with microsecond precision, so the dir name orders them unambiguously
    while the cmd filter still keeps other commands' runs out.
    """
    found = [(m.parent.name, json.loads(m.read_text()))
             for m in (root / ".warden" / "out").rglob("manifest.json")]
    attests = [(name, doc) for name, doc in found if doc.get("cmd") == "attest"]
    assert attests, f"no attest run among {[d.get('cmd') for _, d in found]}"
    return max(attests, key=lambda pair: pair[0])[1]


def _findings_file(root, rule_id):
    path = root.parent / "findings.json"
    path.write_text(json.dumps({
        "reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
        "findings": [{"rule_id": rule_id, "severity": "LOW", "file": "app/x.py",
                      "line": 1, "finding": "f", "evidence": "e",
                      "status": "fixed"}],
        "verdict": "clean"}))
    return path


def test_attest_write_rejects_undeclared_rule_id(sample_repo, tmp_path,
                                                      monkeypatch, capsys):
    """The CLI must hand attest the repo's own rules_dir — otherwise the
    invariant exists in the library and never runs on the real path."""
    root = _clone_fixture(sample_repo, tmp_path)
    findings = _findings_file(root, "docs-drift")
    monkeypatch.chdir(root)
    assert cli.main(["attest", "write", "--findings", str(findings)]) == 2
    err = capsys.readouterr().err
    assert "names no declared rule" in err and "unmapped:" in err


def test_attest_write_accepts_declared_and_unmapped_ids(sample_repo, tmp_path,
                                                             monkeypatch):
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)
    for rule_id in ("scope-creep", "unmapped:docs-drift"):
        findings = _findings_file(root, rule_id)
        assert cli.main(["attest", "write", "--findings", str(findings)]) == 0


class TestAttestCheckExitCodes:
    """`attest check` is a CI gate, so its exit codes carry
    the contract — 0 clean, 1 blocking finding, 2 the gate did not run."""

    @staticmethod
    def _git(root, *args):
        return subprocess.run(["git", *args], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()

    @classmethod
    def _commit(cls, root, name):
        (root / name).write_text("x")
        cls._git(root, "add", name)
        cls._git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", f"add {name}")
        return cls._git(root, "rev-parse", "HEAD")

    @classmethod
    def _commit_shard(cls, root, message="ops(memory): commit the shard"):
        """The ingest commit, faithfully: the shard and NOTHING else.

        What a real one carries. `attest write`'s own artifact lands in the
        gitignored `.warden/out/`, so `memory ingest` + `git add -A` + commit
        stages one new file, the shard. The earlier fixtures here committed a
        marker file beside it, which under the coverage rule
        is a content change landing after the round that attested the branch —
        refused, and rightly. Measured on this repo's own history: 23 of the
        39 merged PRs in pr/230..pr/269 have exactly one commit after their
        last attested one, and it touches exactly the shard.
        """
        cls._git(root, "add", "-A")
        cls._git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", message)
        return cls._git(root, "rev-parse", "HEAD")

    @classmethod
    def _shard(cls, root, sha, *, verdict=None, name=None):
        """`verdict=None` omits the field, which is what shards committed
        before `verdict` shipped look like — a state that must keep answering
        the check. `name` writes a
        second shard for one commit, which the store really holds."""
        doc = {"schema": 1, "source": "attest", "sha": sha,
               "base_sha": "0" * 40, "rules_version": "v1",
               "reviewed_at": "2026-08-23T00:00:00+00:00", "records": []}
        if verdict is not None:
            doc["verdict"] = verdict
        shard_dir = root / attest_mod.SHARD_DIR
        shard_dir.mkdir(parents=True, exist_ok=True)
        path = shard_dir / (name or f"{sha[:8]}.json")
        path.write_text(json.dumps(doc))
        return path

    def _branch(self, sample_repo, tmp_path):
        root = _clone_fixture(sample_repo, tmp_path)
        self._git(root, "switch", "-q", "-c", "feature")
        return root, self._commit(root, "feature.py")

    def test_no_attestation_exits_1(self, sample_repo, tmp_path,
                                         monkeypatch, capsys):
        root, reviewed = self._branch(sample_repo, tmp_path)
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
        monkeypatch.chdir(root)
        assert cli.main(["attest", "check", "--base", "main"]) == 1
        out = capsys.readouterr()
        assert "NO ATTESTATION" in out.out
        assert "no committed attestation" in out.err

    def test_committed_shard_exits_0(self, sample_repo, tmp_path,
                                          monkeypatch, capsys):
        root, reviewed = self._branch(sample_repo, tmp_path)
        self._shard(root, reviewed)
        self._commit_shard(root)   # committing the shard moves head
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
        monkeypatch.chdir(root)
        assert cli.main(["attest", "check", "--base", "main"]) == 0
        assert "PASS" in capsys.readouterr().out

    def test_indeterminate_exits_2_never_1(self, sample_repo, tmp_path,
                                                monkeypatch, capsys):
        """A bad base ref means the check DID NOT RUN. Exit 1 would claim the
        PR has no attestation — a different, unproven statement."""
        root, reviewed = self._branch(sample_repo, tmp_path)
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
        monkeypatch.chdir(root)
        assert cli.main(["attest", "check", "--base", "no-such-ref-xyz"]) == 2
        assert "did NOT run" in capsys.readouterr().err

    def test_head_is_the_pr_head_not_the_merge_ref(self, sample_repo, tmp_path,
                                                        monkeypatch, capsys):
        """The merge-ref trap: on a pull_request event
        `git rev-parse HEAD` is the merge commit actions/checkout built. The
        check must report the commit CI reports about."""
        root, reviewed = self._branch(sample_repo, tmp_path)
        self._shard(root, reviewed)
        pr_head = self._commit_shard(root)
        # what actions/checkout leaves behind on a pull_request event
        self._git(root, "checkout", "-q", "--detach", "main")
        self._git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                  "merge", "-q", "--no-ff", "-m", "merge", "feature")
        merge_ref = self._git(root, "rev-parse", "HEAD")
        assert merge_ref != pr_head

        event = tmp_path / "event.json"
        event.write_text(json.dumps({"pull_request": {"head": {"sha": pr_head}}}))
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
        monkeypatch.chdir(root)
        assert cli.main(["attest", "check", "--base", "main"]) == 0
        out = capsys.readouterr().out
        assert pr_head[:12] in out and merge_ref[:12] not in out


class TestAgentopsYnmTipAttestationsMustBeClean:
    """`attest check` reads `verdict`, not only range MEMBERSHIP: otherwise a
    branch ABANDONED after round 1 — review never finished, findings never
    fixed — passes the required gate on that round's committed
    `findings-open` shard. Per-round attestation commits such a shard BY
    DESIGN, so the hole would be open on every branch, and the same gate
    ships to consumers through `examples/hello-svc`'s workflow.

    Both shapes are here on purpose, because the rule is about the TIP
    attestations — the matched shards no other matched shard descends from —
    and not about every shard: an early `findings-open` shard followed by a
    later clean one is the honest multi-round branch this repo's own protocol
    produces, and a check that refused it would be a worse defect than the
    one being closed.
    """

    _H = TestAttestCheckExitCodes   # the git/shard helpers, not re-collected

    def _branch(self, sample_repo, tmp_path, monkeypatch):
        root = _clone_fixture(sample_repo, tmp_path)
        self._H._git(root, "switch", "-q", "-c", "feature")
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
        monkeypatch.chdir(root)
        return root

    def test_a_findings_open_shard_alone_fails_the_check(
            self, sample_repo, tmp_path, monkeypatch, capsys):
        """Shape 1 — the abandoned branch. One round, findings open, shard
        committed, nothing since. The gate must go red and NAME the verdict."""
        root = self._branch(sample_repo, tmp_path, monkeypatch)
        reviewed = self._H._commit(root, "feature.py")
        self._H._shard(root, reviewed, verdict="findings-open")
        self._H._commit_shard(root)   # committing it moves head

        assert cli.main(["attest", "check", "--base", "main"]) == 1
        out = capsys.readouterr()
        assert "findings-open" in out.out and "findings-open" in out.err
        assert "PASS" not in out.out

    def test_a_later_clean_shard_carries_the_branch(
            self, sample_repo, tmp_path, monkeypatch, capsys):
        """Shape 2 — the honest multi-round branch. Round 1 opened findings
        and its shard is committed; the fix landed; round 2 attests the new
        head CLEAN. The earlier `findings-open` shard is still in range and
        must NOT fail the gate."""
        root = self._branch(sample_repo, tmp_path, monkeypatch)
        round1 = self._H._commit(root, "feature.py")
        self._H._shard(root, round1, verdict="findings-open")
        self._H._commit_shard(root, "ops(memory): round 1")
        round2 = self._H._commit(root, "fix-the-finding.py")
        self._H._shard(root, round2, verdict="clean")
        self._H._commit_shard(root, "ops(memory): round 2")

        assert cli.main(["attest", "check", "--base", "main"]) == 0, (
            "an early round's findings-open shard is expected on a "
            "multi-round branch; only the LAST round has to be clean")
        assert "PASS" in capsys.readouterr().out

    def test_a_pre_verdict_shard_still_answers_the_check(
            self, sample_repo, tmp_path, monkeypatch, capsys):
        """A shard written before `verdict` shipped records no verdict, and
        old evidence does not become illegal. The
        check says so out loud instead of guessing a verdict either way."""
        root = self._branch(sample_repo, tmp_path, monkeypatch)
        reviewed = self._H._commit(root, "feature.py")
        self._H._shard(root, reviewed)               # no verdict field at all
        self._H._commit_shard(root)

        assert cli.main(["attest", "check", "--base", "main"]) == 0
        out = capsys.readouterr().out
        assert "PASS" in out and "no `verdict`" in out

    def test_a_merged_sibling_line_cannot_carry_the_verdict(
            self, sample_repo, tmp_path, monkeypatch, capsys):
        """Every tip is bound, not `matches[0]`. Reading the verdict off
        `matches[0]` under `git rev-list --topo-order` as "the newest by
        ancestry" is wrong: topo-order only LINEARISES a partial order, and on
        a merged range git emits one parent's whole line first, so a sibling
        line's `clean` shard would answer for a branch whose own last round
        left findings open — the abandoned-branch hole reopened for every
        stacked branch `orchestrate` produces."""
        root = self._branch(sample_repo, tmp_path, monkeypatch)
        # the sibling line, reviewed CLEAN, off main
        self._H._git(root, "switch", "-q", "-c", "sibling", "main")
        sibling = self._H._commit(root, "sibling.py")
        self._H._shard(root, sibling, verdict="clean")
        self._H._git(root, "add", "-A")
        self._H._commit(root, "ingest-sibling.txt")
        # this branch's own line, whose last round left findings OPEN
        self._H._git(root, "switch", "-q", "feature")
        mine = self._H._commit(root, "feature.py")
        self._H._shard(root, mine, verdict="findings-open")
        self._H._git(root, "add", "-A")
        self._H._commit(root, "ingest-mine.txt")
        self._H._git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                     "merge", "-q", "--no-ff", "-m", "merge sibling", "sibling")

        assert cli.main(["attest", "check", "--base", "main"]) == 1, (
            "a sibling line's clean shard answered for this branch's own "
            "unclosed review")
        out = capsys.readouterr()
        assert "findings-open" in out.out and "findings-open" in out.err
        assert mine[:12] in out.out, "the open verdict must name its own commit"

    def test_a_merge_of_two_closed_lines_is_not_refused_on_the_verdict(
            self, sample_repo, tmp_path, monkeypatch, capsys):
        """The other side of binding every tip, and the one that would hurt
        more if it broke: binding EVERY tip must not refuse a stacked branch
        whose lines both closed clean.

        It does not, and that is still this test's subject. What refuses this
        range now is a DIFFERENT rule with a different remedy — the coverage
        rule: the merge commit is under no tip's ancestry and
        brings `sibling.py` onto this head, which no round has read HERE. The
        two rules' remedies happen to be the same command, and
        `test_re_reviewing_the_merged_head_collapses_the_tips` runs it.
        """
        root = self._branch(sample_repo, tmp_path, monkeypatch)
        self._H._git(root, "switch", "-q", "-c", "sibling", "main")
        sibling = self._H._commit(root, "sibling.py")
        self._H._shard(root, sibling, verdict="clean")
        self._H._commit_shard(root, "ops(memory): sibling round")
        self._H._git(root, "switch", "-q", "feature")
        mine = self._H._commit(root, "feature.py")
        self._H._shard(root, mine, verdict="clean")
        self._H._commit_shard(root, "ops(memory): my round")
        self._H._git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                     "merge", "-q", "--no-ff", "-m", "merge sibling", "sibling")

        assert cli.main(["attest", "check", "--base", "main"]) == 1
        out = capsys.readouterr().out
        assert "FINDINGS OPEN" not in out, (
            "two tips that both closed clean were refused ON THE VERDICT — "
            "binding every tip must not refuse the honest stacked branch")
        assert "UNREVIEWED COMMITS" in out and "sibling.py" in out, (
            "the merge brought content onto this head that no round read "
            "here, and the check did not say so")

    def test_the_named_shard_is_the_one_the_verdict_came_from(
            self, sample_repo, tmp_path, monkeypatch, capsys):
        """With two shards for one commit, the path and the verdict come from
        the same shard. Path first-wins and verdict last-wins would print one
        shard's path beside the other shard's verdict — an evidence line
        pointing at a file that contradicts it. Both are last-wins: the later
        attestation supersedes, and it supersedes in ONE piece."""
        root = self._branch(sample_repo, tmp_path, monkeypatch)
        reviewed = self._H._commit(root, "feature.py")
        self._H._shard(root, reviewed, verdict="findings-open",
                       name="20260101T000000Z-early.json")
        later = self._H._shard(root, reviewed, verdict="clean",
                               name="20260102T000000Z-later.json")
        self._H._commit_shard(root)

        assert cli.main(["attest", "check", "--base", "main"]) == 0, (
            "the later attestation of this commit closed clean")
        out = capsys.readouterr().out
        assert later.name in out, "the check named a shard it did not read"
        assert "findings-open" not in out

    def test_the_patch_id_join_reads_the_verdict_too(
            self, sample_repo, tmp_path, monkeypatch, capsys):
        """The verdict rule spans two joins, and this covers the patch-id one:
        without it `shard_verdict = None` on that arm would leave the suite
        green while an abandoned branch passes the moment it is rebased —
        which branch protection makes the normal path."""
        root = _clone_fixture(sample_repo, tmp_path)
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
        monkeypatch.chdir(root)

        def git(*args):
            return subprocess.run(["git", *args], cwd=root, capture_output=True,
                                  text=True, check=True).stdout.strip()

        git("checkout", "-qb", "feat")
        (root / "app" / "x.py").write_text("x = 2\n")
        git("add", "-A")
        git("commit", "-qm", "the reviewed change")
        findings = tmp_path / "open.json"
        findings.write_text(json.dumps(
            {"reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
             "findings": [{"rule_id": "unmapped:docs-drift", "severity": "LOW",
                           "file": "app/x.py", "line": 1, "finding": "f",
                           "evidence": "e", "status": "confirmed"}],
             "verdict": "findings-open"}))
        assert cli.main(["attest", "write", "--findings", str(findings),
                         "--base", "main"]) == 1   # findings-open exits 1
        assert cli.main(["memory", "ingest"]) == 0
        git("add", "-A")
        git("commit", "-qm", "ops(memory): commit the shard")
        assert cli.main(["attest", "check", "--base", "main"]) == 1, \
            "the sha join must already refuse this — fixture is wrong"
        capsys.readouterr()

        git("checkout", "-q", "main")
        git("commit", "-q", "--allow-empty", "-m", "main moves")
        git("checkout", "-q", "feat")
        git("rebase", "-q", "main")     # every sha in the range is rewritten
        assert cli.main(["attest", "check", "--base", "main"]) == 1, (
            "a rebase turned an unclosed review into a passing gate — the "
            "patch-id join must read the verdict the sha join reads")
        assert "findings-open" in capsys.readouterr().out

    def test_the_accepted_verdict_is_the_schema_s(self):
        """The gate compares against a value the shipped contract declares;
        a vocabulary change must not leave it matching on a dead string."""
        assert attest_mod.CLEAN_VERDICT in attest_mod.VERDICTS


def _attest_artifact(root, rule_id, head_sha=None):
    """An attestation artifact warden did NOT write — hand-edited, or emitted
    by a platform version predating the rule_id invariant.

    `head_sha` defaults to the repo's real HEAD: ingest refuses to mint a
    shard for an attestation naming a commit no ref contains, and a
    fabricated sha is exactly that. Fixtures that want the orphan path pass
    one explicitly — the realistic default keeps every other test testing
    what it says it tests.
    """
    if head_sha is None:
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root,
            capture_output=True, text=True, check=True).stdout.strip()
    out = root / ".warden" / "out" / "20260824T010000Z-attest"
    out.mkdir(parents=True, exist_ok=True)
    (out / "attestation.json").write_text(json.dumps({
        "head_sha": head_sha, "base_sha": "a" * 40, "rules_version": "v1",
        "reviewed_at": "2026-08-24T01:00:00+00:00",
        "reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
        "verdict": "findings-open",
        "findings": [{"rule_id": rule_id, "severity": "LOW", "file": "app/x.py",
                      "line": 1, "finding": "f", "evidence": "e",
                      "status": "confirmed"}]}))


def test_memory_ingest_rejects_undeclared_rule_id(sample_repo, tmp_path,
                                                       monkeypatch, capsys):
    """The CLI must hand ingest the repo's own rules_dir, and report the
    rejection as its fourth warning class — an artifact `attest write` would
    refuse must not enter the corpus through the reading end of the pipe."""
    root = _clone_fixture(sample_repo, tmp_path)
    _attest_artifact(root, "general")
    monkeypatch.chdir(root)
    assert cli.main(["memory", "ingest"]) == 0
    err = capsys.readouterr().err
    assert "REJECTED artifact" in err and "names no declared rule" in err
    from warden import memory as memory_mod
    assert memory_mod.load_records(root) == []


def test_memory_ingest_accepts_declared_and_unmapped_ids(sample_repo, tmp_path,
                                                              monkeypatch, capsys):
    root = _clone_fixture(sample_repo, tmp_path)
    _attest_artifact(root, "unmapped:docs-drift")
    monkeypatch.chdir(root)
    assert cli.main(["memory", "ingest"]) == 0
    assert "REJECTED artifact" not in capsys.readouterr().err
    from warden import memory as memory_mod
    assert [r["rule_id"] for r in memory_mod.load_records(root)] == \
        ["unmapped:docs-drift"]


def test_ingest_refuses_an_attestation_no_ref_contains(sample_repo, tmp_path,
                                                           monkeypatch, capsys):
    """Half A. An attestation naming a commit no ref contains is
    an orphan: its branch was squash-merged or abandoned, so nothing resolves
    the sha the record points at. Minting a shard for it resurrects evidence
    for code that is not in the tree — and it comes back on every ingest,
    dirtying a clean tree until someone deletes it by hand.

    The direction is the OPPOSITE of the gate guard's, deliberately: gate
    skips on "could not determine" too, because a synthetic detection is worse
    than a lost one. An attestation records a judgment no re-run reproduces,
    so this skips only on a definite False (asserted by the sibling below).
    """
    root = _clone_fixture(sample_repo, tmp_path)
    _attest_artifact(root, "unmapped:docs-drift", head_sha="c" * 40)
    monkeypatch.chdir(root)
    assert cli.main(["memory", "ingest"]) == 0
    err = capsys.readouterr().err
    assert "no ref contains" in err, err
    from warden import memory as memory_mod
    assert memory_mod.load_records(root) == [], (
        "an orphan attestation reached the corpus")
    shards = list((root / ".warden" / "memory" / "attest").glob("*.json"))
    assert not shards, f"an orphan attestation was sharded: {shards}"


def test_ingest_keeps_an_attestation_it_cannot_place(sample_repo, tmp_path,
                                                         monkeypatch, capsys):
    """The other half of the asymmetry, which is the part worth pinning: when
    git cannot say whether the commit is reachable, the attestation is
    INGESTED and the doubt reported. Losing a review round is the worse error,
    and this is the direction a careless fix breaks first — the gate guard one
    function over fails closed on exactly this input.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    _attest_artifact(root, "unmapped:docs-drift")
    monkeypatch.chdir(root)
    monkeypatch.setattr("warden.memory.sha_reachable", lambda root, sha: None)
    assert cli.main(["memory", "ingest"]) == 0
    from warden import memory as memory_mod
    assert [r["rule_id"] for r in memory_mod.load_records(root)] == \
        ["unmapped:docs-drift"], "a review round was dropped on 'do not know'"
    assert "could not determine" in capsys.readouterr().err


def test_ingest_does_not_re_mint_a_shard_filed_on_another_branch(
        sample_repo, tmp_path, monkeypatch, capsys):
    """Half B — the case a reachability guard cannot reach.

    `_event_already_filed` asks the SHARD STORE, which is committed and so
    branch-scoped, while `.warden/out/` is gitignored and branch-independent.
    Switch branches and the store's answer flips to "no", so ingest re-mints
    every run dir whose shard lives on another branch. The commit is perfectly
    reachable, so half A sails past it.

    This is a bug and not an annoyance: a shard for ANOTHER PR's review
    round appears staged-ready in an unrelated branch's tree, where
    `git add -A` files it into that PR's range and `attest check` credits the
    PR with a review it never ran.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    _attest_artifact(root, "unmapped:docs-drift")
    monkeypatch.chdir(root)

    assert cli.main(["memory", "ingest"]) == 0
    store = root / ".warden" / "memory" / "attest"
    filed = list(store.glob("*.json"))
    assert len(filed) == 1, f"expected one shard, got {filed}"

    # The other branch: same run dir on disk, that shard not in this store.
    filed[0].unlink()
    capsys.readouterr()
    assert cli.main(["memory", "ingest"]) == 0
    err = capsys.readouterr().err

    assert not list(store.glob("*.json")), (
        "ingest re-minted a shard already filed elsewhere — on a real branch "
        "that is another PR's review evidence appearing in this PR's tree")
    assert "not re-filed" in err and filed[0].name in err, err
    assert "delete" in err, "the report must name the way back for a real loss"


def test_the_consumed_marker_can_be_cleared_to_re_file(sample_repo, tmp_path,
                                                           monkeypatch, capsys):
    """The escape hatch the refusal message promises must actually work: a
    shard genuinely lost before it was committed has to be re-filable, or the
    guard turns a recoverable mistake into permanent corpus loss."""
    root = _clone_fixture(sample_repo, tmp_path)
    _attest_artifact(root, "unmapped:docs-drift")
    monkeypatch.chdir(root)
    assert cli.main(["memory", "ingest"]) == 0
    store = root / ".warden" / "memory" / "attest"
    list(store.glob("*.json"))[0].unlink()

    from warden import memory as memory_mod
    run_dir = root / ".warden" / "out" / "20260824T010000Z-attest"
    marker = memory_mod._consumed_path(root, run_dir)
    assert marker.is_file(), "no marker was written"
    assert marker.parent != run_dir, (
        "the marker was written INSIDE the sealed run dir, whose manifest "
        "records its file list at seal time")
    marker.unlink()

    assert cli.main(["memory", "ingest"]) == 0
    assert len(list(store.glob("*.json"))) == 1, (
        "clearing the marker did not re-file the shard, so the remedy the "
        "refusal message names does not work")


def test_an_already_filed_event_is_never_reported_as_unreachable(
        sample_repo, tmp_path, monkeypatch, capsys):
    """Identity first, reachability only for an event this store does not
    already hold.

    With the reachability guard first, a run dir whose event is ALREADY a
    committed shard is reported as skipped-unreachable, and the message tells
    the reader to re-run, which would file a SECOND shard for a review round
    already on record.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    _attest_artifact(root, "unmapped:docs-drift")
    monkeypatch.chdir(root)
    assert cli.main(["memory", "ingest"]) == 0
    store = root / ".warden" / "memory" / "attest"
    assert len(list(store.glob("*.json"))) == 1

    # The event is on record; now make its commit unreachable.
    capsys.readouterr()
    monkeypatch.setattr("warden.memory.sha_reachable", lambda root, sha: False)
    assert cli.main(["memory", "ingest"]) == 0
    err = capsys.readouterr().err
    assert "no ref contains" not in err, (
        "an event already in this store was reported as unreachable, and the "
        f"remedy that message names would duplicate it: {err}")
    assert len(list(store.glob("*.json"))) == 1, "the shard was duplicated"


def test_the_marker_is_attest_only_so_gate_stays_re_derivable(
        sample_repo, tmp_path, monkeypatch, capsys):
    """Gate shards are gitignored local working state the next `warden review`
    re-derives — the marker's premise (a committed, branch-scoped store) does
    not hold for them. Applying it there would remove the re-derivation path:
    deleting the gate dir, which the doctrine invites, would leave it
    unrecoverable without hand-editing a marker.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True, check=True).stdout.strip()
    out = root / ".warden" / "out" / "20260824T020000Z-review"
    out.mkdir(parents=True, exist_ok=True)
    (out / "review-findings.json").write_text(json.dumps({
        "head_sha": head, "base_sha": "a" * 40, "rules_version": "v1",
        "findings": [{"rule_id": "unmapped:docs-drift", "severity": "LOW",
                      "file": "app/x.py", "line": 1, "finding": "f",
                      "evidence": "e", "status": "detected"}]}))
    monkeypatch.chdir(root)
    gate = root / ".warden" / "memory" / "gate"

    assert cli.main(["memory", "ingest"]) == 0
    assert list(gate.glob("*.json")), "no gate shard was filed"

    import shutil
    shutil.rmtree(gate)
    capsys.readouterr()
    assert cli.main(["memory", "ingest"]) == 0
    assert list(gate.glob("*.json")), (
        "a deleted gate dir did not re-derive — the marker was applied to a "
        "store whose whole doctrine is that the next review rebuilds it")


def test_a_marker_of_the_wrong_shape_is_treated_as_absent(
        sample_repo, tmp_path, monkeypatch, capsys):
    """On the refusal path the marker is what withholds a review round from
    the corpus, so it is validated rather than trusted: an
    unversioned or shapeless marker reads as absent, and the round is filed.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    _attest_artifact(root, "unmapped:docs-drift")
    monkeypatch.chdir(root)
    from warden import memory as memory_mod
    run_dir = root / ".warden" / "out" / "20260824T010000Z-attest"
    marker = memory_mod._consumed_path(root, run_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"shard": "totally-made-up.json"}))

    assert cli.main(["memory", "ingest"]) == 0
    store = root / ".warden" / "memory" / "attest"
    assert len(list(store.glob("*.json"))) == 1, (
        "a marker with no schema suppressed a real review round")

    # The other dimension the docstring claims: a marker written for the
    # OTHER store must not withhold an attest round. Enforced only by the call
    # site's source check, the stated validation would be one refactor from
    # being none.
    list(store.glob("*.json"))[0].unlink()
    marker.write_text(json.dumps(
        {"schema": 1, "store": "gate", "shard": "totally-made-up.json"}))
    assert cli.main(["memory", "ingest"]) == 0
    assert len(list(store.glob("*.json"))) == 1, (
        "a marker declaring the gate store suppressed an attest review round")


def test_a_marker_that_cannot_be_written_is_reported(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The marker IS the guard, so a silent write failure leaves the invariant
    inoperative and the next sweep on another branch re-mints exactly what
    this exists to prevent. Losing it must not fail an ingest whose evidence
    already landed — but it must be said out loud."""
    root = _clone_fixture(sample_repo, tmp_path)
    _attest_artifact(root, "unmapped:docs-drift")
    monkeypatch.chdir(root)
    monkeypatch.setattr("warden.memory._consumed_path",
                        lambda r, d: Path("/proc/nonexistent/x.json"))
    assert cli.main(["memory", "ingest"]) == 0, "a marker failure was fatal"
    assert len(list((root / ".warden" / "memory" / "attest").glob("*.json"))) == 1, \
        "the evidence was not filed"
    assert "GUARD NOT ARMED" in capsys.readouterr().err, (
        "a silently unarmed guard is the failure this reports")


def test_a_rebase_does_not_invalidate_a_committed_attestation(
        sample_repo, tmp_path, monkeypatch, capsys):
    """A rebase onto main, end to end.

    Branch protection requires a PR be up to date with main. `gh pr
    update-branch --rebase` rewrites every commit sha in origin/main..HEAD, so
    the sha join finds nothing, and without the patch-id fallback the
    required gate reports NO ATTESTATION — for a byte-identical tree that had
    a CLEAN attestation moments earlier.

    Drives the real CLI: attest write -> memory ingest -> commit the shard ->
    rebase -> attest check.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True,
                              text=True, check=True).stdout.strip()

    git("checkout", "-qb", "feat")
    (root / "app" / "x.py").write_text("x = 2\n")
    git("add", "-A")
    git("commit", "-qm", "the reviewed change")

    findings = tmp_path / "f.json"
    findings.write_text(json.dumps(
        {"reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
         "findings": [], "verdict": "clean"}))
    assert cli.main(["attest", "write", "--findings", str(findings),
                     "--base", "main"]) == 0
    assert cli.main(["memory", "ingest"]) == 0
    # The SHARD alone: `.warden/memory/findings.jsonl` is a derived cache
    # `warden enroll` gitignores (GITIGNORE_ENTRIES) and this fixture repo
    # does not, so `add -A` made the ingest commit carry content.
    git("add", attest_mod.SHARD_DIR.as_posix())
    git("commit", "-qm", "ops(memory): commit the shard")

    assert cli.main(["attest", "check", "--base", "main"]) == 0, \
        "the attestation did not pass before the rebase — fixture is wrong"
    capsys.readouterr()

    # main moves, and the branch is brought up to date the way branch
    # protection demands. Every sha in the range is rewritten.
    before = git("rev-list", "main..feat").split()
    git("checkout", "-q", "main")
    git("commit", "-q", "--allow-empty", "-m", "main moves")
    git("checkout", "-q", "feat")
    git("rebase", "-q", "main")
    assert git("rev-list", "main..feat").split() != before, (
        "the rebase rewrote nothing — this test proves nothing")

    assert cli.main(["attest", "check", "--base", "main"]) == 0, (
        "a content-preserving rebase invalidated a valid attestation — the "
        "shortfall workspace-a42 regression")
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "matched by PATCH-ID" in out, (
        "the pass was granted silently. A patch-id match proves the same "
        "CHANGE was reviewed, not that this COMMIT was, so the substitution "
        "has to be visible to whoever reads the gate")


def test_the_fallback_does_not_pass_a_range_nobody_reviewed(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The other half: the fallback must not become a way to pass unreviewed
    work. A different change has different patch-ids and still fails."""
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True,
                              text=True, check=True).stdout.strip()

    git("checkout", "-qb", "feat")
    (root / "app" / "x.py").write_text("x = 2\n")
    git("add", "-A")
    git("commit", "-qm", "reviewed")
    findings = tmp_path / "f.json"
    findings.write_text(json.dumps(
        {"reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
         "findings": [], "verdict": "clean"}))
    assert cli.main(["attest", "write", "--findings", str(findings),
                     "--base", "main"]) == 0
    assert cli.main(["memory", "ingest"]) == 0
    git("add", "-A")
    git("commit", "-qm", "ops(memory): commit the shard")

    # A second branch off main carrying DIFFERENT work, with the shard present
    # in its tree. Nothing reviewed this range.
    git("checkout", "-qb", "other", "feat")
    (root / "app" / "x.py").write_text("x = 999\n")
    git("commit", "-qam", "never reviewed")
    capsys.readouterr()

    git("checkout", "-q", "main")
    git("merge", "-q", "--ff-only", "feat")
    git("checkout", "-q", "other")
    assert cli.main(["attest", "check", "--base", "main"]) == 1, (
        "a range nobody reviewed passed the check via the patch-id fallback")


def _reviewed_branch(root, cli_mod, tmp_path, monkeypatch):
    """A branch with one reviewed commit and its shard committed."""
    import subprocess as sp

    def git(*a):
        return sp.run(["git", *a], cwd=root, capture_output=True, text=True,
                      check=True).stdout.strip()
    git("checkout", "-qb", "feat")
    (root / "app" / "x.py").write_text("x = 2\n")
    git("add", "-A")
    git("commit", "-qm", "the reviewed change")
    findings = tmp_path / "f.json"
    findings.write_text(json.dumps(
        {"reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
         "findings": [], "verdict": "clean"}))
    assert cli_mod.main(["attest", "write", "--findings", str(findings),
                         "--base", "main"]) == 0
    assert cli_mod.main(["memory", "ingest"]) == 0
    # The SHARD alone, which is what a real ingest commit carries: `attest
    # write`'s artifact is gitignored, and `.warden/memory/findings.jsonl` is
    # a derived cache that `warden enroll` gitignores too (GITIGNORE_ENTRIES)
    # — this fixture's repo has no such entry, so `git add -A` staged the
    # cache and made the commit look like content landing after the round.
    git("add", attest_mod.SHARD_DIR.as_posix())
    git("commit", "-qm", "ops(memory): commit the shard")
    return git


def test_an_empty_recorded_binding_is_never_a_universal_pass(
        sample_repo, tmp_path, monkeypatch, capsys):
    """Both narrowing conditions on the fallback need a test, and this is the
    worse of the two: with the empty-list
    guard removed, a shard carrying `range_patch_ids: []` keys the empty tuple,
    `set(()) <= current` is True for EVERY non-empty range, and sorting puts it
    first — a total gate bypass.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)
    git = _reviewed_branch(root, cli, tmp_path, monkeypatch)

    # A second shard whose recorded binding is empty, committed in the tree.
    store = root / ".warden" / "memory" / "attest"
    donor = json.loads(next(store.glob("*.json")).read_text())
    donor["sha"] = "e" * 40
    donor["range_patch_ids"] = []
    (store / "20260101T000000Z-eeeeeeee-empty.json").write_text(
        json.dumps(donor, indent=2) + "\n")
    git("add", "-A")
    git("commit", "-qm", "ops(memory): a shard with an empty binding")

    # A branch off main carrying work nobody reviewed.
    git("checkout", "-qb", "backdoor", "feat")
    (root / "app" / "x.py").write_text("x = 999\n")
    git("commit", "-qam", "never reviewed")
    git("checkout", "-q", "main")
    git("merge", "-q", "--ff-only", "feat")
    git("checkout", "-q", "backdoor")
    capsys.readouterr()

    assert cli.main(["attest", "check", "--base", "main"]) == 1, (
        "a shard with an EMPTY recorded binding matched a range nobody "
        "reviewed — the empty tuple is a subset of everything")


def test_a_partial_overlap_is_not_a_match(sample_repo, tmp_path,
                                              monkeypatch, capsys):
    """The other narrowing condition. Weakening `set(ids) <= current` to a
    non-empty intersection passes any case that shares no patch-id at all.
    This one shares SOME: the attestation covered
    two commits, the range carries only one of them plus unreviewed work.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)
    import subprocess as sp

    def git(*a):
        return sp.run(["git", *a], cwd=root, capture_output=True, text=True,
                      check=True).stdout.strip()

    git("checkout", "-qb", "feat")
    (root / "app" / "x.py").write_text("x = 2\n")
    git("add", "-A")
    git("commit", "-qm", "reviewed one")
    (root / "app" / "y.py").write_text("y = 2\n")
    git("commit", "-qam", "reviewed two")
    findings = tmp_path / "f.json"
    findings.write_text(json.dumps(
        {"reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
         "findings": [], "verdict": "clean"}))
    assert cli.main(["attest", "write", "--findings", str(findings),
                     "--base", "main"]) == 0
    assert cli.main(["memory", "ingest"]) == 0
    git("add", "-A")
    git("commit", "-qm", "ops(memory): commit the shard")

    # A branch carrying only the FIRST reviewed commit, cherry-picked, plus
    # work nobody reviewed. It overlaps the attestation without being covered.
    first = git("rev-list", "--reverse", "main..feat").split()[0]
    shard = git("rev-list", "-1", "feat")
    git("checkout", "-qb", "partial", "main")
    git("cherry-pick", first)
    git("cherry-pick", shard)
    (root / "app" / "z.py").write_text("z = 999\n")
    git("add", "-A")
    git("commit", "-qm", "never reviewed")
    capsys.readouterr()

    assert cli.main(["attest", "check", "--base", "main"]) == 1, (
        "a range carrying only SOME of the attested commits passed — overlap "
        "is not coverage, and the fallback must require the whole set")


def test_the_pass_line_states_only_what_the_match_established(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The pass must not narrate 'a content-preserving rebase. Same change,
    different commits' unconditionally, nor print HEAD as the attested sha:
    with unreviewed commits after the rebase, that tells the reader the range
    is the same change. The record IS the compensating control this design
    rests on; handing the reader a false statement removes it.

    REWRITTEN FOR the coverage rule, deliberately, because it encoded the old
    contract. It used to rebase, add three commits nobody reviewed, and assert
    **exit 0** with "4 commit(s) in this range were NOT covered" — the count
    was the whole compensating control, because a count is all membership can
    offer. It is not the contract any more: those three commits change
    `app/extra*.py`, which no round read, and the coverage rule refuses them.
    The second half below pins exactly that, so the old expectation cannot
    come back silently.

    The subject is unchanged and still tested, on the range that still passes:
    a rebase followed by the shard commit alone. The pass line's three
    honesty claims are what they always were.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)
    git = _reviewed_branch(root, cli, tmp_path, monkeypatch)
    # The commit the shard actually names — read from the shard, not inferred
    # from the range. Inferring it gets the wrong end of a two-commit range.
    store = root / ".warden" / "memory" / "attest"
    attested = json.loads(next(store.glob("*.json")).read_text())["sha"]

    git("checkout", "-q", "main")
    git("commit", "-q", "--allow-empty", "-m", "main moves")
    git("checkout", "-q", "feat")
    git("rebase", "-q", "main")
    capsys.readouterr()

    assert cli.main(["attest", "check", "--base", "main"]) == 0
    out = capsys.readouterr().out
    head = git("rev-parse", "HEAD")

    assert "matched by PATCH-ID" in out
    # One: the shard commit was not part of the reviewed range either, and the
    # honest count says so rather than quietly excusing it. The carve-out
    # excuses it from BLOCKING; it never excuses it from being reported.
    assert "1 commit(s) in this range were NOT covered" in out, (
        f"the pass does not say how much of the range the review never saw: {out}")
    # Scoped to the MATCH line: the header legitimately names HEAD as the end
    # of the range being checked. What must never appear is HEAD in the
    # position that claims to carry the attestation.
    match_line = next(ln for ln in out.splitlines() if "matched by PATCH-ID" in ln)
    assert head[:12] not in match_line, (
        "HEAD is printed as though it carried the attestation; it does not")
    assert attested[:12] in match_line, (
        "the line does not name the commit the shard actually attests")
    assert "Same change" not in out, (
        "the pass still asserts the range is the same change reviewed")

    # THE CONTRACT THAT CHANGED. Three commits nobody reviewed, on the same
    # patch-id path, used to exit 0 behind that count.
    for i in range(3):
        (root / "app" / f"extra{i}.py").write_text(f"e = {i}\n")
        git("add", "-A")
        git("commit", "-qm", f"NOT reviewed {i}")
    capsys.readouterr()

    assert cli.main(["attest", "check", "--base", "main"]) == 1, (
        "three unreviewed commits after the reviewed range passed at exit 0 "
        "— the count is a report, not the gate")
    out = capsys.readouterr()
    assert "UNREVIEWED COMMITS" in out.out
    assert "app/extra0.py" in out.out and "Re-review this head" in out.err


def test_the_fallback_refuses_a_range_containing_a_merge(
        sample_repo, tmp_path, monkeypatch, capsys):
    """A merge in the range refuses the patch-id fallback.

    Patch-ids come from `git log -p --no-merges`, so a merge commit's OWN
    contribution — conflict resolution, or an evil merge — enters neither the
    subset test nor the uncovered count. A branch that cherry-picks one
    reviewed commit and then merges in a backdoor would exit 0, with the gate
    printing "Every commit in this range was covered by that review" while
    `app/backdoor.py` sits in the range, introduced by the merge itself.
    WITHOUT the fallback that range fails closed — so the fallback would turn
    a refusal into an affirmative all-clear.

    Refusing on merges is the fail-closed answer and costs nothing real: the
    bug this exists to fix is `gh pr update-branch --rebase`, which produces a
    linear range by construction.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)
    git = _reviewed_branch(root, cli, tmp_path, monkeypatch)

    # main moves and feat is rebased, so the SHA join is dead and the fallback
    # is the only thing that could pass this range.
    git("checkout", "-q", "main")
    git("commit", "-q", "--allow-empty", "-m", "main moves")
    git("checkout", "-q", "feat")
    git("rebase", "-q", "main")
    # A sidecar nobody reviewed, merged in with --no-ff so the range really
    # contains a MERGE COMMIT. A branch that fast-forwarded would exercise no
    # merge at all and stay green with the refusal deleted.
    git("checkout", "-qb", "sidecar", "main")
    (root / "app" / "backdoor.py").write_text("os.system('curl evil')\n")
    git("add", "-A")
    git("commit", "-qm", "NEVER reviewed")
    git("checkout", "-q", "feat")
    git("merge", "--no-edit", "--no-ff", "-q", "sidecar")
    assert git("rev-list", "--merges", "main..feat").split(), (
        "the range contains no merge commit — this test exercises nothing")
    capsys.readouterr()

    code = cli.main(["attest", "check", "--base", "main"])
    out = capsys.readouterr().out
    assert code != 0, (
        "a range whose merge commit introduced unreviewed content PASSED. "
        f"Without the fallback it fails closed:\n{out}")
    assert "matched by PATCH-ID" not in out
    assert "Every commit in this range was covered" not in out


def test_a_merge_in_range_is_reported_not_silently_skipped(
        sample_repo, tmp_path, monkeypatch, capsys):
    """Refusing is only half of it: a refusal nobody can act on is the
    diagnosability defect this repo names. The reason has to say why the
    content join did not run and what to do instead."""
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)
    git = _reviewed_branch(root, cli, tmp_path, monkeypatch)

    git("checkout", "-q", "main")
    (root / "app" / "other.py").write_text("o = 1\n")
    git("add", "-A")
    git("commit", "-qm", "main moves")
    git("checkout", "-q", "feat")
    git("rebase", "-q", "main")          # kills the sha join
    git("checkout", "-q", "main")
    (root / "app" / "later.py").write_text("l = 1\n")
    git("add", "-A")
    git("commit", "-qm", "main moves again")
    git("checkout", "-q", "feat")
    git("merge", "--no-edit", "-q", "main")   # a merge lands in the range
    capsys.readouterr()

    cli.main(["attest", "check", "--base", "main"])
    out = capsys.readouterr().out
    assert "merge commits" in out, (
        f"a range with a merge was refused with no reason a reader can act on:\n{out}")


def test_a_binding_that_could_not_be_computed_is_reported_at_both_ends(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The three surfaces that make 'could not run' distinguishable from 'no
    attestation', each tested: an untested signal can vanish invisibly."""
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)
    import subprocess as sp

    def git(*a):
        return sp.run(["git", *a], cwd=root, capture_output=True, text=True,
                      check=True).stdout.strip()
    git("checkout", "-qb", "feat")
    (root / "app" / "x.py").write_text("x = 2\n")
    git("add", "-A")
    git("commit", "-qm", "reviewed")

    findings = tmp_path / "f.json"
    findings.write_text(json.dumps(
        {"reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
         "findings": [], "verdict": "clean"}))

    from warden import attest as attest_mod

    # WRITE end: the probe fails -> stderr warning AND a manifest record.
    monkeypatch.setattr(attest_mod, "range_patch_ids", lambda *a, **k: None)
    assert cli.main(["attest", "write", "--findings", str(findings),
                     "--base", "main"]) == 0
    err = capsys.readouterr().err
    assert "records no content binding" in err, (
        f"a shard shipped with no content binding and said nothing: {err}")
    run_dir = sorted((root / ".warden" / "out").glob("*-attest"))[-1]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest.get("patch_binding") == "unavailable", (
        "the cage has no terminal; a signal that lives only in stderr is one "
        f"an unattended run cannot be audited for: {manifest}")

    # CHECK end: the same failure must not read as 'no attestation'.
    assert cli.main(["memory", "ingest"]) == 0
    git("add", "-A")
    git("commit", "-qm", "ops(memory): commit the shard")
    git("checkout", "-q", "main")
    git("commit", "-q", "--allow-empty", "-m", "main moves")
    git("checkout", "-q", "feat")
    git("rebase", "-q", "main")
    capsys.readouterr()
    cli.main(["attest", "check", "--base", "main"])
    out = capsys.readouterr().out
    assert "could not run" in out and "not a statement about whether a review" in out, (
        f"a failed probe rendered as a missing review: {out}")


def test_recall_warns_when_a_files_argument_names_no_existing_path(
        sample_repo, monkeypatch, capsys):
    """A --files argument naming no existing path warns on stderr. A shell
    quoting bug can pass the whole changed-file list as ONE argument; recall
    then matches nothing and prints 'no relevant history' — indistinguishable
    from a genuinely empty corpus, so a review round silently runs without
    its priors. The recall output itself is unchanged (the read stays
    pure)."""
    monkeypatch.chdir(sample_repo)
    garbage = "app/prompt.py db/BASELINE app/core/secrets_surface.py"
    assert cli.main(["memory", "recall", "--files", garbage,
                     "--for", "reviewer"]) == 0
    captured = capsys.readouterr()
    assert garbage in captured.err
    assert "does not exist" in captured.err
    assert "no relevant history" in captured.out


def test_recall_on_existing_path_with_empty_corpus_stays_quiet(
        sample_repo, monkeypatch, capsys):
    """Real empty history must remain distinguishable: an existing path with
    an empty corpus prints the empty-history line and NO warning."""
    monkeypatch.chdir(sample_repo)
    assert cli.main(["memory", "recall", "--files", "app/prompt.py",
                     "--for", "reviewer"]) == 0
    captured = capsys.readouterr()
    assert "does not exist" not in captured.err
    assert "no relevant history" in captured.out


def test_recall_relativizes_absolute_paths_under_the_repo_root(
        tmp_path, monkeypatch, capsys):
    """silent-empty-recall: an absolute path to a real repo file passes the
    exists check, yet unrelativized could never match — recall's dir_prefix
    keys are repo-relative, so '/Users/...' would become query prefix
    '//Users' and silence would read as empty history again. Absolute paths
    under the root are relativized so they MATCH."""
    from datetime import datetime, timezone
    from conftest import REPO_YAML
    (tmp_path / "repo.yaml").write_text(REPO_YAML)
    app = tmp_path / "app"
    app.mkdir()
    (app / "x.py").write_text("x = 1\n")
    mem = tmp_path / ".warden" / "memory"
    mem.mkdir(parents=True)
    record = {"id": "r1", "ts": datetime.now(timezone.utc).isoformat(),
              "seq": 0, "sha": "s", "rule_id": "unmapped:fail-open",
              "tags": [], "dir_prefix": "app", "file": "app/y.py", "line": 1,
              "severity": "HIGH", "finding": "prior finding here",
              "evidence": "e", "status": "confirmed", "origin": "day"}
    (mem / "findings.jsonl").write_text(json.dumps(record) + "\n")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["memory", "recall", "--files", str(app / "x.py"),
                     "--for", "reviewer"]) == 0
    captured = capsys.readouterr()
    assert "prior finding here" in captured.out, \
        "an absolute path under the root must reach the repo-relative keys"
    assert "does not exist" not in captured.err


def test_recall_warns_on_an_absolute_path_outside_the_repo(
        sample_repo, monkeypatch, capsys):
    """An absolute path outside the root exists on disk but can never match
    a repo-relative recall key — existence is false assurance; say so."""
    monkeypatch.chdir(sample_repo)
    assert cli.main(["memory", "recall", "--files", "/etc/hosts",
                     "--for", "reviewer"]) == 0
    assert "outside" in capsys.readouterr().err


def test_diff_writes_the_review_package_to_a_file(tmp_path, monkeypatch, capsys):
    """Review subagents read the range from a file on disk
    instead of inheriting the whole diff through the session."""
    from conftest import REPO_YAML
    (tmp_path / "repo.yaml").write_text(REPO_YAML)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "root"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-qb", "feature"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qam", "feat"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "pkg.md"
    assert cli.main(["diff", "--base", "main", "-o", str(out)]) == 0
    pkg = out.read_text()
    assert "# review package" in pkg and "+y = 2" in pkg
    assert str(out) in capsys.readouterr().out


def test_diff_bad_ref_exits_2(sample_repo, monkeypatch, capsys):
    monkeypatch.chdir(sample_repo)
    assert cli.main(["diff", "--base", "no-such-ref-abc"]) == 2
    assert "warden" in capsys.readouterr().err


def test_memory_ingest_origin_speaks_presence_not_the_clock(sample_repo,
                                                            monkeypatch, capsys):
    """--origin day|night named the clock,
    which no longer starts anything. A run is interactive or unattended."""
    monkeypatch.chdir(sample_repo)
    assert cli.main(["memory", "ingest", "--origin", "unattended"]) == 0
    capsys.readouterr()
    # argparse choices reject the retired vocabulary and NAME the new one,
    # so a caller reading the error learns the replacement in place.
    with pytest.raises(SystemExit):
        cli.main(["memory", "ingest", "--origin", "night"])
    err = capsys.readouterr().err
    assert "interactive" in err and "unattended" in err


def test_decide_list_reads_the_committed_store_via_cli(sample_repo, tmp_path,
                                                       monkeypatch, capsys):
    """The `decide list` CLI branch reads the committed decision
    shard store (not the run artifact) — the wiring, not just the library fn."""
    import json as _json
    from warden import decide as _decide
    root = _clone_fixture(sample_repo, tmp_path)
    doc = _decide.build(
        {"decided": "bind S-05 to rules_version",
         "why": "a stale backtest satisfied the gate forever",
         "cost_if_wrong": "a consumer fails S-05 until it re-derives",
         "alternatives": ["a narrower marker", "do nothing"],
         "scope": "warden certify S-05"},
        head_sha="a" * 40, base_sha="b" * 40, rules_version="v",
        bead="agentops-hff")
    sd = root / _decide.SHARD_DIR
    sd.mkdir(parents=True, exist_ok=True)
    (sd / _decide._shard_name(doc)).write_text(_json.dumps(doc))
    monkeypatch.chdir(root)
    assert cli.main(["decide", "list"]) == 0
    out = capsys.readouterr().out
    assert "committed decisions (1)" in out
    assert "bind S-05 to rules_version" in out and "agentops-hff" in out


def test_decide_list_warns_on_an_unreadable_shard(sample_repo, tmp_path,
                                                  monkeypatch, capsys):
    """A corrupt shard is named loudly, and the count is
    reported as a floor — the repo's UNREAD-not-zero norm."""
    from warden import decide as _decide
    root = _clone_fixture(sample_repo, tmp_path)
    sd = root / _decide.SHARD_DIR
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "20260828T000000Z-aaaaaaaa-deadbeef.json").write_text("{ not json")
    monkeypatch.chdir(root)
    assert cli.main(["decide", "list"]) == 0
    err = capsys.readouterr().err
    assert "could not be read" in err and "floor" in err


def test_attest_write_warns_when_findings_name_no_changed_file(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The CLI computes `changed_paths`, so the guard against `attest write`
    accepting a findings file about a different commit has to drive the CLI,
    not `attest.build()`.

    Unit tests hand-pass `changed_paths`, so nothing else pins the CLI's
    computation of it. Swapping the three-dot diff for `git ls-files` — which
    lists every TRACKED file and therefore accepts a stale findings set —
    must fail here:
    `app/y.py` is tracked but outside the range, so a tracked-file range would
    find overlap and stay silent.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)
    path = root.parent / "stale.json"
    path.write_text(json.dumps({
        "reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
        "findings": [{"rule_id": "scope-creep", "severity": "LOW",
                      "file": "app/y.py", "line": 1, "finding": "f",
                      "evidence": "e", "status": "fixed"}],
        "verdict": "clean"}))
    assert cli.main(["attest", "write", "--findings", str(path)]) == 0, \
        "the range binding is advisory — it must never block an attestation"
    err = capsys.readouterr().err
    assert "app/y.py" in err and "app/x.py" in err, \
        f"the warning must name both what the findings say and what changed: {err}"


def test_an_in_range_finding_is_not_warned_about(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The other half: a warning that fires on honest rounds is one every
    reader learns to skip. `app/x.py` IS the file the branch changed."""
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)
    findings = _findings_file(root, "scope-creep")
    assert cli.main(["attest", "write", "--findings", str(findings)]) == 0
    assert "WARNING" not in capsys.readouterr().err


def test_the_range_is_what_the_branch_changed_not_what_main_also_did(
        sample_repo, tmp_path, monkeypatch, capsys):
    """Three-dot, not two-dot: two-dot
    adds whatever main did AFTER the branch point, which widens the path set
    and therefore SUPPRESSES warnings — the fail-open direction. A finding
    naming a file only main touched is not a finding about this branch's
    review, so it must still warn."""
    root = _clone_fixture(sample_repo, tmp_path)
    git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                    capture_output=True, text=True)
    git("checkout", "-q", "main")
    (root / "app" / "moved_on.py").write_text("m = 1\n")
    git("add", "app/moved_on.py")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
        "-m", "main moves ahead")
    git("checkout", "-q", "work")
    monkeypatch.chdir(root)
    path = root.parent / "mainonly.json"
    path.write_text(json.dumps({
        "reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
        "findings": [{"rule_id": "scope-creep", "severity": "LOW",
                      "file": "app/moved_on.py", "line": 1, "finding": "f",
                      "evidence": "e", "status": "fixed"}],
        "verdict": "clean"}))
    assert cli.main(["attest", "write", "--findings", str(path)]) == 0
    assert "app/moved_on.py" in capsys.readouterr().err, \
        "a file only main changed is not part of what this branch reviewed"


def test_the_range_binding_survives_the_terminal(sample_repo, tmp_path,
                                                     monkeypatch, capsys):
    """enforcement-truth: the warning is recorded, not only printed to
    stderr — nothing in the artifact, the manifest, or the exit code would
    mean it is discarded. The hazard is SILENT corpus poisoning,
    and the unattended cage has no human at the terminal, so a signal that
    lives only in scrollback cannot be audited for after the fact. The repo's
    own precedent is one row above it in the wiki: verify's `dirty` flag
    travels with the result.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    # `sample_repo` is session-scoped and other tests leave run dirs in it,
    # which the clone copies. Reading "the newest attest manifest" would then
    # read one of THEIRS and pass or fail on another test's ordering.
    shutil.rmtree(root / ".warden" / "out", ignore_errors=True)
    monkeypatch.chdir(root)
    path = root.parent / "stale.json"
    path.write_text(json.dumps({
        "reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
        "findings": [{"rule_id": "scope-creep", "severity": "LOW",
                      "file": "app/y.py", "line": 1, "finding": "f",
                      "evidence": "e", "status": "fixed"}],
        "verdict": "clean"}))
    assert cli.main(["attest", "write", "--findings", str(path)]) == 0
    recorded = _latest_attest_manifest(root)
    assert recorded.get("range_binding") == "out-of-range", \
        f"the range binding must outlive the terminal: {recorded}"

    # ...and an honest round records the clean reading, so absence of the key
    # means "an older warden wrote this", not "the check passed".
    capsys.readouterr()
    assert cli.main(["attest", "write", "--findings",
                     str(_findings_file(root, "scope-creep"))]) == 0
    assert _latest_attest_manifest(root).get("range_binding") == "ok"


# --- a repo.yaml that never finishes being read -----------------------------


@pytest.mark.parametrize("argv", [
    ["explain"],
    ["review", "--base", "HEAD", "--no-comment"],
])
def test_commands_name_an_unreadable_repo_yaml_instead_of_denying_it(
        argv, tmp_path, monkeypatch, capsys, make_fifo):
    """The two commands that do NOT reach the FIFO through mine's probe. Both
    resolve their root with `config.find_repo_root`; a walk testing
    `is_file()` — False for a FIFO — would step over a repo.yaml that is
    plainly there and answer "no repo.yaml found from <dir> upward". Exit 2
    either way; the defect would be the SENTENCE, an absence
    claim about a file the reader never managed to look at.

    Bounded because the same commands park the moment find_repo_root stops
    walking past the file and hands it to a reader that opens it blocking."""
    make_fifo(tmp_path / "repo.yaml")
    monkeypatch.chdir(tmp_path)

    assert finishes_within(15, cli.main, argv) == 2
    err = capsys.readouterr().err
    assert "fifo" in err.lower(), err
    assert "no repo.yaml found" not in err, err


def test_the_carry_forward_reading_survives_the_terminal(
        sample_repo, tmp_path, monkeypatch, capsys):
    """`range_binding`'s precedent one test up, for the carry-forward
    reading. Driven through `cli.main` rather than a source grep
    (tests-bite): a grep would survive `carry_state = "distinct"`, a manifest
    recording a reading no comparison produced. Here the value has to come
    from the comparison: a minted round reads `distinct`, no --review-dir
    reads `no-review-dir`, and a copied report is refused at exit 2 with no
    manifest written for it."""
    root = _clone_fixture(sample_repo, tmp_path)
    shutil.rmtree(root / ".warden" / "out", ignore_errors=True)
    monkeypatch.chdir(root)
    assert cli.main(["round", "new", "--base", "main"]) == 0
    round_dir = Path(capsys.readouterr().out.strip())
    (round_dir / "reviewers" / "code-reviewer.json").write_text(
        '{"judged": "head", "findings": []}\n')
    payload = root.parent / "roster.json"
    payload.write_text(json.dumps({
        "reviewers": [{"role": "code-reviewer", "agent": "subagent",
                       "returned": True, "findings": 0,
                       "output": "code-reviewer.json", "round": 1}],
        "findings": [], "verdict": "clean"}))
    assert cli.main(["attest", "write", "--findings", str(payload),
                     "--base", "main", "--review-dir",
                     str(round_dir / "reviewers")]) == 0
    recorded = _latest_attest_manifest(root)
    assert recorded.get("carry_forward") == "distinct"
    assert "0 sibling round(s)" in recorded.get("carry_forward_detail", ""), (
        "the detail — how many rounds were compared — must outlive the "
        f"terminal too: {recorded}")

    capsys.readouterr()
    assert cli.main(["attest", "write", "--findings",
                     str(_findings_file(root, "scope-creep"))]) == 0
    assert _latest_attest_manifest(root).get("carry_forward") == "no-review-dir"

    # A second round carrying the first round's bytes: refused, and the
    # newest manifest is still the honest one above.
    capsys.readouterr()
    assert cli.main(["round", "new", "--base", "main"]) == 0
    second = Path(capsys.readouterr().out.strip())
    shutil.copy(round_dir / "reviewers" / "code-reviewer.json",
                second / "reviewers" / "code-reviewer.json")
    assert cli.main(["attest", "write", "--findings", str(payload),
                     "--base", "main", "--review-dir",
                     str(second / "reviewers")]) == 2
    assert "carried forward" in capsys.readouterr().err
    assert _latest_attest_manifest(root).get("carry_forward") == "no-review-dir"


def _audit_fixture(sample_repo, tmp_path, verify_map, changed: str):
    """A committed repo with one change, a review artifact for that range, and
    a verify artifact for a scope the change does NOT require."""
    import yaml
    raw = yaml.safe_load((sample_repo / "repo.yaml").read_text())
    raw.pop("platform", None)   # no pin -> enforce_platform_pin is a no-op
    raw["verify"] = verify_map
    (tmp_path / "repo.yaml").write_text(yaml.safe_dump(raw))
    shutil.copytree(sample_repo / ".warden" / "rules",
                    tmp_path / ".warden" / "rules")

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True,
                       capture_output=True)
    git("init", "-q", "-b", "main")
    (tmp_path / "seed.txt").write_text("seed\n")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "root")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True).stdout.strip()
    path = tmp_path / changed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "change")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True).stdout.strip()

    out = tmp_path / ".warden" / "out"
    review = out / "20260101T000000000000Z-review"
    review.mkdir(parents=True)
    (review / "review-findings.json").write_text(json.dumps({
        "rules_version": "v1", "engine": "deterministic", "base_sha": base,
        "head_sha": head, "findings": [], "deferred_to_pre_pr": []}))
    return base, head, out


def test_audit_derives_the_required_scopes_from_the_reviewed_range(
        sample_repo, tmp_path, monkeypatch, capsys):
    """`warden audit` driven end to end, so replacing `_cmd_audit` or
    `diffs.changed_files` with `raise RuntimeError` reddens. Named mutation
    this pins: take the scope from the latest verify artifact instead of from
    the reviewed range's paths — last-writer-wins — and the run below renders
    `--scope contracts: PASS` instead of NO VERIFY RESULT."""
    verify_map = {"all": [{"run": "true"}],
                  "contracts": [{"run": "true", "cwd": "schemas"}]}
    _base, head, out = _audit_fixture(sample_repo, tmp_path, verify_map,
                                      "app/x.py")
    # A PASS for a scope the diff does not require, and nothing for the one
    # it does. `contracts` narrows because repo.yaml declares a schemas/
    # component; `app/x.py` is outside it.
    run = out / "20260202T000000000000Z-verify"
    run.mkdir(parents=True)
    (run / "verify-result.json").write_text(json.dumps({
        "scope": "contracts", "passed": True, "head_sha": head,
        "results": [{"cmd": "true", "cwd": "schemas", "exit_code": 0,
                     "duration_s": 0.1}]}))

    monkeypatch.chdir(tmp_path)
    assert cli.main(["audit"]) == 0
    body = capsys.readouterr().out
    assert "verify --scope all**: ⚪ NO VERIFY RESULT" in body
    assert "PASS" not in body, "a scope the diff never required rendered green"


def test_audit_falls_closed_to_every_scope_when_the_range_will_not_resolve(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The reviewed range's shas may not exist locally (a shallow clone, a
    pruned branch). "Could not compute the diff" must ask for MORE
    verification, not less — every declared scope, each reported unverified —
    never a silent single green line."""
    verify_map = {"all": [{"run": "true"}],
                  "contracts": [{"run": "true", "cwd": "schemas"}]}
    _base, _head, out = _audit_fixture(sample_repo, tmp_path, verify_map,
                                       "app/x.py")
    review = out / "20260101T000000000000Z-review" / "review-findings.json"
    doc = json.loads(review.read_text())
    doc["base_sha"] = "0" * 40      # resolves to no object in this repo
    review.write_text(json.dumps(doc))

    monkeypatch.chdir(tmp_path)
    assert cli.main(["audit"]) == 0
    body = capsys.readouterr().out
    assert "verify --scope all**: ⚪ NO VERIFY RESULT" in body
    assert "verify --scope contracts**: ⚪ NO VERIFY RESULT" in body


def test_review_derives_the_required_scopes_from_the_rename_blind_paths(
        sample_repo, tmp_path, monkeypatch, capsys):
    """Renames at the seam that renders the sticky comment on every PR, beside
    the pins at `changed_files` and `DiffContext`. Named mutation this pins:
    `scopes_for_paths(config, diff_ctx.all_files)` — the move below
    reports only `app/x.json`, the emptied `schemas/` subtree disappears, and
    the `contracts` scope stops being required."""
    import yaml
    raw = yaml.safe_load((sample_repo / "repo.yaml").read_text())
    raw.pop("platform", None)
    raw["verify"] = {"all": [{"run": "true"}],
                     "contracts": [{"run": "true", "cwd": "schemas"}]}
    (tmp_path / "repo.yaml").write_text(yaml.safe_dump(raw))
    shutil.copytree(sample_repo / ".warden" / "rules",
                    tmp_path / ".warden" / "rules")

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True,
                       capture_output=True)
    git("init", "-q", "-b", "main")
    (tmp_path / "schemas").mkdir()
    # Long enough that git scores the move as a rename rather than add+delete.
    (tmp_path / "schemas" / "x.json").write_text(
        "".join(f'{{"k{i}": {i}}}\n' for i in range(60)))
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "root")
    git("checkout", "-qb", "feature")
    (tmp_path / "app").mkdir(exist_ok=True)
    git("mv", "schemas/x.json", "app/x.json")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "move")

    monkeypatch.chdir(tmp_path)
    cli.main(["review", "--base", "main", "--no-comment"])
    body = capsys.readouterr().out
    assert "verify --scope contracts**: ⚪ NO VERIFY RESULT" in body, (
        "the scope covering the subtree the file LEFT was not required — "
        "review is reading the rename-DETECTED path list again")
    assert "verify --scope all**: ⚪ NO VERIFY RESULT" in body


def test_attest_write_derives_each_reviewers_outcome_from_the_diff_it_names(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The CLI hands `build` the changed-file list the review was shown, and
    the roster comes out with an `outcome` per entry: a refusal text is
    `no-review`, a report naming a changed file is `reviewed-clean`. Driven
    through the real command, so the seam between the CLI's `changed` and the
    derivation is what is tested."""
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)
    import subprocess as sp

    def git(*a):
        return sp.run(["git", *a], cwd=root, capture_output=True, text=True,
                      check=True).stdout.strip()
    git("checkout", "-qb", "feat")
    (root / "app" / "x.py").write_text("x = 2\n")
    git("add", "-A")
    git("commit", "-qm", "reviewed")

    review_dir = tmp_path / "reviewers"
    review_dir.mkdir()
    (review_dir / "code-reviewer.json").write_text(
        "I am unable to review this request.\n")
    (review_dir / "cross-examiner.json").write_text(
        '{"reviewed": ["app/x.py"], "findings": []}\n')
    findings = tmp_path / "f.json"
    findings.write_text(json.dumps({
        "reviewers": [
            {"role": "code-reviewer", "agent": "s", "round": 1,
             "returned": True, "findings": 0, "output": "code-reviewer.json"},
            {"role": "cross-examiner", "agent": "s", "round": 1,
             "returned": True, "findings": 0, "output": "cross-examiner.json"}],
        "findings": [], "verdict": "clean"}))
    assert cli.main(["attest", "write", "--findings", str(findings),
                     "--base", "main", "--review-dir", str(review_dir)]) == 0
    run_dir = sorted((root / ".warden" / "out").glob("*-attest"))[-1]
    doc = json.loads((run_dir / "attestation.json").read_text())
    outcomes = {r["role"]: r.get("outcome") for r in doc["reviewers"]}
    assert outcomes == {"code-reviewer": "no-review",
                        "cross-examiner": "reviewed-clean"}, outcomes


def test_a_clean_review_of_a_non_ascii_path_is_stamped_reviewed_clean(
        sample_repo, tmp_path, monkeypatch):
    """A changed-file list from `git diff --name-only`, whose default
    `core.quotepath` spells `app/café.py` as `"app/caf\\303\\251.py"`, would
    not match: a reviewer that genuinely reviewed that file names it the way
    everyone writes it, the quoted spelling appears in no output, and a clean
    review would be stamped `no-review` — and a declared `reviewed-clean`
    refused. The
    listing is read NUL-terminated, which git never quotes. Driven through the
    real command against a real git fixture."""
    root = _clone_fixture(sample_repo, tmp_path)
    monkeypatch.chdir(root)
    import subprocess as sp

    def git(*a):
        return sp.run(["git", *a], cwd=root, capture_output=True, text=True,
                      check=True).stdout.strip()
    git("checkout", "-qb", "feat")
    (root / "app" / "café.py").write_text("x = 2\n")
    git("add", "-A")
    git("commit", "-qm", "reviewed")

    review_dir = tmp_path / "reviewers"
    review_dir.mkdir()
    (review_dir / "code-reviewer.json").write_text(
        '{"reviewed": ["app/café.py"], "findings": []}\n')
    (review_dir / "cross-examiner.json").write_text(
        '{"judged": ["app/café.py"], "verdicts": []}\n')
    findings = tmp_path / "f.json"
    findings.write_text(json.dumps({
        "reviewers": [
            {"role": "code-reviewer", "agent": "s", "round": 1,
             "returned": True, "findings": 0, "output": "code-reviewer.json",
             "outcome": "reviewed-clean"},
            {"role": "cross-examiner", "agent": "s", "round": 1,
             "returned": True, "findings": 0, "output": "cross-examiner.json"}],
        "findings": [], "verdict": "clean"}))
    assert cli.main(["attest", "write", "--findings", str(findings),
                     "--base", "main", "--review-dir", str(review_dir)]) == 0, (
        "a declared reviewed-clean of a review that named the changed file was "
        "refused — the changed-file list is still quoted")
    run_dir = sorted((root / ".warden" / "out").glob("*-attest"))[-1]
    doc = json.loads((run_dir / "attestation.json").read_text())
    outcomes = {r["role"]: r.get("outcome") for r in doc["reviewers"]}
    assert outcomes == {"code-reviewer": "reviewed-clean",
                        "cross-examiner": "reviewed-clean"}, outcomes
