"""`warden take --from DIR`: the gate job takes the verify job's results.

The gate job downloads the verify artifact to DIR and runs this in its
checkout. `.warden/out` there is derived state a pull request can still
commit, symlinks included, so it is replaced by a directory made here. Below
DIR nothing is followed through a symlink: each regular file named
`verify-result.json` whose parent directory, at any depth below DIR, has a
name ending in `-verify` is copied to `.warden/out/<that name>/`. Everything
else is skipped, a symlinked result file silently. Two results under one
parent name: the later in sorted walk order wins, as the shell step it
replaces did.

Exit 2, the gate DID NOT RUN, when `.warden` is a symlink or not a directory,
when DIR is a symlink or not a directory, when a directory below DIR or a
candidate result cannot be read (the message names the path and the OS
error), or when nothing was taken.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

RESULT = "verify-result.json"


def _refuse(why: str) -> int:
    print(f"warden take: {why}; the gate DID NOT RUN.", file=sys.stderr)
    return 2


def _read_regular(path: str) -> bytes | None:
    """The bytes of a regular file, or None for anything else: opened without
    following a symlink and without blocking on a FIFO, then checked on the
    open descriptor. A regular file that cannot be opened or read raises
    OSError: it is not skipped."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        # A symlink (ELOOP) or a socket refuses the open; lstat tells those
        # from a regular file this runner may not read.
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as f:
            return f.read()
    finally:
        os.close(fd)


def _raise(error: OSError) -> None:
    raise error


def run(root: Path, source: Path) -> int:
    warden = root / ".warden"
    if warden.is_symlink() or (warden.exists() and not warden.is_dir()):
        return _refuse(".warden in the checkout is a symlink or not a directory, "
                       "so the verify results cannot be written safely")
    try:
        warden.mkdir(exist_ok=True)
        out = warden / "out"
        if out.is_symlink() or (out.exists() and not out.is_dir()):
            out.unlink()
        elif out.exists():
            shutil.rmtree(out)
        out.mkdir()
    except OSError as e:
        return _refuse(f"could not make a fresh .warden/out ({e})")
    if source.is_symlink():
        return _refuse(f"{source} is a symlink, so the verify results cannot be read safely")
    if not source.is_dir():
        return _refuse(f"{source} is not a directory")

    taken = 0
    try:
        # os.walk skips a directory it cannot list unless told otherwise.
        for dirpath, dirnames, filenames in os.walk(source, onerror=_raise):
            dirnames.sort()
            here = Path(dirpath)
            if here == source or not here.name.endswith("-verify") or RESULT not in filenames:
                continue
            data = _read_regular(os.path.join(dirpath, RESULT))
            if data is None:
                continue
            try:
                (out / here.name).mkdir(exist_ok=True)
                (out / here.name / RESULT).write_bytes(data)
            except OSError as e:
                return _refuse(f"could not write .warden/out/{here.name}/{RESULT} ({e})")
            print(f"warden take: took {here.name}/{RESULT} from {here}")
            taken += 1
    except OSError as e:
        return _refuse(f"could not read {e.filename} ({e.strerror or e})")
    if not taken:
        return _refuse("the verify job uploaded no verify result")
    return 0
