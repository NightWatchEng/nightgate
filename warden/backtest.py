"""Backtest a proposed rule against the repo's own history.

This is what separates a recommendation from advice. Before the advisor
proposes a rule, that rule is replayed over the repo's real past — the diffs
it would have flagged — and the flags are split against the defect record:
how many past commits it would have fired on, how many of those turned out to
be defects (a later commit reverted them), and how many were flags on commits
that were fine (the projected false-positive rate). A proposal that would have
fired often and caught nothing is REJECTED *with its numbers*, never quietly
dropped — the rejection is itself evidence, and hiding it lets the same bad
rule be re-proposed every cycle.

The honesty contract this module keeps:

- **Only a mechanical rule can be backtested.** A `claude`-engine rule is a
  judgment, not a regex; replaying it would mean re-running the judge over
  history, which is not affordable here. Those are marked `unbacktested` with
  the reason, never given a fabricated precision figure — the platform has
  been burned once by exactly that.
- **True-positive is a revert coincidence, not a proven catch.** The one
  defect signal derivable from local git at $0 is "this flagged commit was
  later reverted" ("This reverts commit <sha>"). It is a PROXY, and it is
  honest in both directions: a revert unrelated to what the rule flagged
  over-counts true positives, and a defect fixed without a revert under-counts
  them — so the projected false-positive rate is an ESTIMATE that can err
  either way, reported as such, never as a bound or a precise figure.
- **Firing zero times is `no-signal`, not a pass.** A window in which the rule
  never fired says nothing about its precision; it is reported as such, never
  as a clean record.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from .runs import git_env

# A window bound keeps the replay affordable and is always stated in the
# result — a silent cap would make a partial history read as the whole one.
DEFAULT_WINDOW = 200

# Fired this many times while catching nothing and the rule is REJECTED rather
# than merely reported: a rule that never catches a defect but keeps firing is
# pure noise, and noise trains people to ignore the gate. One flag with zero
# catches is thin, but "fired 200 times and caught nothing" is the case to
# reject, so the bar is any firing with no true positive.
_REJECT_WHEN_NO_TRUE_POSITIVE = True


class BacktestError(Exception):
    pass


@dataclass(frozen=True)
class Backtest:
    engine: str
    backtestable: bool
    window: str
    commits_scanned: int
    flagged: int
    true_positive: int
    false_positive: int
    projected_fp_rate: float | None
    verdict: str                       # unbacktested | no-signal | CANDIDATE | REJECTED
    reason: str | None = None          # why unbacktested
    caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"engine": self.engine, "backtestable": self.backtestable,
                "window": self.window, "commits_scanned": self.commits_scanned,
                "flagged": self.flagged, "true_positive": self.true_positive,
                "false_positive": self.false_positive,
                "projected_fp_rate": self.projected_fp_rate,
                "verdict": self.verdict, "reason": self.reason,
                "caveats": list(self.caveats)}


def _git(root, *args: str) -> str:
    env = git_env()
    env["GIT_LITERAL_PATHSPECS"] = "1"
    try:
        return subprocess.run(["git", *args], cwd=root, check=True,
                              capture_output=True, text=True, env=env).stdout
    except FileNotFoundError as e:
        raise BacktestError("git is not on PATH") from e
    except subprocess.CalledProcessError as e:
        raise BacktestError(
            f"git {' '.join(args)} failed: {e.stderr.strip()[:200]}") from e


_REVERTS_COMMIT = re.compile(r"This reverts commit ([0-9a-f]{7,40})")


def _compile_checks(entry) -> tuple[list[dict], list[str]]:
    """Compile a declarative starter's checks for replay. Returns
    (runnable, caveats). A require:true check fires when its pattern is ABSENT,
    which cannot be judged from added lines alone — excluded with a caveat, the
    same refusal `advisor.measure` makes."""
    runnable: list[dict] = []
    caveats: list[str] = []
    for check in entry.starter.get("checks", []):
        cid = check.get("id")
        if check.get("require"):
            caveats.append(f"check {cid!r} uses require:true (fires on ABSENCE) "
                           "and cannot be replayed over added lines — excluded")
            continue
        bits = 0
        for flag in check.get("flags", []):
            bits |= {"i": re.IGNORECASE, "m": re.MULTILINE,
                     "s": re.DOTALL}.get(flag, 0)
        try:
            pattern = re.compile(check["pattern"], bits)
        except re.error as e:
            caveats.append(f"check {cid!r} did not compile ({e}) — excluded")
            continue
        scope = check.get("scope", "added")
        if scope == "file":
            caveats.append(
                f"check {cid!r} is scope:file — the real gate matches it over a "
                "whole file, but the backtest only sees ADDED lines, so a "
                "trigger in unchanged context is not seen (may under-flag)")
        runnable.append({"id": cid, "pattern": pattern, "scope": scope,
                         "globs": check.get("globs") or []})
    return runnable, caveats


def flag_commits(entry, commits) -> tuple[set[str], list[str]]:
    """Which commits the starter would have fired on. `commits` is a list of
    (sha, files), where `files` is either a list of (path, added_text) or a
    plain string (one anonymous file — the synthetic-test form). A commit is
    flagged if any runnable check matches added text in a file its `globs`
    admit. Pure — no git — so the replay logic is testable with synthetic
    history, and it honours each check's globs the way the real gate does."""
    from .rules import glob_match
    runnable, caveats = _compile_checks(entry)
    flagged: set[str] = set()
    for sha, files in commits:
        per_file = [("", files)] if isinstance(files, str) else files
        hit = False
        for check in runnable:
            for path, text in per_file:
                if check["globs"] and path and not any(
                        glob_match(g, path) for g in check["globs"]):
                    continue
                if check["pattern"].search(text or ""):
                    hit = True
                    break
            if hit:
                break
        if hit:
            flagged.add(sha)
    return flagged, caveats


