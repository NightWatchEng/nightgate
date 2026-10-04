"""The wiki build + publish stencil: docs/wiki/ -> build/wiki/ -> the wiki tab.

The wiki tab is a rendered mirror of `docs/wiki/`, published one-way by CI.
That makes `scripts/build-wiki.sh` and `scripts/publish-wiki.sh` gate surface:
what they emit is what a reader who has never cloned the repo sees, and
nothing downstream re-checks it. The failure modes with a test each:

* a page silently missing from the generated `_Sidebar.md` — unreachable
  from the wiki's only nav, because GitHub renders no nav of its own;
* a link still pointing at a `.md` path, which on the wiki is a 404 (wiki
  pages have no extension, and `../design/memory.md` is not mirrored at all);
* the sidebar falling back to GitHub's alphabetical order, which opens with
  `Adopting` and sorts the start-here page last;
* the publisher deleting a page it does not own, or stranding one it retired.

The scripts run for real here rather than being reimplemented in Python: a
reimplementation would test the test. Build output lands in `build/`, which is
gitignored — `test_build_output_is_gitignored` pins that, because a committed
derived tree makes every branch in this repo conflict.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from conftest import _AMBIENT_GIT_CONFIG

ROOT = Path(__file__).parent.parent
WIKI = ROOT / "docs" / "wiki"
SCRIPT = ROOT / "scripts" / "build-wiki.sh"
PUBLISH = ROOT / "scripts" / "publish-wiki.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "wiki-sync.yml"
MANIFEST = ".wiki-managed"

# Every relative link target in the built pages.
LINK_RE = re.compile(r"\]\((?!https?:|#)([^)\s]+?)\)")

# A CLOSED env — os.environ is replaced, not extended — so conftest's session
# neutralisation cannot reach the git this script runs, and the ambient config
# layers have to come from the same one derivation every other closed-env
# fixture uses.
ENV = {**_AMBIENT_GIT_CONFIG, "PATH": "/usr/bin:/bin:/usr/local/bin"}
# The default the script falls back to when GITHUB_REPOSITORY is unset,
# which ENV deliberately leaves out.
REPO_URL = "https://github.com/NightWatchEng/agentops"


def _build(out: Path, src: Path | None = None):
    env = dict(ENV, OUT=str(out))
    if src is not None:
        env["SRC"] = str(src)
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env,
                          capture_output=True, text=True)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Run the real builder once over the real sources."""
    out = tmp_path_factory.mktemp("wiki-out") / "wiki"
    proc = _build(out)
    assert proc.returncode == 0, (
        f"scripts/build-wiki.sh failed:\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}")
    return out


def _sources():
    return sorted(p.stem for p in WIKI.glob("*.md") if not p.name.startswith("_"))


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def test_build_produces_one_page_per_source_plus_sidebar(built):
    """One in, one out. A page that does not get built is a page that
    silently stops being published while its source keeps passing review."""
    produced = sorted(p.stem for p in built.glob("*.md")
                      if not p.name.startswith("_"))
    assert produced == _sources(), (
        "build/wiki pages do not match docs/wiki sources 1:1 — "
        f"missing {sorted(set(_sources()) - set(produced))}, "
        f"unexpected {sorted(set(produced) - set(_sources()))}")
    assert (built / "_Sidebar.md").is_file(), (
        "no _Sidebar.md — GitHub then renders no nav at all, and every page "
        "is reachable only from Home")


def test_every_source_page_appears_in_the_generated_sidebar(built):
    """The sidebar is the wiki's only nav; a page absent from it is a page
    a reader can reach only by guessing its URL.

    Mutation-proved twice: (1) adding a page with no row in Home's index
    table — which is what the builder derives the order from — makes the
    builder exit non-zero and turns this red; (2) dropping an entry from the
    sidebar AFTER the completeness check (the "hand-listed nav someone forgot
    to extend" bug, build still green) turns this red on its own assertion.
    """
    sidebar = (built / "_Sidebar.md").read_text()
    linked = set(re.findall(r"\]\(([^)]+)\)", sidebar))
    missing = [s for s in _sources() if s not in linked]
    assert not missing, (
        f"_Sidebar.md does not link: {missing} — add a row to the Home page "
        "table (the builder derives the sidebar order from it)")


def test_sidebar_groups_pages_under_the_home_tables_section_labels(built):
    """Home's table carries linkless section rows ("**Usage**") so the index
    reads as Introduction / Usage / Reference rather than one twenty-row
    list. A builder that skips every linkless row publishes the flat list the
    grouping exists to replace.

    Two things are asserted. A label must reach the sidebar, and it must
    arrive AS A HEADING: bash `read` treats TAB as IFS whitespace, so an
    empty field silently shifts every field left by one and the heading
    renders as `- [section](Introduction)`, a link to a page that does not
    exist. A heading that renders as a dead link is worse than no grouping.
    """
    home = (WIKI / "Home.md").read_text()
    region = home[home.index("| Page | System |"):]
    region = region[:region.find("\n## ")]
    labels = [m.group(1) for line in region.splitlines()
              if line.startswith("|") and ".md)" not in line
              for m in [re.search(r"\*\*([^*]+)\*\*", line)] if m]
    assert labels, "Home's table declares no section labels — guard is vacuous"

    sidebar = (built / "_Sidebar.md").read_text()
    for label in labels:
        assert f"**{label}**" in sidebar, (
            f"section label {label!r} never reached the sidebar as a heading")
        assert f"]({label})" not in sidebar, (
            f"section label {label!r} rendered as a LINK to a page that does "
            "not exist — the field-shift bug is back")


def test_no_output_link_points_at_an_unmirrored_md_path(built):
    """On the wiki, `[x](Foo.md)` 404s (pages have no extension) and
    `[x](../design/memory.md)` 404s twice over — `docs/design/` is not mirrored.

    Both classes are the same defect from a reader's seat: a dead link on
    the published surface a consumer enrolls from.
    """
    dead = []
    for page in sorted(built.glob("*.md")):
        for target in LINK_RE.findall(page.read_text()):
            if target.endswith(".md") or "/" in target:
                dead.append(f"{page.name} -> {target}")
    assert not dead, (
        "built pages still carry links that do not resolve on the wiki "
        f"(a .md suffix, or a path into the unmirrored repo tree): {dead}")


