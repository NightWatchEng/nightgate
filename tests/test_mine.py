"""PR corpus miner — read every merged PR, not just the attested ones.

The retro sees only PRs that produced an attestation shard. Everything else
is invisible: human hotfixes, PRs merged before enrollment, anything that
skipped the chain. This mines the rest.

The contract that matters most is the honest one: a signal class the miner
could not read is NAMED as unavailable, never reported as a zero. A zero
means "looked, found nothing"; unavailable means "could not look". Conflating
them is how a corpus quietly asserts a clean history it never examined.
"""

import json
import subprocess
from pathlib import Path

import pytest

from conftest import finishes_within
from warden import cli
from warden import mine as mine_mod


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def write(root: Path, name: str, body: str) -> None:
    (root / name).write_text(body)


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.test")
    git(root, "config", "user.name", "t")
    write(root, "seed.txt", "seed\n")
    commit(root, "feat(x): seed the repo (proj-1)")
    return root


def merge_branch(root: Path, branch: str, pr: int, changes: dict[str, str],
                 subject: str) -> str:
    """A feature branch merged with a real merge commit, GitHub's wording."""
    git(root, "checkout", "-q", "-b", branch)
    for name, body in changes.items():
        write(root, name, body)
    commit(root, subject)
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", branch, "-m",
        f"Merge pull request #{pr} from org/{branch}")
    return git(root, "rev-parse", "HEAD")


# --------------------------------------------------------------------------
# git-side signals — no network, real history
# --------------------------------------------------------------------------


def test_reverts_are_found_and_name_the_commit_they_reverted(repo):
    merge_branch(repo, "feat/a", 1, {"a.py": "x = 1\n"}, "feat(a): add a (proj-2)")
    bad = commit_change(repo, "b.py", "y = 2\n", "feat(b): add b (proj-3)")
    git(repo, "revert", "--no-edit", bad)

    result = mine_mod.mine(repo, gh=None)
    reverts = [s for s in result.signals if s.kind == "revert"]
    assert len(reverts) == 1
    assert bad[:8] in reverts[0].detail["reverted"]
    assert reverts[0].link


def commit_change(root: Path, name: str, body: str, message: str) -> str:
    write(root, name, body)
    return commit(root, message)


def test_fix_after_merge_names_the_merged_pr_whose_lines_it_repaired(repo):
    merge_branch(repo, "feat/parser", 7,
                 {"parser.py": "def parse(s):\n    return s.split(',')\n"},
                 "feat(parser): naive split (proj-4)")
    commit_change(repo, "parser.py",
                  "def parse(s):\n    return [p.strip() for p in s.split(',')]\n",
                  "fix(parser): strip whitespace (proj-5)")

    result = mine_mod.mine(repo, gh=None)
    fixes = [s for s in result.signals if s.kind == "fix-after-merge"]
    assert len(fixes) == 1
    assert fixes[0].detail["pr"] == 7
    assert "parser.py" in fixes[0].detail["files"]


def test_a_fix_that_only_adds_lines_is_not_reported_as_repairing_a_pr(repo):
    """Nothing was replaced, so nothing was wrong — reporting this would put
    a false row in a corpus whose whole value is that rows are checkable."""
    merge_branch(repo, "feat/parser", 7, {"parser.py": "a = 1\n"},
                 "feat(parser): add (proj-4)")
    commit_change(repo, "parser.py", "a = 1\nb = 2\n",
                  "fix(parser): also handle b (proj-5)")

    result = mine_mod.mine(repo, gh=None)
    assert [s for s in result.signals if s.kind == "fix-after-merge"] == []


def test_a_fix_repairing_unmerged_work_is_not_attributed_to_a_pr(repo):
    """The signal is 'a merged PR shipped a defect', not 'someone edited a
    line'. Without a merge commit there is no PR to attribute it to."""
    commit_change(repo, "d.py", "v = 1\n", "feat(d): direct to main (proj-6)")
    commit_change(repo, "d.py", "v = 2\n", "fix(d): wrong value (proj-7)")

    result = mine_mod.mine(repo, gh=None)
    fixes = [s for s in result.signals if s.kind == "fix-after-merge"]
    assert all(f.detail.get("pr") is None for f in fixes)


def test_every_signal_row_links_back_to_a_real_commit_or_pr(repo):
    """Any row can be checked back to its PR."""
    merge_branch(repo, "feat/p", 3, {"p.py": "n = 1\n"}, "feat(p): add (proj-8)")
    commit_change(repo, "p.py", "n = 2\n", "fix(p): off by one (proj-9)")
    bad = commit_change(repo, "q.py", "q = 1\n", "feat(q): add q (proj-10)")
    git(repo, "revert", "--no-edit", bad)

    result = mine_mod.mine(repo, gh=None)
    assert result.signals
    for signal in result.signals:
        assert signal.link, f"{signal.kind} row has no checkable link"
        assert signal.ref


def test_merge_style_is_reported_and_squash_names_its_lost_signal(repo, tmp_path):
    merged = mine_mod.mine(repo, gh=None)
    merge_branch(repo, "feat/m", 4, {"m.py": "m = 1\n"}, "feat(m): add (proj-11)")
    assert mine_mod.mine(repo, gh=None).merge_style == "merge-commits"

    # A history with no merge commits at all reads as squash/rebase, and the
    # limitation has to be stated rather than left as a quiet zero.
    assert merged.merge_style == "squash-or-rebase"
    assert any("squash" in limit for limit in merged.limits)


