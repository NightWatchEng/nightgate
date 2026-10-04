"""`warden round count`: the round cap, checked where the builder is not.

The count is derivable from committed evidence — the shards ADDED by a commit
in `base..head` carry `reviewers[].round`, so anything holding the range can
recompute it. A count only `warden round new` reads, on the builder's own
machine, makes the cap advice rather than a hard stop. These tests are the CLI
seam CI calls and the workflow step that calls it.

WHICH WAY IT ERRS is the thing to keep straight, and every test here is about
one side of it. A HIGHER count buys a later round's smaller declared crew; a
LOWER one buys repair budget. The gate reads `capped_rounds`: the highest
round a NON-closure committed shard records, a FLOOR off evidence the branch
itself committed, ANYWHERE the count looked: the range AND every
`--prior-head`. A prior head's reading reaches the VERDICT on purpose — if it
only reported, dropping the commits would still buy the budget back. So exit
1 fires only on the branch's own admission, while the readings that are facts about the evidence rather than
breaches of the budget — the two chains disagreeing, shards recording no
round, and the rewrite report itself — are reported at exit 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import REPO_YAML

ROOT = Path(__file__).parent.parent
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout


def _sha(repo: Path, ref: str = "HEAD") -> str:
    return _git(repo, "rev-parse", ref).strip()


def _graph(cap: int = 2) -> dict:
    """A graph.yaml that RESOLVES: `declared_crew` validates the whole
    constitution once a `review` block is declared, so a half-graph would
    answer exit 2 and prove nothing about the cap."""
    return {
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
        "memory": [{"to": "code-reviewer", "type": "patterns"},
                   {"to": "cross-examiner", "type": "precedents"}],
        "verification": {"independent_judges": ["cross-examiner"]},
        "review": {
            "rounds": [
                {"round": 1, "roles": ["code-reviewer", "cross-examiner"]},
                {"round": 2, "roles": ["scoped-re-reviewer"]},
            ],
            "cap": cap,
            "lenses": [{"id": "secrets-in-diff", "rule": "secrets-in-diff"}],
            "merge_authority": "founder",
        },
    }


@pytest.fixture
def repo(tmp_path):
    """A real git repository on a feature branch off `main`, declaring a
    repair budget of 2 in repo.yaml and the matching cap in graph.yaml."""
    r = tmp_path / "r"
    rules = r / ".warden" / "rules"
    rules.mkdir(parents=True)
    (rules / "secrets-in-diff.md").write_text("---\nid: secrets-in-diff\n---\n")
    _git(r.parent, "init", "-q", "-b", "main", "r")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    policy = yaml.safe_load(REPO_YAML)
    policy["repair"] = {"budget": 2}
    (r / "repo.yaml").write_text(yaml.safe_dump(policy))
    (r / "graph.yaml").write_text(yaml.safe_dump(_graph(), sort_keys=False))
    (r / ".gitignore").write_text(".warden/out/\n")
    (r / "a.txt").write_text("one\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    _git(r, "checkout", "-q", "-b", "feature")
    (r / "a.txt").write_text("two\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "the change")
    return r


def _shard(repo: Path, name: str, rounds: list[int | None], *,
           closure: bool = False) -> str:
    """One committed attestation shard, as `warden memory ingest` writes one.

    A `None` in `rounds` is a legacy reviewer entry carrying no `round` at
    all, which 141 of this repo's 302 committed shards are."""
    (repo / ".warden" / "memory" / "attest").mkdir(parents=True, exist_ok=True)
    path = f".warden/memory/attest/{name}"
    doc = {"schema": 1, "source": "attest", "sha": _sha(repo),
           "verdict": "clean",
           "reviewers": [{"role": "code-reviewer", "returned": True,
                          **({} if n is None else {"round": n})}
                         for n in rounds]}
    if closure:
        doc["closure_verification"] = "verified"
    (repo / path).write_text(json.dumps(doc))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"shard: {name}")
    return path


def _cli(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c",
         "import sys; from warden import cli; "
         f"sys.exit(cli.main({list(argv)!r}))"],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)})


