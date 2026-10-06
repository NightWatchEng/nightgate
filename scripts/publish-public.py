#!/usr/bin/env python3
"""Write the public export of one commit and print its public commit message.

Usage: publish-public.py --out DIR [--sha REV] [--repo DIR]
DIR ends up holding exactly the commit's tree filtered by its publish.yaml (a
top-level `.git` aside), each path its `transforms:` names in the declared public
form, so a rerun changes nothing. The message loses bead ids
and `(no-bead: ...)` asides, keeps `(#N)`, ends its header in the `(no-bead:
private ...)` tag the public commit-lint accepts, trades Evidence paragraphs for
one line carrying only the suite's test counts, and ends `Source: <sha>`.
Exit 0 exported, 1 refused by the manifest before DIR is touched, 2 anything else.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

MANIFEST = "publish.yaml"
MODES = {"100644": 0o644, "100755": 0o755}


class Refused(Exception):
    """The manifest does not account for the tree: exit 1."""


class CannotRun(Exception):
    """git, the commit, a tree entry or the manifest could not be read: exit 2."""


def git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], input=stdin,
                            capture_output=True, check=False)
    if result.returncode != 0:
        raise CannotRun(f"git {' '.join(args)} exited {result.returncode}: "
                        f"{result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def tree_entries(repo: Path, sha: str) -> dict[str, tuple[str, str]]:
    entries = {}  # path -> (mode, blob sha)
    for record in filter(None, git(repo, "ls-tree", "-r", "-z", "--full-tree", sha).split(b"\0")):
        meta, path = record.decode().split("\t", 1)
        mode, _, obj = meta.split(" ")
        if mode not in MODES:  # a symlink or submodule has no public form yet
            raise CannotRun(f"{path}: tree mode {mode} is not a regular file")
        entries[path] = (mode, obj)
    return entries


VOCABULARY_HEADER = ("# The declared defect-class vocabulary. The annotated copy, with each name's\n"
                     "# history and receipts, lives in the private repository.\n")


def vocabulary_only(data: bytes) -> bytes:
    """The `tags:` and `aliases:` mappings, values verbatim and in source order;
    the comments, `left_undeclared:` receipts and every other key stay private.
    An empty block is an empty mapping, as warden/tags.py reads one."""
    import yaml
    doc = yaml.safe_load(data)
    if not isinstance(doc, dict):
        raise CannotRun("vocabulary-only: the file is not a YAML mapping")
    kept = {k: {} if v is None else v for k, v in doc.items() if k in ("tags", "aliases")}
    for key in (k for k, v in kept.items() if not isinstance(v, dict)):
        raise CannotRun(f"vocabulary-only: {key}: is not a mapping")
    return (VOCABULARY_HEADER + yaml.safe_dump(kept, sort_keys=False, allow_unicode=True,
                                               width=float("inf"))).encode()


TRANSFORMS = {"vocabulary-only": vocabulary_only}


def transformed(repo: Path, sha: str, manifest: dict, declared: object, where: str) -> dict:
    """path -> (blob id, bytes) of each declared transform's public form. One on a
    path the manifest keeps private, or on no file, is a typo the policy missed."""
    if not isinstance(declared, dict) or not all(
            isinstance(p, str) and t in TRANSFORMS for p, t in declared.items()):
        raise CannotRun(f"{where}: transforms must map paths to one of {sorted(TRANSFORMS)}")
    out = {}
    for path, name in declared.items():
        # `dir/` lists the directory's children: only one record naming `path` itself is a file
        listed = [r.decode().replace("\t", " ").split(" ", 3) for r in
                  git(repo, "ls-tree", "-z", "--full-tree", sha, "--", path).split(b"\0") if r]
        if not exported(manifest, path) or [r[1::2] for r in listed] != [["blob", path]]:
            raise Refused(f"{MANIFEST}: transform {name} is declared for {path}, "
                          f"not a file it exports at {sha[:12]}")
        data = TRANSFORMS[name](git(repo, "cat-file", "blob", listed[0][2]))
        out[path] = (git(repo, "hash-object", "--stdin", stdin=data).decode().strip(), data)
    return out


def load_manifest(repo: Path, sha: str) -> dict:
    where = f"{MANIFEST} at {sha[:12]}"
    import yaml  # here, so a python without PyYAML is a could-not-run (exit 2)
    try:
        data = yaml.safe_load(git(repo, "show", f"{sha}:{MANIFEST}"))
    except yaml.YAMLError as exc:
        raise CannotRun(f"{where} is not YAML: {exc}") from exc
    top = data.get("top_level") if isinstance(data, dict) else None
    if not isinstance(top, dict) or not top or not set(top) <= {"include", "exclude"}:
        raise CannotRun(f"{where}: top_level must hold include and exclude lists")
    lists = {"top_level.include": top.get("include", []),
             "top_level.exclude": top.get("exclude", []),
             "exclude": data.get("exclude", []), "keep": data.get("keep", [])}
    for key, value in lists.items():
        if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
            raise CannotRun(f"{where}: {key} must be a list of paths")
    # a path in both top_level lists is excluded: the safe reading of a typo
    manifest = {"top_level": {**dict.fromkeys(lists["top_level.include"], "include"),
                              **dict.fromkeys(lists["top_level.exclude"], "exclude")},
                "exclude": lists["exclude"], "keep": lists["keep"]}
    manifest["transforms"] = transformed(repo, sha, manifest, data.get("transforms", {}), where)
    return manifest


def matches(pattern: str, path: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    want, have = pattern.split("/"), path.split("/")
    return len(want) == len(have) and all(map(fnmatch.fnmatchcase, have, want))


def exported(manifest: dict, path: str) -> bool:
    return (manifest["top_level"].get(path.split("/", 1)[0]) == "include"
            and (not any(matches(p, path) for p in manifest["exclude"])
                 or any(matches(p, path) for p in manifest["keep"])))


def selected(manifest: dict, entries: dict) -> dict:
    present, declared = {p.split("/", 1)[0] for p in entries}, set(manifest["top_level"])
    unclassified = sorted(present - declared)
    stale = sorted(declared - present) + [p for p in manifest["exclude"] + manifest["keep"]
                                          if not any(matches(p, path) for path in entries)]
    if unclassified or stale:  # a stale entry is a typo or a rename the policy missed
        raise Refused(f"{MANIFEST} does not match the tree: unclassified "
                      f"{unclassified or 'none'}; matching nothing {stale or 'none'}")
    wanted = {path: entry for path, entry in entries.items() if exported(manifest, path)}
    for path, (obj, _) in manifest.get("transforms", {}).items():
        wanted[path] = (wanted[path][0], obj)  # parity compares the public bytes' blob
    return wanted


def blobs(repo: Path, objs: list[str]) -> dict[str, bytes]:
    out = git(repo, "cat-file", "--batch", stdin="".join(f"{o}\n" for o in objs).encode())
    found, at = {}, 0
    while at < len(out):
        end = out.index(b"\n", at)
        obj, kind, size = (out[at:end].decode().split(" ") + ["", ""])[:3]
        if kind != "blob":
            raise CannotRun(f"git cat-file answered {out[at:end].decode()!r}, not a blob")
        found[obj], at = out[end + 1:end + 1 + int(size)], end + 2 + int(size)
    return found


def sync(out: Path, wanted: dict, content: dict) -> tuple[int, int]:
    """Make `out` hold exactly `wanted`; return (written, removed)."""
    dirs = {str(parent) for p in wanted for parent in Path(p).parents}
    removed = 0
    for path in sorted(out.rglob("*"), reverse=True):  # children before parents
        rel = path.relative_to(out).as_posix()
        if rel.split("/")[0] == ".git":
            continue
        if path.is_symlink() or rel not in (dirs if path.is_dir() else wanted):
            path.rmdir() if path.is_dir() and not path.is_symlink() else path.unlink()
            removed += 1
    written = 0
    for rel, (mode, obj) in sorted(wanted.items()):
        target, data = out / rel, content[obj]
        if (target.is_file() and target.read_bytes() == data
                and target.stat().st_mode & 0o777 == MODES[mode]):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(MODES[mode])
        written += 1
    return written, removed


# A bead id, or a token shaped like one; `agentops-skills` is the skill pack's
# name before v3.0.0, which the release notes and the install pages still name,
# and stays. Part of a longer word (`agentops-redesign-design.md`) is not an id.
_BEAD_ID = re.compile(r"(?<![\w.-])(agentops-[a-z0-9]+(?:\.[0-9]+)*(?:\.\.[0-9]+)?)(?![\w-]|\.\w)")
NOT_IDS = {"agentops-skills"}
_GAP = r"(?:[\s,;/&\0]|and\b)*"  # what separates the ids of a list, e.g. "(a, b)"


def scrub_text(text: str) -> str:
    """Each id becomes \\0, then only a parenthesised list of \\0s and the space
    before a bare \\0 go, so text elsewhere (`f()`, `check .`) is untouched."""
    text = re.sub(r"[ \t]*\(no-bead:(?:[^()]|\([^()]*\))*\)", "", text)
    text = _BEAD_ID.sub(lambda m: m.group(0) if m.group(1) in NOT_IDS else "\0", text)
    text = re.sub(r"[ \t]*\(" + _GAP + r"\0" + _GAP + r"\)", "", text)
    text = re.sub(r"[ \t]*\0", "", text)
    return "\n".join(line.rstrip() for line in text.split("\n"))


# The public fence (scripts/commit-lint.sh, which the exported ci.yml runs on
# every pushed commit) ends a header in `(agentops-id | no-bead: reason)`; an
# id is private and a reason is free text that can name one, so a tag stands
# in for either. The header budget is 100 characters with `(#N)` stripped,
# and authors spend it against their suffix, so the first tag that fits is
# taken. The last is 12 characters, no longer than the shortest suffix the
# fence accepts, so every header it passed still fits.
TAGS = ("(no-bead: private tracker)", "(no-bead: private)", "(no-bead: p)")
HEADER_BUDGET = 100
_SUFFIX = re.compile(r" \((?:agentops-[a-z0-9]+(?:\.[0-9]+)*(?:, ?agentops-[a-z0-9]+(?:\.[0-9]+)*)*"
                     r"|no-bead: [^)]+)\)$")  # the fence's suffix, as commit-lint.sh writes it
_PR_SUFFIX = re.compile(r" \(#[0-9]+\)$")  # the forge's, kept last where it wrote it
# Evidence prose names beads by their bare short id ("c0e4 mutation-proven"),
# which no pattern tells from a word, so only the counts cross; the fence needs
# a top-level `Evidence: ` line on feat/fix, and the receipt stays private.
# The count that crosses is the suite's: the last top-level Evidence paragraph
# carrying one (a squash body holds one per commit, and the last measured the
# tree closest to the one exported), and in it the first count that states its
# skips or follows "suite", and is tied to no test file ("tests/test_x.py 7
# passed", "52 passed in tests/test_docs.py"). A bare "12 passed" or "284 tests
# passed" may be one file's or one repair's, so with no suite-shaped count
# anywhere the line crosses with none rather than a guessed one.
_COUNTS = re.compile(r"\b([0-9]+) (?:tests )?passed(?:(?:,| /|) ([0-9]+) skipped)?")
_FILE_AFTER = re.compile(r"\s*(?:\(|in\s|across\s)\s*\S*(?:/|\.py\b)")
EVIDENCE = "Evidence: {}the full receipt stays with the private Source commit"


def suite_count(paragraph: str) -> str | None:
    """The paragraph's first suite-shaped `N passed[, M skipped]`, normalised."""
    text = " ".join(paragraph.split())
    for m in _COUNTS.finditer(text):
        words = text[:m.start()].split()[-2:] or [""]
        word = words[-1].rstrip(":")  # "x.py, 4106 passed": the comma closes the file's clause
        tied = not word.endswith((",", ";")) and ("/" in word or word.endswith(".py"))
        if tied or _FILE_AFTER.match(text, m.end()):
            continue
        if m.group(2) or "suite" in " ".join(words).lower():
            return f"{m.group(1)} passed" + (f", {m.group(2)} skipped" if m.group(2) else "")
    return None


