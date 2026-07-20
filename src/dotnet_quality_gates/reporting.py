from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_GENERIC_FAILURE = re.compile(r"(?:gate|check) failed\.$", re.IGNORECASE)


def _clean_line(line: str) -> str:
    return _ANSI_ESCAPE.sub("", line).strip()


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_line(value)
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return result


def extract_violations(stdout: str, stderr: str) -> list[str]:
    """Extract the detail lines used by the existing violations output."""
    lines = [*stdout.splitlines(), *stderr.splitlines()]
    return _unique(
        line.strip()[2:].strip()
        for line in lines
        if _clean_line(line).startswith("- ")
    )


def extract_blocking_errors(returncode: int, stdout: str, stderr: str) -> list[str]:
    """Return actionable failure details across gates with different text formats."""
    if returncode == 0:
        return []

    stderr_lines = [_clean_line(line) for line in stderr.splitlines()]
    stdout_lines = [_clean_line(line) for line in stdout.splitlines()]
    bullet_errors = [line[2:].strip() for line in stderr_lines if line.startswith("- ")]
    direct_errors = [
        line
        for line in stderr_lines
        if line
        and not line.startswith("- ")
        and "warning:" not in line.lower()
        and not _GENERIC_FAILURE.search(line)
    ]

    # A few external tools write diagnostics to stdout. Only use those lines
    # when stderr did not provide actionable details, so warning lists from
    # gates such as code-size are never reported as blocking errors.
    if not bullet_errors and not direct_errors:
        bullet_errors = [line[2:].strip() for line in stdout_lines if line.startswith("- ")]
        direct_errors = [
            line
            for line in stdout_lines
            if line
            and not line.startswith("- ")
            and "warning:" not in line.lower()
            and "warnings:" not in line.lower()
            and not _GENERIC_FAILURE.search(line)
        ]

    # Detailed bullets are the most useful representation. Direct diagnostics
    # are retained for gates that report a single error without a bullet list.
    return _unique([*bullet_errors, *direct_errors]) or ["The quality gate failed without a diagnostic message."]


def add_result_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    """Add normalized diagnostics while preserving the schema-v1 envelope."""
    stdout = str(payload.get("stdout", ""))
    stderr = str(payload.get("stderr", ""))
    return {
        **payload,
        "violations": extract_violations(stdout, stderr),
        "blocking_errors": extract_blocking_errors(int(payload.get("returncode", 1)), stdout, stderr),
    }


def console_summary(payload: dict[str, Any]) -> str:
    command = str(payload.get("command", "quality"))
    status = str(payload.get("status", "failed")).upper()
    errors = list(payload.get("blocking_errors", []))
    lines = [f"[{'PASS' if status == 'PASSED' else 'FAIL'}] Quality gate: {command}"]
    if errors:
        lines.append(f"Blocking errors ({len(errors)}):")
        lines.extend(f"  - {error}" for error in errors)
    elif status == "PASSED":
        lines.append("No blocking errors.")
    return "\n".join(lines)


def markdown_summary(payload: dict[str, Any]) -> str:
    command = str(payload.get("command", "quality"))
    status = str(payload.get("status", "failed"))
    errors = list(payload.get("blocking_errors", []))
    warnings = list(payload.get("warnings", []))
    status_label = "Passed" if status == "passed" else "Failed"
    lines = [
        f"## .NET quality gate: `{command}`",
        "",
        f"**Status:** {status_label}  ",
        f"**Blocking errors:** {len(errors)}",
        "",
    ]
    if errors:
        lines.extend(["### Blocking errors", "", *[f"- {_markdown(error)}" for error in errors], ""])
    if warnings:
        lines.extend(["### Warnings", "", *[f"- {_markdown(warning)}" for warning in warnings], ""])
    return "\n".join(lines)


def _markdown(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")
