"""The reader-facing docs stay runnable: every `warden` or `cage` invocation in a
shell block names a subcommand and flags its `--help` accepts, and every
Python, YAML and TOML block parses. Nothing documented is ever executed; help
text is read in-process from each CLI's own parser.

An invocation is recognised bare, through a path (`.warden/bin/warden`), after
a wrapper in `WRAPPERS` with its options, as `python -m warden` or
`python -m warden.cli`, and after `uv run`/`uvx`, where the first word naming a
CLI, a `python -m` or a shell in `SHELLS` starts the command.

A `$(...)` substitution, one nested in arithmetic `$((...))`, and a backtick
span are each read as shell of their own. So is the string a shell in `SHELLS`
runs with `-c` (a short-option cluster holding `c`; a long option such as
`--norc` never is), wherever that shell is a word of a segment whose command
is not a CLI: as the command, after a wrapper, or after any other command
(`timeout 5 sh -c '...'`, `xargs sh -c '...'`), and every such shell in the
segment (a second `find -exec sh -c`) is read. That too is deliberately
wide: `echo sh -c 'warden x'` is read as if the shell ran.

Past those, a segment whose command is something else but which has `warden`
or `cage` as a word (or a path ending in one) outside the shell strings is
reported: before a shell, among its positional arguments
(`xargs sh -c '"$0" "$@"' warden x`, `sh -c 'exec "$@"' sh warden x`), or in
a later clause (`find ... -exec warden x {} +`). The exceptions are a word that
is a redirect target and a command in `OPERAND_ONLY`, taking the file as an
operand without running it. That is deliberately wide: a directory named
`warden` handed to another tool is reported too. Such a report says only that
the CLI is named there; its subcommand and flags are not checked. A CLI before
any shell string is reported as behind an unrecognised command, to be
registered; one past a shell string is reported as its own kind, since whether
that string runs the words past it is not read, and the fix is to call the CLI
directly or make its invocation visible, never to register the shell. So is one
behind a shell in `SHELLS` that runs no string (`sh .warden/bin/warden x`,
`bash --norc warden x`): the shell runs it as a script, and the fix is to call
the CLI directly. A shell is never registered in `WRAPPERS` or `OPERAND_ONLY`
(a cell refuses it): `OPERAND_ONLY` returns before any report, so a shell in it
would silence every CLI named after that shell, its `-c` string's arguments
included. A shell line that does not parse (an unclosed quote) is reported
against its block, and the rest of that block goes unread.

The limits: a CLI inside any other quoted string (`ssh host 'warden x'`,
`eval "warden x"`, `"$SHELL" -c 'warden x'`, a shell's positional argument
`sh -c '$0' 'warden x'`) is part of one word and is not checked;
`command -v warden` is a lookup and is not checked; and only fenced blocks are
read, so an inline command span in prose or a table is not checked.
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import shlex
import tomllib
from functools import cache
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

from cage import cli as cage_cli
from warden import cli as warden_cli

ROOT = Path(__file__).resolve().parent.parent
PAGES = (*sorted((ROOT / "docs" / "wiki").glob("*.md")), ROOT / "README.md")

CLIS = {"warden": warden_cli.main, "cage": cage_cli.main}
SHELL_LANGS = frozenset({"sh", "bash", "shell", "zsh", "console"})
FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w-]*)")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
CHOICES = re.compile(r"^\s+\{([^}]+)\}", re.M)
OPTION_SPEC = re.compile(r"^  (-\S.*?)(?:\s{2,}|$)", re.M)
FLAG = re.compile(r"(?<![\w-])(--?[A-Za-z][\w-]*)")
OPERATORS = frozenset({"&&", "||", "|", ";", "(", ")", "&", ";;"})
PREFIX_WORDS = frozenset({"if", "then", "else", "elif", "do", "!"})
# A wrapper runs the command after its options: the short options that take a
# value, the long ones that do, and the options whose value is itself split
# into words (`env -S`).
WRAPPERS = {
    "sudo": ("CDghpRrTtUu", {"--askpass-prompt", "--chdir", "--chroot", "--close-from",
                             "--command-timeout", "--group", "--host", "--other-user",
                             "--prompt", "--role", "--type", "--user"}),
    "env": ("uCS", {"--unset", "--chdir", "--split-string"}),
    "nice": ("n", {"--adjustment"}),
    "nohup": ("", set()),
    "command": ("", set()),
    "time": ("", set()),
    "exec": ("a", set()),
}
SPLIT_VALUE = frozenset({"-S", "--split-string"})
# Commands that name a CLI file as an operand and never run it.
OPERAND_ONLY = frozenset({"chmod"})
# Shells whose `-c` string is read as a command, and their long options that
# take the next word as a value.
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
SHELL_VALUE_OPTIONS = frozenset({"--rcfile", "--init-file"})


class Invocation(NamedTuple):
    cli: str
    args: list[str]
    # What the CLI was named behind, when it was: the unrecognised command, or
    # the shell whose string it stands after.
    behind: str | None = None
    # "call" is a checked invocation; "behind_command" a CLI named behind an
    # unrecognised command; "behind_shell" a CLI named behind a shell in
    # `SHELLS` that runs no string; "shell_argument" a CLI past a shell's
    # string, among what may be its positional arguments.
    kind: str = "call"


def _is_redirect(word: str) -> bool:
    return set(word) <= set("<>&0123456789") and any(c in word for c in "<>")


def fenced_blocks(page: Path):
    """Yield (line, language, body) for every fenced block on a page."""
    lines = page.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        m = FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        fence, lang = m.group(1), m.group(2).lower()
        end = i + 1
        while end < len(lines) and not lines[end].strip().startswith(fence):
            end += 1
        yield i + 1, lang, "\n".join(lines[i + 1:end])
        i = end + 1


def shell_text(lang: str, body: str) -> str | None:
    """The shell source of a block, or None when the block is not shell.

    An unlabelled block counts only through its `$ `-prompted lines.
    """
    if lang in SHELL_LANGS:
        lines = body.splitlines()
        if any(line.startswith("$ ") for line in lines):
            lines = [line[2:] for line in lines if line.startswith("$ ")]
        return "\n".join(lines)
    if lang == "":
        prompted = [line[2:] for line in body.splitlines() if line.startswith("$ ")]
        return "\n".join(prompted) or None
    return None


def strip_heredocs(source: str) -> str:
    kept, terminator = [], None
    for line in source.splitlines():
        if terminator is not None:
            if line.strip() == terminator:
                terminator = None
            continue
        kept.append(line)
        m = HEREDOC.search(line)
        if m:
            terminator = m.group(1)
    return "\n".join(kept)


def _closing(line: str, i: int) -> int:
    """The index just past the `)` closing the `$(` at `i`, or -1 when the line
    ends first. Quotes, escapes, backtick spans and nested substitutions are
    stepped over, so a `)` inside them closes nothing."""
    depth, j, double = 1, i + 2, False
    while j < len(line):
        c = line[j]
        if c == "\\":
            j += 2
            continue
        if line.startswith("$(", j):
            j = _closing(line, j)
            if j == -1:
                return -1
            continue
        if c == '"':
            double = not double
        elif c in "'`" and not (double and c == "'"):
            j = line.find(c, j + 1)
            if j == -1:
                return -1
        elif not double and c in "()":
            depth += 1 if c == "(" else -1
            if not depth:
                return j + 1
        j += 1
    return -1


def _substitutions(line: str) -> tuple[str, list[str]]:
    """The line with each `$(...)` and backtick span outside single quotes and
    comments replaced by `$_`, and the commands those spans held. Arithmetic
    `$((...))` holds a command only in a substitution of its own."""
    out: list[str] = []
    held: list[str] = []
    i, single, double = 0, False, False
    while i < len(line):
        c = line[i]
        if c == "\\" and not single:
            out.append(line[i:i + 2])
            i += 2
            continue
        if c == "'" and not double:
            single = not single
        elif c == '"' and not single:
            double = not double
        elif (c == "#" and not single and not double
              and (i == 0 or line[i - 1].isspace())):
            out.append(line[i:])
            break
        elif line.startswith("$(", i) and not single:
            j = _closing(line, i)
            j = len(line) + 1 if j == -1 else j
            body = line[i + 2:j - 1]
            if body.startswith("(") and body.endswith(")"):
                held += _substitutions(body[1:-1])[1]
            else:
                held.append(body)
            out.append("$_")
            i = j
            continue
        elif c == "`" and not single:
            end = line.find("`", i + 1)
            end = len(line) if end == -1 else end
            held.append(line[i + 1:end])
            out.append("$_")
            i = end + 1
            continue
        out.append(c)
        i += 1
    return "".join(out), held


def invocations(source: str):
    """Yield an Invocation for every `warden`/`cage` command in shell source."""
    for line in strip_heredocs(source).replace("\\\n", " ").splitlines():
        line, held = _substitutions(line)
        for command in held:
            yield from invocations(command)
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        segment: list[str] = []
        for token in [*lexer, ";"]:
            if token in OPERATORS:
                yield from _segment_invocation(segment)
                segment = []
            else:
                segment.append(token)


def _skip_options(words: list[str], short: str, long: set[str]) -> list[str]:
    """The words after a wrapper's options. A short cluster (`-iu`) ends at its
    first value-taking letter, whose value is the cluster's rest or the next
    word; a split value (`env -S 'a b'`) comes back as the words it holds."""
    i = 0
    while i < len(words):
        word = words[i]
        if word == "--":
            return words[i + 1:]
        if not word.startswith("-"):
            return words[i:]
        i += 1
        if word.startswith("--"):
            name, attached, value = word.partition("=")
            if name not in long:
                continue
        else:
            at = next((j for j, c in enumerate(word[1:], 1) if c in short), None)
            if at is None:
                continue
            name, value = f"-{word[at]}", word[at + 1:]
            attached = value
        if not attached:
            value = words[i] if i < len(words) else ""
            i += 1
        if name in SPLIT_VALUE:
            return _skip_options([*shlex.split(value), *words[i:]], short, long)
    return []


def _strip_prefixes(words: list[str]) -> list[str]:
    """Drop what runs a command without being it: shell keywords, variable
    assignments, a wrapper in `WRAPPERS` with its options, and `python -m`."""
    while words:
        head = Path(words[0]).name
        if words[0] in PREFIX_WORDS or re.match(r"^\w+=", words[0]):
            words = words[1:]
        elif head in WRAPPERS:
            rest = _skip_options(words[1:], *WRAPPERS[head])
            options = words[1:len(words) - len(rest)]
            if head == "command" and any(
                    o.startswith("-") and o != "--" and set(o[1:]) & {"v", "V"}
                    for o in options):
                return []  # `command -v` looks a command up; it runs nothing
            words = rest
        elif _runs_a_module(words):
            module = words[2] if len(words) > 2 else ""
            cli = module.removesuffix(".cli")
            if cli not in CLIS:
                return [module, *words[3:]]
            words = [cli, *words[3:]]
        else:
            return words
    return words


def _runs_a_module(words: list[str]) -> bool:
    return bool(words) and bool(re.fullmatch(r"python[\d.]*", Path(words[0]).name)) \
        and words[1:2] == ["-m"]


def _shell_string(words: list[str]) -> int | None:
    """Where in a shell's arguments the command string its `-c` runs stands,
    None when it runs no string. Only a short-option cluster holding `c` is
    `-c`; a long option never is, and one in `SHELL_VALUE_OPTIONS` takes the
    next word with it. `-` and `--` end the options."""
    runs_string, i = False, 0
    while i < len(words):
        word = words[i]
        if word in ("-", "--"):
            i += 1
            break
        if word.startswith("--"):
            i += 2 if word in SHELL_VALUE_OPTIONS else 1
        elif word[:1] in ("-", "+") and len(word) > 1:
            runs_string = runs_string or (word[0] == "-" and "c" in word[1:])
            # `-o` and `-O` take the next word
            i += 2 if word[-1] in ("o", "O") else 1
        else:
            break
    return i if runs_string and i < len(words) else None


def _named_cli(words: list[str]) -> int | None:
    """Where the first CLI a segment names as a word stands, redirect targets
    aside."""
    for at, (before, word) in enumerate(zip(["", *words], words)):
        if not _is_redirect(before) and Path(word).name in CLIS:
            return at
    return None


def _segment_invocation(segment: list[str]):
    words = _strip_prefixes(list(segment))
    if not words:
        return
    if words[0] in ("uv", "uvx"):
        words = _strip_prefixes(words[next(
            (i for i, w in enumerate(words) if "$" not in w and (
                Path(w).name in CLIS or Path(w).name in SHELLS
                or _runs_a_module(words[i:]))), len(words)):])
    if not words:
        return
    if Path(words[0]).name not in CLIS:
        # Every shell running a string is read wherever it stands. The words
        # around each string (the runner before it, the shell's positional
        # arguments and any later clause after it) stay held to the
        # named-CLI rule.
        # `after` holds, for each string, the shell and where the words past
        # it begin among the words outside the strings.
        outside, rest, after = [], words, []
        while True:
            shell = next((i for i, w in enumerate(rest)
                          if Path(w).name in SHELLS
                          and not (i and _is_redirect(rest[i - 1]))
                          and _shell_string(rest[i + 1:]) is not None), None)
            if shell is None:
                break
            string = shell + 1 + _shell_string(rest[shell + 1:])
            yield from invocations(rest[string])
            outside += rest[:shell]
            after.append((len(outside), rest[shell]))
            rest = rest[string + 1:]
        remaining = [*outside, *rest]
        at = _named_cli(remaining)
        if at is None or Path(words[0]).name in OPERAND_ONLY:
            return
        named = Path(remaining[at]).name
        past = [shell for start, shell in after if start <= at]
        if past:
            # Whether the string runs the words past it is the shell's own
            # business, which this check does not read.
            yield Invocation(named, [], past[-1], "shell_argument")
        elif Path(words[0]).name in SHELLS:
            # A shell running a script: registering the shell is never the
            # fix, so this is not a `behind_command`.
            yield Invocation(named, [], words[0], "behind_shell")
        else:
            yield Invocation(named, [], words[0], "behind_command")
        return
    args = []
    for word in words[1:]:
        if _is_redirect(word):
            break
        if "$" not in word:
            args.append(word)
    yield Invocation(Path(words[0]).name, args)


@cache
def help_text(cli: str, path: tuple[str, ...]) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            CLIS[cli]([*path, "--help"])
        except SystemExit:
            pass
    return out.getvalue()


def invocation_problems(cli: str, args: list[str], behind: str | None = None,
                        kind: str = "call") -> list[str]:
    """What `--help` does not accept in one invocation, empty when all of it is."""
    if kind == "shell_argument":
        return [f"{cli} is named past a `{behind} -c` string, among what may "
                "be its positional arguments, and this check cannot tell "
                "whether that string runs them: call "
                f"{cli} directly on the doc line, or rewrite the line so the "
                f"{cli} invocation is visible"]
    if kind == "behind_shell":
        return [f"{cli} is named behind the shell `{behind}`, which runs it "
                "as a script this check does not read: call "
                f"{cli} directly on the doc line"]
    if kind == "behind_command":
        return [f"{cli} is named behind `{behind}`, which this check does not "
                "recognise: add it to WRAPPERS, or to OPERAND_ONLY if it never "
                "runs the file"]
    assert kind == "call", f"no report for an invocation of kind {kind!r}"
    problems, path = [], []
    text = help_text(cli, ())
    after_flag = positionals_free = False
    for arg in args:
        choices = [] if positionals_free else [
            c.strip() for m in CHOICES.findall(text) for c in m.split(",")]
        if arg.startswith("-") and len(arg) > 1:
            flag = arg.split("=", 1)[0]
            accepted = {f for spec in OPTION_SPEC.findall(text) for f in FLAG.findall(spec)}
            if flag not in accepted:
                problems.append(f"{cli} {' '.join(path)}: no flag {flag}".replace("  ", " "))
            after_flag = "=" not in arg
            continue
        if arg in choices:
            path.append(arg)
            parent, text = text, help_text(cli, tuple(path))
            # A choice that opens no parser of its own is an action value;
            # what follows it is that action's positionals.
            positionals_free = text == parent
        elif choices and not after_flag:
            problems.append(f"{cli} {' '.join(path)}: no subcommand {arg!r}".replace("  ", " "))
            break
        after_flag = False
    return problems


def yaml_run_steps(data):
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "run" and isinstance(value, str):
                yield value
            else:
                yield from yaml_run_steps(value)
    elif isinstance(data, list):
        for item in data:
            yield from yaml_run_steps(item)


def page_problems(page: Path) -> list[str]:
    problems = []
    for line, lang, body in fenced_blocks(page):
        where = f"{page.name}:{line}"
        shell = shell_text(lang, body)
        try:
            if lang == "python":
                ast.parse(body)
            elif lang in ("yaml", "yml"):
                shell = "\n".join(yaml_run_steps(yaml.safe_load(body)))
            elif lang == "toml":
                tomllib.loads(body)
        except (SyntaxError, yaml.YAMLError, tomllib.TOMLDecodeError) as e:
            problems.append(f"{where}: {lang} block does not parse: {e}")
            continue
        try:
            for invocation in invocations(shell or ""):
                problems += [f"{where}: {p}" for p in invocation_problems(*invocation)]
        except ValueError as e:
            problems.append(f"{where}: shell does not parse: {e}")
    return problems


@pytest.mark.parametrize("prefix", ["sudo", "sudo -u ci", "env X=1", "env -i X=1",
                                    "python -m", "python3 -m"])
def test_a_prefixed_invocation_is_still_checked(prefix):
    for cli in ("warden",) if prefix.endswith("-m") else CLIS:
        bogus = [p for inv in invocations(f"{prefix} {cli} explane")
                 for p in invocation_problems(*inv)]
        assert bogus, f"`{prefix} {cli} explane` passed unchecked"
    fine = list(invocations(f"{prefix} warden explain"))
    assert fine == [Invocation("warden", ["explain"])], fine
    assert not invocation_problems(*fine[0])


def _problems(source: str) -> list[str]:
    return [p for inv in invocations(source) for p in invocation_problems(*inv)]


@pytest.mark.parametrize("wrapper", ["sudo -iu ci", "sudo -D /tmp", "env --unset X",
                                     "nohup", "nice", "command", "nice -n 5",
                                     "env --unset=X", "time -p", "exec -a name"])
def test_a_bogus_subcommand_behind_a_wrapper_is_reported(wrapper):
    for cli in CLIS:
        assert _problems(f"{wrapper} {cli} explane"), (
            f"`{wrapper} {cli} explane` passed unchecked")
    assert list(invocations(f"{wrapper} warden explain")) == [
        Invocation("warden", ["explain"])]


def test_a_split_string_is_read_as_the_command_it_holds():
    assert _problems("env -S 'warden explane'")
    assert _problems("env --split-string='warden explane'")
    assert list(invocations("env -S 'X=1 warden' explain")) == [
        Invocation("warden", ["explain"])]


def test_a_split_string_is_reread_as_env_arguments():
    assert list(invocations("env -S '-i warden explain'")) == [
        Invocation("warden", ["explain"])]
    assert _problems("env -S '-i warden explane'")


@pytest.mark.parametrize("template", [
    "bash -c 'warden {sub}'", "sh -c 'warden {sub}'",
    "bash -euo pipefail -c 'warden {sub}'", 'echo "$(warden {sub})"',
    "echo `warden {sub}`", "uv run python -m warden.cli {sub}",
    "python -m warden.cli {sub}"])
def test_a_cli_in_a_shell_string_substitution_or_module_run_is_checked(template):
    assert _problems(template.format(sub="explane")), (
        f"`{template.format(sub='explane')}` passed unchecked")
    assert list(invocations(template.format(sub="explain"))) == [
        Invocation("warden", ["explain"])]


@pytest.mark.parametrize("source", ["timeout 5 warden explain", "doas warden explain",
                                    "stdbuf -oL cage explain",
                                    "python -m pytest .warden/bin/warden"])
def test_a_cli_behind_an_unrecognised_command_is_reported(source):
    assert _problems(source), f"`{source}` passed unchecked"


@pytest.mark.parametrize("source", ["chmod +x .warden/bin/warden",
                                    "cat > .warden/bin/warden",
                                    "command -v warden"])
def test_a_cli_that_is_only_an_operand_is_not_an_invocation(source):
    assert list(invocations(source)) == []


@pytest.mark.parametrize("template", [
    "uv run bash -c 'warden {sub}'", "uvx sh -c 'warden {sub}'",
    "timeout 5 sh -c 'warden {sub}'", "xargs sh -c 'warden {sub}'",
    "find . -exec sh -c 'warden {sub}' \\;"])
def test_a_shell_string_behind_any_runner_is_checked(template):
    assert _problems(template.format(sub="explane")), (
        f"`{template.format(sub='explane')}` passed unchecked")
    assert list(invocations(template.format(sub="explain"))) == [
        Invocation("warden", ["explain"])]


@pytest.mark.parametrize("source", ["bash --norc .warden/bin/warden explane",
                                    "bash --restricted .warden/bin/warden explane",
                                    "bash --rcfile rc .warden/bin/warden explane"])
def test_a_long_shell_option_is_not_read_as_c(source):
    assert _problems(source), f"`{source}` passed unchecked"


def test_a_long_shell_option_that_takes_a_value_is_skipped_with_it():
    assert _problems("bash --rcfile rc -c 'warden explane'")
    assert list(invocations("bash --init-file rc -c 'warden explain'")) == [
        Invocation("warden", ["explain"])]


@pytest.mark.parametrize("template", ["echo $(( $(warden {sub}) + 1 ))",
                                      "echo $(( `warden {sub}` * 2 ))",
                                      "x=$(printf ')'; warden {sub})",
                                      'x=$(printf ")"; warden {sub})',
                                      "x=$(printf \\); warden {sub})",
                                      'x=$(echo "$(printf "a)")"; warden {sub})',
                                      "x=$(echo \"it's\" '\")'; warden {sub})"])
def test_a_command_in_arithmetic_or_past_a_quoted_paren_is_checked(template):
    assert _problems(template.format(sub="explane")), (
        f"`{template.format(sub='explane')}` passed unchecked")
    assert list(invocations(template.format(sub="explain"))) == [
        Invocation("warden", ["explain"])]


@pytest.mark.parametrize("shell", ["sh", "bash", "zsh", "dash", "ksh", "/bin/zsh"])
def test_every_shell_has_its_c_string_read(shell):
    assert _problems(f"{shell} -c 'warden explane'"), f"`{shell} -c` passed unchecked"
    assert list(invocations(f"timeout 5 {shell} -c 'warden explain'")) == [
        Invocation("warden", ["explain"])]


@pytest.mark.parametrize("template", ["bash -c - 'warden {sub}'",
                                      "bash -c -- '-e; warden {sub}'"])
def test_a_shell_string_after_the_end_of_options_is_read(template):
    assert _problems(template.format(sub="explane")), (
        f"`{template.format(sub='explane')}` passed unchecked")
    assert list(invocations(template.format(sub="explain"))) == [
        Invocation("warden", ["explain"])]


@pytest.mark.parametrize("source", [
    "xargs -n1 sh -c '\"$0\" \"$@\"' warden explane",
    "find . -exec sh -c 'echo {}' {} + -exec warden explane {} +",
    "timeout 5 sh -c 'exec \"$@\"' sh warden explane",
    "sh -c '\"$0\" \"$@\"' warden explane",
    "find . -exec sh -c 'echo' {} + -exec sh -c 'warden explane' {} +"])
def test_a_cli_past_a_shell_string_is_reported(source):
    assert _problems(source), f"`{source}` passed unchecked"


@pytest.mark.parametrize("source, kind", [
    ("sh -c 'exec \"$@\"' sh warden explain", "shell_argument"),
    ("bash -c 'true' bash warden explain", "shell_argument"),
    ("xargs sh -c '\"$0\" \"$@\"' cage explain", "shell_argument"),
    ("doas warden explain", "behind_command"),
    ("doas warden sh -c 'true' sh cage explain", "behind_command"),
    ("doas sh .warden/bin/warden explain", "behind_command"),
    ("sh .warden/bin/warden explane", "behind_shell"),
    ("bash --norc .warden/bin/warden explane", "behind_shell"),
    ("sh -e .warden/bin/warden explain", "behind_shell"),
    ("uv run sh .warden/bin/warden explain", "behind_shell"),
    ("/bin/zsh cage explain", "behind_shell"),
    ("nohup ksh warden explain", "behind_shell")])
def test_a_cli_among_shell_arguments_is_reported_as_its_own_kind(source, kind):
    found = list(invocations(source))
    assert [inv.kind for inv in found] == [kind], found
    report = _problems(source)
    assert report, f"`{source}` passed unchecked"
    # Only a report behind an unrecognised command names the tables to
    # register it in; one past a shell string, or behind a shell running a
    # script, never does: registering a shell opens a silent hole.
    named = set(re.findall(r"\w+", " ".join(report))) & {"WRAPPERS", "OPERAND_ONLY"}
    if kind == "behind_command":
        assert named == {"WRAPPERS", "OPERAND_ONLY"}, report
    else:
        assert not named, report


@pytest.mark.parametrize("table", ["WRAPPERS", "OPERAND_ONLY"])
def test_no_shell_is_registered_as_a_wrapper_or_operand_only(table):
    # A shell in OPERAND_ONLY silences every CLI after it, `-c` string and
    # positional arguments included, because that early return runs before
    # any report; a shell in WRAPPERS reads its script as the command.
    registered = {Path(name).name for name in globals()[table]}
    assert not registered & SHELLS, (table, sorted(registered & SHELLS))


def test_a_shell_line_that_does_not_parse_is_reported_naming_the_page(tmp_path):
    page = tmp_path / "page.md"
    page.write_text("text\n\n```bash\nwarden explain\necho 'unterminated\n```\n",
                    encoding="utf-8")
    problems = page_problems(page)
    assert problems and all(p.startswith("page.md:3:") for p in problems), problems


@pytest.mark.parametrize("source", ["echo '$(warden explane)'",
                                    "echo '`warden explane`'",
                                    "echo hi # $(warden explane)",
                                    "echo hi # `warden explane`",
                                    'echo "\\$(warden explane)"',
                                    'echo "\\`warden explane\\`"'])
def test_a_substitution_that_is_quoted_commented_or_escaped_is_not_read(source):
    assert list(invocations(source)) == []


@pytest.mark.parametrize("source", ["echo \"it's $(warden explane)\"",
                                    "echo '\"' \"$(warden explane)\"",
                                    "echo a#$(warden explane)",
                                    "echo \\'\"$(warden explane)\"",
                                    "echo '\\'\"$(warden explane)\""])
def test_a_substitution_next_to_a_quote_comment_or_escape_is_read(source):
    assert _problems(source), f"`{source}` passed unchecked"


def test_documented_commands_are_accepted_and_code_blocks_parse():
    problems = [p for page in PAGES for p in page_problems(page)]
    assert not problems, "\n".join(problems)