def _match_defect(sha: str, defect_shas: set[str]) -> bool:
    """A window sha (full 40-char) is a defect if a revert names it. Revert
    pointers may be abbreviated, so match by prefix in either direction."""
    for d in defect_shas:
        if sha == d or sha.startswith(d) or d.startswith(sha):
            return True
    return False


def classify(flagged: set[str], defect_shas: set[str]) -> dict:
    """Split flags into true/false positives against the defect record and
    decide the verdict. Pure."""
    tp = sum(1 for s in flagged if _match_defect(s, defect_shas))
    fp = len(flagged) - tp
    rate = (fp / len(flagged)) if flagged else None
    if not flagged:
        verdict = "no-signal"
    elif tp == 0 and _REJECT_WHEN_NO_TRUE_POSITIVE:
        verdict = "REJECTED"
    else:
        verdict = "CANDIDATE"
    return {"true_positive": tp, "false_positive": fp,
            "projected_fp_rate": rate, "verdict": verdict}


def parse_added_by_file(diff_text: str) -> list[tuple[str, str]]:
    """Split a `git show --unified=0` body into (path, added_text) per file.

    Driven by diff STRUCTURE, not by line prefixes, so an added source line
    that itself begins with '++' (emitted as '+++content') is not mistaken for
    a '+++ b/file' header: a file header only occurs BEFORE the first `@@`
    hunk, so it is recognised only while NOT inside a hunk. Additions are the
    `+` lines seen while inside a hunk.
    """
    files: list[tuple[str, str]] = []
    current: str | None = None
    added: list[str] = []
    in_hunk = False

    def flush():
        nonlocal added
        if current is not None and added:
            files.append((current, "\n".join(added)))
        added = []

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            flush()
            current = None
            in_hunk = False
        elif not in_hunk and line.startswith("+++ "):
            flush()
            path = line[4:]
            current = path[2:] if path.startswith("b/") else path
        elif not in_hunk and line.startswith("--- "):
            continue
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            added.append(line[1:])
    flush()
    return files


def read_commit_additions(root, n: int):
    """The last `n` commits (newest first) as (sha, [(path, added_text), ...]).
    Merges are skipped — their diff is not the author's added lines. Read with
    --no-ext-diff/--no-textconv so an ambient diff driver cannot reshape the
    output the parse depends on."""
    shas = [s for s in _git(root, "log", "--no-merges", "-n", str(n),
                            "--format=%H").splitlines() if s]
    out = []
    for sha in shas:
        # log.showSignature off for parity with read_reverted_shas — with
        # --format= the header is suppressed anyway, but a future format change
        # must not be able to leak signature text into the parsed diff.
        body = _git(root, "-c", "log.showSignature=false", "show", sha,
                    "--no-color", "--no-ext-diff", "--no-textconv",
                    "--unified=0", "--format=")
        out.append((sha, parse_added_by_file(body)))
    return out


def parse_reverted(raw_log: str) -> set[str]:
    """Revert-target shas from a NUL-terminated `%H%B` log. NUL cannot appear
    in a commit message, so record-splitting on it is robust to any control
    bytes a body may contain (the earlier %x1d framing was not)."""
    reverted: set[str] = set()
    for rec in raw_log.split("\x00"):
        rec = rec.lstrip("\n")
        if len(rec) < 40:
            continue
        body = rec[40:]  # the 40-char %H, then the body
        for m in _REVERTS_COMMIT.finditer(body):
            reverted.add(m.group(1))
    return reverted


