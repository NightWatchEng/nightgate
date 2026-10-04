#!/bin/sh
# Commit-message convention gate. One validator, two callers:
# .githooks/commit-msg (advisory, at commit time) and CI (the real fence, over
# every pushed commit). Template lives in CLAUDE.md "Commit structure".
set -e

# --header-only validates a header with no body behind it. The one caller is
# the PR-title check: a squash merge lands ONE commit whose header is the PR
# TITLE, and the title has no body, so the Evidence rule below (a BODY rule)
# cannot apply to it. Splitting header rules from body rules is what lets both
# callers share this validator instead of growing a second copy of the regex.
# The Evidence rule still binds every commit.
header_only=0
if [ "${1:-}" = "--header-only" ]; then header_only=1; shift; fi

msg_file="$1"
[ -f "$msg_file" ] || { echo "commit-lint: no message file" >&2; exit 1; }
header=$(head -1 "$msg_file")

# GitHub's squash merge appends " (#123)" to the header of the commit it lands
# on main. That text is written by the forge, not the author, and its width is
# unpredictable (#9 is 5 chars, #1234 is 8). Strip it before validating, or the
# same validator that APPROVED a header on the PR REJECTS the identical work
# seconds later on main, where red is a stop-the-line event.
#
# The anchor is what fires: the convention regex requires the header to END
# with the bead id, so any suffix fails it outright, whatever the length.
# Landed after main went red on dc04089. Fixing the anchor alone
# was not enough — see the byte-count note at the length check, which the
# anchor failure had been masking.
#
# Residual, fail-closed and deliberate: the strip is single-pass, so a header
# ending in two suffixes (" (#1) (#2)" — a re-land whose title already carried
# one) loses only the last and is then rejected. Rejecting a rare, genuinely
# odd header beats looping a strip over author-written text.
orig_header="$header"
header=$(printf '%s' "$header" | sed -E 's/ \(#[0-9]+\)$//')

# Machine-generated shapes are exempt. The Dependabot arms match its three
# header shapes and nothing looser — a single bump ("bump X from A to B"),
# a grouped bump ("bump the G group with N updates"), and a grouped bump
# that carried exactly ONE dependency, whose COMMIT header is the bare
# "bump X" (the version tail is on the PR title and in the generated body,
# not on the header; PR #384 went red here on it) — under the prefix
# .github/dependabot.yml CONFIGURES (commit-message: prefix build, include
# scope), so the matched shape is declared, not inferred from commit
# history. Dependabot cannot append a bead reference; its PRs still take
# the full review gauntlet. Residual, stated: this keys on shape, not
# authorship — a human forging the complete Dependabot header escapes the
# format convention, and only that; every other gate still applies.
#
# That residual is CHEAPER than it looks, and saying "forging takes
# deliberate effort" does not cover it: this same validator also runs over
# the PR TITLE, which is free text in a web form —
# so "Merge whatever I want", 'Revert "anything"', "fixup!anything" and a
# hand-typed grouped-bump header all reach these arms at the cost of typing
# them. No forging required for the Merge/Revert/Reapply/fixup/squash arms;
# they match a PREFIX, not a shape.
#
# The arms are still left open, deliberately, after counting what actually
# lands here (census over main when this was written): 132 merge headers, 1 revert,
# 1 Dependabot bump, 0 Reapply/fixup!/squash!. Decisively, the one revert is
# "Revert the directly-pushed ... commits, re-landing via PR" —
# HAND-written prose, not git's generated `Revert "<subject>"` shape. Any
# tightening that keyed on the generated shape would have rejected the real
# traffic while leaving the typed-title route open on the other arms.
#
# What the exposure actually is: convention EVASION, never a red main. A
# title of "Merge ..." lands as "Merge ... (#N)", which main's own lint
# exempts identically, so the two callers keep agreeing and CI stays green.
# The cost of a false reject is higher than the cost of an odd header: it
# blocks an automated PR nobody is watching. tests/test_commit_lint.py pins
# both halves — the real headers stay exempt, and this admission stays here.
case "$header" in
  Merge\ *|Revert\ *|Reapply\ *|fixup!*|squash!*) exit 0 ;;
  "build(deps): bump "*" from "*" to "*) exit 0 ;;
  "build(deps-dev): bump "*" from "*" to "*) exit 0 ;;
  "build(deps): bump the "*" group "*" with "*" update"*) exit 0 ;;
  "build(deps): bump the "*" group with "*" update"*) exit 0 ;;
  "build(deps-dev): bump the "*" group with "*" update"*) exit 0 ;;
  # The one-dependency grouped bump. "bump X" alone is three typed words, so
  # this arm is narrower than the header: X must be a single token and the
  # body must carry Dependabot's generated "Updates `X` from A to B" line
  # naming the same dependency. Full mode only — the PR title for this case
  # carries the version tail and matches the first arm, so in --header-only
  # mode the bare header stays rejected: a title has no body to confirm it.
  "build(deps): bump "*|"build(deps-dev): bump "*)
    dep=${header#*": bump "}
    case "$dep" in
      ""|*" "*) ;;
      *) [ "$header_only" = "0" ] \
           && grep -Fq -e 'Updates `'"$dep"'` from ' "$msg_file" \
           && exit 0 ;;
    esac ;;
esac

fail() {
  echo "commit-lint: $1" >&2
  echo "  header: ${orig_header:-$header}" >&2
  echo "  format: type(scope): summary (agentops-id | no-bead: reason)" >&2
  echo "  types:  feat fix test docs ops tooling refactor" >&2
  echo "  scopes: warden cage skills memory graph examples ci docs repo" >&2
  exit 1
}

echo "$header" | grep -Eq \
  '^(feat|fix|test|docs|ops|tooling|refactor)\((warden|cage|skills|memory|graph|examples|ci|docs|repo)\): .+ \((agentops-[a-z0-9]+(\.[0-9]+)*((, ?)agentops-[a-z0-9]+(\.[0-9]+)*)*|no-bead: [^)]+)\)$' \
  || fail "header does not match the convention"

# CHARACTERS, counted shell- and locale-independently. `${#var}` cannot do
# this: dash (CI's /bin/sh) counts bytes while bash/zsh (a macOS commit hook)
# count characters, so one validator gave two answers — a 99-character header
# with an em dash is 101 bytes, passed the hook, and failed CI. That is half of
# why dc04089 reddened main; the `$`-anchor failure masked it.
#
# Dropping UTF-8 continuation bytes (0x80-0xBF) leaves exactly one byte per
# code point, so `wc -c` then counts characters. Verified identical on
# dash/bash/zsh across C, POSIX, C.UTF-8, en_US.UTF-8 and unset locales.
# The obvious alternatives are all locale- or platform-dependent and were
# rejected for it: `wc -m` counts bytes under C and silently falls back on an
# unknown locale; macOS awk's length() counts bytes while GNU gawk counts
# characters — the same bug on a new axis.
len=$(printf '%s' "$header" | LC_ALL=C tr -d '\200-\277' | wc -c | tr -d ' ')
[ "$len" -le 100 ] || fail "header exceeds 100 chars ($len)"

commit_type=$(echo "$header" | sed -E 's/^([a-z]+)\(.*/\1/')
if [ "$header_only" = "0" ] && { [ "$commit_type" = "feat" ] || [ "$commit_type" = "fix" ]; }; then
  grep -Eq '^Evidence: ' "$msg_file" \
    || fail "feat/fix commits require an 'Evidence:' line (the DoD receipt)"
fi
exit 0
