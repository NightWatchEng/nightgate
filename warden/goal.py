"""warden certify --goal — the redesign's five run-time evidence items.

The ladder in `certify.py` says how ENROLLED a repo is. This says whether the
redesign's success criteria HOLD, and it is the only definition of done for
them: five items, each PASS / FAIL / UNMEASURED, each derived from a committed
artifact or a named input and never from prose. A closed tracker item is not
evidence here; a shard, a workflow file, a ledger row is.

  enrollment  a decide shard scoped `goal:enrollment`, else CI gating on the README's Try it at Level 2+
  one-round   the fraction of recent attested branches whose highest review
              round is 1, over the 20 most recent, PASS at >= 0.7
  roster      the newest attest shard's `roster_verification` is `verified`
  memory      the scheduled detector workflow exists AND a machine adoption
              left its backtest artifact
  cage        `--ledger PATH` counts done rows carrying a PR number, PASS at
              >= 10; without a ledger the item is UNMEASURED

UNMEASURED is not FAIL: it means the input the item needs was not offered or
does not exist yet, and the evidence line says which. The two must not look
alike, because "not looked" and "looked and found it missing" call for
different next steps.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from cage import measure as ledger_mod

from . import certify
from . import yamlio

PASS, FAIL, UNMEASURED = "PASS", "FAIL", "UNMEASURED"

ATTEST_DIR = Path(".warden") / "memory" / "attest"
DECIDE_DIR = Path(".warden") / "memory" / "decide"
BACKTEST_DIR = Path(".warden") / "memory" / "backtests"
MEMORY_WATCH = Path(".github") / "workflows" / "memory-watch.yml"

# The decide-shard scope a founder records the enrollment run under. The
# literal is the whole contract, and it is read as the scope's FIRST TOKEN:
# a decide scope is a sentence, and a ruling that merely mentions the
# literal ("...does not touch goal:enrollment...") is not the record.
ENROLLMENT_SCOPE = "goal:enrollment"

# The mechanics half, read until a collaborator's run is recorded: the CI
# workflow, the script a job must gate on, and the README block's level.
CI_WORKFLOW = Path(".github") / "workflows" / "ci.yml"
README = Path("README.md")
TRY_IT_HEADING = "## Try it"
INIT_PROOF_SCRIPT = "scripts/init-proof.sh"
ENROLLMENT_LEVEL = 2

ONE_ROUND_WINDOW = 20      # most recent attested branches measured
ONE_ROUND_MIN_GROUPS = 5   # fewer than this and the rate is UNMEASURED
ONE_ROUND_BAR = 0.7
CAGE_BAR = 10

# The cage's ledger row, as its runner writes it: date,bead,outcome,pr,note,
# node,duration_s,attempt,run_id. `cage.measure.parse` is the one reader —
# csv, header-tolerant, and returning each malformed record by line so the
# evidence line can name it; these indices are its own, pinned by tests.
LEDGER_DATE, LEDGER_OUTCOME, LEDGER_PR = 0, 2, 3
LEDGER_DONE = "done"

ITEMS = ("enrollment", "one-round", "roster", "memory", "cage")


@dataclass(frozen=True)
class Verdict:
    item: str
    state: str
    evidence: str


def _shards(directory: Path) -> tuple[list[tuple[Path, dict]], list[Path]]:
    """(readable shards as (path, doc), unreadable paths), name order.

    A shard that is not a JSON object is listed rather than skipped in
    silence: a reader that drops what it cannot parse reports a corpus it
    did not read.
    """
    docs, bad = [], []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            bad.append(path)
            continue
        if isinstance(doc, dict):
            docs.append((path, doc))
        else:
            bad.append(path)
    return docs, bad


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _unreadable_note(bad: list[Path], root: Path) -> str:
    if not bad:
        return ""
    return f"; {len(bad)} unreadable shard(s) skipped, first {_rel(bad[0], root)}"


# --------------------------------------------------------------------------- #
# The five checks
# --------------------------------------------------------------------------- #

# Punctuation a scope's first token may carry after the literal — the house
# style writes "goal:enrollment, <url>" and "goal:enrollment: <url>" as
# readily as "goal:enrollment <url>".
_TOKEN_TRAIL = ",:;."


def _scoped_enrollment(scope: object) -> bool:
    """True when the scope's first whitespace-delimited token, less any
    trailing punctuation, is the literal."""
    if not isinstance(scope, str):
        return False
    first = scope.split()[:1]
    return bool(first) and first[0].rstrip(_TOKEN_TRAIL) == ENROLLMENT_SCOPE


def _records(text: str) -> list[str]:
    """Split on `\\n` alone, as awk does; `splitlines` would also split on `\\r` `\\f` `\\v`."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _read(path: Path) -> tuple[str | None, str]:
    """(text, "") with no newline translation; (None, why not) when missing or not UTF-8."""
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return f.read(), ""
    except FileNotFoundError:
        return None, "is missing"
    except OSError as e:
        return None, f"could not be read: {e.strerror or e}"
    except ValueError:
        return None, "is not UTF-8 text"


