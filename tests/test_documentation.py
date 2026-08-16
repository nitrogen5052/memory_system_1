from __future__ import annotations

import importlib.resources
import os
from pathlib import Path
import shlex
import subprocess
import sys

from memory_system.cli import main
from memory_system.config import load_workspace_config


REPOSITORY = Path(__file__).resolve().parents[1]
EXAMPLE = REPOSITORY / "examples/multi-project-workspace"
README = REPOSITORY / "README.md"
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


def _readme_memory_system_commands() -> list[tuple[str, ...]]:
    commands = []
    for line in README.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("memory-system "):
            commands.append(tuple(shlex.split(line)[1:]))
    return commands


def test_readme_memory_system_commands_parse_through_main(tmp_path: Path, capsys) -> None:
    commands = _readme_memory_system_commands()

    assert commands
    for command in commands:
        status = main((*command, "--workspace", str(tmp_path)))
        assert status in {0, 2, 3, 4, 5, 6}
        capsys.readouterr()


def test_local_document_links_exist() -> None:
    text = README.read_text(encoding="utf-8")

    for target in ("docs/installation.md", "docs/architecture.md", "docs/upgrades.md", "docs/recovery.md"):
        assert f"]({target})" in text
        assert (REPOSITORY / target).is_file()


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
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPOSITORY, check=True, capture_output=True
    ).stdout.split(b"\0")

    for relative in filter(None, tracked):
        content = (REPOSITORY / os.fsdecode(relative)).read_text(encoding="utf-8")
        assert not any(pattern in content for pattern in BLOCKED_PATTERNS)


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


def test_source_templates_are_available_to_the_renderer() -> None:
    templates = importlib.resources.files("memory_system").joinpath("templates")

    assert {item.name for item in templates.iterdir()} == TEMPLATES
