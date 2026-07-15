from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dotnet_quality_gates.quality import check_architectural_boundaries


class CheckArchitecturalBoundariesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = check_architectural_boundaries

    def test_load_architectural_boundaries_config_from_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "architectural_boundaries": {
                            "include_roots": [" src ", "", 5],
                            "exclude_globs": [" **/*.g.cs ", "", 6],
                            "layer_rules": {
                                "Domain": [],
                                "Application": [" Domain ", "Nope", 7],
                                "Infrastructure": ["Application", "Domain"],
                                "Presentation": ["Application", "Domain", "Infrastructure"],
                                "Unknown": ["Domain"],
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            include_roots, exclude_globs, layer_rules = self.mod.load_architectural_boundaries_config(policy_path)

        self.assertEqual(include_roots, ["src"])
        self.assertEqual(exclude_globs, ["**/*.g.cs"])
        self.assertEqual(layer_rules["Application"], ["Domain"])
        self.assertNotIn("Unknown", layer_rules)

    def test_validate_project_references_reports_upward_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            domain = root / "src" / "Domain"
            infrastructure = root / "src" / "Infrastructure"
            domain.mkdir(parents=True)
            infrastructure.mkdir(parents=True)
            (infrastructure / "Infrastructure.csproj").write_text("<Project />", encoding="utf-8")
            (domain / "Domain.csproj").write_text(
                """
<Project>
  <ItemGroup>
    <ProjectReference Include="..\\Infrastructure\\Infrastructure.csproj" />
  </ItemGroup>
</Project>
""".strip(),
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations = self.mod.validate_project_references(
                    include_roots=[root / "src"],
                    layer_rules=self.mod.DEFAULT_LAYER_RULES,
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(len(violations), 1)
        self.assertIn("Domain project must not reference Infrastructure project", violations[0])

    def test_validate_using_directives_reports_forbidden_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            application = root / "src" / "Application"
            application.mkdir(parents=True)
            source_file = application / "Services" / "Thing.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """
using Domain.Entities;
using Infrastructure.Persistence.Trading;

namespace Application.Services;
public class Thing {}
""".strip(),
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations = self.mod.validate_using_directives(
                    include_roots=[root / "src"],
                    exclude_globs=[],
                    layer_rules=self.mod.DEFAULT_LAYER_RULES,
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(len(violations), 1)
        self.assertIn("Application code must not depend on Infrastructure namespace", violations[0])

    def test_validate_using_directives_ignores_comments_and_strings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            domain = root / "src" / "Domain"
            domain.mkdir(parents=True)
            source_file = domain / "Entity.cs"
            source_file.write_text(
                """
// using Infrastructure.Persistence.Trading;
namespace Domain;

public class Entity
{
    public string Text => "using Application.Services;";
}
""".strip(),
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations = self.mod.validate_using_directives(
                    include_roots=[root / "src"],
                    exclude_globs=[],
                    layer_rules=self.mod.DEFAULT_LAYER_RULES,
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(violations, [])

    def test_validate_architectural_boundaries_allows_downward_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            application = root / "src" / "Application"
            domain = root / "src" / "Domain"
            application.mkdir(parents=True)
            domain.mkdir(parents=True)
            (domain / "Domain.csproj").write_text("<Project />", encoding="utf-8")
            (application / "Application.csproj").write_text(
                """
<Project>
  <ItemGroup>
    <ProjectReference Include="..\\Domain\\Domain.csproj" />
  </ItemGroup>
</Project>
""".strip(),
                encoding="utf-8",
            )
            (application / "Service.cs").write_text(
                "using Domain.Entities;\nnamespace Application;\npublic class Service {}\n",
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations = self.mod.validate_architectural_boundaries(
                    include_roots=[root / "src"],
                    exclude_globs=[],
                    layer_rules=self.mod.DEFAULT_LAYER_RULES,
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
