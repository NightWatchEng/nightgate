"""verify: repo.yaml-declared commands run fail-closed with a schema-valid artifact."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest
import yaml

from warden import config as config_mod
from warden import verify as verify_mod


def _config_with_verify(sample_repo: Path, tmp_path: Path,
                        verify_map: dict) -> config_mod.RepoConfig:
    raw = yaml.safe_load((sample_repo / "repo.yaml").read_text())
    raw["verify"] = verify_map
    (tmp_path / "repo.yaml").write_text(yaml.safe_dump(raw))
    return config_mod.load(tmp_path)


def test_all_commands_pass(sample_repo, tmp_path):
    cfg = _config_with_verify(sample_repo, tmp_path,
                              {"ok": [{"run": "true"}, {"run": "echo hello"}]})
    doc = verify_mod.run_scope(cfg, "ok")
    assert doc["passed"] is True
    assert [r["exit_code"] for r in doc["results"]] == [0, 0]
    assert "hello" in doc["results"][1]["output_tail"]


def test_any_failure_fails_scope_but_runs_all_steps(sample_repo, tmp_path):
    cfg = _config_with_verify(sample_repo, tmp_path,
                              {"mixed": [{"run": "false"}, {"run": "true"}]})
    doc = verify_mod.run_scope(cfg, "mixed")
    assert doc["passed"] is False
    assert [r["exit_code"] for r in doc["results"]] == [1, 0]


def test_cwd_is_honored(sample_repo, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "marker.txt").write_text("x")
    cfg = _config_with_verify(sample_repo, tmp_path,
                              {"cwd": [{"run": "ls marker.txt", "cwd": "sub"}]})
    assert verify_mod.run_scope(cfg, "cwd")["passed"] is True


def test_unknown_scope_rejected(sample_repo, tmp_path):
    cfg = _config_with_verify(sample_repo, tmp_path, {"only": [{"run": "true"}]})
    with pytest.raises(verify_mod.VerifyError, match="unknown verify scope"):
        verify_mod.run_scope(cfg, "nope")


def test_render_summary_shows_fail_tail(sample_repo, tmp_path):
    cfg = _config_with_verify(sample_repo, tmp_path,
                              {"s": [{"run": "echo boom >&2; exit 3"}]})
    doc = verify_mod.run_scope(cfg, "s")
    text = verify_mod.render_summary(doc)
    assert "FAIL" in text
    assert "exit 3" in text
    assert "boom" in text


def test_contracts_scope_passes(sample_cfg):
    """The fixture repo's declared contracts scope runs green end to end."""
    doc = verify_mod.run_scope(sample_cfg, "contracts")
    assert doc["passed"] is True, doc["results"][0]["output_tail"]


def test_contracts_scope_catches_broken_fixture(sample_repo, tmp_path):
    """Regression guard: an invalid fixture must fail the scope (exit path intact)."""
    bad = tmp_path / "bad_fixture.json"
    bad.write_text('{"not even json')
    cfg = _config_with_verify(sample_repo, tmp_path, {"contracts": [
        {"run": f"python3 -c \"import json; json.load(open('{bad}'))\""}
    ]})
    doc = verify_mod.run_scope(cfg, "contracts")
    assert doc["passed"] is False


def test_verify_result_records_the_commit_it_judged(sample_cfg):
    """Without a SHA in the artifact, nothing downstream can tell which
    commit a verify result describes."""
    doc = verify_mod.run_scope(sample_cfg, "smoke")
    assert len(doc["head_sha"]) == 40


def test_verify_head_sha_is_unknown_outside_a_git_repo(sample_repo, tmp_path):
    """No git history -> the provenance is honestly 'unknown', never guessed."""
    cfg = _config_with_verify(sample_repo, tmp_path, {"ok": [{"run": "true"}]})
    assert verify_mod.run_scope(cfg, "ok")["head_sha"] == "unknown"


def test_verify_records_the_pr_head_sha_when_ci_supplies_the_event(sample_cfg, tmp_path,
                                                                  monkeypatch):
    """In a pull_request job HEAD is the merge commit, so the run must also
    record the PR head it reports about."""
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"head": {"sha": "d" * 40}}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    doc = verify_mod.run_scope(sample_cfg, "smoke")
    assert doc["pr_head_sha"] == "d" * 40
    assert doc["head_sha"] != "d" * 40  # the merge ref, still recorded honestly


def test_verify_records_no_pr_head_sha_outside_a_pull_request(sample_cfg, tmp_path,
                                                             monkeypatch):
    """A push event, or an unreadable payload, means "not a PR run" — never a
    guessed SHA."""
    event = tmp_path / "push-event.json"
    event.write_text(json.dumps({"ref": "refs/heads/main"}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert "pr_head_sha" not in verify_mod.run_scope(sample_cfg, "smoke")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "nope.json"))
    assert "pr_head_sha" not in verify_mod.run_scope(sample_cfg, "smoke")


def test_verify_records_a_dirty_working_tree(sample_repo, tmp_path):
    """Commands that ran against uncommitted content did not judge HEAD, and
    the artifact must not imply they did."""
    cfg = _config_with_verify(sample_repo, tmp_path, {"ok": [{"run": "true"}]})
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run([*git, "add", "repo.yaml"], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "fixture"], cwd=tmp_path, check=True)
    assert verify_mod.run_scope(cfg, "ok")["dirty"] is False

    (tmp_path / "repo.yaml").write_text(
        (tmp_path / "repo.yaml").read_text() + "\n# uncommitted edit\n")
    assert verify_mod.run_scope(cfg, "ok")["dirty"] is True


def test_render_summary_surfaces_a_dirty_tree(sample_repo, tmp_path):
    """verify deliberately RUNS on a dirty tree (it is mid-iteration
    tooling; attest is the final evidence claim and refuses). That asymmetry
    is safe only while the flag is surfaced everywhere the result is met:
    the artifact records `dirty`, the audit comment shows it, and the
    runner's own terminal — render_summary — must say it too."""
    cfg = _config_with_verify(sample_repo, tmp_path, {"s": [{"run": "true"}]})
    doc = verify_mod.run_scope(cfg, "s")
    doc["dirty"] = True
    text = verify_mod.render_summary(doc)
    assert "dirty" in text
    assert "HEAD" in text, "the warning must say what the verdict may not describe"


def test_render_summary_stays_quiet_on_a_clean_tree(sample_repo, tmp_path):
    cfg = _config_with_verify(sample_repo, tmp_path, {"s": [{"run": "true"}]})
    doc = verify_mod.run_scope(cfg, "s")
    doc["dirty"] = False
    assert "dirty" not in verify_mod.render_summary(doc)


