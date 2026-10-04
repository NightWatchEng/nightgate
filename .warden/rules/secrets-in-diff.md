---
id: secrets-in-diff
severity: HIGH
engine: python
applies_to: ["**"]
# Rule bodies quote the very patterns they hunt — never scan them.
excludes: [".warden/rules/**"]
implements: ["hardcoded-credentials"]
---
Flag any ADDED line that introduces a credential or secret: API keys and
tokens (`sk-ant-...`, `ghp_`/`github_pat_`, AWS `AKIA...`, JWTs), passwords,
connection strings with embedded passwords, or hardcoded values that clearly
belong in environment variables.

Do NOT flag obvious placeholders (`xxx`, `<YOUR_KEY>`, `example`) or code that
only READS from the environment. Evidence must quote the exact offending line.
