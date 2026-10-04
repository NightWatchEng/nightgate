"""The proportionate tier: what earns the light round, and what cannot buy it.

Each case builds a throwaway git repo, applies one kind of edit, and asserts
the tier. The cases that matter most are the refusals — a tier that admits
everything is not a tier, and the whole design rests on a builder being unable
to reach the light round by asserting they deserve it.

`## Smuggling` below is the adversarial half: each of those cases is a diff
constructed to carry a behaviour change into the light tier, and each asserts
the refusal and names what the diff was trying to do.
"""
import json
import subprocess
import textwrap

from pathlib import Path

import pytest

from warden import attest as attest_mod
from warden import proportion

ROOT = Path(__file__).resolve().parent.parent

BASE = textwrap.dedent('''\
    """Module docstring."""


    def add(a, b):
        """Return the sum."""
        # a comment
        return a + b
    ''')


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True)


def _repo(tmp_path, files=None):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.fileMode", "true")
    for rel, data in (files or {"pkg/mod.py": BASE.encode()}).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(data)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "tag", "base")
    return repo


def _commit(repo, files, message="change"):
    for rel, data in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if data is None:
            _git(repo, "rm", "-q", "--", rel)
        else:
            path.write_bytes(data)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def _tier(repo):
    return proportion.classify(str(repo), "base", "HEAD")


# --------------------------------------------------------------------------
# What the proof admits
# --------------------------------------------------------------------------

def test_a_comment_and_docstring_only_python_change_is_light(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, {"pkg/mod.py": BASE
                   .replace("Module docstring.", "What this module is for.")
                   .replace("# a comment", "# a much better comment")
                   .replace("Return the sum.", "Return a plus b.")
                   .encode()})
    verdict = _tier(repo)
    assert verdict.light, verdict.reason
    assert verdict.files[0].surface == "python"


def test_size_is_not_the_test_a_large_comment_diff_is_still_light(tmp_path):
    """The proof is about what the code does, not how many lines moved. This
    repo merged a 929-line comment-only diff that was provably inert."""
    repo = _repo(tmp_path)
    fat = BASE.replace("# a comment",
                       "\n".join(f"    # line {i} of a long explanation"
                                 for i in range(400)))
    _commit(repo, {"pkg/mod.py": fat.encode()})
    assert _tier(repo).light




def test_a_range_is_judged_whole_not_commit_by_commit(tmp_path):
    """A code change and its revert inside one range nets to inert, and that
    is the right answer: the tier is a property of base..head."""
    repo = _repo(tmp_path)
    _commit(repo, {"pkg/mod.py": BASE.replace("a + b", "a - b").encode()})
    _commit(repo, {"pkg/mod.py": BASE.replace("# a comment", "# x").encode()})
    assert _tier(repo).light


# --------------------------------------------------------------------------
# What the proof refuses
# --------------------------------------------------------------------------

def test_a_code_change_is_full(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, {"pkg/mod.py": BASE.replace("a + b", "a - b").encode()})
    verdict = _tier(repo)
    assert not verdict.light
    assert "executable code changed" in verdict.blockers[0].reason


@pytest.mark.parametrize("path", [
    "warden/attest.py", "cage/run.py", ".warden/rules/r.py",
    ".github/wf.py", ".githooks/h.py", ".cage/c.py", "scripts/s.py",
    "skills/nightgate-skills/skills/deliver/SKILL.md",
])
def test_the_gates_own_machinery_is_never_light_however_identical(
        tmp_path, path):
    """The founder's floor, and the reason for it: the cost of being wrong
    about the thing that decides what gets reviewed is unbounded."""
    body = BASE if path.endswith(".py") else "# Skill\n\noriginal\n"
    repo = _repo(tmp_path, {path: body.encode()})
    changed = (BASE.replace("# a comment", "# reworded").encode()
               if path.endswith(".py") else b"# Skill\n\nreworded\n")
    _commit(repo, {path: changed})
    verdict = _tier(repo)
    assert not verdict.light
    assert verdict.blockers[0].surface == "machinery"