def scrub_subject(subject: str) -> str:
    pr = _PR_SUFFIX.search(subject := subject.strip())
    head, tail = (subject[:pr.start()], pr.group(0)) if pr else (subject, "")
    suffix = _SUFFIX.search(head)
    if not suffix:  # Merge, Revert, a bump: no suffix to stand in for
        return scrub_text(subject).strip()
    stem = scrub_text(head[:suffix.start()]).strip()
    tag = next((t for t in TAGS if len(f"{stem} {t}") <= HEADER_BUDGET), TAGS[-1])
    return f"{stem} {tag}{tail}"


def scrub_message(message: str, sha: str) -> str:
    subject, _, body = message.strip("\n").partition("\n")
    kept, evidence, nested, top = [], [], False, False
    for line in body.split("\n"):  # an Evidence paragraph runs to the next blank line
        nested = line.lstrip().startswith("Evidence:") or (nested and bool(line.strip()))
        if line.startswith("Evidence:"):
            evidence.append("")
        top = line.startswith("Evidence:") or (top and bool(line.strip()))
        if top:
            evidence[-1] += line + "\n"
        elif not nested:
            kept.append(line)
    body = re.sub(r"(?m)^[ \t]*[-*]\n", "", scrub_text("\n".join(kept)) + "\n")  # id-only bullets
    body = re.sub(r"\n{3,}", "\n\n", body).strip("\n")
    receipt = ""
    if evidence:  # a top-level paragraph: the one place commit-lint looks
        counts = next(filter(None, map(suite_count, reversed(evidence))), None)
        receipt = EVIDENCE.format(f"{counts}; " if counts else "")
    parts = (scrub_subject(subject), body, receipt, f"Source: {sha}")
    return "\n\n".join(p for p in parts if p) + "\n"


