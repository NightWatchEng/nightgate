"""Recall must serve the priors the corpus holds.

Findings on a real corpus run to 500-1500 characters. If `recall` sorts hits
freshest-first and admits RENDERED lines against a fixed budget while
`render_line` emits the whole finding text, the freshest record spends the
budget alone — and when its one line exceeds the budget the loop stops before
selecting anything. An empty recall over dozens of judged records reads
exactly like an empty corpus, and `warden plan` prints "None on record for
these paths" off the same seam.

Three properties, each pinned here: a rendered line is a BOUNDED EXCERPT of
the finding, so one finding's length cannot decide whether its neighbours
are recalled; the budget is DERIVED from RECALL_K lines at that bound, so K
lines always fit; and hits are ranked by RECURRENCE KEY before recency, so
ten restatements of one shape do not crowd out nine others.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from warden import memory as memory_mod
from warden import plan as plan_mod


def record(rid: str, days_old: float, *, rule="r", dir_prefix="src/api",
           finding=None, status="confirmed") -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return {"id": rid, "ts": ts, "seq": 0, "sha": "s", "rule_id": rule,
            "tags": [], "dir_prefix": dir_prefix,
            "file": f"{dir_prefix}/x.py", "line": 1, "severity": "HIGH",
            "finding": finding if finding is not None else f"finding {rid}",
            "evidence": "e", "status": status, "origin": "day"}


def seed(root: Path, records: list[dict]) -> None:
    mem = root / ".warden" / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "findings.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records))


def long_finding(n: int, seed_char: str = "w") -> str:
    """A finding of exactly `n` characters with word boundaries, the shape a
    reviewer's paragraph takes — no sentence end, so the excerpt has to cut
    on a word."""
    words = (f"{seed_char}ord{i} " for i in range(n))
    out = ""
    for w in words:
        if len(out) + len(w) > n:
            break
        out += w
    return out.ljust(n, "x")


# ---------- the two named regressions ----------------------------------------

def test_twenty_long_findings_on_one_prefix_recall_k_lines(tmp_path):
    """20 fresh records on one dir_prefix with 1200-character findings
    recall RECALL_K lines, not 0 or 1."""
    # Twenty DISTINCT findings: identical text on one file and line is one
    # finding restated, and the read seam folds it before recall sees it.
    seed(tmp_path, [record(f"r{i}", i, finding=long_finding(1200, f"f{i}"))
                    for i in range(20)])
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert len(got) == memory_mod.RECALL_K
    rendered = memory_mod.render_recall(got, "reviewer")
    assert len(rendered) <= memory_mod.RECALL_BUDGET
    assert rendered.count("\n") == memory_mod.RECALL_K + 1  # header + K lines


def test_oversize_freshest_record_recalls_the_ones_behind_it(tmp_path):
    """A corpus whose freshest record alone exceeds the whole budget still
    recalls every record behind it — never zero — and recalls the oversize
    one too, as an excerpt."""
    monster = record("monster", 0, finding=long_finding(
        memory_mod.RECALL_BUDGET * 3))
    behind = [record(f"b{i}", i + 1) for i in range(5)]
    seed(tmp_path, [monster] + behind)
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert got, "an oversize freshest record emptied the recall"
    ids = {r["id"] for r in got}
    assert {r["id"] for r in behind} <= ids
    # and the oversize one is not lost either: it renders as an excerpt
    assert "monster" in ids


# ---------- the excerpt bound ------------------------------------------------

def test_rendered_line_is_a_bounded_excerpt():
    rec = record("x", 0, finding=long_finding(1200))
    line = memory_mod.render_line(rec, "reviewer")
    assert len(line) <= memory_mod._LINE_ALLOWANCE - memory_mod._SUFFIX_ALLOWANCE
    assert "…" in line, "a cut excerpt says it was cut"
    assert line.endswith("(confirmed here before) id=x\n")
    # the examiner line excerpts the reason the same way
    ex = record("y", 0, status="refuted", finding=long_finding(1200))
    ex["reason"] = long_finding(1200, "z")
    eline = memory_mod.render_line(ex, "examiner")
    assert len(eline) <= memory_mod._LINE_ALLOWANCE - memory_mod._SUFFIX_ALLOWANCE
    assert "…" in eline and "refuted:" in eline


def test_a_short_finding_is_rendered_whole():
    rec = record("x", 0, finding="Short and complete.")
    assert "Short and complete. (confirmed here before)" in \
        memory_mod.render_line(rec, "reviewer")
    assert "…" not in memory_mod.render_line(rec, "reviewer")


def test_excerpt_is_one_line_and_prefers_a_sentence_end():
    limit = memory_mod.RECALL_EXCERPT
    # a multi-line finding stays ONE bullet: a newline inside it would break
    # the list the reader parses
    assert "\n" not in memory_mod.excerpt("first line\nsecond\n\tthird")
    assert memory_mod.excerpt("a  b\n c") == "a b c"
    # a sentence end inside the window's second half is where the cut lands
    head = "A" * (limit - 50) + "."
    text = head + " " + "b" * 500
    assert memory_mod.excerpt(text) == head + "…"
    # no sentence end: cut on a word, never mid-word
    words = long_finding(1200)
    got = memory_mod.excerpt(words)
    assert got.endswith("…") and len(got) <= limit
    assert not got[:-1].endswith("x")
    assert words.startswith(got[:-1].rstrip())
    # one unbroken token: hard cut, still bounded
    assert len(memory_mod.excerpt("q" * 1000)) <= limit
    # already short: returned as is
    assert memory_mod.excerpt("fine") == "fine"


# ---------- the budget is derived from K ---------------------------------------

def widest_rule(dir_prefix: str = "src/api") -> str:
    """A rule id that fills `_KEY_ALLOWANCE` to the character once rendered
    as "- [<rule> @ <dir_prefix>] " — the allowance's edge, not a 13-char
    key sitting 80 characters inside it. An allowance not bounded by
    construction has to be exercised at its edge."""
    width = memory_mod._KEY_ALLOWANCE - len(f"- [ @ {dir_prefix}] ")
    return "r" * width


def test_budget_is_sized_to_k_lines_at_the_bound(tmp_path):
    assert memory_mod.RECALL_BUDGET == (
        memory_mod._HEADER_ALLOWANCE
        + memory_mod.RECALL_K * memory_mod._LINE_ALLOWANCE)
    assert memory_mod._LINE_ALLOWANCE > memory_mod.RECALL_EXCERPT
    # K records with a key AT its allowance, long findings, the longest note
    # and a recurrence suffix on every line still fit. What this pins is the
    # DERIVATION — drop the key term from _LINE_ALLOWANCE and it recalls
    # fewer than K. It cannot pin the allowance's digit: the fixture derives
    # the key width from the constant, so the two move together. The
    # allowances themselves are pinned against what
    # render emits in test_allowances_cover_what_render_emits.
    seed(tmp_path, [record(f"r{i}", i, status="fixed", rule=widest_rule(),
                           finding=long_finding(3000, f"f{i}"))
                    for i in range(20)])
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert len(got) == memory_mod.RECALL_K
    rendered = memory_mod.render_recall(got, "reviewer")
    assert len(rendered) <= memory_mod.RECALL_BUDGET
    assert "[recurred 20x here]" in rendered
    key = f"- [{widest_rule()} @ src/api] "
    assert len(key) == memory_mod._KEY_ALLOWANCE, "fixture left the edge"
    for line in rendered.splitlines()[1:]:
        assert line.startswith(key)
        assert len(line) + 1 <= memory_mod._LINE_ALLOWANCE


def test_a_key_wider_than_its_allowance_degrades_never_empties(tmp_path):
    """Nothing caps a key's width, so the guarantee is CONDITIONAL and the
    page says so: inside the allowance K lines fit; past it a wide-keyed
    corpus returns fewer lines within the same budget — never zero, and
    never a line cut short."""
    wide = widest_rule() * 4
    seed(tmp_path, [record(f"r{i}", i, rule=wide,
                           finding=long_finding(3000, f"f{i}"))
                    for i in range(20)])
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert 0 < len(got) < memory_mod.RECALL_K
    rendered = memory_mod.render_recall(got, "reviewer")
    assert len(rendered) <= memory_mod.RECALL_BUDGET
    assert rendered.count(f"[{wide} @ src/api]") == len(got)


def test_an_unfittable_line_is_skipped_not_a_stop(tmp_path):
    """The excerpt bounds the finding; the KEY is not excerpted (a truncated
    key is a wrong key). A key wider than the whole budget is the one line
    that still cannot fit, and it is skipped — the records behind it are
    recalled exactly as if it were absent."""
    wide = record("wide", 0, dir_prefix="src/api/" + "d" * (
        memory_mod.RECALL_BUDGET + 10))
    behind = [record(f"b{i}", i + 1) for i in range(3)]
    seed(tmp_path, [wide] + behind)
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert [r["id"] for r in got] == ["b0", "b1", "b2"]


# ---------- ranking: recurrence key before recency -------------------------------

def test_recurrence_keys_are_ranked_before_restatements(tmp_path):
    """Fifteen fresh findings of one (rule, dir_prefix) shape, all NEWER than
    one finding each of five other shapes. Recency alone recalls the one
    shape ten times; the ranking must surface every shape first and fill
    the remainder from the recurrent one — and report the recurrence the
    corpus holds, not the count that happened to be selected."""
    crowd = [record(f"a{i}", i * 0.1, rule="rule-a") for i in range(15)]
    others = [record(f"o{n}", 10 + i, rule=f"rule-{n}")
              for i, n in enumerate("bcdef")]
    seed(tmp_path, crowd + others)
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert len(got) == memory_mod.RECALL_K
    assert {r["rule_id"] for r in got} == {"rule-a", "rule-b", "rule-c",
                                          "rule-d", "rule-e", "rule-f"}
    # the recurrent shape leads (15 > 1), its freshest instance first
    assert got[0]["id"] == "a0"
    # the singletons follow in the first pass, freshest first
    assert [r["id"] for r in got[1:6]] == ["ob", "oc", "od", "oe", "of"]
    rendered = memory_mod.render_recall(got, "reviewer")
    assert rendered.count("[recurred 15x here]") == 5
    assert "[recurred 1x" not in rendered


def test_a_fresh_shape_outranks_an_aging_recurrent_one(tmp_path):
    """Recurrence ranks shapes; the lifecycle still de-ranks aging evidence.
    A shape whose freshest instance is past FRESH_DAYS does not outrank one
    with a fresh instance, however often the aging one recurred."""
    aging = [record(f"g{i}", memory_mod.FRESH_DAYS + 10 + i, rule="rule-old")
             for i in range(5)]
    fresh = [record("f", 3, rule="rule-new")]
    seed(tmp_path, aging + fresh)
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert got[0]["id"] == "f"
    assert [r["id"] for r in got[1:]] == ["g0", "g1", "g2", "g3", "g4"]


def test_recall_records_carry_the_corpus_recurrence(tmp_path):
    """The count `render_recall` prints is computed by `recall` over every
    relevant record, and travels on the returned copy — the cache row is
    never rewritten, and a caller that renders raw records still gets the
    in-list count it always did."""
    seed(tmp_path, [record(f"a{i}", i, rule="rule-a") for i in range(12)]
         + [record("b", 0, rule="rule-b")])
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    by_id = {r["id"]: r for r in got}
    assert by_id["a0"]["recurred"] == 12 and by_id["b"]["recurred"] == 1
    assert all("recurred" not in r for r in memory_mod.load_records(tmp_path))
    raw = [record("p", 0), record("q", 1)]
    assert "[recurred 2x here]" in memory_mod.render_recall(raw, "reviewer")


# ---------- the plan packet reads through the same bound ----------------------------

def test_plan_packet_renders_priors_as_excerpts():
    text = long_finding(1200)
    packet = {"schema": "task-packet/v1", "repo": "sim", "task": "t",
              "area": "", "risk_tier": "UNKNOWN", "paths": ["src/api/"],
              "scenarios": [], "verify": {},
              "priors": [{"rule_id": "r", "dir_prefix": "src/api",
                          "finding": text, "status": "confirmed"}],
              "caveats": []}
    rendered = plan_mod.render(packet)
    assert text not in rendered, "the packet's markdown carried the whole finding"
    assert memory_mod.excerpt(text) in rendered
    # the JSON packet keeps the full text: it is the evidence, the markdown
    # is the reading
    assert packet["priors"][0]["finding"] == text


# ---------- the skip is visible, the allowances are pinned -------------------

def test_all_oversize_corpus_is_not_reported_as_empty(tmp_path):
    """Five relevant records whose every line outruns the whole budget.
    Printing "no relevant history" here would read exactly like an empty
    corpus, so the skip must leave a trace: `recall` reports what it saw, and
    the header says it."""
    seed(tmp_path, [record(f"w{i}", i, rule="r" * memory_mod.RECALL_BUDGET)
                    for i in range(5)])
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert list(got) == []
    assert got.relevant == 5 and got.skipped == 5
    rendered = memory_mod.render_recall(got, "reviewer")
    assert "no relevant history" not in rendered
    assert "0 record(s) of 5 relevant" in rendered
    assert "5 skipped" in rendered
    # a genuinely empty corpus still says so
    empty = memory_mod.recall(tmp_path, files=["docs/x.md"], role="reviewer")
    assert empty.relevant == 0
    assert "no relevant history" in memory_mod.render_recall(empty, "reviewer")
    # and a caller rendering a raw list it built itself gets the old header
    assert "no relevant history" in memory_mod.render_recall([], "reviewer")


def test_header_counts_relevant_and_skipped(tmp_path):
    """A recall thinned by wide keys names how many were relevant and how
    many lines were skipped, so a reader can tell "6 fit" from "6
    relevant"."""
    wide = widest_rule() * 4
    seed(tmp_path, [record(f"r{i}", i, rule=wide,
                           finding=long_finding(3000, f"f{i}"))
                    for i in range(20)])
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert 0 < len(got) < memory_mod.RECALL_K
    assert got.relevant == 20
    assert got.skipped == 20 - len(got)
    rendered = memory_mod.render_recall(got, "reviewer")
    assert f"{len(got)} record(s) of 20 relevant, {got.skipped} skipped" in rendered
    # K lines inside the allowance: nothing skipped, and the header says so
    seed(tmp_path, [record(f"n{i}", i) for i in range(20)])
    full = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    assert full.skipped == 0 and full.relevant == 20
    assert "10 record(s) of 20 relevant —" in memory_mod.render_recall(full, "reviewer")
    assert "skipped" not in memory_mod.render_recall(full, "reviewer")


