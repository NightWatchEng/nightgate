"""Finding text never carries a maintainer's home directory into a shard.

Reviewers run from absolute worktree paths and quote them in `evidence`,
`file`, `finding` and `reason`. `attest write` is where a payload's findings
are validated, and `memory ingest` copies that text into a committed,
content-addressed shard, so the text is fixed at the writer: a path under any
worktree of the repository is rewritten repo-relative, and any other home
path is refused, naming the finding and the field.

Home paths here are COMPOSED, never written literally — a literal would be a
real home path in a tracked file, and `test_no_maintainer_home_path_is_tracked`
would flag this module.
"""

import json
import subprocess
from pathlib import Path

import pytest

from test_cli import _clone_fixture
from warden import attest as attest_mod
from warden import cli


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def _payload(**finding) -> dict:
    return {
        "reviewers": [{"role": "code-reviewer", "agent": "subagent"}],
        "findings": [{"rule_id": "scope-creep", "severity": "LOW",
                      "file": "app/x.py", "line": 1, "finding": "f",
                      "evidence": "e", "status": "fixed", **finding}],
        "verdict": "clean"}


def _write(root: Path, payload: dict) -> Path:
    path = root.parent / "findings.json"
    path.write_text(json.dumps(payload))
    return path


def _artifact(root: Path) -> dict:
    runs = sorted((root / ".warden" / "out").glob("*-attest/attestation.json"))
    assert runs, "attest write produced no artifact"
    return json.loads(runs[-1].read_text())


def _shard_texts(root: Path) -> list[str]:
    return [p.read_text() for p in
            sorted((root / ".warden" / "memory" / "attest").glob("*.json"))]


def test_a_path_under_the_repo_root_is_repo_relative_in_artifact_and_shard(
        sample_repo, tmp_path, monkeypatch, capsys):
    root = _clone_fixture(sample_repo, tmp_path)
    # Quoted through a symlink, so the rewrite must resolve the TEXT's path
    # as well as the root's: the link and the checkout are one directory.
    link = tmp_path / "via-link"
    link.symlink_to(root)
    payload = _payload(
        file=f"{root}/app/x.py",
        evidence=f"grep ran in {root}/app and hit {link}/app/x.py:1.",
        finding=f"the checkout root {root} itself",
        status="dismissed-with-reason",
        reason=f"see {root}/README.md")
    monkeypatch.chdir(root)
    assert cli.main(["attest", "write", "--findings",
                     str(_write(root, payload))]) == 0, capsys.readouterr().err

    f = _artifact(root)["findings"][0]
    assert f["file"] == "app/x.py"
    assert f["evidence"] == "grep ran in app and hit app/x.py:1."
    assert f["finding"] == "the checkout root . itself"
    assert f["reason"] == "see README.md"

    assert cli.main(["memory", "ingest"]) == 0, capsys.readouterr().err
    shards = _shard_texts(root)
    assert shards, "ingest filed no shard"
    joined = "\n".join(shards)
    assert str(root) not in joined and str(link) not in joined, joined
    assert "grep ran in app and hit app/x.py:1." in joined


def test_a_path_under_a_linked_worktree_is_repo_relative(
        sample_repo, tmp_path, monkeypatch, capsys):
    root = _clone_fixture(sample_repo, tmp_path)
    linked = tmp_path / "linked-wt"
    _git(root, "worktree", "add", "-q", "-b", "reviewer", str(linked))

    # From the main checkout, quoting the linked worktree.
    monkeypatch.chdir(root)
    payload = _payload(evidence=f"read {linked}/app/x.py:1 in the reviewer's "
                                "worktree")
    assert cli.main(["attest", "write", "--findings",
                     str(_write(root, payload))]) == 0, capsys.readouterr().err
    assert _artifact(root)["findings"][0]["evidence"] == \
        "read app/x.py:1 in the reviewer's worktree"
    assert cli.main(["memory", "ingest"]) == 0, capsys.readouterr().err
    joined = "\n".join(_shard_texts(root))
    assert str(linked) not in joined and "read app/x.py:1" in joined, joined

    # And from inside the linked worktree, quoting the main checkout.
    monkeypatch.chdir(linked)
    payload = _payload(evidence=f"compared with {root}/app/x.py")
    path = tmp_path / "from-linked.json"
    path.write_text(json.dumps(payload))
    assert cli.main(["attest", "write", "--findings", str(path)]) == 0, \
        capsys.readouterr().err
    assert _artifact(linked)["findings"][0]["evidence"] == \
        "compared with app/x.py"


