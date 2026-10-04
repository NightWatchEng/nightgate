"""Attestation: schema validation, verdict consistency, provenance stamping,
rule_id resolution against the declared ruleset, and the CI requirement that
a PR carry one at all."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from warden import attest as attest_mod

HEAD, BASE = "b" * 40, "a" * 40
REVIEWERS = [{"role": "code-reviewer", "agent": "claude-code subagent"},
             {"role": "cross-examiner", "agent": "claude-code subagent"}]
FINDING = {"rule_id": "scope-creep", "severity": "MEDIUM", "file": "app/X.swift",
           "line": 4, "finding": "new surface", "evidence": "code",
           "status": "confirmed"}


@pytest.fixture
def rules_dir(sample_repo) -> Path:
    return sample_repo / ".warden" / "rules"


@pytest.fixture
def build(rules_dir):
    def _build(payload: dict, *, rules: Path | None = None,
               review_dir: Path | None = None,
               changed: tuple[str, ...] | None = None) -> dict:
        return attest_mod.build(payload, head_sha=HEAD, base_sha=BASE,
                                rules_version="v123",
                                rules_dir=rules or rules_dir,
                                review_dir=review_dir, changed=changed)
    return _build


def test_clean_attestation_stamped(build):
    doc = build({"reviewers": REVIEWERS, "findings": [], "verdict": "clean"})
    assert doc["head_sha"] == HEAD
    assert doc["rules_version"] == "v123"
    assert doc["reviewed_at"]
    assert "CLEAN" in attest_mod.render_summary(doc)


def test_clean_with_confirmed_finding_rejected(build):
    with pytest.raises(attest_mod.AttestError, match="still 'confirmed'"):
        build({"reviewers": REVIEWERS, "findings": [FINDING], "verdict": "clean"})


def test_clean_with_fixed_and_dismissed_findings_ok(build):
    findings = [dict(FINDING, status="fixed"),
                dict(FINDING, status="dismissed-with-reason", reason="test-only")]
    doc = build({"reviewers": REVIEWERS, "findings": findings, "verdict": "clean"})
    assert doc["verdict"] == "clean"


def test_findings_open_requires_confirmed(build):
    with pytest.raises(attest_mod.AttestError, match="no confirmed"):
        build({"reviewers": REVIEWERS, "findings": [], "verdict": "findings-open"})


def test_payload_cannot_forge_provenance(build):
    """The skill must not be able to claim it reviewed a different SHA."""
    with pytest.raises(attest_mod.AttestError, match="extra keys"):
        build({"reviewers": REVIEWERS, "findings": [], "verdict": "clean",
               "head_sha": "f" * 40})


def test_no_reviewers_rejected(build):
    with pytest.raises(attest_mod.AttestError, match="invalid"):
        build({"reviewers": [], "findings": [], "verdict": "clean"})


def test_bad_status_rejected(build):
    bad = dict(FINDING, status="ignored")
    with pytest.raises(attest_mod.AttestError, match="invalid"):
        build({"reviewers": REVIEWERS, "findings": [bad], "verdict": "clean"})


def test_g2_9_dismissed_without_reason_rejected(build):
    bad = dict(FINDING, status="dismissed-with-reason")
    with pytest.raises(attest_mod.AttestError, match="invalid"):
        build({"reviewers": REVIEWERS, "findings": [bad], "verdict": "clean"})


class TestRuleIdResolution:
    """A finding's rule_id must name a declared rule or be
    explicitly `unmapped:<slug>`. Without this the memory corpus accumulates
    ids no rule owns, and the promotion gate (N>=10 + Wilson LB) can never
    fire for a rule that exists."""

    def test_declared_rule_id_accepted(self, build):
        doc = build({"reviewers": REVIEWERS,
                     "findings": [dict(FINDING, status="fixed")],
                     "verdict": "clean"})
        assert doc["findings"][0]["rule_id"] == "scope-creep"

    def test_undeclared_rule_id_rejected(self, build):
        bad = dict(FINDING, rule_id="docs-drift", status="fixed")
        with pytest.raises(attest_mod.AttestError) as e:
            build({"reviewers": REVIEWERS, "findings": [bad], "verdict": "clean"})
        msg = str(e.value)
        assert "docs-drift" in msg and "names no declared rule" in msg
        # names the valid set (house style, cf. certify R-09) ...
        assert "scope-creep" in msg and "secrets-in-diff" in msg
        # ... and teaches the escape hatch instead of just refusing.
        assert "unmapped:" in msg

    def test_bare_general_rejected_regression(self, build):
        """A catch-all `general` (as in `rule_id (or "general")`) names no
        rule and is refused."""
        bad = dict(FINDING, rule_id="general", status="refuted",
                   reason="cross-examiner refuted it")
        with pytest.raises(attest_mod.AttestError, match="names no declared rule"):
            build({"reviewers": REVIEWERS, "findings": [bad], "verdict": "clean"})

    def test_unmapped_prefix_accepted(self, build):
        """A finding no rule covers is a first-class signal for the rule
        advisor, not a workaround — it must survive attestation intact."""
        f = dict(FINDING, rule_id="unmapped:docs-drift", status="fixed")
        doc = build({"reviewers": REVIEWERS, "findings": [f], "verdict": "clean"})
        assert doc["findings"][0]["rule_id"] == "unmapped:docs-drift"

    def test_unmapped_slug_must_be_well_formed(self, build):
        """`unmapped:` groups candidate rules — free text inside it would
        fragment the very signal the prefix exists to collect."""
        for rid in ("unmapped:", "unmapped:Docs Drift", "unmapped:docs_drift",
                    "unmapped:-docs", "unmapped:unmapped:x"):
            bad = dict(FINDING, rule_id=rid, status="fixed")
            with pytest.raises(attest_mod.AttestError, match="unmapped"):
                build({"reviewers": REVIEWERS, "findings": [bad],
                       "verdict": "clean"})

    def test_unmapped_slug_must_name_a_defect_class(self, build):
        """`unmapped:general` would slide the catch-all id the bare-`general`
        test refuses back in, one prefix away. A non-descriptive slug carries
        no signal for the rule advisor, so the reserved words are refused by
        name."""
        for rid in ("unmapped:general", "unmapped:other", "unmapped:misc",
                    "unmapped:unknown", "unmapped:todo", "unmapped:none"):
            bad = dict(FINDING, rule_id=rid, status="fixed")
            with pytest.raises(attest_mod.AttestError, match="defect class"):
                build({"reviewers": REVIEWERS, "findings": [bad],
                       "verdict": "clean"})

    def test_unmapped_may_not_shadow_a_declared_rule(self, build):
        """`unmapped:scope-creep` would launder a real rule's judgments out of
        that rule's precision numbers."""
        bad = dict(FINDING, rule_id="unmapped:scope-creep", status="fixed")
        with pytest.raises(attest_mod.AttestError) as e:
            build({"reviewers": REVIEWERS, "findings": [bad], "verdict": "clean"})
        assert "declared rule" in str(e.value)

    def test_error_message_never_suggests_an_id_it_rejects(self, build):
        """An undeclared-id message proposing 'unmapped:' + slugify(id) would
        hand back, for 'general', 'todo' or '???', a reserved non-descriptive
        slug, and for a near-miss of a real rule the shadow form — an id the
        same function refuses. Any id a message proposes must itself be
        accepted."""
        for rid in ("general", "TODO", "???", "n/a", "docs drift",
                    "Scope-Creep", "docs-drift"):
            bad = dict(FINDING, rule_id=rid, status="fixed")
            with pytest.raises(attest_mod.AttestError) as e:
                build({"reviewers": REVIEWERS, "findings": [bad],
                       "verdict": "clean"})
            for suggested in re.findall(r"'(unmapped:[a-z0-9-]+)'", str(e.value)):
                doc = build({"reviewers": REVIEWERS,
                             "findings": [dict(FINDING, rule_id=suggested,
                                               status="fixed")],
                             "verdict": "clean"})
                assert doc["findings"][0]["rule_id"] == suggested

    def test_near_miss_of_a_declared_rule_names_that_rule(self, build):
        """A case/separator variant of a real id is a typo, not a new class."""
        bad = dict(FINDING, rule_id="Scope_Creep", status="fixed")
        with pytest.raises(attest_mod.AttestError, match="did you mean"):
            build({"reviewers": REVIEWERS, "findings": [bad], "verdict": "clean"})

    def test_every_finding_is_checked_not_just_the_first(self, build):
        good = dict(FINDING, status="fixed")
        bad = dict(FINDING, rule_id="graph-fidelity", status="fixed")
        with pytest.raises(attest_mod.AttestError, match="graph-fidelity"):
            build({"reviewers": REVIEWERS, "findings": [good, bad],
                   "verdict": "clean"})

    def test_paused_rule_id_still_resolves(self, tmp_path, build):
        """A paused rule keeps its id and its history — attesting against it
        must not become an error the moment the founder pauses it."""
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "dormant.md").write_text(
            "---\nid: dormant-rule\nseverity: LOW\nengine: claude\n"
            "applies_to: [\"**\"]\npaused: true\n"
            "paused_reason: three straight refutations\n---\nBody.\n")
        f = dict(FINDING, rule_id="dormant-rule", status="fixed")
        doc = build({"reviewers": REVIEWERS, "findings": [f],
                     "verdict": "clean"}, rules=rules)
        assert doc["findings"][0]["rule_id"] == "dormant-rule"

    @staticmethod
    def _covers_rules(tmp_path, *, paused: bool) -> Path:
        rules = tmp_path / "rules"
        rules.mkdir()
        pause = ("paused: true\npaused_reason: refutation streak\n"
                 if paused else "")
        (rules / "wiki-fidelity.md").write_text(
            "---\nid: wiki-fidelity\nseverity: MEDIUM\nengine: claude\n"
            f'applies_to: ["**"]\ncovers: [docs-drift]\n{pause}---\nbody\n')
        return rules

    def test_covered_slug_accepted_while_covering_rule_paused(
            self, tmp_path, build):
        """A paused rule enforces nothing, so `memory stats` re-opens its class
        as a candidate. Routing the slug's findings onto the paused rule — a
        rule review.py skips and stats quarantines — would leave the re-opened
        candidate unable to accrue evidence under its own slug. While the
        covering rule is paused, the slug is a live candidate key and the
        writer accepts it."""
        rules = self._covers_rules(tmp_path, paused=True)
        f = dict(FINDING, rule_id="unmapped:docs-drift", status="fixed")
        doc = build({"reviewers": REVIEWERS, "findings": [f],
                     "verdict": "clean"}, rules=rules)
        assert doc["findings"][0]["rule_id"] == "unmapped:docs-drift"

    def test_covered_slug_still_refused_while_rule_unpaused(
            self, tmp_path, build):
        """The unpaused half of the same seam, pinned so the pause filter
        cannot drift into 'covers: never refuses'."""
        rules = self._covers_rules(tmp_path, paused=False)
        f = dict(FINDING, rule_id="unmapped:docs-drift", status="fixed")
        with pytest.raises(attest_mod.AttestError, match="already covers"):
            build({"reviewers": REVIEWERS, "findings": [f],
                   "verdict": "clean"}, rules=rules)

    def test_unmapped_paused_rule_id_still_refused(self, tmp_path, build):
        """A pause suspends enforcement, never identity: the rule's own id
        stays taken (a paused rule keeps its id and its history), so hiding
        it behind the unmapped: prefix is refused paused or not."""
        rules = self._covers_rules(tmp_path, paused=True)
        f = dict(FINDING, rule_id="unmapped:wiki-fidelity", status="fixed")
        with pytest.raises(attest_mod.AttestError, match="hides declared rule"):
            build({"reviewers": REVIEWERS, "findings": [f],
                   "verdict": "clean"}, rules=rules)

    def test_rule_id_checked_before_verdict_consistency(self, build):
        """Ordering matters for the message the skill sees: an unresolvable
        rule_id is the actionable error, not the verdict it rides on."""
        bad = dict(FINDING, rule_id="docs-drift")  # status confirmed + clean
        with pytest.raises(attest_mod.AttestError, match="names no declared rule"):
            build({"reviewers": REVIEWERS, "findings": [bad], "verdict": "clean"})


