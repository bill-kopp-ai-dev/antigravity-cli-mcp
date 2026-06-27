# Plano de Refatoração — Persistência do MCP Antigravity

> Documento de planejamento. **Nenhum código foi alterado ainda.** Esta etapa
> descreve *o que* será feito e *por quê*; a implementação virá em uma fase
> posterior, após aprovação.

---

## 1. Contexto e motivação

O servidor MCP `agy-mcp-server` hoje é **stateless**: nada sobrevive entre
chamadas ou entre reinicializações. Quando o Trae IDE orquestra um agente que
usa o `agy`, cada nova sessão começa do zero — sem system-prompt customizado,
sem memória de projetos anteriores, sem histórico de decisões.

Inspiração: `femtobot` resolve isso com um sistema de memória em camadas
(`memory/MEMORY.md`, `SOUL.md`, `USER.md`, `history.jsonl`) versionado por
`GitStore`. Vamos adaptar o conceito para o caso MCP, onde o "agente" é a IDE
agentica do Trae consumindo as tools deste servidor.

Decisão de design já tomada pelo Bill:

- Diretório base: **`~/.open-cli-router/`** (multi-provider)
- Subdiretório por provedor: **`~/.open-cli-router/agy/`**
- Três arquivos Markdown editáveis:
  - **`AGENTS.md`** — system-prompt editável do agente
  - **`PROJECTS.md`** — resumos dos projetos em andamento
  - **`MEMORY.md`** — memória permanente, atualizada após cada sessão
- Múltiplos MCPs coexistirão nesse layout (`~/.open-cli-router/claude-code/`,
  `~/.open-cli-router/codex/`, etc.)

---

## 2. Objetivos e não-objetivos

### Objetivos

1. Adicionar **persistência file-based** ao servidor MCP `agy`.
2. Expor **tools MCP** para criar, ler e atualizar os três arquivos.
3. Carregar **automaticamente** o conteúdo como **contexto** quando o orquestrador
   (Trae) inicia uma sessão via `agy_run_task` / `agy_start_task`.
4. Fornecer um **prompt MCP** que instrui o agente orquestrador sobre como
   manter a persistência (write-after-session, "Dream cycle", etc.).
5. Layout **multi-provider**: a constante base (`~/.open-cli-router`) é
   compartilhada com futuros forks (`claude-code-cli-mcp`, etc.).
6. Manter **atomicidade** de escrita e **validação de paths** (sem traversal).
7. Não quebrar comportamento atual — tudo é **opt-in** via env var.

### Não-objetivos

1. Não implementar versionamento automático via `git` na v1 (opt-in depois).
2. Não consolidar memória automaticamente (Dream/Consolidator ficam para v2).
3. Não expor edição dos arquivos fora do `persistence_base_dir`.
4. Não persistir `RunStore` (continua in-memory; já documentado).
5. Não persistir `_quota_tracker` na v1 (counter é in-memory por design).

---

## 3. Arquitetura proposta

### 3.1 Estrutura de diretórios em runtime

```
~/.open-cli-router/                ← PERSISTENCE_BASE_DIR
├── agy/                           ← PROVIDER_NAMESPACE (= "agy")
│   ├── AGENTS.md                  ← system-prompt editável
│   ├── PROJECTS.md                ← resumos de projetos
│   ├── MEMORY.md                  ← memória permanente
│   ├── .initialized               ← marker file (created by agy_init_persistence)
│   └── .backups/                  ← backups automáticos (opt-in)
│       └── MEMORY.md.2026-06-21T17-00-00Z.bak
├── claude-code/                   ← futuro fork
└── codex/                         ← futuro fork
```

### 3.2 Componentes novos (a serem implementados)

| Módulo | Responsabilidade |
|---|---|
| `agy_mcp_server/persistence/__init__.py` | Pacote, exports. |
| `agy_mcp_server/persistence/paths.py` | Resolve `PERSISTENCE_BASE_DIR` + `PROVIDER_NAMESPACE` → paths concretos. Reusa o padrão `provider.py`. |
| `agy_mcp_server/persistence/store.py` | `PersistenceStore` — classe principal (single source of truth, análoga a `MemoryStore` do femtobot). Métodos: `init()`, `read(name)`, `append(name, ...)`, `replace(name, ...)`, `update_section(name, anchor, ...)`. |
| `agy_mcp_server/persistence/locks.py` | `PersistenceLock` — `threading.Lock` global para serializar escritas (evita race entre tools MCP). |
| `agy_mcp_server/persistence/templates.py` | Conteúdo padrão (seed) para `AGENTS.md`, `PROJECTS.md`, `MEMORY.md` quando `agy_init_persistence` é chamado pela primeira vez. |

