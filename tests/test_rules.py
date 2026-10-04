"""Rule loading, glob matching, and rules_version stability."""

from pathlib import Path

import pytest

from conftest import finishes_within
from warden import rules as rules_mod

VALID_RULE = """---
id: {rid}
severity: {sev}
engine: {engine}
applies_to: {globs}
---
Flag the thing. Evidence must quote the line.
"""


def _rule(tmp_path: Path, name: str, rid: str = "r1", sev: str = "HIGH",
          globs: str = '["**"]', engine: str = "claude") -> Path:
    p = tmp_path / name
    p.write_text(VALID_RULE.format(rid=rid, sev=sev, globs=globs, engine=engine))
    return p


class TestParsing:
    def test_valid_rule_parses(self, tmp_path):
        _rule(tmp_path, "a.md", rid="secrets", sev="MEDIUM", globs='["app/**", "db/**"]')
        (rule,) = rules_mod.load_rules(tmp_path)
        assert rule.id == "secrets"
        assert rule.severity == "MEDIUM"
        assert rule.applies_to == ("app/**", "db/**")
        assert "Flag the thing" in rule.body

    def test_missing_frontmatter_rejected(self, tmp_path):
        (tmp_path / "a.md").write_text("no frontmatter here")
        with pytest.raises(rules_mod.RuleError, match="frontmatter"):
            rules_mod.load_rules(tmp_path)

    def test_bad_severity_rejected(self, tmp_path):
        _rule(tmp_path, "a.md", sev="BLOCKER")
        with pytest.raises(rules_mod.RuleError, match="BLOCKER"):
            rules_mod.load_rules(tmp_path)

    def test_empty_body_rejected(self, tmp_path):
        (tmp_path / "a.md").write_text('---\nid: x\nseverity: HIGH\nengine: claude\napplies_to: ["**"]\n---\n')
        with pytest.raises(rules_mod.RuleError, match="empty"):
            rules_mod.load_rules(tmp_path)

    def test_duplicate_ids_rejected(self, tmp_path):
        _rule(tmp_path, "a.md", rid="same")
        _rule(tmp_path, "b.md", rid="same")
        with pytest.raises(rules_mod.RuleError, match="duplicate"):
            rules_mod.load_rules(tmp_path)

    def test_empty_dir_rejected(self, tmp_path):
        with pytest.raises(rules_mod.RuleError, match="no rule files"):
            rules_mod.load_rules(tmp_path)

    def test_non_string_applies_to_rejected(self, tmp_path):
        """A non-string glob must fail at load, not TypeError at match."""
        (tmp_path / "a.md").write_text(
            '---\nid: x\nseverity: HIGH\nengine: claude\napplies_to: [42]\n---\nBody.\n')
        with pytest.raises(rules_mod.RuleError, match="glob strings"):
            rules_mod.load_rules(tmp_path)

    def test_bom_rule_file_parses_regression(self, tmp_path):
        """A UTF-8 BOM must not defeat frontmatter detection."""
        (tmp_path / "a.md").write_bytes(
            b"\xef\xbb\xbf" + VALID_RULE.format(rid="r1", sev="HIGH", engine="claude",
                                                globs='["**"]').encode())
        (rule,) = rules_mod.load_rules(tmp_path)
        assert rule.id == "r1"


class TestGlobMatch:
    @pytest.mark.parametrize("pattern,path,expected", [
        ("**", "anything/at/all.py", True),
        ("app/**", "app/core/feeds.py", True),
        ("app/**", "app/x.py", True),
        ("app/**", "db/x.sql", False),
        ("app/core/secrets_surface.py", "app/core/secrets_surface.py", True),
        ("app/core/secrets_surface.py", "app/core/secrets.py", False),
        ("**/uv.lock", "app/uv.lock", True),
        ("**/uv.lock", "uv.lock", True),
        ("**/*.png", "assets/design/a.png", True),
        ("*.md", "README.md", True),
        ("*.md", "notes/README.md", False),
        ("db/migrations/**", "db/migrations/0022_x.sql", True),
        ("db/migrations/**", "db/functions/ask/index.ts", False),
    ])
    def test_matrix(self, pattern, path, expected):
        assert rules_mod.glob_match(pattern, path) is expected


