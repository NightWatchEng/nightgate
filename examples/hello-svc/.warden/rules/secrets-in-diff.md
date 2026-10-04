---
id: secrets-in-diff
severity: HIGH
engine: python
applies_to: ["**"]
# Rule bodies quote the very patterns they hunt — never scan them.
excludes: [".warden/rules/**"]
---
Flag any ADDED line that introduces a credential or secret:

- API keys and tokens: provider API keys (e.g. `sk-...`), JWTs, GitHub PATs,
  OAuth client secrets, cloud access key IDs.
- Passwords or connection strings with embedded passwords
  (any `scheme://user:password@host` form).
- `.env` file contents or hardcoded values that clearly belong in env vars
  (repo rule: secrets come from the environment; never commit or log them).

Do NOT flag: obvious placeholders (`sk-ant-xxx`, `<YOUR_KEY>`, `example`,
`redacted`), test fixtures with clearly fake values, or code that only READS
configuration from the environment.

Evidence must quote the exact offending added line.
