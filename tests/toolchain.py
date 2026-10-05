"""What this suite shells out to, and what an absent one is allowed to cost.

THE FAILURE THIS EXISTS FOR. `tests/test_init.py` enrolls a
fixture repo per language and runs the verify scope `warden init` wrote for
it, so the suite executes `go`, `npm` and `uv` for real. Nothing declared
that. CI was green because the GitHub-hosted image preinstalls Go and Node,
and a developer's first clone was red with five `exit 127` cells whose only
stated reason was `assert 1 == 0`. That is the exact shape the newcomer bar
measures ("a newcomer lands a small change here in under an hour") and the
exact shape `CLAUDE.md` admits to ("nobody outside this org has enrolled from
the docs alone"). A suite that fails on clone for undeclared reasons teaches
one lesson, and it is that the project is broken.

SKIP OR FAIL — the question decided here, deliberately.

The corpus's ruling has two halves: a REQUIRED tool
absent is a RED suite, an OPTIONAL one absent is a SKIP whose reason names the
gap. The half that gets misread is what puts a tool in the first class. It is
NOT "the suite uses it". The ruling's own words for its REQUIRED shells are "the ones
both callers actually run under AND EVERY MACHINE HAS" — `sh` and `bash`. Their
absence is red because it is coverage the environment CONTRACTED to provide and
then did not; the machine is broken, and saying so is the correct report.

`go` and `npm` are not that. This platform is Python. A contributor's fresh Mac
has neither, and it is not broken — it never promised them.

#276 made the cage's `go` cell FAIL rather than skip, and that ruling is
correct AND it does not reach here, for a reason worth stating rather than
assuming: the cage DECLARES AND SUPPLIES its toolchain. `.cage/cage.toml`
carries `[toolchain].path = ["/usr/local/go/bin"]` beside `require`. That is a
contract, so an absence inside it is a breach and red is the honest word. A
laptop signs no such contract, so the same red there is not a breach report —
it is a false accusation against the machine, and the first thing the project
says to a newcomer.

So the split is by CONTRACT, not by tool:

  - STRICT (`NIGHTGATE_REQUIRE_TOOLCHAIN=1`, which CI sets and CI also honours
    by installing every declared entry with a pinned action) — an absent tool
    is a NAMED FAILURE. The go cell is the only executable proof in this suite
    that the enrollment surface works for a language the platform is not
    written in, which is the portability claim itself; where the environment
    promised the toolchain, losing that proof must cost a red run, never a
    green one with a quiet line in the summary.
  - LENIENT (the default, a developer's machine) — an absent tool is a NAMED
    SKIP carrying the install command. `pytest -q` says what this run did not
    prove and how to close it, and everything else still runs.

WHAT BOTH HALVES SHARE, and what this module is about: the absence is NAMED.
Never a bare 127. The collection count never moves either — a skip and a
failure are both collected cells, so the suite cannot get quietly smaller,
which is the bug the ruling closed.
"""

import os
import shutil
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PYPROJECT = ROOT / "pyproject.toml"

#: The environment variable that says "this machine promised the toolchain".
#: CI sets it; a laptop does not. Read through `strict()`, never directly.
STRICT_ENV = "NIGHTGATE_REQUIRE_TOOLCHAIN"

#: The pyproject table the declared toolchain lives under: `[tool.<key>.toolchain]`.
TOOL_KEY = "nightgate"

# THE OLD SPELLINGS, read as aliases for one release. The product was renamed
# to Nightgate; a machine, a cage or a shell that already sets the old
# variable, or a checkout whose pyproject still carries the old table, keeps
# working until SUNSET_RELEASE, and says so every run (`deprecations()`, which
# tests/conftest.py prints in the terminal summary). The new spelling wins
# when both are present, so an explicit new-name opt-out is never overridden
# by an old export left in a profile. cage/run.sh still exports the old
# variable name; that file is outside what an agent may edit, so its rename is
# a founder-directed follow-up, and this alias is what keeps caged runs strict
# meanwhile.
LEGACY_STRICT_ENV = "AGENTOPS_REQUIRE_TOOLCHAIN"
LEGACY_TOOL_KEY = "agentops"
#: The release in which the old spellings stop being read.
SUNSET_RELEASE = "v4.0.0"

_INSTALL_HINT = {
    "go": "https://go.dev/dl/ (or `brew install go`)",
    "npm": "https://nodejs.org/ (or `brew install node`)",
    "uv": "https://docs.astral.sh/uv/getting-started/installation/",
    "java": "https://adoptium.net/ (or `brew install openjdk@17`)",
    "mvn": "https://maven.apache.org/download.cgi (or `brew install maven`)",
    "gradle": "https://gradle.org/install/ (or `brew install gradle`)",
}


