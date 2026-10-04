"""`warden attest write --review-dir` refuses a roster that is not the crew
graph.yaml declares for its round.

With a `review` block, the roles dispatched in round N must be exactly the
roles `warden graph crew --round N` resolves: a declared role missing from the
roster, or a role in it the round does not declare, refuses the write and
names each one. Without a review block nothing changes.
"""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from test_cli import _clone_fixture
from warden import attest as attest_mod
from warden import cli
from warden import graph as graph_mod
from warden import runs as runs_mod

HEAD, BASE = "b" * 40, "a" * 40
ROOT = Path(__file__).resolve().parents[1]


def _declared() -> dict:
    """This repo's live protocol as `graph.declared_crew` reads it — a
    LITERAL, held equal to the graph by
    `test_the_literal_crew_is_the_crew_graph_yaml_declares`.

    It stays a literal on purpose, and the reason is the ASSERTIONS, not the
    call sites: the checks below pin exact strings against this crew —
    `match="cap of 2"`, named roles in a refusal — so a fixture reading
    graph.yaml would take them red on any edit to the declared crew, whether
    or not that edit touched what the check is about. Twelve of the fourteen
    `build(...)` sites pass this dict through byte-for-byte; what they vary
    is the ROSTER and the minted round, not the crew. What the equality buys
    is that the literal cannot go stale — which is what it had done: the
    docstring said it read `graph.declared_crew` and nothing asserted the two
    agreed, so graph.yaml's roles could change and every test here kept
    passing against the stale copy.
    """
    return {"cap": 2, "rounds": {1: ["code-reviewer", "cross-examiner"],
                                 2: ["scoped-re-reviewer"]}}


def test_the_literal_crew_is_the_crew_graph_yaml_declares():
    """The binding `_declared`'s docstring used to only claim.

    Only `cap` and `rounds`, and the reason is SCOPE, not completeness.
    `verify_crew` also reads `closure["roles"]` (warden/attest.py:1678 and
    :1710) for a round past the cap, so this pair is not the whole of what
    the roster check reads. What it is, is the whole of what the within-cap
    roster assertions below vary. `declared_crew` returns four more keys —
    `lenses`, `closure`, `delegation` and `merge_authority` — and comparing
    the whole dict would redden here on a lens edit that cannot reach a
    roster.

    WHAT THAT LEAVES UNPINNED, stated rather than claimed away: `_declared`
    carries no `closure` key at all, while this repo's graph.yaml declares
    one, so the literal is already stale in the single uncompared field that
    does reach a roster. Deleting graph.yaml's `closure:` block leaves this
    module at 28 passed. `test_crew_a_round_past_the_cap_refuses` below pins
    `match="cap of 2"` against the LITERAL; driven with the live crew the
    same call raises "the round 3 roster is not the crew graph.yaml declares
    (closure-attestor)" instead, so that sibling pins a refusal the live
    protocol does not produce; that sibling is tracked as open work.
    """
    live = graph_mod.declared_crew(ROOT)
    assert live is not None, (
        f"{ROOT}/graph.yaml declares no review crew, so this module's "
        "literal describes nothing")
    literal = _declared()
    assert literal["cap"] == live["cap"], (
        f"`_declared` carries cap {literal['cap']} and graph.yaml declares "
        f"{live['cap']} — every cap assertion below is measured against a "
        "stale copy")
    assert literal["rounds"] == live["rounds"], (
        f"`_declared` carries rounds {literal['rounds']} and graph.yaml "
        f"declares {live['rounds']} — the roster checks below pass against "
        "a crew this repo no longer declares")


@pytest.fixture
def build(sample_repo):
    rules_dir = sample_repo / ".warden" / "rules"

    def _build(payload: dict, *, review_dir: Path, review_crew) -> dict:
        return attest_mod.build(payload, head_sha=HEAD, base_sha=BASE,
                                rules_version="v1", rules_dir=rules_dir,
                                review_dir=review_dir,
                                review_crew=review_crew)
    return _build


def _round(parent: Path, roles: list[str], rnd: int, head: str = HEAD,
           *, minted: int | None = None) -> Path:
    """A minted round holding one report per role. `minted` is the round
    number warden stamped into `round.json`, which the roster's own `round`
    labels are checked against; it defaults to the round the roles ran in,
    which is the honest case."""
    d = parent / f"round-{rnd}"
    reviewers = d / "reviewers"
    reviewers.mkdir(parents=True)
    for role in roles:
        (reviewers / f"{role.replace(':', '-')}.json").write_text(
            json.dumps({"role": role, "round": rnd, "head": head}))
    runs_mod.write_round_manifest(d, base="main", base_sha=BASE, head=head,
                                  branch="work",
                                  round_no=rnd if minted is None else minted)
    return reviewers


