from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dotnet_quality_gates.quality import check_namespace_layout


class CheckNamespaceLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = check_namespace_layout

    def test_load_source_namespace_layout_config_from_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "source_namespace_layout": {
                            "include_roots": [" src/Foo ", "", 5, "src/Bar"],
                            "exclude_globs": [" **/*.g.cs ", "", 6, "**/Migrations/*.cs"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            include_roots, exclude_globs = self.mod.load_source_namespace_layout_config(policy_path)

        self.assertEqual(include_roots, ["src/Foo", "src/Bar"])
        self.assertEqual(exclude_globs, ["**/*.g.cs", "**/Migrations/*.cs"])

    def test_validate_source_namespace_layout_reports_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project_root = root / "src" / "Domain"
            project_root.mkdir(parents=True)
            (project_root / "Domain.csproj").write_text(
                "<Project><PropertyGroup><AssemblyName>Domain</AssemblyName></PropertyGroup></Project>",
                encoding="utf-8",
            )

            source_file = project_root / "Entities" / "Telemetry" / "ExecutionSession.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                "namespace Domain.Entities;\npublic class ExecutionSession {}\n",
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations, fixed_files = self.mod.validate_source_namespace_layout(
                    include_roots=[project_root],
                    exclude_globs=[],
                    fix=False,
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(fixed_files, 0)
        self.assertEqual(len(violations), 1)
        self.assertIn("does not match expected 'Domain.Entities.Telemetry'", violations[0])

    def test_validate_source_namespace_layout_fix_updates_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project_root = root / "src" / "Presentation" / "Bot"
            project_root.mkdir(parents=True)
            (project_root / "Main.csproj").write_text(
                "<Project><PropertyGroup><AssemblyName>Main</AssemblyName></PropertyGroup></Project>",
                encoding="utf-8",
            )

            source_file = project_root / "Health" / "HealthCheckService.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                "namespace Main;\npublic class HealthCheckService {}\n",
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations, fixed_files = self.mod.validate_source_namespace_layout(
                    include_roots=[project_root],
                    exclude_globs=[],
                    fix=True,
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

            fixed_text = source_file.read_text(encoding="utf-8")

        self.assertEqual(violations, [])
        self.assertEqual(fixed_files, 1)
        self.assertIn("namespace Main.Health;", fixed_text)

    def test_validate_source_namespace_layout_fix_inserts_missing_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project_root = root / "src" / "Application"
            project_root.mkdir(parents=True)
            (project_root / "Application.csproj").write_text(
                "<Project><PropertyGroup><AssemblyName>Application</AssemblyName></PropertyGroup></Project>",
                encoding="utf-8",
            )

            source_file = project_root / "Contracts" / "TradeOrder.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                "using System;\n\npublic class TradeOrder {}\n",
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations, fixed_files = self.mod.validate_source_namespace_layout(
                    include_roots=[project_root],
                    exclude_globs=[],
                    fix=True,
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

            fixed_text = source_file.read_text(encoding="utf-8")

        self.assertEqual(violations, [])
        self.assertEqual(fixed_files, 1)
        self.assertIn("namespace Application.Contracts;", fixed_text)

    def test_validate_source_namespace_layout_scopes_to_target_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project_root = root / "src" / "Infrastructure"
            project_root.mkdir(parents=True)
            (project_root / "Infrastructure.csproj").write_text(
                "<Project><PropertyGroup><AssemblyName>Infrastructure</AssemblyName></PropertyGroup></Project>",
                encoding="utf-8",
            )

            first_file = project_root / "A.cs"
            second_file = project_root / "Nested" / "B.cs"
            second_file.parent.mkdir(parents=True)
            first_file.write_text("namespace Wrong;\npublic class A {}\n", encoding="utf-8")
            second_file.write_text("namespace Wrong;\npublic class B {}\n", encoding="utf-8")

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations, fixed_files = self.mod.validate_source_namespace_layout(
                    include_roots=[project_root],
                    exclude_globs=[],
                    fix=True,
                    target_paths=[second_file],
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

            first_text = first_file.read_text(encoding="utf-8")
            second_text = second_file.read_text(encoding="utf-8")

        self.assertEqual(violations, [])
        self.assertEqual(fixed_files, 1)
        self.assertIn("namespace Wrong;", first_text)
        self.assertIn("namespace Infrastructure.Nested;", second_text)


if __name__ == "__main__":
    unittest.main()
