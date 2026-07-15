from __future__ import annotations

import json
from pathlib import Path
from typing import Callable


class PolicyValidationError(ValueError):
    """Raised when a quality policy cannot be safely interpreted."""


def load_policy_document(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise PolicyValidationError(f"unable to read policy file '{path}': {ex}") from ex
    if not isinstance(raw, dict):
        raise PolicyValidationError(f"policy file '{path}' must contain a JSON object")
    validate_policy_document(raw, path)
    return raw


def validate_policy_file(path: Path) -> None:
    load_policy_document(path)


def validate_policy_document(document: dict[str, object], path: Path | None = None) -> None:
    location = f" in '{path}'" if path is not None else ""
    section_rules: dict[str, dict[str, Callable[[object], bool]]] = {
        "code_size": {
            "include_roots": _string_list,
            "exclude_globs": _string_list,
            "method_warn_lines": _positive_int,
            "method_max_lines": _positive_int,
            "type_warn_lines": _positive_int,
            "type_max_lines": _positive_int,
            "file_warn_lines": _positive_int,
            "file_max_lines": _positive_int,
        },
        "diff_quality": {
            "cyclomatic_complexity_max": _positive_int,
            "cognitive_complexity_max": _positive_int,
            "crap_score_max": _positive_number,
            "max_files_for_gate": _optional_positive_int,
            "line_coverage_threshold": _ratio,
            "branch_coverage_threshold": _optional_ratio,
        },
        "namespace_layout": {"include_roots": _string_list, "exclude_globs": _string_list},
        "source_type_layout": {"include_roots": _string_list, "exclude_globs": _string_list},
        "public_api_documentation": {"include_roots": _string_list, "exclude_globs": _string_list},
        "architectural_boundaries": {
            "include_roots": _string_list,
            "exclude_globs": _string_list,
            "layer_rules": _string_list_map,
        },
        "repo_coverage": {"expected_packages": _string_list},
        "test_architecture": {
            "additional_project_mappings": _string_list_map,
            "project_mappings": _string_list_map,
        },
        "test_conventions": {"source_include_roots": _string_list},
        "unit_test_conventions": {"source_include_roots": _string_list},
    }

    for section_name, fields in section_rules.items():
        if section_name not in document:
            continue
        section = document[section_name]
        if not isinstance(section, dict):
            raise PolicyValidationError(f"policy section '{section_name}'{location} must be an object")
        for field_name, validator in fields.items():
            if field_name in section and not validator(section[field_name]):
                raise PolicyValidationError(
                    f"policy key '{section_name}.{field_name}'{location} has an invalid value"
                )

    code_size = document.get("code_size")
    if isinstance(code_size, dict):
        for warning_key, failure_key in (
            ("method_warn_lines", "method_max_lines"),
            ("type_warn_lines", "type_max_lines"),
            ("file_warn_lines", "file_max_lines"),
        ):
            warning = code_size.get(warning_key)
            failure = code_size.get(failure_key)
            if isinstance(warning, int) and isinstance(failure, int) and warning > failure:
                raise PolicyValidationError(
                    f"policy key '{warning_key}'{location} must not exceed '{failure_key}'"
                )

    boundaries = document.get("architectural_boundaries")
    if isinstance(boundaries, dict) and isinstance(boundaries.get("layer_rules"), dict):
        for layer, dependencies in boundaries["layer_rules"].items():
            if isinstance(dependencies, list) and layer in dependencies:
                raise PolicyValidationError(
                    f"policy key 'architectural_boundaries.layer_rules.{layer}'{location} cannot depend on itself"
                )


def _string_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item.strip() for item in value)


def _string_list_map(value: object) -> bool:
    return (
        isinstance(value, dict)
        and all(
            isinstance(key, str)
            and key.strip()
            and isinstance(items, list)
            and all(isinstance(item, str) and item.strip() for item in items)
            for key, items in value.items()
        )
    )


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _optional_positive_int(value: object) -> bool:
    return value is None or _positive_int(value)


def _positive_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _ratio(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


def _optional_ratio(value: object) -> bool:
    return value is None or _ratio(value)
