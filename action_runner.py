from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Mapping


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
            "warnings": [],
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    result = json.dumps(payload, ensure_ascii=False)
    set_output("result", result)
    set_output("status", str(payload.get("status", "failed")))
    set_output("returncode", str(payload.get("returncode", completed.returncode)))
    set_output("violations", json.dumps(payload.get("violations", []), ensure_ascii=False))
    set_output("warnings", json.dumps(payload.get("warnings", []), ensure_ascii=False))

    if payload.get("stdout"):
        print(payload["stdout"], end="")
    if payload.get("stderr"):
        print(payload["stderr"], end="", file=sys.stderr)
    return int(payload.get("returncode", completed.returncode))


if __name__ == "__main__":
    raise SystemExit(main())
