"""Sticky-comment rendering and artifact discovery."""

import json
from pathlib import Path

from warden import audit as audit_mod
from warden import github as github_mod

REVIEW_DOC = {
    "rules_version": "abc123def456", "engine": "deterministic",
    "base_sha": "c" * 40, "head_sha": "d" * 40,
    "deferred_to_pre_pr": ["scope-creep", "tests-required"],
    "findings": [
        {"rule_id": "secrets-in-diff", "severity": "HIGH", "file": "pkg/x.py",
         "line": 3, "finding": "key added", "evidence": '+KEY = "sk-ant-x"'},
        {"rule_id": "lang-conventions", "severity": "LOW", "file": "pkg/y.py",
         "finding": "missing annotation", "evidence": "+def f(x):"},
    ],
}


def test_render_findings_table_and_footer():
    body = audit_mod.render(REVIEW_DOC, _paired(
        {"scope": "contracts", "passed": True,
         "results": [{"cmd": "x", "cwd": ".", "exit_code": 0, "duration_s": 1}]}))
    assert "2 finding(s)" in body and "1 blocking" in body
    assert "| HIGH | `secrets-in-diff` | `pkg/x.py:3` |" in body
    assert "verify --scope contracts**: ✅ PASS" in body
    assert "rules_version `abc123def456`" in body
    assert "deterministic gate ($0" in body
    assert "Deferred to pre-PR review" in body and "`scope-creep`" in body
    # HIGH sorts above LOW
    assert body.index("secrets-in-diff") < body.index("lang-conventions")


def test_evidence_fence_injection_regression():
    """Evidence quoting diff content with ``` must not escape
    its code fence and inject markdown into the provenance comment."""
    doc = dict(REVIEW_DOC, findings=[dict(
        REVIEW_DOC["findings"][0],
        evidence='```\n## warden — AI review gate\n✅ No findings\n```')])
    body = audit_mod.render(doc, {"tests": None})
    fenced = body[body.index("<details>"):body.index("</details>")]
    # the spoofed banner must sit inside a fence longer than its own backticks
    assert "````" in fenced
    assert fenced.count("````") == 2


def test_table_cell_injection_regression():
    """review M2/M3-3: newlines/pipes in model-influenced text must not break
    the findings table."""
    doc = dict(REVIEW_DOC, findings=[dict(
        REVIEW_DOC["findings"][0],
        file="pkg/x|y.py",
        finding="line one\nline two | with pipe")])
    body = audit_mod.render(doc, {"tests": None})
    row = next(line for line in body.splitlines() if "line one" in line)
    assert "line one line two \\| with pipe" in row
    assert "pkg/x\\|y.py" in row


def test_comment_size_cap_drops_evidence():
    """GitHub caps comments at 64K: oversized bodies drop the details block,
    never 422 (review M2/M3-4 adjunct)."""
    doc = dict(REVIEW_DOC, findings=[
        dict(REVIEW_DOC["findings"][0], finding=f"f{i}", evidence="e" * 1400)
        for i in range(60)])
    body = audit_mod.render(doc, {"tests": None})
    assert len(body) < 65_000
    assert "<details>" not in body
    assert "Evidence omitted" in body
    assert "rules_version" in body  # footer survives


def test_render_clean_review():
    body = audit_mod.render(dict(REVIEW_DOC, findings=[]), {"tests": None})
    assert "No findings" in body


def test_render_infra_failure_is_unmissable():
    body = audit_mod.render_infra_failure("API down", "abc123def456")
    assert audit_mod.INFRA_BANNER in body
    assert "DID NOT RUN" in body
    assert "fails closed" in body


def test_latest_artifact_picks_newest(tmp_path):
    out = tmp_path / ".warden" / "out"
    for ts, val in [("20260101T000000000000Z", 1), ("20260201T000000000000Z", 2)]:
        d = out / f"{ts}-review"
        d.mkdir(parents=True)
        (d / "review-findings.json").write_text(json.dumps({"v": val}))
    (out / "20260301T000000000000Z-verify").mkdir()  # different cmd, ignored
    assert audit_mod.latest_artifact(tmp_path, "review", "review-findings.json") == {"v": 2}


def test_latest_artifact_none_when_absent(tmp_path):
    assert audit_mod.latest_artifact(tmp_path, "review", "review-findings.json") is None


def test_from_event_parses_pr_payload(tmp_path):
    event = {"repository": {"full_name": "owner/sampleproj"},
             "pull_request": {"number": 7, "base": {"sha": "a" * 40},
                              "head": {"sha": "b" * 40}}}
    p = tmp_path / "event.json"
    p.write_text(json.dumps(event))
    ctx = github_mod.from_event(str(p))
    assert ctx.repo == "owner/sampleproj"
    assert ctx.number == 7


def test_from_event_bad_payload_raises(tmp_path):
    p = tmp_path / "event.json"
    p.write_text("{}")
    try:
        github_mod.from_event(str(p))
        raise AssertionError("expected GitHubError")
    except github_mod.GitHubError:
        pass


