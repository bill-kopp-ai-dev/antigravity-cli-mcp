"""Path resolution for the persistence layer.

Centralizes all path logic so forking the server to another CLI provider
only requires changing ``PERSISTENCE_NAMESPACE`` in
:mod:`agy_mcp_server.provider` — the namespace directory is derived
automatically.

Note: paths.py uses ``PERSISTENCE_NAMESPACE`` (the on-disk directory
name) rather than ``PROVIDER_PREFIX`` (the MCP wire-format prefix).
For agy these coincide ("agy"/"agy"); for forks they can differ
(e.g. claude-code-cli-mcp uses PROVIDER_PREFIX="claude" but
PERSISTENCE_NAMESPACE="claude-code").
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from agy_mcp_server import provider as _provider

# Files allowed in the persistence directory.
# Locked to a Literal so traversal attacks cannot reach arbitrary files.
ALLOWED_FILE_NAMES = ("agents", "projects", "memory")
PersistenceFileName = Literal["agents", "projects", "memory"]

# Marker file written by ``agy_init_persistence`` to indicate that the
# directory has been seeded.
INITIALIZED_MARKER = ".initialized"

# Subdirectory for opt-in backups (created on demand).
BACKUP_DIR_NAME = ".backups"


def _current_namespace() -> str:
    """Return the active persistence namespace.

    Lookup is done via the module object so that ``monkeypatch.setattr`` on
    ``agy_mcp_server.provider.PERSISTENCE_NAMESPACE`` propagates correctly.
    """
    return _provider.PERSISTENCE_NAMESPACE


def get_persistence_base_dir(base_dir: Path) -> Path:
    """Resolve and normalize the user-provided persistence base directory.

    Args:
        base_dir: The raw base directory (typically ``~/.open-cli-router``).

    Returns:
        The expanded and resolved absolute path. The directory is NOT created
        by this function — callers must decide whether to create it.
    """
    return Path(base_dir).expanduser().resolve()


def get_persistence_dir(base_dir: Path) -> Path:
    """Return the per-provider persistence directory.

    Example:
        base_dir = ~/.open-cli-router, PERSISTENCE_NAMESPACE = "agy"
        → ~/.open-cli-router/agy
    """
    return get_persistence_base_dir(base_dir) / _current_namespace()


def resolve_file_path(
    base_dir: Path,
    file_name: str,
    *,
    base_dir_resolved: Path | None = None,
) -> Path:
    """Resolve a single file path under the persistence directory.

    Args:
        base_dir: The raw persistence base directory.
        file_name: One of ``agents``, ``projects``, ``memory``.
        base_dir_resolved: Optional pre-resolved base directory (used by
            the store after locking the directory once per process).

    Returns:
        Absolute path to the file. Does NOT verify the file exists.

    Raises:
        ValueError: If ``file_name`` is not in :data:`ALLOWED_FILE_NAMES`.
    """
    if file_name not in ALLOWED_FILE_NAMES:
        raise ValueError(
            f"INVALID_FILE: file_name must be one of {ALLOWED_FILE_NAMES}, "
            f"got {file_name!r}"
        )

    resolved_base = base_dir_resolved or get_persistence_base_dir(base_dir)
    ns = _current_namespace()
    # Display filename is UPPERCASE per the public contract
    # (AGENTS.md, PROJECTS.md, MEMORY.md). The internal logical name
    # (used in tool payloads) remains lowercase.
    target = (resolved_base / ns / f"{file_name.upper()}.md").resolve()

    # Defense in depth: even with the Literal check, refuse if the resolved
    # path escapes the namespace directory.
    expected_prefix = (resolved_base / ns).resolve()
    if not target.is_relative_to(expected_prefix):
        raise ValueError(
            f"PATH_TRAVERSAL: resolved path {target} escapes {expected_prefix}"
        )

    return target