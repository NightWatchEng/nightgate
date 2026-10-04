"""warden review: the deterministic ($0) rules gate.

engine:python rules run their mechanical.py checkers against the diff;
engine:declarative rules run the checks declared in their own frontmatter;
engine:claude rules are reported as deferred_to_pre_pr — they are evaluated
before the PR inside a Claude Code session (the pre-pr-review skill), which
attests via `warden attest`. No network, no API spend, fail-closed.

Invariants unchanged from the LLM era: severity comes from the rule file,
findings are schema-validated, exit semantics are 0/1/2.
"""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from . import declarative
from .config import ConfigError, RepoConfig
from .diffs import DiffContext
from .plugins import load_registry
from .rules import Rule, applicable

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "review-findings.schema.json"
ARTIFACT_SCHEMA = json.loads(_SCHEMA_PATH.read_text())
_artifact_validator = Draft202012Validator(ARTIFACT_SCHEMA)

ENGINE_NAME = "deterministic"


def validate_registry(rules: tuple[Rule, ...], registry: dict) -> None:
    """Every engine:python rule MUST have a checker — core or project plugin —
    a rule that silently never runs is the worst failure mode a gate can have
    (fail-closed)."""
    unchecked = [r.id for r in rules if r.engine == "python" and r.id not in registry]
    if unchecked:
        raise ConfigError(
            f"engine:python rules with no checker (core pack or "
            f".warden/checkers/): {unchecked}")


# Param keys whose value is a companion-tree SIGNAL, matched against
# ctx.all_files (every changed path, before context_excludes) rather than read
# from the scanned context — so a context_exclude hiding that path does NOT
# break the checker, and it is exempt from the fail-closed check below
# (tests-accompany-logic checks whether tests/ changed, and
# tests/ is context-excluded from scanning). New checkers that read a param
# against all_files add the key here — and MUST carry a test proving the
# checker reads that param against ctx.all_files, never ctx.files/content (as
# test_companion_seen_via_all_files_when_context_excluded does for this one):
# the exemption keys on the param NAME, so that test is the only thing keeping
# a future misuse from silently breaking the checker.
_ALL_FILES_PARAMS = frozenset({"companion_prefix"})


def validate_excludes(config: RepoConfig, rules: tuple[Rule, ...]) -> None:
    """A global context_exclude that hides a file a checker's params reference
    silently breaks that checker. Fail closed at load — except
    for params the checker matches against ctx.all_files (see _ALL_FILES_PARAMS),
    which context_excludes cannot hide."""
    from .rules import glob_match

    def referenced_paths(params: dict) -> list[str]:
        out = []
        for key, value in params.items():
            if key in _ALL_FILES_PARAMS:
                continue
            items = value if isinstance(value, list) else [value]
            out += [v for v in items if isinstance(v, str)]
        return out

    for rule in rules:
        if rule.engine != "python" or not rule.params:
            continue
        for ref in referenced_paths(rule.params):
            probe = ref.replace("**", "x/x")  # a concrete path the glob covers
            for exc in config.review.context_excludes:
                if glob_match(exc, probe) or glob_match(exc, ref):
                    raise ConfigError(
                        f"context_exclude {exc!r} hides {ref!r} referenced by "
                        f"rule {rule.id!r} params — the checker would silently "
                        "break; use a rule-level exclude instead")


def run_review(config: RepoConfig, rules: tuple[Rule, ...], ctx: DiffContext,
               rules_version: str, *, project_checkers: bool = True) -> dict:
    """Returns the schema-valid review-findings artifact document."""
    registry = load_registry(config.root / config.review.rules_dir,
                             project=project_checkers)
    validate_registry(rules, registry)
    validate_excludes(config, rules)
    hits = applicable(rules, list(ctx.files))
    findings: list[dict] = []
    deferred: list[str] = []
    paused: list[str] = []
    for rule in rules:
        if rule.id not in hits:
            continue
        if rule.paused:
            # Recorded, never silent: the artifact must show what was OFF
            # while this diff was gated.
            paused.append(rule.id)
            continue
        if rule.engine == "claude":
            deferred.append(rule.id)
            continue
        if rule.engine == "declarative":
            for f in declarative.run(rule, ctx, hits[rule.id]):
                findings.append({**f, "rule_id": rule.id,
                                 "severity": rule.severity})
            continue
        for f in registry[rule.id](ctx, rule.params or {}, hits[rule.id]):
            findings.append({**f, "rule_id": rule.id, "severity": rule.severity})

    doc = {"rules_version": rules_version, "engine": ENGINE_NAME,
           "base_sha": ctx.base, "head_sha": ctx.head,
           "findings": findings, "deferred_to_pre_pr": sorted(deferred),
           "paused": sorted(paused)}
    _artifact_validator.validate(doc)
    return doc


def blocking(config: RepoConfig, doc: dict) -> list[dict]:
    return [f for f in doc["findings"]
            if f["severity"] in config.review.blocking_severities]
