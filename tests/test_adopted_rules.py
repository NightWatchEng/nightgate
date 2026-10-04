"""The declarative starters this repo adopted from its own catalog.

WHY THIS FILE EXISTS, and why it is not covered by test_declarative.py: that
file tests the declarative ENGINE against a fixture rule. This one tests the
PATTERNS this repo actually gates on, loaded from the real `.warden/rules/`.
A declarative rule nobody has watched fire is a pattern nobody has tested,
and `test-cannot-fail` is a promotable class in this repo's own corpus.

Each check is pinned twice, and the second half is the half that matters:

  FIRES   — the violation the catalog cites is caught.
  SILENT  — the SAFE spelling of the same call is NOT caught.

A pattern that flags `yaml.safe_load` or `sha256` alongside its targets would
pass a fires-only test while making the gate useless, which is `overbroad-
pattern` — a class already in this corpus. Deleting the SILENT cases is how
this file stops being a test.
"""

from pathlib import Path

import pytest
import yaml

from warden import declarative
from warden import rules as rules_mod
from warden.diffs import DiffContext
from warden.review import applicable

from conftest import tracked
from private_evidence import EXPORT, publish_excludes

ROOT = Path(__file__).parent.parent

RULES_DIR = ROOT / ".warden" / "rules"


def _assert_covers_root_and_example(workflows: list[Path]) -> None:
    """The anti-vacuity floor, stated as what the message actually claims.

    `>= 2` was satisfied by the two ROOT workflows alone, so the example's
    workflow — the file the whole examples-gating story rests on — could be
    renamed or untracked and both assertions would keep passing while their
    messages claimed to cover it. Assert membership, not a count: a floor that
    a subset satisfies is not a floor.
    """
    rels = {str(w.relative_to(ROOT)) for w in workflows}
    example = "examples/hello-svc/.github/workflows/ci.yml"
    assert any(r.startswith(".github/workflows/") for r in rels), (
        f"no ROOT workflow in the search set: {sorted(rels)}")
    assert example in rels, (
        f"the example's workflow is not in the search set: {sorted(rels)} — "
        "renamed or untracked, and these assertions would keep passing while "
        "claiming to cover the copy consumers take")


# Each adopted rule mapped to the catalog id its `implements:` must name. If a
# rule id or link is renamed, the ALREADY ENFORCED row in `warden rules
# recommend` silently reverts to a PRIOR ART gap.
ADOPTED = {
    "unsafe-deserialization": "unsafe-deserialization",
    "weak-cryptography": "weak-cryptography",
    "security-misconfiguration": "security-misconfiguration",
    # The two judgment-call starters: every firing line carries a written
    # verdict (kept, under a reasoned warden:allow marker at the line).
    "os-command-injection": "os-command-injection",
    "swallowed-exceptions": "swallowed-exceptions",
}

# (rule id, check id, a line that MUST fire, a safe line that MUST NOT)
CASES = [
    ("unsafe-deserialization", "pickle-loads",
     "    obj = pickle.loads(blob)",
     "    obj = json.loads(blob)"),
    ("unsafe-deserialization", "pickle-loads",
     "    obj = marshal.load(fh)",
     "    obj = tomllib.load(fh)"),
    ("unsafe-deserialization", "yaml-unsafe-load",
     "    cfg = yaml.load(fh)",
     "    cfg = yaml.safe_load(fh)"),
    # A bare `yaml\.load\(` catches only the spelling that ERRORS under
    # PyYAML 6 and misses the one named for the vulnerability.
    # safe_load_all must stay silent.
    ("unsafe-deserialization", "yaml-unsafe-load",
     "    cfg = yaml.unsafe_load(fh)",
     "    cfg = yaml.safe_load_all(fh)"),
    ("unsafe-deserialization", "yaml-unsafe-load",
     "    cfg = yaml.load_all(fh)",
     "    cfg = json.load(fh)"),
    ("weak-cryptography", "broken-hash",
     "    digest = hashlib.md5(payload).hexdigest()",
     "    digest = hashlib.sha256(payload).hexdigest()"),
    ("weak-cryptography", "broken-hash",
     "    digest = sha1(payload)",
     "    digest = sha512(payload)"),
    ("weak-cryptography", "predictable-token-source",
     "    token = random.choice(alphabet)",
     "    token = secrets.choice(alphabet)"),
    # The two most direct spellings of the thing this check is named for
    # must fire, not only `choice`.
    ("weak-cryptography", "predictable-token-source",
     "    token = random.randbytes(16)",
     "    token = secrets.token_bytes(16)"),
    ("weak-cryptography", "predictable-token-source",
     "    token = random.getrandbits(128)",
     "    token = secrets.randbits(128)"),
    # `choices` is the most common token idiom, and an alternation keyed on
    # `choice\(` misses it — `choice` is followed by `s`, not `(`, and the
    # alternation does not backtrack. shuffle/uniform stay silent by design;
    # they are not token sources.
    ("weak-cryptography", "predictable-token-source",
     "    token = ''.join(random.choices(alphabet, k=32))",
     "    random.shuffle(cases)"),
    ("security-misconfiguration", "debug-enabled",
     "    DEBUG = True",
     "    DEBUG = False"),
    ("security-misconfiguration", "tls-verification-off",
     "    requests.get(url, verify=False)",
     "    requests.get(url, verify=True)"),
    # The safe form for shell-true is the argv list the
    # message tells authors to use; for os-system it is subprocess.run with
    # a list, which the alternation must not catch.
    ("os-command-injection", "shell-true",
     "    proc = subprocess.run(cmd, shell=True)",
     "    proc = subprocess.run([exe, arg], check=True)"),
    ("os-command-injection", "shell-true",
     "    proc = subprocess.run(cmd, shell = True)",
     "    shell = detect_shell()"),
    ("os-command-injection", "os-system",
     '    os.system("rm " + path)',
     '    os.makedirs(path)'),
    ("os-command-injection", "os-system",
     "    out = subprocess.getoutput(cmd)",
     "    out = subprocess.run([exe], capture_output=True)"),
    # The left \b is load-bearing: without it, plain boolean config flags
    # (`use_shell = True`, `powershell = True`) and identifier substrings
    # (`myos.system(`) satisfy the patterns.
    ("os-command-injection", "shell-true",
     "    subprocess.Popen(cmd, shell=True)",
     "    use_shell = True"),
    ("os-command-injection", "shell-true",
     "    run(c, shell =True)",
     "    powershell = True"),
    ("os-command-injection", "os-system",
     "    os.popen(cmd)",
     "    myos.system(cmd)"),
    # except-pass is scope:file, exercised separately below — CASES drives
    # the added-lines path only.
]


