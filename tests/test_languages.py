from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dotnet_quality_gates import cli
from dotnet_quality_gates.languages import (
    SUPPORTED_LANGUAGES,
    adapter_for_path,
    adapters_for_language,
    normalize_language,
    source_diff_pathspecs,
)
from dotnet_quality_gates.languages.base import iter_source_files
from dotnet_quality_gates.quality import check_namespace_layout, check_source_type_layout
from dotnet_quality_gates.quality.check_code_size import CodeSizeConfig


class LanguageAdapterTests(unittest.TestCase):
    def test_canonical_languages_and_aliases(self) -> None:
        self.assertEqual(
            SUPPORTED_LANGUAGES,
            ("csharp", "python", "java", "kotlin", "typescript", "javascript", "go", "rust"),
        )
        self.assertEqual(normalize_language("C#"), "csharp")
        self.assertEqual(normalize_language("kotlyn"), "kotlin")
        self.assertEqual(normalize_language("kt"), "kotlin")
        self.assertEqual(normalize_language("tsx"), "typescript")
        self.assertEqual(normalize_language("golang"), "go")
        self.assertEqual(normalize_language("rs"), "rust")

    def test_file_extensions_select_the_expected_adapter(self) -> None:
        self.assertEqual(adapter_for_path(Path("Example.cs")).language, "csharp")
        self.assertEqual(adapter_for_path(Path("example.py")).language, "python")
        self.assertEqual(adapter_for_path(Path("Example.java")).language, "java")
        self.assertEqual(adapter_for_path(Path("Example.kt")).language, "kotlin")
        self.assertEqual(adapter_for_path(Path("build.gradle.kts")).language, "kotlin")
        self.assertEqual(adapter_for_path(Path("Example.tsx")).language, "typescript")
        self.assertEqual(adapter_for_path(Path("Example.jsx")).language, "javascript")
        self.assertEqual(adapter_for_path(Path("main.go")).language, "go")
        self.assertEqual(adapter_for_path(Path("main.rs")).language, "rust")

    def test_diff_pathspecs_follow_the_selected_language(self) -> None:
        self.assertEqual(source_diff_pathspecs(language="go"), (":(glob)src/**/*.go",))
        self.assertEqual(
            source_diff_pathspecs(language="kotlyn"),
            (":(glob)src/**/*.kt", ":(glob)src/**/*.kts"),
        )
        self.assertEqual(len(source_diff_pathspecs()), 13)

    def test_auto_discovery_skips_generated_and_dependency_directories(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "main.go").write_text("package main\n", encoding="utf-8")
            for directory in ("node_modules", "target", "bin", "vendor"):
                skipped = root / directory
                skipped.mkdir()
                (skipped / f"ignored-{directory}.rs").write_text("fn ignored() {}\n", encoding="utf-8")

            files = iter_source_files(root, frozenset({".go", ".rs"}))
            adapters = adapters_for_language("auto", root)

        self.assertEqual(files, [root / "src" / "main.go"])
        self.assertEqual([adapter.language for adapter in adapters], ["go"])

    def test_all_non_csharp_adapters_emit_normalized_size_and_complexity_metrics(self) -> None:
        config = CodeSizeConfig(
            include_roots=["src"],
            exclude_globs=[],
            method_warn_lines=40,
            method_max_lines=60,
            type_warn_lines=250,
            type_max_lines=350,
            file_warn_lines=300,
            file_max_lines=450,
        )
        examples = {
            "python": ("Example.py", "class Example:\n    def run(self, value):\n        if value:\n            return 1\n        return 0\n"),
            "java": ("Example.java", "class Example {\n    int run(int value) {\n        if (value > 0) { return 1; }\n        return 0;\n    }\n}\n"),
            "kotlin": ("Example.kt", "class Example {\n    fun run(value: Int): Int {\n        if (value > 0) return 1\n        return 0\n    }\n}\n"),
            "typescript": ("Example.ts", "class Example {\n    run(value: number): number {\n        if (value > 0) return 1;\n        return 0;\n    }\n}\n"),
            "javascript": ("Example.js", "class Example {\n    run(value) {\n        if (value > 0) return 1;\n        return 0;\n    }\n}\n"),
            "go": ("example.go", "package example\n\ntype Example struct {}\n\nfunc run(value int) int {\n    if value > 0 { return 1 }\n    return 0\n}\n"),
            "rust": ("example.rs", "struct Example {}\n\nfn run(value: i32) -> i32 {\n    if value > 0 { return 1; }\n    0\n}\n"),
        }

        with tempfile.TemporaryDirectory() as td:
            for language, (filename, source) in examples.items():
                path = Path(td) / filename
                path.write_text(source, encoding="utf-8")
                adapter = adapter_for_path(path)
                size_metrics = adapter.parse_code_size_metrics(path, source, config)
                complexity_metrics = adapter.parse_complexity_metrics(path.as_posix(), source)

                self.assertTrue(any(metric.kind == "type" for metric in size_metrics), language)
                self.assertTrue(any(metric.kind == "method" for metric in size_metrics), language)
                self.assertEqual([metric.name for metric in complexity_metrics], ["run"], language)
                self.assertEqual(complexity_metrics[0].complexity, 2, language)

    def test_javascript_arrow_go_receiver_and_rust_impl_methods_are_detected(self) -> None:
        config = CodeSizeConfig(
            include_roots=["src"],
            exclude_globs=[],
            method_warn_lines=40,
            method_max_lines=60,
            type_warn_lines=250,
            type_max_lines=350,
            file_warn_lines=300,
            file_max_lines=450,
        )
        examples = {
            "typescript": ("example.ts", "const run = (value: number) => {\n    if (value) return 1;\n    return 0;\n};\n"),
            "javascript": ("example.mjs", "function run(value) {\n    if (value) return 1;\n    return 0;\n}\n"),
            "go": ("example.go", "type Example struct {}\n\nfunc (e *Example) Run(value int) int {\n    if value > 0 { return 1 }\n    return 0\n}\n"),
            "rust": ("example.rs", "struct Example {}\n\nimpl Example {\n    fn run(value: i32) -> i32 {\n        if value > 0 { return 1; }\n        0\n    }\n}\n"),
        }

        with tempfile.TemporaryDirectory() as td:
            for language, (filename, source) in examples.items():
                path = Path(td) / filename
                path.write_text(source, encoding="utf-8")
                adapter = adapter_for_path(path)
                size_metrics = adapter.parse_code_size_metrics(path, source, config)
                complexity_metrics = adapter.parse_complexity_metrics(path.as_posix(), source)

                self.assertTrue(any(metric.kind == "method" for metric in size_metrics), language)
                self.assertEqual([metric.name.lower() for metric in complexity_metrics], ["run"], language)

    def test_cli_reports_selected_language_in_json_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "example.py").write_text(
                "def run(value):\n    return value\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["dotnet-quality", "--repo-root", str(root), "--language", "python", "--output", "json", "code-size"],
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = cli.main()

        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue())["language"], "python")

    def test_java_namespace_and_type_layout_use_the_language_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_root = root / "src" / "com" / "example"
            source_root.mkdir(parents=True)
            source_file = source_root / "Example.java"
            source_file.write_text(
                "package com.example;\n\nclass Example {}\nclass Another {}\n",
                encoding="utf-8",
            )
            original_namespace_root = check_namespace_layout.REPO_ROOT
            original_type_root = check_source_type_layout.REPO_ROOT
            check_namespace_layout.REPO_ROOT = root
            check_source_type_layout.REPO_ROOT = root
            try:
                with patch.dict("os.environ", {"DOTNET_QUALITY_LANGUAGE": "auto"}):
                    namespace_violations, fixed = check_namespace_layout.validate_source_namespace_layout(
                        [root / "src"], [],
                    )
                    type_violations = check_source_type_layout.validate_source_type_layout([root / "src"], [])
            finally:
                check_namespace_layout.REPO_ROOT = original_namespace_root
                check_source_type_layout.REPO_ROOT = original_type_root

        self.assertEqual(namespace_violations, [])
        self.assertEqual(fixed, 0)
        self.assertEqual(len(type_violations), 1)
        self.assertIn("Example.java", type_violations[0])


if __name__ == "__main__":
    unittest.main()
