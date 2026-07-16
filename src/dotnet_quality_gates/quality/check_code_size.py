from __future__ import annotations

import argparse
import bisect
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dotnet_quality_gates.context import current_context
from dotnet_quality_gates.quality.common import (  # noqa: E402
    load_policy_object,
    load_prefixed_baseline_violations,
    parse_changed_lines,
    policy_section,
    sanitize_string_list,
)
from dotnet_quality_gates.subprocess_utils import run_command
from dotnet_quality_gates.unit_test_conventions import (  # noqa: E402
    find_matching_brace,
    iter_cs_files,
    mask_comments_and_strings,
)

REPO_ROOT = current_context().repo_root
DEFAULT_POLICY_PATH = current_context().policy_path

DEFAULT_INCLUDE_ROOTS = ["src"]
DEFAULT_EXCLUDE_GLOBS = [
    "**/*.Designer.cs",
    "**/*.g.cs",
    "**/*.g.i.cs",
    "**/GlobalUsings.cs",
    "**/AssemblyInfo.cs",
]
DEFAULT_METHOD_WARN_LINES = 40
DEFAULT_METHOD_MAX_LINES = 60
DEFAULT_TYPE_WARN_LINES = 250
DEFAULT_TYPE_MAX_LINES = 350
DEFAULT_FILE_WARN_LINES = 300
DEFAULT_FILE_MAX_LINES = 450

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
TYPE_DECLARATION_PATTERN = re.compile(
    r"\b(?:public|protected|internal|private|file)?\s*"
    r"(?:(?:abstract|sealed|static|partial|readonly|unsafe)\s+)*"
    r"(?:(class|struct|interface)|record(?:\s+(?:class|struct))?)\s+([A-Za-z_]\w*)\b"
)
BASELINE_FILE_VIOLATION_PATTERN = re.compile(
    r"^(?P<path>.+) has \d+ file lines; (?P<severity>warn|fail) threshold is \d+\.$"
)
BASELINE_MEMBER_VIOLATION_PATTERN = re.compile(
    r"^(?P<path>.+):\d+: (?P<name>.+) has \d+ (?P<kind>type|method) lines; "
    r"(?P<severity>warn|fail) threshold is \d+\.$"
)


@dataclass(frozen=True)
class CodeSizeConfig:
    include_roots: list[str]
    exclude_globs: list[str]
    method_warn_lines: int
    method_max_lines: int
    type_warn_lines: int
    type_max_lines: int
    file_warn_lines: int
    file_max_lines: int


@dataclass(frozen=True)
class MetricSpan:
    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class CodeSizeMetric:
    kind: str
    path: str
    name: str
    start_line: int
    end_line: int
    line_count: int
    warn_limit: int
    fail_limit: int
    spans: tuple[MetricSpan, ...] = ()
    is_partial_type: bool = False
    type_key: str = ""

    @property
    def location(self) -> str:
        if self.kind == "file":
            return self.path
        return f"{self.path}:{self.start_line}: {self.name}"

    @property
    def all_spans(self) -> tuple[MetricSpan, ...]:
        return self.spans or (MetricSpan(self.path, self.start_line, self.end_line),)


def load_code_size_config(policy_path: Path) -> CodeSizeConfig:
    section = policy_section(load_policy_object(policy_path, "code size"), "code_size")
    include_roots = sanitize_string_list(section.get("include_roots", DEFAULT_INCLUDE_ROOTS))
    exclude_globs = sanitize_string_list(section.get("exclude_globs", DEFAULT_EXCLUDE_GLOBS))

    return CodeSizeConfig(
        include_roots=include_roots or list(DEFAULT_INCLUDE_ROOTS),
        exclude_globs=exclude_globs or list(DEFAULT_EXCLUDE_GLOBS),
        method_warn_lines=sanitize_positive_int(
            section.get("method_warn_lines"),
            DEFAULT_METHOD_WARN_LINES,
        ),
        method_max_lines=sanitize_positive_int(
            section.get("method_max_lines"),
            DEFAULT_METHOD_MAX_LINES,
        ),
        type_warn_lines=sanitize_positive_int(
            section.get("type_warn_lines"),
            DEFAULT_TYPE_WARN_LINES,
        ),
        type_max_lines=sanitize_positive_int(
            section.get("type_max_lines"),
            DEFAULT_TYPE_MAX_LINES,
        ),
        file_warn_lines=sanitize_positive_int(
            section.get("file_warn_lines"),
            DEFAULT_FILE_WARN_LINES,
        ),
        file_max_lines=sanitize_positive_int(
            section.get("file_max_lines"),
            DEFAULT_FILE_MAX_LINES,
        ),
    )


