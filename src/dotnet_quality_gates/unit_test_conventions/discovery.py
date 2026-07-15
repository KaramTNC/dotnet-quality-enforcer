from __future__ import annotations

from pathlib import Path

from .constants import SKIP_DIR_NAMES


def iter_csharp_files(root: Path, skip_file_names: set[str] | frozenset[str] = frozenset()) -> list[Path]:
    """Return deterministic C# file discovery shared by all checks."""
    if not root.exists():
        return []

    return sorted(
        (
            path
            for path in root.rglob("*.cs")
            if path.name not in skip_file_names and not any(part in SKIP_DIR_NAMES for part in path.parts)
        ),
        key=lambda path: path.as_posix().lower(),
    )
