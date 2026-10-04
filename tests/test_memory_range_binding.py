"""Ingest applies the range binding at the seam where it already re-checks
rule_ids.

THE GAP. `attest write` binds a findings set to the range it claims to be
about: a set naming no file the attested range changed is a real signal that
the findings file is stale — a builder's write failed silently and left an
EARLIER round's findings at the same scratchpad path. That check lives beside
`attest write`. `memory ingest` never calls `attest.build`; it reads an
artifact out of `.warden/out/*-attest/` and turns it into a committed shard
directly, and it re-applies the SIBLING invariant at that seam ("an id attest
would refuse must not become a corpus key just because it arrived through the
reader"). Without the same treatment for the range binding, an artifact that
never passed `build` would shard with no binding at all.

WHAT IT MUST NOT DO: refuse. A REFUSAL here — "does not become a shard" —
would reinstate at the reader boundary the refusal deliberately not made at
the writer boundary, where a backtest over the committed shards shows
findings-bearing attestations that would be wrongly refused
(`rename-complete` and `wiki-fidelity` produce out-of-range findings BY
CONSTRUCTION). And it would be strictly worse there:
`attest write` refuses at review time when the author can still react; ingest
refuses at corpus time, when the review is done and the only way through is
deleting a true finding. So ingest computes the binding at the same strength
the writer has — it WARNS, and it never refuses a shard on this basis.
"""

import json
import subprocess
from pathlib import Path

from conftest import seed_rules

from warden import memory as memory_mod


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def repo_with_range(tmp_path: Path, *, touched: str, base_content: str = "x\n"):
    """A real repo with a real base..head that changed exactly `touched`."""
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "seed.txt").write_text("seed\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    base = _git(root, "rev-parse", "HEAD").strip()
    target = root / touched
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(base_content)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "head")
    head = _git(root, "rev-parse", "HEAD").strip()
    return root, base, head


def finding(file: str) -> dict:
    return {"rule_id": "scope-creep", "severity": "MEDIUM", "file": file,
            "line": 1, "finding": "f", "evidence": "e", "status": "confirmed"}


def write_attest(root: Path, stamp: str, doc: dict) -> None:
    d = root / ".warden" / "out" / f"{stamp}-attest"
    d.mkdir(parents=True)
    (d / "attestation.json").write_text(json.dumps(doc) + "\n")


def attestation(base: str, head: str, findings: list[dict]) -> dict:
    return {"head_sha": head, "base_sha": base, "rules_version": "v1",
            "reviewed_at": "2026-09-01T01:00:00+00:00",
            "reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
            "findings": findings, "verdict": "findings-open"}


def ingest(root: Path) -> dict:
    return memory_mod.ingest(
        root, rules_dir=seed_rules(root, "scope-creep", "secrets-in-diff"))


# ---------- the named regression ------------------------------------------


def test_an_out_of_range_artifact_still_shards_and_the_binding_says_so(
        tmp_path):
    """The artifact's findings name a file its own base..head never
    touched, the shard is filed ANYWAY, and the recorded binding says the
    findings are out of range."""
    root, base, head = repo_with_range(tmp_path, touched="src/touched.py")
    write_attest(root, "20260901T010000Z",
                 attestation(base, head, [finding("src/never_touched.py")]))
    result = ingest(root)

    assert len(result["new_shards"]) == 1, (
        "ingest refused a shard over the range binding — the founder ruled "
        "this a WARNING at the writer, and refusing at the reader is strictly "
        f"worse: {result}")
    bindings = result["range_bindings"]
    assert len(bindings) == 1, bindings
    assert bindings[0]["kind"] == "out-of-range", bindings
    assert "src/never_touched.py" in bindings[0]["message"], bindings
    assert "20260901T010000Z-attest" in bindings[0]["artifact"], bindings


def test_findings_inside_the_range_read_ok_and_warn_about_nothing(tmp_path):
    """The other polarity, so the warning is not a constant. An in-range
    findings set is the normal case: it is READ (the row exists, so an
    auditor can tell a checked sweep from an unchecked one) and it carries no
    message, so nothing is printed."""
    root, base, head = repo_with_range(tmp_path, touched="src/touched.py")
    write_attest(root, "20260901T010000Z",
                 attestation(base, head, [finding("src/touched.py")]))
    result = ingest(root)
    assert len(result["new_shards"]) == 1, result
    assert [b["kind"] for b in result["range_bindings"]] == ["ok"], (
        result["range_bindings"])
    assert result["range_bindings"][0]["message"] == "", (
        "an `ok` reading carried a warning message")


def test_a_range_that_cannot_be_resolved_reads_unresolved_not_empty(
        tmp_path):
    """The shas may be long gone by ingest time — squash-merged, amended, or in
    CI's shallow clone. `sha_reachable` already owns that judgment and ingests
    anyway; a range this check cannot resolve is recorded as `unresolved` and
    carries no warning. Reporting it as "changed no files" would send every
    reader of an old artifact hunting a stale findings file that is fine
    (error-names-cause), and recording nothing at all would make an unreadable
    sweep indistinguishable from a clean one.

    The HEAD sha is real and the BASE sha is not, on purpose: with both bogus,
    `sha_reachable` drops the artifact as unreachable and it never reaches
    this check at all — the test would pass without exercising the thing it
    names (neutering the DiffError arm would leave it green).
    """
    root, _base, head = repo_with_range(tmp_path, touched="src/touched.py")
    write_attest(root, "20260901T010000Z",
                 attestation("a" * 40, head, [finding("src/x.py")]))
    result = ingest(root)
    assert len(result["new_shards"]) == 1, (
        "the artifact never reached the binding check: " + repr(result))
    assert [b["kind"] for b in result["range_bindings"]] == ["unresolved"], (
        "an unresolvable range produced a reading it could not have derived: "
        f"{result['range_bindings']}")
    assert result["range_bindings"][0]["message"] == "", (
        "an unresolvable range printed a warning about a range nobody read")


