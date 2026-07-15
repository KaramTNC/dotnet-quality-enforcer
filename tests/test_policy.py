from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dotnet_quality_gates.policy import PolicyValidationError, validate_policy_file


class PolicyValidationTests(unittest.TestCase):
    def test_validates_known_policy_types(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "quality_policy.json"
            path.write_text(
                json.dumps(
                    {
                        "code_size": {"method_max_lines": 80},
                        "diff_quality": {"line_coverage_threshold": 0.9},
                        "architectural_boundaries": {"layer_rules": {"Application": ["Domain"]}},
                    }
                ),
                encoding="utf-8",
            )
            validate_policy_file(path)

    def test_reports_the_exact_invalid_policy_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "quality_policy.json"
            path.write_text('{"code_size": {"method_max_lines": "large"}}', encoding="utf-8")

            with self.assertRaisesRegex(PolicyValidationError, "code_size.method_max_lines"):
                validate_policy_file(path)

    def test_missing_policy_is_valid_and_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            validate_policy_file(Path(td) / "missing.json")


if __name__ == "__main__":
    unittest.main()
