#!/usr/bin/env bash
# A fresh clone reads the tracker with the command CONTRIBUTING.md gives.
#
# usage: scripts/tracker-from-a-clone.sh WORKDIR
#
# WORKDIR must not exist: the clone is made there and nothing is deleted to
# make room for it (a path that exists is refused with exit 2).
#
# Clones this checkout into WORKDIR — tracked files only, so no Dolt database
# under .beads/, which is every outside contributor's position — and runs the
# fenced block under CONTRIBUTING.md's "## Reading the tracker" heading inside
# it, as written. The block clones the tracker's Dolt history from the remote
# .beads/config.yaml names, refs/dolt/data on the git remote. Then asserts:
#   - `bd show $id` (a bead the published history is known to carry) exits 0
#     and prints that id;
#   - `bd ready` exits 0;
#   - `bd dolt pull` exits 0 — the refresh a collaborator runs afterwards;
#   - the clone's HEAD and working tree are what they were. The read must not
#     commit or write anything git sees: `bd init` in a clone commits, and
#     appends to CLAUDE.md, which is why the block is not `bd init`.
#
# TRACKER_PROOF_REMOTE, when set, is exported to bd as BD_SYNC_REMOTE for the
# block and the pull, so a runner with no ssh key reaches the same remote over
# a token-authenticated https spelling. bd 1.1.2's bootstrap persists whatever
# remote it cloned from into the clone's .beads/config.yaml (its
# finalizeSyncedBootstrap; review round 1 reproduced it with the token
# spelling), so in override mode that file is EXPECTED to change and nothing
# else git tracks is: the script checks that the override is the only change,
# restores the tracked file, and only then asserts the tree unchanged. What
# stays behind is inside the clone's gitignored .beads/embeddeddolt/: bd
# records the remote it cloned from there as its Dolt `origin` and in a git
# mirror cache (review round 2 found the override, credentials included, in
# both). That store is the clone's to discard — under $RUNNER_TEMP on a
# runner, with a token scoped to the run — which is why the override should
# never carry a credential that outlives the job. Anything this script
# prints on failure has the override's value redacted. Without the override,
# bd rewrites the same bytes it reads — the committed config.yaml is bd's own
# output — and nothing changes at all.
#
# Exit 1 names the condition that failed, with the command's output. Exit 2
# means the proof could not run: no bd on PATH, a WORKDIR that already exists,
# no CONTRIBUTING.md, or no closed fenced block under the heading.
set -euo pipefail

workdir="${1:?usage: scripts/tracker-from-a-clone.sh WORKDIR}"
root="$(cd "$(dirname "$0")/.." && pwd)"
contributing="${TRACKER_PROOF_CONTRIBUTING:-$root/CONTRIBUTING.md}"
id="agentops-8vvm.1"

# The override may carry a credential, so nothing printed here repeats it.
redact() {
  if [ -n "${TRACKER_PROOF_REMOTE:-}" ]; then
    awk -v s="$TRACKER_PROOF_REMOTE" '{
      while ((i = index($0, s)) > 0) $0 = substr($0, 1, i - 1) "<TRACKER_PROOF_REMOTE>" substr($0, i + length(s))
    } 1'
  else
    cat
  fi
}
fail() {
  printf '%s\n' "tracker from a clone: $1" | redact >&2
  if [ -n "${2:-}" ] && [ -f "$2" ]; then
    sed 's/^/  | /' "$2" | redact >&2
  fi
  exit 1
}
cannot() { echo "tracker from a clone: $1" >&2; exit 2; }

command -v bd >/dev/null 2>&1 || cannot "no bd on PATH"
[ -f "$contributing" ] || cannot "no CONTRIBUTING.md at $contributing"
[ ! -e "$workdir" ] || cannot "$workdir exists; pass a path that does not, nothing is deleted to make room"

# The block as CONTRIBUTING.md holds it: every line between the first fence
# under the heading and its closing fence. tests/test_tracker_from_a_clone.py
# reads the same block in Python and pins what it must say.
block="$(awk '
  /^## / { if (in_section) exit; in_section = ($0 == "## Reading the tracker"); next }
  in_section && /^```/ { if (in_fence) { found = 1; exit }; in_fence = 1; next }
  in_section && in_fence { block = block $0 "\n" }
  END { if (!found) exit 1; printf "%s", block }
' "$contributing")" || cannot "$contributing has no closed fenced block under \"## Reading the tracker\""

if [ -n "${TRACKER_PROOF_REMOTE:-}" ]; then
  export BD_SYNC_REMOTE="$TRACKER_PROOF_REMOTE"
fi

git clone -q "$root" "$workdir"
head_before="$(git -C "$workdir" rev-parse HEAD)"

log="$(mktemp)"
trap 'rm -f "$log"' EXIT
(cd "$workdir" && CI=true bash -euo pipefail -c "$block") >"$log" 2>&1 \
  || fail "the CONTRIBUTING.md block failed in the clone" "$log"
(cd "$workdir" && bd show "$id") >"$log" 2>&1 \
  || fail "bd show $id failed after the block" "$log"
grep -qF -- "$id" "$log" \
  || fail "bd show $id did not print the id" "$log"
(cd "$workdir" && bd ready) >"$log" 2>&1 \
  || fail "bd ready failed after the block" "$log"
(cd "$workdir" && CI=true bd dolt pull) >"$log" 2>&1 \
  || fail "bd dolt pull failed after the block" "$log"
if [ -n "${TRACKER_PROOF_REMOTE:-}" ]; then
  # bd persisted the override into the clone's config.yaml: that one line, and
  # nothing else, may differ. Then put the tracked file back.
  changed="$(git -C "$workdir" status --porcelain)"
  [ "$changed" = " M .beads/config.yaml" ] \
    || fail "with the override set, expected only .beads/config.yaml to change, saw:
$changed"
  # Exactly the tracked sync.remote line out and the override in, spelled
  # the way bd writes it — nothing else may move.
  tracked="$(git -C "$workdir" show HEAD:.beads/config.yaml | grep '^sync\.remote: ')" \
    || fail "HEAD's .beads/config.yaml names no sync.remote to compare the override against"
  expected="$(printf -- '-%s\n+sync.remote: "%s"' "$tracked" "$TRACKER_PROOF_REMOTE")"
  actual="$(git -C "$workdir" diff -U0 -- .beads/config.yaml | grep '^[-+][^-+]')"
  [ "$actual" = "$expected" ] \
    || fail "the override changed .beads/config.yaml other than its sync.remote line becoming the override:
$actual"
  git -C "$workdir" checkout -q -- .beads/config.yaml
fi
head_after="$(git -C "$workdir" rev-parse HEAD)"
[ "$head_after" = "$head_before" ] \
  || fail "the block moved the clone's HEAD from $head_before to $head_after"
dirty="$(git -C "$workdir" status --porcelain)"
[ -z "$dirty" ] || fail "the block left the clone's tree dirty:
$dirty"

echo "tracker from a clone: $id read from a fresh clone; HEAD and tree unchanged"
