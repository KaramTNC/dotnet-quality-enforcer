from __future__ import annotations

import re

from .constants import METHOD_DECLARATION_PATTERN, TEST_ATTRIBUTE_PATTERN
from .masking import mask_comments_and_strings
from .models import TestMethodInfo


def parse_test_method_name(method_name: str) -> str | None:
    parts = method_name.split("_")
    if len(parts) < 2:
        return None
    method_under_test = parts[0]
    descriptive_parts = parts[1:]
    if not method_under_test or any(not part for part in descriptive_parts):
        return None
    if not re.fullmatch(r"[A-Za-z_]\w*", method_under_test):
        return None
    if not all(re.fullmatch(r"[A-Za-z0-9]+", part) for part in descriptive_parts):
        return None
    return method_under_test


def normalize_region_name(region_name: str | None) -> str | None:
    if region_name is None:
        return None
    return re.sub(r"\s+", " ", region_name.strip().strip('"'))


def parse_regions_and_methods(class_body: str, line_offset: int) -> list[TestMethodInfo]:
    methods: list[TestMethodInfo] = []
    region_stack: list[str] = []
    pending_attributes: list[str] = []
    masked_lines = mask_comments_and_strings(class_body).splitlines()
    lines = class_body.splitlines()
    depth = 0

    for index, line in enumerate(lines, start=1):
        masked_line = masked_lines[index - 1] if index - 1 < len(masked_lines) else ""
        stripped = line.strip()
        depth, skip_nested = _update_nested_depth(depth, masked_line)
        if skip_nested:
            continue

        if stripped.startswith("#region"):
            region_stack.append(stripped[len("#region") :].strip().strip('"'))
            pending_attributes = []
            depth = _adjust_depth(depth, masked_line)
            continue
        if stripped.startswith("#endregion"):
            if region_stack:
                region_stack.pop()
            pending_attributes = []
            depth = _adjust_depth(depth, masked_line)
            continue
        if stripped.startswith("["):
            pending_attributes.append(stripped)
            depth = _adjust_depth(depth, masked_line)
            continue

        method_match = METHOD_DECLARATION_PATTERN.match(line)
        if method_match:
            method_name = method_match.group(1)
            methods.append(
                TestMethodInfo(
                    name=method_name,
                    line=line_offset + index,
                    region=region_stack[-1] if region_stack else None,
                    is_test_method=any(TEST_ATTRIBUTE_PATTERN.search(attr) for attr in pending_attributes),
                    method_under_test_from_name=parse_test_method_name(method_name),
                )
            )
            pending_attributes = []
            depth = _adjust_depth(depth, masked_line)
            continue

        if stripped:
            pending_attributes = []
        depth = _adjust_depth(depth, masked_line)

    return methods


def _update_nested_depth(depth: int, masked_line: str) -> tuple[int, bool]:
    if depth == 0:
        return depth, False
    return max(0, depth + masked_line.count("{") - masked_line.count("}")), True


def _adjust_depth(depth: int, masked_line: str) -> int:
    return max(0, depth + masked_line.count("{") - masked_line.count("}"))