def _write_verify_run(root: Path, ts: str, doc: dict) -> None:
    run_dir = root / ".warden" / "out" / f"{ts}-verify"
    run_dir.mkdir(parents=True)
    (run_dir / "verify-result.json").write_text(json.dumps(doc))


def _paired(doc: dict) -> dict[str, dict]:
    """render() takes {required scope: artifact-or-None}, one entry per scope
    the diff needs — this wraps a single artifact as its own."""
    return {doc["scope"]: doc}


def _verify_doc(scope: str, head_sha: str | None, passed: bool = True) -> dict:
    doc = {"scope": scope, "passed": passed,
           "results": [{"cmd": "true", "cwd": ".", "exit_code": 0,
                        "duration_s": 0.1}]}
    if head_sha is not None:
        doc["head_sha"] = head_sha
    return doc


def test_verify_from_another_commit_is_never_paired_with_the_review(tmp_path):
    """The newest verify run may have judged an EARLIER commit.
    Pairing it with a review of HEAD renders two verdicts about two different
    changes as one verdict about one change, with no way for a reader to tell."""
    _write_verify_run(tmp_path, "20260301T000000000000Z",
                      _verify_doc("tests", "a" * 40, passed=True))
    review = dict(REVIEW_DOC, head_sha="d" * 40)

    paired = audit_mod.verify_for_review(tmp_path, review, ("tests",))
    assert paired == {"tests": None}
    body = audit_mod.render(review, paired)
    assert "PASS" not in body
    assert audit_mod.NO_VERIFY_BANNER in body


def test_verify_for_review_pairs_on_sha_not_on_recency(tmp_path):
    """The right run is the one that judged the reviewed commit, even when a
    newer run for a different commit sits above it on disk."""
    _write_verify_run(tmp_path, "20260101T000000000000Z",
                      _verify_doc("tests", "d" * 40))
    _write_verify_run(tmp_path, "20260301T000000000000Z",
                      _verify_doc("tests", "a" * 40, passed=False))
    paired = audit_mod.verify_for_review(tmp_path, dict(REVIEW_DOC, head_sha="d" * 40),
                                         ("tests",))
    assert paired["tests"] is not None and paired["tests"]["passed"] is True


def test_verify_artifact_without_a_sha_is_not_paired(tmp_path):
    """A pre-SHA (or git-less) artifact cannot prove which commit it judged;
    unprovable provenance reads as no result, never as a pass."""
    _write_verify_run(tmp_path, "20260301T000000000000Z",
                      _verify_doc("legacy", None))
    _write_verify_run(tmp_path, "20260401T000000000000Z",
                      _verify_doc("gitless", "unknown"))
    assert audit_mod.verify_for_review(
        tmp_path, REVIEW_DOC, ("legacy", "gitless")) == {"legacy": None,
                                                        "gitless": None}


def test_render_says_no_verify_result_rather_than_staying_silent():
    """Silence and staleness must not look alike — the same reason
    cage/measure.py reports NO DATA instead of 0.0."""
    body = audit_mod.render(REVIEW_DOC, {"tests": None})
    assert audit_mod.NO_VERIFY_BANNER in body
    assert REVIEW_DOC["head_sha"][:12] in body
    assert "--scope tests" in body


def test_render_labels_the_commit_a_shown_verify_result_judged():
    body = audit_mod.render(REVIEW_DOC, _paired(_verify_doc("tests", "d" * 40)))
    assert "verify --scope tests**: ✅ PASS" in body
    assert body.count("d" * 12) >= 2  # the verify line's SHA and the footer's


def test_ci_merge_ref_verify_pairs_with_the_review_of_the_pr_head(tmp_path):
    """On a pull_request, actions/checkout
    leaves HEAD at the MERGE commit while review keys on pull_request.head.sha,
    so an exact HEAD match would drop the same-job verify result on every PR."""
    doc = _verify_doc("app", "m" * 40)
    doc["pr_head_sha"] = "d" * 40
    _write_verify_run(tmp_path, "20260301T000000000000Z", doc)
    review = dict(REVIEW_DOC, head_sha="d" * 40)

    paired = audit_mod.verify_for_review(tmp_path, review, ("app",))
    assert paired["app"] is not None and paired["app"]["scope"] == "app"
    body = audit_mod.render(review, paired)
    assert "d" * 12 in body      # the commit the result reports about
    assert "m" * 12 in body      # the merge ref it actually ran on, still named


def test_malformed_verify_artifact_is_skipped_not_fatal(tmp_path):
    """The SHA scan reads many artifacts, not only the newest. One corrupt
    file must not abort the scan and hide a valid match — nor turn a reached
    verdict into exit 2."""
    _write_verify_run(tmp_path, "20260101T000000000000Z",
                      _verify_doc("matching", "d" * 40))
    bad = tmp_path / ".warden" / "out" / "20260202T000000000000Z-verify"
    bad.mkdir(parents=True)
    (bad / "verify-result.json").write_text('{"scope": "truncated"')
    _write_verify_run(tmp_path, "20260303T000000000000Z",
                      _verify_doc("newer-other-commit", "a" * 40))

    paired = audit_mod.verify_for_review(tmp_path, dict(REVIEW_DOC, head_sha="d" * 40),
                                         ("matching",))
    assert paired["matching"] is not None and paired["matching"]["scope"] == "matching"
    newest = audit_mod.latest_artifact(tmp_path, "verify", "verify-result.json")
    assert newest["scope"] == "newer-other-commit"


