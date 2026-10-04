"""The platform's own cage.

`.cage/cage.toml` and `.cage/prompt.md` are the versioned source of the cage
that runs THIS repo unattended. They bind only once copied beside the
checkout and enrolled, so what a test can hold is the source: it loads
under the cage's own fail-closed schema, its forbidden paths are the
policy's `## Forbidden paths` and nothing else, the prompt hard-invokes
the run protocol and restates every fence, and — in a layout where a
consumer's cage already sits in `../cage/` — the three N checks earn from
it. The lockstep test matches with `grep -E`, the way the runner does.

One relation runs the other way: `repo.yaml`'s HIGH risk tiers are the
SOURCE, and both the fence and the prompt are derived from them rather than
kept beside them. `test_the_fence_and_the_prompt_close_every_high_tiered_path`
and `test_the_prompts_eligibility_sentence_keeps_no_second_fence_list` below
are what make that a check instead of a habit.
"""

import ast
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from cage import config as cage_config
from warden import certify as certify_mod
from warden import enroll as enroll_mod
from warden import config as repo_config
from warden import verify as verify_mod

from tests.test_certify import make_repo

ROOT = Path(__file__).parent.parent
SELF_TOML = ROOT / ".cage" / "cage.toml"
SELF_PROMPT = ROOT / ".cage" / "prompt.md"
POLICY = ROOT / ".warden" / "skills-policy.md"


def policy_forbidden_paths() -> list[str]:
    """The backticked paths in the FIRST paragraph of `## Forbidden paths` —
    the "Never edit:" list. The prose below it names paths too
    (`.warden/rules/*.md` in the carve-out, the three `warden/` files again in
    the note on the tiers) that are illustrations, not fence entries."""
    text = POLICY.read_text()
    m = re.search(r"^## Forbidden paths\n\n(.*?)\n\n", text, re.S | re.M)
    assert m, "policy has no `## Forbidden paths` section with a first paragraph"
    paths = re.findall(r"`([^`]+)`", m.group(1))
    assert paths, "the Never-edit paragraph names no backticked paths"
    return paths


def probe_for(path: str) -> str:
    """A diff line the runner would see for an edit under `path`."""
    return path + "x.md" if path.endswith("/") else path


def prompt_boundaries() -> str:
    """The prompt's `Non-negotiable boundaries` paragraph, flowed to one line.

    Read as a PARAGRAPH, for the reason `policy_forbidden_paths` reads one:
    the prompt also EXPLAINS its fences in prose, and a path named only in an
    explanation binds nothing. A whole-file `in` check passes on that mention
    and becomes a guard that cannot fail — measured while writing
    the tier guard: deleting `warden/plugins.py` from this paragraph
    left a whole-file check GREEN, because the note about the bug two
    paragraphs down says the name.
    """
    m = re.search(r"^Non-negotiable boundaries\b.*?(?:\n\s*\n|\Z)",
                  SELF_PROMPT.read_text(), re.S | re.M)
    assert m, "the prompt has no `Non-negotiable boundaries` paragraph"
    return " ".join(m.group(0).split())


OFFER_KEY = "yours to take"

# A prohibition, in the spellings THIS prompt uses for one. Deliberately not
# an attempt to read negation in general — the bound is stated in
# `test_every_surface_the_prompt_hands_over_survives_the_fence` below, and it
# is the same bargain `prose-parsers-canonical-form-not-phrasing-guesses`
# asks for: one canonical OFFER construction, held positively, and a small
# refusal set for the sentences around it.
PROHIBITION = re.compile(
    r"\bnever\b|\bnot in scope\b|\bout of scope\b|\boff[- ]limits\b"
    r"|\bforbidden\b|\bdo not\b|\bnot yours\b", re.I)


# One flipped sentence per alternative in `PROHIBITION`, so that no
# alternative can be deleted with this module green. Behavioural controls,
# not a restatement: each is APPENDED to the real prompt and fed through
# `offer_contradictions` below, so the row proves the spelling changes a scan
# outcome rather than that someone typed it twice. Completeness is executed
# by `test_every_prohibition_spelling_is_planted`.
#
# Each plant keeps the name and the refusal in ONE SENTENCE, because that is
# what the scan reads. Two were reworded off an em dash while the splitter
# still cut on one — round 2 read that correctly as the plants being fitted
# to the parser, and
# `test_a_refusal_joined_by_an_em_dash_is_not_cut_in_half` is the
# regression that keeps the splitter from cutting there again.
PROHIBITION_PLANTS = (
    "The example consumer under `examples/` is never in scope for this run.",
    "The example consumer under `examples/` is not in scope for this run.",
    "The example consumer under `examples/` is out of scope for this run.",
    "The example consumer under `examples/` is off-limits for this run.",
    "The example consumer under `examples/` is off limits for this run.",
    "The example consumer under `examples/` is forbidden for this run.",
    "Do not touch the example consumer under `examples/` on this run.",
    "The example consumer under `examples/` is not yours to edit.",
)


def sentences(text: str) -> list[str]:
    """`text` flowed to one line and cut at SENTENCE boundaries — the unit
    the polarity scan below reads.

    THE UNIT ERRS LARGE, and both rounds of this branch's review paid for
    getting that wrong, in opposite directions. Round 1: the offer sentence
    was EXEMPT from the scan, so a refusal joined to it rode inside the
    exempt region. Round 2's repair dropped the exemption but split on the
    em dash as well, to separate the prompt's honest "are never eligible"
    from the offered names — and that SHRANK the unit, so "`examples/` —
    never touch it" put the name in one chunk and the refusal in the next
    and the scan saw neither. Both were one mistake: fitting the parser to
    a sentence of the document instead of the document to the parser. The
    tell was that two `PROHIBITION_PLANTS` rows had to be reworded off the
    em dash to keep witnessing anything.

    So the unit is the sentence and nothing smaller, and the prompt carries
    its scope exclusion in a sentence of its own. A unit too BIG can only
    pair a name with a refusal that was not meant for it — a false positive,
    paid in one reword — and a unit too SMALL hides a refusal, which is the
    whole defect. A semicolon, a comma and an em dash all stay INSIDE the
    sentence for that reason: a refusal joined by any of them stays with the
    name it refuses.
    """
    return " ".join(text.split()).split(". ")


def offer_sentence_of(prompt: str) -> str:
    """The ONE eligibility sentence of `prompt` text, flowed to one line.

    The SENTENCE, for the reason `prompt_boundaries` above reads a paragraph
    and `policy_forbidden_paths` reads one: polarity lives in structure, and
    a whole-file `in` check cannot see it. Measured: the
    membership half of the relation test below was `f"`{name}`" in prompt`,
    and flipping the prompt's `examples/` paragraph to "NOT in scope — never
    touch anything under `examples/`" left both of its assertions GREEN,
    because the backticked token was still somewhere in the file. A guard
    that cannot redden in one direction is not guarding that direction.

    The prompt states the polarity by CONSTRUCTION rather than by wording,
    which is what makes this readable: one sentence offers directories, the
    boundaries paragraph withholds them, and a name belongs to exactly one.
    """
    found = [s for s in sentences(prompt) if OFFER_KEY in s]
    assert len(found) == 1, (
        f"expected exactly one sentence carrying {OFFER_KEY!r}, found "
        f"{len(found)} — either the phrase every guard on the offer keys "
        "on has moved, or the prompt has grown a second offer list, which is "
        "the second hand-kept list the tiers replaced, all over again")
    return found[0]


def prompt_offer_sentence() -> str:
    """`offer_sentence_of` the real `.cage/prompt.md`."""
    return offer_sentence_of(SELF_PROMPT.read_text())


def offered_directories(offer: str) -> list[str]:
    """Every directory the offer sentence backticks, DERIVED not transcribed.

    A hand-kept list is the shape the tier derivation replaced, and it was
    reachable again in the commit that added the parser: the relation test
    iterated a hand-kept dict, so it held every name IT knew to the fence and
    saw nothing when a seventh — measured with `.github/`, which the
    post-check closes — was added to the offer. Reading the names OUT of the
    sentence is what makes the add direction visible; `HANDED_OVER` is then a
    probe per derived name, held equal to this set rather than trusted as it.
    """
    return sorted(set(re.findall(r"`(\.?[A-Za-z0-9_][A-Za-z0-9_./-]*/)`",
                                 offer)))


