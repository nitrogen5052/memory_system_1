# Architecture and ownership

## Source precedence and isolation

The manifest is the source of truth for workspace identity, project roots, state documents, and authority files. It is validated before planning. The generated project registry and active-state index derive from that manifest. Root authority governs only workspace-wide routing; each child authority governs its own declared project. Project roots, state files, and authority paths are non-overlapping to prevent cross-project contamination.

For shallow current context, read the root or project state document named by the registry. For deep recall, use the official `claude-mem` runtime within the declared project scope, inspect timelines and full observations, then reconcile them with authority and current files. Write back only durable decisions, preferences, and completed milestones to the appropriate state document. Do not put a transcript, runtime database, credential, or raw observation into the curated Markdown layer.

## Managed and curated content

The installer owns generated registry and state documents. In authority files, it owns only explicitly marked managed blocks. Everything outside those blocks is curated content owned by the workspace maintainer. The installer preserves curated bytes and rejects unmanaged memory instructions rather than guessing how to merge them.

When `plan` reports a conflict, review the existing curated guidance and choose deliberately: move compatible guidance outside the managed block, remove obsolete conflicting guidance, or keep the file unchanged and do not apply. A compatible existing managed block may be adopted; adoption records it as managed without rewriting unrelated curated content. Do not force adoption merely to silence a conflict.

When a manifest removes or reroutes a project, the installer removes only that project's managed authority block and retires its installation record. It never deletes the project's curated state document: retain it as a human-readable retirement record until a maintainer deliberately archives or relocates it. The next backup makes the authority retirement rollback-safe.

## Docker boundary

Docker can be used to run the test suite in a clean environment. It is not a production memory service: container storage and lifecycle are not a substitute for the official local runtime, its durable storage, or its project-scoped recall and writeback workflow.
