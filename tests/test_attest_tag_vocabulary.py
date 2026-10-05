"""`attest write` refuses a tag the vocabulary rejects, before it writes.

A tag tests/test_tag_vocabulary_guards.py rejects is refused at the write
seam, through the same `tags` functions the guard reads, rather than by the
pre-push hook AFTER `warden attest write` has written the shard — so the
refusal lands while nothing exists to delete and no round is re-attested.
"""

import json
import subprocess
from pathlib import Path

import pytest
from conftest import copy_sample_repo, seed_rules

from warden import attest as attest_mod
from warden import cli
from warden import tags as tags_mod

HEAD, BASE = "b" * 40, "a" * 40
# A receipt reason and a ceiling rationale that clear the substance floor, so
# every case below is refused or accepted for its own reason and never for a
# malformed declaration.
_ARGUES = ("frozen at the count that exists today so that any further drift "
           "has to be answered rather than quietly absorbed")


def _repo(tmp_path: Path, *, ceiling: "int | None" = 0,
          corpus_tags: tuple[str, ...] = ()) -> Path:
    root = tmp_path / "repo"
    rules_dir = seed_rules(root, "scope-creep")
    assert rules_dir.is_dir()
    body = ("tags:\n  fail-open: a check that errs toward passing\n"
            f"left_undeclared:\n  seen-once: {_ARGUES}\n")
    if ceiling is not None:
        body += (f"ceiling:\n  {tags_mod.CEILING_LIMIT}: {ceiling}\n"
                 f"  rationale: {_ARGUES}\n")
    mem = root / ".warden" / "memory"
    mem.mkdir(parents=True)
    (mem / "tags.yaml").write_text(body)
    if corpus_tags:
        (mem / "attest").mkdir()
        (mem / "attest" / "s1.json").write_text(json.dumps({"records": [
            {"id": f"r{i}", "ts": "2026-09-01T00:00:00+00:00", "tags": [t],
             "status": "fixed", "rule_id": "scope-creep"}
            for i, t in enumerate(corpus_tags)]}))
    return root


def _payload(*tags: str) -> dict:
    return {"reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
            "findings": [{"rule_id": "scope-creep", "severity": "MEDIUM",
                          "file": "app/x.py", "line": 1, "finding": "f",
                          "evidence": "e", "status": "fixed",
                          "tags": list(tags)}],
            "verdict": "clean"}


def _build(root: Path, *tags: str) -> dict:
    return attest_mod.build(_payload(*tags), head_sha=HEAD, base_sha=BASE,
                            rules_version="v1",
                            rules_dir=root / ".warden" / "rules", root=root)


def test_an_undeclared_tag_is_refused_naming_the_declared_ones(
        tmp_path):
    root = _repo(tmp_path)
    with pytest.raises(attest_mod.AttestError) as e:
        _build(root, "coined-here")
    msg = str(e.value)
    assert "coined-here" in msg and "not in the declared vocabulary" in msg
    assert "Declared tags: fail-open" in msg, (
        "the refusal must list the declared tags, so the builder can re-tag "
        "without opening tags.yaml")
    assert "left_undeclared" in msg, "the receipt remedy must be named"


@pytest.mark.parametrize("tag", ["fail-open", "seen-once"])
def test_a_declared_tag_or_a_receipted_one_is_accepted(tmp_path, tag):
    """Declared, or written down under `left_undeclared:` — both are a
    recorded disposition, and the guard counts neither as undecided."""
    doc = _build(_repo(tmp_path), tag)
    assert doc["findings"][0]["tags"] == [tag]


def test_a_tag_over_the_declared_ceiling_is_refused(tmp_path):
    """Ceiling 1 with one undecided name already committed: a SECOND name
    breaches it, the name already counted does not, and with an empty corpus
    the same new name fits — it is the ceiling refusing, not the vocabulary."""
    root = _repo(tmp_path, ceiling=1, corpus_tags=("old-name",))
    with pytest.raises(attest_mod.AttestError,
                       match=r"new-name.*undecided count to 2 against the "
                             r"declared ceiling of 1"):
        _build(root, "new-name")
    assert _build(root, "old-name")["verdict"] == "clean"
    fresh = _repo(tmp_path / "empty", ceiling=1)
    assert _build(fresh, "new-name")["verdict"] == "clean"


def test_no_declared_ceiling_leaves_an_undeclared_tag_alone(tmp_path):
    """A consumer that declares no ceiling has nothing to breach: `memory
    ingest` warns, and that is the whole contract it signed up for."""
    doc = _build(_repo(tmp_path, ceiling=None), "coined-here")
    assert doc["findings"][0]["tags"] == ["coined-here"]