### 3.3 Mudanças em arquivos existentes

| Arquivo | Mudança |
|---|---|
| `settings.py` | Adicionar campos `persistence_base_dir`, `persistence_enabled`, `persistence_max_file_bytes`, `persistence_backup_on_write`, `persistence_seed_templates`. |
| `models.py` | Adicionar `AgyInitPersistenceRequest/Response`, `AgyReadPersistenceRequest/Response`, `AgyAppendPersistenceRequest/Response`, `AgyUpdatePersistenceRequest/Response`, `AgyLoadContextRequest/Response`. |
| `provider.py` | Já tem `PROVIDER_PREFIX` — reusar para derivar `PROVIDER_NAMESPACE` (= `PROVIDER_PREFIX`). |
| `server.py` | Instanciar `_persistence_store` no bootstrap, adicionar 5 `@mcp.tool` e 1 `@mcp.prompt`. Integrar `load_context` em `agy_run_task` e `agy_start_task` quando habilitado. |
| `tests/` | Criar `tests/test_persistence.py` (paths, atomic write, locks, validação, integração com `agy_run_task`). |

---

## 4. Settings — campos novos

Adicionar ao `Settings` (Pydantic BaseSettings com prefixo `AGY_MCP_`):

| Campo | Env var | Default | Descrição |
|---|---|---|---|
| `persistence_enabled` | `AGY_MCP_PERSISTENCE_ENABLED` | `true` | Liga/desliga o subsistema inteiro. Quando `false`, as tools ainda existem mas retornam `NOT_INITIALIZED` (ou ficam ocultas — decisão abaixo). |
| `persistence_base_dir` | `AGY_MCP_PERSISTENCE_BASE_DIR` | `~/.open-cli-router` | Diretório base compartilhado entre todos os MCPs desta família. |
| `persistence_max_file_bytes` | `AGY_MCP_PERSISTENCE_MAX_FILE_BYTES` | `524_288` (512 KiB) | Limite de leitura. Arquivos maiores falham com `FILE_TOO_LARGE`. |
| `persistence_backup_on_write` | `AGY_MCP_PERSISTENCE_BACKUP_ON_WRITE` | `false` | Faz `.bak` timestamped antes de cada escrita destrutiva. |
| `persistence_seed_templates` | `AGY_MCP_PERSISTENCE_SEED_TEMPLATES` | `true` | Se `true`, `agy_init_persistence` popula os 3 arquivos com templates. Se `false`, cria vazios. |

**Decisão pendente:** se `persistence_enabled=false`, as tools devem ficar
**ocultas** (não listadas no `mcp.list_tools()`) ou **registradas mas retornando
erro `PERSISTENCE_DISABLED`**? Recomendação: **registradas** — facilita debugging
e mantém o schema MCP estável para o Trae (já vimos o impacto de mudar schema
no episódio do `agy_quota`).

---

## 5. Tools MCP — contratos completos

> Todas as tools seguem o padrão atual: recebem `req: <Name>In`, retornam
> `<Name>`. Naming derivado de `tool_name(...)` em `provider.py`.

### 5.1 `agy_init_persistence` — criar o diretório e os 3 arquivos

**Input:** `AgyInitPersistenceRequestIn`
```python
class AgyInitPersistenceRequest(BaseModel):
    force: bool = False           # se True, sobrescreve arquivos existentes
    seed_templates: bool | None = None  # None = usar settings.persistence_seed_templates
```

**Output:** `AgyInitPersistenceResponse`
```python
class AgyInitPersistenceResponse(BaseModel):
    base_dir: str                 # ex: /home/bill/.open-cli-router/agy
    created: list[str]            # paths criados nesta chamada
    already_existed: list[str]    # paths que já existiam
    seed_version: str             # versão do template usado (ex: "1.0.0")
```

**Comportamento:**
- Idempotente: chamada repetida não duplica.
- Se já existir `.initialized` e `force=False`, retorna `already_existed`.
- Cria os 3 arquivos usando `templates.py` (ver §5.6).
- Cria `.initialized` marker (JSON com timestamp + seed_version).

