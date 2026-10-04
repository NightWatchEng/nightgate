"""The repair stop condition, computed from artifacts warden already writes.

deliver caps repair at 2 rounds. A round counter cannot tell "the change still
has defects" from "the repair scaffolding has defects": later rounds largely
review files a PRIOR REPAIR COMMIT wrote, so a bare count stops on the wrong
one.

So the cap counts CHANGE-DEFECT rounds, and this module says which a round
is. Two ranges, both read from round manifests `warden round new` wrote and
never typed by the builder:

- the ORIGINAL diff: the first round's `base_sha...head_sha` (merge-base
  three-dot, so a base branch that moved underneath the work adds nothing);
- the REPAIR range: the files the repair COMMITS themselves wrote — the
  union of each non-merge FIRST-PARENT commit's own diff over the first
  round's head to the latest round's head, minus every commit already
  reachable from a base a round recorded, minus every file the base branch
  moved between the FIRST ROUND's base and every other recorded base AND
  whose bytes the branch carries verbatim from that base (by PATH alone, a
  sibling PR touching a file the repair also wrote would delete the repair's
  own work from this set, and what leaves it lands in `original`, which is
  the revert clause's premise). Not `H1...Hn`:
  that range carries whatever a mid-repair `git merge main` brought in, so a
  LOW on a file NOBODY on this branch wrote would classify `repair` and end
  the review loop — the exact case every surface says never triggers the
  early stop. `--first-parent` closes the ordinary merge outright; the
  two subtractions close the fast-forward, the squash, the cherry-pick and a
  round head minted on the base branch — CONDITIONALLY, and `_repair_files`
  states each condition. Every one errs the same way — what leaves the repair
  set lands in `original`, and `original` counts.

A round COUNTS against the cap when an open finding cites a file the original
diff changed, or when any open finding is above LOW wherever it lands. A round
whose open findings are all LOW and all on repair-written files is a
SCAFFOLDING round: the crew is reviewing its own repair, the loop ends
there, and the builder parks what is open exactly as at the cap.

The classification rules are fixed, not argued:

- a file in BOTH ranges is `both`, which the verdict reads as repair-written —
  repair-written means "a file a prior repair commit wrote", whatever else
  also touched it. It is reported as `both`, never folded, so a repair that
  rewrote the whole change is visible;
- a finding with no file, or on a file in neither range (rename-complete and
  wiki-fidelity produce these by design), is `original`. The unclassifiable
  case never triggers the early stop;
- a severity the schema does not name ranks above LOW; a status that is not
  one of the three closed states is open. Absence is not resolution.

Only COUNTING findings take part in any of this. A wording finding at a
known severity below every blocking severity is reported beside the verdict
and recorded, and does not count. What a finding judges is resolved at one
seam, `kind_of`: a finding whose rule_id names a declared rule judges what
that rule's `judges:` says, whatever the record itself carries; an
`unmapped:<slug>` finding has no rule to say, so it judges wording when its
`judges` field is absent or says `wording`, and behaviour on any other
value. Every other finding counts: a behaviour rule's, an unmapped finding
whose field says anything but `wording`, a rule_id that is neither declared
nor `unmapped:`, and a wording finding at a blocking severity, or at any
severity when the blocking severities are unknown.

Every input that decides a finding's kind is read AT THE ROUND HEAD — the
`head_sha` in the latest round's `round.json`, the commit the verdict is
about — and never off the working tree, so an uncommitted edit changes
nothing: repo.yaml (its `review.rules_dir` and `review.blocking_severities`)
and each rule's `judges:`. The rules_version recorded with the verdict hashes
the repo's surfaces AT THE HEAD too (the rule files, the checkers dir beside
them, repo.yaml's review subtree), but its two platform surfaces, the findings
schema and `warden/mechanical.py`, come from the RUNNING warden package, as
they do for every other rules_version caller: an edit to either moves it.
The head reads go through git objects into memory; no file is written.
`_AtHead` states how a repo enrolled below the git root, a rules_dir outside
the repository, an absolute rules_dir, a path in a git submodule (exit 2,
naming the submodule) and a committed symlink are each handled.

The severity clause is what keeps the file classification from being a
laundering path: a builder whose repair touches every original file cannot
make a MEDIUM stop counting, and the LOWs that do reach adjudication early
are recorded and non-blocking.

The chain of rounds is proved by ancestry, never by a clock: heads are ordered
by how many of the other heads are their ancestors, and a head that is not a
descendant of the one before it is refused as a round from another line of
history.

A round that was never MINTED leaves no manifest, so nothing above can see it.
If round 1 was not minted and round 2 was, the original range is measured as
the true original PLUS round 1's own repair commit — so a file that repair
commit wrote reads as the change's and can promote a finding to the cap's
revert trigger. It is a defect rather than user error because nothing
distinguishes "round 1 was never minted" from "round 2 was the first round",
and the widened range looks correct. The one witness that CAN see the missing
mint is the payload, which declares the rounds the review ran: a finding
attributed to round N is a claim that N rounds ran, and `_declared_rounds`
below refuses a chain carrying fewer round directories than the payload
declares. Absence of a round is not evidence that no round ran.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import attest as attest_mod
from . import config as config_mod
from . import memory as memory_mod
from . import runs
from . import rules as rules_mod
from .diffs import DiffError, git_env, run_git

CLOSED_STATUSES = frozenset({"fixed", "refuted", "dismissed-with-reason"})
_SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
_UNRANKED = 99  # a severity the schema does not name is not LOW

VERDICT_CLEAN = "clean"
VERDICT_STOP = "scaffolding-stop"
VERDICT_COUNTS = "counts"


class RepairError(Exception):
    """The stop condition could not be computed — never a verdict."""


@dataclass(frozen=True)
class Report:
    base_sha: str
    heads: tuple[str, ...]
    original_commits: int
    original_files: tuple[str, ...]
    repair_files: tuple[str, ...]
    # (finding, class) for every OPEN finding, in payload order
    rows: tuple[tuple[dict, str], ...]
    verdict: str
    reasons: tuple[str, ...]
    # every OPEN wording finding below the blocking severities, in payload
    # order; none of them count
    wording: tuple[dict, ...] = ()
    # the rules_version of the rules the kinds were read from
    rules_version: str = ""
    # (rule_id, kind) for every rule id the findings name
    rule_kinds: tuple[tuple[str, str], ...] = ()


def findings_from(payload: object) -> list[dict]:
    """The findings in an attestation payload (`{"findings": [...]}`) — the
    same file `attest write --findings` reads, so there is no second findings
    file to go stale — or a bare list, which is accepted here for a
    classify-only caller and is NOT what `attest write` reads (it requires
    the payload object)."""
    if isinstance(payload, dict):
        payload = payload.get("findings")
    if not isinstance(payload, list) or not all(
            isinstance(f, dict) for f in payload):
        raise RepairError(
            "--findings must be an attestation payload with a `findings` "
            "list, or a bare list of findings — got neither")
    return payload


def _manifest(round_dir: Path) -> dict:
    if not round_dir.is_dir():
        raise RepairError(
            f"{round_dir}: no such round directory — a path warden did not "
            "mint resolves to its PARENT's round.json, so a typo would read "
            "as a valid round")
    try:
        doc = runs.round_manifest_for(round_dir)
    except runs.RoundError as e:
        raise RepairError(str(e)) from e
    if doc is None:
        raise RepairError(
            f"{round_dir}: warden did not mint this round (no round.json), so "
            "it has no base or head to classify against — pass the directory "
            "`warden round new` printed")
    for key in ("base_sha", "head_sha"):
        if not isinstance(doc.get(key), str) or not doc[key]:
            raise RepairError(f"{round_dir}: round.json names no {key}")
    return doc


def _resolve_commit(root: Path, sha: str, label: str) -> str:
    try:
        return run_git(root, "rev-parse", "--verify", f"{sha}^{{commit}}").strip()
    except DiffError as e:
        raise RepairError(
            f"{label} {sha[:12]} is not a commit this repository has — the "
            f"round that names it happened somewhere else ({e})") from e


def _is_ancestor(root: Path, older: str, newer: str) -> bool:
    proc = subprocess.run(["git", "merge-base", "--is-ancestor", older, newer],
                          cwd=root, capture_output=True, text=True,
                          env=git_env())
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise RepairError(f"git merge-base --is-ancestor {older[:12]} "
                      f"{newer[:12]} failed: {proc.stderr.strip()}")


def _ref_bases(root: Path, docs: list[dict]) -> list[str]:
    """The recorded base REF NAMES, resolved as they stand NOW.

    The third subtraction. The other two read `base_sha`, and both need some
    round to have recorded a base that already contains the synced base-branch
    work. `pre-pr-review` mints with `--base main` — a LOCAL ref a fetch does
    not move — so local main can sit behind `origin/main` and EVERY recorded
    base be stale. With stale bases a fast-forward, a squash-merge or a
    cherry-pick off the base branch puts a base-branch file in the repair set,
    and a LOW on it would end the review loop.

    The manifest also records the base's NAME, which is a second, independent
    reading of "where the base branch is". Resolving it here is strictly
    fail-closed: it can only drop a commit from the repair set, and what leaves
    the repair set lands in `original`, which COUNTS.

    A name that will not resolve — a branch deleted, a clone that never had it —
    contributes nothing and is NOT a refusal: the chain is still classified from
    the recorded shas alone. Refusing every chain whose base branch
    was deleted would be failing closed on the wrong axis.

    A resolved ref joins BOTH subtractions, on one condition: the file
    subtraction takes it only while it has NOT absorbed this branch
    (`_repair_files` tests descent from the first round's head). Reachability
    can consult an absorbed ref safely — its descendant test can only drop
    commits that are not the repair's — while the file subtraction over an
    absorbed base would delete the repair's paths wholesale, and that
    condition is the whole of the difference. The squash route is the case
    that needs the file half: a squash is a NEW commit reachability cannot
    see, and under a stale recorded base nothing else can subtract it.

    WHAT IT DOES NOT CLOSE: a local ref that is itself stale resolves to the
    same stale commit and adds nothing. That residual is closed at the other
    end — `warden round new` refuses a `--base` that is behind its own
    upstream — not here.
    """
    out: list[str] = []
    for doc in docs:
        name = doc.get("base")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            out.append(run_git(root, "rev-parse", "--verify",
                               f"{name}^{{commit}}").strip())
        except DiffError:
            continue  # a name this clone does not have adds nothing
    return out


def _chain(root: Path, round_dirs: list[Path]
           ) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """(base_sha of the first round, heads oldest-first, every round's RECORDED
    base, the recorded base REF NAMES resolved now), proved by ancestry.

    The third value is what keeps the base branch out of the repair set: a
    commit already reachable from any base a round recorded is the base
    branch's, however it reached this line of history. The fourth is the same
    reading taken from the base's NAME rather than its recorded sha
    (`_ref_bases`), and the two are kept APART because they are trusted
    differently: a recorded base is a declaration and drives both subtractions
    unconditionally, while a resolved ref is corroboration and joins the file
    subtraction only while it has not absorbed the branch (`_repair_files`).

    The recorded bases must ADVANCE. A later round naming a base
    that is not a descendant of the first round's base is refused, because the
    file subtraction below is a three-dot diff: run against an OLDER base it
    removes nothing, silently, and the verdict then reads as if the subtraction
    had been checked. A chain whose bound cannot be established is refused
    (exit 2), never classified on the assumption — and a 2 is never a stop
    condition met.
    """
    if not round_dirs:
        raise RepairError("no round given — nothing to classify against")
    by_head: dict[str, list[dict]] = {}
    for d in round_dirs:
        doc = _manifest(Path(d))
        head = _resolve_commit(root, doc["head_sha"], "head_sha")
        by_head.setdefault(head, []).append(doc)
    heads = list(by_head)
    # Ordered by ancestry: a head is later than every head that is its
    # ancestor. Two unrelated heads share a rank, and the pairwise check
    # below refuses them by name.
    rank = {h: sum(1 for o in heads if o != h and _is_ancestor(root, o, h))
            for h in heads}
    heads.sort(key=lambda h: rank[h])
    for prev, nxt in zip(heads, heads[1:]):
        if not _is_ancestor(root, prev, nxt):
            raise RepairError(
                f"round head {nxt[:12]} is not a descendant of {prev[:12]} — "
                "a round from another line of history, refused rather than "
                "averaged into this chain")
    # Every round's base_sha is resolved, not only the first head's: a sha
    # this repository does not have says the round happened somewhere else,
    # and that refusal must not depend on which head the round landed at.
    all_bases: set[str] = set()
    for head in heads:
        for doc in by_head[head]:
            all_bases.add(_resolve_commit(root, doc["base_sha"], "base_sha"))
    first = by_head[heads[0]]
    bases = {_resolve_commit(root, doc["base_sha"], "base_sha") for doc in first}
    if len(bases) != 1:
        raise RepairError(
            f"the rounds at {heads[0][:12]} name different bases "
            f"({', '.join(b[:12] for b in sorted(bases))}); the original diff "
            "has one base")
    base = bases.pop()
    if base == heads[0]:
        raise RepairError(
            f"round {heads[0][:12]} names its own head as base_sha, so the "
            "original diff is empty and every later commit would classify as "
            "repair-written — a chain that cannot see the change is refused, "
            "not read as a clean one")
    for other in sorted(all_bases):
        if other != base and not _is_ancestor(root, base, other):
            raise RepairError(
                f"the recorded bases do not advance: round base {other[:12]} "
                f"is not a descendant of the first round's base {base[:12]}. "
                "The file subtraction is a three-dot diff, so against an OLDER "
                "or unrelated base it removes nothing — silently — and the "
                "verdict reads as though the base branch had been subtracted "
                "out. A chain this command cannot bound is "
                "refused, not classified on the assumption")
    ordered = sorted(all_bases, key=lambda b: (b != base, b))
    extra = [b for b in _ref_bases(root, [d for docs in by_head.values()
                                          for d in docs])
             if b not in all_bases]
    return (base, tuple(heads), tuple(ordered), tuple(sorted(set(extra))))


# Path listings are read back and fed to `git rev-parse <rev>:<path>`, so they
# must be the bytes git will accept there — and a listing git has C-QUOTED is
# not. `--name-only -z` is the quoting-free read: paths are NUL-terminated and
# never escaped, whatever `core.quotePath` says and whatever the path holds.
#
# Why not `core.quotePath=false`: it is only half of the quoting-free read.
# `core.quotePath` defaults to TRUE, which C-quotes any path holding a
# non-ASCII byte (`é.md` comes back as `"\303\251.md"`, quotes and all), and
# git C-quotes a path containing a double quote, a backslash or a control
# character WHATEVER the flag says (`"we\"ird.md"`, `"tab\tx.md"`). A quoted
# path fails `rev-parse`, `_blob` returns None for BOTH sides, the two Nones
# compare equal, and a file the repair wrote is silently subtracted into
# `original`. What `-z` does not settle is DECODING: `run_git` reads text
# under the locale, so a path whose bytes are not valid there is a
# `UnicodeDecodeError` out of the listing, not a quoted line — loud.
_NUL_PATHS = ("--name-only", "-z")


def _paths(listing: str) -> list[str]:
    """The paths in a `-z` listing: NUL-terminated, never quoted."""
    return [p for p in listing.split("\0") if p]


def _changed(root: Path, base: str, head: str) -> tuple[str, ...]:
    try:
        out = run_git(root, "diff", *_NUL_PATHS, f"{base}...{head}")
    except DiffError as e:
        raise RepairError(str(e)) from e
    return tuple(sorted(_paths(out)))


def _blob(root: Path, rev: str, path: str) -> str | None:
    """The blob sha of `path` at `rev`, or None where it is absent there.

    Absence is a VALUE, not an error: a path the base branch deleted and one
    it never had are both "nothing of the base branch's is here", and
    `_carries_base_bytes` has to see that rather than raise on it.
    """
    try:
        return run_git(root, "rev-parse", f"{rev}:{path}").strip()
    except DiffError:
        return None


def _carries_base_bytes(root: Path, path: str, first_base: str,
                        other: str, last: str) -> bool:
    """Does the branch hold, at `path`, bytes the BASE BRANCH itself put there?

    Asked of every VERSION the base branch had between the recorded bases, not
    only of its tip. Against the tip alone the test is defeated by the base
    branch moving that path AGAIN after the branch absorbed it: the branch then
    holds base-branch bytes matching no current version, the path stays in the
    repair set, and a LOW on work nobody on this branch authored ends the
    review loop. That is the fail-OPEN direction, and the module docstring
    says this mechanism never errs in it. Both `merge --squash` and a plain
    absorbing commit reach it.

    Bounded: `rev-list -- <path>` walks only the commits that touched it.
    """
    want = _blob(root, last, path)
    try:
        shas = run_git(root, "rev-list", f"{first_base}..{other}", "--", path)
    except DiffError as e:
        raise RepairError(str(e)) from e
    # The FIRST base's own version is in the set, not just the range after it:
    # the commit that put those bytes on the base branch is very often the one
    # the first round was minted against.
    versions = [other, first_base, *(line.strip() for line in shas.splitlines())]
    return any(want == _blob(root, rev, path) for rev in versions if rev)


def _repair_files(root: Path, first: str, last: str,
                  bases: tuple[str, ...],
                  ref_bases: tuple[str, ...] = ()) -> tuple[str, ...]:
    """The files the repair COMMITS wrote, between two round heads.

    The union of each non-merge FIRST-PARENT commit's own diff, minus what
    the base branch put there. Never `first...last`: that range carries
    whatever a mid-repair `git merge main` brought in, and those files are
    not the repair's scaffolding.

    `--first-parent` is the load-bearing flag — it drops the side a merge
    brought in. `--no-merges` states the same intent for the merge commit
    itself and is belt-and-braces: `diff-tree` on a merge already emits no
    paths, so removing it changes no result.

    First-parent alone is not enough. A
    FAST-FORWARD `git merge main`, a `git merge --squash main`, a
    `cherry-pick` off the base branch, and a round head minted ON the base
    branch all put base-branch work on the FIRST-parent chain. So:

    - a candidate commit already reachable from any base a round recorded is
      dropped — it is the base branch's commit, whatever route it took here;
    - and every file the base branch itself moved between the FIRST round's
      base and any OTHER recorded base, WHERE THE BRANCH CARRIES THAT BASE'S
      BYTES, is subtracted — which catches the squash and the cherry-pick,
      where the commit is new but the content is the base branch's.

    Both subtractions run one way only: a file that leaves the repair set
    lands in `original`, which COUNTS. Erring toward "the change's" is the
    fail-closed direction, because the early stop is what is at stake.

    A THIRD subtraction closes the case the first two cannot: both of them need some round to have RECORDED a base that
    already contains the synced base-branch work, and a chain minted against a
    stale local ref records no such base. `_ref_bases` resolves the base's NAME
    as it stands now, which is a second, independent reading of where the base
    branch is, and it joins both subtractions — the file subtraction only while
    it has NOT absorbed this branch, since over an absorbed ref that
    subtraction would delete the repair's own paths (the same limit a RECORDED
    absorbed base still imposes, and not one a corroborating ref should add).

    THE FILE SUBTRACTION IS BY CONTENT, NOT BY PATH. By path, a file the
    repair genuinely REWROTE would leave this set whenever the base branch
    also moved it between the recorded bases. `warden round new --base
    origin/main` resolves the base AT MINT TIME, so a sibling PR merging
    between rounds gives later rounds a different recorded base — and a path
    subtraction would fire on a base-branch MOVE the branch had not merged at
    all, on exactly the shared surfaces where repairs cluster (docs/wiki, the
    policy file), putting a repair-written file in `original` and meeting the
    REVERT clause's premise, which is a trigger the builder may not argue
    with. So the question asked is: does the branch carry, at that path, ANY
    VERSION THE BASE BRANCH ITSELF PUT THERE (`_carries_base_bytes`)? If it
    does, the content is the base branch's whatever route it took here; if it
    does not, `git show --name-only <repair sha>` stands and the file is
    repair-written. Every version and not only the current one: the base
    branch moving a path AGAIN after the branch absorbed it would otherwise
    defeat the test in the fail-OPEN direction, which is the one direction
    this mechanism must never err in.

    ONE LIMIT REMAINS, deliberate and pinned by a named test so that closing
    it later is a visible change of behaviour and not a silent widening of the
    early stop: where a round records
    a base that has ABSORBED this branch, every repair path is inside
    `bases[0]...that base` and the branch carries that base's bytes for all of
    them, so `repair_files` collapses to `()` and the scaffolding stop cannot
    fire for that chain at all. That is fail-closed — what leaves this set
    lands in `original`, which COUNTS.
    """
    try:
        shas = run_git(root, "rev-list", "--no-merges", "--first-parent",
                       f"{first}..{last}")
    except DiffError as e:
        raise RepairError(str(e)) from e
    files: set[str] = set()
    reach = (*bases, *ref_bases)
    for sha in (line.strip() for line in shas.splitlines()):
        if not sha:
            continue
        # A repair commit is a DESCENDANT of the first round's head — that is
        # what distinguishes "reachable from a base" from "reachable from a base
        # BEFORE this chain started". Without the
        # descendant test, a base that has absorbed this branch drops the
        # repair's OWN commits, and the corroborating ref base could not be
        # consulted at all.
        if (not _is_ancestor(root, first, sha)
                and any(_is_ancestor(root, sha, b) for b in reach)):
            continue  # already on the base branch — not this repair's work
        try:
            out = run_git(root, "diff-tree", "--no-commit-id", *_NUL_PATHS,
                          "-r", sha)
        except DiffError as e:
            raise RepairError(str(e)) from e
        files.update(_paths(out))
    # The file subtraction measures from the FIRST round's recorded base to
    # every other base. A resolved ref joins it only when it has NOT absorbed
    # this branch: `bases[0]...an absorbed base` contains the branch's own work,
    # so subtracting it would delete the repair's paths wholesale — which is
    # exactly the limit a RECORDED absorbed base still imposes, and not one a
    # corroborating ref should add.
    for other in (*bases[1:], *(b for b in ref_bases
                                if not _is_ancestor(root, first, b))):
        # ...but only where the branch actually CARRIES that base's bytes.
        # `moved` is every path the base branch touched between the recorded
        # bases, and subtracting all of it would remove the repair's OWN work
        # whenever a sibling PR happened to touch the same file, putting a
        # repair-written file in `original`, which is the revert clause's
        # premise. A path holding a version
        # the BASE BRANCH put there is the base branch's work whatever route it
        # took here (merge, fast-forward, squash, cherry-pick); a path holding
        # bytes the base branch never had is this branch's, and `git show
        # --name-only <repair sha>` is what said so.
        for path in sorted(set(_changed(root, bases[0], other)) & files):
            if _carries_base_bytes(root, path, bases[0], other, last):
                files.discard(path)
    return tuple(sorted(files))


def _round_stated(entry: dict, what: str) -> int | None:
    """The round `entry` states, or None when it states none.

    One reader for the two places a `round` label reaches this module — a
    finding in the payload `classify` judges, and a reviewer entry in a
    COMMITTED shard `round_count` counts — because the refusal is the same
    policy in both: a stated value in a shape warden does not read is not a
    missing one.
    """
    value = entry.get("round")
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 1:
            raise RepairError(
                f"{what} states round {value}, and rounds start at 1 — the "
                "first pass over the diff is round 1. A payload numbering its "
                "rounds from 0 declares `1` for its SECOND round, which would "
                "clear a chain of one round and reproduce the widened range "
                "this check exists to refuse. The "
                "attestation schema's floor is `minimum: 1`, and this command "
                "never runs that schema, so the floor is asserted here")
        return value
    raise RepairError(
        f"{what} states round {value!r}, which is not a whole number as "
        "warden reads one (2, not 2.0 or \"2\"). Refused rather than read as "
        "absent: a stated value in the wrong shape is not a missing one")


def _declared_round(finding: dict) -> int | None:
    """The round a finding declares, or None when it declares none.

    Absent is the only way to get None: a legacy payload (from a pin whose
    findings carry no `lens`/`round` fields) declares nothing about the chain
    and is measured from the round directories alone. A STATED round in a shape warden does
    not read is refused rather than treated as absent — `2.0` and `"2"` are
    the two spellings `attest._round_of` refuses for the same reason, and
    reading either as "no round" would let the payload's claim skip the
    comparison below.
    """
    return _round_stated(finding, "a finding")


def _declared_rounds(findings: list[dict], minted: int) -> None:
    """Refuse a chain that carries fewer rounds than the payload declares.

    An unminted round is invisible to every other check here — it writes no
    manifest — so the payload is the only witness that it ran.
    The highest round any finding is attributed to is how many rounds the
    review says it ran; fewer round directories than that means at least one
    was never minted, or was minted and not passed. Either way the ranges
    below would be measured against a chain that does not start where the
    review did, so this refuses (exit 2) rather than reporting a verdict off
    the wrong range. A 2 is never a stop condition met.
    """
    declared = [r for r in (_declared_round(f) for f in findings)
                if r is not None]
    if not declared:
        return
    highest = max(declared)
    if highest <= minted:
        return
    seats = (f"{minted} round directory was given"
             if minted == 1 else f"{minted} round directories were given")
    raise RepairError(
        f"the findings declare round {highest}, but {seats} — at least one "
        "round was never minted, or was not passed here. An unminted round "
        "is invisible to this command: its repair commit sits inside what is "
        "then measured as the original diff, so a file the repair wrote is "
        "attributed to the change and counts against the cap. "
        "Mint every round with `warden round new`, keep every round "
        "directory, and pass them all")


def _commits(root: Path, base: str, head: str) -> int:
    try:
        out = run_git(root, "rev-list", "--count", f"{base}..{head}")
    except DiffError as e:
        raise RepairError(str(e)) from e
    return int(out.strip() or 0)


def _rank(finding: dict) -> int:
    sev = finding.get("severity")
    return _SEVERITY_RANK.get(sev, _UNRANKED) if isinstance(sev, str) else _UNRANKED


def _is_open(finding: dict) -> bool:
    return str(finding.get("status") or "") not in CLOSED_STATUSES


_OID_HEADER = re.compile(rb"\A[0-9a-f]{40,64} (blob|tree|commit|tag) (\d+)\Z")
_LINK_HEADER = re.compile(rb"\A(symlink|dangling|loop|notdir) (\d+)\Z")
_LINK_REFUSAL = {
    "symlink": "is a symlink whose target leaves the repository",
    "dangling": "is a symlink whose target does not exist",
    "loop": "is a symlink that loops",
    "notdir": "resolves through a symlink to a path under a file",
}

# How many symlinks the walk to the repository follows before calling it a
# loop — the kernel's own bound (MAXSYMLINKS on Linux).
_MAX_LINK_HOPS = 40


def _within(path: str, top: str) -> bool:
    return path == top or path.startswith(top.rstrip("/") + "/")


class _AtHead:
    """A repo's classify inputs, read out of one commit — never the working
    tree, and never through a file on disk: every read is `git cat-file` or
    `git ls-tree` into memory, so there is nothing to clean up and nowhere
    outside a scratch directory a read could write.

    The rules for every repo layout, each a refusal (exit 2) where it is not
    a read:

    - A repo ENROLLED BELOW THE GIT ROOT is read through its prefix within git
      (`git rev-parse --show-prefix` from the directory holding repo.yaml):
      repo.yaml is `<head>:<prefix>repo.yaml`, and the rules dir is resolved
      against that prefix, exactly as the working tree resolves it against
      the repo root.
    - A path that RESOLVES OUTSIDE THE REPOSITORY — a rules_dir of `../x`,
      or an absolute one elsewhere — is refused, naming it. "The repository"
      is the git tree the round head belongs to; nothing outside it exists
      at a commit.
    - An ABSOLUTE path is resolved on disk only up to where it enters the
      repository (so the repo's own rules dir spelled through a symlinked
      parent is inside it); the rest is resolved at the head through git,
      exactly as a relative path is, so an uncommitted retarget of an
      in-repo symlink changes nothing.
    - A path IN A GIT SUBMODULE — the path itself, or one of its own parent
      folders, is a gitlink at the head — is refused, naming that
      submodule: git reports it `missing`, which would read as "declares
      nothing". A submodule beside the path is not on it and refuses
      nothing.
    - A COMMITTED SYMLINK — a rule file, the rules dir itself, repo.yaml —
      is FOLLOWED when its target lies inside the repository's tree at the
      head, which is what the working-tree loader does on disk. A symlink
      whose target leaves the repository, does not exist there, or loops is
      refused, naming the path. git resolves the link inside the commit's
      tree (`cat-file --follow-symlinks`); the target is never looked up on
      disk.
    """

    def __init__(self, root: Path, head: str):
        self.root = root
        self.head = head
        try:
            self.prefix = run_git(root, "rev-parse", "--show-prefix").rstrip("\n")
        except DiffError as e:
            raise RepairError(
                f"{root}: git cannot say where this repo sits in its "
                f"repository, so nothing can be read at the round head ({e})"
            ) from e

    def at(self, path: str) -> str:
        return f"{self.head[:12]}:{path or '.'}"

    def path(self, declared: str | Path, what: str) -> str:
        """`declared` — relative to the repo root, or absolute — as a path in
        the head's tree, where "" is the tree's top."""
        text = str(declared)
        if os.path.isabs(text):
            from_top = self._from_top(text, what)
            joined = ".." if from_top is None else posixpath.normpath(from_top)
        else:
            joined = posixpath.normpath(
                posixpath.join(self.prefix, text.replace(os.sep, "/")))
        if joined == ".." or joined.startswith("../"):
            raise RepairError(
                f"{what} {text!r} resolves outside the repository, so it has "
                f"no content at the round head {self.head[:12]} — refused, "
                "rather than read off the disk")
        return "" if joined == "." else joined

    def _from_top(self, text: str, what: str) -> str | None:
        """The absolute path `text` as a path from the top of the git tree,
        or None when it lies outside it.

        Only the part of the path OUTSIDE the repository is resolved on disk:
        symlinks are followed one component at a time until the walk enters
        the repository's (resolved) top, and every component from there on is
        left for git to resolve at the head, as a relative rules_dir is. An
        in-repo symlink's working-tree target is never consulted, so an
        uncommitted retarget of it changes nothing."""
        top = os.path.realpath(os.fsdecode(
            self._git(["rev-parse", "--show-toplevel"]).rstrip(b"\n")))
        pending = [c for c in text.split("/") if c not in ("", ".")]
        cur, hops = "/", 0
        # A `..` right where the walk enters climbs a real directory, so it
        # is still walked; past the next component it is lexical, as it is
        # in a relative rules_dir.
        while pending and (not _within(cur, top) or pending[0] == ".."):
            comp = pending.pop(0)
            if comp == "..":
                cur = os.path.dirname(cur)
                continue
            nxt = os.path.join(cur, comp)
            if not os.path.islink(nxt):
                cur = nxt
                continue
            hops += 1
            if hops > _MAX_LINK_HOPS:
                raise RepairError(
                    f"{what} {text!r} loops through symlinks before it "
                    "reaches the repository — refused")
            target = os.readlink(nxt)
            pending[:0] = [c for c in target.split("/") if c not in ("", ".")]
            if target.startswith("/"):
                cur = "/"
        if not _within(cur, top):
            return None
        rest = os.path.relpath(cur, top)
        return "/".join(([] if rest == "." else [rest]) + pending) or "."

    def _git(self, args: list[str], data: bytes | None = None) -> bytes:
        try:
            proc = subprocess.run(["git", *args], cwd=self.root, input=data,
                                  capture_output=True, env=git_env())
        except FileNotFoundError as e:
            raise RepairError("git executable not found") from e
        if proc.returncode != 0:
            raise RepairError(
                f"git {' '.join(args)} failed: "
                f"{proc.stderr.decode(errors='replace').strip()}")
        return proc.stdout

    def objects(self, paths: list[str]
                ) -> list[tuple[str, str, bytes] | None]:
        """(kind, oid, content) for each path at the head, symlinks followed
        within the tree, or None where the path is absent. A symlink the rule
        above does not follow is refused."""
        query = b""
        for p in paths:
            raw = os.fsencode(p)
            if b"\n" in raw:
                raise RepairError(f"{p!r}: a path holding a newline cannot be "
                                  "read at the round head")
            query += self.head.encode() + b":" + raw + b"\n"
        out = self._git(["cat-file", "--batch", "--follow-symlinks"], query)
        pos, found = 0, []
        for p in paths:
            nl = out.find(b"\n", pos)
            if nl < 0:
                raise RepairError(f"git cat-file returned nothing for "
                                  f"{self.at(p)}")
            header, pos = out[pos:nl], nl + 1
            if m := _OID_HEADER.match(header):
                size = int(m[2])
                found.append((m[1].decode(), header.split(b" ")[0].decode(),
                              out[pos:pos + size]))
                pos += size + 1
            elif m := _LINK_HEADER.match(header):
                size = int(m[2])
                target = out[pos:pos + size].decode(errors="replace")
                raise RepairError(
                    f"{p} {_LINK_REFUSAL[m[1].decode()]} at the round head "
                    f"{self.head[:12]} ({target}) — a committed symlink is "
                    "followed only to a target inside the repository's tree, "
                    "so this one is refused, not read off the disk")
            elif header.endswith(b" missing"):
                self._refuse_gitlink(p)
                found.append(None)
            else:
                raise RepairError(f"git cat-file could not read {self.at(p)}: "
                                  f"{header.decode(errors='replace')}")
        return found

    def _refuse_gitlink(self, path: str) -> None:
        """git reports a path at or inside a SUBMODULE as `missing`, and
        absence reads as "declares nothing". A gitlink is never absent: when
        `path` itself or one of its own parent folders is one, the read is
        refused, naming that submodule. A submodule BESIDE the path is not
        on it and refuses nothing. The submodule's own commit is not read."""
        parts = path.split("/") if path else []
        ancestors = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
        if not ancestors:
            return
        listing = self._git(["--literal-pathspecs", "ls-tree", "--full-tree",
                             "-z", self.head, "--", *ancestors])
        for entry in listing.split(b"\0"):
            if not entry.startswith(b"160000 "):
                continue
            link = os.fsdecode(entry.split(b"\t", 1)[1])
            # ls-tree on a parent folder lists that folder's other entries
            # too: only a gitlink that IS the path or one of its parents
            # holds it.
            if link in ancestors:
                raise RepairError(
                    f"{self.at(path)} lies in the git submodule {link} — its "
                    "content is not part of the round head's tree, so it is "
                    "refused rather than read as absent")

    def files(self, path: str, suffix: str, what: str, *, strict: bool
              ) -> list[tuple[str, bytes]] | None:
        """(name, bytes) for every entry of the directory at `path` whose
        name ends in `suffix`, in name order; None when the directory is
        absent — or, unless `strict`, is not a directory. The working tree's
        `rules_version` makes the same two distinctions."""
        (obj,) = self.objects([path])
        if obj is None:
            return None
        kind, oid, _ = obj
        if kind != "tree":
            if not strict:
                return None
            raise RepairError(f"{what} {self.at(path)} is a {kind}, not a "
                              "directory")
        # --full-tree: run from a repo enrolled below the git root, ls-tree
        # would otherwise list only what lies under the cwd's prefix.
        listing = self._git(["ls-tree", "--full-tree", "-z", "--name-only",
                             oid])
        names = sorted(os.fsdecode(n) for n in listing.split(b"\0")
                       if n.endswith(suffix.encode()))
        full = [posixpath.join(path, n) for n in names]
        out = []
        for name, p, got in zip(names, full, self.objects(full)):
            if got is None or got[0] != "blob":
                raise RepairError(
                    f"{self.at(p)} is {'absent' if got is None else 'a ' + got[0]}"
                    f" where {what} holds files")
            out.append((name, got[2]))
        return out

    def repo_yaml(self) -> bytes | None:
        path = self.path(config_mod.CONFIG_NAME, config_mod.CONFIG_NAME)
        (obj,) = self.objects([path])
        if obj is None:
            return None
        if obj[0] != "blob":
            raise RepairError(f"{self.at(path)} is a {obj[0]}, not a file")
        return obj[2]

    def review(self) -> config_mod.ReviewSettings:
        """repo.yaml's review settings as committed at the head, validated
        exactly as `config.load` validates the working tree's."""
        where = self.at(self.prefix + config_mod.CONFIG_NAME)
        data = self.repo_yaml()
        if data is None:
            raise RepairError(
                f"{where}: repo.yaml is not committed at the round head, so "
                "the rules dir and blocking severities the round is judged "
                "under cannot be read")
        try:
            return config_mod.parse(data, self.root, where).review
        except (config_mod.ConfigError, UnicodeDecodeError) as e:
            raise RepairError(f"{where}: repo.yaml at the round head cannot "
                              f"be read as a config ({e})") from e


