"""The shipped starters must catch what they claim, and must not fall behind
the rules this repo adopted from them.

WHY THIS FILE EXISTS. A catalog starter is not documentation — it is the
artifact a consumer copies into `.warden/rules/`, and `warden rules recommend`
RUNS it against their tree to report the noise cost of adopting it. So a
starter that misses the dangerous spelling is wrong twice: the consumer who
adopts it gets a rule with a hole, and the consumer who is deciding gets a
hit count computed from the narrower pattern — an understated cost, which
reads as *more* confident than it is.

Widening this repo's own copy of a starter after judging the shipped pattern
fail-open does not widen the catalog: it keeps shipping the narrow one unless
something ties the two together. The gap is systematic rather than a
one-entry oversight, so every declarative starter is held to a corpus.

The two guards below are deliberately different in kind:

  KNOWN_DANGEROUS  pins each starter to the spellings it must catch and the
                   safe spellings it must not. This is the corpus that made
                   the gap measurable, kept so a future edit cannot quietly
                   narrow a pattern back.
  not-weaker-than  pins the RELATIONSHIP: an adopted rule may be wider than
                   the starter it came from (this repo's are), but never
                   narrower — that direction means the catalog is telling
                   consumers to enforce something we ourselves judged
                   insufficient.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from warden import rules as rules_mod
from warden.catalog import load_catalog
from warden.review import applicable

ROOT = Path(__file__).parent.parent
RULES_DIR = ROOT / ".warden" / "rules"

# (check id, [must fire], [must stay silent])
# A MIXTURE on purpose: some of these are spellings a narrow starter pattern
# misses and some are ones it already catches. Both kinds belong here — a
# probe that already passes is what stops a later edit narrowing a pattern
# back.
#
# NO COUNT IS STATED HERE, deliberately. A count nothing derives is a claim
# that rots: the first probe added without restating it makes it false.
#
# The silent column is what keeps a widening from becoming an overbroad
# pattern — the failure the catalog's own false-positive notes warn costs a
# gate its credibility.
KNOWN_DANGEROUS = {
    "sql-fstring": (
        ['cur.executemany(f"INSERT {t}", rows)', 'cur.execute(f"SELECT {x}")',
         'cur.executescript(f"DROP {t}")'],
        ['cur.execute("SELECT ?", (x,))'],
    ),
    "sql-concat": (
        ['cur.execute("SELECT %s" % v)', 'cur.execute("SELECT {}".format(v))',
         'cur.execute("SELECT " + v)',
         'cur.execute(base + "WHERE x=" + v)',
         # No string literal in the call at all.
         'cur.execute(base_query + user_where)',
         'cur.execute(TEMPLATE.format(table))',
         'cur.execute(QUERY % user_input)',
         # A COMMA INSIDE THE QUERY TEXT. Anchoring the scan at the first
         # comma to kill the parameter-arg false positives dropped coverage
         # from 6 of 8 to 2 of 8, because SQL text usually has a comma —
         # a worse hole than the noise it bought. Pinned so no future
         # anchoring attempt can repeat it quietly.
         'cur.execute("SELECT a, b FROM t " + x)',
         'cur.executemany("INS INTO t (a, b) VALUES " + v, rows)'],
        ['cur.execute("SELECT 1")', 'cur.execute("SELECT ?", (x,))',
         # KNOWN LIMIT, pinned as silent on purpose: the no-space form
         # `"..." %v` is not distinguishable by regex from a `%s` PLACEHOLDER
         # inside the string. Requiring whitespace after `%` is what keeps
         # every parameterized `"SELECT %s"` from firing, and that trade is
         # worth more than the rare no-space spelling.
         'cur.execute("SELECT %s" %v)',
         # A LIKE wildcard is the commonest legitimate `%` in SQL and must
         # never be read as concatenation.
         'cur.execute("SELECT * FROM t WHERE n LIKE \'a%\'")',
         'cur.execute("SELECT * FROM t WHERE n LIKE \'%foo%\'")',
         'conn.execute("UPDATE stats SET pct = 50%")',
         'cur.execute("SELECT %s", (val,))'],
    ),
    "os-system": (
        ['os.popen("rm " + p)', 'subprocess.getoutput("ls " + d)',
         'os.system("echo " + x)',
         # getstatusoutput is getoutput's sibling and shells out identically.
         'subprocess.getstatusoutput("ls " + d)', 'commands.getoutput("ls")'],
        ['os.path.join(a, b)', 'os.environ.get("HOME")'],
    ),
    "yaml-unsafe-load": (
        ['yaml.unsafe_load(fh)', 'yaml.full_load(fh)', 'yaml.load_all(fh)',
         'yaml.load(fh)'],
        ['yaml.safe_load(fh)', 'yaml.safe_load_all(fh)'],
    ),
    "inner-html-assignment": (
        ['el.outerHTML = x', 'el.insertAdjacentHTML("beforeend", x)',
         'document.write(x)', 'el.innerHTML = x',
         # The append form builds markup incrementally: `\s*=` alone demands
         # `=` where the next char is `+`.
         'el.innerHTML += userInput', 'el.outerHTML += x'],
        # A read-comparison is not a DOM write; `\s*=` alone would match the
        # first `=` of `===`.
        ['el.textContent = x', 'el.setAttribute("class", x)',
         'if (el.innerHTML === "") {'],
    ),
    "debug-enabled": (
        ['DEBUG = 1', 'DEBUG = True'],
        ['DEBUG = False', 'DEBUG = 0'],
    ),
    "tls-verification-off": (
        ['ctx.check_hostname = False', 'ssl_verify=False', 'verify=False',
         'verify = 0'],
        ['verify=True', 'ctx.check_hostname = True'],
    ),
    "broken-hash": (
        ['hashlib.new("md5")', 'hashlib.md5(x)', 'sha1(x)',
         # hashlib.new is case-insensitive at runtime, so "MD5" really works.
         'hashlib.new("MD5")',
         # Callable-reference and named-algorithm forms carry no `(` after
         # the name, so a branch that requires the `(` misses them.
         'hmac.new(key, msg, hashlib.md5)',
         'hashlib.pbkdf2_hmac("sha1", pw, salt, 100000)'],
        ['hashlib.sha256(x)', 'hashlib.new("sha256")',
         'hashlib.pbkdf2_hmac("sha256", pw, salt, 100000)'],
    ),
    "predictable-token-source": (
        ["''.join(random.choices(a, k=32))", 'random.randbytes(16)',
         'random.getrandbits(128)', 'random.choice(a)'],
        # shuffle/uniform are NOT token sources; flagging them buys noise.
        # `sample` was briefly added and REVERTED for the same reason — it is
        # the sampling primitive by name, and this rule's own body tells an
        # author to dismiss `random.*` used for sampling. Pinned silent so
        # the contradiction cannot be reintroduced quietly.
        ['secrets.token_hex(16)', 'random.shuffle(cases)',
         'random.uniform(0, 1)', 'random.sample(fixtures, 3)'],
    ),
    # pickle-loads, shell-true and dangerously-set-inner-html the sweep found
    # ALREADY adequate — pinned so a later edit cannot narrow them, not
    # because they were broken. except-pass below is NOT in that group: this
    # round changed it.
    "pickle-loads": (
        ['obj = pickle.loads(blob)', 'obj = pickle.load(fh)',
         'obj = marshal.loads(b)'],
        ['obj = json.loads(blob)', 'obj = tomllib.load(fh)'],
    ),
    "shell-true": (
        ['subprocess.run(cmd, shell=True)', 'subprocess.run(cmd, shell = True)'],
        ['subprocess.run(cmd, shell=False)', 'subprocess.run([exe, arg])'],
    ),
    "dangerously-set-inner-html": (
        ['<div dangerouslySetInnerHTML={{__html: x}} />'],
        ['<div>{x}</div>'],
    ),
    "except-pass": (
        # The single-line form is the win this round: the old pattern required
        # a newline after the colon and could not see it.
        ['    except Exception: pass', '    except Exception:\n        pass'],
        # `continue` was briefly added to this check and REVERTED: measured
        # over 722 CPython stdlib modules it added 54 matches, 48 of them
        # retry loops where the loop IS the handling (tempfile.mkstemp,
        # shutil.rmtree). Nothing is discarded there, so the entry's own
        # `guards` text does not cover it. Pinned silent so it is not
        # re-added without re-arguing it.
        ['    except Exception:\n        log.warning("x")',
         '    except FileExistsError:\n            continue'],
    ),
}


# Spellings a starter fires on that are NOT defects. Kept as a register, not
# hidden in the silent column, because a check whose known noise is written
# down can be argued with; one whose noise is quietly excluded cannot. Each
# entry must also be described in its catalog entry's `false_positive_cost`,
# which is what a consumer reads before adopting — asserted below.
# Phrases a starter's false_positive_cost must actually contain. Keyed to
# the registered noise above, so the disclosure cannot drift into generic
# prose that says nothing a consumer can act on.
_COST_MUST_MENTION = {
    "sql-concat": ("later argument", "inside the query", "coalesce", "prefix +"),
}

ACCEPTED_FALSE_POSITIVES = {
    "sql-concat": [
        # The operator belongs to a LATER argument. A line-based regex cannot
        # tell which argument it is in, and every attempt to anchor it trades
        # this noise for a bigger hole. Deciding this needs real parsing
        # (engine: python).
        'cur.execute("SELECT * FROM t LIMIT %s", (n + 1,))',
        'cur.execute(SQL_SELECT, {"pct": total % 100})',
        # Taken verbatim from a repo running this platform. Both are correct
        # parameterized psycopg: the first does SQL arithmetic INSIDE the
        # query text, the second builds a LIKE parameter and binds it. Round
        # 4 found them by running the shipped pattern over that repo — the
        # disclosure had named only the later-argument class.
        'cur.execute("update t set c = coalesce(c, 0) + %s where id = %s", (a, b))',
        'cur.execute("select id from t where id::text like %s", (prefix + "%",))',
    ],
}


def _starter_checks() -> dict[str, dict]:
    out = {}
    for entry in load_catalog():
        if entry.engine != "declarative":
            continue
        for check in (entry.starter or {}).get("checks", []):
            out[check["id"]] = {**check, "entry": entry.id}
    return out


def _flags(check: dict) -> int:
    import re
    bits = 0
    for f in check.get("flags", []):
        bits |= {"i": re.I, "m": re.M, "s": re.S}[f]
    return bits


CHECKS = _starter_checks()


def test_every_declarative_check_is_pinned_here():
    """A new starter check must arrive with its dangerous spellings.

    Without this, the corpus below silently stops covering the catalog and
    the guard degrades to whatever someone remembered to add.
    """
    unpinned = sorted(set(CHECKS) - set(KNOWN_DANGEROUS))
    assert not unpinned, (
        f"declarative starter checks with no dangerous-spelling corpus: "
        f"{unpinned} — add them to KNOWN_DANGEROUS")
    stale = sorted(set(KNOWN_DANGEROUS) - set(CHECKS))
    assert not stale, f"corpus names checks the catalog no longer ships: {stale}"


@pytest.mark.parametrize("check_id", sorted(KNOWN_DANGEROUS))
def test_starter_catches_its_dangerous_spellings(check_id):
    import re
    fires, _silent = KNOWN_DANGEROUS[check_id]
    pattern = re.compile(CHECKS[check_id]["pattern"], _flags(CHECKS[check_id]))
    missed = [s for s in fires if not pattern.search(s)]
    assert not missed, (
        f"catalog starter {check_id!r} ({CHECKS[check_id]['entry']}) misses "
        f"{missed} — a consumer adopting it gets a rule with that hole, and "
        f"`rules recommend` understates its cost by the same margin")


@pytest.mark.parametrize("check_id", sorted(KNOWN_DANGEROUS))
def test_starter_is_silent_on_the_safe_spellings(check_id):
    import re
    _fires, silent = KNOWN_DANGEROUS[check_id]
    pattern = re.compile(CHECKS[check_id]["pattern"], _flags(CHECKS[check_id]))
    wrong = [s for s in silent if pattern.search(s)]
    assert not wrong, (
        f"catalog starter {check_id!r} flags the SAFE spelling {wrong} — an "
        f"overbroad starter is the failure its own false-positive note warns "
        f"about, and it inflates the measured cost consumers decide on")


def _adopted_declarative_rules() -> Iterator[tuple[rules_mod.Rule, str]]:
    for rule in rules_mod.load_rules(RULES_DIR):
        if rule.engine != "declarative":
            continue
        for entry_id in rule.implements:
            yield rule, entry_id


def test_no_adopted_rule_is_narrower_than_the_starter_it_implements():
    """The relationship guard.

    A local rule may be WIDER than its starter. It must never be NARROWER:
    that means the catalog ships consumers a pattern we judged insufficient
    for ourselves.

    It reads the starter's PATTERN rather than comparing the rule against the
    corpus alone, so a rule narrowed to miss something the STARTER catches
    fails. And it walks the STARTER's checks, not the rule's, so a rule that
    DELETES a starter check entirely fails too. Missing either would be this
    repo's recurring "the guard is narrower than the thing it certifies"
    class, inside the guard written to stop drift.

    It compares them on the check-level dimensions: the PATTERN
    (behaviourally, over the shared corpus — every probe the starter matches,
    the rule must match too), plus `globs`, `scope` and `require`. The pattern
    comparison alone is not the invariant: adding
    `globs: ["does/not/exist/**"]` makes a check match nothing while every
    pattern comparison stays green.

    RULE-level narrowing (`applies_to`, `excludes`) is a separate axis and is
    covered by test_no_adopted_rule_is_scoped_to_nothing, because a rule can
    be reduced to a no-op without any check changing. Every starter check has
    a corpus entry, asserted unconditionally by
    test_every_declarative_check_is_pinned_here, so the comparison here cannot
    go vacuous by having nothing to probe with.
    """
    import re
    problems = []
    for rule, entry_id in _adopted_declarative_rules():
        starter = {c["id"]: c for c in CHECKS.values() if c["entry"] == entry_id}
        if not starter:
            continue                      # entry ships no declarative starter
        by_id = {c["id"]: c for c in rule.checks}
        for check_id, ref in starter.items():
            local = by_id.get(check_id)
            if local is None:
                problems.append(
                    f"{rule.id} implements {entry_id} but has NO check "
                    f"{check_id!r} — deleting a check is the maximally "
                    f"narrow change")
                continue
            fires, silent = KNOWN_DANGEROUS.get(check_id, ([], []))
            probes = list(fires) + list(silent)
            starter_pat = re.compile(ref["pattern"], _flags(ref))
            rule_pat = re.compile(local["pattern"], _flags(local))
            narrower = [s for s in probes
                        if starter_pat.search(s) and not rule_pat.search(s)]
            if narrower:
                problems.append(
                    f"{rule.id}/{check_id} misses {narrower}, which the "
                    f"{entry_id} STARTER catches")

            # Pattern is not the only dimension a check narrows on.
            # `globs: ["does/not/exist/**"]` makes a check match NOTHING
            # while every pattern comparison above stays green.
            # `scope` and `require` are the other two: file->added narrows the
            # surface, and require:true inverts the check's meaning outright.
            if local.get("globs") and not ref.get("globs"):
                problems.append(
                    f"{rule.id}/{check_id} adds globs {local['globs']} the "
                    f"{entry_id} starter does not have — that narrows the "
                    f"files it sees, invisibly to a pattern comparison")
            # DIRECTIONAL, not equality. `file` is strictly WIDER than
            # `added` — the catalog says so itself — so adopting it is a
            # hardening the contract explicitly permits. An equality form
            # would reject it under the message "narrower than the starter",
            # which is the opposite of what it is.
            if (ref.get("scope", "added") == "file"
                    and local.get("scope", "added") != "file"):
                problems.append(
                    f"{rule.id}/{check_id} scope 'added' is narrower than the "
                    f"starter's 'file' — it sees only lines the diff adds")
            if bool(local.get("require")) != bool(ref.get("require")):
                problems.append(
                    f"{rule.id}/{check_id} require "
                    f"{local.get('require')!r} != starter {ref.get('require')!r}"
                    f" — require:true inverts what the check means, so this is"
                    f" a different check, neither wider nor narrower")
    assert not problems, (
        "an adopted rule is narrower than the starter it implements:\n  "
        + "\n  ".join(problems))


def test_no_adopted_rule_is_scoped_to_nothing():
    """A rule can be narrowed to a no-op WITHOUT touching any check.

    The RULE-level equivalents of the check-level `globs` hole —
    `applies_to: ["does/not/exist/**"]` and `excludes: ["**"]` — each make a
    rule match nothing without changing any check, so the check-level guard
    cannot see them.

    Asserted through `applicable`, the function the real gate calls, over the
    WHOLE TRACKED TREE. Probing only warden/**.py and cage/**.py would
    false-positive on pinned-actions, which legitimately gates `.github/**` —
    the probe set has to cover every surface a rule may be scoped to, or the
    guard reports a live rule as dead.
    """
    # -z + NUL split, not whitespace: a tracked path containing a space would
    # be silently split into two nonexistent paths, which could mask a dead
    # rule while the count floor still passes. And a missing/failed git is
    # SKIPPED
    # rather than raised — no git means the probe is unavailable, not that the
    # rules are broken (a source tarball or `git archive` export has none).
    import shutil
    import subprocess
    if shutil.which("git") is None:
        pytest.skip("no git available — this probe needs a working copy")
    proc = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.skip(f"not a git working copy: {proc.stderr.strip()[:80]}")
    tracked = [f for f in proc.stdout.split("\0") if f]
    assert len(tracked) > 100, f"tree probe found only {len(tracked)} files"

    # EVERY declared rule, not just declarative ones that carry an
    # `implements:` link. `_adopted_declarative_rules` yields per `implements`
    # ENTRY, so driving this loop from it would never yield a rule with no
    # implements line, and deleting that line while scoping the rule to
    # nothing would pass green. `implements` has no bearing on whether a rule
    # gates anything; the schema makes it optional and shipped rules omit it.
    dead = []
    for rule in rules_mod.load_rules(RULES_DIR):
        hits = applicable((rule,), tracked)
        if not hits.get(rule.id):
            dead.append(
                f"{rule.id} gates NONE of {len(tracked)} tracked files — "
                f"applies_to={list(rule.applies_to)} excludes="
                f"{list(rule.excludes)}")
    assert not dead, "a rule is scoped to nothing:\n  " + "\n  ".join(dead)


def test_measurement_never_counts_this_corpus_as_real_hits():
    """This file quotes every pattern; the advisor must not scan it.

    `warden rules recommend` RUNS each starter against the tree to report a
    noise cost, and this file exists to hold the dangerous spellings — so
    leaving it in the scan inflates the measured hits of starters like
    os-command-injection and sql-injection with fixtures on this page. Those
    numbers are what a consumer weighs when deciding whether to adopt, so an
    inflated count is not cosmetic: it makes the platform's advice wrong in
    the direction of "this will be noisy".

    The `self-reference` class — a file whose job is to quote patterns is,
    by construction, a file the measurement must skip.
    """
    from warden import advisor

    assert "tests/test_catalog_starters.py" in advisor._SELF_REFERENTIAL, (
        "this corpus file is scanned by the measurement it exists to test — "
        "add it to advisor._SELF_REFERENTIAL")


def test_no_starter_measures_a_hit_inside_a_pattern_corpus_file():
    """No measured hit may come from a file whose PURPOSE is quoting patterns.

    Asserted over the SCAN SET, not over `measure`'s samples.
    `advisor.measure` caps `samples` at `_SAMPLES = 3`, so a corpus-file hit
    sorting fourth or later is invisible there, and `tests/` sorts after
    `cage/` and `examples/`: with the exclusion broken and four earlier hits
    under `cage/`, a starter drops out of a samples-based offender list
    entirely while its hit COUNT still includes the corpus file.

    Deliberately NOT "no hit anywhere under tests/". That form is itself
    overbroad — the class it exists to prevent. It would flag the ordinary
    assert-raises idiom (`raise AssertionError(...)` then `except X: pass`),
    which is a real match and correct code. A hit under tests/ needs
    judgment; a corpus file does not, because it quotes every pattern by
    construction.
    """
    from warden import advisor

    corpus_files = ("tests/test_catalog_starters.py",
                    "tests/test_adopted_rules.py")
    missing = [f for f in corpus_files if f not in advisor._SELF_REFERENTIAL]
    assert not missing, (
        f"pattern-corpus files still scanned by the measurement: {missing}")

    limits: list[str] = []
    targets = advisor._scan_targets(ROOT, catalog_file=advisor.CATALOG_PATH,
                                    limits=limits)
    assert targets, "the scan found nothing — this guard would be vacuous"
    scanned = {str(t) for t in targets}
    leaked = sorted(f for f in corpus_files
                    if any(s.endswith(f) or s == f for s in scanned))
    assert not leaked, (
        f"pattern-corpus files reached the measurement scan set: {leaked} — "
        f"every pattern they quote would be counted as a real hit and "
        f"inflate the cost consumers weigh")


# API names a message may claim only if its pattern actually matches them.
# The pairing is deliberately explicit: a message that enumerates spellings
# has to be re-checked when the pattern moves, and that is the whole point.
_MESSAGE_CLAIMS = {
    "os-system": ("os.system", "popen", "getoutput"),
    "inner-html-assignment": ("innerHTML", "outerHTML", "insertAdjacentHTML",
                              "document.write"),
    "pickle-loads": ("pickle", "marshal"),
    "yaml-unsafe-load": ("yaml.load", "unsafe_load", "full_load", "load_all"),
    # CALL SPELLINGS only. `md5`/`sha1` are algorithm names — naming the
    # algorithm is the point of the message, not a partial enumeration of
    # how it was invoked.
    "broken-hash": ("hashlib.new", "pbkdf2_hmac"),
    # sql-concat matches THREE mechanisms; naming only one told an author who
    # wrote `TEMPLATE.format(t)` that they had built SQL "by concatenation".
    "sql-concat": ("concatenation", "%", ".format"),
    # The rest name no API, which is always allowed — declared explicitly so
    # the completeness guard above can see them.
    "sql-fstring": (),
    "shell-true": (),
    "dangerously-set-inner-html": (),
    "debug-enabled": (),
    "tls-verification-off": ("verify", "check_hostname", "ssl_verify"),
    "predictable-token-source": (),
    "except-pass": (),
}


def test_every_declarative_check_declares_its_message_claims():
    """_MESSAGE_CLAIMS must cover every check, or it guards a subset.

    Without a completeness guard — KNOWN_DANGEROUS has one — a check absent
    from the table is unreachable: a partial message like `sql-concat`'s
    "built by concatenation", while the pattern also matches `%` and
    `.format(`, could never be caught. A check with no entry must be declared
    as claiming nothing, explicitly.
    """
    missing = sorted(set(CHECKS) - set(_MESSAGE_CLAIMS))
    assert not missing, (
        f"declarative checks absent from _MESSAGE_CLAIMS: {missing} — add "
        f"them, using an empty tuple for a message that names no API")
    stale = sorted(set(_MESSAGE_CLAIMS) - set(CHECKS))
    assert not stale, f"_MESSAGE_CLAIMS names checks the catalog no longer ships: {stale}"


@pytest.mark.parametrize("check_id", sorted(_MESSAGE_CLAIMS))
def test_message_names_all_the_apis_it_matches_or_none(check_id):
    """A message may be generic, or complete — never partial.

    Widening a pattern without touching its message can leave `os-system`
    matching `subprocess.getstatusoutput` while reporting "os.system()
    interpolates a shell string": an author is told to fix a call they did
    not write, and the finding reads as narrower than the check.

    THE TESTABLE INVARIANT is direction-sensitive. Asserting that every API
    named is matchable is backwards: that partial message satisfies it (it
    names os.system, and os.system does match). The defect is naming SOME of
    what you match. So: if a message names any API from the check's set, it
    must name them all. A generic message that names none is always fine,
    and is what these settled on.
    """
    message = CHECKS[check_id]["message"].lower()
    apis = _MESSAGE_CLAIMS[check_id]
    named = [a for a in apis if a.lower() in message]
    assert not named or len(named) == len(apis), (
        f"{check_id}'s message names {named} but its pattern also matches "
        f"{sorted(set(apis) - set(named))} — a partial enumeration tells the "
        f"author to fix a call they may not have written. Name all of them, "
        f"or describe the class generically.")


def test_accepted_false_positives_are_disclosed_in_the_catalog():
    """Known noise must be written where a consumer decides, not just in tests.

    A starter's `false_positive_cost` is what someone reads before adopting.
    Keeping a known false positive only in a test's silent column would hide
    the cost from exactly the person paying it.
    """
    import re
    entries = {e.id: e for e in load_catalog()}
    for check_id, samples in ACCEPTED_FALSE_POSITIVES.items():
        check = CHECKS[check_id]
        pattern = re.compile(check["pattern"], _flags(check))
        for sample in samples:
            assert pattern.search(sample), (
                f"{check_id} no longer fires on {sample!r} — it is registered "
                f"as an accepted false positive but has been fixed; move it "
                f"to the silent column")
        # Read the text, do not measure it. A length proxy cannot tell a real
        # disclosure from a one-line cost padded out with filler.
        cost = (entries[check["entry"]].false_positive_cost or "").lower()
        for marker in _COST_MUST_MENTION[check_id]:
            assert marker.lower() in cost, (
                f"{check['entry']}'s false_positive_cost does not mention "
                f"{marker!r}, so a consumer adopting it would not see the "
                f"noise registered in ACCEPTED_FALSE_POSITIVES")


def test_scan_skips_nested_checkouts(tmp_path):
    """A nested checkout must not be measured.

    A nested checkout is a full copy of a repo inside another one — a git
    worktree, a submodule, a vendored clone. Scanning into one measures the
    same corpus twice, and since starter hit counts are what the advisor
    recommends guardrails from, the error is silent and lands in the output
    rather than in a stack trace.

    `.git` in _SKIP_DIRS does not cover this: that entry matches a path PART,
    so it catches a normal clone (whose `.git` is a directory) and MISSES a
    git worktree (whose `.git` is a FILE). Parallel agent worktrees under
    .claude/worktrees/ have exactly that shape, and this repo's own cage runs
    its unattended loop inside a worktree.

    Both shapes are asserted, because covering only the directory case would
    leave the worktree shape scanned.
    """
    from warden import advisor as advisor_mod

    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("x = 1\n")
    # The fixture root is itself a checkout, because every real repo is.
    # Without this the `here != root` guard is uncovered: removing that guard
    # makes _scan_targets return [] for EVERY real repo, and a fixture root
    # with no `.git` would still pass. A test
    # claiming to guard this function must catch its most catastrophic mutant.
    (root / ".git").mkdir()

    # a worktree-shaped nested checkout: .git is a FILE
    wt = root / ".claude" / "worktrees" / "agent-1" / "src"
    wt.mkdir(parents=True)
    (wt / "app.py").write_text("x = 1\n")
    (wt.parent / ".git").write_text("gitdir: /elsewhere/.git/worktrees/agent-1\n")

    # a clone-shaped nested checkout: .git is a DIRECTORY
    clone = root / "vendor" / "dep" / "src"
    clone.mkdir(parents=True)
    (clone / "app.py").write_text("x = 1\n")
    (clone.parent / ".git").mkdir()

    limits: list[str] = []
    found = advisor_mod._scan_targets(root, limits=limits)
    rel = sorted(p.relative_to(root).as_posix() for p in found)
    assert rel == ["src/app.py"], (
        "the scan counted files inside a nested checkout — every starter's "
        f"hit count is then inflated by the number of copies present: {rel}")

    # And it must SAY so. A subtree vanishing from every hit count with no
    # line in the report is the same silent-measurement-change class as the
    # bug this fixes.
    disclosure = [ln for ln in limits if "nested checkouts" in ln]
    assert len(disclosure) == 1, f"nested exclusion not disclosed: {limits}"
    for expected in (".claude/worktrees/agent-1", "vendor/dep"):
        assert expected in disclosure[0], (
            f"the limits line does not name {expected}: {disclosure[0]}")

    # No nested checkouts -> no line. A limit that always fires is noise.
    plain = tmp_path / "plain"
    (plain / "src").mkdir(parents=True)
    (plain / "src" / "app.py").write_text("x = 1\n")
    (plain / ".git").mkdir()
    plain_limits: list[str] = []
    advisor_mod._scan_targets(plain, limits=plain_limits)
    assert not [ln for ln in plain_limits if "nested checkouts" in ln], \
        "the nested-checkout limit fired on a tree that has none"
