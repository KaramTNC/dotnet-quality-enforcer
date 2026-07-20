from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

PARSER_MODES = ("auto", "python", "roslyn")
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300.0
DEFAULT_POLICY_RELATIVE_PATH = ".quality/quality_policy.json"


def _resolve_path(value: str | os.PathLike[str], base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def normalize_parser_mode(value: str, *, invalid: str = "auto") -> str:
    """Normalize a parser mode from configuration or environment."""
    selected = value.strip().lower() or invalid
    if selected not in PARSER_MODES:
        return invalid
    return selected


def _parse_timeout(value: str | None) -> float:
    try:
        timeout = float(value or DEFAULT_COMMAND_TIMEOUT_SECONDS)
    except ValueError:
        timeout = DEFAULT_COMMAND_TIMEOUT_SECONDS
    if not isfinite(timeout):
        timeout = DEFAULT_COMMAND_TIMEOUT_SECONDS
    return max(1.0, timeout)


@dataclass(frozen=True)
class ExecutionContext:
    """Resolved process-wide settings shared by all quality commands."""

    repo_root: Path
    policy_path: Path
    parser_mode: str = "auto"
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        cwd: Path | None = None,
    ) -> ExecutionContext:
        """Resolve settings from a supplied environment and working directory.

        Reading both inputs at call time keeps library users from inheriting a
        repository path captured when a command module was imported, while the
        optional arguments make resolution deterministic in tests and callers
        that manage their own process context.
        """
        environment = os.environ if environment is None else environment
        working_directory = (cwd or Path.cwd()).expanduser().resolve()
        repo_root = _resolve_path(environment.get("DOTNET_QUALITY_REPO_ROOT", working_directory), working_directory).resolve()
        policy_path = _resolve_path(
            environment.get("DOTNET_QUALITY_POLICY_PATH", DEFAULT_POLICY_RELATIVE_PATH),
            repo_root,
        ).resolve()
        parser_mode = normalize_parser_mode(environment.get("DOTNET_QUALITY_PARSER", "auto"))
        return cls(
            repo_root=repo_root,
            policy_path=policy_path,
            parser_mode=parser_mode,
            command_timeout_seconds=_parse_timeout(environment.get("DOTNET_QUALITY_COMMAND_TIMEOUT")),
        )

    def child_environment(self, environment: Mapping[str, str] | None = None) -> dict[str, str]:
        child = dict(os.environ if environment is None else environment)
        child["DOTNET_QUALITY_REPO_ROOT"] = str(self.repo_root)
        child["DOTNET_QUALITY_POLICY_PATH"] = str(self.policy_path)
        child["DOTNET_QUALITY_PARSER"] = self.parser_mode
        child["DOTNET_QUALITY_COMMAND_TIMEOUT"] = str(self.command_timeout_seconds)
        return child

    def resolve_path(self, value: str | os.PathLike[str]) -> Path:
        """Resolve a path against this context's repository root."""
        return _resolve_path(value, self.repo_root).resolve()


def current_context(
    environment: Mapping[str, str] | None = None,
    *,
    cwd: Path | None = None,
) -> ExecutionContext:
    return ExecutionContext.from_environment(environment, cwd=cwd)


def resolve_command_path(value: str | os.PathLike[str], repo_root: Path | None = None) -> Path:
    if repo_root is None:
        return current_context().resolve_path(value)
    return _resolve_path(value, repo_root).resolve()
