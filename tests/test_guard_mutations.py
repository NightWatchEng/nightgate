"""The harness that makes "a guard that passes because it cannot fail"
mechanical.

WHY IT EXISTS. One defect wears many costumes: a regex alternative no control
exercises, a plant-tuple entry deletable with the suite green, an error arm no
test tells from success, a blocklist missing a spelling. Left to a human or a
reviewer happening to notice, the class keeps recurring, so this file proves
mechanically that a guard COULD fail.

WHAT IT DOES. For every guard this file declares SWEPT, it enumerates each
ALTERNATIVE out of the live object — each branch of a regex alternation at
every nesting level, each member of a character class, each entry of a
vocabulary or page set — neuters it in process, and runs the tests of the
declared witness modules until one goes RED. An alternative no named test
reddens is a FINDING, reported by guard, by alternative, and with the message
an author needs.

WHAT IT COVERS AND WHAT IT DOES NOT. This is the part a harness gets wrong
most expensively, so it is mechanical rather than promised:
`test_every_module_level_vocabulary_in_the_suite_is_classified` requires
EVERY module-level vocabulary the suite declares to carry a disposition in
`REGISTRY` below. A new one — in any test module, swept or not — turns this
red until someone writes down which it is and why. So the list of what is
outside the sweep is in the tree, it is reviewable, and it cannot grow in
silence. The dispositions:

  SWEPT    a guard; every alternative must take a named test red.

           WHAT THAT PROVES, exactly: no alternative can be DELETED with the
           suite green. It is NOT the stronger claim that each alternative
           demonstrably changes a scan outcome: a witness may be a
           meta-assertion ABOUT the vocabulary — a `set(X) == {...}` or
           `len(X) == n` typed beside it — rather than a behavioural control,
           and `run_test` cannot tell the two apart, because any exception is
           the verdict. A restatement typed far from the tuple can be the sole
           witness and make every member read load-bearing. Read SWEPT as "not
           deletable in silence", never as "every alternative is known to
           bite".
  CONTROL  a control or plant table. Deleting a row does not change a guard —
           it removes the WITNESS for one of that guard's alternatives, which
           the sweep on that guard then reports. Pinned one level out, by
           exactly one of three mechanisms, and each is executed rather than
           asserted: through the guard it serves (`_CONTROL_SERVES`, run by
           `test_a_control_row_is_pinned_through_the_guard_it_serves`),
           by its own completeness test (`_SELF_PINNED_CONTROLS`, run by the
           same test), or by review alone (`_REVIEW_PINNED_CONTROLS` — the
           NEGATIVE controls, which no guard deletion can reveal, each with
           its measurement). `test_every_control_table_is_in_exactly_
           one_bucket` holds the three to a partition.
  DERIVED  a vocabulary a SWEPT pattern is compiled from. Mutating the tuple
           cannot change the already-built pattern, so the pattern is what is
           swept and every spelling the tuple produces is covered through it.
  FIXTURE  input data or expected values — not a pattern any guard consults.
           Deleting an entry shrinks what one test feeds itself, which is a
           different question from whether a guard can fail.
  UNSWEPT  a real guard that this harness does not sweep, with the reason.
           These are the honest gap, and they are listed one by one.

THE HARNESS'S OWN TWO FILES have a SECOND ledger rather than a second
exemption. `guard_mutation.SELF` keeps them out of `REGISTRY` — classifying the
registry inside itself is circular — so the harness's own module-level
vocabularies (`_CONTAINER_CALLS`, `_GROUP_PREFIXES` and the rest) need another
ledger for anything to be able to fail on them. `SELF_REGISTRY` below is total
over exactly those two files, every PINNED row naming a test that is RUN with a
member deleted, and every UNPINNED_SELF row saying why nothing can. Its own
bound is stated where it lives: it uses the discovery machinery it audits.

THE STALE-BYTECODE TRAP, handled by construction rather than by discipline.
Two mutations that remove the same number of characters can be restored
inside one timestamp second and served from a `.pyc` compiled from the
previous one, so a sweep reports an alternative load-bearing that is not.
Every mutation here is `setattr` on the module object; no source file is ever
rewritten, so there is no source for a `.pyc` to be stale against.
`test_the_sweep_writes_no_source_and_no_bytecode` executes that claim.
Separately, `tests/conftest.py` refuses pytest's rewrite cache outright, and
that is what protects a harness that DOES edit files.
"""

from __future__ import annotations

import ast
import re
import sys
import tempfile
import types
from pathlib import Path

import pytest

import guard_mutation as gm
from private_evidence import EXPORT

SWEPT, CONTROL, DERIVED, FIXTURE, UNSWEPT = (
    "SWEPT", "CONTROL", "DERIVED", "FIXTURE", "UNSWEPT")

