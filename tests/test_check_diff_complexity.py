from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import REPO_ROOT

from dotnet_quality_gates.quality import check_diff_complexity


class CheckDiffComplexityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = check_diff_complexity

    def test_load_diff_quality_config_from_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "diff_quality": {
                            "cyclomatic_complexity_max": 8,
                            "cognitive_complexity_max": 14,
                            "crap_score_max": 24.5,
                            "max_files_for_gate": 12,
                        }
                    }
                ),
                encoding="utf-8",
            )
            config = self.mod.load_diff_quality_config(policy_path)

        self.assertEqual(config, (8, 14, 24.5, 12))

    def test_load_diff_quality_config_defaults_to_unlimited_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = self.mod.load_diff_quality_config(Path(td) / "missing.json")

        self.assertIsNone(config[3])

    def test_crap_score_requires_coverage_data(self) -> None:
        method = self.mod.MethodMetric(
            path="src/Example.cs",
            name="NoCoverage",
            signature_key="NoCoverage/0",
            start_line=1,
            end_line=3,
            complexity=10,
        )

        self.assertIsNone(method.coverage_ratio)
        self.assertIsNone(method.crap_score)
        violation = self.mod.crap_violation(method, "src/Example.cs:1: NoCoverage", 30.0)
        self.assertIsNotNone(violation)
        self.assertIn("no method coverage data", violation)

    def test_crap_score_preserves_zero_coverable_line_behavior(self) -> None:
        method = self.mod.MethodMetric(
            path="src/Example.cs",
            name="NoCoverableLines",
            signature_key="NoCoverableLines/0",
            start_line=1,
            end_line=3,
            complexity=10,
            coverage_available=True,
        )

        self.assertEqual(method.coverage_ratio, 1.0)
        self.assertEqual(method.crap_score, 10.0)

    def test_parse_methods_counts_cyclomatic_complexity(self) -> None:
        text = """
namespace Sample;

public sealed class Example
{
    public int Decide(int value)
    {
        if (value > 0 && value < 10)
        {
            return 1;
        }

        for (var i = 0; i < value; i++)
        {
            value--;
        }

        return value == 0 ? 0 : -1;
    }
}
"""
        methods = self.mod.parse_methods("src/Example.cs", text)

        decide = next(method for method in methods if method.name == "Decide")
        self.assertEqual(decide.complexity, 5)

    def test_parse_methods_counts_cognitive_complexity(self) -> None:
        text = """
namespace Sample;

public sealed class Example
{
    public int Decide(int value)
    {
        if (value > 0 && value < 10)
        {
            for (var i = 0; i < value; i++)
            {
                if (value == 4 || value == 5)
                {
                    continue;
                }
            }
        }
        else
        {
            return -1;
        }

        return value;
    }
}
"""
        methods = self.mod.parse_methods("src/Example.cs", text)

        decide = next(method for method in methods if method.name == "Decide")
        self.assertEqual(decide.cognitive_complexity, 13)

    def test_changed_methods_selects_methods_intersecting_diff_lines(self) -> None:
        text = """
public sealed class Example
{
    public void A()
    {
    }

    public void B()
    {
    }
}
"""
        methods = self.mod.parse_methods("src/Example.cs", text)
        changed = self.mod.changed_methods(methods, {8})

        self.assertEqual([method.name for method in changed], ["B"])

    def test_parse_methods_ignores_invocation_and_async_lambda_blocks(self) -> None:
        text = """
public sealed class Example
{
    public void Run()
    {
        Task.Run(async () =>
        {
            if (true)
            {
            }
        });

        var values = items.OrderBy(item =>
        {
            if (item.Enabled)
            {
                return 0;
            }

            return 1;
        });
    }
}
"""
        methods = self.mod.parse_methods("src/Example.cs", text)

        self.assertEqual([method.name for method in methods], ["Run"])

    def test_parse_methods_ignores_control_condition_invocations(self) -> None:
        text = """
public sealed class Example
{
    public void Run()
    {
        while (!token.IsCancellationRequested && ShouldContinue())
        {
            if (CanRun())
            {
            }
        }
    }
}
"""
        methods = self.mod.parse_methods("src/Example.cs", text)

        self.assertEqual([method.name for method in methods], ["Run"])

    def test_validate_diff_complexity_reports_complex_changed_method(self) -> None:
        relative_path = "tests/DiffComplexityScratch.cs"
        scratch_path = REPO_ROOT / relative_path
        scratch_path.write_text(
            """
public sealed class DiffComplexityScratch
{
    public int Risky(int value)
    {
        if (value == 1) return 1;
        if (value == 2) return 2;
        if (value == 3) return 3;
        if (value == 4) return 4;
        if (value == 5) return 5;
        if (value == 6) return 6;
        if (value == 7) return 7;
        if (value == 8) return 8;
        if (value == 9) return 9;
        if (value == 10) return 10;
        return 0;
    }
}
""",
            encoding="utf-8",
        )
        try:
            with tempfile.TemporaryDirectory() as td:
                coverage_path = Path(td) / "coverage.xml"
                coverage_path.write_text(
                    f"""<?xml version="1.0"?>
<coverage>
  <packages>
    <package name="Main">
      <classes>
        <class name="DiffComplexityScratch" filename="{relative_path}">
          <lines>
            <line number="5" hits="1" />
            <line number="6" hits="1" />
            <line number="7" hits="1" />
            <line number="8" hits="1" />
            <line number="9" hits="1" />
            <line number="10" hits="1" />
            <line number="11" hits="1" />
            <line number="12" hits="1" />
            <line number="13" hits="1" />
            <line number="14" hits="1" />
            <line number="15" hits="1" />
            <line number="16" hits="1" />
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
""",
                    encoding="utf-8",
                )
                violations = self.mod.validate_diff_complexity(
                    base="HEAD",
                    changed={relative_path: {6}},
                    coverage_path=coverage_path,
                    cyclomatic_max=10,
                    cognitive_max=15,
                    crap_max=30.0,
                    max_files_for_gate=40,
                )
        finally:
            scratch_path.unlink(missing_ok=True)

        self.assertTrue(any("cyclomatic complexity 11" in violation for violation in violations))

    def test_validate_diff_complexity_uses_reported_cobertura_complexity(self) -> None:
        relative_path = "tests/DiffComplexityScratch.cs"
        scratch_path = REPO_ROOT / relative_path
        scratch_path.write_text(
            """
public sealed class DiffComplexityScratch
{
    public int Risky(int value)
    {
        if (value == 1) return 1;
        return 0;
    }
}
""",
            encoding="utf-8",
        )
        try:
            with tempfile.TemporaryDirectory() as td:
                coverage_path = Path(td) / "coverage.xml"
                coverage_path.write_text(
                    f"""<?xml version="1.0"?>
<coverage>
  <packages>
    <package name="Main">
      <classes>
        <class name="DiffComplexityScratch" filename="{relative_path}">
          <methods>
            <method name="Risky" signature="(System.Int32)" line-rate="1" branch-rate="1" complexity="22">
              <lines>
                <line number="5" hits="1" />
                <line number="6" hits="1" />
                <line number="7" hits="1" />
              </lines>
            </method>
          </methods>
          <lines>
            <line number="5" hits="1" />
            <line number="6" hits="1" />
            <line number="7" hits="1" />
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
""",
                    encoding="utf-8",
                )
                violations = self.mod.validate_diff_complexity(
                    base="HEAD",
                    changed={relative_path: {6}},
                    coverage_path=coverage_path,
                    cyclomatic_max=10,
                    cognitive_max=15,
                    crap_max=30.0,
                    max_files_for_gate=40,
                )
        finally:
            scratch_path.unlink(missing_ok=True)

        self.assertTrue(any("cyclomatic complexity 22" in violation for violation in violations))

    def test_validate_diff_complexity_reports_cognitively_complex_changed_method(self) -> None:
        relative_path = "tests/DiffComplexityScratch.cs"
        scratch_path = REPO_ROOT / relative_path
        scratch_path.write_text(
            """
public sealed class DiffComplexityScratch
{
    public int HardToRead(int value)
    {
        if (value > 0)
        {
            if (value < 10)
            {
                if (value != 5)
                {
                    return 1;
                }
            }
        }

        return 0;
    }
}
""",
            encoding="utf-8",
        )
        try:
            with tempfile.TemporaryDirectory() as td:
                coverage_path = Path(td) / "coverage.xml"
                coverage_path.write_text(
                    f"""<?xml version="1.0"?>
<coverage>
  <packages>
    <package name="Main">
      <classes>
        <class name="DiffComplexityScratch" filename="{relative_path}">
          <lines>
            <line number="5" hits="1" />
            <line number="6" hits="1" />
            <line number="7" hits="1" />
            <line number="8" hits="1" />
            <line number="9" hits="1" />
            <line number="10" hits="1" />
            <line number="11" hits="1" />
            <line number="12" hits="1" />
            <line number="13" hits="1" />
            <line number="14" hits="1" />
            <line number="15" hits="1" />
            <line number="16" hits="1" />
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
""",
                    encoding="utf-8",
                )
                violations = self.mod.validate_diff_complexity(
                    base="HEAD",
                    changed={relative_path: {6}},
                    coverage_path=coverage_path,
                    cyclomatic_max=100,
                    cognitive_max=5,
                    crap_max=1000.0,
                    max_files_for_gate=40,
                )
        finally:
            scratch_path.unlink(missing_ok=True)

        self.assertTrue(any("cognitive complexity 6" in violation for violation in violations))

    def test_parse_coverage_methods_rejects_unsafe_xml_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            coverage_path = Path(td) / "coverage.xml"
            coverage_path.write_text(
                "<!DOCTYPE coverage [<!ENTITY secret 'blocked'>]>"
                "<coverage>&secret;</coverage>",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "DTD or entity"):
                self.mod.parse_coverage_methods(coverage_path)


if __name__ == "__main__":
    unittest.main()
