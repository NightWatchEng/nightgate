"""repo.yaml loading: schema validation, fail-closed behavior, anti-shadow-copy."""

import os
import re
from pathlib import Path

import pytest
import yaml

from conftest import CONSUMER_REPO_NAMES, finishes_within
from warden import config as config_mod
from private_evidence import needs_corpus

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "warden"


def _write_config(sample_repo: Path, tmp_path: Path, mutate) -> Path:
    raw = yaml.safe_load((sample_repo / "repo.yaml").read_text())
    mutate(raw)
    (tmp_path / "repo.yaml").write_text(yaml.safe_dump(raw))
    return tmp_path


def test_sample_repo_yaml_loads(sample_cfg):
    assert sample_cfg.repo == "sampleproj"
    assert {c.name for c in sample_cfg.components} >= {"app", "migrations", "schemas"}
    assert sample_cfg.review.blocking_severities == ("HIGH",)
    assert "contracts" in sample_cfg.verify


def test_find_repo_root_walks_up(sample_repo):
    nested = sample_repo / "app" / "core"
    assert config_mod.find_repo_root(nested) == sample_repo


def test_find_repo_root_fails_outside(tmp_path):
    with pytest.raises(config_mod.ConfigError):
        config_mod.find_repo_root(tmp_path)


def test_missing_components_rejected(sample_repo, tmp_path):
    root = _write_config(sample_repo, tmp_path, lambda raw: raw.pop("components"))
    with pytest.raises(config_mod.ConfigError, match="components"):
        config_mod.load(root)


def test_bad_tier_rejected(sample_repo, tmp_path):
    def mutate(raw):
        raw["risk_tiers"][0]["tier"] = "CRITICAL"
    root = _write_config(sample_repo, tmp_path, mutate)
    with pytest.raises(config_mod.ConfigError, match="CRITICAL"):
        config_mod.load(root)


def test_invalid_yaml_rejected(tmp_path):
    (tmp_path / "repo.yaml").write_text("version: [unclosed")
    with pytest.raises(config_mod.ConfigError, match="YAML"):
        config_mod.load(tmp_path)


def test_classify_first_match_wins_and_defaults_low(sample_cfg):
    assert config_mod.classify(sample_cfg, "db/migrations/0022_new.sql").tier == "HIGH"
    # single-file HIGH tier sits before the MEDIUM app/** catch-all
    assert config_mod.classify(sample_cfg, "app/core/secrets_surface.py").tier == "HIGH"
    assert config_mod.classify(sample_cfg, "app/feeds.py").tier == "MEDIUM"
    assert config_mod.classify(sample_cfg, "README.md").tier == "LOW"


def test_wiki_tier_is_declared_before_the_docs_default():
    """This repo's own wiki is tiered MEDIUM, and the entry must sit ABOVE
    `docs/**` to have any effect.

    The wiki is the reference a consumer enrolls from, so a page that
    misdescribes the system is a defect, not a chore — `docs/**` LOW was the
    wrong tier for it. But `classify()` is first-glob-match-wins, so an entry
    ordered after the `docs/**` catch-all is dead config that reads as policy.
    Assert the classification AND the ordering: the ordering assertion is what
    names the failure when someone tidies the list alphabetically.
    """
    cfg = config_mod.load(ROOT)
    globs = [t.glob for t in cfg.risk_tiers]
    assert "docs/wiki/**" in globs and "docs/**" in globs
    assert globs.index("docs/wiki/**") < globs.index("docs/**"), (
        "docs/wiki/** is ordered after the docs/** catch-all — first match "
        "wins, so the wiki tier can never fire")

    assert config_mod.classify(cfg, "docs/wiki/Gate-Pipeline.md").tier == "MEDIUM"
    assert config_mod.classify(cfg, "docs/design/methodology.md").tier == "LOW"
    # the catch-all still fires for a docs path outside the wiki
    assert config_mod.classify(cfg, "docs/some-note.md").tier == "LOW"


# Canary PATH literals — the directory names of the repo warden was written
# inside before it was a platform. Quoted deliberately: a bare `docs/` occurs
# in this package's own prose all day, and only a quoted one is a path the
# code is reaching for.
_CANARY_PATHS = re.compile(r"[\"'](?:worker/|ios/|supabase/|contracts/|docs/)")

# Consumer repo NAMES, from the one derivation in conftest.py — an AGGREGATE
# over the declared set, word-bounded, matching the sibling guard in
# test_skillpack.py. A single bare literal would see one consumer and stay
# blind to every other, reporting clean while package source names them.
#
# Case-folded, and split from the path half rather than folded into one
# alternation, because the two halves want different flags: a quoted `Docs/`
# is not a path this package reaches for, and `SHORTFALL` in a comment is
# still the repo name.
_CANARY_NAMES = re.compile(
    r"\b(?:" + "|".join(CONSUMER_REPO_NAMES) + r")\b", re.I)


