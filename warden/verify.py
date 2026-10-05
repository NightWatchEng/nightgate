"""warden verify: run the deterministic gates declared in repo.yaml.

Thin orchestrator — the commands live in repo.yaml's `verify` map, never here.
Emits verify-result.json (schema-validated) into an evidence run dir. Always
fail-closed: any command failing fails the scope.
"""

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator, ValidationError

from . import audit as audit_mod
from . import github as github_mod
from . import rules as rules_mod
from . import runs
from .config import RepoConfig, VerifyStep

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "verify-result.schema.json"
_TAIL_CHARS = 4000


class VerifyError(Exception):
    pass


#: The status a POSIX shell returns for "command not found", and the one it
#: returns for "found but not executable". A step exiting either most often
#: judged nothing — but both are ordinary statuses a program may return on its
#: own, so `diagnose` reports them as what the shell said, never as a fact
#: about the tree (round 1 F4, round 2 R2F2).
NOT_FOUND = 127
NOT_EXECUTABLE = 126


# Words that lead a step without BEING the program it runs. A step starting
# with one of these either runs its program later (`cd x && go test`, `env FOO=1
# go vet`) or runs no external program at all (`exit 3`, `:`). Naming the first
# token there would report `exit` as an absent binary, which is a confident
# wrong answer — strictly worse than the bare 127 this replaces.
#
# The list is deliberately long, and it includes shell builtins that HAPPEN to
# exist as files on some systems and not others (`ulimit`, `type`, `command`,
# `hash`): `ulimit -n 2048 && go test ./...` would resolve on macOS, where
# /usr/bin/ulimit exists, and not on ubuntu-latest, where it does not — so the
# same step would be diagnosed differently on two runners (round 1, F5).
_NOT_THE_PROGRAM = frozenset({
    "cd", "exit", "exec", "env", "eval", "source", ".", ":", "set", "unset",
    "export", "shift", "return", "trap", "read", "local", "if", "then", "else",
    "elif", "fi", "for", "while", "until", "do", "done", "case", "esac",
    "function", "time", "!", "{", "}", "(", ")", "sudo", "nohup", "xargs",
    "pushd", "popd", "ulimit", "hash", "type", "command", "alias", "let",
    "declare", "readonly", "jobs", "wait", "umask", "builtin", "getopts",
})


def first_binary(command: str) -> str | None:
    """The bare program name a verify step invokes, past any `VAR=value` prefix.

    `None` whenever the answer is not a bare name this function can stand
    behind, because the only question the caller asks of it is "does this
    resolve on PATH", and that question is MEANINGLESS for anything else:

    - an empty step, a shell construct (`a | b`, `$(x)`, a glob);
    - a leading word from `_NOT_THE_PROGRAM`;
    - A NAME CARRYING A PATH SEPARATOR — `./gradlew`, `sub/x`, `bin/rails`,
      `~/go/bin/golangci-lint`, `/usr/local/go/bin/go`. PATH is not consulted
      for these: the shell resolves them against the step's OWN `cwd`, which
      this function is not given and the reporting process does not share. The
      round-1 reviewers found the cost of guessing anyway, independently and
      with live repros: `{run: "./check.sh", cwd: "sub"}` exiting 1 with a real
      test failure was reported as `./check.sh is not on PATH — install the
      toolchain`, printed ABOVE the real output. Worse, for a root-relative
      step the answer depended on the directory the OPERATOR invoked `warden`
      from, so the same step at the same exit code produced two different
      evidence artifacts — non-determinism in gate evidence, in a gate whose
      first principle is provenance. `./gradlew`, `./scripts/ci.sh` and
      `./node_modules/.bin/jest` are ordinary consumer verify steps and `cwd:`
      is a shipped repo.yaml field, so this was live, not theoretical;
    - a prefix that assigns PATH itself (`PATH=/opt/bin:$PATH tool`). The step
      chose a search path this process cannot see, so its own PATH answers a
      different question than the one asked;
    - A MULTI-LINE STEP. `run: |` is a plain string to the schema and consumers
      mirror CI's block scalar, so `sh -c` runs EVERY line while a `split()`
      over the text sees only the first word of the first one. Round 2 (R2F1)
      found the round-1 HIGH still alive in exactly that shape: a first line
      naming an absent optional tool put "install the toolchain" above a later
      line's real test failure. One line's program is not the step's program.

    What is lost is small and the 127 arm carries it: when one of these really
    IS absent the shell still answers 127, and `diagnose` still says so —
    without naming a binary it cannot check.
    """
    if "\n" in command.strip():
        return None           # every line runs; one line's program is not it
    for token in command.split():
        head = token.split("/")[0]
        if "=" in head and not token.startswith("-"):
            # An environment assignment. If it sets PATH, the step searches a
            # path this process does not have, and no answer here is sound.
            if head.split("=")[0] == "PATH":
                return None
            continue
        if any(c in token for c in "|&;<>()$`\"'*?[]{}\\"):
            return None       # a shell construct or a glob, not a program name
        if "/" in token or token.startswith("~"):
            return None       # resolved against the step's cwd, not PATH
        if token in _NOT_THE_PROGRAM:
            return None
        return token
    return None


