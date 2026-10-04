"""`warden decide` — the decision record, bound to commit and rules_version.

superpowers keeps an ephemeral `Ruling: -- -- ` ledger line that dies with the
session. This makes it durable: a decision — what was decided, why, what it
costs if wrong, the alternatives weighed, the scope it governs — is stamped
with the same provenance an attestation carries (head_sha, base_sha,
rules_version, timestamp) so the record cannot mislabel what was decided
against what, and is committed as a shard so decisions COMPOUND instead of
evaporating.

A decision is not a finding: it carries no rule_id and never enters the
review-precision cache. It rides the same `.warden/out` -> committed-shard
path an attestation does, so `warden memory ingest` picks it up, but into its
own `.warden/memory/decide/` store, kept apart from the findings corpus.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "decision.schema.json"
_validator = Draft202012Validator(json.loads(_SCHEMA_PATH.read_text()))

# The author-supplied half; provenance is stamped by build(), never accepted
# from the caller — the same split attest.build enforces so a skill cannot
# forge what was decided against which commit.
PAYLOAD_KEYS = frozenset({"decided", "why", "cost_if_wrong", "alternatives",
                          "scope"})

# Committed store, parallel to attest's SHARD_DIR and kept separate: decisions
# are evidence of a RULING, not of a review, so mixing them into the findings
# corpus would let a decision be counted as a review event.
SHARD_DIR = Path(".warden") / "memory" / "decide"

# The run-dir artifact `warden decide` writes and `memory ingest` sweeps.
ARTIFACT_GLOB = "*-decide/decision.json"


class DecideError(ValueError):
    pass


def build(payload: dict, *, head_sha: str, base_sha: str, rules_version: str,
          pr: str = "", bead: str = "") -> dict:
    """Stamp provenance onto an author's decision payload and validate it.

    payload carries only the author's fields (PAYLOAD_KEYS); provenance is
    stamped here so the record cannot claim a commit or ruleset it was not
    made against. Raises DecideError on an unexpected key or a schema
    violation.
    """
    if not isinstance(payload, dict):
        raise DecideError("decision payload must be a JSON object")
    unexpected = set(payload) - PAYLOAD_KEYS
    if unexpected:
        # repr by the warden-wide rule for rendering an unknown-key set —
        # not because a JSON object can hand back a
        # non-string key, but so no reader has to check per site.
        raise DecideError(
            "decision payload may only carry "
            f"{sorted(PAYLOAD_KEYS)}; got extra key(s) {sorted(map(repr, unexpected))}")
    doc = {
        **({"pr": pr} if pr else {}),
        **({"bead": bead} if bead else {}),
        "head_sha": head_sha,
        "base_sha": base_sha,
        "rules_version": rules_version,
        "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **payload,
    }
    errors = sorted(_validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(e.message[:150] for e in errors[:5])
        raise DecideError(f"decision invalid: {detail}")
    return doc


def render_summary(doc: dict) -> str:
    alts = len(doc.get("alternatives", []))
    tail = f" · {doc['bead']}" if doc.get("bead") else ""
    return (f"decision recorded: {doc['decided']}\n"
            f"  why: {doc['why']}\n"
            f"  cost if wrong: {doc['cost_if_wrong']}\n"
            f"  {alts} alternative(s) weighed · scope: {doc['scope']}\n"
            f"  head {doc['head_sha'][:12]} · rules_version "
            f"{doc['rules_version']} · {doc['decided_at']}{tail}\n")


def render_list(decisions: list[dict]) -> str:
    """The compounding record, read back: one line per committed
    decision, newest first — what `warden decide record` writes.
    `warden decide show` still renders the latest run artifact in
    full; this reads the committed shard store so decisions actually compound."""
    if not decisions:
        return "no committed decisions on record.\n"
    lines = [f"committed decisions ({len(decisions)}), newest first:"]
    for d in decisions:
        when = (d.get("decided_at") or "")[:10]
        bead = f" · {d['bead']}" if d.get("bead") else ""
        head = (d.get("head_sha") or "")[:12]
        # one line per decision is the format's invariant, so a `decided` that
        # carries a newline is flattened rather than spilled across rows.
        decided = " ".join((d.get("decided") or "").split())
        lines.append(f"  {when} · {head}{bead} · {decided}")
    return "\n".join(lines) + "\n"


def _shard_name(doc: dict) -> str:
    """Deterministic from content, so re-ingesting the same decision is a
    no-op: timestamp for ordering, head sha for provenance, a content digest
    so two decisions in the same second at the same commit never collide."""
    stamp = re.sub(r"[^0-9A-Za-z]", "", doc.get("decided_at", ""))
    head = (doc.get("head_sha") or "")[:8]
    digest = hashlib.sha256(
        json.dumps(doc, sort_keys=True).encode()).hexdigest()[:8]
    return f"{stamp}-{head}-{digest}.json"


def ingest(root: Path) -> dict:
    """Sweep `.warden/out/*-decide/decision.json` artifacts into committed
    decision shards. Idempotent: a shard whose name and bytes already exist is
    left untouched. A malformed or schema-invalid artifact is skipped LOUDLY,
    never partially written — the same fail-closed contract memory.ingest keeps
    for attestations.
    """
    shard_dir = root / SHARD_DIR
    new: list[str] = []
    skipped: list[str] = []
    out_dir = root / ".warden" / "out"
    if not out_dir.is_dir():
        return {"new_decisions": new, "skipped_decisions": skipped}
    for artifact in sorted(out_dir.glob(ARTIFACT_GLOB)):
        try:
            doc = json.loads(artifact.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            skipped.append(f"{artifact.parent.name}: {exc}")
            continue
        errors = sorted(_validator.iter_errors(doc), key=lambda e: list(e.path))
        if errors:
            skipped.append(f"{artifact.parent.name}: {errors[0].message[:150]}")
            continue
        # Redact at the shard boundary, exactly as memory.ingest does for
        # attestations: a decision's free-text why /
        # cost_if_wrong / scope is where a rationale plausibly quotes the very
        # secret it discusses, and this store commits into the same
        # .warden/memory/ tree the attestation redaction exists to protect. The
        # SAME scrubber, so the two boundaries cannot drift; idempotent on real
        # provenance (a 40-hex sha / hash cannot match a secret pattern).
        from .memory import _redact_value
        shard = _redact_value(doc)
        shard_dir.mkdir(parents=True, exist_ok=True)
        shard_path = shard_dir / _shard_name(shard)
        body = json.dumps(shard, indent=2) + "\n"
        if shard_path.exists() and shard_path.read_text() == body:
            continue
        shard_path.write_text(body)
        new.append(shard_path.name)
    return {"new_decisions": new, "skipped_decisions": skipped}


def decisions_from_shards(root: Path) -> list[dict]:
    """Every committed decision, newest first — the compounding record a
    reader or a later `warden decide` can weigh against."""
    shard_dir = root / SHARD_DIR
    if not shard_dir.is_dir():
        return []
    out = []
    for shard_path in sorted(shard_dir.glob("*.json")):
        try:
            out.append(json.loads(shard_path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    out.sort(key=lambda d: d.get("decided_at", ""), reverse=True)
    return out
