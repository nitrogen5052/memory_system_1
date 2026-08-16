"""Deterministic Markdown templates and safe managed-block updates."""

from __future__ import annotations

from importlib import resources
from pathlib import PurePosixPath
import re
from string import Template

from .config import ProjectSpec, WorkspaceConfig


_MARKER = re.compile(r"<!-- memory-system:([A-Za-z0-9._:-]+):(start|end) -->")
_MARKER_LIKE = re.compile(r"<!--\s*memory-system(?::|\s|-->|$).*?(?:-->|$)", re.DOTALL)
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
    return _render_state(config.root_identity, config.root_state, config.root_authority)


def render_project_state(project: ProjectSpec) -> str:
    """Render a child project's state document."""
    return _render_state(project.identity, project.state, project.authority)


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


def _render(name: str, **values: str) -> str:
    source = resources.files("memory_system").joinpath("templates", name).read_text(
        encoding="utf-8"
    )
    return Template(source).substitute(values)


def _render_state(
    identity: str, state: PurePosixPath, authority: tuple[PurePosixPath, ...]
) -> str:
    return _render(
        "project-state.md",
        identity=identity,
        state=_path(state),
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

    markers: dict[str, dict[str, list[re.Match[str]]]] = {}
    for marker in parsed_markers:
        markers.setdefault(marker.group(1), {"start": [], "end": []})[marker.group(2)].append(marker)

    pairs = {}
    for block_id, sides in markers.items():
        starts, ends = sides["start"], sides["end"]
        if len(starts) != 1 or len(ends) != 1 or starts[0].start() > ends[0].start():
            raise ManagedBlockConflict("invalid managed-block marker")
        pairs[block_id] = (starts[0], ends[0])
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
