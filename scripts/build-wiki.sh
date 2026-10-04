#!/usr/bin/env bash
# Render docs/wiki/*.md (the single source of truth) into build/wiki/*.md for
# publication to the GitHub wiki tab.
#
# The wiki tab is meant to be a rendered MIRROR — never hand-edit a page there.
# Edit the file under docs/wiki/, open a PR, and the wiki-sync workflow
# republishes it on merge. That is a rule addressed to people, NOT a mechanism:
# GitHub keeps the wiki tab writable in the browser to anyone with repo write,
# and nothing in this repo can reject such an edit. What the publish path
# guarantees is narrower and worth stating exactly: every page in the manifest
# is overwritten from source on the next run, and no page outside it is ever
# touched.
#
# Four things happen on the way out:
#
#  1. Intra-wiki links lose their extension. `[x](Memory.md)` 404s on a wiki
#     — wiki page names carry no `.md` — so it becomes `[x](Memory)`.
#  2. Out-of-tree links become absolute repo URLs. `docs/design/` and the repo
#     root are NOT mirrored; left relative they resolve to nothing from the
#     wiki's own namespace. (Every reader-facing page IS mirrored — the
#     enrollment and policy-contract guides moved into docs/wiki/ so there is
#     one canonical copy of each, not a wiki page narrating a file.)
#  3. `_Sidebar.md` is generated, grouped by the section-label rows on Home's
#     table (a bold cell with no link: "**Usage**"). Without a sidebar GitHub
#     renders no nav at all, and
#     its fallback is alphabetical: `Adopting` would lead, `Home` would sit
#     between `Graph-Layer` and `Memory`, and `Why-This-Exists` — the page that
#     says "start here" — would sort last. (The C4 ladder happens to survive an
#     alphabetical sort, since `Architecture-1` < `Architecture-4`. The first
#     cut of this comment claimed otherwise, mistranslating distill's real
#     example, where `09-interests` outsorted `1-System-Context` — review round
#     1, F7.)
#  4. `.wiki-managed` is written — the manifest of pages this build owns. The
#     publisher deletes a wiki page ONLY if a previous manifest listed it and
#     this one does not, so retiring a source page retires the published page,
#     while a page this sync never owned is never touched. A dotfile is not a
#     wiki page: GitHub renders `.md`, so the manifest never appears in the nav.
#
# The sidebar order is DERIVED from the page table on Home.md rather than
# hand-listed here. One ordering, in the place a reader already sees it, and
# `test_home_table_lists_every_wiki_source_page` already fails when a page
# lands without a row. A page with no row is a hard stop below, not a silent
# omission: an unnavigable published page is the failure the sidebar exists
# to prevent.
#
#   Local:  bash scripts/build-wiki.sh
#   CI:     GITHUB_REPOSITORY is set by Actions and picks the repo URL up.
set -euo pipefail

SRC="${SRC:-docs/wiki}"
OUT="${OUT:-build/wiki}"
REPO_URL="https://github.com/${GITHUB_REPOSITORY:-NightWatchEng/agentops}"
MANIFEST=".wiki-managed"

[ -d "$SRC" ] || { echo "build-wiki: no source directory $SRC" >&2; exit 1; }
[ -f "$SRC/Home.md" ] || {
  echo "build-wiki: $SRC/Home.md is missing — it carries the sidebar order" >&2
  exit 1
}

rm -rf "$OUT"; mkdir -p "$OUT"