def try_it_block(readme: str) -> str | None:
    """The README's Try it block, byte for byte: the lines between the first
    fence under `## Try it` and its close; None without one. tests/test_goal.py
    pins it equal, as bytes, to scripts/readme-try-it.sh's awk reading."""
    in_section = in_fence = False
    lines: list[str] = []
    for line in _records(readme):
        if line.startswith("## "):
            if in_section:
                break
            in_section = line == TRY_IT_HEADING
        elif in_section and line.startswith("```"):
            if in_fence:
                return "".join(f"{x}\n" for x in lines)
            in_fence = True
        elif in_section and in_fence:
            lines.append(line)
    return None


def _try_it_level(root: Path) -> tuple[int | None, str]:
    """(the level the block's `warden certify --level N` asks, that line) or (None, why)."""
    text, why = _read(root / README)
    if text is None:
        return None, f"{README.as_posix()} {why}"
    block = try_it_block(text)
    if block is None:
        return None, (f"{README.as_posix()} has no fenced block under "
                      f"{TRY_IT_HEADING!r}")
    for line in _records(block):
        m = re.fullmatch(r"warden certify\s+--level[=\s]\s*(\d+)", line.strip())
        if m:
            return int(m.group(1)), line.strip()
    return None, (f"the {TRY_IT_HEADING!r} block in {README.as_posix()} has no "
                  "`warden certify --level N` line")


# What may stand before the script on its line: shells, env, timeout, flags.
_RUNNER = re.compile(r"^(bash|sh|env|timeout|-\S*|\w+=\S*|\d+[smhd]?)$")
# A failing `&&` member does not trip errexit; a lone `&` backgrounds. Not E-01's.
_LIST_OR_BACKGROUND = re.compile(r"&&|(?<![&|>])&(?![&|>])")


def _invocation(run: str, shell: str) -> tuple[bool, str | None]:
    """(whether a logical line of `run` — continuations joined, quotes
    dropped — has this tree's script as its command, bare or after runner
    tokens; the form swallowing its exit status, if any: those certify's E-01
    reads, plus a `&&` list or a lone `&`). A mention is no invocation."""
    # Comments dropped, quotes removed: a quoted path would otherwise pair
    # its quote with the next one and hide what follows.
    bare = "\n".join(l for l in run.split("\n") if not l.lstrip().startswith("#"))
    bare = bare.replace('"', "").replace("'", "")
    lines = certify._logical_lines(bare)
    for i, line in enumerate(lines):
        tokens = line.split()
        while tokens and _RUNNER.match(tokens[0]):
            tokens.pop(0)
        if not tokens or tokens[0].removeprefix("./").removeprefix("$GITHUB_WORKSPACE/") \
                .removeprefix("${GITHUB_WORKSPACE}/") != INIT_PROOF_SCRIPT:
            continue
        if _LIST_OR_BACKGROUND.search(line[line.index(INIT_PROOF_SCRIPT):]):
            return True, "a `&&` list or `&` after the script"
        # The suffix read is this line's alone: another line naming the path
        # (an `echo ... || true`) is not the invocation. Block forms (`set +e`,
        # pipefail) are still read across the whole run.
        only = "\n".join(l if j == i else l.replace(INIT_PROOF_SCRIPT, "")
                         for j, l in enumerate(lines))
        return True, certify._swallow_marker(only, INIT_PROOF_SCRIPT, shell=shell)
    return False, None


