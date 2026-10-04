"""warden deploy: provenance preconditions for the one stage that leaves the repo.

'Humans hold every merge' (graph.py enforces it structurally) must not gain a
side door at deploy time. A commit deploys only if:

- the CHECKOUT IS THE COMMIT — deploy runs the working tree, so HEAD must
  equal `--commit` and the tree must be clean, or the provenance would
  describe code other than what ships (without this, any checkout would
  deploy under an attested commit's label);
- the declared branch carries it — resolved ONLY as `refs/heads/` then
  `refs/remotes/`, so a tag named after the branch can never shadow it;
- a clean committed attestation covers it, read from the COMMIT'S OWN TREE,
  never the filesystem — an uncommitted shard is not evidence.

The one attestation binding is `introduced-by-commit`: the commit's
first-parent diff added a clean-verdict shard under the committed store.
That is what an honestly-gated commit looks like from main: a squash merge
carries the PR's shard in its own diff, and an ingest commit carries the
shard for the commit before it. A `sha == commit` binding read from the
commit's tree is deliberately absent because it is unconstructible — a
shard naming commit X only exists in trees AFTER X (committing the shard
moves head; see attest.check_range), so the tree of X can never contain it.
The shard's own `sha` is NOT constrained beyond shape: after a squash the
sha it names is discarded (and may be gc'd), so any constraint tight enough
to block a forged sha also refuses every honest squash deploy — the fence
against forgery is reachability from the branch where humans hold merge,
recorded in a decision shard.

Everything here is a READ — the scope commands themselves run through
verify.run_scope, the same runner verify uses, never a fork.

Exit-code contract (the CLI's shared one): a determinate refusal is exit 1
and NAMES its reason; a check that cannot run — bad ref, unreadable shard
store, unreportable tree state — raises DeployError, which the CLI maps to
exit 2, never a pass and never a refusal: an indeterminate store must not
fold into a refusal when reachability has already failed, and 'no
attestation' and 'cannot read the store' stay distinct states, the same
split attest.check_range keeps.
"""

import json
import subprocess
from pathlib import Path

from jsonschema import Draft202012Validator

# Reused, not retyped: the committed store deploy reads is the one attest
# writes; two spellings of the path would drift.
from . import memory as memory_mod
from .attest import SHARD_DIR
from .runs import git_env, is_dirty

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "deploy-result.schema.json"


class DeployError(Exception):
    """The provenance check could not run — the CLI fails closed (exit 2)."""


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, env=git_env())
    if proc.returncode != 0:
        raise DeployError(f"git {' '.join(args)} failed: "
                          f"{proc.stderr.strip() or f'exit {proc.returncode}'}")
    return proc.stdout


def _resolve_commit(root: Path, ref: str, what: str) -> str:
    try:
        return _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    except DeployError as e:
        raise DeployError(f"cannot resolve {what} {ref!r}: {e}. The check did "
                          "NOT run — this is not a verdict about the commit") from e


def _resolve_branch(root: Path, branch: str) -> str:
    """The branch tip, resolved ONLY inside the branch namespaces.

    Bare `rev-parse <name>` walks the full gitrevisions precedence, where
    `refs/tags/<name>` outranks `refs/heads/<name>` — so a pushed tag named
    `main` would silently become the reachability target and bypass the
    refusal. A ref given as `refs/heads/...` or `refs/remotes/...` is taken
    literally; any OTHER `refs/...` argument is refused outright, so
    `--branch refs/tags/v1.0` (exactly what $GITHUB_REF holds on a tag push)
    cannot make a tag the reachability target. A bare name must exist as a local or
    remote-tracking branch.
    """
    if branch.startswith("refs/"):
        if not branch.startswith(("refs/heads/", "refs/remotes/")):
            raise DeployError(
                f"--branch {branch!r} is not a branch ref — only "
                "refs/heads/ and refs/remotes/ (or a bare branch name) count "
                "as the deploy branch; tags and other refs deliberately do "
                "not. The check did NOT run")
        candidates = [branch]
    else:
        candidates = [f"refs/heads/{branch}", f"refs/remotes/{branch}"]
    for ref in candidates:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=root, capture_output=True, text=True, env=git_env())
        if proc.returncode == 0:
            return proc.stdout.strip()
    raise DeployError(
        f"cannot resolve branch {branch!r} (tried "
        f"{', '.join(candidates)}) — tags and other refs deliberately do not "
        "count as the deploy branch. The check did NOT run")


