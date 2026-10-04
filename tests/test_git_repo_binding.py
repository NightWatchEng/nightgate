"""No git subprocess in this repository may leave its repo to the ambient cwd.

THE DEFECT THIS REFUSES. `subprocess.run(["git", "add", "-A"])`
does not fail when it names no repository: git walks up from the calling
process's working directory and finds one. During a test run that is this
checkout; inside `warden` or `cage` it is whatever enrolled repository the
consumer happened to invoke from. The incident that opened the bead was an
index left holding a tree its HEAD did not have, which made every guard that
reads `git ls-files` report a tree nobody wrote — three unrelated reds on a
clean worktree, an hour to read, and `git reset` to fix.

WHY THIS IS SEPARATE FROM THE DETECTOR. `tests/conftest.py` watches this
checkout's index and fails the test it moved under; that is cure, and it can
only speak after the damage. This is prevention, and it is pure AST — no
subprocess, no fixture, nothing to run. The two are also separate FILES
because `REPO_FREE_VERBS` below is swept by `tests/test_guard_mutations.py`,
which deletes each alternative and re-runs this module: that has to stay cheap,
and `tests/test_index_isolation.py` drives nested pytest sessions.

THE EXEMPTION SET IS MINIMAL BY CONSTRUCTION. Every verb in `REPO_FREE_VERBS`
has a real call site in this tree that depends on it, so deleting any one of
them turns the scan below red. A verb nobody calls would be an alternative no
test pins — the exact shape `test_guard_mutations` exists to refuse — so the
set is not a list of "verbs that seem safe", it is the list the tree earns.
"""

import ast
import subprocess
from pathlib import Path

from conftest import REPO_ROOT

# Calls that need no repository AND can write no index, so they may omit both
# `cwd=` and `-C`. The key is the verb; the value is the ARGUMENT that makes
# that verb repo-free, and it is required — exempting on the verb alone was a
# review finding, because the bare forms
# are the dangerous ones:
#
#   `git config user.email t@t`   writes the AMBIENT checkout's .git/config
#   `git init`                    with no destination initialises the cwd
#   `git ls-remote`               with no URL reads the cwd's remotes
#
# Each entry names a live caller, and the qualifier each caller actually
# carries, so a member that stops being load-bearing is visible:
#   init       a destination path argument — tests/test_github.py,
#              tests/test_init_proof.py (`git init -q -b main <dest>`)
#   clone      a source and a destination — tests/test_certify.py's shallow
#              clone (`git clone -q -b main --depth 1 <uri> <dest>`)
#   config     --global / --system, which select a config layer rather than
#              the repository — tests/test_ambient_git.py (`--global --list`)
#   ls-remote  a URL — tests/test_init_proof.py's transport refusal
#
# Everything else — add, commit, checkout, read-tree, reset, status, ls-files,
# rev-parse — resolves from the caller's cwd, which is a real checkout.
REPO_FREE_VERBS: dict[str, tuple[str, ...]] = {
    "init": ("<path>",),
    "clone": ("<path>",),
    "config": ("--global", "--system"),
    "ls-remote": ("<path>",),
}


def _tracked_python() -> list[Path]:
    """Every `*.py` file this repository TRACKS, as absolute paths.

    From the index rather than a filesystem walk, for the same reason
    `conftest.tracked` is: a git worktree checked out under
    `.claude/worktrees/` is a full second copy of this tree, and a walk would
    report offenders in files nobody reviewed — failing locally while CI, a
    fresh clone, stayed green. Deriving the file set also means there is no
    hand-written list of directories to drift: a new package is scanned the
    day it is committed.
    """
    out = subprocess.run(["git", "ls-files", "-z", "--", "*.py"],
                         cwd=REPO_ROOT, capture_output=True, text=True,
                         check=True)
    return sorted(REPO_ROOT / rel for rel in out.stdout.split("\0") if rel)


