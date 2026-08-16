from __future__ import annotations

import hashlib
from pathlib import Path
import shutil

from memory_system.cli import main


def _write_workspace(workspace: Path) -> None:
    (workspace / "project").mkdir()
    (workspace / "AGENTS.md").write_text("# Workspace\n", encoding="utf-8")
    (workspace / "project/AGENTS.md").write_text("# Project\n", encoding="utf-8")
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
identity = "project"
path = "project"
state = "_memory/Context/projects/project.md"
authority = ["project/AGENTS.md"]
""",
        encoding="utf-8",
    )


def _hashes(workspace: Path) -> dict[str, str]:
    return {
        path.relative_to(workspace).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in workspace.rglob("*")
        if path.is_file()
    }


def test_fresh_apply_verify_and_reapply_are_idempotent(tmp_path: Path, capsys) -> None:
    _write_workspace(tmp_path)
    args = ("--workspace", str(tmp_path))

    assert main(("plan", *args)) == 0
    assert main(("apply", "--yes", *args)) == 0
    assert main(("verify", *args)) == 0
    capsys.readouterr()
    first_hashes = _hashes(tmp_path)

    assert main(("apply", "--yes", *args)) == 0
    second_output = capsys.readouterr().out

    assert "No changes" in second_output
    assert _hashes(tmp_path) == first_hashes


def test_canonical_example_apply_verify_and_reapply_are_idempotent(
    tmp_path: Path, capsys
) -> None:
    example = Path(__file__).resolve().parents[1] / "examples/multi-project-workspace"
    workspace = tmp_path / "multi-project-workspace"
    shutil.copytree(example, workspace)
    args = ("--workspace", str(workspace))

    assert main(("plan", *args)) == 0
    assert main(("apply", "--yes", *args)) == 0
    assert main(("verify", *args)) == 0
    capsys.readouterr()
    first_hashes = _hashes(workspace)

    assert main(("apply", "--yes", *args)) == 0
    second_output = capsys.readouterr().out

    assert "No changes" in second_output
    assert _hashes(workspace) == first_hashes
