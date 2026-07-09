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


class TestGatePrecedesSubprocess:
    """Regression tests for the sprint-N+1 review fix.

    The original implementation ran the quota gate AFTER
    `_run_agy(...)`, which meant a "blocking" policy still consumed one
    real agy quota slot before raising. Fix: gate must run BEFORE any
    subprocess spawn (or Popen for the async path).
    """

    def test_run_task_gate_runs_before_run_agy(self, monkeypatch, tmp_path):
        """agy_run_task must raise QuotaExhaustedError before calling _run_agy."""
        import importlib
        from agy_mcp_server import server as server_mod

        # Reload server with policy enabled and overage disabled.
        monkeypatch.setenv("AGY_MCP_MODE", "safe")
        monkeypatch.setenv("AGY_MCP_ALLOWED_ROOTS", f'["{tmp_path}"]')
        monkeypatch.setenv("AGY_MCP_PERSISTENCE_ENABLED", "false")
        monkeypatch.setenv("AGY_MCP_QUOTA_POLICY_ENABLED", "true")
        monkeypatch.setenv("AGY_MCP_ALLOW_OVERAGE", "false")
        monkeypatch.setenv("AGY_MCP_QUOTA_ACTIVE_MODEL", "gemini-2.5-pro")
        importlib.reload(server_mod)

        from agy_mcp_server.models import AgyExecOptions, AgyRunTaskRequest
        from agy_mcp_server.server import QuotaExhaustedError

        # Saturate the active model bucket.
        tracker = server_mod._quota_tracker
        for _ in range(1000):
            tracker.record_call("gemini-2.5-pro")

        # If _run_agy is called, the test fails. Use a stub that raises.
        def boom(*args, **kwargs):
            raise AssertionError("_run_agy called despite exhausted quota")

        monkeypatch.setattr(server_mod, "_run_agy", boom)

        req = AgyRunTaskRequest(
            workspace_path=str(tmp_path),
            prompt="OK",
            options=AgyExecOptions(timeout_s=10),
            capture_changes=False,
        )
        with pytest.raises(QuotaExhaustedError) as exc_info:
            server_mod.agy_run_task(req=req)
        assert exc_info.value.model == "gemini-2.5-pro"
        assert exc_info.value.used == 1000
        assert exc_info.value.limit == 1000

    def test_start_task_gate_runs_before_popen(self, monkeypatch, tmp_path):
        """agy_start_task must raise QuotaExhaustedError before calling Popen."""
        import importlib
        from agy_mcp_server import server as server_mod

        monkeypatch.setenv("AGY_MCP_MODE", "safe")
        monkeypatch.setenv("AGY_MCP_ALLOWED_ROOTS", f'["{tmp_path}"]')
        monkeypatch.setenv("AGY_MCP_PERSISTENCE_ENABLED", "false")
        monkeypatch.setenv("AGY_MCP_QUOTA_POLICY_ENABLED", "true")
        monkeypatch.setenv("AGY_MCP_ALLOW_OVERAGE", "false")
        monkeypatch.setenv("AGY_MCP_QUOTA_ACTIVE_MODEL", "gemini-2.5-pro")
        importlib.reload(server_mod)

        from agy_mcp_server.models import AgyExecOptions, AgyStartTaskRequest
        from agy_mcp_server.server import QuotaExhaustedError

        tracker = server_mod._quota_tracker
        for _ in range(1000):
            tracker.record_call("gemini-2.5-pro")

        # Stub _build_agy_popen to raise — proves it's never reached.
        def boom(*args, **kwargs):
            raise AssertionError("_build_agy_popen called despite exhausted quota")

        monkeypatch.setattr(server_mod, "_build_agy_popen", boom)

        req = AgyStartTaskRequest(
            workspace_path=str(tmp_path),
            prompt="OK",
            options=AgyExecOptions(timeout_s=10),
            capture_changes=False,
        )
        with pytest.raises(QuotaExhaustedError):
            server_mod.agy_start_task(req=req)

    def test_run_task_allow_overage_bypasses_gate(self, monkeypatch, tmp_path):
        """When allow_overage=True, exhausted quota does NOT block."""
        import importlib
        from agy_mcp_server import server as server_mod

        monkeypatch.setenv("AGY_MCP_MODE", "safe")
        monkeypatch.setenv("AGY_MCP_ALLOWED_ROOTS", f'["{tmp_path}"]')
        monkeypatch.setenv("AGY_MCP_PERSISTENCE_ENABLED", "false")
        monkeypatch.setenv("AGY_MCP_QUOTA_POLICY_ENABLED", "true")
        monkeypatch.setenv("AGY_MCP_ALLOW_OVERAGE", "true")
        monkeypatch.setenv("AGY_MCP_QUOTA_ACTIVE_MODEL", "gemini-2.5-pro")
        importlib.reload(server_mod)

        from agy_mcp_server.models import AgyExecOptions, AgyRunTaskRequest

        tracker = server_mod._quota_tracker
        for _ in range(1000):
            tracker.record_call("gemini-2.5-pro")

        # Stub _run_agy — overage should let us through.
        def fake_run(workspace, req):
            return ("", "", 0, False)

        monkeypatch.setattr(server_mod, "_run_agy", fake_run)

        req = AgyRunTaskRequest(
            workspace_path=str(tmp_path),
            prompt="OK",
            options=AgyExecOptions(timeout_s=10),
            capture_changes=False,
        )
        # Must not raise.
        resp = server_mod.agy_run_task(req=req)
        assert resp.result.exit_code == 0