"""Render the sticky PR comment from evidence artifacts on disk.

The provenance surface: findings table, verify summary, and a footer binding
rules_version, model, cost, and SHAs. `review` calls this internally; `audit`
re-renders from the latest artifacts (e.g. after a later `verify`).
"""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .runs import UNKNOWN_SHA

_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
INFRA_BANNER = "## ⚠️ AI review gate DID NOT RUN (infra)"
NO_VERIFY_BANNER = "⚪ NO VERIFY RESULT for the reviewed commit"
# GitHub caps comment bodies at 65,536 chars; leave headroom for the marker.
_MAX_BODY = 60_000
_MAX_EVIDENCE = 1_500


def _cell(text: str) -> str:
    """Table-cell sanitization: pipes and newlines would break the row
    (model/PR-author-influenced text)."""
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _fence(text: str) -> str:
    """Fence with more backticks than any run inside — evidence quotes diff
    content, so a crafted ``` must not escape the block."""
    text = text[:_MAX_EVIDENCE]
    longest = max((len(r) for r in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{text}\n{ticks}"


def latest_artifact(root: Path, cmd: str, filename: str, *,
                    match: Callable[[dict], bool] | None = None) -> dict | None:
    """Newest run dir for `cmd` holding `filename` — or, with `match`, the
    newest one whose document satisfies it. Recency is not provenance: use
    `match` whenever the answer must be about a specific commit."""
    hit = latest_artifact_run(root, cmd, filename, match=match)
    return hit[1] if hit else None


def latest_artifact_run(root: Path, cmd: str, filename: str, *,
                        match: Callable[[dict], bool] | None = None
                        ) -> tuple[Path, dict] | None:
    """`latest_artifact` with the run dir it came from, for a caller that must
    NAME the artifact it relied on rather than only read it."""
    out_dir = root / ".warden" / "out"
    if not out_dir.is_dir():
        return None
    for run_dir in sorted(out_dir.iterdir(), reverse=True):
        if run_dir.name.endswith(f"-{cmd}") and (run_dir / filename).is_file():
            try:
                doc = json.loads((run_dir / filename).read_text())
            except (OSError, ValueError):
                # A corrupt artifact is not evidence — and must not abort the
                # scan and hide a valid match further down, nor crash a gate
                # that already reached its verdict.
                continue
            if match is None or match(doc):
                return run_dir, doc
    return None


def _reports_on(verify_doc: dict, head: str, scope: str) -> bool:
    """A verify run is evidence about (`head`, `scope`) when it ran on that
    commit — or CI told it the merge ref it ran on stands for that PR head —
    AND it ran that scope's commands. Both axes, never one: matching the
    commit alone accepts a run of entirely different commands as this diff's
    verification."""
    return (head in (verify_doc.get("head_sha"), verify_doc.get("pr_head_sha"))
            and verify_doc.get("scope") == scope)


def verify_for_commit(root: Path, head: str,
                      scopes: Sequence[str]) -> dict[str, dict | None]:
    """The verify results recorded for `head`, one entry per scope — `None`
    where no artifact on disk judged that commit under that scope.

    The pairing rule itself, with no review document in front of it, because it
    has two callers: `verify_for_review` below (the sticky comment) and
    `warden ship` (the refusal before a PR exists). Both must answer "did a
    verify run judge THIS commit under THIS scope" identically — a second
    spelling could let one scope answer for a diff it never exercised, so
    there is one.

    An absent or unknown head returns `None` for every scope: a commit warden
    cannot name cannot be matched against, and "could not tell" must cost more
    verification, never less.
    """
    return {scope: (hit[1] if (hit := verify_run_for_commit(root, head, scope))
                    else None)
            for scope in scopes}


def verify_run_for_commit(root: Path, head: str,
                          scope: str) -> tuple[Path, dict] | None:
    """`verify_for_commit` for ONE scope, with the run dir the result came
    from — the same pairing rule, not a second spelling of it, for a caller
    that must say which artifact it accepted (`warden verify --check-head`)."""
    if not head or head == UNKNOWN_SHA:
        return None
    return latest_artifact_run(root, "verify", "verify-result.json",
                               match=lambda doc: _reports_on(doc, head, scope))


def verify_for_review(root: Path, review_doc: dict | None,
                      scopes: Sequence[str]) -> dict[str, dict | None]:
    """The verify results recorded for the commit the review judged, one entry
    per scope the diff requires — `None` where no artifact covers that scope.

    Never falls back to a nearby run: a verify result for another commit is
    not evidence about this one, and rendering it beside the review presents
    it as though it were. Likewise a verify result from other COMMANDS is not
    evidence about this diff, so the match is (commit, scope) and the
    selection is made PER SCOPE. Matching on the commit alone would let one
    cheap scope both satisfy the gate for a diff it never exercised and MASK
    an expensive-scope pass on the identical commit, purely by being newer.
    No match -> None, which renders as an explicit NO VERIFY RESULT line for
    that scope, because silence and staleness must not look alike.
    """
    if review_doc is None:
        return {}
    return verify_for_commit(root, str(review_doc.get("head_sha") or ""), scopes)


def _sorted_findings(review_doc: dict) -> list[dict]:
    return sorted(review_doc["findings"],
                  key=lambda f: (_SEVERITY_ORDER[f["severity"]], f["file"]))


def _findings_table(findings: list[dict]) -> list[str]:
    """The count line and the findings table: what the sticky comment opens
    with, and all the job step summary carries of a review."""
    if not findings:
        return ["✅ No findings. All applicable rules passed."]
    high = sum(1 for f in findings if f["severity"] == "HIGH")
    lines = [f"**{len(findings)} finding(s)** — {high} blocking (HIGH).",
             "", "| severity | rule | location | finding |", "|---|---|---|---|"]
    for f in findings:
        loc = _cell(f["file"]) + (f":{f['line']}" if f.get("line") else "")
        lines.append(f"| {f['severity']} | `{_cell(f['rule_id'])}` | `{loc}` "
                     f"| {_cell(f['finding'])} |")
    return lines


def render_step_summary(review_doc: dict) -> str:
    """The review's findings table for `$GITHUB_STEP_SUMMARY` — the same
    table the sticky comment carries, with its deferred and paused lines and
    without the evidence quotes, so a reader of the Actions run sees the
    review's refusal beside verify's."""
    reviewed = str(review_doc.get("head_sha") or UNKNOWN_SHA)[:12]
    lines = [f"## warden review — `{reviewed}`", "",
             *_findings_table(_sorted_findings(review_doc))]
    # The deferred line too: CI evaluates no `engine: claude` rule, so a bare
    # "No findings" here would read as every rule having passed.
    deferred = review_doc.get("deferred_to_pre_pr", [])
    if deferred:
        lines += ["", "Deferred to pre-PR review (Claude Code session): " +
                  ", ".join(f"`{r}`" for r in deferred)]
    paused = review_doc.get("paused", [])
    if paused:
        lines += ["", "⏸ PAUSED (applied to this diff but not enforced): " +
                  ", ".join(f"`{r}`" for r in paused)]
    return "\n".join(lines) + "\n\n"


def render_infra_failure(error: str, rules_version: str) -> str:
    return (f"{INFRA_BANNER}\n\n"
            f"The AI rules were NOT evaluated on this PR: `{error}`\n\n"
            "This check fails closed. Re-run the `ai review` job; if the outage "
            "persists, an admin merge leaves this comment as the audit trail.\n\n"
            f"---\nrules_version `{rules_version}`\n")


def render(review_doc: dict | None,
           verify_docs: Mapping[str, dict | None]) -> str:
    """`verify_docs` maps each REQUIRED verify scope to the artifact that
    judged this commit under it, or None where none did.

    An EMPTY mapping is the backstop, not a state any production caller
    reaches: `scopes_for_paths` always names at least one scope (repo.yaml's
    schema requires a non-empty `verify:`), so a review always arrives with
    its required scopes. If one ever did not, the unscoped NO VERIFY line
    below still renders — an absent verify section would be silence, and
    silence reads as a pass.
    """
    lines = ["## warden — AI review gate", ""]

    if review_doc is None:
        lines.append("_No review artifact found._")
    else:
        findings = _sorted_findings(review_doc)
        lines += _findings_table(findings)
        if findings:
            lines += ["", "<details><summary>Evidence</summary>", ""]
            for f in findings:
                lines += [f"**`{_cell(f['rule_id'])}` @ {_cell(f['file'])}**",
                          _fence(f["evidence"]), ""]
            lines.append("</details>")
        deferred = review_doc.get("deferred_to_pre_pr", [])
        if deferred:
            lines += ["", "Deferred to pre-PR review (Claude Code session): " +
                      ", ".join(f"`{r}`" for r in deferred)]
        paused = review_doc.get("paused", [])
        if paused:
            # Never silent: a human reading this gate verdict must be able to
            # see which rules were switched off while it was reached.
            lines += ["", "⏸ PAUSED (applied to this diff but not enforced): " +
                      ", ".join(f"`{r}`" for r in paused)]

    reviewed = str((review_doc or {}).get("head_sha") or UNKNOWN_SHA)[:12]
    if verify_docs:
        # One line per REQUIRED scope, in a stable order. A scope the diff
        # needs and has no artifact for gets its own NO VERIFY RESULT line:
        # rendering only the scopes that happen to have run would let a
        # partial set read as a verified commit.
        for scope in sorted(verify_docs):
            verify_doc = verify_docs[scope]
            if verify_doc is None:
                lines += ["", f"**verify --scope {scope}**: {NO_VERIFY_BANNER} "
                              f"`{reviewed}` — no verify artifact on disk judged "
                              "it under this scope, which the changed paths "
                              "require. Absence of a result is not a green gate; "
                              f"re-run `warden verify --scope {scope}` on this "
                              "commit."]
                continue
            status = "✅ PASS" if verify_doc["passed"] else "❌ FAIL"
            ran_on = str(verify_doc.get("head_sha") or "")
            reported = str(verify_doc.get("pr_head_sha") or "") or ran_on
            if reported and reported != UNKNOWN_SHA:
                provenance = f"`{reported[:12]}`"
                # A merge-ref run reports on the PR head but ran on another
                # tree; name both rather than let one stand in silently for
                # the other.
                if ran_on and ran_on != reported:
                    provenance += f" (via merge ref `{ran_on[:12]}`)"
            else:
                provenance = "commit not recorded"
            if verify_doc.get("dirty"):
                provenance += " ⚠ working tree dirty at run time"
            elif "dirty" not in verify_doc:
                # Tri-state: an absent key means is_dirty could not determine
                # (runs.py: absent is "not recorded", never "clean") — saying
                # nothing would render it identically to clean.
                provenance += " ⚠ tree state not recorded at run time"
            # Labelled by the REQUIRED scope (the mapping key), not by the
            # artifact's own field: `verify_for_review` matches the two by
            # construction, and reading the artifact here would make `warden
            # audit` raise KeyError on the very artifact its `scope`-missing
            # fallback exists for.
            lines += ["", f"**verify --scope {scope}**: {status} "
                          f"({len(verify_doc['results'])} command(s)) · {provenance}"]
    elif review_doc is not None:
        # An unpaired verify run is NOT evidence about this commit, and its
        # absence is not a pass. Say which commit went unverified.
        lines += ["", f"**verify**: {NO_VERIFY_BANNER} `{reviewed}` — no verify "
                      "artifact on disk judged it. Absence of a result is not a "
                      "green gate; re-run `warden verify` on this commit."]

    if review_doc is not None:
        lines += ["", "---",
                  f"rules_version `{review_doc['rules_version']}` · "
                  f"{review_doc.get('engine', 'deterministic')} gate ($0, no API) · "
                  f"`{review_doc['base_sha'][:12]}...{review_doc['head_sha'][:12]}`"]
    body = "\n".join(lines) + "\n"
    if len(body) > _MAX_BODY:
        # Drop the evidence details rather than 422 on GitHub's 64K cap;
        # the full findings live in the review-findings.json artifact.
        start = body.find("<details>")
        end = body.rfind("</details>")  # evidence may contain the literal tag
        if start != -1 and end != -1:
            body = (body[:start]
                    + "_Evidence omitted (comment size cap) — see the "
                      "review-findings.json workflow artifact._"
                    + body[end + len("</details>"):])
    return body
