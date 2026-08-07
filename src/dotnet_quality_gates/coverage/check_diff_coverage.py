from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

from dotnet_quality_gates.context import current_context
from dotnet_quality_gates.languages import source_diff_pathspecs
from dotnet_quality_gates.quality.common import load_policy_object, parse_changed_lines, policy_section
from dotnet_quality_gates.subprocess_utils import run_command

REPO_ROOT = current_context().repo_root
DEFAULT_POLICY_PATH = current_context().policy_path
DEFAULT_COVERAGE_FILEFILTERS_PATH = REPO_ROOT / ".quality" / "coverage_filefilters.txt"
DEFAULT_LINE_THRESHOLD = 0.80
DEFAULT_BRANCH_THRESHOLD: float | None = None
DEFAULT_MAX_FILES_FOR_GATE: int | None = None
EXECUTABLE_LINE_PATTERN = re.compile(
    r"^(?:await|break|case|catch|const|continue|do|else\s+if|for|foreach|if|lock|"
    r"return|switch|throw|try|using|var|while|yield)\b"
)
MAX_COVERAGE_XML_BYTES = 50 * 1024 * 1024
UNSAFE_XML_DECLARATION_PATTERN = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def load_diff_coverage_config(policy_path: Path) -> tuple[float, float | None, int | None]:
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
    if max_files is not None and (not isinstance(max_files, int) or isinstance(max_files, bool) or max_files < 1):
        max_files = DEFAULT_MAX_FILES_FOR_GATE
    return float(line_threshold), None if branch_threshold is None else float(branch_threshold), max_files


