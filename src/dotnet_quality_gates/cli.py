from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    module: str
    description: str


def _commands() -> dict[str, CommandSpec]:
    return {
        "architectural-boundaries": CommandSpec(
            "dotnet_quality_gates.quality.check_architectural_boundaries",
            "Validate project and namespace dependency boundaries.",
        ),
        "code-size": CommandSpec(
            "dotnet_quality_gates.quality.check_code_size",
            "Validate C# method, type, and file size.",
        ),
        "diff-complexity": CommandSpec(
            "dotnet_quality_gates.quality.check_diff_complexity",
            "Validate changed-method complexity and CRAP scores.",
        ),
        "diff-coverage": CommandSpec(
            "dotnet_quality_gates.coverage.check_diff_coverage",
            "Validate changed-line and changed-branch coverage.",
        ),
        "namespace-layout": CommandSpec(
            "dotnet_quality_gates.quality.check_namespace_layout",
            "Validate source namespaces against their paths.",
        ),
        "public-api-documentation": CommandSpec(
            "dotnet_quality_gates.quality.check_public_api_documentation",
            "Validate XML documentation for public C# APIs.",
        ),
        "repo-coverage": CommandSpec(
            "dotnet_quality_gates.coverage.check_repo_coverage",
            "Validate Cobertura repository and package coverage.",
        ),
        "source-type-layout": CommandSpec(
            "dotnet_quality_gates.quality.check_source_type_layout",
            "Validate C# source type/file layout.",
        ),
        "test-architecture": CommandSpec(
            "dotnet_quality_gates.quality.check_test_architecture",
            "Validate source and test project placement.",
        ),
        "test-conventions": CommandSpec(
            "dotnet_quality_gates.quality.check_test_conventions",
            "Validate source-to-test naming and convention rules.",
        ),
        "coverage-report": CommandSpec(
            "dotnet_quality_gates.coverage.generate_coverage_report",
            "Generate a ReportGenerator coverage report.",
        ),
    }


# Kept as a small compatibility surface for callers that used the old constant.
COMMAND_NAMES = tuple(_commands())


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="dotnet-quality",
        description="Run configurable quality and coverage gates for a C#/.NET repository.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = _commands()
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository working directory. Relative paths and reports are resolved from here.",
    )
    parser.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Output format. JSON wraps command output and exit status for automation.",
    )
    command_descriptions = "\n".join(
        f"  {name:<28} {spec.description}" for name, spec in commands.items()
    )
    parser.add_argument("command", choices=tuple(commands), help="Quality command to run.")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="Arguments for the selected command.")
    parser.epilog = f"Available commands:\n{command_descriptions}"
    args = parser.parse_args()

    repo_root = os.path.abspath(args.repo_root)
    if not os.path.isdir(repo_root):
        parser.error(f"Repository root does not exist: {repo_root}")

    spec = commands[args.command]
    started_at = time.perf_counter()
    child_environment = os.environ.copy()
    # The child resolves its repository from its working directory. Do not let
    # a stale process-level override defeat the explicit --repo-root argument.
    child_environment.pop("DOTNET_QUALITY_REPO_ROOT", None)
    completed = subprocess.run(
        [sys.executable, "-m", spec.module, *args.arguments],
        cwd=repo_root,
        check=False,
        capture_output=args.output == "json",
        env=child_environment,
        text=True,
    )

    if args.output == "json":
        print(
            json.dumps(
                {
                    "command": args.command,
                    "repo_root": repo_root,
                    "returncode": completed.returncode,
                    "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                },
                ensure_ascii=False,
            )
        )
    else:
        if completed.stdout:
            sys.stdout.write(completed.stdout)
        if completed.stderr:
            sys.stderr.write(completed.stderr)

    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
