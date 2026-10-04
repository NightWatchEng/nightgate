"""scripts/ast-identity.py proves a change touches only comments and docstrings.

Each case builds a throwaway git repo with a base commit, applies one kind of
edit on a later commit, and asserts the script's exit code: 0 when the code is
identical once docstrings are stripped, 1 when any code, mode bit or source
encoding differs or a .py file is added, and 2 when nothing could be compared
or a file cannot be decoded or parsed. A pathspec narrows which files are
compared, and an exclude pathspec narrows the positive ones beside it.
"""
import ast
import os
import resource
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ast-identity.py"

BASE = textwrap.dedent('''\
    """Module docstring."""


    def add(a, b):
        """Return the sum."""
        # a comment
        return a + b
    ''')

CODE_FIRST = textwrap.dedent('''\
    def pick(x):
        if x:
            return 1
        return 0
    ''')


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True)


def _repo(tmp_path: Path, files: dict[str, bytes] | None = None) -> Path:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.fileMode", "true")
    for rel, data in (files or {"pkg/mod.py": BASE.encode()}).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(data)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "tag", "base")
    return repo


def _commit(repo: Path, rel: str, text: str | bytes) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "edit")


def _usage() -> str:
    """The script's own USAGE constant, read from its source rather than
    restated here, so a test that wants the usage text pins no wording."""
    for node in ast.parse(SCRIPT.read_text()).body:
        if (isinstance(node, ast.Assign)
                and [getattr(t, "id", None) for t in node.targets] == ["USAGE"]):
            return node.value.value
    raise AssertionError("the script defines no USAGE constant")


def _run(repo: Path, *pathspec: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(repo), "base", "HEAD", *pathspec],
        capture_output=True, text=True)


def test_a_comment_only_edit_exits_0(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "identical  pkg/mod.py" in result.stdout


def test_a_docstring_only_edit_exits_0(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "pkg/mod.py", BASE.replace('"""Return the sum."""',
                                             '"""Add two numbers."""'))
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_one_token_code_change_exits_1(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "pkg/mod.py", BASE.replace("a + b", "a - b"))
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "DIFFERS" in result.stdout


def test_a_changed_first_statement_that_is_code_exits_1(tmp_path):
    """Only a string-constant first statement is a docstring. A body whose
    first statement is code keeps it in the comparison."""
    repo = _repo(tmp_path, {"pkg/mod.py": CODE_FIRST.encode()})
    _commit(repo, "pkg/mod.py", CODE_FIRST.replace("if x:", "if not x:"))
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "DIFFERS" in result.stdout


def test_an_added_python_file_exits_1(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "pkg/new.py", "X = 1\n")
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "pkg/new.py" in result.stdout


def test_a_pathspec_excludes_files_outside_it(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "other/new.py", "X = 1\n")
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    assert _run(repo).returncode == 1
    result = _run(repo, "pkg/")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "other/new.py" not in result.stdout


def test_a_pathspec_still_catches_a_code_change_inside_it(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "pkg/mod.py", BASE.replace("a + b", "a - b"))
    result = _run(repo, "pkg/")
    assert result.returncode == 1, result.stdout + result.stderr


def test_a_pathspec_matching_no_python_file_exits_2(tmp_path):
    """Comparing zero files proves nothing, so it is never reported as
    identical. The message names the pathspecs that matched nothing."""
    repo = _repo(tmp_path)
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    _commit(repo, "cage/run.sh", "#!/bin/sh\necho hi\n")
    for spec in ("pkgg", "cage/run.sh"):
        result = _run(repo, spec)
        assert result.returncode == 2, (spec, result.stdout + result.stderr)
        assert spec in result.stdout, result.stdout
        assert "identical" not in result.stdout, result.stdout


def test_a_mode_bit_change_alone_exits_1(tmp_path):
    repo = _repo(tmp_path)
    os.chmod(repo / "pkg" / "mod.py", 0o755)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chmod")
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "DIFFERS" in result.stdout and "mode" in result.stdout


def test_a_coding_cookie_change_that_redecodes_a_literal_exits_1(tmp_path):
    """The same bytes under a different coding declaration are a different
    string constant, so the file is parsed from its bytes and a changed
    declaration counts as a difference."""
    body = 'S = "é"\n'.encode("utf-8")
    repo = _repo(tmp_path, {"pkg/mod.py": b"# -*- coding: utf-8 -*-\n" + body})
    _commit(repo, "pkg/mod.py", b"# -*- coding: latin-1 -*-\n" + body)
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "DIFFERS" in result.stdout


def test_a_coding_declaration_change_alone_exits_1(tmp_path):
    """An ASCII body parses to the same AST under either declaration, so the
    changed declaration itself is what differs."""
    body = b'S = "plain"\n'
    repo = _repo(tmp_path, {"pkg/mod.py": b"# -*- coding: utf-8 -*-\n" + body})
    _commit(repo, "pkg/mod.py", b"# -*- coding: latin-1 -*-\n" + body)
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "encoding" in result.stdout


def test_a_latin_1_file_with_a_comment_edit_exits_0(tmp_path):
    """A file that is valid under its own declaration but not UTF-8 is
    decoded by that declaration and compared, not refused."""
    head = b"# -*- coding: latin-1 -*-\n"
    repo = _repo(tmp_path, {"pkg/mod.py": head + b'S = "\xe9"  # one\n'})
    _commit(repo, "pkg/mod.py", head + b'S = "\xe9"  # two\n')
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_undecodable_file_exits_2(tmp_path):
    """Exit 1 means a file differs; a file that cannot be decoded or parsed
    was not evaluated, which is exit 2."""
    repo = _repo(tmp_path)
    _commit(repo, "pkg/mod.py", BASE.encode() + b'S = "\xff"\n')
    result = _run(repo)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "could not evaluate" in result.stdout


def test_an_unknown_base_exits_2(tmp_path):
    repo = _repo(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(repo), "no-such-ref"],
        capture_output=True, text=True)
    assert result.returncode == 2, result.stdout + result.stderr