# What may sit either side of a path name for it to be THAT path rather than
# a longer one. Anything that cannot continue a path: the prompt writes a
# fence entry bare and comma-separated (`never touch .warden/, repo.yaml`),
# ends its last one with a semicolon, and backticks an offer, so the edge is
# defined by what a path CANNOT contain rather than by listing the
# punctuation the prompt happens to use today. Round 1 measured both halves
# of that: the first spelling listed `[ ,.]` and could not see the real
# `.claude/` entry, which the paragraph terminates with `;` (pattern-fit),
# and it required a backtick, which the prompt's own prohibitions never
# carry (fail-closed).
_PATH_EDGE = r"[^A-Za-z0-9_./-]"


def names_path(text: str, name: str) -> bool:
    """Does `text` name the path `name`, as itself and not as a longer one?

    Whole-name, not substring, and that is the whole of it: the boundaries
    paragraph names `warden/certification/` and `.warden/`, and a substring
    test would read either as naming `warden/`, which it does not — the fence
    binds at the repo root and those are different names. Backticks are an
    edge like any other, so the offered `` `examples/` `` and a bare
    "under examples/" both count.
    """
    return bool(re.search(rf"(?:^|{_PATH_EDGE}){re.escape(name)}"
                          rf"(?={_PATH_EDGE}|$)", text))


def withholds(boundaries: str, name: str) -> bool:
    """Does the boundaries paragraph name `name` as a fence ENTRY?"""
    return names_path(boundaries, name)


def grep_e(regex: str, lines: list[str]) -> list[str]:
    """Exactly the runner's post-check: `printf '%s\\n' ... | grep -E RE`."""
    proc = subprocess.run(["grep", "-E", regex], input="\n".join(lines) + "\n",
                          capture_output=True, text=True)
    assert proc.returncode < 2, f"grep -E rejected {regex!r}: {proc.stderr}"
    return proc.stdout.splitlines()


@pytest.fixture(scope="module")
def cfg() -> cage_config.CageConfig:
    return cage_config.load(SELF_TOML)


def test_the_self_cage_loads_under_the_cages_own_schema(cfg):
    assert cfg.name == "nightgate"
    assert cfg.github == "NightWatchEng/agentops"
    assert cfg.triggers == ("manual",)
    assert cfg.quiet_enabled is False
    assert cfg.schedule is None, "no clock fires this cage"
    assert cfg.platform_probe == ".warden/bin/warden --help", (
        "the platform repo's shim is the pinned platform here")
    # The policy's ladder grants the machine tier its four narrow writes
    # (three at first, then the attestation shard); the runner-side half of that grant is declared,
    # never inferred. The COUNT is prose and went stale once already —
    # what binds is the grant below, which delegates to
    # `warden autonomy carve-out`, the one definition of the set.
    assert cfg.autonomy_carve_out is True


def test_the_source_names_no_cage_dir_so_the_copy_binds_where_it_lives():
    """`[cage].dir` absent: enroll renders beside the toml, certify finds the
    toml beside the checkout, so the installed copy must resolve its cage
    dir from its own location — a dir named here would be a second path to
    keep in step with where the founder actually copies it."""
    data = SELF_TOML.read_text()
    assert not re.search(r"^\[cage\]", data, re.M)
    assert not re.search(r"^dir\s*=", data, re.M)


def expected_pattern(path: str) -> str:
    """The one ERE the toml may carry for a policy path: dots escaped, a
    directory left open, a file anchored at its end."""
    pattern = path.replace(".", r"\.")
    return pattern if path.endswith("/") else pattern + "$"


def test_forbidden_paths_are_the_policys_and_nothing_else(cfg):
    r"""Lockstep, both directions. Forward, under the runner's own matcher:
    every path the policy's Never-edit list names is caught by the rendered
    regex. Reverse, by EQUALITY of the pattern list with the canonical
    rendering of the policy's paths, because a per-pattern "matches some
    policy probe" check lets a widened pattern through (`\.git` for
    `\.github/` + `\.githooks/` still hits the `.github/` probe while fencing
    `.gitignore`); equality does not."""
    declared = policy_forbidden_paths()
    probes = [probe_for(p) for p in declared]
    hit = grep_e(cfg.forbidden_regex, probes)
    assert sorted(hit) == sorted(probes), (
        f"policy paths the cage would NOT close: {sorted(set(probes) - set(hit))}")
    assert sorted(cfg.forbidden_paths) == sorted(expected_pattern(p) for p in declared)


def test_forbidden_regex_leaves_ordinary_work_alone(cfg):
    """The freeze is the gate surface, not the repo: the paths a run here
    would legitimately touch pass the post-check.

    The three under `examples/` are the SURPRISING ones and the reason this
    list is worth reading: `.cage/cage.toml` cites this test by name for the
    claim that the example consumer's own `.warden/`, `repo.yaml` and
    `.github/` are ordinary work, and the list once carried
    only `repo.yaml` — a citation wider than its evidence, for exactly the
    two paths a reader would want proof of. They pass because the runner's
    alternation is START-ANCHORED, so `\\.warden/` and `\\.github/` bind at
    the repo root and say nothing about the same names nested under the
    portability fixture. Real paths in the tree, not invented ones: an
    `examples/` path that stopped existing would make the citation true of
    nothing.
    """
    ordinary = ["warden/certify.py", "cage/config.py", "cage/cli.py",
                "tests/test_cage.py", "docs/wiki/The-Cage.md", ".gitignore",
                "skills/nightgate-skills/skills/deliver/SKILL.md",
                "examples/hello-svc/repo.yaml",
                "examples/hello-svc/.warden/skills-policy.md",
                "examples/hello-svc/.github/workflows/ci.yml", "CLAUDE.md"]
    for path in ordinary:
        assert (ROOT / path).exists(), (
            f"{path!r} is not in the tree, so passing the fence proves "
            "nothing about any work a run here would do")
    assert grep_e(cfg.forbidden_regex, ordinary) == []


def test_the_prompt_invokes_the_run_protocol_and_restates_every_fence():
    prompt = SELF_PROMPT.read_text()
    # The name must be one the pack in this tree serves: a prompt invoking a
    # skill the installed pack does not serve starts a session with no
    # protocol layer. (run.sh's own pre-flight for this still greps the old
    # pack name — see the xfail below.)
    skill = re.search(r"nightgate-skills:[a-z][a-z0-9-]*", prompt)
    assert skill and skill.group(0) == "nightgate-skills:autonomous-run"
    assert (ROOT / "skills/nightgate-skills/skills/autonomous-run/SKILL.md").is_file()
    # The BOUNDARIES paragraph, not the file: see `prompt_boundaries` for the
    # measurement that moved this.
    boundaries = prompt_boundaries()
    for path in policy_forbidden_paths():
        assert path in boundaries, (
            f"the prompt's boundaries paragraph does not name the fence "
            f"{path!r} — a mention elsewhere in the prompt explains a fence, "
            "it does not state one")
    assert ".run-summary" in prompt, "the runner attributes the run from this file"


@pytest.mark.xfail(strict=True, reason=(
    "agentops-hy6o.18: the pack is nightgate-skills now, but cage/run.sh's "
    "skill-resolution pre-flight still greps the prompt for agentops-skills:, "
    "finds nothing, and skips the check without a log line. run.sh is a "
    "forbidden path for an agent; when it moves this cell XPASSes, which "
    "strict=True turns red so the marker comes off with the fix"))
def test_the_runners_skill_preflight_reads_the_skill_the_prompt_invokes():
    """run.sh refuses a run whose prompt names a skill the installed pack does
    not serve, but only for a skill its own grep finds. A pattern naming a
    pack the prompt no longer invokes finds nothing, and the pre-flight is
    then skipped silently: the run starts on faith with no log line saying so."""
    runner = (ROOT / "cage" / "run.sh").read_text()
    m = re.search(r"PROMPT_SKILL=\$\(grep -oE '([^']+)'", runner)
    assert m, "run.sh no longer resolves the prompt's skill with grep -oE"
    found = re.search(m.group(1), SELF_PROMPT.read_text())
    assert found and found.group(0) == "nightgate-skills:autonomous-run", (
        f"run.sh's pre-flight pattern {m.group(1)!r} does not find the skill "
        "the self-cage prompt invokes")


def test_the_prompt_names_the_self_cage_source_as_off_limits():
    """The policy, the toml and the prompt all name `.cage/` (see
    test_the_self_cage_source_is_fenced_and_tiered_gate_surface below); this
    pins the prompt's own sentences, because the
    prompt is the only fence that binds a session whose skill failed to load."""
    prompt = SELF_PROMPT.read_text()
    assert "never edit .cage/" in prompt
    assert "never run `cage enroll`" in prompt


