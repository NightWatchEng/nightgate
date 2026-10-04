---
id: security-misconfiguration
severity: MEDIUM
engine: declarative
# Shipped code only. The test-tree exclusion is not stylistic here: the
# catalog's own false-positive note for this entry says test fixtures
# legitimately disable verification and that without a test exclude "this
# rule fires constantly, which is the failure mode that teaches people to
# ignore the gate". See unsafe-deserialization.md for the "**" hazard.
applies_to: ["warden/**/*.py", "cage/**/*.py"]
implements: ["security-misconfiguration"]
checks:
  - id: debug-enabled
    # `DEBUG = 1` is the same switch, so both spellings match.
    pattern: 'DEBUG\s*=\s*(True|1)\b'
    message: "debug mode enabled — verify this cannot reach production"
  - id: tls-verification-off
    # check_hostname/ssl_verify are the same decision under other names, and
    # `= 0` is the same value, so every combination matches.
    pattern: '(verify|check_hostname|ssl_verify)\s*=\s*(False|0)\b'
    message: "TLS certificate verification disabled"
---
Development conveniences reaching production — debug modes and disabled
certificate verification (OWASP Top 10:2025 — A02 Security
Misconfiguration).

Adopted at **zero measured hits** on the tree at adoption (#98):
`debug-enabled` 0, `tls-verification-off` 0 across `warden/` and `cage/`.

**What this rule does NOT cover, stated so nobody reads it as broader than
it is.** Both patterns are Python-shaped: `DEBUG = True` and
`verify = False`. They do not match the YAML or TOML spelling of the same
mistake (`debug: true`, `verify = false` in a config file), and this repo
carries deployable configuration in both. Adopting the catalog's starter
verbatim buys the Python surface and nothing else. Widening to config files
is a separate change with its own noise measurement — not something to
assume this rule already did. The catalog entry is `applies when: the repo
carries deployable configuration`; this rule satisfies the code half of that
and leaves the config half unenforced ON PURPOSE.

**Dismissing a true match.** `verify=False` against a local fixture server
in non-shipped code is the catalog's named false positive — but such code
lives under `tests/`, which is out of scope above, so a match inside
`warden/` or `cage/` is far more likely to be real. Dismiss only with the
reason the value cannot reach a production path.

MEDIUM is deliberate. `blocking_severities: [HIGH]`, so this reports without
blocking while it earns a precision history.