@pytest.mark.parametrize("name", ["repo.yaml", "graph.yaml", "pyproject.toml",
                                  "uv.lock"])
def test_the_config_that_drives_the_gate_is_never_light(tmp_path, name):
    repo = _repo(tmp_path, {name: b"original: 1\n"})
    _commit(repo, {name: b"original: 2\n"})
    assert not _tier(repo).light


def test_an_unknown_surface_is_full_because_no_proof_covers_it(tmp_path):
    repo = _repo(tmp_path, {"app/config.yml": b"a: 1\n"})
    _commit(repo, {"app/config.yml": b"a: 2\n"})
    verdict = _tier(repo)
    assert not verdict.light
    assert verdict.blockers[0].surface == "unknown"



def test_an_added_python_file_is_full(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, {"pkg/new.py": b"X = 1\n"})
    assert not _tier(repo).light


def test_an_empty_range_proves_nothing_and_is_full(tmp_path):
    """A comparison over zero files reporting success is a hole this repo has
    already paid for once."""
    repo = _repo(tmp_path)
    verdict = proportion.classify(str(repo), "base", "base")
    assert not verdict.light
    assert "proves nothing" in verdict.reason


def test_a_range_of_only_round_evidence_is_full(tmp_path):
    """The shard a round leaves is set aside so the tier is reachable at all —
    but a range holding nothing else has nothing to compare."""
    repo = _repo(tmp_path)
    _commit(repo, {".warden/memory/attest/s.json": b'{"sha": "x"}\n'})
    verdict = _tier(repo)
    assert not verdict.light
    assert "proves nothing" in verdict.reason


def test_an_unparseable_python_file_is_full(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, {"pkg/mod.py": b"def broken( :\n"})
    verdict = _tier(repo)
    assert not verdict.light
    assert "could not decide" in verdict.reason


def test_a_file_that_is_not_utf8_is_full(tmp_path):
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode()})
    _commit(repo, {"pkg/mod.py": b"\xff\xfe\x00bad bytes"})
    assert not _tier(repo).light


def test_a_mode_change_alone_is_full(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "update-index", "--chmod=+x", "pkg/mod.py")
    _git(repo, "commit", "-qm", "chmod")
    verdict = _tier(repo)
    assert not verdict.light
    assert "mode" in verdict.blockers[0].reason


def test_a_changed_coding_declaration_is_full(tmp_path):
    """Identical trees would not mean identical string values."""
    src = b"# -*- coding: utf-8 -*-\nX = '\\xe9'\n"
    repo = _repo(tmp_path, {"pkg/mod.py": src})
    _commit(repo, {"pkg/mod.py": b"# -*- coding: latin-1 -*-\nX = '\\xe9'\n"})
    assert not _tier(repo).light


# --------------------------------------------------------------------------
# Smuggling: diffs built to carry a behaviour change into the light tier
# --------------------------------------------------------------------------

def test_smuggling_a_lint_suppression_is_refused(tmp_path):
    """The attack: the AST cannot see `# noqa`, so adding one is invisible to
    a tree comparison while silencing a real finding in the gate's own lint.
    An identical tree with different instructions to the linter is a gate
    change wearing a comment's clothes."""
    src = "import os  # a comment\nX = os\n"
    repo = _repo(tmp_path, {"pkg/mod.py": src.encode()})
    _commit(repo, {"pkg/mod.py":
                   "import os  # noqa: F401\nX = os\n".encode()})
    verdict = _tier(repo)
    assert not verdict.light
    assert "tool-directive" in verdict.blockers[0].reason


def test_smuggling_a_removed_coverage_pragma_is_refused(tmp_path):
    src = "def f():\n    return 1  # pragma: no cover\n"
    repo = _repo(tmp_path, {"pkg/mod.py": src.encode()})
    _commit(repo, {"pkg/mod.py": b"def f():\n    return 1  # plain comment\n"})
    assert not _tier(repo).light