def _spanning(parent: Path, dispatches: list[tuple[str, int]], *,
              minted: int, head: str = HEAD) -> tuple[Path, dict]:
    """A round whose roster spans more than one round — the shape older
    shards carry, and the one no single-round fixture can exercise.

    Each report names its own dispatch, so no two are byte-identical (a
    carried-forward copy is refused separately), and the payload's roster
    entry for each dispatch names its own output file.
    """
    d = parent / f"round-span-{minted}"
    reviewers = d / "reviewers"
    reviewers.mkdir(parents=True)
    roster = []
    for role, rnd in dispatches:
        name = f"{role.replace(':', '-')}-r{rnd}.json"
        (reviewers / name).write_text(
            json.dumps({"role": role, "round": rnd, "head": head}))
        roster.append({"role": role, "agent": "subagent", "round": rnd,
                       "returned": True, "findings": 0, "output": name})
    runs_mod.write_round_manifest(d, base="main", base_sha=BASE, head=head,
                                  branch="work", round_no=minted)
    return reviewers, {"reviewers": roster, "findings": [],
                       "verdict": "clean"}


def _payload(roles: list[str], rnd: int) -> dict:
    return {
        "reviewers": [{"role": role, "agent": "subagent", "round": rnd,
                       "returned": True, "findings": 1,
                       "output": f"{role.replace(':', '-')}.json"}
                      for role in roles],
        "findings": [{"rule_id": "scope-creep", "severity": "LOW",
                      "file": "app/x.py", "line": 1, "finding": "f",
                      "evidence": "e", "status": "fixed",
                      "lens": roles[0], "round": rnd}],
        "verdict": "clean",
    }


# --- the check, through build ---------------------------------------------------


def test_crew_a_missing_role_refuses(build, tmp_path):
    rd = _round(tmp_path, ["code-reviewer"], 1)
    with pytest.raises(attest_mod.AttestError, match="missing") as exc:
        build(_payload(["code-reviewer"], 1), review_dir=rd,
              review_crew=_declared())
    assert "cross-examiner" in str(exc.value)


def test_crew_a_missing_lens_dispatch_refuses(build, tmp_path):
    crew = {"cap": 2, "rounds": {1: ["code-reviewer",
                                     "crew:consumer-blast-radius"]}}
    rd = _round(tmp_path, ["code-reviewer"], 1)
    with pytest.raises(attest_mod.AttestError,
                       match="crew:consumer-blast-radius"):
        build(_payload(["code-reviewer"], 1), review_dir=rd, review_crew=crew)


def test_crew_an_extra_undeclared_role_refuses(build, tmp_path):
    roles = ["code-reviewer", "cross-examiner", "crew:fail-closed"]
    rd = _round(tmp_path, roles, 1)
    with pytest.raises(attest_mod.AttestError, match="not declared") as exc:
        build(_payload(roles, 1), review_dir=rd, review_crew=_declared())
    assert "crew:fail-closed" in str(exc.value)


def test_crew_a_role_declared_for_another_round_refuses(build, tmp_path):
    roles = ["scoped-re-reviewer"]
    rd = _round(tmp_path, roles, 1)
    with pytest.raises(attest_mod.AttestError) as exc:
        build(_payload(roles, 1), review_dir=rd, review_crew=_declared())
    message = str(exc.value)
    assert "scoped-re-reviewer" in message and "code-reviewer" in message


def test_crew_a_round_past_the_cap_refuses(build, tmp_path):
    roles = ["scoped-re-reviewer"]
    rd = _round(tmp_path, roles, 3)
    with pytest.raises(attest_mod.AttestError, match="cap of 2"):
        build(_payload(roles, 3), review_dir=rd, review_crew=_declared())


def test_crew_the_refusal_message_names_each_role(build, tmp_path):
    roles = ["code-reviewer", "scoped-re-reviewer"]
    rd = _round(tmp_path, roles, 1)
    with pytest.raises(attest_mod.AttestError) as exc:
        build(_payload(roles, 1), review_dir=rd, review_crew=_declared())
    message = str(exc.value)
    assert "cross-examiner" in message          # the missing one
    assert "scoped-re-reviewer" in message      # the extra one
    assert "round 1" in message


