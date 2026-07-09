# Changelog

All notable changes to this project will be documented in this file.

## 0.2.0 (2026-07-08)

### Added

- `AGY_MCP_QUOTA_POLICY_ENABLED` setting (default `false`). When enabled with `AGY_MCP_ALLOW_OVERAGE=false`, calls are blocked with a structured `QuotaExhaustedError` once the per-window quota is exhausted.
- `AGY_MCP_ALLOW_OVERAGE` setting (default `false`).
- `AGY_MCP_QUOTA_LOW_THRESHOLD_PCT` setting (default `20.0`).
- `QuotaExhaustedError` exception carrying `model`, `used`, `limit`, `reset_in_seconds`.
- Tests: 8 new in `tests/test_quota_enforcement.py` (gate-mirror coverage of `agy_run_task` policy flow).
- Sprint N+1 S4 — `CONTRATO_TOOLS.md` extended with full user-facing
  contracts for all 14 tools (incl. `agy_self_test`, `agy_clear_cache`)
  plus the quota runtime contract (`QuotaExhaustedError`, settings registry).
- Sprint N+1 S4 — `tests/test_contrato_drift.py` (13 tests) keeps the
  Markdown contract and the FastMCP tool registry in sync.
- Sprint N+1 S5 — `agy_quota` response now exposes
  `window_resets_in_seconds` (per-status, `float | None`) so callers can
  schedule retries without re-querying.
- Sprint N+1 S5 — `src/agy_mcp_server/timeout_policy.py` adds the
  `compute_quota_safe_timeout(task_class, model_tier, recent_failure_rate)`
  helper returning `(timeout_s, must_use_async, warning, quota_warning)`.
  Exposed but not yet wired into `agy_run_task` / `agy_start_task` (YAGNI).
- Sprint N+1 S5 — `tools/agy_smoke.py` runs against the live runtime and
  prints `AGY_SMOKE_OK total=14 tolerant=14` if the contract holds.
- Sprint N+1 S5 — `tests/test_secret_drift_guard.py` (3 tests) walks
  `settings.py` AST for `*_SECRET_ID` fields and asserts each has
  regression coverage.
- Sprint N+1 S5 — `README.md` updated with version badges, FastMCP note,
  CHANGELOG/CONTRATO_TOOLS links, and a per-suite test breakdown.
- Sprint N+1 S5 — `tests/test_self_test.py` extended with 5
  `TestSprintN1Contract` cases that lock the §S4 public contract
  (`quota_warning` + `quota_remaining_pct` on 3 response models,
  `window_resets_in_seconds` on `AgyQuotaStatus`).

### Notes

- Project version remains `0.1.0`; this is a backwards-compatible feature addition under the same version. The `0.2.0` bump is reserved for the next window where the public contract changes for users (default settings change).
- Feature flags default `off` — no behaviour change for existing users.
- Ruff baseline improved from 23 → 16 errors (cleaned up 7 stale `F401`
  unused-imports in test files).

## 0.1.0 (2026-07-08)

### Changed

- Bumped `fastmcp` dependency from `>=3.0.0` to `>=3.4.3,<4.0.0` (SSRF hardening, OAuth nonce validation, permissive-mode API fixes from FastMCP 3.3.x → 3.4.x).
- Project version bumped from 0.0.1 to 0.1.0.

### Notes

- This release ships no behavioural changes to the agy MCP server itself. The bump is a hardening-and-parity release aligning with `claude-code-cli-mcp` 3.4.3 baseline.
- Sprint N+1 plan: see `PLAN_NEXT_SPRINT.md` (quota awareness follow-ups land in 0.2.0).
