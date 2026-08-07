from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from .models import LanguageAdapter

SUPPORTED_LANGUAGES = (
    "csharp",
    "python",
    "java",
    "kotlin",
    "typescript",
    "javascript",
    "go",
    "rust",
)
LANGUAGE_MODES = ("auto", *SUPPORTED_LANGUAGES)

LANGUAGE_ALIASES = {
    "c#": "csharp",
    "c-sharp": "csharp",
    "cs": "csharp",
    "csharp": "csharp",
    "py": "python",
    "python": "python",
    "java": "java",
    "kt": "kotlin",
    "kts": "kotlin",
    "kotlin": "kotlin",
    # Keep the spelling from the original request usable without making it
    # part of the canonical public language name.
    "kotlyn": "kotlin",
    "ts": "typescript",
    "tsx": "typescript",
    "typescript": "typescript",
    "js": "javascript",
    "jsx": "javascript",
    "javascript": "javascript",
    "golang": "go",
    "go": "go",
    "rs": "rust",
    "rust": "rust",
}

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        ".tox",
        ".gradle",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        "bin",
        "obj",
        "build",
        "dist",
        "node_modules",
        "target",
        "vendor",
    }
)
_EXTENSION_LANGUAGES = {
    ".cs": "csharp",
    ".py": "python",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
}
_SOURCE_EXTENSIONS = tuple(_EXTENSION_LANGUAGES)
_SUPPORTED_EXTENSIONS = frozenset(_EXTENSION_LANGUAGES)


def normalize_language(value: str | None, *, allow_auto: bool = True) -> str:
    normalized = (value or "").strip().lower()
    if allow_auto and normalized in {"", "auto"}:
        return "auto"
    try:
        return LANGUAGE_ALIASES[normalized]
    except KeyError as ex:
        choices = ", ".join((*SUPPORTED_LANGUAGES, "auto")) if allow_auto else ", ".join(SUPPORTED_LANGUAGES)
        raise ValueError(f"unsupported language '{value}'; expected one of: {choices}") from ex


def language_for_path(path: Path) -> str | None:
    return _EXTENSION_LANGUAGES.get(path.suffix.lower())


def source_diff_pathspecs(root: str = "src", language: str = "auto") -> tuple[str, ...]:
    """Return Git pathspecs covering the selected adapter language(s)."""
    canonical = normalize_language(language)
    extensions = (
        _SOURCE_EXTENSIONS
        if canonical == "auto"
        else tuple(extension for extension, detected in _EXTENSION_LANGUAGES.items() if detected == canonical)
    )
    return tuple(f":(glob){root}/**/*{extension}" for extension in extensions)


def iter_source_files(root: Path, extensions: frozenset[str]) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for current_root, directory_names, file_names in os.walk(root):
        directory_names[:] = sorted(name for name in directory_names if name.lower() not in _SKIP_DIRS)
        files.extend(
            Path(current_root) / file_name
            for file_name in file_names
            if Path(file_name).suffix.lower() in extensions
        )
    return sorted(files, key=lambda path: path.as_posix().lower())


def adapters_for_language(language: str, root: Path | None = None) -> list[LanguageAdapter]:
    """Return adapters for a requested language, or discover them in auto mode."""
    from .csharp import CSharpAdapter
    from .go import GoAdapter
    from .java import JavaAdapter
    from .javascript import JavaScriptAdapter
    from .kotlin import KotlinAdapter
    from .python import PythonAdapter
    from .rust import RustAdapter
    from .typescript import TypeScriptAdapter

    factories: dict[str, Callable[[], LanguageAdapter]] = {
        "csharp": CSharpAdapter,
        "python": PythonAdapter,
        "java": JavaAdapter,
        "kotlin": KotlinAdapter,
        "typescript": TypeScriptAdapter,
        "javascript": JavaScriptAdapter,
        "go": GoAdapter,
        "rust": RustAdapter,
    }
    canonical = normalize_language(language)
    if canonical != "auto":
        return [factories[canonical]()]
    if root is None:
        return [factories[name]() for name in SUPPORTED_LANGUAGES]

    discovered = {
        detected for path in iter_source_files(root, _SUPPORTED_EXTENSIONS) if (detected := language_for_path(path))
    }
    if not discovered:
        # Preserve the historical default for an empty C# repository.
        return [factories["csharp"]()]
    return [factories[name]() for name in SUPPORTED_LANGUAGES if name in discovered]


def adapter_for_path(path: str | Path) -> LanguageAdapter:
    detected = language_for_path(Path(path))
    if detected is None:
        raise ValueError(f"unsupported source extension for '{path}'")
    return adapters_for_language(detected)[0]


def supported_language_help() -> str:
    return (
        "C# (csharp), Python (python), Java (java), Kotlin (kotlin; kotlyn alias), "
        "TypeScript (typescript), JavaScript (javascript), Go (go), and Rust (rust)"
    )
