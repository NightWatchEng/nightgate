"""cage CLI — validate/enroll a project's cage.

Exit codes match warden's convention: 0 = clean, 2 = infra/config error.
Enroll renders artifacts only; loading the launchd job stays a deliberate
human step (printed as instructions, never executed here).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_mod
from . import measure as measure_mod
from . import render as render_mod


def _warn_disabled_postcheck(cfg) -> None:
    if not cfg.forbidden_regex:
        print("WARNING: [gate].forbidden_paths is empty — the forbidden-path "
              "post-check (the automated gate-surface freeze) is DISABLED",
              file=sys.stderr)


def _warn_unscoped_carve_out(cfg) -> None:
    """The migration signal for a grant written as a bare `true`.

    The defect this guards is a grant changing meaning with nobody told, and
    a fix that ships a migration path with no migration signal repeats the
    defect quietly: `true` still admits whatever the pinned platform admits,
    and `cage enroll` is the moment a consumer re-pins. So enroll says it —
    once, here, rather than only in a wiki page a reader has to go find.
    """
    if cfg.autonomy_carve_out and not cfg.carve_out_stores:
        print("NOTICE: [gate].autonomy_carve_out = true is UNSCOPED — it "
              "admits whatever the pinned platform's carve-out admits, and "
              "that set has widened once already (the attestation store was "
              "added as a fourth .warden/ store to a carve-out that had three). Write it "
              "as a list of store prefixes to pin the scope, e.g. "
              "['.warden/rules/', '.warden/memory/backtests/', "
              "'.warden/memory/attest/'] — see docs/wiki/Configuration.md",
              file=sys.stderr)


def _triggers(cfg) -> str:
    """The declared triggers, the scheduler's fire time spelled out."""
    return ", ".join(
        f"schedule {cfg.schedule.hour:02d}:{cfg.schedule.minute:02d}"
        if name == "schedule" else name
        for name in cfg.triggers)


def _quiet(cfg) -> str:
    if not cfg.quiet_enabled:
        return "quiet hours: none (declared off)"
    return f"quiet hours {cfg.quiet_start:02d}:00-{cfg.quiet_end:02d}:00"


def _cmd_validate(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    _warn_disabled_postcheck(cfg)
    _warn_unscoped_carve_out(cfg)
    print(f"ok: {cfg.name} ({cfg.github}) — cage {cfg.cage_dir}, "
          f"triggers: {_triggers(cfg)}, {_quiet(cfg)}")
    return 0


def _cmd_enroll(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    _warn_disabled_postcheck(cfg)
    _warn_unscoped_carve_out(cfg)
    had_services = (cfg.cage_dir / "services.sh").is_file()
    plist = cfg.cage_dir / f"{cfg.label}.plist"
    had_plist = plist.is_file()
    written = render_mod.enroll(cfg)
    for path in written:
        print(f"wrote {path}")
    if had_services and not cfg.services:
        print(f"removed {cfg.cage_dir / 'services.sh'} (no [[service]] declared)")
    if had_plist and cfg.schedule is None:
        print(f"removed {plist} (no [triggers.schedule] declared)")
    prompt = cfg.cage_dir / "prompt.md"
    if not prompt.is_file():
        print(f"WARNING: {prompt} missing — the session prompt is hand-authored; "
              "the runner will fail without it", file=sys.stderr)
    print(f"\nCage rendered for '{cfg.name}'. Triggers: {_triggers(cfg)}. "
          f"{_quiet(cfg)}.")
    # The one-shot override is named ONLY when quiet hours can actually fire:
    # run.sh reads that file inside the quiet-hours branch and nowhere else,
    # so with the guard off the touch does nothing, is never consumed, and
    # sits in $HOME ready to spend itself on the first stop a later
    # `enabled = true` declares.
    bypass = (f"  touch ~/.cage-{cfg.name}-run-now\n"
              if cfg.quiet_enabled else "")
    if cfg.schedule is not None:
        print(f"""The launchd job is NOT loaded by default:
  cp {plist} ~/Library/LaunchAgents/
  launchctl bootstrap gui/$UID ~/Library/LaunchAgents/{cfg.label}.plist
Supervised test (proves the REAL launchd environment — never bare run.sh):
{bypass}  launchctl kickstart gui/$UID/{cfg.label}
Unload:
  launchctl bootout gui/$UID/{cfg.label}""")
    else:
        print(f"""No scheduler is declared, so no launchd job is rendered. Start a run:
  bash {cfg.cage_dir / 'run.sh'} --check      # pre-flight only, mutates nothing
  bash {cfg.cage_dir / 'run.sh'}""")
        if bypass:
            print("Quiet hours still apply to a run however it started; "
                  f"bypass them once with:\n{bypass}", end="")
        print(f"""Removing the plist does NOT unload a launchd job already bootstrapped
from ~/Library/LaunchAgents — if one is still loaded, unload it yourself:
  launchctl bootout gui/$UID/{cfg.label}""")
    return 0


def _fmt(label: str, s: dict) -> str:
    if not s["runs"]:
        return f"  {label}: NO DATA — not measured, which is not the same as no effect"
    return (f"  {label}: {s['runs']} runs · "
            f"mean attempts {s['mean_attempts']} · "
            f"first-pass {s['first_pass_rate']:.0%} · "
            f"landed {s['landed_rate']:.0%}"
            + (f" · {s['skipped']} skipped" if s["skipped"] else ""))


def _cmd_measure(args: argparse.Namespace) -> int:
    raw = args.ledger.read_text().splitlines()
    bad = measure_mod.parse(raw)[1]
    print(f"ledger: {args.ledger}"
          + (f" · {measure_mod.malformed_note(bad)}" if bad else ""))
    if not args.since:
        print(_fmt("all", measure_mod.summarize(raw)))
        return 0

    before, after = measure_mod.split_at(raw, args.since)
    b, a = measure_mod.summarize(before), measure_mod.summarize(after)
    print(f"split at {args.since}:")
    print(_fmt("before", b))
    print(_fmt("after ", a))
    if not (b["runs"] and a["runs"]):
        print("  → no comparison: one arm is empty.")
        return 0
    delta = a["first_pass_rate"] - b["first_pass_rate"]
    print(f"  → first-pass delta {delta:+.0%}, "
          f"attempts {a['mean_attempts'] - b['mean_attempts']:+.2f}")
    if min(b["runs"], a["runs"]) < 10:
        print("  CAUTION: fewer than 10 runs in an arm. Task difficulty is "
              "not controlled for — read this as a direction to watch, not a "
              "result to act on.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cage",
        description="Nightgate cage: render a project's unattended runner from cage.toml")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_validate = sub.add_parser("validate", help="validate a cage.toml")
    p_validate.add_argument("config", type=Path)
    p_validate.set_defaults(func=_cmd_validate)

    p_enroll = sub.add_parser(
        "enroll", help="render run.sh + env + plist + profile into the cage dir")
    p_enroll.add_argument("config", type=Path)
    p_enroll.set_defaults(func=_cmd_enroll)

    p_measure = sub.add_parser(
        "measure",
        help="loop counts and first-pass rate from ledger.csv, optionally split at a date")
    p_measure.add_argument("ledger", type=Path)
    p_measure.add_argument("--since", metavar="YYYY-MM-DD",
                           help="split into before/after arms at this date")
    p_measure.set_defaults(func=_cmd_measure)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except config_mod.CageConfigError as exc:
        print(f"cage: config error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"cage: filesystem error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