def test_smuggling_code_inside_a_string_literal_is_refused(tmp_path):
    """A string is a Constant node, so editing a template, a SQL statement or
    anything destined for exec changes the tree and is seen."""
    src = 'TEMPLATE = "SELECT 1"\n'
    repo = _repo(tmp_path, {"pkg/mod.py": src.encode()})
    _commit(repo, {"pkg/mod.py": b'TEMPLATE = "DROP TABLE t"\n'})
    verdict = _tier(repo)
    assert not verdict.light
    assert "executable code changed" in verdict.blockers[0].reason


def test_smuggling_a_doctest_into_a_docstring_is_refused(tmp_path):
    """A docstring carrying an interactive prompt is executable the moment a
    doctest runner is switched on."""
    repo = _repo(tmp_path)
    _commit(repo, {"pkg/mod.py": BASE.replace(
        "Return the sum.", "Return the sum.\n\n    >>> add(1, 2)\n    3\n"
    ).encode()})
    verdict = _tier(repo)
    assert not verdict.light
    assert "doctest" in verdict.blockers[0].reason


def test_smuggling_a_rename_is_refused(tmp_path):
    """Renames are not detected, so a rename reads as the delete and the add
    it is, and a moved file cannot arrive as a comment change."""
    repo = _repo(tmp_path)
    _commit(repo, {"pkg/mod.py": None, "pkg/renamed.py": BASE.encode()})
    assert not _tier(repo).light


def test_smuggling_one_real_change_among_many_inert_ones_is_refused(tmp_path):
    """Eligibility is a union, not a majority: one unproven file pulls the
    whole range back to the full round."""
    files = {f"pkg/m{i}.py": BASE.encode() for i in range(10)}
    repo = _repo(tmp_path, files)
    changed = {p: BASE.replace("# a comment", f"# reworded {p}").encode()
               for p in files}
    changed["pkg/m7.py"] = BASE.replace("a + b", "a * b").encode()
    _commit(repo, changed)
    verdict = _tier(repo)
    assert not verdict.light
    assert [b.path for b in verdict.blockers] == ["pkg/m7.py"]


def test_the_classifier_cannot_widen_its_own_tier(tmp_path):
    """The classifier lives under warden/, so a change to the thing that
    decides eligibility is never itself eligible."""
    repo = _repo(tmp_path, {"warden/proportion.py": BASE.encode()})
    _commit(repo, {"warden/proportion.py":
                   BASE.replace("# a comment", "# reworded").encode()})
    assert not _tier(repo).light


def test_policy_can_only_add_to_the_floor_never_remove_from_it(tmp_path):
    """The floor is a constant in code, and every lever on it points ONE way.
    `extra_floor` is the caller's, `repo.yaml`'s `proportion.never_light` is
    the repo's, and neither subtracts — so a repo cannot widen its own light
    tier by editing the config that the floor already covers."""
    repo = _repo(tmp_path, {"vendor/lib.py": BASE.encode()})
    _commit(repo, {"vendor/lib.py":
                   BASE.replace("# a comment", "# reworded").encode()})
    assert _tier(repo).light
    narrowed = proportion.classify(str(repo), "base", "HEAD",
                                   extra_floor=("vendor/",))
    assert not narrowed.light
    for entry in proportion.NEVER_LIGHT_FLOOR:
        assert proportion.under_floor(f"{entry.rstrip('/')}/x.py"
                                      if entry.endswith("/") else entry)


def test_a_file_merely_named_like_the_evidence_dir_is_not_carved_out(
        tmp_path):
    """The carve-out matches a directory boundary, not a prefix of a name."""
    assert proportion.is_round_evidence(".warden/memory/attest/s.json", "A")
    assert not proportion.is_round_evidence(
        ".warden/memory/attest-notes.md", "A")


# --------------------------------------------------------------------------
# The roster half: a light crew must be earned, never asserted
# --------------------------------------------------------------------------

CREW = {"cap": 2,
        "rounds": {1: ["code-reviewer", "cross-examiner"],
                   2: ["scoped-re-reviewer"]},
        "closure": None,
        "light": {"roles": ["claims-auditor"], "payload": "claims-only"}}
LIGHT_ROSTER = [{"role": "claims-auditor", "round": 1}]


