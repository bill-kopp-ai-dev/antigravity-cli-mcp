"""Tests for the self-test tool and req-optional behavior."""

from agy_mcp_server.server import agy_self_test
from agy_mcp_server.models import (
    AgySelfTestRequest,
    AgyPollTaskResponse,
    AgyQuotaStatus,
    AgyRunTaskResponse,
    AgyStartTaskResponse,
)


def test_self_test_returns_expected_shape():
    """agy_self_test returns a valid report with the expected fields."""
    result = agy_self_test()
    assert result.total_tools >= 13  # at least the 13 we registered
    assert isinstance(result.tolerant_count, int)
    assert isinstance(result.requires_req_count, int)
    assert len(result.tools) == result.total_tools
    # After the refactor, ALL tools should be tolerant to args={}
    assert result.requires_req_count == 0, (
        f"Some tools still require `req` wrapper: "
        f"{[r.name for r in result.tools if r.requires_req_wrapper]}"
    )
    assert result.tolerant_count == result.total_tools


def test_self_test_with_filter():
    """agy_self_test supports include filter."""
    result = agy_self_test(req=AgySelfTestRequest(include=["agy_health"]))
    assert all(r.name.startswith("agy_health") for r in result.tools)


class TestSprintN1Contract:
    """Lock the §S4 public contract advertised in CONTRATO_TOOLS.md."""

    def test_run_task_response_has_quota_fields(self) -> None:
        fields = set(AgyRunTaskResponse.model_fields.keys())
        assert "quota_warning" in fields
        assert "quota_remaining_pct" in fields

    def test_start_task_response_has_quota_fields(self) -> None:
        fields = set(AgyStartTaskResponse.model_fields.keys())
        assert "quota_warning" in fields
        assert "quota_remaining_pct" in fields

    def test_poll_task_response_has_quota_fields(self) -> None:
        fields = set(AgyPollTaskResponse.model_fields.keys())
        assert "quota_warning" in fields
        assert "quota_remaining_pct" in fields

    def test_quota_status_exposes_window_resets_in_seconds(self) -> None:
        fields = set(AgyQuotaStatus.model_fields.keys())
        assert "window_resets_in_seconds" in fields, (
            "window_resets_in_seconds must be exposed on AgyQuotaStatus "
            "(see CONTRATO_TOOLS §agy_quota, Sprint N+1 DoD item)."
        )

    def test_self_test_summary_advertises_full_registry(self) -> None:
        r = agy_self_test()
        # Loose substring: be tolerant to wording changes but require the
        # numbers to be present somewhere in the summary.
        summary = r.summary.lower()
        assert "14 tools" in summary or "total: 14" in summary
        assert "tolerant" in summary
        assert "req" in summary  # mentions `requires req wrapper`