@pytest.mark.parametrize("field", ["file", "evidence", "finding", "reason"])
@pytest.mark.parametrize("home", ["/Users/" + "somebody",
                                  "/home/" + "someone"])
def test_another_home_path_is_refused_naming_the_finding(
        sample_repo, tmp_path, monkeypatch, capsys, field, home):
    root = _clone_fixture(sample_repo, tmp_path)
    payload = _payload(status="dismissed-with-reason", reason="r")
    clean = dict(payload["findings"][0], rule_id="unmapped:docs-drift")
    leaky = dict(payload["findings"][0])
    leaky[field] = f"{home}/elsewhere/app/x.py"
    payload["findings"] = [clean, leaky]
    monkeypatch.chdir(root)
    assert cli.main(["attest", "write", "--findings",
                     str(_write(root, payload))]) == 2
    err = capsys.readouterr().err
    # The LEAKY finding is named (by its rule id), not the clean one before
    # it, and so is the field; wording unpinned.
    assert "scope-creep" in err and "docs-drift" not in err, err
    assert field in err, err
    assert not list((root / ".warden" / "out").glob("*-attest/attestation.json"))


def test_a_path_under_home_is_refused_when_home_is_not_a_users_path(
        sample_repo, tmp_path, monkeypatch, capsys):
    root = _clone_fixture(sample_repo, tmp_path)
    home = tmp_path / "service-account-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    payload = _payload(evidence=f"cache at {home}/.cache/uv")
    monkeypatch.chdir(root)
    assert cli.main(["attest", "write", "--findings",
                     str(_write(root, payload))]) == 2
    err = capsys.readouterr().err
    assert "0" in err and "evidence" in err, err


def test_a_payload_with_no_absolute_paths_is_unchanged_byte_for_byte(
        sample_repo, tmp_path, monkeypatch, capsys):
    root = _clone_fixture(sample_repo, tmp_path)
    payload = _payload(evidence="`x = 1` at app/x.py:1, see docs/wiki/Home.md",
                       finding="relative paths and a URL https://example.com/a")
    monkeypatch.chdir(root)
    assert cli.main(["attest", "write", "--findings",
                     str(_write(root, payload))]) == 0, capsys.readouterr().err
    assert json.dumps(_artifact(root)["findings"]) == \
        json.dumps(payload["findings"])


def test_placeholders_and_non_home_absolute_paths_pass_untouched(
        sample_repo, tmp_path, monkeypatch, capsys):
    root = _clone_fixture(sample_repo, tmp_path)
    payload = _payload(evidence="`/Users/me/workspace/hello-svc` and "
                                "/home/runner/.cache are placeholders; "
                                "/usr/local/bin/python is not a home")
    monkeypatch.chdir(root)
    assert cli.main(["attest", "write", "--findings",
                     str(_write(root, payload))]) == 0, capsys.readouterr().err
    assert json.dumps(_artifact(root)["findings"]) == \
        json.dumps(payload["findings"])


def test_build_does_not_mutate_the_callers_payload(sample_repo, tmp_path):
    payload = _payload(evidence=f"{tmp_path}/app/x.py")
    before = json.dumps(payload)
    doc = attest_mod.build(payload, head_sha="b" * 40, base_sha="a" * 40,
                           rules_version="v", root=tmp_path,
                           rules_dir=sample_repo / ".warden" / "rules")
    assert doc["findings"][0]["evidence"] == "app/x.py"
    assert json.dumps(payload) == before


