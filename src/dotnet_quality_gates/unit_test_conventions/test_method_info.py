from dataclasses import dataclass


@dataclass
class TestMethodInfo:
    name: str
    line: int
    region: str | None
    is_test_method: bool
    method_under_test_from_name: str | None
