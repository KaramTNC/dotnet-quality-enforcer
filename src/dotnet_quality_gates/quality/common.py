from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from dotnet_quality_gates.context import current_context

_DIFF_HUNK_PATTERN = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<length>\d+))? @@"
)


def load_policy_object(policy_path: Path, warning_context: str) -> dict[str, object]:
    """Load a policy object and safely fall back for unusable user input."""
    if not policy_path.exists():
        return {}

    try:
        raw_policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        print(
            f"Warning: failed to read policy file '{policy_path}': {ex}. "
            f"Falling back to built-in {warning_context} config.",
            file=sys.stderr,
        )
        return {}

    if not isinstance(raw_policy, dict):
        print(
            f"Warning: policy file '{policy_path}' must contain a JSON object. "
            f"Falling back to built-in {warning_context} config.",
            file=sys.stderr,
        )
        return {}

    return raw_policy


def policy_section(policy: dict[str, object], section_name: str) -> dict[str, object]:
    section = policy.get(section_name, {})
    return section if isinstance(section, dict) else {}


def parse_changed_lines(diff_text: str) -> dict[str, set[int]]:
    """Return added line numbers grouped by new-file path from a unified diff."""
    changed: dict[str, set[int]] = defaultdict(set)
    current_file: str | None = None

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if line.startswith("+++"):
            current_file = None
            continue
        if not line.startswith("@@") or current_file is None:
            continue

        match = _DIFF_HUNK_PATTERN.match(line)
        if match is None:
            continue

        start = int(match.group("start"))
        length = int(match.group("length") or 1)
        if length > 0:
            changed[current_file].update(range(start, start + length))

    return dict(changed)


def load_quality_section_config(
    policy_path: Path,
    section_name: str,
    default_include_roots: list[str],
    default_exclude_globs: list[str],
    warning_context: str,
) -> tuple[list[str], list[str]]:
    section = policy_section(load_policy_object(policy_path, warning_context), section_name)
    include_roots = sanitize_string_list(section.get("include_roots", default_include_roots))
    exclude_globs = sanitize_string_list(section.get("exclude_globs", default_exclude_globs))

    return (
        include_roots or list(default_include_roots),
        exclude_globs or list(default_exclude_globs),
    )


def is_repo_excluded(path: Path, exclude_globs: list[str], repo_root: Path | None = None) -> bool:
    repo_root = repo_root or current_context().repo_root
    try:
        relative_path = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return False
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


def sanitize_string_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]
