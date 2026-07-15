from __future__ import annotations

import sys
import unittest
from pathlib import Path

from action_runner import build_command


class ActionRunnerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