# Every module-level vocabulary the suite declares, with its disposition and
# the reason. TOTAL by assertion, not by intention: discovery is an AST read of
# `tests/*.py` plus a runtime look at the value, and the test below requires
# this dict and that set to be equal in both directions.
REGISTRY: dict[str, tuple[str, str]] = {
    # ── test_ship / test_skillpack: the roster refusal's fixture graph, and
    # the protocol set the declared-crew guards read.
    "test_proportion:CREW": (
        FIXTURE, "the resolved crew the light-roster tests pass to "
        "verify_crew — input data, not a pattern any guard consults"),
    "test_proportion:LIGHT_ROSTER": (
        FIXTURE, "the one-entry roster those same tests hand it, so the "
        "refusals can be driven without building a review dir"),
    "test_declare_check:_INDIRECT_SHADOWS_UNSEEN": (
        FIXTURE, "one workflow step per shape that takes `shopt` away from "
        "the builtin without the scan seeing it — input steps driven through "
        "`_shell_text`, not a pattern any guard consults. The residual they "
        "record is counted by "
        "test_the_residual_is_counted_not_only_listed beside them"),
    "test_attest_crew:_GRAPH_SHAPES": (
        FIXTURE, "one graph.yaml per way a document can fail to answer what "
        "this repo's crew is, each paired with what the two seams are RULED to "
        "do with it — input data driven through `ship._declared_crew` and the "
        "`attest write` command, not a pattern any guard consults. The two "
        "seams live in warden/, outside this harness's subject, so a row "
        "deleted with the behaviour it pins cannot be caught here; "
        "test_the_fixture_set_covers_the_shapes_the_ruling_names beside "
        "it is the count that can, and it also holds the divergence classes "
        "to the one the ruling admits"),
    "test_ship:_CREW_GRAPH": (
        FIXTURE, "the graph document the roster tests write into a fixture "
        "repo so it declares a review crew — input data, not a pattern any "
        "guard consults"),
    "test_skillpack:_REVIEW_PROTOCOLS": (
        SWEPT, "the two protocols that dispatch a review and the policy they "
        "read it from; both declared-crew guards iterate it, and "
        "test_the_review_protocol_set_is_the_three_the_crew_rule_binds is the "
        "meta-assertion that makes a dropped member red"),
    "test_skillpack:_LENS_SOURCE_SPELLINGS": (
        SWEPT, "the spellings a prose RESTATEMENT of pre-pr-review's no-crew "
        "lens source would need; the fallback guard bans them so the source "
        "is stated once, in the value it reads, and each is pinned by a "
        "restatement only it catches"),
    "test_skillpack:_LENS_SOURCE_RESTATEMENTS": (
        CONTROL, "one restatement per spelling above, each caught by exactly "
        "one — written out rather than derived from the vocabulary, which "
        "would shrink with it and pin nothing"),
    # ── conftest: the shared fixture module. Its guards are consulted by tests
    # in OTHER modules, which is what `WITNESSES` below is for.
    "conftest:CONSUMER_REPO_NAMES": (
        DERIVED, "`test_config:_CANARY_NAMES` is compiled from it at import, "
        "so the pattern is swept; its other reader, the skill-prose scan, was "
        "retired with the tests that pinned skill wording"),
    "conftest:FILES": (
        FIXTURE, "the synthetic repo's file tree — what the sample repo "
        "contains, not a pattern any guard consults"),
    "conftest:RULES": (
        FIXTURE, "the synthetic repo's ruleset, one rule per CHECKERS id"),
    "conftest:_AMBIENT_GIT_CONFIG": (
        UNSWEPT, "a real guard — test_ambient_git.py names the three "
        "config-layer keys individually in its own checker — but the mutation "
        "is an ENVIRONMENT change the sweep cannot make in process: the value "
        "is spread into closed env dicts at subprocess launch. Covered by "
        "test_ambient_git.py::test_both_ambient_config_layers_are_"
        "neutralised_for_the_session, which reads os.environ directly, for the "
        "three layer keys; the rest of the set is `_NO_BACKGROUND_GIT` below "
        "and is covered there"),
    "conftest:_NO_BACKGROUND_GIT": (
        UNSWEPT, "the environment layer that stops git forking detached "
        "maintenance into a fixture repo. Same reason the entry above is "
        "unswept — it is spread into closed env dicts at subprocess launch, so "
        "an in-process deletion changes nothing about the git that runs. "
        "Covered by tests/test_git_background_maintenance.py, which reads "
        "git's own trace2 event stream and asserts a fixture repo's commit "
        "forked no maintenance child, with a control leg proving the git under "
        "test would have"),
    "conftest:_UNBOUND_GIT_ENV": (
        UNSWEPT, "not a vocabulary — it is EMPTY at import and holds whatever "
        "repo-binding git variables `pytest_configure` took off the caller, "
        "so an import-time mutation is overwritten before collection and the "
        "sweep has nothing to delete. Covered by "
        "tests/test_index_isolation.py::test_a_module_imported_under_an_"
        "exported_git_dir_binds_to_no_repo, which drives a nested session "
        "under an exported GIT_DIR and watches a decoy repo stay untouched"),
    "conftest:_INDEX_WATCH": (
        UNSWEPT, "not a vocabulary — it is EMPTY at import, and every entry "
        "in it is written by `watch_index()` at pytest_sessionstart, so an "
        "import-time mutation is overwritten before the first test runs and "
        "the sweep has nothing it can delete. Covered by "
        "tests/test_index_isolation.py, which drives the index-write guard "
        "it holds state for end to end in nested pytest sessions"),

    # ── the git-binding guards that keep a test off the real checkout.
    "test_git_repo_binding:_REPO_BINDING_GLOBALS": (
        SWEPT, "the leading global options that name the repository a git "
        "call resolves to; each pinned by a MUST_NOT_FIRE row, so dropping "
        "one reports a call that DOES name its repo as repo-less"),
    "test_git_repo_binding:_VALUE_TAKING_NON_BINDINGS": (
        SWEPT, "the non-binding global options whose value is the NEXT argv "
        "element; without them the option reader takes an option's ARGUMENT "
        "for the verb and stops — a false positive on "
        "`git --work-tree <p> --git-dir <d>`. "
        "Its rows are SYNTHETIC and say so: membership is earned by a probe "
        "against real git, not by the row"),
    "test_git_repo_binding:REPO_FREE_VERBS": (
        SWEPT, "the verbs a git call may omit `cwd=`/`-C` for, each mapped to "
        "the argument that makes it repo-free; every verb has a real caller "
        "in this tree, so deleting one reddens the scan on that call site"),
    "test_git_repo_binding:MUST_FIRE": (
        CONTROL, "positive controls: synthetic calls the scan must flag, one "
        "per shape review round 1 measured silent"),
    "test_git_repo_binding:MUST_NOT_FIRE": (
        CONTROL, "negative controls: calls that DO name their repository and "
        "must not be flagged, including the two false positives round 1 "
        "measured"),

    # ── test_docs: the home-path leak guards.
    "test_docs:_ADOPTING_ADAPTATIONS": (
        SWEPT, "the two places Adopting.md's hand-copied pre-flight step "
        "departs from `render_toolchain_step`, as the substitutions the "
        "byte-equality applies. Each pair is asserted present on BOTH sides "
        "before it is applied, so deleting either stops the equality holding"),
    "test_docs:_WARDEN_LINE": (
        UNSWEPT, "a `run:` line in Adopting.md's gate snippet that invokes "
        "warden, which the install-before-use and job-split guards read. One "
        "pattern with no alternation, so the sweep has nothing to delete. "
        "Covered by those guards themselves, measured: against the "
        "pre-hy6o.51 page each of them fails"),
    "test_docs:HOME_PLACEHOLDERS": (
        SWEPT, "names that are placeholders rather than people"),
    "test_docs:HOME_RE": (
        SWEPT, "the maintainer-home-path detector, both roots and the login"),
    "test_docs:HOME_PATH_EXEMPT": (
        SWEPT, "the committed shards quoting a real home path, each exempted "
        "BY PATH — deleting them must make the tree scan fire"),

    # ── test_init: warden init's fixture repos and the workflow step runner.
    "test_init:SHELLS": (
        FIXTURE, "the argv GitHub Actions runs a Linux `run:` step under, per "
        "declared `shell:` (none is `bash -e`), used to execute the generated "
        "workflow's steps — input, not a pattern"),
    "test_init:FIXTURES": (
        FIXTURE, "the manifest and source file of each fixture repo init "
        "enrolls — input data no guard consults"),
    "test_init:FAILING_OUTPUTS": (
        FIXTURE, "one failing run's output per runner and the test names "
        "`verify.failing_tests` must read off it — input and expected data, "
        "one entry per `_FAILING` pattern"),
    "test_init_proof:VISIBILITY": (
        SWEPT, "the stems of words that say who may read a repository, which "
        "the README must not use in its own voice"),
    "test_init_proof:SPELLINGS": (
        FIXTURE, "the platform repository URLs the isolation script lists, each "
        "given a planted local route before git's refusal is checked — input"),
    "test_publish_public:FILES": (
        FIXTURE, "the fixture repository the public export reads — input data"),

    # ── toolchain / test_toolchain: what this repo's own suite shells out to,
    # declared in pyproject.toml and held to what the enrollment surface runs,
    # what CI installs, and what CONTRIBUTING.md tells a newcomer.
    "test_toolchain:_INSTALLER": (
        SWEPT, "the pinned action that supplies each declared binary; "
        "test_ci_installs_every_declared_toolchain looks each one up by "
        "name, and test_every_declared_tool_has_a_way_to_be_installed_in_ci "
        "is the meta-assertion that makes a dropped member red rather than a "
        "CI check that quietly iterates nothing"),
    "test_toolchain:_VERIFY_CARVE_OUT": (
        SWEPT, "the functions allowed to invoke `warden verify` without "
        "routing through `_verify`, each with the reason its scope needs no "
        "declared toolchain; test_every_verify_carve_out_names_a_real_"
        "function reddens on a stale entry and the routing guard reddens on a "
        "dropped one, so a row cannot leave in either direction in silence"),
    "toolchain:_INSTALL_HINT": (
        SWEPT, "how to install each declared binary, carried in the skip/fail "
        "reason so the gap report says how to close it; "
        "test_every_declared_tool_says_how_to_install_it names each "
        "member, so a dropped one is red instead of silently degrading to the "
        "'install it' fallback"),

    # ── test_install_tag_check: the install pages' release-tag check, executed.
    "test_install_tag_check:PAGES": (
        FIXTURE, "the two install pages whose tag check is run — input, not a "
        "pattern any guard consults"),
    "test_install_tag_check:REMOTES": (
        FIXTURE, "the platform's https and ssh URLs, swapped for a local "
        "repository before a documented check is run — input"),
    "test_install_tag_check:ACCESS_FAILURES": (
        FIXTURE, "the error text git prints for each missing-access case, as "
        "expected values each page must name"),
    "test_install_tag_check:LS_REMOTE": (
        UNSWEPT, "the reader that finds each documented git ls-remote. Covered "
        "by test_each_install_page_documents_a_tag_check_for_the_current_version, "
        "which fails when the reader finds no check on a page"),
    "test_install_tag_check:INSTALL": (
        UNSWEPT, "the reader that finds each documented uv tool install line. "
        "Covered by test_each_install_page_names_the_current_release_in_its_"
        "install_and_version_lines, which fails when it finds none on a page"),
    "test_install_tag_check:VERSION_LINE": (
        UNSWEPT, "the reader that finds each `warden X.Y.Z` a page prints. "
        "Covered by the same test, which fails when it finds none on a page"),

    # ── test_review_round_isolation: the carry-forward mandate detector and
    # the retired schema claim.
    "test_review_round_isolation:_ANOTHER_ROUND": (
        SWEPT, "the round-qualifier half of the carry-forward mandate guard"),
    "test_review_round_isolation:_CLAUSE_BREAKS": (
        SWEPT, "the schema clause split"),
    "test_review_round_isolation:_NEGATOR": (
        SWEPT, "the negator vocabulary, as the pattern the guard consults"),
    "test_review_round_isolation:_ONE_DIR_HOLDS_EVERY_ROUND": (
        SWEPT, "the retired schema claim, as the pattern the guard consults"),
    "test_review_round_isolation:_TRANSFER": (
        SWEPT, "the transfer verbs, every inflection"),
    "test_review_round_isolation:_TRANSFERRED": (
        SWEPT, "what a mandate says is transferred between rounds"),
    "test_review_round_isolation:_NEGATORS": (
        DERIVED, "`_NEGATOR` is compiled from it, so the pattern is swept"),
    "test_review_round_isolation:_HOLD_VERBS": (
        DERIVED, "`_ONE_DIR_HOLDS_EVERY_ROUND` is compiled from it"),
    "test_review_round_isolation:_HOLD_QUANTIFIERS": (
        DERIVED, "`_ONE_DIR_HOLDS_EVERY_ROUND` is compiled from it"),
    "test_review_round_isolation:_ANOTHER_ROUND_CONTROLS": (
        CONTROL, "one control per alternative of `_ANOTHER_ROUND`"),
    "test_review_round_isolation:_TRANSFER_CONTROLS": (
        CONTROL, "one control per alternative of `_TRANSFER`"),

    # ── test_round_isolation: the child-program guards.
    "test_round_isolation:_CLOCK_MODULES": (
        SWEPT, "modules whose exports are clock readers"),
    "test_round_isolation:_CLOCK_READERS": (
        SWEPT, "every clock-reader name a child program may not spell"),
    "test_round_isolation:_CODE_FROM_DATA": (
        SWEPT, "the builtins that turn data into code"),
    "test_round_isolation:_MODULE_DUNDERS": (
        SWEPT, "module strings that are not child programs"),
    "test_round_isolation:_SUBPROCESS_SITES": (
        SWEPT, "the launch sites the coverage inversion reads"),
    "test_round_isolation:_TIME_MODULE_ONLY": (
        SWEPT, "readers that only read off the time module"),
    "test_round_isolation:_ADD_METHODS": (
        SWEPT, "the list-mutating methods the arm-coverage AST read consults "
        "— an arm spelled in one it does not read is an arm reported covered "
        "while no plant reaches it"),
    "test_round_isolation:_NOT_PROGRAMS": (
        SWEPT, "module strings that are prose or config rather than child "
        "programs — every other one must parse and be scanned"),
    "test_round_isolation:_CAUGHT_PROGRAMS": (
        CONTROL, "one plant per declared clock reader and per receiver shape"),
    "test_round_isolation:_SILENT_PROGRAMS": (
        CONTROL, "the NEGATIVE plants — frozen conversions that must not fire. "
        "Not pinned by any guard deletion: a guard that loses a branch fires "
        "less, never more"),
    "test_round_isolation:_CODE_FROM_DATA_PLANTS": (
        CONTROL, "one plant per (name, shape) pair"),
    "test_round_isolation:_CODE_FROM_DATA_SHAPES": (
        DERIVED, "the shapes the arms report; the ARMS themselves are pinned "
        "by test_every_arm_of_the_code_from_data_scan_is_exercised_by_a_plant, which traces which `hits.append` sites ran"),

    # ── test_self_cage: the self-cage source, held against its own fence.
    "test_self_cage:_PATH_EDGE": (
        UNSWEPT, "what may sit either side of a path name for it to be that "
        "path and not a longer one. A real guard — both `withholds` and the "
        "polarity scan build their pattern from it at call time, so a "
        "deleted member really does widen them — and this harness cannot "
        "sweep it: it is a regex SOURCE fragment, a str, and "
        "`gm.alternatives` refuses one (PatternUnreadable) rather than "
        "guessing where a class begins in text that is not yet a pattern. "
        "Covered by test_a_path_name_is_that_path_and_not_a_longer_one, "
        "one case per class member, measured 7 of 7 members deletable-red "
        "(round 2 measured the first version of that test at 2 of 7, with "
        "the row already claiming all of them)"),
    "test_self_cage:PROHIBITION": (
        SWEPT, "the refusal set the cage prompt's polarity half reads — the "
        "spellings in which a sentence can WITHDRAW a directory the offer "
        "sentence hands over. Every alternative is witnessed "
        "behaviourally by test_a_planted_prohibition_reddens_the_"
        "polarity_half, one parametrisation per spelling, each appended to "
        "the real prompt and required to be seen"),
    "test_self_cage:PROHIBITION_PLANTS": (
        CONTROL, "one flipped sentence per alternative in PROHIBITION — "
        "POSITIVE controls, so a deleted row leaves a spelling with no "
        "witness and the sweep above reports it. Completeness pinned by "
        "test_every_prohibition_spelling_is_planted, both directions, "
        "at gm.alternatives' granularity: it reads the 9 alternatives this "
        "engine deletes, not the 7 a top-level `|` split yields, because "
        "`off[- ]limits` is one branch and two class members and the table "
        "carries a plant for each (its first spelling asked the split, and "
        "the `off limits` row was then deletable with the witness green)"),
    "test_self_cage:HANDED_OVER": (
        CONTROL, "one probe per directory the cage prompt hands an unattended "
        "session — NEGATIVE controls the fence regex must stay silent on. Its "
        "KEYS are no longer a list at all: they are held equal to the names "
        "`offered_directories` reads out of the prompt's offer sentence, so a "
        "deleted row reddens that equality, which is the measurement that "
        "keeps it out of _REVIEW_PINNED_CONTROLS"),

    # ── guards in other modules. The witness search for a guard elsewhere
    # can cost minutes rather than seconds (one 5-alternative sweep in
    # test_adopted_rules.py took 91s, because an unpinned alternative runs
    # every test in the module). A full pass of test_config's runnable tests
    # is 1.4s, test_ambient_git's 0.4s, test_unknown_key_render's 0.0s, so an
    # unpinned alternative in any of them costs a second, not a minute. What
    # stays UNSWEPT says why.
    "test_ambient_git:_SHELL_BRANCH_PIN": (
        SWEPT, "the shell branch-pin detector. Six of its seven alternatives "
        "were deletable with the module green when the sweep first reached "
        "it — the planted pinned set spelled only `-b` in shell text; "
        "test_every_branch_pin_spelling_is_recognised drives one shell "
        "string per spelling"),
    "test_ambient_git:_SHELL_REPO_START": (
        SWEPT, "the shell repo-start detector; both subcommands pinned by the "
        "planted shell case and the parametrized rejection lines"),
    "test_ambient_git:_BRANCH_PINS": (
        SWEPT, "branch-pin argv tokens; `--initial-branch` as its OWN token "
        "was deletable until the same control planted it"),
    "test_ambient_git:_ENV_WRITERS": (
        SWEPT, "the env-dict writers the closed-env guard reads; one "
        "spelling per writer inside test_both_ambient_config_layers_"
        "are_neutralised_for_the_session, which pins all six. This row "
        "was UNSWEPT through two cuts on two false premises, both from "
        "measuring in the wrong place: a bare interpreter, where that "
        "witness is red because conftest's session-scoped `clean_git_env` "
        "never ran. Inside a pytest session — where this sweep actually "
        "runs — it is green at baseline and 6 of 6 members are witnessed"),
    "test_ambient_git:_GIT_CALLEES": (
        SWEPT, "the generic subprocess drivers that make `run(\"init\", "
        "...)` read as git; one planted call per member in "
        "`_GENERIC_DRIVER_PLANTS`, driven by "
        "test_every_generic_subprocess_driver_reads_as_git. Was UNSWEPT "
        "at 9 of 9 members deletable, because every planted case in the "
        "branch-pin guard carries the `git` literal or a `_git` callee and is caught by "
        "an earlier disjunct. TWO of those nine were subsumed rather than "
        "uncontrolled — `git` and `_git` can never be the sole trigger, since "
        "`\"git\" in callee.lower()` is tested first — so they are deleted, "
        "measured: the whole-of-tests scan returns the identical result "
        "without them"),
    "test_ambient_git:_GENERIC_DRIVER_PLANTS": (
        CONTROL, "one planted call per member of `_GIT_CALLEES`, written out "
        "rather than looped over the tuple — a control generated FROM the "
        "vocabulary reddens on a deletion only because the loop got shorter"),
    "test_ambient_git:_REPO_STARTERS": (
        SWEPT, "repo-start argv tokens; both pinned by the planted set"),
    "test_ambient_git:_GUARD_EXEMPTIONS": (
        SWEPT, "call sites the ambient-git guard exempts. 2 of its 4 keys were "
        "deletable with the module green — their planted material was a plain "
        "string, which the detector reads only as a call argument — so both "
        "are gone, and each key left reddens test_every_exemption_excuses_a_"
        "line_the_guard_would_otherwise_catch when deleted alone"),
    "test_ambient_git:_DEMONSTRATES_THE_BUILT_IN_DEFAULT": (
        SWEPT, "the one (module, function) whose bare init IS the "
        "demonstration; its sole member is read by the exemption-bound test"),
    "test_cage_runner:ALWAYS_QUIET": (
        UNSWEPT, "a quiet-hours window spanning the day; outside the subject"),
    "test_cage_runner:NEVER_QUIET": (
        UNSWEPT, "an empty quiet-hours window; outside the subject"),
    "test_cage_runner:NINE_TO_FIVE_QUIET": (
        UNSWEPT, "an ordinary quiet-hours window; outside the subject"),
    "test_cage_runner:BOUNDARY_HOURS": (
        UNSWEPT, "the hours at a quiet window's edges; outside the subject"),
    "test_config:_ALLOWED_READERS": (
        SWEPT, "the one (module, function) allowed to read repo.yaml by "
        "name; its member was deletable with the module green until the "
        "test_nothing_in_the_package_reads_repo_yaml_by_name planted the "
        "allowed reader"),
    "test_skillpack:OPTIONAL_POLICY_SECTIONS": (
        UNSWEPT, "policy sections a consumer may omit; outside the subject"),
    "test_catalog_starters:ACCEPTED_FALSE_POSITIVES": (
        UNSWEPT, "starter matches accepted as false positives; outside the "
        "subject"),
    "test_catalog_starters:_COST_MUST_MENTION": (
        UNSWEPT, "what a starter's cost note must name; outside the subject"),
    "test_mechanical:RATCHET_PARAMS": (
        UNSWEPT, "baseline-ratchet rule params; outside the subject"),
    "test_lifecycle:FIRES_ALWAYS": (
        UNSWEPT, "the rule that fires on every diff; outside the subject"),
    "test_lifecycle:EXCEPT_PASS": (
        UNSWEPT, "the swallowed-exception body under test; outside the "
        "subject"),
    "test_unknown_key_render:BAD_KEYS": (
        SWEPT, "the heterogeneous unknown-key set each loader must render "
        "without crashing; both keys pinned by the parametrized refusal test"),
    "test_build_wiki:LINK_RE": (
        UNSWEPT, "the wiki link extractor; outside the subject. NO COVERAGE, "
        "and the cost of ending that is now Measured rather than estimated. "
        "First reading: 4 of 4 alternatives unpinned, because its only "
        "consumer takes `built`, a module-scoped fixture on "
        "`tmp_path_factory`, which the shim cannot build — as of this note 11 "
        "of the module's 44 test functions are BLOCKED for that reason, and "
        "each `built` is a full bash run of the wiki builder. Second reading, "
        "with `_resolve` taught `tmp_path_factory` and that consumer given as "
        "a pin: the sweep spends about 110s on this one regex, because the "
        "shim has no fixture scope and rebuilds the wiki once per test per "
        "alternative where pytest builds it once for the module — and 3 of "
        "the 4 alternatives are STILL unpinned, each needing a control that "
        "pays another build to witness. The comparison that matters is the "
        "test it would join: `test_every_alternative_of_every_swept_guard"
        "_is_load_bearing` runs in about 17s today, so one regex would make "
        "it roughly eight times the whole rest of the sweep and the longest "
        "test in the suite by a wide margin. (Wall clocks are machine-"
        "relative; the RATIO is the reading, and a second machine reproduced "
        "both the 3-of-4 and the order of magnitude.) So the teaching was "
        "measured and reverted rather than landed. Ending this gap means "
        "giving the shim fixture SCOPE, which is a harness capability and "
        "wants its own review"),
    "test_cage_runner:FORBIDDEN": (
        UNSWEPT, "paths the caged runner may not write; outside the subject"),
    "test_cage_runner:RUNNER_SYSTEM_DIRS": (
        UNSWEPT, "system dirs the runner needs; outside the subject"),
    "test_catalog:SHIPPED_CWE": (
        UNSWEPT, "the CWE ids the catalog ships; outside the subject"),
    "test_catalog:SHIPPED_LENSES": (
        UNSWEPT, "the lenses the catalog ships; outside the subject"),
    "test_commit_lint:REQUIRED_SHELLS": (
        UNSWEPT, "shells the commit linter must run under; outside the "
        "subject"),
    "test_commit_lint:OPTIONAL_SHELLS": (
        UNSWEPT, "shells it runs under when present; outside the subject"),
    "test_commit_lint:MISSING_SHELLS": (
        FIXTURE, "the shells NOT on this machine — an environment MEASUREMENT "
        "the module reads to emit a counted SKIP instead of letting tests "
        "quietly stop existing, not a pattern any guard consults. Its size "
        "differs per box, which is what made it the case that exposed "
        "discovery's own environment-dependence: it is `()` where every shell "
        "resolves and non-empty where one does not, so it was invisible on the "
        "builder's laptop and named by all five CI jobs. Same disposition as "
        "`test_malformation_matrix:_STORE_AT_IMPORT`, and for the same reason"),
    "test_commit_lint:ALL_SHELLS": (
        DERIVED, "REQUIRED_SHELLS + OPTIONAL_SHELLS"),
    "test_commit_lint:SHELLS": (
        DERIVED, "the parametrize entries for ALL_SHELLS, one per declared "
        "shell — NOT 'the shells actually present', which is what this row "
        "said until round 7 read the code: `_shell_param` wraps an absent "
        "shell in a SKIP marker rather than dropping it, which is the whole "
        "point of the required/optional split above it. A wrong reason here is "
        "the ledger misdescribing its own subject"),
    "test_commit_lint:REAL_EXEMPT_HEADERS": (
        UNSWEPT, "commit headers the validator exempts; outside the subject"),
    "test_config:_CANARY_NAMES": (
        SWEPT, "consumer names warden/*.py may not spell; one FIRES case per "
        "declared name, composed from the same tuple"),
    "test_config:_CANARY_PATHS": (
        SWEPT, "consumer paths warden/*.py may not spell; the `'` member of "
        "its quote class was deletable until a single-quoted case was "
        "planted"),
    "test_config:_READ_CALLS": (
        SWEPT, "the read calls the repo.yaml-reader guard scans for; the "
        "`open` member is read only by the attribute arm and was deletable "
        "until a `.open()` case was planted"),
    "test_schema_description_drift:_FIRST_ARRIVED": (
        UNSWEPT, "the retired-reading matcher over the attestation schema's "
        "`round` description; outside the subject — it arrived with its own "
        "FIRES/SILENT pair, and the harness caught it the moment it landed, "
        "which is the check working"),
    "test_skillpack:_PLATFORM_CAP": (
        UNSWEPT, "reads the repair cap the deliver skill declares — one "
        "capture group, no deletable alternative. Covered by "
        "test_skillpack.py's assertions that the number it finds equals "
        "certify's PLATFORM_REPAIR_CAP and bounds every shipped repo.yaml's "
        "repair.budget"),
    "test_skillpack:REQUIRED_POLICY_SECTIONS": (
        UNSWEPT, "sections every policy must carry; outside the subject"),
    "test_skillpack:PACK_SKILLS": (
        UNSWEPT, "the skills the pack ships; outside the subject"),
    "test_skillpack:_SCOPES": (
        UNSWEPT, "the AST node types the delegate scan reads as a scope of "
        "their own (def, lambda, class, the four comprehensions); outside "
        "the subject — a type tuple the scan branches on, not a pattern any "
        "guard consults. Covered by test_scopes_are_read_as_python_"
        "scopes_them for the class and comprehension members; the def and "
        "lambda members are not individually pinned"),
    "test_skillpack:_INDENTED_DELEGATES": (
        UNSWEPT, "the one renderer `render_stats` INDENTS under TAGS, "
        "subtracted by name from the section-header scan so its literals are "
        "not read as sections. NO COVERAGE, and this row once claimed some: "
        "it said dropping the subtraction reddened the "
        "scanned/emitted equality in `_stats_section_headers`. Measured — "
        "`_INDENTED_DELEGATES = set()` leaves tests/test_skillpack.py at 43 "
        "passed. `tags.render_audit`'s source carries no header-shaped "
        "literal at all; the only one in that file is in "
        "`tags._render_ceiling`, which the scan never reaches because "
        "`_stats_delegates` reads `render_stats`'s own source and not a "
        "delegate's delegates. So the subtraction is defence against a "
        "literal `render_audit` does not yet carry, and an UNSWEPT row "
        "admitting none is the contract of this list"),
    "test_skillpack:_AUDIT_LABELS_NOT_ENUMERATED": (
        UNSWEPT, "audit labels deliberately not enumerated; outside the "
        "subject"),
    "test_skillpack:_SUBST_RE": (
        UNSWEPT, "the `$( ... )` matcher that blanks an embedded foreign argv "
        "before the skill-flag guard reads a warden command's flags — one pattern "
        "with no deletable alternative. Covered by the guard that reads it: "
        "delete the blanking and `warden ship --json title` stops reddening"),
    "test_skillpack:_FLAG_RE": (
        UNSWEPT, "the `--flag` tokenizer the skill-flag guard scans skill prose "
        "with — one character-class pattern with no deletable alternative in "
        "it. Covered by the guard that reads it, whose per-section floors go "
        "red the moment the tokenizer stops finding flags"),
    "test_skillpack:_WIRED_COMMANDS": (
        SWEPT, "the warden commands the wired ship-tail sections invoke, "
        "each with its subparser path; dropping one sends that command's "
        "flags to the looser prose check or empties the section's span list, "
        "so every entry is deleted in turn and the skill-flag guard must "
        "go red"),
    "test_skillpack:_FOREIGN_FLAGS": (
        SWEPT, "the non-warden flags the wired ship-tail sections are allowed "
        "to name; dropping one makes a real flag read as a typo, so every "
        "entry is deleted in turn and the skill-flag guard must go red"),
    "test_skillpack:_WIRED_SECTIONS": (
        SWEPT, "which skill sections the skill-flag guard reads; dropping one "
        "would leave half the wiring uninspected with the suite green, which "
        "is the silent-skip defect one level up"),
    "test_skillpack:SKILL_FILES": (
        FIXTURE, "every SKILL.md in the pack, globbed from the tree at import "
        "— input the warden-spelling and probe-fence guards read, not a hand-typed list that "
        "can go stale; the spelling guard's non-vacuity floor counts the spellings "
        "it finds in them"),
    "test_skillpack:_BARE_WARDEN": (
        UNSWEPT, "the bare-`warden` matcher of the warden-spelling guard — one "
        "lookbehind, no character class, no deletable alternative. Covered by "
        "test_every_warden_invocation_in_the_pack_is_spelled_from_the_"
        "resolved_warden, which reddens the moment a bare spelling is back"),
    "test_skillpack:UNRESOLVED": (
        UNSWEPT, "the skills still calling the launcher outright, until "
        "agentops-hy6o.32.1 empties it. Covered by test_every_skill_that_runs_"
        "warden_resolves_it_first_in_one_shared_step, which compares it by "
        "EQUALITY with the set derived from the tree, so a deleted or extra "
        "member is already red without a sweep"),
    "test_skillpack:_PROBE_FENCE": (
        UNSWEPT, "the if/then/else/fi probe-fence reader of the probe-fence guard — "
        "no deletable alternative. Covered by test_every_probe_fence_"
        "branches_with_two_distinct_arms_of_the_right_polarity, watched red on "
        "the bead's three mutations (polarity inverted, else arm deleted, "
        "fence un-branched)"),
    "test_skillpack:PROBED_SKILLS": (
        SWEPT, "which skills the structural probe-fence guard reads; the guard "
        "derives the same set from the tree (every skill whose fences probe "
        "with --help), so a deleted row is red rather than a skill silently "
        "unread"),
    "test_attest:CHANGED": (
        FIXTURE, "the changed-file list the dispatch-outcome tests hand build — "
        "expected input, not a pattern any guard consults"),
    "test_tag_vocabulary_guards:DECLARED_BY_RETRO": (
        UNSWEPT, "tags the retro declares; outside the subject"),
    "test_tag_vocabulary_guards:DECLARED_BY_THE_RECEIPT_RE_READ": (
        UNSWEPT, "tags the 2026-09-20 re-read declares; outside the subject"),
    "test_unknown_key_render:_BARE_SORT": (
        SWEPT, "the bare-sort detector; each of its three local-name "
        "alternatives pinned by the scan's own FIRES case"),
    "test_unknown_key_render:HETEROGENEOUS_LOADERS": (
        CONTROL, "one case per loader whose unknown-key render is checked"),
    "test_corpus_rules:CORPUS_RULES": (
        UNSWEPT, "the corpus-written rules and their bars; outside the "
        "subject"),
    "test_corpus_rules:ADOPTED_RULES": (
        UNSWEPT, "the machine-adopted rules and the tags they draw on; outside "
        "the subject"),
    "test_memory_rule_ids:DECLARED": (
        UNSWEPT, "rule ids the fixture declares; outside the subject"),

    # ── fixtures: input or expected data. Deleting an entry changes what one
    # test feeds itself, which is a different question from whether a guard can
    # fail — so these are accounted for and not swept.
    "test_adopted_rules:ADOPTED": (
        FIXTURE, "the adopted-rule bodies the cases are run against"),
    "test_adopted_rules:CASES": (
        FIXTURE, "per-rule hit and miss snippets, as input"),
    "test_adopted_rules:DOCKER_CASES": (
        FIXTURE, "dockerfile snippets, as input to the same check"),
    "test_adopted_rules:PIN_CASES": (
        FIXTURE, "action-pin snippets, as input to the same check"),
    "test_adopted_rules:RUN_PIN_CASES": (
        FIXTURE, "run-step pin snippets, as input to the same check"),
    "test_advisor:ENTRY": (FIXTURE, "a sample advisor entry, as input"),
    "test_advisor:ENTRY_2": (
        FIXTURE, "a second sample advisor entry, as input"),
    "test_advisor:PYTHON_IDIOM_ENTRIES": (
        FIXTURE, "the shipped entries tagged langs: [python], as expected data"),
    "test_attest:FINDING": (FIXTURE, "a sample finding payload, as input"),
    "test_attest:REVIEWERS": (
        FIXTURE, "a sample two-reviewer roster, as input to attest.build"),
    "test_catalog:RAW_ENTRIES": (
        FIXTURE, "the catalog entries as shipped, read as input"),
    "test_audit:REVIEW_DOC": (
        FIXTURE, "a sample review document the audit reads"),
    "test_build_wiki:ENV": (
        FIXTURE, "the environment the wiki build subprocess runs under"),
    "test_cage_runner:STUBS": (
        FIXTURE, "stub scripts the caged runner calls, as input"),
    "test_cage:_PREVIOUS_DENIES": (
        FIXTURE, "deny rules the rendered profile must keep, as expected values"),
    "test_catalog:GOOD": (FIXTURE, "a well-formed catalog entry, as input"),
    "test_catalog:GOOD_EDITION": (
        FIXTURE, "a well-formed catalog edition, as input"),
    "test_catalog_starters:CHECKS": (
        FIXTURE, "the starter check definitions, as input"),
    "test_catalog_starters:KNOWN_DANGEROUS": (
        FIXTURE, "starter snippets known to be dangerous"),
    "test_catalog_starters:_MESSAGE_CLAIMS": (
        FIXTURE, "the claim each starter message makes"),
    "test_declare_check:_DIRECTION_PROBES": (
        FIXTURE, "one shell probe per stated _shell_text limit with the "
        "direction it errs in, as input to "
        "test_each_stated_limit_errs_in_the_direction_the_docstring_bins_it"),
    # ── tests/test_malformation_matrix.py. A sibling harness whose axes are
    # DERIVED from the code the same way this one's registry is, so most of
    # these are derivations rather than declarations: mutating one would
    # measure a different subject, not a weaker guard.
    "test_malformation_matrix:CLI_LEAVES": (
        DERIVED, "every leaf of `cli.build_parser()`, read from the parser — "
        "the matrix's command axis, and the reason a subcommand added tomorrow "
        "reddens that module rather than passing unseen"),
    "test_malformation_matrix:RECORD_FIELDS": (
        DERIVED, "the record's own field names, read out of "
        "`memory.build_records`' dict literal — derived, not declared"),
    "test_malformation_matrix:ENVELOPE_KEYS": (
        DERIVED, "the envelope's keys, read from the producer the same way"),
    "test_malformation_matrix:DRIVEN": (
        UNSWEPT, "the COVERAGE rows that are `Driven` — a filter over "
        "DECLARATIONS (a hand-written `Driven(...)` vs `Excluded(...)` per "
        "leaf), not a derivation from code. It is outside the subject, "
        "and it carries the same axis-shrink property as `MUTATIONS`, closed "
        "by `_MUST_DRIVE` in that module, which names the driven "
        "leaves independently, and "
        "`test_no_declared_axis_can_shrink_in_silence` compares the two "
        "in both directions, so a flipped row is named rather than dropping a "
        "count under a floor"),
    "test_malformation_matrix:NAMERS": (
        UNSWEPT, "the DRIVEN rows that must NAME the bad shard — membership is "
        "a hand-declared `names=` flag per row. It is outside the subject, and it "
        "was the sharper axis-shrink case (flipping "
        "('memory', 'check-vocabulary') to `names=False` once left that module "
        "byte-identical to baseline); closed by `_MUST_NAME`, "
        "which names the tier-2 leaves independently of the flag"),
    "test_malformation_matrix:COVERAGE": (
        CONTROL, "one row per CLI leaf, each `Driven` or `Excluded` with a "
        "written reason — the classification table its own "
        "`test_every_cli_leaf_is_classified` requires to be TOTAL over "
        "`CLI_LEAVES`, which is the same shape as this file's REGISTRY"),
    "test_malformation_matrix:MUTATIONS": (
        UNSWEPT, "the five shapes a single field can take when it is wrong — "
        "`absent` is the one a type check alone cannot see. It is outside the "
        "subject, and it WAS a "
        "real instance of this harness's own class: the matrix derives its "
        "cells FROM this tuple, so deleting a member shrank the axis and the "
        "assertions over it together and nothing reddened — measured by this "
        "sweep, and closed by "
        "`_MALFORMATION_SHAPES`, which names the five independently"),
    "test_malformation_matrix:_DRIVEN_ARGV": (
        UNSWEPT, "not vocabulary at all: an empty list that `drive` appends "
        "`(root, argv)` to as the module runs, so the store guard can WITNESS "
        "that the sweep it watches really executed rather than take a "
        "fixture's word for it. It declares no alternatives — the literal is "
        "`[]` — so there is nothing for this harness to mutate; what gives it "
        "content is execution, which puts it outside the subject. Covered by "
        "mutation instead: stubbing the sweep to return a plausible table "
        "without driving anything reddens the guard, and it still reddens "
        "with sibling cells driving the same argv against their own roots, "
        "which is what the root half of each entry is for"),
    "test_malformation_matrix:_MUTANTS": (
        DERIVED, "the VALUE for each of the four non-absent mutations; "
        "`MUTATIONS` above is the axis and this is its lookup"),
    "test_malformation_matrix:_MALFORMATION_SHAPES": (
        CONTROL, "one entry per member of `MUTATIONS`, named independently of "
        "the tuple the matrix iterates — deleting a shape from the axis leaves "
        "this marker unfound and `test_no_declared_axis_can_shrink_in_"
        "silence` names it"),
    "test_malformation_matrix:_MUST_DRIVE": (
        CONTROL, "the CLI leaves the matrix must actually drive, named "
        "independently of the `Driven(...)`/`Excluded(...)` constructors that "
        "select them — flipping a row is a finding rather than one fewer cell"),
    "test_malformation_matrix:_MUST_EXIT": (
        CONTROL, "the driven leaves that must EXIT non-zero on a refused "
        "record, named independently of the hand-declared `exits=` flag — the "
        "fourth knob of the same shape, which round 1 (C05) measured "
        "unpinned: flipping `exits=False` on `memory ingest` left that module "
        "at 216 passed"),
    "test_malformation_matrix:_MUST_NAME": (
        CONTROL, "the driven leaves held to tier 2, named independently of the "
        "hand-declared `names=` flag — flipping one is a finding rather than a "
        "count sliding under a floor"),
    "test_malformation_matrix:_STORE_AT_IMPORT": (
        FIXTURE, "a fingerprint of the live store taken at import, so the "
        "module can prove it did not write to the real one — a measurement of "
        "the environment, not a pattern any guard consults"),
    "test_measure:HEADER_FREE_ROWS": (
        FIXTURE, "measure rows that carry no header, as input"),
    "test_mechanical:FREEZE_PARAMS": (
        FIXTURE, "contract-freeze rule params, as input"),
    "test_mechanical:PROMPT_PARAMS": (
        FIXTURE, "prompt-eval rule params, as input"),
    "test_memory:FIXTURE_RULE_IDS": (
        FIXTURE, "the synthetic corpus's rule ids, as input"),
    "test_memory_clean_review:FIXTURE_RULE_IDS": (
        FIXTURE, "the synthetic corpus's rule ids, as input"),
    "test_memory_identity:RULE_IDS": (
        FIXTURE, "the synthetic corpus's rule ids, as input"),
    "test_memory_identity:PR_172_PAIR": (
        FIXTURE, "two shard names from PR #172, as input"),
    "test_memory_identity:PR_167_TRIO": (
        FIXTURE, "three shard names from PR #167, as input"),
    "test_memory_v2:FIXTURE_RULE_IDS": (
        FIXTURE, "the synthetic corpus's rule ids, as input"),
    "test_memory_v2:GATE_FINDING": (
        FIXTURE, "a sample gate finding record, as input"),
    "test_memory_v2:JUDGED": (FIXTURE, "a sample judged record, as input"),
    "test_mine:PR": (FIXTURE, "a sample PR payload the miner reads"),
    "test_plan:HINTS": (FIXTURE, "sample plan hints, as input to the packet"),
    # ── test_runnable_docs: the documented-commands and code-block check.
    "test_runnable_docs:PAGES": (
        FIXTURE, "the pages the runnable-docs check reads, globbed from "
        "docs/wiki at import plus README — input, not a pattern any guard "
        "consults"),
    "test_runnable_docs:CLIS": (
        FIXTURE, "the two CLI entry points whose in-process --help the check "
        "reads — which programs count, not a pattern any guard consults"),
    "test_runnable_docs:SHELL_LANGS": (
        UNSWEPT, "fence languages the runnable-docs check reads as shell; "
        "outside the subject"),
    "test_runnable_docs:FENCE": (
        UNSWEPT, "the fence opener reader of the same check; outside the "
        "subject"),
    "test_runnable_docs:HEREDOC": (
        UNSWEPT, "the heredoc opener whose body the check skips; outside the "
        "subject"),
    "test_runnable_docs:CHOICES": (
        UNSWEPT, "the argparse choices reader over help text; outside the "
        "subject"),
    "test_runnable_docs:OPTION_SPEC": (
        UNSWEPT, "the option-column reader over help text; outside the "
        "subject"),
    "test_runnable_docs:FLAG": (
        UNSWEPT, "the flag tokenizer over an option column; outside the "
        "subject"),
    "test_runnable_docs:OPERATORS": (
        UNSWEPT, "shell operators that end one command; outside the subject"),
    "test_runnable_docs:PREFIX_WORDS": (
        UNSWEPT, "shell words that may precede a command; outside the "
        "subject"),
    "test_runnable_docs:WRAPPERS": (
        UNSWEPT, "commands that run the command after their options, with "
        "the options that take a value; outside the subject"),
    "test_runnable_docs:SPLIT_VALUE": (
        UNSWEPT, "wrapper options whose value is split into the command "
        "words; outside the subject"),
    "test_runnable_docs:OPERAND_ONLY": (
        UNSWEPT, "commands that name a CLI file without running it; outside "
        "the subject"),
    "test_runnable_docs:SHELLS": (
        SWEPT, "shells whose -c string is read as a command; each is named "
        "by its own input in test_every_shell_has_its_c_string_read, so a "
        "deleted member takes that cell red"),
    "test_runnable_docs:SHELL_VALUE_OPTIONS": (
        SWEPT, "shell long options that take the next word as a value; each "
        "is named by a cell whose -c string goes unread without it"),
}