def test_allowances_cover_what_render_emits(tmp_path):
    """Every term the budget is derived from is held to what render actually
    emits. Unasserted header, note and suffix allowances would let a longer
    header sentence or a trimmed constant break "K lines fit" with the suite
    green."""
    seed(tmp_path, [record("a", 0)])
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    got.relevant, got.skipped = 99999, 99999   # the widest counts the header prints
    for role in ("reviewer", "examiner"):
        header = memory_mod.render_recall(got, role).splitlines()[0]
        assert len(header) + 1 <= memory_mod._HEADER_ALLOWANCE, (role, len(header))
    fixed = record("f", 0, status="fixed", finding="x")
    line = memory_mod.render_line(fixed, "reviewer")
    note = line[line.index(" (since"):]
    assert len(note) <= memory_mod._NOTE_ALLOWANCE
    assert " id=" in line
    # The id is held at the width _record_id actually produces, not a typed
    # stand-in: a wider id would push every line past its allowance while a
    # literal here stayed green.
    real_id = memory_mod._record_id(fixed)
    assert len(" id=" + real_id) <= memory_mod._ID_ALLOWANCE, len(real_id)
    assert len(" [recurred 99999x here]") <= memory_mod._SUFFIX_ALLOWANCE
    assert memory_mod._LINE_ALLOWANCE == (
        memory_mod.RECALL_EXCERPT + memory_mod._KEY_ALLOWANCE
        + memory_mod._NOTE_ALLOWANCE + memory_mod._ID_ALLOWANCE
        + memory_mod._SUFFIX_ALLOWANCE)


