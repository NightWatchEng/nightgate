"""The consumer opt-in: the `proportion:` block in repo.yaml.

A platform constant cannot know which directories are a given consumer's gate
machinery, so a repo names them and warden reads the list off the commits at
each end of the range. `never_light` restricts, and the cases here are about
it being read in the direction that cannot be spent on the diff that writes
it. `prose_roots`, the permissive key the block once carried, is refused, and
the last case here is that refusal.

Absence is the other half and it is asserted rather than assumed — a repo.yaml
without the block must classify exactly as it did before the block existed,
which is what lets a pinned consumer upgrade the platform without touching
config.
"""
import textwrap

import pytest

from warden import declared as declared_mod
from warden import proportion
from test_proportion import BASE, _commit, _git, _repo, _tier

ROOT_YAML = textwrap.dedent("""\
    version: 1
    repo: demo
    components:
      app: {path: pkg/}
    risk_tiers:
      - {glob: "**", tier: LOW}
    verify:
      tests:
        - {run: "true"}
    review:
      rules_dir: .warden/rules
      blocking_severities: [HIGH]
    """)


def _yaml(*never_light: str) -> bytes:
    if not never_light:
        return ROOT_YAML.encode()
    block = "proportion:\n"
    block += f"  never_light: [{', '.join(repr(e) for e in never_light)}]\n"
    return (ROOT_YAML + block).encode()


def _tree(tmp_path, yaml_bytes=None, name="repo"):
    """A repo whose only ordinary source is `vendor/lib.py`, plus a repo.yaml.

    repo.yaml is committed in the BASE, because it is on the floor: a range
    that adds or edits it is never light for that reason alone, and every case
    below is about the floor it declares rather than about touching it.
    """
    files = {"vendor/lib.py": BASE.encode()}
    if yaml_bytes is not None:
        files["repo.yaml"] = yaml_bytes
    return _repo(tmp_path / name, files)


def _reword(repo, path="vendor/lib.py"):
    _commit(repo, {path: BASE.replace("# a comment", "# reworded").encode()})


# --------------------------------------------------------------------------
# Absence changes nothing
# --------------------------------------------------------------------------

def test_a_repo_with_no_repo_yaml_at_all_classifies_as_before(tmp_path):
    """A rev that genuinely has no repo.yaml declares nothing, and the reader
    must say so without reading every failure the same way.

    `_declared_at` LISTS with `git ls-tree` first: an empty listing at exit 0
    is absence, and any other failure is `Undecidable`. That split is what
    keeps this case from turning every repo.yaml-less consumer's tier to full
    while still refusing a rev it cannot read — the cell twelve down drives
    the refusing half."""
    repo = _tree(tmp_path)
    _reword(repo)
    assert _tier(repo).light


def test_a_repo_yaml_without_the_key_classifies_as_before(tmp_path):
    repo = _tree(tmp_path, _yaml())
    _reword(repo)
    assert _tier(repo).light
    assert proportion.declared_floor(str(repo), "base", "HEAD") == ()


# --------------------------------------------------------------------------
# The key adds, and only adds
# --------------------------------------------------------------------------

def test_a_declared_path_is_refused_the_light_round(tmp_path):
    repo = _tree(tmp_path, _yaml("vendor/"))
    _reword(repo)
    verdict = _tier(repo)
    assert not verdict.light
    assert verdict.blockers[0].surface == "machinery"
    assert "vendor/" in verdict.blockers[0].reason


def test_the_platform_floor_survives_a_repo_that_declares_nothing_of_it(
        tmp_path):
    """The constant is not replaced by the declaration, and no spelling of the
    key subtracts from it. A repo naming one directory keeps every entry the
    platform already refused."""
    repo = _repo(tmp_path / "keeps", {"warden/mod.py": BASE.encode(),
                                      "repo.yaml": _yaml("vendor/")})
    _commit(repo, {"warden/mod.py":
                   BASE.replace("# a comment", "# reworded").encode()})
    assert not _tier(repo).light


