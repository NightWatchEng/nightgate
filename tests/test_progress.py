"""`warden progress` — the age of the last boundary, DRIVEN rather than read.

With parallel units, an orchestrator whose only window into any of them is
the final report answers every status question by inspecting worktrees off
disk (`git log`, `ls .warden/out/rounds`, `find -newermt`, `ps`). What stays
invisible that way: a unit that finished a fix, stalled and DIED with commits
local and unpushed looks identical from outside to an agent still working; so
does one stopped a step from shipping, or one whose entire report is
"Waiting.".

The acceptance criterion is the first test here: boundaries for two synthetic
units, and the table must distinguish one ten minutes past its last boundary
from one three hours past it. The second is that it works for a unit that DIED
— the value is that a dead unit still leaves its last boundary, so nothing here
asks the unit to cooperate beyond the call that already happened.
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from warden import progress as progress_mod


def _git(unit: Path, *args: str) -> str:
    out = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                          *args], cwd=unit, capture_output=True, text=True,
                         check=True)
    return out.stdout.strip()


def _commit(unit: Path, text: str) -> str:
    (unit / "file.txt").write_text(text)
    _git(unit, "add", "file.txt")
    _git(unit, "commit", "-q", "-m", text)
    return _git(unit, "rev-parse", "HEAD")


def _unit(root: Path, name: str, *, boundaries=(), branch: str = "") -> Path:
    """A synthetic unit: a REAL one-commit checkout with a boundary log.

    Real git, not a `.git` pointer at nowhere (`tests-bite`). With `gitdir:
    /nowhere/<name>`, `_git(..., "rev-parse", "HEAD")` fails for every unit
    and `Unit.head` is always `""` — which short-circuits `Unit.moved`, so NO
    row rendered through `table()` could ever be starred and the moved-head
    rendering would have nothing able to fail on it. A boundary whose `head`
    is None is recorded at the
    unit's actual head, which is what lets a test say "this unit has not moved"
    and mean it.
    """
    unit = root / name
    unit.mkdir(parents=True)
    _git(unit, "init", "-q", "-b", "main")
    head = _commit(unit, name)
    for boundary, at, ts in boundaries:
        progress_mod.log_path(unit).parent.mkdir(parents=True, exist_ok=True)
        with progress_mod.log_path(unit).open("a") as fh:
            fh.write(json.dumps({"boundary": boundary, "head": at or head,
                                 "ts": ts.isoformat(timespec="seconds"),
                                 "detail": branch}) + "\n")
    return unit


def test_the_table_tells_a_ten_minute_unit_from_a_three_hour_one(tmp_path):
    """THE acceptance criterion, driven: two units, two ages, one table.

    Stalled and working look identical when nothing carries an age, and
    without this the number is reconstructed by hand with `find -newermt`.
    This is that number, rendered.
    """
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    _unit(tmp_path, "fresh", boundaries=[
        ("round-attested", "a" * 40, now - timedelta(minutes=10))])
    _unit(tmp_path, "stalled", boundaries=[
        ("round-minted", "b" * 40, now - timedelta(hours=3))])

    text = progress_mod.table(tmp_path, now=now)
    rows = {line.split()[0]: line for line in text.splitlines()
            if line.split() and line.split()[0] in ("fresh", "stalled")}
    assert "10m" in rows["fresh"], rows["fresh"]
    assert "3h 00m" in rows["stalled"], rows["stalled"]
    assert "head" in text and "at" in text
    assert rows["fresh"] != rows["stalled"]
    # stalest first: the end of the list anyone reading this is looking for
    assert text.index("stalled") < text.index("fresh")
    assert "round-attested" in rows["fresh"]
    assert "round-minted" in rows["stalled"]
    assert "age is time since the last boundary" in text


def test_a_unit_that_died_still_reports_its_last_boundary(tmp_path):
    """No cooperation beyond the call that already happened.

    A unit that dies with commits local writes nothing after its last
    boundary — not a shutdown hook, not a final report. The record is a file,
    appended as the boundary was passed, so it is still there.
    """
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    unit = _unit(tmp_path, "dead", boundaries=[
        ("round-minted", "a" * 40, now - timedelta(hours=5)),
        ("round-attested", "a" * 40, now - timedelta(hours=4))])
    # killed mid-write: a truncated tail line must not hide the boundary before it
    with progress_mod.log_path(unit).open("a") as fh:
        fh.write('{"boundary": "pus')

    entry = progress_mod.last_boundary(unit)
    assert entry["boundary"] == "round-attested"
    assert "4h 00m" in progress_mod.table(tmp_path, now=now)


def test_a_unit_with_no_boundary_is_named_rather_than_omitted(tmp_path):
    """A unit that died before its first boundary is the one a log-keyed
    discovery would make invisible, so units are discovered by being CHECKOUTS
    and an empty one renders as NONE RECORDED with an unknown age."""
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    _unit(tmp_path, "silent")
    _unit(tmp_path, "working", boundaries=[
        ("verify-run", "a" * 40, now - timedelta(minutes=2))])
    text = progress_mod.table(tmp_path, now=now)
    assert "NONE RECORDED" in text and "unknown" in text
    # unknown sorts FIRST: it is maximally suspicious, not reassuring
    assert text.index("silent") < text.index("working")


def test_a_head_that_moved_past_the_last_boundary_is_marked(tmp_path):
    """The signature of a unit that finished and never shipped: work
    committed AFTER the last boundary it reported.

    Visible in the table rather than reconstructed from `git log`.
    """
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    unit = _unit(tmp_path, "u", boundaries=[
        ("round-attested", "a" * 40, now - timedelta(hours=2))])
    reported = progress_mod.Unit(name="u", path=unit, branch="feat/x",
                                head="b" * 40,
                                boundary=progress_mod.last_boundary(unit))
    assert reported.moved
    same = progress_mod.Unit(name="u", path=unit, branch="feat/x",
                             head="a" * 40,
                             boundary=progress_mod.last_boundary(unit))
    assert not same.moved
    # an UNKNOWN head is not a moved head: "could not determine" must not
    # render as a claim that work happened
    unknown = progress_mod.Unit(name="u", path=unit, branch="feat/x",
                                head=progress_mod.runs.UNKNOWN_SHA,
                                boundary=progress_mod.last_boundary(unit))
    assert not unknown.moved


def test_a_boundary_whose_head_is_unknown_is_never_read_as_moved(tmp_path):
    """`unmapped:tri-state-rendered-as-determined`, on the boundary side.

    `record` stores `runs.head_sha`'s UNKNOWN_SHA as-is when git cannot answer,
    and `warden verify` passes exactly that value through — so a boundary whose
    head is "unknown" is reachable from a shipped call site, not only by hand.
    Guarding UNKNOWN on the UNIT side only (which `_git` can never produce —
    dead code) would leave such a boundary rendering as a positive claim that
    work had been committed since. Two shas that differ because one of them is
    not a sha are not evidence of anything.
    """
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    unit = _unit(tmp_path, "u", boundaries=[
        ("verify-run", progress_mod.runs.UNKNOWN_SHA, now - timedelta(hours=1))])
    reported = progress_mod.Unit(name="u", path=unit, branch="feat/x",
                                 head="b" * 40,
                                 boundary=progress_mod.last_boundary(unit))
    assert not reported.moved
    text = progress_mod.table(tmp_path, now=now)
    assert "*" not in text, text
    assert "committed work" not in text


def test_the_star_marks_a_moved_head_in_the_rendered_table(tmp_path):
    """`tests-bite`. The star is the signature of a unit that finished and
    never shipped — work committed after the last boundary — and a synthetic
    unit with an unreadable git could never render one: `Unit.head` is `""`
    and `Unit.moved` is False for every row `table()` builds.

    Driven end to end: a boundary at the unit's real head is unstarred, and the
    same unit after one more commit is starred, with `head` and `at` carrying
    the two different shas they are labelled with.
    """
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    unit = _unit(tmp_path, "u", boundaries=[
        ("round-attested", None, now - timedelta(minutes=5))])
    at = progress_mod.last_boundary(unit)["head"]

    text = progress_mod.table(tmp_path, now=now)
    assert "*" not in text, f"an unmoved unit was starred:\n{text}"
    assert at[:12] in text

    moved_head = _commit(unit, "the work nobody recorded a boundary for")
    text = progress_mod.table(tmp_path, now=now)
    row = next(ln for ln in text.splitlines() if ln.startswith("u "))
    assert f"{at[:12]} *" in row, f"the moved head is not starred:\n{row}"
    assert moved_head[:12] in row, (
        "the unit's actual head — the only sha a reader can act on — is not "
        f"in its row:\n{row}")
    assert row.index(moved_head[:12]) < row.index(at[:12]), (
        "`head` and `at` are the wrong way round against their column headers")
    assert "committed work and recorded no boundary since" in text


def test_an_unreadable_log_is_not_a_unit_that_reached_nothing(tmp_path):
    """Two states that look alike, kept apart: "no boundary recorded" and "its
    log could not be read". Rendering the second as the first is the fail-open
    shape, and one broken unit must not hide the units beside it."""
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    broken = _unit(tmp_path, "broken")
    # a DIRECTORY where the log belongs: readable path, unreadable log
    progress_mod.log_path(broken).mkdir(parents=True)
    _unit(tmp_path, "ok", boundaries=[
        ("pushed", "a" * 40, now - timedelta(minutes=1))])

    with pytest.raises(progress_mod.ProgressError):
        progress_mod.last_boundary(broken)
    text = progress_mod.table(tmp_path, now=now)
    assert "UNREADABLE" in text
    assert "! broken:" in text
    assert "pushed" in text, "one broken unit hid the unit beside it"


def test_a_log_with_content_and_no_boundary_is_unreadable_not_unreached(
        tmp_path):
    """`unmapped:corrupt-input-reads-as-empty`.

    A log that exists, carries content, and yields no boundary — a stray
    redirect, a binary paste, a partially overwritten file — must not return
    None, which is identical to a unit that has recorded nothing. That is the
    state this module's own ProgressError docstring forbids producing, and the
    oversized arm one line above already treats "this is not a boundary log any
    more" as a refusal.
    """
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    unit = _unit(tmp_path, "corrupt")
    path = progress_mod.log_path(unit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\x00\x01 not json at all\n{\"no\": \"boundary\"}\n")
    with pytest.raises(progress_mod.ProgressError) as e:
        progress_mod.last_boundary(unit)
    assert "no boundary" in str(e.value)
    text = progress_mod.table(tmp_path, now=now)
    assert "UNREADABLE" in text and "NONE RECORDED" not in text
    # and a genuinely absent log is still the OTHER state
    _unit(tmp_path, "silent")
    assert "NONE RECORDED" in progress_mod.table(tmp_path, now=now)


def test_a_unit_directory_that_cannot_be_examined_is_reported(tmp_path):
    """`fail-closed`.

    A unit directory whose permissions changed — by whatever killed the unit,
    by a half-torn-down sandbox — must not vanish from the scan with no signal
    at all: that is the exact failure this feature exists to catch, produced
    by the feature.
    And the mechanism matters, which is why this drives a real chmod rather
    than a stub: `Path.exists()` swallows the EACCES itself and answers False,
    so no `except OSError` around it can ever fire. `os.stat` is what lets the
    state through.
    """
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    broken = _unit(tmp_path, "locked")
    _unit(tmp_path, "ok", boundaries=[("pushed", "a" * 40, now)])
    broken.chmod(0o000)
    try:
        if progress_mod.checkout_state(broken) is not None:
            pytest.skip("this filesystem/user can stat through mode 000")
        assert broken in progress_mod.units(tmp_path), (
            "an unexaminable unit was dropped from the scan — silently, which "
            "is indistinguishable from it never having existed"
        )
        # The tri-state must reach the Unit, not die at the filter. `units()`
        # selects with `is not False`, so True and None merge there; without
        # `read_units` consulting `checkout_state` itself, this row renders
        # NONE RECORDED — a false claim that it IS a unit and has recorded
        # nothing, which is worse than dropping the row.
        reported = {u.name: u for u in progress_mod.read_units(tmp_path)}
        assert reported["locked"].error, (
            "the unexaminable unit reached the table with no error on it")
        text = progress_mod.table(tmp_path, now=now)
        assert "locked" in text and "UNREADABLE" in text
        assert "NONE RECORDED" not in text
        assert "! locked:" in text
        assert "pushed" in text, "one broken unit hid the unit beside it"
    finally:
        broken.chmod(0o755)


def test_an_unexaminable_git_entry_is_reported_even_when_the_log_reads(
        tmp_path):
    """`unmapped:tri-state-rendered-as-determined`, isolated.

    The chmod case above cannot tell the two mechanisms apart: a unit directory
    at mode 000 makes BOTH `checkout_state` and `last_boundary` fail, so the
    row is marked UNREADABLE either way and that test alone stays green with
    `read_units`' `checkout_state` branch deleted. A self-referential `.git`
    symlink separates them: the directory lists, the log reads perfectly, and
    only the checkout question is unanswerable. `units()` filters on
    `is not False`, merging True and None, so without this branch the row
    renders NONE RECORDED — a false claim that this IS a unit which has
    recorded nothing.
    """
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    weird = tmp_path / "loop"
    weird.mkdir()
    (weird / ".git").symlink_to(".git")          # ELOOP on stat, not ENOENT
    progress_mod.log_path(weird).parent.mkdir(parents=True, exist_ok=True)
    progress_mod.log_path(weird).write_text(json.dumps(
        {"boundary": "pushed", "head": "a" * 40,
         "ts": (now - timedelta(minutes=4)).isoformat(timespec="seconds")})
        + "\n")

    assert progress_mod.checkout_state(weird) is None
    assert progress_mod.last_boundary(weird)["boundary"] == "pushed"
    reported = {u.name: u for u in progress_mod.read_units(tmp_path)}
    assert "loop" in reported, "the unexaminable unit was dropped"
    assert reported["loop"].error, (
        "warden could not tell whether this is a unit and said nothing — the "
        "tri-state died at the filter in `units()`")
    text = progress_mod.table(tmp_path, now=now)
    assert "! loop:" in text and "could not be examined" in text


def test_an_oversized_log_is_refused_rather_than_read(tmp_path):
    """`progress show` exists to be cheap. A log that has become something else
    is refused by name rather than read into memory."""
    unit = _unit(tmp_path, "u")
    path = progress_mod.log_path(unit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * (progress_mod._MAX_LOG_BYTES + 1))
    with pytest.raises(progress_mod.ProgressError) as e:
        progress_mod.last_boundary(unit)
    assert "read bound" in str(e.value)


def test_record_refuses_a_boundary_outside_the_vocabulary(tmp_path):
    """A closed vocabulary, because the column is read ACROSS units: two units
    describing the same step two ways is what made the hand reconstruction
    expensive."""
    with pytest.raises(progress_mod.ProgressError) as e:
        progress_mod.record(tmp_path, "nearly-done")
    assert "unknown boundary" in str(e.value)
    for boundary in progress_mod.BOUNDARIES:
        assert progress_mod.record(tmp_path, boundary, head="a" * 40).is_file()
    entries = [json.loads(ln) for ln in
               progress_mod.log_path(tmp_path).read_text().splitlines()]
    assert [e["boundary"] for e in entries] == list(progress_mod.BOUNDARIES)
    assert all(e["head"] == "a" * 40 and e["ts"] for e in entries)


def test_record_quietly_never_changes_the_callers_verdict(tmp_path,
                                                              capsys):
    """`verify`, `round new` and `attest write` record a boundary as they
    finish, and none of them may fail because a status line could not be
    written. Never SILENT, though, and never on STDOUT: `round new`'s stdout is
    a command-substitution surface."""
    assert progress_mod.record_quietly(tmp_path, "not-a-boundary") is None
    out = capsys.readouterr()
    assert out.out == "", "a progress warning on stdout would corrupt " \
                         "`ROUND=\"$(warden round new ...)\"`"
    assert "NOT recorded" in out.err


def test_an_unparsable_timestamp_is_unknown_and_never_zero(tmp_path):
    assert progress_mod.age_seconds(None) is None
    assert progress_mod.age_seconds({"ts": "not a time"}) is None
    assert progress_mod.render_age(None) == "unknown"
    # a naive timestamp is read as UTC rather than crashing the table
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    naive = {"ts": "2026-09-10T19:00:00"}
    assert progress_mod.age_seconds(naive, now=now) == 3600


def test_render_age_distinguishes_every_scale_it_claims():
    assert progress_mod.render_age(5) == "5s"
    assert progress_mod.render_age(600) == "10m"
    assert progress_mod.render_age(3600 * 3) == "3h 00m"
    assert progress_mod.render_age(3600 * 50) == "2d 02h"
    # clock skew is named, never rendered as fresh
    assert progress_mod.render_age(-60) == "clock skew"


def test_a_worktree_pointer_file_is_a_checkout_as_much_as_a_git_dir(
        tmp_path):
    """`tests-bite`. BOTH shapes, because production has one of them and the
    fixture has the other.

    Every unit `progress show` exists to scan is a git WORKTREE under
    `.claude/worktrees/<unit>`, where `.git` is a pointer FILE, not a
    directory — this checkout's own `.git` is one. The `_unit` fixture is a
    real `git init` (a pointer at nowhere makes every unit's head `""`), so
    it covers only the directory shape. Without this test, narrowing
    `checkout_state` to `S_ISDIR` — which makes `progress show` report ZERO
    units in a real worktree root, total failure on its only deployment
    shape — would leave the whole suite green.

    The precedent is one module over: `tests/test_catalog_starters.py` pins
    both shapes for `advisor._scan_targets`.
    """
    clone = _unit(tmp_path, "clone")                       # .git is a DIRECTORY
    assert (clone / ".git").is_dir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {clone / '.git' / 'worktrees' / 'w'}\n")
    assert (worktree / ".git").is_file()

    assert progress_mod.checkout_state(worktree) is True, (
        "a worktree's `.git` pointer file is not being read as a checkout — "
        "`progress show` would report ZERO units in a real worktree root")
    assert worktree in progress_mod.units(tmp_path)
    text = progress_mod.table(tmp_path)
    assert {"worktree", "clone"} <= _unit_rows(text), (
        f"the pointer-file unit is scanned but not RENDERED as a row:\n{text}")


def _unit_rows(text: str) -> set[str]:
    """The `unit` column of the rendered table's DATA rows — the lines between
    the dashes and the blank line before the footer — and nothing else. The
    footer interpolates the root path, and `tmp_path`'s basename is derived
    from the test's name, so `"worktree" in text` is satisfied by
    `test_a_worktree_pointer_file_i0` whether or not a worktree row exists.
    """
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("---")) + 1
    rows: set[str] = set()
    for line in lines[start:]:
        if not line.strip():
            break
        if not line.startswith("("):          # "(no git checkout under …)"
            rows.add(line.split()[0])
    return rows


def test_the_table_assertion_reads_a_row_and_not_the_footer(tmp_path):
    """A root whose basename carries the unit name, holding NO such unit. The
    page contains the word; the rows do not. Under the S_ISDIR mutation of
    `checkout_state` a whole-page `in` assertion would be satisfied by
    exactly this footer."""
    root = tmp_path / "test_a_worktree_pointer_file_i0"
    root.mkdir()
    _unit(root, "clone")
    text = progress_mod.table(root)
    assert "worktree" in text, "the footer no longer interpolates the root"
    assert _unit_rows(text) == {"clone"}, text
    # ...and a root with no unit at all renders "(no git checkout under …)"
    # in the row slot, which is not a row either.
    empty = tmp_path / "empty-worktree-root"
    empty.mkdir()
    assert _unit_rows(progress_mod.table(empty)) == set()


def test_units_are_immediate_checkouts_and_never_a_recursive_walk(tmp_path):
    """Worktrees hold full second copies of the tree, and a recursive walk
    reports a tree of stale nested copies as live units — the failure
    `conftest.tracked` records from the other side."""
    _unit(tmp_path, "a")
    nested = _unit(tmp_path, "b")
    _unit(nested, ".claude")          # a nested worktree root, one level down
    (nested / ".claude" / "deep").mkdir()
    (nested / ".claude" / "deep" / ".git").write_text("gitdir: /nowhere\n")
    (tmp_path / "not-a-checkout").mkdir()

    found = [p.name for p in progress_mod.units(tmp_path)]
    assert sorted(found) == ["a", "b"]
    assert progress_mod.checkout_state(tmp_path / "not-a-checkout") is False


def test_the_root_itself_counts_when_it_is_the_only_checkout(tmp_path):
    """A builder working in the checkout directly is one unit, and a table with
    no rows would read as "nothing is happening"."""
    unit = _unit(tmp_path, "solo")
    assert [p.name for p in progress_mod.units(unit)] == ["solo"]
    text = progress_mod.table(unit)
    assert "solo" in text and "1 unit(s)" in text


def test_an_empty_root_says_so_rather_than_rendering_a_blank_table(
        tmp_path):
    text = progress_mod.table(tmp_path)
    assert "no git checkout under" in text and "0 unit(s)" in text


def test_a_unit_filter_narrows_the_table(tmp_path):
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    _unit(tmp_path, "a", boundaries=[("pushed", "a" * 40, now)])
    _unit(tmp_path, "b", boundaries=[("pushed", "b" * 40, now)])
    text = progress_mod.table(tmp_path, now=now, names=("a",))
    assert "1 unit(s)" in text and "  b  " not in text


def test_the_cli_renders_a_table_and_records_a_boundary(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    """Driven through the CLI, because that is the only interface the
    orchestrator has."""
    from warden import cli
    from conftest import REPO_YAML

    root = tmp_path / "repo"
    root.mkdir()
    (root / "repo.yaml").write_text(REPO_YAML)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    monkeypatch.chdir(root)

    assert cli.main(["progress", "record", "repair-committed", "--head",
                     "a" * 40, "--detail", "round 2"]) == 0
    assert cli.main(["progress", "show", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "repair-committed" in out
    assert cli.main(["progress", "show", "--root",
                     str(tmp_path / "missing")]) == 2


def test_the_ignore_claim_and_the_rung_that_would_enforce_it_move_together(
        tmp_path):
    """`enforcement-truth`: the docs name the rung that enforces the log's
    ignore status.

    The log lives under `.warden/out/`, and THIS repo and the shipped example
    gitignore that root. Every other writer under that root mints a
    uniquely-named run dir, while this one appends to a FIXED path on every
    branch, the conflict shape R-08 exists for. Rung R-13 refuses an enrolled
    repo that TRACKS it, the way R-08 / R-10 / R-11 refuse the three sibling
    paths, so the three surfaces describing the log's ignore status must name
    the rung that enforces it, and none may hedge that no rung exists.
    """
    from warden import certify as certify_mod

    assert progress_mod.log_path(tmp_path).parent.name == "out"
    repo = Path(__file__).resolve().parent.parent
    assert ".warden/out/" in (repo / ".gitignore").read_text()
    rungs = {c["id"]: c for lv in certify_mod.load(repo)["levels"].values()
             for c in lv["checks"]}
    assert rungs.get("R-13", {}).get("type") == "not_tracked", rungs.get("R-13")
    assert rungs["R-13"]["path"] == ".warden/out/", rungs["R-13"]
    surfaces = {
        "warden/progress.py": (repo / "warden" / "progress.py").read_text(),
        "warden/cli.py": (repo / "warden" / "cli.py").read_text(),
        "docs/wiki/CLI-Reference.md":
            (repo / "docs" / "wiki" / "CLI-Reference.md").read_text(),
    }
    for name, text in surfaces.items():
        flat = " ".join(text.split())
        assert "R-13" in flat, (
            f"{name} describes the boundary log's ignore status without "
            "naming R-13, the rung that makes it a platform guarantee")
        for hedge in ("no certification rung", "not a platform guarantee",
                      "ignored by config, not by a rung"):
            assert hedge not in flat, (
                f"{name} still says {hedge!r} about a root R-13 enforces")
