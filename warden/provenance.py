"""Skill-step provenance: cite the incident behind a step, and let the retro
name a step whose cited class has gone quiet.

A rule carries `covers:`, a backtest, a precision history, and a pause
mechanism with a required reason; a rule that stops earning its place gets
paused with evidence. A skill step carries nothing, so the craft layer only
grows:
`deliver` and `pre-pr-review` are read in full on every invocation, so every
unnecessary step is paid for on every run, and nothing else measures one.

This module reads a `## Provenance` block from each pack skill — an `as_of`
date and a list of `{step, evidence}` entries — and turns the measurable ones
into a report the retro gathers. Measurable means the evidence is a corpus
`tag:` (a declared defect class); a class that has judged history but nothing
inside the fresh window is QUIET, and its step is a REVIEW candidate.

It never proposes removal. "This step caught nothing" is not proof it is
useless: a guard that prevents a class from ever occurring produces no
findings by working (real-zero), which reads identically to a step nobody
exercises (fail-open). That distinction is a judgment the retro makes with
the corpus in front of it — the same fail-open-vs-real-zero line the miner
already draws — never a verdict this report emits.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

import yaml

from .memory import FRESH_DAYS, _JUDGED_STATUSES
from .tags import applicable_aliases, load_vocab
from . import yamlio

# Evidence ref grammar. A `tag:` names a declared defect class and is the only
# MEASURABLE kind — recurrence is observable in the corpus. The rest are
# provenance: they say why a step exists (a one-time incident) but a bead or a
# commit is not a recurring class, so they are recorded and never called quiet.
_TAG_RE = re.compile(r"^tag:([a-z][a-z0-9-]*)$")
_BEAD_RE = re.compile(r"^agentops-[a-z0-9]+(?:\.[a-z0-9]+)*$")
_COMMIT_RE = re.compile(r"^commit:[0-9a-f]{7,40}$")
_SHARD_RE = re.compile(r"^shard:[\w.-]+$")

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Harness assumptions. A skill may declare, in the same
# ## Provenance block, what it assumes about the environment it runs in:
# sibling skills it invokes by name, and whether it needs subagents. The
# declared keys are closed — a misspelled key (`skill:` for `skills:`) would
# declare nothing and pass every check: existence checked, validity not.
_ASSUMES_KEYS = {"skills", "subagents"}
_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_SIBLING_REF_RE = re.compile(r"nightgate-skills:([a-z][a-z0-9-]*)")


class ProvenanceError(ValueError):
    """A `## Provenance` section that exists but cannot be read as one."""


def _is_recent(ts: str, now: datetime | None) -> bool | None:
    """True/False if `ts` parses (inside/outside the fresh window); None when
    it does not parse. Deliberately NOT `_age_days`, whose undateable->0.0
    default reads as fresh: in this report `fresh` is the BENIGN outcome, so a
    corrupt timestamp counted fresh would flip a genuinely-quiet class to
    `active` and bury the review candidate — a fail-open on corpus-integrity
    data. Undateable stays out of the
    fresh count, so an undateable-only class reads `review`, not `active`."""
    try:
        when = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return max(0.0, (ref - when).total_seconds() / 86400) <= FRESH_DAYS


def parse_provenance_section(text: str) -> dict | None:
    """Extract the `## Provenance` block: `{as_of, steps: [{step, evidence}]}`.

    None when there is no such heading. A heading with no machine-readable
    ```yaml fence, a fence that is not a mapping, or a missing/misshaped
    `as_of` raises ProvenanceError — a section a reader cannot parse is worse
    than none, because a test that only checks the heading would pass it.
    """
    marker = "\n## Provenance"
    idx = text.find(marker) if not text.startswith("## Provenance") else 0
    if idx == -1:
        return None
    section = text[idx:]
    nxt = section.find("\n## ", 1)
    section = section[:nxt] if nxt != -1 else section

    fence = re.search(r"```ya?ml\n(.*?)\n```", section, re.DOTALL)
    if not fence:
        raise ProvenanceError(
            "## Provenance section has no ```yaml block — a heading alone is a "
            "stub the retro cannot read")
    try:
        doc = yamlio.load(fence.group(1))
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        raise ProvenanceError(f"## Provenance yaml did not parse: {exc}") from exc
    if not isinstance(doc, dict):
        raise ProvenanceError("## Provenance block is not a mapping")

    as_of = doc.get("as_of")
    # yaml resolves a bare YYYY-MM-DD to a date object; accept it and both a
    # quoted string form, then hold one string shape downstream.
    if isinstance(as_of, (date, datetime)):
        as_of = as_of.isoformat()[:10]
    if not (isinstance(as_of, str) and ISO_DATE_RE.match(as_of)):
        raise ProvenanceError(
            f"## Provenance needs an as_of: YYYY-MM-DD date, got {as_of!r}")

    steps = doc.get("steps") or []
    if not isinstance(steps, list):
        raise ProvenanceError("## Provenance steps: must be a list")
    parsed = []
    for entry in steps:
        if not isinstance(entry, dict) or "step" not in entry \
                or "evidence" not in entry:
            raise ProvenanceError(
                f"provenance step needs step: and evidence:, got {entry!r}")
        parsed.append({"step": str(entry["step"]),
                       "evidence": str(entry["evidence"])})
    return {"as_of": as_of, "steps": parsed,
            "assumes": _parse_assumes(doc.get("assumes"))}