def _is_ancestor(root: Path, commit: str, tip: str) -> bool:
    proc = subprocess.run(["git", "merge-base", "--is-ancestor", commit, tip],
                          cwd=root, capture_output=True, text=True,
                          env=git_env())
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise DeployError(f"git merge-base --is-ancestor failed "
                      f"({proc.stderr.strip() or f'exit {proc.returncode}'}) — "
                      "reachability could not be determined")


def _parse_shard(text: str, name: str = "shard") -> dict | None:
    """A shard's (sha, verdict) when readable and attest-sourced, else None
    for a non-attest shard. Raises ValueError when unreadable.

    THE SHARED ENVELOPE DEFINITION, not a hand-roll. This is one of several
    readers of `.warden/memory/attest/**`, and it validates through
    `memory.validate_shard_envelope` exactly as `attest.attested_shas` does.
    A private filter would disagree in the PERMISSIVE direction: a committed
    shard with malformed `records`, no `records` key, or a non-string
    `status`/`verdict` is UNREADABLE to the blocking `attest check`, and must
    not be credited here as a clean attestation for deploy eligibility.
    "Attested" must not mean two things to two commands a consumer runs.
    """
    doc = json.loads(text)
    if not isinstance(doc, dict):
        raise ValueError("shard is not a JSON object")
    # A gate shard is a deterministic checker firing, not the orchestrated
    # review — the same scope filter `attest.attested_shas` applies, and it
    # runs AFTER the envelope check for the same reason it does there: every
    # reader that counts this store validates before it filters.
    memory_mod.validate_shard_envelope(name, doc)
    if doc.get("source", "attest") != "attest":
        return None
    sha = doc.get("sha")
    if not isinstance(sha, str) or not sha:
        raise ValueError("shard names no sha")
    return {"sha": sha, "verdict": doc.get("verdict")}


def _first_parent(root: Path, commit: str) -> str | None:
    """The commit's first parent, or None for a ROOT commit only.

    `rev-list --parents -n 1` prints `<commit> [<parent>...]`, so "no
    parents" is a determinate answer in a successful run; a failed run
    raises through _git. Reading ANY rev-parse failure as "root commit" would
    diff the entire tree as introduced, collapsing an indeterminate state
    into a permissive one.
    """
    parts = _git(root, "rev-list", "--parents", "-n", "1", commit).split()
    if not parts:
        raise DeployError(f"git rev-list --parents printed nothing for "
                          f"{commit[:12]} — parentage could not be determined")
    return parts[1] if len(parts) > 1 else None


