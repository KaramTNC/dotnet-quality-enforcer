from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable

Command = Callable[[], int]

COMMAND_NAMES = (
    "architectural-boundaries",
    "code-size",
    "diff-complexity",
    "diff-coverage",
    "namespace-layout",
    "public-api-documentation",
    "repo-coverage",
    "source-type-layout",
    "test-architecture",
    "test-conventions",
    "coverage-report",
)


def _commands() -> dict[str, tuple[str, Command]]:
    from .coverage import check_diff_coverage, check_repo_coverage, generate_coverage_report
    from .quality import (
        check_architectural_boundaries,
        check_code_size,
        check_diff_complexity,
        check_namespace_layout,
        check_public_api_documentation,
        check_source_type_layout,
        check_test_architecture,
        check_test_conventions,
    )

    return {
        "architectural-boundaries": (
            "Validate project and namespace dependency boundaries.",
            check_architectural_boundaries.main,
        ),
        "code-size": ("Validate C# method, type, and file size.", check_code_size.main),
        "diff-complexity": (
            "Validate changed-method complexity and CRAP scores.",
            check_diff_complexity.main,
        ),
        "diff-coverage": (
            "Validate changed-line and changed-branch coverage.",
            check_diff_coverage.main,
        ),
        "namespace-layout": (
            "Validate source namespaces against their paths.",
            check_namespace_layout.main,
        ),
        "public-api-documentation": (
            "Validate XML documentation for public C# APIs.",
            check_public_api_documentation.main,
        ),
        "repo-coverage": (
            "Validate Cobertura repository and package coverage.",
            check_repo_coverage.main,
        ),
        "source-type-layout": (
            "Validate C# source type/file layout.",
            check_source_type_layout.main,
        ),
        "test-architecture": (
            "Validate source and test project placement.",
            check_test_architecture.main,
        ),
        "test-conventions": (
            "Validate source-to-test naming and convention rules.",
            check_test_conventions.main,
        ),
        "coverage-report": (
            "Generate a ReportGenerator coverage report.",
            generate_coverage_report.main,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="dotnet-quality",
        description="Run configurable quality and coverage gates for a C#/.NET repository.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository working directory. Relative paths and reports are resolved from here.",
    )
    parser.add_argument("command", choices=COMMAND_NAMES, help="Quality command to run.")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="Arguments for the selected command.")
    args = parser.parse_args()

    repo_root = os.path.abspath(args.repo_root)
    if not os.path.isdir(repo_root):
        parser.error(f"Repository root does not exist: {repo_root}")

    commands = _commands()
    _, command = commands[args.command]
    original_argv = sys.argv
    original_cwd = os.getcwd()
    original_repo_root = os.environ.get("DOTNET_QUALITY_REPO_ROOT")
    try:
        os.environ["DOTNET_QUALITY_REPO_ROOT"] = repo_root
        os.chdir(repo_root)
        sys.argv = [f"dotnet-quality {args.command}", *args.arguments]
        return command()
    finally:
        sys.argv = original_argv
        os.chdir(original_cwd)
        if original_repo_root is None:
            os.environ.pop("DOTNET_QUALITY_REPO_ROOT", None)
        else:
            os.environ["DOTNET_QUALITY_REPO_ROOT"] = original_repo_root


if __name__ == "__main__":
    raise SystemExit(main())