def judges_at(tree: _AtHead, rules_dir: str | Path
              ) -> tuple[dict[str, str], str]:
    """(rule id -> what the rule judges, rules_version) for the rules in
    `rules_dir` as committed at the round head.

    A directory that is absent or holds no rule files declares nothing, so
    every finding under a rule id reads as behaviour (`kind_of`). A ruleset
    that does not load raises
    `RepairError`: classifying without it would count or skip findings on a
    guess. The version is composed by `rules.version_from`, the same function
    the working tree's `rules_version` feeds. The repo's surfaces are read at
    the head: the rule files, the `checkers` dir beside the rules dir, and
    repo.yaml's review subtree. The platform's two, the findings schema and
    `warden/mechanical.py`, are read from the running warden package, not the
    head.
    """
    path = tree.path(rules_dir, "review.rules_dir")
    if not path:
        raise RepairError(
            f"review.rules_dir {str(rules_dir)!r} is the top of the "
            "repository, so the checkers dir hashed beside it lies outside "
            "the repository — refused")
    checkers_dir = posixpath.join(posixpath.dirname(path), "checkers")
    try:
        rules = tree.files(path, ".md", "the rules dir", strict=True)
        checkers = tree.files(checkers_dir, ".py", "the checkers dir",
                              strict=False) or []

        def surface():
            data = tree.repo_yaml()
            return (None if data is None
                    else rules_mod.enforcement_surface_of(data))
        version = rules_mod.version_from(rules or [], checkers, surface)
        if not rules:
            return {}, version
        loaded = rules_mod.rules_from_bytes(
            [(Path(path) / name, data) for name, data in rules],
            tree.at(path))
    except rules_mod.RuleError as e:
        raise RepairError(
            f"the ruleset in {tree.at(path)} could not be loaded, so no "
            f"finding's kind can be read ({e})") from e
    return {r.id: r.judges for r in loaded}, version


