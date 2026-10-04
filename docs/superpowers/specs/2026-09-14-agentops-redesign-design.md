# Nightgate redesign: all five products working, simpler, adoptable

Approved by the founder on 2026-09-14, section by section. This is the design; the implementation plan follows it. The tracking epic in beads carries the same content.

## Goal

An invited collaborator, with read access to this private repository, enrolls their own repository from the README alone and gets all five products working: the warden gate, the skill pack, memory with the retro loop, the graph layer, and the cage. The platform keeps the whole pipeline, including its own review crew, and stops generating most of its own backlog.

## Decisions

| Question | Decision |
|---|---|
| How much of the pipeline Nightgate owns | The whole pipeline: gate, review crew, cross-examiner, repair loop, cage, ship |
| Prose and guard layer | Cut hard |
| Graph layer | It declares the review crew, and the skills and attest follow it |
| Install | One command plus `warden init` |
| Visibility | Private and invite-only. No public package and no launch; `agentops` and `warden` are both taken on PyPI, and public disclosure waits on the patentability decision |
| Execution | Sequenced sub-projects on main, de-churn first |

## Evidence the design rests on

Measured on 2026-09-14 at main 9d28721 unless a row says otherwise.

| Measurement | Value |
|---|---|
| Bug beads found by reviewing this repo's own PRs | about 130 of 169, by a keyword pass over bead descriptions |
| Bug beads found on a consumer | about 10 |
| Review findings on runtime code | 33% |
| Comment and docstring lines per code line in warden/ | about 0.7 |
| Test functions that read markdown | 398 of 2,163 |
| Of those, tests that read a document and assert on it | 201 |
| Of those, behaviour tests that write markdown fixtures | depends on the counting method: about 130 when every function that writes a `.md` file counts, 41 by a narrower method |
| AI-judged rules at MEDIUM | 10 of 11 |
| Gate blocking severities | HIGH only |
| Cage runs in the ledger, all on a consumer checkout | 7, and none opened a PR: the 1 marked done found no eligible bead; of the 3 marked usage-limit, 2 stopped when the permission profile denied `git push` with attested work waiting and 1 ended when the laptop slept; the two real session-limit stops, on 2026-08-20 and 2026-08-24, were recorded as plain failures |
| Rules promoted through the full loop: bar cleared, retro proposal, founder merge | 2: tests-required, into its checker; and fail-closed, 39 judged with a 0.831 floor, proposed by the 2026-08-26 retro and merged in PR #95 |
| Promotion artifacts with backtests in total | 9; five name a retro in their notes, and the rest were written with the rule change |
| Machine adoptions through the autonomy ladder | 0 |
| Last retro | 2026-08-29 |

The gate blocks HIGH only. The churn comes from the repair loop: `warden round classify` counts a round for any open finding above LOW, and nearly every AI-judged rule is MEDIUM, including the rules that judge wording.

## Sub-project 1: de-churn

1. **Only behaviour findings loop.** Every AI-judged rule declares `judges: behaviour` or `judges: wording` in its frontmatter. `warden round classify` counts a round only for open behaviour findings. Wording findings never block and never count; they are fixed in the same PR when trivial, otherwise filed.
2. **One review round by default.** The code-reviewer runs with the charter lenses inside its brief as checklists, then the cross-examiner. No lens is retired; each still runs, inside one dispatch. A second round runs only when a round-one behaviour finding was repaired, and only over that repair. The hard cap is two rounds. This supersedes the founder ruling that raised the cap from two to three, the 2026-09-10 review-depth ruling that the round budget is not cut, and the 2026-09-14 withdrawal that left those rulings standing; the task's decision shard names all three.
3. **Comments say what the code does.** Incident history and bead ids leave `warden/`, `cage/` and `tests/`; the history lives in beads and git. One mechanical PR per module area. Its proof is that the Python AST with docstrings removed is identical before and after. Tests that read a docstring at runtime are updated in the same PR, so the full suite runs on each.
4. **Retire prose pins.** Remove test functions that read documentation or skill files and assert their wording. Tests that write markdown fixtures are behaviour tests and stay. Keep the tests that protect a consumer-copied snippet, a CLI output format or a schema a machine parses. Documentation gets one replacement check: its code blocks and commands run.
5. **Founder ruling.** The repository rule against weakening tests is overridden for prose-pinning tests only, recorded as a decision shard so the retro does not read the removal as drift.

