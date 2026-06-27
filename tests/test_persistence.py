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
    # Phase 3 (C4): marker agora inclui número de chars omitidos.
    assert "[truncated 4800 chars]" in ctx.memory_excerpt


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
    """Simulate a fork: changing PERSISTENCE_NAMESPACE remaps the persistence dir.

    For agy, PERSISTENCE_NAMESPACE happens to coincide with PROVIDER_PREFIX
    (both "agy"), but the persistence layer reads from PERSISTENCE_NAMESPACE.
    This test sets PERSISTENCE_NAMESPACE (not PROVIDER_PREFIX) to confirm
    that the persistence layer is driven by the right constant.
    """
    import agy_mcp_server.provider as provider_mod

    monkeypatch.setattr(provider_mod, "PERSISTENCE_NAMESPACE", "claude")

    d = get_persistence_dir(tmp_path / ".open-cli-router")
    assert d.name == "claude"

    store = PersistenceStore(base_dir=tmp_path / ".open-cli-router")
    store.init()
    assert (store.base_dir / "AGENTS.md").exists()
    # Template uses {provider} which is rendered from PROVIDER_PREFIX (still "agy").
    # The directory name (namespace) is what changed.
    text = (store.base_dir / "AGENTS.md").read_text()
    assert "agy" in text.lower()  # template still rendered with PROVIDER_PREFIX


def test_provider_namespace_decoupled_from_prefix(monkeypatch, tmp_path: Path):
    """PERSISTENCE_NAMESPACE and PROVIDER_PREFIX podem divergir (paridade com claude).

    Cenário típico de fork futuro: wire prefix curto ("codex") + namespace
    legível em disco ("codex-cli"). O diretório de persistência deve seguir
    o namespace, não o prefix.
    """
    import agy_mcp_server.provider as provider_mod

    monkeypatch.setattr(provider_mod, "PROVIDER_PREFIX", "codex")
    monkeypatch.setattr(provider_mod, "PERSISTENCE_NAMESPACE", "codex-cli")

    d = get_persistence_dir(tmp_path / ".open-cli-router")
    assert d.name == "codex-cli"  # namespace, não prefix

    store = PersistenceStore(base_dir=tmp_path / ".open-cli-router")
    store.init()
    assert (store.base_dir / "AGENTS.md").exists()
    text = (store.base_dir / "AGENTS.md").read_text()
    # Template renderiza {provider} com PROVIDER_PREFIX="codex"
    assert "codex" in text.lower()


def test_namespace_constant_exported_from_provider():
    """Garante que PERSISTENCE_NAMESPACE é exportado de agy_mcp_server.provider."""
    from agy_mcp_server import provider

    assert hasattr(provider, "PERSISTENCE_NAMESPACE")
    assert isinstance(provider.PERSISTENCE_NAMESPACE, str)
    assert provider.PERSISTENCE_NAMESPACE  # non-empty


def test_paths_module_uses_namespace_not_prefix(monkeypatch, tmp_path: Path):
    """Regressão: paths.py não deve mais consultar PROVIDER_PREFIX.

    Se paths.py ainda lesse PROVIDER_PREFIX (em vez de PERSISTENCE_NAMESPACE),
    mudar só PERSISTENCE_NAMESPACE não teria efeito.
    """
    import agy_mcp_server.provider as provider_mod
    from agy_mcp_server.persistence.paths import (
        _current_namespace,
        get_persistence_dir,
    )

    monkeypatch.setattr(provider_mod, "PROVIDER_PREFIX", "wire-prefix")
    monkeypatch.setattr(provider_mod, "PERSISTENCE_NAMESPACE", "disk-ns")

    assert _current_namespace() == "disk-ns"
    d = get_persistence_dir(tmp_path / ".open-cli-router")
    assert d.name == "disk-ns"


# ------------------------------------------------------------------
# Phase 1: confirm field enforcement (paridade com claude)
# ------------------------------------------------------------------


def test_update_persistence_request_has_confirm_field():
    """Default do campo confirm é False (backwards compatible)."""
    from agy_mcp_server.models import AgyUpdatePersistenceRequest

    req = AgyUpdatePersistenceRequest(
        file="memory",
        section_anchor="foo",
        new_content="## foo\nbar\n",
    )
    assert req.confirm is False