class TestAttestCheck:
    """CI asserts a PR carries an attestation that reached the
    committed corpus. The whole point is corpus growth, so the check reads the
    shard `memory ingest` commits — never the gitignored run dir."""

    @staticmethod
    def _git(root, *args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    @classmethod
    def _commit(cls, root, name, body="x"):
        (root / name).write_text(body)
        cls._git(root, "add", name)
        cls._git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", f"add {name}")
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()

    @classmethod
    def _commit_shard(cls, root, message="ops(memory): commit the shard"):
        """The ingest commit, faithfully: the shard and NOTHING else.

        What a real one carries — `attest write`'s artifact is gitignored, so
        `memory ingest` stages exactly one new file. Committing a marker file
        beside it, as this fixture used to, is a content change landing after
        the round that attested the branch, which the coverage rule
        refuses.
        """
        cls._git(root, "add", "-A")
        cls._git(root, "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", message)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()

    @classmethod
    def _shard(cls, root, sha, *, name="shard.json", source="attest", body=None):
        shard_dir = root / attest_mod.SHARD_DIR
        shard_dir.mkdir(parents=True, exist_ok=True)
        path = shard_dir / name
        path.write_text(body if body is not None else json.dumps(
            {"schema": 1, "source": source, "sha": sha, "base_sha": "0" * 40,
             "rules_version": "v1", "reviewed_at": "2026-08-23T00:00:00+00:00",
             "records": []}))
        return path

    @pytest.fixture
    def repo(self, tmp_path):
        """main with one commit, then a branch of two — the second is the
        `memory ingest` commit that lands the shard, exactly as this repo's
        own history looks."""
        root = tmp_path / "repo"
        root.mkdir()
        self._git(root, "init", "-q", "-b", "main")
        base = self._commit(root, "README.md")
        self._git(root, "switch", "-q", "-c", "feature")
        reviewed = self._commit(root, "feature.py")
        return root, base, reviewed

    def test_attestation_on_a_branch_commit_passes(self, repo):
        """The load-bearing case: the shard names the REVIEWED commit, and the
        ingest commit that lands the shard is what head points at. Requiring
        shard.sha == head could never pass."""
        root, base, reviewed = repo
        self._shard(root, reviewed)
        head = self._commit_shard(root)   # the ingest commit
        assert head != reviewed
        doc = attest_mod.check_range(root, base="main", head=head)
        assert [m["sha"] for m in doc["attested"]] == [reviewed]
        assert "PASS" in attest_mod.render_check(doc)

    def test_no_attestation_is_a_finding_not_a_crash(self, repo):
        root, base, reviewed = repo
        doc = attest_mod.check_range(root, base="main", head=reviewed)
        assert doc["attested"] == []
        assert "NO ATTESTATION" in attest_mod.render_check(doc)

    def test_attestation_already_on_the_base_branch_does_not_count(self, repo):
        """A branch that merges main must not inherit main's attestations:
        anchoring on the base BRANCH TIP puts them outside the range."""
        root, base, reviewed = repo
        self._shard(root, base)          # attests a commit already on main
        self._git(root, "add", "-A")
        head = self._commit(root, "ingested.txt")
        doc = attest_mod.check_range(root, base="main", head=head)
        assert doc["attested"] == []
        assert doc["shard_count"] == 1   # the shard was read, just out of range

    def test_gate_shard_does_not_satisfy_the_requirement(self, repo):
        """A gate shard is a deterministic checker firing — a fact, not the
        orchestrated judgment the requirement is about."""
        root, base, reviewed = repo
        self._shard(root, reviewed, source="gate")
        self._git(root, "add", "-A")
        head = self._commit(root, "ingested.txt")
        doc = attest_mod.check_range(root, base="main", head=head)
        assert doc["attested"] == []

    def test_unreadable_shard_is_indeterminate_not_absent(self, repo):
        """Fail closed: "cannot read the store" must never be reported as
        "no attestation" — the exit-code contract keeps 2 and 1 apart."""
        root, base, reviewed = repo
        self._shard(root, reviewed, name="broken.json", body="{ not json")
        self._git(root, "add", "-A")
        head = self._commit(root, "ingested.txt")
        with pytest.raises(attest_mod.AttestError, match="cannot tell"):
            attest_mod.check_range(root, base="main", head=head)

    def test_unreadable_shard_does_not_mask_a_real_match(self, repo):
        """Indeterminacy only matters when nothing matched; a real
        attestation still answers the question."""
        root, base, reviewed = repo
        self._shard(root, reviewed, name="broken.json", body="{ not json")
        self._shard(root, reviewed, name="good.json")
        self._git(root, "add", "-A")
        head = self._commit(root, "ingested.txt")
        doc = attest_mod.check_range(root, base="main", head=head)
        assert [m["sha"] for m in doc["attested"]] == [reviewed]
        assert doc["unreadable"] == [
            (attest_mod.SHARD_DIR / "broken.json").as_posix()]
        assert "WARNING" in attest_mod.render_check(doc)

    @pytest.mark.parametrize("body, why", [
        ('["a record"]', "a top-level JSON array"),
        ('{"source": "attest", "sha": "%s", "records": {"no": "list"}}',
         "a records key that is not a list"),
        ('{"source": "attest", "sha": "%s"}', "no records key at all"),
        ('{"source": "attest", "sha": "%s", "verdict": ["clean"],'
         ' "records": []}', "a verdict that is not a string"),
        ('{"source": "attest", "sha": "%s", "records":'
         ' [{"id": "r", "rule_id": "x", "status": ["fixed"]}]}',
         "a record status that is not a string"),
    ], ids=["array", "records-not-a-list", "no-records-key",
            "verdict-not-a-string", "status-not-a-string"])
    def test_a_shard_every_other_reader_refuses_is_not_an_attestation(
            self, repo, body, why):
        """`attest check` reads a shard's records, not only its ENVELOPE.

        Every reader that COUNTS the store goes through `memory.read_shard` /
        `validate_shard_envelope`, and `attested_shas` — the step that reddens
        a consumer's PR — must too. A hand-roll that asserts the envelope
        carries a usable `sha` and a `source` and never looks at `records`
        lets each shape below SATISFY the blocking attestation gate and be
        refused one step later by `memory ingest`: existence checked,
        validity not.

        Widening what a blocking gate refuses is a consumer blast-radius
        change; `test_the_committed_corpus_of_this_repo_stays_readable` is
        the standing check that this repo's committed shards keep their
        verdict.
        """
        root, base, reviewed = repo
        if "%s" in body:
            body = body % reviewed
        self._shard(root, reviewed, name="broken.json", body=body)
        self._git(root, "add", "-A")
        head = self._commit(root, "ingested.txt")
        rel = (attest_mod.SHARD_DIR / "broken.json").as_posix()
        found, unreadable, _, _ = attest_mod.attested_shas(root, head)
        # …and the CAUSE travels with the path, so the one error that can block
        # a consumer's PR names the defect and not only the file.
        assert unreadable.get(rel), (
            f"a shard with {why} is reported unreadable with no cause — the "
            "validator's message named the defect and it was discarded")
        assert rel in unreadable, (
            f"a shard with {why} satisfies `attest check` and is refused one "
            "step later by `memory ingest` — the two readers of one store "
            f"disagree again: found={found}, unreadable={unreadable}")
        assert reviewed not in found, found
        # …and the gate says INDETERMINATE rather than attested: fail-closed,
        # with the cause in the message and the remedy that fits an
        # already-committed malformed shard.
        with pytest.raises(attest_mod.AttestError, match="cannot tell") as err:
            attest_mod.check_range(root, base="main", head=head)
        assert "broken.json (" in str(err.value), str(err.value)
        # …and the RENDERED warning carries it too, on a real doc: a good shard
        # beside the broken one makes the check answer instead of refusing, and
        # the WARNING line is what an author actually reads.
        self._shard(root, reviewed, name="good.json")
        self._git(root, "add", "-A")
        head = self._commit(root, "second.txt")
        rendered = attest_mod.render_check(
            attest_mod.check_range(root, base="main", head=head))
        assert "broken.json" in rendered and "memory ingest" in rendered, rendered
        assert rendered.count("WARNING: unreadable shard:") == 1, rendered
        assert "broken.json —" in rendered, (
            "the rendered warning names the shard but not the cause, so the "
            "one error that can block a consumer says only 'could not be "
            "read' about a file that parses fine: " + rendered)

    def test_a_non_utf8_shard_on_disk_does_not_escape_unwrapped(
            self, repo):
        """`_uncommitted_shards` must handle a shard that is not UTF-8: reading
        under `except (OSError, json.JSONDecodeError)` alone lets
        UnicodeDecodeError out of `read_text` unwrapped — a traceback naming a
        byte position and no file, from the diagnostic half of the blocking
        gate."""
        root, base, reviewed = repo
        head = self._commit(root, "ingested.txt")
        shard_dir = root / attest_mod.SHARD_DIR
        shard_dir.mkdir(parents=True, exist_ok=True)
        (shard_dir / "notutf8.json").write_bytes(b'{"sha": "\xff\xfe"}')
        # Reported as NO ATTESTATION, never as a crash: an undecodable file
        # that is not committed carries no claim either way.
        doc = attest_mod.check_range(root, base="main", head=head)
        assert doc["attested"] == [] and doc["uncommitted"] == []

    def test_unresolvable_range_did_not_run(self, repo):
        root, base, reviewed = repo
        with pytest.raises(attest_mod.AttestError, match="did NOT run"):
            attest_mod.check_range(root, base="no-such-ref-xyz", head=reviewed)

    def test_missing_shard_dir_is_no_attestation_not_a_crash(self, repo):
        root, base, reviewed = repo
        found, unreadable, _, verdicts = attest_mod.attested_shas(root, "HEAD")
        assert (found, unreadable, verdicts) == ({}, {}, {})

    def test_untracked_shard_fails_the_check(self, repo):
        """A shard that exists ONLY on disk must not satisfy the check:
        printing 'carries a committed attestation' for a file `git ls-files`
        does not list is a local PASS followed by a CI failure. 'Committed'
        must mean committed."""
        root, base, reviewed = repo
        shard = self._shard(root, reviewed)          # written, never committed
        head = self._commit(root, "ingested.txt")    # shard NOT in this commit
        doc = attest_mod.check_range(root, base="main", head=head)
        assert doc["attested"] == [], "an untracked shard must never satisfy the check"
        out = attest_mod.render_check(doc)
        assert "NO ATTESTATION" in out
        # the single most useful diagnostic: the exact forgot-to-commit state
        rel = shard.relative_to(root).as_posix()
        assert rel in out and "git add" in out and "untracked" in out

    def test_staged_shard_fails_with_its_own_cause(self, repo):
        """A staged-but-uncommitted shard is a different state with a
        different remedy than an untracked one — commit it, or the checked
        head predates the shard's ingest commit. It must be named as such,
        not mislabelled untracked, which would name the wrong cause."""
        root, base, reviewed = repo
        head = self._commit(root, "ingested.txt")
        shard = self._shard(root, reviewed)
        self._git(root, "add", str(shard.relative_to(root)))  # staged, not in head
        doc = attest_mod.check_range(root, base="main", head=head)
        assert doc["attested"] == []
        out = attest_mod.render_check(doc)
        rel = shard.relative_to(root).as_posix()
        assert rel in out and "untracked" not in out
        assert "commit" in out

    def test_second_committed_shard_for_one_sha_is_not_misreported(
            self, repo):
        """attested_shas keeps one path per sha, so judging disk-visibility off
        that map would call a SECOND committed shard for the same sha 'not at
        head' with a no-op remedy ('commit it' for a committed file)."""
        root, base, reviewed = repo
        self._shard(root, reviewed, name="a-first.json")
        self._shard(root, reviewed, name="b-second.json")
        self._git(root, "add", "-A")
        head = self._commit(root, "ingested.txt")
        doc = attest_mod.check_range(root, base="main", head=head)
        assert [m["sha"] for m in doc["attested"]] == [reviewed]
        assert doc["uncommitted"] == []
        assert "ON DISK" not in attest_mod.render_check(doc)

    def test_committed_shard_in_a_subdirectory_still_counts(self, repo):
        """The committed lookup keeps full tree paths — a shard filed under a
        subdirectory must count, not crash or land in unreadable, as
        flattening paths to basenames would make it."""
        root, base, reviewed = repo
        sub = root / attest_mod.SHARD_DIR / "2026"
        sub.mkdir(parents=True)
        (sub / "deep.json").write_text(json.dumps(
            {"schema": 1, "source": "attest", "sha": reviewed,
             "base_sha": "0" * 40, "rules_version": "v1",
             "reviewed_at": "2026-08-23T00:00:00+00:00", "records": []}))
        self._git(root, "add", "-A")
        head = self._commit(root, "ingested.txt")
        doc = attest_mod.check_range(root, base="main", head=head)
        assert [m["sha"] for m in doc["attested"]] == [reviewed]
        assert doc["unreadable"] == []


# --- range binding: advisory, never fatal ---

def WARN(*a):
    return attest_mod.range_binding(*a)[1]


def KIND(*a):
    return attest_mod.range_binding(*a)[0]


def test_findings_naming_no_changed_file_are_flagged():
    """A stale findings file from an earlier round is surfaced.

    A findings write that fails silently (a blocked heredoc) leaves a STALE
    findings file from an EARLIER round at the same scratchpad path.
    Everything else validates: schema, every rule_id, a clean tree, a
    consistent verdict. The result would be a well-formed CLEAN attestation
    about a completely different commit, committed as a shard and ingested as
    corpus. Findings that name no file the range changed are the visible sign.
    """
    stale = [dict(FINDING, file="docs/wiki/Architecture-3-Component-Warden.md")]
    msg = WARN(stale, ("warden/attest.py", "tests/test_attest.py"), BASE, HEAD)
    assert msg, "the near-miss must be surfaced"
    assert "docs/wiki/Architecture-3-Component-Warden.md" in msg
    assert "warden/attest.py" in msg
    assert "stale" in msg.lower(), "the likely cause must be named"


def test_an_out_of_range_finding_is_not_called_a_mistake():
    """The founder's ruling: this WARNS and never refuses, because two
    declared rules here produce out-of-range findings by construction —
    rename-complete ('a file the diff did not touch that still carries the
    old name is a filable finding') and wiki-fidelity. A backtest over the
    committed corpus found 1 of 87 shards a refusal would have blocked. The
    warning must say so, or it trains reviewers to delete true findings."""
    stale = [dict(FINDING, file="docs/wiki/Gate-Pipeline.md")]
    msg = WARN(stale, ("warden/cli.py",), BASE, HEAD)
    assert "rename-complete" in msg and "wiki-fidelity" in msg, \
        f"the legitimate shape must be named alongside the suspicious one: {msg}"
    # ...and the pair must not read as a closed list. The single out-of-range
    # instance in this repo's corpus — the one that justified warn-not-refuse
    # — is `unmapped:irreproducible-golden`, neither of the two named rules,
    # so a reviewer holding a third kind must not read themselves out of it.
    assert "any finding about a file the diff did not touch" in msg, \
        f"the cause list must stay open-ended: {msg}"


def test_the_printed_range_notation_matches_the_check():
    """The check is THREE-dot (what the branch changed since the merge base),
    which is what `warden diff` and the gate use. Printing `A..B` would tell
    the reader to verify with a two-dot diff, which in the exact case the
    message describes — main moved ahead — returns the OPPOSITE answer, and
    for an ancestor base denies a change the message is telling them about."""
    for msg in (WARN([FINDING], ("other.py",), BASE, HEAD),
                WARN([FINDING], (), BASE, HEAD)):
        assert f"{BASE[:12]}...{HEAD[:12]}" in msg, \
            f"printed range must be copy-pasteable three-dot: {msg}"


def test_any_overlap_is_silent():
    """ZERO overlap is the signal, never partial: a finding may name a file
    the fix later reverted out of the range, so one file in common is quiet."""
    reverted = dict(FINDING, file="warden/gone.py")
    assert WARN([FINDING, reverted], ("app/X.swift", "warden/other.py"),
                BASE, HEAD) == ""


def test_a_clean_review_names_no_files_and_is_silent():
    """A findings-free attestation names no files, so it has zero overlap by
    construction. Warning here would train the reader to ignore the warning —
    the same instinct behind 'a clean review is still a review': answering NO
    ATTESTATION for a clean review would single out exactly the PRs that
    passed."""
    assert WARN([], ("warden/attest.py",), BASE, HEAD) == ""


def test_an_empty_range_is_named_as_such_not_as_a_stale_file():
    """error-names-cause: base...head changing nothing is a different cause
    with a different remedy (fix --base, not the findings file). Reporting it
    as staleness sends the reader hunting through a file that is fine."""
    msg = WARN([FINDING], (), BASE, HEAD)
    assert "changed no files" in msg and "--base" in msg
    assert "stale" not in msg.lower(), \
        f"an empty range is not a stale findings file: {msg}"


def test_the_manifest_kind_is_the_same_reading_the_terminal_printed():
    """A warning that reaches only the terminal cannot be audited afterwards in
    an unattended run, so the manifest records the kind — and the kind comes
    back from the same call that produced the message, so the two cannot
    drift apart the way a caller re-deriving it would."""
    stale = [dict(FINDING, file="docs/wiki/Gate-Pipeline.md")]
    assert KIND(stale, ("warden/cli.py",), BASE, HEAD) == "out-of-range"
    assert KIND([FINDING], (), BASE, HEAD) == "empty-range"
    assert KIND([FINDING], ("app/X.swift",), BASE, HEAD) == "ok"
    assert KIND([], ("app/X.swift",), BASE, HEAD) == "ok"
    # a kind is never reported without its message, or vice versa
    for args in ((stale, ("warden/cli.py",)), ([FINDING], ()),
                 ([FINDING], ("app/X.swift",)), ([], ("app/X.swift",))):
        kind, msg = attest_mod.range_binding(*args, BASE, HEAD)
        assert (kind == "ok") == (msg == ""), f"{kind!r} paired with {msg!r}"


def test_the_range_binding_reading_claims_no_corpus_consumer():
    """The founder's ruling: the run manifest is the ONLY consumer of the
    range-binding reading, and no doc, schema description or comment may
    claim otherwise.

    Corpus visibility would need two coordinated changes, and the ruling
    declines both. This holds them declined, because the expensive failure
    here is the HALF change: an `out-of-range` waiver declared, and a schema
    saying "the retro can count how often it is claimed", with zero consumers
    in the tree. A named mechanism that does not exist is this repo's
    recurring defect class, so the absence is asserted rather than trusted.

    The guard does not refuse the SYMBOL anywhere in `warden/memory.py`:
    ingest re-applying the check is the sibling of `_check_rule_ids` at the
    same seam, warning into the ingest RUN DIR, which is the very terminus the
    ruling names. What the ruling forbids is a CORPUS consumer, so that is
    what is asserted: no field in the shard envelope, no reader in `stats`.
    The behavioural half — a real ingest of an out-of-range artifact produces
    a shard carrying no such key — is
    tests/test_memory_range_binding.py::test_the_binding_is_not_carried_
    into_the_committed_shard.
    """
    root = Path(__file__).resolve().parent.parent

    schema = (root / "warden" / "schemas" / "attestation.schema.json").read_text()
    assert "range_binding" not in schema, (
        "attestation.schema.json declares a range_binding field — either "
        "memory ingest now carries it into the corpus and this guard should "
        "move, or it is a field no shard can hold")

    ingest = (root / "warden" / "memory.py").read_text()
    # The shard envelope: the fixed field list every committed shard is built
    # from. A key here is a key in the corpus.
    envelope = ingest[ingest.index("envelope = {"):
                      ingest.index("shard = {**_redact_value(envelope)")]
    assert "range_binding" not in envelope, (
        "the shard envelope carries a range_binding key — ingest now writes "
        "the reading into the COMMITTED corpus, which the ruling declines; "
        "update the ruling and the docs together")
    # INVERSE, not a whitelist. Slicing out `stats()` and asserting the symbol
    # is absent there would miss that `stats()` RETURNS `review_events(root)`,
    # defined hundreds of lines outside the slice, so a tally added in
    # review_events (or corpus_age, _candidate_rows, render_stats) would be
    # reported by `memory stats` and read by the retro while the assertion
    # stayed green. So: the reading may appear ONLY inside
    # the one function that computes it and the one line that calls it.
    # `\b` excludes `range_bindings`, the ingest RESULT key, which is the run
    # dir's and is not a corpus consumer.
    fn_start = ingest.index("\ndef range_binding_at_ingest(")
    fn_end = ingest.index("\ndef ", fn_start + 1)

    def line_of(pos: int) -> int:
        return ingest.count("\n", 0, pos) + 1

    # TWO scans, because one regex cannot do both jobs.
    # `\brange_binding\b` does not match the longer identifier
    # `range_binding_at_ingest` — `_` is a word character — so the first scan
    # sees the raw signal and is BLIND to callers, which is exactly the corpus
    # consumer the ruling declines. The second scan counts the callers.
    stray = [line_of(m.start()) for m in re.finditer(r"\brange_binding\b", ingest)
             if not (fn_start <= m.start() < fn_end)]
    assert not stray, (
        f"warden/memory.py names range_binding outside "
        f"range_binding_at_ingest, at line(s) {stray} — either a second reader "
        "of the signal has appeared (the corpus consumer the ruling declines) "
        "or the check moved and this guard should move with it")

    # `\s*\(` so a prose mention in a docstring is not counted as a caller —
    # this scan is about who CALLS the reading, and the docstring that points
    # at it is the opposite of a consumer.
    callers = [line_of(m.start())
               for m in re.finditer(r"\brange_binding_at_ingest\s*\(", ingest)
               if not (fn_start <= m.start() < fn_end)]
    assert len(callers) == 1, (
        f"range_binding_at_ingest is called from {len(callers)} place(s) "
        f"outside its own definition, at line(s) {callers} — the reading has "
        "exactly one consumer, the ingest sweep that records it in the RUN "
        "DIR. A second caller is how the corpus consumer the ruling declines "
        "would arrive")

    # Normalized for the same reason the sibling guard in test_certify.py is:
    # a docstring's leading whitespace differs across interpreters (3.13 made
    # the compiler strip it), so any assertion over raw `__doc__` is one prose
    # reflow away from being version-dependent. Normalizing removes the trap.
    doc = " ".join((attest_mod.range_binding.__doc__ or "").split())
    assert "run manifest" in doc, (
        "range_binding's docstring must name the run manifest as where the "
        "signal ends")


# --------------------------------------------------------------------------
# a content-preserving rebase must not invalidate a valid attestation
# --------------------------------------------------------------------------

def _git(root, *args):
    import subprocess
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()


def _rebase_repo(tmp_path):
    """A repo with a reviewed branch, then main moves under it."""
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@e")
    _git(root, "config", "user.name", "t")
    (root / "f.txt").write_text("a\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "feat")
    (root / "f.txt").write_text("a\nb\n")
    _git(root, "commit", "-qam", "the reviewed change")
    return root


def test_patch_ids_survive_a_rebase_onto_a_moved_base(tmp_path):
    """The property the whole fix rests on, pinned directly.

    The AGGREGATE `base..head` tree diff is a diff against the base TIP, so it
    changes the moment main moves, failing exactly the case this exists to
    survive. Per-commit diffs do not care where the branch is rooted, and this
    test is what distinguishes the two.
    """
    root = _rebase_repo(tmp_path)
    pairs = attest_mod.range_patch_ids(root, "main", "feat")
    assert pairs, "no patch-ids computed for a non-empty range"
    before = sorted(pid for pid, _ in pairs)
    shas_before = _git(root, "rev-list", "main..feat").split()

    _git(root, "checkout", "-q", "main")
    _git(root, "commit", "-q", "--allow-empty", "-m", "main moves")
    _git(root, "checkout", "-q", "feat")
    _git(root, "rebase", "-q", "main")

    assert _git(root, "rev-list", "main..feat").split() != shas_before, (
        "the rebase did not rewrite the shas — this test proves nothing")
    after = attest_mod.range_patch_ids(root, "main", "feat")
    assert sorted(pid for pid, _ in after) == before, (
        "patch-ids moved across a content-preserving rebase, so the fallback "
        "cannot work")
    # The commit half MUST move — it is the thing a rebase rewrites, and the
    # reason only the patch-id half is recorded in the shard.
    assert sorted(c for _, c in after) != sorted(c for _, c in pairs)


def test_an_empty_range_and_a_failed_probe_are_different_answers(tmp_path):
    """Tri-state, for the reason this module already applies elsewhere: "could
    not look" is not an answer. Returning () for both would render a probe
    that never ran as NO ATTESTATION."""
    root = _rebase_repo(tmp_path)
    assert attest_mod.range_patch_ids(root, "feat", "feat") == (), (
        "an empty range is empty, not a failure")
    assert attest_mod.range_patch_ids(root, "main", "no-such-ref") is None, (
        "an unresolvable ref must be distinguishable from an empty range: "
        "collapsing them made `check` report NO ATTESTATION, with a remedy "
        "telling the reader to re-run a review that already happened")


def test_build_omits_the_binding_when_there_is_none(rules_dir):
    """Absent, not empty: a shard that could not compute a binding must be
    distinguishable from one that computed none, or the check cannot tell a
    shard written before the binding existed from a broken one."""
    doc = attest_mod.build(
        {"reviewers": [{"role": "code-reviewer", "agent": "a"}],
         "findings": [], "verdict": "clean"},
        head_sha="c" * 40, base_sha="a" * 40, rules_version="v1",
        rules_dir=rules_dir, range_patch_ids=())
    assert "range_patch_ids" not in doc

    doc = attest_mod.build(
        {"reviewers": [{"role": "code-reviewer", "agent": "a"}],
         "findings": [], "verdict": "clean"},
        head_sha="c" * 40, base_sha="a" * 40, rules_version="v1",
        rules_dir=rules_dir, range_patch_ids=("d" * 40,))
    assert doc["range_patch_ids"] == ["d" * 40]


# --------------------------------------------------------------------------
# Roster verification
# --------------------------------------------------------------------------
#
# Taking `reviewers` verbatim would let a shard name a reviewer that never
# ran, with no check in the platform able to see it, and the false provenance
# would become permanent in an append-only corpus.
#
# Every test below is written so that deleting the guard it covers turns it
# red. A guard whose test has never failed is the other half of this repo's
# recurring defect class.


def _crew(review_dir: Path, *, roles=("code-reviewer", "cross-examiner")):
    """A truthful roster, with the artifacts that make it truthful.

    Each report names its own role, because the protocol mandates it and
    because two reviewers of one round writing the same bytes is the very
    thing `carry_forward` refuses — and that within-round check runs on an
    unminted `tmp_path` too.
    """
    out = []
    for i, role in enumerate(roles):
        name = f"{role}.json"
        (review_dir / name).write_text(
            '{"role": "%s", "findings": [{"finding": "x"}]}\n' % role)
        out.append({"role": role, "agent": "claude-code subagent",
                    "returned": True, "findings": i, "output": name,
                    # Every dispatch states its round under --review-dir, so
                    # a roster fixture is written under the contract the
                    # check enforces.
                    "round": 1})
    return out


def test_a_roster_backed_by_real_outputs_verifies(build, tmp_path):
    doc = build({"reviewers": _crew(tmp_path), "findings": [],
                 "verdict": "clean"}, review_dir=tmp_path)
    assert doc["roster_verification"] == attest_mod.ROSTER_VERIFIED
    assert "VERIFIED" in attest_mod.render_summary(doc)


def test_a_reviewer_with_no_artifact_is_refused(build, tmp_path):
    """THE HEADLINE CASE: a fabricated reviewer.

    A fabricated reviewer is asserted in text and produces no file, because
    no subagent ran to produce one. That is precisely what has no
    artifact in the round directory.
    """
    crew = _crew(tmp_path)
    crew.append({"role": "fail-closed lens", "agent": "independent subagent",
                 "returned": True, "findings": 4,
                 "output": "fail-closed-lens.json"})
    with pytest.raises(attest_mod.AttestError, match="left no trace"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path)


def test_an_empty_output_file_is_not_evidence(build, tmp_path):
    """A silent write failure leaves a zero-byte file: absence and emptiness
    are the same claim."""
    crew = _crew(tmp_path)
    (tmp_path / crew[0]["output"]).write_text("")
    with pytest.raises(attest_mod.AttestError, match="is empty"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path)


def test_two_reviewers_may_not_share_one_artifact(build, tmp_path):
    """Otherwise one real reviewer's output backs an arbitrary number of
    claimed ones, and the check counts files instead of reviewers."""
    crew = _crew(tmp_path)
    crew[1]["output"] = crew[0]["output"]
    with pytest.raises(attest_mod.AttestError, match="already claimed"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path)


@pytest.mark.parametrize("output", [
    "../outside.json", "sub/nested.json", "..", ".",
    "/etc/passwd", "a\\b.json"])
def test_output_must_be_a_plain_basename(build, tmp_path, output):
    """A roster that can name a PATH can point the check at another round's
    file — a cross-round collision — or at anything on disk. The
    check must be confined to the round it describes."""
    crew = _crew(tmp_path)
    crew[0]["output"] = output
    with pytest.raises(attest_mod.AttestError, match="plain basename"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path)


def test_an_unevaluable_entry_is_a_refusal_not_a_pass(build, tmp_path):
    """Fail-closed, the charter lens: a roster entry that does not say
    whether it returned cannot be checked, and 'cannot check' must behave
    like a hit. Silently skipping it is how a fabricated reviewer would walk
    straight back through this guard."""
    crew = _crew(tmp_path)
    crew.append({"role": "consumer-blast-radius lens", "agent": "subagent"})
    with pytest.raises(attest_mod.AttestError, match="no `returned` field"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path)


def test_a_returned_reviewer_must_state_its_findings_count(build, tmp_path):
    crew = _crew(tmp_path)
    del crew[0]["findings"]
    with pytest.raises(attest_mod.AttestError, match="no `findings` count"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path)


def test_a_returned_reviewer_naming_no_output_is_refused(build, tmp_path):
    crew = _crew(tmp_path)
    del crew[0]["output"]
    with pytest.raises(attest_mod.AttestError, match="names no `output`"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path)


def test_a_dispatched_reviewer_that_did_not_return_is_declarable(
        build, tmp_path):
    """The point of `returned` being a declared field rather than a filter.

    A reviewer that was dispatched and fell over is part of what the round
    WAS. Dropping it leaves a roster that reads complete, which is the same
    lie in the other direction — so declaring it must be the easy, legal
    path, and it must not require an artifact that does not exist.
    """
    crew = _crew(tmp_path)
    crew.append({"role": "crew:enforcement-truth", "agent": "subagent",
                 "returned": False, "round": 1})
    doc = build({"reviewers": crew, "findings": [], "verdict": "clean"},
                review_dir=tmp_path)
    assert doc["roster_verification"] == attest_mod.ROSTER_VERIFIED
    assert "DID NOT RETURN" in attest_mod.render_summary(doc)


def test_a_non_returning_reviewer_may_not_also_claim_findings(
        build, tmp_path):
    crew = _crew(tmp_path)
    crew.append({"role": "enforcement-truth lens", "agent": "subagent",
                 "returned": False, "findings": 3})
    with pytest.raises(attest_mod.AttestError, match="returned nothing"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path)


def test_a_non_returning_reviewer_may_not_also_name_an_output(
        build, tmp_path):
    crew = _crew(tmp_path)
    crew.append({"role": "enforcement-truth lens", "agent": "subagent",
                 "returned": False, "output": "ghost.json"})
    with pytest.raises(attest_mod.AttestError, match="names an output file"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path)


def test_a_roster_where_nobody_returned_is_not_a_review(build, tmp_path):
    crew = [{"role": "code-reviewer", "agent": "subagent", "returned": False},
            {"role": "cross-examiner", "agent": "subagent", "returned": False}]
    with pytest.raises(attest_mod.AttestError, match="no review to"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path)


def test_a_missing_review_dir_is_refused_not_downgraded(build, tmp_path):
    """The fail-open shape this must not have: a caller that ASKED for the
    check and silently got `unverified-roster` instead would read the marker
    as noise and ship. Asking and not getting it is an error."""
    with pytest.raises(attest_mod.AttestError, match="not a directory"):
        build({"reviewers": REVIEWERS, "findings": [], "verdict": "clean"},
              review_dir=tmp_path / "nope")


def test_no_review_dir_stamps_the_marker_and_still_writes(build):
    """The decided trade: absence is a MARKER,
    not a refusal, so an enrolled consumer's existing invocation keeps
    working on a HIGH-tier contract — and the shard says so forever."""
    doc = build({"reviewers": REVIEWERS, "findings": [], "verdict": "clean"})
    assert doc["roster_verification"] == attest_mod.ROSTER_UNVERIFIED
    summary = attest_mod.render_summary(doc)
    assert "UNVERIFIED" in summary and "builder-asserted" in summary


def test_the_state_is_stamped_never_taken_from_the_payload(build,
                                                               tmp_path):
    """A caller must not be able to declare itself verified. The payload
    guard is what stops it, so this pins that `roster_verification` is not an
    accepted payload key."""
    payload = {"reviewers": _crew(tmp_path), "findings": [],
               "verdict": "clean", "roster_verification": "verified"}
    with pytest.raises(attest_mod.AttestError, match="extra keys"):
        build(payload)


def test_absent_is_rendered_as_not_recorded_never_as_verified():
    """A shard written before roster verification was never asked.

    Rendering silence would let every such shard read as checked. This is
    the same reasoning `range_patch_ids` uses for omitting rather than
    defaulting, applied to the reader instead of the writer.
    """
    doc = {"head_sha": HEAD, "base_sha": BASE, "rules_version": "v1",
           "reviewed_at": "2026-01-01T00:00:00+00:00", "reviewers": REVIEWERS,
           "findings": [], "verdict": "clean"}
    summary = attest_mod.render_summary(doc)
    assert "NOT RECORDED" in summary
    assert "VERIFIED" not in summary.replace("UNVERIFIED", "")


def test_the_roster_state_vocabulary_is_read_from_the_schema():
    """Retyped vocabularies drift, and this one drifts dangerously: an enum
    ADDITION is the schema edit the contract-freeze gate calls safe, and a
    stale hand-copy in memory.py would silently stop carrying the new state
    into shards — the same drift a hand-copied VERDICTS tuple would have."""
    from warden import memory as memory_mod

    declared = json.loads(
        (Path(attest_mod.__file__).parent / "schemas"
         / "attestation.schema.json").read_text())
    assert (set(attest_mod.ROSTER_STATES)
            == set(declared["properties"]["roster_verification"]["enum"]))
    assert {attest_mod.ROSTER_VERIFIED, attest_mod.ROSTER_UNVERIFIED} == set(
        attest_mod.ROSTER_STATES)
    assert memory_mod.ROSTER_STATES is attest_mod.ROSTER_STATES, (
        "memory must IMPORT the vocabulary, not keep its own copy")


def test_the_marker_rides_into_the_shard_envelope():
    """A declared field with zero consumers declares nothing. The corpus is
    the consumer
    that matters: `attest check`, the retro and any future audit read shards,
    not the gitignored run dir."""
    from warden import memory as memory_mod

    for state in attest_mod.ROSTER_STATES:
        event = memory_mod.build_review_event(
            {"verdict": "clean", "reviewers": REVIEWERS,
             "roster_verification": state})
        assert event["roster_verification"] == state
    # ...and an artifact that never stated one must not acquire a default.
    bare = memory_mod.build_review_event(
        {"verdict": "clean", "reviewers": REVIEWERS})
    assert "roster_verification" not in bare
    junk = memory_mod.build_review_event(
        {"verdict": "clean", "reviewers": REVIEWERS,
         "roster_verification": "totally-fine-honest"})
    assert "roster_verification" not in junk


def test_the_summary_reads_the_fields_not_the_agent_string():
    """Without structured fields the only place to put "returned, 6 findings"
    is inside the free-text `agent` value, as the 20260831T232211Z shard does.
    A renderer parsing that string would re-teach the shape the structured
    fields exist to replace."""
    doc = {"head_sha": HEAD, "base_sha": BASE, "rules_version": "v1",
           "reviewed_at": "2026-01-01T00:00:00+00:00", "verdict": "clean",
           "roster_verification": attest_mod.ROSTER_VERIFIED, "findings": [],
           "reviewers": [{"role": "code-reviewer", "agent": "subagent",
                          "returned": True, "findings": 6,
                          "output": "code-reviewer.json"}]}
    summary = attest_mod.render_summary(doc)
    assert "6 findings" in summary and "code-reviewer.json" in summary


def test_the_new_schema_fields_are_optional_so_old_shards_stay_valid():
    """Consumer blast radius, asserted rather than asserted-about. Every
    committed shard in this repo's corpus predates these fields; if any of
    them had become required, the corpus would stop validating and
    `certify --level 4` would go red on merge to main."""
    schema = json.loads(
        (Path(attest_mod.__file__).parent / "schemas"
         / "attestation.schema.json").read_text())
    required = set(schema["required"])
    assert "roster_verification" not in required, (
        "roster_verification became required — every pre-277 shard is now "
        "invalid, and warden/schemas is declared additive-only")
    reviewer = schema["properties"]["reviewers"]["items"]
    assert set(reviewer["required"]) == {"role", "agent"}, (
        "a reviewer field became required — this breaks every pinned "
        "consumer's existing attest write, not just this repo's corpus")
    assert reviewer["additionalProperties"] is False


# --- review-dir spellings, linked outputs and dropped reviewers ---------------


def test_r1f1_an_empty_review_dir_is_refused_not_downgraded(build,
                                                                tmp_path):
    """An empty --review-dir is refused, never downgraded.

    `--review-dir ""` is what an unset shell variable expands to. A
    truthiness test in the CLI collapses an empty string into the "caller did
    not ask" branch: exit 0, roster stamped `unverified-roster`, and the
    caller reading that as a checked round. And `Path("")` is `PosixPath(".")`
    — a real directory — so passing the value through without refusing it
    would verify the roster against the CURRENT WORKING DIRECTORY instead.

    Both spellings are refused here, because the guard has to hold at both
    layers: the CLI must pass the value through, and verify_roster must
    refuse it.
    """
    for spelling in ("", "   ", "."):
        with pytest.raises(attest_mod.AttestError, match="names no directory"):
            build({"reviewers": _crew(tmp_path), "findings": [],
                   "verdict": "clean"}, review_dir=Path(spelling))


def test_r1f1_the_cli_passes_an_empty_review_dir_through(monkeypatch):
    """The CLI half of the empty --review-dir refusal.

    verify_roster can only refuse what reaches it. A falsy test in the CLI
    ERASES the empty value before the call, so a test of verify_roster alone
    would stay green while the shipped command was fail-open.
    """
    import argparse

    from warden import cli as cli_mod

    parser_args = argparse.Namespace(review_dir="")
    resolved = (Path(parser_args.review_dir)
                if parser_args.review_dir is not None else None)
    assert resolved is not None, (
        "an empty --review-dir must survive as a value the roster check can "
        "refuse, not be erased into 'the caller did not ask'")
    source = Path(cli_mod.__file__).read_text()
    assert "args.review_dir is not None" in source, (
        "warden/cli.py went back to a falsy test on --review-dir, which "
        "silently downgrades an empty value to unverified-roster (R1F1)")


def test_r1f2_a_symlinked_output_is_refused(build, tmp_path):
    """A symlinked output is refused.

    The basename rule only stops a roster NAMING a path. A symlink is how a
    plain basename still resolves anywhere on disk — a cross-round confusion
    reached by a different door — because `is_file()` and `stat()` both
    follow links, so a link to a file outside the round would verify.
    """
    outside = tmp_path.parent / "somewhere-else.json"
    outside.write_text("not this round's output\n")
    review_dir = tmp_path / "reviewers"
    review_dir.mkdir()
    crew = _crew(review_dir)
    (review_dir / "cross-examiner.json").unlink()
    (review_dir / "cross-examiner.json").symlink_to(outside)
    with pytest.raises(attest_mod.AttestError, match="is a symlink"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=review_dir)


def test_r1f2_aliases_cannot_mint_a_second_reviewer(build, tmp_path):
    """The distinctness half of the symlink refusal.

    Keying `claimed_outputs` on the declared NAME would let `ln -s
    code-reviewer.json second.json` produce two 'distinct' claims backed by
    one real artifact — while the summary says "each backed by a distinct
    non-empty output". One `ln -s` is a strictly cheaper forgery than the
    limit the docstring concedes (a caller writing plausible files itself).
    """
    review_dir = tmp_path / "reviewers"
    review_dir.mkdir()
    crew = _crew(review_dir, roles=("code-reviewer",))
    (review_dir / "alias.json").symlink_to(review_dir / "code-reviewer.json")
    crew.append({"role": "cross-examiner", "agent": "subagent",
                 "returned": True, "findings": 0, "output": "alias.json"})
    with pytest.raises(attest_mod.AttestError, match="symlink"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=review_dir)


def test_r2f1_a_hardlinked_output_is_still_one_artifact(build, tmp_path):
    """A hard link is one artifact, which is why uniqueness is keyed on the
    INODE rather than on the path.

    Refusing symlinks and keying uniqueness on the resolved path leaves the
    identical defect one spelling over: `ln` instead of `ln -s`. A hard link
    is two equally real directory entries for one file, so `resolve()` cannot
    collapse them and a path-keyed check sees two distinct artifacts — N
    links to one report verifying as N reviewers.

    A path answers "where"; only (st_dev, st_ino) answers "which file". A
    conceded limit is honest only when closing it is genuinely out of reach,
    and this one is two lines. Conceding a defect you can afford to fix is
    how a docstring quietly becomes the place defects go to be forgiven.
    """
    review_dir = tmp_path / "reviewers"
    review_dir.mkdir()
    crew = _crew(review_dir, roles=("code-reviewer",))
    real = review_dir / "code-reviewer.json"
    (review_dir / "same.json").hardlink_to(real)
    crew.append({"role": "cross-examiner", "agent": "subagent",
                 "returned": True, "findings": 0, "output": "same.json"})
    with pytest.raises(attest_mod.AttestError, match="hard links"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=review_dir)


def test_b1_a_non_object_roster_entry_names_its_cause(build, tmp_path):
    """A non-object roster entry is refused with a message naming the cause.

    A string in the reviewers list reaching `reviewer.get("role")` raises
    AttributeError. The write would still fail — never fail-open — but the
    operator would get a traceback naming `.get` rather than a message naming
    the cause, bypassing AttestError's handling in main(). The schema
    would reject the shape too, but schema validation runs LATER in build()
    than the roster check, so a malformed payload meets this first.
    """
    for junk in ("code-reviewer", ["code-reviewer"], 7, None):
        with pytest.raises(attest_mod.AttestError, match="not an object"):
            build({"reviewers": [junk], "findings": [], "verdict": "clean"},
                  review_dir=tmp_path)


def test_b2_a_directory_output_is_not_reported_as_missing(build,
                                                              tmp_path):
    """A directory output is not reported as missing.

    Refusing an `output` naming a directory with "no such file is in <dir>"
    is false: the name exists. A reader would go hunting for a
    missing file that is sitting right there.
    """
    review_dir = tmp_path / "reviewers"
    review_dir.mkdir()
    crew = _crew(review_dir, roles=("code-reviewer",))
    (review_dir / "a-directory.json").mkdir()
    crew.append({"role": "cross-examiner", "agent": "subagent",
                 "returned": True, "findings": 0, "output": "a-directory.json"})
    with pytest.raises(attest_mod.AttestError,
                       match="is not a regular file") as excinfo:
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=review_dir)
    assert "no such file" not in str(excinfo.value), (
        "a directory is still being reported as an absent file")


def test_r1f3_a_dropped_reviewer_is_caught(build, tmp_path):
    """A dropped reviewer is caught.

    The schema description, Gate-Pipeline.md and the pre-pr-review skill all
    say a DROPPED reviewer is caught — "a silently shorter roster is the same
    lie in the other direction". Walking only the declared roster, without
    listing the directory, gives that claim no mechanism: a round that
    dispatched three reviewers, kept two and omitted the third would pass as
    `verified` with the third's report sitting unclaimed on disk.
    """
    review_dir = tmp_path / "reviewers"
    review_dir.mkdir()
    crew = _crew(review_dir)
    # The third reviewer ran and left its report; the roster omits it.
    (review_dir / "fail-closed-lens.json").write_text('[{"finding": "real"}]\n')
    with pytest.raises(attest_mod.AttestError,
                       match="no roster entry claims") as excinfo:
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=review_dir)
    assert "fail-closed-lens.json" in str(excinfo.value), (
        "the refusal must NAME the unclaimed report, or the operator cannot "
        "tell which reviewer was dropped")