def _literal(value: object) -> str | None:
    """`true`/`false` when VALUE is that YAML bool or word, bare or in `${{ }}`."""
    word = (value.strip().removeprefix("${{").removesuffix("}}").strip() if isinstance(value, str)
            else str(value)).lower()
    return word if isinstance(value, (str, bool)) and word in ("true", "false") else None


def _cannot_fail(job: dict, step: dict) -> str | None:
    """Why the job or step is never a gate: a `continue-on-error` not
    literally false, or a literally false `if`. Triggers are not read here."""
    for what, node in (("job", job), ("step", step)):
        advisory = node.get("continue-on-error")
        if advisory is not None and _literal(advisory) != "false":
            return f"the {what} sets continue-on-error: {_literal(advisory) or advisory}"
        if _literal(node.get("if")) == "false":
            return f"the {what}'s `if` is false"
    return None


def ci_proof(root: Path) -> tuple[str | None, str]:
    """(the CI job whose step gates on the init-proof script, its evidence
    line) when the README's block asks Level 2 or above; (None, why not)."""
    text, why = _read(root / CI_WORKFLOW)
    if text is None:
        return None, f"{CI_WORKFLOW.as_posix()} {why}"
    try:
        doc = yamlio.load(text)
    except yaml.YAMLError:
        return None, f"{CI_WORKFLOW.as_posix()} is not valid YAML"
    if not (root / INIT_PROOF_SCRIPT).is_file():
        return None, f"{INIT_PROOF_SCRIPT} is not in the tree"
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    job_name, disarmed = None, []
    for job_id, job in (jobs.items() if isinstance(jobs, dict) else []):
        steps = job.get("steps") if isinstance(job, dict) else None
        for step in (steps if isinstance(steps, list) else []):
            run = step.get("run") if isinstance(step, dict) else None
            if not isinstance(run, str):
                continue
            invoked, swallow = _invocation(run, str(step.get("shell") or ""))
            if not invoked:
                continue
            reason = (f"the line carries {swallow}" if swallow
                      else _cannot_fail(job, step))
            if reason is None:
                job_name = str(job.get("name") or job_id)
                break
            disarmed.append(f"job {job_id!r} runs it but {reason}")
        if job_name:
            break
    if job_name is None:
        return None, (f"no job in {CI_WORKFLOW.as_posix()} has a step invoking "
                      f"{INIT_PROOF_SCRIPT} as a gate"
                      + ("; " + "; ".join(disarmed) if disarmed else ""))
    level, line = _try_it_level(root)
    if level is None:
        return None, line
    if level < ENROLLMENT_LEVEL:
        return None, (f"the {TRY_IT_HEADING!r} block's `{line}` asks Level "
                      f"{level}, below {ENROLLMENT_LEVEL}")
    return job_name, (f"CI job {job_name!r} has a step invoking {INIT_PROOF_SCRIPT} "
                      f"as a gate, and the README's {TRY_IT_HEADING!r} block's "
                      f"`{line}` asks Level {level}")


def check_enrollment(root: Path) -> Verdict:
    """A decide shard scoped `goal:enrollment` (the collaborator's run, the criterion)
    or, until one is recorded, the CI job proving the block; the evidence says which."""
    docs, bad = _shards(root / DECIDE_DIR)
    for path, doc in docs:
        if _scoped_enrollment(doc.get("scope")):
            return Verdict("enrollment", PASS,
                           f"collaborator: {_rel(path, root)} is scoped "
                           f"{ENROLLMENT_SCOPE}" + _unreadable_note(bad, root))
    job_name, why = ci_proof(root)
    if job_name is not None:
        return Verdict("enrollment", PASS,
                       f"mechanics: {why}; no collaborator run is recorded yet"
                       + _unreadable_note(bad, root))
    return Verdict("enrollment", UNMEASURED,
                   f"no decide shard under {DECIDE_DIR.as_posix()}/ has a "
                   f"scope beginning {ENROLLMENT_SCOPE}, and {why}; record the "
                   "invited collaborator's run URL with `warden decide` under "
                   "that scope" + _unreadable_note(bad, root))


