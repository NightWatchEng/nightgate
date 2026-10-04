"""warden plan — the task packet a session starts from.

Orientation before work, as an artifact instead of a habit. `explain` answers
"what is this repo"; `plan` answers "what does THIS piece of work touch, what
must pass, and what has already gone wrong here".

That last part is what makes it ours: the packet carries **memory priors**
for the areas it names, so a session starts with the organization's scar
tissue rather than rediscovering it. Priors are the reviewer split
(confirmed patterns), never the examiner's precedents — planning is finding
work, not judging findings.

File hints are optional per-area scenario maps in `.warden/file-hints/*.yaml`:

    area: payments
    risk: HIGH
    scenarios:
      - name: add a payment method
        source_files: ["src/payments/", "src/store/payment/"]
        test_files: ["tests/payments/**"]

Absent hints are simply absent — the packet still carries risk tiers, verify
commands, and priors.

`--path` names the files the work touches when no hints do. Each is
normalised to the repo-relative spelling `git diff` gives `explain`, then
tiered by `config.classify`, the one function `warden explain --base` tiers a
diff with, so the packet and the brief tier one file the same way. Two
kinds of path `explain` tiers are refused here, each naming why: one outside
the enrollment (the git root's gate workflow of an enrollment below it,
`../../.github/...`), and a tracked entry that is a directory on disk (a
symlink to one, or a submodule).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from . import config as config_mod
from . import memory as memory_mod
from . import yamlio

HINTS_SUBDIR = "file-hints"


class PlanError(Exception):
    pass


def load_hints(root: Path, area: str) -> dict:
    """Scenario hints for an area, or {} when none are declared."""
    if not area:
        return {}
    path = root / ".warden" / HINTS_SUBDIR / f"{area.lower()}.yaml"
    if not path.is_file():
        return {}
    try:
        doc = yamlio.load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise PlanError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(doc, dict):
        raise PlanError(f"{path}: expected a mapping")
    return doc


def available_areas(root: Path) -> list[str]:
    hints = root / ".warden" / HINTS_SUBDIR
    return sorted(p.stem for p in hints.glob("*.yaml")) if hints.is_dir() else []


def _inside(target: str, root: str) -> str | None:
    rel = os.path.relpath(target, root)
    if rel in (".", "..") or rel.startswith(".." + os.sep) or os.path.isabs(rel):
        return None
    return rel


def repo_relative(root: Path, raw: str, cwd: Path | None = None) -> str:
    """`raw` as the repo-relative, slash-separated spelling the risk_tiers
    globs are written against — the spelling `git diff` hands `explain`.

    Read the way git reads a path argument: relative to `cwd` (default the
    root) unless absolute, then normalised, so `./repo.yaml`, an absolute
    path (a symlinked prefix such as macOS `/tmp` included) and `cli.py`
    typed inside `warden/` reach the glob that tiers them rather than
    falling through to the unmatched LOW; a folder reached through a symlink
    is read as the folder it resolves to. Refused, each naming why: a path
    outside the root (through a symlinked folder pointing out of it too), a
    directory (globs tier files), and a case variant of a file on a
    case-insensitive disk (git would not read it as that file,
    and the case-sensitive globs would tier it LOW)."""
    base = os.path.realpath(cwd if cwd is not None else root)
    target = os.path.normpath(os.path.join(base, raw))
    # Resolve every link in the directory part: git refuses a path beyond a
    # symbolic link, so the spelling a diff carries is the real folder's, and
    # a folder linked out of the repo is outside it. The last
    # component stays as typed: a tracked link is tiered by its own name.
    rel = _inside(os.path.join(os.path.realpath(os.path.dirname(target)),
                               os.path.basename(target)),
                  os.path.realpath(root))
    if rel is None:
        raise PlanError(f"--path {raw!r} is not a file inside {root} — "
                        "plan --path tiers only files inside the enrollment")
    here = Path(os.path.realpath(root))
    for part in rel.split(os.sep):
        if not here.is_dir() or not (here / part).exists():
            break  # not on disk yet: build's caveat names it
        if part not in os.listdir(here):
            raise PlanError(f"--path {raw!r}: {part!r} is not spelled as it is "
                            f"on disk under {here} — name the file as git does")
        here = here / part
    else:
        if here.is_dir():
            raise PlanError(f"--path {raw!r} is a directory — risk_tiers tier "
                            "files, so name the files in it you will touch")
    return rel.replace(os.sep, "/")


def build(config, *, task: str, area: str = "",
          given_paths: tuple[str, ...] | list[str] = (),
          cwd: Path | None = None) -> dict:
    """Assemble the task packet. `given_paths` are the files the caller says
    the work touches, read relative to `cwd` (default the repo root); they are
    tiered and recalled exactly as hinted ones."""
    root = config.root
    hints = load_hints(root, area)

    given = [repo_relative(root, p, cwd) for p in given_paths
             if isinstance(p, str) and p]
    # A named path that is not on disk is still tiered — a plan names files
    # the work will create — but a typo would be tiered the same way, so
    # the packet says which ones it could not find.
    absent = [p for p in given if not (Path(root) / p).exists()]
    paths: list[str] = list(given)
    for scenario in hints.get("scenarios", []) or []:
        for key in ("source_files", "test_files", "config_files"):
            paths += [p for p in (scenario.get(key) or []) if isinstance(p, str)]
    paths = sorted(set(paths))

    # Risk: the highest tier any hinted or given path classifies into, plus
    # the declared area risk if the hints state one. No path from either
    # source -> UNKNOWN, said plainly rather than guessed.
    order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    tiers = [config_mod.classify(config, p.rstrip("/") or p).tier for p in paths]
    declared = hints.get("risk")
    if isinstance(declared, str) and declared in order:
        tiers.append(declared)
    risk = max(tiers, key=lambda t: order.get(t, 0)) if tiers else "UNKNOWN"

    verify = {scope: [step.run for step in steps]
              for scope, steps in (config.verify or {}).items()}

    # Priors are ADVISORY, so a corpus that cannot be read degrades the packet
    # rather than refusing to plan — but it degrades LOUDLY, into the caveat
    # list, because "no priors on record" and "the priors could not be read"
    # send a builder to opposite places and an empty section says the first.
    # The reader raises AttestError naming the file and the
    # field; this keeps that sentence verbatim.
    prior_problem = ""
    priors: list[dict] = []
    if paths:
        try:
            priors = memory_mod.recall(root, files=paths, role="reviewer")
        except memory_mod.AttestError as e:
            prior_problem = str(e)

    return {
        "schema": "task-packet/v1",
        "repo": config.repo,
        "task": task,
        "area": area,
        "risk_tier": risk,
        "paths": paths,
        "scenarios": [s.get("name", "") for s in (hints.get("scenarios") or [])],
        "verify": verify,
        # `.get` for the two fields `RECORD_FIELD_CONTRACT` declares optional:
        # subscripting them would make `warden plan --area <hinted>` die with a
        # bare `KeyError: 'finding'` on a shard the validator deliberately
        # accepts. `rule_id` and `status` stay
        # subscripted: the contract refuses a record without them, so a default
        # here would be a fallback that cannot fire.
        # `id` is the pointer from the markdown's excerpt back to this
        # full text and to the cache row; additive on
        # task-packet/v1.
        "priors": [{"rule_id": p["rule_id"],
                    "dir_prefix": p.get("dir_prefix", ""),
                    "finding": p.get("finding", ""),
                    "status": p["status"],
                    "id": p.get("id", "")}
                   for p in priors],
        "caveats": ([] if paths else
                    ["no file hints for this area and no --path given — "
                     "paths, risk, and priors are unknown, not empty; "
                     "`warden plan --path <file>` tiers each file by "
                     "repo.yaml's risk_tiers, as `warden explain` does"])
                   + ([f"--path not in the working tree, tiered by name "
                       f"only (a file this work creates, or a typo): "
                       f"{', '.join(absent)}"] if absent else [])
                   + ([f"review memory could not be read, so the priors "
                       f"below are EMPTY rather than absent — {prior_problem}"]
                      if prior_problem else [])
                   # A prior whose rendered line did not fit recall's budget
                   # is not absent, and a packet that said nothing about it
                   # would read as if it were.
                   + ([f"{getattr(priors, 'skipped', 0)} relevant prior(s) "
                       "skipped: rendered line over recall's budget — the "
                       "corpus holds them; grep .warden/memory/findings.jsonl"]
                      if getattr(priors, "skipped", 0) else []),
    }


def render(packet: dict) -> str:
    lines = [f"# task packet — {packet['repo']}", "",
             f"**Task**: {packet['task']}",
             f"**Area**: {packet['area'] or '(none given)'}",
             f"**Risk tier**: {packet['risk_tier']}", ""]
    if packet["scenarios"]:
        lines += ["## Known scenarios"] + \
                 [f"- {s}" for s in packet["scenarios"] if s] + [""]
    if packet["paths"]:
        lines += ["## Likely paths"] + [f"- `{p}`" for p in packet["paths"]] + [""]
    if packet["verify"]:
        lines.append("## Must pass")
        for scope, cmds in packet["verify"].items():
            lines += [f"- **{scope}**"] + [f"    - `{c}`" for c in cmds if c]
        lines.append("")
    lines.append("## Priors from review memory")
    if packet["priors"]:
        lines.append("Verify these, do not assume them — they are where this "
                     "area has gone wrong before, not findings about your work.")
        # The markdown is the READING: each prior is the bounded excerpt
        # `memory recall` prints, tagged with its record
        # id. The JSON packet beside it keeps the full finding — that is
        # the evidence — and the section says so, because this markdown is
        # what `warden plan` prints and what a builder reads.
        lines += [f"- [{p['rule_id']} @ {p['dir_prefix']}] "
                  f"{memory_mod.excerpt(p['finding'])} ({p['status']})"
                  + (f" id={p['id']}" if p.get("id") else "")
                  for p in packet["priors"]]
        lines.append("Full text of each prior: task-packet.json beside this "
                     "packet, or grep its id in .warden/memory/findings.jsonl.")
    else:
        lines.append("None on record for these paths.")
    if packet["caveats"]:
        lines += ["", "## Caveats"] + [f"- {c}" for c in packet["caveats"]]
    return "\n".join(lines) + "\n"


def write_packet(run_dir: Path, packet: dict) -> None:
    (run_dir / "task-packet.json").write_text(json.dumps(packet, indent=2) + "\n")
    (run_dir / "task-packet.md").write_text(render(packet))