def _rule(rule_id: str) -> rules_mod.Rule:
    for rule in rules_mod.load_rules(RULES_DIR):
        if rule.id == rule_id:
            return rule
    raise AssertionError(f"{rule_id} not declared in {RULES_DIR}")


def _run(rule: rules_mod.Rule, line: str,
         name: str = "warden/sample.py") -> list[dict]:
    ctx = DiffContext(
        base="a" * 40, head="b" * 40, files=(name,),
        added={name: ((7, line),)}, removed={},
        read_base=lambda f: None, read_head=lambda f: None)
    return declarative.run(rule, ctx, [name])


@pytest.mark.parametrize("rule_id,check_id,bad,_good", CASES)
def test_adopted_check_fires_on_the_violation(rule_id, check_id, bad, _good):
    found = _run(_rule(rule_id), bad)
    assert found, f"{rule_id}/{check_id} did not fire on: {bad}"
    assert any(check_id in f["finding"] for f in found), \
        f"fired, but not via {check_id}: {[f['finding'] for f in found]}"
    assert found[0]["evidence"] == bad.strip()


@pytest.mark.parametrize("rule_id,check_id,_bad,good", CASES)
def test_adopted_check_is_silent_on_the_safe_form(rule_id, check_id, _bad, good):
    # The anti-overbroad half. `yaml.safe_load`, `sha256`, `secrets.choice`
    # and `verify=True` are the spellings the codebase actually uses; a
    # pattern that flags them turns the gate into noise.
    assert not _run(_rule(rule_id), good), \
        f"{rule_id}/{check_id} flagged the SAFE form: {good}"


@pytest.mark.parametrize("rule_id,catalog_id", sorted(ADOPTED.items()))
def test_adopted_rule_declares_its_catalog_link(rule_id, catalog_id):
    # `warden rules recommend` keys ALREADY ENFORCED off `implements:`, not
    # off the rule id. Dropping this link is invisible without a test: the
    # rule keeps gating while the advisor starts reporting the class as an
    # unenforced gap.
    assert catalog_id in _rule(rule_id).implements


@pytest.mark.parametrize("rule_id", sorted(ADOPTED))
def test_adopted_rule_reports_without_blocking(rule_id):
    # MEDIUM is deliberate (blocking_severities is [HIGH]): these ship
    # reporting while they earn a precision history. Promoting one to HIGH is
    # a decision with a precision record behind it, not a passing edit.
    assert _rule(rule_id).severity == "MEDIUM"


