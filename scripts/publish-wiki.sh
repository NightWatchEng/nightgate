#!/usr/bin/env bash
# Reconcile a checked-out GitHub wiki clone against a build/wiki/ tree
# STAGES everything it changes — deletions and publications
# alike — so the caller's next step is a bare `git commit`. Round 2, F2: the
# first cut staged only the deletions, because publication went through plain
# `cp`; a caller that followed this header literally committed the retirement
# and nothing else while the script printed "N page(s) published".
#
#   bash scripts/publish-wiki.sh <wiki-clone-dir> [build-dir]
#
# WHY THIS IS A SCRIPT AND NOT SIX LINES OF `run:` YAML. The property that
# matters here — "deletes exactly the pages it previously published, and
# nothing else" — cannot be tested from inside a workflow file. Round 1 of
# review proved that: the guard was `assert "rm -rf" not in wf`, and inserting
# `git rm` into the same step deleted every unowned page with all twelve tests
# still green (F2). A script can be RUN against a fixture wiki, so the test
# observes the outcome instead of grepping for one spelling of destruction.
#
# THE OWNERSHIP RULE, exactly:
#
#   delete  <=>  the wiki's committed manifest lists the page  AND  this
#                build's manifest does not.
#
# Consequences, both deliberate:
#   * Retiring a source page retires the published page. Copy-only publishing
#     left the retired page served forever — outside the source of truth,
#     absent from the sidebar, still reachable by URL (F6).
#   * A page this sync never published is NEVER touched, including on the very
#     first run, when the wiki carries only the placeholder page a human made
#     by hand to bring the wiki repo into existence and no manifest exists yet.
set -euo pipefail

WIKI="${1:-}"
BUILD="${2:-build/wiki}"
MANIFEST=".wiki-managed"

[ -n "$WIKI" ] || { echo "publish-wiki: usage: publish-wiki.sh <wiki-clone-dir> [build-dir]" >&2; exit 2; }
[ -d "$WIKI" ] || { echo "publish-wiki: wiki clone dir '$WIKI' does not exist" >&2; exit 2; }
[ -d "$BUILD" ] || { echo "publish-wiki: build dir '$BUILD' does not exist — run scripts/build-wiki.sh first" >&2; exit 2; }
[ -f "$BUILD/$MANIFEST" ] || {
  echo "publish-wiki: '$BUILD/$MANIFEST' is missing. It is written by" >&2
  echo "              scripts/build-wiki.sh and names the pages this sync owns;" >&2
  echo "              without it, ownership is unknown and publishing would" >&2
  echo "              either delete pages it does not own or strand retired" >&2
  echo "              ones. Re-run scripts/build-wiki.sh." >&2
  exit 2
}

# A manifest entry names a wiki page and nothing else. The wiki-side manifest
# is read out of a store that anyone with repo write can edit in the browser,
# and it is the ONLY input that decides what gets deleted — so entries are
# validated rather than trusted (round 2, F4). A plain `Name.md` basename is
# the whole permitted vocabulary: no directory component, no `..`, no leading
# dot, no absolute path. `git rm` already refuses a path outside the repo, but
# refusing here means the reason reaches the log instead of a git error, and
# an entry naming a real file in a subdirectory of the wiki is refused too.
is_page_name() {
  case "$1" in
    */*|.*|"") return 1 ;;
    *.md)      return 0 ;;
    *)         return 1 ;;
  esac
}

# 1. Retire: pages a PREVIOUS run published that this build no longer has.
retired=0
if [ -f "$WIKI/$MANIFEST" ]; then
  while IFS= read -r page; do
    [ -n "$page" ] || continue
    if ! is_page_name "$page"; then
      echo "publish-wiki: ignoring '$page' in $WIKI/$MANIFEST — a manifest entry" >&2
      echo "              must be a plain <Name>.md wiki page name, with no path" >&2
      echo "              component. Nothing was deleted for this entry." >&2
      continue
    fi
    grep -qxF -- "$page" "$BUILD/$MANIFEST" && continue   # still ours
    [ -f "$WIKI/$page" ] || continue                      # already gone
    git -C "$WIKI" rm -q -- "$page"
    echo "retired (no longer in docs/wiki): $page"
    retired=$((retired + 1))
  done < "$WIKI/$MANIFEST"
fi

# 2. Publish: overwrite the managed pages, then the manifest that records them.
#    Nothing else in the clone is read or written.
published=0
while IFS= read -r page; do
  [ -n "$page" ] || continue
  if ! is_page_name "$page"; then
    echo "publish-wiki: '$page' in $BUILD/$MANIFEST is not a plain <Name>.md page" >&2
    echo "              name. That manifest is written by scripts/build-wiki.sh" >&2
    echo "              from basenames, so this means the build output was" >&2
    echo "              tampered with or the builder changed. Refusing." >&2
    exit 2
  fi
  cp "$BUILD/$page" "$WIKI/$page"
  git -C "$WIKI" add -- "$page"
  published=$((published + 1))
done < "$BUILD/$MANIFEST"
cp "$BUILD/$MANIFEST" "$WIKI/$MANIFEST"
git -C "$WIKI" add -- "$MANIFEST"

echo "publish-wiki: $published page(s) published, $retired retired."
