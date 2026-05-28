from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, StrictBool


class AgyExecOptions(BaseModel):
    sandbox: StrictBool = True
    dangerously_skip_permissions: StrictBool = False
    timeout_s: int = Field(default=300, ge=1, le=3600)
    env: dict[str, str] | None = None
    extra_args: list[str] = Field(default_factory=list)


class AgyHealthRequest(BaseModel):
    expected_version: str | None = None


class AgyHealthResponse(BaseModel):
    agy_path: str
    agy_version: str
    ok: bool
    notes: list[str] = Field(default_factory=list)


class AgyRunTaskRequest(BaseModel):
    workspace_path: str
    prompt: str
    options: AgyExecOptions = Field(default_factory=AgyExecOptions)
    capture_changes: bool = True
    change_scope: Literal["workspace", "git_only"] = "workspace"


class AgyStartTaskRequest(AgyRunTaskRequest):
    stream_stdout: bool = False


class AgyPollTaskRequest(BaseModel):
    run_id: str


class AgyCancelTaskRequest(BaseModel):
    run_id: str
    force: bool = False


class AgyListRunsRequest(BaseModel):
    limit: int = 50


class AgyRunResult(BaseModel):
    run_id: str
    workspace_path: str
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    started_at: datetime
    finished_at: datetime | None


class WorkspaceChanges(BaseModel):
    method: Literal["git", "snapshot", "none"]
    changed_files: list[str] = Field(default_factory=list)
    diff: str | None = None


class AgyRunTaskResponse(BaseModel):
    result: AgyRunResult
    changes: WorkspaceChanges | None = None


class AgyStartTaskResponse(BaseModel):
    run_id: str
    started_at: datetime


class AgyPollTaskResponse(BaseModel):
    status: Literal["running", "done", "failed", "timed_out"]
    result: AgyRunResult | None = None
    partial_stdout: str = ""
    partial_stderr: str = ""
    changes: WorkspaceChanges | None = None


class AgyCancelTaskResponse(BaseModel):
    canceled: bool
    status: Literal["canceled", "not_found", "already_done"]


class AgyRunSummary(BaseModel):
    run_id: str
    workspace_path: str
    status: Literal["running", "done", "failed", "timed_out"]
    started_at: datetime


class AgyListRunsResponse(BaseModel):
    runs: list[AgyRunSummary] = Field(default_factory=list)
