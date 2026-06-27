# CLI-router MCP Servers — Quick Guide for Other Agents

Two sibling MCP servers in this Trae workspace:

| Server | CLI binary | Models | Tools |
|--------|-----------|--------|-------|
| `agy-mcp-server` | `agy` (Antigravity CLI) | Gemini | 14 + 8 prompts |
| `claude-code-cli-mcp` | `claude` (Claude Code CLI) | Anthropic (Claude) | 12 + 8 prompts |

## Calling any tool

```python
# ✓ correct — dict with `req` wrapper
run_mcp(server="agy-mcp-server", tool="agy_health",
        args={"req": {}})

# ✗ wrong — list wraps as {"item": ...} (breaks the server)
run_mcp(server="agy-mcp-server", tool="agy_health",
        args=[{"req": {}}])
```

The top-level key must always be `req`. After the recent refactor, all tools
also accept `args={}` (the `req` field is optional; Pydantic fills defaults).

## Discoverable prompts (use these!)

Each server exposes prompts via `prompts/list`. **Always call these first** if
you've never used this server, or if you're hitting a confusing error.

- `{provider}_quickstart` — args shape, tool catalog, common gotchas
- `{provider}_contract` — full JSON schema of every tool
- `{provider}_troubleshoot(error="...")` — diagnose a specific error message

Where `{provider}` is `agy` or `claude`.

Example:

```
run_mcp(server="agy-mcp-server", prompt="agy_quickstart")
run_mcp(server="claude-code-cli-mcp", prompt="claude_troubleshoot",
        args={"error": "Not logged in"})
```

## Health & schema probe (run first!)

```
agy_self_test(req={})     → {"total_tools": 14, "tolerant_count": 14, "requires_req_count": 0}
claude_self_test(req={})  → {"total_tools": 12, "tolerant_count": 12, "requires_req_count": 0}
```

If `tolerant_count < total_tools`, the server has a schema regression —
report it, don't retry the call.

## Workspace paths for `*_run_task`

`workspace_path` must be inside:

- `AGY_MCP_ALLOWED_ROOTS` for agy-mcp-server
- `CLAUDE_MCP_ALLOWED_ROOTS` for claude-code-cli-mcp

Both are JSON-array env vars on the server. Default = the server's own project
directory. To use a different path, the user must set the env var in the
Trae MCP server config and restart the server.

## Restart requirement

After any server-side change that adds new tools/prompts, the user must
restart the MCP server in the Trae panel so the registry re-discovers them.
Otherwise `run_mcp` will return "MCP tool is not found" (this is a Trae
client-side cache, not a server bug).

## Common error → fix table

| Error | Cause | Fix |
|-------|-------|-----|
| `req: Missing required argument` | args shape wrong | Use `args={"req": {...}}` (dict, not list) |
| `2 validation errors for AgyRunTaskRequest\nworkspace_path\n  Field required [type=missing, input_value={}]` | Wrapper bug — Trae/Claude Code dropped structured args to `{}` | See "Args Serialization Failure" below |
| `workspace_path is outside allowed roots` | path not in env var | Set `AGY_MCP_ALLOWED_ROOTS`/`CLAUDE_MCP_ALLOWED_ROOTS` env var |
| `MCP tool is not found` | Trae registry stale | Restart MCP server in Trae panel |
| `Not logged in · Please run /login` (claude only) | CLI print-mode auth | User runs `claude login` interactively once |
| `exit_code=1 status=error` (claude) | CLI auth or model error | Check `claude_health(req={})`; verify CLI is logged in |

For deeper diagnosis, call the `troubleshoot` prompt with the exact error.

## Args Serialization Failure (Trae/Claude Code wrapper bug)

**Symptom:** `agy_run_task` or `claude_run_task` returns
`Field required [type=missing, input_value={}]` even though you sent
`args={"req": {"workspace_path": "...", "prompt": "..."}}`.

**Cause:** the Trae/Claude Code MCP client serializes args as empty `{}`
when the tool has required fields. Health/args-free probes (`agy_health`,
`agy_self_test`) pass because they don't need structured args.

**Confirmed:** server code is fine. Verified end-to-end via direct JSON-RPC.

**Workaround — direct invocation script:**

`tools/mcp_direct.py` in this repo spawns a fresh server subprocess and
talks JSON-RPC directly, bypassing the wrapper.

```bash
# 20-30s startup per call (uvx slow). Use ≥60s timeout.
python3 tools/mcp_direct.py agy_run_task /tmp "echo hello"
python3 tools/mcp_direct.py claude_run_task /tmp "echo hello"
python3 tools/mcp_direct.py agy_health   # works for args-free tools too
```

**Long-term fix:** report the wrapper bug to Trae/Claude Code maintainers
(client-side JSON-RPC serialization is dropping `arguments` content).
