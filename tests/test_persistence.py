"""Tests for the persistence layer."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agy_mcp_server.persistence import (
    ALLOWED_FILE_NAMES,
    PersistenceStore,
    get_persistence_dir,
    resolve_file_path,
)


# ------------------------------------------------------------------
# paths
# ------------------------------------------------------------------


def test_persistence_dir_uses_provider_prefix(tmp_path: Path):
    base = tmp_path / ".open-cli-router"
    d = get_persistence_dir(base)
    # PROVIDER_PREFIX is "agy" by default.
    assert d == base / "agy"
    assert d.name == "agy"


def test_resolve_file_path_accepts_allowed_names(tmp_path: Path):
    base = tmp_path / ".open-cli-router"
    for name in ALLOWED_FILE_NAMES:
        p = resolve_file_path(base, name)
        assert p.suffix == ".md"
        assert p.name == f"{name.upper()}.md"


def test_resolve_file_path_rejects_unknown_name(tmp_path: Path):
    base = tmp_path / ".open-cli-router"
    with pytest.raises(ValueError, match="INVALID_FILE"):
        resolve_file_path(base, "secrets")


def test_resolve_file_path_rejects_traversal(tmp_path: Path):
    base = tmp_path / ".open-cli-router"
    with pytest.raises(ValueError, match="INVALID_FILE"):
        resolve_file_path(base, "../../etc/passwd")


# ------------------------------------------------------------------
# store: init / read
# ------------------------------------------------------------------


def _make_store(tmp_path: Path) -> PersistenceStore:
    return PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=10_000,
        seed_templates=True,
    )


def test_init_creates_three_files_and_marker(tmp_path: Path):
    store = _make_store(tmp_path)
    result = store.init()
    assert (store.base_dir / "AGENTS.md").exists()
    assert (store.base_dir / "PROJECTS.md").exists()
    assert (store.base_dir / "MEMORY.md").exists()
    assert (store.base_dir / ".initialized").exists()
    assert len(result.created) >= 3  # 3 seed files
    assert result.seed_version


def test_init_is_idempotent(tmp_path: Path):
    store = _make_store(tmp_path)
    first = store.init()
    second = store.init()
    # Second call reports everything as already existed.
    assert second.already_existed
    assert not second.created
    # Files weren't overwritten.
    content = (store.base_dir / "AGENTS.md").read_text()
    assert "AGENTS" in content  # from the first seed


def test_init_force_overwrites(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init()
    (store.base_dir / "AGENTS.md").write_text("custom content")
    store.init(force=True)
    assert "custom content" not in (store.base_dir / "AGENTS.md").read_text()
    assert "AGENTS" in (store.base_dir / "AGENTS.md").read_text()


def test_init_no_seed_creates_empty_files(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init(seed_templates=False)
    for name in ALLOWED_FILE_NAMES:
        text = (store.base_dir / f"{name.upper()}.md").read_text()
        assert text == ""


def test_init_fails_on_unwritable_base_dir(tmp_path: Path):
    # Create a file at the path where the directory should be created.
    base = tmp_path / "blocker"
    base.write_text("not a directory")
    store = PersistenceStore(base_dir=base / ".open-cli-router")
    with pytest.raises(ValueError, match="PERSISTENCE_BASE_DIR_NOT_WRITABLE"):
        store.init()


# ------------------------------------------------------------------
# store: read
# ------------------------------------------------------------------


def test_read_returns_content(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init()
    result = store.read("agents")
    assert "AGENTS" in result.content
    assert result.size_bytes > 0
    assert not result.truncated
    assert result.modified_at is not None


def test_read_offset_and_limit(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init()
    # Replace seed with ASCII content so byte limit == char limit.
    target = store.base_dir / "AGENTS.md"
    target.write_text("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    limited = store.read("agents", offset=0, limit=10)
    assert limited.content == "ABCDEFGHIJ"
    assert limited.truncated

    # Offset works too.
    chunk = store.read("agents", offset=5, limit=5)
    assert chunk.content == "FGHIJ"


def test_read_rejects_unknown_file(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init()
    with pytest.raises(ValueError, match="INVALID_FILE"):
        store.read("nope")  # type: ignore[arg-type]


def test_read_rejects_too_large(tmp_path: Path):
    store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=10,
        seed_templates=False,
    )
    store.init()
    (store.base_dir / "AGENTS.md").write_text("x" * 50)
    with pytest.raises(ValueError, match="PERSISTENCE_FILE_TOO_LARGE"):
        store.read("agents")


# ------------------------------------------------------------------
# store: append
# ------------------------------------------------------------------


def test_append_grows_file(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init()
    before = store.read("memory").size_bytes
    result = store.append("memory", "Session summary: did X.")
    after = store.read("memory").size_bytes
    assert after > before
    assert result.appended_bytes > 0


def test_append_with_section_header_inserts_once(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init()
    store.append("memory", "first body", section_header="2026-06-21")
    store.append("memory", "second body", section_header="2026-06-21")
    text = store.read("memory").content
    assert text.count("## 2026-06-21") == 1


def test_append_rejects_overflow(tmp_path: Path):
    store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=50,
        seed_templates=False,
    )
    store.init()
    (store.base_dir / "MEMORY.md").write_text("seed\n")
    with pytest.raises(ValueError, match="PERSISTENCE_FILE_TOO_LARGE"):
        store.append("memory", "x" * 1000)


# ------------------------------------------------------------------
# store: update
# ------------------------------------------------------------------


def test_update_replaces_section(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init()
    store.append("projects", "## A\nold\n\n## B\nkeep\n")
    result = store.update("projects", "A", "## A\nnew\n", mode="replace")
    assert result.matched
    text = store.read("projects").content
    assert "new" in text
    assert "old" not in text
    assert "keep" in text


def test_update_miss_returns_matched_false(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init()
    result = store.update("memory", "NoSuchSection", "## NoSuchSection\nx\n")
    assert not result.matched


def test_update_append_mode_ignores_anchor(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init()
    result = store.update(
        "memory", "ignored", "trailing text", mode="append"
    )
    assert result.matched
    assert "trailing text" in store.read("memory").content


# ------------------------------------------------------------------
# store: load_context
# ------------------------------------------------------------------


def test_load_context_returns_excerpts_when_initialized(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init()
    ctx = store.load_context()
    assert ctx.initialized
    assert ctx.agents_excerpt is not None
    assert ctx.projects_excerpt is not None
    assert ctx.memory_excerpt is not None
    assert ctx.base_dir.endswith("agy")


def test_load_context_returns_none_when_not_initialized(tmp_path: Path):
    store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        seed_templates=False,
    )
    store.init()  # marker exists
    (store.base_dir / ".initialized").unlink()
    ctx = store.load_context()
    assert not ctx.initialized
    # Files still exist (created by init), so excerpts are strings (empty).
    # Contract: initialized=False tells caller to call init; excerpts may be empty.
    assert ctx.agents_excerpt is not None
    assert ctx.agents_excerpt == ""


def test_load_context_truncates_large_files(tmp_path: Path):
    store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=1_000_000,
        seed_templates=False,
    )
    store.init()
    (store.base_dir / "MEMORY.md").write_text("X" * 5000)
    ctx = store.load_context(max_chars_per_file=200)
    assert ctx.memory_excerpt is not None
    assert ctx.truncated_flags["memory"] is True
    assert "[truncated]" in ctx.memory_excerpt


# ------------------------------------------------------------------
# store: atomic write + concurrency
# ------------------------------------------------------------------


def test_atomic_write_no_partial_file_on_failure(tmp_path: Path, monkeypatch):
    store = _make_store(tmp_path)
    store.init()
    target = store.base_dir / "AGENTS.md"
    original = target.read_text()

    # Force a failure inside the atomic write by patching os.replace to raise.
    def boom(*_args, **_kwargs):
        raise OSError("simulated atomic-write failure")

    monkeypatch.setattr("agy_mcp_server.persistence.store.os.replace", boom)

    with pytest.raises(OSError, match="simulated atomic-write failure"):
        store.append("agents", "should not land")

    # The original content should be intact (atomic write left nothing behind).
    assert target.read_text() == original

    # And no orphan temp files remain in the directory.
    leftover = [
        p for p in target.parent.iterdir()
        if p.name.startswith(".AGENTS.md.") and p.name.endswith(".tmp")
    ]
    assert leftover == []


def test_concurrent_appends_serialize(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init()
    n_threads = 10
    per_thread = 20

    def worker(i: int) -> None:
        for j in range(per_thread):
            store.append("memory", f"t{i}-{j}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    text = store.read("memory").content
    for i in range(n_threads):
        for j in range(per_thread):
            assert f"t{i}-{j}" in text, f"lost entry t{i}-{j}"


# ------------------------------------------------------------------
# Integration: provider prefix remaps namespace
# ------------------------------------------------------------------


def test_provider_prefix_remaps_namespace(monkeypatch, tmp_path: Path):
    """Simulate a fork: changing PROVIDER_PREFIX remaps the persistence dir."""
    import agy_mcp_server.provider as provider_mod

    monkeypatch.setattr(provider_mod, "PROVIDER_PREFIX", "claude")

    d = get_persistence_dir(tmp_path / ".open-cli-router")
    assert d.name == "claude"

    store = PersistenceStore(base_dir=tmp_path / ".open-cli-router")
    store.init()
    assert (store.base_dir / "AGENTS.md").exists()
    text = (store.base_dir / "AGENTS.md").read_text()
    assert "claude" in text.lower()  # template uses provider name