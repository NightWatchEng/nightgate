"""The commit-message convention gate, exercised as a program.

The validator is the fence for every commit, and its exemption list admits
machine-authored headers such as Dependabot's grouped PRs, which have no way
to append a bead reference. An untested exemption in a gate script is a hole
nobody watches. Each case runs the real script the way both callers do.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "commit-lint.sh"


def _lint(tmp_path: Path, message: str, shell: str = "sh") -> int:
    p = tmp_path / "msg"
    p.write_text(message)
    return subprocess.run([shell, str(SCRIPT), str(p)],
                          capture_output=True).returncode


# The two callers of this one validator run under different shells: CI's
# /bin/sh is dash, a macOS commit hook is bash. Any check that runs only the
# default `sh` tests one of them and calls it agreement.
#
# WHY THIS IS NOT A `shutil.which` FILTER. A filter deletes tests instead of
# reporting a gap: the ubuntu runner has no zsh, two tests here are
# shell-parametrized (one bare, one crossed with the 4 REAL_EXEMPT_HEADERS),
# so one absent shell would remove (1 + 4) x 1 = 5 COLLECTED tests and nobody
# would be told. The suite would simply get weaker, on GATE SURFACE —
# scripts/commit-lint.sh is the validator CI fences commit structure with, so
# a zsh-only bug in it would be caught on a developer's Mac and never in CI.
#
# The split is a real distinction rather than bookkeeping.
# REQUIRED shells are the ones both callers actually run under and every
# machine has: `sh` (dash on the runner) and `bash` (a macOS commit hook).
# Missing one is a FAILURE — the coverage the gate is documented to have is
# gone. OPTIONAL shells are the extra readings a developer machine can give:
# `dash` as itself rather than behind `sh`, and `zsh`. Missing one is a NAMED
# SKIP, which is a visible, counted line in the summary instead of five tests
# that quietly stopped existing.
REQUIRED_SHELLS = ("sh", "bash")
OPTIONAL_SHELLS = ("dash", "zsh")
ALL_SHELLS = REQUIRED_SHELLS + OPTIONAL_SHELLS
MISSING_SHELLS = tuple(s for s in ALL_SHELLS if not shutil.which(s))


def _shell_param(shell: str):
    """One parametrize entry per DECLARED shell, present or not.

    The point is that the collection count does not move: an absent shell
    becomes a skip carrying its own reason, so `pytest -q` reports it and the
    gap is answerable. Deriving the list from what happens to be installed
    would make the loss invisible.
    """
    if shutil.which(shell):
        return shell
    return pytest.param(shell, marks=pytest.mark.skip(
        reason=f"SHELL COVERAGE GAP: {shell} is not installed on this "
               f"machine, so scripts/commit-lint.sh is NOT proven under it "
               f"here. This is a reported gap, not a smaller "
               f"suite — install {shell} to close it."))


SHELLS = [_shell_param(s) for s in ALL_SHELLS]


def _lint_header_only(tmp_path: Path, header: str, shell: str = "sh") -> int:
    p = tmp_path / "title"
    p.write_text(header + "\n")
    return subprocess.run([shell, str(SCRIPT), "--header-only", str(p)],
                          capture_output=True).returncode


@pytest.mark.parametrize("header", [
    "build(deps): bump the action-pins group with 3 updates",
    "build(deps): bump the action-pins group with 1 update",
    "build(deps): bump the action-pins group across 2 directories with 3 updates",
    "build(deps): bump actions/checkout from 4.4.0 to 7.0.1",
    "build(deps-dev): bump lockfile-tool from 1.0.0 to 2.0.0",
    "Merge pull request #1 from x/y",
])
def test_machine_generated_shapes_are_exempt(tmp_path, header):
    assert _lint(tmp_path, header + "\n") == 0


@pytest.mark.parametrize("header,why", [
    ("build(deps): downgrade everything quietly",
     "only Dependabot's 'bump' shapes are exempt — a human hiding behind "
     "build(deps) still fails"),
    ("build(deps): bump nothing in particular tonight",
     "a bare 'bump <anything>' would smuggle arbitrary "
     "headers — only the full 'from A to B' / 'the G group with N updates' "
     "shapes are exempt"),
    ("build(deps-dev): bump something in the pip group",
     "no 'from'/'group with' tail — not a Dependabot header shape"),
    ("build(ci): bump the action-pins group",
     "the exemption is scoped to the deps scopes Dependabot uses"),
    ("feat(warden): something with no bead reference",
     "the convention still binds ordinary commits"),
])
def test_near_misses_still_fail(tmp_path, header, why):
    assert _lint(tmp_path, header + "\n") != 0, why


# Verbatim from PR #384's commit 23721f5 (`git log -1 --format=%B`): a grouped
# update that carried exactly ONE dependency. Dependabot writes the version
# tail on the PR TITLE ("... from 10.0.1 to 10.2.0 in the action-pins group
# across 1 directory") and in the generated body, and NOT on the commit
# header, which is the bare "bump X". The two arms that existed before this
# fixture both require a tail on the header, so the commits job went red on
# a PR nobody had edited.
ONE_UPDATE_GROUP_HEADER = "build(deps): bump astral-sh/setup-uv"
ONE_UPDATE_GROUP_BODY = (
    "\n\nBumps the action-pins group with 1 update in the / directory: "
    "[astral-sh/setup-uv](https://github.com/astral-sh/setup-uv).\n\n\n"
    "Updates `astral-sh/setup-uv` from 10.0.1 to 10.2.0\n"
    "- [Release notes](https://github.com/astral-sh/setup-uv/releases)\n"
    "- [Commits](https://github.com/astral-sh/setup-uv/compare/"
    "20cfd1bf945f4377ade1205e4dbc17946fc9a30d..."
    "c18668ad3cf93ea998bef934396af7bb5c839dc7)\n")
ONE_UPDATE_GROUP_TITLE = ("build(deps): bump astral-sh/setup-uv from 10.0.1 "
                          "to 10.2.0 in the action-pins group across 1 directory")


@pytest.mark.parametrize("shell", SHELLS)
def test_a_one_dependency_grouped_bump_is_exempt_with_its_generated_body(
        tmp_path, shell):
    """Regression for PR #384: the third Dependabot header shape.

    The commit header is the bare "bump X" and the generated body names the
    same X on an "Updates `X` from A to B" line. Both scopes Dependabot uses,
    under every shell the two callers run — the arm uses `${var#pattern}`
    and a nested `case`, and a construct that parses under bash and not dash
    would make this green on a Mac and red in CI's /bin/sh.
    """
    assert _lint(tmp_path, ONE_UPDATE_GROUP_HEADER + ONE_UPDATE_GROUP_BODY,
                 shell) == 0, (
        f"{shell}: Dependabot's one-dependency grouped bump is rejected — "
        "every month that brings exactly one action update goes red")
    dev = (ONE_UPDATE_GROUP_HEADER + ONE_UPDATE_GROUP_BODY).replace(
        "build(deps):", "build(deps-dev):")
    assert _lint(tmp_path, dev, shell) == 0, shell
    # The title side of the same PR: the tail IS on the title, so it takes
    # the first arm, bare and squash-rendered alike.
    assert _lint_header_only(tmp_path, ONE_UPDATE_GROUP_TITLE, shell) == 0
    assert _lint_header_only(tmp_path, ONE_UPDATE_GROUP_TITLE + " (#384)",
                             shell) == 0


def test_the_bare_bump_header_is_confirmed_by_its_body_or_rejected(tmp_path):
    """The new arm must be narrower than the header it admits.

    "build(deps): bump X" is three typed words, cheaper to forge than any
    other Dependabot shape, so the arm keys on the generated body as well:
    the header alone, a body naming a different dependency, a multi-word X,
    an empty X, the wrong scope, and the header-only caller (a title has no
    body to confirm it) all stay rejected. Each is a mutant of the arm that
    would widen the residual the exemption block admits to.
    """
    assert _lint(tmp_path, ONE_UPDATE_GROUP_HEADER + "\n") != 0, (
        "the bare header with no body is exempt — the near-miss guard above "
        "is now hollow")
    other = ONE_UPDATE_GROUP_BODY.replace("`astral-sh/setup-uv`",
                                          "`actions/checkout`")
    assert _lint(tmp_path, ONE_UPDATE_GROUP_HEADER + other) != 0, (
        "a body that names a different dependency confirmed the header")
    assert _lint(tmp_path, "build(deps): bump nothing in particular tonight"
                 "\n\nUpdates `nothing in particular tonight` from 1 to 2\n"
                 ) != 0, "a multi-word X was admitted — Dependabot's X is one token"
    assert _lint(tmp_path, "build(deps): bump \n\nUpdates `` from 1 to 2\n"
                 ) != 0, "an empty X was admitted"
    assert _lint_header_only(tmp_path, ONE_UPDATE_GROUP_HEADER) != 0, (
        "--header-only admitted the bare shape — a PR title has no body to "
        "confirm it, and Dependabot's title for this case carries the tail")
    assert _lint(tmp_path, "build(ci): bump astral-sh/setup-uv"
                 + ONE_UPDATE_GROUP_BODY) != 0, (
        "the exemption is scoped to the deps scopes Dependabot uses")


def test_a_conventional_commit_still_passes(tmp_path):
    msg = ("fix(warden): a real change (agentops-hbz)\n\nWhy: because.\n\n"
           "- did the thing\n\nEvidence: 1 test passed\n")
    assert _lint(tmp_path, msg) == 0


def test_feat_without_evidence_still_fails(tmp_path):
    msg = "feat(warden): a real change (agentops-hbz)\n\nWhy: because.\n"
    assert _lint(tmp_path, msg) != 0


BODY = "\n\nWhy: x\n\n- y\n\nEvidence: z\n"

# A real landed header: 99 characters, 101 BYTES — the em dash is 3. Kept
# verbatim because both halves of the length rule are visible in it: the
# forge suffix, and the multibyte cost.
DC04089 = ("feat(warden): rule lifecycle — retire the dead, demote the noisy, "
           "narrow the broad (agentops-1tw.6)")


def test_squash_suffix_does_not_invalidate_a_legal_header(tmp_path):
    """The squash suffix must not reject a header the gate approved.

    GitHub's squash merge appends " (#N)" to the header of the commit it
    lands on main. A validator that re-runs over text the author never
    wrote and rejects it approves work on the PR and fails the identical
    work seconds later, where red is stop-the-line.

    The ANCHOR is what fires: the convention regex requires the header to end
    with the bead id, so any suffix fails it regardless of length. The suffix
    width is unpredictable (#9 is 5 chars, #999999 is 9), so an author cannot
    budget for it.
    """
    legal = "feat(warden): a summary well inside the cap (agentops-1.2)"
    assert len(legal.encode()) <= 100, "fixture must be legal pre-merge"
    assert _lint(tmp_path, legal + BODY) == 0
    assert _lint(tmp_path, legal + " (#138)" + BODY) == 0, (
        "the squash suffix rejected a header the gate had already approved")
    for suffix in (" (#9)", " (#1234)", " (#999999)"):
        assert _lint(tmp_path, legal + suffix + BODY) == 0, suffix


@pytest.mark.parametrize("shell", SHELLS)
def test_length_is_characters_and_every_shell_agrees(tmp_path, shell):
    """The other half: length is counted in characters, in every shell.

    `${#var}` counts BYTES in dash and CHARACTERS in bash/zsh. CI's /bin/sh is
    dash; a macOS commit hook is bash. So a `${#var}` cap gives two answers,
    and a 99-character header with an em dash — 101 bytes — passes the hook
    and fails CI. An anchor failure masks it: fixing only the anchor exposes
    the byte bug.

    Capping bytes instead is deterministic, but it silently narrows a
    documented convention ("summary <=100 chars") for every author, and it
    rejects DC04089. Determinism is the requirement; changing the rule is
    not. Dropping UTF-8 continuation bytes counts characters without
    depending on shell or locale.

    Parametrized over every shell present, because a check that runs only the
    default `sh` tests one of the two callers and calls it agreement — the
    name would be an enforcement claim the test does not make.
    """
    assert len(DC04089) == 99, "fixture must be 99 CHARACTERS"
    assert len(DC04089.encode()) == 101, "and 101 BYTES — that is the split"
    assert _lint(tmp_path, DC04089 + BODY, shell) == 0, (
        f"{shell}: a 99-character header was rejected — the cap must count "
        "characters, and the two callers must not disagree")
    assert _lint(tmp_path, DC04089 + " (#138)" + BODY, shell) == 0

    over = "feat(warden): " + "é" * (101 - len("feat(warden): ") - len(" (agentops-1.2)")) + " (agentops-1.2)"
    assert len(over) == 101, "fixture must be 101 CHARACTERS"
    assert _lint(tmp_path, over + BODY, shell) != 0, (
        f"{shell}: a 101-character multibyte header must still be rejected — "
        "the continuation-byte strip must not undercount")


def test_the_strip_is_anchored_digits_only_and_space_led(tmp_path):
    """The strip must remove ONLY the forge's suffix.

    Three mutants each admit a real over-length false accept: dropping the
    `$` anchor, making
    the digits optional, and allowing arbitrary text inside the parens. The
    three properties that make the strip safe — anchored, digits-only,
    space-led — are each pinned below, so a later 'generalisation' (GitLab's
    !123, a double suffix) cannot silently make the length cap bypassable by
    writing "(#...)" in a summary.
    """
    head, tail = "feat(warden): ", " (agentops-1.2)"

    # over-length, and survives any correct strip
    over = head + "x" * (101 - len(head) - len(tail)) + tail
    assert len(over.encode()) == 101
    assert _lint(tmp_path, over + BODY) != 0
    assert _lint(tmp_path, over + " (#138)" + BODY) != 0

    # ANCHORED: a mid-header "(#123)" must not be stripped, or an over-length
    # header shortens itself past the cap
    mid = head + "a summary (#123456789012345) that runs on and on and on" \
        + "x" * 20 + tail
    assert len(mid.encode()) > 100
    assert _lint(tmp_path, mid + BODY) != 0, "a mid-header (#N) was stripped"

    # DIGITS-ONLY: " (#)" and " (#see 138)" are not forge suffixes
    assert _lint(tmp_path, head + "a thing" + tail + " (#)" + BODY) != 0
    assert _lint(tmp_path, head + "a thing" + tail + " (#see 138)" + BODY) != 0

    # SPACE-LED: no separator means it is part of the summary
    assert _lint(tmp_path, head + "a thing" + tail + "(#138)" + BODY) != 0

    # and a suffix is never a substitute for the bead id
    assert _lint(tmp_path, head + "a thing (#138)" + BODY) != 0


def test_header_only_accepts_a_title_that_has_no_body(tmp_path):
    """A squash merge lands ONE commit whose header is the PR TITLE, so that
    text must be validated too: a PR whose commits are all valid can still
    land a title whose type (`retro(...)`) is not in the allowed set.

    A title has no body, so the Evidence rule — a BODY rule — cannot apply to
    it. `--header-only` runs the header rules and skips that one, which is
    what lets the PR-title check share this validator instead of growing a
    second copy of the convention regex somewhere else to drift.
    """
    title = "feat(warden): a title with no body behind it (agentops-1.2)"
    assert _lint(tmp_path, title + "\n", ) != 0, (
        "full mode must still require Evidence: on a feat commit")
    assert _lint_header_only(tmp_path, title) == 0


def test_header_only_still_enforces_every_header_rule(tmp_path):
    """--header-only must relax exactly ONE rule and no others.

    The risk in adding a mode to a gate is that it becomes the lenient path
    everything routes through. Each assertion below is a rule that must
    survive: the type list, the scope list, the mandatory bead reference, and
    the length cap.
    """
    # a real title with an invalid type
    assert _lint_header_only(
        tmp_path,
        "retro(2026-08-29): re-derive the corpus, mine 3 escapes, reconcile "
        "the vocabulary (agentops-a5p.17)") != 0, (
        "an invalid TYPE must still be rejected — this is the title that "
        "reached main and could not be linted")
    assert _lint_header_only(
        tmp_path, "feat(nonsuch): a thing (agentops-1.2)") != 0
    assert _lint_header_only(tmp_path, "feat(warden): no bead reference") != 0
    head, tail = "feat(warden): ", " (agentops-1.2)"
    over = head + "x" * (101 - len(head) - len(tail)) + tail
    assert len(over) == 101
    assert _lint_header_only(tmp_path, over) != 0
    # and the forge suffix is still stripped, since the title is what gains it
    assert _lint_header_only(
        tmp_path, "feat(warden): a thing (agentops-1.2) (#138)") == 0

    # A title ALREADY ending in " (#123)" is the shape that defeats a
    # title-only check: the strip is single-pass, so the gate sees one suffix
    # and main sees two — green here, red there, which is the failure this
    # whole step exists to remove. Copy-pasting a landed header out of
    # `git log` produces exactly this.
    # CI therefore checks BOTH the title and the header the squash will build.
    reland = "fix(ci): re-land the pin refresh (agentops-1.2) (#123)"
    assert _lint_header_only(tmp_path, reland) == 0, (
        "the bare title passes — which is precisely why it is not sufficient")
    assert _lint_header_only(tmp_path, reland + " (#0)") != 0, (
        "the squash-rendered form of a double-suffixed title must be rejected "
        "at the gate, not discovered on main")


def test_ci_validates_the_pr_title_enforced():
    """Enforcement truth: the check is a fence only while CI runs it in a step
    whose failure fails the job. Derived from the repo's own machinery —
    `certify._run_check`'s `ci_step_enforced` parses the workflow YAML and
    rejects a shell-swallowed exit, step-level and job-level
    continue-on-error, and a workflow not triggered on the required event.

    Documented limit, inherited from that checker: `if:` expressions are not
    evaluated, so the step's own `if: github.event_name == 'pull_request'` is
    not verified here.
    """
    from warden import certify as certify_mod

    ok, detail = certify_mod._run_check(
        {"type": "ci_step_enforced",
         "pattern": "commit-lint.sh --header-only",
         "event": "pull_request"}, ROOT)
    assert ok, (
        "CI does not enforce the PR-title check — the title becomes main's "
        f"commit header on squash and nothing else validates it. {detail}")


def test_pr_title_is_not_interpolated_into_the_shell():
    """The gate must not become the injection it guards against.

    A PR title is attacker-controlled text. Interpolating `${{ github.event.
    pull_request.title }}` directly into a `run:` block would execute
    `$(...)` or backticks inside it, in the job that validates every PR — the
    shape this repo's own os-command-injection rule exists to catch. It must
    reach the script through `env:` instead.

    Parsed as YAML rather than split on strings: slicing the step body on an
    indent boundary truncates before the run block, which would make the
    guard fail for a reason unrelated to what it checks.
    """
    import yaml

    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = [s for s in doc["jobs"]["commits"]["steps"]
             if "--header-only" in str(s.get("run", ""))]
    assert len(steps) == 1, "the PR-title step is gone or was duplicated"
    step = steps[0]

    run = str(step.get("run", ""))
    assert "pull_request.title" not in run, (
        "the PR title is interpolated into the run block — a title containing "
        "$(...) would execute in the job that validates every PR")
    env = step.get("env") or {}
    assert any("pull_request.title" in str(v) for v in env.values()), (
        "the title must reach the script through env:, not interpolation")
    # BOTH forms must be checked: the bare title, and the header the squash
    # will build from it. Checking only the title lets a double-suffixed title
    # through the gate and onto main. A guard on the validator alone does
    # not catch CI dropping the second invocation, so pin it here.
    assert run.count("--header-only") == 2, (
        "the step must validate the title AND the squash-rendered header "
        f"(title + ' (#N)'); found {run.count('--header-only')} check(s)")
    assert "(#0)" in run, (
        "the step never builds the squash-rendered form, so a title already "
        "ending in ' (#123)' passes the gate and reddens main")

    assert "$PR_TITLE" in run, (
        "the run block never reads PR_TITLE — bound by name, because an "
        "any()-over-env-keys check is satisfied by any second variable")

    # The step's `if:` is load-bearing for CORRECTNESS, not just coverage: on a
    # push event `github.event.pull_request.title` is empty, so without the
    # guard the step lints an empty header and reddens main. And certify's
    # ci_step_enforced does not evaluate `if:` expressions, so `if: false`
    # would silently make this gate a no-op with the suite still green.
    # Precedent: tests/test_gap_answers.py pins an
    # owning job's `if:` verbatim for the same reason.
    assert step.get("if") == "github.event_name == 'pull_request'", (
        f"the PR-title step's `if:` changed to {step.get('if')!r} — an empty "
        "title on a non-pull_request event would redden main, and `if: false` "
        "would disable the gate invisibly")


# --- the exemption arms are reachable from a typed title ---------------------
#
# The arms match git-GENERATED subjects, and the same validator also checks
# the PR TITLE, which is free text in a web form, so "Merge whatever I want"
# passes both callers. The failure mode is convention EVASION, not a red
# main — the title lands as "Merge ... (#N)" and main's own lint exempts it
# identically, so the two callers keep agreeing.
#
# The arms stay open and the script SAYS so, which is only defensible
# against the traffic a tightening would reject. These two guards pin both
# halves.

# Verbatim from `git log main`: the merge-header shapes ("Merge pull request
# #N from ...", "Merge remote-tracking branch ..."), a revert, and a
# Dependabot bump.
#
# Every header below is reachable from main; `git log main --format=%s |
# grep -F "<header>"` finds each one. A census over `git log --all` would
# pick up headers that live only on unmerged local branches. The scope
# matters: these are the shapes a future tightening must not reject, and a
# header that never landed on main is not evidence about what lands on main.
REAL_EXEMPT_HEADERS = [
    "Merge pull request #129 from NightWatchEng/feat/0mr-decide-base-sha-semantic",
    "Merge remote-tracking branch 'origin/main' into fix/17-8-cage-publish",
    "Revert the directly-pushed agentops-dtx.12 commits, re-landing via PR",
    "build(deps): bump the action-pins group with 3 updates",
]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("header", REAL_EXEMPT_HEADERS)
def test_real_history_headers_stay_exempt_in_both_callers(
        tmp_path, header, shell):
    """The traffic any tightening would have to survive, kept as a fixture.

    Real Dependabot and revert traffic must be checked before narrowing the
    arms, because a false reject blocks an automated PR nobody is watching.
    Checking it once proves nothing later — the check only holds if it is
    pinned, so the four shapes this repo has actually landed live here.

    The revert is the one that decides it. The revert header in
    REAL_EXEMPT_HEADERS is HAND-written prose, not git's generated
    `Revert "<subject>"` form. The obvious tightening — require the quote, so
    only git's own revert subject is exempt — would reject this repo's real
    revert while leaving the typed-title route open on `Merge ` and
    `fixup!`. That is the whole argument for the residual-paragraph
    disposition, and it is measured here rather than asserted.

    Both callers, on both axes. MODE, because the exemption block runs before
    the header/body split: a shape exempt at commit time must be exempt at
    PR-title time too, or the gate that approved the work rejects the merge of
    it. And SHELL, because this file's other guards already establish that the
    two callers are two shells — CI's /bin/sh is dash, a macOS commit hook is
    bash — and a check that runs only the default `sh` tests one of them and
    calls it agreement (the reason the length test is parametrized). A
    tightening written with a bash-only
    construct would pass under bash and error under dash; running `sh` alone,
    this fixture would report green and main would go red on the first merge.
    """
    assert _lint(tmp_path, header + "\n", shell) == 0, (
        f"{shell}: a header this repo has really landed is no longer exempt — "
        "if the arms were tightened deliberately, the traffic it rejects is here")
    assert _lint_header_only(tmp_path, header, shell) == 0, shell


def test_the_exemption_residual_admits_the_typed_title_route(tmp_path):
    """Enforcement truth about the gate's own comment.

    Justifying the residual as "forging a Dependabot subject takes deliberate
    effort" would overstate it, because that is not the cheapest route in:
    typing `Merge anything` into the PR title box costs nothing, and the
    Merge/Revert/Reapply/fixup/squash arms match a PREFIX rather than a
    shape. A comment that overstates what a check costs to evade is the same
    defect class `.warden/rules/enforcement-truth.md` exists to catch, one
    level down — so the admission is pinned, not merely written once.

    The behaviour it admits to is asserted first, so this is a guard about a
    true statement rather than a string match on prose.
    """
    for typed in ("Merge whatever I want here",
                  'Revert "anything"',
                  "fixup!anything at all"):
        assert _lint_header_only(tmp_path, typed) == 0, (
            "the arms no longer accept a freely typed title — the residual "
            "below is now overstated and should be rewritten, not re-pinned")

    text = SCRIPT.read_text()
    # Anchored to the exemption block itself, not the whole script prefix: the
    # failure message below says "the exemption block", so a slice running from
    # the shebang would let an admission that migrated up into the
    # --header-only or squash-strip comment satisfy a guard whose message names
    # this one.
    start = text.index("# Machine-generated shapes are exempt")
    block = text[start:text.index('case "$header" in', start)]
    # Flattened to bare prose. For Markdown, " ".join(text.split()) is enough;
    # here the block is a SHELL COMMENT, so the leading `#` of each line
    # survives that join and a sentence rewrapped mid-phrase becomes "...the
    # PR TITLE, # which is free text..." — a failure blaming the wrong thing,
    # red at every wrap width <= 72. Strip the marker, then join.
    flat = " ".join(line.lstrip("#").strip()
                    for line in block.splitlines()).strip()
    for admission in ("PR TITLE, which is free text",
                      "No forging required",
                      "match a PREFIX, not a shape",
                      "convention EVASION, never a red main"):
        assert admission in flat, (
            f"the exemption block lost its residual admission: {admission!r}")


def test_a_required_shell_is_never_silently_absent():
    """The FAILURE half of shell coverage.

    `sh` and `bash` are the two shells the validator's callers actually run
    under. If either is missing, the gate's documented cross-shell agreement
    is not being checked at all, and that is a red suite rather than a
    quieter one. A `shutil.which` filter would drop those tests silently.
    """
    absent = [s for s in REQUIRED_SHELLS if not shutil.which(s)]
    assert not absent, (
        f"required shell(s) missing: {absent}. scripts/commit-lint.sh is "
        "gate surface and both its callers' shells must be exercised — "
        "`${#var}` counts BYTES in dash and CHARACTERS in bash, which is the "
        "divergence this coverage exists for. A missing required shell is "
        "lost gate coverage, not a smaller test run")


def test_the_collection_covers_every_declared_shell_present_or_not():
    """The SKIP half of shell coverage.

    The defect to prevent is not a missing zsh — it is a suite that gets 5
    tests smaller and says nothing. So the invariant is about COLLECTION:
    every declared shell contributes its parameters on every machine, and an
    absent one arrives as a skip carrying a reason a reader can act on.
    """
    ids = [s if isinstance(s, str) else s.values[0] for s in SHELLS]
    assert ids == list(ALL_SHELLS), (
        "SHELLS no longer covers every declared shell — the collection can "
        "shrink again, which is the whole defect")
    for entry in SHELLS:
        if isinstance(entry, str):
            assert shutil.which(entry), (
                f"{entry} is parametrized as present but is not installed")
            continue
        marks = [m for m in entry.marks if m.name == "skip"]
        assert marks, f"{entry.values[0]} is absent but carries no skip mark"
        reason = marks[0].kwargs["reason"]
        assert "SHELL COVERAGE GAP" in reason and entry.values[0] in reason, (
            "an absent shell must skip with a reason that NAMES the gap and "
            f"the shell; got {reason!r}. A bare skip is only marginally "
            "louder than the vanished tests it replaced")


def test_the_gap_is_answerable_rather_than_inferred():
    """MISSING_SHELLS exists so the question "what is this run not proving?"
    has an answer in the module, not in a diff of two collection counts."""
    # NOT `set(MISSING_SHELLS) == {s for s in ALL_SHELLS if not
    # shutil.which(s)}`. That is a tautology — MISSING_SHELLS is defined by
    # that exact expression at module scope, and `shutil.which` cannot change
    # its answer in between, so it asserts a value equals itself and can
    # never fail. A can't-fail assertion inside the guard for can't-fail
    # assertions is the joke this repo cannot afford.
    #
    # The real invariant is that the REPORTED gap and the SKIPPED collection
    # are the same set. Those are produced by different code paths —
    # MISSING_SHELLS by a comprehension, the skips by _shell_param — so
    # comparing them is a genuine cross-check, and it goes red if either
    # drifts.
    skipped = {e.values[0] for e in SHELLS if not isinstance(e, str)}
    assert set(MISSING_SHELLS) == skipped, (
        f"the gap this module REPORTS ({sorted(MISSING_SHELLS)}) is not the "
        f"gap the collection SKIPS ({sorted(skipped)}) — one of the two is "
        "lying about what this run proved")
    # A missing REQUIRED shell is the other test's failure, so anything in
    # MISSING_SHELLS on a passing run is optional by construction. Asserted
    # rather than assumed: if the two lists ever drift, this says so instead
    # of the gap quietly reclassifying itself.
    assert not (set(MISSING_SHELLS) & set(REQUIRED_SHELLS)), (
        "a REQUIRED shell is reported as a mere gap — that is a failure, not "
        "a skip")
    # Nothing may be listed twice, which would double-count the gap and, in
    # the parametrize, collide on the test id.
    assert len(set(ALL_SHELLS)) == len(ALL_SHELLS)
