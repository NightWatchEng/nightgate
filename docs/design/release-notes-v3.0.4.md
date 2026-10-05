# v3.0.4 release notes

A patch release. What it carries since v3.0.3: 7 commits on `main`, one PR
each, PR #423 to #429. It is the release whose `warden init` enrolls a Java
repository. The procedure that cuts the release is
[Releasing](../wiki/Releasing.md).

## For a consumer moving its pin from v3.0.3

Nothing in `repo.yaml` changes. The workflow `warden init` writes for a
Python-only repository is the one v3.0.3 writes; for a node or Go one it
differs in one line, the toolchain pre-flight's hint, which now also names
`actions/setup-java` and `gradle/actions/setup-gradle`. No gate passes or
fails differently for it, so re-copying it is optional. The skill pack is
still 0.25.0; re-add the marketplace at `#v3.0.4`
([Installation](../wiki/Installation.md#upgrading-an-enrolled-repository),
*Upgrading an enrolled repository*, step 1) and run `warden skills pin`.

## warden

- #428 `warden init` enrolls Java: a `pom.xml` runs `mvn -q -B test`; a
  Gradle build or settings file (`build.gradle`, `settings.gradle`, or their
  `.kts` forms) runs `./gradlew test` when the repository has a `gradlew`
  beside it and `gradle test` when it does not. The `.gitignore` entries are
  `target/` for Maven and `build/` and `.gradle/` for Gradle. The gate's
  pre-flight checks only the tool that build runs (`mvn`, `gradle`, or
  `java` for the wrapper), so a Maven repository is never refused for want
  of Gradle
- #426 `warden verify` names failing JUnit tests, from Maven Surefire's
  `<<< FAILURE!` / `<<< ERROR!` lines and Gradle's `<class> > <test> FAILED`
  lines

## platform

- #428 the toolchain table (`[tool.nightgate.toolchain]` in `pyproject.toml`)
  and `.cage/cage.toml` declare `java`, `mvn` and `gradle`; CI installs
  Temurin 17, Maven 3.9.16 and Gradle 9.8.0, each pinned
- #429 the init proof takes a fresh Java repository to Level 3 and runs its
  JUnit test, on every pull request

## docs

- #424 the README states the problem at the top, before the diagram
- #425 the README says any language works with a hand-written `repo.yaml`
- #429 the README and the wiki say `warden init` enrolls Java
- #427 Adopting's hand-written CI snippet installs warden and splits verify
  from review
- #423 a decision record: nightgate and nightgate-demo are public