def test_r1f3_an_unlistable_review_dir_refuses(build, tmp_path,
                                                   monkeypatch):
    """Fail-closed on the listing itself. If the directory cannot be read, a
    dropped reviewer cannot be ruled out, and 'cannot evaluate' must behave
    like a hit — the same rule the rest of this function follows."""
    review_dir = tmp_path / "reviewers"
    review_dir.mkdir()
    crew = _crew(review_dir)
    real_iterdir = Path.iterdir

    def boom(self):
        if self == review_dir:
            raise PermissionError("simulated unreadable review dir")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", boom)
    with pytest.raises(attest_mod.AttestError, match="could not be listed"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=review_dir)


def test_r1f3_an_empty_leftover_file_is_not_a_dropped_reviewer(build,
                                                                   tmp_path):
    """An empty file is a failed write, which says nothing about whether a
    reviewer was dropped. Refusing on it would train the reader to clear the
    directory rather than to look for the missing reviewer."""
    review_dir = tmp_path / "reviewers"
    review_dir.mkdir()
    crew = _crew(review_dir)
    (review_dir / "half-written.json").write_text("")
    doc = build({"reviewers": crew, "findings": [], "verdict": "clean"},
                review_dir=review_dir)
    assert doc["roster_verification"] == attest_mod.ROSTER_VERIFIED