def _toolchain_table() -> tuple[str, dict]:
    """Which `[tool.<key>.toolchain]` table pyproject.toml declares, and it.

    The new key when present; the old one only when the new one is absent;
    an empty table under the new key when neither is, so a caller's "declares
    nothing" failure names the spelling to write.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    tool = data.get("tool", {})
    for key in (TOOL_KEY, LEGACY_TOOL_KEY):
        if "toolchain" in tool.get(key, {}):
            return key, tool[key]["toolchain"]
    return TOOL_KEY, {}


def declared() -> tuple[str, ...]:
    """`[tool.nightgate.toolchain].require` from pyproject.toml.

    `[tool.agentops.toolchain]` is read in its place until SUNSET_RELEASE
    when the new table is absent (`deprecations()` says so).

    Read through `tomllib` rather than a regex over the text, for the reason
    `tests/test_self_cage.py` gives about the cage's toml: a text parse keyed
    on one quote style reads an empty list the moment someone renormalises the
    file, and the guard then fails on "declares nothing" instead of on the
    property it exists for.
    """
    key, tools = _toolchain_table()
    require = tools.get("require", [])
    if not isinstance(require, list) or not all(isinstance(t, str) for t in require):
        raise AssertionError(
            f"[tool.{key}.toolchain].require must be a list of strings, "
            f"got {require!r}")
    return tuple(require)


def deprecations() -> list[str]:
    """One line per old spelling this run is reading, naming the sunset.

    Empty when only the new spellings are in use. A variable set to the empty
    string is not a declaration (`strict()` skips it), so it is not reported.
    """
    lines = []
    if not os.environ.get(STRICT_ENV) and os.environ.get(LEGACY_STRICT_ENV):
        lines.append(
            f"DEPRECATED: {LEGACY_STRICT_ENV} is read as {STRICT_ENV} until "
            f"{SUNSET_RELEASE}, when the old name stops being read. Set "
            f"{STRICT_ENV} instead.")
    if _toolchain_table()[0] == LEGACY_TOOL_KEY:
        lines.append(
            f"DEPRECATED: pyproject.toml's [tool.{LEGACY_TOOL_KEY}.toolchain] "
            f"is read as [tool.{TOOL_KEY}.toolchain] until {SUNSET_RELEASE}, "
            f"when the old table stops being read. Rename the table.")
    return lines


def missing(*tools: str) -> tuple[str, ...]:
    """Which of `tools` do not resolve on PATH, in the order given."""
    return tuple(t for t in tools if shutil.which(t) is None)


def strict(environ=None) -> bool:
    """Whether this machine promised the declared toolchain.

    Explicit in BOTH directions before the ambient fallback, because both
    overrides have a real caller: the regression tests here need to exercise
    the strict half on a laptop, and a contributor debugging one test under a
    stripped PATH needs a way to say "I know, skip them" without editing a
    file. Unset falls back to the old name (`LEGACY_STRICT_ENV`, until
    SUNSET_RELEASE) and then to `CI`, which every forge sets, so the default
    on the only machine that made the promise is the strict one.

    `environ` defaults to this process's environment; a caller judging the
    environment some OTHER process saw (a caged session's, in
    tests/test_cage_runner.py) passes that mapping instead.
    """
    env = os.environ if environ is None else environ
    for name in (STRICT_ENV, LEGACY_STRICT_ENV, "CI"):
        raw = env.get(name)
        if raw is None or raw == "":
            continue
        return raw.strip().lower() not in ("0", "false", "no", "off")
    return False


def reason(absent: tuple[str, ...]) -> str:
    """The sentence a reader gets instead of `/bin/sh: go: command not found`."""
    names = ", ".join(absent)
    hints = "; ".join(f"{t}: {_INSTALL_HINT.get(t, 'install it')}" for t in absent)
    return (f"TOOLCHAIN GAP: {names} not found on PATH. This repo's suite "
            f"shells out to it — tests/test_init.py enrolls a fixture repo and "
            f"RUNS the verify scope `warden init` wrote, so the enrollment "
            f"surface is proven against a real {names} or not at all. "
            f"pyproject.toml's [tool.{TOOL_KEY}.toolchain].require declares it. "
            f"Install: {hints}.")


def require(*tools: str) -> None:
    """Call FIRST in any test that shells out to `tools`.

    Under `strict()` an absent tool fails the cell by name; otherwise it skips
    it by name. Either way the reader is told which binary is missing and how
    to get it, which is the whole point — the old behaviour was the
    command running anyway and the shell answering `exit 127` five times.

    Raising rather than returning a marker is deliberate: a decorator would
    have to decide at COLLECTION time, and `require` is also wanted inside
    parametrized bodies where the tool depends on the param.
    """
    absent = missing(*tools)
    if not absent:
        return
    text = reason(absent)
    if strict():
        pytest.fail(
            text + f" This run is STRICT ({STRICT_ENV}/CI is set), so the "
            "machine declared it would supply the toolchain and did not — "
            "that is a broken environment, not a smaller suite.",
            pytrace=False)
    pytest.skip(text + " This is a REPORTED gap, not a smaller suite: the cell "
                       "is still collected, and `pytest -rs` prints this line.")
