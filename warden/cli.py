"""warden CLI dispatch.

Exit codes (shared across subcommands): 0 = clean, 1 = blocking finding or
failed gate, 2 = infra/config error.
"""

import argparse
import os
import sys
import tempfile

import json
from pathlib import Path

import yaml

from . import attest as attest_mod
from . import decide as decide_mod
from . import declared as declared_mod
from . import advisor as advisor_mod
from . import backtest as backtest_mod
from . import audit as audit_mod
from . import catalog as catalog_mod
from . import certify as certify_mod
from . import goal as goal_mod
from . import config as config_mod
from . import deploy as deploy_mod
from . import diffs as diffs_mod
from . import enroll as enroll_mod
from . import explain as explain_mod
from . import github as github_mod
from . import graph as graph_mod
from . import mine as mine_mod
from . import plan as plan_mod
from . import proportion as proportion_mod
from . import plugins as plugins_mod
from . import progress as progress_mod
from . import repair as repair_mod
from . import review as review_mod
from . import rules as rules_mod
from . import runs
from . import ship as ship_mod
from . import take as take_mod
from . import memory as memory_mod
from . import vocabulary as vocabulary_mod
from . import verify as verify_mod
from . import yamlio

# Stamped into a run manifest's rules_version when the version could not be
# computed — whether repo.yaml could not be read at all, or read cleanly and
# the version still would not compute. The suffix is the error class that
# stopped it, so the marker is actionable rather than merely negative. It is
# NEVER a claim either way about enrollment: that is the whole point of keeping
# it distinct from "unenrolled", which is one. For an unreadable repo.yaml,
# enrollment is exactly what could not be determined.
UNCOMPUTABLE_PREFIX = "uncomputable:"


def _cmd_init(args: argparse.Namespace) -> int:
    return enroll_mod.run(Path.cwd())


def _cmd_take(args: argparse.Namespace) -> int:
    return take_mod.run(Path.cwd(), Path(args.source))


def _cmd_explain(args: argparse.Namespace) -> int:
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    # Surface enrollment mistakes here, not at first review: an engine:python
    # rule id must resolve to a core or project checker.
    rules_dir_ = config.root / config.review.rules_dir
    review_mod.validate_registry(rules_mod.load_rules(rules_dir_),
                                 plugins_mod.load_registry(rules_dir_))
    brief = explain_mod.render(config, base=args.base)
    print(brief, end="")
    rules_dir = config.root / config.review.rules_dir
    run_dir = runs.create_run_dir(config.root, "explain")
    (run_dir / "brief.md").write_text(brief)
    runs.write_manifest(run_dir, root=config.root, cmd="explain",
                        rules_version=rules_mod.rules_version(rules_dir, config.root),
                        exit_status="ok")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    if args.check_head is not None:
        # Runs nothing and writes nothing: it answers whether an artifact
        # already on disk stands for this scope on this commit. Exit 1 is
        # "not accepted, run the scope", which is how the hook reads it.
        accepted, why = verify_mod.check_head(config, args.scope, args.check_head)
        verdict = "ACCEPTED" if accepted else "NOT ACCEPTED"
        print(f"verify --scope {args.scope} --check-head "
              f"{args.check_head[:12]}: {verdict} — {why}")
        return 0 if accepted else 1
    outputs: list[str] = []
    doc = verify_mod.run_scope(config, args.scope, outputs=outputs)
    print(verify_mod.render_summary(doc, outputs=outputs), end="")
    rules_dir = config.root / config.review.rules_dir
    run_dir = runs.create_run_dir(config.root, "verify")
    (run_dir / "verify-result.json").write_text(json.dumps(doc, indent=2) + "\n")
    runs.write_manifest(run_dir, root=config.root, cmd="verify",
                        rules_version=rules_mod.rules_version(rules_dir, config.root),
                        exit_status="pass" if doc["passed"] else "fail")
    # A protocol boundary, recorded as it is passed. Quietly:
    # the scope's verdict is this command's answer and a status line must never
    # be able to change it.
    progress_mod.record_quietly(
        config.root, "verify-run", head=doc["head_sha"],
        detail=f"--scope {args.scope}: {'PASS' if doc['passed'] else 'FAIL'}")
    return 0 if doc["passed"] else 1


def _cmd_deploy(args: argparse.Namespace) -> int:
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    # Scope validation FIRST, before any evidence dir exists: an unknown scope
    # is a config error (exit 2 via VerifyError), and it must not leave a
    # run dir implying a deploy was attempted.
    if not config.deploy:
        raise verify_mod.VerifyError(
            "repo.yaml declares no deploy: scopes — nothing to deploy")
    if args.scope not in config.deploy:
        known = ", ".join(sorted(config.deploy))
        raise verify_mod.VerifyError(
            f"unknown deploy scope {args.scope!r} (repo.yaml defines: {known})")
    rules_dir = config.root / config.review.rules_dir
    version = rules_mod.rules_version(rules_dir, config.root)
    # Deploy mutates state, so it writes a run manifest either way — the
    # evidence-dir convention (memory.py): a refusal is evidence too.
    run_dir = runs.create_run_dir(config.root, "deploy")
    try:
        provenance = deploy_mod.check_provenance(
            config.root, commit_ref=args.commit, branch=args.branch)
    except deploy_mod.DeployError as e:
        print(f"warden deploy: {e}", file=sys.stderr)
        runs.write_manifest(run_dir, root=config.root, cmd="deploy",
                            rules_version=version, exit_status="infra_error",
                            extra={"error": str(e)})
        return 2

    if provenance["refusals"]:
        doc = deploy_mod.build_result(provenance, scope=args.scope,
                                      rules_version=version, run=None)
        (run_dir / "deploy-result.json").write_text(
            json.dumps(doc, indent=2) + "\n")
        runs.write_manifest(run_dir, root=config.root, cmd="deploy",
                            rules_version=version, exit_status="refused")
        print(deploy_mod.render_refusal(doc), end="")
        return 1

    run_doc = verify_mod.run_scope(config, args.scope, which="deploy",
                                   stop_on_failure=True)
    doc = deploy_mod.build_result(provenance, scope=args.scope,
                                  rules_version=version, run=run_doc)
    print(verify_mod.render_summary(run_doc, which="deploy"), end="")
    (run_dir / "deploy-result.json").write_text(json.dumps(doc, indent=2) + "\n")
    runs.write_manifest(run_dir, root=config.root, cmd="deploy",
                        rules_version=version,
                        exit_status="pass" if run_doc["passed"] else "fail")
    return 0 if run_doc["passed"] else 1


def _cmd_review(args: argparse.Namespace) -> int:
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    rules_dir = config.root / config.review.rules_dir
    rules = rules_mod.load_rules(rules_dir)
    version = rules_mod.rules_version(rules_dir, config.root)
    run_dir = runs.create_run_dir(config.root, "review")

    ctx = github_mod.from_event(args.event) if args.event else None
    base, head = (ctx.base_sha, ctx.head_sha) if ctx else (args.base, "HEAD")

    try:
        diff_ctx = diffs_mod.get_context(config.root, base, head,
                                         excludes=config.review.context_excludes)
        doc = review_mod.run_review(config, rules, diff_ctx, version,
                                    project_checkers=not args.no_project_checkers)
    except (diffs_mod.DiffError, config_mod.ConfigError) as e:
        print(f"warden review: gate DID NOT RUN: {e}", file=sys.stderr)
        runs.write_manifest(run_dir, root=config.root, cmd="review",
                            rules_version=version, exit_status="infra_error",
                            extra={"error": str(e)})
        if ctx and not args.no_comment:
            github_mod.upsert_sticky_comment(
                ctx, audit_mod.render_infra_failure(str(e), version), config.root)
        return 2

    (run_dir / "review-findings.json").write_text(json.dumps(doc, indent=2) + "\n")
    blocking = review_mod.blocking(config, doc)
    runs.write_manifest(run_dir, root=config.root, cmd="review",
                        rules_version=version,
                        exit_status="blocked" if blocking else "ok",
                        extra={"finding_count": len(doc["findings"]),
                               "blocking_count": len(blocking),
                               "deferred_to_pre_pr": doc["deferred_to_pre_pr"]})

    # Which scopes this diff REQUIRES is read off repo.yaml and the changed
    # paths — never off whichever scope the agent happened to run last.
    # `scope_files`, not `all_files`: rename detection reports a move as its
    # destination alone, so `all_files` can omit the subtree a file LEFT and a
    # scope covering only that subtree would go unrequired.
    scopes = verify_mod.scopes_for_paths(config, diff_ctx.scope_files)
    comment = audit_mod.render(
        doc, audit_mod.verify_for_review(config.root, doc, scopes))
    if ctx and not args.no_comment:
        try:
            github_mod.upsert_sticky_comment(ctx, comment, config.root)
        except github_mod.GitHubError as e:
            # A blocking verdict was reached — a comment-post hiccup must not
            # relabel it as "gate did not run". The artifact
            # carries the findings. A CLEAN review with no posted comment has
            # no durable audit record in CI, so that case stays exit 2.
            if not blocking:
                raise
            print(f"warden review: comment post failed ({e}); "
                  "verdict stands", file=sys.stderr)
    else:
        print(comment, end="")

    if blocking:
        print(f"warden review: {len(blocking)} blocking finding(s)", file=sys.stderr)
        return 1
    return 0


def _classify_range(config, base: str, head: str, *,
                    enforce: bool) -> int:
    """Print the tier for `base..head`, and under --enforce bind the shards.

    Two jobs, deliberately in one command so the number a builder reads is the
    number CI checks.

    REPORTING is always safe: it derives the verdict and prints it file by
    file with the reason each one earned. It never writes and never consults
    anything the builder wrote.

    ENFORCING is the half that has to run in CI. A light round is admitted at
    `warden attest write`, on the builder's machine, against the range as it
    stood then; a builder who then pushes more commits would be carrying a
    light attestation over a range that is no longer inert. So CI recomputes
    the verdict over the PUSHED range and refuses any committed shard whose
    roster is the light crew when that range does not clear the proof. The
    shard's ROSTER is the tier signal, never a marker the shard sets about
    itself: the roster is the work that was done, and a marker is a claim
    about it. This repo already decided that question once, for the cage's
    provenance check — shape, never a marker.
    """
    verdict = proportion_mod.classify(str(config.root), base, head)
    for f in verdict.files:
        mark = "inert " if f.eligible else "FULL  "
        print(f"{mark} {f.path}: {f.reason}")
    print(f"attest classify: {verdict.tier.upper()} — {verdict.reason}")
    if not enforce:
        return 0
    light_roles = _declared_light_roles(config.root)
    if light_roles is None:
        print("attest classify: this repo declares no light round, so there "
              "is nothing to bind", file=sys.stderr)
        return 0
    offenders = _light_shards_in_range(config.root, base, head, light_roles)
    if offenders and not verdict.light:
        for path, sha in offenders:
            print(f"attest classify: {path} attests {sha[:12]} with the light "
                  f"crew ({', '.join(sorted(light_roles))})", file=sys.stderr)
        print("warden attest classify: a committed attestation reviewed this "
              f"range with the light crew, but {base}..{head[:12]} is not "
              f"provably inert — {verdict.reason}. The range grew after that "
              "round, or the round was never entitled to it. Re-review this "
              "head with the full declared crew", file=sys.stderr)
        return 1
    if offenders:
        print(f"attest classify: {len(offenders)} light attestation(s) bound "
              "to a range that is provably inert")
    return 0


def _declared_light_roles(root) -> set[str] | None:
    """The declared light crew's roles, or None when none is declared."""
    try:
        crew = graph_mod.declared_crew(root)
    except graph_mod.GraphError as exc:
        raise attest_mod.AttestError(
            f"graph.yaml's review crew cannot be resolved, so a light roster "
            f"cannot be recognised: {exc}") from exc
    light = (crew or {}).get("light")
    return set(light["roles"]) if light else None