def _count(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return _cli(repo, "round", "count", "--base", "main", *extra)


# --- the gate itself -----------------------------------------------------------


def test_a_range_carrying_more_rounds_than_the_cap_is_refused(repo):
    """THE ACCEPTANCE. Three rounds' shards committed in the range, a cap of
    2, and the step exits 1 — on a runner, off the builder's laptop, from
    evidence the branch itself committed. Before this command there was no
    CI step that counted at all, so such a branch was refused by nothing."""
    _shard(repo, "round-1.json", [1])
    _shard(repo, "round-2.json", [2])
    _shard(repo, "round-3.json", [3])
    out = _count(repo)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "over the cap of 2" in out.stderr, out.stderr
    assert "graph.yaml review.cap" in out.stderr, out.stderr


def test_the_refusal_names_both_counts_and_the_disagreement(repo):
    """The refusal reports what `warden round new` reports, for the same
    reason: the reader is the one who can tell a chain that lost its rounds
    to a rebase from a shard not committed yet, and a bare exit code hides
    exactly the evidence the two chains disagree."""
    _shard(repo, "round-1.json", [1])
    _shard(repo, "round-3.json", [3])
    out = _count(repo)
    assert out.returncode == 1, out.stdout + out.stderr
    for cue in ("record 3", "local rounds directory holds 0",
                "heads this change pushed earlier carry 0",
                "round-3.json (round 3)", "SHARDS are ahead"):
        assert cue in out.stderr, (cue, out.stderr)
    report = json.loads(out.stdout)
    assert report["capped_rounds"] == 3 and report["cap"] == 2
    assert report["from_shards"] == 3 and report["from_rounds_dir"] == 0
    assert report["disagreement"] is not None


def test_a_range_within_the_cap_passes_and_still_reports(repo):
    """The direction the gate must NOT err in. The floor understates — a
    branch that commits no shard has no floor at all — so a count at or
    under the cap is not evidence of anything and never fails. Every reading
    is still printed, because a reading that reaches no surface is how this
    class of defect hid in the first place."""
    _shard(repo, "round-1.json", [1])
    _shard(repo, "legacy.json", [None])
    out = _count(repo)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "record no round" in out.stderr, out.stderr
    report = json.loads(out.stdout)
    assert report["capped_rounds"] == 1 and report["number"] == 2
    assert report["unrecorded"] == [".warden/memory/attest/legacy.json"]


def test_a_clean_range_with_no_shards_at_all_passes(repo):
    """A branch that has committed no attestation is not over any budget.
    The cap bounds rounds RUN, and this command never invents one."""
    out = _count(repo)
    assert out.returncode == 0, out.stdout + out.stderr
    assert json.loads(out.stdout)["capped_rounds"] == 0


def test_the_closure_round_past_the_cap_is_exempt_by_its_artifact(repo):
    """graph.yaml declares `review.closure` as a terminal, NON-budgeted round
    minted past the cap, so counting its number against the
    budget would fail every branch that used the seam built to rescue them.

    The exemption is keyed on `closure_verification`, which `attest write`
    stamps only after proving the payload raised nothing new — an artifact,
    never arithmetic on the round number. The closure round still RAISES the
    numbering floor: the next round is numbered above it.
    """
    _shard(repo, "round-1.json", [1])
    _shard(repo, "round-2.json", [2])
    _shard(repo, "closure.json", [3], closure=True)
    out = _count(repo)
    assert out.returncode == 0, out.stdout + out.stderr
    report = json.loads(out.stdout)
    assert report["capped_rounds"] == 2, report
    assert report["closure"] == [".warden/memory/attest/closure.json"]
    assert report["from_shards"] == 3 and report["number"] == 4, report


def test_a_third_review_round_beside_a_closure_round_still_bites(repo):
    """The exemption is one shard's, not the range's: a genuine round 3
    committed beside a closure shard is still over the cap. Otherwise the
    closure seam would launder any number of rounds."""
    _shard(repo, "round-3.json", [3])
    _shard(repo, "closure.json", [4], closure=True)
    out = _count(repo)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "Closure shard(s) exempted" in out.stderr, out.stderr
    assert "round-3.json (round 3)" in out.stderr, out.stderr


# --- where the cap comes from --------------------------------------------------


def test_the_cap_is_read_from_graph_then_repo_yaml_and_never_typed(repo):
    """One source, resolved rather than re-typed into the workflow. graph.yaml
    `review.cap` first — `resolve_review` already refuses a cap that
    disagrees with repo.yaml `repair.budget` — then the budget itself for a
    repo that declares no crew."""
    assert json.loads(_count(repo).stdout)["cap_source"] == "graph.yaml review.cap"
    (repo / "graph.yaml").unlink()
    report = json.loads(_count(repo).stdout)
    assert report["cap"] == 2 and report["cap_source"] == "repo.yaml repair.budget"


def test_an_explicit_cap_wins(repo):
    _shard(repo, "round-2.json", [2])
    assert _count(repo, "--cap", "2").returncode == 0
    out = _count(repo, "--cap", "1")
    assert out.returncode == 1 and "over the cap of 1 (--cap)" in out.stderr


def test_a_repo_declaring_no_cap_enforces_none_and_says_so(repo):
    """Reported, not failed: a repo that declares no budget has nothing for
    this to measure against, and inventing a default would gate consumers on
    a number nobody wrote down. The reading is still printed."""
    (repo / "graph.yaml").unlink()
    policy = yaml.safe_load((repo / "repo.yaml").read_text())
    del policy["repair"]
    (repo / "repo.yaml").write_text(yaml.safe_dump(policy))
    _shard(repo, "round-3.json", [3])
    out = _count(repo)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "no cap is enforced here" in out.stderr, out.stderr
    assert json.loads(out.stdout)["cap"] is None


def test_a_declared_review_block_that_will_not_resolve_is_exit_2(repo):
    """Fail-closed, and for `warden ship`'s reason: the repo CLAIMS a crew, so
    the cap question applies and warden cannot answer it. Never folded into
    "no cap declared", which would skip the gate on exactly the graph.yaml
    `warden graph validate` refuses."""
    doc = _graph()
    doc["review"]["cap"] = 1          # disagrees with repo.yaml repair.budget 2
    (repo / "graph.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    out = _count(repo)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "declared round cap could not be read" in out.stderr, out.stderr


# --- fail-closed --------------------------------------------------------------


def test_a_shard_warden_cannot_read_is_exit_2_not_a_pass(repo):
    """A shard warden cannot parse is not an absent round, and passing the
    gate on it would hand the branch the budget the derivation refuses."""
    (repo / ".warden" / "memory" / "attest").mkdir(parents=True)
    (repo / ".warden" / "memory" / "attest" / "broken.json").write_text("{ no")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "shard: broken")
    out = _count(repo)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "not JSON warden can read" in out.stderr, out.stderr


def test_a_range_that_will_not_resolve_is_exit_2(repo):
    out = _cli(repo, "round", "count", "--base", "no/such/ref")
    assert out.returncode == 2, out.stdout + out.stderr
    assert "cannot resolve the range" in out.stderr, out.stderr


def test_a_prior_head_that_does_not_resolve_is_exit_2(repo):
    """The flag exists so the count is not blind; skipping an unreadable
    witness would return the blind count under it and report a pass."""
    out = _count(repo, "--prior-head", "0" * 40)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "prior head" in out.stderr, out.stderr


# --- the rewrite witness, through the CLI ------------------------------------


def test_the_cli_counts_a_head_the_rewrite_left_behind(repo):
    """The gate's answer to the drop: `--prior-head` is a head this change
    pushed EARLIER, which the forge holds and a rewrite of the branch does
    not reach. Its own `base..head` is counted with the same derivation and
    can only RAISE the floor — so a rebase that drops the shard commits
    stops buying a fresh budget, and the drop is named on stderr."""
    keep = _sha(repo)
    _shard(repo, "round-1.json", [1])
    _shard(repo, "round-2.json", [2])
    _shard(repo, "round-3.json", [3])
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "more work")
    before = _sha(repo)
    _git(repo, "rebase", "-q", "--onto", keep, f"{before}~1", "feature")

    blind = _count(repo)
    assert blind.returncode == 0, "the drop bought a fresh budget, as filed"
    assert json.loads(blind.stdout)["capped_rounds"] == 0

    seen = _count(repo, "--prior-head", before)
    assert seen.returncode == 1, seen.stdout + seen.stderr
    assert "REWRITE" in seen.stderr, seen.stderr
    assert "count does not reset" in seen.stderr, seen.stderr
    report = json.loads(seen.stdout)
    assert report["from_shards"] == 0 and report["from_prior_heads"] == 3
    assert report["capped_rounds"] == 3
    assert [(h["head"], h["rounds"]) for h in report["prior_heads"]] \
        == [(before, 3)]
    # ROUND-1 F-5. The verdict came from a prior head, so `shards` is empty —
    # and naming only that printed "Rounds counted: none" on the same line as
    # "records 3 review round(s)". The shards behind the number are named
    # wherever they were read, and the head they were read at is named too.
    assert report["shards"] == []
    assert report["prior_heads"][0]["closure"] == []
    assert sorted(s["path"] for s in report["prior_heads"][0]["shards"]) == [
        ".warden/memory/attest/round-1.json",
        ".warden/memory/attest/round-2.json",
        ".warden/memory/attest/round-3.json"], report["prior_heads"]
    assert "Rounds counted: none" not in seen.stderr, seen.stderr
    assert f"round-3.json (round 3, at {before[:12]}, a head this change " \
        "pushed earlier)" in seen.stderr, seen.stderr


