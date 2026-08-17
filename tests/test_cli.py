from __future__ import annotations

import json
from pathlib import Path

from memory_system.cli import main


def _write_workspace(workspace: Path) -> None:
    (workspace / "alpha").mkdir()
    (workspace / "AGENTS.md").write_text("", encoding="utf-8")
    (workspace / "alpha/AGENTS.md").write_text("", encoding="utf-8")
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
""",
        encoding="utf-8",
    )


def _args(workspace: Path, *command: str) -> list[str]:
    return [*command, "--workspace", str(workspace)]


def test_doctor_returns_zero_for_runtime_warnings(tmp_path: Path, capsys) -> None:
    _write_workspace(tmp_path)

    assert main(_args(tmp_path, "doctor")) == 0
    assert "warning" in capsys.readouterr().out


def test_doctor_returns_two_for_hard_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_workspace(tmp_path)
    import memory_system.runtime as runtime

    monkeypatch.setattr(runtime.sys, "platform", "win32")

    assert main(_args(tmp_path, "doctor")) == 2
    assert "error" in capsys.readouterr().out


def test_plan_is_read_only_and_returns_zero_when_conflict_free(tmp_path: Path, capsys) -> None:
    _write_workspace(tmp_path)

    assert main(_args(tmp_path, "plan", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["changes"]
    assert not payload["conflicts"]
    assert all("content" not in change for change in payload["changes"])
    assert not (tmp_path / ".memory-system").exists()


def test_plan_returns_three_for_conflicts(tmp_path: Path, capsys) -> None:
    _write_workspace(tmp_path)
    (tmp_path / "_memory/Context/project-registry.md").parent.mkdir(parents=True)
    (tmp_path / "_memory/Context/project-registry.md").write_text("local\n", encoding="utf-8")

    assert main(_args(tmp_path, "plan")) == 3
    assert "conflict" in capsys.readouterr().out


def test_apply_requires_yes_when_not_interactive(tmp_path: Path, capsys) -> None:
    _write_workspace(tmp_path)

    assert main(_args(tmp_path, "apply")) == 4
    assert "--yes" in capsys.readouterr().err
    assert not (tmp_path / ".memory-system").exists()


def test_apply_returns_three_for_rebuilt_conflicts(tmp_path: Path, capsys) -> None:
    _write_workspace(tmp_path)
    (tmp_path / "_memory/Context/project-registry.md").parent.mkdir(parents=True)
    (tmp_path / "_memory/Context/project-registry.md").write_text("local\n", encoding="utf-8")

    assert main(_args(tmp_path, "apply", "--yes")) == 3
    assert "conflict" in capsys.readouterr().out


def test_verify_returns_zero_then_five_for_failed_deployment(tmp_path: Path, capsys) -> None:
    _write_workspace(tmp_path)
    assert main(_args(tmp_path, "apply", "--yes")) == 0
    capsys.readouterr()

    assert main(_args(tmp_path, "verify")) == 0
    capsys.readouterr()
    (tmp_path / "_memory/Context/project-registry.md").write_text("drift\n", encoding="utf-8")

    assert main(_args(tmp_path, "verify")) == 5
    assert "FAIL" in capsys.readouterr().out


def test_rollback_returns_zero_then_six_for_invalid_metadata(tmp_path: Path, capsys) -> None:
    _write_workspace(tmp_path)
    assert main(_args(tmp_path, "apply", "--yes")) == 0
    capsys.readouterr()
    metadata = json.loads((tmp_path / ".memory-system/installation.json").read_text(encoding="utf-8"))
    backup = metadata["backup_dir"]

    assert main(_args(tmp_path, "rollback", "--backup", backup)) == 0
    capsys.readouterr()
    assert main(_args(tmp_path, "rollback", "--backup", backup)) == 6
    assert "invalid" in capsys.readouterr().err


def test_module_entry_point_delegates_to_cli_main() -> None:
    import memory_system.__main__ as module_entry
    import memory_system.cli as cli

    assert module_entry.main is cli.main
