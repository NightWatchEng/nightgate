# v3.0.2 release notes

A patch release. What it carries since v3.0.1: 3 commits on `main`, one PR
each, PR #401, #402 and #403. #401 is a dated record of the demo that found
the other two (`docs/design/demo-run-20261005.md`) and changes nothing a
consumer runs. The procedure that cuts the release is
[Releasing](../wiki/Releasing.md).

## For a consumer moving its pin from v3.0.1

Nothing in `repo.yaml` changes. Two things change in what you get:

- **The gate `warden init` writes checks the attestation and reviews a pull
  request whose verify failed** (#402). Once warden is installed, the gate
  job runs `warden review`, the proportionate review tier, `warden attest
  check` and `warden certify`, whatever verify said, and only then fails
  if verify did not succeed. The attestation check is fail-closed: a pull request from
  the same repository with no committed pre-PR review attestation is red, a
  Dependabot one included; a fork's is exempt. A bump does not rewrite a gate
  workflow, so a gate written at v3.0.1 or earlier keeps the old job until
  you re-copy the `gate` job, and the `warden verify --scope` steps, from a
  gate `warden init` writes in a scratch repository enrolled at the same
  directory below the git root and for the same languages: below the root
  the job's name, `warden gate (<dir>)`, is the check your branch rule
  requires, and its working directory and upload path carry the directory
  ([Releasing](../wiki/Releasing.md) step 9, [Installation](../wiki/Installation.md)
  section 1). Once you do, a same-repository pull request is red until it
  carries a committed attestation.
- **`pre-pr-review` runs on an enrolled consumer** (#403). It resolves warden
  first: this repository's `.warden/bin/warden` launcher when there is one,
  else `warden` on PATH when its `--version` is `platform.pin`, and refuses
  with the reason otherwise. The skill pack is 0.24.0; re-add the marketplace
  at `#v3.0.2` ([Installation](../wiki/Installation.md) section 3) and run
  `warden skills pin`.

## warden

- #402 the emitted gate runs review, the proportionate tier, `warden attest
  check` (fail-closed, forks exempt) and certify whenever warden is
  installed, and then fails if verify did not succeed; `warden verify`'s summary names the
  failing tests (`failing tests (N):`), and the emitted verify steps copy it
  into the job log and the step summary

## skills

- #403 `pre-pr-review` resolves warden (the launcher, else PATH at the pin),
  so it runs on a consumer `warden init` enrolled; pack 0.24.0. The other six
  skills that run warden still call the launcher

## docs

- #401 the demo's gate refusing one pull request and passing another, a
  dated record
