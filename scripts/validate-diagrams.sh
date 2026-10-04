#!/usr/bin/env bash
# Render every ```mermaid block under the wiki sources with the REAL Mermaid
# engine, so a diagram that will not render cannot reach the
# wiki. tests/test_docs.py counts fences and checks the first token, which is
# cheap and runs everywhere — but it passes diagrams the parser rejects. Two
# shipped in the C4 ladder's first cut with that test green: a node id named
# `graph` (a reserved keyword) and a stray `;` inside a sequence message (the
# class distill once published to a live wiki).
#
# The files are handed to mermaid-cli WHOLE (`-i file.md`) rather than having
# the fences cut out here first. An earlier cut extracted blocks with awk
# anchored at column 0, which silently skipped any fence indented inside a
# list item or blockquote — GitHub renders those, so the gate was blind to a
# whole class of diagram while reporting green (cross-examination, H2). Not
# owning an extractor is the fix: mermaid-cli finds the fences itself.
#
# TOOLCHAIN PINNING (a recorded decision): the earlier `npx -y
# @mermaid-js/mermaid-cli@11.16.0` pinned only the top-level version — the
# transitive tree floated (puppeteer peer: ^23 || ^24 || ^25) and every
# resolved package ran its lifecycle scripts in the job that validates every
# PR. Now the FULL tree is pinned by scripts/mermaid/package-lock.json
# (integrity hashes) and installed with `npm ci --ignore-scripts`, so what
# runs is what was reviewed and nothing executes at install time. The one
# script this toolchain ever relied on — puppeteer's postinstall browser
# download — is replaced by an explicit executable: PUPPETEER_EXECUTABLE_PATH,
# a probed system Chrome, or a browser previously fetched into puppeteer's
# cache (`npx puppeteer browsers install chrome`).
#
#   Local:  bash scripts/validate-diagrams.sh [dir]
#   CI:     set PUPPETEER_EXECUTABLE_PATH to a preinstalled Chrome. Puppeteer
#           uses exactly that binary, so this costs no chromium fetch.
#
# EXIT CODES are the contract a caller branches on: 0 every diagram rendered;
# 1 at least one diagram did not render; 2 the toolchain could not be set up
# (no browser, npm ci failed, a bad setting); 3 the headless browser did not
# LAUNCH for at least one file and no diagram failed; 4 no diagram was checked
# at all (no markdown, or no mermaid block, under the directory). 3 is a runner
# problem, not a diagram problem, and the one a caller may retry: wiki-sync
# does, a bounded number of times. A diagram failure beside a launch failure
# is 1. 4 is apart from 1 because ci.yml's known-bad control reads 1 as "the
# diagram was rejected", and a validator that saw nothing rejected nothing.
# Residual: under `set -e` a failing command still ends the script with its
# own status, which can be 1, so that control also requires the FAIL line.
#
# PUPPETEER_LAUNCH_TIMEOUT_MS (default 90000) is how long puppeteer waits for
# Chrome to hand back its WebSocket endpoint. Puppeteer's own default is
# 30000, and a hosted runner has missed it with no diagram changed.
set -euo pipefail

SRC="${1:-docs/wiki}"
TOOLCHAIN="$(cd "$(dirname "$0")/mermaid" && pwd)"

# Browser preflight. Without it, a missing/moved Chrome makes EVERY file fail
# with "Mermaid parse error" plus a puppeteer stack trace, blaming diagrams
# nobody touched for a runner-image change — and the real cause ("no executable
# was found") is scrolled off. Fail closed, but name the actual cause.
if [ -n "${PUPPETEER_EXECUTABLE_PATH:-}" ]; then
  if [ ! -x "${PUPPETEER_EXECUTABLE_PATH}" ]; then
    echo "TOOLCHAIN: PUPPETEER_EXECUTABLE_PATH=${PUPPETEER_EXECUTABLE_PATH} is not an executable." >&2
    echo "           This is a runner/browser problem, not a diagram problem." >&2
    exit 2
  fi
else
  for CANDIDATE in \
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
      /usr/bin/google-chrome /usr/bin/chromium /usr/bin/chromium-browser; do
    if [ -x "$CANDIDATE" ]; then
      export PUPPETEER_EXECUTABLE_PATH="$CANDIDATE"
      break
    fi
  done
  # No env, no system Chrome: puppeteer can still find a browser someone
  # fetched into its cache — but if that is absent too, say so HERE rather
  # than letting every diagram fail with a stack trace that blames the docs.
  # Probed for ENTRIES, not bare existence: an empty cache dir (the residue
  # of an interrupted fetch) is exactly as browserless as a missing one, and
  # `-d` alone waved it through into the per-diagram stack traces this
  # preflight exists to prevent (found in review). Residual, named: a
  # cache holding a PARTIAL download still passes — entry presence is as far
  # as bash can honestly probe without reimplementing puppeteer's resolver.
  PPTR_CACHE="${PUPPETEER_CACHE_DIR:-$HOME/.cache/puppeteer}"
  if [ -z "${PUPPETEER_EXECUTABLE_PATH:-}" ] \
     && [ -z "$(ls -A "$PPTR_CACHE" 2>/dev/null)" ]; then
    echo "TOOLCHAIN: no browser for puppeteer — set PUPPETEER_EXECUTABLE_PATH," >&2
    echo "           install Chrome, or run: npx puppeteer browsers install chrome" >&2
    echo "           (install scripts are disabled by design, so puppeteer will" >&2
    echo "           not download one on its own)." >&2
    exit 2
  fi
