from __future__ import annotations

import argparse
import sys
from pathlib import Path



from dotnet_quality_gates.unit_test_conventions import (  # noqa: E402
    DEFAULT_POLICY_PATH,
    DEFAULT_SRC_ROOT,
    DEFAULT_UNIT_TEST_ROOT,
    REPO_ROOT,
    SourceClassInfo,
    TestClassInfo,
    TestMethodInfo,
    build_include_to_test_root_map,
    combine_partial_source_classes,
    compute_brace_depths,
    find_matching_brace,
    is_excluded_source_file,
    iter_cs_files,
    load_default_source_include_roots,
    mask_comments_and_strings,
    normalize_region_name,
    parse_base_types,
    parse_exposed_methods,
    parse_regions_and_methods,
    parse_source_classes,
    parse_targetable_members,
    parse_test_classes,
    parse_test_method_name,
    resolve_expected_test_directory,
    validate_conventions,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate C# test conventions and source-to-test mapping."
    )
    parser.add_argument("--src-root", default=str(DEFAULT_SRC_ROOT))
    parser.add_argument("--unit-test-root", default=str(DEFAULT_UNIT_TEST_ROOT))
    parser.add_argument(
        "--max-violations",
        type=int,
        default=250,
        help="Maximum number of violations to print before truncating output.",
    )
    parser.add_argument(
        "--source-include-roots",
        nargs="+",
        default=None,
        help="Source directories (repo-relative) to include in mapping.",
    )
    parser.add_argument(
        "--policy-path",
        default=str(DEFAULT_POLICY_PATH),
        help="Path to code quality policy JSON.",
    )
    parser.add_argument(
        "--baseline-path",
        default=str(REPO_ROOT / ".quality" / "baselines" / "test_conventions_baseline.txt"),
        help="Path to a baseline file with one known violation per line prefixed by '- '.",
    )
    args = parser.parse_args()

    src_root = Path(args.src_root).resolve()
    unit_test_root = Path(args.unit_test_root).resolve()

    if not src_root.exists():
        print(f"Source root does not exist: {src_root}", file=sys.stderr)
        return 1
    if not unit_test_root.exists():
        print(f"Unit test root does not exist: {unit_test_root}", file=sys.stderr)
        return 1

    configured_include_roots = (
        args.source_include_roots
        if args.source_include_roots is not None
        else load_default_source_include_roots(Path(args.policy_path))
    )

    include_roots: list[Path] = []
    for relative_path in configured_include_roots:
        include_root = (REPO_ROOT / relative_path).resolve()
        if include_root.exists():
            include_roots.append(include_root)
        else:
            print(f"Warning: include root not found and skipped: {relative_path}", file=sys.stderr)

    if not include_roots:
        print("No valid source include roots were found.", file=sys.stderr)
        return 1

    include_to_test_root, mapping_errors = build_include_to_test_root_map(include_roots, unit_test_root)
    if mapping_errors:
        print("Test convention check failed.", file=sys.stderr)
        for mapping_error in mapping_errors:
            print(f" - {mapping_error}", file=sys.stderr)
        return 1

    source_classes: list[SourceClassInfo] = []
    source_errors: list[str] = []
    for include_root in include_roots:
        classes, errors = parse_source_classes(include_root)
        source_classes.extend(classes)
        source_errors.extend(errors)

    source_classes, combine_errors = combine_partial_source_classes(source_classes)
    test_classes, test_errors = parse_test_classes(unit_test_root)

    violations = [*source_errors, *combine_errors, *test_errors]
    violations.extend(validate_conventions(source_classes, test_classes, include_to_test_root))
    baseline_violations = load_baseline_violations(Path(args.baseline_path))
    if baseline_violations:
        violations = [
            violation
            for violation in violations
            if canonicalize_violation_key(violation) not in baseline_violations
        ]

    if violations:
        print("Test convention check failed.", file=sys.stderr)
        displayed = violations[: args.max_violations]
        for violation in displayed:
            print(f" - {violation}", file=sys.stderr)
        if len(violations) > len(displayed):
            remaining = len(violations) - len(displayed)
            print(f" - ... {remaining} additional violations omitted", file=sys.stderr)
        return 1

    print("Test convention check passed.")
    return 0


def load_baseline_violations(path: Path) -> set[str]:
    if not path.exists():
        return set()

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as ex:
        print(f"Warning: failed to read baseline file '{path}': {ex}", file=sys.stderr)
        return set()

    violations: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("- "):
            violations.add(canonicalize_violation_key(line[2:].strip()))
        elif line.startswith(" - "):
            violations.add(canonicalize_violation_key(line[3:].strip()))

    return violations


def canonicalize_violation_key(value: str) -> str:
    return value.replace("\\", "/")


if __name__ == "__main__":
    raise SystemExit(main())
