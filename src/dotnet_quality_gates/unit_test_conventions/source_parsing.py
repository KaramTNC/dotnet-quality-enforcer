from __future__ import annotations

import re
from pathlib import Path

from .constants import SOURCE_TYPE_DECLARATION_PATTERN
from .masking import mask_comments_and_strings
from .models import SourceClassInfo
from .parsing import (
    compute_brace_depths,
    find_matching_brace,
    is_excluded_source_file,
    iter_cs_files,
    parse_base_types,
    parse_exposed_methods,
    parse_targetable_members,
    repo_relative,
)
from .roslyn import RoslynError, analyze_csharp_files


def parse_source_classes(src_root: Path) -> tuple[list[SourceClassInfo], list[str]]:
    source_classes: list[SourceClassInfo] = []
    errors: list[str] = []
    file_paths = [file_path for file_path in iter_cs_files(src_root) if not is_excluded_source_file(file_path)]
    try:
        roslyn_analyses = analyze_csharp_files(file_paths)
    except RoslynError as ex:
        return source_classes, [f"Roslyn parser error: {ex}"]

    for file_path in file_paths:
        roslyn_analysis = roslyn_analyses.get(file_path.resolve())
        if roslyn_analysis is not None:
            source_classes.extend(roslyn_analysis.source_classes)
            errors.extend(
                f"{repo_relative(file_path)}:{diagnostic.line}: "
                f"Roslyn {diagnostic.diagnostic_id}: {diagnostic.message}"
                for diagnostic in roslyn_analysis.diagnostics
            )
            continue
        _parse_source_file(file_path, source_classes, errors)

    return source_classes, errors


def _parse_source_file(
    file_path: Path,
    source_classes: list[SourceClassInfo],
    errors: list[str],
) -> None:
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
        source_classes.append(
            SourceClassInfo(
                name=class_name,
                path=file_path,
                line=class_line,
                exposed_methods=parse_exposed_methods(class_body),
                is_partial=is_partial,
                targetable_members=parse_targetable_members(class_body, class_name),
                requires_test_class=source_kind == "class",
                base_types=base_types,
            )
        )
