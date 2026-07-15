from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .constants import REPO_ROOT
from .models import SourceClassInfo, TestClassInfo
from .parsing import parse_test_method_name


COMPANION_TEST_SUFFIXES = (
    "Additional",
    "Advanced",
    "Compatibility",
    "Configuration",
    "Contract",
    "Decision",
    "DefaultBehavior",
    "Edge",
    "EdgeCase",
    "Interfaces",
    "Runtime",
    "State",
    "Unit",
    "Utility",
    "Workflow",
)

AGGREGATE_TEST_SUFFIXES = (
    "Combinator",
    "Indicator",
    "Interval",
    "Pattern",
    "Patterns",
    "ProfileStrategy",
)


def get_tested_name(test_class_name: str) -> str:
    return test_class_name[: -len("Tests")]


def resolve_source_name_for_test(
    tested_name: str,
    source_by_name: dict[str, list[SourceClassInfo]],
) -> str | None:
    if tested_name in source_by_name:
        return tested_name

    prefix_matches: list[str] = []
    for source_name in source_by_name:
        if tested_name.startswith(source_name):
            suffix = tested_name[len(source_name) :]
            if suffix.startswith("And") or suffix in COMPANION_TEST_SUFFIXES:
                prefix_matches.append(source_name)
        elif source_name.startswith(tested_name):
            suffix = source_name[len(tested_name) :]
            if suffix in ("Base", "Integration", "BrokerClient", "BrokerClientBase"):
                prefix_matches.append(source_name)

    if prefix_matches:
        return max(prefix_matches, key=len)

    return None


def is_allowed_unmapped_companion_test(
    tested_name: str,
    test_path: Path,
) -> bool:
    if "And" in tested_name:
        return True
    return tested_name.endswith(COMPANION_TEST_SUFFIXES + AGGREGATE_TEST_SUFFIXES)


def resolve_test_subject(
    test_method_name: str,
    source_name: str,
    targetable_members: set[str],
) -> str | None:
    method_under_test = parse_test_method_name(test_method_name)
    if method_under_test is None:
        return None

    if method_under_test == source_name:
        return method_under_test

    if method_under_test in targetable_members:
        return method_under_test

    parts = test_method_name.split("_")
    if len(parts) >= 3 and parts[0] == source_name and parts[1] in targetable_members:
        return parts[1]

    return method_under_test


def collect_inherited_targetable_members(
    source: SourceClassInfo,
    source_by_name: dict[str, list[SourceClassInfo]],
    visited: set[str] | None = None,
) -> set[str]:
    visited = visited or set()
    if source.name in visited:
        return set()

    visited.add(source.name)
    inherited_members: set[str] = set()
    for base_type in source.base_types or []:
        candidates = source_by_name.get(base_type, [])
        if len(candidates) != 1:
            continue
        base_source = candidates[0]
        inherited_members.update(base_source.targetable_members or base_source.exposed_methods)
        inherited_members.update(collect_inherited_targetable_members(base_source, source_by_name, visited))

    return inherited_members


def combine_partial_source_classes(source_classes: list[SourceClassInfo]) -> tuple[list[SourceClassInfo], list[str]]:
    combined: dict[str, list[SourceClassInfo]] = defaultdict(list)
    for source in source_classes:
        combined[source.name].append(source)

    result: list[SourceClassInfo] = []
    violations: list[str] = []

    for class_name, items in combined.items():
        if len(items) == 1:
            result.append(items[0])
            continue

        required_items = [item for item in items if item.requires_test_class]
        if len(required_items) == 0:
            result.append(items[0])
            continue
        if len(required_items) == 1:
            result.append(required_items[0])
            continue

        if all(item.is_partial for item in items):
            merged_methods: set[str] = set()
            merged_targetable_members: set[str] = set()
            merged_base_types: list[str] = []
            first = items[0]
            for item in items:
                merged_methods.update(item.exposed_methods)
                merged_targetable_members.update(item.targetable_members or item.exposed_methods)
                for base_type in item.base_types or []:
                    if base_type not in merged_base_types:
                        merged_base_types.append(base_type)
            result.append(
                SourceClassInfo(
                    name=class_name,
                    path=first.path,
                    line=first.line,
                    exposed_methods=merged_methods,
                    is_partial=True,
                    targetable_members=merged_targetable_members,
                    requires_test_class=any(item.requires_test_class for item in items),
                    base_types=merged_base_types,
                )
            )
            continue

        # Duplicate type names can exist in different onion layers. Keep all of
        # them indexed so path-based test placement can still disambiguate.
        result.extend(items)

    return result, violations


