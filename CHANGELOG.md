# Changelog

All notable changes to this project will be documented in this file.

## 0.2.0 (2026-07-08)

### Added

- `AGY_MCP_QUOTA_POLICY_ENABLED` setting (default `false`). When enabled with `AGY_MCP_ALLOW_OVERAGE=false`, calls are blocked with a structured `QuotaExhaustedError` once the per-window quota is exhausted.
- `AGY_MCP_ALLOW_OVERAGE` setting (default `false`).
- `AGY_MCP_QUOTA_LOW_THRESHOLD_PCT` setting (default `20.0`).
- `QuotaExhaustedError` exception carrying `model`, `used`, `limit`, `reset_in_seconds`.
- Tests: 6 new in `tests/test_quota_enforcement.py`.

### Notes

- Project version remains `0.1.0`; this is a backwards-compatible feature addition under the same version (deferring the `0.2.0` bump until quota `agy_self_test` doc lands in S4 to bundle the user-facing contract change). Feature flags default off — no behaviour change for existing users.

## 0.1.0 (2026-07-08)

### Changed

- Bumped `fastmcp` dependency from `>=3.0.0` to `>=3.4.3,<4.0.0` (SSRF hardening, OAuth nonce validation, permissive-mode API fixes from FastMCP 3.3.x → 3.4.x).
- Project version bumped from 0.0.1 to 0.1.0.

### Notes

- This release ships no behavioural changes to the agy MCP server itself. The bump is a hardening-and-parity release aligning with `claude-code-cli-mcp` 3.4.3 baseline.
- Sprint N+1 plan: see `PLAN_NEXT_SPRINT.md` (quota awareness follow-ups land in 0.2.0).
