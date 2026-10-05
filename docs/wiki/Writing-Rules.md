# Writing Rules

A rule is a markdown file with YAML frontmatter under `review.rules_dir`
(conventionally `.warden/rules/`). The parser requires `id`,
`severity` (HIGH/MEDIUM/LOW), `engine` (declarative/python/claude), a non-empty
`applies_to` glob list, and a non-empty body. Duplicate ids are an error, and
so is a `checks:` entry carrying a bad pattern or an unknown key — enrollment
breaks loudly at load time rather than a rule silently never matching.

**Frontmatter keys are not checked that way.** The loader reads the keys it
knows and ignores the rest, so a typo one level up loads clean and silently
does nothing: `implments:` leaves a catalog entry counted as unenforced, and
`pasued:` leaves a rule you meant to pause still enforcing.

The pause pair is the one place a typo *is* caught, in both directions:
`paused: true` beside a misspelled `paused_resaon:` fails at load (a pause with
no reason), and a correct `paused_reason:` beside a misspelled `pasued:` fails
too (a reason with no pause). Everywhere else, copy the spellings below rather
than typing them.

An optional `judges:` key says what a rule's findings are about: `behaviour` (the default) or `wording` (a comment, doc or message not matching the code). Any other value fails at load. `warden round classify` does not count a repair round for a wording finding below every blocking severity. It reads `judges:` from the rule as committed at the round head, so the key must be committed before the round is minted; an uncommitted edit to it changes nothing.

Every rule file is hashed into `rules_version`, so a rule edit is a policy
edit: it changes the version stamped on every subsequent verdict.

## The three engines

| Engine | Runs where | Cost to own |
|---|---|---|
| `declarative` | warden executes the `checks:` in the rule's own frontmatter, in CI, at $0 | a rule file — no Python, no platform change |
| `python` | a checker in warden's core pack or your `.warden/checkers/*.py` | a reviewed Python module |
| `claude` | deferred to the pre-PR review session; shows as *deferred* in the CI comment | judgment, recorded by `warden attest` |

**Start declarative.** Reach for `python` only when a check needs logic a regex
cannot express, and `claude` only when the judgment is genuinely a judgment.

### `engine: declarative`

The cheapest rule you can own. `checks:` carries a regex and a message, plus
optional `globs`, `flags` and `scope` (`added` | `file`). A must-be-present
check is `require: true`, and it needs `scope: file` — paired with the default
`added` scope it raises at load time, because "this text is missing" is not a
question you can ask of a diff hunk.

```markdown
---
id: no-debug-artifacts
severity: MEDIUM
engine: declarative
applies_to: ["**/*.py"]
checks:
  - id: print-debug
    pattern: 'print\("DEBUG'
    scope: added
    message: "debug print left in the diff"
---
Debug artifacts do not ship. Remove the print, or promote it to real logging.
```

A deliberate exception is carved out **per line, never per file**:

```python
subprocess.run(cmd, shell=True)  # warden:allow(shell-true): the command comes from repo.yaml verify scopes, which are reviewed policy rather than untrusted input
```

The marker must directly follow a comment leader (`#`, `//`, `/*`, `<!--`, `;`,
`--`), so a string that merely mentions the syntax cannot suppress. A line
regex cannot parse host-language strings, so a string quoting the marker *with*
its leader still can — but that shape at least reads as a marker to a reviewer.
The reason has a substance floor — at least 8 words over two characters, 6 of
them distinct — so `todo`, `.` and "trusted input" all suppress nothing. Check ids
must be unique across your whole ruleset, because the marker keys on them.

### `engine: python`

Either warden's core pack (`mechanical.py`) or your own module in
`.warden/checkers/` exporting `CHECKERS`. The rule `id` must match a registered
checker — an unknown id fails at review time, and review fails closed on a
`python` rule with no checker behind it. Copying such a rule file means copying
its checker module too.

Repo-specific facts go in the rule's `params:` frontmatter, **never** into
checker code. `examples/hello-svc/.warden/checkers/handler_contract.py` is the
working example and documents the contract in its header.

This is `examples/hello-svc/.warden/rules/secrets-in-diff.md`, verbatim — a
test pins the two identical, so what you copy is what CI enrolls:

