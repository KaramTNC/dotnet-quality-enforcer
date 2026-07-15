from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(os.environ.get("DOTNET_QUALITY_REPO_ROOT", Path.cwd())).resolve()
DEFAULT_POLICY_PATH = REPO_ROOT / ".quality" / "quality_policy.json"
DEFAULT_SRC_ROOT = REPO_ROOT / "src"
DEFAULT_UNIT_TEST_ROOT = REPO_ROOT / "tests"
DEFAULT_SOURCE_INCLUDE_ROOTS = ["src"]

SKIP_DIR_NAMES = {
    ".git",
    ".idea",
    ".vs",
    "bin",
    "obj",
    "TestResults",
}

CLASS_DECLARATION_PATTERN = re.compile(
    r"\b(?:public|protected|internal|private|file)?\s*"
    r"(?:(?:abstract|sealed|static|partial)\s+)*"
    r"class\s+([A-Za-z_]\w*)\b"
)

SOURCE_TYPE_DECLARATION_PATTERN = re.compile(
    r"\b(?:public|protected|internal|private|file)?\s*"
    r"(?:(?:abstract|sealed|static|partial|readonly|record)\s+)*"
    r"(class|interface|struct|record(?:\s+class|\s+struct)?|enum)\s+([A-Za-z_]\w*)\b"
)

METHOD_DECLARATION_PATTERN = re.compile(
    r"^\s*"
    r"(?:public|protected(?:\s+internal)?|internal(?:\s+protected)?|private)\s+"
    r"(?:(?:new|static|virtual|abstract|override|sealed|extern|unsafe|async|partial)\s+)*"
    r".+?\s+"
    r"([A-Za-z_]\w*)(?:\s*<[^>]+>)?\s*\("
)

EXPOSED_METHOD_DECLARATION_PATTERN = re.compile(
    r"^\s*"
    r"(?:public|protected(?:\s+internal)?|internal(?:\s+protected)?)\s+"
    r"(?:(?:new|static|virtual|abstract|override|sealed|extern|unsafe|async|partial)\s+)*"
    r".+?\s+"
    r"([A-Za-z_]\w*)(?:\s*<[^>]+>)?\s*\("
)

EXPOSED_PROPERTY_DECLARATION_PATTERN = re.compile(
    r"^\s*"
    r"(?:public|protected(?:\s+internal)?|internal(?:\s+protected)?)\s+"
    r"(?:(?:new|static|virtual|abstract|override|sealed|extern|unsafe|required)\s+)*"
    r"[A-Za-z_][\w<>,\.\?\[\]\s]*\s+"
    r"([A-Za-z_]\w*)\s*(?:\{|=>)"
)

TARGETABLE_PROPERTY_DECLARATION_PATTERN = re.compile(
    r"^\s*"
    r"(?:public|protected(?:\s+internal)?|internal(?:\s+protected)?|private)\s+"
    r"(?:(?:new|static|virtual|abstract|override|sealed|extern|unsafe|required)\s+)*"
    r"[A-Za-z_][\w<>,\.\?\[\]\s]*\s+"
    r"([A-Za-z_]\w*)\s*(?:\{|=>)"
)

TARGETABLE_EVENT_DECLARATION_PATTERN = re.compile(
    r"^\s*"
    r"(?:public|protected(?:\s+internal)?|internal(?:\s+protected)?|private)\s+"
    r"(?:(?:new|static|virtual|abstract|override|sealed|extern|unsafe)\s+)*"
    r"event\s+[A-Za-z_][\w<>,\.\?\[\]\s]*\s+"
    r"([A-Za-z_]\w*)\s*(?:;|\{|=>)"
)

TEST_ATTRIBUTE_PATTERN = re.compile(r"\[(?:Fact|Theory|SkippableFact|Test|TestMethod)\b")
REGION_METHOD_PATTERN = re.compile(r"^([A-Za-z_]\w*)\s+Tests$")