def read_reverted_shas(root) -> set[str]:
    """Every commit sha that a revert names ('This reverts commit <sha>'),
    scanned across the whole history — a revert can land any time after the
    commit it undoes. This is the true-positive proxy. log.showSignature is
    forced off so a signing setup cannot inject text into the parsed body."""
    raw = _git(root, "-c", "log.showSignature=false", "log", "--no-color",
               "--format=%H%B%x00")
    return parse_reverted(raw)


def backtest_starter(entry, root, window: int = DEFAULT_WINDOW, *,
                     commits=None, defect_shas=None) -> Backtest:
    """Replay one catalog entry's starter over history. `commits` and
    `defect_shas` may be injected (tests, or a caller that already read git);
    otherwise they are read from local git under `root`.

    A non-declarative entry, or one with no runnable check, is `unbacktested`
    with the reason — never a fabricated number.
    """
    engine = getattr(entry, "engine", "unknown")
    if engine != "declarative" or not (entry.starter
                                       and entry.starter.get("checks")):
        if engine == "claude":
            reason = ("a claude rule is a judgment, not a regex — replaying it "
                      "would mean re-running the judge over history, which is "
                      "not affordable here")
        elif engine == "python":
            reason = ("a python rule is a custom checker, not a declarative "
                      "pattern — replaying it would mean executing project code "
                      "per past commit, which is not run here")
        else:
            reason = (f"engine {engine!r} ships no declarative starter to "
                      "replay over history")
        return Backtest(engine=engine, backtestable=False,
                        window="", commits_scanned=0, flagged=0,
                        true_positive=0, false_positive=0,
                        projected_fp_rate=None, verdict="unbacktested",
                        reason=reason)

    if commits is None:
        commits = read_commit_additions(root, window)
    if defect_shas is None:
        defect_shas = read_reverted_shas(root)

    flagged, caveats = flag_commits(entry, commits)
    result = classify(flagged, defect_shas)
    caveats = tuple(caveats) + (
        "true-positive is a flagged commit that a LATER COMMIT REVERTED — a "
        "proxy for 'the rule caught a real defect', not proof: the revert may "
        "be unrelated to what the rule flagged (over-counts true positives), "
        "and a defect fixed without a revert is not counted (under-counts). So "
        "projected_fp_rate is an ESTIMATE that can err in either direction, not "
        "a bound",
        f"replayed over the last {len(commits)} non-merge commit(s)",
    )
    return Backtest(
        engine=engine, backtestable=True,
        window=f"last {window} commits",
        commits_scanned=len(commits),
        flagged=len(flagged),
        true_positive=result["true_positive"],
        false_positive=result["false_positive"],
        projected_fp_rate=result["projected_fp_rate"],
        verdict=result["verdict"], reason=None, caveats=caveats)


def render_backtest(bt: Backtest) -> str:
    """One line a proposal carries. A REJECTED verdict shows its numbers rather
    than being dropped, and an unbacktested rule says so with its reason."""
    if not bt.backtestable:
        return f"backtest: UNBACKTESTED — {bt.reason}"
    if bt.verdict == "no-signal":
        return (f"backtest: NO SIGNAL over {bt.window} "
                f"({bt.commits_scanned} scanned) — the rule never fired, so "
                "precision is unmeasured here, not clean")
    rate = f"{bt.projected_fp_rate * 100:.0f}%" if bt.projected_fp_rate is not None else "n/a"
    head = "REJECTED" if bt.verdict == "REJECTED" else "candidate"
    return (f"backtest: {head} over {bt.window} — flagged {bt.flagged}, "
            f"true-positive {bt.true_positive}, projected false-positive "
            f"{bt.false_positive} ({rate}, a revert-coincidence estimate)")


def render_from_dict(d: dict) -> str:
    """Render a backtest carried as a plain dict (a Recommendation's stored
    field), reusing the one renderer above."""
    return render_backtest(Backtest(
        engine=d["engine"], backtestable=d["backtestable"], window=d["window"],
        commits_scanned=d["commits_scanned"], flagged=d["flagged"],
        true_positive=d["true_positive"], false_positive=d["false_positive"],
        projected_fp_rate=d["projected_fp_rate"], verdict=d["verdict"],
        reason=d.get("reason"), caveats=tuple(d.get("caveats") or ())))
