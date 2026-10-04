"""engine:declarative — mechanical rules a project owns with zero Python.

The contract: a typo in a declarative check fails at LOAD time (enrollment),
never silently at 3am; and a rule file's checks are policy because rule files
are already hashed into rules_version.
"""

from pathlib import Path

import pytest

from warden import declarative
from warden import rules as rules_mod
from warden.diffs import DiffContext

RULE = """\
---
id: no-debug
severity: HIGH
engine: declarative
applies_to: ["src/**/*.py"]
checks:
  - id: no-breakpoint
    pattern: 'breakpoint\\('
    message: "breakpoint() left in the diff"
---
Debug hooks must never ship.
"""


def write_rule(tmp_path: Path, body: str = RULE, name: str = "no-debug.md") -> Path:
    d = tmp_path / "rules"
    d.mkdir(exist_ok=True)
    (d / name).write_text(body)
    return d


def ctx_for(added=None, head=None) -> DiffContext:
    added = added or {}
    head = head or {}
    return DiffContext(
        base="a" * 40, head="b" * 40, files=tuple(set(added) | set(head)),
        added={k: tuple(v) for k, v in added.items()}, removed={},
        read_base=lambda f: None, read_head=lambda f: head.get(f))


# ---------- load-time validation (the whole point) ---------------------------

def test_declarative_rule_loads_and_carries_its_checks(tmp_path):
    rules = rules_mod.load_rules(write_rule(tmp_path))
    assert rules[0].engine == "declarative"
    assert rules[0].checks[0]["id"] == "no-breakpoint"


def test_declarative_rule_needs_a_checker_free_registry(tmp_path):
    # engine:declarative must NOT require a registry entry — that is the
    # entire enrollment-cost saving.
    from warden.review import validate_registry
    validate_registry(rules_mod.load_rules(write_rule(tmp_path)), {})


@pytest.mark.parametrize("mutation, fragment", [
    ("no-checks", "needs a non-empty"),
    ("bad-pattern", "does not compile"),
    ("missing-message", "non-empty 'message'"),
    ("unknown-key", "unknown check keys"),
    ("bad-scope", "scope"),
    ("bad-flag", "flags must be a subset"),
    ("require-without-file-scope", "require:true needs scope:file"),
    ("duplicate-id", "duplicate check id"),
    ("checks-on-python-engine", "only meaningful for"),
])
def test_bad_declarative_rules_fail_at_load(tmp_path, mutation, fragment):
    body = RULE
    if mutation == "no-checks":
        body = body.replace("""checks:
  - id: no-breakpoint
    pattern: 'breakpoint\\('
    message: "breakpoint() left in the diff"
""", "")
    elif mutation == "bad-pattern":
        body = body.replace("'breakpoint\\('", "'([unclosed'")
    elif mutation == "missing-message":
        body = body.replace('    message: "breakpoint() left in the diff"\n', "")
    elif mutation == "unknown-key":
        body = body.replace("    message:", "    sevrity: HIGH\n    message:")
    elif mutation == "bad-scope":
        body = body.replace("    message:", "    scope: everywhere\n    message:")
    elif mutation == "bad-flag":
        body = body.replace("    message:", "    flags: [x]\n    message:")
    elif mutation == "require-without-file-scope":
        body = body.replace("    message:", "    require: true\n    message:")
    elif mutation == "duplicate-id":
        body = body.replace("---\nDebug hooks",
                            "  - id: no-breakpoint\n    pattern: 'pdb'\n"
                            "    message: \"pdb\"\n---\nDebug hooks")
    elif mutation == "checks-on-python-engine":
        body = body.replace("engine: declarative", "engine: python")
    with pytest.raises(rules_mod.RuleError) as exc:
        rules_mod.load_rules(write_rule(tmp_path, body))
    assert fragment in str(exc.value)


# ---------- execution --------------------------------------------------------

def test_added_scope_flags_only_added_lines(tmp_path):
    rule = rules_mod.load_rules(write_rule(tmp_path))[0]
    ctx = ctx_for(added={"src/a.py": [(3, "    breakpoint()"), (4, "    ok()")]})
    found = declarative.run(rule, ctx, ["src/a.py"])
    assert len(found) == 1
    assert found[0]["line"] == 3
    assert "no-breakpoint" in found[0]["finding"]
    assert found[0]["evidence"] == "breakpoint()"


def test_globs_narrow_within_applies_to(tmp_path):
    body = RULE.replace("    message:", '    globs: ["src/api/**"]\n    message:')
    rule = rules_mod.load_rules(write_rule(tmp_path, body))[0]
    ctx = ctx_for(added={"src/api/x.py": [(1, "breakpoint()")],
                         "src/cli/y.py": [(1, "breakpoint()")]})
    found = declarative.run(rule, ctx, ["src/api/x.py", "src/cli/y.py"])
    assert [f["file"] for f in found] == ["src/api/x.py"]


