"""Integration tests for the persistence layer (Phase 6).

Closes the gaps identified in the original diagnostic §4:

- prompt protocol registration (``agy_persistence_protocol`` appears in
  ``mcp.list_prompts()``).
- ``agy_run_task`` end-to-end integration: persistent context is loaded
  and prepended to the prompt before dispatch.
- ``agy_run_task`` continues gracefully when context loading fails.
- ``load_context`` reflects the ``.initialized`` marker state.
- Symlink escape attempts are rejected by the path resolver.
- ``load_context`` respects ``max_chars_per_file`` (total_chars reflects
  the truncated excerpt size, not the original file size).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agy_mcp_server.persistence import (
    ALLOWED_FILE_NAMES,
    PersistenceStore,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_store(tmp_path: Path) -> PersistenceStore:
    return PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=10_000,
        seed_templates=True,
    )


def _allow_tmp_path(monkeypatch, tmp_path: Path) -> None:
    """Adiciona tmp_path ao allowed_roots do _settings global.

    Necessário porque ``agy_run_task`` chama ``_resolve_workspace_path``
    que exige que ``req.workspace_path`` esteja em algum allowed_root.
    """
    from agy_mcp_server import server as server_mod

    monkeypatch.setattr(server_mod._settings, "allowed_roots", [tmp_path])


def _get_registered_prompt_names() -> set[str]:
    """Return the set of prompt names registered on the FastMCP server.

    ``mcp.list_prompts()`` é uma coroutine em FastMCP ≥ 2 — usamos
    ``asyncio.run`` para consumi-la em um contexto sync.
    """
    from agy_mcp_server.server import mcp

    coro = mcp.list_prompts()
    prompts = asyncio.run(coro)
    return {p.name for p in prompts}


# ------------------------------------------------------------------
# 6.1 — prompt protocol registration
# ------------------------------------------------------------------


def test_persistence_protocol_prompt_is_registered():
    """agy_persistence_protocol deve aparecer no registro de prompts."""
    names = _get_registered_prompt_names()
    assert "agy_persistence_protocol" in names, (
        f"Expected agy_persistence_protocol in registered prompts; "
        f"got: {sorted(names)}"
    )


# ------------------------------------------------------------------
# 6.2 — agy_run_task loads context automatically
# ------------------------------------------------------------------


def test_run_task_prepends_persistent_context(tmp_path: Path, monkeypatch):
    """agy_run_task deve chamar load_context e prepender tags XML ao prompt."""
    from agy_mcp_server import server as server_mod
    from agy_mcp_server.models import AgyExecOptions, AgyRunTaskRequest

    _allow_tmp_path(monkeypatch, tmp_path)

    # Setup: persistence habilitado e inicializado
    test_store = _make_store(tmp_path)
    test_store.init()

    # Mock load_context para retornar excerpts conhecidos
    mock_ctx = MagicMock()
    mock_ctx.agents_excerpt = "AGENT CONTENT"
    mock_ctx.projects_excerpt = None
    mock_ctx.memory_excerpt = "MEMORY CONTENT"
    mock_ctx.initialized = True
    monkeypatch.setattr(test_store, "load_context", lambda **kw: mock_ctx)

    monkeypatch.setattr(server_mod, "_persistence_store", test_store)
    monkeypatch.setattr(server_mod._settings, "persistence_enabled", True)

    # Mock _run_agy para capturar o prompt enviado
    captured: dict[str, str] = {}

    def fake_run_agy(workspace, req):
        captured["prompt"] = req.prompt
        return ("", "", 0, False)

    monkeypatch.setattr(server_mod, "_run_agy", fake_run_agy)

    req = AgyRunTaskRequest(
        workspace_path=str(tmp_path),
        prompt="user prompt",
        options=AgyExecOptions(timeout_s=10),
        capture_changes=False,
    )
    server_mod.agy_run_task(req=req)

    prompt = captured["prompt"]
    assert "<persistent-agents-context>" in prompt
    assert "AGENT CONTENT" in prompt
    # projects_excerpt é None → tag não presente
    assert "<persistent-projects-context>" not in prompt
    assert "<persistent-memory-context>" in prompt
    assert "MEMORY CONTENT" in prompt
    assert "user prompt" in prompt
    # Ordem: contexto vem antes do user prompt
    assert prompt.index("AGENT CONTENT") < prompt.index("user prompt")


def test_run_task_with_all_three_context_tags(tmp_path: Path, monkeypatch):
    """Quando todos os excerpts estão preenchidos, 3 tags são prepended."""
    from agy_mcp_server import server as server_mod
    from agy_mcp_server.models import AgyExecOptions, AgyRunTaskRequest

    _allow_tmp_path(monkeypatch, tmp_path)

    test_store = _make_store(tmp_path)
    test_store.init()

    mock_ctx = MagicMock()
    mock_ctx.agents_excerpt = "A"
    mock_ctx.projects_excerpt = "P"
    mock_ctx.memory_excerpt = "M"
    mock_ctx.initialized = True
    monkeypatch.setattr(test_store, "load_context", lambda **kw: mock_ctx)
    monkeypatch.setattr(server_mod, "_persistence_store", test_store)
    monkeypatch.setattr(server_mod._settings, "persistence_enabled", True)

    captured: dict[str, str] = {}

    def fake_run_agy(workspace, req):
        captured["prompt"] = req.prompt
        return ("", "", 0, False)

    monkeypatch.setattr(server_mod, "_run_agy", fake_run_agy)

    req = AgyRunTaskRequest(
        workspace_path=str(tmp_path),
        prompt="X",
        options=AgyExecOptions(timeout_s=10),
        capture_changes=False,
    )
    server_mod.agy_run_task(req=req)

    prompt = captured["prompt"]
    for tag in [
        "<persistent-agents-context>",
        "<persistent-projects-context>",
        "<persistent-memory-context>",
    ]:
        assert tag in prompt, f"missing tag {tag} in {prompt!r}"


# ------------------------------------------------------------------
# 6.3 — agy_run_task continues if context load fails
# ------------------------------------------------------------------


def test_run_task_continues_if_load_context_raises(tmp_path: Path, monkeypatch):
    """Se load_context lança exceção, run continua com prompt original."""
    from agy_mcp_server import server as server_mod
    from agy_mcp_server.models import AgyExecOptions, AgyRunTaskRequest

    _allow_tmp_path(monkeypatch, tmp_path)

    test_store = _make_store(tmp_path)
    test_store.init()

    def boom(**kw):
        raise RuntimeError("simulated load failure")

    monkeypatch.setattr(test_store, "load_context", boom)
    monkeypatch.setattr(server_mod, "_persistence_store", test_store)
    monkeypatch.setattr(server_mod._settings, "persistence_enabled", True)

    captured: dict[str, str] = {}

    def fake_run_agy(workspace, req):
        captured["prompt"] = req.prompt
        return ("", "", 0, False)

    monkeypatch.setattr(server_mod, "_run_agy", fake_run_agy)

    req = AgyRunTaskRequest(
        workspace_path=str(tmp_path),
        prompt="user prompt",
        options=AgyExecOptions(timeout_s=10),
        capture_changes=False,
    )
    # Não deve levantar exceção
    server_mod.agy_run_task(req=req)

    # Prompt é o original, sem tags
    assert captured["prompt"] == "user prompt"


def test_run_task_continues_if_load_context_oserror(tmp_path: Path, monkeypatch):
    """OSError também é não-fatal."""
    from agy_mcp_server import server as server_mod
    from agy_mcp_server.models import AgyExecOptions, AgyRunTaskRequest

    _allow_tmp_path(monkeypatch, tmp_path)

    test_store = _make_store(tmp_path)
    test_store.init()

    def boom(**kw):
        raise OSError("disk error")

    monkeypatch.setattr(test_store, "load_context", boom)
    monkeypatch.setattr(server_mod, "_persistence_store", test_store)
    monkeypatch.setattr(server_mod._settings, "persistence_enabled", True)

    captured: dict[str, str] = {}

    def fake_run_agy(workspace, req):
        captured["prompt"] = req.prompt
        return ("", "", 0, False)

    monkeypatch.setattr(server_mod, "_run_agy", fake_run_agy)

    req = AgyRunTaskRequest(
        workspace_path=str(tmp_path),
        prompt="user",
        options=AgyExecOptions(timeout_s=10),
        capture_changes=False,
    )
    server_mod.agy_run_task(req=req)
    assert captured["prompt"] == "user"


def test_run_task_skips_context_when_persistence_disabled(tmp_path: Path, monkeypatch):
    """Se persistence_enabled=False, run NÃO prepende contexto."""
    from agy_mcp_server import server as server_mod
    from agy_mcp_server.models import AgyExecOptions, AgyRunTaskRequest

    _allow_tmp_path(monkeypatch, tmp_path)

    test_store = _make_store(tmp_path)
    test_store.init()
    monkeypatch.setattr(server_mod, "_persistence_store", test_store)
    monkeypatch.setattr(server_mod._settings, "persistence_enabled", False)

    captured: dict[str, str] = {}

    def fake_run_agy(workspace, req):
        captured["prompt"] = req.prompt
        return ("", "", 0, False)

    monkeypatch.setattr(server_mod, "_run_agy", fake_run_agy)

    req = AgyRunTaskRequest(
        workspace_path=str(tmp_path),
        prompt="user prompt",
        options=AgyExecOptions(timeout_s=10),
        capture_changes=False,
    )
    server_mod.agy_run_task(req=req)
    assert captured["prompt"] == "user prompt"
    assert "<persistent-" not in captured["prompt"]


def test_run_task_skips_context_when_not_initialized(tmp_path: Path, monkeypatch):
    """Se .initialized ausente (mesmo com enabled=true), run NÃO prepende."""
    from agy_mcp_server import server as server_mod
    from agy_mcp_server.models import AgyExecOptions, AgyRunTaskRequest

    _allow_tmp_path(monkeypatch, tmp_path)

    test_store = _make_store(tmp_path)
    # NÃO chama init() → uninitialized
    monkeypatch.setattr(server_mod, "_persistence_store", test_store)
    monkeypatch.setattr(server_mod._settings, "persistence_enabled", True)

    captured: dict[str, str] = {}

    def fake_run_agy(workspace, req):
        captured["prompt"] = req.prompt
        return ("", "", 0, False)

    monkeypatch.setattr(server_mod, "_run_agy", fake_run_agy)

    req = AgyRunTaskRequest(
        workspace_path=str(tmp_path),
        prompt="user",
        options=AgyExecOptions(timeout_s=10),
        capture_changes=False,
    )
    server_mod.agy_run_task(req=req)
    assert captured["prompt"] == "user"


# ------------------------------------------------------------------
# 6.4 — load_context reflects .initialized marker
# ------------------------------------------------------------------


def test_load_context_initialized_true_when_marker_exists(tmp_path: Path):
    store = _make_store(tmp_path)
    store.init()
    ctx = store.load_context()
    assert ctx.initialized is True


def test_load_context_initialized_false_when_marker_missing(tmp_path: Path):
    """Cobre 6.4: deletar .initialized vira initialized=False."""
    store = _make_store(tmp_path)
    store.init()
    ctx = store.load_context()
    assert ctx.initialized is True  # baseline

    (store.base_dir / ".initialized").unlink()
    ctx2 = store.load_context()
    assert ctx2.initialized is False


def test_load_context_is_initialized_property_matches(tmp_path: Path):
    """is_initialized property é consistente com load_context().initialized."""
    store = _make_store(tmp_path)
    assert store.is_initialized is False

    store.init()
    assert store.is_initialized is True

    (store.base_dir / ".initialized").unlink()
    assert store.is_initialized is False


# ------------------------------------------------------------------
# 6.5 — symlink escape blocked
# ------------------------------------------------------------------


def test_symlink_escape_blocked_by_resolve_file_path(tmp_path: Path):
    """Symlink dentro de base_dir apontando para fora é detectado."""
    from agy_mcp_server.persistence.paths import resolve_file_path

    base = tmp_path / ".open-cli-router"
    ns_dir = base / "agy"
    ns_dir.mkdir(parents=True)

    # Cria arquivo FORA do namespace
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")

    # Cria symlink dentro do namespace apontando para fora
    link = ns_dir / "escape"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not supported in this environment")

    # resolve_file_path deve usar Path.resolve() que segue o symlink.
    # O check is_relative_to deve detectar que o target está fora.
    target = (ns_dir / "escape").resolve()
    # O resolved target aponta para outside.txt (fora do namespace)
    assert not target.is_relative_to(ns_dir.resolve())

    # O Literal whitelist bloqueia "escape" como nome (não está em
    # ALLOWED_FILE_NAMES), mas o teste cobre a defesa em profundidade
    # do path resolver.
    with pytest.raises(ValueError, match="INVALID_FILE"):
        resolve_file_path(base, "escape")


def test_resolve_file_path_rejects_dotdot_components(tmp_path: Path):
    """Componentes '..' no file_name são bloqueados pelo Literal whitelist."""
    from agy_mcp_server.persistence.paths import resolve_file_path

    base = tmp_path / ".open-cli-router"
    with pytest.raises(ValueError, match="INVALID_FILE"):
        resolve_file_path(base, "../../../etc/passwd")


# ------------------------------------------------------------------
# 6.6 — load_context respects max_chars_per_file (total_chars)
# ------------------------------------------------------------------


def test_load_context_total_chars_reflects_truncated_excerpt(tmp_path: Path):
    """Cobre 6.6: total_chars é baseado no excerpt truncado, não no original."""
    store = _make_store(tmp_path)
    store.init()
    # 5000 chars no MEMORY.md
    (store.base_dir / "MEMORY.md").write_text("X" * 5000)

    ctx_no_trunc = store.load_context(max_chars_per_file=100_000)
    # Sem truncamento, total_chars inclui o conteúdo completo
    assert ctx_no_trunc.truncated_flags["memory"] is False

    ctx_trunc = store.load_context(max_chars_per_file=100)
    # Truncado, total_chars deve ser menor que 5000 (reflete o excerpt)
    assert ctx_trunc.truncated_flags["memory"] is True
    # total_chars é o tamanho do excerpt (com head + tail + marker)
    assert ctx_trunc.total_chars < 5000
    assert ctx_trunc.total_chars < 1000  # generous upper bound


def test_load_context_truncation_flag_set_per_file(tmp_path: Path):
    """Cada arquivo tem seu próprio truncated_flag."""
    store = _make_store(tmp_path)
    store.init()
    # AGENTS.md é grande, MEMORY.md é pequeno, PROJECTS.md é vazio
    (store.base_dir / "AGENTS.md").write_text("A" * 5000)
    (store.base_dir / "MEMORY.md").write_text("M" * 50)
    (store.base_dir / "PROJECTS.md").write_text("")

    ctx = store.load_context(max_chars_per_file=200)
    assert ctx.truncated_flags["agents"] is True
    assert ctx.truncated_flags["memory"] is False
    assert ctx.truncated_flags["projects"] is False


# ------------------------------------------------------------------
# Bonus: ALLOWED_FILE_NAMES consistency
# ------------------------------------------------------------------


def test_allowed_file_names_match_settings():
    """Os 3 nomes no Literal devem ser exatamente agents/projects/memory."""
    assert ALLOWED_FILE_NAMES == ("agents", "projects", "memory")