def test_out_of_tree_links_become_absolute_repo_urls(built):
    """`../design/` and anything under it are NOT mirrored, so those links have
    to leave the wiki entirely. Rewritten to absolute repo URLs they land on
    the file; left relative they 404.

    Both shapes are asserted because they take different rewrite arms — a
    directory target goes to /tree/, a file to /blob/ — and the arms were
    written separately.
    """
    home = (built / "Home.md").read_text()
    assert "https://github.com/NightWatchEng/agentops/tree/main/docs/design/" in home, (
        "Home.md's design-records link was not rewritten to an absolute URL")
    memory = (built / "Memory.md").read_text()
    assert ("https://github.com/NightWatchEng/agentops/blob/main/docs/design/memory.md"
            in memory), "an out-of-tree FILE link was left relative"


def test_repo_root_links_become_absolute_repo_urls(tmp_path):
    """The `](../../…)` arms, which no live page exercises today.

    Coverage that depends on a page happening to carry a link is coverage
    that leaves when the page is edited, so this drives the builder over a
    fixture instead of the live pages.

    Both root arms are asserted — a directory target goes to /tree/, a file to
    /blob/.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "Home.md").write_text(
        "# H\n\n| Page | System |\n|---|---|\n| [P](P.md) | p |\n\n"
        "Root file: [readme](../../README.md). "
        "Root dir: [examples](../../examples/).\n")
    (src / "P.md").write_text("# P\n\nSee [graph](../../graph.yaml).\n")

    out = tmp_path / "out"
    proc = _build(out, src=src)
    assert proc.returncode == 0, proc.stderr

    home = (out / "Home.md").read_text()
    assert f"{REPO_URL}/blob/main/README.md" in home, (
        "the repo-root FILE arm left `../../README.md` relative")
    assert f"{REPO_URL}/tree/main/examples/" in home, (
        "the repo-root DIRECTORY arm left `../../examples/` relative")
    assert f"{REPO_URL}/blob/main/graph.yaml" in (out / "P.md").read_text()


def test_cross_page_anchors_survive_the_rewrite(tmp_path):
    """`[x](Foo.md#bar)` must become `[x](Foo#bar)`, not `[x](Foo)`. Dropping
    the fragment still links, but stops landing where the author pointed.

    A SAME-PAGE anchor `](#bar)` cannot exercise this: the rewriter cannot
    touch it under any variant of the expression — and no source page uses
    `Page.md#anchor`, so nothing in the corpus exercises the `#` branch
    either, and a fragment-dropping expression would leave the suite green.
    This drives it from a fixture, so the branch is exercised on every run.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "Home.md").write_text(
        "| Page | System |\n|------|--------|\n"
        "| [Target](Target.md) | t |\n"
        "| [Other](Other.md) | o |\n")
    (src / "Target.md").write_text("# T\n")
    (src / "Other.md").write_text(
        "cross [a](Target.md#the-section) plain [b](Target.md) "
        "same-page [c](#local)\n")
    out = tmp_path / "out"
    proc = _build(out, src)
    assert proc.returncode == 0, proc.stderr
    other = (out / "Other.md").read_text()
    assert "[a](Target#the-section)" in other, (
        f"a cross-page anchor was not preserved: {other!r}")
    assert "[b](Target)" in other
    assert "[c](#local)" in other, "a same-page anchor must be left alone"


def test_page_names_with_regex_metacharacters_are_escaped(tmp_path):
    """Page basenames interpolated raw into both halves of a sed `s///`
    corrupt links: a page named `Q&A` publishes `[QA](Q](Q&A.md)A)` — an
    unescaped `&` in the replacement expands to the whole match — and a page
    named `V1.2-Notes` silently RETARGETS a link to `V1x2-Notes.md`, because
    `.` is a metacharacter. Both corruptions exit 0.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "Home.md").write_text(
        "| Page | System |\n|------|--------|\n"
        "| [QA](Q&A.md) | q |\n| [V](V1.2-Notes.md) | v |\n"
        "| [D](V1x2-Notes.md) | d |\n| [P](Plain.md) | p |\n")
    for name in ("Q&A", "V1.2-Notes", "V1x2-Notes"):
        (src / f"{name}.md").write_text("x\n")
    (src / "Plain.md").write_text(
        "amp [q](Q&A.md) dot [v](V1.2-Notes.md) decoy [d](V1x2-Notes.md)\n")
    out = tmp_path / "out"
    proc = _build(out, src)
    assert proc.returncode == 0, proc.stderr
    plain = (out / "Plain.md").read_text()
    assert "[q](Q&A)" in plain, f"`&` corrupted the replacement: {plain!r}"
    assert "[v](V1.2-Notes)" in plain
    assert "[d](V1x2-Notes)" in plain, (
        f"an unescaped `.` retargeted a link to the wrong page: {plain!r}")


def test_a_page_name_containing_the_sed_delimiter_is_handled(tmp_path):
    """Escaping cannot save the DELIMITER. With `|` as the `s` delimiter, a
    page named `A|B` emits `\\|`, which BSD sed hands to the ERE engine as
    ALTERNATION — corrupting the link to the unrelated page `B` — while GNU
    sed rejects the expression outright, so the same tree behaves differently
    on a laptop and on the runner. The per-page expressions use `/`: it is
    the one printable character a filename cannot contain, so no basename can
    close the expression early.
    """
    src = tmp_path / "src"
    src.mkdir()
    # A literal `|` in the basename — the byte a `|` delimiter collides with.
    # Home's table is pipe-delimited, so such a page cannot be listed there;
    # the sidebar order comes from the rows that CAN be, and the rewrite is
    # what this test drives. `Plain` and `B` carry the links being checked.
    (src / "Home.md").write_text(
        "| Page | System |\n|------|--------|\n"
        "| [B](B.md) | b |\n| [P](Plain.md) | p |\n")
    (src / "B.md").write_text("x\n")
    (src / "A|B.md").write_text("x\n")
    (src / "Plain.md").write_text("pipe [x](A|B.md) other [y](B.md)\n")
    out = tmp_path / "out"
    proc = _build(out, src)
    # The unlisted `A|B` page is a hard stop by design (no Home row), but the
    # rewrite has already run — so a delimiter bug would ship corrupted bytes
    # even though the build later exits non-zero.
    plain = (out / "Plain.md").read_text()
    assert plain.strip() == "pipe [x](A|B) other [y](B)", (
        f"the delimiter leaked into the expression: {plain!r}")
    assert "A|B" in proc.stderr, (
        "a page that Home's pipe-delimited table cannot list must still be "
        f"named by the completeness failure:\n{proc.stderr}")


def test_home_rows_may_carry_an_anchor(tmp_path):
    """The builder's awk row parser and
    `test_home_table_lists_every_wiki_source_page`'s regex must accept the
    same spellings of a row. A row written `[X](X.md#sec)` that is valid to
    the test but invisible to the builder hard-stops the build, telling the
    author to add a row that is already there.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "Home.md").write_text(
        "| Page | System |\n|------|--------|\n"
        "| [Anchored](Anchored.md#a-section) | a |\n")
    (src / "Anchored.md").write_text("x\n")
    out = tmp_path / "out"
    proc = _build(out, src)
    assert proc.returncode == 0, (
        "a Home row carrying an anchor was invisible to the sidebar parser:\n"
        f"{proc.stderr}")
    sidebar = (out / "_Sidebar.md").read_text()
    assert "](Anchored)" in sidebar, (
        f"the nav link kept the section anchor instead of the page: {sidebar!r}")


