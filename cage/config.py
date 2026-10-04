"""cage.toml — declared config for one project's cage.

The toml is the source of truth; everything else in a cage directory
(run.sh, cage.env, the launchd plist, the Claude settings profile)
is a derived artifact rendered by `cage enroll`.

The schema is fail-closed: unknown sections or keys reject enrollment
rather than being silently ignored — a typoed limit must not become a
missing hard stop.

`[triggers]` declares how a run STARTS — `schedule` (launchd), `manual`,
`ci-event` — and `[quiet_hours]` declares when one must not, whoever
started it. The two were one section (`[schedule]`) that could only
describe an overnight window: it rejected any window that did not cross midnight, so a
nine-to-five cage was a config error and the schedule was the only way in.

`[toolchain]` declares what the caged PATH must carry — the directories the
runner prepends to its fixed PATH, and the binaries without which a run must
not start. The runner resets PATH so a launchd-started run behaves like a
hand-started one; anything installed outside that fixed list is invisible
inside the cage, and a suite that needs it fails at the ship gate an hour in
rather than at pre-flight.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class CageConfigError(Exception):
    """cage.toml is missing, malformed, or fails validation."""


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_GITHUB_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# Branch prefix feeds a jq startswith() and git branch names; keep it tame.
_BRANCH_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_-]*/$")

# How a run STARTS is declared, never assumed. Each trigger names a way the
# runner is invoked; the values are that trigger's own parameters (only the
# scheduler has any). Order here is the canonical report order.
_TRIGGERS: dict[str, set[str]] = {
    "schedule": {"hour", "minute"},   # renders the launchd plist
    "manual": set(),                  # run it yourself, no scheduler
    "ci-event": set(),                # kicked by an external event
}

_KNOWN = {
    "project": {"name", "github", "live_checkout", "worktree", "branch_prefix"},
    "cage": {"dir", "log"},
    "triggers": set(_TRIGGERS),
    "quiet_hours": {"enabled", "start", "end"},
    "limits": {"max_open_prs", "timeout_secs", "max_resumes"},
    "gate": {"platform_probe", "forbidden_paths", "scrub_paths",
             "autonomy_carve_out"},
    "profile": {"extra_allow", "extra_deny", "additional_directories"},
    "toolchain": {"path", "require"},
}

# A bare command name the runner can hand to `command -v`. No slash (a path is
# `[toolchain].path`'s job), no whitespace (run.sh iterates a space-separated
# list), no glob.
_TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


@dataclass(frozen=True)
class Schedule:
    """The launchd trigger's parameters — one trigger renderer's input."""
    hour: int
    minute: int


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """Is `hour` inside the interval that must stay quiet?

    The interval is read forward from start to end and may wrap midnight
    (23->5, an overnight window) or not (9->17, a working
    day). An empty interval (start == end) declares no quiet hours at all.
    run.sh implements exactly this in bash; the two must agree.
    """
    if start < end:
        return start <= hour < end
    if start > end:
        return hour >= start or hour < end
    return False


@dataclass
class CageConfig:
    name: str
    github: str
    live_checkout: Path
    worktree: Path
    cage_dir: Path
    log: Path
    source: Path  # the cage.toml this config was loaded from
    branch_prefix: str = "auto/"
    triggers: tuple[str, ...] = ()
    schedule: Schedule | None = None
    # The human-collision guard, stated as the hours that must stay QUIET.
    # Default 05:00-23:00 — the complement of a 23-5 overnight window, the
    # shape this guard was first written for.
    quiet_start: int = 5
    quiet_end: int = 23
    max_open_prs: int = 2
    timeout_secs: int = 7200
    max_resumes: int = 2   # a run interrupted mid-bead may be
    #                        continued this many times before the
    #                        bead goes back to the founder
    platform_probe: str = ""
    # The autonomy-ladder carve-out: may the runner consult
    # `warden autonomy carve-out` and, if it answers yes, leave a
    # forbidden-path PR OPEN for a human to merge instead of closing it?
    # DEFAULT FALSE, and that default is the point — it weakens a hard stop
    # this project declared, so it is a per-consumer grant, never platform
    # behaviour a `cage enroll` hands out on the platform's own authority.
    autonomy_carve_out: bool = False
    # The SCOPE of that grant. A bare `true` leaves this
    # empty, which means "whatever the pinned platform admits" — the
    # unscoped reading the flag shipped with, kept so an existing consumer
    # config keeps loading. A list of store prefixes narrows the grant to
    # paths the consumer named: the runner clears a forbidden path only when
    # the platform's carve-out AND this list both admit it, so a platform
    # that grows a fifth store cannot widen a grant already given.
    carve_out_stores: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    scrub_paths: list[str] = field(default_factory=list)
    extra_allow: list[str] = field(default_factory=list)
    extra_deny: list[str] = field(default_factory=list)
    additional_directories: list[str] = field(default_factory=list)
    # Directories PREPENDED to the runner's fixed PATH, and the binaries a
    # run may not start without. The runner resets PATH to a
    # launchd-proof fixed list; a toolchain installed anywhere else — Go's
    # /usr/local/go/bin is the case in point — is simply absent
    # inside the cage, and the suite that needs it fails at ship an hour in.
    toolchain_path: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    services: list[dict] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"com.{self.name}.cage"

    @property
    def quiet_enabled(self) -> bool:
        return self.quiet_start != self.quiet_end

    @property
    def forbidden_regex(self) -> str:
        """One anchored alternation for the runner's post-check grep."""
        if not self.forbidden_paths:
            return ""
        return "^(" + "|".join(self.forbidden_paths) + ")"