class TestApplicable:
    def test_maps_rules_to_hit_files_and_omits_misses(self, tmp_path):
        _rule(tmp_path, "a.md", rid="py-only", globs='["app/**"]')
        _rule(tmp_path, "b.md", rid="sql-only", globs='["db/**"]')
        rules = rules_mod.load_rules(tmp_path)
        hits = rules_mod.applicable(rules, ["app/x.py", "notes/y.md"])
        assert hits == {"py-only": ["app/x.py"]}


class TestRulesVersion:
    def test_changes_when_any_rule_byte_changes(self, tmp_path):
        _rule(tmp_path, "a.md")
        before = rules_mod.rules_version(tmp_path, tmp_path)
        (tmp_path / "a.md").write_text((tmp_path / "a.md").read_text() + "\nMore.")
        assert rules_mod.rules_version(tmp_path, tmp_path) != before

    def test_changes_when_rule_added(self, tmp_path):
        _rule(tmp_path, "a.md")
        before = rules_mod.rules_version(tmp_path, tmp_path)
        _rule(tmp_path, "b.md", rid="r2")
        assert rules_mod.rules_version(tmp_path, tmp_path) != before

    def test_stable_across_calls(self, tmp_path):
        _rule(tmp_path, "a.md")
        assert (rules_mod.rules_version(tmp_path, tmp_path)
                == rules_mod.rules_version(tmp_path, tmp_path))
        assert len(rules_mod.rules_version(tmp_path, tmp_path)) == 12

    def test_file_boundary_collision_regression(self, tmp_path):
        """Distinct rulesets concatenating to identical bytes must never
        share a version — separators make file boundaries unambiguous."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir(), dir_b.mkdir()
        (dir_a / "a.md").write_text("XYZ")
        (dir_a / "b.md").write_text("Q")
        (dir_b / "a.md").write_text("XYZb.mdQ")
        assert (rules_mod.rules_version(dir_a, dir_a)
                != rules_mod.rules_version(dir_b, dir_b))


REPO_YAML = """\
repo: fixture
review:
  rules_dir: rules
  blocking_severities: [HIGH]
  context_excludes: ["*.lock"]
