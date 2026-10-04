"""Decide, from the diff alone, whether a change can skip the full review round.

THE CLAIM THIS MODULE MAKES, AND THE ONE IT DOES NOT

It makes one narrow claim: for every changed Python file, the executable logic
normalises to the same tree once docstrings are removed, so running the new
file cannot take a different branch, call a different function or compute a
different value than running the old one. That is a proof over the parsed
source, not a guess about intent.

It is normalisation, not tree identity, and the difference is worth stating:
a body left empty by removing its docstring becomes `pass`, so a module that
was only a docstring compares equal to one that was only `pass`. Both execute
nothing, so the claim above holds — but `ast.dump` equality is the thing being
compared, and it is slightly coarser than "the same tree".

It does NOT claim the change is harmless. Docstrings survive into `__doc__`
and this repo prints them as CLI usage text, so an AST-identical diff can
still change what a program SAYS. That is a claim defect, and a diff that
reaches the light tier is exactly a diff whose whole remaining content is
claims: comments and docstrings. So the light tier is not "no review". It is
"the one review that fits what is left" — an audit of whether the claims the
diff edits are true of the tree.

ONE SURFACE CARRIES A PROOF: Python. Every other surface — Markdown and YAML
included — takes the full round.

Markdown is refused rather than proven, and on purpose. A .md file may BE
policy: a rule file and a skill are both read as instructions, and a document
carries commands, links and raw HTML that a reader runs or follows rather than
reads. Telling those apart from sentences takes a full Markdown parser, and a
reader that models less than the renderer does lets a changed command through
as a changed sentence. No such reader ships here. `proportion.prose_roots`,
the key that once admitted Markdown under a declared root, is REFUSED wherever
repo.yaml is read: a consumer that still declares it is told to delete it,
never silently given a different tier.

WHY ELIGIBILITY IS COMPUTED, NEVER DECLARED

A builder saying "this one is trivial" is self-certification, which is the
thing the gate exists to refuse. Nothing here reads an author's assertion, a
commit trailer, a label or a stored verdict. `classify` re-derives the verdict
from the two trees every time it runs, and the gate re-runs it rather than
trusting the verdict recorded beside the change. A recorded verdict that
disagrees with a fresh one is a hard failure, not a tie broken in the
builder's favour.

FAIL CLOSED, ALWAYS

Every unknown resolves to the expensive path. An extension nobody listed, a
file that will not parse or decode, a binary, a git invocation that fails, a
tree too deep to walk, an empty range: all of them return the full tier. The
only way to reach the light tier is for every changed file to be individually
proven, and a single unproven file pulls the whole range back. Comparing zero
files proves nothing, so an empty range is refused rather than passed.

THE FLOOR IS NOT CONFIGURABLE DOWNWARD

Some paths are never light however identical their trees are: the gate's own
machinery and the config that drives it. A comment-only edit to the thing that
decides what gets reviewed is precisely the edit that must be reviewed, since
the cost of being wrong there is unbounded.

That list is a CONSTANT in code, and a repo can only ADD to it. `repo.yaml`'s
optional `proportion.never_light:` is the consumer's half — this platform
cannot know which paths are a consumer's gate machinery, so a repo names its
own and they join the floor. Nothing subtracts. There is no key, argument or
order of entries that removes one, and `classify`'s `extra_floor` argument is
the same one-way lever for a caller.

READ FROM BOTH ENDS OF THE RANGE. A declaration read only at head would let a
diff rewrite the policy it is itself being judged under — the self-certification
this module exists to refuse, wearing a config key. `repo.yaml` is on the floor,
so such a diff could not have been light anyway; reading both ends is what makes
that a property of the reading rather than a coincidence of the floor's current
contents.

The key combines toward refusal: `never_light` is UNIONED across the two
ends, so an entry binds the moment it is written and cannot be withdrawn
inside the range that withdraws it.
"""

from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import tokenize
from dataclasses import dataclass, field

