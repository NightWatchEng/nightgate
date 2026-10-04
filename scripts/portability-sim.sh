#!/bin/bash
# Portability proof: a full gated-PR simulation on the example project
# The enrollment steps prove the config resolves; THIS
# proves the gate actually gates — both directions:
#   clean change    -> warden review exits 0
#   planted secret  -> warden review exits 1 (core mechanical rule)
#   debugger hook   -> warden review exits 1 (engine:declarative rule the
#                      example owns in a rule FILE — no project Python)
#   tag ceiling     -> warden memory check-vocabulary exits 0 on the clean
#                      corpus, 1 on a planted undecided tag, 0 again once a
#                      one-line receipt is written, 2 on a declaration it
#                      cannot read
# For the review probes anything else (including exit 2 = gate did not run)
# fails the proof; the vocabulary probes assert their full contract, so the
# one place exit 2 is EXPECTED is the unreadable declaration.
# Runs in CI on every platform PR and locally: scripts/portability-sim.sh
set -euo pipefail

PLATFORM="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PROJ="$TMP/proj"

# The example as its own committed repository, the copy CI also certifies.
bash "$PLATFORM/scripts/example-standalone.sh" "$PROJ"
cd "$PROJ"

run_review() { # $1 = expected exit code, $2 = label
  set +e
  uv run --locked --project "$PLATFORM" warden review --base main --no-comment \
    > "$TMP/review-$2.log" 2>&1
  local got=$?
  set -e
  if [ "$got" -ne "$1" ]; then
    echo "portability-sim FAIL: $2 change — expected exit $1, got $got" >&2
    cat "$TMP/review-$2.log" >&2
    exit 1
  fi
  echo "portability-sim: $2 change -> exit $got (expected $1) OK"
}

run_vocabulary_check() { # $1 = expected exit code, $2 = label
  set +e
  uv run --locked --project "$PLATFORM" warden memory check-vocabulary \
    > "$TMP/vocab-$2.log" 2>&1
  local got=$?
  set -e
  if [ "$got" -ne "$1" ]; then
    echo "portability-sim FAIL: vocabulary $2 — expected exit $1, got $got" >&2
    cat "$TMP/vocab-$2.log" >&2
    exit 1
  fi
  echo "portability-sim: vocabulary $2 -> exit $got (expected $1) OK"
}

# 1. A clean change must pass the gate.
git checkout -qb feature-clean
printf '\n# simulated feature note\n' >> hello_svc/app.py
git commit -qam "feat: clean simulated change"
run_review 0 clean

# 2. A planted secret must be BLOCKED (HIGH mechanical rule).
git checkout -q main && git checkout -qb feature-secret
printf '\nAPI_KEY = "sk-live_SIMULATED1234567890abcd"\n' >> hello_svc/app.py
git commit -qam "feat: simulated leak"
run_review 1 secret

# 3. A declarative rule (zero Python in the consuming repo) must BLOCK too.
git checkout -q main && git checkout -qb feature-debug
printf '\ndef _debug():\n    breakpoint()\n' >> hello_svc/app.py
git commit -qam "feat: simulated debugger hook"
run_review 1 declarative

# 4. A PROJECT CHECKER (.warden/checkers/*.py, engine:python) must BLOCK too —
#    the plugin seam a consumer reaches for when a regex will not do
#    The probe breaks the handler contract structurally:
#    a bare dict where (status, body) is declared.
git checkout -q main && git checkout -qb feature-contract
printf '\ndef broken_probe() -> tuple[int, dict]:\n    return {"status": "missing"}\n' >> hello_svc/app.py
git commit -qam "feat: simulated handler-contract break"
run_review 1 checker

# 5. The tag-vocabulary drift ceiling must gate with only
#    shipped surfaces: the example declares `ceiling: max_undecided: 0` in
#    .warden/memory/tags.yaml and runs `warden memory check-vocabulary` in
#    its CI. `memory ingest` deliberately exits 0 on a breach (cage/run.sh
#    reads that code as "was the corpus fed"), so this is the step that can
#    make a consumer's CI red — proven both directions, plus the remedy and
#    the fail-closed case, so no exit code of the contract is asserted only
#    in prose.
git checkout -q main && git checkout -qb feature-vocabulary
run_vocabulary_check 0 clean
# Plant one committed attest shard carrying a tag with no recorded
# disposition — the shape a review round leaves when it coins a name.
mkdir -p .warden/memory/attest
cat > .warden/memory/attest/20260902T000000Z-simulated-coined.json <<'EOF'
{"records": [{"id": "sim0001", "ts": "2026-09-02T00:00:00+00:00", "seq": 0,
  "source": "attest", "sha": "0000000000000000000000000000000000000000",
  "base_sha": "0000000000000000000000000000000000000000",
  "rule_id": "unmapped:simulated-coined-class", "tags": ["simulated-coined-class"],
  "dir_prefix": "hello_svc", "file": "hello_svc/app.py", "line": 1,
  "severity": "LOW", "finding": "simulated", "evidence": "simulated",
  "status": "fixed"}]}
EOF
git add -A && git commit -qm "review: simulated round that coined a tag"
run_vocabulary_check 1 breached
# The cheap remedy: one line under left_undeclared: decides nothing about the
# class and clears the obligation.
cat >> .warden/memory/tags.yaml <<'EOF'
left_undeclared:
  simulated-coined-class: >-
    seen once in a simulated review round at n=1, recorded so the backlog
    cannot grow in silence, and decided nothing about the class today
EOF
git commit -qam "memory: receipt for the coined tag"
run_vocabulary_check 0 receipted
# A declaration the loader cannot read is never headroom: exit 2, not 0.
sed -i.bak 's/max_undecided: 0/max_undecided: none/' .warden/memory/tags.yaml && rm -f .warden/memory/tags.yaml.bak
git commit -qam "memory: simulated broken ceiling declaration"
run_vocabulary_check 2 unreadable

echo "portability-sim PASS: the example project's gate passes clean diffs, blocks planted secrets, enforces a declarative rule with no project Python, runs the project's own checker plugin, and reddens on a tag-vocabulary ceiling breach using only shipped surfaces"
