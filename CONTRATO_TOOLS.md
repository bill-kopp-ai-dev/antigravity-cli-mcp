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

### agy_clear_cache

Clear the uv package cache to resolve stale-package import errors.

When the MCP server fails to start with errors like:
`ImportError: cannot import name 'Xxx' from 'agy_mcp_server'`
and restarting / clearing uvx cache manually does not resolve it, this
tool runs `uv cache clean` to remove all cached package archives.

**Behaviour:**
- By default (`full=false`), clears only the uv cache directory
  (`~/.cache/uv`). This is the safest option and resolves most
  stale-cache issues without affecting other projects.
- With `full=true`, clears the entire uv cache — useful when the
  server is launched via `--from <path>` and the archive hash keeps
  being reused across sessions.

**Warning:** clearing the cache causes subsequent `uvx` invocations to
re-download and re-install dependencies (slower first startup after
clean).

Input: `AgyClearCacheRequest`
- `full: bool`

Output: `AgyClearCacheResponse`
- `cleared: bool`
- `entries_removed: int` — estimate
- `cache_dir: str`
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
- `quota_warning: "ok" | "low" | "exhausted"` — added Sprint N+1 S2
- `quota_remaining_pct: float` — 0.0–100.0, percent of remaining quota for the active model in the current 5h window. Added Sprint N+1 S2.

### agy_start_task

Input: `AgyStartTaskRequest` (same as `AgyRunTaskRequest`)

Output: `AgyStartTaskResponse`
- `run_id: str`
- `started_at: datetime`
- `quota_warning: "ok" | "low" | "exhausted"` — added Sprint N+1 S2
- `quota_remaining_pct: float` — 0.0–100.0. Added Sprint N+1 S2.

### agy_poll_task

Input: `AgyPollTaskRequest`
- `run_id: str`

Output: `AgyPollTaskResponse`
- `status: "running" | "done" | "failed" | "timed_out"`
- `result: AgyRunResult | None`
- `partial_stdout: str`
- `partial_stderr: str`
- `changes: WorkspaceChanges | None`
- `quota_warning: "ok" | "low" | "exhausted"` — added Sprint N+1 S2
- `quota_remaining_pct: float` — 0.0–100.0. Added Sprint N+1 S2.

### agy_self_test

Metadata-only introspection of every registered tool's input schema.

Input: `AgySelfTestRequest`
- `include: list[str] | None` — filter tools by name prefix; `None` = all tools
- `only_show_tolerant: bool` — if True, only return tools that accept `args={}`

Output: `AgySelfTestResponse`
- `total_tools: int`
- `tolerant_count: int` — number of tools that accept `args={}` (empty required)
- `requires_req_count: int` — number of tools still requiring the legacy `req` wrapper
- `tools: list[AgyToolSchemaReport]` — per-tool schema breakdown
- `server_info: dict[str, Any]` — FastMCP version + registered tool counts
- `summary: str` — human-readable one-liner

Each `AgyToolSchemaReport` exposes:
- `name: str` — tool name (e.g. `"agy_run_task"`)
- `top_level_required: list[str]`
- `top_level_properties: list[str]`
- `accepts_empty_args: bool`
- `requires_req_wrapper: bool`

Cross-reference: parity tool exists in `claude-code-cli-mcp` as `claude_self_test`. The same `requires_req_count` metric drives the dual-call-safety contract on both MCP servers (legacy `args={}` callers must keep working for backwards compatibility).

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
- `window_resets_in_seconds: float | None` (None when `reset_at` is unknown; otherwise seconds until reset, ≥ 0)

Settings (env vars, all `AGY_MCP_QUOTA_*`):
- `AGY_MCP_QUOTA_ACTIVE_MODEL` (default `"unknown"`)
- `AGY_MCP_QUOTA_TIER` (default `"unknown"`)
- `AGY_MCP_QUOTA_PERIOD_HOURS` (default `5.0`)
- `AGY_MCP_QUOTA_TIER_LIMITS` (default `{"free": 30, "pro": 200, "ultra": 1000, "enterprise": 5000}`)
- `AGY_MCP_QUOTA_PROBE_TIMEOUT_S` (default `30`)
- `AGY_MCP_QUOTA_API_BASE_URL` (default `https://generativelanguage.googleapis.com`)
- `AGY_MCP_QUOTA_API_KEY` (no default; required for `use_api=True`)
- `AGY_MCP_QUOTA_POLICY_ENABLED` (default `false`) — added Sprint N+1 S3. When
  `true` and `AGY_MCP_ALLOW_OVERAGE=false`, calls are gated and a structured
  `QuotaExhaustedError` is raised once the per-window quota is exhausted.
- `AGY_MCP_ALLOW_OVERAGE` (default `false`) — added Sprint N+1 S3. When
  `true`, the gate in `agy_run_task` is bypassed (you accept the overage risk
  of incurring Gemini billing overages after the soft quota is exhausted).
- `AGY_MCP_QUOTA_LOW_THRESHOLD_PCT` (default `20.0`) — added Sprint N+1 S3.
  Fraction of the per-window quota below which `quota_warning` is reported as
  `"low"` (otherwise `"ok"`). At 0.0 remaining the warning flips to
  `"exhausted"`.