# Paths that never reach the light tier, whatever the trees say. These are the
# gate's own machinery and the config that drives it: warden and cage are the
# code that decides and records verdicts, .warden and .cage are the policy and
# evidence they read, .github and .githooks are where the gate is actually
# invoked, and repo.yaml and graph.yaml declare the crew and the rules.
#
# Matched at ANY DEPTH, not only at the repo root. A consumer's checkout is
# the normal case for this platform — examples/hello-svc carries its own
# .warden/ and repo.yaml, and a multi-module consumer nests them further — and
# a root-anchored match would leave a nested repo's gate machinery eligible
# while protecting this one's. A trailing slash marks a directory; anything
# else is a file name. Both match on a path-segment boundary, so `wardenx/`
# and `.warden-old/` are not the floor.
NEVER_LIGHT_FLOOR: tuple[str, ...] = (
    "warden/",
    "cage/",
    ".warden/",
    ".cage/",
    ".github/",
    ".githooks/",
    # The validators and simulators the gate shells out to. commit-lint.sh is
    # the commit fence and portability-sim.sh is the proof the gate bites on a
    # consumer; neither is ordinary source.
    "scripts/",
    # The skill pack reads as prose and behaves as code: it is the protocol
    # agents execute, so a sentence removed from it is a step not taken and a
    # new file in it is a new skill. Treating it as a document would let the
    # cheapest possible diff retire a guard.
    "skills/",
    "graph.yaml",
    "repo.yaml",
    # Packaging and the pinned dependency set decide what code actually runs.
    "pyproject.toml",
    "uv.lock",
)

# The round's own evidence, which the protocol FORCES every change to carry:
# a review that runs leaves a shard, and the shard is committed. Counting it as
# content would mean every change that followed the protocol touched .warden/
# and no change could ever be light — the tier would be unreachable by
# construction rather than by judgement. It is dropped from the comparison
# rather than passed by it, so a range with nothing else in it has nothing to
# compare and stays on the full path.
#
# ONLY A NEW ONE. The corpus is append-only evidence: an edited or deleted
# shard rewrites the record of a review that already happened, which is
# content of the most load-bearing kind, not a by-product of this round. The
# unattended runner's carve-out draws the line in exactly this place and for
# exactly this reason, and the first version of this one copied its path
# boundary while dropping its append-only half — so a light diff could have
# rewritten the review corpus it was being judged against.
EVIDENCE_CARVE_OUT: tuple[str, ...] = (".warden/memory/attest/",)

