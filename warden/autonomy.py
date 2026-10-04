"""The safe half of the autonomy ladder.

The ladder (`.warden/skills-policy.md`) draws its line not at human-vs-machine
but at *does the action raise enforcement on anyone?* Two actions do not, and
this module performs them:

  - **pause** a rule on a >=3 consecutive REFUTING REVIEW ROUNDS streak.
    Pausing only ever makes the gate QUIETER — enforced, not assumed: a pause
    whose reopened catalog entry would push UNANSWERED past the repo's declared
    ceiling is REFUSED, because that pause would raise
    enforcement and the ladder's line is drawn exactly there. It is
    reversible by removing one
    field, so a noisy rule stops taxing every review the moment the corpus has
    refuted it three ROUNDS running — with a recorded reason and a pause
    backtest artifact. Rounds, never records: one round's findings are one
    round's opinion however many it filed.
  - **adopt** a NON-BLOCKING rule whose cost was measured. A rule shipped at a
    severity outside `blocking_severities` reports and cannot fail a build, so
    adopting it is closer to adding a reporting lens than to moving the gate.
    A blocking-severity adoption (HIGH on this repo, whatever `blocking_
    severities` names on another) is REFUSED here — raising enforcement is the
    ladder's human-only tier.

The containment property the whole platform rests on lives in what this module
will NOT do: it only ever mutates the working tree. It never commits, pushes,
or merges — merge authority stays the founder's, delegated only by an explicit,
current, revocable grant, on the terms `graph.yaml`'s `review.delegation`
declares; the terms live there and the grant never does, because a tracked file
has no clock. A caller opens a PR; a person merges it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from . import yamlio


class AutonomyError(Exception):
    """A refused machine-tier action — always because performing it would
    exceed the safe half of the ladder (raise enforcement, pause a rule that
    is already off, overwrite a rule, adopt a malformed one)."""


def _rules_dir(root: Path) -> Path:
    """The DECLARED rules dir (`review.rules_dir`), via the one lenient
    derivation (`config.declared_rules_dir`). Hardcoding the default here
    would send machine-tier writes to `.warden/rules` while the gate reads the
    declared dir — a rule the gate never sees, a machine-tier action that
    silently no-ops.

    A declaration that cannot be read REFUSES rather than guessing: acting
    on a guessed dir is the same gate-invisible write with an extra step,
    and a refused action is this module's normal failure shape.
    """
    from .config import declared_rules_dir

    rules_dir, problem = declared_rules_dir(root)
    if problem:
        raise AutonomyError(
            f"cannot resolve the declared rules dir ({problem}) — refusing "
            "to act on a guessed location: a machine-tier write into a dir "
            "the gate does not read is invisible enforcement change")
    return rules_dir


# --------------------------------------------------------------------------- #
# Pause
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PauseAction:
    """One rule the corpus has refuted in three review ROUNDS running, with the
    precision counts that justify the pause (derived from the committed corpus,
    never typed by a caller). `streak` counts rounds; `judged`/`refuted` count
    records, and the two are deliberately different numbers."""
    rule_id: str
    streak: int
    judged: int
    upheld: int
    refuted: int
    dismissed: int
    rate: float
    wilson_lb: float


def pause_candidates(root: Path, *, records: list[dict] | None = None
                     ) -> list[PauseAction]:
    """The rules the ladder would auto-pause: those refuted by >=3 consecutive
    review ROUNDS, per `warden.memory.stats` — which already excludes
    `unmapped:` candidates (you cannot pause a rule that does not exist),
    already-paused rules, and fails CLOSED (empty) on an unreadable ruleset.

    `records` may be injected (tests, or a caller that already read the corpus);
    otherwise the COMMITTED shards are read — the durable evidence, not the
    rebuilt cache.
    """
    from . import memory as memory_mod

    rules_dir = _rules_dir(root)
    if records is None:
        records = memory_mod.records_from_shards(root)
    doc = memory_mod.stats(root, rules_dir=rules_dir, records=records)
    ids = doc.get("pause_candidates", [])
    if not ids:
        return []
    streaks = memory_mod.refutation_streaks(records)
    per_rule = doc.get("per_rule", {})
    actions: list[PauseAction] = []
    for rid in ids:
        row = per_rule.get(rid, {})
        n = row.get("n", 0)
        upheld = row.get("confirmed", 0) + row.get("fixed", 0)
        actions.append(PauseAction(
            rule_id=rid,
            streak=streaks.get(rid, 0),
            judged=n,
            upheld=upheld,
            refuted=row.get("refuted", 0),
            dismissed=row.get("dismissed", 0),
            rate=round(upheld / n, 3) if n else 0.0,
            wilson_lb=row.get("wilson_lb", 0.0),
        ))
    return actions


def _pause_reason(action: PauseAction) -> str:
    return (
        f"auto-paused by the autonomy ladder on a {action.streak}-consecutive-"
        "refuting-review-round streak in the committed corpus, "
        "counted over rounds so one round's write order cannot fabricate it: "
        "pausing "
        "only makes the gate quieter and is reversible by removing this field. "
        "The derived counts and the bound rules_version are in the pause "
        "backtest artifact under .warden/memory/backtests/.")


def with_pause_frontmatter(text: str, reason: str, *, where: str = "rule") -> str:
    """`text` with `paused: true` + `paused_reason` inserted into its YAML
    frontmatter before the closing delimiter — every other byte is left
    untouched (a full YAML round-trip would reflow the whole block and perturb
    rules_version beyond the pause).

    Pure on purpose: the cage's carve-out check RE-DERIVES a
    paused rule file from its base version with this same function and requires
    the committed bytes to match exactly. A second implementation of "what a
    pause edit looks like" would drift from this one, and the drift would show
    up as a hard stop that fires on a legitimate pause, or does not fire on
    something else.
    """
    import yaml

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        raise AutonomyError(f"{where}: no YAML frontmatter to pause")
    close = next((i for i in range(1, len(lines))
                  if lines[i].rstrip("\n") == "---"), None)
    if close is None:
        raise AutonomyError(f"{where}: unterminated frontmatter")
    # safe_dump quotes/escapes the reason exactly as the loader expects to read
    # it back; a hand-built line would break on a colon or quote in the text.
    reason_line = yaml.safe_dump({"paused_reason": reason},
                                 default_flow_style=False, allow_unicode=True,
                                 sort_keys=False)
    insert = "paused: true\n" + reason_line
    return "".join(lines[:close]) + insert + "".join(lines[close:])


def _insert_pause_frontmatter(rule_path: Path, reason: str) -> None:
    """Apply `with_pause_frontmatter` to a rule file in place."""
    # utf-8-sig for parity with the rule loader (rules._parse): a BOM-prefixed
    # file loads there and can become a pause candidate, so the writer must read
    # it the same way or it would reject the frontmatter it was just handed.
    text = rule_path.read_text(encoding="utf-8-sig")
    rule_path.write_text(with_pause_frontmatter(text, reason,
                                                where=str(rule_path)))


def _write_pause_backtest(root: Path, action: PauseAction, rules_version: str,
                          today: str, reason: str) -> Path:
    bt_dir = root / ".warden" / "memory" / "backtests"
    bt_dir.mkdir(parents=True, exist_ok=True)
    path = bt_dir / f"{action.rule_id}-pause-{today.replace('-', '')}.json"
    doc = {
        "schema": 1,
        "rule_id": action.rule_id,
        "action": "pause",
        "judged": action.judged,
        "upheld": action.upheld,
        "refuted": action.refuted,
        "dismissed": action.dismissed,
        "rate": action.rate,
        "wilson_lb": action.wilson_lb,
        "streak": action.streak,
        "window": "the committed corpus at pause time",
        "derived_from": ("warden.autonomy.pause_candidates via "
                         "warden.memory.stats over records_from_shards; counts "
                         "recomputed at write time"),
        "generated": today,
        "note": reason,
        # The version S-05 binds to is the one AFTER the pause edit perturbs it
        # — a pause is a rule change, and it must carry evidence for the ruleset
        # it produces, not the one it left.
        "rules_version": rules_version,
    }
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return path


def _ceiling_breach(root: Path, rule_id: str, catalog_path: Path | None, *,
                    also_paused: frozenset[str] = frozenset()) -> str:
    """Why pausing `rule_id` would push the repo past its declared ceiling.

    The ladder makes pause machine-eligible on one claim: it "only ever makes
    the gate QUIETER". A paused rule's catalog entry reopens into the gap, and
    `rules recommend` exits 1 above the declared ceiling, so in a repo AT its
    ceiling pausing any sole-implementing rule turns green CI red.

    The guard keeps the claim true rather than merely stated. Measured with `paused_extra`, so nothing is written before the answer
    is known: a guard that edits the rule file to find out has already caused
    the breach it is checking for.

    `also_paused` names pauses this run has already decided on but not yet
    written — a batch of candidates is cumulative in the real loop, so
    measuring each against bare disk would clear a pause the previous one
    makes unsafe.

    Returns "" when the pause is safe, or when the repo declares no ceiling —
    no declared line means none to cross, and inventing one would refuse
    pauses in every repo that never opted in. A ceiling that IS declared and
    cannot be read is the opposite case and refuses.
    """
    from . import advisor as advisor_mod

    ceiling, complaints = advisor_mod.load_ceiling(root)
    if complaints:
        # load_ceiling returns (None, complaints) for a ceiling that was
        # DECLARED and could not be read, and its docstring says that "must
        # stop the run, never silently 'no ceiling' — a typo must not disarm
        # the one deterministic obligation this file carries". Discarding the
        # complaints here would disarm it in the one mechanism whose entire job is
        # refusing: "could not look" is not "nothing to cross".
        detail = "; ".join(complaints)
        return (f"the declared ceiling could not be read, so the cost of "
                f"pausing {rule_id!r} cannot be measured — {detail}")
    if ceiling is None:
        return ""
    kwargs = {"catalog_path": catalog_path} if catalog_path else {}
    before = advisor_mod.recommend(root, records=[],
                                   paused_extra=set(also_paused), **kwargs)
    after = advisor_mod.recommend(root, records=[],
                                  paused_extra={rule_id} | set(also_paused),
                                  **kwargs)
    if not (before.gap_known and after.gap_known):
        # recommend fails closed by returning gap_known=False with ZERO
        # recommendations — which would otherwise read as n_before=0,
        # n_after=0, nothing reopened, pause safe. "Could not measure the
        # cost" must refuse here, in the guard itself, not rely on a caller
        # having errored on the same condition first.
        why = "; ".join(dict.fromkeys((*before.notes, *after.notes))) \
            or "no reason reported"
        return (f"the gap could not be computed ({why}), so the cost of "
                f"pausing {rule_id!r} cannot be measured — a pause whose "
                "cost is unknown is not machine-eligible")
    n_before, n_after = len(before.recommendations), len(after.recommendations)
    reopened = sorted({r.entry_id for r in after.recommendations}
                      - {r.entry_id for r in before.recommendations})
    # Both conditions, deliberately. `n_after > ceiling` alone would refuse every
    # pause in a repo already over its ceiling — including pauses that reopen
    # nothing, with a message naming a reopening that did not happen. Such a
    # pause genuinely only makes the gate quieter, which is the ladder's own
    # test for machine-eligibility.
    if n_after <= ceiling or not reopened:
        return ""
    return (f"pausing {rule_id!r} reopens {', '.join(reopened)}, taking "
            f"UNANSWERED {n_before} -> {n_after}, past the declared "
            f"ceiling of {ceiling}. A pause is machine-eligible because it "
            "only ever makes the gate quieter, and this one would not. Raise "
            f"the ceiling in {advisor_mod.ANSWERS_PATH}, record a verdict for "
            "the reopened entry, or adopt a rule that covers it — then pause.")


def apply_pause(root: Path, action: PauseAction, *, today: str | None = None,
                catalog_path: Path | None = None) -> tuple[Path, Path]:
    """Perform the pause: set the frontmatter field with its recorded reason,
    then write the pause backtest artifact stamped with the POST-edit
    rules_version. Returns (rule_path, artifact_path). Refuses a rule that is
    already paused or has no rule file — a double pause is a bug, not a no-op
    — and refuses one that would breach the declared UNANSWERED ceiling,
    which is what keeps "a pause only ever quiets the gate"
    true rather than merely stated.
    """
    from . import rules as rules_mod

    today = today or date.today().isoformat()
    rules_dir = _rules_dir(root)
    rule_path = rules_dir / f"{action.rule_id}.md"
    if not rule_path.is_file():
        raise AutonomyError(
            f"cannot pause {action.rule_id!r}: no rule file at {rule_path}")
    current = {r.id: r for r in rules_mod.load_rules(rules_dir)}
    existing = current.get(action.rule_id)
    if existing is not None and existing.paused:
        raise AutonomyError(
            f"{action.rule_id!r} is already paused — refusing a double pause")

    breach = _ceiling_breach(root, action.rule_id, catalog_path)
    if breach:
        raise AutonomyError(f"refusing to pause: {breach}")

    reason = _pause_reason(action)
    _insert_pause_frontmatter(rule_path, reason)
    # Recompute AFTER the edit: this is the version the backtest must name for
    # S-05 to accept it as fresh evidence for the paused ruleset.
    rv = rules_mod.rules_version(rules_dir, root)
    artifact = _write_pause_backtest(root, action, rv, today, reason)
    return rule_path, artifact


# --------------------------------------------------------------------------- #
# Adopt
# --------------------------------------------------------------------------- #

# A rule id that is safe as a file name for both halves an adoption writes.
_SAFE_RULE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _adopt_backtest_path(root: Path, rule_id: str, today: str) -> Path:
    return (root / ".warden" / "memory" / "backtests"
            / f"{rule_id}-adopt-{today.replace('-', '')}.json")


def _write_adopt_backtest(root: Path, rule, rules_version: str,
                          today: str, blocking: set) -> Path:
    """The artifact an adoption carries.

    WHY IT EXISTS. Adding a rule file bumps `rules_version`, and certification
    S-05 requires SOME backtest artifact stamped with the CURRENT version.
    Without it the tree `warden autonomy adopt` produces would fail S-05 with no
    tooling remedy: a granted consumer merging a machine adoption would drop a
    certification level, silently on any repo whose CI gates below Level 5.

    WHAT IT MAY HONESTLY DECLARE — which is the whole design. Nothing. The
    rule has never produced a finding, so there is no judged/refuted tally,
    and an `engine: claude` rule cannot be replayed over the tree to measure a
    hit count. `judged` is therefore 0 and the precision fields the promote
    artifacts carry are ABSENT rather than zeroed: a zeroed tally reads as one
    that was measured. `_adopt_artifact_problem` refuses an adopt artifact
    that carries a nonzero `judged` OR any of those precision fields, so a
    fabricated measurement is refused on this arm too. It does NOT audit the free-text `note`, and does not claim to.
    """
    path = _adopt_backtest_path(root, rule.id, today)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": 1,
        "rule_id": rule.id,
        "action": "adopt",
        # The record says WHAT was adopted. A record disagreeing with the tree
        # is "evidence for a change the tree does not contain", which is the
        # shape S-05's pause arm already refuses — so the carve-out binds this
        # field to the committed rule file's own severity.
        "severity": rule.severity,
        "judged": 0,
        "window": "none — the rule is new",
        "derived_from": (
            "warden.autonomy.adopt_rule: an adoption records the rule ADDITION "
            "itself. It carries no judged history and no measured hit count — "
            "the rule has produced no findings yet, and an engine:claude rule "
            "cannot be replayed over the tree — so the precision fields a "
            "promote artifact carries are absent rather than zeroed"),
        "generated": today,
        "note": (
            f"adopted by the autonomy ladder's machine tier: {rule.id!r} at "
            f"severity {rule.severity}, which is outside the repo's declared "
            f"blocking_severities {sorted(blocking)} — it reports and cannot "
            "fail a build. `warden autonomy adopt` writes the rule file and "
            "this artifact and opens nothing; a caller opens the PR, a "
            "person merges it, and nothing auto-merges"),
        # Stamped AFTER the rule file lands, for the same reason the pause arm
        # stamps post-edit: the version S-05 binds to is the one the change
        # PRODUCES, not the one it left.
        "rules_version": rules_version,
    }
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return path


def adopt_rule(root: Path, rule_src: str, *, dest_name: str,
               blocking_severities, today: str | None = None
               ) -> tuple[Path, Path]:
    """Adopt a drafted NON-BLOCKING rule: validate it, and REFUSE if its
    severity is in `blocking_severities` — a machine adoption may only add
    reporting, never raise enforcement. Otherwise write it under the repo's
    DECLARED rules dir (`review.rules_dir`), so the gate that reads that dir
    sees what was adopted, plus the adoption backtest artifact
    that change carries. Returns (rule_path, artifact_path). Writes files
    only; opens no PR and never merges.

    The refusal keys on the repo's declared `blocking_severities`, not on the
    literal string "HIGH": if a repo declares MEDIUM blocking too, a MEDIUM
    adoption is refused there for the same reason.

    The artifact is not decoration: without it the tree this
    command produces fails certification S-05, because adding a rule file
    bumps rules_version and S-05 wants an artifact naming the current one. The
    ladder's own carve-out text says an adoption may carry one — "the
    pause/adopt backtest artifact that change carries".
    """
    import tempfile

    from . import rules as rules_mod

    blocking = set(blocking_severities)
    # BEFORE any write, including the validation tempdir's: `dest_name` becomes
    # a file name for both halves an adoption writes, and a separator in it
    # would escape the tempdir as an unhandled FileNotFoundError rather than a
    # refusal.
    if not _SAFE_RULE_ID.fullmatch(dest_name):
        raise AutonomyError(
            f"refusing to adopt: {dest_name!r} is not a plain name — it "
            "becomes a file name for both the rule and its backtest, so a "
            "separator or a dot segment in it would write outside the dirs "
            "the carve-out permits")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / f"{dest_name}.md").write_text(rule_src)
        try:
            parsed = rules_mod.load_rules(Path(td))
        except rules_mod.RuleError as e:
            raise AutonomyError(
                f"refusing to adopt a malformed rule: {e}") from e
    if not parsed:
        raise AutonomyError("refusing to adopt: no rule parsed from the source")
    rule = parsed[0]
    if rule.severity in blocking:
        raise AutonomyError(
            f"machine adoption refused: severity {rule.severity} is in "
            f"blocking_severities {sorted(blocking)} — a machine adoption may "
            "only ADD reporting, never raise enforcement. Promoting a rule to a "
            "blocking severity is the ladder's human-only tier: edit the rule's "
            "severity in a PR a human reviews.")

    # The gate loads rules BY FILE, and the carve-out refuses a mismatch, so a
    # writer that allowed one would produce a tree its own reader auto-closes.
    # Refused here rather than left for the reader.
    if rule.id != dest_name:
        raise AutonomyError(
            f"refusing to adopt: the rule declares id {rule.id!r} but would be "
            f"written as {dest_name}.md — the gate loads rules by file, and the "
            "mismatch hides which one landed")
    # Severity is not the only way a rule raises enforcement: `covers:` and
    # `implements:` do too, at any severity, and `_adoption_problem` — the
    # reader the carve-out and certify run over the committed tree — refuses
    # both. A writer that accepted them produced a tree its own reader
    # auto-closed, and the operator learned it from the carve-out
    # (found building PR #311). Refused here, with the
    # reader's own sentence, before either half is written.
    raised = _enforcement_field_problem(rule)
    if raised:
        raise AutonomyError(f"refusing to adopt: {raised}")
    dest_dir = _rules_dir(root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{dest_name}.md"
    # `is_symlink()` as well as `exists()`: exists() FOLLOWS the link, so a
    # dangling symlink would read as "absent", the write would go THROUGH it
    # to a path outside the rules dir, and the rollback would remove only the
    # link. An adoption writes a new regular file or refuses.
    if dest.exists() or dest.is_symlink():
        raise AutonomyError(
            f"refusing to overwrite an existing rule at {dest} — adoption adds "
            "a rule, it does not edit one, and it never writes through a "
            "symlink to a path the carve-out does not cover")
    today = today or date.today().isoformat()
    artifact_path = _adopt_backtest_path(root, rule.id, today)
    # Checked BEFORE either write, and refused for the same reason the rule
    # file is: an adoption adds evidence, it never edits evidence already
    # committed. A rule write that refuses to overwrite beside an artifact
    # write that silently clobbers would be half a guard.
    if artifact_path.exists() or artifact_path.is_symlink():
        raise AutonomyError(
            f"refusing to overwrite an existing backtest at {artifact_path} — "
            "an adoption adds its evidence, it does not edit evidence already "
            "on disk, and it never writes through a symlink")
    dest.write_text(rule_src)
    # BOTH HALVES OR NEITHER. The artifact is what keeps the produced tree
    # certifiable, so a rule file left behind without one is precisely the
    # uncertifiable tree the artifact exists to prevent. An OSError here removes the rule file and reports
    # the cause rather than escaping as a bare OSError past the CLI's handler.
    try:
        rv = rules_mod.rules_version(dest_dir, root)
        artifact = _write_adopt_backtest(root, rule, rv, today, blocking)
    except OSError as e:
        dest.unlink(missing_ok=True)
        raise AutonomyError(
            f"adoption rolled back: the rule file was written but its backtest "
            f"could not be ({e}). A rule with no artifact bumps rules_version "
            "with nothing evidencing it, which fails certification S-05, so "
            "the rule file was removed rather than left behind") from e
    return dest, artifact


# --------------------------------------------------------------------------- #
# Carve-out
# --------------------------------------------------------------------------- #
#
# The cage's forbidden-path post-check greps the committed diff against one
# regex and closes the PR on a hit. That is correct for a session editing the
# gate, and wrong for the writes the ladder's machine tier is PERMITTED
# to make — so the ladder's permission was auto-closed in practice and the
# carve-out in `.warden/skills-policy.md` existed on paper only.
#
# Four writes now, not three: PR #273 added the attestation shard a
# review round leaves under `.warden/memory/attest/`. The first three are
# RULE changes and the artifacts that justify them; the fourth is EVIDENCE
# that a review happened, which `warden gate` requires of every PR and the
# freeze refused — a contradiction no unattended run could get between. The
# ground of the ruling is that line: a shard changes nothing the gate
# enforces. `.warden/memory/decide/` is NOT admitted for the mirror reason,
# and that is a decision rather than an omission — a decision shard records
# what was RULED, policy the skills refer back to, and no run is blocked
# without one.
#
# PROVENANCE IS DECIDED BY SHAPE, NEVER BY A MARKER. A commit trailer, a flag
# file, an env var — anything a session can write proves only that the session
# wrote it. The diff's own shape is different: a diff that has the shape
# `warden autonomy` produces IS the change `warden autonomy` is permitted to
# make, whoever typed it, because the shape is exactly the bound.
#
# Everything here reads the COMMITTED tree through `git show`, never the
# filesystem — the same hermetic discipline attest.attested_shas keeps, and for
# the same reason: the working tree is session-writable and the post-check's
# whole subject is what got committed.
#
# The check can only ever CLEAR a violation the post-check already raised. It
# never creates one, it never merges anything, and it adds no auto-merge path:
# a cleared run still opens a PR that a human merges.

BACKTEST_SUBDIR = ".warden/memory/backtests"
ATTEST_SUBDIR = ".warden/memory/attest"


def _show(root: Path, rev: str, path: str) -> str | None:
    """`git show <rev>:<path>`, or None when the blob is not there."""
    import subprocess

    from .runs import git_env
    proc = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=root,
                          capture_output=True, text=True, env=git_env())
    return proc.stdout if proc.returncode == 0 else None


def _frontmatter_of(text: str) -> dict | None:
    """A rule file's YAML frontmatter as a mapping, or None when it has none
    that parses. Never raises: every caller here is deciding a refusal, and an
    unparseable header is simply "not the shape we are looking for"."""
    import yaml

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        return None
    close = next((i for i in range(1, len(lines))
                  if lines[i].rstrip("\n") == "---"), None)
    if close is None:
        return None
    try:
        doc = yamlio.load("".join(lines[1:close]))
    except yaml.YAMLError:
        return None
    return doc if isinstance(doc, dict) else None


def _paused_reason_of(text: str) -> str | None:
    """The `paused_reason` in a rule file's frontmatter, or None."""
    doc = _frontmatter_of(text)
    if doc is None or doc.get("paused") is not True:
        return None
    reason = doc.get("paused_reason")
    return reason if isinstance(reason, str) and reason.strip() else None