def _repo_fact_leaks(where: str, line: str) -> list[str]:
    """Repo facts leaking into one line of package source.

    Extracted so a FIRES case can drive the REAL matching logic over inline
    strings. Inlined in the caller it would be a pure absence assertion over a
    package that happens to be clean, so a stale name half would go unnoticed
    without anything going red (the same shape the sibling guard pins).
    """
    hits = []
    if _CANARY_PATHS.search(line):
        hits.append(f"{where}: canary path literal — {line.strip()}")
    for name in _CANARY_NAMES.findall(line):
        hits.append(
            f"{where}: names the consumer repo {name!r} — a repo fact belongs "
            f"in repo.yaml, and a comment that needs the provenance can cite "
            f"the bead id instead. If this is the ordinary English word and "
            f"not the repo, reword it; the guard cannot tell them apart and "
            f"fails closed — {line.strip()}")
    return hits


def test_no_shadow_copy_of_repo_facts():
    """The original warden design doc's named defect: repo facts duplicated in CLI code.

    Repo-specific paths must exist ONLY in repo.yaml; the package reads them
    from config at runtime. The forbidden prefixes are canary path literals
    from the repo warden was written inside — any of them (or ANY consumer's
    name) appearing in warden/*.py is drift waiting to happen.
    """
    # THE SUBJECT SET FIRST. `rglob` over an empty match yields no offenders
    # and the assertion below is vacuously true, so without this the scan's
    # WIRING — which tree, which extension — is covered by nothing while the
    # fires case covers only the matcher; pointing the glob at `*.pyx` would
    # stay green. The sibling guard (`test_docs.py`'s reader-facing scan)
    # asserts its subject first for the same reason.
    sources = sorted(PKG_DIR.rglob("*.py"))
    scanned = {p.relative_to(PKG_DIR).as_posix() for p in sources}
    for required in ("attest.py", "config.py", "advisor.py", "mechanical.py"):
        assert required in scanned, (
            f"{required} is not in the scan's subject set, so this guard "
            f"reports clean over a partial package: {sorted(scanned)[:10]}")

    offenders = []
    for src in sources:
        for lineno, line in enumerate(src.read_text().splitlines(), 1):
            offenders += _repo_fact_leaks(
                f"{src.relative_to(PKG_DIR)}:{lineno}", line)
    assert not offenders, "repo facts belong in repo.yaml, not code:\n" + "\n".join(offenders)


def test_the_repo_fact_scan_fires_on_every_declared_name():
    """The catcher's catcher.

    The assertion above passes because the package is clean, so without this
    every part of it would be deletable with the suite green — including the
    name half, which a single stale literal would silently hollow out. A FIRES
    case per declared name is what makes "the set
    is read" a fact rather than a comment.

    Names are composed from `CONSUMER_REPO_NAMES` rather than written out, so
    adding an enrollment to that tuple extends this proof at the same moment
    it extends the guard, and no literal is planted in this file for the
    proximity scan in `test_docs.py` to trip over.
    """
    assert CONSUMER_REPO_NAMES, (
        "the consumer-repo set is empty, so the name half of the scan below "
        "matches nothing and the guard is off with the suite green")
    for repo in CONSUMER_REPO_NAMES:
        assert _repo_fact_leaks("w.py:1", f"# ported over from {repo}"), (
            f"the scan cannot see {repo!r}, a shape already found live")
        assert _repo_fact_leaks("w.py:1", f"# ported over from {repo.upper()}")
    # BOTH word boundaries, separately. The two cases below are suffix
    # shapes, so they drive only the TRAILING `\b`, and dropping the leading
    # one would otherwise stay green — the same mutant the sibling guard in
    # `test_docs.py` pins on its own pattern.
    assert not _repo_fact_leaks("w.py:1", "# a distilled summary of the round")
    assert not _repo_fact_leaks("w.py:1", "# distillation of the corpus")
    for repo in CONSUMER_REPO_NAMES:
        assert not _repo_fact_leaks("w.py:1", f"# a re{repo} of the corpus"), (
            f"a word ENDING in {repo!r} fires, so the leading word boundary "
            "is doing nothing")

    # the path half, equally undeletable before
    for canary in ("worker/", "ios/", "supabase/", "contracts/", "docs/"):
        assert _repo_fact_leaks("w.py:1", f'    p = Path("{canary}x")'), (
            f"the scan cannot see the canary path {canary!r}")
    # BOTH quote characters. The cases above are all double-quoted, so
    # without this the `'` member of the leading character class would be
    # deletable with this module green.
    assert _repo_fact_leaks("w.py:1", "    p = Path('docs/x')"), (
        "the scan cannot see a single-quoted canary path")
    assert not _repo_fact_leaks("w.py:1", "# see docs/wiki for the rationale")
    assert not _repo_fact_leaks("w.py:1", "    return sorted(paths)")


def test_certification_overlay_is_high_tier(monkeypatch):
    """`.warden/certification.yaml` is the ONLY file that can waive a rule_id
    past E-02 (the certification gate) — a one-line addition under
    `grandfathered_rule_ids:` permanently exempts a defect class. Matching no
    risk_tiers glob, classify would default it to LOW — the same tier as
    docs/**, lower than docs/wiki/**, while .warden/rules/** is HIGH. Both
    the overlay's own header and pre-pr-review call it 'reviewed like any
    gate change', so it draws the same tier as .warden/rules/ and a diff
    touching it gets the same scrutiny.
    """
    cfg = config_mod.load(ROOT)
    assert config_mod.classify(cfg, ".warden/certification.yaml").tier == "HIGH", (
        "the certification overlay — the only gate-waiver in the repo — is "
        "not HIGH; a grandfather addition would arrive as an untiered LOW change")


