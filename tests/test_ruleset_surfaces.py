"""The probe walks the surfaces the reader reads.

`rules.rules_version` reads FOUR surfaces raw — the rules dir, each rule file,
the consuming repo's `.warden/checkers/*.py`, and the extra surfaces it hashes.
A readability probe in `memory.ingest` that hand-rolls a walk of ONE of them
(the rule files) fails in two ways:

* **Readability** — an unreadable `.warden/checkers/*.py` passes such a probe
  and raises a bare `PermissionError` out of `rules_version` AFTER the shard,
  the cache row, the `.consumed` marker and `ingest-result.json` are on disk.
  `cage/run.sh` reads that exit code as "the corpus was not fed" and skips
  publishing the run's attest shard — while the run dir is already marked
  consumed, so no later sweep re-mints it. The corpus was fed and the caller
  is told it was not. The `cli` functions that call `rules_version` read the
  same surfaces, so a repaired probe alone would leave them raising the same
  traceback: the probe is not the reader. `rules_version` therefore refuses
  the surface itself with a `RuleError`, which `cli.main` catches, and every
  caller is driven below and asserted to print no traceback.
* **Validity** — a hand-rolled walk reading each rule file with
  `read_text(encoding="utf-8-sig")` under `except (OSError, ValueError)`
  catches `UnicodeDecodeError`, which IS a `ValueError`. A rule file that
  opens perfectly and is merely not UTF-8 would be classified "cannot be
  READ" and refused on the UNCONDITIONAL path — moving a tree with nothing to
  sweep from exit 0 to exit 2, a widening an invalid ruleset with nothing to
  resolve must not cause.

The fix is one change of shape, not two patches: the READ question is asked by
CALLING `rules_version`, so the probe cannot walk a different set of surfaces
than the reader — and because that reader hashes BYTES, a decode failure is not
a readability answer at all. It lands where it belongs, on the VALIDITY side
(`load_rules`, gated on there being a sweep).
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from warden import cli
from warden import memory as memory_mod
from warden import rules as rules_mod

TRACEBACK = "Traceback (most recent call last)"


def _repo_yaml() -> str:
    return ("version: 1\nrepo: surface-fixture\n"
            "components:\n  app:\n    path: app/\n    lang: python\n"
            "    description: application code\n"
            "risk_tiers:\n  - glob: app/**\n    tier: MEDIUM\n"
            "    reason: production code\n"
            "verify:\n  smoke:\n    - run: \"true\"\n"
            "deploy:\n  staging:\n    - run: \"true\"\n"
            "review:\n  rules_dir: .warden/rules\n"
            "  blocking_severities: [HIGH]\n")


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                           *args], cwd=root, check=True, capture_output=True,
                          text=True)
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    """A minimal enrolled repo with a checker plugin, and its HEAD sha.

    The sha is REACHABLE (a real commit), because an artifact naming an
    unreachable head is dropped by the sweep — and a sweep that files nothing
    cannot show that the mutating half ran before the refusal.
    """
    root = tmp_path / "repo"
    (root / ".warden" / "rules").mkdir(parents=True)
    (root / ".warden" / "checkers").mkdir(parents=True)
    (root / ".warden" / "memory" / "attest").mkdir(parents=True)
    (root / "app").mkdir(parents=True)
    (root / "app" / "x.py").write_text("x = 1\n")
    (root / "repo.yaml").write_text(_repo_yaml())
    (root / ".warden" / "rules" / "ripe.md").write_text(
        "---\nid: ripe\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\n---\nfixture rule\n')
    (root / ".warden" / "checkers" / "plug.py").write_text(
        "def _c(ctx, params, files):\n    return []\n\n"
        "CHECKERS = {'plug-rule': _c}\n")
    (root / ".warden" / "memory" / "tags.yaml").write_text(
        "ceiling:\n  max_undecided: 99\n  rationale: fixture\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    return root, _git(root, "rev-parse", "HEAD")


def _seed_artifact(root: Path, head: str) -> Path:
    run = root / ".warden" / "out" / "20260901T000000Z-attest"
    run.mkdir(parents=True)
    (run / "attestation.json").write_text(json.dumps({
        "reviewed_at": "2026-09-01T00:00:00+00:00", "head_sha": head,
        "base_sha": "b" * 40, "rules_version": "v1", "verdict": "clean",
        "reviewers": [{"role": "r", "agent": "a"}],
        "findings": [{"rule_id": "ripe", "file": "app/x.py", "finding": "f",
                      "evidence": "e", "severity": "MEDIUM",
                      "status": "fixed"}]}))
    return run


def _drive(root: Path, argv: list[str]) -> tuple[int, str]:
    """One warden command in-process against `root`, as the CLI would.

    `cli.main`'s blanket handler prints the traceback to stderr and returns 2,
    so a crash is visible here in exactly the text a consumer would see.
    """
    out, err = io.StringIO(), io.StringIO()
    cwd = os.getcwd()
    os.chdir(root)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(argv)
            except SystemExit as e:            # argparse's own exits
                code = int(e.code or 0)
    finally:
        os.chdir(cwd)
    return code, out.getvalue() + err.getvalue()


@contextlib.contextmanager
def _unreadable(path: Path):
    os.chmod(path, 0o000)
    try:
        yield
    finally:
        os.chmod(path, 0o644 if path.is_file() else 0o755)


@pytest.fixture(autouse=True)
def _not_root():
    if os.geteuid() == 0:
        pytest.skip("root can read a chmod 000 path")


# ── the reader itself ──────────────────────────────────────────────────────

@pytest.mark.parametrize("surface", ["rules-dir", "rule-file",
                                     "checker-plugin", "extra-surface"])
def test_rules_version_refuses_by_name_on_every_surface_it_reads(
        tmp_path, monkeypatch, surface):
    """Every surface the hash reads, refused by NAME rather than raised
    through seventeen call sites.

    The rules dir is the one that was not even loud: `Path.glob` SWALLOWS
    `PermissionError` and returns `[]`, so an unlistable rules dir hashed as
    though the repo declared no rules at all — a different cohort, silently.
    Listing is `iterdir` here for that reason, and this parametrization is what
    holds it.
    """
    root = tmp_path / "repo"
    rules_dir = root / ".warden" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "ripe.md").write_text(
        "---\nid: ripe\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\n---\nfixture rule\n')
    (root / "repo.yaml").write_text(_repo_yaml())
    if surface == "rules-dir":
        target, expect = rules_dir, str(rules_dir)
    elif surface == "rule-file":
        target = expect = rules_dir / "ripe.md"
    elif surface == "checker-plugin":
        (root / ".warden" / "checkers").mkdir()
        target = expect = root / ".warden" / "checkers" / "plug.py"
        target.write_text("CHECKERS = {}\n")
    else:
        # The platform's own hashed surfaces (the findings schema, the
        # checker code) live inside the installed package, so the arm over
        # them is exercised through the tuple that names them.
        target = expect = root / "extra-surface.json"
        target.write_text("{}\n")
        monkeypatch.setattr(rules_mod, "_EXTRA_SURFACES", (target,))
    with _unreadable(target), pytest.raises(rules_mod.RuleError) as excinfo:
        rules_mod.rules_version(rules_dir, root)
    assert str(expect) in str(excinfo.value), excinfo.value
    assert "rules_version" in str(excinfo.value), excinfo.value


def test_an_absent_rules_dir_is_still_not_this_arms_business(tmp_path):
    """The bound on the arm above. A repo with NO ruleset hashes fine and
    always has — refusing it would turn every un-ruled consumer's gate red on
    a platform bump, a widening the unreadable-surface refusal must not bring
    with it.
    `FileNotFoundError` is the one OSError this arm lets through."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "repo.yaml").write_text(_repo_yaml())
    assert len(rules_mod.rules_version(root / ".warden" / "rules", root)) == 12