def test_render_summary_flags_an_unrecorded_tree_state(sample_repo, tmp_path):
    """is_dirty is tri-state, and the None case (git status failed) must not
    collapse to silence — rendering exactly like a clean tree, against
    runs.py's own contract that absent means 'not recorded', never
    'clean'."""
    cfg = _config_with_verify(sample_repo, tmp_path, {"s": [{"run": "true"}]})
    doc = verify_mod.run_scope(cfg, "s")
    doc.pop("dirty", None)
    text = verify_mod.render_summary(doc)
    assert "not recorded" in text


# --- which scopes a diff requires ------------------------------------------

def _config_with(sample_repo: Path, tmp_path: Path, verify_map: dict,
                 components: dict | None = None) -> config_mod.RepoConfig:
    """A config with a chosen verify map and, optionally, chosen components —
    the two declarations `scopes_for_paths` reads together."""
    raw = yaml.safe_load((sample_repo / "repo.yaml").read_text())
    raw["verify"] = verify_map
    if components is not None:
        raw["components"] = components
    (tmp_path / "repo.yaml").write_text(yaml.safe_dump(raw))
    return config_mod.load(tmp_path)


def test_a_root_scope_covers_every_path_a_subtree_scope_only_its_own(
        sample_repo, tmp_path):
    """A step at the repo root makes no narrowness claim, so it covers the
    whole tree; a step whose cwd is a declared component area covers that
    subtree only. No shell parsing — the two declarations are the whole
    derivation."""
    cfg = _config_with(sample_repo, tmp_path, {
        "tests": [{"run": "pytest -q"}],
        "contracts": [{"run": "true", "cwd": "schemas"}],
    })
    assert verify_mod.scopes_for_paths(cfg, ("app/x.py",)) == ("tests",)
    assert verify_mod.scopes_for_paths(
        cfg, ("schemas/api.schema.json",)) == ("contracts", "tests")


def test_a_cwd_narrows_a_scope_only_where_a_component_declares_that_area(
        sample_repo, tmp_path):
    """Reading `cwd` alone as a reach declaration fails OPEN: it drops a
    scope whose commands exercise the whole tree from a subdirectory —
    `{lint: [ruff check .], tests: [pytest, cwd backend]}` would answer
    ("lint",) for a `frontend/` diff, so the gate would render one green line
    and never ask for the test scope.
    A `cwd` is a runner convenience; a `components:` entry is a deliberate
    statement that an area exists. Narrowing takes BOTH."""
    verify_map = {"lint": [{"run": "ruff check ."}],
                  "tests": [{"run": "pytest", "cwd": "backend"}]}
    undeclared = _config_with(sample_repo, tmp_path, verify_map, components={
        "app": {"path": "app/", "lang": "python", "description": "app"},
    })
    assert verify_mod.scopes_for_paths(
        undeclared, ("frontend/x.js",)) == ("lint", "tests")

    declared = _config_with(sample_repo, tmp_path, verify_map, components={
        "app": {"path": "app/", "lang": "python", "description": "app"},
        "backend": {"path": "backend/", "lang": "python", "description": "be"},
    })
    assert verify_mod.scopes_for_paths(declared, ("frontend/x.js",)) == ("lint",)
    assert verify_mod.scopes_for_paths(
        declared, ("backend/svc.py",)) == ("lint", "tests")


def test_undeterminable_and_empty_are_distinct_states_with_one_safe_answer(
        sample_repo, tmp_path):
    """Fail-closed: `None` (the caller could not compute the changed paths),
    `()` (a range that resolved and changed nothing) and paths no declared
    scope claims all require EVERY declared scope. The two unknowns are
    separate arguments the signature accepts separately — the tri-state house
    rule `runs.is_dirty` keeps — even though the safe answer is one answer."""
    cfg = _config_with(sample_repo, tmp_path, {
        "a": [{"run": "true", "cwd": "app"}],
        "s": [{"run": "true", "cwd": "schemas"}],
    })
    assert verify_mod.scopes_for_paths(cfg, None) == ("a", "s")
    assert verify_mod.scopes_for_paths(cfg, ()) == ("a", "s")
    assert verify_mod.scopes_for_paths(cfg, ("unclaimed/x.py",)) == ("a", "s")


def test_a_cwd_prefix_is_matched_on_path_segments(sample_repo, tmp_path):
    """`schemas-old/x` is not inside `schemas`; a bare string prefix would say
    it was, and credit a scope that never ran a command over that path."""
    cfg = _config_with(sample_repo, tmp_path, {
        "narrow": [{"run": "true", "cwd": "schemas"}],
        "root": [{"run": "true"}],
    })
    assert verify_mod.scopes_for_paths(cfg, ("schemas-old/x.py",)) == ("root",)
    assert verify_mod.scopes_for_paths(
        cfg, ("schemas/x.json",)) == ("narrow", "root")


def test_a_cwd_that_escapes_the_repo_widens_rather_than_narrows(
        sample_repo, tmp_path):
    """An absolute cwd, or one climbing out through '..', makes no narrowness
    claim this derivation can read — and an unreadable claim must widen the
    requirement, not silently exempt the scope from every diff."""
    cfg = _config_with(sample_repo, tmp_path, {
        "escaped": [{"run": "true", "cwd": "../elsewhere"}],
        "rooted": [{"run": "true", "cwd": "/opt/thing"}],
        "narrow": [{"run": "true", "cwd": "app"}],
    })
    assert verify_mod.scopes_for_paths(
        cfg, ("schemas/x.json",)) == ("escaped", "rooted")


def test_this_repo_maps_a_warden_diff_to_the_tests_scope_alone():
    """Against THIS repo's real repo.yaml: a warden diff requires the tests
    scope, and the example scope (the hello-svc selfcheck) does not claim
    it. `examples/hello-svc` narrows only because repo.yaml also declares
    the `examples` component."""
    cfg = config_mod.load(Path(__file__).resolve().parents[1])
    assert verify_mod.scopes_for_paths(
        cfg, ("warden/repair.py", "warden/cli.py",
              "tests/test_repair_stop.py")) == ("tests",)
    assert verify_mod.scopes_for_paths(
        cfg, ("examples/hello-svc/hello_svc/app.py",)) == ("example", "tests")


# --- the reach a scope DECLARES, not the one warden infers -----------------

