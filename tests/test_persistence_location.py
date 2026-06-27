"""Tests for Settings.resolve_persistence_base_dir() (Phase 5).

The feature introduces a ``persistence_location`` setting with values
``"global"`` (default) and ``"workspace"`` (parent of server's CWD), plus
a ``$cwd_parent`` escape hatch in ``persistence_base_dir``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError


@pytest.fixture
def restore_cwd():
    """Snapshot and restore the current working directory around a test.

    ``monkeypatch.chdir`` reverts at the end of the test, but does NOT
    guarantee the CWD change persists across the test body in all cases.
    Using ``os.chdir`` directly with explicit snapshot/restore is more
    reliable here.
    """
    original = os.getcwd()
    try:
        yield
    finally:
        os.chdir(original)


def _make_settings(monkeypatch, **overrides):
    """Build a Settings instance with all env vars cleared, then apply overrides.

    Note: pydantic-settings reads env vars AT INSTANTIATION. So we clear
    relevant vars first, then set the ones we want.
    """
    # Clear all persistence-related env vars to start fresh
    for key in [
        "AGY_MCP_PERSISTENCE_BASE_DIR",
        "AGY_MCP_PERSISTENCE_LOCATION",
    ]:
        monkeypatch.delenv(key, raising=False)
    # Apply overrides
    for k, v in overrides.items():
        monkeypatch.setenv(f"AGY_MCP_{k.upper()}", v)
    # Import inside the function so monkeypatch delenv has effect
    from agy_mcp_server.settings import Settings

    return Settings()


# ------------------------------------------------------------------
# Default behavior (global)
# ------------------------------------------------------------------


def test_resolve_default_location_is_global():
    from agy_mcp_server.settings import Settings

    s = Settings()
    assert s.persistence_location == "global"


def test_resolve_default_base_dir_is_open_cli_router(monkeypatch):
    s = _make_settings(monkeypatch)
    resolved = s.resolve_persistence_base_dir()
    assert resolved == Path("~/.open-cli-router").expanduser()


def test_resolve_global_returns_explicit_base_dir(monkeypatch, tmp_path):
    custom = tmp_path / "my-data"
    s = _make_settings(
        monkeypatch,
        persistence_base_dir=str(custom),
        persistence_location="global",
    )
    resolved = s.resolve_persistence_base_dir()
    assert resolved == custom


# ------------------------------------------------------------------
# Workspace mode
# ------------------------------------------------------------------


def test_resolve_workspace_returns_cwd_parent(monkeypatch, tmp_path, restore_cwd):
    fake_server_dir = tmp_path / "fake_server"
    fake_server_dir.mkdir()
    os.chdir(fake_server_dir)

    s = _make_settings(monkeypatch, persistence_location="workspace")
    resolved = s.resolve_persistence_base_dir()
    assert resolved == tmp_path / ".open-cli-router"


def test_resolve_workspace_ignores_persistence_base_dir(monkeypatch, tmp_path, restore_cwd):
    """When location=workspace, persistence_base_dir é ignorado (cwd_parent tem precedência)."""
    fake_server_dir = tmp_path / "fake_server"
    fake_server_dir.mkdir()
    os.chdir(fake_server_dir)

    s = _make_settings(
        monkeypatch,
        persistence_location="workspace",
        persistence_base_dir="/some/other/path",
    )
    resolved = s.resolve_persistence_base_dir()
    # Não usa /some/other/path, usa cwd_parent
    assert resolved == tmp_path / ".open-cli-router"


def test_workspace_persistence_creates_files_in_cwd_parent(
    monkeypatch, tmp_path, restore_cwd
):
    """init() em modo workspace cria no cwd_parent (smoke test)."""
    fake_server_dir = tmp_path / "fake_server"
    fake_server_dir.mkdir()
    os.chdir(fake_server_dir)

    from agy_mcp_server.persistence import PersistenceStore

    s = _make_settings(monkeypatch, persistence_location="workspace")
    store = PersistenceStore(base_dir=s.resolve_persistence_base_dir())
    store.init()

    assert (tmp_path / ".open-cli-router" / "agy" / "AGENTS.md").exists()
    assert (tmp_path / ".open-cli-router" / "agy" / "MEMORY.md").exists()
    assert (tmp_path / ".open-cli-router" / "agy" / "PROJECTS.md").exists()
    assert (tmp_path / ".open-cli-router" / "agy" / ".initialized").exists()


def test_workspace_unwritable_fails_loudly(monkeypatch, tmp_path, restore_cwd):
    """Workspace em FS onde parent não pode virar diretório → falha.

    Cenário: criamos um ARQUIVO no caminho onde o diretório deveria ser
    criado. Em Linux, chdir para arquivo não funciona, então não podemos
    testar essa falha via chdir. Em vez disso, testamos um cenário
    equivalente: ``base_dir`` é um path dentro de um diretório read-only.
    """
    import stat
    import tempfile

    # Cria um diretório read-only e tenta criar .open-cli-router dentro dele
    ro_dir = tmp_path / "read_only"
    ro_dir.mkdir()
    # Bloqueia escrita
    ro_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x apenas

    # O base_dir do store será tmp_path/read_only/.open-cli-router
    # que não pode ser criado (Permission denied)
    from agy_mcp_server.persistence import PersistenceStore

    store = PersistenceStore(base_dir=ro_dir / ".open-cli-router")
    try:
        with pytest.raises((ValueError, PermissionError, OSError)):
            store.init()
    finally:
        # Restaura permissão para cleanup
        ro_dir.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


# ------------------------------------------------------------------
# $cwd_parent escape hatch
# ------------------------------------------------------------------


def test_resolve_cwd_parent_token_in_base_dir(monkeypatch, tmp_path, restore_cwd):
    """$cwd_parent literal em persistence_base_dir é expandido."""
    fake_server_dir = tmp_path / "fake_server"
    fake_server_dir.mkdir()
    os.chdir(fake_server_dir)

    s = _make_settings(
        monkeypatch,
        persistence_base_dir="$cwd_parent/.my-custom",
    )
    # Importante: $cwd_parent tem precedência sobre persistence_location
    resolved = s.resolve_persistence_base_dir()
    assert resolved == tmp_path / ".my-custom"


def test_resolve_cwd_parent_token_with_workspace_location(monkeypatch, tmp_path, restore_cwd):
    """$cwd_parent funciona mesmo com location=workspace (token tem precedência)."""
    fake_server_dir = tmp_path / "fake_server"
    fake_server_dir.mkdir()
    os.chdir(fake_server_dir)

    s = _make_settings(
        monkeypatch,
        persistence_base_dir="$cwd_parent/.my-custom",
        persistence_location="workspace",
    )
    resolved = s.resolve_persistence_base_dir()
    assert resolved == tmp_path / ".my-custom"


def test_cwd_parent_token_empty_suffix(monkeypatch, tmp_path, restore_cwd):
    """$cwd_parent sem sufixo = parent do CWD puro."""
    fake_server_dir = tmp_path / "fake_server"
    fake_server_dir.mkdir()
    os.chdir(fake_server_dir)

    s = _make_settings(monkeypatch, persistence_base_dir="$cwd_parent")
    resolved = s.resolve_persistence_base_dir()
    assert resolved == tmp_path


def test_cwd_parent_token_with_path_separator(monkeypatch, tmp_path, restore_cwd):
    """$cwd_parent com / no início é normalizado."""
    fake_server_dir = tmp_path / "fake_server"
    fake_server_dir.mkdir()
    os.chdir(fake_server_dir)

    s = _make_settings(monkeypatch, persistence_base_dir="$cwd_parent/./data")
    resolved = s.resolve_persistence_base_dir()
    assert resolved == tmp_path / "data"


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------


def test_invalid_location_raises_validation_error(monkeypatch):
    """persistence_location com valor inválido falha Pydantic ValidationError."""
    monkeypatch.setenv("AGY_MCP_PERSISTENCE_LOCATION", "bogus")
    from agy_mcp_server.settings import Settings

    with pytest.raises(ValidationError):
        Settings()


def test_default_location_unchanged_is_global():
    """Regressão: default é 'global' (não mudou comportamento padrão)."""
    from agy_mcp_server.settings import Settings

    s = Settings()
    assert s.persistence_location == "global"
    # E resolve_persistence_base_dir() retorna o path default
    resolved = s.resolve_persistence_base_dir()
    assert resolved == Path("~/.open-cli-router").expanduser()


# ------------------------------------------------------------------
# Cross-mode isolation: dados globais não são tocados por workspace
# ------------------------------------------------------------------


def test_workspace_mode_does_not_touch_global_data(monkeypatch, tmp_path, restore_cwd):
    """Mudar para workspace NÃO apaga ~/.open-cli-router/agy/ existente."""
    # Setup: cria dados em "global" dentro de tmp_path (não polui ~/)
    global_dir = tmp_path / "global_data"
    monkeypatch.setenv("AGY_MCP_PERSISTENCE_BASE_DIR", str(global_dir))

    from agy_mcp_server.persistence import PersistenceStore
    from agy_mcp_server.settings import Settings

    s_global = Settings()
    store_global = PersistenceStore(base_dir=s_global.resolve_persistence_base_dir())
    store_global.init()
    assert (global_dir / "agy" / "MEMORY.md").exists()

    # Agora muda para workspace
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()  # precisa existir antes de criar subdir
    (workspace_root / "fake_server").mkdir()
    os.chdir(workspace_root / "fake_server")
    monkeypatch.setenv("AGY_MCP_PERSISTENCE_LOCATION", "workspace")

    s_workspace = Settings()
    store_workspace = PersistenceStore(
        base_dir=s_workspace.resolve_persistence_base_dir()
    )
    store_workspace.init()

    # Dados globais intactos
    assert (global_dir / "agy" / "MEMORY.md").exists()
    # Dados locais novos
    assert (workspace_root / ".open-cli-router" / "agy" / "MEMORY.md").exists()
    # São arquivos diferentes (mas ambos têm o seed template)
    global_content = (global_dir / "agy" / "MEMORY.md").read_text()
    workspace_content = (workspace_root / ".open-cli-router" / "agy" / "MEMORY.md").read_text()
    assert global_content == workspace_content


# ------------------------------------------------------------------
# Settings integração: backup_keep e truncation_head_ratio
# ------------------------------------------------------------------


def test_backup_keep_default_is_10():
    from agy_mcp_server.settings import Settings

    s = Settings()
    assert s.persistence_backup_keep == 10


def test_truncation_head_ratio_default_is_0_2():
    from agy_mcp_server.settings import Settings

    s = Settings()
    assert s.persistence_truncation_head_ratio == 0.2


def test_backup_keep_propagates_to_store(monkeypatch, tmp_path):
    """Settings.persistence_backup_keep → PersistenceStore(...backup_keep=...)."""
    monkeypatch.setenv("AGY_MCP_PERSISTENCE_BACKUP_KEEP", "5")
    from agy_mcp_server.settings import Settings
    from agy_mcp_server.persistence import PersistenceStore

    s = Settings()
    store = PersistenceStore(
        base_dir=s.resolve_persistence_base_dir(),
        backup_keep=s.persistence_backup_keep,
    )
    assert store._backup_keep == 5


def test_truncation_head_ratio_propagates_to_store(monkeypatch):
    """Settings.persistence_truncation_head_ratio → head_ratio do store."""
    monkeypatch.setenv("AGY_MCP_PERSISTENCE_TRUNCATION_HEAD_RATIO", "0.5")
    from agy_mcp_server.settings import Settings
    from agy_mcp_server.persistence import PersistenceStore

    s = Settings()
    store = PersistenceStore(
        base_dir=s.resolve_persistence_base_dir(),
        head_ratio=s.persistence_truncation_head_ratio,
    )
    assert store._head_ratio == 0.5
