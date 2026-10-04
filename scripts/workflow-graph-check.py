#!/usr/bin/env python3
"""Follow every value one job of a GitHub Actions workflow hands another.

usage: workflow-graph-check.py WORKFLOW.yml [WORKFLOW.yml ...]

actionlint checks expressions, `needs` names and each action's inputs. It does
not follow a value from the step that writes it to the job that reads it, and
it knows nothing about artifacts. This reads the workflow as data, runs
nothing, and reports each of these as a finding:

- a job needs a job the workflow does not define;
- a job reads `needs.J` without listing J in its needs;
- a job reads `needs.J.outputs.K` and J declares no output K;
- a job output reads `steps.S.outputs.K` and the job has no step S, or S is a
  `run:` step with no line, other than a comment line, that writes `K=` to
  $GITHUB_OUTPUT;
- a step reads `steps.S.outputs.K` and no earlier step of its job is S, or S
  never writes K as above;
- a job downloads an artifact by a name that no job it needs, directly or
  through another job, uploads.

`needs`, `steps` and `outputs` are read in the dotted form and the index form,
`needs['J'].outputs['K']`. A reference built any other way, such as
`needs[format(...)]` or `fromJSON`, is not followed.

Exit 0: no finding. Exit 1: one line per finding. Exit 2: a file that is not
a workflow with jobs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

_NAME = r"[A-Za-z_][\w-]*"


def _member(group: str) -> str:
    """`.NAME` or `['NAME']` / `["NAME"]`, the name captured as GROUP."""
    return rf"""(?:\.(?P<{group}>{_NAME})|\[\s*(?P<{group}_q>['"])(?P<{group}_i>{_NAME})(?P={group}_q)\s*\])"""


_OUTPUTS = r"""(?:\.outputs|\[\s*(?P<outputs_q>['"])outputs(?P=outputs_q)\s*\])"""

NEEDS = re.compile(rf"\bneeds{_member('job')}(?:{_OUTPUTS}{_member('key')})?")
STEP_OUTPUT = re.compile(rf"\bsteps{_member('job')}{_OUTPUTS}{_member('key')}")


def _refs(pattern: re.Pattern, text: str) -> list[tuple[str, str]]:
    return [(m["job"] or m["job_i"], m["key"] or m["key_i"] or "")
            for m in pattern.finditer(text)]
DOWNLOAD = "actions/download-artifact@"
UPLOAD = "actions/upload-artifact@"


def _strings(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def _needs(job: dict) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def _writes_output(step: dict, key: str) -> bool:
    """Whether the step can produce output KEY: an action declares its own, so
    only a `run:` script is read."""
    if "run" not in step:
        return True
    write = re.compile(rf"(?<![\w-]){re.escape(key)}=.*>>\s*\"?\$\{{?GITHUB_OUTPUT\b")
    return any(write.search(line) for line in str(step["run"]).splitlines()
               if not line.lstrip().startswith("#"))


def _artifact(step: dict, action: str) -> str | None:
    if not str(step.get("uses", "")).startswith(action):
        return None
    return str((step.get("with") or {}).get("name", "artifact"))


def check(doc) -> list[str]:
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict) or not jobs:
        raise ValueError("no jobs mapping")
    findings: list[str] = []

    def closure(name: str, seen: set[str] | None = None) -> set[str]:
        seen = set() if seen is None else seen
        for need in _needs(jobs.get(name) or {}):
            if need in jobs and need not in seen:
                seen.add(need)
                closure(need, seen)
        return seen

    uploads = {name: {a for s in job.get("steps") or [] if (a := _artifact(s, UPLOAD))}
               for name, job in jobs.items()}

    for name, job in jobs.items():
        needs = _needs(job)
        for need in needs:
            if need not in jobs:
                findings.append(f"{name}: needs `{need}`, which the workflow does not define")
        for text in _strings({k: v for k, v in job.items() if k != "outputs"}) + \
                _strings(job.get("outputs") or {}):
            for need, key in _refs(NEEDS, text):
                if need not in needs:
                    findings.append(f"{name}: reads needs.{need} without listing {need} in needs")
                elif key and need in jobs and key not in (jobs[need].get("outputs") or {}):
                    findings.append(f"{name}: reads needs.{need}.outputs.{key}, "
                                    f"which {need} does not declare")

        steps = job.get("steps") or []
        by_id = {s["id"]: s for s in steps if isinstance(s, dict) and "id" in s}
        for out, value in (job.get("outputs") or {}).items():
            for sid, key in _refs(STEP_OUTPUT, str(value)):
                if sid not in by_id:
                    findings.append(f"{name}: output {out} reads steps.{sid}, "
                                    f"and the job has no step with id {sid}")
                elif not _writes_output(by_id[sid], key):
                    findings.append(f"{name}: output {out} reads steps.{sid}.outputs.{key}, "
                                    f"and step {sid} never writes {key}= to $GITHUB_OUTPUT")
        earlier: dict[str, dict] = {}
        for step in steps:
            label = step.get("name") or step.get("uses") or step.get("id") or "a step"
            for sid, key in _refs(STEP_OUTPUT, " ".join(_strings(step))):
                if sid not in earlier:
                    findings.append(f"{name}: step '{label}' reads steps.{sid}, "
                                    f"and no earlier step has id {sid}")
                elif not _writes_output(earlier[sid], key):
                    findings.append(f"{name}: step '{label}' reads steps.{sid}.outputs.{key}, "
                                    f"and step {sid} never writes {key}= to $GITHUB_OUTPUT")
            if "id" in step:
                earlier[step["id"]] = step
            wanted = _artifact(step, DOWNLOAD)
            if wanted is not None:
                sources = sorted(j for j in closure(name) if wanted in uploads[j])
                if not sources:
                    findings.append(f"{name}: downloads artifact '{wanted}', which no job "
                                    f"it needs uploads")
    return findings


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    status = 0
    for arg in argv:
        try:
            findings = check(yaml.safe_load(Path(arg).read_text()))
        except (OSError, yaml.YAMLError, ValueError) as exc:
            print(f"{arg}: not a workflow this can check ({exc}); nothing was checked",
                  file=sys.stderr)
            return 2
        for finding in findings:
            print(f"{arg}: {finding}")
        if findings:
            status = 1
        else:
            print(f"{arg}: no finding: every needs reference, step output and artifact download "
                  "this reads has a source the reading job can reach")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
