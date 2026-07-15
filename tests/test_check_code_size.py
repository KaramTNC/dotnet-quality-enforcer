from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dotnet_quality_gates.quality import check_code_size


class CheckCodeSizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = check_code_size

    def test_load_code_size_config_from_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "code_size": {
                            "include_roots": [" src/Foo ", "", 5, "src/Bar"],
                            "exclude_globs": [" **/*.g.cs ", "", 6, "**/Migrations/*.cs"],
                            "method_warn_lines": 35,
                            "method_max_lines": 55,
                            "type_warn_lines": 200,
                            "type_max_lines": 300,
                            "file_warn_lines": 250,
                            "file_max_lines": 400,
                        }
                    }
                ),
                encoding="utf-8",
            )

            config = self.mod.load_code_size_config(policy_path)

        self.assertEqual(config.include_roots, ["src/Foo", "src/Bar"])
        self.assertEqual(config.exclude_globs, ["**/*.g.cs", "**/Migrations/*.cs"])
        self.assertEqual(config.method_warn_lines, 35)
        self.assertEqual(config.method_max_lines, 55)
        self.assertEqual(config.type_warn_lines, 200)
        self.assertEqual(config.type_max_lines, 300)
        self.assertEqual(config.file_warn_lines, 250)
        self.assertEqual(config.file_max_lines, 400)

    def test_canonicalize_baseline_violation_ignores_line_counts_and_member_location(self) -> None:
        self.assertEqual(
            self.mod.canonicalize_baseline_violation(
                "src/Example.cs:10: Run/0 has 61 method lines; fail threshold is 60."
            ),
            "method:src/Example.cs:Run/0",
        )
        self.assertEqual(
            self.mod.canonicalize_baseline_violation(
                "src/Example.cs has 451 file lines; fail threshold is 450."
            ),
            "file:src/Example.cs",
        )

    def test_parse_methods_reports_method_line_count(self) -> None:
        config = self.mod.CodeSizeConfig(
            include_roots=["src"],
            exclude_globs=[],
            method_warn_lines=2,
            method_max_lines=4,
            type_warn_lines=50,
            type_max_lines=100,
            file_warn_lines=50,
            file_max_lines=100,
        )
        text = """
public sealed class Example
{
    public void Run()
    {
        var value = 1;
        value++;
    }
}
"""

        methods = self.mod.parse_methods("src/Example.cs", text, config)

        run = next(method for method in methods if method.name == "Run/0")
        self.assertEqual(run.line_count, 5)
        self.assertEqual(run.start_line, 4)
        self.assertEqual(run.end_line, 8)

    def test_parse_types_reports_type_line_count(self) -> None:
        config = self.mod.CodeSizeConfig(
            include_roots=["src"],
            exclude_globs=[],
            method_warn_lines=40,
            method_max_lines=60,
            type_warn_lines=3,
            type_max_lines=5,
            file_warn_lines=50,
            file_max_lines=100,
        )
        text = """
namespace Sample;

public sealed class Example
{
    public void Run()
    {
    }
}
"""

        types = self.mod.parse_types("src/Example.cs", text, config)

        self.assertEqual(len(types), 1)
        self.assertEqual(types[0].name, "class Example")
        self.assertEqual(types[0].line_count, 6)

    def test_parse_methods_ignores_attribute_arguments_before_type_body(self) -> None:
        config = self.mod.CodeSizeConfig(
            include_roots=["src"],
            exclude_globs=[],
            method_warn_lines=40,
            method_max_lines=60,
            type_warn_lines=250,
            type_max_lines=350,
            file_warn_lines=300,
            file_max_lines=450,
        )
        text = """
[Index(nameof(Ticker))]
internal sealed class Entity
{
    public string Ticker { get; set; } = "";
}
"""

        methods = self.mod.parse_methods("src/Entity.cs", text, config)

        self.assertEqual(methods, [])

    def test_split_violations_separates_warnings_and_failures(self) -> None:
        warn = self.mod.CodeSizeMetric("method", "src/A.cs", "Warn/0", 1, 5, 5, 4, 6)
        fail = self.mod.CodeSizeMetric("type", "src/B.cs", "class Fail", 1, 8, 8, 4, 6)

        failures, warnings = self.mod.split_violations([warn, fail])

        self.assertEqual(len(warnings), 1)
        self.assertIn("Warn/0", warnings[0])
        self.assertEqual(len(failures), 1)
        self.assertIn("class Fail", failures[0])

    def test_collect_files_excludes_migrations_and_generated_sources(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_root = root / "src"
            (src_root / "Infrastructure" / "Migrations").mkdir(parents=True)
            (src_root / "Application").mkdir(parents=True)
            keep = src_root / "Application" / "Keep.cs"
            generated = src_root / "Application" / "Generated.g.cs"
            migration = src_root / "Infrastructure" / "Migrations" / "Migration.cs"
            keep.write_text("public sealed class Keep {}", encoding="utf-8")
            generated.write_text("public sealed class Generated {}", encoding="utf-8")
            migration.write_text("public sealed class Migration {}", encoding="utf-8")

            files = self.mod.collect_files(
                include_roots=[src_root],
                exclude_globs=["**/*.g.cs", "**/Migrations/**"],
                repo_root=root,
            )

        self.assertEqual(files, [keep])

    def test_collect_diff_metrics_measures_changed_containing_units(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_file = root / "src" / "Example.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """
public sealed class Example
{
    public void Run()
    {
        var value = 1;
        value++;
    }
}
""",
                encoding="utf-8",
            )
            config = self.mod.CodeSizeConfig(
                include_roots=["src"],
                exclude_globs=[],
                method_warn_lines=2,
                method_max_lines=4,
                type_warn_lines=3,
                type_max_lines=5,
                file_warn_lines=5,
                file_max_lines=7,
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                with patch.object(
                    self.mod,
                    "run_git_diff",
                    return_value=(
                        "+++ b/src/Example.cs\n"
                        "@@ -6,0 +6,1 @@\n"
                        "+        value++;\n"
                    ),
                ):
                    metrics = self.mod.collect_diff_metrics("HEAD~1", config)
            finally:
                self.mod.REPO_ROOT = original_repo_root

        names = {(metric.kind, metric.name) for metric in metrics}
        self.assertIn(("file", "Example.cs"), names)
        self.assertIn(("type", "class Example"), names)
        self.assertIn(("method", "Run/0"), names)

    def test_collect_full_metrics_aggregates_partial_type_lines(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_root = root / "src"
            src_root.mkdir()
            (src_root / "Example.Core.cs").write_text(
                """
namespace Sample;

public partial class Example
{
    public void One()
    {
    }
}
""",
                encoding="utf-8",
            )
            (src_root / "Example.More.cs").write_text(
                """
namespace Sample;

public partial class Example
{
    public void Two()
    {
    }
}
""",
                encoding="utf-8",
            )
            config = self.mod.CodeSizeConfig(
                include_roots=["src"],
                exclude_globs=[],
                method_warn_lines=40,
                method_max_lines=60,
                type_warn_lines=10,
                type_max_lines=12,
                file_warn_lines=50,
                file_max_lines=100,
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                metrics = self.mod.collect_full_metrics(config)
            finally:
                self.mod.REPO_ROOT = original_repo_root

        aggregate = next(metric for metric in metrics if metric.name == "class Example (partial aggregate)")
        self.assertEqual(aggregate.line_count, 12)
        self.assertEqual(len(aggregate.spans), 2)

    def test_collect_diff_metrics_selects_partial_aggregate_from_changed_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_root = root / "src"
            src_root.mkdir()
            (src_root / "Example.Core.cs").write_text(
                """
namespace Sample;

public partial class Example
{
    public void One()
    {
    }
}
""",
                encoding="utf-8",
            )
            (src_root / "Example.More.cs").write_text(
                """
namespace Sample;

public partial class Example
{
    public void Two()
    {
    }
}
""",
                encoding="utf-8",
            )
            config = self.mod.CodeSizeConfig(
                include_roots=["src"],
                exclude_globs=[],
                method_warn_lines=40,
                method_max_lines=60,
                type_warn_lines=10,
                type_max_lines=12,
                file_warn_lines=50,
                file_max_lines=100,
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                with patch.object(
                    self.mod,
                    "run_git_diff",
                    return_value=(
                        "+++ b/src/Example.More.cs\n"
                        "@@ -6,0 +6,1 @@\n"
                        "+    public void Two()\n"
                    ),
                ):
                    metrics = self.mod.collect_diff_metrics("HEAD~1", config)
            finally:
                self.mod.REPO_ROOT = original_repo_root

        names = {(metric.kind, metric.name) for metric in metrics}
        self.assertIn(("type", "class Example (partial aggregate)"), names)


if __name__ == "__main__":
    unittest.main()
