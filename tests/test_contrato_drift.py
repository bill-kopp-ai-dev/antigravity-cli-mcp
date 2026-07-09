"""Sprint 4: keep CONTRATO_TOOLS.md and agy_self_test in sync.

Acts as a doc+code contract drift guard. Fails if any tool the server
registers is not documented in CONTRATO_TOOLS.md, or vice-versa. Also
verifies the documented contract surface matches the actual Pydantic
output models for the quota-aware additions from Sprint N+1.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRATO_PATH = REPO_ROOT / "CONTRATO_TOOLS.md"


def _extract_documented_tools(markdown: str) -> set[str]:
    """Pull every `### <tool_name>` heading that looks like a tool."""
    return {
        m.group(1)
        for m in re.finditer(r"^### (agy_[a-z_]+)\s*$", markdown, flags=re.MULTILINE)
    }


class TestContratoDrift:
    def test_contrato_file_exists(self) -> None:
        assert CONTRATO_PATH.exists(), f"missing {CONTRATO_PATH}"

    def test_documented_tools_match_registered(self) -> None:
        from agy_mcp_server.server import agy_self_test
        r = agy_self_test()
        registered = {t.name for t in r.tools}
        documented = _extract_documented_tools(CONTRATO_PATH.read_text())
        # Server has 1 hidden admin-only tool (clear_cache) registered; contract
        # documents the same set after Sprint 4 closes the gap.
        missing_from_doc = registered - documented
        missing_from_server = documented - registered
        assert not missing_from_doc, (
            f"registered tools not documented: {sorted(missing_from_doc)}. "
            f"Add them to CONTRATO_TOOLS.md §Tools."
        )
        assert not missing_from_server, (
            f"documented tools not registered: {sorted(missing_from_server)}. "
            f"Either drop them or implement them in src/agy_mcp_server/server.py."
        )

    def test_run_task_response_has_quota_fields(self) -> None:
        from agy_mcp_server.models import AgyRunTaskResponse
        fields = set(AgyRunTaskResponse.model_fields.keys())
        assert "quota_warning" in fields, "quota_warning missing on AgyRunTaskResponse"
        assert "quota_remaining_pct" in fields, "quota_remaining_pct missing on AgyRunTaskResponse"

    def test_start_task_response_has_quota_fields(self) -> None:
        from agy_mcp_server.models import AgyStartTaskResponse
        fields = set(AgyStartTaskResponse.model_fields.keys())
        assert "quota_warning" in fields
        assert "quota_remaining_pct" in fields

    def test_poll_task_response_has_quota_fields(self) -> None:
        from agy_mcp_server.models import AgyPollTaskResponse
        fields = set(AgyPollTaskResponse.model_fields.keys())
        assert "quota_warning" in fields
        assert "quota_remaining_pct" in fields

    def test_quota_settings_documented(self) -> None:
        text = CONTRATO_PATH.read_text()
        for env_var in (
            "AGY_MCP_QUOTA_POLICY_ENABLED",
            "AGY_MCP_ALLOW_OVERAGE",
            "AGY_MCP_QUOTA_LOW_THRESHOLD_PCT",
        ):
            assert env_var in text, f"{env_var} not documented in CONTRATO_TOOLS.md"

    def test_quota_exhausted_error_documented(self) -> None:
        text = CONTRATO_PATH.read_text()
        assert "QuotaExhaustedError" in text, (
            "QuotaExhaustedError exception contract must be documented."
        )

    def test_self_test_tool_documented(self) -> None:
        text = CONTRATO_PATH.read_text()
        assert "### agy_self_test" in text, "agy_self_test contract section missing"

    def test_self_test_response_shape_documented(self) -> None:
        text = CONTRATO_PATH.read_text()
        for required_field in (
            "total_tools",
            "tolerant_count",
            "requires_req_count",
            "tools",
            "server_info",
            "summary",
            "AgyToolSchemaReport",
        ):
            assert required_field in text, (
                f"{required_field} not documented in agy_self_test § of CONTRATO_TOOLS.md"
            )

    def test_run_task_quota_fields_documented(self) -> None:
        text = CONTRATO_PATH.read_text()
        # Locate the agy_run_task section and check it mentions quota fields.
        run_section_match = re.search(
            r"### agy_run_task\s*\n(.*?)(?=^### |\Z)", text, flags=re.MULTILINE | re.DOTALL
        )
        assert run_section_match, "agy_run_task section missing from CONTRATO_TOOLS.md"
        body = run_section_match.group(1)
        assert "quota_warning" in body
        assert "quota_remaining_pct" in body

    def test_clear_cache_tool_documented(self) -> None:
        text = CONTRATO_PATH.read_text()
        assert "### agy_clear_cache" in text, "agy_clear_cache contract section missing"

    def test_known_prompts_documented(self) -> None:
        text = CONTRATO_PATH.read_text()
        for prompt in ("agy_quickstart", "agy_troubleshoot"):
            assert prompt in text, f"{prompt} prompt not listed under CONTRATO_TOOLS §Prompts"

    def test_run_task_response_shape_documented(self) -> None:
        text = CONTRATO_PATH.read_text()
        run_section_match = re.search(
            r"### agy_run_task\s*\n(.*?)(?=^### |\Z)", text, flags=re.MULTILINE | re.DOTALL
        )
        assert run_section_match
        body = run_section_match.group(1)
        for required_field in (
            "workspace_path",
            "prompt",
            "options",
            "capture_changes",
            "change_scope",
        ):
            assert required_field in body, f"agy_run_task input missing {required_field}"
        for required_field in ("result", "changes"):
            assert required_field in body, f"agy_run_task output missing {required_field}"

    def test_quota_status_has_window_resets_in_seconds(self) -> None:
        from agy_mcp_server.models import AgyQuotaStatus
        fields = set(AgyQuotaStatus.model_fields.keys())
        assert "window_resets_in_seconds" in fields
