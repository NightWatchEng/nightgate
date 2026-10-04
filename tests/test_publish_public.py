"""scripts/publish-public.py applies publish.yaml to one commit's tree and
scrubs its message; the last test exports this repository's own HEAD."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from private_evidence import needs_tracker

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "publish-public.py"
_spec = importlib.util.spec_from_file_location("publish_public", SCRIPT)
publish = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publish)

FILES = {
    "publish.yaml": "top_level:\n  exclude: [.beads]\n  include: [.warden, README.md,"
                    " docs, publish.yaml, scripts]\nexclude: [.warden/memory/, "
                    "docs/design/retro-2*.md]\nkeep: [.warden/memory/tags.yaml]\n",
    ".beads/issues.jsonl": '{"id": "agentops-abc"}\n',
    ".warden/rules/r.md": "rule\n", ".warden/memory/tags.yaml": "tags: {}\n",
    ".warden/memory/attest/shard.json": "{}\n", "README.md": "readme\n",
    "docs/design/retro-20260101.md": "a retro\n",
    "docs/design/retro-rationale.md": "the retro skill's reasoning\n",
    "scripts/run.sh": "#!/bin/sh\necho hi\n",
}

MESSAGE = """\
fix(warden): the gate reads the base ref from env (agentops-elsx.20) (#384)

Why: a ref name with shell metacharacters would execute (agentops-elsx.4.17,
agentops-g65), found by agentops-hy6o.

- the step reads BASE_REF from env: (agentops-x1y2)
- agentops-skills:deliver and docs/superpowers/specs/agentops-redesign-design.md
  are named, not bead ids
- agentops-daq.11..14

* test(memory): attest review round 1 (agentops-elsx.4.17)
  Evidence: round shard committed

Evidence: 5051 passed, 1 skipped (warden verify --scope tests at 0d9af043);
review rounds 1 and 2 ran (agentops-elsx.20)
"""


# 40ab05b4 on main, its subject and Evidence line verbatim, its body cut short:
# a real feat commit whose export the public repo's commits job lints
REAL_FEAT = """\
feat(repo): publish.yaml and one script export the public tree and message (agentops-hy6o.14)

Why: the founder ruled on 2026-10-04 that this repo stays private and a
public repo receives a filtered export.

- scripts/publish-public.py reads the tree and the manifest from one
  commit's git objects

Evidence: 5070 passed, 1 skipped (warden verify --scope tests at 247177de); ruff F,E9 clean; two review rounds, round 2 clean
"""
LINT = ROOT / "scripts" / "commit-lint.sh"


def _lint(tmp_path: Path, message: str) -> subprocess.CompletedProcess:
    (path := tmp_path / "msg").write_text(message)
    return subprocess.run(["sh", str(LINT), str(path)], capture_output=True, text=True,
                          timeout=60)


def _public_fence_passes(tmp_path: Path, message: str) -> None:
    """What the public repo's commits job asks of a pushed message, and that it
    names no bead. The id check is a plain substring test, not the scrubber's
    pattern, so an id the scrubber misses fails here instead of passing twice."""
    result = _lint(tmp_path, message)
    assert result.returncode == 0, result.stderr
    named = message.replace("agentops-skills", "").replace("agentops-redesign-design", "")
    assert "agentops-" not in named, f"bead ids reach the public repo: {message!r}"

def _git(repo: Path, *args: str) -> str:
    return publish.git(repo, *args).decode().strip()


def _fixture(tmp_path: Path, change: dict | None = None) -> Path:
    """FILES + `change` (None: leave out; `-> target`: a symlink), one commit."""
    repo = tmp_path / "private"
    for rel, text in {**FILES, **(change or {})}.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        if text is not None and text.startswith("-> "):
            os.symlink(text[3:], repo / rel)
        elif text is not None:
            (repo / rel).write_text(text)
            (repo / rel).chmod(0o755 if rel.endswith(".sh") else 0o644)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", MESSAGE)
    return repo


def _run(repo: Path, out: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *flags, str(SCRIPT), "--repo", str(repo),
                           "--out", str(out)], capture_output=True, text=True, timeout=120)


def _files(out: Path) -> set[str]:
    return {p.relative_to(out).as_posix() for p in out.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(out).parts}


@pytest.mark.parametrize("change, code, said", [
    ({"newdir/f.txt": "x\n"}, 1, "unclassified ['newdir']; matching nothing none"),
    ({"scripts/run.sh": None}, 1, "unclassified none; matching nothing ['scripts']"),
    ({"docs/design/retro-20260101.md": None}, 1, "nothing ['docs/design/retro-2*.md']"),
    ({"scripts/link": "-> run.sh"}, 2, "scripts/link: tree mode 120000"),
    ({"publish.yaml": "top_level:\n  README.md: maybe\n"}, 2, "include and exclude lists"),
    ({"publish.yaml": "top_level: {include: [a]}\nkeep: x\n"}, 2, "keep must be a list"),
    ({"publish.yaml": None}, 2, "publish.yaml"),
])
def test_every_top_level_path_is_classified_or_the_export_refuses(tmp_path, change,
                                                                  code, said):
    result = _run(_fixture(tmp_path, change), tmp_path / "out")
    assert result.returncode == code and said in result.stderr, result.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("sub", ["", ".git", ".git/objects"])
def test_the_export_refuses_to_write_over_the_repository_it_reads(tmp_path, sub):
    repo = _fixture(tmp_path)
    result = _run(repo, repo / sub)
    assert result.returncode == 2 and "would overwrite the repository" in result.stderr
    assert _git(repo, "fsck", "--no-progress") == "" and (repo / ".beads/issues.jsonl").is_file()


def test_a_failure_outside_the_manifest_is_could_not_run_not_a_refusal(tmp_path):
    result = _run(_fixture(tmp_path), tmp_path / "out", "-S")  # -S: no site-packages, no yaml
    assert result.returncode == 2 and "yaml" in result.stderr, result.stderr


def test_excluded_paths_are_absent_from_the_public_tree(tmp_path):
    repo, out = _fixture(tmp_path), tmp_path / "out"
    assert _run(repo, out).returncode == 0
    files = _files(out)
    assert not [f for f in files if f.startswith((".beads/", ".warden/memory/attest/",
                                                  "docs/design/retro-2"))]
    assert {".warden/memory/tags.yaml", "docs/design/retro-rationale.md"} <= files


def test_included_paths_are_byte_identical(tmp_path):
    repo, out = _fixture(tmp_path), tmp_path / "out"
    assert _run(repo, out).returncode == 0
    public = set(FILES) - {".beads/issues.jsonl", ".warden/memory/attest/shard.json",
                           "docs/design/retro-20260101.md"}
    assert _files(out) == public
    for rel in public:
        assert (out / rel).read_bytes() == publish.git(repo, "show", f"HEAD:{rel}"), rel
    assert os.access(out / "scripts/run.sh", os.X_OK)
    assert not os.access(out / "README.md", os.X_OK)


def test_scrubbed_subject_trades_the_bead_id_for_a_tag_and_keeps_the_pr_number():
    subject = lambda line: publish.scrub_message(line, "abc").split("\n")[0]  # noqa: E731
    assert subject("fix(ci): env (agentops-elsx.20) (#384)") == \
        "fix(ci): env (no-bead: private tracker) (#384)"
    assert subject("ops(repo): v2.3.0 (no-bead: release) (#380)") == \
        "ops(repo): v2.3.0 (no-bead: private tracker) (#380)"
    assert subject("fix(ci): env (agentops-elsx.20)") == "fix(ci): env (no-bead: private tracker)"
    assert subject("feat(x): two (agentops-ab1, agentops-cd2.3) (#9)") == \
        "feat(x): two (no-bead: private tracker) (#9)"
    # no fence suffix to stand in for: ids still go, nothing is added
    assert subject("x (agentops-ab1/agentops-cd2) (no-bead: v2 (hotfix)) (#9)") == "x (#9)"
    assert subject("build(deps): bump pyyaml") == "build(deps): bump pyyaml"
    assert subject("Merge branch 'main' into x") == "Merge branch 'main' into x"


@pytest.mark.parametrize("suffix, tag", [("(agentops-hy6o.20)", "(no-bead: private)"),
                                         ("(agentops-g65)", "(no-bead: p)"),
                                         ("(no-bead: x)", "(no-bead: p)")])
def test_a_header_spent_to_the_budget_takes_the_tag_that_fits(tmp_path, suffix, tag):
    header = f"fix(ci): {'w' * (100 - len('fix(ci): ') - len(suffix) - 1)} {suffix}"
    assert len(header) == 100
    assert _lint(tmp_path, header + "\n\nEvidence: 1 passed\n").returncode == 0
    public = publish.scrub_message(header + " (#7)\n\nEvidence: 1 passed\n", "abc")
    assert public.split("\n")[0].endswith(f" {tag} (#7)")
    _public_fence_passes(tmp_path, public)


def test_scrubbed_body_keeps_why_and_drops_bead_ids_and_evidence_prose():
    scrubbed = publish.scrub_message(MESSAGE, "abc")
    for gone in ("agentops-elsx", "agentops-g65", "agentops-hy6o", "agentops-x1y2",
                 "agentops-daq", "review rounds 1 and 2", "round shard committed"):
        assert gone not in scrubbed, gone
    for kept in ("Why: a ref name with shell metacharacters would execute, found by.\n",
                 "- the step reads BASE_REF from env:\n",
                 "- agentops-skills:deliver and docs/superpowers/specs/"
                 "agentops-redesign-design.md\n  are named, not bead ids\n",
                 "\n\n* test(memory): attest review round 1\n\n"):
        assert kept in scrubbed, kept
    assert "\n\n\n" not in scrubbed
    ordinary = "- `drive()` runs `ruff check . --select F` ( see #3 ) ,  twice"
    assert publish.scrub_text(ordinary) == ordinary


# hy6o.14 dropped Evidence outright; the public fence requires a top-level
# `Evidence: ` line on feat/fix, so one stands in, carrying only the counts.
# The prose stays private: it names beads by bare short id (R1, the real
# "c0e4 mutation-proven" shape), which no pattern tells from a word.
@pytest.mark.parametrize("evidence, line", [
    ("Evidence: 5051 passed, 1 skipped (warden verify at 0d9af043);\nreview rounds ran",
     "Evidence: 5051 passed, 1 skipped; the full receipt stays with the private Source commit"),
    ("Evidence: c0e4 mutation-proven per member; 12\npassed",
     "Evidence: 12 passed; the full receipt stays with the private Source commit"),
    ("Evidence: agentops-ab1.", "Evidence: the full receipt stays with the private Source commit"),
])
def test_evidence_crosses_as_its_counts_and_nothing_else(tmp_path, evidence, line):
    public = publish.scrub_message(f"feat(ci): x (agentops-ab1)\n\nWhy: y\n\n{evidence}\n", "a")
    assert public == f"feat(ci): x (no-bead: private tracker)\n\nWhy: y\n\n{line}\n\nSource: a\n"
    _public_fence_passes(tmp_path, public)


def test_the_export_of_a_real_main_feat_commit_passes_the_public_commit_lint(tmp_path):
    assert _lint(tmp_path, REAL_FEAT).returncode == 0  # the private fence passed it too
    public = publish.scrub_message(REAL_FEAT, "40ab05b4")
    assert public.startswith("feat(repo): publish.yaml and one script export the public tree"
                             " and message (no-bead: private)\n")
    assert "\nEvidence: 5070 passed, 1 skipped; the full receipt" in public
    _public_fence_passes(tmp_path, public)
    _public_fence_passes(tmp_path, publish.scrub_message(MESSAGE, "abc"))
    # this repo's fence is unchanged: hy6o.14's header, with no suffix at all, is refused
    bare = _lint(tmp_path, public.replace(" (no-bead: private)", "", 1))
    assert bare.returncode == 1 and "header does not match" in bare.stderr


def test_a_message_that_kept_a_bead_id_fails_the_public_check(tmp_path):
    # the raw private message passes commit-lint: only the id check catches it
    with pytest.raises(AssertionError, match="bead ids reach the public repo"):
        _public_fence_passes(tmp_path, REAL_FEAT)


def test_source_trailer_names_the_private_sha(tmp_path):
    result = _run(repo := _fixture(tmp_path), tmp_path / "out")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("fix(warden): the gate reads the base ref from env "
                                    "(no-bead: private tracker) (#384)\n\nWhy:")
    assert result.stdout.endswith(f"\n\nSource: {_git(repo, 'rev-parse', 'HEAD')}\n")


def test_rerun_on_same_sha_is_a_no_op(tmp_path):
    repo, out = _fixture(tmp_path), tmp_path / "out"
    for rel in (".git/HEAD", "stale/old.txt"):  # a public checkout, and an older export
        (out / rel).parent.mkdir(parents=True)
        (out / rel).write_text("x\n")
    first = _run(repo, out)
    assert first.returncode == 0 and "(6 written, 2 removed)" in first.stderr, first.stderr
    stamps = {rel: os.stat(out / rel).st_mtime_ns for rel in _files(out)}
    second = _run(repo, out)
    assert (second.returncode, second.stdout) == (0, first.stdout), second.stderr
    assert "(no change)" in second.stderr and (out / ".git/HEAD").read_text() == "x\n"
    assert {rel: os.stat(out / rel).st_mtime_ns for rel in _files(out)} == stamps


@needs_tracker
def test_this_repos_head_exports_without_the_tracker_or_the_corpus(tmp_path):
    result = _run(ROOT, out := tmp_path / "public")
    assert result.returncode == 0, result.stderr
    assert not (out / ".beads").exists() and not (out / ".warden/memory/attest").exists()
    assert (out / ".warden/memory/tags.yaml").is_file()
    assert result.stdout.endswith(f"Source: {_git(ROOT, 'rev-parse', 'HEAD')}\n")
