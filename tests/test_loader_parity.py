"""One shard shape, one answer — the ENVELOPE, the `status` field, and the
vocabulary BLOCKS.

Three shapes and one problem: a reader that accepts a shape its siblings
refuse, so the corpus disagrees with itself in silence.

* The envelope — a cache rebuild reading `json.loads(...).get("records",
  [])` dies on a top-level JSON array as `AttributeError: 'list' object has
  no attribute 'get'`, dies on a record with no `id` as `KeyError: 'id'`, and
  SILENTLY ACCEPTS a shard with NO `records` key at all while
  `records_from_shards`, `review_events` and certification's E-02 all refuse
  it by name. That last one is the fail-OPEN direction: the one command a
  consumer is told to run to find the bad shard would be the one that says
  nothing.
* The `status` field — `tags.undeclared_at_bar` tests it for frozenset
  membership, so an unguarded list or dict raises `TypeError: unhashable
  type` — not a ValueError, so it escapes `vocabulary.check`'s only handler
  and reaches the CLI's blanket one: a traceback, exit 2, no run dir, no
  artifact. Reachable because `memory ingest` copies `f.get("status",
  "detected")` verbatim out of an attestation artifact nothing validates, so
  the corpus can be poisoned by a hand-written one and stay poisoned for
  every later reader.
* The vocabulary blocks — `tags:` or `aliases:` as a list or a scalar must
  not load as `{}`. The whole declared vocabulary would vanish with no
  complaint (a false RED, exit 1, every name reading as undecided) and a
  refused `aliases:` BLOCK would fold nothing while the breach verdict told
  its author to "fold it with an `aliases:` entry" — the entry already in
  their file.

Not every case here is a regression. The two `still_...` /
`..._is_not_a_complaint` cases are the OPPOSITE-ERROR guards, which must pass
with or without the guards they bound (they are mutation-checked instead, by
tightening the guard until they redden), and
`..._malformed_envelope[records-not-a-list]` is an invariant all four readers
hold, not a repaired regression.

That second direction is not decoration: a fix that makes a reader stricter
must be checked for the opposite error too, or it starts refusing shards the
corpus legitimately contains.
"""

import json
import subprocess
from pathlib import Path

import pytest

from warden import certify as certify_mod
from warden import cli
from warden import memory as memory_mod
from warden import tags as tags_mod
from private_evidence import needs_corpus

REPO = Path(__file__).resolve().parent.parent
SHARD = "20260901T000000Z-aaaaaaaa-bbbbbbbb.json"
HEAD = "1" * 40

# A rationale that clears `_argument_has_substance`, so a case below fails on
# the shape it is testing and never on the rationale floor.
_ARGUES = ("frozen at the count that exists today so that any further drift "
           "has to be answered rather than quietly absorbed")
_CEILING = f"ceiling:\n  max_undecided: 0\n  rationale: {_ARGUES}\n"


def _repo(tmp_path, *, shard=None, tags_yaml: str | None = None) -> Path:
    """A minimal enrolled repo whose committed corpus is at most one shard."""
    root = tmp_path / "repo"
    (root / ".warden" / "memory" / "attest").mkdir(parents=True)
    (root / ".warden" / "rules").mkdir(parents=True)
    (root / "repo.yaml").write_text(
        "version: 1\nrepo: parity-fixture\n"
        "components:\n  app:\n    path: app/\n    lang: python\n"
        "    description: application code\n"
        "risk_tiers:\n  - glob: app/**\n    tier: MEDIUM\n"
        "    reason: production code\n"
        "verify:\n  smoke:\n    - run: \"true\"\n"
        "review:\n  rules_dir: .warden/rules\n"
        "  blocking_severities: [HIGH]\n")
    if tags_yaml is not None:
        (root / ".warden" / "memory" / "tags.yaml").write_text(tags_yaml)
    if shard is not None:
        (root / ".warden" / "memory" / "attest" / SHARD).write_text(
            json.dumps(shard))
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True,
                   capture_output=True)
    return root


def _record(i=0, **extra) -> dict:
    r = {"id": f"rec-{i}", "ts": f"2026-09-01T00:00:0{i}+00:00", "seq": i,
         "source": "attest", "sha": "a" * 40, "base_sha": "b" * 40,
         "rule_id": "unmapped:ripe", "tags": ["ripe"], "dir_prefix": "app",
         "file": "app/x.py", "line": 1, "severity": "MEDIUM",
         "finding": f"f{i}", "evidence": "e", "status": "fixed"}
    r.update(extra)
    return r


def _all_four_readers_refuse(root: Path) -> None:
    """The parity assertion itself, asked of every reader of the store.

    Each owes the same thing in its own currency: a ValueError NAMING THE
    SHARD from `records_from_shards`, an `unreadable` tally from
    `review_events`, an AttestError naming the shard from `memory ingest`,
    and a False verdict naming the shard from certification's E-02.
    """
    with pytest.raises(ValueError, match=SHARD):
        memory_mod.records_from_shards(root)
    assert memory_mod.review_events(root)["unreadable"] == 1
    with pytest.raises(memory_mod.AttestError, match=SHARD):
        memory_mod.ingest(root, rules_dir=root / ".warden" / "rules")
    ok, detail = certify_mod._run_check(
        {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}, root)
    assert not ok and SHARD in detail, detail