def test_a_declaration_binds_from_the_range_that_introduces_it(tmp_path):
    """Read from BOTH ends and unioned, so an entry takes effect in the range
    that writes it rather than one range later."""
    repo = _tree(tmp_path, _yaml())
    _commit(repo, {"repo.yaml": _yaml("vendor/"),
                   "vendor/lib.py": BASE.replace("# a comment",
                                                 "# reworded").encode()})
    assert proportion.declared_floor(str(repo), "base", "HEAD") == ("vendor/",)


def test_a_diff_cannot_widen_its_own_tier_by_deleting_an_entry(tmp_path):
    """The self-certification shape, wearing a config key. Deleting the entry
    at head must not un-refuse the file the entry covered — the union of the
    two ends is what makes that a property of the reading rather than a
    coincidence of what the platform floor happens to hold today."""
    repo = _tree(tmp_path, _yaml("vendor/"))
    _commit(repo, {"repo.yaml": _yaml(),
                   "vendor/lib.py": BASE.replace("# a comment",
                                                 "# reworded").encode()})
    assert proportion.declared_floor(str(repo), "base", "HEAD") == ("vendor/",)
    verdict = _tier(repo)
    assert not verdict.light
    assert [b.path for b in verdict.blockers] == ["repo.yaml", "vendor/lib.py"]


def test_a_nested_consumers_declaration_is_read_from_its_own_repo_yaml(
        tmp_path):
    """Scoped to the repo being classified. A nested `examples/x/repo.yaml` is
    a different repo's policy and this reader never opens it — the entry that
    binds here is the one in the root file, which is why the entries match at
    any depth instead."""
    repo = _tree(tmp_path, _yaml("vendor/"))
    (repo / "examples" / "c").mkdir(parents=True)
    (repo / "examples" / "c" / "repo.yaml").write_bytes(_yaml("pkg/"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "nested")
    _git(repo, "tag", "-f", "base")
    _reword(repo)
    assert proportion.declared_floor(str(repo), "base", "HEAD") == ("vendor/",)


# --------------------------------------------------------------------------
# A floor that cannot be read is not an empty floor
# --------------------------------------------------------------------------

@pytest.mark.parametrize("shape,raw", [
    ("unparseable", b"version: 1\nproportion: [\n"),
    ("not-a-mapping", b"- version: 1\n"),
    ("key-is-not-a-block", b"version: 1\nproportion: never\n"),
    ("entries-are-not-a-list", b"version: 1\nproportion:\n  never_light: all\n"),
    ("an-entry-is-not-a-path", b"version: 1\nproportion:\n  never_light: [7]\n"),
    ("an-entry-is-empty", b'version: 1\nproportion:\n  never_light: [""]\n'),
])
def test_a_repo_yaml_the_floor_cannot_be_read_from_is_full(tmp_path, shape,
                                                           raw):
    """Fail closed, the same way every other unknown in this module does. A
    declaration nobody can parse must not read as "this repo declares
    nothing", because that is the permissive answer and it arrives silently.

    These shapes are refused by the CLASSIFIER, not only by the schema: a
    committed repo.yaml at the merge base was validated by whichever platform
    release wrote it, and a range is classified without anyone re-validating
    either end.
    """
    repo = _tree(tmp_path, raw, name=shape)
    _reword(repo)
    verdict = _tier(repo)
    assert not verdict.light
    assert "could not decide" in verdict.reason, verdict.reason
    # And the refusal must SAY which file it could not read. `classify`'s
    # blanket catch turns any exception into the full tier, so a shape that
    # merely crashed would look identical here — naming repo.yaml is what
    # separates a refusal this module wrote from one it fell into.
    assert "repo.yaml" in verdict.reason, verdict.reason


def test_an_absent_repo_yaml_is_proven_absent_not_inferred_from_a_failure(
        tmp_path):
    """`git show` exits non-zero for a path that is not in the rev and equally
    for a bad object, an unreadable object store or an ambiguous argument.
    Reading every one of those as "declares nothing" is the PERMISSIVE
    direction for the key that restricts, and the comment that said "no
    repo.yaml at this rev" was asserting a cause the code never established.
    Found in review, filed LOW because the revs `classify` passes are already
    resolved — this cell drives the reader directly, where they are not.

    So existence is listed first and only then is the blob read: an empty
    listing at exit 0 is absence, and every other failure is a refusal.
    """
    repo = _tree(tmp_path)
    _reword(repo)
    assert proportion.declared_floor(str(repo), "HEAD") == (), (
        "a rev that genuinely has no repo.yaml must declare nothing")

    with pytest.raises(proportion.Undecidable) as absent:
        proportion._declared_at(str(repo), "0" * 40, proportion.FLOOR_ENTRIES)
    assert "could not list repo.yaml" in str(absent.value)

    with pytest.raises(proportion.Undecidable):
        proportion._declared_at(str(repo), "no-such-rev",
                                proportion.FLOOR_ENTRIES)

    # And the same failure reaches the verdict as the full tier rather than as
    # an empty floor, which is the only thing a caller sees.
    verdict = proportion.classify(str(repo), "0" * 40, "HEAD")
    assert not verdict.light and "could not decide" in verdict.reason


# --------------------------------------------------------------------------
# The declaration surface itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("shape,block,valid", [
    ("absent", "", True),
    ("only-never-light", 'proportion:\n  never_light: ["ops/"]\n', True),
    # An empty block declares nothing while looking like policy, and an empty
    # list is the same mistake one level in. Both are refused at load, so a
    # consumer finds out from the schema rather than from a verdict.
    ("an-empty-block", "proportion: {}\n", False),
    ("an-empty-list", "proportion:\n  never_light: []\n", False),
    ("an-unknown-sub-key", 'proportion:\n  always_light: ["x/"]\n', False),
    ("a-path-that-is-not-a-string", "proportion:\n  never_light: [7]\n",
     False),
])
def test_the_schema_takes_exactly_the_shapes_the_classifier_reads(
        tmp_path, shape, block, valid):
    """The schema is the only thing standing between a consumer and a typo, so
    every shape the classifier can be handed is decided here rather than left
    to whichever refusal happens to fire first."""
    from warden import config as config_mod

    root = tmp_path / shape
    root.mkdir(parents=True)
    (root / ".warden" / "rules").mkdir(parents=True)
    (root / "pkg").mkdir()
    (root / "repo.yaml").write_text(ROOT_YAML + block)
    if valid:
        assert config_mod.load(root) is not None
    else:
        with pytest.raises(Exception):
            config_mod.load(root)

