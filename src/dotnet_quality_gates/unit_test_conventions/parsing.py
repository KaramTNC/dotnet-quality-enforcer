from __future__ import annotations

import re
from pathlib import Path

from dotnet_quality_gates.context import current_context

from .constants import (
    CLASS_DECLARATION_PATTERN,
    EXPOSED_METHOD_DECLARATION_PATTERN,
    METHOD_DECLARATION_PATTERN,
    TARGETABLE_EVENT_DECLARATION_PATTERN,
    TARGETABLE_PROPERTY_DECLARATION_PATTERN,
)
from .discovery import iter_csharp_files
from .masking import mask_comments_and_strings
from .models import TestClassInfo
from .roslyn import RoslynError, analyze_csharp_files
from .test_method_parsing import parse_regions_and_methods


def iter_cs_files(root: Path) -> list[Path]:
    return iter_csharp_files(root)


def repo_relative(path: Path, repo_root: Path | None = None) -> str:
    repo_root = repo_root or current_context().repo_root
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def is_excluded_source_file(path: Path) -> bool:
    normalized = path.as_posix()
    return (
        normalized.endswith((".Designer.cs", ".g.cs", ".g.i.cs", "AssemblyInfo.cs", "GlobalUsings.cs"))
        or "/Migrations/" in normalized
    )


def compute_brace_depths(masked_text: str) -> list[int]:
    depths = [0] * (len(masked_text) + 1)
    depth = 0
    for index, char in enumerate(masked_text):
        depths[index] = depth
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
    depths[-1] = depth
    return depths


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

        depth = max(0, depth + line.count("{") - line.count("}"))
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
                ) and "{" not in stripped and ";" not in stripped:
                    pending_signature = stripped

            property_match = TARGETABLE_PROPERTY_DECLARATION_PATTERN.match(line)
            if property_match:
                members.update({property_match.group(1), "Properties"})

            event_match = TARGETABLE_EVENT_DECLARATION_PATTERN.match(line)
            if event_match:
                members.update({event_match.group(1), "Events"})

            constructor_pattern = (
                r"^\s*(?:public|protected(?:\s+internal)?|internal(?:\s+protected)?|private)\s+"
                + re.escape(class_name)
                + r"\s*\("
            )
            if re.match(constructor_pattern, line):
                members.add("Constructor")

        depth = max(0, depth + line.count("{") - line.count("}"))
    return members


def parse_base_types(type_header: str) -> list[str]:
    inheritance_match = re.search(r":\s*(.+)$", type_header, flags=re.DOTALL)
    if inheritance_match is None:
        return []

    base_types: list[str] = []
    for raw_base_type in inheritance_match.group(1).split(","):
        base_type = re.sub(r"\s+", "", re.sub(r"<.*", "", raw_base_type.strip())).split(".")[-1]
        if re.fullmatch(r"[A-Za-z_]\w*", base_type):
            base_types.append(base_type)
    return base_types


def parse_test_classes(unit_test_root: Path) -> tuple[list[TestClassInfo], list[str]]:
    test_classes: list[TestClassInfo] = []
    errors: list[str] = []
    file_paths = iter_cs_files(unit_test_root)
    try:
        roslyn_analyses = analyze_csharp_files(file_paths)
    except RoslynError as ex:
        return test_classes, [f"Roslyn parser error: {ex}"]

    for file_path in file_paths:
        roslyn_analysis = roslyn_analyses.get(file_path.resolve())
        if roslyn_analysis is not None:
            test_classes.extend(roslyn_analysis.test_classes)
            errors.extend(
                f"{repo_relative(file_path)}:{diagnostic.line}: "
                f"Roslyn {diagnostic.diagnostic_id}: {diagnostic.message}"
                for diagnostic in roslyn_analysis.diagnostics
            )
            continue

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
            test_classes.append(
                TestClassInfo(
                    name=class_name,
                    path=file_path,
                    line=text.count("\n", 0, class_match.start()) + 1,
                    methods=parse_regions_and_methods(class_body, text.count("\n", 0, brace_index)),
                )
            )

        if file_test_class_count > 1:
            errors.append(
                f"{repo_relative(file_path)}: contains {file_test_class_count} test classes. Use one test class per file."
            )

    return test_classes, errors
