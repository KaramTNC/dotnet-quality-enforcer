from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSpan:
    """A source range belonging to a language-neutral metric."""

    path: str
    start_line: int
    end_line: int