def _committed_policy(root: Path, rev: str) -> tuple[str, frozenset[str], str]:
    """(rules_prefix, blocking_severities, problem) read from the COMMITTED
    `repo.yaml` at `rev` — which callers pass as the MERGE BASE, the last
    commit the session did not write.

    Never from `config.load()` / `declared_rules_dir()`,
    which read `repo.yaml` off the FILESYSTEM. The runner calls this with
    cwd = the session-writable worktree, which it never cleans after the
    session, and an UNCOMMITTED `repo.yaml` never appears in the committed
    diff — so it is never a forbidden path and the carve-out is never asked
    about it. A session could otherwise commit a `severity: HIGH` rule, leave
    `blocking_severities: [LOW]` uncommitted beside it, and have the severity
    refusal — the bound the whole ladder rests on — evaluated against a
    threshold it wrote itself. Reading the COMMITTED head alone would still
    leave the committed variant of the same trick (both edits in one commit)
    open; the merge base closes both, because the session wrote none of it.
    """
    raw = _show(root, rev, "repo.yaml")
    if raw is None:
        return "", frozenset(), (f"repo.yaml is not readable at {rev} — the "
                                 "carve-out's policy must come from a commit "
                                 "the session did not write, so an unreadable "
                                 "one refuses")
    try:
        doc = yamlio.load(raw)
    except yaml.YAMLError as e:
        return "", frozenset(), f"repo.yaml at {rev} does not parse ({e})"
    review = doc.get("review") if isinstance(doc, dict) else None
    if not isinstance(review, dict):
        return "", frozenset(), (f"repo.yaml at {rev} declares no `review:` "
                                 "mapping — refusing to guess the policy")
    severities = review.get("blocking_severities")
    if (not isinstance(severities, list) or not severities
            or not all(isinstance(s, str) and s for s in severities)):
        return "", frozenset(), (
            f"repo.yaml at {rev} declares no usable "
            "`review.blocking_severities` — the machine-adoption refusal keys "
            "on it, and a bound that cannot be read is not a bound")
    rules_dir = review.get("rules_dir", ".warden/rules")
    if not isinstance(rules_dir, str) or not rules_dir:
        return "", frozenset(), (f"repo.yaml at {rev} declares an unusable "
                                 "`review.rules_dir`")
    prefix = rules_dir.strip("/")
    if not prefix or prefix.startswith("..") or "/../" in prefix:
        return "", frozenset(), (f"`review.rules_dir` {rules_dir!r} does not "
                                 "name a path inside the repo")
    return prefix + "/", frozenset(severities), ""


