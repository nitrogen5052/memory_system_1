"""Regression coverage for the v1 release review fixes."""

from __future__ import annotations

import json
from pathlib import Path

from memory_system.cli import main
from memory_system.installer import rollback_installation
from memory_system.planner import ChangeKind, build_plan
from memory_system.templates import render_project_protocol, render_root_protocol, upsert_managed_block
from memory_system.verifier import verify_workspace


def _write_workspace(workspace: Path, projects: tuple[str, ...] = ("alpha",)) -> None:
    for name in projects:
        (workspace / name).mkdir(exist_ok=True)
        (workspace / name / "AGENTS.md").write_text("project rules\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("root rules\n", encoding="utf-8")
    (workspace / "compatibility.toml").write_text(
        'schema_version = 1\nmethodology_version = "1.0"\n[claude_mem]\nminimum_version = "13.15.0"\ntested_versions = ["13.15.0"]\ndefault_worker_port = 37700\n',
        encoding="utf-8",
    )
    _write_manifest(workspace, "memory-system.toml", projects)


def _write_manifest(workspace: Path, name: str, projects: tuple[str, ...]) -> None:
    text = '''schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "workspace"
state = "_memory/Context/projects/workspace.md"
authority = ["AGENTS.md"]
'''
    for project in projects:
        text += f'''[[projects]]
identity = "{project}"
path = "{project}"
state = "_memory/Context/projects/{project}.md"
authority = ["{project}/AGENTS.md"]
'''
    (workspace / name).write_text(text, encoding="utf-8")


def _apply(workspace: Path, manifest: str = "memory-system.toml") -> None:
    assert main(("apply", "--yes", "--workspace", str(workspace), "--config", manifest)) == 0


def test_upgrade_preserves_unchanged_records_and_retires_removed_project_without_deleting_state(
    tmp_path: Path,
) -> None:
    _write_workspace(tmp_path)
    _apply(tmp_path)
    alpha_state = tmp_path / "_memory/Context/projects/alpha.md"
    alpha_state.write_text(alpha_state.read_text(encoding="utf-8") + "\ncurated retirement note\n", encoding="utf-8")
    alpha_authority = tmp_path / "alpha/AGENTS.md"
    alpha_authority.write_text("before\n" + alpha_authority.read_text(encoding="utf-8") + "after\n", encoding="utf-8")

    (tmp_path / "beta").mkdir()
    (tmp_path / "beta/AGENTS.md").write_text("beta rules\n", encoding="utf-8")
    _write_manifest(tmp_path, "memory-system.toml", ("alpha", "beta"))
    _apply(tmp_path)
    records = json.loads((tmp_path / ".memory-system/installation.json").read_text(encoding="utf-8"))["artifacts"]
    assert "alpha/AGENTS.md" in records
    assert "_memory/Context/projects/alpha.md" in records

    _write_manifest(tmp_path, "memory-system.toml", ("beta",))
    plan = build_plan(tmp_path)
    assert any(change.kind is ChangeKind.RETIRE_MANAGED_BLOCK for change in plan.changes)
    assert any(change.kind is ChangeKind.RETIRE_CURATED_STATE for change in plan.changes)
    _apply(tmp_path)

    assert alpha_state.read_text(encoding="utf-8").endswith("curated retirement note\n")
    retired_authority = alpha_authority.read_text(encoding="utf-8")
    assert retired_authority.startswith("before\nproject rules\n")
    assert retired_authority.endswith("after\n")
    assert "memory-system:" not in retired_authority
    records = json.loads((tmp_path / ".memory-system/installation.json").read_text(encoding="utf-8"))["artifacts"]
    assert "alpha/AGENTS.md" not in records
    assert "_memory/Context/projects/alpha.md" not in records


def test_managed_block_hash_ignores_unowned_authority_edits(tmp_path: Path) -> None:
    _write_workspace(tmp_path)
    _apply(tmp_path)
    authority = tmp_path / "AGENTS.md"
    authority.write_text("user header\n" + authority.read_text(encoding="utf-8"), encoding="utf-8")

    assert build_plan(tmp_path).conflicts == ()
    assert verify_workspace(tmp_path).ok


def test_upgrade_template_change_replaces_record_without_losing_other_records(
    tmp_path: Path, monkeypatch
) -> None:
    _write_workspace(tmp_path)
    _apply(tmp_path)
    import memory_system.planner as planner

    original = planner.render_root_protocol
    monkeypatch.setattr(planner, "render_root_protocol", lambda config: original(config) + "\nRelease template update.\n")

    plan = build_plan(tmp_path)
    assert [(change.path.as_posix(), change.kind) for change in plan.changes] == [
        ("AGENTS.md", ChangeKind.UPDATE_MANAGED_BLOCK)
    ]
    _apply(tmp_path)
    records = json.loads((tmp_path / ".memory-system/installation.json").read_text(encoding="utf-8"))["artifacts"]
    assert "AGENTS.md" in records
    assert "alpha/AGENTS.md" in records


def test_rerouting_authority_retires_only_old_project_block(tmp_path: Path) -> None:
    _write_workspace(tmp_path)
    _apply(tmp_path)
    old = tmp_path / "alpha/AGENTS.md"
    old.write_text("before\n" + old.read_text(encoding="utf-8") + "after\n", encoding="utf-8")
    new = tmp_path / "alpha/RULES.md"
    new.write_text("new rules\n", encoding="utf-8")
    manifest = tmp_path / "memory-system.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('authority = ["alpha/AGENTS.md"]', 'authority = ["alpha/RULES.md"]'),
        encoding="utf-8",
    )

    _apply(tmp_path)

    assert "memory-system:" not in old.read_text(encoding="utf-8")
    assert "memory-system:alpha:start" in new.read_text(encoding="utf-8")
    records = json.loads((tmp_path / ".memory-system/installation.json").read_text(encoding="utf-8"))["artifacts"]
    assert "alpha/AGENTS.md" not in records
    assert "alpha/RULES.md" in records


