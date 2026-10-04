"""An unknown-key refusal must survive a heterogeneous key set.

THE DEFECT, in one line: `sorted(unknown)` over a set holding both a string
and a non-string raises `TypeError` — so the branch written to REFUSE a
malformed config crashes on exactly the config it exists to refuse, and the
author gets a traceback where they should get the offending key names.

WHY THIS MODULE EXISTS RATHER THAN ONE MORE CASE IN A LOADER'S OWN TEST FILE.
The class is not one site, it is every place in `warden/` that renders an
unknown-key set: eight of them (advisor 2, catalog 2, attest 1, decide 1,
declarative 1, provenance 1). So the guard is written once, across the
loaders, instead of eight times inside six modules.

TWO HALVES, because either alone is defeatable:

1. The BEHAVIOURAL half below feeds a mapping whose unknown keys are `1` and
   `"typo"` through each YAML-fed loader and asserts a complaint naming both.
   It is the half that catches the defect itself.
2. The SOURCE half is what keeps the NINTH site from arriving: a new
   `sorted(unknown)` anywhere under `warden/` fails here, whether or not
   anyone thought to add a case above. It covers the two JSON-fed sites
   (`decide.build`, `attest.build`) that the behavioural half deliberately
   does not: a JSON object's keys are always strings, so those two cannot
   reach the crash and a behavioural case for them would be theatre — but
   they are held to the same spelling so that no reader has to work out, per
   site, whether this particular mapping can hand back a non-string key.

NOT IN SCOPE: `cage/`. `cage/config.py` and `cage/providers.py` render the
same shape, but both parse TOML, whose keys are always strings — and
`cage/providers.py` is a forbidden path for agent edits. Scoping the scan to
`warden/` is therefore the honest bound, and it is stated here rather than
left for a reader to infer from the scan's root.
"""

import re
from pathlib import Path

import pytest
import yaml

from warden import advisor, catalog, declarative, provenance

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "warden"

# The heterogeneous pair. `1` is an int key, which YAML produces from a bare
# `1:` without anyone doing anything exotic, and `"typo"` is the ordinary
# misspelling these branches exist for. Sorting the two together is what
# raises; either alone sorts fine, which is why the defect survived every
# single-key test the loaders already had.
BAD_KEYS = {1: "x", "typo": "y"}


def _answers_file(tmp_path: Path, body: dict) -> Path:
    d = tmp_path / ".warden"
    d.mkdir(parents=True, exist_ok=True)
    (d / "catalog-answers.yaml").write_text(yaml.safe_dump(body))
    return tmp_path


def _advisor_answers(tmp_path: Path) -> str:
    root = _answers_file(tmp_path, {"answers": [
        {"id": "sql-injection", "verdict": "wont-fix", **BAD_KEYS}]})
    answers, complaints = advisor.load_answers(root)
    assert not answers, "a refused answer was loaded anyway"
    return " ".join(complaints)


def _advisor_ceiling(tmp_path: Path) -> str:
    root = _answers_file(tmp_path, {"ceiling": {
        "max_unanswered": 3, "rationale": "a real argument, written out",
        **BAD_KEYS}})
    value, complaints = advisor.load_ceiling(root)
    assert value is None, "a ceiling was read out of a refused declaration"
    return " ".join(complaints)


def _catalog_entry(tmp_path: Path) -> str:
    from test_catalog import GOOD, write_catalog
    path = write_catalog(tmp_path, {**GOOD, **BAD_KEYS})
    with pytest.raises(catalog.CatalogError) as e:
        catalog.load_catalog(path)
    return str(e.value)


def _catalog_edition(tmp_path: Path) -> str:
    from test_catalog import GOOD
    path = tmp_path / "guardrails.yaml"
    path.write_text(yaml.safe_dump({
        "version": 1, "entries": [GOOD],
        "editions": [{**{k: "https://example.test/x" if k.endswith("url")
                         else "v1" for k in catalog.EDITION_KEYS},
                      **BAD_KEYS}]}))
    with pytest.raises(catalog.CatalogError) as e:
        catalog.load_editions(path)
    return str(e.value)


def _provenance_assumes(_tmp_path: Path) -> str:
    fence = yaml.safe_dump({"as_of": "2026-09-01",
                            "steps": [{"step": "s", "evidence": "e"}],
                            "assumes": BAD_KEYS})
    text = "## Provenance\n\n```yaml\n" + fence + "```\n"
    with pytest.raises(provenance.ProvenanceError) as e:
        provenance.parse_provenance_section(text)
    return str(e.value)