def diagnose(command: str, exit_code: int, *, path: str | None = None) -> str | None:
    """Why a step failed, when the reason is "the toolchain is not here".

    A consumer whose machine lacks the toolchain a verify scope
    needs used to get a bare `exit 127 / go: command not found` buried in a
    step's output tail and a red verdict that stated no cause — the same
    undiagnosed failure that cost this repo a 27-minute unattended run before
    anyone read the log closely enough.

    This DIAGNOSES without EXCUSING. The step still failed and the scope is
    still red: "report rather than fail" was considered and refused,
    because a scope that cannot run its commands proved nothing and a green
    there is vacuous. What changes is that the verdict says which binary is
    absent and what PATH it looked on, instead of leaving the reader to infer
    it from a shell message.

    Two arms, because neither alone is enough:
      - the step's first binary is a bare name that does not resolve on PATH,
        which catches a wrapper that swallows the status and exits something
        else;
      - the step exited 127, the shell's own "command not found", which
        catches every command `first_binary` declines to guess at.

    NEITHER ARM IS INFALLIBLE and the wording says so rather than claiming
    otherwise (round 1, F4): 127 is an ordinary exit status a program may
    return on its own (a `make` recipe propagating one, a script's own `exit
    127`). So the 127 arm is phrased as what is KNOWN — the shell reported
    command-not-found — and offers installing a toolchain as the likely cause,
    never as a diagnosis it cannot support. The first arm is the confident one,
    and `first_binary` is the function that keeps it confident by declining
    every name whose resolution it cannot actually check.
    """
    if exit_code == 0:
        return None
    binary = first_binary(command)
    search = os.environ.get("PATH", "") if path is None else path
    if binary:
        if shutil.which(binary, path=search) is None:
            # `shutil.which` demands X_OK, so it answers "absent" for a file
            # that is present without the execute bit. Distinguish them: round
            # 2 (R2F3) had this report "not on PATH" about a file sitting right
            # there, which sends the reader to install something they have.
            found = _on_path_unexecutable(binary, search)
            named = (f"`{binary}` is on PATH at {found} but is not executable"
                     if found else f"`{binary}` is not on PATH")
            return (f"{named}, so this step could not be evaluated. The scope "
                    f"is FAIL because a command that never ran proved nothing "
                    f"— not because the tree is bad. Fix the toolchain, or run "
                    f"where it is present. PATH={search}")
        if exit_code in (NOT_FOUND, NOT_EXECUTABLE):
            # The step's own program resolved, so whatever the shell could not
            # run is something else the command reached — or the program
            # returned this status itself. Both are possible and neither is
            # assertable, so say that rather than inventing a part.
            return (f"the step exited {exit_code}, which is what a shell "
                    f"returns for `command not found` ({NOT_FOUND}) or `not "
                    f"executable` ({NOT_EXECUTABLE}) — but `{binary}` itself "
                    f"resolves, so either something else the step reached did "
                    f"not run, or `{binary}` returned {exit_code} of its own "
                    f"accord. PATH={search}")
        return None
    if exit_code in (NOT_FOUND, NOT_EXECUTABLE):
        # Nothing to name — `first_binary` declined this command — so report
        # only what the shell said.
        return (f"the step exited {exit_code}, which is what a shell returns "
                f"for `command not found` ({NOT_FOUND}) or `not executable` "
                f"({NOT_EXECUTABLE}). This command is not a bare invocation, "
                f"so which part is unavailable is not something this can name "
                f"without guessing; an absent toolchain is the usual cause. "
                f"PATH={search}")
    return None


