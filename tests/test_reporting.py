from __future__ import annotations

import unittest

from dotnet_quality_gates.reporting import (
    add_result_diagnostics,
    console_summary,
    extract_blocking_errors,
    markdown_summary,
)


class ReportingTests(unittest.TestCase):
    def test_extracts_failure_details_without_promoting_warnings(self) -> None:
        errors = extract_blocking_errors(
            1,
            "Code size warnings:\n - warning detail\n",
            "Code size gate failed.\n - src/Example.cs:10: file exceeds 450 lines\n",
        )

        self.assertEqual(errors, ["src/Example.cs:10: file exceeds 450 lines"])

    def test_preserves_single_line_diagnostic_when_no_bullets_exist(self) -> None:
        errors = extract_blocking_errors(1, "", "Coverage file not found: coverage.xml\n")

        self.assertEqual(errors, ["Coverage file not found: coverage.xml"])

    def test_result_contains_normalized_blocking_errors(self) -> None:
        payload = add_result_diagnostics(
            {
                "command": "namespace-layout",
                "status": "failed",
                "returncode": 1,
                "stdout": "",
                "stderr": "Source namespace layout check failed.\n - src/Example.cs:1: Missing namespace.\n",
            }
        )

        self.assertEqual(payload["violations"], ["src/Example.cs:1: Missing namespace."])
        self.assertEqual(payload["blocking_errors"], ["src/Example.cs:1: Missing namespace."])

    def test_prefers_structured_diagnostics_over_scraped_output(self) -> None:
        payload = add_result_diagnostics(
            {
                "command": "namespace-layout",
                "status": "failed",
                "returncode": 1,
                "violations": ["structured violation"],
                "blocking_errors": ["structured blocking error"],
                "stdout": " - stale output detail\n",
                "stderr": " - another stale output detail\n",
            }
        )

        self.assertEqual(payload["violations"], ["structured violation"])
        self.assertEqual(payload["blocking_errors"], ["structured blocking error"])

    def test_summaries_are_easy_to_scan(self) -> None:
        payload = {
            "command": "code-size",
            "status": "failed",
            "blocking_errors": ["src/Example.cs:10: file is too large"],
            "warnings": [],
        }

        self.assertIn("[FAIL] Quality gate: code-size", console_summary(payload))
        self.assertIn("### Blocking errors", markdown_summary(payload))
        self.assertIn("src/Example.cs:10: file is too large", markdown_summary(payload))


if __name__ == "__main__":
    unittest.main()