def _subprocess_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """How THIS module spells subprocess: (module aliases, bare callables).

    Resolved per module rather than assumed, because `subprocess` is not the
    only spelling and the other two are live in this repo: `import subprocess
    as sp` appears at five sites in `tests/test_cli.py`, and `from subprocess
    import run` binds a bare name. A scan keyed on the literal attribute
    `subprocess.run` is silent on both — a review round raised
    this (fail-closed) as the rule's named scope-limited-derivation shape, with
    both spellings measured silent.
    `test_round_isolation._subprocess_uses` already reads imports this way;
    this is the same reading, not a new idea.
    """
    modules, bare = {"subprocess"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                bare.add(alias.asname or alias.name)
    return modules, bare


def repo_less_git_calls(tree: ast.AST, label: str) -> list[str]:
    """Every subprocess call on `["git", ...]` in `tree` that names no repo.

    Keyed on the argv EXPRESSION, and the residual is narrower than "literal
    or invisible". An argv that is not a list or tuple display at all —
    `run(cmd)`, `run(["git"] + args)` — is invisible here, deliberately and
    stated: the detector in `conftest.py` is what stands behind that. But a
    display whose ELEMENTS are partly runtime values is read, by two readers
    in order: `_global_options` exactly, and where that has to stop,
    `_best_effort_verb`. A first cut of that repair exempted the second case
    with this paragraph as its warrant, and the warrant did not reach:
    `["git", flag, "add", "-A"]` is a literal display, and going silent on it
    is the defect this module exists for.

    What is NOT a residual any more is the module spelling: `subprocess.run`,
    `sp.run` and a bare `run` from a from-import are all read.
    """
    modules, bare = _subprocess_names(tree)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if getattr(getattr(func, "value", None), "id", None) not in modules:
                continue
        elif isinstance(func, ast.Name):
            if func.id not in bare:
                continue
        else:
            continue
        argv = node.args[0]
        if not (isinstance(argv, (ast.List, ast.Tuple)) and argv.elts):
            continue
        head = argv.elts[0]
        if not (isinstance(head, ast.Constant) and head.value == "git"):
            continue
        if any(kw.arg == "cwd" for kw in node.keywords):
            continue
        if _binds_a_repo(node, argv):
            continue
        # The verb comes from the SAME option reader `_binds_a_repo` uses, so
        # the two cannot disagree about where the leading options end. The
        # first cut took the first non-option literal anywhere in argv, which
        # reads `git --work-tree <p> add -A` as the verb `<p>`.
        _, verb_at, readable = _global_options(argv)
        if not readable:
            # A runtime value sits in the LEADING OPTION RUN, so the exact
            # read cannot say where the options end. It does NOT follow that
            # the call is exempt. A first cut drew exactly that
            # conclusion and made `["git", flag, "add", "-A"]` silent — the
            # hunted shape with one variable in it — which review caught
            # (fail-closed): the scan has ONE output channel,
            # so "nothing to report" and "I dropped what I could not read"
            # were the same bytes. The BEST-EFFORT read looks for a VERB over
            # what is left, and that is ALL it looks for — `_binds_a_repo` has
            # already had the only say on bindings there is, and round 2 is
            # why this arm no longer has a second one (see that reader).
            verb_at = _best_effort_verb(argv)
        verb = argv.elts[verb_at].value if verb_at is not None else None
        if _is_repo_free(verb, argv, verb_at):
            continue
        source = ast.unparse(node)
        offenders.append(f"{label}:{node.lineno}: git {verb or '<no verb>'} "
                         f"— {source.splitlines()[0][:90]}")
    return offenders


# The leading global options that name the REPOSITORY git will use. Every
# member is pinned by a MUST_NOT_FIRE row below: drop one and a call that
# does name its repo is reported repo-less. `--work-tree` is absent by
# MEASUREMENT, not oversight — see `_binds_a_repo`.
_REPO_BINDING_GLOBALS = frozenset({"-C", "--git-dir"})

# Leading global options that are NOT bindings and take their value as the
# NEXT argv element. Reading the option run needs this list, and
# this is why it exists: without it the reader mistakes an option's ARGUMENT for the
# verb and stops there, so `git --work-tree <p> --git-dir <d> add -A` — a
# call that DOES name its repository — was reported repo-less the moment
# `--work-tree` stopped short-circuiting the loop (silent at
# e4ae0b5, firing at 1c8cc9f). `--opt=value` carries
# its own value and takes no successor, which is why the test below is on the
# split name AND the absence of `=`.
#
# DERIVED BY PROBE AGAINST THE GIT ON THIS MACHINE, because
# the previous cut was written from `git --help`'s synopsis and from memory
# and was wrong three ways at once. Every top-level option `git help git`
# documents was run TWICE — one probe is not enough, and round 1 of #281
# caught this
# header claiming it was (attribution-holds):
#
#   git <opt> <val> version    did `<val>` disappear?
#   git <opt> version          …or does this option print the version by
#                              ITSELF, which is the same output for the
#                              opposite reason?
#
# An option ate its successor iff probe 1 shows `<val>` CONSUMED AS THAT
# OPTION'S VALUE and probe 2 shows the option does not produce probe 1's
# answer by itself. Consumed shows up two ways and the sentence here claimed
# only the first, which round 2 caught (attribution-holds) on two of the seven
# members it introduces:
#
#   git ran the verb        `git --work-tree /tmp version` -> git version
#                           2.55.0. `<val>` left the command position, so
#                           `version` was the command.
#   git failed ON <val>     `git -C XVAL version` -> "fatal: cannot change to
#                           'XVAL': No such file or directory"; `git
#                           --config-env XVAL version` ->
#                           "fatal: invalid config format: XVAL". No version
#                           printed, and the error names `<val>` in the role
#                           of that option's value, which is the same answer.
#
# Probe 2 is what separates both from an option that TERMINATES: `git -v XVAL
# version` prints the version too, and under the one-probe rule this header
# used to state, `-v` would have been filed as value-taking. `git -v version`
# prints it as well, which is what gives it away. Measured, git 2.55.0. The
# four buckets below are TOTAL over the OPTIONS section of `git help git`,
# which documents 25 entries; the buckets list 29 spellings, because four of
# those entries pair a short and a long form (-v/--version, -h/--help,
# -p/--paginate, -P/--no-pager) and each spelling was probed on its own:
#
#   ate the successor  -C  -c  --git-dir  --work-tree  --namespace
#                      --attr-source  --config-env
#   did not            --bare  --paginate  -p  --no-pager  -P  --no-advice
#                      --no-lazy-fetch  --no-optional-locks
#                      --no-replace-objects  --literal-pathspecs
#                      --glob-pathspecs  --noglob-pathspecs
#                      --icase-pathspecs
#   never reach a verb --exec-path / --html-path / --man-path / --info-path
#                      print a path and exit; `--list-cmds` is attached-only
#                      ("unknown option" in the separated spelling)
#   terminate git      -v / --version print the version and stop; -h / --help
#                      turn the rest into a help topic (`git -h XVAL version`
#                      answers "No manual entry for gitXVAL"). Neither can
#                      precede a verb, so neither can move one.
#
# `-C` and `--git-dir` are the two that ate a successor AND name a repository,
# so they live in `_REPO_BINDING_GLOBALS` instead and `_binds_a_repo` short-
# circuits on them before any caller reads the verb index. The other five are
# this set.
#
# THE ROWS BELOW ARE SYNTHETIC, AND THAT IS THE HONEST WORD FOR THEM. The cut
# this replaces called the set "minimal the way `REPO_FREE_VERBS` is", which
# is circular: a `REPO_FREE_VERBS` member is earned by a REAL call site in
# this tree, whereas each member here is pinned by a MUST_NOT_FIRE row the
# same commit wrote — so any member at all could be made "minimal by
# construction" that way, `--super-prefix` being the proof. What earns a
# member here is git's own parser, which is why the probe is written down and
# the claim is COMPLETENESS against git 2.55.0 rather than minimality. The
# rows still pay for themselves: they keep a member from being dropped in
# silence, which is a smaller claim than the one they used to carry.
#
# The three corrections that probe forced:
#   --config-env   the old comment EXCLUDED it, saying the value is "attached,
#                  never a successor". False: `FOO=xyz git --config-env
#                  user.name=FOO config user.name` prints `xyz`, so the
#                  separated form exists and eats the next element.
#   --attr-source  was in neither the set nor the exclusions — forgotten, not
#                  deliberate, which is what refutes the old comment's claim
#                  that the exclusions were measured. `git --attr-source
#                  rev-parse` prints the usage banner because `rev-parse` was
#                  swallowed as its value. It is absent from `git --help`'s
#                  synopsis, which is exactly how it was missed and why the
#                  synopsis is not the source.
#   --super-prefix REMOVED. git 2.55 answers "unknown option: --super-prefix"
#                  — it is not an option any call can use, and it read load-
#                  bearing only because a synthetic row pinned it.
#
# `--exec-path` keeps a sentence because its exclusion was the half that was
# always right: `--exec-path=<path>` carries its value attached, and the BARE
# form prints the exec path and exits without ever reaching a verb.
_VALUE_TAKING_NON_BINDINGS = frozenset({
    "-c", "--attr-source", "--config-env", "--namespace", "--work-tree",
})


def _global_options(
        argv: ast.List | ast.Tuple) -> tuple[list[str], int | None, bool]:
    """The leading global options in `argv`, where the verb starts, and
    whether the read got that far.

    ONE reader, two callers — `_binds_a_repo` and the verb the scan reports —
    because they were two readers with two different ideas of where the
    options end, and that disagreement was a review finding.
    Returns the
    option NAMES (an `--opt=value` reduced to `--opt`), the INDEX of the verb,
    and READABLE.

    THE TWO WAYS THERE IS NO VERB INDEX ARE DIFFERENT QUESTIONS, and conflating
    them was a defect of an earlier cut of this reader:

      readable=True, verb_at=None   argv is options and nothing else. Every
                                    element was read; there simply is no verb.
                                    A decided answer, and callers may act on it.
      readable=False, verb_at=None  the run stopped at a NON-LITERAL element —
                                    a Name, an f-string, a `*spread`. Where
                                    the options end is unknowable from source,
                                    so is the verb, and so is whether a
                                    binding follows. NOT DECIDED, and callers
                                    must not act on it.

    The cut this replaces returned one `None` for both and the scan refused
    the second, reporting the verb as the literal string "None" on three
    correct shapes (`["git", flag, "init", str(dest)]` among them). A non-
    literal the reader SKIPS OVER as a known option's value is not a stop:
    `["git", "-c", cfg, "init", str(dest)]` reads through to `init`, which is
    why the defect was precisely the option the reader does not recognise.
    """
    names: list[str] = []
    i = 1
    while i < len(argv.elts):
        element = argv.elts[i]
        if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
            return names, None, False   # not decided — see the docstring
        value = element.value
        if not value.startswith("-"):
            return names, i, True       # the verb
        name = value.split("=", 1)[0]
        names.append(name)
        i += 1
        if name in _VALUE_TAKING_NON_BINDINGS and "=" not in value:
            i += 1                      # its value is the next element
    return names, None, True            # all options, no verb: decided


def _best_effort_verb(argv: ast.List | ast.Tuple) -> int | None:
    """ONE answer, over an argv the exact reader had to stop reading: the index
    of a candidate verb, or `None`.

    Only reached when `_global_options` returned `readable=False`, and it
    exists because "I cannot read this precisely" is not "this is fine". It
    walks the leading run SKIPPING every element it cannot resolve instead of
    stopping at one, honours `_VALUE_TAKING_NON_BINDINGS` exactly as the exact
    reader does, and calls the first literal that does not start with `-` the
    verb. That verb then refuses the call unless it is repo-free WITH its
    qualifier, which is what keeps the runtime-flag hole closed: `["git", flag,
    "init", str(dest)]` and `["git", *flags, "clone", uri, dest]` read through
    to a repo-free verb carrying its destination, while `["git", flag, "add",
    "-A"]` reads through to `add` and fires.

    IT ANSWERS NOTHING ABOUT BINDINGS, and the first cut of this reader did —
    that is round 2's fail-closed finding and the reason for the narrowing. It
    returned the option names it had collected and the caller exempted on a
    `-C` or `--git-dir` among them. But every name here sits AFTER something
    unresolvable, so the unresolvable element may itself have been the VERB,
    which makes those names the verb's own flags: `["git", verb, "-C",
    "HEAD"]` went silent on a `-C` that means detect-copies, as this module's
    own MUST_FIRE row for `git diff -C HEAD` says in as many words. Reading
    `-C` off a position nothing had established is the two-readers-disagree
    defect, and it came back one reader over. `_binds_a_repo` reads bindings
    POSITIONALLY and has already had the only say there is; this reader adds
    none.

    THE TWO RESIDUALS, both false POSITIVES now, which is the direction this
    module chooses when it must choose. A call whose repo binding is only
    visible after an unresolvable element — `["git", flag, "--git-dir", d,
    "add", "-A"]` — is refused, and so is one whose binding is itself built at
    runtime. No tracked call has either shape (measured: every tracked git
    argv with an unresolvable option run is already exempt by `cwd=` or by a
    binding the exact reader reached), the failure message names `cwd=` as the
    remedy, and refusing a rare correct call beats going silent on the defect
    this module exists for.
    """
    i = 1
    while i < len(argv.elts):
        element = argv.elts[i]
        if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
            i += 1
            continue                    # unresolvable: skip, never stop
        value = element.value
        if not value.startswith("-"):
            return i                    # the candidate verb
        name = value.split("=", 1)[0]
        i += 1
        if name in _VALUE_TAKING_NON_BINDINGS and "=" not in value:
            i += 1                      # its value is the next element
    return None


def _binds_a_repo(node: ast.Call, argv: ast.List | ast.Tuple) -> bool:
    """Does this call name the repository git should use?

    THREE SPELLINGS, and all three are read POSITIONALLY rather than by
    grepping the unparsed call — which is what the first cut did, and it was
    wrong in both directions. `-C` as a
    substring of
    the whole call exempted `git diff -C` and `git blame -C`, where `-C`
    means detect-copies and binds nothing; and it missed `--git-dir` and
    `env={"GIT_DIR": ...}`, so calls that DO name their repo were reported as
    offenders.

      git -C <path> …            a leading global option, before the verb
      git --git-dir=<path> …     likewise
      env={"GIT_DIR": …}         the environment binding

    Four things exempt a call here, counting the `cwd=` its caller checks
    first, and `conftest`'s failure message names three of the four: "no
    `-C`, no `cwd=`, no GIT_DIR". `--git-dir` is the one it does not name —
    stated because the sentence this replaces claimed the two vocabularies
    were identical, and they differ by one member in each direction.

    THE ENV READ IS OVER DICT KEYS, never the whole expression. `ast.walk` of
    the `env=` value matched the string "GIT_DIR" wherever it appeared, so
    `env={"GIT_WORK_TREE": os.environ["GIT_DIR"]}` — a retarget with a
    binding-shaped VALUE — was exempt, as was any scrubbing comprehension
    naming the variable it strips, `runs.git_env()`'s own idiom among them.
    An `env=` that is not a literal dict is not
    decidable here, so it exempts nothing: fail-closed, and the `**spread`
    entry a literal dict may carry is a `None` key that simply does not
    match.

    WHAT IS NOT A BINDING, and it is a shorter list than it looks (agentops-
    yvay). `GIT_WORK_TREE` and `GIT_INDEX_FILE` are two of the nine variables
    `warden/runs.py`'s `_GIT_ENV_OVERRIDES` strips, `--work-tree` is the
    command-line spelling of the first (and appears nowhere under `warden/`),
    and the first cut of this function read that as "the bindings". It is
    not: `_GIT_ENV_OVERRIDES` is the list of variables that RETARGET a git
    call, which is a superset — a variable can move where git writes without
    moving which repository git found. Measured on throwaway repos, `git add
    -A` issued from an ambient checkout:

      --work-tree=<elsewhere>      ambient index ['a.txt'] -> ['e.txt',
      --work-tree <elsewhere>        'planted.txt'] in all three cases: git
      env GIT_WORK_TREE=<elsewhere>  still walks up from the cwd for the
                                     repository and stages the OTHER tree
                                     into THIS index — the incident shape
                                     exactly, an index holding a tree its
                                     HEAD never had
      env GIT_INDEX_FILE=<tmp>     ambient index untouched, but the `git
                                     commit` after it moved the ambient HEAD
                                     (37f65160 -> 1844c60b) — the other half
                                     of the pre-push incident

    So none of the three may exempt a call. `GIT_INDEX_FILE` is the near miss
    worth stating rather than quietly dropping: it does protect the index, it
    is what `conftest.index_entries` passes, and `conftest`'s failure message
    still offers it as a remedy for an index move. It is not a remedy for
    this scan's subject, which is the repository a call resolves to, and it
    had no call site here that omitted `cwd=` anyway.
    """
    options, _, _ = _global_options(argv)
    if any(name in _REPO_BINDING_GLOBALS for name in options):
        return True
    for keyword in node.keywords:
        if keyword.arg != "env":
            continue
        if not isinstance(keyword.value, ast.Dict):
            continue                    # not decidable: exempt nothing
        for key in keyword.value.keys:
            if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                    and key.value == "GIT_DIR"):
                return True
    return False


