"""Tests for the quota module: classifier, tracker, and helpers."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from agy_mcp_server.models import DEFAULT_ACTIVE_MODEL, MODEL_QUOTA_REGISTRY
from agy_mcp_server.quota import (
    DEFAULT_PERIOD_HOURS,
    DEFAULT_TIER_LIMITS,
    FailureKind,
    KNOWN_MODELS,
    QuotaSnapshot,
    QuotaStatus,
    QuotaTracker,
    classify_agy_failure,
    get_default_tracker,
    reset_default_tracker,
)


# ----------------------------------------------------------------------
# classify_agy_failure
# ----------------------------------------------------------------------

class TestClassifyAgyFailure:
    def test_quota_exhausted_from_stderr_keyword(self):
        kind = classify_agy_failure(
            exit_code=1, stdout="", stderr="Error: resource_exhausted", timed_out=False
        )
        assert kind == FailureKind.QUOTA_EXHAUSTED

    def test_quota_exhausted_from_quota_exceeded(self):
        kind = classify_agy_failure(
            exit_code=1, stdout="", stderr="quota exceeded for free tier", timed_out=False
        )
        assert kind == FailureKind.QUOTA_EXHAUSTED

    def test_quota_exhausted_from_429(self):
        kind = classify_agy_failure(
            exit_code=429, stdout="", stderr="HTTP 429 too many requests", timed_out=False
        )
        assert kind == FailureKind.QUOTA_EXHAUSTED

    def test_quota_exhausted_from_rate_limit(self):
        kind = classify_agy_failure(
            exit_code=1, stdout="", stderr="rate limit reached", timed_out=False
        )
        assert kind == FailureKind.QUOTA_EXHAUSTED

    def test_auth_from_401(self):
        kind = classify_agy_failure(
            exit_code=401, stdout="", stderr="Unauthorized", timed_out=False
        )
        assert kind == FailureKind.AUTH

    def test_auth_from_403(self):
        kind = classify_agy_failure(
            exit_code=403, stdout="", stderr="Forbidden", timed_out=False
        )
        assert kind == FailureKind.AUTH

    def test_timeout(self):
        kind = classify_agy_failure(
            exit_code=None, stdout="", stderr="", timed_out=True
        )
        assert kind == FailureKind.TIMEOUT

    def test_suspected_quota_or_bug_pattern(self):
        """The current antigravity-cli-mcp bug pattern: exit 0 + empty stdout/stderr."""
        kind = classify_agy_failure(
            exit_code=0, stdout="", stderr="", timed_out=False
        )
        assert kind == FailureKind.SUSPECTED_QUOTA_OR_BUG

    def test_suspected_quota_or_bug_whitespace_only(self):
        """Empty after stripping whitespace counts as empty."""
        kind = classify_agy_failure(
            exit_code=0, stdout="   \n  ", stderr="\n", timed_out=False
        )
        assert kind == FailureKind.SUSPECTED_QUOTA_OR_BUG

    def test_other_error(self):
        kind = classify_agy_failure(
            exit_code=1, stdout="something went wrong", stderr="details", timed_out=False
        )
        assert kind == FailureKind.OTHER

    def test_successful_run_not_classified_as_failure(self):
        kind = classify_agy_failure(
            exit_code=0, stdout="OK", stderr="", timed_out=False
        )
        assert kind == FailureKind.OTHER  # no failure detected


# ----------------------------------------------------------------------
# QuotaTracker
# ----------------------------------------------------------------------

class TestQuotaTracker:
    def test_initial_status_is_healthy(self):
        t = QuotaTracker()
        s = t.status("claude-opus-4.6-thinking", tier="pro")
        assert isinstance(s, QuotaStatus)
        assert s.used == 0
        assert s.limit == DEFAULT_TIER_LIMITS["pro"]
        assert s.remaining == s.limit
        assert s.healthy is True
        assert s.reset_at is None

    def test_record_call_increments_used(self):
        t = QuotaTracker()
        for _ in range(5):
            t.record_call("gemini-3.1-pro-high")
        s = t.status("gemini-3.1-pro-high", tier="pro")
        assert s.used == 5
        assert s.remaining == DEFAULT_TIER_LIMITS["pro"] - 5
        assert s.healthy is True

    def test_status_exceeds_limit_marks_unhealthy(self):
        t = QuotaTracker(tier_limits={"free": 3})
        for _ in range(5):
            t.record_call("gemini-3-flash")
        s = t.status("gemini-3-flash", tier="free")
        assert s.used == 5
        assert s.remaining == 0
        assert s.healthy is False

    def test_eviction_outside_window(self):
        """Calls older than period_hours should not count."""
        t = QuotaTracker(period_hours=0.001)  # ~3.6 seconds
        now = time.time()
        # 5 old calls (10s ago), 2 recent calls (now)
        for i in range(5):
            t.record_call("model-a", ts=now - 10.0)
        for i in range(2):
            t.record_call("model-a", ts=now)
        s = t.status("model-a", tier="pro")
        assert s.used == 2  # only the 2 recent ones survive

    def test_per_model_isolation(self):
        t = QuotaTracker()
        for _ in range(3):
            t.record_call("model-a")
        for _ in range(2):
            t.record_call("model-b")
        assert t.status("model-a", tier="pro").used == 3
        assert t.status("model-b", tier="pro").used == 2
        assert t.status("model-c", tier="pro").used == 0

    def test_reset_at_computed_correctly(self):
        t = QuotaTracker(period_hours=1.0)
        base = time.time()
        t.record_call("model-a", ts=base)
        t.record_call("model-a", ts=base + 60)
        s = t.status("model-a", tier="pro")
        assert s.reset_at is not None
        # Reset at first_call + period = base + 3600
        expected_reset = datetime.fromtimestamp(base + 3600, tz=timezone.utc)
        # Allow 1 second of slack for execution time.
        assert abs((s.reset_at - expected_reset).total_seconds()) < 1.0

    def test_record_failure_sets_last_failure(self):
        t = QuotaTracker()
        t.record_failure(FailureKind.QUOTA_EXHAUSTED)
        assert t.last_failure_kind == FailureKind.QUOTA_EXHAUSTED
        assert t.last_failure_at is not None

    def test_total_used_in_window(self):
        t = QuotaTracker()
        for _ in range(3):
            t.record_call("model-a")
        for _ in range(2):
            t.record_call("model-b")
        assert t.total_used_in_window() == 5

    def test_unknown_tier_uses_large_default(self):
        t = QuotaTracker()
        s = t.status("any-model", tier="unknown")
        assert s.limit == 999_999
        assert s.healthy is True

    def test_default_period_hours(self):
        assert DEFAULT_PERIOD_HOURS == 5.0

    def test_window_resets_in_seconds_computed_correctly(self):
        t = QuotaTracker(period_hours=1.0)
        s = t.status("model-a", tier="pro")
        assert s.window_resets_in_seconds is None

        base = time.time()
        t.record_call("model-a", ts=base)
        s = t.status("model-a", tier="pro")
        assert s.window_resets_in_seconds is not None
        assert 0 <= s.window_resets_in_seconds <= 3600.0


class TestQuotaTrackerConcurrency:
    def test_thread_safety(self):
        """Multiple threads recording calls should not corrupt state."""
        import threading

        t = QuotaTracker()

        def worker():
            for _ in range(100):
                t.record_call("shared")

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        s = t.status("shared", tier="pro")
        assert s.used == 1000  # 10 threads * 100 calls


# ----------------------------------------------------------------------
# QuotaTracker.snapshot()
# ----------------------------------------------------------------------

class TestQuotaSnapshot:
    def test_snapshot_returns_zero_when_no_calls(self):
        t = QuotaTracker()
        snap = t.snapshot("gemini-2.5-pro")
        assert isinstance(snap, QuotaSnapshot)
        assert snap.used == 0
        assert snap.limit == 1000
        assert snap.remaining == 1000
        assert snap.warning == "ok"

    def test_snapshot_counts_only_in_window(self):
        t = QuotaTracker()
        for _ in range(3):
            t.record_call("gemini-2.5-pro")
        snap = t.snapshot("gemini-2.5-pro")
        assert snap.used == 3
        assert snap.remaining == 997

    def test_snapshot_warning_ok_when_above_threshold(self):
        t = QuotaTracker()
        for _ in range(5):
            t.record_call("gemini-2.5-pro")
        snap = t.snapshot("gemini-2.5-pro")
        assert snap.used == 5
        assert snap.warning == "ok"

    def test_snapshot_warning_low_at_threshold(self):
        t = QuotaTracker()
        for _ in range(800):
            t.record_call("gemini-2.5-pro")
        snap = t.snapshot("gemini-2.5-pro")
        assert snap.remaining == 200
        assert snap.warning == "low"

    def test_snapshot_warning_exhausted_at_zero_remaining(self):
        t = QuotaTracker()
        for _ in range(1000):
            t.record_call("gemini-2.5-pro")
        snap = t.snapshot("gemini-2.5-pro")
        assert snap.remaining == 0
        assert snap.warning == "exhausted"

    def test_snapshot_resolves_unknown_model_to_default(self):
        t = QuotaTracker()
        snap = t.snapshot("unknown-model-xyz")
        expected_limit = MODEL_QUOTA_REGISTRY[DEFAULT_ACTIVE_MODEL].calls_per_window
        assert snap.limit == expected_limit
        assert snap.model == "unknown-model-xyz"

    def test_snapshot_includes_tier_and_window_seconds(self):
        t = QuotaTracker()
        snap = t.snapshot("gemini-2.5-pro")
        assert snap.tier == "pro"
        assert snap.window_remaining_seconds == 0

    def test_snapshot_to_dict_round_trips_fields(self):
        t = QuotaTracker()
        t.record_call("gemini-2.5-flash")
        snap = t.snapshot("gemini-2.5-flash")
        d = snap.to_dict()
        assert d["model"] == "gemini-2.5-flash"
        assert d["used"] == 1
        assert d["limit"] == 2000
        assert d["warning"] == "ok"


# ----------------------------------------------------------------------
# KNOWN_MODELS
# ----------------------------------------------------------------------

class TestKnownModels:
    def test_known_models_contains_expected(self):
        expected = {
            "gemini-3.1-pro-high",
            "gemini-3.1-pro-low",
            "gemini-3-flash",
            "claude-sonnet-4.6-thinking",
            "claude-opus-4.6-thinking",
            "gpt-oss-120b",
        }
        assert set(KNOWN_MODELS) == expected


# ----------------------------------------------------------------------
# Singleton
# ----------------------------------------------------------------------

class TestSingleton:
    def test_get_default_tracker_returns_same_instance(self):
        reset_default_tracker()
        a = get_default_tracker()
        b = get_default_tracker()
        assert a is b

    def test_reset_default_tracker_creates_new(self):
        a = get_default_tracker()
        reset_default_tracker()
        b = get_default_tracker()
        assert a is not b


# ----------------------------------------------------------------------
# Integration with server (smoke)
# ----------------------------------------------------------------------

class TestServerQuotaHookIntegration:
    """Verify the server hooks call into the tracker correctly."""

    def test_quota_module_imports_cleanly(self):
        """Smoke test: import the module to catch syntax/typing errors."""
        from agy_mcp_server import quota  # noqa: F401

    def test_models_define_quota_schemas(self):
        from agy_mcp_server.models import (
            AgyQuotaRequest,
            AgyQuotaResponse,
            AgyQuotaStatus,
        )

        req = AgyQuotaRequest()
        assert req.model is None
        assert req.tier == "unknown"
        assert req.probe is False
        assert req.use_api is False

        resp = AgyQuotaResponse(statuses=[], overall_healthy=True)
        assert resp.statuses == []
        assert resp.overall_healthy is True

    def test_agy_quota_tool_window_resets_in_seconds(self):
        from agy_mcp_server.server import agy_quota, _quota_tracker
        from agy_mcp_server.models import AgyQuotaRequest

        # Clear the tracker's calls to ensure clean state
        with _quota_tracker._lock:
            _quota_tracker._calls.clear()

        # Call with no calls recorded
        req = AgyQuotaRequest(model="gemini-3-flash", tier="pro")
        resp = agy_quota(req)
        assert len(resp.statuses) == 1
        status = resp.statuses[0]
        assert status.window_resets_in_seconds is None

        # Call with a call recorded
        _quota_tracker.record_call("gemini-3-flash")
        resp = agy_quota(req)
        assert len(resp.statuses) == 1
        status = resp.statuses[0]
        assert status.window_resets_in_seconds is not None
        assert status.window_resets_in_seconds >= 0.0
