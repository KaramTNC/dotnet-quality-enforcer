from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from dotnet_quality_gates.architecture import (
    DEFAULT_EXCLUDE_GLOBS,
    DEFAULT_INCLUDE_ROOTS,
    DEFAULT_LAYER_RULES,
    ArchitectureConfig,
    architecture_from_layer_rules,
    load_architecture_config,
)
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

USING_DIRECTIVE_PATTERN = re.compile(
    r"(?m)^\s*(?:global\s+)?using\s+"
    r"(?:static\s+)?"
    r"(?:(?:[A-Za-z_]\w*)\s*=\s*)?"
    r"(?P<namespace>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;"
)
MAX_PROJECT_XML_BYTES = 10 * 1024 * 1024
UNSAFE_XML_DECLARATION_PATTERN = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def load_architectural_boundaries_config(policy_path: Path) -> tuple[list[str], list[str], ArchitectureConfig]:
    section = policy_section(
        load_policy_object(policy_path, "architectural boundary"),
        "architectural_boundaries",
    )

    include_roots = _sanitize_string_list(section.get("include_roots", DEFAULT_INCLUDE_ROOTS))
    exclude_globs = _sanitize_string_list(section.get("exclude_globs", DEFAULT_EXCLUDE_GLOBS))
    architecture = load_architecture_config(policy_path)

    return (
        include_roots or list(DEFAULT_INCLUDE_ROOTS),
        exclude_globs or list(DEFAULT_EXCLUDE_GLOBS),
        architecture,
    )


def validate_architectural_boundaries(
    include_roots: list[Path],
    exclude_globs: list[str],
    layer_rules: dict[str, list[str]] | None = None,
    *,
    architecture: ArchitectureConfig | None = None,
) -> list[str]:
    resolved_architecture = architecture or architecture_from_layer_rules(layer_rules or DEFAULT_LAYER_RULES)
    violations: list[str] = []
    violations.extend(validate_project_references(include_roots, architecture=resolved_architecture))
    violations.extend(
        validate_using_directives(
            include_roots,
            exclude_globs,
            architecture=resolved_architecture,
        )
    )
    return sorted(violations, key=str.lower)


def validate_project_references(
    include_roots: list[Path],
    layer_rules: dict[str, list[str]] | None = None,
    *,
    architecture: ArchitectureConfig | None = None,
) -> list[str]:
    resolved_architecture = architecture or architecture_from_layer_rules(layer_rules or DEFAULT_LAYER_RULES)
    violations: list[str] = []

    for include_root in include_roots:
        for project_path in sorted(include_root.rglob("*.csproj"), key=lambda item: item.as_posix().lower()):
            if any(part in {"bin", "obj"} for part in project_path.parts):
                continue

            source_unit = resolved_architecture.unit_for_path(project_path, REPO_ROOT)
            if source_unit is None:
                continue

            allowed_units = resolved_architecture.allowed_dependency_names(source_unit)
            for reference_text, line_number in read_project_references(project_path):
                reference_path = (project_path.parent / reference_text.replace("\\", "/")).resolve()
                target_unit = resolved_architecture.unit_for_path(reference_path, REPO_ROOT)
                if target_unit is None or target_unit.name in allowed_units:
                    continue

                violations.append(
                    f"{to_repo_path(project_path)}:{line_number}: "
                    f"{source_unit.name} project must not reference {target_unit.name} project '{to_repo_path(reference_path)}'."
                )

    return violations


def validate_using_directives(
    include_roots: list[Path],
    exclude_globs: list[str],
    layer_rules: dict[str, list[str]] | None = None,
    *,
    architecture: ArchitectureConfig | None = None,
) -> list[str]:
    resolved_architecture = architecture or architecture_from_layer_rules(layer_rules or DEFAULT_LAYER_RULES)
    violations: list[str] = []
    known_units = resolved_architecture.unit_names

    for include_root in include_roots:
        for file_path in iter_cs_files(include_root):
            if is_repo_excluded(file_path, exclude_globs, REPO_ROOT):
                continue

            source_unit = resolved_architecture.unit_for_path(file_path, REPO_ROOT)
            if source_unit is None:
                continue

            denied_units = known_units - resolved_architecture.allowed_dependency_names(source_unit)
            if not denied_units:
                continue

            text = file_path.read_text(encoding="utf-8", errors="ignore")
            masked = mask_comments_and_strings(text)
            for match in USING_DIRECTIVE_PATTERN.finditer(masked):
                namespace = match.group("namespace")
                target_unit = resolved_architecture.unit_for_namespace(namespace)
                if target_unit is None or target_unit.name not in denied_units:
                    continue

                line_number = text.count("\n", 0, match.start()) + 1
                violations.append(
                    f"{to_repo_path(file_path)}:{line_number}: "
                    f"{source_unit.name} code must not depend on {target_unit.name} namespace '{namespace}'."
                )

    return violations


def read_project_references(project_path: Path) -> list[tuple[str, int]]:
    text = project_path.read_text(encoding="utf-8", errors="ignore")
    references: list[tuple[str, int]] = []

    try:
        root = parse_safe_xml_text(text)
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


def parse_safe_xml_text(text: str) -> ET.Element:
    if len(text.encode("utf-8")) > MAX_PROJECT_XML_BYTES:
        raise ValueError(f"Project XML exceeds the {MAX_PROJECT_XML_BYTES} byte safety limit")
    if UNSAFE_XML_DECLARATION_PATTERN.search(text):
        raise ValueError("Project XML must not contain DTD or entity declarations")

    # ElementTree does not fetch external resources, while the declaration check
    # above prevents DTD-based entity expansion before parsing untrusted project files.
    return ET.fromstring(text)


def find_project_reference_line(text: str, include: str) -> int:
    include_index = text.find(include)
    if include_index < 0:
        return 1
    return text.count("\n", 0, include_index) + 1


def layer_for_path(path: Path, architecture: ArchitectureConfig | None = None) -> str | None:
    resolved_architecture = architecture or architecture_from_layer_rules(DEFAULT_LAYER_RULES)
    unit = resolved_architecture.unit_for_path(path, REPO_ROOT)
    return unit.name if unit is not None else None


def allowed_dependency_layers(source_layer: str, layer_rules: dict[str, list[str]]) -> set[str]:
    return {source_layer, *layer_rules.get(source_layer, [])}


def first_namespace_segment(namespace: str) -> str:
    return namespace.split(".", 1)[0]


def to_repo_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


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

    try:
        include_root_texts, exclude_globs, architecture = load_architectural_boundaries_config(
            Path(args.policy_path)
        )
    except ValueError as ex:
        print(f"Architectural boundary check failed: {ex}", file=sys.stderr)
        return 2
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

    try:
        violations = validate_architectural_boundaries(
            include_roots=include_roots,
            exclude_globs=exclude_globs,
            architecture=architecture,
        )
    except ValueError as ex:
        print(f"Architectural boundary check failed: {ex}", file=sys.stderr)
        return 1

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
