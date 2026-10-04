"""Guardrail catalog — cited candidates mapped to engine and false-positive
cost.

Two halves, and the second is the one that matters. The first asserts the
loader rejects a malformed entry at LOAD time. The second asserts the
SHIPPED catalog itself holds: an entry with no citation is invented advice,
and invented advice is exactly what this platform exists to replace.
"""

import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from warden import catalog as cat
from warden import cli

# --------------------------------------------------------------------------
# loader contract — a malformed entry fails at load, never silently at use
# --------------------------------------------------------------------------

GOOD = {
    "id": "sql-injection",
    "taxonomy": "CWE-89",
    "name": "SQL injection",
    "guards": "Query strings built by concatenating untrusted input, which "
              "lets a caller rewrite the query.",
    "engine": "declarative",
    "applies_when": "the repo talks to a relational database",
    "false_positive_cost": "A flagged string-built query that is actually "
                           "constant costs the author a dismissal comment.",
    "sources": [{"title": "CWE-89", "url": "https://cwe.mitre.org/data/definitions/89.html"}],
    "starter": {
        "checks": [{"id": "sql-concat", "pattern": r"execute\(.*\+",
                    "message": "SQL built by concatenation"}],
    },
}


def write_catalog(tmp_path: Path, *entries: dict, version: int = 1) -> Path:
    p = tmp_path / "guardrails.yaml"
    p.write_text(yaml.safe_dump({"version": version, "entries": list(entries)}))
    return p


def mutate(**over) -> dict:
    """A copy of GOOD with keys replaced, or dropped when the value is None."""
    out = {k: v for k, v in GOOD.items()}
    for k, v in over.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def test_good_entry_loads(tmp_path):
    entries = cat.load_catalog(write_catalog(tmp_path, GOOD))
    assert [e.id for e in entries] == ["sql-injection"]
    assert entries[0].engine == "declarative"


@pytest.mark.parametrize("field", ["id", "taxonomy", "name", "guards",
                                   "engine", "applies_when",
                                   "false_positive_cost", "sources"])
def test_missing_required_field_is_rejected(tmp_path, field):
    path = write_catalog(tmp_path, mutate(**{field: None}))
    with pytest.raises(cat.CatalogError) as e:
        cat.load_catalog(path)
    assert field in str(e.value)


def test_entry_without_a_citation_is_rejected(tmp_path):
    """The acceptance criterion, at the loader: no citation, no entry."""
    path = write_catalog(tmp_path, mutate(sources=[]))
    with pytest.raises(cat.CatalogError, match="at least one source"):
        cat.load_catalog(path)


def test_citation_must_carry_a_title_and_an_absolute_https_url(tmp_path):
    for bad in ({"title": "CWE-89", "url": "cwe.mitre.org/89"},
                {"title": "CWE-89", "url": "http://cwe.mitre.org/89"},
                {"title": "", "url": "https://cwe.mitre.org/89"}):
        with pytest.raises(cat.CatalogError):
            cat.load_catalog(write_catalog(tmp_path, mutate(sources=[bad])))


def test_unknown_engine_is_rejected(tmp_path):
    path = write_catalog(tmp_path, mutate(engine="vibes", starter=None))
    with pytest.raises(cat.CatalogError, match="engine"):
        cat.load_catalog(path)


def test_not_a_rule_entry_must_say_what_to_do_instead(tmp_path):
    path = write_catalog(tmp_path, mutate(engine="not-a-rule", starter=None))
    with pytest.raises(cat.CatalogError, match="instead"):
        cat.load_catalog(path)


def test_not_a_rule_entry_may_not_ship_a_starter(tmp_path):
    """The class is load-bearing: a NOT-A-RULE entry with a starter is a rule
    pretending it cannot be one."""
    path = write_catalog(tmp_path, mutate(engine="not-a-rule",
                                          instead="Review the design doc."))
    with pytest.raises(cat.CatalogError, match="starter"):
        cat.load_catalog(path)


def test_instead_on_an_enforceable_entry_is_rejected(tmp_path):
    path = write_catalog(tmp_path, mutate(instead="Think harder."))
    with pytest.raises(cat.CatalogError, match="instead"):
        cat.load_catalog(path)


