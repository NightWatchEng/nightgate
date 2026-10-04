"""Rule lifecycle: retire the dead, demote the noisy, narrow the over-broad.

The advisor only ever ADDS. A recommender with no subtractive half produces
rule debt, and rule debt is worse than a missing rule: a gate that fires
constantly on things nobody acts on trains every author to skim past it, which
silently disables the rules that DO matter. That damage is not local to the
noisy rule — it is paid by every other rule in the set.

Three chronic conditions, each measured from data the platform already records:

  - **never fires** — no judged finding, no gate detection, and (where the rule
    can be replayed) no flag over the commit window. Proposed for retirement
    WITH THE WINDOW STATED, because a silence claim without its window is
    unfalsifiable.
  - **always argued down** — `warden.memory` already tallies per-rule
    precision. A rule whose findings are near-always refuted or
    dismissed-with-reason is actively harmful and is proposed for demotion or
    narrowing, not kept for appearances.
  - **always fires** — a rule flagging most of the diffs it is selected on is a
    convention the repo has not adopted, or a scope error. The proposal is to
    narrow `applies_to`, never to delete.

**ACUTE vs CHRONIC.** `warden.autonomy.pause_candidates` handles the acute case:
three consecutive refuting review ROUNDS, pause now (a round, not a record —
counting records measures one reviewer's write order). This module handles the chronic one —
a rule argued down four times in five, forever, never has three in a row and
never pauses. The two are complementary reads of ONE corpus: everything here
comes from `warden.memory.stats`, and the confidence bound is
`warden.memory.wilson_lower_bound` applied to the complementary count. No second
metric is defined here, deliberately: a second copy of a metric is a second
thing to drift, and the one that drifts is always the copy nobody gates on.

**THE HARD CASE, stated plainly rather than bolted on as a caveat.** A rule that
has never fired may be the REASON the thing it guards has never happened.
Silence is consistent with two opposite worlds:

  - the guard is HOLDING — authors stopped writing the hazard because the rule
    is there. Its findings count is zero *by working*.
  - the guard is INERT — the pattern is stale, the glob no longer matches, the
    surface moved, nobody exercises it.

No count can separate those, because deterrence leaves no trace in a findings
table by definition. What this module can do is refuse to collapse them, name
which silence it is looking at (`never-selected` vs `evaluated-silent`), and
offer the one discriminator the data actually supports: for a rule that can be
replayed, whether the hazard it names is still PRESENT IN THE TREE. The
deterrence hypothesis predicts the hazard is absent from the code, not merely
unflagged; finding it sitting there unflagged argues the rule is broken, which
is a reason to fix it before proposing to retire it.

That last inference is narrower than it first looks, and the narrowing is
load-bearing. It holds only for a check the gate runs over a WHOLE FILE. A
`scope: added` check — the default — fires only on lines a diff adds, so
pre-existing matches are outside what it polices and argue neither way; and a
match count over zero eligible files is not a zero at all. Both are reported as
what they are rather than folded into an absence claim.

So every verdict here is a PROPOSAL and every proposal is a question. This
module writes nothing and edits nothing — there is no writer in it, by design.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

from . import backtest as backtest_mod
from . import memory as memory_mod
from . import rules as rules_mod

# A silence claim needs the rule to have been EXERCISED before it means
# anything. The bar is memory's own evidence bar for a precision claim, reused
# rather than re-chosen: below it, "this rule never fired" is a statement about
# the sample, not about the rule.
MIN_EXERCISED = memory_mod.PROMOTE_MIN_N

# Firing on more than half the diffs it is selected on is the over-broad
# signal. Deliberately a rate over SELECTED commits, not over the window: a
# correctly-scoped rule must not read as quiet merely because the repo has
# other directories.
ALWAYS_FIRES_RATE = 0.5

# Verdicts that propose an action. Each ends in '?' because a human decides it:
# a verdict that reads as a decision is how a report becomes an action nobody
# authorised.
PROPOSAL_VERDICTS = ("retire?", "demote?", "narrow?")


@dataclass(frozen=True)
class RuleLife:
    """One declared rule, read against both windows."""
    rule_id: str
    engine: str
    severity: str
    applies_to: tuple[str, ...]

    # --- corpus half: memory's numbers, carried, never recomputed here ---
    judged: int          # n judged findings (memory's _JUDGED_STATUSES)
    upheld: int          # confirmed + fixed
    refuted: int
    dismissed: int
    argued_down: int     # refuted + dismissed — the complementary count
    rate: float | None   # upheld / judged; None when nothing was judged —
    #                      0.0 there is a fabricated point estimate that sorts
    #                      as worst-precision
    wilson_lb: float     # memory's upheld-side floor, carried unchanged
    argued_down_lb: float  # the SAME estimator on the complementary count
    detections: int      # gate detections: a checker firing is a FACT, and it
    #                      is firing evidence even though memory keeps it out
    #                      of precision

    # --- history half: the replay window ---
    selected: int | None   # commits whose files applies_to admits; None=UNREAD
    flagged: int | None    # commits the rule would have fired on; None=not
    #                        replayable or history unread
    fire_rate: float | None
    replay_reason: str     # why flagged is None

    # --- the discriminator, for never-fired rules only ---
    tree_matches: int | None   # None whenever nothing was scanned — 0 here
    #                            would claim a clean tree nobody examined
    hazard: str            # present-in-the-tree (a whole-file check could have
    #                        fired on it) | present-in-lines-it-never-polices
    #                        (scope:added only) | absent-from-the-tree |
    #                        nothing-scanned | unscannable | not-examined

    silence: str           # "" | never-selected | evaluated-silent |
    #                        unobservable-firing | window-too-thin
    verdict: str
    proposal: str

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "engine": self.engine,
                "severity": self.severity, "applies_to": list(self.applies_to),
                "judged": self.judged, "upheld": self.upheld,
                "refuted": self.refuted, "dismissed": self.dismissed,
                "argued_down": self.argued_down, "rate": self.rate,
                "wilson_lb": self.wilson_lb,
                "argued_down_lb": self.argued_down_lb,
                "detections": self.detections, "selected": self.selected,
                "flagged": self.flagged, "fire_rate": self.fire_rate,
                "replay_reason": self.replay_reason,
                "tree_matches": self.tree_matches, "hazard": self.hazard,
                "silence": self.silence, "verdict": self.verdict,
                "proposal": self.proposal}


@dataclass(frozen=True)
class LifecycleReport:
    rows: tuple[RuleLife, ...] = ()
    commit_window: str = ""
    corpus_window: str = ""
    notes: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    # False when the ruleset could not be read: an empty report must never be
    # readable as "every rule is healthy".
    computed: bool = True

    def to_dict(self) -> dict:
        return {"schema": 1, "computed": self.computed,
                "commit_window": self.commit_window,
                "corpus_window": self.corpus_window,
                "rows": [r.to_dict() for r in self.rows],
                # null, not an empty map, when nothing was computed: a
                # consumer reading `proposals` alone must not get a clean bill
                # from a report that judged nothing.
                "proposals": ({v: [r.rule_id for r in self.rows
                                   if r.verdict == v]
                               for v in PROPOSAL_VERDICTS}
                              if self.computed else None),
                "notes": list(self.notes), "limits": list(self.limits)}


class _RuleAsEntry:
    """Adapter presenting a Rule with the two attributes the replay and the
    tree scan already read off a catalog entry (`engine`, `starter`).

    Written as an adapter rather than a copy of either routine on purpose: the
    gate's firing semantics — require:true excluded, flags, globs, scope — are
    subtle enough that a second implementation would diverge, and the number
    that matters here is "what the gate would have done".
    """

    def __init__(self, rule: rules_mod.Rule):
        self.engine = rule.engine
        self.starter = {"checks": [dict(c) for c in rule.checks]}


def _selected_files(rule: rules_mod.Rule,
                    files: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The (path, added) pairs this rule is selected on, using the gate's own
    selection — `applies_to` minus `excludes` — rather than a second reading
    of the same two fields."""
    paths = [p for p, _ in files]
    admitted = set(rules_mod.applicable((rule,), paths).get(rule.id, []))
    return [(p, t) for p, t in files if p in admitted]