def _declarative_check(_tmp_path: Path) -> str:
    with pytest.raises(ValueError) as e:
        declarative.compile_check(
            {"id": "x", "pattern": "a", "message": "m", **BAD_KEYS},
            ".warden/rules/x.md")
    return str(e.value)


HETEROGENEOUS_LOADERS = [
    ("advisor.load_answers", _advisor_answers),
    ("advisor.load_ceiling", _advisor_ceiling),
    ("catalog.load_catalog", _catalog_entry),
    ("catalog.load_editions", _catalog_edition),
    ("provenance.parse_provenance_section", _provenance_assumes),
    ("declarative.compile_check", _declarative_check),
]


@pytest.mark.parametrize("name, run",
                         HETEROGENEOUS_LOADERS,
                         ids=[n for n, _ in HETEROGENEOUS_LOADERS])
def test_an_unknown_key_refusal_names_a_heterogeneous_key_set(
        name, run, tmp_path):
    """Every YAML-fed unknown-key branch complains instead of crashing.

    The assertion is on the KEY NAMES, not merely on "something was raised".
    A refusal that comes back without naming the offending key sends the
    author back to a file they already believed was correct — the whole value
    of these branches is that they say which key.

    Remove the `repr` at any one of the six sites and its case here goes red
    with `TypeError: '<' not supported between instances of 'str' and 'int'`,
    which is the failure a user was getting.
    """
    message = run(tmp_path)
    assert "typo" in message, (
        f"{name} refused a mapping without naming the string key that caused "
        f"it: {message!r}")
    assert "1" in message, (
        f"{name} refused a mapping without naming the non-string key that "
        f"caused it: {message!r}")


# --------------------------------------------------------------------------
# the source half — the ninth site cannot arrive quietly
# --------------------------------------------------------------------------

# The bare-name spelling, which is what every one of the eight sites used.
# Anchored on the LOCAL NAME rather than on "any sorted() call": `sorted()`
# is all over this package over paths, tags and shard ids, and a scan that
# fired on those would be switched off in a week. These three names are what
# an unknown-key set is called here, in every module that has one.
_BARE_SORT = re.compile(r"sorted\(\s*(?:unknown|unexpected|extra)\w*\s*\)")


def test_no_warden_module_renders_an_unknown_key_set_without_repr():
    """The subtractive half: no `sorted(unknown)` survives under `warden/`.

    The behavioural half above is a list someone has to remember to extend.
    This one does not need extending — a new loader that renders its unknown
    keys the crashing way fails here on the day it is written, which is the
    only mechanism that can be true of a class with eight known sites and no
    reason to think eight is the end of it.
    """
    # THE SUBJECT SET FIRST. `PKG_DIR` and the glob can each point at nothing
    # with this module still green, because an empty match yields no
    # offenders and the assertion is then vacuously true. The fires case below
    # pins the REGEX and says nothing about whether the loop ever opens a
    # warden module — so without this, the half the docstring calls "what
    # keeps the NINTH site from arriving" could be switched off by a
    # one-character typo in the scope.
    sources = sorted(PKG_DIR.rglob("*.py"))
    scanned = {p.relative_to(PKG_DIR).as_posix() for p in sources}
    for required in ("advisor.py", "catalog.py", "provenance.py",
                     "declarative.py", "decide.py", "attest.py"):
        assert required in scanned, (
            f"{required} is not in the scan's subject set, so this guard "
            f"reports clean over a partial package: {sorted(scanned)[:10]}")

    offenders = []
    for src in sources:
        for lineno, line in enumerate(src.read_text().splitlines(), 1):
            if _BARE_SORT.search(line):
                offenders.append(
                    f"{src.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "these render an unknown-key set with a bare sorted(), which raises "
        "TypeError the moment the set holds both a string and a non-string — "
        "the refusal path crashing on the input it exists to refuse. "
        "Use sorted(map(repr, ...)):\n  "
        + "\n  ".join(offenders))


def test_the_source_scan_can_actually_fire():
    """The catcher's catcher. The scan above passes because the package is
    clean, so every part of it was deletable with the suite green."""
    for spelling in ("        raise E(f\"got {sorted(unknown)}\")",
                     "    return f\"{sorted(unexpected)}\"",
                     "    x = sorted( extra_keys )"):
        assert _BARE_SORT.search(spelling), (
            f"the scan cannot see {spelling!r}, so it proves nothing about "
            "the spellings it does not happen to match")
    for benign in ("    for p in sorted(unknown_paths.values()):",
                   "    return sorted(map(repr, unknown))",
                   "    files = sorted(p for p in d.iterdir())"):
        assert not _BARE_SORT.search(benign), (
            f"{benign!r} is reported as the defect — the scan taxes a "
            "correct call and would be deleted rather than obeyed")
