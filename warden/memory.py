"""Review memory: ingest/recall/stats.

Design of record: docs/design/memory.md. At structured, grep-scale volume the
append-only event log IS the graph — no engines, $0, no ingestion APIs.

Store:
- Sharded, committed events: one JSON per review event at
  .warden/memory/attest/<utc-ts>-<sha8>.json. Distinct filenames = zero
  merge conflicts across concurrent sessions; survives CI-artifact expiry.
- A shard is a review EVENT (sha, base_sha, rules_version, reviewed_at,
  reviewers, verdict) that CARRIES records — not a container that only exists
  because there were findings. A clean round files a findings-free shard:
  it is what `attest check` reads, and it is why "how many reviews came back
  clean" is answerable at all. It adds no record, so it never touches recall,
  a rule's n, precision or the Wilson bound.
- Derived, gitignored cache .warden/memory/findings.jsonl, rebuilt by
  `memory ingest`, idempotent by content-hash id.
- A record's id names the EVENT (it hashes the head sha); the FINDING it
  states has a second, content-derived identity (`finding_identity`), and
  every seam that counts folds by it (`fold_restatements`): a finding
  re-attested after a repair round moved the head counts once, the later
  shard's disposition wins, and no committed shard is ever rewritten to
  achieve that.
- Secrets are REDACTED at the shard boundary — the committed corpus must
  never contain a live credential, planted or real.
- rule_id is RESOLVED at the same boundary, by attest's own check (imported,
  not copied). The promotion gate and candidate ranking read the corpus, not
  the CLI, so an id `attest write` refuses must not reach a shard through an
  artifact warden did not write.
- NOT hashed into rules_version: memory is context, never policy.

Recall is SPLIT by role — disjoint priors decorrelate the judges:
- reviewer: confirmed|fixed PATTERN priors ("recurred Nx here — verify,
  don't assume"); never candidate findings, and `fixed` is never injected
  as an open instance.
- examiner: refuted|dismissed PRECEDENTS as non-binding case law (kills the
  re-litigation tax).

Stats measure PRECISION ONLY — recall is unobservable; escapes are mined
separately by the retro. And precision is over FILED judgments: the
attestation is written after adjudication, so a candidate the examiner
refuted reaches the corpus only if the protocol files it — the pre-pr-review
skill now does, and `review_events` counts how many rounds did, so the
render can name the bias beside the numbers it biases.

Evidence-dir convention: `ingest` mutates state and writes a run manifest;
`recall` and `stats` are pure reads and do not.
"""

from __future__ import annotations

import hashlib
from datetime import datetime as _dt
import json
import math
import os
import re
import stat
import subprocess
from datetime import datetime, timezone
from collections.abc import Callable
from pathlib import Path

# Reused VERBATIM (import, not copy) — the gate's secret patterns and the
# ingest scrub must never drift apart.
from .mechanical import _SECRET_PATTERNS as _GATE_SECRET_PATTERNS
# Likewise VERBATIM: `attest write` guards the WRITING end of the pipe, this
# guards the READING end, and the promotion gate reads the corpus — not the
# CLI. Two copies of one policy rule would drift.
from .attest import (ANCESTRY_STATES, AttestError, CLOSURE_STATES, OUTCOMES,
                     ROSTER_STATES, ROSTER_VERIFIED, ROUND_STATES,
                     UNMAPPED_PREFIX, lens_is_valid,
                     VERDICTS, _check_rule_ids)
# env: a hook-exported GIT_DIR would retarget the reachability probe at the
# invoking repo instead of `root` — the same trap diffs.run_git documents.
from .runs import git_env

MEMORY_DIRNAME = "memory"
SHARD_SUBDIR = "attest"
GATE_SUBDIR = "gate"
CACHE_NAME = "findings.jsonl"

RECALL_K = 10           # max records injected
# A rendered recall line carries a bounded EXCERPT of the finding text, never
# the whole paragraph, and the budget is DERIVED from RECALL_K such lines, so
# one long finding cannot spend the budget alone and push its NEIGHBOURS out
# of recall. The KEY is not cut: a rule id and a dir_prefix are never
# truncated and nothing caps their width, so K lines fit whenever every key
# sits inside _KEY_ALLOWANCE. A wider key spends the slack the other
# allowances leave, and a line that does not fit is skipped, never a stop —
# and the skip is REPORTED: `recall` returns a `RecallSet` carrying how many
# records were relevant and how many lines were skipped, and the header
# prints both, so a recall thinned by wide keys never reads as an empty
# corpus. Every line ends with the record's id, the one grep key that
# survives the cache's ensure_ascii JSON; the header names the cache once.
RECALL_EXCERPT = 200    # characters of finding text per rendered line
_HEADER_ALLOWANCE = 220   # render_recall header, at five-digit counts
_SUFFIX_ALLOWANCE = 24    # worst-case " [recurred Nx here]" per line
_KEY_ALLOWANCE = 96       # "- [<rule_id> @ <dir_prefix>] " — keys are never cut
_NOTE_ALLOWANCE = 64      # " (since FIXED — verify the pattern, do not report as open)\n"
_ID_ALLOWANCE = 20        # " id=<16 hex>"
_LINE_ALLOWANCE = (RECALL_EXCERPT + _KEY_ALLOWANCE + _NOTE_ALLOWANCE
                   + _ID_ALLOWANCE + _SUFFIX_ALLOWANCE)
RECALL_BUDGET = _HEADER_ALLOWANCE + RECALL_K * _LINE_ALLOWANCE  # rendered characters
# Memory without a lifecycle grows until old findings contradict new ones and
# the reader is deciding from a pile of half-truths. Two horizons, both soft:
# past FRESH_DAYS a record is de-ranked (it still exists, it just stops
# outranking current evidence); past STALE_DAYS it is no longer recalled at
# all. Nothing is deleted — the shards are committed evidence, and evidence
# does not expire just because attention does.
FRESH_DAYS = 90
STALE_DAYS = 365
PROMOTE_MIN_N = 10
PROMOTE_WILSON_LB = 0.7
PAUSE_STREAK = 3
# The micro-test protocol's rep floor (adopted from
# superpowers' validation method). The Wilson bar above says WHEN a rule may
# be promoted; this says what EVIDENCE the proposal carries: a no-guidance
# control arm (control does not exhibit the failure -> stop, nothing to fix),
# MICROTEST_MIN_REPS+ reps per variant, variance reported as a metric, and
# every flagged match manually read rather than trusted from automated
# counts. Certification S-05 refuses a promote-action backtest at the
# current rules_version that carries neither this evidence nor an explicit
# {status: unvalidated, reason} marker.
MICROTEST_MIN_REPS = 5
# CANDIDATE bars — deliberately NOT the promotion bars above.
# A declared rule's promotion is "automate this rule": a deterministic checker
# that auto-rejects, hashed into rules_version. That is expensive to be wrong
# about, so it costs 10 judged cases and a 0.7 confidence floor.
# A candidate has no rule to automate. Its promotion is "WRITE this rule" — a
# prose lens, founder-merged, pausable, that rejects nothing by itself. Same
# cheap-and-reversible class of action as a pause, so it gets the same
# three-strikes arithmetic: recurrence is the signal, precision only filters
# noise. PROMOTE_WILSON_LB is calibrated for the automation decision; applied
# here it would silence a loud class at small n (10 of 11 upheld has a Wilson
# LB of 0.623). The floor is still PRINTED on every candidate the
# report calls out individually (see _render_candidates for what the
# sub-threshold roll-up deliberately omits); it just does not gate an action
# it was not designed to gate.
CANDIDATE_MIN_N = 3
CANDIDATE_MIN_RATE = 0.5   # raw upheld/judged, strictly greater

_REVIEWER_STATUSES = {"confirmed", "fixed", "detected"}
_EXAMINER_STATUSES = {"refuted", "dismissed-with-reason"}
# A gate finding is a deterministic checker firing — a FACT, not a judgment.
# It is a legitimate reviewer prior ("this fired here before") but it must
# never feed precision math: a rule that is already deterministic cannot be
# "promoted" to deterministic, and counting its hits as confirmations would
# manufacture a perfect score out of tautology.
_JUDGED_STATUSES = {"confirmed", "fixed", "refuted", "dismissed-with-reason"}

# Belt-and-braces text scrub: the gate's patterns plus a looser unquoted-
# assignment catch-all. Over-redaction is safe; under-redaction is the
# failure mode.
_LOOSE_SECRET_RE = re.compile(
    r"""(?ix) \b (?:api[_-]?key|secret|token|password|credential|bearer)\b
        \s* [:=]\s* \S+ | \b(?:sk|pk|gho|xox[abp])[-_][A-Za-z0-9_-]{8,}""")


def _has_secret_shape(text: str) -> bool:
    return bool(_LOOSE_SECRET_RE.search(text)) or any(
        pattern.search(text) for pattern, _ in _GATE_SECRET_PATTERNS)


class MemoryError(Exception):
    pass


def _mtime_iso(path: Path) -> str:
    return _dt.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(
        timespec="seconds")


def memory_dir(root: Path) -> Path:
    return root / ".warden" / MEMORY_DIRNAME


def _dir_prefix(file: str) -> str:
    """Recall key: up to the first two directory segments — file paths churn
    (renames, new files); directories are the stable locality signal."""
    parts = Path(file).parent.parts
    return "/".join(parts[:2]) if parts else ""


def _redact(rule_id: str, text: str) -> str:
    if "secret" in rule_id.lower():
        return f"<redacted:{rule_id}>"
    if _has_secret_shape(text):
        return "<redacted:secret-pattern>"
    return text


# A review EVENT and a FINDING are not the same thing. A findings-free shard
# is still an EVENT, because `attest check` looks for a committed shard naming
# a commit in the PR's range, and a clean round must satisfy it. These are the
# fields that make a findings-free shard an EVENT rather than an empty file:
# what was reviewed, against what, when, by whom, with what verdict.
_EVENT_FIELDS = ("head_sha", "base_sha", "reviewed_at", "reviewers", "verdict")
# Checked at the SHARD boundary because `memory ingest` never calls
# `attest.build`: on the findings-free path `_check_rule_ids` returns
# immediately (no findings), so without this nothing between a hand-written
# artifact and committed evidence would look at the verdict at all, and
# `verdict: "CLEAN"` would shard as a review whose outcome no reader can
# classify. VERDICTS is IMPORTED from attest, which reads it out of
# attestation.schema.json — a retyped copy drifts, and the drift runs in the
# worst direction: an enum ADDITION is the schema edit the contract gate calls
# safe, and it would turn a schema-valid clean review into a refused one,
# i.e. NO ATTESTATION.
# The tally's buckets are the other half of that vocabulary; the mapping is
# explicit so a new verdict is a red test, not a silent mis-count.
_VERDICT_BUCKETS = {"clean": "clean", "findings-open": "findings_open"}


def _redact_value(value: object) -> object:
    """Scrub secret-shaped strings anywhere in the shard envelope.

    Shards are committed evidence and redaction is a boundary property, not a
    finding-text property: a reviewer label or a rules_version is a string a
    caller controls, and the corpus must never carry a live credential no
    matter which field carried it in. Applied to the WHOLE envelope, so a
    planted key in `rules_version` or `base_sha` is scrubbed too.
    """
    if isinstance(value, str):
        return "<redacted:secret-pattern>" if _has_secret_shape(value) else value
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    return value


def build_review_event(attestation: dict) -> dict:
    """The event half of a shard: the verdict and who reached it.

    sha/base_sha/rules_version/reviewed_at already ride on the shard envelope;
    these two are what the envelope was missing, and they are what makes "how
    many reviews came back clean" answerable from the corpus at all.

    Each key is present only when the artifact can vouch for it. Writing
    `verdict: ""` for an artifact that named no verdict would be recording an
    answer nobody gave — the tally reads a missing key as "not recorded", which
    is the truth, and an empty string as the same thing while looking like data.
    """
    event = {}
    if attestation.get("verdict") in VERDICTS:
        event["verdict"] = attestation["verdict"]
    if attestation.get("reviewers"):
        event["reviewers"] = _redact_value(attestation["reviewers"])
    # Rides with the roster it describes. A roster recorded without whether
    # anything checked it would leave that answer in the run dir, the one
    # place the corpus never reads. Present only when the artifact states it:
    # absent means the shard predates the check, which is NOT the same as
    # verified, and writing a default here would invent the answer.
    if attestation.get("roster_verification") in ROSTER_STATES:
        event["roster_verification"] = attestation["roster_verification"]
    # Rides with the roster for the same reason, one field over. Without it a
    # reader of the committed shards could not tell a warden-minted round from
    # any directory handed to `--review-dir`. Present only when the artifact
    # states it: absent means the shard predates the field, which is NOT the
    # same as bound.
    if attestation.get("round_binding") in ROUND_STATES:
        event["round_binding"] = attestation["round_binding"]
    # And one field over again, for the round that only exists because it can
    # be proved terminal. Without it the corpus could not tell a closure round
    # — a round minted PAST the repair cap — from an ordinary one, and "the
    # cap was respected" would stop being answerable from the committed
    # shards. Present only when the artifact states it: absent means not a
    # closure round, or a shard predating the check, and neither is the same
    # as verified.
    if attestation.get("closure_verification") in CLOSURE_STATES:
        event["closure_verification"] = attestation["closure_verification"]
    # And one field over again, for the base the envelope already carries.
    # `base_sha` rides every shard; whether it is on the branch it stamps did
    # not, so a reader recomputing figures from `base_sha..sha` could read
    # another merged change's work as this one's with nothing in the committed
    # evidence to warn them. Present only when the artifact states it: absent
    # means the shard predates the field, which is NOT the same as `ancestor`.
    if attestation.get("base_ancestry") in ANCESTRY_STATES:
        event["base_ancestry"] = attestation["base_ancestry"]
    return event


def _event_defects(attestation: dict) -> list[str]:
    """Why a findings-free artifact cannot be filed as a review event.

    Checked ONLY on the findings-free path, and deliberately: there the shard
    IS the whole evidence `attest check` reads, so an artifact that cannot name
    what was reviewed, against what, when, by whom, with what verdict must not
    mint an attestation. An artifact WITH findings is not held to it — its
    records already carry the provenance, and shards predating this invariant
    must keep ingesting.

    Each defect is phrased for the operator who has to fix it. "No verdict" and
    "a verdict outside the vocabulary" are DIFFERENT problems and must not
    share a message: reporting `verdict: "CLEAN"` as a missing verdict tells
    someone to supply what they already supplied, and never names the values
    that would work.
    """
    defects = [f"no {field}" for field in _EVENT_FIELDS
               if not attestation.get(field)]
    verdict = attestation.get("verdict")
    if verdict and verdict not in VERDICTS:
        defects.append(f"verdict {verdict!r} is not one of "
                       + " | ".join(VERDICTS))
    return defects


def _record_id(record: dict) -> str:
    # line included: two instances of the same defect in one file are two
    # records (stats n, recurrence counts). ts excluded: re-reviewing the
    # same sha is the same event, deduped.
    basis = "|".join(str(record.get(k, "")) for k in
                     ("sha", "rule_id", "file", "line", "finding", "status"))
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def build_records(attestation: dict, *, source: str = "attest",
                  origin: str = "interactive") -> list[dict]:
    """Normalize one attestation or gate document into memory records."""
    records = []
    for seq, f in enumerate(attestation.get("findings", [])):
        evidence = _redact(f["rule_id"], f.get("evidence", ""))
        finding_text = _redact(f["rule_id"], f.get("finding", ""))
        record = {
            "ts": attestation.get("reviewed_at", ""),
            # In-event order: a stable tiebreak for the cache and the recall
            # ordering. NOT streak input — a streak counts review rounds, and
            # reading it would make a streak a property of the reviewer's
            # write order.
            "seq": seq,
            "source": source,
            "sha": attestation.get("head_sha", ""),
            "base_sha": attestation.get("base_sha", ""),
            "rule_id": f["rule_id"],
            "tags": sorted(f.get("tags", [])),
            "dir_prefix": _dir_prefix(f["file"]),
            "file": f["file"],
            "line": f.get("line", 0),
            "severity": f.get("severity", ""),
            "finding": finding_text,
            "evidence": evidence,
            "status": f.get("status", "detected"),
            # Presence, not the clock. The READ side never validates
            # this: records carrying the older 'day'/'night' values stay
            # readable — evidence does not expire because a word changed.
            "origin": origin,
        }
        for field in ("pr", "bead"):   # provenance, when the producer knew it
            if attestation.get(field):
                record[field] = str(attestation[field])
        if f.get("legacy_rule_id"):
            # A migrated covered id: the shard records what the
            # artifact actually said, so the fold is auditable, not silent.
            record["legacy_rule_id"] = f["legacy_rule_id"]
        if f.get("reason"):
            # reason is REQUIRED on the examiner path (refuted/dismissed) —
            # exactly where a human quotes the secret while explaining it away
            record["reason"] = _redact(f["rule_id"], f["reason"])
        # Attribution: the lens that raised the finding and the
        # round it arrived in, present only when the artifact states them in
        # the declared shape — `ingest` never calls `attest.build`, so the
        # vocabulary is re-applied here the way ROSTER_STATES is. Absent stays
        # absent: older records carry no lens, and `code-reviewer` is not the
        # default answer to a question those records were never asked.
        if lens_is_valid(f.get("lens")):
            record["lens"] = f["lens"]
        rnd = f.get("round")
        if isinstance(rnd, int) and not isinstance(rnd, bool) and rnd >= 1:
            record["round"] = rnd
        record["id"] = _record_id(record)
        records.append(record)
    return records


def _shard_name(attestation: dict, records: list[dict]) -> str:
    ts = attestation.get("reviewed_at", "")
    try:
        compact = datetime.fromisoformat(ts).astimezone(timezone.utc) \
            .strftime("%Y%m%dT%H%M%SZ")
    except ValueError:
        compact = re.sub(r"[^0-9TZ]", "", ts) or "unknown"
    sha8 = attestation.get("head_sha", "")[:8] or "nohead"
    # content digest keeps filenames distinct for DISTINCT events even on a
    # ts+sha tie — a colliding name must never overwrite another event's
    # records — while identical events still dedupe to one file.
    # A findings-free review event has no records to hash, so it hashes the
    # EVENT: otherwise every clean review in the repo would share one digest
    # and two distinct clean rounds tied on ts+sha would overwrite each other,
    # quietly retiring the property the digest exists for.
    basis = records if records else [build_review_event(attestation)]
    digest = hashlib.sha256(
        json.dumps(basis, sort_keys=True).encode()).hexdigest()[:8]
    return f"{compact}-{sha8}-{digest}.json"


