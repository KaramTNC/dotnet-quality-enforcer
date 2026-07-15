from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from dotnet_quality_gates.context import current_context
from dotnet_quality_gates.quality.common import (  # noqa: E402
    is_repo_excluded,
    load_prefixed_baseline_violations,
    load_quality_section_config,
)
from dotnet_quality_gates.unit_test_conventions import (  # noqa: E402
    REPO_ROOT,
    compute_brace_depths,
    iter_cs_files,
    mask_comments_and_strings,
)
from dotnet_quality_gates.unit_test_conventions.roslyn import RoslynError, analyze_csharp_file  # noqa: E402

TYPE_DECLARATION_PATTERN = re.compile(
    r"\b(?:public|protected|internal|private|file)?\s*"
    r"(?:(?:abstract|sealed|static|partial|readonly|unsafe)\s+)*"
    r"(?:class|interface)\s+([A-Za-z_]\w*)\b"
)


DEFAULT_POLICY_PATH = current_context().policy_path
DEFAULT_INCLUDE_ROOTS = ["src"]
DEFAULT_EXCLUDE_GLOBS = [
    "**/*.Designer.cs",
    "**/*.g.cs",
    "**/*.g.i.cs",
    "**/GlobalUsings.cs",
    "**/AssemblyInfo.cs",
]


def load_source_type_layout_config(policy_path: Path) -> tuple[list[str], list[str]]:
    return load_quality_section_config(
        policy_path,
        "source_type_layout",
        DEFAULT_INCLUDE_ROOTS,
        DEFAULT_EXCLUDE_GLOBS,
        "source type layout",
    )


def is_excluded(path: Path, exclude_globs: list[str]) -> bool:
    return is_repo_excluded(path, exclude_globs, REPO_ROOT)


def parse_top_level_type_declarations(path: Path) -> list[tuple[str, int]]:
    roslyn_analysis = analyze_csharp_file(path)
    if roslyn_analysis is not None:
        return [
            (name, line)
            for name, line, kind in roslyn_analysis.type_declarations
            if kind in {"class", "interface"}
        ]

    text = path.read_text(encoding="utf-8", errors="ignore")
    masked = mask_comments_and_strings(text)
    brace_depths = compute_brace_depths(masked)
    file_scoped_namespace = any(
        line.strip().startswith("namespace ") and line.strip().endswith(";")
        for line in masked.splitlines()
    )
    max_top_level_depth = 0 if file_scoped_namespace else 1

    declarations: list[tuple[str, int]] = []
    for match in TYPE_DECLARATION_PATTERN.finditer(masked):
        if brace_depths[match.start()] > max_top_level_depth:
            continue
        type_name = match.group(1)
        line_number = text.count("\n", 0, match.start()) + 1
        declarations.append((type_name, line_number))

    return declarations


def validate_source_type_layout(
    include_roots: list[Path],
    exclude_globs: list[str],
) -> list[str]:
    violations: list[str] = []

    for include_root in include_roots:
        for file_path in iter_cs_files(include_root):
            if is_excluded(file_path, exclude_globs):
                continue

            try:
                declarations = parse_top_level_type_declarations(file_path)
            except RoslynError as ex:
                violations.append(f"{file_path.relative_to(REPO_ROOT)}: {ex}")
                continue
            if len(declarations) <= 1:
                continue

            first_line = declarations[0][1]
            declared = ", ".join(name for name, _ in declarations)
            violations.append(
                f"{file_path.relative_to(REPO_ROOT)}:{first_line}: "
                f"Multiple top-level class/interface declarations found: {declared}. "
                "Use one top-level class/interface per file."
            )

    return violations


def load_baseline_violations(path: Path) -> set[str]:
    return load_prefixed_baseline_violations(path, canonicalize_violation_key)


def canonicalize_violation_key(value: str) -> str:
    return value.replace("\\", "/")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate source layout conventions for one top-level class/interface per file."
    )
    parser.add_argument(
        "--policy-path",
        default=str(DEFAULT_POLICY_PATH),
        help="Path to code quality policy JSON.",
    )
    parser.add_argument(
        "--baseline-path",
        default=str(REPO_ROOT / ".quality" / "baselines" / "source_type_layout_baseline.txt"),
        help="Path to a baseline file with one known violation per line prefixed by '- '.",
    )
    parser.add_argument(
        "--max-violations",
        type=int,
        default=250,
        help="Maximum number of violations to print before truncating output.",
    )
    args = parser.parse_args()

    include_root_texts, exclude_globs = load_source_type_layout_config(Path(args.policy_path))
    include_roots: list[Path] = []
    for include_root_text in include_root_texts:
        include_root = (REPO_ROOT / include_root_text).resolve()
        if include_root.exists():
            include_roots.append(include_root)
        else:
            print(f"Warning: include root not found and skipped: {include_root_text}", file=sys.stderr)

    if not include_roots:
        print("Source type layout check failed: no valid include roots found.", file=sys.stderr)
        return 1

    violations = validate_source_type_layout(include_roots, exclude_globs)
    baseline_violations = load_baseline_violations(Path(args.baseline_path))
    if baseline_violations:
        violations = [
            violation
            for violation in violations
            if canonicalize_violation_key(violation) not in baseline_violations
        ]

    if violations:
        print("Source type layout check failed.", file=sys.stderr)
        displayed = violations[: args.max_violations]
        for violation in displayed:
            print(f" - {violation}", file=sys.stderr)
        if len(violations) > len(displayed):
            remaining = len(violations) - len(displayed)
            print(f" - ... {remaining} additional violations omitted", file=sys.stderr)
        return 1

    print("Source type layout check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
