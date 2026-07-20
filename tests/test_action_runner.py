from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import action_runner
from action_runner import build_command


class ActionRunnerTests(unittest.TestCase):
    def test_parse_arguments_preserves_quoted_windows_paths(self) -> None:
        with patch.object(action_runner.os, "name", "nt"):
            arguments = action_runner.parse_arguments(
                r'--path "C:\Program Files\quality\source.cs" --name "value with spaces"'
            )

        self.assertEqual(
            arguments,
            ["--path", r"C:\Program Files\quality\source.cs", "--name", "value with spaces"],
        )

    def test_build_command_passes_global_options_before_the_gate(self) -> None:
        command = build_command(
            {
                "command": "code-size",
                "arguments": "--scope full --max-details 10",
                "repo_root": "repository",
                "policy_path": ".quality/policy.json",
                "parser": "python",
                "timeout": "120",
                "install_roslyn": "false",
            },
            str(Path("action")),
        )

        self.assertEqual(command[:2], [sys.executable, "-m"])
        self.assertEqual(command[command.index("--parser") + 1], "python")
        self.assertEqual(command[command.index("--timeout") + 1], "120")
        self.assertEqual(command[-4:], ["--scope", "full", "--max-details", "10"])

    def test_build_command_uses_the_bundled_roslyn_helper_when_requested(self) -> None:
        command = build_command(
            {
                "command": "source-type-layout",
                "arguments": "",
                "repo_root": ".",
                "policy_path": "",
                "parser": "roslyn",
                "timeout": "300",
                "install_roslyn": "true",
            },
            "C:/actions/quality",
        )

        roslyn_command = command[command.index("--roslyn-command") + 1]
        self.assertIn("DotnetQualityRoslyn.dll", roslyn_command)
        self.assertIn("dotnet", roslyn_command)

    @patch.object(action_runner.subprocess, "run")
    def test_main_writes_blocking_errors_to_outputs_and_job_summary(self, run_command: object) -> None:
        run_command.return_value = CompletedProcess(
            [],
            1,
            stdout=json.dumps(
                {
                    "schema_version": 1,
                    "command": "code-size",
                    "status": "failed",
                    "returncode": 1,
                    "violations": [],
                    "warnings": [],
                    "stdout": "",
                    "stderr": "Code size gate failed.\n - src/Example.cs:10: file is too large\n",
                }
            ),
            stderr="",
        )

        with tempfile.TemporaryDirectory() as td:
            output_path = Path(td) / "github-output"
            summary_path = Path(td) / "step-summary"
            with patch.dict(
                os.environ,
                {"GITHUB_OUTPUT": str(output_path), "GITHUB_STEP_SUMMARY": str(summary_path)},
                clear=False,
            ):
                result = action_runner.main()

            output = output_path.read_text(encoding="utf-8")
            summary = summary_path.read_text(encoding="utf-8")

        self.assertEqual(result, 1)
        self.assertIn("blocking-errors", output)
        self.assertIn("src/Example.cs:10: file is too large", output)
        self.assertIn("### Blocking errors", summary)

    @patch.object(action_runner.subprocess, "run")
    def test_main_protects_raw_gate_output_from_github_commands(self, run_command: object) -> None:
        run_command.return_value = CompletedProcess(
            [],
            1,
            stdout=json.dumps(
                {
                    "schema_version": 1,
                    "command": "code-size",
                    "status": "failed",
                    "returncode": 1,
                    "violations": [],
                    "blocking_errors": [],
                    "warnings": [],
                    "stdout": "::error::repository-controlled output\n",
                    "stderr": "::warning::repository-controlled diagnostic\n",
                }
            ),
            stderr="",
        )

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(action_runner.main(), 1)

        self.assertIn("::stop-commands::", stdout.getvalue())
        self.assertIn("::error::repository-controlled output", stdout.getvalue())
        self.assertIn("::stop-commands::", stderr.getvalue())
        self.assertIn("::warning::repository-controlled diagnostic", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
