import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from memory_system.planner import ChangeKind, build_plan, format_plan
from memory_system.templates import (
    render_active_index,
    render_project_protocol,
    render_project_state,
    render_registry,
    render_root_protocol,
    render_root_state,
    upsert_managed_block,
)


@pytest.fixture
def example_workspace(tmp_path: Path) -> Path:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "zeta").mkdir()
    for authority in ("AGENTS.md", "alpha/AGENTS.md", "zeta/RULES.md"):
        (tmp_path / authority).touch()
    (tmp_path / "memory-system.toml").write_text(
        '''
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "workspace"
state = "_memory/Context/projects/workspace.md"
authority = ["AGENTS.md"]
[[projects]]
identity = "zeta"
path = "zeta"
state = "_memory/Context/projects/zeta.md"
authority = ["zeta/RULES.md"]
[[projects]]
identity = "Alpha"
path = "alpha"
state = "_memory/Context/projects/alpha.md"
authority = ["alpha/AGENTS.md"]
''',
        encoding="utf-8",
    )
    (tmp_path / "compatibility.toml").write_text(
        '''
schema_version = 1
methodology_version = "1.0"
[claude_mem]
minimum_version = "13.15.0"
tested_versions = ["13.15.0"]
default_worker_port = 37700
''',
        encoding="utf-8",
    )
    return tmp_path


def test_plan_is_deterministic(example_workspace: Path) -> None:
    first = build_plan(example_workspace)
    second = build_plan(example_workspace)

    assert first == second
    assert [change.path.as_posix() for change in first.changes] == sorted(
        change.path.as_posix() for change in first.changes
    )


def test_fresh_workspace_plans_all_generated_state_and_authority_artifacts(
    example_workspace: Path,
) -> None:
    plan = build_plan(example_workspace)

    assert [(change.path.as_posix(), change.kind) for change in plan.changes] == [
        ("AGENTS.md", ChangeKind.UPDATE_MANAGED_BLOCK),
        ("_memory/Context/active-state-index.md", ChangeKind.CREATE),
        ("_memory/Context/project-registry.md", ChangeKind.CREATE),
        ("_memory/Context/projects/alpha.md", ChangeKind.CREATE),
        ("_memory/Context/projects/workspace.md", ChangeKind.CREATE),
        ("_memory/Context/projects/zeta.md", ChangeKind.CREATE),
        ("alpha/AGENTS.md", ChangeKind.UPDATE_MANAGED_BLOCK),
        ("zeta/RULES.md", ChangeKind.UPDATE_MANAGED_BLOCK),
    ]
    assert not plan.conflicts


def test_exact_existing_generated_file_and_managed_block_are_adopted(
    example_workspace: Path,
) -> None:
    config = _config(example_workspace)
    registry_path = example_workspace / "_memory/Context/project-registry.md"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(render_registry(config), encoding="utf-8")
    agents = example_workspace / "AGENTS.md"
    agents.write_text(
        upsert_managed_block(agents.read_text(encoding="utf-8"), "root", render_root_protocol(config)),
        encoding="utf-8",
    )

    plan = build_plan(example_workspace)
    changes = {change.path.as_posix(): change for change in plan.changes}

    assert changes["_memory/Context/project-registry.md"].kind is ChangeKind.ADOPT_EXISTING
    assert changes["AGENTS.md"].kind is ChangeKind.ADOPT_EXISTING


def test_line_ending_changes_are_not_byte_identical_generated_content(
    example_workspace: Path,
) -> None:
    config = _config(example_workspace)
    registry_path = example_workspace / "_memory/Context/project-registry.md"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_bytes(render_registry(config).replace("\n", "\r\n").encode())

    plan = build_plan(example_workspace)

    assert [(change.path.as_posix(), change.kind) for change in plan.conflicts] == [
        ("_memory/Context/project-registry.md", ChangeKind.CONFLICT)
    ]


def test_recorded_artifacts_produce_no_change(example_workspace: Path) -> None:
    config = _config(example_workspace)
    expected = _expected_artifacts(config)
    for path, content in expected.items():
        target = example_workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _record_artifacts(example_workspace, expected)

    plan = build_plan(example_workspace)

    assert plan.changes == ()
    assert plan.conflicts == ()


def test_unrecorded_existing_state_file_is_a_conflict(example_workspace: Path) -> None:
    state = example_workspace / "_memory/Context/projects/workspace.md"
    state.parent.mkdir(parents=True)
    state.write_text("curated workspace notes\n", encoding="utf-8")

    plan = build_plan(example_workspace)

    assert [(change.path.as_posix(), change.kind) for change in plan.conflicts] == [
        ("_memory/Context/projects/workspace.md", ChangeKind.CONFLICT)
    ]
    assert "state" in plan.conflicts[0].reason


def test_unmanaged_memory_instructions_block_authority_mutation(example_workspace: Path) -> None:
    agents = example_workspace / "AGENTS.md"
    agents.write_text("# Memory policy\nFollow this local protocol.\n", encoding="utf-8")

    plan = build_plan(example_workspace)

    assert [(change.path.as_posix(), change.kind) for change in plan.conflicts] == [
        ("AGENTS.md", ChangeKind.CONFLICT)
    ]
    assert "unmanaged memory instructions" in plan.conflicts[0].reason


def test_generated_symlink_alias_is_a_conflict(example_workspace: Path) -> None:
    context = example_workspace / "_memory/Context"
    context.mkdir(parents=True)
    outside = example_workspace.parent / "outside-active-state-index.md"
    outside.write_text("outside\n", encoding="utf-8")
    (context / "active-state-index.md").symlink_to(outside)

    plan = build_plan(example_workspace)

    assert [(change.path.as_posix(), change.kind) for change in plan.conflicts] == [
        ("_memory/Context/active-state-index.md", ChangeKind.CONFLICT)
    ]
    assert "symlink" in plan.conflicts[0].reason


def test_format_plan_is_deterministic_and_does_not_print_file_content(example_workspace: Path) -> None:
    plan = build_plan(example_workspace)
    unordered = replace(plan, changes=tuple(reversed(plan.changes)))

    text = format_plan(plan)
    payload = json.loads(format_plan(unordered, json_output=True))

    assert "Workspace Memory Protocol" not in text
    assert [item["path"] for item in payload["changes"]] == sorted(
        item["path"] for item in payload["changes"]
    )
    assert list(payload) == sorted(payload)
    assert "content" not in payload["changes"][0]


def _config(workspace: Path):
    from memory_system.config import load_workspace_config

    return load_workspace_config(workspace)


def _expected_artifacts(config) -> dict[str, str]:
    artifacts = {
        "_memory/Context/project-registry.md": render_registry(config),
        "_memory/Context/active-state-index.md": render_active_index(config),
        config.root_state.as_posix(): render_root_state(config),
    }
    for project in config.projects:
        artifacts[project.state.as_posix()] = render_project_state(project)
    for path in config.root_authority:
        artifacts[path.as_posix()] = upsert_managed_block(
            "", "root", render_root_protocol(config)
        )
    for project in config.projects:
        for path in project.authority:
            artifacts[path.as_posix()] = upsert_managed_block(
                "", project.identity, render_project_protocol(project)
            )
    return artifacts


def _record_artifacts(workspace: Path, artifacts: dict[str, str]) -> None:
    records = {}
    for path, content in artifacts.items():
        records[path] = {
            "ownership": "curated" if "/projects/" in path else "managed",
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
    metadata = workspace / ".memory-system/installation.json"
    metadata.parent.mkdir()
    metadata.write_text(json.dumps({"artifacts": records}), encoding="utf-8")
