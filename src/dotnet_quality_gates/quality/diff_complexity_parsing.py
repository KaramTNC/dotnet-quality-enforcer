from __future__ import annotations

import bisect
import re
from pathlib import Path

from dotnet_quality_gates.languages import adapter_for_path
from dotnet_quality_gates.unit_test_conventions import find_matching_brace, mask_comments_and_strings

from .diff_complexity_metric import MethodMetric

CONTROL_KEYWORDS = {
    "if", "for", "foreach", "while", "switch", "catch", "using", "lock", "fixed", "async",
}
TYPE_KEYWORDS = {"class", "record", "struct", "interface"}
METHOD_NAME_PATTERN = re.compile(r"([A-Za-z_]\w*|operator\s*[^\s(]+)\s*$")
DECISION_PATTERN = re.compile(
    r"\b(?:if|for|foreach|while|case|catch|when)\b|&&|\|\||(?<!\?)\?(?!\?)(?=[^;\n{}]*:)"
)
CONTROL_FLOW_PATTERN = re.compile(r"\b(?:if|for|foreach|while|switch|catch)\b")
LOGICAL_OPERATOR_PATTERN = re.compile(r"&&|\|\|")
JUMP_PATTERN = re.compile(r"\b(?:break|continue|goto)\b")
TERNARY_PATTERN = re.compile(r"(?<!\?)\?(?!\?)(?=[^;\n{}]*:)")


def line_starts(text: str) -> list[int]:
    return [0, *[index + 1 for index, char in enumerate(text) if char == "\n"]]


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
        elif char in ")>]}" :
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
    return bool(re.search(r"\belse\s*$", masked_body[:if_index].rstrip()))


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
        if match.group(0) == "if" and is_else_if(masked_body, match.start()):
            depth = max(0, depth - 1)
        score += 1 + depth

        bounds = find_condition_bounds(masked_body, match.end())
        if bounds is not None:
            score += count_logical_operator_sequences(masked_body[bounds[0] : bounds[1]])

    for match in re.finditer(r"\belse\b", masked_body):
        if not masked_body[match.end() :].lstrip().startswith("if"):
            score += 1 + nesting_depth(ranges, match.start())

    for match in TERNARY_PATTERN.finditer(masked_body):
        score += 1 + nesting_depth(ranges, match.start())
    for match in JUMP_PATTERN.finditer(masked_body):
        score += 1 + nesting_depth(ranges, match.start())
    return score


def header_start(masked_text: str, opening_brace_index: int) -> int:
    return max(
        masked_text.rfind(";", 0, opening_brace_index),
        masked_text.rfind("}", 0, opening_brace_index),
        masked_text.rfind("{", 0, opening_brace_index),
    ) + 1


def is_control_block_header(header: str) -> bool:
    return any(re.match(rf"^{keyword}\s*(?:\(|\b)", header) for keyword in CONTROL_KEYWORDS)


def parse_block_methods(path: str, text: str) -> list[MethodMetric]:
    masked = mask_comments_and_strings(text)
    starts = line_starts(text)
    methods: list[MethodMetric] = []

    for match in re.finditer(r"\{", masked):
        open_index = match.start()
        start = header_start(masked, open_index)
        header = masked[start:open_index].strip()
        if "(" not in header or ")" not in header or is_control_block_header(header):
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
        prefix_before_name = prefix[: name_match.start()].strip()
        if (
            name in CONTROL_KEYWORDS
            or not prefix_before_name
            or prefix_before_name.endswith(".")
            or any(re.search(rf"\b{keyword}\b", prefix_before_name) for keyword in TYPE_KEYWORDS)
            or prefix_before_name.endswith("new")
        ):
            continue

        close_index = find_matching_brace(masked, open_index)
        if close_index is None:
            continue

        parameters = header[open_paren + 1 : close_paren]
        methods.append(
            MethodMetric(
                path=path,
                name=name,
                signature_key=signature_key(name, parameters),
                start_line=line_for_index(starts, start),
                end_line=line_for_index(starts, close_index),
                complexity=cyclomatic_complexity(masked[open_index : close_index + 1]),
                cognitive_complexity=cognitive_complexity(masked[open_index : close_index + 1]),
            )
        )

    return methods


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
        prefix_before_name = prefix[: name_match.start()].strip()
        if not prefix_before_name or prefix_before_name.endswith("."):
            continue
        if name in CONTROL_KEYWORDS or any(
            re.search(rf"\b{keyword}\b", prefix_before_name) for keyword in TYPE_KEYWORDS
        ):
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


def _parse_csharp_methods(path: str, text: str) -> list[MethodMetric]:
    return sorted(
        [*parse_block_methods(path, text), *parse_expression_bodied_methods(path, text)],
        key=lambda method: (method.start_line, method.end_line, method.name),
    )


def _parse_csharp_complexity_for_adapter(path: str, text: str) -> list[MethodMetric]:
    return _parse_csharp_methods(path, text)


def _generic_signature_key(name: str, parameters: int) -> str:
    normalized_name = re.sub(r"\s+", "", name)
    return f"{normalized_name}/{parameters}"


def parse_methods(path: str, text: str) -> list[MethodMetric]:
    if Path(path).suffix.lower() == ".cs":
        return _parse_csharp_methods(path, text)

    return [
        MethodMetric(
            path=metric.path,
            name=metric.name,
            signature_key=_generic_signature_key(metric.name, metric.parameters),
            start_line=metric.start_line,
            end_line=metric.end_line,
            complexity=metric.complexity,
            cognitive_complexity=metric.cognitive_complexity,
        )
        for metric in adapter_for_path(path).parse_complexity_metrics(path, text)
    ]


def changed_methods(methods: list[MethodMetric], changed_lines: set[int]) -> list[MethodMetric]:
    return [
        method
        for method in methods
        if any(method.start_line <= line <= method.end_line for line in changed_lines)
    ]
