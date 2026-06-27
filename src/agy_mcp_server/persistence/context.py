"""Prompt context injection for the persistence layer.

Provides a single helper, :func:`build_prompt_with_context`, used by both
``agy_run_task`` (sync) and ``agy_start_task`` (async) to optionally
prepend the editable markdown files (``AGENTS.md``/``PROJECTS.md``/
``MEMORY.md``) to the user prompt before dispatching to the agy CLI.

Design notes:
- Failures are **non-fatal**: if context loading fails, the original
  prompt is returned unchanged. The run continues without context.
- This helper is the single source of truth for context injection. Any
  change to the prepended format (tags, order, truncation) should be
  made here — not in the server.py call sites.
- The function signature takes ``settings`` as a duck-typed object to
  avoid an import cycle with :mod:`agy_mcp_server.settings` (which
  itself imports from persistence indirectly via templates).
"""

from __future__ import annotations

from typing import Any, Protocol


class _SettingsLike(Protocol):
    """Duck-typed subset of Settings that this helper needs.

    Avoids importing Settings directly (and the resulting cycle).
    """

    persistence_enabled: bool


class _StoreLike(Protocol):
    """Duck-typed subset of PersistenceStore that this helper needs."""

    is_initialized: bool

    def load_context(self) -> Any: ...


def build_prompt_with_context(
    prompt: str,
    *,
    settings: _SettingsLike,
    store: _StoreLike,
) -> str:
    """Prepend persistent context excerpts to ``prompt``.

    The excerpts are wrapped in XML tags (one per file) and joined with
    double newlines. If persistence is disabled, uninitialized, or any
    exception occurs, the original prompt is returned unchanged.

    Args:
        prompt: The user prompt to potentially augment.
        settings: Object exposing ``persistence_enabled: bool``.
        store: Object exposing ``is_initialized`` and ``load_context()``.

    Returns:
        ``prompt`` with context prepended if applicable, else ``prompt``.
    """
    if not settings.persistence_enabled or not store.is_initialized:
        return prompt
    try:
        ctx = store.load_context()
    except Exception:
        return prompt

    header_parts: list[str] = []
    agents = getattr(ctx, "agents_excerpt", None)
    projects = getattr(ctx, "projects_excerpt", None)
    memory = getattr(ctx, "memory_excerpt", None)

    if agents:
        header_parts.append(
            f"<persistent-agents-context>\n{agents}\n</persistent-agents-context>"
        )
    if projects:
        header_parts.append(
            f"<persistent-projects-context>\n{projects}\n</persistent-projects-context>"
        )
    if memory:
        header_parts.append(
            f"<persistent-memory-context>\n{memory}\n</persistent-memory-context>"
        )
    if not header_parts:
        return prompt
    return "\n\n".join(header_parts) + "\n\n" + prompt
