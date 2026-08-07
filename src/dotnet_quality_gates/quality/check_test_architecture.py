from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from dotnet_quality_gates.architecture import (
    DEFAULT_LAYER_RULES,
    ArchitectureConfig,
    load_architecture_config,
)
from dotnet_quality_gates.context import current_context
from dotnet_quality_gates.quality.common import load_policy_object, policy_section
from dotnet_quality_gates.unit_test_conventions.discovery import iter_csharp_files

REPO_ROOT = current_context().repo_root
DEFAULT_POLICY_PATH = current_context().policy_path

ONION_LAYERS = tuple(DEFAULT_LAYER_RULES)
TEST_SUITE_ROOTS = ("Unit", "Integration", "EndToEnd")
DEFAULT_TEST_ROOTS = ("test/Unit", "test/Integration", "test/EndToEnd")
DEFAULT_INTEGRATION_TEST_ROOTS = ("test/Integration",)

SKIP_DIR_NAMES = {
    ".git",
    ".idea",
    ".vs",
    "bin",
    "obj",
    "TestResults",
}

SKIP_FILE_NAMES = {
    "AssemblyInfo.cs",
    "GlobalUsings.cs",
}

CLASS_DECLARATION_PATTERN = re.compile(
    r"\b(?:public|protected|internal|private|file)?\s*"
    r"(?:(?:abstract|sealed|static|partial)\s+)*"
    r"class\s+([A-Za-z_]\w*)\b"
)


def load_project_mappings(policy_path: Path) -> dict[str, list[str]]:
    """Load optional extra mappings from policy.

    Project discovery is intentionally source-of-truth based. Legacy
    ``project_mappings`` entries are accepted only when both the test directory
    and mapped source directories still exist, which keeps stale removed
    projects from becoming permanent CI failures.
    """
    test_architecture = policy_section(
        load_policy_object(policy_path, "test architecture"),
        "test_architecture",
    )

    mappings = test_architecture.get("additional_project_mappings")
    if mappings is None:
        mappings = test_architecture.get("project_mappings", {})
    if not isinstance(mappings, dict):
        print(
            "Warning: invalid test architecture project mappings in policy file. "
            "Falling back to discovered project mappings only.",
            file=sys.stderr,
        )
        return {}

    normalized: dict[str, list[str]] = {}
    for test_project, source_projects in mappings.items():
        if not isinstance(test_project, str) or not test_project.strip():
            continue
        if not isinstance(source_projects, list):
            continue
        source_values = [source.strip() for source in source_projects if isinstance(source, str) and source.strip()]
        test_dir = REPO_ROOT / test_project.strip()
        source_dirs = [REPO_ROOT / source for source in source_values]
        if source_values and test_dir.exists() and all(source_dir.exists() for source_dir in source_dirs):
            normalized[test_project.strip()] = source_values

    return normalized