# Comments that change how another tool treats the file. None of these alters
# what Python executes, which is why the AST cannot see them, and all of them
# alter what a linter, a type checker, a formatter or a coverage report
# reports. Silencing a real finding is a gate change wearing a comment's
# clothes, so any movement in this set is refused — including a MOVE. Each
# directive is compared together with the code it sits on, because a `noqa`
# carried from one import to another leaves the set unchanged, the AST
# unchanged, and the lint result different.
# Matched against a comment's BODY, after its leading '#' is stripped, so this
# pattern never spells a directive itself — a linter reading this file must not
# mistake the vocabulary for an instruction.
_SUPPRESSION = re.compile(
    r"""^\s*(
        (no[q]a | nosec | noinspection)\b
      | (pragma | type | fmt | doctest | skipcq | vulture | codespell
         | ruff | mypy | pylint | flake8 | isort | pyright | pytype
         | coverage | black | yapf | bandit | autopep8 | pyre)\s*:
      | trunk-ignore\s*[(:]
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _directive(comment: str) -> bool:
    """Whether a comment token is a tool directive."""
    return bool(_SUPPRESSION.match(comment.lstrip("#")))

# A docstring carrying an interactive prompt is executable under a doctest
# runner. Nothing in this repo runs doctests today; a future --doctest-modules
# would turn every such docstring into code without anyone revisiting this
# file, so a changed one is refused now rather than after the incident.
_DOCTEST = re.compile(r"^\s*>>> ", re.MULTILINE)

FULL = "full"
LIGHT = "light"


class Undecidable(Exception):
    """The range could not be evaluated, so the verdict is the full tier."""


@dataclass(frozen=True)
class FileVerdict:
    """One changed path and why it did or did not clear the bar."""

    path: str
    eligible: bool
    surface: str
    reason: str


@dataclass(frozen=True)
class Verdict:
    """The range's tier, and the per-file findings that produced it."""

    tier: str
    base: str
    head: str
    merge_base: str
    reason: str
    files: tuple[FileVerdict, ...] = field(default_factory=tuple)

    @property
    def light(self) -> bool:
        return self.tier == LIGHT

    @property
    def blockers(self) -> tuple[FileVerdict, ...]:
        """The files that forced the full tier, in report order."""
        return tuple(f for f in self.files if not f.eligible)

    def as_evidence(self) -> dict:
        """The record written beside the change. It carries the inputs the
        verdict was derived from, so a later reader can recompute it rather
        than take its word."""
        return {
            "tier": self.tier,
            "base": self.base,
            "head": self.head,
            "merge_base": self.merge_base,
            "reason": self.reason,
            "files": [
                {
                    "path": f.path,
                    "eligible": f.eligible,
                    "surface": f.surface,
                    "reason": f.reason,
                }
                for f in self.files
            ],
        }


def _git(repo: str, *args: str, binary: bool = False):
    try:
        return subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=not binary,
            check=True,
        ).stdout
    except FileNotFoundError as exc:
        raise Undecidable(f"git could not be run: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        err = exc.stderr
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        raise Undecidable(
            f"git {args[0]} failed: {(err or '').strip()}") from exc
    except UnicodeDecodeError as exc:
        raise Undecidable(f"git {args[0]} output is not UTF-8: {exc}") from exc


def under_floor(path: str, extra: tuple[str, ...] = ()) -> str | None:
    """The floor entry covering `path`, or None.

    A caller may pass `extra` entries; they can only ADD, because the floor
    itself is a constant and nothing subtracts from it.

    The match is on path segments at any depth: "/" + path is searched for
    "/" + entry, so `.warden/` covers both `.warden/rules/r.py` and
    `examples/hello-svc/.warden/checkers/c.py`, while `wardenx/mod.py` and
    `.warden-old/x.py` match nothing.
    """
    probe = "/" + path
    for entry in (*NEVER_LIGHT_FLOOR, *extra):
        if entry.endswith("/"):
            if f"/{entry}" in probe + "/":
                return entry
        elif probe.endswith(f"/{entry}"):
            return entry
    return None


# The repo.yaml key a consumer widens its own floor with. Optional to the
# schema, so a repo.yaml without it loads and behaves exactly as it did before
# the key existed.
FLOOR_KEY = "proportion"
FLOOR_ENTRIES = "never_light"

# The key that once admitted Markdown under a declared root. The schema still
# names it so that an older repo.yaml parses; the runtime refuses it, with
# this one message, at every seam that reads the block.
REMOVED_ENTRIES = "prose_roots"
REMOVED_ENTRIES_REFUSAL = (
    f"`{FLOOR_KEY}.{REMOVED_ENTRIES}` is declared, but the prose arm of the "
    "proportionate tier was removed: Markdown always takes the full review "
    "rounds. Delete the key")


def removed_entries_refusal(block: object) -> str | None:
    """The refusal a parsed `proportion:` block earns, or None.

    One seam for the two readers of repo.yaml — `config.parse` on the working
    tree and `_declared_at` on a commit — so both refuse the same spelling
    with the same sentence.
    """
    if isinstance(block, dict) and REMOVED_ENTRIES in block:
        return REMOVED_ENTRIES_REFUSAL
    return None


def _declared_at(repo: str, rev: str, entries: str) -> tuple[str, ...]:
    """The `proportion.<entries>` paths repo.yaml declares at `rev`.

    Read out of the COMMIT with `git show`, never off the working tree: the
    verdict is about two commits, and a dirty checkout must not be able to
    change what either of them declared. That is also why this is not a call
    to `config.read_repo_yaml` — there is no file at a rev to open.

    A repo.yaml that is absent contributes nothing, and its ABSENCE IS PROVEN
    rather than inferred from a failure. `git show` exits non-zero for a path
    that is not in the rev and equally for a bad object, an unreadable object
    store or an ambiguous argument; reading all of those as "declares nothing"
    is the permissive direction for `never_light`, which restricts. So the
    listing decides existence and only then is the blob read: an empty listing
    at exit 0 is absence, and anything else that fails is `Undecidable`.

    A repo.yaml that is present and unreadable, or whose block is the wrong
    shape, is `Undecidable` for the same reason: a policy that cannot be read
    is not an empty one.
    """
    import yaml

    from . import yamlio

    def _run(*args: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(["git", "-C", repo, *args],
                                  capture_output=True, check=False)
        except OSError as exc:
            raise Undecidable(f"git could not be run: {exc}") from exc

    listing = _run("ls-tree", "-z", "--name-only", rev, "--", "repo.yaml")
    if listing.returncode != 0:
        raise Undecidable(
            f"could not list repo.yaml at {rev}, so whether this repo "
            "declares a proportion block there is unknown: "
            f"{listing.stderr.decode('utf-8', 'replace').strip()}")
    if not listing.stdout.strip():
        return ()           # proven absent at this rev; nothing is declared
    raw = _run("show", f"{rev}:repo.yaml")
    if raw.returncode != 0:
        raise Undecidable(
            f"repo.yaml is listed at {rev} but could not be read: "
            f"{raw.stderr.decode('utf-8', 'replace').strip()}")
    try:
        doc = yamlio.load(raw.stdout)
    except yaml.YAMLError as exc:
        raise Undecidable(
            f"repo.yaml at {rev} could not be parsed, so the paths it "
            f"declares cannot be read: {exc}") from exc
    if doc is None:
        return ()
    if not isinstance(doc, dict):
        raise Undecidable(f"repo.yaml at {rev} is not a mapping")
    block = doc.get(FLOOR_KEY)
    if block is None:
        return ()
    if not isinstance(block, dict):
        raise Undecidable(
            f"repo.yaml at {rev} declares `{FLOOR_KEY}:` as something other "
            "than a block of path lists, so what it says is unknown")
    refusal = removed_entries_refusal(block)
    if refusal is not None:
        raise Undecidable(f"repo.yaml at {rev}: {refusal}")
    if entries not in block:
        return ()
    found = block[entries]
    if not isinstance(found, list) or not all(
            isinstance(e, str) and e for e in found):
        raise Undecidable(
            f"repo.yaml at {rev} declares `{FLOOR_KEY}.{entries}:` as "
            "something other than a list of paths")
    return tuple(found)


def declared_floor(repo: str, *revs: str) -> tuple[str, ...]:
    """Every extra floor entry repo.yaml declares across `revs`, UNIONED.

    The union is the point, and it is the direction a RESTRICTING key wants.
    Reading only head would let a diff delete its own entry and widen the tier
    judging it; reading only the base would let a new entry take effect one
    range late. Adding both means an entry binds as soon as it is written and
    cannot be withdrawn inside the range that withdraws it.
    """
    found: set[str] = set()
    for rev in revs:
        found.update(_declared_at(repo, rev, FLOOR_ENTRIES))
    return tuple(sorted(found))


def is_round_evidence(path: str, status: str) -> bool:
    """Whether `path` is evidence this round leaves rather than content it read.

    `status` is git's raw status letter. Only "A" qualifies: a shard that is
    modified, deleted or type-changed is a rewrite of committed evidence, and
    that is content the round must be judged on.
    """
    return status == "A" and any(path.startswith(prefix)
                                 for prefix in EVIDENCE_CARVE_OUT)


def _encoding(src: bytes) -> str:
    return tokenize.detect_encoding(io.BytesIO(src).readline)[0]


def _suppressions(src: bytes) -> list[tuple[str, str, str]]:
    """Every tool-directive comment in `src`, each paired with the code it
    annotates, sorted.

    The pair is what makes a MOVE visible. The directive alone is
    position-free, so carrying `# noqa: F401` from one import to another
    leaves an identical sorted list while changing which finding is silenced.

    Two anchors, because directives sit in two places. An INLINE one is
    pinned by the code to its left. A STANDALONE one — `# fmt: off`,
    `# pragma: no cover` on its own line — has nothing to its left, so it is
    pinned by the next real statement instead: the thing it governs. Moving
    `# pragma: no cover` from one function to another silences coverage
    somewhere else while leaving the AST untouched, and the following
    statement is what changes when it does.

    Whitespace is removed from both anchors so reformatting is not mistaken
    for moving. What this still cannot see is a directive moved between two
    TEXTUALLY IDENTICAL statements — the anchors are equal because the
    statements are, and the tool it instructs is being pointed at the same
    source either way.
    """
    found = []
    try:
        toks = list(tokenize.tokenize(io.BytesIO(src).readline))
    except (tokenize.TokenError, SyntaxError, UnicodeDecodeError) as exc:
        raise Undecidable(f"could not tokenize: {exc}") from exc
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
            tokenize.DEDENT, tokenize.ENDMARKER, tokenize.ENCODING}
    for i, tok in enumerate(toks):
        if tok.type != tokenize.COMMENT or not _directive(tok.string):
            continue
        before = "".join(tok.line[:tok.start[1]].split())
        after = ""
        if not before:
            nxt = next((t for t in toks[i + 1:] if t.type not in skip), None)
            after = "".join(nxt.line.split()) if nxt else ""
        found.append((" ".join(tok.string.split()), before, after))
    return sorted(found)


def _docstrings(tree: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            try:
                text = ast.get_docstring(node, clean=False)
            except TypeError:
                text = None
            if text:
                out.append(text)
    return out


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    """Remove every docstring from `tree` in place. Only a bare string
    expression in first position is a docstring; any other leading statement
    stays in the comparison. A body emptied by the removal keeps a `pass` so
    the tree stays parseable and two differently-emptied bodies still compare
    equal."""
    for node in ast.walk(tree):
        if (
            isinstance(
                node,
                (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                 ast.ClassDef),
            )
            and node.body
        ):
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return tree


def _parse(src: bytes, path: str) -> ast.AST:
    try:
        return ast.parse(src)
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        raise Undecidable(f"{path}: could not parse: {exc}") from exc


# The git modes a regular file has. A symlink is 120000 and a gitlink 160000;
# `git show` on either returns the target or the pointer, not source, and
# parsing that as Python would "prove" something about a blob that is not
# code — two different link targets can spell the same expression.
_REGULAR_MODES = frozenset({"100644", "100755"})


def _mode_refusal(path: str, surface: str,
                  old_mode: str, new_mode: str) -> FileVerdict | None:
    """The refusal a file's git mode earns, or None.

    Asked before the bytes are read: `git show` on a symlink or a gitlink
    returns the target or the pointer, not the file it is named after, so
    anything derived from those bytes is a proof about the wrong blob.
    """
    if old_mode != new_mode:
        return FileVerdict(path, False, surface,
                           f"file mode changed {old_mode} to {new_mode}")
    if new_mode not in _REGULAR_MODES:
        return FileVerdict(path, False, surface,
                           f"mode {new_mode} is a symlink or submodule, not a "
                           "regular file, so its contents are not the source "
                           "they are named after")
    return None


def _python_verdict(repo: str, mb: str, head: str, path: str,
                    old_mode: str, new_mode: str) -> FileVerdict:
    """Compare one modified .py file across the range."""
    refusal = _mode_refusal(path, "python", old_mode, new_mode)
    if refusal is not None:
        return refusal
    before = _git(repo, "show", f"{mb}:{path}", binary=True)
    after = _git(repo, "show", f"{head}:{path}", binary=True)
    try:
        enc_before, enc_after = _encoding(before), _encoding(after)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise Undecidable(f"{path}: could not read encoding: {exc}") from exc
    if enc_before != enc_after:
        # A changed coding declaration changes what the same bytes decode to,
        # so identical trees would not mean identical string values.
        return FileVerdict(path, False, "python",
                           f"source encoding changed {enc_before} to "
                           f"{enc_after}")
    tree_before, tree_after = _parse(before, path), _parse(after, path)
    doc_before, doc_after = _docstrings(tree_before), _docstrings(tree_after)
    dumped_before = ast.dump(_strip_docstrings(tree_before),
                             include_attributes=False)
    dumped_after = ast.dump(_strip_docstrings(tree_after),
                            include_attributes=False)
    if dumped_before != dumped_after:
        return FileVerdict(path, False, "python",
                           "executable code changed")
    if _suppressions(before) != _suppressions(after):
        # Identical trees, different instructions to the linter, type checker,
        # formatter or coverage run. Those tools are part of the gate.
        return FileVerdict(path, False, "python",
                           "a tool-directive comment was added, removed, "
                           "reworded or moved to different code")
    changed_docs = set(doc_before) ^ set(doc_after)
    if any(_DOCTEST.search(text) for text in changed_docs):
        return FileVerdict(path, False, "python",
                           "a changed docstring contains a doctest prompt, "
                           "which a doctest runner would execute")
    return FileVerdict(path, True, "python",
                       "code identical once docstrings are removed; only "
                       "comments and docstrings changed")


def classify(
    repo: str,
    base: str,
    head: str = "HEAD",
    *,
    extra_floor: tuple[str, ...] = (),
) -> Verdict:
    """The tier for `base..head`, derived from the two trees.

    Never raises: anything that cannot be decided becomes the full tier with
    the cause as its reason, because the expensive path is the safe default
    and a classifier that crashes must not be able to block the gate either.
    """
    try:
        return _classify(repo, base, head, extra_floor)
    except Undecidable as exc:
        return Verdict(FULL, base, head, "", f"could not decide: {exc}")
    except Exception as exc:  # noqa: BLE001 — any failure is the full tier
        return Verdict(FULL, base, head, "",
                       f"could not decide: {type(exc).__name__}: {exc}")


def _classify(repo: str, base: str, head: str,
              extra_floor: tuple[str, ...]) -> Verdict:
    top = _git(repo, "rev-parse", "--show-toplevel").strip()
    if os.path.realpath(top) != os.path.realpath(repo):
        raise Undecidable(
            f"{repo} is not the top level of its repository ({top}); a "
            "listing from a subdirectory would leave changes outside it "
            "unexamined")
    mb = _git(repo, "merge-base", base, head).strip()
    rows = [row for row in _changed(repo, mb, head)
            if not is_round_evidence(row[3], row[0])]
    if not rows:
        # The lesson this repo already paid for once: a comparison over zero
        # files reports success while proving nothing. A range holding only
        # the round's own evidence lands here too, and takes the full path.
        raise Undecidable(
            f"no file to compare between {base} and {head} once the round's "
            "own evidence is set aside; a comparison over zero files proves "
            "nothing")
    # The caller's widening and the repo's own, both one-way. Derived here
    # rather than passed in, so no call site can reach the light tier by
    # forgetting to look the declaration up.
    floor = (*extra_floor, *declared_floor(repo, mb, head))
    verdicts = [_file_verdict(repo, mb, head, row, floor)
                for row in sorted(rows, key=lambda r: r[3])]
    blockers = [v for v in verdicts if not v.eligible]
    if blockers:
        first = blockers[0]
        return Verdict(
            FULL, base, head, mb,
            f"{len(blockers)} of {len(verdicts)} changed file(s) are not "
            f"provably inert, starting with {first.path}: {first.reason}",
            tuple(verdicts))
    return Verdict(
        LIGHT, base, head, mb,
        f"all {len(verdicts)} changed file(s) are provably inert; what the "
        "diff changes is comments and docstrings",
        tuple(verdicts))


def _changed(repo: str, mb: str, head: str) -> list[tuple[str, str, str, str]]:
    """(status, old_mode, new_mode, path) for every path differing between the
    merge base and head. Renames are not detected, so a rename reads as the
    delete and the add it is, and both are refused."""
    out = _git(repo, "diff", "--raw", "-z", "--no-renames", mb, head)
    fields = out.split("\0")
    rows = []
    for meta, path in zip(fields[0::2], fields[1::2]):
        if not meta or not path:
            continue
        try:
            old_mode, new_mode, _, _, status = meta.lstrip(":").split(" ")
        except ValueError as exc:
            raise Undecidable(
                f"could not read the diff listing entry {meta!r}: {exc}"
            ) from exc
        rows.append((status, old_mode, new_mode, path))
    return rows


def _file_verdict(repo: str, mb: str, head: str,
                  row: tuple[str, str, str, str],
                  extra_floor: tuple[str, ...]) -> FileVerdict:
    status, old_mode, new_mode, path = row
    floor = under_floor(path, extra_floor)
    if floor is not None:
        return FileVerdict(path, False, "machinery",
                           f"under {floor}, which is the gate's own machinery "
                           "or the config that drives it and is never light")
    if status != "M":
        # An added .py file is new code, a deletion removes whatever the file
        # did, and a type change swaps a file for a symlink or the reverse.
        # None of those is a comment change.
        return FileVerdict(path, False, "existence",
                           f"the file was added, deleted or type-changed "
                           f"(git status {status}), which changes what the "
                           "tree contains")
    suffix = os.path.splitext(path)[1].lower()
    if suffix == ".py":
        return _python_verdict(repo, mb, head, path, old_mode, new_mode)
    # Everything else, deliberately, Markdown included. A YAML file executes
    # nothing directly and a document is read rather than run, but either can
    # BE policy, and a document carries commands, links and raw HTML that no
    # reader short of a full parser tells from its sentences. An unproven
    # surface costs a full round rather than resting on a judgement nobody
    # checked.
    return FileVerdict(path, False, "unknown",
                       f"no proof exists for a {suffix or 'suffixless'} file, "
                       "so it takes the full round")
