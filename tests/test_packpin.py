"""`warden skills pin`: the pack a machine serves is the one platform.pin names.

The installer's records (measured on Claude Code 2.1.283)
are rebuilt here as fixtures: `plugins/known_marketplaces.json` with the
marketplace's `source.ref`, `plugins/installed_plugins.json` with the copy's
`version`, and the marketplace clone whose own manifests say what it serves.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from warden import __version__, cli, enroll, packpin

ROOT = Path(__file__).resolve().parent.parent
PIN = f"v{__version__}"


def _repo(tmp_path: Path, pin: str | None = PIN) -> Path:
    repo = tmp_path / "platform-src"
    repo.mkdir(parents=True)
    platform = f"platform:\n  pin: {pin}\n" if pin else ""
    (repo / "repo.yaml").write_text(
        "version: 1\nrepo: platform-src\n" + platform +
        "components:\n  app: {path: src/, lang: python}\n"
        "risk_tiers:\n  - {glob: '**', tier: LOW}\n"
        "verify:\n  tests:\n    - {run: 'true', covers: ['**']}\n"
        "review:\n  rules_dir: .warden/rules\n  blocking_severities: [HIGH]\n")
    (repo / ".warden" / "rules").mkdir(parents=True)
    return repo


def _config_dir(tmp_path: Path, *, ref: str | None = PIN, source: str = "git",
                marketplace: bool = True, installed: str | None = "0.19.1",
                serves: str | None = "0.19.1") -> Path:
    """A Claude config dir in the shape the installer leaves behind."""
    cfg = tmp_path / "claude"
    plugins = cfg / "plugins"
    clone = plugins / "marketplaces" / packpin.MARKETPLACE
    (clone / ".claude-plugin").mkdir(parents=True)
    (clone / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"name": packpin.MARKETPLACE,
         "plugins": [{"name": packpin.PLUGIN, "source": "./skills/pack"}]}))
    if serves is not None:
        (clone / "skills" / "pack" / ".claude-plugin").mkdir(parents=True)
        (clone / "skills" / "pack" / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": packpin.PLUGIN, "version": serves}))
    subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    if marketplace:
        src = {"source": source, "url": f"{packpin.MARKETPLACE_URL}.git"}
        if ref is not None:
            src["ref"] = ref
        (plugins / "known_marketplaces.json").write_text(json.dumps(
            {packpin.MARKETPLACE: {"source": src, "installLocation": str(clone)}}))
    copies = [] if installed is None else [
        {"scope": "user", "version": installed, "gitCommitSha": packpin._clone_sha(clone),
         "installPath": str(plugins / "cache" / packpin.MARKETPLACE / packpin.PLUGIN / installed)}]
    for c in copies: (Path(c["installPath"]) / "skills").mkdir(parents=True)  # noqa: E701
    (plugins / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {f"{packpin.PLUGIN}@{packpin.MARKETPLACE}": copies}}))
    return cfg


def _pin(repo: Path, cfg: Path, monkeypatch, capsys) -> tuple[int, str, str]:
    monkeypatch.chdir(repo)
    code = cli.main(["skills", "pin", "--config-dir", str(cfg)])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.mark.parametrize("pin", [PIN, __version__])  # the schema admits `pin: 2.2.0`
def test_a_pack_installed_from_the_marketplace_at_the_pin_passes(
        pin, tmp_path, monkeypatch, capsys):
    cfg = _config_dir(tmp_path)
    code, out, err = _pin(_repo(tmp_path, pin=pin), cfg, monkeypatch, capsys)
    assert code == 0 and err == "", err
    assert f"{packpin.PLUGIN} 0.19.1" in out and PIN in out, out
    assert f"--pack {cfg}/plugins/cache/{packpin.MARKETPLACE}/{packpin.PLUGIN}/0.19.1/skills" in out


def test_a_marketplace_ref_that_disagrees_with_the_pin_is_refused(
        tmp_path, monkeypatch, capsys):
    code, out, err = _pin(_repo(tmp_path), _config_dir(tmp_path, ref="v0.0.1"),
                          monkeypatch, capsys)
    assert code == 1 and out == "", out
    assert "added at v0.0.1" in err and f"pins {PIN}" in err, err
    assert f"add '{packpin.MARKETPLACE_URL}#{PIN}'" in err and "config dir holds one tag" in err, err


def test_a_copy_whose_path_or_commit_is_not_the_clones_is_refused(
        tmp_path, monkeypatch, capsys):
    repo, cfg = _repo(tmp_path), _config_dir(tmp_path)
    record, clone = cfg / "plugins" / "installed_plugins.json", cfg / "plugins" / "marketplaces" / "nightgate"
    [copy] = json.loads(record.read_text())["plugins"][f"{packpin.PLUGIN}@{packpin.MARKETPLACE}"]
    for path, sha, code, says in [(str(tmp_path / "gone"), None, 1, "holds no skills/"),
                                  (copy["installPath"], "0" * 40, 1, "installed from commit"),
                                  (copy["installPath"], None, 1, "(none recorded)"),
                                  (copy["installPath"], packpin._clone_sha(clone), 0, "")]:
        record.write_text(json.dumps({"version": 2, "plugins": {f"{packpin.PLUGIN}@{packpin.MARKETPLACE}": [
            {**copy, "installPath": path, "gitCommitSha": sha}]}}))
        got, out, err = _pin(repo, cfg, monkeypatch, capsys)
        assert got == code and says in err + out, err


@pytest.mark.parametrize("source", ["git", "directory"])
def test_a_marketplace_added_without_a_ref_floats_and_is_refused(
        source, tmp_path, monkeypatch, capsys):
    code, _, err = _pin(_repo(tmp_path), _config_dir(tmp_path, ref=None, source=source),
                        monkeypatch, capsys)
    assert code == 1 and "no ref" in err and f"source: {source}" in err, err


def test_an_absent_marketplace_or_plugin_is_refused_not_passed(
        tmp_path, monkeypatch, capsys):
    """No pack is not the pinned pack: each absence is a refusal (1) that
    names the install, never a pass and never a could-not-run."""
    repo = _repo(tmp_path)
    cfg = _config_dir(tmp_path / "a", marketplace=False)
    code, _, err = _pin(repo, cfg, monkeypatch, capsys)
    assert code == 1 and "is not added" in err and f"#{PIN}'" in err, err
    (cfg / "plugins" / "installed_plugins.json").unlink()
    assert _pin(repo, cfg, monkeypatch, capsys)[0] == 1
    code, _, err = _pin(repo, _config_dir(tmp_path / "b", installed=None),
                        monkeypatch, capsys)
    assert code == 1 and "is not installed from it" in err, err


def test_an_installed_version_the_pinned_clone_does_not_serve_is_refused(
        tmp_path, monkeypatch, capsys):
    """The marketplace ref alone is not the proof: a copy installed before the
    marketplace was re-added is a different pack than the clone serves."""
    code, _, err = _pin(_repo(tmp_path), _config_dir(tmp_path, installed="0.18.0"),
                        monkeypatch, capsys)
    assert code == 1 and "0.18.0 is installed" in err and "serves 0.19.1" in err, err


def test_a_record_the_check_cannot_read_is_unreadable_never_a_pass(
        tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    cfg = _config_dir(tmp_path / "a")
    (cfg / "plugins" / "known_marketplaces.json").write_text("{")
    code, _, err = _pin(repo, cfg, monkeypatch, capsys)
    assert code == 2 and "unreadable" in err, err
    code, _, err = _pin(repo, _config_dir(tmp_path / "b", serves=None), monkeypatch, capsys)
    assert code == 2 and "plugin manifest" in err, err
    code, _, err = _pin(_repo(tmp_path / "c", pin=None), _config_dir(tmp_path / "c"),
                        monkeypatch, capsys)
    assert code == 2 and "no platform.pin" in err, err


def test_the_documented_install_and_init_name_the_pin_and_the_manifest(
        tmp_path, monkeypatch, capsys):
    """Installation.md, `warden init`'s next steps and the check's constants
    all read the one marketplace manifest and the one version."""
    manifest = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["name"] == packpin.MARKETPLACE
    assert [p["name"] for p in manifest["plugins"]] == [packpin.PLUGIN]
    add, install = packpin.install_commands(PIN)
    page = (ROOT / "docs" / "wiki" / "Installation.md").read_text()
    assert f"```bash\n{add}\n{install}\n```" in page, add
    repo = tmp_path / "platform-src"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    assert cli.main(["init"]) == 0
    steps = capsys.readouterr().out.split("Next steps:", 1)[1]
    assert f"{add}; {install}" in steps and "warden skills pin" in steps, steps


def _git(cwd: Path, *args: str) -> str:
    from conftest import _AMBIENT_GIT_CONFIG
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
        env={**os.environ, **_AMBIENT_GIT_CONFIG}).stdout.strip()


def _flat(text: str) -> str:
    return " ".join(text.replace("`", "").split())


def _init_steps(tmp_path: Path, monkeypatch, capsys, version: str) -> str:
    repo = tmp_path / "platform-src"
    repo.mkdir(parents=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')
    _git(repo, "init", "-q", "-b", "main")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(enroll, "__version__", version)
    assert cli.main(["init"]) == 0
    return _flat(capsys.readouterr().out.split("Next steps:", 1)[1])


def test_init_and_the_wiki_name_skills_pin_only_where_the_newest_tag_has_it(
        tmp_path, monkeypatch, capsys):
    """A consumer pinned at the newest release is told to run `warden skills
    pin` only when that tag contains the commit that added it. The history is
    a fixture repo: a release cut before the command, the command's commit,
    then a release cut after it; the newest tag is read from git each time."""
    src = tmp_path / "hist"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    (src / "packpin.py").write_text("# no skills pin yet\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "base")
    _git(src, "tag", packpin.LACKS_PIN_COMMAND)
    (src / "packpin.py").write_text("def check(): ...  # warden skills pin\n")
    _git(src, "commit", "-qam", "add warden skills pin")
    command = _git(src, "rev-parse", "HEAD")
    major, minor, _ = packpin.LACKS_PIN_COMMAND.removeprefix("v").split(".")
    for cut in (None, f"v{major}.{int(minor) + 1}.0"):
        if cut:
            _git(src, "tag", "-a", "-m", "release", cut)
        newest = _git(src, "tag", "--sort=-v:refname").splitlines()[0]
        contains = newest in _git(src, "tag", "--contains", command).splitlines()
        assert packpin.carries_pin_command(newest) is contains, newest
        step = packpin.check_step(newest)
        told_to_run = step.startswith("Then check it with warden skills pin")
        assert told_to_run is contains, step
        assert (newest in step) and ("by hand" in step) is not contains, step
        steps = _init_steps(tmp_path / newest, monkeypatch, capsys,
                            newest.removeprefix("v"))
        assert _flat(step) in steps, steps
        if not contains:
            assert "then check it with warden skills pin" not in steps.lower(), steps


def test_the_wiki_sentence_is_the_one_for_the_adopted_pin():
    """Installation.md's check sentence is `check_step` at the pin the
    shipped example adopts — bumping the pin past the release that lacks the
    command demands the sentence that names the command's tag."""
    pin = yaml.safe_load((ROOT / "examples" / "hello-svc" / "repo.yaml")
                         .read_text())["platform"]["pin"]
    section = (ROOT / "docs" / "wiki" / "Installation.md").read_text().split(
        "## 3. The skill pack", 1)[1].split("\n## ", 1)[0]
    assert _flat(packpin.check_step(pin)) in _flat(section), pin
    if not packpin.carries_pin_command(pin):
        fence = section.split("```bash\n", 1)[1].split("```", 1)[0]
        assert "warden skills pin" not in fence, fence