def test_r1f3_a_non_returning_reviewer_needs_no_report_on_disk(build,
                                                                   tmp_path):
    """The two halves must not fight each other: declaring a reviewer that
    did not return is the honest path, and it must stay legal with nothing
    on disk to claim."""
    review_dir = tmp_path / "reviewers"
    review_dir.mkdir()
    crew = _crew(review_dir)
    crew.append({"role": "crew:consumer-blast-radius", "agent": "subagent",
                 "returned": False, "round": 1})
    doc = build({"reviewers": crew, "findings": [], "verdict": "clean"},
                review_dir=review_dir)
    assert doc["roster_verification"] == attest_mod.ROSTER_VERIFIED


# --- the round state reaches the committed corpus ----------------------------

def _minted_round(root: Path, head: str, base_sha: str = BASE) -> Path:
    """A review dir warden minted, holding one reviewer report.

    Built through `runs.write_round_manifest` rather than by hand, so a change
    to what warden mints breaks this fixture instead of leaving it describing a
    shape warden no longer writes.
    """
    from warden import runs as runs_mod

    d = root / f"round-{head[:6]}"
    reviewers = d / "reviewers"
    reviewers.mkdir(parents=True)
    (reviewers / "code-reviewer.json").write_text('{"findings": []}\n')
    runs_mod.write_round_manifest(d, base="main", base_sha=base_sha,
                                  head=head, branch="mine", round_no=1)
    return reviewers