def test_declarative_starter_must_compile_as_a_real_check(tmp_path):
    """Reuses the gate's own compiler — a starter that cannot compile is a
    starter nobody can paste into a rule file."""
    broken = {"checks": [{"id": "bad", "pattern": "unclosed(", "message": "x"}]}
    with pytest.raises(cat.CatalogError, match="does not compile"):
        cat.load_catalog(write_catalog(tmp_path, mutate(starter=broken)))


def test_declarative_entry_needs_a_starter(tmp_path):
    with pytest.raises(cat.CatalogError, match="starter"):
        cat.load_catalog(write_catalog(tmp_path, mutate(starter=None)))


def test_python_entry_needs_a_starter_sketch(tmp_path):
    path = write_catalog(tmp_path, mutate(engine="python", starter={}))
    with pytest.raises(cat.CatalogError, match="sketch"):
        cat.load_catalog(path)


def test_claude_entry_may_not_ship_a_starter(tmp_path):
    """A judgment rule's starter is its prose body; a checks/sketch block on a
    claude entry would promise machinery that never runs."""
    path = write_catalog(tmp_path, mutate(engine="claude"))
    with pytest.raises(cat.CatalogError, match="starter"):
        cat.load_catalog(path)


def test_unknown_entry_key_is_rejected(tmp_path):
    path = write_catalog(tmp_path, mutate(severity="HIGH"))
    with pytest.raises(cat.CatalogError, match="unknown"):
        cat.load_catalog(path)


def test_duplicate_ids_are_rejected(tmp_path):
    path = write_catalog(tmp_path, GOOD, mutate(taxonomy="CWE-20"))
    with pytest.raises(cat.CatalogError, match="duplicate"):
        cat.load_catalog(path)


def test_taxonomy_id_must_be_a_recognized_shape(tmp_path):
    path = write_catalog(tmp_path, mutate(taxonomy="made-up-42"))
    with pytest.raises(cat.CatalogError, match="taxonomy"):
        cat.load_catalog(path)


def test_empty_catalog_is_rejected(tmp_path):
    with pytest.raises(cat.CatalogError, match="entries"):
        cat.load_catalog(write_catalog(tmp_path))


# --------------------------------------------------------------------------
# asked once — and the boundary of "once"
# --------------------------------------------------------------------------
# Every rule file with an `implements:` line re-read and re-validated the whole
# shipped catalog to ask whether one id exists: 4,272 full parses in a single
# `warden certify`, most of that command's 48 seconds. The question is answered
# once now, and the three below pin what that may and may not reach — the ids
# are remembered, the Entry objects are not, and a caller's own file is read
# from disk every time.


def test_the_shipped_ids_are_read_once_per_process(monkeypatch):
    monkeypatch.setattr(cat, "_shipped_entry_ids", None)
    reads = []
    real = cat.load_catalog
    monkeypatch.setattr(cat, "load_catalog",
                        lambda p=None: (reads.append(p), real(p))[1])

    first, second = cat.shipped_entry_ids(), cat.shipped_entry_ids()

    assert len(reads) == 1, "the shipped catalog was parsed more than once"
    assert first == second
    assert "unsafe-deserialization" in first


def test_a_mutated_entry_cannot_reach_the_next_caller(monkeypatch):
    """Nothing hands two callers the same `Entry`.

    `@dataclass(frozen=True)` freezes an Entry's attribute BINDINGS, not the
    `starter` and `sources` mappings behind them. A remembered tuple would put
    the shipped catalog one `entry.starter["checks"].append(...)` away from
    being wrong for the rest of the process, so the memoized answer is a
    frozenset of ids and `load_catalog` keeps reading from disk.
    """
    monkeypatch.setattr(cat, "_shipped_entry_ids", None)
    entry = next(e for e in cat.load_catalog() if e.starter and "checks" in e.starter)
    entry.starter["checks"].append({"id": "poison", "pattern": "x", "message": "y"})
    entry.sources[0]["url"] = "https://poisoned.example"

    fresh = next(e for e in cat.load_catalog() if e.id == entry.id)
    assert not any(c["id"] == "poison" for c in fresh.starter["checks"])
    assert fresh.sources[0]["url"] != "https://poisoned.example"
    assert entry.id in cat.shipped_entry_ids()