def test_sidebar_order_is_explicit_not_alphabetical(built):
    """GitHub sorts wiki nav alphabetically when no _Sidebar.md is present,
    which opens with `Adopting`, files `Home` between `Graph-Layer` and
    `Memory`, and sorts `Why-This-Exists` — the start-here page — last.

    `ladder == sorted(ladder)` alone cannot distinguish the generated order
    from the fallback, which ALSO satisfies it (`Architecture-1` <
    `Architecture-4`).

    Nor can `order != sorted(order)`: the builder pins `Home` first, and
    `Home` is not alphabetically first, so the whole list can never equal its
    own sort no matter what the rest does. The comparison has to skip the
    pinned entry to mean anything, so it runs over the DERIVED tail. Forcing
    the sidebar through `sort` fails on that line.
    """
    order = re.findall(r"\]\(([^)]+)\)", (built / "_Sidebar.md").read_text())
    assert order[0] == "Home", f"Home is not first in the sidebar: {order[:3]}"
    tail = order[1:]
    assert tail != sorted(tail), (
        "the sidebar below the pinned Home entry is in alphabetical order — "
        f"indistinguishable from the fallback it exists to replace: {tail}")
    assert order[1] == "Why-This-Exists", (
        f"the start-here page does not lead the nav: {order[:3]}")
    ladder = [p for p in order if p.startswith("Architecture-")]
    assert ladder == sorted(ladder), f"C4 ladder is out of zoom order: {ladder}"
    assert order.index("Architecture") < order.index("Architecture-1-System-Context"), (
        "the Architecture hub must precede the levels it zooms into")


def test_build_output_is_gitignored():
    """Derived files are never tracked in this repo — and the ignore is
    anchored to the repo root, so it does not swallow a `build/` directory
    somewhere else that someone means to track."""
    ignored = subprocess.run(["git", "check-ignore", "-q", "build/wiki/Home.md"],
                             cwd=ROOT).returncode
    assert ignored == 0, "build/ is not gitignored — rendered wiki output is derived"
    nested = subprocess.run(
        ["git", "check-ignore", "-q", "examples/hello-svc/build/keep.txt"],
        cwd=ROOT).returncode
    assert nested != 0, (
        "the build/ ignore is unanchored: it swallows a nested build "
        "directory too, which is wider than the reason given for it")


def test_builder_fails_loudly_when_a_page_is_missing_from_the_order(tmp_path):
    """The sidebar is derived from Home's table, so a page added without a
    Home row would otherwise be published with no nav entry. It must be a
    hard stop instead: silent omission is exactly the failure the sidebar
    exists to prevent."""
    src = tmp_path / "wiki"
    src.mkdir()
    (src / "Home.md").write_text(
        "| Page | System |\n|------|--------|\n"
        "| [Listed](Listed.md) | in the table |\n")
    (src / "Listed.md").write_text("listed\n")
    (src / "Orphan.md").write_text("no row on Home\n")
    proc = _build(tmp_path / "out", src)
    assert proc.returncode != 0, "an unordered page was published silently"
    assert "Orphan" in proc.stderr, (
        f"the failure does not name the offending page:\n{proc.stderr}")


def test_build_writes_a_manifest_of_exactly_the_pages_it_owns(built):
    """Ownership has to be recorded, or the publisher cannot tell a page it
    retired from a page it never published."""
    manifest = (built / MANIFEST).read_text().split()
    assert manifest == sorted(p.name for p in built.glob("*.md")), (
        f"the manifest does not list exactly the built pages: {manifest}")
    assert "_Sidebar.md" in manifest, "the generated sidebar is unowned"


# --------------------------------------------------------------------------
# publish — run for real against a fixture wiki clone
# --------------------------------------------------------------------------

def _fixture_wiki(tmp_path: Path, files: dict[str, str]) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    for name, text in files.items():
        (wiki / name).write_text(text)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    for cmd in (["init", "-q", "-b", "master"], ["add", "-A"],
                ["commit", "-qm", "seed"]):
        subprocess.run(["git", "-C", str(wiki)] + cmd, check=True, env=env)
    return wiki


def _publish(wiki: Path, build: Path):
    return subprocess.run(["bash", str(PUBLISH), str(wiki), str(build)],
                          cwd=ROOT, capture_output=True, text=True)


