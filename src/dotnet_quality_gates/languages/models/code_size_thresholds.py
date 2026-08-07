from typing import Protocol


class CodeSizeThresholds(Protocol):
    """Thresholds shared by the language-neutral code-size gate."""

    @property
    def method_warn_lines(self) -> int: ...

    @property
    def method_max_lines(self) -> int: ...

    @property
    def type_warn_lines(self) -> int: ...

    @property
    def type_max_lines(self) -> int: ...

    @property
    def file_warn_lines(self) -> int: ...

    @property
    def file_max_lines(self) -> int: ...
