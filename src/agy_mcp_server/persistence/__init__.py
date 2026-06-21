"""Persistent memory layer for the MCP server.

This package provides file-based persistence at
``~/.open-cli-router/{PROVIDER_PREFIX}/`` for three editable markdown files:

- ``AGENTS.md`` — editable system prompt
- ``PROJECTS.md`` — project summaries
- ``MEMORY.md`` — permanent memory updated after each session

The namespace (``agy``, ``claude-code``, ``codex``) is derived from
``PROVIDER_PREFIX`` in :mod:`agy_mcp_server.provider`, so forking the server
to another CLI provider automatically remaps the persistence directory.
"""

from __future__ import annotations

from agy_mcp_server.persistence.paths import (
    ALLOWED_FILE_NAMES,
    PersistenceFileName,
    get_persistence_base_dir,
    get_persistence_dir,
    resolve_file_path,
)
from agy_mcp_server.persistence.store import PersistenceStore

__all__ = [
    "ALLOWED_FILE_NAMES",
    "PersistenceFileName",
    "PersistenceStore",
    "get_persistence_base_dir",
    "get_persistence_dir",
    "resolve_file_path",
]