def test_publish_never_touches_a_page_it_did_not_publish(tmp_path, built):
    """Non-destructive, proved by RUNNING it rather than by grepping the
    workflow for one spelling of `rm`.

    A grep such as `assert "rm -rf" not in wf` cannot see `git rm` in the
    same step deleting every unowned page, and `rsync --delete`, `git clean`
    and `find -delete` are equally invisible to it. This observes the
    outcome, so every spelling is covered at once.
    """
    wiki = _fixture_wiki(tmp_path, {
        "Hand-Written.md": "someone made this in the browser\n",
        "Home.md": "placeholder\n",
    })
    proc = _publish(wiki, built)
    assert proc.returncode == 0, proc.stderr
    assert (wiki / "Hand-Written.md").read_text() == \
        "someone made this in the browser\n", (
        "the publish step deleted or overwrote a page it does not own")
    assert (wiki / "Home.md").read_text() == (built / "Home.md").read_text(), (
        "a managed page was not overwritten from source")
    assert (wiki / MANIFEST).is_file(), "no ownership record was left behind"


def test_publish_retires_a_page_that_left_the_sources(tmp_path, built):
    """Copy-only publishing would leave a
    retired page served forever — outside the source of truth, absent from
    the regenerated sidebar, still reachable by URL and by search. Deletion
    is scoped to what a PREVIOUS manifest says this sync published.
    """
    previous = (built / MANIFEST).read_text() + "Retired-Page.md\n"
    wiki = _fixture_wiki(tmp_path, {
        "Retired-Page.md": "used to be a source page\n",
        "Hand-Written.md": "never ours\n",
        MANIFEST: previous,
    })
    proc = _publish(wiki, built)
    assert proc.returncode == 0, proc.stderr
    assert not (wiki / "Retired-Page.md").exists(), (
        "a page this sync published and no longer builds is still served: "
        f"{proc.stdout}")
    assert (wiki / "Hand-Written.md").is_file(), (
        "retirement leaked onto a page that was never in the manifest")
    assert MANIFEST not in (wiki / MANIFEST).read_text(), (
        "the manifest lists itself; it is not a page")


def test_publish_stages_everything_it_changed(tmp_path, built):
    """The script's header promises "stages the result". Publishing through
    plain `cp` stages only the DELETIONS, so a caller following the header
    literally commits the retirement and nothing else while the script
    prints "N published" — the wiki keeps stale content and reports success.

    A `git add -A` in the workflow, a different file, does not make that
    acceptable: a script whose stated contract depends on its one caller
    doing something the contract does not mention is a contract nobody can
    rely on.
    """
    previous = (built / MANIFEST).read_text() + "Retired-Page.md\n"
    wiki = _fixture_wiki(tmp_path, {
        "Home.md": "OLD home content\n",
        "Retired-Page.md": "going away\n",
        MANIFEST: previous,
    })
    proc = _publish(wiki, built)
    assert proc.returncode == 0, proc.stderr
    staged = subprocess.run(
        ["git", "-C", str(wiki), "diff", "--cached", "--name-status"],
        capture_output=True, text=True).stdout
    assert "\tHome.md" in staged, (
        f"a modified page was left unstaged: {staged!r}")
    assert "\t_Sidebar.md" in staged, f"a new page was left untracked: {staged!r}"
    assert f"\t{MANIFEST}" in staged, f"the manifest was left unstaged: {staged!r}"
    assert "D\tRetired-Page.md" in staged, f"the deletion is unstaged: {staged!r}"
    # And nothing is left behind for a `git add -A` in the caller to sweep up.
    unstaged = subprocess.run(
        ["git", "-C", str(wiki), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True).stdout.splitlines()
    dirty = [ln for ln in unstaged if ln[1:2].strip()]
    assert not dirty, f"publish left unstaged or untracked changes: {dirty}"


def test_publish_ignores_a_manifest_entry_that_is_not_a_page_name(tmp_path, built):
    """The wiki-side manifest is the only input that decides what
    gets deleted, and it lives in a store anyone with repo write can edit in
    the browser. It cannot be a trust boundary — that actor can delete a page
    directly — but it can be validated, so a path component, a traversal, or a
    dotfile name never reaches `git rm`, and the refusal names its reason
    instead of surfacing as a git error.
    """
    previous = ((built / MANIFEST).read_text()
                + "../escape.md\nsub/dir/Page.md\n.hidden.md\n/abs/Page.md\n")
    wiki = _fixture_wiki(tmp_path, {"Home.md": "x\n", MANIFEST: previous})
    (tmp_path / "escape.md").write_text("a file beside the clone\n")
    proc = _publish(wiki, built)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "escape.md").is_file(), (
        "a traversal entry in the wiki manifest reached the filesystem")
    for bad in ("../escape.md", "sub/dir/Page.md", ".hidden.md", "/abs/Page.md"):
        assert bad in proc.stderr, (
            f"the refusal does not name the rejected entry {bad!r}:\n{proc.stderr}")


def test_publish_refuses_to_run_without_a_manifest(tmp_path):
    """Existence of a build dir is not validity. Without the manifest,
    ownership is unknown — and a publisher that guesses either deletes
    someone else's page or strands a retired one. Fail closed, and name the
    cause: the error must say what produces the missing file."""
    build = tmp_path / "build"
    build.mkdir()
    (build / "Home.md").write_text("x\n")
    wiki = _fixture_wiki(tmp_path, {"Home.md": "y\n"})
    proc = _publish(wiki, build)
    assert proc.returncode != 0, "publishing proceeded with unknown ownership"
    assert "build-wiki.sh" in proc.stderr, (
        f"the error does not name what produces the manifest:\n{proc.stderr}")
    assert (wiki / "Home.md").read_text() == "y\n", (
        "the wiki was modified before the refusal")


# --------------------------------------------------------------------------
# the workflow — gate surface: it writes to a repo outside this one
# --------------------------------------------------------------------------

def _executed_shell() -> str:
    """Only the shell the workflow actually EXECUTES.

    `"scripts/build-wiki.sh" in wf` is satisfied by the `paths:` trigger
    filter, so it survives deleting the whole build step. A trigger filter is
    not a call, and a substring search over the file cannot tell them apart.
    Reading the `run:` bodies out of the parsed document can.

    Commenting the invocation out inside a `run: |` block keeps the string in
    the body, so whole-line shell comments are dropped here. Only a
    WHOLE-line comment is dropped, so `bash x.sh  # why` still reads as an
    invocation.
    """
    doc = yaml.safe_load(WORKFLOW.read_text())
    runs = [step["run"] for job in doc["jobs"].values()
            for step in job["steps"] if "run" in step]
    assert runs, "the workflow has no run: steps at all"
    return "\n".join(line for body in runs for line in body.splitlines()
                     if not line.lstrip().startswith("#"))


