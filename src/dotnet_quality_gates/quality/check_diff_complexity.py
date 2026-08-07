from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from dotnet_quality_gates.context import current_context
from dotnet_quality_gates.coverage.check_diff_coverage import (
    parse_changed_lines,
    parse_coverage,
    resolve_coverage_file,
    run_git_diff,
)
from dotnet_quality_gates.quality.common import load_policy_object, policy_section
from dotnet_quality_gates.quality.diff_complexity_metric import MethodMetric
from dotnet_quality_gates.quality.diff_complexity_parsing import (
    changed_methods,
    parse_methods,
)
from dotnet_quality_gates.subprocess_utils import run_command

REPO_ROOT = current_context().repo_root
DEFAULT_POLICY_PATH = current_context().policy_path

DEFAULT_CYCLOMATIC_MAX = 10
DEFAULT_COGNITIVE_MAX = 10
DEFAULT_CRAP_MAX = 30.0
DEFAULT_MAX_FILES_FOR_GATE: int | None = None


def load_diff_quality_config(policy_path: Path) -> tuple[int, int, float, int | None]:
    section = policy_section(load_policy_object(policy_path, "diff quality"), "diff_quality")
    cyclomatic_max = section.get("cyclomatic_complexity_max", DEFAULT_CYCLOMATIC_MAX)
    cognitive_max = section.get("cognitive_complexity_max", DEFAULT_COGNITIVE_MAX)
    crap_max = section.get("crap_score_max", DEFAULT_CRAP_MAX)
    max_files = section.get("max_files_for_gate", DEFAULT_MAX_FILES_FOR_GATE)

    if not isinstance(cyclomatic_max, int) or cyclomatic_max < 1:
        cyclomatic_max = DEFAULT_CYCLOMATIC_MAX
    if not isinstance(cognitive_max, int) or cognitive_max < 1:
        cognitive_max = DEFAULT_COGNITIVE_MAX
    if not isinstance(crap_max, (int, float)) or crap_max < 1:
        crap_max = DEFAULT_CRAP_MAX
    if max_files is not None and (not isinstance(max_files, int) or isinstance(max_files, bool) or max_files < 1):
        max_files = DEFAULT_MAX_FILES_FOR_GATE

    return cyclomatic_max, cognitive_max, float(crap_max), max_files


def read_git_file(base: str, path: str) -> str | None:
    result = run_command(["git", "show", f"{base}:{path}"], cwd=REPO_ROOT)
    return result.stdout if result.returncode == 0 else None


def add_coverage(method: MethodMetric, line_hits: dict[int, int]) -> MethodMetric:
    coverable = 0
    covered = 0
    for line_number, hits in line_hits.items():
        if method.start_line <= line_number <= method.end_line:
            coverable += 1
            if hits > 0:
                covered += 1

    return MethodMetric(
        path=method.path,
        name=method.name,
        signature_key=method.signature_key,
        start_line=method.start_line,
        end_line=method.end_line,
        complexity=method.complexity,
        cognitive_complexity=method.cognitive_complexity,
        coverable_lines=coverable,
        covered_lines=covered,
    )


def parse_coverage_hits(coverage_path: Path) -> dict[str, dict[int, int]]:
    if not coverage_path.exists():
        raise FileNotFoundError(f"Coverage file not found: {coverage_path}")
    try:
        return parse_coverage(coverage_path)
    except ET.ParseError as ex:
        raise ValueError(f"Failed to parse coverage file '{coverage_path}': {ex}") from ex


def normalize_coverage_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute():
        try:
            normalized = candidate.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            normalized = candidate.as_posix()
    return normalized


