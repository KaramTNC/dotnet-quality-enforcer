from __future__ import annotations

import os
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


if __name__ == "__main__":
    unittest.main()