def _on_path_unexecutable(binary: str, search: str) -> str | None:
    """Where `binary` sits on `search` as a non-executable file, if it does."""
    for directory in search.split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / binary
        if candidate.is_file() and not os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _norm(path: str) -> PurePosixPath | None:
    """A repo-relative directory as a comparable path, or None when it makes
    no narrowness claim this function can read — the repo root, an absolute
    path, or one climbing out through `..`."""
    p = PurePosixPath(path or ".")
    if p == PurePosixPath(".") or p.is_absolute() or ".." in p.parts:
        return None
    return p


def _covers(directory: PurePosixPath, path: PurePosixPath) -> bool:
    """Segment-wise containment, never a string prefix: `examples-old/x` is
    not inside `examples`, and crediting it would name a scope that never ran
    a command over that path."""
    return path == directory or directory in path.parents


DECLARED = "declared"
INFERRED_NARROW = "inferred-narrow"
INFERRED_WIDE = "inferred-wide"


def covers_globs(step: VerifyStep) -> tuple[str, ...]:
    """A step's declared `covers:` globs, with a directory spelling normalized.

    `covers: ["backend/"]` is the spelling repo.yaml uses everywhere else for an
    AREA — `components:` paths and `protected_paths` are written with the
    trailing slash — and under `rules.glob_match` it matches NOTHING, so the
    step would be exempted from every diff while `reach_of` still reported
    `declared`. A trailing slash therefore means the subtree, written
    `<dir>/**`, as BOTH `covers` descriptions in `repo.schema.json` and
    `docs/wiki/Configuration.md` state.

    What this cannot normalize is a BARE directory name — `covers: ["backend"]`
    — which is a legal glob that matches exactly one path (a file called
    `backend`). Guessing there would make a glob mean something other than a
    glob. It is caught the other way instead: `warden declare check` D-01
    refuses a `covers` glob that matches no tracked path, so a reach that can
    never fire is drift rather than a silent exemption.
    """
    return tuple(g.rstrip("/") + "/**" if g.endswith("/") else g
                 for g in step.covers)


def step_reach(step: VerifyStep,
               areas: Sequence[PurePosixPath]) -> tuple[str, str]:
    """How this step's reach is settled, and the reason, in ONE place.

    `scopes_for_paths` (the derivation) and `reach_of` (what `warden declare
    check` D-01 reports) both read this. A report that omitted the
    `components:` half of the inference would call a step whose `cwd` sits
    inside NO declared component, which the derivation WIDENS to the whole
    tree, narrowing by inference. Two readings of one rule drift apart; there
    is one reading.

    - DECLARED: the step named its reach. The second value is the globs.
    - INFERRED_WIDE: no declaration, and nothing narrows it — a root `cwd`, an
      absolute one, one climbing out through `..`, or a subdirectory no
      component declares. Claims nothing, exempts nothing.
    - INFERRED_NARROW: no declaration, and `cwd` + `components:` agree on a
      subtree. The second value is that subtree.
    """
    globs = covers_globs(step)
    if globs:
        return DECLARED, ", ".join(globs)
    cwd = _norm(step.cwd)
    if cwd is None:
        return INFERRED_WIDE, (f"cwd {step.cwd!r} makes no narrowness claim — "
                               "the whole tree")
    if not any(_covers(area, cwd) for area in areas):
        return INFERRED_WIDE, (f"cwd {step.cwd!r} is inside no declared "
                               "component area — the whole tree")
    return INFERRED_NARROW, f"cwd {step.cwd!r}, a declared component area"


