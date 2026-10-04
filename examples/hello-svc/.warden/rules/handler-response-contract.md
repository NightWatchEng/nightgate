---
id: handler-response-contract
severity: HIGH
engine: python
applies_to: ["hello_svc/**/*.py"]
---
Every function declaring the handler contract (`-> tuple[int, dict]`) must
return a two-element tuple: an HTTP status int and a JSON-serializable dict
body. `route()` feeds the pair straight into `json.dumps`, so a bare dict or
a mis-shaped tuple is a runtime 500 on a path no regex can see.

This rule is `engine: python` on purpose — it is the enrollment example's
demonstration of the PROJECT CHECKER seam: a repo gets a deterministic rule
no pattern can express by dropping a reviewed module into
`.warden/checkers/` (see that module's header for the contract). The checker
code is hashed into `rules_version` like any other policy — a checker edit
is a policy edit.
