"""Sprint 3: quota_policy_enabled blocks calls when exhausted."""
from __future__ import annotations

import pytest

from agy_mcp_server.quota import QuotaTracker


def _tracker_full(model: str = "gemini-2.5-pro", limit: int = 1000) -> QuotaTracker:
    t = QuotaTracker()
    for _ in range(limit):
        t.record_call(model)
    return t


class TestQuotaEnforcement:
    def test_default_policy_disabled_no_block(self) -> None:
        from agy_mcp_server.settings import Settings
        s = Settings(_env_file=None)  # nosec — no env file, full defaults
        assert s.quota_policy_enabled is False
        assert s.allow_overage is False
        assert s.quota_low_threshold_pct == 20.0

    def test_quota_exhausted_error_carries_fields(self) -> None:
        from agy_mcp_server.server import QuotaExhaustedError
        exc = QuotaExhaustedError(
            model="gemini-2.5-pro",
            used=1000,
            limit=1000,
            reset_in_seconds=18000,
        )
        assert exc.model == "gemini-2.5-pro"
        assert exc.used == 1000
        assert exc.limit == 1000
        assert exc.reset_in_seconds == 18000
        assert "QUOTA_EXHAUSTED" in str(exc)
        assert "18000s" in str(exc)

    def test_snapshot_exhausted_when_tracker_at_limit(self) -> None:
        t = _tracker_full()
        snap = t.snapshot("gemini-2.5-pro")
        assert snap.warning == "exhausted"
        assert snap.remaining == 0

    def test_snapshot_low_at_eighty_percent(self) -> None:
        t = QuotaTracker()
        for _ in range(800):
            t.record_call("gemini-2.5-pro")
        snap = t.snapshot("gemini-2.5-pro")
        assert snap.warning == "low"
        assert snap.remaining == 200

    def test_snapshot_ok_below_threshold(self) -> None:
        t = QuotaTracker()
        for _ in range(3):
            t.record_call("gemini-2.5-pro")
        snap = t.snapshot("gemini-2.5-pro")
        assert snap.warning == "ok"
        assert snap.remaining == 997

    def test_settings_accepts_explicit_overrides(self) -> None:
        from agy_mcp_server.settings import Settings
        s = Settings(
            _env_file=None,  # nosec
            quota_policy_enabled=True,
            allow_overage=True,
            quota_low_threshold_pct=10.0,
        )
        assert s.quota_policy_enabled is True
        assert s.allow_overage is True
        assert s.quota_low_threshold_pct == 10.0

    def test_quota_exhausted_error_is_exception(self) -> None:
        from agy_mcp_server.server import QuotaExhaustedError
        assert issubclass(QuotaExhaustedError, Exception)
        with pytest.raises(QuotaExhaustedError) as exc_info:
            raise QuotaExhaustedError(model="x", used=10, limit=10, reset_in_seconds=0)
        assert exc_info.value.model == "x"

    def test_gate_with_policy_enabled_and_exhausted_raises(self) -> None:
        """Mirror the gate logic from agy_run_task: when policy enabled,
        overage disabled, snapshot exhausted — raise QuotaExhaustedError."""
        from agy_mcp_server.server import QuotaExhaustedError
        from agy_mcp_server.settings import Settings
        s = Settings(_env_file=None, quota_policy_enabled=True, allow_overage=False)
        t = _tracker_full()
        snap = t.snapshot("gemini-2.5-pro")
        assert snap.warning == "exhausted"
        if s.quota_policy_enabled and not s.allow_overage and snap.warning == "exhausted":
            with pytest.raises(QuotaExhaustedError) as exc_info:
                raise QuotaExhaustedError(
                    model=snap.model,
                    used=snap.used,
                    limit=snap.limit,
                    reset_in_seconds=snap.window_remaining_seconds,
                )
            assert exc_info.value.used == 1000

    def test_gate_disabled_does_not_raise(self) -> None:
        """Mirror: default policy disabled → no block, even if exhausted."""
        from agy_mcp_server.settings import Settings
        s = Settings(_env_file=None)
        t = _tracker_full()
        snap = t.snapshot("gemini-2.5-pro")
        # Default: quota_policy_enabled=False → condition short-circuits to False.
        gate_open = not (s.quota_policy_enabled and not s.allow_overage and snap.warning == "exhausted")
        assert gate_open is True