def test_a_named_catalog_file_is_reread_after_it_changes(tmp_path):
    """A catalog this process WROTE is read again after it is rewritten.

    The suite writes a catalog, loads it, rewrites the same path and loads it
    again constantly. Handing back the pre-rewrite entries would let a guard
    pass on input it never read.
    """
    path = write_catalog(tmp_path, mutate(id="sql-injection"))
    assert [e.id for e in cat.load_catalog(path)] == ["sql-injection"]

    write_catalog(tmp_path, mutate(id="path-traversal"))
    assert [e.id for e in cat.load_catalog(path)] == ["path-traversal"]


# --------------------------------------------------------------------------
# the SHIPPED catalog — the acceptance criteria, asserted on real bytes
# --------------------------------------------------------------------------

# The acceptance criteria are asserted on the RAW yaml, not on parsed Entry
# objects. Assert them through the loader and they cannot fail: the loader
# refuses an uncited entry, so `not e.sources` is unreachable and the test is
# a restatement of the code it is supposed to check. Reading the bytes means
# deleting a citation from the shipped file turns exactly this test red.
RAW_ENTRIES = yaml.safe_load(
    cat.CATALOG_PATH.read_text(encoding="utf-8"))["entries"]


@pytest.fixture(scope="module")
def shipped():
    return cat.load_catalog()


def test_shipped_catalog_loads_and_is_not_empty(shipped):
    assert len(shipped) >= 20


def test_no_shipped_entry_ships_without_a_citation():
    """Acceptance: an entry with no citation does not ship."""
    uncited = [e.get("id") for e in RAW_ENTRIES if not e.get("sources")]
    assert uncited == [], f"catalog entries with no citation: {uncited}"


def test_every_shipped_entry_declares_an_engine():
    undeclared = [e.get("id") for e in RAW_ENTRIES
                  if e.get("engine") not in cat.CATALOG_ENGINES]
    assert undeclared == []


def test_every_shipped_entry_states_a_false_positive_cost():
    missing = [e.get("id") for e in RAW_ENTRIES
               if not str(e.get("false_positive_cost", "")).strip()]
    assert missing == []


def test_every_shipped_not_a_rule_entry_explains_what_to_do_instead():
    naive = [e.get("id") for e in RAW_ENTRIES
             if e.get("engine") == "not-a-rule"
             and not str(e.get("instead", "")).strip()]
    assert naive == []


def test_the_not_a_rule_class_actually_ships():
    """Kept honest on purpose: if this list is ever empty, someone tidied the
    class away by inventing rules for things a rule cannot check."""
    assert [e for e in RAW_ENTRIES if e.get("engine") == "not-a-rule"]


def test_shipped_catalog_covers_all_three_grounded_taxonomies(shipped):
    kinds = {cat.taxonomy_kind(e.taxonomy) for e in shipped}
    assert kinds == {"cwe", "owasp", "review-lens"}


def test_shipped_catalog_covers_every_owasp_2025_item(shipped):
    owasp = {e.taxonomy for e in shipped if e.taxonomy.endswith(":2025")}
    assert owasp == {f"A{n:02d}:2025" for n in range(1, 11)}


# The 2024 CWE Top 25 ranks this catalog carries an entry for. Deliberately
# not all 25: the memory-safety ranks fold into out-of-bounds-memory-access,
# and the rest are untriaged. Pinned as a SET so adding or dropping one is a
# decision someone makes on purpose.
SHIPPED_CWE = {"CWE-79", "CWE-89", "CWE-78", "CWE-22", "CWE-502", "CWE-798",
               "CWE-862", "CWE-352", "CWE-918", "CWE-400", "CWE-20", "CWE-787"}
SHIPPED_LENSES = {"design", "functionality", "complexity", "tests", "naming",
                  "comments", "style", "consistency", "documentation", "context"}


