from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_system.installer import apply_plan
from memory_system.planner import build_plan
from memory_system.verifier import verify_workspace


def _write_workspace_config(workspace: Path) -> None:
    (workspace / "alpha").mkdir()
    (workspace / "beta").mkdir()
    (workspace / "AGENTS.md").write_text("", encoding="utf-8")
    (workspace / "alpha/AGENTS.md").write_text("", encoding="utf-8")
    (workspace / "beta/AGENTS.md").write_text("", encoding="utf-8")
    (workspace / "compatibility.toml").write_text(
        """schema_version = 1
methodology_version = "1.0"

[claude_mem]
minimum_version = "13.15.0"
tested_versions = ["13.15.0"]
default_worker_port = 37700
""",
        encoding="utf-8",
    )
    (workspace / "memory-system.toml").write_text(
        """schema_version = 1
methodology_version = "1.0"

[workspace]
root_identity = "workspace"
state = "_memory/Context/projects/workspace.md"
authority = ["AGENTS.md"]

[[projects]]
identity = "alpha"
path = "alpha"
state = "_memory/Context/projects/alpha.md"
authority = ["alpha/AGENTS.md"]

[[projects]]
identity = "beta"
path = "beta"
state = "_memory/Context/projects/beta.md"
authority = ["beta/AGENTS.md"]
""",
        encoding="utf-8",
    )


@pytest.fixture
def deployed_workspace(tmp_path: Path) -> Path:
    _write_workspace_config(tmp_path)
    plan = build_plan(tmp_path)
    assert not plan.conflicts
    apply_plan(plan, confirmed=True)
    return tmp_path


def _check(report: object, code: str) -> object:
    return next(check for check in report.checks if check.code == code)


def _metadata(workspace: Path) -> dict[str, object]:
    return json.loads((workspace / ".memory-system/installation.json").read_text(encoding="utf-8"))


def _write_metadata(workspace: Path, payload: dict[str, object]) -> None:
    (workspace / ".memory-system/installation.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_verify_accepts_a_deployed_workspace_with_clean_second_plan(deployed_workspace: Path) -> None:
    """A missing registry route or a planned follow-up change must fail this check."""
    report = verify_workspace(deployed_workspace)

    assert report.ok
    assert _check(report, "registry-routing").passed
    assert _check(report, "idempotence").passed


def test_verify_rejects_missing_registry_entry(deployed_workspace: Path) -> None:
    """Deleting one configured route must be observable as routing drift."""
    registry = deployed_workspace / "_memory/Context/project-registry.md"
    registry.write_text(
        "\n".join(line for line in registry.read_text(encoding="utf-8").splitlines() if "alpha" not in line)
        + "\n",
        encoding="utf-8",
    )

    assert not _check(verify_workspace(deployed_workspace), "registry-routing").passed


def test_verify_rejects_missing_curated_state_heading(deployed_workspace: Path) -> None:
    """Removing a required section must fail schema validation without relying on hashes."""
    state = deployed_workspace / "_memory/Context/projects/alpha.md"
    state.write_text(state.read_text(encoding="utf-8").replace("## Freshness", "## Staleness"), encoding="utf-8")

    assert not _check(verify_workspace(deployed_workspace), "state-schema").passed


def test_verify_rejects_duplicate_managed_markers(deployed_workspace: Path) -> None:
    """A second block for the configured owner must fail marker-balance validation."""
    authority = deployed_workspace / "alpha/AGENTS.md"
    authority.write_text(
        authority.read_text(encoding="utf-8")
        + "<!-- memory-system:alpha:start -->\nextra\n<!-- memory-system:alpha:end -->\n",
        encoding="utf-8",
    )

    assert not _check(verify_workspace(deployed_workspace), "managed-markers").passed


def test_verify_rejects_authority_outside_its_project(deployed_workspace: Path) -> None:
    """Routing a child authority through the workspace root must be rejected."""
    config = deployed_workspace / "memory-system.toml"
    config.write_text(config.read_text(encoding="utf-8").replace('authority = ["alpha/AGENTS.md"]', 'authority = ["AGENTS.md"]'), encoding="utf-8")

    assert not _check(verify_workspace(deployed_workspace), "configuration").passed


def test_verify_rejects_managed_artifact_hash_drift(deployed_workspace: Path) -> None:
    """Changing a recorded generated-artifact hash must fail managed ownership validation."""
    payload = _metadata(deployed_workspace)
    payload["artifacts"]["_memory/Context/project-registry.md"]["applied_sha256"] = "0" * 64
    _write_metadata(deployed_workspace, payload)

    assert not _check(verify_workspace(deployed_workspace), "managed-hashes").passed


def test_verify_rejects_absolute_metadata_paths_and_runtime_data(deployed_workspace: Path) -> None:
    """Unsafe metadata routes and forbidden runtime names must be reported independently."""
    payload = _metadata(deployed_workspace)
    payload["artifacts"]["/outside.md"] = {"ownership": "managed"}
    payload["secret"] = "not permitted"
    _write_metadata(deployed_workspace, payload)
    (deployed_workspace / ".memory-system/claude-mem.db").write_text("runtime", encoding="utf-8")

    report = verify_workspace(deployed_workspace)

    assert not _check(report, "metadata-paths").passed
    assert not _check(report, "runtime-data").passed


def test_verify_rejects_sibling_identity_inside_project_block(deployed_workspace: Path) -> None:
    """Mentioning beta inside alpha's managed block must fail project isolation."""
    authority = deployed_workspace / "alpha/AGENTS.md"
    authority.write_text(
        authority.read_text(encoding="utf-8").replace("<!-- memory-system:alpha:end -->", "beta must not be routed here\n<!-- memory-system:alpha:end -->"),
        encoding="utf-8",
    )

    assert not _check(verify_workspace(deployed_workspace), "project-isolation").passed


def test_verify_rejects_a_nonempty_second_plan(deployed_workspace: Path) -> None:
    """A changed generated artifact must make the deployment non-idempotent."""
    registry = deployed_workspace / "_memory/Context/project-registry.md"
    registry.write_text(registry.read_text(encoding="utf-8") + "local drift\n", encoding="utf-8")

    assert not _check(verify_workspace(deployed_workspace), "idempotence").passed


def test_curated_state_edits_remain_schema_valid_and_idempotent(deployed_workspace: Path) -> None:
    """Curated prose is user-owned; only its required structure is verified."""
    state = deployed_workspace / "_memory/Context/projects/alpha.md"
    state.write_text(
        state.read_text(encoding="utf-8").replace("- None recorded.", "- Updated by the project owner."),
        encoding="utf-8",
    )

    report = verify_workspace(deployed_workspace)

    assert _check(report, "state-schema").passed
    assert _check(report, "managed-hashes").passed
    assert _check(report, "idempotence").passed


def test_require_runtime_promotes_missing_runtime_to_failure(
    deployed_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verifier must turn actual doctor runtime warnings into strict failures on request."""
    import memory_system.runtime as runtime

    monkeypatch.setattr(runtime, "discover_installed_versions", lambda home: ())
    monkeypatch.setattr(runtime, "probe_worker", lambda port: False)

    report = verify_workspace(deployed_workspace, require_runtime=True)

    assert not report.ok
    assert not _check(report, "runtime-health").passed
