from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from dotnet_quality_gates.context import current_context
from dotnet_quality_gates.quality.common import (  # noqa: E402
    is_repo_excluded,
    load_policy_object,
    load_prefixed_baseline_violations,
    policy_section,
)
from dotnet_quality_gates.unit_test_conventions import (  # noqa: E402
    REPO_ROOT,
    iter_cs_files,
    mask_comments_and_strings,
)

DEFAULT_POLICY_PATH = current_context().policy_path
DEFAULT_INCLUDE_ROOTS = ["src"]
DEFAULT_EXCLUDE_GLOBS = [
    "**/*.Designer.cs",
    "**/*.g.cs",
    "**/*.g.i.cs",
    "**/AssemblyInfo.cs",
]
DEFAULT_LAYER_RULES = {
    "Domain": [],
    "Application": ["Domain"],
    "Infrastructure": ["Application", "Domain"],
    "Presentation": ["Application", "Domain", "Infrastructure"],
}

USING_DIRECTIVE_PATTERN = re.compile(
    r"(?m)^\s*(?:global\s+)?using\s+"
    r"(?:static\s+)?"
    r"(?:(?:[A-Za-z_]\w*)\s*=\s*)?"
    r"(?P<namespace>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;"
)


def load_architectural_boundaries_config(policy_path: Path) -> tuple[list[str], list[str], dict[str, list[str]]]:
    section = policy_section(
        load_policy_object(policy_path, "architectural boundary"),
        "architectural_boundaries",
    )

    include_roots = _sanitize_string_list(section.get("include_roots", DEFAULT_INCLUDE_ROOTS))
    exclude_globs = _sanitize_string_list(section.get("exclude_globs", DEFAULT_EXCLUDE_GLOBS))
    layer_rules = _load_layer_rules(section.get("layer_rules", DEFAULT_LAYER_RULES))

    return (
        include_roots or list(DEFAULT_INCLUDE_ROOTS),
        exclude_globs or list(DEFAULT_EXCLUDE_GLOBS),
        layer_rules or dict(DEFAULT_LAYER_RULES),
    )


def validate_architectural_boundaries(
    include_roots: list[Path],
    exclude_globs: list[str],
    layer_rules: dict[str, list[str]],
) -> list[str]:
    violations: list[str] = []
    violations.extend(validate_project_references(include_roots, layer_rules))
    violations.extend(validate_using_directives(include_roots, exclude_globs, layer_rules))
    return sorted(violations, key=str.lower)


def validate_project_references(include_roots: list[Path], layer_rules: dict[str, list[str]]) -> list[str]:
    violations: list[str] = []

    for include_root in include_roots:
        for project_path in sorted(include_root.rglob("*.csproj"), key=lambda item: item.as_posix().lower()):
            if any(part in {"bin", "obj"} for part in project_path.parts):
                continue

            source_layer = layer_for_path(project_path)
            if source_layer is None:
                continue

            allowed_layers = allowed_dependency_layers(source_layer, layer_rules)
            for reference_text, line_number in read_project_references(project_path):
                reference_path = (project_path.parent / reference_text.replace("\\", "/")).resolve()
                target_layer = layer_for_path(reference_path)
                if target_layer is None or target_layer in allowed_layers:
                    continue

                violations.append(
                    f"{to_repo_path(project_path)}:{line_number}: "
                    f"{source_layer} project must not reference {target_layer} project '{to_repo_path(reference_path)}'."
                )

    return violations


def validate_using_directives(
    include_roots: list[Path],
    exclude_globs: list[str],
    layer_rules: dict[str, list[str]],
) -> list[str]:
    violations: list[str] = []
    known_layers = set(layer_rules)

    for include_root in include_roots:
        for file_path in iter_cs_files(include_root):
            if is_repo_excluded(file_path, exclude_globs, REPO_ROOT):
                continue

            source_layer = layer_for_path(file_path)
            if source_layer is None:
                continue

            denied_layers = known_layers - allowed_dependency_layers(source_layer, layer_rules)
            if not denied_layers:
                continue

            text = file_path.read_text(encoding="utf-8", errors="ignore")
            masked = mask_comments_and_strings(text)
            for match in USING_DIRECTIVE_PATTERN.finditer(masked):
                namespace = match.group("namespace")
                target_layer = first_namespace_segment(namespace)
                if target_layer not in denied_layers:
                    continue

                line_number = text.count("\n", 0, match.start()) + 1
                violations.append(
                    f"{to_repo_path(file_path)}:{line_number}: "
                    f"{source_layer} code must not depend on {target_layer} namespace '{namespace}'."
                )

    return violations