# Which modules' tests may be the witness for a guard. Defaults to the guard's
# own module; declared only where that is wrong. `conftest.py` has no tests of
# its own, so a sweep that looked there would find nothing and report every
# one of its guards unpinned — a harness manufacturing findings, which is as
# useless as one hiding them.
WITNESSES: dict[str, tuple[str, ...]] = {
    # `toolchain.py` is a helper module with no tests of its own — the same
    # shape as `conftest.py` above. Its readers live in `test_toolchain`.
    "toolchain:_INSTALL_HINT": ("test_toolchain",),
}

# Alternatives knowingly unpinned, each with the measurement that says so. An
# exemption that turns out to BE pinned is itself a finding, so none can outlive
# its reason. Keep this list short: it is the one place the harness takes an
# argument instead of a test.
EXEMPT: dict[str, dict[str, str]] = {
}

# How many alternatives each SWEPT guard holds, exact in both directions. A
# total floor lets an alternative and its witness leave together with the suite
# green, and a count that rises past it goes stale without failing. Per guard
# rather than summed, so a loss in one guard cannot hide behind a gain in
# another, and the failure names the guard that changed.
SWEPT_ALTERNATIVES: dict[str, int] = {
    "test_init_proof:VISIBILITY": 7,
    "test_skillpack:_REVIEW_PROTOCOLS": 3,
    "test_skillpack:_LENS_SOURCE_SPELLINGS": 3,
    "test_toolchain:_INSTALLER": 3,
    "test_toolchain:_VERIFY_CARVE_OUT": 1,
    "toolchain:_INSTALL_HINT": 3,
    "test_ambient_git:_BRANCH_PINS": 3,
    "test_ambient_git:_DEMONSTRATES_THE_BUILT_IN_DEFAULT": 1,
    "test_ambient_git:_ENV_WRITERS": 6,
    "test_ambient_git:_GIT_CALLEES": 7,
    "test_ambient_git:_GUARD_EXEMPTIONS": 2,
    "test_ambient_git:_REPO_STARTERS": 2,
    "test_ambient_git:_SHELL_BRANCH_PIN": 7,
    "test_ambient_git:_SHELL_REPO_START": 2,
    "test_config:_ALLOWED_READERS": 1,
    "test_config:_CANARY_NAMES": 2,
    "test_config:_CANARY_PATHS": 7,
    "test_config:_READ_CALLS": 3,
    "test_docs:_ADOPTING_ADAPTATIONS": 2,
    "test_docs:HOME_PATH_EXEMPT": 2,
    "test_git_repo_binding:REPO_FREE_VERBS": 4,
    "test_git_repo_binding:_REPO_BINDING_GLOBALS": 2,
    # 5: the set is derived by probing the git on this machine rather than its
    # synopsis. `--super-prefix` is out (git 2.55 answers "unknown option");
    # `--attr-source` and `--config-env` are in — both eat a separated value,
    # and each has its own MUST_NOT_FIRE row.
    "test_git_repo_binding:_VALUE_TAKING_NON_BINDINGS": 5,
    "test_docs:HOME_PLACEHOLDERS": 4,
    "test_docs:HOME_RE": 9,
    "test_review_round_isolation:_ANOTHER_ROUND": 12,
    "test_review_round_isolation:_CLAUSE_BREAKS": 6,
    "test_review_round_isolation:_NEGATOR": 10,
    "test_review_round_isolation:_ONE_DIR_HOLDS_EVERY_ROUND": 6,
    "test_review_round_isolation:_TRANSFER": 23,
    "test_review_round_isolation:_TRANSFERRED": 3,
    "test_round_isolation:_ADD_METHODS": 3,
    "test_round_isolation:_CLOCK_MODULES": 4,
    "test_round_isolation:_CLOCK_READERS": 33,
    "test_round_isolation:_CODE_FROM_DATA": 3,
    "test_round_isolation:_MODULE_DUNDERS": 5,
    "test_round_isolation:_NOT_PROGRAMS": 1,
    "test_self_cage:PROHIBITION": 9,
    "test_round_isolation:_SUBPROCESS_SITES": 5,
    "test_round_isolation:_TIME_MODULE_ONLY": 3,
    "test_runnable_docs:SHELLS": 5,
    "test_runnable_docs:SHELL_VALUE_OPTIONS": 2,
    "test_skillpack:PROBED_SKILLS": 3,
    "test_skillpack:_FOREIGN_FLAGS": 5,
    "test_skillpack:_WIRED_COMMANDS": 4,
    "test_skillpack:_WIRED_SECTIONS": 3,
    "test_unknown_key_render:BAD_KEYS": 2,
    "test_unknown_key_render:_BARE_SORT": 3,
}

_CONTROL_SERVES: dict[str, str] = {
    "test_skillpack:_LENS_SOURCE_RESTATEMENTS":
        "test_skillpack:_LENS_SOURCE_SPELLINGS",
    "test_review_round_isolation:_TRANSFER_CONTROLS":
        "test_review_round_isolation:_TRANSFER",
    "test_review_round_isolation:_ANOTHER_ROUND_CONTROLS":
        "test_review_round_isolation:_ANOTHER_ROUND",
    "test_round_isolation:_CAUGHT_PROGRAMS":
        "test_round_isolation:_CLOCK_READERS",
    "test_ambient_git:_GENERIC_DRIVER_PLANTS":
        "test_ambient_git:_GIT_CALLEES",
}

# Control tables pinned by their own COMPLETENESS assertion rather than through
# a guard alternative: `_CODE_FROM_DATA` has three members and fifteen plants —
# five per name — so no single row's deletion leaves a member unexercised. What
# it leaves unexercised is a (name, shape) PAIR, and the named test asserts the
# full cross product directly. The five `test_malformation_matrix` tables are
# the same shape: each axis is named independently of the table the matrix
# iterates, and a row-by-row measurement found every row of all five pinned by
# the named test (36 + 5 + 9 + 5 + 7 rows, 0 deletable), each witness costing
# nothing to run. `PROHIBITION_PLANTS` joined them on the same terms and only
# after the same measurement: its witness asks each of `gm.alternatives`' 9
# alternatives to LOSE a plant when deleted, which is the sweep's own
# question, and all 8 rows are then deletable-red (measured row by row; under
# the first witness, which split the pattern on `|`, 6 of 8 were — the two
# `off[- ]limits` rows shared one spelling and neither was pinned).
_SELF_PINNED_CONTROLS: dict[str, tuple[str, str]] = {
    "test_round_isolation:_CODE_FROM_DATA_PLANTS": (
        "test_round_isolation",
        "test_every_declared_code_from_data_name_is_planted_in_the_control"
    ),
    "test_malformation_matrix:COVERAGE": (
        "test_malformation_matrix", "test_every_cli_leaf_is_classified"),
    "test_malformation_matrix:_MALFORMATION_SHAPES": (
        "test_malformation_matrix",
        "test_no_declared_axis_can_shrink_in_silence"),
    "test_malformation_matrix:_MUST_DRIVE": (
        "test_malformation_matrix",
        "test_no_declared_axis_can_shrink_in_silence"),
    "test_malformation_matrix:_MUST_EXIT": (
        "test_malformation_matrix",
        "test_no_declared_axis_can_shrink_in_silence"),
    "test_malformation_matrix:_MUST_NAME": (
        "test_malformation_matrix",
        "test_no_declared_axis_can_shrink_in_silence"),
    "test_self_cage:PROHIBITION_PLANTS": (
        "test_self_cage", "test_every_prohibition_spelling_is_planted"),
    "test_self_cage:HANDED_OVER": (
        "test_self_cage",
        "test_every_surface_the_prompt_hands_over_survives_the_fence"),
}

# THE THIRD BUCKET: control tables pinned by REVIEW ALONE.
# These are the NEGATIVE controls — inputs a guard must stay silent on — and
# no DELETION of a guard alternative can reveal a missing one: a guard that
# has lost a branch fires less, never more. Each row carries the measurement
# that put it here, because "review alone" is the weakest disposition this
# file has and a row that turns out to be pinned by a test belongs in the
# bucket above with that test named. The three buckets partition REGISTRY's
# CONTROL rows, executed by `test_every_control_table_is_in_exactly_one_
# bucket`: that is what makes deleting a row from ANY of the three a named
# finding, where the two tables the control-row test iterates could not fail
# on their own shrinking.
_REVIEW_PINNED_CONTROLS: dict[str, str] = {
    "test_git_repo_binding:MUST_FIRE":
        "positive plants for the repo-less-git scan — the parametrize source, "
        "so a deleted row is one fewer case rather than a failure (the "
        "axis-shrink shape, disclosed); measured 0 of 25 rows deletable "
        "takes any test of its module red — re-measured at 25 rows, one "
        "row per shape the best-effort reader was found silent on",
    "test_git_repo_binding:MUST_NOT_FIRE":
        "negative plants for the same scan — calls that must NOT fire, and a "
        "guard that has lost a branch fires LESS, never more, so no deletion "
        "can reveal a missing row; measured 0 of 17 rows deletable takes any "
        "test of its module red — re-measured at 17 rows: one per correct call "
        "the best-effort reader must still read, two where it disagreed with "
        "the exact reader, and --attr-source and --config-env in place of "
        "--super-prefix",
    "test_round_isolation:_SILENT_PROGRAMS":
        "negative plants for the clock-reader scan — frozen conversions that "
        "must not fire; measured 0 of 14 rows deletable takes any test of "
        "its module red",
    "test_unknown_key_render:HETEROGENEOUS_LOADERS":
        "one case per loader whose unknown-key render is driven — the "
        "parametrize source, so a deleted row is one fewer case rather than "
        "a failure (the axis-shrink shape, disclosed); measured 0 of 6 "
        "rows deletable takes any test of its module red",
}


def _module(name: str):
    import importlib
    return importlib.import_module(name)


def _split(key: str) -> tuple[str, str]:
    module, _, name = key.partition(":")
    return module, name


