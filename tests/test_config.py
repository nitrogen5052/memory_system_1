from pathlib import Path

import pytest

from memory_system.config import ConfigError, load_compatibility, load_workspace_config


def test_loads_a_valid_two_project_manifest(tmp_path: Path) -> None:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "AGENTS.md").touch()
    (tmp_path / "alpha" / "AGENTS.md").touch()
    (tmp_path / "beta" / "RULES.md").touch()
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
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
authority = ["beta/RULES.md"]
""")
    (tmp_path / "compatibility.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[claude_mem]
minimum_version = "13.15.0"
tested_versions = ["13.15.0"]
default_worker_port = 37700
""")

    config = load_workspace_config(tmp_path)
    compatibility = load_compatibility(tmp_path / "compatibility.toml")

    assert config.root_identity == "root"
    assert config.projects[0].path.as_posix() == "alpha"
    assert config.projects[1].authority[0].as_posix() == "beta/RULES.md"
    assert compatibility.minimum_claude_mem_version == "13.15.0"


def test_rejects_absolute_paths(tmp_path: Path) -> None:
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "/_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
""")
    with pytest.raises(ConfigError, match="absolute"):
        load_workspace_config(tmp_path)


def test_rejects_parent_traversal(tmp_path: Path) -> None:
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["../AGENTS.md"]
""")
    with pytest.raises(ConfigError, match="\\.\\."):
        load_workspace_config(tmp_path)


def test_rejects_invalid_identity(tmp_path: Path) -> None:
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root name"
state = "_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
""")
    with pytest.raises(ConfigError, match="identity"):
        load_workspace_config(tmp_path)


def test_rejects_duplicate_identities(tmp_path: Path) -> None:
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
[[projects]]
identity = "alpha"
path = "alpha"
state = "_memory/Context/projects/alpha.md"
authority = ["alpha/AGENTS.md"]
[[projects]]
identity = "alpha"
path = "beta"
state = "_memory/Context/projects/beta.md"
authority = ["beta/AGENTS.md"]
""")
    with pytest.raises(ConfigError, match="duplicate identity"):
        load_workspace_config(tmp_path)


def test_rejects_case_folded_duplicate_identities(tmp_path: Path) -> None:
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
[[projects]]
identity = "Alpha"
path = "alpha"
state = "_memory/Context/projects/alpha.md"
authority = ["alpha/AGENTS.md"]
[[projects]]
identity = "alpha"
path = "beta"
state = "_memory/Context/projects/beta.md"
authority = ["beta/AGENTS.md"]
""")
    with pytest.raises(ConfigError, match="duplicate identity"):
        load_workspace_config(tmp_path)


def test_rejects_duplicate_canonical_roots(tmp_path: Path) -> None:
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
[[projects]]
identity = "alpha"
path = "alpha"
state = "_memory/Context/projects/alpha.md"
authority = ["alpha/AGENTS.md"]
[[projects]]
identity = "beta"
path = "alpha/."
state = "_memory/Context/projects/beta.md"
authority = ["alpha/OTHER.md"]
""")
    with pytest.raises(ConfigError, match="duplicate project root"):
        load_workspace_config(tmp_path)


def test_rejects_overlapping_projects(tmp_path):
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
[[projects]]
identity = "alpha"
path = "alpha"
state = "_memory/Context/projects/alpha.md"
authority = ["alpha/AGENTS.md"]
[[projects]]
identity = "nested"
path = "alpha/nested"
state = "_memory/Context/projects/nested.md"
authority = ["alpha/nested/AGENTS.md"]
""")
    with pytest.raises(ConfigError, match="overlap"):
        load_workspace_config(tmp_path)


def test_rejects_symlink_aliases_or_escapes(tmp_path: Path) -> None:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").symlink_to(tmp_path / "alpha", target_is_directory=True)
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
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
""")
    with pytest.raises(ConfigError, match="symlink|alias"):
        load_workspace_config(tmp_path)


def test_rejects_authority_symlink_escape(tmp_path: Path) -> None:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "AGENTS.md").touch()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.touch()
    (tmp_path / "alpha" / "AGENTS.md").symlink_to(outside)
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
[[projects]]
identity = "alpha"
path = "alpha"
state = "_memory/Context/projects/alpha.md"
authority = ["alpha/AGENTS.md"]
""")
    (tmp_path / "compatibility.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[claude_mem]
minimum_version = "13.15.0"
tested_versions = ["13.15.0"]
default_worker_port = 37700
""")
    with pytest.raises(ConfigError, match="escapes"):
        load_workspace_config(tmp_path)