def _build(sample_repo, root: Path, payload: dict) -> dict:
    return attest_mod.build(payload, head_sha="b" * 40, base_sha="a" * 40,
                            rules_version="v", root=root,
                            rules_dir=sample_repo / ".warden" / "rules")


def _route(name: str):
    """`(put, get)` for a string `memory ingest` copies into a shard through a
    route the finding-text fields do not cover: the roster `agent`, a
    finding's `tags`, and a roster `output` when no --review-dir checked it.
    Fixture input, so it is built per call rather than held at module level.
    """
    return {
        "agent": (lambda p, v: p["reviewers"][0].__setitem__("agent", v),
                  lambda d: d["reviewers"][0]["agent"]),
        "tags": (lambda p, v: p["findings"][0].__setitem__("tags", ["x", v]),
                 lambda d: d["findings"][0]["tags"][1]),
        "output": (lambda p, v: p["reviewers"][0].__setitem__("output", v),
                   lambda d: d["reviewers"][0]["output"]),
    }[name]


@pytest.mark.parametrize("route", ["agent", "output", "tags"])
def test_every_shard_string_route_is_rewritten_under_a_worktree(
        sample_repo, tmp_path, monkeypatch, capsys, route):
    root = _clone_fixture(sample_repo, tmp_path)
    put, get = _route(route)
    payload = _payload()
    put(payload, f"{root}/.warden/rounds/r1/code-reviewer.json")
    monkeypatch.chdir(root)
    assert cli.main(["attest", "write", "--findings",
                     str(_write(root, payload))]) == 0, capsys.readouterr().err
    assert get(_artifact(root)) == ".warden/rounds/r1/code-reviewer.json"
    assert cli.main(["memory", "ingest"]) == 0, capsys.readouterr().err
    joined = "\n".join(_shard_texts(root))
    assert joined and str(root) not in joined, joined


@pytest.mark.parametrize("route", ["agent", "output", "tags"])
def test_every_shard_string_route_refuses_another_home_path(
        sample_repo, tmp_path, monkeypatch, capsys, route):
    root = _clone_fixture(sample_repo, tmp_path)
    put, _ = _route(route)
    payload = _payload()
    put(payload, "/Users/" + "somebody" + "/wt/code-reviewer.json")
    monkeypatch.chdir(root)
    assert cli.main(["attest", "write", "--findings",
                     str(_write(root, payload))]) == 2
    err = capsys.readouterr().err
    assert route in err, err
    assert not list((root / ".warden" / "out").glob("*-attest/attestation.json"))


def test_the_deepest_worktree_wins_for_a_worktree_nested_in_the_checkout(
        sample_repo, tmp_path):
    root = _clone_fixture(sample_repo, tmp_path)
    nested = root / ".claude" / "worktrees" / "n"
    _git(root, "worktree", "add", "-q", "-b", "nested", str(nested))
    doc = _build(sample_repo, root,
                 _payload(evidence=f"read {nested}/app/x.py:1"))
    assert doc["findings"][0]["evidence"] == "read app/x.py:1"


def test_a_checkout_path_with_a_space_and_brackets_is_rewritten(
        sample_repo, tmp_path, monkeypatch, capsys):
    parent = tmp_path / "My Projects (copy)"
    parent.mkdir()
    root = _clone_fixture(sample_repo, parent)
    payload = _payload(file=f"{root}/app/x.py",
                       evidence=f"see {root}/app/x.py:1 and [{root}/README.md]")
    monkeypatch.chdir(root)
    assert cli.main(["attest", "write", "--findings",
                     str(_write(root, payload))]) == 0, capsys.readouterr().err
    f = _artifact(root)["findings"][0]
    assert f["file"] == "app/x.py"
    assert f["evidence"] == "see app/x.py:1 and [README.md]"


