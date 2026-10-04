# Skills policy — hello-svc

The per-project contract the Nightgate skill pack reads (platform
docs/wiki/Skills-Policy.md defines the required sections). This example file doubles as
the portability fixture: every required section present, minimal content.

## Verify

- Any code change: `python3 -m hello_svc.app --selfcheck`
- Repair budget: `repo.yaml`'s `repair.budget` key (2 here); this line only
  points at it.

## Autonomy scope

No project-specific exclusions beyond the platform baseline.

## Forbidden paths

none

## Review charter

none

## Integrations

none — every optional hook absent: no plan-gauntlet, no dev-executor
(runs implement directly), no review-crew.

## Shipping

PR bodies need no additions beyond the platform defaults.

## Build disciplines

- test-driven-development: any change to `src/` — the failing test first, watched failing.
- systematic-debugging: any red test or unexpected behavior — root cause named before a fix.
- verification-before-completion: before any "passes"/"done" claim — fresh full-command output in hand.