def test_the_self_cage_earns_the_badge_beside_a_consumers_cage(tmp_path):
    """A machine where `../cage/` holds the consumer's cage, so the
    platform's own goes in `../<checkout>-cage/`. The REAL source files, with
    only the two `~`-relative paths re-pointed at the fixture, earn
    N-01..N-03 there, which holds only if discovery does not stop at the
    consumer's cage."""
    root = make_repo(tmp_path / "agentops", 1)
    consumer = tmp_path / "cage"
    consumer.mkdir()
    (consumer / "cage.toml").write_text(
        f'[project]\nlive_checkout = "{tmp_path / "distill"}"\n'
        '[gate]\nforbidden_paths = [".warden/"]\n')
    (consumer / "prompt.md").write_text("theirs\n")

    installed = tmp_path / "agentops-cage"
    installed.mkdir()
    src = SELF_TOML.read_text()
    src = re.sub(r'^live_checkout = ".*"$', f'live_checkout = "{root}"', src, flags=re.M)
    src = re.sub(r'^worktree = ".*"$', f'worktree = "{tmp_path / "agentops-caged"}"',
                 src, flags=re.M)
    (installed / "cage.toml").write_text(src)
    (installed / "prompt.md").write_text(SELF_PROMPT.read_text())
    # the copy is what enroll would render from: it must still load, and
    # resolve its cage dir to where it was copied
    copied = cage_config.load(installed / "cage.toml")
    assert copied.cage_dir == installed

    badge = certify_mod.run(root)["capabilities"]["autonomous"]
    assert badge["earned"] is True, badge
    n01 = [c for c in badge["checks"] if c["id"] == "N-01"][0]
    assert "agentops-cage" in n01["detail"]
    n02 = [c for c in badge["checks"] if c["id"] == "N-02"][0]
    assert n02["detail"] == f"{len(copied.forbidden_paths)} forbidden path pattern(s) declared"


def test_the_self_cage_source_is_fenced_and_tiered_gate_surface():
    """The self-cage source is fenced and tiered, both halves in one place.

    `.cage/cage.toml` + `.cage/prompt.md` declare the fences and the prompt of
    the platform's own unattended runner, so the hand-authored prompt cannot
    be the only thing that says so: the policy's Never-edit list must name
    `.cage/` (and the toml with it, to keep the lockstep test above), and
    repo.yaml must carry a glob for it, or `warden explain` classifies both
    files LOW by default — the tier a docs page gets.

    The lockstep test above holds the fence in both directions once both
    files name it. What this adds is the TIER, which the lockstep does not
    hold: a gate-surface path whose risk tier is the unmatched default is
    exactly the drift `warden declare check` D-04 exists to refuse, and this
    is its instance.
    """
    from warden import config as config_mod

    assert ".cage/" in policy_forbidden_paths(), (
        "the policy's Never-edit paragraph does not name the self-cage source")
    cfg = config_mod.load(ROOT)
    for path in (".cage/cage.toml", ".cage/prompt.md"):
        tier = config_mod.classify(cfg, path)
        assert tier.glob != "<default>", (
            f"{path} is gate surface with no risk_tiers glob — it classifies "
            "by the unmatched default, which leaves the gate surface untiered")
        assert tier.tier == "HIGH", f"{path} classifies {tier.tier}, not HIGH"
        assert tier.reason, f"{path}'s tier carries no reason"


def test_the_runner_reads_the_diff_quoting_free_so_a_raw_probe_is_what_grep_sees(
        cfg):
    """The lockstep test above feeds BARE probe lines by design — it tests the
    regex against the policy, not git's encoding. That is only the runner's
    reality if the runner's listing is quoting-free: under the default
    `core.quotepath`, `git diff --name-only` wraps any non-ASCII path in
    double quotes and the `^(` anchor never matches it. So two things are
    pinned here: the runner reads the diff with `-z`, and the raw spelling of
    a non-ASCII or double-quoted name under every fenced directory hits the
    regex while the C-quoted spelling git emits without `-z` does not — which
    is why the read, not the regex, is the fix."""
    runner = (ROOT / "cage" / "run.sh").read_text()
    assert "diff origin/main...HEAD --name-only -z" in runner, (
        "run.sh's forbidden-path post-check does not read the listing with -z")
    dirs = [p for p in policy_forbidden_paths() if p.endswith("/")]
    assert dirs, "the policy fences no directory"
    raw = [d + name for d in dirs for name in ("naïve.md", 'we"ird.md')]
    assert sorted(grep_e(cfg.forbidden_regex, raw)) == sorted(raw), (
        "a raw non-ASCII or double-quoted name under a fenced directory "
        "does not hit the regex")
    quoted = ['"' + p.replace('"', '\\"') + '"' for p in raw]
    assert grep_e(cfg.forbidden_regex, quoted) == [], (
        "a C-quoted line matched the anchored regex — then the read was never "
        "the defect and this test's premise is wrong")


def test_the_prompt_points_a_run_at_the_work_it_may_take():
    """DISCOVER has a shortlist here, and the prompt has to name it.

    The fences do not close the repo, and the tracker labels the open items
    a run may take. The prompt once told the session most of the backlog
    was forbidden and that "nothing eligible" was the expected answer; a
    session reading that stops without looking, which is the one failure an
    unattended run cannot report on itself. So four things are pinned: the
    label, a `bd` span whose SUBCOMMAND is one bd really has, the absence of
    the claim that made the label pointless, and the two hedges the label
    needs. What is NOT pinned here is whether the surfaces the prompt hands
    over survive the fence — that relation has its own test below.
    """
    # the prompt is hand-wrapped at 72 columns, so any phrase pinned here can
    # straddle a newline — read it as the session does, one flow of words.
    prompt = " ".join(SELF_PROMPT.read_text().split())
    assert "cage-eligible" in prompt, (
        "the prompt does not name the label the tracker puts on the items a "
        "run here may take")
    cmd = re.search(r"`bd ([a-z][a-z-]*)[^`]*--label cage-eligible[^`]*`",
                    prompt)
    assert cmd, (
        "the prompt names no `bd` command that lists the label — a session "
        "cannot act on a shortlist it has no way to read")
    # The span alone is not the claim. `bd frobnicate --label cage-eligible`
    # satisfied the pattern above and lists nothing, so the subcommand is
    # held against the two that read a labelled set. Hand-maintained on
    # purpose: bd is not a dependency of this suite and CI has no bd to
    # interrogate, so this set is the honest width of the check — it catches
    # a subcommand that does not exist, not a flag bd later renames.
    assert cmd.group(1) in {"ready", "list"}, (
        f"the prompt's shortlist command is `bd {cmd.group(1)} ...`, which is "
        "not a bd subcommand that lists issues — a session following it reads "
        "no shortlist and the label might as well not exist")
    # Case-insensitive, and wider than the retired sentence: the claim
    # escapes a literal guard on wording, not only on case. The tracker has
    # stated the same thing as "most of what this repo's own work
    # touches — and the backlog is short", which the first spelling of this
    # guard (`most of the [^.]{0,40}backlog`) did not match at all.
    stale = re.search(
        r"(?:most|mostly|much)[^.]{0,80}backlog"
        r"|backlog[^.]{0,90}(?:forbid|skip|ineligible)", prompt, re.I)
    assert not stale, (
        f"the prompt still says {stale.group(0)!r} is work the cage forbids; "
        "it is not, and that sentence is what stops a run before it looks")
    assert "a hint, not a guarantee" in prompt, (
        "the prompt does not say the label is only a hint — it is a tracker "
        "convention, and a labelled item can still be ineligible once its "
        "real diff is known")
    # The second hedge, and it is not the same as the first. A path check
    # cannot see the policy's `## Autonomy scope` exclusions: a labelled item
    # that bumps the pack version, or one that states a `platform.pin`
    # upgrade path, is release mechanics, which are never eligible here whatever paths they
    # touch. So the prompt has to send the session somewhere a file list
    # cannot take it.
    assert "never a grant" in prompt, (
        "the prompt presents the label as a grant — it is a tracker "
        "convention, and an item can carry it and still be out of scope")
    assert re.search(r"release mechanics.{0,160}whatever paths", prompt), (
        "the prompt does not say the `## Autonomy scope` exclusions bind "
        "whatever paths the work touches — so its pre-flight reads as a path "
        "check alone, and a labelled release-mechanics item passes it")
    assert "nothing eligible" in prompt, (
        "'nothing eligible' is still an honest outcome; the prompt must keep "
        "telling the session what to do with it")


