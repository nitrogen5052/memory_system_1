import hashlib
import json
import os
from pathlib import Path, PurePosixPath

import pytest

from memory_system.installer import ApplyError, apply_plan, rollback_installation
from memory_system.planner import ChangeKind, Plan, PlannedChange, build_plan


def _sha256(content: str | None) -> str | None:
    return hashlib.sha256(content.encode()).hexdigest() if content is not None else None


def _change(path: str, kind: ChangeKind, before: str | None, after: str | None) -> PlannedChange:
    return PlannedChange(
        PurePosixPath(path), kind, _sha256(before), _sha256(after), after, "test change"
    )


@pytest.fixture
def prepared_plan(tmp_path: Path) -> Plan:
    (tmp_path / "AGENTS.md").write_text("before authority\n", encoding="utf-8")
    (tmp_path / "managed.md").write_text("before managed\n", encoding="utf-8")
    (tmp_path / "memory-system.toml").write_text(
        '''
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "workspace"
state = "_memory/Context/projects/workspace.md"
authority = ["AGENTS.md"]
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
    return Plan(
        tmp_path,
        (
            _change("AGENTS.md", ChangeKind.UPDATE_MANAGED_BLOCK, "before authority\n", "after authority\n"),
            _change("managed.md", ChangeKind.UPDATE_GENERATED, "before managed\n", "after managed\n"),
            _change("_memory/Context/projects/workspace.md", ChangeKind.CREATE, None, "curated state\n"),
        ),
        (),
    )


def test_apply_writes_atomically_backups_relative_paths_and_preserves_mode(prepared_plan: Plan) -> None:
    target = prepared_plan.workspace / "managed.md"
    target.chmod(0o640)

    result = apply_plan(prepared_plan, confirmed=True)

    assert result.changed_paths == (
        PurePosixPath("AGENTS.md"),
        PurePosixPath("_memory/Context/projects/workspace.md"),
        PurePosixPath("managed.md"),
    )
    assert result.adopted_paths == ()
    assert result.backup_dir is not None and not result.backup_dir.is_absolute()
    assert ".." not in result.backup_dir.parts
    assert target.read_text(encoding="utf-8") == "after managed\n"
    assert os.stat(target).st_mode & 0o777 == 0o640
    backup = prepared_plan.workspace.joinpath(*result.backup_dir.parts)
    assert (backup / "files/managed.md").read_bytes() == b"before managed\n"
    assert (backup / "files/AGENTS.md").read_bytes() == b"before authority\n"
    assert not (backup / "files/_memory/Context/projects/workspace.md").exists()

    metadata = json.loads(
        (prepared_plan.workspace / ".memory-system/installation.json").read_text(encoding="utf-8")
    )
    assert list(metadata) == sorted(metadata)
    assert metadata["backup_dir"] == result.backup_dir.as_posix()
    assert metadata["methodology_version"] == "1.0"
    assert metadata["schema_version"] == 1
    assert metadata["artifacts"]["_memory/Context/projects/workspace.md"]["ownership"] == "curated"
    assert metadata["artifacts"]["managed.md"]["ownership"] == "managed"


def test_apply_backs_up_existing_installation_metadata_on_upgrade(prepared_plan: Plan) -> None:
    prior = b'{"artifacts": {"legacy.md": {}}}\n'
    metadata = prepared_plan.workspace / ".memory-system/installation.json"
    metadata.parent.mkdir()
    metadata.write_bytes(prior)

    result = apply_plan(prepared_plan, confirmed=True)

    assert result.backup_dir is not None
    backup = prepared_plan.workspace.joinpath(*result.backup_dir.parts)
    assert (backup / "files/.memory-system/installation.json").read_bytes() == prior


def test_adoption_records_ownership_without_rewriting_existing_bytes(tmp_path: Path) -> None:
    target = tmp_path / "registry.md"
    original = b"already generated\r\n"
    target.write_bytes(original)
    plan = Plan(
        tmp_path,
        (_change("registry.md", ChangeKind.ADOPT_EXISTING, original.decode(), None),),
        (),
    )

    result = apply_plan(plan, confirmed=True)

    assert result.changed_paths == ()
    assert result.adopted_paths == (PurePosixPath("registry.md"),)
    assert result.backup_dir is not None
    assert target.read_bytes() == original
    metadata = json.loads((tmp_path / ".memory-system/installation.json").read_text(encoding="utf-8"))
    assert metadata["artifacts"]["registry.md"] == {
        "applied_sha256": _sha256(original.decode()),
        "change_kind": "adopt-existing",
        "original_sha256": _sha256(original.decode()),
        "ownership": "managed",
    }


def test_rollback_of_adoption_leaves_post_adoption_bytes_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "registry.md"
    target.write_bytes(b"already generated\n")
    plan = Plan(
        tmp_path,
        (_change("registry.md", ChangeKind.ADOPT_EXISTING, "already generated\n", None),),
        (),
    )
    applied = apply_plan(plan, confirmed=True)
    assert applied.backup_dir is not None
    target.write_bytes(b"user-owned update\n")

    result = rollback_installation(tmp_path, applied.backup_dir)

    assert result.adopted_paths == (PurePosixPath("registry.md"),)
    assert target.read_bytes() == b"user-owned update\n"
    assert not (tmp_path / ".memory-system/installation.json").exists()


def test_apply_refuses_conflicts_without_writing(prepared_plan: Plan) -> None:
    plan = Plan(prepared_plan.workspace, prepared_plan.changes, (_change("bad.md", ChangeKind.CONFLICT, None, None),))

    with pytest.raises(ApplyError, match="conflicts"):
        apply_plan(plan, confirmed=True)

    assert not (plan.workspace / ".memory-system").exists()


def test_apply_refuses_stale_plan(prepared_plan: Plan) -> None:
    target = prepared_plan.workspace / "AGENTS.md"
    target.write_text("changed after planning\n", encoding="utf-8")

    with pytest.raises(ApplyError, match="changed since plan"):
        apply_plan(prepared_plan, confirmed=True)


def test_apply_restores_all_bytes_after_injected_second_write_failure(
    prepared_plan: Plan, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memory_system.installer as installer

    real_write = installer._atomic_write
    calls = 0

    def fail_second_write(path: Path, content: bytes, mode: int | None) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second-write failure")
        real_write(path, content, mode)

    monkeypatch.setattr(installer, "_atomic_write", fail_second_write)

    with pytest.raises(ApplyError, match="injected second-write failure"):
        apply_plan(prepared_plan, confirmed=True)

    assert (prepared_plan.workspace / "AGENTS.md").read_bytes() == b"before authority\n"
    assert (prepared_plan.workspace / "managed.md").read_bytes() == b"before managed\n"
    assert not (prepared_plan.workspace / "_memory/Context/projects/workspace.md").exists()
    assert not (prepared_plan.workspace / ".memory-system/installation.json").exists()


def test_rollback_restores_original_bytes_prior_metadata_and_adopted_bytes(
    prepared_plan: Plan,
) -> None:
    adopted = prepared_plan.workspace / "registry.md"
    adopted.write_bytes(b"adopted bytes\r\n")
    prior_metadata = b'{"artifacts": {"legacy.md": {}}}\n'
    metadata = prepared_plan.workspace / ".memory-system/installation.json"
    metadata.parent.mkdir()
    metadata.write_bytes(prior_metadata)
    plan = Plan(
        prepared_plan.workspace,
        (*prepared_plan.changes, _change("registry.md", ChangeKind.ADOPT_EXISTING, "adopted bytes\r\n", None)),
        (),
    )
    applied = apply_plan(plan, confirmed=True)

    result = rollback_installation(plan.workspace, applied.backup_dir)

    assert result.changed_paths == (
        PurePosixPath("AGENTS.md"),
        PurePosixPath("_memory/Context/projects/workspace.md"),
        PurePosixPath("managed.md"),
    )
    assert result.adopted_paths == (PurePosixPath("registry.md"),)
    assert (plan.workspace / "AGENTS.md").read_bytes() == b"before authority\n"
    assert (plan.workspace / "managed.md").read_bytes() == b"before managed\n"
    assert not (plan.workspace / "_memory/Context/projects/workspace.md").exists()
    assert adopted.read_bytes() == b"adopted bytes\r\n"
    assert metadata.read_bytes() == prior_metadata


def test_rollback_refuses_incomplete_backup_without_deleting_a_rewritten_file(
    prepared_plan: Plan,
) -> None:
    applied = apply_plan(prepared_plan, confirmed=True)
    assert applied.backup_dir is not None
    backup = prepared_plan.workspace.joinpath(*applied.backup_dir.parts)
    (backup / "files/managed.md").unlink()

    with pytest.raises(ApplyError, match="incomplete"):
        rollback_installation(prepared_plan.workspace, applied.backup_dir)

    assert (prepared_plan.workspace / "managed.md").read_bytes() == b"after managed\n"


def test_rollback_preflights_later_symlink_before_restoring_earlier_target(prepared_plan: Plan) -> None:
    applied = apply_plan(prepared_plan, confirmed=True)
    assert applied.backup_dir is not None
    outside = prepared_plan.workspace.parent / "outside-managed.md"
    outside.write_bytes(b"outside\n")
    managed = prepared_plan.workspace / "managed.md"
    managed.unlink()
    managed.symlink_to(outside)

    with pytest.raises(ApplyError, match="symlink"):
        rollback_installation(prepared_plan.workspace, applied.backup_dir)

    assert (prepared_plan.workspace / "AGENTS.md").read_bytes() == b"after authority\n"
    assert outside.read_bytes() == b"outside\n"


def test_rollback_requires_archived_prior_metadata_before_mutating(prepared_plan: Plan) -> None:
    prior = b'{"artifacts": {"legacy.md": {}}}\n'
    metadata = prepared_plan.workspace / ".memory-system/installation.json"
    metadata.parent.mkdir()
    metadata.write_bytes(prior)
    applied = apply_plan(prepared_plan, confirmed=True)
    assert applied.backup_dir is not None
    backup = prepared_plan.workspace.joinpath(*applied.backup_dir.parts)
    (backup / "files/.memory-system/installation.json").unlink()

    with pytest.raises(ApplyError, match="prior metadata"):
        rollback_installation(prepared_plan.workspace, applied.backup_dir)

    assert (prepared_plan.workspace / "AGENTS.md").read_bytes() == b"after authority\n"
    assert metadata.exists()


def test_rollback_refuses_schema_two_metadata_without_transaction_journal(prepared_plan: Plan) -> None:
    applied = apply_plan(prepared_plan, confirmed=True)
    assert applied.backup_dir is not None
    metadata = prepared_plan.workspace / ".memory-system/installation.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["metadata_schema_version"] = 2
    metadata.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ApplyError, match="safe transaction rollback journal"):
        rollback_installation(prepared_plan.workspace, applied.backup_dir)


def test_apply_recovers_when_atomic_write_raises_after_replace(
    prepared_plan: Plan, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memory_system.installer as installer

    real_write = installer._atomic_write

    def replace_then_fail(path: Path, content: bytes, mode: int | None) -> None:
        real_write(path, content, mode)
        raise OSError("injected post-replace failure")

    monkeypatch.setattr(installer, "_atomic_write", replace_then_fail)

    with pytest.raises(ApplyError, match="post-replace"):
        apply_plan(prepared_plan, confirmed=True)

    assert (prepared_plan.workspace / "AGENTS.md").read_bytes() == b"before authority\n"
    assert not (prepared_plan.workspace / ".memory-system/installation.json").exists()


def test_rollback_refuses_an_edited_installed_artifact(prepared_plan: Plan) -> None:
    applied = apply_plan(prepared_plan, confirmed=True)
    assert applied.backup_dir is not None
    agents = prepared_plan.workspace / "AGENTS.md"
    agents.write_bytes(b"user edit after install\n")

    with pytest.raises(ApplyError, match="changed since installation"):
        rollback_installation(prepared_plan.workspace, applied.backup_dir)

    assert agents.read_bytes() == b"user edit after install\n"
    assert (prepared_plan.workspace / "managed.md").read_bytes() == b"after managed\n"


def test_rollback_recovers_pre_rollback_state_after_mid_restore_failure(
    prepared_plan: Plan, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memory_system.installer as installer

    applied = apply_plan(prepared_plan, confirmed=True)
    assert applied.backup_dir is not None
    real_write = installer._atomic_write
    calls = 0

    def fail_second_restore(path: Path, content: bytes, mode: int | None) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected rollback failure")
        real_write(path, content, mode)

    monkeypatch.setattr(installer, "_atomic_write", fail_second_restore)

    with pytest.raises(ApplyError, match="injected rollback failure"):
        rollback_installation(prepared_plan.workspace, applied.backup_dir)

    assert (prepared_plan.workspace / "AGENTS.md").read_bytes() == b"after authority\n"
    assert (prepared_plan.workspace / "managed.md").read_bytes() == b"after managed\n"
    assert (prepared_plan.workspace / "_memory/Context/projects/workspace.md").read_bytes() == b"curated state\n"
    assert (prepared_plan.workspace / ".memory-system/installation.json").exists()


@pytest.mark.parametrize("backup_dir", [PurePosixPath("/tmp/backup"), PurePosixPath("../backup")])
def test_rollback_rejects_unsafe_backup_paths(prepared_plan: Plan, backup_dir: PurePosixPath) -> None:
    with pytest.raises(ApplyError, match="relative"):
        rollback_installation(prepared_plan.workspace, backup_dir)


def test_apply_rejects_metadata_symlink_outside_workspace(prepared_plan: Plan) -> None:
    outside = prepared_plan.workspace.parent / "outside-metadata.json"
    outside.write_text("outside\n", encoding="utf-8")
    metadata = prepared_plan.workspace / ".memory-system/installation.json"
    metadata.parent.mkdir()
    metadata.symlink_to(outside)

    with pytest.raises(ApplyError, match="symlink"):
        apply_plan(prepared_plan, confirmed=True)

    assert outside.read_text(encoding="utf-8") == "outside\n"