def _below_blocking(finding: dict, blocking: tuple[str, ...] | None) -> bool:
    """Is the finding's severity known and below every blocking severity?
    False when the blocking severities are unknown or none are declared."""
    if not blocking:
        return False
    ranks = [_SEVERITY_RANK.get(b) for b in blocking]
    if any(r is None for r in ranks):
        return False
    return _rank(finding) < min(ranks)


def kind_of(finding: dict, judges: dict[str, str]) -> str:
    """What one finding judges, `behaviour` or `wording` — the one place a
    record's kind is resolved, so every reader of it agrees.

    A rule_id a declared rule answers judges what that rule's `judges:` says
    (`judges` maps rule id to it, read at the round head); the record's own
    `judges` field does not override a rule. An `unmapped:<slug>` finding
    names no rule, so nothing says what it judges: it is wording when the
    field is absent or says `wording`, and behaviour on any other value —
    `behaviour` as declared, or a value the schema would refuse, which reads
    as the counting kind here because `round classify` validates no schema
    and a misspelling must not end the loop. A rule_id that is neither —
    unresolvable, absent, not a string — is behaviour, the counting kind.
    """
    rule_id = finding.get("rule_id")
    if not isinstance(rule_id, str):
        return "behaviour"
    if rule_id in judges:
        return judges[rule_id]
    if rule_id.startswith(attest_mod.UNMAPPED_PREFIX):
        return ("wording" if finding.get("judges", "wording") == "wording"
                else "behaviour")
    return "behaviour"


