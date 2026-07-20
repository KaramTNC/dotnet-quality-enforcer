from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
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
    return _split_windows_command_line(configured) if os.name == "nt" else shlex.split(configured)


def _split_windows_command_line(value: str) -> list[str]:
    """Split a Windows command line using Windows quoting and backslash rules."""
    if os.name == "nt":
        try:
            import ctypes

            argc = ctypes.c_int()
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            shell32.CommandLineToArgvW.argtypes = [
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_int),
            ]
            shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree.restype = ctypes.c_void_p
            argv = shell32.CommandLineToArgvW(value, ctypes.byref(argc))
            if not argv:
                raise OSError("CommandLineToArgvW failed")
            try:
                return [argv[index] for index in range(argc.value)]
            finally:
                kernel32.LocalFree(argv)
        except (AttributeError, OSError, TypeError):
            pass

    arguments: list[str] = []
    current: list[str] = []
    in_quotes = False
    argument_started = False
    index = 0
    while index < len(value):
        character = value[index]
        if character in " \t" and not in_quotes:
            if argument_started:
                arguments.append("".join(current))
                current = []
                argument_started = False
            index += 1
            continue
        if character == "\\":
            start = index
            while index < len(value) and value[index] == "\\":
                index += 1
            slash_count = index - start
            if index < len(value) and value[index] == '"':
                current.extend("\\" * (slash_count // 2))
                argument_started = True
                if slash_count % 2:
                    current.append('"')
                    index += 1
                else:
                    in_quotes = not in_quotes
                    index += 1
            else:
                current.extend("\\" * slash_count)
                argument_started = True
            continue
        if character == '"':
            in_quotes = not in_quotes
            argument_started = True
            index += 1
            continue
        current.append(character)
        argument_started = True
        index += 1

    if argument_started:
        arguments.append("".join(current))
    return arguments


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
    return analyze_csharp_files([path], mode).get(path.resolve())


def analyze_csharp_files(paths: list[Path], mode: str | None = None) -> dict[Path, RoslynFileAnalysis]:
    """Analyze multiple files with one helper process per batch."""
    selected_mode = parser_mode(mode)
    if selected_mode == "python" or not paths:
        return {}

    command = _configured_command()
    if not command:
        if selected_mode == "roslyn":
            raise RoslynError(
                "Roslyn parser was requested but DOTNET_QUALITY_ROSLYN_COMMAND is not configured"
            )
        return {}

    normalized_paths = [path.resolve() for path in paths]
    analyses: dict[Path, RoslynFileAnalysis] = {}
    try:
        for start in range(0, len(normalized_paths), 64):
            batch = normalized_paths[start : start + 64]
            mode_flag = "--file" if len(batch) == 1 else "--files"
            completed = subprocess.run(
                [*command, mode_flag, *[str(path) for path in batch]],
                check=False,
                capture_output=True,
                text=True,
                timeout=current_context().command_timeout_seconds,
            )
            if completed.returncode != 0:
                if selected_mode == "roslyn":
                    detail = completed.stderr.strip() or f"helper exited with code {completed.returncode}"
                    raise RoslynError(f"Roslyn helper failed: {detail}")
                print(
                    "Warning: Roslyn helper failed; falling back to the built-in C# parser.",
                    file=sys.stderr,
                )
                return {}

            payload = json.loads(completed.stdout)
            raw_files = [payload] if len(batch) == 1 else payload.get("files", [])
            if not isinstance(raw_files, list):
                raise TypeError("Roslyn batch response must contain a 'files' list")
            for raw_file in raw_files:
                if not isinstance(raw_file, dict):
                    raise TypeError("Invalid Roslyn file response")
                raw_path = str(raw_file.get("path", batch[0])) if len(batch) > 1 else str(batch[0])
                path = Path(raw_path).resolve()
                if path not in batch:
                    raise ValueError(f"Roslyn response returned an unexpected path: {raw_path}")
                analyses[path] = _parse_analysis(path, raw_file)

            if len(analyses) < start + len(batch):
                missing = sorted(set(batch) - set(analyses))
                raise ValueError(f"Roslyn response omitted files: {', '.join(map(str, missing))}")
        return analyses
    except subprocess.TimeoutExpired as ex:
        if selected_mode == "roslyn":
            raise RoslynError("Roslyn helper timed out") from ex
        print(
            "Warning: Roslyn helper timed out; falling back to the built-in C# parser.",
            file=sys.stderr,
        )
        return {}
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as ex:
        if selected_mode == "roslyn":
            raise RoslynError(f"Roslyn helper returned invalid output: {ex}") from ex
        print(
            f"Warning: Roslyn helper returned invalid output ({ex}); "
            "falling back to the built-in C# parser.",
            file=sys.stderr,
        )
        return {}


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
