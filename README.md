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

**Prompts**
- `prompt_sync_orchestration`: guidance for `agy_run_task`
- `prompt_async_orchestration`: guidance for `agy_start_task` → `agy_poll_task` → `agy_cancel_task`
- `prompt_model_selection_guidance`: explains how model selection works and its limitations
- `prompt_security_and_workspace_rules`: summarizes workspace and safety rules for orchestrators

## Quickstart

Prerequisites:
- Python 3.11+
- uv installed
- `agy` installed and authenticated (run `agy -i` once and complete any login flow)

```bash
uv sync

uv run python -m fastmcp.cli run src/agy_mcp_server/server.py --transport stdio
```

Run tests:

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

### `Unable to handle .../.venv`

This usually comes from your editor's Python environment discovery trying to load an invalid venv. Recreate the environment with `uv sync` and ensure your server config points at the correct interpreter under `.venv/bin/python`.
