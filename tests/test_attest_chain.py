"""A round WITHIN the cap is held to the closure round's
claim identity.

`pre-pr-review` requires a carried finding to be re-filed VERBATIM in the next
round's payload: same `rule_id`, `file`, `line`, `severity` and finding text,
with only the lens, round, status, reason and evidence moving. `verify_closure`
enforced exactly that for the terminal closure round, and NOTHING enforced it
for a round within the cap.

The failure this refuses: a round-2 payload that files the
scoped-re-reviewer's VERDICT text (`"[F0] Addressed, both halves,
..."`) as each finding's text instead of re-filing round 1's claim. Under
`_claim_of`'s identity those are different claims, so round 1's claims are
never closed in the record — and without this check the break surfaces rounds
later at the closure round, where the remedy is re-filing everything the whole
chain left open. The refusal belongs to the round that made the mistake.

`test_a_restated_claim_in_round_2_is_refused_where_it_is_made` is that
reproduction, scoped to the round that made the mistake.
"""

import json
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_YAML, seed_rules
from warden import attest as attest_mod
from warden import runs as runs_mod

HEADLESS_ROLE = "scoped-re-reviewer"


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _finding(rule_id, file, status, *, finding="f", severity="MEDIUM", line=1):
    out = {"rule_id": rule_id, "severity": severity, "file": file,
           "line": line, "finding": finding, "evidence": "e",
           "status": status}
    if status in ("dismissed-with-reason", "refuted"):
        out["reason"] = "r"
    return out


def _shard(sha, base, rnd, records):
    return {"schema": 1, "source": "attest", "sha": sha, "base_sha": base,
            "rules_version": "v1", "reviewed_at": "2026-09-21T00:00:00+00:00",
            "verdict": "findings-open",
            "reviewers": [{"role": "code-reviewer", "agent": "subagent",
                           "round": rnd, "returned": True}],
            "records": [{**r, "round": rnd, "lens": "code-reviewer"}
                        for r in records]}


