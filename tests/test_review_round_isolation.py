"""The carry-forward mandate detector, and the attestation schema it guards.

A reviewer report carried from one review round into another is refused at
`attest write`, and the attestation schema is the machine-readable contract a
payload is generated from. The tests here hold the schema's descriptions free
of any instruction to carry a report between rounds, and certify the detector
that reads them, one control per spelling.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# --- one instruction about earlier rounds' reports, not two ----------------

# A transfer verb, the thing transferred, and another round to transfer it
# from or to. A sentence carrying all three, with the transfer verb NOT
# negated, is telling the reader to put one round's reviewer output into a
# different round — the instruction the `attest write` check refuses.
_TRANSFER = re.compile(
    r"\b(cop(?:y|ies|ied|ying)|move[sd]?|moving|renam(?:e|es|ed|ing)"
    r"|carr(?:y|ies|ied|ying)|place[sd]?|puts?)\b", re.I)
_TRANSFERRED = re.compile(r"reviewers/|\breports?\b|\boutputs?\b", re.I)
_ANOTHER_ROUND = re.compile(
    r"\b(earlier|another|previous|prior|other|final|first|sibling)\b"
    r"[^.]{0,40}\brounds?\b|\brounds?\b[^.]{0,40}\b(earlier|before)\b", re.I)
# Negation scopes RIGHTWARD: a negator BEFORE the transfer verb negates it, one
# after it does not. Testing the whole sentence for a negator would leave
# "Copy each earlier round's report into this round's reviewers/, and do not
# rename it." silent — the mandate wearing an unrelated `not`. Reading
# position is what separates the protocol's own refusal ("no earlier round's
# report is copied, moved or renamed into it") from a mandate carrying a
# negation somewhere else.
#
# The vocabulary is exactly what the loop below certifies, one control per
# spelling, and no wider: an alternative that survives deletion with the test
# green is uncertified width. Two spellings are wrong outright — `n't` can
# never match inside `\b(...)\b` (in "Don't" the `n` has no word boundary
# before it), and `nor` SILENCES a real mandate ("Neither the roster nor the
# payload changes: copy each earlier round's report into the final round's
# `reviewers/`."), because a coordinator left of the verb is not a negation
# of it.
_NEGATORS = ("never", "not", "n't", "no", "cannot", "refuses", "refused",
             "forbids")
# BUILT from the tuple, so the vocabulary cannot be wider than what the loop
# below certifies — a hand-written pattern can keep stems like `refus\w*` /
# `forbid\w*` while the tuple names two spellings, the same over-width a
# `nor` alternative has. A contraction gets no leading
# `\b`: in "Don't" the `n` has no word boundary before it.
_NEGATOR = re.compile(
    "|".join((r"n['\u2019]t\b" if n == "n't" else r"\b" + n.replace(" ", r"\s+") + r"\b")
             for n in _NEGATORS), re.I)


def _carry_forward_mandates(text: str) -> list[str]:
    """Sentences instructing that a reviewer report be carried between rounds.

    Sentence-level, on the FLATTENED text, for the reason `_flat` exists: the
    mandate this catches was three wrapped lines."""
    out = []
    for sentence in re.split(r"(?<=[.!?;]) ", " ".join(text.split())):
        if not (_TRANSFERRED.search(sentence) and _ANOTHER_ROUND.search(sentence)):
            continue
        for verb in _TRANSFER.finditer(sentence):
            if not _NEGATOR.search(sentence[:verb.start()]):
                out.append(sentence)
                break
    return out


# The mandate in its shipped wording, and the spellings it can wear. Each
# control isolates a DIFFERENT alternative of `_TRANSFERRED` — deleting
# `reviewers/`, `\breports?\b` or `\boutputs?\b` fails exactly one of them,
# so no two controls exercise the same path. Every alternative of
# `_TRANSFER` and `_ANOTHER_ROUND` is certified too, by the two tables below.
_MANDATE_AS_SHIPPED = (          # copy · reports · "earlier ... round"
    "`--review-dir` names the FINAL round's `reviewers/`, so copy every "
    "earlier round's reports into it under a name that says its round "
    "(`cross-examiner-r2.json`) and claim each in the roster.")
_MANDATE_AS_A_MOVE = (           # move · reviewers/ ONLY · "final round"
    "Move each earlier round's `reviewers/` files into the final round's "
    "directory under a name that says which round they came from.")
_MANDATE_AS_A_RENAME = (         # rename · report ONLY · "previous round"
    "Rename the report from the previous round into this one's directory "
    "so the roster entry has a file to claim.")
_MANDATE_AS_A_CARRY = (          # carry · output ONLY · "round before"
    "Carry the cross-examiner's output from the round before into this "
    "round's directory, so every roster entry has a file.")
_MANDATE_WEARING_A_NEGATION = (  # a mandate with an unrelated `not` after it
    "Copy each earlier round's report into this round's `reviewers/`, and do "
    "not rename it.")

# ONE control per alternative of `_TRANSFER`, each sentence carrying that
# verb and no other, so deleting the alternative takes exactly this control
# red. `moving` and `renam(?:...|ing)` are separate rows
# because `move[sd]?` cannot match inside "moving" — there is no word
# boundary after `move` there.
#
# One row per INFLECTION, not per verb. A verb-level table leaves `ies`,
# `ied`, `ying`, `ed`, `ing` and the `d` of both `[sd]` classes deletable with
# this file green — nine alternatives whose deletion blinds the guard to a
# mandate written in that tense. The mutation harness enumerates the
# alternatives out of the compiled pattern, so a tenth inflection added to
# `_TRANSFER` without a row here is red rather than noticed.
_TRANSFER_CONTROLS = (
    ("cop…y",
     "Copy each earlier round's report into this round's `reviewers/`."),
    ("cop…ies",
     "The builder copies each earlier round's report into this round's "
     "`reviewers/`."),
    ("cop…ied",
     "Each earlier round's report is copied into this round's `reviewers/`."),
    ("cop…ying",
     "Finish by copying each earlier round's report into this round's "
     "`reviewers/`."),
    ("move (bare)",
     "Move each earlier round's report into this round's `reviewers/`."),
    ("move…s",
     "The builder moves each earlier round's report into this round's "
     "`reviewers/`."),
    ("move…d",
     "Each earlier round's report is moved into this round's `reviewers/`."),
    ("moving",
     "Finish by moving each earlier round's report into this round's "
     "`reviewers/`."),
    ("renam…e",
     "Rename each earlier round's report into this round's `reviewers/`."),
    ("renam…es",
     "The builder renames each earlier round's report into this round's "
     "`reviewers/`."),
    ("renam…ed",
     "Each earlier round's report is renamed into this round's `reviewers/`."),
    ("renam…ing",
     "Finish by renaming each earlier round's report into this round's "
     "`reviewers/`."),
    ("carr…y",
     "Carry each earlier round's report into this round's `reviewers/`."),
    ("carr…ies",
     "The builder carries each earlier round's report into this round's "
     "`reviewers/`."),
    ("carr…ied",
     "Each earlier round's report is carried into this round's `reviewers/`."),
    ("carr…ying",
     "Finish by carrying each earlier round's report into this round's "
     "`reviewers/`."),
    ("place (bare)",
     "Place each earlier round's report into this round's `reviewers/`."),
    ("place…s",
     "The builder places each earlier round's report into this round's "
     "`reviewers/`."),
    ("place…d",
     "Each earlier round's report is placed into this round's `reviewers/`."),
    ("put (bare)",
     "Put each earlier round's report into this round's `reviewers/`."),
    ("put…s",
     "The builder puts each earlier round's report into this round's "
     "`reviewers/`."),
)
# ONE control per alternative of `_ANOTHER_ROUND`, both branches. Each
# sentence names exactly one round-qualifier, and the two tail controls put
# their qualifier where the FIRST branch cannot also fire (no `round` follows
# it), so they certify the tail rather than riding on the head.
_ANOTHER_ROUND_CONTROLS = (
    ("earlier",
     "Copy the earlier round's report into this round's `reviewers/`."),
    ("another",
     "Copy another round's report into this round's `reviewers/`."),
    ("previous",
     "Copy the previous round's report into this round's `reviewers/`."),
    ("prior",
     "Copy the prior round's report into this round's `reviewers/`."),
    ("other",
     "Copy the other round's report into this round's `reviewers/`."),
    ("final",
     "Copy the final round's report into this round's `reviewers/`."),
    ("first",
     "Copy the first round's report into this round's `reviewers/`."),
    ("sibling",
     "Copy the sibling round's report into this round's `reviewers/`."),
    ("tail: round ... earlier",
     "Copy the report from the round minted earlier."),
    ("tail: round ... before",
     "Copy the report from the round minted before."),
)


def test_the_carry_forward_detector_certifies_every_negator_verb_and_round_spelling():
    """The detector the schema guard below runs, certified one control per
    spelling of each vocabulary it reads.

    `_carry_forward_mandates` is what the schema guard runs over every
    attestation schema description, so a vocabulary alternative no control
    exercises is a claim about what that guard reads rather than a property of
    it. A negator before the transfer verb silences a mandate and one after it
    does not; every transfer verb and every round-qualifier is reported; and
    each written mandate, one per alternative of `_TRANSFERRED`, is reported.
    """
    # The protocol's own refusal, in isolation, is not read as a mandate.
    assert _carry_forward_mandates(
        "Never copy a `reviewers/` file forward from an earlier round.") == []

    for negator in (*_NEGATORS, "n’t"):
        silenced = (f"{negator.capitalize()} carry an earlier round's report "
                    "into the final round's `reviewers/`.")
        assert _carry_forward_mandates(silenced) == [], (
            f"the negator {negator!r} before the verb does not negate it: "
            f"{silenced!r} is read as a mandate")
        # ONE sentence: the guard splits on `.!?;`, so a semicolon here would
        # put the negator in a sentence holding no transfer verb and make the
        # assertion unable to fail — it would read the same with `_NEGATOR`
        # matching everything.
        after = ("The roster is already true, so carry an earlier round's "
                 "report into the final round's `reviewers/`, which is "
                 f"{negator} in dispute.")
        assert ";" not in after and "." not in after[:-1], (
            "the control splits into two sentences, so the negator never "
            "reaches the verb's sentence and this assertion cannot fail")
        assert _carry_forward_mandates(after), (
            f"a mandate carrying {negator!r} AFTER the verb went silent — "
            "negation is positional, and this is the CR04 escape returning")

    # Every transfer verb and every round-qualifier is certified, one control
    # each: a mutation sweep once found 10 of 17 alternatives deletable with
    # this file green, which is a wider claim than the controls made good on.
    for alternative, control in _TRANSFER_CONTROLS:
        assert _carry_forward_mandates(control), (
            f"the transfer verb {alternative!r} is an alternative no control "
            f"exercises: {control!r} is not reported as a mandate")
    for alternative, control in _ANOTHER_ROUND_CONTROLS:
        assert _carry_forward_mandates(control), (
            f"the round-naming {alternative!r} is an alternative no control "
            f"exercises: {control!r} is not reported as a mandate")


    for name, mandate in (("as shipped", _MANDATE_AS_SHIPPED),
                          ("as a move", _MANDATE_AS_A_MOVE),
                          ("as a rename", _MANDATE_AS_A_RENAME),
                          ("as a carry", _MANDATE_AS_A_CARRY),
                          ("wearing a negation", _MANDATE_WEARING_A_NEGATION)):
        assert _carry_forward_mandates(mandate), (
            f"the mandate written {name} is not reported")


# --- the SCHEMA is the machine-readable half of that ruling ----------------

_SCHEMA = ROOT / "warden" / "schemas" / "attestation.schema.json"
# The claim the ruling retired, in the shape a schema wears: not an
# instruction to a builder but an assertion about where the reports live. No
# transfer verb, so `_carry_forward_mandates` cannot see it — a guard that
# only reused that function would have reported the shipped text clean.
_HOLD_VERBS = ("holds", "contains", "keeps")
_HOLD_QUANTIFIERS = ("every", "all", "each")
_ONE_DIR_HOLDS_EVERY_ROUND = re.compile(
    r"\b(" + "|".join(_HOLD_VERBS) + r")\b[^.]{0,30}"
    r"\b(" + "|".join(_HOLD_QUANTIFIERS) + r")\b[^.]{0,20}\brounds?\b", re.I)
_RETIRED_SCHEMA_CLAIM = (
    "The final round's directory holds every round's reports, which is what "
    "`--review-dir` verifies against.")


# The clause boundaries promoted to sentence boundaries. Every one carries a
# control below. Without a break, a mandate appended to the shipped ruling
# clause with that break — `", and "`, `", then "`, `", but "` or a spaced em
# dash — reports NOTHING, where the same probe with `", so "` fires.
#
# MUTATION SWEEP over this tuple: each break deleted in turn, with this file
# re-run under `PYTHONDONTWRITEBYTECODE=1` with `__pycache__` swept and
# `-p no:cacheprovider`, goes RED. That proves each break present is
# load-bearing; a set's certification says nothing about the spellings it
# omits.
#
# A BARE comma is deliberately not here: splitting on every `,` tears the
# shipped refusal's own continuation off its negator — "no earlier round's
# report is copied, moved or renamed into the final round's `reviewers/`"
# would be reported as a mandate, so making the refusal more explicit would
# go red with a message asserting the opposite of what it says. Each break
# here takes a comma plus a word, or a dash with spaces; none of them matches
# inside a verb list.
_CLAUSE_BREAKS = (": ", ", so ", ", and ", ", then ", ", but ", " — ")


def _clauses(text: str) -> str:
    """The text with each declared clause boundary promoted to a sentence one.

    `_carry_forward_mandates` scopes negation rightward across a whole
    sentence, which is what separates the protocol's own refusal from a
    mandate (see `_NEGATOR`). A schema description states that refusal as
    ONE long colon-joined sentence opening with `no`, so a mandate appended
    INSIDE it inherits that `no` and goes unreported by the sentence read
    alone. This is read IN ADDITION to the raw text, and the caller asserts
    on the union: a clause read alone loses every mandate whose verb and
    round-qualifier sit in different clauses.

    `_CLAUSE_BREAKS` is SIX breaks and each carries a control, which is the
    standard the set has to meet rather than a count to restate: a break no
    control exercises survives deletion, and the guard's own certification
    cannot see that hole. The bound in the other direction: splitting on
    every `,` tears the refusal's OWN continuation off its negator — 'no
    earlier round's report is copied, moved or renamed into the final round's
    `reviewers/`' would be reported as a mandate, so making the shipped
    refusal more explicit would go red with a message asserting the opposite
    of what it says. A bare comma continues a clause; each break here takes
    a comma plus a word, or a dash with spaces, and none of them matches
    inside a verb list."""
    return re.sub("(" + "|".join(re.escape(b) for b in _CLAUSE_BREAKS) + ")",
                  ". ", text)


def _descriptions(node: object) -> list[str]:
    """Every `description` string in the schema, at any depth."""
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                out.append(value)
            else:
                out.extend(_descriptions(value))
    elif isinstance(node, list):
        for value in node:
            out.extend(_descriptions(value))
    return out


def test_the_schema_does_not_carry_the_instruction_the_ruling_retired():
    """The schema must not assert that the final round's directory holds
    every round's reports. The SKILL tells no one to copy an earlier round's
    reports forward, and the schema is the machine-readable contract — a
    consumer or an agent generating a payload from a schema asserting that
    produces exactly the shape `carry_forward` refuses. Description text
    only: no property, type, required or enum here changes, so
    `warden/schemas/` stays additive-only.

    It proves it can see the retired claim before reporting clean."""
    descriptions = _descriptions(json.loads(_SCHEMA.read_text()))
    assert descriptions, "the schema carries no descriptions to read"
    for description in descriptions:
        # The UNION of both reads: the raw sentence catches a mandate whose
        # verb and round-qualifier sit in different comma clauses, and the
        # clause read catches one appended inside the ruling's own negated
        # clause. Taking only the clause read would trade one blind spot
        # for the other.
        assert (_carry_forward_mandates(description)
                + _carry_forward_mandates(_clauses(description))) == [], (
            "an attestation schema description instructs that a reviewer "
            f"report be carried between rounds: {description!r}")
        assert not _ONE_DIR_HOLDS_EVERY_ROUND.search(description), (
            "an attestation schema description still says one directory "
            f"holds more than its own round's reports: {description!r}")
    # It can see the sentence it exists to keep out — of BOTH shapes, so
    # "clean" is not reachable by a guard that reads for the wrong one.
    assert _ONE_DIR_HOLDS_EVERY_ROUND.search(_RETIRED_SCHEMA_CLAIM), (
        "the guard cannot see the claim the schema once shipped")
    assert _carry_forward_mandates(_MANDATE_AS_SHIPPED), (
        "the guard cannot see a mandate written as an instruction")
    # ...and it sees one planted where a later editor would most naturally
    # put it: INSIDE the ruling clause, whose leading `no` would otherwise
    # pre-negate every transfer verb after it. Read
    # as one sentence this is silent, which is why `_clauses` exists.
    reviewers = json.loads(_SCHEMA.read_text())["properties"]["reviewers"]
    round_field = reviewers["items"]["properties"]["round"]["description"]
    planted_inside = round_field.replace(
        "a sibling round's.",
        "a sibling round's, so copy every earlier round's reports into the "
        "final round's `reviewers/`.", 1)
    assert planted_inside != round_field, "the ruling clause moved"
    assert _carry_forward_mandates(planted_inside) == [], (
        "the sentence-level read is expected to MISS this one — if it now "
        "catches it, `_clauses` is no longer the thing being certified")
    assert _carry_forward_mandates(_clauses(planted_inside)), (
        "a mandate appended inside the ruling clause is not reported: the "
        "clause's leading negator swallows it, which is the CR02 escape")
    # Each clause break is certified by one control, and the raw read is
    # certified by the case the clause read CANNOT see — a mandate whose
    # verb and round-qualifier sit in different comma clauses. Both
    # directions are pinned here: the clause read cannot replace the raw one,
    # and it cannot split on every comma.
    for brk, control in (
            (": ", "No report is invented: copy every earlier round's report "
                   "into this round's `reviewers/`."),
            (", so ", "No report is invented, so copy every earlier round's "
                      "report into this round's `reviewers/`."),
            (", and ", "No report is invented, and copy every earlier round's "
                       "report into this round's `reviewers/`."),
            (", then ", "No report is invented, then copy every earlier "
                        "round's report into this round's `reviewers/`."),
            (", but ", "No report is invented, but copy every earlier round's "
                       "report into this round's `reviewers/`."),
            (" — ", "No report is invented — copy every earlier round's "
                    "report into this round's `reviewers/`.")):
        assert brk in _CLAUSE_BREAKS, (
            f"{brk!r} is no longer a declared clause break, so this control "
            "pins nothing — re-derive the list against the tuple as it stands")
        assert _carry_forward_mandates(_clauses(control)), (
            f"the clause break {brk!r} is an alternative no control "
            f"exercises: {control!r} is not reported as a mandate")
        assert _carry_forward_mandates(control) == [], (
            f"the {brk!r} control is reported by the RAW read too, so it "
            "measures the union and not this break")
    assert len(_CLAUSE_BREAKS) == 6, (
        "a clause break was added or removed without a control above — the "
        "set's own certification cannot see a spelling it omits")
    straddles_a_break = ("For every round earlier than this one: copy the "
                         "reports into `reviewers/`.")
    assert _carry_forward_mandates(straddles_a_break), (
        "the raw read no longer sees a mandate whose round-qualifier and "
        "transfer verb straddle a clause break")
    assert _carry_forward_mandates(_clauses(straddles_a_break)) == [], (
        "the clause read now sees this one too, so it no longer certifies "
        "why the raw read is kept — pick a control the split still hides")
    # THE UNION ITSELF is pinned, not just its two halves. Every control above
    # exercises the helpers directly and none runs the expression the loop
    # actually runs, so reverting the loop to either half alone would leave
    # them all green. Each plant below is caught by exactly ONE half, driven
    # through that same expression, so dropping either half takes this red.
    def _union(text: str) -> list[str]:
        return (_carry_forward_mandates(text)
                + _carry_forward_mandates(_clauses(text)))

    clause_only = round_field.replace(
        "a sibling round's.",
        "a sibling round's, so copy every earlier round's reports into the "
        "final round's `reviewers/`.", 1)
    raw_only = round_field.replace(
        "a sibling round's.",
        "a sibling round's. For every round earlier than this one: copy the "
        "reports into `reviewers/`.", 1)
    assert clause_only != round_field and raw_only != round_field
    assert _carry_forward_mandates(clause_only) == [] and _union(clause_only), (
        "the CLAUSE half of the union is doing nothing: a mandate only the "
        "clause read can see is not reported through the loop's expression")
    assert (_carry_forward_mandates(_clauses(raw_only)) == []
            and _union(raw_only)), (
        "the RAW half of the union is doing nothing: a mandate only the raw "
        "read can see is not reported through the loop's expression")

    # ...and a correctly-worded refusal whose continuation names another
    # round is NOT read as a mandate: splitting on every comma would tear
    # this sentence's continuation off its own negator.
    assert _carry_forward_mandates(_clauses(
        "no earlier round's report is copied, moved or renamed into the "
        "final round's `reviewers/`.")) == [], (
        "a refusal is reported as a mandate: the clause split is wider than "
        "the breaks its controls certify")
    # ...and every alternative of the claim pattern is exercised, one control
    # each, for the reason the mandate patterns below are.
    for verb in _HOLD_VERBS:
        for quantifier in _HOLD_QUANTIFIERS:
            control = (f"The final round's directory {verb} {quantifier} "
                       "round's reports.")
            assert _ONE_DIR_HOLDS_EVERY_ROUND.search(control), (
                f"the spelling {verb!r}/{quantifier!r} is an alternative no "
                f"control exercises: {control!r} is not read as the claim")
    # Absence must not be reachable by deleting the description: the field
    # still says what the ruling put in its place.
    assert "One attestation covers ONE round" in round_field