def _sweep(key: str):
    module_name, name = _split(key)
    module = _module(module_name)
    return gm.sweep(
        module, name,
        exempt=EXEMPT.get(key, {}),
        witnesses=[_module(w) for w in WITNESSES.get(key, (module_name,))])


def test_every_module_level_vocabulary_in_the_suite_is_classified():
    """The anti-silent-skip, and the reason this file is a CI check rather than
    a report.

    Discovery is derived — an AST read of every module-level assignment in
    `tests/*.py`, plus a runtime look at what the name holds — so a new regex
    alternation, vocabulary, plant table or page set appears here the moment it
    is written. This asserts `REGISTRY` accounts for exactly that set, in BOTH
    directions: an unclassified vocabulary is red, and a registry entry whose
    subject has gone is red too.

    A harness that silently skipped a guard would be the same defect one level
    up, and that is the trap this test exists to close. It is cheap — no test
    is run — so it is the half that always executes.
    """
    found = {c.key for c in gm.discover()}
    assert found, "discovery found no vocabularies at all, so this is vacuous"
    unclassified = sorted(found - set(REGISTRY))
    assert not unclassified, (
        f"{len(unclassified)} module-level vocabular(y/ies) in tests/ carry no "
        f"disposition in REGISTRY: {unclassified}. Classify each — SWEPT if a "
        "guard reads it (and then give every alternative a control), CONTROL "
        "if it is a control table, DERIVED if a swept pattern is built from "
        "it, FIXTURE if it is input or expected data, UNSWEPT if it is a guard "
        "this harness does not reach and you are writing down why. An "
        "unclassified one is exactly how an unpinned alternative ships")
    stale = sorted(set(REGISTRY) - found)
    assert not stale, (
        f"REGISTRY names {stale}, which discovery no longer finds — the "
        "constant was renamed or removed and its disposition outlived it")
    kinds = {k for k, _ in REGISTRY.values()}
    assert kinds <= {SWEPT, CONTROL, DERIVED, FIXTURE, UNSWEPT}, kinds
    for key, (kind, note) in REGISTRY.items():
        assert note and len(note) > 20, (
            f"{key} is classified {kind} with no reason worth reading: "
            f"{note!r}")
    for key in WITNESSES:
        assert REGISTRY[key][0] == SWEPT, f"{key} declares witnesses but is "
    for key in EXEMPT:
        assert REGISTRY[key][0] == SWEPT, f"{key} declares exemptions but is "
    for key, served in _CONTROL_SERVES.items():
        assert REGISTRY[key][0] == CONTROL, key
        assert REGISTRY[served][0] == SWEPT, served
    for key in _SELF_PINNED_CONTROLS:
        assert REGISTRY[key][0] == CONTROL, key


def test_every_alternative_of_every_swept_guard_is_load_bearing():
    """The sweep itself. Every alternative of every SWEPT guard is deleted in
    turn and some named test must go RED.

    Read the SWEPT paragraph at the top of this module for what green here
    does and does not prove: it proves no alternative is deletable in silence,
    not that every alternative is known to change a verdict.
    """
    findings, ledger = [], {}
    # The members of HOME_PATH_EXEMPT are corpus shards; the public export
    # carries none, so deleting one there cannot redden anything.
    unreachable = {"test_docs:HOME_PATH_EXEMPT"} if EXPORT else set()
    for key, (kind, _) in sorted(REGISTRY.items()):
        if kind != SWEPT or key in unreachable:
            continue
        found, witnesses = _sweep(key)
        findings += found
        ledger[key] = len(witnesses) + len(found)
    assert ledger, "no guard is declared SWEPT, so this test proves nothing"
    assert not findings, (
        f"{len(findings)} guard alternative(s) are pinned by no test — delete "
        "or neuter each and the suite stays green, so the guard's reach there "
        "is a claim about it and not a property of it:\n  "
        + "\n  ".join(str(f) for f in findings)
        + "\n\nFix by adding ONE control per alternative (the shape "
        "`_TRANSFER_CONTROLS` and `_ANOTHER_ROUND_CONTROLS` use), by deleting the "
        "alternative if it cannot change a verdict, or — only with a "
        "measurement — by listing it in EXEMPT with the reason. Prefer a "
        "BEHAVIOURAL control: a restatement of the vocabulary reddens this "
        "sweep too, and proves only that someone typed the members twice.")
    # An exempt alternative is enumerated but has no witness, so it is added
    # back before comparing with the table.
    swept = {key: n + len(EXEMPT.get(key, {})) for key, n in ledger.items()}
    pinned = {k: n for k, n in SWEPT_ALTERNATIVES.items() if k not in unreachable}
    assert swept == pinned, (
        "the sweep enumerated a different set of alternatives than "
        f"SWEPT_ALTERNATIVES pins: {_count_drift(swept)}")
    if unreachable:
        pytest.skip(f"public export: swept every guard but {sorted(unreachable)}, "
                    "whose members are corpus shards publish.yaml leaves out")


def _count_drift(counted: dict[str, int]) -> list[str]:
    return [f"{key}: pinned {SWEPT_ALTERNATIVES.get(key)}, counted "
            f"{counted.get(key)}"
            for key in sorted(set(counted) | set(SWEPT_ALTERNATIVES))
            if counted.get(key) != SWEPT_ALTERNATIVES.get(key)]


def test_no_swept_alternative_can_leave_the_sweep_in_silence():
    """Every SWEPT guard holds exactly the alternatives `SWEPT_ALTERNATIVES`
    pins.

    Deleting an alternative together with the control that witnesses it keeps
    the sweep green, because nothing is left unpinned. Only a count can see
    that. This runs no test, so it holds even where the sweep is too slow to
    run.
    """
    counted = {}
    for key, (kind, _) in REGISTRY.items():
        if kind == SWEPT:
            module_name, name = _split(key)
            counted[key] = len(gm.alternatives(
                getattr(_module(module_name), name)))
    drift = _count_drift(counted)
    assert not drift, (
        "a SWEPT guard's alternatives no longer match SWEPT_ALTERNATIVES: "
        f"{drift}. A lower count means an alternative left the sweep; if "
        "that was deliberate, or one was added, edit the table in the same "
        "change so the reach moves on purpose")


def test_discovery_sees_a_vocabulary_however_it_is_bound(tmp_path):
    """Discovery sees a module-level vocabulary however it is bound: tuple
    targets, and names bound inside module-scope `try:` / `if:` / `for:` /
    `with:` / `match:`.

    `tests/` need not carry any of those shapes, so neither reading can rely
    on the tree for a control: a naive reader can return the same `discover()`
    there. `discover(tests_dir=...)` takes a directory for exactly this: a
    synthetic module carrying every binding shape, where each one holds a
    value that DOES classify, so dropping any branch of the read drops a
    candidate.

    ONE ROW PER MEMBER of `_SAME_SCOPE_BLOCKS`, `match:` included: the fixture
    below covers body / orelse / finalbody / handlers / cases, so deleting any
    of the five takes this test red.

    The fixture RAISES to reach its except-handler, and that is not decoration:
    discovery reads the AST for the names and the imported module for the
    VALUES, so a name whose binding never executes has nothing to classify and
    is invisible whatever the AST says. That is a real bound of this design,
    and the handler is exercised rather than asserted away.
    """
    (tmp_path / "test_bindings.py").write_text(
        "import re\n"
        "PLAIN = ('a', 'b')\n"
        "ANNOTATED: tuple = ('c', 'd')\n"
        "TUPLE_ONE, TUPLE_TWO = ('e', 'f'), ('g', 'h')\n"
        "[LIST_TARGET] = [('i', 'j')]\n"
        "if True:\n"
        "    IN_IF = ('k', 'l')\n"
        "try:\n"
        "    IN_TRY = ('m', 'n')\n"
        "except ImportError:\n"
        "    pass\n"
        "else:\n"
        "    IN_ELSE = ('q', 'r')\n"
        "finally:\n"
        "    IN_FINALLY = ('s', 't')\n"
        "try:\n"
        "    raise ImportError\n"
        "except ImportError:\n"
        "    IN_HANDLER = ('o', 'p')\n"
        "for _each in (1,):\n"
        "    IN_FOR = ('u', 'v')\n"
        "with open(__file__) as _fh:\n"
        "    IN_WITH = ('w', 'x')\n"
        "match ('y',):\n"
        "    case ('y',):\n"
        "        IN_MATCH = ('1', '2')\n"
        "    case _:\n"
        "        IN_MATCH_ELSE = ('3', '4')\n"
        "def _fn():\n"
        "    NOT_MODULE_LEVEL = ('y', 'z')\n"
        "    return NOT_MODULE_LEVEL\n"
        "class _C:\n"
        "    ALSO_NOT = ('1', '2')\n")
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        found = {c.name for c in gm.discover(tmp_path)}
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("test_bindings", None)

    for name in ("PLAIN", "ANNOTATED", "TUPLE_ONE", "TUPLE_TWO", "LIST_TARGET",
                 "IN_IF", "IN_TRY", "IN_HANDLER", "IN_ELSE", "IN_FINALLY",
                 "IN_FOR", "IN_WITH", "IN_MATCH"):
        assert name in found, (
            f"discovery cannot see a vocabulary bound as {name} — a guard "
            "spelled that way reaches the suite with no disposition, and the "
            "registry's TOTAL claim is false for it")
    # …and MODULE scope is where it stops. A function or class body is not
    # module scope, and reading into one would make the registry demand a
    # disposition for every local in the suite.
    for name in ("NOT_MODULE_LEVEL", "ALSO_NOT"):
        assert name not in found, (
            f"discovery descended into a function or class body and found "
            f"{name} — module scope is the declared bound")


def test_an_empty_vocabulary_is_discovered_on_every_box(tmp_path):
    """The registry's TOTAL claim must be a property of the TREE, not of the
    machine it runs on.

    Discovery reads each name's runtime VALUE to decide whether it is a
    vocabulary, and a container whose contents depend on the environment can
    be empty on one box and not on another. `test_commit_lint:MISSING_SHELLS`
    is that shape:

        MISSING_SHELLS = tuple(s for s in ALL_SHELLS if not shutil.which(s))

    It is `()` where every shell resolves and non-empty where one does not, so
    if an empty container were not discovered, "every module-level vocabulary
    carries a disposition" would be true only on some machines.

    So an empty container is DISCOVERABLE, exactly as a pattern with no
    deletable alternative already is: "no alternative to delete" is a real
    answer about a vocabulary and the registry is where it gets recorded, not
    something the enumerator may swallow. The fixture below is written to be
    empty on every machine, so this test cannot pass by accident of
    environment anywhere.

    EACH HALF OF THE MECHANISM IS PINNED SEPARATELY. A fixture name that is
    BOTH an empty container at runtime AND a literal stays green with either
    half reverted. `RUNTIME_ONLY` is shaped by neither the AST list nor a
    literal, so only `classify`'s empty-container answer finds it; `AST_ONLY`
    never executes its binding, so it has no runtime value at all and only
    `_container_shaped` finds it.

    The NAME says what this proves, and it is not "does not depend on a
    vocabulary's runtime value": the AST half is blind to a container built by
    an ATTRIBUTE call, so one bound under a machine-dependent branch still
    splits by box.
    """
    (tmp_path / "test_empty.py").write_text(
        "import shutil\n"
        "EMPTY_TUPLE = ()\n"
        "EMPTY_LIST = []\n"
        "EMPTY_DICT = {}\n"
        "EMPTY_SET = frozenset()\n"
        # The MISSING_SHELLS shape: a comprehension filtered to nothing.
        "EMPTY_FROM_FILTER = tuple(x for x in ('a', 'b') if False)\n"
        # …and the environment-derived spelling itself, pinned to empty by a
        # name no machine has on PATH.
        "ENV_DERIVED = tuple(n for n in ('no-such-binary-xyzzy',)\n"
        "                    if shutil.which(n))\n"
        "NON_EMPTY = ('a', 'b')\n"
        # Isolates the runtime half: an attribute call, so `_container_shaped`
        # does not shape it; empty at runtime, so only `classify` finds it.
        "import collections\n"
        "RUNTIME_ONLY = collections.OrderedDict()\n"
        # Isolates the AST half: the binding never runs, so there is no runtime
        # value to classify; the assigned expression is a literal, so only
        # `_container_shaped` finds it.
        "if False:\n"
        "    AST_ONLY = ['unreachable']\n")
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        found = {c.name: c for c in gm.discover(tmp_path)}
        # Read off the imported fixture while it is still importable — the
        # two isolating probes below need its live values.
        probe = sys.modules["test_empty"]
        ast_only_value = getattr(probe, "AST_ONLY", None)
        runtime_only_shape = gm.classify(probe.RUNTIME_ONLY)
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("test_empty", None)

    for name in ("EMPTY_TUPLE", "EMPTY_LIST", "EMPTY_DICT", "EMPTY_SET",
                 "EMPTY_FROM_FILTER", "ENV_DERIVED", "NON_EMPTY",
                 "RUNTIME_ONLY", "AST_ONLY"):
        assert name in found, (
            f"discovery cannot see {name} because it is EMPTY on this machine, "
            "so whether the registry is total depends on the box this runs on "
            "— which is the environment-dependent verdict this module exists "
            "to refuse")
    assert found["EMPTY_TUPLE"].size == 0, found["EMPTY_TUPLE"]
    assert found["NON_EMPTY"].size == 2, found["NON_EMPTY"]
    # The two isolating names reach discovery by DIFFERENT halves, asserted so
    # a later reader cannot collapse them into one mechanism: the AST half does
    # not shape an attribute call, and the runtime half has no value to read
    # for a binding that never ran.
    shaped = gm._container_shaped(tmp_path / "test_empty.py")
    assert "RUNTIME_ONLY" not in shaped and "AST_ONLY" in shaped, shaped
    assert ast_only_value is None, (
        "the AST-only probe has a runtime value, so it no longer isolates the "
        "source half — pick a binding that genuinely does not execute")
    assert runtime_only_shape == ("mapping", 0), runtime_only_shape


def test_the_sweep_reports_an_unpinned_alternative():
    """The harness's own control. A sweep that could not report a finding would
    be the defect it hunts, one level up — so it is shown reporting one.

    Driven over a SYNTHETIC module carrying two patterns and one test: the test
    pins only the first alternative of each, so the sweep must name the rest
    and must NOT name the ones it pins.
    """
    fake = types.ModuleType("fake_guard_module")
    fake.GUARD = re.compile(r"alpha|beta|gamma")
    fake.VOCAB = ("one", "two")

    def test_only_alpha_and_one():
        assert fake.GUARD.search("alpha")
        assert "one" in fake.VOCAB

    fake.test_only_alpha_and_one = test_only_alpha_and_one

    findings, witnesses = gm.sweep(fake, "GUARD")
    assert sorted(f.alternative for f in findings) == [
        "branch 'beta'", "branch 'gamma'"], findings
    assert list(witnesses) == ["branch 'alpha'"], witnesses

    findings, witnesses = gm.sweep(fake, "VOCAB")
    assert [f.alternative for f in findings] == ["member 'two'"], findings
    assert list(witnesses) == ["member 'one'"], witnesses

    # …and an EXEMPTION that is wrong is reported too, so no exemption can
    # outlive its reason: `alpha` IS pinned, and claiming otherwise is a
    # finding of its own.
    findings, _ = gm.sweep(fake, "GUARD", exempt={
        "branch 'alpha'": "claimed unpinned", "branch 'beta'": "really is",
        "branch 'gamma'": "really is"})
    assert len(findings) == 1 and "delete the exemption" in str(findings[0]), (
        findings)


def test_a_test_red_before_the_mutation_is_not_a_witness():
    """A test that is RED AT BASELINE under this shim is not a witness.

    Otherwise it is a universal witness: every alternative of every guard in
    its module reads as pinned by it, which manufactures coverage — the
    harness's own defect class. A test can be red under the shim for reasons
    unrelated to any guard: a shim fixture that differs by box, or a
    session-scoped conftest fixture such as `clean_git_env` that runs only
    inside a pytest session. The check below refuses both kinds without
    needing to tell them apart. Where a MEASUREMENT is made matters: measure
    witnesses inside a pytest session, where this harness runs.

    So a red under mutation is a witness only if the same test is GREEN
    with the guard restored. The always-red test below sorts FIRST, so
    without the check it is the witness for every alternative.
    """
    fake = types.ModuleType("fake_baseline_red")
    fake.GUARD = re.compile(r"alpha|beta|gamma")

    def test_a_always_red():
        raise AssertionError("red whatever the guard says")

    def test_b_only_alpha():
        assert fake.GUARD.search("alpha")

    fake.test_a_always_red = test_a_always_red
    fake.test_b_only_alpha = test_b_only_alpha

    findings, witnesses = gm.sweep(fake, "GUARD")
    assert sorted(f.alternative for f in findings) == [
        "branch 'beta'", "branch 'gamma'"], (
        "a test that fails with the guard intact was accepted as the witness "
        f"for alternatives it cannot see: {findings}")
    assert witnesses == {"branch 'alpha'": "fake_baseline_red::test_b_only_alpha"}, (
        witnesses)


def test_a_test_whose_fixture_needs_an_unbuildable_fixture_is_blocked():
    """A test taking a fixture that needs one the shim cannot build is BLOCKED,
    not RUNNABLE.

    `callable_tests` must read fixtures transitively: reading only a test's
    OWN parameters reports such a test runnable, and `_resolve` then raises
    `Unrunnable` mid-sweep, which `run_test` re-raises, so the whole sweep
    crashes instead of listing the test as blocked. `test_build_wiki`'s
    `built`, a module-scoped fixture on `tmp_path_factory`, is the real case.
    A test the harness cannot run is counted and listed, never quietly
    dropped, and never a crash.
    """
    fake = types.ModuleType("fake_deep_fixture")

    def deep(tmp_path_factory):
        return tmp_path_factory

    def shallow(tmp_path):
        return tmp_path

    fake.deep = pytest.fixture(deep)
    fake.shallow = pytest.fixture(shallow)

    def test_needs_deep(deep):
        pass

    def test_needs_shallow(shallow):
        pass

    fake.test_needs_deep = test_needs_deep
    fake.test_needs_shallow = test_needs_shallow

    runnable, blocked = gm.callable_tests(fake)
    assert runnable == ["test_needs_shallow"], runnable
    assert list(blocked) == ["test_needs_deep"], blocked
    assert "tmp_path_factory" in blocked["test_needs_deep"], blocked


def test_a_dict_parametrize_case_reaches_the_test_as_the_dict():
    """A parametrize case that IS a dict is one argument, not a ParameterSet.

    The shim used to recognise `pytest.param(...)` by `hasattr(case, "values")`.
    A plain dict carries `.values` too — the builtin mapping method — so a dict
    case was read as a param object, `case.values` bound the method, and the
    subscript after it raised `TypeError`. Recognising the type instead is what
    tells the two apart.

    Both directions in one cell: the dict must arrive whole, and a real
    `pytest.param` must still be unwrapped, since a fix that only stopped
    crashing could have stopped unwrapping as well.
    """
    def one(scripts):
        pass

    one.pytestmark = [pytest.mark.parametrize(
        "scripts", [None, {"test": "exit 1"}]).mark]
    assert gm._parametrisations(one) == [
        {"scripts": None}, {"scripts": {"test": "exit 1"}}]

    def two(left, right):
        pass

    two.pytestmark = [pytest.mark.parametrize(
        "left,right", [({"a": 1}, {"b": 2})]).mark]
    assert gm._parametrisations(two) == [{"left": {"a": 1}, "right": {"b": 2}}]

    def wrapped(scripts):
        pass

    wrapped.pytestmark = [pytest.mark.parametrize(
        "scripts", [pytest.param({"test": "exit 1"}, id="node")]).mark]
    assert gm._parametrisations(wrapped) == [{"scripts": {"test": "exit 1"}}]