def read_project_references(project_path: Path) -> list[tuple[str, int]]:
    text = project_path.read_text(encoding="utf-8", errors="ignore")
    references: list[tuple[str, int]] = []

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return references

    for element in root.iter():
        if not element.tag.endswith("ProjectReference"):
            continue
        include = element.attrib.get("Include")
        if not include:
            continue
        references.append((include, find_project_reference_line(text, include)))

    return references


def find_project_reference_line(text: str, include: str) -> int:
    include_index = text.find(include)
    if include_index < 0:
        return 1
    return text.count("\n", 0, include_index) + 1


def layer_for_path(path: Path) -> str | None:
    try:
        relative_parts = path.resolve().relative_to((REPO_ROOT / "src").resolve()).parts
    except ValueError:
        return None

    if not relative_parts:
        return None

    first_segment = relative_parts[0]
    if first_segment in DEFAULT_LAYER_RULES:
        return first_segment
    return None


def allowed_dependency_layers(source_layer: str, layer_rules: dict[str, list[str]]) -> set[str]:
    return {source_layer, *layer_rules.get(source_layer, [])}


def first_namespace_segment(namespace: str) -> str:
    return namespace.split(".", 1)[0]


def to_repo_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _load_layer_rules(raw_rules: object) -> dict[str, list[str]]:
    if not isinstance(raw_rules, dict):
        return dict(DEFAULT_LAYER_RULES)

    rules: dict[str, list[str]] = {}
    known_layers = set(DEFAULT_LAYER_RULES)
    for layer, dependencies in raw_rules.items():
        if layer not in known_layers:
            continue
        sanitized_dependencies = [
            dependency
            for dependency in _sanitize_string_list(dependencies)
            if dependency in known_layers and dependency != layer
        ]
        rules[layer] = sanitized_dependencies

    for layer, dependencies in DEFAULT_LAYER_RULES.items():
        rules.setdefault(layer, list(dependencies))

    return rules


def _sanitize_string_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate production architectural dependency boundaries."
    )
    parser.add_argument(
        "--policy-path",
        default=str(DEFAULT_POLICY_PATH),
        help="Path to code quality policy JSON.",
    )
    parser.add_argument(
        "--baseline-path",
        default=str(REPO_ROOT / ".quality" / "baselines" / "architectural_boundaries_baseline.txt"),
        help="Path to a baseline file with one known violation per line prefixed by '- '.",
    )
    parser.add_argument(
        "--max-violations",
        type=int,
        default=250,
        help="Maximum number of violations to print before truncating output.",
    )
    args = parser.parse_args()

    include_root_texts, exclude_globs, layer_rules = load_architectural_boundaries_config(Path(args.policy_path))
    include_roots: list[Path] = []
    for include_root_text in include_root_texts:
        include_root = (REPO_ROOT / include_root_text).resolve()
        if include_root.exists():
            include_roots.append(include_root)
        else:
            print(f"Warning: include root not found and skipped: {include_root_text}", file=sys.stderr)

    if not include_roots:
        print("Architectural boundary check failed: no valid include roots found.", file=sys.stderr)
        return 1

    violations = validate_architectural_boundaries(
        include_roots=include_roots,
        exclude_globs=exclude_globs,
        layer_rules=layer_rules,
    )

    baseline_violations = load_prefixed_baseline_violations(Path(args.baseline_path))
    if baseline_violations:
        violations = [violation for violation in violations if violation not in baseline_violations]

    if violations:
        print("Architectural boundary check failed.", file=sys.stderr)
        displayed = violations[: args.max_violations]
        for violation in displayed:
            print(f" - {violation}", file=sys.stderr)
        if len(violations) > len(displayed):
            remaining = len(violations) - len(displayed)
            print(f" - ... {remaining} additional violations omitted", file=sys.stderr)
        return 1

    print("Architectural boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
