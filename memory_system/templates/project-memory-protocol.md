# Project Memory Protocol

This protocol applies only to project `$project_identity`. Its current state document is `$project_state`.

Before non-mechanical decisions, read this project's authority and state, then use the official claude-mem service in this order: project-scoped search → timeline → full observation retrieval. Omit platform filters from the search and must not recall from sibling projects.

Source precedence is this project's authority, then its state, then verified project-scoped claude-mem observations; higher-precedence sources win conflicts. If claude-mem is unavailable, pause consequential work.