def _attest_shard_problem(root: Path, path: str, state: str | None, head: str,
                          range_shas: frozenset[str],
                          ruleset: "Path | None" = None) -> str | None:
    """Why this `.warden/memory/attest/` path is NOT the shard a review round
    leaves behind, or None when it is.

    WHY AN ATTESTATION IS IN THE CARVE-OUT AT ALL (the founder's ruling,
    PR #273). The other three arms write RULES and the artifacts that justify
    them — things that change what the gate does. A shard changes nothing the
    gate enforces: it is the committed record that a review round RAN, which
    `warden attest check` then requires of every PR. Freezing it beside the
    rules made the two halves of this platform contradict each other: `warden
    gate` refuses a PR carrying no committed shard, the freeze refuses the
    shard, and the first completed unattended run (20260920-205434) built,
    reviewed and repaired a fix and then stopped at a forbidden path for
    committing the evidence the protocol told it to commit, its work unpushed.

    WHAT THIS DOES NOT CLAIM, said plainly because the whole carve-out rests
    on it: admitting the path does not make a shard TRUE. `verify_roster`'s
    own docstring is explicit that a caller which writes plausible reviewer
    files passes it, so nothing here — and nothing anywhere in warden —
    distinguishes a real round from a fabricated one. What keeps that from
    being a NEW hole is that the cage already carried session-authored
    attestation content into the committed corpus without this arm: `cage/
    run.sh`'s publication step runs `warden memory ingest` over the session's
    own `.warden/out/*-attest/attestation.json`, commits the shard it derives
    and pushes it. Trusted code, session data — the same data this arm lets
    the session commit itself. The bound is elsewhere and unchanged: the
    carve-out CLEARS a stop and never merges, `graph.yaml` (which fixes the
    crew a roster must match) stays frozen, and a human merges every PR.

    THE SEAM THAT EQUIVALENCE COSTS, and what is done about it (rounds 1 and
    2, `enforcement-truth`). "Same data" is only true if this path applies the
    refusals `memory ingest` applies on the way in, and the first version of
    this arm applied NONE of them. `memory ingest` has exactly two, and both
    are re-applied here because they cover disjoint halves of one shard:

    - `_check_rule_ids`, for a shard WITH records. A session could otherwise
      hand-write one carrying records whose `rule_id` resolves to no declared
      rule — `validate_shard_envelope` checks field TYPES, not ruleset
      membership — and those records are committed corpus that
      `records_from_shards` -> `stats` -> `pause_candidates` and
      certification E-02 all read. It runs against the ruleset COMMITTED AT
      THE MERGE BASE, for the reason `_committed_policy` reads the policy
      there: the session wrote none of it.
    - the EVENT fields, for a shard with NO records — the half round 1 missed
      and round 2 filed HIGH. `memory._event_defects` guards exactly this on
      the ingest side, and its own comment says why: "on the findings-free
      path `_check_rule_ids` returns immediately (no findings), so without
      this nothing between a hand-written artifact and committed evidence
      would look at the verdict at all". Without it here, a hand-written
      six-field `records: []` shard cleared this arm — and because
      `attest.check_range` reads a verdict-less shard as "unrecorded ... does
      not block", that same file then satisfied the attestation gate. A shard
      ingest would have refused to mint, clearing the freeze AND the gate.

    The second is re-derived rather than called, for ONE reason stated so
    nobody re-unifies them by mistake: `_event_defects` reads an ARTIFACT,
    which names the reviewed commit `head_sha`, and a SHARD names it `sha`.
    Calling it on a shard reports "no head_sha" against every genuine one.
    So `_EVENT_FIELDS` and `VERDICTS` — the vocabularies — are IMPORTED, and
    only the one field rename is local. And it is applied to every shard,
    records or not, which is stricter than ingest: there a records-bearing
    artifact is exempt because its records carry the provenance, and here a
    shard that cannot say what it reviewed, against what, when, by whom and
    with what outcome is not evidence whichever half it falls in.

    INGEST ALSO REDACTS, and that is a third seam, not a refusal — the one a
    round of this branch's own review caught the docstring omitting. `memory
    ingest` scrubs secret-shaped strings over the WHOLE shard envelope
    (`_redact_value`) and over each record's finding/evidence/reason
    (`_redact`), because "the corpus must never carry a live credential no
    matter which field carried it in". A session hand-writing a shard bypasses
    both, so a credential quoted in `evidence` — or planted in
    `rules_version`, or in a reviewer label — would land in
    `.warden/memory/attest/` through this arm having been scrubbed through the
    runner's. `_redaction_problem` closes it the only way that keeps ONE
    definition of what a secret is: it re-runs ingest's own scrubbers over the
    committed bytes and REFUSES any difference. It cannot redact — rewriting
    committed evidence is not this checker's to do — so it refuses and names
    the field, which is the honest shape for a check that can only ever clear
    a stop.

    The residue is named rather than left implied. `range_binding_at_ingest`
    WARNS and never refuses (its own docstring says so) and `_consumed`'s
    one-run-one-shard rule is a hint that suppresses a re-mint, not a gate —
    both are lost as RECORDS on this path and neither was ever a rejection.
    And it remains true that a session can fabricate records under ids that DO
    resolve, inside a well-formed event envelope; nothing above claims
    otherwise, and nothing in warden catches it. Nor is the record half of the
    redaction check as wide as the envelope half, which is the other thing not
    to read into it: `_redaction_problem` re-runs `_redact` over exactly
    `finding`, `evidence` and `reason` — the three fields `build_records`
    scrubs — so a credential planted in a record's `file`, `tags`,
    `dir_prefix`, `bead`, `legacy_rule_id` or any key outside that list is
    admitted silently, `validate_shard_records` type-checking only
    `RECORD_FIELD_CONTRACT`'s keys and ignoring the rest. That is PARITY with
    `memory ingest` — `build_records` copies `file` and `tags` unscrubbed too
    — so this arm admits nothing ingest would have refused, and the residue is
    in what may be CLAIMED for it, not in what it lets through. The envelope
    half above is the one that holds "whichever field carried it in"; the two
    are not the same rule and the stronger one does not cover the weaker.

    TWO NARROWINGS PAST THE INTERACTIVE PATH, which `warden attest write`
    itself permits and this does not:

    - `roster_verification: verified` — the round's declared roster was
      cross-checked against distinct, non-empty reports with none left
      unclaimed. `unverified-roster` is a legal shard for a human running
      warden by hand; it is a bare assertion, and a bare assertion is not
      what this arm exists to carry past a hard stop.
    - `round_binding: bound` — the reports came from a directory `warden
      round new` MINTED for exactly this head. `unminted` is likewise legal
      elsewhere and refused here: warden has no identity for a directory it
      did not mint, so it cannot say which commit those reports describe.

    And the binding that makes the shard THIS branch's: its `sha` must name a
    commit in the judged range. A shard is written against HEAD and committed
    one commit later, so a genuine one always does; an imported shard — from
    another branch, or naming a sha nothing here produced — does not.

    NOT checked here, deliberately: the verdict. An early round's
    `findings-open` shard is committed BY DESIGN (the protocol is one shard
    per round, the last of them clean), and `attest.check_range` owns the
    tip-clean rule on the whole range. Refusing an open verdict here would
    auto-close the honest multi-round branch the protocol produces.
    """
    if state != "A":
        return ("only a NEW attestation shard is in the carve-out; an edited "
                "or deleted one is not. The corpus is append-only evidence, "
                "and rewriting a committed shard rewrites the record of a "
                "review that already happened")
    name = path[len(ATTEST_SUBDIR) + 1:]
    if "/" in name or not name.endswith(".json"):
        return (f"{ATTEST_SUBDIR}/ holds flat `.json` shards as `warden "
                "memory ingest` names them, and this is not one — a path "
                "shaped unlike the writer's output is not the writer's output")
    raw = _show(root, head, path)
    if raw is None:
        return f"its committed blob could not be read at {head}"
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        return f"not readable as an attestation shard ({e})"
    # The SHARED envelope validator, never a second reading of "what a shard
    # is": `memory.read_shard` and `attest.attested_shas` both call it, so a
    # file this arm accepts is one those readers can read. A private shape
    # check here would drift into accepting a file the corpus then chokes on.
    from . import memory as memory_mod
    try:
        memory_mod.validate_shard_envelope(name, doc)
    except ValueError as e:
        return str(e)
    # `.get("source", "attest")` matches `attest._accept_shard`: shards that
    # predate the field are attestations. A `source: gate` shard is NOT —
    # those record a deterministic checker firing, are gitignored on purpose
    # and must not reach the committed corpus through here.
    source = doc.get("source", "attest")
    if source != "attest":
        return (f"declares `source: {source!r}`, so it is not an attestation. "
                "Only the shard a REVIEW ROUND leaves is in the carve-out; a "
                "gate shard is gitignored and never committed")
    sha = doc.get("sha")
    if not isinstance(sha, str) or not sha:
        return ("names no `sha`, so it attests no commit — a shard bound to "
                "nothing is not the record of this branch's review")
    if sha not in range_shas:
        return (f"attests {sha[:12]}, which is not a commit in this diff. An "
                "attestation is in the carve-out as the record of THIS "
                "branch's review; one naming a commit this branch never "
                "produced was imported, not earned")
    roster = doc.get("roster_verification")
    if roster != "verified":
        return (f"records `roster_verification: {roster!r}`, not `verified`. "
                "The carve-out carries only a roster that was cross-checked "
                "against the round's own reports — pass `--review-dir` to "
                "`warden attest write` so the claim is checked rather than "
                "asserted")
    binding = doc.get("round_binding")
    if binding != "bound":
        return (f"records `round_binding: {binding!r}`, not `bound`. The "
                "carve-out carries only a round `warden round new` minted for "
                "the head it attests — warden has no identity for a directory "
                "it did not mint, so it cannot say which commit those reports "
                "describe")
    event = _event_problem(doc)
    if event:
        return event
    redaction = _redaction_problem(doc)
    if redaction:
        return redaction
    return _record_rule_id_problem(doc.get("records") or [], ruleset)


