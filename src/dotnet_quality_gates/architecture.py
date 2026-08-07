from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from dotnet_quality_gates.quality.common import load_policy_object, policy_section, sanitize_string_list

DEFAULT_LAYER_RULES: dict[str, list[str]] = {
    "Domain": [],
    "Application": ["Domain"],
    "Infrastructure": ["Application", "Domain"],
    "Presentation": ["Application", "Domain", "Infrastructure"],
}

DEFAULT_INCLUDE_ROOTS = ["src"]
DEFAULT_EXCLUDE_GLOBS = [
    "**/*.Designer.cs",
    "**/*.g.cs",
    "**/*.g.i.cs",
    "**/AssemblyInfo.cs",
]


class ArchitectureConfigurationError(ValueError):
    """Raised when an architecture policy cannot be safely interpreted."""


@dataclass(frozen=True)
class ArchitectureUnit:
    """A named source area and the dependencies it is allowed to use."""

    name: str
    source_roots: tuple[str, ...]
    namespace_prefixes: tuple[str, ...]
    allowed_dependencies: frozenset[str]


@dataclass(frozen=True)
class ArchitectureConfig:
    """Resolved architecture rules shared by production and test checks."""

    units: tuple[ArchitectureUnit, ...]
    is_custom: bool = False

    @property
    def unit_names(self) -> frozenset[str]:
        return frozenset(unit.name for unit in self.units)

    def unit_for_path(self, path: Path, repo_root: Path) -> ArchitectureUnit | None:
        """Return the most specific configured unit containing ``path``."""
        absolute_path = path.resolve()
        matches: list[tuple[int, ArchitectureUnit]] = []
        for unit in self.units:
            for source_root in unit.source_roots:
                root = resolve_repo_path(repo_root, source_root)
                try:
                    absolute_path.relative_to(root)
                except ValueError:
                    continue
                matches.append((len(root.parts), unit))

        if not matches:
            return None

        deepest = max(depth for depth, _ in matches)
        deepest_units = {unit for depth, unit in matches if depth == deepest}
        if len(deepest_units) != 1:
            return None
        return next(iter(deepest_units))

    def unit_for_namespace(self, namespace: str) -> ArchitectureUnit | None:
        """Return the unit with the longest matching namespace prefix."""
        matches: list[tuple[int, ArchitectureUnit]] = []
        for unit in self.units:
            for prefix in unit.namespace_prefixes:
                if namespace == prefix or namespace.startswith(f"{prefix}."):
                    matches.append((len(prefix), unit))

        if not matches:
            return None

        longest = max(length for length, _ in matches)
        longest_units = {unit for length, unit in matches if length == longest}
        if len(longest_units) != 1:
            return None
        return next(iter(longest_units))

    def allowed_dependency_names(self, unit: ArchitectureUnit) -> frozenset[str]:
        return frozenset({unit.name, *unit.allowed_dependencies})


def load_architecture_config(policy_path: Path) -> ArchitectureConfig:
    """Load custom architecture units, or resolve the legacy Onion defaults."""
    policy = load_policy_object(policy_path, "architecture")
    architecture = policy_section(policy, "architecture")
    raw_units = architecture.get("units")

    if raw_units is None:
        boundaries = policy_section(policy, "architectural_boundaries")
        raw_units = boundaries.get("layer_rules", DEFAULT_LAYER_RULES)
        return ArchitectureConfig(
            units=_build_legacy_units(raw_units),
            is_custom=False,
        )

    return ArchitectureConfig(units=_parse_custom_units(raw_units), is_custom=True)


def architecture_from_layer_rules(raw_rules: object) -> ArchitectureConfig:
    """Build an architecture object for callers using the legacy API."""
    return ArchitectureConfig(units=_build_legacy_units(raw_rules), is_custom=False)


def validate_architecture_units(raw_units: object) -> None:
    """Validate a custom unit definition without resolving repository paths."""
    _parse_custom_units(raw_units)


def _build_legacy_units(raw_rules: object) -> tuple[ArchitectureUnit, ...]:
    rules = raw_rules if isinstance(raw_rules, dict) else DEFAULT_LAYER_RULES
    normalized_rules: dict[str, list[str]] = {}
    for name, dependencies in DEFAULT_LAYER_RULES.items():
        raw_dependencies = rules.get(name, dependencies) if isinstance(rules, dict) else dependencies
        normalized_rules[name] = [
            dependency
            for dependency in sanitize_string_list(raw_dependencies)
            if dependency in DEFAULT_LAYER_RULES and dependency != name
        ]

    return tuple(
        ArchitectureUnit(
            name=name,
            source_roots=(f"src/{name}",),
            namespace_prefixes=(name,),
            allowed_dependencies=frozenset(normalized_rules[name]),
        )
        for name in DEFAULT_LAYER_RULES
    )