def scopes_for_paths(config: RepoConfig,
                     paths: Sequence[str] | None) -> tuple[str, ...]:
    """The declared verify scopes that must have judged a diff touching `paths`.

    A step's reach is DECLARED where repo.yaml says so and INFERRED otherwise.
    The declaration wins, because it is the only one of the two
    that can be right about a command warden cannot read:

    - **Declared** — `covers: [glob, ...]` on the step. The scope is required
      exactly when a changed path matches one of those globs. That covers
      what inference cannot: a step whose `cwd` is `backend/` but whose
      command reaches outside it (`pytest ../..`, a tool whose config sits at
      the repo root, a language server that walks up) would otherwise be
      exempted from every diff outside `backend/`, because no other
      declaration describes the command's reach. `covers: ["**"]` says it.
    - **Inferred** — no `covers:`. A scope NARROWS — claims less than the whole
      tree — only when two independent declarations in repo.yaml agree: the
      step runs in a subdirectory (`cwd`), AND that subdirectory sits inside a
      declared `components:` path. Either one alone is too weak to exempt a
      scope from a diff: `cwd` is a runner convenience an author may have typed
      for any reason, and reading it as a reach declaration on its own would
      silently drop a scope whose commands exercise the whole tree from that
      directory. A component is a deliberate
      statement that an area exists, so the two together are a claim, not an
      accident. Anything else — a root `cwd`, an absolute one, one climbing out
      through `..`, or a subdirectory no component declares — covers the whole
      tree.

    The two arms compose per STEP, never per scope: a scope is required when ANY
    of its steps reaches a changed path, so a declared step answers for itself
    and cannot silence an inferred one beside it.

    `warden declare check` reports which arm each scope is resolved under, so a
    scope still narrowing by inference is visible rather than assumed.

    Fail-closed in both unknowns, which are DISTINCT states even though the
    safe answer is the same one (the tri-state house rule `runs.is_dirty`
    keeps: "could not determine" and "determined to be nothing" must not be
    written the same way):

    - `paths is None` — the caller could not compute the changed paths at all.
    - `paths == ()` — a range that resolved and changed nothing.

    Both return every declared scope, as do paths no declared scope claims,
    because "the gate cannot tell" must cost more verification, not less, and
    rendering no verify section at all would be silence.
    """
    if paths is None:
        return tuple(sorted(config.verify))
    if not paths:
        return tuple(sorted(config.verify))
    areas = [a for a in (_norm(c.path) for c in config.components) if a]
    normalized = [PurePosixPath(p) for p in paths]
    applicable = []
    for scope, steps in config.verify.items():
        for step in steps:
            arm, _ = step_reach(step, areas)
            if arm == DECLARED:
                # Declared reach: the step said what it exercises, so warden
                # reads the declaration and does not guess from `cwd`. A
                # declared step that misses this diff exempts ITSELF, never
                # the scope — the next step is still asked.
                if any(rules_mod.glob_match(g, p)
                       for g in covers_globs(step) for p in paths):
                    applicable.append(scope)
                    break
                continue
            if arm == INFERRED_WIDE:
                applicable.append(scope)
                break
            cwd = _norm(step.cwd)
            if cwd is not None and any(_covers(cwd, p) for p in normalized):
                applicable.append(scope)
                break
    return tuple(sorted(applicable)) or tuple(sorted(config.verify))


def reach_of(step: VerifyStep,
             areas: Sequence[PurePosixPath] = ()) -> tuple[str, str]:
    """`step_reach`, folded to the two answers `warden declare check` D-01
    reports: ("declared", globs) or ("inferred", why) — where "inferred" is
    said ONLY of a step that actually narrows.

    `areas` is the declared component areas, and it is a positional argument
    rather than something this function derives, because the derivation it must
    agree with takes them too. Defaulting it to `()` answers "no component
    declares any area", under which nothing narrows by inference — the same
    answer `scopes_for_paths` gives for that config, never a narrower one.
    """
    arm, why = step_reach(step, areas)
    if arm == DECLARED:
        return "declared", why
    if arm == INFERRED_WIDE:
        return "inferred", why
    return "inferred", f"{why} plus components:"