def _parse_assumes(raw) -> dict:
    """Validate an `assumes:` declaration.

    Absent means nothing declared — exactly the pre-key behavior. Present, it
    must be a mapping over the closed key set with the right shapes; anything
    else raises, because a declaration that silently declares nothing is the
    drift this block exists to make checkable.
    """
    if raw is None:
        return {"skills": [], "subagents": None}
    if not isinstance(raw, dict):
        raise ProvenanceError(f"assumes: must be a mapping, got {raw!r}")
    unknown = set(raw) - _ASSUMES_KEYS
    if unknown:
        # repr: this block is YAML out of a skill's frontmatter fence, whose
        # mapping keys need not be strings; sorted() over a mix raises
        # TypeError on the input the branch exists to refuse.
        raise ProvenanceError(
            f"assumes: unknown keys {sorted(map(repr, unknown))} — a "
            f"misspelled "
            f"assumption declares nothing; the legal keys are "
            f"{sorted(_ASSUMES_KEYS)}")
    skills = raw.get("skills")
    if skills is None:
        skills = []
    # NEVER `or []`: a falsy wrong shape (skills: false, skills: '') would
    # coerce to "nothing declared" and pass every check — the silent-drift
    # shape this validator exists to raise on.
    if not isinstance(skills, list) or not all(
            isinstance(s, str) and _SKILL_NAME_RE.match(s) for s in skills):
        raise ProvenanceError(
            f"assumes: skills: must be a list of skill names, got {skills!r}")
    subagents = raw.get("subagents")
    if subagents is not None and not isinstance(subagents, bool):
        raise ProvenanceError(
            f"assumes: subagents: must be true/false, got {subagents!r}")
    return {"skills": [str(s) for s in skills], "subagents": subagents}


def read_pack_provenance(skills_root) -> dict[str, dict | None]:
    """skill name -> its parsed provenance (None when the section is absent).

    A block that EXISTS but cannot be read — malformed content, OR an I/O
    error reading the file — is stored as `{"error": <msg>}`, never raised:
    this feeds `memory.stats()`, which `certify` calls to judge the promotion
    bar, so one bad skill must not crash a corpus-wide read of an unrelated
    check. The OSError arm closes the
    read_text() half symmetrically with the parse half — an unreadable
    SKILL.md is the same fail-mode as a malformed one. The pack's OWN blocks
    are separately enforced well-formed by tests/test_skillpack.py, which
    calls parse_provenance_section directly and lets it raise. A missing tree
    yields an empty map — a consumer without the pack source measures nothing.
    """
    out: dict[str, dict | None] = {}
    if not skills_root or not skills_root.is_dir():
        return out
    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            try:
                out[skill_dir.name] = parse_provenance_section(skill_md.read_text())
            except (ProvenanceError, OSError) as exc:
                out[skill_dir.name] = {"error": str(exc)}
    return out


