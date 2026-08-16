# Portable Project Memory

Portable Project Memory installs deterministic, local Markdown routing and state templates around the official `claude-mem` runtime. It does not replace that runtime: install and run the supported official `claude-mem` version before using this tool.

The workflow is supported on macOS, Linux, and WSL. Native Windows is deferred; use WSL instead until native Windows support is explicitly released.

## Quick start

Clone the release tag you intend to use, then enter the checkout:

```sh
git clone --branch v1.0.0 --depth 1 <repository-url> portable-project-memory
cd portable-project-memory
```

Copy the clean-room example into the workspace you want to manage, or adapt its relative paths and identities. The example contains a root plus two non-overlapping projects.

```sh
cp -R examples/multi-project-workspace ./workspace
cd workspace
memory-system doctor --workspace .
memory-system plan --workspace .
memory-system apply --yes --workspace .
memory-system verify --workspace .
```

Always inspect the complete `plan` output before running `apply`. `apply --yes` is suitable for reviewed, non-interactive use only; without `--yes`, an interactive terminal asks for confirmation.

If a reviewed installation needs to be undone, use the backup path printed by `apply`:

```sh
memory-system rollback --backup .memory-system/backups/<backup-id> --workspace .
```

## Safety boundaries

This tool writes only generated Markdown and managed blocks in declared authority files. It does not copy, publish, or synchronize memory history through Git. Keep runtime databases, observations, credentials, and other local runtime data out of version control.

Read the operational guides before applying a workspace-wide plan:

- [Installation and daily workflow](docs/installation.md)
- [Architecture and ownership](docs/architecture.md)
- [Upgrade process](docs/upgrades.md)
- [Recovery and rollback](docs/recovery.md)

## Development checks

The committed canonical fixture is exercised by the end-to-end tests. Run the suite with an interpreter that has the development dependency installed:

```sh
python3 -m pytest -v
```