def _runnable_checks(rule: rules_mod.Rule) -> list[dict]:
    """The checks that can stand in for the gate over ADDED lines.

    A `require: true` check fires when its pattern is ABSENT, which neither the
    replay nor the tree scan can judge — both already exclude it with a caveat
    and return a count. Counting that 0 as a firing record would call a rule
    dead on a replay that never ran it, so a rule with nothing else is reported
    unreplayable instead.
    """
    return [c for c in rule.checks if not c.get("require")]


class _PythonReplay:
    """Runs an engine:python rule's checker over past commits through the
    gate's own path, never a second implementation of it: `diffs.get_context`
    against the commit's first parent with the repo's `context_excludes`,
    `rules.applicable` over the files that context scans, and the registered
    checker called with the rule's own params. This is the replay
    docs/design/tests-accompany-logic-20260927.md §0 ran by hand.

    Selection is the gate's too: a commit counts when the files its diff
    SCANS — deletions included, `context_excludes` applied — hold one the
    rule admits. Selecting on added lines instead would miss a commit that
    only deletes (what `contract-freeze` fires on) and count one whose only
    admitted file the gate never shows the checker, and both would read as
    measured silence. The changed-path list is read first and the full diff
    only for a commit the rule admits; both are cached per commit and shared
    by every python rule.
    """

    def __init__(self, root: Path, rules_dir: Path,
                 context_excludes: tuple[str, ...]):
        self.root, self.rules_dir = root, rules_dir
        self.excludes = tuple(context_excludes)
        self._registry: dict | None = None
        self._registry_error = ""
        self._paths: dict = {}
        self._contexts: dict = {}

    def _checker(self, rule_id: str):
        from .plugins import load_registry

        if self._registry is None and not self._registry_error:
            try:
                self._registry = load_registry(self.rules_dir)
            except Exception as e:  # noqa: BLE001 — a registry that cannot
                # load leaves every python rule unmeasured, never silent.
                self._registry_error = str(e)[:200]
        if self._registry_error:
            return None, ("its checker registry could not be loaded ("
                          + self._registry_error + ")")
        if rule_id not in self._registry:
            return None, "no checker is registered under its id"
        return self._registry[rule_id], ""

    def _scanned(self, sha: str):
        """(first parent, the paths the gate would scan), or the reason
        there is no diff to read."""
        from . import diffs
        from .rules import glob_match

        if sha not in self._paths:
            try:
                parents = diffs.run_git(self.root, "rev-list", "--parents",
                                        "-n1", sha).split()[1:]
                if not parents:
                    self._paths[sha] = ("a root commit has no parent to diff "
                                        "against")
                else:
                    names = diffs.range_paths(self.root, parents[0], sha)
                    self._paths[sha] = (parents[0], [
                        n for n in names
                        if not any(glob_match(g, n) for g in self.excludes)])
            except diffs.DiffError as e:
                self._paths[sha] = f"git could not diff it ({str(e)[:160]})"
        return self._paths[sha]

    def _context(self, sha: str, parent: str):
        from . import diffs

        if sha not in self._contexts:
            try:
                self._contexts[sha] = diffs.get_context(self.root, parent, sha,
                                                        self.excludes)
            except diffs.DiffError as e:
                self._contexts[sha] = f"git could not diff it ({str(e)[:160]})"
        return self._contexts[sha]

    def replay(self, rule: rules_mod.Rule, commits, fallback_selected: int,
               caveats: list[str]) -> tuple[int, int | None, str]:
        """(selected, flagged, reason when flagged is None). A commit that
        could not be replayed is never counted silent: some of them make the
        count a floor, stated; all of them make it no count. With no count,
        `selected` is the added-lines selection every other engine reports."""
        check, reason = self._checker(rule.id)
        if check is None:
            return fallback_selected, None, reason
        selected = flagged = 0
        missed: list[tuple[str, str]] = []
        for sha, added in commits:
            scanned = self._scanned(sha)
            if isinstance(scanned, str):
                # No path list: whether the gate would have selected it is
                # unknown, so the added lines decide whether it is named.
                if _selected_files(rule, added):
                    missed.append((sha, scanned))
                continue
            if not rules_mod.applicable((rule,), scanned[1]):
                continue          # the rule admits none of the paths it scans
            scanned = self._context(sha, scanned[0])
            if isinstance(scanned, str):
                missed.append((sha, scanned))   # selected, then unreadable
                continue
            files = rules_mod.applicable((rule,), list(scanned.files)).get(
                rule.id, [])
            try:
                fired = bool(check(scanned, rule.params or {}, files))
            except Exception as e:  # noqa: BLE001 — a checker that raises on
                # an old commit measured nothing there; it did not pass it.
                missed.append((sha, f"its checker raised "
                                    f"{type(e).__name__}: {str(e)[:120]}"))
                continue
            selected += 1
            flagged += fired
        if missed:
            first = f"first: {missed[0][0][:10]}, {missed[0][1]}"
            if not selected:
                return len(missed), None, (
                    f"none of its {len(missed)} selected commit(s) could be "
                    f"replayed ({first})")
            caveats.append(
                f"{rule.id}: {len(missed)} commit(s) it may select could not "
                f"be replayed ({first}), so its flagged count is a floor over "
                f"{selected + len(missed)}")
        return selected + len(missed), flagged, ""