```markdown
---
id: secrets-in-diff
severity: HIGH
engine: python
applies_to: ["**"]
# Rule bodies quote the very patterns they hunt — never scan them.
excludes: [".warden/rules/**"]
---
Flag any ADDED line that introduces a credential or secret:

- API keys and tokens: provider API keys (e.g. `sk-...`), JWTs, GitHub PATs,
  OAuth client secrets, cloud access key IDs.
- Passwords or connection strings with embedded passwords
  (any `scheme://user:password@host` form).
- `.env` file contents or hardcoded values that clearly belong in env vars
  (repo rule: secrets come from the environment; never commit or log them).

Do NOT flag: obvious placeholders (`sk-ant-xxx`, `<YOUR_KEY>`, `example`,
`redacted`), test fixtures with clearly fake values, or code that only READS
configuration from the environment.

Evidence must quote the exact offending added line.
```

Note the `excludes:` line. It is prophylaxis, not a fix for an observed
failure: today's patterns do not match this body, but a rule that hunts
credential shapes while quoting them is one pattern widening away from
flagging itself, and the exclude costs nothing.

### `engine: claude`

A judgment rule. It is evaluated pre-PR inside a review session and recorded
via `warden attest`, not in CI. Ship judgment rules at MEDIUM against
`blocking_severities: [HIGH]` while they earn a precision history — they report
without blocking, and `warden memory stats` tells you when they have earned
promotion.

## Which severities block

`repo.yaml`'s `review.blocking_severities` decides, and it is part of
`rules_version` — flipping it is a policy edit. The common shape is
`[HIGH]`: mechanical HIGH rules stop a merge, everything else reports.

## Pausing a rule

Any rule may carry `paused: true` plus a required `paused_reason:`. A paused
rule stops producing findings but keeps its file, its history, and its place in
`rules_version`, and the pause is announced in `explain`, in the sticky PR
comment, and in `memory stats`.

A paused rule also **stops counting as coverage**: a catalog entry it
`implements:` returns to the gap in `warden rules recommend`, named with the
pause and its reason. Pausing therefore costs a visible number rather than
quietly opening a hole the report calls covered — unless another *unpaused*
rule implements the same entry, in which case coverage genuinely holds.

`warden autonomy pause` does this for you on any rule the corpus has refuted in
three review rounds running, writing the reason and a pause backtest artifact.
It refuses a pause that would push `UNANSWERED` past a declared ceiling. It
edits the working tree only: a caller commits, a human merges.

## Two declared links, and why nothing is matched by name

- `implements: [entry-id]` — a rule of yours says which catalog entry it
  satisfies. Declare it, or the report keeps recommending an entry you already
  guard. The alternative is a tool assuming coverage you never claimed.
- `covers: [defect-class]` — a rule written to answer a recurring `unmapped:`
  defect class says so, and `memory stats` stops proposing a rule that already
  shipped under a different name. Once a rule covers a class, `attest write`
  and `memory ingest` refuse new `unmapped:<slug>` findings for it and name the
  rule to file them under, so the judgments reach that rule's precision.

The catalog speaks CWE/OWASP; the corpus speaks your repo's defect vocabulary.
Nothing is matched by name across that boundary, in either direction.

## The guardrail catalog

The platform ships a catalog of candidate rules a repo might be missing,
grounded in the CWE Top 25 (2024), OWASP Top 10:2025, and the Google reviewer
lenses. Every entry cites its source, says when it applies, states what a wrong
firing costs, and declares an engine.

```sh
warden catalog list --engine declarative
warden catalog show sql-injection
warden rules recommend
```

A fourth engine value, `not-a-rule`, is a **catalog verdict**, not a rule
engine: no engine can check this class — insecure design is one — so the entry
ships `instead:` and no starter.

Entries are candidates. Nothing is enforced until you copy a starter into your
rules dir and a human merges it.

### Answering the gap

`warden rules recommend` leads with **UNANSWERED: N**. Three things shrink it:
adopting an entry, answering it in `.warden/catalog-answers.yaml`, or — on a
warden at v3.0.3 or later — declaring each component's `lang:` in `repo.yaml`.

```yaml
version: 1
answers:
  - id: sql-injection
    verdict: not-applicable      # or: deferred (which requires a `bead:`)
    reason: >-
      No database and no SQL surface anywhere in the tree. Flips the moment
      this repo stores anything in one.