def test_since_window_excludes_older_commits(repo):
    merge_branch(repo, "feat/old", 1, {"o.py": "o = 1\n"}, "feat(o): add (proj-12)")
    commit_change(repo, "o.py", "o = 2\n", "fix(o): wrong (proj-13)")

    assert mine_mod.mine(repo, gh=None, since="2099-01-01").signals == ()
    assert mine_mod.mine(repo, gh=None, since="2000-01-01").signals


# --------------------------------------------------------------------------
# path parsing, attribution, partial reads and the --since window
# --------------------------------------------------------------------------


def test_a_quoted_path_is_never_attributed_to_the_previous_file(repo):
    """git C-quotes non-ASCII paths (`core.quotepath` defaults on), so
    `--- "a/caf\\303\\251.py"` matches no `--- a/` prefix; a parser that keeps
    the PREVIOUS file's name blames one file's hunks against another, naming
    an innocent commit as the introducer."""
    merge_branch(repo, "feat/a", 1, {"a.py": "innocent = 1\n"},
                 "feat(a): innocent lines (proj-20)")
    merge_branch(repo, "feat/u", 2, {"café.py": "guilty = 1\n"},
                 "feat(u): the real culprit (proj-21)")
    write(repo, "a.py", "innocent = 1\n")
    write(repo, "café.py", "guilty = 2\n")
    commit(repo, "fix(u): repair the unicode file (proj-22)")

    result = mine_mod.mine(repo, gh=None)
    fixes = [s for s in result.signals if s.kind == "fix-after-merge"]
    assert len(fixes) == 1
    assert fixes[0].detail["files"] == ["café.py"]
    assert fixes[0].detail["pr"] == 2, "attributed to the innocent PR"


def test_a_glob_character_in_a_path_does_not_pull_in_another_file(repo):
    """A git pathspec is a wildmatch PATTERN, so a per-file `diff -- a?.py`
    also matches ab.py and attributes its hunks to a?.py — the same
    misattribution through a second door, blaming a commit that never touched
    the repaired lines."""
    merge_branch(repo, "feat/g", 31,
                 {"a?.py": "one\ntwo\nthree\nfour\nfive\n",
                  "ab.py": "alpha\nbeta\ngamma\n"},
                 "feat(g): add both (proj-40)")
    # BOTH files must change in the fix, or `a?.py` as a glob has nothing of
    # ab.py's to steal and the leak is invisible: the test would pass with
    # the fix removed.
    write(repo, "a?.py", "one\ntwo\nthree\nfour\nFIVE\n")
    write(repo, "ab.py", "ALPHA\nBETA\nGAMMA\n")
    commit(repo, "fix(g): repair both (proj-41)")

    spans = mine_mod._replaced_line_ranges(repo, "HEAD", "a?.py")
    assert spans == [(5, 1)], f"ab.py's hunks leaked into a?.py: {spans}"
    assert mine_mod._replaced_line_ranges(repo, "HEAD", "ab.py") == [(1, 3)]

    result = mine_mod.mine(repo, gh=None)
    fixes = [s for s in result.signals if s.kind == "fix-after-merge"]
    assert len(fixes) == 1
    assert fixes[0].detail["files"] == ["a?.py", "ab.py"]


def test_a_path_with_a_space_is_read_not_dropped(repo):
    """git appends a TAB after a spaced path, so slicing `--- a/` off alone
    produces 'my file.py\\t' and every blame on it fails."""
    merge_branch(repo, "feat/s", 4, {"my file.py": "v = 1\n"},
                 "feat(s): spaced path (proj-23)")
    write(repo, "my file.py", "v = 2\n")
    commit(repo, "fix(s): wrong value (proj-24)")

    result = mine_mod.mine(repo, gh=None)
    fixes = [s for s in result.signals if s.kind == "fix-after-merge"]
    assert len(fixes) == 1
    assert fixes[0].detail["files"] == ["my file.py"]
    assert fixes[0].detail["pr"] == 4


def test_a_fix_whose_diff_cannot_be_read_is_named_partial(repo, monkeypatch):
    """The git classes are declared ones whose zero is a real zero, so an
    extraction failure must not be swallowed with a bare `continue`. A dropped
    commit is named."""
    merge_branch(repo, "feat/d", 9, {"d.py": "v = 1\n"}, "feat(d): add (proj-25)")
    fix = commit_change(repo, "d.py", "v = 2\n", "fix(d): repair it (proj-26)")

    real = mine_mod._git

    def diff_fails(root, *args):
        if args and args[0] == "diff" and "--name-only" in args:
            raise mine_mod.MineError("fatal: bad object")
        return real(root, *args)

    monkeypatch.setattr(mine_mod, "_git", diff_fails)
    result = mine_mod.mine(repo, gh=None)

    assert [s for s in result.signals if s.kind == "fix-after-merge"] == []
    assert result.counts()["fix-after-merge"] == 0
    dropped = [p for p in result.partial if p.ref == fix]
    assert dropped, "a zero was reported with no record of what was lost"
    assert "cannot read the diff" in dropped[0].reason
    assert dropped[0].to_dict()["ref"] == fix


