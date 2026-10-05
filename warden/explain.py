"""warden explain: render repo.yaml as an agent-orientation brief.

The "context in" half of warden: components, risk tiers, protected paths,
verify scopes, and the rule inventory with the current rules_version. With
--base, also classifies the diff against that ref into tiers and lists which
rules would fire. No network, no LLM.
"""

import subprocess

from . import rules as rules_mod
from .config import RepoConfig, classify
from .diffs import range_paths

HOOKS_DIR = ".githooks"


def local_gate_state(root) -> tuple[str, str]:
    """Whether this clone's git hooks actually run the versioned DoD gate.

    Returns (state, detail) where state is one of enabled | unset | bypassed.

    This is a machine-local fact, so no artifact in the repo can assert it and
    no ladder check can either — it would fail in CI, where hooks do not apply.
    Reporting it is the honest middle: the reader is told, and nothing blocks.

    With core.hooksPath repointed elsewhere (at .beads/hooks, say),
    `.githooks/pre-push` (ruff + pytest) never runs, and `.githooks/commit-msg`
    — the ONLY commit-msg hook, since beads ships none — never runs either.
    Both failures are silent. `.githooks/*` chains the beads hooks precisely so
    enabling it never disables them; .beads/hooks does not chain back.
    """
    try:
        out = subprocess.run(["git", "config", "core.hooksPath"], cwd=root,
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "unset", "could not read core.hooksPath"
    path = out.stdout.strip()
    if not path:
        return "unset", f"core.hooksPath is not set — run: git config core.hooksPath {HOOKS_DIR}"
    if path.rstrip("/").endswith(HOOKS_DIR):
        return "enabled", path
    return "bypassed", (
        f"core.hooksPath is `{path}`, not `{HOOKS_DIR}` — the versioned pre-push "
        f"gate and commit-lint are NOT running. Run: git config core.hooksPath {HOOKS_DIR}")


def core_bare_state(root) -> tuple[str, str]:
    """Whether this checkout's `core.bare` has been silently flipped to true.

    Returns (state, detail) where state is one of ok | corrupted | unknown.

    Git worktrees SHARE one
    `.git/config` — there is not one config per worktree. A write to a shared
    key from anywhere (a sibling worktree, a tool, or the harness's own
    teardown of a worktree under `.claude/worktrees/`) rewrites it for the
    parent checkout and every sibling. `core.bare=true` is the worst of the
    two confirmed symptoms: git READS keep working (`git log`, `git show`)
    while every WRITE (`commit`, `push`, `status`, `rev-parse --show-toplevel`)
    fails with "this operation must be run in a work tree", so nothing looks
    broken until a write is attempted — a night of unattended work can pass
    before it surfaces.

    Same footing as `local_gate_state`: a machine-local fact no repo artifact
    or ladder rung can assert (it would fail in CI, which runs bare-ish
    checkouts), so warden REPORTS it. Fail closed — a read that could not run
    is `unknown`, never `ok`, because "could not look" is not "healthy".
    """
    # `--type=bool` CANONICALISES the value: git treats `1`, `yes`, `on`,
    # `TRUE` all as bare=true, and any of them genuinely breaks the checkout.
    # A raw string compare against "true" would read `core.bare = 1` as
    # healthy, which is a fail-open. git
    # then yields "true"/"false" (exit 0), exit 1 for an unset key (the
    # healthy default), and exit >=2 for a value that is not a valid boolean.
    try:
        out = subprocess.run(
            ["git", "config", "--type=bool", "--get", "core.bare"], cwd=root,
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "unknown", "could not read core.bare"
    if out.returncode == 1:
        return "ok", "unset"          # key not set — a normal non-bare checkout
    if out.returncode != 0:
        # A malformed boolean (`core.bare = garbage`) is itself corruption,
        # but we cannot say it means bare — fail closed to unknown rather
        # than read an unparseable value as healthy.
        return "unknown", (
            f"core.bare is set to a value git cannot parse as a boolean "
            f"({out.stderr.strip()[:80]}) — check .git/config")
    value = out.stdout.strip().lower()
    if value == "true":
        return "corrupted", (
            "core.bare is `true` — git WRITES (commit, push, status) fail in "
            "this checkout while reads keep working, so nothing looks broken "
            "until one is attempted. A worktree sharing this repo's "
            "`.git/config` rewrote the key (worktree isolation is not config "
            "isolation). Run: git config core.bare false")
    return "ok", value or "unset"


def _changed_files(config: RepoConfig, base: str) -> list[str]:
    # run_git raises DiffError on bad refs/shallow clones -> cli exits 2 (infra),
    # never 1 = "blocking finding".
    # --relative: below the git root, the enrollment's own paths, as its
    # risk tiers name them. NUL-terminated (range_paths), so a quoted name
    # never misses its tier.
    return list(range_paths(config.root, base, "HEAD"))


def render(config: RepoConfig, base: str | None = None) -> str:
    rules_dir = config.root / config.review.rules_dir
    rules = rules_mod.load_rules(rules_dir)
    version = rules_mod.rules_version(rules_dir, config.root)

    lines = [f"# {config.repo} — warden brief", ""]

    lines += ["## Components", ""]
    for c in config.components:
        desc = f" — {c.description}" if c.description else ""
        lines.append(f"- `{c.path}` ({c.lang or 'n/a'}){desc}")

    lines += ["", "## Risk tiers (first glob match wins; default LOW)", ""]
    for t in config.risk_tiers:
        reason = f" — {t.reason}" if t.reason else ""
        lines.append(f"- {t.tier}: `{t.glob}`{reason}")

    if config.protected_paths:
        lines += ["", "## Protected paths", ""]
        for p in config.protected_paths:
            lines.append(f"- `{p.path}` — {p.reason}")

    lines += ["", "## Verify scopes", ""]
    for scope, steps in config.verify.items():
        lines.append(f"- {scope}: " + "; ".join(f"`{s.run}`" for s in steps))

    if config.deploy:
        lines += ["", "## Deploy scopes", ""]
        for scope, steps in config.deploy.items():
            lines.append(f"- {scope}: " + "; ".join(f"`{s.run}`" for s in steps))

    if config.design is not None:
        tiers = ", ".join(config.design.decision_required)
        # Orientation, not enforcement — like protected_paths above. warden does
        # NOT block a change here; the `design` block DECLARES which tiers owe a
        # decision record, and the design-approval discipline (not warden) binds
        # it. Naming it a "gate" would be an enforcement claim warden cannot back.
        lines += ["", "## Design decision records (declared, not gated)", "",
                  f"- Decision record expected for tier(s): {tiers} — declared "
                  "in repo.yaml `design`. **Orientation, not enforcement**: "
                  "warden does not block a change that lacks one; the "
                  "design-approval discipline is what binds it."]
        if config.design.charter:
            lines.append(f"- Charter: {config.design.charter}")

    lines += ["", f"## Review rules (rules_version `{version}`)", ""]
    for r in rules:
        mark = " **[PAUSED]**" if r.paused else ""
        lines.append(f"- {r.severity}: `{r.id}`{mark} — applies to " +
                     ", ".join(f"`{g}`" for g in r.applies_to))
        if r.paused:
            lines.append(f"    - paused: {r.paused_reason}")
    blocking = ", ".join(config.review.blocking_severities)
    lines.append(f"\nBlocking severities: {blocking}. "
                 "Findings post to the PR sticky comment, and to the job step "
                 "summary when $GITHUB_STEP_SUMMARY is set; blocking findings "
                 "fail the check.")

    state, detail = local_gate_state(config.root)
    label = {"enabled": "enabled", "unset": "NOT enabled", "bypassed": "BYPASSED"}[state]
    lines += ["", "## Local gate", "", f"- {label}: {detail}"]
    if state != "enabled":
        lines.append("    - CI is the fence; this is the local one, and it is off.")

    # core.bare drift is loud-only: reported when broken, silent when healthy
    # (unlike the gate line above, which always prints). A brief that printed
    # "core.bare: ok" on every run would bury the one time it matters.
    bare_state, bare_detail = core_bare_state(config.root)
    if bare_state != "ok":
        prefix = ("CORRUPTED" if bare_state == "corrupted"
                  else "UNKNOWN (fail closed)")
        lines.append(f"- {prefix}: {bare_detail}")

    if base:
        changed = _changed_files(config, base)
        lines += ["", f"## Diff vs `{base}` ({len(changed)} files)", ""]
        if not changed:
            lines.append("No changed files.")
        else:
            for f in changed:
                tier = classify(config, f)
                lines.append(f"- {tier.tier}: `{f}`")
            firing = rules_mod.applicable(rules, changed)
            lines += ["", "Rules that would fire: " +
                      (", ".join(f"`{rid}`" for rid in firing) if firing else "none")]

    return "\n".join(lines) + "\n"
