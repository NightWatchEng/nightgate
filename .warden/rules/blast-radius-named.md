---
id: blast-radius-named
severity: MEDIUM
engine: claude
judges: behaviour
# The class landed only where a consumer can reach: of its twenty-one judged
# findings, warden/ 9, skills/ 6, docs/ 4 (every one on a wiki page),
# examples/ 1 and cage/ 1. The scope is those five plus the other surfaces
# this platform ships or a consumer copies: the scripts, the package
# metadata, the plugin marketplace manifest every pack install reads, and the
# front page. docs/ is the wiki only, because the design records and retros
# under the rest of docs/ are read by no consumer. This repo's own tests,
# shards and CI config reach no consumer either, so they are not in it.
applies_to: ["warden/**", "skills/**", "docs/wiki/**", "examples/**",
             "cage/**", "scripts/**", ".claude-plugin/**", "pyproject.toml",
             "README.md"]
# No excludes. The body quotes consumer breaks as worked examples, but no
# applies_to glob reaches .warden/, so the rule never selects its own file.
# The rule is NOT named `consumer-blast-radius`: a rule id equal to a
# candidate slug retroactively invalidates every shard filed under
# `unmapped:<slug>` (watched live on fail-open: LEVEL 5 to LEVEL 3). The
# legacy ids are grandfathered in .warden/certification.yaml. The charter
# lens keeps its own id, `consumer-blast-radius`, in graph.yaml.
covers: [consumer-blast-radius]
---
Does this change a shipped schema, artifact, or rendered file a pinned
consumer already depends on?

That question is the source: the `consumer-blast-radius` charter lens in
`graph.yaml` points at this rule, and this rule is the same obligation hashed into
`rules_version`. Twenty-one judged findings filed under the lens's slug (31
committed records, before restatements fold), fifteen upheld, three refuted,
three dismissed. The retro of 2026-09-27 read nineteen; a refutation and a
dismissal were attested within the hour after its window closed, while the
slug was still the lens's filing key. The retro counted four more escapes
in the class, merged defects fixed by a later PR, and none of them is in the
twenty-one.

A consumer here is anyone who runs this platform from outside this tree: a
repo that pins a warden version, the skill pack that ships from the default
branch, a repo `warden init` enrolls, and the example a consumer copies. The
pack and the pin move separately, so a change that is correct at this head
can still break a consumer on an older pin, or one on a newer pack.

Flag an added or modified line that breaks what such a consumer already runs
or reads. Evidence must name three things: the consumer, the surface it
depends on, and the concrete break (the exit code, the refusal, the step that
no longer runs, the level certification drops to). "This might affect
consumers" is not a finding. These are the shapes that recurred here, not a
general taxonomy:

- **The instruction the pinned warden cannot follow.** A skill tells every
  consumer to pass a flag, or to trust a signal, that no released warden has.
  One told every consumer to pass `--path`, which argparse refuses on any pin
  before the change that added it, so the step printed nothing. `warden init`
  wrote a `platform.pin` naming a version with neither `init` nor the flag the
  generated gate then passed.
- **The rendered gate that cannot run.** A workflow `warden init` writes, or
  one a consumer copies from a page or the example, that fails on its first
  run. An install step ran before checkout in a subdirectory gate, so every
  run failed at step one. A gate ran `go vet` and `npm ci` with no toolchain
  declared and exited 127 naming no cause. The example's gate ran `uv run`
  and no step installed uv.
- **The layout this repo does not have.** A change that holds for one flat
  checkout and breaks a consumer layout the platform targets: a `rules_dir`
  inside a git submodule, a nested checkout carrying its own `.warden/`, and
  one machine hosting two enrollments at different pins.
- **The hashed or emitted value that moves.** A change to what feeds
  `rules_version`, or to a field a consumer reads from an artifact, with no
  note that it moved. Routing a hashed surface through a different parser
  changes the hash for any file the two parsers read differently.
- **The fallback path the diff deletes.** A consumer on the documented
  fallback (no `review` block, or a pin that predates a subcommand) loses a
  behaviour, such as its charter lenses reaching a reviewer, because the diff
  removed the last instruction that served that path.
- **The skew nobody states.** A behaviour change to the pack or the gate with
  no pack/pin skew note, where the pages that record such changes carry one
  for every other change.

File a finding of this class under this rule, not under a sibling slug. The
corpus holds a narrower candidate, `breaking-consumer-migration` (a change
that breaks a pinned consumer's migration path), whose records include shapes
listed above; it is not covered here, so `attest write` still accepts
`unmapped:breaking-consumer-migration`, and filing there splits this rule's
precision history in two.

Do NOT flag: a consumer contract that did change when the guidance a consumer
reads already covers it and the refusal names its own remedy (one of the
three refutations); a break the diff is said to introduce when the unchanged
surface already produced it (another, on the example's gate); a
pin-dependent sentence that is explicitly conditional on the pin carrying
the change, where the consumer owes the same action on either pin (the
third); and a change to this repo's own tests, shards or CI config that no
consumer runs. Read the refutations before filing: each claimed a consumer
worse off, and none survived a read of what the consumer actually runs. The
three dismissals were not carve-outs. One finding held and was parked at the
repair cap as a tracked item. One was outside its PR's file boundary and went
to the tracked item for that work. The third held as a skew between a runner
and a reader from different pins, and was left because the change was its
tracked item's explicit ask and both ship from one install.

Read the counts through the caveats the retro recorded. Twenty-one judged is
below the promotion bar: the floor is 0.5 against 0.7. That bar governs
automating a rule, not writing one. Precision here is over FILED judgments,
and recall is unobservable from this corpus, except for the four escapes.
Writing the rule also changes what these findings do: an `unmapped:` record
below HIGH counted toward no repair round unless its reviewer declared
`judges: behaviour`, and this rule declares it once, for every finding.

MEDIUM is deliberate. `blocking_severities: [HIGH]`, so this reports while it
earns a precision history under its own id; the candidate class's counts do
not carry over to it, and raising it to a blocking severity is a separate
human decision with its own evidence.
