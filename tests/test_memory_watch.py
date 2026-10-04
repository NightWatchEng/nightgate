"""`warden memory watch` — the scheduled doorbell for the memory loop.

The loop's trigger used to be a person remembering to run the retro, so a
candidate could sit over the promotion bar for days with nobody told. The watch
compares what the committed corpus says NOW against the last COMMITTED watch
record and reports three kinds of transition: a candidate crossing the
promotion bar, a declared rule entering pause or demote territory, and a skill
file crossing the skill-change propose bar.

Its exit code means one thing each:

    0  nothing crossed a bar since the baseline
    1  at least one transition, each named with its numbers and next command
    3  UNREAD — the corpus, the vocabulary, the ruleset or the baseline could
       not be read, so no claim is made either way

The issue half lives in `scripts/memory-watch-issue.py`, driven here through a
fake `gh` so nothing reaches GitHub.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from warden import cli
from warden import memory as memory_mod
from warden import watch as watch_mod

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "memory-watch.yml"
ISSUE_SCRIPT = ROOT / "scripts" / "memory-watch-issue.py"

_REPO_YAML = (
    "version: 1\nrepo: watch-fixture\n"
    "components:\n  app:\n    path: app/\n    lang: python\n"
    "    description: application code\n"
    "risk_tiers:\n  - glob: app/**\n    tier: MEDIUM\n"
    "    reason: production code\n"
    "verify:\n  smoke:\n    - run: \"true\"\n"
    "review:\n  rules_dir: .warden/rules\n"
    "  blocking_severities: [HIGH]\n")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".warden" / "rules").mkdir(parents=True)
    (root / ".warden" / "memory" / "attest").mkdir(parents=True)
    (root / "repo.yaml").write_text(_REPO_YAML)
    (root / ".warden" / "rules" / "r.md").write_text(
        "---\nid: r\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\n---\nbody\n")
    (root / ".gitignore").write_text(
        ".warden/out/\n.warden/memory/findings.jsonl\n.warden/memory/gate/\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _commit(root, "fixture")
    return root


def _commit(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", msg)


def _rec(rule_id: str, i: int, *, status: str = "fixed", round_n: int = 0,
         file: str = "app/x.py") -> dict:
    """One judged finding. `round_n` picks the review round: records sharing
    one carry one (ts, sha), the way a real shard's do."""
    return {"id": f"{rule_id}-{round_n}-{i}".replace(":", "_"),
            "ts": f"2026-09-01T00:{round_n:02d}:00+00:00", "seq": i,
            "source": "attest", "sha": f"{round_n + 1:040x}",
            "rule_id": rule_id, "tags": [], "file": file,
            "dir_prefix": file.rsplit("/", 1)[0], "line": 1,
            "severity": "LOW", "finding": f"f{round_n}.{i}", "evidence": "e",
            "status": status, "origin": "interactive", "reason": "r"}


def _shard(root: Path, name: str, records: list[dict]) -> Path:
    path = root / ".warden" / "memory" / "attest" / f"{name}.json"
    path.write_text(json.dumps({
        "schema": 1, "source": "attest", "sha": "a" * 40, "base_sha": "b" * 40,
        "rules_version": "test", "reviewed_at": "2026-09-01T00:00:00+00:00",
        "verdict": "clean", "reviewers": [], "records": records}))
    return path


def _watch(root: Path, monkeypatch, capsys, *args: str) -> tuple[int, dict]:
    capsys.readouterr()          # drop anything an earlier call printed
    monkeypatch.chdir(root)
    code = cli.main(["memory", "watch", "--json", *args])
    out = capsys.readouterr()
    return code, json.loads(out.out)


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts}


# ── the issue script, through a fake gh ────────────────────────────────────

