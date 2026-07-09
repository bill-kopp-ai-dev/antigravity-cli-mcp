"""Quota-safe timeout policy for Antigravity CLI task execution.

Computes a per-call timeout from three inputs: how big the task is
(`task_class`), how fast the assigned model tier tends to run
(`model_tier`), and how flaky recent calls have been
(`recent_failure_rate`). The goal is to avoid two failure modes: timing
out real work too early (undersized timeout) and blocking a sync MCP
call past the point where the caller should have used the async
start/poll flow instead (oversized timeout on a slow tier).

This module is a standalone helper: it is not wired into `server.py`
tool handlers yet. Callers may import `compute_quota_safe_timeout`
directly once a tool needs it.
"""

from __future__ import annotations

from typing import Literal

# Base timeout in seconds per task class, calibrated for a "pro" tier model.
BASE_TIMEOUTS: dict[str, int] = {
    "trivial_edit": 120,
    "smoke_test": 60,
    "single_feature": 300,
    "docs_update": 180,
    "test_suite": 600,
    "review": 300,
    "multi_file_refactor": 1800,
    "architecture": 3600,
    "migration": 3600,
    "long_running": 3600,
}

# Free-tier models run slower; higher tiers get a discount.
TIER_MULTIPLIER: dict[str, float] = {
    "free": 1.5,
    "pro": 1.0,
    "ultra": 0.8,
    "enterprise": 0.7,
}

# Timeouts above this should use the async start/poll flow instead of a
# blocking sync call.
ASYNC_THRESHOLD_S: int = 600

# Timeouts in this range are still sync-safe but close enough to the
# threshold that async is worth considering.
_CONSIDER_ASYNC_FLOOR_S: int = 300


def compute_quota_safe_timeout(
    task_class: str,
    model_tier: str,
    recent_failure_rate: float,
) -> tuple[int, bool, Literal["ok", "consider_async"], Literal["ok", "low", "exhausted"]]:
    """Return (timeout_s, must_use_async, warning, quota_warning).

    Raises ValueError if `task_class` or `model_tier` is unknown, or if
    `recent_failure_rate` is outside [0.0, 1.0].
    """
    if task_class not in BASE_TIMEOUTS:
        raise ValueError(f"Unknown task_class: {task_class!r}")
    if model_tier not in TIER_MULTIPLIER:
        raise ValueError(f"Unknown model_tier: {model_tier!r}")
    if recent_failure_rate < 0.0 or recent_failure_rate > 1.0:
        raise ValueError(
            f"recent_failure_rate must be in [0.0, 1.0], got {recent_failure_rate!r}"
        )

    raw = BASE_TIMEOUTS[task_class] * TIER_MULTIPLIER[model_tier] * (
        1.0 + recent_failure_rate * 0.5
    )
    timeout_s = int(max(60, raw))

    must_use_async = bool(timeout_s > ASYNC_THRESHOLD_S)

    warning: Literal["ok", "consider_async"] = (
        "consider_async"
        if _CONSIDER_ASYNC_FLOOR_S < timeout_s <= ASYNC_THRESHOLD_S
        else "ok"
    )

    quota_warning: Literal["ok", "low", "exhausted"]
    if recent_failure_rate > 0.3:
        quota_warning = "exhausted"
    elif recent_failure_rate > 0.1:
        quota_warning = "low"
    else:
        quota_warning = "ok"

    return timeout_s, must_use_async, warning, quota_warning