De-churn edits skill text only where these rules live: the round cap and review default in deliver and pre-pr-review, and the never-weaken sentence in deliver and autonomous-run. deliver already lets a policy set a lower cap, so the cap itself can land in the policy file. The rest of the skill prose is rewritten in sub-project 3.

## Sub-project 2: one-command install

1. A collaborator with read access installs a tagged release with `uv tool install git+https://github.com/NightWatchEng/agentops@<tag>`. No clone of the platform.
2. `warden init` enrolls an existing repository: `repo.yaml` with verify commands detected from `pyproject.toml`, `package.json` or `go.mod`; starter rules from the catalog; a CI workflow pinned to the installed version; a minimal skills policy; and gitignore entries. It refuses to overwrite an existing file.
3. The skill pack installs from the repository's marketplace URL, not a local path.
4. `repo.yaml`'s `platform.pin` is the single version source. The per-repo shim remains only until shortfall and distill migrate.
5. CI runs `warden init` on fresh Python and Node repositories and requires certification Level 2.
6. The README states the access requirement, then "Try it" as three commands run on your own repository.

## Sub-project 3: the graph declares the crew

1. `graph.yaml` gains a `review` block: the crew for each round, the round cap, the charter lenses as checklists, and the node holding merge authority.
2. `warden graph crew --round N` prints the resolved crew as JSON.
3. pre-pr-review and deliver dispatch exactly that crew. The crew prose leaves the skills and `.warden/skills-policy.md`.
4. `warden attest write --review-dir` refuses a roster that does not match the resolved crew.

## Sub-project 4: keep the memory loop running

1. A weekly scheduled workflow runs `warden memory ingest` and `warden memory stats`, and opens or updates one issue when a candidate crosses the promotion bar or a rule enters pause territory.
2. Run the retro, and promote `evidence-attribution` through the full loop with its backtest, after tests-required and fail-closed.
3. Adopt one non-blocking rule through `warden autonomy adopt`, in a human-merged PR: the machine tier's first real use.
4. Certification Level 5 counts a promote artifact, not only a candidate over the bar.
5. The wiki's Roadmap states the promotion history accurately.

## Sub-project 5: prove the cage

1. Install the self-cage with `cage enroll` from the installed package.
2. The rendered permission profile allows exactly the push a run's own branch needs, proven by a test. Two runs recorded as usage-limit were really a denied `git push` with finished work waiting.
3. The runner classifies a stop by its real cause. Today it marks any run whose log contains the words "usage limit" as usage-limit, and the injected prompt contains them; two real session-limit stops were recorded as plain failures.
4. The runner checks remaining usage before starting and records a skip instead of dying mid-run.
5. The proof is 10 unattended runs on low-risk beads in this repository. A run succeeds when it opens a PR that passes the gate. The cage ledger is the evidence.
6. The README marks the cage experimental until the proof passes, and certification reports the measured success rate.

## Order and why

1. De-churn first, because every later PR pays the churn cost until it lands.
2. Install next, because it is what makes Nightgate adoptable by an invited team.
3. Graph third, because it rewrites the crew prose in the skills.
4. Memory loop fourth.
5. Cage last, because it needs stable skills beneath it and spends the most usage.

## Out of scope

- Public visibility, a public package and a product name.
- Replacing the review crew with a commercial reviewer.
- New certification rungs beyond the Level 5 correction.

## Success criteria

- An invited collaborator enrolls a repository from the README alone and reaches Level 2; the CI init test proves the mechanics.
- A typical fix PR finishes in one review round.
- Each of the five products has run-time evidence:
  - warden: a gate verdict on an invited collaborator's PR;
  - skill pack: a PR taken by the ship skill from bead to merge-ready under the one-round protocol, then merged by the founder; ship never merges;
  - graph: an attested roster that matches `warden graph crew`;
  - memory: a scheduled detector run and one machine adoption;
  - cage: ten successful unattended runs.