@pytest.fixture
def branch(tmp_path):
    """A branch whose ROUND 1 raised two findings and COMMITTED its shard.

    `docs-drift` on `a.py` and `stale-guard` on `b.py`, both `confirmed`. A
    repair commit follows, so head is where round 2 would be minted. This is
    the ordinary honest shape, and it is what a round-2 disposition is
    entitled to chain to.
    """
    root = tmp_path / "repo"
    root.mkdir()
    seed_rules(root, "docs-drift", "stale-guard", "scope-creep")
    # A valid policy, committed at the base: `warden attest write` loads it,
    # and it refuses a dirty tree, so it cannot be written later.
    (root / "repo.yaml").write_text(REPO_YAML)
    # Evidence run dirs are gitignored in a real enrolled repo; without this
    # a minted round dirties the tree and `attest write` exits 2.
    (root / ".gitignore").write_text(".warden/out/\n")
    shards = root / attest_mod.SHARD_DIR
    shards.mkdir(parents=True)
    (root / "a.py").write_text("1\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")

    (root / "b.py").write_text("1\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "work")
    head1 = _git(root, "rev-parse", "HEAD")
    (shards / "r1.json").write_text(json.dumps(_shard(head1, base, 1, [
        _finding("docs-drift", "a.py", "confirmed"),
        _finding("stale-guard", "b.py", "confirmed")])))
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "ops(memory): round 1")

    (root / "a.py").write_text("2\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "repair round 1")
    return root, base, _git(root, "rev-parse", "HEAD")


def _round_dir(root: Path, head: str, rnd: int) -> Path:
    d = root / ".warden" / "out" / "rounds" / f"round-{rnd}"
    reviewers = d / "reviewers"
    reviewers.mkdir(parents=True)
    (reviewers / f"{HEADLESS_ROLE}.json").write_text(
        json.dumps({"role": HEADLESS_ROLE, "round": rnd, "head": head}))
    runs_mod.write_round_manifest(d, base="main", base_sha="a" * 40, head=head,
                                  branch="work", round_no=rnd)
    return reviewers


def _payload(findings, *, rnd=2, verdict="clean"):
    return {
        "reviewers": [{"role": HEADLESS_ROLE, "agent": "subagent",
                       "round": rnd, "returned": True,
                       "findings": len(findings),
                       "output": f"{HEADLESS_ROLE}.json"}],
        "findings": [{**f, "lens": HEADLESS_ROLE, "round": rnd}
                     for f in findings],
        "verdict": verdict,
    }


def _build(root, base, head, payload, review_dir):
    return attest_mod.build(payload, head_sha=head, base_sha=base,
                            rules_version="v1",
                            rules_dir=root / ".warden" / "rules",
                            review_dir=review_dir, root=root)


# --- the reproduction ----------------------------------------------------------


def test_a_restated_claim_in_round_2_is_refused_where_it_is_made(branch):
    """THE LIVE BUG. Round 2 files a disposition whose text is the round's own
    VERDICT prose rather than round 1's claim.

    Everything else about the record looks right — same rule, same file, same
    line — and that is precisely what made it invisible: under
    `_claim_of`'s identity it is a different claim, so round 1's finding stays
    open in the record while the payload reads as having answered it.
    """
    root, base, head = branch
    rd = _round_dir(root, head, 2)
    payload = _payload([_finding("docs-drift", "a.py", "fixed",
                                 finding="[F0] Addressed, both halves, "
                                         "verified at round 2")])
    with pytest.raises(attest_mod.AttestError) as e:
        _build(root, base, head, payload, rd)
    message = str(e.value)
    assert "no earlier round committed" in message
    assert "docs-drift" in message and "a.py" in message
    assert "VERBATIM" in message, "the refusal does not name the rule broken"
    assert "re-file the earlier round's text, not this round's verdict prose" \
        in message, "the refusal does not name the remedy for THIS spelling"


def test_a_verbatim_re_file_in_round_2_is_accepted(branch):
    """The honest path the refusal must not touch: the SAME claim, re-filed
    with only its disposition moved."""
    root, base, head = branch
    rd = _round_dir(root, head, 2)
    payload = _payload([
        _finding("docs-drift", "a.py", "fixed"),
        _finding("stale-guard", "b.py", "dismissed-with-reason")])
    doc = _build(root, base, head, payload, rd)
    assert doc["verdict"] == "clean"
    # Never stamped into the shard: this is a write-path refusal about the
    # payload, like `carry_forward`, and `warden/schemas` stays untouched.
    assert "chain_verification" not in doc


def test_a_new_confirmed_finding_in_round_2_is_not_a_broken_chain(branch):
    """A round within the cap RAISES. The whole difference from the closure
    round is that round 2 may find something new. Demanding a prior claim
    for every record would refuse the protocol's own golden path — so the rule binds only the statuses that
    ASSERT an earlier round found this.
    """
    root, base, head = branch
    rd = _round_dir(root, head, 2)
    payload = _payload([_finding("scope-creep", "c.py", "confirmed")],
                       verdict="findings-open")
    doc = _build(root, base, head, payload, rd)
    assert doc["findings"][0]["status"] == "confirmed"


def test_round_1_has_nothing_to_chain_to_and_is_exempt(branch):
    """Round 1 may arrive `fixed` — a reviewer can read a diff that already
    contains the repair — and there is no earlier round for it to re-file."""
    root, base, head = branch
    rd = _round_dir(root, head, 1)
    payload = _payload([_finding("scope-creep", "c.py", "fixed")], rnd=1)
    doc = _build(root, base, head, payload, rd)
    assert doc["verdict"] == "clean"


def test_an_uncommitted_round_1_shard_is_named_as_the_other_remedy(
        branch, tmp_path):
    """Two causes, two remedies, and the message carries both.

    The claim can be restated, or the earlier round's shard can simply not be
    committed on this branch's first-parent line — in which case warden cannot
    see what round 1 raised at all. Sending a builder to re-word a finding
    that was in fact worded correctly costs a round they do not have.
    """
    root, base, head = branch
    # Round 1's shard removed from the branch's history, leaving the repair.
    _git(root, "rm", "-q", "-r", attest_mod.SHARD_DIR.as_posix())
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "drop the round-1 shard")
    head = _git(root, "rev-parse", "HEAD")
    rd = _round_dir(root, head, 2)
    payload = _payload([_finding("docs-drift", "a.py", "fixed")])
    with pytest.raises(attest_mod.AttestError) as e:
        _build(root, base, head, payload, rd)
    assert "`warden memory ingest` it and commit it" in str(e.value)


