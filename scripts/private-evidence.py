#!/usr/bin/env python3
"""Say whether a commit carries the evidence the public export leaves out.

Usage: private-evidence.py [--repo DIR] [--head REV] [--base REV]
Prints `corpus=present|absent` and `tracker=present|absent`, appends the same
lines to $GITHUB_OUTPUT and the reason for each absence to
$GITHUB_STEP_SUMMARY when those are set. The corpus is any tracked path under
.warden/memory/ but tags.yaml, the line publish.yaml draws; the tracker is
.beads/. CI skips a step only on an explicit `absent`, so a missing answer
runs it.

With --base, evidence the base carries and the head does not is a refusal:
a change that deletes the corpus does not earn the skip by deleting it.
Exit 0 decided, 1 refused, 2 git could not answer.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

EVIDENCE = {  # name -> (tree prefix, the path that alone does not count, reason)
    "corpus": (".warden/memory/", ".warden/memory/tags.yaml",
               "no committed review corpus in this tree"),
    "tracker": (".beads/", None, "no committed tracker in this tree"),
}


class CannotAnswer(Exception):
    pass


def present(repo: Path, rev: str, name: str) -> bool:
    prefix, ignored, _ = EVIDENCE[name]
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", rev, "--", prefix],
        capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise CannotAnswer(f"git ls-tree {rev} -- {prefix} exited "
                           f"{result.returncode}: {result.stderr.strip()}")
    return any(p and p != ignored for p in result.stdout.splitlines())


def decide(repo: Path, head: str, base: str | None) -> tuple[dict, list[str]]:
    """({name: present?}, refusals)."""
    found, refused = {}, []
    for name, (prefix, _, _) in EVIDENCE.items():
        found[name] = present(repo, head, name)
        if base and not found[name] and present(repo, base, name):
            refused.append(f"{name}: {base} carries {prefix} and {head} does not — "
                           "a change that deletes it does not get to skip the "
                           "steps that read it")
    return found, refused


def _append(var: str, lines: list[str]) -> None:
    if os.environ.get(var):
        with open(os.environ[var], "a", encoding="utf-8") as fh:
            fh.write("".join(f"{line}\n" for line in lines))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--base")
    args = ap.parse_args(argv)
    try:
        found, refused = decide(Path(args.repo), args.head, args.base)
    except CannotAnswer as exc:
        print(f"private-evidence: could not answer: {exc}", file=sys.stderr)
        return 2
    if refused:
        print("private-evidence: refused:\n  " + "\n  ".join(refused), file=sys.stderr)
        return 1
    outputs = [f"{n}={'present' if ok else 'absent'}" for n, ok in found.items()]
    print("\n".join(outputs))
    _append("GITHUB_OUTPUT", outputs)
    _append("GITHUB_STEP_SUMMARY", [
        f"- **{n} absent: {EVIDENCE[n][2]}** — the steps that read it are "
        "skipped, not passed" for n, ok in found.items() if not ok])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