# ---------- declared_rules_dir: the lenient reader still names problems ----


@pytest.mark.parametrize("body, why", [
    ("- just\n- a list\n", "the document is not a mapping"),
    ("review:\n- rules_dir: policy/rules\n", "review is a list, not a mapping"),
    ("review:\n  rules_dir: ['a', 'b']\n", "rules_dir is not a string"),
    ('review:\n  rules_dir: ""\n', "rules_dir is declared empty"),
    ("review:\n  rules_dir:\n",
     "the key is PRESENT with an orphaned value — the null spelling of the "
     "empty declaration (round-2 review, F2): the repo declared something "
     "that cannot be resolved"),
])
def test_declared_rules_dir_flags_a_parseable_but_malformed_declaration(
        tmp_path, body, why):
    """Fail-closed: `problem` is set for a malformed declaration, not only
    for IO/parse errors. A repo.yaml one indent error away from valid —
    `review:` as a LIST — parses fine and coerces to {}; returning the
    default with an empty problem would hand a caller a confident answer
    about a declaration that exists and cannot actually be resolved."""
    (tmp_path / "repo.yaml").write_text(body)
    rules_dir, problem = config_mod.declared_rules_dir(tmp_path)
    assert problem, (
        f"{why}: a declaration that exists but cannot be resolved must "
        "carry a non-empty problem")
    assert "repo.yaml" in problem
    assert rules_dir == tmp_path / config_mod.DEFAULT_RULES_DIR


@pytest.mark.parametrize("body", [
    "",                       # an empty file declares nothing
    "review:\n",              # a review key with nothing under it
    "review:\n  gate: {}\n",  # review present, no rules_dir declared
])
def test_declared_rules_dir_still_defaults_when_nothing_is_declared(
        tmp_path, body):
    """The malformed-declaration guard must not refuse repos that genuinely
    declare nothing — absent file, empty file, or no rules_dir key all keep
    the permissive default with no problem."""
    (tmp_path / "repo.yaml").write_text(body)
    rules_dir, problem = config_mod.declared_rules_dir(tmp_path)
    assert problem == ""
    assert rules_dir == tmp_path / config_mod.DEFAULT_RULES_DIR


def test_declared_rules_dir_reports_a_non_utf8_file_never_crashes(tmp_path):
    """The lenient reader catches UnicodeDecodeError, not only
    OSError/YAMLError: `read_text()` on a non-UTF-8 repo.yaml must not raise
    out of the one function whose contract is to never crash on a file it is
    not validating — that would crash autonomy, advisor, lifecycle, tags, and
    certify with a raw traceback instead of the (default, problem) pair.
    tags.py catches the same class for rule files."""
    (tmp_path / "repo.yaml").write_bytes(b"\xff\xfereview\xe9")
    rules_dir, problem = config_mod.declared_rules_dir(tmp_path)
    assert problem and "repo.yaml" in problem
    assert rules_dir == tmp_path / config_mod.DEFAULT_RULES_DIR

def test_tag_vocabulary_is_high_tier():
    """The certification-overlay argument, applied to the file next to it.
    An `aliases:` entry in `.warden/memory/tags.yaml` folds committed
    evidence between rules at every read seam: one appended alias can detach
    judged records from a rule, re-propose a rule that already shipped, and
    launder a weak class's precision while `warden certify` keeps its level.
    The loader and tests/test_tag_vocabulary_guards.py refuse that shape;
    certify does not notice, so the tier is the human look. Matching no
    risk_tiers glob, the tag vocabulary would default to LOW — the same tier
    as docs/**, on a file the retro edits weekly.
    """
    cfg = config_mod.load(ROOT)
    assert config_mod.classify(cfg, ".warden/memory/tags.yaml").tier == "HIGH", (
        "the tag vocabulary — where an alias line would rewrite which rule "
        "committed evidence counts toward, unnoticed by certify — is "
        "not HIGH; an alias edit would arrive as an untiered LOW change")


# --- a repo.yaml that never finishes being read -----------------------------
#
# A command that opens repo.yaml by name with a plain, blocking read parks
# forever on a path whose read can never finish — a FIFO, first of all: no
# timeout, no error, and no way for a caller to tell a hang from slow work.
# `warden mine` parked that way leaves an orphan run dir (pr-corpus.json, no
# manifest.json) behind.
#
# Every one of these bounds its call, because the failure being tested for IS
# "does not return": run bare, a regression parks the suite instead of failing
# it.


def test_reading_a_fifo_repo_yaml_raises_instead_of_parking(tmp_path,
                                                                make_fifo):
    """The shared reader classifies before it reads, so the read never starts.

    Both halves are load-bearing and neither substitutes for the other:
    O_NONBLOCK is what makes the FIFO open
    return at all, and the fstat is what stops the non-blocking read that
    follows from answering WRONG — on this fd a writerless FIFO reads `b""`,
    an empty repo.yaml, which `declared_rules_dir` would then report as a repo
    that declares nothing."""
    make_fifo(tmp_path / "repo.yaml")

    with pytest.raises(config_mod.NotARegularFileError) as excinfo:
        finishes_within(5, config_mod.read_repo_yaml, tmp_path)
    assert "fifo" in str(excinfo.value).lower(), excinfo.value


