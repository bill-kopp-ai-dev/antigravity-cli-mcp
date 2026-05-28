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

Add a local MCP server in Trae with these settings:

```json
{
  "mcpServers": {
    "agy-mcp-server": {
      "command": "/path/to/antigravity-cli-mcp/.venv/bin/python",
      "args": [
        "-m",
        "fastmcp.cli",
        "run",
        "src/agy_mcp_server/server.py",
        "--transport",
        "stdio"
      ],
      "cwd": "/path/to/antigravity-cli-mcp",
      "env": {
        "AGY_MCP_MODE": "safe",
        "AGY_MCP_ALLOWED_ROOTS": "[\"/path/to/your/projects\"]",
        "AGY_MCP_FORCE_SANDBOX_IN_SAFE_MODE": "true"
      }
    }
  }
}
```

Important:
- use `-m fastmcp.cli` (not `python -m fastmcp`)
- set `cwd` to the project directory
- `AGY_MCP_ALLOWED_ROOTS` must be a JSON array string

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