def test_the_toml_does_not_restate_the_prior_the_prompt_dropped():
    """The stale prior had three copies, and the toml's is the one arguing.

    `.cage/prompt.md` and `docs/wiki/The-Cage.md` both said most of this
    repo's backlog was work the cage forbids. The third copy sat above
    `[triggers.manual]`, where the claim is not description but the stated
    REASON no clock fires this cage — so a reader weighing a schedule, or a
    session reading the source of its own fences, meets a premise the
    tracker has since falsified. It is falsified by construction, not just
    by the label: `forbidden_regex` anchors the alternation at the path
    root, so the fences reach `.warden/` and `repo.yaml` at the REPO root
    and leave `warden/`, `tests/`, `docs/`, `skills/`, `scripts/` and
    `examples/` whole.
    The honest reason to stay manual is that no run has happened here yet.
    """
    toml = " ".join(SELF_TOML.read_text().split())
    stale = re.search(r"backlog[^.]{0,90}(?:forbid|skip|ineligible)", toml, re.I)
    assert not stale, (
        f"the toml still argues {stale.group(0)!r} — the claim the prompt "
        "dropped, restated where it is the reason for having no schedule")
    stale = re.search(
        r"most(?:ly)?[^.]{0,60}(?:gate-surface|the cage forbids)", toml, re.I)
    assert not stale, (
        f"the toml still says {stale.group(0)!r}; the fences are anchored at "
        "the path root, so they close less than the gate surface, not more")


def test_a_path_name_is_that_path_and_not_a_longer_one():
    """`names_path`'s edges, driven on the prompt's OWN spellings.

    `_PATH_EDGE` is the one guard in this module the mutation harness cannot
    sweep — it is a regex SOURCE fragment rather than a pattern, and
    `gm.alternatives` refuses a str — so its members are pinned here instead,
    each case a string the real `.cage/prompt.md` contains today. Both
    directions, because both have already been wrong: the first spelling
    required a backtick and could not see the prompt's own bare prohibitions
    (round 1, fail-closed), and terminated a name on `[ ,.]` only, so it
    could not see the real `.claude/` entry the paragraph ends with `;`
    (round 1, pattern-fit).

    ONE CASE PER CLASS MEMBER, and that is round 2's finding on the first
    version of this test: it drove two of `_PATH_EDGE`'s seven members, and
    five — `A-Z`, `0-9`, `_`, `/` and `-` — could be deleted from the class
    with this test and the whole module green, while the registry row next
    door said the members were "pinned here instead". A covering test that
    covers two sevenths of what it names is the same defect this branch is
    about. Each case below names the member it drives, so deleting that
    member makes a name that should NOT match start matching.
    """
    boundaries = prompt_boundaries()
    prompt = " ".join(SELF_PROMPT.read_text().split())
    for text, name, want, why in [
        (boundaries, ".claude/", True,
         "the paragraph's last fence entry, terminated by `;`"),
        (boundaries, ".warden/", True, "a fence entry, bare and comma-ended"),
        (boundaries, "warden/", False,
         "`.` — `.warden/` and `warden/certification/` are OTHER names, and "
         "the fence binds at the repo root"),
        (boundaries, "tests/", False, "no fence entry names it"),
        (prompt, "examples/", True, "backticks are an edge like any other"),
        ("never touch anything under examples/ on this run", "examples/",
         True, "the prompt's own prohibitions carry no backticks"),
        ("run `./scripts/portability-sim.sh` too", "scripts/", False,
         "`a-z` — a file under the directory is not the directory"),
        ("examples/hello-svc/tests/ is the fixture's own", "tests/", False,
         "`/` — a nested `tests/` is not the repo-root one the fence names"),
        ("integration-tests/ lives elsewhere", "tests/", False,
         "`-` — a directory whose name merely ENDS in the offered one"),
        ("docs/WikiPage elsewhere", "docs/Wiki", False,
         "`A-Z` — a capital continues a path name"),
        ("tests/2 fixtures", "tests/", False,
         "`0-9` — a digit continues a path name"),
        ("tests/_helpers is nested", "tests/", False,
         "`_` — an underscore continues a path name"),
    ]:
        assert names_path(text, name) is want, (
            f"names_path({text[:60]!r}, {name!r}) is {not want} and should be "
            f"{want}: {why}")


def test_a_refusal_joined_by_an_em_dash_is_not_cut_in_half():
    """The scan's unit must not shrink on an em dash (round 2, fail-closed).

    The repair that closed round 1's exemption hole opened a smaller one:
    it split on ` — ` as well as `". "`, so "`examples/` — never touch it"
    put the name in one chunk and the refusal in the next and
    `offer_contradictions` saw neither. Em-dash joining is the prompt's own
    house style, and the tell was that two `PROHIBITION_PLANTS` rows had to
    be reworded off it to keep witnessing anything.

    Driven on the three joiners the prompt actually uses, so the unit cannot
    quietly shrink on any of them again.
    """
    prompt = SELF_PROMPT.read_text().rstrip("\n")
    for joiner, refusal in [
        (" — ", "never touch it."),
        ("; ", "never touch it."),
        (", ", "though it is forbidden this run."),
    ]:
        flipped = offer_contradictions(
            f"{prompt}\nThe example consumer under `examples/`"
            f"{joiner}{refusal}\n")
        assert [name for name, _, _ in flipped] == ["examples/"], (
            f"a refusal joined to the name by {joiner!r} was not seen — the "
            "scan's unit has shrunk below the sentence, which is the one "
            "direction that hides a defect rather than costing a reword")


def test_the_toml_defers_to_the_prompt_about_what_a_run_may_take():
    """Wherever the toml names the `cage-eligible` label, it must POINT at
    `.cage/prompt.md` and carry the prompt's own hedge.

    The toml once read "What a run here may take is the tracker's
    `cage-eligible` shortlist", flat and unqualified, in the same commit that
    taught the prompt the label is "a hint, not a guarantee, and never a
    grant" — two documents in one product disagreeing about what permits
    work, with the flatter one being the file a session reads as the source
    of its own fences. The labelled set carries release-mechanics items the
    policy's `## Autonomy scope` excludes whatever paths they touch, so the
    hedge is not decoration.

    Pinned as a RELATION, not as wording: the toml may say what it likes
    about the label as long as the sentence that names it also names the
    document that qualifies it and repeats the qualification. Nothing else
    held this — the sibling guard above refuses one retired claim by regex,
    and a second flat restatement would have passed it.
    """
    for sentence in sentences(SELF_TOML.read_text()):
        if "cage-eligible" not in sentence:
            continue
        assert ".cage/prompt.md" in sentence, (
            f"the toml names the label in {sentence[:140]!r} without naming "
            "`.cage/prompt.md` — a second, flatter statement of what a run "
            "may take, beside the document that qualifies it")
        assert "never a grant" in sentence, (
            f"the toml names the label in {sentence[:140]!r} without the "
            "prompt's own hedge — the prompt calls it a hint and never a "
            "grant, and the toml restating it flatter is the disagreement "
            "this guard exists for")


# One PROBE per directory the prompt's eligibility sentence hands a session — a
# real path under it that a diff would carry. NOT the list of offered names:
# that is derived from the sentence by `offered_directories`, and the test below
# holds this dict's keys EQUAL to it, so a directory added to the offer with
# no probe here reddens as loudly as one dropped from the offer.
HANDED_OVER = {
    "warden/": "warden/certify.py",
    "tests/": "tests/test_self_cage.py",
    "docs/": "docs/wiki/The-Cage.md",
    "skills/": "skills/nightgate-skills/skills/deliver/SKILL.md",
    "scripts/": "scripts/portability-sim.sh",
    "examples/": "examples/hello-svc/hello_svc/app.py",
}


def offer_contradictions(prompt: str) -> list[tuple[str, str, str]]:
    """Every (directory, prohibition, sentence) where `prompt` offers a surface
    in its eligibility sentence and refuses it in any sentence of the file.

    A pure function of the text so the controls below can drive it with a
    planted sentence: the real prompt must report NOTHING, and appending any
    one of `PROHIBITION_PLANTS` to it must report `examples/`.

    NO CLAUSE IS EXEMPT, including the offer's own, and round 1 is why. The
    first version skipped the offer chunk — it then read "never eligible" and
    "do not close the repo", both honest there — and the cross-examiner
    measured that a refusal joined to the offer by `;`, ` — ` or `,` rode
    inside the skipped region. The prompt says "leave the rest of the repo
    open" instead, so the offer sentence carries no refusal vocabulary and
    needs no exemption, which is the fix a narrower splitter could not be:
    no splitter separates a comma-joined refusal from a comma-separated list
    of directories.

    THE BOUND, stated rather than implied. `PROHIBITION` is spellings, not
    negation: a rewrite that withdraws a directory in words it does not
    carry ("steer clear of `examples/`") passes this scan, and fails only
    the positive assertion, and only if it also drops the name from the
    offer sentence. What is pinned is the construction the prompt itself
    declares — one place a directory is offered, one place it is withheld.
    """
    offer = offer_sentence_of(prompt)
    found = []
    for name in HANDED_OVER:
        if not names_path(offer, name):
            continue
        for sentence in sentences(prompt):
            if not names_path(sentence, name):
                continue
            hit = PROHIBITION.search(sentence)
            if hit:
                found.append((name, hit.group(0), sentence))
    return found