# --- the workflow step that calls it ------------------------------------------


def _gate_steps() -> list[dict]:
    doc = yaml.safe_load(CI_WORKFLOW.read_text())
    return doc["jobs"]["gate"]["steps"]


def test_the_gate_job_runs_the_counter_on_every_pr():
    """The CLI is only half of it: a cap enforced by a command nobody runs is
    the defect restated. The step lives in the `gate` job because that job is
    the one that runs on a pull_request with the full history the range
    needs, and it must run the REMOTE-TRACKING base — a local `main` in a CI
    checkout is whatever the fetch left behind."""
    steps = [s for s in _gate_steps() if "warden round count" in s.get("run", "")]
    assert steps, (
        "no gate step counts review rounds, so the cap is enforced only on "
        "the builder's laptop again")
    body = "\n".join(s["run"] for s in steps)
    assert 'origin/$BASE_REF' in body, body
    assert all(s["env"]["BASE_REF"] == "${{ github.base_ref }}" for s in steps), steps
    assert "--prior-head" in body, (
        "the step passes no head from outside the branch's own history, so a "
        "rewrite that drops the shard commits still resets the floor in "
        f"silence:\n{body}")


def test_the_gate_step_does_not_swallow_the_counters_exit_code():
    """A step that reports and always exits 0 is the fail-open shape this
    repo has already been bitten by. The status is captured and re-raised."""
    steps = [s for s in _gate_steps() if "warden round count" in s.get("run", "")]
    body = "\n".join(s["run"] for s in steps)
    assert 'exit "$status"' in body, body
    for step in steps:
        assert step.get("continue-on-error") is not True, (
            "continue-on-error makes the count a report that cannot gate")