def load_test_architecture_config(
    policy_path: Path,
    architecture: ArchitectureConfig | None = None,
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Load mappings and test roots, using broad roots for custom layouts."""
    resolved_architecture = architecture or load_architecture_config(policy_path)
    section = policy_section(load_policy_object(policy_path, "test architecture"), "test_architecture")
    mappings = load_project_mappings(policy_path)

    configured_test_roots = section.get("test_roots")
    if not isinstance(configured_test_roots, list):
        test_roots = ["test"] if resolved_architecture.is_custom else list(DEFAULT_TEST_ROOTS)
    else:
        test_roots = [value.strip() for value in configured_test_roots if isinstance(value, str) and value.strip()]

    configured_integration_roots = section.get("integration_test_roots")
    if not isinstance(configured_integration_roots, list):
        integration_roots = [] if resolved_architecture.is_custom else list(DEFAULT_INTEGRATION_TEST_ROOTS)
    else:
        integration_roots = [
            value.strip()
            for value in configured_integration_roots
            if isinstance(value, str) and value.strip()
        ]

    return mappings, test_roots, integration_roots


def to_repo_path(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def is_skipped_path(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def iter_cs_files(root: Path) -> list[Path]:
    return iter_csharp_files(root, SKIP_FILE_NAMES)


def has_source_files(root: Path) -> bool:
    return any(iter_cs_files(root))


def discover_source_roots(repo_root: Path) -> set[str]:
    source_roots: set[str] = set()
    src_root = repo_root / "src"
    for project_path in sorted(src_root.rglob("*.csproj"), key=lambda item: item.as_posix().lower()):
        if is_skipped_path(project_path):
            continue

        project_dir = project_path.parent
        try:
            relative_parts = project_dir.relative_to(src_root).parts
        except ValueError:
            continue
        if not relative_parts or relative_parts[0] not in ONION_LAYERS:
            continue

        if relative_parts[0] == "Presentation" and len(relative_parts) > 1:
            source_roots.add((src_root / "Presentation" / relative_parts[1]).relative_to(repo_root).as_posix())
        else:
            source_roots.add((src_root / relative_parts[0]).relative_to(repo_root).as_posix())

    for layer in ONION_LAYERS:
        if layer == "Presentation":
            continue

        layer_path = src_root / layer
        if has_source_files(layer_path):
            source_roots.add(layer_path.relative_to(repo_root).as_posix())

    return source_roots


def first_matching_source_roots(test_dir: Path, suite: str, source_roots: set[str]) -> list[str]:
    relative_parts = test_dir.relative_to(REPO_ROOT / "test" / suite).parts
    if not relative_parts:
        return []

    if suite == "EndToEnd":
        app_name = relative_parts[0]
        candidate = f"src/Presentation/{app_name}"
        return [candidate] if candidate in source_roots else []

    layer = relative_parts[0]
    if layer not in ONION_LAYERS:
        return []

    if layer == "Presentation" and len(relative_parts) > 1:
        project_candidate = f"src/Presentation/{relative_parts[1]}"
        if project_candidate in source_roots:
            return [project_candidate]

    candidate = f"src/{layer}"
    return [candidate] if candidate in source_roots else []


def discover_project_mappings(
    repo_root: Path,
    extra_mappings: dict[str, list[str]] | None = None,
    architecture: ArchitectureConfig | None = None,
) -> dict[str, list[str]]:
    if architecture is not None and architecture.is_custom:
        return dict(sorted((extra_mappings or {}).items()))

    source_roots = discover_source_roots(repo_root)
    mappings: dict[str, list[str]] = {}

    for suite in TEST_SUITE_ROOTS:
        suite_root = repo_root / "test" / suite
        if not suite_root.exists():
            continue

        candidate_dirs: set[Path] = set()
        for file_path in iter_cs_files(suite_root):
            relative_parts = file_path.relative_to(suite_root).parts
            if suite == "EndToEnd":
                if len(relative_parts) > 1:
                    candidate_dirs.add(suite_root / relative_parts[0])
                continue

            if not relative_parts or relative_parts[0] not in ONION_LAYERS:
                continue

            if relative_parts[0] == "Presentation" and len(relative_parts) > 2:
                candidate_dirs.add(suite_root / relative_parts[0] / relative_parts[1])
            else:
                candidate_dirs.add(suite_root / relative_parts[0])

        for test_dir in sorted(candidate_dirs, key=lambda item: item.as_posix().lower()):
            source_dirs = first_matching_source_roots(test_dir, suite, source_roots)
            if source_dirs:
                mappings[to_repo_path(test_dir)] = source_dirs

    for test_project, source_projects in sorted((extra_mappings or {}).items()):
        mappings[test_project] = source_projects

    return mappings


def validate_test_file_locations(
    repo_root: Path,
    architecture: ArchitectureConfig | None = None,
    project_mappings: dict[str, list[str]] | None = None,
    test_roots: list[str] | None = None,
) -> list[str]:
    if architecture is not None and architecture.is_custom:
        return validate_configured_test_file_locations(
            repo_root,
            project_mappings or {},
            test_roots or ["test"],
        )

    errors: list[str] = []
    source_roots = discover_source_roots(repo_root)

    for suite in TEST_SUITE_ROOTS:
        suite_root = repo_root / "test" / suite
        if not suite_root.exists():
            continue

        for file_path in iter_cs_files(suite_root):
            relative_parts = file_path.relative_to(suite_root).parts
            if suite == "EndToEnd":
                if len(relative_parts) == 1:
                    continue
                candidate = f"src/Presentation/{relative_parts[0]}"
                if candidate not in source_roots:
                    errors.append(
                        f"{to_repo_path(file_path)}: End-to-end tests must live under "
                        "test/EndToEnd/<PresentationProject> matching a source project."
                    )
                continue

            if not relative_parts or relative_parts[0] not in ONION_LAYERS:
                errors.append(
                    f"{to_repo_path(file_path)}: Test file must live under one of the onion layers: "
                    f"{', '.join(ONION_LAYERS)}."
                )
                continue

            source_dirs = first_matching_source_roots(file_path.parent, suite, source_roots)
            if not source_dirs:
                errors.append(
                    f"{to_repo_path(file_path)}: Test path does not map to an existing source onion layer/project."
                )

    return errors


def validate_configured_test_file_locations(
    repo_root: Path,
    project_mappings: dict[str, list[str]],
    test_roots: list[str],
) -> list[str]:
    errors: list[str] = []
    mapping_paths = {
        (repo_root / test_project).resolve(): (test_project, source_projects)
        for test_project, source_projects in project_mappings.items()
    }

    for test_root_text in test_roots:
        test_root = (repo_root / test_root_text).resolve()
        if not test_root.exists():
            continue
        for file_path in iter_cs_files(test_root):
            absolute_file = file_path.resolve()
            matching = [path for path in mapping_paths if is_path_within(absolute_file, path)]
            if matching:
                continue
            errors.append(
                f"{to_repo_path(file_path)}: Test file is not covered by any configured test project mapping."
            )

    return errors


def validate_integration_test_naming(
    test_project: str,
    test_dir: Path,
    integration_test_roots: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    integration_roots = integration_test_roots or DEFAULT_INTEGRATION_TEST_ROOTS
    normalized_project = test_project.replace("\\", "/").rstrip("/")
    if not any(
        normalized_project == root or normalized_project.startswith(f"{root.rstrip('/')}/")
        for root in integration_roots
    ):
        return []

    errors: list[str] = []

    for file_path in iter_cs_files(test_dir):
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        for class_match in CLASS_DECLARATION_PATTERN.finditer(text):
            class_name = class_match.group(1)
            if class_name.endswith("Tests") and not class_name.endswith("IntegrationTests"):
                errors.append(
                    f"{file_path.relative_to(REPO_ROOT).as_posix()}: "
                    f"Integration test class '{class_name}' must end with 'IntegrationTests'."
                )

            if class_name.endswith("IntegrationTests") and not file_path.name.endswith("IntegrationTests.cs"):
                errors.append(
                    f"{file_path.relative_to(REPO_ROOT).as_posix()}: "
                    f"File containing integration test class '{class_name}' must end with 'IntegrationTests.cs'."
                )

    return errors


def is_path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--policy-path",
        default=str(DEFAULT_POLICY_PATH),
        help="Path to code quality policy JSON for optional additional mappings.",
    )
    args = parser.parse_args()

    policy_path = Path(args.policy_path)
    try:
        architecture = load_architecture_config(policy_path)
        extra_mappings, test_roots, integration_test_roots = load_test_architecture_config(
            policy_path,
            architecture,
        )
    except ValueError as ex:
        print(f"Test architecture check failed: {ex}", file=sys.stderr)
        return 2
    project_mappings = discover_project_mappings(REPO_ROOT, extra_mappings, architecture)
    if not project_mappings:
        print("Test architecture check failed: no active test project mappings discovered.", file=sys.stderr)
        return 1

    errors: list[str] = validate_test_file_locations(
        REPO_ROOT,
        architecture=architecture,
        project_mappings=project_mappings,
        test_roots=test_roots,
    )

    for test_project, source_projects in project_mappings.items():
        test_dir = REPO_ROOT / test_project
        source_dirs = [REPO_ROOT / source_project for source_project in source_projects]

        if not test_dir.exists() or not has_source_files(test_dir):
            continue

        for source_project, source_dir in zip(source_projects, source_dirs):
            if not source_dir.exists():
                errors.append(f"Missing mapped source directory for {test_project}: {source_project}")

        if test_dir.exists():
            errors.extend(validate_integration_test_naming(test_project, test_dir, integration_test_roots))

    if errors:
        print("Test architecture check failed.", file=sys.stderr)
        for error in errors:
            print(f" - {error}", file=sys.stderr)
        return 1

    print("Test architecture check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
