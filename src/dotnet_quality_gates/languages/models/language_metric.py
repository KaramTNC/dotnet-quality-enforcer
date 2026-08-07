from dataclasses import dataclass

from .metric_span import MetricSpan


@dataclass(frozen=True)
class LanguageMetric:
    """Normalized size metric emitted by a language adapter."""

    kind: str
    path: str
    name: str
    start_line: int
    end_line: int
    line_count: int
    warn_limit: int
    fail_limit: int
    is_partial_type: bool = False
    type_key: str = ""
    spans: tuple[MetricSpan, ...] = ()