def test_the_hash_over_a_readable_tree_is_unchanged(tmp_path):
    """Listing moved from `glob` to `iterdir`; the BYTES it feeds must not.

    A rules_version that shifted would re-cohort every attestation in every
    enrolled repo — the invisible policy change the hash exists to prevent —
    so the two listings are compared over one tree, dotfiles and all, rather
    than assumed equivalent.
    """
    root = tmp_path / "repo"
    rules_dir = root / ".warden" / "rules"
    checkers = root / ".warden" / "checkers"
    rules_dir.mkdir(parents=True)
    checkers.mkdir()
    (root / "repo.yaml").write_text(_repo_yaml())
    for name in ("b.md", "a.md", ".hidden.md", "not-a-rule.txt"):
        (rules_dir / name).write_text(f"body of {name}\n")
    (rules_dir / "sub.md").mkdir()          # a directory that matches the glob
    for name in ("z.py", "a.py", "notes.md"):
        (checkers / name).write_text(f"# {name}\n")
    assert (sorted(p for p in rules_dir.iterdir() if p.name.endswith(".md"))
            == sorted(rules_dir.glob("*.md")))
    assert (sorted(p for p in checkers.iterdir() if p.name.endswith(".py"))
            == sorted(checkers.glob("*.py")))


# ── every caller of it ─────────────────────────────────────────────────────

