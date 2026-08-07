from dataclasses import dataclass
from pathlib import Path

from .test_method_info import TestMethodInfo


@dataclass
class TestClassInfo:
    name: str
    path: Path
    line: int
    methods: list[TestMethodInfo]
