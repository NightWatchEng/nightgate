#!/usr/bin/env python3
"""Report one `warden memory watch` run on a standing GitHub issue.

usage: memory-watch-issue.py --repo OWNER/NAME --ingest-outcome OUTCOME
           --watch-exit N --watch-json PATH [--run-url URL] [--summary PATH]

Reads the outcome of the `warden memory ingest` step, the exit code of
`warden memory watch --json`, and the JSON it printed. Then:

- no transitions: no GitHub call at all, and one job-summary line;
- transitions: open the standing issue, or comment on it, with each transition
  not already reported. A transition carries a stable key in a hidden
  `<!-- memory-watch:key=... -->` comment, and a key found in any issue with
  this title and label, open or closed, is not reported again;
- UNREAD (the ingest did not succeed, the watch exited 3 or anything other
  than 0 or 1, or its JSON is unreadable or disagrees with its exit): the issue
  says UNREAD with the cause, and this script exits 1 so the job fails.

The notification surface is a GitHub issue because a bead cannot be filed from
CI. Every `gh` call goes through one callable, so a test can fake it.

Exit 0: quiet, or transitions reported (or already reported). Exit 1: UNREAD.
Exit 2: a `gh` call failed or the arguments are unusable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Callable

TITLE = "memory watch: the review-memory loop has something to act on"
LABEL = "memory-watch"
SURFACE = ("The notification surface is a GitHub issue because a bead cannot "
           "be filed from CI: the tracker syncs through a Dolt remote the job "
           "does not have.")
MARKER = "<!-- memory-watch:key={key} -->"
_MARKER = re.compile(r"<!-- memory-watch:key=(\S+) -->")

Gh = Callable[[list], str]


class GhError(Exception):
    pass


def default_gh(args: list) -> str:
    try:
        return subprocess.run(["gh", *args], check=True, capture_output=True,
                              text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        raise GhError(f"gh {' '.join(args[:2])} failed: {detail.strip()}") from e


def read_run(ingest_outcome: str, watch_exit: str, watch_text: str
             ) -> tuple[list[dict], list[str]]:
    """(transitions, unread causes) from what the two steps left behind."""
    causes: list[str] = []
    if ingest_outcome != "success":
        causes.append("`warden memory ingest` did not succeed (step outcome: "
                      f"{ingest_outcome or 'not recorded'}); its output is in "
                      "the run log")
    try:
        doc = json.loads(watch_text)
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        doc = None
    code = int(watch_exit) if re.fullmatch(r"\d+", watch_exit or "") else None
    if code == 3:
        unread = doc.get("unread") if doc else None
        causes += (unread if isinstance(unread, list) and unread else
                   ["`warden memory watch` exited 3 (UNREAD) and printed no "
                    "readable cause"])
        return [], causes
    if code not in (0, 1):
        causes.append(f"`warden memory watch` exited {watch_exit or 'nothing'}"
                      ", which is neither 0 (no transitions), 1 (transitions) "
                      "nor 3 (UNREAD): a crash or a usage error, so nothing "
                      "was read")
        return [], causes
    transitions = doc.get("transitions") if doc else None
    expected = "transitions" if code == 1 else "quiet"
    if (doc is None or doc.get("status") != expected
            or not isinstance(transitions, list)
            or bool(transitions) != (code == 1)
            or not all(isinstance(t, dict) and isinstance(t.get("key"), str)
                       and t["key"] and " " not in t["key"]
                       for t in transitions)):
        causes.append(f"`warden memory watch` exited {code} but its JSON "
                      "does not say the same thing (status "
                      f"{doc.get('status') if doc else None!r}), so neither "
                      "is believed")
        return [], causes
    return transitions, causes


def _entries(transitions: list[dict], causes: list[str]) -> list[dict]:
    if causes:
        digest = hashlib.sha256("\n".join(causes).encode()).hexdigest()[:16]
        text = ("**UNREAD**: the watch could not read what it needs, so no "
                "transition is claimed either way.\n"
                + "\n".join(f"  - {c}" for c in causes))
        return [{"key": f"unread:{digest}", "text": text}]
    out = []
    for t in transitions:
        numbers = ", ".join(f"{k} {v}" for k, v in
                            (t.get("numbers") or {}).items())
        out.append({"key": t["key"], "text": (
            f"**{t.get('class')}** `{t.get('subject')}`: {numbers}\n"
            f"  - bar: {t.get('bar')}\n  - next: {t.get('next')}")})
    return out


def _section(entries: list[dict], run_url: str, note: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    head = f"### {stamp}" + (f" ([run]({run_url}))" if run_url else "")
    body = "\n\n".join(f"- {e['text']}\n  {MARKER.format(key=e['key'])}"
                       for e in entries)
    return f"{head}\n\n{note}\n\n{body}\n" if note else f"{head}\n\n{body}\n"


def report(args: argparse.Namespace, gh: Gh) -> int:
    watch_text = ""
    try:
        with open(args.watch_json, encoding="utf-8") as fh:
            watch_text = fh.read()
    except OSError:
        pass
    transitions, causes = read_run(args.ingest_outcome, args.watch_exit,
                                   watch_text)
    unread = bool(causes)
    entries = _entries(transitions, causes)
    if not entries:
        _summary(args, "memory watch: no transitions (a transition would be "
                       "reported on a GitHub issue, because a bead cannot be "
                       "filed from CI).")
        return 0
    try:
        listed = json.loads(gh(["issue", "list", "--repo", args.repo,
                                "--label", LABEL, "--state", "all",
                                "--json", "number,title,state",
                                "--limit", "100"]))
        mine = sorted((i for i in listed if i.get("title") == TITLE),
                      key=lambda i: i["number"])
        seen: set[str] = set()
        for issue in mine:
            view = json.loads(gh(["issue", "view", str(issue["number"]),
                                  "--repo", args.repo,
                                  "--json", "body,comments"]))
            texts = [view.get("body") or ""] + [
                c.get("body") or "" for c in view.get("comments") or []]
            for text in texts:
                seen.update(_MARKER.findall(text))
        new = [e for e in entries if e["key"] not in seen]
        what = "UNREAD" if unread else f"{len(entries)} transition(s)"
        if not new:
            _summary(args, f"memory watch: {what}, all already reported on "
                           f"the standing issue. {SURFACE}")
            return 1 if unread else 0
        note = "" if unread else _note(watch_text)
        section = _section(new, args.run_url, note)
        standing = next((i for i in mine
                         if str(i.get("state", "")).upper() == "OPEN"), None)
        if standing:
            gh(["issue", "comment", str(standing["number"]), "--repo",
                args.repo, "--body", section])
            where = f"issue #{standing['number']}"
        else:
            gh(["label", "create", LABEL, "--repo", args.repo, "--color",
                "FBCA04", "--description",
                "warden memory watch: a bar crossed in the review memory",
                "--force"])
            intro = ("`warden memory watch` found something in the review "
                     "memory to act on. It reports; it never edits a rule, a "
                     "skill or the baseline. To acknowledge a transition, run "
                     "`warden memory watch --record` and commit the dated "
                     f"record it writes.\n\n{SURFACE}\n\n")
            url = gh(["issue", "create", "--repo", args.repo, "--title",
                      TITLE, "--label", LABEL, "--body", intro + section])
            where = url.strip() or "a new issue"
    except (GhError, ValueError, KeyError, TypeError) as e:
        print(f"memory-watch-issue: {e}", file=sys.stderr)
        _summary(args, f"memory watch: could not reach the standing issue "
                       f"({e}). {SURFACE}")
        return 2
    _summary(args, f"memory watch: {what}, {len(new)} new, reported on "
                   f"{where}. {SURFACE}")
    return 1 if unread else 0


def _note(watch_text: str) -> str:
    try:
        return str(json.loads(watch_text).get("note") or "")
    except (ValueError, AttributeError):
        return ""


def _summary(args: argparse.Namespace, line: str) -> None:
    print(line)
    path = args.summary or os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def run(argv: list[str] | None = None, gh: Gh | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ingest-outcome", required=True)
    parser.add_argument("--watch-exit", required=True)
    parser.add_argument("--watch-json", required=True)
    parser.add_argument("--run-url", default="")
    parser.add_argument("--summary", default="")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return report(args, gh or default_gh)


if __name__ == "__main__":
    sys.exit(run())
