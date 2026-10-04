"""Usage: ast-identity.py <repo> <base> [<head>] [<pathspec>...]

Proves a comments-and-docstrings-only change: for every .py file changed
between the merge base of base and head (head defaults to HEAD), the AST with
docstrings removed must be identical. Comments never reach the AST, so only
docstrings need stripping. Pathspecs select files with git's own pathspec
semantics, relative to <repo>: all of them reach git, unaltered, in one
listing, so magic such as icase, literal and glob applies as git applies it,
and an exclude pathspec (':!path', ':^path' or ':(exclude)path') removes what
it matches from what the positive pathspecs select. With no positive pathspec
git selects the whole tree minus the excludes. Only the .py files among those
selected are compared (default: every .py file). <repo> must be the top level
of its repository: git scopes a listing to a subdirectory, which would leave
every change outside it uncompared.

Every positive pathspec must match at least one selected .py file, after
excludes. A positive pathspec whose changed .py files are all excluded fails
that check. An exclude pathspec is not required to exclude anything: one that
excludes nothing, a misspelled one included, is accepted without a warning,
because it leaves every selected file compared.

What the AST cannot see is compared directly: a file's mode bits and its
source encoding (a changed coding declaration can change what a string literal
decodes to, so each file is parsed from its bytes and a changed declaration
differs). Added, deleted or type-changed .py files differ, because none of
those is a comment change.

Exit 0 identical, 1 a file differs, 2 could not evaluate. Exit 2 covers any
failure to evaluate: a usage error, a <repo> that is not the top level, git
failing or missing from PATH, a file that cannot be decoded, parsed or compared
(a tree too deep to walk included), pathspecs that together select no changed
.py file, a positive pathspec that selects no changed .py file after excludes,
since a pathspec that compares zero files proves nothing about its path, and a
verdict that cannot be written to stdout. An exclude pathspec that excludes
nothing is allowed and never exits 2 by itself. Exit 1 is reached only by
comparing a file, finding it differs, and reporting that.
"""
import ast
import io
import os
import subprocess
import sys
import tokenize

# The first docstring line, kept separately because python -OO strips
# __doc__ and a usage error must still print something.
USAGE = "Usage: ast-identity.py <repo> <base> [<head>] [<pathspec>...]"


class Unevaluable(Exception):
    """The range could not be evaluated. Carries the cause as its message."""


def cause(e):
    """The message naming why evaluation stopped."""
    return str(e) if isinstance(e, Unevaluable) else f"{type(e).__name__}: {e}"


def git(repo, *args, binary=False):
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                              text=not binary, check=True).stdout
    except FileNotFoundError as e:
        raise Unevaluable(f"git could not be run: {e}") from e
    except subprocess.CalledProcessError as e:
        err = e.stderr
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        raise Unevaluable(f"git {args[0]} failed: {(err or '').strip()}") from e


def encoding(src):
    return tokenize.detect_encoding(io.BytesIO(src).readline)[0]


