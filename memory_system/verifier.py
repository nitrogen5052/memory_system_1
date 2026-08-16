"""Read-only structural and idempotence verification for memory deployments."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .config import ConfigError, WorkspaceConfig, load_compatibility, load_workspace_config
from .planner import build_plan
from .runtime import run_doctor
from .templates import ManagedBlockConflict, managed_block_digest, managed_block_ids, render_registry


_STATE_HEADINGS = (
    "Project",
    "Authority",
    "Active Goal",
    "Current Blockers",
    "Hard Invariants",
    "Approved Current Decisions",
    "Pending Actions",
    "Recent Milestones",
    "Freshness",
)
_MARKER = re.compile(r"<!-- memory-system:([A-Za-z0-9._:-]+):(start|end) -->")
_MARKER_LIKE = re.compile(r"<!--\s*memory-system.*?(?:-->|$)", re.DOTALL)
_FORBIDDEN_RUNTIME_TERMS = (
    "claude-mem.db",
    "chroma",
    "token",
    "secret",
    "credential",
    "observer-sessions",
)
_RUNTIME_FAILURE_CODES = frozenset(
    {
        "claude-mem-not-found",
        "version-too-old",
        "untested-version",
        "worker-unreachable",
        "invalid-compatibility",
    }
)


@dataclass(frozen=True)
class VerificationCheck:
    code: str
    passed: bool
    message: str


@dataclass(frozen=True)
class VerificationReport:
    checks: tuple[VerificationCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks)


def verify_workspace(
    workspace: Path, config_path: Path | None = None, require_runtime: bool = False
) -> VerificationReport:
    """Verify a deployed workspace without modifying it or requiring a healthy runtime by default."""
    try:
        workspace = workspace.resolve(strict=True)
        config = load_workspace_config(workspace, config_path)
    except (ConfigError, OSError) as exc:
        return VerificationReport((VerificationCheck("configuration", False, str(exc)),))

    metadata, metadata_error = _load_metadata(workspace)
    checks = [
        VerificationCheck("configuration", True, "workspace configuration is valid"),
        _registry_check(workspace, config),
        _state_schema_check(workspace, config),
        _marker_check(workspace, config),
        _metadata_path_check(metadata, metadata_error),
        _managed_hash_check(workspace, config, metadata, metadata_error),
        _runtime_data_check(workspace, metadata),
        _project_isolation_check(workspace, config),
        _idempotence_check(workspace, config_path),
        _runtime_health_check(workspace, require_runtime),
    ]
    return VerificationReport(tuple(checks))


def _registry_check(workspace: Path, config: WorkspaceConfig) -> VerificationCheck:
    registry = workspace / "_memory/Context/project-registry.md"
    try:
        actual = registry.read_text(encoding="utf-8")
    except OSError as exc:
        return VerificationCheck("registry-routing", False, f"registry is unavailable: {exc}")
    expected_rows = [line for line in render_registry(config).splitlines() if line.startswith("| ")]
    missing_or_duplicate = [row for row in expected_rows if actual.count(row) != 1]
    if missing_or_duplicate:
        return VerificationCheck("registry-routing", False, "configured registry routes must appear exactly once")
    return VerificationCheck("registry-routing", True, "every configured registry route appears exactly once")


def _state_schema_check(workspace: Path, config: WorkspaceConfig) -> VerificationCheck:
    for state in (config.root_state, *(project.state for project in config.projects)):
        try:
            headings = tuple(
                line[3:] for line in _target(workspace, state).read_text(encoding="utf-8").splitlines() if line.startswith("## ")
            )
        except OSError as exc:
            return VerificationCheck("state-schema", False, f"state is unavailable: {state}: {exc}")
        if headings != _STATE_HEADINGS:
            return VerificationCheck("state-schema", False, f"state headings are invalid: {state}")
        content = _target(workspace, state).read_text(encoding="utf-8")
        if "canonical root:" not in content or "last_verified:" not in content or "last_reconciled:" not in content:
            return VerificationCheck("state-schema", False, f"state freshness fields are invalid: {state}")
    return VerificationCheck("state-schema", True, "all curated state documents have the required headings")


def _marker_check(workspace: Path, config: WorkspaceConfig) -> VerificationCheck:
    targets = [(path, "root") for path in config.root_authority]
    targets.extend((path, project.identity) for project in config.projects for path in project.authority)
    for path, expected_id in targets:
        try:
            content = _target(workspace, path).read_text(encoding="utf-8")
        except OSError as exc:
            return VerificationCheck("managed-markers", False, f"authority is unavailable: {path}: {exc}")
        try:
            owners = managed_block_ids(content)
        except ManagedBlockConflict as exc:
            return VerificationCheck("managed-markers", False, f"invalid marker syntax: {path}: {exc}")
        if tuple(owners) != (expected_id,):
            return VerificationCheck("managed-markers", False, f"expected one balanced {expected_id} block: {path}")
        comments = tuple(_MARKER_LIKE.finditer(content))
        markers = tuple(_MARKER.finditer(content))
        if len(comments) != len(markers) or any(comment.group() != marker.group() for comment, marker in zip(comments, markers, strict=True)):
            return VerificationCheck("managed-markers", False, f"invalid marker syntax: {path}")
    return VerificationCheck("managed-markers", True, "all authority targets have one balanced managed block")


def _load_metadata(workspace: Path) -> tuple[dict[str, object] | None, str | None]:
    try:
        payload = json.loads((workspace / ".memory-system/installation.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"installation metadata is invalid: {exc}"
    return (payload, None) if isinstance(payload, dict) else (None, "installation metadata must be an object")


def _metadata_path_check(metadata: dict[str, object] | None, error: str | None) -> VerificationCheck:
    if metadata is None:
        return VerificationCheck("metadata-paths", False, error or "installation metadata is absent")
    artifacts = metadata.get("artifacts")
    if not isinstance(artifacts, dict):
        return VerificationCheck("metadata-paths", False, "installation metadata has no artifact records")
    for raw_path in artifacts:
        path = PurePosixPath(raw_path) if isinstance(raw_path, str) else None
        if path is None or path.is_absolute() or ".." in path.parts:
            return VerificationCheck("metadata-paths", False, "metadata contains an unsafe artifact path")
    if any(isinstance(value, str) and PurePosixPath(value).is_absolute() for value in metadata.values()):
        return VerificationCheck("metadata-paths", False, "metadata contains an absolute path")
    return VerificationCheck("metadata-paths", True, "metadata paths are relative and contained")


def _managed_hash_check(
    workspace: Path, config: WorkspaceConfig, metadata: dict[str, object] | None, error: str | None
) -> VerificationCheck:
    if metadata is None:
        return VerificationCheck("managed-hashes", False, error or "installation metadata is absent")
    artifacts = metadata.get("artifacts")
    if not isinstance(artifacts, dict):
        return VerificationCheck("managed-hashes", False, "installation metadata has no artifact records")
    configured = [
        PurePosixPath("_memory/Context/project-registry.md"),
        PurePosixPath("_memory/Context/active-state-index.md"),
        *config.root_authority,
        *(path for project in config.projects for path in project.authority),
    ]
    for path in configured:
        record = artifacts.get(path.as_posix())
        if not isinstance(record, dict) or record.get("ownership") != "managed":
            return VerificationCheck("managed-hashes", False, f"managed artifact record is missing: {path}")
    for raw_path, record in artifacts.items():
        if not isinstance(record, dict) or record.get("ownership") != "managed":
            continue
        path = PurePosixPath(raw_path) if isinstance(raw_path, str) else None
        if path is None or path.is_absolute() or ".." in path.parts:
            return VerificationCheck("managed-hashes", False, "managed metadata path is unsafe")
        target = _target(workspace, path)
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(workspace)
        except (OSError, ValueError) as exc:
            return VerificationCheck("managed-hashes", False, f"managed artifact is unavailable: {path}: {exc}")
        if resolved != target or not target.is_file():
            return VerificationCheck("managed-hashes", False, f"managed artifact is not a contained regular file: {path}")
        if isinstance(record.get("block_id"), str):
            try:
                actual_block = managed_block_digest(target.read_text(encoding="utf-8"), record["block_id"])
            except (OSError, ManagedBlockConflict) as exc:
                return VerificationCheck("managed-hashes", False, f"managed block is invalid: {path}: {exc}")
            expected_block = record.get("managed_block_sha256")
            if expected_block is not None and expected_block != actual_block:
                return VerificationCheck("managed-hashes", False, f"managed block hash drift: {path}")
            if expected_block is None and record.get("applied_sha256") != hashlib.sha256(target.read_bytes()).hexdigest():
                return VerificationCheck("managed-hashes", False, f"managed artifact hash drift: {path}")
        else:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if record.get("applied_sha256") != actual:
                return VerificationCheck("managed-hashes", False, f"managed artifact hash drift: {path}")
    for state in (config.root_state, *(project.state for project in config.projects)):
        record = artifacts.get(state.as_posix())
        if not isinstance(record, dict) or record.get("ownership") != "curated":
            return VerificationCheck("managed-hashes", False, f"curated state ownership is missing: {state}")
    return VerificationCheck("managed-hashes", True, "managed artifacts match recorded hashes; curated state remains content-owned")


def _runtime_data_check(workspace: Path, metadata: dict[str, object] | None) -> VerificationCheck:
    names = [path.name.casefold() for path in (workspace / ".memory-system").rglob("*")]
    keys = list(_json_keys(metadata)) if metadata is not None else []
    if any(term in value for value in (*names, *keys) for term in _FORBIDDEN_RUNTIME_TERMS):
        return VerificationCheck("runtime-data", False, "runtime data or sensitive keys are stored under .memory-system")
    return VerificationCheck("runtime-data", True, "no forbidden runtime data is stored in installation metadata")


def _json_keys(value: object) -> tuple[str, ...]:
    if isinstance(value, dict):
        return tuple(str(key).casefold() for key in value) + tuple(key for item in value.values() for key in _json_keys(item))
    if isinstance(value, list):
        return tuple(key for item in value for key in _json_keys(item))
    return ()


def _project_isolation_check(workspace: Path, config: WorkspaceConfig) -> VerificationCheck:
    for project in config.projects:
        siblings = tuple(other.identity for other in config.projects if other != project)
        for path in project.authority:
            try:
                content = _target(workspace, path).read_text(encoding="utf-8")
            except OSError as exc:
                return VerificationCheck("project-isolation", False, f"authority is unavailable: {path}: {exc}")
            match = re.search(rf"<!-- memory-system:{re.escape(project.identity)}:start -->\n(.*?)<!-- memory-system:{re.escape(project.identity)}:end -->", content, re.DOTALL)
            if match is None:
                continue
            if any(re.search(rf"\b{re.escape(identity)}\b", match.group(1), re.IGNORECASE) for identity in siblings):
                return VerificationCheck("project-isolation", False, f"{path} leaks a sibling project identity")
    return VerificationCheck("project-isolation", True, "project managed blocks contain no sibling identities")


def _idempotence_check(workspace: Path, config_path: Path | None) -> VerificationCheck:
    try:
        plan = build_plan(workspace, config_path)
    except (ConfigError, OSError, ValueError) as exc:
        return VerificationCheck("idempotence", False, f"second plan could not be built: {exc}")
    if plan.changes or plan.conflicts:
        return VerificationCheck("idempotence", False, "second plan is not empty")
    return VerificationCheck("idempotence", True, "second plan is empty")


def _runtime_health_check(workspace: Path, require_runtime: bool) -> VerificationCheck:
    try:
        doctor = run_doctor(load_compatibility(workspace / "compatibility.toml"))
    except (ConfigError, OSError) as exc:
        return VerificationCheck("runtime-health", not require_runtime, f"runtime check unavailable: {exc}")
    issues = tuple(
        diagnostic
        for diagnostic in doctor.diagnostics
        if diagnostic.severity == "error" or diagnostic.code in _RUNTIME_FAILURE_CODES
    )
    if not issues:
        return VerificationCheck("runtime-health", True, "runtime health is compatible and reachable")
    message = "; ".join(diagnostic.message for diagnostic in issues)
    return VerificationCheck("runtime-health", not require_runtime, f"runtime warning: {message}")


def _target(workspace: Path, path: PurePosixPath) -> Path:
    return workspace.joinpath(*path.parts)