@pytest.mark.parametrize(("field", "value"), [("schema_version", "2"), ("methodology_version", '\"2.0\"')])
def test_rejects_unsupported_versions(tmp_path: Path, field: str, value: str) -> None:
    version_line = f"{field} = {value}"
    (tmp_path / "memory-system.toml").write_text(f"""
{version_line}
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
""" if field == "schema_version" else f"""
schema_version = 1
{version_line}
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
""")
    (tmp_path / "compatibility.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[claude_mem]
minimum_version = "13.15.0"
tested_versions = ["13.15.0"]
default_worker_port = 37700
""")
    with pytest.raises(ConfigError, match="unsupported"):
        load_workspace_config(tmp_path)


@pytest.mark.parametrize("missing", ["state", "authority"])
def test_requires_root_state_and_authority(tmp_path: Path, missing: str) -> None:
    body = "authority = [\"AGENTS.md\"]" if missing == "state" else 'state = "_memory/Context/projects/root.md"'
    (tmp_path / "memory-system.toml").write_text(f"""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
{body}
""")
    with pytest.raises(ConfigError, match=missing):
        load_workspace_config(tmp_path)


def test_requires_child_authority(tmp_path: Path) -> None:
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
[[projects]]
identity = "alpha"
path = "alpha"
state = "_memory/Context/projects/alpha.md"
""")
    with pytest.raises(ConfigError, match="authority"):
        load_workspace_config(tmp_path)


def test_rejects_duplicate_or_case_folded_duplicate_state_ownership(tmp_path: Path) -> None:
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/Root.md"
authority = ["AGENTS.md"]
[[projects]]
identity = "alpha"
path = "alpha"
state = "_memory/Context/projects/root.md"
authority = ["alpha/AGENTS.md"]
""")
    with pytest.raises(ConfigError, match="duplicate state"):
        load_workspace_config(tmp_path)


def test_rejects_duplicate_or_case_folded_duplicate_authority_ownership(tmp_path: Path) -> None:
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
[[projects]]
identity = "alpha"
path = "alpha"
state = "_memory/Context/projects/alpha.md"
authority = ["alpha/AGENTS.md", "alpha/agents.md"]
""")
    with pytest.raises(ConfigError, match="duplicate authority"):
        load_workspace_config(tmp_path)


def test_rejects_child_authority_outside_its_project_root(tmp_path: Path) -> None:
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
[[projects]]
identity = "alpha"
path = "alpha"
state = "_memory/Context/projects/alpha.md"
authority = ["beta/AGENTS.md"]
""")
    with pytest.raises(ConfigError, match="child authority"):
        load_workspace_config(tmp_path)


def test_rejects_root_authority_inside_a_child_root(tmp_path: Path) -> None:
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["alpha/AGENTS.md"]
[[projects]]
identity = "alpha"
path = "alpha"
state = "_memory/Context/projects/alpha.md"
authority = ["alpha/RULES.md"]
""")
    with pytest.raises(ConfigError, match="root authority"):
        load_workspace_config(tmp_path)


@pytest.mark.parametrize("state", ["_memory/Context/root.md", "state.md"])
def test_rejects_state_outside_project_state_directory(tmp_path: Path, state: str) -> None:
    (tmp_path / "memory-system.toml").write_text(f"""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "{state}"
authority = ["AGENTS.md"]
""")
    with pytest.raises(ConfigError, match="_memory/Context/projects"):
        load_workspace_config(tmp_path)


def test_rejects_child_state_outside_project_state_directory(tmp_path: Path) -> None:
    (tmp_path / "memory-system.toml").write_text("""
schema_version = 1
methodology_version = "1.0"
[workspace]
root_identity = "root"
state = "_memory/Context/projects/root.md"
authority = ["AGENTS.md"]
[[projects]]
identity = "alpha"
path = "alpha"
state = "_memory/Context/alpha.md"
authority = ["alpha/AGENTS.md"]
""")
    with pytest.raises(ConfigError, match="_memory/Context/projects"):
        load_workspace_config(tmp_path)
