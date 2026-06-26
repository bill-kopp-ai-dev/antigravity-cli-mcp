from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, StrictBool


def _coerce_empty_str_to_dict(v: Any) -> Any:
    if v == "" or v is None:
        return {}
    return v


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
    pass


class AgyPollTaskRequest(BaseModel):
    run_id: str


class AgyCancelTaskRequest(BaseModel):
    run_id: str
    force: bool = False


class AgyListRunsRequest(BaseModel):
    limit: int = 50


AgyHealthRequestIn = Annotated[AgyHealthRequest, BeforeValidator(_coerce_empty_str_to_dict)]
AgyRunTaskRequestIn = Annotated[AgyRunTaskRequest, BeforeValidator(_coerce_empty_str_to_dict)]
AgyStartTaskRequestIn = Annotated[AgyStartTaskRequest, BeforeValidator(_coerce_empty_str_to_dict)]
AgyPollTaskRequestIn = Annotated[AgyPollTaskRequest, BeforeValidator(_coerce_empty_str_to_dict)]
AgyCancelTaskRequestIn = Annotated[AgyCancelTaskRequest, BeforeValidator(_coerce_empty_str_to_dict)]
AgyListRunsRequestIn = Annotated[AgyListRunsRequest, BeforeValidator(_coerce_empty_str_to_dict)]


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


# ------------------------------------------------------------------
# Quota tool models
# ------------------------------------------------------------------

QuotaTier = Literal["free", "pro", "ultra", "enterprise", "unknown"]
QuotaSource = Literal[
    "local_counter", "api_call", "probe", "error_parser", "combined"
]


class AgyQuotaRequest(BaseModel):
    """Request to check quota status.

    - model: if provided, return only that model's status. Otherwise return all
      known models plus an aggregate entry.
    - tier: subscription tier used to look up per-period call limits.
    - probe: opt-in. Runs a minimal `agy` task to verify the CLI is functional.
      WARNING: probe itself consumes quota.
    - use_api: opt-in. Queries an external API for authoritative quota info.
      Currently a stub that returns None unless implemented.
    """

    model: str | None = None
    tier: QuotaTier = "unknown"
    probe: bool = False
    use_api: bool = False


AgyQuotaRequestIn = Annotated[AgyQuotaRequest, BeforeValidator(_coerce_empty_str_to_dict)]


class AgyQuotaStatus(BaseModel):
    """Per-model quota status."""

    model: str
    tier: QuotaTier
    used: int | None
    limit: int | None
    remaining: int | None
    reset_at: datetime | None
    period_hours: float
    healthy: bool
    source: QuotaSource
    notes: list[str] = Field(default_factory=list)


class AgyQuotaResponse(BaseModel):
    """Response from agy_quota tool."""

    statuses: list[AgyQuotaStatus] = Field(default_factory=list)
    overall_healthy: bool
    active_model: str | None = None
    notes: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------
# Cache tool models
# ------------------------------------------------------------------


class AgyClearCacheRequest(BaseModel):
    """Request to clear the uv package cache.

    - full: if True, clears the entire uv cache (~/.cache/uv).
      if False (default), clears only this project's package entries.
    """

    full: bool = False


class AgyClearCacheResponse(BaseModel):
    """Response from agy_clear_cache tool."""

    cleared: bool
    entries_removed: int
    cache_dir: str
    notes: list[str] = Field(default_factory=list)


AgyClearCacheRequestIn = Annotated[
    AgyClearCacheRequest, BeforeValidator(_coerce_empty_str_to_dict)
]


# ------------------------------------------------------------------
# Persistence tool models
# ------------------------------------------------------------------

PersistenceFileName = Literal["agents", "projects", "memory"]


class AgyInitPersistenceRequest(BaseModel):
    """Initialize the persistence directory and seed the three markdown files."""

    force: bool = False
    seed_templates: bool | None = None


class AgyInitPersistenceResponse(BaseModel):
    base_dir: str
    created: list[str] = Field(default_factory=list)
    already_existed: list[str] = Field(default_factory=list)
    seed_version: str


class AgyReadPersistenceRequest(BaseModel):
    file: PersistenceFileName
    offset: int = 0
    limit: int | None = None


class AgyReadPersistenceResponse(BaseModel):
    file: str
    content: str
    size_bytes: int
    truncated: bool
    modified_at: datetime | None


class AgyAppendPersistenceRequest(BaseModel):
    file: PersistenceFileName
    content: str
    section_header: str | None = None


class AgyAppendPersistenceResponse(BaseModel):
    file: str
    appended_bytes: int
    new_size_bytes: int
    timestamp: datetime


class AgyUpdatePersistenceRequest(BaseModel):
    file: PersistenceFileName
    section_anchor: str
    new_content: str
    mode: Literal["replace", "append"] = "replace"


class AgyUpdatePersistenceResponse(BaseModel):
    file: str
    section_anchor: str
    matched: bool
    new_size_bytes: int


class AgyLoadPersistenceContextRequest(BaseModel):
    include: list[PersistenceFileName] = Field(
        default_factory=lambda: ["agents", "projects", "memory"]
    )
    max_chars_per_file: int = 20_000


class AgyLoadPersistenceContextResponse(BaseModel):
    agents_excerpt: str | None = None
    projects_excerpt: str | None = None
    memory_excerpt: str | None = None
    truncated_flags: dict[str, bool] = Field(default_factory=dict)
    total_chars: int = 0
    base_dir: str = ""
    initialized: bool = False


AgyInitPersistenceRequestIn = Annotated[
    AgyInitPersistenceRequest, BeforeValidator(_coerce_empty_str_to_dict)
]
AgyReadPersistenceRequestIn = Annotated[
    AgyReadPersistenceRequest, BeforeValidator(_coerce_empty_str_to_dict)
]
AgyAppendPersistenceRequestIn = Annotated[
    AgyAppendPersistenceRequest, BeforeValidator(_coerce_empty_str_to_dict)
]
AgyUpdatePersistenceRequestIn = Annotated[
    AgyUpdatePersistenceRequest, BeforeValidator(_coerce_empty_str_to_dict)
]
AgyLoadPersistenceContextRequestIn = Annotated[
    AgyLoadPersistenceContextRequest, BeforeValidator(_coerce_empty_str_to_dict)
]


class AgySelfTestRequest(BaseModel):
    """Request to run the agy self-test (metadata-only)."""
    include: list[str] | None = None  # None = all tools; else filter by name prefix
    only_show_tolerant: bool = False  # If True, only return tools that accept args={}

AgySelfTestRequestIn = Annotated[AgySelfTestRequest, BeforeValidator(_coerce_empty_str_to_dict)]


class AgyToolSchemaReport(BaseModel):
    """Per-tool schema report from agy_self_test."""
    name: str
    top_level_required: list[str] = Field(default_factory=list)
    top_level_properties: list[str] = Field(default_factory=list)
    accepts_empty_args: bool
    requires_req_wrapper: bool  # True if "req" is in top_level_required (legacy schema)


class AgySelfTestResponse(BaseModel):
    """Response from agy_self_test."""
    total_tools: int
    tolerant_count: int  # Number of tools that accept args={} (i.e. required is empty)
    requires_req_count: int  # Number of tools that still require `req` wrapper
    tools: list[AgyToolSchemaReport] = Field(default_factory=list)
    server_info: dict[str, Any] = Field(default_factory=dict)
    summary: str

