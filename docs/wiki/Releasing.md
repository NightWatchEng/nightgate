# Releasing

A release is a tag on `main` whose name is
`v` + `warden.__version__`. Every enrolled repository's `repo.yaml`
`platform.pin` names one, and every `warden` command compares that pin
against the running version and fails closed on a mismatch
(`warden/config.py` `enforce_platform_pin`). A builder prepares the release PR;
the founder merges it and pushes the tag. What v3.0.0 carries is in
[release-notes-v3.0.0](../design/release-notes-v3.0.0.md), a breaking release;
v2.3.0's is [release-notes-v2.3.0](../design/release-notes-v2.3.0.md).

## The procedure

1. **Pick the version** `X.Y.Z` and branch from `main`
   (`ops/release-vX.Y.Z`).

2. **Move the version everywhere it is held in lockstep**, in one commit. Each
   row names what refuses the PR while that place is left behind:

   | Place | What moves | Held by |
   |---|---|---|
   | `warden/__init__.py` | `__version__` (`pyproject.toml` reads it: `[tool.hatch.version]`) | the `release tag names the package version` CI job, at tag time |
   | `repo.yaml` | `platform.pin` | `enforce_platform_pin`: every `warden` command in the suite, first `tests/test_declare_check.py::test_this_repo_has_no_declaration_drift` |
   | `examples/hello-svc/repo.yaml` | `platform.pin` | `enforce_platform_pin`; `tests/test_declare_check.py`; the `portability proof` CI job |
   | `docs/wiki/Adopting.md` | the `repo.yaml` snippet, byte-identical to hello-svc's | `tests/test_docs.py::test_adopting_repo_yaml_snippet_is_the_real_file` |
   | `README.md` Install and Try it | the `@vX.Y.Z` install lines, `warden X.Y.Z`, the `ls-remote` check | `tests/test_install_tag_check.py` (the install lines and `warden X.Y.Z` by `test_each_install_page_names_the_current_release_in_its_install_and_version_lines`); `tests/test_init_proof.py`; the `warden init proof` CI job, whose `scripts/init-proof.sh` refuses a Try it install line that is not `@v` + the version of the wheel it built |
   | `docs/wiki/Installation.md` sections 1 and 3 | the install line, `warden X.Y.Z`, the `ls-remote` check, the marketplace `#vX.Y.Z` | `tests/test_install_tag_check.py`; `tests/test_init.py::test_every_doc_adds_the_skill_pack_marketplace_by_its_github_url`; `tests/test_packpin.py::test_the_documented_install_and_init_name_the_pin_and_the_manifest` |
   | Installation.md section 3 and `Skill-Pack.md` | the sentence `warden/packpin.py` `check_step(pin)` returns for hello-svc's pin | `tests/test_packpin.py::test_the_wiki_sentence_is_the_one_for_the_adopted_pin`, `test_no_wiki_page_offers_skills_pin_to_a_pin_that_lacks_it`, `test_the_pages_drift_at_the_pin_on_the_other_side_of_the_command` |

   What does not move: `packpin.LACKS_PIN_COMMAND` and
   `enroll.LACKS_CLASSIFY_COMMAND` name the last release without a command,
   and stay at that release. A sentence about what an older release lacks
   (`v2.2.0's schema refuses the key`) stays true and stays.
   `git grep -nF` for the previous version lists every mention to decide on,
   outside `.warden/memory/` (review records) and
   `scripts/mermaid/package-lock.json` (npm packages that share the number).

3. **Run the gate locally**:
   `uv run --locked --with ruff==0.16.5 ruff check warden cage tests --select F,E9`,
   then `uv run pytest -q -n auto`. A failure here is a place from step 2
   that was left behind; the assertion message names it.

4. **Open the PR** with a `pre-pr-review` round and its attestation. The
   PR body's first lines give the founder the tag command for step 6.

5. **The founder merges** the PR. CI must be green on the merged commit.

6. **The founder tags the merged commit and pushes the tag**:

   ```sh
   git checkout main && git pull --ff-only
   git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z
   ```

7. **CI checks the tag.** A pushed `v*` tag runs the `release tag names the
   package version` job (`.github/workflows/ci.yml`, `release-tag`), which
   runs `scripts/release-tag-check.py <tag>`: it imports `warden` from the
   checkout and exits 0 when the tag is `v` + `__version__`, 1 when they
   differ, 2 when the tag is not `v<version>` or the version cannot be read.
   Nothing stops a mismatched tag from being pushed; a red job means delete
   it (`git push origin :refs/tags/vX.Y.Z`, `git tag -d vX.Y.Z`) and re-cut
   it from a commit that carries the right version.

8. **Check the tag installs**, from outside any checkout:
   `git ls-remote --tags https://github.com/NightWatchEng/agentops vX.Y.Z`
   prints one line, and
   `uv tool install git+https://github.com/NightWatchEng/agentops@vX.Y.Z`
   followed by `warden --version` prints `warden X.Y.Z`.

   From v3.0.0 on, the tag also reaches the public repository,
   NightWatchEng/nightgate, but not at step 6. `publish-public.yml` runs on a
   push to `main` only, and `scripts/publish-sync.py` `mirror_tags` places a
   tag that exists when that run starts, on the public commit whose `Source:`
   trailer names the tagged commit. The step 5 merge runs before the tag
   exists, so the first push to `main` after step 6 mirrors it. Until then
   `git ls-remote --tags https://github.com/NightWatchEng/nightgate vX.Y.Z`
   prints nothing. A tag pushed there directly must sit on that public
   commit: at any other commit the next run fails, naming the tag.

9. **Consumers bump their pin**, each in its own repository (shortfall is
   the live one), once step 8's `NightWatchEng/nightgate` check prints the
   tag: a gate `warden init` writes installs from there. The bump PR moves `platform.pin` in `repo.yaml` to
   `vX.Y.Z`, plus any key the new schema requires or now admits. A bump does
   not rewrite a gate workflow, so a step the generator added since (v2.3.0's
   `proportionate review tier`) is the consumer's to add, and a step it has
   changed since is the consumer's to re-copy at the next bump (that step,
   after v2.3.0, reads the base ref from a quoted env var and has no fork
   branch; it runs the same command, so it can go in any PR at v2.3.0 or
   later). Where an ADDED step goes depends on which pin judges the bump PR.
   The gate `warden init` writes reads `repo.yaml` from the pull request, so
   it runs the new pin and the step can go in the bump PR. A gate that
   restores `repo.yaml` from the base branch,
   as shortfall's hand-written one does, judges the bump PR at the old pin,
   whose warden lacks the step's command (v2.2.0 has no `attest classify`),
   so the step there would turn that PR's own gate red: it waits for a second
   PR, opened once the bump has merged. After the bump PR, re-add the
   skill-pack marketplace at the new tag
   ([Installation](Installation.md) section 3) and run `warden skills pin`.
   `warden memory ingest` may then warn about tags in older review records
   that `.warden/memory/tags.yaml` does not declare; these are warnings, not
   refusals, and the vocabulary is the retro's to settle
   ([Memory](Memory.md#tag-vocabulary)).
