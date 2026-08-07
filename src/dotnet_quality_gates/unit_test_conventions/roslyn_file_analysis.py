from dataclasses import dataclass

from .models import SourceClassInfo, TestClassInfo
from .roslyn_diagnostic import RoslynDiagnostic


@dataclass(frozen=True)
class RoslynFileAnalysis:
    source_classes: list[SourceClassInfo]
    test_classes: list[TestClassInfo]
    type_declarations: list[tuple[str, int, str]]
    diagnostics: list[RoslynDiagnostic]