def parse_coverage_methods(coverage_path: Path) -> dict[str, list[MethodMetric]]:
    if not coverage_path.exists():
        raise FileNotFoundError(f"Coverage file not found: {coverage_path}")

    try:
        root = ET.parse(coverage_path).getroot()
    except ET.ParseError as ex:
        raise ValueError(f"Failed to parse coverage file '{coverage_path}': {ex}") from ex

    methods_by_path: dict[str, list[MethodMetric]] = {}
    for class_node in root.findall(".//class"):
        filename = class_node.get("filename")
        if not filename:
            continue

        file_path = normalize_coverage_path(filename)
        for method_node in class_node.findall("./methods/method"):
            line_nodes = method_node.findall("./lines/line")
            line_numbers = [
                int(line_node.get("number", "0"))
                for line_node in line_nodes
                if line_node.get("number")
            ]
            if not line_numbers:
                continue

            complexity = int(float(method_node.get("complexity", "0")))
            covered = sum(1 for line_node in line_nodes if int(line_node.get("hits", "0")) > 0)
            method = MethodMetric(
                path=file_path,
                name=method_node.get("name", "<unknown>"),
                signature_key=f"{method_node.get('name', '<unknown>')}{method_node.get('signature', '')}",
                start_line=min(line_numbers),
                end_line=max(line_numbers),
                complexity=complexity,
                coverable_lines=len(line_nodes),
                covered_lines=covered,
            )
            methods_by_path.setdefault(file_path, []).append(method)

    return methods_by_path


def ranges_overlap(left: MethodMetric, right: MethodMetric) -> bool:
    return left.start_line <= right.end_line and right.start_line <= left.end_line


def changed_coverage_methods(methods: list[MethodMetric], changed_lines: set[int]) -> list[MethodMetric]:
    return [
        method
        for method in methods
        if any(method.start_line <= line <= method.end_line for line in changed_lines)
    ]


def coverage_method_id(method: MethodMetric) -> tuple[str, int, int, str]:
    return (method.name, method.start_line, method.end_line, method.signature_key)


def matching_coverage_method(
    method: MethodMetric,
    coverage_methods: list[MethodMetric],
) -> MethodMetric | None:
    candidates = [candidate for candidate in coverage_methods if ranges_overlap(method, candidate)]
    if not candidates:
        return None

    same_name = [candidate for candidate in candidates if candidate.name == method.name]
    if same_name:
        candidates = same_name

    return max(
        candidates,
        key=lambda candidate: min(method.end_line, candidate.end_line)
        - max(method.start_line, candidate.start_line),
    )


def with_reported_complexity(
    method: MethodMetric,
    coverage_method: MethodMetric | None,
) -> MethodMetric:
    if coverage_method is None:
        return method
    return MethodMetric(
        path=method.path,
        name=method.name,
        signature_key=method.signature_key,
        start_line=method.start_line,
        end_line=method.end_line,
        complexity=coverage_method.complexity,
        cognitive_complexity=method.cognitive_complexity,
        coverable_lines=coverage_method.coverable_lines,
        covered_lines=coverage_method.covered_lines,
    )


def _method_violations(
    method: MethodMetric,
    cyclomatic_max: int,
    cognitive_max: int,
    crap_max: float,
) -> list[str]:
    location = f"{method.path}:{method.start_line}: {method.name}"
    violations: list[str] = []
    if method.complexity > cyclomatic_max:
        violations.append(
            f"{location} has cyclomatic complexity {method.complexity}; "
            f"maximum allowed for changed methods is {cyclomatic_max}."
        )
    if method.cognitive_complexity > cognitive_max:
        violations.append(
            f"{location} has cognitive complexity {method.cognitive_complexity}; "
            f"maximum allowed for changed methods is {cognitive_max}."
        )
    if method.crap_score > crap_max:
        violations.append(
            f"{location} has CRAP score {method.crap_score:.2f}; maximum allowed is "
            f"{crap_max:.2f}. Complexity {method.complexity}, method coverage "
            f"{method.covered_lines}/{method.coverable_lines}."
        )
    return violations


