---
id: weak-cryptography
severity: MEDIUM
engine: declarative
# Shipped code only — see unsafe-deserialization.md for why tests/** and "**"
# are both out of scope (fixture self-match, and this file quotes its own
# patterns).
applies_to: ["warden/**/*.py", "cage/**/*.py"]
implements: ["weak-cryptography"]
checks:
  - id: broken-hash
    # The factory form `hashlib.new("md5")` was invisible to the direct-call
    # pattern — found by a sweep that re-checked all seven catalog starters.
    pattern: '(\b(md5|sha1)\s*\(|hashlib\.(new\(\s*)?["'']?(md5|sha1)\b|pbkdf2_hmac\(\s*["''](md5|sha1)["''])'
    flags: ['i']
    message: "MD5/SHA-1 — broken as a digest, legacy inside HMAC/PBKDF2; use SHA-256"
  - id: predictable-token-source
    # MATCHES the catalog starter exactly — verified by
    # tests/test_catalog_starters.py, which fails if a rule is ever narrower
    # than the starter it implements. The adoption review widened this
    # locally while the catalog kept shipping the narrow form; widening the
    # starter closed that gap from the other side, so there is no divergence
    # left to justify.
    # `sample` is deliberately NOT here: it is the sampling primitive by name,
    # and this rule's body tells an author to dismiss `random.*` used for
    # sampling — including it would contradict the page it ships on.
    pattern: 'random\.(random|randint|choices?|randrange|randbytes|getrandbits)\('
    message: "non-cryptographic randomness — use secrets/os.urandom for tokens"
---
Broken primitives and predictable randomness used where the security of the
system depends on them (OWASP Top 10:2025 — A04 Cryptographic Failures).

This platform hashes content as an integrity signal, not as decoration:
`rules_version` is a digest of the rule files, and attestation shards carry
content digests that `attest check` and `certify --level 4` pair against a
commit. Those are the places where a broken digest quietly stops meaning
anything. All of them use SHA-256 today — `sha256(` does not match either
pattern below, by construction.

Adopted at **zero measured hits** on the tree at adoption (#98):
`broken-hash` 0, `predictable-token-source` 0 across `warden/` and `cage/`.

**Dismissing a true match — read this before arguing with the gate.** The
catalog names this rule's own failure mode: MD5 as a cache key or a
non-security content digest is legitimate and WILL be flagged, and a rule
with nowhere to record that argues about non-security hashing forever. So:

- **Non-security digest** (cache key, dedup key, test fixture id, bucketing):
  dismiss, and say in one line what the digest is FOR. The question is never
  "is MD5 weak" — it is "does anything trust this value's collision
  resistance". If nothing does, it is not this rule's business.
- **Anything an attestation, a version, a signature, or a gate decision
  reads**: not dismissible. Collision resistance is exactly what those rest
  on. Switch to SHA-256.
- **`random.*` for non-secret sampling** (jitter, shuffling test order,
  picking a fixture): dismiss. `random.*` for anything a caller treats as
  unguessable — a token, a nonce, a temp path, an id someone could forge:
  not dismissible, use `secrets`.

MEDIUM is deliberate. `blocking_severities: [HIGH]`, so this reports without
blocking while it earns a precision history. This rule has the widest
legitimate-dismissal surface of the three adopted in #98, which is
the reason the guidance above is this specific: an unexplained dismissal here
is how the class stops being read at all.