def test_shipped_catalog_pins_its_cwe_coverage():
    """The CWE half is pinned as exactly as the OWASP half: a bare
    `len(shipped) >= 20` would let five CWE entries be deleted with the whole
    suite green. Coverage the catalog header advertises has to be asserted,
    not implied."""
    cwe = {e["taxonomy"] for e in RAW_ENTRIES
           if str(e["taxonomy"]).startswith("CWE-")}
    assert cwe == SHIPPED_CWE


def test_shipped_catalog_pins_every_google_reviewer_lens():
    """Same asymmetry, third taxonomy. Ten of the TWELVE lenses on the cited
    page: Every Line and Good Things describe how to conduct a review, not a
    defect class a guardrail could name, and the catalog header says so
    rather than letting this pin read as the whole checklist."""
    lenses = {str(e["taxonomy"]).split(":", 1)[1] for e in RAW_ENTRIES
              if str(e["taxonomy"]).startswith("review-lens:")}
    assert lenses == SHIPPED_LENSES


def test_shipped_catalog_cites_only_the_grounded_source_hosts(shipped):
    """Citations are verified at source when authored; a new host is a new
    claim and has to be grounded deliberately, not slipped in."""
    hosts = {s["url"].split("/")[2] for e in shipped for s in e.sources}
    assert hosts <= {"cwe.mitre.org", "owasp.org", "google.github.io"}, hosts


def test_every_shipped_declarative_starter_compiles(shipped):
    """Load-time already enforces this; named here so the regression is
    attributable to the catalog rather than to the loader."""
    starters = [e for e in shipped if e.engine == "declarative"]
    assert starters
    for e in starters:
        assert e.starter["checks"]


# --------------------------------------------------------------------------
# CLI — the catalog is inspectable, and honest about what it did not check
# --------------------------------------------------------------------------


def test_catalog_list_prints_every_entry(shipped, capsys):
    assert cli.main(["catalog", "list"]) == 0
    out = capsys.readouterr().out
    for e in shipped:
        assert e.id in out


def test_catalog_list_filters_by_engine(shipped, capsys):
    assert cli.main(["catalog", "list", "--engine", "not-a-rule"]) == 0
    out = capsys.readouterr().out
    for e in shipped:
        assert (e.id in out) == (e.engine == "not-a-rule")


def test_catalog_show_renders_citation_and_cost(shipped, capsys):
    entry = shipped[0]
    assert cli.main(["catalog", "show", entry.id]) == 0
    out = capsys.readouterr().out
    assert entry.sources[0]["url"] in out
    assert entry.false_positive_cost.split(".")[0] in out


def test_show_renders_a_starter_that_can_actually_be_pasted(shipped, capsys):
    """The starter renders unescaped: yaml.safe_dump escapes the em dashes in
    check messages to \\uXXXX, and the starter rendered on screen must be the
    starter you can paste into a rule file."""
    entry = next(e for e in shipped
                 if e.starter and "checks" in e.starter
                 and any("—" in c["message"] for c in e.starter["checks"]))
    assert cli.main(["catalog", "show", entry.id]) == 0
    out = capsys.readouterr().out
    assert "\\u" not in out
    for check in entry.starter["checks"]:
        assert check["message"] in out


def test_catalog_show_unknown_id_exits_2(capsys):
    assert cli.main(["catalog", "show", "no-such-entry"]) == 2
    assert "warden:" in capsys.readouterr().err


def test_catalog_check_passes_on_the_shipped_catalog(capsys):
    assert cli.main(["catalog", "check"]) == 0


def test_offline_check_states_that_it_did_not_fetch_the_citations(capsys):
    """Enforcement truth (the repo's recurring defect class): the offline
    check validates citation SHAPE only. It must not let a reader believe it
    resolved anything."""
    assert cli.main(["catalog", "check"]) == 0
    out = capsys.readouterr().out
    assert "not fetched" in out
    assert "--online" in out


