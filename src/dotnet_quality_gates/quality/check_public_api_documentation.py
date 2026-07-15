from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from dotnet_quality_gates.quality.common import (  # noqa: E402
    is_repo_excluded,
    load_prefixed_baseline_violations,
    load_quality_section_config,
)
from dotnet_quality_gates.unit_test_conventions import REPO_ROOT, iter_cs_files  # noqa: E402

DEFAULT_POLICY_PATH = REPO_ROOT / ".quality" / "quality_policy.json"
DEFAULT_INCLUDE_ROOTS = ["src"]
DEFAULT_EXCLUDE_GLOBS = [
    "**/*.Designer.cs",
    "**/*.g.cs",
    "**/*.g.i.cs",
    "**/GlobalUsings.cs",
    "**/AssemblyInfo.cs",
]

TYPE_DECLARATION_PATTERN = re.compile(
    r"\b(class|interface|struct|record|enum|delegate)\s+([A-Za-z_]\w*)\b"
)
PARTIAL_TYPE_DECLARATION_PATTERN = re.compile(
    r"\bpartial\s+(?:class|interface|struct|record)\s+([A-Za-z_]\w*)\b"
)
METHOD_DECLARATION_CANDIDATE_PATTERN = re.compile(r"\b([A-Za-z_]\w*|this)\s*(?:<[^>]+>)?\(")
LINE_NUMBER_PATTERN = re.compile(r"(?P<path>.*?\.cs):\d+:\s+(?P<message>Public .*)")
PARTIAL_FILE_SUFFIX_PATTERN = re.compile(r"(?P<prefix>.*?)/(?P<type>[A-Za-z_]\w*)\.[^/]+\.cs:")
METHOD_NAME_EXCLUSIONS = {
    "base",
    "catch",
    "default",
    "for",
    "foreach",
    "if",
    "lock",
    "nameof",
    "new",
    "return",
    "sizeof",
    "static",
    "switch",
    "typeof",
    "using",
    "while",
}


@dataclass(frozen=True)
class TypeScope:
    brace_depth: int
    is_public_api: bool


def normalize_violation_text(text: str) -> str:
    return text.replace("\\", "/")


def canonicalize_violation_key(text: str) -> str:
    normalized = normalize_violation_text(text)
    line_match = LINE_NUMBER_PATTERN.fullmatch(normalized)
    if line_match:
        path = line_match.group("path")
        message = line_match.group("message")
    else:
        path = normalized
        message = ""

    path = PARTIAL_FILE_SUFFIX_PATTERN.sub(r"\g<prefix>/\g<type>.cs:", f"{path}:")[:-1]
    return f"{path}: {message}" if message else path


def load_public_api_documentation_config(policy_path: Path) -> tuple[list[str], list[str]]:
    return load_quality_section_config(
        policy_path,
        "public_api_documentation",
        DEFAULT_INCLUDE_ROOTS,
        DEFAULT_EXCLUDE_GLOBS,
        "public API documentation",
    )


def is_excluded(path: Path, exclude_globs: list[str]) -> bool:
    return is_repo_excluded(path, exclude_globs, REPO_ROOT)


def load_baseline_violations(path: Path) -> set[str]:
    return load_prefixed_baseline_violations(path, canonicalize_violation_key)


def collect_declaration_signature(lines: list[str], start_index: int) -> str:
    parts: list[str] = []
    paren_depth = 0
    bracket_depth = 0

    for index in range(start_index, min(len(lines), start_index + 12)):
        line = lines[index].strip()
        if line.startswith("//"):
            continue

        parts.append(line)
        paren_depth += line.count("(") - line.count(")")
        bracket_depth += line.count("<") - line.count(">")

        if paren_depth <= 0 and bracket_depth <= 0 and (
            "{" in line or line.endswith(";") or "=>" in line
        ):
            break

    return " ".join(parts)


def classify_public_declaration(signature: str) -> tuple[str, str] | None:
    if not signature or not signature.startswith("public "):
        return None

    type_match = TYPE_DECLARATION_PATTERN.search(signature)
    if type_match:
        kind = type_match.group(1)
        name = type_match.group(2)
        return kind, name

    if "(" in signature and ")" in signature:
        for method_name_match in METHOD_DECLARATION_CANDIDATE_PATTERN.finditer(signature):
            name = method_name_match.group(1)
            prefix = signature[: method_name_match.start(1)]
            if "{" in prefix or name in METHOD_NAME_EXCLUSIONS:
                continue
            return "method", name

    event_match = re.search(r"\bevent\b[^;{=]*\b([A-Za-z_]\w*)\s*(?:;|{|=)", signature)
    if event_match:
        return "event", event_match.group(1)

    member_prefix = signature
    for separator in ("{", "=>", ";"):
        if separator in member_prefix:
            member_prefix = member_prefix.split(separator, 1)[0]
    member_name_match = re.search(r"([A-Za-z_]\w*)\s*$", member_prefix)
    if member_name_match:
        return "member", member_name_match.group(1)

    return None


def extract_xml_doc_block(lines: list[str], declaration_line_index: int) -> str:
    index = declaration_line_index - 1
    collected: list[str] = []

    while index >= 0:
        stripped = lines[index].strip()
        if stripped == "":
            break
        if stripped.startswith("///"):
            collected.append(stripped[3:].strip())
            index -= 1
            continue
        if stripped.startswith("[") or stripped.endswith("]"):
            index -= 1
            continue
        break

    return "\n".join(reversed(collected))