def _light_shards_in_range(root, base: str, head: str,
                           light_roles: set[str]) -> list[tuple[str, str]]:
    """(shard path, attested sha) for every shard committed at `head` whose
    roster is the light crew and whose sha is a commit in `base..head`.

    Read from the tree at `head`, not the working copy: what CI must bind is
    what was pushed.
    """
    commits = set(diffs_mod.run_git(
        root, "rev-list", f"{base}..{head}").split())
    # EVERY shard at head, not only the ones this range adds. `attest check`
    # accepts any shard at head naming a commit in the range, and a shard
    # already in `base`'s tree can: a PR may commit one naming a commit that
    # is not its own — never bound there, since that PR's range does not hold
    # it — and a branch holding that commit then merges it in. Reading only
    # the range's shards let that light attestation through both steps.
    # What made the walk cost more on every PR was a
    # `git show` PER shard, so the whole corpus is now read in one
    # `cat-file --batch` instead: a fixed number of git processes.
    # -z, like every other shard reader here: `ls-tree` QUOTES a path
    # containing whitespace or a quote, and splitting on whitespace then
    # drops it. A shard this listing cannot see is a light attestation the
    # enforcement never binds, while `attest check` reads it perfectly well —
    # so renaming a shard would have been enough to evade this step.
    listing = diffs_mod.run_git(root, "ls-tree", "-r", "-z", head, "--",
                                attest_mod.SHARD_DIR.as_posix())
    # `.json` only, like the sibling reader in attest.py: this walks the whole
    # shard directory at head rather than the range, so a committed README or
    # editor artifact there would otherwise brick the step on every future PR
    # with no way out.
    entries = []
    for line in filter(None, listing.split("\0")):
        meta, _, path = line.partition("\t")
        if path.endswith(".json"):
            entries.append((path, *meta.split()[1:3]))
    try:
        blobs = diffs_mod.git_blobs(
            root, sorted({oid for _, kind, oid in entries if kind == "blob"}))
    except diffs_mod.DiffError as exc:
        raise attest_mod.AttestError(
            f"the shards under {attest_mod.SHARD_DIR.as_posix()} at "
            f"{head[:12]} could not be read ({exc}), so whether one attests "
            "this range with the light crew is unknown") from exc
    out = []
    for path, kind, oid in entries:
        try:
            if kind != "blob":
                raise ValueError(f"a {kind}, not a file")
            doc = json.loads(blobs[oid].decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — cause carried, never dropped
            # An unreadable shard is not an absent one. This step exists to
            # prove no light attestation is riding on a range that cannot
            # carry it, and a shard it could not read is a shard it cannot
            # clear, so it refuses rather than skipping.
            raise attest_mod.AttestError(
                f"{path} is committed under {attest_mod.SHARD_DIR.as_posix()} "
                f"at {head[:12]} but could not be read ({exc}), so whether it "
                "attests this range with the light crew is unknown — and this "
                "check cannot tell 'no light attestation' from 'cannot read "
                "the store'") from exc
        if not isinstance(doc, dict):
            raise attest_mod.AttestError(
                f"{path} is not a JSON object, so the roster that reviewed "
                "this range cannot be read from it")
        sha = doc.get("sha")
        roles = {r.get("role") for r in (doc.get("reviewers") or [])
                 if isinstance(r, dict)}
        if isinstance(sha, str) and sha in commits and roles == light_roles:
            out.append((path, sha))
    return out


def _cmd_attest(args: argparse.Namespace) -> int:
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    rules_dir = config.root / config.review.rules_dir
    version = rules_mod.rules_version(rules_dir, config.root)
    if args.action == "show":
        doc = audit_mod.latest_artifact(config.root, "attest", "attestation.json")
        if doc is None:
            print("warden attest: no attestation on record", file=sys.stderr)
            return 2
        print(attest_mod.render_summary(doc), end="")
        return 0

    if args.action == "classify":
        head = (args.head or github_mod.pr_head_sha_from_env()
                or diffs_mod.run_git(config.root, "rev-parse", "HEAD").strip())
        return _classify_range(config, args.base, head, enforce=args.enforce)

    if args.action == "check":
        # The merge-ref trap: on a pull_request event
        # HEAD is the merge commit actions/checkout built, not the commit CI
        # reports about. Env-driven by default, so a consumer whose workflow is
        # already pinned gets the right head without editing their YAML.
        head = (args.head or github_mod.pr_head_sha_from_env()
                or diffs_mod.run_git(config.root, "rev-parse", "HEAD").strip())
        # AttestError -> exit 2 in main(): "cannot determine" is never
        # reported as "no attestation".
        doc = attest_mod.check_range(config.root, base=args.base, head=head)
        print(attest_mod.render_check(doc), end="")
        if not doc["attested"]:
            print("warden attest check: this PR carries no committed "
                  "attestation", file=sys.stderr)
            return 1
        # The verdict rule: membership proves a review ran on
        # this branch, not that it CLOSED. One attestation covers one round,
        # so an early findings-open shard is expected — what has to be clean
        # is the last attestation on each line of history in the range. Exit
        # 1, not 2: the check ran and its answer is a blocking finding.
        open_verdict = doc["open_verdict"]
        if open_verdict:
            print("warden attest check: the last attestation on a line of "
                  f"{args.base}..{head[:12]} "
                  f"({open_verdict['sha'][:12] or '?'} -> "
                  f"{open_verdict['shard']}) carries verdict "
                  f"'{open_verdict['verdict']}' — the review covering this "
                  "branch has findings open", file=sys.stderr)
            return 1
        # The coverage rule. Membership proved a review ran on
        # this branch and the verdict rule proved the last one CLOSED; neither
        # says anything about what landed after it. A commit no tip
        # attestation is an ancestor-or-self of, touching anything outside the
        # evidence carve-out, is unreviewed content shipping behind an
        # attestation that never read it. Exit 1, not 2: the check ran and its
        # answer is a blocking finding.
        gap = doc.get("coverage_gap") or []
        if gap:
            first = gap[0]
            print(f"warden attest check: {len(gap)} commit(s) in "
                  f"{args.base}..{head[:12]} land after the attestation "
                  f"covering their line of history and change reviewed "
                  f"surface — {first['sha'][:12]} touches "
                  f"{first['outside'][0]}"
                  + (f" (+{len(first['outside']) - 1} more)"
                     if len(first["outside"]) > 1 else "")
                  + ". Re-review this head: run the pre-pr-review skill, "
                  "`warden memory ingest`, and commit the shard",
                  file=sys.stderr)
            return 1
        return 0

    if not args.findings:
        print("warden attest write: --findings is required", file=sys.stderr)
        return 2
    payload = json.loads(Path(args.findings).read_text())
    # An attestation stamps HEAD as "what was reviewed" — a dirty tree means
    # the review saw code that HEAD does not contain.
    if diffs_mod.run_git(config.root, "status", "--porcelain").strip():
        print("warden attest write: working tree is dirty — commit before "
              "attesting so head_sha matches what was reviewed", file=sys.stderr)
        return 2
    head = diffs_mod.run_git(config.root, "rev-parse", "HEAD").strip()
    named_base = diffs_mod.run_git(config.root, "rev-parse", args.base).strip()
    # Refuses a roster it can disprove; stamps `unverified-roster` when no
    # dir is offered. AttestError -> exit 2 in main(), so a
    # disproven roster never reads as "the review found nothing".
    # `is not None`, never a falsy test: `--review-dir ""` (an unset shell
    # variable expanded into the flag) must reach verify_roster's refusal
    # rather than collapse into the "caller did not ask" branch and downgrade
    # to `unverified-roster` at exit 0.
    review_dir = (Path(args.review_dir) if args.review_dir is not None
                  else None)
    # THE BASE COMES FROM THE ROUND, never from re-resolving the name here.
    # Resolved BEFORE the file list and the patch-ids below, because all three
    # describe ONE range: taking them from a second resolution of
    # `origin/main` is how a shard came to declare a range its reviewers never
    # read. `attest.base_binding` carries the live shard that did.
    base, base_state, base_detail = attest_mod.base_binding(
        config.root, review_dir, named_base, head)
    # HELD BACK, not printed here (round 2). Two of these states exist only
    # because a round manifest could not be used, and `round_binding` refuses
    # those documents from inside `build` — AFTER `verify_roster`, which is
    # the order `build` argues for: both can refuse, the roster is the
    # stronger claim, and its message is the one worth reading first.
    # Printing the base reading at this point put the manifest complaint above
    # that refusal on stderr, which is the same reading order by another
    # channel. So it is emitted below, once `build` has returned.
    #
    # NOT "the caller did not name this base": three of the four states below
    # stamp exactly `--base`, and what THOSE THREE share is that warden looked
    # for a round's base and did not take one. The fourth,
    # `BASE_ROUND_DIFFERS`, is the opposite case — it took one, and it is not
    # the one `--base` names. Each is a fact worth a line; neither reading
    # covers both, which is why this says so rather than generalising.
    base_note = (base_detail if base_state not in
                 (attest_mod.BASE_FROM_ROUND, attest_mod.BASE_NO_ROUND)
                 else None)
    # Three-dot, matching what the review actually judged: `warden diff` and
    # the gate both scope to base...head, so a findings set is checked against
    # the same file list the reviewer was shown.
    # NUL-terminated, never quoted: under git's default `core.quotepath` a
    # non-ASCII path lists as `"app/caf\303\251.py"`, which no reviewer
    # output contains — so a clean review of that file was stamped no-review
    # and a declared reviewed-clean refused. `-z` is the read warden/repair.py
    # uses for the same listing.
    changed = diffs_mod.range_paths(config.root, base, head)
    # Patch-ids only: the commit half of each pair is exactly what a rebase
    # invalidates, so recording it would defeat the purpose.
    pairs = attest_mod.range_patch_ids(config.root, base, head)
    # The declared crew: None when graph.yaml has no `review` block (the
    # roster is taken as before). A block that cannot be resolved refuses —
    # the check was declared and cannot run.
    #
    # Resolved on BOTH paths, not under --review-dir only, and the widening is
    # deliberate. `build` compares a roster to this crew only where a round
    # dir was offered — every one of those comparisons needs the minted round
    # number, which only a round dir carries — with ONE exception: the light
    # crew, whose whole premise is that eligibility is recomputed and never
    # asserted. `build` cannot recognise a light roster without the
    # declaration that names it, so withholding the crew here was what let
    # that tier be reached by omitting an argument. WHAT CHANGES for a caller
    # writing without --review-dir: a graph.yaml this repo cannot resolve now
    # refuses the write (exit 2) where it used to stamp `unverified-roster` at
    # exit 0. Fail-closed, and the same direction the --review-dir path has
    # always taken. It does NOT narrow to `graph.declares_review` the way
    # `warden ship` does — which boundary is right for this seam is
    # an open question, and answering it here would decide it in passing.
    review_crew = None
    try:
        review_crew = graph_mod.declared_crew(config.root)
    except graph_mod.GraphError as e:
        raise attest_mod.AttestError(
            f"graph.yaml's review crew cannot be resolved, so the roster "
            f"cannot be checked against it: {e}") from e
    doc = attest_mod.build(payload, head_sha=head, base_sha=base,
                           rules_version=version, rules_dir=rules_dir,
                           pr=args.pr or "", bead=args.bead or "",
                           range_patch_ids=tuple(pid for pid, _ in (pairs or ())),
                           review_dir=review_dir,
                           # the same file list range_binding reads: what
                           # each reviewer's output must name to count as a
                           # review rather than a refusal
                           changed=changed,
                           # paths under any worktree of this checkout are
                           # made repo-relative; other home paths refuse
                           root=config.root,
                           review_crew=review_crew)
    # The ancestry reading, READ OFF THE DOCUMENT rather than recomputed here.
    # It used to be taken above and kept out of the shard on the argument that
    # a reading needing no schema field leaves the HIGH-tier artifact contract
    # untouched. That argument was wrong about which reader matters: the
    # reading is exactly the caveat a later reader of the COMMITTED shard
    # needs, and stderr plus a gitignored manifest reach neither the corpus
    # nor them. `build` stamps it now, on `round_binding`'s precedent, and
    # this is one computation so the printed note, the manifest and the shard
    # cannot disagree about one round.
    ancestry = doc["base_ancestry"]
    if ancestry == "not-ancestor":
        ancestry_note = (
            f"base {base[:12]} is NOT an ancestor of head {head[:12]}, so "
            f"`git diff {base[:12]}..{head[:12]}` over this shard's own "
            "figures reports more than this change. The review judged the "
            "three-dot range, which is what the file list and the patch-ids "
            "recorded below are taken from. The attestation records this "
            "reading, so the committed shard carries the caveat")
    elif ancestry != "ancestor":
        # Its own sentence: a range git could not evaluate must not print as
        # a determination in either direction.
        ancestry_note = (
            f"whether base {base[:12]} is an ancestor of head {head[:12]} "
            "could not be computed, so this run records no reading of it — "
            "not a clean one")
    else:
        ancestry_note = None
    # The two base readings, emitted HERE rather than where they were taken:
    # `build` refuses a disproven roster and an unusable round before this
    # line, so a document that reaches it is one the stronger checks accepted,
    # and neither reading can push their refusal down the terminal (round 2).
    # Both ride the run manifest below whatever the terminal does with them.
    for note in (base_note, ancestry_note):
        if note:
            print(f"warden attest write: {note}", file=sys.stderr)
    # Advisory, never fatal. Printed on stderr AND recorded in the run
    # manifest: stderr assumes a human at a terminal, and the unattended cage
    # has none — a signal that lives only in scrollback is one the run cannot
    # be audited for afterwards. Same shape as verify's `dirty` flag, which
    # travels with the result rather than only warning.
    # Read off `doc`, not the payload: a `file` quoted absolute has been made
    # repo-relative there, and only that spelling can match `changed`.
    kind, warning = attest_mod.range_binding(
        doc["findings"], changed, base, head)
    if warning:
        print(f"warden attest write: {warning}", file=sys.stderr)
    # Advisory, never fatal, and never silent: with no patch-id binding this
    # attestation cannot be matched after a content-preserving rebase, so the
    # next rebase reports no attestation. Recorded in the manifest too, for the
    # same reason the sibling advisory above is — the cage has no terminal.
    patch_binding = ("recorded" if pairs else
                     "empty-range" if pairs == () else "unavailable")
    if patch_binding == "unavailable":
        print("warden attest write: could not compute the range's patch-ids, "
              "so this attestation records no content binding — a later "
              "content-preserving rebase will NOT be able to match it and the "
              "gate will report no attestation", file=sys.stderr)
    # Defence in depth for the round binding.
    #
    # `attest.build` is the stamping authority: it calls `round_binding`
    # itself, AFTER `verify_roster` for the reason that ordering argument gives
    # — both can refuse, the roster is the stronger claim, and its message is
    # the one worth reading when both would fire — and a mismatch raises out of
    # THERE as AttestError -> exit 2, the same exit a disproven roster takes.
    # So the call below is reached only on a document `build` already accepted,
    # and cannot raise on any execution warden itself can produce. It exists to
    # render the human sentence for stderr, which the document carries no room
    # for; the STATE printed and recorded is read off `doc`, never off this
    # call, so nothing downstream depends on the two agreeing.
    _round_state, round_detail = attest_mod.round_binding(review_dir, head)
    if doc["round_binding"] == "unminted":
        print(f"warden attest write: {round_detail}", file=sys.stderr)
    # Same shape, same reason: `build` already refused any carried-forward
    # report, so this cannot raise on a document it accepted.
    # It re-derives the READING for the manifest — the state, and the detail
    # that names how many sibling rounds were compared — because the cage has
    # no terminal and a comparison that left no record cannot be audited for
    # afterwards. Both are recorded below; a state alone would keep the
    # count in scrollback.
    carry_state, carry_detail = attest_mod.carry_forward(
        payload.get("reviewers") or [], review_dir)
    # The chain rule's STATE, re-derived for the manifest exactly as
    # `carry_forward`'s is, and for the same reason: the unattended cage has
    # no terminal, so a non-refusing state that lives only in scrollback
    # cannot be audited for afterwards (round 1, F6).
    #
    # SCOPED THE WAY `build` SCOPES IT, and that is load-bearing. Re-deriving
    # is only ever a READING here; the gate is `build`'s. Called
    # unconditionally, this became a second gate over two paths `build`
    # deliberately excludes — a payload written with no `--review-dir`, where
    # `build` never asks the chain question at all, and a closure round, where
    # `verify_closure` has already applied the stricter rule. The first
    # REFUSED documents `build` had just accepted, which is a gate moved by a
    # commit that said it was recording a state (round 2, R2-0). Under this
    # guard `build` has already run and accepted the identical call, so this
    # one cannot raise.
    if review_dir is not None and "closure_verification" not in doc:
        chain_state = attest_mod.verify_chain(
            payload.get("findings") or [], payload.get("reviewers") or [],
            root=config.root, base_sha=base, head_sha=head,
            review_dir=review_dir)
    else:
        # Not "chained" and not a failure: the question was never asked, and
        # an auditor must not be able to read the two as one.
        chain_state = ("closure-round" if "closure_verification" in doc
                       else "no-review-dir")
    run_dir = runs.create_run_dir(config.root, "attest")
    (run_dir / "attestation.json").write_text(json.dumps(doc, indent=2) + "\n")
    runs.write_manifest(run_dir, root=config.root, cmd="attest",
                        rules_version=version, exit_status=doc["verdict"],
                        extra={"range_binding": kind,
                               "patch_binding": patch_binding,
                               # WHERE the stamped base came from. Manifest
                               # only, for the reason its siblings below are:
                               # a reading that needs no schema field leaves
                               # the HIGH-tier artifact contract untouched,
                               # and a base taken from somewhere other than
                               # the flag is a fact about the RUN that must
                               # outlive the scrollback the unattended cage
                               # does not have. Its ancestry sibling below is
                               # the one exception, and the reason is stated
                               # there: that reading is a fact about the
                               # SHARD, not about the run.
                               "base_binding": base_state,
                               "base_binding_detail": base_detail,
                               # In the manifest AND in the attestation, and
                               # READ OFF THE DOCUMENT rather than recomputed,
                               # for `round_binding`'s reason below: the
                               # manifest is what an auditor reads for a run
                               # that never produced a shard, and one
                               # computation means the two cannot disagree
                               # about one round.
                               "base_ancestry": ancestry,
                               # In the manifest AND in the attestation.
                               # It stays here because the
                               # manifest is what an auditor reads for a run
                               # that never produced a shard; it is READ OFF
                               # THE DOCUMENT rather than recomputed, so the
                               # two can never disagree about one round.
                               "round_binding": doc["round_binding"],
                               # Manifest only, never the attestation: the
                               # refusal is the mechanism, and a reading that
                               # needs no schema field keeps the HIGH-tier
                               # artifact contract untouched.
                               "carry_forward": carry_state,
                               "carry_forward_detail": carry_detail,
                               "chain_verification": chain_state,
                               # Same reason the two advisories above are in
                               # the manifest: the unattended cage has no
                               # terminal, so a signal that lives only in
                               # scrollback cannot be audited afterwards.
                               "roster_verification":
                                   doc["roster_verification"],
                               # Present only on a closure round, for the
                               # reason its siblings above are recorded here:
                               # the unattended cage has no terminal, and a
                               # round minted past the repair cap is the one
                               # an auditor most wants to find a record of.
                               **({"closure_verification":
                                   doc["closure_verification"]}
                                  if "closure_verification" in doc else {})})
    progress_mod.record_quietly(config.root, "round-attested", head=head,
                                detail=f"verdict {doc['verdict']}")
    print(attest_mod.render_summary(doc), end="")
    return 0 if doc["verdict"] == "clean" else 1


def _cmd_ship(args: argparse.Namespace) -> int:
    """The ship tail, in order, failing closed with a named reason at each step.

    Exit 0 every step passed; 1 a step refused; 2 a step could not be evaluated
    (`ShipError`, raised in main()'s handler list). It writes a run dir either
    way, because a refusal is evidence too — the same convention `deploy`
    keeps — and the artifact records which steps ran and what each one found.

    It NEVER merges. There is no merge argv in `warden/ship.py` and no
    `--auto`; merge authority is the founder's, and `tests/test_ship.py`
    asserts the absence mechanically rather than trusting this sentence.
    """
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    rules_dir = config.root / config.review.rules_dir
    version = rules_mod.rules_version(rules_dir, config.root)
    head = diffs_mod.run_git(config.root, "rev-parse", "HEAD").strip()
    branch = ship_mod.current_branch(config)
    run_dir = runs.create_run_dir(config.root, "ship")
    try:
        checks, code = ship_mod.run(
            config, base=args.base, title=args.title,
            body_file=Path(args.body_file) if args.body_file else None,
            remote=args.remote, pr_base=args.pr_base,
            linter=Path(args.commit_lint) if args.commit_lint else None,
            checks_only=args.checks_only)
    except ship_mod.ShipError as e:
        print(f"warden ship: COULD NOT EVALUATE: {e}", file=sys.stderr)
        runs.write_manifest(run_dir, root=config.root, cmd="ship",
                            rules_version=version, exit_status="infra_error",
                            extra={"error": str(e)})
        return 2
    (run_dir / "ship-result.json").write_text(
        ship_mod.as_json(checks, base=args.base, head=head, branch=branch,
                         title=args.title))
    runs.write_manifest(run_dir, root=config.root, cmd="ship",
                        rules_version=version,
                        exit_status="ok" if code == 0 else "blocked",
                        extra={"steps_run": len(checks),
                               "refused_at": next((c.step for c in checks
                                                   if not c.ok), "")})
    return code


def _cmd_progress(args: argparse.Namespace) -> int:
    """Render every unit under a worktree root, or append one boundary.

    `show` needs no repo.yaml and no enrollment: it scans checkouts and reads
    their boundary logs, which is what makes it work for a unit that DIED —
    requiring the unit to be loadable would be requiring it to cooperate.
    """
    if args.progress_cmd == "record":
        config = config_mod.load()
        path = progress_mod.record(config.root, args.boundary,
                                   head=args.head or None, detail=args.detail)
        print(f"warden progress: recorded {args.boundary} in {path}")
        return 0
    root = Path(args.root) if args.root else progress_mod.default_root()
    if not root.is_dir():
        print(f"warden progress: {root} is not a directory, so no unit can be "
              "reported — pass --root <worktree root>", file=sys.stderr)
        return 2
    print(progress_mod.table(root, names=tuple(args.unit or ())), end="")
    return 0


def _cmd_decide(args: argparse.Namespace) -> int:
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    rules_dir = config.root / config.review.rules_dir
    version = rules_mod.rules_version(rules_dir, config.root)
    if args.action == "show":
        doc = audit_mod.latest_artifact(config.root, "decide", "decision.json")
        if doc is None:
            print("warden decide: no decision on record", file=sys.stderr)
            return 2
        print(decide_mod.render_summary(doc), end="")
        return 0

    if args.action == "list":
        # Read the COMMITTED shard store — the compounding record
        # `record` + `memory ingest` write. `show`
        # renders the latest run artifact (pre-commit); this reads what survives.
        decisions = decide_mod.decisions_from_shards(config.root)
        # An unreadable shard is named loudly, never silently dropped so the
        # count reads complete when it is not (the repo's UNREAD-not-zero norm).
        shard_dir = config.root / decide_mod.SHARD_DIR
        total = len(list(shard_dir.glob("*.json"))) if shard_dir.is_dir() else 0
        unreadable = total - len(decisions)
        if unreadable > 0:
            print(f"warden decide list: {unreadable} decision shard(s) could "
                  "not be read — the count below is a floor, not a total",
                  file=sys.stderr)
        print(decide_mod.render_list(decisions), end="")
        return 0

    # action == "record"
    if not args.input:
        print("warden decide record: --input is required", file=sys.stderr)
        return 2
    try:
        payload = json.loads(Path(args.input).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"warden decide: could not read --input ({e})", file=sys.stderr)
        return 2
    # A decision stamps HEAD as the commit it was made against — a dirty tree
    # means head_sha does not match the code the decision reasoned over, the
    # same provenance-integrity check `attest write` makes.
    if diffs_mod.run_git(config.root, "status", "--porcelain").strip():
        print("warden decide: working tree is dirty — commit before recording "
              "so head_sha matches the state decided against", file=sys.stderr)
        return 2
    head = diffs_mod.run_git(config.root, "rev-parse", "HEAD").strip()
    # base defaults to HEAD ('as of this commit'), so base==head is normal and
    # correct — a decision is a ruling, not a review of a range. base_sha is
    # NOT a diff base; nothing computes head..base from it (the schema's
    # base_sha description says so too).
    base = diffs_mod.run_git(config.root, "rev-parse", args.base).strip()
    try:
        doc = decide_mod.build(payload, head_sha=head, base_sha=base,
                               rules_version=version, pr=args.pr or "",
                               bead=args.bead or "")
    except decide_mod.DecideError as e:
        print(f"warden decide: {e}", file=sys.stderr)
        return 2
    run_dir = runs.create_run_dir(config.root, "decide")
    (run_dir / "decision.json").write_text(json.dumps(doc, indent=2) + "\n")
    runs.write_manifest(run_dir, root=config.root, cmd="decide",
                        rules_version=version, exit_status="recorded")
    print(decide_mod.render_summary(doc), end="")
    print("  run `warden memory ingest` to commit it as a decision shard.")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    try:
        packet = plan_mod.build(config, task=args.task, area=args.area or "",
                                given_paths=args.path or (), cwd=Path.cwd())
    except plan_mod.PlanError as e:
        print(f"warden plan: {e}", file=sys.stderr)
        return 2
    print(plan_mod.render(packet), end="")
    rules_dir = config.root / config.review.rules_dir
    run_dir = runs.create_run_dir(config.root, "plan")
    plan_mod.write_packet(run_dir, packet)
    runs.write_manifest(run_dir, root=config.root, cmd="plan",
                        rules_version=rules_mod.rules_version(rules_dir, config.root),
                        exit_status=packet["risk_tier"])
    # The markdown above excerpts each prior; the JSON beside it carries the
    # full text, so stdout says where it went.
    print(f"packet: {run_dir / 'task-packet.json'}")
    return 0


def _cmd_certify_goal(args: argparse.Namespace) -> int:
    """`certify --goal`: the five run-time evidence items, one row each.

    A separate report from the ladder: it reads the corpus the ladder
    deliberately does not, and gates only itself. Exit 1 unless every item
    is PASS; `--report-only` exits 0 whatever the table says, for a CI step
    that posts the table without becoming a gate.
    """
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    doc = goal_mod.run(config.root, ledger=args.ledger)
    print(goal_mod.render(doc), end="")
    rules_dir = config.root / config.review.rules_dir
    run_dir = runs.create_run_dir(config.root, "certify")
    (run_dir / "goal.json").write_text(json.dumps(doc, indent=2) + "\n")
    runs.write_manifest(run_dir, root=config.root, cmd="certify",
                        rules_version=rules_mod.rules_version(rules_dir, config.root),
                        exit_status=f"goal-{doc['passed']}-of-{doc['total']}")
    if args.report_only or goal_mod.all_pass(doc):
        return 0
    failing = ", ".join(f"{i['item']}={i['state']}" for i in doc["items"]
                        if i["state"] != goal_mod.PASS)
    print(f"warden certify: goal not met ({failing})", file=sys.stderr)
    return 1


def _cmd_certify(args: argparse.Namespace) -> int:
    # `--goal` is its own report: the ladder's flags do not apply to it, and
    # its flags do not apply to the ladder, so a mix is a usage error rather
    # than a flag silently ignored.
    if args.goal:
        if args.level is not None or args.capability is not None:
            print("warden certify: --goal takes neither --level nor "
                  "--capability", file=sys.stderr)
            return 2
        return _cmd_certify_goal(args)
    if args.ledger is not None or args.report_only:
        print("warden certify: --ledger and --report-only apply to --goal only",
              file=sys.stderr)
        return 2
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    try:
        doc = certify_mod.run(config.root)
    except certify_mod.CertifyError as e:
        print(f"warden certify: {e}", file=sys.stderr)
        return 2
    print(certify_mod.render(doc), end="")
    rules_dir = config.root / config.review.rules_dir
    run_dir = runs.create_run_dir(config.root, "certify")
    (run_dir / "certification.json").write_text(json.dumps(doc, indent=2) + "\n")
    runs.write_manifest(run_dir, root=config.root, cmd="certify",
                        rules_version=rules_mod.rules_version(rules_dir, config.root),
                        exit_status=f"level-{doc['attained_level']}")
    # An unknown badge name is a usage error and must not hide behind a
    # failing level gate — validate the NAME before either gate returns.
    cap = None
    if args.capability is not None:
        cap = doc.get("capabilities", {}).get(args.capability)
        if cap is None:
            print(f"warden certify: no capability {args.capability!r} is "
                  "declared (known: "
                  + (", ".join(sorted(doc.get("capabilities", {}))) or "none")
                  + ")", file=sys.stderr)
            return 2
    if args.level is not None and doc["attained_level"] < args.level:
        print(f"warden certify: required level {args.level}, attained "
              f"{doc['attained_level']}", file=sys.stderr)
        return 1
    if cap is not None and not cap["earned"]:
        failing = ", ".join(c["id"] for c in cap["checks"] if not c["passed"])
        print(f"warden certify: capability {args.capability!r} not "
              f"earned ({failing})", file=sys.stderr)
        return 1
    return 0


def _cmd_graph(args: argparse.Namespace) -> int:
    # graph render/validate are pure reads: no evidence dir (same convention
    # as memory recall/stats).
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    try:
        doc = graph_mod.load(config.root)
        warnings = graph_mod.validate(doc, config.root)
    except graph_mod.GraphError as e:
        print(f"warden graph: {e}", file=sys.stderr)
        return 2
    if args.action == "crew":
        # Exit 1 for a round past the cap (a well-formed question answered
        # no), 2 for every cause that leaves no crew to resolve.
        if args.light and args.round is not None:
            print("warden graph crew: --light and --round N are different "
                  "questions — the light round is not one of the numbered "
                  "rounds, so a range either earns it or takes them all",
                  file=sys.stderr)
            return 2
        if args.light:
            # The proportionate round's crew. It is asked for by name rather
            # than by number because it is not one of the numbered rounds: a
            # range either earns it or takes them all. Without this the crew
            # was declarable and unreachable — nothing in the protocol could
            # ask who reviews a provably inert change.
            review = doc.get("review") or {}
            light = (graph_mod.resolve_review(doc, config.root).get("light")
                     if review else None)
            if light is None:
                print("warden graph crew: this repo declares no light round "
                      "(graph.yaml review.light), so every change takes the "
                      "numbered rounds", file=sys.stderr)
                return 1
            print(json.dumps({"light": True, "roles": light["roles"],
                              "payload": light["payload"]}, indent=2))
            return 0
        if args.round is None:
            print("warden graph crew: --round N or --light is required",
                  file=sys.stderr)
            return 2
        try:
            resolved = graph_mod.crew(doc, config.root, args.round)
        except graph_mod.CrewPastCap as e:
            print(f"warden graph crew: {e}", file=sys.stderr)
            return 1
        except graph_mod.GraphError as e:
            print(f"warden graph crew: {e}", file=sys.stderr)
            return 2
        print(json.dumps(resolved, indent=2))
        return 0
    if args.action == "authority":
        # Who may merge right now, and on what basis. 2 for every cause that
        # leaves the question unanswerable (no `review` block included) —
        # "could not say" is never printed as an answer.
        try:
            answer = graph_mod.authority(doc, config.root)
        except graph_mod.GraphError as e:
            print(f"warden graph authority: {e}", file=sys.stderr)
            return 2
        print(json.dumps(answer, indent=2))
        return 0
    if args.action == "validate":
        for warning in warnings:
            print(f"warden graph: warning: {warning}")
        print(f"graph OK: {len(doc['nodes'])} node(s), {len(doc['edges'])} "
              f"edge(s), {len(doc.get('memory', []))} memory edge(s)")
        return 0
    print(graph_mod.render(doc), end="")
    return 0


def _default_origin() -> str:
    """Was a human present? Derived, never assumed.

    `--origin` is a claim about the run that produced the evidence, and the
    producer never passes it: a fixed default would stamp every unattended
    ingest, so the corpus would assert a human was watching when none was. Presence is
    a property of the ENVIRONMENT, and the caged session is where it is
    knowable — run.sh exports CAGE_PROJECT into the session it launches.
    An explicit --origin still wins over this.
    """
    return "unattended" if os.environ.get("CAGE_PROJECT") else "interactive"


def _cmd_diff(args: argparse.Namespace) -> int:
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    try:
        pkg = diffs_mod.review_package(config.root, args.base, args.head)
    except diffs_mod.DiffError as e:
        print(f"warden: {e}", file=sys.stderr)
        return 2
    if args.output:
        Path(args.output).write_text(pkg)
        print(f"warden diff: wrote {args.output} "
              f"({len(pkg)} bytes, {args.base}..{args.head})")
    else:
        print(pkg, end="")
    return 0


def _cmd_round(args: argparse.Namespace) -> int:
    """Mint one isolated review round and write its package into it.

    The whole point is that the PATH IS NOT THE AGENT'S TO CHOOSE. `warden
    diff -o <path>` takes a path, and an agent can choose that path badly — a
    fixed name under a shared scratchpad root, which a concurrent session then
    overwrites with a package whose base sha matches. So this subcommand derives the directory,
    creates it with `exist_ok=False`, and prints ONLY the path on stdout, so a
    caller writes `ROUND="$(warden round new --base origin/main)"` and has no
    fixed name to get wrong. Everything a human reads goes to stderr, for the same
    reason.
    """
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    try:
        head = diffs_mod.run_git(config.root, "rev-parse", args.head).strip()
        base_sha = diffs_mod.run_git(config.root, "rev-parse", args.base).strip()
        # The branch that MINTED the round, which is what the name and the
        # manifest mean by `branch` — deliberately HEAD's, not `--head`'s,
        # because a `--head` given as a raw sha has no branch to report and
        # this field exists to make a stray round's origin legible, not to
        # describe the range. Named rather than left to look like an oversight.
        branch = diffs_mod.run_git(config.root, "rev-parse", "--abbrev-ref",
                                   "HEAD").strip()
    except Exception as e:
        print(f"warden round new: cannot resolve the range ({e}) — a round "
              "that cannot name its own base and head has no identity to "
              "bind its reviewers' output to", file=sys.stderr)
        return 2
    # A base that is BEHIND its own upstream is refused before anything is
    # minted. A LOCAL ref such as `main` does not move on fetch while
    # `origin/main` does: the manifest would then
    # records a base describing a state nobody is reviewing against, and
    # `warden round classify`'s subtractions silently assume otherwise with no
    # way to check — the manifests are their only input. So the check lives
    # where the ref is still in hand, and it stops the mint rather than warning
    # about it.
    stale = diffs_mod.stale_base(config.root, args.base)
    if stale is not None:
        name, upstream = stale
        print(f"warden round new: --base {args.base} is behind its upstream "
              f"{name} ({upstream[:12]}), so this round would record a base "
              "nobody is reviewing against — and `warden round classify` "
              "cannot see that from a manifest. Mint against "
              f"the remote-tracking ref ({name}), or fetch and fast-forward "
              f"{args.base} first", file=sys.stderr)
        return 2
    # WHICH round this is, derived from the review chain for this base..head
    # rather than counted here a second way: `repair.round_count` reads the
    # shards COMMITTED in the range and the round manifests, the same chain
    # `warden round classify` proves. It is recorded in this round's own
    # manifest, and `attest write --review-dir` checks the roster's `round`
    # labels against it — without a number warden itself assigned, a label was
    # compared with nothing and a first and only review could label itself
    # round 2 to claim that round's shorter crew. Derived BEFORE anything is
    # created, so a chain that cannot be read leaves no half-minted round
    # behind.
    try:
        count = repair_mod.round_count(config.root, base_sha, head)
    except repair_mod.RepairError as e:
        print(f"warden round new: {e} — the round cannot be numbered, and a "
              "round with no number cannot have its roster checked against "
              "the crew graph.yaml declares", file=sys.stderr)
        return 2
    round_no = count.number
    # EVERY reading behind the number, not just the disagreement: the
    # operator is the one who can tell a shard not committed yet from a chain
    # that lost its rounds to a rebase, or a legacy shard from a roster
    # written without a round, and a number printed without them is how this
    # class of defect hides. `notes` is the whole set, so a reading added
    # there reaches this surface without a second edit here.
    for note in count.notes:
        print(f"warden round new: {note}", file=sys.stderr)
    # The RESOLVED head, never `args.head` again. Resolving twice lets a commit
    # landing between the two calls bind the round to one sha while the package
    # describes another — a round whose manifest and bytes disagree.
    # `base_sha` too, for the reason `head` is resolved once: a ref that moves
    # between the manifest and the package makes them describe different ranges.
    try:
        pkg = diffs_mod.review_package(config.root, base_sha, head)
    except diffs_mod.DiffError as e:
        print(f"warden round new: {e}", file=sys.stderr)
        return 2
    try:
        round_dir = runs.create_round_dir(config.root, branch=branch, head=head)
    except runs.RoundError as e:
        print(f"warden round new: {e}", file=sys.stderr)
        return 2
    runs.write_round_manifest(round_dir, base=args.base, base_sha=base_sha,
                              head=head, branch=branch, round_no=round_no)
    (round_dir / "review-package.md").write_text(pkg)
    print(f"warden round new: minted {round_dir.name} as round {round_no} for "
          f"{args.base}...{head[:12]} ({len(pkg)} bytes); reviewer outputs go "
          f"in {round_dir.name}/reviewers/", file=sys.stderr)
    progress_mod.record_quietly(config.root, "round-minted", head=head,
                                detail=round_dir.name)
    # stdout is the PATH and nothing else — this is a command substitution
    # surface, and a stray word here becomes part of someone's directory name.
    print(round_dir)
    return 0


def _declared_round_cap(root: Path) -> tuple[int | None, str]:
    """The round cap this repo declares, and the surface it came from.

    graph.yaml's `review.cap` first, through `graph.declared_crew` — the
    platform's one reader for it, and the one that refuses a graph.yaml
    DECLARING a `review` block that does not resolve rather than answering
    the permissive "this repo declares no cap". Where no crew is declared,
    repo.yaml's `repair.budget`, which `resolve_review` already calls the
    cap's one source and `certify` already validates. Neither surface
    declares one: `(None, why)`, and the caller says so rather than gating on
    a number nobody wrote down.

    Raises GraphError, or OSError/yaml.YAMLError, when a surface that IS
    there cannot be read. A cap warden could not read is not an absent cap.
    """
    crew = graph_mod.declared_crew(root)
    if crew is not None:
        return crew["cap"], "graph.yaml review.cap"
    doc = yamlio.load(config_mod.read_repo_yaml(root).decode("utf-8"))
    ok, detail = certify_mod.check_repair_budget(doc)
    if ok:
        return doc["repair"]["budget"], "repo.yaml repair.budget"
    return None, (f"graph.yaml declares no `review` block and repo.yaml "
                  f"declares no usable repair budget ({detail})")


def _cmd_round_count(args: argparse.Namespace) -> int:
    """Count the review rounds a range's COMMITTED evidence records.

    THE SEAM THIS EXISTS FOR. The count was derivable from
    the range and computed in exactly one place: `warden
    round new`, on the builder's own machine. A cap enforced only where the
    builder is standing is advice — "hard stops live outside the model" —
    so this is the surface anything holding `base..head` can call, and CI
    calls it on every PR.

    WHICH WAY IT ERRS, stated because a round counter can err in both. A
    HIGHER count buys a later round's SMALLER declared crew; a LOWER one buys
    repair budget. The number gated on here is `capped_rounds`: the highest
    round a NON-closure committed shard records, ANYWHERE this count looked
    — the range AND every `--prior-head`. Folding the prior heads into the
    VERDICT is deliberate rather than an overreach (round-1 F-2 caught five
    surfaces claiming the opposite): if a head the rewrite left behind only
    reported, dropping the commits would still buy the budget back, which is
    what a history rewrite would otherwise buy. It is a FLOOR read off evidence the branch
    itself committed, so it understates — a branch that commits no shard has
    no floor at all — and it can only fire on the branch's own admission that
    it ran more rounds than the budget allows. That is why exit 1 is a
    REFUSAL and not a report: nothing here can invent a round the branch did
    not commit evidence for. The readings that are facts about the evidence
    rather than breaches of the budget — the two chains disagreeing, shards
    recording no round, and the rewrite report itself — are PRINTED and exit
    0 on their own.

    THE HEAD IS THE PR'S, NEVER THE MERGE REF (round-1 F-7). On a
    `pull_request` event `actions/checkout` leaves `refs/pull/N/merge`
    checked out, whose FIRST parent is the base tip — and `_added_shards`
    walks first-parent, so a bare `rev-parse HEAD` made the committed floor 0
    for every PR and this gate could not fire in the one place it exists to
    run. Measured: a branch carrying rounds 1-3 against a cap of 2 exited 0
    off the merge ref and 1 off the PR head. Resolved the way `attest check`
    and `verify` already resolve it — env-driven, so a consumer whose
    workflow is already pinned gets the right head without editing their
    YAML, and an explicit `--head` still wins.

    The closure round is excepted by its own artifact, never by arithmetic:
    `closure_verification` is stamped only by `attest write` and only after
    proving the payload raised nothing new, and graph.yaml declares that
    round past the cap. Counting it against the budget would fail every
    branch that used the closure-round seam built to rescue them.

    Exit 0 at or under the cap (or with no cap declared), 1 over it, 2 when
    the chain, a prior head or a declared cap cannot be read.
    """
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    try:
        head = diffs_mod.run_git(
            config.root, "rev-parse",
            args.head or github_mod.pr_head_sha_from_env() or "HEAD").strip()
        base_sha = diffs_mod.run_git(config.root, "rev-parse",
                                     args.base).strip()
    except Exception as e:
        print(f"warden round count: cannot resolve the range ({e}) — a range "
              "warden cannot resolve is not a range with no rounds in it",
              file=sys.stderr)
        return 2
    try:
        count = repair_mod.round_count(config.root, base_sha, head,
                                       prior_heads=tuple(args.prior_head or ()))
    except repair_mod.RepairError as e:
        print(f"warden round count: {e}", file=sys.stderr)
        return 2
    if args.cap is not None:
        cap, source = args.cap, "--cap"
    else:
        try:
            cap, source = _declared_round_cap(config.root)
        except (graph_mod.GraphError, OSError, yaml.YAMLError) as e:
            print(f"warden round count: the declared round cap could not be "
                  f"read ({e}), so this range cannot be measured against it "
                  "— a cap warden could not read is not an absent cap",
                  file=sys.stderr)
            return 2
    # EVERY reading, for the reason `round new` prints them all: the operator
    # is the one who can tell a shard not committed yet from a chain that
    # lost its rounds to a rebase, and a number printed alone is how this
    # class of defect hides.
    for note in count.notes:
        print(f"warden round count: {note}", file=sys.stderr)
    report = {
        "base": base_sha, "head": head,
        "number": count.number,
        "capped_rounds": count.capped_rounds,
        "from_shards": count.from_shards,
        "from_rounds_dir": count.from_rounds_dir,
        "from_prior_heads": count.from_prior_heads,
        "shards": [{"path": p, "round": n} for p, n in count.shards],
        "closure": list(count.closure),
        "unrecorded": list(count.unrecorded),
        "prior_heads": [
            {"head": r.head, "rounds": r.rounds,
             "shards": [{"path": p, "round": n} for p, n in r.shards],
             "closure": list(r.closure)}
            for r in count.prior_heads],
        "disagreement": count.disagreement,
        "rewrite": count.rewrite,
        # Shards readable at head that no counted commit added and that the
        # base branch does not carry. A REPORT, and never part of
        # `capped_rounds` — the count stays keyed on the ADD.
        "uncounted": list(count.uncounted),
        "cap": cap, "cap_source": source,
    }
    print(json.dumps(report, indent=2))
    if cap is None:
        print(f"warden round count: no cap is enforced here — {source}. The "
              f"range records {count.capped_rounds} review round(s) and "
              "nothing was measured against them", file=sys.stderr)
        return 0
    if count.capped_rounds > cap:
        # EVERY shard behind the number, from wherever it was read, and ONLY
        # the ones the number was read from. Naming just `count.shards`
        # printed "Rounds counted: none" beside "records 3 round(s)" whenever
        # the verdict came from a prior head (round-1 F-5); naming a prior
        # head's shards without ITS exemptions then printed a shard the count
        # had exempted under "Rounds counted", beside a clause saying none
        # was counted (round-2 F-8). Both are the same defect — a leading
        # line that sends the author to the wrong shard — so the exemption
        # travels with the shard and the paths are DEDUPED: a witness that is
        # an ancestor carries the range's shards too, and listing one path
        # twice is a second way to misdescribe the same evidence.
        counted: list[str] = []
        exempted: list[str] = []
        seen: set[str] = set()
        for path, n in count.shards:
            seen.add(path)
            (exempted if path in count.closure else counted).append(
                path if path in count.closure else f"{path} (round {n})")
        for reading in count.prior_heads:
            where = f"at {reading.head[:12]}, a head this change pushed earlier"
            for path, n in reading.shards:
                if path in seen:
                    continue
                seen.add(path)
                if path in reading.closure:
                    exempted.append(f"{path} ({where})")
                else:
                    counted.append(f"{path} (round {n}, {where})")
        exempt = (f"Closure shard(s) exempted: {', '.join(exempted)}"
                  if exempted else
                  "A closure round is exempt from the cap; none was counted "
                  "here")
        gap = (f" The two chains disagree: {count.disagreement}"
               if count.disagreement else "")
        print(f"warden round count: the COMMITTED evidence for this change "
              f"records {count.capped_rounds} review round(s), over the cap "
              f"of {cap} ({source}). Read from: the shards in "
              f"{base_sha[:12]}..{head[:12]} record {count.from_shards}, the "
              f"local rounds directory holds {count.from_rounds_dir}, and "
              f"heads this change pushed earlier carry "
              f"{count.from_prior_heads}.{gap} Rounds counted: "
              f"{', '.join(counted) or 'none'}. {exempt}", file=sys.stderr)
        return 1
    print(f"warden round count: round {count.number} is next; "
          f"{count.capped_rounds} review round(s) recorded, cap {cap} "
          f"({source})", file=sys.stderr)
    return 0


def _cmd_round_classify(args: argparse.Namespace) -> int:
    """Say whether this repair round counts against the cap.

    The builder never classifies its own diff: both ranges come from round
    manifests warden wrote, and the findings from the payload `attest write`
    consumes. Exit codes are the contract — 0 the loop ends, 1 the round
    counts, 2 refused — and 2 is never folded into a verdict: a chain warden
    cannot prove, or a payload it cannot read, is not a stop condition met.
    Writes no run dir: it reads evidence, it does not produce any.
    """
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    try:
        payload = json.loads(Path(args.findings).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"warden round classify: could not read --findings ({e})",
              file=sys.stderr)
        return 2
    try:
        findings = repair_mod.findings_from(payload)
        # rules_dir and blocking_severities are NOT taken from `config`,
        # which is the working tree's repo.yaml: the verdict is about the
        # round head, so both are read from the repo.yaml committed there.
        report = repair_mod.classify_at_head(
            config.root, [Path(d) for d in args.round], findings)
    except repair_mod.RepairError as e:
        print(f"warden round classify: {e}", file=sys.stderr)
        return 2
    print(repair_mod.render(report), end="")
    # The verdict is RECORDED, not computed and discarded: a
    # checker whose result nothing keeps is a checker the builder can decline
    # to run. It lands in the latest round directory given — the round it
    # classified — beside that round's `round.json`, and the path is named on
    # stdout so the caller does not have to know where it went. A record that
    # cannot be written does NOT change the verdict: the stop condition was
    # computed from evidence warden wrote, and losing the copy is a reporting
    # failure, said out loud on stderr, not a re-classification.
    latest = repair_mod.round_at(config.root, [Path(d) for d in args.round],
                                 report.heads[-1])
    if latest is None:
        # Cannot happen from `classify` — every head in the report came from one
        # of these manifests — so it is reported rather than guessed at: writing
        # the record into an arbitrary round would bind a verdict to a round it
        # is not about.
        print("warden round classify: no round given names the chain's latest "
              f"head {report.heads[-1][:12]}, so the verdict has no round to be "
              "recorded in; the classification stands, the record does not",
              file=sys.stderr)
    else:
        try:
            path = repair_mod.record(report, latest, findings)
            print(f"  recorded: {path}")
        except OSError as e:
            print(f"warden round classify: the verdict could not be recorded "
                  f"({e}) — the classification stands, the record does not",
                  file=sys.stderr)
    return repair_mod.exit_code(report)


def _cmd_declare(args: argparse.Namespace) -> int:
    """Does the gate's behaviour match what it declares?

    Five checks, each comparing a declaration to the behaviour it governs. Exit
    0 clean, 1 drift, 2 a check that could not evaluate — and a 2 is never read
    as clean, for the same reason `round classify`'s is not a stop condition
    met. Writes no run dir: it reads declarations, it produces no evidence of
    its own.

    D-02 needs a scratch directory because the only honest way to answer "is
    this input hashed" is to change it and recompute — so it copies the ruleset
    and repo.yaml into a temp tree and perturbs THAT, never the real one.
    """
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    with tempfile.TemporaryDirectory(prefix="warden-declare-") as scratch:
        try:
            report = declared_mod.run(config.root, Path(scratch))
        except declared_mod.DeclareError as e:
            print(f"warden declare check: {e}", file=sys.stderr)
            return 2
    print(declared_mod.as_json(report) if args.json
          else declared_mod.render(report), end="")
    return declared_mod.exit_code(report)


def _cmd_skills(args: argparse.Namespace) -> int:
    """Pre-flight the skill pack's declared harness assumptions:
    every sibling skill a pack skill names — declared in
    its `assumes:` block or referenced in prose — must resolve against the
    pack being checked (the repo's pack source by default; point --pack at
    the installed plugin cache to check what the harness actually serves).
    Fails closed: 2 when there is no pack to check OR the pack directory
    serves no skills (the check examined nothing, which must never read as
    a pass), 1 when an assumption is stale or unreadable."""
    from pathlib import Path

    from . import packpin as packpin_mod
    from . import provenance as provenance_mod
    config = config_mod.load()
    if args.action == "pin":
        # Is the pack THIS MACHINE serves the one repo.yaml pins? Reads the
        # installer's own records; 0 pinned, 1 not, 2
        # when a record cannot be read — never a pass.
        config_mod.enforce_platform_pin(config)
        if config.platform_pin is None:
            print("warden skills pin: repo.yaml declares no platform.pin — there "
                  "is no tag to hold the pack to", file=sys.stderr)
            return 2
        where = (Path(args.config_dir) if args.config_dir
                 else packpin_mod.default_config_dir())
        status, detail = packpin_mod.check(where, config.platform_pin)
        print(f"warden skills pin: {detail}",
              file=sys.stdout if status == packpin_mod.PINNED else sys.stderr)
        return packpin_mod.EXIT[status]
    pack = (Path(args.pack) if args.pack
            else config.root / "skills" / "nightgate-skills" / "skills")
    rows = provenance_mod.preflight_assumptions(pack)
    pack_rows = [r for r in rows if r["kind"] == "pack"]
    if pack_rows:
        for r in pack_rows:
            print(f"warden skills preflight: {r['detail']} ({r['name']})",
                  file=sys.stderr)
        print("warden skills preflight: the pre-flight DID NOT RUN, which "
              "is not a pass (pass --pack to point at the pack's skills/ "
              "directory)", file=sys.stderr)
        return 2
    print(provenance_mod.render_preflight(rows), end="")
    return 1 if provenance_mod.preflight_problems(rows) else 0


def _cmd_check_vocabulary(config: config_mod.RepoConfig) -> int:
    """`memory check-vocabulary`: the drift ceiling with a verdict.

    Its own function and its own exit-code contract, kept apart from
    `_cmd_memory`'s ingest branch on purpose: ingest's exit code is spoken
    for (`cage/run.sh` reads it as "was the corpus fed"), and the ceiling
    needed a code that means ONE thing — 0 holds, 1 breached, 2 could not
    evaluate. See warden/vocabulary.py.
    """
    doc = vocabulary_mod.check(config.root)
    rules_dir = config.root / config.review.rules_dir
    # The label is a LITERAL: tests/test_doc_fidelity.py derives the run-dir
    # writer set from these call sites and refuses a label it cannot read.
    run_dir = runs.create_run_dir(config.root, "memory-check-vocabulary")
    (run_dir / vocabulary_mod.ARTIFACT).write_text(
        json.dumps(doc, indent=2) + "\n")
    # exit_status is the verdict word, not "ok": the unattended cage has no
    # terminal, and a manifest that says ok for a breach cannot be audited.
    runs.write_manifest(run_dir, root=config.root, cmd="memory-check-vocabulary",
                        rules_version=rules_mod.rules_version(rules_dir,
                                                              config.root),
                        exit_status=doc["status"])
    print(vocabulary_mod.render_report(doc), end="")
    verdict = vocabulary_mod.render_verdict(doc)
    if verdict:
        print(verdict, end="", file=sys.stderr)
    return vocabulary_mod.exit_code(doc)


def _cmd_memory_watch(config, args: argparse.Namespace) -> int:
    """`memory watch`: compare the corpus now against the last committed watch
    record. Exit 0 quiet, 1 transitions, 3 UNREAD. Writes nothing unless
    `--record`, and then only the dated record, for a human to commit."""
    from . import watch as watch_mod
    result = watch_mod.watch(config.root)
    if args.record:
        if result["status"] == "unread":
            print("warden memory watch: refusing to --record an UNREAD state "
                  "— a record of what could not be read would become the "
                  "next run's baseline", file=sys.stderr)
        else:
            try:
                drift = watch_mod.uncommitted_inputs(config.root)
            except watch_mod.WatchUnread as e:
                # Unreadable is not comparable: it refuses through the same
                # arm rather than reading as a clean tree.
                drift = watch_mod.InputDrift(
                    [], [("the watched inputs", str(e))], 0)
            if not drift.clean:
                print(json.dumps(result, indent=2) + "\n" if args.json
                      else watch_mod.render(result), end="")
                print("warden memory watch: refusing to --record while the "
                      "inputs this state was read from are not all "
                      "committed — the record would hold a state CI's "
                      "checkout cannot see. "
                      + watch_mod.describe_drift(drift),
                      file=sys.stderr)
                return 2
            try:
                path = watch_mod.write_record(config.root, result["state"])
            except FileExistsError as e:
                print(f"warden memory watch: {e.filename} already exists — "
                      "run --record again in a second", file=sys.stderr)
                return 2
            result["recorded"] = path.relative_to(config.root).as_posix()
    print(json.dumps(result, indent=2) + "\n" if args.json
          else watch_mod.render(result), end="")
    return watch_mod.exit_code(result)


def _cmd_memory(args: argparse.Namespace) -> int:
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    if args.record and args.action != "watch":
        print("warden memory: --record belongs to `memory watch`",
              file=sys.stderr)
        return 2
    if args.action == "watch":
        return _cmd_memory_watch(config, args)
    if args.action == "check-vocabulary":
        return _cmd_check_vocabulary(config)
    if args.action == "ingest":
        rules_dir = config.root / config.review.rules_dir
        result = memory_mod.ingest(config.root, rules_dir=rules_dir,
                                   origin=args.origin)
        # The drift ceiling is REPORTED here and enforced elsewhere, because
        # `cage/run.sh` gates the publication of the run's attest shard on
        # THIS exit code, reading a non-zero as "the corpus was not fed". A
        # vocabulary breach feeds the corpus perfectly and then fails — so the
        # shard would be silently dropped, the PR would go red two hops away on
        # "commit carries no attestation", and the run whose review coined the
        # drifting name is exactly the run whose evidence is discarded.
        # `retro/SKILL.md` reads the same code as "the corpus is unread" and
        # would halt the one ritual that clears a breach. Ingest keeps its
        # meaning; the reading travels in `ingest-result.json` and on stderr;
        # the obligation is ENFORCED by `warden memory check-vocabulary`, which
        # has its own exit-code contract and runs from a pull_request job (this
        # repo additionally pins the declared value in a test). NOT the
        # manifest: it carries no ceiling field.
        ceiling = result.get("tag_ceiling") or {}
        run_dir = runs.create_run_dir(config.root, "memory-ingest")
        (run_dir / "ingest-result.json").write_text(json.dumps(result, indent=2) + "\n")
        runs.write_manifest(run_dir, root=config.root, cmd="memory-ingest",
                            rules_version=rules_mod.rules_version(rules_dir, config.root),
                            exit_status="ok",
                            # Every reading, not only the warnings: the
                            # unattended cage has no terminal, and a manifest
                            # that records only problems cannot tell a clean
                            # sweep from one that could check nothing.
                            extra={"range_bindings": [
                                {"artifact": b["artifact"], "kind": b["kind"]}
                                for b in result.get("range_bindings", [])]})
        print(f"memory ingest: {len(result['new_shards'])} new shard(s), "
              f"{result['total_records']} record(s) in cache"
              # The cache row count and the finding count are different
              # numbers on purpose; printing the bridge means a reader who
              # cross-checks this against `memory stats` is not left to guess
              # which of the two moved.
              + (f" ({result['restatements_folded']} restatement(s) fold to "
                 f"{result['total_records'] - result['restatements_folded']} "
                 "finding(s) — what `memory stats` counts)"
                 if result.get("restatements_folded") else ""))
        if result.get("new_decisions"):
            print(f"memory ingest: {len(result['new_decisions'])} new decision "
                  "shard(s) committed")
        for skip in result.get("skipped_decisions", []):
            print(f"memory ingest: SKIPPED decision artifact — {skip}",
                  file=sys.stderr)
        if result.get("cache_tracked"):
            print("warden memory ingest: .warden/memory/findings.jsonl is "
                  "COMMITTED — it is a derived cache rebuilt from the shards, "
                  "so every branch that ingests will conflict with every other. "
                  "Add it to .gitignore and run: git rm --cached "
                  ".warden/memory/findings.jsonl", file=sys.stderr)
        if result.get("gate_tracked"):
            print("warden memory ingest: .warden/memory/gate/ has COMMITTED "
                  "shards — they are local working state the next review "
                  "re-derives, and validating a gate rule with a mutation "
                  "proof writes them for "
                  "defects that were never real. A committed one files a "
                  "synthetic finding into the corpus. Rung R-11 fails "
                  "certification at Level 3. Add .warden/memory/gate/ to "
                  ".gitignore and run: git rm -r --cached "
                  ".warden/memory/gate/", file=sys.stderr)
        for tag in result.get("unknown_tags", []):
            print(f"warden memory ingest: tag {tag!r} is not in the declared "
                  "vocabulary (.warden/memory/tags.yaml) — declare it or use "
                  "an existing tag; a split key splits recall", file=sys.stderr)
        for skip in result["skipped"]:
            print(f"memory ingest: SKIPPED malformed artifact — {skip}",
                  file=sys.stderr)
        for moved in result.get("migrated_rule_ids", []):
            print(f"warden memory ingest: MIGRATED legacy rule_id — a "
                  f"declared rule answers it (covers:), so the record lands "
                  f"under that rule with the legacy id kept as provenance — "
                  f"{moved}", file=sys.stderr)
        for gone in result.get("unreachable", []):
            print(f"warden memory ingest: SKIPPED an artifact on an "
                  f"unreferenced commit — nobody auditing the record could "
                  f"resolve the sha it names. Expected after a mutation "
                  f"proof, a reverted probe, or a squash-merged branch; "
                  f"re-run on a real commit if it was real — {gone}",
                  file=sys.stderr)
        for doubtful in result.get("undetermined", []):
            print(f"warden memory ingest: reachability UNDETERMINED for an "
                  f"attestation — ingested anyway. A gate record the next "
                  f"review re-derives fails closed here; a review round does "
                  f"not, because no re-run reproduces a judgment — "
                  f"{doubtful}", file=sys.stderr)
        for binding in [b for b in result.get("range_bindings", [])
                        if b.get("message")]:
            # Advisory, never fatal — the writer only warns, and refusing at
            # the reader would be strictly worse. Printed AND recorded in the
            # manifest below, for
            # the reason `attest write` records its own: the unattended cage
            # has no terminal, so a signal living only in scrollback cannot be
            # audited afterwards.
            print(f"warden memory ingest: RANGE BINDING "
                  f"{binding['kind']} for {binding['artifact']} — the shard "
                  f"was still filed. {binding['message']}", file=sys.stderr)
        for unarmed in result.get("marker_failures", []):
            print(f"warden memory ingest: GUARD NOT ARMED — {unarmed}",
                  file=sys.stderr)
        for other in result.get("elsewhere", []):
            print(f"warden memory ingest: NOT RE-FILED — this run dir was "
                  f"already sharded, and that shard is not in this branch's "
                  f"store. Re-minting it would file another branch's review "
                  f"event into this one's range, where `attest check` would "
                  f"credit a review that never ran here — {other}",
                  file=sys.stderr)
        for bad in result.get("invalid_rule_ids", []):
            print(f"warden memory ingest: REJECTED artifact — its rule_id "
                  f"does not resolve against {rules_dir}, so `attest write` "
                  f"would have refused it too; the promotion gate keys on "
                  f"rule_id and cannot see a rule that does not exist — {bad}",
                  file=sys.stderr)
        # An UNKNOWN tag stays a warning above: a new defect class must be
        # nameable the moment it is found. A breach of a DECLARED ceiling is
        # the different thing — a name in the corpus with no recorded
        # disposition at all — and it is reported here, loudly, without
        # changing what this command's exit code means (see above).
        # `labelled`, not `complaints`: `ceiling_status` merges four sources
        # and only one of them is the ceiling. One blanket ceiling prefix would
        # tell a repo with no ceiling and one bad `left_undeclared:` receipt
        # that its ceiling was declared and unreadable. The label is attached
        # at the merge site, where the source is known, and printed verbatim
        # here.
        for problem in (ceiling.get("labelled")
                        or ceiling.get("complaints", ())):
            print(f"warden memory ingest: {problem}", file=sys.stderr)
        if ceiling.get("breached"):
            print(f"warden memory ingest: TAG CEILING BREACHED — "
                  f"{len(ceiling['undecided'])} tag(s) in the corpus have no "
                  f"recorded disposition, against a declared ceiling of "
                  f"{ceiling['ceiling']}: "
                  f"{', '.join(ceiling['undecided'])}. Declare each in "
                  f".warden/memory/tags.yaml, fold it, or record why it is "
                  f"left under 'left_undeclared:'. This is REPORTED, not "
                  f"enforced here — `warden memory check-vocabulary` is the "
                  f"step that fails.",
                  file=sys.stderr)
        return 0
    if args.action == "recall":
        # recall/stats are pure reads: no evidence dir (see memory.py docstring)
        if not args.files:
            print("warden memory recall: --files is required", file=sys.stderr)
            return 2
        if not args.for_role:
            print("warden memory recall: --for reviewer|examiner is required "
                  "(split memory — the roles get disjoint priors)", file=sys.stderr)
            return 2
        # A caller quoting bug (a whole file list passed as ONE argument)
        # matches nothing and reads exactly like a genuinely empty corpus.
        # Warn, never fail: a path deleted by the diff
        # under review is a legitimate recall target. Absolute paths under
        # the root are relativized — recall's dir_prefix keys are
        # repo-relative, so the raw absolute string could never match and
        # its silence would read as empty history too.
        query_files = []
        for f in args.files:
            path = Path(f)
            if path.is_absolute():
                try:
                    f = str(path.relative_to(config.root))
                except ValueError:
                    try:
                        f = str(path.resolve().relative_to(
                            config.root.resolve()))
                    except ValueError:
                        print(f"warden memory recall: --files argument {f!r} "
                              "is an absolute path outside this repo — "
                              "recall keys are repo-relative, so it can "
                              "never match history", file=sys.stderr)
                        query_files.append(f)
                        continue
            if not (config.root / f).exists():
                print(f"warden memory recall: --files argument {f!r} does "
                      "not exist in the working tree — if it is not a file "
                      "this diff deletes, the argument is likely a caller "
                      "quoting bug (a whole list passed as one argument "
                      "silently reads as empty history)", file=sys.stderr)
            query_files.append(f)
        records = memory_mod.recall(config.root, files=query_files,
                                    role=args.for_role, tags=args.tags,
                                    rules=args.rules)
        print(memory_mod.render_recall(records, args.for_role), end="")
        # `recall` reads the SAME derived cache `stats` does, and its empty
        # answer is the one a builder acts on — "no relevant history" for a
        # fresh clone holding unread shards is indistinguishable from a corpus
        # with nothing to say. The signal goes where the reader is.
        stale = memory_mod.cache_provenance(config.root)
        if stale.get("problem"):
            print(f"warden memory recall: STALE CORPUS — {stale['problem']}.",
                  file=sys.stderr)
        return 0
    # The provenance line goes FIRST and the warning to stderr, for the reason
    # `attest write` records its own signals twice: the reader of a precision
    # row needs to know which corpus produced it BEFORE reading it, and a stale
    # cache is a thing a human must act on rather than a note in scrollback.
    provenance = memory_mod.cache_provenance(config.root)
    print(memory_mod.render_corpus_provenance(provenance), end="")
    print(memory_mod.render_stats(memory_mod.stats(
        config.root, config.root / config.review.rules_dir)), end="")
    if provenance.get("problem"):
        print(f"warden memory stats: STALE CORPUS — {provenance['problem']}.",
              file=sys.stderr)
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    review_doc = audit_mod.latest_artifact(config.root, "review", "review-findings.json")
    # Paired on the reviewed commit AND on the scopes its diff requires.
    # Re-deriving the changed paths from the review artifact's
    # own range is the only way audit can ask that question — it holds no
    # DiffContext. A range git cannot resolve yields no paths, which
    # `scopes_for_paths` fails closed into "every declared scope".
    if review_doc is not None:
        try:
            files = diffs_mod.changed_files(config.root, review_doc["base_sha"],
                                            review_doc["head_sha"])
        except diffs_mod.DiffError:
            # None, not (): "could not compute the diff" and "the range
            # changed nothing" take the same safe answer but are not the same
            # statement, and the signature keeps them apart.
            files = None
        verify_docs = audit_mod.verify_for_review(
            config.root, review_doc, verify_mod.scopes_for_paths(config, files))
    else:
        # With no review to anchor to there is nothing to mispair, so the
        # latest verify stands alone — labelled with the commit and the scope
        # it actually judged.
        latest = audit_mod.latest_artifact(config.root, "verify",
                                           "verify-result.json")
        verify_docs = ({str(latest.get("scope") or "unknown"): latest}
                       if latest else {})
    comment = audit_mod.render(review_doc, verify_docs)
    if args.event:
        github_mod.upsert_sticky_comment(github_mod.from_event(args.event), comment,
                                         config.root)
    else:
        print(comment, end="")
    return 0


def _cmd_rules_lifecycle(config, rules_dir, records, corpus_unread,
                         window) -> int:
    """The subtractive half of the advisor: the rules this
    repo ALREADY has, read against their own record.

    Exits 0 on a computed report and 2 when the ruleset could not be read —
    the same fail-closed direction `recommend` takes, and for the same reason:
    an empty lifecycle report would read as "every rule is healthy".
    """
    from . import lifecycle as lifecycle_mod

    report = lifecycle_mod.rule_lifecycle(
        config.root, records=records, rules_dir=rules_dir,
        corpus_unread=corpus_unread,
        context_excludes=config.review.context_excludes,
        window=window if window is not None else backtest_mod.DEFAULT_WINDOW)
    print(lifecycle_mod.render(report), end="")

    run_dir = runs.create_run_dir(config.root, "rules-lifecycle")
    (run_dir / "rule-lifecycle.json").write_text(
        json.dumps(report.to_dict(), indent=2) + "\n")
    status = ("computed" if report.computed else "ruleset_unreadable")
    runs.write_manifest(run_dir, root=config.root, cmd="rules-lifecycle",
                        rules_version=(rules_mod.rules_version(rules_dir, config.root)
                                       if rules_dir.is_dir() else ""),
                        exit_status=status)
    if not report.computed:
        print("warden rules lifecycle: the ruleset could not be read, so no "
              "rule was judged. This is not a clean report.", file=sys.stderr)
        return 2
    return 0


def _cmd_rules(args: argparse.Namespace) -> int:
    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    rules_dir = config.root / config.review.rules_dir
    corpus_unread = ""
    try:
        records = memory_mod.records_from_shards(config.root)
    except ValueError as e:
        # A corpus we cannot read makes every entry look unevidenced, which
        # would relabel real evidence as prior art — the one confusion this
        # report exists to prevent. Say so rather than recommending on it.
        records = []
        corpus_unread = str(e)[:160]
        if args.action == "lifecycle":
            # A lifecycle report contains no prior art, so the recommend
            # wording would say the wrong thing on this path:
            # what an unread corpus costs HERE is every rule's judged history.
            print("warden rules lifecycle: the corpus could not be read — "
                  "every rule below reads as having no judged history, NOT as "
                  "a rule that never fired", file=sys.stderr)
        else:
            print("warden rules recommend: the corpus could not be read — "
                  "every row below is prior-art only, NOT a finding that this "
                  "repo has no history", file=sys.stderr)
    # One parser serves both actions, so a flag the chosen action does not
    # implement would be ACCEPTED and silently dropped — `rules lifecycle
    # --max-unanswered 0` would exit 0 with the ceiling enforcing nothing.
    # A gate flag that quietly does nothing is the failure
    # `enforcement-truth` names outright.
    if args.action == "lifecycle":
        stray = [f for f, v in (("--max-unanswered", args.max_unanswered),
                                ("--backtest", args.backtest)) if v is not None]
    else:
        stray = ["--window"] if args.window is not None else []
    if stray:
        print(f"warden rules {args.action}: {', '.join(stray)} "
              f"{'is' if len(stray) == 1 else 'are'} not implemented by this "
              f"action — refusing rather than accepting a flag that would do "
              f"nothing", file=sys.stderr)
        return 2
    if args.action == "lifecycle":
        return _cmd_rules_lifecycle(config, rules_dir, records, corpus_unread,
                                    args.window)
    report = advisor_mod.recommend(config.root, records=records,
                                   rules_dir=rules_dir,
                                   corpus_unread=corpus_unread,
                                   backtest_window=getattr(args, "backtest", None))
    print(advisor_mod.render(report))

    run_dir = runs.create_run_dir(config.root, "rules-recommend")
    (run_dir / "rule-recommendations.json").write_text(
        json.dumps(report.to_dict(), indent=2) + "\n")
    # The manifest is the provenance record, so it must not assert a count
    # the run never computed — no "0-recommended" on an unreadable ruleset
    # whose sibling artifact says gap_known: false.
    # Written AFTER the ceiling check so it can record what actually
    # happened, the way every other command's manifest does.
    def _finish(status: str, code: int) -> int:
        runs.write_manifest(run_dir, root=config.root, cmd="rules-recommend",
                            rules_version=rules_mod.rules_version(rules_dir, config.root),
                            exit_status=status)
        return code

    # A ceiling that was DECLARED but cannot be read must never quietly
    # become "no ceiling" — that would let a typo disarm the one
    # deterministic obligation the answers file carries.
    if report.ceiling_complaints:
        for complaint in report.ceiling_complaints:
            print(f"warden rules recommend: {complaint}", file=sys.stderr)
        print("warden rules recommend: a declared ceiling that cannot be "
              "read is not 'no ceiling' — fix the declaration.",
              file=sys.stderr)
        return _finish("ceiling_unreadable", 2)
    # Explicit beats declared: a caller that types a flag is asking for
    # exactly that ceiling. With no flag, the one declared next to the
    # answers it governs applies — which is what lets CI run the bare
    # command and keeps the value in one place instead of re-typed into a
    # workflow file.
    ceiling = (args.max_unanswered if args.max_unanswered is not None
               else report.declared_ceiling)
    if not report.gap_known:
        if ceiling is not None:
            print("warden rules recommend: the gap could not be computed (the "
                  "ruleset could not be read), so the ceiling cannot be "
                  "checked. Fix the ruleset; a count of 0 here would mean "
                  "'nobody looked', not 'nothing left'.", file=sys.stderr)
            return _finish("gap_unknown", 2)
        return _finish("gap_unknown", 0)
    if ceiling is not None:
        unanswered = len(report.recommendations)
        if unanswered > ceiling:
            print(f"warden rules recommend: {unanswered} unanswered "
                  f"entr{'y' if unanswered == 1 else 'ies'} exceeds the "
                  f"declared ceiling of {ceiling} — adopt them, "
                  f"or record a verdict in {advisor_mod.ANSWERS_PATH}. A gap "
                  f"nobody answers is advice nobody acts on.", file=sys.stderr)
            return _finish(f"{unanswered}-unanswered-over-ceiling", 1)
    return _finish(f"{len(report.recommendations)}-unanswered", 0)


def _cmd_autonomy_carve_out(args, root, autonomy_mod) -> int:
    """Answer the cage's post-check question: "every one of
    these gate-surface paths — is it inside the ladder's carve-out?"

    Exit 0 = yes, and the caller may let the PR stand for a HUMAN to merge.
    Exit 1 = no, naming each reason. Exit 2 = the question could not be
    answered. The caller's default is refusal, so 1 and 2 land the same for it;
    they are distinguished because "outside the carve-out" and "could not
    evaluate" send a READER to different places.

    Read-only, and it writes no run dir: the runner calls it on every
    forbidden-path hit, and a query that leaves an artifact per call would fill
    .warden/out with evidence of nothing happening. It clears nothing itself,
    and it merges nothing — no code path here can.
    """
    if not args.base or not args.paths_from:
        print("warden autonomy carve-out: --base <ref> and --paths-from "
              "<file|-> are required", file=sys.stderr)
        return 2
    try:
        raw = (sys.stdin.read() if args.paths_from == "-"
               else Path(args.paths_from).read_text())
    except OSError as e:
        print(f"warden autonomy carve-out: cannot read --paths-from ({e})",
              file=sys.stderr)
        return 2
    try:
        # No policy is passed in. `config` here was loaded from the FILESYSTEM,
        # and the runner calls this with cwd = the session-writable worktree —
        # so `carve_out_problems` reads `review.blocking_severities` and
        # `review.rules_dir` from the COMMITTED repo.yaml itself. An
        # uncommitted repo.yaml never appears in the diff, so it is never a
        # forbidden path and the carve-out would never be asked about it.
        problems = autonomy_mod.carve_out_problems(
            root, raw.splitlines(), base=args.base)
    except Exception as e:  # fail closed: an unanswerable question refuses
        print(f"warden autonomy carve-out: could not evaluate ({e}) — "
              "failing closed", file=sys.stderr)
        return 2
    if problems:
        print("warden autonomy carve-out: OUTSIDE the ladder's carve-out",
              file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("inside the autonomy-ladder carve-out (pause fields, new "
          "non-blocking rule files, their backtests, the attestation shard a "
          "review round leaves) — still a PR a human merges; nothing here "
          "merges anything")
    return 0


def _cmd_autonomy(args: argparse.Namespace) -> int:
    """The safe half of the autonomy ladder. Mutates the
    working tree only — a caller opens a PR, the founder merges. There is no
    merge path here by design."""
    from . import autonomy as autonomy_mod

    config = config_mod.load()
    config_mod.enforce_platform_pin(config)
    root = config.root
    rules_dir = root / config.review.rules_dir
    if args.action == "carve-out":
        return _cmd_autonomy_carve_out(args, root, autonomy_mod)
    run_dir = runs.create_run_dir(root, "autonomy")

    def _finish(summary: dict, exit_status: str, code: int = 0) -> int:
        (run_dir / "autonomy-result.json").write_text(
            json.dumps(summary, indent=2) + "\n")
        runs.write_manifest(run_dir, root=root, cmd="autonomy",
                            rules_version=rules_mod.rules_version(rules_dir, config.root),
                            exit_status=exit_status)
        return code

    if args.action == "pause":
        try:
            actions = autonomy_mod.pause_candidates(root)
        except ValueError as e:
            # An unreadable/corrupt committed corpus fails CLOSED: pause
            # nothing, and still leave an artifact saying why (certify guards
            # the identical records_from_shards call the same way).
            print(f"warden autonomy pause: corpus unreadable — nothing paused "
                  f"({e})", file=sys.stderr)
            return _finish({"action": "pause", "dry_run": args.dry_run,
                            "candidates": [], "paused": [],
                            "error": str(e)}, "corpus-unreadable", code=2)
        paused: list[dict] = []
        skipped: list[dict] = []
        # The real loop is CUMULATIVE — each apply_pause measures a tree the
        # previous pause already changed — so the dry run has to be too, or it
        # promises a pause the real command then refuses.
        would_pause: set[str] = set()
        for a in actions:
            if args.dry_run:
                # The dry run answers "what would happen", so it must include
                # the refusal — a dry run that says "would pause" for a pause
                # the real command rejects is the more expensive lie.
                breach = autonomy_mod._ceiling_breach(
                    root, a.rule_id, None, also_paused=frozenset(would_pause))
                if breach:
                    print(f"would NOT pause {a.rule_id}: {breach}")
                else:
                    would_pause.add(a.rule_id)
                    print(f"would pause {a.rule_id}: {a.streak} consecutive "
                          f"refuting review rounds ({a.judged} judged)")
                continue
            # One un-pausable candidate (no rule file, already paused, or
            # malformed frontmatter) must not abort the others or lose the run
            # artifact — record it as skipped and carry on.
            try:
                rule_path, artifact = autonomy_mod.apply_pause(root, a)
            except autonomy_mod.AutonomyError as e:
                skipped.append({"rule_id": a.rule_id, "reason": str(e)})
                print(f"skipped {a.rule_id}: {e}", file=sys.stderr)
                continue
            paused.append({"rule_id": a.rule_id, "streak": a.streak,
                           "backtest": str(artifact.relative_to(root))})
            print(f"paused {a.rule_id} ({a.streak} refutations) -> "
                  f"{rule_path.relative_to(root)} + {artifact.relative_to(root)}")
        if not actions:
            print("warden autonomy pause: no rule is on a refutation streak — "
                  "nothing to pause")
        elif not args.dry_run and paused:
            print("\nopen a PR — this never auto-merges; merge authority is the "
                  "founder's.")
        status = ("dry-run" if args.dry_run
                  else f"paused-{len(paused)}" if paused else "nothing-to-pause")
        # A candidate we could not pause is a partial result, not a success:
        # exit 1 so a caller/CI does not read it as "all done".
        code = 1 if skipped else 0
        return _finish({"action": "pause", "dry_run": args.dry_run,
                        "candidates": [a.rule_id for a in actions],
                        "paused": paused, "skipped": skipped}, status, code=code)

    # action == "adopt"
    if not args.source or not args.dest_name:
        print("warden autonomy adopt: --source <rulefile> and --as <id> are "
              "required", file=sys.stderr)
        return 2
    try:
        src = Path(args.source).read_text()
    except OSError as e:
        print(f"warden autonomy adopt: cannot read --source ({e})",
              file=sys.stderr)
        return 2
    try:
        dest, artifact = autonomy_mod.adopt_rule(
            root, src, dest_name=args.dest_name,
            blocking_severities=config.review.blocking_severities)
    except autonomy_mod.AutonomyError as e:
        # A refusal is the mechanism working, not an error to route around, so
        # it still leaves an artifact saying what was refused and why.
        print(f"warden autonomy adopt: refused — {e}", file=sys.stderr)
        _finish({"action": "adopt", "dest_name": args.dest_name,
                 "adopted": None, "backtest": None, "refused": str(e)},
                "refused")
        return 1
    # The artifact is named, not silent: it is what keeps the produced tree
    # certifiable (S-05), and a PR author who does not know it exists will not
    # commit it.
    print(f"adopted -> {dest.relative_to(root)} + "
          f"{artifact.relative_to(root)} (non-blocking; open a PR, never "
          "auto-merged)")
    return _finish({"action": "adopt", "dest_name": args.dest_name,
                    "adopted": str(dest.relative_to(root)),
                    "backtest": str(artifact.relative_to(root)),
                    "refused": None},
                   "adopted")


def _cmd_mine(args: argparse.Namespace) -> int:
    # Deliberately does NOT require repo.yaml. The whole point of the miner is
    # the history a repo had BEFORE it enrolled — demanding enrollment config
    # to read it would lock the door on the room it exists to search.
    root = Path(mine_mod._git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    result = mine_mod.mine(root, since=args.since,
                           gh=mine_mod.default_reader(),
                           repo_slug=args.repo, pr_limit=args.pr_limit)
    print(mine_mod.render(result))

    run_dir = runs.create_run_dir(root, "mine")
    mine_mod.write_artifact(run_dir, result)
    # "unenrolled" is a CLAIM about the repo, so a bare `except Exception`
    # must not stamp it: an enrolled repo whose config would not parse or whose
    # ruleset would not hash would have that claim written into an evidence
    # artifact — "cannot compute" collapsed into "absent". They are different
    # facts with different remedies, so they get different markers, and the
    # uncomputable one names the error class that produced it
    # (error-names-cause).
    #
    # The probe is a READ, not a stat, because `Path.is_file()` performs the
    # same collapse one level down: it swallows OSError and answers False for a
    # directory in repo.yaml's place, a symlink loop, or a readable repo.yaml
    # behind an unreadable parent — all of which would be stamped "unenrolled"
    # with nothing said. `rules._enforcement_surface` states this contract
    # for this exact file ("FileNotFoundError from the read itself, never a
    # stat that would collapse 'cannot look' into 'absent'"); mine keeps
    # it too.
    #
    # Every path below that reaches write_manifest writes one — the miner
    # exists to read a repo's pre-enrollment history, and losing the run's
    # evidence over an unreadable ruleset would be the wrong trade. That
    # includes a repo.yaml that never finishes being read: a FIFO could park
    # the command HERE or inside `config_mod.load`, which opens the same path
    # again, leaving a run dir holding pr-corpus.json and no manifest. Neither
    # parks, because the guard lives where the readers are:
    # `config.read_repo_yaml` classifies the path before it reads a byte and
    # refuses a FIFO or a device with a named error, and `config.load`,
    # `config.declared_rules_dir` and `rules._enforcement_surface` all go
    # through it too. Hardening this probe alone would buy nothing. A SOCKET is
    # equally unenrolled-proof but its class is the kernel's, not warden's:
    # `os.open` refuses it (EOPNOTSUPP/ENXIO) before any classification runs,
    # so the manifest names THAT error rather than NotARegularFileError.
    #
    # The probe reads the whole file rather than one byte, because it is
    # the shared reader and reading repo.yaml twice costs nothing next to
    # reading it two different ways. MemoryError rides with OSError below for
    # that reason: an unbounded read of an absurd repo.yaml is a way to leave
    # this function between write_artifact and write_manifest, which would
    # leave the orphan run dir the paragraph above rules out.
    config_path = root / config_mod.CONFIG_NAME
    try:
        config_mod.read_repo_yaml(root)
    except FileNotFoundError:
        # The one condition that actually means unenrolled. A dangling symlink
        # lands here too, and deliberately: it resolves to no repo.yaml, which
        # is the same answer `rules._enforcement_surface` gives it.
        version = "unenrolled"
    except (OSError, MemoryError) as e:
        version = f"{UNCOMPUTABLE_PREFIX}{type(e).__name__}"
        print(f"warden mine: {config_path} is there but could not be read "
              f"({type(e).__name__}: {e}). That is not the same as an "
              f"unenrolled repo, so the manifest records {version!r}.",
              file=sys.stderr)
    else:
        try:
            config = config_mod.load(root)
            version = rules_mod.rules_version(root / config.review.rules_dir,
                                              root)
        except Exception as e:  # noqa: BLE001 — the run still owes evidence
            version = f"{UNCOMPUTABLE_PREFIX}{type(e).__name__}"
            # Said out loud too, so a reader who never opens the manifest still
            # learns their gate config is unreadable.
            print(f"warden mine: {config_path} read cleanly, so this repo is "
                  f"enrolled, but its rules_version could not be computed "
                  f"({type(e).__name__}: {e}). The manifest records "
                  f"{version!r}, not 'unenrolled'.", file=sys.stderr)
    runs.write_manifest(run_dir, root=root, cmd="mine", rules_version=version,
                        exit_status=f"{len(result.signals)}-signals",
                        extra={"unread_classes": [u.signal_class
                                                  for u in result.unavailable]})
    return 0


def _cmd_catalog(args: argparse.Namespace) -> int:
    entries = catalog_mod.load_catalog()

    if args.action == "list":
        shown = [e for e in entries
                 if args.engine is None or e.engine == args.engine]
        if not shown:
            print(f"no catalog entries with engine:{args.engine}")
            return 0
        for entry in sorted(shown, key=lambda e: (e.engine, e.taxonomy)):
            print(f"{entry.engine:<12} {entry.taxonomy:<20} "
                  f"{entry.id:<28} {entry.name}")
        print(f"\n{len(shown)} of {len(entries)} entries")
        return 0

    if args.action == "show":
        # `id` is nargs="?" because list/check take none. Catch the omission
        # here, or None falls into the lookup and the error sends the reader
        # to `catalog list` hunting an entry named None.
        if not args.id:
            raise catalog_mod.CatalogError(
                "catalog show: an entry id is required — "
                "`warden catalog list` names them all")
        match = next((e for e in entries if e.id == args.id), None)
        if match is None:
            raise catalog_mod.CatalogError(
                f"no catalog entry {args.id!r} — `warden catalog list` "
                "names them all")
        print(f"# {match.id} — {match.name}  [{match.taxonomy}]")
        print(f"\nengine: {match.engine}")
        print(f"guards: {match.guards}")
        print(f"applies when: {match.applies_when}")
        print(f"false-positive cost: {match.false_positive_cost}")
        if match.instead:
            print(f"instead: {match.instead}")
        if match.starter and "sketch" in match.starter:
            print(f"\nstarter (checker sketch):\n{match.starter['sketch']}")
        if match.starter and "checks" in match.starter:
            print("\nstarter (declarative checks):")
            # allow_unicode: check messages carry em dashes, and safe_dump
            # escapes them to \uXXXX by default — a starter nobody can paste
            # into a rule file, which is the whole point of shipping one.
            print(yaml.safe_dump({"checks": match.starter["checks"]},
                                 sort_keys=False, allow_unicode=True).rstrip())
        print("\nsources:")
        for source in match.sources:
            print(f"  - {source['title']}: {source['url']}")
        return 0

    # action == "check" — the loader already validated everything on load;
    # reaching here means the shape holds. Say exactly what that did and did
    # not establish, so nobody reads "OK" as "the citations resolve".
    urls = sorted({s["url"] for e in entries for s in e.sources})
    if not args.online:
        print(f"catalog OK: {len(entries)} entries, {len(urls)} citation URL(s) "
              "validated for shape only — not fetched. "
              "Run `warden catalog check --online` to resolve them.")
        return 0

    failures = []
    for url in urls:
        reason = catalog_mod.resolve_source(url)
        if reason is not None:
            failures.append((url, reason))
    if failures:
        for url, reason in failures:
            print(f"warden: citation does not resolve: {url} — {reason}",
                  file=sys.stderr)
        return 2
    # Resolving is not currency: an archived edition URL keeps
    # resolving after a newer edition ships, so a bare "all resolved" reads as
    # "up to date" when it only proves the links work. Say what resolution did
    # establish, then report currency separately from the declared editions.
    print(f"catalog OK: {len(entries)} entries; all {len(urls)} citation "
          "URL(s) resolved — resolution proves the links work, not that the "
          "cited editions are current:")
    for line in catalog_mod.render_currency(
            catalog_mod.edition_currency(catalog_mod.load_editions())):
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The whole subcommand table, as one value.

    Split out of `main` so the SHAPE of the CLI is readable without running
    it. The malformation matrix (`tests/test_malformation_matrix.py`) derives
    its command axis from this table rather than from a hand-typed roster:
    every leaf it finds is either driven against malformed input or carries a
    written exclusion, so a subcommand added tomorrow fails that test until
    someone classifies it. A hand-maintained enumeration of this surface goes
    stale with the next subcommand; an enumeration the code itself produces
    cannot.
    """
    parser = argparse.ArgumentParser(prog="warden",
                                     description="Nightgate repo control: policy in, evidence out")
    from . import __version__
    # Cheapest possible liveness probe: a consuming repo's shim runs this to
    # tell "the platform is unreachable" apart from "the gate found things".
    parser.add_argument("--version", action="version",
                        version=f"warden {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser(
        "init",
        help="enroll the repository in the current directory: detect pyproject.toml, package.json or go.mod and write repo.yaml, starter rules, .warden/skills-policy.md, the CI workflow and .gitignore entries. Refuses (exit 1, naming each) when any file it would write exists")
    p_init.set_defaults(func=_cmd_init)

    p_explain = sub.add_parser("explain", help="render repo.yaml as an orientation brief")
    p_explain.add_argument("--base", help="also classify the diff vs this git ref")
    p_explain.set_defaults(func=_cmd_explain)

    p_verify = sub.add_parser("verify", help="run repo.yaml deterministic gates for a scope")
    p_verify.add_argument("--scope", required=True,
                          help="verify scope declared in repo.yaml (e.g. contracts)")
    p_verify.add_argument(
        "--check-head", metavar="SHA",
        help="run nothing: exit 0 when the newest artifact for SHA under the "
             "scope is a PASS on SHA itself, from a clean tree, of the "
             "commands repo.yaml declares for the scope now; exit 1, naming "
             "why, otherwise")
    p_verify.set_defaults(func=_cmd_verify)

    p_take = sub.add_parser(
        "take",
        help="in the gate job: replace .warden/out with a fresh directory and copy into it "
             "each regular verify-result.json found below --from in a directory named "
             "*-verify, following no symlink. Exit 2 when .warden or --from is a symlink "
             "or not a directory, or when nothing was taken")
    p_take.add_argument("--from", dest="source", required=True,
                        help="the directory the verify artifact was downloaded to")
    p_take.set_defaults(func=_cmd_take)

    p_deploy = sub.add_parser(
        "deploy",
        help="run a declared deploy scope with provenance — refuses unless "
             "the checkout IS the commit (HEAD == --commit, clean tree), the "
             "commit is reachable from the deploy branch, and its own diff "
             "introduced a clean committed attestation shard")
    p_deploy.add_argument("--scope", required=True,
                          help="deploy scope declared in repo.yaml (e.g. staging)")
    p_deploy.add_argument("--branch", default="main",
                          help="branch a deployable commit must be reachable "
                               "from, resolved only as refs/heads/ then "
                               "refs/remotes/ so a tag can never shadow it "
                               "(default: main; a pipeline pins e.g. "
                               "origin/main, the way attest check pins --base)")
    p_deploy.add_argument("--commit", default="HEAD",
                          help="the commit being deployed; must equal HEAD — "
                               "deploy runs the working tree, so pass the sha "
                               "your pipeline checked out to assert it is the "
                               "one certified (default: HEAD)")
    p_deploy.set_defaults(func=_cmd_deploy)

    p_review = sub.add_parser("review", help="run the AI rules gate on a diff")
    src = p_review.add_mutually_exclusive_group(required=True)
    src.add_argument("--event", help="GitHub Actions pull_request event JSON path")
    src.add_argument("--base", help="local mode: git ref to diff HEAD against")
    p_review.add_argument(
        "--no-project-checkers", action="store_true",
        help="execute no code from the checkout: exit 2, gate DID NOT RUN, when "
             ".warden/checkers/ holds a module instead of importing it. The gate "
             "job warden init writes passes it")
    p_review.add_argument("--no-comment", action="store_true",
                          help="never post to the PR; print the comment instead")
    p_review.set_defaults(func=_cmd_review)

    p_declare = sub.add_parser(
        "declare",
        help="does the gate's behaviour match what it declares? Five checks — "
             "D-01 every verify scope's reach is declared rather than inferred, "
             "D-02 every gate input's rules_version claim holds under "
             "perturbation, D-03 CI runs every declared verify scope in the job "
             "that runs `warden review` or in a job whose verify results it "
             "takes, D-04 every declared gate-surface path "
             "carries an explicit risk tier, D-05 the forge does not allow the "
             "auto-merge graph.yaml's review.delegation excludes (read with "
             "`gh api`; UNREADABLE prints and never reads as clean). Exit 0 "
             "clean, 1 drift, 2 a check could not evaluate")
    declare_sub = p_declare.add_subparsers(dest="declare_cmd", required=True)
    p_declare_check = declare_sub.add_parser(
        "check", help="run every declaration check and report drift")
    p_declare_check.add_argument(
        "--json", action="store_true",
        help="emit the report as JSON instead of the rendered brief")
    p_declare_check.set_defaults(func=_cmd_declare)

    p_diff = sub.add_parser(
        "diff", help="write the deterministic review package for a range "
                     "(log + stat + -U10 diff) so review subagents read a "
                     "file instead of inheriting the diff through context")
    p_diff.add_argument("--base", required=True)
    p_diff.add_argument("--head", default="HEAD")
    p_diff.add_argument("-o", "--output", default=None,
                        help="file to write; stdout when omitted")
    p_diff.set_defaults(func=_cmd_diff)

    p_round = sub.add_parser(
        "round", help="mint an isolated review round: a "
                      "derived, collision-refusing directory holding the "
                      "review package and an empty reviewers/ dir")
    round_sub = p_round.add_subparsers(dest="round_cmd", required=True)
    p_round_new = round_sub.add_parser(
        "new", help="mint one round and print its path on stdout, so callers "
                    "write ROUND=\"$(warden round new --base origin/main)\" "
                    "and "
                    "never a fixed name")
    p_round_new.add_argument("--base", required=True)
    p_round_new.add_argument("--head", default="HEAD")
    p_round_new.set_defaults(func=_cmd_round)
    p_round_count = round_sub.add_parser(
        "count",
        help="the review rounds this range's COMMITTED evidence records, "
             "and whether they are over the declared cap. The CI seam for "
             "the round cap: exit 0 at or under it (or with no cap "
             "declared), 1 over it, 2 when the chain, a `--prior-head` or "
             "the declared cap could not be read")
    p_round_count.add_argument("--base", required=True)
    p_round_count.add_argument(
        "--head", default=None,
        help="the commit to count up to. Omitted, the PR head from the "
             "Actions event, then HEAD — never a bare HEAD on a "
             "`pull_request` event, where it is the merge ref whose FIRST "
             "parent is the base and the committed floor is 0 for every PR")
    p_round_count.add_argument(
        "--cap", type=int, default=None,
        help="the cap to measure against; omitted, it is read from "
             "graph.yaml `review.cap`, then repo.yaml `repair.budget`")
    p_round_count.add_argument(
        "--prior-head", action="append", metavar="SHA",
        help="a head this change pushed EARLIER, from outside this branch's "
             "current history — CI passes the PR's previous head. Its own "
             "`base..SHA` is counted too and can only RAISE the floor, so a "
             "history rewrite that drops the commits which ADDED the shards "
             "no longer resets the count in silence. "
             "Repeatable; a head that does not resolve is exit 2, never "
             "skipped")
    p_round_count.set_defaults(func=_cmd_round_count)
    p_round_classify = round_sub.add_parser(
        "classify",
        help="the repair stop condition, computed: classify "
             "each open finding as on the ORIGINAL diff or on a file the "
             "REPAIR rounds wrote, from the round manifests' sha chain. Exit "
             "0 the review loop ends (clean, or every open finding is LOW "
             "and repair-written), 1 this round counts against the cap, 2 "
             "the chain or payload could not be read")
    p_round_classify.add_argument(
        "--findings", required=True,
        help="the attestation payload (`{\"findings\": [...]}`) — the same "
             "file `attest write --findings` reads, so there is no second "
             "findings file to go stale — or a bare findings list, which is "
             "accepted here only (`attest write` requires the payload object)")
    p_round_classify.add_argument(
        "--round", action="append", required=True, metavar="DIR",
        help="a round directory `warden round new` minted; repeat for every "
             "round of this change, in any order — the chain is proved from "
             "the shas")
    p_round_classify.set_defaults(func=_cmd_round_classify)

    p_attest = sub.add_parser(
        "attest",
        help="record/show the pre-PR review attestation, or assert a PR carries one")
    p_attest.add_argument("action",
                          choices=["write", "show", "check", "classify"])
    p_attest.add_argument(
        "--enforce", action="store_true",
        help="classify: also refuse a committed attestation that reviewed "
             "this range with the light crew when the range is not provably "
             "inert")
    p_attest.add_argument("--findings",
                          help="write: JSON file with reviewers/findings/verdict")
    p_attest.add_argument("--base", default="main",
                          help="write: git ref the review diffed against; "
                               "check: the base BRANCH TIP (e.g. origin/main)")
    p_attest.add_argument("--head",
                          help="check: the commit CI reports about (default: the "
                               "PR head from $GITHUB_EVENT_PATH, else HEAD — never "
                               "the merge ref)")
    p_attest.add_argument("--pr", help="write: PR number this review covered")
    p_attest.add_argument("--bead", help="write: tracker id for provenance")
    p_attest.add_argument(
        "--review-dir",
        help="write: the round directory holding each reviewer's raw output, "
             "and NOTHING else — everything in it is read as a claim about "
             "the round. Every reviewer claiming `returned: true` must name a "
             "distinct, non-empty, non-symlink regular file in it, and every "
             "non-empty report in it must be claimed by a roster entry, or "
             "the write is refused (exit 2). A dir that is empty as a path, "
             "missing, not a directory or unlistable is also refused, never "
             "downgraded. Omitted entirely, the attestation is stamped "
             "`unverified-roster`. Under this flag every "
             "roster entry's `role` must be a lens from the vocabulary "
             "(code-reviewer, cross-examiner, scoped-re-reviewer, builder, "
             "or crew:<slug> for a charter lens) and carry the `round` it was "
             "dispatched in, and every finding must carry the `lens` that "
             "raised it and its `round`, joined to a returned roster entry. "
             "Under this flag each roster entry is also "
             "stamped an `outcome` — reviewed-clean, reviewed-findings or "
             "no-review — derived from the artifacts: a dispatch that did not "
             "return, or a returned output with no findings that names no "
             "file the range changed, is no-review, not a clean review; a "
             "declared no-review stands; and a declared reviewed-clean the "
             "output cannot corroborate is refused")
    p_attest.set_defaults(func=_cmd_attest)

    p_ship = sub.add_parser(
        "ship",
        help="the ship tail as ONE command: verify at HEAD "
             "under every scope the diff requires, every tip attestation "
             "clean, the PR title linted BEFORE the PR exists, gate parity, "
             "then push and open the PR. Each step prints what it checked and "
             "what it found; a step that cannot evaluate REFUSES. Exit 0 all "
             "passed, 1 a step refused, 2 a step could not be evaluated. It "
             "NEVER merges")
    p_ship.add_argument("--base", default="origin/main",
                        help="the base BRANCH TIP every step measures against "
                             "(default: origin/main — the remote-tracking ref, "
                             "not the local branch, which a fetch does not "
                             "move)")
    p_ship.add_argument("--title", required=True,
                        help="the PR title, linted as the squash headline "
                             "before the PR is created. The forge appends "
                             "\" (#N)\" and the validator strips it, so pass "
                             "the bare title")
    p_ship.add_argument("--body-file",
                        help="file holding the PR body (the evidence chain). "
                             "Required unless --checks-only")
    p_ship.add_argument("--remote", default=ship_mod.DEFAULT_REMOTE,
                        help=f"remote to push to (default: "
                             f"{ship_mod.DEFAULT_REMOTE})")
    p_ship.add_argument("--pr-base", default="main",
                        help="branch the PR targets (default: main)")
    p_ship.add_argument("--commit-lint",
                        help=f"path to the header validator (default: "
                             f"{ship_mod.COMMIT_LINT} under the repo root). A "
                             "missing validator is a refusal, never a pass")
    p_ship.add_argument("--checks-only", action="store_true",
                        help="run the four checks and stop — no push, no PR")
    p_ship.set_defaults(func=_cmd_ship)

    p_progress = sub.add_parser(
        "progress",
        help="where every unit under a worktree root is, and HOW LONG it has "
             "been there. `show` renders one table — unit, "
             "branch, head, last boundary, and the AGE of that boundary, "
             "which is what tells stalled from working; `record` appends one "
             "boundary. The log lives under .warden/out/ — ignored by this "
             "repo's and the shipped example's .gitignore, and certify's "
             "R-13 rung refuses a repo that tracks it — and it survives the "
             "unit, so a unit that "
             "DIED still reports its last boundary")
    progress_sub = p_progress.add_subparsers(dest="progress_cmd", required=True)
    p_progress_show = progress_sub.add_parser(
        "show", help="render every unit under a worktree root as one table")
    p_progress_show.add_argument(
        "--root", help="worktree root to scan (default: the repo's "
                       f"{progress_mod.WORKTREE_ROOT} when it exists, else the "
                       "repo itself). Immediate subdirectory checkouts only")
    p_progress_show.add_argument(
        "--unit", action="append",
        help="restrict to this unit directory name; repeatable")
    p_progress_show.set_defaults(func=_cmd_progress)
    p_progress_record = progress_sub.add_parser(
        "record", help="append one boundary for this checkout")
    # No argparse `choices=`: the vocabulary is `progress_mod.BOUNDARIES` and
    # `record` refuses an unknown name with a message that PRINTS the
    # vocabulary and says why it is closed, which argparse's "invalid choice"
    # does not. Declaring the choices here would also split this one reader
    # into one matrix cell per boundary (`tests/test_malformation_matrix.py`
    # expands a positional's choices, because `memory ingest|recall` really are
    # different readers) — seven rows describing one code path.
    p_progress_record.add_argument(
        "boundary", metavar="BOUNDARY",
        help="one of: " + " ".join(progress_mod.BOUNDARIES))
    p_progress_record.add_argument(
        "--head", help="the commit the boundary applies to (default: HEAD)")
    p_progress_record.add_argument("--detail", default="",
                                   help="one short line of context")
    p_progress_record.set_defaults(func=_cmd_progress)

    p_decide = sub.add_parser(
        "decide",
        help="record a decision bound to commit + rules_version (decided, why, "
             "cost if wrong, alternatives, scope), show the latest, or list the "
             "committed store")
    p_decide.add_argument("action", choices=["record", "show", "list"])
    p_decide.add_argument(
        "--input", help="record: JSON file with decided/why/cost_if_wrong/"
                        "alternatives/scope")
    p_decide.add_argument(
        "--base", default="HEAD",
        help="record: git ref the decision was made against (default HEAD — a "
             "ruling not tied to a diff is 'as of this commit')")
    p_decide.add_argument("--pr", help="record: PR number this decision belongs to")
    p_decide.add_argument("--bead", help="record: tracker id for provenance")
    p_decide.set_defaults(func=_cmd_decide)

    p_audit = sub.add_parser("audit",
                             help="re-render the sticky comment from latest artifacts")
    p_audit.add_argument("--event", help="GitHub Actions pull_request event JSON path")
    p_audit.set_defaults(func=_cmd_audit)

    p_plan = sub.add_parser(
        "plan", help="task packet: paths, risk, must-pass commands, memory priors")
    p_plan.add_argument("--task", required=True, help="what the work is")
    p_plan.add_argument("--area", help="file-hints area (.warden/file-hints/<area>.yaml)")
    p_plan.add_argument("--path", action="append", metavar="FILE",
                        help="a file the work touches, relative to the "
                             "current directory as git reads it; tiered as "
                             "`explain --base` tiers it (repeatable)")
    p_plan.set_defaults(func=_cmd_plan)

    p_certify = sub.add_parser(
        "certify", help="score this repo on the enrollment maturity ladder")
    p_certify.add_argument("--level", type=int,
                           help="exit 1 unless this level is attained (CI use)")
    p_certify.add_argument("--capability",
                           help="exit 1 unless this capability badge is "
                                "earned, e.g. autonomous (CI use)")
    p_certify.add_argument("--goal", action="store_true",
                           help="instead of the ladder, report the redesign's "
                                "five run-time evidence items as "
                                "PASS/FAIL/UNMEASURED; exit 1 unless all five "
                                "PASS")
    p_certify.add_argument("--ledger", type=Path,
                           help="with --goal: the cage's ledger.csv, so the "
                                "cage item counts its done rows")
    p_certify.add_argument("--report-only", action="store_true",
                           help="with --goal: exit 0 whatever the table says")
    p_certify.set_defaults(func=_cmd_certify)

    p_rules = sub.add_parser(
        "rules",
        help="guardrail gap analysis: what this repo does not enforce, and "
             "whether the evidence is local or prior art")
    p_rules.add_argument("action", choices=["recommend", "lifecycle"])
    p_rules.add_argument(
        "--max-unanswered", type=int, metavar="N",
        help="exit 1 if more than N catalog entries are applicable, "
             "unenforced, and carry no recorded decision: without it the gap "
             "is visible but nothing obliges an answer. Answer an entry by "
             "adopting it or "
             "by recording a verdict in .warden/catalog-answers.yaml. "
             "Omitted, a `ceiling:` declared in that file applies instead; "
             "this flag overrides it")
    p_rules.add_argument(
        "--backtest", type=int, nargs="?", const=backtest_mod.DEFAULT_WINDOW,
        metavar="N",
        help="replay each recommended rule over the last N commits (default "
             f"{backtest_mod.DEFAULT_WINDOW}) and report flagged / true-positive "
             "/ projected-false-positive, or an UNBACKTESTED marker for a rule "
             "no regex can replay. A rule that fires and "
             "catches nothing is shown REJECTED with its numbers, never dropped")
    p_rules.add_argument(
        "--window", type=int, metavar="N",
        help="lifecycle: read every declared rule's firing record over the "
             f"last N commits (default {backtest_mod.DEFAULT_WINDOW}). The "
             "window is printed with every claim — a 'never fires' verdict "
             "without its window is unfalsifiable")
    p_rules.set_defaults(func=_cmd_rules)

    p_auto = sub.add_parser(
        "autonomy",
        help="the safe half of the autonomy ladder: pause a "
             "rule on a refutation streak, or adopt a non-blocking rule — "
             "always via a PR, never auto-merged. `carve-out` is the read-only "
             "question the cage's forbidden-path post-check asks of a "
             "committed diff")
    p_auto.add_argument("action", choices=["pause", "adopt", "carve-out"])
    p_auto.add_argument("--dry-run", action="store_true",
                        help="pause: list the streak rules, write nothing")
    p_auto.add_argument("--source", metavar="FILE",
                        help="adopt: path to the drafted rule file")
    p_auto.add_argument("--as", dest="dest_name", metavar="ID",
                        help="adopt: rule id / filename stem to write under "
                             "the declared rules dir (review.rules_dir)")
    p_auto.add_argument("--base", metavar="REF",
                        help="carve-out: the diff base the paths were read "
                             "from (e.g. origin/main)")
    p_auto.add_argument("--paths-from", metavar="FILE",
                        help="carve-out: newline-separated paths to judge; "
                             "'-' reads stdin")
    p_auto.set_defaults(func=_cmd_autonomy)

    p_mine = sub.add_parser(
        "mine",
        help="mine defect signals from FULL history — every merged PR, not "
             "just the ones that produced an attestation")
    p_mine.add_argument("--since", help="only commits newer than this git "
                                        "date (e.g. 2026-08-01)")
    p_mine.add_argument("--repo", help="owner/name to read PRs from "
                                       "(default: the github.com origin remote)")
    p_mine.add_argument("--pr-limit", type=int,
                        default=mine_mod._DEFAULT_PR_DETAIL,
                        help="how many recent merged PRs get per-PR requests "
                             "(comments, check runs); the artifact states the "
                             "cap whenever it truncates")
    p_mine.set_defaults(func=_cmd_mine)

    p_catalog = sub.add_parser(
        "catalog",
        help="guardrail catalog: cited candidate rules a repo might be missing")
    p_catalog.add_argument("action", choices=["list", "show", "check"])
    p_catalog.add_argument("id", nargs="?", help="show: catalog entry id")
    p_catalog.add_argument("--engine", choices=catalog_mod.CATALOG_ENGINES,
                           help="list: only entries landing on this engine")
    p_catalog.add_argument("--online", action="store_true",
                           help="check: actually fetch every citation URL "
                                "(the offline pass validates shape only)")
    p_catalog.set_defaults(func=_cmd_catalog)

    p_graph = sub.add_parser("graph",
                             help="declared agent-organization graph: validate graph.yaml, render mermaid, "
                                  "print one round's declared review crew, "
                                  "answer who may merge right now")
    p_graph.add_argument("action",
                         choices=["validate", "render", "crew", "authority"])
    p_graph.add_argument("--round", type=int, default=None,
                         help="crew: the review round to resolve, 1 to the "
                              "cap; past the cap exits 1, unless graph.yaml "
                              "declares review.closure, where a round past "
                              "the cap resolves the closure crew")
    p_graph.add_argument("--light", action="store_true",
                         help="crew: resolve the proportionate round's crew "
                              "instead of a numbered round — the one role "
                              "dispatched for a range `warden attest classify` "
                              "proves cannot alter behaviour. Exits 1 where "
                              "no review.light is declared")
    p_graph.set_defaults(func=_cmd_graph)

    p_skills = sub.add_parser(
        "skills",
        help="skill-pack checks: pre-flight declared harness assumptions "
             "against the pack it is pointed at (fail-closed; --pack for "
             "an installed plugin cache)")
    p_skills.add_argument("action", choices=["preflight", "pin"],
                          help="preflight: resolve the pack's declared "
                               "assumptions; pin: refuse a machine whose "
                               "installed pack is not the one repo.yaml's "
                               "platform.pin names")
    p_skills.add_argument("--config-dir",
                          help="pin: the Claude Code config directory holding "
                               "plugins/ (default: $CLAUDE_CONFIG_DIR, else "
                               "~/.claude)")
    p_skills.add_argument("--pack",
                          help="skills directory to check (default: "
                               "<root>/skills/nightgate-skills/skills; point "
                               "at the installed plugin cache on a consumer)")
    p_skills.set_defaults(func=_cmd_skills)

    p_memory = sub.add_parser("memory",
                              help="review memory: ingest shards, role-split recall, "
                                   "precision stats, check the declared tag-vocabulary "
                                   "ceiling (exit 1 on a breach, 2 when it cannot evaluate), "
                                   "or watch for a bar crossed since the last committed "
                                   "watch record (exit 1 on a transition, 3 UNREAD)")
    p_memory.add_argument("action",
                          choices=["ingest", "recall", "stats", "check-vocabulary",
                                   "watch"])
    p_memory.add_argument("--json", action="store_true",
                          help="watch: print the result as JSON")
    p_memory.add_argument("--record", action="store_true",
                          help="watch: also write a dated watch record under "
                               ".warden/memory/watch/ for a human to commit")
    p_memory.add_argument("--origin", choices=["interactive", "unattended"],
                          default=_default_origin(),
                          help="ingest: was a human present for the run that "
                               "produced these events. Derived from the "
                               "environment by default; pass it to override")
    p_memory.add_argument("--files", nargs="+",
                          help="recall: changed files to key recall on")
    p_memory.add_argument("--for", dest="for_role", choices=["reviewer", "examiner"],
                          help="recall: which role's split to serve")
    p_memory.add_argument("--tags", nargs="*", default=None,
                          help="recall: additional tag keys")
    p_memory.add_argument("--rules", nargs="*", default=None,
                          help="recall: additional rule-id keys")
    p_memory.set_defaults(func=_cmd_memory)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    yamlio.clear_cache()    # the parse memo is per command, never carried over
    try:
        return args.func(args)
    except (config_mod.ConfigError, rules_mod.RuleError, verify_mod.VerifyError,
            github_mod.GitHubError, diffs_mod.DiffError, attest_mod.AttestError,
            catalog_mod.CatalogError, mine_mod.MineError,
            deploy_mod.DeployError, ship_mod.ShipError,
            progress_mod.ProgressError) as e:
        print(f"warden: {e}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 — exit-code contract: 1 is reserved for
        # blocking findings; an unexpected crash means the gate DID NOT RUN -> 2.
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