def _is_repo_free(verb: str | None, argv: ast.List | ast.Tuple,
                  verb_at: int | None) -> bool:
    """Is this one of the calls that needs no repository — WITH the argument
    that makes it so?

    The verb alone is not enough: `git config user.email t@t` writes the
    ambient checkout's `.git/config`, and a bare `git init` or `git ls-remote`
    reads or writes the cwd. So each verb declares what must accompany it, and
    `<path>` means "some argument that is not an option" — a destination or a
    URL. A qualifier that is built at runtime (`str(dest)`) is not a literal,
    so it counts as the path it is.

    The qualifier is looked for AFTER the verb, never across the whole argv,
    and the narrowing came with `_global_options`: a leading option's own
    argument is a non-option literal too, so `git --work-tree <p> init` would
    otherwise read `<p>` as the destination that makes `init` repo-free and
    exempt a call that initialises the ambient cwd.

    `verb_at is None` reaches here only in the DECIDED sense — an argv that is
    leading options and nothing else, so there is no verb and no verb can be
    repo-free. The undecided sense never arrives: `repo_less_git_calls` drops
    the call before this is called. That split is why this
    arm can refuse without refusing correct code.
    """
    required = REPO_FREE_VERBS.get(verb or "")
    if required is None or verb_at is None:
        return False
    rest = list(argv.elts[verb_at + 1:])
    if required == ("<path>",):
        for element in rest:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                if element.value.startswith("-") or element.value == verb:
                    continue
                return True             # a literal destination or URL
            elif not isinstance(element, ast.Constant):
                return True             # str(dest), f"...", a Name: the path
        return False
    return any(isinstance(e, ast.Constant) and isinstance(e.value, str)
               and e.value.split("=")[0] in required for e in rest)