def _replay(rule: rules_mod.Rule, commits, caveats: list[str],
            python: _PythonReplay) -> tuple[int | None, int | None, str]:
    """(selected, flagged, reason). `flagged` is None when the rule cannot be
    replayed, with the reason — never 0, which would claim a measured silence.

    Selection is computed for EVERY engine (it is glob arithmetic, and it is
    what a narrowing proposal acts on); firing is measured where a regex, or
    the rule's own registered checker, can stand in for the gate. A replayed
    python rule's selection is the gate's (see `_PythonReplay`).
    """
    if commits is None:
        return None, None, "the commit history could not be read"
    selected_commits = []
    for sha, files in commits:
        admitted = _selected_files(rule, files)
        if admitted:
            selected_commits.append((sha, admitted))
    selected = len(selected_commits)
    if rule.engine == "claude":
        return selected, None, ("a judgment rule is not replayable as a regex — "
                                "re-running the judge over history is not "
                                "affordable here")
    if rule.engine == "python":
        return python.replay(rule, commits, selected, caveats)
    if not _runnable_checks(rule):
        return selected, None, ("every check it declares fires on ABSENCE "
                                "(require:true), which cannot be replayed over "
                                "added lines")
    flagged, replay_caveats = backtest_mod.flag_commits(_RuleAsEntry(rule),
                                                        selected_commits)
    caveats.extend(f"{rule.id}: {c}" for c in replay_caveats)
    return selected, len(flagged), ""


