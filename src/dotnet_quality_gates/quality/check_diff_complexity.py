from __future__ import annotations

import argparse
import bisect
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from dotnet_quality_gates.context import current_context
from dotnet_quality_gates.coverage.check_diff_coverage import (  # noqa: E402
    parse_changed_lines,
    parse_coverage,
    parse_safe_xml,
    resolve_coverage_file,
    run_git_diff,
)
from dotnet_quality_gates.quality.common import load_policy_object, policy_section  # noqa: E402
from dotnet_quality_gates.subprocess_utils import run_command
from dotnet_quality_gates.unit_test_conventions import find_matching_brace, mask_comments_and_strings  # noqa: E402

REPO_ROOT = current_context().repo_root
DEFAULT_POLICY_PATH = current_context().policy_path

DEFAULT_CYCLOMATIC_MAX = 10
DEFAULT_COGNITIVE_MAX = 10
DEFAULT_CRAP_MAX = 30.0
DEFAULT_MAX_FILES_FOR_GATE: int | None = None

CONTROL_KEYWORDS = {
    "if",
    "for",
    "foreach",
    "while",
    "switch",
    "catch",
    "using",
    "lock",
    "fixed",
    "async",
}
TYPE_KEYWORDS = {"class", "record", "struct", "interface"}
METHOD_NAME_PATTERN = re.compile(r"([A-Za-z_]\w*|operator\s*[^\s(]+)\s*$")
DECISION_PATTERN = re.compile(
    r"\b(?:if|for|foreach|while|case|catch|when)\b"
    r"|&&"
    r"|\|\|"
    r"|(?<!\?)\?(?!\?)(?=[^;\n{}]*:)"
)
CONTROL_FLOW_PATTERN = re.compile(r"\b(?:if|for|foreach|while|switch|catch)\b")
LOGICAL_OPERATOR_PATTERN = re.compile(r"&&|\|\|")
JUMP_PATTERN = re.compile(r"\b(?:break|continue|goto)\b")
TERNARY_PATTERN = re.compile(r"(?<!\?)\?(?!\?)(?=[^;\n{}]*:)")


@dataclass(frozen=True)
class MethodMetric:
    path: str
    name: str
    signature_key: str
    start_line: int
    end_line: int
    complexity: int
    cognitive_complexity: int = 0
    coverable_lines: int = 0
    covered_lines: int = 0
    coverage_available: bool = False

    @property
    def coverage_ratio(self) -> float | None:
        if not self.coverage_available:
            return None
        return 1.0 if self.coverable_lines == 0 else self.covered_lines / self.coverable_lines

    @property
    def crap_score(self) -> float | None:
        coverage_ratio = self.coverage_ratio
        if coverage_ratio is None:
            return None
        uncovered = 1.0 - coverage_ratio
        return (self.complexity**2) * (uncovered**3) + self.complexity


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


