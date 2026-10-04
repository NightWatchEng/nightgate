"""Deterministic checkers: violating diffs yield findings, clean diffs don't."""

from warden import mechanical
from warden.diffs import DiffContext


def _ctx(files: dict[str, list[tuple[int, str]]],
         removed: dict[str, list[str]] | None = None,
         base_files: dict[str, str] | None = None,
         head_files: dict[str, str] | None = None,
         all_files: tuple[str, ...] | None = None) -> DiffContext:
    base_files = base_files or {}
    head_files = head_files or {}
    # all_files defaults to the scanned files; pass it explicitly to model a
    # companion tree (e.g. tests/) that changed but is context-excluded from
    # scanning.
    return DiffContext(
        base="a" * 40, head="b" * 40,
        files=tuple(files),
        added={name: tuple(lines) for name, lines in files.items()},
        removed={name: tuple(lines) for name, lines in (removed or {}).items()},
        read_base=lambda p: base_files.get(p),
        read_head=lambda p: head_files.get(p),
        all_files=all_files if all_files is not None else tuple(files),
    )


class TestSecrets:
    def test_anthropic_key_flagged(self):
        ctx = _ctx({"pkg/config.py": [(3, 'KEY = "sk-ant-api03-realish1234"')]})
        (f,) = mechanical.check_secrets(ctx, {}, ["pkg/config.py"])
        assert f["line"] == 3
        assert "Anthropic" in f["finding"]

    def test_dsn_with_password_flagged(self):
        ctx = _ctx({"a.py": [(1, 'DSN = "postgresql://app:hunter2@db:5432/x"')]})
        assert mechanical.check_secrets(ctx, {}, ["a.py"])

    def test_credential_assignment_flagged(self):
        ctx = _ctx({"a.py": [(1, 'api_key = "abcdefghij0123456789"')]})
        assert mechanical.check_secrets(ctx, {}, ["a.py"])

    def test_placeholder_not_flagged(self):
        ctx = _ctx({"a.py": [(1, 'KEY = "sk-ant-xxxxxxxxxxxx"'),
                             (2, 'TOKEN = "<YOUR_KEY_HERE>"')]})
        assert mechanical.check_secrets(ctx, {}, ["a.py"]) == []

    def test_env_read_not_flagged(self):
        ctx = _ctx({"a.py": [(1, 'KEY = os.environ["ANTHROPIC_API_KEY"]')]})
        assert mechanical.check_secrets(ctx, {}, ["a.py"]) == []

    def test_ios_userdefaults_secret_flagged(self):
        ctx = _ctx({"app/ui/A.swift": [(9, 'UserDefaults.standard.set(token, forKey: "authToken")')]})
        (f,) = mechanical.check_secrets(ctx, {}, ["app/ui/A.swift"])
        assert "Keychain" in f["finding"]

    def test_userdefaults_non_secret_not_flagged(self):
        ctx = _ctx({"app/ui/A.swift": [(9, 'UserDefaults.standard.set(count, forKey: "briefOpens")')]})
        assert mechanical.check_secrets(ctx, {}, ["app/ui/A.swift"]) == []


PROMPT_PARAMS = {"surface_symbols": ["PROMPT_TEXT", "JSON_SUFFIX", "_wire_schema"],
                 "companion_file": "db/BASELINE"}


class TestPromptEvalGate:
    def test_surface_change_without_baseline_flagged(self):
        ctx = _ctx({"app/prompt.py": [(10, 'PROMPT_TEXT = "new text"')]})
        (f,) = mechanical.check_prompt_eval_gate(
            ctx, PROMPT_PARAMS, ["app/prompt.py"])
        assert "BASELINE" in f["finding"]

    def test_removed_line_counts_as_surface_change(self):
        ctx = _ctx({"app/gen.py": [(5, "unrelated = 1")]},
                   removed={"app/gen.py": ["JSON_SUFFIX = old"]})
        assert mechanical.check_prompt_eval_gate(
            ctx, PROMPT_PARAMS, ["app/gen.py"])

    def test_with_baseline_in_diff_not_flagged(self):
        files = {"app/prompt.py": [(10, 'PROMPT_TEXT = "new"')],
                 "db/BASELINE": [(1, "0.96")]}
        ctx = _ctx(files)
        assert mechanical.check_prompt_eval_gate(
            ctx, PROMPT_PARAMS, ["app/prompt.py"]) == []

    def test_non_surface_edit_not_flagged(self):
        ctx = _ctx({"app/gen.py": [(80, "cost = tokens * price")]})
        assert mechanical.check_prompt_eval_gate(
            ctx, PROMPT_PARAMS, ["app/gen.py"]) == []


