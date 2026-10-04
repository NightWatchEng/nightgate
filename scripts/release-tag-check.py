#!/usr/bin/env python3
"""Refuse a release tag whose name is not `v` + warden.__version__.

Every consumer's repo.yaml `platform.pin` is compared against __version__, so
a tag cut without bumping the version is refused by every consumer that pins
it. Usage: release-tag-check.py <tag> [--root DIR]. Exit 0 when they agree,
1 when they differ, 2 when the check cannot run.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path


def tag_version(tag: str) -> str | None:
    if not tag.startswith("v") or len(tag) == 1:
        return None
    return tag[1:]


def package_version(root: Path) -> str:
    root = root.resolve()
    sys.path.insert(0, str(root))
    try:
        sys.modules.pop("warden", None)
        module = importlib.import_module("warden")
    finally:
        sys.path.remove(str(root))
    where = Path(module.__file__).resolve().parent
    if where != root / "warden":
        raise ImportError(f"warden imported from {where}, not from {root}")
    return module.__version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag")
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)

    wanted = tag_version(args.tag)
    if wanted is None:
        print(f"release tag check: {args.tag!r} is not a v<version> tag",
              file=sys.stderr)
        return 2
    try:
        version = package_version(args.root)
    except (ImportError, AttributeError) as exc:
        print(f"release tag check: cannot read warden.__version__ under "
              f"{args.root}: {exc}", file=sys.stderr)
        return 2
    if wanted != version:
        print(f"release tag check: tag {args.tag} names {wanted} but "
              f"warden.__version__ is {version}; bump warden/__init__.py "
              "and tag again", file=sys.stderr)
        return 1
    print(f"release tag check: tag {args.tag} matches warden {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