def _uncounted_wording(finding: dict, judges: dict[str, str],
                       blocking: tuple[str, ...] | None) -> bool:
    return (kind_of(finding, judges) == "wording"
            and _below_blocking(finding, blocking))


def classify(root: Path, round_dirs: list[Path], findings: list[dict],
             rules_dir: Path | str | None = None,
             blocking: tuple[str, ...] | None = None) -> Report:
    """Classify the open findings over the round chain.

    `rules_dir` is the repo's rules dir, relative to `root` or absolute, read
    AT THE ROUND HEAD (`judges_at`); without it every finding under a rule id
    is behaviour, and an `unmapped:` finding is what `kind_of` reads off it.
    `blocking` is the repo's blocking severities; without them every wording
    finding counts. `classify_at_head` reads both from repo.yaml at the head,
    which is what the command does.
    """
    chain = _chain(root, round_dirs)
    judges, version = (judges_at(_AtHead(root, chain[1][-1]), rules_dir)
                       if rules_dir is not None else ({}, ""))
    return _classified(root, round_dirs, chain, findings, judges, version,
                       blocking)


def classify_at_head(root: Path, round_dirs: list[Path],
                     findings: list[dict]) -> Report:
    """`classify` under the policy committed at the round head: repo.yaml's
    `review.rules_dir` and `review.blocking_severities`, and the rules in
    that dir, all read at the latest round's head."""
    chain = _chain(root, round_dirs)
    tree = _AtHead(root, chain[1][-1])
    review = tree.review()
    judges, version = judges_at(tree, review.rules_dir)
    return _classified(root, round_dirs, chain, findings, judges, version,
                       review.blocking_severities)