@pytest.mark.parametrize("rule_id", sorted(ADOPTED))
def test_adopted_rule_actually_gates_the_shipped_tree(rule_id):
    # THE SCOPE HALF. Every case above bypasses `applies_to` entirely: they
    # call declarative.run with a hand-built file list, and declarative.run
    # never consults rule.applies_to — `warden.review.applicable` is the only
    # thing that does. Without this test, an applies_to of "wardn/**/*.py"
    # (a typo matching NOTHING, so the rule gates zero files) leaves the
    # whole suite green.
    #
    # This asserts through `applicable`, the function the real gate calls,
    # over EVERY shipped module — not a spot check. A probe of a few paths
    # would be an enforcement-claim of its own: a literal two-file
    # applies_to, or an excludes: list blinding the rule on one module,
    # would pass it.
    shipped = sorted(
        str(p.relative_to(ROOT))
        for d in ("warden", "cage")
        for p in (ROOT / d).rglob("*.py")
        if "__pycache__" not in p.parts)
    assert len(shipped) > 20, f"tree probe found only {len(shipped)} modules"

    hits = applicable((_rule(rule_id),), shipped)
    ungated = sorted(set(shipped) - set(hits.get(rule_id, [])))
    assert not ungated, (
        f"{rule_id} does not gate {len(ungated)} shipped module(s): "
        f"{ungated[:5]}{'...' if len(ungated) > 5 else ''} — a rule scoped "
        f"past real code reports green over files it never read")


@pytest.mark.parametrize("rule_id", sorted(ADOPTED))
def test_adopted_rule_does_not_scan_its_own_quoted_patterns(rule_id):
    # Rule bodies and the catalog quote the very strings they hunt --
    # `warden/guardrails/catalog.yaml` contains the literal `yaml.load(` in a
    # message field. secrets-in-diff records this trap as
    # `excludes: [".warden/rules/**"]`; these three rely on a narrow
    # applies_to instead, so the narrowness IS the guard and is pinned here.
    #
    # Asserted by MATCHING, not by string shape. A
    # `glob.startswith("tests/")` check recognises exactly one spelling of
    # "scans tests/": `applies_to: ["**/*.py"]` would pass it while genuinely
    # matching this very file, whose CASES table quotes every pattern
    # verbatim. Ask what the glob MATCHES, never what it looks like.
    rule = _rule(rule_id)
    hits = applicable((rule,), [str(Path(__file__).relative_to(ROOT)),
                                "tests/conftest.py",
                                ".warden/rules/unsafe-deserialization.md",
                                "warden/guardrails/catalog.yaml"])
    assert not hits.get(rule.id), \
        f"{rule_id} now scans files that quote its own patterns: " \
        f"{hits.get(rule.id)} — it will flag the fixtures that prove it works"


# ---------- pinned-actions --------------------------------------------------
# Kept out of ADOPTED above: those are the *.py security starters, and the
# shared tests there assert *.py scope and MEDIUM severity. This rule is
# HIGH and scopes to workflow YAML, so it carries its own cases rather than
# bending the shared ones — a parametrisation that has to special-case its
# members stops pinning anything.

PIN_CASES = [
    # (a line that MUST fire, a line that MUST NOT)
    ("      - uses: actions/checkout@v4",
     "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4.4.0"),
    # An EXACT version tag is still mutable — the whole point of the rule.
    ("      - uses: astral-sh/setup-uv@v5.4.2",
     "      - uses: astral-sh/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86  # v5.4.2"),
    # A branch ref is the worst case and must not slip past.
    ("      - uses: some/action@main",
     "      - uses: some/action@0123456789abcdef0123456789abcdef01234567"),
    # A LOCAL action has no `owner/repo@ref` shape and is not this hazard.
    ("      - uses: owner/repo/.github/workflows/x.yml@v1",
     "      - uses: ./.github/actions/local-thing"),
    # Quoting is legal YAML, and an owner class that cannot cross the
    # opening quote would make it a TOTAL bypass.
    ('      - uses: "actions/checkout@v4"',
     '      - uses: "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"'),
    ("      - uses: 'actions/checkout@v4'",
     "      - uses: 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262'"),
]

# Docker image steps — a second check, because `docker://image:tag` is not
# owner/repo shaped and the action pattern legitimately cannot see it.
# Leaving these silent is a gap, not scoping.
DOCKER_CASES = [
    ("      - uses: docker://alpine:3.8",
     "      - uses: docker://alpine@sha256:" + "a" * 64),
    ("      - uses: docker://ghcr.io/owner/img:latest",
     "      - uses: docker://ghcr.io/owner/img@sha256:" + "b" * 64),
    # No tag at all resolves to :latest — the mutable default.
    ("      - uses: docker://alpine",
     '      - uses: "docker://alpine@sha256:' + "c" * 64 + '"'),
]


@pytest.mark.parametrize("bad,good", DOCKER_CASES)
def test_pinned_actions_fires_on_a_mutable_docker_tag(bad, good):
    found = _run(_rule("pinned-actions"), bad, name=".github/workflows/ci.yml")
    assert found, f"pinned-actions did not fire on: {bad}"
    assert "unpinned-docker-image" in found[0]["finding"]