def test_an_unblamable_fix_is_reported_partial_not_silently_dropped(repo,
                                                                    monkeypatch):
    """The reachable path: when blame cannot answer, the commit is named
    in `partial` rather than vanishing behind a clean-looking zero."""
    merge_branch(repo, "feat/b", 8, {"b.py": "v = 1\n"}, "feat(b): add (proj-27)")
    commit_change(repo, "b.py", "v = 2\n", "fix(b): repair (proj-28)")

    real = mine_mod._git

    def blame_fails(root, *args):
        if args and args[0] == "blame":
            raise mine_mod.MineError("fatal: no such path")
        return real(root, *args)

    monkeypatch.setattr(mine_mod, "_git", blame_fails)
    result = mine_mod.mine(repo, gh=None)
    assert [s for s in result.signals if s.kind == "fix-after-merge"] == []
    assert result.partial, "a dropped commit left no trace"
    assert any(p.signal_class == "fix-after-merge" for p in result.partial)
    assert "partial" in mine_mod.render(result).lower()


def test_one_failed_blame_inside_an_otherwise_attributed_commit_is_named(repo,
                                                                         monkeypatch):
    """Distinct from the all-blames-fail case: when SOME spans attribute,
    `introduced` is non-empty and the not-attributed fallback never fires, so
    only the per-blame record can carry the loss. The all-fail test cannot see
    this branch deleted; this one goes red."""
    merge_branch(repo, "feat/p", 15, {"one.py": "a = 1\n", "two.py": "b = 1\n"},
                 "feat(p): add both (proj-34)")
    write(repo, "one.py", "a = 2\n")
    write(repo, "two.py", "b = 2\n")
    fix = commit(repo, "fix(p): repair both (proj-35)")

    real = mine_mod._git

    def blame_two_fails(root, *args):
        if args and args[0] == "blame" and args[-1] == "two.py":
            raise mine_mod.MineError("fatal: cannot blame")
        return real(root, *args)

    monkeypatch.setattr(mine_mod, "_git", blame_two_fails)
    result = mine_mod.mine(repo, gh=None)

    fixes = [s for s in result.signals if s.kind == "fix-after-merge"]
    assert len(fixes) == 1, "one.py still attributes, so a row is emitted"
    assert "two.py" not in fixes[0].detail["files_attributed"]
    dropped = [p for p in result.partial
               if p.ref == fix and "two.py" in p.reason]
    assert dropped, "the failed blame vanished behind the successful one"


def test_the_blame_budget_marks_the_row_truncated_and_partial(repo, monkeypatch):
    """A `break` that exits only the inner span loop skips the remaining files
    while the row still lists them all as repaired."""
    monkeypatch.setattr(mine_mod, "_MAX_BLAME_LINES", 1)
    merge_branch(repo, "feat/big", 11, {"big.py": "\n".join(f"l{i}" for i in range(6)) + "\n"},
                 "feat(big): add big (proj-29)")
    merge_branch(repo, "feat/small", 22, {"small.py": "s = 1\n"},
                 "feat(small): add small (proj-30)")
    write(repo, "big.py", "\n".join(f"L{i}" for i in range(6)) + "\n")
    write(repo, "small.py", "s = 2\n")
    commit(repo, "fix(all): repair everything (proj-31)")

    result = mine_mod.mine(repo, gh=None)
    fixes = [s for s in result.signals if s.kind == "fix-after-merge"]
    assert len(fixes) == 1
    assert fixes[0].detail["truncated"] is True
    # The row must not NAME files it never blamed. `files` is what the
    # commit touched; `files_attributed` is what was actually read, and the
    # two must diverge here or the row is overclaiming again.
    assert fixes[0].detail["files"] == ["big.py", "small.py"]
    assert fixes[0].detail["files_attributed"] == ["big.py"]
    assert "small.py" not in fixes[0].summary
    assert any("budget" in p.reason for p in result.partial)


def test_since_bounds_the_github_classes_too(repo):
    """`--since` must reach the GitHub side too, or those classes are counted
    over all time while the artifact stamps a window beside them."""
    old = dict(PR, number=99, merged_at="2019-03-01T00:00:00Z")
    result = mine_mod.mine(repo, gh=fake_gh(pulls=[old]), repo_slug="org/repo",
                           since="2026-01-01")
    assert result.prs_read == 0
    assert result.counts()["ci-failure"] == 0
    assert result.counts()["review-comment-precedes-change"] == 0


def test_a_relative_since_says_it_could_not_bound_the_github_classes(repo):
    """git understands '2 weeks ago' and this does not. Rather than guess or
    silently skip the filter, it says which half of the report is unbounded."""
    result = mine_mod.mine(repo, gh=fake_gh(pulls=[PR]), repo_slug="org/repo",
                           since="2 weeks ago")
    assert result.prs_read == 1
    assert any("over all time" in limit for limit in result.limits)


def test_a_naive_merged_at_does_not_crash_the_whole_run(repo):
    """`_merged_at` must not return a NAIVE datetime for a merged_at with no
    offset: `naive >= after` raises TypeError — not a MineError, so it would
    escape `mine()` and discard every git-side signal already collected."""
    merge_branch(repo, "feat/n", 40, {"n.py": "v = 1\n"}, "feat(n): add (proj-42)")
    commit_change(repo, "n.py", "v = 2\n", "fix(n): repair (proj-43)")
    naive = dict(PR, number=41, merged_at="2026-08-20T10:00:00")

    result = mine_mod.mine(repo, gh=fake_gh(pulls=[naive]),
                           repo_slug="org/repo", since="2026-01-01")
    assert result.prs_read == 1
    assert result.counts()["fix-after-merge"] == 1, "git-side signals survived"