def _scannable(rule: rules_mod.Rule) -> bool:
    """Is there anything mechanical to look for this rule's hazard WITH? A
    static property of the rule, not a measurement — which is why a judgment
    rule, and a rule that only fires on absence, read `unscannable` whatever
    the verdict, rather than `0 matches`."""
    return rule.engine == "declarative" and bool(_runnable_checks(rule))


def _scan_tree(rule: rules_mod.Rule, root: Path, targets,
               caveats: list[str]) -> tuple[int, int]:
    """(total hits, hits a whole-file check could have fired on), counted the
    way THE GATE would count them.

    Deliberately not `advisor.measure`: that function measures a catalog
    starter's noise cost BEFORE adoption, where per-line waivers do not exist
    yet. This rule is adopted, so the gate's semantics include the reasoned
    `warden:allow(<check-id>)` markers `declarative.run` obeys — and a waived
    line is one the gate provably never fires on. Counting it would make the
    report call a rule BROKEN on a repo's own adjudicated carve-outs. The marker predicate and the pattern compiler are declarative's,
    imported, never re-implemented.

    Hits are attributed PER CHECK so scope can be judged per hit: a single
    total plus `any(check is scope:file)` would credit a whole-file check with
    matches only a scope:added check produced.
    """
    from . import declarative as decl
    from .rules import glob_match

    total = 0
    policed = 0
    for check in _runnable_checks(rule):
        pattern = decl.compile_check(dict(check), str(rule.path))
        scope = check.get("scope", "added")
        globs = check.get("globs") or []
        if scope == "added":
            caveats.append(
                f"{rule.id}: check {check['id']!r} is scope:added — it fires "
                "only on lines a diff ADDS, so its matches here are existing "
                "lines it never polices, not places it failed to fire")
        for path in targets:
            rel = path.relative_to(root).as_posix()
            if globs and not any(glob_match(g, rel) for g in globs):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as e:
                caveats.append(f"{rule.id}: {rel} unreadable ({e}) — not "
                               "scanned, so this count is a floor")
                continue
            lines = text.splitlines()
            for m in pattern.finditer(text):
                lineno = text.count("\n", 0, m.start()) + 1
                if decl._allowed(check["id"], lines, lineno):
                    continue          # the gate never fires here, by decision
                total += 1
                if scope != "added":
                    policed += 1
                if scope == "file":
                    break             # the gate yields one finding per file
    return total, policed


def _tree_hazard(rule: rules_mod.Rule, root: Path, targets,
                 caveats: list[str]) -> tuple[int | None, str]:
    """Does the hazard this rule names still exist in the tree?

    The one discriminator the data supports for a silent rule. `None` where
    nothing was, or could be, scanned — reporting 0 there would claim a clean
    tree nobody examined, which is the failure this whole module is built to
    refuse (a rule scoped to a suffix the scanner does not admit would
    measure `0` over ZERO files and print "absent from the tree").
    """
    if not _scannable(rule):
        return None, "unscannable"
    for check in rule.checks:
        if check.get("require"):
            caveats.append(
                f"{rule.id}: check {check['id']!r} uses require:true (it fires "
                "on ABSENCE) and cannot be counted by matching — excluded, so "
                "any absence claim below covers only the rule's other checks")
    rels = [p.relative_to(root).as_posix() for p in targets]
    admitted = set(rules_mod.applicable((rule,), rels).get(rule.id, []))
    scoped = [p for p in targets
              if p.relative_to(root).as_posix() in admitted]
    if not scoped:
        caveats.append(
            f"{rule.id}: no file the tree scan admits is inside its "
            "applies_to (the scan reads a fixed suffix set), so the hazard "
            "was looked for in ZERO files — not found is not absent")
        return None, "nothing-scanned"
    total, policed = _scan_tree(rule, root, targets=scoped, caveats=caveats)
    if not total:
        return 0, "absent-from-the-tree"
    # WHOSE lines are these? Only a whole-file check could have fired on what
    # is sitting in the tree; a scope:added check fires on lines a diff ADDS,
    # so its matches argue nothing about it either way.
    return total, ("present-in-the-tree" if policed
                   else "present-in-lines-it-never-polices")