def test_crew_a_matching_round_1_roster_writes(build, tmp_path):
    roles = ["code-reviewer", "cross-examiner"]
    rd = _round(tmp_path, roles, 1)
    doc = build(_payload(roles, 1), review_dir=rd, review_crew=_declared())
    assert [r["role"] for r in doc["reviewers"]] == roles


def test_crew_a_matching_round_2_roster_writes(build, tmp_path):
    roles = ["scoped-re-reviewer"]
    rd = _round(tmp_path, roles, 2)
    doc = build(_payload(roles, 2), review_dir=rd, review_crew=_declared())
    assert [r["role"] for r in doc["reviewers"]] == roles


# --- the round label, checked against the round warden minted -------------------


def test_crew_a_first_round_labelled_2_is_refused_naming_the_minted_round(
        build, tmp_path):
    """The crew check a builder could dodge by typing a different number.

    Round 2's declared crew is one role, so a first and only review labelled
    `round: 2` with the scoped re-reviewer alone used to write CLEAN,
    VERIFIED and BOUND at exit 0 — round 1's crew skipped entirely, and
    nothing downstream to catch it: `round.json` carried no round number, so
    the label was compared with nothing at all.
    """
    rd = _round(tmp_path, ["scoped-re-reviewer"], 2, minted=1)
    with pytest.raises(attest_mod.AttestError) as exc:
        build(_payload(["scoped-re-reviewer"], 2), review_dir=rd,
              review_crew=_declared())
    message = str(exc.value)
    assert "round 1" in message and "round 2" in message, message


def test_crew_a_roster_short_of_the_minted_round_is_refused(build, tmp_path):
    """The same equality from the other side: a roster whose highest round is
    1 in a round warden minted as 2 reviews the round BEFORE this one, so the
    round being attested was reviewed by nobody."""
    roles = ["code-reviewer", "cross-examiner"]
    rd = _round(tmp_path, roles, 1, minted=2)
    with pytest.raises(attest_mod.AttestError) as exc:
        build(_payload(roles, 1), review_dir=rd, review_crew=_declared())
    assert "round 2" in str(exc.value)


def test_crew_a_roster_spanning_two_rounds_with_a_wrong_round_2_crew_is_refused(
        build, tmp_path):
    """Every round the roster names is checked, not only the first.

    A roster spanning rounds is a real shape — older shards carry it, and the
    attestation schema says so — so a correct round 1 in front of a wrong
    round 2 must not carry the write. Cutting the per-round loop to its first
    round writes this payload.
    """
    rd, payload = _spanning(tmp_path,
                            [("code-reviewer", 1), ("cross-examiner", 1),
                             ("code-reviewer", 2)], minted=2)
    with pytest.raises(attest_mod.AttestError) as exc:
        build(payload, review_dir=rd, review_crew=_declared())
    message = str(exc.value)
    assert "round 2" in message
    assert "scoped-re-reviewer" in message   # the missing one
    assert "code-reviewer" in message        # the extra one


def test_crew_a_spanning_roster_matching_every_round_writes(build, tmp_path):
    rd, payload = _spanning(tmp_path,
                            [("code-reviewer", 1), ("cross-examiner", 1),
                             ("scoped-re-reviewer", 2)], minted=2)
    doc = build(payload, review_dir=rd, review_crew=_declared())
    assert [r["round"] for r in doc["reviewers"]] == [1, 1, 2]


def test_crew_a_round_with_no_minted_number_is_refused(build, tmp_path):
    """Fail-closed on absence. A `round.json` that names no round cannot say
    which round its reports describe, and reading that as "nothing to
    compare" would make deleting one key the new dodge."""
    roles = ["code-reviewer", "cross-examiner"]
    rd = _round(tmp_path, roles, 1)
    manifest = json.loads((rd.parent / "round.json").read_text())
    manifest.pop("round")
    (rd.parent / "round.json").write_text(json.dumps(manifest))
    # The message this refusal's OWN branch writes, not merely that the write
    # was refused: with the branch gone the mismatch check below it raises
    # too (a round label is never equal to a missing number), so a test
    # asserting only `raises`, or matching text both messages carry, cannot
    # see this guard go.
    with pytest.raises(attest_mod.AttestError,
                       match="no minted round number"):
        build(_payload(roles, 1), review_dir=rd, review_crew=_declared())


def test_crew_no_review_block_keeps_the_old_behaviour(build, tmp_path):
    roles = ["code-reviewer", "crew:fail-closed"]
    rd = _round(tmp_path, roles, 1)
    doc = build(_payload(roles, 1), review_dir=rd, review_crew=None)
    assert [r["role"] for r in doc["reviewers"]] == roles


