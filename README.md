# agy-mcp-server

Version: 0.0.1

A local STDIO MCP server that exposes tools and reusable prompts for running the Google Antigravity CLI (`agy`) inside a controlled workspace.

## Links

- Model Context Protocol (MCP): https://modelcontextprotocol.io/
- FastMCP: https://github.com/PrefectHQ/fastmcp
- FastMCP docs: https://gofastmcp.com/
- uv: https://github.com/astral-sh/uv
- uv docs: https://docs.astral.sh/uv/
- Pydantic: https://github.com/pydantic/pydantic
- Pydantic Settings: https://github.com/pydantic/pydantic-settings
- Antigravity CLI (agy): https://github.com/google-antigravity/antigravity-cli

## Why This Project Exists

> Some time ago I started using Google Antigravity to program. It was the best agentic IDE available. I canceled my OpenAI subscription and bought a year of Gemini Pro (it sounded like a good deal). After that, our favorite woke MegaCorp:
>
> - Changed pricing in a way that made it impossible for developers in developing countries to use their products
> - Replaced the models that worked well with a new version that feels like a nerfed version of the old one
> - Shipped the worst upgrade in history with Antigravity 2.0, which feels like a toy IDE for vibe coders (and it burns tokens like a Chevrolet Chevelle)
>
> Today I woke up, Antigravity 2.0 auto-updated, and I felt deceived. Not knowing what to do with a 1-year Pro plan, I decided to build this MCP server to make my plan portable. Now I can use my Google plan via CLI in any agentic IDE.
>
> This is freedom.
>
> Now you can use your Google plan via antigravity-cli in any IDE:
> - Cursor
> - Windsurf
> - Trae (my favorite)
>
> I will not stop here. I will evolve this MCP server and build others to enable additional CLIs (codex, claude, deepseek, etc).
>
> Enjoy.

## Features

**Tools**
- `agy_health`: checks that `agy` is installed and returns its version
- `agy_run_task`: runs a synchronous (blocking) task
- `agy_start_task`: starts an asynchronous task
- `agy_poll_task`: polls an asynchronous task
- `agy_cancel_task`: cancels a running task
- `agy_list_runs`: lists recent runs
- `agy_quota`: hybrid quota inspection (local counter, failure classifier,
  opt-in probe, opt-in external API)
- `agy_clear_cache`: clears the `uv` cache to recover from stale-package errors
- `agy_init_persistence`: creates `~/.open-cli-router/agy/` with editable
  `AGENTS.md`, `PROJECTS.md`, `MEMORY.md`
- `agy_read_persistence`: reads one of the three persistence files
- `agy_append_persistence`: appends content (typical use: post-session memory)
- `agy_update_persistence`: replaces a section by heading anchor
- `agy_load_persistence_context`: loads truncated excerpts as session context

**Prompts**
- `prompt_sync_orchestration`: guidance for `agy_run_task`
- `prompt_async_orchestration`: guidance for `agy_start_task` → `agy_poll_task` → `agy_cancel_task`
- `prompt_model_selection_guidance`: explains how model selection works and its limitations
- `prompt_security_and_workspace_rules`: summarizes workspace and safety rules for orchestrators
- `agy_persistence_protocol`: instructs the orchestrator on how to maintain
  the persistent memory layer

## Persistent Memory

This MCP server ships with a file-based persistence layer. The
orchestrator (Trae IDE) gets editable markdown files for system prompt
(`AGENTS.md`), project summaries (`PROJECTS.md`), and permanent memory
(`MEMORY.md`). When enabled, the server automatically prepends excerpts
of these files to the prompt sent to `agy` so context survives across
sessions. See [PLAN_PERSISTENCE.md](PLAN_PERSISTENCE.md) and
[CONTRATO_TOOLS.md](CONTRATO_TOOLS.md) for details.

### Storage location: global vs workspace

The persistence directory can live in **two places**, controlled by
`AGY_MCP_PERSISTENCE_LOCATION`:

| Mode | Location | Use case |
|------|----------|----------|
| `global` (default) | `~/.open-cli-router/agy/` | User-level, persists across projects, survives `cd` |
| `workspace` | `<cwd_parent>/.open-cli-router/agy/` | Project-level, can be committed (use `.gitignore`!), portable with the repo |

`<cwd_parent>` is the parent of the server's CWD — for a typical setup,
the server's CWD is the server project directory (e.g.
`/home/user/CLI-router-project/antigravity-cli-mcp`), so the workspace
mode resolves to `/home/user/CLI-router-project/.open-cli-router/agy/`.

**Escape hatch:** `AGY_MCP_PERSISTENCE_BASE_DIR="$cwd_parent/custom"` lets
you pick any custom subdirectory under the workspace root.

> ⚠️ When using `workspace` mode, add `.open-cli-router/` to `.gitignore`
> to avoid accidentally committing agent memory to source control.

### Two-level configuration

Persistence is controlled by **both** server-level environment variables and runtime MCP tool calls:

**Server level** (set in the MCP client `env` block — see [MCP Client Configuration](#mcp-client-configuration)):

| Variable | Purpose | Default |
|----------|---------|---------|
| `AGY_MCP_PERSISTENCE_ENABLED` | Master switch for the persistence feature | `true` |
| `AGY_MCP_PERSISTENCE_LOCATION` | `"global"` (in `~`) or `"workspace"` (in `<cwd_parent>/.open-cli-router/`) | `global` |
| `AGY_MCP_PERSISTENCE_BASE_DIR` | Base directory; the namespace `agy` is appended automatically. Supports `$cwd_parent` token for custom workspace paths. | `~/.open-cli-router` |
| `AGY_MCP_PERSISTENCE_MAX_FILE_BYTES` | Maximum size per file before writes are rejected | `524288` (512 KiB) |
| `AGY_MCP_PERSISTENCE_BACKUP_ON_WRITE` | Create `.bak` before each modification | `false` |
| `AGY_MCP_PERSISTENCE_BACKUP_KEEP` | Number of `.bak` files to retain per source file (rotation) | `10` |
| `AGY_MCP_PERSISTENCE_SEED_TEMPLATES` | Seed default markdown content when initializing | `true` |
| `AGY_MCP_PERSISTENCE_TRUNCATION_HEAD_RATIO` | Fraction of `max_chars_per_file` preserved at head (rest is tail). Lower = more recency. | `0.2` (20% head / 80% tail) |

**Runtime level** (called by the orchestrator via MCP tools):

1. **Initialize once** — call `agy_init_persistence` to create the directory and seed the three files.
2. **Load context** — call `agy_load_persistence_context` at the start of each session to inject excerpts into the next prompt.
3. **Append session notes** — after meaningful work, call `agy_append_persistence` on `MEMORY.md`.
4. **Update structured sections** — when the user changes `AGENTS.md` or `PROJECTS.md`, call `agy_update_persistence` to persist. **Note:** updating `AGENTS.md` in safe mode requires `confirm=true`.

Without step 1, persistence is **enabled but uninitialized** — the server will not inject any context until the directory exists.

### How to initialize the persistence directory

The persistence directory is created lazily — it does **not** exist by default. There are two equivalent ways to create it:

**Option A — via MCP (recommended, normal flow):**

Once both the MCP client configuration and the server are running, ask the orchestrator agent to call:

```text
Please call agy_init_persistence to create the persistence directory.
```

The tool seeds `AGENTS.md`, `PROJECTS.md`, `MEMORY.md`, and a `.initialized` marker under the resolved base dir (either `~/.open-cli-router/agy/` or `<workspace>/.open-cli-router/agy/` depending on `persistence_location`).

**Option B — directly via Python (one-shot, useful for verification or first-time setup):**

From the project root (`antigravity-cli-mcp/`):

```bash
uv run python -c "from agy_mcp_server.persistence import PersistenceStore; from pathlib import Path; PersistenceStore(base_dir=Path.home()/'.open-cli-router', max_file_bytes=524288, backup_on_write=False, seed_templates=True).init()"
```

This call is idempotent — running it twice does not destroy existing data unless you pass `force=True`.

The seed templates are written in **English** so they can be edited by any language-aware agent later.

## Quickstart

Prerequisites:
- Python 3.11+
- uv installed
- `agy` installed and authenticated (run `agy -i` once and complete any login flow)

```bash
uv sync

uv run python -m fastmcp.cli run src/agy_mcp_server/server.py --transport stdio
```

## MCP Client Configuration

The server is launched by an MCP client (Trae, Cursor, Windsurf, etc.) over STDIO. The recommended setup uses `uvx` to install the package from a local source path on demand — no global Python install required.

### Trae / Cursor / Windsurf (`uvx` from local source)

Add to your MCP client configuration (`~/.trae/mcp.json`, `.cursor/mcp.json`, `.windsurf/mcp.json`, or the IDE's MCP settings panel):

```json
{
  "mcpServers": {
    "agy-mcp-server": {
      "command": "uvx",
      "args": [
        "--refresh",
        "--from",
        "/path/to/antigravity-cli-mcp",
        "fastmcp",
        "run",
        "src/agy_mcp_server/server.py"
      ],
      "cwd": "/path/to/antigravity-cli-mcp",
      "env": {
        "AGY_MCP_MODE": "safe",
        "AGY_MCP_ALLOWED_ROOTS": "[\"/path/to/your/projects\"]",
        "AGY_MCP_FORCE_SANDBOX_IN_SAFE_MODE": "true",
        "START_MCP_TIMEOUT_MS": "30000",
        "RUN_MCP_TIMEOUT_MS": "600000"
      }
    }
  }
}
```

> **Tip:** `--refresh` forces `uvx` to re-resolve the local source on every start. Drop it once you stop iterating on the server.
>
> **Tip:** `START_MCP_TIMEOUT_MS` and `RUN_MCP_TIMEOUT_MS` are **client-side** timeouts consumed by the Trae IDE (not by this server).

### Relevant environment variables

The most relevant variables for the `env` block are listed below. See [Configuration](#configuration) for the full reference.

| Variable | Purpose |
|----------|---------|
| `AGY_MCP_MODE` | `safe` (default) or `permissive` |
| `AGY_MCP_ALLOWED_ROOTS` | JSON list of workspace roots the server is allowed to access |
| `AGY_MCP_FORCE_SANDBOX_IN_SAFE_MODE` | `true` enforces sandboxing under safe mode |
| `AGY_MCP_DEFAULT_TIMEOUT_S` | Default per-task timeout in seconds (must be ≤ 3600) |
| `AGY_MCP_PERSISTENCE_ENABLED` | Enables the persistent memory layer (`true` by default) |
| `AGY_MCP_PERSISTENCE_BASE_DIR` | Base directory for the persistence layer (`~/.open-cli-router`) |

> **Persistence is a two-level configuration.** The env vars above only **enable** the feature and pick the base directory. To actually create the files and start injecting context, the orchestrator must call `agy_init_persistence` once at startup. See [Persistent Memory](#persistent-memory) for the full lifecycle.

### Step-by-step Trae setup (Portuguese)

For a guided walkthrough in Portuguese, see [USO_TRAE.md](USO_TRAE.md).

## Running Tests

To install dev dependencies and execute the test suite:

```bash
uv sync --extra dev
uv run pytest
```

## Using This Server in Trae

See [USO_TRAE.md](USO_TRAE.md).

## Configuration

This server uses environment variables via Pydantic Settings (prefix `AGY_MCP_`).

| Variable | Description | Default |
|----------|-------------|---------|
| `AGY_MCP_ALLOWED_ROOTS` | JSON list of allowed workspace roots | current `cwd` |
| `AGY_MCP_MODE` | `safe` or `permissive` | `safe` |
| `AGY_MCP_DEFAULT_TIMEOUT_S` | default timeout (seconds) | `300` |
| `AGY_MCP_MAX_OUTPUT_BYTES` | stdout/stderr capture limit | `2000000` |
| `AGY_MCP_FORCE_SANDBOX_IN_SAFE_MODE` | enforce sandbox in safe mode | `true` |
| `AGY_MCP_ALLOW_ENV_KEYS` | JSON list of allowed env keys (permissive mode) | `[]` |
| `AGY_MCP_ALLOW_EXTRA_ARGS` | JSON list of allowlisted extra args (permissive mode) | `[]` |
| `AGY_MCP_PERSISTENCE_ENABLED` | Enables the persistent markdown memory layer. | `true` |
| `AGY_MCP_PERSISTENCE_BASE_DIR` | Base directory for the persistence layer (namespace `agy` is appended automatically). | `~/.open-cli-router` |
| `AGY_MCP_PERSISTENCE_MAX_FILE_BYTES` | Maximum file size for persistence files before rejecting writes. | `524288` (512 KiB) |
| `AGY_MCP_PERSISTENCE_BACKUP_ON_WRITE` | Create a `.bak` backup copy of files before modification. | `false` |
| `AGY_MCP_PERSISTENCE_SEED_TEMPLATES` | Seed default markdown files if missing on init. | `true` |

Local configuration:

```bash
cp .env.example .env
```

## Antigravity CLI Model Selection

The MCP server does not control which model `agy` uses for each run. The model is chosen inside the interactive CLI and persists across sessions.

1) Start the interactive CLI:

```bash
agy -i
```

2) Type `/model`:

```
> /model
> /model  Set a model
  ↑/↓ Navigate  · enter Select  · tab Complete
esc to cancel
```

3) Select a model (use arrow keys and Enter). The active model is marked as `(current)`:

```
Switch Model

Gemini 3.5 Flash (Medium) (current)
Gemini 3.5 Flash (High)
Gemini 3.5 Flash (Low)
Gemini 3.1 Pro (Low)
> Gemini 3.1 Pro (High)
Gemini 3 Flash

Keyboard: ↑/↓ Navigate  enter Select  esc Go Back
```

Model settings are stored under:

```
~/.gemini/antigravity/
```

## Security

Safe mode (`AGY_MCP_MODE=safe`) is the default:
- `env` is rejected
- `extra_args` is rejected
- `dangerously_skip_permissions` is rejected
- sandbox can be enforced

Permissive mode still enforces explicit allowlists:
- `AGY_MCP_ALLOW_ENV_KEYS`
- `AGY_MCP_ALLOW_EXTRA_ARGS`

See [CONTRATO_TOOLS.md](CONTRATO_TOOLS.md) for the tool contract and available prompts.

## Troubleshooting

### `No module named fastmcp.__main__`

Use `fastmcp.cli` (not `fastmcp`) when invoking through Python:

```bash
python -m fastmcp.cli run src/agy_mcp_server/server.py --transport stdio
```

### `agy not found in PATH`

```bash
which agy
```

If needed, set `AGY_MCP_AGY_PATH` to the full path of the `agy` binary.

### `workspace_path is outside allowed roots`

Set `AGY_MCP_ALLOWED_ROOTS` to include your workspace root:

```bash
export AGY_MCP_ALLOWED_ROOTS='["/path/to/your/projects"]'
```

### `~/.open-cli-router/agy/` does not exist
The persistence directory is created lazily. The server will not create it on its own — you must initialize it once via `agy_init_persistence` (or the Python one-liner under [Persistent Memory → How to initialize](#how-to-initialize-the-persistence-directory)). Without this, the server is **enabled but uninitialized** and no context is injected into prompts.

### `Unable to handle .../.venv`

This usually comes from your editor's Python environment discovery trying to load an invalid venv. Recreate the environment with `uv sync` and ensure your server config points at the correct interpreter under `.venv/bin/python`.