def _skills_pin_drift(pin: str, pages: dict[str, str]) -> list[str]:
    """What each wiki page naming `warden skills pin` says that is false at
    PIN: an unqualified offer while PIN lacks the command, a by-hand check
    once PIN carries it, and a marketplace `#vX.Y.Z` that is not PIN."""
    since = f"first release after {packpin.LACKS_PIN_COMMAND}"
    carries = packpin.carries_pin_command(pin)
    drift = []
    for name, text in pages.items():
        flat = _flat(text)
        if "warden skills pin" not in flat:
            continue
        if not carries and since not in flat:
            drift.append(f"{name}: offers warden skills pin without '{since}'")
        if carries and "by hand" in flat:
            drift.append(f"{name}: still gives the by-hand check {pin} replaces")
        for tag in re.findall(r"agentops#(v[0-9][0-9.]*[0-9])|<url>#(v[0-9][0-9.]*[0-9])", text):
            if (tag[0] or tag[1]) != pin:
                drift.append(f"{name}: names #{tag[0] or tag[1]}, not {pin}")
    return drift


def _wiki_pages() -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted((ROOT / "docs" / "wiki").glob("*.md"))}


def test_no_wiki_page_offers_skills_pin_to_a_pin_that_lacks_it():
    """Every wiki page that names `warden skills pin` is true at the adopted
    pin — Skill-Pack.md said it unqualified beside `#v2.2.0` after
    Installation.md was fixed."""
    pin = yaml.safe_load((ROOT / "examples" / "hello-svc" / "repo.yaml")
                         .read_text())["platform"]["pin"]
    pages = _wiki_pages()
    named = [n for n, t in pages.items() if "warden skills pin" in _flat(t)]
    assert {"Installation.md", "Skill-Pack.md"} <= set(named), named
    assert _skills_pin_drift(pin, pages) == []