def test_an_unreadable_merge_time_is_counted_but_declared(repo):
    """Keeping it is the direction that does not silently shrink the corpus —
    but a reader has to be told the window did not decide it."""
    broken = dict(PR, number=42, merged_at="not-a-date")
    result = mine_mod.mine(repo, gh=fake_gh(pulls=[broken]),
                           repo_slug="org/repo", since="2026-01-01")
    assert result.prs_read == 1
    assert any("could not read" in limit and "--since" in limit
               for limit in result.limits)


def test_the_pr_page_walk_is_bounded_and_says_so(repo, monkeypatch):
    """A narrow --since filters client-side, so the walk cannot stop early;
    unbounded, it pages the ENTIRE closed-PR list at a 20s timeout per request,
    with nothing in the artifact saying it happened."""
    monkeypatch.setattr(mine_mod, "_MAX_PAGES", 2)
    pages = {"n": 0}
    # A FINITE page supply on purpose: with the bound removed the walk must
    # fail this test, not hang it. (It does hang against an endless supply —
    # every page is filtered out by the window, so `len(pulls)` never reaches
    # _MAX_PRS and the `len(batch) < 100` exit never fires.)
    supply = 5

    def gh(path: str):
        if path.startswith("repos/org/repo/pulls?"):
            pages["n"] += 1
            if pages["n"] > supply:
                return []
            return [dict(PR, number=1000 + pages["n"] * 100 + i,
                         head={"sha": f"{i:040d}"},
                         merged_at="2019-01-01T00:00:00Z")
                    for i in range(100)]
        return []

    result = mine_mod.mine(repo, gh=gh, repo_slug="org/repo",
                           since="2026-01-01")
    assert pages["n"] == 2, f"walked {pages['n']} pages past its bound of 2"
    assert any("stopped at its bound" in limit for limit in result.limits)


def test_an_iso_since_bounds_both_sides_without_a_caveat(repo):
    result = mine_mod.mine(repo, gh=fake_gh(pulls=[PR]), repo_slug="org/repo",
                           since="2026-01-01")
    assert not any("over all time" in limit for limit in result.limits)


@pytest.mark.parametrize("subject,is_fix", [
    ("fix(parser): strip whitespace (p-1)", True),
    ("hotfix(api): urgent (p-2)", True),
    ("fix: bare subject (p-3)", True),
    ("feat(ui): fixed-width columns (p-4)", False),
    ("feat: fixed-point math (p-5)", False),
    ("feat: fix-me later (p-6)", False),
    ("docs: document the fixture (p-7)", False),
    ("feat: prefix handling (p-8)", False),
])
def test_fix_subject_does_not_fire_on_hyphenated_adjectives(subject, is_fix):
    """'-' is a non-word char, so a plain \\b closes on it and would read
    'fixed-width' as a repair."""
    assert bool(mine_mod._FIX_SUBJECT.search(subject)) is is_fix


# --------------------------------------------------------------------------
# the reader seam — driven directly, urlopen replaced
# --------------------------------------------------------------------------


def test_default_reader_is_none_without_a_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert mine_mod.default_reader() is None


def test_default_reader_translates_a_github_error_into_a_mine_error(monkeypatch):
    """The reader closure itself is driven here; a test that replaces the seam
    cannot cover it."""
    from warden import github as github_mod
    monkeypatch.setenv("GITHUB_TOKEN", "x")

    def boom(path, timeout=None):
        raise github_mod.GitHubError("HTTP 404: nope")

    monkeypatch.setattr(github_mod, "_api", boom)
    reader = mine_mod.default_reader()
    assert reader is not None
    with pytest.raises(mine_mod.MineError, match="404"):
        reader("repos/o/r/pulls")


def test_default_reader_converts_a_non_github_exception_too(monkeypatch):
    """A read-phase ConnectionResetError is not a GitHubError. Letting it
    escape would discard every git-side signal already collected."""
    from warden import github as github_mod
    monkeypatch.setenv("GITHUB_TOKEN", "x")

    def boom(path, timeout=None):
        raise ConnectionResetError("peer hung up")

    monkeypatch.setattr(github_mod, "_api", boom)
    with pytest.raises(mine_mod.MineError, match="ConnectionResetError"):
        mine_mod.default_reader()("repos/o/r/pulls")


def test_default_reader_passes_a_timeout(monkeypatch):
    """Without a timeout on `_api`, a stalled socket wedges the command and the
    UNREAD row it exists to write is never reached."""
    from warden import github as github_mod
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    seen = {}

    def record(path, timeout=None):
        seen["timeout"] = timeout
        return []

    monkeypatch.setattr(github_mod, "_api", record)
    mine_mod.default_reader()("repos/o/r/pulls")
    assert seen["timeout"] == mine_mod._HTTP_TIMEOUT
    assert seen["timeout"] > 0