@pytest.mark.parametrize("shape, expected", [
    ("file://{root}/a.py", "file://{root}/a.py"),
    ("host:{root}/a.py", "host:{root}/a.py"),
    ("C:{root}/a.py", "C:{root}/a.py"),
    ("the checkout root {root}.", "the checkout root ."),
    ("the checkout root {root}", "the checkout root ."),
    ("({root}/a.py), then", "(a.py), then"),
])
def test_a_rewrite_never_changes_what_the_text_means(
        sample_repo, tmp_path, shape, expected):
    root = tmp_path / "repo"
    root.mkdir()
    doc = _build(sample_repo, root,
                 _payload(evidence=shape.format(root=root)))
    assert doc["findings"][0]["evidence"] == expected.format(root=root)


@pytest.mark.parametrize("prefix", ["file://", "host:", "C:"])
def test_a_prefixed_home_path_is_refused_not_left(sample_repo, tmp_path,
                                                  prefix):
    home = "/Users/" + "somebody"
    with pytest.raises(attest_mod.AttestError, match="evidence"):
        _build(sample_repo, tmp_path,
               _payload(evidence=f"{prefix}{home}/wt/a.py"))


def test_the_home_path_definition_is_the_tree_scans_definition():
    """ONE definition of a home path, held by parity rather than a copy.

    `test_no_maintainer_home_path_is_tracked` decides what a leak is in the
    tree; attest decides what a leak is at the writer. If the two disagreed, a
    shard attest accepted could still fail the tree scan.
    """
    import test_docs
    assert attest_mod.HOME_RE.pattern == test_docs.HOME_RE.pattern
    assert attest_mod.HOME_PLACEHOLDERS == frozenset(test_docs.HOME_PLACEHOLDERS)


def _home_shaped(tmp_path: Path, *names: str) -> Path:
    """A directory under a home-shaped path built inside `tmp_path`
    (`<tmp>/Users/<name>/...`), so a checkout there is judged as the usual
    checkout under a maintainer's home without touching a real home."""
    d = tmp_path.joinpath("Users", "someone", *names)
    d.mkdir(parents=True)
    return d


@pytest.mark.parametrize("home", [False, True])
@pytest.mark.parametrize("shape, expected", [
    ("{root}/a={root}/b", "a=b"),
    ("?f={root}/a&g={root}/b", "?f=a&g=b"),
    ("x={root}/a.py:1&y={root}", "x=a.py:1&y=."),
])
def test_two_roots_in_one_token_are_each_rewritten_at_their_own_boundaries(
        sample_repo, tmp_path, shape, expected, home):
    root = _home_shaped(tmp_path, "repo") if home else tmp_path / "repo"
    root.mkdir(exist_ok=True)
    doc = _build(sample_repo, root,
                 _payload(evidence=shape.format(root=root)))
    assert doc["findings"][0]["evidence"] == expected


