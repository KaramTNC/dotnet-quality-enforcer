from dataclasses import dataclass


@dataclass(frozen=True)
class ComplexityMetric:
    """Normalized method complexity metric emitted by a language adapter."""

    path: str
    name: str
    start_line: int
    end_line: int
    complexity: int
    cognitive_complexity: int
    parameters: int = 0