def test_a_fifo_with_a_writer_holding_it_open_is_refused_too(tmp_path,
                                                                 make_fifo):
    """The SECOND FIFO shape: a fix guarding only the open would pass a test
    that covers only the writerless one.

    With a writer holding the fd and a byte pushed, a one-byte probe in
    `cli._cmd_mine` SUCCEEDS — asserted here, so the test carries its own
    proof that this shape defeats a probe-only fix — and the command would
    then park downstream inside `config.load` if that opened the same path
    blocking. The reader refuses it on st_mode, so which shape it is never
    matters."""
    path = make_fifo(tmp_path / "repo.yaml")
    reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK)   # lets the writer open
    writer = os.open(path, os.O_WRONLY)
    try:
        os.write(writer, b"repo: x\n")
        # The shape the probe-only fix would have passed on.
        with open(path, "rb") as probe:
            assert probe.read(1) == b"r", \
                "fixture no longer reproduces the probe-succeeds shape"

        with pytest.raises(config_mod.NotARegularFileError):
            finishes_within(5, config_mod.load, tmp_path)
    finally:
        os.close(writer)
        os.close(reader)


def test_the_refusal_is_both_an_os_error_and_a_config_error(tmp_path,
                                                                make_fifo):
    """Two live contracts have to keep holding at once, so the class carries
    both bases rather than either caller special-casing a name:

    - `cli._cmd_mine`'s probe splits FileNotFoundError (unenrolled) from every
      other OSError (uncomputable:<class>), so the refusal has to BE an
      OSError or a FIFO would be stamped a false `unenrolled`.
    - `cli.main` prints ConfigError as one line and everything else as a
      traceback, so it has to be a ConfigError to reach an operator as a
      sentence."""
    make_fifo(tmp_path / "repo.yaml")

    with pytest.raises(OSError):
        finishes_within(5, config_mod.read_repo_yaml, tmp_path)
    with pytest.raises(config_mod.ConfigError):
        finishes_within(5, config_mod.read_repo_yaml, tmp_path)


def test_a_directory_repo_yaml_still_raises_is_a_directory(tmp_path):
    """The trap the non-blocking open sets, and the reason the reader fstats.

    `os.open(dir, O_RDONLY)` SUCCEEDS on POSIX — it is the read that fails —
    so a reader that only opened would lose the IsADirectoryError that
    `Path.read_text()` raises for free, and with it the distinction mine's
    probe exists to surface (mine stamps `uncomputable: IsADirectoryError`,
    never `unenrolled`)."""
    (tmp_path / "repo.yaml").mkdir()

    with pytest.raises(IsADirectoryError):
        finishes_within(5, config_mod.read_repo_yaml, tmp_path)


def test_an_absent_repo_yaml_still_raises_file_not_found(tmp_path):
    """The boundary the refusal must not overshoot. FileNotFoundError is the
    one answer that means "this repo is not enrolled", and a dangling symlink
    resolves to exactly that — both callers below it depend on the class."""
    with pytest.raises(FileNotFoundError):
        config_mod.read_repo_yaml(tmp_path)

    (tmp_path / "repo.yaml").symlink_to(tmp_path / "gone.yaml")
    with pytest.raises(FileNotFoundError):
        config_mod.read_repo_yaml(tmp_path)


def test_a_regular_repo_yaml_reads_the_same_bytes_as_before(sample_repo):
    """The hardening must not cost the normal case: same bytes, no wrapper."""
    assert (config_mod.read_repo_yaml(sample_repo)
            == (sample_repo / "repo.yaml").read_bytes())


def test_load_refuses_a_fifo_repo_yaml_within_bounded_time(tmp_path,
                                                              make_fifo):
    """The strict reader is the call site that makes hardening only the probe
    worthless: `cli._cmd_mine`'s probe can succeed (a writer holding the FIFO
    open, one byte pushed) and the command then reaches load, which must not
    park on the same path."""
    make_fifo(tmp_path / "repo.yaml")

    with pytest.raises(config_mod.ConfigError):
        finishes_within(5, config_mod.load, tmp_path)


def test_declared_rules_dir_reports_a_problem_for_a_fifo(tmp_path,
                                                             make_fifo):
    """The lenient reader keeps its (default, problem) contract: it must not
    crash, must not park, and must not answer with the permissive empty
    problem that means "this repo declares nothing"."""
    make_fifo(tmp_path / "repo.yaml")

    rules_dir, problem = finishes_within(5, config_mod.declared_rules_dir,
                                         tmp_path)
    assert rules_dir == tmp_path / config_mod.DEFAULT_RULES_DIR
    assert problem, "an unreadable repo.yaml answered like one that declares nothing"
    assert "repo.yaml" in problem