def test_a_findings_free_attestation_warns_about_nothing(tmp_path):
    """A clean review names no files, so it has zero overlap by construction —
    warning here would train the reader to ignore the warning. It still gets a
    READING (`ok`), because the row is what an auditor reads; what it must not
    get is a message. Held at the ingest seam too, not only inside
    `range_binding`, because a findings-free attestation takes a DIFFERENT path
    through ingest (it is filed as a review EVENT with no records)."""
    root, base, head = repo_with_range(tmp_path, touched="src/touched.py")
    doc = attestation(base, head, [])
    doc["verdict"] = "clean"
    write_attest(root, "20260901T010000Z", doc)
    result = ingest(root)
    assert len(result["new_shards"]) == 1, result
    assert [b["kind"] for b in result["range_bindings"]] == ["ok"], (
        result["range_bindings"])
    # The property is `attest.range_binding`'s ("a clean review is still a
    # review") and it is enforced THERE: ingest deliberately keeps no findings
    # short-circuit of its own. A second one would make this test unfailable —
    # each implementation would cover the other's mutation, so the test would
    # prove nothing about either. It is over-determined inside `range_binding`
    # too (the empty-findings arm and the empty-`named` arm both answer "ok"),
    # so the mutation proof for this one removes BOTH arms at once — which
    # does turn this red.


def test_an_empty_range_is_named_as_its_own_cause(tmp_path):
    """A range that changed nothing has a different cause and a different
    remedy from a stale findings file, and `attest.range_binding` already
    separates them. Ingest must carry the kind it was given rather than
    re-deriving a second classification beside it."""
    root, _base, head = repo_with_range(tmp_path, touched="src/touched.py")
    write_attest(root, "20260901T010000Z",
                 attestation(head, head, [finding("src/touched.py")]))
    result = ingest(root)
    assert len(result["new_shards"]) == 1, result
    assert [b["kind"] for b in result["range_bindings"]] == ["empty-range"], (
        result["range_bindings"])


def test_the_binding_is_not_carried_into_the_committed_shard(tmp_path):
    """THE RUN DIR IS WHERE THIS SIGNAL ENDS.
    The attestation schema declares no field for it and ingest builds its
    envelope from a fixed field list, so `memory stats` and the retro cannot
    count how often it fires — and no doc may say they can. A field with zero
    consumers is a defect of its own, so this guard holds the ABSENCE
    deliberately."""
    root, base, head = repo_with_range(tmp_path, touched="src/touched.py")
    write_attest(root, "20260901T010000Z",
                 attestation(base, head, [finding("src/never_touched.py")]))
    result = ingest(root)
    shard = json.loads((root / ".warden" / "memory" / "attest"
                        / result["new_shards"][0]).read_text())
    assert "range_binding" not in shard, shard
    assert all("range_binding" not in r for r in shard["records"]), shard


def test_c1_an_option_shaped_sha_never_reaches_git(tmp_path):
    """`base_sha` and `head_sha` are read verbatim out of a session-writable
    artifact that never passed `attest write` — that is why this check exists —
    and `git diff` parses a leading `-` as an OPTION, not a revision. A
    `base_sha` of `--output=<path>` makes git exit 0, CREATE a file, and print
    nothing, which this seam would then read as a genuine `empty-range`: a
    reading it never derived, from a command that did something else
    entirely. The shape guard refuses the value before git sees it.
    """
    root, _base, head = repo_with_range(tmp_path, touched="src/touched.py")
    victim = tmp_path / "victim.txt"
    write_attest(root, "20260901T010000Z",
                 attestation(f"--output={victim}", head, [finding("src/x.py")]))
    result = ingest(root)

    assert [b["kind"] for b in result["range_bindings"]] == ["unresolved"], (
        "an option-shaped sha was read as a real range: "
        f"{result['range_bindings']}")
    assert not victim.exists(), (
        f"git wrote {victim} — the option-shaped revision reached the command")
    assert not list(tmp_path.glob("victim.txt*")), (
        "git created a file from the option-shaped revision")
    # and the shard is still filed: this is a WARNING seam, never a refusal
    assert len(result["new_shards"]) == 1, result


def test_c1_an_artifact_with_no_range_is_its_own_kind(tmp_path):
    """`no-range` is not `unresolved`: the artifact declared nothing to read,
    which is a different fact with a different remedy from a range git could
    not resolve (error-names-cause)."""
    root, _base, _head = repo_with_range(tmp_path, touched="src/touched.py")
    doc = attestation("", "", [finding("src/x.py")])
    write_attest(root, "20260901T010000Z", doc)
    result = ingest(root)
    assert [b["kind"] for b in result["range_bindings"]] == ["no-range"], (
        result["range_bindings"])
