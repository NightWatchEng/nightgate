#!/usr/bin/env bash
# Prints the README's "Try it" block byte for byte: every line between the
# first fence under the "## Try it" heading and its closing fence, blank lines
# included, nothing else. tests/test_init_proof.py reads the same block in
# Python and pins the two readings equal, so what CI runs is what the README
# shows.
#
# usage: scripts/readme-try-it.sh [README]
#
# Exit 1, printing nothing, when the README has no closed fenced block under
# that heading.
set -euo pipefail

readme="${1:-$(cd "$(dirname "$0")/.." && pwd)/README.md}"
[ -f "$readme" ] || { echo "readme-try-it: no README at $readme" >&2; exit 1; }

awk '
  /^## / { if (in_section) exit; in_section = ($0 == "## Try it"); next }
  in_section && /^```/ { if (in_fence) { found = 1; exit }; in_fence = 1; next }
  in_section && in_fence { block = block $0 "\n" }
  END { if (!found) exit 1; printf "%s", block }
' "$readme" || { echo "readme-try-it: $readme has no closed fenced block under \"## Try it\"" >&2; exit 1; }
