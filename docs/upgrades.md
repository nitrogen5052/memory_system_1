# Upgrades

Upgrade by cloning or checking out the next intended release tag, reviewing its release notes and compatibility policy, and running the same review-first sequence against a backup-capable workspace:

```sh
memory-system doctor --workspace .
memory-system plan --workspace .
memory-system apply --yes --workspace .
memory-system verify --workspace .
```

Do not treat an upgrade as permission to copy history through Git. The manifest and curated guidance may be versioned; runtime history remains local and is recalled through the official runtime. Before applying, resolve every migration conflict according to the managed-versus-curated ownership rule in [Architecture](architecture.md).

If a newer release changes generated Markdown, a reviewed plan shows the precise paths. Preserve project isolation: never merge child identities, roots, or state files merely to simplify an upgrade.