@pytest.mark.parametrize("bad,good", DOCKER_CASES)
def test_pinned_actions_is_silent_on_a_digest_pinned_image(bad, good):
    assert not _run(_rule("pinned-actions"), good,
                    name=".github/workflows/ci.yml"), \
        f"pinned-actions flagged a digest-pinned image: {good}"


def test_pinned_actions_gates_composite_actions_too():
    # repo.yaml tiers .github/** HIGH, so applies_to must cover more than
    # .github/workflows/**. A composite action runs `uses:` steps under the
    # same job token; a self-reference argument for narrowing does not hold
    # for this glob.
    hits = applicable((_rule("pinned-actions"),),
                      [".github/actions/foo/action.yml",
                       ".github/workflows/ci.yml"])
    assert sorted(hits.get("pinned-actions", [])) == [
        ".github/actions/foo/action.yml", ".github/workflows/ci.yml"]


def test_pinned_actions_does_not_scan_its_own_quoted_patterns():
    # The rule body and this file both quote the shapes it hunts. `.github/**`
    # matches neither — verified rather than assumed, since this argument is
    # easy to make wrongly about a glob.
    hits = applicable((_rule("pinned-actions"),),
                      [".warden/rules/pinned-actions.md",
                       "tests/test_adopted_rules.py",
                       "warden/guardrails/catalog.yaml"])
    assert not hits.get("pinned-actions")


@pytest.mark.parametrize("bad,good", PIN_CASES)
def test_pinned_actions_fires_on_a_mutable_ref(bad, good):
    found = _run(_rule("pinned-actions"), bad, name=".github/workflows/ci.yml")
    assert found, f"pinned-actions did not fire on: {bad}"
    assert "unpinned-action" in found[0]["finding"]


@pytest.mark.parametrize("bad,good", PIN_CASES)
def test_pinned_actions_is_silent_on_a_sha_pin(bad, good):
    # The anti-overbroad half: a correctly pinned action, and a local action,
    # must not be flagged, or every workflow edit argues with the gate.
    assert not _run(_rule("pinned-actions"), good,
                    name=".github/workflows/ci.yml"), \
        f"pinned-actions flagged a correctly pinned or local ref: {good}"