def _cli_callers() -> set[str]:
    """The `cli` functions that call `rules_version`, from cli.py's own AST.

    Derived, never typed: a new caller joins this set on the next run and the
    recipe table below goes red until someone drives it. Seventeen call sites
    over fourteen functions make an unreadable surface a class rather than a
    bug — the repair belongs in the reader, and this is what proves it
    reached all of them.
    """
    src = Path(cli.__file__).read_text()
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "rules_version"):
                out.add(node.name)
                break
    # `_finish` is a closure inside two of the commands below; the command
    # that defines it is what a consumer types.
    return {name for name in out if name.startswith("_cmd_")}


def _recipes() -> dict[str, list[str]]:
    return {
        "_cmd_explain": ["explain"],
        "_cmd_verify": ["verify", "--scope", "smoke"],
        "_cmd_deploy": ["deploy", "--scope", "staging"],
        "_cmd_review": ["review", "--base", "HEAD", "--no-comment"],
        "_cmd_attest": ["attest", "show"],
        "_cmd_decide": ["decide", "list"],
        "_cmd_plan": ["plan", "--task", "probe"],
        "_cmd_certify": ["certify"],
        "_cmd_certify_goal": ["certify", "--goal", "--report-only"],
        "_cmd_check_vocabulary": ["memory", "check-vocabulary"],
        "_cmd_memory": ["memory", "ingest"],
        "_cmd_rules_lifecycle": ["rules", "lifecycle"],
        "_cmd_rules": ["rules", "recommend"],
        "_cmd_autonomy": ["autonomy", "pause"],
        "_cmd_mine": ["mine"],
        # --checks-only: the recipe must reach `rules_version` (it does, before
        # any step runs) without pushing anything from a test.
        "_cmd_ship": ["ship", "--title", "feat(warden): probe (agentops-u6a)",
                      "--checks-only"],
    }


def test_every_cli_caller_of_rules_version_has_a_recipe():
    """The axis is derived, so it cannot go quiet. A new `rules_version`
    caller in cli.py fails here until it is driven below."""
    assert _cli_callers() == set(_recipes()), (
        "undriven rules_version callers: "
        f"{sorted(_cli_callers() - set(_recipes()))}; stale recipes: "
        f"{sorted(set(_recipes()) - _cli_callers())}")


@pytest.mark.parametrize("caller", sorted(_recipes()))
def test_an_unreadable_checker_plugin_refuses_by_name_not_by_traceback(
        tmp_path, caller):
    """Every command that calls `rules_version` answers a `chmod 000`
    `.warden/checkers/plug.py` by naming the file, never with a bare
    `PermissionError` traceback out of `warden/rules.py` that names a Python
    line and not a surface.

    `mine` is the one declared exception and it is asserted, not skipped: it
    surveys repos it does not own, so an uncomputable version is RECORDED
    (`uncomputable:<type>` in the manifest) and said out loud rather than made
    fatal. It must still never traceback, and it must still name the file.
    """
    root, _ = _repo(tmp_path)
    plug = root / ".warden" / "checkers" / "plug.py"
    with _unreadable(plug):
        code, text = _drive(root, _recipes()[caller])
    assert TRACEBACK not in text, text
    assert "plug.py" in text, text
    if caller == "_cmd_mine":
        manifest = json.loads((root / ".warden" / "out" / "latest"
                               / "manifest.json").read_text())
        assert manifest["rules_version"].startswith(cli.UNCOMPUTABLE_PREFIX), \
            manifest
    else:
        assert code == 2, f"{caller} did not refuse: exit {code}\n{text}"


