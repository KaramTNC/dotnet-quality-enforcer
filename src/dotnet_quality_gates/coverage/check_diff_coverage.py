from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

from dotnet_quality_gates.quality.common import (
    load_policy_object,
    parse_changed_lines,
    policy_section,
)

REPO_ROOT = Path(os.environ.get("DOTNET_QUALITY_REPO_ROOT", Path.cwd())).resolve()
DEFAULT_POLICY_PATH = REPO_ROOT / ".quality" / "quality_policy.json"
DEFAULT_COVERAGE_FILEFILTERS_PATH = REPO_ROOT / ".quality" / "coverage_filefilters.txt"
DEFAULT_LINE_THRESHOLD = 0.80
DEFAULT_BRANCH_THRESHOLD: float | None = None
DEFAULT_MAX_FILES_FOR_GATE = 100
EXECUTABLE_LINE_PATTERN = re.compile(
    r"^(?:"
    r"await|break|case|catch|const|continue|do|else\s+if|for|foreach|if|lock|"
    r"return|switch|throw|try|using|var|while|yield"
    r")\b"
)


def load_diff_coverage_config(policy_path: Path) -> tuple[float, float | None, int]:
    section = policy_section(load_policy_object(policy_path, "diff coverage"), "diff_quality")
    line_threshold = section.get("line_coverage_threshold", DEFAULT_LINE_THRESHOLD)
    branch_threshold = section.get("branch_coverage_threshold", DEFAULT_BRANCH_THRESHOLD)
    max_files = section.get("max_files_for_gate", DEFAULT_MAX_FILES_FOR_GATE)

    if not isinstance(line_threshold, (int, float)) or not 0 <= line_threshold <= 1:
        line_threshold = DEFAULT_LINE_THRESHOLD
    if branch_threshold is not None and (
        not isinstance(branch_threshold, (int, float)) or not 0 <= branch_threshold <= 1
    ):
        branch_threshold = DEFAULT_BRANCH_THRESHOLD
    if not isinstance(max_files, int) or max_files < 1:
        max_files = DEFAULT_MAX_FILES_FOR_GATE

    return float(line_threshold), None if branch_threshold is None else float(branch_threshold), max_files


def run_git_diff(base: str) -> str:
    result = subprocess.run(
        [
            "git",
            "diff",
            "--unified=0",
            f"{base}...HEAD",
            "--",
            ":(glob)src/**/*.cs",
            ":(exclude)**/AssemblyInfo.cs",
            ":(exclude)**/*.g.cs",
            ":(exclude)**/*.g.i.cs",
        ],
        check=True,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
    )
    return result.stdout


def parse_coverage(path: Path) -> dict[str, dict[int, int]]:
    tree = ET.parse(path)
    root = tree.getroot()
    coverage: dict[str, dict[int, int]] = defaultdict(dict)

    for class_node in root.findall(".//class"):
        filename = class_node.attrib.get("filename")
        if not filename:
            continue

        normalized = filename.replace("\\", "/")
        for line_node in class_node.findall("./lines/line"):
            number = int(line_node.attrib["number"])
            hits = int(line_node.attrib["hits"])
            coverage[normalized][number] = max(hits, coverage[normalized].get(number, 0))

    return coverage


def parse_condition_coverage(line_node: ET.Element) -> tuple[int, int]:
    condition_coverage = line_node.attrib.get("condition-coverage")
    if condition_coverage:
        match = re.search(r"\((\d+)/(\d+)\)", condition_coverage)
        if match:
            return int(match.group(1)), int(match.group(2))

    conditions = line_node.findall("./conditions/condition")
    if conditions:
        covered = 0
        total = 0
        for condition in conditions:
            total += 1
            coverage = condition.attrib.get("coverage", "0").rstrip("%")
            try:
                if float(coverage) > 0:
                    covered += 1
            except ValueError:
                continue
        return covered, total

    return 0, 0