def _head_moves(root: Path) -> int | None:
    """How many entries HEAD's reflog holds: any commit, checkout or reset
    adds one, so a count that changed across a run means HEAD moved during
    it even when it ended where it began. None when git cannot answer."""
    try:
        out = subprocess.run(["git", "reflog", "show", "--format=%H", "HEAD"],
                             cwd=root, check=True, capture_output=True,
                             text=True, env=runs.git_env()).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return len(out.splitlines())


def run_scope(config: RepoConfig, scope: str, *, which: str = "verify",
              stop_on_failure: bool = False,
              outputs: list[str] | None = None) -> dict:
    """Run every command in the scope; returns the verify-result document.

    `which` selects the repo.yaml scope map — "verify" (default) or "deploy".
    deploy scopes are the SAME shape as verify scopes, so
    they reuse this runner unforked rather than a parallel copy that could
    drift from it.

    `stop_on_failure` halts after the first non-zero step, recording only
    the steps that ran. Verify keeps the default (False): it is a read-only
    gate where every command's verdict is wanted. Deploy passes True — its
    scope is an ORDERED, state-mutating pipeline, and running `flip traffic`
    after `run migrations` failed would land side effects the artifact then
    reports as not deployed.

    `outputs`, when given, receives each step's WHOLE output, one entry per
    record in `results`, for `render_summary` to read pytest's counts from.
    Not the artifact's `output_tail`: a 4000-char window
    can cut away one session's summary and leave another's, so a count read
    there could be a part printed as the whole.
    """
    if which not in ("verify", "deploy"):
        # Fail closed on a bad map name rather than getattr an unrelated
        # attribute (config.root, config.review) and raise an opaque TypeError.
        raise VerifyError(f"unknown scope map {which!r} (expected 'verify' or "
                          "'deploy')")
    scope_map = getattr(config, which)
    if scope not in scope_map:
        known = ", ".join(sorted(scope_map))
        raise VerifyError(f"unknown {which} scope {scope!r} "
                          f"(repo.yaml defines: {known})")

    # The tree the commands START on, stamped again when they end: a suite
    # takes minutes, and a commit or a stash in between would otherwise let
    # the artifact name, as clean, a commit its commands never ran on — which
    # `check_head` turns into a skipped pre-push gate.
    head_before = runs.head_sha(config.root)
    moves_before = _head_moves(config.root)
    dirty_before = runs.is_dirty(config.root)
    results = []
    for step in scope_map[scope]:
        cwd = config.root / step.cwd
        started = time.monotonic()
        # A verify scope IS a shell command by
        # design, and step.run comes from repo.yaml, which is gate surface
        # (HIGH tier). A repo that can edit its repo.yaml already controls
        # its own gate; argv would change the call shape, not the trust
        # boundary. What binds: who can edit repo.yaml, and the cage for
        # unattended runs.
        # warden:allow(shell-true): verify scopes are shell by design; the boundary is repo.yaml's tier, recorded above
        proc = subprocess.run(step.run, shell=True, cwd=cwd,
                              capture_output=True, text=True)
        output = (proc.stdout + proc.stderr)[-_TAIL_CHARS:]
        record = {
            "cmd": step.run,
            "cwd": step.cwd,
            "exit_code": proc.returncode,
            "duration_s": round(time.monotonic() - started, 2),
            "output_tail": output,
        }
        if step.runner:
            # what repo.yaml declares, so a reader of the artifact knows which
            # steps the summary line's pytest counts were read from
            record["runner"] = step.runner
        # An absent toolchain is NAMED, in the artifact as well
        # as on stdout. The artifact is what a reviewer reads days later, and
        # `output_tail` is a 4000-char window a long build can push the one
        # relevant line out of.
        why = diagnose(step.run, proc.returncode)
        if why:
            record["diagnosis"] = why
        results.append(record)
        if outputs is not None:
            outputs.append(proc.stdout + proc.stderr)
        if stop_on_failure and proc.returncode != 0:
            break

    head_after = runs.head_sha(config.root)
    # Equal endpoints are not enough: HEAD can leave and come back (a switch
    # to another branch and back mid-run), which only its reflog shows.
    if _head_moves(config.root) != moves_before:
        head_after = runs.UNKNOWN_SHA
    doc = {
        "scope": scope,
        "passed": all(r["exit_code"] == 0 for r in results),
        # Which commit this result judges. Without it the sticky comment can
        # only guess, and guessing pairs a verdict with the wrong change. HEAD
        # moving mid-run means no one commit was judged: `unknown` pairs with
        # nothing, which is the fail-closed answer.
        "head_sha": head_after if head_after == head_before else runs.UNKNOWN_SHA,
        "results": results,
    }
    # On a pull_request, HEAD is the merge ref actions/checkout built, while
    # review keys on pull_request.head.sha. Record both: the tree we ran on,
    # and the commit the run reports about.
    pr_head = github_mod.pr_head_sha_from_env()
    if pr_head:
        doc["pr_head_sha"] = pr_head
    # Commands that saw uncommitted content did not judge HEAD alone — at the
    # start OR the end of the run; either unknown leaves it "not recorded".
    dirty_after = runs.is_dirty(config.root)
    if dirty_before is not None and dirty_after is not None:
        doc["dirty"] = dirty_before or dirty_after
    schema = json.loads(_SCHEMA_PATH.read_text())
    Draft202012Validator(schema).validate(doc)
    return doc