def test_the_round_state_is_stamped_into_the_attestation(build, tmp_path):
    """The round state is stamped into the attestation.

    On stderr and in the run manifest under `.warden/out/` — gitignored, and
    consumed by no reader — `round_binding: unminted` never reaches a later
    reader of the committed SHARDS, who then cannot tell a warden-minted round
    from any directory handed to `--review-dir`.

    All three states, not just the interesting one. An absent field has to
    mean "written before the field existed", and it can only mean that if the
    field is stamped whenever the question IS asked — including when the
    answer is the dull one.
    """
    roster = [{"role": "code-reviewer", "agent": "subagent", "returned": True,
               "findings": 0, "output": "code-reviewer.json", "round": 1}]

    none_given = build({"reviewers": REVIEWERS, "findings": [],
                        "verdict": "clean"})
    assert none_given["round_binding"] == "no-review-dir"

    plain = tmp_path / "handed-over"
    plain.mkdir()
    (plain / "code-reviewer.json").write_text('{"findings": []}\n')
    unminted = build({"reviewers": roster, "findings": [], "verdict": "clean"},
                     review_dir=plain)
    assert unminted["round_binding"] == "unminted", (
        "a directory warden did not mint is attested without saying so — the "
        "shard is indistinguishable from a minted round")

    bound = build({"reviewers": roster, "findings": [], "verdict": "clean"},
                  review_dir=_minted_round(tmp_path, HEAD))
    assert bound["round_binding"] == "bound"


def test_a_round_minted_for_another_head_is_refused_not_recorded(
        build, tmp_path):
    """The refusal is still the part that BINDS, and it must stay a refusal.
    If a mismatch became a recorded state, the shard would carry a value the
    schema does not declare AND the write that should have stopped would have
    succeeded — the fail-open reading of the same change."""
    foreign = _minted_round(tmp_path, "f" * 40)
    roster = [{"role": "code-reviewer", "agent": "subagent", "returned": True,
               "findings": 0, "output": "code-reviewer.json", "round": 1}]
    with pytest.raises(attest_mod.AttestError, match="different commit"):
        build({"reviewers": roster, "findings": [], "verdict": "clean"},
              review_dir=foreign)


def test_the_round_state_vocabulary_is_read_from_the_schema():
    """`roster_verification`'s precedent, followed exactly.

    A hand-copied second tuple drifts in the dangerous direction: an enum
    ADDITION is the schema edit the contract-freeze gate calls safe, and a
    stale copy in memory.py would silently stop carrying the new state into
    shards.
    """
    from warden import memory as memory_mod

    declared = json.loads(
        (Path(attest_mod.__file__).parent / "schemas"
         / "attestation.schema.json").read_text())
    assert (set(attest_mod.ROUND_STATES)
            == set(declared["properties"]["round_binding"]["enum"]))
    assert memory_mod.ROUND_STATES is attest_mod.ROUND_STATES, (
        "memory must IMPORT the vocabulary, not keep its own copy")


