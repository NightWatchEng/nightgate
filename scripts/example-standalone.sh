#!/bin/bash
# Copy examples/hello-svc out as its own committed git repository at DEST, with
# no evidence run dirs: the state docs/wiki/Quickstart.md starts from, where the
# example's .github/workflows is the git root's. CI's `portability proof` job
# certifies the example there, and scripts/portability-sim.sh gates PRs there.
# usage: scripts/example-standalone.sh DEST   (DEST must not exist)
set -euo pipefail

PLATFORM="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:?usage: example-standalone.sh DEST}"
if [ -e "$DEST" ]; then
  echo "example-standalone: $DEST already exists" >&2
  exit 2
fi

mkdir -p "$DEST"
cp -R "$PLATFORM/examples/hello-svc/." "$DEST"
rm -rf "$DEST/.warden/out"
cd "$DEST"
git init -q -b main
git config user.email sim@nightgate.local
git config user.name "portability-sim"
git add -A && git commit -qm "init: enrolled example project"