RATCHET_PARAMS = {"justification_globs": ["app/evals/golden/**",
                                          "app/prompt.py"]}
BASELINE = "db/BASELINE"


class TestBaselineRatchet:
    def test_lowered_flagged(self):
        ctx = _ctx({BASELINE: [(1, "0.90")]},
                   base_files={BASELINE: "0.951\n"}, head_files={BASELINE: "0.90\n"})
        (f,) = mechanical.check_baseline_ratchet(ctx, RATCHET_PARAMS, [BASELINE])
        assert "only rise" in f["finding"]

    def test_raised_without_justification_flagged(self):
        ctx = _ctx({BASELINE: [(1, "0.97")]},
                   base_files={BASELINE: "0.951\n"}, head_files={BASELINE: "0.97\n"})
        (f,) = mechanical.check_baseline_ratchet(ctx, RATCHET_PARAMS, [BASELINE])
        assert "justify" in f["finding"]

    def test_raised_with_golden_change_not_flagged(self):
        files = {BASELINE: [(1, "0.97")],
                 "app/evals/golden/new-pair.json": [(1, "{}")]}
        ctx = _ctx(files, base_files={BASELINE: "0.951\n"},
                   head_files={BASELINE: "0.97\n"})
        assert mechanical.check_baseline_ratchet(ctx, RATCHET_PARAMS, [BASELINE]) == []

    def test_unparseable_flagged(self):
        ctx = _ctx({BASELINE: [(1, "high enough")]},
                   base_files={BASELINE: "0.951\n"},
                   head_files={BASELINE: "high enough\n"})
        assert mechanical.check_baseline_ratchet(ctx, RATCHET_PARAMS, [BASELINE])


SCHEMA = "schemas/api.schema.json"
FREEZE_PARAMS = {"schema_file": SCHEMA,
                 "fixture_files": ["tests/fixtures/f1.json",
                                   "tests/fixtures/f2.json"]}


def _freeze_ctx(old_schema: dict, new_schema: dict, extra_files: list[str] = ()):
    import json
    files = {SCHEMA: [(1, "{}")]}
    for f in extra_files:
        files[f] = [(1, "{}")]
    return _ctx(files, base_files={SCHEMA: json.dumps(old_schema)},
                head_files={SCHEMA: json.dumps(new_schema)})


class TestContractFreeze:
    def test_fixtures_missing_flagged(self):
        ctx = _freeze_ctx({"type": "object"}, {"type": "object"})
        findings = mechanical.check_contract_freeze(ctx, FREEZE_PARAMS, [SCHEMA])
        assert any("fixtures did not move" in f["finding"] for f in findings)

    def test_new_required_field_is_breaking(self):
        old = {"type": "object", "required": ["a"],
               "properties": {"a": {"type": "string"}, "b": {"type": "string"}}}
        new = {"type": "object", "required": ["a", "b"],
               "properties": {"a": {"type": "string"}, "b": {"type": "string"}}}
        ctx = _freeze_ctx(old, new, extra_files=FREEZE_PARAMS["fixture_files"])
        findings = mechanical.check_contract_freeze(ctx, FREEZE_PARAMS, [SCHEMA])
        assert any("became required" in f["finding"] for f in findings)

    def test_enum_removal_is_breaking(self):
        old = {"properties": {"verdict": {"enum": ["YES", "PARTLY", "NO"]}}}
        new = {"properties": {"verdict": {"enum": ["YES", "NO"]}}}
        ctx = _freeze_ctx(old, new, extra_files=FREEZE_PARAMS["fixture_files"])
        findings = mechanical.check_contract_freeze(ctx, FREEZE_PARAMS, [SCHEMA])
        assert any("'PARTLY' removed" in f["finding"] for f in findings)

    def test_additive_optional_with_fixtures_clean(self):
        old = {"type": "object", "properties": {"a": {"type": "string"}}}
        new = {"type": "object", "properties": {"a": {"type": "string"},
                                                "b": {"type": "integer"}}}
        ctx = _freeze_ctx(old, new, extra_files=FREEZE_PARAMS["fixture_files"])
        assert mechanical.check_contract_freeze(ctx, FREEZE_PARAMS, [SCHEMA]) == []


MIG = "db/migrations/0025_new.sql"


