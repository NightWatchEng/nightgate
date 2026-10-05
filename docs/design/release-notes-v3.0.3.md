# v3.0.3 release notes

A patch release. What it carries since v3.0.2: 16 commits on `main`, one PR
each, PR #405 to #420. It is the release that lets the platform be used from
a public repository: the gate `warden init` writes needs no secret and no
private consumer, and it reviews a pull request from a fork. The procedure
that cuts the release is [Releasing](../wiki/Releasing.md).

## For a consumer moving its pin from v3.0.2

Nothing in `repo.yaml` changes. Two things change in the workflow `warden
init` writes:

- **The gate needs no secret and no private repository** (#419). Its
  `install` job clones `https://github.com/NightWatchEng/nightgate` at
  `platform.pin` over https with no credential, and exits 2 naming the pin
  only if that clone fails. The step that refused a public consumer is gone.
  The deploy-key path stays, for a pin of a private build: with
  `NIGHTGATE_DEPLOY_KEY` set, `install` clones over ssh as before. Until
  v4.0.0, `AGENTOPS_DEPLOY_KEY` alone still installs, with a warning
  ([Installation](../wiki/Installation.md), *CI access to the platform*).
- **A pull request from a fork is reviewed** (#420). A fork's read-only token
  cannot post the sticky comment, so `warden review --event` skips the post,
  says why in one line, writes the findings to the job log and the step
  summary, and exits on the verdict: 0 clean, 1 blocking. On a
  same-repository pull request a 403 is still exit 2 on a clean review and 1
  on a blocking one ([Gate Pipeline](../wiki/Gate-Pipeline.md)).

A bump does not rewrite a workflow. To drop the key requirement, re-copy the
whole workflow file from a scratch enrollment as
[Installation](../wiki/Installation.md#upgrading-an-enrolled-repository),
*Upgrading an enrolled repository*, says. A gate you do not re-copy was
written by 3.0.2 or earlier: in a private repository it keeps working while
its secret is set, and it exits 2 at `install` with no secret or in a public
repository, whether or not the secret is set. The skill pack is
0.25.0; re-add the marketplace at `#v3.0.3` (that section, step 1) and run
`warden skills pin`.

## warden

- #419 the emitted gate installs the public platform anonymously; the key
  path stays for a private pin; the require-a-private-repository step goes
- #420 `warden review` on a fork pull request skips the comment with a named
  line and exits on the verdict
- #410 `warden review` writes its findings table to the job step summary
- #411, #416 `warden rules recommend` reads each component's `lang:`, sets
  other languages' entries aside under OTHER LANGUAGES, and fails closed on
  a `lang:` spelling it cannot read
- #409, #415 `warden verify` names a pytest failing test whole, and a count
  line inside a `-vv` message no longer drops later names
- #414 `warden attest write` refuses a tag whose receipt the new shard
  outgrows
- #413 the public mirror's Evidence line carries the suite's count, or none

## skills

- #405 the other six skills resolve warden the way `pre-pr-review` does (the
  launcher, else PATH at the pin); pack 0.25.0

## docs

- #417, #418 the README is reshaped for adopters, with a CI section saying
  what runs on each pull request; #407 quotes the demo's refusal and pass
- #406 the demo's gate at v3.0.2, a dated record
- #408 one upgrade path, in order (*Upgrading an enrolled repository*)
- #412 Writing-Rules names `langs:` and OTHER LANGUAGES
