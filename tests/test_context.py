from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from dotnet_quality_gates.context import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ExecutionContext,
    current_context,
    resolve_command_path,
)


class ExecutionContextTests(unittest.TestCase):
    def test_from_environment_resolves_relative_values_from_the_requested_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td) / "working directory"
            cwd.mkdir()
            context = ExecutionContext.from_environment(
                {
                    "DOTNET_QUALITY_REPO_ROOT": "repository",
                    "DOTNET_QUALITY_POLICY_PATH": "config/policy.json",
                    "DOTNET_QUALITY_PARSER": "PYTHON",
                    "DOTNET_QUALITY_COMMAND_TIMEOUT": "12.5",
                },
                cwd=cwd,
            )

        self.assertEqual(context.repo_root, (cwd / "repository").resolve())
        self.assertEqual(context.policy_path, (cwd / "repository/config/policy.json").resolve())
        self.assertEqual(context.parser_mode, "python")
        self.assertEqual(context.command_timeout_seconds, 12.5)

    def test_invalid_environment_values_are_safe(self) -> None:
        context = ExecutionContext.from_environment(
            {
                "DOTNET_QUALITY_PARSER": "unsupported",
                "DOTNET_QUALITY_COMMAND_TIMEOUT": "nan",
            },
            cwd=Path.cwd(),
        )

        self.assertEqual(context.parser_mode, "auto")
        self.assertEqual(context.command_timeout_seconds, DEFAULT_COMMAND_TIMEOUT_SECONDS)

    def test_child_environment_does_not_mutate_or_require_process_environment(self) -> None:
        context = ExecutionContext(Path("C:/repo"), Path("C:/repo/policy.json"), "roslyn", 4.0)
        environment = {"EXAMPLE": "value"}

        child = context.child_environment(environment)

        self.assertEqual(environment, {"EXAMPLE": "value"})
        self.assertEqual(child["EXAMPLE"], "value")
        self.assertEqual(child["DOTNET_QUALITY_REPO_ROOT"], str(context.repo_root))
        self.assertEqual(child["DOTNET_QUALITY_PARSER"], "roslyn")

    def test_current_context_and_command_paths_use_call_time_environment(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            environment = dict(os.environ)
            environment["DOTNET_QUALITY_REPO_ROOT"] = str(root)
            environment.pop("DOTNET_QUALITY_POLICY_PATH", None)

            context = current_context(environment, cwd=Path(td))

        self.assertEqual(context.repo_root, root.resolve())
        self.assertEqual(resolve_command_path("src/Example.cs", root), root / "src/Example.cs")


if __name__ == "__main__":
    unittest.main()