def test_a_claim_this_same_round_filed_cannot_license_its_own_disposition(
        branch):
    """The admissible set is rounds strictly BELOW this one.

    A round-2 record cannot be chained to a round-2 record: that is the record
    vouching for itself, and it is the shape a shard committed mid-round would
    hand back. `_closure_evidence` is called with `round_n - 1` for exactly
    this reason.
    """
    root, base, head = branch
    shards = root / attest_mod.SHARD_DIR
    (shards / "r2.json").write_text(json.dumps(_shard(head, base, 2, [
        _finding("scope-creep", "c.py", "confirmed")])))
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "ops(memory): a round-2 shard")
    head = _git(root, "rev-parse", "HEAD")
    rd = _round_dir(root, head, 2)
    payload = _payload([_finding("scope-creep", "c.py", "fixed")])
    with pytest.raises(attest_mod.AttestError) as e:
        _build(root, base, head, payload, rd)
    assert "scope-creep" in str(e.value)


def test_the_carried_statuses_are_the_three_that_answer_a_finding():
    """Pinned, because the rule's whole scope is this tuple.

    `confirmed` must stay OUT: warden cannot tell a new finding from a
    re-confirmed one by its status, so demanding a prior claim there would
    refuse honest round-2 work. Anything else moving in or out of this tuple
    changes which payloads are legal and has to be argued for in the diff.
    """
    assert attest_mod.CARRIED_STATUSES == (
        "fixed", "dismissed-with-reason", "refuted")
    assert "confirmed" not in attest_mod.CARRIED_STATUSES


def test_a_library_caller_with_no_root_is_reported_not_guessed(branch):
    """The check reads git history. A caller that cannot offer a root has not
    had the question asked, and `verify_chain` says so rather than returning a
    state that reads like a pass. `warden attest write` always passes
    `config.root`, so this is reachable only from a library caller.
    """
    _root, base, head = branch
    state = attest_mod.verify_chain(
        [_finding("docs-drift", "a.py", "fixed")],
        [{"role": HEADLESS_ROLE, "round": 2}],
        root=None, base_sha=base, head_sha=head, review_dir=None)
    assert state == "no-root"
    assert state != attest_mod.CHAIN_VERIFIED


# --- the gating value must not come from the party being checked --------------


def test_a_mislabelled_round_cannot_skip_the_chain_check(branch):
    """ROUND 1, F2. The first cut took N from the ROSTER alone, and the roster
    is written by the party being checked: labelling every entry `round: 1`
    drove `round_n <= 1` and the check skipped, silently, on the exact
    restated-claim payload it exists to refuse.

    In THIS repo that hole was closed by accident — `graph.yaml` declares a
    `review` block, so `verify_crew` pins the roster's highest round to the
    minted one and runs first. The reachable surface was a consumer with no
    `review` block, which is the configuration the first cut named as its
    REASON for reading the roster. `_chain_round` now takes the HIGHER of
    warden's own minted number and the roster's.
    """
    root, base, head = branch
    rd = _round_dir(root, head, 2)          # warden minted this as round 2
    payload = _payload([_finding("docs-drift", "a.py", "fixed",
                                 finding="[F0] Addressed, verified")], rnd=1)
    # ...and the roster says round 1, which is the lie.
    assert all(r["round"] == 1 for r in payload["reviewers"])
    with pytest.raises(attest_mod.AttestError) as e:
        _build(root, base, head, payload, rd)
    assert "round 2" in str(e.value), (
        "the refusal did not read warden's own minted round — a payload can "
        "skip the chain rule by labelling itself round 1")