def test_the_pages_drift_at_the_pin_on_the_other_side_of_the_command():
    """The guard binds in the direction the adopted pin did not take. At a pin
    that lacks the command, the other side is the release after it: pages
    still giving the by-hand check and the old `#v` tag are named stale. At a
    pin that carries it (v2.3.0 on), the other side is LACKS_PIN_COMMAND:
    pages that offer the command unqualified and name the new `#v` tag are
    named stale there. At a carrying pin the live arm is also fed a page that
    still gives the by-hand check. Rewritten at each pin bump; last at
    v2.3.0."""
    pin = yaml.safe_load((ROOT / "examples" / "hello-svc" / "repo.yaml")
                         .read_text())["platform"]["pin"]
    if packpin.carries_pin_command(pin):
        other, stale = packpin.LACKS_PIN_COMMAND, "offers warden skills pin without"
    else:
        major, minor, _ = packpin.LACKS_PIN_COMMAND.removeprefix("v").split(".")
        other, stale = f"v{major}.{int(minor) + 1}.0", "still gives the by-hand"
    drift = _skills_pin_drift(other, _wiki_pages())
    for page in ("Installation.md", "Skill-Pack.md"):
        assert any(d.startswith(f"{page}: {stale}") for d in drift), drift
        assert any(d.startswith(f"{page}: names #") for d in drift), drift
    if packpin.carries_pin_command(pin):
        pages = _wiki_pages()
        pages["Skill-Pack.md"] += "\nCheck by hand instead.\n"
        assert _skills_pin_drift(pin, pages) == [
            f"Skill-Pack.md: still gives the by-hand check {pin} replaces"]


def test_init_never_splits_the_manifest_path_at_a_hyphen(tmp_path, monkeypatch, capsys):
    """The by-hand check `warden init` prints names a path a consumer copies.
    textwrap breaks on hyphens by default, so a longer pack name moved the
    wrap point into `.claude-plugin/` and printed `.claude-` and `plugin/`
    on two lines. Wrapping happens only at spaces."""
    repo = tmp_path / "platform-src"
    repo.mkdir(parents=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')
    _git(repo, "init", "-q", "-b", "main")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(enroll, "__version__", packpin.LACKS_PIN_COMMAND.removeprefix("v"))
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert f"skills/{packpin.PLUGIN}/.claude-plugin/plugin.json" in out, out
