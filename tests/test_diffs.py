"""Diff parsing: annotation, structured line extraction, context building."""

import subprocess
from pathlib import Path

from warden import diffs as diffs_mod

SAMPLE = """diff --git a/pkg/mod.py b/pkg/mod.py
index 111..222 100644
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -10,4 +10,5 @@ def f():
 context1
-removed line
+added line
+another added
 context2
"""


class TestAnnotate:
    def test_new_file_line_numbers(self):
        out = diffs_mod.annotate(SAMPLE).splitlines()
        assert "   10:  context1" in out
        assert "   -: -removed line" in out
        assert "   11: +added line" in out
        assert "   12: +another added" in out
        assert "   13:  context2" in out

    def test_headers_left_untouched(self):
        out = diffs_mod.annotate(SAMPLE)
        assert "diff --git a/pkg/mod.py" in out
        assert "\n--- a/pkg/mod.py" in out  # not annotated as a removal
        assert "@@ -10,4 +10,5 @@" in out

    def test_no_newline_marker_keeps_annotating_regression(self):
        """The '\\ No newline at end of file' marker must not
        strip line numbers from the added lines that follow it."""
        diff = ("@@ -1,2 +1,3 @@\n"
                "    line1\n"
                "-line2\n"
                "\\ No newline at end of file\n"
                "+line2changed\n"
                "+line3\n"
                "\\ No newline at end of file\n")
        out = diffs_mod.annotate(diff).splitlines()
        assert "    2: +line2changed" in out
        assert "    3: +line3" in out


class TestParseLines:
    def test_added_and_removed_extracted(self):
        added, removed = diffs_mod.parse_lines(SAMPLE)
        assert added == ((11, "added line"), (12, "another added"))
        assert removed == ("removed line",)

    def test_no_newline_marker_skipped(self):
        diff = ("@@ -1,1 +1,2 @@\n"
                "-old\n"
                "\\ No newline at end of file\n"
                "+new1\n"
                "+new2\n")
        added, removed = diffs_mod.parse_lines(diff)
        assert added == ((1, "new1"), (2, "new2"))
        assert removed == ("old",)

    def test_multiple_hunks_renumber(self):
        diff = SAMPLE + "@@ -50,2 +51,2 @@\n context50\n+new51\n"
        added, _ = diffs_mod.parse_lines(diff)
        assert (52, "new51") in added


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _commit(cwd: Path, msg: str) -> None:
    _git(cwd, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg)