def test_online_check_actually_fetches_every_citation(shipped, monkeypatch, capsys):
    fetched = []

    def fake_resolve(url: str) -> str | None:
        fetched.append(url)
        return None

    monkeypatch.setattr(cat, "resolve_source", fake_resolve)
    assert cli.main(["catalog", "check", "--online"]) == 0
    # citations AND the declared next-edition probes both go through
    # resolve_source (the edition currency pass).
    expected = {s["url"] for e in shipped for s in e.sources}
    expected |= {ed["next_probe_url"] for ed in cat.load_editions()}
    assert set(fetched) == expected
    assert "not fetched" not in capsys.readouterr().out


def test_online_check_fails_when_a_citation_does_not_resolve(monkeypatch, capsys):
    monkeypatch.setattr(cat, "resolve_source", lambda url: "404 Not Found")
    assert cli.main(["catalog", "check", "--online"]) == 2
    assert "404 Not Found" in capsys.readouterr().err


def test_catalog_show_without_an_id_names_the_missing_argument(capsys):
    """`id` is nargs='?', so None must not fall into the lookup and have the
    error tell the reader to go find an entry called None."""
    assert cli.main(["catalog", "show"]) == 2
    err = capsys.readouterr().err
    assert "an entry id is required" in err
    assert "None" not in err


# --------------------------------------------------------------------------
# resolve_source — the only network code in the platform. These drive it
# directly, with urlopen itself replaced: an --online test that monkeypatches
# this function away runs not one line of it.
# --------------------------------------------------------------------------


class _Response:
    def __init__(self, status): self.status = status
    def __enter__(self): return self
    def __exit__(self, *exc): return False


def _http_error(code: str | int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.test/x", int(code),
                                  "Boom", {}, None)


def _patch_urlopen(monkeypatch, *outcomes):
    """Replace urlopen with a scripted sequence; record each request method."""
    calls: list[str] = []
    remaining = list(outcomes)

    def fake_urlopen(request, timeout=None):
        calls.append(request.get_method())
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def test_resolve_source_returns_none_when_head_succeeds(monkeypatch):
    calls = _patch_urlopen(monkeypatch, 200)
    assert cat.resolve_source("https://example.test/x") is None
    assert calls == ["HEAD"]


def test_resolve_source_retries_as_get_when_the_host_refuses_head(monkeypatch):
    """cwe.mitre.org-class behaviour: 405 on HEAD is not a dead citation."""
    for code in (403, 405, 501):
        calls = _patch_urlopen(monkeypatch, _http_error(code), 200)
        assert cat.resolve_source("https://example.test/x") is None
        assert calls == ["HEAD", "GET"]


def test_resolve_source_reports_a_real_http_error_without_retrying(monkeypatch):
    calls = _patch_urlopen(monkeypatch, _http_error(404))
    assert cat.resolve_source("https://example.test/x") == "HTTP 404 Boom"
    assert calls == ["HEAD"]


def test_resolve_source_reports_a_non_raising_error_status(monkeypatch):
    _patch_urlopen(monkeypatch, 503)
    assert cat.resolve_source("https://example.test/x") == "HTTP 503"


def test_resolve_source_reports_an_unreachable_host(monkeypatch):
    _patch_urlopen(monkeypatch, urllib.error.URLError("no route"))
    assert "unreachable" in cat.resolve_source("https://example.test/x")


def test_resolve_source_reports_a_get_retry_that_also_fails(monkeypatch):
    """The fall-through must not swallow the second failure — a citation that
    405s then 404s is a dead citation, not a resolved one."""
    _patch_urlopen(monkeypatch, _http_error(405), _http_error(404))
    assert cat.resolve_source("https://example.test/x") == "HTTP 404 Boom"


def test_resolve_source_reports_a_get_retry_that_is_unreachable(monkeypatch):
    _patch_urlopen(monkeypatch, _http_error(405),
                   urllib.error.URLError("no route"))
    assert "unreachable" in cat.resolve_source("https://example.test/x")


def test_resolve_source_reports_a_get_retry_returning_an_error_status(monkeypatch):
    _patch_urlopen(monkeypatch, _http_error(405), 500)
    assert cat.resolve_source("https://example.test/x") == "HTTP 500"