def _redaction_problem(doc: dict) -> str | None:
    """Why this shard is not what `memory ingest`'s redaction would have
    produced. That is NOT a synonym for "it carries a credential", and the
    message says which of the two reasons fired, because they send the writer
    to different places.

    RE-RUN, NEVER RE-IMPLEMENTED. `_redact_value` (the envelope) and
    `_redact` (a record's finding/evidence/reason, which keys on the rule_id
    so a `secret`-named rule redacts wholesale) are ingest's own scrubbers,
    and what counts as secret-shaped is theirs to say. Both are IDEMPOTENT on
    every real value — the writer runs them before it writes, so a shard that
    came through ingest is a fixed point — which is exactly what makes
    "unchanged by them" a usable test for "these are the bytes ingest would
    have committed".

    It REFUSES rather than scrubs. This checker only ever clears a stop; it
    reads the committed tree through `git show` and has no business rewriting
    committed evidence, and a session whose shard carries a credential needs
    to know that rather than have it quietly fixed.

    The offending VALUE is never echoed, for the obvious reason. The field is
    named because that is what the writer can act on.

    TWO REASONS, TWO MESSAGES (`error-names-cause`). `_redact` has two
    branches and only one of them is about secret SHAPE: a record filed under
    a rule whose id contains `secret` — this repo's own `secrets-in-diff` — is
    rewritten wholesale whatever its text says. A single "carries a
    secret-shaped string" sentence therefore sends the author of a perfectly
    clean record hunting a credential that is not there. Which branch fired is
    derived from `_redact` itself rather than by restating its condition or
    its sentinel string: re-run it with an EMPTY rule id, and a text the
    scrubber then leaves alone is a text that was never secret-shaped.

    The question is asked of the WHOLE RECORD before it is answered, and that
    is the half round 1 caught. Under a `secret`-named rule every field is
    rewritten, so a field-by-field decision answered "the rule id" for
    `finding` and never looked at `evidence` — telling a writer whose record
    DID quote a credential that there was none to hunt. So the shape pass runs
    over all three fields first, and the rule-id message is reached only when
    none of them is secret-shaped.

    WHAT IT DOES NOT READ, said here because the envelope half above reads
    everything: on the record side this is `finding`, `evidence` and `reason`
    only. See the residue paragraph in `_attest_shard_problem`.
    """
    from . import memory as memory_mod

    envelope = {k: v for k, v in doc.items() if k != "records"}
    for field, value in sorted(envelope.items()):
        if memory_mod._redact_value(value) != value:
            return (f"carries a secret-shaped string in `{field}` that "
                    "`warden memory ingest` would have scrubbed before "
                    "committing it (`memory._redact_value`, applied to the "
                    "whole envelope). The corpus must never carry a live "
                    "credential whichever field carried it in, so a shard "
                    "that is not what ingest would have written is refused "
                    "rather than cleared — and rather than scrubbed, because "
                    "this check reads committed bytes and never rewrites them")
    for index, record in enumerate(doc.get("records") or []):
        rule_id = record.get("rule_id") or ""
        texts = [(f, record.get(f)) for f in ("finding", "evidence", "reason")]
        texts = [(f, t) for f, t in texts if isinstance(t, str) and t]
        # SHAPE FIRST, over the WHOLE record, and that ordering is the fix for
        # round 1's `error-names-cause` finding rather than a style choice.
        # `_redact` rewrites EVERY field of a `secret`-named rule's record, so
        # deciding the two reasons field by field reported the rule id for
        # `finding` and returned before it ever reached a credential quoted in
        # `evidence` — a message denying a secret that was there, which is the
        # same defect as the one that claimed a secret that was not.
        for field, text in texts:
            # `_redact` with an EMPTY rule id is `_has_secret_shape` asked
            # through the scrubber itself, so the shape question is derived
            # from the function and never a second copy of its condition.
            if memory_mod._redact("", text) != text:
                return (f"record {index} carries a secret-shaped string in "
                        f"`{field}` that `warden memory ingest` would have "
                        "scrubbed (`memory._redact`). A finding's own text is "
                        "where a quoted credential actually lands, and a "
                        "shard ingest would have rewritten is not a shard it "
                        "would have committed")
        for field, text in texts:
            # Nothing in this record is secret-shaped, so any rewrite left is
            # the rule id's doing and the whole record is clear of credentials.
            if memory_mod._redact(rule_id, text) != text:
                return (f"record {index} is filed under rule id `{rule_id}`, "
                        "which `warden memory ingest` redacts WHOLESALE: "
                        f"`memory._redact` rewrites the whole of `{field}` "
                        "for any rule whose id contains `secret`, whatever "
                        "the text says, so this is not the record ingest "
                        "would have committed. No field of this record "
                        "matches `memory._has_secret_shape` — the rule id is "
                        "the cause, so re-ingest the shard through `warden "
                        "memory ingest`, or re-file the record under the rule "
                        "that judged it; there is no credential here to hunt")
    return None