def flatten_provenance(pack: dict[str, dict | None]) -> list[dict]:
    """One row per (skill, step), carrying the skill's as_of."""
    rows = []
    for skill, doc in pack.items():
        if not doc or "steps" not in doc:  # absent, or a stored parse error
            continue
        for step in doc["steps"]:
            rows.append({"skill": skill, "as_of": doc["as_of"],
                         "step": step["step"], "evidence": step["evidence"]})
    return rows


def classify_evidence(ref: str, vocab: dict[str, str],
                      aliases: dict[str, str]) -> tuple[str, str | None]:
    """(kind, canonical) for an evidence ref.

    kind is one of: tag (measurable, canonical set), unrecognized-tag
    (a tag: naming no declared class), incident, commit, shard, malformed.
    """
    m = _TAG_RE.match(ref)
    if m:
        name = m.group(1)
        canonical = aliases.get(name, name)
        if canonical in vocab:
            return "tag", canonical
        return "unrecognized-tag", name
    if _BEAD_RE.match(ref):
        return "incident", ref
    if _COMMIT_RE.match(ref):
        return "commit", ref
    if _SHARD_RE.match(ref):
        return "shard", ref
    return "malformed", None


def quiet_steps(entries: list[dict], records: list[dict],
                vocab: dict[str, str] | None = None,
                aliases: dict[str, str] | None = None,
                now: datetime | None = None) -> list[dict]:
    """Read each provenance entry against the corpus; never propose removal.

    A `tag:` entry whose class has judged history but nothing inside
    FRESH_DAYS is QUIET — verdict `review`, a candidate the retro looks at,
    never removes. `active` means fresh judged activity; `no-history` means
    zero judged ever (a new guard, or a real-zero the class never recurs);
    `unmeasured` means the evidence is a one-time incident, not a class;
    `unrecognized-tag`/`malformed` surface a provenance error. No verdict is
    ever a removal — that decision needs the fail-open-vs-real-zero judgment,
    which lives in the retro with the corpus in view, not in this tally.
    """
    vocab = vocab if vocab is not None else {}
    aliases = aliases if aliases is not None else {}

    # judged counts per canonical tag: total, and how many are inside the
    # fresh window. Aliases folded so a record tagged with a retired name
    # still counts toward the class its step cites.
    total: dict[str, int] = {}
    fresh: dict[str, int] = {}
    for r in records:
        if r.get("status") not in _JUDGED_STATUSES:
            continue
        # None (undateable) counts toward total but never toward fresh — a
        # corrupt ts must not let a quiet class read active (fail-closed).
        recent = _is_recent(r.get("ts", ""), now)
        for tag in r.get("tags", []) or []:
            canon = aliases.get(tag, tag)
            total[canon] = total.get(canon, 0) + 1
            if recent is True:
                fresh[canon] = fresh.get(canon, 0) + 1

    rows = []
    for e in entries:
        kind, canon = classify_evidence(e["evidence"], vocab, aliases)
        judged_total = total.get(canon, 0) if kind == "tag" else 0
        judged_fresh = fresh.get(canon, 0) if kind == "tag" else 0
        if kind in ("incident", "commit", "shard"):
            verdict = "unmeasured"
        elif kind in ("unrecognized-tag", "malformed"):
            verdict = kind
        elif judged_total == 0:
            verdict = "no-history"
        elif judged_fresh > 0:
            verdict = "active"
        else:
            verdict = "review"
        rows.append({"skill": e.get("skill", ""), "step": e["step"],
                     "evidence": e["evidence"], "kind": kind,
                     "canonical": canon, "judged_total": judged_total,
                     "judged_fresh": judged_fresh, "verdict": verdict})
    return sorted(rows, key=lambda r: (r["skill"], r["step"]))


