# v3.0.1 release notes

A patch release. What it carries since v3.0.0: 2 commits on `main`, one PR
each, PR #397 and #399. The procedure that cuts the release is
[Releasing](../wiki/Releasing.md).

## For a consumer moving its pin from v3.0.0

Nothing breaks. One thing changes in what `warden init` writes:

- **The emitted gate installs from `NightWatchEng/nightgate`** and reads the
  `NIGHTGATE_DEPLOY_KEY` secret (#399). A repository that stored only
  `AGENTOPS_DEPLOY_KEY` keeps a working gate: with `NIGHTGATE_DEPLOY_KEY`
  empty and the old secret set, the gate installs from
  `NightWatchEng/agentops` and prints a warning naming v4.0.0, the release
  that ends the alias. A bump does not rewrite a gate workflow, so a consumer
  moves to nightgate by re-copying three steps together: `platform
  credential`, `build warden at repo.yaml platform.pin` and `remove platform
  credential`. The last one now deletes the key file the first one writes
  under its new name, so a gate that re-copies the first two without it
  leaves the key on the runner while its own removal check passes. `warden
  init` refuses to overwrite an enrolled repository's files, so copy the
  steps from a gate it writes in a scratch repository
  ([Releasing](../wiki/Releasing.md) step 9). The install pages name
  `NightWatchEng/nightgate`.

## repo

- #399 the gate `warden init` writes installs from `NightWatchEng/nightgate`
  with `NIGHTGATE_DEPLOY_KEY`; `AGENTOPS_DEPLOY_KEY` is an alias, installing
  from `NightWatchEng/agentops`, until v4.0.0
- #397 the public export leaves out the root `.github/dependabot.yml`
