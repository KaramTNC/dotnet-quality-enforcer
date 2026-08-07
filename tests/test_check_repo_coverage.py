from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dotnet_quality_gates.coverage import check_repo_coverage


class CheckRepoCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = check_repo_coverage

    def test_load_expected_packages_from_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "repo_coverage": {
                            "expected_packages": [" Main ", "Domain.Core", "Domain.Core"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            packages = self.mod.load_expected_packages(policy_path)

        self.assertEqual(packages, {"Main", "Domain.Core"})

    def test_load_expected_packages_falls_back_on_invalid_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text(
                json.dumps({"repo_coverage": {"expected_packages": "not-a-list"}}),
                encoding="utf-8",
            )
            packages = self.mod.load_expected_packages(policy_path)

        self.assertEqual(packages, set())

    def test_load_expected_packages_falls_back_for_non_object_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text("null", encoding="utf-8")

            packages = self.mod.load_expected_packages(policy_path)

        self.assertEqual(packages, set())

    def test_parse_merged_cobertura_filters_to_expected_packages(self) -> None:
        cobertura_xml = """<?xml version="1.0"?>
<coverage>
  <packages>
    <package name="Main">
      <classes>
        <class name="A" filename="src/A.cs">
          <lines>
            <line number="1" hits="1" branch="true" condition-coverage="50% (1/2)" />
          </lines>
        </class>
      </classes>
    </package>
    <package name="Other">
      <classes>
        <class name="B" filename="src/B.cs">
          <lines>
            <line number="1" hits="0" />
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""
        with tempfile.TemporaryDirectory() as td:
            xml_path = Path(td) / "coverage.xml"
            xml_path.write_text(cobertura_xml, encoding="utf-8")
            overall_line, overall_branch, package_stats, _ = self.mod.parse_merged_cobertura(
                xml_path,
                {"Main"},
            )

        self.assertEqual(overall_line, (1, 1))
        self.assertEqual(overall_branch, (1, 2))
        self.assertIn("Main", package_stats)
        self.assertIn("Other", package_stats)

    def test_main_fails_when_an_expected_package_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            coverage_path = root / "coverage.xml"
            coverage_path.write_text(
                "<coverage><packages><package name='Present'><classes>"
                "<class name='A' filename='src/A.cs'><lines><line number='1' hits='1'/>"
                "</lines></class></classes></package></packages></coverage>",
                encoding="utf-8",
            )
            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps({"repo_coverage": {"expected_packages": ["Present", "Missing"]}}),
                encoding="utf-8",
            )

            with patch(
                "sys.argv",
                [
                    "check_repo_coverage.py",
                    "--coverage",
                    str(coverage_path),
                    "--policy-path",
                    str(policy_path),
                    "--line-threshold",
                    "0",
                ],
            ):
                result = self.mod.main()

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