def test_every_pathspec_must_match_a_changed_python_file(tmp_path):
    """Pathspecs are checked one at a time, not together. A misspelled or
    non-.py entry beside one that matches would otherwise drop out silently,
    and the run would report identical for a path it never compared."""
    repo = _repo(tmp_path)
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    _commit(repo, "cage/run.sh", "#!/bin/sh\necho hi\n")
    for unmatched in ("pkgg", "cage/run.sh"):
        result = _run(repo, "pkg/", unmatched)
        assert result.returncode == 2, (unmatched, result.stdout + result.stderr)
        assert unmatched in result.stdout, result.stdout
        assert "identical" not in result.stdout, result.stdout
    both = _run(repo, "pkg/", "pkg/mod.py")
    assert both.returncode == 0, both.stdout + both.stderr
    assert both.stdout.count("pkg/mod.py") == 1, both.stdout


def test_a_usage_error_under_python_OO_exits_2(tmp_path):
    """-OO strips the docstring the usage message is printed from. A usage
    error is still a failure to evaluate, never a difference."""
    result = subprocess.run(
        [sys.executable, "-OO", str(SCRIPT), str(tmp_path)],
        capture_output=True, text=True)
    assert result.returncode == 2, result.stdout + result.stderr
    assert _usage() in result.stdout, result.stdout + result.stderr


def test_a_comment_edit_on_a_too_deeply_nested_expression_exits_2(tmp_path):
    """A tree too deep to compare raises RecursionError. The file was not
    evaluated, so that is exit 2, and the cause is named."""
    deep = "X = " + "+".join(["1"] * 5000) + "  # one\n"
    repo = _repo(tmp_path, {"pkg/mod.py": deep.encode()})
    _commit(repo, "pkg/mod.py", deep.replace("# one", "# two"))
    result = _run(repo)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "RecursionError" in result.stdout, result.stdout


def test_git_missing_from_path_exits_2(tmp_path):
    """A git that cannot be run is a git failure: exit 2, naming git."""
    repo = _repo(tmp_path)
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    no_git = tmp_path / "no-git"
    no_git.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(repo), "base", "HEAD"],
        capture_output=True, text=True,
        env={**os.environ, "PATH": str(no_git)})
    assert result.returncode == 2, result.stdout + result.stderr
    assert "git" in result.stdout, result.stdout


def test_a_report_that_cannot_be_written_exits_2(tmp_path):
    """A buffered stdout fails only when it is flushed. With a zero file-size
    limit that flush fails, and a verdict that was never delivered is exit 2,
    not the interpreter's shutdown status."""
    repo = _repo(tmp_path)
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    out = tmp_path / "out.txt"
    with open(out, "wb") as sink:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(repo), "base", "HEAD"],
            stdout=sink, stderr=subprocess.PIPE, text=True,
            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE,
                                                  (0, 0)))
    assert result.returncode == 2, result.stderr


def test_a_closed_stdout_exits_2(tmp_path):
    """With stdout closed nothing can be reported, so nothing was proved."""
    repo = _repo(tmp_path)
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    result = subprocess.run(
        ["sh", "-c", 'exec "$0" "$@" >&-',
         sys.executable, str(SCRIPT), str(repo), "base", "HEAD"],
        stderr=subprocess.PIPE, text=True)
    assert result.returncode == 2, result.stderr