**Erros possíveis:**
- `PERSISTENCE_DISABLED` (settings)
- `BASE_DIR_NOT_WRITABLE` (permission denied)
- `INVALID_PATH` (symlink escape, etc.)

### 5.2 `agy_read_persistence` — ler um dos arquivos

**Input:** `AgyReadPersistenceRequestIn`
```python
class AgyReadPersistenceRequest(BaseModel):
    file: Literal["agents", "projects", "memory"]
    offset: int = 0               # bytes (0-based) — útil para arquivos grandes
    limit: int | None = None      # bytes; None = sem limite até max_file_bytes
```

**Output:** `AgyReadPersistenceResponse`
```python
class AgyReadPersistenceResponse(BaseModel):
    file: str                     # caminho absoluto
    content: str
    size_bytes: int
    truncated: bool               # True se bateu em max_file_bytes ou limit
    modified_at: datetime | None
```

### 5.3 `agy_append_persistence` — anexar conteúdo (uso típico: MEMORY.md)

**Input:** `AgyAppendPersistenceRequestIn`
```python
class AgyAppendPersistenceRequest(BaseModel):
    file: Literal["agents", "projects", "memory"]
    content: str                  # markdown a anexar
    section_header: str | None = None  # se setado, garante um `## <header>` antes
```

**Output:** `AgyAppendPersistenceResponse`
```python
class AgyAppendPersistenceResponse(BaseModel):
    file: str
    appended_bytes: int
    new_size_bytes: int
    timestamp: datetime
```

**Comportamento:**
- Escrita atômica: grava em `<file>.tmp.<uuid>` e renomeia.
- Se `section_header` for setado e ainda não existir como `## <header>`, insere
  um header antes do `content`.
- Aplica `persistence_backup_on_write` se habilitado.
- Respeita `persistence_max_file_bytes` (fail se exceder *após* append).

### 5.4 `agy_update_persistence` — substituir/editar uma seção

**Input:** `AgyUpdatePersistenceRequestIn`
```python
class AgyUpdatePersistenceRequest(BaseModel):
    file: Literal["agents", "projects", "memory"]
    section_anchor: str           # heading `## ...` a substituir (match exato ou prefixo)
    new_content: str              # markdown que substitui a seção inteira (incluindo heading)
    mode: Literal["replace", "append"] = "replace"
```

**Output:** `AgyUpdatePersistenceResponse`
```python
class AgyUpdatePersistenceResponse(BaseModel):
    file: str
    section_anchor: str
    matched: bool                 # False se anchor não foi encontrada
    new_size_bytes: int
```

**Comportamento:**
- Localiza a seção pelo `## <section_anchor>` e substitui até o próximo `## `.
- `mode="append"` adiciona `new_content` ao final do arquivo (atalho para casos
  sem anchor).
- Se `matched=False` e `mode="replace"`, retorna sucesso com `matched=False`
  (não é erro — caller decide se quer criar a seção).

### 5.5 `agy_load_persistence_context` — carregar os 3 arquivos para a sessão

**Input:** `AgyLoadPersistenceContextRequestIn`
```python
class AgyLoadPersistenceContextRequest(BaseModel):
    include: list[Literal["agents", "projects", "memory"]] = ["agents", "projects", "memory"]
    max_chars_per_file: int = 20_000
```

**Output:** `AgyLoadPersistenceContextResponse`
```python
class AgyLoadPersistenceContextResponse(BaseModel):
    agents_excerpt: str | None
    projects_excerpt: str | None
    memory_excerpt: str | None
    truncated_flags: dict[str, bool]
    total_chars: int
    base_dir: str
    initialized: bool              # False se .initialized não existe
```

**Comportamento:**
- Se `base_dir` não existe ou não foi inicializado, retorna `initialized=False`
  com `excerpts=None` — caller decide se chama `agy_init_persistence`.
- Chamada automaticamente por `agy_run_task`/`agy_start_task` quando
  `persistence_enabled=true`. **Não bloqueia a execução**: se falhar, o run
  continua sem contexto (warning em `notes`).
- Truncamento inteligente: preserva a *head* (system-prompt-like) e a *tail*
  (memória recente), com elipse no meio.