def parse_branch_coverage(path: Path) -> dict[str, dict[int, tuple[int, int]]]:
    tree = ET.parse(path)
    root = tree.getroot()
    coverage: dict[str, dict[int, tuple[int, int]]] = defaultdict(dict)

    for class_node in root.findall(".//class"):
        filename = class_node.attrib.get("filename")
        if not filename:
            continue

        normalized = filename.replace("\\", "/")
        for line_node in class_node.findall("./lines/line"):
            if line_node.attrib.get("branch", "").lower() != "true":
                continue

            number = int(line_node.attrib["number"])
            covered, total = parse_condition_coverage(line_node)
            previous_covered, previous_total = coverage[normalized].get(number, (0, 0))
            coverage[normalized][number] = (
                max(covered, previous_covered),
                max(total, previous_total),
            )

    return coverage


def resolve_coverage_file(file_path: str, coverage: Mapping[str, object]) -> str | None:
    normalized = file_path.replace("\\", "/")
    if normalized in coverage:
        return normalized

    suffix = f"/{normalized}"
    matches = [candidate for candidate in coverage if candidate.endswith(suffix) or candidate.endswith(normalized)]
    if len(matches) == 1:
        return matches[0]

    return None


def load_coverage_exclude_filters(path: Path = DEFAULT_COVERAGE_FILEFILTERS_PATH) -> list[str]:
    if not path.exists():
        return []

    filters: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith("-"):
            continue

        pattern = line[1:].replace("\\", "/")
        if pattern:
            filters.append(pattern)

    return filters


def is_coverage_excluded(file_path: str, exclude_filters: list[str]) -> bool:
    normalized = file_path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in exclude_filters)


def is_probably_executable_source_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped in {"{", "}", "};", ");"}:
        return False
    if stripped.startswith(("///", "//", "/*", "*", "[", "#", "using ", "namespace ")):
        return False
    if stripped.startswith(("public ", "protected ", "internal ", "private ")):
        return "=>" in stripped
    if stripped.startswith(("class ", "interface ", "record ", "struct ", "enum ")):
        return False
    if EXECUTABLE_LINE_PATTERN.match(stripped):
        return True
    if stripped.startswith(("Log.", "_")):
        return True
    return "=" in stripped and "{ get; set; }" not in stripped