def test_find_repo_root_refuses_a_repo_yaml_it_cannot_read(tmp_path,
                                                               make_fifo):
    """The route by which `explain` and `review` reach the hazard — and the
    reason they would not merely hang on it, which is worse.

    A walk testing `is_file()`, which is False for a FIFO, steps OVER a
    repo.yaml that is plainly there and keeps going. In a tree with no
    ancestor repo.yaml that surfaces as "no repo.yaml found from <dir>
    upward" — an absence claim about a file the reader never managed to look
    at, the same collapse mine's probe refuses one layer down. With an
    ancestor repo.yaml it is worse and silent: the walk resolves to the
    ANCESTOR repo and warden gates the inner tree against someone else's
    policy."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (tmp_path / "repo.yaml").write_text("repo: ancestor\n")
    make_fifo(inner / "repo.yaml")

    with pytest.raises(config_mod.ConfigError) as excinfo:
        finishes_within(5, config_mod.find_repo_root, inner)
    message = str(excinfo.value)
    assert "fifo" in message.lower(), message
    assert "no repo.yaml found" not in message, \
        "a repo.yaml that could not be read was reported as an absent one"


def test_find_repo_root_still_walks_past_a_dangling_symlink(tmp_path):
    """The boundary the case above must not overshoot: a symlink pointing at
    nothing resolves to no repo.yaml, so it is genuinely absent here and the
    walk continues — the same answer mine's probe gives it."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (tmp_path / "repo.yaml").write_text("repo: ancestor\n")
    (inner / "repo.yaml").symlink_to(inner / "gone.yaml")

    assert config_mod.find_repo_root(inner) == tmp_path


# --- the errnos a pathlib pre-check answers wrong ---------------------------
#
# A `path.exists()` in front of `declared_rules_dir`'s read, or an
# `is_file()` in front of `find_repo_root`'s classification, answers a
# DIFFERENT question from the one being asked, and pathlib's error
# handling is where they diverge: `exists()`/`is_file()` swallow ELOOP and
# ENOTDIR (answering False, which reads as "absent") and RE-RAISE EACCES
# (crashing out of a reader whose contract is not to crash). One test per
# errno, because one errno passing proves nothing about the others — the FIFO
# case says nothing about `exists()`, which answers True for a FIFO.

_root_cannot_be_walled = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads through a 000 directory, so the wall is not a wall")


def _loop(root: Path) -> None:
    """A symlink loop at repo.yaml: reading it raises ELOOP."""
    (root / "repo.yaml").symlink_to(root / "other.yaml")
    (root / "other.yaml").symlink_to(root / "repo.yaml")


def test_declared_rules_dir_reports_a_problem_for_a_symlink_loop(tmp_path):
    """`Path.exists()` answers False for ELOOP, so an exists() pre-check
    returns the PERMISSIVE empty problem — "this repo declares nothing" — for
    a repo.yaml that is plainly declared and merely unreadable. Downstream
    that is the malformed-declaration fail-open, reached through a different
    errno:
    `autonomy adopt` writes into the guessed default dir with no refusal, and
    the tag layer reads "no ruleset exists to collide with"."""
    _loop(tmp_path)

    rules_dir, problem = finishes_within(5, config_mod.declared_rules_dir,
                                         tmp_path)
    assert rules_dir == tmp_path / config_mod.DEFAULT_RULES_DIR
    assert problem, "an unreadable repo.yaml answered like one that declares nothing"


@_root_cannot_be_walled
def test_declared_rules_dir_reports_a_problem_behind_a_permission_wall(
        tmp_path):
    """The other half of the same pre-check, failing the other way: `exists()`
    RE-RAISES EACCES, so a lenient reader behind it would crash instead of
    returning its (default, problem) pair — the contract the non-UTF-8 case
    holds too, and the one every caller that routes here to avoid crashing
    depends on."""
    walled = tmp_path / "walled"
    walled.mkdir()
    (walled / "repo.yaml").write_text("repo: x\n")
    walled.chmod(0o000)
    try:
        rules_dir, problem = finishes_within(
            5, config_mod.declared_rules_dir, walled)
    finally:
        walled.chmod(0o700)
    assert rules_dir == walled / config_mod.DEFAULT_RULES_DIR
    assert problem, "a repo.yaml behind a permission wall must carry a problem"


def test_declared_rules_dir_stays_permissive_when_there_is_nothing_there(
        tmp_path):
    """The boundary the two cases above must not overshoot. Absent, and the
    dangling symlink that resolves to absent, are the only inputs that keep
    the permissive empty problem — a repo may genuinely declare nothing."""
    assert config_mod.declared_rules_dir(tmp_path) == (
        tmp_path / config_mod.DEFAULT_RULES_DIR, "")

    (tmp_path / "repo.yaml").symlink_to(tmp_path / "gone.yaml")
    assert config_mod.declared_rules_dir(tmp_path) == (
        tmp_path / config_mod.DEFAULT_RULES_DIR, "")