def _classified(root: Path, round_dirs: list[Path], chain, findings: list[dict],
                judges: dict[str, str], version: str,
                blocking: tuple[str, ...] | None) -> Report:
    base, heads, bases, ref_bases = chain
    # The kind each named rule id resolved to. An unmapped id's kind is read
    # per record, so an id whose records disagree is behaviour: one of its
    # records is the counting kind.
    kinds: dict[str, str] = {}
    for f in findings:
        rid = f.get("rule_id")
        if isinstance(rid, str) and rid:
            kind = kind_of(f, judges)
            kinds[rid] = ("behaviour" if kinds.get(rid) == "behaviour"
                          else kind)
    # Counted by RESOLVED path: the same round passed twice is one round, and
    # two rounds minted at one head are two (they are two dispatches of the
    # crew, and `_chain` folds them to one point on the chain by design).
    _declared_rounds(findings, len({Path(d).resolve() for d in round_dirs}))
    original = _changed(root, base, heads[0])
    repair = (_repair_files(root, heads[0], heads[-1], bases, ref_bases)
              if len(heads) > 1 else ())
    orig_set, rep_set = set(original), set(repair)
    rows: list[tuple[dict, str]] = []
    wording: list[dict] = []
    on_original: list[str] = []
    above_low: list[str] = []
    for f in findings:
        if not _is_open(f):
            continue
        if _uncounted_wording(f, judges, blocking):
            wording.append(f)
            continue
        file = f.get("file")
        file = file.strip() if isinstance(file, str) else ""
        if file and file in orig_set and file in rep_set:
            cls = "both"
        elif file and file in rep_set:
            cls = "repair"
        else:
            cls = "original"  # the change's, or unclassifiable — fail-closed
        rows.append((f, cls))
        if cls == "original":
            on_original.append(file or "(no file)")
        if _rank(f) > _SEVERITY_RANK["LOW"]:
            above_low.append(f"{file or '(no file)'} {f.get('severity')!s}")
    reasons: list[str] = []
    if on_original:
        reasons.append(f"{len(on_original)} open finding(s) on the original "
                       f"diff: {', '.join(on_original)}")
    if above_low:
        reasons.append(f"{len(above_low)} open finding(s) above LOW: "
                       f"{', '.join(above_low)}")
    if not rows:
        verdict = VERDICT_CLEAN
    elif reasons:
        verdict = VERDICT_COUNTS
    else:
        verdict = VERDICT_STOP
        reasons.append(f"every open finding is LOW and cites a file the repair "
                       f"range wrote ({len(rows)}); the crew is reviewing its "
                       "own scaffolding")
    return Report(base_sha=base, heads=heads,
                  original_commits=_commits(root, base, heads[0]),
                  original_files=original,
                  repair_files=repair, rows=tuple(rows), verdict=verdict,
                  reasons=tuple(reasons), wording=tuple(wording),
                  rules_version=version, rule_kinds=tuple(kinds.items()))


def round_at(root: Path, round_dirs: list[Path], head: str) -> Path | None:
    """The given round directory minted for `head`, or None when none was.

    The verdict record goes in the round the verdict is ABOUT, so the caller
    resolves the round by head rather than by argument order — `--round` takes
    the directories in any order by design, and the chain is proved from the
    shas.
    """
    for d in round_dirs:
        try:
            if _resolve_commit(root, _manifest(d)["head_sha"], "head_sha") == head:
                return d
        except RepairError:
            continue
    return None


@dataclass(frozen=True)
class HeadReading:
    """One `--prior-head`'s own reading, whole.

    A named type rather than a widening tuple because every field here is
    load-bearing SEPARATELY, and the one that got dropped when this was a
    3-tuple was `closure` — so a prior head's exempted shard was printed
    under "Rounds counted" beside a sentence saying none was counted
    (round-2 F-8). A reading that carries its number without the shards
    behind it, or its shards without which of them were exempt, cannot be
    reported truthfully by any caller.
    """

    head: str
    # the highest round its own base..head records, closure shards included
    rounds: int
    # (path, highest round) per counted shard of THAT range, in range order
    shards: tuple[tuple[str, int], ...] = ()
    # which of those paths are a closure round's, by the same test the range
    # uses — the exemption is per shard, so it travels with the shard
    closure: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoundCount:
    """Which round is next for a base..head, and BOTH counts behind it.

    The two are never folded into one another. `number` is the higher of them
    plus one, and `disagreement` says so in words whenever they differ: a
    caller that prints the number without the report has hidden exactly the
    evidence that says the committed chain and this laptop's chain disagree.
    """

    number: int
    # prior rounds the COMMITTED shards added in base..head record
    from_shards: int
    # prior rounds the local, gitignored rounds directory holds
    from_rounds_dir: int
    # (path, highest round) per counted shard, in range order
    shards: tuple[tuple[str, int], ...] = ()
    # shards added in the range whose reviewers carry no `round` at all
    unrecorded: tuple[str, ...] = ()
    disagreement: str | None = None
    # counted shards that are a CLOSURE round's (`closure_verification`).
    # They raise the floor like any other and are named apart because the
    # declared cap does not bound them — see `capped_rounds`.
    closure: tuple[str, ...] = ()
    # The highest round a NON-closure shard records, anywhere this count
    # looked: the range AND every `prior_heads` reading. This is the number
    # a declared cap bounds, and the one `warden round count` gates on —
    # `from_shards` is the numbering floor and includes the closure round,
    # which is terminal and spends no budget.
    capped_rounds: int = 0
    # the highest reading from a prior head's own base..prior_head; it can
    # only RAISE the floor, never lower it, exactly as the rounds directory
    from_prior_heads: int = 0
    # one `HeadReading` per head supplied from OUTSIDE this branch's current
    # history — the PR's earlier pushed heads, the witness a rewrite cannot
    # take with it. Each carries its own shards and its own
    # closure paths, because they are the only thing a reader of a refusal
    # can go and look at: when the verdict comes from a prior head, `shards`
    # below is empty, and naming it alone printed "Rounds counted: none"
    # beside "records 3 round(s)" (round-1 F-5).
    prior_heads: tuple[HeadReading, ...] = ()
    # set when a prior head's committed shards record MORE rounds than this
    # branch's current history does: the rewrite report
    rewrite: str | None = None
    # shard paths readable at HEAD that no counted commit added, and that the
    # base branch does not already carry. Reported, never counted — the count
    # stays keyed on the ADD, and `_uncounted_shards` argues why.
    uncounted: tuple[str, ...] = ()

    @property
    def notes(self) -> tuple[str, ...]:
        """Every reading behind this number that a caller MUST print.

        Not just the disagreement. A range whose only committed shards record
        no round at all is the shape where the two counts AGREE at zero and
        the number reads as a clean round 1 over evidence that a review
        already happened — so the unrecorded shards are reported on their own
        line, not folded into a string that is only built when the counts
        differ.
        """
        out: list[str] = []
        if self.disagreement is not None:
            out.append(self.disagreement)
        if self.rewrite is not None:
            out.append(self.rewrite)
        if self.unrecorded:
            out.append(
                f"{len(self.unrecorded)} shard(s) committed in this range "
                "record no round in any roster entry, so they raise no "
                f"floor: {', '.join(self.unrecorded)}. A legacy shard "
                "predating `reviewers[].round` reads this way, and so does a "
                "roster written without it — the count cannot tell them "
                "apart, and neither can this line")
        if self.uncounted:
            out.append(
                f"{len(self.uncounted)} shard(s) are READABLE AT HEAD that no "
                "counted commit added and whose bytes the base branch does "
                f"not carry: {', '.join(self.uncounted)}. They raise no floor "
                "here, because the count is keyed on the ADD and walks "
                "first-parent, so any round they record is outside this "
                "number. WHY the count did not see the ADD is not something "
                "this reading establishes, and there are three ways in: an "
                "ADD on a side branch the change merged in, an ADD made by a "
                "merge commit itself (which is ON the walked line, and which "
                "`git diff-tree` reports no diff for), and a shard re-added "
                "at a path the base branch also carries. A branch's own "
                "honest commits read the same way whenever the head being "
                "counted is a merge ref whose first parent is the base tip. "
                "The shards are in the tree in every one of those cases, and "
                "a reader can open them")
        return tuple(out)


# The shard directory is memory's to name: imported, never retyped, so this
# counter cannot go on counting an empty path the day that one moves.
_SHARD_DIR = f".warden/{memory_mod.MEMORY_DIRNAME}/{memory_mod.SHARD_SUBDIR}"


def _added_shards(root: Path, base: str, head: str) -> list[tuple[str, str]]:
    """(commit, path) for every shard file ADDED by a commit in `base..head`.

    ADDED BY A COMMIT, never "present in the head tree and absent from the
    base tree", and never the `sha` the shard names inside itself. Both of
    the other two re-break on the move this exists to survive: a rebased
    branch's shards still point at the PRE-rebase commits, so a sha test
    finds none of them in the new range, and a tree comparison against a base
    branch that has itself absorbed the shard finds nothing added. The ADD is
    carried by the rewritten commit, whatever its sha becomes.

    FIRST-PARENT, for the reason `_repair_files` walks that way: a commit
    the branch MERGED in is another line's work, and its shards are another
    change's rounds. Walking every commit in the range counted them — an
    ordinary `git merge sibling-branch` whose tip carried one shard numbered
    this change's FIRST review as round 2, and round 2's declared crew is one
    reviewer where round 1's is two, which is the dodge the round binding
    exists to close, reached by accident.
    Merge commits themselves contribute nothing either way: `diff-tree` shows
    no diff for a merge unless asked.

    It errs, and in the direction that costs review rather than buys it: a
    shard whose only ADD sits off this branch's first-parent line raises no
    floor here, so a builder who arranges their evidence onto a side branch
    and merges it gets the lower count. A lower count holds the next round to
    an EARLIER round's larger crew, while the over-count it replaces handed a
    first review round 2's single reviewer — which is why the walk stays
    first-parent. A merge commit's own tree is a second such place, and it is
    cheaper than the first: `git merge --no-ff --no-commit`, write the shard
    into the index, commit, and the ADD belongs to no commit this walk reads.
    The silence is what was wrong with both, and `_uncounted_shards` is what
    ends it: the shard is readable at head in either shape, so it is REPORTED
    rather than counted, and the count stays keyed on the ADD.
    """
    try:
        commits = run_git(root, "rev-list", "--first-parent",
                          f"{base}..{head}").split()
    except DiffError as e:
        raise RepairError(
            f"the commits in {base[:12]}..{head[:12]} could not be listed "
            f"({e}), so the rounds the committed shards record cannot be "
            "counted and the round about to be minted cannot be numbered "
            "from them") from e
    return [(commit, path) for commit in commits
            for path in _shard_adds(root, commit)]


