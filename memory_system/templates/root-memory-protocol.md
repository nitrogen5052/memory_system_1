# Workspace Memory Protocol

This workspace is `$root_identity`. Resolve the exact project from `_memory/Context/project-registry.md` before using memory.

Before a non-mechanical or consequential decision, read that project's current authority files and state document. Then use the official claude-mem service in this order: project-scoped search → timeline → full observation retrieval. Inspect timelines around relevant search results, fetch the full observations, then reconcile them against current files; omit platform filters from the search. Search titles and summaries alone never satisfy this gate.

Agents must not recall from sibling projects. Source precedence is: Current explicit user instructions and platform constraints; current project authority and code/data; current project state; full verified project-scoped claude-mem observations; then Daily Notes and legacy Markdown. Never let historical memory override current authority; flag a consequential authority/code conflict rather than silently choosing.

If claude-mem is unavailable or an established project's expected recall is empty, report the failure and pause consequential work unless the user explicitly waives deep recall. Use `_memory/` only as read-only recovery context; never create a replacement automated memory system.

At a durable decision, completed milestone, or changed handoff: save a concise official claude-mem observation under the exact identity, use a deterministic operation ID and search before retrying, read it back, append a project-prefixed Daily Note, and update current state only if it changed. Update `last_verified` after authority review and `last_reconciled` after deep recall. When evidence becomes stale, record a dated superseding decision; do not rewrite history.

The workspace state document is `$root_state`.