def test_find_repo_root_refuses_a_symlink_loop(tmp_path):
    """Same collapse in the walk: `is_file()` answers False for ELOOP, so an
    is_file() walk steps over an unreadable repo.yaml and resolves to the
    ANCESTOR repo — gating this tree against someone else's policy,
    silently."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (tmp_path / "repo.yaml").write_text("repo: ancestor\n")
    _loop(inner)

    with pytest.raises(config_mod.ConfigError) as excinfo:
        finishes_within(5, config_mod.find_repo_root, inner)
    assert "no repo.yaml found" not in str(excinfo.value), excinfo.value


@_root_cannot_be_walled
def test_find_repo_root_refuses_a_repo_yaml_behind_a_permission_wall(
        tmp_path):
    """The case the handler's own comment names, which it cannot reach behind
    an `is_file()` check: pathlib RE-RAISES EACCES, so a permission wall would
    escape as a bare traceback and the carefully worded refusal never print.
    One stat answers both questions."""
    walled = tmp_path / "walled"
    walled.mkdir()
    (walled / "repo.yaml").write_text("repo: x\n")
    walled.chmod(0o000)
    try:
        with pytest.raises(config_mod.ConfigError) as excinfo:
            finishes_within(5, config_mod.find_repo_root, walled)
    finally:
        walled.chmod(0o700)
    message = str(excinfo.value)
    assert "could not be examined" in message, message
    # And it must not claim the file EXISTS. EACCES here comes from the
    # containing directory's mode and is byte-identical for a present and an
    # absent repo.yaml, so an existence claim sends the reader to look at the
    # wrong thing.
    assert "exists" not in message, message


def test_find_repo_root_refuses_a_symlink_through_a_non_directory(tmp_path):
    """ENOTDIR is "cannot look" too when the candidate IS a directory: the
    symlink target's path runs through a regular file. Treated as absent, it
    produces the same silent ancestor resolution as the FIFO, one errno
    over."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (tmp_path / "repo.yaml").write_text("repo: ancestor\n")
    (inner / "plain.txt").write_text("not a directory\n")
    (inner / "repo.yaml").symlink_to(inner / "plain.txt" / "child")

    with pytest.raises(config_mod.ConfigError) as excinfo:
        finishes_within(5, config_mod.find_repo_root, inner)
    assert "no repo.yaml found" not in str(excinfo.value), excinfo.value


def test_find_repo_root_still_walks_up_when_start_is_a_file(tmp_path):
    """The boundary ENOTDIR must not overshoot: given a FILE as `start`, the
    candidate itself cannot hold a repo.yaml, so that ENOTDIR is an absence
    and the walk continues to the directory that does."""
    (tmp_path / "repo.yaml").write_text("repo: r\n")
    leaf = tmp_path / "sub"
    leaf.mkdir()
    target = leaf / "file.py"
    target.write_text("x = 1\n")

    assert config_mod.find_repo_root(target) == tmp_path


def test_the_readers_docstring_names_a_function_that_exists(tmp_path):
    """A docstring that sends a reader to `autonomy._committed_review_block`
    sends them nowhere: the function is `_committed_policy`. The cheapest
    guard against the class is to
    resolve the name the prose tells a reader to go and check."""
    from warden import autonomy as autonomy_mod

    doc = " ".join((config_mod.read_repo_yaml.__doc__ or "").split())
    assert "autonomy._committed_policy" in doc, \
        "the reader must name the one repo.yaml reader it deliberately excludes"
    assert hasattr(autonomy_mod, "_committed_policy"), \
        "the docstring names a function that no longer exists"
    # The list is CLOSED — it says how many there are — so a new reader added
    # without joining it makes the sentence false rather than incomplete, and
    # a closed count nobody checks is the drift this whole file is about.
    from warden import proportion as proportion_mod

    assert "proportion._declared_at" in doc, \
        "a third reader reads repo.yaml off a rev and the closed list of " \
        "deliberate exceptions does not name it"
    assert hasattr(proportion_mod, "_declared_at"), \
        "the docstring names a function that no longer exists"


# --- the invariant the one-reader design rests on ---------------------------

_READ_CALLS = {"read_text", "read_bytes", "open"}
_ALLOWED_READERS = {("config.py", "read_repo_yaml")}


def _direct_repo_yaml_reads(source: str, module: str) -> list[str]:
    """Every call in `source` that reads repo.yaml by name instead of asking
    `config.read_repo_yaml` — the detector behind the test below.

    It follows a local binding (`path = root / CONFIG_NAME` then
    `path.read_text()`), because that indirection is a common shape of a
    direct read and a detector that only saw one-liners would miss it.
    """
    import ast

    offenders: list[str] = []
    for func in ast.walk(ast.parse(source)):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        def names_it(node) -> bool:
            segment = ast.get_source_segment(source, node) or ""
            return "CONFIG_NAME" in segment or "repo.yaml" in segment

        bound = set()
        for node in ast.walk(func):
            value = getattr(node, "value", None)
            if isinstance(node, ast.Assign) and value is not None and names_it(value):
                bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif (isinstance(node, ast.AnnAssign) and value is not None
                    and names_it(value) and isinstance(node.target, ast.Name)):
                bound.add(node.target.id)

        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in _READ_CALLS:
                target = node.func.value
            elif (isinstance(node.func, ast.Name) and node.func.id == "open"
                    and node.args):
                target = node.args[0]
            else:
                continue
            reads_it = names_it(target) or (isinstance(target, ast.Name)
                                            and target.id in bound)
            if reads_it and (module, func.name) not in _ALLOWED_READERS:
                offenders.append(f"{module}:{node.lineno} in {func.name}()")
    return offenders


