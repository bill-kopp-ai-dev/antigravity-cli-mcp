"""Seed templates for the three persistence files.

Used by ``PersistenceStore.init`` when ``seed_templates=True`` (the default).

Keep the templates small and high-signal. They are intentionally written
in English so they can be edited by any language-aware agent later.
"""

from __future__ import annotations

SEED_VERSION = "1.0.0"

AGENTS_TEMPLATE = """# AGENTS — {provider} CLI

> System prompt editável para o agente orquestrador (Trae IDE) que
> consome o MCP `{provider}-mcp-server`. Edite livremente; mudanças são
> persistidas e aplicadas em sessões futuras.

## Identidade

Você é um agente orquestrador que usa o `{provider}` CLI como backend
de raciocínio via este MCP.

## Diretrizes de uso das tools

1. Antes de cada tarefa, chame `{provider}_load_persistence_context` para
   carregar contexto persistente.
2. Após cada sessão/tarefa significativa, chame
   `{provider}_append_persistence(file="memory", ...)` com um resumo curto.
3. Nunca exponha o conteúdo de `~/.open-cli-router/{provider}/` em logs.
4. Não armazene segredos ou credenciais em `MEMORY.md`.

## Segurança

- Modo `safe` exige `confirm=true` para sobrescrever este arquivo.
- Tools de persistência recusam nomes fora de `agents | projects | memory`.
- Escritas são atômicas (tmp + rename) — perda de dados em meio a uma
  operação é improvável.
"""

PROJECTS_TEMPLATE = """# Projects

> Resumos dos projetos em andamento. Cada seção `## <project>` é
> editável. Anexe novos projetos com `{provider}_update_persistence`.

(nenhum projeto registrado ainda)
"""

MEMORY_TEMPLATE = """# Memory

> Memória permanente do agente. Atualize após cada sessão
> significativa usando `{provider}_append_persistence(file="memory", ...)`
> ou `{provider}_update_persistence(section_anchor="...")`.

<!-- New entries are appended below this line. -->
"""


def render_agents_template(provider: str) -> str:
    return AGENTS_TEMPLATE.format(provider=provider)


def render_projects_template(provider: str) -> str:
    return PROJECTS_TEMPLATE.format(provider=provider)


def render_memory_template(provider: str) -> str:
    return MEMORY_TEMPLATE.format(provider=provider)