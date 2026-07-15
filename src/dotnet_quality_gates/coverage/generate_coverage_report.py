from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from dotnet_quality_gates.subprocess_utils import run_command


def load_filefilters(path: Path) -> str:
    if not path.exists():
        return ""

    filters: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        filters.append(line)

    return ";".join(filters)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", required=True)
    parser.add_argument("--targetdir", required=True)
    parser.add_argument(
        "--reporttypes",
        default="Html;Cobertura;TextSummary",
    )
    parser.add_argument(
        "--filters-file",
        default=".quality/coverage_filefilters.txt",
    )
    args = parser.parse_args()

    command = [
        "reportgenerator",
        f"-reports:{args.reports}",
        f"-targetdir:{args.targetdir}",
        f"-reporttypes:{args.reporttypes}",
        "-verbosity:Warning",
    ]

    filefilters = load_filefilters(Path(args.filters_file))
    if filefilters:
        command.append(f"-filefilters:{filefilters}")

    try:
        completed = run_command(command, capture_output=False)
    except FileNotFoundError:
        print("ReportGenerator executable was not found on PATH.", file=sys.stderr)
        return 127
    except subprocess.TimeoutExpired:
        print("ReportGenerator exceeded the configured command timeout.", file=sys.stderr)
        return 124
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