# ── the envelope ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("shard, why", [
    (["a record", "another"], "a top-level JSON array"),
    ("just a string", "a top-level JSON scalar"),
    ({"sha": HEAD, "source": "attest"}, "an object with NO records key"),
    ({"sha": HEAD, "source": "attest", "records": {"not": "a list"}},
     "a records key that is not a list"),
], ids=["array", "scalar", "no-records-key", "records-not-a-list"])
def test_all_four_readers_refuse_the_same_malformed_envelope(
        tmp_path, shard, why):
    """Named regression. The ENVELOPE around the record list owes the same
    parity as the list itself, and `memory ingest` must not be the reader
    that disagrees.

    The third case matters most, and is why this is fail-open rather than a
    crash: `.get("records", [])` defaults a shard with no records key to
    EMPTY, so ingest would rebuild a corpus that silently omits it while its
    three siblings refuse the same file by name. A consumer told to run
    `memory ingest` to find the bad shard would get exit 0 and no shard named.
    """
    _all_four_readers_refuse(_repo(tmp_path, shard=shard))


def test_ingest_names_the_shard_whose_record_carries_no_id(tmp_path):
    """Named regression. `by_id[record["id"]]` must not raise a bare
    `KeyError: 'id'` naming neither the shard nor the reason.

    This is the ONE strictness `memory ingest` holds that its siblings do not,
    and the asymmetry is honest rather than a parity hole: the cache rebuild
    is the only reader that INDEXES by id. `validate_shard_records` is shared
    with readers that never touch the field, so the requirement rides an
    explicit `require_ids` flag instead of being widened onto all four.
    """
    idless = _record()
    idless.pop("id")
    root = _repo(tmp_path, shard={"sha": HEAD, "source": "attest",
                                  "records": [idless]})
    with pytest.raises(memory_mod.AttestError, match=SHARD) as excinfo:
        memory_mod.ingest(root, rules_dir=root / ".warden" / "rules")
    assert "id" in str(excinfo.value)
    # and the readers that do NOT index by id are unmoved — an id-less record
    # is not a parity hole, it is a strictness only one reader needs.
    assert len(memory_mod.records_from_shards(root)) == 1
    assert memory_mod.review_events(root)["unreadable"] == 0


def test_a_well_formed_shard_still_ingests(tmp_path):
    """The opposite error. Every guard above must leave the shape the
    corpus actually holds untouched."""
    root = _repo(tmp_path, shard={"sha": HEAD, "source": "attest",
                                  "verdict": "clean", "records": [_record()]})
    result = memory_mod.ingest(root, rules_dir=root / ".warden" / "rules")
    assert result["total_records"] == 1
    assert memory_mod.review_events(root)["unreadable"] == 0


@needs_corpus
def test_the_committed_corpus_of_this_repo_stays_readable():
    """The opposite error, measured against the REAL corpus rather than a
    fixture: every shard this repo has committed must still validate under
    the strictest reading any of the four now applies (ingest's, ids
    included). A fix that makes a reader stricter is only half checked until
    the corpus it must keep accepting has been run through it."""
    shards = sorted((REPO / ".warden" / "memory" / "attest").glob("*.json"))
    assert len(shards) > 100, "the corpus should be the real one, not a stub"
    for shard in shards:
        memory_mod.validate_shard_envelope(
            shard.name, json.loads(shard.read_text()), require_ids=True)
    assert memory_mod.records_from_shards(REPO)


# ── the status field ──────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [["fixed"], {"was": "fixed"}, 3, None],
                         ids=["list", "dict", "int", "null"])
def test_a_status_that_is_not_a_string_is_refused_by_every_reader(
        tmp_path, status):
    """Named regression. `records_from_shards` must guard `status`:
    `undeclared_at_bar` asks `record.get("status") not in _JUDGED_STATUSES` —
    a frozenset membership test — so an unhashable status would raise
    TypeError from inside `ceiling_status`, past every reader's
    `except ValueError`."""
    _all_four_readers_refuse(_repo(
        tmp_path, shard={"sha": HEAD, "source": "attest",
                         "records": [_record(status=status)]}))


def test_check_vocabulary_delivers_a_verdict_not_a_traceback(tmp_path):
    """Named regression, at the CLI surface: a TypeError past `warden memory
    check-vocabulary`'s only `except ValueError` would escape
    `_cmd_check_vocabulary` BEFORE `runs.create_run_dir` runs and reach
    `cli.main`'s blanket handler — a traceback, exit 2, no run dir and no
    artifact. Exit 2 is right; arriving at it by crashing is not."""
    root = _repo(tmp_path, tags_yaml=_CEILING,
                 shard={"sha": HEAD, "source": "attest",
                        "records": [_record(status=["fixed"])]})
    import os
    cwd = os.getcwd()
    os.chdir(root)
    try:
        code = cli.main(["memory", "check-vocabulary"])
    finally:
        os.chdir(cwd)
    assert code == 2
    artifacts = list((root / ".warden" / "out").glob(
        "*-memory-check-vocabulary/vocabulary-check.json"))
    assert len(artifacts) == 1, "a refusal leaves an artifact; a crash does not"
    doc = json.loads(artifacts[0].read_text())
    assert doc["status"] == "cannot-evaluate"
    assert any(SHARD in c for c in doc["complaints"]), doc["complaints"]


