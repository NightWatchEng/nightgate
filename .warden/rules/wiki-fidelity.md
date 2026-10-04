---
id: wiki-fidelity
severity: MEDIUM
engine: claude
judges: wording
applies_to: ["warden/**", "cage/**", "skills/**", "docs/wiki/**"]
# This body quotes wiki claims as examples — never scan the rules dir itself.
excludes: [".warden/rules/**"]
# This rule IS what the `unmapped:docs-drift` candidate class became — nine
# recurrences of "the page no longer describes the system", converged on
# independently. Declared so `memory stats` reports the class as covered
# instead of proposing a rule that already shipped under another name.
covers: [docs-drift]
implements: ["review-lens-documentation"]
---
`docs/wiki/` is the reference a consumer enrolls from, so a page that
misdescribes the system is a defect, not a chore. Little of a page is pinned
by a test. `tests/test_runnable_docs.py` reads the fenced blocks of every
`docs/wiki/` page and the README: each `warden` or `cage` invocation must name
a subcommand and flags that CLI's `--help` accepts, and each Python, YAML and
TOML block must parse. It runs nothing, and it never reads an inline command
span in prose or a table. Contract pins hold a few copies equal to their
source: `Adopting.md`'s `repo.yaml` snippet and its workflow snippet's action
SHAs, the rule in `Writing-Rules.md`, the samples in `Graph-Layer.md`
(`tests/test_docs.py`), and `Adopting.md`'s ruleset-probe table, driven against
the probe (`tests/test_doc_fidelity.py`). A few other tests pin a named
contract string on a page. Most of a page has no test behind it, command
tables, schema keys, ladder rows and prose included, and that is yours to judge.
Does the wiki page for the system this diff changes still describe it
accurately?

Name the page that teaches each system the diff touches — warden commands and
the gate -> `Gate-Pipeline.md`; cage -> `The-Cage.md`; skills ->
`Skill-Pack.md`; memory -> `Memory.md`; graph -> `Graph-Layer.md`; enrollment
and `repo.yaml` -> `Adopting.md`; the shape of the platform ->
`Architecture.md` — then read it against the diff. Flag when the page teaches
behavior this diff has changed, omits a capability or option this diff adds,
or carries a claim the code now contradicts. Real examples, all found live:
`certify` missing from the command table, `protected_paths:` documented
nowhere, and "Every warden command writes an evidence run dir", which is false.

Do NOT flag wording preferences, internals a consumer never touches, prose the
diff leaves true, or a page belonging to some other system the diff sits near.
A wiki-only diff is in scope: it can make a page wrong all by itself.

Do not flag a deliberately abstracted artifact for what it leaves out. A C4
diagram, a sequence diagram, or any levelled view omits by design: an L1 that
draws every dependency and an L3 that draws every module are broken levels,
not accurate ones. Flag such a levelled view's omission only when the page
claims the view is complete ("all four external systems") and the claim is
now false, or when what is drawn contradicts the code. Selection is not
misdescription. This carve-out covers levelled ARTIFACTS only: a prose page
that teaches a system is still flagged for omitting a capability or option
the diff adds, exactly as the trigger above says — `certify` missing from the
command table stays a finding. (#150: five refutations, four
naming level-appropriate abstraction, in a round that upheld this rule
fifteen times.)

Evidence: quote the wiki line and the changed code or config that contradicts
it, and name the page.

MEDIUM is deliberate. `blocking_severities: [HIGH]`, so this rule reports
without blocking — it earns a precision history first, and those judged
findings are the data `warden memory stats` ranks. A day-one judgment rule that
blocks is how a gate loses its credibility.