def _corpus_window(records: list[dict], events: dict) -> str:
    age = memory_mod.corpus_age(records)
    return (f"the committed corpus: {age['count']} record(s), oldest "
            f"{age['oldest_days']}d, across {events.get('total', 0)} attested "
            f"review event(s)")


def rule_lifecycle(root: Path, *, records: list[dict] | None = None,
                   rules_dir: Path | None = None,
                   window: int | None = backtest_mod.DEFAULT_WINDOW,
                   commits=None, corpus_unread: str = "",
                   context_excludes: tuple[str, ...] = ()) -> LifecycleReport:
    """Read every declared rule against both windows and propose, never act.

    `commits` may be injected (tests, or a caller that already read git);
    otherwise the last `window` non-merge commits are read from local git. A
    history that cannot be read is a LIMIT, never a silence.
    `context_excludes` is repo.yaml's, so a python checker replays over the
    diff the gate would have scanned.
    """
    root = Path(root)
    if rules_dir is None:
        # The DECLARED dir, not a hardcoded default: a fallback here would
        # be another copy of the derivation `config.declared_rules_dir`
        # exists to be the only one of. An unreadable
        # declaration is an ABSENT report, same as an unreadable ruleset:
        # judging rules found at a guessed location is not a measurement.
        from .config import declared_rules_dir

        rules_dir, dir_problem = declared_rules_dir(root)
        if dir_problem:
            return LifecycleReport(
                notes=(f"the rules-dir declaration could not be read "
                       f"({dir_problem}), so NO lifecycle claim is made "
                       "about any rule — this is not a clean report, it is "
                       "an absent one",),
                computed=False)
    limits: list[str] = []
    notes: list[str] = []
    if records is None:
        # A caller that passes nothing has not shown this function an empty
        # corpus — it has shown it no corpus. Saying so keeps the two apart;
        # the CLI always passes records.
        records = []
        limits.append(
            "no corpus was passed to this call, so every rule below reads as "
            "having no judged history — that is the caller's silence, not the "
            "rule's")
    if corpus_unread:
        notes.append(
            "THE CORPUS WAS NOT READ (" + corpus_unread + ") — every rule below "
            "reads as having no judged history because nothing could be looked "
            "up, NOT because the history is empty. No silence claim here is a "
            "measurement.")

    try:
        declared = rules_mod.load_rules(rules_dir)
    except Exception as e:  # noqa: BLE001 — an unreadable ruleset must not
        # render as "every rule is healthy"; there is nothing to judge, and an
        # empty rows list would read as a clean bill of health.
        return LifecycleReport(
            notes=(f"the ruleset could not be read ({e}), so NO lifecycle "
                   "claim is made about any rule — this is not a clean "
                   "report, it is an absent one",),
            computed=False)

    if commits is None and window is not None:
        try:
            commits = backtest_mod.read_commit_additions(root, window)
        except backtest_mod.BacktestError as e:
            limits.append(f"the commit history could not be read ({e}) — the "
                          "firing window is UNREAD, and no rule below is called "
                          "silent on the strength of a window nobody could look "
                          "at")
    commit_window = (
        f"last {len(commits)} non-merge commit(s)" if commits is not None
        else "UNREAD — the commit history could not be read")

    doc = memory_mod.stats(root, rules_dir=rules_dir, records=records)
    per_rule = doc.get("per_rule", {})
    detections = doc.get("gate_detections", {})
    events = doc.get("review_events", {})
    corpus_window = _corpus_window(records, events)
    if doc.get("rules_error"):
        limits.append(f"memory could not read the ruleset ({doc['rules_error']})")
    # A corpus that holds no judged finding for any DECLARED rule cannot
    # support a silence claim about a particular one: the silence is the
    # corpus's. Restricted to declared ids because a grandfathered legacy id
    # names no rule, so its records say nothing about the ruleset's liveness.
    declared_ids = {r.id for r in declared}
    corpus_live = any(row.get("n", 0) for rid, row in per_rule.items()
                      if rid in declared_ids)
    # ...and a corpus-ONLY silence needs a denominator of its own. The replay
    # can say "selected 40 times, fired 0"; the corpus can only say "no record
    # in N attested reviews", which is worth nothing until N is large enough
    # to be evidence. Same bar, the review events standing in for the judged
    # findings a silent rule by definition does not have.
    corpus_events = events.get("total", 0)
    corpus_usable = corpus_live and corpus_events >= MIN_EXERCISED
    if corpus_live and not corpus_usable:
        limits.append(
            f"the corpus holds only {corpus_events} attested review event(s), "
            f"below the bar of {MIN_EXERCISED} — a rule's absence from it is "
            "not yet evidence about that rule, so no retirement rests on the "
            "corpus alone")
    if not corpus_live:
        limits.append(
            "the corpus holds no judged finding for ANY declared rule, so a "
            "rule's silence here is the corpus's silence, not the rule's — "
            "only the replay window can support a proposal in this state")

    python = _PythonReplay(root, rules_dir, context_excludes)
    targets = None
    rows: list[RuleLife] = []
    for rule in declared:
        row = per_rule.get(rule.id, {})
        judged = row.get("n", 0)
        upheld = row.get("confirmed", 0) + row.get("fixed", 0)
        refuted, dismissed = row.get("refuted", 0), row.get("dismissed", 0)
        argued_down = refuted + dismissed
        detected = detections.get(rule.id, 0)
        # Rounded BEFORE the comparison, so the number the row prints is the
        # number the verdict was decided on — memory.stats rounds its own
        # promotion bound the same way, and an unrounded gate beside a rounded
        # display makes a 0.6996 print as 0.7 and read as a contradiction.
        argued_down_lb = round(
            memory_mod.wilson_lower_bound(argued_down, judged), 3)
        selected, flagged, replay_reason = _replay(rule, commits, limits,
                                                   python)
        fire_rate = (round(flagged / selected, 3)
                     if flagged is not None and selected else None)

        fired = judged > 0 or detected > 0 or bool(flagged)
        tree_matches: int | None = None
        # Scannability is a property of the rule, so it is stated for every
        # row; "not-examined" is reserved for a scannable rule this run had no
        # reason to scan.
        hazard = "not-examined" if _scannable(rule) else "unscannable"
        silence = ""

        if rule.paused:
            # The acute mechanism owns this rule already. Re-proposing it here
            # would double-count one body of evidence across two ladders.
            verdict = "paused"
            proposal = ("already paused by the acute mechanism — reinstate it "
                        "or retire it deliberately; this report leaves it alone")
        elif not fired:
            replayable = flagged is not None
            # A window shorter than the evidence bar cannot support ANY
            # silence claim, including "its glob matched nothing": in a repo
            # with three commits every rule matches nothing, and that says
            # something about the window, not the ruleset.
            window_usable = commits is not None and len(commits) >= MIN_EXERCISED
            if selected == 0 and window_usable:
                silence = "never-selected"
                verdict = "retire?"
            elif replayable and selected is not None and selected >= MIN_EXERCISED:
                silence = "evaluated-silent"
                verdict = "retire?"
            elif (corpus_usable and selected is not None
                    and selected >= MIN_EXERCISED):
                silence = "unobservable-firing"
                verdict = "retire?"
            else:
                silence = "window-too-thin"
                verdict = "untested"
            if verdict == "retire?" and _scannable(rule):
                if targets is None:
                    from . import advisor as advisor_mod
                    scan_limits: list[str] = []
                    targets = advisor_mod._scan_targets(root,
                                                        limits=scan_limits)
                    limits.extend(scan_limits)
                    limits.append(
                        "the tree scan below borrows the advisor's exclusion "
                        "set, so a hazard living in one of the excluded paths "
                        "is not counted — 'absent from the tree' means absent "
                        "from the SCANNED tree")
                tree_matches, hazard = _tree_hazard(rule, root, targets,
                                                    limits)
            proposal = _silence_proposal(silence, hazard)
        elif (judged >= memory_mod.PROMOTE_MIN_N
                and argued_down_lb >= memory_mod.PROMOTE_WILSON_LB):
            verdict = "demote?"
            proposal = ("demote it out of the blocking severities, or narrow "
                        "its applies_to and its checks until the findings it "
                        "produces are ones people act on. Keeping it at its "
                        "current strength for appearances is the expensive "
                        "option: it is the rule teaching authors to skim")
        elif (fire_rate is not None and selected is not None
                and selected >= MIN_EXERCISED
                and fire_rate >= ALWAYS_FIRES_RATE):
            verdict = "narrow?"
            proposal = ("narrow its applies_to (and its checks' globs) — a rule "
                        "that fires on most diffs it sees is a convention the "
                        "repo has not adopted, or a scope error. NOT a deletion")
        else:
            verdict = "active"
            proposal = ""

        rows.append(RuleLife(
            rule_id=rule.id, engine=rule.engine, severity=rule.severity,
            applies_to=rule.applies_to, judged=judged, upheld=upheld,
            refuted=refuted, dismissed=dismissed, argued_down=argued_down,
            # memory's per-rule row carries the counts, not a rate; this is
            # the same upheld/n derivation `autonomy.PauseAction` makes from
            # the same row, not a second statistic. None at n=0: every
            # neighbouring field here is null-rather-than-zero for the same
            # reason, and a 0.0 precision on zero observations is a number
            # nobody measured. The Wilson bounds beside
            # it stay 0.0, which is CORRECT — a lower bound on no evidence
            # really is zero.
            rate=round(upheld / judged, 3) if judged else None,
            wilson_lb=row.get("wilson_lb", 0.0),
            argued_down_lb=argued_down_lb,
            detections=detected, selected=selected, flagged=flagged,
            fire_rate=fire_rate, replay_reason=replay_reason,
            tree_matches=tree_matches, hazard=hazard, silence=silence,
            verdict=verdict, proposal=proposal))

    rows.sort(key=lambda r: (PROPOSAL_VERDICTS.index(r.verdict)
                             if r.verdict in PROPOSAL_VERDICTS else 9,
                             r.rule_id))
    return LifecycleReport(tuple(rows), commit_window, corpus_window,
                           tuple(notes), tuple(limits))


