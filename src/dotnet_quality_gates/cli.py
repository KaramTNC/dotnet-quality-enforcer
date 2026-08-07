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
from dotnet_quality_gates.languages import LANGUAGE_MODES, normalize_language, supported_language_help
from dotnet_quality_gates.policy import PolicyValidationError, validate_policy_file
from dotnet_quality_gates.reporting import add_result_diagnostics


@dataclass(frozen=True)
class CommandSpec:
    module: str
    description: str


def _commands() -> dict[str, CommandSpec]:
    return {
        "architectural-boundaries": CommandSpec("dotnet_quality_gates.quality.check_architectural_boundaries", "Validate project and namespace dependency boundaries."),
        "code-size": CommandSpec("dotnet_quality_gates.quality.check_code_size", "Validate method, type, and file size through a language adapter."),
        "diff-complexity": CommandSpec("dotnet_quality_gates.quality.check_diff_complexity", "Validate changed-method complexity and CRAP scores."),
        "diff-coverage": CommandSpec("dotnet_quality_gates.coverage.check_diff_coverage", "Validate changed-line and changed-branch coverage."),
        "namespace-layout": CommandSpec("dotnet_quality_gates.quality.check_namespace_layout", "Validate source namespaces against their paths."),
        "public-api-documentation": CommandSpec("dotnet_quality_gates.quality.check_public_api_documentation", "Validate XML documentation for public C# APIs."),
        "repo-coverage": CommandSpec("dotnet_quality_gates.coverage.check_repo_coverage", "Validate Cobertura repository and package coverage."),
        "source-type-layout": CommandSpec("dotnet_quality_gates.quality.check_source_type_layout", "Validate source type/file layout through a language adapter."),
        "test-architecture": CommandSpec("dotnet_quality_gates.quality.check_test_architecture", "Validate source and test project placement."),
        "test-conventions": CommandSpec("dotnet_quality_gates.quality.check_test_conventions", "Validate source-to-test naming and convention rules."),
        "coverage-report": CommandSpec("dotnet_quality_gates.coverage.generate_coverage_report", "Generate a ReportGenerator coverage report."),
    }


COMMAND_NAMES = tuple(_commands())
JSON_SCHEMA_VERSION = 1
ROSLYN_COMMANDS = frozenset({"source-type-layout", "test-conventions"})


def _build_parser(commands: dict[str, CommandSpec]) -> argparse.ArgumentParser:
    environment_parser = os.environ.get("DOTNET_QUALITY_PARSER", "auto").strip().lower() or "auto"
    if environment_parser not in PARSER_MODES:
        environment_parser = "auto"
    parser = argparse.ArgumentParser(
        prog="dotnet-quality",
        description="Run configurable quality and coverage gates for supported source languages.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", default=".", help="Repository working directory. Relative paths and reports are resolved from here.")
    parser.add_argument("--output", choices=("text", "json"), default="text", help="Output format. JSON wraps command output and exit status for automation.")
    parser.add_argument("--policy-path", default=None, help="Quality policy JSON path. Overrides the repository default for the child command.")
    parser.add_argument("--parser", choices=PARSER_MODES, default=environment_parser, help="C# parser mode: auto uses Roslyn when configured, python forces the fallback parser, roslyn requires Roslyn.")
    parser.add_argument("--language", choices=LANGUAGE_MODES, type=_parse_language_argument, default=_environment_language(), help=f"Source language adapter (default: csharp). Use auto for mixed-language discovery. Supported: {supported_language_help()}.")
    parser.add_argument("--roslyn-command", default=os.environ.get("DOTNET_QUALITY_ROSLYN_COMMAND"), help="Roslyn helper command used by --parser roslyn or auto.")
    parser.add_argument("--timeout", type=float, default=300.0, help="Maximum seconds for each child or external tool command.")
    parser.add_argument("command", choices=tuple(commands), help="Quality command to run.")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="Arguments for the selected command.")
    parser.epilog = "Available commands:\n" + "\n".join(f"  {name:<28} {spec.description}" for name, spec in commands.items())
    return parser


def _environment_language() -> str:
    try:
        return normalize_language(os.environ.get("DOTNET_QUALITY_LANGUAGE", "csharp"))
    except ValueError:
        return "csharp"


def _parse_language_argument(value: str) -> str:
    try:
        return normalize_language(value)
    except ValueError as ex:
        raise argparse.ArgumentTypeError(str(ex)) from ex


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


def _validate_parser_command(args: argparse.Namespace) -> str | None:
    if args.parser != "roslyn" or args.command in ROSLYN_COMMANDS:
        return None
    return "Parser mode 'roslyn' is currently supported only by: " + ", ".join(sorted(ROSLYN_COMMANDS)) + "."


