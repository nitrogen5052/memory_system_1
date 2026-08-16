# Upgrades

Upgrade by cloning or checking out the next intended release tag, installing that exact checkout into a fresh environment, reviewing its release notes and compatibility policy, and running the same review-first sequence against a backup-capable workspace:

```sh
git clone --branch v1.0.0 --depth 1 https://github.com/nitrogen5052/memory_system_1.git portable-project-memory
cd portable-project-memory
python3 -m venv .venv
.venv/bin/python -m pip install .
```

```sh
.venv/bin/memory-system doctor --workspace .
.venv/bin/memory-system plan --workspace .
.venv/bin/memory-system apply --yes --workspace .
.venv/bin/memory-system verify --workspace .
```

Do not treat an upgrade as permission to copy history through Git. The manifest and curated guidance may be versioned; runtime history remains local and is recalled through the official runtime. Before applying, resolve every migration conflict according to the managed-versus-curated ownership rule in [Architecture](architecture.md).

If a newer release changes generated Markdown, a reviewed plan shows the precise paths. Preserve project isolation: never merge child identities, roots, or state files merely to simplify an upgrade.

Curated project state is never rewritten by an upgrade. If a plan reports a freshness-schema conflict, add the requested `canonical root`, `last_verified`, and `last_reconciled` fields while preserving the existing state prose, then re-run `plan`.