def test_an_unminted_round_still_reads_the_roster(branch, tmp_path):
    """`minted_round` returns None for a directory warden did not mint, and
    there the roster's label is the only number there is.

    Taking the minted number ALONE would have swapped one silent skip for
    another. `round_binding` stamps `unminted` into the shard, so a reader is
    told which number carried the check.
    """
    root, base, head = branch
    unminted = tmp_path / "hand-rolled" / "reviewers"
    unminted.mkdir(parents=True)
    (unminted / f"{HEADLESS_ROLE}.json").write_text(
        json.dumps({"role": HEADLESS_ROLE, "round": 2, "head": head}))
    assert attest_mod.minted_round(unminted) is None
    payload = _payload([_finding("scope-creep", "c.py", "fixed")])
    with pytest.raises(attest_mod.AttestError) as e:
        _build(root, base, head, payload, unminted)
    assert "round 2" in str(e.value) and "scope-creep" in str(e.value)


def test_the_chain_state_reaches_the_run_manifest(branch, monkeypatch,
                                                       tmp_path, capsys):
    """ROUND 1, F6. `CHAIN_VERIFIED`'s comment claimed the state was "warden's
    own vocabulary for the run manifest", and nothing wrote it there: `build`
    dropped `verify_chain`'s return. The refusing state is observable (it
    raises); the NON-refusing states were not, and the unattended cage has no
    terminal to read them from.
    """
    from warden import cli as cli_mod
    root, base, head = branch
    rd = _round_dir(root, head, 2)
    findings = tmp_path / "payload.json"
    findings.write_text(json.dumps(
        _payload([_finding("docs-drift", "a.py", "fixed")])))
    monkeypatch.chdir(root)
    assert cli_mod.main(["attest", "write", "--findings", str(findings),
                         "--base", base, "--review-dir", str(rd)]) == 0
    capsys.readouterr()
    run_dir = max((root / ".warden" / "out").glob("*-attest"),
                  key=lambda d: d.name)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["chain_verification"] == attest_mod.CHAIN_VERIFIED, (
        f"the chain state did not reach the manifest: {manifest}")


def test_a_payload_with_no_review_dir_is_not_gated_by_the_chain_rule(
        branch, monkeypatch, tmp_path, capsys):
    """ROUND 2, R2-0. `build` asks the chain question only where a round dir
    was offered; the manifest re-derivation must not become a SECOND gate over
    the path `build` deliberately excludes.

    It did: the re-derivation shipped unconditional, so `attest write` with no
    `--review-dir` refused a document `build` had just accepted — a gate moved
    by a commit whose stated purpose was recording a state. The manifest says
    the question was not asked rather than leaving it indistinguishable from
    one that passed.
    """
    from warden import cli as cli_mod
    root, base, head = branch
    findings = tmp_path / "payload.json"
    payload = _payload([_finding("docs-drift", "a.py", "fixed",
                                 finding="a claim no earlier round filed")])
    # The legacy shape: no lens/round on the finding, none on the roster.
    for f in payload["findings"]:
        f.pop("lens"), f.pop("round")
    payload["reviewers"] = [{"role": "code-reviewer", "agent": "subagent"}]
    findings.write_text(json.dumps(payload))
    monkeypatch.chdir(root)
    assert cli_mod.main(["attest", "write", "--findings", str(findings),
                         "--base", base]) == 0, (
        "a payload with no --review-dir was refused by the chain rule, which "
        "`build` never applies there")
    capsys.readouterr()
    run_dir = max((root / ".warden" / "out").glob("*-attest"),
                  key=lambda d: d.name)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["chain_verification"] == "no-review-dir", (
        f"the unasked question is not distinguishable from a passed one: "
        f"{manifest.get('chain_verification')!r}")


def test_review_dir_is_a_required_keyword(branch):
    """ROUND 2, R2-2. `review_dir` shipped with a `None` default for one
    round, and omitting it sent `_chain_round` back to the roster alone —
    restoring the very skip F2 closed. A guard whose defence is opt-in has no
    defence, so the keyword has no default and a caller holding no directory
    passes `None` and says so.
    """
    import inspect
    sig = inspect.signature(attest_mod.verify_chain)
    param = sig.parameters["review_dir"]
    assert param.default is inspect.Parameter.empty, (
        "review_dir has a default again — omitting it silently returns "
        "`first-round` for a mislabelled round-2 payload")
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
