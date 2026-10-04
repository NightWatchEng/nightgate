"""A schema description that describes a retired mechanism.

The schemas under `warden/schemas/` are the machine-readable contract: an agent
or a consumer generating a payload reads the DESCRIPTION, so a description of a
mechanism the org has retired produces exactly the shape warden refuses. The
same holds for `reviewers[].round`, which must not tell callers to carry an
earlier round's reports into the final round's directory while `carry_forward`
refuses it by byte identity;
`tests/test_review_round_isolation.py::test_the_schema_does_not_carry_the_instruction_the_ruling_retired`
guards that one.

This is the same guard for `findings[].round`. The protocol files a still-open
finding RE-RAISED under the round that re-confirmed it — the only round
`attest write` will join it to — so a description reading "the review round
this finding FIRST arrived in" diverges from the protocol for every carried
finding. The schema states the re-raise rule, and this test keeps it from
stating the retired reading.

Description text only — no property, type, required or enum changes, so
`warden/schemas/` stays additive-only and `schema-freeze` has nothing to bite.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCHEMA = ROOT / "warden" / "schemas" / "attestation.schema.json"
# The retired reading, verbatim as the schema once stated it, so the matcher
# below can be shown seeing it.
_RETIRED = ("The review round this finding first arrived in, matching the "
            "`round` of the roster entry its `lens` joins to.")

# "first arrived in" / "first raised in" / "originally arrived in" stated as what
# the field MEANS — not as the thing the description is contrasting against.
_FIRST_ARRIVED = re.compile(
    r"\b(?:the\s+)?(?:review\s+)?round\s+(?:this|the|a)\s+finding\s+"
    r"(?:first|originally)\s+(?:arrived|appeared|was\s+raised)", re.I)


def _round_description() -> str:
    doc = json.loads(SCHEMA.read_text())
    return doc["properties"]["findings"]["items"]["properties"]["round"][
        "description"]


def test_the_findings_round_description_states_the_re_raise_rule():
    """The field means the round whose dispatch put the finding in THIS payload
    — for a carried finding, the round that re-confirmed it. A reader who takes
    the old reading leaves a still-open finding out of the next round's payload,
    and `warden round classify` then reads that round clean over a defect the
    review never closed."""
    text = _round_description()
    assert "RE-RAISED" in text or "re-raised" in text, (
        "the description does not mention the re-raise at all, so a payload "
        f"generator has no way to know the rule: {text!r}")
    assert re.search(r"re-?confirm", text, re.I), (
        "the description does not say which round a carried finding is filed "
        f"under — the whole content of the carried-finding ruling: {text!r}")


def test_the_description_no_longer_asserts_the_retired_reading():
    """The negative half. 'First arrived in' is the retired reading, and it must
    not stand as what the field means — only as the thing the new sentence
    contrasts against, which is how the shipped description uses it ('NOT the
    round it first arrived in')."""
    text = _round_description()
    for hit in _FIRST_ARRIVED.finditer(text):
        before = text[max(0, hit.start() - 40):hit.start()].lower()
        assert "not " in before, (
            "the description asserts the retired first-arrived reading: "
            f"...{text[max(0, hit.start() - 60):hit.end() + 20]!r}")


def test_this_guard_can_see_the_sentence_it_exists_to_keep_out():
    """A guard that has never failed proves nothing. Run the matcher over the
    retired description: it must fire, with no preceding negation to excuse
    it."""
    hits = list(_FIRST_ARRIVED.finditer(_RETIRED))
    assert hits, ("the matcher cannot see the reading the schema used to "
                  "carry, so its silence on the current text means nothing")
    for hit in hits:
        assert "not " not in _RETIRED[max(0, hit.start() - 40):hit.start()].lower()
