"""Provider prefix configuration for the MCP server.

This module is the SINGLE source of truth for the provider prefix used in
tool names, prompts, and any other user-visible identifier.

When forking this server for another CLI provider (claude, codex, gemini,
deepseek, etc.), change ``PROVIDER_PREFIX`` to the new provider name. All
MCP tool names are derived from this constant via :func:`tool_name`, so
the rename happens automatically and consistently.

Example:
    Current server (Antigravity CLI):

        >>> from agy_mcp_server.provider import PROVIDER_PREFIX, tool_name
        >>> PROVIDER_PREFIX
        'agy'
        >>> tool_name("health")
        'agy_health'

    Forked server (Claude Code CLI):

        # In claude_code_cli_mcp/provider.py:
        PROVIDER_PREFIX = "claude"

        # Then:
        >>> tool_name("health")
        'claude_health'
"""

from __future__ import annotations

# Single source of truth. Change this when forking the server for another CLI.
PROVIDER_PREFIX: str = "agy"

# Persistence namespace (on-disk directory name).
#
# For agy this coincides with PROVIDER_PREFIX, but it is maintained as a
# separate constant for parity with claude-code-cli-mcp, where
# PROVIDER_PREFIX="claude" but PERSISTENCE_NAMESPACE="claude-code".
#
# Keeping these decoupled lets forks use a short MCP wire prefix
# (e.g. "codex") while exposing a human-readable directory name
# (e.g. "codex-cli") under ``~/.open-cli-router/``.
PERSISTENCE_NAMESPACE: str = "agy"

# Convention: tool names are always ``{PROVIDER_PREFIX}_{suffix}``.
# Keep suffix in snake_case.
_NAME_SEPARATOR: str = "_"


def tool_name(suffix: str) -> str:
    """Build a standardized MCP tool name for this provider.

    Args:
        suffix: The tool's local name (e.g., ``"health"``, ``"run_task"``).
                Must be non-empty and contain only snake_case characters.

    Returns:
        The full MCP tool name (e.g., ``"agy_health"``).

    Raises:
        ValueError: If ``suffix`` is empty or contains characters other than
                    lowercase ASCII letters, digits, or underscores.
    """
    if not suffix:
        raise ValueError("PROVIDER_PREFIX_REQUIRED: tool suffix cannot be empty")

    normalized = suffix.strip().lower()
    if not normalized.replace("_", "").isalnum() or not normalized.replace(
        "_", ""
    ).isascii():
        raise ValueError(
            f"INVALID_TOOL_SUFFIX: suffix must be snake_case ASCII, got {suffix!r}"
        )

    return f"{PROVIDER_PREFIX}{_NAME_SEPARATOR}{normalized}"


def prompt_name(suffix: str) -> str:
    """Build a standardized MCP prompt name for this provider.

    Mirrors :func:`tool_name` so prompt names follow the same convention.
    """
    return tool_name(suffix)