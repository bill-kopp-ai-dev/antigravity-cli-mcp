"""PersistenceStore: file I/O for the persistence layer.

Inspired by femtobot's ``MemoryStore``. Provides atomic writes via
tmp + rename, global lock for concurrency, and size enforcement.

Design notes:
- One instance per process. Holds a resolved base directory.
- All public methods are thread-safe (acquire the global lock).
- File names are restricted to a Literal by the path resolver, so even
  untrusted callers cannot escape the namespace.
- All public methods raise ``ValueError`` with a stable error code prefix
  (``PERSISTENCE_*``) so callers can branch deterministically.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agy_mcp_server.persistence.locks import persistence_lock
from agy_mcp_server.persistence.paths import (
    ALLOWED_FILE_NAMES,
    BACKUP_DIR_NAME,
    INITIALIZED_MARKER,
    PersistenceFileName,
    get_persistence_dir,
    resolve_file_path,
)
from agy_mcp_server.persistence.templates import (
    SEED_VERSION,
    render_agents_template,
    render_memory_template,
    render_projects_template,
)
from agy_mcp_server import provider as _provider

_FILE_TO_TEMPLATE = {
    "agents": render_agents_template,
    "projects": render_projects_template,
    "memory": render_memory_template,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class InitResult:
    base_dir: str
    created: list[str] = field(default_factory=list)
    already_existed: list[str] = field(default_factory=list)
    seed_version: str = SEED_VERSION


@dataclass(slots=True)
class ReadResult:
    file: str
    content: str
    size_bytes: int
    truncated: bool
    modified_at: datetime | None


@dataclass(slots=True)
class AppendResult:
    file: str
    appended_bytes: int
    new_size_bytes: int
    timestamp: datetime


@dataclass(slots=True)
class UpdateResult:
    file: str
    section_anchor: str
    matched: bool
    new_size_bytes: int


@dataclass(slots=True)
class LoadContextResult:
    agents_excerpt: str | None
    projects_excerpt: str | None
    memory_excerpt: str | None
    truncated_flags: dict[str, bool] = field(default_factory=dict)
    total_chars: int = 0
    base_dir: str = ""
    initialized: bool = False


class PersistenceStore:
    """File-backed store for the three editable markdown files."""

    def __init__(
        self,
        *,
        base_dir: Path,
        max_file_bytes: int = 524_288,
        backup_on_write: bool = False,
        seed_templates: bool = True,
    ) -> None:
        self._base_dir_raw = Path(base_dir)
        self._base_dir = get_persistence_dir(self._base_dir_raw)
        self._max_file_bytes = max_file_bytes
        self._backup_on_write = backup_on_write
        self._seed_templates = seed_templates

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def init(self, *, force: bool = False, seed_templates: bool | None = None) -> InitResult:
        """Create the persistence directory and seed the three markdown files.

        Idempotent: re-running without ``force=True`` is a no-op.
        """
        seed = (
            self._seed_templates
            if seed_templates is None
            else seed_templates
        )

        result = InitResult(base_dir=str(self._base_dir), seed_version=SEED_VERSION)

        with persistence_lock():
            try:
                self._base_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ValueError(
                    f"PERSISTENCE_BASE_DIR_NOT_WRITABLE: cannot create {self._base_dir}: {e}"
                ) from e

            marker = self._base_dir / INITIALIZED_MARKER
            if marker.exists() and not force:
                result.already_existed = sorted(
                    str(p) for p in self._base_dir.iterdir() if p.is_file()
                )
                return result

            # Write seed files (or empty ones if seed=False).
            for name in ALLOWED_FILE_NAMES:
                path = self._resolve(name)
                existed = path.exists()
                if existed and not force:
                    result.already_existed.append(str(path))
                    continue
                content = _FILE_TO_TEMPLATE[name](_provider.PROVIDER_PREFIX) if seed else ""
                self._atomic_write(path, content, max_bytes_check=False)
                if existed and force:
                    result.created.append(str(path))
                elif not existed:
                    result.created.append(str(path))

            # Write marker.
            marker_data = {
                "provider": _provider.PROVIDER_PREFIX,
                "seed_version": SEED_VERSION,
                "created_at": _utcnow().isoformat(),
                "seed_templates": seed,
            }
            self._atomic_write(
                marker, json.dumps(marker_data, indent=2), max_bytes_check=False
            )

        return result

    # ------------------------------------------------------------------
    # Read / append / update
    # ------------------------------------------------------------------

    def read(
        self,
        file_name: PersistenceFileName,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> ReadResult:
        path = self._resolve(file_name)
        if not path.exists():
            raise ValueError(f"PERSISTENCE_FILE_NOT_FOUND: {path}")

        stat = path.stat()
        if stat.st_size > self._max_file_bytes:
            raise ValueError(
                f"PERSISTENCE_FILE_TOO_LARGE: {path} is {stat.st_size} bytes "
                f"(limit {self._max_file_bytes})"
            )

        with path.open("rb") as f:
            f.seek(offset)
            data = f.read(limit) if limit is not None else f.read()

        truncated = limit is not None and len(data) == limit
        # Also flag if the file was capped at max_file_bytes but we read all of it.
        if not truncated and stat.st_size > offset + len(data):
            truncated = True

        return ReadResult(
            file=str(path),
            content=data.decode("utf-8", errors="replace"),
            size_bytes=stat.st_size,
            truncated=truncated,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )

    def append(
        self,
        file_name: PersistenceFileName,
        content: str,
        *,
        section_header: str | None = None,
    ) -> AppendResult:
        path = self._resolve(file_name)

        with persistence_lock():
            if not path.exists():
                raise ValueError(f"PERSISTENCE_FILE_NOT_FOUND: {path}")

            current = path.read_text(encoding="utf-8") if path.exists() else ""

            # Build the chunk to append.
            chunk = content if content.endswith("\n") else content + "\n"
            if section_header:
                header_line = f"## {section_header}\n"
                if header_line not in current:
                    chunk = f"\n{header_line}\n{chunk}"

            new_content = current + chunk
            new_size = len(new_content.encode("utf-8"))
            if new_size > self._max_file_bytes:
                raise ValueError(
                    f"PERSISTENCE_FILE_TOO_LARGE: appending would push {path} to "
                    f"{new_size} bytes (limit {self._max_file_bytes})"
                )

            self._maybe_backup(path)
            self._atomic_write(path, new_content)

        return AppendResult(
            file=str(path),
            appended_bytes=len(chunk.encode("utf-8")),
            new_size_bytes=new_size,
            timestamp=_utcnow(),
        )

    def update(
        self,
        file_name: PersistenceFileName,
        section_anchor: str,
        new_content: str,
        *,
        mode: Literal["replace", "append"] = "replace",
    ) -> UpdateResult:
        path = self._resolve(file_name)

        with persistence_lock():
            if not path.exists():
                raise ValueError(f"PERSISTENCE_FILE_NOT_FOUND: {path}")

            current = path.read_text(encoding="utf-8")
            matched = False

            if mode == "append":
                # No anchor matching required.
                updated = current + (new_content if new_content.endswith("\n") else new_content + "\n")
                matched = True
            else:
                updated, matched = _replace_section(current, section_anchor, new_content)

            new_size = len(updated.encode("utf-8"))
            if new_size > self._max_file_bytes:
                raise ValueError(
                    f"PERSISTENCE_FILE_TOO_LARGE: update would push {path} to "
                    f"{new_size} bytes (limit {self._max_file_bytes})"
                )

            self._maybe_backup(path)
            self._atomic_write(path, updated)

        return UpdateResult(
            file=str(path),
            section_anchor=section_anchor,
            matched=matched,
            new_size_bytes=new_size,
        )

    # ------------------------------------------------------------------
    # Context loading
    # ------------------------------------------------------------------

    def load_context(
        self,
        *,
        include: list[PersistenceFileName] | None = None,
        max_chars_per_file: int = 20_000,
    ) -> LoadContextResult:
        """Load the three files as truncated excerpts for a session.

        Truncation preserves head + tail (most-recent) when the file is
        larger than ``max_chars_per_file``.
        """
        includes = include or list(ALLOWED_FILE_NAMES)

        result = LoadContextResult(
            agents_excerpt=None,
            projects_excerpt=None,
            memory_excerpt=None,
            base_dir=str(self._base_dir),
            initialized=(self._base_dir / INITIALIZED_MARKER).exists(),
        )

        for name in includes:
            path = self._resolve(name)
            if not path.exists():
                result.truncated_flags[name] = False
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                result.truncated_flags[name] = False
                continue

            truncated = len(text) > max_chars_per_file
            if truncated:
                # Keep head + tail.
                head = text[: max_chars_per_file // 2]
                tail = text[-max_chars_per_file // 2 :]
                text = f"{head}\n\n... [truncated] ...\n\n{tail}"

            result.truncated_flags[name] = truncated
            result.total_chars += len(text)

            if name == "agents":
                result.agents_excerpt = text
            elif name == "projects":
                result.projects_excerpt = text
            elif name == "memory":
                result.memory_excerpt = text

        return result

    # ------------------------------------------------------------------
    # Status / introspection
    # ------------------------------------------------------------------

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    @property
    def is_initialized(self) -> bool:
        return (self._base_dir / INITIALIZED_MARKER).exists()

    def status(self) -> dict[str, Any]:
        return {
            "base_dir": str(self._base_dir),
            "initialized": self.is_initialized,
            "files": {
                name: {
                    "path": str(self._resolve(name)),
                    "exists": self._resolve(name).exists(),
                    "size_bytes": (
                        self._resolve(name).stat().st_size
                        if self._resolve(name).exists()
                        else 0
                    ),
                }
                for name in ALLOWED_FILE_NAMES
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, file_name: str) -> Path:
        return resolve_file_path(
            self._base_dir_raw,
            file_name,
            base_dir_resolved=self._base_dir.parent,
        )

    def _atomic_write(
        self,
        path: Path,
        content: str,
        *,
        max_bytes_check: bool = True,
    ) -> None:
        """Write to a temp file then rename atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode("utf-8")
        if max_bytes_check and len(encoded) > self._max_file_bytes:
            raise ValueError(
                f"PERSISTENCE_FILE_TOO_LARGE: refusing to write {len(encoded)} bytes to "
                f"{path} (limit {self._max_file_bytes})"
            )

        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(encoded)
            os.replace(tmp_path, path)
        except Exception:
            # Clean up the temp file on failure.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _maybe_backup(self, path: Path) -> None:
        if not self._backup_on_write or not path.exists():
            return
        backup_dir = path.parent / BACKUP_DIR_NAME
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = _utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
        backup_path = backup_dir / f"{path.name}.{ts}.bak"
        backup_path.write_bytes(path.read_bytes())


def _replace_section(
    text: str,
    anchor: str,
    new_content: str,
) -> tuple[str, bool]:
    """Replace the section starting with ``## <anchor>`` up to the next ``## ``.

    Returns the updated text and whether the anchor was matched.
    """
    lines = text.splitlines(keepends=True)
    header = f"## {anchor}"
    start_idx: int | None = None
    for i, line in enumerate(lines):
        if line.rstrip() == header:
            start_idx = i
            break

    if start_idx is None:
        return text, False

    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end_idx = j
            break

    new_block = new_content if new_content.endswith("\n") else new_content + "\n"
    updated_lines = lines[:start_idx] + [new_block] + lines[end_idx:]
    return "".join(updated_lines), True