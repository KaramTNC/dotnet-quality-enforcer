from __future__ import annotations

import re


def mask_comments_and_strings(text: str, *, hash_comments: bool = False) -> str:
    """Replace comments and literals with spaces while preserving newlines."""
    tokens = [
        r"//[^\n]*",
        r"/\*[\s\S]*?(?:\*/|$)",
        r"\"\"\"[\s\S]*?(?:\"\"\"|$)",
        r"(?:@\$|\$@|@|\$)?\"(?:\\.|\"\"|[^\"\\])*\"",
        r"'(?:\\.|[^'\\\n])*'",
        r"`[^`\n]*(?:`|$)",
    ]
    if hash_comments:
        tokens.insert(2, r"\#[^\n]*")
    pattern = re.compile("|".join(f"(?:{token})" for token in tokens))
    return pattern.sub(lambda match: "".join("\n" if char == "\n" else " " for char in match.group()), text)