def test_planning_rejects_wrong_owner_and_nested_blocks_without_writing(tmp_path: Path) -> None:
    _write_workspace(tmp_path)
    authority = tmp_path / "alpha/AGENTS.md"
    authority.write_text(
        "<!-- memory-system:root:start -->\n"
        "<!-- memory-system:alpha:start -->\nbody\n"
        "<!-- memory-system:alpha:end -->\n"
        "<!-- memory-system:root:end -->\n",
        encoding="utf-8",
    )
    before = authority.read_bytes()

    plan = build_plan(tmp_path)

    assert plan.conflicts
    assert "marker" in plan.conflicts[0].reason or "owner" in plan.conflicts[0].reason
    assert authority.read_bytes() == before
    assert not (tmp_path / ".memory-system").exists()


def test_custom_manifest_is_used_for_apply_metadata_and_reapply(tmp_path: Path) -> None:
    _write_workspace(tmp_path, ())
    _write_manifest(tmp_path, "custom-memory.toml", ("alpha",))
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha/AGENTS.md").write_text("alpha rules\n", encoding="utf-8")
    (tmp_path / "memory-system.toml").unlink()

    _apply(tmp_path, "custom-memory.toml")

    assert verify_workspace(tmp_path, tmp_path / "custom-memory.toml").ok
    assert main(("apply", "--yes", "--workspace", str(tmp_path), "--config", "custom-memory.toml")) == 0


def test_protocols_and_state_template_include_durable_recall_and_freshness_contract(tmp_path: Path) -> None:
    _write_workspace(tmp_path)
    config = __import__("memory_system.config", fromlist=["load_workspace_config"]).load_workspace_config(tmp_path)
    root = render_root_protocol(config)
    project = render_project_protocol(config.projects[0])
    state = (tmp_path / "_memory/Context/projects/alpha.md")

    for text in (root, project):
        assert "Current explicit user instructions" in text
        assert "full observation retrieval" in text
        assert "deterministic operation ID" in text
        assert "superseding decision" in text
    _apply(tmp_path)
    rendered = state.read_text(encoding="utf-8")
    assert "canonical root" in rendered
    assert "last_verified" in rendered
    assert "last_reconciled" in rendered


def test_upgrade_rollback_restores_only_latest_transaction_and_keeps_unchanged_files(tmp_path: Path, monkeypatch) -> None:
    _write_workspace(tmp_path)
    _apply(tmp_path)
    unchanged = tmp_path / "_memory/Context/projects/alpha.md"
    unchanged_bytes = unchanged.read_bytes()
    import memory_system.planner as planner

    original = planner.render_root_protocol
    monkeypatch.setattr(planner, "render_root_protocol", lambda config: original(config) + "\nUpgrade body.\n")
    second_plan = build_plan(tmp_path)
    from memory_system.installer import apply_plan

    second = apply_plan(second_plan, confirmed=True)
    assert second.backup_dir is not None
    rollback_installation(tmp_path, second.backup_dir)

    assert unchanged.read_bytes() == unchanged_bytes
    assert "Upgrade body." not in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


def test_retirement_rejects_extra_owner_even_when_recorded_block_digest_matches(tmp_path: Path) -> None:
    _write_workspace(tmp_path)
    _apply(tmp_path)
    authority = tmp_path / "alpha/AGENTS.md"
    authority.write_text(upsert_managed_block(authority.read_text(encoding="utf-8"), "root", "other owner\n"), encoding="utf-8")
    _write_manifest(tmp_path, "memory-system.toml", ())

    plan = build_plan(tmp_path)

    assert plan.conflicts
    assert "owner" in plan.conflicts[0].reason or "marker" in plan.conflicts[0].reason


def test_verifier_rejects_wrong_canonical_root_and_invalid_freshness_values(tmp_path: Path) -> None:
    _write_workspace(tmp_path)
    _apply(tmp_path)
    state = tmp_path / "_memory/Context/projects/alpha.md"
    state.write_text(
        state.read_text(encoding="utf-8")
        .replace("canonical root: `alpha`", "canonical root: `wrong`")
        .replace("last_verified: null", "last_verified: yesterday")
        .replace("last_reconciled: null", "last_reconciled: 2026-08-16T12:00:00"),
        encoding="utf-8",
    )

    report = verify_workspace(tmp_path)

    assert not report.ok
    assert next(check for check in report.checks if check.code == "state-schema").passed is False