def test_nothing_in_the_package_reads_repo_yaml_by_name():
    """`read_repo_yaml`'s docstring calls itself "ONE reader for the whole
    platform", and the whole design rests on that sentence: the hang does not
    come back by editing the reader, it comes back through a NEW call site
    that opens repo.yaml directly. Every behavioural test in this file pins
    one KNOWN call site, so a new one would land green. This is the guard
    that makes the invariant an invariant rather than a claim.

    Deliberately NOT a hit: `autonomy._committed_policy` reads repo.yaml with
    `git show <rev>:repo.yaml`, which never touches the working tree, and
    `tags` only stats it.

    The detector is proved before it is trusted — a grep that cannot fire
    reports a clean tree forever, which is the defect class this repo calls
    tests-bite."""
    planted = (
        'from .config import CONFIG_NAME\n'
        'def one(root):\n'
        '    return (root / CONFIG_NAME).read_text()\n'
        'def two(root):\n'
        '    path = root / CONFIG_NAME\n'
        '    return path.read_bytes()\n'
        'def three(root):\n'
        '    with open(root / "repo.yaml", "rb") as fh:\n'
        '        return fh.read(1)\n'
        # The METHOD spelling of `open`. `three` drives the bare-name arm of
        # the detector; the `open` member of `_READ_CALLS` is read only by
        # the attribute arm, and with no `.open()` planted it would be
        # deletable with this module green.
        'def four(root):\n'
        '    with (root / CONFIG_NAME).open() as fh:\n'
        '        return fh.read()\n')
    caught = _direct_repo_yaml_reads(planted, "planted.py")
    assert len(caught) == 4, \
        f"the detector misses a direct repo.yaml read, so it proves nothing: {caught}"

    # …and the ONE allowed reader is allowed by (module, function). Driving
    # the exemption here means removing it changes a verdict. The same read
    # in another module is still reported.
    allowed = ('from .config import CONFIG_NAME\n'
               'def read_repo_yaml(root):\n'
               '    return (root / CONFIG_NAME).read_text()\n')
    assert _direct_repo_yaml_reads(allowed, "config.py") == [], (
        "the one declared reader is reported as an offender, so the "
        "allowlist is not being consulted")
    assert _direct_repo_yaml_reads(allowed, "other.py"), (
        "the allowlist is keyed on the function name alone, so a "
        "`read_repo_yaml` in any module inherits the exemption")

    offenders = []
    for src in sorted(PKG_DIR.rglob("*.py")):
        offenders += _direct_repo_yaml_reads(
            src.read_text(), src.relative_to(PKG_DIR).as_posix())
    assert not offenders, (
        "repo.yaml is read by name outside config.read_repo_yaml, so the read "
        "can park forever again:\n" + "\n".join(offenders))


# ---------- a ruled-on limitation, kept honest ------------------------------

DECIDE_SHARD_DIR = ROOT / ".warden" / "memory" / "decide"


def _kernel_blocking_ruling_shard():
    """The LATEST committed decision shard on the kernel-blocking class, or
    None.

    Latest, not first. A ruling can be superseded by a later one that
    retracts its central claim. Shard names are
    timestamp-prefixed, so `sorted(...)[-1]` is the standing ruling — and
    reading the first would have this test enforce the retracted one, which
    is the failure mode a superseded-decision lookup has.
    """
    import json
    found = []
    for path in sorted(DECIDE_SHARD_DIR.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        # Keyed on the BEAD, not a phrase from the ruling's text: a
        # superseding ruling words its verdict differently, being a
        # retraction, so matching a sentence of the original would miss it
        # and return the retracted ruling.
        env = doc.get("decision") or doc
        if env.get("bead") == "agentops-b22":
            found.append(env)
    return found[-1] if found else None


@needs_corpus
def test_the_limitation_has_a_committed_ruling_behind_it():
    """This class must NOT be a ruled-on limitation with no ruling behind
    it. So the ruling has to be a committed
    artifact, not a sentence someone wrote in a PR body: `warden decide record`
    plus `memory ingest` put it under `.warden/memory/decide/`, and this is
    what makes "we decided" checkable a year from now.
    """
    shard = _kernel_blocking_ruling_shard()
    assert shard is not None, (
        "no committed decision shard rules on the uninterruptible-kernel-wait "
        "class — Roadmap lists it under Stated limitations, where every entry "
        "must cite the ruling that put it there")
    assert len(shard.get("alternatives") or []) >= 5, (
        "the ruling weighs fewer than five alternatives — the bounds that were "
        "MEASURED and rejected are the evidence, and a ruling without them is "
        "an assertion")
    # Across the WHOLE ruling, not just `why`: the standing ruling carries the
    # per-option measurements in `alternatives`, which is where a reader
    # weighing an option actually looks. Pinning `why` alone would fail a
    # ruling that records MORE there.
    text = " ".join([shard["why"], shard["cost_if_wrong"], shard["decided"],
                     *shard["alternatives"]])
    assert "FIFO" in text and "0.5" in text, (
        "the ruling no longer cites the measurement that decided it: a FIFO "
        "open IS interruptible and a SIGALRM does bound it, which is exactly "
        "why a FIFO-based test would have certified a guard that cannot work")


def test_no_guard_claims_a_bound_warden_does_not_have():
    """The enforcement-truth half, and the reason the ruling adds no guard.

    A watchdog here would report a timeout while the read is still parked in
    the kernel — a false negative dressed as a check, and strictly worse than
    hanging visibly. If a future change adds one, it must come with a test
    that proves the bound against an UNINTERRUPTIBLE wait, which is precisely
    what could not be written; so this fails loudly instead of letting one
    arrive quietly on the strength of a FIFO test.
    """
    import ast

    # The AST, never a substring scan over the source. The docstring in
    # config.py NAMES these mechanisms in order to explain why none of them
    # is used, so a text search flags the explanation as the offence — and the
    # obvious repair, deleting the explanation, is the worst outcome available.
    # Imports and attribute access are what actually add a guard.
    tree = ast.parse((ROOT / "warden" / "config.py").read_text())
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Import):
            used.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            used.add((node.module or "").split(".")[0])
            used.update(a.name for a in node.names)
    # `subprocess` is on this list because it is ALTERNATIVE #4 of the ruling
    # this test enforces — the option that ruling weighed most closely, so
    # the guard must cover it. The bound is stated rather than pretended:
    # this sees names used in config.py, so a deadline helper living in a
    # SIBLING module and merely called from here is invisible to it. That is
    # a real gap, and the reason the Roadmap entry and the docstring carry the
    # ruling too — a reviewer reading either finds it before writing such a
    # helper.
    for smell in ("SIGALRM", "setitimer", "alarm", "signal", "threading",
                  "Thread", "ThreadPoolExecutor", "multiprocessing",
                  "subprocess", "asyncio", "wait_for", "faulthandler",
                  "dump_traceback_later", "_thread", "timeout",
                  "ProcessPoolExecutor", "concurrent", "fork", "posix_spawn"):
        assert smell not in used, (
            f"warden/config.py now uses {smell!r}. Two rulings stand behind "
            "this line and the message has to carry both. The first: no "
            "IN-PROCESS bound honestly reports on a kernel-blocking stat — a "
            "signal is never delivered to a thread in an uninterruptible "
            "wait, so a FIFO test of such a guard passes while proving "
            "nothing. The second: the OUT-OF-PROCESS bound does work "
            "(exit 3 in 1.02s under a capturing caller, with DEVNULL stdio "
            "and start_new_session) and is refused on measured price — 322x "
            "per read, +43ms per invocation, +10.8s per suite run. So a "
            "signal or thread guard here needs a demonstration against an "
            "uninterruptible wait, and a subprocess guard needs the "
            "arithmetic re-done. Neither may be reached by deleting this "
            "test to make a timeout guard go green")