def test_a_declared_covers_beats_the_cwd_plus_components_inference(
        sample_repo, tmp_path):
    """The inference exempts a scope from every diff outside its
    `cwd` once a component declares that area — so a command that REACHES
    outside it (`pytest ../..`, a tool whose config sits at the repo root) is
    silently exempted. `covers:` makes the reach a declaration: this step runs
    in `backend/` and exercises the whole tree, and says so."""
    # TWO scopes, deliberately. With one scope this test would pass with
    # `covers` support deleted, because `scopes_for_paths` falls back to
    # EVERY declared scope when nothing matches, and every declared scope
    # would be this one. The second scope is what
    # makes the widening observable: under the inference alone `tests` is
    # exempt from a frontend diff and the answer is ("lint",).
    cfg = _config_with(sample_repo, tmp_path, {
        "lint": [{"run": "ruff check .", "covers": ["**"]}],
        "tests": [{"run": "pytest ../..", "cwd": "backend",
                   "covers": ["**"]}],
    }, components={
        "backend": {"path": "backend/", "lang": "python", "description": "be"},
    })
    assert verify_mod.scopes_for_paths(
        cfg, ("frontend/x.js",)) == ("lint", "tests"), (
        "the declared reach did not widen the backend-cwd step — delete "
        "`covers` support and this is ('lint',)")
    assert verify_mod.scopes_for_paths(
        cfg, ("backend/svc.py",)) == ("lint", "tests")
    # And the mutation this test exists to catch, stated as an assertion: with
    # the SAME config minus the declaration, the inference exempts `tests`.
    inferred = _config_with(sample_repo, tmp_path, {
        "lint": [{"run": "ruff check ."}],
        "tests": [{"run": "pytest ../..", "cwd": "backend"}],
    }, components={
        "backend": {"path": "backend/", "lang": "python", "description": "be"},
    })
    assert verify_mod.scopes_for_paths(inferred, ("frontend/x.js",)) == ("lint",)


def test_a_declared_covers_can_narrow_where_no_component_declares_the_area(
        sample_repo, tmp_path):
    """The other arm: a step whose `cwd` no component claims covers the whole
    tree by inference. A `covers:` declaration narrows it, because
    the author has now STATED the reach instead of leaving warden to guess."""
    verify_map = {"lint": [{"run": "ruff check .", "covers": ["app/**"]}],
                  "tests": [{"run": "pytest", "cwd": "backend"}]}
    cfg = _config_with(sample_repo, tmp_path, verify_map, components={
        "app": {"path": "app/", "lang": "python", "description": "app"},
    })
    assert verify_mod.scopes_for_paths(cfg, ("app/x.py",)) == ("lint", "tests")
    # `lint` is declared to reach app/ only, so a frontend diff does not
    # require it; `tests` still widens, because its cwd claims an area no
    # component declares.
    assert verify_mod.scopes_for_paths(cfg, ("frontend/x.js",)) == ("tests",)


def test_an_absent_covers_behaves_byte_identically_to_the_inference(
        sample_repo, tmp_path):
    """Additive: a repo.yaml with no `covers:` anywhere answers exactly what
    the cwd-plus-components inference answers. Asserted against the SAME
    maps the root-scope test above uses, so the two arms cannot drift."""
    cfg = _config_with(sample_repo, tmp_path, {
        "tests": [{"run": "pytest -q"}],
        "contracts": [{"run": "true", "cwd": "schemas"}],
    })
    assert verify_mod.scopes_for_paths(cfg, ("app/x.py",)) == ("tests",)
    assert verify_mod.scopes_for_paths(
        cfg, ("schemas/api.schema.json",)) == ("contracts", "tests")


def test_the_unknowns_still_widen_over_a_declared_reach(
        sample_repo, tmp_path):
    """Fail-closed is not traded away by the declaration: `None` (could not
    compute the paths) and `()` (resolved to nothing) still require every
    declared scope, however narrow a `covers:` claims to be."""
    cfg = _config_with(sample_repo, tmp_path, {
        "a": [{"run": "true", "covers": ["app/**"]}],
        "s": [{"run": "true", "covers": ["schemas/**"]}],
    })
    assert verify_mod.scopes_for_paths(cfg, None) == ("a", "s")
    assert verify_mod.scopes_for_paths(cfg, ()) == ("a", "s")
    # A path no declared reach claims widens too — the same fallback.
    assert verify_mod.scopes_for_paths(cfg, ("unclaimed/x.py",)) == ("a", "s")


def test_one_step_of_a_scope_declaring_reach_does_not_exempt_the_others(
        sample_repo, tmp_path):
    """A scope is required when ANY of its steps reaches a changed path, and a
    step that declares `covers:` answers only for itself. Mixing a declared
    step with an inferred one must not let either silence the other."""
    cfg = _config_with(sample_repo, tmp_path, {
        "mixed": [{"run": "a", "covers": ["app/**"]},
                  {"run": "b", "cwd": "schemas"}],
    }, components={
        "app": {"path": "app/", "lang": "python", "description": "app"},
        "schemas": {"path": "schemas/", "lang": "json", "description": "s"},
    })
    assert verify_mod.scopes_for_paths(cfg, ("app/x.py",)) == ("mixed",)
    assert verify_mod.scopes_for_paths(cfg, ("schemas/x.json",)) == ("mixed",)
    # Neither step reaches here, so the fallback widens to every scope — which
    # is this one scope.
    assert verify_mod.scopes_for_paths(cfg, ("elsewhere/x",)) == ("mixed",)


def test_this_repos_example_scope_declares_its_reach():
    """The platform's own repo.yaml stops inferring: the hello-svc selfcheck
    says what it exercises. The answers are the ones
    `test_this_repo_maps_a_warden_diff_to_the_tests_scope_alone` pins,
    now held by a declaration rather than by two declarations agreeing."""
    cfg = config_mod.load(Path(__file__).resolve().parents[1])
    example = cfg.verify["example"]
    assert example[0].covers, "the example scope no longer declares its reach"
    assert verify_mod.scopes_for_paths(
        cfg, ("warden/repair.py",)) == ("tests",)
    assert verify_mod.scopes_for_paths(
        cfg, ("examples/hello-svc/hello_svc/app.py",)) == ("example", "tests")


def test_a_directory_spelling_of_covers_means_the_subtree(
        sample_repo, tmp_path):
    """`covers: ["backend/"]` is the spelling repo.yaml uses everywhere else
    for an AREA — `components:` paths and `protected_paths` are written with
    the trailing slash — and under a bare `rules.glob_match` it matches
    NOTHING, so the step would be exempted from every diff while `reach_of`
    still reported `declared`: a declaration meant to close a silent
    exemption would open one.

    A trailing slash means the subtree, exactly as `components:` reads."""
    cfg = _config_with(sample_repo, tmp_path, {
        "lint": [{"run": "ruff check .", "covers": ["**"]}],
        "tests": [{"run": "pytest", "covers": ["backend/"]}],
    })
    assert verify_mod.covers_globs(cfg.verify["tests"][0]) == ("backend/**",)
    assert verify_mod.scopes_for_paths(
        cfg, ("backend/svc.py",)) == ("lint", "tests"), (
        "a trailing-slash covers glob matched nothing, so the scope was "
        "silently exempt from the subtree it names")
    # It still NARROWS — that is the point of declaring a reach.
    assert verify_mod.scopes_for_paths(cfg, ("frontend/x.js",)) == ("lint",)


