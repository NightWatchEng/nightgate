"""cage service providers: [[service]] schema + services.sh rendering.

Default off is the load-bearing property: no [[service]] entries must leave
every rendered artifact — and the runner's behavior — exactly as before.
"""

import subprocess
from pathlib import Path

import pytest

from cage import cli as cage_cli
from cage import config as cage_config
from cage import providers as cage_providers
from cage import render as cage_render

BASE = """\
[project]
name = "sampleproj"
github = "acme/sampleproj"
live_checkout = "/work/sampleproj"
worktree = "/work/sampleproj-run"

[triggers.schedule]

[gate]
forbidden_paths = []
"""

PG = """
[[service]]
provider = "postgres-ephemeral"
port = 54329
database = "sampledb"
"""


def write_toml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "cage.toml"
    path.write_text(body)
    return path


# ---------- schema -----------------------------------------------------------

def test_service_entries_parse_with_defaults(tmp_path):
    cfg = cage_config.load(write_toml(tmp_path, BASE + "[[service]]\nprovider = 'postgres-ephemeral'\n"))
    assert cfg.services == [{"provider": "postgres-ephemeral"}]


def test_no_services_is_the_default(tmp_path):
    cfg = cage_config.load(write_toml(tmp_path, BASE))
    assert cfg.services == []
    assert cage_providers.render_services(cfg.services) is None


@pytest.mark.parametrize("entry, fragment", [
    ("[[service]]\nprovider = 'mysql'\n", "unknown"),
    ("[[service]]\n", "unknown"),  # provider missing entirely
    ("[[service]]\nprovider = 'postgres-ephemeral'\nport = 80\n", "1024"),
    ("[[service]]\nprovider = 'postgres-ephemeral'\ndatabase = 'Bad-Name'\n", "database"),
    ("[[service]]\nprovider = 'postgres-ephemeral'\nvolume = '/x'\n", "unknown options"),
    ("[[service]]\nprovider = 'postgres-ephemeral'\n[[service]]\nprovider = 'postgres-ephemeral'\n",
     "duplicate"),
])
def test_invalid_service_entries_fail_closed(tmp_path, entry, fragment):
    with pytest.raises(cage_config.CageConfigError) as exc:
        cage_config.load(write_toml(tmp_path, BASE + entry))
    assert fragment in str(exc.value)


# ---------- rendering --------------------------------------------------------

def test_services_sh_renders_three_hooks_with_baked_options(tmp_path):
    cfg = cage_config.load(write_toml(tmp_path, BASE + PG))
    body = cage_providers.render_services(cfg.services)
    for hook in ("services_preflight()", "services_start()", "services_stop()"):
        assert hook in body
    assert "-p 54329" in body and "createdb -h 127.0.0.1 -p 54329" in body
    assert "postgresql://cage@127.0.0.1:54329/sampledb" in body
    assert "GENERATED" in body
    script = tmp_path / "services.sh"
    script.write_text(body)
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_enroll_writes_and_removes_services_sh(tmp_path):
    toml = write_toml(tmp_path, BASE + PG)
    written = cage_render.enroll(cage_config.load(toml))
    services = tmp_path / "services.sh"
    assert services in written and services.is_file()
    # config drops the services -> re-enroll must remove the stale artifact,
    # or the cage keeps starting a database the toml no longer declares
    toml.write_text(BASE)
    cage_render.enroll(cage_config.load(toml))
    assert not services.exists()


def test_newline_smuggled_dbname_is_rejected(tmp_path):
    # regression (review): '$'-anchored regex let 'sampledb\n' through and the
    # rendered artifact dropped services_start/stop definitions mid-source
    body = BASE + '[[service]]\nprovider = "postgres-ephemeral"\ndatabase = "sampledb\\n"\n'
    with pytest.raises(cage_config.CageConfigError, match="database"):
        cage_config.load(write_toml(tmp_path, body))


def test_services_sourced_after_path_reset():
    # regression (review): sourcing services.sh before the runner's fixed
    # PATH assignment wiped the keg-only postgres dirs the setup block adds
    runner = cage_render.runner_script()
    path_reset = runner.index('export PATH="$HOME/.local/bin')
    services_source = runner.index('. "$CAGE_SELF/services.sh"')
    assert services_source > path_reset


def test_provider_reclaims_own_stale_instance(tmp_path):
    cfg = cage_config.load(write_toml(tmp_path, BASE + PG))
    body = cage_providers.render_services(cfg.services)
    # preflight tolerates a busy port only when our postmaster.pid exists...
    assert 'nc -z 127.0.0.1 54329 >/dev/null 2>&1 && [ ! -f "$_PG_DIR/postmaster.pid" ]' in body
    # ...start reclaims it, and stop never deletes a dir under a live postmaster
    assert '[ -f "$_PG_DIR/postmaster.pid" ] && pg_ctl -D "$_PG_DIR" -m fast stop' in body
    assert "keeping $_PG_DIR for next-run reclaim" in body
    assert "command -v nc" in body  # port check fails closed without nc
    assert "unix_socket_directories=$_PG_DIR" in body  # no world-readable /tmp socket


def test_enroll_with_services_is_idempotent(tmp_path):
    toml = write_toml(tmp_path, BASE + PG)
    first = {p.name: p.read_bytes() for p in cage_render.enroll(cage_config.load(toml))}
    second = {p.name: p.read_bytes() for p in cage_render.enroll(cage_config.load(toml))}
    assert "services.sh" in first and first == second


def test_runner_guards_every_hook_call():
    # no services.sh -> hooks undefined -> runner must guard each call with
    # declare -F, keeping behavior byte-identical to the pre-services cage
    runner = cage_render.runner_script()
    for fn in ("services_preflight", "services_start", "services_stop"):
        assert f"declare -F {fn}" in runner, f"{fn} is never guarded"
    assert '. "$CAGE_SELF/services.sh"' in runner


def test_cli_enroll_with_services(tmp_path, capsys):
    toml = write_toml(tmp_path, BASE + PG)
    assert cage_cli.main(["enroll", str(toml)]) == 0
    out = capsys.readouterr().out
    assert "services.sh" in out


# ---------- runtime behavior (bash-level, no real postgres) ------------------

def test_hooks_source_and_export_into_parent_shell(tmp_path):
    """The start hook must run in the parent shell so exports survive —
    simulate the runner's sourcing pattern with a stub services.sh."""
    stub = tmp_path / "services.sh"
    stub.write_text(
        "services_preflight() { return 0; }\n"
        "services_start() { export CAGE_DATABASE_URL='postgresql://x'; return 0; }\n"
        "services_stop() { echo stopped; return 0; }\n")
    probe = (
        f". {stub}\n"
        "declare -F services_preflight >/dev/null && services_preflight\n"
        "declare -F services_start >/dev/null && services_start\n"
        "echo \"url=$CAGE_DATABASE_URL\"\n"
        "services_stop\n")
    result = subprocess.run(["bash", "-uc", probe], capture_output=True, text=True)
    assert result.returncode == 0
    assert "url=postgresql://x" in result.stdout
    assert "stopped" in result.stdout