def _event_problem(doc: dict) -> str | None:
    """Why this shard is not a review EVENT — the shard-side reading of
    `memory._event_defects`, which `memory ingest` applies fail-closed on the
    findings-free path.

    The vocabularies are IMPORTED, never retyped: `_EVENT_FIELDS` is what
    makes a findings-free shard an event rather than an empty file, and
    `VERDICTS` comes from `attestation.schema.json`, so a verdict added there
    is legal here the moment it is legal anywhere. Only the FIELD RENAME is
    local — an artifact names the reviewed commit `head_sha` and a shard names
    it `sha` — and it is local because calling `_event_defects` on a shard
    reports "no head_sha" against every genuine one.
    """
    from . import memory as memory_mod
    from .attest import VERDICTS

    missing = [field for field in memory_mod._EVENT_FIELDS
               if not doc.get("sha" if field == "head_sha" else field)]
    if missing:
        spelled = ", ".join("sha" if f == "head_sha" else f for f in missing)
        return (f"names no {spelled}, so it is not a review EVENT — a shard "
                "that cannot say what was reviewed, against what, when, by "
                "whom and with what outcome is an empty file with a shard's "
                "name on it. `warden memory ingest` refuses to mint one "
                "(`memory._event_defects`), and `attest check` reads a "
                "verdict-less shard as unrecorded rather than blocking, so "
                "this is the shape that clears the freeze AND the gate")
    verdict = doc.get("verdict")
    if verdict not in VERDICTS:
        return (f"records `verdict: {verdict!r}`, which is not one of "
                + " | ".join(VERDICTS)
                + " — a review whose outcome no reader can classify is not "
                "the record of a review")
    return None


def _record_rule_id_problem(records: list, ruleset: "Path | None") -> str | None:
    """Re-apply `memory ingest`'s rule_id refusal to a committed shard's
    records, against the ruleset materialized from the merge base.

    `ruleset` is None when this diff carries no attestation path, in which case
    nothing calls this. It is never None-as-"skip the check": the caller
    refuses when the ruleset could not be materialized, because a check that
    could not look is not a check that passed.

    `attest._check_rule_ids` is THE definition, not a second one — the same
    function `memory.ingest` calls at its own seam (warden/memory.py, the
    sweep), so a shard this clears is one that seam would have produced.
    It reads `file` off every record it reports on; the record contract makes
    `file` optional, while the attestation schema REQUIRES it on every
    finding, so a record without one did not come from an attestation and is
    refused by name rather than as a KeyError three frames down.
    """
    if not records:
        return None
    for index, record in enumerate(records):
        if not record.get("file"):
            return (f"record {index} names no `file`, so it is not a finding "
                    "`warden attest write` could have produced — the "
                    "attestation schema requires one on every finding")
    from . import attest as attest_mod
    from .rules import RuleError

    def named(exc: Exception) -> str:
        """The message with the temp path the ruleset was materialized into
        swapped for what the reader can act on. That path is an
        implementation detail of this check and is DELETED by the time they
        read it, so naming it alone sends them to a directory that never
        existed in their repo (round 2, `error-names-cause`)."""
        return str(exc).replace(str(ruleset), "the rules dir at the merge base")

    try:
        attest_mod._check_rule_ids(records, ruleset)
    except attest_mod.AttestError as e:
        return (f"carries a rule_id `warden memory ingest` would refuse: "
                f"{named(e)}. A shard whose records name a rule the committed "
                "ruleset does not declare was not produced by ingest, and its "
                "records would reach the corpus, `memory stats` and "
                "certification E-02 unresolvable")
    except RuleError as e:
        # NOT the same failure and not the same remedy: `AttestError` means
        # the shard named a bad id, `RuleError` means the RULESET could not be
        # read — an empty or absent rules dir at the merge base, or a rule
        # file there that does not parse. `_materialize_committed_ruleset`
        # returns success for both (it copied what was there), so without this
        # the exception escaped to the CLI's blanket handler and the only
        # thing the reader was handed was the deleted temp path.
        return (f"could not be judged: the ruleset committed at the merge "
                f"base could not be read ({named(e)}). Refused rather than "
                "passed — a shard whose rule_ids nothing could resolve is not "
                "a shard whose rule_ids resolved")
    return None


def _materialize_committed_ruleset(root: Path, rev: str, rules_prefix: str,
                                   dest: Path) -> str:
    """Write the ruleset COMMITTED at `rev` into `dest`; "" or a problem.

    THE MERGE BASE, for the reason `_committed_policy` reads the policy there:
    it is the last commit the session did not write. Resolving ids against the
    HEAD ruleset would let a session commit a rule file beside a fabricated
    shard and have the id resolve against a declaration it wrote itself — the
    same shape the policy read closes one level up. A consumer whose
    `forbidden_paths` regex does not cover its own rules dir is exactly where
    that would otherwise be reachable.

    `-z` because a rule file may carry a newline in its name and a
    line-oriented listing would split one path into two that resolve to
    nothing.
    """
    from .diffs import DiffError, run_git

    try:
        listing = run_git(root, "ls-tree", "-r", "--name-only", "-z", rev,
                          "--", rules_prefix)
    except DiffError as e:
        return (f"the ruleset committed at {rev} could not be listed ({e}), "
                "so a shard's rule_ids cannot be resolved against it")
    for rule_path in (p for p in listing.split("\0") if p.strip()):
        if not rule_path.endswith(".md"):
            continue
        blob = _show(root, rev, rule_path)
        if blob is None:
            return (f"the rule file {rule_path} could not be read at {rev}, "
                    "so the ruleset a shard is judged against is incomplete")
        out = dest / rule_path[len(rules_prefix):]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(blob)
    return ""