def test_every_prohibition_spelling_is_planted():
    """`PROHIBITION_PLANTS` carries a plant per alternative of `PROHIBITION`,
    both directions — at the MUTATION HARNESS'S granularity, not a regex
    split's.

    The completeness witness the control table is pinned by (its
    `_SELF_PINNED_CONTROLS` row next door), and round 1 is why it reads
    `gm.alternatives` rather than `pattern.split("|")`. The split yields the
    seven top-level branches; the harness also deletes CHARACTER-CLASS
    members, so `\\boff[- ]limits\\b` is two more alternatives and the table
    carries a plant for each. Under the split, deleting the `off limits`
    plant left this green while the sweep next door reddened — a row the
    named witness did not pin, in the bucket whose whole claim is that it
    does (enforcement-truth).

    Each alternative is required to LOSE a plant when it is deleted, which is
    the same question the sweep asks and the only one that distinguishes two
    plants for one branch from two plants for two class members.
    """
    import guard_mutation as gm

    alternatives = gm.alternatives(PROHIBITION)
    assert len(alternatives) > 1, (
        "the refusal set has one alternative, so this is vacuous")
    for alternative in alternatives:
        lost = [p for p in PROHIBITION_PLANTS
                if PROHIBITION.search(p) and not alternative.mutated.search(p)]
        assert lost, (
            f"deleting {alternative.label} from PROHIBITION costs no plant in "
            "PROHIBITION_PLANTS, so nothing in this module reddens on it and "
            "the sweep next door reports it unpinned")
    for plant in PROHIBITION_PLANTS:
        assert PROHIBITION.search(plant), (
            f"{plant!r} plants no prohibition this guard reads — it witnesses "
            "no alternative and the sweep will report one unpinned")


@pytest.mark.parametrize("plant", PROHIBITION_PLANTS)
def test_a_planted_prohibition_reddens_the_polarity_half(plant):
    """One BEHAVIOURAL control per prohibition spelling.

    The real prompt reports no contradiction — that is the guard. Each plant
    appended to it must report one, which is what makes every alternative of
    `PROHIBITION` load-bearing rather than merely typed: delete the spelling
    and this parametrisation goes green while the prompt it is meant to
    protect could be flipped in that spelling unseen.
    """
    prompt = SELF_PROMPT.read_text()
    assert offer_contradictions(prompt) == [], (
        "the prompt as written already contradicts its own offer — the "
        "control below cannot mean anything until that is fixed")
    flipped = offer_contradictions(prompt.rstrip("\n") + "\n" + plant + "\n")
    assert [name for name, _, _ in flipped] == ["examples/"], (
        f"appending {plant!r} to the prompt is a refusal of a directory the "
        "offer sentence hands over, and the polarity scan did not see it")


def test_every_surface_the_prompt_hands_over_survives_the_fence(cfg):
    """The prompt's open list and the toml's fence list must not overlap.

    Round 1 of this branch's review found this relation unpinned. The prompt
    tells an unattended session which directories are "yours to take"; the
    toml declares what the runner's post-check closes; the lockstep test
    above holds the toml against the POLICY, and nothing held either against
    the prompt. So `scripts/` could be added to `[gate].forbidden_paths` and
    to the policy together, the lockstep test would follow both happily, and
    the prompt would go on handing `scripts/` to a run whose PR the
    post-check then closes — the session punished for doing what the only
    document binding it told it to do.

    The probe is a real path under each offered directory, matched the way
    `cage/run.sh` matches (`grep -E` over the anchored alternation), because
    the anchoring is the whole reason the two lists can differ: a fence name
    that binds at the repo root says nothing about the same name nested
    under `examples/`.

    THE MEMBERSHIP HALF IS POLARITY-AWARE, and it was not:
    it read `f"`{name}`" in prompt`, a substring test over the whole file
    that cannot tell an OFFER from a PROHIBITION. What replaces it reddens
    on four different flips — a name leaving the offer sentence, a name
    ARRIVING in it (see the bound below), a name appearing as a fence entry
    in the boundaries paragraph, and a name refused by any sentence of the
    file, which is the mutation that was measured ("NOT in scope — never
    touch anything under `examples/`").

    THE ADD DIRECTION IS NOT TOTAL OVER EVERY NAME, and this docstring said
    it was until a review measured the gap. Two assertions cover an arriving name and
    each is bounded: the fence loop is total over `policy_forbidden_paths()`
    in any spelling `names_path` reads — that is the one that matters, since
    no post-check closes an unfenced path — and
    `sorted(HANDED_OVER) == offered_directories(offer)` is total only over
    BACKTICKED tokens ending in `/`. So a directory the policy does not
    fence, written un-backticked, is added to the offer and both assertions
    stay silent: with "and fixtures/," spliced into the real offer sentence,
    `offered_directories` returns the same six names, the `HANDED_OVER`
    equality holds, `offer_contradictions` is `[]`, and `fixtures/` is in no
    fenced glob. The gap is narrow — `HANDED_OVER`'s probe table is not
    total over the offer — and it is stated here rather than claimed away.

    THE ADD DIRECTION IS HELD AGAINST THE FENCE, and that took two rounds
    to get right. Round 1 found the guard iterating `HANDED_OVER` alone, so
    a seventh name added to the offer was invisible. Round 2's repair
    derived the offered set by parsing backticked tokens — and round 2 then
    measured that the parse reads ONE spelling: an un-backticked `.github/`,
    or a backticked FILE like `repo.yaml`, was offered, closed by the
    runner's post-check, and green. The second is the fence-versus-tiers
    drift verbatim, which was about a FILE.

    So the complete assertion is the one that needs no parse at all: for
    every path the POLICY fences, the offer sentence must not name it, in
    ANY spelling `names_path` recognises. That is exactly the property the
    prompt claims ("a name is in one or the other") and it is total over the
    fence rather than over a token shape. `offered_directories` stays as the
    cheaper, narrower companion — it keeps the probe table honest for
    backticked directory tokens, and it is not what holds the fence.

    THE BOUND is stated in `offer_contradictions`, where the scan lives.
    """
    offer = prompt_offer_sentence()
    boundaries = prompt_boundaries()
    contradicted = offer_contradictions(SELF_PROMPT.read_text())
    assert contradicted == [], (
        "the prompt offers a directory and then refuses it: "
        + "; ".join(f"{name!r} — {said!r} in {sentence[:120]!r}"
                    for name, said, sentence in contradicted)
        + ". A session reading the prompt top to bottom gets two answers, "
        "and this is the flip the old substring check could not see")
    fenced = policy_forbidden_paths()
    assert fenced, "the policy fences nothing, so the assertion below is vacuous"
    for path in fenced:
        assert not names_path(offer, path), (
            f"the prompt's offer sentence hands a session {path!r}, which the "
            "policy's Never-edit list fences and the runner's post-check "
            "closes — a run that took the offer would have its PR closed for "
            "following the only document binding it")
    assert sorted(HANDED_OVER) == offered_directories(offer), (
        f"the prompt's offer sentence hands over {offered_directories(offer)} "
        f"and HANDED_OVER carries probes for {sorted(HANDED_OVER)}. Add or "
        "drop a probe to match — an offered directory with no probe is a "
        "surface handed to a run that nothing below checks against the fence")
    for name, probe in HANDED_OVER.items():
        assert not withholds(boundaries, name), (
            f"the prompt offers {name!r} in its eligibility sentence and "
            "withholds it in the boundaries paragraph — the two contradict, "
            "and the boundaries paragraph is the half that binds a session "
            "whose skill failed to load")
        assert grep_e(cfg.forbidden_regex, [probe]) == [], (
            f"the prompt hands a session {name!r} while the cage's own "
            f"post-check closes {probe!r}: a run that took the offer would "
            "have its PR closed for following the prompt")


