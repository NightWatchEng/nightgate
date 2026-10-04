---
id: no-debug-artifacts
severity: HIGH
engine: declarative
applies_to: ["hello_svc/**/*.py", "tests/**/*.py"]
checks:
  - id: no-breakpoint
    pattern: '(?:^|\s)(?:breakpoint\(|pdb\.set_trace\()'
    message: "debugger hook left in the diff"
  - id: no-focused-test
    pattern: '^\s*@pytest\.mark\.only\b'
    globs: ["tests/**/*.py"]
    message: "focused test would silently skip the rest of the suite"
---
Debug hooks and focused tests are how a green suite stops meaning anything:
`breakpoint()` hangs an unattended run forever, and a focused marker makes
CI pass while testing almost nothing.

This rule is `engine: declarative` on purpose — it is the enrollment
example's demonstration that a project gets a CI-enforced mechanical rule by
writing a rule file, with no Python checker and no platform change. The
checks are hashed into `rules_version` like any other policy.