def test_a_module_holding_a_dict_parametrize_case_is_reachable():
    """`callable_tests` surveys a module with a dict-valued parametrize case
    without raising, so every guard in that module is reachable by the sweep.

    This is the defect's real cost. `sweep()` surveys the whole module before
    it mutates anything, so ONE dict case anywhere in a file took down the
    entire sweep — `test_every_alternative_of_every_swept_guard_is_load_
    bearing` errored out rather than reporting a finding — and no vocabulary
    in that file could be declared SWEPT at all. A harness whose contract is
    that its reach is counted was narrowing itself in silence.

    `test_init` is the module that measured it, named here rather than
    synthesised so the survey is driven over the real file.
    """
    import test_init

    holder = "test_a_node_repo_with_no_test_script_gets_no_test_step_and_a_warning"
    assert any(mark.name == "parametrize"
               and any(isinstance(case, dict) for case in mark.args[1])
               for mark in getattr(test_init, holder).pytestmark), (
        f"test_init::{holder} no longer carries a dict-valued parametrize "
        "case, so this cell no longer drives the shape it was written for — "
        "point it at another module that does rather than deleting it")

    runnable, blocked = gm.callable_tests(test_init)
    assert holder in runnable or holder in blocked, (
        f"{holder} was neither runnable nor blocked, so the survey dropped it")


def test_the_enumerator_refuses_a_pattern_it_cannot_take_apart():
    """Fail-closed on the harness's own reach.

    A pattern this cannot model is a pattern whose alternatives nothing here
    proves load-bearing, and reporting that as coverage is the whole failure.
    So an unmodelled group extension RAISES rather than being skipped — which
    also means the sweep above goes red if a future guard uses one, instead of
    quietly covering less.
    """
    with pytest.raises(gm.PatternUnreadable, match="not modelled"):
        gm.regex_alternatives(re.compile(r"(a)(?(1)b|c)"))
    with pytest.raises(gm.PatternUnreadable, match="not a vocabulary"):
        gm.alternatives(object())
    with pytest.raises(gm.PatternUnreadable, match="character class"):
        gm._class_end("a|[bc", 2)       # `re.compile` refuses this one first
    # …and the shapes it DOES model are taken apart, nested levels included.
    labels = [a.label for a in gm.regex_alternatives(
        re.compile(r"x(?:a|b)[cd]|y", re.I))]
    assert labels == ["branch 'a' of 'a|b'", "branch 'b' of 'a|b'",
                      "branch 'x(?:a|b)[cd]'", "branch 'y'",
                      "class member 'c' in '[cd]'",
                      "class member 'd' in '[cd]'"], labels
    # …and the mutated patterns keep the original flags, or every sweep over a
    # case-insensitive guard would measure a different guard.
    assert all(a.mutated.flags == re.compile("", re.I).flags
               for a in gm.regex_alternatives(re.compile("a|b", re.I)))


def test_the_sweep_writes_no_source_and_no_bytecode():
    """The stale-bytecode immunity, executed rather than asserted.

    The trap: pytest keys a rewritten `.pyc` on `(int(mtime), size)` of the
    source, so two same-size mutations restored inside one timestamp second can
    be served from the wrong cache and the sweep reports an alternative
    load-bearing that is not.

    This harness is immune by CONSTRUCTION, not by discipline: it mutates the
    module OBJECT, so no source file is rewritten and there is nothing for a
    `.pyc` to be stale against. Both halves of that are checked — no source
    mtime moves, and no bytecode appears — and the sweep is run TWICE to show
    the verdict is the same both times, which is the symptom a stale cache
    produces.
    """
    tests_dir = Path(gm.__file__).parent
    sources = sorted(tests_dir.glob("*.py"))
    before = {p: p.stat().st_mtime_ns for p in sources}
    cached_before = set(tests_dir.glob("**/__pycache__/*"))

    first = [str(f) for f in _sweep(
        "test_review_round_isolation:_TRANSFER")[0]]
    second = [str(f) for f in _sweep(
        "test_review_round_isolation:_TRANSFER")[0]]
    assert first == second == [], (first, second)

    moved = [p.name for p in sources if p.stat().st_mtime_ns != before[p]]
    assert not moved, (
        f"the sweep rewrote {moved} — the moment it edits a source file it "
        "inherits the stale-.pyc trap, and the immunity this "
        "docstring claims stops being structural")
    appeared = sorted(p.name for p in
                      set(tests_dir.glob("**/__pycache__/*")) - cached_before)
    assert not appeared, (
        f"bytecode appeared under tests/ during the sweep: {appeared}")


def test_a_control_row_is_pinned_through_the_guard_it_serves():
    """The CONTROL disposition, executed.

    The claim is that deleting a row from a control table does not change a
    guard — it removes the WITNESS for one of that guard's alternatives, which
    the sweep on that guard then reports. Asserted for every declared control
    table by deleting its FIRST row and requiring the served guard to report at
    least one finding. One row per table rather than all of them: the mechanism
    is what is in question, and a full cross-product would cost minutes.
    """
    # THE ENGINE'S OWN ENUMERATOR deletes the rows, as the companion's does:
    # a control table may be a tuple or a dict, and rebuilding it by hand from
    # `list(original)` handles tuples only.
    for table_key, guard_key in sorted(_CONTROL_SERVES.items()):
        module_name, table = _split(table_key)
        module = _module(module_name)
        original = getattr(module, table)
        rows = gm.alternatives(original)
        assert len(rows) > 1, f"{table_key} has nothing to delete"
        # SOME row, not the first: rows overlap on purpose — several controls
        # in one table can carry the same spelling, and a redundant row is not
        # a defect. What has to be true is that the table is load-bearing AT
        # ALL, which is what the mechanism claim amounts to.
        culprit = None
        for row in rows:
            setattr(module, table, row.mutated)
            try:
                findings, _ = _sweep(guard_key)
            finally:
                setattr(module, table, original)
            if findings:
                culprit = (row.label, findings)
                break
        assert culprit, (
            f"no row of {table_key} is load-bearing: every one can be deleted "
            f"with every alternative of {guard_key} still pinned. Either the "
            "table is entirely redundant, or it does not serve the guard "
            "`_CONTROL_SERVES` says it does")

    # …and the tables pinned by their OWN completeness assertion instead.
    for table_key, (witness_module, witness) in sorted(
            _SELF_PINNED_CONTROLS.items()):
        module_name, table = _split(table_key)
        module = _module(module_name)
        where = _module(witness_module)
        original = getattr(module, table)
        rows = gm.alternatives(original)
        assert rows, f"{table_key} has nothing to delete"
        # Green with the table INTACT first, or a red below is not a witness
        # — the universal-witness hole `sweep` closes, held shut here too.
        assert gm.run_test(where, witness) is None, (
            f"{witness} is red with {table_key} intact, so it can pin "
            "nothing about the table")
        reddened = None
        for row in rows:
            setattr(module, table, row.mutated)
            try:
                reddened = gm.run_test(where, witness)
            finally:
                setattr(module, table, original)
            if reddened:
                break
        assert reddened, (
            f"no row of {table_key} can be deleted with {witness} going red, "
            "so the completeness assertion that is supposed to pin this table "
            "pins nothing")


def test_every_control_table_is_in_exactly_one_bucket():
    """`_CONTROL_SERVES` and `_SELF_PINNED_CONTROLS` are the two tables the
    control-row test ITERATES, so deleting a row there narrows the loop and
    its assertions together and nothing in that test reddens.

    The pin is an independent naming, and REGISTRY is the other side of it:
    every CONTROL row there is pinned one level out by exactly ONE of three
    mechanisms — through the guard it serves (`_CONTROL_SERVES`), by its own
    completeness assertion (`_SELF_PINNED_CONTROLS`), or by review alone
    (`_REVIEW_PINNED_CONTROLS`, the third bucket, which carries the measured
    reason per row). The three PARTITION REGISTRY's CONTROL rows. Delete a
    row from any bucket and that control table is a CONTROL row in no
    bucket, named below; put a table in two buckets and the overlap is
    named. Neither of the first two tables could say that about itself.

    The third bucket is the honest one, and it cannot be a dumping ground
    in silence: every row there says WHY no deletion reveals it, with the
    measurement, and a table that IS pinned by a test belongs in the second
    bucket with that test named — the same two-direction contract the
    companion holds `SELF_REGISTRY` to.
    """
    controls = {k for k, (kind, _) in REGISTRY.items() if kind == CONTROL}
    assert controls, "REGISTRY declares no CONTROL row, so this is vacuous"
    buckets = {
        "_CONTROL_SERVES": set(_CONTROL_SERVES),
        "_SELF_PINNED_CONTROLS": set(_SELF_PINNED_CONTROLS),
        "_REVIEW_PINNED_CONTROLS": set(_REVIEW_PINNED_CONTROLS),
    }
    orphaned = sorted(controls - set().union(*buckets.values()))
    assert not orphaned, (
        f"{len(orphaned)} CONTROL table(s) are in no bucket: {orphaned}. Each "
        "control table is pinned one level out by exactly one mechanism — "
        "name it in _CONTROL_SERVES (through the guard it serves), "
        "_SELF_PINNED_CONTROLS (by its own completeness test) or "
        "_REVIEW_PINNED_CONTROLS (by review alone, with the measured reason)")
    for name, keys in buckets.items():
        not_controls = sorted(keys - controls)
        assert not not_controls, (
            f"{name} names {not_controls}, which REGISTRY does not classify "
            "CONTROL — the bucket has outlived its subject")
    names = list(buckets)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            overlap = sorted(buckets[first] & buckets[second])
            assert not overlap, (
                f"{overlap} are in both {first} and {second}; a table is "
                "pinned by exactly one mechanism, so one of those is a claim")
    # THE MEASUREMENT IS DERIVED, NOT TRUSTED. Every row states
    # it as "0 of N rows deletable", and N is the size of the table the row
    # describes — so N is checkable against the live table, and a row appended
    # without re-running the measurement leaves a number describing a table
    # nobody measured — e.g. a repair that re-measures MUST_NOT_FIRE at 9 rows,
    # appends two more in the same commit and leaves the 9, the
    # order-of-operations defect this bucket exists to prevent. A `len()` beside
    # the claim is the cheap half of re-measuring and it cannot go stale.
    for key, reason in _REVIEW_PINNED_CONTROLS.items():
        assert "measured" in reason and len(reason) > 60, (
            f"{key} rests on review alone with no measurement behind it: "
            f"{reason!r}")
        module_name, table = _split(key)
        claimed = re.search(r"0 of (\d+) rows deletable", reason)
        assert claimed, (
            f"{key} does not state its measurement in the one form this test "
            "can check — '0 of N rows deletable' — so nothing can tell whether "
            f"N still describes the table: {reason!r}")
        held = len(getattr(_module(module_name), table))
        assert int(claimed.group(1)) == held, (
            f"{key} says the measurement covered {claimed.group(1)} rows and "
            f"the table holds {held}. Re-run the sweep over the table AS IT "
            "STANDS and write that number: a count typed before the last rows "
            "were appended describes a table nobody measured")
    for key, (witness_module, witness) in _SELF_PINNED_CONTROLS.items():
        assert hasattr(_module(witness_module), witness), (
            f"{key} names a completeness witness that is gone: "
            f"{witness_module}::{witness}")
    # THE EXECUTED BUCKETS ARE NAMED HERE, not read off the tables: with only
    # the partition above, a table could be moved from an executed bucket into
    # the review-only one by editing a string, and the control-row test would
    # stop iterating it with the suite green — the demotion `EXEMPT` cannot
    # suffer because the sweep reports a stale exemption. A table joins or
    # leaves an executed bucket by editing this list too, which is a named
    # change, not a drift.
    assert set(_CONTROL_SERVES) == {
        "test_review_round_isolation:_TRANSFER_CONTROLS",
        "test_review_round_isolation:_ANOTHER_ROUND_CONTROLS",
        "test_round_isolation:_CAUGHT_PROGRAMS",
        "test_ambient_git:_GENERIC_DRIVER_PLANTS",
        "test_skillpack:_LENS_SOURCE_RESTATEMENTS",
    }, sorted(_CONTROL_SERVES)
    assert set(_SELF_PINNED_CONTROLS) == {
        "test_round_isolation:_CODE_FROM_DATA_PLANTS",
        "test_malformation_matrix:COVERAGE",
        "test_malformation_matrix:_MALFORMATION_SHAPES",
        "test_malformation_matrix:_MUST_DRIVE",
        "test_malformation_matrix:_MUST_EXIT",
        "test_malformation_matrix:_MUST_NAME",
        "test_self_cage:PROHIBITION_PLANTS",
        "test_self_cage:HANDED_OVER",
    }, sorted(_SELF_PINNED_CONTROLS)
    # …and the other direction, the way `EXEMPT` is held: a review-only row
    # that a test DOES pin is a stale claim. SAMPLED on one row per table,
    # for the reason the sibling test samples — the mechanism is what is in
    # question, and a full pass over `_SILENT_PROGRAMS`' 14 rows costs its
    # module's 1.2s each time. A witness red with the table intact is not
    # a witness, exactly as in `sweep`.
    for key in _REVIEW_PINNED_CONTROLS:
        module_name, table = _split(key)
        module = _module(module_name)
        original = getattr(module, table)
        runnable, _ = gm.callable_tests(module)
        row = gm.alternatives(original)[0]
        setattr(module, table, row.mutated)
        try:
            reddened = [t for t in runnable
                        if gm.run_test(module, t) is not None]
        finally:
            setattr(module, table, original)
        pinned_by = [t for t in reddened if gm.run_test(module, t) is None]
        assert not pinned_by, (
            f"{key} is recorded as pinned by review alone, but deleting "
            f"{row.label} takes {pinned_by} red — it is pinned by a test, so "
            "it belongs in _SELF_PINNED_CONTROLS with that witness named")


def _unswept_account(key: str, note: str) -> None:
    """The account an UNSWEPT row must carry, in ONE derivation.

    Two callers: the ledger loop over `REGISTRY`, and the
    synthetic-note test that witnesses each arm. Raises `AssertionError` with
    the row named, so the ledger loop reports exactly what it reported before.
    """
    assert ("outside the subject" in note or "Covered by" in note
            or "NO COVERAGE" in note), (
        f"{key} is UNSWEPT with no account of what covers it: {note}")
    if "NO COVERAGE" in note:
        assert "Measured" in note, (
            f"{key} declares NO COVERAGE and records no mutation run — "
            "an admitted gap is only worth more than a false claim when "
            f"it is a measured one: {note}")


def test_the_unswept_dispositions_are_witnessed():
    """Every arm of `_unswept_account` has a case that reddens it.

    Run only over the rows `REGISTRY` happens to hold, the `NO COVERAGE` arm
    has no witness: replacing its `if` with `if False:` leaves the whole block
    unexercised and `test_the_ledger_states_the_harness_reach` green — an
    assertion that cannot fail, catalogued in the file that exists to
    catalogue them.

    WHAT THIS DOES AND DOES NOT CLAIM, because the arm it witnesses is itself
    described as a prompt rather than a fence: this pins that the three
    dispositions are the three, and that `NO COVERAGE` without the token
    reddens. It does NOT make `"Measured" in note` a measurement check —
    "Measured nothing at all" still passes, deliberately and stated above.
    """
    ok = [("outside", "a quiet-hours window; outside the subject"),
          ("covered", "Covered by test_x_the_thing_holds"),
          ("measured", "NO COVERAGE. Measured: 6 of 6 members deletable")]
    for key, note in ok:
        _unswept_account(key, note)                       # raises nothing

    bad = [("no-account", "the wiki link extractor", "no account of what"),
           ("empty", "", "no account of what"),
           ("unmeasured", "NO COVERAGE. Never will be.", "records no mutation run")]
    for key, note, needle in bad:
        with pytest.raises(AssertionError) as caught:
            _unswept_account(key, note)
        assert needle in str(caught.value), (
            f"{key!r} reddened for the wrong reason: {caught.value}")
        assert key in str(caught.value), "the failure does not name the row"


def test_the_ledger_states_the_harness_reach():
    """The reach, counted rather than described — so the prose at the top of
    this file cannot drift away from what runs.

    A harness whose stated coverage is wider than its actual coverage is the
    family it exists to close, wearing a different hat.
    """
    by_kind: dict[str, int] = {}
    for kind, _ in REGISTRY.values():
        by_kind[kind] = by_kind.get(kind, 0) + 1
    swept = [k for k, (kind, _) in REGISTRY.items() if kind == SWEPT]
    modules = {_split(k)[0] for k in swept}
    # The swept SUBJECT, named so widening it is a deliberate edit here and not
    # a drift. A guard with its controls in sweeps in 0.0-2.1s, and `sweep`
    # also runs a baseline check on every witness it accepts, one test run per
    # distinct witness. That check is what keeps a widening honest: a test red
    # under the shim for a reason unrelated to the guard would otherwise pose
    # as the witness for alternatives nothing pins. MEASURE WITNESSES INSIDE A
    # PYTEST SESSION: a bare interpreter has no session fixtures, so it reports
    # session-dependent tests red. What is NOT swept, and why, is in each
    # UNSWEPT row's note.
    #
    # THE COUNT IS COUNTED, NOT COPIED: a floor below what the tree holds lets
    # SWEPT rows be reclassified with the ledger that calls itself "counted
    # rather than described" green.
    assert modules == {"test_docs", "test_review_round_isolation",
                       "test_round_isolation", "test_skillpack",
                       "test_config", "test_ambient_git",
                       "test_unknown_key_render",
                       "test_runnable_docs",
                       "test_git_repo_binding",
                       "test_self_cage", "test_init_proof",
                       # `toolchain` is the first NON-test
                       # module in the subject: a helper with no tests of its
                       # own, swept through the WITNESSES entry above, the same
                       # way `conftest` would be if it declared a swept guard.
                       "test_toolchain", "toolchain"}, sorted(modules)
    # Exact, not a floor: moving a guard in or out of the sweep is a
    # deliberate edit of this number.
    assert by_kind[SWEPT] == 48, (
        f"{by_kind[SWEPT]} SWEPT rows — a floor let twelve rows leave the "
        "sweep in silence once; a guard moved in or out of the sweep is a "
        "deliberate edit of this number, not a drift under it")
    assert by_kind[UNSWEPT] >= 1, (
        "no guard is declared out of reach, which would mean this harness "
        "claims to sweep every guard in the suite — it does not")
    # Every UNSWEPT entry says so in its note, so the gap is readable without
    # running anything. THREE dispositions, not two: a row may
    # be outside the subject, covered by a named guard, or honestly uncovered
    # — and the third had no spelling, so a row whose claimed coverage turned
    # out not to exist had nowhere to land but a false "Covered by". That is
    # worse than an admitted gap, because this file's whole contract is that
    # UNSWEPT rows are the gap listed one by one. `NO COVERAGE` is asked to
    # carry the measurement that establishes it — and the clause below PROMPTS
    # for one rather than fencing it: `"Measured" in note` is a substring test
    # over free prose, so a note that NEGATES having measured anything ("NO
    # COVERAGE. Measured nothing at all") satisfies it exactly as a recorded
    # run does, and only a note carrying the token nowhere reddens. That is no
    # weaker than the "Covered by" it joins — a "Covered by" can be false too —
    # but it is not the fence a reader would take "must carry" for, so it is
    # described as what it is.
    #
    # AND THE RULE IS WITNESSED IN THIS FILE, the sharper defect of the two:
    # run only over rows the tree happens to hold, the `NO COVERAGE` arm below
    # could take `if False:` in its place and leave this test GREEN — an unwitnessed assertion inside the ledger whose
    # subject is unwitnessed assertions. `_unswept_account` is the one
    # derivation and `test_the_unswept_dispositions_are_witnessed` drives
    # it over synthetic notes, so every arm has a case that reddens.
    #
    # WHAT IS STILL NOT PINNED, stated rather than implied: `by_kind[UNSWEPT]`
    # is a floor while `by_kind[SWEPT]` is exact, so rows can accumulate under
    # any UNSWEPT disposition without moving a pinned number.
    for key, (kind, note) in REGISTRY.items():
        if kind != UNSWEPT:
            continue
        _unswept_account(key, note)
    # The two files `REGISTRY` does not audit are the harness's OWN, and no
    # others. They are not unaudited: `SELF_REGISTRY` and
    # `test_the_harness_audits_its_own_two_files` are the COMPANION over
    # exactly this pair, which is what the exclusion costs and what pays for
    # it. A third file joining this tuple would leave that companion's subject
    # unchanged and hide from both ledgers at once.
    assert sorted(gm.SELF) == ["guard_mutation.py", "test_guard_mutations.py"], (
        "discovery's self-exclusion has grown past the harness's own two "
        "files, which is where a guard would hide from BOTH ledgers — REGISTRY "
        "skips it and SELF_REGISTRY has never heard of it")
    assert sum(len(v) for v in EXEMPT.values()) <= 3, (
        "the exemption list has grown past a handful — each entry is the "
        "harness taking an argument instead of a test, and a long list is a "
        "guard layer pinned by prose again")