def _shard_adds(root: Path, commit: str) -> list[str]:
    """Shard paths ADDED by one commit.

    Lifted out of `_added_shards`' loop so the one `diff-tree` spelling — the
    flags, `--root`, and the `.json` suffix filter — has one home. A second
    copy of it would be free to drift, and every reading keyed on the ADD has
    to mean the same thing by it.
    """
    try:
        # --root: without it a ROOT commit diffs against nothing and reports
        # no adds at all, so a shard committed in a repository's first commit
        # would be invisible to this count.
        listing = run_git(root, "diff-tree", "--root", "--no-commit-id", "-r",
                          *_NUL_PATHS, "--diff-filter=A", commit,
                          "--", _SHARD_DIR)
    except DiffError as e:
        raise RepairError(
            f"{commit[:12]}: the files it added under {_SHARD_DIR} could not "
            f"be listed ({e}), so the committed review chain cannot be read"
        ) from e
    return [p for p in _paths(listing) if p.endswith(".json")]


def _shard_blobs(root: Path, rev: str) -> dict[str, str]:
    """`path -> blob sha` for every file under the shard dir at `rev`.

    The BLOB, not just the path, because `_uncounted_shards` subtracts one
    tree from another and a path is not an identity: a shard re-added at a
    name the base branch also uses is a different shard with the same
    spelling (round 2).
    """
    try:
        raw = run_git(root, "ls-tree", "-r", "-z", rev, "--", _SHARD_DIR)
    except DiffError as e:
        raise RepairError(
            f"the shards present at {rev[:12]} could not be listed ({e}), so "
            "a shard this count cannot see cannot be told from one there is "
            "none of") from e
    out: dict[str, str] = {}
    for entry in _paths(raw):
        # `<mode> SP <type> SP <sha> TAB <path>` — the path is after the TAB
        # and is NOT quoted under `-z`, which is why this reads that way
        # rather than splitting on whitespace.
        meta, _, path = entry.partition("\t")
        fields = meta.split()
        if path and len(fields) == 3:
            out[path] = fields[2]
    return out


def _uncounted_shards(root: Path, base: str, head: str,
                      counted: "set[str]") -> tuple[str, ...]:
    """Shard paths READABLE AT `head` that no counted commit added.

    THE HOLE THIS REPORTS. `_added_shards` walks `--first-parent`, for the
    reason stated there, and the cost of that walk is a shard whose ADD sits
    somewhere the walk does not go: on a side branch the feature merged in, or
    in the merge commit's own tree where `diff-tree` reports no diff at all.
    Either way it raises no floor, so a builder who arranges the evidence
    commits out of the walk's path gets the lower count and a fresh repair
    budget. Changing the walk is not the fix — the full walk was what shipped
    before, and it was worse: an ordinary `git merge sibling-branch` numbered
    this change's FIRST review as round 2, whose declared crew is one reviewer
    where round 1's is two.

    So this REPORTS and never counts, and the count stays keyed on the ADD.

    WHY IT IS A TREE COMPARISON AND `_added_shards` MUST NOT BE. That
    function's docstring refuses "present at head and absent from base"
    outright, and is right to: a base branch that has itself absorbed the
    shard makes the comparison find nothing, which would silently lower a
    FLOOR. Here the same blindness is the requirement. This is a report, it
    lowers nothing, and shards the base branch already carries are precisely
    the ones that must not be reported — a branch that merges `origin/main`
    mid-flight brings other changes' shards in by the hundred, and they are
    not this change's rounds. `base`'s own tree is what subtracts them, so
    the quiet case is quiet by construction rather than by a filter that
    could be got around.

    THE COMPARISON IS (path, BLOB), and that is the whole of round 2's F-2.
    Subtracting by path alone made every NAME the base branch carries a place
    to hide: drop one of `origin/main`'s 353 shard paths on a side branch,
    re-add it saying whatever you like, merge, and both trees hold the path so
    nothing reported it. The blob keeps the inherited shard quiet — it is
    byte-identical — and names the one whose bytes this branch changed.

    `counted` is the path set `_added_shards` already attributed to a commit
    on the walked line, subtracted so the report can never name a shard that
    DID raise the floor. Without it a path added both on the line and on a
    side branch was reported under a sentence saying no counted commit added
    it, which was false of that path (round 1).

    WHAT IT DOES NOT ESTABLISH, and the note must not claim: WHY a shard is
    here. A branch whose head is a merge ref — `refs/pull/N/merge`, whose
    first parent is the base tip — has its own honest commits off the walked
    line, and every shard it committed reads exactly like an arranged one.
    `warden round count` takes the PR head from the event rather than the
    merge ref for that reason, and the note below still describes rather than
    accuses, because this function cannot tell the two apart.

    Raises RepairError for the reason `_added_shards` does: a tree warden
    cannot read is not one with no shards in it.
    """
    trees = {rev: _shard_blobs(root, rev) for rev in (head, base)}
    # (path, blob), never the path alone. Subtracting by path made a shard the
    # base branch happens to carry a NAME for invisible whatever it now says:
    # delete `.warden/memory/attest/<any of the 353 on main>.json` on a side
    # branch, re-add it stating round 7, merge, and the path is in both trees
    # so nothing reported it (round 2). Comparing the BLOB keeps the quiet
    # case quiet — a shard the branch merely inherited is byte-identical — and
    # reports the one whose bytes the branch changed.
    return tuple(sorted(
        path for (path, blob) in trees[head].items()
        if path.endswith(".json") and path not in counted
        and trees[base].get(path) != blob))


def _shard_rounds(root: Path, base: str, head: str
                  ) -> tuple[tuple[tuple[str, int], ...], tuple[str, ...],
                             tuple[str, ...]]:
    """The rounds the shards committed in `base..head` record, and the rest.

    Returns the counted `(path, round)` pairs in range order, the paths whose
    roster records no round at all, and the paths whose shard is a CLOSURE
    round's — the terminal, non-budgeted round minted past the cap, marked by
    the `closure_verification` field `attest write` stamps only after proving
    the payload raised nothing new. A closure shard still raises the FLOOR
    (the next round is numbered above it), and it is named apart because the
    declared cap does not bound it: `graph.yaml` review.closure declares that
    round, so counting its number against the budget would fail every branch
    that used the closure-round seam.

    Each shard is read AT THE COMMIT THAT ADDED IT, not at the head: a shard
    deleted later in the range is still evidence that the round ran, and
    reading it at the head would let the delete un-count it. The value read
    is the highest `reviewers[].round` the shard states — the field 161 of
    this repo's 302 committed shards already carry, which nothing read until
    now, so promoting it to load-bearing rewrites no shard. A shard whose
    reviewers state no round at all reads as UNRECORDED and lowers nothing:
    the 141 legacy shards are the same precedent as an absent `verdict`.

    Refuses (RepairError) a shard it cannot read or parse. A shard warden
    cannot read is not an absent round, and numbering past it would hand the
    branch the fresh budget this whole derivation exists to refuse.
    """
    highest: dict[str, int] = {}
    order: list[str] = []
    unrecorded: list[str] = []
    closure: list[str] = []
    for commit, path in _added_shards(root, base, head):
        what = f"{path} (added by {commit[:12]})"
        try:
            text = run_git(root, "show", f"{commit}:{path}")
        except DiffError as e:
            raise RepairError(f"{what}: this shard could not be read ({e}), "
                              "so the rounds it records cannot be counted") from e
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as e:
            raise RepairError(f"{what}: this shard is not JSON warden can "
                              f"read ({e}), so the rounds it records cannot "
                              "be counted") from e
        if not isinstance(doc, dict):
            raise RepairError(f"{what}: this shard is not a JSON object, so "
                              "it names no reviewers to count rounds from")
        if "reviewers" not in doc:
            # ABSENT is the legacy shape, and the only tolerated one: 9 of
            # this repo's 302 committed shards carry no roster at all. The
            # test is on the KEY, never on the value: `doc.get("reviewers")`
            # cannot tell an absent key from one explicitly set to `null`,
            # and the schema types `reviewers` as a REQUIRED array, so a
            # stated `null` is schema-invalid and read as legacy anyway —
            # measured, and it lowered the floor in silence.
            unrecorded.append(path)
            continue
        reviewers = doc["reviewers"]
        if not isinstance(reviewers, list):
            # `null` lands HERE now, as `NoneType`, with every other
            # non-array shape.
            raise RepairError(
                f"{what}: this shard's `reviewers` is "
                f"{type(reviewers).__name__}, not the array the attestation "
                "schema requires, so the rounds it records cannot be read. "
                "Refused rather than read as a shard with no round — a stated "
                "roster in a shape warden does not read is not a missing one, "
                "and reading it as missing would lower the floor to whatever "
                "the rest of the range happens to carry")
        if not reviewers:
            # The empty array clears the isinstance test, contributes no
            # entry, and used to fall through to `unrecorded` — a roster
            # STATED as nobody, read as the legacy shape that predates the
            # field. The schema's floor is `minItems: 1`, so
            # this is refused for the reason every other schema-invalid
            # roster is: warden does not lower a floor from a shape no
            # attestation it would write can carry.
            raise RepairError(
                f"{what}: this shard's `reviewers` is the EMPTY array, where "
                "the attestation schema requires `minItems: 1` — a review "
                "with no reviewer is not a review, and no shard `attest "
                "write` produces can carry it. Refused rather than read as a "
                "shard with no round, for the reason a `reviewers` that is "
                "not an array is: reading it as missing would lower the floor "
                "to whatever the rest of the range happens to carry")
        # The VALUE, not the key's presence. The schema types this field as
        # `enum: ["verified"]` and `attest.py` reads it the same way, so a
        # shard carrying `false`, `0`, `{}` or `"raised-new"` states a
        # closure verification that did not happen — and the existence test
        # this used to be exempted it from the cap on any of them, which is
        # fail-closed's "existence checked, validity not" (round-1 F-3). The
        # field is the one that switches the cap off; the looser of two
        # readers of it is the wrong one to be.
        if doc.get("closure_verification") == attest_mod.CLOSURE_VERIFIED:
            closure.append(path)
        stated = []
        for entry in reviewers:
            if not isinstance(entry, dict):
                raise RepairError(
                    f"{what}: a `reviewers` entry is {entry!r}, not the "
                    "object the attestation schema requires — refused for "
                    "the reason a `round` in the wrong shape is, and never "
                    "skipped past: the round it may have stated is exactly "
                    "what the floor is counted from")
            n = _round_stated(entry, f"{what}: a reviewer entry")
            if n is not None:
                stated.append(n)
        if not stated:
            unrecorded.append(path)
            continue
        if path not in highest:
            order.append(path)
        highest[path] = max(highest.get(path, 0), max(stated))
    # A closure shard whose roster records no round lands in `unrecorded`
    # and raises nothing, so it is not reported as a counted exemption
    # either — the exemption only means anything about a round that counts.
    counted = set(order)
    return (tuple((p, highest[p]) for p in order), tuple(unrecorded),
            tuple(p for p in closure if p in counted))


