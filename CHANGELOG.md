# Changelog

All notable changes to this project will be documented in this file.

## 0.1.0 (2026-07-08)

### Changed

- Bumped `fastmcp` dependency from `>=3.0.0` to `>=3.4.3,<4.0.0` (SSRF hardening, OAuth nonce validation, permissive-mode API fixes from FastMCP 3.3.x → 3.4.x).
- Project version bumped from 0.0.1 to 0.1.0.

### Notes

- This release ships no behavioural changes to the agy MCP server itself. The bump is a hardening-and-parity release aligning with `claude-code-cli-mcp` 3.4.3 baseline.
- Sprint N+1 plan: see `PLAN_NEXT_SPRINT.md` (quota awareness follow-ups land in 0.2.0).