def test_a_light_roster_is_accepted_when_the_range_is_proven_inert():
    attest_mod.verify_crew(LIGHT_ROSTER, CREW, 1,
                           light_proof=attest_mod.LightProof(True, "inert"))


def test_a_light_roster_is_refused_when_the_range_is_not_inert():
    with pytest.raises(attest_mod.AttestError) as exc:
        attest_mod.verify_crew(
            LIGHT_ROSTER, CREW, 1,
            light_proof=attest_mod.LightProof(False, "pkg/m.py changed"))
    assert "not the builder's to assert" in str(exc.value)
    assert "pkg/m.py changed" in str(exc.value)


def test_a_light_roster_with_no_proof_at_all_is_refused():
    """Missing evidence is a refusal, not a default. A tier that admits itself
    when its proof could not be computed is not a tier."""
    with pytest.raises(attest_mod.AttestError) as exc:
        attest_mod.verify_crew(LIGHT_ROSTER, CREW, 1, light_proof=None)
    assert "could not recompute" in str(exc.value)


def test_a_light_roster_past_the_cap_is_refused():
    """The light round is a first round, never one bought after the budget."""
    with pytest.raises(attest_mod.AttestError) as exc:
        attest_mod.verify_crew(
            LIGHT_ROSTER, CREW, 3,
            light_proof=attest_mod.LightProof(True, "inert"))
    assert "past the cap" in str(exc.value)


def test_a_roster_mixing_the_light_crew_with_a_full_round_is_not_light():
    """It falls through to the ordinary per-round comparison and is refused
    there, so the light crew cannot be used to dilute a real round."""
    mixed = [{"role": "claims-auditor", "round": 1},
             {"role": "code-reviewer", "round": 1}]
    with pytest.raises(attest_mod.AttestError) as exc:
        attest_mod.verify_crew(mixed, CREW, 1,
                               light_proof=attest_mod.LightProof(True, "x"))
    assert "not the crew graph.yaml declares" in str(exc.value)


def test_a_repo_declaring_no_light_round_is_unaffected():
    """The absent block is the behaviour every repo had before the key, and
    the light roster is then just an undeclared role."""
    without = {**CREW, "light": None}
    with pytest.raises(attest_mod.AttestError) as exc:
        attest_mod.verify_crew(LIGHT_ROSTER, without, 1)
    assert "not the crew graph.yaml declares" in str(exc.value)


def test_the_full_crew_still_verifies_exactly_as_before():
    attest_mod.verify_crew(
        [{"role": "code-reviewer", "round": 1},
         {"role": "cross-examiner", "round": 1}], CREW, 1)


# --------------------------------------------------------------------------
# Regressions from round 1. Each name says which refusal it pins.
# --------------------------------------------------------------------------

def test_a_rewritten_attestation_shard_is_content_not_round_evidence(tmp_path):
    """Round 1: the carve-out copied the coverage rule's PATH boundary and
    dropped its append-only half, so a light diff could rewrite the review
    corpus it was being judged against."""
    shard = ".warden/memory/attest/20260101T000000Z-aaaa-bbbb.json"
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            shard: b'{"sha": "x", "verdict": "findings-open"}'})
    _commit(repo, {"pkg/mod.py": BASE.replace("# a comment", "# x").encode(),
                   shard: b'{"sha": "x", "verdict": "clean"}'})
    verdict = _tier(repo)
    assert not verdict.light
    assert shard in [b.path for b in verdict.blockers]


def test_a_deleted_attestation_shard_is_content_not_round_evidence(tmp_path):
    shard = ".warden/memory/attest/20260101T000000Z-aaaa-bbbb.json"
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            shard: b'{"sha": "x"}'})
    _commit(repo, {"pkg/mod.py": BASE.replace("# a comment", "# x").encode(),
                   shard: None})
    assert not _tier(repo).light


