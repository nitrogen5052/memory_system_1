from importlib import resources
from pathlib import PurePosixPath

import pytest

from memory_system.config import ProjectSpec, WorkspaceConfig
from memory_system.templates import (
    ManagedBlockConflict,
    render_active_index,
    render_project_protocol,
    render_project_state,
    render_registry,
    render_root_protocol,
    render_root_state,
    upsert_managed_block,
)


@pytest.fixture
def config() -> WorkspaceConfig:
    return WorkspaceConfig(
        schema_version=1,
        methodology_version="1.0",
        root_identity="workspace",
        root_state=PurePosixPath("_memory/Context/projects/workspace.md"),
        root_authority=(PurePosixPath("AGENTS.md"),),
        projects=(
            ProjectSpec(
                identity="zeta",
                path=PurePosixPath("zeta"),
                state=PurePosixPath("_memory/Context/projects/zeta.md"),
                authority=(PurePosixPath("zeta/AGENTS.md"),),
            ),
            ProjectSpec(
                identity="Alpha",
                path=PurePosixPath("alpha"),
                state=PurePosixPath("_memory/Context/projects/alpha.md"),
                authority=(PurePosixPath("alpha/RULES.md"),),
            ),
        ),
    )


def test_rendered_registry_routes_root_and_projects_once_in_identity_order(
    config: WorkspaceConfig,
) -> None:
    registry = render_registry(config)

    assert registry.index("| workspace |") < registry.index("| Alpha |") < registry.index("| zeta |")
    assert registry.count("| workspace |") == 1
    assert registry.count("| Alpha |") == 1
    assert registry.count("| zeta |") == 1
    assert "_memory/Context/projects/workspace.md" in registry
    assert "_memory/Context/projects/alpha.md" in registry
    assert "_memory/Context/projects/zeta.md" in registry


def test_rendered_state_documents_have_exact_headings_in_order(
    config: WorkspaceConfig,
) -> None:
    headings = [
        "Project",
        "Authority",
        "Active Goal",
        "Current Blockers",
        "Hard Invariants",
        "Approved Current Decisions",
        "Pending Actions",
        "Recent Milestones",
        "Freshness",
    ]

    for document in (render_root_state(config), render_project_state(config.projects[0])):
        assert [line[3:] for line in document.splitlines() if line.startswith("## ")] == headings


def test_every_markdown_template_is_packaged_and_used(config: WorkspaceConfig) -> None:
    template_names = {
        "root-memory-protocol.md",
        "project-memory-protocol.md",
        "project-registry.md",
        "active-state-index.md",
        "project-state.md",
    }
    template_directory = resources.files("memory_system").joinpath("templates")

    assert {item.name for item in template_directory.iterdir()} == template_names
    assert all(template_directory.joinpath(name).read_text(encoding="utf-8") for name in template_names)
    assert render_root_protocol(config)
    assert render_project_protocol(config.projects[0])
    assert render_registry(config)
    assert render_active_index(config)
    assert render_project_state(config.projects[0])


def test_root_protocol_states_required_recall_and_isolation_rules(config: WorkspaceConfig) -> None:
    protocol = render_root_protocol(config)

    assert "_memory/Context/project-registry.md" in protocol
    assert "project-scoped search → timeline → full observation retrieval" in protocol
    assert "omit platform filters" in protocol
    assert "must not recall from sibling projects" in protocol
    assert "pause consequential work" in protocol


def test_project_protocol_names_project_state_and_local_recall_sequence(
    config: WorkspaceConfig,
) -> None:
    protocol = render_project_protocol(config.projects[0])

    assert "zeta" in protocol
    assert "_memory/Context/projects/zeta.md" in protocol
    assert "project-scoped search → timeline → full observation retrieval" in protocol


def test_active_index_is_sorted_and_lists_all_state_documents(config: WorkspaceConfig) -> None:
    index = render_active_index(config)

    assert index.index("workspace") < index.index("Alpha") < index.index("zeta")
    assert "_memory/Context/projects/workspace.md" in index
    assert "_memory/Context/projects/alpha.md" in index
    assert "_memory/Context/projects/zeta.md" in index


def test_managed_update_inserts_into_empty_authority_file() -> None:
    assert upsert_managed_block("", "root", "managed\n") == (
        "<!-- memory-system:root:start -->\n"
        "managed\n"
        "<!-- memory-system:root:end -->\n"
    )


def test_managed_update_preserves_unowned_bytes() -> None:
    before = "# Local rules\nKeep this exact.\n\n<!-- memory-system:root:start -->\nold\n<!-- memory-system:root:end -->\nTail\n"
    after = upsert_managed_block(before, "root", "new\n")
    assert after == "# Local rules\nKeep this exact.\n\n<!-- memory-system:root:start -->\nnew\n<!-- memory-system:root:end -->\nTail\n"


@pytest.mark.parametrize(
    "existing",
    [
        "<!-- memory-system:root:start -->\n",
        "<!-- memory-system:root:end -->\n",
        "<!-- memory-system:root:start -->\n<!-- memory-system:root:end -->\n<!-- memory-system:root:start -->\n<!-- memory-system:root:end -->\n",
    ],
)
def test_managed_update_rejects_unmatched_or_duplicate_markers(existing: str) -> None:
    with pytest.raises(ManagedBlockConflict, match="marker"):
        upsert_managed_block(existing, "root", "new\n")


@pytest.mark.parametrize(
    "existing",
    [
        "<!-- memory-system:root:start-->\n",
        "<!-- memory-system:root:end-->\n",
        "<!-- memory-system::start -->\n",
        "<!-- memory-system:root:finish -->\n",
        "<!-- memory-system:root :start -->\n",
        "<!--memory-system:root:start -->\n",
    ],
)
def test_managed_update_rejects_malformed_marker_like_comments(existing: str) -> None:
    with pytest.raises(ManagedBlockConflict, match="marker"):
        upsert_managed_block(existing, "root", "new\n")


@pytest.mark.parametrize(
    "existing",
    [
        "# Memory policy\n",
        "Use claude-mem for every decision.\n",
        "- Follow the memory system before editing.\n",
    ],
)
def test_managed_update_rejects_unmanaged_normative_memory_content(existing: str) -> None:
    with pytest.raises(ManagedBlockConflict, match="unmanaged"):
        upsert_managed_block(existing, "root", "new\n")


def test_managed_update_rejects_normative_memory_content_outside_existing_block() -> None:
    existing = (
        "# Memory policy\n"
        "<!-- memory-system:root:start -->\n"
        "old\n"
        "<!-- memory-system:root:end -->\n"
    )

    with pytest.raises(ManagedBlockConflict, match="unmanaged"):
        upsert_managed_block(existing, "root", "new\n")


def test_managed_update_allows_unmanaged_memory_path_reference() -> None:
    existing = "See _memory/Context/active-state.md for workspace routing.\n"

    assert upsert_managed_block(existing, "root", "new\n") == (
        "See _memory/Context/active-state.md for workspace routing.\n"
        "<!-- memory-system:root:start -->\n"
        "new\n"
        "<!-- memory-system:root:end -->\n"
    )
