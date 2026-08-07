from dataclasses import dataclass


@dataclass(frozen=True)
class RoslynDiagnostic:
    diagnostic_id: str
    message: str
    line: int