def _silence_proposal(silence: str, hazard: str) -> str:
    """What to ask first about a silent rule. The question differs by WHICH
    silence it is, and the tree evidence can turn a retirement proposal into a
    repair one."""
    if silence == "never-selected":
        head = ("its applies_to matched no commit in the window — check the "
                "GLOB before the rule: a path that moved reads exactly like a "
                "hazard that stopped happening. The window counts commits that "
                "ADDED lines under those globs, so a commit that only DELETED "
                "from them is not counted here — except for a replayed python "
                "rule, whose window is every commit whose scanned diff "
                "touches them, deletions included")
    elif silence == "evaluated-silent":
        head = ("selected repeatedly and silent every time — the strongest case "
                "that the pattern is stale, and equally the strongest case for "
                "deterrence, since these are the diffs where an author with "
                "the rule in view could have written the hazard and did not")
    elif silence == "unobservable-firing":
        head = ("selected repeatedly, with nothing in the corpus — no judged "
                "finding and no gate detection. THE WEAKEST ROW HERE: its "
                "firing cannot be replayed at all, so nothing was measured "
                "about the rule itself; what was measured is that it appears "
                "in no record across the attested reviews counted in the "
                "corpus window above, and a commit reviewed outside the chain "
                "is invisible to that. Weigh it as an absence of RECORDS, not "
                "an absence of firings")
    else:
        head = "the window barely exercised it; nothing is proposed"
    if hazard == "present-in-the-tree":
        head += (". The hazard it names is PRESENT IN THE TREE and unflagged, "
                 "in lines a whole-file check of its own COULD have fired on — "
                 "that argues the rule is broken, not spent: FIX it (or its "
                 "scope) before proposing to retire it")
    elif hazard == "present-in-lines-it-never-polices":
        head += (". Its pattern matches existing lines, but every check it "
                 "declares is scope:added — it fires only on lines a diff ADDS, "
                 "so pre-existing matches are outside what it polices and argue "
                 "NEITHER way. Do not read them as the rule failing")
    elif hazard == "absent-from-the-tree":
        head += (". The hazard it names is absent from the scanned tree, which "
                 "is what BOTH hypotheses predict — the guard holding, and the "
                 "guard being irrelevant here. Undecidable from counts")
    elif hazard == "nothing-scanned":
        head += (". The tree scan admitted NO file inside its applies_to, so "
                 "the hazard was looked for in zero places — that is not "
                 "evidence of absence, and it may itself be the finding")
    return head