class TestGetContext:
    def test_excludes_lines_and_readers(self, tmp_path):
        _git(tmp_path, "init", "-q", "-b", "main")
        (tmp_path / "base.txt").write_text("v1\n")
        _git(tmp_path, "add", ".")
        _commit(tmp_path, "root")
        (tmp_path / "keep.py").write_text("kept = 1\n")
        (tmp_path / "skip.lock").write_text("noise\n")
        (tmp_path / "base.txt").write_text("v2\n")
        _git(tmp_path, "add", ".")
        _commit(tmp_path, "change")

        ctx = diffs_mod.get_context(tmp_path, "HEAD~1", "HEAD",
                                    excludes=("**/*.lock", "*.lock"))
        assert set(ctx.files) == {"keep.py", "base.txt"}
        assert ctx.added["keep.py"] == ((1, "kept = 1"),)
        assert ctx.removed["base.txt"] == ("v1",)
        assert ctx.read_base("base.txt") == "v1\n"
        assert ctx.read_head("base.txt") == "v2\n"
        assert ctx.read_base("keep.py") is None  # didn't exist at base
        assert len(ctx.base) == 40 and len(ctx.head) == 40

    def test_all_files_carries_the_excluded_paths(self, tmp_path):
        """all_files is every changed path BEFORE excludes —
        so a checker can see an excluded companion tree changed. files stays
        the scanned (exclude-filtered) subset."""
        _git(tmp_path, "init", "-q", "-b", "main")
        (tmp_path / "root.txt").write_text("v1\n")
        _git(tmp_path, "add", "."); _commit(tmp_path, "root")
        (tmp_path / "keep.py").write_text("kept = 1\n")
        (tmp_path / "skip.lock").write_text("noise\n")
        _git(tmp_path, "add", "."); _commit(tmp_path, "change")

        ctx = diffs_mod.get_context(tmp_path, "HEAD~1", "HEAD",
                                    excludes=("**/*.lock", "*.lock"))
        assert "skip.lock" not in ctx.files          # excluded from scanning
        assert "skip.lock" in ctx.all_files          # but visible as changed
        assert set(ctx.files) <= set(ctx.all_files)  # all_files is a superset

    def test_all_files_defaults_to_files_when_unset(self):
        # a hand-built context predating the field is never smaller than what
        # it scans — an unset all_files falls back to files.
        ctx = diffs_mod.DiffContext(
            base="a" * 40, head="b" * 40, files=("warden/x.py",),
            added={}, removed={}, read_base=lambda p: None,
            read_head=lambda p: None)
        assert ctx.all_files == ("warden/x.py",)

    def test_bad_ref_raises(self, tmp_path):
        _git(tmp_path, "init", "-q", "-b", "main")
        try:
            diffs_mod.get_context(tmp_path, "nonexistent-ref")
            raise AssertionError("expected DiffError")
        except diffs_mod.DiffError:
            pass


# ---------- the review package ----------------------------------------------
#
# One deterministic file for BASE..HEAD — commit log, diff --stat, and a
# -U10 diff — so a review subagent reads a file from disk instead of the
# controller holding the whole diff in its context.

def _range_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    # 12 lines: enough that -U10 context reaches lines a default -U3 cannot.
    lines = "".join(f"l{i} = {i}\n" for i in range(1, 13))
    (tmp_path / "a.py").write_text(lines)
    _git(tmp_path, "add", ".")
    _commit(tmp_path, "root commit")
    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "a.py").write_text(lines + "y = 2\n")
    (tmp_path / "b.py").write_text("z = 3\n")
    _git(tmp_path, "add", ".")
    _commit(tmp_path, "feature commit")
    return tmp_path


class TestReviewPackage:
    def test_package_carries_log_stat_and_wide_diff(self, tmp_path):
        repo = _range_repo(tmp_path)
        pkg = diffs_mod.review_package(repo, "main", "HEAD")
        assert "feature commit" in pkg          # git log --oneline
        assert "2 files changed" in pkg         # git diff --stat
        assert "+y = 2" in pkg and "+z = 3" in pkg   # the diff itself
        # -U10 pinned: l3 sits ten lines
        # above the appended change, inside -U10 context and beyond -U3's
        # reach — reverting the flag turns this assertion red.
        assert "l3 = 3" in pkg
        assert "l9 = 9" in pkg

    def test_package_is_byte_deterministic(self, tmp_path):
        repo = _range_repo(tmp_path)
        assert diffs_mod.review_package(repo, "main", "HEAD") == \
            diffs_mod.review_package(repo, "main", "HEAD")

    def test_package_names_the_resolved_shas(self, tmp_path):
        repo = _range_repo(tmp_path)
        pkg = diffs_mod.review_package(repo, "main", "HEAD")
        base = subprocess.run(["git", "rev-parse", "main"], cwd=repo,
                              capture_output=True, text=True).stdout.strip()
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True).stdout.strip()
        assert base in pkg and head in pkg

    def test_package_is_immune_to_user_git_config(self, tmp_path):
        """Named regression: user git config must not reach the package.
        color.ui=always injects ANSI escapes, diff.external replaces hunks
        with per-invocation temp paths, diff.noprefix drops the a/ b/
        prefixes, and core.abbrev changes --oneline hash lengths — the
        determinism the docstring promises must hold under a user's
        ~/.gitconfig, not only under a clean one."""
        repo = _range_repo(tmp_path)
        for k, v in (("color.ui", "always"), ("diff.external", "/bin/echo"),
                     ("diff.noprefix", "true"), ("core.abbrev", "12")):
            _git(repo, "config", k, v)
        pkg = diffs_mod.review_package(repo, "main", "HEAD")
        assert "\x1b[" not in pkg, "ANSI color leaked into the package"
        assert "+y = 2" in pkg, "diff.external replaced the real diff"
        assert "a/a.py" in pkg and "b/a.py" in pkg, "prefixes must be pinned"
        assert pkg == diffs_mod.review_package(repo, "main", "HEAD")


