from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from dotnet_quality_gates.unit_test_conventions import REPO_ROOT


def load_quality_section_config(
    policy_path: Path,
    section_name: str,
    default_include_roots: list[str],
    default_exclude_globs: list[str],
    warning_context: str,
) -> tuple[list[str], list[str]]:
    if not policy_path.exists():
        return list(default_include_roots), list(default_exclude_globs)

    try:
        raw_policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        print(
            f"Warning: failed to read policy file '{policy_path}': {ex}. "
            f"Falling back to built-in {warning_context} config.",
            file=sys.stderr,
        )
        return list(default_include_roots), list(default_exclude_globs)

    section = raw_policy.get(section_name, {})
    include_roots = _sanitize_string_list(section.get("include_roots", default_include_roots))
    exclude_globs = _sanitize_string_list(section.get("exclude_globs", default_exclude_globs))

    return (
        include_roots or list(default_include_roots),
        exclude_globs or list(default_exclude_globs),
    )


def is_repo_excluded(path: Path, exclude_globs: list[str], repo_root: Path | None = None) -> bool:
    repo_root = repo_root or REPO_ROOT
    relative_path = path.relative_to(repo_root)
    return any(relative_path.match(pattern) for pattern in exclude_globs)


def load_prefixed_baseline_violations(
    path: Path,
    normalize: Callable[[str], str] | None = None,
) -> set[str]:
    if not path.exists():
        return set()

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as ex:
        print(f"Warning: failed to read baseline file '{path}': {ex}", file=sys.stderr)
        return set()

    transform = normalize or (lambda value: value)
    violations: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("- "):
            violations.add(transform(line[2:].strip()))
        elif line.startswith(" - "):
            violations.add(transform(line[3:].strip()))
    return violations


def _sanitize_string_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]