Runtime exception (`QuotaExhaustedError`, added Sprint N+1 S3):
- Carries `model`, `used`, `limit`, `reset_in_seconds` for client visibility.
- Raised only when the policy is enabled, overage is disallowed, and the
  per-window snapshot reports `warning="exhausted"`. Default settings keep
  the policy off; existing clients see no behaviour change.

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
  (matching is case-insensitive and strips leading `#` and whitespace)
- `new_content: str`
- `mode: "replace" | "append"` (default `"replace"`)
- `confirm: bool` (default `false`) — **required `true`** when updating
  `file="agents"` in safe mode (parity with `claude-code-cli-mcp`).
  Raises `ValueError("CONFIRM_REQUIRED: ...")` if missing.

Output: `AgyUpdatePersistenceResponse`
- `file: str`, `section_anchor: str`
- `matched: bool`, `new_size_bytes: int`

### agy_load_persistence_context

Load the persistence files as truncated excerpts for a session.

Truncation strategy (Phase 3, C4):
- Default is **asymmetric**: 20% head + 80% tail (configurable via
  `AGY_MCP_PERSISTENCE_TRUNCATION_HEAD_RATIO`). This favors recency
  over ancient history.
- The marker between head and tail includes the number of chars
  omitted: `[truncated N chars]`.

Input: `AgyLoadPersistenceContextRequest`
- `include: list["agents" | "projects" | "memory"]` (default all three)
- `max_chars_per_file: int` (default 20_000)

Output: `AgyLoadPersistenceContextResponse`
- `agents_excerpt / projects_excerpt / memory_excerpt: str | None`
- `truncated_flags: dict[str, bool]`
- `total_chars: int`, `base_dir: str`, `initialized: bool`

Persistence settings (env vars, all `AGY_MCP_PERSISTENCE_*`):
- `AGY_MCP_PERSISTENCE_ENABLED` (default `true`)
- `AGY_MCP_PERSISTENCE_LOCATION` (default `global`) — `"global"` or
  `"workspace"`. When `"workspace"`, files live in
  `<cwd_parent>/.open-cli-router/agy/` instead of `~/.open-cli-router/agy/`.
- `AGY_MCP_PERSISTENCE_BASE_DIR` (default `~/.open-cli-router`) — accepts
  the special token `$cwd_parent` (parent of the server's CWD) for
  custom paths, e.g. `$cwd_parent/.my-persistence`.
- `AGY_MCP_PERSISTENCE_MAX_FILE_BYTES` (default `524288` / 512 KiB)
- `AGY_MCP_PERSISTENCE_BACKUP_ON_WRITE` (default `false`)
- `AGY_MCP_PERSISTENCE_BACKUP_KEEP` (default `10`) — number of `.bak`
  files to retain per source file (Phase 3, C3).
- `AGY_MCP_PERSISTENCE_SEED_TEMPLATES` (default `true`)
- `AGY_MCP_PERSISTENCE_TRUNCATION_HEAD_RATIO` (default `0.2`) — fraction
  of `max_chars_per_file` preserved at the head (Phase 3, C4).

## Prompts

The server exposes reusable prompts intended to guide orchestration. All
prompts return pure markdown text -- no MCP tool is called as a side
effect. The two-name convention below reflects the @mcp.prompt decorator
alias used in the source.

### Sync orchestration

- `prompt_sync_orchestration(workspace_path, goal)` -- playbook for a
  single `agy_run_task`. Input: workspace_path (JSON example value)
  and goal (inlined as a single bullet). Output: markdown with goal,
  workspace echo, constraints (model-selection, workspace, safe defaults),
  a 4-step execution plan, and a JSON example.

### Async orchestration

- `prompt_async_orchestration(workspace_path, goal)` -- playbook for
  `agy_start_task` + `agy_poll_task` + `agy_cancel_task`. Input:
  workspace_path (JSON example value) and goal. Output: markdown with
  goal, constraints (model-selection, run lifecycle), a 5-step execution
  plan covering backoff polling and force escalation, plus JSON
  examples.

### Selection & safety

- `prompt_model_selection_guidance()` (zero-arg) -- explains why this
  MCP server does NOT control model selection and the 4-step `/model`
  CLI procedure.
- `prompt_security_and_workspace_rules()` (zero-arg) -- reminds the
  orchestrator of workspace_path constraints, AGY_MCP_ALLOWED_ROOTS
  gate, safe-mode restrictions, permissive-mode allowlists, and
  recommended safe defaults.

### Cheatsheets

- `agy_contract` -- full machine-readable JSON contract of every
  registered tool. Builds the catalog from mcp._local_provider._components
  so it stays in sync with the actual registered tool schemas.
- `agy_persistence_protocol` -- instructs the orchestrator on how to
  maintain the persistent memory layer (`AGENTS.md`, `PROJECTS.md`,
  `MEMORY.md`).
- `agy_quickstart` -- cheatsheet -- args shape, required CLI binary,
  common gotchas. Read this first if confused.
- `agy_troubleshoot` -- diagnose a specific error string and return the
  fix recipe. Pass the exact error message you received.

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
