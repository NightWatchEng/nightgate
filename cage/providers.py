"""Pluggable pre-flight services for the cage.

A project declares services in cage.toml as [[service]] entries; enroll
renders ONE derived services.sh defining three hooks the generic runner calls
when the file exists (and silently doesn't when it doesn't — default off):

  services_preflight  read-only machine-reality checks, run in pre-flight and
                      --check: binaries resolvable, ports free. Prints a
                      reason and returns 1 -> the run is SKIPPED before a
                      bead is claimed, never died on mid-run.
  services_start      brings services up right before the session and exports
                      connection env the session inherits. Failure -> stop +
                      skip.
  services_stop       tears everything down, including data dirs. Runs in
                      post-run ALWAYS (timeout and forbidden-path runs too).

Option values are validated here and baked into the rendered script — nothing
in services.sh comes from runtime input; enroll additionally bash -n's the
rendered artifact (defense in depth).

Two projects sharing the default postgres port would collide if their runs
overlap: give each project its own port in [[service]].
"""

from __future__ import annotations

import re

from .config import CageConfigError

# \Z, not $: '$' matches before a trailing newline, and one smuggled newline
# in a TOML basic string would detach a rendered error handler mid-script.
_DB_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}\Z")

# Founder decision (2026-08-20): the DB option is brew-ephemeral — a throwaway
# cluster per run on the laptop, torn down in post-run. No standing infra.
_POSTGRES_SETUP = """\
# brew's versioned postgres formulas are keg-only; surface their binaries
for _PG_BIN in /opt/homebrew/opt/postgresql@*/bin /usr/local/opt/postgresql@*/bin; do
  [ -d "$_PG_BIN" ] && PATH="$_PG_BIN:$PATH"
done
export PATH"""

_POSTGRES_PREFLIGHT = """\
  command -v nc >/dev/null 2>&1 || {{ echo "postgres-ephemeral: nc not found (port check needs it)"; return 1; }}
  command -v initdb >/dev/null 2>&1 || {{ echo "postgres-ephemeral: initdb not found (brew install postgresql@17)"; return 1; }}
  command -v pg_ctl >/dev/null 2>&1 || {{ echo "postgres-ephemeral: pg_ctl not found"; return 1; }}
  command -v createdb >/dev/null 2>&1 || {{ echo "postgres-ephemeral: createdb not found"; return 1; }}
  _PG_DIR="$HOME/.cage-svc/$CAGE_PROJECT/postgres"
  # our own stale instance (hard-killed run) is reclaimable at start;
  # anything else on the port is a real conflict
  if nc -z 127.0.0.1 {port} >/dev/null 2>&1 && [ ! -f "$_PG_DIR/postmaster.pid" ]; then
    echo "postgres-ephemeral: port {port} in use and not ours"; return 1
  fi"""

_POSTGRES_START = """\
  _PG_DIR="$HOME/.cage-svc/$CAGE_PROJECT/postgres"
  # reclaim a stale instance a hard-killed run left running
  [ -f "$_PG_DIR/postmaster.pid" ] && pg_ctl -D "$_PG_DIR" -m fast stop >/dev/null 2>&1
  rm -rf "$_PG_DIR"; mkdir -p "$_PG_DIR"
  initdb -D "$_PG_DIR" -U cage --auth=trust >/dev/null 2>&1 \\
    || {{ echo "postgres-ephemeral: initdb failed"; return 1; }}
  # trust auth = single-user-laptop tradeoff for throwaway per-run data:
  # TCP bound to loopback only, unix socket pinned into the 0700 data dir
  # (never world-accessible /tmp)
  pg_ctl -D "$_PG_DIR" -l "$_PG_DIR/pg.log" -w \\
    -o "-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories=$_PG_DIR" start \\
    || {{ echo "postgres-ephemeral: pg_ctl start failed"; return 1; }}
  createdb -h 127.0.0.1 -p {port} -U cage {database} \\
    || {{ echo "postgres-ephemeral: createdb {database} failed"; return 1; }}
  export CAGE_DATABASE_URL="postgresql://cage@127.0.0.1:{port}/{database}"
  echo "postgres-ephemeral: up on 127.0.0.1:{port}/{database}\""""

_POSTGRES_STOP = """\
  _PG_DIR="$HOME/.cage-svc/$CAGE_PROJECT/postgres"
  if [ -d "$_PG_DIR" ]; then
    pg_ctl -D "$_PG_DIR" -m fast stop >/dev/null 2>&1 || true
    if [ -f "$_PG_DIR/postmaster.pid" ]; then
      # a hung postmaster keeps its data dir — deleting it out from under a
      # live process would orphan the port with no reclaim handle
      echo "postgres-ephemeral: stop failed, postmaster.pid persists — keeping $_PG_DIR for next-run reclaim"
    else
      rm -rf "$_PG_DIR"
    fi
  fi"""


def _postgres_ephemeral(options: dict) -> dict:
    port = options.get("port", 54329)
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise CageConfigError("[[service]] postgres-ephemeral: port must be an "
                              "integer in [1024, 65535]")
    database = options.get("database", "cage")
    if not isinstance(database, str) or not _DB_NAME_RE.match(database):
        raise CageConfigError("[[service]] postgres-ephemeral: database must match "
                              f"{_DB_NAME_RE.pattern}")
    unknown = set(options) - {"provider", "port", "database"}
    if unknown:
        raise CageConfigError("[[service]] postgres-ephemeral: unknown options "
                              f"(fail-closed): {', '.join(sorted(unknown))}")
    fmt = {"port": port, "database": database}
    return {
        "setup": _POSTGRES_SETUP,
        "preflight": _POSTGRES_PREFLIGHT.format(**fmt),
        "start": _POSTGRES_START.format(**fmt),
        "stop": _POSTGRES_STOP.format(**fmt),
    }


PROVIDERS = {
    "postgres-ephemeral": _postgres_ephemeral,
}


def validate_services(entries: list) -> list[dict]:
    """Validate raw [[service]] tables; returns them normalized."""
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise CageConfigError("[[service]] must be an array of tables")
    seen: set[str] = set()
    for entry in entries:
        provider = entry.get("provider")
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise CageConfigError(
                f"[[service]].provider {provider!r} unknown — available: "
                + ", ".join(sorted(PROVIDERS)))
        if provider in seen:
            raise CageConfigError(f"[[service]] duplicate provider '{provider}'")
        seen.add(provider)
        PROVIDERS[provider](entry)  # this call IS the option validation (raises)
    return entries


def render_services(services: list[dict]) -> str | None:
    """The services.sh artifact, or None when no services are declared."""
    if not services:
        return None
    rendered = [PROVIDERS[e["provider"]](e) for e in services]
    lines = [
        "#!/bin/bash",
        "# GENERATED by 'cage enroll' from cage.toml [[service]] — "
        "do not edit; edit the toml and re-enroll.",
        "# Sourced by run.sh; hooks: services_preflight (read-only, also in",
        "# --check), services_start (before the session), services_stop (post-run,",
        "# always).",
        "",
    ]
    for block in rendered:
        lines += [block["setup"], ""]
    lines += ["services_preflight() {"]
    for block in rendered:
        lines += [block["preflight"]]
    lines += ["  return 0", "}", "", "services_start() {"]
    for block in rendered:
        lines += [block["start"]]
    lines += ["  return 0", "}", "", "services_stop() {"]
    # teardown in reverse start order
    for block in reversed(rendered):
        lines += [block["stop"]]
    lines += ["  return 0", "}"]
    return "\n".join(lines) + "\n"
