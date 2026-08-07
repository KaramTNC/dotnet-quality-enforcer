from dataclasses import dataclass

from dotnet_quality_gates.languages.models import MetricSpan


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