def sanitize_positive_int(value: object, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default


def line_starts(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def line_for_index(starts: list[int], index: int) -> int:
    return bisect.bisect_right(starts, index)


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
    return any(
        re.match(rf"^{keyword}\s*(?:\(|\b)", header)
        for keyword in CONTROL_KEYWORDS
    )


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


def parse_block_methods(path: str, text: str, config: CodeSizeConfig) -> list[CodeSizeMetric]:
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


def parse_expression_bodied_methods(
    path: str,
    text: str,
    config: CodeSizeConfig,
) -> list[CodeSizeMetric]:
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


def parse_methods(path: str, text: str, config: CodeSizeConfig) -> list[CodeSizeMetric]:
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


def parse_types(path: str, text: str, config: CodeSizeConfig) -> list[CodeSizeMetric]:
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
        is_partial = bool(re.search(r"\bpartial\b", match.group(0)))
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
                is_partial_type=is_partial,
                type_key=type_key,
            )
        )

    return sorted(types, key=lambda type_metric: (type_metric.start_line, type_metric.end_line))


def aggregate_partial_type_metrics(metrics: list[CodeSizeMetric]) -> list[CodeSizeMetric]:
    partial_groups: dict[str, list[CodeSizeMetric]] = {}
    aggregated: list[CodeSizeMetric] = []

    for metric in metrics:
        if metric.kind == "type" and metric.is_partial_type:
            partial_groups.setdefault(metric.type_key, []).append(metric)
        else:
            aggregated.append(metric)

    for group in partial_groups.values():
        ordered = sorted(group, key=lambda item: (item.path, item.start_line))
        if len(ordered) == 1:
            aggregated.append(ordered[0])
            continue

        first = ordered[0]
        aggregated.append(
            CodeSizeMetric(
                kind="type",
                path=first.path,
                name=f"{first.name} (partial aggregate)",
                start_line=first.start_line,
                end_line=first.end_line,
                line_count=sum(item.line_count for item in ordered),
                warn_limit=first.warn_limit,
                fail_limit=first.fail_limit,
                spans=tuple(
                    MetricSpan(item.path, item.start_line, item.end_line)
                    for item in ordered
                ),
                is_partial_type=True,
                type_key=first.type_key,
            )
        )

    return sorted(aggregated, key=lambda item: (item.path, item.start_line, item.end_line, item.name))


def file_metric(path: str, text: str, config: CodeSizeConfig) -> CodeSizeMetric:
    line_count = count_file_lines(text)
    return CodeSizeMetric(
        kind="file",
        path=path,
        name=Path(path).name,
        start_line=1,
        end_line=line_count,
        line_count=line_count,
        warn_limit=config.file_warn_lines,
        fail_limit=config.file_max_lines,
    )


def metric_intersects_lines(metric: CodeSizeMetric, changed_lines: set[int]) -> bool:
    return any(metric.start_line <= line <= metric.end_line for line in changed_lines)


def metric_intersects_changed_lines(
    metric: CodeSizeMetric,
    changed_lines_by_path: dict[str, set[int]],
) -> bool:
    if metric.kind == "file":
        return metric.path in changed_lines_by_path

    for span in metric.all_spans:
        changed_lines = changed_lines_by_path.get(span.path)
        if changed_lines and any(span.start_line <= line <= span.end_line for line in changed_lines):
            return True

    return False


def is_excluded(path: Path, exclude_globs: list[str], repo_root: Path = REPO_ROOT) -> bool:
    try:
        relative_path = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return False
    return any(relative_path.match(pattern) for pattern in exclude_globs)


def collect_files(
    include_roots: list[Path],
    exclude_globs: list[str],
    repo_root: Path = REPO_ROOT,
) -> list[Path]:
    files: list[Path] = []
    for include_root in include_roots:
        for file_path in iter_cs_files(include_root):
            if is_excluded(file_path, exclude_globs, repo_root=repo_root):
                continue
            files.append(file_path)
    return sorted(files)