def _rounds_from_dirs(root: Path, base: str, this_head: str) -> set[str]:
    """The prior round HEADS the local rounds directory holds for this chain.

    THE CHAIN, read with the two ancestry tests the rest of this module uses:
    a round counts when this head IS or descends from the head that round was
    minted for (so a sibling branch's rounds drop out), and when that head is
    not already reachable from the base (so the base branch's own rounds, and
    an earlier branch's merged work, drop out — `_repair_files` tells the
    change's commits from the base branch's the same way). The second test is
    reachability, never "descends from the base tip": a base branch that
    MOVED underneath the work would otherwise drop every round of the change
    itself and renumber round 3 as round 1. Heads are DEDUPED, for the reason
    `_chain` keys by head: two directories minted for one commit are one
    round of the chain, re-run, not two — and THIS head is never among the
    prior ones, which is load-bearing rather than tidy.

    What it cannot survive is a rewrite of the history it tests against: a
    rebase leaves every recorded head off this line, and this count drops to
    zero. That is why it is only HALF of `round_count`.
    """
    rounds_root = root / ".warden" / runs.OUT_DIRNAME / runs.ROUNDS_DIRNAME
    if not rounds_root.is_dir():
        return set()
    prior: set[str] = set()
    # The listing itself, inside the refusal `round_count` documents. A bare
    # `sorted(rounds_root.iterdir())` raises OSError — a rounds root behind a
    # permission wall, or replaced by something unlistable — straight past
    # `cli._cmd_round`'s `except RepairError`, so the operator reads a
    # traceback where the contract promises the exit-2 refusal. A chain warden
    # could not look at is not an empty chain: numbering from it would stamp
    # round 1 on a change that has already had rounds.
    try:
        entries = sorted(rounds_root.iterdir())
    except OSError as e:
        raise RepairError(
            f"{rounds_root} could not be listed ({e}), so the review chain "
            "cannot be read and the round about to be minted cannot be "
            "numbered from it") from e
    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            doc = runs.round_manifest_for(entry)
        except runs.RoundError as e:
            raise RepairError(
                f"{entry}: this round's identity could not be read ({e}), so "
                "the round about to be minted cannot be numbered from the "
                "chain. Remove or repair that round directory") from e
        if doc is None:
            continue          # not a round warden minted — nothing to count
        sha = doc.get("head_sha")
        if not isinstance(sha, str) or not sha:
            raise RepairError(f"{entry}: round.json names no head_sha, so it "
                              "cannot be placed on this chain")
        try:
            prior_head = _resolve_commit(root, sha, "head_sha")
        except RepairError:
            continue          # a round from another clone: on no chain here
        if prior_head == this_head or not _is_ancestor(root, prior_head,
                                                       this_head):
            # one head is one round: a directory minted for THIS commit is
            # this round re-minted, never the round before it. And a head
            # this one does not descend from is another line of history.
            continue
        if _is_ancestor(root, prior_head, base):
            continue          # already on the base branch — not this change's
        prior.add(prior_head)
    return prior


def round_count(root: Path, base_sha: str, head: str,
                prior_heads: tuple[str, ...] | list[str] = ()) -> RoundCount:
    """The round about to be minted for this base..head, from BOTH chains.

    TWO INDEPENDENT COUNTS, and the number is the higher of them plus one:

    - the COMMITTED shards added in `base..head` (`_shard_rounds`), which is
      the FLOOR. It lives in the tree, in the range, so a rebase carries it
      and CI can compute it — the round cap is a fact anything with the range
      can check, not advice from a directory on the builder's laptop;
    - the local rounds directory (`_rounds_from_dirs`), which can only RAISE
      that floor. It is the only witness to a round that has been minted and
      whose shard is not committed yet, which is every round mid-review.

    The higher wins because each count is blind in a different direction, and
    both blindnesses are silent. Taking the shards alone would number round 2
    as round 1 all through a live review; taking the directory alone is the
    defect this exists to fix — measured on a synthetic repo, the directory
    count returned 2 before a rebase and 1 after, for either base, so a
    branch at the cap bought a fresh budget by rebasing and nothing recorded
    that it had.

    A disagreement is REPORTED, never resolved away: `RoundCount.disagreement`
    names both counts, every shard counted, and which direction the gap runs.
    Higher on the SHARD side means rounds this checkout's directory does not
    have — a rebase, a fresh clone, a deleted round dir; higher on the
    DIRECTORY side is the ordinary mid-review case. Reconciling the two
    silently is how this class of bug hides, so this function refuses to.
    `notes` carries that report and every other one; a caller prints them all.

    WHAT IT STILL CANNOT PROVE, said plainly, and in the two shapes it was
    first said too loosely in (round-1 finding 3). The shard floor moves the
    count into committed evidence, and no further: the tree is the builder's
    to write, so a branch that never commits a shard has no floor. Past that,
    the two ways to drop a floor already earned are NOT alike:

    - a shard DELETED by a commit inside the range still counts, because the
      ADD is the key — and the deletion is itself a `.warden/memory/**`
      change in the reviewed diff, the surface `evidence-intact` judges.
      That one is closed by the range itself, and needs no witness;
    - a history REWRITE that drops the commit which added the shard leaves no
      `.warden/memory/**` change in the range at all. Nothing is dispatched
      to judge, nothing reports, and the count resets in silence. Measured:
      `git diff --name-status base...head` prints only the work file. The
      range cannot see it, because the range is what was rewritten.

    The two are still not one shape, and `PRIOR_HEADS` is what answers the
    second without flattening it into the first. Each is a
    head this change pushed EARLIER — the forge's record of the branch, which
    a rewrite of the branch does not reach — and its own `base..prior_head`
    is counted with the same derivation. The floor is the highest reading
    across all of them, so a drop of the shard commits no longer resets it,
    and where the drop is visible at all it is REPORTED as `rewrite`,
    naming both readings and the head that carried the higher. A prior head
    that does not resolve is REFUSED (RepairError), never skipped: a witness
    warden could not read is not a witness that agreed.

    A prior head's reading reaches `capped_rounds` — the number `warden round
    count` REFUSES on — exactly as the range's does, and that is the point
    rather than an overreach (round-1 F-2 found five surfaces claiming
    otherwise). If it only reported, dropping the commits would still buy the
    budget back: a history rewrite would reset the count. `from_shards` stays the
    RANGE's reading alone, and `disagreement` still compares that with the
    rounds directory, so neither says a word about a range it did not read.

    With no prior head supplied the count is exactly what it was, and the
    residual stands. Two limits on the witness itself, neither closed here:
    CI passes `github.event.before`, which the forge sends
    only on a `synchronize` activity type and which is ONE PUSH DEEP. The
    two are not alike, and the caller can only name the first. No `before` at
    all is a state the step can SEE, and it says so; a `before` that is
    present, fetchable and itself already rewritten is indistinguishable here
    from a witness that honestly agrees — nothing in this reading can tell
    them apart, so the second push after a drop is silent and the page says
    that rather than claiming a warning it does not raise.

    THE THIRD SHAPE, reported rather than counted: a shard readable at head
    that no counted commit added raises no floor, because the walk is
    first-parent and the count is keyed on the ADD. Both of those are
    deliberate and neither changes here — see `_added_shards` for what the
    full walk cost when it shipped. What changes is the silence: `uncounted`
    names the shard and `notes` prints it. Shards the BASE BRANCH already
    carries are subtracted, so merging the base branch mid-branch — which
    brings other changes' shards in by the hundred — does not reach this at
    all. `_uncounted_shards` has that argument, and the reason the note
    describes the shape rather than accusing anyone of arranging it.

    And the rounds root is an
    ordinary directory under `.warden/out/`, so anything that can write there
    can still add a round dir and RAISE this number, which buys a later
    round's shorter crew. That is the platform's standing trust boundary
    (`verify_roster`: "a caller that writes plausible files itself passes
    this check"), not a claim this function makes.

    Raises RepairError when either chain cannot be read — a chain warden
    cannot read is not an absent one, and numbering a round from it would
    stamp a count nobody established.
    """
    base = _resolve_commit(root, base_sha, "base_sha")
    this_head = _resolve_commit(root, head, "head_sha")
    shards, unrecorded, closure = _shard_rounds(root, base, this_head)
    from_shards = max((n for _, n in shards), default=0)
    capped = max((n for p, n in shards if p not in closure), default=0)

    # The witness OUTSIDE this branch's current history. Counted with the
    # same derivation over its own `base..prior_head`, so a head that is
    # already an ancestor of this one contributes a subset and can only
    # agree — the case worth paying for is the head a rewrite left behind.
    readings: list[HeadReading] = []
    from_prior = 0
    for raw in prior_heads:
        resolved = _resolve_commit(root, raw, "prior head")
        p_shards, _, p_closure = _shard_rounds(root, base, resolved)
        p_floor = max((n for _, n in p_shards), default=0)
        readings.append(HeadReading(head=resolved, rounds=p_floor,
                                    shards=p_shards, closure=p_closure))
        from_prior = max(from_prior, p_floor)
        capped = max(capped, max((n for p, n in p_shards
                                  if p not in p_closure), default=0))
    rewrite = None
    if from_prior > from_shards:
        ahead = ", ".join(f"{r.head[:12]} ({r.rounds} round(s))"
                          for r in readings if r.rounds > from_shards)
        rewrite = (
            f"the committed shards in {base[:12]}..{this_head[:12]} record "
            f"{from_shards} prior round(s), but a head this change pushed "
            f"EARLIER carries more: {ahead}. A history REWRITE dropped the "
            "commits that ADDED those shards — which is not the shard DELETED "
            "inside the range, whose deletion is itself a `.warden/memory/**` "
            "change the reviewed diff carries and `evidence-intact` judges. A "
            "drop leaves the range carrying no memory change at all, so "
            "nothing is dispatched and nothing would report it. The floor is "
            "taken from the higher reading, which is why the count does not "
            "reset")

    # The committed floor is the highest SHARD reading, wherever it was read:
    # the range, or a head the range no longer contains. A prior head can
    # only raise it, exactly as the rounds directory can.
    committed = max(from_shards, from_prior)
    from_dirs = len(_rounds_from_dirs(root, base, this_head))
    prior = max(committed, from_dirs)
    disagreement = None
    if from_shards != from_dirs:
        # The two CHAINS this function has always named, compared as they
        # always were. A prior head's reading is a third input and gets its
        # own note (`rewrite`) rather than being folded into this sentence,
        # which would make it describe a range it did not read.
        counted = ", ".join(f"{p} (round {n})" for p, n in shards) or "none"
        side = ("the SHARDS are ahead, so rounds ran that this checkout's "
                "rounds directory does not hold — a rebase, a fresh clone, "
                "or a deleted round directory"
                if from_shards > from_dirs else
                "the DIRECTORY is ahead, the ordinary mid-review case: a "
                "round has been minted and its shard is not committed yet")
        disagreement = (
            f"the committed shards in {base[:12]}..{this_head[:12]} record "
            f"{from_shards} prior round(s) and the local rounds directory "
            f"holds {from_dirs} — {side}. Round {prior + 1} is being "
            "numbered from the highest reading behind it; no count is folded "
            f"into another. Shards counted: {counted}")
    return RoundCount(number=prior + 1, from_shards=from_shards,
                      from_rounds_dir=from_dirs, shards=shards,
                      unrecorded=unrecorded, disagreement=disagreement,
                      closure=closure, capped_rounds=capped,
                      from_prior_heads=from_prior,
                      prior_heads=tuple(readings), rewrite=rewrite,
                      # The counted set is read off the readings above rather
                      # than walked a second time: every path `_added_shards`
                      # found lands in `shards` or in `unrecorded`, and a
                      # closure path is a member of one of those. A second
                      # walk would be free to disagree with the first about
                      # which paths this range counted.
                      uncounted=_uncounted_shards(
                          root, base, this_head,
                          {p for p, _n in shards} | set(unrecorded)))