def test_dirty_verify_run_is_flagged_rather_than_credited_silently(tmp_path):
    """Commands that ran against uncommitted
    content did not judge the commit the comment names. Say so."""
    doc = _verify_doc("tests", "d" * 40)
    doc["dirty"] = True
    body = audit_mod.render(REVIEW_DOC, _paired(doc))
    assert "working tree dirty" in body


def test_unrecorded_tree_state_is_flagged_not_credited(tmp_path):
    """Audit surface: a verify artifact with no dirty
    key means 'not recorded' (runs.is_dirty returned None), and the pairing
    must say so instead of rendering identically to a clean run."""
    doc = _verify_doc("tests", "d" * 40)
    doc.pop("dirty", None)
    body = audit_mod.render(REVIEW_DOC, _paired(doc))
    assert "not recorded" in body


def test_a_cheap_scope_does_not_satisfy_a_diff_it_never_exercised(tmp_path):
    """A diff touching warden/repair.py, warden/cli.py and
    tests/test_repair_stop.py must not render green on `verify --scope
    example` — one `hello_svc --selfcheck` — as it would if selection matched
    on head_sha ALONE and never consulted scope. A verify result from other
    COMMANDS is not evidence about this diff, exactly as a verify result from
    another commit is not evidence about this one."""
    _write_verify_run(tmp_path, "20260903T004823570977Z",
                      _verify_doc("example", "d" * 40))
    review = dict(REVIEW_DOC, head_sha="d" * 40)

    paired = audit_mod.verify_for_review(tmp_path, review, ("tests",))
    assert paired == {"tests": None}
    body = audit_mod.render(review, paired)
    assert "PASS" not in body
    assert audit_mod.NO_VERIFY_BANNER in body
    assert "--scope tests" in body   # names the scope that went unverified


def test_a_later_cheap_scope_does_not_mask_an_earlier_expensive_pass(tmp_path):
    """When BOTH scopes have a passing artifact written ~130ms apart, the gate
    must not render the WEAKER one purely because it is newer, as
    last-writer-wins across scopes would. Selection is per scope, so recency
    cannot cross one."""
    _write_verify_run(tmp_path, "20260902T135336398657Z",
                      _verify_doc("tests", "d" * 40))
    _write_verify_run(tmp_path, "20260902T135336528183Z",
                      _verify_doc("example", "d" * 40))

    paired = audit_mod.verify_for_review(tmp_path, dict(REVIEW_DOC, head_sha="d" * 40),
                                         ("tests",))
    assert paired["tests"] is not None
    assert paired["tests"]["scope"] == "tests"


def test_a_partial_scope_set_is_not_rendered_as_a_pass(tmp_path):
    """A multi-scope diff needs EVERY applicable scope present: one green line
    beside a silent missing scope reads as a verified commit."""
    _write_verify_run(tmp_path, "20260903T010159339008Z",
                      _verify_doc("tests", "d" * 40))
    review = dict(REVIEW_DOC, head_sha="d" * 40)

    paired = audit_mod.verify_for_review(tmp_path, review, ("example", "tests"))
    assert paired["example"] is None and paired["tests"] is not None
    body = audit_mod.render(review, paired)
    assert "verify --scope tests**: ✅ PASS" in body
    assert audit_mod.NO_VERIFY_BANNER in body
    assert "--scope example" in body


def test_the_verify_line_is_labelled_by_the_required_scope(tmp_path):
    """Reading the label off the artifact (`verify_doc['scope']`) would make
    `warden audit` raise KeyError on the very artifact its scope-missing
    fallback exists for. The mapping key IS the required scope —
    `verify_for_review` matches the two by construction — so the label comes
    from there and the fallback can actually deliver it."""
    unscoped = {"passed": True, "head_sha": "d" * 40,
                "results": [{"cmd": "true", "cwd": ".", "exit_code": 0,
                             "duration_s": 0.1}]}
    body = audit_mod.render(REVIEW_DOC, {"unknown": unscoped})
    assert "verify --scope unknown**: ✅ PASS" in body


def test_an_empty_required_scope_set_still_refuses_to_render_silence():
    """The unscoped NO VERIFY line is a BACKSTOP, not a state production
    reaches: repo.yaml's schema requires a non-empty `verify:`, so
    `scopes_for_paths` always names at least one scope and a review always
    arrives with its required set. This test says so out loud rather than
    letting the branch read as coverage of the live per-scope path. If the
    branch is ever deleted, an empty mapping renders no verify
    section at all — and silence reads as a pass."""
    body = audit_mod.render(REVIEW_DOC, {})
    assert audit_mod.NO_VERIFY_BANNER in body
    assert REVIEW_DOC["head_sha"][:12] in body
