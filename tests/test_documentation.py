from __future__ import annotations

import importlib.resources
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from memory_system.cli import main
from memory_system.config import load_workspace_config


REPOSITORY = Path(__file__).resolve().parents[1]
EXAMPLE = REPOSITORY / "examples/multi-project-workspace"
README = REPOSITORY / "README.md"
DOCKERFILE = REPOSITORY / "Dockerfile"
TEMPLATES = {
    "active-state-index.md",
    "project-memory-protocol.md",
    "project-registry.md",
    "project-state.md",
    "root-memory-protocol.md",
}
BLOCKED_PATTERNS = (
    ".claude-mem/" + "claude-mem.db",
    "CLAUDE_MEM_" + "*_API_KEY",
    "sync " + "token",
    "/" + "Users/",
    "/" + "home/",
)
_SOURCE_SCAN_EXCLUDED_PARTS = {
    ".git",
    ".memory-system",
    ".pytest_cache",
    ".superpowers",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}
_SOURCE_TEXT_SUFFIXES = {"", ".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}


def _repository_text_files() -> tuple[Path, ...]:
    if (REPOSITORY / ".git").is_dir() and shutil.which("git") is not None:
        tracked = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPOSITORY, check=True, capture_output=True
        ).stdout.split(b"\0")
        return tuple(REPOSITORY / os.fsdecode(relative) for relative in filter(None, tracked))

    return tuple(
        path
        for path in sorted(REPOSITORY.rglob("*"))
        if path.is_file()
        and path.suffix in _SOURCE_TEXT_SUFFIXES
        and not any(
            part in _SOURCE_SCAN_EXCLUDED_PARTS or part.endswith(".egg-info")
            for part in path.relative_to(REPOSITORY).parts
        )
    )


def _readme_memory_system_commands() -> list[tuple[str, ...]]:
    commands = []
    for line in README.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(".venv/bin/memory-system "):
            commands.append(tuple(shlex.split(line)[1:]))
    return commands


def test_readme_memory_system_commands_parse_through_main(tmp_path: Path, capsys) -> None:
    commands = _readme_memory_system_commands()

    assert "python3 -m venv .venv" in README.read_text(encoding="utf-8")
    assert ".venv/bin/python -m pip install ." in README.read_text(encoding="utf-8")
    assert commands
    from memory_system.cli import _parser

    for command in commands:
        _parser().parse_args((*command, "--workspace", str(tmp_path)))


def test_documented_quick_start_commands_succeed_in_order(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "workspace"
    shutil.copytree(EXAMPLE, workspace)

    for command in (
        ("doctor",),
        ("plan",),
        ("apply", "--yes"),
        ("verify",),
        ("apply", "--yes"),
    ):
        assert main((*command, "--workspace", str(workspace))) == 0
        capsys.readouterr()


def test_local_document_links_exist() -> None:
    text = README.read_text(encoding="utf-8")

    for target in ("docs/installation.md", "docs/architecture.md", "docs/upgrades.md", "docs/recovery.md"):
        assert f"]({target})" in text
        assert (REPOSITORY / target).is_file()


def test_onboarding_and_upgrade_docs_are_reproducible() -> None:
    readme = README.read_text(encoding="utf-8")
    installation = (REPOSITORY / "docs/installation.md").read_text(encoding="utf-8")
    upgrades = (REPOSITORY / "docs/upgrades.md").read_text(encoding="utf-8")

    for text in (readme, installation, upgrades):
        assert "https://github.com/nitrogen5052/memory_system_1.git" in text
    assert "python3 -m venv .venv" in installation
    assert ".venv/bin/python -m pip install ." in installation
    assert "python3 -m venv .venv" in upgrades
    assert ".venv/bin/python -m pip install ." in upgrades
    assert "https://github.com/thedotmack/claude-mem#quick-start" in installation


def test_documented_upgrade_workflow_targets_an_explicit_workspace(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    workspace = tmp_path / "managed-workspace"
    checkout = tmp_path / "tool-checkout"
    shutil.copytree(EXAMPLE, workspace)
    checkout.mkdir()
    upgrades = (REPOSITORY / "docs/upgrades.md").read_text(encoding="utf-8")

    assert 'WORKSPACE="/absolute/path/to/existing-workspace"' in upgrades
    assert '--workspace "$WORKSPACE"' in upgrades
    monkeypatch.chdir(checkout)
    for command in (("doctor",), ("plan",), ("apply", "--yes"), ("verify",)):
        assert main((*command, "--workspace", str(workspace))) == 0
        capsys.readouterr()


def test_example_has_two_isolated_child_projects() -> None:
    config = load_workspace_config(EXAMPLE)

    assert config.root_identity == "multi-project-workspace"
    assert config.root_state.as_posix() == "_memory/Context/projects/multi-project-workspace.md"
    assert tuple(path.as_posix() for path in config.root_authority) == ("AGENTS.md",)
    assert [(project.identity, project.path.as_posix()) for project in config.projects] == [
        ("alpha", "alpha"),
        ("beta", "beta"),
    ]


def test_tracked_examples_and_repository_files_contain_no_portability_secrets() -> None:
    for path in _repository_text_files():
        content = path.read_text(encoding="utf-8")
        assert not any(pattern in content for pattern in BLOCKED_PATTERNS)


def test_portability_scan_supports_source_snapshots_without_git(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _command: None)

    files = _repository_text_files()

    assert README in files
    assert REPOSITORY / "memory_system/config.py" in files
    assert not any("__pycache__" in path.parts for path in files)


def test_templates_are_packaged_in_an_installed_wheel(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    environment = tmp_path / "environment"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheelhouse),
            ".",
        ],
        cwd=REPOSITORY,
        check=True,
    )
    wheel = next(wheelhouse.glob("portable_project_memory-*.whl"))
    subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], check=True
    )
    console_script = environment / ("Scripts/memory-system.exe" if os.name == "nt" else "bin/memory-system")
    help_result = subprocess.run([str(console_script), "--help"], check=True, capture_output=True, text=True)
    plan_result = subprocess.run(
        [str(console_script), "plan", "--workspace", str(EXAMPLE)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        [
            str(python),
            "-c",
            "from importlib import resources; "
            "print('\\n'.join(sorted(item.name for item in resources.files('memory_system').joinpath('templates').iterdir())))",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert set(result.stdout.splitlines()) == TEMPLATES
    assert "usage: memory-system" in help_result.stdout
    assert "changes" in plan_result.stdout.casefold()


def test_source_templates_are_available_to_the_renderer() -> None:
    templates = importlib.resources.files("memory_system").joinpath("templates")

    assert {item.name for item in templates.iterdir()} == TEMPLATES


def test_docker_clean_room_reapply_requires_exact_no_change_output() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert 'reapply_output="$(memory-system apply --yes --workspace /tmp/memory-system-workspace)"' in dockerfile
    assert 'test "$reapply_output" = "No changes"' in dockerfile
