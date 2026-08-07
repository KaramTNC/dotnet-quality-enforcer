from __future__ import annotations

import re
from pathlib import Path

from .base import iter_source_files
from .masking import mask_comments_and_strings
from .models import (
    CodeSizeThresholds,
    ComplexityMetric,
    LanguageAdapter,
    LanguageMetric,
    TypeDeclarationAdapter,
)
from .specs import CONTROL_WORDS, RegexLanguageSpec


def _line_number(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _matching_brace(masked: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return len(masked)


def _python_end_line(text: str, start_line: int, indent: int) -> int:
    lines = text.splitlines()
    end = len(lines)
    for index in range(start_line, len(lines)):
        line = lines[index]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            end = index
            break
    return max(start_line, end)


class GenericAdapter(LanguageAdapter, TypeDeclarationAdapter):
    def __init__(self, spec: RegexLanguageSpec) -> None:
        self.spec = spec
        self.language = spec.language
        self.display_name = spec.display_name
        self.extensions = spec.extensions

    def discover_files(self, root: Path) -> list[Path]:
        return iter_source_files(root, self.extensions)

    def _type_matches(self, masked: str) -> list[re.Match[str]]:
        matches = [match for pattern in self.spec.type_patterns for match in pattern.finditer(masked)]
        return sorted(matches, key=lambda match: match.start())

    def _method_matches(self, masked: str) -> list[re.Match[str]]:
        matches = [match for pattern in self.spec.method_patterns for match in pattern.finditer(masked)]
        return sorted(matches, key=lambda match: match.start())

    @staticmethod
    def _match_parameters(match: re.Match[str]) -> str:
        return match.groupdict().get("parameters", "") or ""

    def parse_code_size_metrics(self, path: Path, text: str, config: CodeSizeThresholds) -> list[LanguageMetric]:
        method_warn = config.method_warn_lines
        method_max = config.method_max_lines
        type_warn = config.type_warn_lines
        type_max = config.type_max_lines
        file_warn = config.file_warn_lines
        file_max = config.file_max_lines
        metrics = [
            LanguageMetric("file", path.as_posix(), path.name, 1, max(1, len(text.splitlines())), max(1, len(text.splitlines())), file_warn, file_max)
        ]
        masked = mask_comments_and_strings(text, hash_comments=self.spec.hash_comments)
        if self.spec.indentation_based:
            for match in self._type_matches(masked):
                start = _line_number(text, match.start())
                indent = len(masked[match.start() :].split("\n", 1)[0]) - len(masked[match.start() :].split("\n", 1)[0].lstrip())
                end = _python_end_line(text, start, indent)
                metrics.append(LanguageMetric("type", path.as_posix(), f"class {match.group('name')}", start, end, end - start + 1, type_warn, type_max))
            for match in self._method_matches(masked):
                start = _line_number(text, match.start())
                line = masked[match.start() :].split("\n", 1)[0]
                indent = len(line) - len(line.lstrip())
                end = _python_end_line(text, start, indent)
                metrics.append(LanguageMetric("method", path.as_posix(), f"{match.group('name')}/{_parameter_count(self._match_parameters(match))}", start, end, end - start + 1, method_warn, method_max))
            return metrics

        for match in self._type_matches(masked):
            opening = masked.find("{", match.end() - 1)
            if opening < 0:
                continue
            start = _line_number(text, match.start())
            end = _line_number(text, _matching_brace(masked, opening))
            kind = match.groupdict().get("kind") or "class"
            metrics.append(LanguageMetric("type", path.as_posix(), f"{kind} {match.group('name')}", start, end, end - start + 1, type_warn, type_max))
        for match in self._method_matches(masked):
            if match.group("name") in CONTROL_WORDS:
                continue
            opening = masked.find("{", match.end() - 1)
            start = _line_number(text, match.start())
            if opening >= 0 and masked[match.end() - 1] == "{":
                end = _line_number(text, _matching_brace(masked, opening))
            else:
                end = start
            metrics.append(LanguageMetric("method", path.as_posix(), f"{match.group('name')}/{_parameter_count(self._match_parameters(match))}", start, end, end - start + 1, method_warn, method_max))
        return metrics

    def parse_complexity_metrics(self, path: str, text: str) -> list[ComplexityMetric]:
        masked = mask_comments_and_strings(text, hash_comments=self.spec.hash_comments)
        metrics: list[ComplexityMetric] = []
        if self.spec.indentation_based:
            matches = self._method_matches(masked)
            for match in matches:
                start = _line_number(text, match.start())
                line = masked[match.start() :].split("\n", 1)[0]
                indent = len(line) - len(line.lstrip())
                end = _python_end_line(text, start, indent)
                body = "\n".join(masked.splitlines()[start:end])
                metrics.append(_complexity_metric(path, match.group("name"), start, end, body, self._match_parameters(match)))
            return metrics
        for match in self._method_matches(masked):
            if match.group("name") in CONTROL_WORDS:
                continue
            start = _line_number(text, match.start())
            opening = masked.find("{", match.end() - 1)
            end_index = _matching_brace(masked, opening) if opening >= 0 and masked[match.end() - 1] == "{" else match.end()
            end = _line_number(text, end_index)
            body = masked[match.end() : end_index]
            metrics.append(_complexity_metric(path, match.group("name"), start, end, body, self._match_parameters(match)))
        return metrics

    def parse_type_declarations(self, path: Path, text: str) -> list[tuple[str, int]]:
        masked = mask_comments_and_strings(text, hash_comments=self.spec.hash_comments)
        return [
            (match.group("name"), _line_number(text, match.start()))
            for match in self._type_matches(masked)
        ]


def _parameter_count(parameters: str) -> int:
    return 0 if not parameters.strip() else parameters.count(",") + 1


def _complexity_metric(path: str, name: str, start: int, end: int, body: str, parameters: str) -> ComplexityMetric:
    decision_count = len(
        re.findall(
            r"\b(?:if|for|while|case|catch|when|elif|except|switch|select|match|loop)\b|&&|\|\||\?",
            body,
        )
    )
    complexity = 1 + decision_count
    cognitive = decision_count + len(
        re.findall(
            r"\b(?:if|for|while|case|catch|when|elif|except|switch|select|match|loop)\b",
            body,
        )
    )
    return ComplexityMetric(path, name, start, end, complexity, cognitive, _parameter_count(parameters))
