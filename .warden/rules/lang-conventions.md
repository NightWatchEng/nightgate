---
id: lang-conventions
severity: LOW
engine: claude
judges: behaviour
applies_to: ["warden/**", "tests/**", "examples/**"]
implements: ["review-lens-style"]
---
Platform Python conventions. Flag clear violations in ADDED code only:
new function signatures missing type annotations; mutable class-style value
types where a frozen dataclass fits; gross PEP 8 violations (ruff covers
syntax-level). Shell: scripts must pass `sh -n`/`bash -n` and quote variables.

Evidence: quote the added line and name the convention.