def test_workflow_runs_the_builder_and_the_publisher():
    executed = _executed_shell()
    assert "bash scripts/build-wiki.sh" in executed, (
        "no executed step runs the builder — a `paths:` filter naming the "
        "script is a trigger, not a call")
    assert "bash scripts/publish-wiki.sh" in executed, (
        "no executed step runs the publisher")


def test_workflow_publishes_on_main_and_on_demand():
    doc = yaml.safe_load(WORKFLOW.read_text())
    on = doc[True] if True in doc else doc["on"]   # YAML 1.1 reads `on:` as True
    assert "workflow_dispatch" in on, (
        "no manual trigger — the one-time wiki init is finished by hand, and "
        "the first publish has to be triggerable without a code push")
    assert on["push"]["branches"] == ["main"]
    paths = on["push"]["paths"]
    for path in ("docs/wiki/**", "scripts/build-wiki.sh",
                 "scripts/publish-wiki.sh"):
        assert path in paths, f"paths filter does not cover {path}"


def test_workflow_fails_actionably_when_the_wiki_was_never_initialized():
    """A GitHub wiki git repo does not exist until someone creates the first
    page in the browser — `agentops.wiki.git` is a 404 today. Without this
    the first run fails as an opaque `git clone` error on a URL that looks
    correct, and the actual fix (a one-time founder click) is unguessable.
    """
    emitted = [ln for ln in _executed_shell().splitlines()
               if "::error::" in ln]
    assert emitted, "no annotated failure is emitted for the uninitialized wiki"
    err = emitted[0]
    for cue in ("Wiki", "first page", "re-run"):
        assert cue.lower() in err.lower(), (
            f"the ::error:: does not tell the operator about {cue!r}: {err}")


def test_workflow_validates_diagrams_before_it_publishes():
    """The validator exists so a diagram that will not render cannot reach the
    wiki. Ordering is the whole property: validating after the push would
    leave every test green while broken diagrams were already live.
    """
    executed = _executed_shell()
    for marker in ("validate-diagrams.sh", "bash scripts/build-wiki.sh",
                   "bash scripts/publish-wiki.sh"):
        assert marker in executed, (
            f"no executed step runs {marker!r}, so there is no ordering to "
            "check — the publish path lost a stage")
    validate = executed.index("validate-diagrams.sh")
    build = executed.index("bash scripts/build-wiki.sh")
    publish = executed.index("bash scripts/publish-wiki.sh")
    assert validate < build < publish, (
        "the diagram gate does not run before the build and publish steps")


def test_the_diagram_validator_is_used_never_forked():
    """`scripts/validate-diagrams.sh` is the one diagram validator. This
    workflow CALLS it; it does not carry a second parser.

    The ownership check is made against the tree and the branch's own
    history rather than a string, so a second validator written here fails
    rather than drifts.
    """
    assert (ROOT / "scripts" / "validate-diagrams.sh").is_file(), (
        "scripts/validate-diagrams.sh is not in the tree — it is the one "
        "diagram gate and it landed on main; do not write a second one here")
    assert "bash scripts/validate-diagrams.sh" in _executed_shell(), (
        "the publish path does not run the diagram gate at all")

    # Ref-free half, so this still bites in a shallow CI checkout: exactly one
    # script in the tree may drive mermaid-cli. A second one is the drift.
    # The census keys on the DRIVING MECHANISMS, not on one spelling:
    # `mmdc` (the binary every mermaid-cli invocation runs, npx-resolved or
    # lockfile-installed) OR the package literal. Keying on the package
    # literal alone goes blind to an invocation through
    # node_modules/.bin/mmdc — the only literal left can be a header COMMENT,
    # so a rival driving mermaid that way would be invisible while deleting
    # a stale comment would turn this red.
    drivers = sorted(p.name for p in (ROOT / "scripts").glob("*.sh")
                     if "mmdc" in p.read_text()
                     or "@mermaid-js/mermaid-cli" in p.read_text())
    assert drivers == ["validate-diagrams.sh"], (
        f"more than one script drives the mermaid engine: {drivers} — two "
        "rival diagram gates drift, and validate-diagrams.sh is the one that exists")

    # History half. The exit status of `git log origin/main..HEAD` is
    # checked: when the ref does not resolve — the default shallow
    # `actions/checkout` on a pull_request never creates
    # `refs/remotes/origin/main` — git exits 128 with EMPTY stdout, and an
    # unchecked assertion would pass no matter what the branch had done to
    # the file. A check that cannot evaluate must not read as a pass.
    base = next((ref for ref in ("origin/main", "main")
                 if subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref],
                                   cwd=ROOT, capture_output=True).returncode == 0),
                None)
    if base is None:
        pytest.skip("no origin/main or main ref in this checkout, so the "
                    "history half cannot evaluate — skipped out loud rather "
                    "than passed silently; the ref-free half above still ran")
    # --diff-filter=A: forbid CREATING the file on a branch (the fork this
    # guard exists for), not editing it. The unfiltered form would outlaw
    # every edit to the canonical validator — pinning its npm toolchain, say
    # — while changing nothing about ownership. Editing THE validator is
    # maintenance; a branch that ADDS the file is either
    # recreating a deleted gate or shadowing it, which is the drift. The
    # ref-free half above still pins "exactly one mermaid driver" on the
    # tree, so a rival written under another name fails there.
    proc = subprocess.run(
        ["git", "log", "--oneline", "--diff-filter=A", f"{base}..HEAD", "--",
         "scripts/validate-diagrams.sh"],
        cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"`git log {base}..HEAD` failed, so this guard could not evaluate: "
        f"{proc.stderr.strip()}")
    assert proc.stdout.strip() == "", (
        "this branch CREATES scripts/validate-diagrams.sh, the one diagram "
        f"gate already on main — two rival diagram gates would drift:\n{proc.stdout.strip()}")


