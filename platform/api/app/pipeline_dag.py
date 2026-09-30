"""Parsing and validating an operator's DAG config, before any of it can run.

Pure: no database, no storage, no Temporal. Registration calls this to decide
whether a version can be sealed at all; nothing here assumes it is being
called from a request. The rule, same as contracts.py's own argument: the
unsafe pipeline (one with a cycle, or a dangling reference, or no way to
reach a gate decision) should not be expressible, not merely rejected at
run time after it half-executes.
"""

from __future__ import annotations

import re

import yaml

BUILTIN_BLOCKS = {"adopt_version", "transcribe", "detect", "handoff", "redact", "verify"}
STEP_KINDS = {"builtin", "script", "gate", "wait_for_human"}

# ${steps.NAME.KEY}, the only reference syntax a step's `inputs` values
# understand. Anything else in an inputs value is a literal.
REF_PATTERN = re.compile(r"^\$\{steps\.([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\}$")


class DagConfigError(Exception):
    """The YAML itself is malformed or missing its required top-level shape.

    Distinct from validate_dag's problem list below: this is thrown for
    something that is not a DAG at all (bad YAML, no 'steps' key), where a
    list of specific problems would be meaningless. validate_dag is for a
    real DAG that breaks a specific rule.
    """


def parse_dag_config(yaml_text: str) -> dict:
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise DagConfigError(f"not valid YAML: {exc}") from exc

    if not isinstance(parsed, dict):
        raise DagConfigError("the top level of the file must be a mapping")
    steps = parsed.get("steps")
    if not isinstance(steps, list) or not steps:
        raise DagConfigError("'steps' must be a non-empty list")
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or "name" not in step or "kind" not in step:
            raise DagConfigError(f"step {i} is missing 'name' or 'kind'")
    return parsed


def _referenced_step_names(value) -> list[str]:
    """Every step name a value's ${steps.X.Y} reference points at, if any."""
    if isinstance(value, str):
        m = REF_PATTERN.match(value)
        if m:
            return [m.group(1)]
    return []


def validate_dag(config: dict, script_names: set[str]) -> list[str]:
    """Every problem found, never raising. Empty means the DAG can register.

    script_names is the set of paths actually present in the uploaded zip,
    so a script step naming a file that was never uploaded is caught here,
    before a run ever tries to fetch it and fails minutes later instead.
    """
    problems: list[str] = []
    steps = config.get("steps", [])
    names = [s["name"] for s in steps]

    seen: set[str] = set()
    for name in names:
        if name in seen:
            problems.append(f"step name {name!r} is used more than once")
        seen.add(name)

    by_name = {s["name"]: s for s in steps}

    for step in steps:
        kind = step.get("kind")
        if kind not in STEP_KINDS:
            problems.append(
                f"step {step['name']!r} has kind {kind!r}, which is not one of "
                f"{sorted(STEP_KINDS)}"
            )
            continue

        for dep in step.get("depends_on", []):
            if dep not in by_name:
                problems.append(
                    f"step {step['name']!r} depends_on {dep!r}, which is not a step "
                    f"in this DAG"
                )

        for key, value in (step.get("inputs") or {}).items():
            for ref in _referenced_step_names(value):
                if ref not in by_name:
                    problems.append(
                        f"step {step['name']!r}'s input {key!r} references "
                        f"${{steps.{ref}...}}, which is not a step in this DAG"
                    )

        if kind == "builtin":
            block = step.get("block")
            if block not in BUILTIN_BLOCKS:
                problems.append(
                    f"step {step['name']!r} names builtin block {block!r}, which is "
                    f"not one of {sorted(BUILTIN_BLOCKS)}"
                )
        elif kind == "script":
            script = step.get("script")
            if not script or script not in script_names:
                problems.append(
                    f"step {step['name']!r} names script {script!r}, which is not "
                    f"in the uploaded zip"
                )
        elif kind == "gate":
            if not step.get("to_class"):
                problems.append(f"gate step {step['name']!r} needs a to_class")
            inputs = step.get("inputs") or {}
            if "recommendation" not in inputs or "recommendation_reason" not in inputs:
                problems.append(
                    f"gate step {step['name']!r} needs inputs.recommendation and "
                    f"inputs.recommendation_reason"
                )

    # Cycle detection: a plain depth-first walk, reporting the first back
    # edge found rather than every one, the same "one clear problem, not a
    # traversal dump" reasoning contracts.py's own validate already uses.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in names}

    def visit(name: str, path: list[str]) -> bool:
        color[name] = GRAY
        for dep in by_name.get(name, {}).get("depends_on", []):
            if dep not in color:
                continue  # already reported as dangling, above
            if color[dep] == GRAY:
                problems.append(f"cycle: {' -> '.join(path + [dep])}")
                return True
            if color[dep] == WHITE and visit(dep, path + [dep]):
                return True
        color[name] = BLACK
        return False

    for name in names:
        if color.get(name) == WHITE:
            if visit(name, [name]):
                break

    # Exactly one terminal (nothing depends on it) gate step.
    depended_on = {dep for s in steps for dep in s.get("depends_on", [])}
    terminals = [s for s in steps if s["name"] not in depended_on]
    gate_terminals = [s for s in terminals if s.get("kind") == "gate"]
    if len(gate_terminals) == 0:
        problems.append(
            "this DAG has no terminal gate step: nothing depends on a step of "
            "kind 'gate', so no run of it could ever reach a gate_decision"
        )
    elif len(gate_terminals) > 1:
        names_ = ", ".join(s["name"] for s in gate_terminals)
        problems.append(
            f"this DAG has more than one terminal gate step ({names_}); exactly "
            f"one is required"
        )

    return problems
