from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


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

    completed = subprocess.run(command, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