def carve_out_problems(root: Path, paths, *, base: str, head: str = "HEAD"
                       ) -> list[str]:
    """Why each of `paths` is NOT inside the autonomy-ladder carve-out.

    An empty list means every path is inside it: every FORBIDDEN path the
    caller handed over writes only what `.warden/skills-policy.md` permits the
    machine tier to write, and the caller (the cage runner) may let the PR
    stand for a human to merge instead of closing it. A non-empty list is the
    reason it may not, one line per problem.

    THE SCOPE, stated because a caller's report will quote it: this judges the
    paths it is GIVEN — the runner's forbidden-path matches — not the whole
    diff. The rest of the diff is ordinary session work that the post-check
    never objected to, and nothing here REPORTS on it.

    A RULE CHANGE's rule-to-artifact link does, however, BIND against the whole
    committed diff, and the two are different things. A consumer's
    `forbidden_paths` regex and its `review.rules_dir` are independent config,
    and NOTHING validates that the two agree: both `warden autonomy pause` and
    `warden autonomy adopt` write the rule to the declared dir and the artifact
    to `.warden/memory/backtests/`, so a regex covering one and not the other
    hands over half the change. Binding only against the handed half would make
    the checker refuse an artifact whose rule WAS changed and a rule change
    whose artifact WAS committed — on BOTH arms, auto-closing the
    one PR shape the carve-out exists to let stand. So the link is looked up in
    the committed diff on both arms, while a PROBLEM is still only ever
    reported for a path the caller named.

    WHAT IT VERIFIES, and what it does not. A shard under
    `.warden/memory/attest/` is judged on its own by `_attest_shard_problem`,
    whose docstring holds that arm's whole argument and its two narrowings.
    For the rule arms it verifies the SHAPE of the write
    and the self-consistency of the artifact beside it: that a modified rule
    file is its base bytes plus exactly the writer's pause insertion, that an
    added rule parses and is non-blocking, that a pause is of a non-blocking
    rule and carries a `pause` artifact naming it with DECLARED counts — the
    counts are checked for shape and against the ladder's streak bar, never
    recomputed from the corpus, so "declared" is the honest word. It
    does NOT re-derive the corpus justification — the refutation streak and the
    UNANSWERED-ceiling guard that `warden autonomy pause` applies before it
    writes — because those read the committed corpus at the BASE ruleset and
    the check runs against a worktree whose corpus dir is session-writable.
    That justification is the reviewing human's, on a PR that exists precisely
    so a human sees it. Saying so is the point: a carve-out that implied it had
    re-derived permission would be the enforcement claim this repo files as a
    defect.

    FAIL CLOSED, everywhere. An unresolvable policy, an unreadable blob, a git
    command that will not run, a path the diff does not contain, a file whose
    shape cannot be re-derived — each is a problem, never a pass. The caller's
    default is refusal, and this only ever removes it.
    """
    from .diffs import DiffError, run_git

    wanted = list(dict.fromkeys(p.strip() for p in paths if p.strip()))
    if not wanted:
        return ["no paths given — nothing to clear, and an empty carve-out "
                "claim must not read as a clean one"]

    # The MERGE BASE, resolved once and used for the file list, every base blob
    # read, AND the policy. `git diff A...B` is already three-dot — it compares
    # the merge base to B — so reading base blobs from the TIP of `base`
    # would compare a different pair. In the unattended loop that is the
    # normal case, not the exotic one: main advances during a run, someone
    # edits the very rule the session paused, and the reconstruction fails
    # against bytes the session never saw. That fails CLOSED, so nothing
    # unsafe gets through — but it auto-closes legitimate autonomy PRs and
    # tells the reader something rode along that had not.
    try:
        merge_base = run_git(root, "merge-base", base, head).strip()
        status_out = run_git(root, "diff", "--name-status",
                             f"{merge_base}..{head}")
        # The same range the file list came from, as commit shas. An
        # attestation shard is cleared only when the commit it attests is one
        # of these — the binding that makes it THIS branch's review record
        # rather than one imported from elsewhere. Read from `merge_base` for
        # the reason the blobs are: `A...B` is the merge base to B, so a
        # rev-list off the TIP of `base` would enumerate a different range.
        range_shas = frozenset(
            line.strip()
            for line in run_git(root, "rev-list",
                                f"{merge_base}..{head}").splitlines()
            if line.strip())
    except DiffError as e:
        return [f"the diff {base}...{head} could not be read ({e}) — the "
                "carve-out is decided from the committed diff, so an "
                "unreadable one refuses"]
    if not merge_base:
        return [f"{base} and {head} have no merge base — there is no committed "
                "diff to judge the carve-out from"]

    # Policy from the MERGE BASE, not from head. Reading it at head would leave
    # a committed hole: a session can commit
    # `blocking_severities: [LOW]` and a `severity: HIGH` rule in the same
    # commit, and the rule then reads as non-blocking against the policy the
    # session itself just wrote. `repo\.yaml$` in forbidden_paths would hold
    # that shut only by config, not by this checker. The merge base is the last commit the
    # session did not write, so it is the only honest place to read the bound
    # the session is being judged against.
    rules_prefix, blocking, policy_problem = _committed_policy(root, merge_base)
    if policy_problem:
        return [policy_problem]
    status: dict[str, str] = {}
    for line in status_out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            # a rename/copy names the DESTINATION last; key on it
            status[parts[-1]] = parts[0][:1]

    problems: list[str] = []
    paused_in_diff: set[str] = set()
    added_in_diff: set[str] = set()
    backtest_paths: list[str] = []
    attest_paths: list[str] = []

    def rule_id_of(path: str) -> str:
        return path[len(rules_prefix):-len(".md")]

    # Every adoption the COMMITTED DIFF contains, whether or not the caller
    # handed the rule file over -> its declared severity. This is the binding
    # side of the scope split in the docstring: `added_in_diff` (handed) is
    # what carries an OBLIGATION, `adopted_in_commit` is what an artifact may
    # be BOUND to. Severity comes from the rule the loader parsed, never from a
    # second frontmatter read — a BOM-prefixed rule loads there and not here,
    # and the mismatch would refuse `warden autonomy adopt`'s own output.
    adopted_in_commit: dict[str, str] = {}
    for path, state in sorted(status.items()):
        if (state == "A" and path.startswith(rules_prefix)
                and path.endswith(".md")):
            src = _show(root, head, path)
            if src is None:
                continue
            rid = rule_id_of(path)
            problem, severity = _adoption_problem(src, rid, blocking)
            if problem is None and severity:
                adopted_in_commit[rid] = severity

    # Every PAUSE the COMMITTED DIFF contains, whether or not the caller handed
    # the rule file over. The pause mirror of `adopted_in_commit`:
    # `paused_in_diff` (handed) is what carries an
    # OBLIGATION, this is what an artifact may be BOUND to. A SET, not a map,
    # because `_pause_artifact_problem` binds on the rule id alone — a pause
    # artifact declares no severity for the committed file to disagree with.
    # `merge_base`, never `base`: `_pause_edit_problem` re-derives the pause
    # from the BASE blob, and reading that from the tip of `base` compares a
    # pair the session never saw.
    paused_in_commit: set[str] = set()
    for path, state in sorted(status.items()):
        if (state == "M" and path.startswith(rules_prefix)
                and path.endswith(".md")):
            if _pause_edit_problem(root, path, merge_base, head,
                                   blocking) is None:
                paused_in_commit.add(rule_id_of(path))

    # Pass 1 — rule files. Their outcome is what a backtest may then reference.
    for path in wanted:
        state = status.get(path)
        if state is None:
            problems.append(
                f"{path}: not changed in {base}...{head} — the carve-out is "
                "judged from the committed diff, and a path outside it cannot "
                "be cleared")
            continue
        if path.startswith(BACKTEST_SUBDIR + "/"):
            backtest_paths.append(path)
            continue
        # The attestation arm stands ALONE — no pass-2/pass-3 obligation runs
        # over it. A backtest is in the carve-out only as the artifact a rule
        # change carries, so it binds to one; a shard carries nothing and
        # nothing carries it. Its binding is to the RANGE, applied in pass 1b,
        # which is separate only so the merge-base ruleset its rule_id check
        # needs is materialized ONCE per call rather than once per shard.
        if path.startswith(ATTEST_SUBDIR + "/"):
            attest_paths.append(path)
            continue
        if not (path.startswith(rules_prefix) and path.endswith(".md")):
            problems.append(
                f"{path}: outside the carve-out. The machine tier may write "
                f"only a pause field in an existing {rules_prefix}*.md, a new "
                f"non-blocking rule there, its backtest under "
                f"{BACKTEST_SUBDIR}/, or the attestation shard a review round "
                f"leaves under {ATTEST_SUBDIR}/ — nothing else under "
                ".warden/, and none of repo.yaml, the schemas, mechanical.py "
                "or warden/certification/")
            continue
        rid = rule_id_of(path)
        if state == "A":
            src = _show(root, head, path)
            if src is None:
                problems.append(f"{path}: added, but its committed blob could "
                                f"not be read at {head}")
                continue
            problem, _severity = _adoption_problem(src, rid, blocking)
            if problem:
                problems.append(f"{path}: {problem}")
                continue
            added_in_diff.add(rid)
        elif state == "M":
            problem = _pause_edit_problem(root, path, merge_base, head,
                                          blocking)
            if problem:
                problems.append(f"{path}: {problem}")
                continue
            paused_in_diff.add(rid)
        else:
            problems.append(
                f"{path}: {state!r} is not a carve-out change — the machine "
                "tier adds a rule or pauses one; it never deletes or renames "
                "a rule file")

    # Pass 1b — attestation shards. Separate from pass 1 only so the
    # merge-base ruleset their rule_id check resolves against is materialized
    # ONCE, and only when a shard is actually handed over. A failure to
    # materialize it is a PROBLEM on every shard, never a skipped check: a
    # check that could not look is not a check that passed.
    if attest_paths:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="warden-carve-out-") as tmp:
            ruleset = Path(tmp)
            ruleset_problem = _materialize_committed_ruleset(
                root, merge_base, rules_prefix, ruleset)
            for path in attest_paths:
                if ruleset_problem:
                    problems.append(f"{path}: {ruleset_problem}")
                    continue
                problem = _attest_shard_problem(root, path, status.get(path),
                                                head, range_shas, ruleset)
                if problem:
                    problems.append(f"{path}: {problem}")

    # Pass 2 — backtests, which are only in scope as the artifact a rule change
    # in THIS diff carries. An artifact alone widens .warden/ writes for free.
    # Artifacts ACCEPTED here are collected as evidence for pass 3; a rejected
    # one must never satisfy the obligation it failed.
    evidenced_pauses: set[str] = set()
    evidenced_adoptions: set[str] = set()
    for path in backtest_paths:
        if status.get(path) != "A":
            problems.append(f"{path}: only a NEW backtest artifact is in the "
                            "carve-out; an edited or deleted one is not")
            continue
        raw = _show(root, head, path)
        if raw is None:
            problems.append(f"{path}: its committed blob could not be read at "
                            f"{head}")
            continue
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as e:
            problems.append(f"{path}: not readable as a backtest record ({e})")
            continue
        # `pause` and `adopt` ONLY, and each BOUND to the rule change this diff
        # actually makes. The enum certification accepts is wider
        # (certify.BACKTEST_ACTIONS), but the writer this check mirrors emits
        # exactly two shapes: `_write_pause_backtest` writes `pause` and
        # `_write_adopt_backtest` writes `adopt`. Accepting the wider enum
        # would let a session ship a fabricated `promote` artifact — the
        # record S-05 and the retro read as "this rule was measured" — through
        # the gate-surface freeze beside a benign non-blocking rule. Accepting
        # `adopt` does not reopen that:
        # an adopt artifact is accepted only for a rule THIS DIFF ADDED, with
        # a shape that can declare nothing it did not derive.
        action = doc.get("action") if isinstance(doc, dict) else None
        if action not in ("pause", "adopt"):
            problems.append(
                f"{path}: only an `action: pause` or `action: adopt` artifact "
                "is in the carve-out — those are the only backtests `warden "
                "autonomy` writes, and a wider enum here would carry "
                "fabricated promotion evidence past the freeze")
            continue
        rid = doc.get("rule_id")
        if action == "pause":
            if rid not in paused_in_commit:
                problems.append(
                    f"{path}: names rule {rid!r}, which this diff did not "
                    "pause — a pause backtest is in the carve-out as the "
                    "artifact a PAUSE carries, never on its own and never "
                    "beside some other rule change")
                continue
            bad = _pause_artifact_problem(doc)
            if bad:
                problems.append(f"{path}: {bad}")
                continue
            evidenced_pauses.add(rid)
        else:
            if rid not in adopted_in_commit:
                problems.append(
                    f"{path}: names rule {rid!r}, which this diff did not "
                    "add — an adoption backtest is in the carve-out as the "
                    "artifact an ADOPTION carries, never on its own and never "
                    "beside some other rule change")
                continue
            bad = _adopt_artifact_problem(doc, adopted_in_commit[rid])
            if bad:
                problems.append(f"{path}: {bad}")
                continue
            evidenced_adoptions.add(rid)

    # Pass 3 — a rule change must carry its evidence, which is the ladder's own
    # text ("the pause/adopt backtest artifact that change carries") and what
    # certification S-05 demands of it. Adding a rule file bumps
    # rules_version exactly as pausing one does, so an adoption with no
    # artifact produces a tree that FAILS S-05 — the cage must not leave such a
    # PR open as if it were the shape `warden autonomy` writes.
    # A pause's evidence is looked for in the COMMITTED DIFF, not only among
    # the handed paths — the same scope split the adoption arm carries. An
    # artifact already reported as a problem above still does not count.
    evidenced_pauses_in_commit = evidenced_pauses | {
        rid for rid, _ in _committed_pause_evidence(
            root, head, status, paused_in_commit, set(backtest_paths))}
    for rid in sorted(paused_in_diff - evidenced_pauses_in_commit):
        problems.append(
            f"{rules_prefix}{rid}.md: paused with no pause backtest for "
            f"{rid!r} added under {BACKTEST_SUBDIR}/ in the same diff — the "
            "carve-out covers a pause AND the artifact it carries, and "
            "`warden autonomy pause` writes both")
    # An adoption's evidence is looked for in the COMMITTED DIFF, not only
    # among the handed paths — see the docstring's scope split. An artifact
    # already reported as a problem above does not count: a rejected file must
    # not satisfy the obligation it failed, so the message says the diff
    # carries no VALID one rather than none at all.
    evidenced_in_commit = evidenced_adoptions | {
        rid for rid, _ in _committed_adopt_evidence(
            root, head, status, adopted_in_commit, set(backtest_paths))}
    for rid in sorted(added_in_diff - evidenced_in_commit):
        problems.append(
            f"{rules_prefix}{rid}.md: adopted, and this diff adds no VALID "
            f"adoption backtest for {rid!r} under {BACKTEST_SUBDIR}/ — adding "
            "a rule bumps rules_version, so certification S-05 wants an "
            "artifact naming the version the addition produces; the carve-out "
            "covers an adoption AND the artifact it carries, and `warden "
            "autonomy adopt` writes both. If an artifact for it IS in the "
            "diff, the reason it was not accepted is reported above")

    return problems


