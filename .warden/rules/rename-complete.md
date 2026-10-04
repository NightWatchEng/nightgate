---
id: rename-complete
severity: MEDIUM
engine: claude
judges: wording
applies_to: ["**"]
# Six judged candidate records, six upheld, earned across the night->run
# vocabulary retirements. They landed everywhere a
# rename has to reach: docs 1, docs/wiki 2, skills 1, .warden/rules 1, cage 1
# — no directory carve-out is honest, so applies_to is **. The rule judges a
# DIFF's intent (engine: claude), not text, so a rule body naming rename
# surfaces does not self-match, and no excludes are needed.
# Not named `incomplete-rename`: a rule id that equals a candidate slug
# retroactively invalidates every shard filed under `unmapped:<slug>` — attest
# refuses the slug, E-02 fails across the committed corpus, and certification
# drops (watched live on fail-open: LEVEL 5 to LEVEL 3). `covers:` is the seam
# built for exactly this; the 6 legacy ids are grandfathered in
# .warden/certification.yaml.
covers: [incomplete-rename]
---
When a diff renames a symbol, file, config key, or vocabulary term, does the
rename reach EVERY surface that names the old thing — or only the ones a grep
of the source tree happens to see?

Six judged records, six upheld — the class earned almost entirely across the
`night` -> `run` retirements, where the same shape kept reappearing: a rename
that swept the code a grep reads and missed the surfaces it does not. The
instances, from those records: the sibling retro skill still stated the old
ledger contract; the policy contract and two wiki pages kept night vocabulary in
the very text being de-night-ed, one of them inside a rendered diagram node a
prose search skips; the gate's own `wiki-fidelity.md` still routed the cage to
the retired name; `cage/measure.py` keyed its API and prose on `nights` after
the concept was gone.

The confirmable move is boring and effective: take the retired term from the
diff and search the WHOLE working tree for it — a file the diff did not touch
that still carries the old name is a filable finding, because you can NAME the
term and the file. All six earning records were exactly this: in-repo files
outside the DIFF, not outside a whole-tree grep. Evidence must NAME the old
term and the file that still carries it — a hunch that "something was probably
missed" is not a finding.

Surfaces IN the tree — filable, because you can point at the file:

- **Hand-authored files a generator never rewrites.** `enroll` regenerates
  the files it owns; a hand-written one (the cage `prompt.md`, a pinned
  snippet, a diagram node) is invisible to a grep aimed only at *source*, but
  a whole-tree grep finds it. A rename that updated the generated side and not
  the authored side is half done.
- **String literals and prose UNDER TEST.** A blanket regex over `tests/`
  rewrites the assertions and the docstrings that explain the retired word,
  turning the test vacuous while it stays green — this is also a
  test-cannot-fail instance, so the fix is a named test that would fail if the
  old path returned, not a search-and-replace.
- **Compat bridges retired without an absence test.** Deleting the old path is
  not proving it is gone: a bridge removed without a test that asserts the old
  name no longer resolves can be silently resurrected by a stale cache or a
  pre-rename installed pack — the missing test is itself the in-tree finding.

Surfaces OUTSIDE the tree — raise as a QUESTION, not a filed finding, since a
diff-only reviewer cannot name the file that still carries the term:

- **Caches and installed artifacts.** A plugin cache keeps RETIRED versions
  beside current ones; a manifest version left unbumped gives installers no
  signal to refresh. Ask whether the cache/manifest was refreshed — you cannot
  confirm it from the diff.
- **Tracker comments and out-of-band records.** `bd list | grep` does not read
  bd COMMENTS; a rename checked only against titles misses markers real beads
  carry in their bodies (the `night-attempted:` markers a removal nearly
  reopened retry-grinding over). Ask whether comment-bodies were swept.

Do NOT flag: a rename whose old term is DELIBERATELY kept inside a documented
compatibility marker or a "renamed from X" changelog line (the old name as
history, not as a live path); a partial rename the diff's own description
scopes as one step of several with the rest tracked; a match inside this
rule's or another rule's body, which quotes retired terms as examples.