def test_a_reader_error_mid_sweep_marks_only_that_class_unread(repo):
    """Drives the per-class `except MineError`; raising inside `_merged_pulls`
    reaches a different branch."""
    def gh(path: str):
        if path.startswith("repos/org/repo/pulls?"):
            return [PR]
        if "/check-runs" in path:
            raise mine_mod.MineError("HTTP 502")
        return []

    result = mine_mod.mine(repo, gh=gh, repo_slug="org/repo")
    unread = {u.signal_class for u in result.unavailable}
    assert "ci-failure" in unread
    assert "review-comment-precedes-change" not in unread
    assert result.counts()["review-comment-precedes-change"] == 0
    assert "ci-failure" not in result.counts()


def test_the_pr_list_cap_is_stated_when_it_bites(repo, monkeypatch):
    """The cap is a bound on completeness, and a bound nobody is told about
    reads as 'we saw everything'."""
    monkeypatch.setattr(mine_mod, "_MAX_PRS", 2)
    pulls = [dict(PR, number=n, head={"sha": f"{n:040d}"}) for n in range(1, 6)]
    result = mine_mod.mine(repo, gh=fake_gh(pulls=pulls), repo_slug="org/repo")
    assert result.prs_read == 2
    assert any("capped at 2" in limit for limit in result.limits)


def test_a_per_file_diff_failure_is_named_partial(repo, monkeypatch):
    """The second drop site: one unreadable file inside an otherwise
    readable commit must not vanish behind the commit's other files."""
    merge_branch(repo, "feat/two", 13, {"ok.py": "a = 1\n", "bad.py": "b = 1\n"},
                 "feat(two): add both (proj-32)")
    write(repo, "ok.py", "a = 2\n")
    write(repo, "bad.py", "b = 2\n")
    fix = commit(repo, "fix(two): repair both (proj-33)")

    real = mine_mod._git

    def diff_one_fails(root, *args):
        if args and args[0] == "diff" and args[-1] == "bad.py":
            raise mine_mod.MineError("fatal: unreadable")
        return real(root, *args)

    monkeypatch.setattr(mine_mod, "_git", diff_one_fails)
    result = mine_mod.mine(repo, gh=None)
    dropped = [p for p in result.partial
               if p.ref == fix and "bad.py" in p.reason]
    assert dropped, "an unreadable file left no trace on an otherwise good row"


def test_a_long_partial_list_says_how_many_it_did_not_print(repo, monkeypatch):
    many = tuple(mine_mod.Partial("fix-after-merge", f"{i:040d}", "nope")
                 for i in range(14))
    result = mine_mod.MineResult("a" * 40, None, None, "merge-commits", (), (),
                                 (), partial=many)
    text = mine_mod.render(result)
    assert "and 4 more" in text


def test_no_github_remote_names_the_classes_unread(repo):
    """With no origin remote, every GitHub class is named unread."""
    result = mine_mod.mine(repo, gh=fake_gh(pulls=[PR]), repo_slug=None)
    reasons = {u.signal_class: u.reason for u in result.unavailable}
    assert set(mine_mod.GH_CLASSES) <= set(reasons)
    assert any("origin" in r for r in reasons.values())


# --------------------------------------------------------------------------
# the honesty contract — unavailable is not zero
# --------------------------------------------------------------------------


def test_github_classes_are_named_unavailable_when_there_is_no_reader(repo):
    """Unavailable signal classes are named explicitly rather than silently
    reported as zero."""
    result = mine_mod.mine(repo, gh=None)
    named = {u.signal_class for u in result.unavailable}
    assert set(mine_mod.GH_CLASSES) <= named
    for entry in result.unavailable:
        assert entry.reason.strip(), f"{entry.signal_class} unavailable with no reason"


def test_an_unavailable_class_is_not_counted_as_zero(repo):
    """The distinction the corpus lives on: a zero means 'looked, found
    nothing'; unavailable means 'could not look'."""
    result = mine_mod.mine(repo, gh=None)
    counts = result.counts()
    for cls in mine_mod.GH_CLASSES:
        assert counts.get(cls) != 0, (
            f"{cls} reported as a zero count while also unavailable — a reader "
            "cannot tell 'no signal' from 'never read'")
        assert cls not in counts


def test_git_classes_report_a_real_zero_when_history_is_clean(repo):
    result = mine_mod.mine(repo, gh=None)
    assert result.counts()["revert"] == 0
    assert result.counts()["fix-after-merge"] == 0
    assert {u.signal_class for u in result.unavailable} & set(mine_mod.GIT_CLASSES) == set()


def test_a_gh_reader_that_errors_marks_the_class_unavailable_not_empty(repo):
    def boom(path):
        raise mine_mod.MineError("HTTP 403: rate limited")

    result = mine_mod.mine(repo, gh=boom, repo_slug="org/repo")
    reasons = {u.signal_class: u.reason for u in result.unavailable}
    assert set(mine_mod.GH_CLASSES) <= set(reasons)
    assert any("403" in r for r in reasons.values())


# --------------------------------------------------------------------------
# github-side signals — injected reader, no network
# --------------------------------------------------------------------------


def fake_gh(pulls=None, comments=None, checks=None):
    pulls = pulls if pulls is not None else []
    comments = comments or {}
    checks = checks or {}

    def reader(path: str):
        if path.startswith("repos/org/repo/pulls?"):
            return pulls
        if "/comments" in path:
            number = int(path.split("/pulls/")[1].split("/")[0])
            return comments.get(number, [])
        if "/check-runs" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            return {"check_runs": checks.get(sha, [])}
        return []
    return reader