def test_a_new_attestation_shard_is_still_round_evidence(tmp_path):
    """The carve-out must still admit the shard the protocol forces every
    change to commit, or the tier is unreachable by construction."""
    repo = _repo(tmp_path)
    _commit(repo, {"pkg/mod.py": BASE.replace("# a comment", "# x").encode(),
                   ".warden/memory/attest/new.json": b'{"sha": "y"}'})
    assert _tier(repo).light


def test_the_floor_matches_a_nested_checkout_not_only_the_repo_root(tmp_path):
    """Round 1: a consumer's own gate machinery sits below the root, so a
    root-anchored floor protected this repo's and left theirs eligible."""
    nested = "examples/hello-svc/.warden/checkers/handler_contract.py"
    repo = _repo(tmp_path, {nested: BASE.encode()})
    _commit(repo, {nested: BASE.replace("# a comment", "# reworded").encode()})
    verdict = _tier(repo)
    assert not verdict.light
    assert verdict.blockers[0].surface == "machinery"


def test_a_path_merely_starting_with_a_floor_name_is_not_the_floor(tmp_path):
    assert proportion.under_floor("wardenx/mod.py") is None
    assert proportion.under_floor(".warden-old/x.py") is None
    assert proportion.under_floor("warden/attest.py") == "warden/"


def test_a_relocated_suppression_comment_is_refused(tmp_path):
    """Round 1: the directive list was position-free, so carrying `# noqa`
    from one import to another left the AST and the sorted list identical
    while changing which finding is silenced."""
    before = "import os  # noqa: F401\nimport sys\n\nX = 1\n"
    after = "import os\nimport sys  # noqa: F401\n\nX = 1\n"
    repo = _repo(tmp_path, {"pkg/mod.py": before.encode()})
    _commit(repo, {"pkg/mod.py": after.encode()})
    verdict = _tier(repo)
    assert not verdict.light
    assert "moved" in verdict.blockers[0].reason


def test_reflowing_whitespace_around_a_suppression_is_not_a_move(tmp_path):
    """The pair is compared with whitespace removed, so the position rule does
    not turn every reformat into a refusal."""
    repo = _repo(tmp_path, {"pkg/mod.py": b"X = [1,2]  # noqa: E501\n"})
    _commit(repo, {"pkg/mod.py": b"X = [1, 2]  # noqa: E501\n"})
    assert _tier(repo).light