def _committed_pause_evidence(root: Path, head: str, status: dict,
                              paused: set, already_judged: set):
    """Every (rule_id, path) the COMMITTED DIFF adds as a valid pause backtest.

    VALID is the load-bearing word, and it is re-derived here rather than
    assumed: a committed artifact satisfies the pass-3 obligation only if
    `_pause_artifact_problem` clears it and it names a rule this diff actually
    paused. Existence is not evidence — a two-key stub committed beside a
    pause, and never handed to the check, must leave the pause unevidenced
    exactly as a handed one does.

    Two conditions here are SYMMETRY with `_committed_adopt_evidence` rather
    than guards closing a reachable hole, and both are labelled as such because
    a comment that implies otherwise is the claim this repo audits for:

    - `already_judged` skips paths the caller handed over, whose pass-2 verdict
      is authoritative. Pass 2 and this generator apply the same two tests, so
      today they cannot disagree.
    - `rid in paused` cannot change an outcome either: `paused_in_diff` is a
      subset of `paused_in_commit` by construction and pass 3 only ever
      subtracts from `paused_in_diff`, so an artifact naming an unpaused rule
      has no obligation to satisfy. Mutating it away leaves the suite green
      — it is kept because the generator is written to be
      correct standing alone, not because a test can currently tell.

    The pause mirror of `_committed_adopt_evidence`. `paused`
    is a SET, not a map: a pause artifact is bound by rule id alone, because it
    declares no severity for the committed rule file to disagree with.
    """
    for path, state in sorted(status.items()):
        if (state != "A" or path in already_judged
                or not path.startswith(BACKTEST_SUBDIR + "/")):
            continue
        raw = _show(root, head, path)
        if raw is None:
            continue
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(doc, dict) or doc.get("action") != "pause":
            continue
        rid = doc.get("rule_id")
        if rid in paused and not _pause_artifact_problem(doc):
            yield rid, path


def _committed_adopt_evidence(root: Path, head: str, status: dict,
                              adopted: dict, already_judged: set):
    """Every (rule_id, path) the COMMITTED DIFF adds as a valid adoption
    backtest, skipping paths the caller already handed over (those were judged
    in pass 2 and their verdict is authoritative — re-judging one here would
    let a rejected artifact satisfy the obligation it failed)."""
    for path, state in sorted(status.items()):
        if (state != "A" or path in already_judged
                or not path.startswith(BACKTEST_SUBDIR + "/")):
            continue
        raw = _show(root, head, path)
        if raw is None:
            continue
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(doc, dict) or doc.get("action") != "adopt":
            continue
        rid = doc.get("rule_id")
        if rid in adopted and not _adopt_artifact_problem(doc, adopted[rid]):
            yield rid, path