def _issue_module():
    spec = importlib.util.spec_from_file_location("memory_watch_issue",
                                                  ISSUE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeGh:
    """Just enough of `gh issue` and `gh label` to hold one repo's issues."""

    def __init__(self, issues: list[dict] | None = None):
        self.issues = issues or []
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(list(args))
        verb = tuple(args[:2])
        if verb == ("label", "create"):
            return ""
        if verb == ("issue", "list"):
            label = args[args.index("--label") + 1]
            # `gh issue list` defaults to OPEN issues; only `--state all`
            # reaches a closed one. The fake honours that, or dropping the
            # flag from the real call would change nothing here.
            state = (args[args.index("--state") + 1].lower()
                     if "--state" in args else "open")
            return json.dumps([
                {"number": i["number"], "title": i["title"],
                 "state": i["state"]}
                for i in self.issues if label in i["labels"]
                and state in ("all", i["state"].lower())])
        if verb == ("issue", "view"):
            issue = self._issue(int(args[2]))
            return json.dumps({"body": issue["body"],
                               "comments": [{"body": c}
                                            for c in issue["comments"]]})
        if verb == ("issue", "create"):
            number = len(self.issues) + 1
            self.issues.append({
                "number": number, "state": "OPEN",
                "title": args[args.index("--title") + 1],
                "body": args[args.index("--body") + 1],
                "labels": [args[args.index("--label") + 1]],
                "comments": []})
            return f"https://github.com/o/r/issues/{number}\n"
        if verb == ("issue", "comment"):
            self._issue(int(args[2]))["comments"].append(
                args[args.index("--body") + 1])
            return ""
        raise AssertionError(f"unexpected gh call {args}")

    def _issue(self, number: int) -> dict:
        return next(i for i in self.issues if i["number"] == number)

    def writes(self) -> list[list[str]]:
        return [c for c in self.calls
                if tuple(c[:2]) in {("issue", "create"), ("issue", "comment")}]


def _report(tmp_path: Path, gh, watch_doc: dict | None, *,
            watch_exit: int, ingest: str = "success",
            mod=None) -> tuple[int, str]:
    # `mod` is passed when the test needs the SAME module object the script
    # runs in — each _issue_module() call execs a fresh one, so a GhError
    # raised from another copy is a different class and is not caught.
    mod = mod or _issue_module()
    watch_json = tmp_path / "watch.json"
    watch_json.write_text("" if watch_doc is None else json.dumps(watch_doc))
    summary = tmp_path / "summary.md"
    code = mod.run(["--repo", "o/r", "--ingest-outcome", ingest,
                    "--watch-exit", str(watch_exit),
                    "--watch-json", str(watch_json),
                    "--run-url", "https://example.invalid/run/1",
                    "--summary", str(summary)], gh=gh)
    return code, summary.read_text() if summary.exists() else ""


def _markers(text: str) -> list[str]:
    return re.findall(r"<!-- memory-watch:key=(\S+) -->", text)


# ── transitions ────────────────────────────────────────────────────────────

def test_a_candidate_crossing_the_bar_is_reported_once_then_silenced_by_a_recorded_baseline(
        tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    # below the bar: five judged, all upheld — a propose row, not a promotion
    _shard(root, "below", [_rec("unmapped:klass", i) for i in range(5)])
    _commit(root, "below the bar")
    before = _tree(root)
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 0, doc
    assert doc["status"] == "quiet" and doc["transitions"] == []
    assert doc["baseline"] is None
    assert _tree(root) == before, "the watch wrote without --record"

    # above it: twelve judged, all upheld (floor 0.757)
    _shard(root, "above", [_rec("unmapped:klass", i, round_n=1)
                           for i in range(7)])
    _commit(root, "above the bar")
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 1, doc
    assert [t["class"] for t in doc["transitions"]] == ["candidate-promotion"]
    crossing = doc["transitions"][0]
    assert crossing["subject"] == "klass"
    assert crossing["numbers"]["judged"] == 12
    assert crossing["numbers"]["wilson_lb"] >= memory_mod.PROMOTE_WILSON_LB
    assert str(memory_mod.PROMOTE_MIN_N) in crossing["bar"]
    assert "nightgate-skills:retro" in crossing["next"]
    assert "no watch record is committed" in doc["note"].lower()

    # the issue reports it once, and a rerun with no new baseline re-reports
    # nothing: the key is stable across runs
    gh = FakeGh()
    assert _report(tmp_path, gh, doc, watch_exit=code)[0] == 0
    code_again, doc_again = _watch(root, monkeypatch, capsys)
    assert doc_again["transitions"] == doc["transitions"]
    assert _report(tmp_path, gh, doc_again, watch_exit=code_again)[0] == 0
    assert len(gh.writes()) == 1, gh.calls
    assert _markers(gh.issues[0]["body"]) == [crossing["key"]]

    # --record writes one dated record and nothing else
    monkeypatch.chdir(root)
    assert cli.main(["memory", "watch", "--record"]) == 1
    capsys.readouterr()
    written = sorted((root / ".warden" / "memory" / "watch").glob("*.json"))
    assert len(written) == 1
    assert set(_tree(root)) - set(before) == {
        ".warden/memory/attest/above.json",
        str(written[0].relative_to(root))}
    # an UNCOMMITTED record is not a baseline
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 1 and doc["baseline"] is None, doc
    _commit(root, "record the watch")
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 0, doc
    assert doc["transitions"] == []
    assert doc["baseline"]["path"] == str(written[0].relative_to(root))


def test_a_rule_entering_pause_territory_is_a_transition(tmp_path, monkeypatch,
                                                         capsys):
    root = _repo(tmp_path)
    _shard(root, "upheld", [_rec("r", i) for i in range(4)])
    for n in range(1, 1 + memory_mod.PAUSE_STREAK):
        _shard(root, f"refuted-{n}", [_rec("r", 0, status="refuted",
                                           round_n=n)])
    _commit(root, "three refuting rounds")
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 1, doc
    pauses = [t for t in doc["transitions"] if t["class"] == "rule-pause"]
    assert [t["subject"] for t in pauses] == ["r"], doc
    assert pauses[0]["numbers"]["streak"] == memory_mod.PAUSE_STREAK
    assert "warden autonomy pause" in pauses[0]["next"]


def test_a_rule_entering_demote_territory_is_a_transition(tmp_path, monkeypatch,
                                                          capsys):
    root = _repo(tmp_path)
    # twelve judged, all argued down by dismissal — a chronic demote, and no
    # refutation streak, so this is not the pause arm
    _shard(root, "dismissed", [_rec("r", i, status="dismissed-with-reason")
                               for i in range(12)])
    _commit(root, "argued down")
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 1, doc
    assert [(t["class"], t["subject"]) for t in doc["transitions"]] == [
        ("rule-demote", "r")], doc
    assert doc["transitions"][0]["numbers"]["argued_down"] == 12
    assert "warden rules lifecycle" in doc["transitions"][0]["next"]


def test_a_skill_file_crossing_the_propose_bar_is_a_transition(
        tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    skill = "skills/pack/skills/deliver/SKILL.md"
    _shard(root, "skill", [_rec("r", i, file=skill)
                           for i in range(memory_mod.CANDIDATE_MIN_N)])
    _commit(root, "skill recurrence")
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 1, doc
    assert [(t["class"], t["subject"]) for t in doc["transitions"]] == [
        ("skill-propose", skill)], doc
    assert "nightgate-skills:retro" in doc["transitions"][0]["next"]


# ── UNREAD: never silence ──────────────────────────────────────────────────

def test_an_unreadable_corpus_is_unread_with_its_own_exit(tmp_path, monkeypatch,
                                                         capsys):
    root = _repo(tmp_path)
    _shard(root, "fine", [_rec("unmapped:klass", i) for i in range(12)])
    (root / ".warden" / "memory" / "attest" / "broken.json").write_text(
        '{"records": [{"rule_id": "r", "status"')
    _commit(root, "a corrupt shard")
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 3, doc
    assert doc["status"] == "unread" and doc["transitions"] == []
    assert any("broken.json" in cause for cause in doc["unread"]), doc


@pytest.mark.parametrize("body", [
    "{not json", json.dumps({"schema": 1}), json.dumps([1, 2]),
    # a record that is well-formed in every way EXCEPT its schema: only the
    # schema clause can refuse it, and read as a baseline it would silence
    # every subject it happens to hold
    json.dumps({"schema": watch_mod.SCHEMA + 1,
                "recorded_at": "2026-09-01T00:00:00+00:00",
                "state": {cls: {} for cls in watch_mod.CLASSES}})])
def test_a_corrupt_baseline_is_unread_not_a_fresh_start(tmp_path, monkeypatch,
                                                       capsys, body):
    root = _repo(tmp_path)
    _shard(root, "above", [_rec("unmapped:klass", i) for i in range(12)])
    watch_dir = root / ".warden" / "memory" / "watch"
    watch_dir.mkdir(parents=True)
    (watch_dir / "watch-20260901T000000Z.json").write_text(body)
    _commit(root, "a corrupt watch record")
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 3, doc
    assert doc["transitions"] == [], (
        "a corrupt baseline read as no baseline would report every over-bar "
        "state as new")
    assert any("watch-20260901T000000Z.json" in c for c in doc["unread"]), doc


def test_a_committed_stray_file_in_the_watch_dir_is_unread(tmp_path,
                                                           monkeypatch,
                                                           capsys):
    """A committed file that is not a watch record means the newest baseline
    cannot be told apart, so the watch must refuse rather than pick the
    newest NAME it recognizes and report quiet against it.

    The stray sorts before the records here, so skipping the check leaves a
    readable baseline and a silent run — the shape a `if strays:` deleted
    from committed_baseline produces."""
    root = _repo(tmp_path)
    _shard(root, "above", [_rec("unmapped:klass", i) for i in range(12)])
    watch_dir = root / ".warden" / "memory" / "watch"
    watch_dir.mkdir(parents=True)
    (watch_dir / "notes.md").write_text("a hand-written note\n")
    (watch_dir / "watch-20260901T000000Z.json").write_text(json.dumps({
        "schema": watch_mod.SCHEMA,
        "recorded_at": "2026-09-01T00:00:00+00:00",
        "state": {cls: ({"klass": {"judged": 12}}
                        if cls == "candidate-promotion" else {})
                  for cls in watch_mod.CLASSES}}))
    _commit(root, "a stray file beside a readable record")
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 3, doc
    assert doc["transitions"] == [] and doc["status"] == "unread"
    assert any("notes.md" in cause for cause in doc["unread"]), doc


def test_the_note_says_when_the_corpus_read_is_not_the_committed_one(
        tmp_path, monkeypatch, capsys):
    """The baseline comes from HEAD but the corpus comes from the WORKING
    TREE, so the two can be different trees: a locally deleted or unstaged
    shard makes a run quiet over a corpus HEAD does not have. The note says
    so rather than letting 'no transitions' stand for the committed corpus."""
    root = _repo(tmp_path)
    _shard(root, "above", [_rec("unmapped:klass", i) for i in range(12)])
    _commit(root, "above the bar")
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 1, doc
    assert "working tree" not in doc["note"].lower(), doc

    # the committed shard, moved aside: nothing is over a bar in the tree the
    # watch actually read, and HEAD still holds the crossing
    (root / ".warden" / "memory" / "attest" / "above.json").unlink()
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 0 and doc["transitions"] == [], doc
    assert "working tree" in doc["note"].lower(), doc
    assert "above.json" in doc["note"], doc


# ── the note names EVERY working-tree input, not only the shards ──────────
#
# `watch` reads four working-tree inputs — repo.yaml (which
# declares the rules dir), the rule files, tags.yaml and the attest shards —
# and its note once named the shards alone. An uncommitted `covers:` line in a rule
# file therefore moved the report with the note byte-identical to a clean
# tree's. One named regression test per input.

def _dirty(root: Path, which: str) -> str:
    """Leave one watched input differing from HEAD. Returns its path."""
    if which == "rule file":
        rule = root / ".warden" / "rules" / "r.md"
        rule.write_text(rule.read_text().replace(
            "applies_to: ['**']", "applies_to: ['**']\ncovers: [klass]"))
        return ".warden/rules/r.md"
    if which == "repo.yaml":
        # a VALID edit that leaves the state unchanged — the point is that
        # the tree the state was read from is not HEAD's, not that this one
        # edit moved it
        (root / "repo.yaml").write_text(
            _REPO_YAML.replace("reason: production code",
                               "reason: production code, re-worded"))
        return "repo.yaml"
    if which == "tags.yaml":
        (root / ".warden" / "memory" / "tags.yaml").write_text(
            "tags:\n  klass: a class\n")
        return ".warden/memory/tags.yaml"
    raise AssertionError(which)


@pytest.mark.parametrize("which,label", [("rule file", "the rule files"),
                                         ("repo.yaml", "repo.yaml"),
                                         ("tags.yaml", "the tag vocabulary")])
def test_the_note_names_an_uncommitted_input_that_is_not_a_shard(
        tmp_path, monkeypatch, capsys, which, label):
    """Every input the state is read from moves the reading, so an
    uncommitted one is named — with its path and the input it belongs to."""
    root = _repo(tmp_path)
    _shard(root, "above", [_rec("unmapped:klass", i) for i in range(12)])
    _commit(root, "above the bar")
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 1 and "working tree" not in doc["note"].lower(), doc

    path = _dirty(root, which)
    code, doc = _watch(root, monkeypatch, capsys)
    assert doc["status"] != "unread", doc
    assert "working tree" in doc["note"].lower(), doc
    assert path in doc["note"] and label in doc["note"], doc
    assert "1 of the 4 input(s)" in doc["note"], doc


def test_a_renamed_input_is_named_by_both_its_paths(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    """A rename's `-z` record is TWO fields — `XY <new>` then the ORIGINAL
    path with no status prefix — so stripping three bytes from every field
    eats three characters off the original and labels it nothing. The note
    then names a path that does not exist."""
    root = _repo(tmp_path)
    _shard(root, "above", [_rec("unmapped:klass", i) for i in range(12)])
    _commit(root, "above the bar")
    _git(root, "mv", ".warden/rules/r.md", ".warden/rules/renamed.md")

    drift = watch_mod.uncommitted_inputs(root)
    assert sorted(drift.differing) == [
        ("the rule files", ".warden/rules/r.md"),
        ("the rule files", ".warden/rules/renamed.md")], drift
    assert all(label for label, _ in drift.differing), (
        "a path came back with no input label, so the record was mis-parsed")

    code, doc = _watch(root, monkeypatch, capsys)
    assert doc["status"] != "unread", doc
    assert ".warden/rules/r.md" in doc["note"], doc
    # the mis-parse's signature: a label-less entry holding a path three
    # characters short of a real one
    assert "; : " not in doc["note"] and "rden/rules/r.md" not in \
        doc["note"].replace(".warden/rules/r.md", ""), doc
    assert "1 of the 4 input(s)" in doc["note"], doc


def test_a_rename_across_two_watched_inputs_credits_each_half(
        tmp_path, monkeypatch, capsys):
    """A rename can span TWO watched inputs, and both readings moved.

    `git mv .warden/rules/r2.md .warden/memory/attest/r2.md` takes a rule out
    of the rules dir and drops a file into the shard dir. Taking the first
    path's label for the whole record credited both halves to the attest
    shards — a rules-file path printed as a shard, and the rules dir reported
    clean although a rule had left it.
    """
    root = _repo(tmp_path)
    _shard(root, "above", [_rec("unmapped:klass", i) for i in range(12)])
    (root / ".warden" / "rules" / "r2.md").write_text(
        "---\nid: r2\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\n---\nbody\n")
    _commit(root, "above the bar, two rules")
    _git(root, "mv", ".warden/rules/r2.md", ".warden/memory/attest/r2.md")

    drift = watch_mod.uncommitted_inputs(root)
    assert sorted(drift.differing) == [
        ("the attest shards", ".warden/memory/attest/r2.md"),
        ("the rule files", ".warden/rules/r2.md")], drift

    code, doc = _watch(root, monkeypatch, capsys)
    assert doc["status"] != "unread", doc
    # the tally counts BOTH inputs: the rules dir is not clean, it lost a rule
    assert "2 of the 4 input(s) it is read from, over 2 path(s)" \
        in doc["note"], doc
    assert "the rule files: .warden/rules/r2.md" in doc["note"], doc
    assert "the attest shards: .warden/memory/attest/r2.md" \
        in doc["note"], doc
    # the mis-credit's signature: the rules path named under the shard label
    assert "the attest shards: .warden/rules/r2.md" not in doc["note"], doc


def test_a_rename_out_of_the_watched_specs_is_not_a_rename_to_git(
        tmp_path):
    """The case the one-label rule was argued from, run against git.

    `git mv README.md .warden/rules/x.md` was cited as a rename whose
    ORIGINAL path sits outside every spec. Under a pathspec git filters the
    deletion side out BEFORE rename detection and emits `A  <new>` with no
    original field, so that record never reaches the labelling at all.
    `UNWATCHED` therefore stands as a defensive default,
    proven at the labeller rather than through a case git does not produce.
    """
    root = _repo(tmp_path)
    (root / "README.md").write_text("a\nb\nc\nd\ne\nf\ng\n")
    _commit(root, "a readme outside every watched input")
    _git(root, "mv", "README.md", ".warden/rules/x.md")

    specs, _ = watch_mod.watched_inputs(root)
    out = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all", "--",
         *specs.values()], cwd=root, check=True, capture_output=True).stdout
    assert watch_mod._porcelain_records(out) == [[".warden/rules/x.md"]], out
    assert watch_mod.uncommitted_inputs(root).differing == [
        ("the rule files", ".warden/rules/x.md")]

    # the default itself, exercised where it can actually be reached
    assert watch_mod._input_label("README.md", specs) == ""
    assert (watch_mod._input_label("README.md", specs)
            or watch_mod.UNWATCHED) == watch_mod.UNWATCHED


def test_the_note_names_every_differing_input_not_the_first_five_paths(
        tmp_path, monkeypatch, capsys):
    """Eight differing paths across three inputs: every INPUT is named.

    The enumeration capped the FLAT list at five (label, path) pairs, sorted
    by input order, so the six rule files ate the budget and the note said
    "3 of the 4 input(s) ... over 8 path(s)" while naming the rule files
    alone — the count right, the enumeration short, which is the silent drop
    the full-input note closes, surviving inside its own fix.
    """
    root = _repo(tmp_path)
    _shard(root, "above", [_rec("unmapped:klass", i) for i in range(12)])
    _commit(root, "above the bar")
    for i in range(6):                       # six uncommitted rule files
        (root / ".warden" / "rules" / f"extra{i}.md").write_text(
            f"---\nid: extra{i}\nseverity: LOW\nengine: claude\n"
            "applies_to: ['**']\n---\nbody\n")
    _dirty(root, "tags.yaml")                # + the vocabulary
    _shard(root, "local", [_rec("unmapped:other", 0)])   # + a shard

    code, doc = _watch(root, monkeypatch, capsys)
    assert doc["status"] != "unread", doc
    assert "3 of the 4 input(s) it is read from, over 8 path(s)" \
        in doc["note"], doc
    for label in ("the rule files", "the tag vocabulary",
                  "the attest shards"):
        assert label + ":" in doc["note"], (
            f"{label} differs from HEAD and the note never names it — the "
            "count says three inputs moved the reading and the reader is "
            f"told about {doc['note'].count(': ')}")
    assert ".warden/memory/tags.yaml" in doc["note"], doc
    assert ".warden/memory/attest/local.json" in doc["note"], doc
    # a long input elides its OWN paths and says how many it held back
    assert "(+3 more path(s))" in doc["note"], doc


def test_an_elided_input_still_names_itself_and_counts_its_paths():
    """The enumeration, unit-level: every label present, paths capped per
    label, and the elision counted rather than a bare ellipsis."""
    pairs = [("the rule files", f"r{i}.md") for i in range(5)]
    pairs += [("the tag vocabulary", "tags.yaml")]
    said = watch_mod.describe_uncommitted(pairs)
    assert said == ("the rule files: r0.md, r1.md, r2.md (+2 more path(s)); "
                    "the tag vocabulary: tags.yaml"), said
    assert watch_mod.describe_uncommitted(pairs[:1]) == \
        "the rule files: r0.md"
    assert watch_mod.describe_uncommitted([]) == ""

    # EVERY input named, however many paths crowd in ahead of it — driven
    # against the subject, because the line that used to sit here asserted a
    # property of the `pairs` literal and never called `describe_uncommitted`
    # at all: true by construction, unfalsifiable by any change to the
    # enumeration it claimed to pin (round 1 of this PR's review).
    crowded = [("the rule files", f"r{i}.md") for i in range(9)]
    crowded += [("the tag vocabulary", "tags.yaml"),
                ("the attest shards", "s.json")]
    said = watch_mod.describe_uncommitted(crowded)
    for label in ("the rule files", "the tag vocabulary", "the attest shards"):
        assert f"{label}:" in said, (label, said)
    assert "(+6 more path(s))" in said, said
    # and no path of a NAMED input is invented: the elision counts, the
    # enumeration does not pad
    assert "r3.md" not in said, said


def test_the_note_counts_inputs_and_paths_apart(tmp_path, monkeypatch,
                                                capsys):
    """Six uncommitted shards are ONE input differing, over six paths. The
    first cut printed `6 of the input(s)` with four inputs in existence.
    """
    root = _repo(tmp_path)
    _shard(root, "above", [_rec("unmapped:klass", i) for i in range(12)])
    _commit(root, "above the bar")
    for i in range(6):
        _shard(root, f"local{i}", [_rec("unmapped:other", 0)])
    code, doc = _watch(root, monkeypatch, capsys)
    assert doc["status"] != "unread", doc
    assert "1 of the 4 input(s) it is read from, over 6 path(s)" \
        in doc["note"], doc


@pytest.mark.parametrize("which", ["rule file", "repo.yaml", "tags.yaml"])
def test_record_refuses_over_any_uncommitted_watched_input(tmp_path,
                                                           monkeypatch,
                                                           capsys, which):
    """A baseline written over an input HEAD does not hold records a state
    CI's checkout cannot reproduce — for every input, not only the shards."""
    root = _repo(tmp_path)
    _shard(root, "above", [_rec("unmapped:klass", i) for i in range(12)])
    _commit(root, "above the bar")
    path = _dirty(root, which)
    monkeypatch.chdir(root)
    assert cli.main(["memory", "watch", "--record"]) == 2
    err = capsys.readouterr().err
    assert "not all committed" in err and path in err, err
    assert not (root / ".warden" / "memory" / "watch").exists()


def test_the_watched_inputs_are_the_four_the_note_promises(tmp_path):
    """The pathspec side of the note's promise, by the names it prints.

    Named for what it checks: an earlier spelling called itself "every input
    read_state reads" while comparing a typed set, and `read_state` never
    appeared in it. The test below is the half
    that drives `read_state`.
    """
    root = _repo(tmp_path)
    specs, uncomparable = watch_mod.watched_inputs(root)
    assert not uncomparable
    assert set(specs) == {"repo.yaml", "the rule files",
                          "the tag vocabulary", "the attest shards"}
    assert specs["the rule files"] == ".warden/rules/"
    assert specs["the tag vocabulary"] == ".warden/memory/tags.yaml"
    assert specs["the attest shards"] == ".warden/memory/attest/"


@pytest.mark.parametrize("which", ["rule file", "repo.yaml", "tags.yaml"])
def test_every_watched_input_can_move_what_read_state_returns(
        tmp_path, which):
    """The watched list earns its name by DRIVING `read_state`: each input
    is one whose working-tree content the state actually depends on."""
    root = _repo(tmp_path)
    _shard(root, "above", [_rec("unmapped:klass", i) for i in range(12)])
    before, causes = watch_mod.read_state(root)
    assert not causes and before["candidate-promotion"], causes
    if which == "rule file":
        # a rule that COVERS the class takes the candidate off the row
        rule = root / ".warden" / "rules" / "r.md"
        rule.write_text(rule.read_text().replace(
            "applies_to: ['**']", "applies_to: ['**']\ncovers: [klass]"))
    elif which == "repo.yaml":
        # the declaration alone moves the reading: the same corpus judged
        # against a DIFFERENT ruleset, one that covers the class
        (root / ".warden" / "elsewhere").mkdir()
        (root / ".warden" / "elsewhere" / "r.md").write_text(
            (root / ".warden" / "rules" / "r.md").read_text().replace(
                "applies_to: ['**']", "applies_to: ['**']\ncovers: [klass]"))
        (root / "repo.yaml").write_text(_REPO_YAML.replace(
            "rules_dir: .warden/rules", "rules_dir: .warden/elsewhere"))
    else:
        (root / ".warden" / "memory" / "tags.yaml").write_text(
            "aliases:\n  klass: other-klass\ntags:\n"
            "  other-klass: the fold target\n")
    after, causes = watch_mod.read_state(root)
    assert not causes, causes
    assert after != before, (
        f"an uncommitted {which} left the state byte-identical, so the note "
        "names an input the reading does not depend on")


def test_the_skill_pack_is_read_by_stats_and_is_not_a_watched_input(tmp_path):
    """The fifth-input question, answered rather than assumed.

    `read_state` calls `memory.stats`, which reads the working-tree skill
    pack for SKILL-STEP PROVENANCE. That read feeds `quiet_steps`, which the
    watch state does not consume — so the pack is correctly NOT watched, and
    this proves it instead of leaving the omission to be re-discovered.
    """
    root = _repo(tmp_path)
    _shard(root, "above", [_rec("unmapped:klass", i) for i in range(12)])
    before, causes = watch_mod.read_state(root)
    assert not causes, causes
    pack = root / "skills" / "nightgate-skills" / "skills" / "s"
    pack.mkdir(parents=True)
    (pack / "SKILL.md").write_text(
        "---\nname: s\ndescription: d\n---\n\n# s\n\n"
        "## Provenance\n\n```yaml\nas_of: 2026-09-20\nsteps:\n"
        "  - step: \"do the thing\"\n    evidence: tag:klass\n```\n")
    after, causes = watch_mod.read_state(root)
    assert not causes, causes
    assert after == before, (
        "the working-tree skill pack moved the watched state, so it is a "
        "fifth input the note must name")
    specs, _ = watch_mod.watched_inputs(root)
    assert not any(spec.startswith("skills/") for spec in specs.values())


def test_a_rules_dir_declared_outside_the_repo_is_reported_uncomparable(
        tmp_path, monkeypatch, capsys):
    """git cannot compare a path outside the repo against HEAD, so it is
    named as an input whose drift is UNKNOWN rather than dropped — and
    --record refuses over it."""
    root = _repo(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "r.md").write_text(
        "---\nid: r\nseverity: LOW\nengine: claude\n"
        "applies_to: ['**']\n---\nbody\n")
    (root / "repo.yaml").write_text(
        _REPO_YAML.replace("rules_dir: .warden/rules",
                           f"rules_dir: {outside}"))
    _commit(root, "rules live outside the repo")
    drift = watch_mod.uncommitted_inputs(root)
    assert drift.differing == [], drift
    assert [label for label, _ in drift.uncomparable] == ["the rule files"]
    assert "outside this repo" in drift.uncomparable[0][1]
    assert not drift.clean and drift.inputs == 4

    # UNKNOWN, never "differs": an input git was not asked about is not an
    # input known to have changed.
    said = watch_mod.describe_drift(drift)
    assert "UNKNOWN" in said and "could not be compared" in said, said
    assert "differs from HEAD" not in said, said

    monkeypatch.chdir(root)
    assert cli.main(["memory", "watch", "--record"]) == 2
    err = capsys.readouterr().err
    assert "outside this repo" in err and "UNKNOWN" in err, err


def test_no_readable_git_history_is_unread(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    _shard(root, "above", [_rec("unmapped:klass", i) for i in range(12)])
    import shutil
    shutil.rmtree(root / ".git")
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 3, doc
    assert any("committed" in c for c in doc["unread"]), doc


def test_record_refuses_while_unread(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    (root / ".warden" / "memory" / "attest" / "broken.json").write_text("[]")
    _commit(root, "a corrupt shard")
    monkeypatch.chdir(root)
    assert cli.main(["memory", "watch", "--record"]) == 3
    assert not (root / ".warden" / "memory" / "watch").exists()


def test_record_refuses_while_attest_shards_are_uncommitted(tmp_path,
                                                          monkeypatch, capsys):
    """A record written over a shard HEAD does not hold would take in a
    subject CI cannot see, and when the shard lands its crossing would be
    silenced."""
    root = _repo(tmp_path)
    _shard(root, "local", [_rec("unmapped:klass", i) for i in range(12)])
    monkeypatch.chdir(root)
    assert cli.main(["memory", "watch", "--record"]) == 2
    assert "not all committed" in capsys.readouterr().err
    assert not (root / ".warden" / "memory" / "watch").exists()


def test_a_subject_with_a_space_still_makes_a_one_token_key(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    root = _repo(tmp_path)
    skill = "skills/pack/skills/my skill/SKILL.md"
    _shard(root, "skill", [_rec("r", i, file=skill)
                           for i in range(memory_mod.CANDIDATE_MIN_N)])
    _commit(root, "a spaced skill path")
    code, doc = _watch(root, monkeypatch, capsys)
    assert code == 1, doc
    (crossing,) = doc["transitions"]
    assert crossing["subject"] == skill and " " not in crossing["key"], doc
    gh = FakeGh()
    assert _report(tmp_path, gh, doc, watch_exit=code)[0] == 0
    assert _markers(gh.issues[0]["body"]) == [crossing["key"]]


def test_a_failing_ingest_is_unread_even_when_the_watch_says_quiet(tmp_path):
    gh = FakeGh()
    quiet = {"schema": 1, "status": "quiet", "unread": [], "transitions": [],
             "baseline": None, "note": ""}
    code, summary = _report(tmp_path, gh, quiet, watch_exit=0,
                            ingest="failure")
    assert code != 0
    assert len(gh.issues) == 1 and "UNREAD" in gh.issues[0]["body"]
    assert "warden memory ingest" in gh.issues[0]["body"]
    assert "UNREAD" in summary


@pytest.mark.parametrize("watch_exit,doc", [
    (3, {"schema": 1, "status": "unread", "transitions": [], "baseline": None,
         "unread": ["the committed corpus could not be read: x.json"],
         "note": ""}),
    (2, None),            # a crash: exit 2 and nothing parseable
    (0, {"schema": 1, "status": "transitions", "unread": [], "baseline": None,
         "note": "", "transitions": [{"key": "k"}]}),   # exit and status disagree
    (1, {"schema": 1, "status": "transitions", "unread": [], "baseline": None,
         "note": "", "transitions": []}),   # exit 1 naming no transition
])
def test_the_issue_script_reads_every_unread_shape_as_unread(tmp_path,
                                                            watch_exit, doc):
    gh = FakeGh()
    code, summary = _report(tmp_path, gh, doc, watch_exit=watch_exit)
    assert code != 0
    assert "UNREAD" in gh.issues[0]["body"] and "UNREAD" in summary


# ── the standing issue ─────────────────────────────────────────────────────

def _transitions(*keys: str) -> dict:
    return {"schema": 1, "status": "transitions", "unread": [],
            "baseline": None, "note": "no watch record is committed",
            "transitions": [
                {"key": k, "class": k.split(":")[0], "subject": k,
                 "numbers": {"judged": 12}, "bar": "the bar",
                 "next": "run the retro"} for k in keys]}


def test_the_issue_script_creates_the_issue_when_absent(tmp_path):
    gh = FakeGh()
    code, summary = _report(tmp_path, gh, _transitions("a:x@no-baseline"),
                            watch_exit=1)
    assert code == 0
    assert [c[:2] for c in gh.writes()] == [["issue", "create"]]
    assert ["label", "create"] in [c[:2] for c in gh.calls]
    body = gh.issues[0]["body"]
    assert _markers(body) == ["a:x@no-baseline"]
    assert "a bead cannot be filed from CI" in body
    assert "a bead cannot be filed from CI" in summary


def test_the_issue_script_comments_on_the_standing_issue_when_present(tmp_path):
    gh = FakeGh()
    _report(tmp_path, gh, _transitions("a:x@no-baseline"), watch_exit=1)
    code, _ = _report(tmp_path, gh,
                      _transitions("a:x@no-baseline", "b:y@no-baseline"),
                      watch_exit=1)
    assert code == 0
    assert len(gh.issues) == 1
    assert [_markers(c) for c in gh.issues[0]["comments"]] == [
        ["b:y@no-baseline"]], "only the new key is commented"


def test_the_issue_script_skips_a_key_already_reported(tmp_path):
    reported = ("earlier\n<!-- memory-watch:key=a:x@no-baseline -->\n")
    gh = FakeGh([{"number": 7, "state": "OPEN", "labels": ["memory-watch"],
                  "title": _issue_module().TITLE, "body": "intro",
                  "comments": [reported]}])
    code, summary = _report(tmp_path, gh, _transitions("a:x@no-baseline"),
                            watch_exit=1)
    assert code == 0
    assert gh.writes() == [], gh.calls
    assert "already reported" in summary


def test_no_transitions_is_silent_with_a_one_line_summary(tmp_path):
    gh = FakeGh()
    quiet = {"schema": 1, "status": "quiet", "unread": [], "transitions": [],
             "baseline": None, "note": ""}
    code, summary = _report(tmp_path, gh, quiet, watch_exit=0)
    assert code == 0
    assert gh.calls == []
    assert len(summary.strip().splitlines()) == 1, summary
    assert "a bead cannot be filed from CI" in summary


def test_a_gh_call_that_fails_fails_the_job_with_its_own_exit(tmp_path):
    """`gh` unreachable is not 'nothing to report'. The script's docstring
    says exit 2, and the workflow's last step is what turns that into a red
    job: returning 0 there reports nothing on a green run."""
    mod = _issue_module()

    def raising_gh(args: list) -> str:
        raise mod.GhError("gh issue list failed: not logged in")

    code, summary = _report(tmp_path, raising_gh,
                            _transitions("a:x@no-baseline"), watch_exit=1,
                            mod=mod)
    assert code == 2, summary
    assert "not logged in" in summary, summary


def test_a_key_reported_on_a_closed_issue_is_not_reported_again(tmp_path):
    """The dedupe reads issues in ANY state: somebody closing the standing
    issue must not make every key on it new again. Only `--state all` reaches
    a closed issue, so dropping that pair re-opens the whole backlog."""
    gh = FakeGh([{"number": 7, "state": "CLOSED", "labels": ["memory-watch"],
                  "title": _issue_module().TITLE, "body": "intro",
                  "comments": ["done\n<!-- memory-watch:key=a:x@no-baseline -->\n"]}])
    code, summary = _report(tmp_path, gh, _transitions("a:x@no-baseline"),
                            watch_exit=1)
    assert code == 0
    assert gh.writes() == [], gh.calls
    assert "already reported" in summary, summary


@pytest.mark.parametrize("outcome", ["cancelled", "skipped", ""])
def test_an_ingest_that_did_not_succeed_is_unread_whatever_it_is_called(
        tmp_path, outcome):
    """`steps.<id>.outcome` takes four values, not two. Anything but
    `success` means the corpus the watch read may be stale, so the run is
    UNREAD — reading only `failure` as bad silences the other two."""
    gh = FakeGh()
    quiet = {"schema": 1, "status": "quiet", "unread": [], "transitions": [],
             "baseline": None, "note": ""}
    code, summary = _report(tmp_path, gh, quiet, watch_exit=0, ingest=outcome)
    assert code != 0, summary
    assert len(gh.issues) == 1, gh.calls
    assert "UNREAD" in gh.issues[0]["body"] and "UNREAD" in summary
    assert "warden memory ingest" in gh.issues[0]["body"]


class _FakeSubprocess:
    """The names `default_gh` reaches, with real subprocess semantics: a
    non-zero exit raises only when the caller passed `check=True`."""

    CalledProcessError = subprocess.CalledProcessError
    SubprocessError = subprocess.SubprocessError

    def __init__(self, returncode: int = 3, stderr: str = "gh: not logged in"):
        self.returncode, self.stderr = returncode, stderr
        self.kwargs: dict = {}

    def run(self, argv, **kwargs):
        self.kwargs = kwargs
        if kwargs.get("check") and self.returncode:
            raise subprocess.CalledProcessError(self.returncode, argv,
                                                output="", stderr=self.stderr)
        return subprocess.CompletedProcess(argv, self.returncode, stdout="",
                                           stderr=self.stderr)


def test_the_default_gh_raises_on_a_failed_call_rather_than_returning_it(
        monkeypatch):
    """The fake `gh` every other test drives is not the one CI runs. The real
    callable must pass `check=True`, or a failed `gh issue comment`/`create`
    returns its empty stdout, the script reports success, and the transition
    is never posted anywhere."""
    mod = _issue_module()
    fake = _FakeSubprocess()
    monkeypatch.setattr(mod, "subprocess", fake)
    with pytest.raises(mod.GhError) as caught:
        mod.default_gh(["issue", "create", "--repo", "o/r"])
    assert "not logged in" in str(caught.value)
    assert fake.kwargs.get("check") is True, fake.kwargs
    assert fake.kwargs.get("capture_output") and fake.kwargs.get("text")


# ── the workflow ───────────────────────────────────────────────────────────

def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def test_the_workflow_runs_on_a_schedule_and_by_hand():
    doc = _workflow()
    triggers = doc.get("on", doc.get(True))   # YAML 1.1 reads `on:` as True
    assert isinstance(triggers, dict), triggers
    assert set(triggers) == {"schedule", "workflow_dispatch"}, triggers
    crons = [entry["cron"] for entry in triggers["schedule"]]
    assert len(crons) == 1 and len(crons[0].split()) == 5, crons


def test_the_workflow_reads_contents_writes_issues_and_never_pushes():
    doc = _workflow()
    assert doc["permissions"] == {"contents": "read", "issues": "write"}
    for name, job in doc["jobs"].items():
        assert "permissions" not in job, f"job {name} widens permissions"
        for step in job["steps"]:
            run = step.get("run", "")
            assert not re.search(r"\bgit\s+(push|commit)\b", run), step
            uses = step.get("uses")
            if uses:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", uses), \
                    f"unpinned action {uses}"
                if uses.startswith("actions/checkout@"):
                    assert step.get("with", {}).get(
                        "persist-credentials") is False


def test_the_workflow_ingests_then_watches_then_reports_in_bash():
    doc = _workflow()
    (job,) = doc["jobs"].values()
    assert job.get("defaults", {}).get("run", {}).get("shell") == "bash"
    runs = [s.get("run", "") for s in job["steps"]]
    order = [next(i for i, r in enumerate(runs) if needle in r)
             for needle in ("warden memory ingest", "warden memory watch",
                            "scripts/memory-watch-issue.py")]
    assert order == sorted(order), runs
    ingest = job["steps"][order[0]]
    assert "||" not in ingest["run"] and not ingest.get("continue-on-error"), \
        "the ingest step's exit must stand"
