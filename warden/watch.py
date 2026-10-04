"""`warden memory watch` — the memory loop's doorbell.

The loop turns review findings into rule and skill changes, but its trigger was
a person remembering to run the retro: a candidate crossed the promotion bar
and sat there for days with nobody told. Detection is deterministic and costs
nothing, so it runs on a schedule. This module is that detection.

It compares the state the corpus shows now — read from the WORKING TREE's
shards — against the last COMMITTED watch record under
`.warden/memory/watch/`, and reports a transition for every subject that is
over a bar now and was not in the record:

  - candidate-promotion — a candidate over the promotion bar, per
    `memory.promotion_candidates`
  - rule-pause — a declared rule on a refutation streak, per `memory.stats`
  - rule-demote — a declared rule `warden rules lifecycle` calls `demote?`
  - skill-propose — a skill file over the skill-change propose bar, per
    `memory.skill_recurrence`

Every bar is read from the code that defines it; nothing here restates one.

A transition is evidence, and CI's fresh checkout needs a baseline, so the
record is committed, dated, and read from git's HEAD rather than the working
tree: a record written and not yet committed is not a baseline. With no record
committed, every state over a bar now is a transition, and the output says so.

The two sides can therefore be different trees. CI's checkout cannot diverge,
but a laptop's can — a file deleted, staged, or not yet committed — so the
note NAMES every WORKING-TREE INPUT the state is read from that differs from
HEAD — every INPUT, with a long one eliding its own paths past
`PATHS_PER_INPUT` and saying how many it held back: an unnamed input is a
reading nobody knows moved, while an unnamed path sits under an input already
named. `watched_inputs()` below builds that list: repo.yaml (which declares the
rules dir), the rule files themselves, the tag vocabulary, and the attest
shards. All four move the reading, so naming only one of them is a bug:
one uncommitted `covers:` line in a rule file took
a report from 8 transitions to 7 — the candidate the watch exists to catch
disappeared — with the note byte-identical to a clean tree's.

FAIL CLOSED. A corpus, vocabulary, ruleset or baseline that cannot be read is
UNREAD, exit 3, and never "no transitions". A corrupt record is not treated as
no record: that would report every over-bar state as new.

It edits nothing. `--record` (in the CLI) writes one new dated record and
nothing else, for a human to commit, and refuses while ANY watched input is
uncommitted: a baseline must not hold a state CI's checkout cannot see.

KNOWN LIMITS, stated rather than implied. A subject already in the baseline
is not reported again, even if it left the bar and came back, until a newer
record drops it. And `--record` takes in everything over a bar at that
moment, including anything that crossed since the issue last reported, so
read its output before committing the record.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

from . import memory as memory_mod

SCHEMA = 1
WATCH_DIR = Path(".warden") / "memory" / "watch"
EXIT_QUIET, EXIT_TRANSITIONS, EXIT_UNREAD = 0, 1, 3
CLASSES = ("candidate-promotion", "rule-pause", "rule-demote", "skill-propose")
NO_BASELINE = "no-baseline"
_RECORD_NAME = re.compile(r"watch-\d{8}T\d{6}Z\.json")

_RETRO = "nightgate-skills:retro"
_ADVISOR = "nightgate-skills:rule-advisor"
NEXT = {
    "candidate-promotion": (f"run the retro ({_RETRO}); an accepted rule "
                            f"proposal goes to {_ADVISOR}, which drafts the "
                            "rule and its backtest"),
    "rule-pause": ("read `warden autonomy pause --dry-run`, then run the retro "
                   f"({_RETRO}) to decide the pause"),
    "rule-demote": ("read the row in `warden rules lifecycle`, then run the "
                    f"retro ({_RETRO}); a demotion is human-only, through a "
                    "reviewed PR"),
    "skill-propose": f"run the retro ({_RETRO}), which proposes the skill change",
}


class WatchUnread(Exception):
    """Something the watch must read could not be read."""


def bars() -> dict[str, str]:
    from .lifecycle import MIN_EXERCISED
    m = memory_mod
    return {
        "candidate-promotion": (f"{m.PROMOTE_MIN_N}+ judged AND a floor >= "
                                f"{m.PROMOTE_WILSON_LB}, on a candidate the "
                                "retro would propose"),
        "rule-pause": f"{m.PAUSE_STREAK} refuting review rounds running",
        "rule-demote": (f"{MIN_EXERCISED}+ judged AND an argued-down floor >= "
                        f"{m.PROMOTE_WILSON_LB}"),
        "skill-propose": (f"{m.CANDIDATE_MIN_N}+ judged AND an upheld rate "
                          f"above {m.CANDIDATE_MIN_RATE} AND no refutation "
                          "streak"),
    }


def read_state(root: Path) -> tuple[dict | None, list[str]]:
    """(state, unread causes): every subject over each bar right now."""
    from . import lifecycle as lifecycle_mod
    from . import tags as tags_mod
    from .config import declared_rules_dir

    causes: list[str] = []
    rules_dir, problem = declared_rules_dir(root)
    if problem:
        causes.append(f"the rules-dir declaration could not be resolved "
                      f"({problem})")
    records = None
    try:
        records = memory_mod.records_from_shards(root)
    except ValueError as e:
        causes.append(f"the committed corpus could not be read: {e}")
    folds = tags_mod.fold_problems(root)
    if folds:
        causes.append("the declared vocabulary cannot be applied to this "
                      "corpus, so it cannot be folded as declared: "
                      + "; ".join(folds))
    if causes:
        return None, causes
    doc = memory_mod.stats(root, rules_dir, records=records)
    if doc["rules_error"]:
        return None, [f"the ruleset could not be read: {doc['rules_error']}"]
    # No commit window: the demote verdict rests on judged counts alone, and
    # a scheduled job must not depend on how much history the checkout holds.
    report = lifecycle_mod.rule_lifecycle(root, records=records,
                                          rules_dir=rules_dir, window=None)
    if not report.computed:
        return None, ["`warden rules lifecycle` could not judge the ruleset: "
                      + " ".join(report.notes)]
    streaks = memory_mod.refutation_streaks(records)
    per_rule = doc["per_rule"]
    state = {
        "candidate-promotion": {
            row["slug"]: {"judged": row["n"], "upheld": row["upheld"],
                          "refuted": row["refuted"],
                          "dismissed": row["dismissed"],
                          "wilson_lb": row["wilson_lb"]}
            for row in memory_mod.promotion_candidates(doc)},
        "rule-pause": {
            rid: {"streak": streaks.get(rid, 0),
                  "judged": per_rule.get(rid, {}).get("n", 0),
                  "refuted": per_rule.get(rid, {}).get("refuted", 0)}
            for rid in doc["pause_candidates"]},
        "rule-demote": {
            r.rule_id: {"judged": r.judged, "argued_down": r.argued_down,
                        "argued_down_lb": r.argued_down_lb}
            for r in report.rows if r.verdict == "demote?"},
        "skill-propose": {
            row["file"]: {"judged": row["n"], "upheld": row["upheld"],
                          "refuted": row["refuted"],
                          "dismissed": row["dismissed"], "rate": row["rate"]}
            for row in doc["skill_recurrence"] if row["verdict"] == "propose"},
    }
    return state, []


def _git(root: Path, *args: str, subject: str = "") -> bytes:
    # `subject` names WHAT could not be read, because this runs for two
    # questions now — the committed baseline and how the watched inputs
    # differ from HEAD — and an UNREAD cause that names the wrong one sends
    # the reader to the wrong file.
    subject = subject or (f"the committed watch records under "
                          f"{WATCH_DIR.as_posix()}/")
    try:
        return subprocess.run(["git", *args], cwd=root, check=True,
                              capture_output=True, timeout=60).stdout
    except FileNotFoundError as e:
        raise WatchUnread(f"git is not available, so {subject} cannot be "
                          "read") from e
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode("utf-8", "replace").strip()
        raise WatchUnread(f"{subject} cannot be read from git "
                          f"({detail})") from e
    except subprocess.TimeoutExpired as e:
        raise WatchUnread(f"git did not answer within 60s, so {subject} "
                          "cannot be read") from e


def _record_problem(doc: object) -> str:
    if not isinstance(doc, dict):
        return "not a JSON object"
    if doc.get("schema") != SCHEMA:
        return f"schema {doc.get('schema')!r}, expected {SCHEMA}"
    if not isinstance(doc.get("recorded_at"), str):
        return "no recorded_at"
    state = doc.get("state")
    if not isinstance(state, dict) or set(state) != set(CLASSES):
        return f"its state does not hold exactly {', '.join(CLASSES)}"
    for cls in CLASSES:
        if not (isinstance(state[cls], dict)
                and all(isinstance(k, str) and isinstance(v, dict)
                        for k, v in state[cls].items())):
            return f"its {cls} entry is not a map of subjects"
    return ""


def committed_baseline(root: Path) -> tuple[str | None, dict | None, set[str]]:
    """(path, record, committed names): the newest record committed at HEAD.

    Raises WatchUnread when git cannot answer, when a committed file in the
    watch dir is not a record the watch can place in time, or when the newest
    record is corrupt.
    """
    where = WATCH_DIR.as_posix()
    listed = _git(root, "ls-tree", "-z", "--name-only", "HEAD", "--",
                  f"{where}/")
    names = {Path(entry.decode("utf-8", "replace")).name
             for entry in listed.split(b"\0") if entry}
    strays = sorted(n for n in names if not _RECORD_NAME.fullmatch(n))
    if strays:
        raise WatchUnread(f"{where}/{strays[0]}: a committed file that is not "
                          "a watch record (watch-YYYYMMDDTHHMMSSZ.json), so "
                          "the newest baseline cannot be told apart")
    if not names:
        return None, None, names
    newest = f"{where}/{max(names)}"
    raw = _git(root, "show", f"HEAD:./{newest}")
    try:
        doc = json.loads(raw.decode("utf-8"))
        problem = _record_problem(doc)
    except ValueError as e:
        problem = f"not JSON ({e})"
    if problem:
        raise WatchUnread(f"{newest}: a corrupt watch record ({problem}) — a "
                          "baseline that cannot be read is not the same as no "
                          "baseline, so no transition is claimed either way")
    return newest, doc, names


def watched_inputs(root: Path) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """({label: repo-relative pathspec}, [(label, why it cannot be compared)]).

    Every working-tree input `read_state` reads, in the order the module
    docstring names them. A pathspec ending in `/` is a directory; the rest
    are single files. The second list is the inputs git cannot be asked
    about at all — a `rules_dir` declared OUTSIDE this repo is the one that
    happens — and it is returned rather than dropped, because an input whose
    drift cannot be measured is not an input known to be clean.
    """
    from .config import CONFIG_NAME, declared_rules_dir
    from .tags import vocab_path

    rules_dir, _ = declared_rules_dir(root)
    specs = {CONFIG_NAME: CONFIG_NAME}
    uncomparable: list[tuple[str, str]] = []
    try:
        specs["the rule files"] = rules_dir.relative_to(root).as_posix() + "/"
    except ValueError:
        uncomparable.append((
            "the rule files",
            f"{rules_dir} is outside this repo, so git cannot compare the "
            "rules the state was read from against HEAD"))
    specs["the tag vocabulary"] = vocab_path(root).relative_to(
        root).as_posix()
    specs["the attest shards"] = (
        memory_mod.memory_dir(root).relative_to(root).as_posix()
        + "/" + memory_mod.SHARD_SUBDIR + "/")
    return specs, uncomparable


def _input_label(path: str, specs: dict[str, str]) -> str:
    """The watched input `path` belongs to — longest matching spec wins, so a
    nested input is never credited to the one that contains it."""
    best, best_len = "", -1
    for label, spec in specs.items():
        hit = path.startswith(spec) if spec.endswith("/") else path == spec
        if hit and len(spec) > best_len:
            best, best_len = label, len(spec)
    return best


# The label for a path no spec matches. DEFENSIVE, and said so rather than
# justified by a case git does not produce: the comment here used to cite
# `git mv README.md .warden/rules/x.md`, which under a pathspec git never
# reports that way — it filters the deletion side out BEFORE rename
# detection and emits `A  .warden/rules/x.md` with no original field
# Since `git status` is asked only about the specs, every
# path it answers with matches one; this keeps an unmatched path from
# printing as a label-less `: <path>` should that ever stop being true.
UNWATCHED = "a path outside the watched inputs"


def _porcelain_records(out: bytes) -> list[list[str]]:
    """The paths of each `git status --porcelain -z` record, grouped.

    A rename or copy is TWO NUL-terminated fields: `XY <path>` and then the
    ORIGINAL path with NO status prefix. A reader that strips three bytes
    from every field eats three real characters off that original and labels
    it nothing — which is what this module did until a review
    reproduced it on `git mv`. Both paths differ
    from HEAD, so both are named; grouping is how the second field is read as
    a path rather than as another `XY <path>` record, and NOT how either half
    is labelled — the caller labels each path on its own.
    """
    fields = [f for f in out.split(b"\0") if f]
    records: list[list[str]] = []
    i = 0
    while i < len(fields):
        entry, i = fields[i], i + 1
        if len(entry) <= 3:            # not `XY <path>`: nothing to name
            continue
        status, group = entry[:2], [entry[3:].decode("utf-8", "replace")]
        if b"R" in status or b"C" in status:
            if i < len(fields):        # the unprefixed original path
                group.append(fields[i].decode("utf-8", "replace"))
                i += 1
        records.append(group)
    return records


class InputDrift(NamedTuple):
    """How the working tree the state was read from differs from HEAD.

    THREE numbers, deliberately not one. `differing` is per PATH and
    `inputs` is how many were asked about, so a caller can say "1 of 4
    inputs, over 6 paths" instead of "6 of the inputs", which is what the
    first cut of this said with four inputs in existence. `uncomparable`
    is kept APART from `differing`: an
    input git cannot be asked about is not an input known to differ, and
    folding the two reports an unmeasurable drift as a measured one.
    """

    differing: list[tuple[str, str]]     # (input label, differing path)
    uncomparable: list[tuple[str, str]]  # (input label, why it cannot be read)
    inputs: int                          # how many inputs were looked at

    @property
    def clean(self) -> bool:
        """True only when every input was compared AND none differs."""
        return not self.differing and not self.uncomparable


def uncommitted_inputs(root: Path) -> InputDrift:
    """How each watched input differs from HEAD, or could not be compared.

    Supersedes `uncommitted_shards`, which asked git about
    `.warden/memory/attest/` alone: an uncommitted rule file, repo.yaml or
    tags.yaml moved the reading with the note unchanged.
    """
    root = Path(root)
    specs, uncomparable = watched_inputs(root)
    out = _git(root, "status", "--porcelain", "-z", "--untracked-files=all",
               "--", *specs.values(),
               subject="how the watched working-tree inputs differ from HEAD")
    order = {label: i for i, label in enumerate(specs)}
    found: list[tuple[str, str]] = []
    for group in _porcelain_records(out):
        # EACH PATH under ITS OWN input. A rename can span two watched
        # inputs — `git mv .warden/rules/r.md .warden/memory/attest/r.md`
        # moves the reading of BOTH — and taking the first path's label for
        # the whole record printed a rules file as an attest shard and left
        # the rules dir reported clean although a rule had left it.
        # Both halves differ from HEAD and both are
        # measured, so both are counted.
        found += [(_input_label(path, specs) or UNWATCHED, path)
                  for path in group]
    found.sort(key=lambda pair: (order.get(pair[0], len(order)), pair[1]))
    return InputDrift(found, uncomparable, len(specs) + len(uncomparable))


PATHS_PER_INPUT = 3


def describe_uncommitted(pairs: list[tuple[str, str]],
                         per_input: int = PATHS_PER_INPUT) -> str:
    """`<label>: <details>` for EVERY label, eliding details past `per_input`.

    Grouped by input, and capped WITHIN an input rather than across the flat
    list. Capping the flat list at five pairs named at most five paths, and
    the paths arrive sorted by input order, so eight differing paths across
    three inputs printed "3 of the 4 input(s) ... over 8 path(s)" and then
    named the rule files alone: the count said three and the enumeration
    named one, which is the silent drop this watch exists to close
    surviving inside its own fix. Every differing input is
    now named; a long input elides its own paths and says how many.
    """
    groups: dict[str, list[str]] = {}
    for label, detail in pairs:
        groups.setdefault(label, []).append(detail)
    shown = []
    for label, details in groups.items():
        elided = len(details) - per_input
        shown.append(f"{label}: " + ", ".join(details[:per_input])
                     + (f" (+{elided} more path(s))" if elided > 0 else ""))
    return "; ".join(shown)


def describe_drift(drift: InputDrift) -> str:
    """The note's and the refusal's one sentence about the working tree.

    Empty when every input was compared and none differs — a caller appends
    it unconditionally, so silence here means "the tree the state was read
    from is HEAD's" and never "nobody looked".
    """
    # "of N" only when N is known: the CLI builds a drift with no input count
    # when git itself could not be run, and "1 of the 0 input(s)" reads as an
    # arithmetic bug in a message whose whole job is to be trusted.
    def of_total(n: int) -> str:
        return f"{n} of the {drift.inputs}" if drift.inputs else str(n)

    parts = []
    if drift.differing:
        inputs = len({label for label, _ in drift.differing})
        parts.append(
            f"The state above was read from the WORKING TREE, which differs "
            f"from HEAD in {of_total(inputs)} input(s) it is read from, over "
            f"{len(drift.differing)} path(s): "
            f"{describe_uncommitted(drift.differing)}. This run is not over "
            "the committed corpus.")
    if drift.uncomparable:
        # Its own sentence, never folded into the count above: git was not
        # asked or did not answer, so whether these differ is UNKNOWN, and an
        # unmeasured input reported as a measured difference is a claim the
        # run cannot make.
        parts.append(
            f"{of_total(len(drift.uncomparable))} input(s) could not be "
            f"compared against HEAD at all, so whether the state above is the "
            f"committed one is UNKNOWN for "
            f"{'them' if len(drift.uncomparable) > 1 else 'it'}: "
            f"{describe_uncommitted(drift.uncomparable)}.")
    return " ".join(parts)


def watch(root: Path) -> dict:
    root = Path(root)
    state, causes = read_state(root)
    path = record = None
    committed: set[str] = set()
    pending = InputDrift([], [], 0)
    try:
        path, record, committed = committed_baseline(root)
        # the state side is the working tree's: say when that is not HEAD's
        pending = uncommitted_inputs(root)
    except WatchUnread as e:
        causes.append(str(e))
    if causes:
        return {"schema": SCHEMA, "status": "unread", "unread": causes,
                "baseline": None, "transitions": [], "state": None,
                "note": ""}
    where = WATCH_DIR.as_posix()
    if record is None:
        note = (f"No watch record is committed under {where}/, so every state "
                "over a bar right now is reported as a transition.")
        before = {cls: {} for cls in CLASSES}
        tag = NO_BASELINE
    else:
        note = (f"Compared against {path}, recorded {record['recorded_at']} "
                "and committed at HEAD.")
        before = record["state"]
        tag = Path(path).stem
    on_disk = ({p.name for p in (root / WATCH_DIR).glob("watch-*.json")}
               if (root / WATCH_DIR).is_dir() else set())
    uncommitted = sorted(on_disk - committed)
    if uncommitted:
        note += (f" {len(uncommitted)} record(s) not yet committed are not a "
                 f"baseline: {', '.join(uncommitted)}.")
    drift_note = describe_drift(pending)
    if drift_note:
        note += " " + drift_note
    bar = bars()
    transitions = [
        # the subject is percent-encoded so a key never holds a space
        {"key": f"{cls}:{quote(subject, safe='/._-@')}@{tag}",
         "class": cls, "subject": subject,
         "numbers": numbers, "bar": bar[cls], "next": NEXT[cls]}
        for cls in CLASSES
        for subject, numbers in sorted(state[cls].items())
        if subject not in before[cls]]
    return {"schema": SCHEMA,
            "status": "transitions" if transitions else "quiet",
            "unread": [], "transitions": transitions, "note": note,
            "baseline": (None if record is None else
                         {"path": path, "recorded_at": record["recorded_at"]}),
            "state": state}


def exit_code(result: dict) -> int:
    return {"quiet": EXIT_QUIET, "transitions": EXIT_TRANSITIONS}.get(
        result["status"], EXIT_UNREAD)


def write_record(root: Path, state: dict, now: datetime | None = None) -> Path:
    """Write one new dated record. Never overwrites: a record is evidence."""
    now = now or datetime.now(timezone.utc)
    directory = Path(root) / WATCH_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"watch-{now:%Y%m%dT%H%M%SZ}.json"
    doc = {"schema": SCHEMA, "recorded_at": now.isoformat(timespec="seconds"),
           "bars": bars(), "state": state}
    with path.open("x") as fh:
        fh.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return path


def render(result: dict) -> str:
    lines = ["warden memory watch — reports what crossed a bar; edits nothing"]
    if result["status"] == "unread":
        lines.append("UNREAD — no transition is claimed either way, because:")
        lines += [f"  - {cause}" for cause in result["unread"]]
        return "\n".join(lines) + "\n"
    lines.append(result["note"])
    transitions = result["transitions"]
    if not transitions:
        lines.append("no transitions")
    else:
        lines.append(f"TRANSITIONS ({len(transitions)}):")
        for t in transitions:
            numbers = ", ".join(f"{k} {v}" for k, v in t["numbers"].items())
            lines.append(f"  {t['class']} {t['subject']}: {numbers}")
            lines.append(f"    bar: {t['bar']}")
            lines.append(f"    next: {t['next']}")
    if result.get("recorded"):
        lines.append(f"recorded {result['recorded']} — commit it, and the next "
                     "run compares against it")
    else:
        lines.append("`warden memory watch --record` writes this state as a "
                     "dated record for a human to commit.")
    return "\n".join(lines) + "\n"
