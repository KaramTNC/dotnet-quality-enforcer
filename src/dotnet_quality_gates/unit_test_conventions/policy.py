from __future__ import annotations

import json
import sys
from pathlib import Path

from .constants import DEFAULT_SOURCE_INCLUDE_ROOTS


def load_default_source_include_roots(policy_path: Path) -> list[str]:
    if not policy_path.exists():
        return list(DEFAULT_SOURCE_INCLUDE_ROOTS)

    try:
        raw_policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        print(
            f"Warning: failed to read policy file '{policy_path}': {ex}. "
            "Falling back to built-in source include roots.",
            file=sys.stderr,
        )
        return list(DEFAULT_SOURCE_INCLUDE_ROOTS)

    section = raw_policy.get("test_conventions")
    if not isinstance(section, dict):
        section = raw_policy.get("unit_test_conventions", {})
    if not isinstance(section, dict):
        section = {}

    include_roots = section.get("source_include_roots", DEFAULT_SOURCE_INCLUDE_ROOTS)
    if not isinstance(include_roots, list):
        print(
            "Warning: invalid 'test_conventions.source_include_roots' in policy file. "
            "Falling back to built-in source include roots.",
            file=sys.stderr,
        )
        return list(DEFAULT_SOURCE_INCLUDE_ROOTS)

    normalized = [item.strip() for item in include_roots if isinstance(item, str) and item.strip()]
    return normalized or list(DEFAULT_SOURCE_INCLUDE_ROOTS)