def test_reach_of_and_the_derivation_answer_from_one_rule(
        sample_repo, tmp_path):
    """`reach_of` must reproduce the WHOLE inference, `components:` test
    included — otherwise a step whose `cwd` sits inside NO declared
    component, which the DERIVATION widens to the whole tree, is reported as
    narrowing by inference. Two readings of one rule drift.

    Both call `step_reach`, and this pins the two answers against each
    other rather than against a re-typed expectation."""
    cfg = _config_with(sample_repo, tmp_path, {
        "undeclared": [{"run": "true", "cwd": "nodeclare"}],
        "declared-area": [{"run": "true", "cwd": "app"}],
    }, components={
        "app": {"path": "app/", "lang": "python", "description": "app"},
    })
    areas = [PurePosixPath("app")]
    wide = cfg.verify["undeclared"][0]
    narrow = cfg.verify["declared-area"][0]
    # the step the derivation widens is NOT reported as narrowing...
    assert verify_mod.step_reach(wide, areas)[0] == verify_mod.INFERRED_WIDE
    assert "no declared component" in verify_mod.reach_of(wide, areas)[1]
    # ...and the one it narrows is.
    assert verify_mod.step_reach(narrow, areas)[0] == verify_mod.INFERRED_NARROW
    # The binding: a step reported WIDE is required by a diff that touches
    # neither cwd, and a step reported NARROW is not.
    assert verify_mod.scopes_for_paths(cfg, ("elsewhere/x",)) == ("undeclared",)


def test_both_covers_descriptions_state_the_trailing_slash_rule():
    """A claim that the schema description states the trailing-slash
    normalization is a claim about a document, so it is checked: the verify
    and deploy descriptions and the wiki page must all carry it.

    This is what keeps them carrying it: an author who normalizes differently
    has three places to change, and this fails until they do."""
    import json as _json
    schema = _json.loads(
        (Path(__file__).resolve().parents[1]
         / "warden" / "schemas" / "repo.schema.json").read_text())
    for block in ("verify", "deploy"):
        desc = schema["properties"][block]["additionalProperties"]["items"][
            "properties"]["covers"]["description"]
        assert "ending in `/` means the SUBTREE" in desc, (
            f"the {block} step's `covers` description does not state the "
            "trailing-slash rule the code implements")
        assert "bare directory NAME is not normalized" in desc, (
            f"the {block} step's `covers` description does not state the limit")
    config = (Path(__file__).resolve().parents[1]
              / "docs" / "wiki" / "Configuration.md").read_text()
    assert "A glob ending in `/` means the SUBTREE" in config


# ── an absent toolchain is DIAGNOSED, not left as a bare 127 ────────────────
#
# A consumer whose machine lacks the toolchain a verify scope needs used to get
# `exit 127` in the step list and `/bin/sh: go: command not found` buried in an
# output tail, with the verdict itself stating no cause. That is the same
# undiagnosed failure that cost this repo a 27-minute unattended run before
# anyone read the log closely enough to see it.
#
# The fix DIAGNOSES without EXCUSING. "Report rather than fail" is refused: a
# scope that could not run
# its commands proved nothing, and a green there is vacuous. Every test below
# therefore asserts BOTH halves: the reason is named, AND the scope is still
# red.

def _no_such_binary(tmp_path: Path) -> str:
    """A command name that certainly does not resolve on this machine."""
    name = "warden-absent-toolchain-probe"
    assert shutil.which(name) is None, "the control: the probe is really absent"
    return name


def test_an_absent_binary_is_named_as_the_failure_reason(sample_repo, tmp_path):
    absent = _no_such_binary(tmp_path)
    cfg = _config_with_verify(sample_repo, tmp_path,
                              {"s": [{"run": f"{absent} vet ./..."}]})
    doc = verify_mod.run_scope(cfg, "s")
    step = doc["results"][0]
    assert doc["passed"] is False, "diagnosing must not excuse: the scope stays red"
    assert step["exit_code"] != 0
    diagnosis = step.get("diagnosis")
    assert diagnosis, "the step failed for want of a binary and said no such thing"
    assert absent in diagnosis, diagnosis
    assert "not on PATH" in diagnosis, diagnosis
    assert os.environ["PATH"] in diagnosis, (
        "the reason names the binary but not the PATH it looked on — which is "
        "the half that diagnoses a cage that RESET PATH while "
        "the binary was installed the whole time")


def test_the_named_reason_reaches_the_summary_a_human_reads(sample_repo, tmp_path):
    """The artifact is not where a runner looks first; stdout is."""
    absent = _no_such_binary(tmp_path)
    cfg = _config_with_verify(sample_repo, tmp_path, {"s": [{"run": absent}]})
    text = verify_mod.render_summary(verify_mod.run_scope(cfg, "s"))
    assert "FAIL" in text
    assert absent in text and "not on PATH" in text, text
    assert "could not be evaluated" in text, text


def test_a_step_that_exits_127_is_diagnosed_even_when_unparseable(
        sample_repo, tmp_path):
    """The second arm. `first_binary` refuses to guess at a compound command,
    so the shell's own 127 carries those. It is a STRONG signal, not an
    infallible one — round 1 (F4) refuted the docstring that called it that —
    so it reports what the shell said and offers the likely cause."""
    absent = _no_such_binary(tmp_path)
    cfg = _config_with_verify(
        sample_repo, tmp_path, {"s": [{"run": f"cd . && {absent} test ./..."}]})
    doc = verify_mod.run_scope(cfg, "s")
    step = doc["results"][0]
    assert step["exit_code"] == 127
    assert doc["passed"] is False
    assert verify_mod.first_binary(step["cmd"]) is None, (
        "the control: this command is exactly the shape first_binary declines")
    assert "127" in step["diagnosis"], step["diagnosis"]
    assert "not a bare invocation" in step["diagnosis"], step["diagnosis"]
    assert "without guessing" in step["diagnosis"], step["diagnosis"]
    assert "absent toolchain is the usual cause" in step["diagnosis"], step["diagnosis"]


def test_an_ordinary_failure_is_not_dressed_up_as_a_missing_toolchain(
        sample_repo, tmp_path):
    """The most important negative. A diagnosis attached to a real test
    failure would tell a consumer to go install something instead of reading
    their own red test — worse than the silence it replaced."""
    cfg = _config_with_verify(sample_repo, tmp_path,
                              {"s": [{"run": "echo boom >&2; exit 3"},
                                     {"run": "false"}]})
    doc = verify_mod.run_scope(cfg, "s")
    assert doc["passed"] is False
    for step in doc["results"]:
        assert "diagnosis" not in step, step


def test_a_passing_step_carries_no_diagnosis(sample_repo, tmp_path):
    cfg = _config_with_verify(sample_repo, tmp_path, {"s": [{"run": "true"}]})
    doc = verify_mod.run_scope(cfg, "s")
    assert doc["passed"] is True
    assert "diagnosis" not in doc["results"][0]


