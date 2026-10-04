# v2.3.0 release notes

What v2.3.0 carries since v2.2.0: 109 commits on `main`, one PR each,
PR #255 to PR #379. Grouped by the product each commit's scope names;
test-only and docs-only PRs are listed by number at the end of each group.
The procedure that cuts it is [Releasing](../wiki/Releasing.md).

## For a consumer moving its pin from v2.2.0

- `warden skills pin` (#331, #353): checks that the installed skill pack is
  the one `platform.pin` names. Re-add the marketplace at `#v2.3.0`
  ([Installation](../wiki/Installation.md) section 3), then run it.
- `runner: pytest` on a verify step (#348, #364) puts pytest's counts on
  `warden verify`'s summary line. v2.2.0's schema refuses the key.
- The gate `warden init` writes gains a `proportionate review tier` step
  (#376) and a toolchain step (#284). A bump does not rewrite a workflow
  already written: copy the steps in by hand.
- A gate `warden init` 2.2.0 wrote below the git root fails at its install
  step; [Installation](../wiki/Installation.md) section 1 gives the repair.

## warden

- #260 a gate credited only to the enrollment GitHub runs it for
- #270 derive the round count from the shards a rebase carries
- #272 a terminal closure round attests a head the cap's repair moved
- #273 the carve-out admits the attestation shard a review round leaves
- #277 invalid rosters, a hidden reset, and a cap CI never counted
- #278 attest check refuses a commit the round never covered
- #279 declare check refuses five more ways extglob can be on at a `!(`
- #280 three claims the code did not keep
- #284 the consumer gate declares the toolchain its verify scopes run
- #291 a shard records the base its round was minted against
- #292 a change that provably cannot alter behaviour earns a lighter round
- #293 three shell shapes the scanner misread
- #295 a declared prose root earns the light round
- #299 warden certify takes 3 seconds instead of 48
- #300 a shard carries its base caveat, a light crew its proof
- #306 the ancestry contract says what git does
- #307 delete the prose arm; Markdown always takes the full rounds
- #309 certify --goal reports the redesign's five run-time evidence items
- #317 an unmapped finding is wording unless it declares behaviour
- #318 autonomy adopt refuses a covers:/implements: draft at the writer
- #321 certify --goal reads the CI init proof as enrollment evidence
- #323 the delegate scan follows a local alias or refuses it, never skips
- #326 D-03 refuses when a subdirectory repo.yaml takes the root's gate
- #328 a gate naming an owner outside the checkout is no enrollment
- #329 an enrollment path through a symlink back into the repo is none
- #331 the skill pack installs at platform.pin; skills pin refuses a skew
- #332 a case-variant prefix is no enrollment on a case-insensitive disk
- #348 verify's summary line prints pytest counts per scope
- #351 runs_pytest reads uv options and wrappers, and says when it cannot
- #353 init and Installation.md name the release that carries skills pin
- #354 attest write refuses a tag the vocabulary rejects before writing
- #356 runs_pytest tokenises the whole command once
- #362 withhold pytest counts from a step that can run several sessions
- #364 a verify step declares runner: pytest; the shell is not parsed
- #365 goal's enrollment reader checks the invocation line, not the run
- #367 the CI attest step reads every shard in one git batch
- #370 declare check D-05 reads the forge's auto-merge setting
- #371 one YAML loader, libyaml when present, acceptance proven identical
- #375 each YAML document is parsed once per run, outputs proven same
- #376 init and hello-svc emit the classify --enforce step consumers lacked
- docs and tests: #262, #298

## graph

- #259 review crew in graph.yaml; attest checks it
- #263 the skills dispatch the crew graph.yaml declares
- #269 graph.yaml declares delegation terms and refuses the grant
- #361 the blast-radius lens points at its rule; pre-pr-review names it

## memory

- #257 scheduled promotion watch; S-04 needs a promotion
- #261 promote evidence-attribution to a declared rule
- #265 the retro reads every stats section; the watch names every input
- #311 first machine adoption, subject-required by warden autonomy adopt
- #347 tests-bite names hermeticity, the class behind seven escapes
- #352 rules lifecycle replays python rules and prints their firing
- #360 consumer-blast-radius becomes a rule that covers its slug
- #363 blast-radius-named names the lens pointer, not the gone checklist
- #373 receipts state their re-read trigger in one key; prose refused
- #374 unsafe-deserialization names yamlio.load as the one loader
- #377 unsafe-deserialization prescribes yamlio.load in warden and cage
- docs and tests: #266, #346, #355

## cage

- #256 usage pre-flight skips a run; $[ scan reads quotes
- #264 the prompt points a run at the work it may take
- #268 the fence and the prompt close every path repo.yaml tiers HIGH
- #276 a caged run can now reach ship and publish
- #283 the fence's claims now hold
- #286 a caged run inherits the toolchain contract its own toml declares
- #359 ledger is csv, header and quoted notes; readers say what they drop

## skills (pack)

- #305 crew absolute covers all tiers, lens source is a value
- #316 five stale sentences in the review protocol and the schema
- #330 pre-pr-review keeps its steps; its history moves to docs/design
- #333 deliver keeps its steps; its history moves to docs/design
- #334 retro keeps its steps; its history moves to docs/design
- #337 rule-advisor keeps its steps; its history moves to docs/design
- #339 orchestrate keeps its steps; its history moves to docs/design
- #340 autonomous-run keeps its steps; its history moves to docs/design
- docs and tests: #310, #315, #349, #357, #379

## CI

- #296 the gate runs the suite in parallel, and nothing defaults to it
- #302 the merge race, the env gap and the retitle
- #304 the gate takes the verify job's result
- #319 one script reads the README Try it block; the init proof counts it
- #327 a fresh clone clones the tracker from refs/dolt/data, proven on every PR

## repository

- #267 name the writer when this checkout's git index moves
- #275 --work-tree is not a repo binding; the xdist guard reaches its shape
- #281 the git-binding scan matches real git
- #282 go and npm are declared, and an absent one is named
- #297 no git the suite runs forks a background writer into a fixture repo
- #335 the pre-push hook accepts a verify artifact naming the pushed head
- docs and tests: #255, #274, #285, #288, #289, #290, #301, #308, #322, #345, #350, #366, #368, #369, #378

## docs

- #320 The-Cage.md describes the 2026-09-20 run as its ledger row does
- #324 first-round names who numbered the round, not warden alone
- docs and tests: #313
