from __future__ import annotations

import re
from bisect import bisect_right

from dotnet_quality_gates.languages.models import CodeSizeThresholds
from dotnet_quality_gates.unit_test_conventions import find_matching_brace, mask_comments_and_strings

from .code_size_metric import CodeSizeMetric

CONTROL_KEYWORDS = {
    "if", "for", "foreach", "while", "switch", "catch", "using", "lock", "fixed", "async",
}
TYPE_KEYWORDS = {"class", "record", "struct", "interface"}
METHOD_NAME_PATTERN = re.compile(r"([A-Za-z_]\w*|operator\s*[^\s(]+)\s*$")
TYPE_DECLARATION_PATTERN = re.compile(
    r"\b(?:public|protected|internal|private|file)?\s*"
    r"(?:(?:abstract|sealed|static|partial|readonly|unsafe)\s+)*"
    r"(?:(class|struct|interface)|record(?:\s+(?:class|struct))?)\s+([A-Za-z_]\w*)\b"
)


def line_starts(text: str) -> list[int]:
    return [0, *[index + 1 for index, char in enumerate(text) if char == "\n"]]


def line_for_index(starts: list[int], index: int) -> int:
    return bisect_right(starts, index)


def count_file_lines(text: str) -> int:
    """Count physical file lines while excluding XML documentation comments."""
    line_count = 0
    in_documentation_block = False

    for line in text.splitlines():
        stripped = line.lstrip()
        if in_documentation_block:
            closing_index = stripped.find("*/")
            if closing_index < 0:
                continue
            in_documentation_block = False
            if stripped[closing_index + 2 :].strip():
                line_count += 1
            continue

        if stripped.startswith("///"):
            continue
        if stripped.startswith("/**"):
            closing_index = stripped.find("*/", 3)
            if closing_index < 0:
                in_documentation_block = True
                continue
            if stripped[closing_index + 2 :].strip():
                line_count += 1
            continue

        line_count += 1

    return line_count


def header_start(masked_text: str, opening_brace_index: int) -> int:
    candidates = [
        masked_text.rfind(";", 0, opening_brace_index),
        masked_text.rfind("}", 0, opening_brace_index),
        masked_text.rfind("{", 0, opening_brace_index),
    ]
    return max(candidates) + 1


def is_control_block_header(header: str) -> bool:
    return any(re.match(rf"^{keyword}\s*(?:\(|\b)", header) for keyword in CONTROL_KEYWORDS)


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


def method_display_name(name: str, parameters: str) -> str:
    normalized_name = re.sub(r"\s+", "", name)
    return f"{normalized_name}/{count_parameters(parameters)}"


def strip_leading_attributes(header: str) -> str:
    return re.sub(r"^\s*(?:\[[^\]]*\]\s*)+", "", header, flags=re.S)


def parse_block_methods(path: str, text: str, config: CodeSizeThresholds) -> list[CodeSizeMetric]:
    masked = mask_comments_and_strings(text)
    starts = line_starts(text)
    methods: list[CodeSizeMetric] = []

    for match in re.finditer(r"\{", masked):
        open_index = match.start()
        start = header_start(masked, open_index)
        raw_header = masked[start:open_index]
        header = strip_leading_attributes(raw_header).strip()
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
        header_index = start + len(raw_header) - len(raw_header.lstrip())
        start_line = line_for_index(starts, header_index)
        end_line = line_for_index(starts, close_index)
        methods.append(
            CodeSizeMetric(
                kind="method",
                path=path,
                name=method_display_name(name, parameters),
                start_line=start_line,
                end_line=end_line,
                line_count=end_line - start_line + 1,
                warn_limit=config.method_warn_lines,
                fail_limit=config.method_max_lines,
            )
        )

    return methods


def parse_expression_bodied_methods(path: str, text: str, config: CodeSizeThresholds) -> list[CodeSizeMetric]:
    masked_lines = mask_comments_and_strings(text).splitlines()
    methods: list[CodeSizeMetric] = []

    for line_number, line in enumerate(masked_lines, start=1):
        if "=>" not in line or ";" not in line or "(" not in line:
            continue

        header, _body = line.split("=>", 1)
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

        parameters = header[open_paren + 1 : close_paren]
        methods.append(
            CodeSizeMetric(
                kind="method",
                path=path,
                name=method_display_name(name, parameters),
                start_line=line_number,
                end_line=line_number,
                line_count=1,
                warn_limit=config.method_warn_lines,
                fail_limit=config.method_max_lines,
            )
        )

    return methods


def parse_methods(path: str, text: str, config: CodeSizeThresholds) -> list[CodeSizeMetric]:
    methods = parse_block_methods(path, text, config)
    methods.extend(parse_expression_bodied_methods(path, text, config))
    return sorted(methods, key=lambda method: (method.start_line, method.end_line, method.name))


def namespace_for_index(masked_text: str, index: int) -> str:
    namespace = ""
    for match in re.finditer(r"\bnamespace\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*(?:;|\{)", masked_text):
        if match.start() > index:
            break
        namespace = match.group(1)
    return namespace


def parse_types(path: str, text: str, config: CodeSizeThresholds) -> list[CodeSizeMetric]:
    masked = mask_comments_and_strings(text)
    starts = line_starts(text)
    types: list[CodeSizeMetric] = []

    for match in TYPE_DECLARATION_PATTERN.finditer(masked):
        brace_index = masked.find("{", match.end())
        if brace_index < 0:
            continue

        semicolon_index = masked.find(";", match.end(), brace_index)
        if semicolon_index >= 0:
            continue

        close_index = find_matching_brace(masked, brace_index)
        if close_index is None:
            continue

        start_line = line_for_index(starts, match.start())
        end_line = line_for_index(starts, close_index)
        kind = match.group(1) or "record"
        name = match.group(2)
        namespace = namespace_for_index(masked, match.start())
        type_key = f"{namespace}:{kind}:{name}"
        types.append(
            CodeSizeMetric(
                kind="type",
                path=path,
                name=f"{kind} {name}",
                start_line=start_line,
                end_line=end_line,
                line_count=end_line - start_line + 1,
                warn_limit=config.type_warn_lines,
                fail_limit=config.type_max_lines,
                is_partial_type=bool(re.search(r"\bpartial\b", match.group(0))),
                type_key=type_key,
            )
        )

    return sorted(types, key=lambda type_metric: (type_metric.start_line, type_metric.end_line))