def test_update_agents_in_safe_mode_requires_confirm(tmp_path: Path, monkeypatch):
    """Em safe mode, atualizar AGENTS.md sem confirm=true lança ValueError."""
    from agy_mcp_server import server as server_mod
    from agy_mcp_server.models import AgyUpdatePersistenceRequest

    # Redireciona persistence store para tmp_path
    test_store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=10_000,
        seed_templates=True,
    )
    test_store.init()
    monkeypatch.setattr(server_mod, "_persistence_store", test_store)
    monkeypatch.setattr(server_mod._settings, "persistence_enabled", True)
    monkeypatch.setattr(server_mod._settings, "mode", "safe")

    req = AgyUpdatePersistenceRequest(
        file="agents",
        section_anchor="Identity",
        new_content="## Identity\nreplaced\n",
        confirm=False,
    )
    with pytest.raises(ValueError, match="CONFIRM_REQUIRED"):
        server_mod.agy_update_persistence(req=req)


def test_update_agents_in_safe_mode_with_confirm_succeeds(tmp_path: Path, monkeypatch):
    """Em safe mode, atualizar AGENTS.md com confirm=true é permitido."""
    from agy_mcp_server import server as server_mod
    from agy_mcp_server.models import AgyUpdatePersistenceRequest

    test_store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=10_000,
        seed_templates=True,
    )
    test_store.init()
    monkeypatch.setattr(server_mod, "_persistence_store", test_store)
    monkeypatch.setattr(server_mod._settings, "persistence_enabled", True)
    monkeypatch.setattr(server_mod._settings, "mode", "safe")

    req = AgyUpdatePersistenceRequest(
        file="agents",
        section_anchor="Identity",
        new_content="## Identity\nreplaced content\n",
        confirm=True,
    )
    resp = server_mod.agy_update_persistence(req=req)
    assert resp.matched is True


def test_update_memory_in_safe_mode_no_confirm_required(tmp_path: Path, monkeypatch):
    """Em safe mode, atualizar MEMORY.md não exige confirm."""
    from agy_mcp_server import server as server_mod
    from agy_mcp_server.models import AgyUpdatePersistenceRequest

    test_store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=10_000,
        seed_templates=True,
    )
    test_store.init()
    monkeypatch.setattr(server_mod, "_persistence_store", test_store)
    monkeypatch.setattr(server_mod._settings, "persistence_enabled", True)
    monkeypatch.setattr(server_mod._settings, "mode", "safe")

    # memory: confirm=False é OK
    req = AgyUpdatePersistenceRequest(
        file="memory",
        section_anchor="NoMatch",
        new_content="trailing",
        mode="append",
    )
    resp = server_mod.agy_update_persistence(req=req)
    assert resp.matched is True


def test_update_projects_in_safe_mode_no_confirm_required(tmp_path: Path, monkeypatch):
    """Em safe mode, atualizar PROJECTS.md não exige confirm."""
    from agy_mcp_server import server as server_mod
    from agy_mcp_server.models import AgyUpdatePersistenceRequest

    test_store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=10_000,
        seed_templates=True,
    )
    test_store.init()
    monkeypatch.setattr(server_mod, "_persistence_store", test_store)
    monkeypatch.setattr(server_mod._settings, "persistence_enabled", True)
    monkeypatch.setattr(server_mod._settings, "mode", "safe")

    req = AgyUpdatePersistenceRequest(
        file="projects",
        section_anchor="NoMatch",
        new_content="trailing",
        mode="append",
    )
    resp = server_mod.agy_update_persistence(req=req)
    assert resp.matched is True


def test_update_agents_in_permissive_mode_no_confirm_required(tmp_path: Path, monkeypatch):
    """Em permissive mode, atualizar AGENTS.md não exige confirm."""
    from agy_mcp_server import server as server_mod
    from agy_mcp_server.models import AgyUpdatePersistenceRequest

    test_store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=10_000,
        seed_templates=True,
    )
    test_store.init()
    monkeypatch.setattr(server_mod, "_persistence_store", test_store)
    monkeypatch.setattr(server_mod._settings, "persistence_enabled", True)
    monkeypatch.setattr(server_mod._settings, "mode", "permissive")

    req = AgyUpdatePersistenceRequest(
        file="agents",
        section_anchor="Identity",
        new_content="## Identity\nreplaced\n",
        confirm=False,
    )
    resp = server_mod.agy_update_persistence(req=req)
    assert resp.matched is True


def test_update_persistence_disabled_raises_before_confirm_check(tmp_path: Path, monkeypatch):
    """Se persistence_enabled=False, falha com PERSISTENCE_DISABLED (não CONFIRM_REQUIRED)."""
    from agy_mcp_server import server as server_mod
    from agy_mcp_server.models import AgyUpdatePersistenceRequest

    test_store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        max_file_bytes=10_000,
        seed_templates=True,
    )
    monkeypatch.setattr(server_mod, "_persistence_store", test_store)
    monkeypatch.setattr(server_mod._settings, "persistence_enabled", False)
    monkeypatch.setattr(server_mod._settings, "mode", "safe")

    req = AgyUpdatePersistenceRequest(
        file="agents",
        section_anchor="Identity",
        new_content="x",
        confirm=True,  # mesmo com confirm, falha antes
    )
    with pytest.raises(ValueError, match="PERSISTENCE_DISABLED"):
        server_mod.agy_update_persistence(req=req)