def _expand(raw: str, table: str, key: str) -> Path:
    # Absolute only: launchd runs with cwd '/', so a relative path here would
    # render a plist that never fires and deny rules that seal nothing.
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise CageConfigError(f"[{table}].{key} must be an absolute path, got {raw!r}")
    return path


def _validate_ere(pattern: str) -> None:
    # The runner matches with grep -E, not Python re — a Python-only construct
    # (lookaround, named group) must not silently disable the whole
    # forbidden-path post-check at 3am. '(?' is never POSIX ERE; reject it
    # statically because greps DISAGREE about it (BSD errors, GNU accepts with
    # different semantics), then probe the machine's actual grep for the rest.
    if "(?" in pattern:
        raise CageConfigError(
            f"[gate].forbidden_paths entry {pattern!r} is not a valid POSIX ERE "
            "('(?...' groups are Python-only; the runner matches with grep -E)")
    proc = subprocess.run(["grep", "-E", pattern], input=b"", capture_output=True)
    if proc.returncode >= 2:
        raise CageConfigError(
            f"[gate].forbidden_paths entry {pattern!r} is not a valid POSIX ERE "
            f"(grep -E rejected it: {proc.stderr.decode().strip()})")


def _require_str(section: dict, table: str, key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value:
        raise CageConfigError(f"[{table}].{key} is required and must be a non-empty string")
    return value


def _opt_int(section: dict, table: str, key: str, default: int, lo: int, hi: int) -> int:
    value = section.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise CageConfigError(f"[{table}].{key} must be an integer in [{lo}, {hi}]")
    return value


def _opt_str_list(section: dict, table: str, key: str) -> list[str]:
    value = section.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise CageConfigError(f"[{table}].{key} must be a list of non-empty strings")
    return value


def load(toml_path: Path | str) -> CageConfig:
    from . import providers  # deferred: providers imports CageConfigError from here
    toml_path = Path(toml_path)
    if not toml_path.is_file():
        raise CageConfigError(f"config not found: {toml_path}")
    try:
        data = tomllib.loads(toml_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise CageConfigError(f"invalid TOML in {toml_path}: {exc}") from exc

    unknown = []
    for table, keys in data.items():
        if table == "service":
            continue  # [[service]] array — validated by the provider registry
        if table not in _KNOWN:
            unknown.append(f"[{table}]")
        elif isinstance(keys, dict):
            unknown.extend(f"[{table}].{k}" for k in keys if k not in _KNOWN[table])
            if table == "triggers":
                # one level deeper: a trigger's own parameters fail closed too
                for name, body in keys.items():
                    if name not in _TRIGGERS:
                        continue
                    if not isinstance(body, dict):
                        raise CageConfigError(f"[triggers.{name}] must be a table")
                    unknown.extend(f"[triggers.{name}].{k}" for k in body
                                   if k not in _TRIGGERS[name])
        else:
            raise CageConfigError(f"top-level key '{table}' must be a table")
    if unknown:
        raise CageConfigError("unknown config keys (fail-closed): " + ", ".join(sorted(unknown)))

    project = data.get("project", {})
    cage = data.get("cage", {})
    triggers = data.get("triggers", {})
    quiet = data.get("quiet_hours", {})
    limits = data.get("limits", {})
    gate = data.get("gate", {})
    profile = data.get("profile", {})
    toolchain = data.get("toolchain", {})

    name = _require_str(project, "project", "name")
    if not _NAME_RE.match(name):
        raise CageConfigError(f"[project].name '{name}' must match {_NAME_RE.pattern}")
    github = _require_str(project, "project", "github")
    if not _GITHUB_RE.match(github):
        raise CageConfigError(f"[project].github '{github}' must be owner/repo")
    live_checkout = _expand(_require_str(project, "project", "live_checkout"),
                            "project", "live_checkout")
    worktree = _expand(_require_str(project, "project", "worktree"), "project", "worktree")
    # Collisions are compared RESOLVED: '/work/proj/../proj' or a symlink must
    # not slip past this and let the runner 'checkout -B && clean -fdq' the
    # live checkout.
    if worktree.resolve() == live_checkout.resolve():
        raise CageConfigError("[project].worktree must differ from live_checkout — "
                              "the cage never touches the live checkout")

    branch_prefix = project.get("branch_prefix", "auto/")
    if not isinstance(branch_prefix, str) or not _BRANCH_PREFIX_RE.match(branch_prefix):
        raise CageConfigError(f"[project].branch_prefix '{branch_prefix}' must match "
                              f"{_BRANCH_PREFIX_RE.pattern} (trailing slash required)")

    # The cage dir defaults to wherever the toml lives — config sits beside
    # the artifacts it generates.
    cage_dir = (_expand(cage["dir"], "cage", "dir") if "dir" in cage
                else toml_path.resolve().parent)
    if cage_dir.resolve() in (worktree.resolve(), live_checkout.resolve()):
        raise CageConfigError("[cage].dir must not be the worktree or live checkout")
    log_path = (_expand(cage["log"], "cage", "log") if "log" in cage
                else Path.home() / "Library/Logs" / f"{name}-cage.log")

    # Triggers: at least one, declared. A cage with none has no stated way to
    # start, and defaulting one in is how "runs belong to a fixed hour"
    # became architecture in the first place.
    declared = tuple(name for name in _TRIGGERS if name in triggers)
    if not declared:
        raise CageConfigError(
            "[triggers] must declare at least one trigger — "
            + ", ".join(f"[triggers.{name}]" for name in _TRIGGERS)
            + " (how a run starts is a declared decision, never a default)")

    # Quiet hours: "do not run while a human may be working". The one-shot
    # run-now override files bypass THIS, which is why the guard belongs to
    # the cage rather than to any one trigger. Turning it off must be a
    # visible decision, never an omission (fail-closed).
    quiet_on = quiet.get("enabled", True)
    if not isinstance(quiet_on, bool):
        raise CageConfigError("[quiet_hours].enabled must be true or false")
    if quiet_on:
        quiet_start = _opt_int(quiet, "quiet_hours", "start", 5, 0, 23)
        quiet_end = _opt_int(quiet, "quiet_hours", "end", 23, 0, 23)
        if quiet_start == quiet_end:
            raise CageConfigError(
                "[quiet_hours].start and .end must differ — an empty interval "
                "is not a declaration of quiet hours; set enabled = false to "
                "run at any hour")
    else:
        if "start" in quiet or "end" in quiet:
            raise CageConfigError(
                "[quiet_hours] declares hours alongside enabled = false — one "
                "of the two is wrong; drop the hours, or drop 'enabled = false'")
        quiet_start = quiet_end = 0

    schedule = None
    if "schedule" in declared:
        sched = triggers["schedule"]
        hour = _opt_int(sched, "triggers.schedule", "hour", 23, 0, 23)
        minute = _opt_int(sched, "triggers.schedule", "minute", 30, 0, 59)
        if quiet_start != quiet_end and in_quiet_hours(hour, quiet_start, quiet_end):
            raise CageConfigError(
                f"[triggers.schedule].hour {hour} falls inside the "
                f"{quiet_start}:00-{quiet_end}:00 quiet hours — launchd would "
                "fire and the runner would always skip")
        schedule = Schedule(hour=hour, minute=minute)

    max_open_prs = _opt_int(limits, "limits", "max_open_prs", 2, 1, 50)
    timeout_secs = _opt_int(limits, "limits", "timeout_secs", 7200, 60, 86400)
    max_resumes = _opt_int(limits, "limits", "max_resumes", 2, 0, 10)

    platform_probe = gate.get("platform_probe", "")
    if not isinstance(platform_probe, str):
        raise CageConfigError("[gate].platform_probe must be a string (empty disables)")
    if "'" in platform_probe or "\n" in platform_probe:
        raise CageConfigError("[gate].platform_probe must not contain quotes or newlines")

    # The post-check is a hard stop; disabling it must be a visible decision,
    # never an omission (fail-closed).
    if "forbidden_paths" not in gate:
        raise CageConfigError(
            "[gate].forbidden_paths must be declared — list the gate-surface path "
            "regexes, or declare an explicit [] to run with the post-check DISABLED")
    forbidden_paths = _opt_str_list(gate, "gate", "forbidden_paths")
    for pattern in forbidden_paths:
        _validate_ere(pattern)

    # Opting IN to the autonomy-ladder carve-out is a grant, so it is declared
    # or it is off. A non-boolean rejects rather than being
    # read for truthiness: "no" and "" are both true in Python, and a typo must
    # not quietly weaken the forbidden-path stop.
    # A LIST is the scoped form of the same grant: it names
    # the stores under which the machine tier may write, and the runner
    # clears nothing outside them however much the pinned platform's own
    # carve-out admits. `true` keeps the unscoped reading it shipped with —
    # whatever the pin admits — so every config written against the bool
    # still loads, and `false`/absent is still no grant at all.
    carve_out_raw = gate.get("autonomy_carve_out", False)
    carve_out_stores: list[str] = []
    if isinstance(carve_out_raw, bool):
        carve_out = carve_out_raw
    elif isinstance(carve_out_raw, list):
        if not carve_out_raw:
            raise CageConfigError(
                "[gate].autonomy_carve_out = [] admits no store, which is a "
                "grant that can never clear anything — write `false` to "
                "decline the carve-out outright")
        carve_out = True
        carve_out_stores = _opt_str_list(gate, "gate", "autonomy_carve_out")
        for store in carve_out_stores:
            # `\` is rejected with the glob characters, not beside them: the
            # runner matches with a bash `case` pattern, where a backslash
            # ESCAPES the next character — so `.warden/rul\es/` would silently
            # admit `.warden/rules/` and nothing would say so.
            if (store.startswith("/") or store.startswith("./")
                    or ".." in store
                    or not store.endswith("/")
                    or any(c.isspace() for c in store)
                    or any(c in store for c in "*?[\\")):
                raise CageConfigError(
                    f"[gate].autonomy_carve_out entry {store!r} must be a "
                    "literal relative directory prefix ending in '/' — no "
                    "whitespace, '..' or glob characters (the runner matches "
                    "each forbidden path against it literally)")
    else:
        raise CageConfigError(
            "[gate].autonomy_carve_out must be true, false, or a list of "
            "store prefixes — it grants the "
            "runner permission to leave a forbidden-path PR open when "
            "`warden autonomy carve-out` says the diff is a machine-tier "
            "pause or non-blocking adoption, so it is declared, never inferred")

    scrub_paths = _opt_str_list(gate, "gate", "scrub_paths")
    for scrub in scrub_paths:
        if (scrub.startswith("/") or ".." in scrub
                or any(c.isspace() for c in scrub)
                or any(c in scrub for c in "*?[")):
            raise CageConfigError(
                f"[gate].scrub_paths entry {scrub!r} must be a literal relative path — "
                "no whitespace, '..', or glob characters (the runner matches literally)")

    # [toolchain] — what the caged PATH must carry. Shape is
    # checked here; PRESENCE is checked by the runner on the machine that
    # will actually run, because that is where the answer can be true. An
    # enroll-time existence check would either pass vacuously in CI or refuse
    # a config that is correct for the cage host.
    toolchain_path = _opt_str_list(toolchain, "toolchain", "path")
    expanded_path: list[str] = []
    for entry in toolchain_path:
        if ":" in entry or any(c.isspace() for c in entry):
            raise CageConfigError(
                f"[toolchain].path entry {entry!r} must hold no ':' or "
                "whitespace — the runner joins these into PATH")
        expanded_path.append(str(_expand(entry, "toolchain", "path")))
    required_tools = _opt_str_list(toolchain, "toolchain", "require")
    for tool in required_tools:
        if not _TOOL_RE.match(tool):
            raise CageConfigError(
                f"[toolchain].require entry {tool!r} must be a bare command "
                f"name matching {_TOOL_RE.pattern} — the runner resolves each "
                "with `command -v`, so a path or a flag names nothing")

    return CageConfig(
        name=name,
        github=github,
        live_checkout=live_checkout,
        worktree=worktree,
        cage_dir=cage_dir,
        log=log_path,
        source=toml_path.resolve(),
        branch_prefix=branch_prefix,
        triggers=declared,
        schedule=schedule,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
        max_open_prs=max_open_prs,
        timeout_secs=timeout_secs,
        max_resumes=max_resumes,
        platform_probe=platform_probe,
        autonomy_carve_out=carve_out,
        carve_out_stores=carve_out_stores,
        forbidden_paths=forbidden_paths,
        scrub_paths=scrub_paths,
        toolchain_path=expanded_path,
        required_tools=required_tools,
        extra_allow=_opt_str_list(profile, "profile", "extra_allow"),
        extra_deny=_opt_str_list(profile, "profile", "extra_deny"),
        additional_directories=_opt_str_list(profile, "profile", "additional_directories"),
        services=providers.validate_services(data.get("service", [])),
    )
