"""Regression tests for the quota_warning + quota_remaining_pct fields
added to the run/start/poll task response models (Sprint 2, commit 2).

These tests are contract-level: they verify that the Pydantic models expose
the new fields, default to safe values ('ok' / 100.0), and accept the values
that agy_run_task / agy_start_task / agy_poll_task wire into them.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agy_mcp_server.models import (
    AgyPollTaskResponse,
    AgyRunTaskResponse,
    AgyRunResult,
    AgyStartTaskResponse,
)


def _make_run_result() -> AgyRunResult:
    return AgyRunResult(
        run_id="run-test-123",
        workspace_path="/tmp/test",
        started_at=datetime.now(tz=timezone.utc),
        finished_at=datetime.now(tz=timezone.utc),
        stdout="ok",
        stderr="",
        exit_code=0,
        timed_out=False,
        duration_ms=10,
        total_cost_usd=0.0,
        model_usage={},
        permission_denials=[],
    )


class TestQuotaResponseFields:
    def test_agy_run_task_response_has_quota_fields(self) -> None:
        fields = AgyRunTaskResponse.model_fields
        assert "quota_warning" in fields
        assert "quota_remaining_pct" in fields

    def test_agy_start_task_response_has_quota_fields(self) -> None:
        fields = AgyStartTaskResponse.model_fields
        assert "quota_warning" in fields
        assert "quota_remaining_pct" in fields

    def test_agy_poll_task_response_has_quota_fields(self) -> None:
        fields = AgyPollTaskResponse.model_fields
        assert "quota_warning" in fields
        assert "quota_remaining_pct" in fields

    def test_quota_warning_default_is_ok(self) -> None:
        rr = AgyRunTaskResponse(result=_make_run_result())
        assert rr.quota_warning == "ok"
        assert rr.quota_remaining_pct == 100.0
        sr = AgyStartTaskResponse(run_id="run-abc", started_at=datetime.now(tz=timezone.utc))
        assert sr.quota_warning == "ok"
        assert sr.quota_remaining_pct == 100.0
        pr = AgyPollTaskResponse(status="running")
        assert pr.quota_warning == "ok"
        assert pr.quota_remaining_pct == 100.0

    def test_quota_warning_accepts_low(self) -> None:
        rr = AgyRunTaskResponse(
            result=_make_run_result(),
            quota_warning="low",
            quota_remaining_pct=15.0,
        )
        assert rr.quota_warning == "low"
        assert rr.quota_remaining_pct == 15.0

    def test_quota_warning_accepts_exhausted(self) -> None:
        rr = AgyRunTaskResponse(
            result=_make_run_result(),
            quota_warning="exhausted",
            quota_remaining_pct=0.0,
        )
        assert rr.quota_warning == "exhausted"
        assert rr.quota_remaining_pct == 0.0

    def test_quota_warning_rejects_invalid_literal(self) -> None:
        # Literal["ok","low","exhausted"] must reject other strings.
        import pytest
        with pytest.raises(Exception):
            AgyRunTaskResponse(
                result=_make_run_result(),
                quota_warning="full",  # type: ignore[arg-type]
            )
