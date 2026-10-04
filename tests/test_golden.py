"""Golden fixture: canned diff -> byte-expected artifact from the full
deterministic pipeline (parse -> match -> check -> stamp) over the sample
fixture ruleset. If this fails after an intentional gate change, regenerate
the golden artifact and justify it in the same commit."""

import json
from pathlib import Path

from warden import config as config_mod
from warden import review as review_mod
from warden import rules as rules_mod
from warden.diffs import DiffContext, parse_lines

GOLDEN = Path(__file__).resolve().parent / "golden"


def test_golden_secrets_diff_produces_expected_artifact(sample_repo):
    cfg = config_mod.load(sample_repo)
    rules = rules_mod.load_rules(sample_repo / ".warden" / "rules")
    raw_diff = (GOLDEN / "secrets.diff").read_text()
    added, removed = parse_lines(raw_diff)
    ctx = DiffContext(base="c" * 40, head="d" * 40, files=("app/config.py",),
                      added={"app/config.py": added},
                      removed={"app/config.py": removed},
                      read_base=lambda p: None, read_head=lambda p: None)

    doc = review_mod.run_review(cfg, rules, ctx, "goldenversion")

    expected = json.loads((GOLDEN / "expected-artifact.json").read_text())
    assert doc == expected