def test_file_scope_matches_untouched_content_with_line_number(tmp_path):
    body = RULE.replace("    message:", "    scope: file\n    message:")
    rule = rules_mod.load_rules(write_rule(tmp_path, body))[0]
    ctx = ctx_for(added={"src/a.py": [(9, "# unrelated added line")]},
                  head={"src/a.py": "one\ntwo\nbreakpoint()\nfour\n"})
    found = declarative.run(rule, ctx, ["src/a.py"])
    assert len(found) == 1 and found[0]["line"] == 3


def test_require_flags_absence(tmp_path):
    body = """\
---
id: license-header
severity: MEDIUM
engine: declarative
applies_to: ["src/**/*.py"]
checks:
  - id: has-license
    pattern: 'SPDX-License-Identifier'
    scope: file
    require: true
    message: "file is missing its SPDX license header"
---
Every source file states its license.
"""
    rule = rules_mod.load_rules(write_rule(tmp_path, body, "license.md"))[0]
    ctx = ctx_for(head={"src/good.py": "# SPDX-License-Identifier: MIT\n",
                        "src/bad.py": "print(1)\n"})
    found = declarative.run(rule, ctx, ["src/good.py", "src/bad.py"])
    assert [f["file"] for f in found] == ["src/bad.py"]


def test_deleted_file_asserts_nothing(tmp_path):
    body = RULE.replace("    message:", "    scope: file\n    message:")
    rule = rules_mod.load_rules(write_rule(tmp_path, body))[0]
    assert declarative.run(rule, ctx_for(head={}), ["src/gone.py"]) == []


def test_flags_apply(tmp_path):
    body = RULE.replace("'breakpoint\\('", "'BREAKPOINT\\('").replace(
        "    message:", "    flags: [i]\n    message:")
    rule = rules_mod.load_rules(write_rule(tmp_path, body))[0]
    ctx = ctx_for(added={"src/a.py": [(1, "breakpoint()")]})
    assert len(declarative.run(rule, ctx, ["src/a.py"])) == 1


def test_checks_change_rules_version(tmp_path):
    """A declarative check IS policy — editing one must move the cohort."""
    d = write_rule(tmp_path)
    before = rules_mod.rules_version(d, tmp_path)
    (d / "no-debug.md").write_text(RULE.replace("breakpoint\\(", "pdb\\.set_trace\\("))
    assert rules_mod.rules_version(d, tmp_path) != before


# ---------- inline allow markers ---------------------------------------------
#
# A carve-out must carry its reason recorded next to the code. Rule-level
# globs can only exclude whole files — an exclude that hides a real instance
# is indistinguishable from one that hides a false positive. The marker is
# per-line, names the check it answers, and REQUIRES a reason: a bare marker
# suppresses nothing (fail closed).

ALLOW_RULE = """---
id: no-shell
severity: MEDIUM
engine: declarative
applies_to: ["src/**/*.py"]
checks:
  - id: shell-true
    pattern: 'shell\\s*=\\s*True'
    message: "subprocess with shell=True"
  - id: except-pass
    pattern: 'except[^:\\n]*:\\s*(\\n\\s*)?pass\\b'
    message: "exception swallowed"
    scope: file
    flags: [m]
---
Body.
"""


def _allow_rule(tmp_path):
    return rules_mod.load_rules(write_rule(tmp_path, ALLOW_RULE,
                                           "no-shell.md"))[0]


def test_allow_marker_with_reason_suppresses_same_line(tmp_path):
    line = ("run(c, shell=True)  "
            "# warden:allow(shell-true): verify scopes are shell commands by design and the boundary is recorded")
    out = declarative.run(_allow_rule(tmp_path),
                          ctx_for(added={"src/a.py": [(3, line)]},
                                  head={"src/a.py": line + "\n"}),
                          ["src/a.py"])
    assert out == []


def test_allow_marker_on_the_line_above_suppresses(tmp_path):
    content = ("try:\n    f()\n"
               "# warden:allow(except-pass): the symlink is only a convenience pointer never load-bearing for readers\n"
               "except OSError:\n    pass\n")
    out = declarative.run(_allow_rule(tmp_path),
                          ctx_for(head={"src/a.py": content}), ["src/a.py"])
    assert out == []


def test_allow_without_a_reason_does_not_suppress(tmp_path):
    line = "run(c, shell=True)  # warden:allow(shell-true)"
    out = declarative.run(_allow_rule(tmp_path),
                          ctx_for(added={"src/a.py": [(3, line)]},
                                  head={"src/a.py": line + "\n"}),
                          ["src/a.py"])
    assert len(out) == 1, "a bare marker must not suppress — reasons are the point"