def _resolve_covered_legacy_ids(findings: list[dict],
                                rules_dir: Path) -> list[str]:
    """Migrate rule_ids a declared rule already ANSWERS onto that rule —
    the reader-side complement of attest's write-side refusal.

    Write-side refusal reaches an author who can fix the id; ingest's
    rejection hits legacy artifacts with no author present, so an id coined
    before the covering rule existed (`docs-drift` before wiki-fidelity
    declared `covers: [docs-drift]`) would be rejected on every ingest —
    permanent corpus loss on real judged evidence. Three forms migrate, all
    via the explicit covered_classes links (never inference): a bare covered
    slug, `unmapped:<covered-slug>` (only while the covering rule is
    unpaused — see below), and `unmapped:<declared-id>` — the prefixed forms
    landing exactly where attest's own error message tells the author to
    file them. The bare form lands on the covering RULE even while it is
    paused, though attest's generic message suggests the unmapped spelling:
    a bare slug is a legacy spelling, and re-keying its old judgments onto
    the live candidate row would inflate the re-opened class's recurrence
    with stale evidence — the row the pause re-opened is for FRESH
    judgments. The legacy id stays on the finding as provenance
    (`legacy_rule_id`), and every migration is reported, never silent.
    Anything else is rejected.

    Because this rewrite — and the pause-conditional skip — makes routing
    flip with pause state, the ingest dedup must never compare RAW record
    ids across sweeps: `_event_already_filed` folds both routings of one
    event to one identity via `legacy_rule_id`.
    """
    # include_paused: migration is an IDENTITY question, not an enforcement
    # one — a paused covering rule still owns its legacy ids, or pausing it
    # re-opens the permanent-corpus-loss rejection this function exists to
    # close.
    covered = covered_classes(rules_dir, include_paused=True)
    # ... but migration rescues exactly what validation refuses, no more:
    # while the covering rule is paused, `_check_rule_ids` itself accepts
    # `unmapped:<covered-slug>` as a live candidate key — the re-opened class
    # accruing evidence under its own slug is the point of re-proposing it.
    # Migrating that spelling anyway would re-route the fresh evidence onto
    # the paused rule and freeze the candidate row the enforcement filter
    # just unfroze. The spellings the writer refuses paused or not — a bare
    # slug, `unmapped:<declared-id>` — still migrate on the identity link.
    enforced = covered_classes(rules_dir)
    valid = {r.id for r in _declared_rules(rules_dir)[0]}
    migrated: list[str] = []
    for f in findings:
        rid = f.get("rule_id", "")
        if rid in valid:
            continue
        prefixed = rid.startswith(UNMAPPED_PREFIX)
        slug = rid[len(UNMAPPED_PREFIX):] if prefixed else rid
        target = covered.get(slug)
        if not target:
            continue
        if prefixed and slug not in valid and slug not in enforced:
            continue    # a live candidate key needs no rescue
        f["legacy_rule_id"] = rid
        f["rule_id"] = target
        migrated.append(f"{rid} -> {target}")
    return migrated


def _normalized_ids(records: list[dict]) -> set[str]:
    """Record ids normalized to the rule_id the ARTIFACT actually said —
    a routing-independent event identity.

    Record ids hash rule_id, and rule_id is what the covered-legacy
    migration and the pause routing rewrite — so the same artifact
    re-swept under a different ruleset state produces different raw ids for
    one review event. `legacy_rule_id` records the artifact's original
    spelling on every rewritten record precisely so the fold is auditable;
    recomputing the id under that original spelling makes both routings of
    one event compare equal, while distinct events (different files, lines,
    findings) stay distinct even on a ts+sha tie. Records never rewritten
    normalize to their stored id.
    """
    return {_record_id({**r, "rule_id": r.get("legacy_rule_id")
                        or r.get("rule_id", "")}) for r in records}


def finding_identity(record: dict) -> str:
    """The FINDING a record states, independent of which review event filed
    it — the content-derived identity every counting seam reads.

    `_record_id` hashes the head sha, on purpose: two shards are two events,
    and the corpus keeps both. But a re-attestation is a normal, CORRECT act
    — the head-binding refuses a stale attestation after a repair round moves
    the head, so the builder re-mints and re-attests, and the payload carried
    forward files every finding again under a fresh id. Counted raw, those
    copies inflate a rule's n exactly where review was most repeated, which
    is where the promotion evidence looks strongest.

    Identity is (rule as the artifact SPELLED it, file, line, finding text).
    Line stays in: two instances of one defect in one file are two findings,
    the `_record_id` rule kept. sha, ts, seq and status stay OUT: they name
    the event, and the event is what a restatement changes. The rule is
    normalized through `legacy_rule_id` for the reason `_normalized_ids`
    gives — migration and the pause routing rewrite `rule_id`, and a
    restatement swept under a different ruleset state must still fold.
    """
    basis = "|".join(str(record.get(k, "")) for k in ("file", "line", "finding"))
    rule = record.get("legacy_rule_id") or record.get("rule_id", "")
    return hashlib.sha256(f"{rule}|{basis}".encode()).hexdigest()[:16]


def fold_restatements(records: list[dict]) -> list[dict]:
    """One finding, however many shards restate it, counted once — the LATER
    round's disposition wins.

    A READ-side fold, applied at every seam that counts (the two loaders and
    `stats`), never a write: the shards are append-only committed evidence
    and `.warden/rules/evidence-intact.md` treats editing one as falsifying
    the record. Every copy stays in its shard; the fold decides what the
    tally sees.

    Only JUDGMENTS fold. A gate `detected` record is a checker firing on a
    commit — a fact, and four commits it fired on are four facts, so those
    pass through untouched (they never enter precision anyway). And a fold
    only ever crosses ROUNDS: a restatement is by definition in a later
    (ts, sha) round, so two same-content records one round filed — say the
    reviewer and the examiner disagreeing on one finding — stay two, exactly
    as `_record_id` (which hashes status) already keeps them. Within a
    group the latest round keeps every record it filed; every earlier
    round's copies fold into it.

    "Later" is the same order the cache is written in — (ts, sha) — so the
    answer does not depend on the order the records arrived in. The
    survivor (the latest round's lowest-seq copy) carries `restated: N`,
    the number of earlier copies it stands for, AND `restated_from`, those
    copies themselves; idempotent, so a fold of a fold keeps both rather
    than losing them. Records the fold leaves alone are returned unchanged,
    in their input order.

    `restated_from` is not bookkeeping — it is what keeps the fold from
    REWRITING HISTORY. A count alone cannot say that a dropped copy was
    judged differently from the survivor, so a status flip across rounds
    would change the sign of a contribution invisibly; and the round-level
    arithmetic (`_streaks_by_round`, `_refuting_weight`) would read a round
    that was MIXED as it happened as one that refuted everything, which
    LENGTHENS a refutation streak toward the auto-pause trigger and removes
    enforcement. So the dropped copies are carried; `_with_restatements`
    hands the streak-RESETTING ones back to the rounds that filed them, in
    the safe direction only; and `stats` reports how many changed a
    disposition (`restatements_changed`). The fold decides what PRECISION
    counts; what a round SAID is not its to edit.

    What this deliberately does NOT fold: the same finding text at another
    line, in another file, or under another rule — those are distinct
    findings (`finding_identity`). A genuinely recurring class is re-raised
    in a reviewer's own words; only a payload copied forward reproduces the
    text byte for byte, which is why the fold reads as restatement rather
    than as recurrence erased.
    """
    groups: dict[str, list[dict]] = {}
    for r in records:
        if r.get("status") in _JUDGED_STATUSES:
            groups.setdefault(finding_identity(r), []).append(r)
    out: dict[int, dict] = {}
    for copies in groups.values():
        latest = max(_round_key(c) for c in copies)
        kept = [c for c in copies if _round_key(c) == latest]
        dropped: list[dict] = []
        for c in copies:
            dropped.extend(c.get("restated_from", ()))
            if _round_key(c) != latest:
                dropped.append(_bare(c))
        for c in kept:
            # A survivor of an earlier fold that is NOT this fold's survivor
            # is stripped, so its carried copies (already collected above)
            # cannot be counted twice by a fold of a fold.
            out[id(c)] = _bare(c) if c.get("restated_from") else c
        if dropped:
            first = min(kept, key=lambda c: c.get("seq", 0))
            # Sorted, for the reason the survivor is chosen by (ts, sha) and
            # not by arrival: the fold's OUTPUT, this list included, must not
            # depend on the order its input happened to be in.
            dropped.sort(key=lambda c: (c.get("ts", ""), c.get("sha", ""),
                                        c.get("seq", 0), c.get("id", "")))
            out[id(first)] = {**_bare(first), "restated": len(dropped),
                              "restated_from": dropped}
    return [out.get(id(r), r) for r in records
            if r.get("status") not in _JUDGED_STATUSES or id(r) in out]


def _bare(record: dict) -> dict:
    """A record without the fold's own bookkeeping — what the round filed."""
    return {k: v for k, v in record.items()
            if k not in ("restated", "restated_from")}


def _disposition(record: dict) -> str:
    """The bucket a judged record contributes to, which is the unit a reader
    ACTS on — coarser than `status` on purpose.

    `confirmed` and `fixed` are one bucket: both are the rule being upheld,
    both sit in the Wilson numerator, and a repair round moving a finding
    from the first to the second is the normal course of a review, not a
    change of mind. `refuted` and `dismissed-with-reason` are kept apart
    from each other because the streak arithmetic treats them differently —
    a refutation extends a streak, a dismissal resets it.
    """
    status = record.get("status")
    return "upheld" if status in ("confirmed", "fixed") else str(status)


def _with_restatements(records: list[dict], *, refutations: bool) -> list[dict]:
    """Folded records, with folded-away judgments handed back to the rounds
    that filed them — as much of that history as the CALLER's safe direction
    needs, and no more.

    `refutations=False` withholds the folded-away refutations; `True` returns
    the pre-fold multiset exactly. The choice is not a preference: the two
    round-level readers below have OPPOSITE safe directions, and a single
    restoration rule would serve one of them and break the other.

    The round-level arithmetic below is read by `warden autonomy pause`,
    which MUTATES GATE SURFACE, so `_round_key` states its safety invariant
    as a direction: a merge "can only ever SHORTEN a streak". The
    restatement fold is the other thing that moves what a round holds, and
    it moves it BOTH ways, so it needs the direction imposed rather than
    assumed:

    - a REFUTATION restated across re-attestations is one judgment carried
      forward, not a fresh round refuting the rule. It stays folded, the
      earlier rounds fall silent, and the streak shortens. That is the whole
      point of the fold here — otherwise every repair round would walk a
      rule toward a pause it never earned.
    - an UPHELD or DISMISSED judgment that a later round restated as refuted
      is handed back. Without that, a round that was MIXED as it happened
      reads as one that refuted everything, and the streak LENGTHENS — a
      raw history of two refuting rounds can read as three, which is
      PAUSE_STREAK. A streak the fold invented is enforcement removed by a
      rewrite of history.

    So the streak reader takes `refutations=False` and the fold is monotone
    in the safe direction there: it may shorten a streak, never lengthen one.

    `_refuting_weight` takes `refutations=True`, because its safe direction
    is the OPPOSITE one. The weight is a SUPPRESSOR — `_candidate_rows` and
    `skill_recurrence` read `weight >= PAUSE_STREAK` to STOP proposing a
    class reviewers keep refuting — so a weight that falls UN-blocks a
    proposal. Subtracting a folded-away refutation from the round that filed
    it would lower the weight, which can turn a `noisy` candidate into a
    `propose` one and flip certify's S-04 `promotion_bar_met`. Handing the
    whole history back makes the weight identical to the pre-fold weight —
    the fold does not touch the suppressor at all — which is the only
    direction with no failure mode.

    Both properties are pinned by executable tests over random corpora
    rather than by this paragraph.
    """
    out: list[dict] = []
    for r in records:
        out.append(r)
        out.extend(prior for prior in r.get("restated_from", ())
                   if refutations or prior.get("status") != "refuted")
    return out


