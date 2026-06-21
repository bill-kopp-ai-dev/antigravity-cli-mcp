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

### agy_quota

Check Antigravity CLI model quota status.

The Antigravity CLI does NOT expose a direct quota inspection endpoint. This
tool implements a hybrid strategy combining four sources:

- **A) Local counter (always-on)**: Tracks every agy_run_task / agy_start_task
  invocation per active model in a sliding 5-hour window (the documented
  quota refresh cadence). Reports `used`, `limit` (from settings),
  `remaining`, and `reset_at`. Works even when `agy` itself is broken.
- **B) Failure classifier (always-on)**: Inspects every failed agy run for
  quota-related keywords (`resource_exhausted`, `quota exceeded`, `429`).
  Notes last failure classification in each status entry.
- **C) Probe call (opt-in via `probe=True`)**: Runs a minimal `agy` task to
  verify the CLI is responsive. WARNING: this call itself consumes quota.
- **D) External API (opt-in via `use_api=True`)**: Stub: queries the Gemini
  API quota endpoint if implemented. Currently returns None with a logged
  warning unless `AGY_MCP_QUOTA_API_KEY` is set.

Input: `AgyQuotaRequest`
- `model: str | None` — if provided, return only that model's status.
- `tier: "free" | "pro" | "ultra" | "enterprise" | "unknown"` — subscription
  tier for limit lookup.
- `probe: bool` — opt-in flag for the C strategy above.
- `use_api: bool` — opt-in flag for the D strategy above.

Output: `AgyQuotaResponse`
- `statuses: list[AgyQuotaStatus]` — per-model status entries.
- `overall_healthy: bool` — True if all statuses are healthy.
- `active_model: str | None` — the configured active model
  (from `AGY_MCP_QUOTA_ACTIVE_MODEL`).
- `notes: list[str]` — top-level notes (warnings, opt-in acknowledgements).

Per-model `AgyQuotaStatus` fields:
- `model`, `tier`
- `used`, `limit`, `remaining` (counts in current 5h window)
- `reset_at: datetime | None`
- `period_hours: float`
- `healthy: bool`
- `source: "local_counter" | "api_call" | "probe" | "error_parser" | "combined"`
- `notes: list[str]`

Settings (env vars, all `AGY_MCP_QUOTA_*`):
- `AGY_MCP_QUOTA_ACTIVE_MODEL` (default `"unknown"`)
- `AGY_MCP_QUOTA_TIER` (default `"unknown"`)
- `AGY_MCP_QUOTA_PERIOD_HOURS` (default `5.0`)
- `AGY_MCP_QUOTA_TIER_LIMITS` (default `{"free": 30, "pro": 200, "ultra": 1000, "enterprise": 5000}`)
- `AGY_MCP_QUOTA_PROBE_TIMEOUT_S` (default `30`)
- `AGY_MCP_QUOTA_API_BASE_URL` (default `https://generativelanguage.googleapis.com`)
- `AGY_MCP_QUOTA_API_KEY` (no default; required for `use_api=True`)

### agy_init_persistence

Initialize the persistent memory layer.

Creates `~/.open-cli-router/agy/` (or `AGY_MCP_PERSISTENCE_BASE_DIR/agy/`)
with three editable Markdown files: `AGENTS.md`, `PROJECTS.md`, `MEMORY.md`.
Idempotent — re-running without `force=true` is a no-op.

Input: `AgyInitPersistenceRequest`
- `force: bool`
- `seed_templates: bool | None`

Output: `AgyInitPersistenceResponse`
- `base_dir: str`
- `created: list[str]`
- `already_existed: list[str]`
- `seed_version: str`

### agy_read_persistence

Read one of the three persistence files.

Input: `AgyReadPersistenceRequest`
- `file: "agents" | "projects" | "memory"`
- `offset: int` (default 0)
- `limit: int | None` (default: whole file)

Output: `AgyReadPersistenceResponse`
- `file: str`, `content: str`, `size_bytes: int`
- `truncated: bool`, `modified_at: datetime | None`

### agy_append_persistence

Append content to a persistence file (typical use: append to `MEMORY.md`
after each session).

Input: `AgyAppendPersistenceRequest`
- `file: "agents" | "projects" | "memory"`
- `content: str`
- `section_header: str | None` — inserts `## <header>` before the content
  if the heading is not already present.

Output: `AgyAppendPersistenceResponse`
- `file: str`, `appended_bytes: int`
- `new_size_bytes: int`, `timestamp: datetime`

### agy_update_persistence

Replace a section in a persistence file by heading anchor.

Input: `AgyUpdatePersistenceRequest`
- `file: "agents" | "projects" | "memory"`
- `section_anchor: str` — heading text without the `## ` prefix
- `new_content: str`
- `mode: "replace" | "append"` (default `"replace"`)

Output: `AgyUpdatePersistenceResponse`
- `file: str`, `section_anchor: str`
- `matched: bool`, `new_size_bytes: int`

### agy_load_persistence_context

Load the persistence files as truncated excerpts for a session.

Input: `AgyLoadPersistenceContextRequest`
- `include: list["agents" | "projects" | "memory"]` (default all three)
- `max_chars_per_file: int` (default 20_000)

Output: `AgyLoadPersistenceContextResponse`
- `agents_excerpt / projects_excerpt / memory_excerpt: str | None`
- `truncated_flags: dict[str, bool]`
- `total_chars: int`, `base_dir: str`, `initialized: bool`

Persistence settings (env vars, all `AGY_MCP_PERSISTENCE_*`):
- `AGY_MCP_PERSISTENCE_ENABLED` (default `true`)
- `AGY_MCP_PERSISTENCE_BASE_DIR` (default `~/.open-cli-router`)
- `AGY_MCP_PERSISTENCE_MAX_FILE_BYTES` (default `524288`)
- `AGY_MCP_PERSISTENCE_BACKUP_ON_WRITE` (default `false`)
- `AGY_MCP_PERSISTENCE_SEED_TEMPLATES` (default `true`)

## Prompts

The server also exposes reusable prompts intended to guide orchestration:

- `prompt_sync_orchestration`: guidance for `agy_run_task`
- `prompt_async_orchestration`: guidance for `agy_start_task` → `agy_poll_task` → `agy_cancel_task`
- `prompt_model_selection_guidance`: explains model selection and its limitations
- `prompt_security_and_workspace_rules`: summarizes security and workspace rules
- `agy_persistence_protocol`: instructs the orchestrator on how to maintain
  the persistent memory layer (`AGENTS.md`, `PROJECTS.md`, `MEMORY.md`).

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