def test_a_retargeted_symlink_named_py_is_refused(tmp_path):
    """Round 1: `git show` on a symlink returns its TARGET, which was parsed
    as Python — and two different targets can spell the same expression."""
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode()})
    (repo / "link.py").symlink_to("pkg/real.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "link")
    _git(repo, "tag", "-f", "base")
    (repo / "link.py").unlink()
    (repo / "link.py").symlink_to("pkg /real.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "retarget")
    verdict = _tier(repo)
    assert not verdict.light
    assert "symlink" in verdict.blockers[0].reason


def test_a_pep484_type_comment_is_a_tool_directive(tmp_path):
    """The directive vocabulary claims type checkers; it must cover the type
    comment, not only `# type: ignore`."""
    repo = _repo(tmp_path, {"pkg/mod.py": b"X = []  # type: List[int]\n"})
    _commit(repo, {"pkg/mod.py": b"X = []  # type: List[str]\n"})
    assert not _tier(repo).light


# --- the light round is still a round warden minted ------------------------

def test_a_light_roster_in_an_unminted_round_is_refused():
    """Round 1: the light path returned before the minted-number check, so a
    directory `warden round new` never made was accepted."""
    with pytest.raises(attest_mod.AttestError) as exc:
        attest_mod.verify_crew(LIGHT_ROSTER, CREW, None,
                               light_proof=attest_mod.LightProof(True, "x"))
    assert "no minted round number" in str(exc.value)


def test_a_light_roster_in_round_two_is_refused():
    """Within the cap, so the old past-the-cap test did not reach it: a light
    crew minted behind a real round is a cheap round riding on one."""
    roster = [{"role": "claims-auditor", "round": 2}]
    with pytest.raises(attest_mod.AttestError) as exc:
        attest_mod.verify_crew(roster, CREW, 2,
                               light_proof=attest_mod.LightProof(True, "x"))
    assert "the light round is round 1" in str(exc.value)


def test_a_light_roster_labelled_with_another_round_is_refused():
    roster = [{"role": "claims-auditor", "round": 1},
              {"role": "claims-auditor", "round": 99}]
    with pytest.raises(attest_mod.AttestError) as exc:
        attest_mod.verify_crew(roster, CREW, 1,
                               light_proof=attest_mod.LightProof(True, "x"))
    assert "labels its entries with round(s)" in str(exc.value)


# --------------------------------------------------------------------------
# Regressions from round 2, including the repair's own defects.
# --------------------------------------------------------------------------

def test_a_moved_standalone_coverage_pragma_is_refused(tmp_path):
    """Round 2: the pairing anchored a directive to the code on its LEFT, and
    a directive on its own line has none — so `# pragma: no cover` moved from
    one function to another silenced coverage elsewhere under an identical
    AST."""
    before = ("def f():\n    # pragma: no cover\n    return 1\n\n\n"
              "def g():\n    return 2\n")
    after = ("def f():\n    return 1\n\n\n"
             "def g():\n    # pragma: no cover\n    return 2\n")
    repo = _repo(tmp_path, {"pkg/mod.py": before.encode()})
    _commit(repo, {"pkg/mod.py": after.encode()})
    verdict = _tier(repo)
    assert not verdict.light
    assert "moved" in verdict.blockers[0].reason


def test_a_moved_standalone_fmt_region_is_refused(tmp_path):
    before = "# fmt: off\nX = 1\nY = 2\n"
    after = "X = 1\n# fmt: off\nY = 2\n"
    repo = _repo(tmp_path, {"pkg/mod.py": before.encode()})
    _commit(repo, {"pkg/mod.py": after.encode()})
    assert not _tier(repo).light


def test_ordinary_prose_is_not_read_as_a_tool_directive(tmp_path):
    """Round 2: the widened vocabulary matched bare words, so a comment
    saying `pragmatic` was refused with a message claiming it was a tool
    directive — untrue, and it denies the tier for no reason."""
    for comment in ("# pragmatic choice", "# Vulture hunting notes",
                    "# codespell is nice", "# TY: thanks"):
        assert not proportion._directive(comment), comment
    for comment in ("# noqa: F401", "# pragma: no cover", "# fmt: off",
                    "# type: ignore", "# nosec", "# trunk-ignore(ruff)"):
        assert proportion._directive(comment), comment


def test_a_comment_edit_naming_a_tool_in_prose_is_still_light(tmp_path):
    repo = _repo(tmp_path, {"pkg/mod.py": b"X = 1  # a pragmatic choice\n"})
    _commit(repo, {"pkg/mod.py": b"X = 1  # a pragmatic decision\n"})
    assert _tier(repo).light


@pytest.mark.parametrize("label", [1.0, True, "1"])
def test_a_light_roster_label_that_is_not_an_integer_is_refused(label):
    """Round 2: the light path read `round` with a raw `.get`, so 2.0 and True
    — both of which JSON Schema's `type: integer` admits — were accepted where
    the numbered rounds refuse them."""
    roster = [{"role": "claims-auditor", "round": label}]
    with pytest.raises(attest_mod.AttestError) as exc:
        attest_mod.verify_crew(roster, CREW, 1,
                               light_proof=attest_mod.LightProof(True, "x"))
    assert "not an integer as warden reads one" in str(exc.value)


def test_a_light_roster_entry_with_no_round_is_refused_by_name():
    """Round 2: a roster where one entry carried `round` and another did not
    put None in a sorted set and raised TypeError — a stack trace where a
    named refusal belongs. `round` is optional in the schema, so this is
    reachable from a schema-valid attestation."""
    roster = [{"role": "claims-auditor", "round": 1},
              {"role": "claims-auditor"}]
    with pytest.raises(attest_mod.AttestError) as exc:
        attest_mod.verify_crew(roster, CREW, 1,
                               light_proof=attest_mod.LightProof(True, "x"))
    assert "states no `round`" in str(exc.value)


def test_the_light_crew_must_be_an_independent_context(tmp_path):
    """Round 1 finding 12, which shipped untested: declaring the builder as
    the light crew made the builder sole reviewer of their own change, and the
    light round is the only round that change gets."""
    from warden import graph as graph_mod
    nodes = {"builder": {"kind": "skill", "authority": "find"},
             "claims-auditor": {"kind": "subagent", "authority": "find"}}
    rounds = {1: ["code-reviewer"]}
    with pytest.raises(graph_mod.GraphError) as exc:
        graph_mod._resolve_light(
            {"light": {"roles": ["builder"], "payload": "claims-only"}},
            nodes, [], rounds, 2)
    assert "dispatches a subagent" in str(exc.value)
    assert graph_mod._resolve_light(
        {"light": {"roles": ["claims-auditor"], "payload": "claims-only"}},
        nodes, [], rounds, 2) == {"roles": ["claims-auditor"],
                                  "payload": "claims-only"}


def test_a_shard_whose_name_holds_whitespace_is_still_seen(tmp_path):
    """Round 1 finding 1, which shipped untested: the enforcement listed
    shards with `ls-tree --name-only` and split on whitespace, so `attest
    check` read a shard the binding step could not see. Renaming a shard was
    enough to carry a light attestation over a range that never earned it."""
    from warden import cli as cli_mod
    shard = '.warden/memory/attest/2026 01 01-light.json'
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode()})
    _commit(repo, {"pkg/mod.py": BASE.replace("# a comment", "# x").encode()})
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True,
                          check=True).stdout.strip()
    _commit(repo, {shard: json.dumps(
        {"sha": head, "reviewers": [{"role": "claims-auditor"}]}
    ).encode()})
    found = cli_mod._light_shards_in_range(repo, "base", "HEAD",
                                           {"claims-auditor"})
    assert [p for p, _ in found] == [shard]