def test_rendered_output_stays_within_budget_across_key_widths(tmp_path):
    """The budget is a bound on the RENDERED text at every key width, not
    only at the edge the derivation was sized for — the recurrence suffix
    is charged before it is rendered, so a line that fits without it does
    not push the whole past the budget with it."""
    edge = len(widest_rule())
    for width in (edge - 20, edge, edge + 12, edge + 40, edge + 200):
        seed(tmp_path, [record(f"r{i}", i, rule="r" * width,
                               finding=long_finding(3000, f"f{i}"))
                        for i in range(20)])
        got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
        rendered = memory_mod.render_recall(got, "reviewer")
        assert got, width
        assert len(rendered) <= memory_mod.RECALL_BUDGET, (width, len(rendered))
        # `skipped` counts lines the loop examined and dropped; it stops at
        # K, so the two sum to the corpus only when fewer than K fit
        assert len(got) == memory_mod.RECALL_K or len(got) + got.skipped == 20


def test_lines_carry_the_record_id(tmp_path):
    """An excerpt is a pointer only if the reader can follow it. Every line
    ends with `id=<record id>`, which greps cleanly in the ensure_ascii
    cache where an em dash or a quote in the opening words would not."""
    seed(tmp_path, [record("abc123def4567890", 0,
                           finding="Rename with no breadcrumb — " + "w" * 400)])
    got = memory_mod.recall(tmp_path, files=["src/api/y.py"], role="reviewer")
    rendered = memory_mod.render_recall(got, "reviewer")
    assert "id=abc123def4567890" in rendered
    assert "findings.jsonl" in rendered.splitlines()[0]
    raw = (tmp_path / ".warden" / "memory" / "findings.jsonl").read_text()
    assert "abc123def4567890" in raw
    assert "\\u2014" in raw and "\u2014" not in raw, "the cache is ensure_ascii"
    # the examiner line carries it too
    ex = record("fedcba9876543210", 0, status="refuted"); ex["reason"] = "no"
    assert "id=fedcba9876543210" in memory_mod.render_line(ex, "examiner")
    # a record with no id (a caller's own row) renders without a dangling pointer
    bare = record("x", 0); del bare["id"]
    assert "id=" not in memory_mod.render_line(bare, "reviewer")


