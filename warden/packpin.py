"""The skill pack a machine serves is the one repo.yaml's `platform.pin` names.

Neither `claude plugin marketplace add` nor `claude plugin install` takes a
version flag, but the marketplace SOURCE may carry a ref: `<url>#<tag>` clones
the marketplace at that tag and records `{"source": "git", "url", "ref"}` in
`<config dir>/plugins/known_marketplaces.json`; `claude plugin install` then
copies the pack out of that clone and records the copy's `version` in
`installed_plugins.json`. Measured on Claude Code 2.1.283:
`marketplace update` keeps a ref'd clone on its ref, and adding a marketplace
of the same name with a different source is refused, so a pin bump is
`marketplace remove`, `marketplace add` at the new tag, `plugin install`.

`check` reads those two records and the pinned clone's own manifests and
answers whether the installed pack is the pin's. It fails closed: a record it
cannot read is `unreadable` (exit 2), never a pass, and an absent marketplace
or plugin is `unpinned` (exit 1) — no pack is not the pinned pack.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

MARKETPLACE = "nightgate"
PLUGIN = "nightgate-skills"
MARKETPLACE_URL = "https://github.com/NightWatchEng/nightgate"

PINNED, UNPINNED, UNREADABLE = "pinned", "unpinned", "unreadable"
EXIT = {PINNED: 0, UNPINNED: 1, UNREADABLE: 2}


def default_config_dir() -> Path:
    """Where Claude Code keeps its plugin records: `CLAUDE_CONFIG_DIR`, else
    `~/.claude`."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def install_commands(pin: str) -> list[str]:
    """The install the docs and `warden init` spell, at PIN."""
    return [f"claude plugin marketplace add '{MARKETPLACE_URL}#{pin}'",
            f"claude plugin install {PLUGIN}@{MARKETPLACE}"]


# The newest release cut before `warden skills pin` landed (#331): a consumer
# pinned there installs a warden that has no such command. Every later release
# is cut from main after #331 and carries it.
LACKS_PIN_COMMAND = "v2.2.0"


def _release_key(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tag.removeprefix("v").split("."))


def carries_pin_command(pin: str) -> bool:
    """Whether the warden release PIN names has `warden skills pin`."""
    return _release_key(pin) > _release_key(LACKS_PIN_COMMAND)


def check_step(pin: str) -> str:
    """How a consumer pinned at PIN checks the pack it installed — the
    sentence Installation.md and `warden init` both print. A release without
    the command is told the manual check the command automates, never to
    run a command its warden lacks."""
    pin = "v" + pin.removeprefix("v")
    if carries_pin_command(pin):
        return f"Then check it with warden skills pin, which {pin} carries."
    return (f"warden skills pin ships from the first release after "
            f"{LACKS_PIN_COMMAND}, so a warden at {pin} does not have it. Check "
            f"by hand instead: in plugins/known_marketplaces.json under the "
            f"Claude config dir, the {MARKETPLACE} entry's source ref must be "
            f"{pin}; in plugins/installed_plugins.json, the {PLUGIN}@{MARKETPLACE} "
            f"version must equal the one in that clone's "
            f"skills/{PLUGIN}/.claude-plugin/plugin.json.")