def high_tier_globs() -> list[str]:
    """Every glob `repo.yaml` tiers HIGH, in declaration order.

    Derived, never transcribed: this is the ONE list the fence and the prompt
    are held against, so adding a HIGH glob to `repo.yaml` extends the
    requirement by construction rather than by someone remembering to.
    """
    cfg = repo_config.load(ROOT)
    globs = [t.glob for t in cfg.risk_tiers if t.tier == "HIGH"]
    assert globs, "repo.yaml declares no HIGH risk tier at all, so this is vacuous"
    return globs


def probe_for_glob(glob: str) -> str:
    """A repo-relative path `git diff --name-only` would carry for an edit
    under a `risk_tiers` glob. `**` is the only wildcard this repo's HIGH
    globs use; anything else is refused rather than guessed at, because a
    probe that silently missed its own glob would make the guard vacuous."""
    if glob.endswith("/**"):
        probe = glob[:-3] + "/probe.md"
    elif glob.endswith("/"):
        probe = glob + "probe.md"
    else:
        probe = glob
    assert "*" not in probe and "?" not in probe, (
        f"HIGH glob {glob!r} has a wildcard shape this probe builder does not "
        "handle — teach it the shape rather than letting the guard check a "
        "path the glob does not match")
    return probe


def prompt_fence_regex() -> str:
    """The fence the PROMPT states, rendered the way the runner renders the
    toml's: the policy paths the prompt's own boundaries paragraph names,
    as one start-anchored alternation.

    Built from the prompt rather than from the toml on purpose. The toml is
    the hard stop; the prompt is the only fence that binds a session whose
    skill failed to load, and the drift this guards is precisely the two
    disagreeing — so asking the toml twice would restate one answer and prove nothing.
    """
    boundaries = prompt_boundaries()
    named = [p for p in policy_forbidden_paths() if p in boundaries]
    assert named, "the prompt's boundaries paragraph fences nothing at all"
    return "^(" + "|".join(expected_pattern(p) for p in named) + ")"


def test_the_fence_and_the_prompt_close_every_high_tiered_path(cfg):
    """Every path `repo.yaml` tiers HIGH is closed by the runner's post-check
    AND named off-limits by the prompt.

    THE BUG THIS EXISTS FOR. `repo.yaml` tiered three globs HIGH under
    `warden/` and `[gate].forbidden_paths` closed NONE of them — the one
    `warden/` path it did close, `warden/certification/`, is tiered MEDIUM by
    the `warden/**` catch-all. `.cage/prompt.md` kept a SECOND, hand-written
    list of the reserved files and named two of the three.
    `warden/plugins.py` — "plugin loading = policy execution surface" —
    was therefore fenced nowhere and affirmatively handed to an
    unattended session by the one document binding it. A platform whose whole
    job is hard stops had a hard stop that missed a policy execution surface,
    and only the fact that `.cage/` had not yet been installed kept it
    theoretical.

    WHY IT IS A DERIVATION AND NOT A LIST. Three hand-kept copies of one set
    is how the drift happened, so this takes `repo.yaml`'s HIGH globs as the
    source and checks the other two against it. A fourth copy typed here
    would be the same defect wearing this test's name.

    ONE DIRECTION ONLY, and deliberately. HIGH ⊆ fence is asserted;
    fence ⊆ HIGH is not. The fence closes `warden/certification/`, which the
    `warden/**` catch-all tiers MEDIUM — a fence wider than the tiers costs a
    run some eligible work, a fence narrower than the tiers is the bug above.

    Matched with `grep -E` over the anchored alternation, exactly as
    `cage/run.sh` matches, because the anchoring is what lets the two lists
    differ: a fence name binding at the repo root says nothing about the same
    name nested under `examples/`.
    """
    repo_cfg = repo_config.load(ROOT)
    prompt_regex = prompt_fence_regex()
    for glob in high_tier_globs():
        probe = probe_for_glob(glob)
        tier = repo_config.classify(repo_cfg, probe)
        assert tier.tier == "HIGH", (
            f"the probe {probe!r} for HIGH glob {glob!r} classifies "
            f"{tier.tier} via {tier.glob!r} — an earlier glob shadows it, so "
            "either the probe or repo.yaml's glob order is wrong and this "
            "guard would be checking the wrong path")
        assert grep_e(cfg.forbidden_regex, [probe]) == [probe], (
            f"repo.yaml tiers {glob!r} HIGH and the cage's post-check does "
            f"NOT close {probe!r}: an unattended run could commit it and the "
            "PR would be left open. Add the pattern to the policy's "
            "Never-edit paragraph and to [gate].forbidden_paths")
        assert grep_e(prompt_regex, [probe]) == [probe], (
            f"repo.yaml tiers {glob!r} HIGH and `.cage/prompt.md` does NOT "
            f"tell a session to stay off {probe!r}. The prompt is the only "
            "fence a session whose skill failed to load ever sees — name the "
            "path in its boundaries paragraph")


def test_the_prompts_eligibility_sentence_keeps_no_second_fence_list():
    """The sentence that hands `warden/` over must POINT at the fence list,
    never restate it.

    The guard above proves the fence and the prompt cover every HIGH path
    today. What it cannot see is a prompt that re-grows its own copy of the
    `warden/` exceptions and lets that copy rot again — the copy was the
    defect, not any one missing name. So the offer sentence is held to naming
    no `warden/` subpath at all: `warden/` minus what the boundaries
    paragraph already names, and the reader follows the pointer.

    Scoped to the CLAUSE, not the paragraph, so the prose around it may
    still explain the bug by name (it does, and should).
    """
    offer = prompt_offer_sentence()
    reserved = [p for p in policy_forbidden_paths() if p.startswith("warden/")]
    assert reserved, "the policy fences no `warden/` path, so this is vacuous"
    for path in reserved:
        tail = path[len("warden/"):]
        for spelling in (path, tail):
            assert spelling not in offer, (
                f"the eligibility sentence names {spelling!r}, restating the "
                "fence list it should point at — that second copy is what let "
                "`warden/plugins.py` be fenced nowhere and offered here")
    assert "the fence list above already names" in offer, (
        "the eligibility sentence no longer points at the boundaries "
        "paragraph, so a session reading it is told `warden/` is open with no "
        "exception at all")




def autonomy_tree() -> "ast.Module":
    """`warden/autonomy.py` parsed once, for every walk below."""
    return ast.parse((ROOT / "warden" / "autonomy.py").read_text())


def autonomy_startswith_calls() -> "list[ast.Call]":
    """Every `<expr>.startswith(<arg>)` call node in `warden/autonomy.py`."""
    return [node for node in ast.walk(autonomy_tree())
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
            and node.args]


def autonomy_startswith_value(node: "ast.Call") -> str | None:
    """A `startswith` argument resolved to a string, or None.

    A bare literal, a module-level constant read off the imported module, or
    a `+` of either — the three spellings `warden/autonomy.py` uses. Anything
    else is left unresolved on purpose and lands in the pinned
    unresolvable set."""
    from warden import autonomy as autonomy_mod

    def value_of(inner) -> str | None:
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            return inner.value
        if isinstance(inner, ast.Name):
            got = getattr(autonomy_mod, inner.id, None)
            return got if isinstance(got, str) else None
        if isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Add):
            left, right = value_of(inner.left), value_of(inner.right)
            return None if left is None or right is None else left + right
        return None

    return value_of(node.args[0])