def validate_diff_complexity(
    base: str,
    changed: dict[str, set[int]],
    coverage_path: Path,
    cyclomatic_max: int,
    cognitive_max: int,
    crap_max: float,
    max_files_for_gate: int | None,
) -> list[str]:
    if not changed:
        return []
    if max_files_for_gate is not None and len(changed) > max_files_for_gate:
        print(
            f"Diff complexity gate skipped: {len(changed)} changed production files exceeds "
            f"maintenance threshold of {max_files_for_gate}."
        )
        return []

    coverage = parse_coverage_hits(coverage_path)
    coverage_methods = parse_coverage_methods(coverage_path)
    violations: list[str] = []
    for file_path, lines in sorted(changed.items()):
        current_path = REPO_ROOT / file_path
        if not current_path.exists():
            continue

        current_methods = changed_methods(
            parse_methods(file_path, current_path.read_text(encoding="utf-8", errors="ignore")),
            lines,
        )
        if not current_methods:
            continue

        base_by_signature: dict[str, list[MethodMetric]] = {}
        base_text = read_git_file(base, file_path)
        if base_text is not None:
            for method in parse_methods(file_path, base_text):
                base_by_signature.setdefault(method.signature_key, []).append(method)

        coverage_key = resolve_coverage_file(file_path, coverage)
        line_hits = coverage.get(coverage_key, {}) if coverage_key is not None else {}
        coverage_method_key = resolve_coverage_file(file_path, coverage_methods)
        reported_methods = coverage_methods.get(coverage_method_key or file_path, [])
        checked_reported_methods: set[tuple[str, int, int, str]] = set()

        for raw_method in current_methods:
            reported_method = matching_coverage_method(raw_method, reported_methods)
            if reported_method is not None:
                checked_reported_methods.add(coverage_method_id(reported_method))
            method = with_reported_complexity(add_coverage(raw_method, line_hits), reported_method)
            violations.extend(_method_violations(method, cyclomatic_max, cognitive_max, crap_max))
            previous_complexities = [
                candidate.complexity
                for candidate in base_by_signature.get(method.signature_key, [])
            ]
            if previous_complexities and max(previous_complexities) > cyclomatic_max:
                previous = max(previous_complexities)
                if method.complexity > previous:
                    location = f"{method.path}:{method.start_line}: {method.name}"
                    violations.append(
                        f"{location} increases already-high cyclomatic complexity "
                        f"from {previous} to {method.complexity}."
                    )
            previous_cognitive = [
                candidate.cognitive_complexity
                for candidate in base_by_signature.get(method.signature_key, [])
            ]
            if previous_cognitive and max(previous_cognitive) > cognitive_max:
                previous = max(previous_cognitive)
                if method.cognitive_complexity > previous:
                    location = f"{method.path}:{method.start_line}: {method.name}"
                    violations.append(
                        f"{location} increases already-high cognitive complexity "
                        f"from {previous} to {method.cognitive_complexity}."
                    )

        for method in changed_coverage_methods(reported_methods, lines):
            if coverage_method_id(method) not in checked_reported_methods:
                violations.extend(_method_violations(method, cyclomatic_max, cognitive_max, crap_max))

    return violations


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fail PRs that add or change risky production methods by cyclomatic "
            "complexity, cognitive complexity, or CRAP score."
        )
    )
    parser.add_argument("--base", required=True)
    parser.add_argument("--coverage", required=True)
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--cyclomatic-max", type=int, default=None)
    parser.add_argument("--cognitive-max", type=int, default=None)
    parser.add_argument("--crap-max", type=float, default=None)
    parser.add_argument("--max-files-for-gate", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    policy_values = load_diff_quality_config(Path(args.policy_path))
    cyclomatic_max = args.cyclomatic_max if args.cyclomatic_max is not None else policy_values[0]
    cognitive_max = args.cognitive_max if args.cognitive_max is not None else policy_values[1]
    crap_max = args.crap_max if args.crap_max is not None else policy_values[2]
    max_files = args.max_files_for_gate if args.max_files_for_gate is not None else policy_values[3]

    try:
        changed = parse_changed_lines(run_git_diff(args.base))
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as ex:
        detail = getattr(ex, "stderr", None) or str(ex)
        print(f"Unable to compute git diff against '{args.base}': {detail.strip()}", file=sys.stderr)
        return 1
    if not changed:
        print("No changed production source files detected; skipping diff complexity gate.")
        return 0

    try:
        violations = validate_diff_complexity(
            args.base, changed, Path(args.coverage), cyclomatic_max, cognitive_max, crap_max, max_files
        )
    except (FileNotFoundError, ValueError) as ex:
        print(str(ex), file=sys.stderr)
        return 1

    if violations:
        print("Diff complexity gate failed.", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1

    print(
        "Diff complexity gate passed "
        f"(cyclomatic <= {cyclomatic_max}, cognitive <= {cognitive_max}, CRAP <= {crap_max:.2f})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