def build_include_to_test_root_map(
    include_roots: list[Path],
    unit_test_root: Path,
) -> tuple[list[tuple[Path, Path]], list[str]]:
    mappings: list[tuple[Path, Path]] = []
    violations: list[str] = []

    for include_root in include_roots:
        try:
            include_relative = include_root.relative_to(REPO_ROOT)
        except ValueError:
            violations.append(
                f"Include root '{include_root}' is outside repository root and cannot be mapped to unit-test root."
            )
            continue

        include_parts = include_relative.parts
        if len(include_parts) < 2 or include_parts[0] != "src":
            violations.append(
                f"Include root '{include_relative.as_posix()}' must start with 'src/' to support mirrored test architecture."
            )
            continue

        test_root = unit_test_root.joinpath(*include_parts[1:])
        mappings.append((include_root, test_root))

    mappings.sort(key=lambda pair: len(pair[0].parts), reverse=True)
    return mappings, violations


def resolve_expected_test_directory(
    source_path: Path,
    include_to_test_root: list[tuple[Path, Path]],
) -> Path | None:
    for include_root, test_root in include_to_test_root:
        try:
            relative_directory = source_path.parent.relative_to(include_root)
            return test_root / relative_directory
        except ValueError:
            continue
    return None


def validate_conventions(
    source_classes: list[SourceClassInfo],
    test_classes: list[TestClassInfo],
    include_to_test_root: list[tuple[Path, Path]],
) -> list[str]:
    violations: list[str] = []

    source_by_name: dict[str, list[SourceClassInfo]] = defaultdict(list)
    for source in source_classes:
        source_by_name[source.name].append(source)

    test_by_source_name: dict[str, list[TestClassInfo]] = defaultdict(list)
    for test_class in test_classes:
        tested_name = get_tested_name(test_class.name)
        source_name = resolve_source_name_for_test(tested_name, source_by_name) or tested_name
        test_by_source_name[source_name].append(test_class)

    for class_name, sources in sorted(source_by_name.items()):
        tests = test_by_source_name.get(class_name, [])
        if len(sources) != 1:
            continue
        source = sources[0]
        if not source.requires_test_class:
            continue
        if len(tests) == 0:
            violations.append(
                f"{source.path.relative_to(REPO_ROOT)}:{source.line}: "
                f"Missing unit test class '{class_name}Tests' for source class '{class_name}'."
            )

    for test_class in test_classes:
        tested_name = get_tested_name(test_class.name)
        source_name = resolve_source_name_for_test(tested_name, source_by_name) or tested_name
        source_candidates = source_by_name.get(source_name, [])

        if len(source_candidates) != 1:
            if len(source_candidates) == 0:
                if is_allowed_unmapped_companion_test(tested_name, test_class.path):
                    continue
                violations.append(
                    f"{test_class.path.relative_to(REPO_ROOT)}:{test_class.line}: "
                    f"Test class '{test_class.name}' has no matching source class '{source_name}'."
            )
            continue

        if source_name != tested_name:
            continue

        source = source_candidates[0]
        if not source.requires_test_class:
            continue
        targetable_members = set(source.targetable_members or source.exposed_methods)
        targetable_members.update(collect_inherited_targetable_members(source, source_by_name))

        for method in test_class.methods:
            if method.is_test_method:
                test_subject = resolve_test_subject(method.name, source.name, targetable_members)
                if test_subject is None:
                    violations.append(
                        f"{test_class.path.relative_to(REPO_ROOT)}:{method.line}: "
                        f"Unit test method '{method.name}' must include a target and descriptive suffix, "
                        "for example '<MemberName>_ReturnsValue', '<MemberName>_WhenCondition', "
                        "or '<ClassName>_<Behavior>'."
                    )
                    continue

                if test_subject != source.name and targetable_members and test_subject not in targetable_members:
                    violations.append(
                        f"{test_class.path.relative_to(REPO_ROOT)}:{method.line}: "
                        f"Test method '{method.name}' targets '{test_subject}', "
                        f"which is not a declared member or class-level behavior on '{source.name}'."
                    )

    for source in source_classes:
        if not source.requires_test_class:
            continue
        tests = test_by_source_name.get(source.name, [])
        if not tests:
            continue

        expected_directory = resolve_expected_test_directory(source.path, include_to_test_root)
        if expected_directory is None:
            continue

        for test in tests:
            if get_tested_name(test.name) != source.name:
                continue

            if len(source_by_name.get(source.name, [])) > 1 and test.path.parent.resolve() != expected_directory.resolve():
                continue

            if test.path.parent.resolve() != expected_directory.resolve():
                try:
                    expected_relative = expected_directory.relative_to(REPO_ROOT).as_posix()
                except ValueError:
                    expected_relative = expected_directory.as_posix()

                violations.append(
                    f"{test.path.relative_to(REPO_ROOT)}:{test.line}: "
                    f"Test class '{test.name}' for source '{source.name}' must be located in '{expected_relative}' "
                    f"to mirror source architecture."
                )

    return violations