def test_no_git_subprocess_leaves_its_repository_to_the_ambient_cwd():
    """Over every tracked `*.py` file, platform and suite alike.

    Not scoped to `tests/`: `warden` and `cage` shell out to git constantly,
    and a repo-less call there carries the defect into every enrolled
    consumer, where the ambient cwd is somebody else's repository and the
    index it would move is not ours to move. Both are clean today; this is
    what keeps them clean.

    Add `subprocess.run(["git", "add", "-A"])` anywhere tracked and this goes
    red naming the file and line. Delete any member of `REPO_FREE_VERBS` and
    it goes red too, on the real call site that member exempts — which is
    what makes the exemption set load-bearing rather than decorative.
    """
    files = _tracked_python()
    assert len(files) > 50, (
        f"only {len(files)} tracked python files found, so this scan has "
        "almost nothing to say — the derivation is broken, not the tree")
    offenders = []
    for path in files:
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue          # a deliberately malformed fixture, not a caller
        offenders += repo_less_git_calls(tree, str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "these git calls name no repository, so they resolve from whatever "
        "directory the process happens to be in:\n  "
        + "\n  ".join(offenders)
        + "\n\nPass `cwd=` (a tmp_path repo in a test, the enrolled root in "
          "warden/cage) or `-C <path>`. If the command genuinely needs no "
          "repository and can write no index, add its verb to "
          "REPO_FREE_VERBS with the caller that earns it.")