def test_a_rule_id_used_as_a_tag_is_refused(tmp_path):
    """The myf guard's shape, read through the same function it now imports:
    a rule id in `tags:` where the class it covers was wanted."""
    with pytest.raises(attest_mod.AttestError,
                       match=r"scope-creep are rule ids, not declared tags"):
        _build(_repo(tmp_path), "scope-creep")


def test_an_unreadable_ceiling_refuses_a_tagged_payload(tmp_path):
    """A declaration nobody can read is a complaint, never headroom — for a
    declared tag too, since what it declares cannot be shown."""
    root = _repo(tmp_path, ceiling=None)
    doc = root / ".warden" / "memory" / "tags.yaml"
    doc.write_text(doc.read_text() + "ceiling:\n  max_undecided: 1\n")
    for tag in ("coined-here", "fail-open"):
        with pytest.raises(attest_mod.AttestError, match="cannot be read"):
            _build(root, tag)
    assert _build(root)["verdict"] == "clean", "an untagged payload is fine"


def test_r1_a_covered_class_is_not_refused_as_a_rule_id(tmp_path):
    """Round 1, F1: the namespace the guard folds against includes every
    `covers:` class, and the seam read it whole — so the class a rule covers,
    which is exactly the tag its finding should carry, was refused as a
    misused rule id and told not to declare itself. The seam reads ids only."""
    root = _repo(tmp_path, ceiling=1)
    (root / ".warden" / "rules" / "wording.md").write_text(
        "---\nid: wording\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\ncovers: [docs-drift]\n---\nfixture rule\n')
    assert _build(root, "docs-drift")["verdict"] == "clean"
    with pytest.raises(attest_mod.AttestError, match="wording are rule ids"):
        _build(root, "wording")


def test_r1_an_unreadable_tags_block_is_named_not_a_symptom(tmp_path):
    """Round 1, F2: a `tags:` block that is a list loads as {}, and the
    rule-id reading ran first, so `lang-conventions` — declared on purpose —
    was refused as a misused rule id with "Declared tags: (none)". The
    declaration's own complaint is read first and is what the refusal says."""
    root = _repo(tmp_path)
    seed_rules(root, "lang-conventions")
    doc = root / ".warden" / "memory" / "tags.yaml"
    doc.write_text(doc.read_text().replace(
        "tags:\n  fail-open: a check that errs toward passing\n",
        "tags: [lang-conventions]\n"))
    with pytest.raises(attest_mod.AttestError) as e:
        _build(root, "lang-conventions")
    assert "cannot be read" in str(e.value)
    assert "rule ids" not in str(e.value), str(e.value)


def test_the_cli_exits_2_and_writes_nothing(sample_repo, tmp_path,
                                                   monkeypatch, capsys):
    """The seam the bead names: exit 2, one message, and no run dir — the
    shard `memory ingest` would copy never exists."""
    root = copy_sample_repo(sample_repo, tmp_path / "cli")
    (root / ".warden" / "memory").mkdir(parents=True, exist_ok=True)
    (root / ".warden" / "memory" / "tags.yaml").write_text(
        (_repo(tmp_path) / ".warden" / "memory" / "tags.yaml").read_text())
    for args in (["init", "-q", "-b", "main"], ["add", "."],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit",
                  "-q", "-m", "fixture"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps(_payload("coined-here")))
    monkeypatch.chdir(root)
    code = cli.main(["attest", "write", "--findings", str(payload),
                     "--base", "HEAD"])
    err = capsys.readouterr().err
    assert code == 2, err
    assert "coined-here" in err and "Declared tags: fail-open" in err
    assert not list((root / ".warden").glob("out/*-attest")), (
        "a refused payload left an attest run dir behind")


# ── the receipts the new shard would outgrow (agentops-s9ce) ─────────────────
#
# tests/test_tag_vocabulary_guards.py holds every `left_undeclared:` receipt to
# its premise over the COMMITTED corpus: an at-bar name needs a receipt that
# argues the bar at an `n=<judged>` it has not outgrown, and a receipt's
# `trigger:` must not have fired. A payload that broke one was written,
# ingested and committed, and only then reddened the suite — with tags.yaml
# out of a builder's reach, the remedy was to rebuild the branch without the
# shard. These are refused at the write seam instead, and only when THIS
# payload is what breaks the premise.

def _receipt_repo(tmp_path: Path, receipt: str, *, corpus: int,
                  ceiling: "int | None" = 0, tag: str = "watched") -> Path:
    """A repo whose one receipt is `receipt` (the YAML body under the tag's
    key) and whose committed corpus holds `corpus` fixed records tagged
    `tag`, each a distinct finding."""
    root = tmp_path / "repo"
    seed_rules(root, "scope-creep")
    body = (f"tags:\n  fail-open: a check that errs toward passing\n"
            f"left_undeclared:\n  {tag}:\n{receipt}")
    if ceiling is not None:
        body += (f"ceiling:\n  {tags_mod.CEILING_LIMIT}: {ceiling}\n"
                 f"  rationale: {_ARGUES}\n")
    mem = root / ".warden" / "memory"
    mem.mkdir(parents=True)
    (mem / "tags.yaml").write_text(body)
    (mem / "attest").mkdir()
    (mem / "attest" / "s1.json").write_text(json.dumps({"records": [
        {"id": f"r{i}", "ts": "2026-09-01T00:00:00+00:00", "sha": "c" * 40,
         "tags": [tag], "status": "fixed", "rule_id": "scope-creep",
         "file": f"app/old{i}.py", "line": 1, "finding": f"old {i}"}
        for i in range(corpus)]}))
    return root


def _receipt(reason: str, trigger: str = "none") -> str:
    return f"    reason: >-\n      {reason}\n    trigger: {trigger}\n"


_SUB_BAR = _receipt(f"n=2, seen twice and {_ARGUES}")
_AT_BAR = _receipt(f"n=3, AT the declaration bar, and {_ARGUES}")


def test_a_payload_taking_a_receipted_name_to_the_bar_is_refused(tmp_path):
    """The bead's shape: a receipt arguing from n=2 below the bar, and a
    payload whose record takes the name to 3 judged, 3 upheld — AT the bar,
    under a receipt that neither argues it nor states the count. Refused
    naming the tag and the receipt; with one record fewer committed, the same
    payload leaves the name below the bar and is written."""
    root = _receipt_repo(tmp_path, _SUB_BAR, corpus=2)
    with pytest.raises(attest_mod.AttestError) as e:
        _build(root, "watched")
    msg = str(e.value)
    assert "watched" in msg and "left_undeclared" in msg, msg
    assert "AT the declaration bar" in msg, (
        "the refusal must say which premise the receipt no longer meets")
    below = _receipt_repo(tmp_path / "below", _SUB_BAR, corpus=1)
    assert _build(below, "watched")["verdict"] == "clean"


def test_an_at_bar_receipt_the_shard_outgrows_is_refused(tmp_path):
    """Argued from the bar at n=3: a third record fits the count it states,
    a fourth outgrows it — the hy6o.6 shape, n=5 against 8."""
    fits = _receipt_repo(tmp_path / "fits", _AT_BAR, corpus=2)
    assert _build(fits, "watched")["verdict"] == "clean"
    root = _receipt_repo(tmp_path, _AT_BAR, corpus=3)
    with pytest.raises(attest_mod.AttestError,
                       match=r"watched.*n=3.* 4 judged"):
        _build(root, "watched")


def test_an_at_bar_receipt_stating_no_count_is_refused(tmp_path):
    """The guard needs `n=<judged>` on an at-bar receipt, or a reader cannot
    tell a live decision from one the corpus has moved past."""
    root = _receipt_repo(
        tmp_path, _receipt(f"AT the declaration bar, and {_ARGUES}"), corpus=2)
    with pytest.raises(attest_mod.AttestError, match=r"watched.*n=<judged>"):
        _build(root, "watched")


def test_a_receipt_whose_trigger_the_shard_fires_is_refused(tmp_path):
    """A receipt naming its own re-read condition is held to it on either
    side of the bar: a second record meets `judged_at_least: 2`."""
    reason = _receipt(f"n=1, seen once and {_ARGUES}",
                      trigger="{judged_at_least: 2}")
    assert _build(_receipt_repo(tmp_path / "once", reason, corpus=0),
                  "watched")["verdict"] == "clean"
    root = _receipt_repo(tmp_path, reason, corpus=1)
    with pytest.raises(attest_mod.AttestError,
                       match=r"watched.*judged_at_least: 2 is met"):
        _build(root, "watched")


def test_an_undeclared_name_reaching_the_bar_within_the_ceiling_is_refused(
        tmp_path):
    """Under a ceiling of 1 an undecided name is within bounds, and the guard
    still refuses one AT the bar with no receipt to re-read. Two records
    committed, the payload's third takes it there."""
    root = _repo(tmp_path, ceiling=1, corpus_tags=("coined", "coined"))
    with pytest.raises(attest_mod.AttestError, match=r"coined.*no receipt"):
        _build(root, "coined")
    two = _repo(tmp_path / "two", ceiling=1, corpus_tags=("coined",))
    assert _build(two, "coined")["verdict"] == "clean"


def test_a_restated_finding_is_not_counted_twice(tmp_path):
    """A round-2 payload re-files a round-1 finding byte for byte; the reader
    folds the two into one record, so the restatement is no new evidence and
    does not outgrow the receipt."""
    root = _receipt_repo(tmp_path, _AT_BAR, corpus=2)
    shard = root / ".warden" / "memory" / "attest" / "s1.json"
    doc = json.loads(shard.read_text())
    doc["records"].append({"id": "same", "ts": "2026-09-01T00:00:00+00:00",
                           "sha": "c" * 40, "tags": ["watched"],
                           "status": "fixed", "rule_id": "scope-creep",
                           "file": "app/x.py", "line": 1, "finding": "f"})
    shard.write_text(json.dumps(doc))
    assert _build(root, "watched")["verdict"] == "clean"


def test_a_premise_the_corpus_already_broke_is_not_this_payloads(tmp_path):
    """Refused is what THIS payload breaks. A receipt the committed shards
    have already outgrown is the suite's to report, not a reason to refuse
    every later payload carrying the name."""
    root = _receipt_repo(tmp_path, _AT_BAR, corpus=5)
    assert _build(root, "watched")["verdict"] == "clean"


def test_no_declared_ceiling_leaves_receipts_unread(tmp_path):
    """The scope `attest write` already reads the vocabulary under: a repo
    that declares no ceiling is untouched by any of this."""
    root = _receipt_repo(tmp_path, _SUB_BAR, corpus=2, ceiling=None)
    assert _build(root, "watched")["verdict"] == "clean"


def test_a_corpus_that_cannot_be_read_refuses_any_finding(tmp_path):
    """Fail closed: whether a receipt is outgrown cannot be shown without the
    corpus — and not only for a receipted tag, since a payload carrying a
    declared tag, or none, can still replace a committed record by restating
    it (`test_r1_a_restatement_dropping_a_name_is_counted`). So every payload
    with findings is refused, naming the cause; a repo declaring no ceiling
    is not read and writes exactly as before."""
    root = _receipt_repo(tmp_path, _SUB_BAR, corpus=1)
    (root / ".warden" / "memory" / "attest" / "s1.json").write_text("{")
    for tags in (("watched",), ("fail-open",), ()):
        with pytest.raises(attest_mod.AttestError,
                           match=r"committed corpus cannot be read"):
            _build(root, *tags)
    open_repo = _receipt_repo(tmp_path / "open", _SUB_BAR, corpus=1,
                              ceiling=None)
    (open_repo / ".warden" / "memory" / "attest" / "s1.json").write_text("{")
    assert _build(open_repo, "watched")["verdict"] == "clean"


def test_r1_a_restatement_dropping_a_name_is_counted(tmp_path):
    """Round 1, F1: the check read only the names the payload CARRIES, but
    the fold keeps the later round's copy whatever its tags. Re-filing a
    committed `watched` refutation as `fixed` under a declared tag drops one
    refuted `watched` record: 3 judged, 2 upheld, AT the bar under a receipt
    that never argues it. Refused naming `watched`, though the payload never
    names it."""
    root = _receipt_repo(tmp_path, _SUB_BAR, corpus=2)
    shard = root / ".warden" / "memory" / "attest" / "s1.json"
    doc = json.loads(shard.read_text())
    for i, (file, finding) in enumerate((("app/x.py", "f"),
                                         ("app/y.py", "other"))):
        doc["records"].append({
            "id": f"ref{i}", "ts": "2026-09-01T00:00:00+00:00",
            "sha": "c" * 40, "tags": ["watched"], "status": "refuted",
            "reason": "r", "rule_id": "scope-creep", "file": file, "line": 1,
            "finding": finding})
    shard.write_text(json.dumps(doc))
    for tags in (("fail-open",), ()):
        with pytest.raises(attest_mod.AttestError,
                           match=r"watched.*AT the declaration bar"):
            _build(root, *tags)


def test_the_cli_refuses_an_outgrown_receipt_and_writes_nothing(
        sample_repo, tmp_path, monkeypatch, capsys):
    """The seam builders hit: `warden attest write` refuses with the tag and
    the receipt named, and leaves no run dir for `memory ingest` to copy."""
    root = copy_sample_repo(sample_repo, tmp_path / "cli")
    fixture = _receipt_repo(tmp_path / "fx", _AT_BAR, corpus=3)
    mem = root / ".warden" / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "tags.yaml").write_text(
        (fixture / ".warden" / "memory" / "tags.yaml").read_text())
    (mem / "attest").mkdir(exist_ok=True)
    (mem / "attest" / "s1.json").write_text(
        (fixture / ".warden" / "memory" / "attest" / "s1.json").read_text())
    for args in (["init", "-q", "-b", "main"], ["add", "."],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit",
                  "-q", "-m", "fixture"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps(_payload("watched")))
    monkeypatch.chdir(root)
    code = cli.main(["attest", "write", "--findings", str(payload),
                     "--base", "HEAD"])
    err = capsys.readouterr().err
    assert code == 2, err
    assert "watched" in err and "left_undeclared" in err and "n=3" in err
    assert not list((root / ".warden").glob("out/*-attest")), (
        "a refused payload left an attest run dir behind")
