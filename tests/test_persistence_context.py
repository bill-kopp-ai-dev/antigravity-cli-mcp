"""Tests for the build_prompt_with_context helper (Phase 4).

This helper is the single source of truth for injecting persistent
context excerpts into user prompts. It is called by both
``agy_run_task`` and ``agy_start_task`` in server.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agy_mcp_server.persistence import (
    PersistenceStore,
    build_prompt_with_context,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_store(tmp_path: Path) -> PersistenceStore:
    return PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=10_000,
        seed_templates=True,
    )


@dataclass
class _FakeSettings:
    """Minimal duck-typed settings for build_prompt_with_context."""

    persistence_enabled: bool = True


@dataclass
class _FakeContext:
    """Minimal LoadContextResult-like object."""

    agents_excerpt: str | None = None
    projects_excerpt: str | None = None
    memory_excerpt: str | None = None


class _FakeStore:
    """Minimal PersistenceStore-like object for unit-testing the helper."""

    def __init__(self, *, is_initialized: bool = True, ctx: _FakeContext | None = None,
                 raise_on_load: Exception | None = None):
        self.is_initialized = is_initialized
        self._ctx = ctx or _FakeContext()
        self._raise = raise_on_load

    def load_context(self):
        if self._raise is not None:
            raise self._raise
        return self._ctx


# ------------------------------------------------------------------
# Disabled / uninitialized paths
# ------------------------------------------------------------------


def test_helper_disabled_returns_original():
    settings = _FakeSettings(persistence_enabled=False)
    store = _FakeStore(is_initialized=True)
    out = build_prompt_with_context("user prompt", settings=settings, store=store)
    assert out == "user prompt"


def test_helper_uninitialized_returns_original():
    settings = _FakeSettings(persistence_enabled=True)
    store = _FakeStore(is_initialized=False)
    out = build_prompt_with_context("user prompt", settings=settings, store=store)
    assert out == "user prompt"


def test_helper_disabled_and_uninitialized_returns_original():
    settings = _FakeSettings(persistence_enabled=False)
    store = _FakeStore(is_initialized=False)
    out = build_prompt_with_context("user prompt", settings=settings, store=store)
    assert out == "user prompt"


# ------------------------------------------------------------------
# Successful injection
# ------------------------------------------------------------------


def test_helper_prepends_all_three_tags_when_initialized():
    settings = _FakeSettings(persistence_enabled=True)
    ctx = _FakeContext(
        agents_excerpt="AGENTS CONTENT",
        projects_excerpt="PROJECTS CONTENT",
        memory_excerpt="MEMORY CONTENT",
    )
    store = _FakeStore(is_initialized=True, ctx=ctx)
    out = build_prompt_with_context("user prompt", settings=settings, store=store)

    assert "<persistent-agents-context>" in out
    assert "<persistent-projects-context>" in out
    assert "<persistent-memory-context>" in out
    assert "AGENTS CONTENT" in out
    assert "PROJECTS CONTENT" in out
    assert "MEMORY CONTENT" in out
    # User prompt is at the end
    assert out.endswith("user prompt")


def test_helper_preserves_excerpt_order_agents_projects_memory():
    """A ordem dos tags deve ser sempre AGENTS → PROJECTS → MEMORY → user prompt."""
    settings = _FakeSettings(persistence_enabled=True)
    ctx = _FakeContext(
        agents_excerpt="AAA",
        projects_excerpt="BBB",
        memory_excerpt="CCC",
    )
    store = _FakeStore(is_initialized=True, ctx=ctx)
    out = build_prompt_with_context("user", settings=settings, store=store)
    a_idx = out.index("AAA")
    b_idx = out.index("BBB")
    c_idx = out.index("CCC")
    u_idx = out.index("user")
    assert a_idx < b_idx < c_idx < u_idx


def test_helper_skips_none_excerpts():
    settings = _FakeSettings(persistence_enabled=True)
    # Apenas agents_excerpt preenchido
    ctx = _FakeContext(agents_excerpt="AAA", projects_excerpt=None, memory_excerpt=None)
    store = _FakeStore(is_initialized=True, ctx=ctx)
    out = build_prompt_with_context("user", settings=settings, store=store)

    assert "<persistent-agents-context>" in out
    assert "<persistent-projects-context>" not in out
    assert "<persistent-memory-context>" not in out


def test_helper_returns_original_when_all_excerpts_empty():
    """Se todos os excerpts forem None/vazios, retorna prompt original sem prepend."""
    settings = _FakeSettings(persistence_enabled=True)
    ctx = _FakeContext(agents_excerpt=None, projects_excerpt=None, memory_excerpt=None)
    store = _FakeStore(is_initialized=True, ctx=ctx)
    out = build_prompt_with_context("user", settings=settings, store=store)
    assert out == "user"


# ------------------------------------------------------------------
# Failure handling (non-fatal)
# ------------------------------------------------------------------


def test_helper_load_failure_is_non_fatal():
    """Se load_context lança exceção, retorna prompt original (sem raise)."""
    settings = _FakeSettings(persistence_enabled=True)
    store = _FakeStore(is_initialized=True, raise_on_load=RuntimeError("simulated"))
    out = build_prompt_with_context("user", settings=settings, store=store)
    assert out == "user"


def test_helper_handles_io_error_in_load_context():
    settings = _FakeSettings(persistence_enabled=True)
    store = _FakeStore(
        is_initialized=True,
        raise_on_load=OSError("disk error"),
    )
    out = build_prompt_with_context("user", settings=settings, store=store)
    assert out == "user"


# ------------------------------------------------------------------
# Integration with real PersistenceStore
# ------------------------------------------------------------------


def test_helper_with_real_persistence_store(tmp_path: Path):
    """Smoke test: helper funciona com PersistenceStore real (não fake)."""
    real_store = _make_store(tmp_path)
    real_store.init()
    settings = _FakeSettings(persistence_enabled=True)
    out = build_prompt_with_context(
        "user prompt", settings=settings, store=real_store
    )
    # Como o store foi inicializado, deve ter tags
    assert "<persistent-agents-context>" in out
    assert "<persistent-projects-context>" in out
    assert "<persistent-memory-context>" in out
    assert out.endswith("user prompt")


def test_helper_with_real_store_uninitialized(tmp_path: Path):
    """Real store mas sem .initialized marker → retorna prompt original."""
    real_store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=10_000,
        seed_templates=False,
    )
    real_store.init()
    # Remover o marker para forçar is_initialized=False
    (real_store.base_dir / ".initialized").unlink()
    settings = _FakeSettings(persistence_enabled=True)
    out = build_prompt_with_context("user", settings=settings, store=real_store)
    assert out == "user"


def test_helper_with_real_store_only_agents_has_content(tmp_path: Path):
    """Real store: AGENTS tem seed, mas PROJECTS e MEMORY estão vazios → só AGENTS tag."""
    real_store = _make_store(tmp_path)
    real_store.init()
    # Esvaziar PROJECTS.md e MEMORY.md para que load_context não retorne excerpts
    (real_store.base_dir / "PROJECTS.md").write_text("")
    (real_store.base_dir / "MEMORY.md").write_text("")
    settings = _FakeSettings(persistence_enabled=True)
    out = build_prompt_with_context("user", settings=settings, store=real_store)
    assert "<persistent-agents-context>" in out
    assert "<persistent-projects-context>" not in out
    assert "<persistent-memory-context>" not in out