# ══════════════════════════════════════════════════════════════════════════
# The harness's own seams
#
# Everything below is about `guard_mutation.py` and this file, the two the
# registry above is forbidden to audit.
# `test_the_harness_audits_its_own_two_files` answers that with a
# COMPANION ledger over SELF, not a second exemption.
# ══════════════════════════════════════════════════════════════════════════

# Every shape a module-level assignment's VALUE can carry that this suite
# spells today, with whether `_container_shaped` proves it a container from
# the SOURCE alone. Named here rather than derived from `_CONTAINER_CALLS` or
# from the function's own branches: a control derived from the table it pins
# shrinks with it and stays green.
#
# `_shape_probe_module` writes every one of these under `if False:`, so the
# binding never runs and there is no runtime value to classify — which is what
# makes this a control on the SOURCE half alone rather than on the union.
_SHAPE_PROBE: tuple[tuple[str, str, bool], ...] = (
    # ── the six bare builtin names of `_CONTAINER_CALLS`, one each ─────────
    ("CALL_TUPLE", "tuple(('a',))", True),
    ("CALL_LIST", "list(('a',))", True),
    ("CALL_SET", "set(('a',))", True),
    ("CALL_FROZENSET", "frozenset(('a',))", True),
    ("CALL_DICT", "dict(a=1)", True),
    ("CALL_SORTED", "sorted(('a',))", True),
    # ── shapes the literal/comprehension read already proved ───────────────
    ("LITERAL", "('a', 'b')", True),
    ("LISTLIT", "['a', 'b']", True),
    ("SETLIT", "{'a', 'b'}", True),
    ("DICTLIT", "{'a': 1}", True),
    ("LISTCOMP", "[_x for _x in ('a',)]", True),
    ("SETCOMP", "{_x for _x in ('a',)}", True),
    ("DICTCOMP", "{_x: 1 for _x in ('a',)}", True),
    # ── shapes proved through a binop, a helper, an if-expr or an alias ────
    ("BINOP", "LITERAL + ('c',)", True),
    ("HELPER_CALL", "_mk()", True),
    ("IFEXP", "LITERAL if _flag else BINOP", True),
    ("ALIAS", "LITERAL", True),
    # ── and the residual, which the docstring's bound names ────────────────
    ("ATTR_CALL", "collections.OrderedDict()", False),
    ("METHOD", "DICTLIT.copy()", False),
    ("SUBSCRIPT", "NESTED[0]", False),
    ("OPAQUE_HELPER", "_opaque()", False),
    ("SCALAR", "1", False),
    ("UNKNOWN_NAME", "_never_bound", False),
    # ── helper-arm shapes that must NOT be proved. Each would promote a
    #    scalar into the registry's subject, which is the harm the residual
    #    paragraph says the bound avoids.
    ("NESTED_HELPER", "_outer()", False),      # returns None; the LITERAL is
                                               # a nested def's, not `_outer`'s
    ("MAYBE_NONE", "_maybe()", False),         # one arm is a bare `return`
    ("COROUTINE", "_amk()", False),            # a coroutine, not a container
    ("SHADOW_LOCAL", "_shadow_local()", False),   # returns a helper LOCAL that
                                                  # merely spells `LITERAL`
    ("SHADOW_PARAM", "_shadow_param(1)", False),  # …and a PARAMETER that does
    # ── the same class, two constructs further ─────────────────────────────
    ("GENERATOR", "_gen()", False),        # a generator, whatever it returns
    ("FALLTHROUGH", "_fallthrough()", False),   # an exit that returns nothing
    ("MOD_FORMAT", "'%s and %s' % LITERAL", False),   # a str, not a container
    # ── the binop, if-expr and helper arms' qualifiers. `BINOP` proves
    #    through either operand and `IFEXP` requires both arms, so each side
    #    is its own row; the rest pin `+` alone, the recursion guard, `all`
    #    over a helper's returns, and `yield from`.
    ("BINOP_LEFT", "LITERAL + _never_bound", True),    # proved by its LEFT
    ("BINOP_RIGHT", "_never_bound + LITERAL", True),   # …and by its RIGHT
    ("BINOP_NON_ADD", "SETLIT - SETLIT", False),   # a set, but not by `+`:
                                                   # concatenation only
    ("IFEXP_BODY_ONLY", "LITERAL if _flag else _never_bound", False),
    ("IFEXP_ELSE_ONLY", "_never_bound if _flag else LITERAL", False),
    ("RECURSIVE_HELPER", "_recursive()", False),   # the recursion guard, or
                                                   # a RecursionError
    ("MIXED_RETURNS", "_mixed()", False),   # ONE return is a scalar: the
                                            # quantifier is `all`, not `any`
    ("GENERATOR_FROM", "_gen_from()", False),   # `yield from` is a generator
                                                # as surely as `yield`
)

# Multi-target bindings, which `_SHAPE_PROBE` cannot express: it names ONE
# name per row and unpacking is the question of what each name gets. Proving
# the whole value and applying that to every unpacked name would put the `3`
# of `VOCAB, LIMIT = ('a','b'), 3` into the registry's subject.
_UNPACK_PROBE: tuple[tuple[str, tuple[tuple[str, bool], ...]], ...] = (
    ("PAIR_VOCAB, PAIR_SCALAR = ('a', 'b'), 3",
     (("PAIR_VOCAB", True), ("PAIR_SCALAR", False))),
    ("BOTH_LEFT, BOTH_RIGHT = ('a',), ['b']",
     (("BOTH_LEFT", True), ("BOTH_RIGHT", True))),
    ("[SOLE_TARGET] = [('a', 'b')]", (("SOLE_TARGET", True),)),
    ("CHAIN_ONE = CHAIN_TWO = ('a', 'b')",
     (("CHAIN_ONE", True), ("CHAIN_TWO", True))),
    # An opaque right-hand side settles nothing about either element.
    ("OPAQUE_LEFT, OPAQUE_RIGHT = _opaque()",
     (("OPAQUE_LEFT", False), ("OPAQUE_RIGHT", False))),
    # ── `_paired`'s guards, one row per side. A SUBSCRIPT
    #    target beside a name: the non-sequence-target guard refuses it, and
    #    without that guard `_paired` reads `.elts` off a Subscript and dies.
    ("SUB_SINK[0], SUB_PEER = ('a',), ('b',)", (("SUB_PEER", True),)),
    # A star on the TARGET side: `*STAR_HEAD` swallows the first two values
    # and STAR_TAIL is the int, but the sides align positionally on nothing
    # the AST can see — without the target-side check `zip` pairs STAR_TAIL
    # with `('b',)` and proves the wrong name.
    ("*STAR_HEAD, STAR_TAIL = ('a',), ('b',), 3",
     (("STAR_HEAD", False), ("STAR_TAIL", False))),
    # A star on the VALUE side: STAR_TWO is whatever `_opaque()` yields
    # second, and without the value-side check `zip` pairs it with `('c',)`.
    ("STAR_ONE, STAR_TWO, STAR_THREE = *_opaque(), ('c',)",
     (("STAR_ONE", False), ("STAR_TWO", False), ("STAR_THREE", False))),
)

# One case per group extension `_GROUP_PREFIXES` models: the pattern, and the
# alternatives the enumerator must take out of it. Keyed by the prefix so the
# two tables can be compared in both directions — a prefix deleted from either
# side is named rather than silently shrinking a loop.
_GROUP_PREFIX_CASES: dict[str, tuple[str, list[str]]] = {
    # `?:` is the prefix table's alone: `_SCOPED_FLAGS` requires a flag letter
    # or a negation, so without the prefix entry this pattern is REFUSED
    # rather than re-read by the inline-flags arm.
    "?:": (r"(?:sux:a|b)",
           ["branch 'sux:a' of 'sux:a|b'", "branch 'b' of 'sux:a|b'"]),
    "?=": (r"(?=a|b)", ["branch 'a' of 'a|b'", "branch 'b' of 'a|b'"]),
    "?!": (r"(?!a|b)", ["branch 'a' of 'a|b'", "branch 'b' of 'a|b'"]),
    "?<=": (r"(?<=a|b)", ["branch 'a' of 'a|b'", "branch 'b' of 'a|b'"]),
    "?<!": (r"(?<!a|b)", ["branch 'a' of 'a|b'", "branch 'b' of 'a|b'"]),
    "?>": (r"(?>a|b)", ["branch 'a' of 'a|b'", "branch 'b' of 'a|b'"]),
}

# The OTHER arm, so the two stay disjoint in both directions: a scoped-flag
# group must still be read past, and it must be `_SCOPED_FLAGS` that does it
# rather than a prefix-table entry. Requiring a flag letter there is what makes
# `?:` above load-bearing, so these cases are the half that shows the arm keeps
# its own subject.
# ONE CASE PER ALTERNATIVE the enumerator can take out of `_SCOPED_FLAGS` —
# both branches and every member of its three character classes — so the
# companion's PINNED contract (every member deletable takes this test red) is
# meetable rather than a claim. `a` and `u` need their own cases because they
# cannot share a group with each other; `L` is not in the vocabulary at all,
# for the reason `_SCOPED_FLAGS` states.
_SCOPED_FLAG_CASES: dict[str, tuple[str, list[str]]] = {
    "?imsx:": (r"(?imsx:a|b)", ["branch 'a' of 'a|b'", "branch 'b' of 'a|b'"]),
    "?a:": (r"(?a:a|b)", ["branch 'a' of 'a|b'", "branch 'b' of 'a|b'"]),
    "?u:": (r"(?u:a|b)", ["branch 'a' of 'a|b'", "branch 'b' of 'a|b'"]),
    "?i-msx:": (r"(?i-msx:a|b)",
                ["branch 'a' of 'a|b'", "branch 'b' of 'a|b'"]),
    "?m-isx:": (r"(?m-isx:a|b)",
                ["branch 'a' of 'a|b'", "branch 'b' of 'a|b'"]),
    "?-imsx:": (r"(?-imsx:a|b)",
                ["branch 'a' of 'a|b'", "branch 'b' of 'a|b'"]),
}

# PINNED   every member's deletion must take the named witness RED.
# SAMPLED  the same claim, proved on ONE member because proving it on all of
#          them costs minutes rather than seconds — the bound the sibling
#          `test_a_control_row_is_pinned_through_the_guard_it_serves`
#          draws, and for the same reason: the mechanism is what is in
#          question. The row says what the cost is.
# LEDGER   this ledger, pinned inline by its own totality comparison.
# UNPINNED_SELF  a vocabulary of the harness that nothing can fail on, with
#          the reason. The honest gap, listed one by one.
PINNED, SAMPLED, LEDGER, UNPINNED_SELF = (
    "PINNED", "SAMPLED", "LEDGER", "UNPINNED_SELF")

# THE COMPANION. `SELF` keeps `guard_mutation.py` and this file out of
# `REGISTRY` — classifying the registry inside itself is circular — so without
# this ledger `_CONTAINER_CALLS`, the whole `ast.Call` branch and
# `_GROUP_PREFIXES` would have no test able to fail on them. The answer is not
# a second exemption one level up. It is this ledger: TOTAL over what discovery
# finds in those two files, each row naming the test that goes RED when a
# member is deleted, and that test is RUN rather than cited.
#
# Its bound, stated because a companion described more widely than it is would
# be the same defect a third level up: it uses the SAME discovery machinery it
# audits, so a discovery bug blinds both. What limits that is that discovery is
# itself driven by synthetic-directory controls — `discover(tests_dir=...)` over
# a fixture this file writes — which do not go through the `tests/` walk at all.
SELF_REGISTRY: dict[str, tuple[str, str, str]] = {
    "guard_mutation:SELF": (
        PINNED, "test_the_ledger_states_the_harness_reach",
        "the two files the harness does not audit; the ledger asserts the "
        "exclusion is exactly these and no third file can join it"),
    "guard_mutation:_CONTAINER_CALLS": (
        PINNED, "test_every_container_call_spelling_is_load_bearing",
        "the bare builtin names `_container_shaped` proves a container from "
        "the source; unpinned, every member was deletable, and the "
        "whole `ast.Call` branch with them, at 8 passed"),
    "guard_mutation:_GROUP_PREFIXES": (
        PINNED, "test_every_group_extension_prefix_is_modelled",
        "the regex group extensions the branch walker steps past; dropping "
        "any one of the five lookaround/atomic entries makes the enumerator "
        "REFUSE a pattern it used to take apart"),
    "guard_mutation:_SAME_SCOPE_BLOCKS": (
        PINNED, "test_discovery_sees_a_vocabulary_however_it_is_bound",
        "the compound-statement attributes that hold statements at the same "
        "scope; the fixture there carries one binding per member, `cases` "
        "included, so dropping any of the five is named"),
    "guard_mutation:_CONTAINER_LITERALS": (
        PINNED, "test_the_container_shaping_bound_is_pinned",
        "the expression nodes that ARE a container with nothing to prove; "
        "`_SHAPE_PROBE` carries one row per member, so dropping any of the "
        "seven leaves a shape the source read can no longer see"),
    "test_guard_mutations:REGISTRY": (
        SAMPLED, "test_every_module_level_vocabulary_in_the_suite_is_"
        "classified",
        "the disposition ledger itself — deleting a row leaves discovery "
        "finding a vocabulary that carries none, which is the totality claim. "
        "SAMPLED because it holds 140 rows and the witness re-walks every "
        "module in tests/: one deletion is a second, all of them is minutes"),
    "test_guard_mutations:SWEPT_ALTERNATIVES": (
        PINNED, "test_no_swept_alternative_can_leave_the_sweep_in_silence",
        "the exact alternative count of each SWEPT guard; a deleted row leaves "
        "a guard the table no longer pins, which the witness names as drift"),
    "test_guard_mutations:WITNESSES": (
        UNPINNED_SELF, "",
        "which modules' tests may witness a guard, EMPTY since no conftest "
        "guard is swept: an empty table has no member to delete, so no "
        "witness can go red on it. A conftest guard joining the sweep needs "
        "its row here and a test naming it again"),
    "test_guard_mutations:EXEMPT": (
        UNPINNED_SELF, "",
        "the knowingly-unpinned alternatives, EMPTY since the one guard it "
        "exempted a branch of was retired with the prose-pinning tests: an "
        "empty table has no member to delete, so no witness can go red on it. "
        "The sweep still reports a stale exemption the day a row joins"),
    "test_guard_mutations:_SHAPE_PROBE": (
        PINNED, "test_the_container_shaping_bound_is_pinned",
        "one row per expression shape a module-level assignment can carry, "
        "with the marker names asserted independently so a deleted row is "
        "named rather than quietly narrowing the probe"),
    "test_guard_mutations:_UNPACK_PROBE": (
        PINNED, "test_the_container_shaping_bound_is_pinned",
        "one row per multi-target binding shape, with the name each target is "
        "expected to bind — the question `_SHAPE_PROBE` cannot ask, since it "
        "names one name per row (round 2, R2-04)"),
    "test_guard_mutations:_GROUP_PREFIX_CASES": (
        PINNED, "test_every_group_extension_prefix_is_modelled",
        "one case per group extension; compared against `_GROUP_PREFIXES` in "
        "BOTH directions, so neither table can shrink without the other "
        "naming it"),
    "guard_mutation:_SCOPED_FLAGS": (
        PINNED, "test_every_group_extension_prefix_is_modelled",
        "the scoped inline-flag spellings the group walker reads past — the "
        "other arm of `_GROUP_PREFIXES`, and the one whose looseness made "
        "`?:` deletable with every enumeration unchanged (round 1, C04). "
        "`_SCOPED_FLAG_CASES` drives one pattern per alternative"),
    "test_guard_mutations:_SCOPED_FLAG_CASES": (
        PINNED, "test_every_group_extension_prefix_is_modelled",
        "one case per scoped-flag spelling the OTHER arm of the group walker "
        "reads, so tightening `_SCOPED_FLAGS` to make `?:` load-bearing is "
        "shown not to have cost that arm its own subject (round 1, C04)"),
    "test_guard_mutations:SELF_REGISTRY": (
        LEDGER, "test_the_harness_audits_its_own_two_files",
        "this ledger, pinned STRUCTURALLY by the `unclassified` assertion at "
        "the top of that test rather than by an executed mutation: the two "
        "totality assertions force `subject == set(SELF_REGISTRY)`, so a "
        "deleted row is named on the next run by set arithmetic and no "
        "mutation can leave the comparison silent. It cannot be its own "
        "witness without recursing, and round 1 (C03) measured the "
        "thinned-copy loop that used to claim otherwise as unfailable"),
    "test_guard_mutations:_CONTROL_SERVES": (
        PINNED, "test_every_control_table_is_in_exactly_one_bucket",
        "the reason this companion earns its place: `test_a_control_row_is_pinned_through_the_"
        "guard_it_serves` ITERATES this table, so deleting a row narrowed the "
        "loop and its assertions together and nothing reddened — the "
        "axis-shrink shape inside the harness that finds it. "
        "Pinned by the partition: a row deleted here is "
        "a CONTROL row of REGISTRY in no bucket, and the witness names it"),
    "test_guard_mutations:_SELF_PINNED_CONTROLS": (
        PINNED, "test_every_control_table_is_in_exactly_one_bucket",
        "the same shape one table over: the second half of that test iterates "
        "this one, so a deleted row removed its own assertion. Pinned by the "
        "same partition, and its five matrix rows arrived "
        "with the row-by-row measurement that put them here"),
    "test_guard_mutations:_REVIEW_PINNED_CONTROLS": (
        PINNED, "test_every_control_table_is_in_exactly_one_bucket",
        "the third bucket — control tables pinned by review alone, each with "
        "the measurement; the partition names a row deleted from here "
        "exactly as it names one deleted from the other two"),
}