# --- the CLI: graph.yaml is read from the repo ----------------------------------


def _enroll_crew(root: Path, *, review: bool, budget: int = 2,
                 light: bool = False) -> None:
    repo = yaml.safe_load((root / "repo.yaml").read_text())
    repo["repair"] = {"budget": budget}
    (root / "repo.yaml").write_text(yaml.safe_dump(repo, sort_keys=False))
    doc = {
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
    }
    if review:
        doc["review"] = {
            "rounds": [{"round": 1, "roles": ["code-reviewer",
                                              "cross-examiner"]},
                       {"round": 2, "roles": ["scoped-re-reviewer"]}],
            "cap": 2,
            "lenses": [{"id": "consumer-blast-radius",
                        "checklist": ["a shipped schema changed?"]}],
            "merge_authority": "founder",
        }
    if light:
        # Declared only where the caller asks for it, so the eight callers
        # that predate the light tier keep the graph they were written against.
        doc["nodes"]["claims-auditor"] = {"kind": "subagent", "impl": "c",
                                          "authority": "find"}
        doc["edges"].append({"from": "builder", "to": "claims-auditor",
                             "payload": "diff"})
        doc["review"]["light"] = {"roles": ["claims-auditor"],
                                  "payload": "claims-only"}
    (root / "graph.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    for args in (("add", "."), ("commit", "-q", "-m", "enroll crew")):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True)


def _head(root: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _attest(root: Path, tmp_path: Path, roles: list[str], rnd: int) -> int:
    rd = _round(tmp_path / "rounds", roles, rnd, head=_head(root))
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps(_payload(roles, rnd)))
    return cli.main(["attest", "write", "--findings", str(findings),
                     "--base", "main", "--review-dir", str(rd)])


def _attest_runs(root: Path) -> list[Path]:
    return sorted((root / ".warden" / "out").glob("*-attest"))


def _attest_no_dir(root: Path, tmp_path: Path, roles: list[str],
                   rnd: int = 1) -> int:
    """`warden attest write` with NO `--review-dir` — the call the light tier
    was reachable around by making."""
    findings = tmp_path / "findings-no-dir.json"
    findings.write_text(json.dumps(_payload(roles, rnd)))
    return cli.main(["attest", "write", "--findings", str(findings),
                     "--base", "main"])


# --- the light crew cannot be reached by omitting an argument -------------------
#
# `verify_crew` — and so the light tier's inertness proof — used to run only
# inside the `--review-dir` branch, and the crew it compares against was only
# resolved there too. A roster naming the light crew and nothing else, written
# without the flag, was therefore held to NO proof: the tier whose whole
# premise is that eligibility is recomputed and never asserted was reachable by
# leaving an argument off. These drive the COMMAND, not `build`, because the
# hole had two halves and only one of them is inside `build` — the other is
# whether `_cmd_attest` resolves the declared crew at all on that path, and a
# test that hands `build` a crew by keyword cannot see it.


def test_crew_cli_refuses_a_light_roster_written_with_no_review_dir(
        sample_repo, tmp_path, monkeypatch, capsys):
    """THE HOLE, at the exact call that opened it. Exit 2, and the refusal
    names both halves of the remedy — the mint AND the flag that was left
    off — because "mint a round" alone tells a caller half of why this
    roster is the one roster no write may assert."""
    root = _clone_fixture(sample_repo, tmp_path)
    _enroll_crew(root, review=True, light=True)
    monkeypatch.chdir(root)
    assert _attest_no_dir(root, tmp_path, ["claims-auditor"]) == 2
    err = capsys.readouterr().err
    assert "light crew" in err and "claims-auditor" in err
    assert "no minted round number" in err
    assert "--review-dir" in err, (
        "the refusal names the mint but not the flag, so a caller who adds a "
        "round dir still does not learn why this roster needed one")
    assert _attest_runs(root) == [], "a refused light roster wrote a run dir"


def test_crew_cli_still_writes_an_ordinary_roster_with_no_review_dir(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The contract change is the light branch and nothing else. Every other
    roster still writes without a round dir and is stamped as the asserted
    thing it is — moving when the whole of `verify_crew` runs would instead
    hold every existing caller to a round number only a round dir carries."""
    root = _clone_fixture(sample_repo, tmp_path)
    _enroll_crew(root, review=True, light=True)
    monkeypatch.chdir(root)
    code = _attest_no_dir(root, tmp_path, ["code-reviewer"])
    assert code == 0, capsys.readouterr().err
    doc = json.loads(
        (_attest_runs(root)[0] / "attestation.json").read_text())
    assert doc["roster_verification"] == attest_mod.ROSTER_UNVERIFIED
    assert doc["round_binding"] == "no-review-dir"


def test_crew_cli_a_light_roster_with_no_light_round_declared_is_unaffected(
        sample_repo, tmp_path, monkeypatch, capsys):
    """With no `review.light` there is no light crew to recognise, so
    `claims-auditor` is just a role and the no-review-dir write is the
    behaviour every repo had before the tier."""
    root = _clone_fixture(sample_repo, tmp_path)
    _enroll_crew(root, review=True, light=False)
    monkeypatch.chdir(root)
    assert _attest_no_dir(root, tmp_path, ["claims-auditor"]) == 0, \
        capsys.readouterr().err


def test_crew_cli_no_review_dir_refuses_a_review_block_that_cannot_resolve(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The stated contract change, pinned so a later narrowing cannot make it
    silently again.

    `_cmd_attest` resolves the declared crew on BOTH paths now, because
    `build` cannot recognise a light roster without the declaration that
    names it. The cost is here: a graph.yaml that DECLARES a review block and
    does not resolve now refuses a write that carries no `--review-dir`,
    where it used to stamp `unverified-roster` at exit 0. Fail-closed, and the
    same direction the `--review-dir` path has always taken. Which boundary
    this seam should draw — this, or `warden ship`'s narrower
    `graph.declares_review` question — is an open decision; what this
    pins is that the answer is a decision and not a drift.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    _enroll_crew(root, review=True, budget=1)   # cap 2 vs budget 1: declared,
    monkeypatch.chdir(root)                     # and does not resolve
    assert _attest_no_dir(root, tmp_path, ["code-reviewer"]) == 2
    assert "review crew cannot be resolved" in capsys.readouterr().err
    assert _attest_runs(root) == []


def test_crew_cli_refuses_a_roster_missing_a_declared_role(
        sample_repo, tmp_path, monkeypatch, capsys):
    root = _clone_fixture(sample_repo, tmp_path)
    _enroll_crew(root, review=True)
    monkeypatch.chdir(root)
    assert _attest(root, tmp_path, ["code-reviewer"], 1) == 2
    assert "cross-examiner" in capsys.readouterr().err
    assert _attest_runs(root) == [], "a refused roster wrote a run dir"


def test_crew_cli_writes_a_matching_roster(sample_repo, tmp_path, monkeypatch,
                                           capsys):
    root = _clone_fixture(sample_repo, tmp_path)
    _enroll_crew(root, review=True)
    monkeypatch.chdir(root)
    code = _attest(root, tmp_path, ["code-reviewer", "cross-examiner"], 1)
    assert code == 0, capsys.readouterr().err
    assert len(_attest_runs(root)) == 1


def test_crew_cli_without_a_review_block_keeps_the_old_behaviour(
        sample_repo, tmp_path, monkeypatch, capsys):
    root = _clone_fixture(sample_repo, tmp_path)
    _enroll_crew(root, review=False)
    monkeypatch.chdir(root)
    code = _attest(root, tmp_path, ["code-reviewer"], 1)
    assert code == 0, capsys.readouterr().err


def test_crew_cli_refuses_when_the_review_block_cannot_be_resolved(
        sample_repo, tmp_path, monkeypatch, capsys):
    """A declared crew that does not resolve (here its cap disagrees with
    repair.budget) refuses the write by name, with no traceback and no run
    dir: the check was declared and could not run."""
    root = _clone_fixture(sample_repo, tmp_path)
    _enroll_crew(root, review=True, budget=1)
    monkeypatch.chdir(root)
    assert _attest(root, tmp_path, ["code-reviewer", "cross-examiner"], 1) == 2
    err = capsys.readouterr().err
    assert "cannot be resolved" in err and "repair.budget" in err
    assert "Traceback" not in err
    assert _attest_runs(root) == [], "an unresolvable crew wrote a run dir"


# --- the number `warden round new` mints, through the CLI -----------------------


def _mint_cli(capsys) -> Path:
    """The round directory `warden round new` minted, by the path it prints.

    The buffer is drained first: an earlier step's `attest write` summary is
    still in it otherwise, and this asserts that stdout carries the path and
    NOTHING else — the property that lets a caller write
    `ROUND="$(warden round new ...)"`.
    """
    capsys.readouterr()
    code = cli.main(["round", "new", "--base", "main"])
    out = capsys.readouterr()
    assert code == 0, out.err
    printed = [line for line in out.out.splitlines() if line.strip()]
    assert len(printed) == 1, f"stdout is not just the path: {out.out!r}"
    return Path(printed[0])


def _minted_number(round_dir: Path) -> int:
    return json.loads((round_dir / "round.json").read_text())["round"]


def _reports(reviewers: Path, roles: list[str], rnd: int, head: str) -> None:
    for role in roles:
        (reviewers / f"{role.replace(':', '-')}.json").write_text(
            json.dumps({"role": role, "round": rnd, "head": head}))


def _attest_minted(tmp_path: Path, reviewers: Path, roles: list[str],
                   rnd: int) -> int:
    findings = tmp_path / f"findings-round-{rnd}.json"
    findings.write_text(json.dumps(_payload(roles, rnd)))
    return cli.main(["attest", "write", "--findings", str(findings),
                     "--base", "main", "--review-dir", str(reviewers)])


def _commit(root: Path, message: str) -> None:
    for args in (("add", "-A"),
                 ("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "-m", message)):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True)


def _ingest_and_commit(root: Path, capsys) -> None:
    """The step between two rounds: `memory ingest`, then commit the SHARD.

    The shard alone — `.warden/memory/findings.jsonl` is a derived cache that
    `warden enroll` gitignores, and staging it would make the ingest commit
    carry content. Output is drained so a later `capsys.readouterr()` in the
    test still reads what the test is about.
    """
    assert cli.main(["memory", "ingest"]) == 0
    capsys.readouterr()
    for args in (("add", attest_mod.SHARD_DIR.as_posix()),
                 ("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-q", "-m", "ops(memory): commit the shard")):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True)


def test_crew_cli_a_minted_round_1_takes_its_own_crew_and_writes(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The honest case, end to end: warden mints round 1, the round-1 crew
    reviews it, and the roster labelled 1 writes."""
    root = _clone_fixture(sample_repo, tmp_path)
    _enroll_crew(root, review=True)
    monkeypatch.chdir(root)
    minted = _mint_cli(capsys)
    assert _minted_number(minted) == 1
    roles = ["code-reviewer", "cross-examiner"]
    _reports(minted / "reviewers", roles, 1, _head(root))
    code = _attest_minted(tmp_path, minted / "reviewers", roles, 1)
    assert code == 0, capsys.readouterr().err
    assert len(_attest_runs(root)) == 1


def test_crew_cli_refuses_a_first_round_labelled_2(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The dodge, end to end. Round 2's declared crew is the scoped
    re-reviewer alone, so relabelling the FIRST round `2` skipped both the
    code-reviewer and the cross-examiner and still wrote CLEAN."""
    root = _clone_fixture(sample_repo, tmp_path)
    _enroll_crew(root, review=True)
    monkeypatch.chdir(root)
    minted = _mint_cli(capsys)
    _reports(minted / "reviewers", ["scoped-re-reviewer"], 2, _head(root))
    assert _attest_minted(tmp_path, minted / "reviewers",
                          ["scoped-re-reviewer"], 2) == 2
    err = capsys.readouterr().err
    assert "round 1" in err and "round 2" in err, err
    assert _attest_runs(root) == [], "a relabelled round wrote a run dir"


def test_crew_cli_mints_2_for_the_second_round_and_its_crew_writes(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The second round on the same branch is minted as round 2, and round
    2's declared crew — the scoped re-reviewer alone — writes against it."""
    root = _clone_fixture(sample_repo, tmp_path)
    _enroll_crew(root, review=True)
    monkeypatch.chdir(root)
    first = _mint_cli(capsys)
    assert _minted_number(first) == 1
    roles = ["code-reviewer", "cross-examiner"]
    _reports(first / "reviewers", roles, 1, _head(root))
    assert _attest_minted(tmp_path, first / "reviewers", roles, 1) == 0
    # Round 1's shard, ingested and COMMITTED — one shard per round, which is
    # what the protocol has always said and what the chain rule
    # now needs: round 2's payload disposes of round 1's claim as `fixed`, and
    # a disposition may only chain to a claim a committed earlier round filed.
    _ingest_and_commit(root, capsys)
    # A round never survives a commit, so the repair moves head and the next
    # round is minted for it.
    (root / "app" / "x.py").write_text("x = 2\n")
    _commit(root, "repair")
    second = _mint_cli(capsys)
    assert _minted_number(second) == 2
    _reports(second / "reviewers", ["scoped-re-reviewer"], 2, _head(root))
    code = _attest_minted(tmp_path, second / "reviewers",
                          ["scoped-re-reviewer"], 2)
    assert code == 0, capsys.readouterr().err


def test_crew_cli_a_round_from_before_the_base_does_not_raise_the_number(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The count is the chain's, not the directory listing's.

    Round directories accumulate under `.warden/out/rounds/` across branches,
    and a round minted before this change's base is not a round OF this
    change. Counting it would mint round 2 for a first review and demand the
    wrong crew — so only rounds inside `base..head` count.
    """
    root = _clone_fixture(sample_repo, tmp_path)
    _enroll_crew(root, review=True)
    monkeypatch.chdir(root)
    base_sha = subprocess.run(["git", "rev-parse", "main"], cwd=root,
                              check=True, capture_output=True,
                              text=True).stdout.strip()
    earlier = root / ".warden" / "out" / "rounds" / "an-earlier-branch"
    (earlier / "reviewers").mkdir(parents=True)
    runs_mod.write_round_manifest(earlier, base="main", base_sha=base_sha,
                                  head=base_sha, branch="earlier", round_no=1)
    assert _minted_number(_mint_cli(capsys)) == 1


@pytest.mark.parametrize("bad", ["2", 2.0, 0, -1, True],
                         ids=["string", "float", "zero", "negative", "bool"])
def test_crew_a_minted_round_number_warden_cannot_read_is_refused(tmp_path,
                                                                  bad):
    """The READ side of the same number, driven with every shape that is not
    a round.

    `minted_round` is what the roster's `round` labels are compared against.
    A value in the wrong shape is not a missing one: read as missing it would
    return None, and under a declared crew `verify_crew` would then refuse
    with "no minted round number" — a DIFFERENT refusal, naming a round that
    was never minted rather than a manifest warden cannot read. `True` is the
    sharp shape: a plain int check passes it, and the roster would then be
    measured against round `true`.
    """
    d = tmp_path / "round-unreadable"
    reviewers = d / "reviewers"
    reviewers.mkdir(parents=True)
    (d / "round.json").write_text(json.dumps(
        {"schema": 1, "round_id": d.name, "base": "main", "base_sha": BASE,
         "head_sha": HEAD, "branch": "work", "round": bad}))
    with pytest.raises(attest_mod.AttestError,
                       match="not a round number as warden reads one"):
        attest_mod.minted_round(reviewers)


# --- the two crew boundaries, one fixture set -----------------------------------

#: A graph.yaml per way a document can fail to answer "what is this repo's
#: crew", with what each seam is RULED to do with it. The ruling is recorded as
#: a decision shard; this is the fixture set the acceptance asked for, and the
#: point of driving both seams over one set is that the divergence is then a
#: decided asymmetry rather than something a reader has to infer from one seam.
#:
#: `ship_raises` — whether `warden ship`'s roster step can even ask the
#: question. `attest_exit` — what `warden attest write --review-dir` returns.
_GRAPH_SHAPES = [
    # Bytes that are not UTF-8. Neither seam can read the keys of a document it
    # cannot decode, so both refuse and they AGREE.
    ("undecodable", b"\xff\xfe\x00nodes:\n", True, 2),
    # YAML that does not parse. Same: no keys to ask for.
    ("unparseable", b"nodes: [\n", True, 2),
    # PARSES, carries no `review` key, and this schema rejects it. THE
    # DIVERGENCE, and it is the ruling: ship asks only whether a crew is
    # CLAIMED and ships a repo that claims none, because enforcement must not
    # land on a repo that made no claim. `--review-dir` is a caller ASKING for
    # the roster to be cross-checked, so a document warden cannot validate
    # means the check the caller asked for cannot be performed, and that is
    # exit 2 rather than a pass.
    ("schema-rejected-and-declares-no-review", b"version: 1\nnodes: {}\n",
     False, 2),
    # A MISSPELLED review key, which is the commonest way INTO the case above
    # rather than a case of its own, and measuring it is what established that.
    # The schema sets `additionalProperties: false`, so `reveiw:` does not read
    # as a valid document with a key missing — it reads as a document the schema
    # rejects. So: the same divergence, reached by a typo.
    ("misspelled-review-key", None, False, 2),
]


@pytest.mark.parametrize("shape,raw,ship_raises,attest_exit", _GRAPH_SHAPES,
                         ids=[s for s, _, _, _ in _GRAPH_SHAPES])
def test_both_crew_seams_are_pinned_on_one_graph_fixture_set(
        sample_repo, tmp_path, monkeypatch, shape, raw, ship_raises,
        attest_exit):
    """`warden ship` and `warden attest write --review-dir` ask DIFFERENT
    questions of graph.yaml, on purpose, and both are pinned here.

    THE RULING, recorded as a decision shard rather than left to be inferred
    from whichever seam a reader met first:

    - `warden ship` asks whether a crew is CLAIMED, off the document's keys. It
      must not refuse a whole ship tail over a graph.yaml that declares no crew
      at all: that is enforcement landing on a repo that made no claim, and it
      cost a consumer every run including `--checks-only`.
    - `warden attest write --review-dir` asks what the crew IS, which cannot be
      answered without validating the document. The FLAG is the caller asking
      for the roster to be cross-checked, so "warden cannot perform the check
      you asked for" must be exit 2 and never a pass. Fail-closed is not a
      preference on a seam whose whole output is evidence that a review ran.

    So the asymmetry is deliberate, and it is narrow: it shows up only for a
    document that PARSES, declares no crew, and this schema rejects. Nothing is
    blocked by it — `attest write` without the flag still writes, stamping
    `unverified-roster`, which is the honest record of a roster nothing checked.

    WITHOUT THIS TEST a later refactor could move either boundary in either
    direction with nothing going red, which is how the seams came to differ
    unnoticed in the first place.
    """
    from warden import config as config_mod
    from warden import ship as ship_mod

    root = _clone_fixture(sample_repo, tmp_path / shape)
    _enroll_crew(root, review=True)
    graph = root / graph_mod.GRAPH_FILENAME
    if raw is None:
        doc = yaml.safe_load(graph.read_text())
        doc["reveiw"] = doc.pop("review")
        graph.write_text(yaml.safe_dump(doc, sort_keys=False))
    else:
        graph.write_bytes(raw)
    # COMMITTED, so the only thing distinguishing these four cases is the
    # graph. An uncommitted edit makes `attest write` exit 2 on the dirty-tree
    # check before it ever reaches the crew question, which would have read as
    # the roster boundary refusing and pinned nothing about it.
    for args in (("add", "."), ("commit", "-q", "-m", shape)):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True)
    monkeypatch.chdir(root)

    # The ship seam. `_declared_crew` runs before `check_range`, so a graph it
    # cannot ask raises out of the step rather than returning a failed check.
    cfg = config_mod.load(root)
    if ship_raises:
        with pytest.raises(ship_mod.ShipError):
            ship_mod._declared_crew(cfg)
    else:
        assert ship_mod._declared_crew(cfg) is None, (
            f"a {shape} graph.yaml now reads as DECLARING a crew to ship, so "
            "the narrow question is no longer being asked and a repo that "
            "claims no crew can have its whole ship tail refused")

    # The attest seam, driven as the COMMAND: the boundary is in
    # `_cmd_attest`'s branch, not in `build`, and a call that hands `build` a
    # crew by keyword cannot see it.
    assert _attest(root, tmp_path / f"{shape}-round", ["code-reviewer",
                                                      "cross-examiner"],
                   1) == attest_exit, (
        f"`attest write --review-dir` over a {shape} graph.yaml no longer "
        f"exits {attest_exit}. The flag is the caller asking for the roster to "
        "be cross-checked; a document warden cannot validate means that check "
        "cannot be performed, and a pass there records a roster nothing read "
        "as one that was read")


def test_the_fixture_set_covers_the_shapes_the_ruling_names():
    """The count, because the ruling is only as good as the set it was taken
    over: a shape deleted with the behaviour it pins leaves the rest green."""
    assert [s for s, _, _, _ in _GRAPH_SHAPES] == [
        "undecodable", "unparseable", "schema-rejected-and-declares-no-review",
        "misspelled-review-key"], (
        "a graph shape left the set; the ruling weighed these four, so adding "
        "or removing one is a re-decision and belongs in a decision shard")
    diverge = [s for s, _, sr, ae in _GRAPH_SHAPES if sr != (ae == 2)]
    assert diverge == ["schema-rejected-and-declares-no-review",
                       "misspelled-review-key"], (
        f"the seams now diverge on {diverge}. The ruling admits ONE divergence "
        "CLASS — a document that parses, carries no `review` key and fails the "
        "schema — and both shapes above are in it: the schema sets "
        "`additionalProperties: false`, so a misspelled key is a schema "
        "rejection rather than a valid document with a key missing. A third "
        "shape here is an undecided boundary, not a wider version of a "
        "decided one")
