---
id: os-command-injection
severity: MEDIUM
engine: declarative
# Shipped code only, the unsafe-deserialization precedent: tests write these
# spellings as fixture strings to prove the checks fire (test_advisor.py and
# test_catalog_starters.py both quote `shell=True` verbatim), and this file
# and the catalog entry quote them too — scanning tests/** or ** would flag
# the gate's own proof. fail-closed (engine: claude) keeps judgment coverage
# of tests/**, so the narrow scope here blinds nothing that watches.
applies_to: ["warden/**/*.py", "cage/**/*.py"]
implements: ["os-command-injection"]
checks:
  # Left boundaries added by the adoption's review round: without \b,
  # `use_shell = True` and `powershell = True` (plain config flags) fired,
  # and `myos.system(` satisfied the alternation as a substring. The
  # catalog starter carries the same fix — the two stay in step.
  - id: shell-true
    pattern: '\bshell\s*=\s*True'
    message: "subprocess with shell=True — pass an argv list instead"
  - id: os-system
    pattern: '\b(os\.(system|popen)|subprocess\.get(status)?output|commands\.getoutput)\('
    message: "command string runs via /bin/sh — pass an argv list"
---
Shell strings built from anything untrusted, where a semicolon in a filename
becomes a second command (CWE-78, 2024 CWE Top 25 rank 7).

Adopted by the #105 adjudication at **one measured hit** on the
tree, and that hit is the adjudication's worked example rather than noise:
`warden/verify.py` runs `step.run` — a consumer-authored `repo.yaml` string —
with `shell=True`, **kept deliberately** under a reasoned
`warden:allow(shell-true)` marker at the line. The recorded boundary: a
verify scope IS a shell command by design; the file that supplies it is gate
surface (HIGH-tier here), and a repo that can edit its own `repo.yaml`
already controls its own gate — argv would change the call shape, not the
trust boundary. The protections that actually bind are who can edit
`repo.yaml`, and the cage for unattended runs. Zero unallowed hits at
adoption.

**Dismissing a true match.** A constant command with no interpolation still
matches `shell=True` — the pattern cannot see what the string holds. Prefer
an argv list; where shell semantics are genuinely the point, carry a
`warden:allow(shell-true): <reason>` on or above the line, because the
reason is the artifact the next reader inherits. A bare marker suppresses
nothing.

**What this rule does NOT cover.** The spellings above. A shell reached
through `Popen(..., shell=True)` matches; one reached through a library that
shells out internally does not, and `scripts/*.sh` — which ARE shell — are
out of scope by construction.

MEDIUM is deliberate: `blocking_severities: [HIGH]`, so this reports without
blocking while it earns a precision history. Mechanical rule — a false
positive is a pattern bug to fix, not a finding to argue with.
