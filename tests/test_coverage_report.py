from __future__ import annotations

import contextlib
import io
import sys
import unittest
from unittest.mock import patch

from dotnet_quality_gates.coverage import generate_coverage_report


class CoverageReportTests(unittest.TestCase):
    def test_missing_reportgenerator_has_a_stable_exit_code(self) -> None:
        stderr = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                ["coverage-report", "--reports", "coverage.xml", "--targetdir", "report"],
            ),
            patch.object(generate_coverage_report, "run_command", side_effect=FileNotFoundError),
            contextlib.redirect_stderr(stderr),
        ):
            result = generate_coverage_report.main()

        self.assertEqual(result, 127)
        self.assertIn("ReportGenerator executable was not found", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
