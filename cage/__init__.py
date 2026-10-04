"""cage — the unattended run cage, generalized.

A project enrolls by writing a cage.toml; `cage enroll` renders
the cage directory's derived artifacts (generic run.sh, cage.env,
launchd plist, Claude settings profile). Every hard stop lives in the
rendered runner, outside the model.

Accepted limitations:
- The profile's Bash allow rules are mostly command-prefix matches, so a
  session granted python3/uv can in principle write files the Read/Edit/Write
  deny rules seal, and push through a tool other than `git push`. The only
  `git push` the base profile allows is an exact rule per run for that run's
  own branch ([profile].extra_allow can add more). The
  enforcing stop for main is SERVER-SIDE: branch protection + founder-only
  merges. The cage's job is containment plus
  evidence (forbidden-path post-check, ledger, notify), not a sandbox.
"""
