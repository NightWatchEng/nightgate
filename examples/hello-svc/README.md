# hello-svc

Minimal stdlib HTTP JSON service (`/health`, `/greet?name=`) used as the Nightgate enrollment example — its `repo.yaml` + `.warden/rules/` + `.warden/checkers/` are the template that `docs/wiki/Adopting.md` walks through.
Run: `python3 -m hello_svc.app` · CI check: `python3 -m hello_svc.app --selfcheck` · tests: `python3 -m pytest tests -q` (all from this directory; `__pycache__/` and `.warden/out/` are git-ignored).