PR = {"number": 12, "merged_at": "2026-08-20T10:00:00Z",
      "merge_commit_sha": "a" * 40, "head": {"sha": "b" * 40},
      "title": "feat: thing", "html_url": "https://github.com/org/repo/pull/12",
      "merged_by": {"login": "some-bot"}}


def test_review_comment_that_preceded_a_change_becomes_a_signal(repo):
    gh = fake_gh(pulls=[PR], comments={12: [
        {"path": "svc.py", "body": "this fails when the list is empty",
         "created_at": "2026-08-20T09:00:00Z", "user": {"login": "reviewer"},
         "html_url": "https://github.com/org/repo/pull/12#discussion_r1"},
    ]})
    result = mine_mod.mine(repo, gh=gh, repo_slug="org/repo")
    hits = [s for s in result.signals if s.kind == "review-comment-precedes-change"]
    assert len(hits) == 1
    assert hits[0].detail["file"] == "svc.py"
    assert hits[0].link.endswith("discussion_r1")


def test_ci_failures_are_grouped_by_check_name(repo):
    gh = fake_gh(pulls=[PR], checks={"b" * 40: [
        {"name": "warden gate", "conclusion": "failure",
         "html_url": "https://github.com/org/repo/runs/1"},
        {"name": "warden tests", "conclusion": "success", "html_url": "x"},
    ]})
    result = mine_mod.mine(repo, gh=gh, repo_slug="org/repo")
    fails = [s for s in result.signals if s.kind == "ci-failure"]
    assert len(fails) == 1
    assert fails[0].detail["check"] == "warden gate"


def test_a_successful_check_is_not_a_signal(repo):
    gh = fake_gh(pulls=[PR], checks={"b" * 40: [
        {"name": "warden tests", "conclusion": "success", "html_url": "x"}]})
    result = mine_mod.mine(repo, gh=gh, repo_slug="org/repo")
    assert [s for s in result.signals if s.kind == "ci-failure"] == []


# --------------------------------------------------------------------------
# merge-actor drift — the declared org vs the real one
# --------------------------------------------------------------------------


GRAPH = """version: 1
nodes:
  builder: {kind: skill, authority: find}
  gate: {kind: deterministic, authority: gate}
  founder: {kind: human, authority: merge%s}
edges:
  - {from: builder, to: gate, payload: diff}
"""


def seed_graph(root: Path, github: str = "") -> None:
    (root / "graph.yaml").write_text(GRAPH % github)


def test_merge_actor_drift_when_the_merger_is_not_a_declared_login(repo):
    seed_graph(repo, ", github: [the-founder]")
    result = mine_mod.mine(repo, gh=fake_gh(pulls=[PR]), repo_slug="org/repo")
    drift = [s for s in result.signals if s.kind == "merge-actor"]
    assert len(drift) == 1
    assert drift[0].detail["actor"] == "some-bot"
    assert drift[0].detail["declared_node"] == "founder"


def test_no_drift_when_the_merger_matches_the_declared_login(repo):
    seed_graph(repo, ", github: [some-bot]")
    result = mine_mod.mine(repo, gh=fake_gh(pulls=[PR]), repo_slug="org/repo")
    assert [s for s in result.signals if s.kind == "merge-actor"] == []


def test_an_unbindable_graph_says_so_instead_of_reporting_no_drift(repo):
    """With no github: mapping, nothing binds a real
    merge event to the declared merge node. Reporting 'no drift' there would
    be a green tick for a check that never ran."""
    seed_graph(repo)  # merge node declares no github logins
    result = mine_mod.mine(repo, gh=fake_gh(pulls=[PR]), repo_slug="org/repo")
    reasons = {u.signal_class: u.reason for u in result.unavailable}
    assert "merge-actor" in reasons
    assert "github" in reasons["merge-actor"]
    assert [s for s in result.signals if s.kind == "merge-actor"] == []


def test_no_graph_at_all_marks_merge_actor_unavailable(repo):
    result = mine_mod.mine(repo, gh=fake_gh(pulls=[PR]), repo_slug="org/repo")
    assert "merge-actor" in {u.signal_class for u in result.unavailable}


# --------------------------------------------------------------------------
# artifact + CLI
# --------------------------------------------------------------------------


def test_per_pr_detail_is_capped_and_the_cap_is_stated_not_silent(repo):
    """Comments and check-runs cost one request per PR, so a 91-PR repo makes
    ~182 calls and can time out — reporting ci-failure as unread. Capping is
    fine; capping SILENTLY would read as 'we looked at everything'."""
    pulls = [dict(PR, number=n, head={"sha": f"{n:040d}"}) for n in range(1, 8)]
    result = mine_mod.mine(repo, gh=fake_gh(pulls=pulls), repo_slug="org/repo",
                           pr_limit=3)
    assert result.prs_read == 7
    assert result.prs_detailed == 3
    assert any("3 " in limit and "detail" in limit for limit in result.limits)


def test_no_cap_line_when_every_pr_was_examined(repo):
    result = mine_mod.mine(repo, gh=fake_gh(pulls=[PR]), repo_slug="org/repo",
                           pr_limit=10)
    assert result.prs_detailed == 1
    assert not any("detail" in limit for limit in result.limits)