def test_plan_packet_points_at_the_full_text():
    """`warden plan`'s stdout is the excerpted markdown; the full text lives
    in task-packet.json beside it. The reading names its evidence: each
    prior carries its id and the section says where the whole finding is."""
    packet = {"schema": "task-packet/v1", "repo": "sim", "task": "t",
              "area": "", "risk_tier": "UNKNOWN", "paths": ["src/api/"],
              "scenarios": [], "verify": {},
              "priors": [{"rule_id": "r", "dir_prefix": "src/api",
                          "finding": long_finding(1200), "status": "confirmed",
                          "id": "abc123def4567890"}],
              "caveats": []}
    rendered = plan_mod.render(packet)
    assert "id=abc123def4567890" in rendered
    assert "task-packet.json" in rendered
    assert "findings.jsonl" in rendered
    # a prior with no id (an older packet) renders without a dangling pointer
    del packet["priors"][0]["id"]
    assert "id=" not in plan_mod.render(packet)


def test_plan_build_carries_ids_and_a_skip_caveat(tmp_path):
    from test_plan import FakeConfig, write_hints
    write_hints(tmp_path, "api.yaml", {"area": "api", "scenarios": [
        {"name": "s", "source_files": ["src/api/"]}]})
    seed(tmp_path, [record("abc123def4567890", 0)]
         + [record(f"w{i}", i + 1, rule="r" * memory_mod.RECALL_BUDGET)
            for i in range(3)])
    packet = plan_mod.build(FakeConfig(tmp_path), task="t", area="api")
    assert [p["id"] for p in packet["priors"]] == ["abc123def4567890"]
    assert any("3 relevant prior(s) skipped" in c for c in packet["caveats"]), \
        packet["caveats"]