### 5.6 Templates — conteúdo seed

Inspirado em `femtobot/templates/AGENTS.md` e `MEMORY.md`:

```markdown
<!-- ~/.open-cli-router/agy/AGENTS.md (seed v1.0.0) -->

# AGENTS — Antigravity CLI

> System prompt editável para o agente orquestrador (Trae IDE) que
> consome o MCP `agy-mcp-server`. Edite livremente; mudanças são
> persistidas e aplicadas em sessões futuras.

## Identidade

Você é um agente orquestrador que usa o Antigravity CLI (`agy`) como
backend de raciocínio via este MCP.

## Diretrizes de uso das tools

1. Antes de cada tarefa, chame `agy_load_persistence_context` para
   carregar contexto persistente.
2. Após cada sessão/tarefa significativa, chame
   `agy_append_persistence(file="memory", ...)` com um resumo curto.
3. Nunca exponha o conteúdo de `~/.open-cli-router/agy/` em logs.
```

```markdown
<!-- ~/.open-cli-router/agy/PROJECTS.md (seed v1.0.0) -->

# Projects

> Resumos dos projetos em andamento. Cada seção `## <project>` é
> editável. Anexe novos projetos com `agy_update_persistence`.

(nenhum projeto registrado ainda)
```

```markdown
<!-- ~/.open-cli-router/agy/MEMORY.md (seed v1.0.0) -->

# Memory

> Memória permanente do agente. Atualize após cada sessão
> significativa usando `agy_append_persistence(file="memory", ...)`
> ou `agy_update_persistence(section_anchor="...")`.

<!-- New entries are appended below this line. -->
```

---

## 6. Prompt MCP — orientação ao orquestrador

Adicionar em `server.py`:

```python
@mcp.prompt(name=prompt_name("persistence_protocol"))
def prompt_persistence_protocol() -> str:
    """Instructs the orchestrator agent on how to maintain the MCP persistence layer."""
    return (
        "You have access to a persistent memory layer at "
        "~/.open-cli-router/agy/ with three editable files: "
        "AGENTS.md (your editable system prompt), PROJECTS.md "
        "(project summaries), and MEMORY.md (permanent memory).\n"
        "\n"
        "Lifecycle:\n"
        "1. On the first run, call agy_init_persistence to create "
        "the directory and seed files.\n"
        "2. At the start of each session, call "
        "agy_load_persistence_context to load the latest state.\n"
        "3. After each meaningful session, append a concise summary "
        "to MEMORY.md using agy_append_persistence.\n"
        "4. When the user explicitly changes AGENTS.md or "
        "PROJECTS.md, use agy_update_persistence to persist.\n"
        "\n"
        "Do not store secrets, credentials, or full file dumps in "
        "MEMORY.md — keep entries small and high-signal."
    )
