---
id: schema-freeze
severity: MEDIUM
engine: claude
judges: behaviour
applies_to: ["warden/schemas/**"]
---
The JSON schemas under `warden/schemas/` are contracts consumers parse
(manifests, attestations, findings, repo.yaml). Flag any change that is not
purely additive-optional: new required fields, removed properties, changed
types or enums, tightened constraints. Consumers pin platform versions, so a
breaking schema change requires a version bump called out in the diff.
