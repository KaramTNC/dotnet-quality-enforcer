from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dotnet_quality_gates import cli


class CliTests(unittest.TestCase):
    def _run_cli(self, arguments: list[str]) -> tuple[int, dict[str, object]]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["dotnet-quality", *arguments]), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = cli.main()

        self.assertEqual(stderr.getvalue(), "")
        return result, json.loads(stdout.getvalue())

    def test_json_output_runs_from_explicit_repository_root_without_mutating_process_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_root = root / "src"
            source_root.mkdir()
            (source_root / "Example.cs").write_text(
                "namespace Example;\npublic class Example { }\n",
                encoding="utf-8",
            )

            original_cwd = os.getcwd()
            original_environment = os.environ.copy()
            result, payload = self._run_cli(
                [
                    "--repo-root",
                    str(root),
                    "--output",
                    "json",
                    "code-size",
                    "--scope",
                    "full",
                ]
            )

        self.assertEqual(result, 0)
        self.assertEqual(payload["command"], "code-size")
        self.assertEqual(payload["returncode"], 0)
        self.assertIn("Code size gate passed", payload["stdout"])
        self.assertEqual(payload["stderr"], "")
        self.assertEqual(os.getcwd(), original_cwd)
        self.assertEqual(dict(os.environ), original_environment)

    def test_json_output_preserves_command_failure_and_diagnostics(self) -> None:
        result, payload = self._run_cli(
            [
                "--repo-root",
                str(Path.cwd()),
                "--output",
                "json",
                "code-size",
                "--scope",
                "diff",
            ]
        )

        self.assertEqual(result, 1)
        self.assertEqual(payload["returncode"], 1)
        self.assertIn("--base is required", payload["stderr"])


if __name__ == "__main__":
    unittest.main()