class TestMigrationSafety:
    def test_edit_to_existing_migration_flagged(self):
        ctx = _ctx({MIG: [(3, "ALTER TABLE x ADD COLUMN y int;")]},
                   base_files={MIG: "old content"},
                   head_files={MIG: "new content"})
        (f,) = mechanical.check_migration_safety(ctx, {}, [MIG])
        assert "immutable" in f["finding"]

    def test_destructive_ddl_flagged(self):
        ctx = _ctx({MIG: [(1, "DROP TABLE users;")]},
                   head_files={MIG: "DROP TABLE users;\n"})
        findings = mechanical.check_migration_safety(ctx, {}, [MIG])
        assert any("Destructive" in f["finding"] for f in findings)

    def test_allow_marker_suppresses_destructive(self):
        content = "-- warden:allow-destructive dropping unused staging table\nDROP TABLE staging_tmp;\n"
        ctx = _ctx({MIG: [(2, "DROP TABLE staging_tmp;")]}, head_files={MIG: content})
        assert mechanical.check_migration_safety(ctx, {}, [MIG]) == []

    def test_delete_without_where_flagged(self):
        content = "DELETE FROM events;\n"
        ctx = _ctx({MIG: [(1, content)]}, head_files={MIG: content})
        findings = mechanical.check_migration_safety(ctx, {}, [MIG])
        assert any("WHERE" in f["finding"] for f in findings)

    def test_delete_with_where_clean(self):
        content = "DELETE FROM events WHERE ts < now() - interval '90 days';\nALTER TABLE events ENABLE ROW LEVEL SECURITY;\n"
        ctx = _ctx({MIG: [(1, content)]}, head_files={MIG: content})
        assert mechanical.check_migration_safety(ctx, {}, [MIG]) == []

    def test_create_table_without_rls_flagged(self):
        content = "CREATE TABLE trends (id uuid);\n"
        ctx = _ctx({MIG: [(1, content)]}, head_files={MIG: content})
        findings = mechanical.check_migration_safety(ctx, {}, [MIG])
        assert any("ROW LEVEL SECURITY" in f["finding"] for f in findings)

    def test_create_table_with_rls_clean(self):
        content = ("CREATE TABLE trends (id uuid);\n"
                   "ALTER TABLE trends ENABLE ROW LEVEL SECURITY;\n"
                   "CREATE POLICY own ON trends FOR SELECT USING (true);\n")
        ctx = _ctx({MIG: [(1, content)]}, head_files={MIG: content})
        assert mechanical.check_migration_safety(ctx, {}, [MIG]) == []


