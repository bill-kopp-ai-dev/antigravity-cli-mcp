from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from agy_mcp_server.models import AgyRunResult, WorkspaceChanges

RunStatus = Literal["running", "done", "failed", "timed_out"]


@dataclass
class StoredRun:
    status: RunStatus
    result: AgyRunResult | None
    changes: WorkspaceChanges | None
    started_at: datetime


class RunStore:
    def __init__(self, *, max_runs: int) -> None:
        self._max_runs = max_runs
        self._runs: OrderedDict[str, StoredRun] = OrderedDict()

    def put(self, run_id: str, run: StoredRun) -> None:
        self._runs[run_id] = run
        self._runs.move_to_end(run_id)
        self._prune()

    def get(self, run_id: str) -> StoredRun | None:
        return self._runs.get(run_id)

    def list(self, limit: int) -> list[tuple[str, StoredRun]]:
        items = list(self._runs.items())
        items.reverse()
        return items[: max(0, limit)]

    def _prune(self) -> None:
        while len(self._runs) > self._max_runs:
            self._runs.popitem(last=False)
