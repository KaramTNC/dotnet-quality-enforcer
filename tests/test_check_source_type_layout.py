from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dotnet_quality_gates.quality import check_source_type_layout


class CheckSourceTypeLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = check_source_type_layout

    def test_load_source_type_layout_config_from_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "source_type_layout": {
                            "include_roots": [" src/Foo ", "", 5, "src/Bar"],
                            "exclude_globs": [" **/*.g.cs ", "", 6, "**/Migrations/*.cs"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            include_roots, exclude_globs = self.mod.load_source_type_layout_config(policy_path)

        self.assertEqual(include_roots, ["src/Foo", "src/Bar"])
        self.assertEqual(exclude_globs, ["**/*.g.cs", "**/Migrations/*.cs"])

    def test_validate_source_type_layout_reports_multiple_top_level_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_root = root / "src" / "Application"
            src_root.mkdir(parents=True)
            source_file = src_root / "MultiType.cs"
            source_file.write_text(
                "namespace Demo;\npublic class A {}\npublic interface B {}\n",
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations = self.mod.validate_source_type_layout(
                    include_roots=[src_root],
                    exclude_globs=[],
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(len(violations), 1)
        self.assertIn("Multiple top-level class/interface declarations found", violations[0])

    def test_validate_source_type_layout_ignores_nested_class(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_root = root / "src" / "Application"
            src_root.mkdir(parents=True)
            source_file = src_root / "NestedType.cs"
            source_file.write_text(
                "namespace Demo;\npublic class A { public class B { } }\n",
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations = self.mod.validate_source_type_layout(
                    include_roots=[src_root],
                    exclude_globs=[],
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
