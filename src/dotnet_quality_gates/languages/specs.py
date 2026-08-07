import re
from dataclasses import dataclass

CONTROL_WORDS = {"if", "for", "while", "switch", "catch", "when", "try", "else", "do"}
_TYPE_RE = re.compile(
    r"\b(?:(?:data|sealed|abstract|final|public|private|protected|internal|open)\s+)*"
    r"(?P<kind>class|interface|enum|record|object|struct)\s+(?P<name>[A-Za-z_]\w*)"
)
_JAVA_KOTLIN_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|final|abstract|open|override|"
    r"suspend|inline|operator|tailrec|synchronized|native|default|fun)\s+)*"
    r"(?:(?:[A-Za-z_]\w*|[A-Za-z_]\w*\s*[<>,.?\[\]]*)\s+)*"
    r"(?P<name>[A-Za-z_]\w*)\s*\((?P<parameters>[^;{}()]*)\)\s*(?::\s*[^={]+)?\s*(?P<body>[{=])",
    re.MULTILINE,
)
_PYTHON_METHOD_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<parameters>[^)]*)\)",
    re.MULTILINE,
)
_JS_TYPE_RE = re.compile(
    r"(?m)^\s*(?:(?:export|declare|abstract|public|private)\s+)*"
    r"(?P<kind>class|interface|enum|type)\s+(?P<name>[A-Za-z_]\w*)"
)
_JS_METHOD_RE = re.compile(
    r"(?m)^\s*(?:(?:export|public|private|protected|static|async|get|set|function)\s+)*"
    r"(?P<name>[A-Za-z_]\w*)\s*\((?P<parameters>[^;{}()]*)\)\s*(?::\s*[^={]+)?\s*(?P<body>[{=])"
)
_JS_ARROW_RE = re.compile(
    r"(?m)^\s*(?:const|let|var)\s+(?P<name>[A-Za-z_]\w*)\s*=\s*(?:async\s*)?"
    r"\((?P<parameters>[^)]*)\)\s*=>\s*(?P<body>\{)"
)
_GO_TYPE_RE = re.compile(
    r"(?m)^\s*type\s+(?P<name>[A-Za-z_]\w*)\s+(?P<kind>struct|interface)\s*\{"
)
_GO_METHOD_RE = re.compile(
    r"(?m)^\s*func\s*(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)\s*"
    r"\((?P<parameters>[^)]*)\)[^{]*\{"
)
_RUST_TYPE_RE = re.compile(
    r"(?m)^\s*(?:(?:pub|crate|async|unsafe)\s+)*(?P<kind>struct|enum|trait|impl)\s+"
    r"(?P<name>[A-Za-z_]\w*)[^\{]*\{"
)
_RUST_METHOD_RE = re.compile(
    r"(?m)^\s*(?:(?:pub|async|const|unsafe|extern)\s+)*fn\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*\((?P<parameters>[^)]*)\)[^{]*\{"
)
_PYTHON_TYPE_RE = re.compile(r"(?m)^\s*class\s+(?P<name>[A-Za-z_]\w*)")


@dataclass(frozen=True)
class RegexLanguageSpec:
    """Syntax patterns needed by the dependency-free generic adapter."""

    language: str
    display_name: str
    extensions: frozenset[str]
    type_patterns: tuple[re.Pattern[str], ...]
    method_patterns: tuple[re.Pattern[str], ...]
    indentation_based: bool = False
    hash_comments: bool = False


PYTHON_SPEC = RegexLanguageSpec(
    language="python",
    display_name="Python",
    extensions=frozenset({".py"}),
    type_patterns=(_PYTHON_TYPE_RE,),
    method_patterns=(_PYTHON_METHOD_RE,),
    indentation_based=True,
    hash_comments=True,
)
JAVA_SPEC = RegexLanguageSpec(
    language="java",
    display_name="Java",
    extensions=frozenset({".java"}),
    type_patterns=(_TYPE_RE,),
    method_patterns=(_JAVA_KOTLIN_METHOD_RE,),
)
KOTLIN_SPEC = RegexLanguageSpec(
    language="kotlin",
    display_name="Kotlin",
    extensions=frozenset({".kt", ".kts"}),
    type_patterns=(_TYPE_RE,),
    method_patterns=(_JAVA_KOTLIN_METHOD_RE,),
)
TYPESCRIPT_SPEC = RegexLanguageSpec(
    language="typescript",
    display_name="TypeScript",
    extensions=frozenset({".ts", ".tsx"}),
    type_patterns=(_JS_TYPE_RE,),
    method_patterns=(_JS_METHOD_RE, _JS_ARROW_RE),
)
JAVASCRIPT_SPEC = RegexLanguageSpec(
    language="javascript",
    display_name="JavaScript",
    extensions=frozenset({".js", ".jsx", ".mjs", ".cjs"}),
    type_patterns=(_JS_TYPE_RE,),
    method_patterns=(_JS_METHOD_RE, _JS_ARROW_RE),
)
GO_SPEC = RegexLanguageSpec(
    language="go",
    display_name="Go",
    extensions=frozenset({".go"}),
    type_patterns=(_GO_TYPE_RE,),
    method_patterns=(_GO_METHOD_RE,),
)
RUST_SPEC = RegexLanguageSpec(
    language="rust",
    display_name="Rust",
    extensions=frozenset({".rs"}),
    type_patterns=(_RUST_TYPE_RE,),
    method_patterns=(_RUST_METHOD_RE,),
)