def _rename_repo(tmp_path: Path) -> Path:
    """A range whose only change is a file MOVED between two subtrees."""
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "examples").mkdir()
    (tmp_path / "warden").mkdir()
    # Long enough that git scores the move as a 100% rename rather than an
    # unrelated add+delete — the case the collapse actually bites on.
    (tmp_path / "examples" / "x.py").write_text(
        "".join(f"line {i}\n" for i in range(60)))
    _git(tmp_path, "add", ".")
    _commit(tmp_path, "root commit")
    _git(tmp_path, "checkout", "-qb", "feature")
    _git(tmp_path, "mv", "examples/x.py", "warden/x.py")
    _commit(tmp_path, "move it")
    return tmp_path


def test_changed_files_reports_both_sides_of_a_rename(tmp_path):
    """`git diff --name-only` applies rename
    detection (git's default since 2.9) and prints the DESTINATION alone, so
    a moved file leaves no trace of the subtree it came FROM. Feed that to the
    verify-scope derivation and a scope covering only the emptied subtree is
    never required for the diff that emptied it. Named mutation: drop
    `--no-renames` and this returns `("warden/x.py",)`."""
    repo = _rename_repo(tmp_path)
    assert diffs_mod.changed_files(repo, "main", "HEAD") == (
        "examples/x.py", "warden/x.py")


def test_scope_files_carries_the_rename_blind_list_all_files_cannot(tmp_path):
    """The same defect at the seam `warden review` actually reads: `all_files`
    is the rename-DETECTED list the checkers scan, `scope_files` is the
    rename-blind one the scope derivation needs, and they differ exactly on a
    move."""
    repo = _rename_repo(tmp_path)
    ctx = diffs_mod.get_context(repo, "main", "HEAD")
    assert ctx.all_files == ("warden/x.py",)
    assert ctx.scope_files == ("examples/x.py", "warden/x.py")


def test_scope_files_defaults_to_all_files_on_a_hand_built_context():
    """A context built by hand (tests, plugins) declares no scope_files; an
    unset value must never be SMALLER than what is scanned, the same contract
    `all_files` already keeps."""
    ctx = diffs_mod.DiffContext(base="a" * 40, head="b" * 40,
                                files=("app/x.py",), added={}, removed={},
                                read_base=lambda p: None,
                                read_head=lambda p: None)
    assert ctx.scope_files == ctx.all_files == ("app/x.py",)