_DOCTRINE = (
    "ABSENCE OF EVIDENCE. A rule that has never fired may be the REASON the "
    "thing it guards has never happened. Zero findings is consistent with two "
    "opposite worlds: the guard is HOLDING (authors stopped writing the "
    "hazard because the rule is there — it produces no findings BY WORKING), "
    "and the guard is INERT (stale pattern, moved surface, a glob that no "
    "longer matches). No count separates them, because deterrence leaves no "
    "trace in a findings table by definition. So every row below is a "
    "PROPOSAL with a question attached — retirement here is never automatic, "
    "and this command edits nothing.")


def _wrap(text: str, indent: str) -> list[str]:
    """Wrapped, never truncated — the same contract the advisor's ANSWERED
    rows keep. A proposal whose reasoning is cut off is a proposal a reader
    cannot argue with, and arguing with it is the entire point."""
    return [indent + line for line in textwrap.wrap(
        " ".join(text.split()), width=92, break_long_words=False,
        break_on_hyphens=False)]


def render(report: LifecycleReport) -> str:
    lines = ["warden rules lifecycle — the rules this repo already has, read "
             "against their own record"]
    lines.append("")
    for note in report.notes:
        lines.extend(_wrap(f"NOTE: {note}", "  "))
    if not report.computed:
        lines.append("  NOT A CLEAN REPORT: no rule was judged at all.")
        return "\n".join(lines) + "\n"

    # The two window lines are deliberately NOT wrapped: they are quoted
    # verbatim by every claim below, and a claim whose window arrives in two
    # pieces is one a reader has to reassemble.
    lines.append(f"  FIRING WINDOW: {report.commit_window}")
    lines.append(f"  CORPUS WINDOW: {report.corpus_window}")
    lines.append("")
    lines.extend(_wrap(_DOCTRINE, "  "))

    def _section(verdict: str, heading: str, blurb: str) -> None:
        picked = [r for r in report.rows if r.verdict == verdict]
        lines.append("")
        lines.append(f"{heading} ({len(picked)})")
        lines.extend(_wrap(blurb, "  "))
        if not picked:
            lines.append("  none")
        for r in picked:
            lines.append(f"  {r.rule_id} [{r.severity}, engine:{r.engine}] "
                         f"applies_to={list(r.applies_to)}")
            lines.extend(_wrap(_counts_line(r), "      "))
            lines.extend(_wrap(f"-> {r.proposal}", "      "))

    _section("retire?", "RETIRE?",
             "never fired over the windows named above — read every row "
             "against the absence-of-evidence problem before acting on it.")
    _section("demote?", "DEMOTE?",
             "the corpus argues these DOWN. Demote or narrow — do not keep a "
             "rule at strength for appearances. (The acute lever is separate: "
             "`warden autonomy pause` fires on a streak of consecutive "
             "refuting review ROUNDS; these rows are the chronic case it "
             "cannot see.)")
    _section("narrow?", "NARROW?",
             "fires on most of the diffs it is selected on — a convention the "
             "repo has not adopted, or a scope error. Narrow applies_to.")

    paused = [r for r in report.rows if r.verdict == "paused"]
    untested = [r for r in report.rows if r.verdict == "untested"]
    active = [r for r in report.rows if r.verdict == "active"]
    if paused:
        lines.append("")
        lines.append(f"PAUSED ({len(paused)}) — the acute mechanism holds "
                     "these; this report leaves them alone")
        for r in paused:
            lines.append(f"  {r.rule_id}")
    if untested:
        lines.append("")
        lines.append(f"UNTESTED ({len(untested)}) — silent, but over a window "
                     "that barely exercised them: UNMEASURED is not clean, and "
                     "it is not dead either")
        for r in untested:
            lines.append(f"  {r.rule_id}:")
            lines.extend(_wrap(_counts_line(r), "      "))
    lines.append("")
    lines.append(f"ACTIVE ({len(active)}) — firing, and the corpus is not "
                 "arguing them down")
    for r in active:
        lines.append(f"  {r.rule_id}:")
        lines.extend(_wrap(_counts_line(r), "      "))

    if report.limits:
        lines.append("")
        lines.append("LIMITS — what the numbers above do and do not cover:")
        for limit in report.limits:
            lines.extend(_wrap(f"- {limit}", "  "))
    return "\n".join(lines) + "\n"


def _counts_line(r: RuleLife) -> str:
    """Every number a row makes a claim on, with the unmeasurable ones named
    UNMEASURED rather than shown as zero."""
    fired = (f"flagged {r.flagged}/{r.selected} selected"
             if r.flagged is not None else
             f"firing UNMEASURED ({r.replay_reason})"
             + (f", selected {r.selected}" if r.selected is not None else ""))
    tree = ("" if r.tree_matches is None
            else f"; tree matches {r.tree_matches}")
    return (f"judged {r.judged} (upheld {r.upheld}, refuted {r.refuted}, "
            f"dismissed {r.dismissed}, argued-down floor {r.argued_down_lb}), "
            f"gate detections {r.detections}; {fired}{tree}")