def render_quiet_steps(rows: list[dict]) -> list[str]:
    """The provenance section of `memory stats`, read as
    REVIEW candidates and provenance errors — never as removals.

    A quiet step is one whose cited class went silent; the retro decides
    whether that silence is a guard working (keep it) or a guard nobody
    exercises (a removal to propose), which this tally deliberately does not.
    """
    if not rows:
        return []
    lines = [
        "SKILL-STEP PROVENANCE — steps citing a corpus class, read against "
        "recurrence.",
        "  A QUIET step's class has gone silent; that is a REVIEW candidate, "
        "NEVER a removal. Silence can mean the guard is working (real-zero), "
        "which reads identically to a guard nobody exercises (fail-open) — the "
        "retro judges which with the corpus in view.",
    ]
    review = [r for r in rows if r["verdict"] == "review"]
    for r in review:
        lines.append(
            f"  QUIET {r['skill']} — {r['step']!r}: cites {r['evidence']}, "
            f"{r['judged_total']} judged all-time, 0 in the last {FRESH_DAYS}d "
            "— review whether the guard is holding or idle")
    errors = [r for r in rows
              if r["verdict"] in ("unrecognized-tag", "malformed", "malformed-block")]
    for r in errors:
        if r["verdict"] == "unrecognized-tag":
            why = "names no declared tag"
        elif r["verdict"] == "malformed":
            why = "is not a tag:/agentops-/commit:/shard: ref"
        else:  # malformed-block — the whole section would not parse
            why = f"unreadable ## Provenance block: {r.get('error', '')}"
        lines.append(f"  PROVENANCE ERROR {r['skill']} — {r['step']!r}: "
                     f"{r['evidence']} {why}")
    active = sum(1 for r in rows if r["verdict"] == "active")
    unmeasured = sum(1 for r in rows if r["verdict"] == "unmeasured")
    no_hist = sum(1 for r in rows if r["verdict"] == "no-history")
    lines.append(
        f"  ({active} active, {no_hist} no-history/real-zero, {unmeasured} "
        "incident-cited/unmeasured — provenance recorded, recurrence not "
        "measurable)")
    return lines


def provenance_report(skills_root, records, vocab=None, aliases=None,
                      now: datetime | None = None) -> list[dict]:
    """Convenience wrapper the retro/stats path uses: read the pack, read it
    against the corpus. Empty when there is no pack source to read."""
    pack = read_pack_provenance(skills_root)
    entries = flatten_provenance(pack)
    rows = (quiet_steps(entries, records, vocab=vocab, aliases=aliases, now=now)
            if entries else [])
    # A block that would not parse is surfaced as its own row, never dropped
    # and never allowed to have crashed the read above (fail-closed).
    for skill, doc in sorted(pack.items()):
        if isinstance(doc, dict) and doc.get("error"):
            rows.append({"skill": skill, "step": "(## Provenance block)",
                         "evidence": "-", "kind": "malformed-block",
                         "canonical": None, "judged_total": 0,
                         "judged_fresh": 0, "verdict": "malformed-block",
                         "error": doc["error"]})
    return rows


def load_vocab_and_aliases(root):
    """Declared tags + the aliases that FOLD, for the stats() call site.

    The applicable set, not the raw table: `quiet_steps` folds record tags and
    evidence refs with it, over records the corpus already folded. Reading the
    raw table here would fold by an alias the corpus refused, and a provenance
    tally that disagrees with the corpus about a class is worse than no tally.
    """
    return load_vocab(root), applicable_aliases(root)


# ── Harness-assumption pre-flight ───────────────────────────────────────────

def _resolve_sibling(skills_root, name: str) -> tuple[bool, str]:
    """Does the installed pack actually SERVE skill `name`?

    Existence of a file is never accepted as proof that what it names
    resolves: the SKILL.md must exist, be
    readable, and carry frontmatter whose `name:` is the name being resolved.
    """
    target = skills_root / name / "SKILL.md"
    if not target.is_file():
        return False, "not served by the installed pack (no SKILL.md)"
    try:
        text = target.read_text()
    except OSError as exc:
        return False, f"SKILL.md unreadable: {exc}"
    if not text.startswith("---\n"):
        return False, ("SKILL.md exists but has no frontmatter — existence "
                       "is not resolution")
    end = text.find("\n---", 4)
    frontmatter = text[4:end] if end != -1 else ""
    # The name: line is read the way the plugin loader reads it — line-wise,
    # not as a full YAML document, because descriptions legally carry inline
    # colons that a strict yaml.safe_load refuses.
    m = re.search(r"^name:\s*(.+?)\s*$", frontmatter, re.MULTILINE)
    served = m.group(1) if m else None
    # A YAML-quoted name (name: "deliver" / name: 'deliver') is served as the
    # bare name by any frontmatter parser — refusing it would false-block an
    # honest pack.
    if served and len(served) >= 2 and served[0] == served[-1] \
            and served[0] in "'\"":
        served = served[1:-1]
    if served != name:
        return False, (f"SKILL.md exists but its frontmatter names "
                       f"{served!r}, not {name!r}")
    return True, "ok"


