from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dotnet_quality_gates.context import PARSER_MODES, current_context

from .models import SourceClassInfo, TestClassInfo, TestMethodInfo


class RoslynError(RuntimeError):
    """Raised when strict Roslyn parsing cannot analyze a file."""


@dataclass(frozen=True)
class RoslynDiagnostic:
    diagnostic_id: str
    message: str
    line: int


@dataclass(frozen=True)
class RoslynFileAnalysis:
    source_classes: list[SourceClassInfo]
    test_classes: list[TestClassInfo]
    type_declarations: list[tuple[str, int, str]]
    diagnostics: list[RoslynDiagnostic]


def _configured_command() -> list[str] | None:
    configured = os.environ.get("DOTNET_QUALITY_ROSLYN_COMMAND", "").strip()
    if not configured:
        return None
    return shlex.split(configured, posix=os.name != "nt")


def parser_mode(value: str | None = None) -> str:
    selected = (value or os.environ.get("DOTNET_QUALITY_PARSER", "auto")).strip().lower()
    if selected not in PARSER_MODES:
        raise ValueError(f"Unsupported parser mode '{selected}'. Choose: {', '.join(PARSER_MODES)}")
    return selected


def analyze_csharp_file(path: Path, mode: str | None = None) -> RoslynFileAnalysis | None:
    """Analyze a C# file with the optional Roslyn helper.

    ``auto`` uses Roslyn when configured and otherwise uses the Python parser.
    ``python`` always uses the fallback parser. ``roslyn`` is strict and raises
    ``RoslynError`` when the helper is missing or fails.
    """
    selected_mode = parser_mode(mode)
    if selected_mode == "python":
        return None

    command = _configured_command()
    if not command:
        if selected_mode == "roslyn":
            raise RoslynError(
                "Roslyn parser was requested but DOTNET_QUALITY_ROSLYN_COMMAND is not configured"
            )
        return None

    try:
        completed = subprocess.run(
            [*command, "--file", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=current_context().command_timeout_seconds,
        )
        if completed.returncode != 0:
            if selected_mode == "roslyn":
                detail = completed.stderr.strip() or f"helper exited with code {completed.returncode}"
                raise RoslynError(f"Roslyn helper failed for '{path}': {detail}")
            return None
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            if selected_mode == "roslyn":
                raise RoslynError("Roslyn helper returned a non-object JSON response")
            return None
        return _parse_analysis(path, payload)
    except subprocess.TimeoutExpired as ex:
        if selected_mode == "roslyn":
            raise RoslynError(f"Roslyn helper timed out for '{path}'") from ex
        return None
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as ex:
        if selected_mode == "roslyn":
            raise RoslynError(f"Roslyn helper returned invalid output for '{path}': {ex}") from ex
        return None


def _parse_analysis(path: Path, payload: dict[str, object]) -> RoslynFileAnalysis:
    raw_types = payload.get("types", [])
    raw_diagnostics = payload.get("diagnostics", [])
    if not isinstance(raw_types, list) or not isinstance(raw_diagnostics, list):
        raise TypeError("Invalid Roslyn analysis response")

    source_classes: list[SourceClassInfo] = []
    test_classes: list[TestClassInfo] = []
    type_declarations: list[tuple[str, int, str]] = []
    for raw_type in raw_types:
        if not isinstance(raw_type, dict):
            raise TypeError("Invalid Roslyn type response")
        name = _required_string(raw_type, "name")
        kind = _required_string(raw_type, "kind")
        type_declarations.append((name, _required_int(raw_type, "line"), kind))
        source_classes.append(
            SourceClassInfo(
                name=name,
                path=path,
                line=_required_int(raw_type, "line"),
                exposed_methods=_string_set(raw_type, "exposedMethods"),
                is_partial=bool(raw_type.get("isPartial", False)),
                targetable_members=_string_set(raw_type, "targetableMembers"),
                requires_test_class=bool(raw_type.get("requiresTestClass", kind == "class")),
                base_types=_string_list(raw_type, "baseTypes"),
            )
        )
        if name.endswith("Tests"):
            test_classes.append(
                TestClassInfo(
                    name=name,
                    path=path,
                    line=_required_int(raw_type, "line"),
                    methods=_test_methods(raw_type),
                )
            )

    diagnostics = [
        RoslynDiagnostic(
            diagnostic_id=_required_string(raw_diagnostic, "id"),
            message=_required_string(raw_diagnostic, "message"),
            line=_required_int(raw_diagnostic, "line"),
        )
        for raw_diagnostic in raw_diagnostics
        if isinstance(raw_diagnostic, dict)
    ]
    return RoslynFileAnalysis(source_classes, test_classes, type_declarations, diagnostics)


def _required_string(value: dict[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str):
        raise TypeError(f"Roslyn response field '{key}' must be a string")
    return result


def _required_int(value: dict[str, object], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int):
        raise TypeError(f"Roslyn response field '{key}' must be an integer")
    return result


def _string_set(value: dict[str, object], key: str) -> set[str]:
    return set(_string_list(value, key))


def _string_list(value: dict[str, object], key: str) -> list[str]:
    result = value.get(key, [])
    if not isinstance(result, list) or not all(isinstance(item, str) for item in result):
        raise TypeError(f"Roslyn response field '{key}' must be a string list")
    return list(result)


def _test_methods(value: dict[str, object]) -> list[TestMethodInfo]:
    raw_methods = value.get("methods", [])
    if not isinstance(raw_methods, list):
        raise TypeError("Roslyn response field 'methods' must be a list")

    methods: list[TestMethodInfo] = []
    for raw_method in raw_methods:
        if not isinstance(raw_method, dict):
            raise TypeError("Invalid Roslyn method response")
        methods.append(
            TestMethodInfo(
                name=_required_string(raw_method, "name"),
                line=_required_int(raw_method, "line"),
                region=None,
                is_test_method=bool(raw_method.get("isTestMethod", False)),
                method_under_test_from_name=None,
            )
        )
    return methods
