from __future__ import annotations

import re
from pathlib import Path

from .constants import (
    CLASS_DECLARATION_PATTERN,
    EXPOSED_METHOD_DECLARATION_PATTERN,
    EXPOSED_PROPERTY_DECLARATION_PATTERN,
    METHOD_DECLARATION_PATTERN,
    REPO_ROOT,
    SKIP_DIR_NAMES,
    SOURCE_TYPE_DECLARATION_PATTERN,
    TARGETABLE_EVENT_DECLARATION_PATTERN,
    TARGETABLE_PROPERTY_DECLARATION_PATTERN,
    TEST_ATTRIBUTE_PATTERN,
)
from .models import SourceClassInfo, TestClassInfo, TestMethodInfo


def iter_cs_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.cs"):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        files.append(path)
    return files


def repo_relative(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def is_excluded_source_file(path: Path) -> bool:
    normalized = path.as_posix()
    if normalized.endswith(".Designer.cs"):
        return True
    if normalized.endswith(".g.cs") or normalized.endswith(".g.i.cs"):
        return True
    if normalized.endswith("AssemblyInfo.cs") or normalized.endswith("GlobalUsings.cs"):
        return True
    if "/Migrations/" in normalized:
        return True
    return False


def compute_brace_depths(masked_text: str) -> list[int]:
    depths = [0] * (len(masked_text) + 1)
    depth = 0
    for index, char in enumerate(masked_text):
        depths[index] = depth
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
    depths[len(masked_text)] = depth
    return depths


def mask_comments_and_strings(text: str) -> str:
    chars = list(text)
    i = 0
    n = len(chars)
    state = "code"

    while i < n:
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < n else ""

        if state == "code":
            if c == "/" and nxt == "/":
                chars[i] = " "
                chars[i + 1] = " "
                i += 2
                state = "line_comment"
                continue
            if c == "/" and nxt == "*":
                chars[i] = " "
                chars[i + 1] = " "
                i += 2
                state = "block_comment"
                continue
            if c == "'" and nxt:
                chars[i] = " "
                i += 1
                state = "char"
                continue
            if c == "@":
                if nxt == '"':
                    chars[i] = " "
                    chars[i + 1] = " "
                    i += 2
                    state = "verbatim_string"
                    continue
                if nxt == "$" and i + 2 < n and chars[i + 2] == '"':
                    chars[i] = " "
                    chars[i + 1] = " "
                    chars[i + 2] = " "
                    i += 3
                    state = "verbatim_string"
                    continue
            if c == "$":
                if nxt == '"':
                    chars[i] = " "
                    chars[i + 1] = " "
                    i += 2
                    state = "string"
                    continue
                if nxt == "@" and i + 2 < n and chars[i + 2] == '"':
                    chars[i] = " "
                    chars[i + 1] = " "
                    chars[i + 2] = " "
                    i += 3
                    state = "verbatim_string"
                    continue
            if c == '"':
                chars[i] = " "
                i += 1
                state = "string"
                continue
            i += 1
            continue

        if state == "line_comment":
            if c != "\n":
                chars[i] = " "
            i += 1
            if c == "\n":
                state = "code"
            continue

        if state == "block_comment":
            chars[i] = " "
            if c == "*" and nxt == "/":
                chars[i + 1] = " "
                i += 2
                state = "code"
            else:
                i += 1
            continue

        if state == "string":
            if c != "\n":
                chars[i] = " "
            if c == "\\" and i + 1 < n:
                if chars[i + 1] != "\n":
                    chars[i + 1] = " "
                i += 2
                continue
            if c == '"':
                i += 1
                state = "code"
                continue
            i += 1
            continue

        if state == "verbatim_string":
            if c != "\n":
                chars[i] = " "
            if c == '"' and nxt == '"':
                if chars[i + 1] != "\n":
                    chars[i + 1] = " "
                i += 2
                continue
            if c == '"':
                i += 1
                state = "code"
                continue
            i += 1
            continue

        if state == "char":
            if c != "\n":
                chars[i] = " "
            if c == "\\" and i + 1 < n:
                if chars[i + 1] != "\n":
                    chars[i + 1] = " "
                i += 2
                continue
            if c == "'":
                i += 1
                state = "code"
                continue
            i += 1
            continue

    return "".join(chars)


def find_matching_brace(masked_text: str, opening_brace_index: int) -> int | None:
    depth = 0
    for index in range(opening_brace_index, len(masked_text)):
        char = masked_text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


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
    masked_body = mask_comments_and_strings(class_body)

    lines = class_body.splitlines()
    masked_lines = masked_body.splitlines()
    depth = 0
    for index, line in enumerate(lines, start=1):
        masked_line = masked_lines[index - 1] if index - 1 < len(masked_lines) else ""
        stripped = line.strip()

        if depth != 0:
            depth += masked_line.count("{")
            depth -= masked_line.count("}")
            if depth < 0:
                depth = 0
            continue

        if stripped.startswith("#region"):
            region_name = stripped[len("#region") :].strip().strip('"')
            region_stack.append(region_name)
            pending_attributes = []
            depth += masked_line.count("{")
            depth -= masked_line.count("}")
            continue

        if stripped.startswith("#endregion"):
            if region_stack:
                region_stack.pop()
            pending_attributes = []
            depth += masked_line.count("{")
            depth -= masked_line.count("}")
            continue

        if stripped.startswith("["):
            pending_attributes.append(stripped)
            depth += masked_line.count("{")
            depth -= masked_line.count("}")
            continue

        method_match = METHOD_DECLARATION_PATTERN.match(line)
        if method_match:
            method_name = method_match.group(1)
            is_test_method = any(TEST_ATTRIBUTE_PATTERN.search(attr) for attr in pending_attributes)
            method_under_test = parse_test_method_name(method_name)
            methods.append(
                TestMethodInfo(
                    name=method_name,
                    line=line_offset + index,
                    region=region_stack[-1] if region_stack else None,
                    is_test_method=is_test_method,
                    method_under_test_from_name=method_under_test,
                )
            )
            pending_attributes = []
            depth += masked_line.count("{")
            depth -= masked_line.count("}")
            continue

        if stripped:
            pending_attributes = []

        depth += masked_line.count("{")
        depth -= masked_line.count("}")
        if depth < 0:
            depth = 0

    return methods


def parse_exposed_methods(class_body: str) -> set[str]:
    methods: set[str] = set()
    pending_signature: str | None = None
    depth = 0
    for line in class_body.splitlines():
        if depth == 0:
            stripped = line.strip()
            if pending_signature is not None:
                signature = f"{pending_signature} {stripped}"
                method_match = EXPOSED_METHOD_DECLARATION_PATTERN.match(signature)
                if method_match and "=" not in signature[: method_match.start(1)]:
                    methods.add(method_match.group(1))
                    pending_signature = None
                elif "{" in stripped or ";" in stripped:
                    pending_signature = None
                else:
                    pending_signature = signature
            elif not re.search(r"\b(?:class|interface|struct|record)\b", line):
                method_match = EXPOSED_METHOD_DECLARATION_PATTERN.match(line)
                if method_match and "=" not in line[: method_match.start(1)]:
                    methods.add(method_match.group(1))
                elif re.match(r"^\s*(?:public|protected(?:\s+internal)?|internal(?:\s+protected)?)\b", line):
                    if "{" not in stripped and ";" not in stripped:
                        pending_signature = stripped

        depth += line.count("{")
        depth -= line.count("}")
        if depth < 0:
            depth = 0
    return methods


def parse_targetable_members(class_body: str, class_name: str) -> set[str]:
    members: set[str] = {"Constructor"}
    pending_signature: str | None = None
    depth = 0
    for line in class_body.splitlines():
        if depth == 0:
            stripped = line.strip()
            if pending_signature is not None:
                signature = f"{pending_signature} {stripped}"
                method_match = METHOD_DECLARATION_PATTERN.match(signature)
                if method_match and "=" not in signature[: method_match.start(1)]:
                    members.add(method_match.group(1))
                    pending_signature = None
                elif "{" in stripped or ";" in stripped:
                    pending_signature = None
                else:
                    pending_signature = signature
            elif not re.search(r"\b(?:class|interface|struct|record)\b", line):
                method_match = METHOD_DECLARATION_PATTERN.match(line)
                if method_match and "=" not in line[: method_match.start(1)]:
                    members.add(method_match.group(1))
                elif re.match(
                    r"^\s*(?:public|protected(?:\s+internal)?|internal(?:\s+protected)?|private)\b",
                    line,
                ):
                    if "{" not in stripped and ";" not in stripped:
                        pending_signature = stripped

            property_match = TARGETABLE_PROPERTY_DECLARATION_PATTERN.match(line)
            if property_match:
                members.add(property_match.group(1))
                members.add("Properties")

            event_match = TARGETABLE_EVENT_DECLARATION_PATTERN.match(line)
            if event_match:
                members.add(event_match.group(1))
                members.add("Events")

            constructor_pattern = (
                r"^\s*(?:public|protected(?:\s+internal)?|internal(?:\s+protected)?|private)\s+"
                + re.escape(class_name)
                + r"\s*\("
            )
            if re.match(constructor_pattern, line):
                members.add("Constructor")

        depth += line.count("{")
        depth -= line.count("}")
        if depth < 0:
            depth = 0
    return members


def parse_base_types(type_header: str) -> list[str]:
    inheritance_match = re.search(r":\s*(.+)$", type_header, flags=re.DOTALL)
    if inheritance_match is None:
        return []

    base_types: list[str] = []
    for raw_base_type in inheritance_match.group(1).split(","):
        base_type = raw_base_type.strip()
        if not base_type:
            continue
        base_type = re.sub(r"<.*", "", base_type)
        base_type = re.sub(r"\s+", "", base_type)
        base_type = base_type.split(".")[-1]
        if re.fullmatch(r"[A-Za-z_]\w*", base_type):
            base_types.append(base_type)

    return base_types


def parse_source_classes(src_root: Path) -> tuple[list[SourceClassInfo], list[str]]:
    source_classes: list[SourceClassInfo] = []
    errors: list[str] = []

    for file_path in iter_cs_files(src_root):
        if is_excluded_source_file(file_path):
            continue

        text = file_path.read_text(encoding="utf-8", errors="ignore")
        masked = mask_comments_and_strings(text)
        brace_depths = compute_brace_depths(masked)
        file_scoped_namespace = re.search(r"^\s*namespace\s+[A-Za-z0-9_.]+\s*;", masked, flags=re.MULTILINE) is not None
        max_top_level_depth = 0 if file_scoped_namespace else 1

        for class_match in SOURCE_TYPE_DECLARATION_PATTERN.finditer(masked):
            if brace_depths[class_match.start()] > max_top_level_depth:
                continue

            source_kind = class_match.group(1).split()[0]
            class_name = class_match.group(2)
            is_partial = bool(re.search(r"\bpartial\b", class_match.group(0)))
            brace_index = masked.find("{", class_match.end())
            type_header_end = brace_index if brace_index != -1 else masked.find(";", class_match.end())
            type_header = masked[class_match.end() : type_header_end] if type_header_end != -1 else ""
            base_types = parse_base_types(type_header) if source_kind == "class" else []
            if brace_index == -1:
                class_body = ""
            else:
                closing_brace_index = find_matching_brace(masked, brace_index)
                if closing_brace_index is None:
                    errors.append(f"{repo_relative(file_path)}: unable to find matching brace for class {class_name}")
                    continue
                class_body = text[brace_index + 1 : closing_brace_index]
            class_line = text.count("\n", 0, class_match.start()) + 1
            exposed_methods = parse_exposed_methods(class_body)
            targetable_members = parse_targetable_members(class_body, class_name)
            source_classes.append(
                SourceClassInfo(
                    name=class_name,
                    path=file_path,
                    line=class_line,
                    exposed_methods=exposed_methods,
                    is_partial=is_partial,
                    targetable_members=targetable_members,
                    requires_test_class=source_kind == "class",
                    base_types=base_types,
                )
            )

    return source_classes, errors


def parse_test_classes(unit_test_root: Path) -> tuple[list[TestClassInfo], list[str]]:
    test_classes: list[TestClassInfo] = []
    errors: list[str] = []

    for file_path in iter_cs_files(unit_test_root):
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        masked = mask_comments_and_strings(text)

        file_test_class_count = 0
        for class_match in CLASS_DECLARATION_PATTERN.finditer(masked):
            class_name = class_match.group(1)
            if not class_name.endswith("Tests"):
                continue

            file_test_class_count += 1
            brace_index = masked.find("{", class_match.end())
            if brace_index == -1:
                continue
            closing_brace_index = find_matching_brace(masked, brace_index)
            if closing_brace_index is None:
                errors.append(f"{repo_relative(file_path)}: unable to find matching brace for class {class_name}")
                continue

            class_body = text[brace_index + 1 : closing_brace_index]
            class_line = text.count("\n", 0, class_match.start()) + 1
            methods = parse_regions_and_methods(class_body, text.count("\n", 0, brace_index))
            test_classes.append(
                TestClassInfo(
                    name=class_name,
                    path=file_path,
                    line=class_line,
                    methods=methods,
                )
            )

        if file_test_class_count > 1:
            errors.append(
                f"{repo_relative(file_path)}: contains {file_test_class_count} test classes. Use one test class per file."
            )

    return test_classes, errors
