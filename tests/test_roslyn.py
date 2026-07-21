from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from dotnet_quality_gates.unit_test_conventions import roslyn


class RoslynBridgeTests(unittest.TestCase):
    def test_bridge_is_opt_in(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(roslyn.analyze_csharp_file(Path("Example.cs")))

    def test_parses_roslyn_response_into_source_class_models(self) -> None:
        analysis = roslyn._parse_analysis(
            Path("src/Example.cs"),
            {
                "types": [
                    {
                        "name": "Example",
                        "kind": "class",
                        "line": 4,
                        "isPartial": True,
                        "exposedMethods": ["Run"],
                        "targetableMembers": ["Run", "Properties"],
                        "requiresTestClass": True,
                        "baseTypes": ["BaseExample"],
                    }
                ],
                "diagnostics": [
                    {"id": "CS1002", "message": "; expected", "line": 9},
                ],
            },
        )

        self.assertEqual(analysis.source_classes[0].name, "Example")
        self.assertEqual(analysis.source_classes[0].line, 4)
        self.assertEqual(analysis.source_classes[0].targetable_members, {"Run", "Properties"})
        self.assertEqual(analysis.diagnostics[0].diagnostic_id, "CS1002")

    def test_strict_mode_requires_a_configured_helper(self) -> None:
        with patch.dict(os.environ, {"DOTNET_QUALITY_PARSER": "roslyn"}, clear=True):
            with self.assertRaises(roslyn.RoslynError):
                roslyn.analyze_csharp_file(Path("Example.cs"))

    def test_configured_command_preserves_quoted_windows_paths(self) -> None:
        with (
            patch.object(roslyn.os, "name", "nt"),
            patch.dict(
                os.environ,
                {
                    "DOTNET_QUALITY_ROSLYN_COMMAND": (
                        r'"C:\Program Files\dotnet\dotnet.exe" '
                        r'"C:\quality tools\DotnetQualityRoslyn.dll"'
                    )
                },
                clear=True,
            ),
        ):
            command = roslyn._configured_command()

        self.assertEqual(
            command,
            [r"C:\Program Files\dotnet\dotnet.exe", r"C:\quality tools\DotnetQualityRoslyn.dll"],
        )

    def test_multiple_files_use_the_batch_protocol(self) -> None:
        files = [Path("src/One.cs").resolve(), Path("src/Two.cs").resolve()]
        payload = {
            "files": [
                {"path": str(path), "types": [], "diagnostics": []}
                for path in files
            ]
        }
        completed = subprocess.CompletedProcess(
            args=["roslyn"], returncode=0, stdout=json.dumps(payload), stderr=""
        )
        with (
            patch.dict(
                os.environ,
                {"DOTNET_QUALITY_PARSER": "roslyn", "DOTNET_QUALITY_ROSLYN_COMMAND": "roslyn"},
                clear=True,
            ),
            patch.object(roslyn.subprocess, "run", return_value=completed) as run,
        ):
            analyses = roslyn.analyze_csharp_files(files)

        self.assertEqual(set(analyses), set(files))
        command = run.call_args.args[0]
        self.assertIn("--files", command)
        self.assertIn(str(files[0]), command)


if __name__ == "__main__":
    unittest.main()
