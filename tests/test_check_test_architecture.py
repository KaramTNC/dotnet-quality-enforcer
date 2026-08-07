from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dotnet_quality_gates.architecture import ArchitectureConfig, ArchitectureUnit
from dotnet_quality_gates.quality import check_test_architecture


class CheckTestArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = check_test_architecture

    def test_load_project_mappings_from_policy_keeps_existing_extra_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "Foo").mkdir(parents=True)
            (root / "test" / "Unit" / "Foo").mkdir(parents=True)
            policy_path = root / "policy.json"

            policy_path.write_text(
                json.dumps(
                    {
                        "test_architecture": {
                            "additional_project_mappings": {
                                " test/Unit/Foo ": [" src/Foo ", "", 1],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                mappings = self.mod.load_project_mappings(policy_path)
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(mappings, {"test/Unit/Foo": ["src/Foo"]})

    def test_load_project_mappings_ignores_stale_legacy_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            policy_path = root / "policy.json"

            policy_path.write_text(
                json.dumps(
                    {
                        "test_architecture": {
                            "project_mappings": {
                                "test/Integration.Windows/Presentation/BacktesterApp": [
                                    "src/Presentation/BacktesterApp",
                                ],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                mappings = self.mod.load_project_mappings(policy_path)
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(mappings, {})

    def test_load_project_mappings_falls_back_on_invalid_structure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text(
                json.dumps({"test_architecture": {"project_mappings": ["bad"]}}),
                encoding="utf-8",
            )
            mappings = self.mod.load_project_mappings(policy_path)

        self.assertEqual(mappings, {})

    def test_custom_architecture_maps_arbitrary_test_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "src" / "Features" / "Orders"
            test_project = root / "tests" / "Unit" / "Features" / "Orders"
            source.mkdir(parents=True)
            test_project.mkdir(parents=True)
            (source / "Order.cs").write_text("namespace Product.Features.Orders;", encoding="utf-8")
            (test_project / "OrderTests.cs").write_text("public class OrderTests {}", encoding="utf-8")
            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "architecture": {
                            "units": {
                                "Orders": {
                                    "source_roots": ["src/Features/Orders"],
                                    "namespace_prefixes": ["Product.Features.Orders"],
                                    "allowed_dependencies": [],
                                }
                            }
                        },
                        "test_architecture": {
                            "test_roots": ["tests"],
                            "project_mappings": {
                                "tests/Unit/Features/Orders": ["src/Features/Orders"]
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                architecture = self.mod.load_architecture_config(policy_path)
                mappings, test_roots, integration_roots = self.mod.load_test_architecture_config(
                    policy_path,
                    architecture,
                )
                discovered = self.mod.discover_project_mappings(root, mappings, architecture)
                errors = self.mod.validate_test_file_locations(
                    root,
                    architecture=architecture,
                    project_mappings=discovered,
                    test_roots=test_roots,
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(discovered, {"tests/Unit/Features/Orders": ["src/Features/Orders"]})
        self.assertEqual(test_roots, ["tests"])
        self.assertEqual(integration_roots, [])
        self.assertEqual(errors, [])

    def test_custom_architecture_reports_unmapped_test_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tests" / "Unit" / "Features" / "Orders").mkdir(parents=True)
            test_file = root / "tests" / "Unit" / "Features" / "Orders" / "OrderTests.cs"
            test_file.write_text("public class OrderTests {}", encoding="utf-8")
            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                architecture = ArchitectureConfig(
                    units=(
                        ArchitectureUnit(
                            name="Orders",
                            source_roots=("src/Features/Orders",),
                            namespace_prefixes=("Product.Features.Orders",),
                            allowed_dependencies=frozenset(),
                        ),
                    ),
                    is_custom=True,
                )
                errors = self.mod.validate_test_file_locations(
                    root,
                    architecture=architecture,
                    project_mappings={},
                    test_roots=["tests"],
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(len(errors), 1)
        self.assertIn("not covered by any configured test project mapping", errors[0])

    def test_discover_project_mappings_uses_current_onion_layout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "Domain").mkdir(parents=True)
            (root / "src" / "Domain" / "Domain.csproj").write_text("<Project />", encoding="utf-8")
            (root / "src" / "Application").mkdir(parents=True)
            (root / "src" / "Application" / "Application.csproj").write_text("<Project />", encoding="utf-8")
            (root / "src" / "Infrastructure").mkdir(parents=True)
            (root / "src" / "Infrastructure" / "Infrastructure.csproj").write_text("<Project />", encoding="utf-8")
            (root / "src" / "Presentation" / "Bot").mkdir(parents=True)
            (root / "src" / "Presentation" / "Bot" / "Main.csproj").write_text("<Project />", encoding="utf-8")

            (root / "test" / "Unit" / "Domain").mkdir(parents=True)
            (root / "test" / "Unit" / "Domain" / "FooTests.cs").write_text("public class FooTests { }", encoding="utf-8")
            (root / "test" / "Integration" / "Infrastructure").mkdir(parents=True)
            (root / "test" / "Integration" / "Infrastructure" / "BarIntegrationTests.cs").write_text(
                "public class BarIntegrationTests { }",
                encoding="utf-8",
            )
            (root / "test" / "EndToEnd" / "Bot").mkdir(parents=True)
            (root / "test" / "EndToEnd" / "Bot" / "BazIntegrationTests.cs").write_text(
                "public class BazIntegrationTests { }",
                encoding="utf-8",
            )
            (root / "test" / "Integration.Windows" / "bin").mkdir(parents=True)
            (root / "test" / "Integration.Windows" / "bin" / "OldTests.cs").write_text(
                "public class OldTests { }",
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                mappings = self.mod.discover_project_mappings(root)
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(
            mappings,
            {
                "test/EndToEnd/Bot": ["src/Presentation/Bot"],
                "test/Integration/Infrastructure": ["src/Infrastructure"],
                "test/Unit/Domain": ["src/Domain"],
            },
        )

    def test_main_passes_for_matching_temp_layout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "Foo").mkdir(parents=True)
            (root / "src" / "Domain").mkdir(parents=True)
            (root / "src" / "Domain" / "Domain.csproj").write_text("<Project />", encoding="utf-8")
            (root / "test" / "Unit" / "Foo").mkdir(parents=True)
            (root / "test" / "Unit" / "Domain").mkdir(parents=True)
            (root / "test" / "Unit" / "Domain" / "FooTests.cs").write_text(
                "public class FooTests { }",
                encoding="utf-8",
            )

            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "test_architecture": {
                            "additional_project_mappings": {
                                "test/Unit/Foo": ["src/Foo"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                import sys

                original_argv = sys.argv
                sys.argv = [
                    "check_test_architecture.py",
                    "--policy-path",
                    str(policy_path),
                ]
                try:
                    result = self.mod.main()
                finally:
                    sys.argv = original_argv
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(result, 0)

    def test_validate_integration_test_naming_reports_non_integration_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            integration_dir = root / "test" / "Integration" / "Infrastructure" / "Foo"
            integration_dir.mkdir(parents=True)
            file_path = integration_dir / "BadTests.cs"
            file_path.write_text(
                "public class BadTests { }",
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                errors = self.mod.validate_integration_test_naming(
                    "test/Integration/Infrastructure/Foo",
                    integration_dir,
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(len(errors), 1)
        self.assertIn("must end with 'IntegrationTests'", errors[0])

    def test_validate_integration_test_naming_allows_non_test_helper_classes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            integration_dir = root / "test" / "Integration" / "Infrastructure" / "Foo"
            integration_dir.mkdir(parents=True)
            file_path = integration_dir / "FakeProvider.cs"
            file_path.write_text(
                "public class FakeProvider { }",
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                errors = self.mod.validate_integration_test_naming(
                    "test/Integration/Infrastructure/Foo",
                    integration_dir,
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(errors, [])

    def test_validate_test_file_locations_skips_assembly_info(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "Infrastructure").mkdir(parents=True)
            (root / "src" / "Infrastructure" / "Infrastructure.csproj").write_text("<Project />", encoding="utf-8")
            (root / "test" / "Integration").mkdir(parents=True)
            (root / "test" / "Integration" / "AssemblyInfo.cs").write_text(
                "using Xunit;\n[assembly: CollectionBehavior(DisableTestParallelization = true)]\n",
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                errors = self.mod.validate_test_file_locations(root)
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
