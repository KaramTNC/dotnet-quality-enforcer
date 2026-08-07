from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from dotnet_quality_gates.context import current_context
from dotnet_quality_gates.languages import adapter_for_path, adapters_for_language, source_diff_pathspecs
from dotnet_quality_gates.languages.models import CodeSizeThresholds, LanguageMetric, MetricSpan
from dotnet_quality_gates.quality.code_size_config import CodeSizeConfig
from dotnet_quality_gates.quality.code_size_metric import CodeSizeMetric
from dotnet_quality_gates.quality.code_size_parsing import count_file_lines, parse_methods, parse_types
from dotnet_quality_gates.quality.common import (  # noqa: E402
    load_policy_object,
    load_prefixed_baseline_violations,
    parse_changed_lines,
    policy_section,
    sanitize_string_list,
)
from dotnet_quality_gates.subprocess_utils import run_command

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

BASELINE_FILE_VIOLATION_PATTERN = re.compile(
    r"^(?P<path>.+) has \d+ file lines; (?P<severity>warn|fail) threshold is \d+\.$"
)
BASELINE_MEMBER_VIOLATION_PATTERN = re.compile(
    r"^(?P<path>.+):\d+: (?P<name>.+) has \d+ (?P<kind>type|method) lines; "
    r"(?P<severity>warn|fail) threshold is \d+\.$"
)


def load_code_size_config(policy_path: Path) -> CodeSizeConfig:
    policy = load_policy_object(policy_path, "code size")
    section = policy_section(policy, "code_size")
    include_roots = sanitize_string_list(section.get("include_roots", DEFAULT_INCLUDE_ROOTS))
    exclude_globs = sanitize_string_list(section.get("exclude_globs", DEFAULT_EXCLUDE_GLOBS))

    language = current_context().language

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
        language=language,
    )


def sanitize_positive_int(value: object, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default


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


def file_metric(path: str, text: str, config: CodeSizeThresholds) -> CodeSizeMetric:
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
    language: str | None = None,
) -> list[Path]:
    files: list[Path] = []
    for include_root in include_roots:
        for adapter in adapters_for_language(language or current_context().language, include_root):
            for file_path in adapter.discover_files(include_root):
                if is_excluded(file_path, exclude_globs, repo_root=repo_root):
                    continue
                files.append(file_path)
    return sorted(files)


def run_git_diff(base: str, language: str = "auto") -> str:
    result = run_command(
        [
            "git",
            "diff",
            "--unified=0",
            f"{base}...HEAD",
            "--",
            *source_diff_pathspecs(language=language),
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
    adapter = adapter_for_path(path)
    normalized_metrics = adapter.parse_code_size_metrics(Path(relative_path), text, config)
    return [_code_size_metric(metric) for metric in normalized_metrics]


def _code_size_metric(metric: LanguageMetric) -> CodeSizeMetric:
    return CodeSizeMetric(
        kind=metric.kind,
        path=metric.path,
        name=metric.name,
        start_line=metric.start_line,
        end_line=metric.end_line,
        line_count=metric.line_count,
        warn_limit=metric.warn_limit,
        fail_limit=metric.fail_limit,
        spans=tuple(MetricSpan(span.path, span.start_line, span.end_line) for span in metric.spans),
        is_partial_type=metric.is_partial_type,
        type_key=metric.type_key,
    )


def _parse_csharp_metrics_for_adapter(path: Path, text: str, config: CodeSizeThresholds) -> list[CodeSizeMetric]:
    relative_path = path.as_posix()
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
        for file_path in collect_files(
            include_roots,
            config.exclude_globs,
            repo_root=REPO_ROOT,
            language=config.language,
        )
        for metric in collect_metrics_for_file(file_path, config)
    ]
    return aggregate_partial_type_metrics(metrics)


def collect_diff_metrics(base: str, config: CodeSizeConfig) -> list[CodeSizeMetric]:
    changed = parse_changed_lines(run_git_diff(base, config.language))
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail supported source code that exceeds configured file, type, or method size thresholds."
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
    return parser.parse_args()


def _collect_metrics_for_scope(args: argparse.Namespace, config: CodeSizeConfig) -> list[CodeSizeMetric] | None:
    try:
        return collect_full_metrics(config) if args.scope == "full" else collect_diff_metrics(args.base, config)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as ex:
        detail = getattr(ex, "stderr", None) or str(ex)
        print(f"Code size check could not inspect the repository: {detail.strip()}", file=sys.stderr)
        return None


def _remove_baseline_violations(
    failures: list[str], warnings: list[str], baseline_path: Path
) -> tuple[list[str], list[str]]:
    baseline_violations = load_prefixed_baseline_violations(baseline_path, canonicalize_baseline_violation)
    if not baseline_violations:
        return failures, warnings
    return (
        [violation for violation in failures if canonicalize_baseline_violation(violation) not in baseline_violations],
        [warning for warning in warnings if canonicalize_baseline_violation(warning) not in baseline_violations],
    )


def _print_code_size_results(
    args: argparse.Namespace,
    config: CodeSizeConfig,
    failures: list[str],
    warnings: list[str],
) -> int:
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


def main() -> int:
    args = _parse_args()

    if args.scope == "diff" and not args.base:
        print("Code size check failed: --base is required when --scope diff.", file=sys.stderr)
        return 1

    config = load_code_size_config(Path(args.policy_path))
    metrics = _collect_metrics_for_scope(args, config)
    if metrics is None:
        return 1
    failures, warnings = split_violations(metrics)
    failures, warnings = _remove_baseline_violations(failures, warnings, Path(args.baseline_path))
    return _print_code_size_results(args, config, failures, warnings)


if __name__ == "__main__":
    raise SystemExit(main())