def clean_attestation(root: Path, commit: str) -> dict | None:
    """The clean-verdict shard the commit's own diff introduced, or None.

    Read exclusively from git objects (`diff-tree` + `show <commit>:<path>`)
    — never the filesystem, so an uncommitted or untracked shard can never
    satisfy the precondition. The diff is against the FIRST
    parent only, never `-m`: a merge must not be credited with shards other
    PRs added to main. Paths are NUL-terminated so a shard filename with
    non-ASCII characters is not C-quoted into an unreadable path.

    Raises DeployError when nothing matched AND shards were unreadable:
    'no attestation' and 'cannot read the store' must stay distinct states.
    """
    unreadable: list[str] = []
    parent = _first_parent(root, commit)
    range_args = [parent, commit] if parent else ["--root", commit]
    added = _git(root, "diff-tree", "-r", "-z", "--no-commit-id",
                 "--name-only", "--diff-filter=A", *range_args,
                 "--", str(SHARD_DIR))
    # Shards are `*.json` — the same membership rule attest.attested_shas
    # globs. Without the filter, a committed `.gitkeep` or README under the
    # store would be fed to json.loads and reported as an UNREADABLE store
    # (exit 2) where the determinate answer is "no attestation" (exit 1).
    for rel in [p for p in added.split("\0")
                if p and p.endswith(".json")]:
        try:
            parsed = _parse_shard(_git(root, "show", f"{commit}:{rel}"),
                                  Path(rel).name)
        except DeployError:
            # `git show` failed on a path diff-tree just named — that is an
            # unreadable store, not an absent attestation.
            unreadable.append(rel)
            continue
        except ValueError as e:
            unreadable.append(f"{rel} ({e})")
            continue
        if parsed and parsed["verdict"] == "clean":
            return {"shard": Path(rel).name, "sha": parsed["sha"],
                    "via": "introduced-by-commit"}

    if unreadable:
        raise DeployError(
            f"{len(unreadable)} attestation shard(s) could not be read "
            f"({', '.join(unreadable[:3])}) and no clean shard matched — this "
            "check cannot tell 'no attestation' from 'cannot read the store', "
            "so it refuses to guess")
    return None


def check_provenance(root: Path, *, commit_ref: str, branch: str) -> dict:
    """The deploy preconditions for one commit, every refusal named.

    Returns {commit, head, branch, branch_tip, dirty, reachable_from_branch,
    clean_attestation, refusals}. Raises DeployError when a precondition
    could not be evaluated at all — never folding that into a refusal.
    """
    commit = _resolve_commit(root, commit_ref, "commit")
    head = _resolve_commit(root, "HEAD", "HEAD")
    tip = _resolve_branch(root, branch)

    refusals: list[str] = []
    # The checkout IS what deploys (run_scope shells out in the working
    # tree), so the tree must be exactly the commit whose provenance this
    # run certifies — measured BEFORE any scope command runs, since the
    # commands themselves may write files.
    if head != commit:
        refusals.append(
            f"the checkout is {head[:12]}, not {commit[:12]} — deploy runs "
            "the working tree, so HEAD must be the very commit being "
            "deployed; check it out first")
    dirty = is_dirty(root)
    if dirty is None:
        raise DeployError("git cannot report working-tree state — a deploy "
                          "from an unverifiable tree is refused, not assumed "
                          "clean")
    if dirty:
        refusals.append(
            "the working tree is dirty — what would deploy is not the "
            "commit; commit or stash first (the same integrity line "
            "`attest write` holds)")

    reachable = _is_ancestor(root, commit, tip)
    if not reachable:
        refusals.append(
            f"commit {commit[:12]} is not reachable from branch {branch!r} "
            f"(tip {tip[:12]}) — only commits the branch carries deploy; "
            "the gate is a precondition of deploy, not a path around it")

    attestation = clean_attestation(root, commit)
    if attestation is None:
        refusals.append(
            f"no clean committed attestation covers commit {commit[:12]} "
            f"(store: {SHARD_DIR}) — run the pre-pr-review skill, then "
            "`warden memory ingest`, and commit the shard it writes")

    return {"commit": commit, "head": head, "branch": branch,
            "branch_tip": tip, "dirty": dirty,
            "reachable_from_branch": reachable,
            "clean_attestation": attestation, "refusals": refusals}


def build_result(provenance: dict, *, scope: str, rules_version: str,
                 run: dict | None) -> dict:
    """Assemble and schema-validate the deploy-result document."""
    doc = {
        **provenance,
        "scope": scope,
        "rules_version": rules_version,
        "deployed": bool(not provenance["refusals"] and run and run["passed"]),
        "run": run,
    }
    schema = json.loads(_SCHEMA_PATH.read_text())
    Draft202012Validator(schema).validate(doc)
    return doc


def render_refusal(doc: dict) -> str:
    lines = [f"deploy --scope {doc['scope']}: REFUSED — the gate is a "
             "precondition of deploy"]
    lines += [f"  ✗ {reason}" for reason in doc["refusals"]]
    return "\n".join(lines) + "\n"