def test_the_schema_says_only_the_top_level_repo_yaml_is_read(tmp_path):
    """Consumer blast radius, found in review. The block lands in a SHIPPED
    schema, and a nested module's repo.yaml validates it exactly as the root
    one does — while the classifier reads only the root file, so a nested
    declaration binds nothing. For the restricting key that is the permissive
    direction, and nothing on the page said so.

    Behaviour first, then the sentence: a nested repo.yaml declaring a path
    must leave that path light, and both descriptions must say why.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    repo = _tree(tmp_path, _yaml())
    (repo / "examples" / "c").mkdir(parents=True)
    (repo / "examples" / "c" / "repo.yaml").write_bytes(_yaml("vendor/"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "nested")
    _git(repo, "tag", "-f", "base")
    _reword(repo)
    assert _tier(repo).light, (
        "a nested repo.yaml's never_light entry took effect, which would make "
        "the sentence below wrong in the other direction")

    schema = json.loads(
        (root / "warden" / "schemas" / "repo.schema.json").read_text())
    block = schema["properties"][proportion.FLOOR_KEY]["properties"]
    assert "git top level" in block[proportion.FLOOR_ENTRIES]["description"], (
        f"`{proportion.FLOOR_ENTRIES}`'s description does not say it is read "
        "only from the repo.yaml at the git top level, so a nested consumer "
        "can validate a block that does nothing")


def test_the_new_key_carries_a_hash_claim(tmp_path):
    """D-02's obligation: a property the shipped schema allows must have a
    GATE_INPUTS entry saying whether it is hashed into rules_version. The live
    check next door proves the claim by perturbation; this names the entry so
    deleting it is red here too."""
    claimed = {g.where: g for g in declared_mod.GATE_INPUTS}
    entry = claimed.get("repo.yaml:proportion")
    assert entry is not None, "the new key declares no hash claim"
    assert entry.hashed is False
    assert proportion.FLOOR_ENTRIES in entry.reason, (
        f"the claim does not account for `{proportion.FLOOR_ENTRIES}`, so "
        "the block is declared by nothing")


def test_this_repo_declares_a_floor_entry():
    """What this repo actually declares, asserted rather than branched around.

    `.claude/` is the floor entry and the reason the key exists: harness
    configuration that executes on every session here, which no platform
    constant could have known about. It must NOT be covered by the constant,
    or the declaration proves nothing. This goes red the commit it changes.
    """
    from pathlib import Path

    import yaml

    from warden import config as config_mod

    root = Path(__file__).resolve().parent.parent
    doc = yaml.safe_load(config_mod.read_repo_yaml(root))
    assert config_mod.load(root) is not None, (
        "repo.yaml no longer validates against the shipped schema")
    block = doc[proportion.FLOOR_KEY]
    assert ".claude/" in block[proportion.FLOOR_ENTRIES], block
    assert proportion.under_floor(".claude/agents/reviewer.md",
                                  tuple(block[proportion.FLOOR_ENTRIES]))
    assert not proportion.under_floor(".claude/agents/reviewer.md"), (
        "the platform constant already covers `.claude/`, so declaring it "
        "proves nothing about the key")


# --------------------------------------------------------------------------
# The key the block no longer carries
# --------------------------------------------------------------------------

def test_a_declared_prose_root_is_refused_with_exit_2_and_one_message(
        tmp_path, monkeypatch, capsys):
    """`proportion.prose_roots` once admitted Markdown under a declared root.
    The arm behind it is gone, and a consumer still declaring the key must be
    TOLD so rather than handed a different tier in silence.

    The schema keeps the key, because schemas are additive and an older
    repo.yaml must still parse; the refusal is the runtime's. Both readers of
    the block refuse with the one sentence: `config.parse`, which every
    `warden` command loads through, so the command exits 2 with it on stderr;
    and `_declared_at`, which reads the block off each end of a range, so the
    classifier returns the full tier carrying it. A control without the key
    passes both, which is what makes the refusal the key's.
    """
    from warden import cli
    from warden import config as config_mod

    declared = ROOT_YAML + 'proportion:\n  prose_roots: ["docs/"]\n'
    message = proportion.REMOVED_ENTRIES_REFUSAL
    assert "prose arm" in message and "removed" in message
    assert "Delete the key" in message

    # The working-tree seam: every command loads through it.
    with pytest.raises(config_mod.ConfigError) as refused:
        config_mod.parse(declared.encode(), tmp_path, "repo.yaml")
    assert message in str(refused.value)
    config_mod.parse(_yaml("vendor/"), tmp_path, "repo.yaml")   # the control

    # The same seam through the CLI: exit 2, the sentence on stderr.
    repo = _tree(tmp_path, declared.encode())
    _reword(repo)
    monkeypatch.chdir(repo)
    assert cli.main(["attest", "classify", "--base", "base"]) == 2
    err = capsys.readouterr().err
    assert message in err, err

    # The commit seam: the classifier reads the block off the range's ends
    # and refuses to the full tier with the same sentence.
    verdict = _tier(repo)
    assert not verdict.light
    assert "could not decide" in verdict.reason, verdict.reason
    assert message in verdict.reason, verdict.reason
    control = _tree(tmp_path, _yaml("ops/"), name="control")
    _reword(control)
    assert _tier(control).light