def test_a_glob_pathspec_compares_every_python_file_git_matches(tmp_path):
    """Files are selected by git's own pathspec semantics, so a glob that
    matches a root-level file compares it too."""
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            "top.py": b"X = 1\n",
                            "pkgutil.py": b"Y = 1\n"})
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    _commit(repo, "top.py", "X = 2\n")
    _commit(repo, "pkgutil.py", "Y = 2\n")
    for spec, root_file in (("*", "top.py"), ("pkg*", "pkgutil.py")):
        result = _run(repo, spec)
        assert result.returncode == 1, (spec, result.stdout + result.stderr)
        assert root_file in result.stdout, (spec, result.stdout)


def test_a_subdirectory_repo_argument_exits_2(tmp_path):
    """git -C <subdir> scopes the listing to that subdirectory, so a change
    outside it would never be compared. Only the top level is accepted."""
    repo = _repo(tmp_path)
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    _commit(repo, "other/new.py", "X = 1\n")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(repo / "pkg"), "base", "HEAD"],
        capture_output=True, text=True)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "identical" not in result.stdout, result.stdout


def test_a_positive_pathspec_whose_matches_are_all_excluded_exits_2(tmp_path):
    """Excludes narrow the positive pathspecs they sit beside, as in git. With
    pkg/mod.py the only changed .py under pkg, 'pkg' minus that file compares
    nothing under pkg, even though a changed file elsewhere survives the
    exclude on its own."""
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            "top.py": b"X = 1  # one\n"})
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    _commit(repo, "top.py", "X = 1  # two\n")
    for exclude in (":!pkg/mod.py", ":^pkg/mod.py", ":(exclude)pkg/mod.py"):
        result = _run(repo, "pkg", exclude)
        assert result.returncode == 2, (exclude, result.stdout + result.stderr)
        assert "pkg" in result.stdout, result.stdout
        assert "identical" not in result.stdout, result.stdout
    # Beside a positive pathspec that does match, the emptied one is still
    # refused: every positive pathspec must match after excludes.
    _commit(repo, "other/x.py", "Y = 1\n")
    result = _run(repo, "other", "pkg", ":!pkg/mod.py")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "identical" not in result.stdout, result.stdout


def test_an_excluded_file_is_never_compared_or_reported(tmp_path):
    """'pkg :(exclude)pkg/gen.py' leaves a code change in pkg/gen.py out of
    the proof, so a comment-only change in pkg/mod.py is identical."""
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            "pkg/gen.py": b"G = 1\n"})
    _commit(repo, "pkg/gen.py", "G = 2\n")
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    result = _run(repo, "pkg", ":(exclude)pkg/gen.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pkg/gen.py" not in result.stdout, result.stdout
    assert "identical  pkg/mod.py" in result.stdout, result.stdout


def test_the_caret_short_form_excludes_like_the_bang(tmp_path):
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            "pkg/gen.py": b"G = 1\n"})
    _commit(repo, "pkg/gen.py", "G = 2\n")
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    result = _run(repo, "pkg", ":^pkg/gen.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pkg/gen.py" not in result.stdout, result.stdout


def test_exclude_only_pathspecs_select_the_whole_tree_minus_them(tmp_path):
    """With no positive pathspec git selects every path the excludes leave,
    so a code change outside them is caught, and a file under them, here one
    that does not even parse, is never compared."""
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            "pkg/gen.py": b"G = 1\n",
                            "top.py": b"X = 1\n"})
    _commit(repo, "pkg/gen.py", "G = (\n")
    _commit(repo, "pkg/mod.py", BASE.replace("a + b", "a - b"))
    _commit(repo, "top.py", "X = 2\n")
    result = _run(repo, ":!pkg/gen.py", ":^pkg/mod.py")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "DIFFERS    top.py" in result.stdout, result.stdout
    assert "pkg/" not in result.stdout, result.stdout


def test_pathspec_magic_reaches_git_in_every_listing(tmp_path):
    """icase, literal and glob magic, on positive and exclude pathspecs
    alike, are git's to apply, so each combination selects the files git
    selects."""
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            "pkg/gen.py": b"G = 1\n"})
    _commit(repo, "pkg/gen.py", "G = 2\n")
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    for specs in ((":(icase)PKG", ":(exclude,icase)PKG/GEN.PY"),
                  (":(glob)pkg/*.py", ":(exclude,literal)pkg/gen.py"),
                  (":(literal)pkg", ":!pkg/gen.py"),
                  ("pkg", ":(exclude,glob)pkg/g*.py")):
        result = _run(repo, *specs)
        assert result.returncode == 0, (specs, result.stdout + result.stderr)
        assert "pkg/gen.py" not in result.stdout, (specs, result.stdout)


