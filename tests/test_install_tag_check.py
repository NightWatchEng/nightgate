"""The install pages' release-tag check answers the question they ask of it.

README.md and docs/wiki/Installation.md tell a reader who cannot install a tag
to run `git ls-remote` and read its output. Every documented `git ls-remote`
is executed here against a local repository holding a lightweight tag, an
annotated tag and no third tag, so a check that is blind to one kind of tag
fails here rather than telling a reader a published release is missing.

The form `refs/tags/vX^{}` asks for the peeled ref, which only an annotated tag
has: for a lightweight tag it printed nothing while the tag was published.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
from conftest import _AMBIENT_GIT_CONFIG

from warden import __version__

ROOT = Path(__file__).resolve().parent.parent
PAGES = {"README.md": ROOT / "README.md",
         "Installation.md": ROOT / "docs" / "wiki" / "Installation.md"}
REMOTES = ("https://github.com/NightWatchEng/nightgate",
           "git@github.com:NightWatchEng/nightgate.git")
TAG = f"v{__version__}"
LS_REMOTE = re.compile(r"git ls-remote[^`\n]*")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
        env={**os.environ, **_AMBIENT_GIT_CONFIG}).stdout


def _documented_checks(page: Path) -> list[list[str]]:
    """Every `git ls-remote` on the page, inline or fenced, split into words."""
    return [shlex.split(m.group(0)) for m in LS_REMOTE.finditer(page.read_text())]


@pytest.fixture(scope="module")
def remote(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("platform")
    _git(work, "init", "-q", "-b", "main")
    (work / "f").write_text("x\n")
    _git(work, "add", "f")
    _git(work, "commit", "-qm", "base")
    _git(work, "tag", "v-lightweight")
    _git(work, "tag", "-a", "-m", "release", "v-annotated")
    return work


def _run(argv: list[str], remote: Path, tag: str) -> subprocess.CompletedProcess:
    local = [str(remote) if word in REMOTES else word.replace(TAG, tag) for word in argv]
    assert local != argv, f"{shlex.join(argv)} names neither the platform remote nor {TAG}"
    return subprocess.run(local, capture_output=True, text=True, timeout=60,
                          env={**os.environ, **_AMBIENT_GIT_CONFIG})


INSTALL = re.compile(r"uv tool install git\+https://github\.com/NightWatchEng/nightgate@(v[^\s`]+)")
VERSION_LINE = re.compile(r"`warden ([0-9][0-9.]*[0-9])`")


@pytest.mark.parametrize("page", sorted(PAGES))
def test_each_install_page_names_the_current_release_in_its_install_and_version_lines(page):
    """Every install line and every `warden X.Y.Z` a page prints names this
    checkout's version, so a release bump that misses one is refused."""
    text = PAGES[page].read_text()
    tags, versions = INSTALL.findall(text), VERSION_LINE.findall(text)
    assert tags and versions, f"{page} shows no install line or no version line"
    assert set(tags) == {TAG}, (page, tags)
    assert set(versions) == {__version__}, (page, versions)


@pytest.mark.parametrize("page", sorted(PAGES))
def test_each_install_page_documents_a_tag_check_for_the_current_version(page):
    checks = _documented_checks(PAGES[page])
    assert checks, f"{page} documents no git ls-remote tag check"
    for argv in checks:
        assert any(TAG in word for word in argv), (page, argv)
        assert any(word in REMOTES for word in argv), (page, argv)


@pytest.mark.parametrize("kind", ["lightweight", "annotated"])
@pytest.mark.parametrize("page", sorted(PAGES))
def test_the_documented_tag_check_prints_one_line_for_a_published_tag_of_either_kind(
        page, kind, remote):
    tag = f"v-{kind}"
    for argv in _documented_checks(PAGES[page]):
        result = _run(argv, remote, tag)
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        assert len(lines) == 1 and lines[0].endswith(f"\trefs/tags/{tag}"), (
            f"{page}: `{shlex.join(argv)}` printed {result.stdout!r} for a published "
            f"{kind} tag, and the page says no output means the tag is not published")


@pytest.mark.parametrize("page", sorted(PAGES))
def test_the_documented_tag_check_prints_nothing_for_an_unpublished_tag(page, remote):
    for argv in _documented_checks(PAGES[page]):
        result = _run(argv, remote, "v-unpublished")
        assert (result.returncode, result.stdout) == (0, ""), (page, result)


# What git prints over each transport when the reader lacks access, as observed
# against the private platform repository: https with no credentials prompts for
# a username, https with an invalid or expired token is refused authentication,
# https with valid credentials for an account without access gets a 404, ssh
# with no key GitHub accepts is refused, and ssh with a key whose account has no
# access gets the 404's ssh form.
ACCESS_FAILURES = ("Username for 'https://github.com':", "Invalid username or token",
                   "Authentication failed", "Repository not found",
                   "Permission denied (publickey)", "ERROR: Repository not found.")


@pytest.mark.parametrize("failure", ACCESS_FAILURES)
@pytest.mark.parametrize("page", sorted(PAGES))
def test_each_install_page_names_the_access_failure_git_prints(page, failure):
    text = " ".join(PAGES[page].read_text().split())
    assert failure in text, f"{page} does not name {failure!r}"