class TestReviewRoundG2Regressions:
    """Named regressions across the checkers: baseline ratchet, prompt
    segments, secret markers, migration safety and schema breaks."""

    def test_g2_3_nan_baseline_rejected(self):
        ctx = _ctx({BASELINE: [(1, "nan")]},
                   base_files={BASELINE: "0.95\n"}, head_files={BASELINE: "nan\n"})
        (f,) = mechanical.check_baseline_ratchet(ctx, RATCHET_PARAMS, [BASELINE])
        assert "parseable" in f["finding"]

    def test_g2_3_nan_old_baseline_still_guards(self):
        ctx = _ctx({BASELINE: [(1, "0.01")]},
                   base_files={BASELINE: "nan\n"}, head_files={BASELINE: "0.01\n"})
        # old side unparseable -> treated as absent; no silent all-comparisons-False
        assert mechanical.check_baseline_ratchet(ctx, RATCHET_PARAMS, [BASELINE]) == []

    def test_g2_4_interior_prompt_edit_caught(self):
        old = 'X = 1\nJSON_SUFFIX = """\nline one\nold interior\n"""\nY = 2\n'
        new = 'X = 1\nJSON_SUFFIX = """\nline one\nNEW interior\n"""\nY = 2\n'
        f = "app/gen.py"
        ctx = _ctx({f: [(4, "NEW interior")]},
                   base_files={f: old}, head_files={f: new})
        (finding,) = mechanical.check_prompt_eval_gate(ctx, PROMPT_PARAMS, [f])
        assert "JSON_SUFFIX" in finding["finding"]

    def test_g2_4_unrelated_edit_outside_segments_clean(self):
        old = 'X = 1\nJSON_SUFFIX = """same"""\nY = 2\n'
        new = 'X = 99\nJSON_SUFFIX = """same"""\nY = 2\n'
        f = "app/gen.py"
        ctx = _ctx({f: [(1, "X = 99")]}, base_files={f: old}, head_files={f: new})
        assert mechanical.check_prompt_eval_gate(ctx, PROMPT_PARAMS, [f]) == []

    def test_g2_5_real_key_next_to_placeholder_text_flagged(self):
        line = 'url = "https://example.com"; KEY = "sk-ant-api03-Zx9Yw8Vu7Tt6"'
        ctx = _ctx({"a.py": [(1, line)]})
        (f,) = mechanical.check_secrets(ctx, {}, ["a.py"])
        assert "Anthropic" in f["finding"]

    def test_g2_6_marker_scopes_to_next_statement_only(self):
        content = ("-- warden:allow-destructive dropping staging table\n"
                   "DROP TABLE staging_tmp;\n"
                   "TRUNCATE users;\n")
        ctx = _ctx({MIG: [(2, "x")]}, head_files={MIG: content})
        findings = mechanical.check_migration_safety(ctx, {}, [MIG])
        assert len(findings) == 1
        assert "TRUNCATE" in findings[0]["evidence"].upper()

    def test_g2_6_bare_marker_without_reason_does_not_suppress(self):
        content = "-- warden:allow-destructive\nDROP TABLE x;\n"
        ctx = _ctx({MIG: [(2, "x")]}, head_files={MIG: content})
        assert mechanical.check_migration_safety(ctx, {}, [MIG])

    def test_g2_7_delete_at_eof_without_semicolon_flagged(self):
        content = "DELETE FROM events"
        ctx = _ctx({MIG: [(1, content)]}, head_files={MIG: content})
        findings = mechanical.check_migration_safety(ctx, {}, [MIG])
        assert any("WHERE" in f["finding"] for f in findings)

    def test_g2_7_where_in_comment_does_not_suppress(self):
        content = "DELETE FROM cards; -- where needed later\n"
        ctx = _ctx({MIG: [(1, content)]}, head_files={MIG: content})
        findings = mechanical.check_migration_safety(ctx, {}, [MIG])
        assert any("WHERE" in f["finding"] for f in findings)

    def test_g2_7_where_inside_identifier_does_not_suppress(self):
        content = "DELETE FROM everywhere_temp;\n"
        ctx = _ctx({MIG: [(1, content)]}, head_files={MIG: content})
        findings = mechanical.check_migration_safety(ctx, {}, [MIG])
        assert any("WHERE" in f["finding"] for f in findings)

    def test_g2_8_min_items_raise_is_breaking(self):
        old = {"properties": {"insights": {"minItems": 3, "maxItems": 8}}}
        new = {"properties": {"insights": {"minItems": 4, "maxItems": 8}}}
        ctx = _freeze_ctx(old, new, extra_files=FREEZE_PARAMS["fixture_files"])
        findings = mechanical.check_contract_freeze(ctx, FREEZE_PARAMS, [SCHEMA])
        assert any("minItems 3 -> 4" in f["finding"] for f in findings)

    def test_g2_8_not_null_without_default_flagged(self):
        content = "ALTER TABLE cards ADD COLUMN rank int NOT NULL;\n"
        ctx = _ctx({MIG: [(1, content)]}, head_files={MIG: content})
        findings = mechanical.check_migration_safety(ctx, {}, [MIG])
        assert any("NOT NULL" in f["finding"] for f in findings)

    def test_g2_8_not_null_with_default_clean(self):
        content = "ALTER TABLE cards ADD COLUMN rank int NOT NULL DEFAULT 0;\n"
        ctx = _ctx({MIG: [(1, content)]}, head_files={MIG: content})
        assert mechanical.check_migration_safety(ctx, {}, [MIG]) == []