# ------------------------------------------------------------------
# Phase 3: robustness improvements (C1-C5)
# ------------------------------------------------------------------


# --- C1: section_header normalization ---

def test_append_section_header_normalizes_double_hash(tmp_path: Path):
    """Cobre C1: cliente envia '## foo' não deve duplicar prefixo."""
    store = _make_store(tmp_path)
    store.init()
    store.append("memory", "body", section_header="## foo")
    text = store.read("memory").content
    assert text.count("## foo") == 1
    assert "## ## foo" not in text


def test_append_section_header_normalizes_whitespace(tmp_path: Path):
    """Cobre C1: espaços extras ao redor do header são stripped."""
    store = _make_store(tmp_path)
    store.init()
    store.append("memory", "body", section_header="  ##  bar  ")
    text = store.read("memory").content
    assert text.count("## bar") == 1


def test_append_section_header_dedup_case_insensitive(tmp_path: Path):
    """Cobre C1: dedup case-insensitive (foo vs Foo vs FOO)."""
    store = _make_store(tmp_path)
    store.init()
    store.append("memory", "body1", section_header="foo")
    store.append("memory", "body2", section_header="FOO")
    store.append("memory", "body3", section_header="Foo")
    text = store.read("memory").content
    # Deve haver APENAS um header (case-insensitive match)
    assert sum(1 for line in text.splitlines() if line.rstrip().lower() == "## foo") == 1


def test_append_empty_header_after_normalization_raises(tmp_path: Path):
    """Cobre C1: header que vira vazio após normalização lança ValueError."""
    store = _make_store(tmp_path)
    store.init()
    with pytest.raises(ValueError, match="INVALID_SECTION_HEADER"):
        store.append("memory", "body", section_header="##")
    with pytest.raises(ValueError, match="INVALID_SECTION_HEADER"):
        store.append("memory", "body", section_header="   ")


def test_append_existing_section_header_is_deduped_case_insensitively(tmp_path: Path):
    """Cobre C1: arquivo já tem '## Foo', append com 'foo' deve ser deduped."""
    store = _make_store(tmp_path)
    store.init()
    # Cria manualmente uma seção
    store.append("memory", "## Foo\ncontent\n")
    # Tenta adicionar com lowercase — não deve duplicar
    store.append("memory", "more", section_header="foo")
    text = store.read("memory").content
    assert sum(1 for line in text.splitlines() if line.rstrip().lower() == "## foo") == 1


# --- C2: case-insensitive anchor matching ---

def test_update_section_anchor_is_case_insensitive(tmp_path: Path):
    """Cobre C2: anchor 'foo' deve encontrar '## Foo'."""
    store = _make_store(tmp_path)
    store.init()
    # Cria uma seção com header "MyProject"
    target = store.base_dir / "PROJECTS.md"
    target.write_text("# Projects\n\n## MyProject\nold body\n\n## Other\nkeep\n")
    result = store.update("projects", "myproject", "## MyProject\nnew body\n")
    assert result.matched
    text = store.read("projects").content
    assert "new body" in text
    assert "old body" not in text
    assert "## Other" in text  # outras seções preservadas


def test_update_section_anchor_strips_hash_prefix(tmp_path: Path):
    """Cobre C2: anchor '## Foo' (com prefixo) normalizado para 'Foo'."""
    store = _make_store(tmp_path)
    store.init()
    target = store.base_dir / "PROJECTS.md"
    target.write_text("## Foo\nold\n")
    result = store.update("projects", "## Foo", "## Foo\nnew\n")
    assert result.matched
    assert "new" in store.read("projects").content


def test_update_section_anchor_empty_after_normalization_returns_miss(tmp_path: Path):
    """Cobre C2: anchor que normaliza para vazio → matched=False (não erro)."""
    store = _make_store(tmp_path)
    store.init()
    result = store.update("memory", "##", "x")
    assert result.matched is False


# --- C3: backup rotation ---

def test_backup_rotation_keeps_last_n(tmp_path: Path):
    """Cobre C3: rotação mantém apenas últimos N backups."""
    store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        backup_on_write=True,
        backup_keep=3,
        seed_templates=False,
    )
    store.init()

    # 5 escritas — cada uma com timestamp único (segundos inteiros via sleep)
    import time
    for i in range(5):
        store.append("memory", f"entry {i}")
        # Forçar timestamp diferente (ISO-8601 inclui segundos)
        time.sleep(1.05)

    backup_dir = store.base_dir / ".backups"
    backups = sorted(backup_dir.glob("MEMORY.md.*.bak"))
    assert len(backups) == 3, f"expected 3 backups after rotation, got {len(backups)}"


