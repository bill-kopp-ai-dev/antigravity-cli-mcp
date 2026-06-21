# MCP Contract (Tools & Prompts)

This document describes the public contract for tools and prompts exposed by this MCP server.

## Tools

### agy_health

Input: `AgyHealthRequest`
- `expected_version: str | None`

Output: `AgyHealthResponse`
- `agy_path: str`
- `agy_version: str`
- `ok: bool`
- `notes: list[str]`

### agy_run_task

Input: `AgyRunTaskRequest`
- `workspace_path: str`
- `prompt: str`
- `options: AgyExecOptions`
- `capture_changes: bool`
- `change_scope: "workspace" | "git_only"`

Output: `AgyRunTaskResponse`
- `result: AgyRunResult`
- `changes: WorkspaceChanges | None`

### agy_start_task

Input: `AgyStartTaskRequest` (same as `AgyRunTaskRequest`)

Output: `AgyStartTaskResponse`
- `run_id: str`
- `started_at: datetime`

### agy_poll_task

Input: `AgyPollTaskRequest`
- `run_id: str`

Output: `AgyPollTaskResponse`
- `status: "running" | "done" | "failed" | "timed_out"`
- `result: AgyRunResult | None`
- `partial_stdout: str`
- `partial_stderr: str`
- `changes: WorkspaceChanges | None`

### agy_cancel_task

Input: `AgyCancelTaskRequest`
- `run_id: str`
- `force: bool`

Output: `AgyCancelTaskResponse`
- `canceled: bool`
- `status: "canceled" | "not_found" | "already_done"`

### agy_list_runs

Input: `AgyListRunsRequest`
- `limit: int`

Output: `AgyListRunsResponse`
- `runs: list[AgyRunSummary]`

## Prompts

The server also exposes reusable prompts intended to guide orchestration:

- `prompt_sync_orchestration`: guidance for `agy_run_task`
- `prompt_async_orchestration`: guidance for `agy_start_task` → `agy_poll_task` → `agy_cancel_task`
- `prompt_model_selection_guidance`: explains model selection and its limitations
- `prompt_security_and_workspace_rules`: summarizes security and workspace rules

## Model Selection (Important)

This MCP server does not control which model `agy` uses. `agy` model selection is configured interactively via `/model` and persists across sessions.

Example models shown in the TUI:

| Model | Notes |
|------|-------|
| `Gemini 3.5 Flash (Medium)` | default |
| `Gemini 3.5 Flash (High)` | higher quality |
| `Gemini 3.5 Flash (Low)` | lower cost |
| `Gemini 3.1 Pro (Low)` | lower cost |
| `Gemini 3.1 Pro (High)` | higher quality |
| `Gemini 3 Flash` | fast |
| `claude-sonnet-4.6-thinking` | extended thinking |
| `claude-opus-4.6-thinking` | extended thinking |
| `gpt-oss-120b` | open weights |

See [README.md](README.md) for a step-by-step guide.

## Security Configuration

### Safe mode (default)

- `sandbox` can be enforced
- `env` is rejected
- `extra_args` is rejected
- `dangerously_skip_permissions` is rejected

### Permissive mode

Still requires explicit allowlists:
- `AGY_MCP_ALLOW_ENV_KEYS`
- `AGY_MCP_ALLOW_EXTRA_ARGS`

### Validations

- blocks path traversal (`/../../etc`)
- blocks symlink escape
- blocks command injection via argument validation
- enforces `timeout_s` range (1–3600)
- rejects non-string keys/values in `env` and non-string entries in `extra_args`
- uses strict booleans to prevent string coercion issues