def test_a_poisoned_attestation_never_becomes_a_shard(tmp_path):
    """Named regression, at the SEAM rather than at each reader. `attest
    build` validates against attestation.schema.json, whose `status` is a
    four-value string enum — but `memory ingest` does not validate, it goes
    json.loads -> build_records, and build_records copies `f.get("status",
    "detected")` verbatim. Unrefused, an attestation with `"status":
    ["fixed"]` is SHARDED by ingest, which then crashes on the shard it has
    just written. Refused at the crossing, so the corpus is never poisoned in
    the first place."""
    root = _repo(tmp_path)
    (root / ".warden" / "rules" / "rule-a.md").write_text(
        "---\nid: rule-a\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\n---\nfixture rule\n')
    run = root / ".warden" / "out" / "20260901T000000Z-attest"
    run.mkdir(parents=True)
    (run / "attestation.json").write_text(json.dumps({
        "head_sha": HEAD, "base_sha": "b" * 40, "rules_version": "v1",
        "reviewed_at": "2026-09-01T10:00:00+00:00",
        "reviewers": [{"role": "code-reviewer", "agent": "x"},
                      {"role": "cross-examiner", "agent": "x"}],
        "verdict": "findings-open",
        "findings": [{"rule_id": "rule-a", "severity": "MEDIUM",
                      "file": "app/x.py", "line": 1, "finding": "f",
                      "evidence": "e", "status": ["fixed"]}]}))
    result = memory_mod.ingest(root, rules_dir=root / ".warden" / "rules")
    assert result["new_shards"] == [], "the poisoned artifact must not shard"
    assert any("status" in s for s in result["skipped"]), result["skipped"]
    assert not list((root / ".warden" / "memory" / "attest").glob("*.json"))
    # and the command a consumer actually runs says so out loud — a refusal
    # only the return value carries is a refusal nobody reads.
    import contextlib
    import io
    import os
    cwd = os.getcwd()
    os.chdir(root)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(
                io.StringIO()):
            code = cli.main(["memory", "ingest"])
    finally:
        os.chdir(cwd)
    assert code == 0, "a bad artifact is skipped loudly; the sweep still runs"
    assert "SKIPPED malformed artifact" in err.getvalue()
    assert "status" in err.getvalue()


def test_every_status_the_producer_writes_is_still_accepted(tmp_path):
    """The opposite error. The four statuses `attestation.schema.json`
    declares, plus the `detected` a gate record defaults to, must all pass —
    the guard refuses a SHAPE, never a vocabulary."""
    root = _repo(tmp_path, shard={
        "sha": HEAD, "source": "attest",
        "records": [_record(i, status=s) for i, s in enumerate(
            ("confirmed", "fixed", "refuted", "dismissed-with-reason",
             "detected"))]})
    assert len(memory_mod.records_from_shards(root)) == 5
    assert memory_mod.review_events(root)["unreadable"] == 0


# ── the vocabulary blocks ─────────────────────────────────────────────────

@pytest.mark.parametrize("block, why", [
    ("tags:\n  - fail-open\n", "a leading dash under tags:"),
    ("tags: fail-open\n", "a scalar tags:"),
    ("aliases:\n  - ripe: fail-open\n", "a leading dash under aliases:"),
    ("aliases: ripe->fail-open\n", "a scalar aliases:"),
], ids=["tags-list", "tags-scalar", "aliases-list", "aliases-scalar"])
def test_a_non_mapping_block_is_a_named_refusal_not_a_false_red(
        tmp_path, block, why):
    """Named regression. If `load_vocab` and `load_aliases` returned `{}` for
    any non-mapping block, the whole declared vocabulary — or every declared
    fold — would vanish with NO complaint: every name read as undecided, the
    ceiling red, and the verdict telling the author to declare names already
    declared, or to "fold it with an `aliases:` entry" whose entry is in the
    file it has just silently dropped.

    Fail CLOSED like `load_ceiling` and `load_left_undeclared` do: a
    block that cannot be read is `cannot-evaluate` (exit 2), never a breach
    verdict computed from a vocabulary nobody could read.
    """
    import os
    root = _repo(tmp_path, tags_yaml=block + _CEILING,
                 shard={"sha": HEAD, "source": "attest",
                        "records": [_record(i) for i in range(3)]})
    cwd = os.getcwd()
    os.chdir(root)
    try:
        code = cli.main(["memory", "check-vocabulary"])
    finally:
        os.chdir(cwd)
    assert code == 2, why
    doc = json.loads(list((root / ".warden" / "out").glob(
        "*-memory-check-vocabulary/vocabulary-check.json"))[0].read_text())
    assert doc["status"] == "cannot-evaluate", why
    assert any("must be a mapping" in c for c in doc["complaints"]), doc
    # THE LABEL, not only the payload. Without `vocabulary.check`'s fourth
    # `elif` branch the complaint comes out labelled "RECEIPTS UNREADABLE
    # ('left_undeclared:' cannot be read)" for a `tags:` problem — the reader
    # sent to a block that is fine, which is the exact harm the branch's own
    # comment cites and which its three sibling labels each have a test for.
    # Both directions, as those siblings do: the right label present AND the
    # three wrong ones absent.
    assert any(c.startswith("DECLARATION BLOCK UNREADABLE")
               for c in doc["complaints"]), (why, doc["complaints"])
    joined = " ".join(doc["complaints"])
    for wrong in ("RECEIPTS UNREADABLE", "CEILING UNREADABLE",
                  "VOCABULARY UNREADABLE"):
        assert wrong not in joined, (why, wrong, doc["complaints"])
    # and the label Adopting.md promises a consumer BY NAME is the one the
    # code ships, so the documented contract cannot drift from the string.
    promised = " ".join(
        (REPO / "docs" / "wiki" / "Adopting.md").read_text().split())
    assert "DECLARATION BLOCK UNREADABLE" in promised, why


def test_a_refused_aliases_block_reaches_the_surface_that_prescribes_it(
        tmp_path):
    """Named regression, the second half. `alias_problems`' docstring asserts
    that EVERY surface prescribing a fold can say the fold was silently
    dropped — for a refused BLOCK as well as a refused ENTRY, even though a
    malformed block loads as an empty table, on which `_split_aliases`
    returns `({}, [])` early."""
    root = _repo(tmp_path,
                 tags_yaml="tags:\n  fail-open: x\n" + _CEILING
                 + "aliases:\n  - ripe: fail-open\n",
                 shard={"sha": HEAD, "source": "attest",
                        "records": [_record(i) for i in range(3)]})
    problems = tags_mod.alias_problems(root)
    assert problems and any("aliases" in p and "must be a mapping" in p
                            for p in problems), problems


@pytest.mark.parametrize("block", [
    "", "tags: {}\n", "tags:\n", "aliases: {}\n", "aliases:\n",
    "tags:\n  fail-open: errs toward passing\n",
], ids=["absent", "empty-map", "null-tags", "empty-aliases", "null-aliases",
        "declared"])
def test_an_absent_or_empty_block_is_not_a_complaint(tmp_path, block):
    """The opposite error, and the line the refusal draws. A `tags:` key with
    no entries DECLARES nothing, and `{}` is exactly what it says — nothing is
    silently dropped, so nothing is refused. Only a block carrying content the
    loader would throw away (a list, a scalar) is a refusal."""
    root = _repo(tmp_path, tags_yaml=block + _CEILING, shard={
        "sha": HEAD, "source": "attest", "records": []})
    assert tags_mod.block_problems(root) == ()
    assert tags_mod.ceiling_status(root, [])["complaints"] == []


def test_the_repos_own_vocabulary_files_still_load():
    """The opposite error, against the REAL declarations rather than
    fixtures: this repo's `.warden/memory/tags.yaml` and the hello-svc
    portability fixture consumers copy from must both stay complaint-free."""
    for root in (REPO, REPO / "examples" / "hello-svc"):
        assert tags_mod.block_problems(root) == (), root
        assert tags_mod.load_vocab(root), root


# ── undecodable shards and non-object artifacts ───────────────────────────

def test_crew1_a_shard_that_is_not_utf8_never_crashes_certify(tmp_path):
    """Named regression. `except (OSError, json.JSONDecodeError)` misses the
    UnicodeDecodeError a non-UTF-8 shard raises, in E-02 and in
    `review_events` alike. `certify.run` evaluates every rung regardless of
    earlier failures and E-04 calls `review_events`, so a reader still holding
    that tuple takes `warden certify` down with a traceback on the exact shard
    E-02 names: every reader goes through `read_shard`, not half of them."""
    root = _repo(tmp_path, shard={"sha": HEAD, "source": "attest",
                                  "records": [_record()]})
    # sorts FIRST, so E-02 meets it before anything else can fail
    bad = root / ".warden" / "memory" / "attest" / "20260101T000000Z-cc-dd.json"
    bad.write_bytes(b'{"records": [{"finding": "caf\xe9"}]}')
    assert memory_mod.review_events(root)["unreadable"] == 1
    with pytest.raises(ValueError, match=bad.name):
        memory_mod.records_from_shards(root)
    ok, detail = certify_mod._run_check(
        {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}, root)
    assert not ok and bad.name in detail, detail
    # E-04 is the rung that CALLS review_events, and `certify.run` evaluates
    # every rung regardless of earlier failures — so this is the one a
    # UnicodeDecodeError would take all the way out of the command.
    ok, detail = certify_mod._run_check(
        {"id": "E-04", "label": "l", "type": "corpus_min_reviews", "min": 1},
        root)
    assert "1 unreadable file(s)" in detail, detail
    doc = certify_mod.run(root)   # a VERDICT, never a traceback
    assert doc["attained_level"] is not None


def test_crew1_a_malformed_artifact_never_leaves_a_half_rebuilt_store(tmp_path):
    """Named regression. The ingest sweep MUTATES, and the line above its
    parse block promises that a malformed artifact is skipped loudly with the
    store and cache left consistent. An except tuple naming
    `json.JSONDecodeError` rather than `ValueError`, and no `AttributeError`,
    lets an artifact whose top level is an array or a scalar die on
    `doc.get("findings", [])` AFTER an earlier artifact has been sharded and
    marked consumed; the cache is never rebuilt, and `load_records` reads a
    missing cache as an EMPTY corpus: `memory recall` and `memory stats` then
    report nothing over a store holding committed evidence."""
    # One shard already COMMITTED — the evidence a crash would hide.
    root = _repo(tmp_path, shard={"sha": HEAD, "source": "attest",
                                  "verdict": "clean",
                                  "records": [_record()]})
    for stamp, body in (("20260901T000000Z", json.dumps(["not an object"])),
                        ("20260902T000000Z", json.dumps("a scalar"))):
        run = root / ".warden" / "out" / f"{stamp}-attest"
        run.mkdir(parents=True)
        (run / "attestation.json").write_text(body)
    # a third, unreadable as bytes rather than as JSON
    run = root / ".warden" / "out" / "20260903T000000Z-attest"
    run.mkdir(parents=True)
    (run / "attestation.json").write_bytes(b'{"findings": [], "x": "caf\xe9"}')
    result = memory_mod.ingest(root, rules_dir=root / ".warden" / "rules")
    assert len(result["skipped"]) == 3, result["skipped"]
    assert result["new_shards"] == []
    # the committed evidence is still readable back through the CACHE — a
    # crash would leave it absent, and `load_records` reads a missing cache as
    # an empty corpus with no complaint at all.
    assert len(memory_mod.load_records(root)) == 1
    assert len(memory_mod.records_from_shards(root)) == 1


# ── the reader in the failure path, and the wrong subject ─────────────────

def test_r2_a_non_utf8_shard_sorting_LAST_still_never_crashes_certify(tmp_path):
    """Named regression. `warden/certify.py` holds TWO shard readers.
    `_shadow_remedy` runs from inside E-02's own `except AttestError` handler
    and sweeps EVERY shard rather than stopping where the caller stopped, so a
    hand-rolled read there under `except (OSError, json.JSONDecodeError)`
    lets a non-UTF-8 shard sorting AFTER the one that raised take the whole
    command down with a traceback: no verdict, no artifact.

    The sibling `test_crew1_...never_crashes_certify` cannot catch this and
    the reason is the point: it names its bad shard so it sorts FIRST, which
    means E-02 refuses it before the remedy path can run. A guard narrower
    than its own claim is the class this file exists to end, so the order
    is pinned here explicitly rather than left to a filename."""
    root = _repo(tmp_path)
    (root / ".warden" / "rules" / "rule-a.md").write_text(
        "---\nid: rule-a\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\ncovers: [foo]\n---\nfixture rule\n')
    shards = root / ".warden" / "memory" / "attest"
    # sorts FIRST: resolves to a covered slug, so `_check_rule_ids` raises and
    # the AttestError handler calls `_shadow_remedy` over every shard
    (shards / "20260101T000000Z-aa-bb.json").write_text(json.dumps(
        {"sha": HEAD, "source": "attest",
         "records": [_record(rule_id="unmapped:foo")]}))
    # sorts LAST, and is not UTF-8
    late = shards / "20260202T000000Z-cc-dd.json"
    late.write_bytes(b'{"sha": "' + b"2" * 40 + b'", "source": "attest", '
                     b'"records": [{"finding": "caf\xe9"}]}')
    ok, detail = certify_mod._run_check(
        {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}, root)
    assert not ok, detail
    doc = certify_mod.run(root)   # a VERDICT, never a traceback
    assert doc["attained_level"] is not None


def test_r2_an_unreadable_ruleset_is_not_reported_as_a_malformed_artifact(
        tmp_path):
    """Named regression. The artifact read's try block catches `OSError` so a
    non-UTF-8 or non-object artifact is skipped rather than aborting this
    mutating sweep — but if that block also wrapped `_check_rule_ids` and
    `build_records`, which read the RULESET, a `chmod 000` rule file would
    stop being fatal and be reported as a malformed ARTIFACT, naming the run
    dir (the one thing that is fine), while `memory ingest` exits 0 — the code
    the cage reads as "the corpus rebuilt". A check that could not look is not
    a check that passed, and it must not blame the wrong subject either.

    The artifact read keeps its widened tuple; the ruleset failure stays
    FATAL, which is the property this test pins.

    The failure's currency is an `AttestError` NAMING THE RULESET, raised from
    a probe that runs BEFORE the sweep: `cli.main` prints `warden: <message>`
    and exits 2 instead of dumping a traceback, and nothing is half-written —
    an artifact sharded earlier in the same run cannot leave the store written
    and the cache below never rebuilt (and `load_records` reads a missing
    cache as an EMPTY corpus). The assertion below therefore names the RULE
    FILE and refuses to see the run dir, which is the whole claim: the ruleset
    failed and the artifact must not wear the blame."""
    import os
    root = _repo(tmp_path)
    rule = root / ".warden" / "rules" / "rule-a.md"
    rule.write_text("---\nid: rule-a\nseverity: MEDIUM\nengine: claude\n"
                    'applies_to: ["**"]\n---\nfixture rule\n')
    run = root / ".warden" / "out" / "20260901T000000Z-attest"
    run.mkdir(parents=True)
    (run / "attestation.json").write_text(json.dumps({
        "head_sha": HEAD, "base_sha": "b" * 40, "rules_version": "v1",
        "reviewed_at": "2026-09-01T10:00:00+00:00",
        "reviewers": [{"role": "code-reviewer", "agent": "x"},
                      {"role": "cross-examiner", "agent": "x"}],
        "verdict": "findings-open",
        "findings": [{"rule_id": "rule-a", "severity": "MEDIUM",
                      "file": "app/x.py", "line": 1, "finding": "f",
                      "evidence": "e", "status": "confirmed"}]}))
    os.chmod(rule, 0o000)
    try:
        if os.access(rule, os.R_OK):        # running as root: the chmod is inert
            pytest.skip("cannot make a file unreadable as this user")
        with pytest.raises(memory_mod.AttestError) as raised:
            memory_mod.ingest(root, rules_dir=root / ".warden" / "rules")
        assert "rule-a.md" in str(raised.value), raised.value
        assert "20260901T000000Z-attest" not in str(raised.value), (
            "the RULESET failed; the run dir is the one thing that is fine")
    finally:
        os.chmod(rule, 0o644)


@pytest.mark.parametrize("body, expect, why", [
    (["not an object"], "not a review artifact", "a top-level array"),
    ("a scalar", "not a review artifact", "a top-level scalar"),
    ({"findings": "not a list"}, "not a review artifact",
     "findings that is not a list"),
    ({"findings": ["not a mapping"]}, "finding 0 is not a mapping",
     "a findings LIST holding a non-mapping"),
    ({"findings": [{"rule_id": 7}]}, "`rule_id` that is not a string",
     "a finding whose rule_id is not a string"),
], ids=["array", "scalar", "findings-not-a-list", "element-not-a-mapping",
        "rule-id-not-a-string"])
def test_r3_a_malformed_artifact_shape_is_skipped_by_name(tmp_path, body,
                                                          expect, why):
    """Named regression. A two-`try` split that checks the artifact's TOP
    LEVEL only, with the second tuple narrowed to `(json.JSONDecodeError,
    KeyError, TypeError)`, lets a `findings` LIST holding a non-mapping, or a
    finding whose `rule_id` is an int, go straight past the check and die as
    an `AttributeError` inside `_resolve_covered_legacy_ids`. That aborts a
    MUTATING sweep: the committed shard below stays on disk, the cache is
    never rebuilt, and `load_records` reads a missing cache as an EMPTY
    corpus.

    These are the same shapes `validate_shard_records` refuses on the shard
    side, so accepting them would also be a parity hole at the one seam that
    claims to refuse at the crossing."""
    # a shard already COMMITTED, so the half-rebuilt-store harm is visible
    root = _repo(tmp_path, shard={"sha": HEAD, "source": "attest",
                                  "verdict": "clean",
                                  "records": [_record()]})
    run = root / ".warden" / "out" / "20260901T000000Z-attest"
    run.mkdir(parents=True)
    (run / "attestation.json").write_text(json.dumps(body))
    result = memory_mod.ingest(root, rules_dir=root / ".warden" / "rules")
    assert len(result["skipped"]) == 1, (why, result["skipped"])
    assert expect in result["skipped"][0], (why, result["skipped"])
    assert result["new_shards"] == [], why
    assert len(memory_mod.load_records(root)) == 1, (
        why, "the cache must still be rebuilt from the committed shard")


# ── the field one over from `status` ──────────────────────────────────────

def _poisonable_repo(tmp_path, tags):
    """A repo whose one attest artifact carries `tags` verbatim."""
    root = _repo(tmp_path)
    (root / ".warden" / "rules" / "rule-a.md").write_text(
        "---\nid: rule-a\nseverity: MEDIUM\nengine: claude\n"
        'applies_to: ["**"]\n---\nfixture rule\n')
    finding = {"rule_id": "rule-a", "severity": "MEDIUM", "file": "app/x.py",
               "line": 1, "finding": "f", "evidence": "e",
               "status": "confirmed"}
    if tags is not _ABSENT:
        finding["tags"] = tags
    # A REACHABLE head, so the opposite-error half of this pair actually
    # reaches the shard write instead of stopping at the reachability guard —
    # a fixture that cannot shard would make "still accepted" unfalsifiable.
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "fixture"],
                   cwd=root, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()
    run = root / ".warden" / "out" / "20260901T000000Z-attest"
    run.mkdir(parents=True)
    (run / "attestation.json").write_text(json.dumps({
        "head_sha": head, "base_sha": "b" * 40, "rules_version": "v1",
        "reviewed_at": "2026-09-01T10:00:00+00:00",
        "reviewers": [{"role": "code-reviewer", "agent": "x"},
                      {"role": "cross-examiner", "agent": "x"}],
        "verdict": "findings-open", "findings": [finding]}))
    return root


_ABSENT = object()


@pytest.mark.parametrize("tags", ["flaky", {"a": 1}, [1, 2], [None]],
                         ids=["string", "mapping", "ints", "null-element"])
def test_final_a_non_list_tags_never_becomes_five_fabricated_names(tmp_path,
                                                                   tags):
    """Named regression. A seam that refuses a non-string `status` must not
    MANGLE a non-list `tags` in the same breath — the neighbouring field of
    the same schema, which declares both.

    Ordering is the mechanism, and it is why the guard has to live in
    `_artifact_shape_problem` rather than at the crossing: `build_records`
    does `sorted(f.get("tags", []))`, so the STRING "flaky" becomes the
    perfectly valid list ['a','f','k','l','y'] BEFORE `validate_shard_records`
    can look. The shard-side tags guard is structurally unable to fire, the
    shard is filed with `skipped` empty, and five fabricated names enter the
    committed corpus and the vocabulary ceiling — a corpus poisoned by
    exactly the route the `status` guard exists to close."""
    root = _poisonable_repo(tmp_path, tags)
    result = memory_mod.ingest(root, rules_dir=root / ".warden" / "rules")
    assert result["new_shards"] == [], (tags, "the artifact must not shard")
    assert any("tags" in s for s in result["skipped"]), result["skipped"]
    assert not list((root / ".warden" / "memory" / "attest").glob("*.json"))
    from warden import tags as tags_mod2
    assert tags_mod2.undecided_tags(
        root, memory_mod.records_from_shards(root)) == [], (
        "no fabricated name may reach the vocabulary ceiling")


@pytest.mark.parametrize("tags", [["flaky"], [], _ABSENT],
                         ids=["a-real-tag", "empty-list", "absent"])
def test_final_the_tags_a_producer_writes_are_still_accepted(tmp_path, tags):
    """The opposite error. A list of strings, an empty list, and NO tags key
    at all are all shapes the producer writes — `build_records` defaults an
    absent one to `[]` — and the guard must refuse a SHAPE, never a
    vocabulary."""
    root = _poisonable_repo(tmp_path, tags)
    result = memory_mod.ingest(root, rules_dir=root / ".warden" / "rules")
    assert result["skipped"] == [], (tags, result["skipped"])
    assert len(result["new_shards"]) == 1, (tags, result)
    assert len(memory_mod.records_from_shards(root)) == 1


@needs_corpus
def test_final_the_committed_corpus_still_carries_only_well_formed_tags():
    """The opposite error against the REAL corpus rather than fixtures: every
    record this repo has committed must still pass the shape the artifact seam
    now demands, or the guard would refuse evidence the store legitimately
    holds."""
    shards = sorted((REPO / ".warden" / "memory" / "attest").glob("*.json"))
    assert len(shards) > 100, "the corpus should be the real one, not a stub"
    for shard in shards:
        for i, r in enumerate(json.loads(shard.read_text())["records"]):
            tags = r.get("tags")
            assert tags is None or (
                isinstance(tags, list)
                and all(isinstance(t, str) for t in tags)), (shard.name, i)


# ── the envelope's own hash-keyed field, and the close ────────────────────

@pytest.mark.parametrize("verdict", [["clean"], {"v": "clean"}, 3],
                         ids=["list", "mapping", "int"])
def test_final2_a_verdict_that_is_not_a_string_is_refused_by_every_reader(
        tmp_path, verdict):
    """Named regression. `verdict` is the one ENVELOPE key a reader
    dereferences by HASH — `review_events` does
    `_VERDICT_BUCKETS.get(doc.get("verdict", ""))` — and that line sits
    OUTSIDE the try whose `unreadable` bucket exists for exactly this. Untyped,
    `"verdict": ["clean"]` raises `TypeError: unhashable type` past
    `certify.run`'s bare `_run_check` loop and `cli._cmd_certify`'s
    `except CertifyError`, out through the blanket handler: traceback, exit 2,
    no verdict, no certification.json — while `records_from_shards` accepts
    the same shard. The same shape as `status` one surface up, typed in the
    same shared definition rather than inside any one reader."""
    _all_four_readers_refuse(_repo(
        tmp_path, shard={"sha": HEAD, "source": "attest", "verdict": verdict,
                         "records": [_record()]}))


@pytest.mark.parametrize("envelope", [
    {"verdict": "clean"}, {"verdict": "findings-open"}, {},
], ids=["clean", "findings-open", "absent"])
def test_final2_the_verdicts_the_corpus_holds_are_still_accepted(tmp_path,
                                                                 envelope):
    """The opposite error, and the line drawn from MEASUREMENT rather than
    taste: 9 of this repo's 150 committed shards carry NO `verdict` at all
    (they predate the field), and `review_events` buckets those as
    `verdict_unrecorded` on purpose. Absent stays legal; only a PRESENT one of
    the wrong type is a refusal."""
    root = _repo(tmp_path, shard={"sha": HEAD, "source": "attest",
                                  "records": [_record()], **envelope})
    assert len(memory_mod.records_from_shards(root)) == 1
    assert memory_mod.review_events(root)["unreadable"] == 0


def test_final2_every_envelope_key_a_reader_dereferences_is_typed():
    """The shard ENVELOPE's dereferenced keys, and where each one is typed.

    WHAT THIS PROVES, and it is narrower than it may look. It proves that the
    envelope keys read by the four reader functions listed below are each
    either typed inline by their reader or typed by `validate_shard_envelope`,
    and it fails if one of those four grows a new one. That is worth having:
    it is what catches the next `verdict`.

    WHAT IT DOES NOT PROVE, written down because a claim that the surface is
    shut would be false. It scans SOURCE TEXT for a hand-typed roster of
    functions and a regex bound to the local name `doc`, so both of its axes
    are maintained by a human and it goes blind exactly where a human
    forgets. Concretely, today: it does not see `certify._run_check`'s E-02
    arm, which is an envelope reader; it cannot see RECORD fields at all —
    those are typed by `memory.RECORD_FIELD_CONTRACT`, not checked here; and
    it says nothing about multi-record interactions, right-type/wrong-format
    values, or callers that use warden as a library.

    A closure claim over this class needs a matrix whose axes are DERIVED
    from the code — `build_records`' record literal and the CLI's subparser
    table — asserting that no command ever reaches stderr with a traceback.
    That matrix is `tests/test_malformation_matrix.py`. This test covers one
    surface of the class, and saying so is the difference between a bound
    and a boast.
    """
    import re
    typed_in_the_shared_validator = {"records", "verdict"}
    typed_inline_by_their_reader = {
        # memory.review_events + attest.attested_shas both guard with
        # `isinstance(sha, str) and sha`
        "sha",
        # compared with != / == only, so no type can raise
        "source", "ts", "base_sha", "head_sha", "rules_version",
        "reviewed_at", "legacy_rule_id",
        # attest.py: `isinstance(ids, list) and ids and all(isinstance(i, str)...)`
        "range_patch_ids",
    }
    known = typed_in_the_shared_validator | typed_inline_by_their_reader
    def body_of(module: str, name: str) -> str:
        """One function's source, `def name(` to the next top-level `def`."""
        src = (REPO / "warden" / module).read_text()
        start = src.index(f"def {name}(")
        rest = src[start:]
        nxt = re.search(r"\n(?:def |@)", rest[1:])
        return rest[: nxt.start() + 1] if nxt else rest

    # The functions that read a shard ENVELOPE off a parsed `doc`. Scoped by
    # name rather than "everything after review_events", because that wider
    # slice sweeps up `stats`' render helpers — a scan that reports keys no
    # shard reader touches is a scan nobody will keep passing.
    readers = [("memory.py", "review_events"),
               ("memory.py", "validate_shard_envelope"),
               ("memory.py", "_event_already_filed"),
               ("certify.py", "_shadow_remedy")]
    seen: set[str] = set()
    for module, name in readers:
        seen |= set(re.findall(r'doc(?:\.get\(|\[)"([a-z_]+)"',
                               body_of(module, name)))
    assert len(seen) >= 4, (
        "the scan found almost no envelope dereference — it has gone blind, "
        f"which is the failure mode it exists to prevent (saw {sorted(seen)})")
    assert seen <= known, (
        f"a shard reader dereferences envelope key(s) {sorted(seen - known)} "
        f"that nothing types. Either type it in validate_shard_envelope (if "
        f"any reader hashes or indexes it) or add it above with the guard "
        f"that makes it safe — do not widen this set without one.")
    # and the two the shared validator owns really are asserted there — so
    # "typed in the shared validator" cannot become a claim the table makes
    # about a clause nobody wrote.
    envelope_src = body_of("memory.py", "validate_shard_envelope")
    for key in typed_in_the_shared_validator:
        assert f'"{key}"' in envelope_src, (
            f"the table says `{key}` is typed by validate_shard_envelope, "
            f"and it is not")


# ── the widest consumer-visible refusal, pinned ───────────────────────────

def test_terminal_an_absent_status_is_actually_refused(tmp_path):
    """Named regression. `Adopting.md` calls the ABSENT-`status` refusal "the
    widest change here" and tells a consumer it is worth Level 4 -> Level 3 on
    an unchanged tree, so it has to be pinned.

    THE MUTATION THAT ISOLATES IT is `"status" in record and not
    isinstance(...)`, which leaves the rest of the suite green and reddens
    only this case. `status is not None and not isinstance(...)` does NOT
    isolate it: that spelling also reddens
    `test_a_status_that_is_not_a_string_is_refused_by_every_reader[null]`,
    because a null `status` and
    an absent one are the same value to `.get`. A mutation recipe that
    reddens a sibling proves the sibling bites, not this one.

    Only `status` is here. The absent-`id` refusal is already pinned by
    `test_ingest_names_the_shard_whose_record_carries_no_id`, and a second
    copy of it would be coverage this file only appears to have."""
    rec = _record()
    rec.pop("status")
    root = _repo(tmp_path, shard={"sha": HEAD, "source": "attest",
                                  "verdict": "clean", "records": [rec]})
    with pytest.raises(memory_mod.AttestError, match=SHARD) as excinfo:
        memory_mod.ingest(root, rules_dir=root / ".warden" / "rules")
    assert "status" in str(excinfo.value), excinfo.value
    # every reader, not just ingest — that is what makes it the wide one
    with pytest.raises(ValueError, match=SHARD):
        memory_mod.records_from_shards(root)
    assert memory_mod.review_events(root)["unreadable"] == 1
    ok, detail = certify_mod._run_check(
        {"id": "E-02", "label": "l", "type": "attest_rule_ids_resolve"}, root)
    assert not ok and SHARD in detail, detail