def test_an_enrollment_below_the_git_root_reads_its_own_shards(tmp_path):
    """init ships the binding step to a gate whose steps run in `svc/api`;
    its shards must read from there, or the step exits 2."""
    from warden import cli as cli_mod
    shard = "svc/api/.warden/memory/attest/a.json"
    repo = _repo(tmp_path, {"svc/api/pkg/mod.py": BASE.encode()})
    _commit(repo, {"svc/api/pkg/mod.py":
                   BASE.replace("# a comment", "# x").encode()})
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True,
                          check=True).stdout.strip()
    _commit(repo, {shard: json.dumps(
        {"sha": head, "reviewers": [{"role": "claims-auditor"}]}).encode()})
    found = cli_mod._light_shards_in_range(repo / "svc" / "api", "base",
                                           "HEAD", {"claims-auditor"})
    assert [sha for _, sha in found] == [head]


def test_an_unreadable_shard_refuses_rather_than_binding_nothing(tmp_path):
    from warden import cli as cli_mod
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode()})
    _commit(repo, {".warden/memory/attest/broken.json": b"{not json"})
    with pytest.raises(attest_mod.AttestError) as exc:
        cli_mod._light_shards_in_range(repo, "base", "HEAD",
                                       {"claims-auditor"})
    assert "could not be read" in str(exc.value)


def test_a_non_shard_file_under_the_shard_dir_does_not_brick_the_step(
        tmp_path):
    """Round 2: the reader took every path under the shard dir, so a committed
    README there would refuse on every future PR with no way out. The sibling
    reader in attest.py takes `.json` only."""
    from warden import cli as cli_mod
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode()})
    _commit(repo, {".warden/memory/attest/README.md": b"# notes\n"})
    assert cli_mod._light_shards_in_range(
        repo, "base", "HEAD", {"claims-auditor"}) == []


def _head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True,
                          check=True).stdout.strip()