def _parse_custom_units(raw_units: object) -> tuple[ArchitectureUnit, ...]:
    if not isinstance(raw_units, dict) or not raw_units:
        raise ArchitectureConfigurationError("architecture.units must be a non-empty object")

    units: list[ArchitectureUnit] = []
    seen_roots: dict[str, str] = {}
    seen_namespaces: dict[str, str] = {}

    for raw_name, raw_definition in raw_units.items():
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not name:
            raise ArchitectureConfigurationError("architecture.units names must be non-empty strings")
        if not isinstance(raw_definition, dict):
            raise ArchitectureConfigurationError(f"architecture.units.{name} must be an object")

        allowed_fields = {"source_roots", "namespace_prefixes", "allowed_dependencies"}
        unknown_fields = sorted(set(raw_definition) - allowed_fields)
        if unknown_fields:
            raise ArchitectureConfigurationError(
                f"architecture.units.{name} contains unknown key(s): {', '.join(unknown_fields)}"
            )

        source_roots = _required_string_list(raw_definition.get("source_roots"), f"architecture.units.{name}.source_roots")
        namespace_prefixes = sanitize_string_list(raw_definition.get("namespace_prefixes", [name]))
        if not namespace_prefixes:
            raise ArchitectureConfigurationError(
                f"architecture.units.{name}.namespace_prefixes must contain at least one namespace"
            )
        dependencies = sanitize_string_list(raw_definition.get("allowed_dependencies", []))

        for source_root in source_roots:
            normalized_root = normalize_repo_relative_path(source_root, f"architecture.units.{name}.source_roots")
            previous = seen_roots.get(normalized_root)
            if previous is not None and previous != name:
                raise ArchitectureConfigurationError(
                    f"architecture source root '{source_root}' is assigned to both '{previous}' and '{name}'"
                )
            seen_roots[normalized_root] = name

        for namespace_prefix in namespace_prefixes:
            normalized_prefix = normalize_namespace_prefix(
                namespace_prefix,
                f"architecture.units.{name}.namespace_prefixes",
            )
            previous = seen_namespaces.get(normalized_prefix)
            if previous is not None and previous != name:
                raise ArchitectureConfigurationError(
                    f"architecture namespace prefix '{namespace_prefix}' is assigned to both '{previous}' and '{name}'"
                )
            seen_namespaces[normalized_prefix] = name

        units.append(
            ArchitectureUnit(
                name=name,
                source_roots=tuple(
                    normalize_repo_relative_path(root, f"architecture.units.{name}.source_roots")
                    for root in source_roots
                ),
                namespace_prefixes=tuple(
                    normalize_namespace_prefix(prefix, f"architecture.units.{name}.namespace_prefixes")
                    for prefix in namespace_prefixes
                ),
                allowed_dependencies=frozenset(dependencies),
            )
        )

    names = {unit.name for unit in units}
    for unit in units:
        unknown = sorted(unit.allowed_dependencies - names)
        if unknown:
            raise ArchitectureConfigurationError(
                f"architecture.units.{unit.name}.allowed_dependencies contains unknown unit(s): {', '.join(unknown)}"
            )
        if unit.name in unit.allowed_dependencies:
            raise ArchitectureConfigurationError(
                f"architecture.units.{unit.name}.allowed_dependencies cannot contain itself"
            )

    return tuple(units)


def resolve_repo_path(repo_root: Path, relative_path: str) -> Path:
    return (repo_root / relative_path).resolve()


def normalize_repo_relative_path(value: str, field_name: str) -> str:
    path = Path(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ArchitectureConfigurationError(f"{field_name} must be a repository-relative path: '{value}'")
    normalized = "/".join(part for part in path.parts if part not in {"", "."})
    if not normalized:
        raise ArchitectureConfigurationError(f"{field_name} must contain a non-empty path")
    return normalized


def normalize_namespace_prefix(value: str, field_name: str) -> str:
    prefix = value.strip()
    segments = prefix.split(".")
    identifier_pattern = re.compile(r"^[A-Za-z_]\w*$")
    if not prefix or any(not identifier_pattern.fullmatch(segment) for segment in segments):
        raise ArchitectureConfigurationError(f"{field_name} contains an invalid namespace prefix: '{value}'")
    return prefix


def _required_string_list(value: object, field_name: str) -> list[str]:
    values = sanitize_string_list(value)
    if not values:
        raise ArchitectureConfigurationError(f"{field_name} must contain at least one value")
    return values