def test_pinned_actions_gates_the_real_workflow_files():
    # Scope asserted through `applicable`, the function the real gate calls —
    # declarative.run never reads applies_to, so a rule scoped to a typo
    # would gate NOTHING while the fires/silent cases stay green.
    workflows = sorted(str(p.relative_to(ROOT))
                       for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "no workflow files found to gate"
    hits = applicable((_rule("pinned-actions"),), workflows)
    assert sorted(hits.get("pinned-actions", [])) == workflows


def test_pinned_actions_blocks_rather_than_reports():
    """HIGH is load-bearing: the rule must FAIL THE GATE, and MEDIUM would
    report without blocking.

    Reads the REAL repo.yaml, because severity alone does not decide blocking
    — `warden/review.py` blocks on `severity in blocking_severities`.
    Asserting only `severity == "HIGH"` would let `blocking_severities:
    [MEDIUM]` leave the suite green while this rule silently degrades to
    reporting: existence checked, validity not.
    """
    from warden import config as config_mod
    rule = _rule("pinned-actions")
    assert rule.severity == "HIGH"
    assert "supply-chain-pinning" in rule.implements

    blocking = config_mod.load(ROOT).review.blocking_severities
    assert rule.severity in blocking, (
        f"pinned-actions is {rule.severity} but repo.yaml blocks only "
        f"{blocking} — the rule reports without failing the gate, which is "
        f"the half of the rule's purpose it exists to satisfy")


def test_every_workflow_ref_in_this_repo_is_sha_pinned():
    """Every action ref in the real workflow files is SHA-pinned.

    The patterns are COMPILED OUT OF THE RULE ITSELF rather than re-typed. A
    regex re-typed here drifts the moment the rule grows a check: one that
    understands only `owner/repo@ref` can never see a `docker://` ref, so
    `uses: docker://alpine:3.8` in a real workflow would leave the suite
    green. A re-typed pattern is a second source of truth; deriving it means
    this can only ever assert exactly what the gate enforces.
    """
    from warden import declarative
    from warden.diffs import DiffContext

    rule = _rule("pinned-actions")
    workflows = tracked("*.github/workflows/*.y*ml")
    _assert_covers_root_and_example(workflows)

    offenders = []
    for wf in workflows:
        rel = str(wf.relative_to(ROOT))
        added = tuple(enumerate(wf.read_text().splitlines(), 1))
        ctx = DiffContext(
            base="a" * 40, head="b" * 40, files=(rel,),
            added={rel: added}, removed={},
            read_base=lambda f: None, read_head=lambda f: None)
        for finding in declarative.run(rule, ctx, [rel]):
            offenders.append(f"{rel}:{finding['line']}: {finding['evidence']}")
    assert not offenders, (
        "unpinned refs the shipped tree still carries:\n  "
        + "\n  ".join(offenders))


# ---------- except-pass (scope:file) and the adjudication -------------------

def _file_ctx(rel: str, content: str) -> DiffContext:
    return DiffContext(
        base="a" * 40, head="b" * 40, files=(rel,),
        added={}, removed={},
        read_base=lambda f: None,
        read_head=lambda f: content if f == rel else None)


def test_except_pass_fires_on_a_bare_swallow():
    rule = _rule("swallowed-exceptions")
    content = "try:\n    f()\nexcept OSError:\n    pass\n"
    found = declarative.run(rule, _file_ctx("warden/sample.py", content),
                            ["warden/sample.py"])
    assert found and "except-pass" in found[0]["finding"]
    assert found[0]["line"] == 3


def test_except_pass_fires_through_a_trailing_comment():
    """`except X:  # transient` + pass must fire: if a throwaway comment broke
    the match, it would be an unreasoned waiver strictly cheaper than the
    sanctioned reasoned marker."""
    rule = _rule("swallowed-exceptions")
    content = ("try:\n    f()\nexcept OSError:  # transient\n    pass\n")
    found = declarative.run(rule, _file_ctx("warden/sample.py", content),
                            ["warden/sample.py"])
    assert found and "except-pass" in found[0]["finding"]


def test_except_pass_stays_silent_when_the_handler_does_work():
    """The comment-crossing widening must not swallow a real handler: a
    body that DOES something before an incidental pass is not a swallow."""
    rule = _rule("swallowed-exceptions")
    content = ("try:\n    f()\nexcept OSError:\n    cleanup()\n    pass\n")
    assert not declarative.run(rule, _file_ctx("warden/sample.py", content),
                               ["warden/sample.py"])


def test_except_pass_is_silent_on_a_handled_except():
    rule = _rule("swallowed-exceptions")
    content = ("try:\n    f()\nexcept OSError as e:\n"
               "    raise VerifyError(str(e)) from e\n")
    assert not declarative.run(rule, _file_ctx("warden/sample.py", content),
                               ["warden/sample.py"])


def test_except_pass_honors_a_reasoned_allow_marker():
    rule = _rule("swallowed-exceptions")
    content = ("try:\n    f()\n"
               "# warden:allow(except-pass): never load-bearing because every reader falls back to sorting run dirs\n"
               "except OSError:\n    pass\n")
    assert not declarative.run(rule, _file_ctx("warden/sample.py", content),
                               ["warden/sample.py"])


def test_except_pass_refuses_a_bare_allow_marker():
    rule = _rule("swallowed-exceptions")
    content = ("try:\n    f()\n"
               "# warden:allow(except-pass)\n"
               "except OSError:\n    pass\n")
    assert declarative.run(rule, _file_ctx("warden/sample.py", content),
                           ["warden/sample.py"]), (
        "a marker with no reason suppressed — reasons are the record")


def test_the_shipped_tree_carries_zero_unallowed_hits():
    """Derived, not asserted: every current hit of both
    adopted judgment-call rules in shipped code sits under a reasoned
    warden:allow marker. Delete any marker, or add a bare swallow or
    shell=True anywhere under warden/ or cage/, and this goes red with the
    offending line named — 'green for reasons a reader can check'."""
    from warden.rules import glob_match

    offenders = []
    for rule_id in ("os-command-injection", "swallowed-exceptions"):
        rule = _rule(rule_id)
        files = [str(p.relative_to(ROOT))
                 for p in sorted(ROOT.glob("warden/**/*.py"))
                 + sorted(ROOT.glob("cage/**/*.py"))]
        files = [f for f in files
                 if any(glob_match(g, f) for g in rule.applies_to)]
        for rel in files:
            content = (ROOT / rel).read_text()
            ctx = _file_ctx(rel, content)
            # scope:added checks need the lines as "added" to exercise the
            # tree; scope:file checks read head content.
            ctx = DiffContext(
                base="a" * 40, head="b" * 40, files=(rel,),
                added={rel: tuple(enumerate(content.splitlines(), 1))},
                removed={}, read_base=lambda f: None,
                read_head=lambda f, _c=content, _r=rel: _c if f == _r else None)
            for finding in declarative.run(rule, ctx, [rel]):
                offenders.append(f"{rel}:{finding.get('line')}: "
                                 f"{finding['finding']}")
    assert not offenders, (
        "unallowed hits in the shipped tree — adjudicate each (a reasoned "
        "warden:allow marker) or fix it:\n  " + "\n  ".join(offenders))


# ---------- pin freshness is a mechanism, not a memory ----------------------

@pytest.mark.skipif(EXPORT and publish_excludes(".github/dependabot.yml"),
                    reason="public export: publish.yaml leaves .github/dependabot.yml out")
def test_dependabot_watches_the_action_pins():
    """The decided answer to pin freshness: a SHA
    nobody updates decays into an unpatched dependency, and a trailing
    version comment makes an update legible without making one happen.
    Dependabot understands SHA pins and bumps the sha AND the comment
    together. Monthly and grouped, deliberately: this repo's Definition of
    Done makes every PR expensive, so staleness surfaces as ONE bundled PR a
    month rather than a stream — deleting this config un-decides it."""
    cfg = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
    # GitHub silently ignores an invalid dependabot.yml — the quiet failure
    # this mechanism exists to close — so the REQUIRED schema keys are
    # pinned too, not only the decided ones: deleting `directory:`
    # invalidates the config and must turn this red.
    assert cfg.get("version") == 2, "dependabot.yml needs version: 2"
    actions = [u for u in cfg.get("updates", [])
               if u.get("package-ecosystem") == "github-actions"]
    assert actions, "no github-actions ecosystem in .github/dependabot.yml"
    entry = actions[0]
    dirs = entry.get("directories") or (
        [entry["directory"]] if entry.get("directory") else [])
    assert "/" in dirs, (
        "github-actions updates need the root directory — without it GitHub "
        "rejects the config silently and no updates ever run")
    assert "/examples/hello-svc" in dirs, (
        "the shipped example left the freshness mechanism's coverage — a "
        "mechanism narrower than the pinned surface lets the copy consumers "
        "take decay invisibly")
    cm = entry.get("commit-message") or {}
    assert cm.get("prefix") == "build" and cm.get("include") == "scope", (
        "the commit header shape must be CONFIGURED, not inferred from "
        "history — commit-lint.sh exempts exactly the configured shape, and "
        "an inferred prefix flipping would turn every monthly PR red")
    assert entry.get("schedule", {}).get("interval") == "monthly", (
        "the recorded decision is monthly — a different cadence is a new "
        "decision, record it in the tracker and here")
    assert entry.get("groups"), (
        "updates must be grouped into one PR — ungrouped bumps are the "
        "review-load cost the decision explicitly contained")


def test_the_example_ships_a_traveling_freshness_mechanism():
    """The root dependabot.yml keeps the shipped example fresh
    INSIDE this repo, but GitHub reads Dependabot config only from a
    repo's root — so it does not travel when a consumer copies hello-svc/ out.
    The example carries its OWN .github/dependabot.yml (inert here, active once
    copied) so the enrollment surface a consumer takes carries a freshness
    mechanism instead of decaying invisibly. Same shape as the root config,
    scoped to the single-repo root the copy will sit at."""
    path = ROOT / "examples" / "hello-svc" / ".github" / "dependabot.yml"
    assert path.exists(), (
        "the example lost its traveling freshness mechanism — a consumer "
        "copying hello-svc/ out gets SHA pins no Dependabot watches")
    cfg = yaml.safe_load(path.read_text())
    assert cfg.get("version") == 2, "example dependabot.yml needs version: 2"
    actions = [u for u in cfg.get("updates", [])
               if u.get("package-ecosystem") == "github-actions"]
    assert actions, "no github-actions ecosystem in the example dependabot.yml"
    entry = actions[0]
    dirs = entry.get("directories") or (
        [entry["directory"]] if entry.get("directory") else [])
    # The consumer's root: hello-svc IS the repo once copied out, so the
    # workflows live at `/`, not at /examples/hello-svc. A config narrower than
    # `/` is rejected by GitHub silently and never runs.
    assert "/" in dirs, (
        "the example's dependabot must watch the root it will sit at once "
        "copied — without it GitHub rejects the config silently")
    assert entry.get("schedule", {}).get("interval") == "monthly", (
        "same recorded cadence as the platform (monthly) — the example teaches "
        "one bundled PR a month, not a stream")
    assert entry.get("groups"), (
        "grouped, like the root config — ungrouped bumps are the review-load "
        "the decision contained")
    cm = entry.get("commit-message") or {}
    assert cm.get("prefix") == "build" and cm.get("include") == "scope", (
        "configured header shape, so a consumer adopting commit-lint gets the "
        "exempted prefix instead of an inferred one that could flip")


def test_pinned_actions_rule_names_the_freshness_mechanism():
    """The rule body answered pin freshness with 'a founder decision
    recorded in the bead' — a documented intention, which this corpus's
    top defect class says must not be mistaken for a mechanism. The body
    must point at the mechanism that actually runs."""
    import re

    body = (RULES_DIR / "pinned-actions.md").read_text()
    # Not a bare filename needle ('we declined dependabot.yml' would satisfy
    # it): the sentence that CARRIES the decision is what must survive.
    assert re.search(r"mechanism is\s+`\.github/dependabot\.yml`", body), (
        "pinned-actions.md no longer states that the freshness mechanism "
        "IS .github/dependabot.yml — the body mentions or describes "
        "something else, which is the intention-not-mechanism regression")


# ---------- pinned-run-invocations ------------------------------------------
# pinned-actions' sibling: the same supply-chain argument for package-manager
# invocations in `run:` steps, which the uses:-shaped patterns above cannot
# see. MEDIUM where pinned-actions is HIGH — these patterns judge free-form
# shell, not a closed grammar, so the rule reports while it earns a precision
# history and was adopted through `warden autonomy adopt`, which refuses
# blocking severities by construction.

RUN_PIN_CASES = [
    # (check id, a line that MUST fire, a line that MUST NOT)
    ("unlocked-uv-run",
     "        run: uv run pytest -q",
     "        run: uv run --locked pytest -q"),
    ("unlocked-uv-run",
     "        run: uv run --project ../.. warden explain",
     "        run: uv run --locked --project ../.. warden explain"),
    # --locked anywhere after the invocation satisfies the check: flag order
    # is a style choice, not a pin.
    ("unlocked-uv-run",
     "        run: uv run warden certify --level 4",
     "        run: uv run --project ../.. --locked warden certify --level 4"),
    # A line-wide lookahead would let a LATER command's --locked vouch for
    # an earlier unlocked invocation. The segment-confined pattern must
    # fire on the first invocation here — and must still credit --locked
    # within the same segment (the silent form).
    ("unlocked-uv-run",
     "        run: uv run pytest -q && uv run --locked warden verify",
     "        run: uv run --locked pytest -q && uv run --locked warden verify"),
    # The borrow must not cross a single | or & either — a two-character
    # stop set (&&, ||, ;) passes them, so a pipeline sibling's --locked
    # (or an unrelated program's --locked-log flag) would vouch for the
    # unlocked head of the pipe.
    ("unlocked-uv-run",
     "        run: uv run gen-report | uv run --locked sink",
     "        run: uv run --locked gen-report | uv run --locked sink"),
    ("unpinned-uvx",
     "        run: uvx ruff check .",
     "        run: uvx ruff==0.16.5 check ."),
    # A sibling command's == must not vouch for a bare uvx, and uvx's own
    # idiomatic exact-version request (`tool@X.Y.Z`) is a PIN, not a float.
    ("unpinned-uvx",
     "        run: uvx black . && pip install x==1.0",
     "        run: uvx ruff@0.16.5 check ."),
    # The Adopting.md shim shape: a --from ref carrying a 40-hex sha is the
    # pinned form; a bare git+ URL runs the branch head.
    ("unpinned-uvx",
     "        run: uvx --from git+https://github.com/acme/tool@main tool",
     "        run: uvx --from git+ssh://git@github.com/acme/tool@"
     + "0123456789abcdef0123456789abcdef01234567" + " tool"),
    # --locked does not govern a --with extra: a lint step can float ruff
    # while reading as locked.
    ("unpinned-with",
     "        run: uv run --locked --with ruff ruff check warden",
     "        run: uv run --locked --with ruff==0.16.5 ruff check warden"),
    ("unpinned-with",
     '        run: "uv run --with pytest python -m pytest tests -q"',
     '        run: "uv run --locked --with pytest==9.1.1 python -m pytest tests -q"'),
    # A (\s|$) tail would let every explicitly FLOATING specifier pass
    # exactly like a pinned one — a range operator, a
    # quoted range, and the equals form must all fire; a quoted ==-pin is
    # genuinely pinned and stays silent.
    ("unpinned-with",
     "        run: uv run --locked --with ruff>=0.16 ruff check warden",
     "        run: uv run --locked --with 'ruff==0.16.5' ruff check warden"),
    ("unpinned-with",
     "        run: uv run --locked --with 'ruff>=0.16' ruff check warden",
     "        run: echo done"),
    ("unpinned-with",
     "        run: uv run --locked --with=ruff ruff check warden",
     "        run: uv run --locked ruff check warden"),
    # DIRECT REFERENCES — a sha-pinned git ref and a wheel URL — are scoped
    # out of the check entirely (`+`, `:`, `/` excluded from the
    # backtrack-killing tail; the rule body records that ref pinned-ness is
    # not judged either way).
    ("unpinned-with",
     "        run: uv run --locked --with ruff~=0.16 ruff check warden",
     "        run: uv run --locked --with git+https://github.com/acme/tool@"
     "0123456789abcdef0123456789abcdef01234567 tool"),
    ("unpinned-with",
     "        run: uv run --locked --with pkg[extra] x",
     "        run: uv run --locked --with https://files.example/ruff-0.16.5-py3-none-any.whl x"),
    # npx is flagged even version-tagged: the tag pins one package, not the
    # transitive tree it resolves nor the lifecycle scripts it executes.
    ("npx-at-run-time",
     "        run: npx -y @mermaid-js/mermaid-cli@11.16.0 -i doc.md",
     "        run: bash scripts/validate-diagrams.sh docs/wiki"),
    ("npx-at-run-time",
     "        run: npx prettier --check .",
     "        run: npm ci --ignore-scripts --no-audit --no-fund"),
    # There is deliberately NO pip check: the rule body records why (a line
    # regex cannot judge per-package pinning on a multi-package install).
    # This safe-only row pins that a pip line does not
    # fire ANY check by accident.
    ("npx-at-run-time",
     "        run: npx -y something",
     "        run: pip install requests==2.32.3 flask"),
]


@pytest.mark.parametrize("check_id,bad,_good", RUN_PIN_CASES)
def test_pinned_run_invocations_fires_on_the_floating_form(check_id, bad, _good):
    found = _run(_rule("pinned-run-invocations"), bad,
                 name=".github/workflows/ci.yml")
    assert found, f"pinned-run-invocations did not fire on: {bad}"
    assert any(check_id in f["finding"] for f in found), (
        f"fired, but not via {check_id}: {[f['finding'] for f in found]}")


@pytest.mark.parametrize("check_id,_bad,good", RUN_PIN_CASES)
def test_pinned_run_invocations_is_silent_on_the_pinned_form(check_id, _bad, good):
    # The anti-overbroad half: a locked/pinned invocation must not be
    # flagged, or every workflow edit argues with the gate.
    assert not _run(_rule("pinned-run-invocations"), good,
                    name=".github/workflows/ci.yml"), \
        f"pinned-run-invocations flagged the pinned form: {good}"


def test_pinned_run_invocations_gates_the_real_workflow_files():
    # Scope asserted through `applicable`, the function the real gate calls
    # (declarative.run never reads applies_to).
    workflows = sorted(str(p.relative_to(ROOT))
                       for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "no workflow files found to gate"
    hits = applicable((_rule("pinned-run-invocations"),), workflows)
    assert sorted(hits.get("pinned-run-invocations", [])) == workflows


def test_pinned_run_invocations_does_not_scan_its_own_quoted_patterns():
    # The rule body, this file, and the shell script whose pinning it
    # motivated all quote the shapes it hunts. Its globs match none of them —
    # verified by matching, never by string shape.
    hits = applicable((_rule("pinned-run-invocations"),),
                      [".warden/rules/pinned-run-invocations.md",
                       "tests/test_adopted_rules.py",
                       "scripts/validate-diagrams.sh",
                       "docs/wiki/Adopting.md"])
    assert not hits.get("pinned-run-invocations")


def test_pinned_run_invocations_reports_without_blocking():
    """MEDIUM is load-bearing in the OPPOSITE direction from pinned-actions:
    the autonomy ladder's machine rung may only ADD reporting, and `warden
    autonomy adopt` refuses a severity in blocking_severities — this rule
    landing non-blocking is what made the machine adoption legitimate.
    Promotion to HIGH is a human decision with a precision record behind it.

    Read against the REAL repo.yaml, not the severity string alone, because
    blocking_severities decides whether a severity blocks."""
    from warden import config as config_mod
    rule = _rule("pinned-run-invocations")
    assert rule.severity == "MEDIUM"
    assert "supply-chain-pinning" in rule.implements
    blocking = config_mod.load(ROOT).review.blocking_severities
    assert rule.severity not in blocking, (
        f"pinned-run-invocations is {rule.severity} and repo.yaml blocks "
        f"{blocking} — a machine-adopted rule now fails builds, which the "
        "ladder reserves for a human decision")


def test_every_run_invocation_in_this_repo_is_pinned():
    """Every package-manager invocation in the real workflows is pinned,
    asserted with the rule's OWN checks — derived, never re-typed, so this
    can only assert exactly what the gate enforces (a re-typed pattern here
    goes blind the moment the rule grows a check)."""
    from warden import declarative
    from warden.diffs import DiffContext

    rule = _rule("pinned-run-invocations")
    workflows = tracked("*.github/workflows/*.y*ml")
    _assert_covers_root_and_example(workflows)

    offenders = []
    for wf in workflows:
        rel = str(wf.relative_to(ROOT))
        added = tuple(enumerate(wf.read_text().splitlines(), 1))
        ctx = DiffContext(
            base="a" * 40, head="b" * 40, files=(rel,),
            added={rel: added}, removed={},
            read_base=lambda f: None, read_head=lambda f: None)
        for finding in declarative.run(rule, ctx, [rel]):
            offenders.append(f"{rel}:{finding['line']}: {finding['evidence']}")
    assert not offenders, (
        "unpinned package-manager invocation(s) in the real workflows:\n  "
        + "\n  ".join(offenders))
