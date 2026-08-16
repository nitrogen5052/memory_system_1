"""Transactional application and rollback for portable memory plans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile

from .config import load_workspace_config
from .planner import ChangeKind, Plan, PlannedChange


_METADATA_PATH = PurePosixPath(".memory-system/installation.json")
_BACKUPS_ROOT = PurePosixPath(".memory-system/backups")


class ApplyError(RuntimeError):
    """Raised when a memory installation cannot be safely applied."""


@dataclass(frozen=True)
class InstallResult:
    backup_dir: PurePosixPath | None
    changed_paths: tuple[PurePosixPath, ...]
    adopted_paths: tuple[PurePosixPath, ...]
    restored_after_failure: bool


@dataclass(frozen=True)
class _FileState:
    content: bytes | None
    mode: int | None


@dataclass(frozen=True)
class _RollbackOperation:
    path: PurePosixPath
    replacement: _FileState


def apply_plan(plan: Plan, confirmed: bool) -> InstallResult:
    """Apply a conflict-free plan, restoring the workspace if a write fails."""
    if not confirmed:
        raise ApplyError("installation was not confirmed")
    if plan.conflicts:
        raise ApplyError("plan contains conflicts")

    workspace = _workspace(plan.workspace)
    changes = tuple(sorted(plan.changes, key=lambda item: item.path.as_posix()))
    _validate_changes(workspace, changes)
    metadata = _contained_path(workspace, _METADATA_PATH, "metadata")
    _validate_no_symlink_alias(workspace, _METADATA_PATH, "metadata")
    if not changes:
        return InstallResult(None, (), (), False)

    for change in changes:
        _check_before_hash(workspace, change)

    prior_metadata_existed = metadata.exists()
    backup_dir = _create_backup_dir(workspace)
    originals: dict[PurePosixPath, bytes | None] = {}
    modes: dict[PurePosixPath, int | None] = {}
    rewritten = tuple(change for change in changes if change.kind is not ChangeKind.ADOPT_EXISTING)
    for change in rewritten:
        _backup_target(workspace, backup_dir, change.path, originals, modes)
    _backup_target(workspace, backup_dir, _METADATA_PATH, originals, modes)

    changed_paths: list[PurePosixPath] = []
    adopted_paths = tuple(change.path for change in changes if change.kind is ChangeKind.ADOPT_EXISTING)
    touched: list[PurePosixPath] = []
    try:
        for change in rewritten:
            assert change.content is not None
            target = _contained_path(workspace, change.path, "planned target")
            touched.append(change.path)
            _atomic_write(target, change.content.encode("utf-8"), modes[change.path])
            _check_after_hash(target, change)
            changed_paths.append(change.path)

        payload = _installation_payload(workspace, changes, backup_dir, prior_metadata_existed)
        metadata_bytes = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        touched.append(_METADATA_PATH)
        _atomic_write(metadata, metadata_bytes, modes[_METADATA_PATH])
        if _sha256_bytes(metadata.read_bytes()) != _sha256_bytes(metadata_bytes):
            raise ApplyError("installation metadata hash mismatch after write")
    except (OSError, UnicodeError, ApplyError) as exc:
        restore_error = _restore(workspace, touched, originals, modes)
        detail = str(exc) if restore_error is None else f"{exc}; recovery failed: {restore_error}"
        raise ApplyError(detail) from exc

    return InstallResult(backup_dir, tuple(changed_paths), adopted_paths, False)


def rollback_installation(workspace: Path, backup_dir: PurePosixPath) -> InstallResult:
    """Restore the installation represented by *backup_dir* without touching adopted bytes."""
    workspace = _workspace(workspace)
    backup = _validate_backup_dir(workspace, backup_dir)
    metadata = _contained_path(workspace, _METADATA_PATH, "metadata")
    _validate_no_symlink_alias(workspace, _METADATA_PATH, "metadata")
    if not metadata.exists():
        raise ApplyError("installation metadata does not exist")
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplyError("invalid installation metadata") from exc
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, dict):
        raise ApplyError("invalid installation metadata")
    if payload.get("backup_dir") != backup_dir.as_posix():
        raise ApplyError("backup directory does not match installation metadata")
    operations, changed, adopted = _preflight_rollback(workspace, backup, payload, artifacts)
    states = {operation.path: _capture_state(workspace, operation.path) for operation in operations}
    touched: list[PurePosixPath] = []
    try:
        for operation in operations:
            touched.append(operation.path)
            _restore_state(_contained_path(workspace, operation.path, "rollback target"), operation.replacement)
    except OSError as exc:
        restore_error = _restore_states(workspace, touched, states)
        detail = str(exc) if restore_error is None else f"{exc}; rollback recovery failed: {restore_error}"
        raise ApplyError(detail) from exc
    return InstallResult(backup_dir, changed, adopted, False)


def _workspace(workspace: Path) -> Path:
    try:
        resolved = workspace.resolve(strict=True)
    except OSError as exc:
        raise ApplyError(f"workspace is unavailable: {workspace}") from exc
    if not resolved.is_dir():
        raise ApplyError("workspace is not a directory")
    return resolved


def _validate_changes(workspace: Path, changes: tuple[PlannedChange, ...]) -> None:
    seen: set[PurePosixPath] = set()
    for change in changes:
        path = _safe_relative(change.path, "planned path")
        if path in seen:
            raise ApplyError(f"duplicate planned path: {path}")
        seen.add(path)
        if change.kind is ChangeKind.CONFLICT or change.kind not in ChangeKind:
            raise ApplyError(f"invalid planned change kind: {change.kind}")
        if change.kind is ChangeKind.ADOPT_EXISTING:
            if change.content is not None or change.before_sha256 is None:
                raise ApplyError(f"invalid adoption change: {path}")
        elif change.content is None or change.after_sha256 is None:
            raise ApplyError(f"missing replacement content: {path}")
        _contained_path(workspace, path, "planned target")
        _validate_no_symlink_alias(workspace, path, "planned target")


def _check_before_hash(workspace: Path, change: PlannedChange) -> None:
    target = _contained_path(workspace, change.path, "planned target")
    actual = _sha256_bytes(target.read_bytes()) if target.exists() else None
    if actual != change.before_sha256:
        raise ApplyError(f"{change.path} changed since plan")


def _check_after_hash(target: Path, change: PlannedChange) -> None:
    actual = _sha256_bytes(target.read_bytes())
    if actual != change.after_sha256:
        raise ApplyError(f"{target.name} hash mismatch after write")


def _create_backup_dir(workspace: Path) -> PurePosixPath:
    _validate_no_symlink_alias(workspace, PurePosixPath(".memory-system"), "metadata directory")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = _BACKUPS_ROOT / timestamp
    target = _contained_path(workspace, backup_dir, "backup")
    try:
        (target / "files").mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise ApplyError("could not create backup directory") from exc
    return backup_dir


def _backup_target(
    workspace: Path,
    backup_dir: PurePosixPath,
    path: PurePosixPath,
    originals: dict[PurePosixPath, bytes | None],
    modes: dict[PurePosixPath, int | None],
) -> None:
    target = _contained_path(workspace, path, "backup target")
    if target.exists():
        original = target.read_bytes()
        mode = stat.S_IMODE(target.stat().st_mode)
        archive = _contained_path(workspace, backup_dir / "files" / path, "backup file")
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, archive)
    else:
        original, mode = None, None
    originals[path], modes[path] = original, mode


def _restore(
    workspace: Path,
    touched: list[PurePosixPath],
    originals: dict[PurePosixPath, bytes | None],
    modes: dict[PurePosixPath, int | None],
) -> OSError | None:
    failure: OSError | None = None
    for path in reversed(touched):
        try:
            _restore_state(_contained_path(workspace, path, "restore target"), _FileState(originals[path], modes[path]))
        except OSError as exc:
            failure = failure or exc
    return failure


def _installation_payload(
    workspace: Path,
    changes: tuple[PlannedChange, ...],
    backup_dir: PurePosixPath,
    prior_metadata_existed: bool,
) -> dict[str, object]:
    try:
        config = load_workspace_config(workspace)
        schema_version, methodology_version = config.schema_version, config.methodology_version
    except Exception as exc:  # The plan may be constructed by an API caller without config files.
        if any(change.path.parts[:3] == ("_memory", "Context", "projects") for change in changes):
            raise ApplyError("could not load workspace configuration") from exc
        schema_version, methodology_version = 1, "unknown"
    artifacts: dict[str, dict[str, str | None]] = {}
    for change in changes:
        record: dict[str, str | None] = {
            "original_sha256": change.before_sha256,
            "applied_sha256": change.after_sha256 or change.before_sha256,
            "change_kind": change.kind.value,
            "ownership": _ownership(change.path),
        }
        block_id = _block_id(change.content)
        if block_id is not None:
            record["block_id"] = block_id
        artifacts[change.path.as_posix()] = record
    return {
        "applied_at_utc": datetime.now(UTC).isoformat(),
        "artifacts": artifacts,
        "backup_dir": backup_dir.as_posix(),
        "methodology_version": methodology_version,
        "prior_metadata_existed": prior_metadata_existed,
        "schema_version": schema_version,
    }


def _ownership(path: PurePosixPath) -> str:
    return "curated" if path.parts[:3] == ("_memory", "Context", "projects") else "managed"


def _block_id(content: str | None) -> str | None:
    if content is None:
        return None
    marker = "<!-- memory-system:"
    start = content.find(marker)
    if start < 0:
        return None
    end = content.find(":start -->", start)
    return content[start + len(marker) : end] if end > start else None


def _atomic_write(path: Path, content: bytes, mode: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".memory-system-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _safe_relative(path: PurePosixPath | str, label: str) -> PurePosixPath:
    value = PurePosixPath(path)
    if value.is_absolute() or ".." in value.parts or value == PurePosixPath("."):
        raise ApplyError(f"{label} path must be relative and contained")
    return value


def _contained_path(workspace: Path, path: PurePosixPath, label: str) -> Path:
    safe = _safe_relative(path, label)
    target = workspace.joinpath(*safe.parts)
    try:
        target.resolve(strict=False).relative_to(workspace)
    except (OSError, ValueError) as exc:
        raise ApplyError(f"{label} path escapes workspace through a symlink") from exc
    return target


def _validate_no_symlink_alias(workspace: Path, path: PurePosixPath, label: str) -> None:
    target = _contained_path(workspace, path, label)
    try:
        if target.resolve(strict=False) != target:
            raise ApplyError(f"{label} uses a symlink")
    except OSError as exc:
        raise ApplyError(f"{label} cannot be resolved") from exc


def _validate_backup_dir(workspace: Path, backup_dir: PurePosixPath) -> Path:
    safe = _safe_relative(backup_dir, "backup")
    if safe.parts[: len(_BACKUPS_ROOT.parts)] != _BACKUPS_ROOT.parts:
        raise ApplyError("backup path must be relative to .memory-system/backups")
    target = _contained_path(workspace, safe, "backup")
    _validate_no_symlink_alias(workspace, safe, "backup")
    if not target.is_dir():
        raise ApplyError("backup directory does not exist")
    return target


def _validate_archive_path(backup: Path, archive: Path) -> None:
    try:
        resolved_backup = backup.resolve(strict=True)
        resolved_archive = archive.resolve(strict=False)
        resolved_archive.relative_to(resolved_backup)
        if resolved_archive != archive:
            raise ApplyError("backup file uses a symlink")
    except (OSError, ValueError) as exc:
        raise ApplyError("backup file escapes backup directory") from exc


def _preflight_rollback(
    workspace: Path, backup: Path, payload: dict[str, object], artifacts: dict[object, object]
) -> tuple[tuple[_RollbackOperation, ...], tuple[PurePosixPath, ...], tuple[PurePosixPath, ...]]:
    operations: list[_RollbackOperation] = []
    changed: list[PurePosixPath] = []
    adopted: list[PurePosixPath] = []
    for raw_path, record in sorted(artifacts.items()):
        path = _safe_relative(raw_path, "artifact")
        if not isinstance(record, dict):
            raise ApplyError("invalid installation artifact")
        _contained_path(workspace, path, "artifact")
        _validate_no_symlink_alias(workspace, path, "artifact")
        kind = record.get("change_kind")
        if kind == ChangeKind.ADOPT_EXISTING.value:
            adopted.append(path)
            continue
        applied = record.get("applied_sha256")
        if not isinstance(applied, str):
            raise ApplyError(f"invalid applied hash for {path}")
        target = _contained_path(workspace, path, "artifact")
        actual = _sha256_bytes(target.read_bytes()) if target.exists() else None
        if actual != applied:
            raise ApplyError(f"{path} changed since installation")
        archive = backup / "files" / Path(*path.parts)
        _validate_archive_path(backup, archive)
        if kind == ChangeKind.CREATE.value:
            operations.append(_RollbackOperation(path, _FileState(None, None)))
        elif not archive.is_file():
            raise ApplyError(f"backup is incomplete for {path}")
        else:
            operations.append(
                _RollbackOperation(path, _FileState(archive.read_bytes(), stat.S_IMODE(archive.stat().st_mode)))
            )
        changed.append(path)
    archived_metadata = backup / "files" / Path(*_METADATA_PATH.parts)
    _validate_archive_path(backup, archived_metadata)
    prior_metadata_existed = payload.get("prior_metadata_existed")
    if not isinstance(prior_metadata_existed, bool):
        raise ApplyError("invalid prior metadata condition")
    if prior_metadata_existed and not archived_metadata.is_file():
        raise ApplyError("backup is incomplete for prior metadata")
    metadata_replacement = (
        _FileState(archived_metadata.read_bytes(), stat.S_IMODE(archived_metadata.stat().st_mode))
        if prior_metadata_existed
        else _FileState(None, None)
    )
    operations.append(_RollbackOperation(_METADATA_PATH, metadata_replacement))
    return tuple(operations), tuple(changed), tuple(adopted)


def _capture_state(workspace: Path, path: PurePosixPath) -> _FileState:
    target = _contained_path(workspace, path, "rollback target")
    if not target.exists():
        return _FileState(None, None)
    return _FileState(target.read_bytes(), stat.S_IMODE(target.stat().st_mode))


def _restore_states(
    workspace: Path, touched: list[PurePosixPath], states: dict[PurePosixPath, _FileState]
) -> OSError | None:
    failure: OSError | None = None
    for path in reversed(touched):
        try:
            _restore_state(_contained_path(workspace, path, "rollback recovery target"), states[path])
        except OSError as exc:
            failure = failure or exc
    return failure


def _restore_state(path: Path, state: _FileState) -> None:
    if state.content is None:
        _remove_created_target(path)
    else:
        _atomic_write(path, state.content, state.mode)


def _remove_created_target(path: Path) -> None:
    if path.exists() or path.is_symlink():
        path.unlink()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
