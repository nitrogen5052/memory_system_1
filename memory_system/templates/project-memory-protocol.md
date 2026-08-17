# Project Memory Protocol

This protocol applies only to project `$project_identity`. Its current state document is `$project_state`.

Before non-mechanical or consequential decisions, read this project's authority and state, then use the official claude-mem service in this order: project-scoped search → timeline → full observation retrieval. Inspect timelines around relevant search results, fetch the full observations, then reconcile them against current files. Omit platform filters from the search and must not recall from sibling projects. Search titles and summaries alone never satisfy this gate.

Source precedence is: Current explicit user instructions and platform constraints; current project authority and code/data; this state; full verified project-scoped claude-mem observations; then Daily Notes and legacy Markdown. Historical memory never overrides current authority; flag consequential authority/code conflicts.

If claude-mem is unavailable or expected established-project recall is empty, report it and pause consequential work unless the user explicitly waives deep recall. Use `_memory/` only as read-only recovery context; never create a replacement automated memory system.

At a durable decision, completed milestone, or changed handoff: save a concise official claude-mem observation under `$project_identity`, use a deterministic operation ID and search before retrying, read it back, append a project-prefixed Daily Note, and update this state only when it changed. Update `last_verified` after authority review and `last_reconciled` after deep recall. Record a dated superseding decision for stale evidence; never rewrite historical observations.