def test_ingest_refuses_before_it_writes_anything(tmp_path):
    """The refusal lands BEFORE the point of no return: nothing the sweep
    writes exists when ingest refuses.

    `cage/run.sh` reads a non-zero `memory ingest` as "the corpus was not fed"
    and does not publish the run's attest shard — while the run dir is already
    marked `.consumed`, so the next sweep will not re-mint it. Every mutation
    the sweep performs is asserted absent here, not just the shard.
    """
    root, head = _repo(tmp_path)
    run = _seed_artifact(root, head)
    plug = root / ".warden" / "checkers" / "plug.py"
    with _unreadable(plug):
        code, text = _drive(root, ["memory", "ingest"])
    assert TRACEBACK not in text, text
    assert code == 2, text
    assert "plug.py" in text and "NOT fed" in text, text
    shards = list((root / ".warden" / "memory" / "attest").glob("*.json"))
    assert shards == [], f"the shard was written before the refusal: {shards}"
    assert not (root / ".warden" / "memory" / "findings.jsonl").exists(), \
        "the derived cache was rebuilt before the refusal"
    assert not (root / ".warden" / "out" / ".consumed").exists(), \
        "the run dir was marked consumed before the refusal"
    assert list((root / ".warden" / "out").iterdir()) == [run], \
        "an ingest run dir was created before the refusal"


# ── the other half of the split: VALIDITY ──────────────────────────────────

def _make_non_utf8(rules_dir: Path) -> Path:
    rule = rules_dir / "ripe.md"
    rule.write_bytes(
        "---\nid: ripe\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\n---\nfixture rule \xe9\n'.encode("latin-1"))
    return rule


def test_a_non_utf8_rule_file_with_nothing_to_ingest_still_exits_zero(
        tmp_path):
    """A rule file that OPENS perfectly and is merely not UTF-8 is not a
    readability failure; it is an invalid ruleset, and an invalid ruleset
    with nothing to resolve against it exits 0 (`cage/run.sh` keys on the
    code). A probe that hand-rolls a DECODING read classifies it as
    unreadable; the reader the probe calls hashes bytes.
    """
    root, _ = _repo(tmp_path)
    _make_non_utf8(root / ".warden" / "rules")
    code, text = _drive(root, ["memory", "ingest"])
    assert TRACEBACK not in text, text
    assert code == 0, f"a readable-but-invalid ruleset moved 0 -> {code}\n{text}"


def test_a_non_utf8_rule_file_with_a_sweep_refuses_and_names_it(tmp_path):
    """The other direction of the same split, and the reason the bound is not
    a hole: the moment there IS an artifact whose rule_ids must resolve, the
    same file is fatal — by NAME, through `load_rules`, with no traceback.
    `_parse` must not raise a bare `UnicodeDecodeError` (a ValueError, so
    neither of the validity arm's two exception types).
    """
    root, head = _repo(tmp_path)
    rule = _make_non_utf8(root / ".warden" / "rules")
    _seed_artifact(root, head)
    code, text = _drive(root, ["memory", "ingest"])
    assert TRACEBACK not in text, text
    assert code == 2, text
    assert rule.name in text and "NOT fed" in text, text
    assert list((root / ".warden" / "memory" / "attest").glob("*.json")) == [], \
        "the sweep ran before the ruleset was refused"


def test_the_probe_is_the_reader_rather_than_a_second_walk(tmp_path):
    """The shared cause, asserted rather than described. The read half of the
    probe calls `rules_version` — so it cannot walk a different set of
    surfaces than the reader whose failure it exists to pre-empt, which is
    the shared cause of both the readability and the validity failure.
    """
    src = ast.parse(Path(memory_mod.__file__).read_text())
    probe = next(n for n in ast.walk(src)
                 if isinstance(n, ast.FunctionDef)
                 and n.name == "_assert_ruleset_readable")
    called = {n.func.attr for n in ast.walk(probe)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    called |= {n.func.id for n in ast.walk(probe)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "rules_version" in called, (
        "the read probe hand-rolls its own walk again instead of calling "
        "rules_version, the one walk every reader of the ruleset shares")
