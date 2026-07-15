from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .context import current_context


def command_timeout() -> float:
    return current_context().command_timeout_seconds


def run_command(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    capture_output: bool = True,
    check: bool = False,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run an external tool with a bounded timeout and a useful command shape."""
    return subprocess.run(
        [os.fspath(part) for part in command],
        cwd=cwd,
        capture_output=capture_output,
        check=check,
        text=text,
        timeout=command_timeout(),
    )