def _event_already_filed(shard_dir: Path, attestation: dict,
                         records: list[dict]) -> Path | None:
    """The shard already holding THIS review event, if one is filed.

    Identity is the event — its timestamp, its head sha, and the records it
    judged — never the bytes of the moment it was filed. The filename
    carries a content digest, so any change in how a record serializes (a
    new field, a renamed default) would otherwise give every artifact still
    sitting in .warden/out a fresh filename and file a second shard for a
    review that happened once. The invariant is general: one review event,
    one shard, ever.

    Records compare by NORMALIZED id (`_normalized_ids`), because rule_id —
    which the raw id hashes — is rewritten by migration and flips with pause
    state: comparing raw ids would re-file one event after a pause flip, and
    a coarser prefix-glob comparison would swallow a DISTINCT event tying on
    ts+sha (two same-second attestations of one head are constructible on
    the real write path). Normalizing back through `legacy_rule_id` keeps
    both properties at once: one event, one shard, across routing flips;
    distinct tied events still both file.

    Distinct events that tie on ts+sha still get their own file: they carry
    different records, so their id sets differ and this returns None, which
    is exactly the property the digest was added for.
    """
    prefix = _shard_name(attestation, records).rsplit("-", 1)[0]
    # Same basis _shard_name uses: a findings-free event has no records to
    # identify it by, so it is identified by the EVENT — otherwise every
    # clean review of one sha would collapse into the first one filed.
    event = build_review_event(attestation) if not records else None
    want = _normalized_ids(records)
    for path in sorted(shard_dir.glob(f"{prefix}-*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue   # unreadable here is not a verdict; the reader fails closed
        filed = doc.get("records", [])
        if event is not None:
            if not filed and all(doc.get(k) == v for k, v in event.items()):
                return path
        elif _normalized_ids(filed) == want:
            return path
    return None


def sha_reachable(root: Path, sha: str) -> bool | None:
    """Is `sha` contained by any ref in this repo?

    True / False when git can answer; **None when it cannot** — the three
    states are kept apart on purpose, because the caller must not read
    "could not look" as "not reachable" (that would silently drop real
    records) nor as "reachable" (that would ingest the synthetic ones).

    Definitive NOT-reachable covers two shapes: the commit exists but no ref
    contains it (a reverted or abandoned probe), and the commit is gone from
    the object store entirely (`no such commit` — pruned, or never fetched).
    A record naming either is unresolvable by anyone auditing it later.
    """
    if not sha:
        return None
    try:
        proc = subprocess.run(
            ["git", "for-each-ref", f"--contains={sha}", "--count=1",
             "--format=%(refname)"],
            cwd=root, capture_output=True, text=True, env=git_env())
    except (FileNotFoundError, OSError):
        return None          # no git here — cannot look
    if proc.returncode == 0:
        return bool(proc.stdout.strip())
    if "no such commit" in proc.stderr.lower():
        return False         # object absent: definitively unreachable
    return None              # some other git failure — cannot look


def _dot_git_present(root: Path) -> bool:
    """Is there a `.git` at `root` or above it, on disk?

    Asked of the FILESYSTEM, never of git — this is the discriminator used
    when git itself refuses to answer, so it must not depend on git working.
    """
    try:
        for d in (root, *root.resolve().parents):
            if (d / ".git").exists():
                return True
    # This function exists to answer when git itself is broken, so the walk
    # failing IS the answer.
    # warden:allow(except-pass): an OSError during the walk means not a usable repo, the direction the docstring promises
    except OSError:
        pass
    return False


def is_git_repo(root: Path) -> bool | None:
    """Does `root` sit inside a git work tree?

    THREE-VALUED, for the same reason `sha_reachable` is: True = yes,
    False = definitively not a checkout, **None = a repo is here but git
    would not read it**.

    A two-valued answer would fail OPEN and silently disable the entire
    guard. `not a git repository` and
    `detected dubious ownership in repository at ...` are both exit 128, so
    collapsing them to False would mean a container or bind-mounted checkout
    — where dubious-ownership is the ordinary failure — ingests every gate
    artifact with no probe at all and an empty `unreachable` list, a result
    byte-identical to a clean sweep. A carefully fail-closed inner probe is
    worth nothing behind an outer probe that fails open on the same
    condition.

    False is reserved for "there is no repo here", which is the only case
    where the guard should be inert: a consuming repo that is not a git
    checkout has no revert semantics to protect against, and failing closed
    there would refuse to ingest anything rather than guard anything.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root, capture_output=True, text=True, env=git_env())
    except (FileNotFoundError, OSError):
        # No git binary. Whether that is safe depends on whether a repo is
        # THERE, which the filesystem can answer without git.
        return None if _dot_git_present(root) else False
    if proc.returncode == 0:
        # "false" means a git context that is not a work tree (bare repo, or
        # inside .git/). Refs exist there, so the guard must not go inert.
        return True if proc.stdout.strip() == "true" else None
    return None if _dot_git_present(root) else False


CONSUMED_SUBDIR = ".consumed"


def _consumed_path(root: Path, run_dir: Path) -> Path:
    """Where the hint for `run_dir` lives.

    Beside the run dirs, never INSIDE one. A run dir is
    sealed evidence: `runs.write_manifest` records its file list at seal time,
    so writing a marker into it silently makes that manifest wrong for every
    ingested run. `.warden/out/` is gitignored wholesale, so a sibling
    directory is just as local and mutates nothing already recorded.
    """
    return root / ".warden" / "out" / CONSUMED_SUBDIR / f"{run_dir.name}.json"


def _consumed(root: Path, run_dir: Path) -> dict | None:
    """What this run dir was already sharded as, or None.

    The run dir is the artifact's only durable identity across branches.
    `_event_already_filed` asks the SHARD STORE whether an
    event is on record — and that store is committed, so it is branch-scoped,
    while `.warden/out/` is gitignored and branch-independent. Switch branches
    and the store's answer flips to "no", so ingest would re-mint every run dir
    whose shard lives on some other branch — and a shard for another PR's
    review round would appear staged-ready in an unrelated branch's tree,
    where `git add -A` files it into that PR's range and `attest check`
    credits the PR with a review it never ran.

    VALIDATED, because on the refusal path this hint is what withholds a
    review round from the corpus: a marker of the wrong
    schema, or one written for the other store, is treated as absent rather
    than trusted. What it still cannot check is whether the shard it names
    ever existed — by construction that shard is not in this store — so a
    hand-forged marker can suppress one round. The refusal is reported for
    exactly that reason, and it names the file to delete.
    """
    try:
        doc = json.loads(_consumed_path(root, run_dir).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        return None
    if not isinstance(doc.get("shard"), str) or not doc["shard"]:
        return None
    if doc.get("store") != "attest":
        # Checked HERE, not left to the call site: a marker declaring
        # `store: gate` must not withhold an attest round, and a stated check
        # carried by something other than the validator that states it is one
        # refactor away from being no check at all.
        return None
    return doc


# A revision this seam will hand to git. Deliberately narrow — a committed sha,
# nothing else — because the values come from an artifact a session can write
# and `git diff` reads a leading `-` as an option.
_SHA_SHAPE = re.compile(r"[0-9a-fA-F]{7,64}")


def range_binding_at_ingest(root: Path, attestation: dict) -> tuple[str, str]:
    """The range binding, re-applied at the SHARD boundary.

    `memory ingest` never calls `attest.build`, so every invariant the writer
    holds has to be re-applied here or it does not hold for the reader — which
    is the argument that put `_check_rule_ids` at this seam. Without this, an
    artifact that never passed `attest write` would shard with no binding at
    all.

    IT WARNS. It never refuses, and the caller must not make it refuse. The
    writer's version of this check ships as a WARNING by founder ruling: two
    of this repo's own rules (`rename-complete`, `wiki-fidelity`) produce
    out-of-range findings BY CONSTRUCTION, so a refusal would reject valid
    attestations. Reinstating the refusal here would be strictly
    worse than at the writer: `attest write` refuses at review time when the
    author can react, ingest would refuse at corpus time when the review is
    done and the only way through is deleting a true finding.

    NO WAIVER FIELD, because `build` honours none: there is no
    `out_of_range_reason` anywhere in the contract, the schema declares no such
    field, and inventing one here would be a declared mechanism with zero
    consumers.

    ALWAYS RETURNS A READING, one of five kinds, because the manifest is what
    an unattended run is audited from and "nothing recorded" must not be the
    same shape as "checked and clean". `ok`,
    `out-of-range` and `empty-range` come from `attest.range_binding` itself —
    carried, never re-derived, so the two seams cannot classify one artifact
    two ways. Two more are this seam's own: `no-range` (the artifact declares
    no base/head at all) and `unresolved` (git could not read the range).

    UNRESOLVED IS NOT EMPTY. The shas may be long gone by ingest time (a squash
    merge, an amend, CI's shallow clone) and `sha_reachable` already owns that
    judgment and ingests anyway. Reporting an unreadable range as one that
    "changed no files" would send every reader of an old artifact hunting a
    stale findings file that is fine. Only `out-of-range` and `empty-range`
    carry a message; the other three carry "" and the caller stays silent.

    THE SHAS ARE SHAPE-CHECKED FIRST, and this is a refusal, not a nicety.
    They are read verbatim out of a session-writable
    artifact that never passed `attest write` — that is the whole reason this
    function exists — and `git diff` parses a leading `-` as an OPTION, not a
    revision: a `base_sha` of `--output=<path>` makes git exit 0, write a file,
    and return nothing, which would read as a real `empty-range`.
    Same guard shape as `github.py`'s.
    """
    from .attest import range_binding
    from .diffs import DiffError, range_paths

    # NO findings short-circuit here, deliberately. `range_binding` already
    # answers "ok" for a findings-free set ("a clean review is still a review"),
    # and a copy of that rule at this seam is a second place to change it —
    # with two implementations each covers the other's mutation, so no test
    # over the property can fail. One source, one git call more on a clean
    # round.
    findings = attestation.get("findings") or []
    base = attestation.get("base_sha") or ""
    head = attestation.get("head_sha") or ""
    if not (base and head):
        return "no-range", ""
    if not (_SHA_SHAPE.fullmatch(base) and _SHA_SHAPE.fullmatch(head)):
        # An option-shaped or otherwise unrecognizable revision is UNRESOLVED,
        # never passed to git: the check that cannot evaluate reports that it
        # could not, and reaches no tool that would act on the string.
        return "unresolved", ""
    try:
        # Three-dot, exactly as `attest write` computed it: the gate scopes to
        # base...head, so the file list is the one the reviewer was shown.
        changed = range_paths(root, base, head)
    except DiffError:
        return "unresolved", ""
    return range_binding(findings, changed, base, head)


def _mark_consumed(root: Path, run_dir: Path, shard_name: str) -> str | None:
    """Record which shard this run dir produced; return a failure to report.

    Attest only. The marker exists because the shard store is committed and
    therefore branch-scoped; the gate store is gitignored, never committed,
    and the doctrine is that the next `warden review` re-derives it. Applying
    the marker there would remove that re-derivation path and make a deleted
    gate dir unrecoverable without hand-editing.

    A write failure is RETURNED rather than swallowed. It is not a cosmetic
    loss: the marker is the whole mechanism, so a silent failure leaves the
    invariant inoperative for that run dir and the next sweep on another
    branch re-mints exactly the shard this guard exists to prevent. Losing it
    must not fail an ingest whose evidence already landed — but it must be
    said out loud.
    """
    path = _consumed_path(root, run_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"schema": 1, "store": "attest", "shard": shard_name}, indent=2) + "\n")
    except OSError as exc:
        return (f"{run_dir.name}: could not record which shard this run dir "
                f"produced ({exc}) — the one-run-dir-one-shard guard is not "
                f"armed for it, so a sweep on another branch may re-file "
                f"this event")
    return None


def _assert_ruleset_readable(rules_dir: Path, root: Path) -> None:
    """Refuse, by name, a ruleset `ingest` is about to resolve against —
    the READ half of a two-question probe.

    * **Can it be READ?** — asked ALWAYS, and asked by CALLING the reader.
      `rules.rules_version` is what every consumer of this ruleset computes,
      it reads FOUR surfaces (the rules dir, each rule file, the repo's
      `.warden/checkers/*.py`, and the extra surfaces it hashes), and it
      refuses an unreadable one by name. A probe that walked its own subset
      of those surfaces would pass an unreadable checker plugin and let a
      bare `PermissionError` escape from the middle of the MUTATING sweep —
      after the shard, the cache row and the `.consumed` marker were on
      disk. Calling the reader keeps the probe and the reader on one set of
      surfaces.

      The sweep is not the only reader either — `cli._cmd_memory` calls
      `rules_version` for the run manifest AFTER `ingest` returns, so gating
      this question on "is there anything to sweep" would move the traceback
      rather than remove it, and turn an unlistable dir's exit 2 with a name
      into a silent exit 0. Asked at the TOP of `ingest`, before its first
      `mkdir`, so the message's "nothing has been written" is true when it
      is printed.
    * **Is it VALID?** — `_assert_ruleset_valid` below, asked only when the
      sweep has an artifact to resolve rule_ids against.

    A NON-UTF-8 rule file is NOT this half's business. It opens perfectly; it
    is merely unusable, which is the validity question. A text read under
    `except (OSError, ValueError)` would refuse it here — `UnicodeDecodeError`
    IS a `ValueError` — on the UNCONDITIONAL path, and move a tree with
    nothing to sweep from exit 0 to exit 2, which `cage/run.sh` keys on.
    Reading BYTES, as the reader does, puts the decode failure on the
    validity side by construction rather than by a carefully maintained
    exception tuple.

    AttestError either way, so the CLI prints `warden: <message>` and exits 2 —
    the same fatal direction the behaviour always had (a test in
    `tests/test_loader_parity.py` pins that it must never be downgraded to a
    skipped artifact), with the ruleset named instead of a traceback.
    """
    from .rules import RuleError, rules_version

    try:
        rules_version(rules_dir, root)
    except RuleError as exc:
        raise AttestError(
            f"the declared ruleset could not be read — {exc}. Every finding's "
            "rule_id resolves against it, so the corpus was NOT fed; nothing "
            "has been written") from exc


def _assert_ruleset_valid(rules_dir: Path) -> None:
    """Refuse a ruleset that reads perfectly and is merely INVALID — a bad
    `severity`, a duplicate id, a file that is not UTF-8.

    Asked only when the sweep has at least one artifact to resolve rule_ids
    against. A tree with an invalid ruleset and nothing to ingest exits 0, and
    `cage/run.sh` reads that code as "was the corpus fed", so moving it to 2
    for a sweep that resolves nothing would change what the cage reads with
    no defect behind it.

    `load_rules`, NOT `covered_classes` / `_declared_rules`: those two swallow
    every exception into an (rules, error) pair on purpose — `stats` is a pure
    read that must still render — so probing through them would be a
    guard that cannot fire. The readers inside the sweep go through
    `_RuleIndex`, which calls `load_rules` raw, so this calls exactly what they
    call.

    An EMPTY ruleset is not invalid here: `load_rules` refuses one ("no rule
    files"), a repo with no rules ingests fine, and an artifact that needs a
    rule is still refused per-artifact. The listing is a plain glob because
    the READ question has already been answered by the arm above — an
    unlistable dir never reaches this line.
    """
    from .rules import RuleError, load_rules

    if not any(rules_dir.glob("*.md")):
        return
    try:
        load_rules(rules_dir)
    except (OSError, RuleError) as exc:
        raise AttestError(
            f"the declared ruleset at {rules_dir} cannot be read ({exc}) — "
            "every finding's rule_id resolves against it, so the corpus was "
            "NOT fed; nothing has been written") from exc


def ingest(root: Path, *, rules_dir: Path, origin: str = "interactive") -> dict:
    """Sweep .warden/out attest artifacts into committed shards, then
    rebuild the derived cache from ALL shards. Idempotent end to end.

    Every finding's rule_id is resolved against rules_dir by the SAME check
    `attest write` applies — an artifact carrying an id attest would refuse is
    rejected whole and reported, never sharded, with one reader-side
    exception: an id a declared rule already answers MIGRATES onto that rule
    instead (see _resolve_covered_legacy_ids). rules_dir is required (never
    defaulted), exactly as in attest.build: a caller that could omit it would
    silently reopen the free-form rule_id hole this closes, and a consuming
    repo's rules_dir is a config fact, not a guessable path.

    The range binding is re-applied at this seam too, for the same reason —
    and at the same STRENGTH the writer has: it WARNS into
    `range_bindings` and never refuses a shard. See `range_binding_at_ingest`.
    """
    # BEFORE the first mkdir, and before anything else this function does:
    # the refusal says "nothing has been written", so it must land before the
    # point of no return. Asked on every path, sweep or no sweep, because
    # `cli._cmd_memory` reads the ruleset for the run manifest after this
    # returns.
    _assert_ruleset_readable(rules_dir, root)
    shard_dir = memory_dir(root) / SHARD_SUBDIR
    shard_dir.mkdir(parents=True, exist_ok=True)
    gate_dir = memory_dir(root) / GATE_SUBDIR
    gate_dir.mkdir(parents=True, exist_ok=True)

    new_shards = []
    skipped: list[str] = []
    invalid_rule_ids: list[str] = []
    migrated_rule_ids: list[str] = []
    unreachable: list[str] = []
    elsewhere: list[str] = []
    undetermined: list[str] = []
    range_bindings: list[dict] = []

    def _note(problem: str | None) -> None:
        if problem:
            marker_failures.append(problem)

    marker_failures: list[str] = []
    # Resolved ONCE: the answer cannot change mid-sweep, and probing per
    # artifact would fork the guard's behaviour across one ingest run.
    #
    # `is not False` on purpose. Only a definitive "there is no repo here"
    # makes the guard inert; None ("a repo is here, git would not read it")
    # keeps it ACTIVE, and sha_reachable then returns None for every
    # artifact, so the round fails closed and says so — instead of the
    # unreadable-repo case quietly becoming a clean sweep.
    guard_active = is_git_repo(root) is not False
    out_dir = root / ".warden" / "out"
    sources = [("attest", "*-attest/attestation.json"),
               ("gate", "*-review/review-findings.json")]
    if out_dir.is_dir():
      # The artifact roster FIRST, so the VALIDITY probe below runs only when a
      # sweep will actually resolve rule_ids against it. The READ question was
      # asked unconditionally at the top.
      roster = [(source, artifact) for source, pattern in sources
                for artifact in sorted(out_dir.glob(pattern))]
      if roster:
          _assert_ruleset_valid(rules_dir)
      for source, artifact in roster:
        # ONE RUN DIR, ONE SHARD, EVER. Checked before
        # parsing, and against the marker rather than the store, because
        # the store is branch-scoped and the run dir is not. When the
        # named shard IS in this branch's store the normal idempotent
        # path below handles it; when it is not, re-minting would file
        # another branch's review event into this one's range.
        already = _consumed(root, artifact.parent) \
            if source == "attest" else None
        # THE ARTIFACT, and only the artifact. Two try blocks, not one. A
        # malformed artifact — a top-level array, a scalar, a non-UTF-8
        # file — is skipped loudly instead of aborting this MUTATING sweep,
        # so this block catches `OSError` and `ValueError`. The second block
        # wraps `_resolve_covered_legacy_ids`, `_check_rule_ids` and
        # `build_records`, which read the RULESET, and must not catch
        # `OSError`: a `chmod 000` rule file would otherwise be reported as a
        # malformed ARTIFACT, naming the run dir (the one thing that is fine)
        # while `warden memory ingest` exits 0 — the exit code the cage reads
        # as "the corpus rebuilt". A check that could not look is not a check
        # that passed, and it must not blame the wrong subject either.
        try:
            doc = json.loads(artifact.read_text())
        except (OSError, ValueError) as exc:
            # ValueError covers JSONDecodeError AND the UnicodeDecodeError
            # a non-UTF-8 artifact raises.
            skipped.append(f"{artifact.parent.name}: {exc}")
            continue
        problem = _artifact_shape_problem(doc)
        if problem:
            # The ENVELOPE AND ITS ELEMENTS, refused by name rather than
            # met with an AttributeError from inside
            # `_resolve_covered_legacy_ids` or `build_records`. PARITY:
            # these are the same shapes `validate_shard_records` refuses
            # on the shard side, asked here of the `findings` an artifact
            # carries — which is what makes "refused at the crossing" true
            # of the shape as well as of `status`, down to a findings LIST
            # holding a non-mapping.
            skipped.append(f"{artifact.parent.name}: {problem}")
            continue
        try:
            if source == "gate":
                # the gate artifact names its own time differently
                doc.setdefault("reviewed_at", _mtime_iso(artifact))
            # Covered legacy ids migrate before the check; then an id
            # attest would refuse must not become a corpus key just
            # because it arrived through the reader.
            migrated = _resolve_covered_legacy_ids(
                doc.get("findings", []), rules_dir)
            _check_rule_ids(doc.get("findings", []), rules_dir)
            records = build_records(doc, source=source, origin=origin)
        except (KeyError, TypeError, AttributeError) as exc:
            # A malformed artifact must not abort the sweep mid-mutation:
            # skip it loudly and keep the store + cache consistent.
            #
            # `OSError` is deliberately NOT here — see the split above; it
            # would be the RULESET failing, not this artifact, and this
            # sweep exiting 0 over an unreadable ruleset is the fail-open
            # the split exists to end.
            #
            # `AttributeError` IS here, as a backstop behind the shape
            # check above rather than in place of it: `_redact` and
            # `build_records` reach into a finding's optional fields, and
            # a shape nobody enumerated must be skipped loudly rather than
            # abort a MUTATING loop.
            #
            # KNOWN MISLABEL: `TypeError` can come from the RULESET too.
            # `rules.py` checks a rule `id` for PRESENCE only, so a
            # non-string or blank `id` makes `_check_rule_ids`'s
            # `", ".join(sorted(valid))` raise TypeError, and this arm then
            # blames the run dir while ingest exits 0.
            # `json.JSONDecodeError` is NOT here: nothing in this block
            # parses JSON, and a name that can never fire reads as a guard
            # that is doing something.
            skipped.append(f"{artifact.parent.name}: {exc}")
            continue
        except AttestError as exc:
            # Rejected WHOLE and loudly, sweep continues — the same
            # contract as a malformed artifact. A shard is one review
            # event: dropping just the offending finding would file a
            # shard whose records silently disagree with the artifact it
            # names. Aborting the sweep instead would leave the cache
            # half-rebuilt, which is worse than either extreme.
            invalid_rule_ids.append(f"{artifact.parent.name}: {exc}")
            continue
        # THE SEAM, not each reader. `attest build`
        # validates against attestation.schema.json, whose `status` is a
        # four-value string enum — but this reader does not, and
        # `build_records` copies `f.get("status", "detected")` verbatim.
        # So a hand-written or third-party artifact carrying
        # `"status": ["fixed"]` would be SHARDED here and then crash the very
        # command that had just written it, and every later reader with
        # it. Refused at the crossing, through the same definition the
        # readers apply, so the corpus is never poisoned in the first
        # place. The artifact is rejected WHOLE and reported, exactly as a
        # malformed one is: a shard is one review event, and filing the
        # good records of a bad artifact writes evidence that silently
        # disagrees with the artifact it names.
        try:
            validate_shard_records(artifact.parent.name, records)
        except ValueError as exc:
            skipped.append(str(exc))
            continue
        if not records:
            # A clean review IS a review. A round that found nothing still
            # files a shard, because `attest check` reads the committed
            # shards and would otherwise report NO ATTESTATION for exactly
            # the PRs that passed review. A findings-free attest artifact
            # files the review EVENT instead.
            if source != "attest":
                # A gate run that fired nothing judged nothing and names no
                # reviewer, and `attest check` ignores gate shards by
                # design — an empty gate shard per run would be committed
                # churn with no reader.
                continue
            defects = _event_defects(doc)
            if defects:
                # Fail CLOSED. The findings-free shard is the WHOLE
                # evidence for the attestation gate, so minting one off an
                # artifact that cannot say what was reviewed, against what,
                # when, by whom, with what verdict would replace one
                # fail-open with another.
                skipped.append(
                    f"{artifact.parent.name}: findings-free attestation "
                    f"cannot be filed as a review event — "
                    f"{'; '.join(defects)}")
                continue
        attestation = doc
        shard_path = (shard_dir / _shard_name(attestation, records)) if source == "attest" \
            else (gate_dir / _shard_name(attestation, records))
        # One review event, one shard — ever, decided by NORMALIZED record
        # identity for every artifact: migration and the pause routing
        # rewrite rule_id, which the raw record ids hash, so raw comparison
        # would re-file one event after a pause flip, and a coarse
        # prefix-glob comparison would swallow a DISTINCT event tying on
        # ts+sha. `_event_already_filed` folds both routings of one event to
        # one identity via legacy_rule_id, so both properties hold at once.
        filed = _event_already_filed(shard_path.parent, attestation, records)
        if not filed:
            # ORDER IS THE WHOLE POINT. This runs only after the identity
            # check above has found no filed shard: run earlier, it would
            # report already-committed shards as unreachable and tell the
            # reader to re-run — which would file a duplicate for a review
            # round already on record.
            #
            # Reached only when the event is genuinely absent here, this
            # asks the narrower question: is there any point minting a
            # shard for this commit?
            #
            # Direction reversed from gate's, deliberately. Gate skips on
            # "could not determine" too, because a synthetic detection is
            # worse than a lost one and the next review re-derives a real
            # one. Nothing re-derives a judgment, so this skips only on a
            # definite no.
            #
            # The tradeoff, stated rather than hidden: an attestation
            # orphaned before ingest (an amend between `attest write` and
            # here) is dropped. That shard was already dead to the gate —
            # `attest check` looks for a commit in the PR's range, and the
            # pre-amend sha is not in it — so the recovery is the re-attest
            # you would have had to run regardless, and the message says so.
            # head_sha, NOT sha: a review artifact carries base_sha and
            # head_sha only — `sha` is the RECORD field build_records
            # derives. Reading the record's name off the artifact would
            # return None for every artifact, fail the guard closed on all
            # of them, and silently drop every legitimate gate detection.
            reach = (sha_reachable(root, attestation.get("head_sha", ""))
                     if guard_active else True)
            if source == "gate" and reach is not True:
                # FAIL CLOSED on None as well as False: "could not
                # determine" must not read as "safe to ingest", or the
                # guard passes in exactly the broken-git case it cannot
                # evaluate. A dropped gate record is recoverable by
                # re-running review; a synthetic one silently credits a
                # rule with a hit it never scored.
                unreachable.append(
                    f"{artifact.parent.name}: gate artifact names commit "
                    f"{(attestation.get('head_sha') or '?')[:12]}, which "
                    + ("no ref contains" if reach is False
                       else "reachability could not be determined for")
                    + " — not ingested")
                continue
            if source == "attest" and reach is False:
                unreachable.append(
                    f"{artifact.parent.name}: attestation names commit "
                    f"{(attestation.get('head_sha') or '?')[:12]}, which "
                    "no ref contains — a squash-merged or abandoned "
                    "branch, or an amended commit. `attest check` reads "
                    "the PR's own range, so this shard could never "
                    "satisfy it; re-run `warden attest write` on the "
                    "current HEAD if the review still applies")
                continue
            if source == "attest" and reach is None:
                undetermined.append(
                    f"{artifact.parent.name}: could not determine whether "
                    f"{(attestation.get('head_sha') or '?')[:12]} is "
                    "reachable; ingested anyway, because losing a review "
                    "round is worse than keeping an orphan")
        if filed:
            # Marked here too, not only on a fresh write: every run dir
            # predating this guard is unmarked, and this is the seam that
            # retires them one ingest at a time. Marked with the name of
            # the shard that ACTUALLY holds the event, which is not always
            # the name computed above — a shard's digest moves when its
            # records reserialize (tag canonicalization, migration), so
            # recording the computed name would make a correctly-filed
            # event look absent on the next sweep and report it as another
            # branch's. `_event_already_filed` compares normalized record
            # identity and is the authority; the marker is a hint.
            if source == "attest":
                _note(_mark_consumed(root, artifact.parent, filed.name))
            continue
        if already:
            # The event is NOT in this store, and this run dir was already
            # sharded — the cross-branch case. Re-minting
            # would file another branch's review event into this one's
            # range, where `attest check` would credit a review that never
            # ran here. Reported, never silent: a shard genuinely lost
            # before it was committed is a real case, and the remedy has
            # to be discoverable.
            elsewhere.append(
                f"{artifact.parent.name}: already ingested as "
                f"{already['shard']}, and this branch's store holds no "
                f"shard for the event — not re-filed. If it was genuinely "
                f"lost before being committed, delete "
                f"{_consumed_path(root, artifact.parent)} to re-file it")
            continue
        # The range binding, at the moment the artifact CROSSES into the
        # corpus — after the already-filed and already-consumed
        # short-circuits above, so an idempotent re-ingest of a shard that
        # is already committed does not re-warn on every CI run. Advisory
        # only: it is appended to the result and NEVER consulted below, so
        # no reading of it can stop a shard being written.
        if source == "attest":
            kind, message = range_binding_at_ingest(root, attestation)
            # EVERY attest shard gets a row, `ok` included — the sibling
            # `attest write` stamps its reading on every run for the same
            # reason, and a list that only ever holds problems cannot tell
            # "all clean" from "nothing could be checked".
            range_bindings.append({"artifact": artifact.parent.name,
                                   "kind": kind, "message": message})
        envelope = {
            "schema": 1,
            "source": source,
            "sha": attestation.get("head_sha", ""),
            "base_sha": attestation.get("base_sha", ""),
            "rules_version": attestation.get("rules_version", ""),
            "reviewed_at": attestation.get("reviewed_at", ""),
            # Carried so `attest check` can match a rebased range by
            # content. It rides the envelope rather than a
            # record because it describes the REVIEW EVENT's range, not any
            # one finding — a clean round has no records and needs it too.
            **({"range_patch_ids": attestation["range_patch_ids"]}
               if source == "attest" and attestation.get("range_patch_ids")
               else {}),
            # The event rides on EVERY attest shard, not only the
            # findings-free one: a verdict recorded on some events and not
            # others is a corpus that can still only be asked half the
            # question. Gate shards carry neither — a checker firing has no
            # verdict and no reviewers.
            **(build_review_event(attestation) if source == "attest" else {}),
        }
        # Redaction is a property of the SHARD boundary, so the envelope
        # crosses it too, not only the records. Idempotent on
        # every real value: a 40-hex sha and a rules_version hash cannot
        # match a secret pattern.
        shard = {**_redact_value(envelope), "records": records}
        body = json.dumps(shard, indent=2) + "\n"
        if shard_path.exists() and shard_path.read_text() == body:
            if source == "attest":
                _note(_mark_consumed(root, artifact.parent, shard_path.name))
            continue
        shard_path.write_text(body)
        if source == "attest":
            _note(_mark_consumed(root, artifact.parent, shard_path.name))
        new_shards.append(shard_path.name)
        # Reported only for a write that happened: a rejected artifact's
        # migration never sharded, and an idempotent re-ingest of an
        # already-sharded one produces nothing to announce.
        migrated_rule_ids.extend(
            f"{artifact.parent.name}: {m}" for m in migrated)

    # Rebuild the cache from every shard; last-write wins per id.
    #
    # VALIDATED, and retroactively on purpose: this reader cannot build an
    # index keyed on `record["id"]` out of records that have none, and a shard
    # the other three readers refuse is not evidence this one may quietly
    # keep. The remedy for a refused shard is to repair it at its producer,
    # never to edit committed evidence to move a number — and
    # docs/wiki/Adopting.md states the upgrade cost for a store that has one.
    by_id: dict[str, dict] = {}
    for shard_path in sorted(list(shard_dir.glob("*.json"))
                             + list(gate_dir.glob("*.json"))):
        # Refused BY NAME, not indexed blind. `record["id"]` on a shard whose
        # `records` holds anything but objects would raise a bare TypeError or
        # KeyError from inside the cache rebuild, naming neither the shard nor
        # the reason — in the one command a consumer is told to run to find
        # out WHICH shard is bad. AttestError so the CLI prints
        # `warden: <message>` and exits 2 rather than a traceback; the shared
        # validator so this reader cannot drift from the other three.
        #
        # `read_shard`, not `json.loads(...).get("records", [])`: the ENVELOPE
        # is checked too, so a top-level array is refused by name rather than
        # dying as AttributeError, and — the fail-open half — a shard with no
        # `records` key is refused rather than defaulting to EMPTY and being
        # silently accepted. `require_ids` because this is the reader that
        # indexes by id.
        try:
            filed = read_shard(shard_path, require_ids=True)["records"]
        except ValueError as e:
            raise AttestError(f"{e} — the corpus cannot be rebuilt until "
                              "that shard is removed or repaired at its "
                              "source") from e
        for record in filed:
            by_id[record["id"]] = record
    ordered = sorted(by_id.values(),
                     key=lambda r: (r["ts"], r.get("sha", ""), r.get("seq", 0)))
    # Fold retired tag aliases before the cache is written: shards keep the
    # historical name (committed evidence), the derived index sees one key.
    from .tags import canonicalize_records, ceiling_status, unknown_tags
    ordered = canonicalize_records(root, ordered)
    cache = memory_dir(root) / CACHE_NAME
    cache.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in ordered))
    # The CACHE is one row per record id — the shards' complete index, and
    # `total_records` below is its row count. A READING OF THE CORPUS is a
    # different question, and `memory stats` answers every one of them over
    # the folded records, so this command must too: `undeclared_at_bar`
    # counts judged RECORDS against DECLARE_MIN_N, so over unfolded records
    # one coined tag carried through three re-attestations would reach the
    # declaration bar here while absent from `memory stats` — two readers of
    # one corpus disagreeing about a verdict that PRINTS "TAG CEILING
    # BREACHED".
    #
    # `unknown_tags` reads the SAME folded list: `tags.audit` derives its own
    # `unknown` from the records `stats` hands it — which are folded — so an
    # unfolded `used` here would print a different UNKNOWN verdict for one
    # store. One answer for both readers; the fold is what a reader of this
    # corpus counts.
    folded = fold_restatements([dict(r) for r in ordered])
    used = [tag for r in folded for tag in (r.get("tags") or [])]
    # Decisions ride the same out -> committed-shard path but
    # into their OWN store, kept apart from the findings corpus above: a
    # decision is a ruling, not a review event, and must never be counted as
    # one. Swept here so `warden memory ingest` is the single command that
    # commits everything the run dir accumulated.
    from . import decide as _decide
    try:
        decisions = _decide.ingest(root)
    except OSError as e:
        # The findings cache is already written above; a filesystem failure in
        # the decision sweep must not undo the review ingest that is this
        # command's critical job. Reported as a skipped decision, never fatal.
        decisions = {"new_decisions": [],
                     "skipped_decisions": [f"decision sweep failed: {e}"]}
    return {"new_shards": new_shards, "skipped": skipped,
            "invalid_rule_ids": invalid_rule_ids,
            "migrated_rule_ids": migrated_rule_ids,
            "unreachable": unreachable,
            "elsewhere": elsewhere,
            "undetermined": undetermined,
            # Reported, never carried into the shard: the run dir is where this
            # signal ENDS, by founder ruling. The attestation
            # schema declares no field for it and the envelope above is built
            # from a fixed field list, so `memory stats` and the retro cannot
            # count how often it fires — and no doc may say they can. One row
            # per attest shard written, whatever the reading.
            "range_bindings": range_bindings,
            "marker_failures": marker_failures,
            "cache_tracked": _cache_is_tracked(root),
            "gate_tracked": _gate_dir_is_tracked(root),
            "unknown_tags": unknown_tags(root, used),
            # The declared drift ceiling, read over the SAME canonicalized
            # records the cache was just written from — a fold discharges the
            # obligation exactly as a declaration does, so the reading must
            # come after canonicalization, never before — and
            # after the restatement fold, so this reader and `memory stats`
            # cannot return different verdicts.
            "tag_ceiling": ceiling_status(root, folded),
            # Reconciles the two record counts a reader meets: this artifact's
            # `total_records` is the cache's rows, `memory stats` counts
            # findings, and the difference is exactly this number.
            "restatements_folded": len(ordered) - len(folded),
            "new_decisions": decisions["new_decisions"],
            "skipped_decisions": decisions["skipped_decisions"],
            "total_records": len(ordered), "cache": str(cache)}


def _gate_dir_is_tracked(root: Path) -> bool:
    """Is any gate shard committed? It must not be (rung R-11).

    The rung catches this at certification; this catches it at the moment
    the shards are written, which is when the person can still act on it.
    Same shape as `_cache_is_tracked` — validating a gate rule with a
    mutation proof is what walks people into it.
    """
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch",
             f".warden/{MEMORY_DIRNAME}/{GATE_SUBDIR}/"],
            cwd=root, capture_output=True)
    except (OSError, FileNotFoundError):
        return False
    return proc.returncode == 0


def _cache_is_tracked(root: Path) -> bool:
    """Is the derived cache committed? It must not be.

    Every ingest rewrites it, so a tracked cache conflicts on every
    concurrent branch — and it carries nothing the shards don't.
    """
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", f".warden/{MEMORY_DIRNAME}/{CACHE_NAME}"],
            cwd=root, capture_output=True)
    except (OSError, FileNotFoundError):
        return False
    return proc.returncode == 0


def cache_provenance(root: Path) -> dict:
    """Which corpus the derived cache actually holds, and whether it is behind.

    `memory stats` computes its precision rows from the CACHE and its REVIEW
    EVENTS section from the shard directory, live, and the two can be days
    apart: a checkout can print its shard count beside precision rows
    computed over a cache written before a field (such as `lens`) existed, so
    a table over that field renders no rows and reads exactly like a corpus
    that genuinely carries none. This line says which corpus each half is
    over.

    Deliberately NOT a change of source. `memory recall` and `warden plan` read
    this same cache, so a `stats` that quietly read the shards instead would
    answer a different question than the priors a builder is handed, and the
    disagreement would move rather than close. So this computes rows and
    mtime for the cache, the shard count beside it, and whether any shard
    file is NEWER than the cache.

    Never raises. It is a provenance line on a report, and a report that dies
    computing its own header is worse than one that says it cannot tell —
    an unreadable store is reported as `stale` with the cause named, which is
    the fail-closed reading: a check that cannot evaluate behaves like a hit.

    STALENESS IS AN MTIME COMPARISON, and that is a heuristic with a known
    direction rather than a proof. A fresh clone stamps every file at checkout
    time, so a cache whose CONTENT is current can read as stale after a pull.
    The error is one-sided on purpose: a false "run ingest" costs one command,
    and a false "current" lets a reader take a corpus days behind for the
    answer. An exact check would have to
    re-read every shard, which is the cost the derived cache exists to avoid.
    """
    cache = memory_dir(root) / CACHE_NAME
    doc: dict = {"cache": f".warden/{MEMORY_DIRNAME}/{CACHE_NAME}",
                 "exists": False, "rows": 0, "written_at": None,
                 "shards": None, "newer_shards": 0, "stale": True,
                 "problem": None}
    # The STORE is counted FIRST, and unconditionally. The missing-cache state is
    # the one a fresh clone and every CI run is in, and it is exactly where the
    # count matters most: a repo with 176 unread shards and one with none are
    # different situations, and returning before the listing would render
    # them identically as "unknown shard(s)".
    shard_dir = memory_dir(root) / SHARD_SUBDIR
    shard_problem = None
    try:
        # ASK THE SYSCALL, for the reason `records_from_shards` does: a DANGLING
        # SYMLINK at the attest path raises FileNotFoundError from `iterdir`,
        # which would read as "a real, empty store" and render stale: False
        # with no problem — a false CURRENT, the direction this function must
        # never err in.
        link = os.lstat(shard_dir)
        if stat.S_ISLNK(link.st_mode):
            os.stat(shard_dir)        # resolves, or raises below
        shards = [q for q in shard_dir.iterdir() if q.name.endswith(".json")]
    except FileNotFoundError as e:
        if shard_dir.is_symlink():
            shards = None
            shard_problem = (f".warden/{MEMORY_DIRNAME}/{SHARD_SUBDIR} is a "
                             "dangling symlink — the committed store it points "
                             "at cannot be read, so the cache cannot be checked "
                             "against it")
        else:
            # A real, empty store: no review has ever been sharded here.
            shards = []
            del e
    except OSError as e:
        shards = None
        shard_problem = (f".warden/{MEMORY_DIRNAME}/{SHARD_SUBDIR} cannot be "
                         f"listed ({e}), so the cache cannot be checked "
                         "against the committed store")
    doc["shards"] = None if shards is None else len(shards)
    try:
        st = cache.stat()
        doc["exists"] = True
        doc["written_at"] = datetime.fromtimestamp(
            st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
        doc["rows"] = sum(1 for line in cache.read_text(
            encoding="utf-8", errors="replace").splitlines() if line)
    except FileNotFoundError:
        if shards == []:
            # No cache AND no committed store is a CONSISTENT, genuinely empty
            # corpus, not a stale one: there is nothing unread to warn about.
            # `examples/hello-svc` ships exactly this shape, and warning there
            # would spend the signal on the one repo that cannot be behind.
            doc["stale"] = False
            return doc
        doc["problem"] = (
            "no derived cache — nothing has ingested in this checkout, so "
            "every figure computed from it is over an EMPTY corpus"
            + (f", while the committed store holds {len(shards)} shard(s)"
               if shards else "")
            + ". Run `warden memory ingest`")
        return doc
    except OSError as e:
        doc["problem"] = f"the derived cache cannot be read ({e})"
        return doc
    if shard_problem is not None:
        doc["problem"] = shard_problem
        return doc
    newer = 0
    for shard in shards:
        try:
            if shard.stat().st_mtime > st.st_mtime:
                newer += 1
        except OSError:
            # Unreadable shard: cannot be ruled out as newer, so it counts.
            newer += 1
    doc["newer_shards"] = newer
    doc["stale"] = newer > 0
    if newer:
        doc["problem"] = (
            f"{newer} of {len(shards)} committed shard(s) are NEWER than the "
            "derived cache, so every figure computed from it is over an older "
            "corpus than the one on disk — including any field that shipped "
            "since. Run `warden memory ingest`")
    return doc


def render_corpus_provenance(doc: dict) -> str:
    """The header line `memory stats` was missing — printed above every row it
    qualifies, never below, because a caveat under a table is read after the
    number it was supposed to qualify."""
    where = doc["cache"]
    shards = ("unknown" if doc["shards"] is None else str(doc["shards"]))
    line = (f"CORPUS — precision rows below are computed over the DERIVED "
            f"cache {where}: {doc['rows']} row(s)"
            + (f", written {doc['written_at']}" if doc["written_at"] else "")
            + f". The committed store beside it holds {shards} shard(s); "
              "REVIEW EVENTS is counted from those, live. The cache is "
              "rebuilt only by `warden memory ingest`.")
    lines = [line]
    if doc.get("problem"):
        lines.append(f"  STALE — {doc['problem']}.")
    return "".join(f"{out}\n" for out in lines)


def load_records(root: Path) -> list[dict]:
    """The DERIVED index, read through the same definition as the shards.

    `memory recall`, `memory stats` and `warden plan` all read here, so the
    rows are validated through the same record contract as the shards: a
    single malformed row — a `ts` that is a dict, a `file` that is null —
    would otherwise reach `_streaks_by_round` and `_is_skill_file` as an
    unhashable key or an AttributeError and take the command down with a
    traceback. That the cache is derived and gitignored makes it a likelier
    source of one, not a safer one: it survives a branch switch, a
    half-finished sweep and a hand edit.

    AttestError rather than ValueError, matching the cache REBUILD one file
    over: the CLI prints `warden: <message>` and exits 2, and the remedy for a
    derived file is a rebuild — `warden memory ingest` — not an edit, so the
    message says which.
    """
    from .tags import canonicalize_records
    cache = memory_dir(root) / CACHE_NAME
    if not cache.is_file():
        return []
    where = f".warden/memory/{CACHE_NAME}"
    try:
        rows = [json.loads(line)
                for line in cache.read_text().splitlines() if line]
    except (OSError, ValueError) as e:
        # ValueError covers JSONDecodeError AND the UnicodeDecodeError a
        # non-UTF-8 cache raises — the same tuple `read_shard` uses, for the
        # same reason.
        raise AttestError(f"{where}: unreadable derived cache ({e}) — it is "
                          "rebuilt from the committed shards, so delete it "
                          "and run `warden memory ingest`") from e
    try:
        validate_shard_records(where, rows)
    except ValueError as e:
        raise AttestError(f"{e} — the derived cache is rebuilt from the "
                          "committed shards, so delete it and run `warden "
                          "memory ingest`") from e
    # Re-folding a cache ingest already canonicalized is a no-op; folding
    # here as well covers a cache written before the alias was declared.
    # Restatements fold at the READ seam, never in the cache: the cache is
    # one row per record id, the shards' complete index, and what a reader
    # counts is findings.
    return fold_restatements(canonicalize_records(root, rows))


# ── the record contract: one table, every reader ──────────────────────────
#
# WHAT A SHARD RECORD IS, as types, in one place. `build_records` is the sole
# writer of these fields and `validate_shard_records` is the sole reader-side
# gate on them, so a field's type belongs in neither of them and in both. A
# single malformed field — `ts` as a dict, `sha` as a list, `file` as null,
# `lens` as an int — would otherwise surface as an uncaught
# TypeError/AttributeError/KeyError from somewhere deep inside a counting
# seam, with no verdict, no artifact, and nothing naming the file.
#
# The table is the mechanism and `tests/test_malformation_matrix.py` is its
# fence: that test derives the field list from `build_records`' own AST and
# fails on any field this table does not classify. A field added to the
# producer is therefore covered on the next test run, with nothing here to
# remember to edit.
#
# `required` is the one judgement call: REFUSE an absent field only where no
# honest default exists.
#
# * `ts`, `rule_id` and `status` are REQUIRED — present AND non-empty, because
#   the empty string reaches the same one-bucket fold that absence does and
#   `build_records` can write it (`ts` comes from `attestation.get(
#   "reviewed_at", "")`). Every counting seam buckets by
#   them — `_round_key` keys a streak on (ts, sha), the per-rule tally keys on
#   rule_id, `_JUDGED_STATUSES` keys on status — and defaulting to `""` folds
#   distinct records into ONE bucket, which is fail-OPEN in the Wilson math: a
#   rule's refutations silently merging into one round can only ever make a
#   promotion bar easier to clear.
# * Everything else DEFAULTS at the reader, because a render field or a filter
#   key has an honest empty value and a blocking refusal here would be a
#   widening measured only against THIS repo's corpus. The live consumer runs
#   a pinned platform against a store nobody here has read; an absent
#   `dir_prefix` must degrade its recall, not turn its ingest red on the bump.
#
# The TYPE half carries no such risk: the records this producer writes carry
# every field at the declared type, so typing them refuses nothing the
# producer has written — the same footing `status` and the envelope `verdict`
# were added on.
_STR = (str,)
_INT = (int,)
_LIST_OF_STR = "list[str]"
_LIST_OF_STR_OR_NULL = "list[str]|null"
# Spelled for the author who has to fix the shard, not for the interpreter.
_TYPE_WORDS = {str: "string", int: "integer"}

RECORD_FIELD_CONTRACT: dict[str, tuple[object, bool]] = {
    # field:            (tolerated type, absence is refused)
    "id": (_STR, False),           # required only for the cache reader, which
                                   # asks via `require_ids` — see below
    "ts": (_STR, True),
    "seq": (_INT, False),
    "source": (_STR, False),
    "sha": (_STR, False),
    "base_sha": (_STR, False),
    "rule_id": (_STR, True),
    # NULL IS LEGAL HERE, and only here: `tags: null` is an older shard's
    # spelling of "no tags" and `[]` is the honest default for it — a property
    # a test in `tests/test_vocabulary_check.py` pins. Readers apply that
    # default with `or []`, because a `.get("tags", [])` default fires on
    # ABSENT and not on present-and-null, and `None` is not iterable.
    "tags": (_LIST_OF_STR_OR_NULL, False),
    "dir_prefix": (_STR, False),
    "file": (_STR, False),
    "line": (_INT, False),
    "severity": (_STR, False),
    "finding": (_STR, False),
    "evidence": (_STR, False),
    "status": (_STR, True),
    "origin": (_STR, False),
    # Provenance and attribution, written only when the producer knew them.
    # Absent on most of the corpus, so absence here is the NORMAL state and
    # refusing it would refuse the corpus.
    "pr": (_STR, False),
    "bead": (_STR, False),
    "legacy_rule_id": (_STR, False),
    "reason": (_STR, False),
    "lens": (_STR, False),
    "round": (_INT, False),
}


def _field_type_problem(field: str, value: object) -> str:
    """Why this PRESENT value is not what `field` holds, or "" if it is."""
    expected, _ = RECORD_FIELD_CONTRACT[field]
    if expected in (_LIST_OF_STR, _LIST_OF_STR_OR_NULL):
        if value is None and expected is _LIST_OF_STR_OR_NULL:
            return ""
        if isinstance(value, list) and all(isinstance(t, str) for t in value):
            return ""
        return f"`{field}` that is not a list of strings ({value!r})"
    # `bool` is an `int` in Python and never a legal `seq`, `line` or `round`:
    # `True` would sort among the integers and read as 1.
    if isinstance(value, expected) and not isinstance(value, bool):
        return ""
    names = " or ".join(_TYPE_WORDS.get(t, t.__name__) for t in expected)
    article = "an" if names[0] in "aeiou" else "a"
    return f"`{field}` that is not {article} {names} ({value!r})"


def validate_shard_records(name: str, records: object, *,
                           require_ids: bool = False) -> None:
    """Refuse a shard whose `records` is not a list of well-formed records.

    ONE definition, called by every reader of the store — `records_from_shards`
    (which raises), `review_events` (which counts `unreadable`), `memory
    ingest`'s cache rebuild (which re-raises as AttestError) and
    certification's E-02. Readers that each carry their own idea of what a
    shard IS drift apart, in either direction, even when each change is
    individually correct; parity is this function, not a claim any reader
    makes.

    `require_ids` is the ONE strictness that is not shared, and it is a flag
    rather than another spelling of this check because only one reader needs
    it: `memory ingest`'s cache rebuild is the only caller that INDEXES by
    `record["id"]`. Off by default; the flag says which reader is asking.

    Precisely what the flag gates: `id`'s TYPE is checked for every reader,
    like every other field's (`RECORD_FIELD_CONTRACT`), and the FLAG gates
    only whether `id` must be PRESENT and non-empty.

    WHAT it checks is `RECORD_FIELD_CONTRACT`'s, not this function's. An
    enumeration inside a validator is an enumeration that drifts from its
    producer. The table is one value, the producer's own field list fences it
    (`tests/test_malformation_matrix.py`), and the message below is generated
    so a new field cannot arrive with no sentence to print.
    """
    if not isinstance(records, list):
        raise ValueError(f"{name}: not an evidence shard (no records list)")
    for i, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"{name}: record {i} is not a mapping "
                             f"({type(record).__name__})")
        # TYPE first, across every field, then PRESENCE — two passes, because
        # a record usually has at most one defect and the reader should be told
        # about what is WRONG before what is MISSING. One interleaved pass
        # would report "no `ts`" for a record whose real defect is a
        # list-valued `rule_id`, sending the author to the wrong line.
        for field in RECORD_FIELD_CONTRACT:
            if field not in record:
                continue
            problem = _field_type_problem(field, record[field])
            if problem:
                # A PRESENT value of the wrong type, for any field the
                # producer writes: `ts` as a dict would reach
                # `_streaks_by_round` as an unhashable key, `file` as null
                # would reach `_is_skill_file` as an AttributeError. One
                # table, one sentence, every field.
                raise ValueError(f"{name}: record {i} carries a {problem}")
        for field, (_expected, required) in RECORD_FIELD_CONTRACT.items():
            if required and not record.get(field):
                # EMPTY, not just absent: `build_records` derives `ts` from
                # `attestation.get("reviewed_at", "")`, so an artifact with no
                # `reviewed_at` — which ingest accepts whenever the artifact
                # carries findings — would file records with `ts: ""`, and
                # `_round_key` would bucket every one of them into
                # `("", sha)`. That is exactly the ONE-bucket fold the
                # `required` column exists to prevent, reached by the empty
                # string instead of by absence.
                #
                # A present-but-wrongly-TYPED value is reported by the loop
                # above, which runs first, so this arm only ever speaks about
                # an absent or empty one.
                absent = field not in record
                raise ValueError(
                    f"{name}: record {i} carries "
                    + ("no " if absent else "an empty ")
                    + f"`{field}` — every reader of this store buckets by it")
        if require_ids and not record.get("id"):
            raise ValueError(f"{name}: record {i} carries no usable `id` "
                             f"({record.get('id')!r}) — the cache rebuild "
                             f"indexes by it")


def _artifact_shape_problem(doc: object) -> str:
    """Why this attest/gate ARTIFACT cannot be read, or "" if it can.

    The write-seam counterpart of `validate_shard_envelope`, asking the same
    questions of the `findings` an artifact carries that the shard side asks
    of the `records` a shard carries. It is a separate function rather than
    the same one because the two shapes genuinely differ — a finding has no
    `id`, no `ts` and no `status` until `build_records` derives them — so
    folding them together would mean a flag per field and a validator that
    agreed with neither caller.

    What it must never become is a THIRD idea of what a review event is: it
    checks only what the code below it would otherwise meet as an
    AttributeError, and `validate_shard_records` still has the final word on
    the records that come out.
    """
    if not isinstance(doc, dict):
        return (f"not a review artifact (top level is "
                f"{type(doc).__name__}, not a JSON object)")
    findings = doc.get("findings", [])
    if not isinstance(findings, list):
        return (f"not a review artifact (`findings` is "
                f"{type(findings).__name__}, not a list)")
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            return (f"finding {i} is not a mapping "
                    f"({type(f).__name__})")
        if "rule_id" in f and not isinstance(f["rule_id"], str):
            return (f"finding {i} carries a `rule_id` that is not a "
                    f"string ({f['rule_id']!r})")
        tags = f.get("tags")
        if tags is not None and (
                not isinstance(tags, list)
                or not all(isinstance(t, str) for t in tags)):
            # HERE and not at the crossing, because by the crossing the
            # evidence is gone: `build_records` does `sorted(f.get("tags",
            # []))`, which turns the STRING "flaky" into the perfectly valid
            # list ['a','f','k','l','y'] — so the shard-side `tags` guard is
            # structurally unable to fire, and five fabricated names would
            # enter the committed corpus and the vocabulary ceiling with
            # `skipped` empty. ABSENT stays legal: `build_records` defaults
            # it to [].
            return (f"finding {i} carries `tags` that is not a list of "
                    f"strings ({tags!r})")
    return ""


def validate_shard_envelope(name: str, doc: object, *,
                            require_ids: bool = False) -> None:
    """Refuse a shard whose ENVELOPE is not a records-bearing JSON object.

    The other half of `validate_shard_records`, so every reader refuses a bad
    envelope by name, in all three directions:

    * a top-level JSON ARRAY would die as `AttributeError: 'list' object has
      no attribute 'get'` from a bare `.get("records")`;
    * a shard with NO `records` key would be SILENTLY ACCEPTED, because the
      same call defaults to `[]` — the fail-OPEN direction and the worst of
      the three, since `memory ingest` is the one command a consumer is told
      to run to find out WHICH shard is bad;
    * a record with no `id` would die as `KeyError: 'id'` (see
      `require_ids`).

    A missing `records` key and a `records` that is not a list are ONE
    message on purpose — `validate_shard_records` already owns that sentence
    and `doc.get("records")` hands it None — so the two cannot drift into
    two different accounts of the same refusal.
    """
    if not isinstance(doc, dict):
        raise ValueError(f"{name}: not an evidence shard "
                         "(not a JSON object)")
    if "verdict" in doc and not isinstance(doc["verdict"], str):
        # The one envelope key a reader dereferences by HASH:
        # `review_events` does `_VERDICT_BUCKETS.get(doc.get("verdict", ""))`,
        # and that line sits OUTSIDE the try whose `unreadable` bucket exists
        # for exactly this — so `"verdict": ["clean"]` would raise `TypeError:
        # unhashable type` past `certify.run`'s bare `_run_check` loop and
        # `cli._cmd_certify`'s `except CertifyError`: traceback, exit 2, no
        # verdict, no certification.json. It is refused HERE, in the shared
        # definition, never inside `review_events`: moving the bucket lookup
        # into that reader's try would repair one reader and leave
        # `records_from_shards` accepting a shard `review_events` refuses —
        # a parity break in the other direction.
        #
        # ABSENT stays legal: shards that predate the field carry no
        # `verdict`, and `review_events` buckets those as
        # `verdict_unrecorded` on purpose. Only a PRESENT one of the wrong
        # type is a refusal.
        raise ValueError(f"{name}: carries a `verdict` that is not a string "
                         f"({doc['verdict']!r}) — every reader buckets by it")
    validate_shard_records(name, doc.get("records"), require_ids=require_ids)


def read_shard(path: Path, *, require_ids: bool = False) -> dict:
    """Parse and validate one committed shard, or raise ValueError naming it.

    The read and the shape in one definition, so every caller wraps the same
    exceptions: a shard that is unreadable, not JSON, or not UTF-8 is named by
    file for every reader rather than escaping one of them as a traceback.
    """
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        # ValueError covers JSONDecodeError AND UnicodeDecodeError, so a shard
        # that is not UTF-8 is named by file, not by a byte position.
        raise ValueError(f"{path.name}: unreadable shard ({e})") from e
    validate_shard_envelope(path.name, doc, require_ids=require_ids)
    return doc


def records_from_shards(root: Path) -> list[dict]:
    """Records straight from the committed shards, bypassing the cache.

    The cache is derived and gitignored (R-08), so anything that must hold in
    CI — certification above all — reads the shards, which are the committed
    source of truth. FAIL CLOSED on an unreadable shard: silently skipping
    one deletes its records from the Wilson math, and the shard most likely
    to matter is the one carrying a rule's refutations — a corrupted shard
    must never IMPROVE a score.

    The same rule for the DIRECTORY and for each RECORD. `Path.glob` swallows
    the directory's own error — a plain file, a dangling symlink, or a chmod
    000 dir at the attest path would read as an EMPTY corpus, the exact
    hazard tags.py documents for the rules dir. An ABSENT dir is different
    and stays empty: no review has run yet (hello-svc ships none). And a
    shard whose envelope is fine but whose records are not mappings, or whose
    `tags` is not a list of strings, would escape every reader as an
    AttributeError or TypeError. Refused here, by name, so every reader gets
    one answer.
    """
    shard_dir = memory_dir(root) / SHARD_SUBDIR
    where = f".warden/memory/{SHARD_SUBDIR}"
    # ASK THE SYSCALL, never `Path.exists()` / `Path.is_symlink()`. Which
    # OSErrors those two swallow is an interpreter detail that differs across
    # the CPythons this repo declares and tests: on 3.12 `exists()` propagates
    # PermissionError, and on 3.14 it swallows it, so the same unsearchable
    # tree would read as an EMPTY CORPUS. A fail-closed guard whose verdict
    # depends on the interpreter is not a guard; `os.lstat` raises the same
    # OSError everywhere and every arm below names it.
    try:
        st = os.lstat(shard_dir)
    except FileNotFoundError:
        # ABSENT is a real, empty corpus: no review has run yet, and
        # hello-svc ships no attest dir at all.
        st = None
    except OSError as e:
        raise ValueError(f"{where}: cannot be listed ({e})") from e
    if st is not None and stat.S_ISLNK(st.st_mode):
        try:
            os.stat(shard_dir)
        except FileNotFoundError as e:
            raise ValueError(f"{where}: a dangling symlink — the corpus it "
                             "points at cannot be read") from e
        except OSError as e:
            raise ValueError(f"{where}: cannot be listed ({e})") from e
    try:
        listed = [] if st is None else list(shard_dir.iterdir())
    except OSError as e:
        raise ValueError(f"{where}: cannot be listed ({e})") from e
    records: list[dict] = []
    for shard in sorted(p for p in listed if p.name.endswith(".json")):
        # Read + envelope + records, through the one definition every reader
        # of this store calls.
        records.extend(read_shard(shard)["records"])
    # The shards keep the historical tag; the reader folds declared aliases
    # so certification's Wilson math counts a merged class under one key —
    # and folds restatements, so it counts a finding re-attested under a
    # moved head once. Same two folds as `load_records`: the
    # cache path and the shard path must never disagree on n.
    from .tags import canonicalize_records
    return fold_restatements(canonicalize_records(root, records))


def excerpt(text: str, limit: int = RECALL_EXCERPT) -> str:
    """A bounded, one-line reading of a finding for recall and the plan
    packet: whitespace collapsed so a multi-line finding
    stays one bullet; at most `limit` characters; cut at the last sentence
    end inside the window's second half, else the last word boundary there,
    else hard at the limit; a cut is marked with `…` so the reader knows
    there is more. The stored record is untouched — this is a rendering.
    """
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    window = flat[:limit - 1]
    floor = limit // 2
    cut = max(window.rfind(end) for end in (". ", "? ", "! "))
    if cut >= floor:
        return window[:cut + 1] + "…"
    cut = window.rfind(" ")
    if cut >= floor:
        return window[:cut].rstrip() + "…"
    return window + "…"


class RecallSet(list):
    """What `recall` selected, and what it saw doing so: `relevant` is how
    many records matched the query inside the stale horizon, `skipped` how
    many of those were dropped because their rendered line did not fit the
    budget. A list, so every caller that indexes, counts or iterates the
    result is untouched; the two counts ride along for `render_recall` and
    `warden plan`, which say them out loud — a skipped record that left no
    trace read exactly like an absent one."""
    relevant: int = 0
    skipped: int = 0


def _recurrence_key(record: dict) -> tuple[str, str]:
    """The SHAPE a record recurs as — (rule, locality) — the key
    `render_recall` counts "[recurred Nx here]" by and `recall` ranks by."""
    return (record["rule_id"], record.get("dir_prefix") or "")


def recall(root: Path, *, files: list[str], role: str,
           tags: list[str] | None = None,
           rules: list[str] | None = None) -> RecallSet:
    """Select role-split records keyed by (dir_prefix, tags, rule_id) —
    never bare file paths. Ranked by recurrence key, then recency; K- and
    character-budgeted, the budget sized to K lines of bounded excerpt.

    Root-level files have no dir_prefix and are reachable via tags/rules
    keys only — directories are the stable locality signal.

    Each returned record is a COPY of its cache row carrying one derived
    field, `recurred`: how many relevant, unexpired records share its
    (rule, dir_prefix) shape. `render_recall` prints it; the cache row is
    never rewritten."""
    if role == "reviewer":
        wanted = _REVIEWER_STATUSES
    elif role == "examiner":
        wanted = _EXAMINER_STATUSES
    else:
        raise MemoryError(f"unknown recall role {role!r} (reviewer|examiner)")
    query_prefixes = {_dir_prefix(f) for f in files if _dir_prefix(f)}
    # Queries fold like records do: a caller quoting a retired tag or
    # unmapped slug (copied from an old shard) must still hit the merged key.
    from .tags import canonicalize_query
    query_tags, query_rules = canonicalize_query(
        root, tags=tags or [], rules=rules or [])

    def relevant(record: dict) -> bool:
        if record["status"] not in wanted:
            return False
        # `.get`, not `[...]`: `dir_prefix` is a FILTER key with an honest
        # empty value — a record with none is simply not locality-keyed and
        # stays reachable through tags/rules. A BLOCKING refusal in the
        # shared validator would be a widening measured only against this
        # repo's own corpus.
        prefix = record.get("dir_prefix") or ""
        if prefix and any(
                prefix == p or p.startswith(prefix + "/")
                or prefix.startswith(p + "/")
                for p in query_prefixes):
            return True
        # `or []`, not the `.get` default: the default fires on ABSENT and a
        # present `tags: null` — a legal older-shard spelling — would reach
        # `set()` as a TypeError.
        if query_tags & set(record.get("tags") or []):
            return True
        return record["rule_id"] in query_rules

    hits = [r for r in load_records(root) if relevant(r)]
    # Past the stale horizon a record stops being recalled: a two-year-old
    # refutation of code that no longer exists is noise wearing the costume
    # of evidence.
    hits = [r for r in hits if _age_days(r.get("ts", "")) <= STALE_DAYS]

    # Fresh evidence outranks aging evidence regardless of raw recency order.
    def age_rank(record: dict) -> tuple[bool, float]:
        age = _age_days(record.get("ts", ""))
        return (age > FRESH_DAYS, age)

    # Rank by RECURRENCE KEY before recency. Restatements of
    # one finding already fold at the read seam; recency alone would still
    # let ten fresh findings of one (rule, dir_prefix) shape fill every one
    # of the K slots while nine other shapes go unmentioned.
    # So: group by shape; order shapes fresh-before-aging (the lifecycle's
    # de-rank, by each shape's freshest instance), then most-recurrent
    # first, then freshest; and deal one record per shape per pass, so
    # every shape is named before any is repeated.
    by_key: dict[tuple[str, str], list[dict]] = {}
    for record in sorted(hits, key=age_rank):
        by_key.setdefault(_recurrence_key(record), []).append(record)
    shapes = sorted(by_key, key=lambda k: (age_rank(by_key[k][0])[0],
                                           -len(by_key[k]),
                                           age_rank(by_key[k][0])[1]))
    deepest = max((len(v) for v in by_key.values()), default=0)
    ranked = [by_key[k][depth] for depth in range(deepest)
              for k in shapes if depth < len(by_key[k])]
    # Budget the FINAL rendered size: header + per-line recurrence suffixes
    # count against RECALL_BUDGET, not just the raw lines. The excerpt bounds
    # the finding, and K lines fit whenever every key sits inside
    # _KEY_ALLOWANCE. Keys are never cut — a truncated key is a wrong key —
    # so a wider key spends the slack, and the lowest-ranked lines of a
    # wide-keyed recall can fall out. A line that does not fit is SKIPPED,
    # never a stop: one record's width must not decide whether the ones
    # behind it are recalled — and the skip is COUNTED on the result, so the
    # header can say "N record(s) of R relevant, S skipped" rather than
    # passing off a thinned recall, or an emptied one, as the whole history.
    selected, spent = RecallSet(), _HEADER_ALLOWANCE
    selected.relevant = len(hits)
    for record in ranked:
        if len(selected) >= RECALL_K:
            break
        cost = len(render_line(record, role)) + _SUFFIX_ALLOWANCE
        if spent + cost > RECALL_BUDGET:
            selected.skipped += 1
            continue
        selected.append({**record,
                         "recurred": len(by_key[_recurrence_key(record)])})
        spent += cost
    return selected


def render_line(record: dict, role: str) -> str:
    # RENDER fields default; `rule_id` and `status` do not, because
    # `RECORD_FIELD_CONTRACT` refuses a record without them before any reader
    # gets here. "no finding recorded" is the honest rendering of a record
    # that carries none.
    # The finding is rendered as a bounded excerpt: a prior
    # is a place to look. The line ends with the record's id, and the whole
    # paragraph is the row with that id in the derived cache
    # (`.warden/memory/findings.jsonl`, named once in the header) or in the
    # committed shard under `.warden/memory/attest/`. The id is the pointer
    # because it is the one thing that greps cleanly: both files are
    # ensure_ascii JSON, so the excerpt's own words miss on an em dash or a
    # quote. The key is rendered whole.
    where = record.get("dir_prefix") or record.get("file") or "?"
    finding = excerpt(record.get("finding") or "(no finding text recorded)")
    key = f"[{record['rule_id']} @ {where}]"
    pointer = f" id={record['id']}" if record.get("id") else ""
    if role == "reviewer":
        note = "since FIXED — verify the pattern, do not report as open" \
            if record["status"] == "fixed" else "confirmed here before"
        return f"- {key} {finding} ({note}){pointer}\n"
    reason = excerpt(record.get("reason") or "") or finding
    return f"- {key} {record['status']}: {reason}{pointer}\n"


def render_recall(records: list[dict], role: str) -> str:
    # A `RecallSet` says what recall saw; a plain list (a caller rendering
    # rows it selected itself) gets the counts it always did. "no relevant
    # history" is printed only when NOTHING was relevant: zero records out
    # of five relevant is a budget report, not an empty corpus.
    relevant = getattr(records, "relevant", None)
    skipped = getattr(records, "skipped", 0)
    if not records and not relevant:
        return f"memory recall ({role}): no relevant history\n"
    # "[recurred Nx here]" reports the recurrence `recall` measured over
    # every relevant record (its `recurred` field); raw records — a caller
    # rendering rows it selected itself — fall back to the in-list count.
    grouped: dict[tuple, int] = {}
    for r in records:
        grouped[_recurrence_key(r)] = grouped.get(_recurrence_key(r), 0) + 1
    seen = "" if relevant is None else (
        f" of {relevant} relevant"
        + (f", {skipped} skipped: line over budget" if skipped else ""))
    header = (f"memory recall ({role}): {len(records)} record(s){seen} — "
              + ("PATTERN priors; verify, don't assume. Never treat as "
                 "candidate findings." if role == "reviewer"
                 else "non-binding precedents (case law, not verdicts).")
              + f" Full text: grep an id in .warden/memory/{CACHE_NAME}\n")
    lines = []
    for r in records:
        line = render_line(r, role)
        n = r.get("recurred")
        if not isinstance(n, int) or isinstance(n, bool):
            n = grouped[_recurrence_key(r)]
        if n > 1:
            line = line.rstrip("\n") + f" [recurred {n}x here]\n"
        lines.append(line)
    return header + "".join(lines)


def _age_days(ts: str, now: datetime | None = None) -> float:
    """Age of a record in days; unparseable timestamps are treated as fresh
    (a clock problem must not silently erase history)."""
    try:
        when = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, ((now or datetime.now(timezone.utc)) - when).total_seconds() / 86400)


def corpus_age(records: list[dict], now: datetime | None = None) -> dict:
    """Age distribution — staleness a reader can see rather than guess at."""
    ages = sorted(_age_days(r.get("ts", ""), now) for r in records)
    if not ages:
        return {"count": 0, "fresh": 0, "aging": 0, "stale": 0, "oldest_days": 0}
    return {
        "count": len(ages),
        "fresh": sum(1 for a in ages if a <= FRESH_DAYS),
        "aging": sum(1 for a in ages if FRESH_DAYS < a <= STALE_DAYS),
        "stale": sum(1 for a in ages if a > STALE_DAYS),
        "oldest_days": round(ages[-1]),
    }


def dispatch_outcomes(root: Path) -> dict[str, dict]:
    """Per lens, what its dispatches came back WITH.

    Counted from the committed rosters — one roster entry is one dispatch —
    and NEVER from records: a record is a finding, and the whole point of
    this table is the dispatches that produced none. `dispatches` is the
    denominator the review-depth ledger leads with; `no-review` is the half of
    'zero-yield' that was invisible inside it — a refusal, a truncated run, an
    output naming no file in the diff — and `reviewed-clean` the half that
    was a review. A roster entry with no `outcome` is `unknown`: every shard
    written before the field shipped is one, and unknown is never folded into
    reviewed-clean.

    An outcome is counted only where warden DERIVED it — on a shard whose
    `roster_verification` is `verified`. Without `--review-dir` the roster
    is `unverified-roster` and a declared outcome rides into the shard as the
    builder's word; read as a bucket, a builder-typed `reviewed-clean` would
    be a verified clean review asserted out of nothing, the exact claim the
    field exists to stop being free. So an outcome nothing cross-checked is
    `unknown`, as is any outcome on a shard written before the roster check.

    `no-review` means the dispatch did not return, or it returned no findings
    and an output naming no file the diff changed, or the builder declared it
    (a lowered outcome stands). The middle case is usually a refusal, a truncated run or a nothing-to-review, but it is also
    every clean review run from a pack older than the rule telling a reviewer
    to name what it read — a 0.16.x pack against a warden carrying this field.

    Shard acceptance is `review_events`'s, field for field: the same
    `read_shard`, the same skip of a gate shard or a sha-less one. A roster
    entry that is not an object is counted under `<malformed>` rather than
    dropped — a dispatch this tally cannot read is still a dispatch.
    """
    table: dict[str, dict] = {}
    shard_dir = memory_dir(root) / SHARD_SUBDIR
    if not shard_dir.is_dir():
        return table
    for path in sorted(shard_dir.glob("*.json")):
        try:
            doc = read_shard(path)
            sha = doc["sha"]
            source = doc.get("source", "attest")
        except (OSError, ValueError, KeyError, TypeError):
            continue     # review_events counts it unreadable; one bucket
        if source != "attest" or not isinstance(sha, str) or not sha:
            continue
        reviewers = doc.get("reviewers")
        if not isinstance(reviewers, list):
            continue
        for entry in reviewers:
            malformed = not isinstance(entry, dict)
            role = ("<malformed>" if malformed
                    else entry.get("role") if isinstance(entry.get("role"), str)
                    else "<malformed>")
            row = table.setdefault(role, {
                "dispatches": 0, "returned": 0, "reviewed-clean": 0,
                "reviewed-findings": 0, "no-review": 0, "unknown": 0})
            row["dispatches"] += 1
            if not malformed and entry.get("returned") is True:
                row["returned"] += 1
            corroborated = doc.get("roster_verification") == ROSTER_VERIFIED
            outcome = (None if malformed or not corroborated
                       else entry.get("outcome"))
            row[outcome if outcome in OUTCOMES else "unknown"] += 1
    return table


def seat_outcomes(root: Path, records: list[dict]) -> dict:
    """Per reviewer SEAT graph.yaml's `review` block declares: what it raised
    and how that was judged.

    A seat is a role the review block names — every round's roles in round
    order, then the closure and light rounds' — and a finding belongs to the
    seat that RAISED it, never to who was dispatched and never to a later
    seat that re-filed it. The restatement fold keeps the LATEST round's copy
    of a finding, lens included, and a round-2 re-review or a closure round
    re-files every finding still open verbatim, so reading the survivor's
    `lens` would move each one into the re-filer's row. The seat and the
    round are therefore read off the EARLIEST copy (`restated_from` and the
    survivor, ordered by `_round_key` then `seq`); the disposition is the
    fold's, the latest judgment. Every declared seat gets a row, at n=0 when
    it raised nothing, because a seat missing from the table reads as one
    that does not exist. A finding whose raising lens is no declared seat is
    counted in `undeclared` by lens and folded into no row; one whose
    earliest copy carries no lens is counted in `unattributed`, never
    credited to whichever later copy happened to carry one.

    `wilson_lb` is the rule rows' floor over the same numerator, confirmed +
    fixed, and None at n=0, where a floor of 0.0 would read as measured.
    `unique` counts the seat's findings whose (rule as the artifact spelled
    it, file, line) no OTHER declared seat raised in the same review round
    (`_round_key`); `unique_share` is that over n, None at n=0. A seat that
    runs alone in its round is unique by construction, which the share
    cannot tell from a seat that finds what no one else does.

    Read-only: nothing promotes or pauses a seat off these numbers. A
    graph.yaml warden cannot resolve withholds every row and names the cause
    in `error`, rather than printing an empty table that reads as a crew that
    raised nothing.
    """
    from . import graph as graph_mod
    out: dict = {"declared": False, "error": "", "rows": [], "undeclared": {},
                 "unattributed": 0}
    try:
        crew = graph_mod.declared_crew(root)
    except graph_mod.GraphError as exc:
        out["error"] = str(exc)
        return out
    if crew is None:
        return out
    seats: list[str] = []
    for n in sorted(crew["rounds"]):
        seats += [s for s in crew["rounds"][n] if s not in seats]
    for extra in (crew.get("closure"), crew.get("light")):
        if extra:
            seats += [s for s in extra["roles"] if s not in seats]
    out["declared"] = True

    rows = {s: {"seat": s, "confirmed": 0, "fixed": 0, "refuted": 0,
                "dismissed": 0, "n": 0, "unique": 0} for s in seats}
    raised_by: dict[tuple, set[str]] = {}
    mine: list[tuple[str, tuple]] = []
    for r in fold_restatements(records):
        if r.get("status") not in _JUDGED_STATUSES:
            continue
        first = min([r, *r.get("restated_from", ())],
                    key=lambda c: (_round_key(c), c.get("seq", 0)))
        lens = first.get("lens")
        if not lens:
            out["unattributed"] += 1
            continue
        if lens not in rows:
            out["undeclared"][lens] = out["undeclared"].get(lens, 0) + 1
            continue
        row = rows[lens]
        status = r["status"]
        row["dismissed" if status == "dismissed-with-reason" else status] += 1
        row["n"] += 1
        key = (_round_key(first),
               first.get("legacy_rule_id") or first.get("rule_id", ""),
               first.get("file", ""), first.get("line"))
        raised_by.setdefault(key, set()).add(lens)
        mine.append((lens, key))
    for lens, key in mine:
        if raised_by[key] == {lens}:
            rows[lens]["unique"] += 1
    for row in rows.values():
        row["wilson_lb"] = (round(wilson_lower_bound(
            row["confirmed"] + row["fixed"], row["n"]), 3)
            if row["n"] else None)
        row["unique_share"] = (round(row["unique"] / row["n"], 3)
                               if row["n"] else None)
    out["rows"] = [rows[s] for s in seats]
    out["undeclared"] = dict(sorted(out["undeclared"].items()))
    return out


def review_events(root: Path) -> dict:
    """How many review EVENTS the corpus holds, and how they came back.

    Counted from the committed attest shards — one shard is one event — and
    NEVER from records: a record is a finding, and counting findings here would
    conflate the two.

    Clean rounds are sharded too, so "how many reviews came back clean" is
    answerable: a corpus holding only the reviews that FOUND something would
    give every review-level rate a self-selected denominator that excludes
    every clean round.

    This tally is reported ALONGSIDE precision and never inside it. Precision
    is per FINDING; a clean review contributes no finding and must not move a
    rule's n, its rate, or its Wilson bound.
    """
    tally = {"total": 0, "clean": 0, "findings_open": 0,
             "verdict_unrecorded": 0, "unreadable": 0,
             # The attestation is written AFTER adjudication,
             # so a candidate the examiner refuted only reaches the corpus
             # if the builder files it. A round with records but no refuted
             # record is either a round the examiner refuted nothing in or a
             # round that dropped its refutations — this tally cannot tell
             # which, and says so in the render rather than picking one.
             "with_records": 0, "with_refutation": 0}
    shard_dir = memory_dir(root) / SHARD_SUBDIR
    if not shard_dir.is_dir():
        return tally
    for path in sorted(shard_dir.glob("*.json")):
        try:
            # `read_shard`, the same read every reader of the store uses, so
            # a non-UTF-8 shard is counted unreadable rather than escaping as
            # a traceback out of certification's E-04, which calls this.
            doc = read_shard(path)
            sha = doc["sha"]
            source = doc.get("source", "attest")
        except (OSError, ValueError, KeyError, TypeError):
            # Reported, not swallowed: "I could not read the store" and "the
            # store holds fewer reviews" are different states, and a silent
            # skip asserts the second. AttestError is not raised here — stats
            # is a pure read that must still render — so the render calls the
            # tally a floor instead.
            tally["unreadable"] += 1
            continue
        # Acceptance is attest.attested_shas's, field for field and exception
        # for exception. Two readers of one store that disagree about what a
        # shard IS is its own defect class: a sha-less shard must not be
        # counted as a review here while `attest check` calls it unreadable.
        # PARITY with records_from_shards is inside `read_shard` above — the
        # SAME function, called rather than a second guard restating it,
        # because hand-written restatements drift. Unreadable is the honest
        # bucket and the fail-closed one: a shard nobody could read must not
        # render as a round that filed nothing, with `unreadable` left at 0 so
        # the "floor, not a count" warning never fires.
        #
        # It runs BEFORE the source/sha short-circuit below, and that order
        # matters: records_from_shards refuses every shard in this directory
        # on the record shape ALONE, so a shard that is malformed AND carries
        # a foreign `source` or a null `sha` must be counted unreadable here,
        # not skipped as "not an orchestrated review" while the sibling reader
        # refuses the whole corpus. Folding it into the read above keeps that
        # order by construction.
        filed = doc["records"]
        if source != "attest" or not isinstance(sha, str) or not sha:
            continue   # a checker firing is a fact, not an orchestrated review
        tally["total"] += 1
        if filed:
            tally["with_records"] += 1
            if any(r.get("status") == "refuted" for r in filed):
                tally["with_refutation"] += 1
        bucket = _VERDICT_BUCKETS.get(doc.get("verdict", ""))
        if bucket:
            tally[bucket] += 1
        else:
            # A shard carrying no verdict this vocabulary recognises —
            # typically one written before the envelope carried one. Counted
            # apart rather than folded into either bucket: evidence that cannot
            # answer the question must not be made to look like it did, and the
            # bucket does not claim to know WHY it cannot.
            tally["verdict_unrecorded"] += 1
    return tally


def wilson_lower_bound(successes: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    phat = successes / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def _declared_rules(rules_dir: Path | None) -> tuple[tuple, str]:
    """(rules, error). Stats is a pure read and must still render when the
    ruleset will not load — but it must SAY SO.

    Swallowing the error silently would be quieter, not safer: with no
    ruleset, nothing is marked paused, and a paused rule then appears as
    promotable — the one outcome stats claims to
    prevent. The error is returned so the caller can report it and withhold
    every judgment that depends on knowing the ruleset.
    """
    if rules_dir is None:
        return (), ""
    try:
        from .rules import load_rules
        return load_rules(rules_dir), ""
    except Exception as exc:
        return (), f"{type(exc).__name__}: {exc}"


def paused_rule_ids(root: Path, rules_dir: Path) -> set[str]:
    """Rule ids currently paused — stats marks them so a paused rule's
    numbers are never read as an active rule's performance."""
    return {r.id for r in _declared_rules(rules_dir)[0] if r.paused}


def covered_classes(rules_dir: Path | None, *,
                    include_paused: bool = False) -> dict[str, str]:
    """candidate slug -> the declared rule that already answers it.

    Two links, both explicit: a rule whose id IS the slug (`docs-drift`), and
    a rule that DECLARES the class it answers (`wiki-fidelity` covering
    `docs-drift`). Nothing else is inferred — guessing which rule subsumes a
    defect class is exactly the unevidenced claim this report exists to avoid.

    Both links are COVERAGE only while the rule is UNPAUSED: review.py skips
    a paused rule before it can produce a finding, so counting its links here
    would suppress a class from ever being re-proposed while nothing enforces
    it — the same fail-open the advisor avoids for `implements:`, one
    declared link over. The default answers that ENFORCEMENT question and
    excludes paused rules, so a new caller gets the fail-closed reading.
    `include_paused=True` answers the IDENTITY question instead — ingest
    migrates legacy ids onto the covering rule even while it is paused,
    because a record's identity does not lapse with enforcement and
    filtering there would re-open the permanent-corpus-loss path the
    migration exists to close (a legacy `docs-drift` record rejected on every
    ingest for as long as wiki-fidelity stays paused). One spelling is the
    exception, and it proves the split: `unmapped:<covered-slug>` under a
    PAUSED covering rule is a live candidate key — attest accepts it, ingest
    files it under the slug unmigrated, and the re-proposed candidate row
    accrues it. See _resolve_covered_legacy_ids.
    """
    covered: dict[str, str] = {}
    for rule in _declared_rules(rules_dir)[0]:
        if rule.paused and not include_paused:
            continue
        covered[rule.id] = rule.id
        for slug in rule.covers:
            covered[slug] = rule.id
    return covered


def _candidate_rows(per_candidate: dict[str, dict], covered: dict[str, str],
                    streaks: dict[str, int],
                    weights: dict[str, int] | None = None,
                    paused_covered: dict[str, str] | None = None) -> list[dict]:
    """Candidates ranked as PROPOSALS, not measured as rules.

    Recurrence is the ranking key: the statement a candidate makes is "N
    reviewers independently found this class, and no rule covers it". The
    slug names the CLASS to write a rule for — never the rule's own id, which
    must DIFFER from it: a rule id equal to its slug shadows the committed
    evidence that justified writing it (`_PROPOSE_RULE_RECIPE`, which this
    function's rows are rendered beside, carries the full coordinated recipe).

    `paused_covered` maps slug -> the PAUSED rule that declares it. A paused
    rule's links are not coverage, so the class earns a real
    verdict on its own numbers — but a class that re-proposes while a rule
    for it already exists must NAME that rule (`paused_covered_by`), or the
    report invites writing a duplicate a single unpause would answer.
    """
    rows = []
    for rule_id, row in per_candidate.items():
        slug = rule_id[len(UNMAPPED_PREFIX):]
        upheld = row["confirmed"] + row["fixed"]
        rate = upheld / row["n"] if row["n"] else 0.0
        streak = streaks.get(rule_id, 0)
        if slug in covered:
            verdict = "covered"
        elif row["n"] < CANDIDATE_MIN_N:
            # Recurrence is checked FIRST: one judged case that was refuted is
            # one data point, not a noisy class, and branding it noise on n=1
            # is the same small-sample error the promotion floor exists to
            # prevent — pointed the other way.
            verdict = "watch"
        elif (rate <= CANDIDATE_MIN_RATE or streak >= PAUSE_STREAK
              or (weights or {}).get(slug, 0) >= PAUSE_STREAK):
            # The streak is a SEPARATE bar from the rate:
            # a class with a strong history and three fresh refutations still
            # scores well above the rate bar, and proposing "write this rule"
            # about something reviewers have just rejected three times running
            # is exactly the proposal the streak exists to stop. A declared
            # rule in that state becomes a pause candidate; a candidate has no
            # rule to pause, so the streak stops the proposal instead.
            verdict = "noisy"
        else:
            verdict = "propose"
        rows.append({"slug": slug, "rule_id": rule_id, "n": row["n"],
                     "upheld": upheld, "refuted": row["refuted"],
                     "dismissed": row["dismissed"], "rate": round(rate, 3),
                     "wilson_lb": row["wilson_lb"], "verdict": verdict,
                     "streak": streak, "covered_by": covered.get(slug),
                     "paused_covered_by": (None if slug in covered else
                                           (paused_covered or {}).get(slug))})
    return sorted(rows, key=lambda r: (-r["n"], r["slug"]))


def promotion_candidates(doc: dict) -> list[dict]:
    """The candidate rows of a `stats` doc that clear the PROMOTION bar, not
    only the proposal bar: a `propose` verdict (so not covered, noisy or on a
    refutation streak) with PROMOTE_MIN_N+ judged AND a PROMOTE_WILSON_LB
    floor.

    One definition, read by certify's `promotion_bar_met` and by
    `warden memory watch`, so "a candidate crossed the bar" means the same
    thing in the badge and in the doorbell.
    """
    return [row for row in doc.get("candidates") or []
            if row["verdict"] == "propose"
            and row["n"] >= PROMOTE_MIN_N
            and row["wilson_lb"] >= PROMOTE_WILSON_LB]


# The craft layer this recurrence covers: the skill pack and the policy
# contract that governs how every change is judged. NOT warden/ or cage/ —
# those are code, and their recurrence becomes a rule, which the candidate
# path already handles. A protocol cannot be measured by precision the way a
# rule can, so its only signal is recurrence in the corpus.
_POLICY_CONTRACT = ".warden/skills-policy.md"


def _is_skill_file(file: str) -> bool:
    return file.startswith("skills/") or file == _POLICY_CONTRACT


def skill_recurrence(records: list[dict]) -> list[dict]:
    """Judged findings attributed to a SKILL FILE, ranked as proposals.

    The retro loop turns recurrence into a proposal for rules; this is its
    craft-layer half. Grouped by file, not dir_prefix: a skill IS its file.
    The bar is the
    rule-candidate bar in full — 3+ judged AND more upheld than not (rate
    strictly above CANDIDATE_MIN_RATE) AND no fresh refutation streak —
    because writing a skill step is a cheap reversible act; the bar is
    explicit rather than felt precisely because a protocol edit cannot be
    measured by precision afterwards. The streak clause is the same separate
    bar _candidate_rows carries: proposing 'change this skill' about a
    protocol reviewers have just cleared three times running is exactly the
    proposal the streak exists to stop, whatever the lifetime rate.
    """
    per_file: dict[str, dict] = {}
    for r in records:
        file = r.get("file", "")
        if not _is_skill_file(file) or r["status"] not in _JUDGED_STATUSES:
            continue
        row = per_file.setdefault(file, {"n": 0, "upheld": 0, "refuted": 0,
                                         "dismissed": 0})
        row["n"] += 1
        if r["status"] in ("confirmed", "fixed"):
            row["upheld"] += 1
        elif r["status"] == "refuted":
            row["refuted"] += 1
        else:
            row["dismissed"] += 1
    # Per-file refutation streak, computed by the same helper stats() uses for
    # rules — one arithmetic, so "a streak" cannot mean two things. Counted
    # over review ROUNDS: a round of findings on one skill file is one round's
    # opinion of it, however many lines the reviewer filed.
    _skill_subject = (lambda r: (r.get("file", "")
                                 if _is_skill_file(r.get("file", "")) else None))
    streaks = _streaks_by_round(records, subject=_skill_subject)
    weights = _refuting_weight(records, subject=_skill_subject)
    rows = []
    for file, row in per_file.items():
        rate = row["upheld"] / row["n"] if row["n"] else 0.0
        streak = streaks.get(file, 0)
        if row["n"] < CANDIDATE_MIN_N:
            verdict = "watch"          # too few to call — the small-sample rule
        elif (rate <= CANDIDATE_MIN_RATE or streak >= PAUSE_STREAK
              or weights.get(file, 0) >= PAUSE_STREAK):
            verdict = "noisy"          # reviewers keep clearing it; not a change
        else:
            verdict = "propose"
        rows.append({"file": file, "n": row["n"], "upheld": row["upheld"],
                     "refuted": row["refuted"], "dismissed": row["dismissed"],
                     "rate": round(rate, 3), "streak": streak,
                     "verdict": verdict})
    return sorted(rows, key=lambda r: (-r["n"], r["file"]))


def _round_key(record: dict) -> tuple[str, str]:
    """The review ROUND a record belongs to.

    One attestation writes one shard, and `build_records` stamps every record
    in it with that event's `reviewed_at` and head sha — so (ts, sha) IS the
    round, derivable from any record without a schema change or a re-ingest of
    the committed corpus. `seq` is deliberately absent: it is the order the
    reviewer happened to write its findings in, and it is exactly what the
    streak must not see.

    Two DISTINCT events can tie on (ts, sha) — `_shard_name` hashes content to
    keep their filenames apart — and would be read here as one round. That
    merge can only ever SHORTEN a streak, which is the safe direction for a
    mechanism whose action is to mutate gate surface.

    THE PREMISE IS NOW TRUE, and what that cost was MEASURED rather than
    argued. While one attestation covered N rounds, those N read as ONE and
    every round-based streak was under-counted — accidental shortening in the
    safe direction. One attestation now covers one round, which removes that
    accident and makes this arithmetic both more accurate and more sensitive.

    THE READING EVERY FIGURE BELOW IS UNDER, named first because getting it
    wrong is how the first draft of this paragraph shipped a false number.
    Nothing reaches this function raw: `load_records` and `records_from_shards`
    both return `fold_restatements(canonicalize_records(...))`, and `stats`
    re-folds before calling `refutation_streaks`, so there is no production
    caller that walks `_streaks_by_round` over raw shard records. A figure
    measured on the pre-fold shards therefore describes no live behaviour —
    the tail can hold two refuting shards at ONE sha that the fold collapses
    into one round, which is exactly the difference between the two readings.
    ANCHORED, because every count here moves with each merge: measured over
    the 375 shards committed at 7811e387f81f, which the corpus folds to 2277
    records, 308 (ts, sha) keys and 291 subjects.

    Re-keying on the round id the shard already carries moves NOTHING: 0
    streaks change and 0 weights change. Only 5 of those keys ever held more
    than one round, all written on 2026-09-02/03 under the retired protocol.
    NOTHING in the corpus reaches PAUSE_STREAK — the longest refutation streak
    is 2 (`unmapped:contradictory-guidance`, which `memory stats` prints as
    proposable, not paused), and `_refuting_weight` maxes at 1. So the new
    sensitivity is ACCEPTED here rather than re-keyed: re-keying is a measured
    no-op on every record the corpus holds, and it would buy that no-op by
    making this function depend on a field `build_records` did not always
    stamp.

    THE OPPOSITE-SIGN EFFECT, measured with it because the two had to be read
    together — and it did NOT come back clean, so it is recorded as open
    rather than accepted. `dismissed-with-reason` is how a PARKED finding is
    filed — a defect the review UPHELD and handed to a tracked item — and it
    lands in the dismissed bucket, counting in a rule's `n` while sitting
    outside the numerator of the precision floor a promotion is gated on. It
    is not rare: 28 of the 54 rule ids with 3+ judged cases carry at least
    one. And it MOVES DECISIONS. Take the parked findings out of the
    denominator and four rules cross the full promotion gate
    (`PROMOTE_MIN_N` judged AND `PROMOTE_WILSON_LB` floor, and each clears the
    N on the reduced denominator too): `rename-complete` 0.473 -> 0.832,
    `error-names-cause` 0.652 -> 0.843, `lang-conventions` 0.665 -> 0.787,
    `attribution-holds` 0.623 -> 0.851. Each would be promotable under the
    parked-out reading and is not under the one that ships. Which denominator
    is correct is a policy question this function cannot answer and does not
    try to: the arithmetic is UNCHANGED here, and the open question is
    tracked. What is settled is only that it is not a no-op — the first draft
    of this paragraph claimed it moved nothing, which a review round measured
    and refuted.

    The restatement fold is the OTHER thing that moves a
    round's contents, and it is held to the same direction:
    `_streaks_by_round` and `_refuting_weight` both read
    `_with_restatements`, in OPPOSITE modes, because their safe directions
    are opposite. The streak reader takes `refutations=False` — a
    folded-away upheld judgment is handed back, a folded-away refutation
    stays folded — so the fold can only SHORTEN a streak; unguarded it could
    lengthen one, and a round that upheld a finding later restated as
    refuted would read as all-refuted, which is the direction that REMOVES
    enforcement. The weight reader takes `refutations=True`, the whole
    pre-fold history, because it is a SUPPRESSOR and a weight that falls
    un-blocks a proposal, so it must never read the streak's mode.
    """
    return (record.get("ts", ""), record.get("sha", ""))


def _streaks_by_round(records: list[dict], *,
                      subject: Callable[[dict], str | None],
                      ) -> dict[str, int]:
    """Consecutive REFUTING REVIEW ROUNDS at the tail of each subject's judged
    history — the one streak arithmetic, keyed by whatever `subject` names
    (a rule id for rules, a file for skills).

    A round contributes ONE step, and only if every judged record it holds for
    that subject is a refutation; any upheld or dismissed judgment in the round
    resets the count to 0. A round holding nothing judged for a subject leaves
    that subject untouched — other rules' findings never move this one.

    Rounds are walked in (ts, sha) order; within a round every subject is
    resolved from the round's whole verdict, so no permutation of the records
    inside a round can change the answer.

    Read over `_with_restatements(refutations=False)`: a round's verdict is
    what THAT round filed, and the precision fold must not edit it — except
    that a refutation carried forward stays folded, which is the one
    direction that can only SHORTEN a streak.
    """
    per_round: dict[tuple[str, str], dict[str, list[str]]] = {}
    for r in _with_restatements(records, refutations=False):
        if r["status"] not in _JUDGED_STATUSES:
            continue
        key = subject(r)
        if key is None:
            continue
        per_round.setdefault(_round_key(r), {}).setdefault(key, []).append(
            r["status"])
    streaks: dict[str, int] = {}
    for _round in sorted(per_round):
        for key, statuses in per_round[_round].items():
            if all(s == "refuted" for s in statuses):
                streaks[key] = streaks.get(key, 0) + 1
            else:
                streaks[key] = 0
    return streaks


def _refuting_weight(records: list[dict], *,
                     subject: Callable[[dict], str | None],
                     ) -> dict[str, int]:
    """The suppressor's bar: the most refutations any SINGLE round filed for a
    subject, where that round upheld nothing for it.

    The round streak is the right TRIGGER and the wrong SUPPRESSOR:
    `_candidate_rows` and `skill_recurrence` read `streak >= PAUSE_STREAK` to
    STOP proposing a class reviewers keep refuting, so a shorter streak
    un-blocks a proposal. Seven upheld rounds then one round refuting a class
    three times would read as `propose` on the streak alone — the retro
    offering to write a rule about a class a round had just refuted three
    times over.

    Counting refutations WITHIN a round is order-independent, so the
    suppressor keeps that protection without regaining write-order
    sensitivity, and the pause TRIGGER stays strictly round-based.

    Read over `_with_restatements(refutations=True)` — the WHOLE pre-fold
    history, unlike `_streaks_by_round`. This is a suppressor, so a weight
    that falls un-blocks a proposal, and the fold must not be able to lower
    it.
    """
    weight: dict[str, int] = {}
    per_round: dict[tuple[str, str], dict[str, list[str]]] = {}
    for r in _with_restatements(records, refutations=True):
        if r["status"] not in _JUDGED_STATUSES:
            continue
        key = subject(r)
        if key is None:
            continue
        per_round.setdefault(_round_key(r), {}).setdefault(key, []).append(
            r["status"])
    for statuses_by_key in per_round.values():
        for key, statuses in statuses_by_key.items():
            if all(s == "refuted" for s in statuses):
                weight[key] = max(weight.get(key, 0), len(statuses))
    return weight


def refutation_streaks(records: list[dict]) -> dict[str, int]:
    """Per-rule count of consecutive REFUTING REVIEW ROUNDS at the tail of its
    judged history. PAUSE_STREAK such rounds is the pause signal.

    Shared so the auto-pause mechanism in warden.autonomy consumes the SAME
    arithmetic that `stats` and the candidate rows already do, instead of a
    private copy that could drift from the gate's notion of a streak.

    A streak is a TREND ACROSS ROUNDS, never a property of one artifact's write
    order. Every record a round writes shares that round's ts and sha, so
    ordered by (ts, sha, seq) the key degenerates to `seq` inside a round: a
    round that filed 15 upheld findings and then 5 refutations would read as
    "5 consecutive refutations", and permuting seq within that single round
    would move the number between 0 and 5.
    """
    return _streaks_by_round(records, subject=lambda r: r["rule_id"])


def stats(root: Path, rules_dir: Path | None = None,
          records: list[dict] | None = None) -> dict:
    """Per-rule and per-tag precision counts, plus candidate-rule proposals.

    `records` defaults to the local cache; pass `records_from_shards(root)`
    to compute over the committed corpus instead — certify does, because the
    cache is derived, gitignored, and absent in CI.

    PRECISION ONLY — recall is unobservable from this corpus; escapes are the
    retro's job.

    Declared rules and `unmapped:` candidates are counted the same way and
    reported SEPARATELY, because they are different statements: a declared
    rule's row measures a rule that exists, a candidate's row proposes one
    that does not.
    """
    if records is None:
        records = load_records(root)
    # Idempotent over records a loader already folded; decisive over records
    # handed in raw. Either way the denominators below count FINDINGS, and a
    # finding restated by a re-attestation is one finding.
    records = fold_restatements(records)
    restated = sum(r.get("restated", 0) for r in records)
    # A fold that REVERSES a disposition is not the same event as one that
    # restates it, and a bare count cannot tell them apart: "the later
    # shard's disposition wins" can ERASE an earlier round's upheld judgment,
    # moving the sign of a contribution rather than only its multiplicity.
    # Counted and rendered separately so the reader of a promotion or a
    # pause sees it whenever it is not zero.
    #
    # Compared by `_disposition`, not raw `status`: `confirmed` -> `fixed` is
    # the CANONICAL repair-round restatement, the very shape the fold exists
    # for, and it moves no sign at all (`wilson_lower_bound(confirmed + fixed,
    # n)` counts both). What moves a sign is crossing between the three
    # buckets a reader acts on — upheld, refuted, dismissed.
    restated_changed = sum(
        1 for r in records for prior in r.get("restated_from", ())
        if _disposition(prior) != _disposition(r))
    per_rule: dict[str, dict] = {}
    per_candidate: dict[str, dict] = {}
    per_tag: dict[str, dict] = {}

    def bump(table: dict, key: str, status: str) -> None:
        row = table.setdefault(key, {"confirmed": 0, "fixed": 0, "refuted": 0,
                                     "dismissed": 0, "n": 0})
        bucket = "dismissed" if status == "dismissed-with-reason" else status
        row[bucket] += 1
        row["n"] += 1

    # Outcome per LENS and per ROUND: the reader the
    # attribution fields exist for. Keyed on the finding's own `lens` — who
    # RAISED it — never on the roster, which says who was dispatched. A judged
    # record with no lens is COUNTED, apart, and never folded into a lens:
    # older records carry none, and a tally that made them look attributed
    # would misstate who raised them.
    per_lens: dict[str, dict] = {}
    per_round: dict[int, dict] = {}
    lens_unrecorded = 0
    # A record WITH a lens and no usable round is counted here rather than
    # vanishing from per_round with no trace. The
    # determinant is the FINDING: build_records copies `round` off the finding
    # and never off the roster, so this counts findings that stated no round —
    # or stated one warden cannot read — whatever the roster said. A
    # bool is not a round, whatever `isinstance(True, int)` says.
    round_unrecorded = 0

    detections: dict[str, int] = {}
    for r in records:
        if r["status"] not in _JUDGED_STATUSES:
            # gate detections are counted separately — hotspots for the
            # retro, never precision for the promotion gate
            detections[r["rule_id"]] = detections.get(r["rule_id"], 0) + 1
            continue
        bump(per_candidate if r["rule_id"].startswith(UNMAPPED_PREFIX)
             else per_rule, r["rule_id"], r["status"])
        # Tags are a cross-cutting index over BOTH — a defect class can carry
        # the same tag whether or not a rule already names it.
        for tag in r.get("tags") or []:   # present-and-null is legal — see above
            bump(per_tag, tag, r["status"])
        rnd = r.get("round")
        usable_round = (isinstance(rnd, int) and not isinstance(rnd, bool)
                        and rnd >= 1)
        if r.get("lens"):
            bump(per_lens, r["lens"], r["status"])
            if not usable_round:
                round_unrecorded += 1
        else:
            lens_unrecorded += 1
        if usable_round:
            bump(per_round, rnd, r["status"])

    for table in (per_rule, per_candidate, per_tag):
        for row in table.values():
            row["wilson_lb"] = round(
                wilson_lower_bound(row["confirmed"] + row["fixed"], row["n"]), 3)

    # Promotion gate: N>=10 AND Wilson LB >= 0.7. Pause: 3 straight
    # refutations (cheap, reversible — Tricorder's >10%-not-useful precedent).
    promotable = sorted(k for k, v in per_rule.items()
                        if v["n"] >= PROMOTE_MIN_N
                        and v["wilson_lb"] >= PROMOTE_WILSON_LB)
    # Promotion means "automate this DECLARED rule": an id that names no
    # rule — a grandfathered legacy id above all — has nothing to automate,
    # and must never satisfy the promotion gate however dense its history.
    # Only filter when a ruleset
    # was given: stats without rules_dir stays a pure per-id tally.
    # Grouped by (ts, sha) into ROUNDS. `seq` is deliberately not part of it:
    # it is a cache/recall tiebreak, and a streak must not depend on it.
    streaks = refutation_streaks(records)
    # Candidates are excluded from the PAUSE list: you cannot pause a rule
    # that does not exist. The streak itself is not discarded — it is carried
    # into the candidate row, where it blocks the proposal.
    paused = sorted(k for k, v in streaks.items()
                    if v >= PAUSE_STREAK and not k.startswith(UNMAPPED_PREFIX))
    declared, rules_error = _declared_rules(rules_dir)
    already_paused = {r.id for r in declared if r.paused}
    if rules_dir is not None and not rules_error:
        declared_ids = {r.id for r in declared}
        promotable = [r for r in promotable if r in declared_ids]
    # A rule already paused is not a pause CANDIDATE, and must never be
    # proposed for promotion off numbers it stopped earning.
    promotable = [r for r in promotable if r not in already_paused]
    paused = [r for r in paused if r not in already_paused]
    if rules_error:
        # Fail CLOSED on everything that needs the ruleset to be true: with no
        # readable ruleset we cannot know what is paused, and an unfiltered
        # promotable list would propose automating a rule that is switched
        # off. Reported loudly rather than quietly emptied.
        promotable, paused = [], []
    # A paused rule's declared links, kept apart from coverage: they no
    # longer suppress a candidate, but the re-proposed row must
    # name the paused rule rather than invite a duplicate.
    paused_covered = {slug: r.id for r in declared if r.paused
                      for slug in (r.id, *r.covers)}
    candidates = _candidate_rows(
        per_candidate, covered_classes(rules_dir), streaks,
        _refuting_weight(records, subject=lambda r: (
            r["rule_id"][len(UNMAPPED_PREFIX):]
            if r.get("rule_id", "").startswith(UNMAPPED_PREFIX) else None)),
        paused_covered=paused_covered)
    skill_rows = skill_recurrence(records)
    # The subtractive half of the craft layer: steps that
    # cite a corpus class, read against recurrence. Never a removal — a quiet
    # class may be a guard working, which the retro judges, not this tally.
    from . import provenance as _prov
    skills_root = root / "skills" / "nightgate-skills" / "skills"
    _vocab, _aliases = _prov.load_vocab_and_aliases(root)
    quiet_step_rows = _prov.provenance_report(
        skills_root, records, vocab=_vocab, aliases=_aliases)
    from .tags import audit as tag_audit
    age = corpus_age(records)
    return {"corpus_age": age, "tags": tag_audit(root, records),
            "review_events": review_events(root),
            "dispatches": dispatch_outcomes(root),
            "seats": seat_outcomes(root, records),
            "per_rule": per_rule, "per_tag": per_tag,
            "per_lens": per_lens, "per_round": per_round,
            "lens_unrecorded": lens_unrecorded,
            "round_unrecorded": round_unrecorded,
            "promotable": promotable, "pause_candidates": paused,
            "paused": sorted(already_paused), "rules_error": rules_error,
            "candidates": candidates,
            "rule_proposals": [c["slug"] for c in candidates
                               if c["verdict"] == "propose"],
            "skill_recurrence": skill_rows,
            "skill_proposals": [r["file"] for r in skill_rows
                                if r["verdict"] == "propose"],
            "quiet_steps": quiet_step_rows,
            "quiet_step_reviews": [
                f"{r['skill']}: {r['step']}" for r in quiet_step_rows
                if r["verdict"] == "review"],
            "gate_detections": dict(sorted(detections.items())),
            "restatements_folded": restated,
            "restatements_changed": restated_changed,
            "total_records": len(records)}


# Naming the rule after the slug is the ONE move that breaks the corpus: a
# rule whose id IS the slug — or which `covers:` it — makes every shard that
# ever filed under that slug stop resolving (certification drops from LEVEL 5
# to LEVEL 3), and committed evidence is not re-filed to satisfy a check. So
# the recipe travels with the proposal, at the point the mistake is made,
# rather than living only in the wiki.
_PROPOSE_RULE_RECIPE = [
    "  WRITING one of these is a COORDINATED change — the slug is already in "
    "committed evidence:",
    "    1. give the rule an id that DIFFERS from the slug, and declare "
    "`covers: [<slug>]` in its frontmatter",
    "       (a rule id equal to the slug shadows the very history that "
    "justified it);",
    f"    2. grandfather the committed ids — add `{UNMAPPED_PREFIX}<slug>` "
    "under `grandfathered_rule_ids:` in",
    "       `.warden/certification.yaml`, or certification E-02 drops the "
    "moment the rule lands;",
    "    3. carry the backtest artifact at the NEW rules_version (S-05).",
    "    Steps 1 and 2 are both required: `covers:` is itself one of the two "
    "conditions that",
    "    invalidates the history. `nightgate-skills:rule-advisor` drafts all "
    "three together.",
]


def _render_candidates(candidates: list[dict]) -> list[str]:
    """The candidate section: proposals, not measurements.

    Every line names an action a human could take. The Wilson bound is shown
    on every candidate the report calls out individually — real information
    about the class, labelled `floor` so it is not misread as the promotion
    gate, which does not apply here. The `watch` roll-up deliberately shows
    upheld/judged and NO floor: under CANDIDATE_MIN_N cases a confidence bound
    is a number with nothing behind it, and printing one invites exactly the
    over-reading the bound exists to prevent.
    """
    lines = [
        "CANDIDATE RULES — recurring defect classes NO rule covers "
        f"({UNMAPPED_PREFIX}). The slug names the CLASS, not the rule.",
        "  A candidate's promotion is WRITE this rule, not automate it — there "
        "is no rule to automate.",
        "  Proposing needs %d+ judged cases AND more upheld than not; that is "
        "deliberately not the" % CANDIDATE_MIN_N,
        "  promotion bar above, which gates automating a rule that already "
        "exists.",
    ]
    if any(c["verdict"] == "propose" for c in candidates):
        lines += _PROPOSE_RULE_RECIPE
    watching = []
    for c in candidates:
        tally = (f"{c['n']} judged, {c['upheld']} upheld, {c['refuted']} refuted, "
                 f"{c['dismissed']} dismissed ({c['rate'] * 100:.0f}%, "
                 f"floor {c['wilson_lb']})")
        # A paused rule's declared link is not coverage, but a
        # row it points at must carry the rule's name: proposing the class
        # with no note invites a duplicate rule one unpause would answer.
        paused_by = c.get("paused_covered_by")
        pause_note = (f" — rule '{paused_by}' already declares this class but "
                      "is PAUSED and enforces nothing; unpause or replace it "
                      "rather than writing a duplicate") if paused_by else ""
        if c["verdict"] == "propose":
            lines.append(f"  PROPOSE RULE {c['slug']}: {tally}{pause_note}")
        elif c["verdict"] == "covered":
            lines.append(f"  covered {c['slug']}: {tally} — already declared as "
                         f"rule '{c['covered_by']}'")
        elif c["verdict"] == "noisy":
            why = (f" — refuted in {c['streak']} rounds running"
                   if c["streak"] >= PAUSE_STREAK else "")
            lines.append(f"  too noisy to propose {c['slug']}: {tally}{why}"
                         f"{pause_note}")
        else:
            paused_flag = (f", rule '{paused_by}' PAUSED" if paused_by else "")
            watching.append(f"{c['slug']} ({c['upheld']}/{c['n']}{paused_flag})")
    if watching:
        lines.append(f"  watching (upheld/judged, under {CANDIDATE_MIN_N} "
                     "judged cases — too few to call either way): "
                     + ", ".join(watching))
    return lines


def _render_skill_recurrence(rows: list[dict]) -> list[str]:
    """The craft-layer section: recurrence by skill file,
    read as PROPOSALS for a skill change, never as measurements. Same shape
    and vocabulary as the candidate section, so the retro reads them the
    same way — proposes only, at the candidate bar, and refuses a single
    finding out loud."""
    if not rows:
        return []
    lines = [
        "SKILL RECURRENCE — findings attributed to a skill file or the policy "
        "contract, read as proposals for a SKILL CHANGE.",
        "  A protocol governs how every change is judged and cannot be "
        "measured by precision the way a rule can, so recurrence is the only "
        "signal — and the bar is the candidate bar, never a single finding.",
    ]
    watching = []
    for r in rows:
        tally = (f"{r['n']} judged, {r['upheld']} upheld, {r['refuted']} "
                 f"refuted, {r['dismissed']} dismissed ({r['rate'] * 100:.0f}%)")
        if r["verdict"] == "propose":
            lines.append(f"  PROPOSE SKILL CHANGE {r['file']}: {tally}")
        elif r["verdict"] == "noisy":
            why = (f"refuted in {r['streak']} rounds running"
                   if r.get("streak", 0) >= PAUSE_STREAK
                   else "more cleared than upheld")
            lines.append(f"  too noisy to propose {r['file']}: {tally} — {why}")
        else:
            watching.append(f"{r['file']} ({r['upheld']}/{r['n']})")
    if watching:
        lines.append(f"  watching (upheld/judged, under {CANDIDATE_MIN_N} "
                     "judged cases — too few to call): " + ", ".join(watching))
    return lines


def _render_review_events(events: dict) -> list[str]:
    """The review-event tally: rounds, not findings.

    Rendered above the precision sections and explicitly fenced off from them.
    A reader who sees "3 clean" next to a rule's n must be told, in the render
    itself, that the two are different denominators — the whole defect this
    section came out of was two different things counted as one.
    """
    if not events.get("total") and not events.get("unreadable"):
        return []
    line = (f"REVIEW EVENTS — {events['total']} review(s) on record: "
            f"{events['clean']} clean, {events['findings_open']} with findings")
    if events.get("verdict_unrecorded"):
        # No cause is named. The bucket establishes that the shard carries no
        # verdict, not WHY — a malformed shard written minutes ago lands here
        # too, so it must not be called legacy evidence.
        line += f", {events['verdict_unrecorded']} with no verdict recorded"
    lines = [line + ".",
             "  A clean review is an EVENT, not a finding: counted here and "
             "nowhere else — the precision rows below are over findings only."]
    if events.get("unreadable"):
        lines.append(f"  WARNING: {events['unreadable']} attestation shard(s) "
                     "could not be read — this tally is a floor, not a count.")
    if events.get("with_records"):
        # Printed on every render that has a round to describe, whatever the
        # ratio: the precision rows below are computed over what was FILED,
        # and a reader deciding a promotion needs the bias named beside the
        # numbers it biases. No cause is asserted for any one round — the
        # tally cannot see the examiner's verdicts, only what reached the
        # shard.
        # "rounds that FILED a record", never "rounds with findings": the
        # header two lines up already spends that phrase on the VERDICT
        # bucket (`findings-open`), and clean-verdict shards can carry
        # records — so the same words would print two different numbers in
        # one block. No count is written here: it moves every merge.
        lines.append(
            f"  {events['with_refutation']} of {events['with_records']} rounds "
            "that FILED a record filed a refutation (not the verdict bucket "
            "above — a round can file records and still verdict clean). "
            "Precision below is over FILED "
            "judgments: a candidate the cross-examiner refuted but nobody "
            "filed is invisible here, and every rate reads HIGH by the share "
            "of raised findings that were refuted and dropped.")
    return lines


def _render_lenses(doc: dict) -> list[str]:
    """Outcome per lens and per round, with the unattributed
    count said out loud. These are OUTCOME rows, not precision: no Wilson
    bound, no promotion, no pause — a lens is not a rule, and the question
    they answer (should review depth scale to risk?) is the
    retro's to weigh, not this tally's to decide."""
    per_lens = doc.get("per_lens") or {}
    per_round = doc.get("per_round") or {}
    missing = doc.get("lens_unrecorded", 0)
    no_round = doc.get("round_unrecorded", 0)
    if not per_lens and not per_round and not missing and not no_round:
        return []
    lines = ["LENSES — outcome per reviewer lens and per round; the lens is "
             "who RAISED the finding, and these rows are outcomes, not "
             "precision."]
    for key in sorted(per_lens):
        row = per_lens[key]
        lines.append(
            f"  lens {key}: n={row['n']} confirmed={row['confirmed']} "
            f"fixed={row['fixed']} refuted={row['refuted']} "
            f"dismissed={row['dismissed']}")
    for key in sorted(per_round):
        row = per_round[key]
        lines.append(
            f"  round {key}: n={row['n']} confirmed={row['confirmed']} "
            f"fixed={row['fixed']} refuted={row['refuted']} "
            f"dismissed={row['dismissed']}")
    if missing:
        lines.append(
            f"  {missing} judged record(s) carry no lens — written before "
            "attribution shipped, or attested without "
            "--review-dir; counted here and folded into no lens.")
    if no_round:
        lines.append(
            f"  {no_round} lens-attributed record(s) carry no round — the "
            "FINDING stated none, or stated one warden cannot read; the "
            "roster's round is never copied onto a record. In a lens row "
            "above, in no round row.")
    return lines


def _render_dispatches(doc: dict) -> list[str]:
    """Per-lens dispatch outcomes: the denominator the
    review-depth ledger leads with, with zero-yield split into the two things
    it was hiding. Outcome rows, not precision, for the reason `_render_lenses`
    gives."""
    table = doc.get("dispatches") or {}
    if not table:
        return []
    lines = ["DISPATCHES — per lens, what each roster entry came back with; "
             "`no-review` is stamped three ways: a dispatch that did not "
             "return; a returned output with no findings that names no file "
             "the diff changed — usually a refusal or a truncated run, but "
             "also any clean review from a pack that never told its reviewer "
             "to name what it read; and a no-review the builder declared, "
             "which stands."]
    for key in sorted(table):
        row = table[key]
        lines.append(
            f"  dispatch {key}: dispatches={row['dispatches']} "
            f"returned={row['returned']} reviewed-clean={row['reviewed-clean']} "
            f"reviewed-findings={row['reviewed-findings']} "
            f"no-review={row['no-review']} unknown={row['unknown']}")
    if any(row["unknown"] for row in table.values()):
        lines.append(
            "  `unknown` counts roster entries with no outcome, or with one "
            "nothing cross-checked — written before the field shipped, or "
            "attested without --review-dir; never upgraded to reviewed-clean.")
    return lines


def _render_seats(doc: dict) -> list[str]:
    """Per reviewer seat: precision and unique share, the scoreboard for
    crew composition. Read-only rows — `seat_outcomes` says what each
    number counts, and the header says it on every render."""
    seats = doc.get("seats")
    if seats is None:
        return []
    lines = ["REVIEWER SEATS — per seat graph.yaml's review block declares: "
             "the findings it RAISED (the lens on a finding's earliest copy, "
             "never a later re-filer's) and how they were finally judged. "
             "wilson_lb is the rule rows' floor over confirmed+fixed; unique "
             "is how many of its findings no other declared seat raised on "
             "the same rule, file and line in the same round — a seat alone "
             "in its round is unique by construction. Read-only: nothing "
             "promotes or pauses a seat."]
    if seats.get("error"):
        lines.append("  SEATS WITHHELD — graph.yaml could not be resolved, so "
                     "no seat is scored: " + seats["error"])
        return lines
    if not seats.get("declared"):
        lines.append("  this repo declares no review crew (no graph.yaml, or "
                     "one with no `review` block), so there is no seat to "
                     "score.")
        return lines
    for row in seats.get("rows") or []:
        share = ("-" if row["unique_share"] is None
                 else f"{row['unique_share'] * 100:.0f}%")
        floor = "-" if row["wilson_lb"] is None else row["wilson_lb"]
        lines.append(
            f"  seat {row['seat']}: n={row['n']} confirmed={row['confirmed']} "
            f"fixed={row['fixed']} refuted={row['refuted']} "
            f"dismissed={row['dismissed']} wilson_lb={floor} "
            f"unique={row['unique']} ({share})")
    undeclared = seats.get("undeclared") or {}
    if undeclared:
        lines.append(
            f"  {sum(undeclared.values())} judged record(s) raised by a lens "
            "no seat declares (" + ", ".join(
                f"{k}={v}" for k, v in undeclared.items())
            + ") — counted here, in no seat row.")
    if seats.get("unattributed"):
        lines.append(
            f"  {seats['unattributed']} judged finding(s) whose first copy "
            "names no lens — raised before attribution shipped, or attested "
            "without --review-dir; credited to no seat, never to a later "
            "re-filer.")
    return lines


def render_stats(doc: dict) -> str:
    age = doc.get("corpus_age") or {}
    # "finding(s)", not "record(s)": this number counts FINDINGS, and
    # `memory ingest` prints the cache's record count beside the fold that
    # reconciles them. One word for two quantities on one store would
    # confuse them.
    lines = [f"memory stats — {doc['total_records']} finding(s)"
             + (f" ({age.get('fresh', 0)} fresh, {age.get('aging', 0)} aging, "
                f"{age.get('stale', 0)} stale — oldest {age.get('oldest_days', 0)}d)"
                if age.get("count") else "")
             # Silent when zero: a corpus with nothing folded should not
             # read as though something was.
             + (f"; {doc['restatements_folded']} restatement(s) folded — a "
                "finding re-attested under a moved head counts once, the "
                "later shard's disposition wins"
                if doc.get("restatements_folded") else "")
             # Named apart from the count, because it is a different event:
             # a later round did not merely repeat an earlier judgment, it
             # REVERSED one, and the fold keeps only the reversal.
             + (f", {doc['restatements_changed']} of them REVERSING an "
                "earlier round's judgment (upheld/refuted/dismissed), not "
                "restating it"
                if doc.get("restatements_changed") else ""),
             # Standing caveat: prints on EVERY render, above every section.
             "CAVEAT: precision only; recall is unobservable — escapes are "
             "mined separately by the retro."]
    lines += _render_review_events(doc.get("review_events") or {})
    lines.append("DECLARED RULES — precision of the rules that exist.")
    if doc.get("rules_error"):
        # Never quietly: with no readable ruleset, promotion and pause
        # candidacy are withheld rather than computed off a ruleset whose
        # paused rules we cannot see.
        lines.append("  RULESET UNREADABLE — promotion/pause candidacy and "
                     "candidate coverage withheld: " + doc["rules_error"])
    for key in sorted(doc["per_rule"]):
        row = doc["per_rule"][key]
        lines.append(
            f"  rule {key}: n={row['n']} confirmed={row['confirmed']} "
            f"fixed={row['fixed']} refuted={row['refuted']} "
            f"dismissed={row['dismissed']} wilson_lb={row['wilson_lb']}")
    if doc.get("gate_detections"):
        hot = ", ".join(f"{k}={v}" for k, v in doc["gate_detections"].items())
        lines.append(f"  gate detections (deterministic hits, NOT precision): {hot}")
    lines.append(
        "  promotable (needs %d+ judged cases AND a %.0f%% confidence floor): %s" % (
            PROMOTE_MIN_N, PROMOTE_WILSON_LB * 100,
            ", ".join(doc["promotable"]) or "none — guardrail holding"))
    # The bar above says WHEN; the micro-test protocol says what EVIDENCE the
    # proposal carries. Printed beside the bar so the reader
    # who acts on `promotable` learns both halves in one place.
    lines.append(
        "  a promotion proposal carries micro-test validation — no-guidance "
        "control arm, %d+ reps per variant, variance reported, every flagged "
        "match manually read — or is explicitly marked unvalidated with the "
        "reason (S-05 refuses a promote backtest with neither)."
        % MICROTEST_MIN_REPS)
    if doc.get("paused"):
        lines.append("  PAUSED (not enforced): " + ", ".join(doc["paused"]))
    if doc["pause_candidates"]:
        lines.append("  PAUSE candidates (%d refuting rounds running): %s" % (
            PAUSE_STREAK, ", ".join(doc["pause_candidates"])))
    if doc.get("candidates"):
        lines += _render_candidates(doc["candidates"])
    lines += _render_skill_recurrence(doc.get("skill_recurrence") or [])
    from . import provenance as _prov
    lines += _prov.render_quiet_steps(doc.get("quiet_steps") or [])
    lines += _render_lenses(doc)
    lines += _render_dispatches(doc)
    lines += _render_seats(doc)
    lines.append("TAGS — one index across both; a class keeps its tag whether "
                 "or not a rule names it.")
    for key in sorted(doc["per_tag"]):
        row = doc["per_tag"][key]
        lines.append(
            f"  tag {key}: n={row['n']} confirmed={row['confirmed']} "
            f"fixed={row['fixed']} refuted={row['refuted']} "
            f"dismissed={row['dismissed']} wilson_lb={row['wilson_lb']}")
    if doc.get("tags"):
        from .tags import render_audit
        lines.append("  " + render_audit(doc["tags"]).strip().replace("\n", "\n  "))
    return "\n".join(lines) + "\n"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