# --- the ordered nav, read out of Home's page table -------------------------
# Emits "target<TAB>label<TAB>nested" per row, in table order. A row marked
# with the ladder arrow (U+21B3, "↳") renders one level in.
#
# The `(#[^)]*)?` is load-bearing: `test_home_table_lists_every_wiki_source_page`
# accepts a row written `[Memory](Memory.md#recall)`, and without it that row
# was invisible here — the build then hard-stopped telling the author to add a
# row that was already there (review round 1, F8). Two parsers of one table
# must accept the same rows, or the stricter one's error points the wrong way.
order() {
  awk -F'|' '
    /^\| Page \| System \|/ { intable = 1; next }
    intable && /^## /        { exit }
    intable && /^\|/ {
      cell = $2
      if (cell ~ /^[[:space:]]*:?-+:?[[:space:]]*$/) next          # separator row
      if (match(cell, /\[[^]]*\]\([^)]*\.md(#[^)]*)?\)/) == 0) {
        # A linkless row carrying only bold text is a SECTION LABEL
        # ("**Usage**"), not a page. Emitted with an empty target so the
        # sidebar can group under it; anything else is skipped as before.
        if (match(cell, /\*\*[^*]+\*\*/) > 0) {
          heading = substr(cell, RSTART + 2, RLENGTH - 4)
          # A dash, not an empty first field: bash `read` treats TAB as IFS
          # whitespace, so a leading tab is stripped and the fields shift left
          # by one (the label lands in `target`). The dash is never a page
          # name, so the completeness check below cannot match it either.
          print "-\t" heading "\tsection"
        }
        next
      }
      item     = substr(cell, RSTART, RLENGTH)
      split_at = index(item, "](")
      label    = substr(item, 2, split_at - 2)
      target   = substr(item, split_at + 2, length(item) - split_at - 2)
      gsub(/\*/, "", label)
      sub(/#.*$/, "", target)          # nav links to the page, not a section
      sub(/\.md$/, "", target)
      print target "\t" label "\t" (index(cell, "\342\206\263") ? 1 : 0)
    }
  ' "$SRC/Home.md"
}

# --- link rewriting ---------------------------------------------------------
# Out-of-tree first: once `../..` and `..` are absolute they no longer match
# the intra-wiki pass. Directory targets (trailing `/`) go to /tree/, files to
# /blob/. From docs/wiki/, `../..` is the repo root and `..` is docs/.
# The out-of-tree expressions use `|` as the `s` delimiter, because their
# replacements are URLs full of `/`. They interpolate no page name.
#
# The PER-PAGE expressions use `/`, and that choice is the fix for round 2, F3.
# Page names are escaped into both halves — interpolated raw, a page named
# `Q&A` published `[QA](Q](Q&A.md)A)`, since sed expands an unescaped `&` in
# the replacement to the whole match, and `V1.2-Notes` silently RETARGETED a
# link to `V1x2-Notes.md`, because `.` is a metacharacter (round 1, F9). But
# escaping cannot save the DELIMITER itself: with `|` as the delimiter a page
# named `A|B` produced `\|`, which BSD sed hands to the ERE engine as
# alternation (corrupting the link to the unrelated page `B`) and GNU sed
# rejects outright. `/` is the one printable character a filename cannot
# contain, so no basename can ever close the expression early.
esc_lhs() { printf '%s' "$1" | sed 's/[][\\.^$*+?(){}|]/\\&/g'; }
esc_rhs() { printf '%s' "$1" | sed 's/[\\&]/\\&/g'; }

rewrite() {
  local page lhs rhs
  local -a args=(
    -e "s|\]\(\.\./\.\./([^)]*/)\)|](${REPO_URL}/tree/main/\1)|g"
    -e "s|\]\(\.\./\.\./([^)]*)\)|](${REPO_URL}/blob/main/\1)|g"
    -e "s|\]\(\.\./([^)]*/)\)|](${REPO_URL}/tree/main/docs/\1)|g"
    -e "s|\]\(\.\./([^)]*)\)|](${REPO_URL}/blob/main/docs/\1)|g"
  )
  for page in "$@"; do
    lhs="$(esc_lhs "$page")"
    rhs="$(esc_rhs "$page")"
    # `](Page.md)` and `](Page.md#anchor)` -> `](Page)` / `](Page#anchor)`.
    # The anchor is kept: dropping it still links, but stops landing where
    # the author pointed it.
    args+=(-e "s/\]\(${lhs}\.md([)#])/](${rhs}\1/g")
  done
  sed -E "${args[@]}"
}

# --- build ------------------------------------------------------------------
npages=0
declare -a PAGES
PAGES=()
for f in "$SRC"/*.md; do
  [ -f "$f" ] || continue
  base="$(basename "$f" .md)"
  case "$base" in _*) continue ;; esac   # _Sidebar & friends are generated
  PAGES+=("$base")
  npages=$((npages + 1))
done
[ "$npages" -gt 0 ] || { echo "build-wiki: no pages under $SRC" >&2; exit 1; }

for base in "${PAGES[@]}"; do
  # Source filenames are already wiki page names (Architecture-1-System-Context
  # -> the page of that name), so there is no name map to drift out of date.
  rewrite "${PAGES[@]}" < "$SRC/$base.md" > "$OUT/$base.md"
done

# --- completeness: every built page must have a place in the nav ------------
ORDERED="$(order)"
missing=""
for base in "${PAGES[@]}"; do
  [ "$base" = "Home" ] && continue        # Home is pinned first, not listed
  if ! printf '%s\n' "$ORDERED" | cut -f1 | grep -qxF -- "$base"; then
    missing="$missing $base"
  fi
done
if [ -n "$missing" ]; then
  echo "build-wiki: these pages have no row in $SRC/Home.md's page table, so" >&2
  echo "            they would publish with no sidebar entry:$missing" >&2
  echo "            Add a row to the table on Home.md — it IS the nav order." >&2
  exit 1
fi

# --- _Sidebar.md ------------------------------------------------------------
{
  echo "- [🏠 Home](Home)"
  echo
  printf '%s\n' "$ORDERED" | while IFS="$(printf '\t')" read -r target label nested; do
    if [ "$nested" = "section" ]; then printf -- '\n**%s**\n\n' "$label"; continue; fi
    [ -n "$target" ] || continue
    if [ "$nested" = "1" ]; then printf -- '  - [%s](%s)\n' "$label" "$target"
    else                         printf -- '- [%s](%s)\n'   "$label" "$target"
    fi
  done
  echo
  echo "---"
  echo
  echo "_Generated by \`scripts/build-wiki.sh\` from \`docs/wiki/\` — do not edit here._"
} > "$OUT/_Sidebar.md"

# --- .wiki-managed: the pages this build owns -------------------------------
for f in "$OUT"/*.md; do basename "$f"; done | LC_ALL=C sort > "$OUT/$MANIFEST"

echo "Built $(grep -c . "$OUT/$MANIFEST") wiki pages into $OUT:"
cat "$OUT/$MANIFEST"