def export(repo: Path, rev: str, out: Path) -> tuple[str, int, int, int]:
    # DIR may sit inside the work tree, never over it, nor over or inside the git dirs
    target, work = out.resolve(), repo.resolve()
    for d in git(repo, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir",
                 "--show-toplevel").decode().splitlines():
        guarded = Path(d).resolve()
        if guarded.is_relative_to(target) or (guarded != work and target.is_relative_to(guarded)):
            raise CannotRun(f"--out {out} would overwrite the repository's {guarded}")
    sha = git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
    manifest = load_manifest(repo, sha)
    wanted, public = selected(manifest, tree_entries(repo, sha)), dict(manifest["transforms"].values())
    content = {**blobs(repo, sorted({obj for _, obj in wanted.values()} - set(public))), **public}
    message = scrub_message(git(repo, "log", "-1", "--format=%B", sha).decode(), sha)
    out.mkdir(parents=True, exist_ok=True)
    return message, len(wanted), *sync(out, wanted, content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sha", default="HEAD")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    try:
        message, files, written, removed = export(args.repo, args.sha, args.out)
    except Exception as exc:  # anything but a manifest refusal is a could-not-run
        print(f"public export {'refused' if isinstance(exc, Refused) else 'cannot run'}: "
              f"{exc}", file=sys.stderr)
        return 1 if isinstance(exc, Refused) else 2
    change = f"{written} written, {removed} removed" if written or removed else "no change"
    print(f"public export: {files} files in {args.out} ({change})", file=sys.stderr)
    sys.stdout.write(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
