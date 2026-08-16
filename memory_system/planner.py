"""Read-only, deterministic deployment planning for portable project memory."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path, PurePosixPath

from .config import WorkspaceConfig, load_workspace_config
from .templates import (
    ManagedBlockConflict,
    render_active_index,
    render_project_protocol,
    render_project_state,
    render_registry,
    render_root_protocol,
    render_root_state,
    upsert_managed_block,
)


class ChangeKind(StrEnum):
    CREATE = "create"
    ADOPT_EXISTING = "adopt-existing"
    UPDATE_MANAGED_BLOCK = "update-managed-block"
    UPDATE_GENERATED = "update-generated"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class PlannedChange:
    path: PurePosixPath
    kind: ChangeKind
    before_sha256: str | None
    after_sha256: str | None
    content: str | None
    reason: str


@dataclass(frozen=True)
class Plan:
    workspace: Path
    changes: tuple[PlannedChange, ...]
    conflicts: tuple[PlannedChange, ...]


def build_plan(workspace: Path, config_path: Path | None = None) -> Plan:
    """Return the changes needed to install memory artifacts without writing them."""
    workspace = workspace.resolve(strict=True)
    config = load_workspace_config(workspace, config_path)
    records = _load_records(workspace)
    changes: list[PlannedChange] = []
    conflicts: list[PlannedChange] = []

    for path, content in _generated_artifacts(config).items():
        if _is_symlink_alias(workspace, path):
            conflicts.append(_conflict(path, None, "path uses a symlink alias"))
        else:
            _plan_generated(workspace, path, content, records.get(path), changes, conflicts)
    for path, content in _state_artifacts(config).items():
        if _is_symlink_alias(workspace, path):
            conflicts.append(_conflict(path, None, "path uses a symlink alias"))
        else:
            _plan_state(workspace, path, content, records.get(path), changes, conflicts)
    for path, block_id, body in _authority_artifacts(config):
        if _is_symlink_alias(workspace, path):
            conflicts.append(_conflict(path, None, "path uses a symlink alias"))
        else:
            _plan_authority(workspace, path, block_id, body, records.get(path), changes, conflicts)

    return Plan(
        workspace=workspace,
        changes=tuple(sorted(changes, key=_path_key)),
        conflicts=tuple(sorted(conflicts, key=_path_key)),
    )


def format_plan(plan: Plan, json_output: bool = False) -> str:
    """Format a plan without exposing managed or user-authored file content."""
    changes = tuple(sorted(plan.changes, key=_path_key))
    conflicts = tuple(sorted(plan.conflicts, key=_path_key))
    payload = {
        "changes": [_change_data(change) for change in changes],
        "conflicts": [_change_data(change) for change in conflicts],
        "workspace": str(plan.workspace),
    }
    if json_output:
        return json.dumps(payload, sort_keys=True, indent=2) + "\n"
    lines = [f"workspace: {plan.workspace}"]
    for label, items in (("changes", changes), ("conflicts", conflicts)):
        lines.append(f"{label}:")
        lines.extend(
            f"  {item.path} {item.kind}: {item.reason} "
            f"({item.before_sha256 or '-'} -> {item.after_sha256 or '-'})"
            for item in items
        )
    return "\n".join(lines) + "\n"


def _generated_artifacts(config: WorkspaceConfig) -> dict[PurePosixPath, str]:
    return {
        PurePosixPath("_memory/Context/project-registry.md"): render_registry(config),
        PurePosixPath("_memory/Context/active-state-index.md"): render_active_index(config),
    }


def _state_artifacts(config: WorkspaceConfig) -> dict[PurePosixPath, str]:
    artifacts = {config.root_state: render_root_state(config)}
    artifacts.update({project.state: render_project_state(project) for project in config.projects})
    return artifacts


def _authority_artifacts(config: WorkspaceConfig) -> tuple[tuple[PurePosixPath, str, str], ...]:
    artifacts = [(path, "root", render_root_protocol(config)) for path in config.root_authority]
    for project in config.projects:
        artifacts.extend(
            (path, project.identity, render_project_protocol(project)) for path in project.authority
        )
    return tuple(artifacts)


def _plan_generated(
    workspace: Path,
    path: PurePosixPath,
    content: str,
    record: dict[str, str] | None,
    changes: list[PlannedChange],
    conflicts: list[PlannedChange],
) -> None:
    before = _read(workspace, path)
    if before is None:
        changes.append(_change(path, ChangeKind.CREATE, None, content, "generated artifact is absent"))
    elif before == content and _owned(record, "managed") and _matches_applied(record, before):
        return
    elif before == content:
        if _owned(record, "managed"):
            conflicts.append(_conflict(path, before, "managed generated artifact differs from applied hash"))
        else:
            changes.append(_change(path, ChangeKind.ADOPT_EXISTING, before, None, "exact generated artifact"))
    elif _owned(record, "managed"):
        changes.append(_change(path, ChangeKind.UPDATE_GENERATED, before, content, "managed generated artifact changed"))
    else:
        conflicts.append(_conflict(path, before, "generated artifact is not managed"))


def _plan_state(
    workspace: Path,
    path: PurePosixPath,
    content: str,
    record: dict[str, str] | None,
    changes: list[PlannedChange],
    conflicts: list[PlannedChange],
) -> None:
    before = _read(workspace, path)
    if before is None and _owned(record, "curated"):
        conflicts.append(_conflict(path, None, "recorded curated state was deleted"))
    elif before is None:
        changes.append(_change(path, ChangeKind.CREATE, None, content, "curated state is absent"))
    elif not _owned(record, "curated"):
        conflicts.append(_conflict(path, before, "unrecorded pre-existing state file"))
    # Curated state prose belongs to the project.  Its structural schema is
    # verified separately, so a content edit must not produce a managed drift.


def _plan_authority(
    workspace: Path,
    path: PurePosixPath,
    block_id: str,
    body: str,
    record: dict[str, str] | None,
    changes: list[PlannedChange],
    conflicts: list[PlannedChange],
) -> None:
    before = _read(workspace, path)
    if before is None:
        conflicts.append(_conflict(path, None, "configured authority file is absent"))
        return
    try:
        after = upsert_managed_block(before, block_id, body)
    except ManagedBlockConflict as exc:
        conflicts.append(_conflict(path, before, str(exc)))
        return
    if after == before and _owned(record, "managed") and _matches_applied(record, before):
        return
    elif after == before:
        if _owned(record, "managed"):
            conflicts.append(_conflict(path, before, "managed authority differs from applied hash"))
        else:
            changes.append(_change(path, ChangeKind.ADOPT_EXISTING, before, None, "exact managed block"))
    else:
        changes.append(
            _change(path, ChangeKind.UPDATE_MANAGED_BLOCK, before, after, "managed block differs")
        )


def _load_records(workspace: Path) -> dict[PurePosixPath, dict[str, str]]:
    metadata = workspace / ".memory-system/installation.json"
    metadata_path = PurePosixPath(".memory-system/installation.json")
    if _is_symlink_alias(workspace, metadata_path) or not metadata.exists():
        return {}
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    artifacts = data.get("artifacts") if isinstance(data, dict) else None
    if not isinstance(artifacts, dict):
        return {}
    records = {}
    for raw_path, record in artifacts.items():
        path = PurePosixPath(raw_path) if isinstance(raw_path, str) else None
        if path is not None and not path.is_absolute() and ".." not in path.parts and isinstance(record, dict):
            records[path] = {
                key: value for key, value in record.items() if isinstance(key, str) and isinstance(value, str)
            }
    return records


def _read(workspace: Path, path: PurePosixPath) -> str | None:
    target = workspace.joinpath(*path.parts)
    if not target.exists():
        return None
    return target.read_bytes().decode("utf-8")


def _is_symlink_alias(workspace: Path, path: PurePosixPath) -> bool:
    target = workspace.joinpath(*path.parts)
    try:
        return target.resolve(strict=False) != target
    except OSError:
        return True


def _owned(record: dict[str, str] | None, ownership: str) -> bool:
    return record is not None and record.get("ownership") == ownership


def _matches_applied(record: dict[str, str] | None, content: str) -> bool:
    return record is not None and record.get("applied_sha256") == _sha256(content)


def _change(
    path: PurePosixPath,
    kind: ChangeKind,
    before: str | None,
    after: str | None,
    reason: str,
) -> PlannedChange:
    return PlannedChange(path, kind, _sha256(before), _sha256(after), after, reason)


def _conflict(path: PurePosixPath, before: str | None, reason: str) -> PlannedChange:
    return PlannedChange(path, ChangeKind.CONFLICT, _sha256(before), None, None, reason)


def _sha256(content: str | None) -> str | None:
    return hashlib.sha256(content.encode()).hexdigest() if content is not None else None


def _path_key(change: PlannedChange) -> str:
    return change.path.as_posix()


def _change_data(change: PlannedChange) -> dict[str, str | None]:
    return {
        "after_sha256": change.after_sha256,
        "before_sha256": change.before_sha256,
        "kind": change.kind,
        "path": change.path.as_posix(),
        "reason": change.reason,
    }