```

---

## 7. Integração com tools existentes

### 7.1 `agy_run_task` / `agy_start_task`

Quando `persistence_enabled=true`, **antes** de chamar `agy`:

1. Chamar `agy_load_persistence_context` (internamente, sem nova chamada MCP).
2. Injetar o resultado como **system-prompt adicional** via stdin ou via env
   var `AGY_MCP_PERSISTENCE_CONTEXT` (agy consome? verificar docs do agy).
3. Em caso de falha ao carregar, continuar sem contexto e adicionar warning em
   `notes` da resposta existente (sem quebrar contrato).

**Decisão pendente:** como o `agy` consome system-prompt adicional hoje? Se
for via flag (`--system-prompt-file`), passar o path de um arquivo temp
gerado pelo MCP. Se for stdin, prepender o contexto.

### 7.2 `agy_quota`

Adicionar campo opcional `notes` na resposta incluindo:

> "Last MEMORY.md entry: 2026-06-21 17:00 (agy_quota) — see ~/.open-cli-router/agy/MEMORY.md"

(Útil para debug quando quota é esgotada em sequência.)

---

## 8. Segurança

1. **Path validation:** toda path resolvida em `PersistenceStore` é
   `Path.resolve()` + check `is_relative_to(base_dir)`. Symlinks
   intermediários são recusados.
2. **File size cap:** `persistence_max_file_bytes` aplicado em **leitura** E
   **escrita**. Escrita que excede o limite falha com `FILE_TOO_LARGE` sem
   truncar.
3. **Atomicidade:** `os.replace(tmp, target)` no Linux; no Windows usar
   `os.replace` (também atômico). Lock global via `threading.Lock` para
   serializar entre tools MCP concorrentes.
4. **No secrets warning:** seed templates incluem nota explícita ("Do not store
   secrets in MEMORY.md").
5. **No arbitrary filesystem access:** tools só aceitam os 3 nomes fixos
   (`agents`, `projects`, `memory`). Mesmo se um usuário tentar `file="../../etc/passwd"`,
   o validador `Literal[...]` recusa antes de chegar ao filesystem.
6. **Mode-aware:** em `mode="safe"`, `agy_update_persistence` em `AGENTS.md`
   exige `confirm=true` (campo novo opcional). Em `mode="permissive"`, segue
   livre.

---

## 9. Compatibilidade e migração

1. **Default `persistence_enabled=true`** — todos os usuários do MCP passam a
   ter persistência sem ação manual.
2. **Primeira execução** cria o diretório automaticamente? **Não.** Decisão:
   `agy_init_persistence` precisa ser chamada explicitamente. Auto-init no
   bootstrap do servidor quebraria o princípio de "opt-in" e surpreenderia o
   usuário. Um *prompt* (`prompt_persistence_protocol`) deixa claro que o
   orquestrador deve chamar `agy_init_persistence` na primeira vez.
3. **Backwards compat:** nenhuma das 8 tools existentes muda de assinatura.
   Apenas ganham (opcionalmente) contexto extra carregado automaticamente.
4. **Cache do uv:** mudanças em `models.py` exigem `--refresh` na config do
   Trae (já estamos fazendo isso).

---

## 10. Plano de testes (`tests/test_persistence.py`)

| Teste | O que valida |
|---|---|
| `test_persistence_disabled_raises` | Settings com `persistence_enabled=false` → tools retornam `PERSISTENCE_DISABLED`. |
| `test_init_persistence_creates_files` | `agy_init_persistence` cria os 3 arquivos + `.initialized` com templates. |
| `test_init_persistence_idempotent` | 2ª chamada sem `force` retorna `already_existed`. |
| `test_init_persistence_force_overwrites` | `force=true` sobrescreve. |
| `test_path_traversal_blocked` | `file="../../etc/passwd"` rejeitado pelo Literal. |
| `test_symlink_escape_blocked` | Symlink criado em `base_dir` apontando para fora é recusado. |
| `test_atomic_write_no_partial_file` | Força uma exceção no meio do write; target permanece intacto. |
| `test_concurrent_writes_serialized` | 10 threads × 100 appends → 1000 entradas sem perda. |
| `test_file_size_cap_enforced` | Escrita que excederia `max_file_bytes` falha com `FILE_TOO_LARGE`. |
| `test_append_section_header_inserts_when_missing` | `section_header` cria heading se não existir. |
| `test_update_section_replaces_existing` | Anchor encontrada → substituída até o próximo `## `. |
| `test_update_section_miss_returns_matched_false` | Anchor ausente → `matched=false`, sem erro. |
| `test_load_context_returns_truncation_flags` | Arquivo > max_chars_per_file → `truncated_flags[name]=true`. |
| `test_load_context_uninitialized_returns_false` | Sem `.initialized` → `initialized=false`, excerpts=None. |
| `test_run_task_loads_context_automatically` | Mock `_persistence_store.load_context`; verifica injeção no `agy_run_task`. |
| `test_run_task_continues_if_context_load_fails` | Mock que lança exceção; run continua com warning em `notes`. |
| `test_provider_namespace_uses_prefix` | `PROVIDER_PREFIX="agy"` → `~/.open-cli-router/agy/`. Fork para `"claude"` muda namespace automaticamente. |
| `test_prompt_protocol_is_registered` | `prompt_persistence_protocol` aparece em `mcp.list_prompts()`. |

**Total esperado: 18 testes novos**, somando ~60 testes totais.

---