# Calls the scan MUST fire on, each with what makes it dangerous. Every row
# was measured silent by review except the first, which is the shape the
# guard was written for.
MUST_FIRE = {
    'subprocess.run(["git", "add", "-A"])':
        "the hunted shape: no repository named at all",
    'import subprocess as sp\nsp.run(["git", "add", "-A"])':
        "an aliased module import — live at five sites in tests/test_cli.py",
    'from subprocess import run\nrun(["git", "add", "-A"])':
        "a from-import binding a bare callable",
    'subprocess.run(["git", "config", "user.email", "t@t"])':
        "`config` without --global/--system writes the AMBIENT .git/config",
    'subprocess.run(["git", "init"])':
        "`init` with no destination initialises the current directory",
    'subprocess.run(["git", "ls-remote"])':
        "`ls-remote` with no URL reads the current repo's remotes",
    'subprocess.run(["git", "diff", "-C", "HEAD"])':
        "`-C` here is detect-copies and binds no repository",
    # The four retargeting spellings that are NOT bindings.
    # Each was exempt between #267 and this commit, and the pre-#267 scan
    # flagged all of them — a hole the repair opened, which is worse than a
    # missing guard because it manufactures confidence. One row per spelling,
    # because a single row would let two of the three quietly come back.
    'subprocess.run(["git", "--work-tree=/tmp/wt", "add", "-A"])':
        "`--work-tree` names a TREE, not a repository: measured, this left "
        "the AMBIENT checkout's index holding the OTHER tree "
        "(['a.txt'] -> ['e.txt', 'planted.txt'])",
    'subprocess.run(["git", "--work-tree", wt, "add", "-A"])':
        "the separated spelling of the same flag, measured the same way",
    'subprocess.run(["git", "add", "-A"], env={"GIT_WORK_TREE": wt})':
        "the environment spelling, measured the same way — warden/runs.py "
        "strips it for exactly this reason",
    'subprocess.run(["git", "add", "-A"], env={"GIT_INDEX_FILE": idx})':
        "`GIT_INDEX_FILE` redirects the INDEX, not the repository: `add` is "
        "harmless but the `git commit` after it moved the ambient HEAD "
        "(measured 37f65160 -> 1844c60b), which is the half of the pre-push "
        "incident that moved a branch ref",
    # The env read walked the WHOLE
    # `env=` expression, so any call MENTIONING "GIT_DIR" anywhere was
    # exempt. All three were measured silent at 1c8cc9f and fire now.
    'subprocess.run(["git", "add", "-A"], env={"GIT_WORK_TREE": os.environ["GIT_DIR"]})':
        "a retarget whose VALUE is binding-shaped — the key is what decides",
    'subprocess.run(["git", "add", "-A"], '
    'env={k: v for k, v in os.environ.items() if k != "GIT_DIR"})':
        "a scrubbing comprehension naming the variable it STRIPS — the "
        "`runs.git_env()` idiom, which binds nothing",
    'subprocess.run(["git", "add", "-A"], env={"NOTE": "GIT_DIR"})':
        "the variable named in a VALUE, binding nothing at all",
    # The leading option's argument is a non-option literal, so reading the
    # qualifier across the whole argv exempted a bare `init` (see
    # `_is_repo_free`).
    'subprocess.run(["git", "--work-tree", wt, "init"])':
        "`init` with no destination: `<wt>` is --work-tree's argument, not "
        "the destination that would make the verb repo-free",
    # A runtime value AFTER the verb decides nothing — the
    # reader has already found the verb — so the call is read and refused
    # exactly as a literal one would be. This row has an EMPTY option run, so
    # it never reaches the best-effort reader at all; it pins the qualifier
    # read, and the nine rows below are what pin that reader.
    'subprocess.run(["git", "add", path])':
        "a runtime value after a verb the reader already found: still the "
        "hunted shape, and no exemption may reach it",
    # Fail-closed: the FIRST cut of that fix exempted
    # every argv whose leading option run held a non-literal. That made the
    # hunted shape silent one variable from any call site, and since the scan
    # has one output channel, a green run and a dropped call were the same
    # bytes. One row per route to the best-effort reader, because a Name, a
    # star-unpack and an f-string reach it differently. All five were SILENT
    # at 1e1c062 and fire now.
    'subprocess.run(["git", flag, "add", "-A"])':
        "the hunted shape with one runtime flag in front of it — `add` is "
        "never repo-free, whatever `flag` turns out to be",
    'subprocess.run(["git", opt, "commit", "-m", "x"])':
        "the half of the pre-push incident that moved a branch ref, behind a "
        "runtime option",
    'subprocess.run(["git", *flags, "add", "-A"])':
        "the star-unpacked spelling, whose `ast.Starred` is not a Constant",
    'subprocess.run(["git", f"-c{k}", "add", "-A"])':
        "an f-string option: a JoinedStr the reader cannot resolve either, "
        "and skipping it still reaches `add`",
    'subprocess.run(["git", flag, "--work-tree", wt, "init"])':
        "`--work-tree` is not a binding, so reading past a runtime flag to a "
        "destination-less `init` must still refuse it",
    # Fail-closed: the first cut of the best-effort
    # reader ALSO returned the option names it collected, and the caller
    # exempted on a `-C`/`--git-dir` among them. Every such name sits after
    # something unresolvable, which may have been the verb — so these read a
    # VERB'S OWN FLAG as a repo binding, and all three were silent at 6276cef
    # while firing at origin/main. The reader answers nothing about bindings
    # now; `_binds_a_repo` is the only thing that does.
    'subprocess.run(["git", verb, "-C", "HEAD"])':
        "`-C` after a runtime verb is detect-copies — the same `git diff -C` "
        "shape above, with the verb not spelled out",
    'subprocess.run(["git", "-c", cfg, verb, "-C", "HEAD"])':
        "the same, reached through a value-taking option the exact reader "
        "could follow before it stopped",
    'subprocess.run(["git", sub, "--git-dir", "x"])':
        "a binding-shaped token at a position nothing established",
    'subprocess.run(["git", flag, "--git-dir", d, "add", "-A"])':
        "the residual this costs, stated as a row rather than a sentence: a "
        "call that DOES name its repo, where the name is only visible past a "
        "runtime value — refused, and `cwd=` is the remedy the message names",
}

