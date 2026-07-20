from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Mapping

from dotnet_quality_gates.reporting import add_result_diagnostics, console_summary, markdown_summary


def parse_arguments(value: str) -> list[str]:
    return shlex.split(value, posix=os.name != "nt") if value.strip() else []


def build_command(inputs: Mapping[str, str], action_path: str) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "dotnet_quality_gates.cli",
        "--repo-root",
        inputs.get("repo_root", "."),
        "--output",
        "json",
        "--parser",
        inputs.get("parser", "auto"),
        "--timeout",
        inputs.get("timeout", "300"),
    ]
    policy_path = inputs.get("policy_path", "").strip()
    if policy_path:
        command.extend(["--policy-path", policy_path])

    roslyn_command = inputs.get("roslyn_command", "").strip()
    if not roslyn_command and inputs.get("install_roslyn", "false").lower() == "true":
        helper_path = Path(action_path) / "tools" / "roslyn-analyzer" / "bin" / "Release" / "net8.0" / "DotnetQualityRoslyn.dll"
        roslyn_command = f'dotnet "{helper_path}"'
    if roslyn_command:
        command.extend(["--roslyn-command", roslyn_command])

    command.append(inputs["command"])
    command.extend(parse_arguments(inputs.get("arguments", "")))
    return command


def set_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    delimiter = f"DOTNET_QUALITY_{uuid.uuid4().hex}"
    with open(output_path, "a", encoding="utf-8", newline="\n") as output:
        output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def _inputs() -> dict[str, str]:
    return {
        "command": os.environ.get("ACTION_COMMAND", ""),
        "arguments": os.environ.get("ACTION_ARGUMENTS", ""),
        "repo_root": os.environ.get("ACTION_REPO_ROOT", "."),
        "policy_path": os.environ.get("ACTION_POLICY_PATH", ""),
        "parser": os.environ.get("ACTION_PARSER", "auto"),
        "roslyn_command": os.environ.get("ACTION_ROSLYN_COMMAND", ""),
        "install_roslyn": os.environ.get("ACTION_INSTALL_ROSLYN", "false"),
        "timeout": os.environ.get("ACTION_TIMEOUT", "300"),
    }


def main() -> int:
    inputs = _inputs()
    command = build_command(inputs, os.environ.get("GITHUB_ACTION_PATH", "."))
    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    try:
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("quality command returned a non-object result")
    except (json.JSONDecodeError, ValueError):
        payload = {
            "schema_version": 1,
            "command": inputs["command"],
            "status": "failed",
            "returncode": completed.returncode,
            "violations": [],
            "blocking_errors": [],
            "warnings": [],
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    payload = add_result_diagnostics(payload)
    result = json.dumps(payload, ensure_ascii=False)
    set_output("result", result)
    set_output("status", str(payload.get("status", "failed")))
    set_output("returncode", str(payload.get("returncode", completed.returncode)))
    set_output("violations", json.dumps(payload.get("violations", []), ensure_ascii=False))
    set_output("blocking-errors", json.dumps(payload.get("blocking_errors", []), ensure_ascii=False))
    set_output("warnings", json.dumps(payload.get("warnings", []), ensure_ascii=False))

    print(console_summary(payload))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8", newline="\n") as summary:
                summary.write(markdown_summary(payload))
                summary.write("\n\n")
        except OSError as ex:
            print(f"Warning: unable to write GitHub step summary: {ex}", file=sys.stderr)

    if payload.get("stdout"):
        print("::group::Gate details")
        print(payload["stdout"], end="")
        if not payload["stdout"].endswith("\n"):
            print()
        print("::endgroup::")
    if payload.get("stderr"):
        print("::group::Gate diagnostics", file=sys.stderr)
        print(payload["stderr"], end="", file=sys.stderr)
        if not payload["stderr"].endswith("\n"):
            print(file=sys.stderr)
        print("::endgroup::", file=sys.stderr)
    return int(payload.get("returncode", completed.returncode))


if __name__ == "__main__":
    raise SystemExit(main())