def test_the_round_state_rides_into_the_shard_envelope():
    """The corpus is the consumer that matters: `attest check`, the retro and
    any future audit read shards, not the gitignored run dir. A state that
    stops at the run dir reaches none of them."""
    from warden import memory as memory_mod

    for state in attest_mod.ROUND_STATES:
        event = memory_mod.build_review_event(
            {"verdict": "clean", "reviewers": REVIEWERS,
             "round_binding": state})
        assert event["round_binding"] == state

    bare = memory_mod.build_review_event(
        {"verdict": "clean", "reviewers": REVIEWERS})
    assert "round_binding" not in bare, (
        "an artifact that never stated a round state acquired a default — "
        "absent must stay absent, because absent is not 'bound'")
    junk = memory_mod.build_review_event(
        {"verdict": "clean", "reviewers": REVIEWERS,
         "round_binding": "definitely-minted-honest"})
    assert "round_binding" not in junk, (
        "a state outside the declared vocabulary reached the corpus")


def test_the_summary_names_the_round_state_and_says_absent_out_loud():
    """A verification state nobody prints is a declared field with zero
    consumers. Absent gets its own line rather than silence: a shard written
    before the field existed was never asked the question, and saying nothing
    would read as a pass."""
    base = {"head_sha": HEAD, "base_sha": BASE, "rules_version": "v1",
            "reviewed_at": "2026-01-01T00:00:00+00:00", "verdict": "clean",
            "findings": [], "reviewers": REVIEWERS}
    assert "BOUND" in attest_mod.render_summary(dict(base, round_binding="bound"))
    assert "UNMINTED" in attest_mod.render_summary(
        dict(base, round_binding="unminted"))
    assert "NO REVIEW DIR" in attest_mod.render_summary(
        dict(base, round_binding="no-review-dir"))
    old = attest_mod.render_summary(base)
    assert "NOT RECORDED" in old and "not the same as" in old, (
        "a pre-yt0.39.1 shard renders silently, so a reader cannot tell "
        "'never asked' from 'asked and bound'")


def test_the_field_is_additive_so_every_committed_shard_stays_valid():
    """Consumer blast radius, asserted rather than asserted-about.

    `warden/schemas/**` is declared additive-only and pinned consumers parse
    it. Three things have to hold and each is checked separately, because a
    single "the schema still loads" assertion would pass through all three:
    the field must be OPTIONAL (every shard in this repo's corpus predates it,
    and `certify --level 4` re-validates the corpus on every merge to main);
    the object must stay closed, since `additionalProperties: false` is what
    makes the addition meaningful rather than decorative; and the enum must
    declare every state the code can actually produce, or `build` would stamp a
    value its own validator rejects.
    """
    schema = json.loads(
        (Path(attest_mod.__file__).parent / "schemas"
         / "attestation.schema.json").read_text())

    assert "round_binding" not in set(schema["required"]), (
        "round_binding became required — every shard written before it is now "
        "invalid, and warden/schemas is declared additive-only")
    assert schema["additionalProperties"] is False, (
        "the attestation object stopped being closed, so adding a declared "
        "field no longer says anything a reader can rely on")
    assert set(attest_mod.ROUND_STATES) == {"bound", "unminted", "no-review-dir"}, (
        "the declared states no longer match what round_binding() returns — "
        "build stamps its result straight into a document this schema "
        f"validates, so a fourth state would refuse every write: "
        f"{attest_mod.ROUND_STATES}")


# --- the base's ancestry, carried into the committed evidence ----------------
#
# `base_binding` takes the base FROM THE ROUND, which is right — and a round
# minted against a base branch that had already moved past this branch records
# a base that is not on it. Warden computed that reading and printed it; it
# stopped at stderr and a gitignored run manifest, so the COMMITTED shard
# carried the misleading base with no committed record of the caveat, and two
# shards on one branch could disagree about the base with nothing saying which
# one a reader may recompute from. These pin the reading into the shard, on
# `round_binding`'s precedent and with its three assertions.


def test_the_ancestry_vocabulary_is_read_from_the_schema():
    """`round_binding`'s precedent, followed exactly: a hand-copied second
    tuple drifts in the dangerous direction, because an enum ADDITION is the
    schema edit the contract-freeze gate calls safe."""
    from warden import memory as memory_mod

    declared = json.loads(
        (Path(attest_mod.__file__).parent / "schemas"
         / "attestation.schema.json").read_text())
    assert (set(attest_mod.ANCESTRY_STATES)
            == set(declared["properties"]["base_ancestry"]["enum"]))
    assert memory_mod.ANCESTRY_STATES is attest_mod.ANCESTRY_STATES, (
        "memory must IMPORT the vocabulary, not keep its own copy")


def test_the_ancestry_reading_rides_into_the_shard_envelope():
    """The corpus is the consumer that matters. A reader recomputing figures
    from a shard's own `base_sha..sha` is exactly who the not-ancestor reading
    is for, and the run manifest that used to hold it is gitignored."""
    from warden import memory as memory_mod

    for state in attest_mod.ANCESTRY_STATES:
        event = memory_mod.build_review_event(
            {"verdict": "clean", "reviewers": REVIEWERS,
             "base_ancestry": state})
        assert event["base_ancestry"] == state

    bare = memory_mod.build_review_event(
        {"verdict": "clean", "reviewers": REVIEWERS})
    assert "base_ancestry" not in bare, (
        "an artifact that never stated an ancestry acquired a default — "
        "absent must stay absent, because absent is not 'ancestor'")
    junk = memory_mod.build_review_event(
        {"verdict": "clean", "reviewers": REVIEWERS,
         "base_ancestry": "definitely-on-the-branch-honest"})
    assert "base_ancestry" not in junk, (
        "a reading outside the declared vocabulary reached the corpus")


def test_the_summary_names_the_ancestry_and_says_absent_out_loud():
    """A reading nobody prints is a declared field with zero consumers, and
    this is the one a person acts on wrongly when it is missing: they run
    `git diff base..head` over the shard's own shas."""
    base = {"head_sha": HEAD, "base_sha": BASE, "rules_version": "v1",
            "reviewed_at": "2026-01-01T00:00:00+00:00", "verdict": "clean",
            "findings": [], "reviewers": REVIEWERS}
    assert "ON THE BRANCH" in attest_mod.render_summary(
        dict(base, base_ancestry="ancestor"))
    assert "NOT ON THE BRANCH" in attest_mod.render_summary(
        dict(base, base_ancestry="not-ancestor"))
    assert "NOT EVALUATED" in attest_mod.render_summary(
        dict(base, base_ancestry="unavailable"))
    old = attest_mod.render_summary(base)
    assert "base: NOT RECORDED" in old and "not the same as" in old, (
        "a shard written before the field renders silently, so a reader "
        "cannot tell 'never recorded' from 'recorded and on the branch'")


def test_the_ancestry_field_is_additive_so_the_corpus_stays_valid():
    """Consumer blast radius, asserted rather than asserted-about — the three
    separate checks `round_binding`'s sibling above makes, for its reasons."""
    schema = json.loads(
        (Path(attest_mod.__file__).parent / "schemas"
         / "attestation.schema.json").read_text())

    assert "base_ancestry" not in set(schema["required"]), (
        "base_ancestry became required — every shard written before it is now "
        "invalid, and warden/schemas is declared additive-only")
    assert schema["additionalProperties"] is False
    assert set(attest_mod.ANCESTRY_STATES) == {"ancestor", "not-ancestor",
                                               "unavailable"}, (
        "the declared readings no longer match what base_ancestry() returns — "
        "build stamps its result straight into a document this schema "
        f"validates: {attest_mod.ANCESTRY_STATES}")


def test_a_build_with_no_repository_to_ask_records_unavailable(build):
    """`root` None is "could not look", and it must not read as "looked and
    found it on the branch"."""
    doc = build({"reviewers": REVIEWERS, "findings": [], "verdict": "clean"})
    assert doc["base_ancestry"] == attest_mod.ANCESTRY_UNAVAILABLE


# --- the light crew, reachable by omitting an argument -----------------------
#
# `verify_crew` — and so the light tier's inertness proof — used to live inside
# `if review_dir is not None`. A shard written WITHOUT a review dir, carrying a
# claims-auditor roster, was held to no proof at all: nothing recomputed the
# tier, so the roster was taken on the builder's word. That is the
# self-certification the tier exists to refuse, reached by omitting an
# argument.

def _light_crew(light: dict | None = ...) -> dict:
    """A declared crew with a light round — built per call rather than kept as
    a module constant, so the roles here are this test's fixture data and not a
    second vocabulary beside the graph's."""
    crew = {"cap": 2,
            "rounds": {1: ["code-reviewer", "cross-examiner"],
                       2: ["scoped-re-reviewer"]},
            "closure": None,
            "light": {"roles": ["claims-auditor"], "payload": "claims-only"}}
    return crew if light is ... else {**crew, "light": light}


def _light_roster() -> list:
    """The light crew's roster as a payload writes it."""
    return [{"role": "claims-auditor", "round": 1, "agent": "subagent",
             "returned": True, "findings": 0}]


def test_a_light_roster_written_with_no_review_dir_is_refused(rules_dir,
                                                                   tmp_path):
    """THE HOLE, driven at the exact call that opened it: `build` with a light
    roster and `review_dir=None`. It used to stamp `unverified-roster` at exit
    0, which for this one tier is not a recorded downgrade — the roster IS the
    eligibility claim, so an unchecked one is the assertion the tier refuses.
    """
    with pytest.raises(attest_mod.AttestError) as exc:
        attest_mod.build(
            {"reviewers": _light_roster(), "findings": [],
             "verdict": "clean"},
            head_sha=HEAD, base_sha=BASE, rules_version="v1",
            rules_dir=rules_dir, review_dir=None, root=tmp_path,
            review_crew=_light_crew())
    assert "light crew" in str(exc.value)
    assert "no minted round number" in str(exc.value)
    assert "--review-dir" in str(exc.value), (
        "the refusal names the mint but not the flag that was left off, so a "
        "caller is told half the remedy")


def test_every_other_roster_still_writes_with_no_review_dir(rules_dir,
                                                                tmp_path):
    """The contract change is the light branch and nothing else. Moving when
    `verify_crew` runs would otherwise hold every existing caller to a round
    number only a round dir carries."""
    doc = attest_mod.build(
        {"reviewers": REVIEWERS, "findings": [], "verdict": "clean"},
        head_sha=HEAD, base_sha=BASE, rules_version="v1",
        rules_dir=rules_dir, review_dir=None, root=tmp_path,
        review_crew=_light_crew())
    assert doc["roster_verification"] == attest_mod.ROSTER_UNVERIFIED
    assert doc["round_binding"] == "no-review-dir"


def test_a_repo_declaring_no_light_round_is_unaffected(rules_dir,
                                                            tmp_path):
    """With no `review.light` declared there is no light crew to recognise,
    and `claims-auditor` is then just a role — the behaviour every repo had
    before the tier, preserved on the no-review-dir path too."""
    doc = attest_mod.build(
        {"reviewers": _light_roster(), "findings": [], "verdict": "clean"},
        head_sha=HEAD, base_sha=BASE, rules_version="v1",
        rules_dir=rules_dir, review_dir=None, root=tmp_path,
        review_crew=_light_crew(light=None))
    assert doc["roster_verification"] == attest_mod.ROSTER_UNVERIFIED


# --- a reviewer output carried forward between rounds ------------------------
#
# A BYTE-IDENTICAL code-reviewer.json copied forward from round 1 into later
# rounds satisfies `verify_roster`: each copy is a distinct, non-empty,
# non-symlink regular file in the right directory, which is all it asks. So
# the roster check, which exists to stop a shard claiming a review that did
# not happen, is satisfied by a stale artifact — the same defect class one
# seam over. These tests pin the closure: identical bytes in another round
# are a refusal, and the refusal names both rounds.

def _rounds_root(tmp_path: Path) -> Path:
    return tmp_path / ".warden" / "out" / "rounds"


def _round(tmp_path: Path, name: str, head: str, *,
           reports: dict[str, str]) -> Path:
    """One minted round under a shared rounds root, holding `reports`
    (basename -> bytes) in its `reviewers/`. Built through
    `runs.write_round_manifest` so a change to what warden mints breaks this
    fixture rather than leaving it describing a shape warden no longer writes."""
    from warden import runs as runs_mod

    d = _rounds_root(tmp_path) / name
    reviewers = d / "reviewers"
    reviewers.mkdir(parents=True)
    for basename, text in reports.items():
        (reviewers / basename).write_text(text)
    runs_mod.write_round_manifest(d, base="main", base_sha=BASE, head=head,
                                  branch="mine", round_no=1)
    return reviewers


def _roster(*roles: str) -> list[dict]:
    # `round` is required on every roster entry under --review-dir; these
    # fixtures are all single-round.
    return [{"role": r, "agent": "subagent", "returned": True, "findings": 0,
             "output": f"{r}.json", "round": 1} for r in roles]


REPORT_R1 = '{"judged": "cccccccccccc", "findings": []}\n'