def test_the_config_docstring_states_the_ruling_not_a_todo():
    """"Tracked, not closed" was true before the ruling and is misleading
    after it: a reader deciding whether to add a timeout needs to find the
    reasoning that says not to, at the site they would edit."""
    src = (ROOT / "warden" / "config.py").read_text()
    flat = " ".join(src.split())
    assert "tracked, not closed" not in flat, (
        "the docstring still calls the kernel-blocking class merely tracked — "
        "it was ruled on, and the seam that describes it has to say so")


# --- the deferral carries a price ------------------------------------------

def _deferral_price_ruling_shard():
    """The committed decision shard pricing the deferral, or None.

    Keyed on the bead, exactly as `_kernel_blocking_ruling_shard` is and for
    the same reason: a phrase from the ruling's text is not an identity,
    and this ruling words its verdict as a refusal where the one it follows
    words it as a retraction.
    """
    import json
    for path in sorted(DECIDE_SHARD_DIR.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        env = doc.get("decision") or doc
        if env.get("bead") == "agentops-b22.1":
            return env
    return None


@needs_corpus
def test_the_deferral_is_refused_on_a_measured_price_not_a_vibe():
    """The pricing ruling answers the question the kernel-blocking ruling
    leaves open: not "can it be bounded" but "what does bounding it cost, and
    can the bound be scoped to where it matters".

    An unmeasured deferral is how a stated limitation calcifies while the
    Roadmap keeps promising a bound exists. So the standing position has to
    carry numbers a later reader can disagree with, and the numbers have to
    be in the shard rather than in a PR body nobody can find. The specific
    figures are pinned because the ruling turns on their MAGNITUDE: a 3x tax
    would have been shipped and a 322x one is not, so a ruling that kept the
    words and lost the arithmetic would be a different ruling wearing this
    one's name.
    """
    shard = _deferral_price_ruling_shard()
    assert shard is not None, (
        "no committed decision shard rules on the COST of the bounded read — "
        "that ruling exists so the deferral stops being open-ended, and a "
        "refusal with no measurement behind it is the assertion it replaced")

    text = " ".join([shard["decided"], shard["why"], shard["cost_if_wrong"],
                     shard["scope"], *shard["alternatives"]])
    for number in ("0.03", "10.75", "322x", "1006"):
        assert number in text, (
            f"the pricing ruling no longer cites {number!r} — the refusal is on "
            "measured price, so losing the measurement leaves a bare opinion")
    assert "1.02s" in text or "1.02" in text, (
        "the ruling no longer records that the bound WORKS under a capturing "
        "caller — refusing something on price requires saying it functions")
    assert len(shard.get("alternatives") or []) >= 5, (
        "the ruling weighs fewer than five alternatives; the scoped variants "
        "are the part a reader is most likely to propose next")
