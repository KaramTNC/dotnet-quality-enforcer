from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from dotnet_quality_gates.context import current_context
from dotnet_quality_gates.quality.common import load_policy_object, policy_section

REPO_ROOT = current_context().repo_root
DEFAULT_POLICY_PATH = current_context().policy_path

DEFAULT_EXPECTED_PACKAGES: set[str] = set()
MAX_COVERAGE_XML_BYTES = 50 * 1024 * 1024
UNSAFE_XML_DECLARATION_PATTERN = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def load_expected_packages(policy_path: Path) -> set[str]:
    if not policy_path.exists():
        return set(DEFAULT_EXPECTED_PACKAGES)

    section = policy_section(load_policy_object(policy_path, "expected packages"), "repo_coverage")
    if not section:
        return set(DEFAULT_EXPECTED_PACKAGES)

    expected_packages = section.get("expected_packages", DEFAULT_EXPECTED_PACKAGES)
    if not isinstance(expected_packages, list) or not all(
        isinstance(name, str) and name.strip() for name in expected_packages
    ):
        print(
            "Warning: invalid 'repo_coverage.expected_packages' in policy file. "
            "Falling back to built-in expected packages.",
            file=sys.stderr,
        )
        return set(DEFAULT_EXPECTED_PACKAGES)

    return {name.strip() for name in expected_packages}


def parse_condition_coverage(line_node: ET.Element) -> tuple[int, int]:
    condition_coverage = line_node.attrib.get("condition-coverage")
    if condition_coverage:
        match = re.search(r"\((\d+)/(\d+)\)", condition_coverage)
        if match:
            return int(match.group(1)), int(match.group(2))

    conditions = line_node.findall("./conditions/condition")
    if conditions:
        covered = 0
        total = 0
        for condition in conditions:
            total += 1
            coverage = condition.attrib.get("coverage", "0").rstrip("%")
            try:
                if float(coverage) > 0:
                    covered += 1
            except ValueError:
                continue
        return covered, total

    return 0, 0


def parse_merged_cobertura(
    path: Path,
    expected_packages: set[str],
) -> tuple[
    tuple[int, int],
    tuple[int, int],
    dict[str, tuple[int, int, int, int]],
    dict[str, tuple[int, int, int, int]],
]:
    root = parse_safe_xml(path)

    class_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    package_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])

    for package in root.findall(".//package"):
        package_name = package.attrib.get("name", "<unknown>")
        for class_node in package.findall("./classes/class"):
            class_name = class_node.attrib.get("name", "<unknown>")
            class_key = f"{package_name}.{class_name}"

            for line_node in class_node.findall("./lines/line"):
                hits = int(line_node.attrib["hits"])
                class_stats[class_key][1] += 1
                package_stats[package_name][1] += 1
                if hits > 0:
                    class_stats[class_key][0] += 1
                    package_stats[package_name][0] += 1

                if line_node.attrib.get("branch", "").lower() == "true":
                    covered_branches, total_branches = parse_condition_coverage(line_node)
                    class_stats[class_key][3] += total_branches
                    class_stats[class_key][2] += covered_branches
                    package_stats[package_name][3] += total_branches
                    package_stats[package_name][2] += covered_branches

    relevant_package_stats = {name: stats for name, stats in package_stats.items() if name in expected_packages}
    overall_line_covered = sum(stats[0] for stats in relevant_package_stats.values())
    overall_line_total = sum(stats[1] for stats in relevant_package_stats.values())
    overall_branch_covered = sum(stats[2] for stats in relevant_package_stats.values())
    overall_branch_total = sum(stats[3] for stats in relevant_package_stats.values())

    return (
        (overall_line_covered, overall_line_total),
        (overall_branch_covered, overall_branch_total),
        {
            name: (covered, valid, branch_covered, branch_total)
            for name, (covered, valid, branch_covered, branch_total) in package_stats.items()
        },
        {
            name: (covered, valid, branch_covered, branch_total)
            for name, (covered, valid, branch_covered, branch_total) in class_stats.items()
        },
    )


def parse_safe_xml(path: Path) -> ET.Element:
    with path.open("rb") as stream:
        content = stream.read(MAX_COVERAGE_XML_BYTES + 1)

    if len(content) > MAX_COVERAGE_XML_BYTES:
        raise ValueError(
            f"XML report exceeds the {MAX_COVERAGE_XML_BYTES} byte safety limit"
        )
    if UNSAFE_XML_DECLARATION_PATTERN.search(content):
        raise ValueError("XML reports must not contain DTD or entity declarations")

    # ElementTree does not fetch external resources, while the declaration check
    # above prevents DTD-based entity expansion before parsing untrusted reports.
    return ET.fromstring(content)