def carve_out_routed_prefixes() -> tuple[list[str], list[str]]:
    r"""What `warden autonomy carve-out` actually ROUTES a handed path on.

    Parsed out of `warden/autonomy.py` rather than restated: every
    `<expr>.startswith(<arg>)` in the MODULE, with `<arg>` resolved to a
    string where it can be — a module-level constant (`ATTEST_SUBDIR + "/"`)
    or a bare literal. Returns (resolved prefixes, unresolvable expressions).

    THE POINT, and the reason this is an AST walk and not a list: a guard
    whose subject is a hand-kept pair of constant names is the fourth copy of
    the set it claims to derive, and `tests-bite` is this repo's signature
    defect (round 1 filed exactly that against this guard's first version).

    WHOLE-MODULE, not `carve_out_problems` alone, and that is round 2's
    finding: scoping the walk to one function made it blind to the refactor
    this very commit performed elsewhere — lift the routing into a
    `_route(path)` helper and a fifth admitted store appears in neither list,
    so the docs assertion stays green over a widened fence and the
    `decide`-is-out pin goes vacuous. Reading the module costs a wider net,
    and the size of it was miscounted here twice — so the count is no
    longer prose. It is a
    canonical line the test below reads and re-measures, and a `startswith`
    added anywhere in `warden/autonomy.py` reddens it:

    WALK COST (`carve_out_problems` 5, module 8, extra 3)

    The three extra are —

      * `_committed_policy`'s `prefix.startswith("..")` traversal guard,
        which resolves to `".."` and therefore reaches neither assertion;
      * `_committed_pause_evidence`'s and `_committed_adopt_evidence`'s
        `path.startswith(BACKTEST_SUBDIR + "/")`, which DO resolve, DO begin
        `.warden/`, and DO land in the `admitted` set the docs-parity
        assertion reads.

    Those last two widen nothing TODAY only because `admitted` is a set and
    `carve_out_problems` already routes the same `.warden/memory/backtests/`
    prefix — a duplicate, not a fourth store. The earlier sentence here said
    the wider net reached neither assertion, which was wrong about the count
    and wrong about the reach, and an earlier draft than that one also said
    `_attest_shard_problem` has a call (it has none).

    That direction is still the safe one, and the duplicate is why it is
    worth saying precisely: a prefix the wider net picks up must be NAMED in
    the documents, never silently admitted, so an extra call that resolved
    to a store the documents do not carry would redden here rather than
    passing — which is the whole point of reading the module.

    WHAT IT STILL CANNOT SEE, said rather than implied: a prefix assembled at
    runtime, a membership test spelled some other way (`in`, `fnmatch`, a
    regex), or routing that lives in another module. The unresolvable-set
    assertion in the test below is what keeps the first of those from passing
    unseen; the other two would need a different walk, and nothing here
    claims to catch them.
    """
    resolved: list[str] = []
    unresolved: list[str] = []
    for node in autonomy_startswith_calls():
        got = autonomy_startswith_value(node)
        if got is None:
            unresolved.append(ast.unparse(node.args[0]))
        else:
            resolved.append(got)
    return resolved, unresolved


def test_every_warden_store_the_carve_out_routes_is_named_by_every_doc():
    r"""The carve-out's admitted `.warden/` stores, DERIVED FROM THE ROUTING.

    THE DRIFT THIS EXISTS FOR, and it is the same one
    `test_the_fence_and_the_prompt_close_every_high_tiered_path` above names:
    one set, several hand-kept copies. What `warden autonomy carve-out`
    admits is stated in `warden/autonomy.py`, in `.warden/skills-policy.md`'s
    carve-out paragraph, in `docs/wiki/The-Cage.md`'s carve-out section, in
    `docs/wiki/CLI-Reference.md`'s row for the command,
    `docs/wiki/Configuration.md`'s grant paragraph, and in the CLI's own
    success line. When a fourth admitted path was added, round 1 caught
    the CLI-Reference row by reading, and round 2 caught Configuration.md —
    which the guard did not then check — the same way. Both are checked now.

    So the CODE is the source and the three documents are the checked copies.
    The subject is `carve_out_routed_prefixes`, which parses the `startswith`
    branches out of `warden/autonomy.py` — the WHOLE module, for the reason
    the paragraph below repeats, and not `carve_out_problems` alone, which is
    what an earlier wording of this sentence said and what the two halves of
    this docstring used to disagree about. Round 1's
    `tests-bite` finding was that an earlier version of this guard named the
    two constants itself, which made it a fourth hand-kept list unable to see
    a fifth store added under any other name.

    The unresolvable-expression assertion is the other half: the rules arm
    routes on `rules_prefix`, a value that comes from the consumer's
    `repo.yaml` and has no fixed spelling, so it cannot be checked against a
    document. Pinning the unresolvable set to exactly those names means a
    NEW branch keyed on anything this walk cannot read goes red here rather
    than passing unseen.

    THE SUBJECT IS THE WHOLE MODULE (round 2, `tests-bite`). Scoped to
    `carve_out_problems`, the walk missed routing lifted into a helper — the
    refactor this same commit applied to the shard pass — so a fifth store
    would have been admitted with the guard green. `carve_out_routed_prefixes`
    says what the wider net still cannot see.

    ONE DIRECTION ONLY, deliberately, exactly as the HIGH-tier guard is: code ⊆
    docs is asserted, docs ⊆ code is not. The documents legitimately name
    stores they REFUSE (`.warden/memory/decide/`, `.warden/certification.yaml`,
    `.warden/memory/tags.yaml`), and a document that over-states the fence
    costs a reader caution, where one that under-states it costs the fence.
    """
    routed, unresolved = carve_out_routed_prefixes()
    assert sorted(set(unresolved)) == ["rules_prefix"], (
        "`warden/autonomy.py` routes on a `startswith` argument this guard "
        f"cannot resolve to a string: {sorted(set(unresolved))}. Only "
        "`rules_prefix` may be unresolvable (it is the consumer's declared "
        "rules dir, which has no fixed spelling). Anything else is a routed "
        "prefix no document can be checked against")
    admitted = sorted({p.rstrip("/") for p in routed
                       if p.startswith(".warden/")})
    assert admitted, "no `.warden/` store is routed — the walk read nothing"

    policy = POLICY.read_text()
    cage_page = (ROOT / "docs" / "wiki" / "The-Cage.md").read_text()
    cli_page = (ROOT / "docs" / "wiki" / "CLI-Reference.md").read_text()
    carve = re.search(r"\*\*Autonomy-ladder carve-out\*\*(.*?)\n## ",
                      policy, re.S)
    assert carve, "the policy has no autonomy-ladder carve-out paragraph"
    section = re.search(r"\n## The autonomy-ladder carve-out\n(.*?)\n## ",
                        cage_page, re.S)
    assert section, "The-Cage.md has no autonomy-ladder carve-out section"
    row = next((line for line in cli_page.splitlines()
                if line.startswith("| `autonomy carve-out`")), None)
    assert row, "CLI-Reference.md has no `autonomy carve-out` row"
    config_page = (ROOT / "docs" / "wiki" / "Configuration.md").read_text()
    # From the grant marker to the fenced example that follows it, which is
    # where the prose about the grant ends on that page.
    grant = re.search(r"`\[gate\]\.autonomy_carve_out = true`(.*?)\n```",
                      config_page, re.S)
    assert grant, "Configuration.md has no autonomy_carve_out grant paragraph"
    copies = {".warden/skills-policy.md's carve-out paragraph": carve.group(1),
              "docs/wiki/The-Cage.md's carve-out section": section.group(1),
              "docs/wiki/CLI-Reference.md's `autonomy carve-out` row": row,
              "docs/wiki/Configuration.md's grant paragraph": grant.group(1)}
    for store in admitted:
        for where, text in copies.items():
            assert store in text, (
                f"`warden autonomy carve-out` routes {store!r} into the "
                f"carve-out and {where} does not name it — the fence is wider "
                "than the document a reader is handed")
    # The one store ruled OUT by decision rather than omission, pinned
    # against the ROUTING so the ruling survives the next person who reads the
    # two `.warden/memory/` stores as a pair.
    assert not any(store.startswith(".warden/memory/decide")
                   for store in admitted), (
        "`.warden/memory/decide/` is routed into the carve-out, which is ruled "
        "out: a decision shard records what was RULED (policy the "
        "skills refer back to), not that a process ran, and no run is blocked "
        "without one")


def test_the_walks_docstring_states_the_cost_the_wider_net_pays():
    r"""The WALK COST line, re-measured against `warden/autonomy.py`.

    Why it is a test rather than a careful edit: the sentence it replaces was
    wrong twice in a row, in a docstring whose
    whole subject is "do not hand-keep what you can derive". It said the
    whole-module walk cost exactly one extra `startswith` call and reached
    neither assertion; it costs three, and two of them resolve under
    `.warden/` and DO reach the docs-parity assertion above.

    Three claims are pinned, all of them measured here: the counts, which
    helper functions the two reaching calls live in, and that the third
    extra call reaches nothing. The counts redden on any `startswith` added
    to the module, which is the intended cost — the docstring is the reader's
    account of what the wider net picks up, and a new call changes it.
    """
    doc = carve_out_routed_prefixes.__doc__
    stated = re.search(r"WALK COST \(`carve_out_problems` (\d+), "
                       r"module (\d+), extra (\d+)\)", doc)
    assert stated, (
        "`carve_out_routed_prefixes`' docstring carries no canonical WALK "
        "COST line — it is the ONE spelling this guard reads, so a count "
        "written any other way is prose again")
    said = tuple(int(g) for g in stated.groups())

    tree = autonomy_tree()
    calls = autonomy_startswith_calls()
    narrow = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "carve_out_problems"), None)
    assert narrow, "`warden/autonomy.py` declares no `carve_out_problems`"
    inside = [n for n in calls if narrow.lineno <= n.lineno <= narrow.end_lineno]
    measured = (len(inside), len(calls), len(calls) - len(inside))
    assert said == measured, (
        f"the docstring's WALK COST says {said} (carve_out_problems, module, "
        f"extra) and `warden/autonomy.py` measures {measured} — the count is "
        "the docstring's claim about how much the wider net picks up, and it "
        "has now been wrong twice")

    extra = [n for n in calls if n not in inside]
    holders = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    def holder_of(node) -> str:
        for name, fn in holders.items():
            if fn.lineno <= node.lineno <= fn.end_lineno:
                return name
        raise AssertionError(f"no top-level function holds line {node.lineno}")

    reaching = {holder_of(n) for n in extra
                if (autonomy_startswith_value(n) or "").startswith(".warden/")}
    assert reaching == {"_committed_pause_evidence", "_committed_adopt_evidence"}, (
        f"the extra calls that resolve under `.warden/` live in {reaching} — "
        "the docstring names `_committed_pause_evidence` and "
        "`_committed_adopt_evidence`, and a third helper routing a "
        "`.warden/` prefix is a store the documents may not name")
    for name in reaching:
        assert f"`{name}`" in doc, (
            f"{name!r} routes a `.warden/` prefix into the walk and the "
            "docstring does not name it — the wider net's reach is exactly "
            "what the prose count misstated")
    silent = {holder_of(n) for n in extra} - reaching
    assert silent == {"_committed_policy"}, (
        f"the extra calls reaching neither assertion live in {silent}; the "
        "docstring accounts only for `_committed_policy`'s `\"..\"` traversal "
        "guard")