def run_git_diff(base: str) -> str:
    result = run_command(
        [
            "git",
            "diff",
            "--unified=0",
            f"{base}...HEAD",
            "--",
            ":(glob)src/**/*.cs",
            ":(exclude)**/AssemblyInfo.cs",
            ":(exclude)**/*.g.cs",
            ":(exclude)**/*.g.i.cs",
            ":(exclude)**/*.Designer.cs",
            ":(exclude)**/GlobalUsings.cs",
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    return result.stdout


def collect_metrics_for_file(path: Path, config: CodeSizeConfig) -> list[CodeSizeMetric]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    # Resolve both paths before comparing them. Windows temporary directories
    # may expose the repository root through an 8.3 alias while file discovery
    # returns the corresponding long path.
    relative_path = path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    return [
        file_metric(relative_path, text, config),
        *parse_types(relative_path, text, config),
        *parse_methods(relative_path, text, config),
    ]


def collect_full_metrics(config: CodeSizeConfig) -> list[CodeSizeMetric]:
    include_roots = [
        (REPO_ROOT / include_root_text).resolve()
        for include_root_text in config.include_roots
        if (REPO_ROOT / include_root_text).exists()
    ]
    metrics = [
        metric
        for file_path in collect_files(include_roots, config.exclude_globs, repo_root=REPO_ROOT)
        for metric in collect_metrics_for_file(file_path, config)
    ]
    return aggregate_partial_type_metrics(metrics)


def collect_diff_metrics(base: str, config: CodeSizeConfig) -> list[CodeSizeMetric]:
    changed = parse_changed_lines(run_git_diff(base))
    if not changed:
        return []

    return [
        metric
        for metric in collect_full_metrics(config)
        if metric_intersects_changed_lines(metric, changed)
    ]


def format_metric(metric: CodeSizeMetric, severity: str) -> str:
    limit = metric.fail_limit if severity == "fail" else metric.warn_limit
    return (
        f"{metric.location} has {metric.line_count} {metric.kind} lines; "
        f"{severity} threshold is {limit}."
    )


def split_violations(metrics: list[CodeSizeMetric]) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    for metric in sorted(metrics, key=lambda item: (-item.line_count, item.path, item.start_line)):
        if metric.line_count > metric.fail_limit:
            failures.append(format_metric(metric, "fail"))
        elif metric.line_count > metric.warn_limit:
            warnings.append(format_metric(metric, "warn"))
    return failures, warnings


def canonicalize_baseline_violation(violation: str) -> str:
    file_match = BASELINE_FILE_VIOLATION_PATTERN.match(violation)
    if file_match:
        return f"file:{file_match.group('path')}"

    member_match = BASELINE_MEMBER_VIOLATION_PATTERN.match(violation)
    if member_match:
        return (
            f"{member_match.group('kind')}:{member_match.group('path')}:"
            f"{member_match.group('name')}"
        )

    return violation


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail C# code that exceeds configured file, type, or method size thresholds."
    )
    parser.add_argument("--scope", choices=["full", "diff"], default="full")
    parser.add_argument("--base", default=None, help="Base ref for --scope diff.")
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument(
        "--baseline-path",
        default=str(REPO_ROOT / ".quality" / "baselines" / "code_size_baseline.txt"),
        help="Path to a baseline file with one known metric violation per line prefixed by '- '.",
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=250,
        help="Maximum failures/warnings to print per severity.",
    )
    args = parser.parse_args()

    if args.scope == "diff" and not args.base:
        print("Code size check failed: --base is required when --scope diff.", file=sys.stderr)
        return 1

    config = load_code_size_config(Path(args.policy_path))
    try:
        metrics = (
            collect_full_metrics(config)
            if args.scope == "full"
            else collect_diff_metrics(args.base, config)
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as ex:
        detail = getattr(ex, "stderr", None) or str(ex)
        print(f"Code size check could not inspect the repository: {detail.strip()}", file=sys.stderr)
        return 1
    failures, warnings = split_violations(metrics)
    baseline_violations = load_prefixed_baseline_violations(
        Path(args.baseline_path),
        canonicalize_baseline_violation,
    )
    if baseline_violations:
        failures = [
            violation
            for violation in failures
            if canonicalize_baseline_violation(violation) not in baseline_violations
        ]
        warnings = [
            violation
            for violation in warnings
            if canonicalize_baseline_violation(violation) not in baseline_violations
        ]

    if warnings:
        print("Code size warnings:")
        for warning in warnings[: args.max_details]:
            print(f" - {warning}")
        if len(warnings) > args.max_details:
            print(f" - ... {len(warnings) - args.max_details} additional warnings omitted")

    if failures:
        print("Code size gate failed.", file=sys.stderr)
        for failure in failures[: args.max_details]:
            print(f" - {failure}", file=sys.stderr)
        if len(failures) > args.max_details:
            print(f" - ... {len(failures) - args.max_details} additional failures omitted", file=sys.stderr)
        return 1

    print(
        "Code size gate passed "
        f"({args.scope}; method <= {config.method_max_lines}, "
        f"type <= {config.type_max_lines}, file <= {config.file_max_lines})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