# --------------------------------------------------------------------------
# wiki fidelity — derived, so the page cannot silently omit an engine value
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Edition currency — a cited edition has an expiry, and
# "all URLs resolved" is not "up to date". Editions are DECLARED data: each
# dated source names the edition it cites and a probe URL for the NEXT
# edition, so a check can compare rather than a human re-reading. The probe
# reports loudly (CURRENT/SUPERSEDED); it never fails the check, because a
# new edition is a prompt to re-triage, not a defect.
# --------------------------------------------------------------------------

GOOD_EDITION = {
    "source": "cwe-top-25",
    "edition": "2024",
    "cited_url": "https://cwe.mitre.org/top25/archive/2024/2024_cwe_top25.html",
    "next_edition": "2025",
    "next_probe_url": "https://cwe.mitre.org/top25/archive/2025/2025_cwe_top25.html",
}


def write_catalog_with_editions(tmp_path, editions, *entries):
    p = tmp_path / "guardrails.yaml"
    body = {"version": 1, "entries": list(entries) or [GOOD], "editions": editions}
    p.write_text(yaml.safe_dump(body))
    return p


def test_load_editions_absent_is_empty_not_an_error(tmp_path):
    # A consumer catalog with no editions block still loads — the currency
    # pass simply has nothing to say. Backward compatible.
    path = write_catalog(tmp_path, GOOD)
    assert cat.load_editions(path) == ()


def test_load_editions_parses_the_declared_shape(tmp_path):
    path = write_catalog_with_editions(tmp_path, [GOOD_EDITION])
    (ed,) = cat.load_editions(path)
    assert ed["source"] == "cwe-top-25"
    assert ed["edition"] == "2024"
    assert ed["next_probe_url"].startswith("https://")


@pytest.mark.parametrize("key", sorted(GOOD_EDITION))
def test_edition_missing_a_required_key_is_rejected(tmp_path, key):
    bad = {k: v for k, v in GOOD_EDITION.items() if k != key}
    path = write_catalog_with_editions(tmp_path, [bad])
    with pytest.raises(cat.CatalogError):
        cat.load_editions(path)


def test_edition_urls_must_be_absolute_https(tmp_path):
    bad = {**GOOD_EDITION, "next_probe_url": "http://cwe.mitre.org/2025"}
    path = write_catalog_with_editions(tmp_path, [bad])
    with pytest.raises(cat.CatalogError, match="https"):
        cat.load_editions(path)


def test_edition_currency_is_superseded_when_the_next_edition_resolves():
    # The next-edition URL resolving is the signal a newer edition shipped.
    rows = cat.edition_currency([GOOD_EDITION], probe=lambda url: None)
    (row,) = rows
    assert row["status"] == "SUPERSEDED"
    assert row["next_edition"] == "2025"


def test_edition_currency_is_current_when_the_next_edition_is_absent():
    rows = cat.edition_currency([GOOD_EDITION], probe=lambda url: "HTTP 404")
    assert rows[0]["status"] == "CURRENT"


def test_edition_currency_probes_the_next_url_not_the_cited_one():
    seen = []

    def probe(url):
        seen.append(url)
        return "HTTP 404"

    cat.edition_currency([GOOD_EDITION], probe=probe)
    assert seen == [GOOD_EDITION["next_probe_url"]]
    assert GOOD_EDITION["cited_url"] not in seen


def test_shipped_catalog_declares_editions_for_its_dated_sources():
    """The dated sources (CWE Top 25, OWASP Top 10) declare the edition they
    cite as data, so currency is checkable. Read from the bytes, so deleting
    the block turns this red."""
    raw = yaml.safe_load(cat.CATALOG_PATH.read_text(encoding="utf-8"))
    editions = raw.get("editions") or []
    sources = {e["source"] for e in editions}
    assert {"cwe-top-25", "owasp-top-10"} <= sources, (
        f"dated sources without a declared edition: "
        f"{{'cwe-top-25', 'owasp-top-10'}} - {sources}")
    # and the shipped block validates
    assert cat.load_editions()