ceiling:
  max_unanswered: 3
  rationale: >-
    Must-not-grow, set when the gap stood at 3. A new entry goes red and must
    be answered rather than absorbed.
```

An answer is a **record that thinking happened, not an exemption from it.** The
loader refuses a verdict with no argument and a deferral with no tracker id.

An entry that ships a mechanical starter is re-measured every run: a
`not-applicable` verdict whose starter starts matching is **CONTRADICTED BY THE
CODE**, counted as unanswered again, and shown with its refutation — so a false
waiver cannot buy its way past the ceiling. The 25 entries that ship no starter — every `claude`, `python` and
`not-a-rule` one — are marked **NOT MECHANICALLY CHECKABLE** and rest on their
argument, which is why that argument is mandatory: nothing can ever contradict
those waivers. An answer the scan could not reach is **UNVERIFIED** and counted as
unanswered, because "could not look" is not "clean".

The third is about which language a starter reads. A catalog entry's `langs:`
names the languages whose idioms its starter matches — `shell=True`,
`pickle.loads` and `except: pass` are Python spellings, so seven entries carry
`langs: [python]`; an entry without `langs:` reads no language in particular.
`recommend` reads the `lang:` of every component in `repo.yaml`, and an entry
whose `langs:` names none of them is set aside under **OTHER LANGUAGES**
instead of recommended, and is not counted in UNANSWERED. Setting aside is
narrow: only an entry that is unanswered, whose rule is not paused, and whose
starter measurably matches nothing in this tree — a match, or a scan that
could not finish, keeps the row, with a note saying why. The class may still
matter in that repo; no starter in its language exists yet, and `warden
catalog show <id>` says what each one guards. No component declaring a
language the catalog tags (`python`, `node`, `go`) sets nothing aside, and a
LIMITS line says so. On a repo that declares one of those languages but not
Python, a report recommending an `engine:python` entry says, under PRIOR ART,
that the engine names the language the checker is written in, not the one it
reads.

v3.0.3 is the first release that carries this paragraph: v3.0.2 and earlier have
no `langs:` key and no OTHER LANGUAGES section, so on a repo pinned there a
declared `lang:` sets nothing aside and moves no count.

The bare `warden rules recommend` enforces the declared ceiling: exit 1 above
it, exit 2 when the gap or the ceiling cannot be read. That is what a CI step
should run — one source of truth, no count re-typed into a workflow.

### Evidence versus prior art

The report never lets the second borrow the first's authority.

- **EVIDENCE-BASED** rows have at least `EVIDENCE_MIN_N` (3) judged findings in
  your own corpus:
  *this went wrong here, N times*. Findings that were refuted or dismissed
  never count as evidence for an entry.
- **PRIOR ART** rows have fewer. Published sources say the class matters; your
  repo has judged it once, twice, or never — and the count is shown, so a
  silent class is distinguishable from a barely-seen one.

Where an entry ships a mechanical starter, the checks are run against your tree
and the row states **how many lines in it match today** — an upper bound on the
noise, not a count of findings the rule would produce, since a rule scoped to
`added` fires only on lines a diff touches (and `require: true` checks are left
out of the count entirely). You adopt with a measured ceiling rather than a
hope. A rule that fires noisily is worse
than no rule: it teaches people to ignore the gate.

## Retiring rules

`warden rules lifecycle` is the subtractive half — it reads every rule you
already have against its own record and proposes **RETIRE?** (never fired, and
it names which of three silences: never-selected, evaluated-silent, or
unobservable-firing — a rule whose firing the replay cannot see at all; a
window too thin to judge returns **UNTESTED** instead), **DEMOTE?**
(the corpus argues it down), and **NARROW?** (fires on most diffs it sees —
narrow `applies_to`, never delete).

Proposals, never actions, and every one states the absence-of-evidence problem:
**a rule that has never fired may be the reason the thing it guards has never
happened.** Where a rule can be replayed, the report also says whether the
hazard is still present in the tree unflagged — which argues repair before
retirement.

---

Next: [Gate Pipeline](Gate-Pipeline.md) · [Memory](Memory.md) ·
[CLI Reference](CLI-Reference.md)