## 11. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| `agy` CLI não aceita system-prompt extra via flag | Decidir na fase de implementação entre: (a) `--system-prompt-file`, (b) prepender no stdin, (c) usar apenas o contexto para a *IDE agentica* (não para o `agy` diretamente). |
| Diretório `~/.open-cli-router/` pode ser criado em FS read-only | Falha clara em `agy_init_persistence` com `BASE_DIR_NOT_WRITABLE`. |
| Crescimento descontrolado de `MEMORY.md` | (a) `persistence_max_file_bytes`; (b) sugerir truncamento via `agy_update_persistence` no prompt; (c) v2: implementar Dream/Consolidator. |
| Concorrência entre múltiplos MCPs (agy + claude) escrevendo no mesmo `~/.open-cli-router/` | Cada MCP escreve só no *seu* subdiretório (`agy/`, `claude-code/`) — não há colisão. |
| Mudança de schema MCP causa re-descoberta no Trae | Mesmo cuidado do `agy_quota`: incluir fallback de coerção `BeforeValidator(_coerce_empty_str_to_dict)` nos novos `*In` types. |
| Backup `.bak` infinito em `persistence_backup_on_write=true` | v2: rotação automática (manter últimos N). Documentar na v1. |

---

## 12. Decisões abertas (precisam do Bill)

1. **Auto-init no bootstrap?** Minha recomendação: **não** — exige chamada
   explícita de `agy_init_persistence`. Mais previsível e respeita opt-in.
2. **Injeção automática de contexto no `agy_run_task`?** Minha recomendação:
   **sim**, mas apenas quando `persistence_enabled=true` E `base_dir` estiver
   inicializado. Se falhar, warning + continua.
3. **Mecanismo de injeção no `agy`:** precisa investigar se `agy` aceita
   `--system-prompt-file` ou só stdin. Pode mudar o design.
4. **Modo safe exige `confirm=true` para `AGENTS.md`?** Minha recomendação:
   **sim** — protege contra sobrescrita acidental do system-prompt.
5. **`MEMORY.md` ter tamanho máximo?** Sugestão: 1 MiB antes de exigir
   truncamento manual. Configurável via env var.
6. **Versionamento com git?** Recomendo **deixar para v2**. Na v1, oferecer
   apenas o `.bak` simples.

---

## 13. Fases de implementação (visão de alto nível)

1. **Phase 1 — paths + settings:** `persistence/paths.py`, settings novos,
   tests de resolução de path. *Sem tools ainda.*
2. **Phase 2 — store:** `PersistenceStore`, `locks.py`, `templates.py`.
   Tests de atomicidade e concorrência.
3. **Phase 3 — models + tools:** 5 tools MCP no `server.py`. Tests de contrato.
4. **Phase 4 — prompt + integração:** `prompt_persistence_protocol`,
   integração em `agy_run_task`/`agy_start_task`.
5. **Phase 5 — docs:** atualizar `CONTRATO_TOOLS.md`, `README.md`,
   `USO_TRAE.md`.
6. **Phase 6 — fork prep:** verificar que o layout multi-provider funciona
   com `PROVIDER_PREFIX="claude"` (dry-run via teste).

Estimativa de ordem: **3 a 5 dias de trabalho**, similar ao `agy_quota`.

---

## 14. Arquivos a serem criados/modificados (resumo)

**Novos:**
- `src/agy_mcp_server/persistence/__init__.py`
- `src/agy_mcp_server/persistence/paths.py`
- `src/agy_mcp_server/persistence/store.py`
- `src/agy_mcp_server/persistence/locks.py`
- `src/agy_mcp_server/persistence/templates.py`
- `tests/test_persistence.py`

**Modificados:**
- `src/agy_mcp_server/settings.py` (+5 campos)
- `src/agy_mcp_server/models.py` (+5 Request/Response)
- `src/agy_mcp_server/server.py` (imports, instância, 5 tools, 1 prompt,
  integração em `agy_run_task`)
- `CONTRATO_TOOLS.md` (documentar as 5 tools + 1 prompt)
- `README.md` (mencionar persistência)
- `USO_TRAE.md` (exemplo de uso com persistência)

---

## 15. Padrões reutilizados do femtobot

| Padrão femtobot | Adaptação para agy-mcp |
|---|---|
| `MemoryStore` em `agent/memory.py` | Vira `PersistenceStore` em `persistence/store.py`. Mantém a API baseada em arquivos (read/append/replace). |
| `_DEFAULT_MAX_HISTORY` + cursor | Adotamos `persistence_max_file_bytes` como cap simples; cursor fica para v2 (Dream). |
| `GitStore` (versionamento) | Não incluído na v1. Planejado para v2 opt-in. |
| Templates em `femtobot/templates/AGENTS.md`, `memory/MEMORY.md` | Reaproveitamos o estilo (heading + meta-linhas em comentário HTML). |
| `paths.get_memory_dir()` | Vira `persistence/paths.py:resolve_persistence_dir()`. |
| Lock global para append (`_append_lock`) | `PersistenceLock` global para serializar tools MCP concorrentes. |

