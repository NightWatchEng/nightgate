"""Project checker plugins: per-repo engine:python rules.

The platform ships a generic checker pack (mechanical.CHECKERS). A consuming
repo adds its own deterministic rules by dropping modules into
`.warden/checkers/*.py`, each exporting a `CHECKERS: dict[str, Checker]`
with the same signature as the core pack:

    def check_my_rule(ctx: DiffContext, params: dict, files: list[str]) -> list[dict]: ...
    CHECKERS = {"my-rule": check_my_rule}

Trust model: this executes code from the consuming repository — the same trust
level as running that repository's tests. This applies to EVERY command that
loads the registry, including `warden explain` (enrollment-time validation):
do not run warden inside a clone you would not run tests in. The code is versioned, reviewed, and gated like any other repo file,
and rules_version hashes it (a checker edit is a policy edit).

Fail-closed rules:
- A project checker may NOT shadow a core rule id (refuse loudly; a project
  wanting different behavior names a new rule).
- Two project modules exporting the same id refuse loudly.
- A module that fails to import refuses loudly (a silently skipped checker is
  the worst failure mode a gate can have).
- A caller that must execute nothing from the checkout (`warden review
  --no-project-checkers`, the gate job `warden init` writes) passes
  project=False: any module present is refused, never imported and never
  skipped.
"""

import importlib.util
from collections.abc import Callable
from pathlib import Path

from .config import ConfigError
from .mechanical import CHECKERS as CORE_CHECKERS

CHECKERS_DIRNAME = "checkers"


def checkers_dir(rules_dir: Path) -> Path:
    """`.warden/checkers/`, sibling of the rules dir."""
    return rules_dir.parent / CHECKERS_DIRNAME


def load_registry(rules_dir: Path, *, project: bool = True) -> dict[str, Callable]:
    """Core pack + project plugins, merged fail-closed."""
    registry = dict(CORE_CHECKERS)
    plug_dir = checkers_dir(rules_dir)
    if not plug_dir.is_dir():
        return registry
    modules = sorted(plug_dir.glob("*.py"))
    if modules and not project:
        raise ConfigError(
            f"{plug_dir} holds project checker modules "
            f"({', '.join(m.name for m in modules)}), and this run executes no "
            "code from the checkout (--no-project-checkers)")
    for mod_path in modules:
        try:
            spec = importlib.util.spec_from_file_location(
                f"warden_project_checkers_{mod_path.stem}", mod_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:  # noqa: BLE001 — any import failure is fail-closed
            raise ConfigError(f"project checker module {mod_path} failed to "
                              f"load: {e}") from e
        exported = getattr(module, "CHECKERS", None)
        if not isinstance(exported, dict) or not exported:
            raise ConfigError(f"{mod_path}: must export a non-empty CHECKERS dict")
        for rule_id, fn in exported.items():
            if rule_id in CORE_CHECKERS:
                raise ConfigError(
                    f"{mod_path}: checker {rule_id!r} shadows a core rule — "
                    "name a new rule instead")
            if rule_id in registry:
                raise ConfigError(
                    f"{mod_path}: duplicate project checker id {rule_id!r}")
            if not callable(fn):
                raise ConfigError(f"{mod_path}: CHECKERS[{rule_id!r}] is not callable")
            registry[rule_id] = fn
    return registry