def test_a_reviewer_output_carried_forward_from_an_earlier_round_is_refused(
        build, tmp_path):
    """A reviewer output carried forward from an earlier round is refused.

    Round 1 judged an earlier head; round 2 was minted for HEAD and its
    reviewer file is round 1's, byte for byte. The roster check alone is
    SATISFIED by that — asserted here on purpose, so the test documents the
    gap it closes and goes red if the closure is removed — and `build` must
    refuse, naming the reviewer, the file and the round it was copied from.
    """
    _round(tmp_path, "r1-earlier", "c" * 40,
           reports={"code-reviewer.json": REPORT_R1})
    r2 = _round(tmp_path, "r2-this", HEAD,
                reports={"code-reviewer.json": REPORT_R1})
    state, _ = attest_mod.verify_roster(_roster("code-reviewer"), r2)
    assert state == attest_mod.ROSTER_VERIFIED, (
        "the roster check is expected to pass here — that is the gap; if it "
        "started refusing, this test no longer proves the carry-forward "
        "check is what closes it")
    with pytest.raises(attest_mod.AttestError) as e:
        build({"reviewers": _roster("code-reviewer"), "findings": [],
               "verdict": "clean"}, review_dir=r2)
    msg = str(e.value)
    assert "carried forward" in msg
    assert "code-reviewer" in msg and "r1-earlier" in msg, (
        "the refusal must name the reviewer and the round it was copied from")


def test_a_hard_link_across_rounds_is_the_same_carry_forward(build,
                                                                  tmp_path):
    """`ln` rather than `cp`: no path comparison sees it, and the inode check
    in verify_roster is scoped to ONE round. Content identity catches both
    spellings with one rule."""
    import os

    r1 = _round(tmp_path, "r1-earlier", "c" * 40,
                reports={"code-reviewer.json": REPORT_R1})
    r2 = _round(tmp_path, "r2-this", HEAD, reports={})
    os.link(r1 / "code-reviewer.json", r2 / "code-reviewer.json")
    with pytest.raises(attest_mod.AttestError, match="carried forward"):
        build({"reviewers": _roster("code-reviewer"), "findings": [],
               "verdict": "clean"}, review_dir=r2)


def test_distinct_outputs_across_rounds_are_fine(build, tmp_path):
    """The negative, so the check cannot be satisfied by refusing everything:
    a round whose report differs from every sibling's attests, and the reading
    says what was compared."""
    _round(tmp_path, "r1-earlier", "c" * 40,
           reports={"code-reviewer.json": REPORT_R1})
    r2 = _round(tmp_path, "r2-this", HEAD,
                reports={"code-reviewer.json":
                         '{"judged": "bbbbbbbbbbbb", "findings": []}\n'})
    doc = build({"reviewers": _roster("code-reviewer"), "findings": [],
                 "verdict": "clean"}, review_dir=r2)
    assert doc["round_binding"] == "bound"
    state, detail = attest_mod.carry_forward(_roster("code-reviewer"), r2)
    assert state == "distinct"
    assert "1 sibling round" in detail


def test_the_refusal_covers_every_sibling_not_only_the_previous_one(
        build, tmp_path):
    """Any later round can carry round 1's bytes. A check comparing only
    against the immediately preceding round would pass round 3 here: it
    differs from round 2 and is a copy of round 1. Every sibling is
    compared."""
    _round(tmp_path, "r1-earliest", "c" * 40,
           reports={"code-reviewer.json": REPORT_R1})
    _round(tmp_path, "r2-middle", "d" * 40,
           reports={"code-reviewer.json":
                    '{"judged": "dddddddddddd", "findings": []}\n'})
    r3 = _round(tmp_path, "r3-this", HEAD,
                reports={"code-reviewer.json": REPORT_R1})
    with pytest.raises(attest_mod.AttestError, match="r1-earliest"):
        build({"reviewers": _roster("code-reviewer"), "findings": [],
               "verdict": "clean"}, review_dir=r3)


def test_two_reviewers_in_one_round_with_identical_reports_are_refused(
        build, tmp_path):
    """The same claim within a round: one report cannot be evidence that two
    reviewers ran. verify_roster keys on the inode, so `cp` — two inodes, one
    content — slipped past it; content identity is the rule that says 'one
    artifact' in every spelling."""
    r = _round(tmp_path, "r-this", HEAD,
               reports={"code-reviewer.json": REPORT_R1,
                        "cross-examiner.json": REPORT_R1})
    with pytest.raises(attest_mod.AttestError) as e:
        build({"reviewers": _roster("code-reviewer", "cross-examiner"),
               "findings": [], "verdict": "clean"}, review_dir=r)
    assert "cross-examiner" in str(e.value) and "code-reviewer" in str(e.value)


def test_r2_the_mandated_shape_clears_both_honest_collisions(build,
                                                                 tmp_path):
    """`judged` alone leaves the check refusing two HONEST states, and both
    are on the gate's golden
    path — the two reviewers of one round when the PR is clean, and a round
    re-minted at the same head.

    The `judged`-only shape is asserted colliding first, to show which
    discriminator does the work; then the shipped shape (head + round + role)
    is asserted `distinct` on both axes. A loosening of `carry_forward` — one
    that stopped comparing within a round, or stopped comparing siblings
    minted for the same head — goes red on the first half; a shape that lost
    `round` or `role` goes red on the second.
    """
    # The `judged`-only shape: nothing else. Both halves collide.
    bare = '{"judged": "%s", "findings": []}\n' % HEAD
    r1 = _round(tmp_path, "r1-same-head", HEAD,
                reports={"code-reviewer.json": bare})
    r2 = _round(tmp_path, "r2-same-head", HEAD,
                reports={"code-reviewer.json": bare,
                         "cross-examiner.json": bare})
    with pytest.raises(attest_mod.AttestError,
                       match="already claimed in this round"):
        attest_mod.carry_forward(_roster("code-reviewer", "cross-examiner"),
                                 r2)
    with pytest.raises(attest_mod.AttestError, match="carried forward"):
        attest_mod.carry_forward(_roster("code-reviewer"), r2)
    assert r1  # both rounds minted at one head; the sibling is the collision

    # The shipped shape. Same head, same empty findings, honest reports.
    def report(round_name: str, role: str) -> str:
        return ('{"judged": "%s", "round": "%s", "role": "%s", '
                '"findings": []}\n' % (HEAD, round_name, role))

    _round(tmp_path, "s1-same-head", HEAD,
           reports={"code-reviewer.json": report("s1-same-head",
                                                 "code-reviewer"),
                    "cross-examiner.json": report("s1-same-head",
                                                  "cross-examiner")})
    s2 = _round(tmp_path, "s2-same-head", HEAD,
                reports={"code-reviewer.json": report("s2-same-head",
                                                      "code-reviewer"),
                         "cross-examiner.json": report("s2-same-head",
                                                       "cross-examiner")})
    state, detail = attest_mod.carry_forward(
        _roster("code-reviewer", "cross-examiner"), s2)
    assert state == "distinct", detail
    assert "2 claimed report(s)" in detail


def test_r3_the_within_round_check_runs_on_an_unminted_dir(tmp_path):
    """The within-round duplicate check needs nothing from the round manifest,
    so being unable to identify the ROUND must not switch it off.

    `unminted` is a state the protocol blesses at exit 0 — the documented
    fallback for a warden that predates `round new`. If the early return for
    it happens before the claimed reports are hashed against each other, two
    roster entries backed by one `cp`-copied file are accepted there, and
    `verify_roster` cannot see it: its key is the inode, and `cp` makes two.
    That is the exact seam this function was added to close, switched off in
    the one state where nothing else is watching.
    """
    plain = tmp_path / "handed-over"
    plain.mkdir()
    (plain / "code-reviewer.json").write_text(REPORT_R1)
    (plain / "cross-examiner.json").write_text(REPORT_R1)   # `cp`: two inodes
    roster = _roster("code-reviewer", "cross-examiner")
    state, _ = attest_mod.verify_roster(roster, plain)
    assert state == attest_mod.ROSTER_VERIFIED, (
        "the roster check is expected to pass here — that is the gap this "
        "test exists to close, and if it started refusing this test no "
        "longer proves the content check is what closes it")
    with pytest.raises(attest_mod.AttestError) as e:
        attest_mod.carry_forward(roster, plain)
    assert "already claimed in this round" in str(e.value)


def test_r3_a_dangling_reviewers_link_refuses_rather_than_reading_as_absent(
        tmp_path):
    """`iterdir()` raises FileNotFoundError for a DANGLING symlink exactly as
    it does for a directory that is not there, and the two are opposite
    answers.

    Absence is not unreadability — a sibling with no `reviewers/` has nothing
    to have been copied from, and is skipped. A sibling whose `reviewers/` is
    a link to nothing cannot be examined, so it cannot be ruled out as the
    source, and the function's own contract calls that a refusal. Reading it
    as absence both accepts the copy and reports `0 sibling round(s)` — a
    partial scan that renders as a clean one.
    """
    r1 = _round(tmp_path, "r1-earlier", "c" * 40,
                reports={"code-reviewer.json": REPORT_R1})
    r2 = _round(tmp_path, "r2-this", HEAD,
                reports={"code-reviewer.json": REPORT_R1})
    with pytest.raises(attest_mod.AttestError, match="carried forward"):
        attest_mod.carry_forward(_roster("code-reviewer"), r2)
    r1.rename(r1.parent / "stashed")
    r1.symlink_to(r1.parent / "gone")          # dangles
    with pytest.raises(attest_mod.AttestError) as e:
        attest_mod.carry_forward(_roster("code-reviewer"), r2)
    assert "could not be" in str(e.value) and "r1-earlier" in str(e.value)


def test_an_unminted_dir_has_no_siblings_and_says_so(tmp_path):
    """A directory warden did not mint has no rounds root to compare against.
    That is recorded as `unminted` — the state round_binding already stamps —
    never as `distinct`, which would claim a comparison that never ran."""
    plain = tmp_path / "handed-over"
    plain.mkdir()
    (plain / "code-reviewer.json").write_text(REPORT_R1)
    state, _ = attest_mod.carry_forward(_roster("code-reviewer"), plain)
    assert state == "unminted"
    assert attest_mod.carry_forward([], None)[0] == "no-review-dir"


