from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotnet_quality_gates.context import PARSER_MODES, ExecutionContext
from dotnet_quality_gates.policy import PolicyValidationError, validate_policy_file
from dotnet_quality_gates.reporting import add_result_diagnostics


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
JSON_SCHEMA_VERSION = 1
ROSLYN_COMMANDS = frozenset({"source-type-layout", "test-conventions"})


def main() -> int:
    environment_parser = os.environ.get("DOTNET_QUALITY_PARSER", "auto").strip().lower() or "auto"
    if environment_parser not in PARSER_MODES:
        environment_parser = "auto"
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
    parser.add_argument(
        "--policy-path",
        default=None,
        help="Quality policy JSON path. Overrides the repository default for the child command.",
    )
    parser.add_argument(
        "--parser",
        choices=PARSER_MODES,
        default=environment_parser,
        help="C# parser mode: auto uses Roslyn when configured, python forces the fallback parser, roslyn requires Roslyn.",
    )
    parser.add_argument(
        "--roslyn-command",
        default=os.environ.get("DOTNET_QUALITY_ROSLYN_COMMAND"),
        help="Roslyn helper command used by --parser roslyn or auto.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Maximum seconds for each child or external tool command.",
    )
    command_descriptions = "\n".join(
        f"  {name:<28} {spec.description}" for name, spec in commands.items()
    )
    parser.add_argument("command", choices=tuple(commands), help="Quality command to run.")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="Arguments for the selected command.")
    parser.epilog = f"Available commands:\n{command_descriptions}"
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.is_dir():
        parser.error(f"Repository root does not exist: {repo_root}")

    policy_path = _policy_path(repo_root, args.policy_path, args.arguments)
    if args.parser == "roslyn" and args.command not in ROSLYN_COMMANDS:
        return _emit_failure(
            args.output,
            args.command,
            repo_root,
            policy_path,
            args.parser,
            "Parser mode 'roslyn' is currently supported only by: "
            + ", ".join(sorted(ROSLYN_COMMANDS))
            + ".",
            2,
        )
    try:
        validate_policy_file(policy_path)
    except PolicyValidationError as ex:
        return _emit_failure(args.output, args.command, repo_root, policy_path, args.parser, str(ex), 2)

    context = ExecutionContext(
        repo_root=repo_root,
        policy_path=policy_path,
        parser_mode=args.parser,
        command_timeout_seconds=max(1.0, args.timeout),
    )

    spec = commands[args.command]
    child_arguments = list(args.arguments)
    if args.policy_path is not None and args.command != "coverage-report" and not any(
        argument == "--policy-path" or argument.startswith("--policy-path=")
        for argument in child_arguments
    ):
        child_arguments.extend(["--policy-path", str(policy_path)])
    started_at = time.perf_counter()
    child_environment = context.child_environment()
    if args.roslyn_command:
        child_environment["DOTNET_QUALITY_ROSLYN_COMMAND"] = args.roslyn_command
    else:
        child_environment.pop("DOTNET_QUALITY_ROSLYN_COMMAND", None)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", spec.module, *child_arguments],
            cwd=repo_root,
            check=False,
            capture_output=args.output == "json",
            env=child_environment,
            text=True,
            timeout=context.command_timeout_seconds,
        )
    except FileNotFoundError as ex:
        return _emit_failure(args.output, args.command, repo_root, policy_path, args.parser, str(ex), 127)
    except subprocess.TimeoutExpired:
        message = f"Command '{args.command}' exceeded the {context.command_timeout_seconds:g}s timeout."
        return _emit_failure(args.output, args.command, repo_root, policy_path, args.parser, message, 124)

    if args.output == "json":
        print(json.dumps(_result_payload(
            args.command,
            repo_root,
            policy_path,
            args.parser,
            completed.returncode,
            round((time.perf_counter() - started_at) * 1000, 3),
            completed.stdout,
            completed.stderr,
        ), ensure_ascii=False))
    else:
        if completed.stdout:
            sys.stdout.write(completed.stdout)
        if completed.stderr:
            sys.stderr.write(completed.stderr)

    return completed.returncode


def _policy_path(repo_root: Path, explicit: str | None, arguments: list[str]) -> Path:
    value = explicit
    for index, argument in enumerate(arguments):
        if argument == "--policy-path" and index + 1 < len(arguments):
            value = arguments[index + 1]
        elif argument.startswith("--policy-path="):
            value = argument.split("=", 1)[1]
    selected = value or ".quality/quality_policy.json"
    path = Path(selected)
    return (repo_root / path).resolve() if not path.is_absolute() else path.resolve()


def _emit_failure(
    output: str,
    command: str,
    repo_root: Path,
    policy_path: Path,
    parser_mode: str,
    message: str,
    returncode: int,
) -> int:
    if output == "json":
        print(json.dumps(_result_payload(
            command, repo_root, policy_path, parser_mode, returncode, 0.0, "", message
        ), ensure_ascii=False))
    else:
        print(message, file=sys.stderr)
    return returncode


def _result_payload(
    command: str,
    repo_root: Path,
    policy_path: Path,
    parser_mode: str,
    returncode: int,
    duration_ms: float,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    warnings = [line.strip() for line in [*stdout.splitlines(), *stderr.splitlines()] if "warning:" in line.lower()]
    return add_result_diagnostics({
        "schema_version": JSON_SCHEMA_VERSION,
        "command": command,
        "repo_root": str(repo_root),
        "policy_path": str(policy_path),
        "parser": parser_mode,
        "status": "passed" if returncode == 0 else "failed",
        "returncode": returncode,
        "duration_ms": duration_ms,
        "violations": [],
        "blocking_errors": [],
        "warnings": warnings,
        "stdout": stdout,
        "stderr": stderr,
    })


if __name__ == "__main__":
    raise SystemExit(main())
