from pathlib import Path
from typing import Protocol

from .code_size_thresholds import CodeSizeThresholds
from .complexity_metric import ComplexityMetric
from .language_metric import LanguageMetric


class LanguageAdapter(Protocol):
    """Core contract implemented by each supported programming language."""

    language: str
    display_name: str
    extensions: frozenset[str]

    def discover_files(self, root: Path) -> list[Path]: ...

    def parse_code_size_metrics(
        self, path: Path, text: str, config: CodeSizeThresholds
    ) -> list[LanguageMetric]: ...

    def parse_complexity_metrics(self, path: str, text: str) -> list[ComplexityMetric]: ...
