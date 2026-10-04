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