# --- the diagram validator's browser launch, and the step that retries it ----
#
# Headless Chrome that never hands back its WebSocket endpoint can fail the
# publish job on main with no diagram changed, and a red check on main reads
# as a gate failure. What these tests CAN prove is control flow: a launch
# failure is classified apart from a diagram failure, the step retries only
# that class and a bounded number of times with a pause between attempts, and
# its failure names the browser. What they CANNOT prove: that the flake rate
# drops, that the longer launch timeout is long enough on a hosted runner, or
# that a future puppeteer keeps the error wording the classifier keys on. Only
# CI history over time shows those.

VALIDATOR = ROOT / "scripts" / "validate-diagrams.sh"
# puppeteer's own wording, from @puppeteer/browsers at the pinned lockfile
_WS_ENDPOINT_TIMEOUT = ("TimeoutError: Timed out after 30000 ms while waiting "
                        "for the WS endpoint URL to appear in stdout!")

_FAKE_MMDC = """#!/bin/bash
while [ $# -gt 0 ]; do
  if [ "$1" = -p ]; then pp="$2"; shift 2
  elif [ "$1" = -i ]; then in="$2"; shift 2
  else shift; fi
done
cp "$pp" "$in.pp.json"
if grep -q HUGE-LOG "$in"; then
  i=0
  while [ $i -lt 8000 ]; do
    echo "    at frame $i (node:internal/a/long/stack/trace/line/to/fill/the/log)"
    i=$((i + 1))
  done >&2
fi
if grep -q LAUNCH-FLAKE "$in"; then echo "%s" >&2; exit 1; fi
if grep -q PARSE-ERROR "$in"; then echo "Error: Parse error on line 3" >&2; exit 1; fi
exit 0
""" % _WS_ENDPOINT_TIMEOUT