class TestTestsAccompanyLogic:
    """The MECHANICAL half of tests-required, as a checker — added logic
    under warden/ with no change under tests/ in the same diff. The
    regression-NAMING half stays engine:claude."""

    P = {"companion_prefix": "tests/", "exempt_globs": []}

    def test_added_def_without_tests_change_flags(self):
        ctx = _ctx({"warden/x.py": [(1, "def foo(a):")]})
        (f,) = mechanical.check_tests_accompany_logic(ctx, self.P, ["warden/x.py"])
        assert "warden/x.py" in f["file"]
        assert "warden/x.py:1" in f["evidence"]

    def test_added_branching_without_tests_change_flags(self):
        ctx = _ctx({"warden/x.py": [(5, "    if user_input:")]})
        assert mechanical.check_tests_accompany_logic(ctx, self.P, ["warden/x.py"])

    def test_companion_in_files_silences_it(self):
        ctx = _ctx({"warden/x.py": [(1, "def foo():")],
                    "tests/test_x.py": [(1, "def test_foo():")]})
        assert mechanical.check_tests_accompany_logic(
            ctx, self.P, ["warden/x.py"]) == []

    def test_companion_seen_via_all_files_when_context_excluded(self):
        # tests/ is context-excluded, so it is NOT in ctx.files, but it IS in
        # ctx.all_files. The checker must read all_files, or it would fire on
        # every warden/ PR.
        ctx = _ctx({"warden/x.py": [(1, "def foo():")]},
                   all_files=("warden/x.py", "tests/test_x.py"))
        assert mechanical.check_tests_accompany_logic(
            ctx, self.P, ["warden/x.py"]) == []

    def test_all_files_without_companion_still_flags(self):
        ctx = _ctx({"warden/x.py": [(1, "def foo():")]},
                   all_files=("warden/x.py", "docs/readme.md"))
        assert mechanical.check_tests_accompany_logic(ctx, self.P, ["warden/x.py"])

    def test_non_logic_addition_is_silent(self):
        ctx = _ctx({"warden/x.py": [(1, "MAX = 3"), (2, "import os")]})
        assert mechanical.check_tests_accompany_logic(
            ctx, self.P, ["warden/x.py"]) == []

    def test_a_comment_that_mentions_if_is_not_logic(self):
        ctx = _ctx({"warden/x.py": [(1, "# if this ever changes, add a test")]})
        assert mechanical.check_tests_accompany_logic(
            ctx, self.P, ["warden/x.py"]) == []

    def test_a_string_literal_containing_def_is_not_logic(self):
        ctx = _ctx({"warden/x.py": [(1, '    msg = "def not a function"')]})
        assert mechanical.check_tests_accompany_logic(
            ctx, self.P, ["warden/x.py"]) == []

    def test_exempt_glob_is_skipped(self):
        ctx = _ctx({"warden/schemas/gen.py": [(1, "def build():")]})
        p = {"companion_prefix": "tests/", "exempt_globs": ["warden/schemas/**"]}
        assert mechanical.check_tests_accompany_logic(
            ctx, p, ["warden/schemas/gen.py"]) == []

    def test_one_finding_per_file_with_added_logic(self):
        ctx = _ctx({"warden/a.py": [(1, "def a():")],
                    "warden/b.py": [(1, "def b():")]})
        fs = mechanical.check_tests_accompany_logic(
            ctx, self.P, ["warden/a.py", "warden/b.py"])
        assert {f["file"] for f in fs} == {"warden/a.py", "warden/b.py"}

    def test_async_def_counts_as_logic(self):
        ctx = _ctx({"warden/x.py": [(1, "async def fetch():")]})
        assert mechanical.check_tests_accompany_logic(ctx, self.P, ["warden/x.py"])


class TestTestsAccompanyLogicResidual:
    """The checker reads ADDED LINES, not parsed code, so a logic keyword at
    the start of a multi-line-STRING body (a prompt or docstring line) is a
    KNOWN false positive. Pinned here so the coverage matches reality — the
    mid-line-string test alone would give false comfort — and the rule body
    discloses it; the residual is accepted because the rule is
    MEDIUM/non-blocking and its FPs are dismissed in review."""

    P = {"companion_prefix": "tests/", "exempt_globs": []}

    def test_a_logic_keyword_starting_a_string_body_is_a_known_false_positive(self):
        # this line is the body of a triple-quoted GUIDE = \"\"\"...\"\"\", not code
        ctx = _ctx({"warden/prompts.py": [(2, "def your function like this:")]})
        fs = mechanical.check_tests_accompany_logic(ctx, self.P, ["warden/prompts.py"])
        # documents the residual: it DOES fire (a limitation, not a guarantee)
        assert fs, "if this becomes silent, the FP was fixed — update the rule body"
        assert "warden/prompts.py:2" in fs[0]["evidence"]


def test_case_soft_keyword_identifier_does_not_fire():
    """`case` is a SOFT keyword; `case = 5` and
    `case: int = 5` are identifiers, not match arms, and must not fire."""
    from warden import mechanical as m
    P = {"companion_prefix": "tests/", "exempt_globs": []}
    for line in ("    case = 5", "    case: int = 5"):
        ctx = _ctx({"warden/x.py": [(1, line)]})
        assert m.check_tests_accompany_logic(ctx, P, ["warden/x.py"]) == [], line