def check_head(config: RepoConfig, scope: str,
               sha: str) -> tuple[Path | None, str]:
    """Whether a verify artifact already on disk stands for running `scope` on
    commit `sha`: (the run dir accepted, why) or (None, why not).

    The pre-push hook's question, asked so the hook need not
    re-run a suite whose verdict on the pushed commit is already recorded. It
    finds the artifact through `audit.verify_run_for_commit`, the pairing rule
    `warden ship`'s verify-at-head step reads, and then refuses everything
    that is not exactly a clean pass on that commit of the commands repo.yaml
    declares for the scope NOW. Each refusal is "run it", never "fail": a
    missing, FAILED, dirty, tree-state-unknown, merge-ref, malformed or
    command-mismatched artifact all send the caller back to the suite.
    """
    if scope not in config.verify:
        known = ", ".join(sorted(config.verify))
        raise VerifyError(f"unknown verify scope {scope!r} "
                          f"(repo.yaml defines: {known})")
    short = sha[:12]
    hit = audit_mod.verify_run_for_commit(config.root, sha, scope)
    if hit is None:
        return None, f"no verify artifact names {short} under scope {scope}"
    run_dir, doc = hit
    where = run_dir.relative_to(config.root)
    try:
        Draft202012Validator(json.loads(_SCHEMA_PATH.read_text())).validate(doc)
    except ValidationError as e:
        return None, f"{where} is not a valid verify result ({e.message})"
    if doc.get("head_sha") != sha:
        # Matched on pr_head_sha: CI ran a merge ref standing for this head,
        # which is not this commit's tree.
        return None, (f"{where} ran on {str(doc.get('head_sha'))[:12]}, a "
                      f"tree standing for {short}, not {short} itself")
    if not doc["passed"] or any(r["exit_code"] != 0 for r in doc["results"]):
        return None, f"{where} records FAIL on {short}"
    if doc.get("dirty") is not False:
        state = "a DIRTY tree" if doc.get("dirty") else "no tree state"
        return None, f"{where} records {state}, so it did not judge {short} alone"
    ran = [(r["cmd"], r["cwd"]) for r in doc["results"]]
    declared = [(step.run, step.cwd) for step in config.verify[scope]]
    if ran != declared:
        return None, (f"{where} ran other commands than repo.yaml's {scope} "
                      "scope declares now")
    return run_dir, f"{where} records PASS on {short}, clean tree, same commands"


