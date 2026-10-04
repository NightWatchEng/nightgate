#!/usr/bin/env python3
"""Carry every private main commit the public repository lacks, one public commit each.

Usage: publish-sync.py --public URL --work DIR --head SHA [--repo DIR]
                       [--tags-from vX.Y.Z] [--report FILE] [--no-push]
DIR, a new clone of URL, must sit outside the private work tree: publish-public.py
deletes what its manifest does not select. The public tip's `Source:` trailer
names the last commit carried; an empty public main gets one import of --head.
Each first-parent commit since is exported, committed (empty if only excluded
paths moved) and parity-checked: its (path, mode, blob) list must equal the
manifest's selection. v* tags from --tags-from on a carried commit are mirrored.
One atomic, non-force push, unless --no-push. Exit 0 carried or nothing to
carry, 1 refused (nothing pushed; a tag it cannot place refuses after the
push), 2 anything else.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "publish_public", Path(__file__).resolve().parent / "publish-public.py")
publish = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publish)
git, Refused, CannotRun = publish.git, publish.Refused, publish.CannotRun

IMPORT_SUBJECT = "ops(repo): import the public tree (no-bead: private tracker)"  # commit-lint clean
IDENTITY = ("-c", "user.name=Nightgate export", "-c", "user.email=export@nightgate.invalid",
            "-c", "commit.gpgsign=false")
SOURCE = re.compile(r"^Source: ([0-9a-f]{40})$", re.M)
VERSION = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


def source_of(work: Path, rev: str) -> str | None:
    found = SOURCE.findall(git(work, "log", "-1", "--format=%B", rev).decode())
    return found[-1] if found else None


def pending(repo: Path, work: Path, head: str) -> tuple[list[str], bool]:
    """(private commits to carry, oldest first; whether the first is the import)."""
    if not git(work, "for-each-ref", "refs/remotes/origin/main").strip():
        return [head], True  # the clone is on an unborn main
    git(work, "checkout", "-q", "-B", "main", "origin/main")
    last = source_of(work, "HEAD")
    if last is None:
        raise Refused("the public tip carries no Source trailer; it was not written by this export")
    if is_ancestor(repo, head, last):
        return [], False  # a stale run: the public tip is already at or past --head
    if not is_ancestor(repo, last, head):
        raise Refused(f"the public tip's Source {last[:12]} is not an ancestor of {head[:12]}")
    todo = git(repo, "rev-list", "--first-parent", "--reverse", f"{last}..{head}").decode().split()
    if git(repo, "rev-parse", f"{todo[0]}^1").decode().strip() != last:
        raise Refused(f"{last[:12]} is not on {head[:12]}'s first-parent line")
    return todo, False


def is_ancestor(repo: Path, older: str, newer: str) -> bool:  # an unknown commit reads as no
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer],
                          capture_output=True, check=False).returncode == 0


def carry(repo: Path, work: Path, sha: str, imported: bool) -> str:
    message, files, _, _ = publish.export(repo, sha, work)
    message = f"{IMPORT_SUBJECT}\n\nSource: {sha}\n" if imported else message
    git(work, "add", "-A")
    date = git(repo, "log", "-1", "--format=%aI", sha).decode().strip()
    git(work, *IDENTITY, "commit", "-q", "--allow-empty", f"--date={date}", "-F", "-",
        stdin=message.encode())
    want = publish.selected(publish.load_manifest(repo, sha), publish.tree_entries(repo, sha))
    have = publish.tree_entries(work, "HEAD")  # blob ids are content hashes: comparable
    if have != want or source_of(work, "HEAD") != sha:
        wrong = sorted(p for p in set(have) | set(want) if have.get(p) != want.get(p))
        raise Refused(f"parity failed for {sha[:12]}: {len(wrong)} path(s) differ, "
                      f"first {wrong[:5]}; Source {source_of(work, 'HEAD')}")
    return f"{sha} -> {git(work, 'rev-parse', 'HEAD').decode().strip()}: {files} files, parity ok"


def mirror_tags(repo: Path, work: Path, head: str, floor: tuple[int, ...]) -> tuple[list, list, list]:
    log = git(work, "log", "--format=%H%x00%B%x1e", "main").decode().split("\x1e")
    carried = {found[-1]: public for public, _, body in (e.strip("\n").partition("\0") for e in log)
               if (found := SOURCE.findall(body))}  # private sha -> its public commit
    lines, made, wrong = [], [], []  # wrong: refused once the commits are pushed
    refs = git(repo, "for-each-ref", "--format=%(refname:short) %(objectname) %(*objectname)",
               "refs/tags/v*").decode().splitlines()
    for name, obj, *peeled in (r.split() for r in refs):
        version = VERSION.fullmatch(name)
        if not version or tuple(map(int, version.groups())) < floor:
            continue
        commit, target = (peeled or [obj])[0], carried.get((peeled or [obj])[0])
        existing = (git(work, "for-each-ref", "--format=%(objectname) %(*objectname)",  # peeled
                        f"refs/tags/{name}").decode().split() or [""])[-1]
        if existing and existing != (target or existing):
            wrong.append(f"public tag {name} is at {existing[:12]}, not {target[:12]}; "
                         "delete it there and the next run mirrors it")
        elif target and not existing:
            git(work, "tag", name, target)
            made.append(name)
        elif not existing and is_ancestor(repo, commit, head):  # behind the carried line
            wrong.append(f"tag {name} is on {commit[:12]}, which no public commit carries; "
                         "push it to the public repository by hand")
        lines.append(f"tag {name} -> {existing or target or 'not carried yet, left for a later run'}")
    return lines, made, wrong


def run(args: argparse.Namespace, report: list[str]) -> None:
    repo, work = args.repo.resolve(), args.work.resolve()
    top = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if work.is_relative_to(top) or (work.exists() and any(work.iterdir())):
        raise CannotRun(f"--work {work} must be a new directory outside {top}")
    head = git(repo, "rev-parse", "--verify", f"{args.head}^{{commit}}").decode().strip()
    git(repo, "init", "-q", "-b", "main", str(work))
    git(work, "remote", "add", "origin", args.public)
    git(work, "fetch", "-q", "--tags", "origin")
    todo, imported = pending(repo, work, head)
    for i, sha in enumerate(todo):
        report.append(carry(repo, work, sha, imported and i == 0))
    floor = tuple(map(int, VERSION.fullmatch(args.tags_from).groups()))
    lines, tags, wrong = mirror_tags(repo, work, head, floor)
    report.extend(lines)
    if (todo or tags) and not args.no_push:
        git(work, "push", "-q", "--atomic", "origin", "main:refs/heads/main",
            *(f"refs/tags/{t}:refs/tags/{t}" for t in tags))
    report.append(f"{len(todo)} commit(s) and {len(tags)} tag(s) "
                  f"{'prepared, not pushed' if args.no_push else 'pushed'}")
    if wrong:  # after the push: a tag the export cannot place never holds the commits back
        raise Refused("; ".join(wrong))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--public", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--tags-from", default="v3.0.0")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args(argv)
    report, code = [], 0
    try:
        if not VERSION.fullmatch(args.tags_from):
            raise CannotRun(f"--tags-from {args.tags_from!r} is not vX.Y.Z")
        run(args, report)
    except Exception as exc:  # anything but a refusal is a could-not-run
        code = 1 if isinstance(exc, Refused) else 2
        report.append(f"public sync {'refused' if code == 1 else 'cannot run'}: {exc}")
    text = "\n".join(report) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text)
    (sys.stderr if code else sys.stdout).write(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
