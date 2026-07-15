from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from dotnet_quality_gates.context import current_context
from dotnet_quality_gates.quality.common import (  # noqa: E402
    is_repo_excluded,
    load_prefixed_baseline_violations,
    load_quality_section_config,
)
from dotnet_quality_gates.unit_test_conventions import REPO_ROOT, iter_cs_files  # noqa: E402

DEFAULT_POLICY_PATH = current_context().policy_path
DEFAULT_INCLUDE_ROOTS = ["src", "test"]
DEFAULT_EXCLUDE_GLOBS = [
    "**/*.Designer.cs",
    "**/*.g.cs",
    "**/*.g.i.cs",
    "**/GlobalUsings.cs",
    "**/AssemblyInfo.cs",
]

TYPE_DECLARATION_PATTERN = re.compile(
    r"\b(?:public|protected|internal|private|file)?\s*"
    r"(?:(?:abstract|sealed|static|partial|readonly|unsafe)\s+)*"
    r"(?:class|interface|struct|record|enum)\s+[A-Za-z_]\w*\b"
)
NAMESPACE_DECLARATION_PATTERN = re.compile(
    r"(?m)^(?P<prefix>\s*namespace\s+)"
    r"(?P<name>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)"
    r"(?P<suffix>\s*(?:;|\{)?\s*)$"
)


@dataclass(frozen=True)
class ProjectNamespaceInfo:
    project_root: Path
    root_namespace: str


def load_source_namespace_layout_config(policy_path: Path) -> tuple[list[str], list[str]]:
    return load_quality_section_config(
        policy_path,
        "source_namespace_layout",
        DEFAULT_INCLUDE_ROOTS,
        DEFAULT_EXCLUDE_GLOBS,
        "source namespace layout",
    )


def is_excluded(path: Path, exclude_globs: list[str]) -> bool:
    return is_repo_excluded(path, exclude_globs, REPO_ROOT)


def load_baseline_violations(path: Path) -> set[str]:
    return load_prefixed_baseline_violations(path)


def read_root_namespace(csproj_path: Path) -> str:
    text = csproj_path.read_text(encoding="utf-8", errors="ignore")

    root_namespace_match = re.search(r"<RootNamespace>\s*([^<]+?)\s*</RootNamespace>", text)
    if root_namespace_match:
        return root_namespace_match.group(1).strip()

    assembly_name_match = re.search(r"<AssemblyName>\s*([^<]+?)\s*</AssemblyName>", text)
    if assembly_name_match:
        return assembly_name_match.group(1).strip()

    return csproj_path.stem


def discover_project_namespaces(include_roots: list[Path]) -> list[ProjectNamespaceInfo]:
    projects: dict[Path, ProjectNamespaceInfo] = {}
    for include_root in include_roots:
        for csproj in include_root.rglob("*.csproj"):
            if any(part in {"bin", "obj"} for part in csproj.parts):
                continue
            project_root = csproj.parent.resolve()
            projects[project_root] = ProjectNamespaceInfo(
                project_root=project_root,
                root_namespace=read_root_namespace(csproj),
            )
    return sorted(projects.values(), key=lambda p: len(p.project_root.parts), reverse=True)


def resolve_project_for_file(
    file_path: Path,
    projects: list[ProjectNamespaceInfo],
) -> ProjectNamespaceInfo | None:
    for project in projects:
        try:
            file_path.relative_to(project.project_root)
            return project
        except ValueError:
            continue
    return None


