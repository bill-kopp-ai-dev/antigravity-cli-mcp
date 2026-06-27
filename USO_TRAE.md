# Using This MCP Server in Trae

This MCP server exposes the Antigravity CLI (`agy`) as tools and prompts for Trae. Trae starts the server locally (STDIO transport) and discovers tools automatically.

## Links

- FastMCP: https://github.com/PrefectHQ/fastmcp
- FastMCP docs: https://gofastmcp.com/
- MCP: https://modelcontextprotocol.io/
- uv: https://docs.astral.sh/uv/

## 1) Prepare the Environment

This project uses `uv` to manage the virtual environment and dependencies.

```bash
uv sync
```

What `uv sync` does:
- reads `pyproject.toml` and `uv.lock`
- creates `.venv/` (if missing)
- installs dependencies (`fastmcp`, `pydantic`, etc.)

## 2) Register the MCP Server in Trae

Trae supports `stdio`, `SSE`, and `Streamable HTTP` MCP servers. The recommended setup is a `stdio` server using `uvx` (the runner that ships with `uv`). Trae itself bundles `uv` and exposes the `uvx` executable on PATH, so no extra Python install is required.

Open `Settings → MCP` in Trae and add a manual server, or create `.trae/mcp.json` at the project root.

### 2.1 Manual setup (recommended)

```json
{
  "mcpServers": {
    "agy-mcp-server": {
      "command": "uvx",
      "args": [
        "--from",
        "/path/to/antigravity-cli-mcp",
        "--with",
        "fastmcp>=3.0.0",
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

### 2.2 Project-level setup (.trae/mcp.json)

Trae can load MCP servers from `.trae/mcp.json` in the workspace root. Enable "Project-level MCP" in `Settings → MCP`, then commit the file:

```json
{
  "mcpServers": {
    "agy-mcp-server": {
      "command": "uvx",
      "args": [
        "--from",
        "${workspaceFolder}/antigravity-cli-mcp",
        "fastmcp",
        "run",
        "antigravity-cli-mcp/src/agy_mcp_server/server.py"
      ],
      "cwd": "${workspaceFolder}",
      "env": {
        "AGY_MCP_MODE": "safe",
        "AGY_MCP_ALLOWED_ROOTS": "[${workspaceFolder}]",
        "AGY_MCP_FORCE_SANDBOX_IN_SAFE_MODE": "true"
      }
    }
  }
}
```

Important:
- the official Trae docs recommend `uvx` (or `npx`) over `python -m fastmcp.cli`; `uvx` is the runner Trae looks for.
- `command` cannot contain spaces; use `args` for the rest of the invocation.
- `${workspaceFolder}` is expanded by Trae at startup and resolves to the current project root.
- `START_MCP_TIMEOUT_MS` / `RUN_MCP_TIMEOUT_MS` are read by Trae from the `env` block to cap server startup and tool calls.
- `AGY_MCP_ALLOWED_ROOTS` must be a JSON array string, not a comma-separated list.
- `.trae/mcp.json` is loaded from the project root, so make sure the path you commit is portable.

## 3) Troubleshooting

### `Unable to handle .../.venv`

This typically comes from Trae's Python environment discovery trying to load an invalid environment path.

Fixes:
- recreate the environment with `uv sync`
- ensure the `command` points to the correct interpreter under `.venv/bin/python`

### `No module named fastmcp.__main__`

Use `fastmcp.cli`:

```bash
python -m fastmcp.cli run src/agy_mcp_server/server.py --transport stdio
```

### `agy not found in PATH`

Ensure `agy` is installed and in your PATH:

```bash
which agy
```

If needed, set `AGY_MCP_AGY_PATH` to the full path of the `agy` binary.

### `workspace_path is outside allowed roots`

Add your workspace root to `AGY_MCP_ALLOWED_ROOTS`:

```json
"AGY_MCP_ALLOWED_ROOTS": "[\"/path/to/your/projects\", \"/another/path\"]"
```

## 4) Configuration Sources

Option A (recommended): set env vars in Trae's MCP server config (`env` field).

Option B: use a `.env` file:

```bash
cp .env.example .env
```

Precedence:
1. Trae MCP config `env`
2. `.env`
3. code defaults

### Persistence location: global vs workspace

The persistence directory can live in **two places**, controlled by `AGY_MCP_PERSISTENCE_LOCATION`:

| Mode | Resolved path (typical setup) | Use case |
|------|------------------------------|----------|
| `global` (default) | `~/.open-cli-router/agy/` | User-level, survives `cd`, not project-tied |
| `workspace` | `<cwd_parent>/.open-cli-router/agy/` | Project-level, portable, can be committed (use `.gitignore`!) |

`<cwd_parent>` is the parent of the server's CWD. For the config above where `cwd` is `/path/to/antigravity-cli-mcp`, `cwd_parent` is `/path/to` (the user's workspace root).

**Example — workspace mode in Trae's MCP config:**

```jsonc
{
  "mcpServers": {
    "agy-mcp-server": {
      "command": "uvx",
      "args": [...],
      "cwd": "/path/to/antigravity-cli-mcp",
      "env": {
        "AGY_MCP_PERSISTENCE_ENABLED": "true",
        "AGY_MCP_PERSISTENCE_LOCATION": "workspace"
        // Optional custom path (overrides LOCATION):
        // "AGY_MCP_PERSISTENCE_BASE_DIR": "$cwd_parent/.my-persistence"
      }
    }
  }
}
```

With the snippet above, persistence files live at `/path/to/.open-cli-router/agy/`. Remember to add `.open-cli-router/` to `.gitignore` if your workspace is a git repo.

**Escape hatch:** `AGY_MCP_PERSISTENCE_BASE_DIR="$cwd_parent/custom"` accepts any custom subdirectory under the workspace root, regardless of `LOCATION`.

## 5) Example Calls

### 5.1 Health check

```json
{ "expected_version": "1.0.3" }
```

### 5.2 Synchronous run

```json
{
  "workspace_path": "/path/to/project",
  "prompt": "Add docstrings to all functions in main.py",
  "capture_changes": true,
  "change_scope": "workspace",
  "options": {
    "sandbox": true,
    "dangerously_skip_permissions": false,
    "timeout_s": 300,
    "env": null,
    "extra_args": []
  }
}
```

### 5.3 Asynchronous run (start → poll)

Start:

```json
{
  "workspace_path": "/path/to/project",
  "prompt": "Refactor the project to use type hints",
  "capture_changes": true,
  "change_scope": "workspace",
  "options": {
    "sandbox": true,
    "timeout_s": 600
  }
}
```

Poll:

```json
{ "run_id": "run-<uuid>" }
```

Cancel:

```json
{ "run_id": "run-<uuid>", "force": false }
```

### 5.4 List recent runs

```json
{ "limit": 10 }
```

## Notes

### Model selection

This MCP server does not control model selection. Configure it inside `agy` using `/model`:

```bash
agy -i
```

Then:

```
> /model
> /model  Set a model
  ↑/↓ Navigate  · enter Select  · tab Complete
esc to cancel
```

The active model is marked as `(current)` and persists under `~/.gemini/antigravity/`.
