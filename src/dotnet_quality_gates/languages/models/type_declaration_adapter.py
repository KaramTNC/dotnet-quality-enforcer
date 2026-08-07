from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class TypeDeclarationAdapter(Protocol):
    """Optional adapter capability used by source type layout validation."""

    def parse_type_declarations(self, path: Path, text: str) -> list[tuple[str, int]]: ...
