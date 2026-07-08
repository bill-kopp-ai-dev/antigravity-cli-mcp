"""Quota tracking, classification, and probing for Antigravity CLI calls.

Implements a hybrid quota-checking strategy combining four sources:

- A) Local sliding-window counter (always-on, zero-cost).
- B) Failure classifier (always-on, parses agy run results for quota signals).
- C) Probe call (opt-in via `probe=True`; consumes quota).
- D) External API (opt-in via `use_api=True`; requires additional auth).

The Antigravity CLI does NOT expose a direct quota inspection endpoint. Quota
refresh cadence is approximately every 5 hours (compute-based, not daily).
Per-tier call limits are conservative estimates because exact numbers are not
publicly published.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from .models import resolve_quota_profile

logger = logging.getLogger(__name__)


# Refresh cadence: 5 hours (per Antigravity docs/community sources).
DEFAULT_PERIOD_HOURS = 5.0


# Conservative per-tier call limits (calls per period). Exact numbers are not
# publicly published; treat as estimates. Override via env settings.
DEFAULT_TIER_LIMITS: dict[str, int] = {
    "free": 30,
    "pro": 200,
    "ultra": 1000,
    "enterprise": 5000,
}


# Models known to Antigravity (from https://antigravity.google/docs/models).
KNOWN_MODELS: frozenset[str] = frozenset(
    {
        "gemini-3.1-pro-high",
        "gemini-3.1-pro-low",
        "gemini-3-flash",
        "claude-sonnet-4.6-thinking",
        "claude-opus-4.6-thinking",
        "gpt-oss-120b",
    }
)


QuotaTier = Literal["free", "pro", "ultra", "enterprise", "unknown"]
QuotaSource = Literal[
    "local_counter", "api_call", "probe", "error_parser", "combined"
]


class FailureKind:
    """Classification of an agy run failure."""

    QUOTA_EXHAUSTED = "quota_exhausted"
    SUSPECTED_QUOTA_OR_BUG = "suspected_quota_or_bug"
    AUTH = "auth"
    TIMEOUT = "timeout"
    OTHER = "other"


_QUOTA_PATTERNS = [
    re.compile(r"resource_exhausted", re.IGNORECASE),
    re.compile(r"quota\s+exceeded", re.IGNORECASE),
    re.compile(r"rate\s+limit", re.IGNORECASE),
    re.compile(r"\b429\b"),
    re.compile(r"too\s+many\s+requests", re.IGNORECASE),
]

_AUTH_PATTERNS = [
    re.compile(r"\bunauthorized\b", re.IGNORECASE),
    re.compile(r"\bforbidden\b", re.IGNORECASE),
    re.compile(r"\b401\b"),
    re.compile(r"\b403\b"),
]


def classify_agy_failure(
    exit_code: int | None,
    stdout: str,
    stderr: str,
    timed_out: bool,
) -> str:
    """Classify an agy run failure.

    Returns one of FailureKind.* values.

    Heuristics:
    - timed_out -> TIMEOUT
    - stderr/stdout contains quota-related keywords -> QUOTA_EXHAUSTED
    - exit_code in {401, 403} or auth keywords -> AUTH
    - exit_code == 0 with empty stdout AND stderr -> SUSPECTED_QUOTA_OR_BUG
      (this matches the current antigravity-cli-mcp bug pattern).
    - else -> OTHER
    """
    if timed_out:
        return FailureKind.TIMEOUT

    blob = f"{stdout or ''}\n{stderr or ''}"
    for pat in _QUOTA_PATTERNS:
        if pat.search(blob):
            return FailureKind.QUOTA_EXHAUSTED

    for pat in _AUTH_PATTERNS:
        if pat.search(blob):
            return FailureKind.AUTH

    if exit_code == 0 and not (stdout or "").strip() and not (stderr or "").strip():
        return FailureKind.SUSPECTED_QUOTA_OR_BUG

    return FailureKind.OTHER


@dataclass
class QuotaStatus:
    """Per-model quota status."""

    model: str
    tier: str
    used: int | None
    limit: int | None
    remaining: int | None
    reset_at: datetime | None
    period_hours: float
    healthy: bool
    source: str
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class QuotaSnapshot:
    """Zero-cost snapshot of a model's current quota window, resolved from
    `MODEL_QUOTA_REGISTRY` (see models.py) rather than a live probe call."""

    model: str
    used: int
    limit: int
    remaining: int
    reset_at: float  # unix timestamp
    window_remaining_seconds: int
    warning: Literal["ok", "low", "exhausted"]
    tier: str

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "reset_at": self.reset_at,
            "window_remaining_seconds": self.window_remaining_seconds,
            "warning": self.warning,
            "tier": self.tier,
        }


class QuotaTracker:
    """Sliding-window local counter for agy calls per model.

    Thread-safe. Tracks timestamps of calls in a `period_hours` window and
    provides status queries per (model, tier).
    """

    def __init__(
        self,
        *,
        period_hours: float = DEFAULT_PERIOD_HOURS,
        tier_limits: dict[str, int] | None = None,
    ) -> None:
        self.period_hours = float(period_hours)
        self.tier_limits: dict[str, int] = dict(tier_limits or DEFAULT_TIER_LIMITS)
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        # Last failure classification (for debugging / observability).
        self.last_failure_kind: str | None = None
        self.last_failure_at: float | None = None

    def record_call(
        self,
        model: str = "unknown",
        ts: float | None = None,
    ) -> None:
        with self._lock:
            ts = ts if ts is not None else time.time()
            cutoff = ts - self.period_hours * 3600.0
            dq = self._calls[model]
            while dq and dq[0] < cutoff:
                dq.popleft()
            dq.append(ts)

    def record_failure(self, kind: str, ts: float | None = None) -> None:
        with self._lock:
            self.last_failure_kind = kind
            self.last_failure_at = ts if ts is not None else time.time()

    def status(self, model: str, tier: str) -> QuotaStatus:
        with self._lock:
            now = time.time()
            cutoff = now - self.period_hours * 3600.0
            dq = self._calls.get(model, deque())
            recent = [t for t in dq if t >= cutoff]
            used = len(recent)
            limit = self.tier_limits.get(tier, 999_999)
            remaining = max(0, limit - used)
            reset_at: datetime | None = None
            if recent:
                window_start = min(recent)
                reset_at = datetime.fromtimestamp(
                    window_start + self.period_hours * 3600.0,
                    tz=timezone.utc,
                )
            notes: list[str] = []
            if self.last_failure_kind is not None:
                notes.append(
                    f"last_failure={self.last_failure_kind} at "
                    f"{datetime.fromtimestamp(self.last_failure_at, tz=timezone.utc).isoformat()}"
                    if self.last_failure_at is not None
                    else f"last_failure={self.last_failure_kind}"
                )
            return QuotaStatus(
                model=model,
                tier=tier,
                used=used,
                limit=limit,
                remaining=remaining,
                reset_at=reset_at,
                period_hours=self.period_hours,
                healthy=used < limit,
                source="local_counter",
                notes=notes,
            )

    def snapshot(self, model: str, *, low_threshold_pct: float = 20.0) -> QuotaSnapshot:
        """Return a zero-cost snapshot of the current window state for `model`.

        Unlike `status()`, the limit/tier are resolved from
        `MODEL_QUOTA_REGISTRY` (per-model call budgets) rather than the
        legacy `tier_limits` mapping. Acquires the existing instance lock
        briefly to read the call list.
        """
        profile = resolve_quota_profile(model)
        with self._lock:
            now = time.time()
            window_start = now - (profile.window_hours * 3600)
            recent = [t for t in self._calls.get(model, ()) if t >= window_start]
            used = len(recent)
            limit = profile.calls_per_window
            remaining = max(0, limit - used)
            reset_at = (recent[0] + profile.window_hours * 3600) if recent else now
            window_remaining = max(0, int(reset_at - now))

        if remaining == 0:
            warning: Literal["ok", "low", "exhausted"] = "exhausted"
        elif (remaining / limit * 100) <= low_threshold_pct:
            warning = "low"
        else:
            warning = "ok"

        return QuotaSnapshot(
            model=model,
            used=used,
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
            window_remaining_seconds=window_remaining,
            warning=warning,
            tier=profile.tier.value,
        )

    def all_known_models_status(self, tier: str) -> list[QuotaStatus]:
        return [self.status(m, tier) for m in sorted(KNOWN_MODELS)]

    def total_used_in_window(self) -> int:
        with self._lock:
            now = time.time()
            cutoff = now - self.period_hours * 3600.0
            total = 0
            for dq in self._calls.values():
                total += sum(1 for t in dq if t >= cutoff)
            return total


def probe_agy_quota(
    *,
    agy_path: str,
    workspace_path: str | None = None,
    timeout_s: int = 30,
) -> tuple[bool, str, str]:
    """Run a minimal `agy` task to probe whether the CLI is functional.

    WARNING: this call itself consumes quota. Use sparingly and only behind
    the explicit `probe=True` opt-in on AgyQuotaRequest.

    Returns:
        (healthy, message, failure_kind)
    """
    args: list[str] = [
        agy_path,
        "--prompt",
        "ok",
        "--output-format",
        "json",
        "--max-turns",
        "1",
    ]
    if workspace_path:
        args.extend(["--add-dir", workspace_path])

    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False, "probe timed out", FailureKind.TIMEOUT
    except FileNotFoundError:
        return False, f"agy binary not found at {agy_path}", FailureKind.OTHER
    except Exception as e:  # pragma: no cover - defensive
        return False, f"probe failed: {e}", FailureKind.OTHER

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    kind = classify_agy_failure(proc.returncode, stdout, stderr, False)
    healthy = proc.returncode == 0 and kind not in (
        FailureKind.QUOTA_EXHAUSTED,
        FailureKind.SUSPECTED_QUOTA_OR_BUG,
    )
    msg = f"probe exit_code={proc.returncode} kind={kind} stdout_len={len(stdout)} stderr_len={len(stderr)}"
    return healthy, msg, kind


def fetch_gemini_api_quota(  # pragma: no cover - external dependency
    *,
    api_base_url: str,
    api_key: str,
    timeout_s: int = 10,
) -> dict[str, object] | None:
    """Attempt to fetch quota information from the Gemini API directly.

    This is a stub: requires `google-auth` and `google-cloud-aiplatform`
    (or manual HTTP calls to `generativelanguage.googleapis.com`). Currently
    returns None with a logged warning so the orchestrator knows the feature
    is opt-in but not yet wired up.

    To enable: install `httpx` and implement the actual API call here.
    """
    logger.warning(
        "fetch_gemini_api_quota is a stub. To enable, implement the call to "
        "%s with the appropriate API key. Returning None.",
        api_base_url,
    )
    return None


# Module-level singleton.
_default_tracker: QuotaTracker | None = None
_default_tracker_lock = threading.Lock()


def get_default_tracker() -> QuotaTracker:
    """Return the process-wide QuotaTracker (lazy init)."""
    global _default_tracker
    if _default_tracker is None:
        with _default_tracker_lock:
            if _default_tracker is None:
                _default_tracker = QuotaTracker()
    return _default_tracker


def reset_default_tracker() -> None:
    """Reset the singleton (used in tests)."""
    global _default_tracker
    with _default_tracker_lock:
        _default_tracker = None