def test_a_light_shard_already_on_base_naming_a_range_commit_is_bound(
        tmp_path):
    """Bounding the read to the shards the range adds would let this
    through. A PR commits a light shard naming a commit that is not
    its own, so its own step never binds it; the branch holding that commit
    then merges base in, and `attest check` accepts the shard for it. The
    enforcement must still see it."""
    from warden import cli as cli_mod
    shard = ".warden/memory/attest/foreign.json"
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, {"pkg/mod.py": b"def add(a, b):\n    return a - b\n"})
    side = _head(repo)
    _git(repo, "checkout", "-q", "main")
    _commit(repo, {shard: json.dumps(
        {"sha": side, "reviewers": [{"role": "claims-auditor"}]}).encode()})
    _git(repo, "tag", "-f", "base")
    _git(repo, "checkout", "-q", "side")
    _git(repo, "merge", "-q", "--no-edit", "main")
    found = cli_mod._light_shards_in_range(repo, "base", "HEAD",
                                           {"claims-auditor"})
    assert found == [(shard, side)]


def test_reading_the_shards_costs_the_same_git_calls_at_any_corpus_size(
        tmp_path, monkeypatch):
    """The corpus is read in one batch rather than one `git show` per shard,
    which cost more on every PR that landed, so the number of git processes
    does not move with its size."""
    from warden import cli as cli_mod
    calls = []
    real = subprocess.run

    def counting(cmd, *a, **k):
        if cmd and cmd[0] == "git":
            calls.append(cmd)
        return real(cmd, *a, **k)

    def git_calls(n: int) -> int:
        repo = _repo(tmp_path / str(n))
        _commit(repo, {f".warden/memory/attest/s{i}.json": json.dumps(
            {"sha": "0" * 40, "reviewers": []}).encode() for i in range(n)})
        calls.clear()
        monkeypatch.setattr(subprocess, "run", counting)
        try:
            cli_mod._light_shards_in_range(repo, "base", "HEAD",
                                           {"claims-auditor"})
        finally:
            monkeypatch.setattr(subprocess, "run", real)
        return len(calls)

    assert git_calls(2) == git_calls(40)


@pytest.mark.parametrize("how", ["rewritten", "renamed"])
def test_a_shard_the_range_rewrites_or_renames_is_still_bound(tmp_path, how):
    """Bounding the walk to added shards must not open a door: a shard at
    `base` rewritten in place, or moved and edited, can carry a light roster
    onto this range as well as a new one can."""
    from warden import cli as cli_mod
    old = ".warden/memory/attest/old.json"
    moved = ".warden/memory/attest/moved.json"
    # Indented, with lines the move leaves alone, so git pairs it as a rename.
    doc = {"sha": "0" * 40, "reviewers": [{"role": "code-reviewer"}],
           **{f"note{i}": "unchanged by the move" for i in range(20)}}
    repo = _repo(tmp_path, {"pkg/mod.py": BASE.encode(),
                            old: json.dumps(doc, indent=2).encode()})
    _commit(repo, {"pkg/mod.py": BASE.replace("# a comment", "# x").encode()})
    light = json.dumps({**doc, "sha": _head(repo),
                        "reviewers": [{"role": "claims-auditor"}]},
                       indent=2).encode()
    target = old if how == "rewritten" else moved
    _commit(repo, {old: None, target: light} if how == "renamed"
            else {old: light})
    found = cli_mod._light_shards_in_range(repo, "base", "HEAD",
                                           {"claims-auditor"})
    assert [p for p, _ in found] == [target]


def test_the_light_crew_is_reachable_by_name(tmp_path):
    """Round 1 finding 8, which shipped untested: the crew was declarable and
    unreachable, so nothing in the protocol could ask who reviews a provably
    inert change."""
    from warden import graph as graph_mod
    crew = graph_mod.declared_crew(ROOT)
    assert crew["light"] == {"roles": ["claims-auditor"],
                             "payload": "claims-only"}


def test_the_light_round_is_not_one_of_the_numbered_rounds():
    """`--light` and `--round N` are different questions, and asking both at
    once used to answer only one of them silently."""
    import argparse
    from warden import cli as cli_mod
    parser = cli_mod.build_parser()
    args = parser.parse_args(["graph", "crew", "--light", "--round", "2"])
    assert args.light and args.round == 2
    assert isinstance(args, argparse.Namespace)
