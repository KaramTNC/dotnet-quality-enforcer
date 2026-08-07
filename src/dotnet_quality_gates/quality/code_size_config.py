from dataclasses import dataclass


@dataclass(frozen=True)
class CodeSizeConfig:
    include_roots: list[str]
    exclude_globs: list[str]
    method_warn_lines: int
    method_max_lines: int
    type_warn_lines: int
    type_max_lines: int
    file_warn_lines: int
    file_max_lines: int
    language: str = "csharp"