"""


class TestRulesVersionHashesEnforcement:
    """repo.yaml decides enforcement (blocking_severities turns a finding
    into exit 1; context_excludes decides what the gate sees), so two
    attestations sharing a rules_version must have been judged under the
    same repo.yaml enforcement keys."""

    def _fixture(self, tmp_path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        _rule(rules_dir, "a.md")
        (tmp_path / "repo.yaml").write_text(REPO_YAML)
        return rules_dir

    def test_blocking_severities_move_the_version(self, tmp_path):
        """Named regression: flipping [HIGH] -> [HIGH, MEDIUM] changes the
        verdict of every subsequent PR, so it must move rules_version."""
        rules_dir = self._fixture(tmp_path)
        before = rules_mod.rules_version(rules_dir, tmp_path)
        (tmp_path / "repo.yaml").write_text(REPO_YAML.replace(
            "[HIGH]", "[HIGH, MEDIUM]"))
        assert rules_mod.rules_version(rules_dir, tmp_path) != before

    def test_context_excludes_move_the_version(self, tmp_path):
        rules_dir = self._fixture(tmp_path)
        before = rules_mod.rules_version(rules_dir, tmp_path)
        (tmp_path / "repo.yaml").write_text(REPO_YAML.replace(
            '["*.lock"]', '["*.lock", "vendored/**"]'))
        assert rules_mod.rules_version(rules_dir, tmp_path) != before

    def test_unrelated_repo_yaml_edit_does_not_move_it(self, tmp_path):
        """The other half of the acceptance: a component added, a comment
        reflowed — anything outside review: — must NOT churn the cohort."""
        rules_dir = self._fixture(tmp_path)
        before = rules_mod.rules_version(rules_dir, tmp_path)
        (tmp_path / "repo.yaml").write_text(
            "# a comment\ncomponents:\n  app: {path: app}\n" + REPO_YAML)
        assert rules_mod.rules_version(rules_dir, tmp_path) == before

    def test_review_formatting_noise_does_not_move_it(self, tmp_path):
        """Canonicalized, not raw bytes: the same review: values spelled with
        different YAML whitespace are the same enforcement."""
        rules_dir = self._fixture(tmp_path)
        before = rules_mod.rules_version(rules_dir, tmp_path)
        (tmp_path / "repo.yaml").write_text(
            "repo: fixture\nreview:\n  rules_dir: rules\n"
            "  blocking_severities:\n    - HIGH\n"
            "  context_excludes:\n    - '*.lock'\n")
        assert rules_mod.rules_version(rules_dir, tmp_path) == before

    def test_presence_of_a_review_block_moves_it(self, tmp_path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        _rule(rules_dir, "a.md")
        bare = rules_mod.rules_version(rules_dir, tmp_path)
        (tmp_path / "repo.yaml").write_text(REPO_YAML)
        assert rules_mod.rules_version(rules_dir, tmp_path) != bare

    def test_unparseable_repo_yaml_fails_closed(self, tmp_path):
        """An unreadable enforcement declaration is hashed wholesale, never
        silently skipped — skipping would freeze the version while the file
        (and possibly the enforcement inside it) keeps changing."""
        rules_dir = self._fixture(tmp_path)
        parsed = rules_mod.rules_version(rules_dir, tmp_path)
        (tmp_path / "repo.yaml").write_text("review: [broken: {")
        broken_a = rules_mod.rules_version(rules_dir, tmp_path)
        (tmp_path / "repo.yaml").write_text("review: [broken2: {")
        broken_b = rules_mod.rules_version(rules_dir, tmp_path)
        assert len({parsed, broken_a, broken_b}) == 3

    def test_non_mapping_repo_yaml_is_malformed_not_absent(self, tmp_path):
        """A repo.yaml that parses to a list/scalar must not collapse into
        'declares no review block'. Malformed is its own hashed state,
        distinct from absent and from each other malformed content."""
        rules_dir = self._fixture(tmp_path)
        (tmp_path / "repo.yaml").write_text("just a scalar\n")
        scalar = rules_mod.rules_version(rules_dir, tmp_path)
        (tmp_path / "repo.yaml").write_text("- a\n- list\n")
        listy = rules_mod.rules_version(rules_dir, tmp_path)
        (tmp_path / "repo.yaml").write_text("repo: fixture\n")  # no review:
        absent = rules_mod.rules_version(rules_dir, tmp_path)
        assert len({scalar, listy, absent}) == 3

    def test_json_hostile_review_value_hashes_not_crashes(self, tmp_path):
        """An unquoted YAML date under review: must not raise TypeError from
        json.dumps, which would kill every rules_version caller. It must
        hash (fail closed), not crash — and a value change must still move
        the version."""
        rules_dir = self._fixture(tmp_path)
        (tmp_path / "repo.yaml").write_text(
            "review:\n  rules_dir: rules\n  since: 2026-01-01\n")
        a = rules_mod.rules_version(rules_dir, tmp_path)
        (tmp_path / "repo.yaml").write_text(
            "review:\n  rules_dir: rules\n  since: 2026-01-02\n")
        b = rules_mod.rules_version(rules_dir, tmp_path)
        assert a != b and len(a) == 12


class TestSampleRuleset:
    def test_sample_rules_load_with_expected_ids(self, sample_repo):
        rules = rules_mod.load_rules(sample_repo / ".warden" / "rules")
        ids = {r.id for r in rules}
        assert ids == {"secrets-in-diff", "prompt-eval-gate", "contract-freeze",
                       "migration-safety", "baseline-ratchet", "scope-creep",
                       "tests-required"}

    def test_sample_blocking_rules_are_the_mechanical_ones(self, sample_repo):
        rules = rules_mod.load_rules(sample_repo / ".warden" / "rules")
        high = {r.id for r in rules if r.severity == "HIGH"}
        assert high == {"secrets-in-diff", "prompt-eval-gate", "contract-freeze",
                        "migration-safety", "baseline-ratchet"}


# --- a repo.yaml that never finishes being read -----------------------------


def test_the_enforcement_surface_does_not_park_on_a_fifo(tmp_path,
                                                             make_fifo):
    """`_enforcement_surface` promises that "cannot look" and "absent" stay
    distinct, and its own docstring says the None comes from the READ, "never
    a stat that would collapse 'cannot look' into 'absent'". On a FIFO that
    read never returns, and the promise has to hold for inputs that do not
    come back too. A FIFO is unreadable, so it hashes as `unreadable` —
    distinct from the None that means no repo.yaml exists at all."""
    make_fifo(tmp_path / "repo.yaml")

    assert finishes_within(5, rules_mod._enforcement_surface, tmp_path) \
        == b"unreadable"


def test_rules_version_still_computes_over_an_unreadable_repo_yaml(
        tmp_path, make_fifo):
    """The caller that matters: every command hashes the ruleset before it
    does anything, so a version that cannot be computed is a command that
    never starts."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    _rule(rules_dir, "a.md")
    make_fifo(tmp_path / "repo.yaml")

    version = finishes_within(5, rules_mod.rules_version, rules_dir, tmp_path)
    assert len(version) == 12, version


