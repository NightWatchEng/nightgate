---
id: unsafe-deserialization
severity: MEDIUM
engine: declarative
# Shipped code only. NOT tests/**: a test that deserializes a fixture it
# wrote itself is the catalog's named false positive, and this repo's own
# rule tests must contain the literal patterns below to prove the checks
# fire — scanning tests/** would make those fixtures trip the gate.
# NOT "**": this file and warden/guardrails/catalog.yaml both quote
# `yaml.load(` verbatim, so a wider applies_to flags the rule's own body and
# the catalog entry it implements. That is the trap secrets-in-diff records
# as `excludes: [".warden/rules/**"]`; here the narrow applies_to is what
# prevents it, so widening this line means adding those excludes.
applies_to: ["warden/**/*.py", "cage/**/*.py"]
implements: ["unsafe-deserialization"]
checks:
  - id: pickle-loads
    pattern: '\b(pickle|marshal)\.loads?\('
    message: "deserializing untrusted bytes executes whatever they name — use json"
  - id: yaml-unsafe-load
    # Matches the catalog starter, which was widened to this same shape
    # after the adoption review judged the original fail-open: bare
    # `yaml\.load\(`
    # caught only the spelling that ERRORS under PyYAML 6 (a load without
    # Loader= is a TypeError) and missed `yaml.unsafe_load(` — the call named
    # for this vulnerability — plus `full_load` and `load_all`.
    # safe_load/safe_load_all still do not match.
    # Kept in step by tests/test_catalog_starters.py, which fails if a rule
    # is ever NARROWER than the starter it implements.
    pattern: 'yaml\.(unsafe_|full_)?load(_all)?\('
    message: "unsafe YAML load can construct arbitrary types — use warden.yamlio.load() in warden/ and cage/, yaml.safe_load() elsewhere"
---
Formats that reconstruct arbitrary objects, where parsing input is
indistinguishable from executing it (CWE-502, 2024 CWE Top 25 rank 16).

This platform reads YAML everywhere — `repo.yaml`, the guardrail catalog,
consumer configs it does not own. Every call site in `warden/` and `cage/`
goes through `warden.yamlio.load` TODAY, the one loader (#371), which
constructs only through `yaml.SafeLoader` or libyaml's `CSafeLoader`, and
`tests/test_yamlio.py::test_no_warden_or_cage_module_parses_yaml_around_the_loader`
fails the suite on a `yaml.*load*` or `*Loader` spelling anywhere else there;
warden parses files supplied by the repo under review. `pickle` has no
current call site here at all, which is the cheapest moment to forbid it.

Adopted at **zero measured hits** on the tree at adoption (#98):
`pickle-loads` 0, `yaml-unsafe-load` 0 across `warden/` and `cage/`.

**What this rule does NOT cover, stated so nobody reads it as broader than
it is.** It matches the `pickle`/`marshal` and PyYAML spellings named in the
checks above. It does not reach every deserializer that can instantiate a
type — `jsonpickle`, `dill`, `shelve`, `numpy.load(allow_pickle=True)`, or a
`__reduce__` reached through some other library are all invisible to it.
Adding one is a fresh measurement, not something to assume this rule did.

**Dismissing a true match.** `yaml.load(f, Loader=yaml.SafeLoader)` is safe
and still matches — the pattern cannot see the Loader argument. In `warden/`
and `cage/` do not dismiss it: the #371 test above fails the suite on that
spelling everywhere there but `yamlio.py`, so route the call through
`warden.yamlio.load`. In `yamlio.py` itself, or on a path a wider
`applies_to` reaches, say so and dismiss; outside `warden/` and `cage/`
prefer `yaml.safe_load` so the next reader does not re-litigate it.

MEDIUM is deliberate. `blocking_severities: [HIGH]`, so this reports without
blocking while it earns a precision history — the footing wiki-fidelity and
fail-closed both shipped on. Unlike those two this rule is mechanical, so a
false positive here is a pattern bug, not a misreading, and should be fixed
in the pattern rather than argued about in the finding.