def test_the_cage_declares_every_toolchain_the_enrollment_surface_runs():
    r"""What the caged PATH must carry, DERIVED from what the platform runs.

    THE FAILURE THIS EXISTS FOR is a real one, not a hypothetical: the first
    completed run of this cage (20260920-205434) built and reviewed a fix for
    27 minutes and then refused at ship step 1 with `go: command not found`.
    `tests/test_init.py` enrolls a fixture repo per language and runs the
    verify scope `warden init` wrote for it, so the suite shells out to every
    binary `enroll.verify_commands` can emit — and the runner resets PATH to a
    fixed list that Go's own install dir is not on. `verify --scope tests`
    was therefore red at EVERY head, which no session can clear, and weakening
    the test is forbidden.

    So the toolchain is DECLARED and this is the derivation: every binary the
    enrollment surface can run, for every language it supports, must be named
    in `[toolchain].require`. A fourth language added to `enroll.LANGUAGES`
    reddens here rather than costing another run its ship step.

    The direction is code -> toml only. Declaring MORE than the enrollment
    surface needs is a consumer's business (a project whose own tests need
    `dolt` declares it), and costs a skip; declaring less costs the run.
    """
    # Through the LOADER, never a regex over the toml text: a text parse keyed
    # on double quotes reads an empty list the moment someone normalises this
    # file to the single quotes its other lists use, and the guard then fails
    # on "names no binary" instead of on the property it is for.
    declared = set(cage_config.load(SELF_TOML).required_tools)
    assert declared, "[toolchain].require names no binary"

    # ONE derivation of "what program does this step run", shared with the
    # pyproject guard and with the gate's own diagnosis: `warden.verify.
    # first_binary`. A private
    # near-copy lived here and returned the first non-assignment token
    # UNCONDITIONALLY, so the moment `verify_commands` emits anything that is
    # not a bare invocation the two disagreed — and this one failed in the
    # confusing direction, quietly adding a word like `cd` to the set
    # cage.toml must declare and then failing on a binary that is not one.
    #
    # The canonical function DECLINES (returns None) for a path-bearing name,
    # a PATH-assigning prefix, a shell construct, a leading builtin and a
    # multi-line step, because "does this resolve on PATH" is meaningless for
    # those. An unparseable step is therefore stated, not skipped: this guard
    # cannot say what toolchain such a step needs, so it says so and fails
    # rather than reporting a clean derivation over a set with a hole in it.
    needed, unparseable = set(), []
    with tempfile.TemporaryDirectory() as probe:
        for lang in enroll_mod.LANGUAGES:
            for command in enroll_mod.verify_commands(Path(probe), lang):
                binary = verify_mod.first_binary(command)
                if binary is None:
                    unparseable.append(f"{lang.name}: {command!r}")
                else:
                    needed.add(binary)
    assert not unparseable, (
        "`warden.verify.first_binary` declines to name the program these "
        "enrollment verify steps run, so this guard cannot derive what the "
        "cage must declare and is not reporting a clean answer over a "
        "partial set:\n  " + "\n  ".join(unparseable))
    missing = sorted(needed - declared)
    assert not missing, (
        f"`warden init` writes verify steps that run {missing} and the cage "
        f"does not declare them in [toolchain].require (it declares "
        f"{sorted(declared)}). tests/test_init.py runs those steps for real, "
        "so an unattended run would find `verify --scope tests` red at every "
        "HEAD and refuse at ship step 1 — which has cost a whole completed "
        "run")


def test_the_go_toolchain_dir_is_declared_rather_than_assumed():
    """The half `require` cannot do.

    `require` makes an absent tool a named pre-flight SKIP. That turns a
    wasted hour into a cheap stop, and it does not make the run POSSIBLE —
    for that the binary has to be reachable, and `go` is not, because the
    runner's fixed PATH is `~/.local/bin`, homebrew, and the system dirs,
    while Go's own installer writes /usr/local/go/bin. So the directory is
    declared too, and the pair is the fix: supplied, and loud when it is not.
    """
    cfg = cage_config.load(SELF_TOML)
    assert "go" in cfg.required_tools
    assert cfg.toolchain_path, (
        "[toolchain].require names `go` but no [toolchain].path supplies it — "
        "every run would SKIP at pre-flight, which is honest and still no run")
    assert any(entry.rstrip("/").endswith("go/bin") for entry in cfg.toolchain_path), (
        f"no declared toolchain dir looks like a Go install dir: "
        f"{cfg.toolchain_path}")
    # NOT an `entry.startswith("/")` assertion: `cage.config.load` routes every
    # [toolchain].path entry through `_expand`, which refuses a relative one,
    # so by the time `cfg` exists every entry is absolute BY CONSTRUCTION and
    # the assertion could never fail. The loader's refusal is tested where it
    # lives, as the `relative-toolchain-path` mutation in tests/test_cage.py.
    for entry in cfg.toolchain_path:
        assert "/Users/" not in entry, (
            f"{entry!r} carries a real home directory into a tracked file "
            "(tests/test_docs.py::test_no_maintainer_home_path_is_tracked)")


def test_the_self_cage_grant_names_its_stores_and_names_only_real_ones():
    """This repo takes its own medicine: the grant is SCOPED.

    A bare `autonomy_carve_out = true` admits whatever the pinned platform
    admits, which moved under a granted consumer once already (the
    carve-out went from three `.warden/` writes to four). Written as a
    list it cannot: the runner clears only what both the platform's shape
    check and this list admit.

    ONE DIRECTION, like the guards above it: every store the toml names must
    be one the carve-out actually ROUTES, so a typo — which would silently
    admit nothing and refuse a run at 3am — is caught here. The reverse is
    NOT asserted, and that is the whole point of scoping: a fifth store the
    platform grows must NOT reach this grant on its own. It arrives as a
    named refusal in a run's report, and a human adds the line.
    """
    cfg = cage_config.load(SELF_TOML)
    assert cfg.autonomy_carve_out is True
    assert cfg.carve_out_stores, (
        "the self-cage grant is still the unscoped bool — a platform bump "
        "would widen a grant a consumer already gave, and "
        "this repo is the consumer it is easiest to check")
    routed, _unresolved = carve_out_routed_prefixes()
    # (path, problem). The path is root-anchored; the runner matches a DIFF
    # LINE, which is repo-relative, so it is made relative again here.
    rules_path, problem = repo_config.declared_rules_dir(ROOT)
    assert not problem, problem
    rules_dir = rules_path.relative_to(ROOT).as_posix().rstrip("/") + "/"
    admitted = {p for p in routed if p.startswith(".warden/")} | {rules_dir}
    for store in cfg.carve_out_stores:
        assert store in admitted, (
            f"[gate].autonomy_carve_out grants {store!r}, which "
            "`warden autonomy carve-out` never routes into the carve-out "
            f"(it routes {sorted(admitted)}). A grant that names nothing real "
            "admits nothing and refuses a run that should have stood")
    assert ".warden/memory/attest/" in cfg.carve_out_stores, (
        "the attestation store is not granted, so the runner's own corpus "
        "publication is refused on every cleared run and the PR fails "
        "`warden attest check` with NO ATTESTATION")