def test_a_shell_builtin_leading_a_step_is_never_named_as_absent():
    """`exit` and `cd` do not resolve on PATH and are not missing binaries.
    Naming one would be a confident wrong answer, which is strictly worse than
    the bare 127 this replaces."""
    for command in ("exit 3", "cd sub && make", ": # nothing",
                    "export FOO=1; ./x", "if true; then false; fi"):
        assert verify_mod.first_binary(command) is None, command
    # and the diagnosis for them comes only from the shell's own verdict
    assert verify_mod.diagnose("exit 3", 3) is None
    assert "127" in verify_mod.diagnose("cd sub && nope", 127)


@pytest.mark.parametrize("command,expected", [
    ("go vet ./...", "go"),
    ("npm install --no-package-lock", "npm"),
    ("PYTHONDONTWRITEBYTECODE=1 uv run --locked pytest -q", "uv"),
    ("NIGHTGATE_REQUIRE_TOOLCHAIN=1 uv run pytest -q", "uv"),
    ("", None),
    # A pipeline's first binary IS the first one, and naming it is right: if
    # the LAST one is the absent one the shell answers 127 and the other arm
    # of `diagnose` says so without guessing which part.
    ("a | b", "a"),
    ("$(which go) test", None),
    # ── the round-1 HIGH. A name carrying a path separator is resolved by the
    # shell against the STEP's cwd, which this function is not given, so it
    # declines rather than asking PATH a question PATH does not answer.
    ("A=1 B=2 ./script.sh", None),
    ("/usr/local/go/bin/go test", None),
    ("./gradlew build", None),
    ("bin/rails test", None),
    ("~/go/bin/golangci-lint run", None),
    ("sub/tool --check", None),
    # a prefix that assigns PATH: the step searches a path this process does
    # not have, so no answer here is sound
    ("PATH=/opt/bin:$PATH mytool --check", None),
    # the builtins that exist as files on one runner and not another
    ("ulimit -n 2048 && go test ./...", None),
    ("pushd sub && make", None),
])
def test_first_binary_names_the_program_or_declines(command, expected):
    assert verify_mod.first_binary(command) == expected


# ── the round-1 HIGH, as named regressions ──────────────────────────────────
#
# Both round-1 reviewers reached this independently with live reproductions:
# `diagnose` resolved a path-bearing name with `shutil.which`, which ignores
# PATH and stats relative to the REPORTING process's cwd. An ordinary red test
# in a step with a `cwd:` was therefore reported as an absent toolchain, with
# "install the toolchain" printed above the real failure output — the false
# positive this feature's own docstring calls the worst outcome. Worse, for a
# root-relative step the diagnosis appeared or not depending on the directory
# the OPERATOR ran `warden` from: the same step at the same exit code writing
# two different evidence artifacts, in a gate whose first principle is
# provenance.