class TestJudges:
    """A rule declares whether it judges behaviour or wording; the default is
    behaviour, so a rule that says nothing keeps counting a repair round."""

    def _write(self, tmp_path: Path, extra: str) -> Path:
        p = tmp_path / "a.md"
        p.write_text("---\nid: r1\nseverity: MEDIUM\nengine: claude\n"
                     f"applies_to: [\"**\"]\n{extra}---\nFlag the thing.\n")
        return p

    @pytest.mark.parametrize("kind", ["behaviour", "wording"])
    def test_a_declared_judges_value_loads(self, tmp_path, kind):
        self._write(tmp_path, f"judges: {kind}\n")
        (rule,) = rules_mod.load_rules(tmp_path)
        assert rule.judges == kind

    def test_a_rule_that_declares_no_judges_is_behaviour(self, tmp_path):
        self._write(tmp_path, "")
        (rule,) = rules_mod.load_rules(tmp_path)
        assert rule.judges == "behaviour"

    @pytest.mark.parametrize("value", ["style", "Wording", "true", "[wording]"])
    def test_an_unknown_judges_value_is_refused_naming_the_file(self, tmp_path,
                                                                value):
        path = self._write(tmp_path, f"judges: {value}\n")
        with pytest.raises(rules_mod.RuleError, match="judges") as exc:
            rules_mod.load_rules(tmp_path)
        assert str(path) in str(exc.value)

    def test_every_ai_judged_rule_in_this_repo_declares_what_it_judges(self):
        rules_dir = Path(__file__).parent.parent / ".warden" / "rules"
        for rule in rules_mod.load_rules(rules_dir):
            if rule.engine != "claude":
                continue
            head = rule.path.read_text().split("\n---\n", 1)[0]
            assert any(line.startswith("judges:") for line in head.splitlines()), (
                f"{rule.path.name} is engine:claude and declares no judges:")

    @staticmethod
    def _assert_wording_rules_are_exactly_the_expected_set(rules_dir: Path):
        wording = {r.id for r in rules_mod.load_rules(rules_dir)
                   if r.engine == "claude" and r.judges == "wording"}
        assert wording == {"wiki-fidelity", "rename-complete"}, (
            f"the rules judging wording are {sorted(wording)}; only "
            "wiki-fidelity and rename-complete may, because a wording finding "
            "below the blocking severities does not count a repair round")

    def test_only_wiki_fidelity_and_rename_complete_judge_wording(self):
        self._assert_wording_rules_are_exactly_the_expected_set(
            Path(__file__).parent.parent / ".warden" / "rules")

    def test_flipping_any_other_ai_judged_rule_to_wording_fails_the_pin(
            self, tmp_path):
        import shutil
        src = Path(__file__).parent.parent / ".warden" / "rules"
        copy = tmp_path / "rules"
        shutil.copytree(src, copy)
        behaviour = [r for r in rules_mod.load_rules(copy)
                     if r.engine == "claude" and r.judges == "behaviour"]
        assert behaviour
        for rule in behaviour:
            original = rule.path.read_text()
            rule.path.write_text(original.replace(
                "\njudges: behaviour\n", "\njudges: wording\n", 1))
            with pytest.raises(AssertionError, match=rule.id):
                self._assert_wording_rules_are_exactly_the_expected_set(copy)
            rule.path.write_text(original)