def _shape_probe_module(tmp_path: Path) -> Path:
    """A synthetic module carrying every row of `_SHAPE_PROBE`.

    Every probe binding sits under `if False:`, so none of them EXECUTES: the
    module has no runtime value for any of these names, and whatever
    `_container_shaped` says about them it says from the source alone. The
    `AST_ONLY = ['unreachable']` probe in
    `test_an_empty_vocabulary_is_discovered_on_every_box` exercises the
    ast.List path and says nothing about the `ast.Call` path beside it.
    """
    body = ["import collections\n",
            "def _mk():\n    return ('h', 'i')\n",
            "def _opaque():\n    return object()\n",
            # A nested def's return is not the outer helper's, a bare
            # `return` is a `None` the proof has to count, and calling an
            # `async def` yields a coroutine rather than what it returns.
            "def _outer():\n"
            "    def _inner():\n        return ('a', 'b')\n"
            "    _inner()\n",
            "def _maybe(flag=True):\n"
            "    if flag:\n        return ('a', 'b')\n"
            "    return\n",
            "async def _amk():\n    return ('a', 'b')\n",
            # A helper's LOCAL and its PARAMETER are not the module-level
            # name they happen to spell.
            "def _shadow_local():\n"
            "    LITERAL = 1\n    return LITERAL\n",
            "def _shadow_param(LITERAL):\n    return LITERAL\n",
            # A generator returns a GENERATOR whatever its `return`
            # says, and an exit that falls off the end contributes no
            # `ast.Return` node for the implicit `None` it yields.
            "def _gen():\n    yield 1\n    return ('a', 'b')\n",
            "def _fallthrough(flag=False):\n"
            "    if flag:\n        return ('a', 'b')\n",
            # A helper that calls itself, one whose returns DISAGREE, and a
            # `yield from` generator.
            "def _recursive():\n    return _recursive()\n",
            "def _mixed(flag=True):\n"
            "    if flag:\n        return ('a', 'b')\n"
            "    return 1\n",
            "def _gen_from():\n    yield from ()\n    return ('a', 'b')\n",
            "if False:\n"]
    body += [f"    {line}\n" for line, _ in _UNPACK_PROBE]
    body += [f"    {name} = {expr}\n" for name, expr, _ in _SHAPE_PROBE]
    path = tmp_path / "test_shape_probe.py"
    path.write_text("".join(body))
    return path


def _discover_in(tmp_path: Path) -> set[str]:
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        return {c.name for c in gm.discover(tmp_path)}
    finally:
        sys.path.remove(str(tmp_path))
        for stem in [p.stem for p in tmp_path.glob("*.py")]:
            sys.modules.pop(stem, None)


def test_every_container_call_spelling_is_load_bearing(tmp_path):
    """The `ast.Call` branch of `_container_shaped`, and every member of
    `_CONTAINER_CALLS`, are load-bearing.

    `AST_ONLY = ['unreachable']` elsewhere is a LITERAL, which exercises the
    `ast.List` path and never reaches the call path at all, so the
    source-level recognition of `tuple(...)` spellings needs its own control.

    The six names are typed HERE, not read off `gm._CONTAINER_CALLS`: a
    control derived from the tuple it pins shrinks with it and stays green.
    """
    path = _shape_probe_module(tmp_path)
    shaped = gm._container_shaped(path)
    for builtin in ("tuple", "list", "set", "frozenset", "dict", "sorted"):
        name = f"CALL_{builtin.upper()}"
        assert name in shaped, (
            f"`{name} = {builtin}(...)` is not shaped from the source, so a "
            f"vocabulary spelled `{builtin}(...)` under a machine-dependent "
            "branch leaves the registry's subject on one box and rejoins it "
            f"on another — {sorted(shaped)}")
    assert sorted(gm._CONTAINER_CALLS) == sorted(
        ("tuple", "list", "set", "frozenset", "dict", "sorted")), (
        "`_CONTAINER_CALLS` gained or lost a spelling with no probe row "
        f"beside it: {gm._CONTAINER_CALLS}")
    # …and end to end, through `discover`, where the binding never executes so
    # only the source read can find it.
    found = _discover_in(tmp_path)
    for builtin in ("tuple", "list", "set", "frozenset", "dict", "sorted"):
        assert f"CALL_{builtin.upper()}" in found, (found, builtin)


def test_the_container_shaping_bound_is_pinned(tmp_path):
    """`_container_shaped`'s bound, stated AND executed.

    Beyond literals, comprehensions and the six bare builtin calls of
    `_CONTAINER_CALLS`, four shapes are proved (a helper whose every return is
    container-shaped, a binop with a container operand, an if-expr with two
    container arms, and a bare alias of a shaped name); the
    rest are the RESIDUAL, and this probe is what keeps the docstring's
    account of it honest in both directions: a shape that starts being proved,
    or stops being proved, is named here.

    Every marker name is asserted present INDEPENDENTLY of `_SHAPE_PROBE`'s
    own rows, the `_ADD_METHODS` shape — a probe whose expectation is derived
    from the probe shrinks with it.
    """
    path = _shape_probe_module(tmp_path)
    rows = {name: is_shaped for name, _, is_shaped in _SHAPE_PROBE}
    for marker in ("CALL_TUPLE", "CALL_LIST", "CALL_SET", "CALL_FROZENSET",
                   "CALL_DICT", "CALL_SORTED", "LITERAL", "LISTLIT", "SETLIT",
                   "DICTLIT", "LISTCOMP", "SETCOMP", "DICTCOMP",
                   "BINOP", "HELPER_CALL", "IFEXP", "ALIAS",
                   "ATTR_CALL", "METHOD", "SUBSCRIPT", "OPAQUE_HELPER",
                   "SCALAR", "UNKNOWN_NAME", "NESTED_HELPER", "MAYBE_NONE",
                   "COROUTINE", "SHADOW_LOCAL", "SHADOW_PARAM", "GENERATOR",
                   "FALLTHROUGH", "MOD_FORMAT",
                   "BINOP_LEFT", "BINOP_RIGHT", "BINOP_NON_ADD",
                   "IFEXP_BODY_ONLY", "IFEXP_ELSE_ONLY", "RECURSIVE_HELPER",
                   "MIXED_RETURNS", "GENERATOR_FROM"):
        assert marker in rows, (
            f"`_SHAPE_PROBE` no longer carries the {marker!r} shape, so this "
            "probe covers less than its own docstring claims")
    shaped = gm._container_shaped(path)
    proved = {name for name, is_shaped in rows.items() if is_shaped}
    residual = {name for name, is_shaped in rows.items() if not is_shaped}
    assert proved <= shaped, (
        "`_container_shaped` stopped proving "
        f"{sorted(proved - shaped)} — a vocabulary spelled that way is "
        "invisible to the source half again")
    assert not (residual & shaped), (
        "`_container_shaped` now proves "
        f"{sorted(residual & shaped)}, which the docstring's stated residual "
        "says it does not. Widen the bound in BOTH places or neither")
    # …and the UNPACKING half, which one name per row cannot express. Each
    # marker is named here rather than read off `_UNPACK_PROBE`, so a deleted
    # row is a finding rather than one fewer iteration.
    for marker in ("PAIR_VOCAB", "PAIR_SCALAR", "BOTH_LEFT", "BOTH_RIGHT",
                   "SOLE_TARGET", "CHAIN_ONE", "CHAIN_TWO", "OPAQUE_LEFT",
                   "OPAQUE_RIGHT", "SUB_PEER", "STAR_HEAD", "STAR_TAIL",
                   "STAR_ONE", "STAR_TWO", "STAR_THREE"):
        assert any(marker == name for _, pairs in _UNPACK_PROBE
                   for name, _ in pairs), (
            f"`_UNPACK_PROBE` no longer carries {marker!r}")
    for line, pairs in _UNPACK_PROBE:
        for name, expected in pairs:
            assert (name in shaped) is expected, (
                f"`{line}` — {name} is "
                f"{'not ' if expected else ''}proved a container and should "
                f"be the other way. Unpacking binds an ELEMENT to each name, "
                "so proving the whole value and marking every target puts "
                "whatever the other elements are into the registry's subject")


def test_every_group_extension_prefix_is_modelled():
    """Every member of `_GROUP_PREFIXES`, the table `_branch_levels` steps past
    to reach a group's body, is load-bearing.

    A test whose only group is `(?:...)` cannot pin the other prefixes, so
    this carries one case per prefix, compared to the table in BOTH
    directions so neither can shrink in silence, and each case ENUMERATED
    rather than restated.
    """
    for prefix in ("?:", "?=", "?!", "?<=", "?<!", "?>"):
        assert prefix in _GROUP_PREFIX_CASES, (
            f"no case for the {prefix!r} group extension")
    # The scoped-flag spellings, named here rather than read off the table:
    # one per alternative `_SCOPED_FLAGS` carries, so a deleted case is a
    # finding rather than one fewer iteration below.
    for spelling in ("?imsx:", "?a:", "?u:", "?i-msx:", "?m-isx:", "?-imsx:"):
        assert spelling in _SCOPED_FLAG_CASES, (
            f"no case for the {spelling!r} scoped-flag spelling — the "
            "alternative of `_SCOPED_FLAGS` it drives is unexercised")
    assert {p for p, _ in gm._GROUP_PREFIXES} == set(_GROUP_PREFIX_CASES), (
        f"`_GROUP_PREFIXES` models {sorted(p for p, _ in gm._GROUP_PREFIXES)} "
        f"and this control covers {sorted(_GROUP_PREFIX_CASES)} — one table "
        "moved without the other")
    for prefix, (pattern, expected) in sorted(
            list(_GROUP_PREFIX_CASES.items())
            + list(_SCOPED_FLAG_CASES.items())):
        labels = [a.label for a in gm.regex_alternatives(re.compile(pattern))]
        assert labels == expected, (
            f"with the {prefix!r} extension, {pattern!r} enumerates {labels} "
            f"rather than {expected} — the walker no longer steps past it, so "
            "every guard using that extension is enumerated as a different "
            "pattern or refused outright")
    # …and the two arms are DISJOINT. `?:` belongs to the prefix table: if
    # `_SCOPED_FLAGS` also matched it, the prefix entry could be deleted with
    # every enumeration byte-identical, and its row above could not fail.
    for prefix in _SCOPED_FLAG_CASES:
        assert re.match(gm._SCOPED_FLAGS, prefix), (
            f"`_SCOPED_FLAGS` no longer reads {prefix!r}, so a scoped-flag "
            "group is refused instead of descended")
    for prefix in _GROUP_PREFIX_CASES:
        assert not re.match(gm._SCOPED_FLAGS, prefix), (
            f"`_SCOPED_FLAGS` also matches {prefix!r}, which the prefix table "
            "owns — the two arms overlap again, so that prefix-table entry is "
            "deletable with the enumeration unchanged")


def test_a_vocabulary_bound_inside_a_match_case_is_discovered(tmp_path):
    """`_module_level` walks `ast.Match`.

    A `match` stores its bodies under `cases[].body`, not body / orelse /
    finalbody / handlers, so a walk over only those leaves `IN_MATCH`
    invisible to all three readers (`_assigned_names`, `discover`,
    `_container_shaped`). `IN_MATCH_CONTROL` and `IN_WHILE` are what such a
    walk still sees.
    """
    path = tmp_path / "test_match.py"
    path.write_text(
        "IN_MATCH_CONTROL = ('a', 'b')\n"
        "match ('x',):\n"
        "    case ('x',):\n"
        "        IN_MATCH = ('c', 'd')\n"
        "    case _:\n"
        "        IN_MATCH_ELSE = ('e', 'f')\n"
        "_n = 0\n"
        "while _n < 1:\n"
        "    IN_WHILE = ('g', 'h')\n"
        "    _n += 1\n")
    for name in ("IN_MATCH_CONTROL", "IN_MATCH", "IN_MATCH_ELSE", "IN_WHILE"):
        assert name in gm._assigned_names(path), (
            f"`_assigned_names` cannot see {name}: "
            f"{sorted(gm._assigned_names(path))}")
        assert name in gm._container_shaped(path), (
            f"`_container_shaped` cannot see {name}: "
            f"{sorted(gm._container_shaped(path))}")
    found = _discover_in(tmp_path)
    for name in ("IN_MATCH_CONTROL", "IN_MATCH", "IN_MATCH_ELSE", "IN_WHILE"):
        assert name in found, (
            f"discovery cannot see a vocabulary bound as {name}, so a guard "
            "spelled that way reaches the suite with no disposition while the "
            f"registry calls itself TOTAL: {sorted(found)}")


def test_a_function_local_container_cannot_promote_a_module_scalar(
        tmp_path):
    """A function-LOCAL container cannot promote a module-level scalar.

    An `ast.walk` over the whole tree would let a function-LOCAL `X = []`
    shape an unrelated module-level scalar `X = 1` into the registry's
    subject. `tests/` need carry no such collision for a measurement to land
    on, so the collision is built here instead. `COLLIDE` is a module-level
    scalar; a function below binds a list to the same name, and a class body
    does the same for `ALSO_COLLIDES`.
    """
    path = tmp_path / "test_scope_collision.py"
    path.write_text(
        "VOCAB = ('a', 'b')\n"
        "COLLIDE = 1\n"
        "def _helper():\n"
        "    COLLIDE = []\n"
        "    COLLIDE.append('x')\n"
        "    return COLLIDE\n"
        "class _Holder:\n"
        "    ALSO_COLLIDES = {}\n"
        "ALSO_COLLIDES = 2\n")
    shaped = gm._container_shaped(path)
    assert "COLLIDE" not in shaped, (
        "a function-LOCAL container shaped the module-level scalar of the "
        f"same name: {sorted(shaped)}")
    assert "ALSO_COLLIDES" not in shaped, (
        "a CLASS-body container shaped the module-level scalar of the same "
        f"name: {sorted(shaped)}")
    found = _discover_in(tmp_path)
    assert "VOCAB" in found, found
    assert "COLLIDE" not in found and "ALSO_COLLIDES" not in found, (
        "an unrelated module-level SCALAR was promoted into the registry's "
        f"subject by a same-named local: {sorted(found)}")


def test_a_vocabulary_compiled_through_a_helper_is_discovered(tmp_path):
    """A vocabulary compiled through a HELPER is discovered.

    `discover` admits a string only when the module feeds it to `re`.
    `_VOCAB_VIA_HELPER` sits beside a structurally identical `_VOCAB_DIRECT`,
    the first passed to `_mk(src) -> re.compile(src)` and the second to
    `re.compile`. Missing the helper one would leave it with no disposition
    while the totality test stays green: a silent skip rather than a
    recorded gap.
    """
    path = tmp_path / "test_helper_vocab.py"
    path.write_text(
        "import re\n"
        "def _mk(src):\n"
        "    return re.compile(src)\n"
        "_VOCAB_DIRECT = r'moving|renamed|copied'\n"
        "_VOCAB_VIA_HELPER = r'shifting|retitled|cloned'\n"
        "_NOT_A_PATTERN = 'plain text with no bar'\n"
        "GUARD_DIRECT = re.compile(_VOCAB_DIRECT)\n"
        "GUARD_VIA_HELPER = _mk(_VOCAB_VIA_HELPER)\n"
        "_ANN_SEP = r'\\s*'\n"
        "_ANN_JOIN = r'and|or'\n"
        "_ANN_GAP: str = _ANN_SEP + _ANN_JOIN\n"
        "GUARD_ANNOTATED = re.compile(_ANN_GAP)\n"
        "_TUP_SEP = r'\\s*'\n"
        "_TUP_JOIN = r'then|next'\n"
        "_TUP_GAP, _TUP_OTHER = _TUP_SEP + _TUP_JOIN, 1\n"
        "GUARD_TUPLE = re.compile(_TUP_GAP)\n"
        # Only the FIRST positional argument of an `re.*` call is the pattern.
        # Reading them all would make the HAYSTACK a regex source too, and turn
        # prose fixtures into registry candidates.
        "_PAT_ARG = r'sought|wanted'\n"
        "_HAY_ARG = 'a long line of ordinary prose with no alternation'\n"
        "def _find(pattern, haystack):\n"
        "    return re.search(pattern, haystack)\n"
        "GUARD_VIA_PAIR = _find(_PAT_ARG, _HAY_ARG)\n"
        "DIRECT_PAIR = re.search(_PAT_ARG, _HAY_ARG)\n"
        # ── the helper arms, one binding each. A KEYWORD argument reaches the
        # helper's regex parameter; a POSITIONAL-ONLY and a KEYWORD-ONLY parameter
        # are declared parameters too; a helper with `*rest` is called with
        # MORE positionals than it names; a parameter reaches `re` through
        # `.join` rather than an `re.*` call; a `.join` at a plain call site
        # is read by the seed directly; and a LOCAL that merely spells the
        # helper's name is not the helper.
        "_VOCAB_VIA_KEYWORD = r'shifted|retitled'\n"
        "GUARD_VIA_KEYWORD = _mk(src=_VOCAB_VIA_KEYWORD)\n"
        "def _mk_posonly(src, /):\n    return re.compile(src)\n"
        "def _mk_kwonly(*, src):\n    return re.compile(src)\n"
        "_VOCAB_POSONLY = r'lifted|hoisted'\n"
        "_VOCAB_KWONLY = r'lowered|dropped'\n"
        "GUARD_POSONLY = _mk_posonly(_VOCAB_POSONLY)\n"
        "GUARD_KWONLY = _mk_kwonly(src=_VOCAB_KWONLY)\n"
        "def _mk_var(src, *rest):\n    return re.compile(str(src))\n"
        "_VOCAB_VARARG = r'spun|turned'\n"
        "GUARD_VARARG = _mk_var(_VOCAB_VARARG, 1, 2)\n"
        "def _glue(sep, parts):\n    return sep.join(parts)\n"
        "_HELPER_SEP = r'and|or'\n"
        "GLUED = _glue(_HELPER_SEP, ('a', 'b'))\n"
        "_SEP_GLUE = r'then|next'\n"
        "def test_glue():\n    assert _SEP_GLUE.join(['a', 'b'])\n"
        "_SHADOWED_HELPER_ARG = r'plain|prose'\n"
        "def test_shadowed_helper(_mk=str):\n"
        "    assert _mk(_SHADOWED_HELPER_ARG)\n")
    sources = gm._regex_source_names(path)
    for name in ("_VOCAB_VIA_KEYWORD", "_VOCAB_POSONLY", "_VOCAB_KWONLY",
                 "_VOCAB_VARARG", "_HELPER_SEP", "_SEP_GLUE"):
        assert name in sources, (
            f"{name} reaches `re` through a helper arm the reader no longer "
            f"has: {sorted(sources)}")
    assert "_SHADOWED_HELPER_ARG" not in sources, (
        "a local that merely spells the helper's name was read as the "
        f"helper, so its argument is a regex source: {sorted(sources)}")
    assert "_VOCAB_VIA_HELPER" in sources, (
        "a vocabulary reaching `re` through a one-hop helper is not read as a "
        f"regex source: {sorted(sources)}")
    found = _discover_in(tmp_path)
    assert "_VOCAB_DIRECT" in found, found
    assert "_VOCAB_VIA_HELPER" in found, (
        "the helper-bound vocabulary reaches the suite with no disposition "
        f"while its direct twin beside it is found: {sorted(found)}")
    # …and the PARTS a source is built from propagate however the source
    # itself is bound: an annotated or tuple-target binding must propagate
    # too, or its parts reach the suite undeclared while the totality test
    # stays green.
    for name in ("_ANN_SEP", "_ANN_JOIN", "_TUP_SEP", "_TUP_JOIN"):
        assert name in sources, (
            f"{name} does not propagate through the binding that consumes it: "
            f"{sorted(sources)}")
    assert "_PAT_ARG" in sources, (
        "the PATTERN argument of a one-hop helper is not read as a regex "
        f"source: {sorted(sources)}")
    assert "_HAY_ARG" not in sources, (
        "the SUBJECT an `re.*` call searches is read as a regex source, so "
        "any prose fixture passed to one earns a registry row it did not "
        f"deserve: {sorted(sources)}")