def ratio(covered: int, valid: int) -> float:
    return 1.0 if valid == 0 else covered / valid


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", required=True, help="Path to merged Cobertura.xml")
    parser.add_argument(
        "--line-threshold",
        type=float,
        default=None,
        help="Required repo line coverage ratio.",
    )
    parser.add_argument(
        "--branch-threshold",
        type=float,
        default=None,
        help="Required repo branch coverage ratio.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Deprecated alias for --line-threshold.",
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=50,
        help="Maximum uncovered packages/classes to print",
    )
    parser.add_argument(
        "--policy-path",
        default=str(DEFAULT_POLICY_PATH),
        help="Path to code quality policy JSON.",
    )
    args = parser.parse_args()

    coverage_path = Path(args.coverage)
    if not coverage_path.exists():
        print(f"Coverage file not found: {coverage_path}", file=sys.stderr)
        return 1

    expected_packages = load_expected_packages(Path(args.policy_path))
    if not expected_packages:
        print("No expected packages configured for repo coverage gate.", file=sys.stderr)
        return 1

    line_threshold = args.line_threshold
    if line_threshold is None:
        line_threshold = args.threshold if args.threshold is not None else 1.0
    branch_threshold = args.branch_threshold if args.branch_threshold is not None else None

    try:
        overall_line, overall_branch, package_stats, class_stats = parse_merged_cobertura(
            coverage_path,
            expected_packages,
        )
    except (OSError, ET.ParseError, KeyError, ValueError) as ex:
        print(f"Unable to read coverage report '{coverage_path}': {ex}", file=sys.stderr)
        return 1
    missing_packages = sorted(expected_packages - set(package_stats))

    relevant_package_stats = {name: stats for name, stats in package_stats.items() if name in expected_packages}
    relevant_class_stats = {
        name: stats for name, stats in class_stats.items() if name.split(".", 1)[0] in expected_packages
    }

    lowest_line_packages = sorted(
        (
            (name, covered, valid, ratio(covered, valid))
            for name, (covered, valid, _, _) in relevant_package_stats.items()
        ),
        key=lambda item: item[3],
    )
    lowest_line_classes = sorted(
        (
            (name, covered, valid, ratio(covered, valid))
            for name, (covered, valid, _, _) in relevant_class_stats.items()
        ),
        key=lambda item: item[3],
    )
    lowest_branch_packages = sorted(
        (
            (name, covered, valid, ratio(covered, valid))
            for name, (_, _, covered, valid) in relevant_package_stats.items()
            if valid > 0
        ),
        key=lambda item: item[3],
    )
    lowest_branch_classes = sorted(
        (
            (name, covered, valid, ratio(covered, valid))
            for name, (_, _, covered, valid) in relevant_class_stats.items()
            if valid > 0
        ),
        key=lambda item: item[3],
    )

    overall_line_ratio = ratio(*overall_line)
    print(f"Repo line coverage: {overall_line_ratio * 100:.2f}% (threshold {line_threshold * 100:.2f}%)")

    overall_branch_ratio = None
    if branch_threshold is not None:
        overall_branch_ratio = ratio(*overall_branch)
        print(
            f"Repo branch coverage: {overall_branch_ratio * 100:.2f}% "
            f"(threshold {branch_threshold * 100:.2f}%)"
        )

    line_failed = overall_line_ratio + 1e-9 < line_threshold
    branch_failed = (
        branch_threshold is not None
        and overall_branch_ratio is not None
        and overall_branch_ratio + 1e-9 < branch_threshold
    )

    if not line_failed and not branch_failed and not missing_packages:
        print("Repo coverage gate passed.")
        return 0

    print("Repo coverage gate failed.", file=sys.stderr)

    if missing_packages:
        print("Expected package aliases were not present in the merged report:", file=sys.stderr)
        for name in missing_packages:
            print(f" - {name}", file=sys.stderr)

    if line_failed:
        print("Lowest line-coverage packages:", file=sys.stderr)
        for name, covered, valid, pkg_ratio in lowest_line_packages[: args.max_details]:
            print(
                f" - {name}: {covered}/{valid} lines covered ({pkg_ratio * 100:.2f}%)",
                file=sys.stderr,
            )
        if len(lowest_line_packages) > args.max_details:
            print(f" - ... {len(lowest_line_packages) - args.max_details} more packages", file=sys.stderr)

        print("Lowest line-coverage classes:", file=sys.stderr)
        for name, covered, valid, cls_ratio in lowest_line_classes[: args.max_details]:
            print(
                f" - {name}: {covered}/{valid} lines covered ({cls_ratio * 100:.2f}%)",
                file=sys.stderr,
            )
        if len(lowest_line_classes) > args.max_details:
            print(f" - ... {len(lowest_line_classes) - args.max_details} more classes", file=sys.stderr)

    if branch_failed:
        print("Lowest branch-coverage packages:", file=sys.stderr)
        for name, covered, valid, pkg_ratio in lowest_branch_packages[: args.max_details]:
            print(
                f" - {name}: {covered}/{valid} branches covered ({pkg_ratio * 100:.2f}%)",
                file=sys.stderr,
            )
        if len(lowest_branch_packages) > args.max_details:
            print(f" - ... {len(lowest_branch_packages) - args.max_details} more packages", file=sys.stderr)

        print("Lowest branch-coverage classes:", file=sys.stderr)
        for name, covered, valid, cls_ratio in lowest_branch_classes[: args.max_details]:
            print(
                f" - {name}: {covered}/{valid} branches covered ({cls_ratio * 100:.2f}%)",
                file=sys.stderr,
            )
        if len(lowest_branch_classes) > args.max_details:
            print(f" - ... {len(lowest_branch_classes) - args.max_details} more classes", file=sys.stderr)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
