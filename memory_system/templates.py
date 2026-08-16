"""Deterministic Markdown templates and safe managed-block updates."""

from __future__ import annotations

from importlib import resources
from pathlib import PurePosixPath
import re
from string import Template

from .config import ProjectSpec, WorkspaceConfig


_MARKER = re.compile(r"<!-- memory-system:([A-Za-z0-9._:-]+):(start|end) -->")
_MARKER_LIKE = re.compile(r"<!--\s*memory-system.*?(?:-->|$)", re.DOTALL)
_UNMANAGED_HEADING = re.compile(r"^#{1,6}\s+.*memory", re.IGNORECASE | re.MULTILINE)
_UNMANAGED_NORM = re.compile(
    r"^[*-]?\s*.*(?:claude-mem|memory system)", re.IGNORECASE | re.MULTILINE
)


class ManagedBlockConflict(ValueError):
    """Raised when an authority file has conflicting memory instructions."""


def render_registry(config: WorkspaceConfig) -> str:
    """Render the workspace-wide routing registry."""
    rows = [_registry_row(config.root_identity, ".", config.root_state, config.root_authority)]
    rows.extend(
        _registry_row(project.identity, project.path, project.state, project.authority)
        for project in _sorted_projects(config)
    )
    return _render("project-registry.md", project_rows="\n".join(rows))


def render_active_index(config: WorkspaceConfig) -> str:
    """Render the deterministic index of all current state documents."""
    rows = [_state_row(config.root_identity, config.root_state)]
    rows.extend(_state_row(project.identity, project.state) for project in _sorted_projects(config))
    return _render("active-state-index.md", state_rows="\n".join(rows))


def render_root_state(config: WorkspaceConfig) -> str:
    """Render the root workspace state document."""
    return _render_state(config.root_identity, ".", config.root_authority)


def render_project_state(project: ProjectSpec) -> str:
    """Render a child project's state document."""
    return _render_state(project.identity, project.path, project.authority)


def render_root_protocol(config: WorkspaceConfig) -> str:
    """Render the workspace-root memory protocol."""
    return _render(
        "root-memory-protocol.md",
        root_identity=config.root_identity,
        root_state=_path(config.root_state),
    )


def render_project_protocol(project: ProjectSpec) -> str:
    """Render a child project's local memory protocol."""
    return _render(
        "project-memory-protocol.md",
        project_identity=project.identity,
        project_state=_path(project.state),
    )


def upsert_managed_block(existing: str, block_id: str, body: str) -> str:
    """Insert or replace one managed block without changing unowned bytes."""
    pairs = _validated_marker_pairs(existing)
    _reject_unmanaged_memory_content(_unmanaged_text(existing, pairs))
    pair = pairs.get(block_id)
    if pair is not None:
        start, end = pair
        return f"{existing[:start.end()]}\n{body}{existing[end.start():]}"

    separator = "" if not existing or existing.endswith("\n") else "\n"
    return f"{existing}{separator}{_start_marker(block_id)}\n{body}{_end_marker(block_id)}\n"


def remove_managed_block(existing: str, block_id: str) -> str:
    """Remove exactly one owned block while preserving all other bytes."""
    pairs = _validated_marker_pairs(existing)
    _reject_unmanaged_memory_content(_unmanaged_text(existing, pairs))
    pair = pairs.get(block_id)
    if pair is None:
        raise ManagedBlockConflict(f"managed block owner is not {block_id}")
    start, end = pair
    return existing[: start.start()] + existing[end.end() :]


def managed_block_digest(existing: str, block_id: str) -> str:
    """Return a digest of one exact managed block, excluding unowned bytes."""
    import hashlib

    pairs = _validated_marker_pairs(existing)
    pair = pairs.get(block_id)
    if pair is None:
        raise ManagedBlockConflict(f"managed block owner is not {block_id}")
    start, end = pair
    return hashlib.sha256(existing[start.start() : end.end()].encode("utf-8")).hexdigest()


def managed_block_ids(existing: str) -> tuple[str, ...]:
    """Return validated managed owners in source order."""
    pairs = _validated_marker_pairs(existing)
    return tuple(block_id for block_id, _pair in pairs.items())


def _render(name: str, **values: str) -> str:
    source = resources.files("memory_system").joinpath("templates", name).read_text(
        encoding="utf-8"
    )
    return Template(source).substitute(values)


def _render_state(
    identity: str, canonical_root: PurePosixPath | str, authority: tuple[PurePosixPath, ...]
) -> str:
    return _render(
        "project-state.md",
        identity=identity,
        canonical_root=_path(canonical_root),
        authority="<br>".join(_path(item) for item in authority),
    )


def _sorted_projects(config: WorkspaceConfig) -> tuple[ProjectSpec, ...]:
    return tuple(sorted(config.projects, key=lambda project: project.identity.casefold()))


def _registry_row(
    identity: str,
    path: PurePosixPath | str,
    state: PurePosixPath,
    authority: tuple[PurePosixPath, ...],
) -> str:
    return f"| {identity} | {_path(path)} | {_path(state)} | {'<br>'.join(_path(item) for item in authority)} |"


def _state_row(identity: str, state: PurePosixPath) -> str:
    return f"| {identity} | {_path(state)} |"


def _path(path: PurePosixPath | str) -> str:
    return path if isinstance(path, str) else path.as_posix()


def _validated_marker_pairs(existing: str) -> dict[str, tuple[re.Match[str], re.Match[str]]]:
    marker_comments = tuple(_MARKER_LIKE.finditer(existing))
    parsed_markers = tuple(_MARKER.finditer(existing))
    if len(marker_comments) != len(parsed_markers) or any(
        comment.group() != marker.group()
        for comment, marker in zip(marker_comments, parsed_markers, strict=True)
    ):
        raise ManagedBlockConflict("invalid managed-block marker")

    pairs: dict[str, tuple[re.Match[str], re.Match[str]]] = {}
    open_marker: re.Match[str] | None = None
    for marker in parsed_markers:
        block_id, side = marker.group(1), marker.group(2)
        if side == "start":
            # Blocks are intentionally flat: nesting and overlap make ownership
            # ambiguous and could cause an update to replace someone else's bytes.
            if open_marker is not None or block_id in pairs:
                raise ManagedBlockConflict("overlapping or duplicate managed-block marker")
            open_marker = marker
        else:
            if open_marker is None or open_marker.group(1) != block_id:
                raise ManagedBlockConflict("invalid managed-block marker")
            pairs[block_id] = (open_marker, marker)
            open_marker = None
    if open_marker is not None:
        raise ManagedBlockConflict("invalid managed-block marker")
    return pairs


def _unmanaged_text(
    existing: str, pairs: dict[str, tuple[re.Match[str], re.Match[str]]]
) -> str:
    spans = sorted((start.start(), end.end()) for start, end in pairs.values())
    unowned: list[str] = []
    cursor = 0
    for start, end in spans:
        unowned.append(existing[cursor:start])
        cursor = end
    unowned.append(existing[cursor:])
    return "".join(unowned)


def _reject_unmanaged_memory_content(text: str) -> None:
    if _UNMANAGED_HEADING.search(text) or _UNMANAGED_NORM.search(text):
        raise ManagedBlockConflict("unmanaged memory instructions")


def _start_marker(block_id: str) -> str:
    return f"<!-- memory-system:{block_id}:start -->"


def _end_marker(block_id: str) -> str:
    return f"<!-- memory-system:{block_id}:end -->"