def run_git_diff(base: str) -> str:
    result = run_command(
        [
            "git", "diff", "--unified=0", f"{base}...HEAD", "--",
            *source_diff_pathspecs(language=current_context().language),
            ":(exclude)**/AssemblyInfo.cs",
            ":(exclude)**/*.g.cs",
            ":(exclude)**/*.g.i.cs",
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    return result.stdout


def parse_safe_xml(path: Path) -> ET.Element:
    with path.open("rb") as stream:
        content = stream.read(MAX_COVERAGE_XML_BYTES + 1)

    if len(content) > MAX_COVERAGE_XML_BYTES:
        raise ValueError(
            f"XML report exceeds the {MAX_COVERAGE_XML_BYTES} byte safety limit"
        )
    if UNSAFE_XML_DECLARATION_PATTERN.search(content):
        raise ValueError("XML reports must not contain DTD or entity declarations")

    # ElementTree does not fetch external resources, while the declaration check
    # above prevents DTD-based entity expansion before parsing untrusted reports.
    return ET.fromstring(content)


def parse_coverage(path: Path) -> dict[str, dict[int, int]]:
    root = parse_safe_xml(path)
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
    if not conditions:
        return 0, 0

    covered = 0
    for condition in conditions:
        coverage = condition.attrib.get("coverage", "0").rstrip("%")
        try:
            covered += float(coverage) > 0
        except ValueError:
            continue
    return covered, len(conditions)


def parse_branch_coverage(path: Path) -> dict[str, dict[int, tuple[int, int]]]:
    root = parse_safe_xml(path)
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
            coverage[normalized][number] = max(covered, previous_covered), max(total, previous_total)
    return coverage


def resolve_coverage_file(file_path: str, coverage: Mapping[str, object]) -> str | None:
    normalized = file_path.replace("\\", "/")
    if normalized in coverage:
        return normalized
    suffix = f"/{normalized}"
    matches = [candidate for candidate in coverage if candidate.endswith(suffix) or candidate.endswith(normalized)]
    return matches[0] if len(matches) == 1 else None


def load_coverage_exclude_filters(path: Path = DEFAULT_COVERAGE_FILEFILTERS_PATH) -> list[str]:
    if not path.exists():
        return []
    filters: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and line.startswith("-"):
            pattern = line[1:].replace("\\", "/")
            if pattern:
                filters.append(pattern)
    return filters


def is_coverage_excluded(file_path: str, exclude_filters: list[str]) -> bool:
    normalized = file_path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in exclude_filters)


def is_probably_executable_source_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped in {"{", "}", "};", ");"}:
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
    return [
        line_number
        for line_number in sorted(lines)
        if 1 <= line_number <= len(source_lines)
        and is_probably_executable_source_line(source_lines[line_number - 1])
    ]


def _missing_file_stats(file_path: str, lines: set[int]) -> tuple[int, list[str]]:
    missing = changed_executable_lines_without_coverage(file_path, lines)
    if not missing:
        return 0, []
    preview = ", ".join(str(number) for number in missing[:10])
    suffix = "..." if len(missing) > 10 else ""
    return len(missing), [f"{file_path}: no coverage data for changed executable lines {preview}{suffix}"]


def _tracked_file_stats(
    file_path: str,
    lines: set[int],
    line_hits: dict[int, int],
    branch_hits: dict[int, tuple[int, int]],
    branch_enabled: bool,
) -> tuple[int, int, int, int, list[str], list[str]]:
    relevant_lines = 0
    missing_lines: list[int] = []
    missing_branches: list[str] = []
    executable_changed_lines = set(changed_executable_lines_without_coverage(file_path, lines))
    tracked_lines = tracked_covered = tracked_branches = tracked_covered_branches = 0

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
        if branch_enabled and line_number in branch_hits:
            branch_covered, branch_total = branch_hits[line_number]
            if branch_total > 0:
                tracked_branches += branch_total
                tracked_covered_branches += branch_covered
                if branch_covered < branch_total:
                    missing_branches.append(f"{line_number} ({branch_covered}/{branch_total})")

    if tracked_lines == 0:
        preview = ", ".join(str(number) for number in missing_lines[:10])
        suffix = "..." if len(missing_lines) > 10 else ""
        details = [f"{file_path}: no coverage data for changed executable lines {preview}{suffix}"] if missing_lines else []
        return len(missing_lines), 0, 0, 0, details, []

    relevant_lines = tracked_lines
    details = []
    if missing_lines:
        preview = ", ".join(str(number) for number in missing_lines[:10])
        suffix = "..." if len(missing_lines) > 10 else ""
        details.append(f"{file_path}: uncovered changed lines {preview}{suffix}")
    branch_details = []
    if missing_branches:
        preview = ", ".join(missing_branches[:10])
        suffix = "..." if len(missing_branches) > 10 else ""
        branch_details.append(f"{file_path}: uncovered changed branches {preview}{suffix}")
    return relevant_lines, tracked_covered, tracked_branches, tracked_covered_branches, details, branch_details


def _evaluate_changed_files(
    changed: dict[str, set[int]],
    coverage: dict[str, dict[int, int]],
    branch_coverage: dict[str, dict[int, tuple[int, int]]],
    exclude_filters: list[str],
    branch_enabled: bool,
) -> tuple[int, int, int, int, list[str], list[str]]:
    totals = [0, 0, 0, 0]
    uncovered_details: list[str] = []
    uncovered_branch_details: list[str] = []
    for file_path, lines in sorted(changed.items()):
        if is_coverage_excluded(file_path, exclude_filters):
            continue
        coverage_key = resolve_coverage_file(file_path, coverage)
        if coverage_key is None:
            relevant, details = _missing_file_stats(file_path, lines)
            totals[0] += relevant
            uncovered_details.extend(details)
            continue
        stats = _tracked_file_stats(
            file_path,
            lines,
            coverage.get(coverage_key, {}),
            branch_coverage.get(coverage_key, {}),
            branch_enabled,
        )
        totals[0] += stats[0]
        totals[1] += stats[1]
        totals[2] += stats[2]
        totals[3] += stats[3]
        uncovered_details.extend(stats[4])
        uncovered_branch_details.extend(stats[5])
    return (
        totals[0],
        totals[1],
        totals[2],
        totals[3],
        uncovered_details,
        uncovered_branch_details,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--coverage", required=True)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--branch-threshold", type=float, default=None)
    parser.add_argument("--max-files-for-gate", type=int, default=None)
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--filters-file", default=str(DEFAULT_COVERAGE_FILEFILTERS_PATH))
    return parser.parse_args()


def _resolve_thresholds(args: argparse.Namespace) -> tuple[float, float | None, int | None]:
    policy_line, policy_branch, policy_max = load_diff_coverage_config(Path(args.policy_path))
    return (
        args.threshold if args.threshold is not None else policy_line,
        args.branch_threshold if args.branch_threshold is not None else policy_branch,
        args.max_files_for_gate if args.max_files_for_gate is not None else policy_max,
    )


def _load_coverage_inputs(
    args: argparse.Namespace,
    branch_threshold: float | None,
) -> tuple[dict[str, dict[int, int]], list[str], dict[str, dict[int, tuple[int, int]]] | None] | None:
    coverage_path = Path(args.coverage)
    try:
        coverage = parse_coverage(coverage_path)
        filters = load_coverage_exclude_filters(Path(args.filters_file))
        branch_coverage = parse_branch_coverage(coverage_path) if branch_threshold is not None else None
    except (OSError, ET.ParseError, KeyError, ValueError) as ex:
        print(f"Unable to read coverage report '{coverage_path}': {ex}", file=sys.stderr)
        return None
    return coverage, filters, branch_coverage


def _report_coverage_result(
    relevant: int,
    covered: int,
    line_threshold: float,
    uncovered: list[str],
    relevant_branches: int,
    branch_covered: int,
    branch_threshold: float | None,
    uncovered_branches: list[str],
) -> int:
    if relevant == 0:
        print("No executable changed lines were found in coverage data; skipping diff coverage gate.")
        return 0
    ratio = covered / relevant
    print(f"Diff coverage: {covered}/{relevant} executable changed lines covered ({ratio:.1%}); threshold {line_threshold:.0%}")
    if ratio + 1e-9 < line_threshold:
        print("Changed-line coverage gate failed.", file=sys.stderr)
        for detail in uncovered:
            print(f" - {detail}", file=sys.stderr)
        return 1
    if branch_threshold is not None and relevant_branches > 0:
        branch_ratio = branch_covered / relevant_branches
        print(f"Diff branch coverage: {branch_covered}/{relevant_branches} changed branches covered ({branch_ratio:.1%}); threshold {branch_threshold:.0%}")
        if branch_ratio + 1e-9 < branch_threshold:
            print("Changed-branch coverage gate failed.", file=sys.stderr)
            for detail in uncovered_branches:
                print(f" - {detail}", file=sys.stderr)
            return 1
    elif branch_threshold is not None:
        print("No changed branch lines were found in coverage data; skipping diff branch coverage gate.")
    print("Changed-line coverage gate passed.")
    return 0


def main() -> int:
    args = _parse_args()
    line_threshold, branch_threshold, max_files = _resolve_thresholds(args)
    coverage_path = Path(args.coverage)
    if not coverage_path.exists():
        print(f"Coverage file not found: {coverage_path}", file=sys.stderr)
        return 1

    try:
        changed = parse_changed_lines(run_git_diff(args.base))
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as ex:
        detail = getattr(ex, "stderr", None) or str(ex)
        print(f"Unable to compute git diff against '{args.base}': {detail.strip()}", file=sys.stderr)
        return 1
    if not changed:
        print("No changed production source files detected; skipping diff coverage gate.")
        return 0
    if max_files is not None and len(changed) > max_files:
        print(f"Diff coverage gate skipped: {len(changed)} changed production files exceeds maintenance threshold of {max_files}.")
        return 0

    coverage_inputs = _load_coverage_inputs(args, branch_threshold)
    if coverage_inputs is None:
        return 1
    coverage, filters, branch_coverage = coverage_inputs
    relevant, covered, relevant_branches, branch_covered, uncovered, uncovered_branches = _evaluate_changed_files(
        changed, coverage, branch_coverage or {}, filters, branch_threshold is not None
    )
    return _report_coverage_result(
        relevant,
        covered,
        line_threshold,
        uncovered,
        relevant_branches,
        branch_covered,
        branch_threshold,
        uncovered_branches,
    )


if __name__ == "__main__":
    raise SystemExit(main())