def test_diff_paths_and_lines_are_read_relative_to_a_subdirectory(tmp_path):
    """A repository enrolled below the git root reads its diff through its own
    directory: paths relative to it, nothing outside it, and every added line
    of every file it lists."""
    repo = tmp_path / "mono"
    api = repo / "svc" / "api"
    api.mkdir(parents=True)

    def git(*args: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", *args],
                       cwd=repo, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    (api / "a.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    (api / "a.py").write_text("x = 1\ny = 2\n")
    (repo / "top.txt").write_text("t\n")
    git("add", "-A")
    git("commit", "-qm", "change")

    ctx = diffs_mod.get_context(api, "HEAD~1", "HEAD")
    assert ctx.files == ("a.py",) and ctx.all_files == ("a.py",)
    assert ctx.scope_files == ("a.py",)
    assert ctx.added["a.py"] == ((2, "y = 2"),)
    assert ctx.read_head("a.py") == "x = 1\ny = 2\n"
    assert ctx.read_base("a.py") == "x = 1\n"
    assert diffs_mod.changed_files(api, "HEAD~1", "HEAD") == ("a.py",)
    assert diffs_mod.range_paths(api, "HEAD~1", "HEAD") == ("a.py",)

    at_root = diffs_mod.get_context(repo, "HEAD~1", "HEAD")
    assert at_root.files == ("svc/api/a.py", "top.txt")
    assert at_root.added["svc/api/a.py"] == ((2, "y = 2"),)
    assert at_root.read_head("svc/api/a.py") == "x = 1\ny = 2\n"
    assert diffs_mod.range_paths(repo, "HEAD~1", "HEAD") == ("svc/api/a.py", "top.txt")


def test_every_changed_name_listing_reads_a_quoted_name_as_the_file(tmp_path):
    """git C-quotes a non-ASCII name, and a name with `"` even under
    core.quotepath=false, in a listing that is not NUL-terminated. Review,
    verify-scope choice and explain each read such a listing, so each got a
    string naming no file. Read at the root and below it."""
    from warden import explain as explain_mod

    repo = tmp_path / "mono"
    api = repo / "svc" / "api"
    api.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (api / "keep.txt").write_text("k\n")
    _git(repo, "add", "-A")
    _commit(repo, "base")
    names = ("café.py", 'q"x.py', ":/leak.py")
    for name in names:
        (api / name).parent.mkdir(parents=True, exist_ok=True)
        (api / name).write_text("x = 1\n")
    # What `:/leak.py` read as a pathspec names instead: the top-level file.
    (repo / "leak.py").write_text("another file, at the top\n")
    _git(repo, "add", "-A")
    _commit(repo, "change")

    for where, prefix, outside in ((api, "", ()), (repo, "svc/api/", ("leak.py",))):
        want = tuple(sorted((*(prefix + n for n in names), *outside)))
        ctx = diffs_mod.get_context(where, "HEAD~1", "HEAD")
        assert tuple(sorted(ctx.files)) == want
        assert tuple(sorted(ctx.all_files)) == want
        assert tuple(sorted(ctx.scope_files)) == want
        for name in names:
            assert ctx.added[prefix + name] == ((1, "x = 1"),), name
        assert tuple(sorted(diffs_mod.changed_files(where, "HEAD~1", "HEAD"))) == want
        assert tuple(sorted(diffs_mod.range_paths(where, "HEAD~1", "HEAD"))) == want

        class _Config:
            root = where
        assert sorted(explain_mod._changed_files(_Config, "HEAD~1")) == list(want)


def test_range_binding_at_ingest_reads_the_enrollments_paths(tmp_path):
    """`memory ingest` re-checks that a findings set names a file its range
    changed. Below the git root the findings name the enrollment's paths, so
    the changed list must be read relative to it or every honest attestation
    reads as out of range."""
    from warden import memory as memory_mod

    repo = tmp_path / "mono"
    api = repo / "svc" / "api"
    api.mkdir(parents=True)

    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                               "-c", "commit.gpgsign=false", *args],
                              cwd=repo, check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    (api / "a.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD").strip()
    (api / "a.py").write_text("x = 1\ny = 2\n")
    git("add", "-A")
    git("commit", "-qm", "change")
    head = git("rev-parse", "HEAD").strip()

    finding = {"rule_id": "secrets-in-diff", "severity": "HIGH", "file": "a.py", "line": 2,
               "finding": "planted", "evidence": "+y = 2"}
    kind, message = memory_mod.range_binding_at_ingest(
        api, {"findings": [finding], "base_sha": base, "head_sha": head})
    assert kind == "ok", (kind, message)
