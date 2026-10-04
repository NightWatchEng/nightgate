"""The per-process YAML memo changes speed, not outputs.

`yamlio.load` memoizes by document text, so the proofs are: the commands
that read the most YAML print, exit and write byte-for-byte the same with
the memo off (WARDEN_NO_YAML_CACHE=1) as with it on; a file rewritten
between two loads in one process is parsed afresh; and no caller can reach
another caller's copy of a memoized document.
"""
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from test_cli import _clone_fixture
from warden import cli, yamlio

def _outputs(base: Path, work: Path, monkeypatch, capsys) -> list:
    """Run ingest, certify and declare check in a fresh copy of `base` at the fixed path `work`, so
    paths in the output agree between runs; return every exit code, stream
    and every file the commands left under .warden/memory."""
    shutil.copytree(base, work, symlinks=True,
                    ignore=shutil.ignore_patterns("*.lock"))
    monkeypatch.chdir(work)
    capsys.readouterr()
    seen = []
    for argv in (["memory", "ingest", "--origin", "interactive"],
                 ["certify"], ["declare", "check"]):
        code = cli.main(argv)
        out, err = capsys.readouterr()
        seen.append((argv, code, out, err))
    memory = work / ".warden" / "memory"
    seen.append(sorted((str(p.relative_to(work)), p.read_bytes())
                       for p in memory.rglob("*") if p.is_file()))
    monkeypatch.chdir(base)
    shutil.rmtree(work)
    return seen


def test_certify_ingest_and_declare_check_are_identical_without_the_memo(
        sample_repo, tmp_path, monkeypatch, capsys):
    base = _clone_fixture(sample_repo, tmp_path)
    finding = {"rule_id": "scope-creep", "severity": "LOW", "file": "app/x.py",
               "line": 1, "finding": "f", "evidence": "e", "status": "fixed"}
    payload = tmp_path / "findings.json"
    payload.write_text(json.dumps({
        "reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
        "findings": [finding], "verdict": "clean"}))
    monkeypatch.chdir(base)
    assert cli.main(["attest", "write", "--findings", str(payload)]) == 0, \
        capsys.readouterr().err

    work = tmp_path / "work"
    monkeypatch.delenv("WARDEN_NO_YAML_CACHE", raising=False)
    cached = _outputs(base, work, monkeypatch, capsys)
    monkeypatch.setenv("WARDEN_NO_YAML_CACHE", "1")
    uncached = _outputs(base, work, monkeypatch, capsys)

    assert cached == uncached
    # Not vacuous: ingest filed a shard and every command ran to a verdict.
    assert any(path.startswith(".warden/memory/attest/")
               for path, _ in cached[-1]), cached[-1]
    assert all(code in (0, 1) for _, code, _, _ in cached[:-1]), cached


def test_a_file_changed_between_two_loads_is_parsed_again(tmp_path,
                                                            monkeypatch):
    monkeypatch.delenv("WARDEN_NO_YAML_CACHE", raising=False)
    path = tmp_path / "doc.yaml"
    path.write_text("ceiling: 1\n")
    first = path.stat()
    assert yamlio.load(path.read_text()) == {"ceiling": 1}
    # Same size, same mtime: the rewrite a (path, mtime_ns, size) key would
    # miss, and the reason the memo is keyed by content instead.
    path.write_text("ceiling: 2\n")
    os.utime(path, ns=(first.st_atime_ns, first.st_mtime_ns))
    assert path.stat().st_size == first.st_size
    assert yamlio.load(path.read_text()) == {"ceiling": 2}


def test_a_memoized_document_is_never_shared_between_callers(monkeypatch):
    monkeypatch.delenv("WARDEN_NO_YAML_CACHE", raising=False)
    text = "a: [1, 2]\nb: {c: 3}\n"
    first = yamlio.load(text)
    first["a"].append(99)
    first["b"]["c"] = 0
    assert yamlio.load(text) == {"a": [1, 2], "b": {"c": 3}}


def test_the_memo_keeps_unique_keys_apart_and_never_memoizes_a_refusal(
        monkeypatch):
    monkeypatch.delenv("WARDEN_NO_YAML_CACHE", raising=False)
    text = "k: 1\nk: 2\n"
    assert yamlio.load(text) == {"k": 2}
    with pytest.raises(yaml.YAMLError, match="duplicate key"):
        yamlio.load(text, unique_keys=True)
    with pytest.raises(yaml.YAMLError, match="duplicate key"):
        yamlio.load(text, unique_keys=True)
    assert (True, text) not in yamlio._MEMO


def test_each_command_starts_with_an_empty_memo(tmp_path, monkeypatch,
                                                capsys):
    sentinel = (False, "left: over from an earlier command\n")
    yamlio._MEMO[sentinel] = {"left": "over"}
    monkeypatch.chdir(tmp_path)          # no repo.yaml: the command refuses
    cli.main(["explain"])
    capsys.readouterr()
    assert sentinel not in yamlio._MEMO
