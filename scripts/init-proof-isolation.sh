#!/usr/bin/env bash
# usage: init-proof-isolation.sh
#
# Run before scripts/init-proof.sh in CI. Fails unless `warden` on PATH is the
# one in uv's tool bin directory. Then sets protocol.allow=never in the global
# git config, so git refuses every transport, local paths included, whatever
# the host, case, port or URL form. It checks the spellings of the platform
# repository listed below, and only those: for each, `git ls-remote` must exit
# non-zero with git's own "transport '...' not allowed", which git prints
# before it connects. A spelling that reaches a repository, or fails any other
# way (a route that only lacks credentials on this runner, or a
# protocol.<name>.allow or GIT_ALLOW_PROTOCOL setting that re-allows its
# transport), fails this script. It does not cover a non-git route, such as
# urllib, which reads no git config.
set -euo pipefail

tool_bin="$(uv tool dir --bin)"
where="$(command -v warden)" || { echo "no warden on PATH" >&2; exit 1; }
if [ "$where" != "$tool_bin/warden" ]; then
  echo "warden resolves to '$where', not the uv tool's $tool_bin/warden" >&2
  exit 1
fi
echo "warden: $where ($(warden --version))"

git config --global protocol.allow never

export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=5"
for url in https://github.com/NightWatchEng/nightgate \
           https://github.com/nightwatcheng/nightgate \
           http://github.com/NightWatchEng/nightgate \
           ftps://github.com/NightWatchEng/nightgate.git \
           ftp://github.com/NightWatchEng/nightgate.git \
           git@github.com:NightWatchEng/nightgate.git \
           git@github.com:nightwatcheng/nightgate.git \
           git@github.com:/NightWatchEng/nightgate.git \
           ssh://git@github.com/NightWatchEng/nightgate.git \
           ssh://git@github.com/nightwatcheng/nightgate.git \
           ssh://git@github.com:22/NightWatchEng/nightgate.git \
           git+ssh://git@github.com/NightWatchEng/nightgate.git \
           git://github.com/NightWatchEng/nightgate.git; do
  if out="$(git ls-remote --tags "$url" 2>&1)"; then
    echo "git reached the platform through $url; it must be unreachable here" >&2
    exit 1
  fi
  case "$out" in
    *"transport '"*"' not allowed"*) echo "refused: $url" ;;
    *) printf 'git failed on %s without refusing its transport:\n%s\n' "$url" "$out" >&2
       exit 1 ;;
  esac
done