def next_round_number(root: Path, base_sha: str, head: str) -> int:
    """Which round `warden round new` is about to mint, for this base..head.

    `round_count(...).number`, for every caller that wants the number and not
    the two counts behind it. READ THE DISAGREEMENT REPORT with `round_count`
    where the number is being recorded: this spelling drops it on the floor.

    THE COUNTER IS THE CHAIN, and there are two of them — the shards
    committed in the range, which is the floor a rebase carries, and the
    local rounds directory, which can only raise it. `round_count` states the
    rule and what it still cannot prove; `_shard_rounds` and
    `_rounds_from_dirs` state how each half is read, including the two
    ancestry tests the rest of this module already uses: a round counts when
    this head IS or descends from the head that round was minted for (so a
    sibling branch's rounds drop out), and when that head is not already
    reachable from the base (so the base branch's own rounds, and an earlier
    branch's merged work, drop out — `_repair_files` tells the change's
    commits from the base branch's the same way). The second test is
    reachability, never "descends from the base tip": a base branch that
    MOVED underneath the work would otherwise drop every round of the change
    itself and renumber round 3 as round 1. Heads are DEDUPED, for the reason
    `_chain` keys by head: two directories minted for one commit are one
    round of the chain, re-run, not two — and THIS head is never among the
    prior ones, which is load-bearing rather than tidy. A round is re-minted
    whenever a reviewer crashes or a commit lands under it, so if a second
    mint at ONE commit advanced the number, minting twice would be the new
    way to buy a later round's shorter crew — the dodge this whole binding
    exists to close, reopened at the counter.

    WHY IT EXISTS. `attest write --review-dir` compares each roster entry's
    `round` label with the number minted here. Without a minted number the
    label was compared with nothing, and a first and only review could label
    itself `round: 2` and be held to round 2's declared crew — one reviewer
    where round 1 declares two. A crew check a builder can dodge by typing a
    different number is not a check. And while the number came from the
    gitignored rounds directory ALONE, the cap it feeds was advice: a branch
    that had spent its rounds got a fresh budget by rebasing, because every
    recorded head left this line of history at once. The committed shards are
    what make the count a fact the range carries.

    WHAT IT CANNOT PROVE, said plainly: the rounds root is an ordinary
    directory under `.warden/out/`, so anything that can write there can add
    a round dir and RAISE this number, which buys a later round's shorter
    crew. That is the platform's standing trust boundary (`verify_roster`: "a
    caller that writes plausible files itself passes this check"), not a
    claim this function makes. It can no longer LOWER the number below what
    the committed shards in the range record, and dropping those is a visible
    deletion of committed evidence — see `round_count`. A round whose head
    this repository does not have cannot be placed on any chain and is
    SKIPPED, which can only lower the directory's count, never raise it.

    Raises RepairError when either chain cannot be read — a chain warden
    cannot read is not an absent one, and numbering a round from it would
    stamp a count nobody established.
    """
    return round_count(root, base_sha, head).number


def findings_digest(findings: list[dict]) -> str:
    """sha256 over the findings a verdict was computed from, first 12 hex.

    What binds a recorded verdict to the payload it judged. A
    record that named only the chain could be read as a verdict over any
    findings set — including the one a builder wished it had had.
    Canonicalized (sorted keys, no whitespace drift) so a re-serialized payload
    digests the same; any VALUE change digests differently.
    """
    h = hashlib.sha256()
    h.update(json.dumps(findings, sort_keys=True, separators=(",", ":"),
                        default=str).encode())
    return h.hexdigest()[:12]


def record(report: Report, round_dir: Path, findings: list[dict]) -> Path:
    """Write the verdict into the round it classified, and return the path.

    Without a record of what `classify` said, a builder could read exit 1 and
    mint a fourth round with nothing downstream able to tell, although the
    builder never classifies its own diff. This file is that record — the
    same role `round_binding` and `roster_verification` play for the round
    directory and the reviewer roster.

    The record carries the verdict, the exit code the command returned, both
    ends of the chain, the open-finding count and the payload digest, so a
    CLAIMED scaffolding stop can be checked against the findings that were in
    front of the checker when it said so. It sits in the round directory, next
    to `round.json`, which is where every other artifact of a round already
    lives and where `attest write --review-dir` is already pointed.

    THE HALF THIS DOES NOT DO, stated rather than implied: the verdict does not
    yet ride into the committed attestation shard, because `.warden/out/` is
    evidence, not tracked. Stamping it the way `round_binding` is stamped is
    left to `warden attest write`.
    """
    doc = {
        "schema": 1,
        "round_id": round_dir.name,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": report.verdict,
        "exit_code": exit_code(report),
        "base_sha": report.base_sha,
        "heads": list(report.heads),
        "original_files": list(report.original_files),
        "repair_files": list(report.repair_files),
        "open_findings": len(report.rows),
        "wording_findings": list(report.wording),
        "rules_version": report.rules_version,
        "rule_kinds": dict(report.rule_kinds),
        "findings_digest": findings_digest(findings),
        "reasons": list(report.reasons),
    }
    path = round_dir / "classify.json"
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return path


def exit_code(report: Report) -> int:
    """0 the review loop ends (clean, or scaffolding stop); 1 this round
    counts against the cap. 2 is reserved for a refusal and never derived
    from a report."""
    return 1 if report.verdict == VERDICT_COUNTS else 0


def render(report: Report) -> str:
    h = report.heads
    lines = [f"warden round classify: {report.verdict}",
             f"  original diff {report.base_sha[:12]}...{h[0][:12]} "
             f"({report.original_commits} commit(s), "
             f"{len(report.original_files)} file(s)): "
             f"{', '.join(report.original_files) or '-'}"]
    if len(h) > 1:
        lines.append(f"  repair wrote  {h[0][:12]}..{h[-1][:12]} "
                     f"({len(h) - 1} repair round(s), non-merge commits only, "
                     f"{len(report.repair_files)} file(s)): "
                     f"{', '.join(report.repair_files) or '-'}")
    else:
        lines.append("  repair wrote  (single round — nothing repaired yet, so "
                     "every finding is the change's)")
    lines.append(f"  open findings: {len(report.rows)}")
    for f, cls in report.rows:
        file = f.get("file") or "(no file)"
        note = ""
        if cls == "original" and file not in report.original_files:
            note = "  [outside both ranges — counts as the change's]"
        lines.append(f"    {cls:<8} {str(f.get('severity')):<7} {file}"
                     f"{note}")
    if report.wording:
        lines.append(f"  open wording findings below the blocking severities "
                     f"(do not count): "
                     f"{len(report.wording)}")
        for f in report.wording:
            lines.append(f"    {str(f.get('rule_id')):<16} "
                         f"{str(f.get('severity')):<7} "
                         f"{f.get('file') or '(no file)'}")
    for r in report.reasons:
        lines.append(f"  - {r}")
    tail = {
        VERDICT_CLEAN: "no open finding that counts — the loop ends because it "
                       "is clean",
        VERDICT_STOP: ("scaffolding stop — park the open LOWs as tracked "
                       "items, recorded dismissed-with-reason; do not mint "
                       "another round"),
        VERDICT_COUNTS: ("this round counts against the repair cap — a "
                         "defect in the change is still open"),
    }[report.verdict]
    lines.append(f"  verdict: {report.verdict} — {tail}")
    return "\n".join(lines) + "\n"