# pytest's closing line, with or without its `=` rule: `3 passed, 1 skipped in
# 0.12s`, `== 1 failed, 4 passed in 0.31s (0:00:01) ==`, `no tests ran in 0.01s`,
# and pytest 9's subtests groups: `2 passed, 3 subtests passed in 0.01s`.
_PYTEST_SUMMARY = re.compile(
    r"^=*\s*((?:\d+ (?:subtests? )?[a-z]+)(?:, \d+ (?:subtests? )?[a-z]+)*"
    r"|no tests ran) in \d[\d.]*s\b")
# A subtests group counts subtests, not tests, so it is matched and set aside.
_PYTEST_COUNT = re.compile(r"(\d+) (subtests? )?([a-z]+)")
# An SGR colour code: pytest colours its summary under FORCE_COLOR, PY_COLORS
# or --color=yes, and a code before the count defeats `_PYTEST_SUMMARY`.
_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _summaries(output: str) -> list[re.Match]:
    """Every pytest summary line in a step's whole output, colour removed."""
    lines = _ANSI_SGR.sub("", output).splitlines()
    return [m for m in map(_PYTEST_SUMMARY.match, lines) if m]


def pytest_counts(output: str) -> dict[str, int] | None:
    """The outcome counts from pytest's summary line in a step's whole output,
    keyed as pytest words them (`passed`, `skipped`, `failed`, `error`, ...).

    None whenever the output does not carry exactly one summary line, because
    the caller prints the number as evidence and a wrong number is worse than
    none: no line means the session died before printing one, and more than
    one means two sessions in one step, or a test that printed a line shaped
    like a summary, where picking one is a guess.
    """
    found = _summaries(output)
    if len(found) != 1:
        return None
    return {word: int(n) for n, subtests, word
            in _PYTEST_COUNT.findall(found[0].group(1)) if not subtests}


def _pytest_clause(results: Sequence[dict],
                   outputs: Sequence[str]) -> str | None:
    """The scope's pytest counts for its summary line, summed over every step
    repo.yaml DECLARES `runner: pytest`. The command text
    is never read to guess a runner: a fail-closed check cannot parse a
    general-purpose shell, and seven follow-ups in two days proved it.

    Any step whose counts cannot be taken as the step's whole withholds the
    total, since a partial sum prints a smaller number as if it were whole:
    an undeclared step whose output carries a summary-shaped line, and a
    declared step whose output carries none, or more than one (several
    sessions, where one that died before printing leaves no trace, so even
    one line is taken only on the declaration's word that there is one
    session). A scope with no declared step and no summary-shaped output
    prints no clause (None)."""
    declared = [r.get("runner") == "pytest" for r in results]
    for r, out, is_pytest in zip(results, outputs, declared):
        if not is_pytest and _summaries(out):
            return (f"pytest: no counts — `{r['cmd']}` printed a pytest "
                    "summary and does not declare `runner: pytest`")
    if not any(declared):
        return None
    total: dict[str, int] = {}
    for i, r in enumerate(results):
        if not declared[i]:
            continue
        found = _summaries(outputs[i]) if i < len(outputs) else []
        if not found:
            return "pytest: counts not found in the captured output"
        if len(found) > 1:
            return (f"pytest: counts withheld — `{r['cmd']}` printed "
                    f"{len(found)} pytest summaries and verify reads one per step")
        for word, n in pytest_counts(outputs[i]).items():
            total[word] = total.get(word, 0) + n
    clause = (f"pytest: {total.get('passed', 0)} passed, "
              f"{total.get('skipped', 0)} skipped, {total.get('failed', 0)} failed")
    errors = total.get("error", 0) + total.get("errors", 0)
    if errors:
        clause += f", {errors} error{'s' if errors != 1 else ''}"
    return clause