def test_online_check_reports_currency_and_never_claims_up_to_date(monkeypatch, capsys):
    """`--online` must report a currency verdict per dated source, and must
    not let 'resolved' read as 'up to date'. Here every citation resolves and
    every next-edition probe 404s (CURRENT)."""
    next_urls = {e["next_probe_url"] for e in cat.load_editions()}

    def fake(url):
        return "HTTP 404" if url in next_urls else None

    monkeypatch.setattr(cat, "resolve_source", fake)
    assert cli.main(["catalog", "check", "--online"]) == 0
    out = capsys.readouterr().out
    assert "CURRENT" in out
    assert "cwe-top-25" in out
    # The biting guard: a bare "all N citation URL(s) resolved." line never
    # contains "up to date", so an "up to date" absence check could not catch
    # a regression to it. Pin the phrase that separates resolution from
    # currency; a bare resolution line fails here.
    assert ("resolution proves the links work, not that the cited editions "
            "are current") in out
    assert "up to date" not in out.lower()


def test_online_check_calls_out_a_superseded_edition_but_still_exits_zero(monkeypatch, capsys):
    """A newer edition is a re-triage prompt, not a defect: reported loudly,
    exit stays 0 (failing would be wrong)."""
    def fake(url):
        return None  # everything resolves, including the next-edition probes

    monkeypatch.setattr(cat, "resolve_source", fake)
    assert cli.main(["catalog", "check", "--online"]) == 0
    out = capsys.readouterr().out
    assert "SUPERSEDED" in out


def test_offline_check_makes_no_currency_claim(capsys):
    # The offline pass fetches nothing, so it must not assert currency either.
    assert cli.main(["catalog", "check"]) == 0
    out = capsys.readouterr().out.lower()
    assert "superseded" not in out and "up to date" not in out


@pytest.mark.parametrize("reason", ["unreachable: timed out", "HTTP 503",
                                    "HTTP 429 Too Many Requests", "HTTP 403"])
def test_edition_currency_is_unknown_when_the_probe_cannot_complete(reason):
    """Enforcement-truth: a probe that could not complete proves nothing
    about currency and must NOT read as the benign CURRENT. It reports
    UNKNOWN and carries the reason as detail."""
    rows = cat.edition_currency([GOOD_EDITION], probe=lambda url: reason)
    assert rows[0]["status"] == "UNKNOWN"
    assert rows[0]["detail"] == reason


@pytest.mark.parametrize("reason", ["HTTP 404 Not Found", "HTTP 410 Gone"])
def test_edition_currency_is_current_only_on_a_definitive_not_found(reason):
    rows = cat.edition_currency([GOOD_EDITION], probe=lambda url: reason)
    assert rows[0]["status"] == "CURRENT"


def test_render_reports_unknown_as_not_a_pass():
    rows = cat.edition_currency([GOOD_EDITION], probe=lambda url: "HTTP 503")
    text = "\n".join(cat.render_currency(rows))
    assert "UNKNOWN" in text
    assert "not a pass" in text
    assert "HTTP 503" in text  # the reason is surfaced, never discarded


def test_shipped_edition_cited_url_is_actually_cited_by_an_entry(shipped):
    """Enforcement-truth: 'derived from the citation' must be literally
    true — an edition's cited_url has to be a URL the catalog's own entries
    actually cite, not a plausible look-alike. This is the strong tie the
    host-match test below only approximates."""
    cited = {s["url"] for e in shipped for s in e.sources}
    for ed in cat.load_editions():
        assert ed["cited_url"] in cited, (
            f"{ed['source']}: cited_url {ed['cited_url']} is not a URL any "
            "catalog entry cites — the edition claims a citation it does not have")


def test_shipped_next_probe_urls_share_their_cited_url_host():
    """A next-edition probe keyed on a host unrelated to its citation is
    invented, not derived. Pin that each probe lives on the same host as the
    edition it advances, so an ungrounded scheme cannot slip in (an OWASP
    probe on www-project-top-ten for a citation on owasp.org/Top10/ is the
    shape this refuses)."""
    from urllib.parse import urlparse
    for ed in cat.load_editions():
        cited_host = urlparse(ed["cited_url"]).netloc
        probe_host = urlparse(ed["next_probe_url"]).netloc
        assert probe_host == cited_host, (
            f"{ed['source']}: probe host {probe_host} != cited host "
            f"{cited_host} — the next-edition URL is not derived from the "
            "citation")
