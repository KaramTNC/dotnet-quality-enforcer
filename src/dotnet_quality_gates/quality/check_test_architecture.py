from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


REPO_ROOT = Path(os.environ.get("DOTNET_QUALITY_REPO_ROOT", Path.cwd())).resolve()
DEFAULT_POLICY_PATH = REPO_ROOT / ".quality" / "quality_policy.json"

ONION_LAYERS = ("Domain", "Application", "Infrastructure", "Presentation")
TEST_SUITE_ROOTS = ("Unit", "Integration", "EndToEnd")

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
    if not policy_path.exists():
        return {}

    try:
        raw_policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        print(
            f"Warning: failed to read policy file '{policy_path}': {ex}. "
            "Falling back to discovered project mappings only.",
            file=sys.stderr,
        )
        return {}

    test_architecture = raw_policy.get("test_architecture", {})
    if not isinstance(test_architecture, dict):
        return {}

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


def to_repo_path(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def is_skipped_path(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def iter_cs_files(root: Path) -> list[Path]:
    if not root.exists():
        return []

    files: list[Path] = []
    for path in root.rglob("*.cs"):
        if is_skipped_path(path):
            continue
        if path.name in SKIP_FILE_NAMES:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.as_posix().lower())


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


def discover_project_mappings(repo_root: Path, extra_mappings: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
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


def validate_test_file_locations(repo_root: Path) -> list[str]:
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


def validate_integration_test_naming(test_project: str, test_dir: Path) -> list[str]:
    if not test_project.startswith("test/Integration"):
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--policy-path",
        default=str(DEFAULT_POLICY_PATH),
        help="Path to code quality policy JSON for optional additional mappings.",
    )
    args = parser.parse_args()

    extra_mappings = load_project_mappings(Path(args.policy_path))
    project_mappings = discover_project_mappings(REPO_ROOT, extra_mappings)
    if not project_mappings:
        print("Test architecture check failed: no active test project mappings discovered.", file=sys.stderr)
        return 1

    errors: list[str] = validate_test_file_locations(REPO_ROOT)

    for test_project, source_projects in project_mappings.items():
        test_dir = REPO_ROOT / test_project
        source_dirs = [REPO_ROOT / source_project for source_project in source_projects]

        if not test_dir.exists() or not has_source_files(test_dir):
            continue

        for source_project, source_dir in zip(source_projects, source_dirs):
            if not source_dir.exists():
                errors.append(f"Missing mapped source directory for {test_project}: {source_project}")

        if test_dir.exists():
            errors.extend(validate_integration_test_naming(test_project, test_dir))

    if errors:
        print("Test architecture check failed.", file=sys.stderr)
        for error in errors:
            print(f" - {error}", file=sys.stderr)
        return 1

    print("Test architecture check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