def test_a_colon_joined_path_is_left_as_written_not_normalised_across_the_colon(
        sample_repo, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    doc = _build(sample_repo, root,
                 _payload(evidence=f"PATH={root}/bin:{root}/../x"))
    assert doc["findings"][0]["evidence"] == f"PATH=bin:{root}/../x"


@pytest.mark.parametrize("shape, follows", [
    ("host:{root}/a.py", "host:"),
    ("file://{root}/a.py", "file://"),
    ("C:{root}/a.py", "C:"),
    ("PATH={root}/.venv/bin:{root}/bin", "PATH=.venv/bin:"),
])
def test_a_prefixed_or_colon_joined_path_under_a_home_checkout_is_refused_naming_what_it_follows(
        sample_repo, tmp_path, shape, follows):
    root = _home_shaped(tmp_path, "repo")
    with pytest.raises(attest_mod.AttestError) as refused:
        _build(sample_repo, root, _payload(evidence=shape.format(root=root)))
    err = str(refused.value)
    # Where it sits, and the text the path follows; wording unpinned.
    assert "evidence" in err and follows in err, err


def test_a_path_under_a_home_checkout_is_rewritten(
        sample_repo, tmp_path, monkeypatch, capsys):
    root = _clone_fixture(sample_repo, _home_shaped(tmp_path))
    payload = _payload(file=f"{root}/app/x.py",
                       evidence=f"see {root}/app/x.py:1 and ({root}/README.md)")
    monkeypatch.chdir(root)
    assert cli.main(["attest", "write", "--findings",
                     str(_write(root, payload))]) == 0, capsys.readouterr().err
    f = _artifact(root)["findings"][0]
    assert f["file"] == "app/x.py"
    assert f["evidence"] == "see app/x.py:1 and (README.md)"


@pytest.mark.parametrize("parent", [("My Projects",), ("My Projects", "home")])
@pytest.mark.parametrize("quoted", [".claude/worktrees/r24/x.py",
                                    ".claude/worktrees/r2/../r24/x.py"])
def test_a_name_prefix_worktree_miss_falls_back_to_the_checkout_root(
        sample_repo, tmp_path, parent, quoted):
    # A space in the checkout keeps the generic pattern from rescuing it, so
    # only the literal matcher can rewrite the path.
    base = (_home_shaped(tmp_path, parent[0]) if len(parent) > 1
            else tmp_path / parent[0])
    base.mkdir(parents=True, exist_ok=True)
    root = _clone_fixture(sample_repo, base)
    _git(root, "worktree", "add", "-q", "-b", "r2",
         str(root / ".claude" / "worktrees" / "r2"))
    doc = _build(sample_repo, root,
                 _payload(evidence=f"read {root}/{quoted}:1"))
    assert doc["findings"][0]["evidence"] == \
        "read .claude/worktrees/r24/x.py:1"


@pytest.mark.parametrize("quoted", ["{home}/../other/secret",
                                    "{home}/x/../../other",
                                    "{root}/../../other/x"])
def test_a_home_path_that_walks_out_of_home_is_still_refused(
        sample_repo, tmp_path, monkeypatch, quoted):
    # A `$HOME` the /Users and /home pattern cannot see, and a checkout
    # under it: a path written from that home is a home path however its
    # `..` walks, so it is refused, as the pattern home is.
    home = tmp_path / "service-account"
    root = home / "ck"
    root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(attest_mod.AttestError, match="evidence"):
        _build(sample_repo, root,
               _payload(evidence=quoted.format(home=home, root=root)))


@pytest.mark.parametrize("parent", ["plain", "My Projects"])
@pytest.mark.parametrize("quoted", [".claude/worktrees/r24/x.py",
                                    ".claude/worktrees/r2/../r24/x.py"])
def test_a_walked_path_is_claimed_by_the_deepest_worktree_holding_it(
        sample_repo, tmp_path, parent, quoted):
    base = tmp_path / parent
    base.mkdir()
    root = _clone_fixture(sample_repo, base)
    for name in ("r2", "r24"):
        _git(root, "worktree", "add", "-q", "-b", name,
             str(root / ".claude" / "worktrees" / name))
    doc = _build(sample_repo, root,
                 _payload(evidence=f"read {root}/{quoted}:1"))
    assert doc["findings"][0]["evidence"] == "read x.py:1"


@pytest.mark.parametrize("folder", ["c++", "C#", "50%", "a%20b", "x!", "y*", "z^", "w$"])
def test_a_folder_ending_on_punctuation_stays_inside_its_path(
        sample_repo, tmp_path, folder):
    root = tmp_path / "repo"
    root.mkdir()
    doc = _build(sample_repo, root,
                 _payload(evidence=f"see {root}/{folder}/x.py and "
                                   f"{root}/{folder}/../../other/x"))
    # Rewritten under the root; walked out of it, left as written.
    assert doc["findings"][0]["evidence"] == \
        f"see {folder}/x.py and {root}/{folder}/../../other/x"


@pytest.mark.parametrize("folder", ["c++", "C#", "50%", "a%20b", "x!", "y*", "z^", "w$"])
def test_a_folder_ending_on_punctuation_under_a_home_checkout_walking_out_is_refused(
        sample_repo, tmp_path, folder):
    root = _home_shaped(tmp_path, "repo")
    assert _build(sample_repo, root, _payload(
        evidence=f"{root}/{folder}/x.py"))["findings"][0]["evidence"] == \
        f"{folder}/x.py"
    with pytest.raises(attest_mod.AttestError, match="evidence"):
        _build(sample_repo, root,
               _payload(evidence=f"{root}/{folder}/../../../other/x"))


@pytest.mark.parametrize("walk", ["../../other/x", "../.."])
@pytest.mark.parametrize("folder", ["a=", "b:", "q?", "amp&"])
def test_a_folder_ending_on_a_separator_under_a_home_checkout_walking_out_is_refused(
        sample_repo, tmp_path, folder, walk):
    # A trailing `..` is a path segment, not sentence punctuation.
    root = _home_shaped(tmp_path, "repo")
    assert _build(sample_repo, root, _payload(
        evidence=f"{root}/{folder}/x.py"))["findings"][0]["evidence"] == \
        f"{folder}/x.py"
    with pytest.raises(attest_mod.AttestError, match="evidence"):
        _build(sample_repo, root,
               _payload(evidence=f"{root}/{folder}/{walk}"))


@pytest.mark.parametrize("spelling", ["root", "link"])
@pytest.mark.parametrize("folder", ["a=", "b:", "q?", "amp&"])
def test_a_folder_ending_on_a_separator_walking_out_is_left_as_written(
        sample_repo, tmp_path, folder, spelling):
    # Through a link only the generic pattern sees the path, so the walk-out
    # check must hold in that pass too.
    root = tmp_path / "repo"
    root.mkdir()
    link = tmp_path / "via-link"
    link.symlink_to(root)
    quoted = f"{root if spelling == 'root' else link}/{folder}/../../other/x"
    doc = _build(sample_repo, root, _payload(evidence=quoted))
    assert doc["findings"][0]["evidence"] == quoted


@pytest.mark.parametrize("trailer", ["?", "!", ":", ".", ",", ";", ":12", "&y=1"])
@pytest.mark.parametrize("folder", ["a=", "x"])
def test_a_final_dotdot_followed_by_punctuation_under_a_home_checkout_is_refused(
        sample_repo, tmp_path, folder, trailer):
    # Punctuation after a final `..` trails the path, as after any other
    # name; it does not turn the `..` into a folder that stays inside.
    root = _home_shaped(tmp_path, "repo")
    with pytest.raises(attest_mod.AttestError, match="evidence"):
        _build(sample_repo, root,
               _payload(evidence=f"{root}/{folder}/../..{trailer}"))


@pytest.mark.parametrize("trailer", ["?", "!", ":", ".", ",", ";", ":12", "&y=1"])
@pytest.mark.parametrize("folder", ["a=", "x"])
@pytest.mark.parametrize("checkout, spelling", [
    ("plain", "root"), ("plain", "link"), ("home", "link")])
def test_a_final_dotdot_followed_by_punctuation_walking_out_is_left_as_written(
        sample_repo, tmp_path, folder, trailer, checkout, spelling):
    root = _home_shaped(tmp_path, "repo") if checkout == "home" \
        else tmp_path / "repo"
    root.mkdir(exist_ok=True)
    link = tmp_path / "via-link"
    link.symlink_to(root)
    quoted = (f"{root if spelling == 'root' else link}"
              f"/{folder}/../..{trailer}")
    doc = _build(sample_repo, root, _payload(evidence=quoted))
    assert doc["findings"][0]["evidence"] == quoted


@pytest.mark.parametrize("home", [False, True])
@pytest.mark.parametrize("shape, expected", [
    ("{root}/x/..", "."),
    ("in {root}/x/.., then", "in ., then"),
    ("{root}/x/..?", ".?"),
    ("{root}/x/../y.py?", "y.py?"),
    ("see {root}/x.py.", "see x.py."),
    ("see {root}/a=/x.py.", "see a=/x.py."),
])
def test_a_final_dotdot_is_a_segment_and_a_full_stop_after_a_name_stays_one(
        sample_repo, tmp_path, shape, expected, home):
    root = _home_shaped(tmp_path, "repo") if home else tmp_path / "repo"
    root.mkdir(exist_ok=True)
    doc = _build(sample_repo, root,
                 _payload(evidence=shape.format(root=root)))
    assert doc["findings"][0]["evidence"] == expected


def _checkout(tmp_path: Path, home: bool) -> tuple[Path, Path]:
    """A checkout, plain or under a home-shaped path, and a symlink to it
    that only the generic pattern can see."""
    root = _home_shaped(tmp_path, "repo") if home else tmp_path / "repo"
    root.mkdir(exist_ok=True)
    link = tmp_path / "via-link"
    link.symlink_to(root)
    return root, link


@pytest.mark.parametrize("home", [False, True])
@pytest.mark.parametrize("spelling", ["root", "link"])
@pytest.mark.parametrize("shape, expected", [
    ("go test {root}/internal/...", "go test internal/..."),
    ("go test {root}/internal/...,", "go test internal/...,"),
    ("{root}/x/...hidden", "x/...hidden"),
    ("go vet {root}/adapters/pg/... passed", "go vet adapters/pg/... passed"),
    ("{root}/x/...hidden.", "x/...hidden."),
])
def test_a_segment_starting_with_three_dots_is_an_ordinary_name(
        sample_repo, tmp_path, shape, expected, spelling, home):
    # Go package patterns. A run of dots alone is rewritten only because its
    # other reading, `..` before a full stop, stays inside too.
    root, link = _checkout(tmp_path, home)
    quoted = shape.format(root=root if spelling == "root" else link)
    doc = _build(sample_repo, root, _payload(evidence=quoted))
    assert doc["findings"][0]["evidence"] == expected


@pytest.mark.parametrize("shape", [
    "go test {root}/...",
    "{root}/x/../...",
    "it walked out to {root}/x/../... then",
    "{root}/a=/../...",
    "{root}/x/../....",
    "{root}/...?{root}/x",
    "{root}/...?/usr/x",
])
def test_a_final_run_of_dots_walking_out_as_dotdot_under_a_home_checkout_is_refused(
        sample_repo, tmp_path, shape):
    # `...` is equally `..` and a full stop; that reading walks out.
    root = _home_shaped(tmp_path, "repo")
    with pytest.raises(attest_mod.AttestError, match="evidence"):
        _build(sample_repo, root, _payload(evidence=shape.format(root=root)))


@pytest.mark.parametrize("shape", [
    "go test {root}/...",
    "{root}/x/../...",
    "it walked out to {root}/x/../... then",
    "{root}/a=/../...",
    "{root}/x/../....",
    "{root}/...?/usr/x",
])
@pytest.mark.parametrize("checkout, spelling", [
    ("plain", "root"), ("plain", "link"), ("home", "link")])
def test_a_final_run_of_dots_walking_out_as_dotdot_is_left_as_written(
        sample_repo, tmp_path, shape, checkout, spelling):
    root, link = _checkout(tmp_path, checkout == "home")
    quoted = shape.format(root=root if spelling == "root" else link)
    doc = _build(sample_repo, root, _payload(evidence=quoted))
    assert doc["findings"][0]["evidence"] == quoted


@pytest.mark.parametrize("trailer", [
    "#L12", "~", "%20", "*", "”", "…", "—"])
@pytest.mark.parametrize("folder", ["a=", "x"])
def test_a_final_dotdot_before_a_non_name_character_under_a_home_checkout_is_refused(
        sample_repo, tmp_path, folder, trailer):
    root = _home_shaped(tmp_path, "repo")
    with pytest.raises(attest_mod.AttestError, match="evidence"):
        _build(sample_repo, root,
               _payload(evidence=f"{root}/{folder}/../..{trailer}"))


@pytest.mark.parametrize("trailer", [
    "#L12", "~", "%20", "*", "”", "…", "—"])
@pytest.mark.parametrize("folder", ["a=", "x"])
@pytest.mark.parametrize("checkout, spelling", [
    ("plain", "root"), ("plain", "link"), ("home", "link")])
def test_a_final_dotdot_before_a_non_name_character_walking_out_is_left_as_written(
        sample_repo, tmp_path, folder, trailer, checkout, spelling):
    root, link = _checkout(tmp_path, checkout == "home")
    quoted = (f"{root if spelling == 'root' else link}"
              f"/{folder}/../..{trailer}")
    doc = _build(sample_repo, root, _payload(evidence=quoted))
    assert doc["findings"][0]["evidence"] == quoted


@pytest.mark.parametrize("home", [False, True])
@pytest.mark.parametrize("shape, expected", [
    ("the root {root}..", "the root ."),
    ("the root {root}...", "the root ."),
])
def test_every_dot_after_the_bare_root_is_dropped(
        sample_repo, tmp_path, shape, expected, home):
    # One dot kept would render `..`, the parent.
    root, _ = _checkout(tmp_path, home)
    doc = _build(sample_repo, root,
                 _payload(evidence=shape.format(root=root)))
    assert doc["findings"][0]["evidence"] == expected


@pytest.mark.parametrize("home", [False, True])
@pytest.mark.parametrize("shape, expected", [
    ("{link}/x/..", "."),
    ("in {link}/x/.., then", "in ., then"),
    ("{link}/x/..?", ".?"),
    ("{link}/x/..#L12", ".#L12"),
    ("{link}/x/../y.py?", "y.py?"),
])
def test_a_final_dotdot_quoted_through_a_link_is_a_segment(
        sample_repo, tmp_path, shape, expected, home):
    root, link = _checkout(tmp_path, home)
    doc = _build(sample_repo, root,
                 _payload(evidence=shape.format(link=link)))
    assert doc["findings"][0]["evidence"] == expected


@pytest.mark.parametrize("shape, names_prefix", [
    ("host:{root}/..", False),
    ("host:{root}/..?", False),
    ("host:{root}/..#L12", False),
    ("host:{root}/x/..", True),
    ("host:{root}/x/...", True),
    ("host:{root}/...", False),
])
def test_a_prefixed_path_names_what_it_follows_only_while_it_stays_under_the_root(
        sample_repo, tmp_path, shape, names_prefix):
    root = _home_shaped(tmp_path, "repo")
    with pytest.raises(attest_mod.AttestError) as refused:
        _build(sample_repo, root, _payload(evidence=shape.format(root=root)))
    err = str(refused.value)
    assert "evidence" in err, err
    assert ("'host:'" in err) is names_prefix, err


@pytest.mark.parametrize("home", [False, True])
@pytest.mark.parametrize("spelling", ["root", "link"])
@pytest.mark.parametrize("shape, expected", [
    ("{root}/...hidden", "...hidden"),
    ("{root}/...hidden.", "...hidden."),
    ("see {root}/...hidden,", "see ...hidden,"),
])
def test_a_root_level_segment_starting_with_three_dots_and_a_name_is_rewritten_never_refused(
        sample_repo, tmp_path, shape, expected, spelling, home):
    # At the root the `..` reading would walk out, so only a name reading
    # keeps this from being refused under a home checkout.
    root, link = _checkout(tmp_path, home)
    quoted = shape.format(root=root if spelling == "root" else link)
    doc = _build(sample_repo, root, _payload(evidence=quoted))
    assert doc["findings"][0]["evidence"] == expected


@pytest.mark.parametrize("home", [False, True])
@pytest.mark.parametrize("spelling", ["root", "link"])
@pytest.mark.parametrize("shape, expected", [
    ("{root}/x/..foo", "x/..foo"),
    ("{root}/..foo", "..foo"),
    ("{root}/x/..foo.py:12", "x/..foo.py:12"),
])
def test_a_segment_starting_with_dotdot_and_a_letter_is_a_name_not_a_segment(
        sample_repo, tmp_path, shape, expected, spelling, home):
    root, link = _checkout(tmp_path, home)
    quoted = shape.format(root=root if spelling == "root" else link)
    doc = _build(sample_repo, root, _payload(evidence=quoted))
    assert doc["findings"][0]["evidence"] == expected
