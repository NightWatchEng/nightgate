"""warden ship: the ship tail as one deterministic command.

The last mechanical steps of a change, hand-run, give no immediate feedback
and fail SILENTLY. Three failure shapes this command makes unreportable as
success:

- **gate parity green while the verify artifact names a commit that is not
  the shipped head.** Run verify, commit the attestation shard, head moves,
  nothing re-verifies — or one cheap scope satisfies parity on a diff that
  changed another component entirely.
- **a PR title that fails commit-lint AFTER the PR is open**, because the
  headline runs past the 100-char budget. CI lints the squash headline, so
  that is a red PR and a round trip.
- **work finished and never pushed**, its only copy sitting local.

So the tail is one command, it runs the steps in order, each step PRINTS what
it checked and what it found, and a step that cannot evaluate REFUSES rather
than passing. None of the three failures above can be reported as success by a
unit that ran this.

Two design constraints are load-bearing rather than stylistic:

- **Which verify scopes a diff requires comes from `verify.scopes_for_paths`** —
  the same derivation `warden declare check` D-01 reports on and `warden
  review`/`audit` pair artifacts by. A second spelling of that rule is a drift
  source: one scope selection disagreeing with another is how parity passes
  on the wrong scope.
- **It does not merge, and cannot.** There is no `gh pr merge` here and no
  `--auto`; merge authority is the founder's. `warden ship` opens a PR and
  stops, which is the same boundary `deliver` and `autonomous-run` hold.

What it is NOT: a second verify runner. Step 1 asks whether an artifact on disk
judged THIS commit under each required scope — the refusal is the product, and
re-running the suite inside the tail would cost minutes to answer a question
the evidence already answers. `audit` reads the same artifacts the same way;
ship refuses where audit renders NO VERIFY RESULT.
"""

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import audit as audit_mod
from . import attest as attest_mod
from . import graph as graph_mod
from . import diffs as diffs_mod
from . import progress as progress_mod
from . import verify as verify_mod
from .config import RepoConfig

# The PR-title validator, relative to the repo root. One validator, already
# shared by `.githooks/commit-msg` and CI — ship is the THIRD caller of the
# same script and re-implements none of it. Re-deriving the 100-char budget
# here would be a second spelling of a rule that is easy to get wrong on the
# character-versus-byte axis alone.
#
# A DEFAULT, not a requirement, and the residual is stated rather than hidden:
# this is the platform repo's own path, so a consumer keeping the validator
# elsewhere passes `--commit-lint`. It is a flag and not a `repo.yaml` key
# because the schemas are gate surface — a new declared key is a separate,
# human-reviewed change — and a flag cannot go stale the way a config key whose
# reader disappeared can. A consumer with no validator at all gets the step's
# refusal, which is the correct answer: a title nothing checked is not a title
# known to be good.
COMMIT_LINT = "scripts/commit-lint.sh"
DEFAULT_REMOTE = "origin"


class ShipError(Exception):
    """A step could not be evaluated. Exit 2, never 1: "the tail did not run"
    and "the tail ran and refused" are different answers and a unit must not be
    able to report one as the other."""


@dataclass(frozen=True)
class Check:
    """One step, what it checked, and what it found.

    `ok` is a verdict, never a default: every constructor sets it explicitly,
    and the only way to reach a passing step is for its own assertion to hold.
    """
    step: str
    ok: bool
    checked: str
    found: str
    extra: dict = field(default_factory=dict)


def render_check(check: Check, *, index: int, total: int) -> str:
    verdict = "OK" if check.ok else "REFUSED"
    return (f"ship step {index}/{total} {check.step}: {verdict}\n"
            f"  checked: {check.checked}\n"
            f"  found:   {check.found}\n")


def run_cmd(argv: list[str], cwd: Path) -> tuple[int, str]:
    """Run `argv` and return (exit code, combined output).

    argv, never a shell string: every element here is either a literal or a
    value warden resolved itself (a branch name, a sha, a path), and a shell
    would make a branch name with a metacharacter in it executable.

    The single seam the tests drive — one place to intercept every external
    call, so a test can assert on the argv ship BUILDS rather than on whatever
    a live git and gh happened to do.
    """
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    except OSError as e:
        raise ShipError(f"{argv[0]!r} could not be run ({e}) — this step "
                        "cannot be evaluated, and an unevaluated step is "
                        "never a pass") from e
    return proc.returncode, (proc.stdout + proc.stderr).strip()


