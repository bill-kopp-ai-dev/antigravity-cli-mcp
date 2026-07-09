"""Tests for the timeout_policy module: compute_quota_safe_timeout."""

from __future__ import annotations

import pytest

from agy_mcp_server.timeout_policy import (
    ASYNC_THRESHOLD_S,
    BASE_TIMEOUTS,
    TIER_MULTIPLIER,
    compute_quota_safe_timeout,
)

ALL_TASK_CLASSES = [
    "trivial_edit",
    "smoke_test",
    "single_feature",
    "docs_update",
    "test_suite",
    "review",
    "multi_file_refactor",
    "architecture",
    "migration",
    "long_running",
]

ALL_TIERS = ["free", "pro", "ultra", "enterprise"]


class TestTables:
    def test_base_timeouts_has_all_task_classes(self):
        assert set(BASE_TIMEOUTS.keys()) == set(ALL_TASK_CLASSES)

    def test_tier_multiplier_has_all_tiers(self):
        assert set(TIER_MULTIPLIER.keys()) == set(ALL_TIERS)


class TestFloor:
    @pytest.mark.parametrize("task_class", ALL_TASK_CLASSES)
    @pytest.mark.parametrize("model_tier", ALL_TIERS)
    def test_timeout_never_below_60(self, task_class, model_tier):
        timeout_s, _, _, _ = compute_quota_safe_timeout(task_class, model_tier, 0.0)
        assert timeout_s >= 60


class TestMustUseAsync:
    def test_multi_file_refactor_free_must_use_async(self):
        timeout_s, must_use_async, _, _ = compute_quota_safe_timeout(
            "multi_file_refactor", "free", 0.0
        )
        assert timeout_s == 2700
        assert must_use_async is True

    def test_trivial_edit_pro_does_not_need_async(self):
        timeout_s, must_use_async, _, _ = compute_quota_safe_timeout(
            "trivial_edit", "pro", 0.0
        )
        assert timeout_s == 120
        assert must_use_async is False


class TestFailureRateEffect:
    def test_higher_failure_rate_increases_timeout(self):
        low, *_ = compute_quota_safe_timeout("single_feature", "pro", 0.0)
        high, *_ = compute_quota_safe_timeout("single_feature", "pro", 0.5)
        assert low < high


class TestQuotaWarning:
    def test_quota_warning_exhausted_above_0_3(self):
        _, _, _, quota_warning = compute_quota_safe_timeout("review", "pro", 0.4)
        assert quota_warning == "exhausted"

    def test_quota_warning_low_between_0_1_and_0_3(self):
        _, _, _, quota_warning = compute_quota_safe_timeout("review", "pro", 0.2)
        assert quota_warning == "low"

    def test_quota_warning_ok_below_0_1(self):
        _, _, _, quota_warning = compute_quota_safe_timeout("review", "pro", 0.05)
        assert quota_warning == "ok"


class TestValidation:
    def test_unknown_task_class_raises(self):
        with pytest.raises(ValueError):
            compute_quota_safe_timeout("not_a_task_class", "pro", 0.0)

    def test_unknown_model_tier_raises(self):
        with pytest.raises(ValueError):
            compute_quota_safe_timeout("review", "not_a_tier", 0.0)

    def test_negative_failure_rate_raises(self):
        with pytest.raises(ValueError):
            compute_quota_safe_timeout("review", "pro", -0.1)

    def test_failure_rate_above_1_raises(self):
        with pytest.raises(ValueError):
            compute_quota_safe_timeout("review", "pro", 1.1)


class TestAsyncWarningSweetSpot:
    def test_test_suite_pro_hits_consider_async_boundary(self):
        # test_suite (600) * pro (1.0) = 600, the inclusive upper edge of the
        # (300, 600] "consider_async" sweet spot.
        timeout_s, must_use_async, warning, _ = compute_quota_safe_timeout(
            "test_suite", "pro", 0.0
        )
        assert timeout_s == 600
        assert must_use_async is False
        assert warning == "consider_async"

    def test_test_suite_free_exceeds_threshold_so_warning_is_ok(self):
        # test_suite (600) * free (1.5) = 900, already past ASYNC_THRESHOLD_S,
        # so must_use_async is True and the "consider_async" sweet-spot
        # warning (reserved for calls that are still sync-safe) does not
        # apply here; it collapses back to "ok".
        timeout_s, must_use_async, warning, _ = compute_quota_safe_timeout(
            "test_suite", "free", 0.0
        )
        assert timeout_s == 900
        assert timeout_s > ASYNC_THRESHOLD_S
        assert must_use_async is True
        assert warning == "ok"
