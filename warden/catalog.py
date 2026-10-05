"""Guardrail catalog: candidate rules a repo might want, each one cited.

The gate enforces whatever `.warden/rules/` happens to contain. Nothing in
the platform tells a repo which guardrails it is MISSING — and a freshly
enrolled repo has no review memory to mine, so the cold start is the common
case for every new consumer. This module is the prior art half of that
answer: a versioned, shipped catalog grounded in published sources.

Three shapes of grounding, three engines they land on:

- CWE Top 25 (2024) and OWASP Top 10:2025 — the security surface. Mostly
  mechanical: a declarative pattern or a python checker.
- The Google reviewer lenses — the judgment surface. Mostly `engine: claude`.
- The residue that is neither, which is the load-bearing part. Insecure
  design cannot be a regex. An entry that says `not-a-rule` and names what
  to do instead is more useful than a rule that pretends, and it is what
  keeps the catalog from degenerating into a checklist of things that
  technically pass.

Every entry cites a source. An entry with no citation is invented advice,
and invented advice is the thing this platform exists to replace — so it
fails at LOAD time, not at review time.

What the offline loader checks is citation SHAPE. It does not fetch
anything: `warden catalog check --online` is the pass that actually resolves
each URL, and the offline output says so rather than letting a reader
believe otherwise.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

from .rules import ENGINES
from . import yamlio

# `not-a-rule` is not an engine anything executes — it is the honest verdict
# that no engine can, and it carries `instead:` in place of a starter.
CATALOG_ENGINES = ENGINES + ("not-a-rule",)

CATALOG_PATH = Path(__file__).resolve().parent / "guardrails" / "catalog.yaml"

_SLUG = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")

# Taxonomy shapes we can trace back to a published list. A new shape means a
# new source of authority, which is a deliberate decision, not a typo.
_TAXONOMIES = {
    "cwe": re.compile(r"\ACWE-\d+\Z"),
    "owasp": re.compile(r"\AA\d{2}:\d{4}\Z"),
    "review-lens": re.compile(r"\Areview-lens:[a-z-]+\Z"),
}

_REQUIRED = ("id", "taxonomy", "name", "guards", "engine", "applies_when",
             "false_positive_cost", "sources")
_KNOWN_KEYS = set(_REQUIRED) | {"starter", "instead", "corpus_classes",
                                 "langs"}

# The languages `warden init` detects (enroll.ALL_LANGUAGES, pinned equal by
# tests/test_advisor.py — enroll imports this module, so it is not imported
# back). An entry's `langs:` names some of these.
LANGS = ("python", "node", "go")

_USER_AGENT = "nightgate-warden-catalog-check"
_TIMEOUT = 15


class CatalogError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    id: str
    taxonomy: str
    name: str
    guards: str            # what it guards, in one sentence a non-specialist reads
    engine: str
    applies_when: str      # an SQL-injection rule on a repo with no database
    #                        is pure noise; this is how the advisor knows
    false_positive_cost: str   # what a wrong firing does to the person who hits it
    sources: tuple[dict, ...]  # [{title, url}] — checked for shape, fetched only
    #                            by `catalog check --online`
    starter: dict | None = None    # declarative: {checks: [...]}; python: {sketch: str}
    instead: str = ""              # not-a-rule: what to do in place of a rule
    corpus_classes: tuple[str, ...] = ()  # defect-class slugs (the review
    #                                 corpus's own vocabulary) this guardrail
    #                                 corresponds to. Declared, never matched
    #                                 by name: the catalog speaks CWE/OWASP
    #                                 and the corpus speaks this repo's defect
    #                                 classes, and inferring the join would
    #                                 dress prior art up as local evidence.
    langs: tuple[str, ...] = ()   # the languages whose idioms the STARTER
    #                               reads (os-command-injection's checks
    #                               read Python's subprocess spellings).
    #                               Empty means it reads none in particular.
    #                               `rules recommend` sets an entry aside on
    #                               a repo that declares none of them, when
    #                               its starter matches nothing in the tree;
    #                               the class may still matter there.


def taxonomy_kind(taxonomy: str) -> str:
    """Which published list this id comes from ('cwe' | 'owasp' | 'review-lens')."""
    for kind, pattern in _TAXONOMIES.items():
        if pattern.match(taxonomy):
            return kind
    raise CatalogError(
        f"taxonomy {taxonomy!r} matches no known list — expected CWE-<n>, "
        f"A<nn>:<year>, or review-lens:<name>")


def _text(raw: dict, key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{where}: {key!r} must be a non-empty string")
    return value.strip()


def _sources(raw: dict, where: str) -> tuple[dict, ...]:
    value = raw.get("sources")
    if not isinstance(value, list) or not value:
        raise CatalogError(f"{where}: 'sources' must list at least one source "
                           "— an entry with no citation does not ship")
    out = []
    for src in value:
        if not isinstance(src, dict) or set(src) != {"title", "url"}:
            raise CatalogError(f"{where}: each source is a mapping with exactly "
                               "'title' and 'url'")
        title, url = src.get("title"), src.get("url")
        if not isinstance(title, str) or not title.strip():
            raise CatalogError(f"{where}: source 'title' must be non-empty")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise CatalogError(f"{where}: source url {url!r} must be an "
                               "absolute https:// URL a reader can open")
        out.append({"title": title.strip(), "url": url})
    return tuple(out)


def _starter(raw: dict, engine: str, where: str) -> dict | None:
    starter = raw.get("starter")
    if starter is not None and not isinstance(starter, dict):
        raise CatalogError(f"{where}: 'starter' must be a mapping")

    if engine in ("claude", "not-a-rule"):
        if starter is not None:
            why = ("a judgment rule's starter IS its prose body"
                   if engine == "claude" else
                   "a not-a-rule entry carries 'instead', not machinery")
            raise CatalogError(f"{where}: engine:{engine} may not ship a "
                               f"'starter' — {why}")
        return None

    if starter is None:
        raise CatalogError(f"{where}: engine:{engine} needs a 'starter' — a "
                           "candidate nobody can paste into a rule file is "
                           "advice, not a guardrail")

    if engine == "declarative":
        if set(starter) != {"checks"}:
            raise CatalogError(f"{where}: a declarative starter holds exactly "
                               "'checks'")
        checks = starter["checks"]
        if not isinstance(checks, list) or not checks:
            raise CatalogError(f"{where}: 'starter.checks' must be a non-empty list")
        # The gate's own compiler, never a second implementation: a starter
        # that will not compile in a rule file must not compile here either.
        from .declarative import compile_check
        seen = set()
        for check in checks:
            if not isinstance(check, dict):
                raise CatalogError(f"{where}: each starter check is a mapping")
            try:
                compile_check(dict(check), where)
            except ValueError as e:
                raise CatalogError(str(e)) from e
            if check["id"] in seen:
                raise CatalogError(f"{where}: duplicate starter check id "
                                   f"{check['id']!r}")
            seen.add(check["id"])
        return {"checks": [dict(c) for c in checks]}

    # engine: python — a real checker is code review, so the catalog ships the
    # sketch a reviewer would implement from, and says that is what it is.
    if set(starter) != {"sketch"}:
        raise CatalogError(f"{where}: a python starter holds exactly 'sketch' "
                           "— the checker outline a reviewer implements from")
    sketch = starter["sketch"]
    if not isinstance(sketch, str) or not sketch.strip():
        raise CatalogError(f"{where}: 'starter.sketch' must be a non-empty string")
    return {"sketch": sketch.strip()}


def _parse_entry(raw: object, index: int) -> Entry:
    where = f"catalog entry #{index}"
    if not isinstance(raw, dict):
        raise CatalogError(f"{where}: must be a mapping")
    where = f"catalog entry {raw.get('id', f'#{index}')!r}"

    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        raise CatalogError(f"{where}: missing required field(s) "
                           f"{', '.join(repr(k) for k in missing)}")
    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        # repr: a YAML mapping key need not be a string, and sorted() over a
        # mix of str and int raises TypeError on exactly the input this
        # branch exists to refuse.
        raise CatalogError(
            f"{where}: unknown key(s) {sorted(map(repr, unknown))}")

    entry_id = _text(raw, "id", where)
    if not _SLUG.match(entry_id):
        raise CatalogError(f"{where}: id must be a lowercase slug "
                           "(words joined by '-')")

    taxonomy = _text(raw, "taxonomy", where)
    taxonomy_kind(taxonomy)  # raises when the shape is not a published list

    engine = _text(raw, "engine", where)
    if engine not in CATALOG_ENGINES:
        raise CatalogError(f"{where}: engine {engine!r} not in {CATALOG_ENGINES}")

    instead = raw.get("instead", "")
    if not isinstance(instead, str):
        raise CatalogError(f"{where}: 'instead' must be a string")
    instead = instead.strip()
    if engine == "not-a-rule" and not instead:
        raise CatalogError(f"{where}: engine:not-a-rule requires 'instead' — "
                           "naming what cannot be checked is only useful "
                           "alongside what to do about it")
    if engine != "not-a-rule" and instead:
        raise CatalogError(f"{where}: 'instead' belongs to engine:not-a-rule "
                           f"entries, not engine:{engine}")

    raw_classes = raw.get("corpus_classes", [])
    if (not isinstance(raw_classes, list)
            or not all(isinstance(s, str) for s in raw_classes)):
        raise CatalogError(f"{where}: 'corpus_classes' must be a list of slugs")
    for slug in raw_classes:
        if not _SLUG.match(slug):
            raise CatalogError(
                f"{where}: corpus_classes entry {slug!r} is not a well-formed "
                "slug — it would match no corpus key and link nothing")

    raw_langs = raw.get("langs", [])
    if (not isinstance(raw_langs, list) or not raw_langs and "langs" in raw
            or not all(isinstance(x, str) for x in raw_langs)):
        raise CatalogError(f"{where}: 'langs' must be a non-empty list of "
                           f"languages from {LANGS}")
    unknown_langs = [x for x in raw_langs if x not in LANGS]
    if unknown_langs:
        raise CatalogError(f"{where}: 'langs' names {unknown_langs}, not a "
                           f"language warden init detects {LANGS}")
    if raw_langs and engine in ("claude", "not-a-rule"):
        raise CatalogError(f"{where}: 'langs' names the language a STARTER "
                           f"reads, and engine:{engine} ships none")

    return Entry(
        id=entry_id,
        taxonomy=taxonomy,
        name=_text(raw, "name", where),
        guards=_text(raw, "guards", where),
        engine=engine,
        applies_when=_text(raw, "applies_when", where),
        false_positive_cost=_text(raw, "false_positive_cost", where),
        sources=_sources(raw, where),
        starter=_starter(raw, engine, where),
        instead=instead,
        corpus_classes=tuple(raw_classes),
        langs=tuple(raw_langs),
    )


def load_catalog(path: Path | None = None) -> tuple[Entry, ...]:
    """Parse and validate a catalog file (the shipped one by default).

    Reads from disk on every call. Nothing here is remembered, because an
    `Entry` is frozen only at its attribute bindings: its `starter` and its
    `sources` mappings stay mutable, and a tuple handed to two callers would
    let the first one's edit reach the second. `shipped_entry_ids` is the
    memoized question, and it answers with strings.
    """
    path = Path(path) if path is not None else CATALOG_PATH
    if not path.is_file():
        raise CatalogError(f"no catalog at {path}")
    try:
        doc = yamlio.load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as e:
        raise CatalogError(f"{path}: not valid YAML: {e}") from e
    if not isinstance(doc, dict):
        raise CatalogError(f"{path}: catalog must be a YAML mapping")
    if not isinstance(doc.get("version"), int):
        raise CatalogError(f"{path}: 'version' must be an integer")
    raw_entries = doc.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise CatalogError(f"{path}: 'entries' must be a non-empty list")

    entries = tuple(_parse_entry(raw, i) for i, raw in enumerate(raw_entries))
    ids = [e.id for e in entries]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise CatalogError(f"{path}: duplicate entry ids: {dupes}")
    return entries


# One question, asked thousands of times, whose answer cannot change while the
# process runs. Every rule file carrying an `implements:` line re-read and
# re-validated the whole shipped catalog just to ask whether an id exists:
# 4,272 full parses in a single `warden certify`, which was most of that
# command's 48 seconds. It is answered once here.
#
# Only the SHIPPED catalog, because that file is package data installed beside
# this module and a warden reading a different one would be a different
# warden. A caller with a catalog file of its own calls `load_catalog(path)`,
# which is unchanged and reads from disk every time — so a caller that writes
# a catalog and reads it back can never be handed the earlier one.
#
# A frozenset of strings, because that is what the caller asks for and because
# nothing a caller holds can then be mutated back into what the next caller
# reads. The parsed `Entry` objects are deliberately NOT kept: their `starter`
# and `sources` mappings are mutable, so sharing them would put the shipped
# catalog one `entry.starter["checks"].append(...)` away from being wrong for
# the rest of the process.
_shipped_entry_ids: frozenset[str] | None = None


def shipped_entry_ids() -> frozenset[str]:
    """The ids the SHIPPED catalog declares, parsed once per process."""
    global _shipped_entry_ids
    if _shipped_entry_ids is None:
        _shipped_entry_ids = frozenset(e.id for e in load_catalog())
    return _shipped_entry_ids


def resolve_source(url: str) -> str | None:
    """Fetch a citation. None when it resolves, else why it did not.

    Only ever called by `catalog check --online`. Nothing in the gate, the
    test suite, or CI reaches the network on its own.
    """
    request = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return None if response.status < 400 else f"HTTP {response.status}"
    except urllib.error.HTTPError as e:
        if e.code not in (403, 405, 501):  # host refuses HEAD; retry as GET
            return f"HTTP {e.code} {e.reason}"
    except (urllib.error.URLError, OSError) as e:
        return f"unreachable: {e}"

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return None if response.status < 400 else f"HTTP {response.status}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code} {e.reason}"
    except (urllib.error.URLError, OSError) as e:
        return f"unreachable: {e}"


# ── Edition currency ──────────────────────────────────────────────────────
# A citation URL resolving is not the same as the edition it cites being
# current: an archived edition URL is SUPPOSED to keep resolving after a newer
# one ships. So a dated source declares, as data, the edition it cites AND a
# probe URL for the NEXT edition. `catalog check --online` asks that declared,
# falsifiable question — "is there a newer one" — instead of scraping prose or
# letting "all URLs resolved" read as "up to date" (the enforcement-claim
# class this closes). The probe is a heuristic and says so: a SUPERSEDED
# result is a prompt to re-triage, never a defect, and never fails the check.
EDITION_KEYS = ("source", "edition", "cited_url", "next_edition",
                "next_probe_url")


def load_editions(path: Path | None = None) -> tuple[dict, ...]:
    """The dated sources' declared editions. Absent block -> empty tuple, so a
    consumer catalog without one still loads and simply has no currency to
    report. Present, every entry is validated to the declared shape."""
    path = Path(path) if path is not None else CATALOG_PATH
    if not path.is_file():
        raise CatalogError(f"no catalog at {path}")
    try:
        doc = yamlio.load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as e:
        raise CatalogError(f"{path}: not valid YAML: {e}") from e
    raw = (doc or {}).get("editions")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise CatalogError(f"{path}: 'editions' must be a list")
    out = []
    for i, ed in enumerate(raw):
        where = f"{path} editions[{i}]"
        if not isinstance(ed, dict):
            raise CatalogError(f"{where}: each edition must be a mapping")
        missing = [k for k in EDITION_KEYS if k not in ed]
        if missing:
            raise CatalogError(f"{where}: missing key(s) {missing}")
        unknown = set(ed) - set(EDITION_KEYS)
        if unknown:
            # repr, for the reason above.
            raise CatalogError(
                f"{where}: unknown key(s) {sorted(map(repr, unknown))}")
        for k in EDITION_KEYS:
            if not isinstance(ed[k], str) or not ed[k].strip():
                raise CatalogError(f"{where}: {k!r} must be a non-empty string")
        for k in ("cited_url", "next_probe_url"):
            if not ed[k].startswith("https://"):
                raise CatalogError(
                    f"{where}: {k!r} must be an absolute https URL, got {ed[k]!r}")
        out.append({k: ed[k] for k in EDITION_KEYS})
    sources = [e["source"] for e in out]
    dupes = sorted({s for s in sources if sources.count(s) > 1})
    if dupes:
        raise CatalogError(f"{path}: duplicate edition source(s): {dupes}")
    return tuple(out)


def _currency_status(reason: str | None) -> tuple[str, str | None]:
    """Classify a next-edition probe result into a currency verdict.

    Only a DEFINITIVE host answer of not-found (404/410) proves the next
    edition is absent -> CURRENT. A probe that could not complete — unreachable,
    rate-limited, a 5xx, a 403 — proves nothing, so it is UNKNOWN, not the
    benign CURRENT. Reporting an inconclusive probe as CURRENT would
    re-manufacture the exact false confidence this check exists to kill;
    UNKNOWN is not a pass, the same
    line the retro draws with 'UNKNOWN is not zero'."""
    if reason is None:
        return "SUPERSEDED", None
    m = re.search(r"HTTP (\d+)", reason)
    if m and int(m.group(1)) in (404, 410):
        return "CURRENT", None
    return "UNKNOWN", reason


def edition_currency(editions, probe=None) -> list[dict]:
    """Probe each declared edition's NEXT-edition URL. Resolves -> SUPERSEDED
    (a newer edition appears to exist); a definitive not-found -> CURRENT; a
    probe that could not complete -> UNKNOWN (never the benign CURRENT).
    `probe` defaults to resolve_source (looked up at call time, so tests can
    inject one). Never raises and never fails the check — currency is a report,
    not a gate."""
    probe = probe or resolve_source
    rows = []
    for ed in editions:
        status, detail = _currency_status(probe(ed["next_probe_url"]))
        rows.append({"source": ed["source"], "edition": ed["edition"],
                     "next_edition": ed["next_edition"],
                     "next_probe_url": ed["next_probe_url"],
                     "status": status, "detail": detail})
    return rows


def render_currency(rows) -> list[str]:
    """Human-readable currency report, reused by the CLI and the retro. Frames
    every verdict honestly: SUPERSEDED is a probe-based re-triage prompt (not
    proof), CURRENT is 'the declared probe returned not-found' (not 'up to
    date'), and UNKNOWN says the probe could not decide (not a pass)."""
    if not rows:
        return ["catalog currency: no dated editions declared."]
    lines = ["catalog currency (probe-based — SUPERSEDED and UNKNOWN are "
             "re-triage prompts, not proof, and never fail the check):"]
    for r in rows:
        if r["status"] == "SUPERSEDED":
            lines.append(
                f"  SUPERSEDED {r['source']}:{r['edition']} — a "
                f"{r['next_edition']} edition appears to exist at "
                f"{r['next_probe_url']}; re-triage the catalog against it.")
        elif r["status"] == "CURRENT":
            lines.append(
                f"  CURRENT {r['source']}:{r['edition']} — the declared "
                f"{r['next_edition']} probe returned not-found; no newer "
                "edition there.")
        else:  # UNKNOWN — the probe could not decide; never read as a pass
            lines.append(
                f"  UNKNOWN {r['source']}:{r['edition']} — could not probe the "
                f"{r['next_edition']} edition ({r['detail']}); currency "
                "undetermined, not a pass. Re-triage the probe URL if it never "
                "resolves.")
    return lines