# ── step 1: verify at HEAD, under every scope the diff requires ────────────

def required_scopes(config: RepoConfig, base: str,
                    head: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(required scopes, changed paths) for `base...head`.

    Both halves come from the shipped derivation and neither is re-spelled
    here: `diffs.changed_files` is the rename-detection-off path list the scope
    question needs (a move touches two subtrees), and
    `verify.scopes_for_paths` is the reach rule `warden declare check` D-01
    reports on.

    A range that will not resolve RAISES. `scopes_for_paths` has a fail-closed
    arm for that (it returns every declared scope for `paths is None`), and
    refusing is the stronger of the two answers: ship cannot push or open a PR
    for a range it could not read, so guessing a scope set would only let the
    step after it fail less clearly.
    """
    try:
        paths = diffs_mod.changed_files(config.root, base, head)
    except diffs_mod.DiffError as e:
        raise ShipError(f"the range {base}...{head} will not resolve ({e}), so "
                        "which verify scopes this diff requires cannot be "
                        "derived") from e
    return verify_mod.scopes_for_paths(config, paths), paths


def check_verify_at_head(config: RepoConfig, *, base: str, head: str) -> Check:
    """Every required scope has a verify artifact that names HEAD and passed.

    THE recurring failure this pins: run verify, commit the attestation shard,
    head moves, nothing re-verifies — and the unit reports green. An
    attestation cannot name the commit containing it, but a VERIFY result names
    whatever you run it on, so running verify after the shard commit is always
    available. One unit reasoned itself into the opposite and shipped a green
    claim about the wrong sha; that reasoning is what this refusal removes.

    Three distinct refusals, because they are three different states and
    collapsing them sends the reader to the wrong fix:

    - no artifact judged this commit under that scope (re-run verify);
    - an artifact judged it and FAILED (fix the code);
    - an artifact judged it on a tree that was dirty, or whose state git could
      not determine — it did not judge HEAD alone, and tri-state means
      "not recorded" is refused exactly like "dirty" rather than read as clean.
    """
    scopes, paths = required_scopes(config, base, head)
    checked = (f"{len(scopes)} verify scope(s) the diff requires, derived from "
               f"repo.yaml reach over {len(paths)} changed path(s) "
               f"({', '.join(scopes)}), each against HEAD {head[:12]}")
    docs = audit_mod.verify_for_commit(config.root, head, scopes)
    problems = []
    found = []
    for scope in scopes:
        doc = docs.get(scope)
        if doc is None:
            problems.append(
                f"{scope}: NO verify artifact on disk names HEAD "
                f"{head[:12]} under this scope — run "
                f"`warden verify --scope {scope}` on this commit. Absence of a "
                "result is not a green gate")
            continue
        if not doc.get("passed"):
            problems.append(f"{scope}: the verify artifact for {head[:12]} "
                            "records FAIL")
            continue
        if doc.get("dirty"):
            problems.append(
                f"{scope}: the verify artifact for {head[:12]} ran on a DIRTY "
                "tree, so it did not judge HEAD alone — commit, then re-run it")
            continue
        if "dirty" not in doc:
            problems.append(
                f"{scope}: the verify artifact for {head[:12]} records no tree "
                "state (git could not answer), so it is unverified against "
                "HEAD — re-run it")
            continue
        found.append(f"{scope}: PASS on {head[:12]}, clean tree")
    return Check("verify-at-head", not problems, checked,
                 "; ".join(problems or found),
                 extra={"scopes": list(scopes), "changed_paths": len(paths)})


# ── step 2: every tip attestation clean ───────────────────────────────────

def check_attest_clean(config: RepoConfig, *, base: str, head: str) -> Check:
    """`warden attest check`'s rule, in process: a committed shard names a
    commit in `base..head`, the LAST shard on every line of that range
    verdicts clean, and no commit those tips do not cover ships anything but a
    round's own evidence.

    THREE conditions, because `attest check` has three. The coverage rule
    shipped with its refusal wired only into `warden attest
    check`'s exit code, and this function read `attested` and `open_verdict`
    and stopped — so `ship --checks-only`, which `deliver` and `orchestrate`
    run as the pre-PR gate-parity step, returned `attest-clean: PASS` on
    exactly the range CI's required step exits 1 on. A parity check whose
    docstring says "`attest check`'s rule, in process" and then asks fewer
    questions than it does is worse than no parity check: it is a green light
    that certifies the red one has not been asked (round 1, F1).

    `attest.check_range` is called rather than re-derived — one attestation
    covers one round, so an earlier round's `findings-open` shard is committed
    by design and only the tips bind. An `AttestError` here is a
    cannot-evaluate, so it becomes exit 2 rather than a refusal: an unreadable
    shard store is not an absent attestation.
    """
    crew = _declared_crew(config)
    checked = (f"every TIP attestation in {base}..{head[:12]} — committed, "
               "naming a commit in the range, and verdict clean; and no "
               "uncovered commit shipping content" +
               (", each carrying a roster warden checked against the crew "
                "graph.yaml declares" if crew is not None else ""))
    try:
        doc = attest_mod.check_range(config.root, base=base, head=head)
    except attest_mod.AttestError as e:
        raise ShipError(f"attest check could not be evaluated ({e}) — an "
                        "unreadable shard store is not an absent "
                        "attestation") from e
    if not doc["attested"]:
        return Check("attest-clean", False, checked,
                     "NO committed attestation names a commit in this range — "
                     "the branch carries no review")
    open_verdict = doc.get("open_verdict")
    if open_verdict:
        return Check("attest-clean", False, checked,
                     f"the last attestation on a line of this range "
                     f"({str(open_verdict.get('sha') or '?')[:12]} -> "
                     f"{open_verdict.get('shard')}) carries verdict "
                     f"{open_verdict.get('verdict')!r} — the review covering "
                     "this branch has findings open")
    # The coverage rule, in the same order `attest check` asks it: after the
    # verdict, because a review that never CLOSED is the more basic failure,
    # and the two have different remedies.
    gap = doc.get("coverage_gap") or []
    if gap:
        named = "; ".join(f"{row['sha'][:12]} -> {', '.join(row['outside'][:3])}"
                          for row in gap[:3])
        return Check("attest-clean", False, checked,
                     f"{len(gap)} commit(s) in this range land after the "
                     f"attestation covering their line of history and change "
                     f"content no round has read: {named}"
                     + (f"; and {len(gap) - 3} more" if len(gap) > 3 else "")
                     + f". Only a commit whose whole diff sits under "
                     f"{attest_mod.SHARD_DIR.as_posix()}/ is admitted after a "
                     "round. Re-review this head, `warden memory ingest`, and "
                     "commit the shard")
    # The roster, last, and only where a crew is DECLARED: a repo with no
    # `review` block has nothing for a roster to be checked against. That is
    # SHIP's boundary and ship's alone — `attest write --review-dir` still
    # validates the whole document before it will answer, so it refuses
    # documents this step now ships.
    asserted = [(shard, state) for shard, state
                in _roster_states(config, head=head, tips=doc.get("latest") or [])
                if state != attest_mod.ROSTER_VERIFIED] if crew is not None else []
    if asserted:
        named = "; ".join(
            f"{shard} ({'builder-asserted' if state == attest_mod.ROSTER_UNVERIFIED else 'records no roster state'})"
            for shard, state in asserted)
        return Check("attest-clean", False, checked,
                     f"this repo declares a review crew, and the tip "
                     f"attestation says its roster was never checked against "
                     f"it: {named}. Re-run the round and attest it with "
                     f"`warden attest write --review-dir <round>/reviewers` — "
                     f"without that flag the roster is the builder's word, so "
                     f"neither it nor the declared crew "
                     f"(`warden graph crew --round N`) was compared with "
                     f"anything")
    return Check("attest-clean", True, checked,
                 "every tip attestation in the range is clean" +
                 (", with a roster warden checked against the declared crew"
                  if crew is not None else ""))


def _declared_crew(config: RepoConfig) -> dict | None:
    """The review crew this repo declares, or None when it declares none.

    The TRIGGER is narrow, and the narrowness is the contract: this step turns
    on only where graph.yaml declares a `review` block, which is the boundary
    Graph-Layer.md draws for `warden ship`. Asking `declared_crew` first — it
    validates the whole document before it can answer "no review key" — cost a
    consumer whose graph.yaml this schema rejects the WHOLE ship tail, every
    run and `--checks-only` too, on a repo that declares no crew and shipped
    fine on the commit before. A roster check is not a licence to re-validate
    a document it was never about; `warden graph validate` is the command that
    judges the graph.

    `attest write --review-dir` does NOT draw that boundary, and the claim
    that it did — made here, as this narrowing's own recorded rationale — was
    WITHDRAWN rather than softened. `warden/cli.py` calls `declared_crew`
    unconditionally under that flag, and `declared_crew` loads and
    schema-validates the whole document before it can answer "no review key",
    so a graph.yaml this schema rejects while carrying no `review` key, or one
    whose key is misspelled, refuses the write (AttestError, exit 2) while ship
    ships it.

    THE TWO SEAMS DIVERGE BY RULING, and the ruling is committed in the
    decision store (`warden decide list`), not here. The short of it: the two
    commands are asked different questions by their callers. ship is a
    readiness report that must not refuse a whole tail over a repo that claims
    no crew. The `--review-dir` FLAG is the caller asking for the roster to be
    cross-checked, so a document warden cannot validate means that check cannot
    be performed, and exit 2 is the only answer that does not record a roster
    nothing read as one that was read. Nothing is blocked by it: a write
    without the flag still stamps `unverified-roster`.

    So narrowing `declared_crew` to THIS trigger to make the two agree would
    reopen the fail-closed hole the round-one fix closed, and
    `tests/test_attest_crew.py` drives both seams over one fixture set so the
    move goes red rather than unnoticed.

    Where a block IS declared, nothing is softened. The crew is read through
    `graph.declared_crew`, never re-derived: once this trigger has fired, ship
    and attest ask the SAME function what the crew is, so they cannot disagree
    about that — only about when to ask it, which is the ruling above. A declared
    block that does not resolve is a cannot-evaluate, because the repo claims
    a crew and warden cannot say what it is; so is a document that cannot be
    decoded or parsed, which cannot be asked for its keys either. A
    `GraphError` is never swallowed into None — an unreadable graph reading as
    "declares no crew" is the hole `declared_crew`'s docstring exists to close.
    """
    try:
        declared = graph_mod.declares_review(config.root)
    except graph_mod.GraphError as e:
        raise ShipError(
            f"whether this repo declares a review crew could not be asked of "
            f"{graph_mod.GRAPH_FILENAME} at all ({e}) — a document warden "
            "cannot decode or parse cannot be read for its keys, and 'could "
            "not say' is not 'declares none'") from e
    if not declared:
        return None
    try:
        return graph_mod.declared_crew(config.root)
    except graph_mod.GraphError as e:
        raise ShipError(
            f"this repo declares a review crew and it could not be resolved "
            f"({e}), so a shard's roster cannot be checked against it") from e


def _roster_states(config: RepoConfig, *, head: str,
                   tips: list) -> list[tuple[str, str]]:
    """(shard path, `roster_verification`) for each tip attestation.

    Read out of git at `head`, exactly as `attested_shas` reads the store: CI
    clones the repo and sees only committed files, so a check whose refusal
    says "committed" must read what a clone would. "" is a shard that records
    no state — written before the field existed — and it is returned as a
    state rather than skipped, because "could not say" must never reach the
    caller as "checked and fine".
    """
    out: list[tuple[str, str]] = []
    for tip in tips:
        shard = str((tip or {}).get("shard") or "")
        if not shard:
            continue
        try:
            doc = json.loads(diffs_mod.run_git(config.root, "show",
                                               f"{head}:{shard}"))
        except (diffs_mod.DiffError, ValueError) as e:
            raise ShipError(
                f"the tip attestation {shard} could not be read at "
                f"{head[:12]} ({e}), so what it records about its own roster "
                "is unknown — an unreadable shard is not a checked one") from e
        state = doc.get("roster_verification") if isinstance(doc, dict) else None
        out.append((shard, state if isinstance(state, str) else ""))
    return out


# ── step 3: the PR title, BEFORE the PR exists ────────────────────────────

def check_title(config: RepoConfig, *, title: str,
                linter: Path | None = None) -> Check:
    """Lint the PR title with the repo's own commit-lint, header-only.

    BEFORE the PR is created, which is the entire point: a squash merge lands
    one commit whose header IS the PR title, CI lints that headline, and three
    titles in one session were found over budget only after the PR was open.
    The forge appends " (#N)" and the validator strips it, so the bare title is
    exactly what to lint — and the budget counts CHARACTERS, not bytes, which
    is the validator's own problem and stays there rather than being counted a
    second time here.

    A missing validator REFUSES. A title nothing could check is not a title
    known to be good, and passing here would reproduce the failure one step
    later in CI, which is the round trip this exists to remove.
    """
    script = linter or (config.root / COMMIT_LINT)
    checked = (f"the PR title against {script} --header-only, the same "
               "validator .githooks/commit-msg and CI run (the forge's "
               f"\" (#N)\" suffix is the validator's to strip): {title!r}")
    if not title.strip():
        return Check("pr-title", False, checked,
                     "the title is empty — there is nothing to lint, and an "
                     "unlinted title is not a linted one")
    if not script.is_file():
        raise ShipError(
            f"{script} is not a file, so the PR title cannot be linted. Pass "
            "--commit-lint <path> for a repo that keeps the validator "
            "elsewhere; a title nothing checked must not be shipped as one "
            "that passed")
    with tempfile.TemporaryDirectory() as tmp:
        msg = Path(tmp) / "pr-title"
        msg.write_text(title + "\n", encoding="utf-8")
        # Under `sh` rather than relying on the exec bit: the script is
        # `#!/bin/sh` and its own length check is pinned identical across
        # dash/bash/zsh, so the interpreter is not a variable here — and a
        # checkout that lost the mode bit still gets its title linted.
        code, output = run_cmd(["sh", str(script), "--header-only", str(msg)],
                              config.root)
    if code != 0:
        return Check("pr-title", False, checked,
                     f"commit-lint REFUSED the title (exit {code}): "
                     f"{output or 'no diagnostic'}")
    return Check("pr-title", True, checked,
                 f"commit-lint accepts it ({len(title)} characters)")


# ── step 4: gate parity ───────────────────────────────────────────────────

def check_parity(config: RepoConfig, *, base: str) -> Check:
    """`warden review --base <base> --no-comment` exits 0 — exactly what CI
    will say, an order of magnitude cheaper to learn here.

    Run as a subprocess of THIS interpreter (`-m warden`), not called in
    process: parity means running the command CI runs, and a direct call to the
    review internals would share this process's already-loaded config and
    cwd — the two things most likely to differ between a laptop and a runner.
    """
    checked = (f"gate parity: `warden review --base {base} --no-comment`, "
               "the command CI runs, in this repo")
    code, output = run_cmd([sys.executable, "-m", "warden", "review",
                            "--base", base, "--no-comment"], config.root)
    tail = "\n".join(output.splitlines()[-12:])
    # The gate's exit codes pass THROUGH, they do not collapse.
    # `warden review` returns 2 for "gate DID NOT RUN" — an
    # unresolvable range, an unreadable config, an unexpected crash — and 1
    # only for a blocking finding. Rendering both as a refusal would make ship report
    # "the tail ran and refused" about a gate that never ran, which is the one
    # thing this module's own ShipError docstring says a unit must not be able
    # to do. Reachable from ship's own default `--base origin/main` in a
    # worktree that has not fetched.
    if code == 2:
        raise ShipError(
            "`warden review` exited 2 — the gate DID NOT RUN, so parity with "
            f"CI could not be evaluated at all:\n{tail}")
    if code != 0:
        return Check("gate-parity", False, checked,
                     f"exit {code} — CI will say the same. Tail:\n{tail}")
    return Check("gate-parity", True, checked, "exit 0")


# ── step 5: push, and prove the remote has it ─────────────────────────────

def current_branch(config: RepoConfig) -> str:
    """The branch ship would push, read through `diffs.run_git`.

    Deliberately NOT through `run_cmd`: that seam is for the calls the tail
    PERFORMS — the push, the PR, the parity run — and reading local git state is
    a different thing. Keeping them apart is what lets a test stub every
    external effect without also stubbing the repo out from under the command.
    """
    try:
        out = diffs_mod.run_git(config.root, "rev-parse", "--abbrev-ref",
                                "HEAD").strip()
    except diffs_mod.DiffError as e:
        raise ShipError(f"the current branch could not be read ({e}), so ship "
                        "cannot name what it would push") from e
    if not out:
        raise ShipError("the current branch could not be read, so ship cannot "
                        "name what it would push")
    return out.splitlines()[0].strip()


def push(config: RepoConfig, *, branch: str, head: str,
         remote: str = DEFAULT_REMOTE) -> Check:
    """Push the branch by its LITERAL name, then ask the remote whether it
    actually has the commit.

    Both halves matter. Finished work that is never pushed leaves its only
    copy local, so the push is part of the tail rather than a thing a builder
    remembers. And the confirmation is a `ls-remote` against the remote
    itself, not the local remote-tracking ref: a tracking ref is this
    checkout's belief about the remote, and "I believe I pushed" is exactly
    the claim to check.

    Refuses `main` and a detached HEAD before running anything. Never
    force-pushes: there is no `--force` here, and a non-fast-forward is a
    refusal for a human to resolve.
    """
    checked = (f"push {branch} to {remote}, then confirm {remote} carries "
               f"{head[:12]}")
    if branch in ("main", "master", "HEAD"):
        return Check("pushed", False, checked,
                     f"refusing to push {branch!r} — ship never pushes to the "
                     "default branch, and 'HEAD' means a detached checkout "
                     "with no branch to push")
    code, output = run_cmd(["git", "push", "--set-upstream", remote, branch],
                           config.root)
    if code != 0:
        return Check("pushed", False, checked,
                     f"git push exited {code}: {output or 'no diagnostic'}")
    code, output = run_cmd(["git", "ls-remote", remote,
                            f"refs/heads/{branch}"], config.root)
    if code != 0:
        raise ShipError(f"git ls-remote {remote} exited {code} ({output}), so "
                        "whether the remote carries this commit could not be "
                        "established — and an unconfirmed push is what left "
                        "one unit's only copy local")
    remote_sha = output.split()[0] if output.split() else ""
    if remote_sha != head:
        return Check("pushed", False, checked,
                     f"{remote}/{branch} is at {remote_sha[:12] or 'nothing'} "
                     f"and HEAD is {head[:12]} — the work is NOT on the remote")
    return Check("pushed", True, checked,
                 f"{remote}/{branch} is at {head[:12]}")


# ── step 6: open (or update) the PR. Never merge ──────────────────────────

def open_pr(config: RepoConfig, *, branch: str, title: str,
            body_file: Path, base: str = "main") -> Check:
    """Create the PR, or update the one this branch already has.

    Idempotent by asking first: a second `gh pr create` on the same branch
    either errors or opens a duplicate, and a builder re-running the tail after
    a refusal is the normal case, not the exception.

    It opens. It does not merge, it does not enable auto-merge, and no argv
    built here names a merge — `tests/test_ship.py` asserts that over this
    module's own source, because the boundary is worth a mechanical check
    rather than a sentence.
    """
    checked = (f"a PR for {branch} into {base} exists and carries this title "
               f"and body (ship opens; it never merges)")
    if not body_file.is_file():
        raise ShipError(f"{body_file} is not a file, so the PR body — the "
                        "evidence chain — cannot be read")
    # `state` as well as `url`, and only an OPEN PR is the one to update.
    # `gh pr view <branch>` resolves a CLOSED or MERGED PR
    # for a reused branch name — this repo squash-merges, so a recycled branch
    # is the ordinary path, not an exotic one — and editing that PR's title
    # would report `pr-opened: OK — updated <url>` at exit 0 with the
    # pushed commits in no open PR at all. Existence checked, validity not.
    # `baseRefName` too: this step's own `checked` line claims "a PR
    # for <branch> INTO <base>", and reading only the state would check two
    # thirds of that sentence. An open PR targeting another base is not the one to
    # update — silently retargeting it would be worse than refusing — so it is
    # a refusal a human resolves.
    code, existing = run_cmd(
        ["gh", "pr", "view", branch, "--json", "url,state,baseRefName",
         "--jq", '.state + " " + .baseRefName + " " + .url'], config.root)
    # ONE line, then split. `run_cmd` returns stdout and stderr
    # concatenated, so `gh`'s "A new release of gh is available" banner — which
    # rides along on a perfectly successful call — would otherwise become part
    # of the URL this step reports and writes into ship-result.json.
    first = existing.strip().splitlines()[0] if existing.strip() else ""
    state, _, rest = first.partition(" ")
    pr_base_ref, _, url = rest.partition(" ")
    if code == 0 and state == "OPEN" and url.startswith("http"):
        if pr_base_ref != base:
            return Check("pr-opened", False, checked,
                         f"{branch} already has an OPEN PR ({url}) into "
                         f"{pr_base_ref or 'an unreported base'}, not {base} — "
                         "refusing to retarget or to open a second PR for one "
                         "branch. Close it, or ship with --pr-base "
                         f"{pr_base_ref}")
        code, output = run_cmd(["gh", "pr", "edit", branch, "--title", title,
                                "--body-file", str(body_file)], config.root)
        if code != 0:
            return Check("pr-opened", False, checked,
                         f"gh pr edit exited {code}: {output}")
        return Check("pr-opened", True, checked, f"updated {url}")
    if code == 0 and state in ("CLOSED", "MERGED"):
        print(f"warden ship: {branch} already carries a {state} PR ({url}) — "
              "opening a new one rather than editing a PR nothing will merge",
              file=sys.stderr)
    code, output = run_cmd(["gh", "pr", "create", "--base", base, "--head",
                            branch, "--title", title, "--body-file",
                            str(body_file)], config.root)
    if code != 0:
        return Check("pr-opened", False, checked,
                     f"gh pr create exited {code}: {output}")
    url = next((ln.strip() for ln in reversed(output.splitlines())
                if ln.strip().startswith("http")), "")
    return Check("pr-opened", True, checked, url or output or "PR created")


# ── the tail ──────────────────────────────────────────────────────────────

def run(config: RepoConfig, *, base: str, title: str, body_file: Path | None,
        remote: str = DEFAULT_REMOTE, pr_base: str = "main",
        linter: Path | None = None,
        checks_only: bool = False) -> tuple[list[Check], int]:
    """Every step in order, stopping at the first refusal.

    Stopping is deliberate. The steps are ordered cheapest-correction-first,
    and each later one is only meaningful given the earlier: pushing a branch
    whose verify artifact names the wrong commit is how a green claim about the
    wrong sha reached a PR four times in one session.

    Returns the checks that RAN and the exit code: 0 all passed, 1 a step
    refused. A step that cannot evaluate raises `ShipError` -> exit 2.
    """
    head = diffs_mod.run_git(config.root, "rev-parse", "HEAD").strip()
    branch = current_branch(config)
    steps = [
        lambda: check_verify_at_head(config, base=base, head=head),
        lambda: check_attest_clean(config, base=base, head=head),
        lambda: check_title(config, title=title, linter=linter),
        lambda: check_parity(config, base=base),
    ]
    if not checks_only:
        if body_file is None:
            raise ShipError("--body-file is required to open a PR (it carries "
                            "the evidence chain); pass --checks-only to run "
                            "the four checks alone")
        steps.append(lambda: push(config, branch=branch, head=head,
                                  remote=remote))
        steps.append(lambda: open_pr(config, branch=branch, title=title,
                                     body_file=body_file, base=pr_base))
    total = len(steps)
    checks: list[Check] = []
    for index, step in enumerate(steps, start=1):
        check = step()
        checks.append(check)
        # `flush=True`: stdout is block-buffered whenever it is not a terminal,
        # so a piped or captured run would print the stderr refusal line FIRST
        # and the narration after it — backwards, in the one command whose
        # product IS its narration.
        print(render_check(check, index=index, total=total), end="", flush=True)
        if not check.ok:
            print(f"warden ship: REFUSED at step {index}/{total} "
                  f"({check.step}) — nothing after it ran", file=sys.stderr)
            return checks, 1
        _note_boundary(config, check, head=head)
    return checks, 0


# The boundaries ship itself passes, and nothing more: it records `parity-run`
# for the gate it RAN, and `pushed`/`pr-opened` for the two it performed. It
# does not record `verify-run` — step 1 reads an artifact another command
# produced, and claiming a boundary ship did not pass would make the progress
# table say something untrue about where the unit is.
_BOUNDARY_FOR = {"gate-parity": "parity-run", "pushed": "pushed",
                 "pr-opened": "pr-opened"}


def _note_boundary(config: RepoConfig, check: Check, *, head: str) -> None:
    boundary = _BOUNDARY_FOR.get(check.step)
    if boundary:
        progress_mod.record_quietly(config.root, boundary, head=head,
                                    detail=check.found[:120])


def as_json(checks: list[Check], *, base: str, head: str, branch: str,
            title: str) -> str:
    return json.dumps({
        "base": base, "head_sha": head, "branch": branch, "title": title,
        "passed": all(c.ok for c in checks),
        "checks": [{"step": c.step, "ok": c.ok, "checked": c.checked,
                    "found": c.found, **c.extra} for c in checks],
    }, indent=2) + "\n"