def line_starts(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def line_for_index(starts: list[int], index: int) -> int:
    return bisect.bisect_right(starts, index)


def count_parameters(parameters: str) -> int:
    parameters = parameters.strip()
    if not parameters:
        return 0

    depth = 0
    count = 1
    for char in parameters:
        if char in "(<[{":
            depth += 1
        elif char in ")>]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            count += 1
    return count


def signature_key(name: str, parameters: str) -> str:
    normalized_name = re.sub(r"\s+", "", name)
    return f"{normalized_name}/{count_parameters(parameters)}"


def cyclomatic_complexity(masked_body: str) -> int:
    return 1 + sum(1 for _ in DECISION_PATTERN.finditer(masked_body))


def count_logical_operator_sequences(condition: str) -> int:
    operators = [match.group(0) for match in LOGICAL_OPERATOR_PATTERN.finditer(condition)]
    if not operators:
        return 0

    sequences = 1
    for previous, current in zip(operators, operators[1:]):
        if current != previous:
            sequences += 1
    return sequences


def find_condition_bounds(masked_body: str, keyword_end: int) -> tuple[int, int] | None:
    open_paren = masked_body.find("(", keyword_end)
    if open_paren < 0:
        return None

    depth = 0
    for index in range(open_paren, len(masked_body)):
        char = masked_body[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return open_paren + 1, index
    return None


def find_block_end(masked_body: str, keyword_end: int) -> int | None:
    bounds = find_condition_bounds(masked_body, keyword_end)
    search_start = bounds[1] + 1 if bounds is not None else keyword_end
    search_end = masked_body.find(";", search_start)
    open_brace = masked_body.find("{", search_start)
    if open_brace < 0:
        return search_end
    if search_end >= 0 and search_end < open_brace:
        return search_end
    return find_matching_brace(masked_body, open_brace)


def is_else_if(masked_body: str, if_index: int) -> bool:
    prefix = masked_body[:if_index].rstrip()
    return bool(re.search(r"\belse\s*$", prefix))


def control_flow_ranges(masked_body: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for match in CONTROL_FLOW_PATTERN.finditer(masked_body):
        end = find_block_end(masked_body, match.end())
        if end is not None:
            ranges.append((match.start(), end))
    return ranges


def nesting_depth(ranges: list[tuple[int, int]], position: int) -> int:
    return sum(1 for start, end in ranges if start < position < end)


def cognitive_complexity(masked_body: str) -> int:
    ranges = control_flow_ranges(masked_body)
    score = 0

    for match in CONTROL_FLOW_PATTERN.finditer(masked_body):
        depth = nesting_depth(ranges, match.start())
        keyword = match.group(0)
        if keyword == "if" and is_else_if(masked_body, match.start()):
            depth = max(0, depth - 1)

        score += 1 + depth

        bounds = find_condition_bounds(masked_body, match.end())
        if bounds is not None:
            score += count_logical_operator_sequences(masked_body[bounds[0] : bounds[1]])

    for match in re.finditer(r"\belse\b", masked_body):
        after_else = masked_body[match.end() :].lstrip()
        if after_else.startswith("if"):
            continue
        score += 1 + nesting_depth(ranges, match.start())

    for match in TERNARY_PATTERN.finditer(masked_body):
        score += 1 + nesting_depth(ranges, match.start())

    for match in JUMP_PATTERN.finditer(masked_body):
        score += 1 + nesting_depth(ranges, match.start())

    return score


def header_start(masked_text: str, opening_brace_index: int) -> int:
    candidates = [
        masked_text.rfind(";", 0, opening_brace_index),
        masked_text.rfind("}", 0, opening_brace_index),
        masked_text.rfind("{", 0, opening_brace_index),
    ]
    return max(candidates) + 1


def parse_block_methods(path: str, text: str) -> list[MethodMetric]:
    masked = mask_comments_and_strings(text)
    starts = line_starts(text)
    methods: list[MethodMetric] = []

    for match in re.finditer(r"\{", masked):
        open_index = match.start()
        start = header_start(masked, open_index)
        header = masked[start:open_index].strip()

        if "(" not in header or ")" not in header:
            continue
        if is_control_block_header(header):
            continue

        close_paren = header.rfind(")")
        open_paren = header.rfind("(", 0, close_paren)
        if open_paren < 0:
            continue

        prefix = header[:open_paren].strip()
        name_match = METHOD_NAME_PATTERN.search(prefix)
        if name_match is None:
            continue

        name = name_match.group(1)
        if name in CONTROL_KEYWORDS:
            continue

        prefix_before_name = prefix[: name_match.start()].strip()
        if not prefix_before_name or prefix_before_name.endswith("."):
            continue
        if any(re.search(rf"\b{keyword}\b", prefix_before_name) for keyword in TYPE_KEYWORDS):
            continue
        if prefix_before_name.endswith("new"):
            continue

        close_index = find_matching_brace(masked, open_index)
        if close_index is None:
            continue

        parameters = header[open_paren + 1 : close_paren]
        body = masked[open_index : close_index + 1]
        methods.append(
            MethodMetric(
                path=path,
                name=name,
                signature_key=signature_key(name, parameters),
                start_line=line_for_index(starts, start),
                end_line=line_for_index(starts, close_index),
                complexity=cyclomatic_complexity(body),
                cognitive_complexity=cognitive_complexity(body),
            )
        )

    return methods


def is_control_block_header(header: str) -> bool:
    return any(
        re.match(rf"^{keyword}\s*(?:\(|\b)", header)
        for keyword in CONTROL_KEYWORDS
    )


def parse_expression_bodied_methods(path: str, text: str) -> list[MethodMetric]:
    masked_lines = mask_comments_and_strings(text).splitlines()
    methods: list[MethodMetric] = []

    for line_number, line in enumerate(masked_lines, start=1):
        if "=>" not in line or ";" not in line or "(" not in line:
            continue

        header, body = line.split("=>", 1)
        close_paren = header.rfind(")")
        open_paren = header.rfind("(", 0, close_paren)
        if open_paren < 0:
            continue

        prefix = header[:open_paren].strip()
        name_match = METHOD_NAME_PATTERN.search(prefix)
        if name_match is None:
            continue

        name = name_match.group(1)
        if name in CONTROL_KEYWORDS:
            continue

        prefix_before_name = prefix[: name_match.start()].strip()
        if not prefix_before_name or prefix_before_name.endswith("."):
            continue
        if any(re.search(rf"\b{keyword}\b", prefix_before_name) for keyword in TYPE_KEYWORDS):
            continue
        if prefix_before_name.endswith("new"):
            continue

        parameters = header[open_paren + 1 : close_paren]
        methods.append(
            MethodMetric(
                path=path,
                name=name,
                signature_key=signature_key(name, parameters),
                start_line=line_number,
                end_line=line_number,
                complexity=cyclomatic_complexity(body),
                cognitive_complexity=cognitive_complexity(body),
            )
        )

    return methods


def parse_methods(path: str, text: str) -> list[MethodMetric]:
    methods = parse_block_methods(path, text)
    methods.extend(parse_expression_bodied_methods(path, text))
    return sorted(methods, key=lambda method: (method.start_line, method.end_line, method.name))


def changed_methods(methods: list[MethodMetric], changed_lines: set[int]) -> list[MethodMetric]:
    selected: list[MethodMetric] = []
    for method in methods:
        if any(method.start_line <= line <= method.end_line for line in changed_lines):
            selected.append(method)
    return selected


def read_git_file(base: str, path: str) -> str | None:
    result = run_command(
        ["git", "show", f"{base}:{path}"],
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def add_coverage(
    method: MethodMetric,
    line_hits: dict[int, int],
) -> MethodMetric:
    coverable = 0
    covered = 0
    coverage_available = False
    for line_number, hits in line_hits.items():
        if method.start_line <= line_number <= method.end_line:
            coverage_available = True
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
        coverage_available=coverage_available,
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
        root = parse_safe_xml(coverage_path)
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
                method_line = method_node.get("line")
                if not method_line:
                    continue
                line_numbers = [int(method_line)]

            complexity = int(float(method_node.get("complexity", "0")))
            covered = sum(1 for line_node in line_nodes if int(line_node.get("hits", "0")) > 0)
            method = MethodMetric(
                path=file_path,
                name=method_node.get("name", "<unknown>"),
                signature_key=f"{method_node.get('name', '<unknown>')}{method_node.get('signature', '')}",
                start_line=min(line_numbers),
                end_line=max(line_numbers),
                complexity=complexity,
                cognitive_complexity=0,
                coverable_lines=len(line_nodes),
                covered_lines=covered,
                coverage_available=True,
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
        coverage_available=coverage_method.coverage_available,
    )


def crap_violation(method: MethodMetric, location: str, crap_max: float) -> str | None:
    crap_score = method.crap_score
    if crap_score is None:
        return f"{location} has no method coverage data; CRAP score cannot be evaluated."
    if crap_score > crap_max:
        return (
            f"{location} has CRAP score {crap_score:.2f}; maximum allowed is "
            f"{crap_max:.2f}. Complexity {method.complexity}, method coverage "
            f"{method.covered_lines}/{method.coverable_lines}."
        )
    return None


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

        current_text = current_path.read_text(encoding="utf-8", errors="ignore")
        current_methods = changed_methods(parse_methods(file_path, current_text), lines)
        if not current_methods:
            continue

        base_text = read_git_file(base, file_path)
        base_by_signature: dict[str, list[MethodMetric]] = {}
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
            location = f"{method.path}:{method.start_line}: {method.name}"

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

            base_complexities = [
                candidate.complexity
                for candidate in base_by_signature.get(method.signature_key, [])
            ]
            if base_complexities:
                previous = max(base_complexities)
                if previous > cyclomatic_max and method.complexity > previous:
                    violations.append(
                        f"{location} increases already-high cyclomatic complexity "
                        f"from {previous} to {method.complexity}."
                    )

            base_cognitive_complexities = [
                candidate.cognitive_complexity
                for candidate in base_by_signature.get(method.signature_key, [])
            ]
            if base_cognitive_complexities:
                previous = max(base_cognitive_complexities)
                if previous > cognitive_max and method.cognitive_complexity > previous:
                    violations.append(
                        f"{location} increases already-high cognitive complexity "
                        f"from {previous} to {method.cognitive_complexity}."
                    )

            violation = crap_violation(method, location, crap_max)
            if violation is not None:
                violations.append(violation)

        for method in changed_coverage_methods(reported_methods, lines):
            if coverage_method_id(method) in checked_reported_methods:
                continue

            location = f"{method.path}:{method.start_line}: {method.name}"
            if method.complexity > cyclomatic_max:
                violations.append(
                    f"{location} has cyclomatic complexity {method.complexity}; "
                    f"maximum allowed for changed methods is {cyclomatic_max}."
                )

            violation = crap_violation(method, location, crap_max)
            if violation is not None:
                violations.append(violation)

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail PRs that add or change risky production C# methods by cyclomatic "
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
    args = parser.parse_args()

    (
        policy_cyclomatic_max,
        policy_cognitive_max,
        policy_crap_max,
        policy_max_files,
    ) = load_diff_quality_config(Path(args.policy_path))
    cyclomatic_max = args.cyclomatic_max if args.cyclomatic_max is not None else policy_cyclomatic_max
    cognitive_max = args.cognitive_max if args.cognitive_max is not None else policy_cognitive_max
    crap_max = args.crap_max if args.crap_max is not None else policy_crap_max
    max_files = args.max_files_for_gate if args.max_files_for_gate is not None else policy_max_files

    try:
        changed = parse_changed_lines(run_git_diff(args.base))
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as ex:
        detail = getattr(ex, "stderr", None) or str(ex)
        print(f"Unable to compute git diff against '{args.base}': {detail.strip()}", file=sys.stderr)
        return 1
    if not changed:
        print("No changed production .cs files detected; skipping diff complexity gate.")
        return 0

    try:
        violations = validate_diff_complexity(
            args.base,
            changed,
            Path(args.coverage),
            cyclomatic_max,
            cognitive_max,
            crap_max,
            max_files,
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