def preflight_assumptions(skills_root) -> list[dict]:
    """Resolve every skill's harness assumptions against the installed pack.

    One row per checked assumption: `{skill, kind, name, status, detail}`.
    kind is `sibling` (a skill invoked by name — declared in `assumes:
    skills:` OR referenced in prose as `nightgate-skills:<name>`; an
    undeclared reference is not an unchecked one), `subagents` (a declared
    harness capability — surfaced as `assumed`, never claimed verified,
    because this check cannot see the harness), `declarations` (the
    ## Provenance block itself), or `pack` (the pack directory).

    Fails CLOSED throughout: a missing pack, an unreadable SKILL.md, and a
    declaration block that cannot be parsed are all failures — silence or
    absence never reads as a pass. Statuses `unresolved` and `unreadable`
    are the blocking ones.
    """
    rows: list[dict] = []
    if not skills_root or not skills_root.is_dir():
        return [{"skill": "(pack)", "kind": "pack", "name": str(skills_root),
                 "status": "unresolved",
                 "detail": "no skill pack directory — nothing can resolve, "
                           "and absence is a failure, not a pass"}]
    examined = 0
    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        skill = skill_dir.name
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        examined += 1
        try:
            text = skill_md.read_text()
        except OSError as exc:
            rows.append({"skill": skill, "kind": "declarations", "name": skill,
                         "status": "unreadable",
                         "detail": f"SKILL.md unreadable: {exc}"})
            continue
        declared: list[str] = []
        subagents = None
        try:
            doc = parse_provenance_section(text)
        except ProvenanceError as exc:
            rows.append({"skill": skill, "kind": "declarations", "name": skill,
                         "status": "unreadable",
                         "detail": f"assumptions cannot be read: {exc}"})
            doc = None
        if doc:
            declared = doc["assumes"]["skills"]
            subagents = doc["assumes"]["subagents"]
        siblings = sorted(set(declared) | set(_SIBLING_REF_RE.findall(text)))
        for name in siblings:
            ok, detail = _resolve_sibling(skills_root, name)
            rows.append({"skill": skill, "kind": "sibling", "name": name,
                         "status": "resolved" if ok else "unresolved",
                         "detail": detail})
        if subagents:
            rows.append({"skill": skill, "kind": "subagents",
                         "name": "subagents", "status": "assumed",
                         "detail": "harness capability — not machine-checkable "
                                   "here; the skill must state how it degrades "
                                   "without it"})
    if examined == 0:
        # A directory that EXISTS but serves no skill is the vacuous-pass
        # trap the absent-dir arm alone does not close: --pack pointed at the
        # plugin root instead of its skills/ child, or an empty cache dir,
        # examined nothing — and a check that examined nothing must never
        # read as a pass.
        return [{"skill": "(pack)", "kind": "pack", "name": str(skills_root),
                 "status": "unresolved",
                 "detail": "pack directory contains no skills (no "
                           "<skill>/SKILL.md) — nothing was examined, which "
                           "is not a pass; is --pack pointing at the skills/ "
                           "directory rather than the plugin root?"}]
    return rows


def preflight_problems(rows: list[dict]) -> list[dict]:
    """The blocking subset: stale or unreadable assumptions."""
    return [r for r in rows if r["status"] in ("unresolved", "unreadable")]


def render_preflight(rows: list[dict]) -> str:
    problems = preflight_problems(rows)
    lines = []
    for r in problems:
        lines.append(f"STALE ASSUMPTION {r['skill']} -> {r['name']}: "
                     f"{r['detail']}")
    assumed = [r for r in rows if r["status"] == "assumed"]
    for r in assumed:
        lines.append(f"assumed {r['skill']}: {r['name']} ({r['detail']})")
    resolved = sum(1 for r in rows if r["status"] == "resolved")
    lines.append(f"preflight: {resolved} resolved, {len(assumed)} assumed, "
                 f"{len(problems)} stale/unreadable")
    return "\n".join(lines) + "\n"