def _script(repo: Path, rel: str, body: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(0o755)


def test_a_relative_script_in_a_cwd_that_fails_is_not_called_absent(
        sample_repo, tmp_path):
    _script(tmp_path, "sub/check.sh", "echo 'real test failure: 3 assertions failed'; exit 1")
    cfg = _config_with_verify(sample_repo, tmp_path,
                              {"s": [{"run": "./check.sh", "cwd": "sub"}]})
    doc = verify_mod.run_scope(cfg, "s")
    step = doc["results"][0]
    assert step["exit_code"] == 1, step
    assert "real test failure" in step["output_tail"], (
        "the control: the script really ran and really failed on the merits")
    assert "diagnosis" not in step, (
        f"an ordinary red test was reported as an absent toolchain: "
        f"{step.get('diagnosis')}")


def test_a_root_relative_script_reads_the_same_from_any_cwd(
        sample_repo, tmp_path, monkeypatch):
    """The non-determinism half. The artifact must not depend on where the
    operator stood when they ran warden."""
    _script(tmp_path, "root.sh", "echo 'FAIL: 3 tests failed'; exit 1")
    (tmp_path / "sub").mkdir(exist_ok=True)
    cfg = _config_with_verify(sample_repo, tmp_path, {"s": [{"run": "./root.sh"}]})

    seen = []
    for where in (tmp_path, tmp_path / "sub"):
        monkeypatch.chdir(where)
        seen.append(verify_mod.run_scope(cfg, "s")["results"][0].get("diagnosis"))
    assert seen == [None, None], (
        f"the diagnosis depends on the operator's cwd: {seen}")


def test_a_step_that_sets_its_own_path_is_not_judged_against_ours(
        sample_repo, tmp_path):
    bindir = tmp_path / "extrabin"
    bindir.mkdir()
    _script(tmp_path, "extrabin/mytool", "echo 'FAIL: assertion x != y'; exit 1")
    cfg = _config_with_verify(
        sample_repo, tmp_path,
        {"s": [{"run": f"PATH={bindir}:$PATH mytool --check"}]})
    step = verify_mod.run_scope(cfg, "s")["results"][0]
    assert step["exit_code"] == 1 and "assertion" in step["output_tail"], step
    assert "diagnosis" not in step, step.get("diagnosis")


def test_the_127_arm_no_longer_claims_what_it_cannot_know():
    """F4, tightened by R2F2. 127 is an ordinary exit status a program may
    return on its own, so the wording states what is known and names both
    possibilities. It must not assert that some OTHER part of the step did not
    run, because for a single-token step there is no other part."""
    text = verify_mod.diagnose("make test", 127)
    assert text and "not a bare invocation" not in text, text
    assert "`make` itself resolves" in text, text
    assert "returned 127 of its own accord" in text, text

    # R2F2: the single-token case. No "other part" may be asserted.
    lone = verify_mod.diagnose("make", 127)
    assert "another part of the step" not in lone, lone
    assert "something else the step reached" in lone, lone

    compound = verify_mod.diagnose("cd sub && nope", 127)
    assert "not a bare invocation" in compound, compound


def test_a_multi_line_step_is_not_judged_by_its_first_line(
        sample_repo, tmp_path):
    """R2F1 — the round-1 HIGH, still alive in a block scalar.

    `run: |` is a plain string to the schema and consumers mirror CI's block
    scalar, so `sh -c` runs EVERY line while a split() over the text sees only
    the first word of the first one. Round 2 reproduced the exact artifact F1
    called the worst outcome: a first line naming an absent optional tool put
    'install the toolchain' above a later line's real test failure.
    """
    _script(tmp_path, "sub/check.sh", "echo 'real test failure: 3 assertions failed'; exit 1")
    absent = _no_such_binary(tmp_path)
    cfg = _config_with_verify(sample_repo, tmp_path, {"s": [
        {"run": f"{absent} --warm-cache\n./sub/check.sh\n"}]})
    step = verify_mod.run_scope(cfg, "s")["results"][0]
    assert "real test failure" in step["output_tail"], (
        "the control: the later line really ran and really failed")
    assert verify_mod.first_binary(step["cmd"]) is None, (
        "a multi-line step has no single program to name")
    assert "not on PATH" not in (step.get("diagnosis") or ""), (
        f"the step was accused of an absent toolchain over a real failure: "
        f"{step.get('diagnosis')}")


def test_a_present_but_unexecutable_binary_is_named_as_that(tmp_path):
    """R2F3 — shutil.which demands X_OK, so it answers 'absent' for a file
    sitting right there without the execute bit. Telling a reader to install
    something they already have is the same class of wrong answer as F1."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "notexec").write_text("#!/bin/sh\nexit 0\n")
    (bindir / "notexec").chmod(0o644)
    text = verify_mod.diagnose("notexec --check", 126, path=str(bindir))
    assert text and "is not on PATH" not in text, text
    assert "is on PATH at" in text and "not executable" in text, text
    assert str(bindir / "notexec") in text, text


def test_diagnose_reports_against_the_path_it_is_given(tmp_path):
    """The PATH is a parameter, so the diagnosis describes the environment the
    step ran in rather than whatever the reporting process happens to have."""
    assert verify_mod.diagnose("go vet", 127, path=str(tmp_path)).startswith(
        "`go` is not on PATH")
    real = shutil.which("sh")
    assert real
    assert verify_mod.diagnose("sh -c x", 1, path=str(Path(real).parent)) is None


# ── check_head: the pre-push hook's artifact question ─────────────────────

_SHA = "c" * 40


def _artifact(root: Path, name: str, drop: tuple = (), **over) -> None:
    doc = {"scope": "ok", "passed": True, "head_sha": _SHA, "dirty": False,
           "results": [{"cmd": "true", "cwd": ".", "exit_code": 0,
                        "duration_s": 0.1}]}
    doc.update(over)
    for key in drop:
        del doc[key]
    run_dir = root / ".warden" / "out" / f"{name}-verify"
    run_dir.mkdir(parents=True)
    (run_dir / "verify-result.json").write_text(json.dumps(doc))


def test_check_head_accepts_a_clean_pass_and_names_it(sample_repo, tmp_path):
    cfg = _config_with_verify(sample_repo, tmp_path, {"ok": [{"run": "true"}]})
    _artifact(tmp_path, "20260927T000000000000Z")
    accepted, why = verify_mod.check_head(cfg, "ok", _SHA)
    assert accepted == tmp_path / ".warden/out/20260927T000000000000Z-verify"
    assert "PASS" in why


@pytest.mark.parametrize("over, said", [
    ({"dirty": True}, "DIRTY"),
    ({"drop": ("dirty",)}, "no tree state"),
    ({"head_sha": "d" * 40, "pr_head_sha": _SHA}, "standing for"),
    ({"results": [{"cmd": "true"}]}, "not a valid verify result"),
    ({"passed": True, "results": [{"cmd": "true", "cwd": ".", "exit_code": 1,
                                   "duration_s": 0.1}]}, "FAIL"),
])
def test_check_head_refuses_what_did_not_judge_this_commit_clean(
        sample_repo, tmp_path, over, said):
    cfg = _config_with_verify(sample_repo, tmp_path, {"ok": [{"run": "true"}]})
    _artifact(tmp_path, "20260927T000000000000Z", **over)
    accepted, why = verify_mod.check_head(cfg, "ok", _SHA)
    assert accepted is None and said in why


def test_check_head_takes_the_newest_artifact_not_any_pass(sample_repo, tmp_path):
    """A newer FAIL on the same commit is the verdict; an older PASS beneath it
    is not evidence about the tree as it stands."""
    cfg = _config_with_verify(sample_repo, tmp_path, {"ok": [{"run": "true"}]})
    _artifact(tmp_path, "20260927T000000000000Z")
    _artifact(tmp_path, "20260927T000001000000Z", passed=False)
    accepted, why = verify_mod.check_head(cfg, "ok", _SHA)
    assert accepted is None and "FAIL" in why


def test_check_head_refuses_an_undeclared_scope(sample_repo, tmp_path):
    cfg = _config_with_verify(sample_repo, tmp_path, {"ok": [{"run": "true"}]})
    with pytest.raises(verify_mod.VerifyError, match="unknown verify scope"):
        verify_mod.check_head(cfg, "nope", _SHA)


def _committed(tmp_path: Path) -> list[str]:
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".warden/\n")
    subprocess.run([*git, "add", "."], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "fixture"], cwd=tmp_path, check=True)
    return git


def test_a_commit_during_the_run_names_no_commit(sample_repo, tmp_path):
    """Round finding (fail-closed): head was stamped only when the commands
    ENDED, so a commit made while the suite ran was named as the commit
    judged — and `check_head` would skip the pre-push gate for it."""
    cmd = ("git -c user.email=t@t -c user.name=t commit -q --allow-empty "
           "-m mid-run")
    cfg = _config_with_verify(sample_repo, tmp_path, {"ok": [{"run": cmd}]})
    _committed(tmp_path)
    doc = verify_mod.run_scope(cfg, "ok")
    assert doc["passed"] is True
    assert doc["head_sha"] == "unknown", (
        "a run that started on one commit and ended on another named the second")


def test_a_tree_dirty_at_the_start_is_dirty_even_if_cleaned(sample_repo, tmp_path):
    """The same finding's second half: the commands ran on uncommitted
    content that was gone by the time the tree state was stamped."""
    cfg = _config_with_verify(sample_repo, tmp_path,
                              {"ok": [{"run": "git checkout -q -- repo.yaml"}]})
    _committed(tmp_path)
    (tmp_path / "repo.yaml").write_text(
        (tmp_path / "repo.yaml").read_text() + "\n# uncommitted edit\n")
    doc = verify_mod.run_scope(cfg, "ok")
    assert doc["passed"] is True
    assert doc["dirty"] is True, "a run on uncommitted edits was recorded clean"


def test_a_head_that_leaves_and_returns_names_no_commit(sample_repo, tmp_path):
    """Round-2 finding (fail-closed): endpoints alone miss A -> B -> A, a
    switch away and back while the suite ran; HEAD's reflog does not."""
    cmd = "git checkout -q -b elsewhere && git checkout -q main"
    cfg = _config_with_verify(sample_repo, tmp_path, {"ok": [{"run": cmd}]})
    _committed(tmp_path)
    doc = verify_mod.run_scope(cfg, "ok")
    assert doc["passed"] is True
    assert doc["head_sha"] == "unknown", (
        "a HEAD that left and came back mid-run was named as the commit judged")


def test_a_run_that_leaves_head_alone_still_names_it(sample_repo, tmp_path):
    cfg = _config_with_verify(sample_repo, tmp_path, {"ok": [{"run": "true"}]})
    _committed(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
                          capture_output=True, text=True).stdout.strip()
    assert verify_mod.run_scope(cfg, "ok")["head_sha"] == head


# --- pytest counts on the summary line -------------------------------------
#
# `warden verify` printed PASS and nothing else, so the commit Evidence line's
# "N tests passed" was read out of a gitignored artifact. The summary line now
# carries the counts for every step that DECLARES `runner: pytest`, parsed
# from each step's whole output, which `run_scope` hands back beside the
# document. The command text is never read to guess the runner.
# Where the counts cannot be read, the line SAYS so rather than printing a
# number.

PYTEST = "pytest"


def _first_line(scope: str, passed: bool, *steps: tuple) -> str:
    """The summary line for a scope whose steps are (cmd, output[, runner])."""
    doc = {"scope": scope, "passed": passed, "dirty": False, "results": [
        {"cmd": cmd, "cwd": ".", "exit_code": 0 if passed else 1,
         "duration_s": 1.0, "output_tail": out[-verify_mod._TAIL_CHARS:],
         **({"runner": rest[0]} if rest else {})}
        for cmd, out, *rest in steps]}
    outputs = [out for _, out, *_ in steps]
    return verify_mod.render_summary(doc, outputs=outputs).splitlines()[0]


def _undeclared(cmd: str) -> str:
    return (f"pytest: no counts — `{cmd}` printed a pytest summary and does "
            "not declare `runner: pytest`")


def _mixed_suite(tmp_path: Path, *, params: int = 0) -> str:
    """A tiny suite with one of each outcome — plus `params` more passing
    cases — and the step that runs it verbosely."""
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "test_mix.py").write_text(
        "import pytest\n"
        "def test_a(): pass\n"
        "def test_b(): pass\n"
        "def test_c(): assert False\n"
        "@pytest.mark.skip(reason='x')\n"
        "def test_d(): pass\n"
        + (f"@pytest.mark.parametrize('n', range({params}))\n"
           "def test_many(n): pass\n" if params else ""))
    return f"{sys.executable} -m pytest -v -p no:cacheprovider -p no:xdist t"