def test_a_regex_source_is_a_module_name_not_a_local_collision(tmp_path):
    """`_regex_source_names` returns module-level names, never function LOCALS.

    If a local passed to `re.search` in one test counted, every OTHER test
    binding a local of that name would propagate whatever it was built from,
    and fixtures would become registry candidates by that collision alone
    while structurally identical fixtures beside them stayed out.

    The seed may still be found anywhere — a `GLUE.join(...)` at a call site
    is how a separator vocabulary joined into a pattern is reached — but the
    PROPAGATION runs over module-level assignments only, and what comes back
    is module-level names only.
    """
    path = tmp_path / "test_collision.py"
    path.write_text(
        "import re\n"
        "PATTERN = r'moving|renamed'\n"
        "FIXTURE = '#!/bin/sh\\ncase \"$1\" in a|b) exit 0 ;; esac\\n'\n"
        # SAME SPELLING as the module-level fixture, and the local is what
        # reaches `re`. Three ways of binding it locally, since
        # `_locally_bound` enumerates target kinds rather than reading `Store`
        # context.
        #
        # THE COLLIDING NAME SITS AT ARGUMENT 0, the PATTERN position, and
        # that is load-bearing rather than stylistic: the seed reads only
        # argument 0, so a colliding name in the SUBJECT position could not
        # reach `used` by any route, both negative assertions below would be
        # vacuous, and the whole `_locally_bound` shadowing subsystem could be
        # deleted with the full suite green.
        "def test_one():\n"
        "    FIXTURE = r'moving|renamed'\n"
        "    assert re.search(FIXTURE, 'moving')\n"
        "def test_two(FIXTURE=r'moving|renamed'):\n"
        "    assert re.fullmatch(FIXTURE, 'moving')\n"
        "def test_three():\n"
        "    for FIXTURE in (r'moving|renamed',):\n"
        "        assert re.match(FIXTURE, 'moving')\n"
        # …and a name a function DECLARES global is NOT shadowed, so the seed
        # still sees it. `_locally_bound` honours that, and a reader who
        # deleted the `freed` subtraction would lose this candidate.
        "GLOBAL_VOCAB = r'held|kept'\n"
        "def test_four():\n"
        "    global GLOBAL_VOCAB\n"
        # The ASSIGNMENT is what makes this control able to fail. Declaring
        # `global` and only READING the name never puts it in `_locally_bound`'s
        # `bound` at all, so `bound - freed` would be a no-op there.
        "    GLOBAL_VOCAB = GLOBAL_VOCAB or r'held|kept'\n"
        "    assert re.search(GLOBAL_VOCAB, 'held')\n"
        # …and a comprehension target is its OWN scope: it shadows INSIDE the
        # comprehension and binds nothing in the function around it, so the
        # same name used beside it is still the module-level one and still a
        # seed. `_locally_bound` enumerates target kinds for exactly this — a
        # blanket `Store` read would have shadowed it for the whole function.
        "COMP_VOCAB = r'looped|spun'\n"
        "def test_five():\n"
        "    assert [str(COMP_VOCAB) for COMP_VOCAB in ('x',)]\n"
        "    assert re.search(COMP_VOCAB, 'looped')\n"
        # A CLASS BODY is a scope and binds names, and a `match` CASE
        # CAPTURE binds one — each must shadow, or the fixture is a regex
        # source by spelling collision.
        "class TestInClass:\n"
        "    FIXTURE = r'moving|renamed'\n"
        "    MATCHED = re.search(FIXTURE, 'moving')\n"
        "def test_six(value=(r'moving|renamed',)):\n"
        "    match value:\n"
        "        case (FIXTURE,):\n"
        "            assert re.search(FIXTURE, 'moving')\n"
        # …and a METHOD does NOT see its class's attributes: a bare name there
        # is the module global, so a class attribute must not shadow inside
        # one. `METHOD_VOCAB` is both, and the method's use is the module's.
        "METHOD_VOCAB = r'called|invoked'\n"
        "class TestMethodScope:\n"
        "    METHOD_VOCAB = 'shadow'\n"
        "    def test_seven(self):\n"
        "        assert re.search(METHOD_VOCAB, 'called')\n"
        "def test_eight():\n"
        "    assert re.search(PATTERN, 'moving')\n"
        # ── one binding construct per arm of `_locally_bound` and per member
        # of the seed's scope tuples, each colliding with `FIXTURE` at
        # argument 0. `tests/` need bind nothing these ways beside a regex
        # read, so each arm gets its control here.
        # Parameters, one per kind:
        "def test_posonly(FIXTURE=r'moving|renamed', /):\n"
        "    assert re.search(FIXTURE, 'moving')\n"
        "def test_kwonly(*, FIXTURE=r'moving|renamed'):\n"
        "    assert re.search(FIXTURE, 'moving')\n"
        "def test_vararg(*FIXTURE):\n"
        "    assert re.search(FIXTURE[0], 'moving')\n"
        "def test_kwarg(**FIXTURE):\n"
        "    assert re.search(FIXTURE['p'], 'moving')\n"
        # A lambda's parameter shadows inside the lambda:
        "def test_lambda_param():\n"
        "    assert (lambda FIXTURE: re.search(FIXTURE, 'moving'))(1)\n"
        # AnnAssign, AugAssign, NamedExpr:
        "def test_annotated():\n"
        "    FIXTURE: str = r'moving|renamed'\n"
        "    assert re.search(FIXTURE, 'moving')\n"
        "def test_augmented():\n"
        "    FIXTURE += 'x'\n"
        "    assert re.search(FIXTURE, 'moving')\n"
        "def test_walrus():\n"
        "    if (FIXTURE := r'moving|renamed'):\n"
        "        assert re.search(FIXTURE, 'moving')\n"
        # except-as, with-as, import-as, match star, match mapping rest:
        "def test_except_as():\n"
        "    try:\n        pass\n"
        "    except Exception as FIXTURE:\n"
        "        assert re.search(FIXTURE, 'moving')\n"
        "def test_with_as():\n"
        "    with open(__file__) as FIXTURE:\n"
        "        assert re.search(FIXTURE, 'moving')\n"
        "def test_import_as():\n"
        "    import re as FIXTURE\n"
        "    assert re.search(FIXTURE, 'moving')\n"
        "def test_match_star(value=(r'moving|renamed',)):\n"
        "    match value:\n"
        "        case [*FIXTURE]:\n"
        "            assert re.search(FIXTURE[0], 'moving')\n"
        "def test_match_rest(value={}):\n"
        "    match value:\n"
        "        case {**FIXTURE}:\n"
        "            assert re.search(FIXTURE['k'], 'moving')\n"
        # An `async def` is a function scope; `async for` binds its target:
        "async def test_async_def():\n"
        "    FIXTURE = r'moving|renamed'\n"
        "    assert re.search(FIXTURE, 'moving')\n"
        "async def test_async_for():\n"
        "    async for FIXTURE in _aiter():\n"
        "        assert re.search(FIXTURE, 'moving')\n"
        # The four comprehension kinds, each reading `re` INSIDE its scope:
        "def test_listcomp():\n"
        "    assert [re.search(FIXTURE, 'x') for FIXTURE in (r'a|b',)]\n"
        "def test_setcomp():\n"
        "    assert {re.search(FIXTURE, 'x') for FIXTURE in (r'a|b',)}\n"
        "def test_dictcomp():\n"
        "    assert {FIXTURE: re.search(FIXTURE, 'x') for FIXTURE in (r'a|b',)}\n"
        "def test_genexp():\n"
        "    assert any(re.search(FIXTURE, 'x') for FIXTURE in (r'a|b',))\n"
        # A nested def, async def or class binds its NAME in the enclosing
        # function, and a dotted import binds its FIRST component:
        "NESTED_DEF_VOCAB = r'held|kept'\n"
        "NESTED_ASYNC_VOCAB = r'held|kept'\n"
        "NESTED_CLASS_VOCAB = r'held|kept'\n"
        "DOTTED_VOCAB = r'held|kept'\n"
        "def test_nested_def():\n"
        "    def NESTED_DEF_VOCAB():\n        pass\n"
        "    assert re.search(NESTED_DEF_VOCAB, 'held')\n"
        "def test_nested_async():\n"
        "    async def NESTED_ASYNC_VOCAB():\n        pass\n"
        "    assert re.search(NESTED_ASYNC_VOCAB, 'held')\n"
        "def test_nested_class():\n"
        "    class NESTED_CLASS_VOCAB:\n        pass\n"
        "    assert re.search(NESTED_CLASS_VOCAB, 'held')\n"
        "def test_dotted_import():\n"
        "    import DOTTED_VOCAB.child\n"
        "    assert re.search(DOTTED_VOCAB, 'held')\n"
        # …while a nested def's BODY and a lambda's body bind nothing in the
        # function around them, so these two stay the module's:
        "INNER_VOCAB = r'held|kept'\n"
        "LAMBDA_VOCAB = r'held|kept'\n"
        "def test_inner_body():\n"
        "    def _inner():\n        INNER_VOCAB = 'shadow'\n"
        "    assert re.search(INNER_VOCAB, 'held')\n"
        "def test_lambda_body():\n"
        "    _f = lambda: (LAMBDA_VOCAB := 'shadow')\n"
        "    assert re.search(LAMBDA_VOCAB, 'held')\n")
    sources = gm._regex_source_names(path)
    for name in ("NESTED_DEF_VOCAB", "NESTED_ASYNC_VOCAB",
                 "NESTED_CLASS_VOCAB", "DOTTED_VOCAB"):
        assert name not in sources, (
            f"{name} is bound by a nested definition or a dotted import "
            "inside the only function that reads it, and the reader no "
            f"longer sees that binding: {sorted(sources)}")
    for name in ("INNER_VOCAB", "LAMBDA_VOCAB"):
        assert name in sources, (
            f"{name} is bound only inside a nested def's or a lambda's own "
            "body, which is a different scope — the reader now walks into "
            f"one of those: {sorted(sources)}")
    assert "PATTERN" in sources, (
        f"a module-level pattern fed to `re` inside a test is no longer read "
        f"as a regex source: {sorted(sources)}")
    # No assertion restates the `& _assigned_names(path)` at the end of
    # `_regex_source_names`: it would restate the implementation's own final
    # intersection, so it could not fail. Nor would `discover` notice the `&`
    # removed, since it reads `sources` only inside a loop over
    # `_assigned_names`. The `&` is
    # what makes the function's own contract ("module-level names") true for
    # any caller, and the only backstop if the seed's shadowing is ever
    # narrowed; what is asserted below is the BEHAVIOUR: which names come
    # back, and which do not.
    assert "FIXTURE" not in sources, (
        "a shell-script fixture became a regex source through a function-local "
        f"name collision — the row it earns is accidental: {sorted(sources)}")
    # …and the negative half is NOT vacuous: every colliding binding above
    # reaches `re` at argument 0, so the ONLY thing keeping `FIXTURE` out is
    # `_locally_bound` recognising the construct that binds it. Deleting any
    # one of those arms puts it straight back in. A re-parse comparison such
    # as `gm._regex_source_names(path) == sources` is `f(x) == f(x)` and adds
    # nothing.
    for name in ("GLOBAL_VOCAB", "COMP_VOCAB", "METHOD_VOCAB"):
        assert name in sources, (
            f"{name} is shadowed by a binding that is not this function's — a "
            "`global` declaration and a comprehension target bind nothing in "
            "the enclosing scope, and a seed dropped for one of those is a "
            f"vocabulary with no disposition: {sorted(sources)}")
    found = _discover_in(tmp_path)
    assert "FIXTURE" not in found, (
        f"the fixture is a registry candidate by collision: {sorted(found)}")
    assert {"PATTERN", "GLOBAL_VOCAB", "COMP_VOCAB",
            "METHOD_VOCAB"} <= found, sorted(found)


def test_returns_a_value_refuses_a_valueless_exit_on_its_own():
    """`_returns_a_value`'s valueless-exit clause, held directly.

    It cannot be pinned through `_container_shaped`: the quantifier there
    asks `_proves` of the exit's `None` value and gets `False` whether or not
    the helper was admitted. Two guards that cover for each other are each
    deletable alone; this one is the function's own contract ("does calling
    it yield what its returns say"), so it is held here directly, where a
    deleted clause has no second guard to hide behind.
    """
    early, valued = ast.parse(
        "def early_none(flag):\n"
        "    if flag:\n        return\n"
        "    return ('a', 'b')\n"
        "def all_valued(flag):\n"
        "    if flag:\n        return ('c',)\n"
        "    return ('a', 'b')\n").body
    assert gm._returns_a_value(valued)
    assert not gm._returns_a_value(early), (
        "a helper with a bare `return` on one path is proved to return what "
        "its other return says — the valueless exit is not being counted")


def test_locally_bound_honours_nonlocal_and_never_binds_a_nameless_form():
    """Four of `_locally_bound`'s guards are UNOBSERVABLE through the seed, so
    they are held here directly: `ast.Nonlocal` in the freed tuple, because a
    nonlocal name is by definition an enclosing function's local and already
    in `shadowed`; and the three `and node.name`-style guards on `except`,
    `case` captures and mapping rests, because they keep `None` out of a set
    of names and `None` collides with no name. They are this function's own
    contract: a `set[str]` that carries `None` is a wrong type waiting for a
    reader that iterates it. `with` items carry no such guard: `_bound_names`
    already binds nothing for a `None` target, so one there would have no
    observable effect — and a guard with no observable effect is not a
    bound.
    """
    outer, nameless = ast.parse(
        "def outer():\n"
        "    X = 1\n"
        "    def inner():\n"
        "        nonlocal X\n"
        "        X = 2\n"
        "        Y = 3\n"
        "    return inner\n"
        "def nameless(value):\n"
        "    try:\n        pass\n"
        "    except Exception:\n        pass\n"
        "    with value:\n        pass\n"
        "    match value:\n"
        "        case [*_]:\n            pass\n"
        "        case {'k': 1}:\n            pass\n"
        "        case _:\n            pass\n").body
    inner = outer.body[1]
    assert gm._locally_bound(inner) == {"Y"}, (
        "a `nonlocal` name is the ENCLOSING function's binding, not this "
        f"one's: {gm._locally_bound(inner)}")
    assert gm._locally_bound(nameless) == {"value"}, (
        "a nameless `except`, `with`, `case [*_]`, mapping pattern or `case "
        f"_` bound something: {gm._locally_bound(nameless)}")


def test_the_harness_audits_its_own_two_files():
    """THE COMPANION, and the decision behind it.

    `SELF` keeps this file and `guard_mutation.py` out of `REGISTRY`, and the
    reason is sound — `REGISTRY` is a mapping of strings, exactly the shape
    discovery looks for, so classifying it inside itself is circular. The
    consequence is that the harness's own module-level vocabularies need a
    ledger of their own, because the harness cannot find an unpinned one in
    itself through REGISTRY. A second exemption would repeat the mistake one
    level up; this ledger is the other answer.

    TOTAL over what discovery finds in those two files, both directions, and
    every executed row's witness is RUN with a member deleted rather than
    cited — EVERY member for a PINNED row, one for a SAMPLED one. The LEDGER
    row is the one exception and it says why in its own note: a test cannot be
    its own witness, so what pins it is the `unclassified` assertion below,
    structurally.
    """
    subject = {c.key for c in gm.discover(exclude=())
               if c.module in ("guard_mutation", "test_guard_mutations")}
    assert subject, "the companion found nothing at all, so it is vacuous"
    unclassified = sorted(subject - set(SELF_REGISTRY))
    assert not unclassified, (
        f"{len(unclassified)} module-level vocabular(y/ies) in the HARNESS "
        f"itself carry no disposition: {unclassified}. The auditor cannot "
        "audit itself through REGISTRY, which is why this ledger exists — "
        "classify each and name the test that goes red when a member is "
        "deleted")
    stale = sorted(set(SELF_REGISTRY) - subject)
    assert not stale, (
        f"SELF_REGISTRY names {stale}, which discovery no longer finds in the "
        "harness's own files")
    here = _module("test_guard_mutations")
    for key, (kind, witness, note) in sorted(SELF_REGISTRY.items()):
        assert kind in (PINNED, SAMPLED, LEDGER, UNPINNED_SELF), (key, kind)
        assert note and len(note) > 20, (key, note)
        if kind is UNPINNED_SELF:
            assert not witness, (
                f"{key} is recorded UNPINNED_SELF yet names a witness — if a "
                "test can fail on it, it is not the gap it says it is")
            continue
        if kind is LEDGER:
            assert witness == "test_the_harness_audits_its_own_two_files", (
                f"{key} is LEDGER but names {witness!r}; the only row that may "
                "be LEDGER is the one this test cannot run without recursing")
            continue
        assert hasattr(here, witness), (
            f"{key} names a witness that is gone: {witness}")
        module_name, name = _split(key)
        module = _module(module_name)
        original = getattr(module, name)
        # THE ENGINE'S OWN ENUMERATOR, not a hand-rolled member walk. A regex
        # SOURCE STRING's deletable alternatives are its branches and
        # character-class members, and `list(a_string)` would have handed the
        # mutation loop its characters — so the companion asks `alternatives`
        # exactly what the sweep next door asks it.
        as_source = isinstance(original, str)
        members = [(alt.label,
                    alt.mutated.pattern if as_source else alt.mutated)
                   for alt in gm.alternatives(
                       re.compile(original) if as_source else original)]
        assert members, f"{key} has nothing to delete"
        survived = []
        for index in range(len(members)):
            label, mutated = members[index]
            setattr(module, name, mutated)
            try:
                reddened = gm.run_test(here, witness)
            finally:
                setattr(module, name, original)
            if kind is SAMPLED:
                if reddened:
                    survived = []
                    break
                survived = [label]
                break
            if not reddened:
                survived.append(label)
        assert not survived, (
            f"{key}: {survived!r} can be deleted with {witness} still GREEN, "
            "so the harness's own vocabulary is pinned by nothing there — "
            "exactly the property this module exists to refuse, one level up. "
            "Add a control that exercises the member, or record the row "
            "UNPINNED_SELF with the measurement and a bead")
    # SELF_REGISTRY itself is pinned by the `unclassified` assertion at the
    # top of this test, and by nothing else here. That is a STRUCTURAL pin,
    # not an executed one: `unclassified` empty and `stale` empty together
    # force `subject == set(SELF_REGISTRY)`, so deleting any row makes
    # `unclassified` name it on the next run — there is no mutation that
    # leaves the comparison silent, and no witness this test could run
    # without recursing into itself.
    #
    # A "thinned copy" loop adds nothing here: given the two assertions above,
    # `subject - (SELF_REGISTRY - {victim})` is `{victim}` by set arithmetic
    # for every victim, so such a loop cannot fail.


def test_the_shim_builds_tmp_path_resolved_the_way_pytest_does(
        tmp_path, monkeypatch):
    """The shim builds `tmp_path` resolved, as pytest's own fixture is. On
    macOS TMPDIR is `/var/...`, a symlink to `/private/var/...`, so with an
    unresolved path two test_config tests that compare a resolved path
    against `tmp_path` are red under the shim and green under pytest — and
    green again on a Linux runner. A box-dependent shim loses witnesses on
    one box and not the other.

    Box-independent here: the temp root is pointed at a symlink on purpose,
    so this reddens on any machine whose shim returns the unresolved path.
    """
    real = tmp_path / "real-temp"
    real.mkdir()
    link = tmp_path / "linked-temp"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(tempfile, "tempdir", str(link))

    stack: list = []
    try:
        built = gm._resolve(sys.modules[__name__], "tmp_path", stack)
        assert built == built.resolve(), (
            f"the shim's tmp_path {built} is not resolved — pytest's is, so a "
            "test comparing resolved paths reads differently under the shim")
        assert built.parent == real.resolve(), built
    finally:
        for undo in reversed(stack):
            undo()

    import test_config
    for name in ("test_find_repo_root_still_walks_past_a_dangling_symlink",
                 "test_find_repo_root_still_walks_up_when_start_is_a_file"):
        assert gm.run_test(test_config, name) is None, (
            f"test_config::{name} is red under the shim with a symlinked temp "
            "root, and green under pytest — the witness the shim loses")
