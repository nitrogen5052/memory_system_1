"""Strict, local-only configuration loading for portable project memory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import tomllib
from typing import Any


_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
_STATE_ROOT = PurePosixPath("_memory/Context/projects")


class ConfigError(ValueError):
    """Raised when a portable memory configuration violates its contract."""


@dataclass(frozen=True)
class ProjectSpec:
    identity: str
    path: PurePosixPath
    state: PurePosixPath
    authority: tuple[PurePosixPath, ...]


@dataclass(frozen=True)
class WorkspaceConfig:
    schema_version: int
    methodology_version: str
    root_identity: str
    root_state: PurePosixPath
    root_authority: tuple[PurePosixPath, ...]
    projects: tuple[ProjectSpec, ...]


@dataclass(frozen=True)
class Compatibility:
    schema_version: int
    methodology_version: str
    minimum_claude_mem_version: str
    tested_claude_mem_versions: tuple[str, ...]
    default_worker_port: int


def load_compatibility(path: Path) -> Compatibility:
    """Load the local compatibility policy from *path*."""
    data = _read_toml(path)
    claude_mem = _table(data, "claude_mem")
    return Compatibility(
        schema_version=_integer(data, "schema_version"),
        methodology_version=_string(data, "methodology_version"),
        minimum_claude_mem_version=_string(claude_mem, "minimum_version"),
        tested_claude_mem_versions=tuple(_strings(claude_mem, "tested_versions")),
        default_worker_port=_integer(claude_mem, "default_worker_port"),
    )


def load_workspace_config(
    workspace: Path, config_path: Path | None = None
) -> WorkspaceConfig:
    """Load and validate a workspace manifest without accessing any network service."""
    try:
        workspace_root = workspace.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ConfigError(f"workspace does not exist: {workspace}") from exc
    if not workspace_root.is_dir():
        raise ConfigError(f"workspace is not a directory: {workspace}")

    manifest_path = config_path or workspace_root / "memory-system.toml"
    data = _read_toml(manifest_path)
    schema_version = _integer(data, "schema_version")
    methodology_version = _string(data, "methodology_version")

    root = _table(data, "workspace")
    root_identity = _identity(root, "root_identity")
    root_state = _state(root, "state")
    root_authority = _authorities(root, "authority")
    project_tables = data.get("projects", [])
    if not isinstance(project_tables, list):
        raise ConfigError("projects must be an array of tables")
    projects = tuple(_project(project) for project in project_tables)

    _validate_unique_identities(root_identity, projects)
    _validate_unique_paths("state", (root_state, *(project.state for project in projects)))
    _validate_unique_paths(
        "authority",
        (*root_authority, *(authority for project in projects for authority in project.authority)),
    )
    _validate_project_roots(projects)
    _validate_authority_scopes(root_authority, projects)
    compatibility = load_compatibility(workspace_root / "compatibility.toml")
    if schema_version != compatibility.schema_version:
        raise ConfigError("unsupported schema version")
    if methodology_version != compatibility.methodology_version:
        raise ConfigError("unsupported methodology version")
    _validate_existing_paths(workspace_root, root_state, projects, root_authority)

    return WorkspaceConfig(
        schema_version=schema_version,
        methodology_version=methodology_version,
        root_identity=root_identity,
        root_state=root_state,
        root_authority=root_authority,
        projects=projects,
    )


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as source:
            data = tomllib.load(source)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file does not exist: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"configuration root must be a table: {path}")
    return data


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"missing or invalid [{key}] table")
    return value


def _string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ConfigError(f"missing or invalid {key}")
    return value


def _integer(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"missing or invalid {key}")
    return value


def _strings(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"missing or invalid {key}")
    return value


def _identity(data: dict[str, Any], key: str) -> str:
    value = _string(data, key)
    if not _IDENTITY.fullmatch(value):
        raise ConfigError(f"invalid identity: {value}")
    return value


def _path(data: dict[str, Any], key: str) -> PurePosixPath:
    raw = _string(data, key)
    path = PurePosixPath(raw)
    if path.is_absolute():
        raise ConfigError(f"absolute paths are not allowed: {raw}")
    if ".." in path.parts:
        raise ConfigError(f".. is not allowed in paths: {raw}")
    return path


def _state(data: dict[str, Any], key: str) -> PurePosixPath:
    path = _path(data, key)
    if not _is_under(path, _STATE_ROOT):
        raise ConfigError("state paths must be under _memory/Context/projects")
    return path


def _authorities(data: dict[str, Any], key: str) -> tuple[PurePosixPath, ...]:
    values = _strings(data, key)
    if not values:
        raise ConfigError(f"{key} must not be empty")
    return tuple(_path({key: value}, key) for value in values)


def _project(data: Any) -> ProjectSpec:
    if not isinstance(data, dict):
        raise ConfigError("each project must be a table")
    return ProjectSpec(
        identity=_identity(data, "identity"),
        path=_path(data, "path"),
        state=_state(data, "state"),
        authority=_authorities(data, "authority"),
    )


def _validate_unique_identities(root_identity: str, projects: tuple[ProjectSpec, ...]) -> None:
    identities = (root_identity, *(project.identity for project in projects))
    if len({identity.casefold() for identity in identities}) != len(identities):
        raise ConfigError("duplicate identity")


def _validate_unique_paths(kind: str, paths: tuple[PurePosixPath, ...]) -> None:
    canonical = [path.as_posix().casefold() for path in paths]
    if len(set(canonical)) != len(canonical):
        raise ConfigError(f"duplicate {kind}")


def _validate_project_roots(projects: tuple[ProjectSpec, ...]) -> None:
    for index, project in enumerate(projects):
        for other in projects[index + 1 :]:
            if project.path == other.path:
                raise ConfigError("duplicate project root")
            if _is_under(project.path, other.path) or _is_under(other.path, project.path):
                raise ConfigError("project roots overlap")


def _validate_authority_scopes(
    root_authority: tuple[PurePosixPath, ...], projects: tuple[ProjectSpec, ...]
) -> None:
    for authority in root_authority:
        if any(_is_under(authority, project.path) for project in projects):
            raise ConfigError("root authority is inside a child root")
    for project in projects:
        for authority in project.authority:
            if not _is_under(authority, project.path):
                raise ConfigError("child authority is outside its declared project root")


def _validate_existing_paths(
    workspace: Path,
    root_state: PurePosixPath,
    projects: tuple[ProjectSpec, ...],
    root_authority: tuple[PurePosixPath, ...],
) -> None:
    resolved_roots = []
    for project in projects:
        resolved_roots.append(_resolve_contained(workspace, project.path, "project root"))
    if len(set(resolved_roots)) != len(resolved_roots):
        raise ConfigError("symlink alias for project root")
    for state in (root_state, *(project.state for project in projects)):
        _resolve_contained(workspace, state, "state", require_exists=False)
    for authority in (*root_authority, *(item for project in projects for item in project.authority)):
        _resolve_contained(workspace, authority, "authority")


def _resolve_contained(
    workspace: Path, path: PurePosixPath, kind: str, *, require_exists: bool = True
) -> Path:
    expected = workspace.joinpath(*path.parts)
    try:
        resolved = expected.resolve(strict=require_exists)
    except FileNotFoundError as exc:
        raise ConfigError(f"{kind} does not exist: {path}") from exc
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ConfigError(f"{kind} escapes the workspace: {path}") from exc
    if resolved != expected:
        raise ConfigError(f"{kind} uses a symlink alias: {path}")
    return resolved


def _is_under(path: PurePosixPath, root: PurePosixPath) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