# --- round-1 repairs ----------------------------------------------------------


def test_f7_the_count_reads_the_pr_head_not_the_merge_ref(repo, tmp_path):
    """ROUND-1 F-7, the one that made the whole gate inert.

    `actions/checkout` on a `pull_request` event leaves `refs/pull/N/merge`
    checked out — a merge commit whose FIRST parent is the base tip and whose
    second is the PR head. `_added_shards` walks `--first-parent` and a merge
    contributes no diff, so a bare `rev-parse HEAD` walked the base line and
    the merge commit and never the branch's own commits: `from_shards` was 0
    for EVERY pull request, and the acceptance test above passed only because
    its fixture is checked out on the branch.

    Measured here in both directions on one fixture, which is what makes it a
    regression rather than an assertion: off the merge ref the range carries
    three rounds and the count must still refuse; the merge ref is proved to
    be a real one whose first parent is the base.
    """
    _shard(repo, "round-1.json", [1])
    _shard(repo, "round-2.json", [2])
    _shard(repo, "round-3.json", [3])
    pr_head = _sha(repo)
    # what the forge builds and actions/checkout leaves behind
    _git(repo, "checkout", "-q", "--detach", "main")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
         "merge", "-q", "--no-ff", "-m", "merge", pr_head)
    merge_ref = _sha(repo)
    parents = _git(repo, "rev-parse", f"{merge_ref}^1", f"{merge_ref}^2").split()
    assert parents == [_sha(repo, "main"), pr_head], (
        "the fixture no longer reproduces the trap: the merge ref's first "
        f"parent has to be the base tip, got {parents}")

    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"head": {"sha": pr_head}}}))
    blind = _cli(repo, "round", "count", "--base", "main", "--head", "HEAD")
    assert blind.returncode == 0, "the fixture stopped reproducing the trap"
    assert json.loads(blind.stdout)["from_shards"] == 0, (
        "a first-parent walk from the merge ref must see no shard at all — "
        "that is the defect")

    env = {**os.environ, "PYTHONPATH": str(ROOT),
           "GITHUB_EVENT_PATH": str(event)}
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; from warden import cli; "
         f"sys.exit(cli.main({['round', 'count', '--base', 'main']!r}))"],
        cwd=repo, capture_output=True, text=True, env=env)
    assert out.returncode == 1, out.stdout + out.stderr
    report = json.loads(out.stdout)
    assert report["head"] == pr_head and report["head"] != merge_ref
    assert report["capped_rounds"] == 3, report