def has_valid_summary(xml_doc: str) -> bool:
    if not xml_doc:
        return False

    if re.search(r"<\s*inheritdoc\b", xml_doc, flags=re.IGNORECASE):
        return True

    summary_match = re.search(
        r"<\s*summary\s*>(?P<content>.*?)<\s*/\s*summary\s*>",
        xml_doc,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not summary_match:
        return False

    content = re.sub(r"<[^>]+>", "", summary_match.group("content"))
    return bool(content.strip())


def is_partial_type_signature(signature: str) -> bool:
    return bool(PARTIAL_TYPE_DECLARATION_PATTERN.search(signature))


def collect_documented_public_partial_types(
    include_roots: list[Path],
    exclude_globs: list[str],
) -> set[str]:
    documented_partial_types: set[str] = set()

    for include_root in include_roots:
        for file_path in iter_cs_files(include_root):
            if is_excluded(file_path, exclude_globs):
                continue

            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            for line_index, line in enumerate(lines):
                stripped = line.strip()
                if not stripped.startswith("public "):
                    continue

                signature = collect_declaration_signature(lines, line_index)
                partial_match = PARTIAL_TYPE_DECLARATION_PATTERN.search(signature)
                if partial_match is None:
                    continue

                xml_doc = extract_xml_doc_block(lines, line_index)
                if has_valid_summary(xml_doc):
                    documented_partial_types.add(partial_match.group(1))

    return documented_partial_types


def is_public_api_context(type_stack: list[TypeScope]) -> bool:
    return all(scope.is_public_api for scope in type_stack)


def declaration_introduces_type_scope(signature: str) -> bool:
    return TYPE_DECLARATION_PATTERN.search(signature) is not None


def count_braces(line: str) -> int:
    return line.count("{") - line.count("}")


def validate_public_api_documentation(
    include_roots: list[Path],
    exclude_globs: list[str],
) -> list[str]:
    violations: list[str] = []
    documented_partial_types = collect_documented_public_partial_types(include_roots, exclude_globs)

    for include_root in include_roots:
        for file_path in iter_cs_files(include_root):
            if is_excluded(file_path, exclude_globs):
                continue

            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            brace_depth = 0
            type_stack: list[TypeScope] = []
            pending_type_scope: TypeScope | None = None
            for line_index, line in enumerate(lines):
                stripped = line.strip()
                while type_stack and brace_depth <= type_stack[-1].brace_depth:
                    type_stack.pop()

                if pending_type_scope is not None and "{" in line:
                    type_stack.append(pending_type_scope)
                    pending_type_scope = None

                if stripped.startswith(("public ", "internal ", "private ", "protected ")):
                    signature = collect_declaration_signature(lines, line_index)
                    classified = classify_public_declaration(signature)

                    if declaration_introduces_type_scope(signature):
                        type_is_public_api = bool(
                            classified is not None
                            and classified[0] in {"class", "interface", "struct", "record", "enum", "delegate"}
                            and is_public_api_context(type_stack)
                        )
                        new_scope = TypeScope(brace_depth=brace_depth, is_public_api=type_is_public_api)
                        if "{" in stripped:
                            type_stack.append(new_scope)
                        else:
                            pending_type_scope = new_scope

                    if stripped.startswith("public ") and not stripped.startswith("public namespace"):
                        if classified is not None and is_public_api_context(type_stack):
                            kind, name = classified
                            xml_doc = extract_xml_doc_block(lines, line_index)
                            if not has_valid_summary(xml_doc) and not (
                                kind in {"class", "interface", "struct", "record"}
                                and is_partial_type_signature(signature)
                                and name in documented_partial_types
                            ):
                                relative_path = file_path.relative_to(REPO_ROOT).as_posix()
                                violations.append(
                                    f"{relative_path}:{line_index + 1}: "
                                    f"Public {kind} '{name}' is missing XML documentation summary."
                                )

                brace_depth += count_braces(line)

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate XML documentation for public API declarations."
    )
    parser.add_argument(
        "--policy-path",
        default=str(DEFAULT_POLICY_PATH),
        help="Path to code quality policy JSON.",
    )
    parser.add_argument(
        "--baseline-path",
        default=str(REPO_ROOT / ".quality" / "baselines" / "public_api_documentation_baseline.txt"),
        help="Path to a baseline file with one known violation per line prefixed by '- '.",
    )
    parser.add_argument(
        "--max-violations",
        type=int,
        default=250,
        help="Maximum number of violations to print before truncating output.",
    )
    args = parser.parse_args()

    include_root_texts, exclude_globs = load_public_api_documentation_config(Path(args.policy_path))
    include_roots: list[Path] = []
    for include_root_text in include_root_texts:
        include_root = (REPO_ROOT / include_root_text).resolve()
        if include_root.exists():
            include_roots.append(include_root)
        else:
            print(f"Warning: include root not found and skipped: {include_root_text}", file=sys.stderr)

    if not include_roots:
        print("Public API documentation check failed: no valid include roots found.", file=sys.stderr)
        return 1

    violations = validate_public_api_documentation(
        include_roots=include_roots,
        exclude_globs=exclude_globs,
    )

    baseline_violations = load_baseline_violations(Path(args.baseline_path))
    if baseline_violations:
        violations = [
            violation
            for violation in violations
            if canonicalize_violation_key(violation) not in baseline_violations
        ]

    if violations:
        print("Public API documentation check failed.", file=sys.stderr)
        displayed = violations[: args.max_violations]
        for violation in displayed:
            print(f" - {violation}", file=sys.stderr)
        if len(violations) > len(displayed):
            remaining = len(violations) - len(displayed)
            print(f" - ... {remaining} additional violations omitted", file=sys.stderr)
        return 1

    print("Public API documentation check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