def test_backup_rotation_disabled_keeps_all(tmp_path: Path):
    """Cobre C3: backup_keep=0 (sem rotação agressiva) — todos preservados.

    Observação: backup_keep=0 significa manter 0 backups após escrita.
    Mas o backup recém-criado é contado: a rotação remove o que excede
    N. Se N=0, o backup atual é removido. Aqui testamos N grande.
    """
    store = PersistenceStore(
        base_dir=tmp_path / ".open-cli-router",
        backup_on_write=True,
        backup_keep=100,
        seed_templates=False,
    )
    store.init()
    import time
    for i in range(3):
        store.append("memory", f"entry {i}")
        time.sleep(1.05)

    backup_dir = store.base_dir / ".backups"
    backups = sorted(backup_dir.glob("MEMORY.md.*.bak"))
    assert len(backups) == 3


# --- C4: asymmetric truncation ---

def test_load_context_truncation_favors_tail(tmp_path: Path):
    """Cobre C4: head_ratio=0.2 → ~20% head, ~80% tail."""
    store = _make_store(tmp_path)  # default head_ratio=0.2
    store.init()
    # 1000 chars: primeiros = "AAAAAAAA..." (50x A), últimos = "ZZZZZ..." (50x Z)
    text = ("A" * 500) + ("B" * 500)
    (store.base_dir / "MEMORY.md").write_text(text)

    ctx = store.load_context(max_chars_per_file=100)
    assert ctx.truncated_flags["memory"] is True
    excerpt = ctx.memory_excerpt
    # Com head_ratio=0.2 e max_chars=100 → head_size=20, tail_size=80
    # Head = primeiros 20 chars (todos 'A')
    assert excerpt.startswith("A" * 20)
    # Tail = últimos 80 chars (todos 'B')
    assert excerpt.endswith("B" * 80)


def test_load_context_truncation_head_ratio_validation(tmp_path: Path):
    """Cobre C4: head_ratio fora de [0,1] é rejeitado."""
    with pytest.raises(ValueError, match="INVALID_HEAD_RATIO"):
        PersistenceStore(base_dir=tmp_path, head_ratio=-0.1)
    with pytest.raises(ValueError, match="INVALID_HEAD_RATIO"):
        PersistenceStore(base_dir=tmp_path, head_ratio=1.5)


def test_load_context_truncation_omitted_chars_in_marker(tmp_path: Path):
    """Cobre C4: marker de truncamento inclui número de chars omitidos."""
    store = _make_store(tmp_path)
    store.init()
    (store.base_dir / "MEMORY.md").write_text("X" * 1000)
    ctx = store.load_context(max_chars_per_file=100)
    excerpt = ctx.memory_excerpt
    # 1000 - 100 = 900 chars omitidos
    assert "[truncated 900 chars]" in excerpt


# --- C5: read() truncated flag logic ---

def test_read_truncated_when_limit_exhausts_file(tmp_path: Path):
    """Cobre C5: read com limit < tamanho do arquivo → truncated=True."""
    store = _make_store(tmp_path)
    store.init()
    (store.base_dir / "AGENTS.md").write_text("X" * 1000)

    result = store.read("agents", limit=100)
    assert result.truncated is True
    assert len(result.content) == 100


def test_read_not_truncated_when_limit_covers_file(tmp_path: Path):
    """Cobre C5: read com limit >= tamanho do arquivo → truncated=False."""
    store = _make_store(tmp_path)
    store.init()
    (store.base_dir / "AGENTS.md").write_text("X" * 100)

    result = store.read("agents", limit=200)
    assert result.truncated is False
    assert len(result.content) == 100


def test_read_with_offset_and_limit(tmp_path: Path):
    """Cobre C5: offset+limit — truncated se ainda há dados após."""
    store = _make_store(tmp_path)
    store.init()
    (store.base_dir / "AGENTS.md").write_text("ABCDEFGHIJKLMNOPQRSTUVWXYZ")  # 26 chars

    # offset=10, limit=5 → lê "KLMNO", ainda há 11 chars após offset (total 26)
    result = store.read("agents", offset=10, limit=5)
    assert result.content == "KLMNO"
    assert result.truncated is True


def test_read_offset_at_end_returns_not_truncated(tmp_path: Path):
    """Cobre C5: offset no fim do arquivo + limit suficiente → não truncated."""
    store = _make_store(tmp_path)
    store.init()
    (store.base_dir / "AGENTS.md").write_text("ABCDE")  # 5 chars

    # offset=5 (fim), limit=10 → lê 0 chars, nada após
    result = store.read("agents", offset=5, limit=10)
    assert result.content == ""
    assert result.truncated is False