# Calls the scan must stay SILENT on, each with what makes it safe.
#
# NO ROW IS IDENTIFIED BY POSITION HERE. The header used to
# read "the last three are the false positives round 1 measured", and the
# repair that appended to the table left it describing three rows it had
# never seen — a positional reference into an append-only table goes stale
# the next time anyone appends. Each row's own note says what it is and which
# round measured it, so the table can grow without any sentence going wrong.
# Three provenances are mixed in here, all of them named per row: shapes an
# earlier round measured as live false positives, false positives a LATER
# round's repair introduced and this one closed, and synthetic pins that earn
# nothing on their own beyond keeping a vocabulary member from being dropped
# in silence.
#
# A PIN ROW'S VALUE ELEMENT IS A LITERAL — the element belonging to the
# member the row pins, and only that one. Load-bearing, not style, and stated
# narrowly because the sentence here first claimed the whole leading option
# run was literal, which was false of nine of the seventeen rows below and
# named an enforcement that does not exist (round 1 of #281,
# enforcement-truth). What
# is true: a row fires when its member is deleted, and with the member gone
# the reader walks INTO the value that member used to skip. A literal there
# is read as the verb and the row fires; a Name there sends the exact reader
# to the best-effort one, which may reach a different answer. Nothing asserts
# this property directly — what catches a violation is the sweep in
# `test_guard_mutations.py` going red on "alternative no test pins", one
# level out and after the fact. The rows whose option run still ends
# `--git-dir, d` keep that Name deliberately: it sits AFTER the pinned
# member's own value, so no deletion of that member ever walks into it, and
# it is the spelling a real caller writes.
MUST_NOT_FIRE = {
    'subprocess.run(["git", "add", "-A"], cwd=tmp)': "cwd names the repo",
    'subprocess.run(["git", "-C", "/tmp/r", "add", "-A"])': "-C names the repo",
    'subprocess.run(["git", "--git-dir", "/tmp/r/.git", "add", "-A"])':
        "--git-dir names the repo",
    'subprocess.run(["git", "add", "-A"], env={"GIT_DIR": d})':
        "GIT_DIR names the repo — one of the three bindings conftest names",
    'subprocess.run(["git", "init", "-q", "-b", "main", str(dest)])':
        "init with an explicit destination",
    'subprocess.run(["git", "config", "--global", "--list"])':
        "config selecting a layer, not a repository",
    'subprocess.run(["git", "ls-remote", "--tags", url])':
        "ls-remote against a URL",
    # The false positive the first cut
    # of that fix INTRODUCED. Dropping --work-tree from the early return let
    # the option reader break on the flag's separated argument, so a call
    # that does name its repo, one element later, was reported repo-less.
    # Silent at e4ae0b5, fired at 1c8cc9f, silent again here.
    'subprocess.run(["git", "--work-tree", "/tmp/wt", "--git-dir", '
    '"/tmp/r/.git", "add", "-A"])':
        "the canonical detached-worktree pair — --git-dir names the repo, "
        "and --work-tree's argument is not the verb",
    'subprocess.run(["git", "-c", "core.hooksPath=x", "--git-dir", d, "add", "-A"])':
        "`-c <name=value>` likewise takes the next element; this one was a "
        "false positive at e4ae0b5 too, so the repair closed it in both",
    # One row per remaining member of `_VALUE_TAKING_NON_BINDINGS`. SYNTHETIC
    # — see that set's comment: what earns a member is git's parser, measured;
    # these rows only keep a member from leaving in silence.
    'subprocess.run(["git", "--namespace", "ns", "--git-dir", d, "add", "-A"])':
        "`--namespace <name>` takes the next element (git 2.55: probed)",
    # Two members the previous cut got wrong against real git.
    'subprocess.run(["git", "--attr-source", "HEAD", "--git-dir", d, '
    '"add", "-A"])':
        "`--attr-source <tree-ish>` takes the next element — absent from the "
        "old set AND from its exclusions, so this call fired although "
        "--git-dir names the repo one element later",
    'subprocess.run(["git", "--config-env", "u.n=E", "--git-dir", d, '
    '"add", "-A"])':
        "`--config-env <name>=<envvar>` takes the next element — the old "
        "comment excluded it claiming the value is always attached, which "
        "`FOO=xyz git --config-env user.name=FOO config user.name` refutes",
    # A runtime value in the LEADING OPTION RUN, on a call that
    # is CORRECT. All three were silent at e4ae0b5 and at 1c8cc9f and fired at
    # 5db9899 with the verb printed as "None" — the first two name their
    # destination and the third selects a config layer. They stay silent now
    # by being READ, not by being skipped: the best-effort reader walks past
    # the value it cannot resolve and reaches a repo-free verb carrying its
    # qualifier. That is the same read that makes the MUST_FIRE siblings
    # above fire, which is why one arm cannot be loosened without the other
    # going wrong. One row per spelling, because `ast.Starred` and a bare Name
    # reach the reader by different routes.
    'subprocess.run(["git", flag, "init", str(dest)])':
        "a conditional flag before the verb, and the call does carry the "
        "destination that makes `init` repo-free",
    'subprocess.run(["git", *flags, "clone", uri, dest])':
        "a star-unpacked flag list — the ordinary way to write conditional "
        "flags, and `ast.Starred` is not an `ast.Constant`",
    'subprocess.run(["git", opt, "config", "--global", "--list"])':
        "a runtime option before `config --global`, which selects a layer "
        "rather than a repository",
    # The best-effort reader did not know
    # `_VALUE_TAKING_NON_BINDINGS`, so it took `-c`'s literal argument for the
    # verb and refused a `clone` that carries its destination, naming the verb
    # `protocol.file.allow=always`. That is the two-readers-disagree shape
    # review found between the ORIGINAL pair, one reader over.
    # Only the LITERAL-value spelling misfired — with a runtime value the skip
    # path walked past it anyway — which is why no earlier row caught it.
    'subprocess.run(["git", flag, "-c", "protocol.file.allow=always", '
    '"clone", uri, dest])':
        "`-c <name=value>` takes the next element in BOTH readers now, so "
        "the verb is `clone` and its destination is there",
    'subprocess.run(["git", *flags, "--namespace", "ns", "clone", uri, dest])':
        "the same disagreement on a long value-taking option",
}


def test_the_scan_sees_every_shape_it_exists_for():
    """Both directions, over synthetic modules, one row per measured case.

    Synthetic because the tree is clean: a scan asserted only against a clean
    tree passes just as well when the reader has stopped reading.
    Review found five silent dangerous shapes and two false
    positives with exactly this kind of probe, which is why each is a row
    here rather than a sentence in a docstring.
    """
    silent = [f"{src!r} ({why})" for src, why in MUST_FIRE.items()
              if not repo_less_git_calls(ast.parse(src), "synthetic")]
    assert not silent, (
        "the scan is SILENT on calls that name no repository:\n  "
        + "\n  ".join(silent))
    noisy = [f"{src!r} ({why})" for src, why in MUST_NOT_FIRE.items()
             if repo_less_git_calls(ast.parse(src), "synthetic")]
    assert not noisy, (
        "the scan FIRES on calls that do name their repository, so it would "
        "refuse correct code:\n  " + "\n  ".join(noisy))
