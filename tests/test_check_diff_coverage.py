from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from dotnet_quality_gates.coverage import check_diff_coverage


class CheckDiffCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = check_diff_coverage

    def test_parse_changed_lines_parses_new_ranges(self) -> None:
        diff_text = """diff --git a/src/Foo.cs b/src/Foo.cs
+++ b/src/Foo.cs
@@ -1,0 +2,2 @@
@@ -10,1 +12,1 @@
diff --git a/src/Bar.cs b/src/Bar.cs
+++ b/src/Bar.cs
@@ -4,0 +5,0 @@
@@ -7,1 +8,3 @@
"""
        changed = self.mod.parse_changed_lines(diff_text)
        self.assertEqual(changed["src/Foo.cs"], {2, 3, 12})
        self.assertEqual(changed["src/Bar.cs"], {8, 9, 10})

    def test_parse_coverage_uses_max_hits_per_line(self) -> None:
        cobertura_xml = """<?xml version="1.0"?>
<coverage>
  <packages>
    <package name="Main">
      <classes>
        <class name="A" filename="src/Foo.cs">
          <lines>
            <line number="10" hits="0" />
          </lines>
        </class>
        <class name="B" filename="src/Foo.cs">
          <lines>
            <line number="10" hits="3" />
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
            coverage = self.mod.parse_coverage(xml_path)

        self.assertEqual(coverage["src/Foo.cs"][10], 3)

    def test_load_diff_coverage_config_from_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "diff_quality": {
                            "line_coverage_threshold": 1.0,
                            "branch_coverage_threshold": 1.0,
                            "max_files_for_gate": 40,
                        }
                    }
                ),
                encoding="utf-8",
            )
            config = self.mod.load_diff_coverage_config(policy_path)

        self.assertEqual(config, (1.0, 1.0, 40))

    def test_resolve_coverage_file_supports_suffix_match(self) -> None:
        coverage = {
            "src/Foo.cs": {1: 1},
            "C:/agent/work/repo/src/Bar.cs": {2: 1},
        }
        exact = self.mod.resolve_coverage_file("src/Foo.cs", coverage)
        suffix = self.mod.resolve_coverage_file("src/Bar.cs", coverage)
        missing = self.mod.resolve_coverage_file("src/Baz.cs", coverage)

        self.assertEqual(exact, "src/Foo.cs")
        self.assertEqual(suffix, "C:/agent/work/repo/src/Bar.cs")
        self.assertIsNone(missing)

    def test_is_coverage_excluded_uses_reportgenerator_exclude_patterns(self) -> None:
        filters = ["*src/Presentation/Bot/Hosting/Program.cs"]

        self.assertTrue(
            self.mod.is_coverage_excluded(
                "src/Presentation/Bot/Hosting/Program.cs",
                filters,
            )
        )
        self.assertFalse(self.mod.is_coverage_excluded("src/Foo.cs", filters))

    def test_probably_executable_line_skips_comments_and_declarations(self) -> None:
        self.assertFalse(
            self.mod.is_probably_executable_source_line(
                "/// Strategy execution engine abstraction."
            )
        )
        self.assertFalse(self.mod.is_probably_executable_source_line("{ get; set; }"))
        self.assertFalse(self.mod.is_probably_executable_source_line("public interface IFoo"))
        self.assertTrue(self.mod.is_probably_executable_source_line("return value;"))
        self.assertTrue(self.mod.is_probably_executable_source_line("var result = value;"))

    def test_parse_branch_coverage_reads_condition_coverage(self) -> None:
        cobertura_xml = """<?xml version="1.0"?>
<coverage>
  <packages>
    <package name="Main">
      <classes>
        <class name="A" filename="src/Foo.cs">
          <lines>
            <line number="10" hits="1" branch="true" condition-coverage="50% (1/2)" />
            <line number="11" hits="1" />
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
            coverage = self.mod.parse_branch_coverage(xml_path)

        self.assertEqual(coverage["src/Foo.cs"][10], (1, 2))
        self.assertNotIn(11, coverage["src/Foo.cs"])


if __name__ == "__main__":
    unittest.main()