def test_an_unlistable_sibling_round_refuses_rather_than_passes(
        build, tmp_path):
    """Fail-closed within the evidence offered: a sibling whose reports cannot
    be read cannot be ruled out as the source of a copy. A check that cannot
    evaluate behaves like a hit."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    r1 = _round(tmp_path, "r1-earlier", "c" * 40,
                reports={"code-reviewer.json": REPORT_R1})
    r2 = _round(tmp_path, "r2-this", HEAD,
                reports={"code-reviewer.json":
                         '{"judged": "bbbbbbbbbbbb", "findings": []}\n'})
    r1.chmod(0)
    try:
        with pytest.raises(attest_mod.AttestError, match="could not be"):
            build({"reviewers": _roster("code-reviewer"), "findings": [],
                   "verdict": "clean"}, review_dir=r2)
    finally:
        r1.chmod(0o755)


def test_a_sibling_without_a_reviewers_dir_is_nothing_to_compare(
        build, tmp_path):
    """Absence is not unreadability: a sibling entry with no `reviewers/` —
    a round minted and abandoned, or a stray file under the rounds root — has
    no reports to have been copied from, and must not be reported as one that
    could not be read."""
    from warden import runs as runs_mod

    root = _rounds_root(tmp_path)
    abandoned = root / "r0-abandoned"
    abandoned.mkdir(parents=True)
    runs_mod.write_round_manifest(abandoned, base="main", base_sha=BASE,
                                  head="e" * 40, branch="mine", round_no=1)
    (root / "stray.txt").write_text("not a round\n")
    r2 = _round(tmp_path, "r2-this", HEAD,
                reports={"code-reviewer.json": REPORT_R1})
    doc = build({"reviewers": _roster("code-reviewer"), "findings": [],
                 "verdict": "clean"}, review_dir=r2)
    assert doc["round_binding"] == "bound"
    # THE READING, not only that `build` did not raise. The docstring's words
    # ("must not be reported as one that could not be read") are pinned by the
    # absence of a refusal; the claim in the test's own NAME — "is nothing to
    # compare" — is pinned only here, where adding `compared += 1` to the
    # skipped-sibling arm goes red. The detail string is what an auditor reads
    # to know how much of the rounds root was examined, and an inflated count
    # reads as a wider scan than ran.
    state, detail = attest_mod.carry_forward(_roster("code-reviewer"), r2)
    assert state == "distinct", state
    assert "against 0 sibling round(s)" in detail, (
        "an abandoned sibling and a stray file are counted as compared, so the "
        "evidence line claims a scan that did not happen: " + detail)
    # …and a sibling that DOES hold a report is counted, so the 0 above is a
    # count and not a constant.
    (abandoned / "reviewers").mkdir()
    (abandoned / "reviewers" / "cross-examiner.json").write_text(
        '{"judged": "dddddddddddd", "findings": []}\n')
    state, detail = attest_mod.carry_forward(_roster("code-reviewer"), r2)
    assert state == "distinct" and "against 1 sibling round(s)" in detail, detail


def test_a_symlink_to_the_round_under_attestation_is_not_a_sibling(
        build, tmp_path):
    """`carry_forward` follows symlinked siblings deliberately, so excluding the
    round under attestation by PATH equality is not enough: a link in the
    rounds root resolving BACK to that round would be walked as a sibling, its
    `reviewers/` would be this round's own, and every claimed report would
    match itself.

    The docstring's safety argument — "following it can only add a refusal" —
    holds only with that exclusion; without it, following adds a FALSE refusal
    of an honest round. Nothing in the tree creates the shape today; a
    convenience-symlink feature in a rounds root is precisely the kind of thing
    someone adds without thinking.
    """
    r2 = _round(tmp_path, "r2-this", HEAD,
                reports={"code-reviewer.json": REPORT_R1})
    # `_round` returns the `reviewers/` dir, so the ROUND is its parent and the
    # rounds root is the parent of that — which is what a convenience link
    # would be written into.
    round_dir, root = r2.parent, _rounds_root(tmp_path)
    # An honest, fully protocol-shaped round with no siblings at all.
    assert attest_mod.carry_forward(_roster("code-reviewer"), r2)[0] \
        == "distinct"
    for name, target in (("latest", round_dir),
                         ("relative", Path(round_dir.name))):
        link = root / name
        link.symlink_to(target)
        state, detail = attest_mod.carry_forward(_roster("code-reviewer"), r2)
        assert state == "distinct", (
            f"a {name} symlink pointing at the round under attestation is read "
            f"as a sibling holding a copy of its own reports: {state}")
        assert "against 0 sibling round(s)" in detail, detail
        link.unlink()
    # …and a symlink to a DIFFERENT round holding a copied report still
    # refuses, so the exclusion narrowed what it had to and nothing else.
    other = _round(tmp_path, "r1-earlier", "c" * 40,
                   reports={"code-reviewer.json": REPORT_R1})
    (root / "elsewhere").symlink_to(other.parent)
    with pytest.raises(attest_mod.AttestError, match="carried forward"):
        attest_mod.carry_forward(_roster("code-reviewer"), r2)


def test_r1_a_relative_review_dir_still_finds_the_rounds_root(
        build, tmp_path, monkeypatch):
    """`Path("reviewers").parent` is `.` and `.`'s parent is `.` again, so an
    unresolved one-component relative --review-dir would make the rounds root
    the round dir ITSELF — zero siblings, a copy accepted as `distinct`. The
    same family as `Path("")` being `.`, one seam over. The path is resolved
    before any parent of it is walked."""
    _round(tmp_path, "r1-earlier", "c" * 40,
           reports={"code-reviewer.json": REPORT_R1})
    r2 = _round(tmp_path, "r2-this", HEAD,
                reports={"code-reviewer.json": REPORT_R1})
    monkeypatch.chdir(r2.parent)
    with pytest.raises(attest_mod.AttestError, match="carried forward"):
        build({"reviewers": _roster("code-reviewer"), "findings": [],
               "verdict": "clean"}, review_dir=Path("reviewers"))


def test_r1_a_sibling_that_lists_but_cannot_be_searched_refuses(
        build, tmp_path):
    """Filtering entries with `is_file()`, which on CPython 3.13+ swallows
    EACCES and answers False, would let a sibling `reviewers/` at 0o444 list
    its names, read every name as "not a file", and COUNT as compared.
    `chmod(0)` fails at iterdir on every interpreter and cannot see it.
    Entries are examined with lstat, which raises on every version."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    r1 = _round(tmp_path, "r1-earlier", "c" * 40,
                reports={"code-reviewer.json": REPORT_R1})
    r2 = _round(tmp_path, "r2-this", HEAD,
                reports={"code-reviewer.json": REPORT_R1})
    r1.chmod(0o444)
    try:
        with pytest.raises(attest_mod.AttestError, match="could not be"):
            build({"reviewers": _roster("code-reviewer"), "findings": [],
                   "verdict": "clean"}, review_dir=r2)
    finally:
        r1.chmod(0o755)


def test_r1_a_sibling_report_that_cannot_be_read_refuses(build, tmp_path):
    """The read half of "listed or read", driven on its own: the directory is
    fine, the FILE is not. verify_roster never touches a sibling's files, so
    only this check can refuse here."""
    import os

    if os.geteuid() == 0:
        pytest.skip("root ignores file permissions")
    r1 = _round(tmp_path, "r1-earlier", "c" * 40,
                reports={"code-reviewer.json": REPORT_R1})
    r2 = _round(tmp_path, "r2-this", HEAD,
                reports={"code-reviewer.json":
                         '{"judged": "bbbbbbbbbbbb", "findings": []}\n'})
    (r1 / "code-reviewer.json").chmod(0)
    try:
        with pytest.raises(attest_mod.AttestError, match="could not be read"):
            build({"reviewers": _roster("code-reviewer"), "findings": [],
                   "verdict": "clean"}, review_dir=r2)
    finally:
        (r1 / "code-reviewer.json").chmod(0o644)


def test_r1_a_symlinked_sibling_round_is_compared_not_skipped(build,
                                                                   tmp_path):
    """A sibling that is a symlink must not be excluded silently, with the
    reading saying `0 sibling round(s)`. Comparing
    THROUGH it can only add refusals, never remove one, so it is compared; a
    link that dangles cannot be examined and refuses instead."""
    import os

    r1 = _round(tmp_path, "r1-earlier", "c" * 40,
                reports={"code-reviewer.json": REPORT_R1})
    r2 = _round(tmp_path, "r2-this", HEAD,
                reports={"code-reviewer.json": REPORT_R1})
    moved = tmp_path / "moved-r1"
    os.rename(r1.parent, moved)
    os.symlink(moved, r1.parent)
    with pytest.raises(attest_mod.AttestError, match="carried forward"):
        build({"reviewers": _roster("code-reviewer"), "findings": [],
               "verdict": "clean"}, review_dir=r2)
    os.unlink(r1.parent)
    os.symlink(tmp_path / "gone", r1.parent)
    with pytest.raises(attest_mod.AttestError, match="could not be examined"):
        build({"reviewers": _roster("code-reviewer"), "findings": [],
               "verdict": "clean"}, review_dir=r2)


# --- a reviewer that reviewed nothing is not a clean review -----

CHANGED = ("warden/attest.py", "tests/test_attest.py")


def _crew_with_outputs(review_dir: Path, outputs: dict[str, str],
                       findings: dict[str, int] | None = None,
                       outcome: dict[str, str] | None = None) -> list[dict]:
    """A roster whose OUTPUT TEXT is the test's subject."""
    out = []
    for role, text in outputs.items():
        name = f"{role}.json"
        (review_dir / name).write_text(text)
        entry = {"role": role, "agent": "claude-code subagent", "round": 1,
                 "returned": True, "findings": (findings or {}).get(role, 0),
                 "output": name}
        if outcome and role in outcome:
            entry["outcome"] = outcome[role]
        out.append(entry)
    return out


def _by_role(doc: dict) -> dict[str, dict]:
    return {r["role"]: r for r in doc["reviewers"]}


def test_a_refusal_text_output_attests_no_review_not_reviewed_clean(
        build, tmp_path):
    """THE HEADLINE CASE. A returned file carrying a refusal, a classifier
    decline, a truncated run, or a 'nothing to review' produced without
    reading the diff satisfies `returned: true` + a non-empty file exactly as
    a clean review does — and the review-depth ledger counted it as
    zero-yield, 'came back with nothing'. The observable that separates the
    two is whether the output names any file the range changed: a review
    that read nothing cannot cite what it read."""
    crew = _crew_with_outputs(tmp_path, {
        "code-reviewer": "I'm sorry, but I can't help with reviewing this.\n",
        "cross-examiner": '{"role": "cross-examiner", "reviewed": '
                          '["warden/attest.py"], "findings": []}\n'})
    doc = build({"reviewers": crew, "findings": [], "verdict": "clean"},
                review_dir=tmp_path, changed=CHANGED)
    roster = _by_role(doc)
    assert roster["code-reviewer"]["outcome"] == "no-review", roster
    assert roster["cross-examiner"]["outcome"] == "reviewed-clean", roster
    summary = attest_mod.render_summary(doc)
    assert "no-review" in summary and "reviewed-clean" in summary, summary


def test_a_declared_reviewed_clean_the_output_cannot_corroborate_is_refused(
        build, tmp_path):
    """Fail-closed on the CLAIM: a roster may say reviewed-clean, and warden
    refuses it when the output names no file from the diff. The derivation
    is silent where it can decide; the refusal is for a builder asserting
    what the artifact does not show."""
    crew = _crew_with_outputs(
        tmp_path, {"code-reviewer": "Nothing to review here.\n",
                   "cross-examiner": "read warden/attest.py: fine\n"},
        outcome={"code-reviewer": "reviewed-clean"})
    with pytest.raises(attest_mod.AttestError, match="cannot corroborate"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path, changed=CHANGED)


def test_findings_imply_reviewed_findings_and_a_contradiction_is_refused(
        build, tmp_path):
    crew = _crew_with_outputs(
        tmp_path, {"code-reviewer": "warden/attest.py:12 a finding\n",
                   "cross-examiner": "warden/attest.py: judged\n"},
        findings={"code-reviewer": 2})
    doc = build({"reviewers": crew, "findings": [], "verdict": "clean"},
                review_dir=tmp_path, changed=CHANGED)
    assert _by_role(doc)["code-reviewer"]["outcome"] == "reviewed-findings"
    crew = _crew_with_outputs(
        tmp_path, {"code-reviewer": "warden/attest.py:12 a finding\n",
                   "cross-examiner": "warden/attest.py: judged\n"},
        findings={"code-reviewer": 2},
        outcome={"code-reviewer": "reviewed-clean"})
    with pytest.raises(attest_mod.AttestError, match="contradicts"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path, changed=CHANGED)
    # a declared no-review always STANDS — the builder may lower an outcome
    # (a truncated run the text does not show), never raise one
    crew = _crew_with_outputs(
        tmp_path, {"code-reviewer": "warden/attest.py:12 a finding\n",
                   "cross-examiner": "warden/attest.py: judged\n"},
        findings={"code-reviewer": 2}, outcome={"code-reviewer": "no-review"})
    doc = build({"reviewers": crew, "findings": [], "verdict": "clean"},
                review_dir=tmp_path, changed=CHANGED)
    assert _by_role(doc)["code-reviewer"]["outcome"] == "no-review"


def test_a_dispatch_that_did_not_return_is_no_review(build, tmp_path):
    crew = _crew(tmp_path)
    crew.append({"role": "crew:enforcement-truth", "agent": "subagent",
                 "returned": False, "round": 1})
    doc = build({"reviewers": crew, "findings": [], "verdict": "clean"},
                review_dir=tmp_path, changed=CHANGED)
    assert _by_role(doc)["crew:enforcement-truth"]["outcome"] == "no-review"


def test_an_outcome_the_vocabulary_does_not_name_is_refused(build, tmp_path):
    crew = _crew_with_outputs(
        tmp_path, {"code-reviewer": "warden/attest.py ok\n",
                   "cross-examiner": "warden/attest.py judged ok\n"},
        outcome={"code-reviewer": "looked-fine"})
    with pytest.raises(attest_mod.AttestError, match="outcome"):
        build({"reviewers": crew, "findings": [], "verdict": "clean"},
              review_dir=tmp_path, changed=CHANGED)


def test_without_review_dir_an_outcome_is_carried_never_derived(build):
    """No review dir means no outputs to read: a declared outcome rides into
    the shard as the builder's word (the roster is `unverified-roster` there
    anyway), and an undeclared one stays ABSENT — absent is UNKNOWN, and no
    reader may upgrade it."""
    declared = [{"role": "code-reviewer", "agent": "subagent",
                 "returned": True, "findings": 0, "outcome": "no-review"},
                {"role": "cross-examiner", "agent": "subagent"}]
    doc = build({"reviewers": declared, "findings": [], "verdict": "clean"})
    roster = _by_role(doc)
    assert roster["code-reviewer"]["outcome"] == "no-review"
    assert "outcome" not in roster["cross-examiner"]


def test_the_outcome_vocabulary_is_read_from_the_schema():
    assert set(attest_mod.OUTCOMES) == {"reviewed-clean", "reviewed-findings",
                                         "no-review"}
