from dataclasses import dataclass


@dataclass(frozen=True)
class MethodMetric:
    path: str
    name: str
    signature_key: str
    start_line: int
    end_line: int
    complexity: int
    cognitive_complexity: int = 0
    coverable_lines: int = 0
    covered_lines: int = 0

    @property
    def coverage_ratio(self) -> float:
        return 1.0 if self.coverable_lines == 0 else self.covered_lines / self.coverable_lines

    @property
    def crap_score(self) -> float:
        uncovered = 1.0 - self.coverage_ratio
        return (self.complexity**2) * (uncovered**3) + self.complexity
