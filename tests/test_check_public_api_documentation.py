from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dotnet_quality_gates.quality import check_public_api_documentation


class CheckPublicApiDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = check_public_api_documentation

    def test_load_public_api_documentation_config_from_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "public_api_documentation": {
                            "include_roots": [" src/Foo ", "", 4, "src/Bar"],
                            "exclude_globs": [" **/*.g.cs ", "", 5, "**/Migrations/*.cs"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            include_roots, exclude_globs = self.mod.load_public_api_documentation_config(policy_path)

        self.assertEqual(include_roots, ["src/Foo", "src/Bar"])
        self.assertEqual(exclude_globs, ["**/*.g.cs", "**/Migrations/*.cs"])

    def test_validate_public_api_documentation_reports_missing_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_root = root / "src" / "Application"
            src_root.mkdir(parents=True)
            source_file = src_root / "MissingDocs.cs"
            source_file.write_text(
                "namespace Application;\npublic class MissingDocs { public void Run() { } }\n",
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations = self.mod.validate_public_api_documentation(
                    include_roots=[src_root],
                    exclude_globs=[],
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(len(violations), 1)
        self.assertIn("missing XML documentation summary", violations[0])

    def test_validate_public_api_documentation_accepts_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_root = root / "src" / "Application"
            src_root.mkdir(parents=True)
            source_file = src_root / "Documented.cs"
            source_file.write_text(
                "\n".join(
                    [
                        "namespace Application;",
                        "/// <summary>Documented type.</summary>",
                        "public class Documented",
                        "{",
                        "    /// <summary>Runs the workflow.</summary>",
                        "    public void Run() { }",
                        "}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations = self.mod.validate_public_api_documentation(
                    include_roots=[src_root],
                    exclude_globs=[],
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(violations, [])

    def test_validate_public_api_documentation_ignores_public_member_in_internal_type(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_root = root / "src" / "Application"
            src_root.mkdir(parents=True)
            source_file = src_root / "InternalType.cs"
            source_file.write_text(
                "\n".join(
                    [
                        "namespace Application;",
                        "internal static class InternalType",
                        "{",
                        "    public static void Helper() { }",
                        "}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations = self.mod.validate_public_api_documentation(
                    include_roots=[src_root],
                    exclude_globs=[],
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(violations, [])

    def test_validate_public_api_documentation_accepts_documented_partial_type_part(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_root = root / "src" / "Application"
            src_root.mkdir(parents=True)
            (src_root / "DocumentedPartial.cs").write_text(
                "\n".join(
                    [
                        "namespace Application;",
                        "/// <summary>Documented partial type.</summary>",
                        "public partial class DocumentedPartial",
                        "{",
                        "}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (src_root / "DocumentedPartial.Helpers.cs").write_text(
                "\n".join(
                    [
                        "namespace Application;",
                        "public partial class DocumentedPartial",
                        "{",
                        "}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            original_repo_root = self.mod.REPO_ROOT
            self.mod.REPO_ROOT = root
            try:
                violations = self.mod.validate_public_api_documentation(
                    include_roots=[src_root],
                    exclude_globs=[],
                )
            finally:
                self.mod.REPO_ROOT = original_repo_root

        self.assertEqual(violations, [])

    def test_canonicalize_violation_key_ignores_line_number_and_partial_suffix(self) -> None:
        key = self.mod.canonicalize_violation_key(
            "src\\Application\\Foo.Bar.cs:42: Public method 'Run' is missing XML documentation summary."
        )

        self.assertEqual(
            key,
            "src/Application/Foo.cs: Public method 'Run' is missing XML documentation summary.",
        )

    def test_classify_public_declaration_handles_tuple_return_method(self) -> None:
        classified = self.mod.classify_public_declaration(
            "public static (string Value, string? Error) TryResolve(int id) => default;"
        )

        self.assertEqual(classified, ("method", "TryResolve"))

    def test_classify_public_declaration_uses_constructor_before_base_initializer(self) -> None:
        classified = self.mod.classify_public_declaration(
            'public ExampleClient(string token) : base("https://example.test", 120, "Example")'
        )

        self.assertEqual(classified, ("method", "ExampleClient"))


if __name__ == "__main__":
    unittest.main()