def test_a_declared_step_prints_its_counts(sample_repo, tmp_path):
    cmd = _mixed_suite(tmp_path)
    cfg = _config_with_verify(sample_repo, tmp_path,
                              {"tests": [{"run": cmd, "runner": PYTEST}]})
    outputs: list[str] = []
    doc = verify_mod.run_scope(cfg, "tests", outputs=outputs)
    assert doc["results"][0]["runner"] == PYTEST, "the artifact records it"
    first = verify_mod.render_summary(doc, outputs=outputs).splitlines()[0]
    assert first.startswith("verify --scope tests: FAIL"), first
    assert first.endswith("pytest: 2 passed, 1 skipped, 1 failed"), (
        first, outputs)


def test_an_undeclared_pytest_step_prints_no_counts_and_names_the_key(
        sample_repo, tmp_path):
    """The very command the declared test runs, left undeclared: its output
    carries a real pytest summary, and no number is printed from it."""
    cmd = _mixed_suite(tmp_path)
    cfg = _config_with_verify(sample_repo, tmp_path, {"tests": [{"run": cmd}]})
    outputs: list[str] = []
    doc = verify_mod.run_scope(cfg, "tests", outputs=outputs)
    assert "runner" not in doc["results"][0]
    first = verify_mod.render_summary(doc, outputs=outputs).splitlines()[0]
    assert first == f"verify --scope tests: FAIL — {_undeclared(cmd)}", first


@pytest.mark.parametrize("cmd, out", [
    ("go test ./...", "ok  \texample.com/x\t0.3s\n"),
    ("cargo test", "test result: ok. 3 passed; 0 failed\n"),
    ("go test ./...", ""),
])
def test_a_declared_non_pytest_command_withholds(cmd, out):
    """A declaration is taken at its word for WHICH steps to read, never for
    the number: no pytest summary in the output, no count."""
    first = _first_line("tests", True, (cmd, out, PYTEST))
    assert first == ("verify --scope tests: PASS — "
                     "pytest: counts not found in the captured output"), first


@pytest.mark.parametrize("cmd", [
    "make test", "uvx pytest -q", "xvfb-run -s '-screen 0 1x1x8' pytest -q",
    "sh -c 'pytest -q' <<< ''", "tox -e py312",
])
def test_the_command_text_is_never_read(cmd):
    """Every wrapper a shell parser would fail on reads
    the same way: declared, counted; undeclared, the sentence."""
    out = "5 passed, 1 skipped in 1.0s\n"
    assert _first_line("t", True, (cmd, out, PYTEST)).endswith(
        "pytest: 5 passed, 1 skipped, 0 failed")
    assert _first_line("t", True, (cmd, out)) == (
        f"verify --scope t: PASS — {_undeclared(cmd)}")


def test_a_declared_step_running_two_sessions_withholds(
        sample_repo, tmp_path):
    """Round 1 (fail-closed) read counts past the 4000-char window; #362
    withheld a step that COULD run two sessions by parsing it.
    Declared, a step whose output carries two summaries withholds and says
    so; one that overflows the window still reports."""
    cmd = _mixed_suite(tmp_path, params=300)
    cfg = _config_with_verify(sample_repo, tmp_path, {
        "one": [{"run": cmd, "runner": PYTEST}],
        "two": [{"run": f"{cmd}; {cmd}", "runner": PYTEST}]})
    outputs: list[str] = []
    doc = verify_mod.run_scope(cfg, "one", outputs=outputs)
    assert len(outputs[0]) > verify_mod._TAIL_CHARS, "the control: it overflows"
    first = verify_mod.render_summary(doc, outputs=outputs).splitlines()[0]
    assert first.endswith("pytest: 302 passed, 1 skipped, 1 failed"), first

    outputs = []
    doc = verify_mod.run_scope(cfg, "two", outputs=outputs)
    first = verify_mod.render_summary(doc, outputs=outputs).splitlines()[0]
    assert first.endswith(f"pytest: counts withheld — `{cmd}; {cmd}` printed 2 "
                          "pytest summaries and verify reads one per step"), first


def test_the_cli_prints_the_counts(sample_repo, tmp_path, monkeypatch,
                                          capsys):
    """`warden verify` is where the Evidence line is read, so the wiring that
    hands the outputs to the summary is pinned at the command, not only in
    the module."""
    from warden import cli
    cmd = _mixed_suite(tmp_path)
    _config_with_verify(sample_repo, tmp_path,
                        {"tests": [{"run": cmd, "runner": PYTEST}]})
    monkeypatch.chdir(tmp_path)
    assert cli.main(["verify", "--scope", "tests"]) == 1
    first = capsys.readouterr().out.splitlines()[0]
    assert first.endswith("pytest: 2 passed, 1 skipped, 1 failed"), first


