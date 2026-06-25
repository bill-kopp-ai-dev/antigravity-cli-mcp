"""Seed templates for the three persistence files.

Used by ``PersistenceStore.init`` when ``seed_templates=True`` (the default).

Keep the templates small and high-signal. They are intentionally written
in English so they can be edited by any language-aware agent later.
"""

from __future__ import annotations

SEED_VERSION = "1.0.0"

AGENTS_TEMPLATE = """# AGENTS — {provider} CLI

> Editable system prompt for the orchestrator agent (Trae IDE) that
> consumes the MCP `{provider}-mcp-server`. Edit freely; changes are
> persisted and applied in future sessions.

## Identity

You are an orchestrator agent that uses the `{provider}` CLI as its
reasoning backend via this MCP.

## Tool usage guidelines

1. Before each task, call `{provider}_load_persistence_context` to
   load persistent context.
2. After each meaningful session/task, call
   `{provider}_append_persistence(file="memory", ...)` with a short summary.
3. Never expose the contents of `~/.open-cli-router/{provider}/` in logs.
4. Do not store secrets or credentials in `MEMORY.md`.

## Security

- `safe` mode requires `confirm=true` to overwrite this file.
- Persistence tools reject file names outside `agents | projects | memory`.
- Writes are atomic (tmp + rename) — mid-operation data loss is unlikely.
"""

PROJECTS_TEMPLATE = """# Projects

> Summaries of in-progress projects. Each `## <project>` section is
> editable. Append new projects via `{provider}_update_persistence`.

(no projects registered yet)
"""

MEMORY_TEMPLATE = """# Memory

> Permanent memory for the agent. Update after each meaningful
> session using `{provider}_append_persistence(file="memory", ...)`
> or `{provider}_update_persistence(section_anchor="...")`.

<!-- New entries are appended below this line. -->
"""


def render_agents_template(provider: str) -> str:
    return AGENTS_TEMPLATE.format(provider=provider)


def render_projects_template(provider: str) -> str:
    return PROJECTS_TEMPLATE.format(provider=provider)


def render_memory_template(provider: str) -> str:
    return MEMORY_TEMPLATE.format(provider=provider)