def _identity_keys(doc: dict) -> list[str]:
    """What names the branch a shard attests. `bead` and `pr` when the writer
    was told them; every `range_patch_ids` entry, since a patch-id is the
    stable hash of one commit's diff and two rounds of one branch share the
    commits the first round judged, across a rebase or a merge-forward that
    moves `base_sha`; and `base_sha` only when a shard carries none of
    those. Shards are one branch when any key is shared. A shared key can
    only fold two branches into one, which pushes a group's highest round
    UP and the rate DOWN — it never splits one branch into a fragment that
    reads as a one-round success."""
    keys = []
    for field in ("bead", "pr"):
        value = doc.get(field)
        if isinstance(value, str) and value:
            keys.append(f"{field}:{value}")
    keys += [f"patch:{pid}" for pid in (doc.get("range_patch_ids") or [])
             if isinstance(pid, str) and pid]
    return keys or [f"base_sha:{doc.get('base_sha', '')}"]


def _branches(docs: list[dict]) -> list[list[dict]]:
    """Group shards into branches: the connected components of shared
    identity keys."""
    parent = list(range(len(docs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    for i, doc in enumerate(docs):
        for key in _identity_keys(doc):
            if key in owner:
                parent[find(i)] = find(owner[key])
            else:
                owner[key] = i
    groups: dict[int, list[dict]] = {}
    for i, doc in enumerate(docs):
        groups.setdefault(find(i), []).append(doc)
    return list(groups.values())


def _highest_round(docs: list[dict]) -> int | None:
    """The highest `round` any roster entry or finding record in these
    shards carries; None when none carries one."""
    rounds = [entry.get("round") for d in docs
              for entry in list(d.get("reviewers") or []) + list(d.get("records") or [])
              if isinstance(entry, dict)]
    ints = [r for r in rounds if isinstance(r, int) and not isinstance(r, bool)]
    return max(ints) if ints else None


def check_one_round(root: Path) -> Verdict:
    """Of the 20 most recent attested branches, the fraction whose highest
    review round is 1. A branch none of whose shards carry a round number
    predates the field and cannot be placed, so it is left out of the 20
    rather than read as any round."""
    docs, bad = _shards(root / ATTEST_DIR)
    groups = _branches([doc for _, doc in docs])
    measurable = []
    for members in groups:
        highest = _highest_round(members)
        if highest is not None:
            newest = max(str(d.get("reviewed_at", "")) for d in members)
            measurable.append((newest, highest))
    measurable.sort(reverse=True)
    window = measurable[:ONE_ROUND_WINDOW]
    unplaced = len(groups) - len(measurable)
    note = (f"; {unplaced} branch(es) carry no round number and are left out"
            if unplaced else "") + _unreadable_note(bad, root)
    if len(window) < ONE_ROUND_MIN_GROUPS:
        return Verdict("one-round", UNMEASURED,
                       f"{len(window)} attested branch(es) carry a round number "
                       f"under {ATTEST_DIR.as_posix()}/; {ONE_ROUND_MIN_GROUPS} "
                       "needed" + note)
    ones = sum(1 for _, highest in window if highest == 1)
    fraction = ones / len(window)
    state = PASS if fraction >= ONE_ROUND_BAR else FAIL
    return Verdict("one-round", state,
                   f"{ones} of the {len(window)} most recent attested branches "
                   f"finished in one round = {fraction:.2f} (bar "
                   f"{ONE_ROUND_BAR:.2f}; branches keyed by bead, pr, shared "
                   f"range_patch_ids or base_sha over {len(docs)} shards)"
                   + note)


def check_roster(root: Path) -> Verdict:
    """The newest attest shard, by `reviewed_at`, says `verified`.

    Shard file names begin with the shard's UTC timestamp, so the newest
    file by name is the newest shard whether or not it parses: an
    unreadable newest shard is a FAIL naming it, never a fall-back to the
    readable one before it."""
    docs, bad = _shards(root / ATTEST_DIR)
    if not docs:
        return Verdict("roster", FAIL,
                       f"no attest shard under {ATTEST_DIR.as_posix()}/"
                       + _unreadable_note(bad, root))
    newest_file = max(path for path in [p for p, _ in docs] + bad)
    if newest_file in bad:
        return Verdict("roster", FAIL,
                       f"{_rel(newest_file, root)} (newest) could not be read "
                       "as a JSON object, so its roster_verification is unknown"
                       + _unreadable_note(bad, root))
    path, doc = max(docs, key=lambda pd: (str(pd[1].get("reviewed_at", "")),
                                          pd[0].name))
    value = doc.get("roster_verification")
    found = "absent" if value is None else repr(value)
    if value == "verified":
        return Verdict("roster", PASS,
                       f"{_rel(path, root)} carries roster_verification: verified"
                       + _unreadable_note(bad, root))
    return Verdict("roster", FAIL,
                   f"{_rel(path, root)} (newest) carries roster_verification "
                   f"{found}, not 'verified'" + _unreadable_note(bad, root))


def check_memory(root: Path) -> Verdict:
    """The detector workflow exists AND one adoption artifact exists — the
    file `warden autonomy adopt` writes under the backtests dir, keyed by its
    `action: adopt` field."""
    workflow = root / MEMORY_WATCH
    has_workflow = workflow.is_file()
    docs, bad = _shards(root / BACKTEST_DIR)
    adopted = [path for path, doc in docs if doc.get("action") == "adopt"]
    if has_workflow and adopted:
        return Verdict("memory", PASS,
                       f"{MEMORY_WATCH.as_posix()} exists and "
                       f"{_rel(adopted[0], root)} records action: adopt"
                       + _unreadable_note(bad, root))
    missing = []
    if not has_workflow:
        missing.append(f"{MEMORY_WATCH.as_posix()} is missing")
    if not adopted:
        missing.append(f"no readable artifact under {BACKTEST_DIR.as_posix()}/ "
                       "records action: adopt")
    return Verdict("memory", FAIL, "; ".join(missing) + _unreadable_note(bad, root))


def check_cage(root: Path, ledger: Path | None) -> Verdict:
    """Rows whose outcome is `done` and whose PR column is non-empty."""
    if ledger is None:
        return Verdict("cage", UNMEASURED,
                       "no --ledger given; pass the cage's ledger.csv to count "
                       "its done rows")
    try:
        text = ledger.read_text()
    except OSError as e:
        return Verdict("cage", FAIL, f"ledger {ledger} could not be read: "
                       f"{e.strerror or e}")
    rows, bad = ledger_mod.parse(text.splitlines())
    done = [r for r in rows
            if r[LEDGER_OUTCOME] == LEDGER_DONE and r[LEDGER_PR].strip()]
    state = PASS if len(done) >= CAGE_BAR else FAIL
    return Verdict("cage", state,
                   f"{len(done)}/{CAGE_BAR} done rows with a PR number in "
                   f"{ledger} ({len(rows)} well-formed rows read"
                   + (f"; {ledger_mod.malformed_note(bad)}" if bad else "")
                   + ")")


# --------------------------------------------------------------------------- #
# Run and render
# --------------------------------------------------------------------------- #

def run(root: Path, ledger: Path | None = None) -> dict:
    verdicts = [check_enrollment(root), check_one_round(root),
                check_roster(root), check_memory(root),
                check_cage(root, ledger)]
    assert tuple(v.item for v in verdicts) == ITEMS
    return {
        "items": [{"item": v.item, "state": v.state, "evidence": v.evidence}
                  for v in verdicts],
        "passed": sum(1 for v in verdicts if v.state == PASS),
        "total": len(verdicts),
    }


def all_pass(doc: dict) -> bool:
    return doc["passed"] == doc["total"]


def render(doc: dict) -> str:
    """Fixed-width: one row per item, then the summary line."""
    item_w = max(len("item"), *(len(i["item"]) for i in doc["items"]))
    state_w = max(len("verdict"), len(UNMEASURED))
    lines = [f"{'item':<{item_w}}  {'verdict':<{state_w}}  evidence"]
    for i in doc["items"]:
        lines.append(f"{i['item']:<{item_w}}  {i['state']:<{state_w}}  "
                     f"{i['evidence']}")
    lines.append(f"GOAL: {doc['passed']} of {doc['total']}")
    return "\n".join(lines) + "\n"