def test_f7_an_explicit_head_still_wins_over_the_event(repo, tmp_path):
    """The env fallback is a DEFAULT, never an override: a caller that names
    a head gets that head, or a consumer could not count a range the event
    does not describe."""
    _shard(repo, "round-1.json", [1])
    named = _sha(repo)
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "after the shard")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"head": {"sha": _sha(repo)}}}))
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; from warden import cli; "
         f"sys.exit(cli.main({['round', 'count', '--base', 'main', '--head', named]!r}))"],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT),
             "GITHUB_EVENT_PATH": str(event)})
    assert out.returncode == 0, out.stdout + out.stderr
    assert json.loads(out.stdout)["head"] == named


@pytest.mark.parametrize("value", [False, 0, {}, [], "raised-new"])
def test_f3_a_closure_verification_outside_the_enum_is_not_exempt(repo, value):
    """ROUND-1 F-3. The exemption tests the VALUE, not the key's presence.

    The shipped schema types `closure_verification` as `enum: ["verified"]`
    and `attest.py` reads it that way, so a shard carrying `false`, `0`, `{}`
    or `"raised-new"` states a closure verification that did not happen. The
    existence test this used to be exempted such a shard from the cap on any
    of them — fail-closed's "existence checked, validity not", on the one
    field that switches the gate off. Measured before the fix: a shard
    stating round 9 read as `capped_rounds: 0` and exited 0 under a cap of 2.
    """
    (repo / ".warden" / "memory" / "attest").mkdir(parents=True, exist_ok=True)
    (repo / ".warden" / "memory" / "attest" / "odd.json").write_text(json.dumps(
        {"schema": 1, "source": "attest", "verdict": "clean",
         "closure_verification": value,
         "reviewers": [{"role": "code-reviewer", "returned": True,
                        "round": 9}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "shard: odd closure marker")
    out = _count(repo)
    assert out.returncode == 1, out.stdout + out.stderr
    report = json.loads(out.stdout)
    assert report["capped_rounds"] == 9 and report["closure"] == [], report


# --- the workflow step, after the round-1 repair -------------------------------


def _round_count_step() -> dict:
    steps = [s for s in _gate_steps() if "warden round count" in s.get("run", "")]
    assert len(steps) == 1, f"expected one round-count step, got {len(steps)}"
    return steps[0]


def test_f1_the_step_names_the_missing_witness_on_every_path():
    """ROUND-1 F-1. `github.event.before` rides on the `synchronize` activity
    type alone, and this workflow listens to four. On the other three the
    witness is simply absent — and the NOTE that named a missing one used to
    sit INSIDE the `[ -n "$BEFORE" ]` guard, so the one path where it is
    always missing said nothing at all, while the wiki claimed it was "named
    where one is not".

    Both paths now raise a `::warning::` annotation, which reaches the checks
    UI rather than only the step summary, and each says which witness is
    missing and what the run is blind to.
    """
    body = _round_count_step()["run"]
    assert body.count("::warning::") >= 2, (
        "a path where no witness could be obtained is silent again:\n" + body)
    assert "blind to a rewrite that dropped the shard commits" in body, (
        "the absent-witness warning no longer says what the run is blind to")
    assert "witness outside the branch's own history is missing" in body, (
        "the unfetchable-head warning no longer names the missing witness")
    # the empty-BEFORE branch must be the FIRST test, not an else inside a
    # guard that already required BEFORE to be non-empty
    assert '-z "${BEFORE:-}"' in body, body


def test_f7_the_step_passes_no_head_so_the_event_supplies_the_pr_head():
    """A `--head` on this step would be a second place to get the merge-ref
    trap wrong, and `rev-parse HEAD` is exactly the wrong answer on the event
    it runs under. The command takes the PR head from the Actions event."""
    body = _round_count_step()["run"]
    assert "--head" not in body, (
        "the step names a head; it must let the event supply the PR head, or "
        f"a bare HEAD is the merge ref and the floor is 0 for every PR:\n{body}")


# --- round-2 repairs ----------------------------------------------------------


def test_f8_a_prior_heads_closure_shard_is_reported_as_exempt_not_counted(
        repo):
    """ROUND-2 F-8. The exemption travels WITH the shard, wherever read.

    The round-1 repair carried a prior head's shards into the refusal and
    dropped that head's closure paths, so the CLI could not filter them: a
    shard the count had deliberately EXEMPTED was printed under "Rounds
    counted", and the next clause said none was counted. That is round-1
    F-5's own defect — a leading line that sends the author to the wrong
    shard — reintroduced at the other end of the same repair.

    The fixture is the one no earlier test built: the verdict comes from a
    prior head whose shards include a closure one, and the range carries
    nothing because the shard commits were dropped.
    """
    keep = _sha(repo)
    _shard(repo, "round-3.json", [3])
    _shard(repo, "closure.json", [5], closure=True)
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "more work")
    before = _sha(repo)
    _git(repo, "rebase", "-q", "--onto", keep, f"{before}~1", "feature")

    out = _count(repo, "--prior-head", before)
    assert out.returncode == 1, out.stdout + out.stderr
    report = json.loads(out.stdout)
    assert report["capped_rounds"] == 3, "the exemption itself must still hold"
    assert report["prior_heads"][0]["closure"] == [
        ".warden/memory/attest/closure.json"], report["prior_heads"]
    counted, _, exempt = out.stderr.partition("Closure shard(s) exempted:")
    assert "closure.json" not in counted, (
        f"an exempted shard is named as a round counted:\n{out.stderr}")
    assert "closure.json" in exempt, out.stderr
    assert "round-3.json" in counted, out.stderr
    assert "none was counted here" not in out.stderr, out.stderr


def test_f8_a_shard_both_the_range_and_the_witness_carry_is_named_once(repo):
    """The second symptom of the same loop. An ordinary push gives a witness
    that is an ANCESTOR, so it carries the range's shards too — and listing
    one path twice is another way to misdescribe the same evidence."""
    _shard(repo, "round-3.json", [3])
    before = _sha(repo)
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the ordinary next push")
    out = _count(repo, "--prior-head", before)
    assert out.returncode == 1, out.stdout + out.stderr
    # scoped to the LIST, not the whole stream: the disagreement note names
    # its own shards on purpose, and that is a different sentence.
    listing = out.stderr.split("Rounds counted: ")[-1]
    assert listing.count("round-3.json") == 1, out.stderr


def test_f9_the_step_warns_only_where_it_can_SEE_the_witness_is_missing():
    """ROUND-2 F-9. The two limits of `github.event.before` do not report
    alike, and claiming they do was the same enforcement-truth defect the
    round-1 repair was written to close.

    No `before` at all is a state the shell can test for, and it warns. A
    `before` that is present, fetchable and ITSELF already rewritten is
    indistinguishable from a witness that honestly agrees — so the second
    push after a drop is SILENT. This test holds the surfaces to that: the
    step warns on exactly the paths it can detect, and no page may promise a
    warning on the one it cannot.
    """
    body = _round_count_step()["run"]
    # exactly two detectable paths: no `before`, and a `before` that will not
    # resolve. Neither is "the witness was itself rewritten".
    assert body.count("::warning::") == 2, (
        "the step warns on a number of paths it cannot actually "
        f"distinguish, or has gone silent on one it can:\n{body}")
    for page in ("docs/wiki/Graph-Layer.md", "docs/wiki/CLI-Reference.md"):
        text = (ROOT / page).read_text()
        for claim in ("In both cases the step raises",
                      "naming the missing witness on either path",
                      "never narrowed by silence"):
            assert claim not in text, (
                f"{page} promises a ::warning:: on the one-push-deep path, "
                f"where the step raises none: {claim!r}")
    page = (ROOT / "docs" / "wiki" / "Graph-Layer.md").read_text()
    assert "second push after a drop is **silent**" in page, (
        "Graph-Layer no longer says the second push after a drop is silent")