---

## 16. Refatoração 2026-06 (changelog)

Esta refatoração endereçou 13 problemas mapeados em [PERSISTENCE_ANALYSIS.md](file:///home/bill/Codes/CLI-router-project/PERSISTENCE_ANALYSIS.md) e adicionou 1 feature nova. Executada em 7 fases, todas entregues.

### Phase 1 — Bug fixes (paridade com claude)

- Adicionado campo `confirm: bool = False` no `AgyUpdatePersistenceRequest`.
- Adicionado enforcement em `agy_update_persistence`: em `mode="safe"`, atualizar
  `AGENTS.md` exige `confirm=true` (senão `ValueError("CONFIRM_REQUIRED: ...")`).
- 7 testes novos cobrindo: default False, safe mode + sem confirm → raise,
  safe mode + confirm → ok, memory/projects safe mode → ok, permissive → ok,
  persistence_enabled=false falha antes do check de confirm.

### Phase 2 — Consistência de design

- Introduzida constante `PERSISTENCE_NAMESPACE = "agy"` em
  [agy_mcp_server/provider.py](file:///home/bill/Codes/CLI-router-project/antigravity-cli-mcp/src/agy_mcp_server/provider.py)
  (paridade com `claude-code-cli-mcp` onde `PROVIDER_PREFIX="claude"` e
  `PERSISTENCE_NAMESPACE="claude-code"`).
- Migrado `persistence/paths.py` para usar `PERSISTENCE_NAMESPACE` em vez de
  `PROVIDER_PREFIX` para resolver o diretório em disco. `PROVIDER_PREFIX`
  continua sendo usado para renderizar `{provider}` no template.
- 3 testes novos: `test_provider_namespace_uses_namespace_constant`,
  `test_provider_namespace_decoupled_from_prefix`,
  `test_paths_module_uses_namespace_not_prefix` (regressão).
- Decisão documentada: `persistence_max_file_bytes` default mantido em
  512 KiB (agy é referência; claude será alinhado em plano separado).

### Phase 3 — Robustez do store

- **C1 (section_header normalization):** novo helper
  `_normalize_section_header()` usa regex `^[#\s]+` para remover prefixos
  `#` e whitespace. Bug C1 (cliente enviando `"## foo"` gerava `## ## foo`)
  corrigido. Dedup agora é **case-insensitive** via `_header_line_exists()`.
  Header que normaliza para vazio lança `ValueError("INVALID_SECTION_HEADER")`.
- **C2 (anchor case-insensitive):** `_replace_section()` normaliza o anchor
  e faz match via `.lower()`.
- **C3 (backup rotation):** setting `persistence_backup_keep: int = 10`.
  `_maybe_backup()` agora ordena por timestamp ISO e remove os excedentes.
- **C4 (truncamento assimétrico):** setting `persistence_truncation_head_ratio:
  float = 0.2`. `load_context()` calcula `head_size=int(max_chars*head_ratio)`,
  marker agora inclui `[truncated N chars]` em vez de `[truncated]`.
- **C5 (read() truncated flag):** lógica corrigida: `truncated = remaining_after_offset > len(data)` quando `limit` set, ou `stat.st_size > max_file_bytes` quando sem `limit`.
- 17 testes novos cobrindo os 5 itens.

### Phase 4 — Refatoração de integração

- Novo módulo
  [persistence/context.py](file:///home/bill/Codes/CLI-router-project/antigravity-cli-mcp/src/agy_mcp_server/persistence/context.py)
  com helper `build_prompt_with_context()` (duck-typed via Protocol para
  evitar import cycle com `settings.py`).
- Exportado em `persistence/__init__.py`.
- `agy_run_task` e `agy_start_task` refatorados: ~54 linhas inline duplicadas
  → 8 linhas usando o helper.
- 12 testes novos em `tests/test_persistence_context.py` cobrindo todos os
  caminhos: disabled, uninitialized, prepend completo, ordem dos tags,
  skip de None, all-empty, failure (RuntimeError, OSError), integração com
  store real.

### Phase 5 — Feature nova: `persistence_location`

- Setting novo `persistence_location: Literal["global", "workspace"] = "global"`.
- Setting novo `persistence_backup_keep: int = 10` (Phase 3, integrado no
  bootstrap).
- Setting novo `persistence_truncation_head_ratio: float = 0.2` (Phase 3,
  integrado no bootstrap).
- Método novo `Settings.resolve_persistence_base_dir()` com 3 modos:
  1. `$cwd_parent` token em `persistence_base_dir` (escape hatch, precedência máxima).
  2. `location="workspace"` → `<cwd_parent>/.open-cli-router/`.
  3. Default → `persistence_base_dir` expandido (comportamento legado).
- Bootstrap do `server.py` agora chama `_settings.resolve_persistence_base_dir()`
  e propaga `backup_keep` + `head_ratio` para o `PersistenceStore`.
- `prompt_persistence_protocol` agora inclui nota sobre `persistence_location`
  e warning sobre `.gitignore` em modo workspace.
- `.env.example` documenta todas as variáveis novas com exemplos comentados.
- 18 testes novos em `tests/test_persistence_location.py` cobrindo: default
  global, workspace mode, `$cwd_parent` token, validação (location inválido
  → ValidationError), cross-mode isolation, settings integration.

### Phase 6 — Testes de integração (gap §4 do diagnóstico)

- 15 testes novos em `tests/test_persistence_integration.py` cobrindo os 8 gaps
  identificados:
  - `agy_persistence_protocol` registrado em `mcp.list_prompts()` (usa
    `asyncio.run` para consumir a coroutine).
  - `agy_run_task` prepende tags XML do contexto persistente em modo normal.
  - `agy_run_task` continua sem contexto se `load_context` lança
    `RuntimeError` ou `OSError` (não-fatal).
  - `agy_run_task` pula contexto se `persistence_enabled=False` ou
    `is_initialized=False`.
  - `load_context().initialized` reflete o `.initialized` marker em disco.
  - `resolve_file_path` recusa `..` e symlink escape via `is_relative_to`.
  - `load_context()` respeita `max_chars_per_file`: `total_chars` reflete o
    excerpt truncado, não o arquivo original.

### Phase 7 — Documentação + migração

- `CONTRATO_TOOLS.md`: documentação de `confirm`, novos env vars, truncation
  assimétrica.
- `README.md`: nova seção "Storage location: global vs workspace" com tabela,
  warning sobre `.gitignore`, atualização dos defaults.
- `USO_TRAE.md`: nova subseção "Persistence location: global vs workspace"
  com exemplo de config Trae para workspace mode e nota sobre escape hatch.
- `PLAN_PERSISTENCE.md`: este §16 (changelog).
- `MCP_USER_GUIDE.md` raiz: (atualizado em plano separado se necessário).
- **Migração automática:** **não incluída**. Usuário que muda de
  `global` para `workspace` deve mover manualmente os arquivos existentes:
  ```bash
  mv ~/.open-cli-router/agy <cwd_parent>/.open-cli-router/agy
  ```

### Métricas finais

| Métrica | Antes (PERSISTENCE_ANALYSIS §0) | Depois |
|---|---|---|
| Testes em `test_persistence*.py` | 25 | 88 |
| Total de testes | 72 | 144 |
| Divergências com claude | 5 | 0 (parity total) |
| Bugs em runtime | 2 (`## ## foo`, template errado) | 0 (cobertos por testes) |
| Features | 5 tools + 1 prompt | 5 tools + 1 prompt (mesmas) + `persistence_location` + `$cwd_parent` escape hatch |
| LOC em `server.py` (injeção de contexto) | ~54 inline duplicado | 8 (helper compartilhado) |

**Compatibilidade:** zero impacto em setups existentes — todos os settings
novos têm defaults que reproduzem o comportamento anterior.

### Pendente (deferido para v2)

- D1 Dream cycle / Consolidator
- D2 Versionamento Git automático
- D3 `{provider}_search_persistence`
- D4 Export/import tool
- D5 Métricas em `agy_status`
- D6 `render_session_entry` helper
- C6 Lock per-file
- C7 Cross-MCP awareness
- Migration tool automática entre `global` ↔ `workspace`