def test_a_gh_zero_carries_the_denominator_it_was_counted_over(repo):
    """A count of 0 ci-failures over 40 PRs and over 0 PRs are different
    facts. Without the denominator a reader cannot tell a clean history from
    a read that returned nothing — the same conflation `unavailable` exists
    to prevent, one level down."""
    result = mine_mod.mine(repo, gh=fake_gh(pulls=[PR]), repo_slug="org/repo")
    assert result.prs_read == 1
    assert result.to_dict()["prs_read"] == 1
    assert "1 merged PR" in mine_mod.render(result)


def test_prs_read_is_none_not_zero_when_github_was_never_read(repo):
    result = mine_mod.mine(repo, gh=None)
    assert result.prs_read is None
    assert result.to_dict()["prs_read"] is None
    assert "merged PR" not in mine_mod.render(result)


def test_result_serializes_with_counts_unavailable_and_limits(repo):
    merge_branch(repo, "feat/s", 5, {"s.py": "s = 1\n"}, "feat(s): add (proj-14)")
    doc = mine_mod.mine(repo, gh=None).to_dict()
    for key in ("schema", "head", "merge_style", "signals", "counts",
                "unavailable", "limits", "generated"):
        assert key in doc, f"artifact missing {key}"
    assert isinstance(doc["unavailable"], list)


def test_cli_mine_writes_an_artifact_and_exits_0(repo, monkeypatch, capsys):
    merge_branch(repo, "feat/c", 6, {"c.py": "c = 1\n"}, "feat(c): add (proj-15)")
    commit_change(repo, "c.py", "c = 2\n", "fix(c): wrong (proj-16)")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    assert cli.main(["mine"]) == 0
    out = capsys.readouterr().out
    assert "fix-after-merge" in out

    artifacts = sorted((repo / ".warden" / "out").glob("*-mine/pr-corpus.json"))
    assert len(artifacts) == 1
    doc = json.loads(artifacts[0].read_text())
    assert doc["counts"]["fix-after-merge"] == 1


def test_cli_mine_states_what_it_could_not_read(repo, monkeypatch, capsys):
    """Terminal output must carry the same honesty as the artifact — a reader
    who never opens the JSON still learns which classes went unread."""
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert cli.main(["mine"]) == 0
    out = capsys.readouterr().out
    assert "not read" in out.lower()
    assert "is not a zero" in out
    for cls in mine_mod.GH_CLASSES:
        assert cls in out


# --- enrollment state in the run manifest ------------------------------------
#
# The miner deliberately does not require repo.yaml, so it has to say
# something about rules_version for a repo that has none. "unenrolled" is a
# CLAIM about the repo. Stamped by a bare `except Exception`, any crash inside
# the version computation — an unparseable repo.yaml, an unreadable ruleset —
# would be reported as an absence of enrollment: "cannot compute" collapsed
# into "absent" inside an evidence artifact.

_FIXTURE_REPO_YAML = """\
version: 1
repo: fixture
components:
  src:
    path: ./
    lang: python
    description: fixture source
risk_tiers:
  - glob: "**"
    tier: LOW
    reason: fixture
verify:
  tests:
    - run: "true"
review:
  rules_dir: .warden/rules
  blocking_severities: [HIGH]
"""


def _mine_manifest(root: Path) -> dict:
    manifests = sorted((root / ".warden" / "out").glob("*-mine/manifest.json"))
    assert len(manifests) == 1, f"expected one mine manifest, got {manifests}"
    return json.loads(manifests[0].read_text())


def _enroll(root: Path) -> None:
    """A repo.yaml this repo's own schema accepts.

    Not the smallest one it accepts — `lang`, `description` and `reason` are
    all optional. Do not read this fixture as a minimal-schema reference.
    """
    (root / ".warden" / "rules").mkdir(parents=True, exist_ok=True)
    write(root, "repo.yaml", _FIXTURE_REPO_YAML)


def _mine_offline(root: Path, monkeypatch) -> None:
    monkeypatch.chdir(root)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)


def test_a_repo_with_no_repo_yaml_is_stamped_unenrolled(repo, monkeypatch,
                                                            capsys):
    """The one condition that actually means unenrolled still says so."""
    _mine_offline(repo, monkeypatch)

    assert cli.main(["mine"]) == 0
    capsys.readouterr()
    assert _mine_manifest(repo)["rules_version"] == "unenrolled"


def test_a_crashing_rules_version_is_not_stamped_unenrolled(
        repo, monkeypatch, capsys):
    """An ENROLLED repo whose version computation raises must not have its
    evidence stamped with a false enrollment state. "cannot compute" and
    "absent" are different facts with different remedies."""
    _enroll(repo)
    _mine_offline(repo, monkeypatch)

    def boom(*a, **k):
        raise OSError("ruleset unreadable")

    monkeypatch.setattr(cli.rules_mod, "rules_version", boom)

    assert cli.main(["mine"]) == 0
    err = capsys.readouterr().err
    stamped = _mine_manifest(repo)["rules_version"]
    assert stamped != "unenrolled", \
        "a crash inside rules_version was reported as an unenrolled repo"
    assert stamped == "uncomputable:OSError", stamped
    # A reader who never opens the manifest still learns the version was not
    # computed, and what stopped it.
    assert "OSError" in err and "ruleset unreadable" in err
    assert "not 'unenrolled'" in err