def sanitize_namespace_segment(segment: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", segment.strip())
    if not cleaned:
        return "_"
    if cleaned[0].isdigit():
        return f"_{cleaned}"
    return cleaned


def build_expected_namespace(file_path: Path, project: ProjectNamespaceInfo) -> str:
    relative_dir = file_path.parent.relative_to(project.project_root)
    segments = [sanitize_namespace_segment(value) for value in relative_dir.parts if value]
    if not segments:
        return project.root_namespace
    return ".".join([project.root_namespace, *segments])


def find_namespace_declaration(text: str) -> tuple[str, int, int, int] | None:
    match = NAMESPACE_DECLARATION_PATTERN.search(text)
    if not match:
        return None
    namespace = match.group("name")
    name_start = match.start("name")
    name_end = match.end("name")
    line_number = text.count("\n", 0, match.start()) + 1
    return namespace, name_start, name_end, line_number


def has_type_declaration(text: str) -> bool:
    return TYPE_DECLARATION_PATTERN.search(text) is not None


def insert_file_scoped_namespace(text: str, namespace: str) -> str:
    lines = text.splitlines(keepends=True)
    insert_index = 0

    while insert_index < len(lines):
        stripped = lines[insert_index].strip()
        if (
            stripped == ""
            or stripped.startswith("//")
            or stripped.startswith("#")
            or stripped.startswith("using ")
            or stripped.startswith("global using ")
            or stripped.startswith("extern alias ")
        ):
            insert_index += 1
            continue
        break

    namespace_lines = [f"namespace {namespace};\n", "\n"]
    return "".join([*lines[:insert_index], *namespace_lines, *lines[insert_index:]])


def validate_source_namespace_layout(
    include_roots: list[Path],
    exclude_globs: list[str],
    fix: bool = False,
    target_paths: list[Path] | None = None,
) -> tuple[list[str], int]:
    violations: list[str] = []
    fixed_files = 0
    projects = discover_project_namespaces(include_roots)
    target_files: set[Path] | None = None

    if target_paths:
        resolved_files: set[Path] = set()
        for path in target_paths:
            absolute = path.resolve()
            if absolute.is_file() and absolute.suffix.lower() == ".cs":
                resolved_files.add(absolute)
            elif absolute.is_dir():
                for nested in absolute.rglob("*.cs"):
                    if any(part in {"bin", "obj"} for part in nested.parts):
                        continue
                    resolved_files.add(nested.resolve())
        target_files = resolved_files

    for include_root in include_roots:
        for file_path in iter_cs_files(include_root):
            absolute_file = file_path.resolve()
            if target_files is not None and absolute_file not in target_files:
                continue
            if is_excluded(file_path, exclude_globs):
                continue

            project = resolve_project_for_file(absolute_file, projects)
            if project is None:
                continue

            expected_namespace = build_expected_namespace(absolute_file, project)
            original_text = file_path.read_text(encoding="utf-8", errors="ignore")
            declaration = find_namespace_declaration(original_text)

            updated_text = original_text
            violation: str | None = None

            if declaration is not None:
                current_namespace, start, end, line_number = declaration
                if current_namespace != expected_namespace:
                    violation = (
                        f"{file_path.relative_to(REPO_ROOT)}:{line_number}: "
                        f"Namespace '{current_namespace}' does not match expected '{expected_namespace}'."
                    )
                    if fix:
                        updated_text = f"{updated_text[:start]}{expected_namespace}{updated_text[end:]}"
            else:
                if has_type_declaration(original_text):
                    violation = (
                        f"{file_path.relative_to(REPO_ROOT)}:1: "
                        f"Missing namespace declaration. Expected '{expected_namespace}'."
                    )
                    if fix:
                        updated_text = insert_file_scoped_namespace(original_text, expected_namespace)

            if fix and updated_text != original_text:
                file_path.write_text(updated_text, encoding="utf-8")
                fixed_files += 1
                continue

            if violation is not None:
                violations.append(violation)

    return violations, fixed_files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate source namespace declarations based on project-relative file paths."
    )
    parser.add_argument(
        "--policy-path",
        default=str(DEFAULT_POLICY_PATH),
        help="Path to code quality policy JSON.",
    )
    parser.add_argument(
        "--baseline-path",
        default=str(REPO_ROOT / ".quality" / "baselines" / "source_namespace_layout_baseline.txt"),
        help="Path to a baseline file with one known violation per line prefixed by '- '.",
    )
    parser.add_argument(
        "--max-violations",
        type=int,
        default=250,
        help="Maximum number of violations to print before truncating output.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Automatically rewrite namespace declarations to expected values.",
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        default=None,
        help="Optional list of file or directory paths to scope the check/fix to.",
    )
    args = parser.parse_args()

    include_root_texts, exclude_globs = load_source_namespace_layout_config(Path(args.policy_path))
    include_roots: list[Path] = []
    for include_root_text in include_root_texts:
        include_root = (REPO_ROOT / include_root_text).resolve()
        if include_root.exists():
            include_roots.append(include_root)
        else:
            print(f"Warning: include root not found and skipped: {include_root_text}", file=sys.stderr)

    if not include_roots:
        print("Source namespace layout check failed: no valid include roots found.", file=sys.stderr)
        return 1

    target_paths = None
    if args.paths:
        target_paths = [(REPO_ROOT / path).resolve() if not Path(path).is_absolute() else Path(path).resolve() for path in args.paths]

    violations, fixed_files = validate_source_namespace_layout(
        include_roots=include_roots,
        exclude_globs=exclude_globs,
        fix=args.fix,
        target_paths=target_paths,
    )

    if args.fix:
        print(f"Source namespace layout fix completed. Updated {fixed_files} file(s).")
        return 0

    baseline_violations = load_baseline_violations(Path(args.baseline_path))
    if baseline_violations:
        violations = [violation for violation in violations if violation not in baseline_violations]

    if violations:
        print("Source namespace layout check failed.", file=sys.stderr)
        displayed = violations[: args.max_violations]
        for violation in displayed:
            print(f" - {violation}", file=sys.stderr)
        if len(violations) > len(displayed):
            remaining = len(violations) - len(displayed)
            print(f" - ... {remaining} additional violations omitted", file=sys.stderr)
        return 1

    print("Source namespace layout check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
