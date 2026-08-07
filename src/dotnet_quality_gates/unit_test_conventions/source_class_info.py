from dataclasses import dataclass
from pathlib import Path


@dataclass
class SourceClassInfo:
    name: str
    path: Path
    line: int
    exposed_methods: set[str]
    is_partial: bool
    targetable_members: set[str] | None = None
    requires_test_class: bool = True
    base_types: list[str] | None = None
