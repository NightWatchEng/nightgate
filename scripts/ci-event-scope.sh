#!/bin/sh
# What a CI event invalidates: everything, or only the job that reads the
# pull request's title.
#
# THE COST THIS EXISTS FOR. `edited` is in ci.yml's activity types
# deliberately and must stay: without it a pull request retitled AFTER CI went
# green fired no new run, and with non-strict branch protection the stale green
# satisfied the merge button, which is how an invalid header landed on main
# permanently. But only the `commit messages` job reads the title; the other
# jobs re-build, re-lint and re-run the whole suite for a text change none of
# them looks at. Measured over 60 consecutive pull_request runs (45 distinct
# head shas): 15 runs were a second or third build of a sha already built, and
# the `edited` trigger accounts for 326 runner-minutes of that. Runner-minutes,
# not wall clock: a run's wall clock is its slowest interpreter leg, and this
# decision does not touch that. It makes a run cost less and, by the seven
# jobs that now wait on it, take a few seconds longer — measured at 8 seconds on
# its first real run, against 326 minutes. Runner-minutes are the bill.
#
# THE TRAP, and the reason this is a script with an executable decision rather
# than an `if:` expression. A job skipped by `if:` publishes its check run as
# SKIPPED, and branch protection reads a skipped required check as a passed
# one. So a fast path is a way to turn a RED run green by editing the title,
# unless the fast path can only be taken where the same sha has already been
# green. That is what this asks the forge, and every answer that is not an
# instant at which it passed — no answer, an unreadable one, a shape that is
# not an instant — is the full run. Fail-closed is not a preference here: the
# failure mode is a merge button lit by a check that never ran.
#
# THE SECOND TRAP has two halves, and round 1 found the first version closing
# only one. A change of BASE BRANCH arrives as `edited` and genuinely
# invalidates every job, because the diff, the merge ref and every guard that
# compares against the base all move under it — that is the identity change,
# and it is checked before anything else. But the merge ref moves when the
# base branch's TIP moves too, which fires no pull_request event at all: a run
# that went green against main@A says nothing about the same head against
# main@B, and keying the fast path on the head sha alone would skip seven jobs
# on the strength of it. So the green run must also be NEWER than the base
# branch's newest run — every merge into it fires one — and anything else is
# the full build.
#
# Residual, recorded rather than claimed closed: a merge that fired no run on
# the base branch (a workflow disabled, a run that failed to create) leaves
# that timestamp older than it should be. It is the same delivery-window
# residual the trigger comment in ci.yml already records for `edited` itself,
# and it narrows rather than opens: with no base-branch run at all, the
# comparison has nothing to beat and the answer is the full build.
#
# Inputs, all through the environment so the caller is a fixed command with no
# arguments to get wrong:
#
#   EVENT_NAME        github.event_name
#   ACTION            github.event.action ('' on events that have none)
#   BASE_CHANGED      'true' or 'false' — whether github.event.changes.base is
#                     present. ANY OTHER VALUE, the empty string included, is
#                     the full build: round 1 found that an unset BASE_CHANGED
#                     fell straight through to the forge query, so deleting
#                     one line from the workflow's `env:` made the base-change
#                     branch unreachable with every test still green. It is
#                     the one input whose absence was permissive, and this is
#                     what makes every absence answer the same way.
#   HEAD_SHA          the pull request's head sha
#   BASE_REF          the base branch, for the base-movement question
#   GITHUB_REPOSITORY owner/name, for the forge queries
#   WORKFLOW_FILE     the workflow whose earlier runs are asked about
#   GREEN_AT_CMD      overrides the "when did this sha last pass" query
#   BASE_RUN_AT_CMD   overrides the "when did the base branch last run" query
#                     Both exist so every branch below is EXECUTED by a test
#                     rather than read off this file's text.
#
# Output: `fast=true` or `fast=false` on stdout with a `reason:` line, and the
# same `fast=` line appended to $GITHUB_OUTPUT when the forge set one.

set -u

EVENT_NAME="${EVENT_NAME-}"
ACTION="${ACTION-}"
BASE_CHANGED="${BASE_CHANGED-}"
HEAD_SHA="${HEAD_SHA-}"
BASE_REF="${BASE_REF-main}"
WORKFLOW_FILE="${WORKFLOW_FILE-ci.yml}"

decide() {
  echo "fast=$1"
  echo "reason: $2"
  if [ -n "${GITHUB_OUTPUT-}" ]; then
    echo "fast=$1" >> "$GITHUB_OUTPUT"
  fi
  exit 0
}

# An ISO-8601 UTC instant and nothing else. Both answers below come off the
# same forge clock, so a lexicographic compare of this exact shape is a
# chronological one; anything that is not this shape is an answer this cannot
# read, which is the full build.
is_instant() {
  case "$1" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z)
      return 0 ;;
    *) return 1 ;;
  esac
}

if [ "$EVENT_NAME" != "pull_request" ]; then
  decide false "a '$EVENT_NAME' event is not a pull request edit"
fi

if [ "$ACTION" != "edited" ]; then
  decide false "the activity type '$ACTION' changes the code under review"
fi

if [ "$BASE_CHANGED" != "false" ]; then
  decide false "the caller did not say the base branch was unchanged (changes.base: '$BASE_CHANGED') — a base change moves the diff, the merge ref and every guard that compares against the base, and an input this cannot read is treated as one"
fi

if [ -z "$HEAD_SHA" ]; then
  decide false "the event carries no head sha, so nothing can be asked about it"
fi

if [ -n "${GREEN_AT_CMD-}" ]; then
  # shellcheck disable=SC2086  # a command line, split deliberately
  green_at=$($GREEN_AT_CMD 2>&1) || green_at="__unanswered__"
else
  green_at=$(gh api "repos/${GITHUB_REPOSITORY-}/actions/workflows/${WORKFLOW_FILE}/runs?head_sha=${HEAD_SHA}&event=pull_request&status=success" --jq '[.workflow_runs[].created_at] | max // ""' 2>&1) || green_at="__unanswered__"
fi

if ! is_instant "$green_at"; then
  decide false "no successful run of $WORKFLOW_FILE on $HEAD_SHA the forge could name a time for (answer: '$green_at'), so every job still has something to prove — a skipped required check reads as a passed one, and a retitle must never be able to publish one over a run that was not green"
fi

if [ -n "${BASE_RUN_AT_CMD-}" ]; then
  # shellcheck disable=SC2086  # a command line, split deliberately
  base_at=$($BASE_RUN_AT_CMD 2>&1) || base_at="__unanswered__"
else
  base_at=$(gh api "repos/${GITHUB_REPOSITORY-}/actions/workflows/${WORKFLOW_FILE}/runs?branch=${BASE_REF}&event=push" --jq '[.workflow_runs[].created_at] | max // ""' 2>&1) || base_at="__unanswered__"
fi

if ! is_instant "$base_at"; then
  decide false "the forge could not say when $BASE_REF last moved (answer: '$base_at'), so whether the green run on $HEAD_SHA judged today's base is unknown — and unknown is not unchanged"
fi

newest=$(printf '%s\n%s\n' "$green_at" "$base_at" | sort | tail -1)
if [ "$newest" != "$green_at" ] || [ "$green_at" = "$base_at" ]; then
  decide false "$BASE_REF last ran at $base_at and the green run on $HEAD_SHA is from $green_at, so that run judged an older base — the merge ref has moved under this pull request since it passed"
fi

decide true "$WORKFLOW_FILE passed on $HEAD_SHA at $green_at, after $BASE_REF last moved at $base_at, and this edit changed only text no other job reads — so only the title job re-runs"
