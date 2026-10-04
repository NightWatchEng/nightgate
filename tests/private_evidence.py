"""Named skips for the tests that read what the public export leaves out.

publish.yaml keeps .warden/memory/ (bar tags.yaml), .beads/ and some
docs/design records out of the public tree, so a test that reads them cannot
pass there. It skips, naming what is absent, and only when BOTH the corpus and
the tracker are absent: a tree that tracks one of them is this working repo,
where losing the other is a red suite, never a skip
(test_private_evidence.py::test_the_working_repo_never_reads_as_the_export).
The answer comes from scripts/private-evidence.py, the function CI asks.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "private_evidence_script", ROOT / "scripts" / "private-evidence.py")
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)
_spec = importlib.util.spec_from_file_location(
    "publish_public_script", ROOT / "scripts" / "publish-public.py")
publish = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publish)

CORPUS = script.present(ROOT, "HEAD", "corpus")
TRACKER = script.present(ROOT, "HEAD", "tracker")
EXPORT = not CORPUS and not TRACKER


def lacks(what: str):
    return pytest.mark.skipif(EXPORT, reason=f"public export: publish.yaml "
                              f"leaves {what} out of this tree")


needs_corpus = lacks("the committed review corpus (.warden/memory/)")
needs_tracker = lacks("the tracker (.beads/)")
needs_design_records = lacks("the docs/design records (and corpus) this reads")


def publish_excludes(rel: str) -> bool:
    """Whether publish.yaml's `exclude` list leaves `rel` out of the export."""
    manifest = yaml.safe_load((ROOT / "publish.yaml").read_text())
    return any(publish.matches(pattern, rel) for pattern in manifest["exclude"])