def _adopt_artifact_problem(doc: dict, severity: str | None) -> str | None:
    """Why this adoption artifact is not the record `_write_adopt_backtest`
    writes.

    The mirror of `_pause_artifact_problem`, and stricter in one direction on
    purpose: an adoption may declare NOTHING it could not have derived. At
    adoption time the rule has produced no findings, so there is no judged
    history, and this check cannot re-derive one from a corpus dir the session
    can write. A nonzero tally here is a promotion-shaped claim — the record
    S-05 and the retro read as "this rule was measured" — which is exactly the
    claim refused under the `promote` name. `judged: 0` is
    the honest declaration and the only accepted one.

    `severity` is the COMMITTED rule file's own, so the artifact cannot record
    an adoption the tree does not show: a backtest disagreeing with the rule it
    names is "evidence for a change the tree does not contain", the shape
    S-05's pause arm already refuses. None means pass 1 could not read one,
    which refuses — this check never guesses the thing it is binding to.
    """
    judged = doc.get("judged")
    if isinstance(judged, bool) or not isinstance(judged, int) or judged != 0:
        return (f"its 'judged' is {judged!r} — an adoption has no judged "
                "history (the rule has produced no findings yet) and this "
                "check cannot re-derive one, so `warden autonomy adopt` "
                "writes 0 and anything else is a measurement claim nothing "
                "backs")
    # `judged: 0` beside a fabricated precision tally is the same claim in
    # another field. The writer
    # omits these entirely, so their presence is not the shape it emits.
    for field in ("upheld", "refuted", "dismissed", "rate", "wilson_lb"):
        if field in doc:
            return (f"it declares {field!r} — an adoption records no precision "
                    "tally at all (the rule has produced no findings and an "
                    "engine:claude rule cannot be replayed over the tree), so "
                    "`warden autonomy adopt` omits these fields and a value "
                    "here is a promotion-shaped claim nothing backs")
    if not (isinstance(doc.get("rules_version"), str)
            and doc["rules_version"].strip()):
        # SHAPE ONLY, and the pass line says so: this check cannot recompute
        # the ruleset hash from a worktree the session can write, so it cannot
        # tell a CURRENT version from a stale or invented one. What it can
        # refuse is an artifact that names no ruleset at all, which cannot be
        # the record `warden autonomy adopt` writes.
        return ("it names no `rules_version` — an adoption backtest is bound "
                "to the ruleset its rule change produces, and one naming no "
                "ruleset cannot be the record `warden autonomy adopt` writes")
    if not (isinstance(doc.get("note"), str) and doc["note"].strip()):
        return ("it records no `note` — an adoption artifact carries the "
                "reason the rule was adopted, and an empty one is not a "
                "record")
    declared = doc.get("severity")
    if not severity:
        return ("the severity of the rule it names could not be read from the "
                "committed file, so the artifact cannot be bound to it")
    if declared != severity:
        return (f"declares severity {declared!r} while the committed rule "
                f"file declares {severity!r} — a backtest that disagrees with "
                "the rule it names is evidence for a change the tree does not "
                "contain")
    return None


def _pause_artifact_problem(doc: dict) -> str | None:
    """Why this pause artifact is not the record `_write_pause_backtest` writes.

    Not a re-derivation of the corpus — see `carve_out_problems`' docstring for
    why that is out of reach here — but a refusal of the two-key stub. A file
    reading `{"action": "pause", "rule_id": "X"}` must not satisfy this
    carve-out or certification S-05, or the "recorded reason" the ladder makes
    a pause conditional on costs nothing to fabricate.
    Booleans are excluded explicitly: `isinstance(True, int)` is True in Python,
    and `"judged": true` is not a case count.
    """
    for field in ("judged", "refuted", "streak"):
        value = doc.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return (f"its {field!r} is {value!r} — a pause artifact carries the "
                    "counts the pause was derived from (judged, refuted, "
                    "streak), and `warden autonomy pause` writes all three")
    from .memory import PAUSE_STREAK
    if doc["streak"] < PAUSE_STREAK:
        return (f"its streak is {doc['streak']} — the ladder makes a pause "
                f"machine-eligible on {PAUSE_STREAK} consecutive refuting "
                "review ROUNDS, so a smaller streak is a pause the machine "
                "tier may not take")
    if not (isinstance(doc.get("note"), str) and doc["note"].strip()):
        return ("it records no `note` — the ladder makes a pause conditional "
                "on a recorded reason, and an empty one is not a record")
    return None


def _adoption_problem(src: str, rule_id: str, blocking: frozenset
                      ) -> tuple[str | None, str | None]:
    """(problem, severity) for this added rule file — problem None when it is a
    machine adoption, and severity the one the LOADER read.

    The severity refusal is RE-DERIVED from the committed bytes rather than
    trusted: `adopt_rule` made the same refusal when it wrote the file, but the
    file reaching a PR is what the cage judges, and between the two a session
    could have edited it.

    The severity is RETURNED rather than re-read by the caller: a caller taking
    it from `_frontmatter_of`, which requires the first line to be exactly
    `---`, would disagree with the loader, which reads with `utf-8-sig`. A
    BOM-prefixed rule would PARSE here and have no severity there, so the
    artifact could not be bound and the carve-out would refuse `warden
    autonomy adopt`'s own output. One parser, one answer.
    """
    import tempfile

    from . import rules as rules_mod

    with tempfile.TemporaryDirectory() as td:
        (Path(td) / f"{rule_id}.md").write_text(src)
        try:
            parsed = rules_mod.load_rules(Path(td))
        except rules_mod.RuleError as e:
            return f"added, but it does not parse as a rule ({e})", None
    if not parsed:
        return "added, but no rule parsed from it", None
    rule = parsed[0]
    if rule.id != rule_id:
        return (f"added as {rule_id!r} but declares id {rule.id!r} — the gate "
                "loads rules by file, and the mismatch hides which one landed",
                None)
    if rule.severity in blocking:
        return (f"severity {rule.severity} is in blocking_severities "
                f"{sorted(blocking)} — a machine adoption may only ADD "
                "reporting, never raise enforcement", None)
    raised = _enforcement_field_problem(rule)
    if raised:
        return raised, None
    return None, rule.severity


def _enforcement_field_problem(rule) -> str | None:
    """The sentence refusing a rule that raises enforcement through a
    frontmatter field rather than its severity, or None when it declares
    neither. ONE helper for two seams: `adopt_rule` (the writer) raises it
    and `_adoption_problem` (the reader) returns it, so the draft the writer
    refuses is exactly the tree the reader would refuse, in the same words.

    Severity is not the only way a rule raises enforcement; there are two
    others.

    `covers:` is the sharper one: an UNPAUSED rule that covers a slug makes
    `attest._RuleIndex.answered` REFUSE `unmapped:<slug>` at the WRITE seam,
    so a covers-bearing adoption starts rejecting attestations that were
    legal a commit earlier — enforcement raised, at a severity that cannot
    fail a build, which is exactly the gap the severity check leaves.

    `implements:` credits a guardrail-catalog entry as ENFORCED, removing it
    from `warden rules recommend`'s UNANSWERED list — the count CI exits 1
    above. A machine adoption could flip that job red to green with nobody
    having decided the entry is answered.

    Both are legitimate things for a rule to declare. They are decisions a
    person makes, so a diff carrying them is a human's PR: refused at both
    seams, auto-closed by the post-check exactly as before.
    """
    for field, why in (
            ("covers", "makes `attest write` refuse `unmapped:<slug>` for every "
                       "slug it names — enforcement raised at the write seam, "
                       "whatever the severity"),
            ("implements", "credits a guardrail-catalog entry as ENFORCED and "
                           "removes it from the UNANSWERED count CI gates on")):
        declared = getattr(rule, field, None)
        if declared:
            return (f"declares {field}: {sorted(declared)}, which {why}. A "
                    "machine adoption may only ADD reporting; deciding a class "
                    "is answered is a person's call")
    return None


def _pause_edit_problem(root: Path, path: str, base: str, head: str,
                        blocking: frozenset) -> str | None:
    """Why this modified rule file is not a pure pause edit, or None.

    Decided by RE-DERIVATION, not by inspection: take the file's base bytes,
    apply the writer's own insertion (`with_pause_frontmatter`) with the reason
    the head version carries, and require the result to equal the head bytes
    exactly. Nothing else can ride along — not a body edit, not a severity
    move, not a reflowed frontmatter — because any other byte makes the
    reconstruction differ.

    And a BLOCKING rule is out of scope, deliberately narrower than the ladder
    text. A pause is machine-eligible because it "only ever makes the gate
    quieter", which `warden autonomy pause` earns with a streak check and the
    UNANSWERED-ceiling guard that this check cannot re-derive. Without those,
    the reachable abuse is precise: a session pauses the one BLOCKING rule that
    would have failed its own diff, and — rules being read from the PR's own
    tree — the gate runs quiet on the very change.
    Refusing a blocking-severity pause removes that without touching the case
    the ladder was written for: every covers-bearing rule ships non-blocking,
    so the machine tier's real pauses are unaffected. A blocking pause is still
    perfectly legal — it is simply a human's PR, auto-closed here as before.
    """
    before = _show(root, base, path)
    after = _show(root, head, path)
    if before is None:
        return f"its base blob could not be read at {base}"
    if after is None:
        return f"its committed blob could not be read at {head}"
    # The writer reads with utf-8-sig and writes plain utf-8, so a pause of a
    # BOM-prefixed rule file DROPS the BOM. Re-derive from the same starting
    # bytes the writer saw, or that one legal pause would fail to reconstruct.
    before = before.lstrip("﻿")
    if _paused_reason_of(before) is not None:
        return ("was already paused at the base — the carve-out covers taking "
                "a rule from running to paused, not editing a pause")
    reason = _paused_reason_of(after)
    if reason is None:
        return ("modified without becoming paused — the only rule-file edit "
                "in the carve-out is adding `paused: true` with a "
                "`paused_reason:`")
    try:
        expected = with_pause_frontmatter(before, reason, where=path)
    except AutonomyError as e:
        return f"its base version cannot carry a pause ({e})"
    if expected != after:
        return ("changed more than the pause — re-deriving the pause from the "
                "base version does not reproduce the committed bytes, so "
                "something else rode along in the same edit")
    # Severity read from the BASE version: the reconstruction above already
    # proves head and base agree on it, and base is the ruleset the pause is
    # being taken against.
    severity = (_frontmatter_of(before) or {}).get("severity")
    if severity in blocking:
        return (f"is a {severity} rule, and {severity} is in "
                f"blocking_severities {sorted(blocking)} — pausing a BLOCKING "
                "rule quiets a check that can fail a build, including on this "
                "PR's own gate run, and this check cannot re-derive the streak "
                "and ceiling that make a pause safe. That pause is a human's "
                "PR")
    return None
