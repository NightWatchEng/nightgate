"""Project-checker plugin seam + platform version pinning (review P1-3)."""

from pathlib import Path

import pytest

from warden import config as config_mod
from warden import plugins
from warden.mechanical import CHECKERS as CORE

GOOD_MODULE = '''
def check_custom(ctx, params, files):
    return [{"file": f, "finding": "hit", "evidence": "x"} for f in files]
CHECKERS = {"my-custom-rule": check_custom}
'''


def _plug_dir(tmp_path: Path) -> Path:
    rules = tmp_path / ".warden" / "rules"
    rules.mkdir(parents=True)
    (tmp_path / ".warden" / "checkers").mkdir()
    return rules


class TestLoadRegistry:
    def test_no_checkers_dir_returns_core(self, tmp_path):
        rules = tmp_path / ".warden" / "rules"
        rules.mkdir(parents=True)
        assert plugins.load_registry(rules) == CORE

    def test_project_checker_merges(self, tmp_path):
        rules = _plug_dir(tmp_path)
        (rules.parent / "checkers" / "custom.py").write_text(GOOD_MODULE)
        reg = plugins.load_registry(rules)
        assert "my-custom-rule" in reg
        assert set(CORE).issubset(reg)
        findings = reg["my-custom-rule"](None, {}, ["a.py"])
        assert findings[0]["finding"] == "hit"

    def test_shadowing_core_rule_fails_closed(self, tmp_path):
        rules = _plug_dir(tmp_path)
        (rules.parent / "checkers" / "shadow.py").write_text(
            "CHECKERS = {'secrets-in-diff': lambda c, p, f: []}")
        with pytest.raises(config_mod.ConfigError, match="shadows a core rule"):
            plugins.load_registry(rules)

    def test_duplicate_across_modules_fails_closed(self, tmp_path):
        rules = _plug_dir(tmp_path)
        (rules.parent / "checkers" / "a.py").write_text(GOOD_MODULE)
        (rules.parent / "checkers" / "b.py").write_text(GOOD_MODULE)
        with pytest.raises(config_mod.ConfigError, match="duplicate project checker"):
            plugins.load_registry(rules)

    def test_import_failure_fails_closed(self, tmp_path):
        rules = _plug_dir(tmp_path)
        (rules.parent / "checkers" / "broken.py").write_text("import nonexistent_mod_xyz")
        with pytest.raises(config_mod.ConfigError, match="failed to\\s+load"):
            plugins.load_registry(rules)

    def test_missing_or_empty_checkers_export_fails_closed(self, tmp_path):
        rules = _plug_dir(tmp_path)
        (rules.parent / "checkers" / "empty.py").write_text("CHECKERS = {}")
        with pytest.raises(config_mod.ConfigError, match="non-empty CHECKERS"):
            plugins.load_registry(rules)

    def test_non_callable_entry_fails_closed(self, tmp_path):
        rules = _plug_dir(tmp_path)
        (rules.parent / "checkers" / "bad.py").write_text("CHECKERS = {'x-rule': 42}")
        with pytest.raises(config_mod.ConfigError, match="not callable"):
            plugins.load_registry(rules)


class TestPlatformPin:
    def _cfg(self, pin):
        from warden.config import RepoConfig, ReviewSettings
        return RepoConfig(root=Path("."), repo="x", platform_pin=pin,
                          components=(), risk_tiers=(), protected_paths=(),
                          verify={}, review=ReviewSettings(
                              rules_dir=".warden/rules",
                              blocking_severities=("HIGH",),
                              context_excludes=()))

    def test_no_pin_is_fine(self):
        config_mod.enforce_platform_pin(self._cfg(None))

    def test_matching_pin_with_v_prefix(self):
        from warden import __version__
        config_mod.enforce_platform_pin(self._cfg(f"v{__version__}"))
        config_mod.enforce_platform_pin(self._cfg(__version__))

    def test_mismatch_fails_closed(self):
        with pytest.raises(config_mod.ConfigError, match="pins platform"):
            config_mod.enforce_platform_pin(self._cfg("v999.0.0"))


