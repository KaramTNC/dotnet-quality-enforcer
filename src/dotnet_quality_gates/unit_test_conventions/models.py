from __future__ import annotations

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


@dataclass
class TestMethodInfo:
    name: str
    line: int
    region: str | None
    is_test_method: bool
    method_under_test_from_name: str | None


@dataclass
class TestClassInfo:
    name: str
    path: Path
    line: int
    methods: list[TestMethodInfo]