fi

LAUNCH_TIMEOUT_MS="${PUPPETEER_LAUNCH_TIMEOUT_MS:-90000}"
if ! [[ "$LAUNCH_TIMEOUT_MS" =~ ^[1-9][0-9]*$ ]]; then
  echo "TOOLCHAIN: PUPPETEER_LAUNCH_TIMEOUT_MS=${LAUNCH_TIMEOUT_MS} is not a whole number of milliseconds." >&2
  exit 2
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
printf '{"args":["--no-sandbox","--disable-setuid-sandbox"],"timeout":%s}' \
  "$LAUNCH_TIMEOUT_MS" > "$WORK/pp.json"

# Install the PINNED toolchain from the committed lockfile, scripts disabled.
# `npm ci` refuses a lockfile that does not match package.json and verifies
# every tarball against its recorded integrity hash — a floating or retagged
# package cannot slip in. Installed into the temp dir so the repo tree stays
# clean and every run is hermetic.
cp "$TOOLCHAIN/package.json" "$TOOLCHAIN/package-lock.json" "$WORK/"
if ! (cd "$WORK" && npm ci --ignore-scripts --no-audit --no-fund) \
     > "$WORK/npm-ci.log" 2>&1; then
  echo "TOOLCHAIN: npm ci for the pinned mermaid toolchain failed." >&2
  echo "           This is an install problem, not a diagram problem." >&2
  tail -20 "$WORK/npm-ci.log" | sed 's/^/    /' >&2
  exit 2
fi
MMDC="$WORK/node_modules/.bin/mmdc"
if [ ! -x "$MMDC" ]; then
  echo "TOOLCHAIN: npm ci reported success but $MMDC is missing/not executable." >&2
  exit 2
fi

shopt -s nullglob
files=("$SRC"/*.md)
if [ ${#files[@]} -eq 0 ]; then
  echo "No markdown found under $SRC - nothing to validate." >&2
  exit 4
fi

# puppeteer's wording when Chrome never came up: the wait for its WebSocket
# endpoint timing out, or the process failing to start at all
LAUNCH_FAILURE_RE='waiting for the WS endpoint URL|Failed to launch the browser process'
# Log excerpts read the FILE with `head` and indent the few lines it emits:
# `sed log | head` ends the reader early, SIGPIPEs the writer on a log past a
# pipe buffer, and under pipefail + set -e ended this script at 141 before
# its exit code was chosen.
fail=0; nolaunch=0; checked=0; withdiagrams=0
for f in "${files[@]}"; do
  base="$(basename "$f" .md)"
  grep -q '```mermaid' "$f" || continue
  withdiagrams=$((withdiagrams + 1))
  checked=$((checked + 1))
  if "$MMDC" -p "$WORK/pp.json" -i "$f" -o "$WORK/$base.out.md" >"$WORK/$base.log" 2>&1; then
    echo "OK  $base"
  elif grep -qE "$LAUNCH_FAILURE_RE" "$WORK/$base.log"; then
    echo "BROWSER $base - the headless browser did not launch, so this diagram was not checked:"
    head -n 4 "$WORK/$base.log" | sed 's/^/    /'
    nolaunch=1
  else
    echo "FAIL $base - Mermaid render failed:"
    head -n 12 "$WORK/$base.log" | sed 's/^/    /'
    fail=1
  fi
done

# A run that validated nothing is not a pass. Without this a typo'd path, an
# emptied directory, or a rename would report success having checked zero
# diagrams — the fail-open shape this whole script exists to close.
if [ "$withdiagrams" -eq 0 ]; then
  echo "No mermaid blocks found under $SRC - nothing to validate." >&2
  exit 4
fi

if [ "$fail" -eq 0 ] && [ "$nolaunch" -eq 1 ]; then
  echo "TOOLCHAIN: the headless browser did not launch (see the BROWSER lines)." >&2
  echo "           This is a runner/browser problem, not a diagram problem." >&2
  exit 3
fi
echo "Validated $checked file(s) containing mermaid diagrams."
exit $fail