def _run_validator(tmp_path, pages, plain=(), **env):
    """Run the REAL validator over `pages`, with `npm` faked on PATH: the fake
    `npm ci` installs a fake mmdc whose outcome each page's marker names, so
    no network, no node and no browser are involved. `plain` names pages
    written with no mermaid block."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mmdc = tmp_path / "fake-mmdc"
    mmdc.write_text(_FAKE_MMDC)
    npm = bin_dir / "npm"
    npm.write_text("#!/bin/bash\nmkdir -p node_modules/.bin\n"
                   f"cp '{mmdc}' node_modules/.bin/mmdc\n"
                   "chmod +x node_modules/.bin/mmdc\n")
    npm.chmod(0o755)
    src = tmp_path / "wiki"
    src.mkdir()
    for name, marker in pages.items():
        (src / f"{name}.md").write_text(
            f"# {name}\n\n```mermaid\ngraph TD\n  A --> B\n  %% {marker}\n```\n")
    for name in plain:
        (src / f"{name}.md").write_text(f"# {name}\n\nNo diagram here.\n")
    run_env = {k: v for k, v in os.environ.items()
               if k != "PUPPETEER_LAUNCH_TIMEOUT_MS"}
    run_env.update(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                   PUPPETEER_EXECUTABLE_PATH="/bin/sh", **env)
    proc = subprocess.run(["bash", str(VALIDATOR), str(src)], env=run_env,
                          capture_output=True, text=True)
    return proc, src


def test_a_browser_that_never_launches_is_not_reported_as_a_diagram_failure(
        tmp_path):
    proc, _ = _run_validator(tmp_path, {"Flaky": "LAUNCH-FLAKE", "Fine": "ok"})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 3, out
    assert "BROWSER Flaky" in out, out
    assert "Mermaid render failed" not in out, (
        "a browser that never started was reported as a diagram that will "
        f"not render:\n{out}")
    assert "not a diagram problem" in out, out


@pytest.mark.parametrize("marker,code", [
    ("LAUNCH-FLAKE HUGE-LOG", 3),
    ("PARSE-ERROR HUGE-LOG", 1),
])
def test_a_large_render_log_does_not_end_the_run_before_its_exit_is_chosen(
        tmp_path, marker, code):
    """A render log is excerpted into the job output. Piping the whole log
    into a reader that stops after a few lines kills the writer with SIGPIPE,
    and under `pipefail` and `set -e` that ends the script at 141: no exit 3,
    so wiki-sync neither retries nor names the launch. A puppeteer stack
    trace is easily past a pipe buffer; this one is about 600KB."""
    proc, _ = _run_validator(tmp_path, {"Big": marker})
    out = proc.stdout + proc.stderr
    assert proc.returncode == code, f"exit {proc.returncode}:\n{out[-2000:]}"


def test_a_diagram_failure_beside_a_launch_failure_is_still_a_diagram_failure(
        tmp_path):
    proc, _ = _run_validator(
        tmp_path, {"Flaky": "LAUNCH-FLAKE", "Broken": "PARSE-ERROR"})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "FAIL Broken" in out, out


@pytest.mark.parametrize("shape,pages,plain", [
    ("no markdown at all", {}, ()),
    ("markdown with no mermaid block", {}, ("Prose",)),
])
def test_a_run_that_checked_no_diagram_exits_apart_from_a_rejection(
        tmp_path, shape, pages, plain):
    """Exit 1 is the one code a caller reads as "a diagram did not render".
    A run that found nothing to check must not share it, or ci.yml's
    known-bad control reads a blinded validator (a fence grep anchored at
    column 0 skipping the indented fixture) as a rejection and passes."""
    proc, _ = _run_validator(tmp_path, pages, plain=plain)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 4, f"{shape}: exit {proc.returncode}\n{out}"
    assert "nothing to validate" in out, out


def test_a_parse_error_alone_never_mentions_the_browser(tmp_path):
    proc, _ = _run_validator(tmp_path, {"Broken": "PARSE-ERROR"})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "did not launch" not in out, out


def test_the_browser_launch_timeout_is_longer_than_puppeteers_default(tmp_path):
    proc, src = _run_validator(tmp_path, {"Fine": "ok"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    config = json.loads((src / "Fine.md.pp.json").read_text())
    assert config["timeout"] > 30000, config
    assert "--no-sandbox" in config["args"], config


def test_the_browser_launch_timeout_is_overridable_and_refuses_a_non_number(
        tmp_path):
    chosen, garbage = tmp_path / "chosen", tmp_path / "garbage"
    chosen.mkdir()
    garbage.mkdir()
    proc, src = _run_validator(chosen, {"Fine": "ok"},
                               PUPPETEER_LAUNCH_TIMEOUT_MS="45000")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((src / "Fine.md.pp.json").read_text())["timeout"] == 45000
    proc, _ = _run_validator(garbage, {"Fine": "ok"},
                             PUPPETEER_LAUNCH_TIMEOUT_MS="90s")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "PUPPETEER_LAUNCH_TIMEOUT_MS" in proc.stderr, proc.stderr


def _validate_step() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text())
    steps = [step for job in doc["jobs"].values() for step in job["steps"]
             if "validate-diagrams.sh" in step.get("run", "")]
    assert len(steps) == 1, "expected exactly one step running the validator"
    return steps[0]


BROKEN_FIXTURE = ROOT / "tests" / "fixtures" / "broken-diagram"


def _run_validate_step(tmp_path, exits, backoff="0", step=None, **extra_env):
    """Run the workflow step's own `run:` text under GitHub's default bash
    flags, with the validator replaced by one that exits with `exits` in
    turn (the last code repeats). `step` defaults to wiki-sync's. On exit 1
    the stand-in prints the rejection line the real validator prints for
    the known-bad fixture, unless STUB_NO_MARKER is set."""
    step = step or _validate_step()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copytree(BROKEN_FIXTURE,
                    tmp_path / "tests" / "fixtures" / "broken-diagram")
    (tmp_path / "exits").write_text("\n".join(map(str, exits)) + "\n")
    (scripts / "validate-diagrams.sh").write_text(
        'n=$(( $(cat calls 2>/dev/null || echo 0) + 1 )); echo "$n" > calls\n'
        'code=$(sed -n "${n}p" exits); [ -n "$code" ] || '
        'code=$(tail -n 1 exits)\n'
        'echo "validator attempt $n"\n'
        'if [ "$code" = 1 ] && [ -z "${STUB_NO_MARKER:-}" ]; then\n'
        '  for f in tests/fixtures/broken-diagram/*.md; do\n'
        '    echo "FAIL $(basename "$f" .md) - Mermaid render failed:"\n'
        '  done\n'
        'fi\n'
        'exit "$code"\n')
    env = {**os.environ,
           **{k: str(v) for k, v in (step.get("env") or {}).items()},
           "DIAGRAM_LAUNCH_BACKOFF_SECS": backoff, **extra_env}
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path, env=env, capture_output=True, text=True)
    calls = int((tmp_path / "calls").read_text())
    return proc, calls, step


def test_the_publish_job_retries_a_browser_that_did_not_launch(tmp_path):
    proc, calls, _ = _run_validate_step(tmp_path, [3, 0])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == 2, "a launch failure was not retried"
    warnings = [ln for ln in proc.stdout.splitlines()
                if ln.startswith("::warning")]
    assert warnings and "browser" in warnings[0].lower(), proc.stdout


def test_the_publish_jobs_retry_is_bounded_and_its_failure_names_the_browser_launch(
        tmp_path):
    proc, calls, step = _run_validate_step(tmp_path, [3])
    attempts = int(step["env"]["DIAGRAM_LAUNCH_ATTEMPTS"])
    assert 2 <= attempts <= 5, f"retry bound {attempts} is not a small bound"
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert calls == attempts, f"{calls} attempts against a bound of {attempts}"
    errors = [ln for ln in proc.stdout.splitlines() if ln.startswith("::error")]
    assert len(errors) == 1, proc.stdout
    for cue in ("browser", "did not launch", "not a diagram",
                "not a gate failure"):
        assert cue in errors[0].lower(), f"the ::error lacks {cue!r}: {errors[0]}"


@pytest.mark.parametrize("code,cause", [
    (1, "a diagram that will not render"),
    (2, "a toolchain that did not install"),
    (4, "a run that checked no diagram"),
])
def test_the_publish_job_neither_retries_nor_relabels_any_other_failure(
        tmp_path, code, cause):
    proc, calls, _ = _run_validate_step(tmp_path, [code, 0])
    assert proc.returncode == code, proc.stdout + proc.stderr
    assert calls == 1, f"{cause} was retried"
    assert "browser" not in proc.stdout.lower(), (
        f"{cause} was labelled a browser launch:\n{proc.stdout}")


def test_the_publish_job_pauses_between_launch_attempts(tmp_path):
    assert int(_validate_step()["env"]["DIAGRAM_LAUNCH_BACKOFF_SECS"]) > 0, (
        "the workflow declares no backoff")
    started = time.monotonic()
    proc, calls, _ = _run_validate_step(tmp_path, [3, 0], backoff="1")
    assert proc.returncode == 0 and calls == 2, proc.stdout + proc.stderr
    assert time.monotonic() - started >= 1, "the retry did not wait"


# --- ci.yml's negative control: the known-bad diagram must be REJECTED ------
#
# The control proves the validator still catches a bad diagram. Negating the
# validator's exit (`! bash scripts/validate-diagrams.sh ...`) passes on ANY
# non-zero exit, so a browser that never launched (exit 3, no diagram
# checked) would report green as a rejection. Only exit 1 is a rejection.

CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _negative_control_step() -> dict:
    doc = yaml.safe_load(CI_WORKFLOW.read_text())
    steps = [step for job in doc["jobs"].values()
             for step in job.get("steps", [])
             if "tests/fixtures/broken-diagram" in step.get("run", "")]
    assert len(steps) == 1, "expected exactly one known-bad diagram control"
    return steps[0]


def _run_negative_control(tmp_path, exits, backoff="0", **extra_env):
    return _run_validate_step(tmp_path, exits, backoff=backoff,
                              step=_negative_control_step(), **extra_env)


def test_the_known_bad_diagram_control_is_red_on_an_exit_1_that_rejected_nothing(
        tmp_path):
    """Exit 1 is also what `set -e` returns when any command in the validator
    fails with status 1. Without the fixture's own FAIL line in the output,
    exit 1 is not evidence that the diagram was rejected."""
    proc, calls, _ = _run_negative_control(tmp_path, [1], STUB_NO_MARKER="1")
    assert proc.returncode != 0, (
        "an exit 1 with no rejection line passed the control:\n" + proc.stdout)
    assert calls == 1
    errors = [ln for ln in proc.stdout.splitlines() if ln.startswith("::error")]
    assert len(errors) == 1 and "IndentedBrokenFence" in errors[0], proc.stdout


def test_the_known_bad_diagram_control_is_red_when_the_validator_checked_no_diagram(
        tmp_path):
    proc, calls, _ = _run_negative_control(tmp_path, [4, 1])
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert calls == 1, "a run that checked no diagram was retried"
    errors = [ln for ln in proc.stdout.splitlines() if ln.startswith("::error")]
    assert len(errors) == 1, proc.stdout
    assert "checked no diagram" in errors[0].lower(), errors[0]


_ALWAYS_REJECTS_MMDC = ('#!/bin/bash\n'
                        'echo "Error: Parse error on line 3" >&2\nexit 1\n')
_FENCE_GREP = "grep -q '```mermaid' \"$f\""


def _run_control_on_the_real_validator(tmp_path, validator_text):
    """ci.yml's control, its real `run:` text, over the real fixture and a
    validator with the given text. npm and mmdc are faked, and the fake mmdc
    rejects every diagram it is handed, so the only question is whether the
    validator hands it the fixture at all."""
    (tmp_path / "scripts" / "mermaid").mkdir(parents=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy(ROOT / "scripts" / "mermaid" / name,
                    tmp_path / "scripts" / "mermaid" / name)
    (tmp_path / "scripts" / "validate-diagrams.sh").write_text(validator_text)
    shutil.copytree(BROKEN_FIXTURE,
                    tmp_path / "tests" / "fixtures" / "broken-diagram")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mmdc = tmp_path / "fake-mmdc"
    mmdc.write_text(_ALWAYS_REJECTS_MMDC)
    (bin_dir / "npm").write_text(
        "#!/bin/bash\nmkdir -p node_modules/.bin\n"
        f"cp '{mmdc}' node_modules/.bin/mmdc\nchmod +x node_modules/.bin/mmdc\n")
    (bin_dir / "npm").chmod(0o755)
    step = _negative_control_step()
    env = {**{k: v for k, v in os.environ.items()
              if k != "PUPPETEER_LAUNCH_TIMEOUT_MS"},
           **{k: str(v) for k, v in (step.get("env") or {}).items()},
           "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
           "PUPPETEER_EXECUTABLE_PATH": "/bin/sh",
           "DIAGRAM_LAUNCH_BACKOFF_SECS": "0"}
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path, env=env, capture_output=True, text=True)


def test_the_known_bad_diagram_control_passes_on_the_real_validator(tmp_path):
    proc = _run_control_on_the_real_validator(tmp_path, VALIDATOR.read_text())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FAIL IndentedBrokenFence - Mermaid render failed:" in proc.stdout


def test_the_known_bad_diagram_control_is_red_on_a_validator_blind_to_indented_fences(
        tmp_path):
    """The mutant: the fence grep anchored at column 0. The
    indented fixture is then skipped, the validator finds nothing to check,
    and the control must go red naming that, not pass as a rejection."""
    text = VALIDATOR.read_text()
    assert _FENCE_GREP in text, "the validator's fence grep moved; update the mutant"
    mutant = text.replace(_FENCE_GREP, "grep -q '^```mermaid' \"$f\"")
    proc = _run_control_on_the_real_validator(tmp_path, mutant)
    assert proc.returncode != 0, (
        "a validator blind to the indented fixture passed the control:\n"
        + proc.stdout + proc.stderr)
    assert "FAIL IndentedBrokenFence" not in proc.stdout
    errors = [ln for ln in proc.stdout.splitlines() if ln.startswith("::error")]
    assert len(errors) == 1 and "checked no diagram" in errors[0].lower(), (
        proc.stdout + proc.stderr)


def test_the_known_bad_diagram_control_passes_when_the_gate_rejects_the_diagram(
        tmp_path):
    proc, calls, _ = _run_negative_control(tmp_path, [1])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == 1, "a real rejection was retried"


def test_the_known_bad_diagram_control_is_red_when_the_browser_never_launches(
        tmp_path):
    proc, calls, step = _run_negative_control(tmp_path, [3])
    assert proc.returncode != 0, (
        "a browser that never launched passed the control as a rejection:\n"
        + proc.stdout + proc.stderr)
    attempts = int(step["env"]["DIAGRAM_LAUNCH_ATTEMPTS"])
    assert 2 <= attempts <= 5, f"retry bound {attempts} is not a small bound"
    assert calls == attempts, f"{calls} attempts against a bound of {attempts}"
    errors = [ln for ln in proc.stdout.splitlines() if ln.startswith("::error")]
    assert len(errors) == 1, proc.stdout
    for cue in ("browser", "did not launch", "not a diagram"):
        assert cue in errors[0].lower(), f"the ::error lacks {cue!r}: {errors[0]}"


def test_the_known_bad_diagram_control_retries_a_launch_then_reads_the_rejection(
        tmp_path):
    proc, calls, _ = _run_negative_control(tmp_path, [3, 1])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == 2, "a launch failure was not retried"


@pytest.mark.parametrize("code,cause", [
    (0, "a gate that accepted the known-bad diagram"),
    (2, "a toolchain that did not install"),
])
def test_the_known_bad_diagram_control_is_red_on_any_exit_but_a_rejection(
        tmp_path, code, cause):
    proc, calls, _ = _run_negative_control(tmp_path, [code, 1])
    assert proc.returncode != 0, f"{cause} passed the control"
    assert calls == 1, f"{cause} was retried"
    assert "browser" not in proc.stdout.lower(), (
        f"{cause} was labelled a browser launch:\n{proc.stdout}")


def test_the_known_bad_diagram_control_pauses_between_launch_attempts(tmp_path):
    assert int(_negative_control_step()["env"]
               ["DIAGRAM_LAUNCH_BACKOFF_SECS"]) > 0, "ci.yml declares no backoff"
    started = time.monotonic()
    proc, calls, _ = _run_negative_control(tmp_path, [3, 1], backoff="1")
    assert proc.returncode == 0 and calls == 2, proc.stdout + proc.stderr
    assert time.monotonic() - started >= 1, "the retry did not wait"