def test_version_single_source_of_truth():
    """review P1-7: the package version and warden.__version__ must not drift —
    a release bumping one but not the other ships a pin-mismatch to consumers.
    pyproject declares no version of its own; hatch reads warden/__init__.py."""
    import importlib.metadata
    import tomllib
    from warden import __version__
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert "version" not in pyproject["project"], (
        "pyproject carries a static version again — a second source to drift")
    assert "version" in pyproject["project"]["dynamic"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "warden/__init__.py"
    assert importlib.metadata.version("warden") == __version__


# ---------- the example project exercises the seam ---------------------------
#
# The standing portability proof carries a .warden/checkers/ entry, so the
# seam a consumer reaches for when a regex will not do is proven by the
# example they copy, not only by unit fixtures.

EXAMPLE_RULES = (Path(__file__).resolve().parents[1]
                 / "examples" / "hello-svc" / ".warden" / "rules")


def _ctx(src: str):
    from warden.diffs import DiffContext
    return DiffContext(base="b" * 40, head="h" * 40,
                       files=("hello_svc/app.py",),
                       added={}, removed={},
                       read_base=lambda f: None,
                       read_head=lambda f: src)


class TestHelloSvcProjectChecker:
    def test_example_registry_carries_the_project_checker(self):
        reg = plugins.load_registry(EXAMPLE_RULES)
        assert "handler-response-contract" in reg
        assert set(CORE).issubset(reg)

    def test_checker_flags_a_bare_dict_return(self):
        reg = plugins.load_registry(EXAMPLE_RULES)
        findings = reg["handler-response-contract"](_ctx(
            "def broken() -> tuple[int, dict]:\n"
            "    return {'status': 'missing'}\n"), {}, ["hello_svc/app.py"])
        assert len(findings) == 1
        assert "bare dict" in findings[0]["finding"]
        assert findings[0]["line"] == 2

    def test_checker_flags_a_misshaped_tuple(self):
        reg = plugins.load_registry(EXAMPLE_RULES)
        findings = reg["handler-response-contract"](_ctx(
            "def broken() -> tuple[int, dict]:\n"
            "    return 200, {'ok': True}, 'extra'\n"), {}, ["hello_svc/app.py"])
        assert len(findings) == 1
        assert "3-tuple" in findings[0]["finding"]

    def test_checker_passes_the_real_service(self):
        reg = plugins.load_registry(EXAMPLE_RULES)
        app = (EXAMPLE_RULES.parents[1] / "hello_svc" / "app.py").read_text()
        assert reg["handler-response-contract"](_ctx(app), {},
                                                ["hello_svc/app.py"]) == []

    def test_checker_ignores_returns_of_nested_functions(self):
        """A helper defined inside a contract function has its own contract
        (none); flagging its returns would be the checker firing on
        something it was never meant to catch."""
        reg = plugins.load_registry(EXAMPLE_RULES)
        findings = reg["handler-response-contract"](_ctx(
            "def handler() -> tuple[int, dict]:\n"
            "    def fmt():\n"
            "        return {'inner': True}\n"
            "    return 200, fmt()\n"), {}, ["hello_svc/app.py"])
        assert findings == []

    def test_checker_stays_quiet_on_unprovable_returns_and_bad_syntax(self):
        """Deterministic and conservative: names, calls, and files the parser
        cannot read are not this rule's failure to report."""
        reg = plugins.load_registry(EXAMPLE_RULES)
        assert reg["handler-response-contract"](_ctx(
            "def h() -> tuple[int, dict]:\n"
            "    return make_response()\n"), {}, ["hello_svc/app.py"]) == []
        assert reg["handler-response-contract"](_ctx(
            "def h( -> broken syntax"), {}, ["hello_svc/app.py"]) == []

    def test_rules_version_changes_when_the_checker_changes(self, tmp_path):
        """A checker edit is a policy edit, so it must move
        rules_version."""
        import shutil
        from warden.rules import rules_version
        dst = tmp_path / ".warden"
        shutil.copytree(EXAMPLE_RULES.parent, dst)
        before = rules_version(dst / "rules", tmp_path)
        checker = next((dst / "checkers").glob("*.py"))
        checker.write_text(checker.read_text() + "\n# policy edit\n")
        after = rules_version(dst / "rules", tmp_path)
        assert before != after

    def test_checker_stays_quiet_on_starred_tuples(self):
        """A starred element makes the
        tuple's runtime length statically undecidable — arity-flagging it is
        a guess, and under a HIGH blocking rule a guess blocks a legitimate
        PR. Provable breaks only."""
        reg = plugins.load_registry(EXAMPLE_RULES)
        assert reg["handler-response-contract"](_ctx(
            "def h() -> tuple[int, dict]:\n"
            "    pair = (200, {})\n"
            "    return *pair,\n"), {}, ["hello_svc/app.py"]) == []