def test_a_repo_yaml_that_cannot_be_read_is_not_called_unenrolled(
        repo, monkeypatch, capsys):
    """The probe is a read, not a stat. `Path.is_file()` answers False for a
    directory in repo.yaml's place — swallowing the OSError and performing the
    very collapse these tests guard against, one level below the `except`."""
    (repo / ".warden" / "rules").mkdir(parents=True, exist_ok=True)
    (repo / "repo.yaml").mkdir()
    _mine_offline(repo, monkeypatch)

    assert cli.main(["mine"]) == 0
    err = capsys.readouterr().err
    stamped = _mine_manifest(repo)["rules_version"]
    assert stamped != "unenrolled", \
        "a repo.yaml that could not be read was reported as an absent one"
    assert stamped == "uncomputable:IsADirectoryError", stamped
    assert "could not be read" in err and "IsADirectoryError" in err


def test_a_dangling_repo_yaml_symlink_is_unenrolled(repo, monkeypatch,
                                                        capsys):
    """The boundary the case above must not overshoot. A symlink pointing at
    nothing resolves to no repo.yaml, so the read raises FileNotFoundError and
    "unenrolled" is the honest answer — the same one `rules._enforcement_surface`
    gives it. Over-correcting this to `uncomputable:` would be as wrong in the
    other direction."""
    (repo / "repo.yaml").symlink_to(repo / "gone.yaml")
    _mine_offline(repo, monkeypatch)

    assert cli.main(["mine"]) == 0
    assert capsys.readouterr().err == ""
    assert _mine_manifest(repo)["rules_version"] == "unenrolled"


def test_an_invalid_repo_yaml_names_its_error_class(repo, monkeypatch,
                                                        capsys):
    """A repo.yaml that fails schema validation raises ConfigError; the stamp
    names the class so a reader knows what to go and fix (error-names-cause).

    Named for schema validity, not for parseability: the body is valid YAML.
    The genuinely unparseable route is pinned one unit down, by
    tests/test_config.py::test_invalid_yaml_rejected."""
    _enroll(repo)
    write(repo, "repo.yaml", "repo: fixture\n")  # missing required sections
    _mine_offline(repo, monkeypatch)

    assert cli.main(["mine"]) == 0
    err = capsys.readouterr().err
    assert _mine_manifest(repo)["rules_version"] == "uncomputable:ConfigError"
    # The message an operator actually hits, not only the monkeypatched one.
    assert "ConfigError" in err and "not 'unenrolled'" in err


def test_an_enrolled_repo_still_gets_its_real_rules_version(repo,
                                                                monkeypatch,
                                                                capsys):
    """The narrowing must not cost the happy path: an enrolled repo whose
    version computes gets the hash, not a marker."""
    _enroll(repo)
    _mine_offline(repo, monkeypatch)

    assert cli.main(["mine"]) == 0
    capsys.readouterr()
    stamped = _mine_manifest(repo)["rules_version"]
    assert stamped not in ("unenrolled", ""), stamped
    assert not stamped.startswith("uncomputable:"), stamped
    assert len(stamped) == 12, stamped


# --- a repo.yaml that never finishes being read ------------------------------


def test_a_fifo_repo_yaml_is_uncomputable_and_still_writes_a_manifest(
        repo, monkeypatch, capsys, make_fifo):
    """A FIFO in repo.yaml's place is bounded, not a hang.

    `mkfifo repo.yaml`, tokens unset, `warden mine`: an unbounded read parks
    the command indefinitely and leaves its run dir holding pr-corpus.json
    with NO manifest.json — the orphan-run-dir shape ingest is hardened
    against. It can park in the probe (no writer) or, with a writer holding
    the FIFO open, downstream in `config.load`, which is why the bound is at
    the readers and not at this call site.

    Two claims are asserted together on purpose: the run finishes AND it says
    the honest thing. A FIFO is a repo.yaml that could not be read, which is
    not the same fact as a repo that has none — stamping `unenrolled` here
    would trade the hang for a false enrollment claim."""
    (repo / ".warden" / "rules").mkdir(parents=True, exist_ok=True)
    make_fifo(repo / "repo.yaml")
    _mine_offline(repo, monkeypatch)

    assert finishes_within(30, cli.main, ["mine"]) == 0
    err = capsys.readouterr().err
    stamped = _mine_manifest(repo)["rules_version"]
    assert stamped != "unenrolled", \
        "a repo.yaml that could not be read was reported as an absent one"
    assert stamped == "uncomputable:NotARegularFileError", stamped
    assert "could not be read" in err and "NotARegularFileError" in err


def test_a_probe_that_runs_out_of_memory_still_writes_a_manifest(
        repo, monkeypatch, capsys):
    """A way to leave `_cmd_mine` between `write_artifact` and
    `write_manifest` that would land in exactly the orphan run dir the FIFO
    test guards against.

    The probe reads the whole file through the shared reader, so an absurd
    repo.yaml raises MemoryError — not an OSError, so a handler catching only
    OSError would let it escape and the run would lose its evidence for a
    reason unrelated to enrollment."""
    _enroll(repo)
    _mine_offline(repo, monkeypatch)

    def oom(*a, **k):
        raise MemoryError("repo.yaml does not fit in memory")

    monkeypatch.setattr(cli.config_mod, "read_repo_yaml", oom)

    assert cli.main(["mine"]) == 0
    err = capsys.readouterr().err
    stamped = _mine_manifest(repo)["rules_version"]
    assert stamped == "uncomputable:MemoryError", stamped
    assert "MemoryError" in err and "not the same as an unenrolled repo" in err