def test_a_runner_other_than_pytest_is_refused(sample_repo, tmp_path):
    with pytest.raises(config_mod.ConfigError):
        _config_with_verify(sample_repo, tmp_path,
                            {"t": [{"run": "go test ./...", "runner": "go"}]})


@pytest.mark.parametrize("step, recorded", [
    ({"run": "true", "runner": PYTEST}, {}),
    ({"run": "true"}, {"runner": PYTEST}),
], ids=["declared-now", "declared-then"])
def test_check_head_compares_commands_not_runners(sample_repo, tmp_path,
                                                        step, recorded):
    """#335's `--check-head` still pairs on (cmd, cwd): declaring a runner
    changes what the summary prints, not which artifact stands for a head."""
    cfg = _config_with_verify(sample_repo, tmp_path, {"ok": [step]})
    _artifact(tmp_path, "20260927T000000000000Z", results=[
        {"cmd": "true", "cwd": ".", "exit_code": 0, "duration_s": 0.1,
         **recorded}])
    accepted, why = verify_mod.check_head(cfg, "ok", _SHA)
    assert accepted is not None, why


@pytest.mark.parametrize("path", ["repo.yaml", "examples/hello-svc/repo.yaml"])
def test_the_shipped_pytest_steps_declare_their_runner(path):
    raw = yaml.safe_load((Path(__file__).parent.parent / path).read_text())
    steps = [s for scope in raw["verify"].values() for s in scope
             if "pytest" in s["run"]]
    assert steps and all(s.get("runner") == PYTEST for s in steps), steps


def test_a_document_with_no_outputs_prints_no_counts():
    """The artifact's tail is never read for counts: it is the window round 1
    showed can print a part as the whole."""
    doc = {"scope": "tests", "passed": True, "dirty": False, "results": [
        {"cmd": "uv run pytest -q", "cwd": ".", "exit_code": 0, "runner": PYTEST,
         "duration_s": 1.0, "output_tail": "3 passed in 1.0s\n"}]}
    assert verify_mod.render_summary(doc).splitlines()[0] == (
        "verify --scope tests: PASS")


@pytest.mark.parametrize("line, counts", [
    ("1234 passed, 5 skipped in 150.20s (0:02:30)",
     {"passed": 1234, "skipped": 5}),
    ("===== 1 failed, 4 passed, 2 warnings in 0.31s =====",
     {"failed": 1, "passed": 4, "warnings": 2}),
    ("3 passed, 1 error in 1.00s", {"passed": 3, "error": 1}),
    ("no tests ran in 0.01s", {}),
    # round 1 (pattern-fit): pytest 9's `N subtests passed` group made the
    # whole line unreadable. Only PASSED subtests get that group; a failed or
    # skipped subtest is counted under pytest's own `failed`/`skipped`, and
    # those print as pytest printed them. Real 9.1.1 output, one failing
    # test with one failing subtest:
    ("2 passed, 3 subtests passed in 0.01s", {"passed": 2}),
    ("2 failed, 2 passed, 1 skipped, 3 subtests passed in 0.02s",
     {"failed": 2, "passed": 2, "skipped": 1}),
])
def test_the_pytest_summary_line_shapes_parse(line, counts):
    assert verify_mod.pytest_counts(f"....F.s\n{line}\n") == counts


@pytest.mark.parametrize("summary, printed", [
    ("3 passed, 1 error in 1.00s", "1 error"),
    ("3 passed, 2 errors in 1.00s", "2 errors"),
])
def test_errors_are_printed_and_never_folded_into_passed(summary, printed):
    """Round 1 (tests-bite): pytest words one error `1 error` and more
    `errors`, and only the plural was pinned on the summary line."""
    first = _first_line("tests", False,
                        ("uv run pytest -q", f"E\n{summary}\n", PYTEST))
    assert first.endswith(f"pytest: 3 passed, 0 skipped, 0 failed, {printed}"), first


def test_counts_sum_across_the_pytest_steps_of_one_scope():
    first = _first_line("tests", True,
                        ("uv run ruff check .", "All checks passed!\n"),
                        ("uv run pytest -q a", "10 passed, 1 skipped in 1.0s\n",
                         PYTEST),
                        ("python -m pytest -q b", "5 passed in 1.0s\n", PYTEST))
    assert first == ("verify --scope tests: PASS — "
                     "pytest: 15 passed, 1 skipped, 0 failed"), first


def test_a_scope_with_no_summary_and_no_declared_step_prints_nothing():
    first = _first_line("lint", True, ("uv run ruff check .", "All checks passed!\n"),
                        ("go vet ./...", ""))
    assert first == "verify --scope lint: PASS"


@pytest.mark.parametrize("wrapped, out", [
    ("make test", "5 passed, 1 failed in 1.0s\n"),
    ("uvx pytest -q b", "5 passed, 1 failed in 1.0s\n"),
    # round 2 (tests-bite): the colour strip on an undeclared step's output
    ("make test", "\x1b[31m\x1b[1m1 failed\x1b[0m, \x1b[32m5 passed\x1b[0m"
                  "\x1b[31m in 1.0s\x1b[0m\n"),
], ids=["make", "uvx", "make-coloured"])
def test_an_undeclared_step_beside_a_declared_one_withholds_the_total(
        wrapped, out):
    """Round 1 (fail-closed): a step left out of the sum printed `0 failed`
    for a FAIL, and 5 passes short."""
    first = _first_line("tests", False,
                        ("uv run pytest -q a", "10 passed in 1.0s\n", PYTEST),
                        (wrapped, out))
    assert first == f"verify --scope tests: FAIL — {_undeclared(wrapped)}", first


def test_a_coloured_summary_line_still_parses():
    """Round 1 (pattern-fit): under FORCE_COLOR, PY_COLORS or --color=yes
    pytest 9.1.1 wraps its summary in SGR codes, and the pattern missed it."""
    line = ("\x1b[31m\x1b[31m\x1b[1m1 failed\x1b[0m, \x1b[32m1 passed\x1b[0m"
            "\x1b[31m in 0.06s\x1b[0m\x1b[0m")
    assert verify_mod.pytest_counts(f".F\n{line}\n") == {"failed": 1, "passed": 1}


def test_unreadable_counts_are_said_never_guessed():
    first = _first_line("tests", False, ("uv run pytest -q",
                                         "ImportError while loading conftest\n",
                                         PYTEST))
    assert first == ("verify --scope tests: FAIL — "
                     "pytest: counts not found in the captured output"), first


def test_one_unreadable_step_withholds_the_scope_total():
    """A partial sum would print a smaller number as if it were the whole."""
    first = _first_line("tests", False,
                        ("pytest a", "10 passed in 1.0s\n", PYTEST),
                        ("pytest b", "Segmentation fault\n", PYTEST))
    assert first.endswith("pytest: counts not found in the captured output"), first
