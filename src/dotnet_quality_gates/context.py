from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PARSER_MODES = ("auto", "python", "roslyn")
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300.0


def _resolve_path(value: str | os.PathLike[str], base: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


@dataclass(frozen=True)
class ExecutionContext:
    """Resolved process-wide settings shared by all quality commands."""

    repo_root: Path
    policy_path: Path
    parser_mode: str = "auto"
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> ExecutionContext:
        repo_root = Path(os.environ.get("DOTNET_QUALITY_REPO_ROOT", Path.cwd())).resolve()
        policy_path = _resolve_path(
            os.environ.get("DOTNET_QUALITY_POLICY_PATH", ".quality/quality_policy.json"),
            repo_root,
        ).resolve()
        parser_mode = os.environ.get("DOTNET_QUALITY_PARSER", "auto").strip().lower() or "auto"
        if parser_mode not in PARSER_MODES:
            parser_mode = "auto"
        try:
            timeout = float(os.environ.get("DOTNET_QUALITY_COMMAND_TIMEOUT", DEFAULT_COMMAND_TIMEOUT_SECONDS))
        except ValueError:
            timeout = DEFAULT_COMMAND_TIMEOUT_SECONDS
        return cls(
            repo_root=repo_root,
            policy_path=policy_path,
            parser_mode=parser_mode,
            command_timeout_seconds=max(1.0, timeout),
        )

    def child_environment(self, environment: dict[str, str] | None = None) -> dict[str, str]:
        child = dict(environment or os.environ)
        child["DOTNET_QUALITY_REPO_ROOT"] = str(self.repo_root)
        child["DOTNET_QUALITY_POLICY_PATH"] = str(self.policy_path)
        child["DOTNET_QUALITY_PARSER"] = self.parser_mode
        child["DOTNET_QUALITY_COMMAND_TIMEOUT"] = str(self.command_timeout_seconds)
        return child


def current_context() -> ExecutionContext:
    return ExecutionContext.from_environment()


def resolve_command_path(value: str | os.PathLike[str], repo_root: Path | None = None) -> Path:
    root = repo_root or current_context().repo_root
    return _resolve_path(value, root).resolve()