def normal(src):
    """The AST dump of `src` (bytes, so its coding declaration applies) with
    every docstring removed. Only a string-constant first statement is a
    docstring; any other first statement stays in the comparison."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.dump(tree, include_attributes=False)


def changed(repo, mb, head, specs):
    """{path: (status, old_mode, new_mode, path)} for each .py file that the
    pathspecs `specs` select together and that differs between `mb` and
    `head`. The pathspecs reach git as given, in one listing, so git's own
    matching decides which files they cover, excludes and magic included; only
    the .py filter is applied here. Read with --raw -z so modes are visible
    and paths arrive unquoted."""
    try:
        out = git(repo, "diff", "--raw", "-z", "--no-renames", mb, head, "--",
                  *specs)
    except UnicodeDecodeError as e:
        raise Unevaluable(f"the diff listing is not UTF-8: {e}") from e
    fields = out.split("\0")
    rows = {}
    for meta, path in zip(fields[0::2], fields[1::2]):
        if not meta or not path.endswith(".py"):
            continue
        old_mode, new_mode, _, _, status = meta.lstrip(":").split(" ")
        rows[path] = (status, old_mode, new_mode, path)
    return rows


def compare(repo, mb, head, old_mode, new_mode, path):
    """None when identical, else the reason the file differs."""
    if old_mode != new_mode:
        return f"mode {old_mode} -> {new_mode}"
    before = git(repo, "show", f"{mb}:{path}", binary=True)
    after = git(repo, "show", f"{head}:{path}", binary=True)
    enc_before, enc_after = encoding(before), encoding(after)
    if enc_before != enc_after:
        return f"source encoding {enc_before} -> {enc_after}"
    if normal(before) != normal(after):
        return "code"
    return None


def evaluate(argv):
    """(path, reason) for every changed .py file, reason None when identical.

    Raises on anything that stops a file or pathspec from being evaluated;
    main turns every exception into exit 2."""
    if len(argv) < 2:
        raise Unevaluable(f"expected <repo> <base>, got {len(argv)} "
                          f"argument(s)\n{(__doc__ or USAGE).strip()}")
    repo, base = argv[0], argv[1]
    head = argv[2] if len(argv) > 2 else "HEAD"
    specs = argv[3:] or ["."]
    top = git(repo, "rev-parse", "--show-toplevel").strip()
    if os.path.realpath(top) != os.path.realpath(repo):
        raise Unevaluable(f"{repo} is not the top level of its repository "
                          f"({top}); a listing from a subdirectory leaves "
                          "changes outside it uncompared")
    mb = git(repo, "merge-base", base, head).strip()
    # The selection is ONE listing of every pathspec, so excludes narrow the
    # positive pathspecs exactly as git applies them.
    rows = changed(repo, mb, head, specs)
    if not rows:
        raise Unevaluable("pathspec(s) " + " ".join(specs) + " together "
                          "select no changed .py file; comparing zero files "
                          "proves nothing")
    # Each pathspec must also match a selected file. A pathspec listed alone
    # is matched by git, and intersecting that listing with the selection
    # attributes selected files to it without parsing its magic here. A
    # positive pathspec whose matches were all excluded, or that matched
    # nothing, intersects to nothing. An exclude listed alone selects the
    # whole tree minus itself, which contains every selected file, so an
    # exclude always passes, including one that excludes nothing.
    unmatched = [spec for spec in specs
                 if not rows.keys() & changed(repo, mb, head, [spec]).keys()]
    if unmatched:
        raise Unevaluable("no selected .py file matches pathspec(s) "
                          + " ".join(unmatched)
                          + " after excludes; comparing zero files under a "
                          "pathspec proves nothing about its path")
    results = []
    for path in sorted(rows):
        status, old_mode, new_mode, _ = rows[path]
        if status != "M":
            results.append((path, f"{status} (added, deleted or type-changed "
                                  "is not a comment change)"))
            continue
        try:
            reason = compare(repo, mb, head, old_mode, new_mode, path)
        except Exception as e:
            raise Unevaluable(f"{path}: {cause(e)}") from e
        results.append((path, reason))
    return results


def abandon_stdout():
    """Point fd 1 at the null device after a failed write. The unwritten
    buffer is flushed again when the interpreter exits, and a second failure
    there would replace exit 2 with the interpreter's own shutdown status."""
    try:
        fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(fd, 1)
        os.close(fd)
    except OSError:
        pass


def report(lines):
    """True only when every line was written and flushed to stdout. Stdout
    is buffered when it is not a terminal, so a closed pipe or a full device
    fails at the flush, not at the print."""
    if sys.stdout is None:
        return False
    try:
        for line in lines:
            print(line)
        sys.stdout.flush()
        return True
    except BaseException:
        abandon_stdout()
        return False


def main(argv):
    if sys.stdout is None:
        # Started with stdout closed: no verdict can be delivered, so none is.
        return 2
    try:
        results = evaluate(argv)
    except BaseException as e:
        report([f"could not evaluate: {cause(e)}"])
        return 2
    differs = [(path, reason) for path, reason in results if reason is not None]
    lines = [f"identical  {path}" if reason is None
             else f"DIFFERS    {path}: {reason}" for path, reason in results]
    lines.append(f"{len(results)} python file(s) changed; "
                 + ("CODE CHANGED" if differs
                    else "code identical, comments and docstrings only"))
    if not report(lines):
        # A verdict that could not be reported was not delivered.
        return 2
    return 1 if differs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