def test_a_glob_exclude_removes_no_more_than_its_glob_matches(tmp_path):
    """Under glob magic '*' stops at a slash, so 'pkg/g*.py' excludes
    pkg/gen.py but not pkg/gx/sub.py. Read without glob magic, the same
    pattern would exclude the code change in pkg/gx/sub.py too and hide it."""
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            "pkg/gen.py": b"G = 1\n",
                            "pkg/gx/sub.py": b"S = 1\n"})
    _commit(repo, "pkg/gen.py", "G = 2\n")
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    _commit(repo, "pkg/gx/sub.py", "S = 2\n")
    result = _run(repo, "pkg", ":(exclude,glob)pkg/g*.py")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "DIFFERS    pkg/gx/sub.py" in result.stdout, result.stdout
    assert "pkg/gen.py" not in result.stdout, result.stdout


def test_an_exclude_that_excludes_nothing_is_allowed(tmp_path):
    """Only a positive pathspec must match. An exclude that removes nothing
    leaves every selected file compared, so the verdict stands."""
    repo = _repo(tmp_path)
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    result = _run(repo, "pkg", ":!nowhere")
    assert result.returncode == 0, result.stdout + result.stderr
    result = _run(repo, "pkg", ":!nowhere", "pkgg")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "pkgg" in result.stdout, result.stdout


def _skip_unless_star_filenames(tmp_path: Path) -> None:
    """A file literally named with '*' is what makes literal magic change a
    result. Where the filesystem refuses that name the case cannot be built."""
    probe = tmp_path / "probe*.py"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        pytest.skip(f"filesystem refuses '*' in a filename: {e}")


def test_a_literal_exclude_removes_only_the_file_named_by_it(tmp_path):
    """':(exclude,literal)pkg/*.py' excludes the file literally named
    'pkg/*.py', not every file the wildcard would match, so a code change in
    pkg/mod.py is still compared. Read without literal magic the exclude would
    hide it behind a comment-only change in top.py."""
    _skip_unless_star_filenames(tmp_path)
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            "pkg/*.py": b"STAR = 1\n",
                            "top.py": b"X = 1  # one\n"})
    _commit(repo, "pkg/mod.py", BASE.replace("a + b", "a - b"))
    _commit(repo, "top.py", "X = 1  # two\n")
    result = _run(repo, ":(exclude,literal)pkg/*.py")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "pkg/mod.py" in result.stdout, result.stdout


def test_a_literal_positive_pathspec_selects_only_the_file_named_by_it(tmp_path):
    """':(literal)pkg/*.py' selects the file literally named 'pkg/*.py', whose
    change is comment-only, and not pkg/mod.py, whose code changed. The same
    pathspec without literal magic is a wildcard that selects both."""
    _skip_unless_star_filenames(tmp_path)
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            "pkg/*.py": b"STAR = 1  # one\n"})
    _commit(repo, "pkg/mod.py", BASE.replace("a + b", "a - b"))
    _commit(repo, "pkg/*.py", "STAR = 1  # two\n")
    literal = _run(repo, ":(literal)pkg/*.py")
    assert literal.returncode == 0, literal.stdout + literal.stderr
    assert "pkg/mod.py" not in literal.stdout, literal.stdout
    wildcard = _run(repo, "pkg/*.py")
    assert wildcard.returncode == 1, wildcard.stdout + wildcard.stderr
    assert "pkg/mod.py" in wildcard.stdout, wildcard.stdout


def test_a_literal_positive_pathspec_matching_no_changed_file_exits_2(tmp_path):
    """Each positive pathspec must itself match a selected file, and literal
    magic applies to that check too. The file named 'pkg/*.py' is unchanged,
    so ':(literal)pkg/*.py' matches nothing, even though pkg/mod.py, selected
    beside it, is a file its wildcard reading would match."""
    _skip_unless_star_filenames(tmp_path)
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            "pkg/*.py": b"STAR = 1\n"})
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    result = _run(repo, "pkg/mod.py", ":(literal)pkg/*.py")
    assert result.returncode == 2, result.stdout + result.stderr


def test_a_literal_exclude_keeps_a_code_change_in_its_own_file_out(tmp_path):
    """':(exclude,literal)pkg/*.py' removes the file named 'pkg/*.py' from the
    proof, so its code change is never compared or reported and a comment-only
    change in pkg/mod.py is identical."""
    _skip_unless_star_filenames(tmp_path)
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            "pkg/*.py": b"STAR = 1\n"})
    _commit(repo, "pkg/*.py", "STAR = 2\n")
    _commit(repo, "pkg/mod.py", BASE.replace("# a comment", "# reworded"))
    result = _run(repo, ":(exclude,literal)pkg/*.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pkg/*.py" not in result.stdout, result.stdout