def changed_executable_lines_without_coverage(file_path: str, lines: set[int]) -> list[int]:
    path = REPO_ROOT / file_path
    if not path.exists():
        return []

    source_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    executable_lines: list[int] = []
    for line_number in sorted(lines):
        if line_number < 1 or line_number > len(source_lines):
            continue
        if is_probably_executable_source_line(source_lines[line_number - 1]):
            executable_lines.append(line_number)

    return executable_lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--coverage", required=True)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--branch-threshold", type=float, default=None)
    parser.add_argument("--max-files-for-gate", type=int, default=None)
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument(
        "--filters-file",
        default=str(DEFAULT_COVERAGE_FILEFILTERS_PATH),
        help="Optional file containing coverage exclusion globs.",
    )
    args = parser.parse_args()

    policy_line_threshold, policy_branch_threshold, policy_max_files = load_diff_coverage_config(
        Path(args.policy_path)
    )
    line_threshold = args.threshold if args.threshold is not None else policy_line_threshold
    branch_threshold = (
        args.branch_threshold if args.branch_threshold is not None else policy_branch_threshold
    )
    max_files_for_gate = args.max_files_for_gate if args.max_files_for_gate is not None else policy_max_files

    coverage_path = Path(args.coverage)
    if not coverage_path.exists():
        print(f"Coverage file not found: {coverage_path}", file=sys.stderr)
        return 1

    try:
        changed = parse_changed_lines(run_git_diff(args.base))
    except (OSError, subprocess.CalledProcessError) as ex:
        detail = getattr(ex, "stderr", None) or str(ex)
        print(f"Unable to compute git diff against '{args.base}': {detail.strip()}", file=sys.stderr)
        return 1
    if not changed:
        print("No changed production .cs files detected; skipping diff coverage gate.")
        return 0

    if len(changed) > max_files_for_gate:
        print(
            f"Diff coverage gate skipped: {len(changed)} changed production files exceeds "
            f"maintenance threshold of {max_files_for_gate}."
        )
        return 0

    try:
        coverage = parse_coverage(coverage_path)
        coverage_exclude_filters = load_coverage_exclude_filters(Path(args.filters_file))
        branch_coverage = parse_branch_coverage(coverage_path) if branch_threshold is not None else {}
    except (OSError, ET.ParseError, KeyError, ValueError) as ex:
        print(f"Unable to read coverage report '{coverage_path}': {ex}", file=sys.stderr)
        return 1
    covered_lines = 0
    relevant_lines = 0
    covered_branches = 0
    relevant_branches = 0
    uncovered_details: list[str] = []
    uncovered_branch_details: list[str] = []

    for file_path, lines in sorted(changed.items()):
        if is_coverage_excluded(file_path, coverage_exclude_filters):
            continue

        coverage_key = resolve_coverage_file(file_path, coverage)
        line_hits = coverage.get(coverage_key, {}) if coverage_key is not None else {}
        branch_hits = branch_coverage.get(coverage_key, {}) if coverage_key is not None else {}

        if coverage_key is None:
            missing_without_coverage = changed_executable_lines_without_coverage(file_path, lines)
            if not missing_without_coverage:
                continue

            relevant_lines += len(missing_without_coverage)
            preview = ", ".join(str(number) for number in missing_without_coverage[:10])
            suffix = "..." if len(missing_without_coverage) > 10 else ""
            uncovered_details.append(
                f"{file_path}: no coverage data for changed executable lines {preview}{suffix}"
            )
            continue

        tracked_lines = 0
        tracked_covered = 0
        tracked_branches = 0
        tracked_covered_branches = 0
        missing_lines: list[int] = []
        missing_branches: list[str] = []
        executable_changed_lines = set(changed_executable_lines_without_coverage(file_path, lines))

        for line_number in sorted(lines):
            if line_number not in line_hits:
                if line_number in executable_changed_lines:
                    relevant_lines += 1
                    missing_lines.append(line_number)
                continue

            tracked_lines += 1
            if line_hits[line_number] > 0:
                tracked_covered += 1
            else:
                missing_lines.append(line_number)

            if branch_threshold is not None and line_number in branch_hits:
                branch_covered, branch_total = branch_hits[line_number]
                if branch_total <= 0:
                    continue
                tracked_branches += branch_total
                tracked_covered_branches += branch_covered
                if branch_covered < branch_total:
                    missing_branches.append(f"{line_number} ({branch_covered}/{branch_total})")

        if tracked_lines == 0:
            if missing_lines:
                preview = ", ".join(str(number) for number in missing_lines[:10])
                suffix = "..." if len(missing_lines) > 10 else ""
                uncovered_details.append(
                    f"{file_path}: no coverage data for changed executable lines {preview}{suffix}"
                )
            continue

        relevant_lines += tracked_lines
        covered_lines += tracked_covered
        relevant_branches += tracked_branches
        covered_branches += tracked_covered_branches

        if missing_lines:
            preview = ", ".join(str(number) for number in missing_lines[:10])
            suffix = "..." if len(missing_lines) > 10 else ""
            uncovered_details.append(f"{file_path}: uncovered changed lines {preview}{suffix}")

        if missing_branches:
            preview = ", ".join(missing_branches[:10])
            suffix = "..." if len(missing_branches) > 10 else ""
            uncovered_branch_details.append(f"{file_path}: uncovered changed branches {preview}{suffix}")

    if relevant_lines == 0:
        print("No executable changed lines were found in coverage data; skipping diff coverage gate.")
        return 0

    ratio = covered_lines / relevant_lines
    print(
        f"Diff coverage: {covered_lines}/{relevant_lines} executable changed lines covered "
        f"({ratio:.1%}); threshold {line_threshold:.0%}"
    )

    if ratio + 1e-9 < line_threshold:
        print("Changed-line coverage gate failed.", file=sys.stderr)
        for detail in uncovered_details:
            print(f" - {detail}", file=sys.stderr)
        return 1

    if branch_threshold is not None and relevant_branches > 0:
        branch_ratio = covered_branches / relevant_branches
        print(
            f"Diff branch coverage: {covered_branches}/{relevant_branches} changed branches covered "
            f"({branch_ratio:.1%}); threshold {branch_threshold:.0%}"
        )

        if branch_ratio + 1e-9 < branch_threshold:
            print("Changed-branch coverage gate failed.", file=sys.stderr)
            for detail in uncovered_branch_details:
                print(f" - {detail}", file=sys.stderr)
            return 1

    elif branch_threshold is not None:
        print("No changed branch lines were found in coverage data; skipping diff branch coverage gate.")

    print("Changed-line coverage gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