def _build_child_invocation(
    args: argparse.Namespace,
    context: ExecutionContext,
    policy_path: Path,
) -> tuple[list[str], dict[str, str]]:
    child_arguments = list(args.arguments)
    if args.policy_path is not None and args.command != "coverage-report" and not any(
        argument == "--policy-path" or argument.startswith("--policy-path=") for argument in child_arguments
    ):
        child_arguments.extend(["--policy-path", str(policy_path)])
    child_environment = context.child_environment()
    if args.roslyn_command:
        child_environment["DOTNET_QUALITY_ROSLYN_COMMAND"] = args.roslyn_command
    else:
        child_environment.pop("DOTNET_QUALITY_ROSLYN_COMMAND", None)
    return child_arguments, child_environment


def _invoke_child(
    args: argparse.Namespace,
    commands: dict[str, CommandSpec],
    context: ExecutionContext,
    child_arguments: list[str],
    child_environment: dict[str, str],
    repo_root: Path,
    policy_path: Path,
) -> tuple[subprocess.CompletedProcess[str], float] | int:
    started_at = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", commands[args.command].module, *child_arguments],
            cwd=repo_root,
            check=False,
            capture_output=args.output == "json",
            env=child_environment,
            text=True,
            timeout=context.command_timeout_seconds,
        )
    except FileNotFoundError as ex:
        return _emit_failure(args.output, args.command, repo_root, policy_path, args.parser, args.language, str(ex), 127)
    except subprocess.TimeoutExpired:
        message = f"Command '{args.command}' exceeded the {context.command_timeout_seconds:g}s timeout."
        return _emit_failure(args.output, args.command, repo_root, policy_path, args.parser, args.language, message, 124)
    return completed, round((time.perf_counter() - started_at) * 1000, 3)


def _write_child_result(
    args: argparse.Namespace,
    repo_root: Path,
    policy_path: Path,
    completed: subprocess.CompletedProcess[str],
    duration_ms: float,
) -> None:
    if args.output == "json":
        payload = _result_payload(
            args.command,
            repo_root,
            policy_path,
            args.parser,
            args.language,
            completed.returncode,
            duration_ms,
            completed.stdout,
            completed.stderr,
        )
        print(json.dumps(payload, ensure_ascii=False))
    else:
        if completed.stdout:
            sys.stdout.write(completed.stdout)
        if completed.stderr:
            sys.stderr.write(completed.stderr)


def _run_child(args: argparse.Namespace, parser: argparse.ArgumentParser, commands: dict[str, CommandSpec]) -> int:
    repo_root = Path(args.repo_root).resolve()
    if not repo_root.is_dir():
        parser.error(f"Repository root does not exist: {repo_root}")
    policy_path = _policy_path(repo_root, args.policy_path, args.arguments)
    parser_error = _validate_parser_command(args)
    if parser_error is not None:
        return _emit_failure(args.output, args.command, repo_root, policy_path, args.parser, args.language, parser_error, 2)
    try:
        validate_policy_file(policy_path)
    except PolicyValidationError as ex:
        return _emit_failure(args.output, args.command, repo_root, policy_path, args.parser, args.language, str(ex), 2)
    context = ExecutionContext(repo_root, policy_path, args.parser, args.language, max(1.0, args.timeout))
    child_arguments, child_environment = _build_child_invocation(args, context, policy_path)
    result = _invoke_child(args, commands, context, child_arguments, child_environment, repo_root, policy_path)
    if isinstance(result, int):
        return result
    completed, duration_ms = result
    _write_child_result(args, repo_root, policy_path, completed, duration_ms)
    return completed.returncode


def main() -> int:
    commands = _commands()
    parser = _build_parser(commands)
    return _run_child(parser.parse_args(), parser, commands)


def _emit_failure(output: str, command: str, repo_root: Path, policy_path: Path, parser_mode: str, language: str, message: str, returncode: int) -> int:
    if output == "json":
        print(json.dumps(_result_payload(command, repo_root, policy_path, parser_mode, language, returncode, 0.0, "", message), ensure_ascii=False))
    else:
        print(message, file=sys.stderr)
    return returncode


def _result_payload(command: str, repo_root: Path, policy_path: Path, parser_mode: str, language: str, returncode: int, duration_ms: float, stdout: str, stderr: str) -> dict[str, Any]:
    warnings = [line.strip() for line in [*stdout.splitlines(), *stderr.splitlines()] if "warning:" in line.lower()]
    return add_result_diagnostics({
        "schema_version": JSON_SCHEMA_VERSION,
        "command": command,
        "repo_root": str(repo_root),
        "policy_path": str(policy_path),
        "parser": parser_mode,
        "language": language,
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