def test_allow_for_a_different_check_does_not_suppress(tmp_path):
    line = ("run(c, shell=True)  "
            "# warden:allow(except-pass): wrong check named")
    out = declarative.run(_allow_rule(tmp_path),
                          ctx_for(added={"src/a.py": [(3, line)]},
                                  head={"src/a.py": line + "\n"}),
                          ["src/a.py"])
    assert len(out) == 1


def test_file_scope_reports_the_first_unallowed_match(tmp_path):
    """The marker sits on the line above, not AFTER the except colon: there
    the marker text itself would break the pattern, and the test would pass
    with no suppression implemented at all (the vacuous-guard shape). On the
    line above, the pattern genuinely fires and suppression is what is being
    tested."""
    content = ("try:\n    f()\n"
               "# warden:allow(except-pass): the documented convenience pointer is never load-bearing for any reader\n"
               "except OSError:\n"
               "    pass\n"
               "try:\n    g()\nexcept ValueError:\n    pass\n")
    out = declarative.run(_allow_rule(tmp_path),
                          ctx_for(head={"src/a.py": content}), ["src/a.py"])
    assert len(out) == 1
    assert out[0]["line"] == 8, "the allowed first match must not mask the second"


# ---------- the marker holes: spellings a carve-out must not accept ----------

SUBSTANTIVE = ("the symlink is a convenience pointer that is never "
               "load-bearing for any reader")


@pytest.mark.parametrize("reason", [".", "todo", "ok", "x",
                                    "short reason here"])
def test_a_junk_reason_does_not_suppress(tmp_path, reason):
    """'reason is REQUIRED' means a substantive reason, not any non-separator
    token: a literal dot must not suppress a live finding when the catalog
    demands 8 substantive words to answer an entry. The marker carries the same
    substance floor as an answer's reason and a ceiling's rationale."""
    line = f"run(c, shell=True)  # warden:allow(shell-true): {reason}"
    out = declarative.run(_allow_rule(tmp_path),
                          ctx_for(added={"src/a.py": [(3, line)]},
                                  head={"src/a.py": line + "\n"}),
                          ["src/a.py"])
    assert len(out) == 1, f"a junk reason ({reason!r}) suppressed"


def test_a_substantive_reason_still_suppresses(tmp_path):
    line = f"run(c, shell=True)  # warden:allow(shell-true): {SUBSTANTIVE}"
    out = declarative.run(_allow_rule(tmp_path),
                          ctx_for(added={"src/a.py": [(3, line)]},
                                  head={"src/a.py": line + "\n"}),
                          ["src/a.py"])
    assert out == []


@pytest.mark.parametrize("line_above", [
    'HELP = "carry a warden:allow(shell-true): %s marker"' % SUBSTANTIVE,
    '"""docs mention warden:allow(shell-true): %s"""' % SUBSTANTIVE,
    '# see https://docs/warden:allow(shell-true):-%s' % SUBSTANTIVE.replace(" ", "-"),
])
def test_a_marker_outside_a_comment_does_not_suppress(tmp_path, line_above):
    """A marker smuggled into a string literal, docstring, or URL is
    camouflaged suppression a diff reader sees as data. The marker must
    directly follow a comment leader."""
    content = line_above + "\nrun(c, shell=True)\n"
    out = declarative.run(_allow_rule(tmp_path),
                          ctx_for(added={"src/a.py": [(2, "run(c, shell=True)")]},
                                  head={"src/a.py": content}),
                          ["src/a.py"])
    assert len(out) == 1, f"suppressed by non-comment marker: {line_above!r}"


def test_comment_leaders_of_other_languages_work(tmp_path):
    for leader in ("#", "//", "/*", "<!--", ";"):
        content = (f"{leader} warden:allow(except-pass): {SUBSTANTIVE}\n"
                   "except OSError:\n    pass\n")
        out = declarative.run(_allow_rule(tmp_path),
                              ctx_for(head={"src/a.py": content}), ["src/a.py"])
        assert out == [], f"leader {leader!r} not honored"


def test_duplicate_check_ids_across_rules_are_refused(tmp_path):
    """Check ids are unique across rule files, not only within one:
    otherwise one marker silences two rules' checks — the author adjudicates
    one and unknowingly waives the other. Collisions fail at load, where
    every other identity defect in the ruleset fails."""
    write_rule(tmp_path, ALLOW_RULE, "no-shell.md")
    clone = ALLOW_RULE.replace("id: no-shell", "id: other-rule", 1)
    write_rule(tmp_path, clone, "other-rule.md")
    with pytest.raises(rules_mod.RuleError, match="check id"):
        rules_mod.load_rules(tmp_path / "rules")
