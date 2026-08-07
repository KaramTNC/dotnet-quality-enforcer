from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .base import iter_source_files
from .models import CodeSizeThresholds, ComplexityMetric, LanguageAdapter, LanguageMetric, MetricSpan

if TYPE_CHECKING:
    from dotnet_quality_gates.quality.check_code_size import CodeSizeMetric


class CSharpAdapter(LanguageAdapter):
    """Adapter boundary for the existing C#/.NET analyzers."""

    language = "csharp"
    display_name = "C#"
    extensions = frozenset({".cs"})

    def discover_files(self, root: Path) -> list[Path]:
        return iter_source_files(root, self.extensions)

    def parse_code_size_metrics(self, path: Path, text: str, config: CodeSizeThresholds) -> list[LanguageMetric]:
        from dotnet_quality_gates.quality.check_code_size import (
            _parse_csharp_metrics_for_adapter,
        )

        return [
            _from_legacy_metric(metric)
            for metric in _parse_csharp_metrics_for_adapter(path, text, config)
        ]

    def parse_complexity_metrics(self, path: str, text: str) -> list[ComplexityMetric]:
        from dotnet_quality_gates.quality.diff_complexity_parsing import _parse_csharp_complexity_for_adapter

        return [
            ComplexityMetric(
                path=metric.path,
                name=metric.name,
                start_line=metric.start_line,
                end_line=metric.end_line,
                complexity=metric.complexity,
                cognitive_complexity=metric.cognitive_complexity,
                parameters=_parameter_count(metric.signature_key),
            )
            for metric in _parse_csharp_complexity_for_adapter(path, text)
        ]


def _from_legacy_metric(metric: CodeSizeMetric) -> LanguageMetric:
    spans = tuple(
        MetricSpan(
            path=getattr(span, "path"),
            start_line=getattr(span, "start_line"),
            end_line=getattr(span, "end_line"),
        )
        for span in getattr(metric, "spans", ())
    )
    return LanguageMetric(
        kind=getattr(metric, "kind"),
        path=getattr(metric, "path"),
        name=getattr(metric, "name"),
        start_line=getattr(metric, "start_line"),
        end_line=getattr(metric, "end_line"),
        line_count=getattr(metric, "line_count"),
        warn_limit=getattr(metric, "warn_limit"),
        fail_limit=getattr(metric, "fail_limit"),
        is_partial_type=getattr(metric, "is_partial_type", False),
        type_key=getattr(metric, "type_key", ""),
        spans=spans,
    )


def _parameter_count(signature_key: str) -> int:
    try:
        return int(signature_key.rsplit("/", 1)[1])
    except (IndexError, ValueError):
        return 0