# One line naming one failing test, per runner whose output the platform's
# own enrollments meet, each anchored on what only that runner prints: TAP's
# `not ok N - ` (`node --test` off a terminal; a `# TODO`/`# SKIP` line is not
# a failure), node's spec reporter's `\u2716 name (Nms)` with its duration (a
# linter's `\u2716 3 problems` has none), pytest's short summary naming a
# `.py` node id up to its ` - ` message (unittest's `FAILED (failures=2)` and
# a log's `ERROR connecting` name none), and `go test -v`'s `--- FAIL: `.
_FAILING = (
    re.compile(r"^\s*not ok \d+ - (?!.*#\s*(?:TODO|SKIP)\b)(.+?)\s*$", re.I),
    re.compile(r"^\s*\u2716 (.+?) \(\d+(?:\.\d+)?ms\)$"),
    re.compile(r"^(?:FAILED|ERROR) (\S+?\.py(?:::.+?)?)(?: - .*)?$"),
    re.compile(r"^\s*--- FAIL: (\S+)"),
)
_FAILING_SHOWN = 20


def failing_tests(output: str) -> list[str]:
    """The failing tests a step's output names, first-seen order, each once.
    Read off the WHOLE output, so a name the tail window cut away is still
    printed; empty when no line has a shape it knows, and the tail stands."""
    names: list[str] = []
    for line in _ANSI_SGR.sub("", output).splitlines():
        for pattern in _FAILING:
            m = pattern.match(line)
            if m and m.group(1) not in names:
                names.append(m.group(1))
                break
    return names


def render_summary(doc: dict, *, which: str = "verify",
                   outputs: Sequence[str] | None = None) -> str:
    # `which` labels the run for its map (verify | deploy) — run_scope serves
    # both, so a hardcoded 'verify' label would misname a deploy run.
    head = f"{which} --scope {doc['scope']}: {'PASS' if doc['passed'] else 'FAIL'}"
    # The counts ride the summary line, so the Evidence
    # line's "N tests passed" is read here rather than out of an artifact.
    # They come from `outputs`, the whole output `run_scope` handed back; a
    # document with no outputs beside it prints no counts at all.
    clause = None if outputs is None else _pytest_clause(doc["results"], outputs)
    lines = [f"{head} — {clause}" if clause else head]
    if doc.get("dirty"):
        # Deliberate asymmetry with attest: verify RUNS on
        # a dirty tree — it is mid-iteration tooling — but the runner must
        # see what the artifact records, or a PASS from a tree that is not
        # HEAD gets paired confidently against a review of HEAD.
        lines.append("  ⚠ working tree dirty — this verdict describes the "
                     "tree as it ran, not HEAD alone")
    elif "dirty" not in doc:
        # is_dirty is tri-state; None (git status failed) omits the key.
        # runs.py's contract: absent is "not recorded", never "clean" — and
        # silence here would render the two identically.
        lines.append("  ⚠ tree state not recorded (git status failed) — "
                     "this verdict is unverified against HEAD")
    for i, r in enumerate(doc["results"]):
        status = "ok" if r["exit_code"] == 0 else f"exit {r['exit_code']}"
        lines.append(f"  [{status}] ({r['duration_s']}s) {r['cmd']}")
        if r.get("diagnosis"):
            # Above the output tail, not inside it: the tail is where the
            # unreadable `/bin/sh: go: command not found` already was.
            lines.append(f"    ✗ {r['diagnosis']}")
        failing = (failing_tests(outputs[i] if outputs and i < len(outputs)
                                 else r.get("output_tail") or "")
                   if r["exit_code"] != 0 else [])
        if failing:
            # Named above the tail and in the log, because the tail is 15
            # lines and the artifact that holds the rest expires in a day.
            lines.append(f"    failing tests ({len(failing)}):")
            lines.extend(f"      - {n}" for n in failing[:_FAILING_SHOWN])
            if len(failing) > _FAILING_SHOWN:
                lines.append(f"      … and {len(failing) - _FAILING_SHOWN} more")
        if r["exit_code"] != 0 and r.get("output_tail"):
            tail = "\n".join(r["output_tail"].splitlines()[-15:])
            lines.append("    " + tail.replace("\n", "\n    "))
    return "\n".join(lines) + "\n"