def _clone_sha(location: Path) -> str | None:
    from .runs import git_env  # a hook-exported GIT_DIR must not retarget the read
    try:
        run = subprocess.run(["git", "-C", str(location), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=30, env=git_env())
    except (OSError, subprocess.SubprocessError):
        return None
    return run.stdout.strip() if run.returncode == 0 else None


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    """(document, None) or (None, why) — absent is `None, None`."""
    if not path.is_file():
        return None, None
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return None, f"{path} is unreadable: {exc}"
    if not isinstance(doc, dict):
        return None, f"{path} is not a JSON object"
    return doc, None


def _clone_version(location: Path) -> tuple[str | None, str | None]:
    """The pack version the marketplace clone at LOCATION serves: its own
    marketplace.json names the plugin's source directory, whose plugin.json
    carries the version."""
    manifest, why = _load_json(location / ".claude-plugin" / "marketplace.json")
    if manifest is None:
        return None, why or f"{location} holds no marketplace manifest"
    sources = {p.get("name"): p.get("source") for p in manifest.get("plugins") or []
               if isinstance(p, dict)}
    source = sources.get(PLUGIN)
    if not isinstance(source, str):
        return None, f"{location}'s manifest does not serve {PLUGIN} from a path"
    plugin, why = _load_json(location / source / ".claude-plugin" / "plugin.json")
    if plugin is None:
        return None, why or f"{location}/{source} holds no plugin manifest"
    version = plugin.get("version")
    if not isinstance(version, str):
        return None, f"{location}/{source}'s plugin manifest names no version"
    return version, None


def check(config_dir: Path, pin: str) -> tuple[str, str]:
    """(status, detail): is the pack CONFIG_DIR serves installed from the
    marketplace at PIN, and is it the version that clone serves?"""
    plugins = config_dir / "plugins"
    pin = "v" + pin.removeprefix("v")  # the schema admits `2.2.0`; every tag has the v
    redo = "; ".join([f"claude plugin marketplace remove {MARKETPLACE}",
                      *install_commands(pin)])
    known, why = _load_json(plugins / "known_marketplaces.json")
    if why:
        return UNREADABLE, why
    entry = (known or {}).get(MARKETPLACE)
    if not isinstance(entry, dict):
        return UNPINNED, (f"marketplace {MARKETPLACE} is not added under {plugins} — "
                          f"the pack is not installed; repo.yaml pins {pin}: {redo}")
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    ref = source.get("ref")
    if not isinstance(ref, str) or not ref:
        kind = source.get("source", "?")
        return UNPINNED, (f"marketplace {MARKETPLACE} is added with no ref "
                          f"(source: {kind}), so the pack floats on whatever that "
                          f"source serves; repo.yaml pins {pin}: {redo}")
    if "v" + ref.removeprefix("v") != pin:
        return UNPINNED, (f"marketplace {MARKETPLACE} is added at {ref} but "
                          f"repo.yaml pins {pin} (one Claude config dir holds one tag; "
                          f"another pin needs its own CLAUDE_CONFIG_DIR): {redo}")
    location = entry.get("installLocation")
    if not isinstance(location, str):
        return UNREADABLE, f"marketplace {MARKETPLACE} records no installLocation"
    served, why = _clone_version(Path(location))
    if served is None:
        return UNREADABLE, why or "the clone's version cannot be read"
    installed, why = _load_json(plugins / "installed_plugins.json")
    if why:
        return UNREADABLE, why
    copies = ((installed or {}).get("plugins") or {}).get(f"{PLUGIN}@{MARKETPLACE}")
    copies = [c for c in (copies if isinstance(copies, list) else []) if isinstance(c, dict)]
    if not copies:
        return UNPINNED, (f"marketplace {MARKETPLACE} is pinned at {pin} but "
                          f"{PLUGIN} is not installed from it: "
                          f"{install_commands(pin)[1]}")
    for copy in copies:
        version = copy.get("version")
        if version != served:
            return UNPINNED, (f"{PLUGIN} {version} is installed but the marketplace "
                              f"clone at {pin} serves {served} — reinstall it: "
                              f"{install_commands(pin)[1]}")
        path = copy.get("installPath")
        if not isinstance(path, str) or not (Path(path) / "skills").is_dir():
            return UNPINNED, (f"{PLUGIN} is recorded at {path}, which holds no "
                              f"skills/ — reinstall it: {install_commands(pin)[1]}")
        sha, head = copy.get("gitCommitSha"), _clone_sha(Path(location))
        if head is None or sha != head:  # no recorded commit is no proof either
            return (UNREADABLE if head is None else UNPINNED), (
                f"{PLUGIN} was installed from commit {sha or '(none recorded)'} but the clone at {location} "
                f"is at {head or 'an unreadable commit'} — reinstall: {install_commands(pin)[1]}")
    packs = ", ".join(f"{c.get('installPath')}/skills" for c in copies)
    return PINNED, (f"{PLUGIN} {served} is installed from marketplace {MARKETPLACE} "
                    f"at {pin}, the tag repo.yaml pins (--pack {packs})")
