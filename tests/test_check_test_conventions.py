from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dotnet_quality_gates.quality import check_test_conventions


class CheckTestConventionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = check_test_conventions

    def test_load_default_source_include_roots_from_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "test_conventions": {
                            "source_include_roots": [" src/Foo ", "", 3, "src/Bar"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            include_roots = self.mod.load_default_source_include_roots(policy_path)

        self.assertEqual(include_roots, ["src/Foo", "src/Bar"])

    def test_load_default_source_include_roots_supports_legacy_policy_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "unit_test_conventions": {
                            "source_include_roots": ["src/Legacy"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            include_roots = self.mod.load_default_source_include_roots(policy_path)

        self.assertEqual(include_roots, ["src/Legacy"])

    def test_parse_test_method_name_accepts_relaxed_descriptive_suffixes(self) -> None:
        self.assertEqual(
            self.mod.parse_test_method_name("LoadFromEnvironment_ReadsProviderCredentials"),
            "LoadFromEnvironment",
        )
        self.assertEqual(
            self.mod.parse_test_method_name("IsTierAllowed_WhenModeIsFreeOnly"),
            "IsTierAllowed",
        )

    def test_parse_test_method_name_rejects_names_without_method_prefix_and_suffix(self) -> None:
        self.assertIsNone(self.mod.parse_test_method_name("ReadsProviderCredentials"))
        self.assertIsNone(self.mod.parse_test_method_name("LoadFromEnvironment_"))

    def test_parse_targetable_members_includes_methods_properties_and_constructors(self) -> None:
        members = self.mod.parse_targetable_members(
            """
            public RuntimeConfig(string name)
            {
            }

            public string Name { get; }
            internal bool IsEnabled => true;
            private string Hidden { get; }
            private void Normalize()
            {
            }
            public void Load()
            {
            }
            """,
            "RuntimeConfig",
        )

        self.assertEqual(members, {"Constructor", "Name", "IsEnabled", "Hidden", "Normalize", "Load", "Properties"})

    def test_parse_exposed_methods_only_returns_methods_requiring_coverage(self) -> None:
        members = self.mod.parse_exposed_methods(
            """
            public RuntimeConfig(string name)
            {
            }

            public string Name { get; }
            public void Load()
            {
            }
            """
        )

        self.assertEqual(members, {"Load"})

    def test_parse_regions_and_methods_ignores_nested_test_doubles(self) -> None:
        methods = self.mod.parse_regions_and_methods(
            """
            #region Load Tests
            [Fact]
            public void Load_ReadsSettings()
            {
            }
            #endregion

            private sealed class FakeDependency
            {
                public void Load()
                {
                }
            }
            """,
            line_offset=0,
        )

        self.assertEqual([method.name for method in methods], ["Load_ReadsSettings"])

    def test_parse_source_classes_indexes_non_class_types_without_requiring_tests(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source_path = Path(td) / "OrderStatus.cs"
            source_path.write_text(
                """
                namespace Domain.Entities.Orders;

                public record OrderStatus(string Status);

                public interface IOrderStatus
                {
                    string GetStatus();
                }
                """,
                encoding="utf-8",
            )

            sources, errors = self.mod.parse_source_classes(Path(td))

        self.assertEqual(errors, [])
        self.assertEqual({source.name for source in sources}, {"OrderStatus", "IOrderStatus"})
        self.assertFalse(any(source.requires_test_class for source in sources))

    def test_parse_base_types_extracts_class_inheritance_targets(self) -> None:
        self.assertEqual(
            self.mod.parse_base_types("(string id) : BaseService, IFoo<Bar>"),
            ["BaseService", "IFoo"],
        )

    def test_build_include_to_test_root_map(self) -> None:
        include_roots = [self.mod.REPO_ROOT / "src" / "Domain"]
        unit_test_root = self.mod.REPO_ROOT / "test" / "Unit"

        mappings, errors = self.mod.build_include_to_test_root_map(include_roots, unit_test_root)

        self.assertEqual(errors, [])
        self.assertEqual(len(mappings), 1)
        include_root, test_root = mappings[0]
        self.assertEqual(include_root, self.mod.REPO_ROOT / "src" / "Domain")
        self.assertEqual(test_root, self.mod.REPO_ROOT / "test" / "Unit" / "Domain")

    def test_validate_conventions_reports_mirrored_path_violation(self) -> None:
        source = self.mod.SourceClassInfo(
            name="AccountInfo",
            path=self.mod.REPO_ROOT / "src" / "Domain" / "Entities" / "Accounts" / "AccountInfo.cs",
            line=1,
            exposed_methods=set(),
            is_partial=False,
        )
        test_class = self.mod.TestClassInfo(
            name="AccountInfoTests",
            path=self.mod.REPO_ROOT / "test" / "Unit" / "Domain" / "Entities" / "AccountInfoTests.cs",
            line=1,
            methods=[],
        )
        include_to_test_root = [
            (
                self.mod.REPO_ROOT / "src" / "Domain",
                self.mod.REPO_ROOT / "test" / "Unit" / "Domain",
            )
        ]

        violations = self.mod.validate_conventions([source], [test_class], include_to_test_root)

        self.assertTrue(any("must be located in" in violation for violation in violations))

    def test_validate_conventions_does_not_require_regions_or_exposed_method_checklists(self) -> None:
        source = self.mod.SourceClassInfo(
            name="MarketDataFactory",
            path=self.mod.REPO_ROOT / "src" / "Infrastructure" / "MarketData" / "Configuration" / "MarketDataFactory.cs",
            line=10,
            exposed_methods={"LoadFromEnvironment", "IsTierAllowed"},
            is_partial=False,
            targetable_members={"LoadFromEnvironment", "IsTierAllowed"},
        )
        test_class = self.mod.TestClassInfo(
            name="MarketDataFactoryTests",
            path=self.mod.REPO_ROOT / "test" / "Unit" / "Infrastructure" / "MarketData" / "Configuration" / "MarketDataFactoryTests.cs",
            line=1,
            methods=[
                self.mod.TestMethodInfo(
                    name="LoadFromEnvironment_ReadsProviderCredentials",
                    line=5,
                    region=None,
                    is_test_method=True,
                    method_under_test_from_name="LoadFromEnvironment",
                ),
            ],
        )
        include_to_test_root = [
            (
                self.mod.REPO_ROOT / "src" / "Infrastructure",
                self.mod.REPO_ROOT / "test" / "Unit" / "Infrastructure",
            )
        ]

        violations = self.mod.validate_conventions([source], [test_class], include_to_test_root)

        self.assertEqual(violations, [])

    def test_validate_conventions_accepts_class_level_behavior_names(self) -> None:
        source = self.mod.SourceClassInfo(
            name="MarketDataFactory",
            path=self.mod.REPO_ROOT / "src" / "Infrastructure" / "MarketData" / "Configuration" / "MarketDataFactory.cs",
            line=10,
            exposed_methods={"LoadFromEnvironment"},
            is_partial=False,
            targetable_members={"LoadFromEnvironment"},
        )
        test_class = self.mod.TestClassInfo(
            name="MarketDataFactoryTests",
            path=self.mod.REPO_ROOT / "test" / "Unit" / "Infrastructure" / "MarketData" / "Configuration" / "MarketDataFactoryTests.cs",
            line=1,
            methods=[
                self.mod.TestMethodInfo(
                    name="MarketDataFactory_ReadsProviderCredentials",
                    line=5,
                    region=None,
                    is_test_method=True,
                    method_under_test_from_name="MarketDataFactory",
                ),
            ],
        )
        include_to_test_root = [
            (
                self.mod.REPO_ROOT / "src" / "Infrastructure",
                self.mod.REPO_ROOT / "test" / "Unit" / "Infrastructure",
            )
        ]

        violations = self.mod.validate_conventions([source], [test_class], include_to_test_root)

        self.assertEqual(violations, [])

    def test_validate_conventions_allows_companion_test_classes_without_method_target_checks(self) -> None:
        source = self.mod.SourceClassInfo(
            name="MarketDataFactory",
            path=self.mod.REPO_ROOT / "src" / "Infrastructure" / "MarketData" / "Configuration" / "MarketDataFactory.cs",
            line=10,
            exposed_methods={"LoadFromEnvironment"},
            is_partial=False,
            targetable_members={"LoadFromEnvironment"},
        )
        test_class = self.mod.TestClassInfo(
            name="MarketDataFactoryAdditionalTests",
            path=self.mod.REPO_ROOT / "test" / "Unit" / "Infrastructure" / "MarketData" / "Configuration" / "MarketDataFactoryAdditionalTests.cs",
            line=1,
            methods=[
                self.mod.TestMethodInfo(
                    name="ProviderMatrix_UsesExpectedFallbacks",
                    line=5,
                    region=None,
                    is_test_method=True,
                    method_under_test_from_name="ProviderMatrix",
                ),
            ],
        )
        include_to_test_root = [
            (
                self.mod.REPO_ROOT / "src" / "Infrastructure",
                self.mod.REPO_ROOT / "test" / "Unit" / "Infrastructure",
            )
        ]

        violations = self.mod.validate_conventions([source], [test_class], include_to_test_root)

        self.assertEqual(violations, [])

    def test_validate_conventions_allows_unmapped_aggregate_test_classes(self) -> None:
        test_class = self.mod.TestClassInfo(
            name="BrokerContextAndRuntimeStatusFactoryTests",
            path=self.mod.REPO_ROOT / "test" / "Unit" / "Presentation" / "Bot" / "BrokerContextAndRuntimeStatusFactoryTests.cs",
            line=1,
            methods=[
                self.mod.TestMethodInfo(
                    name="RuntimeStatusFactory_ProjectsContextState",
                    line=5,
                    region=None,
                    is_test_method=True,
                    method_under_test_from_name="RuntimeStatusFactory",
                ),
            ],
        )

        violations = self.mod.validate_conventions([], [test_class], [])

        self.assertEqual(violations, [])

    def test_validate_conventions_accepts_class_prefixed_method_names(self) -> None:
        source = self.mod.SourceClassInfo(
            name="MarketDataFactory",
            path=self.mod.REPO_ROOT / "src" / "Infrastructure" / "MarketData" / "Configuration" / "MarketDataFactory.cs",
            line=10,
            exposed_methods={"LoadFromEnvironment"},
            is_partial=False,
        )
        test_class = self.mod.TestClassInfo(
            name="MarketDataFactoryTests",
            path=self.mod.REPO_ROOT / "test" / "Unit" / "Infrastructure" / "MarketData" / "Configuration" / "MarketDataFactoryTests.cs",
            line=1,
            methods=[
                self.mod.TestMethodInfo(
                    name="MarketDataFactory_LoadFromEnvironment_ReadsProviderCredentials",
                    line=5,
                    region="LoadFromEnvironment Tests",
                    is_test_method=True,
                    method_under_test_from_name="MarketDataFactory",
                ),
            ],
        )
        include_to_test_root = [
            (
                self.mod.REPO_ROOT / "src" / "Infrastructure",
                self.mod.REPO_ROOT / "test" / "Unit" / "Infrastructure",
            )
        ]

        violations = self.mod.validate_conventions([source], [test_class], include_to_test_root)

        self.assertEqual(violations, [])

    def test_validate_conventions_allows_inherited_exposed_method_targets(self) -> None:
        base = self.mod.SourceClassInfo(
            name="BaseService",
            path=self.mod.REPO_ROOT / "src" / "Infrastructure" / "BaseService.cs",
            line=1,
            exposed_methods={"Load"},
            is_partial=False,
            targetable_members={"Load"},
            requires_test_class=False,
        )
        source = self.mod.SourceClassInfo(
            name="DerivedService",
            path=self.mod.REPO_ROOT / "src" / "Infrastructure" / "DerivedService.cs",
            line=1,
            exposed_methods=set(),
            is_partial=False,
            targetable_members=set(),
            base_types=["BaseService"],
        )
        test_class = self.mod.TestClassInfo(
            name="DerivedServiceTests",
            path=self.mod.REPO_ROOT / "test" / "Unit" / "Infrastructure" / "DerivedServiceTests.cs",
            line=1,
            methods=[
                self.mod.TestMethodInfo(
                    name="Load_UsesDerivedConfiguration",
                    line=5,
                    region="Load Tests",
                    is_test_method=True,
                    method_under_test_from_name="Load",
                ),
            ],
        )
        include_to_test_root = [
            (
                self.mod.REPO_ROOT / "src" / "Infrastructure",
                self.mod.REPO_ROOT / "test" / "Unit" / "Infrastructure",
            )
        ]

        violations = self.mod.validate_conventions([base, source], [test_class], include_to_test_root)

        self.assertEqual(violations, [])

    def test_combine_partial_source_classes_keeps_duplicate_names_for_path_disambiguation(self) -> None:
        first = self.mod.SourceClassInfo(
            name="EnrichedSignal",
            path=self.mod.REPO_ROOT / "src" / "Application" / "Contracts" / "EnrichedSignal.cs",
            line=1,
            exposed_methods=set(),
            is_partial=False,
        )
        second = self.mod.SourceClassInfo(
            name="EnrichedSignal",
            path=self.mod.REPO_ROOT / "src" / "Domain" / "ValueObjects" / "Signals" / "EnrichedSignal.cs",
            line=1,
            exposed_methods=set(),
            is_partial=False,
        )

        sources, errors = self.mod.combine_partial_source_classes([first, second])

        self.assertEqual(errors, [])
        self.assertEqual(sources, [first, second])

    def test_validate_conventions_disambiguates_duplicate_source_names_by_path(self) -> None:
        app_source = self.mod.SourceClassInfo(
            name="EnrichedSignal",
            path=self.mod.REPO_ROOT / "src" / "Application" / "Contracts" / "EnrichedSignal.cs",
            line=1,
            exposed_methods=set(),
            is_partial=False,
        )
        domain_source = self.mod.SourceClassInfo(
            name="EnrichedSignal",
            path=self.mod.REPO_ROOT / "src" / "Domain" / "ValueObjects" / "Signals" / "EnrichedSignal.cs",
            line=1,
            exposed_methods=set(),
            is_partial=False,
        )
        app_test = self.mod.TestClassInfo(
            name="EnrichedSignalTests",
            path=self.mod.REPO_ROOT / "test" / "Unit" / "Application" / "Contracts" / "EnrichedSignalTests.cs",
            line=1,
            methods=[],
        )
        domain_test = self.mod.TestClassInfo(
            name="EnrichedSignalTests",
            path=self.mod.REPO_ROOT / "test" / "Unit" / "Domain" / "ValueObjects" / "Signals" / "EnrichedSignalTests.cs",
            line=1,
            methods=[],
        )
        include_to_test_root = [
            (
                self.mod.REPO_ROOT / "src" / "Application",
                self.mod.REPO_ROOT / "test" / "Unit" / "Application",
            ),
            (
                self.mod.REPO_ROOT / "src" / "Domain",
                self.mod.REPO_ROOT / "test" / "Unit" / "Domain",
            ),
        ]

        violations = self.mod.validate_conventions(
            [app_source, domain_source],
            [app_test, domain_test],
            include_to_test_root,
        )

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
